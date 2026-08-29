"""Canonical typed values and ordered selections for mechanical evidence."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from .json_codec import canonical_json


@dataclass(frozen=True)
class CanonicalValue:
    """One implementation-independent tagged value."""

    kind: str
    value: object
    metadata: tuple[tuple[str, object], ...] = ()

    @property
    def projection(self) -> Mapping[str, object]:
        """Return the stable tagged projection used by equality and caching."""

        projected: object
        if self.kind == "bytes":
            assert isinstance(self.value, bytes)
            projected = base64.b64encode(self.value).decode("ascii")
        elif self.kind in {"array", "record", "mapping", "table"}:
            projected = _project_compound(self.value)
        else:
            projected = self.value
        result: dict[str, object] = {"type": self.kind, "value": projected}
        result.update(self.metadata)
        return result

    @property
    def identity(self) -> str:
        """Return canonical JSON for this exact typed value."""

        return canonical_json(self.projection)

    def typed_equal(self, other: CanonicalValue) -> bool:
        """Compare canonical type and value, with IEEE NaN inequality."""

        if self.kind == "binary_float" and _binary_float_nan(self):
            return False
        if other.kind == "binary_float" and _binary_float_nan(other):
            return False
        return self.identity == other.identity


@dataclass(frozen=True)
class SelectionItem:
    """One ordered selected value at a canonical source coordinate."""

    coordinate: tuple[object, ...]
    value: CanonicalValue
    record: int | None = None
    field: tuple[object, ...] | None = None

    @property
    def identity(self) -> str:
        """Return stable coordinate plus typed-value identity."""

        return canonical_json(
            {
                "coordinate": list(self.coordinate),
                "value": self.value.projection,
            }
        )


@dataclass(frozen=True)
class SelectionResult:
    """Successful bounded locator selection and dependency projection."""

    locator_identity: str
    source_identity: str
    source_profile: str
    items: tuple[SelectionItem, ...]
    matches: int
    membership: tuple[str, ...]
    identities: tuple[tuple[CanonicalValue, ...], ...] = ()
    shape: tuple[int, ...] | None = None
    dependency_projection: str = ""
    limit_profile: str = "v2-initial"
    declared_version: str = "v2"
    effective_version: str = "v2"

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("a successful selection must contain an item")


def null_value() -> CanonicalValue:
    """Return canonical null."""

    return CanonicalValue("null", None)


def boolean_value(value: bool) -> CanonicalValue:
    """Return canonical Boolean."""

    return CanonicalValue("boolean", value)


def integer_value(value: int) -> CanonicalValue:
    """Return canonical arbitrary-precision integer."""

    return CanonicalValue("integer", str(value))


def decimal_value(value: Decimal) -> CanonicalValue:
    """Return canonical finite base-ten coefficient and exponent."""

    if not value.is_finite():
        raise ValueError("canonical decimals must be finite")
    sign, digits, exponent = value.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        coefficient = -coefficient
    if coefficient == 0:
        coefficient = 0
    return CanonicalValue(
        "decimal",
        {"coefficient": str(coefficient), "exponent": int(exponent)},
    )


def binary_float_value(bits: int, raw: bytes) -> CanonicalValue:
    """Return one exact IEEE-style binary-float bit pattern."""

    if bits not in {16, 32, 64, 128} or len(raw) * 8 != bits:
        raise ValueError("binary-float width and bytes disagree")
    return CanonicalValue(
        "binary_float",
        raw.hex(),
        (("bits", bits),),
    )


def string_value(value: str) -> CanonicalValue:
    """Return exact Unicode string value without normalization."""

    return CanonicalValue("string", value)


def bytes_value(value: bytes) -> CanonicalValue:
    """Return exact byte content."""

    return CanonicalValue("bytes", value)


def array_value(
    values: Sequence[CanonicalValue], *, shape: Sequence[int], dtype: str
) -> CanonicalValue:
    """Return one row-major canonical array."""

    return CanonicalValue(
        "array",
        tuple(values),
        (("dtype", dtype), ("order", "C"), ("shape", list(shape))),
    )


def source_content_identity(payload: bytes) -> str:
    """Return the retained-byte content identity."""

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def selection_dependency(
    *, source_identity: str, locator_identity: str, items: Sequence[SelectionItem]
) -> str:
    """Return a stable selected dependency projection."""

    return hashlib.sha256(
        canonical_json(
            {
                "items": [item.identity for item in items],
                "locator": locator_identity,
                "source": source_identity,
            }
        ).encode("utf-8")
    ).hexdigest()


def _project_compound(value: object) -> object:
    if isinstance(value, CanonicalValue):
        return value.projection
    if isinstance(value, Mapping):
        return {
            str(key): _project_compound(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_project_compound(item) for item in value]
    return value


def _binary_float_nan(value: CanonicalValue) -> bool:
    metadata = dict(value.metadata)
    bits = metadata.get("bits")
    if not isinstance(bits, int) or not isinstance(value.value, str):
        return False
    raw = int(value.value, 16)
    if bits == 16:
        exponent_bits, fraction_bits = 5, 10
    elif bits == 32:
        exponent_bits, fraction_bits = 8, 23
    elif bits == 64:
        exponent_bits, fraction_bits = 11, 52
    elif bits == 128:
        exponent_bits, fraction_bits = 15, 112
    else:
        return False
    exponent = (raw >> fraction_bits) & ((1 << exponent_bits) - 1)
    fraction = raw & ((1 << fraction_bits) - 1)
    return exponent == (1 << exponent_bits) - 1 and fraction != 0
