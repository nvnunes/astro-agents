"""Entry-scoped retention authoring actions."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from research_log_data import load_data_file
from validation.retention import (
    RetentionFile,
    RetentionRecord,
    load_retention_file,
    retention_file_from_records,
)

from .context import EntryContext
from .graph_state import (
    authored_uses,
    describe_uses,
    material_consumers,
    normalized_uses,
    publish_updates,
    related_entries,
)
from .model import ActionError, ActionResult, RetentionArguments
from .scaffold import observe_physical_entries
from .storage import entry_lock_under_log, log_lock


def list_records(entry: EntryContext) -> ActionResult:
    """Return bounded semantic retention records without mutation."""

    current = _load(entry)
    records = () if current is None else current.records
    return ActionResult(
        "retention.list",
        "unchanged",
        "retention.listed",
        False,
        records=tuple(
            {
                "coverage": "directory" if record.directory else "exact-paths",
                "id": record.id,
                "reason": record.reason,
                "targets": list(record.paths or (record.directory,)),
            }
            for record in records
        ),
    )


def require_unretained_paths(entry: EntryContext, paths: Sequence[str]) -> None:
    """Make transfers from intentional retention to active ownership explicit."""

    targets = tuple(Path(path).resolve() for path in paths)
    blockers: list[dict[str, Any]] = []
    for observed in observe_physical_entries(entry.log):
        if not any(
            target.is_relative_to(observed.root.resolve()) for target in targets
        ):
            continue
        candidate = EntryContext(entry.log, observed.id, observed.root)
        retained = _load(candidate)
        for record in retained.records if retained else ():
            coverage = [candidate.root / target for target in _targets(record)]
            if any(_overlaps(target, kept) for target in targets for kept in coverage):
                blockers.append(
                    {
                        "entry": candidate.id,
                        "retention": record.id,
                        "targets": list(_targets(record)),
                    }
                )
    if blockers:
        raise ActionError(
            "retention.ownership.conflict",
            "material is intentionally retained outside the active graph; "
            "remove its coverage with log retention update or delete "
            "before syncing ownership\n"
            + "\n".join(
                f"{blocker['entry']}: retention {blocker['retention']} "
                f"— {blocker['targets']}"
                for blocker in blockers
            ),
            records=tuple(blockers),
        )


def add_or_update(
    entry: EntryContext,
    *,
    action: str,
    arguments: RetentionArguments,
) -> ActionResult:
    """Ensure one decision or apply an additive coverage/reason update."""

    with (
        nullcontext() if arguments.dry_run else log_lock(entry.log),
        nullcontext() if arguments.dry_run else entry_lock_under_log(entry),
    ):
        current = _load(entry)
        existing = {record.id: record for record in current.records} if current else {}
        previous = existing.get(arguments.record_id)
        candidate = _candidate_record(entry, action, arguments, previous)
        _require_disconnected(entry, candidate)
        if action == "add" and arguments.record_id in existing:
            if existing[arguments.record_id] == candidate:
                return _result(action, "unchanged", False)
            raise ActionError("retention.record.conflict", arguments.record_id)
        if action == "update" and arguments.record_id not in existing:
            raise ActionError("retention.record.missing", arguments.record_id)
        if action == "update" and existing[arguments.record_id] == candidate:
            return _result(action, "unchanged", False)
        existing[arguments.record_id] = candidate
        built = retention_file_from_records(
            entry.root / "retention.json",
            entry_root=entry.root,
            records=tuple(existing.values()),
        )
        if not arguments.dry_run:
            publish_updates((entry,), {built.path: built.canonical_json()})
        removed = (
            set(_targets(previous)) - set(_targets(candidate)) if previous else set()
        )
        return _changed_result(action, arguments.dry_run, entry, removed)


def _targets(record: RetentionRecord | None) -> tuple[str, ...]:
    return () if record is None else record.paths or (str(record.directory),)


def _candidate_record(
    entry: EntryContext,
    action: str,
    arguments: RetentionArguments,
    previous: RetentionRecord | None,
) -> RetentionRecord:
    if action == "add":
        reason = (
            arguments.reason
            if arguments.reason is not None
            else previous.reason
            if previous
            else None
        )
        return _record(entry, arguments.record_id, arguments.targets, reason)
    if previous is None:
        raise ActionError("retention.record.missing", arguments.record_id)
    added = {_relative_target(entry.root, target) for target in arguments.targets}
    removed = {
        _relative_target(entry.root, target) for target in arguments.remove_targets
    }
    if added & removed:
        raise ActionError(
            "retention.target.conflict", "a target cannot be both added and removed"
        )
    targets = (set(_targets(previous)) | added) - removed
    reason = (
        None
        if arguments.clear_reason
        else arguments.reason
        if arguments.reason is not None
        else previous.reason
    )
    return _record(entry, arguments.record_id, tuple(sorted(targets)), reason)


def _require_disconnected(entry: EntryContext, record: RetentionRecord) -> None:
    targets = [entry.root / target for target in _targets(record)]
    connected: list[dict[str, Any]] = []
    for target in targets:
        connected.extend(material_consumers(entry, target))
    data_path = entry.root / "data.json"
    if data_path.exists():
        data = load_data_file(data_path, entry_root=entry.root)
        for item in data.inputs:
            if any(
                _overlaps(target, Path(item.canonical_target)) for target in targets
            ):
                for affected, _ in related_entries(entry, item.name):
                    connected.extend(authored_uses(affected, item.name))
                    connected.extend(normalized_uses(affected, item.name))
    if connected:
        raise ActionError(
            "retention.target.connected",
            "targets still belong to commands or evidence; remove their uses "
            "and sync their owners first: " + describe_uses(tuple(connected)),
            records=tuple(connected),
        )


def _overlaps(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return (
        first == second or first.is_relative_to(second) or second.is_relative_to(first)
    )


def _changed_result(
    action: str, dry_run: bool, entry: EntryContext, removed: set[str]
) -> ActionResult:
    return ActionResult(
        f"retention.{action}",
        "dry-run" if dry_run else "changed",
        "retention.dry-run" if dry_run else "retention.changed",
        True,
        records=tuple(
            {"disconnected": str(entry.root / target)}
            for target in sorted(removed)
            if (entry.root / target).exists()
        ),
    )


def rename(
    entry: EntryContext, old_id: str, new_id: str, *, dry_run: bool
) -> ActionResult:
    """Rename one retention identity while preserving its decision."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = _required(entry)
        existing = {record.id: record for record in current.records}
        if old_id not in existing:
            raise ActionError("retention.record.missing", old_id)
        if new_id in existing:
            raise ActionError("retention.record.conflict", new_id)
        old = existing.pop(old_id)
        existing[new_id] = RetentionRecord(
            new_id, paths=old.paths, directory=old.directory, reason=old.reason
        )
        built = retention_file_from_records(
            current.path, entry_root=entry.root, records=tuple(existing.values())
        )
        if not dry_run:
            publish_updates((entry,), {built.path: built.canonical_json()})
        return _result("rename", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, record_id: str, *, dry_run: bool) -> ActionResult:
    """Remove one selected retention record and delete an empty registry."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = _load(entry)
        if current is None or record_id not in {item.id for item in current.records}:
            return _result("delete", "absent", False)
        removed = set(
            _targets(next(item for item in current.records if item.id == record_id))
        )
        remaining = tuple(item for item in current.records if item.id != record_id)
        text = None
        if remaining:
            text = retention_file_from_records(
                current.path, entry_root=entry.root, records=remaining
            ).canonical_json()
        if not dry_run:
            publish_updates((entry,), {current.path: text})
        return _changed_result("delete", dry_run, entry, removed)


def _record(
    entry: EntryContext,
    record_id: str,
    targets: Sequence[str],
    reason: str | None,
) -> RetentionRecord:
    if not targets:
        raise ActionError("retention.target.missing", "at least one target is required")
    normalized = tuple(
        sorted({_relative_target(entry.root, target) for target in targets})
    )
    paths = [entry.root.joinpath(*PurePosixPath(item).parts) for item in normalized]
    directories = [path for path in paths if path.is_dir()]
    files = [path for path in paths if path.is_file()]
    if len(directories) == 1 and len(paths) == 1:
        return RetentionRecord(record_id, directory=normalized[0], reason=reason)
    if len(files) == len(paths):
        return RetentionRecord(record_id, paths=normalized, reason=reason)
    raise ActionError(
        "retention.target.mixed",
        "use either one directory or one or more regular files",
    )


def _relative_target(root: Path, value: str) -> str:
    lexical = Path(value)
    if lexical.is_absolute():
        raise ActionError("retention.target.invalid", "target must be entry-relative")
    target = root / lexical
    try:
        relative = target.absolute().relative_to(root).as_posix()
    except ValueError as error:
        raise ActionError("retention.target.outside_entry", value) from error
    if relative in {"", "."}:
        raise ActionError("retention.target.invalid", value)
    return relative


def _load(entry: EntryContext) -> RetentionFile | None:
    path = entry.root / "retention.json"
    return (
        load_retention_file(path, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _required(entry: EntryContext) -> RetentionFile:
    current = _load(entry)
    if current is None:
        raise ActionError("retention.record.missing", "retention registry is absent")
    return current


def _result(action: str, status: str, changed: bool) -> ActionResult:
    return ActionResult(
        f"retention.{action}",
        status,
        f"retention.{status}",
        changed,
    )
