"""Entry-scoped evidence authoring actions."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_log_data import load_data_file, resolve_input_token, verify_fingerprint
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    evidence_file_from_records,
    index_summary_references,
    load_evidence_file,
)
from validation.locator import (
    evaluate_locator,
    evaluate_observed_locator,
    observe_source,
)
from validation.mechanical_values import CanonicalValue, SelectionResult
from validation.presentation import (
    evaluate_candidate_record,
    find_entry_presentation,
    index_entry_presentations_all,
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

    current = _load(entry)
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
        current = _load(entry)
        locator = _common_locator(entry, arguments)
        transformation = _common_transformation(entry, arguments, locator)
        evaluated = evaluate_candidate_record(
            entry_root=entry.root,
            log_root=entry.log.root,
            record_id=arguments.record_id,
            raw_sources=(
                {"source": _token(arguments.source), "locator": locator},
            ),
            transformation=transformation,
        )
        candidate = evaluated.record
        existing = {record.id: record for record in current.records} if current else {}
        if action == "add" and arguments.record_id in existing:
            if existing[arguments.record_id] == candidate:
                return _result(action, "unchanged", False)
            raise ActionError("evidence.record.conflict", arguments.record_id)
        if action == "update" and arguments.record_id not in existing:
            raise ActionError("evidence.record.missing", arguments.record_id)
        if action == "update" and existing[arguments.record_id] == candidate:
            return _result(action, "unchanged", False)
        existing[arguments.record_id] = candidate
        built = _build(entry, tuple(existing.values()))
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
            raw_sources=tuple(source.as_dict() for source in old.sources),
            transformation=old.transformation,
        )
        if (
            evaluated.presentation.document != old.document
            or evaluated.presentation.kind != old.kind
        ):
            raise ActionError("evidence.rename.presentation_changed", new_id)
        existing[new_id] = EvidenceRecord(
            new_id, old.document, old.kind, old.sources, old.transformation
        )
        built = _build(entry, tuple(existing.values()))
        if not dry_run:
            remove_or_write(built.path, built.canonical_json())
        return _result("rename", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, record_id: str, *, dry_run: bool) -> ActionResult:
    """Remove one record only after its marker and summary references are absent."""

    with entry_lock(entry):
        current = _load(entry)
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
    entry: EntryContext, arguments: EvidenceCommonArguments
) -> Mapping[str, Any]:
    presentation = find_entry_presentation(
        entry.root, entry.log.root, arguments.record_id
    )
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
    data = load_data_file(entry.root / "data.json", entry_root=entry.root)
    resolved = resolve_input_token(_token(arguments.source), data)
    verify_fingerprint(resolved.resource)
    observation = observe_source(Path(resolved.path))
    if observation.profile in {"hdf5", "json", "npz"} and "text" not in base:
        base["path"] = []
    selection = evaluate_observed_locator(observation, base)
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
    return {**base, "expect": expect}


def _common_transformation(
    entry: EntryContext,
    arguments: EvidenceCommonArguments,
    locator: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    presentation = find_entry_presentation(
        entry.root, entry.log.root, arguments.record_id
    )
    selection = _selection(entry, arguments.source, locator)
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


def _selection(
    entry: EntryContext, source: str, locator: Mapping[str, Any]
) -> SelectionResult:
    data = load_data_file(entry.root / "data.json", entry_root=entry.root)
    resolved = resolve_input_token(_token(source), data)
    return evaluate_locator(Path(resolved.path), locator)


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


def _load(entry: EntryContext) -> EvidenceFile | None:
    path = entry.root / "evidence.json"
    return (
        load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _required(entry: EntryContext) -> EvidenceFile:
    current = _load(entry)
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


def _result(action: str, status: str, changed: bool) -> ActionResult:
    return ActionResult(
        f"evidence.{action}",
        status,
        f"evidence.{status}",
        changed,
    )
