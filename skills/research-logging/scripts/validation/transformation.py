"""Closed v2 presentation transformations and exact presentation comparison."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Mapping, NoReturn, Sequence, cast

from .errors import MechanicalContractError
from .json_codec import V2JsonError, canonical_json, decode_json
from .locator import DECIMAL_TEXT_RE, INTEGER_TEXT_RE, authored_literal
from .mechanical_values import CanonicalValue, SelectionResult

MAX_TRANSFORMATION_BYTES = 32 * 1024
MAX_INPUT_SLOTS = 256
MAX_OUTPUT_PARTS = 10_000
MAX_TABLE_CELLS = 10_000
MAX_AUTHORED_TEXT_BYTES = 64 * 1024
MAX_PRESENTED_BYTES = 64 * 1024
MAX_UNIT_BYTES = 32
ATTACHED_UNITS = frozenset({"%", "°", "°C", "°F", "x"})
NON_TABLE_COUNTS = {
    "interval": (3, 3),
    "plus_minus": (2, 2),
    "range": (2, 2),
    "scalar": (1, 1),
    "text": (1, 1),
    "tuple": (2, 8),
}
BOOLEAN_STYLES = {
    "pass_fail": {True: "Pass", False: "Fail"},
    "true_false": {True: "true", False: "false"},
    "yes_no": {True: "yes", False: "no"},
}
SEQUENCE_SEPARATORS = {"comma": ", ", "dimensions": " x ", "slash": " / "}
ALIGNMENT_RE = re.compile(r":?-{3,}:?\Z")


# Transformation contracts and expression-consumption state.


class TransformationV2Error(MechanicalContractError):
    """One precise v2 transformation or presentation-comparison failure."""


@dataclass(frozen=True)
class InputReference:
    """One concrete consumed locator item."""

    input: int
    item: int


@dataclass(frozen=True)
class RenderedPart:
    """One rendered cell or value part and its exact provenance."""

    text: str
    numeric: bool
    references: tuple[InputReference, ...]
    intermediates: tuple[str, ...] = ()
    value_kind: str | None = None


@dataclass(frozen=True)
class TransformationResult:
    """One complete canonical statistic, output, or table result."""

    kind: str
    identity: str
    accepted_spellings: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    numerical_cells: frozenset[tuple[int, int]] = frozenset()
    references: tuple[InputReference, ...] = ()
    intermediates: tuple[str, ...] = ()
    dependency_projection: str = ""
    limit_profile: str = "v2-initial"


@dataclass(frozen=True)
class _ExpressionContext:
    inputs: Sequence[SelectionResult]
    tracker: _ConsumptionTracker
    reference_kind: str
    record: int | None = None


class _ConsumptionTracker:
    """Resolve and enforce exact one-time selection consumption."""

    def __init__(self, inputs: Sequence[SelectionResult]):
        if not inputs or len(inputs) > MAX_INPUT_SLOTS:
            _fail(
                "transformation.input.reference_invalid",
                "inputs",
                {"slots": len(inputs), "limit": MAX_INPUT_SLOTS},
            )
        self.inputs = inputs
        self.consumed: list[InputReference] = []

    def item(
        self, input_index: object, item_index: object
    ) -> tuple[CanonicalValue, InputReference]:
        source = self._input(input_index)
        if not _index(item_index) or cast(int, item_index) >= len(source.items):
            _fail(
                "transformation.input.reference_invalid",
                "source",
                {"input": input_index, "item": item_index},
            )
        reference = InputReference(cast(int, input_index), cast(int, item_index))
        self._consume(reference)
        return source.items[reference.item].value, reference

    def field(
        self, input_index: object, field_index: object, record: int
    ) -> tuple[CanonicalValue, InputReference]:
        source = self._input(input_index)
        if not _index(field_index):
            _fail(
                "transformation.input.reference_invalid",
                "field",
                {"input": input_index, "field": field_index},
            )
        grouped = [
            (index, item)
            for index, item in enumerate(source.items)
            if item.record == record
        ]
        field = cast(int, field_index)
        if field >= len(grouped):
            _fail(
                "transformation.input.reference_invalid",
                "field",
                {"input": input_index, "field": field, "record": record},
            )
        item_index, item = grouped[field]
        reference = InputReference(cast(int, input_index), item_index)
        self._consume(reference)
        return item.value, reference

    def complete(self) -> None:
        expected = {
            InputReference(input_index, item_index)
            for input_index, source in enumerate(self.inputs)
            for item_index in range(len(source.items))
        }
        observed = set(self.consumed)
        if expected - observed:
            _fail(
                "transformation.input.unused",
                "inputs",
                {"references": _reference_list(expected - observed)},
            )

    def _input(self, input_index: object) -> SelectionResult:
        if not _index(input_index) or cast(int, input_index) >= len(self.inputs):
            _fail(
                "transformation.input.reference_invalid",
                "source",
                {"input": input_index},
            )
        return self.inputs[cast(int, input_index)]

    def _consume(self, reference: InputReference) -> None:
        if reference in self.consumed:
            _fail(
                "transformation.input.reused",
                "source",
                {"input": reference.input, "item": reference.item},
            )
        self.consumed.append(reference)


# Public evaluation, parsing, and presentation comparison.


def evaluate_transformation(
    transformation: Mapping[str, Any] | None,
    inputs: Sequence[SelectionResult],
    *,
    presentation_kind: str,
) -> TransformationResult:
    """Evaluate one active-v2 transformation against ordered locator inputs."""

    tracker = _ConsumptionTracker(inputs)
    if transformation is None:
        result = _identity_result(inputs, tracker, presentation_kind)
    else:
        normalized, identity = _parse_transformation(transformation)
        form = normalized["form"]
        if form == "table":
            result = _table_result(normalized, identity, inputs, tracker)
        else:
            if presentation_kind == "table":
                _fail(
                    "transformation.output.shape",
                    "transformation",
                    {"form": form, "kind": presentation_kind},
                )
            result = _non_table_result(normalized, identity, inputs, tracker)
        if result.kind != presentation_kind:
            _fail(
                "association.kind_mismatch",
                "transformation",
                {"expected": result.kind, "observed": presentation_kind},
            )
    tracker.complete()
    return _with_dependency(result, inputs)


def parse_transformation(value: str) -> Mapping[str, Any]:
    """Parse one standalone, explicitly versioned v2 transformation."""

    if not isinstance(value, str):
        _fail("transformation.syntax.invalid", "transformation", {"value": value})
    version = re.match(r"v([0-9]+):", value)
    if version is None:
        _fail("transformation.syntax.invalid", "transformation", {"value": value})
    if version.group(1) != "2":
        _fail(
            "transformation.version.unsupported",
            "transformation",
            {"version": version.group(1)},
        )
    try:
        decoded = decode_json(
            value[version.end() :],
            maximum_bytes=MAX_TRANSFORMATION_BYTES - version.end(),
            subject="transformation",
        )
    except V2JsonError as exc:
        _fail("transformation.syntax.invalid", "transformation", {"error": str(exc)})
    if not isinstance(decoded, Mapping):
        _fail("transformation.syntax.invalid", "transformation", {"value": decoded})
    normalized, _ = _parse_transformation(decoded)
    return normalized


def compare_presentation(
    result: TransformationResult,
    *,
    presented_kind: str,
    presented: str,
) -> None:
    """Compare one complete strictly parsed presentation to its canonical result."""

    if len(presented.encode("utf-8")) > MAX_PRESENTED_BYTES:
        _fail(
            "transformation.output.too_large",
            "presentation",
            {"bytes": len(presented.encode("utf-8"))},
        )
    if result.kind != presented_kind:
        _fail(
            "association.kind_mismatch",
            "presentation",
            {"expected": result.kind, "observed": presented_kind},
        )
    if result.kind == "table":
        headings, rows = parse_markdown_table(presented)
        if headings != result.headings or rows != result.rows:
            _fail(
                "transformation.presentation.mismatch",
                "table",
                {"headings": headings, "rows": rows},
            )
        return
    if presented not in result.accepted_spellings:
        _fail(
            "transformation.presentation.mismatch",
            result.kind,
            {"expected": result.accepted_spellings, "observed": presented},
        )


def parse_markdown_table(
    text: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Parse only the ordinary rectangular Markdown table grammar in the spec."""

    lines = text.splitlines()
    if len(lines) < 3:
        _fail("association.presentation.syntax_invalid", "table", {"lines": len(lines)})
    parsed = [_markdown_cells(line) for line in lines]
    width = len(parsed[0])
    if not width or any(len(row) != width for row in parsed):
        _fail(
            "association.presentation.syntax_invalid",
            "table",
            {"widths": [len(row) for row in parsed]},
        )
    if not all(ALIGNMENT_RE.fullmatch(cell) for cell in parsed[1]):
        _fail(
            "association.presentation.syntax_invalid", "table", {"alignment": parsed[1]}
        )
    return tuple(parsed[0]), tuple(tuple(row) for row in parsed[2:])


# Recipe decoding and scalar rendering.


def _parse_transformation(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    if (
        not isinstance(value, Mapping)
        or not value
        or not isinstance(value.get("form"), str)
    ):
        _fail("transformation.syntax.invalid", "transformation", {"value": value})
    try:
        identity = "v2:" + canonical_json(value)
    except V2JsonError as exc:
        _fail("transformation.syntax.invalid", "transformation", {"error": str(exc)})
    if len(identity.encode("utf-8")) > MAX_TRANSFORMATION_BYTES:
        _fail(
            "transformation.output.too_large",
            "transformation",
            {"bytes": len(identity.encode("utf-8"))},
        )
    form = value["form"]
    if form not in {*NON_TABLE_COUNTS, "percentage", "table"}:
        _fail("transformation.syntax.invalid", "transformation", {"form": form})
    normalized = dict(value)
    if form == "percentage" and normalized.get("decimal_places") == 1:
        normalized.pop("decimal_places")
        identity = "v2:" + canonical_json(normalized)
    return normalized, identity


def _identity_result(
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
    presentation_kind: str,
) -> TransformationResult:
    if len(inputs) != 1 or len(inputs[0].items) != 1 or presentation_kind == "table":
        _fail(
            "transformation.input.reference_invalid",
            "identity",
            {"inputs": len(inputs), "items": [len(item.items) for item in inputs]},
        )
    value, reference = tracker.item(0, 0)
    if presentation_kind == "output" and value.kind != "string":
        _fail("transformation.type.mismatch", "identity", {"type": value.kind})
    text = _identity_text(value)
    return TransformationResult(
        kind=presentation_kind,
        identity="identity",
        accepted_spellings=(text,),
        references=(reference,),
    )


def _identity_text(value: CanonicalValue) -> str:
    if value.kind == "string":
        return cast(str, value.value)
    if value.kind == "integer":
        return cast(str, value.value)
    if value.kind == "decimal":
        parts = cast(Mapping[str, object], value.value)
        decimal = Decimal(cast(str, parts["coefficient"])).scaleb(
            cast(int, parts["exponent"])
        )
        return _plain_decimal(decimal)
    if value.kind == "boolean":
        return "true" if value.value else "false"
    if value.kind == "null":
        return "null"
    _fail("transformation.type.mismatch", "identity", {"type": value.kind})


def _non_table_result(
    recipe: Mapping[str, Any],
    identity: str,
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
) -> TransformationResult:
    form = cast(str, recipe["form"])
    context = _ExpressionContext(inputs, tracker, "item")
    spellings: tuple[str, ...]
    if form == "percentage":
        part = _percentage(recipe, context)
        spellings = (part.text,)
    else:
        values = _value_array(recipe, form)
        parts = [_value_expression(value, context) for value in values]
        spellings = _form_spellings(form, parts, _unit(recipe))
        part = RenderedPart(
            spellings[0],
            any(value.numeric for value in parts),
            tuple(reference for value in parts for reference in value.references),
            tuple(item for value in parts for item in value.intermediates),
        )
        if form == "text" and any(value.value_kind != "string" for value in parts):
            _fail("transformation.type.mismatch", form, {"types": _part_types(parts)})
        if form != "text" and any(not value.numeric for value in parts):
            _fail("transformation.type.mismatch", form, {"types": _part_types(parts)})
    return TransformationResult(
        kind="output" if form == "text" else "statistic",
        identity=identity,
        accepted_spellings=spellings,
        references=part.references,
        intermediates=part.intermediates,
    )


def _value_array(recipe: Mapping[str, Any], form: str) -> Sequence[Mapping[str, Any]]:
    allowed = {"form", "values"} | ({"unit"} if form != "text" else set())
    values = recipe.get("values")
    lower, upper = NON_TABLE_COUNTS[form]
    if (
        set(recipe) - allowed
        or not isinstance(values, list)
        or not lower <= len(values) <= upper
    ):
        _fail(
            "transformation.syntax.invalid",
            form,
            {
                "fields": sorted(recipe),
                "values": len(values) if isinstance(values, list) else None,
            },
        )
    if not all(isinstance(value, Mapping) for value in values):
        _fail("transformation.syntax.invalid", form, {"values": values})
    return cast(Sequence[Mapping[str, Any]], values)


def _value_expression(
    expression: Mapping[str, Any], context: _ExpressionContext
) -> RenderedPart:
    if (
        set(expression) - {"magnitude", "parse", "render", "scale", "source"}
        or "source" not in expression
    ):
        _fail("transformation.syntax.invalid", "value", {"fields": sorted(expression)})
    value, reference = _resolve_source(expression["source"], context)
    return _resolved_value_expression(expression, value, reference)


def _resolved_value_expression(
    expression: Mapping[str, Any],
    value: CanonicalValue,
    reference: InputReference,
) -> RenderedPart:
    parsed, intermediate = _apply_parse(value, expression.get("parse"))
    intermediates = list(intermediate)
    numeric = _numeric_fraction(parsed)
    if expression.get("magnitude") is not None:
        if expression["magnitude"] is not True or numeric is None:
            _fail(
                "transformation.type.mismatch",
                "magnitude",
                {"value": expression.get("magnitude")},
            )
        numeric = abs(numeric)
        intermediates.append(_fraction_identity(numeric))
    if "scale" in expression:
        if numeric is None:
            _fail("transformation.type.mismatch", "scale", {"type": parsed.kind})
        numeric *= _scale(expression["scale"])
        intermediates.append(_fraction_identity(numeric))
    needs_render = numeric is not None
    if needs_render:
        assert numeric is not None
        if "render" not in expression:
            _fail("transformation.render.invalid", "value", {"render": None})
        text = _render_number(numeric, expression["render"])
    else:
        if "render" in expression:
            _fail("transformation.render.invalid", "value", {"type": parsed.kind})
        text = _passthrough_text(parsed)
    return RenderedPart(
        text,
        needs_render,
        (reference,),
        tuple(intermediates),
        "numeric" if needs_render else parsed.kind,
    )


def _percentage(recipe: Mapping[str, Any], context: _ExpressionContext) -> RenderedPart:
    if not {"form", "source"} <= set(recipe) <= {"decimal_places", "form", "source"}:
        _fail("transformation.syntax.invalid", "percentage", {"fields": sorted(recipe)})
    places = recipe.get("decimal_places", 1)
    if not _precision(places, minimum=0):
        _fail("transformation.render.invalid", "percentage", {"decimal_places": places})
    value, reference = _resolve_source(recipe["source"], context)
    return _resolved_percentage(value, reference, cast(int, places))


def _resolved_percentage(
    value: CanonicalValue, reference: InputReference, places: int
) -> RenderedPart:
    parsed, intermediate = _apply_parse(
        value, "decimal" if value.kind == "string" else None
    )
    numeric = _numeric_fraction(parsed)
    if numeric is None:
        _fail("transformation.type.mismatch", "percentage", {"type": parsed.kind})
    scaled = numeric * 100
    text = _render_number(scaled, {"decimal_places": places, "mode": "fixed"}) + "%"
    return RenderedPart(
        text,
        True,
        (reference,),
        (*intermediate, _fraction_identity(scaled)),
        "numeric",
    )


def _resolve_source(
    value: object, context: _ExpressionContext
) -> tuple[CanonicalValue, InputReference]:
    if not isinstance(value, Mapping):
        _fail("transformation.input.reference_invalid", "source", {"value": value})
    if context.reference_kind == "item" and set(value) == {"input", "item"}:
        return context.tracker.item(value["input"], value["item"])
    if (
        context.reference_kind == "field"
        and set(value) == {"field", "input"}
        and context.record is not None
    ):
        return context.tracker.field(value["input"], value["field"], context.record)
    _fail(
        "transformation.input.reference_invalid",
        "source",
        {"fields": sorted(value)},
    )


def _apply_parse(
    value: CanonicalValue, parse: object
) -> tuple[CanonicalValue, tuple[str, ...]]:
    if parse is None:
        return value, ()
    if parse not in {"decimal", "integer"} or value.kind != "string":
        _fail(
            "transformation.parse_failed", "parse", {"parse": parse, "type": value.kind}
        )
    text = cast(str, value.value)
    if parse == "integer" and INTEGER_TEXT_RE.fullmatch(text):
        parsed = CanonicalValue("integer", str(int(text)))
    elif parse == "decimal" and DECIMAL_TEXT_RE.fullmatch(text):
        try:
            decimal = Decimal(text)
        except InvalidOperation:
            _fail("transformation.parse_failed", "parse", {"value": text})
        parsed = _decimal_canonical(decimal)
    else:
        _fail("transformation.parse_failed", "parse", {"value": text})
    return parsed, (parsed.identity,)


def _numeric_fraction(value: CanonicalValue) -> Fraction | None:
    if value.kind == "integer":
        return Fraction(int(cast(str, value.value)))
    if value.kind == "decimal":
        parts = cast(Mapping[str, object], value.value)
        coefficient = int(cast(str, parts["coefficient"]))
        exponent = cast(int, parts["exponent"])
        return (
            Fraction(coefficient * 10**exponent)
            if exponent >= 0
            else Fraction(coefficient, 10 ** (-exponent))
        )
    if value.kind == "binary_float":
        return _binary_fraction(value)
    return None


def _binary_fraction(value: CanonicalValue) -> Fraction:
    bits = dict(value.metadata).get("bits")
    if bits not in {16, 32, 64} or not isinstance(value.value, str):
        _fail("transformation.type.mismatch", "binary_float", {"bits": bits})
    widths = {16: (5, 10, 15), 32: (8, 23, 127), 64: (11, 52, 1023)}
    exponent_bits, fraction_bits, bias = widths[cast(int, bits)]
    raw = int(value.value, 16)
    sign = -1 if raw >> (cast(int, bits) - 1) else 1
    exponent = (raw >> fraction_bits) & ((1 << exponent_bits) - 1)
    fraction = raw & ((1 << fraction_bits) - 1)
    if exponent == (1 << exponent_bits) - 1:
        _fail(
            "transformation.nonfinite_unsupported", "binary_float", {"hex": value.value}
        )
    if exponent == 0:
        significand, power = fraction, 1 - bias - fraction_bits
    else:
        significand, power = (
            (1 << fraction_bits) + fraction,
            exponent - bias - fraction_bits,
        )
    result = Fraction(sign * significand)
    return result * 2**power if power >= 0 else result / 2 ** (-power)


def _scale(value: object) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        _fail("transformation.scale.invalid", "scale", {"value": value})
    numeric = (
        Fraction(value) if isinstance(value, int) else _fraction_from_decimal(value)
    )
    if not numeric:
        _fail("transformation.scale.invalid", "scale", {"value": value})
    return numeric


def _render_number(value: Fraction, render: object) -> str:
    if not isinstance(render, Mapping) or not isinstance(render.get("mode"), str):
        _fail("transformation.render.invalid", "render", {"value": render})
    mode = render["mode"]
    sign_always = _render_sign(render)
    if mode in {"integer", "grouped_integer"}:
        if set(render) - {"mode", "sign"} or value.denominator != 1:
            _fail(
                "transformation.render.invalid",
                "render",
                {"mode": mode, "value": str(value)},
            )
        text = str(abs(value.numerator))
        if mode == "grouped_integer":
            text = f"{int(text):,}"
        return _signed(text, value, sign_always)
    if mode == "fixed":
        places = render.get("decimal_places")
        if set(render) - {"decimal_places", "mode", "sign"} or not _precision(
            places, minimum=0
        ):
            _fail("transformation.render.invalid", "render", {"value": render})
        return _fixed(value, cast(int, places), sign_always)
    if mode in {"scientific", "significant"}:
        figures = render.get("significant_figures")
        if set(render) - {"mode", "sign", "significant_figures"} or not _precision(
            figures, minimum=1
        ):
            _fail("transformation.render.invalid", "render", {"value": render})
        return _significant(
            value,
            cast(int, figures),
            scientific=mode == "scientific",
            sign_always=sign_always,
        )
    _fail("transformation.render.invalid", "render", {"mode": mode})


def _fixed(value: Fraction, places: int, sign_always: bool) -> str:
    rounded = _round_half_even(value * 10**places)
    absolute = abs(rounded)
    digits = str(absolute).rjust(places + 1, "0")
    text = digits if places == 0 else f"{digits[:-places]}.{digits[-places:]}"
    rounded_value = Fraction(rounded, 10**places)
    return _signed(text, rounded_value, sign_always)


def _significant(
    value: Fraction, figures: int, *, scientific: bool, sign_always: bool
) -> str:
    if value == 0:
        coefficient = "0" if figures == 1 else "0." + "0" * (figures - 1)
        text = coefficient + ("e0" if scientific else "")
        return "+" + text if sign_always else text
    exponent = _decimal_exponent(abs(value))
    places = figures - 1 - exponent
    rounded = _round_at_decimal_place(value, places)
    rounded_exponent = _decimal_exponent(abs(rounded)) if rounded else 0
    if rounded_exponent != exponent:
        exponent = rounded_exponent
        places = figures - 1 - exponent
        rounded = _round_at_decimal_place(value, places)
    if scientific:
        coefficient = _fixed(rounded / _power10(exponent), figures - 1, False)
        text = f"{coefficient}e{exponent}"
        return _signed(text.lstrip("-"), rounded, sign_always)
    return _fixed(rounded, max(places, 0), sign_always)


def _form_spellings(
    form: str, parts: Sequence[RenderedPart], unit: str | None
) -> tuple[str, ...]:
    values = [part.text for part in parts]
    suffix = _unit_suffix(unit)
    if form in {"scalar", "text"}:
        return (values[0] + suffix,)
    if form == "range":
        return (f"{values[0]}–{values[1]}{suffix}",)
    if form == "plus_minus":
        return (
            f"{values[0]} ± {values[1]}{suffix}",
            f"{values[0]} +/- {values[1]}{suffix}",
        )
    if form == "interval":
        return (f"{values[0]} [{values[1]}, {values[2]}]{suffix}",)
    if form == "tuple":
        return (f"({', '.join(values)}){suffix}",)
    _fail("transformation.syntax.invalid", "form", {"form": form})


# Table modes and row composition.


def _table_result(
    recipe: Mapping[str, Any],
    identity: str,
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
) -> TransformationResult:
    headings = _headings(recipe.get("headings"))
    mode = recipe.get("mode")
    if mode == "direct":
        rows, numeric, intermediates = _direct_table(
            recipe, inputs, tracker, len(headings)
        )
    elif mode == "structured":
        rows, numeric, intermediates = _structured_table(
            recipe, inputs, tracker, len(headings)
        )
    elif mode == "summary":
        rows, numeric, intermediates = _summary_table(
            recipe, inputs, tracker, len(headings)
        )
    else:
        _fail("transformation.syntax.invalid", "table", {"mode": mode})
    if len(rows) * len(headings) > MAX_TABLE_CELLS:
        _fail(
            "transformation.output.too_large",
            "table",
            {"cells": len(rows) * len(headings)},
        )
    references = tuple(tracker.consumed)
    return TransformationResult(
        kind="table",
        identity=identity,
        headings=headings,
        rows=rows,
        numerical_cells=frozenset(numeric),
        references=references,
        intermediates=intermediates,
    )


def _direct_table(
    recipe: Mapping[str, Any],
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
    width: int,
) -> tuple[tuple[tuple[str, ...], ...], set[tuple[int, int]], tuple[str, ...]]:
    if set(recipe) != {"columns", "form", "headings", "mode"} or len(inputs) != 1:
        _fail(
            "transformation.table.direct_mismatch",
            "direct",
            {"fields": sorted(recipe), "inputs": len(inputs)},
        )
    columns = recipe["columns"]
    if not isinstance(columns, list) or len(columns) != width:
        _fail(
            "transformation.table.direct_mismatch",
            "direct",
            {"columns": columns, "width": width},
        )
    compound = _direct_array_rows(inputs[0], width)
    if compound is not None:
        reference_value, reference = tracker.item(0, 0)
        assert reference_value.kind == "array"
        return _render_direct_rows(compound, columns, reference)
    groups = _record_groups(inputs[0])
    if not groups or any(len(group) != width for group in groups):
        _fail(
            "transformation.table.direct_mismatch",
            "direct",
            {"records": [len(group) for group in groups]},
        )
    rows: list[tuple[str, ...]] = []
    numerical: set[tuple[int, int]] = set()
    intermediates: list[str] = []
    for row_number, group in enumerate(groups, 1):
        rendered = [
            _direct_cell(descriptor, tracker, item_index)
            for descriptor, item_index in zip(columns, group)
        ]
        rows.append(tuple(part.text for part in rendered))
        numerical.update(
            (row_number, column)
            for column, part in enumerate(rendered, 1)
            if part.numeric
        )
        intermediates.extend(
            intermediate for part in rendered for intermediate in part.intermediates
        )
    return tuple(rows), numerical, tuple(intermediates)


def _render_direct_rows(
    rows: Sequence[Sequence[CanonicalValue]],
    columns: Sequence[object],
    reference: InputReference,
) -> tuple[tuple[tuple[str, ...], ...], set[tuple[int, int]], tuple[str, ...]]:
    rendered_rows: list[tuple[str, ...]] = []
    numerical: set[tuple[int, int]] = set()
    intermediates: list[str] = []
    for row_number, row in enumerate(rows, 1):
        rendered = [
            _render_direct_value(descriptor, value, reference)
            for descriptor, value in zip(columns, row)
        ]
        rendered_rows.append(tuple(part.text for part in rendered))
        numerical.update(
            (row_number, column)
            for column, part in enumerate(rendered, 1)
            if part.numeric
        )
        intermediates.extend(
            intermediate for part in rendered for intermediate in part.intermediates
        )
    return tuple(rendered_rows), numerical, tuple(intermediates)


def _direct_array_rows(
    source: SelectionResult, width: int
) -> tuple[tuple[CanonicalValue, ...], ...] | None:
    if len(source.items) != 1 or source.items[0].value.kind != "array":
        return None
    array = source.items[0].value
    values = cast(tuple[CanonicalValue, ...], array.value)
    shape = dict(array.metadata).get("shape")
    if isinstance(shape, list) and len(shape) == 2:
        rows, columns = shape
        if not isinstance(rows, int) or columns != width or rows * width != len(values):
            _fail(
                "transformation.table.direct_mismatch",
                "direct array",
                {"shape": shape, "width": width},
            )
        return tuple(
            tuple(values[offset : offset + width])
            for offset in range(0, len(values), width)
        )
    nested = [
        cast(tuple[CanonicalValue, ...], value.value)
        for value in values
        if value.kind == "array"
    ]
    if (
        len(nested) != len(values)
        or not nested
        or any(len(row) != width for row in nested)
    ):
        _fail(
            "transformation.table.direct_mismatch",
            "direct array",
            {"rows": [len(row) for row in nested], "width": width},
        )
    return tuple(nested)


def _direct_cell(
    descriptor: object, tracker: _ConsumptionTracker, item_index: int
) -> RenderedPart:
    if not isinstance(descriptor, Mapping) or not isinstance(
        descriptor.get("form"), str
    ):
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    value, reference = tracker.item(0, item_index)
    return _render_direct_value(descriptor, value, reference)


def _render_direct_value(
    descriptor: object,
    value: CanonicalValue,
    reference: InputReference,
) -> RenderedPart:
    if not isinstance(descriptor, Mapping) or not isinstance(
        descriptor.get("form"), str
    ):
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    form = descriptor["form"]
    if form == "text":
        return _direct_text(descriptor, value, reference)
    if form == "boolean":
        return _boolean_implicit(descriptor, value, reference)
    if form == "percentage":
        return _direct_percentage(descriptor, value, reference)
    if form == "scalar":
        return _direct_scalar(descriptor, value, reference)
    _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})


def _direct_text(
    descriptor: Mapping[str, Any],
    value: CanonicalValue,
    reference: InputReference,
) -> RenderedPart:
    if set(descriptor) != {"form"}:
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    if value.kind != "string":
        _fail("transformation.type.mismatch", "text", {"type": value.kind})
    return RenderedPart(
        _passthrough_text(value), False, (reference,), value_kind="string"
    )


def _direct_percentage(
    descriptor: Mapping[str, Any],
    value: CanonicalValue,
    reference: InputReference,
) -> RenderedPart:
    if not {"form"} <= set(descriptor) <= {"decimal_places", "form"}:
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    places = descriptor.get("decimal_places", 1)
    if not _precision(places, minimum=0):
        _fail(
            "transformation.render.invalid",
            "percentage",
            {"decimal_places": places},
        )
    return _resolved_percentage(value, reference, cast(int, places))


def _direct_scalar(
    descriptor: Mapping[str, Any],
    value: CanonicalValue,
    reference: InputReference,
) -> RenderedPart:
    if set(descriptor) - {"form", "unit", "value"} or not isinstance(
        descriptor.get("value"), Mapping
    ):
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    expression = cast(Mapping[str, Any], descriptor["value"])
    if set(expression) - {"magnitude", "parse", "render", "scale"}:
        _fail("transformation.syntax.invalid", "direct column", {"value": descriptor})
    part = _resolved_value_expression(expression, value, reference)
    if not part.numeric and part.value_kind != "null":
        _fail(
            "transformation.type.mismatch",
            "scalar",
            {"type": part.value_kind},
        )
    return RenderedPart(
        part.text + _unit_suffix(_unit(descriptor)),
        part.numeric,
        part.references,
        part.intermediates,
    )


def _structured_table(
    recipe: Mapping[str, Any],
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
    width: int,
) -> tuple[tuple[tuple[str, ...], ...], set[tuple[int, int]], tuple[str, ...]]:
    if (
        set(recipe) != {"columns", "form", "headings", "mode", "rows"}
        or len(inputs) != 1
    ):
        _fail(
            "transformation.syntax.invalid",
            "structured",
            {"fields": sorted(recipe), "inputs": len(inputs)},
        )
    columns, rows_spec = recipe["columns"], recipe["rows"]
    if (
        not isinstance(columns, list)
        or len(columns) != width
        or not isinstance(rows_spec, Mapping)
        or not {"input"} <= set(rows_spec) <= {"input", "order"}
        or rows_spec["input"] != 0
    ):
        _fail(
            "transformation.syntax.invalid",
            "structured",
            {"columns": columns, "rows": rows_spec},
        )
    groups = _record_groups(inputs[0])
    order = _structured_order(rows_spec.get("order"), inputs[0], len(groups))
    rows: list[tuple[str, ...]] = []
    numeric: set[tuple[int, int]] = set()
    intermediates: list[str] = []
    for output_row, record in enumerate(order, 1):
        context = _ExpressionContext(inputs, tracker, "field", record)
        rendered = [_cell_recipe(column, context) for column in columns]
        rows.append(tuple(part.text for part in rendered))
        numeric.update(
            (output_row, column)
            for column, part in enumerate(rendered, 1)
            if part.numeric
        )
        intermediates.extend(
            intermediate for part in rendered for intermediate in part.intermediates
        )
    return tuple(rows), numeric, tuple(intermediates)


def _summary_table(
    recipe: Mapping[str, Any],
    inputs: Sequence[SelectionResult],
    tracker: _ConsumptionTracker,
    width: int,
) -> tuple[tuple[tuple[str, ...], ...], set[tuple[int, int]], tuple[str, ...]]:
    if (
        set(recipe) != {"form", "headings", "mode", "rows"}
        or not isinstance(recipe["rows"], list)
        or not recipe["rows"]
    ):
        _fail("transformation.syntax.invalid", "summary", {"fields": sorted(recipe)})
    rows: list[tuple[str, ...]] = []
    numeric: set[tuple[int, int]] = set()
    intermediates: list[str] = []
    context = _ExpressionContext(inputs, tracker, "item")
    for row_number, row in enumerate(recipe["rows"], 1):
        if not isinstance(row, list) or len(row) != width or not row:
            _fail("transformation.output.shape", "summary", {"row": row_number})
        rendered = [
            _summary_cell(cell, column, width, context)
            for column, cell in enumerate(row, 1)
        ]
        rows.append(tuple(part.text for part in rendered))
        numeric.update(
            (row_number, column)
            for column, part in enumerate(rendered, 1)
            if part.numeric
        )
        intermediates.extend(
            intermediate for part in rendered for intermediate in part.intermediates
        )
    return tuple(rows), numeric, tuple(intermediates)


def _summary_cell(
    cell: object, column: int, width: int, context: _ExpressionContext
) -> RenderedPart:
    if isinstance(cell, Mapping) and cell.get("form") == "label":
        text = cell.get("text")
        if (
            column != 1
            or width == 1
            or set(cell) != {"form", "text"}
            or not _cell_text(text)
        ):
            _fail(
                "transformation.table.label_invalid",
                "label",
                {"column": column, "text": text},
            )
        return RenderedPart(cast(str, text), False, ())
    return _cell_recipe(cell, context)


def _cell_recipe(cell: object, context: _ExpressionContext) -> RenderedPart:
    if not isinstance(cell, Mapping) or not isinstance(cell.get("form"), str):
        _fail("transformation.syntax.invalid", "cell", {"value": cell})
    form = cell["form"]
    if form == "percentage":
        return _percentage(cell, context)
    if form == "boolean":
        return _boolean_cell(cell, context)
    if form == "sequence":
        return _sequence_cell(cell, context)
    if form not in NON_TABLE_COUNTS:
        _fail("transformation.syntax.invalid", "cell", {"form": form})
    values = _value_array(cell, cast(str, form))
    parts = [_value_expression(value, context) for value in values]
    _validate_cell_parts(cast(str, form), parts)
    spelling = _form_spellings(cast(str, form), parts, _unit(cell))[0]
    if not _cell_text(spelling, allow_empty=form == "text"):
        _fail("transformation.output.shape", "cell", {"value": spelling})
    return RenderedPart(
        spelling,
        any(part.numeric for part in parts),
        tuple(reference for part in parts for reference in part.references),
        tuple(item for part in parts for item in part.intermediates),
    )


# Structured cell semantics and ordering.


def _validate_cell_parts(form: str, parts: Sequence[RenderedPart]) -> None:
    if form == "text":
        if any(part.value_kind != "string" for part in parts):
            _fail(
                "transformation.type.mismatch",
                "text",
                {"types": _part_types(parts)},
            )
    elif form == "scalar":
        if any(not part.numeric and part.value_kind != "null" for part in parts):
            _fail(
                "transformation.type.mismatch", "scalar", {"types": _part_types(parts)}
            )
    elif any(not part.numeric for part in parts):
        _fail(
            "transformation.type.mismatch",
            form,
            {"types": _part_types(parts)},
        )


def _boolean_cell(cell: Mapping[str, Any], context: _ExpressionContext) -> RenderedPart:
    if (
        set(cell) != {"form", "style", "values"}
        or not isinstance(cell["values"], list)
        or len(cell["values"]) != 1
    ):
        _fail("transformation.boolean.invalid", "boolean", {"value": cell})
    expression = cell["values"][0]
    if not isinstance(expression, Mapping) or not {"source"} <= set(expression) <= {
        "parse",
        "source",
    }:
        _fail("transformation.boolean.invalid", "boolean", {"value": expression})
    value, reference = _resolve_source(expression["source"], context)
    return _boolean_value(value, expression.get("parse"), cell["style"], reference)


def _boolean_implicit(
    descriptor: Mapping[str, Any], value: CanonicalValue, reference: InputReference
) -> RenderedPart:
    if not {"form", "style"} <= set(descriptor) <= {"form", "parse", "style"}:
        _fail("transformation.boolean.invalid", "boolean", {"value": descriptor})
    return _boolean_value(
        value, descriptor.get("parse"), descriptor["style"], reference
    )


def _boolean_value(
    value: CanonicalValue, parse: object, style: object, reference: InputReference
) -> RenderedPart:
    if style not in BOOLEAN_STYLES:
        _fail("transformation.boolean.invalid", "boolean", {"style": style})
    if parse is None and value.kind == "boolean":
        boolean = bool(value.value)
        intermediates: tuple[str, ...] = ()
    elif (
        parse == "boolean"
        and value.kind == "string"
        and value.value in {"true", "false", "True", "False"}
    ):
        boolean = value.value in {"true", "True"}
        intermediates = (CanonicalValue("boolean", boolean).identity,)
    else:
        _fail(
            "transformation.boolean.invalid",
            "boolean",
            {"parse": parse, "type": value.kind, "value": value.value},
        )
    return RenderedPart(
        BOOLEAN_STYLES[cast(str, style)][boolean],
        False,
        (reference,),
        intermediates,
        value_kind="boolean",
    )


def _sequence_cell(
    cell: Mapping[str, Any], context: _ExpressionContext
) -> RenderedPart:
    if (
        not {"form", "style", "values"}
        <= set(cell)
        <= {"form", "style", "unit", "values"}
        or cell["style"] not in SEQUENCE_SEPARATORS
    ):
        _fail("transformation.syntax.invalid", "sequence", {"value": cell})
    values = cell["values"]
    if not isinstance(values, list) or not 2 <= len(values) <= 8:
        _fail("transformation.syntax.invalid", "sequence", {"values": values})
    parts = [
        _value_expression(value, context)
        for value in values
        if isinstance(value, Mapping)
    ]
    if len(parts) != len(values) or not all(part.numeric for part in parts):
        _fail("transformation.type.mismatch", "sequence", {"values": values})
    text = SEQUENCE_SEPARATORS[cast(str, cell["style"])].join(
        part.text for part in parts
    )
    return RenderedPart(
        text + _unit_suffix(_unit(cell)),
        True,
        tuple(reference for part in parts for reference in part.references),
        tuple(item for part in parts for item in part.intermediates),
        value_kind="numeric",
    )


def _part_types(parts: Sequence[RenderedPart]) -> list[str | None]:
    return [part.value_kind for part in parts]


def _structured_order(
    value: object, source: SelectionResult, records: int
) -> list[int]:
    if value is None:
        return list(range(records))
    if not isinstance(value, list) or not source.identities or len(value) != records:
        _fail("transformation.table.order_mismatch", "order", {"value": value})
    expected = [
        canonical_json([item.projection for item in identity])
        for identity in source.identities
    ]
    observed = [
        canonical_json([authored_literal(item).projection for item in row])
        if isinstance(row, list)
        else "invalid"
        for row in value
    ]
    if sorted(observed) != sorted(expected):
        _fail(
            "transformation.table.order_mismatch",
            "order",
            {"expected": expected, "observed": observed},
        )
    return [expected.index(identity) for identity in observed]


def _record_groups(source: SelectionResult) -> list[list[int]]:
    groups: dict[int, list[int]] = {}
    for index, item in enumerate(source.items):
        if item.record is None:
            _fail("transformation.table.input_not_records", "input", {"item": index})
        groups.setdefault(item.record, []).append(index)
    return [groups[index] for index in sorted(groups)]


def _headings(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(_heading(item) for item in value)
    ):
        _fail("transformation.syntax.invalid", "headings", {"value": value})
    if sum(len(item.encode("utf-8")) for item in value) > MAX_AUTHORED_TEXT_BYTES:
        _fail(
            "transformation.output.too_large",
            "headings",
            {"bytes": sum(len(item.encode("utf-8")) for item in value)},
        )
    return tuple(value)


def _heading(value: object) -> bool:
    return isinstance(value, str) and _cell_text(value) and "|" not in value


def _cell_text(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and value == value.strip()
        and "|" not in value
        and not any(unicodedata.category(char) == "Cc" for char in value)
    )


def _unit(recipe: Mapping[str, Any]) -> str | None:
    value = recipe.get("unit")
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > MAX_UNIT_BYTES
        or any(char in value for char in "`|*_[]<>\r\n")
        or any(unicodedata.category(char) == "Cc" for char in value)
        or value.startswith("x ")
    ):
        _fail("transformation.syntax.invalid", "unit", {"value": value})
    return value


def _unit_suffix(unit: str | None) -> str:
    if unit is None:
        return ""
    return unit if unit in ATTACHED_UNITS else " " + unit


def _render_sign(value: Mapping[str, Any]) -> bool:
    sign = value.get("sign")
    if sign not in {None, "always"}:
        _fail("transformation.render.invalid", "render.sign", {"value": sign})
    return sign == "always"


def _passthrough_text(value: CanonicalValue) -> str:
    if value.kind == "string":
        return cast(str, value.value)
    if value.kind == "null":
        return "null"
    _fail("transformation.type.mismatch", "value", {"type": value.kind})


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if char == "|" and not escaped:
            cells.append(_markdown_cell("".join(current)))
            current = []
        else:
            current.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    cells.append(_markdown_cell("".join(current)))
    return cells


def _markdown_cell(value: str) -> str:
    value = value.strip(" ")
    if (
        len(value) >= 2
        and value.startswith("`")
        and value.endswith("`")
        and "`" not in value[1:-1]
    ):
        return value[1:-1]
    if "`" in value or re.search(r"(?<!\\)\\(?![\\|])", value):
        _fail("association.presentation.syntax_invalid", "table cell", {"value": value})
    return value.replace("\\|", "|").replace("\\\\", "\\")


def _round_half_even(value: Fraction) -> int:
    sign = -1 if value < 0 else 1
    numerator = abs(value.numerator)
    quotient, remainder = divmod(numerator, value.denominator)
    comparison = remainder * 2 - value.denominator
    if comparison > 0 or comparison == 0 and quotient % 2:
        quotient += 1
    return sign * quotient


def _round_at_decimal_place(value: Fraction, places: int) -> Fraction:
    if places >= 0:
        return Fraction(_round_half_even(value * 10**places), 10**places)
    factor = 10 ** (-places)
    return Fraction(_round_half_even(value / factor) * factor)


def _decimal_exponent(value: Fraction) -> int:
    exponent = 0
    if value >= 1:
        while value >= 10:
            value /= 10
            exponent += 1
    else:
        while value < 1:
            value *= 10
            exponent -= 1
    return exponent


def _power10(exponent: int) -> Fraction:
    return Fraction(10**exponent) if exponent >= 0 else Fraction(1, 10 ** (-exponent))


def _signed(text: str, value: Fraction, always: bool) -> str:
    if value < 0:
        return "-" + text
    return "+" + text if always else text


def _plain_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _fraction_from_decimal(value: Decimal) -> Fraction:
    if not value.is_finite():
        _fail("transformation.scale.invalid", "decimal", {"value": str(value)})
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    return (
        Fraction(coefficient * 10**exponent)
        if exponent >= 0
        else Fraction(coefficient, 10 ** (-exponent))
    )


def _decimal_canonical(value: Decimal) -> CanonicalValue:
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    return CanonicalValue(
        "decimal", {"coefficient": str(coefficient), "exponent": exponent}
    )


def _precision(value: object, *, minimum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= 18
    )


def _index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _fraction_identity(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _reference_list(
    values: Sequence[InputReference] | set[InputReference],
) -> list[Mapping[str, int]]:
    return [
        {"input": item.input, "item": item.item}
        for item in sorted(values, key=lambda item: (item.input, item.item))
    ]


def _with_dependency(
    result: TransformationResult, inputs: Sequence[SelectionResult]
) -> TransformationResult:
    payload = {
        "headings": list(result.headings),
        "identity": result.identity,
        "intermediates": list(result.intermediates),
        "inputs": [source.dependency_projection for source in inputs],
        "kind": result.kind,
        "limits": result.limit_profile,
        "numerical_cells": [list(cell) for cell in sorted(result.numerical_cells)],
        "references": _reference_list(result.references),
        "rows": [list(row) for row in result.rows],
        "spellings": list(result.accepted_spellings),
        "version": "v2",
    }
    dependency = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return replace(result, dependency_projection=dependency)


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise TransformationV2Error(
        code,
        subject,
        observed,
        "Presentation Transformation Subcontract",
    )
