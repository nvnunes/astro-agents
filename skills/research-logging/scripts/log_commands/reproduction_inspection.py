"""Bounded read-only inspection of one native, immutable reproduction target.

Counts and statuses come from the saved model's classifiers. Current Markdown,
registries and generated artifacts never reinterpret a saved outcome. Retained
streams are optional supplements, not a prerequisite for diagnosis.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from research_log_result_store import ResultStoreError, result_snapshot
from validation.pyrun_state import NAME_RE
from validation.summary import human_saved_at

from .context import ENTRY_ID_RE, LogContext
from .model import ActionError
from .reproduction_domain import (
    MAX_INSPECTION_BYTES,
    MAX_INSPECTION_ITEMS,
    MAX_STREAM_TAIL_BYTES,
    PROBLEM_CODES,
    ArtifactRef,
    Classification,
    ExecutionRef,
    NotComparedReason,
    ReproductionDomainError,
)
from .reproduction_paths import canonical_run_root, saved_run_path
from .reproduction_run import run_identity
from .reproduction_saved_run import SavedRun
from .reproduction_saved_storage import (
    EmptyConfirmation,
    load_empty_confirmation,
    load_latest_saved_run,
    load_saved_run,
)
from .reproduction_summary import saved_counts
from .reproduction_work import ArtifactWork, CommandWork

CURSOR_SCHEMA = "research-log-reproduction-cursor/1"
MAX_CURSOR_BYTES = 2 * 1024
COMMAND_STATUSES = frozenset(
    {"not-run", "skipped-by-policy", "succeeded", "failed", "blocked"}
)
COMMAND_REASONS = PROBLEM_CODES | {
    "not-needed",
    "previous-failure",
    "previous-block",
    "skipped-by-policy",
}
ARTIFACT_STATUSES = frozenset({"matched", "not-matched", "not-compared"})
ARTIFACT_REASONS = frozenset(reason.value for reason in NotComparedReason)
COMMAND_DETAIL_SECTIONS = (
    "artifacts",
    "dependencies",
    "diagnoses",
    "recipe.inputs",
    "recipe.outputs",
    "recipe.parameters",
    "recipe.parameter_roles",
    "recipe.environment",
    "result.outputs",
    "result.argv",
    "result.blocked_by",
)
ARTIFACT_DETAIL_SECTIONS = ("diagnoses", "result.evidence")


@dataclass(frozen=True)
class SavedInspection:
    """One authenticated run and generation from the same read transaction."""

    log: LogContext
    run: SavedRun
    generation: int


@dataclass(frozen=True)
class EmptyInspection:
    """Confirmed empty coverage without fabricating a run identity or result."""

    log: LogContext
    confirmation: EmptyConfirmation

    @property
    def generation(self) -> int:
        return self.confirmation.generation


@dataclass(frozen=True)
class ListSelection:
    """Closed saved-list selectors, all included in continuation binding."""

    kind: str
    entry: str | None = None
    cid: str | None = None
    status: str | None = None
    reason: str | None = None
    format: str = "text"

    def __post_init__(self) -> None:
        if self.kind not in {"commands", "artifacts"}:
            raise ActionError(
                "reproduction.selector.invalid", "List commands or artifacts"
            )
        if self.entry is not None and not ENTRY_ID_RE.fullmatch(self.entry):
            raise ActionError("reproduction.selector.invalid", "Use a stable entry ID")
        if self.cid is not None and (
            self.kind != "artifacts" or not NAME_RE.fullmatch(self.cid)
        ):
            raise ActionError(
                "reproduction.selector.invalid", "Invalid artifact CID filter"
            )
        statuses = COMMAND_STATUSES if self.kind == "commands" else ARTIFACT_STATUSES
        reasons = COMMAND_REASONS if self.kind == "commands" else ARTIFACT_REASONS
        if self.status is not None and self.status not in statuses:
            raise ActionError("reproduction.selector.invalid", "Unknown status")
        if self.reason is not None and self.reason not in reasons:
            raise ActionError("reproduction.selector.invalid", "Unknown reason")
        if self.format not in {"text", "json"}:
            raise ActionError("reproduction.selector.invalid", "Use text or json")
        _validate_status_reason(self)


def _validate_status_reason(selection: ListSelection) -> None:
    if selection.status is None or selection.reason is None:
        return
    if selection.kind == "artifacts":
        allowed = {"not-compared"}
    elif selection.reason in PROBLEM_CODES:
        allowed = {"failed", "blocked"}
    elif selection.reason == "skipped-by-policy":
        allowed = {"skipped-by-policy"}
    else:
        allowed = {"not-run"}
    if selection.status not in allowed:
        raise ActionError(
            "reproduction.selector.invalid", "Reason is outside the selected status"
        )


def _load_empty_inspection(
    db: sqlite3.Connection, log: LogContext
) -> EmptyInspection | None:
    receipt = load_empty_confirmation(db)
    if receipt is None:
        return None
    if receipt.summary != str(log.summary):
        raise ReproductionDomainError("empty confirmation belongs to another log")
    return EmptyInspection(log, receipt)


def load_inspection(
    log: LogContext, run_id: str | None = None
) -> SavedInspection | EmptyInspection:
    """Read native facts without locks, writes, old readers or live source scans.

    An explicit run must belong to this log. Default selection is the latest
    normally published run, including its recorded entry-target coverage.
    Missing, unsupported and malformed results have separate public errors.
    """

    if run_id is not None:
        try:
            run_identity(run_id)
        except ReproductionDomainError as error:
            raise ActionError("reproduction.selector.invalid", str(error)) from error
    try:
        with result_snapshot(log.root) as db:
            if run_id is None:
                run = load_latest_saved_run(db)
                if run is None:
                    empty = _load_empty_inspection(db, log)
                    if empty is not None:
                        return empty
                    raise ActionError(
                        "reproduction.results.missing", "No saved reproduction"
                    )
            else:
                run = load_saved_run(db, run_id)
            if run is None:
                raise ActionError("reproduction.run.unknown", f"Unknown run: {run_id}")
            if run.summary != str(log.summary):
                raise ActionError(
                    "reproduction.results.invalid", "Saved run belongs to another log"
                )
            row = db.execute(
                "SELECT generation FROM store_state WHERE domain='reproduction'"
            ).fetchone()
            return SavedInspection(log, run, 0 if row is None else int(row[0]))
    except ResultStoreError as error:
        code = "missing" if error.code == "results.store.missing" else "invalid"
        raise ActionError(f"reproduction.results.{code}", str(error)) from error
    except ReproductionDomainError as error:
        code = "unsupported" if "unsupported" in str(error) else "invalid"
        raise ActionError(f"reproduction.results.{code}", str(error)) from error


def inspection_summary(
    inspection: SavedInspection | EmptyInspection,
) -> dict[str, object]:
    """Return compact saved identity/coverage and derived hierarchical totals."""

    if isinstance(inspection, EmptyInspection):
        return {
            "log": str(inspection.log.root),
            "run_id": None,
            "saved_at": inspection.confirmation.confirmed_at,
            "target": {"kind": "log", "entry": None},
            "status": "empty-confirmed",
            "commands": {
                "total": 0,
                "not_run": {
                    "total": 0,
                    "not_needed": 0,
                    "previous_failure": 0,
                    "previous_block": 0,
                },
                "skipped_by_policy": 0,
                "selected": {
                    "total": 0,
                    "succeeded": 0,
                    "failed": {"total": 0, "reasons": {}},
                    "blocked": {"total": 0, "reasons": {}},
                },
            },
            "artifacts": {
                "total": 0,
                "matched": 0,
                "not_matched": 0,
                "not_compared": {"total": 0, "reasons": {}},
            },
        }
    run = inspection.run
    return {
        "log": str(inspection.log.root),
        "run_id": run.run_id,
        "saved_at": run.finished_at,
        "target": run.target.as_dict(),
        "status": "complete",
        **saved_counts(run),
    }


def _human(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").capitalize()


def _count_lines(label: str, value: object, depth: int = 0) -> list[str]:
    if isinstance(value, Mapping):
        lines = [f"{'  ' * depth}{label} — {value['total']}"]
        for key, child in value.items():
            if key == "total":
                continue
            if key == "reasons":
                for reason, count in child.items():
                    lines.extend(_count_lines(_human(reason), count, depth + 1))
            else:
                lines.extend(_count_lines(_human(key), child, depth + 1))
        return lines
    return [f"{'  ' * depth}{label} — {value}"]


def render_saved_summary(inspection: SavedInspection | EmptyInspection) -> str:
    """Shared vertical body for single-log show and summary-only reproduction.md."""

    summary = inspection_summary(inspection)
    target_value = summary["target"]
    assert isinstance(target_value, dict)
    target = "Log" if target_value["kind"] == "log" else target_value["entry"]
    lines = [
        f"Log: {inspection.log.root.name}",
        f"Run: {summary['run_id'] or '—'}",
        f"Saved: {human_saved_at(str(summary['saved_at']))}",
        f"Target: {target}",
        "Status: Empty confirmed"
        if isinstance(inspection, EmptyInspection)
        else "Status: Complete",
        "",
    ]
    counts = summary
    lines.extend(_count_lines("Commands", counts["commands"]))
    lines.append("")
    lines.extend(_count_lines("Artifacts", counts["artifacts"]))
    return "\n".join(lines) + "\n"


def _detail_command(
    inspection: SavedInspection, identity: ExecutionRef | ArtifactRef
) -> str:
    argv = ["log", "reproduce", "detail"]
    if isinstance(identity, ExecutionRef):
        argv += [
            "command",
            "--cid",
            identity.cid,
            "--execution-id",
            identity.execution_id,
        ]
    else:
        argv += ["artifact", "--artifact", identity.artifact]
    argv += [
        "--path",
        str(inspection.log.root),
        "--entry",
        identity.entry,
        "--run-id",
        inspection.run.run_id,
    ]
    return shlex.join(argv)


def _command_row(inspection: SavedInspection, work: CommandWork) -> dict[str, object]:
    classification = inspection.run.command_classification(work.identity)
    problem = inspection.run.primary_problem(work.identity)
    return {
        "identity": work.identity.as_dict(),
        "status": classification.status,
        "reason": classification.reason,
        "explanation": problem.explanation if problem is not None else None,
        "detail_command": _detail_command(inspection, work.identity),
    }


def _artifact_row(inspection: SavedInspection, work: ArtifactWork) -> dict[str, object]:
    classification = inspection.run.artifact_classification(work.identity)
    result = inspection.run.artifact_result(work.identity)
    problem = (
        inspection.run.problem(result.problem_ids[0]) if result.problem_ids else None
    )
    if (
        problem is None
        and classification.status == "not-compared"
        and work.producer is not None
    ):
        problem = inspection.run.primary_problem(work.producer)
    return {
        "identity": work.identity.as_dict(),
        "status": classification.status,
        "reason": classification.reason,
        "explanation": problem.explanation if problem is not None else None,
        "detail_command": _detail_command(inspection, work.identity),
    }


def _matches_selection(
    selection: ListSelection, classification: Classification
) -> bool:
    return (selection.status is None or selection.status == classification.status) and (
        selection.reason is None or selection.reason == classification.reason
    )


def _selected_work(
    inspection: SavedInspection, selection: ListSelection
) -> list[CommandWork | ArtifactWork]:
    run = inspection.run
    if (
        selection.entry is not None
        and run.target.kind == "entry"
        and selection.entry != run.target.entry
    ):
        raise ActionError(
            "reproduction.selector.invalid", "Entry is outside this run's target"
        )
    work = run.commands if selection.kind == "commands" else run.artifacts
    return [
        item
        for item in work
        if (selection.entry is None or item.identity.entry == selection.entry)
        and (
            selection.cid is None
            or (
                isinstance(item, ArtifactWork)
                and item.producer is not None
                and item.producer.cid == selection.cid
            )
        )
        and _matches_selection(
            selection,
            run.command_classification(item.identity)
            if isinstance(item, CommandWork)
            else run.artifact_classification(item.identity),
        )
    ]


def _binding(inspection: SavedInspection, selection: ListSelection) -> str:
    fields = {
        "log": str(inspection.log.root),
        "run_id": inspection.run.run_id,
        "generation": inspection.generation,
        **vars(selection),
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _cursor(kind: str, binding: str, offset: int) -> str:
    raw = json.dumps(
        {"schema": CURSOR_SCHEMA, "kind": kind, "binding": binding, "offset": offset},
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(cursor: str | None, kind: str, binding: str) -> int:
    if cursor is None:
        return 0
    try:
        if len(cursor.encode()) > MAX_CURSOR_BYTES or not re.fullmatch(
            r"[A-Za-z0-9_-]+", cursor
        ):
            raise ValueError("Invalid cursor encoding")
        value = json.loads(
            base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
        )
        if (
            set(value) != {"schema", "kind", "binding", "offset"}
            or value["schema"] != CURSOR_SCHEMA
        ):
            raise ValueError("Invalid cursor fields")
        if type(value["offset"]) is not int or value["offset"] < 0:
            raise ValueError("Invalid cursor offset")
        if value["kind"] != kind or value["binding"] != binding:
            raise ActionError(
                "reproduction.cursor.stale",
                "Cursor does not match this run and selectors",
            )
        return value["offset"]
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ActionError(
            "reproduction.cursor.invalid", "Invalid continuation cursor"
        ) from error


def bounded_response(value: dict[str, object]) -> dict[str, object]:
    """Fail closed rather than silently removing scalar diagnosis or context."""

    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_INSPECTION_BYTES:
        raise ActionError(
            "reproduction.limit.exceeded", "Inspection exceeds its fixed byte bound"
        )
    return value


def list_saved(
    inspection: SavedInspection | EmptyInspection,
    selection: ListSelection,
    cursor: str | None = None,
) -> dict[str, object]:
    """Page deterministic saved identities; cursors bind every selector and format."""

    if isinstance(inspection, EmptyInspection):
        if cursor is not None:
            raise ActionError("reproduction.cursor.stale", "Empty target has no page")
        return {
            "run_id": None,
            "kind": selection.kind,
            "matched": 0,
            "returned": 0,
            "remaining": 0,
            "cursor": None,
            "items": [],
        }
    work = _selected_work(inspection, selection)
    binding = _binding(inspection, selection)
    offset = _offset(cursor, selection.kind, binding)
    if offset > len(work):
        raise ActionError(
            "reproduction.cursor.invalid", "Cursor is outside the matched list"
        )
    page = work[offset : offset + MAX_INSPECTION_ITEMS]
    rows = [
        _command_row(inspection, item)
        if isinstance(item, CommandWork)
        else _artifact_row(inspection, item)
        for item in page
    ]
    end = offset + len(page)
    return bounded_response(
        {
            "run_id": inspection.run.run_id,
            "kind": selection.kind,
            "matched": len(work),
            "returned": len(page),
            "remaining": len(work) - end,
            "cursor": _cursor(selection.kind, binding, end)
            if end < len(work)
            else None,
            "items": rows,
        }
    )


def _stream_tail(
    inspection: SavedInspection, project_root: str, path: str | None
) -> dict[str, object]:
    """Read only a safe retained stream; missing files never change outcome."""

    unavailable: dict[str, object] = {
        "path": path,
        "available": False,
        "truncated": False,
        "excerpt": None,
        "reason": "not-recorded",
    }
    if path is None:
        return unavailable
    relative = PurePosixPath(path)
    if ".." in relative.parts:
        return {**unavailable, "reason": "path-invalid"}
    logical_root = Path(project_root).joinpath(*saved_run_path(inspection.run).parts)
    if not relative.is_absolute():
        unavailable["path"] = str(logical_root.joinpath(*relative.parts))
    try:
        run_root = canonical_run_root(
            logical_root, Path(project_root), require_exists=True
        )
        relative = _relative_stream_path(relative, logical_root, run_root)
    except (OSError, ValueError, RuntimeError):
        return {**unavailable, "reason": "unavailable"}
    target = run_root
    for part in relative.parts:
        target /= part
        if target.is_symlink():
            return {**unavailable, "reason": "path-invalid"}
    try:
        if not target.is_file():
            raise OSError("Retained stream is not a regular file")
        with target.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - MAX_STREAM_TAIL_BYTES))
            raw = stream.read(MAX_STREAM_TAIL_BYTES)
    except OSError:
        return {**unavailable, "reason": "unavailable"}
    text = raw.decode("utf-8", errors="replace")
    excerpt = "".join(
        char if char in "\n\t" or ord(char) >= 32 and ord(char) != 127 else "�"
        for char in text
    )
    return {
        "path": unavailable["path"],
        "available": True,
        "truncated": size > len(raw),
        "excerpt": excerpt,
        "reason": None,
        "bytes": size,
    }


def _relative_stream_path(
    path: PurePosixPath, logical_root: Path, run_root: Path
) -> PurePosixPath:
    if not path.is_absolute():
        return path
    for root in (logical_root, run_root):
        if path.is_relative_to(root):
            return path.relative_to(root)
    raise ValueError("Stream is outside the retained run")


def _command_causes(
    inspection: SavedInspection, identity: ExecutionRef
) -> list[dict[str, object]]:
    """Follow saved prerequisite links once, retaining all relevant owned causes."""

    pending, seen = [identity], set()
    problems: dict[str, dict[str, object]] = {}
    run = inspection.run
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        work = run.command(current)
        result = run.command_result(current)
        ids = (
            result.problem_ids
            if result is not None and result.problem_ids
            else work.problem_ids
        )
        for problem_id in ids:
            problems.setdefault(problem_id, run.problem(problem_id).as_dict())
        prerequisites = result.blocked_by if result is not None else work.dependencies
        pending.extend(reversed(prerequisites))
    return list(problems.values())


def _collection_owner(
    packet: dict[str, object], name: str
) -> tuple[dict[str, object], str]:
    parts = name.split(".")
    owner = packet
    for part in parts[:-1]:
        child = owner.get(part)
        if not isinstance(child, dict):
            return {}, parts[-1]
        owner = child
    return owner, parts[-1]


@dataclass(frozen=True)
class _DetailPage:
    section: str | None
    cursor: str | None
    format: str


def _detail_sections(
    inspection: SavedInspection,
    identity: ExecutionRef | ArtifactRef,
    packet: dict[str, object],
    sections: tuple[str, ...],
    page: _DetailPage,
) -> dict[str, object]:
    """Page only known collection fields; never remove scalar diagnosis."""

    section, cursor, format = page.section, page.cursor, page.format

    if format not in {"text", "json"} or (
        section is not None and section not in sections
    ):
        raise ActionError(
            "reproduction.selector.invalid", "Invalid detail section or format"
        )
    if cursor is not None and section is None:
        raise ActionError(
            "reproduction.selector.invalid", "Detail cursor requires --section"
        )
    pages: dict[str, object] = {}
    for name in sections:
        owner, field = _collection_owner(packet, name)
        value = owner.get(field)
        if not isinstance(value, (list, dict)):
            if section == name:
                raise ActionError(
                    "reproduction.selector.invalid", "Section is not recorded"
                )
            continue
        binding = hashlib.sha256(
            json.dumps(
                {
                    "run": inspection.run.run_id,
                    "generation": inspection.generation,
                    "identity": identity.as_dict(),
                    "section": name,
                    "format": format,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        offset = _offset(cursor if section == name else None, "detail", binding)
        if offset >= len(value) and offset != 0:
            raise ActionError(
                "reproduction.cursor.invalid", "Cursor is outside the section"
            )
        items = sorted(value) if isinstance(value, dict) else value
        selected = items[offset : offset + MAX_INSPECTION_ITEMS]
        owner[field] = (
            {key: value[key] for key in selected}
            if isinstance(value, dict)
            else selected
        )
        end = offset + len(selected)
        next_cursor = _cursor("detail", binding, end) if end < len(value) else None
        if next_cursor is not None or section == name:
            pages[name] = {
                "matched": len(value),
                "returned": len(selected),
                "remaining": len(value) - end,
                "cursor": next_cursor,
                "next_command": _detail_command(inspection, identity)
                + " "
                + shlex.join(
                    ["--section", name, "--cursor", next_cursor, "--format", format]
                )
                if next_cursor
                else None,
            }
    if pages:
        packet["sections"] = pages
    return bounded_response(packet)


def command_detail(
    inspection: SavedInspection,
    identity: ExecutionRef,
    *,
    section: str | None = None,
    cursor: str | None = None,
    format: str = "text",
) -> dict[str, object]:
    """Lead with diagnosed outcome and retain the accepted recipe/actual invocation."""

    run = inspection.run
    try:
        work = run.command(identity)
    except KeyError as error:
        raise ActionError(
            "reproduction.identity.unknown", "Command is not in this saved target"
        ) from error
    result = run.command_result(identity)
    facts = result.as_dict() if result is not None else None
    if facts is not None:
        facts = {
            key: value
            for key, value in facts.items()
            if key not in {"identity", "problem_ids"}
        }
    return _detail_sections(
        inspection,
        identity,
        {
            "run_id": run.run_id,
            **_command_row(inspection, work),
            "recipe": work.execution.recipe.as_dict(),
            "entry_root": work.entry_root,
            "project_root": work.project_root,
            "dependencies": [item.as_dict() for item in work.dependencies],
            "diagnoses": _command_causes(inspection, identity),
            "result": facts,
            "stdout": _stream_tail(
                inspection,
                work.project_root,
                result.stdout_path if result is not None else None,
            ),
            "stderr": _stream_tail(
                inspection,
                work.project_root,
                result.stderr_path if result is not None else None,
            ),
            "artifacts": [
                _artifact_row(inspection, item)
                for item in run.artifacts
                if item.producer == identity
            ],
        },
        COMMAND_DETAIL_SECTIONS,
        _DetailPage(section, cursor, format),
    )


def artifact_detail(
    inspection: SavedInspection,
    identity: ArtifactRef,
    *,
    section: str | None = None,
    cursor: str | None = None,
    format: str = "text",
) -> dict[str, object]:
    """Expose retained difference/comparator facts without checking today's outputs."""

    run = inspection.run
    try:
        work = run.artifact(identity)
    except KeyError as error:
        raise ActionError(
            "reproduction.identity.unknown", "Artifact is not in this saved target"
        ) from error
    result = run.artifact_result(identity)
    facts = {
        key: value
        for key, value in result.as_dict().items()
        if key not in {"identity", "problem_ids"}
    }
    return _detail_sections(
        inspection,
        identity,
        {
            "run_id": run.run_id,
            **_artifact_row(inspection, work),
            "retained_path": work.retained_path,
            "producer": _command_row(inspection, run.command(work.producer))
            if work.producer is not None
            else None,
            "output": work.output,
            "result": facts,
            "diagnoses": [run.problem(item).as_dict() for item in result.problem_ids]
            if result.problem_ids
            else _command_causes(inspection, work.producer)
            if work.producer is not None
            else [],
        },
        ARTIFACT_DETAIL_SECTIONS,
        _DetailPage(section, cursor, format),
    )
