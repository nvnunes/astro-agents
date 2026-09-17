"""Reconcile accepted current source with durable native completion."""

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
from .reproduction_domain import ArtifactOutcome, ExecutionRef
from .reproduction_execution import _utc_now
from .reproduction_work import CommandWork
from .reproduction_work_job import open_work_job
from .reproduction_work_plan import ReproductionPlan
from .storage import atomic_write_text, entry_lock


def _reconcile_state(
    log: LogContext, work: CommandWork, *, adopt_accepted_source: bool
) -> None:
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
        accepted_source = work.accepted_source if adopt_accepted_source else None
        accepted_observed = (
            replace(
                work.execution.observed,
                script=accepted_source.script,
                effective_code=accepted_source.effective_code,
            )
            if accepted_source is not None
            else work.execution.observed
        )
        if current.recipe.as_dict() != work.execution.recipe.as_dict() or (
            current.observed.as_dict() != work.execution.observed.as_dict()
            and current.observed.as_dict() != accepted_observed.as_dict()
        ):
            raise ActionError(
                "reproduction.requirement.execution_changed",
                "current execution no longer matches the accepted execution",
            )
        replacement = replace(
            current,
            requires_reproduction=False,
            observed=(
                replace(
                    current.observed,
                    script=accepted_source.script,
                    effective_code=accepted_source.effective_code,
                )
                if accepted_source is not None
                else current.observed
            ),
        )
        if replacement == current:
            return
        command = state.commands[work.identity.cid]
        executions = dict(command.executions)
        executions[work.identity.execution_id] = replacement
        commands = dict(state.commands)
        commands[work.identity.cid] = PyrunCommand(executions)
        candidate = PyrunFile(state.path, state.entry_root, commands)
        atomic_write_text(
            state.path,
            validated_pyrun_serialization(
                candidate, project_root=Path(work.project_root)
            ),
        )


def reconcile_completed_source(
    log: LogContext,
    run_root: Path,
    identity: ExecutionRef,
    *,
    accepted_plan: ReproductionPlan | None = None,
) -> bool:
    """Reconcile only complete accepted work, then acknowledge the atomic write.

    Keep the original job→entry lock order and exact recipe/observation guard.
    A lost acknowledgment retries an already-applied state without executing.
    Complete production may have unequal artifacts or comparison diagnostics.
    """

    with open_work_job(run_root) as job:
        plan = job.accepted.plan if accepted_plan is None else accepted_plan
        if not job.source_reconciliation_ready(identity, plan=plan):
            return False
        work = plan.command(identity)
        artifacts = tuple(
            artifact
            for artifact in plan.artifacts
            if artifact.producer == identity
        )
        results = tuple(
            job.load_artifact_result(artifact.identity) for artifact in artifacts
        )
        adopt_accepted_source = all(
            result is not None and result.outcome is ArtifactOutcome.MATCHED
            for result in results
        )
        _reconcile_state(
            log,
            work,
            adopt_accepted_source=adopt_accepted_source,
        )
        job.acknowledge_source_reconciliation(
            identity, reconciled_at=_utc_now(), plan=plan
        )
    return True


def reconcile_completed_sources(
    log: LogContext,
    run_root: Path,
    *,
    accepted_plan: ReproductionPlan | None = None,
) -> None:
    """Reconcile all eligible accepted executions after comparison durability."""

    if accepted_plan is None:
        with open_work_job(run_root) as job:
            accepted_plan = job.accepted.plan
    identities = tuple(work.identity for work in accepted_plan.commands)
    for identity in identities:
        reconcile_completed_source(
            log, run_root, identity, accepted_plan=accepted_plan
        )
