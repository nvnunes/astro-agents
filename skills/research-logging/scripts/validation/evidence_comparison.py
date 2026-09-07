"""Shared evidence-scoped reproduction comparison contract."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Mapping, NoReturn, Sequence, cast

from research_log_data import DataFile, InputResource, resolve_input_token

from .errors import MechanicalContractError
from .evidence import EvidenceFile, EvidenceRecord
from .json_codec import canonical_json
from .locator import evaluate_locator
from .mechanical_values import CanonicalValue, SelectionItem, SelectionResult
from .transformation import evaluate_transformation

EVIDENCE_COMPARISON_RESULT_CONTRACT = "research-log-evidence-scoped-comparison-result/1"


class EvidenceComparisonError(MechanicalContractError):
    """One invalid or unevaluable evidence-scoped comparison contract."""


@dataclass(frozen=True)
class EvidenceComparisonDefinition:
    """The complete evidence comparison definition for one artifact."""

    resource: InputResource
    data: DataFile
    records: tuple[EvidenceRecord, ...]
    identity: str


@dataclass(frozen=True)
class EvidenceComparisonOutcome:
    """Every record-level result for one evidence-scoped artifact comparison."""

    matched: bool
    records: tuple[Mapping[str, object], ...]


def evidence_comparison_definitions(
    data: DataFile, evidence: EvidenceFile | None
) -> tuple[EvidenceComparisonDefinition, ...]:
    """Resolve and validate every evidence-scoped artifact in one entry."""

    selected = tuple(item for item in data.inputs if item.comparison is not None)
    if not selected:
        validate_reproduction_tolerances(data, evidence)
        return ()
    if evidence is None:
        _fail(
            "reproduction.comparison.evidence_missing",
            str(data.path),
            {"artifacts": [item.name for item in selected]},
        )
    assert evidence is not None
    validate_reproduction_tolerances(data, evidence)
    definitions = tuple(
        _definition(resource, data=data, evidence=evidence) for resource in selected
    )
    return definitions


def validate_reproduction_tolerances(
    data: DataFile, evidence: EvidenceFile | None
) -> None:
    """Require every tolerance to belong to evidence-scoped artifact use."""

    if evidence is None:
        return
    selected = {
        item.canonical_target for item in data.inputs if item.comparison is not None
    }
    for record in evidence.records:
        if record.reproduction_tolerance is None:
            continue
        applicable = {
            resolve_input_token(source.source, data).resource.canonical_target
            for source in record.sources
        } & selected
        if not applicable:
            _fail(
                "reproduction.comparison.tolerance_incompatible",
                record.id,
                {"reason": "evidence_scoped_artifact_required"},
            )


def evidence_comparison_definition(
    resource: InputResource, *, data: DataFile, evidence: EvidenceFile | None
) -> EvidenceComparisonDefinition:
    """Resolve and validate one evidence-scoped artifact declaration."""

    if resource.comparison is None:
        raise ValueError("resource does not select evidence-scoped comparison")
    if evidence is None:
        _fail(
            "reproduction.comparison.evidence_missing",
            resource.name,
            {"target": resource.location},
        )
    return _definition(resource, data=data, evidence=evidence)


def evidence_comparison_identity(
    resource: InputResource, *, data: DataFile, evidence: EvidenceFile | None
) -> str | None:
    """Return the current definition identity without reading artifact values."""

    if resource.comparison is None:
        return None
    if evidence is None:
        return None
    return _definition(resource, data=data, evidence=evidence).identity


def compare_evidence_scoped(
    definition: EvidenceComparisonDefinition,
    *,
    regenerated: Path,
) -> EvidenceComparisonOutcome:
    """Compare retained and regenerated selections under one validated definition."""

    expected = _evaluate_records(definition, replacement=None)
    observed = _evaluate_records(definition, replacement=regenerated)
    records: list[Mapping[str, object]] = []
    matched = True
    for record, left, right in zip(definition.records, expected, observed):
        tolerance = record.reproduction_tolerance
        equal = _selections_equal(
            left,
            right,
            tolerance=(None if tolerance is None else _fraction(tolerance.absolute)),
        )
        matched = matched and equal
        records.append(
            {
                "definition": _record_identity(record),
                "expected": [_selection_projection(item) for item in left],
                "id": record.id,
                "matched": equal,
                "regenerated": [_selection_projection(item) for item in right],
                "tolerance": (None if tolerance is None else tolerance.as_dict()),
            }
        )
    return EvidenceComparisonOutcome(matched, tuple(records))


def validate_tolerant_selection(
    record: EvidenceRecord,
    resources: Sequence[InputResource],
    selections: Sequence[SelectionResult],
) -> None:
    """Require numeric selection from every scoped artifact using a tolerance."""

    if record.reproduction_tolerance is None:
        return
    for resource, selection in zip(resources, selections):
        if resource.comparison is not None and not any(
            _numeric_value(item.value) is not None for item in selection.items
        ):
            _fail(
                "reproduction.comparison.tolerance_incompatible",
                record.id,
                {"reason": "numeric_selection_required", "resource": resource.name},
            )


def definition_for_target(
    definitions: Sequence[EvidenceComparisonDefinition], target: Path
) -> EvidenceComparisonDefinition | None:
    """Return the unique definition for one canonical artifact path."""

    canonical = target.resolve().as_posix()
    matches = [
        item for item in definitions if item.resource.canonical_target == canonical
    ]
    if len(matches) > 1:
        _fail(
            "reproduction.comparison.declaration_conflict",
            canonical,
            {"definitions": len(matches)},
        )
    return matches[0] if matches else None


def _definition(
    resource: InputResource, *, data: DataFile, evidence: EvidenceFile
) -> EvidenceComparisonDefinition:
    records = tuple(
        record
        for record in evidence.records
        if record.kind != "artifact"
        and any(
            resolve_input_token(source.source, data).resource.canonical_target
            == resource.canonical_target
            for source in record.sources
        )
    )
    if not records:
        _fail(
            "reproduction.comparison.evidence_missing",
            resource.name,
            {"target": resource.location},
        )
    definition = {
        "comparison": resource.comparison.as_dict()
        if resource.comparison is not None
        else None,
        "records": [record.as_dict() for record in records],
        "resource": resource.name,
    }
    identity = hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()
    return EvidenceComparisonDefinition(resource, data, records, identity)


def _evaluate_records(
    definition: EvidenceComparisonDefinition, *, replacement: Path | None
) -> tuple[tuple[SelectionResult, ...], ...]:
    data = _data_for(definition)
    evaluated: list[tuple[SelectionResult, ...]] = []
    for record in definition.records:
        selections: list[SelectionResult] = []
        resources: list[InputResource] = []
        for source in record.sources:
            resolved = resolve_input_token(source.source, data)
            resources.append(resolved.resource)
            path = Path(resolved.path)
            if (
                replacement is not None
                and resolved.resource.canonical_target
                == definition.resource.canonical_target
            ):
                path = replacement
            if source.locator is None:
                _fail(
                    "reproduction.comparison.evidence_incompatible",
                    record.id,
                    {"reason": "locator_required"},
                )
            selection = evaluate_locator(path, source.locator)
            selections.append(selection)
        evaluate_transformation(
            record.transformation, selections, presentation_kind=record.kind
        )
        validate_tolerant_selection(record, resources, selections)
        evaluated.append(tuple(selections))
    return tuple(evaluated)


def _data_for(definition: EvidenceComparisonDefinition) -> DataFile:
    return definition.data


def _selections_equal(
    expected: Sequence[SelectionResult],
    regenerated: Sequence[SelectionResult],
    *,
    tolerance: Fraction | None,
) -> bool:
    if len(expected) != len(regenerated):
        return False
    return all(
        _selection_equal(left, right, tolerance=tolerance)
        for left, right in zip(expected, regenerated)
    )


def _selection_equal(
    expected: SelectionResult,
    regenerated: SelectionResult,
    *,
    tolerance: Fraction | None,
) -> bool:
    structural = (
        expected.locator_identity == regenerated.locator_identity
        and expected.source_profile == regenerated.source_profile
        and expected.matches == regenerated.matches
        and expected.membership == regenerated.membership
        and expected.identities == regenerated.identities
        and expected.shape == regenerated.shape
        and expected.limit_profile == regenerated.limit_profile
        and expected.declared_version == regenerated.declared_version
        and expected.effective_version == regenerated.effective_version
        and len(expected.items) == len(regenerated.items)
    )
    if not structural:
        return False
    return all(
        _item_equal(left, right, tolerance=tolerance)
        for left, right in zip(expected.items, regenerated.items)
    )


def _item_equal(
    expected: SelectionItem,
    regenerated: SelectionItem,
    *,
    tolerance: Fraction | None,
) -> bool:
    if (
        expected.coordinate != regenerated.coordinate
        or expected.record != regenerated.record
        or expected.field != regenerated.field
    ):
        return False
    if tolerance is None:
        return expected.value.typed_equal(regenerated.value)
    left = _numeric_value(expected.value)
    right = _numeric_value(regenerated.value)
    if left is None or right is None:
        return expected.value.typed_equal(regenerated.value)
    if expected.value.kind != regenerated.value.kind:
        return False
    return abs(left - right) <= tolerance


def _numeric_value(value: CanonicalValue) -> Fraction | None:
    if value.kind == "integer" and isinstance(value.value, str):
        return Fraction(int(value.value))
    if value.kind == "decimal" and isinstance(value.value, Mapping):
        coefficient = value.value.get("coefficient")
        exponent = value.value.get("exponent")
        if isinstance(coefficient, str) and isinstance(exponent, int):
            return _fraction(str(Decimal(coefficient).scaleb(exponent)))
    if value.kind == "binary_float" and isinstance(value.value, str):
        bits = dict(value.metadata).get("bits")
        formats = {16: ">e", 32: ">f", 64: ">d"}
        if bits not in formats:
            return None
        number = struct.unpack(formats[cast(int, bits)], bytes.fromhex(value.value))[0]
        if number == number and abs(number) != float("inf"):
            return Fraction.from_float(number)
    return None


def _selection_projection(selection: SelectionResult) -> Mapping[str, object]:
    return {
        "declared_version": selection.declared_version,
        "effective_version": selection.effective_version,
        "identities": [
            [value.projection for value in identity]
            for identity in selection.identities
        ],
        "items": [
            {
                "coordinate": list(item.coordinate),
                "field": list(item.field) if item.field is not None else None,
                "record": item.record,
                "value": item.value.projection,
            }
            for item in selection.items
        ],
        "limit_profile": selection.limit_profile,
        "locator": selection.locator_identity,
        "matches": selection.matches,
        "membership": list(selection.membership),
        "shape": list(selection.shape) if selection.shape is not None else None,
        "source_profile": selection.source_profile,
    }


def _record_identity(record: EvidenceRecord) -> str:
    return hashlib.sha256(canonical_json(record.as_dict()).encode("utf-8")).hexdigest()


def _fraction(value: str) -> Fraction:
    return Fraction(Decimal(value))


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise EvidenceComparisonError(
        code,
        subject,
        observed,
        "Evidence-Scoped Reproduction Comparison",
    )
