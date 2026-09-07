"""Public bounded evidence-record construction and presentation evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from research_log_data import load_data_file, resolve_input_token, verify_fingerprint

from .errors import MechanicalContractError
from .evidence import (
    MAX_PRESENTATION_BYTES,
    SECTION_CLASSIFIER_VERSION,
    EvidenceRecord,
    PresentedItem,
    evidence_record_from_fields,
    index_entry_presentations,
)
from .filesystem import BoundedFileReadError, bounded_file_bytes
from .json_codec import canonical_json
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
    definition: Mapping[str, object],
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
            "sources": definition["sources"],
            "transformation": definition["transformation"],
            **(
                {"reproduction_tolerance": definition["reproduction_tolerance"]}
                if "reproduction_tolerance" in definition
                else {}
            ),
        },
    )
    data = load_data_file(entry_root / "data.json", entry_root=entry_root)
    selections = []
    for source in record.sources:
        resolved = resolve_input_token(source.source, data)
        if resolved.member is None and resolved.resource.kind != "file":
            raise PresentationEvaluationError(
                "evidence.declaration.invalid",
                source.source,
                {"reason": "file_source_required"},
                "Evidence Presentation Authoring",
            )
        source_path = Path(resolved.path)
        if presentation.kind == "artifact":
            require_artifact_source_association(
                presentation, source_path=source_path, log_root=log_root
            )
        verify_fingerprint(resolved.resource)
        if presentation.kind != "artifact":
            assert source.locator is not None
            selections.append(evaluate_locator(source_path, source.locator))
    if presentation.kind == "artifact":
        return CandidateEvaluation(record, presentation, ())
    result = evaluate_transformation(
        record.transformation, selections, presentation_kind=record.kind
    )
    compare_presentation(
        result, presented_kind=presentation.kind, presented=presentation.value
    )
    return CandidateEvaluation(record, presentation, tuple(selections))


def require_artifact_source_association(
    presentation: PresentedItem, *, source_path: Path, log_root: Path
) -> None:
    """Require one artifact presentation to match its complete source."""

    if presentation.kind != "artifact":
        return
    if presentation.presentation_form == "inline-text":
        _require_inline_artifact_content(presentation, source_path=source_path)
        return
    if presentation.presentation_form not in {"image", "link"}:
        raise PresentationEvaluationError(
            "association.presentation.syntax_invalid",
            presentation.id,
            {"presentation_form": presentation.presentation_form},
            "Strict Presentation Parsing And Comparison",
        )
    marked = (log_root / presentation.value).resolve()
    declared = source_path.resolve()
    if marked != declared:
        raise PresentationEvaluationError(
            "association.artifact.source_mismatch",
            presentation.id,
            {"declared": declared.as_posix(), "marked": marked.as_posix()},
            "Artifact Evidence Association",
        )


def artifact_evidence_dependencies(
    record: EvidenceRecord,
    presentation: PresentedItem,
    inputs: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Return the complete dependency projection for one artifact record."""

    return (
        {"record": canonical_json(record.as_dict())},
        {
            "presentation": {
                "document": presentation.document,
                "format": presentation.presentation_format,
                "form": presentation.presentation_form,
                "id": presentation.id,
                "value": presentation.value,
            }
        },
        {
            "context": {
                "classification": presentation.section_classification,
                "classifier_version": SECTION_CLASSIFIER_VERSION,
                "under_results": presentation.under_results,
            }
        },
        {"inputs": [dict(item) for item in inputs]},
    )


def _require_inline_artifact_content(
    presentation: PresentedItem, *, source_path: Path
) -> None:
    if presentation.presentation_format != "diff":
        raise PresentationEvaluationError(
            "association.presentation.syntax_invalid",
            presentation.id,
            {"format": presentation.presentation_format},
            "Strict Presentation Parsing And Comparison",
        )
    try:
        raw = bounded_file_bytes(source_path, maximum_bytes=MAX_PRESENTATION_BYTES)
    except BoundedFileReadError as error:
        if error.reason == "byte_limit":
            raise PresentationEvaluationError(
                "association.resource.too_large",
                source_path.as_posix(),
                {"bytes": error.observed, "limit": error.limit},
                "Association Resource Bounds",
            ) from error
        if error.detail == "not_regular_file":
            raise PresentationEvaluationError(
                "association.artifact.inline_source_invalid",
                source_path.as_posix(),
                {"reason": "not_regular_file"},
                "Inline Artifact Association",
            ) from error
        raise PresentationEvaluationError(
            "association.artifact.inline_source_unavailable",
            source_path.as_posix(),
            {"reason": error.reason, "detail": error.detail},
            "Inline Artifact Association",
            outcome="unavailable",
        ) from error
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PresentationEvaluationError(
            "association.artifact.inline_source_invalid",
            source_path.as_posix(),
            {"reason": "invalid_utf8", "offset": error.start},
            "Inline Artifact Association",
        ) from error
    expected = _normalize_inline_artifact(source)
    presented = presentation.value
    if expected != presented:
        expected_bytes = expected.encode("utf-8")
        presented_bytes = presented.encode("utf-8")
        raise PresentationEvaluationError(
            "association.artifact.content_mismatch",
            presentation.id,
            {
                "normalized_source_bytes": len(expected_bytes),
                "normalized_source_sha256": hashlib.sha256(
                    expected_bytes
                ).hexdigest(),
                "presentation_bytes": len(presented_bytes),
                "presentation_sha256": hashlib.sha256(presented_bytes).hexdigest(),
            },
            "Inline Artifact Association",
        )


def _normalize_inline_artifact(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized[:-1] if normalized.endswith("\n") else normalized
