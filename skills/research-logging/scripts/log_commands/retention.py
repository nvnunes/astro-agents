"""Entry-scoped retention authoring actions."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Sequence

from validation.retention import (
    RetentionFile,
    RetentionRecord,
    load_retention_file,
    retention_file_from_records,
)

from .context import EntryContext
from .model import ActionError, ActionResult, RetentionArguments
from .storage import entry_lock, remove_or_write


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


def add_or_update(
    entry: EntryContext,
    *,
    action: str,
    arguments: RetentionArguments,
) -> ActionResult:
    """Add or completely replace one retention decision."""

    with entry_lock(entry):
        current = _load(entry)
        existing = {record.id: record for record in current.records} if current else {}
        candidate = _record(
            entry, arguments.record_id, arguments.targets, arguments.reason
        )
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
            remove_or_write(built.path, built.canonical_json())
        return _result(
            action,
            "dry-run" if arguments.dry_run else "changed",
            True,
        )


def rename(
    entry: EntryContext, old_id: str, new_id: str, *, dry_run: bool
) -> ActionResult:
    """Rename one retention identity while preserving its decision."""

    with entry_lock(entry):
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
            remove_or_write(built.path, built.canonical_json())
        return _result("rename", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, record_id: str, *, dry_run: bool) -> ActionResult:
    """Remove one selected retention record and delete an empty registry."""

    with entry_lock(entry):
        current = _load(entry)
        if current is None or record_id not in {item.id for item in current.records}:
            return _result("remove", "absent", False)
        remaining = tuple(item for item in current.records if item.id != record_id)
        text = None
        if remaining:
            text = retention_file_from_records(
                current.path, entry_root=entry.root, records=remaining
            ).canonical_json()
        if not dry_run:
            remove_or_write(current.path, text)
        return _result("remove", "dry-run" if dry_run else "changed", True)


def _record(
    entry: EntryContext,
    record_id: str,
    targets: Sequence[str],
    reason: str | None,
) -> RetentionRecord:
    if not targets:
        raise ActionError("retention.target.missing", "at least one target is required")
    normalized = tuple(_relative_target(entry.root, target) for target in targets)
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
