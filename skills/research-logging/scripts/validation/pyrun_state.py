"""Strict command-oriented execution state owned by ``pyrun``."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NoReturn, cast

from effective_code import FINGERPRINT_ALGORITHM
from research_log_data import DataContractError, Fingerprint, parse_fingerprint

from .errors import MechanicalContractError
from .file_publication import atomic_replace_text, install_path, remove_file
from .json_codec import V2JsonError, decode_json
from .pyrun_contract import (
    PYRUN_MANAGED_ENVIRONMENT,
    PyrunContractError,
    effective_parameter_roles,
    recipe_script_parameters,
)
from .pyrun_outputs import (
    canonical_output_path,
    output_target_path,
    portable_output_path,
)

if TYPE_CHECKING:
    from .commands import Invocation

PYRUN_SCHEMA = "research-log-pyrun/v7"
PYRUN_FILENAME = "pyrun.json"
PYRUN_RUNNER = "research-log-pyrun-runner/1"
PYRUN_RECOVERY_RUNNER = "research-log-pyrun-agent-confirmed-recovery/1"
PYRUN_ENVIRONMENT_PROFILE = "pyrun-standard/v1"
PYRUN_EXECUTION_CONTRACT = "research-log-pyrun-execution/2"
PYRUN_EXECUTION_PREFIX = "pyrun-exec/v2:"
PYRUN_EXECUTION_RE = re.compile(r"pyrun-exec/v2:[0-9a-f]{64}\Z")
PYRUN_BACKUP_RE = re.compile(r"pyrun\.json(?:\.[2-9][0-9]*)?\.bak\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
ENVIRONMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EXECUTIONS = 256
MAX_PARAMETERS = 4_096
MAX_INPUTS = 128
MAX_OUTPUTS = 256
MAX_ENVIRONMENT = 64
MAX_STRING_BYTES = 8 * 1024
MAX_PATH_BYTES = 2 * 1024


class PyrunStateError(MechanicalContractError):
    """One exact command-oriented execution-state contract failure."""


@dataclass(frozen=True)
class ExecutionRecipe:
    """The normalized structural recipe compared separately from parameter ID."""

    script: str
    parameters: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    inputs: tuple[str, ...]
    outputs: tuple[tuple[str, str], ...]
    parameter_roles: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted and identity projection."""

        return {
            "environment": dict(self.environment),
            "inputs": list(self.inputs),
            "outputs": dict(self.outputs),
            "parameters": list(self.parameters),
            "parameter_roles": dict(self.parameter_roles),
            "script": self.script,
        }


@dataclass(frozen=True)
class ObservedExecution:
    """Available observations for one script, input, code tree, and output set."""

    script: Fingerprint | None
    inputs: tuple[tuple[str, Fingerprint], ...]
    effective_code: Fingerprint | None
    outputs: tuple[tuple[str, Fingerprint], ...]

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted observation projection."""

        return {
            "effective_code": (
                self.effective_code.as_dict()
                if self.effective_code is not None
                else None
            ),
            "inputs": {name: value.as_dict() for name, value in self.inputs},
            "outputs": {name: value.as_dict() for name, value in self.outputs},
            "script": self.script.as_dict() if self.script is not None else None,
        }


@dataclass(frozen=True)
class PyrunExecution:
    """One complete current execution recipe and its observed state."""

    requires_reproduction: bool
    auto_reproduce: bool
    last_run_at: str | None
    runner: str
    environment_profile: str
    execution_contract: str
    recipe: ExecutionRecipe
    observed: ObservedExecution
    exclusive: bool = False

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted execution projection."""

        return {
            "auto_reproduce": self.auto_reproduce,
            "environment_profile": self.environment_profile,
            "execution_contract": self.execution_contract,
            "exclusive": self.exclusive,
            "last_run_at": self.last_run_at,
            "observed": self.observed.as_dict(),
            "recipe": self.recipe.as_dict(),
            "requires_reproduction": self.requires_reproduction,
            "runner": self.runner,
        }


@dataclass(frozen=True)
class PyrunCommand:
    """One CID-owned parameter execution mapping."""

    executions: Mapping[str, PyrunExecution]

    def as_dict(self) -> dict[str, object]:
        """Return the exact persisted command projection."""

        return {
            "executions": {
                key: self.executions[key].as_dict() for key in sorted(self.executions)
            }
        }


@dataclass(frozen=True)
class PyrunFile:
    """One entry-owned mapping from CIDs to parameter executions."""

    path: Path
    entry_root: Path
    commands: Mapping[str, PyrunCommand]
    schema: str = PYRUN_SCHEMA

    def as_dict(self) -> dict[str, object]:
        """Return the exact canonical file projection."""

        if self.schema != PYRUN_SCHEMA:
            raise PyrunStateError(
                "pyrun.state.schema.unsupported",
                str(self.path),
                {"schema": self.schema, "supported": PYRUN_SCHEMA},
                "Pyrun Execution State",
            )
        return {
            "commands": {
                cid: self.commands[cid].as_dict() for cid in sorted(self.commands)
            },
            "schema": self.schema,
        }

    def execution(self, cid: str, identity: str) -> PyrunExecution | None:
        """Return one complete-key execution, if present."""

        command = self.commands.get(cid)
        return command.executions.get(identity) if command is not None else None

    def execution_items(self) -> tuple[tuple[str, str, PyrunExecution], ...]:
        """Return all executions in canonical CID and parameter order."""

        return tuple(
            (cid, identity, command.executions[identity])
            for cid in sorted(self.commands)
            for command in (self.commands[cid],)
            for identity in sorted(command.executions)
        )

    def serialized(self) -> str:
        """Return canonical UTF-8 JSON with one trailing newline."""

        return (
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )


@dataclass(frozen=True)
class ExecutionAssociation:
    """One current command associated with its exact persisted execution."""

    cid: str
    identity: str
    execution: PyrunExecution


@dataclass(frozen=True)
class OutputOwnerIndex:
    """One entry's canonical output identities mapped to execution owners."""

    entry_root: Path
    owners: Mapping[str, ExecutionAssociation]


@dataclass(frozen=True)
class ResolvedExecutionOutput:
    """One output or directory-member path resolved against execution state."""

    subject: str
    key: str
    path: Path
    association: ExecutionAssociation | None
    owner: ExecutionAssociation | None


@dataclass(frozen=True)
class CurrentExecution:
    """One current expanded invocation and its canonical recipe."""

    identity: str
    invocation: Invocation
    recipe: ExecutionRecipe


@dataclass(frozen=True)
class ExecutionChange:
    """One stored/current member pair with a changed recipe or policy."""

    current: CurrentExecution
    stored: PyrunExecution


@dataclass(frozen=True)
class CommandComparison:
    """Disjoint comparison of one current CID and its stored bucket."""

    missing: tuple[CurrentExecution, ...]
    stale: tuple[ExecutionAssociation, ...]
    recipe_changed: tuple[ExecutionChange, ...]
    policy_changed: tuple[ExecutionChange, ...]
    unchanged: tuple[ExecutionAssociation, ...]


def compare_command(
    state: PyrunFile,
    cid: str,
    invocations: tuple[Invocation, ...],
    *,
    project_root: Path,
) -> CommandComparison:
    """Compare one current CID against its stored parameter executions."""

    current: dict[str, CurrentExecution] = {}
    for invocation in invocations:
        if invocation.cid != cid:
            _invalid(invocation.document, {"cid": invocation.cid, "expected": cid})
        recipe = recipe_from_invocation(
            invocation, entry_root=state.entry_root, project_root=project_root
        )
        identity = execution_id(recipe)
        if identity in current:
            _invalid(
                invocation.document,
                {"cid": cid, "execution_id": identity, "reason": "parameter_collision"},
            )
        current[identity] = CurrentExecution(identity, invocation, recipe)
    stored = state.commands.get(cid, PyrunCommand({})).executions
    missing: list[CurrentExecution] = []
    recipe_changed: list[ExecutionChange] = []
    policy_changed: list[ExecutionChange] = []
    unchanged: list[ExecutionAssociation] = []
    for identity, member in sorted(current.items()):
        prior = stored.get(identity)
        if prior is None:
            missing.append(member)
        elif prior.recipe != member.recipe:
            recipe_changed.append(ExecutionChange(member, prior))
        elif (
            prior.auto_reproduce != member.invocation.auto_reproduce
            or prior.exclusive != member.invocation.exclusive
        ):
            policy_changed.append(ExecutionChange(member, prior))
        else:
            unchanged.append(ExecutionAssociation(cid, identity, prior))
    stale = tuple(
        ExecutionAssociation(cid, identity, stored[identity])
        for identity in sorted(set(stored) - set(current))
    )
    return CommandComparison(
        tuple(missing),
        stale,
        tuple(recipe_changed),
        tuple(policy_changed),
        tuple(unchanged),
    )


def pending_execution(current: CurrentExecution) -> PyrunExecution:
    """Create a recipe-only member without manufacturing observations."""

    return PyrunExecution(
        True,
        current.invocation.auto_reproduce,
        None,
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        current.recipe,
        ObservedExecution(None, (), None, ()),
        current.invocation.exclusive,
    )


def changed_execution(change: ExecutionChange) -> PyrunExecution:
    """Retain only historical observations applicable to the current recipe."""

    old = change.stored
    new = change.current.recipe
    if old.recipe == new:
        return replace(
            old,
            auto_reproduce=change.current.invocation.auto_reproduce,
            exclusive=change.current.invocation.exclusive,
        )
    same_script = old.recipe.script == new.script
    same_effective_code_context = (
        same_script
        and "PYTHONPATH" not in dict(old.recipe.environment)
        and "PYTHONPATH" not in dict(new.environment)
    )
    inputs = tuple(
        (name, value) for name, value in old.observed.inputs if name in set(new.inputs)
    )
    output_contract = dict(new.outputs)
    outputs = tuple(
        (name, value)
        for name, value in old.observed.outputs
        if dict(old.recipe.outputs).get(name) == output_contract.get(name)
    )
    return PyrunExecution(
        True,
        change.current.invocation.auto_reproduce,
        None,
        old.runner,
        old.environment_profile,
        old.execution_contract,
        new,
        ObservedExecution(
            old.observed.script if same_script else None,
            inputs,
            (old.observed.effective_code if same_effective_code_context else None),
            outputs,
        ),
        change.current.invocation.exclusive,
    )


def associate_execution(
    state: PyrunFile, invocation: Invocation, *, project_root: Path
) -> ExecutionAssociation | None:
    """Return the exact current execution for one authored command, if present.

    The association is identity-based. A malformed or changed invocation is not
    projected into a synthetic legacy support record.
    """

    try:
        recipe = recipe_from_invocation(
            invocation, entry_root=state.entry_root, project_root=project_root
        )
    except PyrunStateError:
        return None
    identity = execution_id(recipe)
    execution = state.execution(invocation.cid, identity)
    return (
        ExecutionAssociation(invocation.cid, identity, execution)
        if execution is not None
        else None
    )


def associate_exact_execution(
    state: PyrunFile, invocation: Invocation, *, project_root: Path
) -> ExecutionAssociation | None:
    """Return an execution only when its complete canonical recipe still matches."""

    association = associate_execution(state, invocation, project_root=project_root)
    if association is None:
        return None
    recipe = recipe_from_invocation(
        invocation,
        entry_root=state.entry_root,
        project_root=project_root,
    )
    return association if association.execution.recipe == recipe else None


def execution_output_owners(state: PyrunFile) -> OutputOwnerIndex:
    """Index every persisted output identity by its owning execution."""

    owners: dict[str, ExecutionAssociation] = {}
    for cid, identity, execution in state.execution_items():
        association = ExecutionAssociation(cid, identity, execution)
        for output, _ in execution.recipe.outputs:
            owners[output] = association
    return OutputOwnerIndex(state.entry_root, owners)


def resolve_execution_output(
    invocation: Invocation,
    material: str,
    *,
    project_root: Path,
    association: ExecutionAssociation | None,
    owners: OutputOwnerIndex,
) -> ResolvedExecutionOutput:
    """Resolve an output or directory member and retain its state association."""

    material_path = Path(material).resolve()
    subject = material
    key = portable_output_path(
        material_path, entry_root=owners.entry_root, project_root=project_root
    )
    owner = owners.owners.get(key)
    if owner is None:
        covering = {
            Path(collection.root).resolve()
            for collection in invocation.collections
            if collection.direction == "output"
            and collection.mechanism == "directory"
            and collection.root is not None
            and _within(material_path, Path(collection.root).resolve())
        }
        if len(covering) > 1:
            _invalid(
                material,
                {"reason": "ambiguous_output_directory"},
            )
        if covering:
            path = next(iter(covering))
            key = portable_output_path(
                path, entry_root=owners.entry_root, project_root=project_root
            )
            owner = owners.owners.get(key)
            material_path = path
            subject = path.as_posix()
    return ResolvedExecutionOutput(
        subject, key, material_path, owner if owner == association else None, owner
    )


def execution_id(recipe: ExecutionRecipe | tuple[str, ...]) -> str:
    """Return the stable v2 identity of normalized child-script parameters."""

    payload = json.dumps(
        list(
            recipe_script_parameters(recipe.parameters)
            if isinstance(recipe, ExecutionRecipe)
            else recipe
        ),
        ensure_ascii=False,
        separators=(",", ":"),
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
        kind = (
            relationship.input_resource.kind
            if relationship.input_resource is not None
            else "file"
        )
        prior = outputs.setdefault(key, kind)
        if prior != kind:
            _invalid(invocation.document, {"output": key, "reason": "kind_conflict"})
    parameters = invocation.recipe_parameters or invocation.parameters
    return build_execution_recipe(
        ExecutionRecipe(
            script,
            parameters,
            invocation.environment,
            inputs,
            tuple(sorted(outputs.items())),
            invocation.parameter_roles,
        ),
        subject=invocation.document,
        entry_root=entry_root,
        project_root=project_root,
    )


def build_execution_recipe(
    recipe: ExecutionRecipe,
    *,
    subject: object,
    entry_root: Path,
    project_root: Path | None,
) -> ExecutionRecipe:
    """Build and validate one canonical recipe for every producer path."""

    _decode_recipe(
        recipe.as_dict(),
        str(subject),
        entry_root=entry_root,
        project_root=project_root,
    )
    return recipe


def ordinary_execution(
    recipe: ExecutionRecipe,
    observed: ObservedExecution,
    *,
    auto_reproduce: bool,
    last_run_at: str,
    exclusive: bool = False,
) -> PyrunExecution:
    """Build the versioned state established by a successful ordinary run."""

    return PyrunExecution(
        requires_reproduction=False,
        auto_reproduce=auto_reproduce,
        last_run_at=last_run_at,
        runner=PYRUN_RUNNER,
        environment_profile=PYRUN_ENVIRONMENT_PROFILE,
        execution_contract=PYRUN_EXECUTION_CONTRACT,
        recipe=recipe,
        observed=observed,
        exclusive=exclusive,
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
    if key.startswith("<log>/"):
        return Path(os.path.abspath(entry_root)).parent.parent.joinpath(
            *Path(key.removeprefix("<log>/")).parts
        )
    return Path(os.path.abspath(entry_root)).joinpath(*Path(key).parts)


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
    except (OSError, UnicodeError) as error:
        _invalid(path, {"error": str(error)})
    return parse_pyrun_state_text(
        raw,
        subject=path,
        entry_root=root,
        project_root=project_root,
    )


def parse_pyrun_state_text(
    raw: str,
    *,
    subject: object,
    entry_root: Path,
    project_root: Path | None = None,
) -> PyrunFile:
    """Decode canonical pyrun bytes for a known entry without a path alias."""

    root = entry_root.resolve()
    expected = root / PYRUN_FILENAME
    try:
        value = decode_json(raw, maximum_bytes=MAX_FILE_BYTES, subject=str(subject))
    except V2JsonError as error:
        _invalid(subject, {"error": str(error)})
    if not isinstance(value, Mapping):
        _invalid(subject, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    schema = value.get("schema")
    if schema != PYRUN_SCHEMA:
        raise PyrunStateError(
            "pyrun.state.schema.unsupported",
            str(subject),
            {"schema": schema, "supported": PYRUN_SCHEMA},
            "Pyrun Execution State",
        )
    if set(value) != {"commands", "schema"}:
        _invalid(subject, {"fields": _fields(value)})
    commands = _decode_commands(
        value.get("commands"),
        subject=subject,
        entry_root=root,
        project_root=project_root,
    )
    result = PyrunFile(expected, root, commands)
    _validate_ownership(result, project_root=project_root)
    if raw != result.serialized():
        _invalid(subject, {"reason": "noncanonical_serialization"})
    return result


def _decode_commands(
    raw_commands: object,
    *,
    subject: object,
    entry_root: Path,
    project_root: Path | None,
) -> dict[str, PyrunCommand]:
    """Decode every bounded CID bucket in one current state file."""

    if not isinstance(raw_commands, Mapping):
        _invalid(subject, {"schema": PYRUN_SCHEMA})
    if not raw_commands or len(raw_commands) > MAX_EXECUTIONS:
        _invalid(
            subject,
            {"commands": len(raw_commands), "limit": MAX_EXECUTIONS},
        )
    commands: dict[str, PyrunCommand] = {}
    for cid, raw_command in raw_commands.items():
        decoded_cid = _decode_cid(cid, subject)
        commands[decoded_cid] = _decode_command(
            decoded_cid,
            raw_command,
            subject=subject,
            entry_root=entry_root,
            project_root=project_root,
        )
    execution_count = sum(len(command.executions) for command in commands.values())
    if execution_count > MAX_EXECUTIONS:
        _invalid(subject, {"executions": execution_count, "limit": MAX_EXECUTIONS})
    return commands


def _decode_cid(value: object, subject: object) -> str:
    if not isinstance(value, str) or NAME_RE.fullmatch(value) is None:
        _invalid(subject, {"cid": value})
    return value


def _decode_command(
    cid: object,
    raw_command: object,
    *,
    subject: object,
    entry_root: Path,
    project_root: Path | None,
) -> PyrunCommand:
    if not isinstance(raw_command, Mapping) or set(raw_command) != {"executions"}:
        _invalid(subject, {"cid": cid, "fields": _fields(raw_command)})
    raw_executions = raw_command.get("executions")
    if not isinstance(raw_executions, Mapping) or not raw_executions:
        _invalid(subject, {"cid": cid, "executions": _fields(raw_executions)})
    executions: dict[str, PyrunExecution] = {}
    for key, raw_execution in raw_executions.items():
        identity = _decode_execution_id(key, cid=cid, subject=subject)
        execution = _decode_execution(
            raw_execution,
            f"{subject}:commands[{cid!r}]:executions[{identity!r}]",
            entry_root=entry_root,
            project_root=project_root,
        )
        if execution_id(execution.recipe) != identity:
            _invalid(
                subject,
                {"cid": cid, "execution_id": identity, "reason": "identity_mismatch"},
            )
        executions[identity] = execution
    return PyrunCommand(executions)


def _decode_execution_id(value: object, *, cid: object, subject: object) -> str:
    if not isinstance(value, str) or PYRUN_EXECUTION_RE.fullmatch(value) is None:
        _invalid(subject, {"cid": cid, "execution_id": value})
    return value


def parse_pyrun_execution(
    value: object,
    *,
    subject: object,
    entry_root: Path,
    project_root: Path | None = None,
    accepted: bool = False,
) -> PyrunExecution:
    """Decode one execution; accepted records use frozen roots, not live paths."""

    execution = _decode_execution(
        value,
        str(subject),
        entry_root=Path(os.path.abspath(entry_root))
        if accepted
        else entry_root.resolve(),
        project_root=project_root,
        accepted=accepted,
    )
    if PYRUN_EXECUTION_RE.fullmatch(str(subject)) is not None and (
        execution_id(execution.recipe) != subject
    ):
        _invalid(subject, {"reason": "identity_mismatch"})
    return execution


def publish_execution_locked(
    current: PyrunFile,
    cid: str,
    execution: PyrunExecution,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Publish one execution into an already validated locked snapshot."""

    root = current.entry_root.resolve()
    path = root / PYRUN_FILENAME
    try:
        if current.path != path or current.schema != PYRUN_SCHEMA:
            _invalid(path, {"reason": "invalid_publication_snapshot"})
        if NAME_RE.fullmatch(cid) is None:
            _invalid(path, {"cid": cid, "reason": "invalid"})
        identity = execution_id(execution.recipe)
        decoded = _decode_execution(
            execution.as_dict(),
            f"{path}:commands[{cid!r}]:executions[{identity!r}]",
            entry_root=root,
            project_root=project_root,
        )
        if decoded != execution or execution_id(decoded.recipe) != identity:
            _invalid(
                path,
                {"execution_id": identity, "reason": "noncanonical_execution"},
            )
        targets = _output_targets(
            execution.recipe, entry_root=root, project_root=project_root
        )
        for prior_cid, prior_identity, prior in current.execution_items():
            if (prior_cid, prior_identity) == (cid, identity):
                continue
            prior_targets = _output_targets(
                prior.recipe, entry_root=root, project_root=project_root
            )
            if _target_sets_overlap(targets, prior_targets):
                _invalid(
                    path,
                    {
                        "executions": sorted(
                            ((prior_cid, prior_identity), (cid, identity))
                        ),
                        "reason": "output_ownership_overlap",
                    },
                )
        commands = dict(current.commands)
        command = commands.get(cid, PyrunCommand({}))
        executions = dict(command.executions)
        executions[identity] = execution
        commands[cid] = PyrunCommand(executions)
        result = PyrunFile(path, root, commands)
        execution_count = len(result.execution_items())
        if execution_count > MAX_EXECUTIONS:
            _invalid(
                path,
                {"executions": execution_count, "limit": MAX_EXECUTIONS},
            )
        serialized = result.serialized()
        if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
            _invalid(path, {"bytes": len(serialized.encode("utf-8"))})
        _atomic_write(path, serialized)
        return result
    except OSError as error:
        raise PyrunStateError(
            "pyrun.state.unavailable",
            str(path),
            {"error": str(error)},
            "Pyrun Execution State",
        ) from error


def update_auto_reproduce_locked(
    entry_root: Path,
    cid: str,
    execution_ids: tuple[str, ...],
    *,
    auto_reproduce: bool,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically change only automatic-reproduction policy."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    selected = tuple(dict.fromkeys(execution_ids))
    if not selected or len(selected) != len(execution_ids):
        _invalid(path, {"reason": "execution_selection_invalid"})
    command = current.commands.get(cid)
    existing = command.executions if command is not None else {}
    missing = sorted(set(selected) - set(existing))
    if missing:
        _invalid(path, {"reason": "execution_missing", "executions": missing})
    executions = dict(existing)
    for key in selected:
        value = executions[key]
        executions[key] = replace(value, auto_reproduce=auto_reproduce)
    result = _with_command_executions(current, cid, executions)
    _atomic_write(path, _validated_serialization(result, project_root=project_root))
    return result


def update_exclusive_locked(
    entry_root: Path,
    cid: str,
    execution_ids: tuple[str, ...],
    *,
    exclusive: bool,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically change only managed-reproduction exclusivity policy."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    selected = tuple(dict.fromkeys(execution_ids))
    if not selected or len(selected) != len(execution_ids):
        _invalid(path, {"reason": "execution_selection_invalid"})
    command = current.commands.get(cid)
    existing = command.executions if command is not None else {}
    missing = sorted(set(selected) - set(existing))
    if missing:
        _invalid(path, {"reason": "execution_missing", "executions": missing})
    executions = dict(existing)
    for key in selected:
        executions[key] = replace(executions[key], exclusive=exclusive)
    result = _with_command_executions(current, cid, executions)
    _atomic_write(path, _validated_serialization(result, project_root=project_root))
    return result


def without_executions(
    entry_root: Path,
    cid: str,
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
    command = current.commands.get(cid)
    existing = command.executions if command is not None else {}
    missing = sorted(set(selected) - set(existing))
    if missing:
        _invalid(path, {"reason": "execution_missing", "executions": missing})
    result = _with_command_executions(
        current,
        cid,
        {key: value for key, value in existing.items() if key not in selected},
    )
    if result.commands:
        _validated_serialization(result, project_root=project_root)
    return result


def clear_reproduction_requirement_locked(
    entry_root: Path,
    cid: str,
    execution_id_value: str,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically clear one execution's reproduction requirement."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    current = load_pyrun_state(path, entry_root=root, project_root=project_root)
    value = current.execution(cid, execution_id_value)
    if value is None:
        _invalid(path, {"execution_id": execution_id_value, "reason": "missing"})
    executions = dict(current.commands[cid].executions)
    executions[execution_id_value] = replace(value, requires_reproduction=False)
    result = _with_command_executions(current, cid, executions)
    _atomic_write(path, _validated_serialization(result, project_root=project_root))
    return result


def retire_execution_locked(
    entry_root: Path,
    cid: str,
    execution_id_value: str,
    *,
    project_root: Path | None = None,
) -> PyrunFile:
    """Atomically remove one explicitly selected complete execution."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    result = without_executions(
        root, cid, (execution_id_value,), project_root=project_root
    )
    if result.commands:
        _atomic_write(path, _validated_serialization(result, project_root=project_root))
    else:
        _atomic_remove(path)
    return result


def _with_command_executions(
    state: PyrunFile,
    cid: str,
    executions: Mapping[str, PyrunExecution],
) -> PyrunFile:
    commands = dict(state.commands)
    if executions:
        commands[cid] = PyrunCommand(dict(executions))
    else:
        commands.pop(cid, None)
    return PyrunFile(state.path, state.entry_root, commands)


def quarantine_invalid_pyrun_state(
    entry_root: Path, *, project_root: Path | None = None
) -> PyrunFile:
    """Return valid initial state or preserve malformed state for Repair."""

    root = entry_root.resolve()
    path = root / PYRUN_FILENAME
    if not path.exists() and not path.is_symlink():
        return empty_pyrun_state(root)
    if path.is_symlink() or not path.is_file():
        return load_pyrun_state(path, entry_root=root, project_root=project_root)
    try:
        return load_pyrun_state(path, entry_root=root, project_root=project_root)
    except PyrunStateError:
        pass
    backup = root / f"{PYRUN_FILENAME}.bak"
    number = 2
    while backup.exists() or backup.is_symlink():
        backup = root / f"{PYRUN_FILENAME}.{number}.bak"
        number += 1
    try:
        install_path(path, backup)
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
    accepted: bool = False,
) -> PyrunExecution:
    fields = {
        "environment_profile",
        "execution_contract",
        "last_run_at",
        "observed",
        "recipe",
        "runner",
        "auto_reproduce",
        "exclusive",
        "requires_reproduction",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(subject, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    requires_reproduction = value.get("requires_reproduction")
    auto_reproduce = value.get("auto_reproduce")
    exclusive = value.get("exclusive")
    timestamp = value.get("last_run_at")
    if (
        not isinstance(requires_reproduction, bool)
        or not isinstance(auto_reproduce, bool)
        or not isinstance(exclusive, bool)
    ):
        _invalid(
            subject,
            {
                "auto_reproduce": auto_reproduce,
                "requires_reproduction": requires_reproduction,
            },
        )
    if timestamp is not None and not _valid_timestamp(timestamp):
        _invalid(subject, {"last_run_at": timestamp})
    if (
        value.get("runner") not in {PYRUN_RUNNER, PYRUN_RECOVERY_RUNNER}
        or value.get("environment_profile") != PYRUN_ENVIRONMENT_PROFILE
        or value.get("execution_contract") != PYRUN_EXECUTION_CONTRACT
    ):
        _invalid(subject, {"reason": "unsupported_version"})
    recipe = _decode_recipe(
        value.get("recipe"),
        subject,
        entry_root=entry_root,
        project_root=project_root,
        accepted=accepted,
    )
    observed = _decode_observed(
        value,
        recipe,
        subject,
        entry_root=entry_root,
        accepted=accepted,
    )
    return PyrunExecution(
        requires_reproduction,
        auto_reproduce,
        cast(str, timestamp) if timestamp is not None else None,
        cast(str, value["runner"]),
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
        exclusive,
    )


def _decode_recipe(
    value: object,
    subject: str,
    *,
    entry_root: Path,
    project_root: Path | None,
    accepted: bool = False,
) -> ExecutionRecipe:
    fields = {
        "environment",
        "inputs",
        "outputs",
        "parameters",
        "script",
        "parameter_roles",
    }
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
    if not isinstance(outputs, Mapping) or len(outputs) > MAX_OUTPUTS:
        _invalid(subject, {"outputs": _fields(outputs)})
    decoded_outputs: list[tuple[str, str]] = []
    for key, kind in outputs.items():
        if not isinstance(key, str) or kind not in {"file", "directory"}:
            _invalid(subject, {"output": key, "kind": kind})
        canonical = (
            canonical_output_path(
                key,
                entry_root=entry_root,
                project_root=project_root or entry_root,
            )
            if accepted
            else portable_output_path(
                key,
                entry_root=entry_root,
                project_root=project_root,
                authored=True,
            )
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
        _decode_parameter_roles(value.get("parameter_roles"), parameters, subject),
    )
    _require_nonoverlapping_outputs(
        recipe,
        entry_root=entry_root,
        project_root=project_root,
        subject=subject,
        accepted=accepted,
    )
    return recipe


def _decode_parameter_roles(
    value: object, parameters: list[str], subject: object
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and isinstance(role, str)
        and role in {"input", "output", "ordinary"}
        for key, role in value.items()
    ):
        _invalid(subject, {"reason": "parameter_roles"})
    try:
        effective = effective_parameter_roles(
            recipe_script_parameters(parameters), value
        )
    except PyrunContractError as error:
        _invalid(subject, {"reason": "parameter_roles", "error": str(error)})
    if dict(effective) != value:
        _invalid(subject, {"reason": "incomplete_parameter_roles"})
    return effective


def _decode_environment(value: object, subject: str) -> tuple[tuple[str, str], ...]:
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
    accepted: bool = False,
) -> ObservedExecution:
    execution = cast(Mapping[str, Any], value)
    allow_partial = execution["requires_reproduction"]
    value = execution.get("observed")
    fields = {"effective_code", "inputs", "outputs", "script"}
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(subject, {"observed_fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    inputs = _decode_fingerprint_map(value.get("inputs"), subject, maximum=MAX_INPUTS)
    outputs = _decode_fingerprint_map(
        value.get("outputs"),
        subject,
        maximum=MAX_OUTPUTS,
        kinds=dict(recipe.outputs),
    )
    input_names = tuple(name for name, _ in inputs)
    output_names = tuple(name for name, _ in outputs)
    if (not allow_partial and input_names != recipe.inputs) or not set(
        input_names
    ).issubset(recipe.inputs):
        _invalid(subject, {"reason": "observed_input_keys"})
    recipe_output_names = tuple(name for name, _ in recipe.outputs)
    if (not allow_partial and output_names != recipe_output_names) or not set(
        output_names
    ).issubset(recipe_output_names):
        _invalid(subject, {"reason": "observed_output_keys"})
    effective_code = _decode_effective_code(value.get("effective_code"), subject)
    raw_script = value.get("script")
    script = (
        None
        if raw_script is None and allow_partial
        else _decode_fingerprint(raw_script, subject, kind="file")
    )
    return ObservedExecution(
        script,
        inputs,
        effective_code,
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


def _decode_effective_code(value: object, subject: str) -> Fingerprint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"algorithm", "digest"}:
        _invalid(subject, {"effective_code": value})
    algorithm = value.get("algorithm")
    digest = value.get("digest")
    if algorithm != FINGERPRINT_ALGORITHM or not isinstance(digest, str):
        _invalid(subject, {"effective_code": value})
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        _invalid(subject, {"effective_code": value})
    return Fingerprint(FINGERPRINT_ALGORITHM, digest=digest)


def _validated_serialization(value: PyrunFile, *, project_root: Path | None) -> str:
    items = value.execution_items()
    if not items or len(items) > MAX_EXECUTIONS:
        _invalid(value.path, {"executions": len(items)})
    _validate_ownership(value, project_root=project_root)
    for cid, key, execution in items:
        if key != execution_id(execution.recipe):
            _invalid(value.path, {"execution_id": key, "reason": "identity_mismatch"})
        decoded = _decode_execution(
            execution.as_dict(),
            f"{value.path}:commands[{cid!r}]:executions[{key!r}]",
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


def _validate_ownership(value: PyrunFile, *, project_root: Path | None) -> None:
    owners: list[tuple[str, str, tuple[Path, ...]]] = []
    for cid, key, execution in value.execution_items():
        targets = _output_targets(
            execution.recipe,
            entry_root=value.entry_root,
            project_root=project_root,
        )
        for prior_cid, prior_key, prior_targets in owners:
            if _target_sets_overlap(targets, prior_targets):
                _invalid(
                    value.path,
                    {
                        "executions": sorted(((prior_cid, prior_key), (cid, key))),
                        "reason": "output_ownership_overlap",
                    },
                )
        owners.append((cid, key, targets))


def _require_nonoverlapping_outputs(
    recipe: ExecutionRecipe,
    *,
    entry_root: Path,
    project_root: Path | None,
    subject: object,
    accepted: bool = False,
) -> None:
    targets = _output_targets(
        recipe, entry_root=entry_root, project_root=project_root, accepted=accepted
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
    accepted: bool = False,
) -> tuple[Path, ...]:
    return tuple(
        (
            (project_root or entry_root) / key.removeprefix("<project>/")
            if accepted and key.startswith("<project>/")
            else entry_root / key
            if accepted
            else output_target_path(
                key,
                entry_root=entry_root,
                project_root=project_root,
                authored=True,
            )
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
    atomic_replace_text(path, text)


def _atomic_remove(path: Path) -> None:
    remove_file(path)


def _invalid(subject: object, observed: object) -> NoReturn:
    raise PyrunStateError(
        "pyrun.state.invalid",
        str(subject),
        observed,
        "Pyrun Execution State",
    )
