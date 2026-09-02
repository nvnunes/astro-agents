"""Strict serialization for cached successful evidence selections."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .json_codec import V2JsonError, canonical_json, decode_json
from .mechanical_values import (
    CanonicalValue,
    SelectionItem,
    SelectionResult,
    selection_dependency,
)

SELECTION_CODEC_SCHEMA = "research-log-selection-result/1"
MAX_CODEC_BYTES = 512 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTENT_IDENTITY_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class SelectionCodecError(ValueError):
    """Raised when cached selection bytes violate the exact codec contract."""


def encode_selection(result: SelectionResult) -> bytes:
    """Return deterministic UTF-8 bytes for one successful selection."""

    payload = {
        "schema": SELECTION_CODEC_SCHEMA,
        "selection": {
            "declared_version": result.declared_version,
            "dependency_projection": result.dependency_projection,
            "effective_version": result.effective_version,
            "identities": [
                [value.projection for value in identity]
                for identity in result.identities
            ],
            "items": [_item_projection(item) for item in result.items],
            "limit_profile": result.limit_profile,
            "locator_identity": result.locator_identity,
            "matches": result.matches,
            "membership": list(result.membership),
            "shape": list(result.shape) if result.shape is not None else None,
            "source_identity": result.source_identity,
            "source_profile": result.source_profile,
        },
    }
    try:
        return canonical_json(payload).encode("utf-8")
    except V2JsonError as error:
        raise SelectionCodecError(str(error)) from error


def decode_selection(payload: bytes) -> SelectionResult:
    """Decode exact cached bytes and reject malformed or inconsistent state."""

    if len(payload) > MAX_CODEC_BYTES:
        raise SelectionCodecError("cached selection exceeds the codec bound")
    try:
        text = payload.decode("utf-8")
        root = decode_json(
            text,
            maximum_bytes=MAX_CODEC_BYTES,
            subject="cached selection",
        )
    except (UnicodeError, V2JsonError) as error:
        raise SelectionCodecError(str(error)) from error
    envelope = _mapping(root, "cached selection")
    if set(envelope) != {"schema", "selection"}:
        raise SelectionCodecError("cached selection envelope has incorrect fields")
    if envelope["schema"] != SELECTION_CODEC_SCHEMA:
        raise SelectionCodecError("cached selection schema is unsupported")
    try:
        return _decode_result(envelope["selection"])
    except SelectionCodecError:
        raise
    except (TypeError, ValueError) as error:
        raise SelectionCodecError("cached selection result is invalid") from error


def _item_projection(item: SelectionItem) -> Mapping[str, object]:
    return {
        "coordinate": list(item.coordinate),
        "field": list(item.field) if item.field is not None else None,
        "record": item.record,
        "value": item.value.projection,
    }


def _decode_result(value: object) -> SelectionResult:
    item = _mapping(value, "selection")
    expected = {
        "declared_version",
        "dependency_projection",
        "effective_version",
        "identities",
        "items",
        "limit_profile",
        "locator_identity",
        "matches",
        "membership",
        "shape",
        "source_identity",
        "source_profile",
    }
    if set(item) != expected:
        raise SelectionCodecError("cached selection has incorrect fields")
    source_identity = _string(item["source_identity"], "source_identity")
    dependency = _string(item["dependency_projection"], "dependency_projection")
    if _CONTENT_IDENTITY_RE.fullmatch(source_identity) is None:
        raise SelectionCodecError("cached source identity is invalid")
    if _SHA256_RE.fullmatch(dependency) is None:
        raise SelectionCodecError("cached dependency projection is invalid")
    items = tuple(
        _decode_item(candidate, index)
        for index, candidate in enumerate(_sequence(item["items"], "items"))
    )
    identities = tuple(
        tuple(
            _decode_canonical(candidate, f"identities[{number}][{index}]")
            for index, candidate in enumerate(
                _sequence(identity, f"identities[{number}]")
            )
        )
        for number, identity in enumerate(_sequence(item["identities"], "identities"))
    )
    shape_value = item["shape"]
    shape = (
        None
        if shape_value is None
        else tuple(
            _nonnegative_integer(candidate, f"shape[{index}]")
            for index, candidate in enumerate(_sequence(shape_value, "shape"))
        )
    )
    result = SelectionResult(
        locator_identity=_string(item["locator_identity"], "locator_identity"),
        source_identity=source_identity,
        source_profile=_string(item["source_profile"], "source_profile"),
        items=items,
        matches=_nonnegative_integer(item["matches"], "matches"),
        membership=tuple(
            _string(candidate, f"membership[{index}]")
            for index, candidate in enumerate(
                _sequence(item["membership"], "membership")
            )
        ),
        identities=identities,
        shape=shape,
        dependency_projection=dependency,
        limit_profile=_string(item["limit_profile"], "limit_profile"),
        declared_version=_string(item["declared_version"], "declared_version"),
        effective_version=_string(item["effective_version"], "effective_version"),
    )
    expected_dependency = selection_dependency(
        source_identity=result.source_identity,
        locator_identity=result.locator_identity,
        items=result.items,
    )
    if result.dependency_projection != expected_dependency:
        raise SelectionCodecError("cached dependency projection does not match items")
    return result


def _decode_item(value: object, index: int) -> SelectionItem:
    item = _mapping(value, f"items[{index}]")
    if set(item) != {"coordinate", "field", "record", "value"}:
        raise SelectionCodecError(f"items[{index}] has incorrect fields")
    record_value = item["record"]
    field_value = item["field"]
    return SelectionItem(
        coordinate=tuple(_coordinate(item["coordinate"], f"items[{index}].coordinate")),
        value=_decode_canonical(item["value"], f"items[{index}].value"),
        record=(
            None
            if record_value is None
            else _nonnegative_integer(record_value, f"items[{index}].record")
        ),
        field=(
            None
            if field_value is None
            else tuple(_coordinate(field_value, f"items[{index}].field"))
        ),
    )


def _coordinate(value: object, subject: str) -> list[object]:
    result: list[object] = []
    for index, candidate in enumerate(_sequence(value, subject)):
        if isinstance(candidate, str):
            result.append(candidate)
        elif (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and candidate >= 0
        ):
            result.append(candidate)
        else:
            raise SelectionCodecError(f"{subject}[{index}] is invalid")
    return result


def _decode_canonical(value: object, subject: str) -> CanonicalValue:
    item = _mapping(value, subject)
    if "type" not in item or "value" not in item:
        raise SelectionCodecError(f"{subject} is not a canonical value")
    kind = _string(item["type"], f"{subject}.type")
    metadata_fields = _metadata_fields(kind)
    if set(item) != {"type", "value", *metadata_fields}:
        raise SelectionCodecError(f"{subject} has incorrect fields for {kind}")
    decoded = _canonical_payload(kind, item["value"], subject)
    metadata = tuple(
        (name, _metadata_value(name, item[name], subject))
        for name in sorted(metadata_fields)
    )
    if kind == "binary_float":
        bits = dict(metadata).get("bits")
        if not isinstance(bits, int) or len(cast(str, decoded)) != bits // 4:
            raise SelectionCodecError(f"{subject}.value width does not match bits")
    return CanonicalValue(kind, decoded, metadata)


def _metadata_fields(kind: str) -> set[str]:
    fields = {
        "array": {"dtype", "order", "shape"},
        "binary_float": {"bits"},
        "date": {"resolution"},
        "datetime": {"resolution"},
        "duration": {"unit"},
        "quantity": {"unit"},
        "time": {"resolution"},
    }
    supported = {
        "array",
        "binary_float",
        "boolean",
        "bytes",
        "date",
        "datetime",
        "decimal",
        "duration",
        "integer",
        "mapping",
        "null",
        "quantity",
        "record",
        "string",
        "table",
        "time",
    }
    if kind not in supported:
        raise SelectionCodecError(f"unsupported canonical value type: {kind}")
    return fields.get(kind, set())


def _canonical_payload(kind: str, value: object, subject: str) -> object:
    decoders = {
        "array": _canonical_sequence,
        "binary_float": _canonical_binary_float,
        "boolean": _canonical_boolean,
        "bytes": _canonical_bytes,
        "date": _canonical_string,
        "datetime": _canonical_string,
        "decimal": _canonical_decimal,
        "duration": _canonical_unit_value,
        "integer": _canonical_integer,
        "mapping": _canonical_mapping,
        "null": _canonical_null,
        "quantity": _canonical_unit_value,
        "record": _canonical_mapping,
        "string": _canonical_string,
        "table": _canonical_sequence,
        "time": _canonical_string,
    }
    decoder = decoders.get(kind)
    if decoder is None:
        raise SelectionCodecError(f"unsupported canonical value type: {kind}")
    return decoder(value, subject)


def _canonical_null(value: object, subject: str) -> None:
    if value is not None:
        raise SelectionCodecError(f"{subject}.value must be null")
    return None


def _canonical_boolean(value: object, subject: str) -> bool:
    if not isinstance(value, bool):
        raise SelectionCodecError(f"{subject}.value must be Boolean")
    return value


def _canonical_integer(value: object, subject: str) -> str:
    text = _string(value, f"{subject}.value")
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text) is None:
        raise SelectionCodecError(f"{subject}.value is not an integer")
    return text


def _canonical_decimal(value: object, subject: str) -> Mapping[str, object]:
    decimal = _mapping(value, f"{subject}.value")
    if set(decimal) != {"coefficient", "exponent"}:
        raise SelectionCodecError(f"{subject}.value is not a decimal")
    coefficient = _string(decimal["coefficient"], f"{subject}.value.coefficient")
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", coefficient) is None:
        raise SelectionCodecError(f"{subject}.value coefficient is invalid")
    return {
        "coefficient": coefficient,
        "exponent": _integer(decimal["exponent"], f"{subject}.value.exponent"),
    }


def _canonical_binary_float(value: object, subject: str) -> str:
    encoded = _string(value, f"{subject}.value")
    if re.fullmatch(r"[0-9a-f]+", encoded) is None:
        raise SelectionCodecError(f"{subject}.value is not hexadecimal")
    return encoded


def _canonical_bytes(value: object, subject: str) -> bytes:
    encoded = _text(value, f"{subject}.value")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise SelectionCodecError(f"{subject}.value is not base64") from error
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise SelectionCodecError(f"{subject}.value is not canonical base64")
    return raw


def _canonical_string(value: object, subject: str) -> str:
    return _text(value, f"{subject}.value")


def _canonical_unit_value(value: object, subject: str) -> CanonicalValue:
    numeric = _decode_canonical(value, f"{subject}.value")
    if numeric.kind not in {"integer", "decimal", "binary_float"}:
        raise SelectionCodecError(f"{subject}.value must be numeric")
    return numeric


def _canonical_sequence(value: object, subject: str) -> tuple[CanonicalValue, ...]:
    return tuple(
        _decode_canonical(candidate, f"{subject}.value[{index}]")
        for index, candidate in enumerate(_sequence(value, f"{subject}.value"))
    )


def _canonical_mapping(value: object, subject: str) -> Mapping[str, CanonicalValue]:
    mapping = _mapping(value, f"{subject}.value")
    return {
        key: _decode_canonical(candidate, f"{subject}.value.{key}")
        for key, candidate in mapping.items()
    }


def _metadata_value(name: str, value: object, subject: str) -> object:
    if name == "bits":
        bits = _integer(value, f"{subject}.bits")
        if bits not in {16, 32, 64, 128}:
            raise SelectionCodecError(f"{subject}.bits is unsupported")
        return bits
    if name == "shape":
        return [
            _nonnegative_integer(candidate, f"{subject}.shape[{index}]")
            for index, candidate in enumerate(_sequence(value, f"{subject}.shape"))
        ]
    text = _string(value, f"{subject}.{name}")
    if not text:
        raise SelectionCodecError(f"{subject}.{name} must not be empty")
    if name == "order" and text != "C":
        raise SelectionCodecError(f"{subject}.order is unsupported")
    return text


def _mapping(value: object, subject: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise SelectionCodecError(f"{subject} must be an object")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, subject: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise SelectionCodecError(f"{subject} must be an array")
    return value


def _string(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value:
        raise SelectionCodecError(f"{subject} must be a nonempty string")
    return value


def _text(value: object, subject: str) -> str:
    if not isinstance(value, str):
        raise SelectionCodecError(f"{subject} must be a string")
    return value


def _integer(value: object, subject: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SelectionCodecError(f"{subject} must be an integer")
    return value


def _nonnegative_integer(value: object, subject: str) -> int:
    result = _integer(value, subject)
    if result < 0:
        raise SelectionCodecError(f"{subject} must be nonnegative")
    return result


__all__ = [
    "SELECTION_CODEC_SCHEMA",
    "SelectionCodecError",
    "decode_selection",
    "encode_selection",
]
