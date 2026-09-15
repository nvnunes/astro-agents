"""Durable launch, status, stop, resume, and supervision for reproduction."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence, cast

import psutil
from research_log_data import (
    DataContractError,
    InputResource,
    ResourceIdentity,
    observe_fingerprint,
    parse_fingerprint,
)
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.operation_state import (
    OperationLockError,
    operation_directory,
    operation_lock,
    operation_lock_owner,
    require_mutation_ready,
    research_snapshot,
)

from .context import LogContext, resolve_entry, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_comparison import (
    CurrentRequirementContext,
    clear_current_reproduction_requirement,
    compare_current_execution_outputs,
    load_current_recorded_comparisons,
    project_current_recorded_comparisons,
    record_current_dependency_skip,
)
from .reproduction_contract import (
    ReproductionPlan,
    ReproductionRuntime,
    canonical_record_digest,
)
from .reproduction_execution import (
    CurrentPlanControl,
    ReproductionControlPlaneError,
    current_execution_attempts,
    execute_current_reproduction_plan,
    open_current_workspace,
    populate_current_output_workspace,
    preflight_execution_safety,
)
from .reproduction_job_storage import (
    AcceptedJob,
    ExecutionIdentity,
    ExecutionTerminal,
    JobStoreError,
    PublicationFailure,
    PublicationProjection,
    PublicationResumeRequest,
    RecoveryWorkerObservation,
    RunFailure,
    RunOwner,
    RunResumeRequest,
    RunStatus,
    RunStopCompletion,
    RunStopRequest,
    begin_publication_resume,
    begin_run_resume,
    clear_execution_permit,
    clear_execution_scratch,
    create_job,
    finish_run_stop,
    load_accepted_scheduling,
    load_execution_checkpoint,
    load_execution_readiness,
    load_run_control,
    load_run_owner,
    load_scheduler_owner,
    open_locked_job,
    recognize_run_directory,
    record_execution_terminal,
    record_publication_failure,
    replace_recovery_workers,
    replace_run_owner,
    request_run_failure,
    request_run_stop,
)
from .reproduction_job_storage import (
    WorkerRecord as StoredWorkerRecord,
)
from .reproduction_job_storage import (
    load_accepted_plan as load_current_accepted_plan,
)
from .reproduction_job_storage import (
    load_publication_projection as load_current_publication_projection,
)
from .reproduction_job_storage import (
    load_run_status as load_current_run_status,
)
from .reproduction_paths import (
    canonical_run_path,
    iter_canonical_run_roots,
    project_tmp_relative,
    run_leaf,
)
from .reproduction_planner import (
    ReproductionSelection,
    plan_reproduction,
    prepare_reproduction_context,
)
from .reproduction_publication import (
    CompletedPublication,
    open_reproduction_publication,
    verify_publication_retry_compatibility,
)
from .reproduction_result_storage import PublicationCommitQuery

STATUS_SCHEMA = "research-log-reproduction-status/7"
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
EXECUTION_ID_RE = re.compile(r"pyrun-exec/v2:[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
MAX_STATUS_BYTES = 64 * 1024 * 1024
MAX_RUN_DIRECTORIES = 100_000
STOP_WAIT_SECONDS = 45.0
STATUS_POLL_SECONDS = 0.1
FRESH_RUN = "fresh"
STOPPED_RESUME = "stopped"
PUBLICATION_RETRY = "publication"


@dataclass(frozen=True)
class _CurrentSupervisorContext:
    """Validated current-format state supplied to one stage callback."""

    log: LogContext
    run_root: Path
    plan: ReproductionPlan
    mode: Literal["fresh", "stopped", "publication"]


@dataclass(frozen=True)
class _LostSupervisorContext:
    """One locked snapshot used to reconcile a dead durable owner."""

    log: LogContext
    run_root: Path
    run_id: str
    status: RunStatus
    owner: RunOwner | None
    now: str


@dataclass(frozen=True)
class _CurrentSupervisorCallbacks:
    """Current-format stage callbacks replaced by later cutover Tasks."""

    execute: Callable[[_CurrentSupervisorContext], Literal["completed", "stopped"]]
    compare: Callable[[_CurrentSupervisorContext], None]
    publish: Callable[[_CurrentSupervisorContext], None]


@dataclass(frozen=True)
class ReproductionLaunch:
    """One accepted run ID or one terminal no-work reconciliation."""

    run_id: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        if (self.run_id is None) == (self.summary is None):
            raise ValueError("reproduction launch needs exactly one result")

    def render(self) -> str:
        """Return the complete CLI-owned launch output."""

        if self.run_id is not None:
            return f"{self.run_id}\n"
        return cast(str, self.summary)


def _supervise_current_job(
    log: LogContext,
    run_root: Path,
    *,
    mode: Literal["fresh", "stopped", "publication"],
    callbacks: _CurrentSupervisorCallbacks,
) -> None:
    """Route one current SQLite job through validated durable stage boundaries."""

    recognized = recognize_run_directory(run_root)
    if recognized == "historical_unsupported":
        raise ActionError(
            "reproduction.run.unsupported",
            "historical reproduction run cannot use current supervision",
        )
    if recognized != "current":
        raise ActionError(
            "reproduction.run.invalid", "current reproduction state is absent"
        )
    if mode not in {FRESH_RUN, STOPPED_RESUME, PUBLICATION_RETRY}:
        raise ActionError("reproduction.run.invalid", "invalid supervisor mode")
    plan = load_current_accepted_plan(run_root)
    project_root = resolve_project_root(log.root)
    if (project_root / plan.summary).resolve() != log.summary.resolve():
        raise ActionError(
            "reproduction.run.invalid", "accepted run belongs to a different log"
        )
    context = _CurrentSupervisorContext(log, run_root, plan, mode)
    if mode == PUBLICATION_RETRY:
        _supervise_current_publication(context, callbacks)
        return
    _supervise_current_execution(context, callbacks)


def _supervise_current_publication(
    context: _CurrentSupervisorContext,
    callbacks: _CurrentSupervisorCallbacks,
) -> None:
    status = load_current_run_status(context.run_root)
    failure = load_current_publication_projection(context.run_root).publication
    if not _current_publication_route_valid(status, failure):
        raise ActionError(
            "reproduction.run.invalid",
            "publication supervisor mode does not match durable state",
        )
    callbacks.publish(context)
    _require_current_publication_complete(context.run_root)


def _current_publication_route_valid(status: object, publication: object) -> bool:
    """Accept explicit retries and interruption recovery, but no other mode."""

    from .reproduction_job_storage import PublicationStateProjection, RunStatus

    if not isinstance(status, RunStatus) or not isinstance(
        publication, PublicationStateProjection
    ):
        return False
    if (
        status.status is not None
        or status.phase != "publishing"
        or publication.stage not in {"ready", "publishing", "result_committed"}
        or publication.publication_identity is None
    ):
        return False
    failures = (
        publication.failure_code,
        publication.failure_message,
        publication.failure_recorded_at,
    )
    return all(value is None for value in failures) or all(
        value is not None for value in failures
    )


def _supervise_current_execution(
    context: _CurrentSupervisorContext,
    callbacks: _CurrentSupervisorCallbacks,
) -> None:
    status = load_current_run_status(context.run_root)
    mode = context.mode
    if (
        status.status is not None
        or status.phase not in {"accepted", "executing", "comparing"}
        or status.stop_requested_at is not None
        or (mode == FRESH_RUN and status.resumed_at is not None)
        or (mode == STOPPED_RESUME and status.resumed_at is None)
    ):
        raise ActionError(
            "reproduction.run.invalid",
            f"{mode} supervisor mode does not match durable state",
        )
    outcome = callbacks.execute(context)
    after_execute = load_current_run_status(context.run_root)
    if outcome == "stopped":
        if after_execute.status != "stopped" or after_execute.phase is not None:
            raise ActionError(
                "reproduction.run.invalid",
                "stopped callback result is not durably terminal",
            )
        return
    if outcome != "completed":
        raise ActionError(
            "reproduction.run.invalid", "execution callback returned invalid outcome"
        )
    if (
        after_execute.status is not None
        or after_execute.phase not in {"executing", "comparing"}
        or after_execute.stop_requested_at is not None
        or any(
            checkpoint.state in {"active", "stopped"}
            for checkpoint in after_execute.checkpoints
        )
    ):
        raise ActionError(
            "reproduction.run.invalid", "execution callback did not durably complete"
        )
    callbacks.compare(context)
    after_compare = load_current_run_status(context.run_root)
    if (
        after_compare.status is not None
        or after_compare.phase != "comparing"
        or after_compare.stop_requested_at is not None
    ):
        raise ActionError(
            "reproduction.run.invalid", "comparison callback changed run lifecycle"
        )
    callbacks.publish(context)
    _require_current_publication_complete(context.run_root)


def _require_current_publication_complete(run_root: Path) -> None:
    status = load_current_run_status(run_root)
    if status.status != "complete" or status.phase is not None:
        raise ActionError(
            "reproduction.run.invalid",
            "publication callback did not durably complete the run",
        )


def _execute_current_stage(
    context: _CurrentSupervisorContext,
    *,
    confinement: Any = None,
) -> Literal["completed", "stopped"]:
    """Run the accepted execution graph through current SQLite callbacks."""

    status = load_current_run_status(context.run_root)
    workspace = open_current_workspace(
        resolve_project_root(context.log.root), context.run_root, status.run_id
    )
    batch = execute_current_reproduction_plan(
        context.log,
        context.plan,
        workspace,
        CurrentPlanControl(
            os.getpid(),
            resume=context.mode == STOPPED_RESUME,
            execution_timeout_seconds=context.plan.execution_timeout_seconds,
            stop_requested=lambda: (
                load_run_control(context.run_root).stop_requested_at is not None
            ),
            confinement=confinement,
        ),
    )
    if not batch.stopped:
        return "completed"
    owner = _require_current_supervisor_owner(context.run_root)
    now = max(_utc_now(), status.updated_at, owner.registered_at)
    replace_run_owner(
        context.run_root,
        RunOwner(os.getpid(), "stopped", owner.registered_at, now),
    )
    finish_run_stop(context.run_root, RunStopCompletion(now))
    return "stopped"


def _compare_current_stage(context: _CurrentSupervisorContext) -> None:
    """Commit missing terminal comparisons, skips, and exact entry effects."""

    status = load_current_run_status(context.run_root)
    project_root = resolve_project_root(context.log.root)
    workspace = open_current_workspace(project_root, context.run_root, status.run_id)
    recorded = {
        (item.entry, item.cid, item.execution_id): item
        for item in load_current_recorded_comparisons(
            context.run_root, workspace, verify_outputs=True
        )
    }
    for attempt in current_execution_attempts(context.log, context.plan, workspace):
        key = (attempt.entry, attempt.cid, attempt.execution_id)
        comparison = recorded.get(key)
        if comparison is None:
            comparison = compare_current_execution_outputs(
                context.log,
                context.plan,
                workspace,
                attempt,
                recorded_at=_utc_now(),
            )
            recorded[key] = comparison
        if comparison.complete:
            clear_current_reproduction_requirement(
                CurrentRequirementContext(
                    context.log, context.plan, context.run_root, project_root
                ),
                comparison,
                recorded_at=_utc_now(),
            )
    for planned in sorted(
        context.plan.executions, key=lambda item: cast(int, item["order"])
    ):
        entry = cast(str, planned["entry"])
        cid = cast(str, planned["cid"])
        execution_id = cast(str, planned["execution_id"])
        key = (entry, cid, execution_id)
        if key in recorded:
            continue
        checkpoint = load_execution_checkpoint(
            context.run_root, ExecutionIdentity(entry, cid, execution_id)
        )
        readiness = load_execution_readiness(
            context.run_root, ExecutionIdentity(entry, cid, execution_id)
        )
        if checkpoint is None and readiness.disposition == "dependency_failed":
            recorded[key] = record_current_dependency_skip(
                context.plan,
                planned,
                run_root=context.run_root,
                recorded_at=_utc_now(),
            )
            continue
        raise ActionError(
            "reproduction.comparison.inventory_invalid",
            f"execution has no terminal comparison input: {entry}:{cid}:{execution_id}",
        )


def _publish_current_stage(context: _CurrentSupervisorContext) -> None:
    """Publish or reconcile one fixed terminal job under the complete lock order."""

    with open_locked_job(context.run_root) as store:
        projection = store.load_publication_projection()
        request = _current_completed_publication(context, projection)
        publication_identity = _current_publication_identity(request, projection)
        stage = projection.publication.stage
        stored_identity = projection.publication.publication_identity
        if stage == "not_ready":
            store.prepare_publication(publication_identity, updated_at=_utc_now())
            stage = "ready"
        elif stored_identity != publication_identity:
            raise ActionError(
                "reproduction.publication.identity_changed",
                "durable publication identity does not match terminal state",
            )
        query = _current_publication_query(context, request)
        with open_reproduction_publication(context.log) as publisher:
            if stage == "ready":
                store.begin_publication(publication_identity, updated_at=_utc_now())
                stage = "publishing"
            if stage in {"publishing", "result_committed"}:
                match = publisher.lookup_run_commit(query)
                if match.disposition == "conflict":
                    raise ActionError(
                        "reproduction.publication.conflict",
                        "result store contains conflicting metadata for this run ID",
                    )
                if match.disposition == "absent":
                    if stage == "publishing":
                        store.reset_absent_publication(
                            publication_identity, updated_at=_utc_now()
                        )
                    else:
                        store.reset_missing_result_commit(
                            publication_identity, updated_at=_utc_now()
                        )
                    store.begin_publication(publication_identity, updated_at=_utc_now())
                    result_commit = publisher.publish_result_transaction(request)
                    generation = result_commit.generation
                    store.record_result_commit(
                        publication_identity, generation, updated_at=_utc_now()
                    )
                    stage = "result_committed"
                elif stage == "publishing":
                    assert match.observed_generation is not None
                    store.record_result_commit(
                        publication_identity,
                        match.observed_generation,
                        updated_at=_utc_now(),
                    )
                    stage = "result_committed"
            if stage != "result_committed":
                raise ActionError(
                    "reproduction.publication.state_invalid",
                    f"cannot materialize report from publication stage {stage}",
                )
            committed = store.load_publication_projection().publication
            assert committed.result_generation is not None
            report = publisher.materialize_report(committed.result_generation)
            store.record_report_commit(
                publication_identity, report.generation, updated_at=_utc_now()
            )


def _current_completed_publication(
    context: _CurrentSupervisorContext, projection: PublicationProjection
) -> CompletedPublication:
    """Reconstruct the exact terminal publication input from durable job rows."""

    workspace = open_current_workspace(
        resolve_project_root(context.log.root),
        context.run_root,
        projection.identity.run_id,
    )
    stored = project_current_recorded_comparisons(
        projection.comparisons,
        context.run_root,
        workspace,
        verify_outputs=True,
    )
    checkpoint_keys = {
        (item.entry, item.cid, item.execution_id) for item in projection.checkpoints
    }
    comparisons = tuple(
        item
        for item in stored
        if (item.entry, item.cid, item.execution_id) in checkpoint_keys
    )
    skipped = tuple(
        {
            "entry": item.entry,
            "cid": item.cid,
            "execution_id": item.execution_id,
            "reason": "dependency_failed",
        }
        for item in stored
        if (item.entry, item.cid, item.execution_id) not in checkpoint_keys
        and all(
            artifact.outcome == "skipped" and artifact.reason == "dependency_failed"
            for artifact in item.artifacts
        )
    )
    if len(comparisons) + len(skipped) != len(stored):
        raise ActionError(
            "reproduction.publication.invalid",
            "durable dependency-skip comparison is malformed",
        )
    terminal_times = [item.recorded_at for item in projection.comparisons]
    terminal_times.extend(
        item.finished_at
        for item in projection.checkpoints
        if item.finished_at is not None
    )
    return CompletedPublication(
        context.plan,
        comparisons,
        projection.identity.run_id,
        projection.accepted_at,
        max(projection.accepted_at, *terminal_times),
        context.run_root,
        skipped,
        tuple(
            {
                "elapsed_seconds": item.elapsed_seconds,
                "entry": item.entry,
                "cid": item.cid,
                "execution_id": item.execution_id,
                "finished_at": item.finished_at,
                "started_at": item.started_at,
            }
            for item in sorted(
                projection.checkpoints,
                key=lambda value: next(
                    cast(int, planned["order"])
                    for planned in context.plan.executions
                    if planned["entry"] == value.entry
                    and planned["cid"] == value.cid
                    and planned["execution_id"] == value.execution_id
                ),
            )
            if item.started_at is not None
        ),
    )


def _current_publication_identity(
    request: CompletedPublication, projection: PublicationProjection
) -> str:
    """Digest the fixed plan and exact terminal rows before any result write."""

    return canonical_record_digest(
        {
            "accepted_at": request.accepted_at,
            "comparisons": [asdict(item) for item in projection.comparisons],
            "finished_at": request.finished_at,
            "plan": json.loads(request.plan.serialized()),
            "run_id": request.run_id,
            "run_path": projection.identity.run_path,
        }
    )


def _current_publication_query(
    context: _CurrentSupervisorContext, request: CompletedPublication
) -> PublicationCommitQuery:
    """Build the bounded unique-run lookup from immutable terminal metadata."""

    project_root = resolve_project_root(context.log.root)
    target = request.plan.target
    return PublicationCommitQuery(
        request.run_id,
        cast(str, target["kind"]),
        cast(str | None, target.get("entry")),
        cast(str | None, target.get("cid")),
        cast(str | None, target.get("execution_id")),
        request.plan.include_all,
        request.accepted_at,
        request.finished_at,
        "complete",
        project_tmp_relative(request.run_folder, project_root),
    )


def _prepare_plan(  # noqa: PLR0913
    log: LogContext,
    entry: str | None,
    include_all: bool,
    runtime: ReproductionRuntime,
    selection: ReproductionSelection,
    *,
    publish_validation: bool,
) -> ReproductionPlan:
    """Evaluate and plan once while the caller owns the normal log lock."""

    accepted_snapshot = research_snapshot(log.summary)
    result = evaluate_mechanical(
        EvaluationRequest(log.summary, FullEvaluationTarget())
    )
    if research_snapshot(log.summary) != accepted_snapshot:
        raise ActionError(
            "reproduction.validation.source_changed",
            "research-owned state changed during validation planning",
        )
    from research_log_result_store import record_report_materialization, results_lock
    from validation.records import publish_validation_outputs_locked
    from validation.snapshot_report import compose_snapshot_report
    from validation.snapshot_storage import (
        SnapshotPublicationRequest,
        entry_relations_from_evaluation,
        load_validation_snapshot,
        publish_validation_snapshot,
    )

    if publish_validation:
        with results_lock(log.root):
            assert result.snapshot is not None
            stored = publish_validation_snapshot(
                SnapshotPublicationRequest(
                    log.root,
                    result.snapshot,
                    entry_relations_from_evaluation(result.context),
                )
            )
            identity = (
                f"committed validation snapshot {stored.snapshot_id} generation "
                f"{stored.generation}"
            )
            try:
                report_bytes = compose_snapshot_report(
                    load_validation_snapshot(log.root)
                ).encode()
            except Exception as error:
                raise ActionError(
                    "results.report.render_failed", f"{identity}: {error}"
                ) from error
            try:
                publish_validation_outputs_locked(
                    log.root, {"validation.md": report_bytes}
                )
                record_report_materialization(
                    log.root,
                    "validation",
                    report_bytes,
                    expected_generation=stored.generation,
                )
            except Exception as error:
                raise ActionError(
                    "results.report.write_failed",
                    f"{identity}; report marker is stale: {error}",
                ) from error
    prepared = prepare_reproduction_context(result)
    return plan_reproduction(
        log,
        prepared,
        entry=resolve_entry(log, entry) if entry is not None else None,
        include_all=include_all,
        runtime=runtime,
        selection=selection,
    )


def launch_reproduction(
    log: LogContext,
    *,
    entry: str | None,
    include_all: bool,
    runtime: ReproductionRuntime = ReproductionRuntime(),
    selection: ReproductionSelection = ReproductionSelection(),
) -> ReproductionLaunch:
    """Return a no-work summary or hand an accepted plan to a supervisor."""

    lock_fds = _acquire_scope_locks(log, entry)
    try:
        with operation_lock(log.root, "log.lock", mode="exclusive"):
            plan = _prepare_plan(
                log, entry, include_all, runtime, selection, publish_validation=True
            )
            if not plan.executions:
                from .reproduction_queries import reproduction_reconciliation_text

                if selection.policy == "recheck" and entry is None:
                    from .reproduction_publication import (
                        empty_reproduction_recovery_needed,
                        recover_empty_reproduction_results,
                    )

                    if empty_reproduction_recovery_needed(log, plan):
                        recover_empty_reproduction_results(
                            log, plan, updated_at=_utc_now()
                        )
                return ReproductionLaunch(
                    summary=reproduction_reconciliation_text(
                        log, plan, generated_at=_utc_now()
                    )
                )
            project = resolve_project_root(log.root)
            run_id = _new_run_id()
            accepted_at = _utc_now()
            run_root = _new_run_root(project, log, entry, run_id, accepted_at)
            with operation_lock(project, "reproduction-promotion-index.lock"):
                with operation_lock(log.root, "reproduction-publication.lock"):
                    _require_no_promotion_conflict(log, plan)
                run_root.mkdir(parents=True)
                create_job(
                    run_root,
                    AcceptedJob(
                        run_id,
                        plan,
                        accepted_at,
                        canonical_run_path(accepted_at, run_root.name).as_posix(),
                    ),
                )
        _spawn_supervisor(log, run_root, lock_fds, mode=FRESH_RUN)
    finally:
        _close_fds(lock_fds)
    return ReproductionLaunch(run_id=run_id)


def dry_run_reproduction(
    log: LogContext,
    *,
    entry: str | None,
    include_all: bool,
    runtime: ReproductionRuntime = ReproductionRuntime(),
    selection: ReproductionSelection = ReproductionSelection(),
) -> ReproductionPlan:
    """Return one stable, write-free plan after the runtime safety preflight."""

    lock_fds = _acquire_scope_locks(log, entry)
    try:
        with operation_lock(log.root, "log.lock", mode="exclusive"):
            plan = _prepare_plan(
                log, entry, include_all, runtime, selection, publish_validation=False
            )
        preflight_execution_safety()
        return plan
    finally:
        _close_fds(lock_fds)


def reproduction_status(
    log: LogContext, run_id: str, *, reconcile: bool = True
) -> Mapping[str, object]:
    """Return the frozen deterministic status projection for one run."""

    root = _find_run(log, run_id)
    if reconcile:
        _reconcile_current_lost_supervisor(log, root, run_id)
    return _current_status_projection(load_current_run_status(root), root)


def format_reproduction_status(status: Mapping[str, object]) -> str:
    """Compose concise human status without hiding failures."""

    state = status.get("status") or status.get("phase")
    progress = f"{status['completed_executions']}/{status['total_executions']}"
    lines = [f"Run {status['run_id']}: {state} ({progress} executions)"]
    if "execution_timeout_seconds" in status:
        lines.append(
            f"Per-command runtime limit: {status['execution_timeout_seconds']} seconds"
        )
    active = status.get("active_executions")
    if isinstance(active, Sequence) and active:
        lines.append(
            "Active executions: "
            + ", ".join(
                f"{item['entry']}:{item['execution_id']}"
                for item in active
                if isinstance(item, Mapping)
            )
        )
        timings = status.get("execution_timings")
        if isinstance(timings, Sequence):
            for item in timings:
                if isinstance(item, Mapping) and item.get("state") == "active":
                    lines.append(
                        f"Active execution time ({item['entry']}): "
                        f"{item['elapsed_seconds']} seconds"
                    )
    operational = status.get("operational_failure")
    if isinstance(operational, Mapping):
        lines.append(
            f"Operational failure: {operational['code']}: {operational['message']}"
        )
    diagnostic = status.get("latest_execution_diagnostic")
    if isinstance(diagnostic, Mapping):
        lines.append(
            "Latest execution diagnostic: "
            f"{diagnostic['code']}: {diagnostic['message']}"
        )
    return "\n".join(lines) + "\n"


def stop_reproduction(log: LogContext, run_id: str) -> Mapping[str, object]:
    """Request bounded worker-tree shutdown and wait for a stable result."""

    root = _find_run(log, run_id)
    _reconcile_current_lost_supervisor(log, root, run_id)
    status = load_current_run_status(root)
    if status.status == "stopped":
        return _current_status_projection(status, root)
    if status.status is not None:
        raise ActionError(
            "reproduction.stop.invalid_state", f"run is already {status.status}"
        )
    request_run_stop(root, RunStopRequest(_utc_now()))
    from .reproduction_scheduler import cancel_run_waiters

    cancel_run_waiters(resolve_project_root(log.root), run_id)
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        projected = reproduction_status(log, run_id)
        if projected["status"] == "stopped":
            return projected
        if projected["status"] == "failed":
            raise ActionError(
                "reproduction.stop.failed", "run failed before stop completed"
            )
        if projected["status"] == "complete":
            raise ActionError(
                "reproduction.stop.completed", "run completed before stop took effect"
            )
        time.sleep(STATUS_POLL_SECONDS)
    projected = reproduction_status(log, run_id)
    raise ActionError(
        "reproduction.stop.incomplete",
        _survivor_summary(projected.get("surviving_workers")),
    )


def resume_reproduction(log: LogContext, run_id: str) -> str:
    """Resume one stopped fixed plan or retry its failed publication."""

    root = _find_run(log, run_id)
    _reconcile_current_lost_supervisor(log, root, run_id)
    status = load_current_run_status(root)
    publication = load_current_publication_projection(root).publication
    publication_retry = (
        status.status == "failed"
        and status.operational_failure is not None
        and status.operational_failure.code == "reproduction.publication.failed"
        and publication.stage in {"ready", "result_committed"}
        and publication.publication_identity is not None
    )
    if status.status != "stopped" and not publication_retry:
        raise ActionError(
            "reproduction.resume.invalid_state",
            "only a stopped run or failed reproduction publication can resume",
        )
    plan = load_current_accepted_plan(root)
    if publication_retry:
        verify_publication_retry_compatibility(log, plan)
    entry = cast(str | None, plan.target["entry"])
    lock_fds = _acquire_scope_locks(log, entry)
    try:
        with operation_lock(
            resolve_project_root(log.root), "reproduction-promotion-index.lock"
        ):
            _verify_resume_activation(log, plan)
            now = _utc_now()
            if publication_retry:
                assert publication.publication_identity is not None
                begin_publication_resume(
                    root,
                    PublicationResumeRequest(
                        publication.publication_identity,
                        cast(Literal["ready", "result_committed"], publication.stage),
                        now,
                    ),
                )
            else:
                begin_run_resume(root, RunResumeRequest(now))
        _spawn_supervisor(
            log,
            root,
            lock_fds,
            mode=PUBLICATION_RETRY if publication_retry else STOPPED_RESUME,
        )
    except BaseException:
        _close_fds(lock_fds)
        raise
    _close_fds(lock_fds)
    return run_id


def _verify_resume_activation(log: LogContext, plan: ReproductionPlan) -> None:
    _require_no_promotion_conflict(log, plan)


def supervise_reproduction(
    log: LogContext,
    run_root: Path,
    *,
    mode: str,
    inherited_locks: Sequence[int],
    confinement: Any = None,
) -> None:
    """Run one accepted job to a terminal state while retaining its locks."""

    if mode not in {FRESH_RUN, STOPPED_RESUME, PUBLICATION_RETRY}:
        raise ActionError(
            "reproduction.run.invalid", f"invalid supervisor mode: {mode}"
        )
    recognized = recognize_run_directory(run_root)
    if recognized == "historical_unsupported":
        raise ActionError(
            "reproduction.run.unsupported",
            "historical reproduction jobs are unsupported; start a new run",
        )
    if recognized != "current":
        raise ActionError("reproduction.run.invalid", "current job state is absent")
    status = load_current_run_status(run_root)
    try:
        owner = _require_current_supervisor_owner(run_root)
        if mode != PUBLICATION_RETRY:
            preflight_execution_safety(confinement)
            if mode == FRESH_RUN:
                populate_current_output_workspace(
                    resolve_project_root(log.root), run_root, status.run_id
                )
            else:
                open_current_workspace(
                    resolve_project_root(log.root), run_root, status.run_id
                )
        callbacks = _CurrentSupervisorCallbacks(
            lambda context: _execute_current_stage(context, confinement=confinement),
            _compare_current_stage,
            _publish_current_stage,
        )
        _supervise_current_job(
            log,
            run_root,
            mode=cast(Literal["fresh", "stopped", "publication"], mode),
            callbacks=callbacks,
        )
        latest = load_current_run_status(run_root)
        now = max(_utc_now(), latest.updated_at, owner.registered_at)
        replace_run_owner(
            run_root,
            RunOwner(
                os.getpid(),
                "stopped" if latest.status == "stopped" else "exited",
                owner.registered_at,
                now,
            ),
        )
        return
    except SystemExit:
        raise
    except BaseException as error:
        _record_current_supervisor_failure(run_root, status, error)
        raise


def _record_current_supervisor_failure(
    run_root: Path, initial: object, error: BaseException
) -> None:
    """Persist one current supervisor failure without converting process death."""

    from .reproduction_job_storage import RunStatus

    cleanup_incomplete = (
        isinstance(error, ReproductionControlPlaneError) and error.cleanup_incomplete
    )
    if not isinstance(initial, RunStatus):
        _require_cleanup_exclusion(
            cleanup_incomplete,
            "cleanup-incomplete failure has no current lifecycle snapshot",
        )
        return
    try:
        _persist_current_supervisor_failure(
            run_root, error, cleanup_incomplete=cleanup_incomplete
        )
    except BaseException as persistence_error:
        _raise_cleanup_exclusion_failure(cleanup_incomplete, persistence_error)
        return


def _persist_current_supervisor_failure(
    run_root: Path,
    error: BaseException,
    *,
    cleanup_incomplete: bool,
) -> None:
    owner = load_scheduler_owner(run_root).owner
    owned = (
        owner is not None
        and owner.supervisor_pid == os.getpid()
        and owner.state == "running"
    )
    _require_cleanup_exclusion(
        cleanup_incomplete and not owned,
        "cleanup-incomplete failure lost its durable owner lease",
    )
    if not owned or owner is None:
        return
    status = load_current_run_status(run_root)
    now = max(_utc_now(), status.updated_at, owner.registered_at)
    if status.status is None and status.phase == "publishing":
        _record_current_publication_failure(run_root, error, now)
    elif status.status is None:
        request_run_failure(
            run_root,
            RunFailure(
                cast(str, getattr(error, "code", "reproduction.supervisor.failed")),
                str(error) or type(error).__name__,
                now,
            ),
        )
    if not cleanup_incomplete:
        replace_run_owner(
            run_root,
            RunOwner(os.getpid(), "exited", owner.registered_at, now),
        )


def _record_current_publication_failure(
    run_root: Path, error: BaseException, now: str
) -> None:
    publication = load_current_publication_projection(run_root).publication
    if publication.stage not in {"publishing", "result_committed"}:
        return
    record_publication_failure(
        run_root,
        publication.stage,
        PublicationFailure(
            cast(str, getattr(error, "code", "reproduction.publication.failed")),
            str(error) or type(error).__name__,
            now,
        ),
    )


def _require_cleanup_exclusion(required: bool, message: str) -> None:
    if required:
        raise ActionError("reproduction.recovery.exclusion_failed", message)


def _raise_cleanup_exclusion_failure(
    cleanup_incomplete: bool, error: BaseException
) -> None:
    if not cleanup_incomplete:
        return
    if isinstance(error, ActionError) and (
        error.code == "reproduction.recovery.exclusion_failed"
    ):
        raise error
    raise ActionError(
        "reproduction.recovery.exclusion_failed",
        "cleanup-incomplete failure could not persist durable exclusion",
    ) from error


def _require_current_supervisor_owner(run_root: Path) -> RunOwner:
    """Return the exact live owner installed before this supervisor was released."""

    owner = load_scheduler_owner(run_root).owner
    if owner is None or owner.supervisor_pid != os.getpid() or owner.state != "running":
        raise ActionError(
            "reproduction.run.owner_invalid",
            "current supervisor does not own the durable running lease",
        )
    return owner


def _verify_accepted_materials(plan: ReproductionPlan) -> None:
    """Reobserve every frozen local material before publishing a fixed run."""

    materials = plan.comparison_context.get("materials")
    if not isinstance(materials, list):
        raise ActionError(
            "reproduction.publication.material_invalid",
            "accepted comparison material context is invalid",
        )
    for material in materials:
        if not isinstance(material, Mapping):
            raise ActionError(
                "reproduction.publication.material_invalid",
                "accepted comparison material is invalid",
            )
        identity, kind, fingerprint = (
            material.get("identity"),
            material.get("kind"),
            material.get("fingerprint"),
        )
        if not isinstance(identity, str) or not isinstance(fingerprint, Mapping):
            raise ActionError(
                "reproduction.publication.material_invalid",
                "accepted comparison material is invalid",
            )
        if kind not in {"file", "directory"}:
            continue
        path = Path(identity)
        try:
            expected = parse_fingerprint(fingerprint, f"publication:{identity}")
            resource = InputResource(
                "publication-material",
                cast(str, kind),
                identity,
                ResourceIdentity(
                    expected.algorithm,
                    commit=(
                        expected.digest
                        if expected.algorithm == "git-commit-sha1-v1"
                        else None
                    ),
                    files=expected.files,
                    patterns=expected.patterns,
                ),
                True,
                identity,
            )
            observed = observe_fingerprint(resource).fingerprint
            if path.is_symlink() or observed != expected:
                raise ValueError("fingerprint changed")
        except (OSError, ValueError, DataContractError) as error:
            raise ActionError(
                "reproduction.publication.material_changed",
                f"accepted material changed: {identity}: {error}",
            ) from error


def supervisor_main(arguments: Sequence[str]) -> int:
    """Internal detached-supervisor process entrypoint."""

    if len(arguments) not in {4, 5}:
        return 2
    summary, run_root, raw_fds, raw_resume = arguments[:4]
    if raw_resume not in {"0", "1", "2"}:
        return 2
    fds = tuple(int(value) for value in raw_fds.split(",") if value)
    try:
        if len(arguments) == 5:
            gate_fd = int(arguments[4])
            try:
                if os.read(gate_fd, 1) != b"1":
                    return 2
            finally:
                os.close(gate_fd)
        log = resolve_log(Path(summary).with_suffix(""))
        supervise_reproduction(
            log,
            Path(run_root),
            mode=(
                STOPPED_RESUME
                if raw_resume == "1"
                else PUBLICATION_RETRY
                if raw_resume == "2"
                else FRESH_RUN
            ),
            inherited_locks=fds,
        )
    finally:
        _close_fds(fds)
    return 0


def _spawn_supervisor(
    log: LogContext,
    run_root: Path,
    lock_fds: Sequence[int],
    *,
    mode: str,
) -> None:
    encoded_mode = {
        FRESH_RUN: "0",
        STOPPED_RESUME: "1",
        PUBLICATION_RETRY: "2",
    }.get(mode)
    if encoded_mode is None:
        raise ActionError("reproduction.run.invalid", "invalid supervisor mode")
    environment = dict(os.environ)
    scripts = str(Path(__file__).resolve().parents[1])
    prior = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = scripts if not prior else f"{scripts}:{prior}"
    read_gate, write_gate = os.pipe()
    try:
        log_path = run_root / "supervisor.log"
        with log_path.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "log_commands.reproduction_jobs",
                    str(log.summary),
                    str(run_root),
                    ",".join(str(value) for value in lock_fds),
                    encoded_mode,
                    str(read_gate),
                ],
                cwd=resolve_project_root(log.root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
                pass_fds=(*tuple(lock_fds), read_gate),
            )
        os.close(read_gate)
        read_gate = -1
        status = load_current_run_status(run_root)
        now = max(_utc_now(), status.updated_at)
        replace_run_owner(run_root, RunOwner(process.pid, "running", now, now))
        os.write(write_gate, b"1")
    except BaseException:
        if "process" in locals():
            process.terminate()
        raise
    finally:
        if read_gate >= 0:
            os.close(read_gate)
        os.close(write_gate)


def _acquire_scope_locks(
    log: LogContext,
    entry: str | None,
    *,
    ignore_recovery_run_id: str | None = None,
) -> tuple[int, ...]:
    directory = operation_directory(log.root)
    directory.mkdir(parents=True, exist_ok=True)
    require_mutation_ready(log.root, entry_id=entry)
    _require_no_recovery_exclusion(
        log, entry, ignore_recovery_run_id=ignore_recovery_run_id
    )
    requests = (
        (("reproduction-log.lock", fcntl.LOCK_EX),)
        if entry is None
        else (
            ("reproduction-log.lock", fcntl.LOCK_SH),
            (f"reproduction-entry-{entry}.lock", fcntl.LOCK_EX),
        )
    )
    opened: list[int] = []
    try:
        for name, operation in requests:
            descriptor = os.open(
                directory / name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o644,
            )
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            except BlockingIOError as error:
                os.close(descriptor)
                path = directory / name
                raise OperationLockError(path, operation_lock_owner(path)) from error
            os.set_inheritable(descriptor, True)
            opened.append(descriptor)
        # A supervisor can exit after our initial probe but before this process
        # owns every overlapping descriptor. Recheck its SQLite lease now.
        _require_no_recovery_exclusion(
            log, entry, ignore_recovery_run_id=ignore_recovery_run_id
        )
    except BaseException:
        _close_fds(opened)
        raise
    return tuple(opened)


def _require_no_promotion_conflict(log: LogContext, plan: ReproductionPlan) -> None:
    materials = cast(
        Sequence[Mapping[str, object]], plan.comparison_context["materials"]
    )
    inputs = {
        Path(cast(str, item["identity"])).resolve()
        for item in materials
        if item.get("role") == "boundary" and isinstance(item.get("identity"), str)
    }
    directory = operation_directory(resolve_project_root(log.root))
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("promotion-*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            outputs = value["outputs"]
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as error:
            raise ActionError(
                "reproduction.promotion.state_invalid", str(path)
            ) from error
        if not isinstance(outputs, list) or not all(
            isinstance(item, str) for item in outputs
        ):
            raise ActionError("reproduction.promotion.state_invalid", str(path))
        overlap = _overlapping_paths(
            inputs, tuple(Path(item).resolve() for item in outputs)
        )
        if overlap:
            raise ActionError(
                "reproduction.promotion.conflict",
                f"active promotion changes a reproduction input: {min(overlap)}",
            )


def _overlapping_paths(paths: set[Path], other: Sequence[Path]) -> set[Path]:
    """Return accepted input paths overlapping promoted files or directories."""

    result: set[Path] = set()
    for left in paths:
        for right in other:
            if left == right or left in right.parents or right in left.parents:
                result.add(left)
    return result


def _reconcile_current_lost_supervisor(
    log: LogContext, run_root: Path, run_id: str
) -> None:
    """Reconcile one dead current supervisor without restarting its work."""

    status = load_current_run_status(run_root)
    if status.run_id != run_id:
        return
    owner = load_run_owner(run_root)
    if _current_recovery_not_needed(status, owner):
        return
    plan = load_current_accepted_plan(run_root)
    fds = _acquire_scope_locks(
        log,
        cast(str | None, plan.target["entry"]),
        ignore_recovery_run_id=run_id,
    )
    try:
        status = load_current_run_status(run_root)
        owner = load_run_owner(run_root)
        if _current_recovery_not_needed(status, owner):
            return
        now = max(
            _utc_now(),
            status.updated_at,
            owner.registered_at if owner is not None else status.accepted_at,
            owner.last_observed_at if owner is not None else status.accepted_at,
        )
        context = _LostSupervisorContext(log, run_root, run_id, status, owner, now)
        if status.status is not None:
            _reconcile_terminal_owner(context)
        elif status.phase == "publishing":
            _fail_interrupted_current_publication(run_root, now)
            replace_run_owner(
                run_root,
                RunOwner(
                    owner.supervisor_pid if owner is not None else os.getpid(),
                    "exited",
                    owner.registered_at if owner is not None else status.accepted_at,
                    now,
                ),
            )
        else:
            _reconcile_interrupted_execution_owner(context)
    finally:
        _close_fds(fds)


def _current_recovery_not_needed(status: RunStatus, owner: RunOwner | None) -> bool:
    if status.status is not None and (owner is None or owner.state != "running"):
        return True
    return (
        owner is not None
        and owner.state == "running"
        and _pid_alive(owner.supervisor_pid)
    )


def _reconcile_terminal_owner(context: _LostSupervisorContext) -> None:
    """Close only a dead supervisor lease left after terminal publication."""

    survivors = _terminate_marked_workers(context.run_id)
    if survivors:
        # The terminal run's still-running dead owner remains the durable
        # SQLite exclusion until a later pass proves every process exited.
        return
    owner = context.owner
    if owner is not None and owner.state == "running":
        replace_run_owner(
            context.run_root,
            RunOwner(
                owner.supervisor_pid,
                "exited",
                owner.registered_at,
                context.now,
            ),
        )


def _reconcile_interrupted_execution_owner(
    context: _LostSupervisorContext,
) -> None:
    """Stop and close a dead execution supervisor after worker cleanup."""

    survivors = _terminate_marked_workers(context.run_id)
    replace_recovery_workers(
        context.run_root,
        _recovery_worker_observations(survivors, observed_at=context.now),
        observed_at=context.now,
    )
    if survivors:
        return
    from .reproduction_scheduler import reconcile_run_admission

    reconcile_run_admission(resolve_project_root(context.log.root), context.run_root)
    if context.status.phase != "stopping":
        request_run_stop(context.run_root, RunStopRequest(context.now))
    _terminalize_current_active_executions(
        resolve_project_root(context.log.root), context.run_root, context.now
    )
    owner = context.owner
    replace_run_owner(
        context.run_root,
        RunOwner(
            owner.supervisor_pid if owner is not None else os.getpid(),
            "stopped",
            (owner.registered_at if owner is not None else context.status.accepted_at),
            context.now,
        ),
    )
    finish_run_stop(context.run_root, RunStopCompletion(context.now))


def _fail_interrupted_current_publication(run_root: Path, now: str) -> None:
    projection = load_current_publication_projection(run_root).publication
    if projection.publication_identity is None:
        raise ActionError(
            "reproduction.publication.state_invalid",
            "interrupted publication has no durable identity",
        )
    if projection.stage == "ready":
        with open_locked_job(run_root) as store:
            store.begin_publication(projection.publication_identity, updated_at=now)
        expected = "publishing"
    elif projection.stage in {"publishing", "result_committed"}:
        expected = projection.stage
    else:
        raise ActionError(
            "reproduction.publication.state_invalid",
            f"interrupted publication has invalid stage {projection.stage}",
        )
    record_publication_failure(
        run_root,
        expected,
        PublicationFailure(
            "reproduction.supervisor.interrupted",
            "The durable supervisor was interrupted during publication.",
            now,
        ),
    )


def _terminalize_current_active_executions(
    project_root: Path, run_root: Path, now: str
) -> None:
    """Stop dead-owner checkpoints and reconcile each exact scheduler permit."""

    from .reproduction_scheduler import SchedulerIdentity, reconcile_permit

    status = load_current_run_status(run_root)
    for initial_checkpoint in status.checkpoints:
        checkpoint = initial_checkpoint
        identity = ExecutionIdentity(
            checkpoint.entry, checkpoint.cid, checkpoint.execution_id
        )
        if checkpoint.state == "active":
            if checkpoint.permit_id is None:
                raise ActionError(
                    "reproduction.run.invariant",
                    "active recovery checkpoint has no scheduler permit",
                )
            workers = tuple(
                StoredWorkerRecord(
                    worker.worker_id,
                    worker.parent_worker_id,
                    worker.pid,
                    "exited",
                    worker.registered_at,
                    max(now, worker.last_observed_at),
                )
                for worker_identity, worker in status.workers
                if worker_identity == identity
            )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    checkpoint.entry,
                    checkpoint.cid,
                    checkpoint.execution_id,
                    checkpoint.permit_id,
                    "stopped",
                    now,
                    None,
                    checkpoint.elapsed_seconds,
                    failure_code="supervisor_lost",
                    failure_message="The durable supervisor was interrupted.",
                    failure_recorded_at=now,
                    workers=workers,
                ),
            )
            refreshed = load_execution_checkpoint(run_root, identity)
            if refreshed is None:
                raise ActionError(
                    "reproduction.run.invariant",
                    "recovery checkpoint disappeared after terminal commit",
                )
            checkpoint = refreshed
        if checkpoint.permit_id is not None:
            accepted = load_accepted_scheduling(run_root, identity)
            reconciliation = reconcile_permit(
                SchedulerIdentity(
                    project_root,
                    status.run_id,
                    checkpoint.entry,
                    checkpoint.cid,
                    checkpoint.execution_id,
                    accepted.plan_order,
                ),
                load_scheduler_owner(run_root),
            )
            if reconciliation.clear_run_permit_id is not None:
                clear_execution_permit(
                    run_root,
                    identity,
                    reconciliation.clear_run_permit_id,
                    cast(Literal["succeeded", "failed", "stopped"], checkpoint.state),
                    now,
                )
        if checkpoint.scratch_path is not None:
            _remove_current_scratch(Path(checkpoint.scratch_path))
            clear_execution_scratch(run_root, identity, checkpoint.scratch_path)


def _remove_current_scratch(path: Path) -> None:
    temporary = Path("/private/tmp")
    if (
        not path.is_absolute()
        or path.parent != temporary
        or not path.name.startswith("reproduction-scratch-")
    ):
        raise ActionError(
            "reproduction.scratch.invalid", "stored scratch path is not owned"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(temporary, flags)
    except OSError as error:
        raise ActionError(
            "reproduction.scratch.invalid", "scratch parent is unavailable"
        ) from error
    try:
        try:
            observed = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(observed.st_mode)
            or not shutil.rmtree.avoids_symlink_attacks
        ):
            raise ActionError(
                "reproduction.scratch.invalid", "stored scratch path is not owned"
            )
        try:
            cast(Any, shutil.rmtree)(path.name, dir_fd=directory_fd)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ActionError(
                "reproduction.scratch.invalid", "stored scratch path changed"
            ) from error
    finally:
        os.close(directory_fd)


def _require_no_recovery_exclusion(
    log: LogContext,
    entry: str | None,
    *,
    ignore_recovery_run_id: str | None,
) -> None:
    """Reject overlap while a current SQLite run still needs orphan recovery."""

    project_root = resolve_project_root(log.root)
    if not (project_root / "tmp").exists():
        return
    try:
        roots = iter_canonical_run_roots(project_root, max_entries=MAX_RUN_DIRECTORIES)
    except OSError as error:
        raise ActionError("reproduction.recovery.invalid", str(error)) from error
    for run_root in roots:
        if recognize_run_directory(run_root) != "current":
            continue
        try:
            plan = load_current_accepted_plan(run_root)
            if plan.summary != _summary_identity(log):
                continue
            status = load_run_control(run_root)
            owner = load_run_owner(run_root)
            run_id = load_current_run_status(run_root).run_id
        except JobStoreError as error:
            raise ActionError("reproduction.recovery.invalid", str(run_root)) from error
        if run_id == ignore_recovery_run_id:
            continue
        owner_live = (
            owner is not None
            and owner.state == "running"
            and _pid_alive(owner.supervisor_pid)
        )
        needs_recovery = (
            (status.status is None and status.phase == "stopping")
            or (owner is not None and owner.state == "running" and not owner_live)
            or (status.status is None and not owner_live)
        )
        target_entry = cast(str | None, plan.target["entry"])
        if needs_recovery and (
            entry is None or target_entry is None or entry == target_entry
        ):
            raise ActionError(
                "reproduction.recovery.active",
                f"orphaned worker cleanup still owns {run_id}",
            )


def _recovery_worker_observations(
    survivors: Sequence[Mapping[str, object]],
    *,
    observed_at: str,
) -> tuple[RecoveryWorkerObservation, ...]:
    """Convert one exhaustive process scan to durable worker observations."""

    observed: list[RecoveryWorkerObservation] = []
    for survivor in survivors:
        entry = survivor.get("entry")
        cid = survivor.get("cid")
        execution_id = survivor.get("execution_id")
        identity = (
            ExecutionIdentity(entry, cid, execution_id)
            if isinstance(entry, str)
            and isinstance(cid, str)
            and isinstance(execution_id, str)
            else None
        )
        registered_at = survivor.get("registered_at")
        last_observed_at = survivor.get("last_observed_at")
        observed.append(
            RecoveryWorkerObservation(
                identity,
                StoredWorkerRecord(
                    cast(str, survivor["worker_id"]),
                    cast(str | None, survivor.get("parent_worker_id")),
                    cast(int, survivor["pid"]),
                    "running",
                    registered_at if isinstance(registered_at, str) else observed_at,
                    last_observed_at
                    if isinstance(last_observed_at, str)
                    else observed_at,
                ),
            )
        )
    return tuple(observed)


def _terminate_marked_workers(run_id: str) -> list[Mapping[str, object]]:
    found: dict[int, tuple[psutil.Process, str | None, str | None, str | None]] = {}
    try:
        for process in psutil.process_iter(["pid"]):
            try:
                marker = process.environ().get("RESEARCH_LOG_REPRODUCTION_RUN_ID")
                if marker == run_id or (
                    isinstance(marker, str) and marker.startswith(f"{run_id}:")
                ):
                    entry, cid, execution = _marker_identity(run_id, marker)
                    found[process.pid] = (process, entry, cid, execution)
            except psutil.Error:
                continue
    except (OSError, psutil.Error) as error:
        raise ActionError(
            "reproduction.worker.inspection_unavailable", str(error)
        ) from error
    processes = [item[0] for item in found.values()]
    for process in processes:
        try:
            process.kill()
        except psutil.Error:
            pass
    _, live = psutil.wait_procs(processes, timeout=10.0)
    now = _utc_now()
    return [
        {
            "entry": found[process.pid][1],
            "cid": found[process.pid][2],
            "worker_id": f"worker-{process.pid}",
            "parent_worker_id": None,
            "pid": process.pid,
            "execution_id": found[process.pid][3],
            "state": "running",
            "registered_at": now,
            "last_observed_at": now,
        }
        for process in sorted(live, key=lambda item: item.pid)
    ]


def _marker_identity(
    run_id: str, marker: object
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(marker, str) or not marker.startswith(f"{run_id}:"):
        return None, None, None
    parts = marker.removeprefix(f"{run_id}:").split(":")
    if (
        len(parts) == 3
        and re.fullmatch(r"e[0-9]{3}", parts[0]) is not None
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", parts[1]) is not None
    ):
        digest = parts[2]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is not None:
            return parts[0], parts[1], f"pyrun-exec/v2:{digest}"
    return None, None, None


def load_accepted_plan(run_root: Path) -> ReproductionPlan:
    """Load the immutable plan from the current SQLite authority."""

    recognized = recognize_run_directory(run_root)
    if recognized == "historical_unsupported":
        raise ActionError(
            "reproduction.run.unsupported",
            "historical reproduction jobs are unsupported; start a new run",
        )
    if recognized != "current":
        raise ActionError("reproduction.run.invalid", "current job state is absent")
    try:
        return load_current_accepted_plan(run_root)
    except JobStoreError as error:
        raise ActionError(error.code, str(error)) from error


def _current_status_projection(status: object, run_root: Path) -> Mapping[str, object]:
    """Project the SQLite authority into the unchanged public status/7 shape."""

    from .reproduction_job_storage import RunStatus

    if not isinstance(status, RunStatus):
        raise ActionError("reproduction.run.invalid", "invalid current run status")
    active_keys = {
        (item.entry, item.cid, item.execution_id)
        for item in status.checkpoints
        if item.state == "active" and item.permit_id is not None
    }
    workers = [
        {
            **asdict(worker),
            "entry": None if identity is None else identity.entry,
            "cid": None if identity is None else identity.cid,
            "execution_id": None if identity is None else identity.execution_id,
        }
        for identity, worker in status.workers
    ]
    active = [
        {"entry": item.entry, "cid": item.cid, "execution_id": item.execution_id}
        for item in status.checkpoints
        if (item.entry, item.cid, item.execution_id) in active_keys
    ]
    timings = [
        {
            "elapsed_seconds": item.elapsed_seconds,
            "entry": item.entry,
            "cid": item.cid,
            "execution_id": item.execution_id,
            "failure": (
                None
                if item.failure_code is None
                else {
                    "code": item.failure_code,
                    "message": item.failure_message,
                    "recorded_at": item.failure_recorded_at,
                }
            ),
            "finished_at": item.finished_at,
            "started_at": item.started_at,
            "state": item.state,
        }
        for item in status.checkpoints
        if item.started_at is not None
    ]
    resumable = status.status == "stopped"
    if status.status == "failed":
        publication = load_current_publication_projection(run_root).publication
        resumable = (
            status.operational_failure is not None
            and status.operational_failure.code == "reproduction.publication.failed"
            and publication.stage in {"ready", "result_committed"}
            and publication.publication_identity is not None
        )
    projection = {
        "artifact_outcomes": dict(status.artifact_outcomes),
        "active_executions": active,
        "active_workers": [
            item
            for item in workers
            if item["state"] == "running"
            and (item["entry"], item["execution_id"]) in active_keys
        ],
        "completed_executions": status.completed_executions,
        "execution_timeout_seconds": status.execution_timeout_seconds,
        "execution_timings": timings,
        "include_all": status.include_all,
        "jobs": status.jobs,
        "latest_execution_diagnostic": (
            None
            if status.latest_execution_diagnostic is None
            else asdict(status.latest_execution_diagnostic)
        ),
        "operational_failure": (
            None
            if status.operational_failure is None
            else asdict(status.operational_failure)
        ),
        "phase": status.phase,
        "resumable": resumable,
        "run_id": status.run_id,
        "schema": STATUS_SCHEMA,
        "status": status.status,
        "summary": status.summary,
        "surviving_workers": [item for item in workers if item["state"] == "running"],
        "target": dict(status.target),
        "timestamps": {
            "accepted_at": status.accepted_at,
            "finished_at": status.finished_at,
            "resumed_at": status.resumed_at,
            "started_at": status.started_at,
            "stopped_at": status.stopped_at,
            "updated_at": status.updated_at,
        },
        "total_executions": status.total_executions,
    }
    return _bounded_status(projection)


def _bounded_status(value: Mapping[str, object]) -> Mapping[str, object]:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > MAX_STATUS_BYTES:
        raise ActionError(
            "reproduction.status.resource_limit",
            "status projection crossed its byte bound",
        )
    return value


def _find_run(log: LogContext, run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    try:
        candidates = iter_canonical_run_roots(
            resolve_project_root(log.root), max_entries=MAX_RUN_DIRECTORIES
        )
    except OSError as error:
        code = (
            "reproduction.run.resource_limit"
            if "scan limit" in str(error)
            else "reproduction.run.missing"
        )
        raise ActionError(code, str(error)) from error
    matches, unsupported = _matching_run_roots(log, run_id, candidates)
    discovered = len(matches) + len(unsupported)
    if discovered > 1:
        raise ActionError(
            "reproduction.run.integrity",
            f"expected one run, found {discovered}: {run_id}",
        )
    if unsupported:
        raise ActionError(
            "reproduction.run.unsupported",
            "historical reproduction run is unsupported; start a new current run",
        )
    if not matches:
        raise ActionError("reproduction.run.missing", f"run not found: {run_id}")
    return matches[0]


def _matching_run_roots(
    log: LogContext, run_id: str, candidates: Sequence[Path]
) -> tuple[list[Path], list[Path]]:
    """Classify exact current and historical candidates without decoding JSON."""

    matches: list[Path] = []
    unsupported: list[Path] = []
    for candidate in candidates:
        if not candidate.name.endswith(f"-{run_id}"):
            continue
        recognized = recognize_run_directory(candidate)
        if recognized == "current":
            try:
                status = load_current_run_status(candidate)
            except JobStoreError as error:
                raise ActionError(error.code, str(error)) from error
            if status.run_id == run_id and status.summary == _summary_identity(log):
                matches.append(candidate.resolve())
        elif recognized == "historical_unsupported" and _historical_run_matches_log(
            candidate, log, run_id
        ):
            unsupported.append(candidate.resolve())
    return matches, unsupported


def _historical_run_matches_log(candidate: Path, log: LogContext, run_id: str) -> bool:
    """Match only the legacy canonical leaf, without reading historical JSON."""

    log_leaf = run_leaf(log.root.name, None, run_id)
    prefix = log_leaf.removesuffix(run_id)
    return candidate.name == log_leaf or (
        candidate.name.startswith(prefix) and candidate.name.endswith(f"-{run_id}")
    )


def _new_run_root(
    project: Path,
    log: LogContext,
    entry: str | None,
    run_id: str,
    accepted_at: str,
) -> Path:
    leaf = run_leaf(log.root.name, entry, run_id)
    return project / canonical_run_path(accepted_at, leaf)


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"reproduce-{stamp}-{secrets.token_hex(6)}"


def _summary_identity(log: LogContext) -> str:
    return log.summary.resolve().relative_to(resolve_project_root(log.root)).as_posix()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _survivor_summary(value: object) -> str:
    workers = value if isinstance(value, list) else []
    return f"worker cleanup remains incomplete ({len(workers)} survivors)"


def _close_fds(values: Sequence[int]) -> None:
    for descriptor in values:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


if __name__ == "__main__":
    raise SystemExit(supervisor_main(sys.argv[1:]))
