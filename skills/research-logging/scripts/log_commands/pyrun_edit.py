"""Markdown-first command corrections; only entry-owned pyrun.json is written."""

from __future__ import annotations

from dataclasses import replace
from difflib import unified_diff
from pathlib import Path

from research_log_data import (
    DataContractError,
    DataFile,
    Fingerprint,
    input_token_parts,
    load_data_file,
)
from validation.commands import Invocation
from validation.errors import MechanicalContractError
from validation.fingerprint_cache import FingerprintCache, FingerprintCacheError
from validation.pyrun_outputs import output_target_path, portable_output_path
from validation.pyrun_state import (
    PYRUN_FILENAME,
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    execution_id,
    load_pyrun_state,
    portable_script_path,
    recipe_from_invocation,
    script_target_path,
    validated_pyrun_serialization,
)

from .context import EntryContext, resolve_project_root
from .model import ActionError, ActionResult
from .pyrun_parameters import (
    CommandEdit,
    changed_parameter_roles,
    changed_parameters,
    parameter_spans,
    script_parameters,
    selected_spans,
)
from .pyrun_policy import entry_invocations
from .storage import atomic_write_text, entry_lock


def edit_command(
    entry: EntryContext, *, execution_id_value: str, edit: CommandEdit, dry_run: bool
) -> ActionResult:
    """Verify one explicit Markdown change and atomically replace its state record.

    Preserves policy and observations for unchanged materials. New materials must
    exist and be observable. No commands run, registries change, or Markdown is
    written. A corrected recipe has no recorded run and requires reproduction.
    """
    project = resolve_project_root(entry.root)
    try:
        with entry_lock(entry):
            return _edit_locked(entry, project, execution_id_value, edit, dry_run)
    except (
        DataContractError,
        MechanicalContractError,
        FingerprintCacheError,
        OSError,
    ) as error:
        raise ActionError("pyrun.edit.unavailable", str(error)) from error


def _edit_locked(
    entry: EntryContext, project: Path, identity: str, edit: CommandEdit, dry_run: bool
) -> ActionResult:
    path = entry.root / PYRUN_FILENAME
    state = load_pyrun_state(path, entry_root=entry.root, project_root=project)
    old = state.executions.get(identity)
    if old is None:
        raise ActionError("pyrun.edit.missing", f"no recorded execution {identity}")
    data_path = entry.root / "data.json"
    data = (
        load_data_file(data_path, entry_root=entry.root) if data_path.exists() else None
    )
    recipe = _matching_recipe(entry, old, edit, data, project)
    new_id = execution_id(recipe)
    if new_id != identity and new_id in state.executions:
        raise ActionError(
            "pyrun.edit.collision", "corrected execution ID already exists"
        )
    if recipe == old.recipe:
        raise ActionError(
            "pyrun.edit.unchanged", "requested edit does not change the recorded recipe"
        )
    with FingerprintCache(project, writable=False) as cache:
        observed = _observations(old, recipe, data, entry, cache)
    corrected = replace(
        old,
        recipe=recipe,
        observed=observed,
        requires_reproduction=True,
        last_run_at=None,
    )
    executions = dict(state.executions)
    del executions[identity]
    executions[new_id] = corrected
    text = validated_pyrun_serialization(
        replace(state, executions=executions), project_root=project
    )
    record: dict[str, object] = {
        "previous_execution_id": identity,
        "execution_id": new_id,
    }
    if dry_run:
        before = path.read_text(encoding="utf-8")
        record["diff"] = "".join(
            unified_diff(
                before.splitlines(True),
                text.splitlines(True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
    else:
        atomic_write_text(path, text)
    return ActionResult(
        "pyrun." + edit.action,
        "planned" if dry_run else "updated",
        "pyrun.edit.verified",
        not dry_run,
        (str(path),),
        (record,),
    )


def _has_outputs(invocation: Invocation) -> bool:
    return bool(invocation.outputs) or any(
        collection.direction == "output" for collection in invocation.collections
    )


def _matching_recipe(
    entry: EntryContext,
    old: PyrunExecution,
    edit: CommandEdit,
    data: DataFile | None,
    project: Path,
) -> ExecutionRecipe:
    expected_parameters = changed_parameters(old.recipe.parameters, edit)
    expected_roles = changed_parameter_roles(
        old.recipe.parameters, old.recipe.parameter_roles, edit
    )
    expected_script = old.recipe.script
    if edit.action == "set-script":
        expected_script = portable_script_path(
            str(edit.value), entry_root=entry.root, project_root=project, authored=True
        )
    matches: list[ExecutionRecipe] = []
    for invocation in entry_invocations(entry, project_root=project):
        if not _has_outputs(invocation):
            continue
        recipe = recipe_from_invocation(
            invocation, entry_root=entry.root, project_root=project
        )
        if (
            recipe.script != expected_script
            or recipe.environment != old.recipe.environment
        ):
            continue
        if (invocation.auto_reproduce, invocation.exclusive) != (
            old.auto_reproduce,
            old.exclusive,
        ):
            continue
        expected = _output_aliases(expected_parameters, invocation, entry, project)
        if _parameter_signature(recipe.parameters) != _parameter_signature(expected):
            continue
        if recipe.parameter_roles != expected_roles:
            continue
        if _material_changes_agree(old.recipe, recipe, edit, data, entry):
            matches.append(recipe)
    if len(matches) != 1:
        raise ActionError(
            "pyrun.edit.markdown_disagreement",
            "expected exactly one Markdown command matching only the requested edit; "
            f"found {len(matches)}",
        )
    return matches[0]


def _parameter_signature(tokens: tuple[str, ...]) -> tuple[tuple[str, str | None], ...]:
    # Accept equivalent equals/separate value spellings; preserve parameter order.
    return tuple(
        (
            span.selector
            if span.selector.startswith("@")
            else tokens[span.start].split("=", 1)[0],
            span.value,
        )
        for span in parameter_spans(tokens)
    )


def _output_aliases(
    tokens: tuple[str, ...], invocation: Invocation, entry: EntryContext, project: Path
) -> tuple[str, ...]:
    aliases = {
        f"<{item.input_resource.name}>": portable_output_path(
            item.input_resource.canonical_target,
            entry_root=entry.root,
            project_root=project,
        )
        for item in invocation.outputs
        if item.input_resource is not None
    }
    result = []
    for token in tokens:
        if token.startswith("-") and "=" in token:
            name, value = token.split("=", 1)
            result.append(f"{name}={aliases.get(value, value)}")
        else:
            result.append(aliases.get(token, token))
    return tuple(result)


def _material_changes_agree(
    old: ExecutionRecipe,
    new: ExecutionRecipe,
    edit: CommandEdit,
    data: DataFile | None,
    entry: EntryContext,
) -> bool:
    if edit.action == "set-script":
        return old.inputs == new.inputs and old.outputs == new.outputs
    _, tokens = script_parameters(old.parameters)
    values = {
        span.value for span in selected_spans(tokens, edit) if span.value is not None
    }
    if edit.value is not None:
        values.add(edit.value)
    names: set[str] = set()
    outputs: set[str] = set()
    for value in values:
        parts = input_token_parts(value)
        if parts is not None:
            names.add(parts[0])
            resource = data.by_name.get(parts[0]) if data else None
            if resource is not None:
                value = resource.canonical_target
        try:
            outputs.add(
                portable_output_path(
                    value,
                    entry_root=entry.root,
                    project_root=resolve_project_root(entry.root),
                    authored=parts is None,
                )
            )
        except MechanicalContractError:
            continue
    return (set(old.inputs) ^ set(new.inputs)) <= names and (
        set(old.outputs) ^ set(new.outputs)
    ) <= {(path, kind) for path in outputs for kind in ("file", "directory")}


def _observations(
    old: PyrunExecution,
    recipe: ExecutionRecipe,
    data: DataFile | None,
    entry: EntryContext,
    cache: FingerprintCache,
) -> ObservedExecution:
    old_inputs = dict(old.observed.inputs)
    inputs: list[tuple[str, Fingerprint]] = []
    for name in recipe.inputs:
        fingerprint = old_inputs.get(name)
        if fingerprint is None:
            resource = data.by_name.get(name) if data else None
            if resource is None:
                raise ActionError("pyrun.edit.input", f"input {name} is not registered")
            observation = cache.verify(resource)
            if observation is None:
                raise ActionError(
                    "pyrun.edit.input", f"input {name} cannot be observed"
                )
            fingerprint = observation.fingerprint
        inputs.append((name, fingerprint))
    outputs = _output_observations(old, recipe, entry, cache)
    script, code = old.observed.script, old.observed.code
    if recipe.script != old.recipe.script:
        path = script_target_path(
            recipe.script, entry_root=entry.root, project_root=cache.project_root
        )
        script, code = cache.observe_regular_file(path).fingerprint, ()
    return ObservedExecution(script, tuple(inputs), code, outputs)


def _output_observations(
    old: PyrunExecution,
    recipe: ExecutionRecipe,
    entry: EntryContext,
    cache: FingerprintCache,
) -> tuple[tuple[str, Fingerprint], ...]:
    prior = dict(old.observed.outputs)
    result = []
    for name, kind in recipe.outputs:
        fingerprint = prior.get(name) if (name, kind) in old.recipe.outputs else None
        if fingerprint is None:
            path = output_target_path(
                name, entry_root=entry.root, project_root=cache.project_root
            )
            observation = (
                cache.observe_directory(path)
                if kind == "directory"
                else cache.observe_regular_file(path)
            )
            fingerprint = observation.fingerprint
        result.append((name, fingerprint))
    return tuple(result)
