"""Fail-closed disposable execution for planned research-log recipes."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import (
    BinaryIO,
    Callable,
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
    cast,
)

import psutil
from research_log_data import (
    DataContractError,
    DataFile,
    Fingerprint,
    ResolvedInputToken,
    compose_directory_fingerprint,
    input_token_parts,
    observe_directory_tree,
    observe_file_content,
    observe_fingerprint,
    resolve_input_token,
)
from stream_capture import StreamCapture, StreamDestination
from validation.file_publication import install_path, sync_directory
from validation.output_bindings import OutputBindingError, project_output_bindings
from validation.pyrun_outputs import code_target_path, output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    script_target_path,
)

from .context import EntryContext, LogContext, resolve_entry, resolve_project_root
from .model import ActionError
from .reproduction_contract import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    AcceptedInvocation,
    ReproductionPlan,
    accepted_invocation,
    successful_checkpoint_state,
)
from .reproduction_job_storage import (
    CheckpointProjection as StoredCheckpointProjection,
)
from .reproduction_job_storage import ExecutionIdentity as StoredExecutionIdentity
from .reproduction_job_storage import WorkerRecord as StoredWorkerRecord
from .reproduction_paths import canonical_run_root

RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
RUNNER_MARKER = "RESEARCH_LOG_REPRODUCTION_RUN_ID"
_T = TypeVar("_T")
MAX_WORKERS_PER_EXECUTION = 1_024
MAX_WORKERS_PER_RUN = 4_096
POLL_SECONDS = 0.1
WORKER_SETTLE_SECONDS = 1.0
GRACEFUL_STOP_SECONDS = 30.0
FORCED_STOP_SECONDS = 10.0


@dataclass(frozen=True)
class ReproductionWorkspace:
    """One immutable mapping from retained paths to run-local output paths."""

    run_id: str
    run_root: Path
    source_project: Path
    work_project: Path
    runtime_root: Path
    diagnostics_root: Path
    staging_root: Path

    def map_source(self, path: Path) -> Path:
        """Map one lexical source-project path into the output workspace."""

        source = path.absolute()
        try:
            relative = source.relative_to(self.source_project)
        except ValueError:
            try:
                relative = source.resolve().relative_to(self.source_project)
            except ValueError:
                return source.resolve()
        return self.work_project / relative


@dataclass(frozen=True)
class WorkerRecord:
    """One observed member of a supervised execution process tree."""

    worker_id: str
    parent_worker_id: str | None
    pid: int
    execution_id: str
    state: str
    registered_at: str
    last_observed_at: str
    entry: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "entry": self.entry,
            "execution_id": self.execution_id,
            "last_observed_at": self.last_observed_at,
            "parent_worker_id": self.parent_worker_id,
            "pid": self.pid,
            "registered_at": self.registered_at,
            "state": self.state,
            "worker_id": self.worker_id,
        }


@dataclass(frozen=True)
class ExecutionCheckpoint:
    """Durable state for one execution attempt in its unchanged run path."""

    entry: str
    cid: str
    execution_id: str
    state: str
    path: str
    completed_at: str | None
    outputs: tuple[Mapping[str, object], ...]
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None
    failure: Mapping[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "entry": self.entry,
            "cid": self.cid,
            "execution_id": self.execution_id,
            "finished_at": self.finished_at,
            "failure": dict(self.failure) if self.failure is not None else None,
            "outputs": [dict(value) for value in self.outputs],
            "path": self.path,
            "state": self.state,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class ExecutionAttempt:
    """The complete internal result of one bounded recipe attempt."""

    entry: str
    cid: str
    execution_id: str
    returncode: int | None
    stopped: bool
    failure_code: str | None
    failure_message: str | None
    checkpoint: ExecutionCheckpoint
    workers: tuple[WorkerRecord, ...]
    stdout: str
    stderr: str


@dataclass(frozen=True)
class IsolatedExecutionResult:
    """In-memory execution result for retained, non-job command verification."""

    attempt: ExecutionAttempt
    output_paths: Mapping[str, Path]


@dataclass(frozen=True)
class ExecutionBatch:
    """Ordered execution attempts plus mechanically blocked descendants."""

    attempts: tuple[ExecutionAttempt, ...]
    reused: tuple[str, ...]
    dependency_skips: tuple[Mapping[str, object], ...]
    stopped: bool


@dataclass
class _BatchSchedule:
    pending: list[Mapping[str, object]]
    running: dict[Future[ExecutionAttempt | None], Mapping[str, object]]
    attempts: dict[str, ExecutionAttempt]
    reused: list[str]
    skips: list[Mapping[str, object]]
    unavailable: set[str]
    complete: set[str]
    stopped: bool = False


@dataclass(frozen=True)
class CurrentExecutionControl:
    """Runtime controls for one SQLite-owned fixed-plan invocation."""

    permit_id: str
    resume: bool = False
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None


@dataclass(frozen=True)
class CurrentPlanControl:
    """Supervisor-owned controls for the complete current-format execution stage."""

    supervisor_pid: int
    resume: bool = False
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None


class ReproductionControlPlaneError(ActionError):
    """A callback or durable-state write failed outside research execution."""

    def __init__(self, error: BaseException, *, cleanup_incomplete: bool = False):
        if isinstance(error, ReproductionControlPlaneError):
            code = error.code
        else:
            code = cast(str, getattr(error, "code", "reproduction.job.failed"))
        message = str(error) or type(error).__name__
        super().__init__(code, message)
        self.cleanup_incomplete = cleanup_incomplete


@dataclass(frozen=True)
class _PreparedExecution:
    entry: str
    cid: str
    execution_id: str
    execution: PyrunExecution
    run_root: Path
    runtime_root: Path
    diagnostics_root: Path
    work_entry: Path
    output_paths: Mapping[str, Path]
    command: tuple[str, ...]
    environment: Mapping[str, str]
    captures: Mapping[str, Path]
    stdout: Path
    stderr: Path


@dataclass(frozen=True)
class _ExecutionSource:
    """One entry and its immutable accepted invocations for a reproduction pass."""

    entry: EntryContext
    invocations: Mapping[tuple[str, str], AcceptedInvocation]

    def invocation(self, cid: str, execution_id: str) -> AcceptedInvocation:
        """Return the sole execution admitted for this fixed run."""

        try:
            return self.invocations[(cid, execution_id)]
        except KeyError as error:
            raise ActionError(
                "reproduction.execution.missing",
                "execution is absent from the accepted plan: "
                f"{self.entry.id}:{cid}:{execution_id}",
            ) from error


@dataclass(frozen=True)
class _PreparationOptions:
    source: _ExecutionSource | None = None


@dataclass(frozen=True)
class _CommandContext:
    """Accepted declarations and workspace paths for one recipe expansion."""

    data: DataFile | None
    source_entry: Path
    workspace: ReproductionWorkspace
    output_paths: Mapping[str, Path]
    generated: Mapping[Path, tuple[Path, str]]
    expected_inputs: Mapping[str, Fingerprint]


@dataclass(frozen=True)
class _CurrentPlanExecutionContext:
    log: LogContext
    plan: ReproductionPlan
    workspace: ReproductionWorkspace
    control: CurrentPlanControl
    backend: ConfinementBackend
    sources: dict[str, _ExecutionSource]
    generated: Mapping[Path, tuple[Path, str]]


@dataclass(frozen=True)
class _ProcessOutcome:
    returncode: int | None
    stopped: bool
    failure_code: str | None
    failure_message: str | None
    workers: tuple[WorkerRecord, ...]


@dataclass(frozen=True)
class _LaunchedProcess:
    process: subprocess.Popen[bytes]
    capture: StreamCapture


@dataclass(frozen=True)
class _CurrentPreparedInvocation:
    identity: StoredExecutionIdentity
    prior: StoredCheckpointProjection
    source: _ExecutionSource
    accepted: AcceptedInvocation
    generated: Mapping[Path, tuple[Path, str]]
    prepared: _PreparedExecution


@dataclass(frozen=True)
class _CurrentProcessResult:
    prepared: _PreparedExecution
    outcome: _ProcessOutcome
    active_elapsed: float
    scratch: Path
    started_at: str
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _RunCallbacks:
    stop_requested: Callable[[], bool]
    execution_timeout_seconds: int
    on_launch: Callable[[str], None]
    on_workers: Callable[[tuple[WorkerRecord, ...]], None]


class ConfinementBackend(Protocol):
    """Runtime control that confines one complete child process tree."""

    def preflight(self) -> None: ...

    def command(
        self,
        command: Sequence[str],
        *,
        writable_roots: Sequence[Path],
        readonly_paths: Sequence[tuple[Path, str]],
    ) -> list[str]: ...


class DarwinSeatbelt:
    """Fail-closed macOS Seatbelt confinement for reproduction workers."""

    executable = Path("/usr/bin/sandbox-exec")

    def preflight(self) -> None:
        if sys.platform != "darwin" or not self.executable.is_file():
            raise ActionError(
                "reproduction.safety.unavailable",
                "the required macOS reproduction confinement is unavailable",
            )

    def command(
        self,
        command: Sequence[str],
        *,
        writable_roots: Sequence[Path],
        readonly_paths: Sequence[tuple[Path, str]],
    ) -> list[str]:
        self.preflight()
        profile = _seatbelt_profile(writable_roots, readonly_paths)
        if not writable_roots:
            raise ActionError(
                "reproduction.safety.invalid", "confinement has no writable root"
            )
        profile_root = writable_roots[-1].resolve()
        digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
        profile_path = profile_root / f"seatbelt-{digest}.sb"
        if not profile_path.exists():
            temporary = profile_path.with_suffix(".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    handle.write(profile)
                    handle.flush()
                    os.fsync(handle.fileno())
                install_path(temporary, profile_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return [str(self.executable), "-f", str(profile_path), *command]


def preflight_execution_safety(
    backend: ConfinementBackend | None = None,
) -> None:
    """Fail unless the code-owned runtime confinement is available."""

    (backend or DarwinSeatbelt()).preflight()


def populate_current_output_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Populate one accepted SQLite run without legacy metadata directories."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    source = project_root.resolve()
    try:
        root = canonical_run_root(run_root, source, require_exists=True)
    except OSError as error:
        raise ActionError(
            "reproduction.run.path_invalid", "accepted run directory is invalid"
        ) from error
    allowed = {
        "state.sqlite",
        "state.sqlite-journal",
        "state.sqlite-shm",
        "state.sqlite-wal",
        "state.lock",
        "supervisor.log",
    }
    if any(path.name not in allowed for path in root.iterdir()):
        raise ActionError(
            "reproduction.run.path_invalid", "accepted run directory is not pristine"
        )
    return _populate_output_workspace(source, root, run_id, cleanup_root=False)


def _populate_output_workspace(
    source: Path,
    target_root: Path,
    run_id: str,
    *,
    cleanup_root: bool,
) -> ReproductionWorkspace:
    """Create an empty writable project-layout projection for generated files."""

    work = target_root / "workspace"
    try:
        work.mkdir()
        runtime = target_root / "runtime"
        diagnostics = target_root / "diagnostics"
        staging = target_root / "executions"
        for directory in (runtime, diagnostics, staging):
            directory.mkdir()
        sync_directory(target_root)
    except BaseException:
        if cleanup_root:
            shutil.rmtree(target_root, ignore_errors=True)
        else:
            for name in ("workspace", "runtime", "diagnostics", "executions"):
                path = target_root / name
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
        raise
    return ReproductionWorkspace(
        run_id,
        target_root,
        source,
        work,
        runtime,
        diagnostics,
        staging,
    )


def open_current_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Open the SQLite-format workspace without requiring legacy metadata paths."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    root = canonical_run_root(run_root, project_root.resolve(), require_exists=True)
    paths = {
        "workspace": root / "workspace",
        "runtime": root / "runtime",
        "diagnostics": root / "diagnostics",
        "executions": root / "executions",
    }
    if any(path.is_symlink() or not path.is_dir() for path in paths.values()):
        raise ActionError(
            "reproduction.workspace.invalid", "current run workspace is incomplete"
        )
    return ReproductionWorkspace(
        run_id,
        root,
        project_root.resolve(),
        paths["workspace"],
        paths["runtime"],
        paths["diagnostics"],
        paths["executions"],
    )


def execute_current_planned_recipe(
    log: LogContext,
    plan: ReproductionPlan,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    control: CurrentExecutionControl,
) -> ExecutionAttempt:
    """Execute one invocation whose mutable state is owned by ``state.sqlite``."""

    current = _prepare_current_invocation(log, plan, planned, workspace, control)
    result = _run_current_invocation(current, plan, workspace, control)
    return _finish_current_invocation(current, result, planned, workspace, control)


def _prepare_current_invocation(
    log: LogContext,
    plan: ReproductionPlan,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    control: CurrentExecutionControl,
) -> _CurrentPreparedInvocation:
    """Validate and prepare one already-permitted current invocation."""

    from .reproduction_job_storage import (
        ExecutionIdentity,
        load_execution_checkpoint,
    )

    entry_id = _required_string(planned, "entry")
    cid = _required_string(planned, "cid")
    execution_id = _required_string(planned, "execution_id")
    identity = ExecutionIdentity(entry_id, cid, execution_id)
    prior = load_execution_checkpoint(workspace.run_root, identity)
    if prior is None or prior.state != "active" or prior.permit_id != control.permit_id:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.checkpoint.invalid",
                "current execution has no matching attached permit",
            )
        )
    sources: dict[str, _ExecutionSource] = {}
    generated = _generated_output_paths(log, plan, workspace, sources=sources)
    source = _execution_source(log, plan, workspace, entry_id, sources)
    accepted = source.invocation(cid, execution_id)
    prepared = _prepare_execution(
        log,
        planned,
        workspace,
        generated,
        _PreparationOptions(source),
    )
    _verify_accepted_source_observations(prepared, source.entry.root, workspace)
    _preflight_output_paths(prepared.output_paths.values(), prepared.run_root)
    _clear_outputs(prepared.output_paths.values())
    return _CurrentPreparedInvocation(
        identity, prior, source, accepted, generated, prepared
    )


def _run_current_invocation(
    current: _CurrentPreparedInvocation,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    control: CurrentExecutionControl,
) -> _CurrentProcessResult:
    """Launch one child after durable scratch/start ownership is committed."""

    from .reproduction_job_storage import (
        ExecutionStart,
        record_execution_start,
        replace_execution_workers,
    )

    backend = control.confinement or DarwinSeatbelt()
    scratch = Path(tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp"))
    prepared = replace(
        current.prepared,
        environment={**current.prepared.environment, "TMPDIR": str(scratch)},
    )
    started_at = current.prior.started_at or _utc_now()
    stdout = prepared.stdout.relative_to(workspace.run_root).as_posix()
    stderr = prepared.stderr.relative_to(workspace.run_root).as_posix()
    try:
        command = _confined_command(
            backend, prepared.command, plan, workspace, prepared
        )
        record_execution_start(
            workspace.run_root,
            ExecutionStart(
                current.identity.entry,
                current.identity.cid,
                current.identity.execution_id,
                control.permit_id,
                _utc_now(),
                started_at,
                str(scratch),
                elapsed_seconds=current.prior.elapsed_seconds,
                stdout_path=stdout,
                stderr_path=stderr,
            ),
        )
    except BaseException as error:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ReproductionControlPlaneError(error) from error
    outcome, _launched_at, active_elapsed = _run_prepared(
        prepared,
        command,
        workspace,
        _RunCallbacks(
            control.stop_requested,
            control.execution_timeout_seconds,
            lambda _at: None,
            lambda workers: _control_plane_call(
                replace_execution_workers,
                workspace.run_root,
                current.identity,
                tuple(_stored_worker(item) for item in workers),
            ),
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
    try:
        shutil.rmtree(scratch)
    except OSError as error:
        raise ReproductionControlPlaneError(
            ActionError("scratch_cleanup_incomplete", str(error)),
            cleanup_incomplete=True,
        ) from error
    return _CurrentProcessResult(
        prepared, outcome, active_elapsed, scratch, started_at, stdout, stderr
    )


def _finish_current_invocation(
    current: _CurrentPreparedInvocation,
    result: _CurrentProcessResult,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    control: CurrentExecutionControl,
) -> ExecutionAttempt:
    """Commit terminal output, release its permit, and clear scratch ownership."""

    from .reproduction_job_storage import (
        CheckpointOutput as StoredCheckpointOutput,
    )
    from .reproduction_job_storage import (
        ExecutionTerminal,
        clear_execution_permit,
        clear_execution_scratch,
        load_scheduler_owner,
        record_execution_terminal,
    )
    from .reproduction_scheduler import SchedulerIdentity, reconcile_permit

    prepared = result.prepared
    outputs = _observe_available_outputs(prepared.output_paths, prepared.execution)
    state, failure_code, failure_message = _attempt_state(
        result.outcome, len(outputs), len(prepared.output_paths)
    )
    finished_at = None if state == "stopped" else _utc_now()
    elapsed = current.prior.elapsed_seconds + result.active_elapsed
    checkpoint = ExecutionCheckpoint(
        current.identity.entry,
        current.identity.cid,
        current.identity.execution_id,
        state,
        "state.sqlite",
        finished_at if successful_checkpoint_state(state) else None,
        outputs,
        result.started_at,
        finished_at,
        elapsed,
        (
            None
            if failure_code is None
            else {
                "code": failure_code,
                "message": failure_message or "Execution failed.",
                "recorded_at": finished_at or _utc_now(),
            }
        ),
    )
    if successful_checkpoint_state(state):
        try:
            _verify_accepted_source_observations(
                prepared, current.source.entry.root, workspace
            )
            _verify_accepted_input_observations(current.accepted, current.generated)
            _materialize_outputs(prepared, workspace, current.source)
        except (OSError, ValueError, ActionError) as error:
            failure_code = cast(
                str, getattr(error, "code", "output_materialization_failed")
            )
            failure_message = str(error)
            finished_at = _utc_now()
            checkpoint = replace(
                checkpoint,
                state="failed",
                completed_at=None,
                finished_at=finished_at,
                failure={
                    "code": failure_code,
                    "message": failure_message,
                    "recorded_at": finished_at,
                },
            )
    stored_workers = tuple(_stored_worker(item) for item in result.outcome.workers)
    record_execution_terminal(
        workspace.run_root,
        ExecutionTerminal(
            current.identity.entry,
            current.identity.cid,
            current.identity.execution_id,
            control.permit_id,
            cast(Literal["succeeded", "failed", "stopped"], checkpoint.state),
            _utc_now(),
            checkpoint.finished_at,
            elapsed,
            tuple(
                StoredCheckpointOutput(
                    cast(str, item["artifact"]),
                    cast(Mapping[str, object], item["fingerprint"]),
                )
                for item in checkpoint.outputs
            ),
            failure_code=(
                None if checkpoint.failure is None else str(checkpoint.failure["code"])
            ),
            failure_message=(
                None
                if checkpoint.failure is None
                else str(checkpoint.failure["message"])
            ),
            failure_recorded_at=(
                None
                if checkpoint.failure is None
                else str(checkpoint.failure["recorded_at"])
            ),
            workers=stored_workers,
        ),
    )
    scheduling = SchedulerIdentity(
        workspace.source_project,
        workspace.run_id,
        current.identity.entry,
        current.identity.cid,
        current.identity.execution_id,
        _execution_order(planned),
    )
    reconciliation = reconcile_permit(
        scheduling, load_scheduler_owner(workspace.run_root)
    )
    if reconciliation.clear_run_permit_id != control.permit_id:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.scheduler.reconciliation_required",
                "terminal permit was not released exactly",
            )
        )
    clear_execution_permit(
        workspace.run_root,
        current.identity,
        control.permit_id,
        cast(Literal["succeeded", "failed", "stopped"], checkpoint.state),
        _utc_now(),
    )
    clear_execution_scratch(workspace.run_root, current.identity, str(result.scratch))
    return ExecutionAttempt(
        current.identity.entry,
        current.identity.cid,
        current.identity.execution_id,
        result.outcome.returncode,
        result.outcome.stopped,
        failure_code,
        failure_message,
        checkpoint,
        result.outcome.workers,
        result.stdout,
        result.stderr,
    )


def _stored_worker(worker: WorkerRecord) -> StoredWorkerRecord:
    """Translate one runtime worker without leaking storage types publicly."""

    return StoredWorkerRecord(
        worker.worker_id,
        worker.parent_worker_id,
        worker.pid,
        cast(Literal["running", "exited"], worker.state),
        worker.registered_at,
        worker.last_observed_at,
    )


def execute_current_reproduction_plan(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    control: CurrentPlanControl,
) -> ExecutionBatch:
    """Execute one immutable plan using only SQLite scheduling and checkpoints."""

    ordered = sorted(plan.executions, key=_execution_order)
    _require_execution_order(ordered)
    context = _prepare_current_plan_context(log, plan, workspace, control)
    schedule = _recover_current_schedule(context, ordered)
    _run_current_schedule(context, schedule)
    return _current_execution_batch(schedule, ordered)


def _prepare_current_plan_context(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    control: CurrentPlanControl,
) -> _CurrentPlanExecutionContext:
    """Resolve immutable execution inputs once for the complete current plan."""

    sources: dict[str, _ExecutionSource] = {}
    generated = _generated_output_paths(log, plan, workspace, sources=sources)
    return _CurrentPlanExecutionContext(
        log,
        plan,
        workspace,
        control,
        control.confinement or DarwinSeatbelt(),
        sources,
        generated,
    )


def _recover_current_schedule(
    context: _CurrentPlanExecutionContext,
    ordered: Sequence[Mapping[str, object]],
) -> _BatchSchedule:
    """Rebuild the runnable schedule from identity-local durable checkpoints."""

    schedule = _BatchSchedule(list(ordered), {}, {}, [], [], set(), set())
    for planned in tuple(schedule.pending):
        _recover_current_planned_execution(context, schedule, planned)
    return schedule


def _recover_current_planned_execution(
    context: _CurrentPlanExecutionContext,
    schedule: _BatchSchedule,
    planned: Mapping[str, object],
) -> None:
    """Apply one checkpoint's exact recovery disposition to the schedule."""

    from .reproduction_job_storage import load_execution_checkpoint

    entry = _required_string(planned, "entry")
    cid = _required_string(planned, "cid")
    execution_id = _required_string(planned, "execution_id")
    source = _execution_source(
        context.log, context.plan, context.workspace, entry, context.sources
    )
    checkpoint = load_execution_checkpoint(
        context.workspace.run_root, StoredExecutionIdentity(entry, cid, execution_id)
    )
    reference = _execution_reference(entry, cid, execution_id)
    if checkpoint is None:
        return
    if checkpoint.state == "stopped":
        if not context.control.resume:
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.checkpoint.invalid",
                    "fresh execution cannot reuse a stopped checkpoint",
                )
            )
        _reset_current_stopped_workspace(context.workspace, entry, cid, execution_id)
        return
    if checkpoint.state == "active" and checkpoint.started_at is None:
        return
    if checkpoint.state not in {"succeeded", "failed"}:
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.checkpoint.recovery_required",
                f"active execution requires recovery: {reference}",
            )
        )
    if checkpoint.state == "succeeded":
        local = _runtime_checkpoint(checkpoint)
        if not _checkpoint_outputs_current(
            local, source, context.workspace, cid, execution_id
        ):
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.checkpoint.changed",
                    f"completed checkpoint is not reusable: {reference}",
                )
            )
        prepared = _prepare_execution(
            context.log,
            planned,
            context.workspace,
            context.generated,
            _PreparationOptions(source),
        )
        _materialize_outputs(prepared, context.workspace, source)
    else:
        schedule.unavailable.add(reference)
    schedule.complete.add(reference)
    schedule.reused.append(reference)
    schedule.pending.remove(planned)


def _run_current_schedule(
    context: _CurrentPlanExecutionContext, schedule: _BatchSchedule
) -> None:
    """Drain runnable identities while honoring durable and local stop requests."""

    with ThreadPoolExecutor(
        max_workers=context.plan.jobs, thread_name_prefix="reproduce-current"
    ) as pool:
        while schedule.pending or schedule.running:
            if _current_stop_requested(context):
                schedule.stopped = True
            progress = _resolve_current_pending(schedule, context.workspace.run_root)
            if not schedule.stopped:
                progress = (
                    _launch_current_ready(
                        schedule,
                        pool,
                        context.plan.jobs,
                        context.workspace.run_root,
                        lambda planned: _execute_current_scheduled_recipe(
                            context, planned
                        ),
                    )
                    or progress
                )
            if schedule.running:
                _collect_current_finished(schedule)
            elif schedule.stopped:
                break
            elif schedule.pending and not progress:
                time.sleep(POLL_SECONDS)


def _current_stop_requested(context: _CurrentPlanExecutionContext) -> bool:
    from .reproduction_job_storage import load_run_control

    state = load_run_control(context.workspace.run_root)
    return (
        context.control.stop_requested()
        or state.stop_requested_at is not None
        or state.phase == "stopping"
    )


def _current_execution_batch(
    schedule: _BatchSchedule, ordered: Sequence[Mapping[str, object]]
) -> ExecutionBatch:
    """Return attempts in accepted order without reconstructing skipped identities."""

    ordered_attempts = tuple(
        schedule.attempts[reference]
        for reference in (
            _execution_reference(
                _required_string(item, "entry"),
                _required_string(item, "cid"),
                _required_string(item, "execution_id"),
            )
            for item in ordered
        )
        if reference in schedule.attempts
    )
    return ExecutionBatch(
        ordered_attempts,
        tuple(schedule.reused),
        tuple(schedule.skips),
        schedule.stopped,
    )


def current_execution_attempts(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
) -> tuple[ExecutionAttempt, ...]:
    """Reconstruct comparison-ready terminal attempts from SQLite checkpoints."""

    from .reproduction_job_storage import ExecutionIdentity, load_execution_checkpoint

    results = []
    sources: dict[str, _ExecutionSource] = {}
    for planned in sorted(plan.executions, key=_execution_order):
        entry = _required_string(planned, "entry")
        cid = _required_string(planned, "cid")
        execution_id = _required_string(planned, "execution_id")
        checkpoint = load_execution_checkpoint(
            workspace.run_root, ExecutionIdentity(entry, cid, execution_id)
        )
        if checkpoint is None or checkpoint.state not in {"succeeded", "failed"}:
            continue
        runtime = _runtime_checkpoint(checkpoint)
        source = _execution_source(log, plan, workspace, entry, sources)
        if checkpoint.state == "succeeded" and not _checkpoint_outputs_current(
            runtime, source, workspace, cid, execution_id
        ):
            raise ActionError(
                "reproduction.checkpoint.changed",
                f"completed checkpoint is not current: {entry}:{execution_id}",
            )
        results.append(
            ExecutionAttempt(
                entry,
                cid,
                execution_id,
                0 if checkpoint.state == "succeeded" else None,
                False,
                checkpoint.failure_code,
                checkpoint.failure_message,
                runtime,
                (),
                checkpoint.stdout_path or "",
                checkpoint.stderr_path or "",
            )
        )
    return tuple(results)


def _runtime_checkpoint(checkpoint: object) -> ExecutionCheckpoint:
    from .reproduction_job_storage import CheckpointProjection

    if not isinstance(checkpoint, CheckpointProjection):
        raise ReproductionControlPlaneError(
            ActionError(
                "reproduction.checkpoint.invalid", "invalid checkpoint projection"
            )
        )
    failure = (
        None
        if checkpoint.failure_code is None
        else {
            "code": checkpoint.failure_code,
            "message": checkpoint.failure_message,
            "recorded_at": checkpoint.failure_recorded_at,
        }
    )
    return ExecutionCheckpoint(
        checkpoint.entry,
        checkpoint.cid,
        checkpoint.execution_id,
        checkpoint.state,
        "state.sqlite",
        checkpoint.finished_at if checkpoint.state == "succeeded" else None,
        tuple(
            {"artifact": item.artifact, "fingerprint": dict(item.fingerprint)}
            for item in checkpoint.outputs
        ),
        checkpoint.started_at,
        checkpoint.finished_at,
        checkpoint.elapsed_seconds,
        failure,
    )


def _reset_current_stopped_workspace(
    workspace: ReproductionWorkspace, entry: str, cid: str, execution_id: str
) -> None:
    """Discard only the stopped identity's private, non-authoritative bytes."""

    for path in (
        _attempt_root(workspace, entry, cid, execution_id),
        _attempt_runtime_root(workspace, entry, cid, execution_id),
    ):
        if path.is_symlink():
            raise ReproductionControlPlaneError(
                ActionError("reproduction.workspace.invalid", str(path))
            )
        if path.exists():
            shutil.rmtree(path)


def _resolve_current_pending(
    schedule: _BatchSchedule,
    run_root: Path,
) -> bool:
    from .reproduction_job_storage import ExecutionIdentity, load_execution_readiness

    progress = False
    for planned in tuple(schedule.pending):
        entry = _required_string(planned, "entry")
        cid = _required_string(planned, "cid")
        execution_id = _required_string(planned, "execution_id")
        readiness = load_execution_readiness(
            run_root, ExecutionIdentity(entry, cid, execution_id)
        )
        if readiness.disposition != "dependency_failed":
            continue
        dependencies = tuple(
            sorted(
                _execution_reference(item.entry, item.cid, item.execution_id)
                for item in readiness.failed_dependencies
            )
        )
        reference = _execution_reference(entry, cid, execution_id)
        schedule.skips.append(
            {
                "depends_on": list(dependencies),
                "entry": entry,
                "cid": cid,
                "execution_id": execution_id,
                "reason": "dependency_failed",
            }
        )
        schedule.unavailable.add(reference)
        schedule.complete.add(reference)
        schedule.pending.remove(planned)
        progress = True
    return progress


def _launch_current_ready(
    schedule: _BatchSchedule,
    pool: ThreadPoolExecutor,
    jobs: int,
    run_root: Path,
    run_one: Callable[[Mapping[str, object]], ExecutionAttempt | None],
) -> bool:
    from .reproduction_job_storage import ExecutionIdentity, load_execution_readiness

    slots = jobs - len(schedule.running)
    if slots <= 0:
        return False
    ready = []
    for planned in schedule.pending:
        identity = ExecutionIdentity(
            _required_string(planned, "entry"),
            _required_string(planned, "cid"),
            _required_string(planned, "execution_id"),
        )
        if load_execution_readiness(run_root, identity).disposition == "ready":
            ready.append(planned)
    exclusive = [item for item in ready if item.get("exclusive") is True]
    launchable = exclusive[:1] if exclusive else ready
    if exclusive and schedule.running:
        return False
    for planned in launchable[:slots]:
        schedule.running[pool.submit(run_one, planned)] = planned
        schedule.pending.remove(planned)
        if planned.get("exclusive") is True:
            break
    return bool(launchable[:slots])


def _execute_current_scheduled_recipe(
    context: _CurrentPlanExecutionContext,
    planned: Mapping[str, object],
) -> ExecutionAttempt | None:
    """Acquire one accepted permit, execute, and durably release it."""

    from .reproduction_job_storage import (
        ExecutionIdentity,
        load_accepted_scheduling,
        load_execution_checkpoint,
        load_run_control,
        load_scheduler_owner,
    )
    from .reproduction_scheduler import (
        SchedulerIdentity,
        SchedulerPermitRequest,
        _resolve_claim,
        _resolve_claims,
        poll_permit,
        reconcile_permit,
    )

    entry = _required_string(planned, "entry")
    cid = _required_string(planned, "cid")
    execution_id = _required_string(planned, "execution_id")
    identity = ExecutionIdentity(entry, cid, execution_id)
    workspace = context.workspace
    control = context.control
    accepted = load_accepted_scheduling(workspace.run_root, identity)
    scheduler_identity = SchedulerIdentity(
        workspace.source_project,
        workspace.run_id,
        entry,
        cid,
        execution_id,
        accepted.plan_order,
    )
    prior = load_execution_checkpoint(workspace.run_root, identity)
    expected_state: Literal["absent", "stopped"] = (
        "stopped" if prior is not None and prior.state == "stopped" else "absent"
    )
    request = SchedulerPermitRequest(
        scheduler_identity,
        accepted.kind,
        control.supervisor_pid,
        tuple(
            _resolve_claims(
                list(accepted.read_paths),
                workspace.run_root,
                workspace.source_project,
            )
        ),
        tuple(
            _resolve_claims(
                list(accepted.write_paths),
                workspace.run_root,
                workspace.source_project,
            )
        ),
        _resolve_claim(accepted.run_path, workspace.run_root, workspace.source_project),
        tuple(
            _resolve_claims(
                list(accepted.writable_paths),
                workspace.run_root,
                workspace.source_project,
            )
        ),
        _utc_now(),
    )
    while True:
        state = load_run_control(workspace.run_root)
        if (
            control.stop_requested()
            or state.stop_requested_at is not None
            or state.phase == "stopping"
        ):
            reconcile_permit(
                scheduler_identity, load_scheduler_owner(workspace.run_root)
            )
            return None
        request = replace(request, polled_at=_utc_now())
        decision = poll_permit(
            workspace.run_root,
            request,
            checkpointed_at=_utc_now(),
            expected_state=expected_state,
        )
        if decision.disposition == "granted":
            assert decision.permit is not None
            return execute_current_planned_recipe(
                context.log,
                context.plan,
                planned,
                workspace,
                CurrentExecutionControl(
                    decision.permit.permit_id,
                    resume=expected_state == "stopped",
                    execution_timeout_seconds=control.execution_timeout_seconds,
                    stop_requested=lambda: (
                        control.stop_requested()
                        or load_run_control(workspace.run_root).stop_requested_at
                        is not None
                    ),
                    confinement=context.backend,
                ),
            )
        time.sleep(POLL_SECONDS)


def _collect_current_finished(schedule: _BatchSchedule) -> None:
    """Fold one or more durable terminal current-format executions."""

    done, _ = wait(tuple(schedule.running), return_when=FIRST_COMPLETED)
    for future in done:
        schedule.running.pop(future)
        attempt = future.result()
        if attempt is None:
            schedule.stopped = True
            continue
        reference = _execution_reference(
            attempt.entry, attempt.cid, attempt.execution_id
        )
        schedule.attempts[reference] = attempt
        schedule.complete.add(reference)
        if attempt.stopped:
            schedule.stopped = True
        if not successful_checkpoint_state(attempt.checkpoint.state):
            schedule.unavailable.add(reference)


def execute_isolated_invocation(  # noqa: PLR0913
    entry: EntryContext,
    invocation: AcceptedInvocation,
    workspace_root: Path,
    *,
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    stop_requested: Callable[[], bool] = lambda: False,
    confinement: ConfinementBackend | None = None,
    input_observations: Mapping[str, Fingerprint] | None = None,
) -> IsolatedExecutionResult:
    """Run one current invocation in a retained isolated workspace.

    This adapter deliberately has no plan, checkpoint, staging, or publication
    ownership.  It shares command expansion, output binding, confinement,
    process monitoring, and bounded cleanup with planned execution.
    """

    root = workspace_root.resolve()
    workspace = _isolated_workspace(entry, root)
    invocation = _isolated_current_invocation(invocation, input_observations)
    output = workspace.work_project
    runtime = workspace.runtime_root
    diagnostics = workspace.diagnostics_root
    for path in (output, runtime, diagnostics):
        path.mkdir(parents=True, exist_ok=True)
    source = _ExecutionSource(
        entry, {(invocation.cid, invocation.execution_id): invocation}
    )
    prepared = _prepare_execution(
        entry.log,
        {
            "entry": entry.id,
            "cid": invocation.cid,
            "execution_id": invocation.execution_id,
        },
        workspace,
        {},
        _PreparationOptions(source),
    )
    _preflight_output_paths(prepared.output_paths.values(), prepared.run_root)
    _clear_outputs(prepared.output_paths.values())
    backend = confinement or DarwinSeatbelt()
    backend.preflight()
    scratch = Path(tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp"))
    prepared = replace(
        prepared, environment={**prepared.environment, "TMPDIR": str(scratch)}
    )
    try:
        command = backend.command(
            prepared.command,
            writable_roots=(
                scratch,
                prepared.run_root,
                prepared.runtime_root,
                prepared.diagnostics_root,
            ),
            readonly_paths=(),
        )
        outcome, started_at, elapsed = _run_prepared(
            prepared,
            command,
            workspace,
            _RunCallbacks(
                stop_requested,
                execution_timeout_seconds,
                lambda _at: None,
                lambda _workers: None,
            ),
        )
    finally:
        try:
            _clear_isolated_seatbelt_profiles(prepared.diagnostics_root)
        finally:
            try:
                if scratch.exists() or scratch.is_symlink():
                    shutil.rmtree(scratch)
            except OSError as error:
                raise ReproductionControlPlaneError(
                    ActionError("scratch_cleanup_incomplete", str(error)),
                    cleanup_incomplete=True,
                ) from error
    outputs = _observe_available_outputs(prepared.output_paths, prepared.execution)
    state, code, message = _attempt_state(
        outcome, len(outputs), len(prepared.output_paths)
    )
    finished = None if outcome.stopped or started_at is None else _utc_now()
    attempt = ExecutionAttempt(
        entry.id,
        invocation.cid,
        invocation.execution_id,
        outcome.returncode,
        outcome.stopped,
        code,
        message,
        ExecutionCheckpoint(
            entry.id,
            invocation.cid,
            invocation.execution_id,
            state,
            "",
            finished if state == "succeeded" else None,
            outputs,
            started_at,
            finished,
            elapsed,
            None,
        ),
        outcome.workers,
        str(prepared.stdout),
        str(prepared.stderr),
    )
    return IsolatedExecutionResult(attempt, prepared.output_paths)


def _clear_isolated_seatbelt_profiles(root: Path) -> None:
    """Remove transient confinement profiles after child cleanup completes."""

    for path in root.glob("seatbelt-*.sb"):
        if path.is_symlink() or not path.is_file():
            raise ActionError("command.verify.diagnostics.invalid", str(path))
        path.unlink()


def preflight_isolated_invocation(
    entry: EntryContext,
    invocation: AcceptedInvocation,
    workspace_root: Path,
    *,
    confinement: ConfinementBackend | None = None,
    input_observations: Mapping[str, Fingerprint] | None = None,
) -> None:
    """Validate isolated command, bindings, paths, and confinement before writes."""

    workspace = _isolated_workspace(entry, workspace_root.resolve())
    invocation = _isolated_current_invocation(invocation, input_observations)
    execution = invocation.execution
    relative_entry = entry.root.resolve().relative_to(workspace.source_project)
    attempt_root = _attempt_root(
        workspace, entry.id, invocation.cid, invocation.execution_id
    )
    output_paths = _output_paths(
        execution,
        entry_root=attempt_root / relative_entry,
        project_root=attempt_root,
    )
    _execution_command(
        execution,
        _CommandContext(
            (
                None
                if invocation.data is None
                else replace(invocation.data, entry_root=entry.root)
            ),
            entry.root,
            workspace,
            output_paths,
            {},
            dict(execution.observed.inputs),
        ),
    )
    _preflight_output_paths(output_paths.values(), workspace.work_project)
    (confinement or DarwinSeatbelt()).preflight()


def _isolated_workspace(entry: EntryContext, root: Path) -> ReproductionWorkspace:
    """Return the write-free workspace layout for one command verification."""

    return ReproductionWorkspace(
        "command-verification",
        root,
        resolve_project_root(entry.log.root),
        root / "outputs",
        root / "runtime",
        root / "diagnostics",
        root / "outputs",
    )


def _isolated_current_invocation(
    invocation: AcceptedInvocation,
    observations: Mapping[str, Fingerprint] | None,
) -> AcceptedInvocation:
    """Bind verification to current direct-input observations only."""

    if observations is None:
        return invocation
    execution = replace(
        invocation.execution,
        observed=replace(
            invocation.execution.observed,
            inputs=tuple(sorted(observations.items())),
        ),
    )
    return replace(invocation, execution=execution)


def _verify_accepted_source_observations(
    prepared: _PreparedExecution, entry_root: Path, workspace: ReproductionWorkspace
) -> None:
    """Require retained script and helper bytes to match this invocation's plan."""

    script = script_target_path(
        prepared.execution.recipe.script,
        entry_root=entry_root,
        project_root=workspace.source_project,
    )
    observed = prepared.execution.observed
    try:
        if script.is_symlink() or _fingerprint(script, "file") != observed.script:
            raise ValueError("script observation changed")
        for name, fingerprint in observed.code:
            helper = code_target_path(name, entry_root=entry_root)
            if helper.is_symlink() or _fingerprint(helper, "file") != fingerprint:
                raise ValueError(f"helper observation changed: {name}")
    except (OSError, ValueError) as error:
        raise ActionError("reproduction.source.changed", str(error)) from error


def _verify_accepted_input_observations(
    accepted: AcceptedInvocation, generated: Mapping[Path, tuple[Path, str]]
) -> None:
    """Require accepted direct inputs to remain stable after child execution."""

    if accepted.data is None:
        return
    expected = dict(accepted.execution.observed.inputs)
    for name in accepted.execution.recipe.inputs:
        resource = accepted.data.by_name.get(name)
        if resource is None:
            raise ActionError(
                "reproduction.input.invalid", f"accepted input is missing: {name}"
            )
        source = Path(resource.canonical_target)
        if _regenerated_input_path(source.resolve(), generated) is not None:
            continue
        try:
            observed = observe_fingerprint(resource).fingerprint
        except (DataContractError, OSError, ValueError) as error:
            raise ActionError("reproduction.input.unavailable", str(error)) from error
        if expected.get(name) != observed:
            raise ActionError(
                "reproduction.input.observation_mismatch",
                f"accepted input changed after execution: {name}",
            )


def _prepare_execution(
    log: LogContext,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    generated: Mapping[Path, tuple[Path, str]],
    options: _PreparationOptions = _PreparationOptions(),
) -> _PreparedExecution:
    entry_id = _required_string(planned, "entry")
    cid = _required_string(planned, "cid")
    execution_id = _required_string(planned, "execution_id")
    if options.source is None:
        raise ActionError(
            "reproduction.execution.invalid",
            "execution requires an accepted invocation source",
        )
    loaded = options.source
    source_entry = loaded.entry
    accepted = loaded.invocation(cid, execution_id)
    execution = accepted.execution
    attempt_root = _attempt_root(workspace, entry_id, cid, execution_id)
    runtime_root = _attempt_runtime_root(workspace, entry_id, cid, execution_id)
    diagnostics_root = (
        workspace.diagnostics_root / entry_id / cid / execution_id.rsplit(":", 1)[-1]
    )
    relative_entry = source_entry.root.resolve().relative_to(
        workspace.source_project.resolve()
    )
    work_entry = attempt_root / relative_entry
    work_entry.mkdir(parents=True, exist_ok=True)
    output_paths = _output_paths(
        execution,
        entry_root=work_entry,
        project_root=attempt_root,
    )
    command, captures = _execution_command(
        execution,
        _CommandContext(
            None
            if accepted.data is None
            else replace(accepted.data, entry_root=source_entry.root),
            source_entry.root,
            workspace,
            output_paths,
            generated,
            dict(execution.observed.inputs),
        ),
    )
    stdout_path, stderr_path = _diagnostic_paths(workspace, entry_id, cid, execution_id)
    return _PreparedExecution(
        entry_id,
        cid,
        execution_id,
        execution,
        attempt_root,
        runtime_root,
        diagnostics_root,
        work_entry,
        output_paths,
        tuple(command),
        _execution_environment(execution, workspace, entry_id, cid, execution_id),
        captures,
        stdout_path,
        stderr_path,
    )


def _confined_command(
    backend: ConfinementBackend,
    command: Sequence[str],
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    prepared: _PreparedExecution,
) -> list[str]:
    backend.preflight()
    readonly = _readonly_boundaries(plan, workspace)
    return backend.command(
        command,
        writable_roots=(
            Path(prepared.environment["TMPDIR"]),
            prepared.run_root,
            prepared.runtime_root,
            prepared.diagnostics_root,
        ),
        readonly_paths=readonly,
    )


def _run_prepared(
    prepared: _PreparedExecution,
    command: Sequence[str],
    workspace: ReproductionWorkspace,
    callbacks: _RunCallbacks,
) -> tuple[_ProcessOutcome, str | None, float]:
    registry = _WorkerRegistry(
        prepared.entry,
        prepared.execution_id,
        prepared.environment[RUNNER_MARKER],
    )
    started_at: str | None = None
    started_monotonic: float | None = None
    try:
        with ExitStack() as stack:
            launched = _launch_process(prepared, command, stack)
            started_at = _utc_now()
            started_monotonic = time.monotonic()
            deadline = started_monotonic + callbacks.execution_timeout_seconds
            _control_plane_call(callbacks.on_launch, started_at)
            _control_plane_call(registry.register_root, launched.process.pid)
            _control_plane_call(
                callbacks.on_workers, _control_plane_call(registry.records)
            )
            outcome = _monitor_process(
                launched,
                registry,
                callbacks,
                deadline,
            )
            failure_code, failure_message = _finish_streams(
                launched, outcome.failure_code, outcome.failure_message
            )
    except ReproductionControlPlaneError as error:
        try:
            survivors = _control_plane_call(registry.stop_all)
        except BaseException as cleanup_error:
            raise ReproductionControlPlaneError(
                cleanup_error, cleanup_incomplete=True
            ) from error
        if survivors:
            raise ReproductionControlPlaneError(
                ActionError("worker_cleanup_incomplete", _survivor_message(survivors)),
                cleanup_incomplete=True,
            ) from error
        raise
    except BaseException as error:
        survivors = _control_plane_call(registry.stop_all)
        return (
            _ProcessOutcome(
                None,
                bool(survivors),
                (
                    "worker_cleanup_incomplete"
                    if survivors
                    else cast(str, getattr(error, "code", "execution_exception"))
                ),
                _survivor_message(survivors) if survivors else str(error),
                _control_plane_call(registry.records),
            ),
            started_at,
            (
                0.0
                if started_monotonic is None
                else max(0.0, time.monotonic() - started_monotonic)
            ),
        )
    return (
        _ProcessOutcome(
            outcome.returncode,
            outcome.stopped,
            failure_code,
            failure_message,
            _control_plane_call(registry.records),
        ),
        started_at,
        max(0.0, time.monotonic() - started_monotonic),
    )


def _launch_process(
    prepared: _PreparedExecution,
    command: Sequence[str],
    stack: ExitStack,
) -> _LaunchedProcess:
    stdout = stack.enter_context(prepared.stdout.open("ab", buffering=0))
    stderr = stack.enter_context(prepared.stderr.open("ab", buffering=0))
    capture_handles: dict[str, BinaryIO] = {}
    for option, path in prepared.captures.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        capture_handles[option] = stack.enter_context(path.open("wb", buffering=0))
    combined = capture_handles.get("--capture-stdout-stderr")
    stdout_capture = capture_handles.get("--capture-stdout")
    stderr_capture = capture_handles.get("--capture-stderr")
    process = subprocess.Popen(
        command,
        cwd=prepared.work_entry,
        env=prepared.environment,
        stdin=subprocess.DEVNULL,
        stdout=(
            subprocess.PIPE
            if combined is not None or stdout_capture is not None
            else stdout
        ),
        stderr=(
            subprocess.STDOUT
            if combined is not None
            else subprocess.PIPE
            if stderr_capture is not None
            else stderr
        ),
        start_new_session=True,
    )
    capture = StreamCapture()
    _start_stream_pumps(
        process,
        stdout,
        stderr,
        capture_handles,
        capture,
    )
    stack.callback(capture.finish, FORCED_STOP_SECONDS)
    return _LaunchedProcess(process, capture)


def _start_stream_pumps(
    process: subprocess.Popen[bytes],
    stdout: BinaryIO,
    stderr: BinaryIO,
    captures: Mapping[str, BinaryIO],
    capture: StreamCapture,
) -> None:
    combined = captures.get("--capture-stdout-stderr")
    stdout_capture = captures.get("--capture-stdout")
    stderr_capture = captures.get("--capture-stderr")
    for source, destinations in (
        (process.stdout, (stdout, combined or stdout_capture)),
        (process.stderr, (stderr, stderr_capture)),
    ):
        if source is None:
            continue
        capture.start(
            source,
            tuple(
                StreamDestination(
                    "stdout diagnostics" if destination is stdout else
                    "stderr diagnostics" if destination is stderr else
                    "declared capture",
                    destination,
                    True,
                    durable=True,
                )
                for destination in destinations
                if destination is not None
            ),
        )


def _monitor_process(
    launched: _LaunchedProcess,
    registry: _WorkerRegistry,
    callbacks: _RunCallbacks,
    deadline: float,
) -> _ProcessOutcome:
    process = launched.process
    stopped = False
    failure_code: str | None = None
    failure_message: str | None = None
    while process.poll() is None:
        _control_plane_call(registry.refresh)
        _control_plane_call(callbacks.on_workers, _control_plane_call(registry.records))
        stop = _monitor_stop(launched, registry, callbacks, deadline)
        if stop is not None:
            stopped, failure_code, failure_message = stop
            break
        time.sleep(POLL_SECONDS)
    returncode = process.poll()
    if returncode is None:
        try:
            returncode = process.wait(timeout=FORCED_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            returncode = None
    _control_plane_call(registry.refresh)
    _control_plane_call(callbacks.on_workers, _control_plane_call(registry.records))
    if not stopped:
        survivors = _control_plane_call(
            registry.wait_for_descendants, WORKER_SETTLE_SECONDS
        )
        if survivors:
            remaining = _control_plane_call(registry.stop_all)
            failure_code = "worker_survived"
            failure_message = _survivor_message(remaining or survivors)
            stopped = bool(remaining)
    return _ProcessOutcome(
        returncode,
        stopped,
        failure_code,
        failure_message,
        _control_plane_call(registry.records),
    )


def _stop_after_capture_failure(
    registry: _WorkerRegistry,
) -> tuple[bool, str, str]:
    """Stop one worker tree after a required stream destination fails."""

    survivors = _control_plane_call(registry.stop_all)
    if survivors:
        return True, "worker_cleanup_incomplete", _survivor_message(survivors)
    return False, "capture_failed", "could not retain captured output"


def _monitor_stop(
    launched: _LaunchedProcess,
    registry: _WorkerRegistry,
    callbacks: _RunCallbacks,
    deadline: float,
) -> tuple[bool, str | None, str | None] | None:
    """Return one requested execution stop and its failure projection."""

    if launched.capture.required_failed.is_set():
        return _stop_after_capture_failure(registry)
    if callbacks.stop_requested():
        survivors = _control_plane_call(registry.stop_all)
        if survivors:
            return True, "worker_cleanup_incomplete", _survivor_message(survivors)
        return True, None, None
    if time.monotonic() < deadline:
        return None
    survivors = _control_plane_call(registry.stop_all)
    if survivors:
        return True, "worker_cleanup_incomplete", _survivor_message(survivors)
    return (
        False,
        "execution_timeout",
        "command exceeded the runtime limit of "
        f"{callbacks.execution_timeout_seconds} seconds",
    )


def _finish_streams(
    launched: _LaunchedProcess,
    failure_code: str | None,
    failure_message: str | None,
) -> tuple[str | None, str | None]:
    failures = launched.capture.finish(FORCED_STOP_SECONDS)
    required = [failure for failure in failures if failure.required]
    if required and failure_code is None:
        return (
            "capture_failed",
            f"could not retain captured output: {required[0].error}",
        )
    return failure_code, failure_message


def _attempt_state(
    outcome: _ProcessOutcome,
    observed_outputs: int,
    declared_outputs: int,
) -> tuple[str, str | None, str | None]:
    if outcome.stopped:
        return (
            "stopped",
            outcome.failure_code or "stop_requested",
            outcome.failure_message or "Reproduction was stopped by request.",
        )
    if outcome.failure_code is not None:
        return "failed", outcome.failure_code, outcome.failure_message
    if outcome.returncode != 0:
        return (
            "failed",
            "execution_failed",
            f"execution exited with status {outcome.returncode}",
        )
    if observed_outputs != declared_outputs:
        return (
            "failed",
            "output_missing",
            "one or more declared outputs were not generated",
        )
    return "succeeded", None, None


def _execution_order(planned: Mapping[str, object]) -> int:
    value = planned.get("order")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ActionError(
            "reproduction.plan.invalid", "planned execution has invalid order"
        )
    return value


def _require_execution_order(ordered: Sequence[Mapping[str, object]]) -> None:
    expected = list(range(1, len(ordered) + 1))
    observed = [_execution_order(value) for value in ordered]
    references = [
        _execution_reference(
            _required_string(value, "entry"),
            _required_string(value, "cid"),
            _required_string(value, "execution_id"),
        )
        for value in ordered
    ]
    if observed != expected or len(references) != len(set(references)):
        raise ActionError(
            "reproduction.plan.invalid", "execution order or identity is invalid"
        )


def _dependency_references(planned: Mapping[str, object]) -> tuple[str, ...]:
    value = planned.get("depends_on")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ActionError(
            "reproduction.plan.invalid", "execution dependencies are invalid"
        )
    result = tuple(cast(list[str], value))
    if result != tuple(sorted(set(result))):
        raise ActionError(
            "reproduction.plan.invalid", "execution dependencies are not canonical"
        )
    return result


def _execution_reference(entry: str, cid: str, execution_id: str) -> str:
    return f"{entry}:{cid}:{execution_id}"


def _checkpoint_outputs_current(
    checkpoint: ExecutionCheckpoint,
    source: _ExecutionSource,
    workspace: ReproductionWorkspace,
    cid: str,
    execution_id: str,
) -> bool:
    entry = source.entry
    try:
        execution = source.invocation(cid, execution_id).execution
    except ActionError:
        return False
    project_root = _attempt_root(workspace, entry.id, cid, execution_id)
    paths = _output_paths(
        execution,
        entry_root=project_root
        / entry.root.resolve().relative_to(workspace.source_project.resolve()),
        project_root=project_root,
    )
    expected = {
        cast(str, value["artifact"]): value["fingerprint"]
        for value in checkpoint.outputs
    }
    if set(expected) != set(paths):
        return False
    kinds = dict(execution.recipe.outputs)
    try:
        return all(
            not path.is_symlink()
            and path.exists()
            and _fingerprint(path, kinds[artifact]).as_dict() == expected[artifact]
            for artifact, path in paths.items()
        )
    except (OSError, ValueError):
        return False


def _execution_command(
    execution: PyrunExecution,
    context: _CommandContext,
) -> tuple[list[str], Mapping[str, Path]]:
    if context.data is None and execution.recipe.inputs:
        raise ActionError(
            "reproduction.input.unavailable",
            "accepted execution has no data declarations",
        )
    interpreter_link = context.workspace.source_project / ".conda" / "bin" / "python"
    interpreter = interpreter_link.resolve()
    if not interpreter.is_file():
        raise ActionError(
            "reproduction.environment.missing",
            f"project-local Python is unavailable: {interpreter_link}",
        )
    script = script_target_path(
        execution.recipe.script,
        entry_root=context.source_entry,
        project_root=context.workspace.source_project,
    )
    if script.is_symlink() or not script.is_file():
        raise ActionError(
            "reproduction.script.unavailable", f"retained script is missing: {script}"
        )
    try:
        projection = project_output_bindings(
            execution.recipe.parameters,
            execution.recipe.outputs,
            entry_root=context.source_entry,
            project_root=context.workspace.source_project,
            subject=execution.recipe.script,
        )
    except OutputBindingError as error:
        raise ActionError("reproduction.output.binding_invalid", str(error)) from error
    captures = {
        binding.option: context.output_paths[binding.output]
        for binding in projection.captures
        if binding.option is not None
    }
    bindings = {
        binding.parameter_index: binding.substituted(
            context.output_paths[binding.output]
        )
        for binding in projection.parameters
        if binding.parameter_index is not None
    }
    arguments = []
    for index, value in enumerate(projection.child_parameters):
        binding = bindings.get(index)
        if binding is not None:
            arguments.append(binding)
            continue
        arguments.append(_resolve_parameter(value, context))
    return [str(interpreter), str(script), *arguments], captures


def _execution_source(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    entry_id: str,
    sources: dict[str, _ExecutionSource],
) -> _ExecutionSource:
    source = sources.get(entry_id)
    if source is not None:
        return source
    entry = resolve_entry(log, entry_id)
    invocations = {
        (
            _required_string(command, "cid"),
            _required_string(command, "execution_id"),
        ): accepted_invocation(
            plan,
            entry_id,
            _required_string(command, "cid"),
            _required_string(command, "execution_id"),
        )
        for command in plan.commands
        if command.get("entry") == entry_id
    }
    source = _ExecutionSource(entry, invocations)
    sources[entry_id] = source
    return source


def _generated_output_paths(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    *,
    sources: dict[str, _ExecutionSource] | None = None,
) -> Mapping[Path, tuple[Path, str]]:
    """Map retained generated identities to run-local graph paths."""

    result: dict[Path, tuple[Path, str]] = {}
    loaded_sources = sources if sources is not None else {}
    for planned in plan.executions:
        entry_id = _required_string(planned, "entry")
        source = _execution_source(log, plan, workspace, entry_id, loaded_sources)
        entry = source.entry
        execution = source.invocation(
            _required_string(planned, "cid"), _required_string(planned, "execution_id")
        ).execution
        work_entry = workspace.map_source(entry.root)
        for identity, kind in execution.recipe.outputs:
            retained = output_target_path(
                identity,
                entry_root=entry.root,
                project_root=workspace.source_project,
            )
            generated = output_target_path(
                identity,
                entry_root=work_entry,
                project_root=workspace.work_project,
            )
            result[retained.resolve()] = (generated, kind)
    return result


def _resolve_parameter(value: str, context: _CommandContext) -> str:
    """Expand one accepted child argument from its fixed invocation context."""

    data = context.data
    value = value.replace("<project>", str(context.workspace.source_project))
    if data is not None:
        value = value.replace("<log>", str(data.entry_root.parent.parent))
    parts = input_token_parts(value)
    if parts is None:
        return value
    if data is None:
        raise ActionError(
            "reproduction.input.unavailable",
            "accepted execution has no data declarations",
        )
    assert data is not None
    try:
        resolved = resolve_input_token(value, data)
    except DataContractError as error:
        raise ActionError("reproduction.input.invalid", str(error)) from error
    if resolved.projection == "commit":
        return resolved.value
    source = Path(resolved.value)
    mapped = _regenerated_input_path(source.resolve(), context.generated)
    if mapped is not None:
        return _resolve_staged_input(resolved, mapped, context)
    return _resolve_retained_input(resolved, context)


def _expected_input(
    resolved: ResolvedInputToken, context: _CommandContext
) -> Fingerprint:
    """Return the recorded consumer observation for one resolved input."""

    expected = context.expected_inputs.get(resolved.resource.name)
    if expected is None:
        raise ActionError(
            "reproduction.input.observation_missing",
            f"recorded input observation is unavailable: {resolved.resource.name}",
        )
    return expected


def _resolve_retained_input(
    resolved: ResolvedInputToken, context: _CommandContext
) -> str:
    """Verify a retained source input before passing it to the child."""

    expected = _expected_input(resolved, context)
    try:
        observed = observe_fingerprint(resolved.resource).fingerprint
    except (DataContractError, OSError, ValueError) as error:
        raise ActionError(
            "reproduction.input.unavailable",
            f"accepted input is unavailable: {resolved.resource.name}: {error}",
        ) from error
    if observed != expected:
        raise ActionError(
            "reproduction.input.observation_mismatch",
            f"accepted input changed: {resolved.resource.name}",
        )
    return resolved.value


def _resolve_staged_input(
    resolved: ResolvedInputToken, mapped: Path, context: _CommandContext
) -> str:
    """Verify a durable staged producer output against the consumer baseline."""

    if not mapped.exists():
        raise ActionError(
            "reproduction.input.unavailable",
            f"regenerated input is unavailable: {resolved.resource.name}",
        )
    expected = _expected_input(resolved, context)
    staged_root = _regenerated_input_path(
        Path(resolved.resource.canonical_target).resolve(), context.generated
    )
    if staged_root is None:
        raise ActionError(
            "reproduction.input.observation_missing",
            f"recorded input observation is unavailable: {resolved.resource.name}",
        )
    try:
        observed = observe_fingerprint(
            replace(
                resolved.resource,
                location=staged_root.as_posix(),
                canonical_target=staged_root.as_posix(),
            )
        ).fingerprint
    except (DataContractError, OSError, ValueError) as error:
        raise ActionError(
            "reproduction.input.unavailable",
            f"regenerated input is unavailable: {resolved.resource.name}: {error}",
        ) from error
    if observed != expected:
        raise ActionError(
            "reproduction.input.observation_mismatch",
            "regenerated input does not match the consumer's recorded "
            f"observation: {resolved.resource.name}",
        )
    return str(mapped)


def _regenerated_input_path(
    source: Path, generated: Mapping[Path, tuple[Path, str]]
) -> Path | None:
    exact = generated.get(source)
    if exact is not None:
        return exact[0]
    for retained, (workspace_path, kind) in generated.items():
        if kind != "directory":
            continue
        try:
            relative = source.relative_to(retained)
        except ValueError:
            continue
        return workspace_path / relative
    return None


def _execution_environment(
    execution: PyrunExecution,
    workspace: ReproductionWorkspace,
    entry: str,
    cid: str,
    execution_id_value: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(dict(execution.recipe.environment))
    execution_root = _attempt_runtime_root(workspace, entry, cid, execution_id_value)
    execution_root.mkdir(parents=True, exist_ok=True)
    roots = {
        "MPLCONFIGDIR": execution_root / "matplotlib",
        "XDG_CACHE_HOME": execution_root / "cache",
        "MATLAB_PREFDIR": execution_root / "matlab",
    }
    for path in roots.values():
        path.mkdir(exist_ok=True)
    environment.update({name: str(path) for name, path in roots.items()})
    identity = execution_id_value.rsplit(":", 1)[-1]
    environment[RUNNER_MARKER] = f"{workspace.run_id}:{entry}:{cid}:{identity}"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _output_paths(
    execution: PyrunExecution, *, entry_root: Path, project_root: Path
) -> dict[str, Path]:
    return {
        output: output_target_path(
            output, entry_root=entry_root, project_root=project_root
        )
        for output, _ in execution.recipe.outputs
    }


def _preflight_output_paths(paths: Iterable[Path], work_project: Path) -> None:
    root = work_project.resolve()
    for path in paths:
        lexical = path.absolute()
        try:
            lexical.relative_to(root)
        except ValueError as error:
            raise ActionError(
                "reproduction.output.escape",
                f"output escapes the run-local workspace: {path}",
            ) from error
        current = root
        for part in lexical.relative_to(root).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ActionError(
                    "reproduction.output.symlink",
                    f"output traverses a symlink: {path}",
                )


def _clear_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.is_symlink():
            raise ActionError(
                "reproduction.output.symlink", f"output is a symlink: {path}"
            )
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)


def _observe_available_outputs(
    paths: Mapping[str, Path], execution: PyrunExecution
) -> tuple[Mapping[str, object], ...]:
    kinds = dict(execution.recipe.outputs)
    result: list[Mapping[str, object]] = []
    for artifact, path in sorted(paths.items()):
        kind = kinds[artifact]
        if path.is_symlink() or not path.exists():
            continue
        try:
            fingerprint = _fingerprint(path, kind)
        except (OSError, ValueError):
            continue
        result.append({"artifact": artifact, "fingerprint": fingerprint.as_dict()})
    return tuple(result)


def _fingerprint(path: Path, kind: str) -> Fingerprint:
    if kind == "file":
        digest, _ = observe_file_content(path)
        return Fingerprint("sha256", digest=digest)
    _, members, _ = observe_directory_tree(path)
    entries = []
    for member in members:
        if member.type == "directory":
            entries.append(member)
            continue
        digest, _ = observe_file_content(path / PurePosixPath(member.path))
        entries.append(type(member)(member.path, "file", digest))
    return compose_directory_fingerprint(tuple(entries))


def observe_output_fingerprint(path: Path, kind: str) -> Fingerprint:
    """Observe one declared output using the shared file/directory contract."""

    if path.is_symlink() or not path.exists():
        raise OSError(f"declared output is unavailable: {path}")
    return _fingerprint(path, kind)


def _readonly_boundaries(
    plan: ReproductionPlan, workspace: ReproductionWorkspace
) -> tuple[tuple[Path, str], ...]:
    result: set[tuple[Path, str]] = set()
    materials = cast(
        Sequence[Mapping[str, object]], plan.comparison_context["materials"]
    )
    for material in materials:
        if material.get("role") != "boundary":
            continue
        identity = material.get("identity")
        kind = material.get("kind")
        if not isinstance(identity, str) or kind not in {
            "file",
            "directory",
            "git-repository",
        }:
            raise ActionError(
                "reproduction.source.invalid", "invalid retained boundary snapshot"
            )
        result.add((Path(identity), cast(str, kind)))
    return tuple(sorted(result, key=lambda value: value[0].as_posix()))


def _seatbelt_profile(
    writable_roots: Sequence[Path], readonly_paths: Sequence[tuple[Path, str]]
) -> str:
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow file-read*)",
        "(allow sysctl-read)",
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix*)",
    ]
    for root in sorted({path.resolve() for path in writable_roots}):
        rules.append(f'(allow file-write* (subpath "{_sandbox_quote(root)}"))')
    for path, kind in sorted(readonly_paths, key=lambda value: value[0].as_posix()):
        matcher = "subpath" if kind in {"directory", "git-repository"} else "literal"
        rules.append(f'(deny file-write* ({matcher} "{_sandbox_quote(path)}"))')
    return " ".join(rules)


def _sandbox_quote(path: Path) -> str:
    value = str(path.resolve())
    if any(character in value for character in "\r\n\0"):
        raise ActionError(
            "reproduction.safety.invalid", "sandbox path contains a control character"
        )
    return value.replace("\\", "\\\\").replace('"', '\\"')


class _WorkerRegistry:
    def __init__(self, entry: str, execution_id: str, marker_value: str):
        self.entry = entry
        self.execution_id = execution_id
        self.marker = f"{RUNNER_MARKER}={marker_value}"
        self.root_pid: int | None = None
        self.parents: dict[int, int | None] = {}
        self.registered: dict[int, str] = {}
        self.last_seen: dict[int, str] = {}

    def register_root(self, pid: int) -> None:
        now = _utc_now()
        self.root_pid = pid
        self.parents[pid] = None
        self.registered[pid] = now
        self.last_seen[pid] = now

    def refresh(self) -> tuple[int, ...]:
        table = _process_table(self.marker)
        candidates = set(self.parents)
        changed = True
        while changed:
            changed = False
            for pid, (parent, state, command) in table.items():
                if _is_zombie(state):
                    continue
                if parent in candidates or self.marker in command:
                    if pid not in candidates:
                        candidates.add(pid)
                        changed = True
                    self.parents.setdefault(
                        pid, parent if parent in candidates else None
                    )
        now = _utc_now()
        live = []
        for pid in sorted(candidates):
            row = table.get(pid)
            if row is None or _is_zombie(row[1]):
                continue
            self.registered.setdefault(pid, now)
            self.last_seen[pid] = now
            live.append(pid)
        if (
            len(live) > MAX_WORKERS_PER_EXECUTION
            or len(self.registered) > MAX_WORKERS_PER_RUN
        ):
            raise ActionError(
                "reproduction.worker.resource_limit", "worker limit exceeded"
            )
        return tuple(live)

    def wait_for_descendants(self, seconds: float) -> tuple[int, ...]:
        deadline = time.monotonic() + seconds
        while True:
            live = tuple(pid for pid in self.refresh() if pid != self.root_pid)
            if not live or time.monotonic() >= deadline:
                return live
            time.sleep(POLL_SECONDS)

    def stop_all(self) -> tuple[int, ...]:
        live = self.refresh()
        self._signal(live, signal.SIGTERM)
        survivors = self._wait_live(GRACEFUL_STOP_SECONDS)
        if survivors:
            self._signal(survivors, signal.SIGKILL)
            survivors = self._wait_live(FORCED_STOP_SECONDS)
        return survivors

    def _signal(self, pids: Sequence[int], requested: signal.Signals) -> None:
        if self.root_pid in pids:
            try:
                os.killpg(cast(int, self.root_pid), requested)
            except (OSError, ProcessLookupError):
                pass
        for pid in sorted(pids, reverse=True):
            try:
                os.kill(pid, requested)
            except (OSError, ProcessLookupError):
                pass

    def _wait_live(self, seconds: float) -> tuple[int, ...]:
        deadline = time.monotonic() + seconds
        while True:
            live = self.refresh()
            if not live or time.monotonic() >= deadline:
                return live
            time.sleep(POLL_SECONDS)

    def records(self) -> tuple[WorkerRecord, ...]:
        table = _process_table(self.marker)
        values = []
        for pid in sorted(self.registered):
            parent = self.parents.get(pid)
            state = (
                "running"
                if pid in table and not _is_zombie(table[pid][1])
                else "exited"
            )
            values.append(
                WorkerRecord(
                    f"worker-{pid}",
                    f"worker-{parent}" if parent in self.registered else None,
                    pid,
                    self.execution_id,
                    state,
                    self.registered[pid],
                    self.last_seen[pid],
                    self.entry,
                )
            )
        return tuple(values)


def _process_table(marker: str) -> dict[int, tuple[int, str, str]]:
    result: dict[int, tuple[int, str, str]] = {}
    marker_name, marker_value = marker.split("=", 1)
    for process in psutil.process_iter(["pid", "ppid", "status"]):
        try:
            pid = process.info["pid"]
            parent = process.info["ppid"]
            state = process.info["status"]
        except (KeyError, psutil.Error):
            continue
        if not isinstance(pid, int) or not isinstance(parent, int):
            continue
        marked = ""
        if parent == 1:
            try:
                if process.environ().get(marker_name) == marker_value:
                    marked = marker
            except psutil.Error:
                pass
        result[pid] = (parent, str(state), marked)
    return result


def _is_zombie(state: str) -> bool:
    return state == psutil.STATUS_ZOMBIE or state.startswith("Z")


def _diagnostic_paths(
    workspace: ReproductionWorkspace,
    entry: str,
    cid: str,
    execution_id: str,
) -> tuple[Path, Path]:
    stdout, stderr = _diagnostic_relative_paths(workspace, entry, cid, execution_id)
    stdout.parent.mkdir(parents=True, exist_ok=True)
    return stdout, stderr


def _diagnostic_relative_paths(
    workspace: ReproductionWorkspace,
    entry: str,
    cid: str,
    execution_id: str,
) -> tuple[Path, Path]:
    root = (
        workspace.diagnostics_root
        / entry
        / cid
        / execution_id.removeprefix("pyrun-exec/v2:")
    )
    return root / "stdout.log", root / "stderr.log"


def _attempt_root(
    workspace: ReproductionWorkspace, entry: str, cid: str, execution_id: str
) -> Path:
    """Return the accepted attempt-private mirrored-project root."""

    return workspace.staging_root / entry / cid / execution_id.rsplit(":", 1)[-1]


def _attempt_runtime_root(
    workspace: ReproductionWorkspace, entry: str, cid: str, execution_id: str
) -> Path:
    return workspace.runtime_root / entry / cid / execution_id.rsplit(":", 1)[-1]


def _materialize_outputs(
    prepared: _PreparedExecution,
    workspace: ReproductionWorkspace,
    source: _ExecutionSource,
) -> None:
    """Publish private attempt outputs into the shared dependency workspace."""

    shared_entry = workspace.map_source(source.entry.root)
    shared = _output_paths(
        prepared.execution,
        entry_root=shared_entry,
        project_root=workspace.work_project,
    )
    _preflight_output_paths(shared.values(), workspace.work_project)
    kinds = dict(prepared.execution.recipe.outputs)
    for artifact, private in sorted(prepared.output_paths.items()):
        target = shared[artifact]
        if _output_already_materialized(private, target, artifact, kinds[artifact]):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        identity_tail = prepared.execution_id.rsplit(":", 1)[-1]
        temporary = target.with_name(
            f".{target.name}.{prepared.entry}-{identity_tail}.tmp"
        )
        _remove_materialization_temporary(temporary)
        try:
            _copy_materialized_output(private, temporary)
            install_path(temporary, target)
        finally:
            _remove_materialization_temporary(temporary)


def _output_already_materialized(
    private: Path, target: Path, artifact: str, kind: str
) -> bool:
    if private.is_symlink() or not private.exists():
        raise OSError(f"private output is unavailable: {artifact}")
    if target.is_symlink():
        raise OSError(f"materialized output is a symlink: {artifact}")
    if not target.exists():
        return False
    if _fingerprint(target, kind) == _fingerprint(private, kind):
        return True
    if target.is_dir() or private.is_dir():
        raise OSError(
            f"existing directory output cannot be atomically replaced: {artifact}"
        )
    return False


def _copy_materialized_output(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _remove_materialization_temporary(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def _control_plane_call(callback: Callable[..., _T], *args: object) -> _T:
    try:
        return callback(*args)
    except ReproductionControlPlaneError:
        raise
    except BaseException as error:
        raise ReproductionControlPlaneError(error) from error


def _required_string(value: Mapping[str, object], name: str) -> str:
    found = value.get(name)
    if not isinstance(found, str):
        raise ActionError(
            "reproduction.plan.invalid", f"planned execution has no {name}"
        )
    return found


def _survivor_message(survivors: Sequence[int]) -> str:
    return "supervised workers survived cleanup: " + ", ".join(map(str, survivors))


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
