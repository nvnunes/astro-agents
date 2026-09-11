"""Bounded exact comparison and durable reproduction-output records."""

from __future__ import annotations

import codecs
import csv
import itertools
import json
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence, cast

from research_log_data import (
    Fingerprint,
    compose_directory_fingerprint,
    load_data_file,
    observe_directory_tree,
    observe_file_content,
    parse_fingerprint,
)
from validation.errors import MechanicalContractError
from validation.evidence import load_evidence_file
from validation.evidence_comparison import (
    EVIDENCE_COMPARISON_RESULT_CONTRACT,
    EvidenceComparisonDefinition,
    compare_evidence_scoped,
    definition_for_target,
    evidence_comparison_definitions,
)
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .context import LogContext, resolve_entry
from .model import ActionError
from .reproduction_contract import (
    ReproductionPlan,
    is_repair_verification,
    successful_checkpoint_state,
)
from .reproduction_execution import ExecutionAttempt, ReproductionWorkspace
from .storage import atomic_write_text

COMPARISON_CONTRACT = "research-log-reproduction-comparison/1"
LEGACY_STAGING_SCHEMA = "research-log-reproduction-staging/1"
STAGING_SCHEMA = "research-log-reproduction-staging/2"
MAX_REGULAR_BYTES = 1 << 40
MAX_DIRECTORY_MEMBERS = 100_000
MAX_DIRECTORY_DEPTH = 64
MAX_DIRECTORY_BYTES = 1 << 40
MAX_JSON_DEPTH = 256
MAX_JSON_NODES = 10_000_000
MAX_TABLE_ROWS = 10_000_000
MAX_TABLE_COLUMNS = 10_000
MAX_TABLE_CELLS = 100_000_000
MAX_ARRAY_MEMBERS = 17_179_869_184
MAX_IMAGE_PIXELS = 2_147_483_648
MAX_WORKING_MEMORY = 4 << 30
MAX_STAGING_MANIFEST_BYTES = 64 << 20
IO_CHUNK_BYTES = 8 << 20
ARRAY_CHUNK_MEMBERS = 1_048_576

_IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_JSON_SUFFIXES = {".json"}
_TABLE_SUFFIXES = {".csv", ".tsv"}
_ARRAY_SUFFIXES = {".h5", ".hdf5", ".mat", ".npy", ".npz"}
_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".log",
    ".md",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SUFFIX_PROFILES = {
    **{suffix: "image" for suffix in _IMAGE_SUFFIXES},
    **{suffix: "json" for suffix in _JSON_SUFFIXES},
    **{suffix: "table" for suffix in _TABLE_SUFFIXES},
    **{suffix: "named_array" for suffix in _ARRAY_SUFFIXES},
    **{suffix: "text" for suffix in _TEXT_SUFFIXES},
}
_INTEGER_RE = re.compile(r"[+-]?(?:0|[1-9][0-9]*)\Z")
_FLOAT_RE = re.compile(
    r"[+-]?(?:(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|"
    r"[0-9]+[eE][+-]?[0-9]+|inf(?:inity)?|nan)\Z",
    re.IGNORECASE,
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class _ComparisonFailure(Exception):
    """One expected fail-closed comparison condition."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ArtifactComparison:
    """The bounded comparison result for one declared execution output."""

    artifact: str
    outcome: str
    reason: str | None
    profile: str | None
    expected: Mapping[str, object] | None
    regenerated: Mapping[str, object] | None
    evidence_definition: str | None = None
    evidence: tuple[Mapping[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        comparison: dict[str, object] | None = None
        if self.profile is not None:
            comparison = {
                "contract": COMPARISON_CONTRACT,
                "expected": dict(self.expected) if self.expected is not None else None,
                "profile": self.profile,
                "regenerated": (
                    dict(self.regenerated) if self.regenerated is not None else None
                ),
            }
            if self.evidence_definition is not None:
                comparison["evidence_contract"] = EVIDENCE_COMPARISON_RESULT_CONTRACT
                comparison["evidence_definition"] = self.evidence_definition
                comparison["evidence"] = [dict(item) for item in self.evidence]
        return {
            "artifact": self.artifact,
            "comparison": comparison,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecutionComparison:
    """Every artifact result and its retained run-local output root."""

    entry: str
    execution_id: str
    artifacts: tuple[ArtifactComparison, ...]
    staging: str | None
    complete: bool

    @property
    def matched(self) -> bool:
        return self.complete and all(
            item.outcome == "matched" for item in self.artifacts
        )


@dataclass(frozen=True)
class _StagingRequest:
    plan: ReproductionPlan
    workspace: ReproductionWorkspace
    attempt: ExecutionAttempt
    source_entry_root: Path
    outputs: Sequence[tuple[str, str]]
    results: tuple[ArtifactComparison, ...]


def compare_artifacts(expected: Path, regenerated: Path) -> ArtifactComparison:
    """Compare one artifact path using its closed, suffix-selected v1 profile."""

    artifact = regenerated.name
    profile: str | None = None
    expected_fingerprint = _observed_fingerprint(expected)
    regenerated_fingerprint = _observed_fingerprint(regenerated)
    try:
        profile = _profile(expected, regenerated)
        equal = _compare_with_profile(expected, regenerated, profile)
    except _ComparisonFailure as error:
        return ArtifactComparison(
            artifact,
            "comparison_failed",
            error.reason,
            profile,
            expected_fingerprint,
            regenerated_fingerprint,
        )
    except MemoryError:
        return ArtifactComparison(
            artifact,
            "comparison_failed",
            "resource_limit",
            profile,
            expected_fingerprint,
            regenerated_fingerprint,
        )
    except Exception:
        return ArtifactComparison(
            artifact,
            "comparison_failed",
            "comparator_error",
            profile,
            expected_fingerprint,
            regenerated_fingerprint,
        )
    return ArtifactComparison(
        artifact,
        "matched" if equal else "changed",
        None if equal else "content_changed",
        profile,
        expected_fingerprint,
        regenerated_fingerprint,
    )


def compare_execution_outputs(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    attempt: ExecutionAttempt,
) -> ExecutionComparison:
    """Compare or stage one complete execution output set without promotion."""

    source_entry = resolve_entry(log, attempt.entry)
    state = load_pyrun_state(
        source_entry.root / "pyrun.json",
        entry_root=source_entry.root,
        project_root=workspace.source_project,
    )
    execution = state.executions.get(attempt.execution_id)
    if execution is None:
        raise ActionError(
            "reproduction.comparison.execution_missing",
            f"execution is no longer present: {attempt.entry}:{attempt.execution_id}",
        )
    data_path = source_entry.root / "data.json"
    evidence_path = source_entry.root / "evidence.json"
    definitions: tuple[EvidenceComparisonDefinition, ...] = ()
    if data_path.is_file() and not data_path.is_symlink():
        data = load_data_file(data_path, entry_root=source_entry.root)
        evidence = (
            load_evidence_file(
                evidence_path,
                log_root=log.root,
                entry_root=source_entry.root,
            )
            if evidence_path.is_file() and not evidence_path.is_symlink()
            else None
        )
        definitions = evidence_comparison_definitions(data, evidence)
    results: list[ArtifactComparison] = []
    for artifact, _kind in execution.recipe.outputs:
        expected = output_target_path(
            artifact,
            entry_root=source_entry.root,
            project_root=workspace.source_project,
        )
        regenerated = workspace.map_source(expected)
        definition = definition_for_target(definitions, expected)
        results.append(
            _compare_execution_output(
                artifact,
                expected=expected,
                regenerated=regenerated,
                attempt=attempt,
                definition=definition,
            )
        )
    complete = successful_checkpoint_state(attempt.checkpoint.state)
    staged = _record_execution(
        _StagingRequest(
            plan,
            workspace,
            attempt,
            source_entry.root,
            execution.recipe.outputs,
            tuple(results),
        )
    )
    return ExecutionComparison(
        attempt.entry,
        attempt.execution_id,
        tuple(results),
        staged,
        complete,
    )


def _compare_execution_output(
    artifact: str,
    *,
    expected: Path,
    regenerated: Path,
    attempt: ExecutionAttempt,
    definition: EvidenceComparisonDefinition | None,
) -> ArtifactComparison:
    if not successful_checkpoint_state(attempt.checkpoint.state):
        return ArtifactComparison(
            artifact,
            "failed",
            attempt.failure_code or "generation_failed",
            None,
            _observed_fingerprint(expected),
            _observed_fingerprint(regenerated),
        )
    if not expected.exists() or expected.is_symlink():
        return ArtifactComparison(
            artifact,
            "comparison_failed",
            "baseline_unavailable",
            None,
            _observed_fingerprint(expected),
            _observed_fingerprint(regenerated),
        )
    if not regenerated.exists() or regenerated.is_symlink():
        return ArtifactComparison(
            artifact,
            "failed",
            "output_missing",
            None,
            _observed_fingerprint(expected),
            _observed_fingerprint(regenerated),
        )
    compared = compare_artifacts(expected, regenerated)
    if definition is None:
        return replace(compared, artifact=artifact)
    if compared.outcome != "changed":
        return ArtifactComparison(
            artifact,
            compared.outcome,
            compared.reason,
            compared.profile,
            compared.expected,
            compared.regenerated,
            definition.identity,
        )
    return _compare_evidence_change(
        artifact,
        regenerated=regenerated,
        compared=compared,
        definition=definition,
    )


def _compare_evidence_change(
    artifact: str,
    *,
    regenerated: Path,
    compared: ArtifactComparison,
    definition: EvidenceComparisonDefinition,
) -> ArtifactComparison:
    try:
        evidence_result = compare_evidence_scoped(definition, regenerated=regenerated)
    except MechanicalContractError:
        return ArtifactComparison(
            artifact,
            "comparison_failed",
            "evidence_comparison_failed",
            "evidence",
            compared.expected,
            compared.regenerated,
            definition.identity,
        )
    return ArtifactComparison(
        artifact,
        "matched" if evidence_result.matched else "changed",
        None if evidence_result.matched else "content_changed",
        "evidence",
        compared.expected,
        compared.regenerated,
        definition.identity,
        evidence_result.records,
    )


def clear_execution_reproduction_requirement_locked(
    log: LogContext,
    plan: ReproductionPlan,
    result: ExecutionComparison,
    *,
    project_root: Path,
) -> bool:
    """Record that one execution no longer requires reproduction."""

    if not result.complete or is_repair_verification(plan):
        return False
    planned = {
        (str(item.get("entry")), str(item.get("execution_id")))
        for item in plan.executions
    }
    if (result.entry, result.execution_id) not in planned:
        raise ActionError(
            "reproduction.requirement.execution_unplanned",
            f"execution is outside the accepted plan: "
            f"{result.entry}:{result.execution_id}",
        )
    entry = resolve_entry(log, result.entry)
    state = load_pyrun_state(
        entry.root / "pyrun.json",
        entry_root=entry.root,
        project_root=project_root,
    )
    current = state.executions.get(result.execution_id)
    if current is None:
        raise ActionError(
            "reproduction.requirement.execution_missing", result.execution_id
        )
    if not current.requires_reproduction:
        return False
    executions = dict(state.executions)
    executions[result.execution_id] = replace(current, requires_reproduction=False)
    candidate = PyrunFile(state.path, state.entry_root, executions)
    atomic_write_text(
        state.path,
        validated_pyrun_serialization(candidate, project_root=project_root),
    )
    return True


def _profile(expected: Path, regenerated: Path) -> str:
    left = _path_kind(expected)
    right = _path_kind(regenerated)
    if left != right:
        return "kind"
    if left == "directory":
        return "directory"
    if left != "file":
        raise _ComparisonFailure("unsupported_format", "unsupported artifact kind")
    return _SUFFIX_PROFILES.get(expected.suffix.lower(), "opaque_file")


def _compare_with_profile(expected: Path, regenerated: Path, profile: str) -> bool:
    if profile == "kind":
        return False
    comparators = {
        "directory": _compare_directories,
        "image": _compare_images,
        "json": _compare_json,
        "named_array": _compare_arrays,
        "opaque_file": _compare_bytes,
        "table": _compare_table,
        "text": _compare_text,
    }
    comparator = comparators.get(profile)
    if comparator is None:
        raise _ComparisonFailure("unsupported_format", f"unknown profile: {profile}")
    if profile == "directory":
        return comparator(expected, regenerated)
    left_identity = _regular_identity(expected)
    right_identity = _regular_identity(regenerated)
    equal = comparator(expected, regenerated)
    _require_unchanged(expected, left_identity)
    _require_unchanged(regenerated, right_identity)
    return equal


def _compare_bytes(expected: Path, regenerated: Path) -> bool:
    left = _regular_identity(expected)
    right = _regular_identity(regenerated)
    if left[2] != right[2]:
        return False
    equal = True
    with expected.open("rb") as first, regenerated.open("rb") as second:
        while True:
            left_chunk = first.read(IO_CHUNK_BYTES)
            right_chunk = second.read(IO_CHUNK_BYTES)
            if left_chunk != right_chunk:
                equal = False
                break
            if not left_chunk:
                break
    _require_unchanged(expected, left)
    _require_unchanged(regenerated, right)
    return equal


def _compare_text(expected: Path, regenerated: Path) -> bool:
    left_identity = _regular_identity(expected)
    right_identity = _regular_identity(regenerated)
    equal = left_identity[2] == right_identity[2]
    left_decoder = codecs.getincrementaldecoder("utf-8")("strict")
    right_decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with expected.open("rb") as first, regenerated.open("rb") as second:
            while True:
                left_chunk = first.read(IO_CHUNK_BYTES)
                right_chunk = second.read(IO_CHUNK_BYTES)
                left_text = left_decoder.decode(left_chunk, final=not left_chunk)
                right_text = right_decoder.decode(right_chunk, final=not right_chunk)
                if left_text != right_text:
                    equal = False
                if not left_chunk and not right_chunk:
                    break
    except UnicodeDecodeError as error:
        raise _ComparisonFailure("unsupported_format", str(error)) from error
    _require_unchanged(expected, left_identity)
    _require_unchanged(regenerated, right_identity)
    return equal


def _compare_json(expected: Path, regenerated: Path) -> bool:
    left = _load_json(expected)
    right = _load_json(regenerated)
    return _json_equal(left, right)


def _load_json(path: Path) -> object:
    raw = _read_bounded(path, maximum_memory=MAX_WORKING_MEMORY // 4)
    _preflight_json_depth(raw)
    try:
        value = json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON scalar: {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _ComparisonFailure("resource_limit", "JSON logical limit exceeded")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _preflight_json_depth(raw: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for value in raw:
        if in_string:
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                in_string = False
        elif value == 0x22:
            in_string = True
        elif value in {0x5B, 0x7B}:
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise _ComparisonFailure(
                    "resource_limit", "JSON nesting depth exceeded"
                )
        elif value in {0x5D, 0x7D}:
            depth -= 1


def _json_equal(left: object, right: object) -> bool:
    stack = [(left, right)]
    while stack:
        first, second = stack.pop()
        if type(first) is not type(second):
            return False
        if isinstance(first, dict):
            second_dict = cast(dict[object, object], second)
            if first.keys() != second_dict.keys():
                return False
            stack.extend((value, second_dict[key]) for key, value in first.items())
        elif isinstance(first, list):
            second_list = cast(list[object], second)
            if len(first) != len(second_list):
                return False
            stack.extend(zip(first, second_list, strict=True))
        elif first != second:
            return False
        elif isinstance(first, float) and first == 0.0:
            if math.copysign(1.0, first) != math.copysign(1.0, cast(float, second)):
                return False
    return True


def _compare_table(expected: Path, regenerated: Path) -> bool:
    delimiter = "\t" if expected.suffix.lower() == ".tsv" else ","
    first = _table_rows(expected, delimiter)
    second = _table_rows(regenerated, delimiter)
    equal = True
    sentinel = object()
    for left, right in itertools.zip_longest(first, second, fillvalue=sentinel):
        if left is sentinel or right is sentinel:
            equal = False
        elif not _table_row_equal(
            cast(tuple[object, ...], left), cast(tuple[object, ...], right)
        ):
            equal = False
    return equal


def _table_rows(path: Path, delimiter: str) -> Iterator[tuple[object, ...]]:
    identity = _regular_identity(path)
    if identity[2] > MAX_WORKING_MEMORY // 2:
        raise _ComparisonFailure("resource_limit", "table exceeds working memory")
    rows = 0
    cells = 0
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            for row in csv.reader(handle, delimiter=delimiter, strict=True):
                rows += 1
                cells += len(row)
                if (
                    rows > MAX_TABLE_ROWS
                    or len(row) > MAX_TABLE_COLUMNS
                    or cells > MAX_TABLE_CELLS
                ):
                    raise _ComparisonFailure(
                        "resource_limit", "table logical limit exceeded"
                    )
                yield (
                    tuple(row)
                    if rows == 1
                    else tuple(_typed_cell(value) for value in row)
                )
    except (csv.Error, UnicodeError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error
    _require_unchanged(path, identity)


def _typed_cell(value: str) -> object:
    if value == "":
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if _INTEGER_RE.fullmatch(value):
        return int(value)
    if _FLOAT_RE.fullmatch(value):
        return float(value)
    return value


def _table_row_equal(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    return len(left) == len(right) and all(
        _table_cell_equal(first, second)
        for first, second in zip(left, right, strict=True)
    )


def _table_cell_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if not isinstance(left, float):
        return left == right
    return _float_scalar_equal(left, cast(float, right))


def _compare_arrays(expected: Path, regenerated: Path) -> bool:
    suffix = expected.suffix.lower()
    if suffix in {".npy", ".npz"}:
        return _compare_numpy_container(expected, regenerated, suffix)
    if suffix in {".h5", ".hdf5"}:
        return _compare_hdf5(expected, regenerated)
    if suffix == ".mat":
        with expected.open("rb") as handle:
            expected_hdf5 = handle.read(8) == b"\x89HDF\r\n\x1a\n"
        with regenerated.open("rb") as handle:
            regenerated_hdf5 = handle.read(8) == b"\x89HDF\r\n\x1a\n"
        if expected_hdf5 != regenerated_hdf5:
            return False
        return (
            _compare_hdf5(expected, regenerated)
            if expected_hdf5
            else _compare_mat(expected, regenerated)
        )
    raise _ComparisonFailure("unsupported_format", "unknown array container")


def _compare_numpy_container(expected: Path, regenerated: Path, suffix: str) -> bool:
    try:
        import numpy as np
    except ImportError as error:
        raise _ComparisonFailure("unsupported_format", "numpy unavailable") from error
    try:
        _regular_identity(expected)
        _regular_identity(regenerated)
        if suffix == ".npy":
            left = np.load(expected, mmap_mode="r", allow_pickle=False)
            right = np.load(regenerated, mmap_mode="r", allow_pickle=False)
            return _numpy_array_equal(left, right)
        _preflight_npz(expected)
        _preflight_npz(regenerated)
        with (
            np.load(expected, allow_pickle=False) as first,
            np.load(regenerated, allow_pickle=False) as second,
        ):
            if sorted(first.files) != sorted(second.files):
                return False
            for name in sorted(first.files):
                if not _numpy_array_equal(first[name], second[name]):
                    return False
            return True
    except _ComparisonFailure:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error


def _numpy_array_equal(
    left: object, right: object, *, allow_object: bool = False
) -> bool:
    import numpy as np

    memory_mapped = isinstance(left, np.memmap) and isinstance(right, np.memmap)
    first = np.asanyarray(left)
    second = np.asanyarray(right)
    if first.dtype != second.dtype or first.shape != second.shape:
        return False
    if first.dtype.hasobject:
        if not allow_object:
            raise _ComparisonFailure(
                "unsupported_format", "object arrays are unsupported"
            )
        return _object_array_equal(first, second)
    if first.size > MAX_ARRAY_MEMBERS:
        raise _ComparisonFailure("resource_limit", "array member limit exceeded")
    if first.nbytes + second.nbytes > MAX_WORKING_MEMORY and not memory_mapped:
        raise _ComparisonFailure("resource_limit", "array exceeds working memory")
    if first.dtype.fields is not None:
        return all(
            _numpy_array_equal(first[name], second[name])
            for name in first.dtype.names or ()
        )
    left_flat = first.reshape(-1)
    right_flat = second.reshape(-1)
    for start in range(0, first.size, ARRAY_CHUNK_MEMBERS):
        left_chunk = left_flat[start : start + ARRAY_CHUNK_MEMBERS]
        right_chunk = right_flat[start : start + ARRAY_CHUNK_MEMBERS]
        if not _primitive_array_equal(left_chunk, right_chunk):
            return False
    return True


def _object_array_equal(left: object, right: object) -> bool:
    import numpy as np

    first = np.asanyarray(left)
    second = np.asanyarray(right)
    if first.size > MAX_ARRAY_MEMBERS:
        raise _ComparisonFailure("resource_limit", "array member limit exceeded")
    if first.nbytes + second.nbytes > MAX_WORKING_MEMORY:
        raise _ComparisonFailure("resource_limit", "array exceeds working memory")
    nodes = [0]
    for a, b in zip(first.flat, second.flat, strict=True):
        if not _object_value_equal(a, b, depth=1, nodes=nodes):
            return False
    return True


def _object_value_equal(
    left: object, right: object, *, depth: int, nodes: list[int]
) -> bool:
    import numpy as np

    nodes[0] += 1
    if nodes[0] > MAX_ARRAY_MEMBERS or depth > MAX_JSON_DEPTH:
        raise _ComparisonFailure("resource_limit", "object structure limit exceeded")
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        return _object_ndarray_equal(left, right, depth=depth, nodes=nodes)
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        return _float_scalar_equal(left, cast(float, right))
    if isinstance(left, complex):
        right_complex = cast(complex, right)
        return _float_scalar_equal(
            left.real, right_complex.real
        ) and _float_scalar_equal(left.imag, right_complex.imag)
    return bool(left == right)


def _object_ndarray_equal(
    left: object, right: object, *, depth: int, nodes: list[int]
) -> bool:
    import numpy as np

    if not isinstance(left, np.ndarray) or not isinstance(right, np.ndarray):
        return False
    if left.dtype != right.dtype or left.shape != right.shape:
        return False
    if not left.dtype.hasobject:
        return _numpy_array_equal(left, right)
    return all(
        _object_value_equal(first, second, depth=depth + 1, nodes=nodes)
        for first, second in zip(left.flat, right.flat, strict=True)
    )


def _primitive_array_equal(left: object, right: object) -> bool:
    import numpy as np

    first = np.asanyarray(left)
    second = np.asanyarray(right)
    if first.dtype.kind == "c":
        return _primitive_array_equal(
            first.real, second.real
        ) and _primitive_array_equal(first.imag, second.imag)
    if first.dtype.kind == "f":
        nan_equal = np.isnan(first) & np.isnan(second)
        ordinary_equal = first == second
        if not bool(np.all(nan_equal | ordinary_equal)):
            return False
        zeros = ordinary_equal & (first == 0)
        return bool(np.array_equal(np.signbit(first[zeros]), np.signbit(second[zeros])))
    return bool(np.array_equal(first, second))


def _float_scalar_equal(left: float, right: float) -> bool:
    if math.isnan(left) or math.isnan(right):
        return math.isnan(left) and math.isnan(right)
    if left != right:
        return False
    return left != 0.0 or math.copysign(1.0, left) == math.copysign(1.0, right)


def _preflight_npz(path: Path) -> None:
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.file_size > MAX_WORKING_MEMORY // 2:
                    raise _ComparisonFailure(
                        "resource_limit", "NPZ member exceeds working memory"
                    )
    except _ComparisonFailure:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error


def _compare_hdf5(expected: Path, regenerated: Path) -> bool:
    try:
        import h5py
    except ImportError as error:
        raise _ComparisonFailure("unsupported_format", "h5py unavailable") from error
    try:
        with h5py.File(expected, "r") as first, h5py.File(regenerated, "r") as second:
            left_names = _hdf5_names(first)
            right_names = _hdf5_names(second)
            if left_names != right_names or not _hdf5_attrs_equal(
                first.attrs, second.attrs
            ):
                return False
            for name, kind in left_names:
                left = first[name]
                right = second[name]
                if not _hdf5_attrs_equal(left.attrs, right.attrs):
                    return False
                if kind == "dataset":
                    if not isinstance(right, h5py.Dataset) or not _hdf5_dataset_equal(
                        left, right
                    ):
                        return False
            return True
    except _ComparisonFailure:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error


def _hdf5_names(root: object) -> tuple[tuple[str, str], ...]:
    import h5py

    result: list[tuple[str, str]] = []

    def visit(name: str, value: object) -> None:
        link = root.get(name, getlink=True)  # type: ignore[attr-defined]
        if not isinstance(link, h5py.HardLink):
            raise _ComparisonFailure(
                "unsupported_format", "external and soft HDF5 links are unsupported"
            )
        if isinstance(value, h5py.Dataset):
            kind = "dataset"
        elif isinstance(value, h5py.Group):
            kind = "group"
        else:
            raise _ComparisonFailure("unsupported_format", "unsupported HDF5 link")
        result.append((name, kind))

    root.visititems(visit)  # type: ignore[attr-defined]
    if len(result) > MAX_DIRECTORY_MEMBERS:
        raise _ComparisonFailure("resource_limit", "HDF5 member limit exceeded")
    return tuple(sorted(result))


def _hdf5_attrs_equal(left: object, right: object) -> bool:
    first = cast(Any, left)
    second = cast(Any, right)
    if sorted(first.keys()) != sorted(second.keys()):
        return False
    for name in sorted(first.keys()):
        if not _numpy_array_equal(first[name], second[name], allow_object=True):
            return False
    return True


def _hdf5_dataset_equal(left: object, right: object) -> bool:
    first = cast(Any, left)
    second = cast(Any, right)
    if first.dtype != second.dtype or first.shape != second.shape:
        return False
    members = math.prod(first.shape) if first.shape else 1
    if members > MAX_ARRAY_MEMBERS:
        raise _ComparisonFailure("resource_limit", "array member limit exceeded")
    if not first.shape:
        return _numpy_array_equal(first[()], second[()], allow_object=True)
    row_members = max(1, math.prod(first.shape[1:]))
    itemsize = max(1, first.dtype.itemsize)
    if row_members * itemsize * 2 > MAX_WORKING_MEMORY:
        raise _ComparisonFailure("resource_limit", "HDF5 row exceeds working memory")
    step = max(
        1,
        min(
            ARRAY_CHUNK_MEMBERS // row_members,
            MAX_WORKING_MEMORY // (row_members * itemsize * 2),
        ),
    )
    for start in range(0, first.shape[0], step):
        selection = slice(start, min(first.shape[0], start + step))
        if not _numpy_array_equal(
            first[selection], second[selection], allow_object=True
        ):
            return False
    return True


def _compare_mat(expected: Path, regenerated: Path) -> bool:
    try:
        from scipy.io import loadmat, whosmat
    except ImportError as error:
        raise _ComparisonFailure("unsupported_format", "scipy unavailable") from error
    try:
        first_names = sorted(
            (name, shape, dtype) for name, shape, dtype in whosmat(expected)
        )
        second_names = sorted(
            (name, shape, dtype) for name, shape, dtype in whosmat(regenerated)
        )
        if first_names != second_names:
            return False
        for name, shape, _dtype in first_names:
            if math.prod(shape) > MAX_ARRAY_MEMBERS:
                raise _ComparisonFailure(
                    "resource_limit", "array member limit exceeded"
                )
            left = loadmat(expected, variable_names=[name], squeeze_me=False)[name]
            right = loadmat(regenerated, variable_names=[name], squeeze_me=False)[name]
            if left.nbytes + right.nbytes > MAX_WORKING_MEMORY:
                raise _ComparisonFailure(
                    "resource_limit", "MAT member exceeds working memory"
                )
            if not _numpy_array_equal(left, right, allow_object=True):
                return False
        return True
    except _ComparisonFailure:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error


def _compare_images(expected: Path, regenerated: Path) -> bool:
    try:
        from PIL import Image
    except ImportError as error:
        raise _ComparisonFailure("unsupported_format", "Pillow unavailable") from error
    try:
        with Image.open(expected) as first, Image.open(regenerated) as second:
            left_frames = getattr(first, "n_frames", 1)
            right_frames = getattr(second, "n_frames", 1)
            if left_frames != right_frames:
                return False
            pixels = 0
            for frame in range(left_frames):
                first.seek(frame)
                second.seek(frame)
                pixels += first.width * first.height
                if pixels > MAX_IMAGE_PIXELS:
                    raise _ComparisonFailure(
                        "resource_limit", "image pixel limit exceeded"
                    )
                if first.size != second.size or first.mode != second.mode:
                    return False
                bands = max(1, len(first.getbands()))
                rows = max(1, MAX_WORKING_MEMORY // max(1, first.width * bands * 2))
                for top in range(0, first.height, rows):
                    box = (0, top, first.width, min(first.height, top + rows))
                    if first.crop(box).tobytes() != second.crop(box).tobytes():
                        return False
            return True
    except _ComparisonFailure:
        raise
    except (OSError, ValueError) as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error


def _compare_directories(expected: Path, regenerated: Path) -> bool:
    left = _directory_members(expected)
    right = _directory_members(regenerated)
    left_projection = tuple((relative, kind) for relative, kind, _ in left)
    right_projection = tuple((relative, kind) for relative, kind, _ in right)
    if left_projection != right_projection:
        return False
    for (relative, kind, left_path), (_, _, right_path) in zip(
        left, right, strict=True
    ):
        if kind == "directory":
            continue
        profile = _profile(left_path, right_path)
        if not _compare_with_profile(left_path, right_path, profile):
            return False
    return left_projection == tuple(
        (relative, kind) for relative, kind, _path in _directory_members(expected)
    ) and right_projection == tuple(
        (relative, kind) for relative, kind, _path in _directory_members(regenerated)
    )


def _directory_members(root: Path) -> tuple[tuple[str, str, Path], ...]:
    if root.is_symlink() or not root.is_dir():
        raise _ComparisonFailure("unsupported_format", "directory is unavailable")
    result: list[tuple[str, str, Path]] = []
    total_bytes = 0
    stack = [(root, 0)]
    while stack:
        parent, depth = stack.pop()
        if depth > MAX_DIRECTORY_DEPTH:
            raise _ComparisonFailure("resource_limit", "directory depth exceeded")
        try:
            children = sorted(parent.iterdir(), key=lambda path: path.name)
        except OSError as error:
            raise _ComparisonFailure("comparator_error", str(error)) from error
        for child in children:
            if child.is_symlink():
                raise _ComparisonFailure(
                    "unsupported_format", "directory contains symlink"
                )
            relative = child.relative_to(root).as_posix()
            if child.is_dir():
                kind = "directory"
                stack.append((child, depth + 1))
            elif child.is_file():
                kind = "file"
                total_bytes += child.stat().st_size
            else:
                raise _ComparisonFailure(
                    "unsupported_format", "unsupported directory member"
                )
            result.append((relative, kind, child))
            if len(result) > MAX_DIRECTORY_MEMBERS or total_bytes > MAX_DIRECTORY_BYTES:
                raise _ComparisonFailure("resource_limit", "directory limit exceeded")
    return tuple(sorted(result, key=lambda item: item[0]))


def _record_execution(request: _StagingRequest) -> str:
    """Persist one complete comparison before state update or publication."""

    workspace = request.workspace
    attempt = request.attempt
    by_artifact = {item.artifact: item for item in request.results}
    outputs: list[dict[str, object]] = []
    retained_bytes = 0
    for artifact, kind in request.outputs:
        retained = output_target_path(
            artifact,
            entry_root=request.source_entry_root,
            project_root=workspace.source_project,
        )
        source = workspace.map_source(retained)
        available = source.exists() and not source.is_symlink()
        staged_path = None
        if available:
            try:
                staged_path = source.relative_to(workspace.work_project).as_posix()
            except ValueError as error:
                raise ActionError(
                    "reproduction.staging.path_invalid", str(source)
                ) from error
            retained_bytes += _available_bytes(source, kind)
        result = by_artifact[artifact]
        outputs.append(
            {
                "artifact": artifact,
                "available": available,
                **(
                    {
                        "evidence": [dict(item) for item in result.evidence],
                        "evidence_contract": EVIDENCE_COMPARISON_RESULT_CONTRACT,
                        "evidence_definition": result.evidence_definition,
                    }
                    if result.evidence_definition is not None
                    else {}
                ),
                "expected": result.expected,
                "kind": kind,
                "outcome": result.outcome,
                "profile": result.profile,
                "reason": result.reason,
                "regenerated": result.regenerated,
                "staged": staged_path,
            }
        )
    diagnostics = [
        value
        for value in (attempt.stdout, attempt.stderr)
        if _available_run_file(workspace.run_root, value)
    ]
    record = {
        "bytes": retained_bytes,
        "complete": successful_checkpoint_state(attempt.checkpoint.state),
        "diagnostics": diagnostics,
        "entry": attempt.entry,
        "execution_id": attempt.execution_id,
        "outputs": outputs,
        "path": "workspace",
    }
    _upsert_staging_manifest(request.plan, workspace, record)
    return "workspace"


def _upsert_staging_manifest(
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    record: Mapping[str, object],
) -> None:
    path = workspace.run_root / "staging.json"
    if path.exists() or path.is_symlink():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ActionError("reproduction.staging.invalid", str(error)) from error
        if not isinstance(current, dict) or set(current) != {
            "executions",
            "run_id",
            "schema",
            "target",
        }:
            raise ActionError("reproduction.staging.invalid", str(path))
        if (
            current.get("schema") != STAGING_SCHEMA
            or current.get("run_id") != workspace.run_id
            or current.get("target") != dict(plan.target)
        ):
            raise ActionError("reproduction.staging.invalid", str(path))
    else:
        current = {
            "executions": [],
            "run_id": workspace.run_id,
            "schema": STAGING_SCHEMA,
            "target": dict(plan.target),
        }
    executions = current.get("executions")
    if not isinstance(executions, list):
        raise ActionError("reproduction.staging.invalid", str(path))
    identity = (record.get("entry"), record.get("execution_id"))
    executions[:] = [
        item
        for item in executions
        if not isinstance(item, dict)
        or (item.get("entry"), item.get("execution_id")) != identity
    ]
    executions.append(dict(record))
    executions.sort(key=lambda item: (item["entry"], item["execution_id"]))
    serialized = _canonical_json(current)
    if len(serialized.encode("utf-8")) > MAX_STAGING_MANIFEST_BYTES:
        raise ActionError(
            "reproduction.staging.resource_limit", "staging manifest limit exceeded"
        )
    atomic_write_text(path, serialized)


def load_recorded_comparisons(
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    *,
    verify_outputs: bool = True,
) -> tuple[ExecutionComparison, ...]:
    """Load every durable v2 execution comparison for publication or resume."""

    path = workspace.run_root / "staging.json"
    if not path.exists() and not path.is_symlink():
        return ()
    if path.is_symlink() or not path.is_file():
        raise ActionError("reproduction.staging.invalid", str(path))
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_STAGING_MANIFEST_BYTES:
            raise ValueError("staging manifest crossed its byte bound")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ActionError("reproduction.staging.invalid", str(error)) from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {"executions", "run_id", "schema", "target"}
        or value.get("schema") != STAGING_SCHEMA
        or value.get("run_id") != workspace.run_id
        or value.get("target") != dict(plan.target)
        or not isinstance(value.get("executions"), list)
    ):
        raise ActionError("reproduction.staging.invalid", str(path))
    results = tuple(
        _decode_recorded_comparison(item, workspace, verify_outputs=verify_outputs)
        for item in cast(list[object], value["executions"])
    )
    identities = [(item.entry, item.execution_id) for item in results]
    if identities != sorted(set(identities)):
        raise ActionError("reproduction.staging.invalid", "comparison order is invalid")
    return results


def _decode_recorded_comparison(
    value: object,
    workspace: ReproductionWorkspace,
    *,
    verify_outputs: bool,
) -> ExecutionComparison:
    fields = {
        "bytes",
        "complete",
        "diagnostics",
        "entry",
        "execution_id",
        "outputs",
        "path",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ActionError("reproduction.staging.invalid", "comparison is invalid")
    entry = value.get("entry")
    execution_id = value.get("execution_id")
    outputs = value.get("outputs")
    diagnostics = value.get("diagnostics")
    retained_bytes = value.get("bytes")
    if (
        not isinstance(entry, str)
        or not isinstance(execution_id, str)
        or not isinstance(outputs, list)
        or not isinstance(diagnostics, list)
        or not all(isinstance(item, str) for item in diagnostics)
        or not isinstance(retained_bytes, int)
        or isinstance(retained_bytes, bool)
        or retained_bytes < 0
        or not isinstance(value.get("complete"), bool)
        or value.get("path") != "workspace"
    ):
        raise ActionError("reproduction.staging.invalid", "comparison is invalid")
    artifacts = tuple(
        _decode_recorded_artifact(item, workspace, verify_outputs=verify_outputs)
        for item in cast(list[object], outputs)
    )
    identities = [item.artifact for item in artifacts]
    if identities != sorted(set(identities)):
        raise ActionError("reproduction.staging.invalid", "output order is invalid")
    return ExecutionComparison(
        entry,
        execution_id,
        artifacts,
        "workspace",
        cast(bool, value["complete"]),
    )


def _decode_recorded_artifact(
    value: object,
    workspace: ReproductionWorkspace,
    *,
    verify_outputs: bool,
) -> ArtifactComparison:
    fields = {
        "artifact",
        "available",
        "expected",
        "kind",
        "outcome",
        "profile",
        "reason",
        "regenerated",
        "staged",
    }
    evidence_fields = {"evidence", "evidence_contract", "evidence_definition"}
    if (
        not isinstance(value, Mapping)
        or not fields <= set(value) <= fields | evidence_fields
        or set(value) & evidence_fields not in (set(), evidence_fields)
    ):
        raise ActionError("reproduction.staging.invalid", "output is invalid")
    artifact = value.get("artifact")
    outcome = value.get("outcome")
    profile = value.get("profile")
    reason = value.get("reason")
    available = value.get("available")
    staged = value.get("staged")
    evidence = value.get("evidence", [])
    evidence_definition = value.get("evidence_definition")
    if (
        not isinstance(artifact, str)
        or value.get("kind") not in {"file", "directory"}
        or outcome
        not in {"matched", "changed", "failed", "comparison_failed", "skipped"}
        or profile is not None
        and not isinstance(profile, str)
        or reason is not None
        and not isinstance(reason, str)
        or not isinstance(available, bool)
        or staged is not None
        and not isinstance(staged, str)
        or available != (staged is not None)
        or not isinstance(evidence, list)
        or not all(isinstance(item, Mapping) for item in evidence)
        or evidence_definition is not None
        and (
            not isinstance(evidence_definition, str)
            or _SHA256_RE.fullmatch(evidence_definition) is None
            or value.get("evidence_contract") != EVIDENCE_COMPARISON_RESULT_CONTRACT
        )
    ):
        raise ActionError("reproduction.staging.invalid", "output is invalid")
    current_path = None
    if staged is not None:
        current_path = _safe_workspace_path(workspace, staged)
    expected = _recorded_fingerprint(value.get("expected"), artifact)
    regenerated = _recorded_fingerprint(value.get("regenerated"), artifact)
    if verify_outputs:
        _require_recorded_output_current(
            current_path,
            cast(str, value["kind"]),
            regenerated,
            available=available,
        )
    return ArtifactComparison(
        artifact,
        cast(str, outcome),
        reason,
        profile,
        expected,
        regenerated,
        evidence_definition,
        tuple(dict(item) for item in cast(list[Mapping[str, object]], evidence)),
    )


def _recorded_fingerprint(value: object, artifact: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    return parse_fingerprint(value, artifact).as_dict()


def _safe_workspace_path(workspace: ReproductionWorkspace, value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ActionError("reproduction.staging.invalid", "output path is invalid")
    path = workspace.work_project.joinpath(*pure.parts)
    try:
        path.resolve().relative_to(workspace.work_project.resolve())
    except ValueError as error:
        raise ActionError(
            "reproduction.staging.invalid", "output path escapes"
        ) from error
    return path


def _require_recorded_output_current(
    path: Path | None,
    kind: str,
    regenerated: Mapping[str, object] | None,
    *,
    available: bool,
) -> None:
    """Reject altered run-local output state instead of permitting a retry."""

    if not available:
        if path is not None:
            raise ActionError("reproduction.staging.invalid", "output path is invalid")
        return
    assert path is not None
    if (
        path.is_symlink()
        or (kind == "file" and not path.is_file())
        or (kind == "directory" and not path.is_dir())
    ):
        raise ActionError(
            "reproduction.staging.output_changed", f"recorded output changed: {path}"
        )
    current = _observed_fingerprint(path)
    if regenerated is not None and current != regenerated:
        raise ActionError(
            "reproduction.staging.output_changed", f"recorded output changed: {path}"
        )


def _available_bytes(source: Path, kind: str) -> int:
    if kind == "file":
        if not source.is_file():
            raise ActionError("reproduction.staging.kind_changed", str(source))
        return source.stat().st_size
    elif kind == "directory":
        if not source.is_dir():
            raise ActionError("reproduction.staging.kind_changed", str(source))
        members = _directory_members(source)
        return sum(
            path.stat().st_size for _, item_kind, path in members if item_kind == "file"
        )
    raise ActionError("reproduction.staging.kind_invalid", kind)


def _available_run_file(run_root: Path, value: str) -> bool:
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return False
    path = run_root.joinpath(*pure.parts)
    try:
        path.resolve().relative_to(run_root.resolve())
    except ValueError:
        return False
    return path.is_file() and not path.is_symlink()


def _observed_fingerprint(path: Path) -> Mapping[str, object] | None:
    try:
        if path.is_symlink() or not path.exists():
            return None
        if path.is_file():
            _regular_identity(path)
            digest, _ = observe_file_content(path)
            return Fingerprint("sha256", digest=digest).as_dict()
        if path.is_dir():
            _directory_members(path)
            _, members, _ = observe_directory_tree(path)
            entries = []
            for member in members:
                if member.type == "directory":
                    entries.append(member)
                else:
                    digest, _ = observe_file_content(path / PurePosixPath(member.path))
                    entries.append(type(member)(member.path, "file", digest))
            return compose_directory_fingerprint(tuple(entries)).as_dict()
    except (OSError, ValueError, _ComparisonFailure):
        return None
    return None


def _regular_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        value = path.stat(follow_symlinks=False)
    except OSError as error:
        raise _ComparisonFailure("comparator_error", str(error)) from error
    if not path.is_file() or path.is_symlink():
        raise _ComparisonFailure("unsupported_format", "artifact is not a regular file")
    if value.st_size > MAX_REGULAR_BYTES:
        raise _ComparisonFailure("resource_limit", "regular artifact limit exceeded")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _require_unchanged(path: Path, identity: tuple[int, int, int, int]) -> None:
    if _regular_identity(path) != identity:
        raise _ComparisonFailure("comparator_error", "artifact changed while reading")


def _read_bounded(path: Path, *, maximum_memory: int = MAX_WORKING_MEMORY) -> bytes:
    identity = _regular_identity(path)
    if identity[2] > maximum_memory:
        raise _ComparisonFailure("resource_limit", "decoder working memory exceeded")
    value = path.read_bytes()
    _require_unchanged(path, identity)
    return value


def _path_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "missing"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
