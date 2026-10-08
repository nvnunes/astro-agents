"""Read-only command execution state for ordinary Record and Repair work."""

from __future__ import annotations

from validation.pyrun_state import PyrunFile, load_pyrun_state

from .context import EntryContext, resolve_project_root
from .model import ActionResult


def _state(entry: EntryContext) -> PyrunFile | None:
    path = entry.root / "pyrun.json"
    return (
        load_pyrun_state(
            path, entry_root=entry.root, project_root=resolve_project_root(entry.root)
        )
        if path.exists() or path.is_symlink()
        else None
    )


def list_commands(entry: EntryContext) -> ActionResult:
    """Expose the recipe and execution policy needed for Record and Repair."""

    state = _state(entry)
    records = (
        tuple(
            {
                "cid": cid,
                "execution_id": identity,
                **execution.recipe.as_dict(),
                "auto_reproduce": execution.auto_reproduce,
                "exclusive": execution.exclusive,
                "requires_reproduction": execution.requires_reproduction,
            }
            for cid, identity, execution in state.execution_items()
        )
        if state
        else ()
    )
    return ActionResult(
        "command.list", "unchanged", "command.listed", False, records=records
    )


def list_reservations(
    entry: EntryContext, cid: str, reservation: str | None
) -> ActionResult:
    """Expose a read-only snapshot of selected invocation ownership."""

    from research_log_reservations import inspect_reservations

    records = inspect_reservations(
        resolve_project_root(entry.root), entry.root, cid, reservation=reservation
    )
    return ActionResult(
        "command.reservations",
        "unchanged",
        "command.reservations.listed",
        False,
        records=records,
    )
