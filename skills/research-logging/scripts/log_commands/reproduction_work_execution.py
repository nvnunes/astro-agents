"""Execute Job5 accepted recipes using the existing physical execution rules.

Native job facts, not mutable source registries or checkpoint diagnoses, own
acceptance and completion. Process supervision, confinement, source/input
guards, capture handling and output materialization share their existing owners.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, cast

from validation.pyrun_outputs import output_target_path

from .context import EntryContext, LogContext
from .model import ActionError
from .reproduction_artifact_results import (
    ArtifactObservationContext,
    compare_accepted_artifact,
)
from .reproduction_command_results import completed_command_observation
from .reproduction_domain import (
    CommandOutcome,
    ExecutionRef,
    ProblemStage,
    WorkSelection,
)
from .reproduction_execution import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    ConfinementBackend,
    DarwinSeatbelt,
    ReproductionControlPlaneError,
    ReproductionWorkspace,
    _attempt_state,
    _clear_outputs,
    _control_plane_call,
    _CurrentProcessResult,
    _ExecutionSource,
    _material_readonly_boundaries,
    _materialize_outputs,
    _observe_available_outputs,
    _preflight_output_paths,
    _PreparationOptions,
    _prepare_execution,
    _PreparedExecution,
    _run_prepared,
    _RunCallbacks,
    _stored_worker,
    _utc_now,
    _verify_accepted_input_observations,
    _verify_accepted_source_observations,
)
from .reproduction_invocation import (
    AcceptedInvocation,
)
from .reproduction_run import CommandResult
from .reproduction_work import CommandWork
from .reproduction_work_job import (
    AttemptCompletion,
    AttemptInterruption,
    ExecutionStart,
    WorkCheckpoint,
    WorkJobAcceptance,
    open_work_job,
)
from .reproduction_work_plan import ReproductionPlan


@dataclass(frozen=True)
class WorkExecutionControl:
    """Supervisor grant and physical execution controls for one native attempt.

    ``release_grant`` must release/reconcile the exact global scheduler grant;
    it runs only after native terminal facts are durable. Its failure keeps the
    run-local permit/scratch ownership intact for control-plane recovery.
    """

    permit_id: str
    release_grant: Callable[[], None]
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None


@dataclass(frozen=True)
class _WorkAttempt:
    plan: ReproductionPlan
    work: CommandWork
    prior: WorkCheckpoint
    source: _ExecutionSource
    generated: Mapping[Path, tuple[Path, str]]
    prepared: _PreparedExecution


def _source(
    log: LogContext, plan: ReproductionPlan, work: CommandWork
) -> _ExecutionSource:
    return _ExecutionSource(
        EntryContext(log, work.identity.entry, Path(work.entry_root)),
        {
            (item.identity.cid, item.identity.execution_id): AcceptedInvocation(
                item.identity, Path(item.entry_root), item.execution, item.data
            )
            for item in plan.commands
            if item.identity.entry == work.identity.entry
        },
    )


def _generated_paths(
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
) -> Mapping[Path, tuple[Path, str]]:
    result = {}
    for work in plan.commands:
        if work.selection is not WorkSelection.RUN:
            continue
        entry = Path(work.entry_root)
        for name, kind in work.execution.recipe.outputs:
            retained = output_target_path(
                name, entry_root=entry, project_root=workspace.source_project
            )
            generated = output_target_path(
                name,
                entry_root=workspace.map_source(entry),
                project_root=workspace.work_project,
            )
            result[retained.resolve()] = (generated, kind)
    return result


def _prepare_attempt(
    log: LogContext,
    identity: ExecutionRef,
    workspace: ReproductionWorkspace,
    control: WorkExecutionControl,
) -> _WorkAttempt:
    with open_work_job(workspace.run_root) as job:
        _require_accepted_workspace(job.accepted, workspace)
        prior = job.load_execution_checkpoint(identity)
        plan = job.accepted.plan
        work = plan.command(identity)
    if prior is None or prior.state != "active" or prior.permit_id != control.permit_id:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.checkpoint.invalid",
                "native execution has no matching attached permit",
            )
        )
    source = _source(log, plan, work)
    generated = _generated_paths(plan, workspace)
    prepared = _prepare_execution(
        log, identity, workspace, generated, _PreparationOptions(source)
    )
    _verify_accepted_source_observations(prepared, source.entry.root, workspace)
    _preflight_output_paths(prepared.output_paths.values(), prepared.run_root)
    _clear_outputs(prepared.output_paths.values())
    return _WorkAttempt(plan, work, prior, source, generated, prepared)


def _run_attempt(
    attempt: _WorkAttempt,
    workspace: ReproductionWorkspace,
    control: WorkExecutionControl,
) -> _CurrentProcessResult:
    backend = control.confinement or DarwinSeatbelt()
    scratch = Path(tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp"))
    prepared = replace(
        attempt.prepared,
        environment={**attempt.prepared.environment, "TMPDIR": str(scratch)},
    )
    started_at = attempt.prior.started_at or _utc_now()
    stdout = prepared.stdout.relative_to(workspace.run_root).as_posix()
    stderr = prepared.stderr.relative_to(workspace.run_root).as_posix()
    try:
        backend.preflight()
        command = backend.command(
            prepared.command,
            writable_roots=(
                scratch,
                prepared.run_root,
                prepared.runtime_root,
                prepared.diagnostics_root,
            ),
            readonly_paths=_material_readonly_boundaries(attempt.plan.materials),
        )
        with open_work_job(workspace.run_root) as job:
            identity = attempt.work.identity
            job.record_execution_start(
                ExecutionStart(
                    identity.entry,
                    identity.cid,
                    identity.execution_id,
                    control.permit_id,
                    _utc_now(),
                    started_at,
                    str(scratch),
                    attempt.prior.elapsed_seconds,
                    stdout,
                    stderr,
                )
            )
    except BaseException as error:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ReproductionControlPlaneError(error) from error

    def record_workers(workers):
        with open_work_job(workspace.run_root) as job:
            job.replace_execution_workers(
                attempt.work.identity,
                control.permit_id,
                tuple(_stored_worker(item) for item in workers),
            )

    outcome, _launched_at, elapsed = _run_prepared(
        prepared,
        command,
        workspace,
        _RunCallbacks(
            control.stop_requested,
            control.execution_timeout_seconds,
            lambda _at: None,
            lambda workers: _control_plane_call(record_workers, workers),
        ),
    )
    if any(worker.state == "running" for worker in outcome.workers):
        raise ReproductionControlPlaneError(
            ActionError(
                "worker_cleanup_incomplete",
                "terminal execution retains a supervised worker",
            ),
            cleanup_incomplete=True,
        )
    return _CurrentProcessResult(
        prepared,
        outcome,
        elapsed,
        scratch,
        started_at,
        stdout,
        stderr,
        tuple(command),
        prepared.work_entry.as_posix(),
    )


def _materialized_result(
    attempt: _WorkAttempt,
    result: _CurrentProcessResult,
    workspace: ReproductionWorkspace,
    outputs: tuple[Mapping[str, object], ...],
) -> _CurrentProcessResult:
    state, _code, _message = _attempt_state(
        result.outcome, len(outputs), len(result.prepared.output_paths)
    )
    if state != "succeeded":
        return result
    try:
        _verify_accepted_source_observations(
            result.prepared, attempt.source.entry.root, workspace
        )
        _verify_accepted_input_observations(
            attempt.source.invocation(
                attempt.work.identity.cid, attempt.work.identity.execution_id
            ),
            attempt.generated,
        )
        _materialize_outputs(result.prepared, workspace, attempt.source)
    except (OSError, ValueError, ActionError) as error:
        return replace(
            result,
            outcome=replace(
                result.outcome,
                failure_code=cast(
                    str, getattr(error, "code", "output_materialization_failed")
                ),
                failure_message=str(error),
                failure_stage=ProblemStage.MATERIALIZE,
                error_type=type(error).__name__,
            ),
        )
    return result


def execute_work_recipe(
    log: LogContext,
    identity: ExecutionRef,
    workspace: ReproductionWorkspace,
    control: WorkExecutionControl,
) -> CommandResult | None:
    """Execute only the same-identity Job5 accepted recipe and persist actual facts.

    Native terminal result/problems and exited workers commit before global grant
    release, then run-local permit/scratch CAS cleanup. Stopped work retains its
    interrupted checkpoint and returns no research result. Control-plane errors
    propagate without inventing a failed command or releasing an uncertain grant.
    No current recipe/data registry is loaded and no old plan is manufactured.
    """

    attempt = _prepare_attempt(log, identity, workspace, control)
    result = _run_attempt(attempt, workspace, control)
    outputs = _observe_available_outputs(
        result.prepared.output_paths, result.prepared.execution
    )
    result = _materialized_result(attempt, result, workspace, outputs)
    when = _utc_now()
    observation, problems = completed_command_observation(
        attempt.work, result, when, outputs
    )
    workers = tuple(_stored_worker(item) for item in result.outcome.workers)
    elapsed = attempt.prior.elapsed_seconds + result.active_elapsed
    with open_work_job(workspace.run_root) as job:
        if observation is None:
            _state, _code, message = _attempt_state(
                result.outcome, len(outputs), len(result.prepared.output_paths)
            )
            job.record_execution_stop(
                AttemptInterruption(
                    identity,
                    control.permit_id,
                    when,
                    elapsed,
                    message or "Execution stopped.",
                    {str(item["artifact"]): item["fingerprint"] for item in outputs},
                    workers,
                )
            )
        else:
            job.record_attempt_completion(
                AttemptCompletion(
                    observation,
                    problems,
                    control.permit_id,
                    when,
                    elapsed,
                    workers,
                )
            )
    try:
        shutil.rmtree(result.scratch)
    except OSError as error:
        raise ReproductionControlPlaneError(
            ActionError("scratch_cleanup_incomplete", str(error)),
            cleanup_incomplete=True,
        ) from error
    _control_plane_call(control.release_grant)
    with open_work_job(workspace.run_root) as job:
        job.clear_execution_permit(
            identity, control.permit_id, checkpointed_at=_utc_now()
        )
        job.clear_execution_scratch(
            identity, str(result.scratch), checkpointed_at=_utc_now()
        )
    return observation


def compare_work_outputs(workspace: ReproductionWorkspace) -> None:
    """Persist only actual original-comparator facts from accepted generated paths.

    Each comparison verifies its frozen retained baseline and auxiliary evidence.
    Failed/blocked/nonselected producers do not invoke a comparator; completion
    derives their artifact reasons. Callers cannot submit fabricated result facts.
    """

    with open_work_job(workspace.run_root) as job:
        _require_accepted_workspace(job.accepted, workspace)
        plan = job.accepted.plan
        succeeded = {
            work.identity
            for work in plan.commands
            if (result := job.load_command_result(work.identity)) is not None
            and result.outcome is CommandOutcome.SUCCEEDED
        }
        recorded = {
            artifact.identity
            for artifact in plan.artifacts
            if job.load_artifact_result(artifact.identity) is not None
        }
    for artifact in plan.artifacts:
        if artifact.identity in recorded or artifact.producer not in succeeded:
            continue
        assert artifact.producer is not None
        assert artifact.output is not None
        producer = plan.command(artifact.producer)
        regenerated = output_target_path(
            artifact.output,
            entry_root=workspace.map_source(Path(producer.entry_root)),
            project_root=workspace.work_project,
        )
        compared, problems = compare_accepted_artifact(
            artifact,
            producer,
            regenerated,
            context=ArtifactObservationContext(
                workspace.run_id, _utc_now(), plan.evidence_only
            ),
        )
        with open_work_job(workspace.run_root) as job:
            job._record_artifact_comparison(compared, problems)


def _require_accepted_workspace(
    accepted: WorkJobAcceptance,
    workspace: ReproductionWorkspace,
) -> None:
    root = (accepted.project_root / accepted.run_path).resolve()
    expected = (
        root,
        root / accepted.workspace_path,
        root / "runtime",
        root / accepted.diagnostics_path,
        root / "executions",
    )
    actual = (
        workspace.run_root,
        workspace.work_project,
        workspace.runtime_root,
        workspace.diagnostics_root,
        workspace.staging_root,
    )
    if workspace.run_id != accepted.run_id or (
        workspace.source_project != accepted.project_root
        or actual != expected
        or any(path.is_symlink() or not path.is_dir() for path in actual)
    ):
        raise ActionError(
            "reproduction.workspace.invalid", "workspace is not the accepted job"
        )
