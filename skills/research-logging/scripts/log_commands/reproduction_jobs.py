"""Durable launch, status, stop, resume, and supervision for reproduction."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, cast

import psutil
from research_log_data import DataContractError, parse_fingerprint
from validation.operation_state import (
    OperationLockError,
    operation_directory,
    operation_lock,
    operation_lock_owner,
    require_mutation_ready,
)

from .context import LogContext, resolve_entry, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_comparison import (
    ExecutionComparison,
    clear_execution_reproduction_requirement_locked,
    compare_execution_outputs,
    load_recorded_comparisons,
)
from .reproduction_contract import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    LEGACY_PLAN_SCHEMA,
    MAX_EXECUTION_TIMEOUT_SECONDS,
    PLAN_SCHEMA,
    PRECONTINUATION_PLAN_SCHEMA,
    PREEXECUTION_PLAN_SCHEMA,
    PRETIMEOUT_PLAN_SCHEMA,
    ReproductionPlan,
    ReproductionRuntime,
    is_repair_verification,
    successful_checkpoint_state,
    valid_reproduction_target,
)
from .reproduction_execution import (
    ExecutionAttempt,
    ExecutionControl,
    ReproductionWorkspace,
    WorkerRecord,
    cleanup_reproduction_scratch,
    completed_execution_attempts,
    execute_reproduction_plan,
    open_existing_workspace,
    populate_output_workspace,
    preflight_execution_safety,
)
from .reproduction_paths import (
    canonical_run_path,
    is_checkpoint_temporary_name,
    iter_canonical_run_roots,
    run_leaf,
)
from .reproduction_planner import (
    RESUME_SELECTION,
    ReproductionSelection,
    plan_reproduction,
    verify_reproduction_runtime_snapshot,
    verify_reproduction_snapshot,
)
from .reproduction_publication import (
    CompletedPublication,
    publish_completed_reproduction,
    verify_publication_retry_compatibility,
)
from .reproduction_results import OUTCOMES
from .storage import atomic_write_text

LEGACY_RUN_SCHEMA = "research-log-reproduction-run/2"
PRECONTINUATION_RUN_SCHEMA = "research-log-reproduction-run/3"
PRETIMEOUT_RUN_SCHEMA = "research-log-reproduction-run/4"
PREEXECUTION_RUN_SCHEMA = "research-log-reproduction-run/5"
RUN_SCHEMA = "research-log-reproduction-run/6"
LEGACY_STATUS_SCHEMA = "research-log-reproduction-status/2"
PRECONTINUATION_STATUS_SCHEMA = "research-log-reproduction-status/3"
PRETIMEOUT_STATUS_SCHEMA = "research-log-reproduction-status/4"
PREEXECUTION_STATUS_SCHEMA = "research-log-reproduction-status/5"
STATUS_SCHEMA = "research-log-reproduction-status/6"
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
EXECUTION_ID_RE = re.compile(r"pyrun-exec/v1:[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
ACTIVE_PHASES = {
    "accepted",
    "planning",
    "preflight",
    "executing",
    "comparing",
    "publishing",
    "stopping",
}
TERMINAL_STATUSES = {"complete", "stopped", "failed"}
MAX_RUN_RECORD_BYTES = 256 * 1024 * 1024
MAX_STATUS_BYTES = 64 * 1024 * 1024
MAX_RUN_DIRECTORIES = 100_000
MAX_CHECKPOINTS = 2_048
MAX_CHECKPOINT_DIRECTORY_ENTRIES = 2 * MAX_CHECKPOINTS
STOP_WAIT_SECONDS = 45.0
STATUS_POLL_SECONDS = 0.1
FRESH_RUN = "fresh"
STOPPED_RESUME = "stopped"
PUBLICATION_RETRY = "publication"
CONTINUATION = "continuation"
LEGACY_VALIDATION_BLOCKED_PUBLICATION_FAILURE = (
    "unsupported artifact reason: 'validation_blocked'"
)
LEGACY_RUN_INVALID_PUBLICATION_FAILURE = (
    "unsupported artifact reason: 'reproduction.run.invalid'"
)


def _parallel_run(record: Mapping[str, object]) -> bool:
    return record.get("schema") in {
        PRECONTINUATION_RUN_SCHEMA,
        PRETIMEOUT_RUN_SCHEMA,
        PREEXECUTION_RUN_SCHEMA,
        RUN_SCHEMA,
    }


def _continuation_run(record: Mapping[str, object]) -> bool:
    return record.get("schema") in {
        PRETIMEOUT_RUN_SCHEMA,
        PREEXECUTION_RUN_SCHEMA,
        RUN_SCHEMA,
    }


@dataclass(frozen=True)
class _FailureContext:
    now: str | None = None
    entry: str | None = None
    include_entry: bool = False


@dataclass(frozen=True)
class _ResumeContext:
    root: Path
    record: Mapping[str, object]
    state: Mapping[str, object]
    publication_retry: bool
    continuing: bool
    entry: str | None


@dataclass(frozen=True)
class _CheckpointTerminal:
    state: str
    code: str
    message: str


@dataclass(frozen=True)
class _RunStateContext:
    log: LogContext
    root: Path
    run_id: str


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


_RUN_STATE_THREAD_LOCK = threading.Lock()


def launch_reproduction(
    log: LogContext,
    *,
    entry: str | None,
    include_all: bool,
    runtime: ReproductionRuntime = ReproductionRuntime(),
    selection: ReproductionSelection = ReproductionSelection(),
) -> ReproductionLaunch:
    """Return a no-work summary or hand an accepted plan to a supervisor."""

    selected = resolve_entry(log, entry) if entry is not None else None
    plan = plan_reproduction(
        log,
        entry=selected,
        include_all=include_all,
        runtime=runtime,
        selection=selection,
    )
    if not plan.executions:
        from .reproduction_queries import reproduction_reconciliation_text

        if selection.policy == "recheck" and entry is None:
            from .reproduction_publication import (
                empty_reproduction_recovery_needed,
                recover_empty_reproduction_results,
            )

            if empty_reproduction_recovery_needed(log, plan):
                lock_fds = _acquire_scope_locks(log, None)
                try:
                    recover_empty_reproduction_results(log, plan, updated_at=_utc_now())
                finally:
                    _close_fds(lock_fds)
        return ReproductionLaunch(
            summary=reproduction_reconciliation_text(
                log,
                plan,
                generated_at=_utc_now(),
            )
        )
    project = resolve_project_root(log.root)
    _require_no_active_legacy_run(project)
    run_id = _new_run_id()
    accepted_at = _utc_now()
    run_root = _new_run_root(project, log, entry, run_id, accepted_at)
    lock_fds = _acquire_scope_locks(log, entry)
    try:
        with operation_lock(log.root, "reproduction-publication.lock"):
            with operation_lock(project, "reproduction-promotion-index.lock"):
                _require_no_promotion_conflict(log, plan)
                run_root.mkdir(parents=True)
                accepted = _accepted_record(
                    log,
                    plan,
                    run_id,
                    run_root,
                    accepted_at=accepted_at,
                )
                atomic_write_text(run_root / "run.json", _canonical(accepted))
        _spawn_supervisor(log, run_root, lock_fds, mode=FRESH_RUN)
    except BaseException:
        _close_fds(lock_fds)
        raise
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

    selected = resolve_entry(log, entry) if entry is not None else None
    plan = plan_reproduction(
        log,
        entry=selected,
        include_all=include_all,
        runtime=runtime,
        selection=selection,
    )
    preflight_execution_safety()
    verify_reproduction_snapshot(log, plan)
    return plan


def reproduction_status(
    log: LogContext, run_id: str, *, reconcile: bool = True
) -> Mapping[str, object]:
    """Return the frozen deterministic status projection for one run."""

    root = _find_run(log, run_id)
    if reconcile:
        _reconcile_lost_supervisor(log, root, run_id)
    record = _load_run(root / "run.json")
    return _status_projection(record)


def format_reproduction_status(status: Mapping[str, object]) -> str:
    """Compose concise human status without hiding failures."""

    state = status.get("status") or status.get("phase")
    progress = f"{status['completed_executions']}/{status['total_executions']}"
    lines = [f"Run {status['run_id']}: {state} ({progress} executions)"]
    if "execution_timeout_seconds" in status:
        lines.append(
            f"Per-command runtime limit: {status['execution_timeout_seconds']} seconds"
        )
    if "attempt" in status:
        lines.append(
            f"Attempt {status['attempt']}; logical queue "
            + ("resolved" if status["resolved"] else "unresolved")
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
    _reconcile_lost_supervisor(log, root, run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(root / "run.json")
        state = cast(dict[str, object], record["state"])
        if state["status"] == "stopped":
            return _status_projection(record)
        if state["status"] is not None:
            raise ActionError(
                "reproduction.stop.invalid_state",
                f"run is already {state['status']}",
            )
        state["phase"] = "stopping"
        _touch(root / "stop.request")
        _stamp(record)
        _write_run(root, record)
    from .reproduction_scheduler import cancel_scheduling_waiters

    cancel_scheduling_waiters(resolve_project_root(log.root), run_id)
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        status = reproduction_status(log, run_id)
        if status["status"] == "stopped":
            return status
        if status["status"] == "failed":
            raise ActionError(
                "reproduction.stop.failed", "run failed before stop completed"
            )
        if status["status"] == "complete":
            raise ActionError(
                "reproduction.stop.completed", "run completed before stop took effect"
            )
        time.sleep(STATUS_POLL_SECONDS)
    status = reproduction_status(log, run_id)
    raise ActionError(
        "reproduction.stop.incomplete",
        _survivor_summary(status.get("surviving_workers")),
    )


def resume_reproduction(log: LogContext, run_id: str) -> str:
    """Continue one logical reproduction or retry its failed publication."""

    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root, run_id)
    record = _load_run(root / "run.json")
    _verify_checkpoint_inventory(root, record)
    context = _resume_context(root, record)
    plan = _resume_plan(log, context)
    if context.publication_retry:
        verify_publication_retry_compatibility(log, plan)
    if context.continuing and not plan.executions:
        from .reproduction_queries import reproduction_reconciliation_text

        return reproduction_reconciliation_text(log, plan, generated_at=_utc_now())
    lock_fds = _acquire_scope_locks(log, context.entry)
    try:
        _verify_resume_plan(log, context, plan)
        (root / "stop.request").unlink(missing_ok=True)
        _install_resume_state(log, run_id, context, plan, now=_utc_now())
        _spawn_supervisor(
            log,
            root,
            lock_fds,
            mode=(
                PUBLICATION_RETRY
                if context.publication_retry
                else CONTINUATION
                if context.continuing
                else STOPPED_RESUME
            ),
        )
    except BaseException:
        _close_fds(lock_fds)
        raise
    _close_fds(lock_fds)
    return run_id


def _resume_context(root: Path, record: Mapping[str, object]) -> _ResumeContext:
    state = cast(Mapping[str, object], record["state"])
    publication_retry = _is_publication_retry(record)
    continuing = _continuation_run(record) and not publication_retry
    if continuing and (
        state["status"] not in TERMINAL_STATUSES
        or state["status"] == "complete"
        and _logical_queue_resolved(record)
    ):
        raise ActionError(
            "reproduction.resume.invalid_state",
            "the logical reproduction is not resumable",
        )
    if not continuing and state["status"] != "stopped" and not publication_retry:
        raise ActionError(
            "reproduction.resume.invalid_state",
            "only a stopped run or failed reproduction publication can resume",
        )
    target = cast(Mapping[str, object], record["target"])
    return _ResumeContext(
        root,
        record,
        state,
        publication_retry,
        continuing,
        cast(str | None, target["entry"]),
    )


def _resume_plan(log: LogContext, context: _ResumeContext) -> ReproductionPlan:
    if not context.continuing:
        return _plan_from_record(context.record)
    queue = cast(Sequence[Mapping[str, object]], context.record["queue"])
    scope = frozenset(
        (cast(str, item["entry"]), cast(str, item["execution_id"])) for item in queue
    )
    queued = frozenset(
        (cast(str, item["entry"]), cast(str, item["execution_id"]))
        for item in queue
        if item.get("queued") is True
    )
    return plan_reproduction(
        log,
        entry=resolve_entry(log, context.entry) if context.entry is not None else None,
        include_all=cast(bool, context.record["include_all"]),
        runtime=ReproductionRuntime(
            cast(int, context.record["jobs"]),
            cast(
                int,
                context.record.get(
                    "execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT_SECONDS
                ),
            ),
        ),
        selection=ReproductionSelection(
            RESUME_SELECTION,
            verify_repair=is_repair_verification(_plan_from_record(context.record)),
            execution_id=cast(Mapping[str, str], context.record["target"]).get(
                "execution_id"
            ),
            command_queue=queued,
            command_scope=scope,
            prior_commands=_continuation_prior_commands(context.record),
        ),
    )


def _verify_resume_plan(
    log: LogContext, context: _ResumeContext, plan: ReproductionPlan
) -> None:
    if context.continuing:
        verify_reproduction_snapshot(log, plan)
    else:
        verify_reproduction_runtime_snapshot(log, plan)


def _install_resume_state(
    log: LogContext,
    run_id: str,
    context: _ResumeContext,
    plan: ReproductionPlan,
    *,
    now: str,
) -> None:
    with _run_state_lock(log, run_id):
        updated = _load_run(context.root / "run.json")
        _require_run_identity(updated, run_id)
        updated_state = cast(dict[str, object], updated["state"])
        current_publication_retry = _is_publication_retry(updated)
        if context.continuing:
            if (
                not _continuation_run(updated)
                or updated_state["status"] != context.state["status"]
                or current_publication_retry
            ):
                raise ActionError(
                    "reproduction.resume.invalid_state",
                    "run state changed before continuation",
                )
            _begin_continuation_attempt(updated, plan, context.root, now=now)
        elif updated_state["status"] != "stopped" and not current_publication_retry:
            raise ActionError(
                "reproduction.resume.invalid_state",
                "run state changed before resume",
            )
        if current_publication_retry != context.publication_retry:
            raise ActionError(
                "reproduction.resume.invalid_state",
                "run recovery mode changed before resume",
            )
        if not context.continuing:
            updated_state.update(
                {"status": None, "phase": "accepted", "operational_failure": None}
            )
            _clear_active(updated_state)
            timestamps = cast(dict[str, object], updated["timestamps"])
            timestamps.update(
                {"resumed_at": now, "stopped_at": None, "updated_at": now}
            )
        _write_run(context.root, updated)


def _begin_continuation_attempt(
    record: dict[str, object],
    plan: ReproductionPlan,
    root: Path,
    *,
    now: str,
) -> None:
    """Archive the terminal attempt and install one fresh immutable plan."""

    attempt = cast(int, record["attempt"])
    archived = {
        "attempt": attempt,
        "checkpoints": copy.deepcopy(record["checkpoints"]),
        "plan": copy.deepcopy(record["plan"]),
        "progress": copy.deepcopy(record["progress"]),
        "source_snapshot": copy.deepcopy(record["source_snapshot"]),
        "state": copy.deepcopy(record["state"]),
        "timestamps": copy.deepcopy(record["timestamps"]),
        "validation_snapshot": copy.deepcopy(record["validation_snapshot"]),
        "workers": copy.deepcopy(record["workers"]),
    }
    _archive_attempt_files(root, attempt)
    cast(list[object], record["attempts"]).append(archived)
    plan_value = plan.as_dict()
    plan_value.pop("schema")
    if record.get("schema") == PRETIMEOUT_RUN_SCHEMA:
        plan_value.pop("execution_timeout_seconds")
    record.update(
        {
            "attempt": attempt + 1,
            "checkpoints": [],
            "plan": plan_value,
            "progress": {
                "artifact_outcomes": {name: 0 for name in OUTCOMES},
                "completed_executions": 0,
                "total_executions": len(plan.executions),
            },
            "source_snapshot": dict(plan.source_snapshot),
            "validation_snapshot": dict(plan.validation_snapshot),
            "workers": [],
        }
    )
    cast(dict[str, object], record["state"]).update(
        {
            "active_executions": [],
            "latest_execution_diagnostic": None,
            "operational_failure": None,
            "phase": "accepted",
            "status": None,
        }
    )
    accepted_at = cast(Mapping[str, object], archived["timestamps"])["accepted_at"]
    record["timestamps"] = {
        "accepted_at": accepted_at,
        "finished_at": None,
        "resumed_at": now,
        "started_at": None,
        "stopped_at": None,
        "updated_at": now,
    }


def _archive_attempt_files(root: Path, attempt: int) -> None:
    destination = root / "attempts" / f"{attempt:04d}"
    destination.mkdir(parents=True)
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name in {"attempts", "run.json"}:
            continue
        path.replace(destination / path.name)


def _continuation_prior_commands(
    record: Mapping[str, object],
) -> Mapping[tuple[str, str], Mapping[str, object]]:
    """Project terminal failed and blocked state from the current attempt."""

    snapshots = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in cast(
            Sequence[Mapping[str, object]],
            cast(Mapping[str, object], record["source_snapshot"])["commands"],
        )
    }
    checkpoints = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in cast(Sequence[Mapping[str, object]], record["checkpoints"])
    }
    prior: dict[tuple[str, str], Mapping[str, object]] = {}
    for key, snapshot in snapshots.items():
        disposition = (
            "blocked"
            if snapshot.get("selection") == "blocked"
            else "succeeded"
            if successful_checkpoint_state(checkpoints.get(key, {}).get("state"))
            else "failed"
            if checkpoints.get(key, {}).get("state") == "failed"
            else None
        )
        digest = snapshot.get("source_digest")
        if disposition is not None and isinstance(digest, str):
            prior[key] = {"disposition": disposition, "source_digest": digest}
    return prior


def _is_publication_retry(record: Mapping[str, object]) -> bool:
    """Recognize canonical and one exact legacy terminal publication failure."""

    state = cast(Mapping[str, object], record["state"])
    timestamps = cast(Mapping[str, object], record["timestamps"])
    current_schema = _parallel_run(record)
    inactive = (
        state.get("active_executions") == []
        if current_schema
        else state.get("current_execution") is None
    )
    terminal_checkpoints = (
        {"succeeded", "failed"} if current_schema else {"complete", "partial"}
    )
    terminal = (
        state.get("status") == "failed"
        and state.get("phase") is None
        and isinstance(timestamps.get("finished_at"), str)
        and inactive
        and all(
            item.get("state") == "exited"
            for item in cast(Sequence[Mapping[str, object]], record["workers"])
        )
        and all(
            item.get("state") in terminal_checkpoints
            for item in cast(Sequence[Mapping[str, object]], record["checkpoints"])
        )
    )
    if not terminal:
        return False
    operational = state.get("operational_failure")
    if not isinstance(operational, Mapping):
        return False
    if operational.get("code") == "reproduction.publication.failed":
        return True
    return (
        _parallel_run(record)
        and operational.get("code") == "reproduction.job.failed"
        and operational.get("message")
        in {
            LEGACY_RUN_INVALID_PUBLICATION_FAILURE,
            LEGACY_VALIDATION_BLOCKED_PUBLICATION_FAILURE,
        }
        and operational.get("entry") is None
        and operational.get("execution_id") is None
    )


def supervise_reproduction(
    log: LogContext,
    run_root: Path,
    *,
    mode: str,
    inherited_locks: Sequence[int],
    confinement: Any = None,
) -> None:
    """Run one accepted job to a terminal state while retaining its locks."""

    if mode not in {CONTINUATION, FRESH_RUN, STOPPED_RESUME, PUBLICATION_RETRY}:
        raise ActionError(
            "reproduction.run.invalid", f"invalid supervisor mode: {mode}"
        )
    resume = mode in {STOPPED_RESUME, PUBLICATION_RETRY}
    record = _load_run(run_root / "run.json")
    _verify_checkpoint_inventory(run_root, record)
    plan = _plan_from_record(record)
    run_id = cast(str, record["run_id"])
    run_state = _RunStateContext(log, run_root, run_id)
    try:
        _transition(run_state, phase="preflight", started=True)
        preflight_execution_safety(confinement)
        workspace = (
            open_existing_workspace(resolve_project_root(log.root), run_root, run_id)
            if resume
            else populate_output_workspace(
                resolve_project_root(log.root), run_root, run_id
            )
        )
        comparisons = {
            (item.entry, item.execution_id): item
            for item in load_recorded_comparisons(plan, workspace)
        }
        for comparison in comparisons.values():
            clear_execution_reproduction_requirement_locked(
                log,
                plan,
                comparison,
                project_root=workspace.source_project,
            )
        for attempt in completed_execution_attempts(
            log,
            plan,
            workspace,
            legacy=record.get("schema") == LEGACY_RUN_SCHEMA,
        ):
            key = (attempt.entry, attempt.execution_id)
            if key not in comparisons:
                comparison = _compare_and_confirm(log, plan, workspace, attempt)
                comparisons[key] = comparison
        resumable = _resumable_execution_references(record, mode=mode)
        checkpoint_failures = _failed_checkpoint_references(record)
        prior_failures = (
            frozenset(
                f"{item.entry}:{item.execution_id}"
                for item in comparisons.values()
                if not item.complete
                and f"{item.entry}:{item.execution_id}" not in resumable
            )
            | checkpoint_failures
        )
        prior_attempts = (
            frozenset(
                f"{item.entry}:{item.execution_id}"
                for item in comparisons.values()
                if f"{item.entry}:{item.execution_id}" not in resumable
            )
            | checkpoint_failures
        )

        def completed(attempt: ExecutionAttempt) -> None:
            comparison = _compare_and_confirm(log, plan, workspace, attempt)
            comparisons[(attempt.entry, attempt.execution_id)] = comparison

        _transition(run_state, phase="executing")
        batch = execute_reproduction_plan(
            log,
            plan,
            workspace,
            ExecutionControl(
                resume=resume,
                execution_timeout_seconds=plan.execution_timeout_seconds,
                stop_requested=lambda: (run_root / "stop.request").exists(),
                confinement=confinement,
                prior_attempts=prior_attempts,
                prior_failures=prior_failures,
                legacy=record.get("schema") == LEGACY_RUN_SCHEMA,
                attempt_completed=completed,
                progress=lambda event, entry, identity, attempt: _execution_progress(
                    run_state, event, (entry, identity), attempt
                ),
                worker_progress=lambda entry, identity, workers: _execution_workers(
                    run_state, entry, identity, workers
                ),
            ),
        )
        if batch.stopped:
            _finish_stopped(log, run_root, run_id, batch.attempts)
            return
        _transition(run_state, phase="comparing", current_execution=None)
        verify_reproduction_runtime_snapshot(log, plan)
        recorded_comparisons = load_recorded_comparisons(
            plan, workspace, verify_outputs=False
        )
        _transition(run_state, phase="publishing")
        current = _load_run(run_root / "run.json")
        accepted_at = cast(Mapping[str, str | None], current["timestamps"])[
            "accepted_at"
        ]
        finished = _utc_now()
        published = publish_completed_reproduction(
            log,
            CompletedPublication(
                plan,
                recorded_comparisons,
                run_id,
                cast(str, accepted_at),
                finished,
                run_root,
                batch.dependency_skips,
            ),
        )
        run = next(item for item in published.results.runs if item.run_id == run_id)
        _finish_complete(log, run_root, run_id, run.artifact_outcomes, finished)
        _close_fds(inherited_locks)
        _validate_reproduced_log(log)
    except BaseException as error:
        _finish_failed(log, run_root, run_id, error)


def _compare_and_confirm(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    attempt: ExecutionAttempt,
) -> ExecutionComparison:
    comparison = compare_execution_outputs(log, plan, workspace, attempt)
    clear_execution_reproduction_requirement_locked(
        log,
        plan,
        comparison,
        project_root=workspace.source_project,
    )
    return comparison


def _resumable_execution_references(
    record: Mapping[str, object], *, mode: str
) -> frozenset[str]:
    if mode != STOPPED_RESUME:
        return frozenset()
    if record.get("schema") != LEGACY_RUN_SCHEMA:
        return frozenset(
            f"{item['entry']}:{item['execution_id']}"
            for item in cast(Sequence[Mapping[str, object]], record["checkpoints"])
            if item["state"] == "stopped"
        )
    reference = _legacy_resumable_execution_reference(record)
    return frozenset((reference,)) if reference is not None else frozenset()


def _failed_checkpoint_references(record: Mapping[str, object]) -> frozenset[str]:
    if record.get("schema") == LEGACY_RUN_SCHEMA:
        return frozenset()
    return frozenset(
        f"{item['entry']}:{item['execution_id']}"
        for item in cast(Sequence[Mapping[str, object]], record["checkpoints"])
        if item["state"] == "failed"
    )


def _legacy_resumable_execution_reference(record: Mapping[str, object]) -> str | None:
    diagnostic = cast(Mapping[str, object], record["state"]).get(
        "latest_execution_diagnostic"
    )
    if not isinstance(diagnostic, Mapping):
        return None
    execution_id = diagnostic.get("execution_id")
    if not isinstance(execution_id, str):
        return None
    target = cast(Mapping[str, object], record["target"])
    entry = target.get("entry")
    if isinstance(entry, str):
        return f"{entry}:{execution_id}"
    stored_plan = cast(Mapping[str, object], record["plan"])
    for planned in cast(Sequence[Mapping[str, object]], stored_plan["executions"]):
        if planned.get("execution_id") == execution_id:
            return f"{planned['entry']}:{execution_id}"
    return None


def _validate_reproduced_log(log: LogContext) -> None:
    """Run ordinary validation after reproduction releases its scope lock."""

    from .validation_adapter import evaluate_validation

    try:
        evaluate_validation(log.summary)
    except Exception as error:
        print(f"Post-reproduction validation did not complete: {error}")


def supervisor_main(arguments: Sequence[str]) -> int:
    """Internal detached-supervisor process entrypoint."""

    if len(arguments) != 4:
        return 2
    summary, run_root, raw_fds, raw_resume = arguments
    if raw_resume not in {"0", "1", "2", "3"}:
        return 2
    fds = tuple(int(value) for value in raw_fds.split(",") if value)
    try:
        log = resolve_log(Path(summary).with_suffix(""))
        supervise_reproduction(
            log,
            Path(run_root),
            mode=(
                STOPPED_RESUME
                if raw_resume == "1"
                else PUBLICATION_RETRY
                if raw_resume == "2"
                else CONTINUATION
                if raw_resume == "3"
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
        CONTINUATION: "3",
    }.get(mode)
    if encoded_mode is None:
        raise ActionError("reproduction.run.invalid", "invalid supervisor mode")
    environment = dict(os.environ)
    scripts = str(Path(__file__).resolve().parents[1])
    prior = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = scripts if not prior else f"{scripts}:{prior}"
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
            ],
            cwd=resolve_project_root(log.root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
            pass_fds=tuple(lock_fds),
        )
    atomic_write_text(
        run_root / "supervisor.json",
        _canonical({"pid": process.pid, "started_at": _utc_now()}),
    )


def _acquire_scope_locks(log: LogContext, entry: str | None) -> tuple[int, ...]:
    directory = operation_directory(log.root)
    directory.mkdir(parents=True, exist_ok=True)
    require_mutation_ready(log.root, entry_id=entry)
    requests = (
        (("log.lock", fcntl.LOCK_EX),)
        if entry is None
        else (
            ("log.lock", fcntl.LOCK_SH),
            (f"entry-{entry}.lock", fcntl.LOCK_EX),
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
    except BaseException:
        _close_fds(opened)
        raise
    return tuple(opened)


def _require_no_promotion_conflict(log: LogContext, plan: ReproductionPlan) -> None:
    materials = cast(Sequence[Mapping[str, object]], plan.source_snapshot["materials"])
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
        overlap = inputs & {Path(item).resolve() for item in outputs}
        if overlap:
            raise ActionError(
                "reproduction.promotion.conflict",
                f"active promotion changes a reproduction input: {min(overlap)}",
            )


@contextmanager
def _run_state_lock(log: LogContext, run_id: str) -> Iterator[None]:
    digest = run_id.removeprefix("reproduce-")
    with _RUN_STATE_THREAD_LOCK:
        with operation_lock(log.root, f"reproduction-state-{digest}.lock"):
            yield


@contextmanager
def _locked_run(run: _RunStateContext) -> Iterator[dict[str, object]]:
    with _run_state_lock(run.log, run.run_id):
        record = _load_run(run.root / "run.json")
        _require_run_identity(record, run.run_id)
        yield record


def _require_run_identity(record: Mapping[str, object], run_id: str) -> None:
    if record.get("run_id") != run_id:
        raise ActionError("reproduction.run.invalid", "run ID changed")


def _execution_progress(
    run: _RunStateContext,
    event: str,
    execution: tuple[str, str],
    attempt: ExecutionAttempt | None,
) -> None:
    entry, identity = execution
    with _locked_run(run) as record:
        state = cast(dict[str, object], record["state"])
        progress = cast(dict[str, object], record["progress"])
        if "active_executions" not in state:
            state["current_execution"] = identity if event == "started" else None
        else:
            reference = _execution_progress_reference(entry, identity)
            if _plan_order(record, reference) == 2**31:
                raise ActionError(
                    "reproduction.run.invalid",
                    f"execution is absent from accepted plan: {entry}:{identity}",
                )
            if event == "started":
                active = cast(list[Mapping[str, object]], state["active_executions"])
                if reference not in active:
                    active.append(reference)
                    active.sort(key=lambda item: _plan_order(record, item))
            else:
                state["active_executions"] = [
                    item
                    for item in cast(
                        list[Mapping[str, object]], state["active_executions"]
                    )
                    if item != reference
                ]
        if event == "finished":
            progress["completed_executions"] = (
                cast(int, progress["completed_executions"]) + 1
            )
        if attempt is not None:
            prior_workers: dict[tuple[str, str, str], Mapping[str, object]] = {
                (
                    str(item.get("entry") or ""),
                    str(item.get("execution_id") or ""),
                    cast(str, item["worker_id"]),
                ): item
                for item in cast(Sequence[Mapping[str, object]], record["workers"])
            }
            for item in attempt.workers:
                value = item.as_dict()
                if "active_executions" not in state:
                    value.pop("entry", None)
                prior_workers[
                    (
                        str(value.get("entry") or ""),
                        str(value.get("execution_id") or ""),
                        cast(str, value["worker_id"]),
                    )
                ] = value
            record["workers"] = sorted(
                prior_workers.values(),
                key=lambda item: (
                    str(item.get("entry") or ""),
                    str(item.get("execution_id") or ""),
                    str(item["worker_id"]),
                ),
            )
            record["checkpoints"] = _checkpoint_dicts(
                run.root, legacy=record.get("schema") == LEGACY_RUN_SCHEMA
            )
            if attempt.failure_code is not None:
                state["latest_execution_diagnostic"] = _failure(
                    attempt.failure_code,
                    attempt.failure_message or "Execution failed.",
                    identity,
                    _FailureContext(
                        entry=attempt.entry,
                        include_entry="active_executions" in state,
                    ),
                )
        _stamp(record)
        _write_run(run.root, record)


def _execution_progress_reference(
    entry: str,
    identity: str,
) -> Mapping[str, object]:
    return {"entry": entry, "execution_id": identity}


def _execution_workers(
    run: _RunStateContext,
    entry: str,
    identity: str,
    workers: Sequence[WorkerRecord],
) -> None:
    """Persist the current complete worker history for one active execution."""

    with _locked_run(run) as record:
        legacy = record.get("schema") == LEGACY_RUN_SCHEMA
        retained = [
            item
            for item in cast(Sequence[Mapping[str, object]], record["workers"])
            if (
                item.get("execution_id") != identity
                if legacy
                else (item.get("entry"), item.get("execution_id")) != (entry, identity)
            )
        ]
        current = [item.as_dict() for item in workers]
        if legacy:
            for item in current:
                item.pop("entry", None)
        record["workers"] = sorted(
            (*retained, *current),
            key=lambda item: _worker_sort_key(item, legacy=legacy),
        )
        _stamp(record)
        _write_run(run.root, record)


def _plan_order(record: Mapping[str, object], reference: Mapping[str, object]) -> int:
    for item in cast(
        Sequence[Mapping[str, object]],
        cast(Mapping[str, object], record["plan"])["executions"],
    ):
        if (
            isinstance(item, Mapping)
            and item.get("entry") == reference.get("entry")
            and item.get("execution_id") == reference.get("execution_id")
        ):
            return cast(int, item["order"])
    return 2**31


def _plan_item(
    record: Mapping[str, object], reference: Mapping[str, object]
) -> Mapping[str, object]:
    for item in cast(
        Sequence[Mapping[str, object]],
        cast(Mapping[str, object], record["plan"])["executions"],
    ):
        if item.get("entry") == reference.get("entry") and item.get(
            "execution_id"
        ) == reference.get("execution_id"):
            return item
    return {}


def _worker_sort_key(
    item: Mapping[str, object], *, legacy: bool
) -> tuple[str, str, str]:
    return (
        "" if legacy else str(item.get("entry") or ""),
        "" if legacy else str(item.get("execution_id") or ""),
        str(item["worker_id"]),
    )


def _clear_active(state: dict[str, object]) -> None:
    if "active_executions" in state:
        state["active_executions"] = []
    else:
        state["current_execution"] = None


def _transition(
    run: _RunStateContext,
    *,
    phase: str,
    current_execution: str | None | object = ...,
    started: bool = False,
) -> None:
    with _locked_run(run) as record:
        state = cast(dict[str, object], record["state"])
        state["phase"] = phase
        if current_execution is not ...:
            if "active_executions" in state:
                state["active_executions"] = []
            else:
                state["current_execution"] = current_execution
        timestamps = cast(dict[str, object], record["timestamps"])
        if started and timestamps["started_at"] is None:
            timestamps["started_at"] = _utc_now()
        _stamp(record)
        _write_run(run.root, record)


def _finish_stopped(
    log: LogContext,
    run_root: Path,
    run_id: str,
    attempts: Sequence[ExecutionAttempt],
    *,
    wait_for_retry: bool = True,
) -> None:
    latest = attempts[-1] if attempts else None
    observed_survivors = _stop_workers_and_clean_scratch(run_root, run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        legacy = record.get("schema") == LEGACY_RUN_SCHEMA
        if legacy:
            for worker in observed_survivors:
                cast(dict[str, object], worker).pop("entry", None)
        workers = _combined_attempt_workers(
            attempts,
            legacy=legacy,
            prior=cast(Sequence[Mapping[str, object]], record["workers"]),
        )
        workers = _reconciled_worker_history(workers, observed_survivors, legacy=legacy)
        survivors = [item for item in workers if item["state"] == "running"]
        if survivors:
            state = cast(dict[str, object], record["state"])
            state.update({"status": None, "phase": "stopping"})
            if latest is not None:
                state["latest_execution_diagnostic"] = _failure(
                    "worker_cleanup_incomplete",
                    latest.failure_message or "One or more workers survived shutdown.",
                    latest.execution_id,
                    _FailureContext(
                        entry=latest.entry,
                        include_entry="active_executions" in state,
                    ),
                )
            record["workers"] = workers
            _stamp(record)
            _write_run(run_root, record)
    if survivors:
        if wait_for_retry:
            _wait_for_cleanup_retry(log, run_root, run_id)
        return
    from .reproduction_scheduler import release_run_scheduling

    release_run_scheduling(resolve_project_root(log.root), run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        legacy = record.get("schema") == LEGACY_RUN_SCHEMA
        workers = _combined_attempt_workers(
            attempts,
            legacy=legacy,
            prior=cast(Sequence[Mapping[str, object]], record["workers"]),
        )
        workers = _reconciled_worker_history(workers, (), legacy=legacy)
        now = _utc_now()
        state = cast(dict[str, object], record["state"])
        state.update({"status": "stopped", "phase": None})
        _clear_active(state)
        state["latest_execution_diagnostic"] = _failure(
            latest.failure_code if latest else "stop_requested",
            latest.failure_message
            if latest
            else "Reproduction was stopped by request.",
            latest.execution_id if latest else None,
            _FailureContext(
                now=now,
                entry=latest.entry if latest else None,
                include_entry="active_executions" in state,
            ),
        )
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"stopped_at": now, "updated_at": now})
        record["workers"] = workers
        record["checkpoints"] = _checkpoint_dicts(run_root, legacy=legacy)
        _write_run(run_root, record)


def _combined_attempt_workers(
    attempts: Sequence[ExecutionAttempt],
    *,
    legacy: bool,
    prior: Sequence[Mapping[str, object]] = (),
) -> list[Mapping[str, object]]:
    """Return every attempt worker in stable compound-identity order."""

    retained = {_worker_identity(item, legacy=legacy): dict(item) for item in prior}
    for attempt in attempts:
        for worker in attempt.workers:
            value = worker.as_dict()
            key = _worker_identity(value, legacy=legacy)
            if legacy:
                value.pop("entry")
            retained[key] = value
    return sorted(
        retained.values(), key=lambda item: _worker_sort_key(item, legacy=legacy)
    )


def _worker_identity(
    item: Mapping[str, object], *, legacy: bool
) -> tuple[object, object, object]:
    return (
        None if legacy else item.get("entry"),
        item.get("execution_id"),
        item.get("worker_id"),
    )


def _reconciled_worker_history(
    prior: Sequence[Mapping[str, object]],
    survivors: Sequence[Mapping[str, object]],
    *,
    legacy: bool,
) -> list[Mapping[str, object]]:
    live = {_worker_identity(item, legacy=legacy): item for item in survivors}
    retained = {
        _worker_identity(item, legacy=legacy): {
            **item,
            "state": (
                "running" if _worker_identity(item, legacy=legacy) in live else "exited"
            ),
        }
        for item in prior
    }
    for identity, survivor in live.items():
        if identity in retained:
            retained[identity] = {
                **retained[identity],
                "state": "running",
                "last_observed_at": survivor["last_observed_at"],
            }
        else:
            retained[identity] = dict(survivor)
    return sorted(
        retained.values(), key=lambda item: _worker_sort_key(item, legacy=legacy)
    )


def _wait_for_cleanup_retry(
    log: LogContext,
    run_root: Path,
    run_id: str,
    *,
    terminal_status: str = "stopped",
) -> None:
    request = run_root / "stop.request"
    observed = request.stat().st_mtime_ns if request.exists() else 0
    while True:
        time.sleep(STATUS_POLL_SECONDS)
        current = request.stat().st_mtime_ns if request.exists() else 0
        if current == observed:
            continue
        observed = current
        complete = (
            _continue_failed_cleanup(log, run_root, run_id)
            if terminal_status == "failed"
            else _retry_stopped_cleanup(log, run_root, run_id)
        )
        if complete:
            return


def _retry_stopped_cleanup(log: LogContext, run_root: Path, run_id: str) -> bool:
    _finish_stopped(
        log,
        run_root,
        run_id,
        (),
        wait_for_retry=False,
    )
    return (
        cast(Mapping[str, object], _load_run(run_root / "run.json")["state"])["status"]
        == "stopped"
    )


def _finish_complete(
    log: LogContext,
    run_root: Path,
    run_id: str,
    counts: Mapping[str, int],
    finished: str,
) -> None:
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        state = cast(dict[str, object], record["state"])
        state.update(
            {
                "status": "complete",
                "phase": None,
                "operational_failure": None,
            }
        )
        _clear_active(state)
        cast(dict[str, object], record["progress"])["artifact_outcomes"] = dict(counts)
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"finished_at": finished, "updated_at": finished})
        record["checkpoints"] = _checkpoint_dicts(
            run_root, legacy=record.get("schema") == LEGACY_RUN_SCHEMA
        )
        _write_run(run_root, record)


def _finish_failed(
    log: LogContext, run_root: Path, run_id: str, error: BaseException
) -> None:
    if not (run_root / "run.json").is_file():
        return
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        now = _utc_now()
        state = cast(dict[str, object], record["state"])
        state.update({"status": None, "phase": "stopping"})
        state["operational_failure"] = _failure(
            cast(str, getattr(error, "code", "reproduction.job.failed")),
            str(error),
            None,
            _FailureContext(
                now=now,
                include_entry="active_executions" in state,
            ),
        )
        _stamp(record)
        _write_run(run_root, record)
    if not _continue_failed_cleanup(log, run_root, run_id):
        _wait_for_cleanup_retry(log, run_root, run_id, terminal_status="failed")


def _continue_failed_cleanup(log: LogContext, run_root: Path, run_id: str) -> bool:
    survivors = _stop_workers_and_clean_scratch(run_root, run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        legacy = record.get("schema") == LEGACY_RUN_SCHEMA
        if legacy:
            for worker in survivors:
                cast(dict[str, object], worker).pop("entry", None)
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return state["status"] == "failed"
        operational = state.get("operational_failure")
        if not isinstance(operational, Mapping):
            raise ActionError(
                "reproduction.run.invalid", "failed cleanup lost terminal intent"
            )
        workers = _reconciled_worker_history(
            cast(Sequence[Mapping[str, object]], record["workers"]),
            survivors,
            legacy=legacy,
        )
        record["workers"] = workers
        if survivors:
            state.update({"status": None, "phase": "stopping"})
            _stamp(record)
            _write_run(run_root, record)
            return False
        now = _utc_now()
        record["checkpoints"] = _terminalize_active_checkpoints(
            run_root,
            now=now,
            legacy=legacy,
            terminal=_CheckpointTerminal(
                "failed",
                cast(str, operational["code"]),
                cast(str, operational["message"]),
            ),
        )
        _synchronize_checkpoint_progress(record)
        _stamp(record)
        _write_run(run_root, record)
    from .reproduction_scheduler import release_run_scheduling

    release_run_scheduling(resolve_project_root(log.root), run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return state["status"] == "failed"
        now = _utc_now()
        state.update({"status": "failed", "phase": None})
        _clear_active(state)
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"finished_at": now, "updated_at": now})
        _write_run(run_root, record)
    return True


def _reconcile_lost_supervisor(log: LogContext, run_root: Path, run_id: str) -> None:
    record = _load_run(run_root / "run.json")
    _require_run_identity(record, run_id)
    if cast(Mapping[str, object], record["state"])["status"] is not None:
        return
    pid = _supervisor_pid(run_root)
    if pid is not None and _pid_alive(pid):
        return
    state = cast(Mapping[str, object], record["state"])
    if state["phase"] == "stopping" and isinstance(
        state.get("operational_failure"), Mapping
    ):
        _continue_failed_cleanup(log, run_root, run_id)
        return
    survivors = _stop_workers_and_clean_scratch(run_root, run_id)
    if record.get("schema") == LEGACY_RUN_SCHEMA:
        for worker in survivors:
            cast(dict[str, object], worker).pop("entry", None)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return
        now = _utc_now()
        legacy = record.get("schema") == LEGACY_RUN_SCHEMA
        workers = _reconciled_worker_history(
            cast(Sequence[Mapping[str, object]], record["workers"]),
            survivors,
            legacy=legacy,
        )
        state["latest_execution_diagnostic"] = _failure(
            "supervisor_lost",
            "The durable supervisor was interrupted.",
            None,
            _FailureContext(
                now=now,
                include_entry="active_executions" in state,
            ),
        )
        if survivors:
            state["phase"] = "stopping"
            record["workers"] = workers
        else:
            record["checkpoints"] = _stop_active_checkpoints(
                run_root, now=now, legacy=legacy
            )
            _synchronize_checkpoint_progress(record)
            record["workers"] = workers
            state.update({"status": None, "phase": "stopping"})
        cast(dict[str, object], record["timestamps"])["updated_at"] = now
        _write_run(run_root, record)
    if survivors:
        return
    from .reproduction_scheduler import release_run_scheduling

    release_run_scheduling(resolve_project_root(log.root), run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return
        now = _utc_now()
        _clear_active(state)
        state.update({"status": "stopped", "phase": None})
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"stopped_at": now, "updated_at": now})
        _write_run(run_root, record)


def _stop_workers_and_clean_scratch(
    run_root: Path, run_id: str
) -> list[Mapping[str, object]]:
    survivors = _terminate_marked_workers(run_id)
    if not survivors:
        cleanup_reproduction_scratch(run_root)
    return survivors


def _terminate_marked_workers(run_id: str) -> list[Mapping[str, object]]:
    found: dict[int, tuple[psutil.Process, str | None, str | None]] = {}
    try:
        for process in psutil.process_iter(["pid"]):
            try:
                marker = process.environ().get("RESEARCH_LOG_REPRODUCTION_RUN_ID")
                if marker == run_id or (
                    isinstance(marker, str) and marker.startswith(f"{run_id}:")
                ):
                    entry, execution = _marker_identity(run_id, marker)
                    found[process.pid] = (process, entry, execution)
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
            "worker_id": f"worker-{process.pid}",
            "parent_worker_id": None,
            "pid": process.pid,
            "execution_id": found[process.pid][2],
            "state": "running",
            "registered_at": now,
            "last_observed_at": now,
        }
        for process in sorted(live, key=lambda item: item.pid)
    ]


def _marker_identity(run_id: str, marker: object) -> tuple[str | None, str | None]:
    if not isinstance(marker, str) or not marker.startswith(f"{run_id}:"):
        return None, None
    parts = marker.removeprefix(f"{run_id}:").split(":")
    if len(parts) == 2 and re.fullmatch(r"e[0-9]{3}", parts[0]) is not None:
        digest = parts[1]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is not None:
            return parts[0], f"pyrun-exec/v1:{digest}"
    if len(parts) == 1 and re.fullmatch(r"[0-9a-f]{64}", parts[0]) is not None:
        return None, f"pyrun-exec/v1:{parts[0]}"
    return None, None


def _stop_active_checkpoints(
    run_root: Path,
    *,
    now: str,
    legacy: bool,
) -> list[Mapping[str, object]]:
    return _terminalize_active_checkpoints(
        run_root,
        now=now,
        legacy=legacy,
        terminal=_CheckpointTerminal(
            "stopped",
            "supervisor_lost",
            "The durable supervisor was interrupted.",
        ),
    )


def _synchronize_checkpoint_progress(record: dict[str, object]) -> None:
    checkpoints = cast(Sequence[Mapping[str, object]], record["checkpoints"])
    completed = sum(item["state"] != "active" for item in checkpoints)
    progress = cast(dict[str, object], record["progress"])
    progress["completed_executions"] = completed


def _terminalize_active_checkpoints(
    run_root: Path,
    *,
    now: str,
    legacy: bool,
    terminal: _CheckpointTerminal,
) -> list[Mapping[str, object]]:
    checkpoints = _checkpoint_dicts(run_root, legacy=legacy)
    for checkpoint in checkpoints:
        if checkpoint["state"] != "active":
            continue
        updated = dict(checkpoint)
        updated["state"] = "partial" if legacy else terminal.state
        if not legacy:
            updated["failure"] = {
                "code": terminal.code,
                "message": terminal.message,
                "recorded_at": now,
            }
        path = run_root / cast(str, updated["path"])
        atomic_write_text(path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
    stopped = _checkpoint_dicts(run_root, legacy=legacy)
    return stopped


def _accepted_record(
    log: LogContext,
    plan: ReproductionPlan,
    run_id: str,
    run_root: Path,
    *,
    accepted_at: str | None = None,
) -> dict[str, object]:
    now = accepted_at or _utc_now()
    plan_value = plan.as_dict()
    plan_value.pop("schema")
    commands = plan.source_snapshot.get("commands")
    if not isinstance(commands, Sequence):
        raise ActionError("reproduction.run.invalid", "command queue is unavailable")
    return {
        "attempt": 1,
        "attempts": [],
        "checkpoints": [],
        "execution_timeout_seconds": plan.execution_timeout_seconds,
        "include_all": plan.include_all,
        "jobs": plan.jobs,
        "paths": {
            "diagnostics": "diagnostics",
            "run": canonical_run_path(now, run_root.name).as_posix(),
            "staging": "executions",
            "workspace": "workspace",
        },
        "plan": plan_value,
        "progress": {
            "artifact_outcomes": {name: 0 for name in OUTCOMES},
            "completed_executions": 0,
            "total_executions": len(plan.executions),
        },
        "run_id": run_id,
        "queue": sorted(
            [dict(cast(Mapping[str, object], item)) for item in commands],
            key=lambda item: (str(item.get("entry")), str(item.get("execution_id"))),
        ),
        "schema": RUN_SCHEMA,
        "source_snapshot": dict(plan.source_snapshot),
        "state": {
            "active_executions": [],
            "latest_execution_diagnostic": None,
            "operational_failure": None,
            "phase": "accepted",
            "status": None,
        },
        "summary": plan.summary,
        "target": dict(plan.target),
        "timestamps": {
            "accepted_at": now,
            "finished_at": None,
            "resumed_at": None,
            "started_at": None,
            "stopped_at": None,
            "updated_at": now,
        },
        "validation_snapshot": dict(plan.validation_snapshot),
        "workers": [],
    }


def _plan_from_record(record: Mapping[str, object]) -> ReproductionPlan:
    legacy = record.get("schema") == LEGACY_RUN_SCHEMA
    schema = (
        LEGACY_PLAN_SCHEMA
        if legacy
        else PRECONTINUATION_PLAN_SCHEMA
        if record.get("schema") == PRECONTINUATION_RUN_SCHEMA
        else PRETIMEOUT_PLAN_SCHEMA
        if record.get("schema") == PRETIMEOUT_RUN_SCHEMA
        else PREEXECUTION_PLAN_SCHEMA
        if record.get("schema") == PREEXECUTION_RUN_SCHEMA
        else PLAN_SCHEMA
    )
    value = {"schema": schema, **cast(Mapping[str, object], record["plan"])}
    if (
        value.get("source_snapshot") != record["source_snapshot"]
        or value.get("validation_snapshot") != record["validation_snapshot"]
    ):
        raise ActionError("reproduction.run.invalid", "plan snapshots disagree")
    fields = {
        "boundaries",
        "cases",
        "executions",
        "failures",
        "include_all",
        *(() if legacy else ("jobs",)),
        *(
            ("execution_timeout_seconds",)
            if schema in {PREEXECUTION_PLAN_SCHEMA, PLAN_SCHEMA}
            else ()
        ),
        "schema",
        "source_snapshot",
        "summary",
        "target",
        "validation_snapshot",
    }
    if set(value) != fields or value["schema"] != schema:
        raise ActionError("reproduction.run.invalid", "stored plan fields are invalid")
    plan = ReproductionPlan(
        cast(str, value["summary"]),
        cast(Mapping[str, object], value["target"]),
        cast(bool, value["include_all"]),
        cast(Mapping[str, object], value["validation_snapshot"]),
        cast(Mapping[str, object], value["source_snapshot"]),
        tuple(cast(Sequence[Mapping[str, object]], value["cases"])),
        tuple(
            {**item, "exclusive": True} if legacy else item
            for item in cast(Sequence[Mapping[str, object]], value["executions"])
        ),
        tuple(cast(Sequence[Mapping[str, object]], value["boundaries"])),
        tuple(cast(Sequence[Mapping[str, object]], value["failures"])),
        cast(int, value.get("jobs", 1)),
        cast(
            int,
            value.get("execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT_SECONDS),
        ),
    )
    if {**plan.as_dict(), "schema": schema} != value and record.get("schema") in {
        PREEXECUTION_RUN_SCHEMA,
        RUN_SCHEMA,
    }:
        raise ActionError("reproduction.run.invalid", "stored plan is not canonical")
    if _parallel_run(record) and record.get("jobs") != plan.jobs:
        raise ActionError("reproduction.run.invalid", "accepted jobs value changed")
    if record.get("schema") in {PREEXECUTION_RUN_SCHEMA, RUN_SCHEMA} and (
        record.get("execution_timeout_seconds") != plan.execution_timeout_seconds
    ):
        raise ActionError(
            "reproduction.run.invalid", "accepted execution timeout changed"
        )
    is_repair_verification(plan)
    return plan


def _status_projection(record: Mapping[str, object]) -> Mapping[str, object]:
    state = cast(Mapping[str, object], record["state"])
    progress = cast(Mapping[str, object], record["progress"])
    workers = cast(Sequence[Mapping[str, object]], record["workers"])
    checkpoints = cast(Sequence[Mapping[str, object]], record["checkpoints"])
    if record.get("schema") == LEGACY_RUN_SCHEMA:
        return _bounded_status(
            {
                "artifact_outcomes": progress["artifact_outcomes"],
                "completed_executions": progress["completed_executions"],
                "current_execution": state["current_execution"],
                "execution_timings": _execution_timings(checkpoints, legacy=True),
                "include_all": record["include_all"],
                "latest_execution_diagnostic": state["latest_execution_diagnostic"],
                "operational_failure": state["operational_failure"],
                "phase": state["phase"],
                "run_id": record["run_id"],
                "schema": LEGACY_STATUS_SCHEMA,
                "status": state["status"],
                "summary": record["summary"],
                "surviving_workers": _running_workers(workers),
                "target": record["target"],
                "timestamps": record["timestamps"],
                "total_executions": progress["total_executions"],
            }
        )
    active = cast(Sequence[Mapping[str, object]], state["active_executions"])
    active_references = {(item["entry"], item["execution_id"]) for item in active}
    projection = {
        "artifact_outcomes": progress["artifact_outcomes"],
        "active_executions": list(active),
        "active_workers": [
            dict(item)
            for item in workers
            if item.get("state") == "running"
            and (item.get("entry"), item.get("execution_id")) in active_references
        ],
        "completed_executions": progress["completed_executions"],
        **(
            {"execution_timeout_seconds": record["execution_timeout_seconds"]}
            if record.get("schema") in {PREEXECUTION_RUN_SCHEMA, RUN_SCHEMA}
            else {}
        ),
        "execution_timings": _execution_timings(checkpoints),
        "include_all": record["include_all"],
        "jobs": record["jobs"],
        "latest_execution_diagnostic": state["latest_execution_diagnostic"],
        "operational_failure": state["operational_failure"],
        "phase": state["phase"],
        "run_id": record["run_id"],
        "schema": (
            STATUS_SCHEMA
            if record.get("schema") == RUN_SCHEMA
            else PREEXECUTION_STATUS_SCHEMA
            if record.get("schema") == PREEXECUTION_RUN_SCHEMA
            else PRETIMEOUT_STATUS_SCHEMA
            if record.get("schema") == PRETIMEOUT_RUN_SCHEMA
            else PRECONTINUATION_STATUS_SCHEMA
        ),
        "status": state["status"],
        "summary": record["summary"],
        "surviving_workers": _running_workers(workers),
        "target": record["target"],
        "timestamps": record["timestamps"],
        "total_executions": progress["total_executions"],
    }
    if _continuation_run(record):
        resolved = _logical_queue_resolved(record)
        projection.update(
            {
                "attempt": record["attempt"],
                "attempts": record["attempt"],
                "resolved": resolved,
                "resumable": state["status"] in {"failed", "stopped"}
                or state["status"] == "complete"
                and not resolved,
            }
        )
    return _bounded_status(projection)


def _logical_queue_resolved(record: Mapping[str, object]) -> bool:
    queue = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in cast(Sequence[Mapping[str, object]], record["queue"])
        if item.get("queued") is True
    }
    current = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in cast(
            Sequence[Mapping[str, object]],
            cast(Mapping[str, object], record["source_snapshot"])["commands"],
        )
    }
    checkpoints = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in cast(Sequence[Mapping[str, object]], record["checkpoints"])
    }
    for key, accepted in queue.items():
        item = current.get(key)
        if item is None:
            return False
        selection = item.get("selection")
        if selection == "not_needed":
            continue
        if selection == "run" and successful_checkpoint_state(
            checkpoints.get(key, {}).get("state")
        ):
            continue
        if accepted.get("requires_reproduction") is False and selection == "policy":
            continue
        return False
    return True


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


def _execution_timings(
    checkpoints: Sequence[Mapping[str, object]], *, legacy: bool = False
) -> list[Mapping[str, object]]:
    return [
        (
            {
                "elapsed_seconds": item["elapsed_seconds"],
                "entry": item["entry"],
                "execution_id": item["execution_id"],
                "failure": item.get("failure"),
                "finished_at": item["finished_at"],
                "started_at": item["started_at"],
                "state": item["state"],
            }
            if not legacy
            else {
                "elapsed_seconds": item["elapsed_seconds"],
                "entry": item["entry"],
                "execution_id": item["execution_id"],
                "finished_at": item["finished_at"],
                "started_at": item["started_at"],
                "state": item["state"],
            }
        )
        for item in checkpoints
        if item["started_at"] is not None
    ]


def _running_workers(
    workers: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [dict(item) for item in workers if item.get("state") == "running"]


def _load_run(path: Path) -> dict[str, object]:
    raw = _read_run_snapshot(path)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ActionError("reproduction.run.invalid", str(error)) from error
    common_fields = {
        "checkpoints",
        "include_all",
        "paths",
        "plan",
        "progress",
        "run_id",
        "schema",
        "source_snapshot",
        "state",
        "summary",
        "target",
        "timestamps",
        "validation_snapshot",
        "workers",
    }
    schema = value.get("schema") if isinstance(value, dict) else None
    parallel = schema in {
        PRECONTINUATION_RUN_SCHEMA,
        PRETIMEOUT_RUN_SCHEMA,
        PREEXECUTION_RUN_SCHEMA,
        RUN_SCHEMA,
    }
    fields = (
        common_fields
        | ({"jobs"} if parallel else set())
        | (
            {"attempt", "attempts", "queue"}
            if schema in {PRETIMEOUT_RUN_SCHEMA, PREEXECUTION_RUN_SCHEMA, RUN_SCHEMA}
            else set()
        )
        | (
            {"execution_timeout_seconds"}
            if schema in {PREEXECUTION_RUN_SCHEMA, RUN_SCHEMA}
            else set()
        )
    )
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or schema
        not in {
            LEGACY_RUN_SCHEMA,
            PRECONTINUATION_RUN_SCHEMA,
            PRETIMEOUT_RUN_SCHEMA,
            PREEXECUTION_RUN_SCHEMA,
            RUN_SCHEMA,
        }
    ):
        raise ActionError("reproduction.run.invalid", "run record fields are invalid")
    if RUN_ID_RE.fullmatch(str(value.get("run_id"))) is None:
        raise ActionError("reproduction.run.invalid", "run ID is invalid")
    state = value.get("state")
    current_state_fields = {
        "active_executions" if parallel else "current_execution",
        "latest_execution_diagnostic",
        "operational_failure",
        "phase",
        "status",
    }
    legacy_state_fields = {
        "current_execution",
        "latest_failure",
        "phase",
        "status",
    }
    allowed_state_fields = (
        {frozenset(current_state_fields)}
        if parallel
        else {frozenset(current_state_fields), frozenset(legacy_state_fields)}
    )
    if not isinstance(state, dict) or frozenset(state) not in allowed_state_fields:
        raise ActionError("reproduction.run.invalid", "run state is invalid")
    status = state["status"]
    phase = state["phase"]
    if status not in {*TERMINAL_STATUSES, None} or phase not in {*ACTIVE_PHASES, None}:
        raise ActionError("reproduction.run.invalid", "run lifecycle is invalid")
    if (status is None) == (phase is None):
        raise ActionError("reproduction.run.invalid", "run lifecycle is incoherent")
    _validate_run_members(value)
    _plan_from_record(value)
    canonical = _canonical(value).encode("utf-8")
    if raw != canonical:
        raise ActionError("reproduction.run.invalid", "run record is not canonical")
    if "latest_failure" in state:
        failure = state.pop("latest_failure")
        state["latest_execution_diagnostic"] = None if status == "failed" else failure
        state["operational_failure"] = failure if status == "failed" else None
    return cast(dict[str, object], value)


def _read_run_snapshot(path: Path) -> bytes:
    """Read one regular run record through a bounded immutable descriptor."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("run record is not a regular file")
            if metadata.st_size > MAX_RUN_RECORD_BYTES:
                raise OSError("run record crossed its byte bound")
            raw = handle.read(MAX_RUN_RECORD_BYTES + 1)
        if len(raw) > MAX_RUN_RECORD_BYTES:
            raise OSError("run record crossed its byte bound")
        return raw
    except OSError as error:
        raise ActionError("reproduction.run.invalid", str(error)) from error


def _validate_run_members(value: Mapping[str, object]) -> None:
    _validate_run_target(value)
    _validate_progress(value.get("progress"))
    _validate_timestamps(value.get("timestamps"))
    _validate_paths(value)
    state = cast(Mapping[str, object], value["state"])
    _validate_active_state(value, state)
    legacy = value.get("schema") == LEGACY_RUN_SCHEMA
    if "latest_failure" in state:
        _validate_failure(state.get("latest_failure"), legacy=True)
    else:
        _validate_failure(state.get("latest_execution_diagnostic"), legacy=legacy)
        _validate_failure(state.get("operational_failure"), legacy=legacy)
    _validate_workers(value.get("workers"), legacy=legacy)
    _validate_checkpoints(value.get("checkpoints"), legacy=legacy)
    if _continuation_run(value):
        _validate_continuation_state(value)
    _validate_execution_target_scope(value)


def _validate_execution_target_scope(value: Mapping[str, object]) -> None:
    """Reject widened persisted command plans before execution or resume."""

    target = cast(Mapping[str, object], value["target"])
    if target.get("kind") != "execution":
        return
    if value.get("schema") != RUN_SCHEMA:
        raise ActionError(
            "reproduction.run.invalid", "legacy run cannot target an execution"
        )
    key = (target["entry"], target["execution_id"])
    queue = cast(Sequence[Mapping[str, object]], value["queue"])
    plan = value.get("plan")
    if not isinstance(plan, Mapping) or plan.get("target") != target:
        raise ActionError("reproduction.run.invalid", "execution plan target changed")
    if [(item["entry"], item["execution_id"]) for item in queue] != [key]:
        raise ActionError("reproduction.run.invalid", "execution target queue changed")
    source = cast(Mapping[str, object], value["source_snapshot"])
    collections = [
        source.get("commands"),
        source.get("executions"),
        plan.get("executions"),
        plan.get("cases"),
    ]
    for collection in collections:
        if not isinstance(collection, list) or any(
            not isinstance(item, Mapping)
            or (item.get("entry"), item.get("execution_id")) != key
            for item in collection
        ):
            raise ActionError(
                "reproduction.run.invalid", "execution target scope changed"
            )


def _validate_continuation_state(value: Mapping[str, object]) -> None:
    attempt = value.get("attempt")
    attempts = value.get("attempts")
    queue = value.get("queue")
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt <= 0
        or not isinstance(attempts, list)
        or len(attempts) != attempt - 1
        or not isinstance(queue, list)
        or len(queue) > 10_000
    ):
        raise ActionError("reproduction.run.invalid", "attempt lineage is invalid")
    queue_keys: list[tuple[str, str]] = []
    for item in queue:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("entry"), str)
            or not isinstance(item.get("execution_id"), str)
            or not isinstance(item.get("queued"), bool)
        ):
            raise ActionError("reproduction.run.invalid", "logical queue is invalid")
        queue_keys.append((cast(str, item["entry"]), cast(str, item["execution_id"])))
    if queue_keys != sorted(queue_keys) or len(queue_keys) != len(set(queue_keys)):
        raise ActionError("reproduction.run.invalid", "logical queue order is invalid")
    expected_fields = {
        "attempt",
        "checkpoints",
        "plan",
        "progress",
        "source_snapshot",
        "state",
        "timestamps",
        "validation_snapshot",
        "workers",
    }
    for index, archived in enumerate(attempts, 1):
        if (
            not isinstance(archived, Mapping)
            or set(archived) != expected_fields
            or archived.get("attempt") != index
        ):
            raise ActionError("reproduction.run.invalid", "attempt history is invalid")


def _validate_run_target(value: Mapping[str, object]) -> None:
    target = value.get("target")
    if not valid_reproduction_target(target):
        raise ActionError("reproduction.run.invalid", "run target is invalid")
    if not isinstance(value.get("include_all"), bool):
        raise ActionError("reproduction.run.invalid", "selection policy is invalid")
    if _parallel_run(value) and (
        not isinstance(value.get("jobs"), int)
        or isinstance(value.get("jobs"), bool)
        or cast(int, value["jobs"]) <= 0
    ):
        raise ActionError("reproduction.run.invalid", "jobs value is invalid")
    timeout = value.get("execution_timeout_seconds")
    if value.get("schema") in {PREEXECUTION_RUN_SCHEMA, RUN_SCHEMA} and (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= MAX_EXECUTION_TIMEOUT_SECONDS
    ):
        raise ActionError("reproduction.run.invalid", "execution timeout is invalid")


def _validate_active_state(
    value: Mapping[str, object], state: Mapping[str, object]
) -> None:
    if _parallel_run(value):
        active = state.get("active_executions")
        jobs = cast(int, value["jobs"])
        if (
            not isinstance(active, list)
            or len(active) > jobs
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"entry", "execution_id"}
                or not isinstance(item.get("entry"), str)
                or not isinstance(item.get("execution_id"), str)
                or EXECUTION_ID_RE.fullmatch(cast(str, item.get("execution_id")))
                is None
                for item in active
            )
        ):
            raise ActionError(
                "reproduction.run.invalid", "active executions are invalid"
            )
        typed = cast(Sequence[Mapping[str, object]], active)
        keys = [
            (cast(str, item["entry"]), cast(str, item["execution_id"]))
            for item in typed
        ]
        if (
            len(keys) != len(set(keys))
            or list(typed) != sorted(typed, key=lambda item: _plan_order(value, item))
            or any(_plan_order(value, item) == 2**31 for item in typed)
            or len(typed) > 1
            and any(_plan_item(value, item).get("exclusive") is True for item in typed)
        ):
            raise ActionError(
                "reproduction.run.invalid", "active execution order is invalid"
            )
    else:
        current = state.get("current_execution")
        if current is not None and (
            not isinstance(current, str) or EXECUTION_ID_RE.fullmatch(current) is None
        ):
            raise ActionError(
                "reproduction.run.invalid", "current execution is invalid"
            )


def _validate_progress(value: object) -> None:
    progress = value
    if not isinstance(progress, Mapping) or set(progress) != {
        "artifact_outcomes",
        "completed_executions",
        "total_executions",
    }:
        raise ActionError("reproduction.run.invalid", "run progress is invalid")
    counts = progress.get("artifact_outcomes")
    numbers = (progress.get("completed_executions"), progress.get("total_executions"))
    if (
        not isinstance(counts, Mapping)
        or set(counts) != set(OUTCOMES)
        or any(not _nonnegative_int(item) for item in counts.values())
        or any(not _nonnegative_int(item) for item in numbers)
        or cast(int, numbers[0]) > cast(int, numbers[1])
    ):
        raise ActionError("reproduction.run.invalid", "run progress is invalid")


def _validate_timestamps(value: object) -> None:
    timestamp_fields = {
        "accepted_at",
        "finished_at",
        "resumed_at",
        "started_at",
        "stopped_at",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != timestamp_fields:
        raise ActionError("reproduction.run.invalid", "run timestamps are invalid")
    if any(
        item is not None
        and not isinstance(item, str)
        or isinstance(item, str)
        and TIMESTAMP_RE.fullmatch(item) is None
        for item in value.values()
    ):
        raise ActionError("reproduction.run.invalid", "run timestamp is invalid")


def _validate_paths(record: Mapping[str, object]) -> None:
    value = record.get("paths")
    if not isinstance(value, Mapping) or set(value) != {
        "diagnostics",
        "run",
        "staging",
        "workspace",
    }:
        raise ActionError("reproduction.run.invalid", "run paths are invalid")
    if (
        value.get("diagnostics") != "diagnostics"
        or value.get("staging") != "executions"
        or value.get("workspace") != "workspace"
    ):
        raise ActionError("reproduction.run.invalid", "run paths are invalid")
    timestamps = cast(Mapping[str, object], record["timestamps"])
    target = cast(Mapping[str, object], record["target"])
    summary = record.get("summary")
    run_id = record.get("run_id")
    accepted_at = timestamps.get("accepted_at")
    entry = target.get("entry")
    if not all(isinstance(item, str) for item in (summary, run_id, accepted_at)):
        raise ActionError("reproduction.run.invalid", "run paths are invalid")
    expected_leaf = run_leaf(
        Path(cast(str, summary)).stem,
        cast(str | None, entry),
        cast(str, run_id),
    )
    expected = canonical_run_path(cast(str, accepted_at), expected_leaf).as_posix()
    if value.get("run") != expected:
        raise ActionError("reproduction.run.invalid", "run path is not canonical")


def _validate_failure(value: object, *, legacy: bool) -> None:
    if value is None:
        return
    fields = {"code", "execution_id", "message", "recorded_at"}
    expected = fields if legacy else fields | {"entry"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ActionError("reproduction.run.invalid", "latest failure is invalid")
    if not all(
        isinstance(value.get(name), str) and value.get(name)
        for name in ("code", "message")
    ):
        raise ActionError("reproduction.run.invalid", "latest failure is invalid")
    execution = value.get("execution_id")
    entry = value.get("entry")
    if execution is not None and (
        not isinstance(execution, str) or EXECUTION_ID_RE.fullmatch(execution) is None
    ):
        raise ActionError("reproduction.run.invalid", "failure execution is invalid")
    if not legacy and (
        entry is not None
        and (not isinstance(entry, str) or re.fullmatch(r"e[0-9]{3}", entry) is None)
        or (entry is None) != (execution is None)
    ):
        raise ActionError("reproduction.run.invalid", "failure entry is invalid")
    recorded = value.get("recorded_at")
    if not isinstance(recorded, str) or TIMESTAMP_RE.fullmatch(recorded) is None:
        raise ActionError("reproduction.run.invalid", "failure timestamp is invalid")


def _validate_workers(value: object, *, legacy: bool) -> None:
    if not isinstance(value, list) or len(value) > 4_096:
        raise ActionError("reproduction.run.invalid", "worker list is invalid")
    fields = {
        "execution_id",
        "last_observed_at",
        "parent_worker_id",
        "pid",
        "registered_at",
        "state",
        "worker_id",
    }
    expected = fields if legacy else fields | {"entry"}
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ActionError("reproduction.run.invalid", "worker record is invalid")
        item = cast(Mapping[str, object], raw)
        entry = item.get("entry")
        execution = item.get("execution_id")
        if (
            set(item) != expected
            or not isinstance(item.get("worker_id"), str)
            or re.fullmatch(r"worker-[0-9]+", cast(str, item["worker_id"])) is None
            or item.get("parent_worker_id") is not None
            and (
                not isinstance(item.get("parent_worker_id"), str)
                or re.fullmatch(r"worker-[0-9]+", cast(str, item["parent_worker_id"]))
                is None
            )
            or not isinstance(item.get("pid"), int)
            or isinstance(item.get("pid"), bool)
            or cast(int, item["pid"]) <= 1
            or item.get("state") not in {"running", "exited"}
            or not _valid_timestamp(item.get("registered_at"))
            or not _valid_timestamp(item.get("last_observed_at"))
            or cast(str, item["last_observed_at"]) < cast(str, item["registered_at"])
            or execution is not None
            and (
                not isinstance(execution, str)
                or EXECUTION_ID_RE.fullmatch(execution) is None
            )
            or not legacy
            and entry is not None
            and (
                not isinstance(entry, str) or re.fullmatch(r"e[0-9]{3}", entry) is None
            )
            or not legacy
            and ((entry is None) != (execution is None))
        ):
            raise ActionError("reproduction.run.invalid", "worker record is invalid")
    typed = cast(Sequence[Mapping[str, object]], value)
    keys = [
        (
            (cast(str, item["worker_id"]),)
            if legacy
            else (
                str(item.get("entry") or ""),
                str(item.get("execution_id") or ""),
                cast(str, item["worker_id"]),
            )
        )
        for item in typed
    ]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ActionError("reproduction.run.invalid", "worker order is invalid")


def _validate_checkpoints(value: object, *, legacy: bool) -> None:
    if not isinstance(value, list) or len(value) > 2_048:
        raise ActionError("reproduction.run.invalid", "checkpoint list is invalid")
    fields = {
        "completed_at",
        "elapsed_seconds",
        "entry",
        "execution_id",
        "finished_at",
        "outputs",
        "path",
        "started_at",
        "state",
    }
    expected = fields if legacy else fields | {"failure"}
    if any(not isinstance(item, Mapping) or set(item) != expected for item in value):
        raise ActionError("reproduction.run.invalid", "checkpoint record is invalid")
    timestamp_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for item in cast(Sequence[Mapping[str, object]], value):
        state = item["state"]
        started_at = item["started_at"]
        finished_at = item["finished_at"]
        completed_at = item["completed_at"]
        elapsed_seconds = item["elapsed_seconds"]
        success = "complete" if legacy else "succeeded"
        states = (
            {"active", "complete", "partial"}
            if legacy
            else {
                "active",
                "succeeded",
                "failed",
                "stopped",
            }
        )
        if (
            not isinstance(item["entry"], str)
            or re.fullmatch(r"e[0-9]{3}", item["entry"]) is None
            or not isinstance(item["execution_id"], str)
            or EXECUTION_ID_RE.fullmatch(item["execution_id"]) is None
            or item["path"]
            != "checkpoints/"
            + item["entry"]
            + "-"
            + item["execution_id"].removeprefix("pyrun-exec/v1:")
            + ".json"
            or state not in states
            or not isinstance(item["outputs"], list)
            or len(cast(list[object], item["outputs"])) > 256
            or not _valid_checkpoint_outputs(item["outputs"])
            or any(
                timestamp is not None
                and (
                    not isinstance(timestamp, str)
                    or timestamp_re.fullmatch(timestamp) is None
                )
                for timestamp in (started_at, finished_at, completed_at)
            )
            or elapsed_seconds is not None
            and (
                not isinstance(elapsed_seconds, (int, float))
                or isinstance(elapsed_seconds, bool)
                or elapsed_seconds < 0
            )
        ):
            raise ActionError(
                "reproduction.run.invalid", "checkpoint record is invalid"
            )
        if (
            started_at is None
            and (finished_at is not None or elapsed_seconds is not None)
            or started_at is not None
            and elapsed_seconds is None
            or state == "active"
            and finished_at is not None
            or isinstance(started_at, str)
            and isinstance(finished_at, str)
            and finished_at < started_at
            or state == success
            and (finished_at is None or completed_at != finished_at)
            or state != success
            and completed_at is not None
        ):
            raise ActionError(
                "reproduction.run.invalid", "checkpoint timing is invalid"
            )
        failure = item.get("failure")
        if not legacy and (
            state in {"active", "succeeded"}
            and failure is not None
            or state in {"failed", "stopped"}
            and not _valid_checkpoint_failure(failure)
        ):
            raise ActionError(
                "reproduction.run.invalid", "checkpoint failure is invalid"
            )
    keys = [
        (cast(str, item["entry"]), cast(str, item["execution_id"]))
        for item in cast(Sequence[Mapping[str, object]], value)
    ]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ActionError("reproduction.run.invalid", "checkpoint order is invalid")


def _valid_checkpoint_outputs(value: object) -> bool:
    assert isinstance(value, list)
    artifacts: list[str] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"artifact", "fingerprint"}
            or not isinstance(item.get("artifact"), str)
        ):
            return False
        artifact = cast(str, item["artifact"])
        try:
            parse_fingerprint(item.get("fingerprint"), artifact)
        except (DataContractError, ValueError):
            return False
        artifacts.append(artifact)
    return artifacts == sorted(set(artifacts))


def _valid_checkpoint_failure(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"code", "message", "recorded_at"}
        and all(
            isinstance(value.get(name), str) and value.get(name)
            for name in ("code", "message")
        )
        and _valid_timestamp(value.get("recorded_at"))
    )


def _valid_timestamp(value: object) -> bool:
    return isinstance(value, str) and TIMESTAMP_RE.fullmatch(value) is not None


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _write_run(root: Path, record: Mapping[str, object]) -> None:
    encoded = _canonical(record)
    if len(encoded.encode("utf-8")) > MAX_RUN_RECORD_BYTES:
        raise ActionError(
            "reproduction.run.resource_limit", "run record crossed its byte bound"
        )
    atomic_write_text(root / "run.json", encoded)


def _find_run(log: LogContext, run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    matches: list[Path] = []
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
    for candidate in candidates:
        if not candidate.name.endswith(f"-{run_id}"):
            continue
        path = candidate / "run.json"
        if path.is_file() and not path.is_symlink():
            record = _load_run(path)
            if record["run_id"] == run_id and record["summary"] == _summary_identity(
                log
            ):
                matches.append(candidate.resolve())
    if not matches:
        raise ActionError("reproduction.run.missing", f"run not found: {run_id}")
    if len(matches) > 1:
        raise ActionError(
            "reproduction.run.integrity",
            f"expected one run, found {len(matches)}: {run_id}",
        )
    return matches[0]


def _new_run_root(
    project: Path,
    log: LogContext,
    entry: str | None,
    run_id: str,
    accepted_at: str,
) -> Path:
    leaf = run_leaf(log.root.name, entry, run_id)
    return project / canonical_run_path(accepted_at, leaf)


def _require_no_active_legacy_run(project: Path) -> None:
    """Refuse v3 acceptance while an unmanaged v2 run may still be live."""

    for root in iter_canonical_run_roots(project, max_entries=MAX_RUN_DIRECTORIES):
        path = root / "run.json"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping) or value.get("schema") != LEGACY_RUN_SCHEMA:
            continue
        state = value.get("state")
        if not isinstance(state, Mapping) or state.get("status") is not None:
            continue
        pid = _supervisor_pid(root)
        workers = value.get("workers")
        active_workers = isinstance(workers, list) and any(
            isinstance(item, Mapping) and item.get("state") == "running"
            for item in workers
        )
        if pid is not None and _pid_alive(pid) or active_workers:
            raise ActionError(
                "reproduction.scheduler.legacy_active",
                f"active v2 reproduction is not enrolled: {value.get('run_id')}",
            )


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"reproduce-{stamp}-{secrets.token_hex(6)}"


def _summary_identity(log: LogContext) -> str:
    return log.summary.resolve().relative_to(resolve_project_root(log.root)).as_posix()


def _checkpoint_dicts(run_root: Path, *, legacy: bool) -> list[Mapping[str, object]]:
    root = run_root / "checkpoints"
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint root is invalid"
        )
    pairs = [(path, _checkpoint_value(path)) for path in _checkpoint_paths(root)]
    values = [value for _path, value in pairs]
    _validate_checkpoints(values, legacy=legacy)
    for path, value in pairs:
        if path.relative_to(run_root).as_posix() != value["path"]:
            raise ActionError(
                "reproduction.checkpoint.invalid",
                "checkpoint file location does not match its payload",
            )
    _validate_checkpoint_membership(run_root, values)
    return values


def _checkpoint_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_CHECKPOINT_DIRECTORY_ENTRIES:
                raise ActionError(
                    "reproduction.checkpoint.resource_limit",
                    "checkpoint directory crossed its entry bound",
                )
            if is_checkpoint_temporary_name(entry.name):
                continue
            paths.append(Path(entry.path))
            if len(paths) > MAX_CHECKPOINTS:
                raise ActionError(
                    "reproduction.checkpoint.resource_limit",
                    "checkpoint directory crossed its checkpoint bound",
                )
    return sorted(paths)


def _checkpoint_value(path: Path) -> Mapping[str, object]:
    try:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise OSError(f"invalid checkpoint path: {path}")
        raw = path.read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise OSError("checkpoint crossed its byte bound")
        text = raw.decode("utf-8")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("checkpoint is not an object")
        if (
            text
            != json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ):
            raise ValueError("checkpoint is not canonical")
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.checkpoint.invalid", str(error)) from error
    return value


def _validate_checkpoint_membership(
    run_root: Path, checkpoints: Sequence[Mapping[str, object]]
) -> None:
    record = _load_run(run_root / "run.json")
    plan = cast(Mapping[str, object], record["plan"])
    accepted = {
        (item["entry"], item["execution_id"])
        for item in cast(Sequence[Mapping[str, object]], plan["executions"])
    }
    observed = {(item["entry"], item["execution_id"]) for item in checkpoints}
    if not observed <= accepted:
        raise ActionError(
            "reproduction.checkpoint.invalid",
            "checkpoint is absent from the accepted plan",
        )


def _verify_checkpoint_inventory(run_root: Path, record: Mapping[str, object]) -> None:
    observed = _checkpoint_dicts(
        run_root, legacy=record.get("schema") == LEGACY_RUN_SCHEMA
    )
    if observed != record["checkpoints"]:
        raise ActionError(
            "reproduction.checkpoint.changed",
            "checkpoint inventory changed after its durable run projection",
        )


def _supervisor_pid(run_root: Path) -> int | None:
    try:
        value = json.loads((run_root / "supervisor.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    pid = value.get("pid") if isinstance(value, dict) else None
    return (
        pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 else None
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _failure(
    code: str | None,
    message: str | None,
    execution_id: str | None,
    context: _FailureContext = _FailureContext(),
) -> Mapping[str, object]:
    value: dict[str, object] = {
        "code": code or "reproduction.job.failed",
        "execution_id": execution_id,
        "message": message or "Reproduction failed.",
        "recorded_at": context.now or _utc_now(),
    }
    if context.include_entry:
        value["entry"] = context.entry
    return value


def _stamp(record: dict[str, object]) -> None:
    cast(dict[str, object], record["timestamps"])["updated_at"] = _utc_now()


def _touch(path: Path) -> None:
    path.touch(exist_ok=True)
    os.utime(path, None)


def _survivor_summary(value: object) -> str:
    workers = value if isinstance(value, list) else []
    return f"worker cleanup remains incomplete ({len(workers)} survivors)"


def _close_fds(values: Sequence[int]) -> None:
    for descriptor in values:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


if __name__ == "__main__":
    raise SystemExit(supervisor_main(sys.argv[1:]))
