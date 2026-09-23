"""Markdown-owned evidence comparison and coupled presentation refresh."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from research_log_data import (
    DataContractError,
    DataFile,
    Fingerprint,
    InputResource,
    build_local_input,
    data_file_from_inputs,
    input_token_parts,
    load_data_file,
    normalize_input_location,
    observe_file_content,
    resolve_input_token,
)
from research_log_reservations import artifact_transaction, require_artifact_access
from validation.errors import MechanicalContractError
from validation.evidence import (
    MAX_PRESENTATION_BYTES,
    SUMMARY_LINE_RE,
    SUMMARY_REFERENCE_RE,
    EvidenceRecord,
    PresentedItem,
    evidence_file_from_records,
    evidence_record_from_fields,
    index_entry_presentations,
    index_summary_references,
    load_evidence_file,
    require_markdown_definition,
)
from validation.evidence_markdown import (
    MarkdownEvidence,
    materialize_statistic,
    read_markdown_evidence,
    source_token,
)
from validation.filesystem import bounded_file_bytes
from validation.locator import (
    SourceObservation,
    evaluate_observed_locator,
    observe_source,
    require_source_unchanged,
    selection_expectations,
)
from validation.operation_state import (
    begin_registry_transaction,
    finish_guarded_publication,
    operation_lock,
)
from validation.presentation import (
    index_entry_presentations_all,
    require_artifact_source_association,
)
from validation.provenance import require_origin_boundary
from validation.transformation import (
    TransformationResult,
    compare_presentation,
    evaluate_transformation,
)

from .authoring_transactions import (
    artifact_locations,
    data_change_paths,
    merge_data,
    require_unchanged,
    resource_authority,
)
from .context import (
    EntryContext,
    parse_entry_document_name,
    resolve_entry,
    resolve_project_root,
)
from .data_assertions import assignment, ensure_declaration, require_local_target
from .graph_state import authored_evidence_uses
from .materials import inspect_log_materials
from .model import ActionError, ActionResult, EvidenceSyncArguments
from .retention import require_unretained_paths
from .scaffold import observe_physical_entries
from .storage import (
    PublicationError,
    atomic_write_texts,
    entry_locks,
)


@dataclass(frozen=True)
class EvidenceEdit:
    """One fully evaluated replacement and normalized maintained record."""

    entry: EntryContext
    document: Path
    marker: MarkdownEvidence
    record: EvidenceRecord
    after: str
    data: DataFile
    artifact_observation: tuple[Path, str, Mapping[str, object]] | None = None


@dataclass(frozen=True)
class EvidenceBatch:
    """Prepared entry evidence edits and the state their publication must recheck."""

    entry: EntryContext
    edits: tuple[EvidenceEdit, ...]
    data: DataFile | None
    before_data: DataFile | None
    before_records: Mapping[str, EvidenceRecord]
    observations: Mapping[Path, SourceObservation]
    removed: set[str]
    arguments: EvidenceSyncArguments
    lock_entries: tuple[EntryContext, ...]


def compare_or_sync(
    entry: EntryContext, action: str, arguments: EvidenceSyncArguments
) -> ActionResult:
    """Preflight all selected records, then publish only changed owned files."""

    if action == "sync" and arguments.source is None:
        return _prepare_entry_set(entry, arguments)
    if arguments.record_ids or arguments.renames or arguments.deletions:
        raise ActionError(
            "evidence.sync.arguments.conflict",
            "--source cannot be combined with --id, --rename, or --delete",
        )
    if arguments.source is None and arguments.record_id is None:
        raise ActionError(
            "cli.arguments.invalid", "evidence compare requires --id or --source"
        )
    return _prepare_operation(entry, action, arguments)


def _entry_selection(
    arguments: EvidenceSyncArguments,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Normalize a bounded entry change set and reject incompatible identities."""

    renames = {assignment(raw, "--rename") for raw in arguments.renames}
    sources = [old for old, _ in renames]
    targets = [new for _, new in renames]
    if (
        len(set(sources)) != len(sources)
        or len(set(targets)) != len(targets)
        or set(sources) & set(targets)
    ):
        raise ActionError(
            "evidence.sync.selection.conflict",
            "renames must be one-to-one without chains or cycles",
        )
    deleted = set(arguments.deletions)
    selected = set(arguments.record_ids) | set(targets)
    if not selected and not deleted:
        raise ActionError(
            "cli.arguments.invalid",
            "evidence sync requires --id, --rename, or --delete",
        )
    if deleted & (selected | set(sources)) or set(sources) & set(arguments.record_ids):
        raise ActionError(
            "evidence.sync.selection.conflict",
            "an evidence ID cannot be selected for incompatible actions",
        )
    return tuple(sorted(selected)), tuple(sorted(renames)), tuple(sorted(deleted))


def _require_old_markdown_absent(entry: EntryContext, ids: set[str]) -> None:
    if not ids:
        return
    selected = frozenset((entry.id, record_id) for record_id in ids)
    references = index_summary_references(
        entry.log.summary.read_text(encoding="utf-8"), selected=selected
    )
    markers = [
        item
        for record_id in ids
        for item in index_entry_presentations_all(
            entry.root, entry.log.root, record_id=record_id
        )
    ]
    if markers or references:
        names = sorted(
            {item.id for item in markers}
            | {item.evidence_id for item in references if item.entry == entry.id}
        )
        raise ActionError(
            "evidence.sync.markdown_present",
            f"remove old entry markers and summary references first: {names}",
        )


def _prepare_entry_set(
    entry: EntryContext, arguments: EvidenceSyncArguments
) -> ActionResult:
    selected, renames, deleted = _entry_selection(arguments)
    removed = set(deleted) | {old for old, _ in renames}
    lock_entries = _lock_entries(entry, arguments)
    with (
        nullcontext()
        if arguments.dry_run
        else entry_locks(entry.log, (entry,), timeout_seconds=10)
    ):
        before_data = _load_data(entry)
        before_records = {record.id: record for record in _load_records(entry)}
    _require_old_markdown_absent(entry, removed)
    observations: dict[Path, SourceObservation] = {}
    owned = tuple(_owned_marker(entry, record_id) for record_id in selected)
    if not owned and (
        arguments.add_origins
        or arguments.add_origin_directories
        or arguments.add_from_entries
        or arguments.target_changes
    ):
        raise ActionError(
            "evidence.sync.declaration.unused",
            "declaration options require a selected final evidence ID",
        )
    data = (
        _candidate_data(
            entry,
            tuple(item[1] for item in owned),
            arguments,
            before_data,
            covered_ids=set(selected) | removed,
        )
        if owned
        else None
    )
    edits = (
        tuple(_evaluate_edit(entry, item, data, observations) for item in owned)
        if data is not None
        else ()
    )
    _check_renames(renames, before_records, edits)
    report = _entry_report(renames, deleted, edits)
    _recheck_sources(edits, observations)
    data_updates = {data.path: data.canonical_json()} if data is not None else {}
    changed = (
        _changed_updates(entry, edits, data_updates, removed)
        if arguments.dry_run
        else _publish_entry_set(
            EvidenceBatch(
                entry,
                edits,
                data,
                before_data,
                before_records,
                observations,
                removed,
                arguments,
                lock_entries,
            )
        )
    )
    return ActionResult(
        "evidence.sync",
        "dry-run" if arguments.dry_run else "changed" if changed else "unchanged",
        "evidence.synced",
        bool(changed),
        tuple(str(path) for path in sorted(changed)),
        records=report,
    )


def _check_renames(
    renames: tuple[tuple[str, str], ...],
    before_records: Mapping[str, EvidenceRecord],
    edits: tuple[EvidenceEdit, ...],
) -> None:
    by_id = {edit.record.id: edit for edit in edits}
    for old, new in renames:
        if old in before_records:
            if new in before_records:
                raise ActionError("evidence.sync.destination.conflict", new)
        elif before_records.get(new) != by_id[new].record or (
            by_id[new].marker.before != by_id[new].after
        ):
            raise ActionError(
                "evidence.sync.source.missing",
                f"{old}: missing source; {new} is not already synchronized",
            )


def _entry_report(
    renames: tuple[tuple[str, str], ...],
    deleted: tuple[str, ...],
    edits: tuple[EvidenceEdit, ...],
) -> tuple[dict[str, object], ...]:
    renamed_from = {new: old for old, new in renames}
    report_rows: list[dict[str, object]] = []
    for edit in edits:
        row: dict[str, object] = {
            "id": edit.record.id,
            "before": edit.marker.before,
            "after": edit.after,
        }
        if edit.record.id in renamed_from:
            row["renamed_from"] = renamed_from[edit.record.id]
        report_rows.append(row)
    report_rows.extend({"id": record_id, "deleted": True} for record_id in deleted)
    return tuple(report_rows)


def _publish_entry_set(batch: EvidenceBatch) -> dict[Path, str | None]:
    entry = batch.entry
    edits = batch.edits
    data = batch.data
    removed = batch.removed
    data_updates = {data.path: data.canonical_json()} if data is not None else {}
    with (
        entry_locks(entry.log, batch.lock_entries, timeout_seconds=10),
        operation_lock(entry.log.root, "summary.lock", timeout_seconds=10),
        artifact_transaction(resolve_project_root(entry.root)),
    ):
        _require_old_markdown_absent(entry, removed)
        current_records = {record.id: record for record in _load_records(entry)}
        for old in removed:
            require_unchanged(
                batch.before_records.get(old),
                current_records.get(old),
                f"{old} evidence record",
            )
        _check_selected_edits(
            edits, {entry.id: batch.before_records}, batch.observations
        )
        if data is not None:
            edits, fresh = _merge_entry_data(entry, edits, data, batch.before_data)
            _recheck_from_entries(batch, fresh)
            _recheck_target_ownership(batch, fresh)
            data_updates = {fresh.path: fresh.canonical_json()}
        changed = _changed_updates(entry, edits, data_updates, removed)
        _publish_edits(edits, changed, entries=(entry,))
    return changed


def _recheck_target_ownership(batch: EvidenceBatch, fresh: DataFile) -> None:
    covered = {edit.record.id for edit in batch.edits} | batch.removed
    before = batch.before_data.by_name if batch.before_data else {}
    for raw in batch.arguments.target_changes:
        name, _ = assignment(raw, "--change-target")
        prior = before.get(name)
        if (
            prior is not None
            and prior.canonical_target == fresh.by_name[name].canonical_target
        ):
            continue
        blockers = _target_blockers(batch.entry, name, covered)
        if blockers:
            _raise_target_shared(batch.entry, name, blockers)


def _recheck_from_entries(batch: EvidenceBatch, data: DataFile) -> None:
    for raw in batch.arguments.add_from_entries:
        name, source_id = assignment(raw, "--add-from-entry")
        source = _load_data(resolve_entry(batch.entry.log, source_id))
        expected = data.by_name[name]
        require_unchanged(
            resource_authority(replace(expected, reference_entry=None)),
            resource_authority(source.by_name.get(name) if source else None),
            f"{source_id}/{name} source declaration",
        )


def _merge_entry_data(
    entry: EntryContext,
    edits: tuple[EvidenceEdit, ...],
    data: DataFile,
    before_data: DataFile | None,
) -> tuple[tuple[EvidenceEdit, ...], DataFile]:
    names = {
        _source_name(source.source) for edit in edits for source in edit.record.sources
    }
    fresh = merge_data(before_data, data, _load_data(entry), names=names, entry=entry)
    assert fresh is not None
    for name in names:
        require_unchanged(
            resource_authority(data.by_name[name]),
            resource_authority(fresh.by_name[name]),
            f"{entry.id}/{name} source target",
        )
    require_artifact_access(
        resolve_project_root(entry.root),
        reads=artifact_locations(fresh, names),
        writes=data_change_paths(before_data, fresh),
    )
    return tuple(replace(edit, data=fresh) for edit in edits), fresh


def _lock_entries(
    entry: EntryContext, arguments: EvidenceSyncArguments
) -> tuple[EntryContext, ...]:
    if arguments.source is not None:
        return tuple(item[0] for item in _source_entries(entry, arguments.source))
    entries = {entry.id: entry}
    for raw in arguments.add_from_entries:
        _, source_id = assignment(raw, "--add-from-entry")
        entries[source_id] = resolve_entry(entry.log, source_id)
    return tuple(entries.values())


def _prepare_operation(
    entry: EntryContext, action: str, arguments: EvidenceSyncArguments
) -> ActionResult:
    targets = _lock_entries(entry, arguments)
    with (
        nullcontext()
        if action == "compare" or arguments.dry_run
        else entry_locks(entry.log, targets, timeout_seconds=10)
    ):
        before_data = {target.id: _load_data(target) for target in targets}
        before_records = {
            target.id: {record.id: record for record in _load_records(target)}
            for target in targets
            if arguments.source is not None or target.id == entry.id
        }
    observations: dict[Path, SourceObservation] = {}
    if arguments.source is not None:
        if (
            arguments.add_origins
            or arguments.add_origin_directories
            or arguments.add_from_entries
            or arguments.target_changes
        ):
            raise ActionError(
                "evidence.sync.arguments.conflict",
                "source-scoped sync refreshes existing definitions only",
            )
        edits = _source_edits(entry, arguments.source, observations)
        data_updates: dict[Path, str] = {}
    else:
        assert arguments.record_id is not None
        _load_records(entry)
        document, marker, presentation = _owned_marker(entry, arguments.record_id)
        data = _candidate_data(entry, (marker,), arguments, before_data[entry.id])
        edits = (
            _evaluate_edit(entry, (document, marker, presentation), data, observations),
        )
        data_updates = {data.path: data.canonical_json()}
    report: tuple[dict[str, object], ...] = tuple(
        {"id": edit.record.id, "before": edit.marker.before, "after": edit.after}
        for edit in edits
    )
    if action == "compare":
        _recheck_sources(edits, observations)
        return ActionResult(
            "evidence.compare", "unchanged", "evidence.compared", False, records=report
        )
    _recheck_sources(edits, observations)
    if arguments.dry_run:
        changed = _changed_updates(entry, edits, data_updates)
    else:
        # Expensive extraction and hashing above hold no entry/log locks. Final
        # reads rebind selected markers and merge into fresh documents/registries.
        with (
            entry_locks(entry.log, targets, timeout_seconds=10),
            operation_lock(entry.log.root, "summary.lock", timeout_seconds=10),
            artifact_transaction(resolve_project_root(entry.root)),
        ):
            _check_selected_edits(edits, before_records, observations)
            fresh_edits = []
            for edit in edits:
                names = {_source_name(source.source) for source in edit.record.sources}
                fresh = merge_data(
                    before_data[edit.entry.id],
                    edit.data,
                    _load_data(edit.entry),
                    names=names,
                    entry=edit.entry,
                )
                assert fresh is not None
                for name in names:
                    require_unchanged(
                        resource_authority(edit.data.by_name[name]),
                        resource_authority(fresh.by_name[name]),
                        f"{edit.entry.id}/{name} source target",
                    )
                fresh_edits.append(replace(edit, data=fresh))
                if arguments.source is None:
                    data_updates[fresh.path] = fresh.canonical_json()
            edits = tuple(fresh_edits)
            if arguments.source is not None:
                require_unchanged(
                    tuple(target.id for target in targets),
                    tuple(target.id for target in _lock_entries(entry, arguments)),
                    "source-scoped evidence entries",
                )
                require_unchanged(
                    tuple(sorted((edit.entry.id, edit.record.id) for edit in edits)),
                    _source_record_ids(entry, arguments.source),
                    "source-scoped evidence selection",
                )
            reads = tuple(
                path
                for edit in edits
                for path in artifact_locations(
                    edit.data,
                    {_source_name(source.source) for source in edit.record.sources},
                )
            )
            writes = tuple(
                path
                for edit in edits
                for path in data_change_paths(before_data[edit.entry.id], edit.data)
            )
            require_artifact_access(
                resolve_project_root(entry.root), reads=reads, writes=writes
            )
            changed = _changed_updates(entry, edits, data_updates)
            _publish_edits(edits, changed)
    return ActionResult(
        "evidence.sync",
        "dry-run" if arguments.dry_run else "changed" if changed else "unchanged",
        "evidence.synced",
        bool(changed),
        tuple(str(path) for path in sorted(changed)),
        records=report,
    )


def _changed_updates(
    entry: EntryContext,
    edits: tuple[EvidenceEdit, ...],
    data_updates: Mapping[Path, str],
    removed: set[str] | None = None,
) -> dict[Path, str | None]:
    updates = _publication_updates(entry, edits, removed=removed)
    updates.update(data_updates)
    return {
        path: value
        for path, value in updates.items()
        if (path.read_text(encoding="utf-8") if path.exists() else None) != value
    }


def _check_selected_edits(
    edits: tuple[EvidenceEdit, ...],
    before_records: Mapping[str, Mapping[str, EvidenceRecord]],
    observations: Mapping[Path, SourceObservation],
) -> None:
    for observation in observations.values():
        require_source_unchanged(observation)
    for edit in edits:
        document, marker, _ = _owned_marker(edit.entry, edit.record.id)
        require_unchanged(edit.document, document, f"{edit.record.id} document")
        require_unchanged(
            replace(edit.marker, start=0, end=0, before=""),
            replace(marker, start=0, end=0, before=""),
            f"{edit.record.id} Markdown evidence",
        )
        if marker.before != edit.after:
            require_unchanged(
                edit.marker.before,
                marker.before,
                f"{edit.record.id} presented evidence",
            )
        current = {record.id: record for record in _load_records(edit.entry)}
        if current.get(edit.record.id) != edit.record:
            require_unchanged(
                before_records[edit.entry.id].get(edit.record.id),
                current.get(edit.record.id),
                f"{edit.record.id} evidence record",
            )
        if edit.artifact_observation is not None:
            path, _, identity = edit.artifact_observation
            stat = path.stat()
            fresh_identity = {
                "kind": "file",
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "ctime_ns": stat.st_ctime_ns,
            }
            require_unchanged(identity, fresh_identity, f"{edit.record.id} artifact")


def _publish_edits(
    edits: tuple[EvidenceEdit, ...],
    updates: Mapping[Path, str | None],
    *,
    entries: tuple[EntryContext, ...] = (),
) -> None:
    if not updates:
        return
    selected_entries = {edit.entry.id: edit.entry for edit in edits}
    selected_entries.update({entry.id: entry for entry in entries})
    residues = []
    try:
        for target in sorted(selected_entries.values(), key=lambda item: item.id):
            residues.append(begin_registry_transaction(target.log.root, target.id))
    except OSError:
        for residue in residues:
            finish_guarded_publication(residue)
        raise
    try:
        atomic_write_texts(updates)
    except PublicationError as error:
        if error.rollback_complete:
            for residue in residues:
                finish_guarded_publication(residue)
        raise
    for residue in residues:
        finish_guarded_publication(residue)


def _owned_marker(
    entry: EntryContext, record_id: str
) -> tuple[Path, MarkdownEvidence, PresentedItem]:
    matches = []
    for document in sorted(entry.root.iterdir()):
        identity = parse_entry_document_name(document.name)
        if (
            identity is None
            or identity.id != entry.id
            or document.is_symlink()
            or not document.is_file()
        ):
            continue
        text = document.read_text(encoding="utf-8")
        presentations = index_entry_presentations(
            text,
            document=document.relative_to(entry.log.root).as_posix(),
            record_id=record_id,
        )
        if not presentations:
            continue
        marker = read_markdown_evidence(text, record_id)
        found = [item for item in presentations if item.id == record_id]
        if len(found) != 1 or not found[0].context_valid:
            raise ActionError(
                "evidence.marker.context",
                f"{document}: {record_id} requires an experimental section",
            )
        if marker.kind != found[0].kind:
            raise ActionError("evidence.marker.kind", record_id)
        matches.append((document, marker, found[0]))
    if len(matches) != 1:
        raise ActionError(
            "evidence.marker.unresolved",
            f"{record_id}: expected one marker, found {len(matches)}",
        )
    return matches[0]


def _load_data(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return load_data_file(path, entry_root=entry.root)
    except DataContractError as error:
        raise ActionError(
            "evidence.sync.registry.invalid", f"{path}: direct Repair required: {error}"
        ) from error


def _load_records(entry: EntryContext) -> tuple[EvidenceRecord, ...]:
    path = entry.root / "evidence.json"
    if not path.exists() and not path.is_symlink():
        return ()
    try:
        return load_evidence_file(
            path, log_root=entry.log.root, entry_root=entry.root
        ).records
    except MechanicalContractError as error:
        raise ActionError(
            "evidence.sync.registry.invalid", f"{path}: direct Repair required: {error}"
        ) from error


def _candidate_data(
    entry: EntryContext,
    markers: tuple[MarkdownEvidence, ...],
    arguments: EvidenceSyncArguments,
    current: DataFile | None,
    *,
    covered_ids: set[str] | None = None,
) -> DataFile:
    items = dict(current.by_name) if current else {}
    used_names = {
        _source_name(source["source"])
        for marker in markers
        for source in marker.sources
    }
    asserted = set()
    for values, kind, flag in (
        (arguments.add_origins, "file", "--add-origin"),
        (arguments.add_origin_directories, "directory", "--add-origin-directory"),
    ):
        for raw in values:
            name, target = assignment(raw, flag)
            location = normalize_input_location(target, entry_root=entry.root)
            require_local_target(entry, name, location, kind=kind, origin=True)
            candidate = build_local_input(
                name, kind, location, entry_root=entry.root, origin=True
            )
            ensure_declaration(entry, items, candidate, flag, "evidence.sync")
            asserted.add(name)
    for raw in arguments.add_from_entries:
        name, source_id = assignment(raw, "--add-from-entry")
        source_entry = resolve_entry(entry.log, source_id)
        source_data = _load_data(source_entry)
        source = source_data.by_name.get(name) if source_data else None
        if (
            source_id == entry.id
            or source is None
            or source.origin
            or source.reference_entry
        ):
            raise ActionError(
                "data.reference.source_invalid",
                f"{source_id} must directly declare generated data named {name}",
            )
        ensure_declaration(
            entry,
            items,
            replace(source, reference_entry=source_id),
            "--add-from-entry",
            "evidence.sync",
        )
        asserted.add(name)
    if asserted - used_names:
        raise ActionError(
            "evidence.sync.declaration.unused",
            f"unused additions: {sorted(asserted - used_names)}",
        )
    for name in used_names - set(items):
        raise ActionError(
            "data.input.missing",
            f"{name}: add an origin with --add-origin NAME=PATH or "
            "--add-origin-directory NAME=PATH; for generated data author "
            "and run log command sync on its producer",
        )
    _change_targets(
        entry,
        items,
        covered_ids if covered_ids is not None else {marker.id for marker in markers},
        used_names,
        arguments.target_changes,
    )
    built = data_file_from_inputs(
        entry.root / "data.json", entry_root=entry.root, inputs=tuple(items.values())
    )
    changed_origins = {
        name
        for name in asserted
        | {assignment(raw, "--change-target")[0] for raw in arguments.target_changes}
        if items[name].origin
        and (current is None or current.by_name.get(name) != items[name])
    }
    if changed_origins:
        materials = inspect_log_materials(entry.log, data_overrides={entry.root: built})
        for name in sorted(changed_origins):
            require_origin_boundary(
                items[name].canonical_target,
                items[name],
                materials.invocations,
                confirmed_record=materials.confirmed,
            )
    return built


def _change_targets(
    entry: EntryContext,
    items: dict[str, InputResource],
    record_ids: set[str],
    used_names: set[str],
    values: tuple[str, ...],
) -> None:
    changes: dict[str, str] = {}
    for raw in values:
        name, target = assignment(raw, "--change-target")
        if name in changes and changes[name] != target:
            raise ActionError(
                "evidence.sync.target.conflict",
                f"{name}: conflicting --change-target values",
            )
        changes[name] = target
    for name, target in sorted(changes.items()):
        existing = items.get(name)
        if existing is None or name not in used_names:
            raise ActionError(
                "evidence.sync.target.not_owned",
                f"selected evidence does not use {name}; use log data update",
            )
        if existing.reference_entry or existing.kind == "git-repository":
            raise ActionError(
                "evidence.sync.target.shared", f"{name}: use log data update"
            )
        location = normalize_input_location(target, entry_root=entry.root)
        require_local_target(
            entry, name, location, kind=existing.kind, origin=existing.origin
        )
        base = build_local_input(
            name, existing.kind, location, entry_root=entry.root, origin=existing.origin
        )
        candidate = replace(
            base, identity=existing.identity, comparison=existing.comparison
        )
        if candidate == existing:
            continue
        blockers = _target_blockers(entry, name, record_ids)
        if blockers:
            _raise_target_shared(entry, name, blockers)
        items[name] = candidate


def _raise_target_shared(
    entry: EntryContext, name: str, blockers: tuple[dict[str, object], ...]
) -> None:
    owners = set()
    for item in blockers:
        identity = item.get("evidence", item.get("command", "reference"))
        owners.add(f"{item.get('entry')}/{identity}")
    raise ActionError(
        "evidence.sync.target.shared",
        f"{name}: unselected consumers {sorted(owners)}; use log data update",
        records=blockers,
        diagnostic_log=entry.log.root,
    )


def _target_blockers(
    entry: EntryContext, name: str, record_ids: set[str]
) -> tuple[dict[str, object], ...]:
    materials = inspect_log_materials(entry.log)
    blockers: list[dict[str, object]] = (
        [{"commands": True, "entry": entry.id}]
        if name in materials.input_names.get(entry.root, ())
        else []
    )
    for observed in observe_physical_entries(entry.log):
        target = EntryContext(entry.log, observed.id, observed.root)
        data = _load_data(target)
        resource = data.by_name.get(name) if data else None
        if resource and resource.reference_entry == entry.id:
            blockers.append({"entry": target.id, "reference": name})
        if target.id != entry.id:
            continue
        path = target.root / "evidence.json"
        if path.exists():
            records = load_evidence_file(
                path, log_root=entry.log.root, entry_root=entry.root
            ).records
            for record in records:
                if record.id not in record_ids and any(
                    _source_name(source.source) == name for source in record.sources
                ):
                    blockers.append({"entry": entry.id, "evidence": record.id})
    blockers.extend(
        authored_evidence_uses(entry, name, excluded_ids=frozenset(record_ids))
    )
    return tuple(blockers)


def _evaluate_edit(
    entry: EntryContext,
    owned: tuple[Path, MarkdownEvidence, PresentedItem],
    data: DataFile,
    observations: dict[Path, SourceObservation],
) -> EvidenceEdit:
    document, marker, presentation = owned
    sources = []
    selections = []
    artifact = None
    for source in marker.sources:
        resolved = resolve_input_token(source["source"], data)
        if resolved.member is None and resolved.resource.kind != "file":
            raise ActionError(
                "evidence.source.file_required",
                f"{source['source']}: select a declared directory member",
            )
        path = Path(resolved.path).resolve()
        require_artifact_access(resolve_project_root(entry.root), reads=(path,))
        require_unretained_paths(entry, (path.as_posix(),))
        if marker.kind == "artifact":
            if presentation.presentation_form in {"image", "link"}:
                require_artifact_source_association(
                    presentation, source_path=path, log_root=entry.log.root
                )
            digest, identity = observe_file_content(path)
            artifact = (path, digest, identity)
            sources.append(source)
        else:
            if path not in observations:
                observations[path] = observe_source(path)
            selection = evaluate_observed_locator(observations[path], source["locator"])
            sources.append(
                {
                    "source": source["source"],
                    "locator": {
                        **source["locator"],
                        "expect": selection_expectations(selection),
                    },
                }
            )
            selections.append(selection)
    fields: dict[str, Any] = {
        "id": marker.id,
        "document": document.relative_to(entry.log.root).as_posix(),
        "kind": marker.kind,
        "sources": sources,
        "transformation": materialize_statistic(
            marker.statistic, [len(selection.items) for selection in selections]
        )
        if marker.statistic
        else marker.transformation,
    }
    if marker.tolerance:
        fields["reproduction_tolerance"] = {"absolute": marker.tolerance}
    if artifact and presentation.presentation_form in {"image", "link"}:
        fields["artifact_fingerprint"] = Fingerprint("sha256", artifact[1]).as_dict()
    record = evidence_record_from_fields(
        subject=marker.id, log_root=entry.log.root, entry_root=entry.root, fields=fields
    )
    if artifact:
        after = (
            marker.before
            if presentation.presentation_form in {"image", "link"}
            else _artifact_body(artifact[0])
        )
        result = None
    else:
        result = evaluate_transformation(
            record.transformation, selections, presentation_kind=marker.kind
        )
        after = _render(marker, result)
    _validate_rendering(entry, owned, record, after, result)
    return EvidenceEdit(entry, document, marker, record, after, data, artifact)


def _validate_rendering(
    entry: EntryContext,
    owned: tuple[Path, MarkdownEvidence, PresentedItem],
    record: EvidenceRecord,
    after: str,
    result: TransformationResult | None,
) -> None:
    document, marker, _ = owned
    before = document.read_text(encoding="utf-8")
    candidate = before[: marker.start] + after + before[marker.end :]
    items = index_entry_presentations(
        candidate, document=record.document, record_id=record.id
    )
    item = next((item for item in items if item.id == record.id), None)
    if item is None:
        raise ActionError(
            "evidence.render.invalid",
            f"{record.id}: rendering destroyed its presentation marker",
        )
    require_markdown_definition(record, item)
    if result is not None:
        compare_presentation(result, presented_kind=item.kind, presented=item.value)
    elif item.presentation_form == "inline-text":
        expected = after[:-1] if after.endswith("\n") else after
        if item.value != expected:
            raise ActionError(
                "evidence.render.invalid",
                f"{record.id}: source text conflicts with the artifact fence delimiter",
            )


def _artifact_body(path: Path) -> str:
    raw = bounded_file_bytes(path, maximum_bytes=MAX_PRESENTATION_BYTES)
    normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized if not normalized or normalized.endswith("\n") else normalized + "\n"
    )


def _render(marker: MarkdownEvidence, result: TransformationResult) -> str:
    if marker.kind == "statistic":
        if marker.before[1:-1] in result.accepted_spellings:
            return marker.before
        spelling = (
            result.accepted_spellings[1]
            if " +/- " in marker.before and len(result.accepted_spellings) > 1
            else result.accepted_spellings[0]
        )
        return "`" + spelling + "`"
    if marker.kind == "output":
        return (
            result.accepted_spellings[0] + "\n" if result.accepted_spellings[0] else ""
        )
    try:
        compare_presentation(result, presented_kind="table", presented=marker.before)
        return marker.before
    except MechanicalContractError as error:
        if error.code != "transformation.presentation.mismatch":
            raise
    indentation = marker.header[: len(marker.header) - len(marker.header.lstrip(" \t"))]
    return marker.header + "".join(
        indentation + "| " + " | ".join(row) + " |\n" for row in result.rows
    )


def _source_entries(
    entry: EntryContext, raw_source: str
) -> tuple[tuple[EntryContext, DataFile], ...]:
    token = source_token(raw_source)
    parts = input_token_parts(token)
    if parts is None or parts[1] is not None or parts[2] is not None:
        raise ActionError(
            "evidence.source.invalid",
            "--source selects a direct generated declaration name",
        )
    name = parts[0]
    data = _load_data(entry)
    resource = data.by_name.get(name) if data else None
    if resource is None or resource.origin or resource.reference_entry:
        raise ActionError(
            "evidence.source.not_owned",
            f"{entry.id}: {name} must be directly declared generated data",
        )
    selected = []
    for observed in observe_physical_entries(entry.log):
        candidate_entry = EntryContext(entry.log, observed.id, observed.root)
        candidate_data = (
            data if candidate_entry.id == entry.id else _load_data(candidate_entry)
        )
        candidate = candidate_data.by_name.get(name) if candidate_data else None
        if candidate and (
            candidate_entry.id == entry.id or candidate.reference_entry == entry.id
        ):
            if (
                candidate.canonical_target != resource.canonical_target
                or candidate.kind != resource.kind
            ):
                raise ActionError(
                    "data.reference.inconsistent",
                    f"{candidate_entry.id}: {name}: use log data update to repair",
                )
            assert candidate_data is not None
            selected.append((candidate_entry, candidate_data))
    return tuple(selected)


def _source_edits(
    entry: EntryContext, raw_source: str, observations: dict[Path, SourceObservation]
) -> tuple[EvidenceEdit, ...]:
    name = _source_name(raw_source)
    edits = []
    for target, data in _source_entries(entry, raw_source):
        path = target.root / "evidence.json"
        if not path.exists():
            continue
        records = load_evidence_file(
            path, log_root=entry.log.root, entry_root=target.root
        ).records
        for record in records:
            if any(_source_name(source.source) == name for source in record.sources):
                document, marker, presentation = _owned_marker(target, record.id)
                if not any(
                    _source_name(source["source"]) == name for source in marker.sources
                ):
                    raise ActionError(
                        "evidence.source.definition_changed",
                        f"{target.id}/{record.id}: sync its ID-scoped definition first",
                    )
                edits.append(
                    _evaluate_edit(
                        target, (document, marker, presentation), data, observations
                    )
                )
    return tuple(edits)


def _source_record_ids(
    entry: EntryContext, raw_source: str
) -> tuple[tuple[str, str], ...]:
    name = _source_name(raw_source)
    return tuple(
        sorted(
            (target.id, record.id)
            for target, _ in _source_entries(entry, raw_source)
            for record in _load_records(target)
            if any(_source_name(source.source) == name for source in record.sources)
        )
    )


def _publication_updates(
    entry: EntryContext,
    edits: tuple[EvidenceEdit, ...],
    *,
    removed: set[str] | None = None,
) -> dict[Path, str | None]:
    updates: dict[Path, str | None] = {}
    entries = {edit.entry.id: edit.entry for edit in edits}
    if removed:
        entries[entry.id] = entry
    for target in entries.values():
        path = target.root / "evidence.json"
        records = {record.id: record for record in _load_records(target)}
        if target.id == entry.id:
            for record_id in removed or ():
                records.pop(record_id, None)
        records.update(
            {
                edit.record.id: edit.record
                for edit in edits
                if edit.entry.id == target.id
            }
        )
        updates[path] = (
            evidence_file_from_records(
                path,
                log_root=entry.log.root,
                entry_root=target.root,
                records=tuple(records.values()),
            ).canonical_json()
            if records
            else None
        )
    for document in {edit.document for edit in edits}:
        text = document.read_text(encoding="utf-8")
        rebound = [
            replace(edit, marker=read_markdown_evidence(text, edit.record.id))
            for edit in edits
            if edit.document == document
        ]
        for edit in sorted(
            rebound,
            key=lambda item: item.marker.start,
            reverse=True,
        ):
            text = text[: edit.marker.start] + edit.after + text[edit.marker.end :]
        updates[document] = text
    summary = _refresh_summary(entry, edits)
    if summary is not None:
        updates[entry.log.summary] = summary
    return updates


def _refresh_summary(
    entry: EntryContext, edits: tuple[EvidenceEdit, ...]
) -> str | None:
    original = entry.log.summary.read_text(encoding="utf-8")
    selected = {(edit.entry.id, edit.record.id): edit for edit in edits}
    index_summary_references(original, selected=frozenset(selected))

    def replace_reference(match: Any) -> str:
        reference = SUMMARY_REFERENCE_RE.fullmatch(match["reference"])
        if reference is None:
            return match[0]
        edit = selected.get((reference["entry"], reference["id"]))
        if edit is None:
            return match[0]
        if edit.marker.kind == "statistic" and reference["row"] is None:
            value = edit.after
        elif edit.marker.kind == "table" and reference["row"] is not None:
            from validation.transformation import parse_markdown_table

            headings, rows = parse_markdown_table(edit.after)
            row, column = int(reference["row"]), int(reference["column"])
            if row > len(rows) or column > len(headings):
                raise ActionError("summary.reference.cell_missing", match["reference"])
            value = "`" + rows[row - 1][column - 1] + "`"
        else:
            raise ActionError("summary.reference.kind", match["reference"])
        return value + match["reference"]

    refreshed = SUMMARY_LINE_RE.sub(replace_reference, original)
    return refreshed if refreshed != original else None


def _recheck_sources(
    edits: tuple[EvidenceEdit, ...], observations: Mapping[Path, SourceObservation]
) -> None:
    for observation in observations.values():
        require_source_unchanged(observation)
    for edit in edits:
        refreshed = data_file_from_inputs(
            edit.data.path, entry_root=edit.entry.root, inputs=edit.data.inputs
        )
        for source in edit.record.sources:
            original = resolve_input_token(source.source, edit.data).path
            if resolve_input_token(source.source, refreshed).path != original:
                raise ActionError("evidence.source.target_changed", edit.record.id)
        if edit.artifact_observation is not None:
            path, digest, identity = edit.artifact_observation
            if observe_file_content(path) != (digest, identity):
                raise ActionError("evidence.artifact.source_changed", edit.record.id)


def _source_name(raw_source: str) -> str:
    token = source_token(raw_source)
    parts = input_token_parts(token)
    if parts is None:
        raise ActionError("evidence.source.invalid", token)
    return parts[0]
