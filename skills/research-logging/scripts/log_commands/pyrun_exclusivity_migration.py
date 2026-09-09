"""Bounded Markdown-first migration from pyrun state v2 to v3."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence, cast

from validation.commands import Invocation, command_fence_opening_lines
from validation.operation_state import PYRUN_EXCLUSIVITY_MIGRATION_RESIDUE
from validation.pyrun_state import (
    LEGACY_PYRUN_SCHEMA,
    PYRUN_FILENAME,
    PYRUN_SCHEMA,
    PyrunFile,
    execution_id,
    load_pyrun_state,
    migrated_exclusivity_state,
    parse_pyrun_state_text,
    recipe_from_invocation,
)

from .context import EntryContext, LogContext, resolve_project_root
from .model import ActionError
from .pyrun_policy import _entry_invocations
from .scaffold import observe_entries
from .storage import (
    atomic_write_text,
    entry_lock_under_log,
    log_lock,
    sync_directory,
)

RESULT_SCHEMA = "research-log-pyrun-exclusivity-migration-result/1"
TRANSACTION_SCHEMA = "research-log-pyrun-exclusivity-migration-transaction/1"
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_TRANSACTION_BYTES = 64 * 1024 * 1024
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
TRANSACTION_ID_RE = re.compile(r"pyrun-exclusivity-[0-9a-f]{24}\Z")


@dataclass
class _MigrationContext:
    log: LogContext
    entry: EntryContext
    state: PyrunFile
    project_root: Path
    fence_lines: dict[str, tuple[int, ...]]
    policies: dict[str, bool]
    expansion_indexes: Counter[tuple[str, int, tuple[str, ...]]]


@dataclass(frozen=True)
class _EntryReconciliation:
    entry_item: Mapping[str, object]
    execution_items: tuple[Mapping[str, object], ...]
    candidate: PyrunFile


@dataclass(frozen=True)
class _RefusalAccounting:
    item: Mapping[str, object]
    totals: Mapping[str, int]


def migrate_exclusivity(log: LogContext, *, dry_run: bool) -> Mapping[str, object]:
    """Reconcile every current command and atomically publish v3 state."""

    return _preview_migration(log) if dry_run else _apply_migration(log)


def _preview_migration(log: LogContext) -> Mapping[str, object]:
    observed = _observed_entries(log)
    if _transaction_path(log).exists():
        error = ActionError(
            "pyrun.migrate.recovery_required",
            "migration transaction residue requires the mutating command",
        )
        return _refused_result(log, observed, error)
    try:
        entries = _participating_entries(log, entries=observed)
        result, _ = _reconcile(log, entries)
    except (ActionError, OSError, UnicodeError, ValueError) as error:
        return _refused_result(log, observed, error)
    return result


def _apply_migration(log: LogContext) -> Mapping[str, object]:
    with log_lock(log, allow_pyrun_exclusivity_migration=True):
        try:
            _recover_transaction(log)
        except (ActionError, OSError, UnicodeError, ValueError) as error:
            return _refused_result(log, _observed_entries(log), error)
        observed = _observed_entries(log)
        return _migrate_locked_entries(log, observed)


def _migrate_locked_entries(
    log: LogContext, observed: tuple[EntryContext, ...]
) -> Mapping[str, object]:
    with ExitStack() as stack:
        for entry in observed:
            stack.enter_context(entry_lock_under_log(entry))
        repeated = _observed_entries(log)
        if tuple((item.id, item.root.resolve()) for item in repeated) != tuple(
            (item.id, item.root.resolve()) for item in observed
        ):
            error = ActionError(
                "pyrun.migrate.inventory_changed",
                "entry inventory changed during migration admission",
            )
            return _refused_result(log, observed, error)
        try:
            entries = _participating_entries(log, entries=repeated)
            result, candidates = _reconcile(log, entries)
        except (ActionError, OSError, UnicodeError, ValueError) as error:
            return _refused_result(log, repeated, error)
        if result["changed"]:
            _publish_transaction(log, candidates)
        return {**result, "status": "complete"}


def _observed_entries(log: LogContext) -> tuple[EntryContext, ...]:
    return tuple(EntryContext(log, item.id, item.root) for item in observe_entries(log))


def _participating_entries(
    log: LogContext, *, entries: tuple[EntryContext, ...] | None = None
) -> tuple[EntryContext, ...]:
    project = resolve_project_root(log.root)
    candidates = entries if entries is not None else _observed_entries(log)
    selected = []
    for entry in candidates:
        path = entry.root / PYRUN_FILENAME
        invocations = _output_invocations(entry, project)
        if path.exists() or path.is_symlink() or invocations:
            selected.append(entry)
    return tuple(selected)


def _output_invocations(
    entry: EntryContext, project_root: Path
) -> tuple[Invocation, ...]:
    return tuple(
        invocation
        for invocation in _entry_invocations(entry, project_root=project_root)
        if invocation.outputs
        or any(
            collection.direction == "output" for collection in invocation.collections
        )
    )


def format_migration_result(result: Mapping[str, object]) -> str:
    """Render the complete deterministic migration accounting as text."""

    totals = result["totals"]
    assert isinstance(totals, Mapping)
    lines = [
        f"Exclusivity migration: {result['status']}",
        f"Changed: {'yes' if result['changed'] else 'no'}",
        f"Stored executions: {totals['stored_executions']}",
        f"Converted v2: {totals['converted_v2']}",
        f"Unchanged v3: {totals['unchanged_v3']}",
        f"Exclusive true: {totals['exclusive_true']}",
        f"Exclusive false: {totals['exclusive_false']}",
        f"Unaccounted: {totals['unaccounted']}",
    ]
    for item in cast(Sequence[Mapping[str, object]], result["executions"]):
        lines.append(
            f"{item['entry']} {item['execution_id']} "
            f"exclusive={str(item['target_exclusive']).lower()} {item['action']}"
        )
    for item in cast(Sequence[Mapping[str, object]], result["diagnostics"]):
        lines.append(f"Diagnostic: {item['code']}: {item['message']}")
    return "\n".join(lines) + "\n"


def _reconcile(
    log: LogContext, entries: tuple[EntryContext, ...]
) -> tuple[dict[str, object], dict[Path, PyrunFile]]:
    project_root = resolve_project_root(log.root)
    if not entries:
        raise ActionError("pyrun.migrate.empty", "the log has no authored pyrun work")
    states: dict[str, PyrunFile] = {}
    for entry in entries:
        path = entry.root / PYRUN_FILENAME
        if not path.exists() and not path.is_symlink():
            raise ActionError(
                "pyrun.migrate.state_missing",
                f"authored output command has no pyrun state in {entry.id}",
            )
        states[entry.id] = load_pyrun_state(
            path,
            entry_root=entry.root,
            project_root=project_root,
        )
    schemas = {state.schema for state in states.values()}
    if not schemas <= {LEGACY_PYRUN_SCHEMA, PYRUN_SCHEMA} or len(schemas) != 1:
        raise ActionError(
            "pyrun.migrate.mixed_schema",
            "mixed pyrun v2/v3 state has no recognized migration transaction",
        )

    fence_lines: dict[str, tuple[int, ...]] = {}
    reconciled = tuple(
        _reconcile_entry(log, entry, states[entry.id], project_root, fence_lines)
        for entry in sorted(entries, key=lambda item: item.id)
    )
    entry_items = [dict(item.entry_item) for item in reconciled]
    execution_items = [
        dict(execution) for item in reconciled for execution in item.execution_items
    ]
    candidates = {item.candidate.path: item.candidate for item in reconciled}
    total_authored = sum(
        cast(int, item.entry_item["authored_invocations"]) for item in reconciled
    )
    total_expanded = len(execution_items)
    total_stored = sum(
        cast(int, item.entry_item["stored_executions"]) for item in reconciled
    )
    total_true = sum(
        cast(int, item.entry_item["exclusive_true"]) for item in reconciled
    )
    total_false = total_stored - total_true

    converted = total_stored if schemas == {LEGACY_PYRUN_SCHEMA} else 0
    unchanged = total_stored if schemas == {PYRUN_SCHEMA} else 0
    result: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "summary": log.summary.relative_to(project_root).as_posix(),
        "status": "ready" if schemas == {LEGACY_PYRUN_SCHEMA} else "complete",
        "changed": bool(converted),
        "totals": {
            "authored_invocations": total_authored,
            "expanded_executions": total_expanded,
            "stored_executions": total_stored,
            "converted_v2": converted,
            "unchanged_v3": unchanged,
            "exclusive_true": total_true,
            "exclusive_false": total_false,
            "unaccounted": 0,
        },
        "entries": entry_items,
        "executions": execution_items,
        "diagnostics": [],
    }
    _require_result_bound(result)
    return result, candidates


def _require_result_bound(result: Mapping[str, object]) -> None:
    encoded = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise ActionError(
            "pyrun.migrate.resource_limit", "migration result crossed its byte bound"
        )


def _refused_result(
    log: LogContext,
    entries: Sequence[EntryContext],
    error: Exception,
) -> Mapping[str, object]:
    """Return deterministic best-effort accounting for a bounded refusal."""

    project = resolve_project_root(log.root)
    accounting = [
        _refusal_entry_accounting(entry, project)
        for entry in sorted(entries, key=lambda item: item.id)
    ]
    totals: Counter[str] = Counter()
    for item in accounting:
        totals.update(item.totals)
    result: Mapping[str, object] = {
        "schema": RESULT_SCHEMA,
        "summary": log.summary.relative_to(project).as_posix(),
        "status": "refused",
        "changed": False,
        "totals": {
            name: totals[name]
            for name in (
                "authored_invocations",
                "expanded_executions",
                "stored_executions",
                "converted_v2",
                "unchanged_v3",
                "exclusive_true",
                "exclusive_false",
                "unaccounted",
            )
        },
        "entries": [item.item for item in accounting],
        "executions": [],
        "diagnostics": [_refusal_diagnostic(error)],
    }
    _require_result_bound(result)
    return result


def _refusal_entry_accounting(entry: EntryContext, project: Path) -> _RefusalAccounting:
    try:
        invocations = _output_invocations(entry, project)
    except (ActionError, OSError, UnicodeError):
        invocations = ()
    grouped = {(item.document, item.fence, item.authored_group) for item in invocations}
    state = _optional_state(entry, project)
    authored_ids, policies = _authored_identities(entry, project, invocations)
    stored_ids = list(state.executions) if state is not None else []
    authored_counts = Counter(authored_ids)
    stored_counts = Counter(stored_ids)
    unaccounted = sum((authored_counts - stored_counts).values()) + sum(
        (stored_counts - authored_counts).values()
    )
    matched = set(authored_ids) & set(stored_ids)
    true_count = sum(policies[item] for item in matched)
    stored = len(stored_ids)
    return _RefusalAccounting(
        {
            "entry": entry.id,
            "authored_invocations": len(grouped),
            "expanded_executions": len(invocations),
            "stored_executions": stored,
            "exclusive_true": true_count,
            "exclusive_false": len(matched) - true_count,
        },
        {
            "authored_invocations": len(grouped),
            "expanded_executions": len(invocations),
            "stored_executions": stored,
            "converted_v2": (
                stored
                if state is not None and state.schema == LEGACY_PYRUN_SCHEMA
                else 0
            ),
            "unchanged_v3": (
                stored if state is not None and state.schema == PYRUN_SCHEMA else 0
            ),
            "exclusive_true": true_count,
            "exclusive_false": len(matched) - true_count,
            "unaccounted": unaccounted,
        },
    )


def _optional_state(entry: EntryContext, project: Path) -> PyrunFile | None:
    path = entry.root / PYRUN_FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return load_pyrun_state(path, entry_root=entry.root, project_root=project)
    except (ActionError, OSError, UnicodeError, ValueError):
        return None


def _authored_identities(
    entry: EntryContext,
    project: Path,
    invocations: Sequence[Invocation],
) -> tuple[list[str], dict[str, bool]]:
    identities: list[str] = []
    policies: dict[str, bool] = {}
    for invocation in invocations:
        try:
            identity = execution_id(
                recipe_from_invocation(
                    invocation, entry_root=entry.root, project_root=project
                )
            )
        except (OSError, UnicodeError, ValueError):
            continue
        identities.append(identity)
        policies.setdefault(identity, invocation.exclusive)
    return identities, policies


def _refusal_diagnostic(error: Exception) -> Mapping[str, object]:
    message = str(error)
    entry = re.search(r"\be[0-9]{3}\b", message)
    execution = re.search(r"pyrun-exec/v1:[0-9a-f]{64}", message)
    return {
        "code": cast(str, getattr(error, "code", "pyrun.migrate.refused")),
        "message": message,
        "entry": entry.group(0) if entry else None,
        "execution_id": execution.group(0) if execution else None,
        "markdown_path": None,
        "line": None,
    }


def _reconcile_entry(
    log: LogContext,
    entry: EntryContext,
    state: PyrunFile,
    project_root: Path,
    fence_lines: dict[str, tuple[int, ...]],
) -> _EntryReconciliation:
    invocations = _output_invocations(entry, project_root)
    grouped = Counter(
        (item.document, item.fence, item.authored_group) for item in invocations
    )
    context = _MigrationContext(
        log,
        entry,
        state,
        project_root,
        fence_lines,
        {},
        Counter(),
    )
    execution_items = tuple(
        _migration_execution_item(context, invocation) for invocation in invocations
    )
    missing = sorted(set(state.executions) - set(context.policies))
    if missing:
        raise ActionError(
            "pyrun.migrate.unaccounted_execution",
            f"stored executions have no current Markdown command in {entry.id}: "
            + ", ".join(missing),
        )
    true_count = sum(context.policies.values())
    entry_item = {
        "entry": entry.id,
        "authored_invocations": len(grouped),
        "expanded_executions": len(invocations),
        "stored_executions": len(state.executions),
        "exclusive_true": true_count,
        "exclusive_false": len(context.policies) - true_count,
    }
    candidate = migrated_exclusivity_state(
        state, context.policies, project_root=project_root
    )
    return _EntryReconciliation(entry_item, execution_items, candidate)


def _migration_execution_item(
    context: _MigrationContext, item: Invocation
) -> Mapping[str, object]:
    recipe = recipe_from_invocation(
        item,
        entry_root=context.entry.root,
        project_root=context.project_root,
    )
    identity = execution_id(recipe)
    if identity in context.policies:
        raise ActionError(
            "pyrun.migrate.ambiguous_command",
            f"multiple commands resolve to {context.entry.id}:{identity}",
        )
    recorded = context.state.executions.get(identity)
    if recorded is None or recorded.recipe != recipe:
        raise ActionError(
            "pyrun.migrate.recipe_disagreement",
            f"Markdown recipe is not recorded for {context.entry.id}:{identity}",
        )
    if recorded.auto_reproduce != item.auto_reproduce or (
        context.state.schema == PYRUN_SCHEMA and recorded.exclusive != item.exclusive
    ):
        raise ActionError(
            "pyrun.migrate.policy_disagreement",
            f"authored policy differs for {context.entry.id}:{identity}",
        )
    context.policies[identity] = item.exclusive
    document_lines = _document_fence_lines(context, item.document)
    if item.fence > len(document_lines):
        raise ActionError(
            "pyrun.migrate.location_invalid",
            f"command fence is absent from {item.document}",
        )
    group = (item.document, item.fence, item.authored_group)
    is_loop = any(value.startswith("loop:") for value in item.authored_group)
    expansion_index = context.expansion_indexes[group] if is_loop else None
    context.expansion_indexes[group] += 1
    legacy = context.state.schema == LEGACY_PYRUN_SCHEMA
    return {
        "entry": context.entry.id,
        "execution_id": identity,
        "script": recipe.script,
        "markdown_path": item.document,
        "line": document_lines[item.fence - 1],
        "invocation_kind": "loop_expansion" if is_loop else "direct",
        "expansion_index": expansion_index,
        "prior_schema": context.state.schema,
        "prior_exclusive": None if legacy else recorded.exclusive,
        "target_exclusive": item.exclusive,
        "action": "convert" if legacy else "unchanged",
    }


def _document_fence_lines(context: _MigrationContext, document: str) -> tuple[int, ...]:
    if document not in context.fence_lines:
        path = context.log.root / document
        context.fence_lines[document] = command_fence_opening_lines(
            path.read_text(encoding="utf-8")
        )
    return context.fence_lines[document]


def _publish_transaction(log: LogContext, candidates: Mapping[Path, PyrunFile]) -> None:
    root = _transaction_root(log)
    if root.exists():
        raise ActionError("pyrun.migrate.residue", "migration residue already exists")
    root.mkdir(parents=True)
    sync_directory(root.parent)
    created_at = _utc_now()
    transaction_id = f"pyrun-exclusivity-{secrets.token_hex(12)}"
    project = resolve_project_root(log.root)
    entries = []
    for index, (target, state) in enumerate(
        sorted(candidates.items(), key=lambda item: item[0].as_posix())
    ):
        staged = root / f"{index:06d}-{state.entry_root.name}.json"
        atomic_write_text(staged, state.serialized())
        entries.append(
            {
                "entry": state.entry_root.name.split("-")[3],
                "target": target.relative_to(project).as_posix(),
                "original_digest": _digest(target),
                "staged": staged.relative_to(root).as_posix(),
                "staged_digest": _digest(staged),
                "published": False,
            }
        )
    journal: dict[str, object] = {
        "schema": TRANSACTION_SCHEMA,
        "transaction_id": transaction_id,
        "state": "prepared",
        "created_at": created_at,
        "entries": entries,
    }
    _write_transaction(log, journal)
    journal["state"] = "committing"
    _write_transaction(log, journal)
    _roll_forward(log, journal)


def _recover_transaction(log: LogContext) -> None:
    path = _transaction_path(log)
    if not path.exists():
        return
    journal = _load_transaction(log)
    if journal["state"] == "prepared":
        _verify_prepared(log, journal)
        _discard_transaction(log, journal)
        return
    _roll_forward(log, journal)


def _verify_prepared(log: LogContext, journal: Mapping[str, object]) -> None:
    """Verify a pre-commit transaction before discarding its intact stages."""

    project = resolve_project_root(log.root)
    root = _transaction_root(log)
    for item in cast(Sequence[Mapping[str, object]], journal["entries"]):
        staged = root / cast(str, item["staged"])
        target = project / cast(str, item["target"])
        if _digest(staged) != item["staged_digest"]:
            raise ActionError(
                "pyrun.migrate.residue_invalid", "staged migration bytes changed"
            )
        if _digest(target) != item["original_digest"]:
            raise ActionError(
                "pyrun.migrate.target_changed",
                f"migration target changed: {item['target']}",
            )


def _roll_forward(log: LogContext, journal: dict[str, object]) -> None:
    project = resolve_project_root(log.root)
    root = _transaction_root(log)
    entries = cast(list[dict[str, object]], journal["entries"])
    for item in entries:
        target = project / cast(str, item["target"])
        staged = root / cast(str, item["staged"])
        if _digest(staged) != item["staged_digest"]:
            raise ActionError(
                "pyrun.migrate.residue_invalid", "staged migration bytes changed"
            )
        target_digest = _digest(target)
        if target_digest == item["original_digest"]:
            atomic_write_text(target, staged.read_text(encoding="utf-8"))
        elif target_digest != item["staged_digest"]:
            raise ActionError(
                "pyrun.migrate.target_changed",
                f"migration target changed: {item['target']}",
            )
        item["published"] = True
        _write_transaction(log, journal)
    _discard_transaction(log, journal)


def _load_transaction(log: LogContext) -> dict[str, object]:
    path = _transaction_path(log)
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("transaction must be a regular file")
        raw = path.read_bytes()
        if len(raw) > MAX_TRANSACTION_BYTES:
            raise OSError("migration transaction crossed its byte bound")
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActionError("pyrun.migrate.residue_invalid", str(error)) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "transaction_id", "state", "created_at", "entries"}
        or value.get("schema") != TRANSACTION_SCHEMA
        or value.get("state") not in {"prepared", "committing"}
        or not isinstance(value.get("transaction_id"), str)
        or TRANSACTION_ID_RE.fullmatch(value["transaction_id"]) is None
        or not isinstance(value.get("created_at"), str)
        or TIMESTAMP_RE.fullmatch(value["created_at"]) is None
        or not isinstance(value.get("entries"), list)
    ):
        raise ActionError(
            "pyrun.migrate.residue_invalid", "invalid migration transaction"
        )
    entries = cast(list[object], value["entries"])
    if not entries or any(not _valid_transaction_entry(item) for item in entries):
        raise ActionError(
            "pyrun.migrate.residue_invalid", "invalid migration transaction entry"
        )
    if text != json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n":
        raise ActionError(
            "pyrun.migrate.residue_invalid",
            "migration transaction is not canonical",
        )
    _validate_transaction_entries(log, cast(Mapping[str, object], value))
    return cast(dict[str, object], value)


def _validate_transaction_entries(
    log: LogContext, journal: Mapping[str, object]
) -> None:
    project = resolve_project_root(log.root)
    root = _transaction_root(log)
    inventory = {entry.id: entry for entry in _observed_entries(log)}
    entries = cast(Sequence[Mapping[str, object]], journal["entries"])
    identifiers = [cast(str, item["entry"]) for item in entries]
    if identifiers != sorted(set(identifiers)):
        raise ActionError(
            "pyrun.migrate.residue_invalid",
            "migration transaction entry order is invalid",
        )
    published = [cast(bool, item["published"]) for item in entries]
    if (
        journal["state"] == "prepared"
        and any(published)
        or published != sorted(published, reverse=True)
    ):
        raise ActionError(
            "pyrun.migrate.residue_invalid",
            "migration transaction publication state is invalid",
        )
    for index, item in enumerate(entries):
        entry = inventory.get(cast(str, item["entry"]))
        if entry is None:
            raise ActionError(
                "pyrun.migrate.residue_invalid",
                "migration transaction entry is not in the log inventory",
            )
        expected_target = (entry.root / PYRUN_FILENAME).relative_to(project).as_posix()
        expected_staged = f"{index:06d}-{entry.root.name}.json"
        if item["target"] != expected_target or item["staged"] != expected_staged:
            raise ActionError(
                "pyrun.migrate.residue_invalid",
                "migration transaction paths are not canonical",
            )
        _validate_staged_candidate(
            root / expected_staged,
            entry=entry,
            project_root=project,
            transaction_entry=item,
        )


def _validate_staged_candidate(
    path: Path,
    *,
    entry: EntryContext,
    project_root: Path,
    transaction_entry: Mapping[str, object],
) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("staged migration candidate must be a regular file")
        raw = path.read_text(encoding="utf-8")
        candidate = parse_pyrun_state_text(
            raw,
            subject=path,
            entry_root=entry.root,
            project_root=project_root,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("pyrun.migrate.residue_invalid", str(error)) from error
    if candidate.schema != PYRUN_SCHEMA or raw != candidate.serialized():
        raise ActionError(
            "pyrun.migrate.residue_invalid",
            "staged migration candidate is not canonical v3 state",
        )
    if _digest(path) != transaction_entry["staged_digest"]:
        raise ActionError(
            "pyrun.migrate.residue_invalid", "staged migration bytes changed"
        )
    target = project_root / cast(str, transaction_entry["target"])
    target_digest = _digest(target)
    if target_digest == transaction_entry["staged_digest"]:
        if target.read_text(encoding="utf-8") != raw:
            raise ActionError(
                "pyrun.migrate.residue_invalid",
                "published migration target differs from its stage",
            )
        return
    if target_digest != transaction_entry["original_digest"]:
        raise ActionError(
            "pyrun.migrate.target_changed",
            f"migration target changed: {transaction_entry['target']}",
        )
    current = load_pyrun_state(
        target,
        entry_root=entry.root,
        project_root=project_root,
    )
    expected = _reconcile_entry(
        entry.log,
        entry,
        current,
        project_root,
        {},
    ).candidate
    if raw != expected.serialized():
        raise ActionError(
            "pyrun.migrate.residue_invalid",
            "staged migration candidate does not match authored policy",
        )


def _valid_transaction_entry(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "entry",
        "target",
        "original_digest",
        "staged",
        "staged_digest",
        "published",
    }:
        return False
    target = value.get("target")
    staged = value.get("staged")
    return (
        isinstance(value.get("entry"), str)
        and re.fullmatch(r"e[0-9]{3}", value["entry"]) is not None
        and isinstance(target, str)
        and _safe_relative_path(target)
        and isinstance(staged, str)
        and Path(staged).name == staged
        and isinstance(value.get("original_digest"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["original_digest"]) is not None
        and isinstance(value.get("staged_digest"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["staged_digest"]) is not None
        and isinstance(value.get("published"), bool)
    )


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _write_transaction(log: LogContext, journal: Mapping[str, object]) -> None:
    atomic_write_text(
        _transaction_path(log),
        json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _discard_transaction(log: LogContext, journal: Mapping[str, object]) -> None:
    root = _transaction_root(log)
    entries = cast(Sequence[Mapping[str, object]], journal["entries"])
    for item in entries:
        (root / cast(str, item["staged"])).unlink(missing_ok=True)
    _transaction_path(log).unlink(missing_ok=True)
    sync_directory(root)
    root.rmdir()
    sync_directory(root.parent)


def _transaction_root(log: LogContext) -> Path:
    return (
        log.root
        / ".cache/research-log-operations"
        / PYRUN_EXCLUSIVITY_MIGRATION_RESIDUE
    )


def _transaction_path(log: LogContext) -> Path:
    return _transaction_root(log) / "transaction.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
