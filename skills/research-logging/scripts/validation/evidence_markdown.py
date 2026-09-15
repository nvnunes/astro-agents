"""Bounded Markdown-owned evidence declarations and exact replacement regions."""

from __future__ import annotations

import re
import shlex
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, NoReturn, Sequence

from .errors import MechanicalContractError
from .evidence import MARKDOWN_LINK_RE, MAX_PRESENTATION_BYTES, authored_eid_comments
from .locator import parse_locator
from .transformation import parse_markdown_table

MAX_DEFINITION_BYTES = 32 * 1024


@dataclass(frozen=True)
class MarkdownEvidence:
    """One definition and its sole owned visible region in an authored document."""

    id: str
    kind: str
    start: int
    end: int
    before: str
    sources: tuple[Mapping[str, Any], ...]
    transformation: Mapping[str, Any] | None
    tolerance: str | None
    header: str = ""
    statistic: Mapping[str, Any] | None = None


def read_markdown_evidence(text: str, record_id: str) -> MarkdownEvidence:
    """Read one unique adjacent marker without moving its comment or neighbors."""

    try:
        return _read_markdown_evidence(text, record_id)
    except MechanicalContractError as error:
        raise MechanicalContractError(
            error.code, record_id, error.observed, "Evidence Markdown Definitions"
        ) from error


def _read_markdown_evidence(text: str, record_id: str) -> MarkdownEvidence:

    markers = [
        match for match in authored_eid_comments(text) if match["id"] == record_id
    ]
    if len(markers) != 1:
        _fail("evidence.marker.unresolved", {"id": record_id, "matches": len(markers)})
    marker = markers[0]
    definition = marker["definition"].strip()
    if not definition or len(definition.encode("utf-8")) > MAX_DEFINITION_BYTES:
        _fail(
            "evidence.definition.invalid",
            {"id": record_id, "reason": "complete_comment_required"},
        )
    kind, start, end, header = _region(text, marker.start(), marker.end())
    global_fields, sources, columns = _clauses(definition)
    if not sources:
        _fail("evidence.definition.invalid", {"reason": "source_required"})
    tolerance = _single(global_fields, "reproduction_tolerance")
    if set(global_fields) - {"form", "unit", "reproduction_tolerance"}:
        _fail("evidence.definition.invalid", {"fields": sorted(global_fields)})
    normalized_sources = tuple(_source(clause, kind) for clause in sources)
    if kind == "table" and len(normalized_sources) == 1:
        source = normalized_sources[0]
        locator = {
            **source["locator"],
            "select": [pointer(_single(fields, "column") or "") for fields in columns],
        }
        normalized_sources = ({"source": source["source"], "locator": locator},)
    transformation = _transformation(kind, sources, columns, header, global_fields)
    return MarkdownEvidence(
        record_id,
        kind,
        start,
        end,
        text[start:end],
        normalized_sources,
        transformation,
        tolerance,
        header,
        {
            "sources": sources,
            "form": _single(global_fields, "form"),
            "unit": _single(global_fields, "unit"),
        }
        if kind == "statistic"
        else None,
    )


def materialize_statistic(
    declaration: Mapping[str, Any], counts: Sequence[int]
) -> Mapping[str, Any] | None:
    """Bind bounded operand formatting to the observed selection order."""

    return _statistic_transformation(
        declaration["sources"], declaration["form"], declaration["unit"], counts
    )


def pointer(value: str) -> list[object]:
    """Decode a pointer with explicit wildcard and half-open slice segments."""

    if value == "":
        return []
    if not value.startswith("/"):
        _fail("evidence.pointer.invalid", {"pointer": value})
    result: list[object] = []
    for raw in value[1:].split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        bounds = re.fullmatch(r"\[([0-9]*):([0-9]*)\]", segment)
        if segment == "*":
            result.append({"all": True})
        elif bounds:
            result.append(
                {"slice": [int(item) if item else None for item in bounds.groups()]}
            )
        else:
            result.append(int(segment) if segment.isdigit() else segment)
    return result


def _region(text: str, marker_start: int, marker_end: int) -> tuple[str, int, int, str]:
    prefix = text[:marker_start]
    code = re.search(r"`[^`\r\n]*`\Z", prefix)
    if code:
        return "statistic", code.start(), marker_start, ""
    links = list(MARKDOWN_LINK_RE.finditer(prefix))
    if links and links[-1].end() == marker_start:
        return "artifact", links[-1].start(), marker_start, ""
    if prefix.rsplit("\n", 1)[-1].strip() or not text[marker_end:].startswith("\n"):
        _fail("evidence.marker.invalid", {"reason": "adjacent_presentation_required"})
    start = marker_end + 1
    fence = re.match(r"(`{3,})(text|diff)\n", text[start:])
    if fence:
        body_start = start + fence.end()
        closing = re.search(
            r"(?m)^" + re.escape(fence[1]) + r"[ \t]*$", text[body_start:]
        )
        if closing is None:
            _fail("evidence.marker.invalid", {"reason": "unclosed_text_fence"})
        end = body_start + closing.start()
        return ("output" if fence[2] == "text" else "artifact"), body_start, end, ""
    lines = text[start:].splitlines(keepends=True)
    if len(lines) < 2:
        _fail("evidence.marker.invalid", {"reason": "table_or_text_fence_required"})
    header = "".join(lines[:2])
    parse_markdown_table(header)
    end = start + len(header)
    for line in lines[2:]:
        if "|" not in line or not line.strip():
            break
        end += len(line)
    if end - start > MAX_PRESENTATION_BYTES:
        _fail("evidence.presentation.too_large", {"bytes": end - start})
    return "table", start, end, header


def _lex_fields(definition: str) -> list[dict[str, list[str]]]:
    lexer = shlex.shlex(definition, posix=True, punctuation_chars=";")
    lexer.whitespace_split = True
    lexer.commenters = ""
    groups: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token == ";":
                groups.append([])
            else:
                groups[-1].append(token)
    except ValueError as error:
        _fail("evidence.definition.invalid", {"reason": str(error)})
    result = []
    for tokens in groups:
        fields: dict[str, list[str]] = {}
        for token in tokens:
            key, separator, value = token.partition("=")
            if not separator or not key:
                _fail("evidence.definition.invalid", {"token": token})
            fields.setdefault(key, []).append(value)
        result.append(fields)
    return result


def _clauses(
    definition: str,
) -> tuple[
    dict[str, list[str]], list[dict[str, list[str]]], list[dict[str, list[str]]]
]:
    global_fields: dict[str, list[str]] = {}
    sources: list[dict[str, list[str]]] = []
    columns: list[dict[str, list[str]]] = []
    for fields in _lex_fields(definition):
        if "column" in fields:
            columns.append(fields)
        else:
            for key in ("form", "unit", "reproduction_tolerance"):
                if key in fields:
                    global_fields.setdefault(key, []).extend(fields.pop(key))
            if "source" in fields:
                sources.append(fields)
            elif fields:
                _fail("evidence.definition.invalid", {"fields": sorted(fields)})
    return global_fields, sources, columns


def _single(fields: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = fields.get(name, ())
    if len(values) > 1:
        _fail(
            "evidence.definition.invalid",
            {"reason": "duplicate_property", "property": name},
        )
    return values[0] if values else None


def _source(fields: Mapping[str, Sequence[str]], kind: str) -> Mapping[str, Any]:
    allowed = {
        "source",
        "path",
        "select",
        "identity",
        "where",
        "line",
        "lines",
        "chars",
        "render",
        "parse",
        "scale",
        "magnitude",
        "sign",
    }
    if set(fields) - allowed:
        _fail(
            "evidence.definition.invalid",
            {
                "fields": sorted(fields),
                "required_action": "record a script for derived evidence",
            },
        )
    source = _single(fields, "source")
    assert source is not None
    token = source_token(source)
    if kind == "artifact":
        if set(fields) != {"source"}:
            _fail(
                "evidence.definition.invalid",
                {"reason": "whole_artifact_source_required"},
            )
        return {"source": token, "locator": None}
    if kind == "output":
        if set(fields) - {"source", "line", "lines", "chars"}:
            _fail("evidence.definition.invalid", {"reason": "verbatim_slice_required"})
        selector = {
            key: _single(fields, key)
            for key in ("line", "lines", "chars")
            if key in fields
        }
        locator: Mapping[str, Any] = {"text": selector}
    else:
        locator = {"path": pointer(_single(fields, "path") or "")}
        for key in ("select", "identity"):
            if key in fields:
                locator[key] = [pointer(value) for value in fields[key]]
        if "where" in fields:
            locator["where"] = [_condition(value) for value in fields["where"]]
    return {"source": token, "locator": dict(parse_locator(locator).value)}


def _condition(value: str) -> Mapping[str, Any]:
    parts = value.split(":", 3)
    if len(parts) != 4:
        _fail(
            "evidence.condition.invalid",
            {"value": value, "syntax": "POINTER:eq|in:TYPE:VALUE"},
        )
    path, operation, kind, literal = parts
    parsed = {"parse": kind} if kind in {"decimal", "integer"} else {}
    if operation not in {"eq", "in"}:
        _fail("evidence.condition.invalid", {"operation": operation})
    if operation == "in":
        return {
            **parsed,
            "path": pointer(path),
            "op": operation,
            "values": [_literal(kind, item) for item in literal.split(",")],
        }
    return {
        **parsed,
        "path": pointer(path),
        "op": operation,
        "value": _literal(kind, literal),
    }


def _literal(kind: str, value: str) -> object:
    value = urllib.parse.unquote(value)
    if kind == "string":
        return value
    if kind == "boolean" and value in {"true", "false"}:
        return value == "true"
    if kind == "null" and value == "null":
        return None
    try:
        if kind == "integer":
            return int(value)
        if kind == "decimal":
            result = Decimal(value)
            if result.is_finite():
                return result
    except (ValueError, InvalidOperation):
        pass
    _fail("evidence.condition.invalid", {"type": kind, "value": value})


def source_token(source: str) -> str:
    """Expand a compact source name/member into the canonical data token."""

    if source.startswith("<"):
        return source
    name, separator, member = source.partition("/")
    return f"<{name}>" + (f"/{member}" if separator else "")


def _transformation(
    kind: str,
    sources: Sequence[Mapping[str, Sequence[str]]],
    columns: Sequence[Mapping[str, Sequence[str]]],
    header: str,
    global_fields: Mapping[str, Sequence[str]],
) -> Mapping[str, Any] | None:
    form = _single(global_fields, "form")
    unit = _single(global_fields, "unit")
    if kind in {"artifact", "output"}:
        if len(sources) != 1 or columns or form or unit:
            _fail(
                "evidence.definition.invalid",
                {"reason": "single_verbatim_source_required"},
            )
        return None
    if kind == "table":
        return _table_transformation(sources, columns, header, global_fields)
    if columns:
        _fail("evidence.definition.invalid", {"reason": "columns_require_table"})
    return _statistic_transformation(sources, form, unit)


def _table_transformation(
    sources: Sequence[Mapping[str, Sequence[str]]],
    columns: Sequence[Mapping[str, Sequence[str]]],
    header: str,
    global_fields: Mapping[str, Sequence[str]],
) -> Mapping[str, Any]:
    if (
        len(sources) != 1
        or not columns
        or set(global_fields) - {"reproduction_tolerance"}
    ):
        _fail(
            "evidence.table.unsupported",
            {"required_action": "record a script for joined or derived tables"},
        )
    headings, _ = parse_markdown_table(header)
    if len(headings) != len(columns):
        _fail(
            "evidence.table.columns",
            {"headings": len(headings), "columns": len(columns)},
        )
    if "select" in sources[0] or set(sources[0]) & {
        "render",
        "parse",
        "scale",
        "magnitude",
        "sign",
    }:
        _fail("evidence.table.columns", {"reason": "use_column_bindings_not_select"})
    return {
        "form": "table",
        "mode": "direct",
        "headings": list(headings),
        "columns": [_column(fields) for fields in columns],
    }


def _statistic_transformation(
    sources: Sequence[Mapping[str, Sequence[str]]],
    form: str | None,
    unit: str | None,
    counts: Sequence[int] | None = None,
) -> Mapping[str, Any] | None:
    if len(sources) == 1 and (sources[0].get("render", [""])[0]).startswith("boolean:"):
        if form not in {None, "scalar"} or unit:
            _fail(
                "evidence.definition.invalid", {"reason": "boolean_is_one_short_value"}
            )
        column = _column(
            {
                key: value
                for key, value in sources[0].items()
                if key in {"render", "parse", "scale", "magnitude", "sign"}
            }
        )
        return {**column, "source": {"input": 0, "item": 0}}
    if form not in {
        None,
        "scalar",
        "range",
        "tuple",
        "interval",
        "plus_minus",
        "percentage",
    }:
        _fail("evidence.definition.invalid", {"form": form})
    if form == "percentage":
        return _percentage_transformation(sources, unit)
    values = []
    for number, fields in enumerate(sources):
        count = (
            counts[number]
            if counts is not None
            else max(
                (
                    len(fields.get(key, ()))
                    for key in (
                        "select",
                        "render",
                        "parse",
                        "scale",
                        "magnitude",
                        "sign",
                    )
                ),
                default=1,
            )
            or 1
        )
        for item in range(count):
            expression = _operand_expression(fields, item, count)
            values.append({**expression, "source": {"input": number, "item": item}})
    if (
        form is None
        and len(values) == 1
        and not _operand_expression(sources[0], 0, 1)
        and not unit
    ):
        return None
    return {
        "form": form or "scalar",
        "values": values,
        **({"unit": unit} if unit else {}),
    }


def _operand_expression(
    fields: Mapping[str, Sequence[str]], item: int, count: int
) -> dict[str, Any]:
    selected = {}
    for key in ("render", "parse", "scale", "magnitude", "sign"):
        values = fields.get(key, ())
        if len(values) not in {0, 1, count}:
            _fail(
                "evidence.render.invalid",
                {"property": key, "values": len(values), "selected_items": count},
            )
        if values:
            selected[key] = [values[0] if len(values) == 1 else values[item]]
    return _expression(selected)


def _percentage_transformation(
    sources: Sequence[Mapping[str, Sequence[str]]],
    unit: str | None,
) -> Mapping[str, Any]:
    if len(sources) != 1 or unit:
        _fail("evidence.definition.invalid", {"form": "percentage"})
    render = _single(sources[0], "render") or "fixed:1"
    match = re.fullmatch(r"fixed:([0-9]+)", render)
    if match is None or set(sources[0]) & {"parse", "scale", "magnitude", "sign"}:
        _fail("evidence.definition.invalid", {"form": "percentage", "render": render})
    return {
        "form": "percentage",
        "source": {"input": 0, "item": 0},
        "decimal_places": int(match[1]),
    }


def _column(fields: Mapping[str, Sequence[str]]) -> Mapping[str, Any]:
    if set(fields) - {
        "column",
        "render",
        "parse",
        "scale",
        "magnitude",
        "sign",
        "unit",
    }:
        _fail(
            "evidence.table.unsupported",
            {
                "fields": sorted(fields),
                "required_action": "record a script for derived columns",
            },
        )
    _single(fields, "column")
    render = _single(fields, "render") or "text"
    if render == "text":
        if set(fields) != {"column"} and set(fields) != {"column", "render"}:
            _fail("evidence.definition.invalid", {"reason": "text_column_is_verbatim"})
        return {"form": "text"}
    if render.startswith("boolean:"):
        if set(fields) - {"column", "render", "parse"}:
            _fail(
                "evidence.definition.invalid",
                {"reason": "boolean_column_has_no_numeric_transform"},
            )
        return {
            "form": "boolean",
            "style": render.split(":", 1)[1],
            **({"parse": _single(fields, "parse")} if "parse" in fields else {}),
        }
    if render.startswith("percentage:"):
        precision = render.split(":", 1)[1]
        if not precision.isdigit() or set(fields) - {"column", "render"}:
            _fail("evidence.render.invalid", {"render": render})
        return {"form": "percentage", "decimal_places": int(precision)}
    return {
        "form": "scalar",
        "value": _expression(fields),
        **({"unit": _single(fields, "unit")} if "unit" in fields else {}),
    }


def _expression(fields: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    expression: dict[str, Any] = {}
    for name in ("parse", "scale", "magnitude"):
        value = _single(fields, name)
        if value is not None:
            if name == "magnitude" and value != "true":
                _fail("evidence.definition.invalid", {"magnitude": value})
            expression[name] = (
                True
                if name == "magnitude"
                else _literal("decimal", value)
                if name == "scale"
                else value
            )
    render = _single(fields, "render")
    if render is not None:
        mode, _, precision = render.partition(":")
        descriptor: dict[str, Any] = {"mode": mode}
        if mode in {"fixed", "scientific", "significant"}:
            if not precision.isdigit():
                _fail("evidence.render.invalid", {"render": render})
            descriptor[
                "decimal_places" if mode == "fixed" else "significant_figures"
            ] = int(precision)
        elif mode not in {"integer", "grouped_integer"} or precision:
            _fail("evidence.render.invalid", {"render": render})
        sign = _single(fields, "sign")
        if sign is not None:
            descriptor["sign"] = sign
        expression["render"] = descriptor
    elif "sign" in fields:
        _fail("evidence.render.invalid", {"reason": "sign_requires_render"})
    return expression


def _fail(code: str, observed: object) -> NoReturn:
    raise MechanicalContractError(
        code, "Markdown evidence", observed, "Evidence Markdown Definitions"
    )
