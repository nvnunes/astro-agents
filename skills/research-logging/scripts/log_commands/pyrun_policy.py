"""Researcher-facing lifecycle changes for command-oriented ``pyrun`` state."""

from __future__ import annotations

from pathlib import Path

from research_log_data import DataContractError, DataFile, load_data_file
from validation.commands import (
    CommandContext,
    Invocation,
    discover_commands,
    order_invocations,
)
from validation.errors import MechanicalContractError
from validation.pyrun_state import (
    PYRUN_FILENAME,
    ExecutionRecipe,
    PyrunStateError,
    execution_id,
    load_pyrun_state,
    recipe_from_invocation,
    update_slow_locked,
)

from .context import EntryContext, resolve_project_root
from .model import ActionError, ActionResult
from .scaffold import observe_entries
from .storage import entry_lock


def update_slow(
    entry: EntryContext, *, execution_id_value: str, slow: bool
) -> ActionResult:
    """Apply one Markdown-first ``slow`` classification under the entry lock."""

    project_root = resolve_project_root(entry.root)
    with entry_lock(entry):
        try:
            state = load_pyrun_state(
                entry.root / PYRUN_FILENAME,
                entry_root=entry.root,
                project_root=project_root,
            )
            candidates = _entry_invocations(entry, project_root=project_root)
            recipes = tuple(
                (
                    invocation,
                    recipe_from_invocation(
                        invocation,
                        entry_root=entry.root,
                        project_root=project_root,
                    ),
                )
                for invocation in candidates
                if invocation.outputs
                or any(
                    collection.direction == "output"
                    for collection in invocation.collections
                )
            )
        except (DataContractError, MechanicalContractError, OSError) as error:
            raise ActionError("pyrun.update.unavailable", str(error)) from error
        matching = [
            (invocation, recipe)
            for invocation, recipe in recipes
            if execution_id(recipe) == execution_id_value
        ]
        if len(matching) != 1:
            raise ActionError(
                "pyrun.update.command_unresolved",
                f"expected one current command for {execution_id_value}, "
                f"found {len(matching)}",
            )
        invocation, _ = matching[0]
        selected = _authored_invocation_group(invocation, recipes)
        selected_ids: list[str] = []
        for current, recipe in selected:
            if current.slow != slow:
                raise ActionError(
                    "pyrun.update.markdown_disagreement",
                    "edit only the Markdown --slow token before updating state",
                )
            identity = execution_id(recipe)
            recorded = state.executions.get(identity)
            if recorded is None or recorded.recipe != recipe:
                raise ActionError(
                    "pyrun.update.recipe_disagreement",
                    f"current Markdown recipe does not match {identity}",
                )
            if identity not in selected_ids:
                selected_ids.append(identity)
        changed = any(state.executions[key].slow != slow for key in selected_ids)
        if changed:
            try:
                update_slow_locked(
                    entry.root,
                    tuple(selected_ids),
                    slow=slow,
                    project_root=project_root,
                )
            except PyrunStateError as error:
                raise ActionError("pyrun.update.failed", str(error)) from error
    relative = (entry.root / PYRUN_FILENAME).relative_to(project_root).as_posix()
    return ActionResult(
        "pyrun.update",
        "updated" if changed else "unchanged",
        "pyrun.slow.updated" if changed else "pyrun.slow.unchanged",
        changed,
        (relative,),
    )


def _entry_invocations(
    entry: EntryContext, *, project_root: Path
) -> tuple[Invocation, ...]:
    """Discover every eligible current invocation in one selected entry."""

    observed = next(
        (item for item in observe_entries(entry.log) if item.id == entry.id), None
    )
    if observed is None or observed.root.resolve() != entry.root.resolve():
        raise ActionError("entry.identity.unresolved", entry.id)
    data = _load_data(entry.root)
    documents: list[tuple[Invocation, ...]] = []
    for document in observed.documents:
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ActionError("association.document_unavailable", str(error)) from error
        discovered = discover_commands(
            text,
            CommandContext(
                log_id=entry.log.root.as_posix(),
                entry=entry.id,
                document=document.relative_to(entry.log.root).as_posix(),
                entry_root=entry.root,
                log_root=entry.log.root,
                project_root=project_root,
                data_file=data,
            ),
        )
        if discovered.failures:
            failure = discovered.failures[0]
            raise ActionError(
                "pyrun.update.command_invalid",
                f"{document}: fence {failure.fence}, command {failure.ordinal}: "
                f"{failure.error}",
            )
        documents.append(discovered.invocations)
    return order_invocations(documents)


def _load_data(entry_root: Path) -> DataFile | None:
    path = entry_root / "data.json"
    return (
        load_data_file(path, entry_root=entry_root)
        if path.exists() or path.is_symlink()
        else None
    )


def _authored_invocation_group(
    selected: Invocation,
    recipes: tuple[tuple[Invocation, ExecutionRecipe], ...],
) -> tuple[tuple[Invocation, ExecutionRecipe], ...]:
    """Return one invocation or every concrete expansion of its static loop."""

    if not any(item.startswith("loop:") for item in selected.authored_group):
        return tuple(item for item in recipes if item[0] is selected)
    return tuple(
        item
        for item in recipes
        if item[0].document == selected.document
        and item[0].fence == selected.fence
        and item[0].authored_group == selected.authored_group
    )
