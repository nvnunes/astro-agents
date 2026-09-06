"""Strict command-oriented execution state owned by ``pyrun``."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NoReturn, cast

from research_log_data import DataContractError, Fingerprint, parse_fingerprint

from .errors import MechanicalContractError
from .json_codec import V2JsonError, decode_json
from .pyrun_contract import PYRUN_MANAGED_ENVIRONMENT
from .pyrun_outputs import (
    OutputSupport,
    PyrunOutputsFile,
    ScriptSupport,
    code_target_path,
    output_target_path,
    portable_code_path,
    portable_output_path,
)

if TYPE_CHECKING:
    from .commands import Invocation

PYRUN_SCHEMA = "research-log-pyrun/v1"
PYRUN_FILENAME = "pyrun.json"
PYRUN_RUNNER = "research-log-pyrun-runner/1"
PYRUN_ENVIRONMENT_PROFILE = "pyrun-standard/v1"
PYRUN_EXECUTION_CONTRACT = "research-log-pyrun-execution/1"
PYRUN_EXECUTION_PREFIX = "pyrun-exec/v1:"
PYRUN_EXECUTION_RE = re.compile(r"pyrun-exec/v1:[0-9a-f]{64}\Z")
PYRUN_BACKUP_RE = re.compile(r"pyrun\.json(?:\.[2-9][0-9]*)?\.bak\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
ENVIRONMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EXECUTIONS = 256
MAX_PARAMETERS = 4_096
MAX_INPUTS = 128
MAX_OUTPUTS = 256
MAX_ENVIRONMENT = 64
MAX_CODE_PATHS = 256
MAX_STRING_BYTES = 8 * 1024
MAX_PATH_BYTES = 2 * 1024


class PyrunStateError(MechanicalContractError):
    """One exact command-oriented execution-state contract failure."""


@dataclass(frozen=True)
class ExecutionRecipe:
    """The normalized structural recipe that determines an execution ID."""

    script: str
    parameters: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    inputs: tuple[str, ...]
    outputs: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted and identity projection."""

        return {
            "environment": dict(self.environment),
            "inputs": list(self.inputs),
            "outputs": dict(self.outputs),
            "parameters": list(self.parameters),
            "script": self.script,
        }


@dataclass(frozen=True)
class ObservedExecution:
    """Complete observations for one script, input, code, and output set."""

    script: Fingerprint
    inputs: tuple[tuple[str, Fingerprint], ...]
    code: tuple[tuple[str, Fingerprint], ...]
    outputs: tuple[tuple[str, Fingerprint], ...]

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted observation projection."""

        return {
            "code": {name: value.as_dict() for name, value in self.code},
            "inputs": {name: value.as_dict() for name, value in self.inputs},
            "outputs": {name: value.as_dict() for name, value in self.outputs},
            "script": self.script.as_dict(),
        }


@dataclass(frozen=True)
class PyrunExecution:
    """One complete current execution recipe and its observed state."""

    confirmed: bool
    slow: bool
    last_run_at: str | None
    runner: str
    environment_profile: str
    execution_contract: str
    recipe: ExecutionRecipe
    observed: ObservedExecution

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted execution projection."""

        return {
            "confirmed": self.confirmed,
            "environment_profile": self.environment_profile,
            "execution_contract": self.execution_contract,
            "last_run_at": self.last_run_at,
            "observed": self.observed.as_dict(),
            "recipe": self.recipe.as_dict(),
            "runner": self.runner,
            "slow": self.slow,
        }


@dataclass(frozen=True)
class PyrunFile:
    """One entry-owned mapping from stable execution IDs to current state."""

    path: Path
    entry_root: Path
    executions: Mapping[str, PyrunExecution]

    def as_dict(self) -> dict[str, object]:
        """Return the exact canonical file projection."""

        return {
            "executions": {
                key: self.executions[key].as_dict()
                for key in sorted(self.executions)
            },
            "schema": PYRUN_SCHEMA,
        }

    def serialized(self) -> str:
        """Return canonical UTF-8 JSON with one trailing newline."""

        return (
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )


def execution_id(recipe: ExecutionRecipe) -> str:
    """Return the stable v1 identity of one normalized execution recipe."""

    payload = json.dumps(
        recipe.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return PYRUN_EXECUTION_PREFIX + hashlib.sha256(payload).hexdigest()


def recipe_from_invocation(
    invocation: Invocation,
    *,
    entry_root: Path,
    project_root: Path,
) -> ExecutionRecipe:
    """Return the command-oriented recipe declared by one parsed invocation."""

    if invocation.script_argument is None:
        _invalid(invocation.document, {"reason": "script_missing"})
    script = portable_script_path(
        invocation.script_argument,
        entry_root=entry_root,
        project_root=project_root,
        authored=True,
    )
    inputs = tuple(
        sorted(
            {
                relationship.input_resource.name
                for relationship in invocation.inputs
                if relationship.input_resource is not None
            }
        )
    )
    outputs: dict[str, str] = {}
    directory_members: set[str] = set()
    for collection in invocation.collections:
        if collection.direction != "output" or collection.mechanism != "directory":
            continue
        if collection.root is None:
            _invalid(invocation.document, {"reason": "output_directory_root"})
        key = portable_output_path(
            collection.root,
            entry_root=entry_root,
            project_root=project_root,
        )
        outputs[key] = "directory"
        directory_members.update(collection.members)
    for relationship in invocation.outputs:
        if relationship.path in directory_members:
            continue
        key = portable_output_path(
            relationship.path,
            entry_root=entry_root,
            project_root=project_root,
        )
        prior = outputs.setdefault(key, "file")
        if prior != "file":
            _invalid(invocation.document, {"output": key, "reason": "kind_conflict"})
    recipe = ExecutionRecipe(
        script,
        invocation.recipe_parameters,
        invocation.environment,
        inputs,
        tuple(sorted(outputs.items())),
    )
    _decode_recipe(
        recipe.as_dict(),
        invocation.document,
        entry_root=entry_root,
        project_root=project_root,
    )
    return recipe


def ordinary_execution(
    recipe: ExecutionRecipe,
    observed: ObservedExecution,
    *,
    slow: bool,
    last_run_at: str,
) -> PyrunExecution:
    """Build the versioned state established by a successful ordinary run."""

    return PyrunExecution(
        confirmed=True,
        slow=slow,
        last_run_at=last_run_at,
        runner=PYRUN_RUNNER,
        environment_profile=PYRUN_ENVIRONMENT_PROFILE,
        execution_contract=PYRUN_EXECUTION_CONTRACT,
        recipe=recipe,
        observed=observed,
    )


def portable_script_path(
    value: str,
    *,
    entry_root: Path,
    project_root: Path | None = None,
    authored: bool = False,
) -> str:
    """Return one canonical entry-relative or log-relative script identity."""

    return _portable_script_path(
        value,
        entry_root=entry_root,
        project_root=project_root,
        authored=authored,
    )


def script_target_path(
    value: str, *, entry_root: Path, project_root: Path | None = None
) -> Path:
    """Resolve one canonical script identity without resolving symlinks."""

    key = _portable_script_path(
        value,
        entry_root=entry_root,
        project_root=project_root,
        authored=False,
    )
    if key.startswith("<project>/"):
        if project_root is None:
            _invalid(value, {"reason": "project_root_required"})
        return Path(os.path.abspath(project_root)).joinpath(
            *Path(key.removeprefix("<project>/")).parts
        )
    return code_target_path(key, entry_root=entry_root)


def validate_output_paths(
    outputs: tuple[str, ...],
    *,
    entry_root: Path,
    project_root: Path | None = None,
) -> None:
    """Reject duplicate, aliased, or ancestor-descendant output targets."""

    if not outputs or len(outputs) > MAX_OUTPUTS:
        _invalid(entry_root / PYRUN_FILENAME, {"outputs": len(outputs)})
    normalized = tuple(
        portable_output_path(
            value,
            entry_root=entry_root,
            project_root=project_root,
            authored=True,
        )
        for value in outputs
    )
    if len(normalized) != len(set(normalized)):
        _invalid(entry_root / PYRUN_FILENAME, {"reason": "duplicate_output"})
    targets = tuple(
        output_target_path(
            value,
            entry_root=entry_root,
            project_root=project_root,
            authored=True,
        ).absolute()
        for value in normalized
    )
    for index, left in enumerate(targets):
        for right in targets[index + 1 :]:
            if _paths_overlap(left, right):
                _invalid(
                    entry_root / PYRUN_FILENAME,
                    {"reason": "output_set_overlap"},
                )


def empty_pyrun_state(entry_root: Path) -> PyrunFile:
    """Return an empty command-oriented state surface for one entry."""

    root = entry_root.resolve()
    return PyrunFile(root / PYRUN_FILENAME, root, {})


def validated_pyrun_serialization(
    state: PyrunFile, *, project_root: Path | None = None
) -> str:
    """Return canonical bytes after enforcing the complete production contract.

    This is the publication boundary for callers that construct a complete
    multi-execution state before performing their own larger transaction. It
    applies the same decoder and ownership checks as ordinary ``pyrun``
    publication without writing the entry-owned file.
    """

    return _validated_serialization(state, project_root=project_root)


def legacy_output_projection(
    state: PyrunFile,
    invocations: tuple[Invocation, ...],
    *,
    project_root: Path,
) -> PyrunOutputsFile:
    """Project v1 execution state for pre-cutover validation consumers.

    This adapter exists only while the maintained corpus still contains legacy
    output support. Phase 4 removes it when every consumer reads executions
    directly.
    """

    current_parameters: dict[str, tuple[str, ...]] = {}
    for invocation in invocations:
        try:
            recipe = recipe_from_invocation(
                invocation,
                entry_root=state.entry_root,
                project_root=project_root,
            )
        except PyrunStateError:
            continue
        current_parameters.setdefault(execution_id(recipe), invocation.parameters)
    outputs: dict[str, OutputSupport] = {}
    for identity, execution in state.executions.items():
        parameters = current_parameters.get(
            identity, ("<pyrun-state-recipe-mismatch>",)
        )
        observed_outputs = dict(execution.observed.outputs)
        for output, _ in execution.recipe.outputs:
            outputs[output] = OutputSupport(
                execution.confirmed,
                observed_outputs[output],
                ScriptSupport(execution.recipe.script, execution.observed.script),
                parameters,
                execution.observed.inputs,
                execution.observed.code,
            )
    return PyrunOutputsFile(state.path, state.entry_root, outputs)


def load_pyrun_state(
    path: Path, *, entry_root: Path, project_root: Path | None = None
) -> PyrunFile:
    """Read one strict canonical entry-root ``pyrun.json`` file."""

    root = entry_root.resolve()
    expected = root / PYRUN_FILENAME
    if path.is_symlink() or path.resolve() != expected:
        _invalid(path, {"expected": str(expected), "reason": "location"})
    try:
        raw = path.read_text(encoding="utf-8")
        value = decode_json(raw, maximum_bytes=MAX_FILE_BYTES, subject=str(path))
    except (OSError, UnicodeError, V2JsonError) as error:
        _invalid(path, {"error": str(error)})
    if not isinstance(value, Mapping) or set(value) != {"executions", "schema"}:
        _invalid(path, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    raw_executions = value.get("executions")
    if value.get("schema") != PYRUN_SCHEMA or not isinstance(
        raw_executions, Mapping
    ):
        _invalid(path, {"schema": value.get("schema")})
    if not raw_executions or len(raw_executions) > MAX_EXECUTIONS:
        _invalid(
            path,
            {"executions": len(raw_executions), "limit": MAX_EXECUTIONS},
        )
    executions: dict[str, PyrunExecution] = {}
    for key, raw_execution in raw_executions.items():
        if not isinstance(key, str) or PYRUN_EXECUTION_RE.fullmatch(key) is None:
            _invalid(path, {"execution_id": key})
        execution = _decode_execution(
            raw_execution,
            f"{path}:executions[{key!r}]",
            entry_root=root,
            project_root=project_root,
        )
        if execution_id(execution.recipe) != key:
            _invalid(path, {"execution_id": key, "reason": "identity_mismatch"})
        executions[key] = execution
    result = PyrunFile(expected, root, executions)
    _validate_ownership(result, project_root=project_root)
    if raw != result.serialized():
        _invalid(path, {"reason": "noncanonical_serialization"})
    return result


def publish_execution_locked(
    entry_root: Path,
    execution: PyrunExecution,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically replace every overlapping owner under the entry lock."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    try:
        current = (
            load_pyrun_state(path, entry_root=root, project_root=project_root)
            if path.exists() or path.is_symlink()
            else empty_pyrun_state(root)
        )
        candidate_paths = _output_targets(
            execution.recipe, entry_root=root, project_root=project_root
        )
        executions = {
            key: value
            for key, value in current.executions.items()
            if not _target_sets_overlap(
                candidate_paths,
                _output_targets(
                    value.recipe, entry_root=root, project_root=project_root
                ),
            )
        }
        executions[execution_id(execution.recipe)] = execution
        result = PyrunFile(path, root, executions)
        serialized = _validated_serialization(result, project_root=project_root)
        _atomic_write(path, serialized)
        return result
    except OSError as error:
        raise PyrunStateError(
            "pyrun.state.unavailable",
            str(path),
            {"error": str(error)},
            "Pyrun Execution State",
        ) from error


def update_slow_locked(
    entry_root: Path,
    execution_ids: tuple[str, ...],
    *,
    slow: bool,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically change only ``slow`` for exact current executions."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    selected = tuple(dict.fromkeys(execution_ids))
    if not selected or len(selected) != len(execution_ids):
        _invalid(path, {"reason": "execution_selection_invalid"})
    missing = sorted(set(selected) - set(current.executions))
    if missing:
        _invalid(path, {"reason": "execution_missing", "executions": missing})
    executions = dict(current.executions)
    for key in selected:
        value = executions[key]
        executions[key] = PyrunExecution(
            value.confirmed,
            slow,
            value.last_run_at,
            value.runner,
            value.environment_profile,
            value.execution_contract,
            value.recipe,
            value.observed,
        )
    result = PyrunFile(path, root, executions)
    _atomic_write(path, _validated_serialization(result, project_root=project_root))
    return result


def without_executions(
    entry_root: Path,
    execution_ids: tuple[str, ...],
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Build validated state with complete selected executions retired."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    selected = tuple(dict.fromkeys(execution_ids))
    if not selected or len(selected) != len(execution_ids):
        _invalid(path, {"reason": "execution_selection_invalid"})
    missing = sorted(set(selected) - set(current.executions))
    if missing:
        _invalid(path, {"reason": "execution_missing", "executions": missing})
    result = PyrunFile(
        path,
        root,
        {
            key: value
            for key, value in current.executions.items()
            if key not in selected
        },
    )
    if result.executions:
        _validated_serialization(result, project_root=project_root)
    return result


def confirm_execution_locked(
    entry_root: Path,
    execution_id_value: str,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically confirm one execution without changing any other field."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    value = current.executions.get(execution_id_value)
    if value is None:
        _invalid(path, {"execution_id": execution_id_value, "reason": "missing"})
    executions = dict(current.executions)
    executions[execution_id_value] = PyrunExecution(
        True,
        value.slow,
        value.last_run_at,
        value.runner,
        value.environment_profile,
        value.execution_contract,
        value.recipe,
        value.observed,
    )
    result = PyrunFile(path, root, executions)
    _atomic_write(path, _validated_serialization(result, project_root=project_root))
    return result


def retire_execution_locked(
    entry_root: Path,
    execution_id_value: str,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically remove one explicitly selected complete execution."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    result = without_executions(
        root, (execution_id_value,), project_root=project_root
    )
    if result.executions:
        _atomic_write(path, _validated_serialization(result, project_root=project_root))
    else:
        _atomic_remove(path)
    return result


def quarantine_invalid_pyrun_state(
    entry_root: Path, *, project_root: Path | None = None
) -> None:
    """Preserve malformed state and require explicit Repair before execution."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        load_pyrun_state(path, entry_root=root, project_root=project_root)
        return
    try:
        load_pyrun_state(path, entry_root=root, project_root=project_root)
        return
    except PyrunStateError:
        pass
    backup = root / f"{PYRUN_FILENAME}.bak"
    number = 2
    while backup.exists() or backup.is_symlink():
        backup = root / f"{PYRUN_FILENAME}.{number}.bak"
        number += 1
    try:
        os.replace(path, backup)
        _sync_directory(root)
    except OSError as error:
        raise PyrunStateError(
            "pyrun.state.quarantine_failed",
            str(path),
            {"backup": str(backup), "error": str(error)},
            "Pyrun Execution State",
        ) from error
    raise PyrunStateError(
        "pyrun.state.quarantined",
        str(path),
        {"backup": str(backup), "repair_required": True},
        "Pyrun Execution State",
    )


def _decode_execution(
    value: object,
    subject: str,
    *,
    entry_root: Path,
    project_root: Path | None,
) -> PyrunExecution:
    fields = {
        "confirmed",
        "environment_profile",
        "execution_contract",
        "last_run_at",
        "observed",
        "recipe",
        "runner",
        "slow",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(subject, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    confirmed = value.get("confirmed")
    slow = value.get("slow")
    timestamp = value.get("last_run_at")
    if not isinstance(confirmed, bool) or not isinstance(slow, bool):
        _invalid(subject, {"confirmed": confirmed, "slow": slow})
    if timestamp is not None and not _valid_timestamp(timestamp):
        _invalid(subject, {"last_run_at": timestamp})
    if (
        value.get("runner") != PYRUN_RUNNER
        or value.get("environment_profile") != PYRUN_ENVIRONMENT_PROFILE
        or value.get("execution_contract") != PYRUN_EXECUTION_CONTRACT
    ):
        _invalid(subject, {"reason": "unsupported_version"})
    recipe = _decode_recipe(
        value.get("recipe"),
        subject,
        entry_root=entry_root,
        project_root=project_root,
    )
    observed = _decode_observed(
        value.get("observed"), recipe, subject, entry_root=entry_root
    )
    return PyrunExecution(
        confirmed,
        slow,
        cast(str | None, timestamp),
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
    )


def _decode_recipe(
    value: object,
    subject: str,
    *,
    entry_root: Path,
    project_root: Path | None,
) -> ExecutionRecipe:
    fields = {"environment", "inputs", "outputs", "parameters", "script"}
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(subject, {"recipe_fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    script = value.get("script")
    parameters = value.get("parameters")
    environment = value.get("environment")
    inputs = value.get("inputs")
    outputs = value.get("outputs")
    if not _bounded_path(script):
        _invalid(subject, {"script": script})
    script = _portable_script_path(
        cast(str, script),
        entry_root=entry_root,
        project_root=project_root,
        authored=False,
    )
    if (
        not isinstance(parameters, list)
        or len(parameters) > MAX_PARAMETERS
        or not all(_bounded_parameter(item) for item in parameters)
    ):
        _invalid(subject, {"parameters": parameters})
    decoded_environment = _decode_environment(environment, subject)
    if (
        not isinstance(inputs, list)
        or len(inputs) > MAX_INPUTS
        or not all(isinstance(item, str) and NAME_RE.fullmatch(item) for item in inputs)
        or inputs != sorted(set(inputs))
    ):
        _invalid(subject, {"inputs": inputs})
    if not isinstance(outputs, Mapping) or not outputs or len(outputs) > MAX_OUTPUTS:
        _invalid(subject, {"outputs": _fields(outputs)})
    decoded_outputs: list[tuple[str, str]] = []
    for key, kind in outputs.items():
        if not isinstance(key, str) or kind not in {"file", "directory"}:
            _invalid(subject, {"output": key, "kind": kind})
        canonical = portable_output_path(
            key,
            entry_root=entry_root,
            project_root=project_root,
            authored=True,
        )
        if canonical != key or not _bounded_path(key):
            _invalid(subject, {"output": key, "canonical": canonical})
        decoded_outputs.append((key, cast(str, kind)))
    recipe = ExecutionRecipe(
        script,
        tuple(cast(list[str], parameters)),
        tuple(sorted(decoded_environment)),
        tuple(cast(list[str], inputs)),
        tuple(sorted(decoded_outputs)),
    )
    _require_nonoverlapping_outputs(
        recipe, entry_root=entry_root, project_root=project_root, subject=subject
    )
    return recipe


def _decode_environment(
    value: object, subject: str
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or len(value) > MAX_ENVIRONMENT:
        _invalid(subject, {"environment": _fields(value)})
    result: list[tuple[str, str]] = []
    for name, raw in value.items():
        if (
            not isinstance(name, str)
            or ENVIRONMENT_RE.fullmatch(name) is None
            or name in PYRUN_MANAGED_ENVIRONMENT
            or not _bounded_parameter(raw)
        ):
            _invalid(subject, {"environment_name": name})
        result.append((name, cast(str, raw)))
    return tuple(sorted(result))


def _decode_observed(
    value: object,
    recipe: ExecutionRecipe,
    subject: str,
    *,
    entry_root: Path,
) -> ObservedExecution:
    fields = {"code", "inputs", "outputs", "script"}
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(subject, {"observed_fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    inputs = _decode_fingerprint_map(
        value.get("inputs"), subject, maximum=MAX_INPUTS
    )
    outputs = _decode_fingerprint_map(
        value.get("outputs"),
        subject,
        maximum=MAX_OUTPUTS,
        kinds=dict(recipe.outputs),
    )
    if tuple(name for name, _ in inputs) != recipe.inputs:
        _invalid(subject, {"reason": "observed_input_keys"})
    if tuple(name for name, _ in outputs) != tuple(name for name, _ in recipe.outputs):
        _invalid(subject, {"reason": "observed_output_keys"})
    code = _decode_code(value.get("code"), subject, entry_root=entry_root)
    return ObservedExecution(
        _decode_fingerprint(value.get("script"), subject, kind="file"),
        inputs,
        code,
        outputs,
    )


def _decode_fingerprint_map(
    value: object,
    subject: str,
    *,
    maximum: int,
    kinds: Mapping[str, str] | None = None,
) -> tuple[tuple[str, Fingerprint], ...]:
    if not isinstance(value, Mapping) or len(value) > maximum:
        _invalid(subject, {"fingerprints": _fields(value)})
    result: list[tuple[str, Fingerprint]] = []
    for name, raw in value.items():
        if not isinstance(name, str):
            _invalid(subject, {"fingerprint_key": name})
        result.append(
            (
                name,
                _decode_fingerprint(
                    raw, subject, kind=kinds.get(name) if kinds is not None else None
                ),
            )
        )
    return tuple(sorted(result))


def _decode_code(
    value: object, subject: str, *, entry_root: Path
) -> tuple[tuple[str, Fingerprint], ...]:
    if not isinstance(value, Mapping) or len(value) > MAX_CODE_PATHS:
        _invalid(subject, {"code": _fields(value)})
    result: list[tuple[str, Fingerprint]] = []
    resolved: set[Path] = set()
    for key, raw in value.items():
        if not isinstance(key, str) or not _bounded_path(key):
            _invalid(subject, {"code_path": key})
        canonical = portable_code_path(key, entry_root=entry_root)
        target = code_target_path(canonical, entry_root=entry_root).absolute()
        if canonical != key or target in resolved:
            _invalid(subject, {"code_path": key, "reason": "alias"})
        resolved.add(target)
        result.append((key, _decode_fingerprint(raw, subject, kind="file")))
    return tuple(sorted(result))


def _validated_serialization(
    value: PyrunFile, *, project_root: Path | None
) -> str:
    if not value.executions or len(value.executions) > MAX_EXECUTIONS:
        _invalid(value.path, {"executions": len(value.executions)})
    _validate_ownership(value, project_root=project_root)
    for key, execution in value.executions.items():
        if key != execution_id(execution.recipe):
            _invalid(value.path, {"execution_id": key, "reason": "identity_mismatch"})
        decoded = _decode_execution(
            execution.as_dict(),
            f"{value.path}:executions[{key!r}]",
            entry_root=value.entry_root,
            project_root=project_root,
        )
        if decoded != execution:
            _invalid(
                value.path,
                {"execution_id": key, "reason": "noncanonical_execution"},
            )
    serialized = value.serialized()
    if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
        _invalid(value.path, {"bytes": len(serialized.encode("utf-8"))})
    return serialized


def _validate_ownership(
    value: PyrunFile, *, project_root: Path | None
) -> None:
    owners: list[tuple[str, tuple[Path, ...]]] = []
    for key, execution in value.executions.items():
        targets = _output_targets(
            execution.recipe,
            entry_root=value.entry_root,
            project_root=project_root,
        )
        for prior_key, prior_targets in owners:
            if _target_sets_overlap(targets, prior_targets):
                _invalid(
                    value.path,
                    {
                        "executions": sorted((prior_key, key)),
                        "reason": "output_ownership_overlap",
                    },
                )
        owners.append((key, targets))


def _require_nonoverlapping_outputs(
    recipe: ExecutionRecipe,
    *,
    entry_root: Path,
    project_root: Path | None,
    subject: object,
) -> None:
    targets = _output_targets(
        recipe, entry_root=entry_root, project_root=project_root
    )
    for index, left in enumerate(targets):
        for right in targets[index + 1 :]:
            if _paths_overlap(left, right):
                _invalid(subject, {"reason": "output_set_overlap"})


def _output_targets(
    recipe: ExecutionRecipe,
    *,
    entry_root: Path,
    project_root: Path | None,
) -> tuple[Path, ...]:
    return tuple(
        output_target_path(
            key,
            entry_root=entry_root,
            project_root=project_root,
            authored=True,
        ).absolute()
        for key, _ in recipe.outputs
    )


def _target_sets_overlap(left: tuple[Path, ...], right: tuple[Path, ...]) -> bool:
    return any(_paths_overlap(first, second) for first in left for second in right)


def _paths_overlap(left: Path, right: Path) -> bool:
    return _within(left, right) or _within(right, left)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _portable_script_path(
    value: str,
    *,
    entry_root: Path,
    project_root: Path | None,
    authored: bool,
) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_PATH_BYTES:
        _invalid(value, {"reason": "script_path_invalid"})
    root = Path(os.path.abspath(entry_root))
    log = root.parent.parent
    lexical = _script_lexical_path(
        value,
        root=root,
        log=log,
        project_root=project_root,
    )
    canonical = _canonical_script_identity(
        value,
        lexical=lexical,
        root=root,
        log=log,
        project_root=project_root,
    )
    if not canonical.endswith(".py") or not authored and canonical != value:
        _invalid(value, {"canonical": canonical, "reason": "script_path_invalid"})
    return canonical


def _script_lexical_path(
    value: str,
    *,
    root: Path,
    log: Path,
    project_root: Path | None,
) -> Path:
    if value.startswith("<project>/"):
        if project_root is None:
            _invalid(value, {"reason": "script_path_invalid"})
        suffix = value.removeprefix("<project>/")
        project = Path(os.path.abspath(project_root))
        return project.joinpath(*_script_parts(value, suffix))
    if value.startswith("<log>/"):
        suffix = value.removeprefix("<log>/")
        return log.joinpath(*_script_parts(value, suffix))
    if Path(value).is_absolute():
        _invalid(value, {"reason": "script_path_invalid"})
    return root.joinpath(*_script_parts(value, value))


def _canonical_script_identity(
    value: str,
    *,
    lexical: Path,
    root: Path,
    log: Path,
    project_root: Path | None,
) -> str:
    relative = _relative_to(lexical, root)
    if relative is not None:
        return Path(*relative.parts).as_posix()
    relative = _relative_to(lexical, log)
    if relative is not None:
        return "<log>/" + Path(*relative.parts).as_posix()
    if project_root is None:
        _invalid(value, {"reason": "script_outside_log"})
    relative = _relative_to(lexical, Path(os.path.abspath(project_root)))
    if relative is None:
        _invalid(value, {"reason": "script_outside_project"})
    return "<project>/" + Path(*relative.parts).as_posix()


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _script_parts(value: str, suffix: str) -> tuple[str, ...]:
    parts = tuple(Path(suffix).parts)
    if (
        not suffix
        or suffix.startswith("/")
        or "\\" in suffix
        or any(character in suffix for character in "<>")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        _invalid(value, {"reason": "script_path_invalid"})
    return parts


def _decode_fingerprint(
    value: object, subject: str, *, kind: str | None = None
) -> Fingerprint:
    try:
        return parse_fingerprint(value, subject, kind=kind)
    except DataContractError as error:
        _invalid(subject, {"fingerprint": value, "reason": error.code})


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z") == value


def _bounded_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= MAX_PATH_BYTES
    )


def _bounded_parameter(value: object) -> bool:
    return isinstance(value, str) and len(value.encode("utf-8")) <= MAX_STRING_BYTES


def _fields(value: object) -> object:
    return sorted(value) if isinstance(value, Mapping) else type(value).__name__


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_remove(path: Path) -> None:
    path.unlink()
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _invalid(subject: object, observed: object) -> NoReturn:
    raise PyrunStateError(
        "pyrun.state.invalid",
        str(subject),
        observed,
        "Pyrun Execution State",
    )
