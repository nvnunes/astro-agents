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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, cast

import psutil
from validation.operation_state import (
    OperationLockError,
    operation_directory,
    operation_lock,
    require_mutation_ready,
)

from .context import LogContext, resolve_entry, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_comparison import compare_execution_outputs
from .reproduction_contract import PLAN_SCHEMA, ReproductionPlan
from .reproduction_execution import (
    ExecutionAttempt,
    ExecutionControl,
    completed_execution_attempts,
    execute_reproduction_plan,
    open_existing_workspace,
    populate_disposable_copy,
    preflight_execution_safety,
)
from .reproduction_planner import plan_reproduction, verify_reproduction_snapshot
from .reproduction_publication import (
    CompletedPublication,
    publish_completed_reproduction,
)
from .reproduction_results import OUTCOMES
from .storage import atomic_write_text

RUN_SCHEMA = "research-log-reproduction-run/1"
STATUS_SCHEMA = "research-log-reproduction-status/1"
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
MAX_RUN_RECORD_BYTES = 128 << 20
MAX_RUN_DIRECTORIES = 100_000
STOP_WAIT_SECONDS = 45.0
STATUS_POLL_SECONDS = 0.1


def launch_reproduction(
    log: LogContext, *, entry: str | None, include_slow: bool
) -> str:
    """Accept one immutable plan and hand its scope lock to a supervisor."""

    selected = resolve_entry(log, entry) if entry is not None else None
    plan = plan_reproduction(log, entry=selected, include_slow=include_slow)
    project = resolve_project_root(log.root)
    run_id = _new_run_id()
    run_root = _new_run_root(project, log, entry, run_id)
    lock_fds = _acquire_scope_locks(log, entry)
    try:
        run_root.mkdir(parents=True)
        accepted = _accepted_record(log, plan, run_id, run_root, project)
        atomic_write_text(run_root / "run.json", _canonical(accepted))
        _spawn_supervisor(log, run_root, lock_fds, resume=False)
    except BaseException:
        _close_fds(lock_fds)
        raise
    _close_fds(lock_fds)
    return run_id


def dry_run_reproduction(
    log: LogContext, *, entry: str | None, include_slow: bool
) -> ReproductionPlan:
    """Return one stable, write-free plan after the runtime safety preflight."""

    selected = resolve_entry(log, entry) if entry is not None else None
    plan = plan_reproduction(log, entry=selected, include_slow=include_slow)
    preflight_execution_safety()
    verify_reproduction_snapshot(log, plan)
    return plan


def reproduction_status(
    log: LogContext, run_id: str, *, reconcile: bool = True
) -> Mapping[str, object]:
    """Return the frozen deterministic status projection for one run."""

    root = _find_run(log, run_id)
    if reconcile:
        _reconcile_lost_supervisor(log, root)
    record = _load_run(root / "run.json")
    return _status_projection(record)


def format_reproduction_status(status: Mapping[str, object]) -> str:
    """Compose concise human status without hiding failures."""

    state = status.get("status") or status.get("phase")
    progress = f"{status['completed_executions']}/{status['total_executions']}"
    lines = [f"Run {status['run_id']}: {state} ({progress} executions)"]
    current = status.get("current_execution")
    if current is not None:
        lines.append(f"Current execution: {current}")
    failure = status.get("latest_failure")
    if isinstance(failure, Mapping):
        lines.append(f"Latest failure: {failure['code']}: {failure['message']}")
    return "\n".join(lines) + "\n"


def stop_reproduction(log: LogContext, run_id: str) -> Mapping[str, object]:
    """Request bounded worker-tree shutdown and wait for a stable result."""

    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root)
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
    """Reacquire the immutable scope and resume one stopped run in place."""

    root = _find_run(log, run_id)
    _reconcile_lost_supervisor(log, root)
    record = _load_run(root / "run.json")
    state = cast(Mapping[str, object], record["state"])
    if state["status"] != "stopped":
        raise ActionError(
            "reproduction.resume.invalid_state", "only a stopped run can resume"
        )
    target = cast(Mapping[str, object], record["target"])
    entry = cast(str | None, target["entry"])
    lock_fds = _acquire_scope_locks(log, entry)
    try:
        verify_reproduction_snapshot(log, _plan_from_record(record))
        (root / "stop.request").unlink(missing_ok=True)
        now = _utc_now()
        updated = _load_run(root / "run.json")
        cast(dict[str, object], updated["state"]).update(
            {"status": None, "phase": "accepted", "current_execution": None}
        )
        timestamps = cast(dict[str, object], updated["timestamps"])
        timestamps["resumed_at"] = now
        timestamps["stopped_at"] = None
        timestamps["updated_at"] = now
        _write_run(root, updated)
        _spawn_supervisor(log, root, lock_fds, resume=True)
    except BaseException:
        _close_fds(lock_fds)
        raise
    _close_fds(lock_fds)
    return run_id


def supervise_reproduction(
    log: LogContext,
    run_root: Path,
    *,
    resume: bool,
    inherited_locks: Sequence[int],
    confinement: Any = None,
) -> None:
    """Run one accepted job to a terminal state while retaining its locks."""

    del inherited_locks  # descriptors remain open until this process exits
    record = _load_run(run_root / "run.json")
    plan = _plan_from_record(record)
    run_id = cast(str, record["run_id"])
    try:
        _transition(log, run_root, phase="preflight", started=True)
        preflight_execution_safety(confinement)
        workspace = (
            open_existing_workspace(resolve_project_root(log.root), run_root, run_id)
            if resume
            else populate_disposable_copy(
                resolve_project_root(log.root), run_root, run_id
            )
        )
        _transition(log, run_root, phase="executing")
        batch = execute_reproduction_plan(
            log,
            plan,
            workspace,
            ExecutionControl(
                resume=resume,
                stop_requested=lambda: (run_root / "stop.request").exists(),
                confinement=confinement,
                progress=lambda event, identity, attempt: _execution_progress(
                    log, run_root, event, identity, attempt
                ),
            ),
        )
        if batch.stopped:
            _finish_stopped(log, run_root, batch.attempts)
            return
        _transition(log, run_root, phase="comparing", current_execution=None)
        complete = completed_execution_attempts(log, plan, workspace)
        partial = tuple(
            attempt
            for attempt in batch.attempts
            if attempt.checkpoint.state != "complete"
        )
        comparisons = tuple(
            compare_execution_outputs(log, plan, workspace, attempt)
            for attempt in (*complete, *partial)
        )
        _transition(log, run_root, phase="publishing")
        current = _load_run(run_root / "run.json")
        accepted_at = cast(Mapping[str, str | None], current["timestamps"])[
            "accepted_at"
        ]
        finished = _utc_now()
        published = publish_completed_reproduction(
            log,
            CompletedPublication(
                plan,
                comparisons,
                run_id,
                cast(str, accepted_at),
                finished,
                run_root,
                batch.dependency_skips,
            ),
        )
        run = next(item for item in published.results.runs if item.run_id == run_id)
        _finish_complete(log, run_root, run.artifact_outcomes, finished)
    except BaseException as error:
        _finish_failed(log, run_root, error)


def supervisor_main(arguments: Sequence[str]) -> int:
    """Internal detached-supervisor process entrypoint."""

    if len(arguments) != 4:
        return 2
    summary, run_root, raw_fds, raw_resume = arguments
    fds = tuple(int(value) for value in raw_fds.split(",") if value)
    try:
        log = resolve_log(Path(summary).with_suffix(""))
        supervise_reproduction(
            log,
            Path(run_root),
            resume=raw_resume == "1",
            inherited_locks=fds,
        )
    finally:
        _close_fds(fds)
    return 0


def _spawn_supervisor(
    log: LogContext, run_root: Path, lock_fds: Sequence[int], *, resume: bool
) -> None:
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
                "1" if resume else "0",
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
                raise OperationLockError(
                    f"research-log operation is active: {directory / name}"
                ) from error
            os.set_inheritable(descriptor, True)
            opened.append(descriptor)
    except BaseException:
        _close_fds(opened)
        raise
    return tuple(opened)


@contextmanager
def _run_state_lock(log: LogContext, run_id: str) -> Iterator[None]:
    digest = run_id.removeprefix("reproduce-")
    with operation_lock(log.root, f"reproduction-state-{digest}.lock"):
        yield


def _execution_progress(
    log: LogContext,
    run_root: Path,
    event: str,
    identity: str,
    attempt: ExecutionAttempt | None,
) -> None:
    with _run_state_lock(log, cast(str, _load_run(run_root / "run.json")["run_id"])):
        record = _load_run(run_root / "run.json")
        state = cast(dict[str, object], record["state"])
        progress = cast(dict[str, object], record["progress"])
        if event == "started":
            state["current_execution"] = identity
        else:
            state["current_execution"] = None
            progress["completed_executions"] = (
                cast(int, progress["completed_executions"]) + 1
            )
        if attempt is not None:
            record["workers"] = [item.as_dict() for item in attempt.workers]
            record["checkpoints"] = _checkpoint_dicts(run_root)
            if attempt.failure_code is not None:
                state["latest_failure"] = _failure(
                    attempt.failure_code,
                    attempt.failure_message or "Execution failed.",
                    identity,
                )
        _stamp(record)
        _write_run(run_root, record)


def _transition(
    log: LogContext,
    run_root: Path,
    *,
    phase: str,
    current_execution: str | None | object = ...,
    started: bool = False,
) -> None:
    record = _load_run(run_root / "run.json")
    run_id = cast(str, record["run_id"])
    with _run_state_lock(log, run_id):
        record = _load_run(run_root / "run.json")
        state = cast(dict[str, object], record["state"])
        state["phase"] = phase
        if current_execution is not ...:
            state["current_execution"] = current_execution
        timestamps = cast(dict[str, object], record["timestamps"])
        if started and timestamps["started_at"] is None:
            timestamps["started_at"] = _utc_now()
        _stamp(record)
        _write_run(run_root, record)


def _finish_stopped(
    log: LogContext, run_root: Path, attempts: Sequence[ExecutionAttempt]
) -> None:
    latest = attempts[-1] if attempts else None
    survivors = (
        [item.as_dict() for item in latest.workers if item.state == "running"]
        if latest is not None
        else []
    )
    if survivors:
        assert latest is not None
        _mark_stopping(log, run_root, survivors, latest)
        _wait_for_cleanup_retry(log, run_root)
        return
    record = _load_run(run_root / "run.json")
    with _run_state_lock(log, cast(str, record["run_id"])):
        record = _load_run(run_root / "run.json")
        now = _utc_now()
        state = cast(dict[str, object], record["state"])
        state.update({"status": "stopped", "phase": None, "current_execution": None})
        state["latest_failure"] = _failure(
            latest.failure_code if latest else "stop_requested",
            latest.failure_message
            if latest
            else "Reproduction was stopped by request.",
            latest.execution_id if latest else None,
            now=now,
        )
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"stopped_at": now, "updated_at": now})
        record["workers"] = (
            [item.as_dict() for item in latest.workers] if latest else []
        )
        record["checkpoints"] = _checkpoint_dicts(run_root)
        _write_run(run_root, record)


def _mark_stopping(
    log: LogContext,
    run_root: Path,
    survivors: Sequence[Mapping[str, object]],
    latest: ExecutionAttempt,
) -> None:
    record = _load_run(run_root / "run.json")
    with _run_state_lock(log, cast(str, record["run_id"])):
        record = _load_run(run_root / "run.json")
        state = cast(dict[str, object], record["state"])
        state.update({"status": None, "phase": "stopping", "current_execution": None})
        state["latest_failure"] = _failure(
            "worker_cleanup_incomplete",
            latest.failure_message or "One or more workers survived shutdown.",
            latest.execution_id,
        )
        record["workers"] = list(survivors)
        _stamp(record)
        _write_run(run_root, record)


def _wait_for_cleanup_retry(log: LogContext, run_root: Path) -> None:
    request = run_root / "stop.request"
    observed = request.stat().st_mtime_ns if request.exists() else 0
    while True:
        time.sleep(STATUS_POLL_SECONDS)
        current = request.stat().st_mtime_ns if request.exists() else 0
        if current == observed:
            continue
        observed = current
        survivors = _terminate_marked_workers(
            cast(str, _load_run(run_root / "run.json")["run_id"])
        )
        if survivors:
            continue
        _finish_stopped(log, run_root, ())
        return


def _finish_complete(
    log: LogContext,
    run_root: Path,
    counts: Mapping[str, int],
    finished: str,
) -> None:
    record = _load_run(run_root / "run.json")
    with _run_state_lock(log, cast(str, record["run_id"])):
        record = _load_run(run_root / "run.json")
        state = cast(dict[str, object], record["state"])
        state.update({"status": "complete", "phase": None, "current_execution": None})
        cast(dict[str, object], record["progress"])["artifact_outcomes"] = dict(counts)
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"finished_at": finished, "updated_at": finished})
        record["checkpoints"] = _checkpoint_dicts(run_root)
        _write_run(run_root, record)


def _finish_failed(log: LogContext, run_root: Path, error: BaseException) -> None:
    if not (run_root / "run.json").is_file():
        return
    record = _load_run(run_root / "run.json")
    with _run_state_lock(log, cast(str, record["run_id"])):
        record = _load_run(run_root / "run.json")
        now = _utc_now()
        state = cast(dict[str, object], record["state"])
        state.update({"status": "failed", "phase": None, "current_execution": None})
        state["latest_failure"] = _failure(
            cast(str, getattr(error, "code", "reproduction.job.failed")),
            str(error),
            None,
            now=now,
        )
        timestamps = cast(dict[str, object], record["timestamps"])
        timestamps.update({"finished_at": now, "updated_at": now})
        record["checkpoints"] = _checkpoint_dicts(run_root)
        _write_run(run_root, record)


def _reconcile_lost_supervisor(log: LogContext, run_root: Path) -> None:
    record = _load_run(run_root / "run.json")
    if cast(Mapping[str, object], record["state"])["status"] is not None:
        return
    pid = _supervisor_pid(run_root)
    if pid is not None and _pid_alive(pid):
        return
    survivors = _terminate_marked_workers(cast(str, record["run_id"]))
    with _run_state_lock(log, cast(str, record["run_id"])):
        record = _load_run(run_root / "run.json")
        state = cast(dict[str, object], record["state"])
        if state["status"] is not None:
            return
        now = _utc_now()
        state["current_execution"] = None
        state["latest_failure"] = _failure(
            "supervisor_lost", "The durable supervisor was interrupted.", None, now=now
        )
        if survivors:
            state["phase"] = "stopping"
            record["workers"] = survivors
        else:
            state.update({"status": "stopped", "phase": None})
            cast(dict[str, object], record["timestamps"])["stopped_at"] = now
        cast(dict[str, object], record["timestamps"])["updated_at"] = now
        _write_run(run_root, record)


def _terminate_marked_workers(run_id: str) -> list[Mapping[str, object]]:
    found: list[psutil.Process] = []
    try:
        for process in psutil.process_iter(["pid"]):
            try:
                if process.environ().get("RESEARCH_LOG_REPRODUCTION_RUN_ID") == run_id:
                    found.append(process)
            except psutil.Error:
                continue
    except (OSError, psutil.Error) as error:
        raise ActionError(
            "reproduction.worker.inspection_unavailable", str(error)
        ) from error
    for process in found:
        try:
            process.kill()
        except psutil.Error:
            pass
    _, live = psutil.wait_procs(found, timeout=10.0)
    now = _utc_now()
    return [
        {
            "worker_id": f"worker-{process.pid}",
            "parent_worker_id": None,
            "pid": process.pid,
            "execution_id": None,
            "state": "running",
            "registered_at": now,
            "last_observed_at": now,
        }
        for process in sorted(live, key=lambda item: item.pid)
    ]


def _accepted_record(
    log: LogContext,
    plan: ReproductionPlan,
    run_id: str,
    run_root: Path,
    project: Path,
) -> dict[str, object]:
    now = _utc_now()
    plan_value = plan.as_dict()
    plan_value.pop("schema")
    return {
        "checkpoints": [],
        "include_slow": plan.include_slow,
        "paths": {
            "diagnostics": "diagnostics",
            "run": run_root.relative_to(project).as_posix(),
            "staging": "executions",
            "working_copy": "worktree",
        },
        "plan": plan_value,
        "progress": {
            "artifact_outcomes": {name: 0 for name in OUTCOMES},
            "completed_executions": 0,
            "total_executions": len(plan.executions),
        },
        "run_id": run_id,
        "schema": RUN_SCHEMA,
        "source_snapshot": dict(plan.source_snapshot),
        "state": {
            "current_execution": None,
            "latest_failure": None,
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
    value = {"schema": PLAN_SCHEMA, **cast(Mapping[str, object], record["plan"])}
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
        "include_slow",
        "schema",
        "source_snapshot",
        "summary",
        "target",
        "validation_snapshot",
    }
    if set(value) != fields or value["schema"] != PLAN_SCHEMA:
        raise ActionError("reproduction.run.invalid", "stored plan fields are invalid")
    plan = ReproductionPlan(
        cast(str, value["summary"]),
        cast(Mapping[str, object], value["target"]),
        cast(bool, value["include_slow"]),
        cast(Mapping[str, object], value["validation_snapshot"]),
        cast(Mapping[str, object], value["source_snapshot"]),
        tuple(cast(Sequence[Mapping[str, object]], value["cases"])),
        tuple(cast(Sequence[Mapping[str, object]], value["executions"])),
        tuple(cast(Sequence[Mapping[str, object]], value["boundaries"])),
        tuple(cast(Sequence[Mapping[str, object]], value["failures"])),
    )
    if plan.as_dict() != value:
        raise ActionError("reproduction.run.invalid", "stored plan is not canonical")
    return plan


def _status_projection(record: Mapping[str, object]) -> Mapping[str, object]:
    state = cast(Mapping[str, object], record["state"])
    progress = cast(Mapping[str, object], record["progress"])
    workers = cast(Sequence[Mapping[str, object]], record["workers"])
    return {
        "artifact_outcomes": progress["artifact_outcomes"],
        "completed_executions": progress["completed_executions"],
        "current_execution": state["current_execution"],
        "include_slow": record["include_slow"],
        "latest_failure": state["latest_failure"],
        "phase": state["phase"],
        "run_id": record["run_id"],
        "schema": STATUS_SCHEMA,
        "status": state["status"],
        "summary": record["summary"],
        "surviving_workers": [
            {
                name: item[name]
                for name in (
                    "execution_id",
                    "last_observed_at",
                    "parent_worker_id",
                    "pid",
                    "registered_at",
                    "state",
                    "worker_id",
                )
            }
            for item in workers
            if item.get("state") == "running"
        ],
        "target": record["target"],
        "timestamps": record["timestamps"],
        "total_executions": progress["total_executions"],
    }


def _load_run(path: Path) -> dict[str, object]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_RUN_RECORD_BYTES
    ):
        raise ActionError("reproduction.run.invalid", f"invalid run record: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ActionError("reproduction.run.invalid", str(error)) from error
    fields = {
        "checkpoints",
        "include_slow",
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
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != RUN_SCHEMA
    ):
        raise ActionError("reproduction.run.invalid", "run record fields are invalid")
    if RUN_ID_RE.fullmatch(str(value.get("run_id"))) is None:
        raise ActionError("reproduction.run.invalid", "run ID is invalid")
    state = value.get("state")
    if not isinstance(state, dict) or set(state) != {
        "current_execution",
        "latest_failure",
        "phase",
        "status",
    }:
        raise ActionError("reproduction.run.invalid", "run state is invalid")
    status = state["status"]
    phase = state["phase"]
    if status not in {*TERMINAL_STATUSES, None} or phase not in {*ACTIVE_PHASES, None}:
        raise ActionError("reproduction.run.invalid", "run lifecycle is invalid")
    if (status is None) == (phase is None):
        raise ActionError("reproduction.run.invalid", "run lifecycle is incoherent")
    _validate_run_members(value)
    _plan_from_record(value)
    canonical = _canonical(value)
    if path.read_text(encoding="utf-8") != canonical:
        raise ActionError("reproduction.run.invalid", "run record is not canonical")
    return cast(dict[str, object], value)


def _validate_run_members(value: Mapping[str, object]) -> None:
    target = value.get("target")
    if not isinstance(target, Mapping) or set(target) != {"entry", "kind"}:
        raise ActionError("reproduction.run.invalid", "run target is invalid")
    if target.get("kind") == "entry":
        if not isinstance(target.get("entry"), str):
            raise ActionError("reproduction.run.invalid", "entry target is invalid")
    elif target != {"entry": None, "kind": "log"}:
        raise ActionError("reproduction.run.invalid", "log target is invalid")
    if not isinstance(value.get("include_slow"), bool):
        raise ActionError("reproduction.run.invalid", "slow policy is invalid")
    _validate_progress(value.get("progress"))
    _validate_timestamps(value.get("timestamps"))
    _validate_paths(value.get("paths"))
    state = cast(Mapping[str, object], value["state"])
    current = state.get("current_execution")
    if current is not None and (
        not isinstance(current, str) or EXECUTION_ID_RE.fullmatch(current) is None
    ):
        raise ActionError("reproduction.run.invalid", "current execution is invalid")
    _validate_failure(state.get("latest_failure"))
    _validate_workers(value.get("workers"))
    _validate_checkpoints(value.get("checkpoints"))


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


def _validate_paths(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "diagnostics",
        "run",
        "staging",
        "working_copy",
    }:
        raise ActionError("reproduction.run.invalid", "run paths are invalid")
    if (
        value.get("diagnostics") != "diagnostics"
        or value.get("staging") != "executions"
        or value.get("working_copy") != "worktree"
    ):
        raise ActionError("reproduction.run.invalid", "run paths are invalid")


def _validate_failure(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) != {
        "code",
        "execution_id",
        "message",
        "recorded_at",
    }:
        raise ActionError("reproduction.run.invalid", "latest failure is invalid")
    if not all(
        isinstance(value.get(name), str) and value.get(name)
        for name in ("code", "message")
    ):
        raise ActionError("reproduction.run.invalid", "latest failure is invalid")
    execution = value.get("execution_id")
    if execution is not None and (
        not isinstance(execution, str) or EXECUTION_ID_RE.fullmatch(execution) is None
    ):
        raise ActionError("reproduction.run.invalid", "failure execution is invalid")
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
    if any(not isinstance(item, Mapping) or set(item) != fields for item in value):
        raise ActionError("reproduction.run.invalid", "worker record is invalid")


def _validate_checkpoints(value: object) -> None:
    if not isinstance(value, list) or len(value) > 100_000:
        raise ActionError("reproduction.run.invalid", "checkpoint list is invalid")
    fields = {"completed_at", "execution_id", "outputs", "path", "state"}
    if any(not isinstance(item, Mapping) or set(item) != fields for item in value):
        raise ActionError("reproduction.run.invalid", "checkpoint record is invalid")


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _write_run(root: Path, record: Mapping[str, object]) -> None:
    atomic_write_text(root / "run.json", _canonical(record))


def _find_run(log: LogContext, run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    tmp = resolve_project_root(log.root) / "tmp"
    if tmp.is_symlink() or not tmp.is_dir():
        raise ActionError("reproduction.run.missing", f"run not found: {run_id}")
    matches: list[Path] = []
    for index, candidate in enumerate(
        sorted(tmp.iterdir(), key=lambda item: item.name)
    ):
        if index >= MAX_RUN_DIRECTORIES:
            raise ActionError(
                "reproduction.run.resource_limit", "run scan limit exceeded"
            )
        if (
            not candidate.name.startswith("reproduce-")
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        path = candidate / "run.json"
        if path.is_file() and not path.is_symlink():
            record = _load_run(path)
            if record["run_id"] == run_id and record["summary"] == _summary_identity(
                log
            ):
                matches.append(candidate.resolve())
    if len(matches) != 1:
        raise ActionError(
            "reproduction.run.missing", f"expected one run, found {len(matches)}"
        )
    return matches[0]


def _new_run_root(
    project: Path, log: LogContext, entry: str | None, run_id: str
) -> Path:
    name = _safe_component(log.root.name)
    parts = ["reproduce", name]
    if entry is not None:
        parts.append(_safe_component(entry))
    parts.append(run_id)
    return project / "tmp" / "-".join(parts)


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"reproduce-{stamp}-{secrets.token_hex(6)}"


def _safe_component(value: str) -> str:
    selected = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not selected:
        raise ActionError("reproduction.run.path_invalid", "empty run path component")
    return selected[:64]


def _summary_identity(log: LogContext) -> str:
    return log.summary.resolve().relative_to(resolve_project_root(log.root)).as_posix()


def _checkpoint_dicts(run_root: Path) -> list[Mapping[str, object]]:
    root = run_root / "checkpoints"
    if not root.is_dir():
        return []
    values: list[Mapping[str, object]] = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


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
    *,
    now: str | None = None,
) -> Mapping[str, object]:
    return {
        "code": code or "reproduction.job.failed",
        "execution_id": execution_id,
        "message": message or "Reproduction failed.",
        "recorded_at": now or _utc_now(),
    }


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
