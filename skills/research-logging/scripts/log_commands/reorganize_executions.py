"""Relocate complete execution records in the owning registry transaction."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping

from effective_code import (
    EffectiveCodeError,
    EffectiveCodeFingerprint,
    relocate_effective_code,
)
from python_execution import PythonExecutionContext
from research_log_data import Fingerprint, input_token_parts, observe_file_content
from validation.pyrun_contract import recipe_script_parameters
from validation.pyrun_outputs import output_target_path, portable_output_path
from validation.pyrun_state import (
    PYRUN_FILENAME,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    build_execution_recipe,
    empty_pyrun_state,
    execution_id,
    load_pyrun_state,
    portable_script_path,
    script_target_path,
    validated_pyrun_serialization,
)

from .context import EntryContext, resolve_project_root
from .model import ActionError


def mapped_path(value: str, paths: Mapping[str, str]) -> str:
    """Apply the most specific explicit path mapping, including descendants."""

    for old in sorted(paths, key=len, reverse=True):
        if value == old:
            return paths[old]
        if value.startswith(old.rstrip("/") + "/"):
            return paths[old].rstrip("/") + value[len(old.rstrip("/")) :]
    return value


@dataclass
class ExecutionRelocation:
    """Explicit locator/name changes and the selected output observer."""

    source: EntryContext
    destination: EntryContext
    paths: Mapping[str, str]
    names: Mapping[str, str]
    material_paths: Mapping[str, str]
    observe_output: Callable[[Path, str], Fingerprint]
    _code_cache: dict[tuple[Path, Fingerprint], Fingerprint] = field(
        default_factory=dict, init=False, repr=False
    )

    def selected(self, value: str, selected_paths: set[str]) -> bool:
        if any(
            value == root or value.startswith(root.rstrip("/") + "/")
            for root in selected_paths
        ):
            return True
        project = resolve_project_root(self.source.root)
        original = output_target_path(
            value, entry_root=self.source.root, project_root=project, accepted=True
        )
        return any(
            root != self.source.root.as_posix() and original.is_relative_to(Path(root))
            for root in self.material_paths
        )

    def output(self, value: str) -> str:
        project = resolve_project_root(self.source.root)
        if value.startswith("<project>/"):
            original = output_target_path(
                value, entry_root=self.source.root, project_root=project, accepted=True
            )
            target = mapped_path(original.as_posix(), self.material_paths)
            return portable_output_path(
                target, entry_root=self.destination.root, project_root=project
            )
        return mapped_path(value, self.paths)

    def parameter(self, value: str) -> str:
        prefix, separator, payload = (
            value.partition("=") if value.startswith("-") else ("", "", value)
        )
        parts = input_token_parts(payload)
        if parts is not None:
            name, projection, member = parts
            suffix = f":{projection}" if projection else ""
            payload = f"<{self.names.get(name, name)}{suffix}>" + (
                f"/{member}" if member else ""
            )
        elif payload.startswith("<project>/"):
            payload = self.output(payload)
        else:
            payload = mapped_path(payload, self.paths)
            if Path(payload).is_absolute():
                payload = mapped_path(payload, self.material_paths)
        return prefix + separator + payload

    def parameters(self, execution: PyrunExecution) -> tuple[str, ...]:
        original = execution.recipe.parameters
        child = recipe_script_parameters(original)
        prefix = list(original[: len(original) - len(child)])
        for index in range(1, len(prefix), 2):
            prefix[index] = self.parameter(prefix[index])
        return tuple(prefix) + _map_child_parameters(
            child, dict(execution.recipe.parameter_roles), self.parameter
        )

    def effective_code(
        self, execution: PyrunExecution, script: Path
    ) -> Fingerprint | None:
        previous = execution.observed.effective_code
        if (
            previous is None
            or previous.digest is None
            or "PYTHONPATH" in dict(execution.recipe.environment)
        ):
            return previous
        cache_key = (script, previous)
        if cache_key in self._code_cache:
            return self._code_cache[cache_key]
        project = resolve_project_root(self.source.root)
        aliases = {
            self.destination.root.relative_to(
                project
            ).as_posix(): self.source.root.relative_to(project).as_posix()
        }
        aliases.update(
            {
                (self.destination.root / new).relative_to(project).as_posix(): (
                    self.source.root / old
                )
                .relative_to(project)
                .as_posix()
                for old, new in self.paths.items()
                if old.startswith("scripts/") and new.startswith("scripts/")
            }
        )
        original_script = script_target_path(
            execution.recipe.script, entry_root=self.source.root, project_root=project
        )
        aliases[script.relative_to(project).as_posix()] = original_script.relative_to(
            project
        ).as_posix()
        try:
            context = PythonExecutionContext.for_research_script(
                script,
                entry_root=self.destination.root,
                log_root=self.destination.log.root,
                project_root=project,
            )
            current = relocate_effective_code(
                script,
                project_root=project,
                import_roots=context.import_roots,
                previous=EffectiveCodeFingerprint(previous.algorithm, previous.digest),
                path_aliases=aliases,
            )
        except (EffectiveCodeError, OSError, ValueError):
            return previous
        result = Fingerprint(current.algorithm, current.digest)
        self._code_cache[cache_key] = result
        return result

    def execution(self, execution: PyrunExecution) -> PyrunExecution:
        project = resolve_project_root(self.source.root)
        script = mapped_path(execution.recipe.script, self.paths)
        if script.startswith("<project>/"):
            original = script_target_path(
                script, entry_root=self.source.root, project_root=project
            )
            script = portable_script_path(
                mapped_path(original.as_posix(), self.material_paths),
                entry_root=self.destination.root,
                project_root=project,
                authored=True,
            )
        target = script_target_path(
            script, entry_root=self.destination.root, project_root=project
        )
        source_script = script_target_path(
            execution.recipe.script, entry_root=self.source.root, project_root=project
        )
        source_digest, _ = observe_file_content(source_script)
        target_digest, _ = observe_file_content(target)
        if source_digest != target_digest:
            raise ActionError(
                "reorganize.transfer.script_changed",
                f"{script}: destination script differs from the current source copy",
            )
        outputs = {key: self.output(key) for key, _ in execution.recipe.outputs}
        observed_outputs = dict(execution.observed.outputs)
        for key, kind in execution.recipe.outputs:
            path = output_target_path(
                outputs[key],
                entry_root=self.destination.root,
                project_root=project,
                authored=True,
            )
            fingerprint = observed_outputs.get(key)
            if fingerprint is None:
                original = output_target_path(
                    key,
                    entry_root=self.source.root,
                    project_root=project,
                    accepted=True,
                )
                fingerprint = self.observe_output(original, kind)
            if self.observe_output(path, kind) != fingerprint:
                raise ActionError("data.fingerprint.mismatch", key)
        return replace(
            execution,
            recipe=build_execution_recipe(
                replace(
                    execution.recipe,
                    script=script,
                    parameters=self.parameters(execution),
                    inputs=tuple(
                        sorted(
                            self.names.get(name, name)
                            for name in execution.recipe.inputs
                        )
                    ),
                    outputs=tuple(
                        sorted(
                            (outputs[key], kind)
                            for key, kind in execution.recipe.outputs
                        )
                    ),
                ),
                subject=self.destination.root / PYRUN_FILENAME,
                entry_root=self.destination.root,
                project_root=project,
            ),
            observed=replace(
                execution.observed,
                effective_code=self.effective_code(execution, target),
                inputs=tuple(
                    sorted(
                        (self.names.get(name, name), value)
                        for name, value in execution.observed.inputs
                    )
                ),
                outputs=tuple(
                    sorted(
                        (outputs[key], value)
                        for key, value in execution.observed.outputs
                    )
                ),
            ),
        )


def _map_child_parameters(
    values: tuple[str, ...], roles: Mapping[str, str], mapper: Callable[[str], str]
) -> tuple[str, ...]:
    """Remap material arguments while retaining scalar arguments byte-for-byte."""

    result: list[str] = []
    index = 0
    positional = 0
    while index < len(values):
        token = values[index]
        if token.startswith("-") and token not in {"-", "--"}:
            name, separator, value = token.lstrip("-").partition("=")
            if separator:
                result.append(
                    token
                    if roles.get(name) == "ordinary"
                    else token.split("=", 1)[0] + "=" + mapper(value)
                )
            elif index + 1 < len(values) and not values[index + 1].startswith("-"):
                value = values[index + 1]
                result.extend(
                    (token, value if roles.get(name) == "ordinary" else mapper(value))
                )
                index += 1
            else:
                result.append(token)
        else:
            positional += 1
            result.append(
                token if roles.get(f"@{positional}") == "ordinary" else mapper(token)
            )
        index += 1
    return tuple(result)


@dataclass(frozen=True)
class ExecutionTransfer:
    """Coupled execution updates and physical boundaries guarded at publication."""

    updates: Mapping[Path, str | None]
    records: tuple[dict[str, object], ...]
    boundaries: tuple[Path, ...]


def transfer_execution_records(
    relocation: ExecutionRelocation, selected_paths: set[str]
) -> ExecutionTransfer:
    """Move full output bundles, preserving observations and execution policies."""

    source, destination = relocation.source, relocation.destination
    path = source.root / PYRUN_FILENAME
    if source == destination or not path.exists():
        return ExecutionTransfer({}, (), ())
    project = resolve_project_root(source.root)
    state = load_pyrun_state(path, entry_root=source.root, project_root=project)
    destination_path = destination.root / PYRUN_FILENAME
    target = (
        load_pyrun_state(
            destination_path, entry_root=destination.root, project_root=project
        )
        if destination_path.exists()
        else empty_pyrun_state(destination.root)
    )
    remaining = {
        cid: dict(command.executions) for cid, command in state.commands.items()
    }
    incoming = {
        cid: dict(command.executions) for cid, command in target.commands.items()
    }
    records: list[dict[str, object]] = []
    boundaries: list[Path] = []
    for cid, identity, execution in state.execution_items():
        outputs = tuple(key for key, _ in execution.recipe.outputs)
        selected = tuple(relocation.selected(key, selected_paths) for key in outputs)
        if not any(selected):
            continue
        if not all(selected):
            raise ActionError(
                "reorganize.transfer.support_ambiguous",
                f"transfer selects only part of execution {identity}",
            )
        moved = relocation.execution(execution)
        boundaries.extend(
            output_target_path(
                key, entry_root=source.root, project_root=project, accepted=True
            )
            for key in outputs
        )
        boundaries.extend(
            output_target_path(
                key, entry_root=destination.root, project_root=project, authored=True
            )
            for key, _ in moved.recipe.outputs
        )
        new_identity = execution_id(moved.recipe)
        bucket = incoming.setdefault(cid, {})
        prior = bucket.get(new_identity)
        if prior is not None and prior != moved:
            raise ActionError(
                "reorganize.transfer.execution_conflict",
                f"{cid}/{new_identity}: destination execution already exists",
            )
        bucket[new_identity] = moved
        del remaining[cid][identity]
        records.append(
            {
                "entry": destination.id,
                "cid": cid,
                "execution_id": new_identity,
                "source_entry": source.id,
                "source_execution_id": identity,
            }
        )
    if not records:
        return ExecutionTransfer({}, (), ())
    source_state = PyrunFile(
        path,
        source.root,
        {cid: PyrunCommand(values) for cid, values in remaining.items() if values},
    )
    target_state = PyrunFile(
        destination_path,
        destination.root,
        {cid: PyrunCommand(values) for cid, values in incoming.items()},
    )
    return ExecutionTransfer(
        {
            path: validated_pyrun_serialization(source_state, project_root=project)
            if source_state.commands
            else None,
            destination_path: validated_pyrun_serialization(
                target_state, project_root=project
            ),
        },
        tuple(records),
        tuple(boundaries),
    )
