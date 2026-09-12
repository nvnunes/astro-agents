"""Durable launch, status, stop, resume, and supervision for reproduction."""

from __future__ import annotations

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
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence, cast

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
)

from .context import LogContext, resolve_entry, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_comparison import (
    ExecutionComparison,
    clear_execution_reproduction_requirement_locked,
    compare_execution_outputs,
    load_recorded_comparisons,
    verify_evidence_only_context,
)
from .reproduction_contract import (
    ReproductionPlan,
    ReproductionRuntime,
    accepted_invocation,
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
    ReproductionSelection,
    plan_reproduction,
    prepare_reproduction_context,
)
from .reproduction_publication import (
    CompletedPublication,
    publish_completed_reproduction,
    verify_publication_retry_compatibility,
)
from .reproduction_results import OUTCOMES
from .storage import atomic_write_text

RUN_SCHEMA = "research-log-reproduction-run/7"
STATUS_SCHEMA = "research-log-reproduction-status/7"
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
RECOVERY_GUARD_PREFIX = "reproduction-recovery-"
RECOVERY_GUARD_SCHEMA = "research-log-reproduction-recovery-guard/1"


def _prepare_plan(
    log: LogContext,
    entry: str | None,
    include_all: bool,
    runtime: ReproductionRuntime,
    selection: ReproductionSelection,
) -> ReproductionPlan:
    """Evaluate and plan once while the caller owns the normal log lock."""

    result = evaluate_mechanical(
        EvaluationRequest(log.summary, _utc_now()[:10], FullEvaluationTarget())
    )
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
            plan = _prepare_plan(log, entry, include_all, runtime, selection)
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
                    atomic_write_text(run_root / "plan.json", plan.serialized())
                    accepted = _accepted_record(
                        log,
                        plan,
                        run_id,
                        run_root,
                        accepted_at=accepted_at,
                    )
                    atomic_write_text(run_root / "run.json", _canonical(accepted))
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
            plan = _prepare_plan(log, entry, include_all, runtime, selection)
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
        _reconcile_lost_supervisor(log, root, run_id)
    record = _load_run(root / "run.json")
    plan = load_accepted_plan(root)
    _require_active_membership(record, plan)
    return _status_projection(record, plan, root)


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
    _reconcile_lost_supervisor(log, root, run_id)
    with _run_state_lock(log, run_id):
        record = _load_run(root / "run.json")
        state = cast(dict[str, object], record["state"])
        if state["status"] == "stopped":
            return _status_projection(record, load_accepted_plan(root), root)
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
    """Resume one stopped fixed plan or retry its failed publication."""

    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root, run_id)
    record = _load_run(root / "run.json")
    _verify_checkpoint_inventory(root, record)
    context = _resume_context(root, record)
    plan = load_accepted_plan(context.root)
    if context.publication_retry:
        verify_publication_retry_compatibility(log, plan)
    lock_fds = _acquire_scope_locks(log, context.entry)
    try:
        with operation_lock(
            resolve_project_root(log.root), "reproduction-promotion-index.lock"
        ):
            _verify_resume_activation(log, plan)
            (root / "stop.request").unlink(missing_ok=True)
            _activate_fixed_plan_resume(log, run_id, context, plan, now=_utc_now())
        _spawn_supervisor(
            log,
            root,
            lock_fds,
            mode=PUBLICATION_RETRY if context.publication_retry else STOPPED_RESUME,
        )
    except BaseException:
        _close_fds(lock_fds)
        raise
    _close_fds(lock_fds)
    return run_id


def _resume_context(root: Path, record: Mapping[str, object]) -> _ResumeContext:
    state = cast(Mapping[str, object], record["state"])
    publication_retry = _is_publication_retry(record, root)
    if state["status"] != "stopped" and not publication_retry:
        raise ActionError(
            "reproduction.resume.invalid_state",
            "only a stopped run or failed reproduction publication can resume",
        )
    return _ResumeContext(
        root,
        record,
        state,
        publication_retry,
        cast(str | None, load_accepted_plan(root).target["entry"]),
    )


def _verify_resume_activation(log: LogContext, plan: ReproductionPlan) -> None:
    _require_no_promotion_conflict(log, plan)


def _activate_fixed_plan_resume(
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
        current_publication_retry = _is_publication_retry(updated, context.root)
        if updated_state["status"] != "stopped" and not current_publication_retry:
            raise ActionError(
                "reproduction.resume.invalid_state",
                "run state changed before resume",
            )
        if current_publication_retry != context.publication_retry:
            raise ActionError(
                "reproduction.resume.invalid_state",
                "run recovery mode changed before resume",
            )
        updated_state.update(
            {"status": None, "phase": "accepted", "operational_failure": None}
        )
        _clear_active(updated_state)
        timestamps = cast(dict[str, object], updated["timestamps"])
        timestamps.update({"resumed_at": now, "stopped_at": None, "updated_at": now})
        _write_run(context.root, updated)


def _is_publication_retry(
    record: Mapping[str, object], root: Path | None = None
) -> bool:
    """Recognize a terminal current-format publication failure."""

    state = cast(Mapping[str, object], record["state"])
    timestamps = cast(Mapping[str, object], record["timestamps"])
    terminal = (
        state.get("status") == "failed"
        and state.get("phase") is None
        and isinstance(timestamps.get("finished_at"), str)
        and state.get("active_executions") == []
        and all(
            item.get("state") == "exited"
            for item in cast(Sequence[Mapping[str, object]], record["workers"])
        )
        and root is not None
        and all(
            item.get("state") in {"succeeded", "failed"}
            for item in _checkpoint_dicts(root)
        )
    )
    if not terminal:
        return False
    operational = state.get("operational_failure")
    if not isinstance(operational, Mapping):
        return False
    if operational.get("code") == "reproduction.publication.failed":
        return True
    return False


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
    resume = mode in {STOPPED_RESUME, PUBLICATION_RETRY}
    record = _load_run(run_root / "run.json")
    _verify_checkpoint_inventory(run_root, record)
    plan = load_accepted_plan(run_root)
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
        if mode == PUBLICATION_RETRY:
            _retry_publication(log, run_state, plan, comparisons, record)
            _close_fds(inherited_locks)
            return
        for comparison in comparisons.values():
            verify_evidence_only_context(plan)
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
        ):
            key = (attempt.entry, attempt.execution_id)
            if key not in comparisons:
                comparison = _compare_and_confirm(log, plan, workspace, attempt)
                comparisons[key] = comparison
        resumable = _resumable_execution_references(run_root, mode=mode)
        checkpoint_failures = _failed_checkpoint_references(run_root)
        prior_failures = (
            frozenset(
                f"{item.entry}:{item.execution_id}"
                for item in comparisons.values()
                if not item.matched
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

        def completed(attempt: ExecutionAttempt) -> bool:
            comparison = _compare_and_confirm(log, plan, workspace, attempt)
            comparisons[(attempt.entry, attempt.execution_id)] = comparison
            return comparison.matched

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
        _transition(run_state, phase="comparing")
        recorded_comparisons = load_recorded_comparisons(
            plan, workspace, verify_outputs=False
        )
        _transition(run_state, phase="publishing")
        _verify_accepted_materials(plan)
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
    except BaseException as error:
        _finish_failed(log, run_root, run_id, error)


def _compare_and_confirm(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    attempt: ExecutionAttempt,
) -> ExecutionComparison:
    comparison = compare_execution_outputs(log, plan, workspace, attempt)
    verify_evidence_only_context(plan)
    clear_execution_reproduction_requirement_locked(
        log,
        plan,
        comparison,
        project_root=workspace.source_project,
    )
    return comparison


def _publication_retry_skips(
    plan: ReproductionPlan,
    comparisons: Mapping[tuple[str, str], ExecutionComparison],
) -> tuple[Mapping[str, object], ...]:
    """Derive terminal dependency skips from immutable edges and comparisons."""

    expected = {
        (cast(str, item["entry"]), cast(str, item["execution_id"]))
        for item in plan.executions
    }
    if not set(comparisons) <= expected:
        raise ActionError(
            "reproduction.publication.inventory_invalid",
            "publication retry comparison is outside the accepted plan",
        )
    failed = {key for key, comparison in comparisons.items() if not comparison.matched}
    skipped: dict[tuple[str, str], Mapping[str, object]] = {}
    pending = {key for key in expected if key not in comparisons}
    by_key = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in plan.executions
    }
    while pending:
        changed = False
        for key in sorted(pending):
            dependencies = {
                _split_execution_reference(value)
                for value in cast(Sequence[str], by_key[key].get("depends_on", ()))
            }
            blocked = sorted(dependencies & (failed | set(skipped)))
            if blocked:
                skipped[key] = {
                    "depends_on": [
                        f"{entry}:{identity}" for entry, identity in blocked
                    ],
                    "entry": key[0],
                    "execution_id": key[1],
                    "reason": "dependency_failed",
                }
                pending.remove(key)
                changed = True
        if not changed:
            break
    if not expected or pending:
        raise ActionError(
            "reproduction.publication.inventory_invalid",
            "publication retry requires complete durable terminal accounting",
        )
    return tuple(skipped[key] for key in sorted(skipped))


def _retry_publication(
    log: LogContext,
    run_state: _RunStateContext,
    plan: ReproductionPlan,
    comparisons: Mapping[tuple[str, str], ExecutionComparison],
    record: Mapping[str, object],
) -> None:
    """Retry only the durable publication transaction for a fixed terminal run."""

    verify_evidence_only_context(plan)
    skips = _publication_retry_skips(plan, comparisons)
    _require_retry_checkpoint_inventory(run_state.root, plan, comparisons, skips)
    project_root = resolve_project_root(log.root)
    for comparison in comparisons.values():
        clear_execution_reproduction_requirement_locked(
            log, plan, comparison, project_root=project_root
        )
        verify_evidence_only_context(plan)
        _verify_accepted_materials(plan)
    _transition(run_state, phase="publishing")
    accepted_at = cast(Mapping[str, str | None], record["timestamps"])["accepted_at"]
    finished = _utc_now()
    published = publish_completed_reproduction(
        log,
        CompletedPublication(
            plan,
            tuple(comparisons.values()),
            run_state.run_id,
            cast(str, accepted_at),
            finished,
            run_state.root,
            skips,
        ),
    )
    run = next(
        item for item in published.results.runs if item.run_id == run_state.run_id
    )
    _finish_complete(
        log, run_state.root, run_state.run_id, run.artifact_outcomes, finished
    )


def _require_retry_checkpoint_inventory(
    root: Path,
    plan: ReproductionPlan,
    comparisons: Mapping[tuple[str, str], ExecutionComparison],
    skips: Sequence[Mapping[str, object]],
) -> None:
    """Require one complete, non-overlapping terminal retry inventory."""

    checkpoints = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in _checkpoint_dicts(root)
    }
    skipped = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])) for item in skips
    }
    expected = {
        (cast(str, item["entry"]), cast(str, item["execution_id"]))
        for item in plan.executions
    }
    compared = set(comparisons)
    if (
        not expected
        or compared & skipped
        or compared | skipped != expected
        or set(checkpoints) != compared
        or any(
            item["state"] not in {"succeeded", "failed"}
            for item in checkpoints.values()
        )
    ):
        raise ActionError(
            "reproduction.publication.inventory_invalid",
            "publication retry checkpoint inventory is incomplete",
        )
    for key, comparison in comparisons.items():
        checkpoint = checkpoints[key]
        invocation = accepted_invocation(plan, *key)
        declared = {name for name, _kind in invocation.execution.recipe.outputs}
        artifacts = {item.artifact: item for item in comparison.artifacts}
        outputs = {
            cast(str, item["artifact"]): cast(Mapping[str, object], item["fingerprint"])
            for item in cast(Sequence[Mapping[str, object]], checkpoint["outputs"])
        }
        if set(artifacts) != declared:
            raise ActionError(
                "reproduction.publication.inventory_invalid",
                "publication retry comparison outputs disagree with the "
                "accepted execution",
            )
        if checkpoint["state"] == "succeeded":
            if not comparison.complete or set(outputs) != declared:
                raise ActionError(
                    "reproduction.publication.inventory_invalid",
                    "successful checkpoint lacks complete durable comparison evidence",
                )
            if any(
                artifact.regenerated != outputs[name]
                for name, artifact in artifacts.items()
            ):
                raise ActionError(
                    "reproduction.publication.inventory_invalid",
                    "checkpoint output observations disagree with durable comparison",
                )
        elif comparison.complete:
            raise ActionError(
                "reproduction.publication.inventory_invalid",
                "failed checkpoint cannot retain a complete comparison",
            )


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


def _split_execution_reference(value: str) -> tuple[str, str]:
    """Decode one planner-owned entry-qualified execution dependency."""

    entry, separator, identity = value.partition(":pyrun-exec/")
    if not separator:
        raise ActionError(
            "reproduction.publication.inventory_invalid", "invalid dependency"
        )
    return entry, f"pyrun-exec/{identity}"


def _resumable_execution_references(root: Path, *, mode: str) -> frozenset[str]:
    if mode != STOPPED_RESUME:
        return frozenset()
    return frozenset(
        f"{item['entry']}:{item['execution_id']}"
        for item in _checkpoint_dicts(root)
        if item["state"] == "stopped"
    )


def _failed_checkpoint_references(root: Path) -> frozenset[str]:
    return frozenset(
        f"{item['entry']}:{item['execution_id']}"
        for item in _checkpoint_dicts(root)
        if item["state"] == "failed"
    )


def supervisor_main(arguments: Sequence[str]) -> int:
    """Internal detached-supervisor process entrypoint."""

    if len(arguments) != 4:
        return 2
    summary, run_root, raw_fds, raw_resume = arguments
    if raw_resume not in {"0", "1", "2"}:
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


def _acquire_scope_locks(
    log: LogContext,
    entry: str | None,
    *,
    ignore_recovery_run_id: str | None = None,
) -> tuple[int, ...]:
    directory = operation_directory(log.root)
    directory.mkdir(parents=True, exist_ok=True)
    require_mutation_ready(log.root, entry_id=entry)
    _require_no_recovery_guard(
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
        # A status process can persist a survivor guard after our initial
        # check but before this process owns all overlapping fds.  Recheck
        # while those fds are held, and let the exception path close them.
        _require_no_recovery_guard(
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
        plan = load_accepted_plan(run.root)
        state = cast(dict[str, object], record["state"])
        progress = cast(dict[str, object], record["progress"])
        reference = _execution_progress_reference(entry, identity)
        if _plan_order(plan, reference) == 2**31:
            raise ActionError(
                "reproduction.run.invalid",
                f"execution is absent from accepted plan: {entry}:{identity}",
            )
        if event == "started":
            active = cast(list[Mapping[str, object]], state["active_executions"])
            if reference not in active:
                active.append(reference)
                active.sort(key=lambda item: _plan_order(plan, item))
        else:
            state["active_executions"] = [
                item
                for item in cast(list[Mapping[str, object]], state["active_executions"])
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
        retained = [
            item
            for item in cast(Sequence[Mapping[str, object]], record["workers"])
            if (item.get("entry"), item.get("execution_id")) != (entry, identity)
        ]
        current = [item.as_dict() for item in workers]
        record["workers"] = sorted(
            (*retained, *current),
            key=_worker_sort_key,
        )
        _stamp(record)
        _write_run(run.root, record)


def _plan_order(plan: ReproductionPlan, reference: Mapping[str, object]) -> int:
    for item in cast(
        Sequence[Mapping[str, object]],
        plan.executions,
    ):
        if (
            isinstance(item, Mapping)
            and item.get("entry") == reference.get("entry")
            and item.get("execution_id") == reference.get("execution_id")
        ):
            return cast(int, item["order"])
    return 2**31


def _plan_item(
    plan: ReproductionPlan, reference: Mapping[str, object]
) -> Mapping[str, object]:
    for item in cast(
        Sequence[Mapping[str, object]],
        plan.executions,
    ):
        if item.get("entry") == reference.get("entry") and item.get(
            "execution_id"
        ) == reference.get("execution_id"):
            return item
    return {}


def _worker_sort_key(item: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(item.get("entry") or ""),
        str(item.get("execution_id") or ""),
        str(item["worker_id"]),
    )


def _clear_active(state: dict[str, object]) -> None:
    state["active_executions"] = []


def _transition(
    run: _RunStateContext,
    *,
    phase: str,
    started: bool = False,
) -> None:
    with _locked_run(run) as record:
        state = cast(dict[str, object], record["state"])
        state["phase"] = phase
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
        workers = _combined_attempt_workers(
            attempts,
            prior=cast(Sequence[Mapping[str, object]], record["workers"]),
        )
        workers = _reconciled_worker_history(workers, observed_survivors)
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
        workers = _combined_attempt_workers(
            attempts,
            prior=cast(Sequence[Mapping[str, object]], record["workers"]),
        )
        workers = _reconciled_worker_history(workers, ())
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
        _write_run(run_root, record)


def _combined_attempt_workers(
    attempts: Sequence[ExecutionAttempt],
    *,
    prior: Sequence[Mapping[str, object]] = (),
) -> list[Mapping[str, object]]:
    """Return every attempt worker in stable compound-identity order."""

    retained = {_worker_identity(item): dict(item) for item in prior}
    for attempt in attempts:
        for worker in attempt.workers:
            value = worker.as_dict()
            key = _worker_identity(value)
            retained[key] = value
    return sorted(retained.values(), key=_worker_sort_key)


def _worker_identity(item: Mapping[str, object]) -> tuple[object, object, object]:
    return (
        item.get("entry"),
        item.get("execution_id"),
        item.get("worker_id"),
    )


def _reconciled_worker_history(
    prior: Sequence[Mapping[str, object]],
    survivors: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    live = {_worker_identity(item): item for item in survivors}
    retained = {
        _worker_identity(item): {
            **item,
            "state": ("running" if _worker_identity(item) in live else "exited"),
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
    return sorted(retained.values(), key=_worker_sort_key)


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
        )
        record["workers"] = workers
        if survivors:
            state.update({"status": None, "phase": "stopping"})
            _stamp(record)
            _write_run(run_root, record)
            return False
        now = _utc_now()
        _terminalize_active_checkpoints(
            run_root,
            now=now,
            terminal=_CheckpointTerminal(
                "failed",
                cast(str, operational["code"]),
                cast(str, operational["message"]),
            ),
        )
        _synchronize_checkpoint_progress(record, run_root)
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
    """Reserve the accepted scope before reconciling an orphaned supervisor."""

    record = _load_run(run_root / "run.json")
    _require_run_identity(record, run_id)
    if cast(Mapping[str, object], record["state"])["status"] is not None:
        return
    pid = _supervisor_pid(run_root)
    if pid is not None and _pid_alive(pid):
        return
    plan = load_accepted_plan(run_root)
    fds = _acquire_scope_locks(
        log,
        cast(str | None, plan.target["entry"]),
        ignore_recovery_run_id=run_id,
    )
    try:
        _reconcile_lost_supervisor_reserved(log, run_root, run_id)
        latest = _load_run(run_root / "run.json")
        state = cast(Mapping[str, object], latest["state"])
        workers = cast(Sequence[Mapping[str, object]], latest["workers"])
        survivors = [item for item in workers if item.get("state") == "running"]
        if state["status"] is None and state["phase"] == "stopping" and survivors:
            _write_recovery_guard(
                log, run_id, cast(str | None, plan.target["entry"]), survivors
            )
        elif state["status"] is not None:
            _clear_recovery_guard(log, run_id)
    finally:
        _close_fds(fds)


def _reconcile_lost_supervisor_reserved(
    log: LogContext, run_root: Path, run_id: str
) -> None:
    record = _load_run(run_root / "run.json")
    plan = load_accepted_plan(run_root)
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
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        _require_run_identity(record, run_id)
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return
        now = _utc_now()
        workers = _reconciled_worker_history(
            cast(Sequence[Mapping[str, object]], record["workers"]),
            survivors,
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
            _write_recovery_guard(
                log,
                run_id,
                cast(str | None, plan.target["entry"]),
                survivors,
            )
        else:
            _stop_active_checkpoints(run_root, now=now)
            _synchronize_checkpoint_progress(record, run_root)
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


def _recovery_guard_path(log: LogContext, run_id: str) -> Path:
    return operation_directory(log.root) / f"{RECOVERY_GUARD_PREFIX}{run_id}.json"


def _write_recovery_guard(
    log: LogContext,
    run_id: str,
    entry: str | None,
    survivors: Sequence[Mapping[str, object]],
) -> None:
    """Persist exclusion after this inspecting process releases its descriptors."""

    atomic_write_text(
        _recovery_guard_path(log, run_id),
        _canonical(
            {
                "entry": entry,
                "run_id": run_id,
                "schema": RECOVERY_GUARD_SCHEMA,
                "workers": [
                    {
                        "pid": item.get("pid"),
                        "worker_id": item.get("worker_id"),
                    }
                    for item in survivors
                ],
            }
        ),
    )


def _clear_recovery_guard(log: LogContext, run_id: str) -> None:
    _recovery_guard_path(log, run_id).unlink(missing_ok=True)


def _require_no_recovery_guard(
    log: LogContext,
    entry: str | None,
    *,
    ignore_recovery_run_id: str | None,
) -> None:
    """Reject new work while an orphan cleanup still owns an overlapping scope."""

    directory = operation_directory(log.root)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob(f"{RECOVERY_GUARD_PREFIX}*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ActionError(
                "reproduction.recovery.guard_invalid", str(path)
            ) from error
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != RECOVERY_GUARD_SCHEMA
            or not isinstance(value.get("run_id"), str)
            or value.get("entry") is not None
            and not isinstance(value.get("entry"), str)
            or not isinstance(value.get("workers"), list)
        ):
            raise ActionError("reproduction.recovery.guard_invalid", str(path))
        guard_run = cast(str, value["run_id"])
        guard_entry = cast(str | None, value["entry"])
        if guard_run == ignore_recovery_run_id:
            continue
        if entry is None or guard_entry is None or entry == guard_entry:
            raise ActionError(
                "reproduction.recovery.active",
                f"orphaned worker cleanup still owns {guard_run}",
            )

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
) -> list[Mapping[str, object]]:
    return _terminalize_active_checkpoints(
        run_root,
        now=now,
        terminal=_CheckpointTerminal(
            "stopped",
            "supervisor_lost",
            "The durable supervisor was interrupted.",
        ),
    )


def _synchronize_checkpoint_progress(record: dict[str, object], run_root: Path) -> None:
    checkpoints = _checkpoint_dicts(run_root)
    completed = sum(item["state"] != "active" for item in checkpoints)
    progress = cast(dict[str, object], record["progress"])
    progress["completed_executions"] = completed


def _terminalize_active_checkpoints(
    run_root: Path,
    *,
    now: str,
    terminal: _CheckpointTerminal,
) -> list[Mapping[str, object]]:
    checkpoints = _checkpoint_dicts(run_root)
    for checkpoint in checkpoints:
        if checkpoint["state"] != "active":
            continue
        updated = dict(checkpoint)
        updated["state"] = terminal.state
        updated["failure"] = {
            "code": terminal.code,
            "message": terminal.message,
            "recorded_at": now,
        }
        path = run_root / cast(str, updated["path"])
        atomic_write_text(path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
    stopped = _checkpoint_dicts(run_root)
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
    return {
        "paths": {
            "diagnostics": "diagnostics",
            "run": canonical_run_path(now, run_root.name).as_posix(),
            "staging": "executions",
            "workspace": "workspace",
        },
        "progress": {
            "artifact_outcomes": {name: 0 for name in OUTCOMES},
            "completed_executions": 0,
        },
        "run_id": run_id,
        "schema": RUN_SCHEMA,
        "state": {
            "active_executions": [],
            "latest_execution_diagnostic": None,
            "operational_failure": None,
            "phase": "accepted",
            "status": None,
        },
        "timestamps": {
            "accepted_at": now,
            "finished_at": None,
            "resumed_at": None,
            "started_at": None,
            "stopped_at": None,
            "updated_at": now,
        },
        "workers": [],
    }


def load_accepted_plan(run_root: Path) -> ReproductionPlan:
    """Load the one immutable plan accepted beside a current run record."""

    path = run_root / "plan.json"
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("accepted plan is not a regular file")
        return ReproductionPlan.from_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise ActionError("reproduction.run.invalid", str(error)) from error


def _status_projection(
    record: Mapping[str, object], plan: ReproductionPlan, root: Path
) -> Mapping[str, object]:
    state = cast(Mapping[str, object], record["state"])
    progress = cast(Mapping[str, object], record["progress"])
    workers = cast(Sequence[Mapping[str, object]], record["workers"])
    checkpoints = _checkpoint_dicts(root)
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
        "execution_timeout_seconds": plan.execution_timeout_seconds,
        "execution_timings": _execution_timings(checkpoints),
        "include_all": plan.include_all,
        "jobs": plan.jobs,
        "latest_execution_diagnostic": state["latest_execution_diagnostic"],
        "operational_failure": state["operational_failure"],
        "phase": state["phase"],
        "run_id": record["run_id"],
        "schema": STATUS_SCHEMA,
        "status": state["status"],
        "summary": plan.summary,
        "surviving_workers": _running_workers(workers),
        "target": plan.target,
        "timestamps": record["timestamps"],
        "total_executions": len(plan.executions),
    }
    projection["resumable"] = state["status"] == "stopped" or _is_publication_retry(
        record, root
    )
    return _bounded_status(projection)


def _require_active_membership(
    record: Mapping[str, object], plan: ReproductionPlan
) -> None:
    """Require active permits to be accepted, ordered, and within the job cap."""

    state = cast(Mapping[str, object], record["state"])
    active = cast(Sequence[Mapping[str, object]], state["active_executions"])
    order = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): cast(
            int, item["order"]
        )
        for item in plan.executions
    }
    keys = [
        (cast(str, item["entry"]), cast(str, item["execution_id"])) for item in active
    ]
    if (
        len(keys) > plan.jobs
        or any(key not in order for key in keys)
        or keys != sorted(keys, key=lambda key: order[key])
    ):
        raise ActionError(
            "reproduction.run.invalid",
            "active executions do not match accepted scheduling membership",
        )


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
    checkpoints: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [
        {
            "elapsed_seconds": item["elapsed_seconds"],
            "entry": item["entry"],
            "execution_id": item["execution_id"],
            "failure": item.get("failure"),
            "finished_at": item["finished_at"],
            "started_at": item["started_at"],
            "state": item["state"],
        }
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
    fields = {"paths", "progress", "run_id", "schema", "state", "timestamps", "workers"}
    if not isinstance(value, dict) or value.get("schema") != RUN_SCHEMA:
        raise ActionError(
            "reproduction.run.unsupported",
            "this historical reproduction job cannot resume; "
            "start a new current-format run",
        )
    if set(value) != fields:
        raise ActionError("reproduction.run.invalid", "run record fields are invalid")
    if RUN_ID_RE.fullmatch(str(value.get("run_id"))) is None:
        raise ActionError("reproduction.run.invalid", "run ID is invalid")
    state = value.get("state")
    current_state_fields = {
        "active_executions",
        "latest_execution_diagnostic",
        "operational_failure",
        "phase",
        "status",
    }
    if not isinstance(state, dict) or frozenset(state) != frozenset(
        current_state_fields
    ):
        raise ActionError("reproduction.run.invalid", "run state is invalid")
    status = state["status"]
    phase = state["phase"]
    if status not in {*TERMINAL_STATUSES, None} or phase not in {*ACTIVE_PHASES, None}:
        raise ActionError("reproduction.run.invalid", "run lifecycle is invalid")
    if (status is None) == (phase is None):
        raise ActionError("reproduction.run.invalid", "run lifecycle is incoherent")
    _validate_run_members(value)
    canonical = _canonical(value).encode("utf-8")
    if raw != canonical:
        raise ActionError("reproduction.run.invalid", "run record is not canonical")
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
    _validate_progress(value.get("progress"))
    _validate_timestamps(value.get("timestamps"))
    _validate_paths(value)
    state = cast(Mapping[str, object], value["state"])
    _validate_active_state(value, state)
    _validate_failure(state.get("latest_execution_diagnostic"))
    _validate_failure(state.get("operational_failure"))
    _validate_workers(value.get("workers"))


def _validate_active_state(
    value: Mapping[str, object], state: Mapping[str, object]
) -> None:
    active = state.get("active_executions")
    if (
        not isinstance(active, list)
        or len(active) > MAX_CHECKPOINTS
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"entry", "execution_id"}
            or not isinstance(item.get("entry"), str)
            or not isinstance(item.get("execution_id"), str)
            or EXECUTION_ID_RE.fullmatch(cast(str, item.get("execution_id"))) is None
            for item in active
        )
    ):
        raise ActionError("reproduction.run.invalid", "active executions are invalid")
    typed = cast(Sequence[Mapping[str, object]], active)
    keys = [
        (cast(str, item["entry"]), cast(str, item["execution_id"])) for item in typed
    ]
    if len(keys) != len(set(keys)):
        raise ActionError(
            "reproduction.run.invalid", "active execution order is invalid"
        )


def _validate_progress(value: object) -> None:
    progress = value
    if not isinstance(progress, Mapping) or set(progress) != {
        "artifact_outcomes",
        "completed_executions",
    }:
        raise ActionError("reproduction.run.invalid", "run progress is invalid")
    counts = progress.get("artifact_outcomes")
    completed = progress.get("completed_executions")
    if (
        not isinstance(counts, Mapping)
        or set(counts) != set(OUTCOMES)
        or any(not _nonnegative_int(item) for item in counts.values())
        or not _nonnegative_int(completed)
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
    if (
        not isinstance(value.get("run"), str)
        or Path(cast(str, value["run"])).is_absolute()
    ):
        raise ActionError("reproduction.run.invalid", "run path is not canonical")
    logical = PurePosixPath(cast(str, value["run"]))
    accepted_at = cast(Mapping[str, object], record["timestamps"])["accepted_at"]
    if (
        not isinstance(accepted_at, str)
        or logical.parts[:3] != ("tmp", "reproduction", accepted_at[:10])
        or len(logical.parts) != 4
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        raise ActionError("reproduction.run.invalid", "run path is not canonical")


def _validate_failure(value: object) -> None:
    if value is None:
        return
    fields = {"code", "execution_id", "message", "recorded_at"}
    expected = fields | {"entry"}
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
    if (
        entry is not None
        and (not isinstance(entry, str) or re.fullmatch(r"e[0-9]{3}", entry) is None)
        or (entry is None) != (execution is None)
    ):
        raise ActionError("reproduction.run.invalid", "failure entry is invalid")
    recorded = value.get("recorded_at")
    if not isinstance(recorded, str) or TIMESTAMP_RE.fullmatch(recorded) is None:
        raise ActionError("reproduction.run.invalid", "failure timestamp is invalid")


def _validate_workers(value: object) -> None:
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
    expected = fields | {"entry"}
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
            or entry is not None
            and (
                not isinstance(entry, str) or re.fullmatch(r"e[0-9]{3}", entry) is None
            )
            or (entry is None) != (execution is None)
        ):
            raise ActionError("reproduction.run.invalid", "worker record is invalid")
    typed = cast(Sequence[Mapping[str, object]], value)
    keys = [
        (
            str(item.get("entry") or ""),
            str(item.get("execution_id") or ""),
            cast(str, item["worker_id"]),
        )
        for item in typed
    ]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ActionError("reproduction.run.invalid", "worker order is invalid")


def _validate_checkpoints(value: object) -> None:
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
    expected = fields | {"failure"}
    if any(not isinstance(item, Mapping) or set(item) != expected for item in value):
        raise ActionError("reproduction.run.invalid", "checkpoint record is invalid")
    timestamp_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for item in cast(Sequence[Mapping[str, object]], value):
        state = item["state"]
        started_at = item["started_at"]
        finished_at = item["finished_at"]
        completed_at = item["completed_at"]
        elapsed_seconds = item["elapsed_seconds"]
        success = "succeeded"
        states = {"active", "succeeded", "failed", "stopped"}
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
        if (
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
            if record["run_id"] == run_id and load_accepted_plan(
                candidate
            ).summary == _summary_identity(log):
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


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"reproduce-{stamp}-{secrets.token_hex(6)}"


def _summary_identity(log: LogContext) -> str:
    return log.summary.resolve().relative_to(resolve_project_root(log.root)).as_posix()


def _checkpoint_dicts(run_root: Path) -> list[Mapping[str, object]]:
    root = run_root / "checkpoints"
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint root is invalid"
        )
    pairs = [(path, _checkpoint_value(path)) for path in _checkpoint_paths(root)]
    values = [value for _path, value in pairs]
    _validate_checkpoints(values)
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
    plan = load_accepted_plan(run_root)
    accepted = {(item["entry"], item["execution_id"]) for item in plan.executions}
    observed = {(item["entry"], item["execution_id"]) for item in checkpoints}
    if not observed <= accepted:
        raise ActionError(
            "reproduction.checkpoint.invalid",
            "checkpoint is absent from the accepted plan",
        )


def _verify_checkpoint_inventory(run_root: Path, record: Mapping[str, object]) -> None:
    del record
    _checkpoint_dicts(run_root)


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
