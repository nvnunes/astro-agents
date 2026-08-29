"""Strict JSON decoding and canonical serialization shared by v2 contracts."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping, cast


class V2JsonError(ValueError):
    """Raised when v2 JSON is malformed or cannot be canonicalized."""


def decode_json(text: str, *, maximum_bytes: int, subject: str) -> Any:
    """Decode bounded UTF-8 text while preserving exact JSON numeric meaning."""

    if text.startswith("\ufeff"):
        raise V2JsonError(f"{subject} must not contain a byte-order mark")
    size = len(text.encode("utf-8"))
    if size > maximum_bytes:
        raise V2JsonError(
            f"{subject} is too large: observed {size} bytes, limit {maximum_bytes}"
        )
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError, V2JsonError) as exc:
        raise V2JsonError(f"invalid {subject}: {exc}") from exc


def canonical_json(value: object) -> str:
    """Serialize one strict v2 JSON value without insignificant whitespace."""

    if isinstance(value, Mapping):
        return _canonical_mapping(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    return _canonical_atom(value)


def _canonical_atom(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise V2JsonError("canonical JSON numbers must be finite")
        return _canonical_decimal(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise V2JsonError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_mapping(value: Mapping[object, object]) -> str:
    if not all(isinstance(key, str) for key in value):
        raise V2JsonError("canonical JSON object keys must be strings")
    typed = cast(Mapping[str, object], value)
    return (
        "{"
        + ",".join(
            f"{canonical_json(key)}:{canonical_json(typed[key])}"
            for key in sorted(typed)
        )
        + "}"
    )


def canonicalize(value: object) -> Any:
    """Return ordinary JSON-compatible values with decimals kept exact."""

    canonical_json(value)
    if isinstance(value, Mapping):
        return {key: canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


def _canonical_decimal(value: Decimal) -> str:
    value = value.normalize()
    if value == 0:
        return "0.0"
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    coefficient = "".join(str(digit) for digit in digits)
    prefix = "-" if sign else ""
    if exponent >= 0:
        return prefix + coefficient + "0" * exponent + ".0"
    point = len(coefficient) + exponent
    if point > 0:
        return prefix + coefficient[:point] + "." + coefficient[point:]
    return prefix + "0." + "0" * (-point) + coefficient


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise V2JsonError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise V2JsonError(f"non-finite JSON number is prohibited: {value}")
