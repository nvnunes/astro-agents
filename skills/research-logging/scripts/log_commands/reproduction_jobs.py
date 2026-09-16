"""Durable launch, status, stop, resume, and supervision for reproduction."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, cast

from validation.engine import (
    EvaluationRequest,
    EvaluationResult,
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
from .reproduction_domain import WorkSelection
from .reproduction_execution import (
    preflight_execution_safety,
)
from .reproduction_invocation import (
    ReproductionRuntime,
)
from .reproduction_job_control import (
    JobStoreError,
    RunOwner,
    RunResumeRequest,
    RunStopRequest,
    recognize_run_directory,
)
from .reproduction_paths import (
    canonical_run_path,
    iter_canonical_run_roots,
    run_leaf,
)
from .reproduction_planner import (
    ReproductionSelection,
    plan_reproduction_work,
    prepare_reproduction_context,
)
from .reproduction_process_recovery import (
    _pid_alive,
)
from .reproduction_saved_run import RunSettings, RunTarget
from .reproduction_work_job import WorkJobAcceptance, create_work_job, open_work_job
from .reproduction_work_plan import ReproductionPlan

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
class ReproductionLaunch:
    """One accepted run ID or terminal current-work reconciliation."""

    run_id: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        if (self.run_id is None) == (self.summary is None):
            raise ValueError("reproduction launch needs exactly one result")

    def render(self) -> str:
        """Return the CLI-owned launch acknowledgment or current-work summary."""
        return (
            f"{self.run_id}\n" if self.run_id is not None else cast(str, self.summary)
        )


def _prepare_reproduction_evaluation(
    log: LogContext,
    *,
    publish_validation: bool,
) -> EvaluationResult:
    """Evaluate once under the log lock; only real launch publishes validation."""

    accepted_snapshot = research_snapshot(log.summary)
    result = evaluate_mechanical(EvaluationRequest(log.summary, FullEvaluationTarget()))
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
    return result


def _prepare_work(
    log: LogContext,
    target: RunTarget,
    settings: RunSettings,
    *,
    publish_validation: bool,
) -> tuple[ReproductionPlan, str]:
    evaluation = _prepare_reproduction_evaluation(
        log, publish_validation=publish_validation
    )
    plan = plan_reproduction_work(
        log,
        prepare_reproduction_context(evaluation),
        entry=resolve_entry(log, target.entry) if target.entry is not None else None,
        include_all=settings.include_all,
        runtime=ReproductionRuntime(settings.jobs, settings.execution_timeout_seconds),
        selection=ReproductionSelection(
            "recheck" if settings.recheck else "incremental"
        ),
    )
    assert evaluation.snapshot is not None
    return plan, evaluation.snapshot.source_identity


def launch_reproduction(
    log: LogContext, target: RunTarget, settings: RunSettings
) -> ReproductionLaunch:
    """Accept fresh native work and hand its locks to the detached supervisor.

    No runnable work creates neither run nor saved result; return the same current
    plan view as preview. Real preparation still publishes fresh validation.
    """
    lock_fds = _acquire_scope_locks(log, target.entry)
    try:
        with operation_lock(log.root, "log.lock", mode="exclusive"):
            plan, source = _prepare_work(log, target, settings, publish_validation=True)
            if not any(work.selection is WorkSelection.RUN for work in plan.commands):
                from .reproduction_plan_preview import plan_page, render_plan_page

                if (
                    settings.recheck
                    and target.kind == "log"
                    and not (plan.commands or plan.artifacts)
                ):
                    from research_log_result_store import results_lock

                    from .reproduction_saved_report import (
                        materialize_saved_report_locked,
                    )
                    from .reproduction_saved_storage import confirm_empty_replacement

                    with operation_lock(log.root, "reproduction-publication.lock"):
                        with results_lock(log.root):
                            if (
                                confirm_empty_replacement(log.root, _utc_now())
                                is not None
                            ):
                                materialize_saved_report_locked(log)

                return ReproductionLaunch(
                    summary=render_plan_page(plan_page(plan, source))
                )
            project = resolve_project_root(log.root)
            run_id, accepted_at = _new_run_id(), _utc_now()
            run_root = _new_run_root(project, log, target.entry, run_id, accepted_at)
            with operation_lock(project, "reproduction-promotion-index.lock"):
                with operation_lock(log.root, "reproduction-publication.lock"):
                    _require_no_promotion_conflict(log, plan)
                run_root.mkdir(parents=True)
                create_work_job(
                    run_root,
                    WorkJobAcceptance(
                        run_id,
                        plan,
                        accepted_at,
                        canonical_run_path(accepted_at, run_root.name).as_posix(),
                        project,
                    ),
                )
        _spawn_supervisor(log, run_root, lock_fds, mode=FRESH_RUN)
    finally:
        _close_fds(lock_fds)
    return ReproductionLaunch(run_id=run_id)


def preview_reproduction(
    log: LogContext,
    target: RunTarget,
    settings: RunSettings,
    *,
    cursor: str | None = None,
    format: str = "text",
) -> dict[str, object]:
    """Prepare the shared native plan without publishing or accepting work.

    Scope/log locks and physical safety preflight apply. A continuation reruns
    preparation and rejects changed source/settings/history; no cached plan is
    execution authority.
    """
    from .reproduction_plan_preview import plan_page

    fds = _acquire_scope_locks(log, target.entry)
    try:
        with operation_lock(log.root, "log.lock", mode="exclusive"):
            plan, source = _prepare_work(
                log, target, settings, publish_validation=False
            )
            page = plan_page(plan, source, cursor=cursor, format=format)
        preflight_execution_safety()
        return page
    finally:
        _close_fds(fds)


def reproduction_status(
    log: LogContext, run_id: str, *, reconcile: bool = True
) -> Mapping[str, object]:
    """Inspect native lifecycle and retained diagnostics, including unpublished work."""
    root = _find_run(log, run_id)
    if reconcile:
        _reconcile_lost_supervisor(log, root)
    with open_work_job(root) as job:
        return _bounded_status(job.load_operational_status())


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
    """Request bounded worker-tree shutdown and wait for a stable native result."""
    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root)
    with open_work_job(root) as job:
        status = job.load_run_control()
        if status.status == "stopped":
            return _bounded_status(job.load_operational_status())
        if status.status is not None:
            raise ActionError(
                "reproduction.stop.invalid_state", f"run is already {status.status}"
            )
        job.request_run_stop(RunStopRequest(_utc_now()))
    from .reproduction_scheduler import cancel_run_waiters

    cancel_run_waiters(resolve_project_root(log.root), run_id)
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        projected = reproduction_status(log, run_id)
        if projected["status"] == "stopped":
            return projected
        if projected["status"] in {"failed", "complete"}:
            raise ActionError(
                "reproduction.stop.failed"
                if projected["status"] == "failed"
                else "reproduction.stop.completed",
                "run became terminal before stop completed",
            )
        time.sleep(STATUS_POLL_SECONDS)
    projected = reproduction_status(log, run_id)
    raise ActionError(
        "reproduction.stop.incomplete",
        _survivor_summary(projected.get("surviving_workers")),
    )


def resume_reproduction(log: LogContext, run_id: str) -> str:
    """Resume stopped fixed acceptance or retry its frozen failed publication."""
    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root)
    with open_work_job(root) as job:
        status = job.load_run_control()
        plan = job.accepted.plan
        publication_retry = (
            status.status == "failed"
            and status.operational_code == "reproduction.publication.failed"
            and job.load_publication() is not None
        )
        if status.status != "stopped" and not publication_retry:
            raise ActionError(
                "reproduction.resume.invalid_state",
                "only a stopped run or failed reproduction publication can resume",
            )
    fds = _acquire_scope_locks(log, plan.target.entry)
    try:
        with operation_lock(
            resolve_project_root(log.root), "reproduction-promotion-index.lock"
        ):
            _require_no_promotion_conflict(log, plan)
            with open_work_job(root) as job:
                request = RunResumeRequest(_utc_now())
                if publication_retry:
                    job.begin_publication_resume(request)
                else:
                    job.begin_run_resume(request)
        _spawn_supervisor(
            log,
            root,
            fds,
            mode=PUBLICATION_RETRY if publication_retry else STOPPED_RESUME,
        )
    finally:
        _close_fds(fds)
    return run_id


def supervise_reproduction(
    log: LogContext,
    run_root: Path,
    *,
    mode: str,
    inherited_locks: Sequence[int],
    confinement: Any = None,
) -> None:
    """Run one native accepted lifecycle while retaining inherited scope locks."""
    if mode not in {FRESH_RUN, STOPPED_RESUME, PUBLICATION_RETRY}:
        raise ActionError(
            "reproduction.run.invalid", f"invalid supervisor mode: {mode}"
        )
    from .reproduction_work_supervision import WorkPlanControl, supervise_work_job

    supervise_work_job(
        log,
        run_root,
        mode=cast(Literal["fresh", "stopped", "publication"], mode),
        control=WorkPlanControl(
            os.getpid(), resume=mode == STOPPED_RESUME, confinement=confinement
        ),
    )


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
        with open_work_job(run_root) as job:
            status = job.load_operational_status()
            timestamps = cast(Mapping[str, str], status["timestamps"])
            now = max(_utc_now(), timestamps["updated_at"])
            job.replace_run_owner(RunOwner(process.pid, "running", now, now))
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
    materials = plan.materials
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


def _reconcile_lost_supervisor(log: LogContext, run_root: Path) -> None:
    from .reproduction_work_recovery import recover_work_job

    with open_work_job(run_root) as job:
        state, owner = job.load_run_control(), job.load_run_owner()
        if (
            owner is not None
            and owner.state == "running"
            and _pid_alive(owner.supervisor_pid)
        ):
            return
        if state.status is not None and (owner is None or owner.state != "running"):
            return
        entry, run_id = job.accepted.plan.target.entry, job.accepted.run_id
    fds = _acquire_scope_locks(log, entry, ignore_recovery_run_id=run_id)
    try:
        recover_work_job(log, run_root)
    finally:
        _close_fds(fds)


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
        if recognize_run_directory(run_root, project_root) != "current":
            continue
        try:
            with open_work_job(run_root) as job:
                plan = job.accepted.plan
                status = job.load_run_control()
                owner = job.load_run_owner()
                run_id = job.accepted.run_id
            if plan.summary != _summary_identity(log):
                continue
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
        target_entry = plan.target.entry
        if needs_recovery and (
            entry is None or target_entry is None or entry == target_entry
        ):
            raise ActionError(
                "reproduction.recovery.active",
                f"orphaned worker cleanup still owns {run_id}",
            )


def load_accepted_plan(run_root: Path) -> ReproductionPlan:
    """Load native immutable acceptance; never decode older job formats."""
    try:
        with open_work_job(run_root) as job:
            return job.accepted.plan
    except JobStoreError as error:
        raise ActionError(error.code, str(error)) from error


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
    project_root = resolve_project_root(log.root)
    try:
        candidates = iter_canonical_run_roots(
            project_root, max_entries=MAX_RUN_DIRECTORIES
        )
    except OSError as error:
        code = (
            "reproduction.run.resource_limit"
            if "scan limit" in str(error)
            else "reproduction.run.missing"
        )
        raise ActionError(code, str(error)) from error
    matches, unsupported = _matching_run_roots(
        log, run_id, candidates, project_root=project_root
    )
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
    log: LogContext,
    run_id: str,
    candidates: Sequence[Path],
    *,
    project_root: Path,
) -> tuple[list[Path], list[Path]]:
    """Classify exact current and historical candidates without decoding JSON."""

    matches: list[Path] = []
    unsupported: list[Path] = []
    for candidate in candidates:
        if not candidate.name.endswith(f"-{run_id}"):
            continue
        recognized = recognize_run_directory(candidate, project_root)
        if recognized == "current":
            try:
                with open_work_job(candidate) as job:
                    accepted = job.accepted
            except JobStoreError as error:
                raise ActionError(error.code, str(error)) from error
            if accepted.run_id == run_id and accepted.plan.summary == _summary_identity(
                log
            ):
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
    return str(log.summary.resolve())


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
