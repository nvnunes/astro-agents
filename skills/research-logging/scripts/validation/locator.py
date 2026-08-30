"""Bounded v2 locator parsing and initial source-profile evaluation."""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import re
import sys
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, Sequence, cast

from .json_codec import V2JsonError, canonical_json, decode_json
from .mechanical_values import (
    CanonicalValue,
    SelectionItem,
    SelectionResult,
    array_value,
    binary_float_value,
    boolean_value,
    bytes_value,
    decimal_value,
    integer_value,
    null_value,
    selection_dependency,
    source_content_identity,
    string_value,
)

MAX_LOCATOR_BYTES = 8 * 1024
MAX_PATHS = 256
MAX_CONDITIONS = 64
MAX_ALTERNATIVES = 256
MAX_IDENTITIES = 256
MAX_RECORDS = 100_000
MAX_SELECTED_ITEMS = 10_000
MAX_TEXT_OR_JSON_BYTES = 64 * 1024 * 1024
MAX_BINARY_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BINARY_MEMBER_OVERHEAD_BYTES = 1024 * 1024
INTEGER_TEXT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
DECIMAL_TEXT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
SHAPE_PROPERTY_RE = re.compile(r"shape(?:\[(?P<index>0|[1-9][0-9]*)\])?\Z")
HDF_SIGNATURE = b"\x89HDF\r\n\x1a\n"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class LocatorV2Error(ValueError):
    """A precise stable v2 locator failure or unavailable observation."""

    def __init__(
        self,
        code: str,
        subject: str,
        observed: object,
        rule: str,
        *,
        outcome: str = "fail",
    ):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule
        self.outcome = outcome


@dataclass(frozen=True)
class ParsedLocator:
    """Validated locator plus canonical authored identity."""

    value: Mapping[str, Any]
    identity: str


@dataclass(frozen=True)
class SourceObservation:
    """One stable retained-source observation reusable across locators."""

    path: Path
    payload: bytes
    profile: str
    source_identity: str
    file_observation: tuple[int, int, int, int, int]
    identity_reused: bool = False


@dataclass(frozen=True)
class _Node:
    coordinate: tuple[object, ...]
    value: object
    inherent_identity: bool = False


@dataclass(frozen=True)
class _Candidate:
    node: _Node
    source_record: int


@dataclass(frozen=True)
class _EvaluationContext:
    locator: ParsedLocator
    profile: str
    source_identity: str
    collection_properties: Mapping[str, object]
    inherent_identity: bool


def parse_locator(locator: Mapping[str, Any]) -> ParsedLocator:
    """Validate and canonicalize one embedded v2 locator object."""

    if not isinstance(locator, Mapping):
        _fail("locator.syntax.invalid", "locator", {"type": type(locator).__name__})
    value = locator
    allowed = {"expect", "identity", "path", "property", "select", "text", "where"}
    if not value or not set(value) <= allowed:
        _fail("locator.syntax.invalid", "locator", {"fields": sorted(value)})
    if "text" in value and set(value) - {"text", "expect"}:
        _fail("locator.syntax.invalid", "locator", {"text_conflicts": sorted(value)})
    normalized = _normalized_locator_fields(value)
    if "expect" in value:
        normalized["expect"] = _expectation(value["expect"], normalized.get("identity"))
    _validate_locator_relationships(normalized)
    try:
        identity = "v2:" + canonical_json(normalized)
    except V2JsonError as exc:
        _fail("locator.syntax.invalid", "locator", {"error": str(exc)})
    if len(identity.encode("utf-8")) > MAX_LOCATOR_BYTES:
        _fail(
            "locator.encoding.too_large",
            "locator",
            {"bytes": len(identity.encode("utf-8")), "limit": MAX_LOCATOR_BYTES},
        )
    return ParsedLocator(value=normalized, identity=identity)


def _normalized_locator_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    parsers: Mapping[str, Callable[[object], object]] = {
        "identity": lambda item: _path_list(item, "locator.identity"),
        "path": lambda item: _path(item, "locator.path"),
        "property": _property,
        "select": lambda item: _path_list(item, "locator.select"),
        "text": _text_selector,
        "where": _conditions,
    }
    return {key: parser(value[key]) for key, parser in parsers.items() if key in value}


def _path_list(value: object, subject: str) -> list[list[object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PATHS:
        _fail("locator.syntax.invalid", subject, {"value": value})
    paths = [_path(path, f"{subject}[{index}]") for index, path in enumerate(value)]
    encoded = [canonical_json(path) for path in paths]
    if len(encoded) != len(set(encoded)):
        _fail("locator.syntax.invalid", subject, {"reason": "duplicate"})
    return paths


def _property(value: object) -> str:
    supported = {"columns", "dtype", "member_count", "members", "row_count", "size"}
    if not isinstance(value, str) or (
        SHAPE_PROPERTY_RE.fullmatch(value) is None and value not in supported
    ):
        _fail("locator.syntax.invalid", "locator.property", {"value": value})
    return value


def _validate_locator_relationships(value: Mapping[str, Any]) -> None:
    if "where" in value and "path" not in value and "select" not in value:
        _fail("locator.syntax.invalid", "locator.where", {"reason": "no_candidate_set"})


def evaluate_locator(
    source: Path,
    locator: Mapping[str, Any],
    *,
    declared_profile: str | None = None,
) -> SelectionResult:
    """Evaluate one v2 locator against one stable retained source."""

    observation = observe_source(source, declared_profile=declared_profile)
    return evaluate_observed_locator(observation, locator)


def observe_source(
    source: Path,
    *,
    declared_profile: str | None = None,
    trusted_identity: Mapping[str, object] | None = None,
) -> SourceObservation:
    """Read, classify, and identify one stable retained source exactly once."""

    if source.is_symlink():
        _fail("locator.source.unsafe", str(source), {"reason": "symlink"})
    source = source.resolve()
    if not source.is_file():
        _fail("locator.path.unresolved", str(source), {"regular_file": False})
    before = _file_observation(source)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        _fail(
            "locator.reader.unavailable",
            str(source),
            {"error": str(exc)},
            outcome="unavailable",
        )
    profile = _classify_source(source, payload, declared_profile)
    reused_identity = _trusted_source_identity(trusted_identity, before)
    observation = SourceObservation(
        path=source,
        payload=payload,
        profile=profile,
        source_identity=(
            f"sha256:{reused_identity}"
            if reused_identity is not None
            else source_content_identity(payload)
        ),
        file_observation=before,
        identity_reused=reused_identity is not None,
    )
    _require_unchanged(observation)
    return observation


def evaluate_observed_locator(
    observation: SourceObservation, locator: Mapping[str, Any]
) -> SelectionResult:
    """Evaluate one locator against a previously established source observation."""

    parsed = parse_locator(locator)
    _require_unchanged(observation)
    source = observation.path
    payload = observation.payload
    profile = observation.profile
    source_identity = observation.source_identity
    if profile == "csv" or profile == "tsv":
        result = _evaluate_record_table(payload, parsed, profile, source_identity)
    elif profile == "json":
        result = _evaluate_json(payload, parsed, source_identity)
    elif profile == "npz":
        result = _evaluate_npz(source, payload, parsed, source_identity)
    elif profile == "hdf5":
        result = _evaluate_hdf5(source, payload, parsed, source_identity)
    elif profile == "text":
        result = _evaluate_text(payload, parsed, source_identity)
    else:
        _fail("locator.source.unsupported", str(source), {"profile": profile})
    _require_unchanged(observation)
    return result


def authored_literal(value: object, subject: str = "literal") -> CanonicalValue:
    """Decode one strict v2 authored literal to the common value model."""

    if value is None:
        return null_value()
    primitives: Mapping[type[object], Callable[[Any], CanonicalValue]] = {
        bool: boolean_value,
        int: integer_value,
        Decimal: decimal_value,
        str: string_value,
    }
    handler = primitives.get(type(value))
    if handler is not None:
        return handler(value)
    if not isinstance(value, Mapping):
        _fail("locator.literal.invalid", subject, {"value": value})
    tagged = cast(Mapping[str, Any], value)
    kind = tagged.get("type")
    decoders: Mapping[str, Callable[[Mapping[str, Any], str], CanonicalValue]] = {
        "binary_float": _binary_float_literal,
        "bytes": _bytes_literal,
        "date": _temporal_literal,
        "datetime": _temporal_literal,
        "duration": _unit_literal,
        "quantity": _unit_literal,
        "time": _temporal_literal,
    }
    decoder = decoders.get(kind) if isinstance(kind, str) else None
    if decoder is None:
        _fail("locator.literal.invalid", subject, {"value": tagged})
    return decoder(tagged, subject)


def _binary_float_literal(value: Mapping[str, Any], subject: str) -> CanonicalValue:
    bits = value.get("bits")
    encoded = value.get("hex")
    if (
        set(value) != {"bits", "hex", "type"}
        or not isinstance(bits, int)
        or bits not in {16, 32, 64, 128}
        or not isinstance(encoded, str)
        or encoded != encoded.lower()
        or re.fullmatch(rf"[0-9a-f]{{{bits // 4}}}", encoded) is None
    ):
        _fail("locator.literal.invalid", subject, {"value": value})
    return binary_float_value(bits, bytes.fromhex(encoded))


def _bytes_literal(value: Mapping[str, Any], subject: str) -> CanonicalValue:
    encoded = value.get("base64")
    if set(value) != {"base64", "type"} or not isinstance(encoded, str):
        _fail("locator.literal.invalid", subject, {"value": value})
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        _fail("locator.literal.invalid", subject, {"value": value})
    if base64.b64encode(raw).decode("ascii") != encoded:
        _fail("locator.literal.invalid", subject, {"value": value})
    return bytes_value(raw)


def _temporal_literal(value: Mapping[str, Any], subject: str) -> CanonicalValue:
    if set(value) != {"resolution", "type", "value"} or not all(
        isinstance(value.get(key), str) for key in ("resolution", "type", "value")
    ):
        _fail("locator.literal.invalid", subject, {"value": value})
    return CanonicalValue(
        cast(str, value["type"]),
        value["value"],
        (("resolution", value["resolution"]),),
    )


def _unit_literal(value: Mapping[str, Any], subject: str) -> CanonicalValue:
    unit = value.get("unit")
    kind = value.get("type")
    if set(value) != {"type", "unit", "value"} or not isinstance(unit, str) or not unit:
        _fail("locator.literal.invalid", subject, {"value": value})
    numeric = authored_literal(value["value"], f"{subject}.value")
    if numeric.kind not in {"integer", "decimal", "binary_float"}:
        _fail("locator.literal.invalid", subject, {"value": value})
    return CanonicalValue(cast(str, kind), numeric, (("unit", unit),))


def _path(value: object, subject: str) -> list[object]:
    if not isinstance(value, list) or len(value) > MAX_PATHS:
        _fail("locator.syntax.invalid", subject, {"value": value})
    result: list[object] = []
    for segment in value:
        if (
            isinstance(segment, str)
            or isinstance(segment, int)
            and not isinstance(segment, bool)
            and segment >= 0
        ):
            result.append(segment)
            continue
        if (
            isinstance(segment, Mapping)
            and set(segment) == {"all"}
            and segment["all"] is True
        ):
            result.append({"all": True})
            continue
        if isinstance(segment, Mapping) and set(segment) == {"slice"}:
            bounds = segment["slice"]
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(
                    bound is not None
                    and (
                        not isinstance(bound, int)
                        or isinstance(bound, bool)
                        or bound < 0
                    )
                    for bound in bounds
                )
            ):
                _fail("locator.syntax.invalid", subject, {"segment": segment})
            result.append({"slice": list(bounds)})
            continue
        _fail("locator.syntax.invalid", subject, {"segment": segment})
    return result


def _conditions(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_CONDITIONS:
        _fail("locator.syntax.invalid", "locator.where", {"value": value})
    result = [_condition(item, index) for index, item in enumerate(value)]
    return sorted(result, key=canonical_json)


def _condition(value: object, index: int) -> Mapping[str, Any]:
    subject = f"locator.where[{index}]"
    if not isinstance(value, Mapping):
        _fail("locator.syntax.invalid", subject, {"value": value})
    condition = cast(Mapping[str, Any], value)
    op = condition.get("op")
    expected = {"op", "path", "value"} if op == "eq" else {"op", "path", "values"}
    if "parse" in condition:
        expected.add("parse")
    if set(condition) != expected or op not in {"eq", "in"}:
        _fail(
            "locator.syntax.invalid",
            subject,
            {"fields": sorted(condition), "op": op},
        )
    parse = condition.get("parse")
    if parse is not None and parse not in {"decimal", "integer"}:
        _fail("locator.syntax.invalid", subject, {"parse": parse})
    normalized: dict[str, Any] = {
        "op": op,
        "path": _path(condition["path"], subject),
    }
    if parse is not None:
        normalized["parse"] = parse
    normalized.update(_condition_literals(condition, cast(str, op), parse, subject))
    return normalized


def _condition_literals(
    condition: Mapping[str, Any], op: str, parse: object, subject: str
) -> Mapping[str, object]:
    if op == "eq":
        literal = authored_literal(condition["value"], f"{subject}.value")
        _require_parse_type(literal, parse, subject)
        return {"value": _literal_projection(literal)}
    alternatives = condition["values"]
    if (
        not isinstance(alternatives, list)
        or not alternatives
        or len(alternatives) > MAX_ALTERNATIVES
    ):
        _fail(
            "locator.syntax.invalid",
            f"{subject}.values",
            {"value": alternatives},
        )
    literals = [authored_literal(item, f"{subject}.values") for item in alternatives]
    for literal in literals:
        _require_parse_type(literal, parse, subject)
    unique = {literal.identity: literal for literal in literals}
    return {"values": [_literal_projection(unique[key]) for key in sorted(unique)]}


def _expectation(value: object, identity: object) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or not value
        or not set(value)
        <= {
            "identities",
            "items",
            "matches",
            "shape",
        }
    ):
        _fail("locator.syntax.invalid", "locator.expect", {"value": value})
    typed = cast(Mapping[str, Any], value)
    result = _expectation_counts(typed)
    if "shape" in typed:
        result["shape"] = _expectation_shape(typed["shape"])
    if "identities" in typed:
        result["identities"] = _expectation_identities(typed["identities"], identity)
        if "matches" in result and result["matches"] != len(result["identities"]):
            _fail(
                "locator.syntax.invalid",
                "locator.expect",
                {
                    "matches": result["matches"],
                    "identities": len(result["identities"]),
                },
            )
    return result


def _expectation_counts(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("items", "matches"):
        if key in value:
            count = value[key]
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                _fail(
                    "locator.syntax.invalid", f"locator.expect.{key}", {"value": count}
                )
            result[key] = count
    return result


def _expectation_shape(value: object) -> list[int]:
    if not isinstance(value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in value
    ):
        _fail("locator.syntax.invalid", "locator.expect.shape", {"value": value})
    return value


def _expectation_identities(value: object, identity: object) -> list[list[object]]:
    if (
        not isinstance(identity, list)
        or not isinstance(value, list)
        or not value
        or len(value) > MAX_IDENTITIES
    ):
        _fail("locator.syntax.invalid", "locator.expect.identities", {"value": value})
    decoded = [
        _expectation_identity(item, number, len(identity))
        for number, item in enumerate(value)
    ]
    keys = [canonical_json(item) for item in decoded]
    if len(keys) != len(set(keys)):
        _fail(
            "locator.syntax.invalid",
            "locator.expect.identities",
            {"reason": "duplicate"},
        )
    return decoded


def _expectation_identity(value: object, number: int, width: int) -> list[object]:
    subject = f"locator.expect.identities[{number}]"
    if not isinstance(value, list) or len(value) != width:
        _fail("locator.syntax.invalid", subject, {"value": value})
    return [_literal_projection(authored_literal(part, subject)) for part in value]


def _text_selector(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not {"contains"} <= set(value) <= {
        "contains",
        "occurrence",
    }:
        _fail("locator.syntax.invalid", "locator.text", {"value": value})
    contains = value["contains"]
    occurrence = value.get("occurrence")
    if (
        not isinstance(contains, str)
        or not contains
        or occurrence is not None
        and occurrence != "all"
        and (
            not isinstance(occurrence, int)
            or isinstance(occurrence, bool)
            or occurrence <= 0
        )
    ):
        _fail("locator.syntax.invalid", "locator.text", {"value": value})
    return dict(value)


def _evaluate_record_table(
    payload: bytes,
    locator: ParsedLocator,
    profile: str,
    source_identity: str,
) -> SelectionResult:
    if len(payload) > MAX_TEXT_OR_JSON_BYTES:
        _fail("locator.source.too_large", profile, {"bytes": len(payload)})
    if locator.value.get("path", []) != []:
        _fail("locator.syntax.invalid", profile, {"path": locator.value.get("path")})
    if "text" in locator.value:
        _fail("locator.source.unsupported", profile, {"operation": "text"})
    try:
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(
            io.StringIO(text), delimiter="," if profile == "csv" else "\t"
        )
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)):
            _fail("locator.source.format_mismatch", profile, {"headers": headers})
        records = [dict(row) for row in reader]
    except (UnicodeError, csv.Error) as exc:
        _fail("locator.source.format_mismatch", profile, {"error": str(exc)})
    if len(records) > MAX_RECORDS:
        _fail("locator.selection.too_large", profile, {"records": len(records)})
    if any(
        None in row or any(value is None for value in row.values()) for row in records
    ):
        _fail("locator.source.format_mismatch", profile, {"reason": "ragged_record"})
    if "select" not in locator.value and "property" not in locator.value:
        _fail("locator.syntax.invalid", profile, {"select_required": True})
    candidates = [
        _Candidate(_Node((index,), row), index) for index, row in enumerate(records)
    ]
    return _evaluate_candidates(
        candidates,
        _EvaluationContext(
            locator=locator,
            profile=profile,
            source_identity=source_identity,
            collection_properties={"columns": headers, "row_count": len(records)},
            inherent_identity=False,
        ),
    )


def _evaluate_json(
    payload: bytes, locator: ParsedLocator, source_identity: str
) -> SelectionResult:
    if len(payload) > MAX_TEXT_OR_JSON_BYTES:
        _fail("locator.source.too_large", "json", {"bytes": len(payload)})
    if "path" not in locator.value or "text" in locator.value:
        _fail("locator.syntax.invalid", "json", {"path_required": True})
    try:
        root = decode_json(
            payload.decode("utf-8"),
            maximum_bytes=MAX_TEXT_OR_JSON_BYTES,
            subject="JSON source",
        )
    except (UnicodeError, V2JsonError) as exc:
        _fail("locator.source.format_mismatch", "json", {"error": str(exc)})
    nodes = _resolve_nodes(
        [_Node((), root)], locator.value["path"], "locator.path.unresolved"
    )
    candidates = _candidate_nodes(nodes, locator.value)
    return _evaluate_candidates(
        candidates,
        _EvaluationContext(
            locator=locator,
            profile="json",
            source_identity=source_identity,
            collection_properties={},
            inherent_identity=all(
                candidate.node.inherent_identity for candidate in candidates
            ),
        ),
    )


def _evaluate_npz(
    source: Path, payload: bytes, locator: ParsedLocator, source_identity: str
) -> SelectionResult:
    _preflight_npz(source, payload)
    try:
        import numpy as np
    except ImportError:
        _fail(
            "locator.reader.unavailable",
            str(source),
            {"reader": "numpy"},
            outcome="unavailable",
        )
    try:
        with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
            members = sorted(archive.files)
            arrays = {name: archive[name] for name in members}
    except ValueError as exc:
        code = (
            "locator.source.unsafe"
            if "Object arrays" in str(exc)
            else "locator.source.format_mismatch"
        )
        _fail(code, str(source), {"error": str(exc)})
    except (OSError, zipfile.BadZipFile) as exc:
        _fail("locator.source.format_mismatch", str(source), {"error": str(exc)})
    for name, array in arrays.items():
        if array.dtype.hasobject:
            _fail("locator.source.unsafe", name, {"dtype": str(array.dtype)})
        if array.nbytes > MAX_BINARY_MEMBER_BYTES:
            _fail("locator.source.too_large", name, {"bytes": int(array.nbytes)})
    return _evaluate_array_container(arrays, locator, "npz", source_identity)


def _preflight_npz(source: Path, payload: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
    except (OSError, zipfile.BadZipFile) as exc:
        _fail("locator.source.format_mismatch", str(source), {"error": str(exc)})
    if len(members) > MAX_RECORDS:
        _fail("locator.source.too_large", str(source), {"members": len(members)})
    names = [item.filename for item in members]
    if len(names) != len(set(names)):
        _fail(
            "locator.source.format_mismatch",
            str(source),
            {"duplicate_members": True},
        )
    staging_limit = MAX_BINARY_MEMBER_BYTES + MAX_BINARY_MEMBER_OVERHEAD_BYTES
    for member in members:
        if member.flag_bits & 0x1:
            _fail("locator.source.unsafe", member.filename, {"encrypted": True})
        if member.file_size > staging_limit:
            _fail(
                "locator.source.too_large",
                member.filename,
                {"bytes": member.file_size, "staging_limit": staging_limit},
            )


def _evaluate_hdf5(
    source: Path, payload: bytes, locator: ParsedLocator, source_identity: str
) -> SelectionResult:
    try:
        import h5py
    except ImportError:
        _fail(
            "locator.reader.unavailable",
            str(source),
            {"reader": "h5py"},
            outcome="unavailable",
        )
    try:
        with h5py.File(io.BytesIO(payload), "r") as handle:
            _reject_hdf_links(handle)
            root = _hdf_tree(handle)
    except LocatorV2Error:
        raise
    except (OSError, ValueError) as exc:
        _fail("locator.source.format_mismatch", str(source), {"error": str(exc)})
    return _evaluate_array_container(root, locator, "hdf5", source_identity)


def _evaluate_array_container(
    root: Mapping[str, object],
    locator: ParsedLocator,
    profile: str,
    source_identity: str,
) -> SelectionResult:
    if "path" not in locator.value or "text" in locator.value:
        _fail("locator.syntax.invalid", profile, {"path_required": True})
    nodes = _resolve_nodes(
        [_Node((), root)], locator.value["path"], "locator.path.unresolved"
    )
    candidates = _aligned_candidates(nodes, locator.value)
    properties = {"member_count": len(root), "members": sorted(root)}
    return _evaluate_candidates(
        candidates,
        _EvaluationContext(
            locator=locator,
            profile=profile,
            source_identity=source_identity,
            collection_properties=properties,
            inherent_identity=all(
                candidate.node.inherent_identity for candidate in candidates
            ),
        ),
    )


def _evaluate_text(
    payload: bytes, locator: ParsedLocator, source_identity: str
) -> SelectionResult:
    if len(payload) > MAX_TEXT_OR_JSON_BYTES:
        _fail("locator.source.too_large", "text", {"bytes": len(payload)})
    if set(locator.value) - {"expect", "text"} or "text" not in locator.value:
        _fail("locator.syntax.invalid", "text", {"fields": sorted(locator.value)})
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        _fail("locator.text.decode", "text", {"error": str(exc)})
    selector = locator.value["text"]
    matches = [
        (index, line)
        for index, line in enumerate(lines)
        if selector["contains"] in line
    ]
    match_count = len(matches)
    occurrence = selector.get("occurrence")
    if occurrence == "all":
        selected = matches
    elif isinstance(occurrence, int):
        selected = matches[occurrence - 1 : occurrence]
    elif match_count == 1:
        selected = matches
    else:
        _fail("locator.selection.ambiguous", "text", {"matches": match_count})
    if not selected:
        _fail("locator.selection.empty", "text", {"matches": match_count})
    items = tuple(
        SelectionItem(
            coordinate=(
                "match",
                rank,
                hashlib.sha256(line.encode("utf-8")).hexdigest(),
            ),
            value=string_value(line),
        )
        for rank, (_, line) in enumerate(selected, 1)
    )
    _check_expectations(locator, match_count, items, (), None)
    context = _EvaluationContext(locator, "text", source_identity, {}, True)
    return _selection_result(context, items, match_count, (), None)


def _evaluate_candidates(
    candidates: Sequence[_Candidate],
    context: _EvaluationContext,
) -> SelectionResult:
    matched = _matched_candidates(candidates, context.locator)
    _require_candidate_identity(matched, context)
    identities = _record_identities(matched, context.locator.value.get("identity"))
    items = _candidate_items(matched, context)
    if not items:
        _fail("locator.selection.empty", context.profile, {"matches": len(matched)})
    if len(items) > MAX_SELECTED_ITEMS:
        _fail("locator.selection.too_large", context.profile, {"items": len(items)})
    shape = _selected_shape(items)
    _check_expectations(context.locator, len(matched), items, identities, shape)
    return _selection_result(context, tuple(items), len(matched), identities, shape)


def _matched_candidates(
    candidates: Sequence[_Candidate], locator: ParsedLocator
) -> list[_Candidate]:
    matched = [
        candidate for candidate in candidates if _candidate_matches(candidate, locator)
    ]
    if not matched and "property" not in locator.value:
        _fail("locator.selection.empty", "candidates", {"matches": 0})
    return matched


def _candidate_matches(candidate: _Candidate, locator: ParsedLocator) -> bool:
    decisions = tuple(
        _condition_matches(candidate.node, condition)
        for condition in locator.value.get("where", [])
    )
    return all(decisions)


def _require_candidate_identity(
    matched: Sequence[_Candidate], context: _EvaluationContext
) -> None:
    declared = context.locator.value.get("identity")
    collection_property = context.locator.value.get("property") in {
        "columns",
        "member_count",
        "members",
        "row_count",
    }
    if (
        len(matched) > 1
        and not declared
        and not context.inherent_identity
        and not collection_property
    ):
        _fail(
            "locator.selection.ambiguous",
            context.profile,
            {"matches": len(matched), "identity": False},
        )


def _candidate_items(
    matched: Sequence[_Candidate], context: _EvaluationContext
) -> list[SelectionItem]:
    property_name = context.locator.value.get("property")
    if property_name in {
        "columns",
        "row_count",
        "members",
        "member_count",
    } and not context.locator.value.get("select"):
        return [_collection_property_item(cast(str, property_name), matched, context)]
    return [
        SelectionItem(
            coordinate=node.coordinate,
            value=canonical_source_value(node.value),
            record=record_number,
        )
        for record_number, candidate in enumerate(matched)
        for node in _selected_candidate_nodes(candidate.node, context)
    ]


def _collection_property_item(
    property_name: str,
    matched: Sequence[_Candidate],
    context: _EvaluationContext,
) -> SelectionItem:
    if property_name not in context.collection_properties:
        _fail(
            "locator.property.unsupported",
            context.profile,
            {"property": property_name},
        )
    value = (
        len(matched)
        if property_name == "row_count"
        else context.collection_properties[property_name]
    )
    return SelectionItem(("property", property_name), canonical_source_value(value))


def _selected_candidate_nodes(node: _Node, context: _EvaluationContext) -> list[_Node]:
    select = context.locator.value.get("select")
    nodes = (
        [
            selected
            for path in select
            for selected in _resolve_nodes([node], path, "locator.field.missing")
        ]
        if select
        else [node]
    )
    property_name = context.locator.value.get("property")
    if property_name is None:
        return nodes
    return [
        _property_node(selected, cast(str, property_name), context.profile)
        for selected in nodes
    ]


def _resolve_nodes(
    nodes: Sequence[_Node], path: Sequence[object], code: str
) -> list[_Node]:
    current = list(nodes)
    for segment in path:
        expanded: list[_Node] = []
        for node in current:
            try:
                expanded.extend(_segment(node, segment))
            except (IndexError, KeyError, TypeError, ValueError):
                _fail(code, canonical_json(list(node.coordinate)), {"segment": segment})
        current = expanded
        if len(current) > MAX_SELECTED_ITEMS:
            _fail("locator.selection.too_large", "path", {"nodes": len(current)})
    return current


def _segment(node: _Node, segment: object) -> list[_Node]:
    value = node.value
    if isinstance(segment, str):
        if not isinstance(value, Mapping) or segment not in value:
            raise KeyError(segment)
        return [
            _Node((*node.coordinate, segment), value[segment], node.inherent_identity)
        ]
    if isinstance(segment, int):
        if not _sequence_like(value):
            raise IndexError(segment)
        sequence = cast(Any, value)
        if segment >= len(sequence):
            raise IndexError(segment)
        return [_Node((*node.coordinate, segment), sequence[segment], True)]
    assert isinstance(segment, Mapping)
    if "all" in segment:
        if isinstance(value, Mapping):
            return [
                _Node((*node.coordinate, key), value[key], True)
                for key in sorted(value, key=lambda item: canonical_json(str(item)))
            ]
        if _sequence_like(value):
            sequence = cast(Any, value)
            return [
                _Node((*node.coordinate, index), sequence[index], True)
                for index in range(len(sequence))
            ]
        raise TypeError("all")
    bounds = segment["slice"]
    if not _sequence_like(value):
        raise TypeError("slice")
    sequence = cast(Any, value)
    start, stop = bounds
    indexes = range(len(sequence))[slice(start, stop)]
    return [
        _Node((*node.coordinate, index), sequence[index], True) for index in indexes
    ]


def _candidate_nodes(
    nodes: Sequence[_Node], locator: Mapping[str, Any]
) -> list[_Candidate]:
    record_ops = any(key in locator for key in ("identity", "select", "where"))
    expanded: list[_Node] = []
    for node in nodes:
        if record_ops and isinstance(node.value, list):
            expanded.extend(
                _Node((*node.coordinate, index), item, True)
                for index, item in enumerate(node.value)
            )
        else:
            expanded.append(node)
    if len(expanded) > MAX_RECORDS:
        _fail("locator.selection.too_large", "records", {"records": len(expanded)})
    return [_Candidate(node, index) for index, node in enumerate(expanded)]


def _aligned_candidates(
    nodes: Sequence[_Node], locator: Mapping[str, Any]
) -> list[_Candidate]:
    if len(nodes) != 1 or not isinstance(nodes[0].value, Mapping):
        return _candidate_nodes(nodes, locator)
    mapping = nodes[0].value
    paths = [
        *locator.get("select", []),
        *[condition["path"] for condition in locator.get("where", [])],
        *locator.get("identity", []),
    ]
    top_fields = {path[0] for path in paths if path and isinstance(path[0], str)}
    if not top_fields:
        return _candidate_nodes(nodes, locator)
    arrays = {
        field: mapping[field]
        for field in top_fields
        if field in mapping and _sequence_like(mapping[field])
    }
    if set(arrays) != top_fields:
        _fail(
            "locator.field.missing",
            "aligned arrays",
            {"fields": sorted(top_fields - set(arrays))},
        )
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        _fail(
            "locator.alignment.invalid", "aligned arrays", {"lengths": sorted(lengths)}
        )
    count = next(iter(lengths))
    if count > MAX_RECORDS:
        _fail("locator.selection.too_large", "aligned arrays", {"records": count})
    candidates = []
    for index in range(count):
        row = {field: value[index] for field, value in arrays.items()}
        candidates.append(
            _Candidate(_Node((*nodes[0].coordinate, index), row, True), index)
        )
    return candidates


def _condition_matches(node: _Node, condition: Mapping[str, Any]) -> bool:
    selected = _resolve_nodes([node], condition["path"], "locator.field.missing")
    if len(selected) != 1:
        _fail(
            "locator.type.mismatch",
            canonical_json(list(node.coordinate)),
            {"condition_items": len(selected)},
        )
    observed = canonical_source_value(selected[0].value)
    parse = condition.get("parse")
    if parse is not None:
        if observed.kind != "string" or not isinstance(observed.value, str):
            _fail(
                "locator.type.mismatch",
                canonical_json(list(node.coordinate)),
                {"parse": parse, "type": observed.kind},
            )
        observed = _parse_lexical(observed.value, parse, node.coordinate)
    if condition["op"] == "eq":
        return observed.typed_equal(authored_literal(condition["value"]))
    return any(
        observed.typed_equal(authored_literal(value)) for value in condition["values"]
    )


def _record_identities(
    candidates: Sequence[_Candidate], paths: object
) -> tuple[tuple[CanonicalValue, ...], ...]:
    if not paths:
        return ()
    assert isinstance(paths, list)
    identities: list[tuple[CanonicalValue, ...]] = []
    for candidate in candidates:
        values: list[CanonicalValue] = []
        for path in paths:
            nodes = _resolve_nodes([candidate.node], path, "locator.field.missing")
            if len(nodes) != 1:
                _fail("locator.type.mismatch", "identity", {"items": len(nodes)})
            value = canonical_source_value(nodes[0].value)
            if value.kind in {"array", "mapping", "record", "table"}:
                _fail("locator.type.mismatch", "identity", {"type": value.kind})
            values.append(value)
        identities.append(tuple(values))
    keys = [
        canonical_json([value.projection for value in identity])
        for identity in identities
    ]
    if len(keys) != len(set(keys)):
        _fail("locator.identity.duplicate", "identity", {"identities": keys})
    return tuple(identities)


def _check_expectations(
    locator: ParsedLocator,
    matches: int,
    items: Sequence[SelectionItem],
    identities: Sequence[Sequence[CanonicalValue]],
    shape: tuple[int, ...] | None,
) -> None:
    expect = locator.value.get("expect", {})
    if "matches" in expect and expect["matches"] != matches:
        _fail(
            "locator.expectation.mismatch",
            "expect.matches",
            {"expected": expect["matches"], "observed": matches},
        )
    if "items" in expect and expect["items"] != len(items):
        _fail(
            "locator.expectation.mismatch",
            "expect.items",
            {"expected": expect["items"], "observed": len(items)},
        )
    if "shape" in expect and tuple(expect["shape"]) != shape:
        _fail(
            "locator.expectation.mismatch",
            "expect.shape",
            {"expected": expect["shape"], "observed": shape},
        )
    if "identities" in expect:
        expected = tuple(
            tuple(authored_literal(value) for value in identity)
            for identity in expect["identities"]
        )
        observed_keys = [
            canonical_json([value.projection for value in identity])
            for identity in identities
        ]
        expected_keys = [
            canonical_json([value.projection for value in identity])
            for identity in expected
        ]
        if observed_keys != expected_keys:
            _fail(
                "locator.identity.expectation_mismatch",
                "expect.identities",
                {"expected": expected_keys, "observed": observed_keys},
            )


def _selection_result(
    context: _EvaluationContext,
    items: tuple[SelectionItem, ...],
    matches: int,
    identities: tuple[tuple[CanonicalValue, ...], ...],
    shape: tuple[int, ...] | None,
) -> SelectionResult:
    if identities:
        membership = tuple(
            canonical_json([value.projection for value in identity])
            for identity in identities
        )
    else:
        membership = tuple(
            dict.fromkeys(canonical_json(list(item.coordinate)) for item in items)
        )
    dependency = selection_dependency(
        source_identity=context.source_identity,
        locator_identity=context.locator.identity,
        items=items,
    )
    return SelectionResult(
        locator_identity=context.locator.identity,
        source_identity=context.source_identity,
        source_profile=context.profile,
        items=items,
        matches=matches,
        membership=membership,
        identities=identities,
        shape=shape,
        dependency_projection=dependency,
    )


def _property_node(node: _Node, property_name: str, profile: str) -> _Node:
    value = node.value
    match = SHAPE_PROPERTY_RE.fullmatch(property_name)
    shape = _native_shape(value)
    if match is not None:
        if shape is None:
            _fail("locator.property.unsupported", profile, {"property": property_name})
        index = match.group("index")
        selected: object = (
            list(shape)
            if index is None
            else shape[int(index)]
            if int(index) < len(shape)
            else None
        )
        if selected is None:
            _fail(
                "locator.property.unsupported",
                profile,
                {"property": property_name, "shape": shape},
            )
        return _Node((*node.coordinate, "property", property_name), selected)
    if property_name == "size" and shape is not None:
        size = 1
        for dimension in shape:
            size *= dimension
        return _Node((*node.coordinate, "property", property_name), size)
    if property_name == "dtype" and hasattr(value, "dtype"):
        return _Node((*node.coordinate, "property", property_name), str(value.dtype))
    if property_name == "member_count" and isinstance(value, Mapping):
        return _Node((*node.coordinate, "property", property_name), len(value))
    _fail("locator.property.unsupported", profile, {"property": property_name})


def canonical_source_value(value: object) -> CanonicalValue:
    """Map one decoded supported source value to the common value model."""

    primitive = _primitive_source_value(value)
    if primitive is not None:
        return primitive
    numpy_value = _numpy_source_value(value)
    if numpy_value is not None:
        return numpy_value
    compound = _compound_source_value(value)
    if compound is not None:
        return compound
    _fail("locator.type.mismatch", "value", {"type": type(value).__name__})


def _primitive_source_value(value: object) -> CanonicalValue | None:
    if value is None:
        return null_value()
    handlers: Mapping[type[object], Callable[[Any], CanonicalValue]] = {
        bool: boolean_value,
        bytes: bytes_value,
        Decimal: decimal_value,
        int: integer_value,
        str: string_value,
    }
    handler = handlers.get(type(value))
    return handler(value) if handler is not None else None


def _numpy_source_value(value: object) -> CanonicalValue | None:
    np = _numpy_module()
    if np is None:
        return None
    if isinstance(value, np.bool_):
        return boolean_value(bool(value))
    if isinstance(value, np.integer):
        return integer_value(int(value))
    if isinstance(value, np.floating):
        dtype = value.dtype
        raw = value.tobytes()
        if (
            dtype.byteorder == "<"
            or dtype.byteorder == "="
            and sys.byteorder == "little"
        ):
            raw = raw[::-1]
        return binary_float_value(dtype.itemsize * 8, raw)
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            _fail("locator.source.unsafe", "array", {"dtype": str(value.dtype)})
        values = tuple(canonical_source_value(item) for item in value.reshape(-1))
        return array_value(values, shape=value.shape, dtype=str(value.dtype))
    return None


def _compound_source_value(value: object) -> CanonicalValue | None:
    if isinstance(value, Mapping):
        ordered = {
            str(key): canonical_source_value(value[key])
            for key in sorted(value, key=str)
        }
        return CanonicalValue("mapping", ordered)
    if isinstance(value, (list, tuple)):
        values = tuple(canonical_source_value(item) for item in value)
        return array_value(values, shape=(len(values),), dtype="mixed")
    return None


def _selected_shape(items: Sequence[SelectionItem]) -> tuple[int, ...] | None:
    if len(items) != 1 or items[0].value.kind != "array":
        return None
    shape = dict(items[0].value.metadata).get("shape")
    return tuple(shape) if isinstance(shape, list) else None


def _native_shape(value: object) -> tuple[int, ...] | None:
    if hasattr(value, "shape"):
        return tuple(int(item) for item in value.shape)
    if isinstance(value, (list, tuple)):
        return (len(value),)
    return None


def _sequence_like(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        or hasattr(value, "shape")
        and hasattr(value, "__getitem__")
    )


def _numpy_module() -> Any:
    try:
        import numpy
    except ImportError:
        return None
    return numpy


def _parse_lexical(
    value: str, parse: str, coordinate: tuple[object, ...]
) -> CanonicalValue:
    if parse == "integer" and INTEGER_TEXT_RE.fullmatch(value):
        return integer_value(int(value))
    if parse == "decimal" and DECIMAL_TEXT_RE.fullmatch(value):
        try:
            return decimal_value(Decimal(value))
        except InvalidOperation:
            pass
    _fail(
        "locator.predicate.parse_failed",
        canonical_json(list(coordinate)),
        {"parse": parse, "value": value},
    )


def _literal_projection(value: CanonicalValue) -> object:
    if value.kind == "null":
        return None
    if value.kind in {"boolean", "string"}:
        return value.value
    if value.kind == "integer":
        return int(cast(str, value.value))
    projectors: Mapping[str, Callable[[CanonicalValue], object]] = {
        "binary_float": _project_binary_float,
        "bytes": _project_bytes,
        "date": _project_temporal,
        "datetime": _project_temporal,
        "decimal": _project_decimal,
        "duration": _project_unit,
        "quantity": _project_unit,
        "time": _project_temporal,
    }
    projector = projectors.get(value.kind)
    if projector is None:
        raise ValueError(f"not an authored literal: {value.kind}")
    return projector(value)


def _project_decimal(value: CanonicalValue) -> Decimal:
    parts = cast(Mapping[str, str | int], value.value)
    return Decimal(cast(str, parts["coefficient"])).scaleb(cast(int, parts["exponent"]))


def _project_bytes(value: CanonicalValue) -> Mapping[str, object]:
    assert isinstance(value.value, bytes)
    return {
        "base64": base64.b64encode(value.value).decode("ascii"),
        "type": "bytes",
    }


def _project_binary_float(value: CanonicalValue) -> Mapping[str, object]:
    return {
        "bits": dict(value.metadata)["bits"],
        "hex": value.value,
        "type": "binary_float",
    }


def _project_temporal(value: CanonicalValue) -> Mapping[str, object]:
    return {
        "resolution": dict(value.metadata)["resolution"],
        "type": value.kind,
        "value": value.value,
    }


def _project_unit(value: CanonicalValue) -> Mapping[str, object]:
    assert isinstance(value.value, CanonicalValue)
    return {
        "type": value.kind,
        "unit": dict(value.metadata)["unit"],
        "value": _literal_projection(value.value),
    }


def _require_parse_type(value: CanonicalValue, parse: object, subject: str) -> None:
    expected = (
        "integer" if parse == "integer" else "decimal" if parse == "decimal" else None
    )
    if expected is not None and value.kind != expected:
        _fail("locator.literal.invalid", subject, {"parse": parse, "type": value.kind})


def _classify_source(source: Path, payload: bytes, declared: str | None) -> str:
    suffix = source.suffix.lower()
    if payload.startswith(HDF_SIGNATURE):
        observed = "hdf5"
    elif payload.startswith(ZIP_SIGNATURES) and suffix == ".npz":
        observed = "npz"
    elif suffix == ".json":
        observed = "json"
    elif suffix == ".csv":
        observed = "csv"
    elif suffix == ".tsv":
        observed = "tsv"
    elif suffix in {".log", ".txt"}:
        observed = "text"
    elif suffix in {".pkl", ".pickle"}:
        _fail("locator.source.unsafe", str(source), {"suffix": suffix})
    else:
        _fail("locator.source.unsupported", str(source), {"suffix": suffix})
    if declared is not None and declared != observed:
        _fail(
            "locator.source.format_mismatch",
            str(source),
            {"declared": declared, "observed": observed},
        )
    return observed


def _reject_hdf_links(
    group: Any, seen_groups: set[int] | None = None, nodes: list[int] | None = None
) -> None:
    import h5py

    seen_groups = set() if seen_groups is None else seen_groups
    nodes = [0] if nodes is None else nodes
    address = int(h5py.h5o.get_info(group.id).addr)
    if address in seen_groups:
        _fail("locator.source.unsafe", group.name, {"reason": "group_alias"})
    seen_groups.add(address)
    for name in group.keys():
        nodes[0] += 1
        if nodes[0] > MAX_RECORDS:
            _fail("locator.source.too_large", group.name, {"nodes": nodes[0]})
        link = group.get(name, getlink=True)
        if isinstance(link, (h5py.ExternalLink, h5py.SoftLink)):
            _fail("locator.source.unsafe", name, {"link": type(link).__name__})
        item = group.get(name)
        if isinstance(item, h5py.Group):
            _reject_hdf_links(item, seen_groups, nodes)


def _hdf_tree(group: Any) -> Mapping[str, object]:
    import h5py

    result: dict[str, object] = {}
    for name in sorted(group.keys()):
        item = group[name]
        if isinstance(item, h5py.Group):
            result[name] = _hdf_tree(item)
        else:
            if item.size * item.dtype.itemsize > MAX_BINARY_MEMBER_BYTES:
                _fail(
                    "locator.source.too_large",
                    item.name,
                    {"bytes": item.size * item.dtype.itemsize},
                )
            if item.dtype.hasobject:
                _fail("locator.source.unsafe", item.name, {"dtype": str(item.dtype)})
            result[name] = item[()]
    return result


def _file_observation(source: Path) -> tuple[int, int, int, int, int]:
    try:
        stat = source.stat()
    except OSError as exc:
        _fail(
            "locator.reader.unavailable",
            str(source),
            {"error": str(exc)},
            outcome="unavailable",
        )
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _trusted_source_identity(
    value: Mapping[str, object] | None,
    observation: tuple[int, int, int, int, int],
) -> str | None:
    if not isinstance(value, Mapping) or set(value) != {
        "ctime_ns",
        "mtime_ns",
        "sha256",
        "size",
    }:
        return None
    _device, _inode, size, mtime_ns, ctime_ns = observation
    digest = value.get("sha256")
    if (
        value.get("size") != size
        or value.get("mtime_ns") != mtime_ns
        or value.get("ctime_ns") != ctime_ns
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        return None
    return digest


def _require_unchanged(observation: SourceObservation) -> None:
    if _file_observation(observation.path) != observation.file_observation:
        _fail(
            "locator.source.changed",
            str(observation.path),
            {"observation": "changed"},
            outcome="unavailable",
        )


def _fail(
    code: str,
    subject: str,
    observed: object,
    *,
    outcome: str = "fail",
) -> NoReturn:
    raise LocatorV2Error(
        code,
        subject,
        observed,
        "V2 Expanded Mechanical Locator Language",
        outcome=outcome,
    )
