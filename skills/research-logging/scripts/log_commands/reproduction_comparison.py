"""Bounded exact comparison and durable staging for reproduction outputs."""

from __future__ import annotations

import codecs
import csv
import hashlib
import itertools
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence, cast

from research_log_data import (
    Fingerprint,
    compose_directory_fingerprint,
    observe_directory_tree,
    observe_file_content,
)
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .context import LogContext, resolve_entry
from .model import ActionError
from .reproduction_contract import ReproductionPlan
from .reproduction_execution import ExecutionAttempt, ReproductionWorkspace

COMPARISON_CONTRACT = "research-log-reproduction-comparison/1"
STAGING_SCHEMA = "research-log-reproduction-staging/1"
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
MAX_RUN_STAGING_BYTES = 1 << 40
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

    def as_dict(self) -> dict[str, object]:
        comparison = None
        if self.profile is not None:
            comparison = {
                "contract": COMPARISON_CONTRACT,
                "expected": dict(self.expected) if self.expected is not None else None,
                "profile": self.profile,
                "regenerated": (
                    dict(self.regenerated)
                    if self.regenerated is not None
                    else None
                ),
            }
        return {
            "artifact": self.artifact,
            "comparison": comparison,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecutionComparison:
    """Every artifact result and optional retained diagnostic bundle."""

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


@dataclass(frozen=True)
class ConfirmationUpdates:
    """Candidate entry states and bytes for one coordinated publication."""

    files: Mapping[Path, str]
    states: Mapping[str, PyrunFile]
    execution_ids: Mapping[str, frozenset[str]]


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
    results: list[ArtifactComparison] = []
    for artifact, _kind in execution.recipe.outputs:
        expected = output_target_path(
            artifact,
            entry_root=source_entry.root,
            project_root=workspace.source_project,
        )
        regenerated = workspace.map_source(expected)
        if attempt.checkpoint.state != "complete":
            results.append(
                ArtifactComparison(
                    artifact,
                    "failed",
                    attempt.failure_code or "generation_failed",
                    None,
                    _observed_fingerprint(expected),
                    _observed_fingerprint(regenerated),
                )
            )
            continue
        if not expected.exists() or expected.is_symlink():
            results.append(
                ArtifactComparison(
                    artifact,
                    "comparison_failed",
                    "baseline_unavailable",
                    None,
                    _observed_fingerprint(expected),
                    _observed_fingerprint(regenerated),
                )
            )
            continue
        if not regenerated.exists() or regenerated.is_symlink():
            results.append(
                ArtifactComparison(
                    artifact,
                    "failed",
                    "output_missing",
                    None,
                    _observed_fingerprint(expected),
                    _observed_fingerprint(regenerated),
                )
            )
            continue
        compared = compare_artifacts(expected, regenerated)
        results.append(
            ArtifactComparison(
                artifact,
                compared.outcome,
                compared.reason,
                compared.profile,
                compared.expected,
                compared.regenerated,
            )
        )
    complete = attempt.checkpoint.state == "complete"
    matched = complete and all(item.outcome == "matched" for item in results)
    staged = None
    if not matched:
        staged = _stage_execution(
            _StagingRequest(
                plan,
                workspace,
                attempt,
                source_entry.root,
                execution.recipe.outputs,
                tuple(results),
            )
        )
    else:
        _discard_matched_outputs(
            workspace,
            tuple(
                workspace.map_source(
                    output_target_path(
                        artifact,
                        entry_root=source_entry.root,
                        project_root=workspace.source_project,
                    )
                )
                for artifact, _kind in execution.recipe.outputs
            ),
        )
    return ExecutionComparison(
        attempt.entry,
        attempt.execution_id,
        tuple(results),
        staged,
        complete,
    )


def prepare_confirmation_updates_locked(
    log: LogContext,
    plan: ReproductionPlan,
    results: Sequence[ExecutionComparison],
    *,
    project_root: Path,
    verify_snapshot: bool = True,
) -> ConfirmationUpdates:
    """Build exact confirmation writes under the caller's scope lock.

    The caller owns either the selected entry lock or the enclosing log lock.
    Snapshot verification is deliberately adjacent to building the retained
    transaction. The publication owner combines these bytes with the targeted
    validation refresh and authoritative reproduction result before writing.
    """

    from .reproduction_planner import verify_reproduction_snapshot

    if verify_snapshot:
        verify_reproduction_snapshot(log, plan)
    selected: dict[str, set[str]] = {}
    for result in results:
        if result.matched:
            selected.setdefault(result.entry, set()).add(result.execution_id)
    updates: dict[Path, str] = {}
    states: dict[str, PyrunFile] = {}
    changed_ids: dict[str, frozenset[str]] = {}
    for entry_id, identities in sorted(selected.items()):
        entry = resolve_entry(log, entry_id)
        state = load_pyrun_state(
            entry.root / "pyrun.json",
            entry_root=entry.root,
            project_root=project_root,
        )
        missing = sorted(identities - set(state.executions))
        if missing:
            raise ActionError(
                "reproduction.confirmation.execution_missing", ", ".join(missing)
            )
        executions = dict(state.executions)
        changed = False
        changed_in_entry: set[str] = set()
        for identity in identities:
            current = executions[identity]
            if current.confirmed:
                continue
            changed = True
            changed_in_entry.add(identity)
            executions[identity] = PyrunExecution(
                True,
                current.slow,
                current.last_run_at,
                current.runner,
                current.environment_profile,
                current.execution_contract,
                current.recipe,
                current.observed,
            )
        if changed:
            candidate = PyrunFile(state.path, state.entry_root, executions)
            states[entry_id] = candidate
            changed_ids[entry_id] = frozenset(changed_in_entry)
            updates[state.path] = validated_pyrun_serialization(
                candidate, project_root=project_root
            )
    return ConfirmationUpdates(updates, states, changed_ids)


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
        raise _ComparisonFailure(
            "unsupported_format", f"unknown profile: {profile}"
        )
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
            if math.copysign(1.0, first) != math.copysign(
                1.0, cast(float, second)
            ):
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
        with np.load(expected, allow_pickle=False) as first, np.load(
            regenerated, allow_pickle=False
        ) as second:
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
        raise _ComparisonFailure(
            "resource_limit", "object structure limit exceeded"
        )
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        return _object_ndarray_equal(
            left, right, depth=depth, nodes=nodes
        )
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
        ) and _primitive_array_equal(
            first.imag, second.imag
        )
    if first.dtype.kind == "f":
        nan_equal = np.isnan(first) & np.isnan(second)
        ordinary_equal = first == second
        if not bool(np.all(nan_equal | ordinary_equal)):
            return False
        zeros = ordinary_equal & (first == 0)
        return bool(
            np.array_equal(np.signbit(first[zeros]), np.signbit(second[zeros]))
        )
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
                    if not isinstance(
                        right, h5py.Dataset
                    ) or not _hdf5_dataset_equal(left, right):
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
        raise _ComparisonFailure(
            "resource_limit", "HDF5 row exceeds working memory"
        )
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
        (relative, kind)
        for relative, kind, _path in _directory_members(expected)
    ) and right_projection == tuple(
        (relative, kind)
        for relative, kind, _path in _directory_members(regenerated)
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


def _stage_execution(request: _StagingRequest) -> str:
    plan = request.plan
    workspace = request.workspace
    attempt = request.attempt
    digest = hashlib.sha256(
        f"{attempt.entry}\0{attempt.execution_id}".encode("utf-8")
    ).hexdigest()[:16]
    relative_bundle = PurePosixPath("executions", f"{attempt.entry}-{digest}")
    bundle = workspace.run_root.joinpath(*relative_bundle.parts)
    if bundle.exists() or bundle.is_symlink():
        raise ActionError(
            "reproduction.staging.exists", f"staging bundle already exists: {bundle}"
        )
    temporary = bundle.with_name(f".{bundle.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ActionError(
            "reproduction.staging.exists", f"temporary staging exists: {temporary}"
        )
    existing_bytes = _recorded_staging_bytes(workspace)
    temporary.mkdir(parents=True)
    by_artifact = {item.artifact: item for item in request.results}
    manifest_outputs: list[dict[str, object]] = []
    copied_bytes = 0
    try:
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
                destination = temporary / "outputs" / _portable_stage_path(artifact)
                copied_bytes += _copy_available(source, destination, kind)
                staged_path = destination.relative_to(temporary).as_posix()
            result = by_artifact[artifact]
            manifest_outputs.append(
                {
                    "artifact": artifact,
                    "available": available,
                    "expected": result.expected,
                    "kind": kind,
                    "outcome": result.outcome,
                    "reason": result.reason,
                    "regenerated": result.regenerated,
                    "staged": staged_path,
                }
            )
        diagnostics: list[str] = []
        for value in (attempt.stdout, attempt.stderr):
            source = workspace.run_root / PurePosixPath(value)
            if source.is_file() and not source.is_symlink():
                destination = temporary / "diagnostics" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_bytes += destination.stat().st_size
                diagnostics.append(destination.relative_to(temporary).as_posix())
        bundle_record = {
            "bytes": copied_bytes,
            "complete": attempt.checkpoint.state == "complete",
            "diagnostics": diagnostics,
            "entry": attempt.entry,
            "execution_id": attempt.execution_id,
            "outputs": manifest_outputs,
            "path": relative_bundle.as_posix(),
        }
        bundle_text = _canonical_json(bundle_record)
        if (
            existing_bytes
            + copied_bytes
            + len(bundle_text.encode("utf-8"))
            > MAX_RUN_STAGING_BYTES
        ):
            raise ActionError(
                "reproduction.staging.resource_limit", "run staging limit exceeded"
            )
        (temporary / "bundle.json").write_text(bundle_text, encoding="utf-8")
        os.replace(temporary, bundle)
        _sync_directory(bundle.parent)
        _append_staging_manifest(plan, workspace, bundle_record)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return relative_bundle.as_posix()


def _append_staging_manifest(
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
    if any(
        (item.get("entry"), item.get("execution_id")) == identity
        for item in executions
        if isinstance(item, dict)
    ):
        raise ActionError("reproduction.staging.exists", str(identity))
    executions.append(dict(record))
    executions.sort(key=lambda item: (item["entry"], item["execution_id"]))
    serialized = _canonical_json(current)
    if len(serialized.encode("utf-8")) > MAX_STAGING_MANIFEST_BYTES:
        raise ActionError(
            "reproduction.staging.resource_limit", "staging manifest limit exceeded"
        )
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _recorded_staging_bytes(workspace: ReproductionWorkspace) -> int:
    path = workspace.run_root / "staging.json"
    if not path.exists():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        executions = value["executions"]
        sizes = [item["bytes"] for item in executions]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ActionError("reproduction.staging.invalid", str(error)) from error
    if not all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0
        for size in sizes
    ):
        raise ActionError("reproduction.staging.invalid", str(path))
    return sum(cast(list[int], sizes))


def _portable_stage_path(artifact: str) -> Path:
    if artifact.startswith("<project>/"):
        return Path("project").joinpath(
            *PurePosixPath(artifact.removeprefix("<project>/")).parts
        )
    return Path("entry").joinpath(*PurePosixPath(artifact).parts)


def _copy_available(source: Path, destination: Path, kind: str) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = _observed_fingerprint(source)
    if before is None:
        raise ActionError("reproduction.staging.source_unavailable", str(source))
    if kind == "file":
        if not source.is_file():
            raise ActionError("reproduction.staging.kind_changed", str(source))
        _regular_identity(source)
        shutil.copy2(source, destination)
        size = destination.stat().st_size
    elif kind == "directory":
        if not source.is_dir():
            raise ActionError("reproduction.staging.kind_changed", str(source))
        members = _directory_members(source)
        shutil.copytree(source, destination, symlinks=False)
        size = sum(
            path.stat().st_size
            for _, item_kind, path in members
            if item_kind == "file"
        )
    else:
        raise ActionError("reproduction.staging.kind_invalid", kind)
    after = _observed_fingerprint(source)
    copied = _observed_fingerprint(destination)
    if before != after or before != copied:
        raise ActionError(
            "reproduction.staging.copy_changed", f"staging copy changed: {source}"
        )
    return size


def _discard_matched_outputs(
    workspace: ReproductionWorkspace, outputs: Sequence[Path]
) -> None:
    work_root = workspace.work_project.resolve()
    for path in outputs:
        absolute = path.absolute()
        try:
            absolute.relative_to(work_root)
        except ValueError as error:
            raise ActionError(
                "reproduction.comparison.discard_outside_copy", str(path)
            ) from error
        if absolute.is_symlink():
            raise ActionError("reproduction.output.symlink", str(path))
        if absolute.is_dir():
            shutil.rmtree(absolute)
        elif absolute.exists():
            absolute.unlink()


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
                    digest, _ = observe_file_content(
                        path / PurePosixPath(member.path)
                    )
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


def _read_bounded(
    path: Path, *, maximum_memory: int = MAX_WORKING_MEMORY
) -> bytes:
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
