"""Supervise fixed native work through the shared coordinator and publication.

Scheduling state is transient; accepted work, actual results and interrupted
attempt recovery remain owned by Job6. No route replans. Successful execution
publishes durable native facts; publication-only retry consumes frozen completion
without execution. The caller owns scope locks and installs the supervisor lease.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal

from .context import LogContext
from .model import ActionError
from .reproduction_domain import CommandOutcome, ExecutionRef
from .reproduction_execution import (
    ConfinementBackend,
    DarwinSeatbelt,
    ReproductionControlPlaneError,
    ReproductionWorkspace,
    _fingerprint,
    _materialize_outputs,
    _PreparationOptions,
    _prepare_execution,
    _reset_current_stopped_workspace,
    _utc_now,
    open_current_workspace,
    populate_current_output_workspace,
)
from .reproduction_job_control import (
    ExecutionIdentity,
    JobStoreTransitionError,
    RunFailure,
    RunOwner,
    RunStopCompletion,
    RunStopRequest,
)
from .reproduction_run import CommandResult
from .reproduction_scheduler import (
    POLL_SECONDS,
    SchedulerIdentity,
    SchedulerPermitRequest,
    _accepted_scheduler_claims,
    poll_work_permit,
    reconcile_permit,
)
from .reproduction_work_execution import (
    WorkExecutionControl,
    _generated_paths,
    _require_accepted_workspace,
    _source,
    compare_work_outputs,
    execute_work_recipe,
)
from .reproduction_work_job import open_work_job
from .reproduction_work_plan import ReproductionPlan
from .reproduction_work_publication import publish_work_job


@dataclass(frozen=True)
class WorkPlanControl:
    """Native stage controls; fixed jobs and timeout come from accepted settings.

    The caller must hold the run scope locks and have registered this running
    supervisor PID. A local stop request is made durable before canceling waits.
    ``resume`` permits only clean stopped attempts, never active started work.
    Production defaults to the existing Darwin confinement backend.
    """

    supervisor_pid: int
    resume: bool = False
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None


@dataclass(frozen=True)
class _WorkStage:
    log: LogContext
    plan: ReproductionPlan
    workspace: ReproductionWorkspace
    control: WorkPlanControl
    backend: ConfinementBackend


def _stop_requested(stage: _WorkStage) -> bool:
    local_stop = stage.control.stop_requested()
    with open_work_job(stage.workspace.run_root) as job:
        state = job.load_run_control()
        if local_stop and state.status is None and state.phase != "stopping":
            job.request_run_stop(RunStopRequest(_utc_now()))
        return (
            local_stop
            or state.stop_requested_at is not None
            or state.phase == "stopping"
        )


def _completed_work(stage: _WorkStage, identity: ExecutionRef) -> bool:
    """Recover unchanged actual results; no fabricated checkpoint outcome."""

    with open_work_job(stage.workspace.run_root) as job:
        checkpoint = job.load_execution_checkpoint(identity)
        result = job.load_command_result(identity)
    if checkpoint is not None:
        if checkpoint.state == "active" and checkpoint.started_at is not None:
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.checkpoint.recovery_required",
                    "active started work requires owner recovery",
                )
            )
        if checkpoint.state in {"completed", "stopped"} and (
            checkpoint.permit_id is not None or checkpoint.scratch_path is not None
        ):
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.checkpoint.recovery_required",
                    "terminal attempt requires grant/scratch recovery",
                )
            )
        if checkpoint.state == "stopped":
            if not stage.control.resume:
                raise ReproductionControlPlaneError(
                    ActionError(
                        "reproduction.checkpoint.invalid",
                        "fresh execution cannot reuse stopped work",
                    )
                )
            _reset_current_stopped_workspace(
                stage.workspace, identity.entry, identity.cid, identity.execution_id
            )
    if result is None:
        return False
    if result.outcome is CommandOutcome.SUCCEEDED:
        _reuse_completed_outputs(stage, result)
        _compare_completed_output(stage, result.identity)
    return True


def _compare_completed_output(stage: _WorkStage, identity: ExecutionRef) -> None:
    """Compare before dependants run, unless a concurrent stop won the race."""

    try:
        compare_work_outputs(stage.workspace, identity)
    except JobStoreTransitionError:
        if _stop_requested(stage):
            return
        raise


def _reuse_completed_outputs(stage: _WorkStage, result: CommandResult) -> None:
    """Preserve original private-output fingerprint and materialization guards."""

    work = stage.plan.command(result.identity)
    source = _source(stage.log, stage.plan, work)
    prepared = _prepare_execution(
        stage.log,
        result.identity,
        stage.workspace,
        _generated_paths(stage.plan, stage.workspace),
        _PreparationOptions(source),
    )
    kinds = dict(work.execution.recipe.outputs)
    try:
        current = set(result.outputs) == set(prepared.output_paths) and all(
            not path.is_symlink()
            and path.exists()
            and _fingerprint(path, kinds[name]).as_dict() == result.outputs[name]
            for name, path in prepared.output_paths.items()
        )
    except (OSError, ValueError):
        current = False
    if not current:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.checkpoint.changed",
                "completed native outputs are not reusable",
            )
        )
    _materialize_outputs(prepared, stage.workspace, source)


def _release_completed_grant(
    stage: _WorkStage, identity: SchedulerIdentity, grant: str
) -> None:
    with open_work_job(stage.workspace.run_root) as job:
        owner = job.load_scheduler_owner()
    reconciliation = reconcile_permit(identity, owner)
    if reconciliation.clear_run_permit_id != grant:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.scheduler.reconciliation_required",
                "terminal permit was not released exactly",
            )
        )


def _execute_scheduled_work(
    stage: _WorkStage, identity: ExecutionRef
) -> CommandResult | None:
    workspace = stage.workspace
    with open_work_job(workspace.run_root) as job:
        accepted = job.load_accepted_scheduling(
            ExecutionIdentity(identity.entry, identity.cid, identity.execution_id)
        )
        prior = job.load_execution_checkpoint(identity)
    expected: Literal["absent", "stopped"] = (
        "stopped" if prior is not None and prior.state == "stopped" else "absent"
    )
    key = SchedulerIdentity(
        workspace.source_project,
        workspace.run_id,
        identity.entry,
        identity.cid,
        identity.execution_id,
        accepted.plan_order,
    )
    request = SchedulerPermitRequest(
        key,
        accepted.kind,
        stage.control.supervisor_pid,
        *_accepted_scheduler_claims(
            accepted, workspace.run_root, workspace.source_project
        ),
        _utc_now(),
    )
    while True:
        if _stop_requested(stage):
            with open_work_job(workspace.run_root) as job:
                owner = job.load_scheduler_owner()
            reconcile_permit(key, owner)
            return None
        decision = poll_work_permit(
            workspace.run_root,
            replace(request, polled_at=_utc_now()),
            checkpointed_at=_utc_now(),
            expected_state=expected,
        )
        if decision.disposition == "granted":
            assert decision.permit is not None
            grant = decision.permit.permit_id
            result = execute_work_recipe(
                stage.log,
                identity,
                workspace,
                WorkExecutionControl(
                    grant,
                    lambda: _release_completed_grant(stage, key, grant),
                    stage.plan.settings.execution_timeout_seconds,
                    lambda: _stop_requested(stage),
                    stage.backend,
                ),
            )
            if (
                result is not None
                and result.outcome is CommandOutcome.SUCCEEDED
                and not _stop_requested(stage)
            ):
                _compare_completed_output(stage, result.identity)
            return result
        time.sleep(POLL_SECONDS)


def _resolve_pending(
    stage: _WorkStage, pending: list[ExecutionRef]
) -> list[ExecutionRef]:
    ready: list[ExecutionRef] = []
    with open_work_job(stage.workspace.run_root) as job:
        if job.load_run_control().phase == "stopping":
            return ready
        for identity in tuple(pending):
            disposition = job.load_execution_readiness(identity).disposition
            if disposition == "dependency_failed":
                job.record_dependency_block(identity)
                pending.remove(identity)
            elif disposition == "ready":
                ready.append(identity)
    return ready


def _launch_ready(
    stage: _WorkStage,
    pool: ThreadPoolExecutor,
    pending: list[ExecutionRef],
    running: dict[Future[CommandResult | None], ExecutionRef],
    ready: list[ExecutionRef],
) -> None:
    slots = stage.plan.settings.jobs - len(running)
    if slots <= 0:
        return
    exclusive = [key for key in ready if stage.plan.command(key).execution.exclusive]
    if exclusive and running:
        return
    launchable = exclusive[:1] if exclusive else ready[:slots]
    for identity in launchable:
        running[pool.submit(_execute_scheduled_work, stage, identity)] = identity
        pending.remove(identity)


def execute_work_plan(
    log: LogContext,
    workspace: ReproductionWorkspace,
    control: WorkPlanControl,
) -> Literal["completed", "stopped"]:
    """Drain fixed native work, preserving dependency/exclusive/reuse decisions.

    Durable results are not copied into a second batch accounting model. Failed
    prerequisites create only a blocked result with exact links and no attempt.
    Stopping drains supervised futures before returning; the caller then closes
    run ownership and stop state. Active started attempts require prior recovery.
    """

    with open_work_job(workspace.run_root) as job:
        _require_accepted_workspace(job.accepted, workspace)
        owner = job.load_run_owner()
        if (
            owner is None
            or owner.state != "running"
            or owner.supervisor_pid != control.supervisor_pid
        ):
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.run.owner_invalid",
                    "native stage has no matching running supervisor",
                )
            )
        plan = job.accepted.plan
        if (workspace.source_project / plan.summary).resolve() != log.summary.resolve():
            raise ActionError(
                "reproduction.run.invalid",
                "accepted summary differs from requested log",
            )
    backend = control.confinement or DarwinSeatbelt()
    backend.preflight()
    stage = _WorkStage(log, plan, workspace, control, backend)
    pending = [ExecutionRef.from_dict(row["identity"]) for row in plan.scheduling]
    pending = [identity for identity in pending if not _completed_work(stage, identity)]
    running: dict[Future[CommandResult | None], ExecutionRef] = {}
    stopped = False
    with ThreadPoolExecutor(
        max_workers=plan.settings.jobs, thread_name_prefix="reproduce"
    ) as pool:
        while pending or running:
            stopped = stopped or _stop_requested(stage)
            if not stopped:
                ready = _resolve_pending(stage, pending)
                _launch_ready(stage, pool, pending, running, ready)
            if running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    running.pop(future)
                    stopped = future.result() is None or stopped
            elif stopped:
                break
            elif pending:
                time.sleep(POLL_SECONDS)
    return "stopped" if stopped else "completed"


def _finish_stopped(run_root: Path, owner: RunOwner) -> None:
    with open_work_job(run_root) as job:
        now = max(_utc_now(), owner.registered_at, owner.last_observed_at)
        job.replace_run_owner(replace(owner, state="stopped", last_observed_at=now))
        job.finish_run_stop(RunStopCompletion(now))


def _record_supervisor_failure(run_root: Path, error: BaseException) -> None:
    """Keep uncertain grants/workers under the running lease for dead-owner recovery."""

    with open_work_job(run_root) as job:
        owner = job.load_run_owner()
        if (
            owner is None
            or owner.supervisor_pid != os.getpid()
            or owner.state != "running"
        ):
            return
        state = job.load_run_control()
        if state.status is not None:
            return
        now = max(_utc_now(), owner.registered_at, owner.last_observed_at)
        code = (
            "reproduction.publication.failed"
            if state.phase == "publishing"
            else getattr(error, "code", "reproduction.supervisor.failed")
        )
        if state.operational_code is None:
            job.request_run_failure(
                RunFailure(code, str(error) or type(error).__name__, now)
            )
        proof = job.load_scheduler_owner()
        cleanup_incomplete = (
            isinstance(error, ReproductionControlPlaneError)
            and error.cleanup_incomplete
        )
        if cleanup_incomplete or proof.checkpoints or proof.running_workers:
            return
        job.replace_run_owner(replace(owner, state="exited", last_observed_at=now))
        job.finish_run_stop(RunStopCompletion(now))


def _supervisor_context(
    log: LogContext,
    run_root: Path,
    mode: Literal["fresh", "stopped", "publication"],
    control: WorkPlanControl,
) -> tuple[RunOwner, Path, str]:
    """Bind the supervisor route to the exact accepted job and running process."""

    with open_work_job(run_root) as job:
        state = job.load_run_control()
        owner = job.load_run_owner()
        _require_supervisor_owner(owner, control.supervisor_pid)
        assert owner is not None
        if job.accepted.plan.summary != str(log.summary):
            raise ActionError(
                "reproduction.run.invalid", "accepted run belongs to a different log"
            )
        if state.status is not None:
            raise ActionError(
                "reproduction.run.invalid",
                "supervisor route requires an active run",
            )
        if mode == "publication":
            valid = state.phase == "publishing" and job.load_publication() is not None
        else:
            valid = (
                mode in {"fresh", "stopped"}
                and state.phase in {"accepted", "executing", "comparing", "stopping"}
                and control.resume == (mode == "stopped")
            )
        if not valid:
            raise ActionError(
                "reproduction.run.invalid", "supervisor mode differs from durable state"
            )
        project_root = job.accepted.project_root
        run_id = job.accepted.run_id
    return owner, project_root, run_id


def _require_supervisor_owner(owner: RunOwner | None, supervisor_pid: int) -> None:
    if (
        owner is None
        or owner.state != "running"
        or owner.supervisor_pid != os.getpid()
        or supervisor_pid != os.getpid()
    ):
        raise ActionError(
            "reproduction.run.owner_invalid",
            "supervisor does not own the running lease",
        )


def supervise_work_job(
    log: LogContext,
    run_root: Path,
    *,
    mode: Literal["fresh", "stopped", "publication"],
    control: WorkPlanControl,
) -> None:
    """Complete one accepted native lifecycle while retaining caller-owned scope locks.

    The launcher must register this process before releasing its start gate.
    Fresh/stopped routes use the fixed accepted execution graph and existing
    workspace guards. Publication retry uses only frozen completed facts, without
    opening the workspace or performing execution preflight. Successful publication
    closes its exact lease; interruption closes only quiescent stopped state.
    Genuine operational errors retain uncertain cleanup ownership for recovery.
    """

    owner, project_root, run_id = _supervisor_context(log, run_root, mode, control)
    try:
        if mode == "publication":
            publish_work_job(log, run_root)
            return
        (control.confinement or DarwinSeatbelt()).preflight()
        workspace = (
            populate_current_output_workspace(project_root, run_root, run_id)
            if mode == "fresh"
            else open_current_workspace(project_root, run_root, run_id)
        )
        outcome = execute_work_plan(log, workspace, control)
        with open_work_job(run_root) as job:
            stopping = job.load_run_control().phase == "stopping"
        if outcome == "stopped" or stopping:
            _finish_stopped(run_root, owner)
            return
        compare_work_outputs(workspace)
        from .reproduction_reconciliation import reconcile_completed_sources

        reconcile_completed_sources(log, run_root)
        with open_work_job(run_root) as job:
            stopping = job.load_run_control().phase == "stopping"
        if stopping:
            _finish_stopped(run_root, owner)
            return
        publish_work_job(log, run_root)
    except SystemExit:
        raise
    except BaseException as error:
        _record_supervisor_failure(run_root, error)
        raise
