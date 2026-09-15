"""Entry-scoped evidence authoring actions."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Mapping

from research_log_data import (
    load_data_file,
    resolve_input_token,
)
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    evidence_file_from_records,
    index_summary_references,
    load_evidence_file,
    require_markdown_definition,
)
from validation.presentation import (
    evaluate_candidate_record,
    index_entry_presentations_all,
    require_artifact_baseline_form,
    require_artifact_fingerprint,
)

from .context import EntryContext
from .graph_state import declaration_uses, publish_updates, token_name
from .model import ActionError, ActionResult
from .storage import entry_lock_under_log, log_lock


def list_records(entry: EntryContext) -> ActionResult:
    """Return bounded semantic evidence records without registry details."""

    current = load_current(entry)
    records = () if current is None else current.records
    return ActionResult(
        "evidence.list",
        "unchanged",
        "evidence.listed",
        False,
        records=tuple(
            {
                "document": record.document,
                "id": record.id,
                "kind": record.kind,
                "sources": [
                    {
                        "source": source.source,
                        "locator": {
                            key: value
                            for key, value in source.locator.items()
                            if key != "expect"
                        }
                        if source.locator
                        else None,
                    }
                    for source in record.sources
                ],
                "transformation": record.transformation,
                "reproduction_tolerance": record.reproduction_tolerance.absolute
                if record.reproduction_tolerance
                else None,
            }
            for record in records
        ),
    )


def rename(
    entry: EntryContext, old_id: str, new_id: str, *, dry_run: bool
) -> ActionResult:
    """Rename one registry ID after the agent completes all Markdown edits."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = _required(entry)
        existing = {record.id: record for record in current.records}
        if old_id not in existing:
            raise ActionError("evidence.record.missing", old_id)
        if new_id in existing:
            raise ActionError("evidence.record.conflict", new_id)
        marker_ids = {
            item.id
            for selected_id in (old_id, new_id)
            for item in index_entry_presentations_all(
                entry.root, entry.log.root, record_id=selected_id
            )
        }
        summary_ids = {
            item.evidence_id
            for item in index_summary_references(
                entry.log.summary.read_text(encoding="utf-8"),
                selected=frozenset(((entry.id, old_id), (entry.id, new_id))),
            )
            if item.entry == entry.id
        }
        if old_id in marker_ids or old_id in summary_ids or new_id not in marker_ids:
            raise ActionError(
                "evidence.rename.markdown_incomplete",
                "rename the marker and every summary reference before the registry",
            )
        old = existing.pop(old_id)
        evaluated = evaluate_candidate_record(
            entry_root=entry.root,
            log_root=entry.log.root,
            record_id=new_id,
            definition=_candidate_definition(
                sources=[source.as_dict() for source in old.sources],
                transformation=old.transformation,
                tolerance=(
                    None
                    if old.reproduction_tolerance is None
                    else old.reproduction_tolerance.absolute
                ),
            ),
            capture_artifact_fingerprint=False,
        )
        if (
            evaluated.presentation.document != old.document
            or evaluated.presentation.kind != old.kind
        ):
            raise ActionError("evidence.rename.presentation_changed", new_id)
        if old.kind == "artifact":
            require_artifact_baseline_form(old, evaluated.presentation)
            if evaluated.presentation.presentation_form in {"image", "link"}:
                require_artifact_fingerprint(
                    old,
                    source_path=_artifact_source_path(entry, old),
                )
        existing[new_id] = EvidenceRecord(
            new_id,
            old.document,
            old.kind,
            old.sources,
            old.transformation,
            old.reproduction_tolerance,
            old.artifact_fingerprint,
            old.artifact_fingerprint_present,
        )
        require_markdown_definition(existing[new_id], evaluated.presentation)
        built = _build(entry, tuple(existing.values()))
        if not dry_run:
            publish_updates((entry,), {built.path: built.canonical_json()})
        return _result("rename", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, record_id: str, *, dry_run: bool) -> ActionResult:
    """Remove one record only after its marker and summary references are absent."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = load_current(entry)
        if current is None or record_id not in {item.id for item in current.records}:
            return _result("delete", "absent", False)
        marker_ids = {
            item.id
            for item in index_entry_presentations_all(
                entry.root, entry.log.root, record_id=record_id
            )
        }
        references = index_summary_references(
            entry.log.summary.read_text(encoding="utf-8"),
            selected=frozenset(((entry.id, record_id),)),
        )
        if record_id in marker_ids or any(
            item.entry == entry.id and item.evidence_id == record_id
            for item in references
        ):
            raise ActionError(
                "evidence.delete.markdown_present",
                "remove the marker and summary references before the registry record",
            )
        remaining = tuple(item for item in current.records if item.id != record_id)
        deleted = next(item for item in current.records if item.id == record_id)
        names = {token_name(source.source) for source in deleted.sources}
        unused: tuple[dict[str, object], ...] = tuple(
            {"unused_data": name}
            for name in sorted(name for name in names if name is not None)
            if not any(
                not (use.get("entry") == entry.id and use.get("evidence") == record_id)
                for use in declaration_uses(entry, name)
            )
        )
        text = _build(entry, remaining).canonical_json() if remaining else None
        if not dry_run:
            publish_updates((entry,), {current.path: text})
        return ActionResult(
            "evidence.delete",
            "dry-run" if dry_run else "changed",
            "evidence.deleted",
            True,
            records=unused,
        )


def _candidate_definition(
    *, sources: object, transformation: object, tolerance: str | None
) -> Mapping[str, object]:
    definition: dict[str, object] = {
        "sources": sources,
        "transformation": transformation,
    }
    if tolerance is not None:
        definition["reproduction_tolerance"] = {"absolute": tolerance}
    return definition


def load_current(entry: EntryContext) -> EvidenceFile | None:
    """Load the complete current evidence registry when present."""

    path = entry.root / "evidence.json"
    return (
        load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _required(entry: EntryContext) -> EvidenceFile:
    current = load_current(entry)
    if current is None:
        raise ActionError("evidence.record.missing", "evidence registry is absent")
    return current


def _build(entry: EntryContext, records: tuple[EvidenceRecord, ...]) -> EvidenceFile:
    return evidence_file_from_records(
        entry.root / "evidence.json",
        log_root=entry.log.root,
        entry_root=entry.root,
        records=records,
    )


def _artifact_source_path(entry: EntryContext, record: EvidenceRecord) -> Path:
    data = load_data_file(entry.root / "data.json", entry_root=entry.root)
    return Path(resolve_input_token(record.sources[0].source, data).path).resolve()


def _result(
    action: str,
    status: str,
    changed: bool,
    *,
    records: tuple[dict[str, object], ...] | None = None,
) -> ActionResult:
    return ActionResult(
        f"evidence.{action}",
        status,
        f"evidence.{status}",
        changed,
        records=records,
    )
