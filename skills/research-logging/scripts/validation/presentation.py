"""Public bounded evidence-record construction and presentation evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_log_data import load_data_file, resolve_input_token, verify_fingerprint

from .errors import MechanicalContractError
from .evidence import (
    EvidenceRecord,
    PresentedItem,
    evidence_record_from_fields,
    index_entry_presentations,
)
from .locator import evaluate_locator
from .mechanical_values import SelectionResult
from .transformation import compare_presentation, evaluate_transformation

MAX_ENTRY_DOCUMENTS = 256


class PresentationEvaluationError(MechanicalContractError):
    """One precise authoring-time presentation resolution failure."""


@dataclass(frozen=True)
class CandidateEvaluation:
    """One decoded record, presentation, and complete source selections."""

    record: EvidenceRecord
    presentation: PresentedItem
    selections: tuple[SelectionResult, ...]


def find_entry_presentation(
    entry_root: Path, log_root: Path, record_id: str
) -> PresentedItem:
    """Resolve one marker ID across the bounded Markdown owned by an entry."""

    matches = [
        item
        for item in index_entry_presentations_all(entry_root, log_root)
        if item.id == record_id
    ]
    if len(matches) != 1:
        raise PresentationEvaluationError(
            "evidence.presentation.unresolved",
            record_id,
            {"matches": len(matches)},
            "Evidence Presentation Authoring",
        )
    return matches[0]


def index_entry_presentations_all(
    entry_root: Path, log_root: Path
) -> tuple[PresentedItem, ...]:
    """Index all bounded entry-root Markdown presentation markers."""

    documents = sorted(
        path
        for path in entry_root.glob("*.md")
        if path.is_file() and not path.is_symlink()
    )
    if len(documents) > MAX_ENTRY_DOCUMENTS:
        raise PresentationEvaluationError(
            "evidence.presentation.too_large",
            str(entry_root),
            {"documents": len(documents), "limit": MAX_ENTRY_DOCUMENTS},
            "Evidence Presentation Authoring",
        )
    matches: list[PresentedItem] = []
    for document in documents:
        relative = document.relative_to(log_root).as_posix()
        matches.extend(
            index_entry_presentations(
                document.read_text(encoding="utf-8"), document=relative
            )
        )
    return tuple(matches)


def evaluate_candidate_record(
    *,
    entry_root: Path,
    log_root: Path,
    record_id: str,
    raw_sources: object,
    transformation: object,
) -> CandidateEvaluation:
    """Decode and completely compare one candidate evidence record."""

    presentation = find_entry_presentation(entry_root, log_root, record_id)
    record = evidence_record_from_fields(
        subject=f"evidence definition for {record_id!r}",
        log_root=log_root,
        entry_root=entry_root,
        fields={
            "document": presentation.document,
            "id": record_id,
            "kind": presentation.kind,
            "sources": raw_sources,
            "transformation": transformation,
        },
    )
    data = load_data_file(entry_root / "data.json", entry_root=entry_root)
    selections = []
    for source in record.sources:
        resolved = resolve_input_token(source.source, data)
        verify_fingerprint(resolved.resource)
        selections.append(evaluate_locator(Path(resolved.path), source.locator))
    result = evaluate_transformation(
        record.transformation, selections, presentation_kind=record.kind
    )
    compare_presentation(
        result, presented_kind=presentation.kind, presented=presentation.value
    )
    return CandidateEvaluation(record, presentation, tuple(selections))
