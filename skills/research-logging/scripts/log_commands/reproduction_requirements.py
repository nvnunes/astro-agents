"""Reconcile accepted reproduction requirements with durable native completion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from validation.pyrun_state import (
    PyrunCommand,
    PyrunFile,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .context import LogContext, resolve_entry
from .model import ActionError
from .reproduction_domain import ExecutionRef
from .reproduction_execution import _utc_now
from .reproduction_work import CommandWork
from .reproduction_work_job import open_work_job
from .storage import atomic_write_text, entry_lock


def _clear_flag(log: LogContext, work: CommandWork) -> None:
    entry = resolve_entry(log, work.identity.entry)
    with entry_lock(entry):
        state = load_pyrun_state(
            entry.root / "pyrun.json",
            entry_root=entry.root,
            project_root=Path(work.project_root),
        )
        current = state.execution(work.identity.cid, work.identity.execution_id)
        if current is None:
            raise ActionError(
                "reproduction.requirement.execution_missing", work.identity.execution_id
            )
        if (
            current.recipe.as_dict() != work.execution.recipe.as_dict()
            or current.observed.as_dict() != work.execution.observed.as_dict()
        ):
            raise ActionError(
                "reproduction.requirement.execution_changed",
                "current execution no longer matches the accepted execution",
            )
        if not current.requires_reproduction:
            return
        command = state.commands[work.identity.cid]
        executions = dict(command.executions)
        executions[work.identity.execution_id] = replace(
            current, requires_reproduction=False
        )
        commands = dict(state.commands)
        commands[work.identity.cid] = PyrunCommand(executions)
        candidate = PyrunFile(state.path, state.entry_root, commands)
        atomic_write_text(
            state.path,
            validated_pyrun_serialization(
                candidate, project_root=Path(work.project_root)
            ),
        )


def clear_completed_requirement(
    log: LogContext, run_root: Path, identity: ExecutionRef
) -> bool:
    """Clear only complete accepted work, acknowledging after the atomic write.

    Keep the original job→entry lock order and exact recipe/observation guard.
    A lost acknowledgment retries an already-cleared flag without executing.
    Complete production may have unequal artifacts or comparison diagnostics.
    """

    with open_work_job(run_root) as job:
        if not job.requirement_clear_ready(identity):
            return False
        _clear_flag(log, job.accepted.plan.command(identity))
        job.acknowledge_requirement_clear(identity, cleared_at=_utc_now())
    return True


def clear_completed_requirements(log: LogContext, run_root: Path) -> None:
    """Reconcile all eligible accepted executions after comparison durability."""

    with open_work_job(run_root) as job:
        identities = tuple(work.identity for work in job.accepted.plan.commands)
    for identity in identities:
        clear_completed_requirement(log, run_root, identity)
