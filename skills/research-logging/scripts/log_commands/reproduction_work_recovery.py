"""Recover a dead native supervisor using unchanged physical cleanup rules.

The caller owns the accepted run's scope locks. Recovery never launches recipes,
replans, translates older jobs or invents a research failure for interrupted work.
Surviving processes keep the supervisor lease as durable overlap exclusion.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Literal

from .context import LogContext
from .reproduction_domain import WorkSelection
from .reproduction_execution import _utc_now
from .reproduction_job_control import (
    ExecutionIdentity,
    JobStoreInvariantError,
    RunFailure,
    RunOwner,
    RunStopCompletion,
    RunStopRequest,
)
from .reproduction_process_recovery import (
    _pid_alive,
    _recovery_worker_observations,
    _remove_current_scratch,
    _terminate_marked_workers,
)
from .reproduction_scheduler import (
    SchedulerIdentity,
    reconcile_permit,
    reconcile_work_admission,
)
from .reproduction_work_job import (
    AttemptInterruption,
    accepted_scheduling_projection,
    open_work_job,
)


def _recover_attempts(project_root: Path, run_root: Path, observed_at: str) -> None:
    reconcile_work_admission(project_root, run_root)
    with open_work_job(run_root) as job:
        plan = job.accepted.plan
        run_id = job.accepted.run_id
        identities = tuple(
            work.identity
            for work in plan.commands
            if work.selection is WorkSelection.RUN
        )
    for identity in identities:
        with open_work_job(run_root) as job:
            checkpoint = job.load_execution_checkpoint(identity)
            if checkpoint is None:
                continue
            if checkpoint.state == "active":
                if checkpoint.permit_id is None:
                    raise JobStoreInvariantError("active recovery has no permit")
                workers = tuple(
                    replace(
                        worker,
                        state="exited",
                        last_observed_at=max(observed_at, worker.last_observed_at),
                    )
                    for worker in job.load_execution_workers(identity)
                )
                job.record_execution_stop(
                    AttemptInterruption(
                        identity,
                        checkpoint.permit_id,
                        observed_at,
                        checkpoint.elapsed_seconds,
                        "The durable supervisor was interrupted.",
                        {},
                        workers,
                    )
                )
            permit_id = checkpoint.permit_id
            scratch = checkpoint.scratch_path
            accepted = accepted_scheduling_projection(
                plan,
                run_id,
                ExecutionIdentity(identity.entry, identity.cid, identity.execution_id),
            )
            proof = job.load_scheduler_owner()
        if permit_id is not None:
            reconciled = reconcile_permit(
                SchedulerIdentity(
                    project_root,
                    run_id,
                    identity.entry,
                    identity.cid,
                    identity.execution_id,
                    accepted.plan_order,
                ),
                proof,
            )
            if reconciled.clear_run_permit_id is not None:
                with open_work_job(run_root) as job:
                    job.clear_execution_permit(
                        identity,
                        reconciled.clear_run_permit_id,
                        checkpointed_at=observed_at,
                    )
        if scratch is not None:
            _remove_current_scratch(Path(scratch))
            with open_work_job(run_root) as job:
                job.clear_execution_scratch(
                    identity, scratch, checkpointed_at=observed_at
                )


def recover_work_job(
    log: LogContext, run_root: Path
) -> Literal["live", "incomplete", "recovered"]:
    """Reconcile one dead native owner while the caller holds run scope locks.

    A live running lease is non-mutating. An exhaustive marked-worker scan must
    prove cleanup before permits, scratch and terminal ownership are closed.
    Interrupted publication becomes a publication-only-resumable operational
    failure, keeping its frozen facts. Other interruptions become stopped unless
    a genuine operational failure intent already exists. No source reads occur.
    """

    with open_work_job(run_root) as job:
        if job.accepted.summary_identity != str(log.summary):
            raise JobStoreInvariantError("recovery log differs from accepted job")
        state = job.load_run_control()
        owner = job.load_run_owner()
        accepted_at = job.accepted.accepted_at
        run_id = job.accepted.run_id
        project_root = job.accepted.project_root
        if (
            owner is not None
            and owner.state == "running"
            and _pid_alive(owner.supervisor_pid)
        ):
            return "live"
        if state.status is not None and (owner is None or owner.state != "running"):
            return "recovered"
    observed_at = max(
        _utc_now(),
        accepted_at,
        owner.registered_at if owner else accepted_at,
        owner.last_observed_at if owner else accepted_at,
    )
    survivors = _terminate_marked_workers(run_id)
    with open_work_job(run_root) as job:
        if state.status is None:
            job.replace_recovery_workers(
                _recovery_worker_observations(survivors, observed_at=observed_at),
                observed_at=observed_at,
            )
        if survivors:
            return "incomplete"
        if state.status is not None:
            assert owner is not None
            job.replace_run_owner(
                RunOwner(
                    owner.supervisor_pid, "exited", owner.registered_at, observed_at
                )
            )
            return "recovered"
        if state.phase == "publishing":
            if job.load_publication() is None:
                raise JobStoreInvariantError("interrupted publication has no journal")
            job.request_run_failure(
                RunFailure(
                    "reproduction.publication.failed",
                    "The durable supervisor was interrupted during publication.",
                    observed_at,
                )
            )
        elif state.phase != "stopping":
            job.request_run_stop(RunStopRequest(observed_at))
            job.acknowledge_run_stop()
    _recover_attempts(project_root, run_root, observed_at)
    with open_work_job(run_root) as job:
        job.replace_run_owner(
            RunOwner(
                owner.supervisor_pid if owner else os.getpid(),
                "exited" if state.phase == "publishing" else "stopped",
                owner.registered_at if owner else accepted_at,
                observed_at,
            )
        )
        job.finish_run_stop(RunStopCompletion(observed_at))
    return "recovered"
