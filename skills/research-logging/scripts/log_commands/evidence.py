"""Entry-scoped evidence authoring actions."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_log_data import (
    Fingerprint,
    load_data_file,
    observe_file_content,
    resolve_input_token,
)
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    evidence_file_from_records,
    index_summary_references,
    load_evidence_file,
)
from validation.mechanical_values import CanonicalValue, SelectionResult
from validation.presentation import (
    CandidateEvaluation,
    PreparedArtifactObservation,
    PreparedEvidenceContext,
    bind_prepared_locator,
    evaluate_candidate_record,
    evaluate_prepared_definition,
    index_entry_presentations_all,
    prepare_common_evidence_context,
    require_artifact_baseline_form,
    require_artifact_fingerprint,
    select_prepared_source,
)
from validation.transformation import parse_markdown_table

from .context import EntryContext
from .model import ActionError, ActionResult, EvidenceCommonArguments
from .storage import entry_lock, remove_or_write

_NUMBER_PRESENTATION_RE = re.compile(
    r"(?P<number>[+-]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+)|[0-9]+)"
    r"(?:\.(?P<fraction>[0-9]+))?(?:[eE](?P<exponent>[+-]?[0-9]+))?)"
    r"(?P<unit>%|°C|°F|°|x|\s+\S(?:.*\S)?)?\Z"
)


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
                "sources": [source.source for source in record.sources],
            }
            for record in records
        ),
    )


def add_or_update_common(
    entry: EntryContext,
    *,
    action: str,
    arguments: EvidenceCommonArguments,
) -> ActionResult:
    """Build and completely evaluate one common one-source evidence record."""

    with entry_lock(entry):
        current = load_current(entry)
        prepared = prepare_common_evidence_context(
            entry_root=entry.root,
            log_root=entry.log.root,
            record_id=arguments.record_id,
            source=_token(arguments.source),
        )
        presentation = prepared.presentation
        if presentation.kind == "artifact":
            if (
                arguments.select
                or arguments.identity
                or arguments.where
                or arguments.as_percentage
                or arguments.scale is not None
                or arguments.reproduction_tolerance is not None
            ):
                raise ActionError(
                    "evidence.common.unsupported",
                    "artifact evidence accepts only one whole-artifact source",
                )
            evaluated = evaluate_prepared_definition(
                prepared,
                definition=_candidate_definition(
                    sources=[
                        {"source": _token(arguments.source), "locator": None},
                    ],
                    transformation=None,
                    tolerance=arguments.reproduction_tolerance,
                ),
                capture_artifact_fingerprint=True,
            )
            return apply_candidate_locked(
                entry,
                action,
                evaluated,
                current=current,
                dry_run=arguments.dry_run,
            )
        prepared, locator = _common_locator(prepared, arguments)
        transformation = _common_transformation(prepared, arguments)
        evaluated = evaluate_prepared_definition(
            prepared,
            definition=_candidate_definition(
                sources=[
                    {"source": _token(arguments.source), "locator": locator},
                ],
                transformation=transformation,
                tolerance=arguments.reproduction_tolerance,
            ),
            capture_artifact_fingerprint=True,
        )
        return apply_candidate_locked(
            entry,
            action,
            evaluated,
            current=current,
            dry_run=arguments.dry_run,
        )


def apply_candidate_locked(
    entry: EntryContext,
    action: str,
    evaluated: CandidateEvaluation,
    *,
    current: EvidenceFile | None,
    dry_run: bool,
) -> ActionResult:
    """Apply one fully evaluated candidate while the entry lock is held."""

    candidate = evaluated.record
    prepared_artifact_observation = evaluated.artifact_observation
    existing = {record.id: record for record in current.records} if current else {}
    previous = existing.get(candidate.id)
    if action == "add" and candidate.id in existing:
        if existing[candidate.id] == candidate:
            return _result(action, "unchanged", False)
        raise ActionError("evidence.record.conflict", candidate.id)
    if action == "update" and candidate.id not in existing:
        raise ActionError("evidence.record.missing", candidate.id)
    if action == "update" and existing[candidate.id] == candidate:
        return _result(
            action,
            "unchanged",
            False,
            records=_artifact_update_report(
                previous, prepared_artifact_observation
            ),
        )
    _recheck_prepared_artifact(
        entry,
        candidate,
        prepared_artifact_observation=prepared_artifact_observation,
    )
    existing[candidate.id] = candidate
    built = _build(entry, tuple(existing.values()))
    if not dry_run:
        remove_or_write(built.path, built.canonical_json())
    return _result(
        action,
        "dry-run" if dry_run else "changed",
        True,
        records=_artifact_update_report(previous, prepared_artifact_observation),
    )


def rename(
    entry: EntryContext, old_id: str, new_id: str, *, dry_run: bool
) -> ActionResult:
    """Rename one registry ID after the agent completes all Markdown edits."""

    with entry_lock(entry):
        current = _required(entry)
        existing = {record.id: record for record in current.records}
        if old_id not in existing:
            raise ActionError("evidence.record.missing", old_id)
        if new_id in existing:
            raise ActionError("evidence.record.conflict", new_id)
        marker_ids = {
            item.id
            for item in index_entry_presentations_all(entry.root, entry.log.root)
        }
        summary_ids = {
            item.evidence_id
            for item in index_summary_references(
                entry.log.summary.read_text(encoding="utf-8")
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
        built = _build(entry, tuple(existing.values()))
        if not dry_run:
            remove_or_write(built.path, built.canonical_json())
        return _result("rename", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, record_id: str, *, dry_run: bool) -> ActionResult:
    """Remove one record only after its marker and summary references are absent."""

    with entry_lock(entry):
        current = load_current(entry)
        if current is None or record_id not in {item.id for item in current.records}:
            return _result("remove", "absent", False)
        marker_ids = {
            item.id
            for item in index_entry_presentations_all(entry.root, entry.log.root)
        }
        references = index_summary_references(
            entry.log.summary.read_text(encoding="utf-8")
        )
        if record_id in marker_ids or any(
            item.entry == entry.id and item.evidence_id == record_id
            for item in references
        ):
            raise ActionError(
                "evidence.remove.markdown_present",
                "remove the marker and summary references before the registry record",
            )
        remaining = tuple(item for item in current.records if item.id != record_id)
        text = _build(entry, remaining).canonical_json() if remaining else None
        if not dry_run:
            remove_or_write(current.path, text)
        return _result("remove", "dry-run" if dry_run else "changed", True)


def _common_locator(
    prepared: PreparedEvidenceContext, arguments: EvidenceCommonArguments
) -> tuple[PreparedEvidenceContext, Mapping[str, Any]]:
    presentation = prepared.presentation
    if (
        presentation.kind == "output"
        and not arguments.select
        and not arguments.identity
        and not arguments.where
    ):
        base: dict[str, Any] = {
            "text": {"contains": presentation.value, "occurrence": 1}
        }
    else:
        if not arguments.select:
            raise ActionError(
                "evidence.common.unsupported",
                "this presentation requires --select or advanced definition mode",
            )
        base = {"select": [_pointer(value) for value in arguments.select]}
        if arguments.identity:
            base["identity"] = [_pointer(value) for value in arguments.identity]
        if arguments.where:
            base["where"] = [
                {
                    "op": "eq",
                    "path": _pointer(pointer),
                    "value": _typed_value(kind, value),
                }
                for pointer, kind, value in arguments.where
            ]
    observation = prepared.sources[0].observation
    assert observation is not None
    if observation.profile in {"hdf5", "json", "npz"} and "text" not in base:
        base["path"] = []
    prepared = select_prepared_source(prepared, base)
    selection = prepared.sources[0].selection
    assert selection is not None
    expect: dict[str, Any] = {
        "items": len(selection.items),
        "matches": selection.matches,
    }
    if selection.identities:
        expect["identities"] = [
            [value.projection for value in identity_value]
            for identity_value in selection.identities
        ]
    if selection.shape is not None:
        expect["shape"] = list(selection.shape)
    locator = {**base, "expect": expect}
    return bind_prepared_locator(prepared, locator), locator


def _common_transformation(
    prepared: PreparedEvidenceContext,
    arguments: EvidenceCommonArguments,
) -> Mapping[str, Any] | None:
    presentation = prepared.presentation
    selection = prepared.sources[0].selection
    assert selection is not None
    if arguments.as_percentage:
        if presentation.kind == "table":
            raise ActionError(
                "evidence.common.unsupported",
                "table conversions require advanced definition mode",
            )
        match = _number_presentation(presentation.value)
        return {
            "decimal_places": len(match.group("fraction") or ""),
            "form": "percentage",
            "source": {"input": 0, "item": 0},
        }
    if arguments.scale is not None:
        if presentation.kind == "table":
            raise ActionError(
                "evidence.common.unsupported",
                "table conversions require advanced definition mode",
            )
        return _scale_transformation(presentation.value, selection, arguments.scale)
    if presentation.kind != "table":
        if (
            len(selection.items) == 1
            and _identity_text(selection.items[0].value) == presentation.value
        ):
            return None
        if len(selection.items) == 1:
            descriptor = _scalar_descriptor(
                selection.items[0].value, presentation.value
            )
            return {
                "form": "scalar",
                "values": [
                    {**descriptor["value"], "source": {"input": 0, "item": 0}}
                ],
                **({"unit": descriptor["unit"]} if "unit" in descriptor else {}),
            }
        return None
    headings, rows = parse_markdown_table(presentation.value)
    columns = _direct_columns(selection, rows, len(headings))
    return {
        "columns": columns,
        "form": "table",
        "headings": list(headings),
        "mode": "direct",
    }


def _scale_transformation(
    presented: str,
    selection: SelectionResult,
    scale: str,
) -> Mapping[str, Any]:
    try:
        factor = Decimal(scale)
    except InvalidOperation as error:
        raise ActionError("evidence.scale.invalid", scale) from error
    if not factor.is_finite():
        raise ActionError("evidence.scale.invalid", scale)
    if len(selection.items) != 1:
        raise ActionError(
            "evidence.common.unsupported", "scaling requires one selected value"
        )
    descriptor = _scalar_descriptor(selection.items[0].value, presented)
    value: dict[str, Any] = {
        **descriptor["value"],
        "scale": factor,
        "source": {"input": 0, "item": 0},
    }
    result: dict[str, Any] = {"form": "scalar", "values": [value]}
    if "unit" in descriptor:
        result["unit"] = descriptor["unit"]
    return result


def _number_presentation(value: str) -> re.Match[str]:
    match = _NUMBER_PRESENTATION_RE.fullmatch(value)
    if match is None:
        raise ActionError("evidence.common.unsupported", "cannot infer numeric render")
    return match


def _scalar_descriptor(value: CanonicalValue, presented: str) -> dict[str, Any]:
    if value.kind == "null" and presented == "null":
        return {"form": "scalar", "value": {}}
    match = _number_presentation(presented)
    expression = _scalar_expression(value, _number_render(match))
    descriptor: dict[str, Any] = {"form": "scalar", "value": expression}
    unit = (match.group("unit") or "").strip()
    if unit:
        descriptor["unit"] = unit
    return descriptor


def _number_render(match: re.Match[str]) -> dict[str, Any]:
    number = match.group("number")
    render: dict[str, Any]
    if match.group("exponent") is not None:
        if "E" in number or "+" in match.group("exponent"):
            raise ActionError(
                "evidence.common.unsupported", "unsupported scientific spelling"
            )
        mantissa = re.split("[eE]", number.lstrip("+-"), maxsplit=1)[0]
        digits = mantissa.replace(".", "").lstrip("0")
        render = {
            "mode": "scientific",
            "significant_figures": (
                len(digits) if digits else max(1, len(mantissa.replace(".", "")))
            ),
        }
    elif "," in number:
        if match.group("fraction") is not None:
            raise ActionError(
                "evidence.common.unsupported", "grouped decimals require a definition"
            )
        render = {"mode": "grouped_integer"}
    elif match.group("fraction") is not None:
        render = {
            "decimal_places": len(match.group("fraction")),
            "mode": "fixed",
        }
    else:
        render = {"mode": "integer"}
    if number.startswith("+"):
        render["sign"] = "always"
    return render


def _scalar_expression(
    value: CanonicalValue, render: Mapping[str, Any]
) -> dict[str, Any]:
    expression: dict[str, Any] = {"render": dict(render)}
    if value.kind == "string":
        expression["parse"] = "decimal"
    elif value.kind not in {"binary_float", "decimal", "integer"}:
        raise ActionError(
            "evidence.common.unsupported",
            f"cannot render source type {value.kind!r} as a scalar",
        )
    return expression


def _direct_columns(
    selection: SelectionResult,
    rows: Sequence[Sequence[str]],
    width: int,
) -> list[dict[str, Any]]:
    values = _direct_values(selection, len(rows), width)
    columns: list[dict[str, Any]] = []
    for column in range(width):
        descriptors = [
            _direct_descriptor(values[row][column], rows[row][column])
            for row in range(len(rows))
        ]
        if not descriptors or any(value != descriptors[0] for value in descriptors[1:]):
            raise ActionError(
                "evidence.common.unsupported",
                "direct-table columns require one consistent rendering",
            )
        columns.append(descriptors[0])
    return columns


def _direct_values(
    selection: SelectionResult, row_count: int, width: int
) -> tuple[tuple[CanonicalValue, ...], ...]:
    if len(selection.items) == 1 and selection.items[0].value.kind == "array":
        outer = selection.items[0].value
        flat = tuple(outer.value) if isinstance(outer.value, tuple) else ()
        nested = tuple(
            tuple(value.value)
            for value in flat
            if isinstance(value, CanonicalValue)
            and value.kind == "array"
            and isinstance(value.value, tuple)
        )
        if len(nested) == len(flat) == row_count and all(
            len(row) == width for row in nested
        ):
            return nested
        shape = dict(outer.metadata).get("shape")
        if shape == [row_count, width] and len(flat) == row_count * width:
            return tuple(
                tuple(flat[offset : offset + width])
                for offset in range(0, len(flat), width)
            )
    items = tuple(item.value for item in selection.items)
    if len(items) == row_count * width:
        return tuple(
            tuple(items[offset : offset + width])
            for offset in range(0, len(items), width)
        )
    raise ActionError(
        "evidence.common.unsupported",
        "selected source does not have the presented direct-table shape",
    )


def _direct_descriptor(value: CanonicalValue, presented: str) -> dict[str, Any]:
    if value.kind == "string" and value.value == presented:
        return {"form": "text"}
    if value.kind == "null" and presented == "null":
        return {"form": "scalar", "value": {}}
    boolean_styles = {
        "Pass": "pass_fail",
        "Fail": "pass_fail",
        "true": "true_false",
        "false": "true_false",
        "yes": "yes_no",
        "no": "yes_no",
    }
    if presented in boolean_styles and value.kind in {"boolean", "string"}:
        descriptor: dict[str, Any] = {
            "form": "boolean",
            "style": boolean_styles[presented],
        }
        if value.kind == "string":
            descriptor["parse"] = "boolean"
        return descriptor
    return _scalar_descriptor(value, presented)


def _identity_text(value: CanonicalValue) -> str | None:
    if value.kind == "string":
        return str(value.value)
    if value.kind == "integer":
        return str(value.value)
    if value.kind == "decimal" and isinstance(value.value, Mapping):
        coefficient = Decimal(str(value.value.get("coefficient")))
        return format(coefficient.scaleb(int(value.value.get("exponent", 0))), "f")
    if value.kind == "boolean":
        return "true" if value.value else "false"
    if value.kind == "null":
        return "null"
    return None


def _pointer(value: str) -> list[object]:
    if value == "":
        return []
    if not value.startswith("/"):
        raise ActionError("evidence.pointer.invalid", value)
    result: list[object] = []
    for raw in value[1:].split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        result.append(int(segment) if segment.isdigit() else segment)
    return result


def _typed_value(kind: str, value: str) -> object:
    if kind == "string":
        return value
    if kind == "integer":
        try:
            return int(value)
        except ValueError as error:
            raise ActionError("evidence.condition.invalid", value) from error
    if kind == "decimal":
        try:
            result = Decimal(value)
        except InvalidOperation as error:
            raise ActionError("evidence.condition.invalid", value) from error
        if not result.is_finite():
            raise ActionError("evidence.condition.invalid", value)
        return result
    if kind == "boolean" and value in {"true", "false"}:
        return value == "true"
    if kind == "null" and value == "null":
        return None
    raise ActionError("evidence.condition.invalid", f"{kind}:{value}")


def _token(source: str) -> str:
    return source if source.startswith("<") else f"<{source}>"


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


def _recheck_prepared_artifact(
    _entry: EntryContext,
    _candidate: EvidenceRecord,
    *,
    prepared_artifact_observation: PreparedArtifactObservation | None,
) -> None:
    """Reject publication when an accepted artifact changed after preparation."""

    if prepared_artifact_observation is None:
        return
    current_path = prepared_artifact_observation.path
    digest, current_identity = observe_file_content(current_path)
    if (
        Fingerprint("sha256", digest) != prepared_artifact_observation.fingerprint
        or current_identity != prepared_artifact_observation.identity
    ):
        raise ActionError("evidence.artifact.source_changed", _candidate.id)


def _artifact_source_path(entry: EntryContext, record: EvidenceRecord) -> Path:
    data = load_data_file(entry.root / "data.json", entry_root=entry.root)
    return Path(resolve_input_token(record.sources[0].source, data).path).resolve()


def _artifact_update_report(
    old: EvidenceRecord | None, prepared: PreparedArtifactObservation | None
) -> tuple[dict[str, object], ...] | None:
    if prepared is None:
        return None
    old_fingerprint = None if old is None else old.artifact_fingerprint
    return (
        {
            "artifact_fingerprint": {
                "initial_acceptance": old_fingerprint is None,
                "new": prepared.fingerprint.as_dict(),
                "old": (
                    old_fingerprint.as_dict()
                    if old_fingerprint is not None
                    else None
                ),
            }
        },
    )


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
