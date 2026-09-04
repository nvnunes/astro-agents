"""Closed identity and registry coordination for explicit Reorganize work."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Mapping

from research_log_data import (
    DataFile,
    InputResource,
    data_file_from_inputs,
    load_data_file,
    validate_log_consistency,
    verify_fingerprint,
)
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    evidence_file_from_records,
    load_evidence_file,
)
from validation.operation_state import begin_reorganization, finish_reorganization
from validation.retention import load_retention_file

from .context import EntryContext, LogContext, LogCreationContext, resolve_entry
from .model import ActionError, ActionResult, EntryUpdateArguments, TransferArguments
from .scaffold import (
    EntryObservation,
    SummaryEntryObservation,
    observe_entries,
    observe_physical_entries,
    observe_summary_entries,
    validate_entry_date,
    validate_entry_slug,
    validate_entry_title,
)
from .storage import (
    atomic_write_texts,
    log_and_entry_locks,
    log_creation_lock,
    log_lock,
)

_DOCUMENT_RE = re.compile(r"(?P<id>e[0-9]{3,})(?P<suffix>[a-z]?)\.md\Z")


@dataclass(frozen=True)
class _EntryUpdate:
    current: EntryObservation
    destination: Path
    date: str
    slug: str
    title: str | None


@dataclass(frozen=True)
class _ReorderPlan:
    desired: tuple[str, ...]
    entries: Mapping[str, EntryObservation]
    roots: Mapping[Path, Path]
    documents: Mapping[Path, Path]
    ids: Mapping[str, str]


def update_entry(entry: EntryContext, arguments: EntryUpdateArguments) -> ActionResult:
    """Publish one pre-edited date, slug, or title change."""

    if arguments.date is None and arguments.slug is None and arguments.title is None:
        raise ActionError("reorganize.update.empty", "update-entry requires one field")
    title = validate_entry_title(arguments.title) if arguments.title else None
    with log_and_entry_locks(entry.log, (entry,)):
        current = _physical(entry)
        date = validate_entry_date(arguments.date) if arguments.date else current.date
        slug = validate_entry_slug(arguments.slug) if arguments.slug else current.slug
        summary = _summary_by_id(entry.log).get(entry.id)
        destination = entry.root.parent / f"{date}-{entry.id}-{slug}"
        plan = _EntryUpdate(current, destination, date, slug, title)
        _require_summary_identity(summary, plan)
        if arguments.date is not None or arguments.title is not None:
            _require_headings(current.documents, date, title)
        changed = destination != entry.root
        if not changed:
            return _result("update-entry", "unchanged", False, (entry.log.summary,))
        paths = (entry.root, destination)
        if arguments.dry_run:
            return _result("update-entry", "dry-run", True, paths)
        _publish_identity(entry.log, {entry.root: destination}, {})
        return _result("update-entry", "changed", True, paths)


def reorder(
    log: LogContext, desired: tuple[str, ...], *, dry_run: bool
) -> ActionResult:
    """Assign sequential entry IDs from one complete desired ordering."""

    physical = observe_physical_entries(log)
    current_ids = tuple(item.id for item in physical)
    if len(desired) != len(set(desired)) or set(desired) != set(current_ids):
        raise ActionError(
            "reorganize.reorder.incomplete",
            "--entries must contain every current entry ID exactly once",
        )
    by_id = {item.id: item for item in physical}
    id_map = {old: f"e{index:03d}" for index, old in enumerate(desired, 1)}
    selected = tuple(resolve_entry(log, entry_id) for entry_id in current_ids)
    with log_and_entry_locks(log, selected):
        locked = observe_physical_entries(log)
        if tuple(item.id for item in locked) != current_ids:
            raise ActionError(
                "reorganize.reorder.changed",
                "entry inventory changed while acquiring locks",
            )
        physical = locked
        by_id = {item.id: item for item in physical}
        roots = {
            item.root: item.root.with_name(f"{item.date}-{id_map[item.id]}-{item.slug}")
            for item in physical
        }
        documents = _document_moves(physical, roots, id_map)
        plan = _ReorderPlan(desired, by_id, roots, documents, id_map)
        _require_reorder_summary(log, plan)
        changed = any(source != destination for source, destination in roots.items())
        paths = tuple(path for pair in roots.items() for path in pair)
        if not changed:
            return _result("reorder", "unchanged", False, (log.summary,))
        if dry_run:
            return _result("reorder", "dry-run", True, paths)
        _publish_identity(log, roots, documents)
        return _result("reorder", "changed", True, paths)


def relocate_log(log: LogContext, destination: Path, *, dry_run: bool) -> ActionResult:
    """Relocate one maintained summary/root pair on one filesystem."""

    target = destination if destination.is_absolute() else Path.cwd() / destination
    if target.suffix == ".md" or target.name == "entries" or target.is_symlink():
        raise ActionError(
            "reorganize.relocate.invalid", "--to names a logical log base"
        )
    target = target.parent.resolve() / target.name
    target_summary = target.parent / f"{target.name}.md"
    _require_relocation_target(log, target, target_summary)
    _require_relocation_markdown(log, target.name)
    paths = (log.summary, log.root, target_summary, target)
    if dry_run:
        return _result("relocate-log", "dry-run", True, paths)
    from .context import resolve_project_root

    creation = LogCreationContext(
        target_summary,
        target,
        resolve_project_root(target.parent),
    )
    with log_creation_lock(creation), log_lock(log):
        _require_relocation_target(log, target, target_summary)
        _require_relocation_markdown(log, target.name)
        _publish_relocation(log, target_summary, target)
    return _result("relocate-log", "changed", True, paths)


def remove_empty_entry(entry: EntryContext, *, dry_run: bool) -> ActionResult:
    """Remove one canonical scaffold after the summary item is absent."""

    with log_and_entry_locks(entry.log, (entry,)):
        if entry.id in _summary_by_id(entry.log):
            raise ActionError(
                "reorganize.remove.summary_present",
                "remove the maintained-summary item before the scaffold",
            )
        children = tuple(sorted(entry.root.iterdir(), key=lambda path: path.name))
        document = entry.root / f"{entry.id}.md"
        runner = entry.root / "pyrun"
        if set(children) != {document, runner} or not runner.is_symlink():
            raise ActionError("reorganize.remove.nonempty", str(entry.root))
        text = document.read_text(encoding="utf-8")
        if not re.fullmatch(r"#[^\r\n]+\r?\n?", text):
            raise ActionError("reorganize.remove.nonempty", str(document))
        _require_no_entry_reference(entry.log, entry)
        paths = (document, runner, entry.root)
        if dry_run:
            return _result("remove-empty-entry", "dry-run", True, paths)
        _remove_scaffold(entry, document, runner)
        return _result("remove-empty-entry", "changed", True, paths)


def transfer(log: LogContext, arguments: TransferArguments) -> ActionResult:
    """Delegate one explicitly selected registry transfer."""

    from .reorganize_transfer import transfer_registries

    source = resolve_entry(log, arguments.source_entry)
    destination = resolve_entry(log, arguments.destination_entry)
    with log_and_entry_locks(
        log, (source, destination) if source != destination else (source,)
    ):
        return transfer_registries(source, destination, arguments)


def _publish_identity(
    log: LogContext,
    roots: Mapping[Path, Path],
    documents: Mapping[Path, Path],
) -> None:
    data, evidence = _load_identity_registries(log)
    residue = begin_reorganization(log.root)
    completed: list[tuple[Path, Path]] = []
    try:
        _rename_simultaneously(roots, completed)
        realized_documents = {
            _under_moved_root(source, roots): destination
            for source, destination in documents.items()
        }
        _rename_simultaneously(realized_documents, completed)
        observe_entries(log)
        updates = _identity_registry_updates(log, roots, documents, data, evidence)
        atomic_write_texts(updates)
    except (OSError, UnicodeError, ValueError) as error:
        rollback = _rollback_renames(completed)
        if not rollback:
            finish_reorganization(_moved_residue(residue, roots))
        detail = f"; rollback failed: {'; '.join(rollback)}" if rollback else ""
        raise ActionError(
            "reorganize.publication.failed", f"{error}{detail}"
        ) from error
    finish_reorganization(_moved_residue(residue, roots))


def _publish_relocation(log: LogContext, summary: Path, root: Path) -> None:
    data, _ = _load_identity_registries(log)
    residue = begin_reorganization(log.root)
    completed: list[tuple[Path, Path]] = []
    try:
        os.replace(log.root, root)
        completed.append((log.root, root))
        os.replace(log.summary, summary)
        completed.append((log.summary, summary))
        moved_log = LogContext(summary, root)
        observe_entries(moved_log)
        updates = _relocated_data_updates(log, moved_log, data)
        atomic_write_texts(updates)
    except (OSError, UnicodeError, ValueError) as error:
        rollback = _rollback_renames(completed)
        if not rollback:
            finish_reorganization(root / residue.relative_to(log.root))
        detail = f"; rollback failed: {'; '.join(rollback)}" if rollback else ""
        raise ActionError("reorganize.relocate.failed", f"{error}{detail}") from error
    finish_reorganization(root / residue.relative_to(log.root))


def _load_identity_registries(
    log: LogContext,
) -> tuple[dict[Path, DataFile], dict[Path, EvidenceFile]]:
    data: dict[Path, DataFile] = {}
    evidence: dict[Path, EvidenceFile] = {}
    for entry in observe_physical_entries(log):
        data_path = entry.root / "data.json"
        evidence_path = entry.root / "evidence.json"
        if data_path.exists() or data_path.is_symlink():
            data[entry.root] = load_data_file(data_path, entry_root=entry.root)
            for item in data[entry.root].inputs:
                verify_fingerprint(item)
        if evidence_path.exists() or evidence_path.is_symlink():
            evidence[entry.root] = load_evidence_file(
                evidence_path, log_root=log.root, entry_root=entry.root
            )
        retention = entry.root / "retention.json"
        if retention.exists() or retention.is_symlink():
            load_retention_file(retention, entry_root=entry.root)
    return data, evidence


def _identity_registry_updates(
    log: LogContext,
    roots: Mapping[Path, Path],
    documents: Mapping[Path, Path],
    data: Mapping[Path, DataFile],
    evidence: Mapping[Path, EvidenceFile],
) -> dict[Path, str]:
    updates: dict[Path, str] = {}
    data_candidates: list[DataFile] = []
    for owner, current_data in data.items():
        new_owner = roots.get(owner, owner)
        items = tuple(
            _mapped_input(item, new_owner, roots) for item in current_data.inputs
        )
        data_candidate = data_file_from_inputs(
            new_owner / "data.json", entry_root=new_owner, inputs=items
        )
        data_candidates.append(data_candidate)
        if tuple(item.location for item in items) != tuple(
            item.location for item in current_data.inputs
        ):
            updates[data_candidate.path] = data_candidate.canonical_json()
    validate_log_consistency(tuple(data_candidates))
    for owner, current_evidence in evidence.items():
        new_owner = roots.get(owner, owner)
        records = tuple(
            _mapped_evidence(record, log, roots, documents)
            for record in current_evidence.records
        )
        if tuple(record.document for record in records) != tuple(
            record.document for record in current_evidence.records
        ):
            evidence_candidate = evidence_file_from_records(
                new_owner / "evidence.json",
                log_root=log.root,
                entry_root=new_owner,
                records=records,
            )
            updates[evidence_candidate.path] = evidence_candidate.canonical_json()
    return updates


def _relocated_data_updates(
    old_log: LogContext, new_log: LogContext, data: Mapping[Path, DataFile]
) -> dict[Path, str]:
    roots = {owner: new_log.root / owner.relative_to(old_log.root) for owner in data}
    updates: dict[Path, str] = {}
    data_candidates: list[DataFile] = []
    for owner, current in data.items():
        new_owner = roots[owner]
        items = tuple(
            _relocated_input(item, old_log.root, new_log.root, new_owner)
            for item in current.inputs
        )
        built = data_file_from_inputs(
            new_owner / "data.json", entry_root=new_owner, inputs=items
        )
        data_candidates.append(built)
        if tuple(item.location for item in items) != tuple(
            item.location for item in current.inputs
        ):
            updates[built.path] = built.canonical_json()
    validate_log_consistency(tuple(data_candidates))
    return updates


def _mapped_input(
    item: InputResource,
    new_owner: Path,
    roots: Mapping[Path, Path],
) -> InputResource:
    if Path(item.location).is_absolute():
        if _map_path(Path(item.canonical_target), roots) != Path(item.canonical_target):
            raise ActionError(
                "reorganize.reference.unsupported",
                f"absolute internal data target: {item.location}",
            )
        return item
    target = _map_path(Path(item.canonical_target), roots)
    location = os.path.relpath(target, start=new_owner).replace(os.sep, "/")
    return replace(
        item, location=location, canonical_target=target.resolve().as_posix()
    )


def _relocated_input(
    item: InputResource,
    old_log: Path,
    new_log: Path,
    new_owner: Path,
) -> InputResource:
    if Path(item.location).is_absolute():
        return item
    target = Path(item.canonical_target)
    try:
        target = new_log / target.relative_to(old_log)
    except ValueError:
        pass
    location = os.path.relpath(target, start=new_owner).replace(os.sep, "/")
    return replace(
        item, location=location, canonical_target=target.resolve().as_posix()
    )


def _mapped_evidence(
    record: EvidenceRecord,
    log: LogContext,
    roots: Mapping[Path, Path],
    documents: Mapping[Path, Path],
) -> EvidenceRecord:
    source = log.root / PurePosixPath(record.document)
    destination = documents.get(source, _map_path(source, roots))
    document = destination.relative_to(log.root).as_posix()
    return replace(record, document=document)


def _map_path(path: Path, roots: Mapping[Path, Path]) -> Path:
    for source, destination in roots.items():
        try:
            return destination / path.relative_to(source)
        except ValueError:
            continue
    return path


def _under_moved_root(path: Path, roots: Mapping[Path, Path]) -> Path:
    return _map_path(path, roots)


def _rename_simultaneously(
    moves: Mapping[Path, Path], completed: list[tuple[Path, Path]]
) -> None:
    active = {
        source: destination
        for source, destination in moves.items()
        if source != destination
    }
    if len(set(active.values())) != len(active):
        raise ActionError("reorganize.identity.collision", "duplicate destination")
    for destination in active.values():
        if destination.exists() and destination not in active:
            raise ActionError("reorganize.identity.collision", str(destination))
    temporary: dict[Path, Path] = {}
    for index, source in enumerate(sorted(active, key=lambda path: path.as_posix())):
        target = source.with_name(f".{source.name}.reorganize-{index}")
        if os.path.lexists(target):
            raise ActionError("reorganize.identity.residue", str(target))
        os.replace(source, target)
        completed.append((source, target))
        temporary[source] = target
    for source, destination in active.items():
        temporary[source].parent.mkdir(parents=False, exist_ok=True)
        os.replace(temporary[source], destination)
        completed.append((temporary[source], destination))


def _rollback_renames(completed: list[tuple[Path, Path]]) -> list[str]:
    errors: list[str] = []
    for source, destination in reversed(completed):
        if not os.path.lexists(destination):
            continue
        try:
            os.replace(destination, source)
        except OSError as error:
            errors.append(f"{destination} -> {source}: {error}")
    return errors


def _moved_residue(residue: Path, roots: Mapping[Path, Path]) -> Path:
    return _map_path(residue, roots)


def _physical(entry: EntryContext) -> EntryObservation:
    return next(
        item for item in observe_physical_entries(entry.log) if item.id == entry.id
    )


def _summary_by_id(log: LogContext) -> dict[str, SummaryEntryObservation]:
    return {item.id: item for item in observe_summary_entries(log)}


def _require_summary_identity(
    summary: SummaryEntryObservation | None,
    plan: _EntryUpdate,
) -> None:
    expected_documents = {
        plan.destination / document.name for document in plan.current.documents
    }
    if (
        summary is None
        or summary.date != plan.date
        or summary.slug != plan.slug
        or set(summary.documents) != expected_documents
        or plan.title is not None
        and summary.title != plan.title
    ):
        raise ActionError(
            "reorganize.markdown.incomplete",
            "edit the maintained summary links and identity before update-entry",
        )


def _require_headings(
    documents: tuple[Path, ...], date: str, title: str | None
) -> None:
    for document in documents:
        first = document.read_text(encoding="utf-8").splitlines()[0]
        expected = f"# {date}: {title}" if title is not None else f"# {date}:"
        if first != expected and not (title is None and first.startswith(expected)):
            raise ActionError(
                "reorganize.markdown.incomplete", f"entry heading is stale: {document}"
            )


def _document_moves(
    entries: tuple[EntryObservation, ...],
    roots: Mapping[Path, Path],
    ids: Mapping[str, str],
) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for entry in entries:
        for document in entry.documents:
            match = _DOCUMENT_RE.fullmatch(document.name)
            assert match is not None
            result[document] = roots[entry.root] / (
                ids[entry.id] + match.group("suffix") + ".md"
            )
    return result


def _require_reorder_summary(log: LogContext, plan: _ReorderPlan) -> None:
    summary = observe_summary_entries(log)
    if len(summary) != len(plan.desired):
        raise ActionError(
            "reorganize.markdown.incomplete", "summary entry count changed"
        )
    for index, old_id in enumerate(plan.desired):
        current = plan.entries[old_id]
        target = summary[index]
        expected_docs = {plan.documents[document] for document in current.documents}
        if (
            target.id != plan.ids[old_id]
            or target.date != current.date
            or target.slug != current.slug
            or set(target.documents) != expected_docs
            or target.documents[0].parent != plan.roots[current.root]
        ):
            raise ActionError(
                "reorganize.markdown.incomplete",
                "edit every maintained-summary identity before reorder",
            )


def _require_relocation_markdown(log: LogContext, root_name: str) -> None:
    text = log.summary.read_text(encoding="utf-8")
    expected = f"{root_name}/validation.md"
    if expected not in text:
        raise ActionError(
            "reorganize.markdown.incomplete", "update the summary validation link first"
        )
    if root_name != log.root.name and f"{log.root.name}/" in text:
        raise ActionError(
            "reorganize.markdown.incomplete", "summary contains a stale log-root link"
        )


def _require_relocation_target(log: LogContext, target: Path, summary: Path) -> None:
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ActionError(
            "reorganize.relocate.invalid", "destination parent is unavailable"
        )
    if os.path.lexists(target) or os.path.lexists(summary):
        raise ActionError("reorganize.relocate.conflict", str(target))
    if log.root.stat().st_dev != target.parent.stat().st_dev:
        raise ActionError("reorganize.relocate.cross_device", str(target))


def _require_no_entry_reference(log: LogContext, entry: EntryContext) -> None:
    needles = (entry.root.name, f"entry = {entry.id}")
    for item in observe_physical_entries(log):
        if item.id == entry.id:
            continue
        for document in item.documents:
            text = document.read_text(encoding="utf-8")
            if any(needle in text for needle in needles):
                raise ActionError("reorganize.remove.referenced", str(document))


def _remove_scaffold(entry: EntryContext, document: Path, runner: Path) -> None:
    document_text = document.read_text(encoding="utf-8")
    runner_target = os.readlink(runner)
    residue = begin_reorganization(entry.log.root)
    try:
        document.unlink()
        runner.unlink()
        entry.root.rmdir()
    except OSError as error:
        rollback: list[str] = []
        try:
            entry.root.mkdir(exist_ok=True)
            document.write_text(document_text, encoding="utf-8")
            if not runner.exists() and not runner.is_symlink():
                os.symlink(runner_target, runner)
        except OSError as restore_error:
            rollback.append(str(restore_error))
        if not rollback:
            finish_reorganization(residue)
        detail = f"; rollback failed: {'; '.join(rollback)}" if rollback else ""
        raise ActionError("reorganize.remove.failed", f"{error}{detail}") from error
    finish_reorganization(residue)


def _result(
    action: str, status: str, changed: bool, paths: tuple[Path, ...]
) -> ActionResult:
    return ActionResult(
        f"reorganize.{action}",
        status,
        f"reorganize.{status}",
        changed,
        paths=tuple(path.as_posix() for path in paths),
    )
