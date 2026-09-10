"""Fail-closed disposable execution for planned research-log recipes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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
    compose_directory_fingerprint,
    input_token_parts,
    load_data_file,
    observe_directory_tree,
    observe_file_content,
    parse_fingerprint,
    resolve_input_token,
)
from validation.output_bindings import OutputBindingError, project_output_bindings
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    PyrunFile,
    load_pyrun_state,
    script_target_path,
)

from .context import EntryContext, LogContext, resolve_entry
from .model import ActionError
from .reproduction_contract import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    ReproductionPlan,
    successful_checkpoint_state,
)
from .reproduction_paths import canonical_run_root, checkpoint_temporary_path
from .storage import atomic_write_text

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
class ExecutionControl:
    """Runtime controls shared by one recipe or complete plan execution."""

    resume: bool = False
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None
    generated_paths: Mapping[Path, tuple[Path, str]] | None = None
    source: _ExecutionSource | None = None
    prior_attempts: frozenset[str] = frozenset()
    prior_failures: frozenset[str] = frozenset()
    legacy: bool = False
    attempt_completed: Callable[[ExecutionAttempt], None] = lambda _attempt: None
    progress: Callable[[str, str, str, ExecutionAttempt | None], None] = (
        lambda _event, _entry, _execution_id, _attempt: None
    )
    worker_progress: Callable[[str, str, tuple[WorkerRecord, ...]], None] = (
        lambda _entry, _execution_id, _workers: None
    )


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


class _ControlPlaneState:
    """Share the first control-plane failure across concurrent attempts."""

    def __init__(self) -> None:
        self.failure = threading.Event()
        self.errors: list[ReproductionControlPlaneError] = []

    def record(self, error: ReproductionControlPlaneError) -> None:
        if not self.errors:
            self.errors.append(error)
        self.failure.set()


@dataclass(frozen=True)
class _PreparedExecution:
    entry: str
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
    checkpoint: Path
    legacy: bool


@dataclass(frozen=True)
class _ExecutionSource:
    """One entry and its validated pyrun authority for a reproduction pass."""

    entry: EntryContext
    state: PyrunFile


@dataclass(frozen=True)
class _PreparationOptions:
    source: _ExecutionSource | None = None
    legacy: bool = False


@dataclass(frozen=True)
class _PlanExecutionContext:
    log: LogContext
    plan: ReproductionPlan
    workspace: ReproductionWorkspace
    control: ExecutionControl
    backend: ConfinementBackend
    sources: Mapping[str, _ExecutionSource]
    generated: Mapping[Path, tuple[Path, str]]


@dataclass(frozen=True)
class _AttemptFailure:
    error: BaseException
    prior: ExecutionCheckpoint | None
    legacy: bool


@dataclass(frozen=True)
class _CheckpointLoadContext:
    workspace: ReproductionWorkspace
    entry: str
    execution_id: str
    legacy: bool


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
    pumps: tuple[threading.Thread, ...]
    stream_errors: list[BaseException]


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
                os.replace(temporary, profile_path)
                _sync_directory(profile_root)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return [str(self.executable), "-f", str(profile_path), *command]


def preflight_execution_safety(
    backend: ConfinementBackend | None = None,
) -> None:
    """Fail unless the code-owned runtime confinement is available."""

    (backend or DarwinSeatbelt()).preflight()


def prepare_output_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Create the sole run-ID-bound output workspace and runtime paths."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    source = project_root.resolve()
    try:
        target_root = canonical_run_root(run_root, source, require_exists=False)
    except OSError as error:
        raise ActionError(
            "reproduction.run.path_invalid",
            "run directory must use the canonical dated reproduction path",
        ) from error
    if target_root.exists() or target_root.is_symlink():
        raise ActionError(
            "reproduction.run.exists", f"run directory already exists: {run_root}"
        )
    target_root.mkdir(parents=True)
    return _populate_output_workspace(source, target_root, run_id, cleanup_root=True)


def populate_output_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Populate an accepted run directory with no current-attempt workspace."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    source = project_root.resolve()
    try:
        root = canonical_run_root(run_root, source, require_exists=True)
    except OSError as error:
        raise ActionError(
            "reproduction.run.path_invalid", "accepted run directory is invalid"
        ) from error
    allowed = {"attempts", "run.json", "supervisor.json", "supervisor.log"}
    entries = tuple(root.iterdir())
    attempts = root / "attempts"
    invalid_attempts = attempts.is_symlink() or (
        attempts.exists() and not attempts.is_dir()
    )
    if any(path.name not in allowed for path in entries) or invalid_attempts:
        raise ActionError(
            "reproduction.run.path_invalid", "accepted run directory is not pristine"
        )
    return _populate_output_workspace(source, root, run_id, cleanup_root=False)


def _populate_output_workspace(
    source: Path, target_root: Path, run_id: str, *, cleanup_root: bool
) -> ReproductionWorkspace:
    """Create an empty writable project-layout projection for generated files."""

    work = target_root / "workspace"
    try:
        work.mkdir()
        runtime = target_root / "runtime"
        diagnostics = target_root / "diagnostics"
        staging = target_root / "executions"
        for directory in (runtime, diagnostics, target_root / "checkpoints"):
            directory.mkdir()
        _sync_directory(target_root)
    except BaseException:
        if cleanup_root:
            shutil.rmtree(target_root, ignore_errors=True)
        else:
            for name in (
                "workspace",
                "runtime",
                "diagnostics",
                "executions",
                "checkpoints",
            ):
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


def open_existing_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Open a stopped run's exact existing paths without recreating them."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    root = run_root.resolve()
    paths = [root / name for name in ("workspace", "runtime", "diagnostics")]
    paths.append(root / "checkpoints")
    if any(path.is_symlink() or not path.is_dir() for path in paths):
        raise ActionError(
            "reproduction.workspace.invalid", "stopped run workspace is incomplete"
        )
    return ReproductionWorkspace(
        run_id,
        root,
        project_root.resolve(),
        paths[0],
        paths[1],
        paths[2],
        root / "executions",
    )


def execute_planned_recipe(
    log: LogContext,
    plan: ReproductionPlan,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    control: ExecutionControl = ExecutionControl(),
) -> ExecutionAttempt:
    """Execute one accepted recipe against its run-local output workspace."""

    sources: dict[str, _ExecutionSource] = {}
    if control.source is not None:
        sources[control.source.entry.id] = control.source
    generated = control.generated_paths
    if generated is None:
        generated = _generated_output_paths(log, plan, workspace, sources=sources)
    entry_id = _required_string(planned, "entry")
    source = _execution_source(log, workspace, entry_id, sources)
    prepared = _prepare_execution(
        log,
        planned,
        workspace,
        generated,
        _PreparationOptions(source, control.legacy),
    )
    _preflight_output_paths(prepared.output_paths.values(), prepared.run_root)
    if not control.resume:
        _clear_outputs(prepared.output_paths.values())
    prior = (
        _load_checkpoint_control_plane(
            workspace,
            prepared.entry,
            prepared.execution_id,
            legacy=control.legacy,
        )
        if control.resume
        else None
    )
    prior_started_at = prior.started_at if prior is not None else None
    prior_elapsed = prior.elapsed_seconds or 0.0 if prior is not None else 0.0
    backend = control.confinement or DarwinSeatbelt()

    def launched(started_at: str) -> None:
        _write_checkpoint(
            prepared.checkpoint,
            _active_checkpoint(
                prepared,
                workspace,
                started_at=prior_started_at or started_at,
                elapsed_seconds=prior_elapsed,
            ),
            legacy=control.legacy,
        )

    outcome, launched_at, active_elapsed = _run_with_scratch(
        prepared,
        backend,
        plan,
        workspace,
        _RunCallbacks(
            control.stop_requested,
            control.execution_timeout_seconds,
            launched,
            lambda workers: _control_plane_call(
                control.worker_progress,
                prepared.entry,
                prepared.execution_id,
                workers,
            ),
        ),
    )
    outputs = _observe_available_outputs(prepared.output_paths, prepared.execution)
    state, failure_code, failure_message = _attempt_state(
        outcome,
        len(outputs),
        len(prepared.output_paths),
        legacy=control.legacy,
    )
    finished_at = None if outcome.stopped or launched_at is None else _utc_now()
    checkpoint = ExecutionCheckpoint(
        prepared.entry,
        prepared.execution_id,
        state,
        prepared.checkpoint.relative_to(workspace.run_root).as_posix(),
        finished_at if successful_checkpoint_state(state) else None,
        outputs,
        prior_started_at or launched_at,
        finished_at,
        (
            prior_elapsed + active_elapsed
            if prior_started_at is not None or launched_at is not None
            else None
        ),
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
    _write_checkpoint(prepared.checkpoint, checkpoint, legacy=control.legacy)
    if successful_checkpoint_state(state) and not control.legacy:
        try:
            _materialize_outputs(prepared, workspace, source)
        except (OSError, ValueError) as error:
            failure_code = "output_materialization_failed"
            failure_message = str(error)
            checkpoint = ExecutionCheckpoint(
                prepared.entry,
                prepared.execution_id,
                "failed",
                checkpoint.path,
                None,
                checkpoint.outputs,
                checkpoint.started_at,
                checkpoint.finished_at,
                checkpoint.elapsed_seconds,
                {
                    "code": failure_code,
                    "message": failure_message,
                    "recorded_at": _utc_now(),
                },
            )
            _write_checkpoint(prepared.checkpoint, checkpoint, legacy=False)
    return ExecutionAttempt(
        prepared.entry,
        prepared.execution_id,
        outcome.returncode,
        outcome.stopped,
        failure_code,
        failure_message,
        checkpoint,
        outcome.workers,
        prepared.stdout.relative_to(workspace.run_root).as_posix(),
        prepared.stderr.relative_to(workspace.run_root).as_posix(),
    )


def cleanup_reproduction_scratch(run_root: Path) -> None:
    """Remove owned scratch only after the caller has confirmed workers stopped.

    Durable ownership records are retained on failure, blocking relaunch until
    recovery can complete. No unrelated temporary directories are inspected.
    """

    for record in sorted((run_root / "scratch").glob("*/*.json")):
        _remove_execution_scratch(record)


def _remove_execution_scratch(record: Path) -> None:
    try:
        if record.is_symlink():
            raise ValueError(f"scratch record is a symlink: {record}")
        value = json.loads(record.read_text(encoding="utf-8"))
        path = Path(value)
        if path.parent != Path("/private/tmp") or not path.name.startswith(
            "reproduction-scratch-"
        ):
            raise ValueError(f"invalid scratch directory: {path}")
        if path.is_symlink():
            raise ValueError(f"scratch directory is a symlink: {path}")
        if path.exists():
            shutil.rmtree(path)
        record.unlink()
    except (OSError, TypeError, ValueError) as error:
        raise ReproductionControlPlaneError(
            ActionError("scratch_cleanup_incomplete", str(error)),
            cleanup_incomplete=True,
        ) from error


def _run_with_scratch(
    prepared: _PreparedExecution,
    backend: ConfinementBackend,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    callbacks: _RunCallbacks,
) -> tuple[_ProcessOutcome, str | None, float]:
    record = (
        workspace.run_root
        / "scratch"
        / prepared.entry
        / f"{prepared.execution_id.rsplit(':', 1)[-1]}.json"
    )
    if record.exists() or record.is_symlink():
        raise ReproductionControlPlaneError(
            ActionError(
                "scratch_cleanup_incomplete", f"scratch recovery required: {record}"
            ),
            cleanup_incomplete=True,
        )
    scratch = Path(tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp"))
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        _control_plane_call(atomic_write_text, record, json.dumps(str(scratch)) + "\n")
    except BaseException:
        shutil.rmtree(scratch)
        raise
    prepared = replace(
        prepared, environment={**prepared.environment, "TMPDIR": str(scratch)}
    )
    cleanup_pending = False
    try:
        command = _confined_command(
            backend, prepared.command, plan, workspace, prepared
        )
        result = _run_prepared(prepared, command, workspace, callbacks)
        cleanup_pending = any(worker.state == "running" for worker in result[0].workers)
        return result
    except ReproductionControlPlaneError as error:
        cleanup_pending = error.cleanup_incomplete
        raise
    finally:
        if not cleanup_pending:
            _remove_execution_scratch(record)


def _prepare_execution(
    log: LogContext,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    generated: Mapping[Path, tuple[Path, str]],
    options: _PreparationOptions = _PreparationOptions(),
) -> _PreparedExecution:
    entry_id = _required_string(planned, "entry")
    execution_id = _required_string(planned, "execution_id")
    loaded = options.source or _execution_source(log, workspace, entry_id, {})
    source_entry = loaded.entry
    execution = loaded.state.executions.get(execution_id)
    if execution is None:
        raise ActionError(
            "reproduction.execution.missing",
            f"accepted execution is no longer present: {entry_id}:{execution_id}",
        )
    attempt_root = (
        workspace.work_project
        if options.legacy
        else _attempt_root(workspace, entry_id, execution_id)
    )
    runtime_root = (
        workspace.runtime_root / execution_id.rsplit(":", 1)[-1]
        if options.legacy
        else _attempt_runtime_root(workspace, entry_id, execution_id)
    )
    diagnostics_root = (
        workspace.diagnostics_root
        if options.legacy
        else workspace.diagnostics_root / entry_id / execution_id.rsplit(":", 1)[-1]
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
        source_entry=source_entry.root,
        workspace=workspace,
        output_paths=output_paths,
        generated=generated,
    )
    stdout_path, stderr_path = _diagnostic_paths(
        workspace, entry_id, execution_id, legacy=options.legacy
    )
    checkpoint_path = _checkpoint_path(workspace, entry_id, execution_id)
    return _PreparedExecution(
        entry_id,
        execution_id,
        execution,
        attempt_root,
        runtime_root,
        diagnostics_root,
        work_entry,
        output_paths,
        tuple(command),
        _execution_environment(
            execution, workspace, entry_id, execution_id, legacy=options.legacy
        ),
        captures,
        stdout_path,
        stderr_path,
        checkpoint_path,
        options.legacy,
    )


def _active_checkpoint(
    prepared: _PreparedExecution,
    workspace: ReproductionWorkspace,
    *,
    started_at: str,
    elapsed_seconds: float,
) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        prepared.entry,
        prepared.execution_id,
        "active",
        prepared.checkpoint.relative_to(workspace.run_root).as_posix(),
        None,
        (),
        started_at,
        None,
        elapsed_seconds,
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
        writable_roots=(Path(prepared.environment["TMPDIR"]),)
        + (
            (workspace.work_project, workspace.runtime_root)
            if prepared.legacy
            else (
                prepared.run_root,
                prepared.runtime_root,
                prepared.diagnostics_root,
            )
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
            deadline = (
                started_monotonic + callbacks.execution_timeout_seconds
            )
            _control_plane_call(callbacks.on_launch, started_at)
            _control_plane_call(registry.register_root, launched.process.pid)
            _control_plane_call(
                callbacks.on_workers, _control_plane_call(registry.records)
            )
            outcome = _monitor_process(
                launched.process,
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
    errors: list[BaseException] = []
    pumps = _start_stream_pumps(
        process,
        stdout,
        stderr,
        capture_handles,
        errors,
    )
    return _LaunchedProcess(process, pumps, errors)


def _start_stream_pumps(
    process: subprocess.Popen[bytes],
    stdout: BinaryIO,
    stderr: BinaryIO,
    captures: Mapping[str, BinaryIO],
    errors: list[BaseException],
) -> tuple[threading.Thread, ...]:
    combined = captures.get("--capture-stdout-stderr")
    stdout_capture = captures.get("--capture-stdout")
    stderr_capture = captures.get("--capture-stderr")
    pumps: list[threading.Thread] = []
    for source, destinations in (
        (process.stdout, (stdout, combined or stdout_capture)),
        (process.stderr, (stderr, stderr_capture)),
    ):
        if source is None:
            continue
        thread = threading.Thread(
            target=_pump_stream,
            args=(source, destinations, errors),
            daemon=True,
        )
        pumps.append(thread)
        thread.start()
    return tuple(pumps)


def _monitor_process(
    process: subprocess.Popen[bytes],
    registry: _WorkerRegistry,
    callbacks: _RunCallbacks,
    deadline: float,
) -> _ProcessOutcome:
    stopped = False
    failure_code: str | None = None
    failure_message: str | None = None
    while process.poll() is None:
        _control_plane_call(registry.refresh)
        _control_plane_call(
            callbacks.on_workers, _control_plane_call(registry.records)
        )
        if callbacks.stop_requested():
            stopped = True
            survivors = _control_plane_call(registry.stop_all)
            if survivors:
                failure_code = "worker_cleanup_incomplete"
                failure_message = _survivor_message(survivors)
            break
        if time.monotonic() >= deadline:
            survivors = _control_plane_call(registry.stop_all)
            if survivors:
                failure_code = "worker_cleanup_incomplete"
                failure_message = _survivor_message(survivors)
            else:
                failure_code = "execution_timeout"
                failure_message = (
                    "command exceeded the runtime limit of "
                    f"{callbacks.execution_timeout_seconds} seconds"
                )
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


def _finish_streams(
    launched: _LaunchedProcess,
    failure_code: str | None,
    failure_message: str | None,
) -> tuple[str | None, str | None]:
    for thread in launched.pumps:
        thread.join(timeout=FORCED_STOP_SECONDS)
    if any(thread.is_alive() for thread in launched.pumps):
        launched.stream_errors.append(RuntimeError("capture stream did not close"))
    if launched.stream_errors and failure_code is None:
        return (
            "capture_failed",
            f"could not retain captured output: {launched.stream_errors[0]}",
        )
    return failure_code, failure_message


def _pump_stream(
    source: BinaryIO,
    destinations: Sequence[BinaryIO | None],
    errors: list[BaseException],
) -> None:
    """Copy one captured child stream to its diagnostics and declared output."""

    try:
        while chunk := source.read(64 * 1024):
            for destination in destinations:
                if destination is not None:
                    destination.write(chunk)
    except BaseException as error:
        errors.append(error)
    finally:
        source.close()


def _attempt_state(
    outcome: _ProcessOutcome,
    observed_outputs: int,
    declared_outputs: int,
    *,
    legacy: bool = False,
) -> tuple[str, str | None, str | None]:
    incomplete = "partial" if legacy else "failed"
    stopped = "partial" if legacy else "stopped"
    success = "complete" if legacy else "succeeded"
    if outcome.stopped:
        return (
            stopped,
            outcome.failure_code or "stop_requested",
            outcome.failure_message or "Reproduction was stopped by request.",
        )
    if outcome.failure_code is not None:
        return incomplete, outcome.failure_code, outcome.failure_message
    if outcome.returncode != 0:
        return (
            incomplete,
            "execution_failed",
            f"execution exited with status {outcome.returncode}",
        )
    if observed_outputs != declared_outputs:
        return (
            incomplete,
            "output_missing",
            "one or more declared outputs were not generated",
        )
    return success, None, None


def execute_reproduction_plan(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    control: ExecutionControl = ExecutionControl(),
) -> ExecutionBatch:
    """Execute every runnable component without crossing dependency failures."""

    from .reproduction_planner import verify_reproduction_runtime_snapshot

    verify_reproduction_runtime_snapshot(log, plan)
    ordered = sorted(plan.executions, key=_execution_order)
    _require_execution_order(ordered)
    schedule = _BatchSchedule(
        list(ordered),
        {},
        {},
        [],
        [],
        set(control.prior_failures),
        set(control.prior_attempts) | set(control.prior_failures),
    )
    backend = control.confinement or DarwinSeatbelt()
    sources: dict[str, _ExecutionSource] = {}
    generated = _generated_output_paths(log, plan, workspace, sources=sources)
    for planned in ordered:
        _execution_source(log, workspace, _required_string(planned, "entry"), sources)

    control_state = _ControlPlaneState()
    runtime_control = replace(
        control,
        stop_requested=lambda: (
            control_state.failure.is_set() or control.stop_requested()
        ),
    )
    runtime = _PlanExecutionContext(
        log, plan, workspace, runtime_control, backend, sources, generated
    )
    with ThreadPoolExecutor(
        max_workers=plan.jobs, thread_name_prefix="reproduce"
    ) as pool:
        while schedule.pending or schedule.running:
            if runtime_control.stop_requested():
                schedule.stopped = True
            progress_made = _resolve_pending_controlled(
                schedule, workspace, sources, runtime_control, control_state
            )
            if schedule.stopped:
                if not schedule.running:
                    break
            else:
                progress_made = (
                    _launch_ready(
                        schedule,
                        pool,
                        plan.jobs,
                        lambda planned: _execute_scheduled_recipe(runtime, planned),
                    )
                    or progress_made
                )
            if schedule.running:
                _collect_finished(
                    schedule,
                    runtime_control,
                    control_state,
                )
            elif schedule.pending and not progress_made and not schedule.stopped:
                raise ActionError(
                    "reproduction.scheduler.deadlock",
                    "no pending execution can become ready",
                )
    if control_state.errors:
        raise control_state.errors[0]
    ordered_attempts = tuple(
        schedule.attempts[reference]
        for reference in (
            _execution_reference(
                _required_string(item, "entry"),
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


def _execute_scheduled_recipe(
    context: _PlanExecutionContext, planned: Mapping[str, object]
) -> ExecutionAttempt | None:
    from .reproduction_scheduler import (
        acquire_scheduling_permit,
        release_scheduling_permit,
    )

    control = context.control
    workspace = context.workspace
    scheduled = {**planned, "exclusive": True} if control.legacy else planned
    permit = acquire_scheduling_permit(
        workspace.source_project,
        workspace.run_root,
        workspace.run_id,
        scheduled,
        stop_requested=control.stop_requested,
    )
    if permit is None:
        return None
    entry_id = _required_string(planned, "entry")
    identity = _required_string(planned, "execution_id")
    checkpoint: ExecutionCheckpoint | None = None
    try:
        checkpoint = _load_checkpoint_control_plane(
            workspace, entry_id, identity, legacy=control.legacy
        )
        _control_plane_call(control.progress, "started", entry_id, identity, None)
        attempt = execute_planned_recipe(
            context.log,
            context.plan,
            planned,
            workspace,
            ExecutionControl(
                resume=control.resume and checkpoint is not None,
                execution_timeout_seconds=control.execution_timeout_seconds,
                stop_requested=control.stop_requested,
                confinement=context.backend,
                generated_paths=context.generated,
                source=context.sources[entry_id],
                legacy=control.legacy,
                progress=control.progress,
                worker_progress=control.worker_progress,
            ),
        )
    except ReproductionControlPlaneError as error:
        if not error.cleanup_incomplete:
            release_scheduling_permit(permit)
        raise
    except BaseException as error:
        attempt = _exception_attempt(
            workspace,
            entry_id,
            identity,
            _AttemptFailure(error, checkpoint, control.legacy),
        )
    if not any(worker.state == "running" for worker in attempt.workers):
        release_scheduling_permit(permit)
    return attempt


def _exception_attempt(
    workspace: ReproductionWorkspace,
    entry: str,
    execution_id: str,
    failure: _AttemptFailure,
) -> ExecutionAttempt:
    """Durably terminate an attempt that failed outside child supervision."""

    prior = failure.prior
    error = failure.error
    code = cast(str, getattr(error, "code", "execution_exception"))
    message = str(error) or type(error).__name__
    path = _checkpoint_path(workspace, entry, execution_id)
    state = "partial" if failure.legacy else "failed"
    checkpoint = ExecutionCheckpoint(
        entry,
        execution_id,
        state,
        path.relative_to(workspace.run_root).as_posix(),
        None,
        prior.outputs if prior is not None else (),
        prior.started_at if prior is not None else None,
        prior.finished_at if prior is not None else None,
        prior.elapsed_seconds if prior is not None else None,
        (
            None
            if failure.legacy
            else {"code": code, "message": message, "recorded_at": _utc_now()}
        ),
    )
    _write_checkpoint(path, checkpoint, legacy=failure.legacy)
    stdout, stderr = _diagnostic_relative_paths(
        workspace, entry, execution_id, legacy=failure.legacy
    )
    return ExecutionAttempt(
        entry,
        execution_id,
        None,
        False,
        code,
        message,
        checkpoint,
        (),
        stdout.relative_to(workspace.run_root).as_posix(),
        stderr.relative_to(workspace.run_root).as_posix(),
    )


def _resolve_pending(
    schedule: _BatchSchedule,
    workspace: ReproductionWorkspace,
    sources: Mapping[str, _ExecutionSource],
    control: ExecutionControl,
) -> bool:
    """Resolve failed dependencies, prior results, and reusable checkpoints."""

    progress_made = False
    for planned in list(schedule.pending):
        entry_id = _required_string(planned, "entry")
        identity = _required_string(planned, "execution_id")
        reference = _execution_reference(entry_id, identity)
        dependencies = set(_dependency_references(planned))
        blocked = tuple(sorted(dependencies & schedule.unavailable))
        if blocked:
            schedule.skips.append(
                {
                    "depends_on": list(blocked),
                    "entry": entry_id,
                    "execution_id": identity,
                    "reason": "dependency_failed",
                }
            )
            schedule.unavailable.add(reference)
            schedule.complete.add(reference)
            schedule.pending.remove(planned)
            progress_made = True
            continue
        if reference in schedule.complete:
            schedule.reused.append(reference)
            _control_plane_call(control.progress, "reused", entry_id, identity, None)
            schedule.pending.remove(planned)
            progress_made = True
            continue
        checkpoint = _load_checkpoint_control_plane(
            workspace, entry_id, identity, legacy=control.legacy
        )
        if checkpoint is None or not successful_checkpoint_state(checkpoint.state):
            continue
        if not control.resume or not _checkpoint_outputs_current(
            checkpoint, sources[entry_id], workspace, identity, legacy=control.legacy
        ):
            raise ReproductionControlPlaneError(
                ActionError(
                    "reproduction.checkpoint.changed",
                    f"completed checkpoint is not reusable: {reference}",
                )
            )
        schedule.reused.append(reference)
        schedule.complete.add(reference)
        if not control.legacy:
            prepared = _prepare_execution(
                sources[entry_id].entry.log,
                planned,
                workspace,
                {},
                _PreparationOptions(sources[entry_id]),
            )
            _materialize_outputs(prepared, workspace, sources[entry_id])
        _control_plane_call(control.progress, "reused", entry_id, identity, None)
        schedule.pending.remove(planned)
        progress_made = True
    return progress_made


def _resolve_pending_controlled(
    schedule: _BatchSchedule,
    workspace: ReproductionWorkspace,
    sources: Mapping[str, _ExecutionSource],
    control: ExecutionControl,
    control_state: _ControlPlaneState,
) -> bool:
    try:
        return _resolve_pending(schedule, workspace, sources, control)
    except ReproductionControlPlaneError as error:
        control_state.record(error)
        schedule.stopped = True
        return False


def _launch_ready(
    schedule: _BatchSchedule,
    pool: ThreadPoolExecutor,
    jobs: int,
    run_one: Callable[[Mapping[str, object]], ExecutionAttempt | None],
) -> bool:
    ready = [
        planned
        for planned in schedule.pending
        if set(_dependency_references(planned)) <= schedule.complete
    ]
    exclusive_ready = [item for item in ready if item.get("exclusive") is True]
    launchable = exclusive_ready[:1] if exclusive_ready else ready
    if exclusive_ready and schedule.running:
        return False
    slots = jobs - len(schedule.running)
    for planned in launchable[:slots]:
        schedule.running[pool.submit(run_one, planned)] = planned
        schedule.pending.remove(planned)
        if planned.get("exclusive") is True:
            break
    return bool(launchable[:slots])


def _collect_finished(
    schedule: _BatchSchedule,
    control: ExecutionControl,
    control_state: _ControlPlaneState,
) -> None:
    done, _ = wait(tuple(schedule.running), return_when=FIRST_COMPLETED)
    for future in done:
        schedule.running.pop(future)
        try:
            attempt = future.result()
        except ReproductionControlPlaneError as error:
            control_state.record(error)
            schedule.stopped = True
            continue
        if attempt is None:
            schedule.stopped = True
            continue
        reference = _execution_reference(attempt.entry, attempt.execution_id)
        schedule.attempts[reference] = attempt
        schedule.complete.add(reference)
        if control_state.failure.is_set():
            continue
        try:
            _control_plane_call(
                control.progress,
                "finished",
                attempt.entry,
                attempt.execution_id,
                attempt,
            )
        except ReproductionControlPlaneError as error:
            control_state.record(error)
            schedule.stopped = True
            continue
        control.attempt_completed(attempt)
        if attempt.stopped:
            schedule.stopped = True
        if not successful_checkpoint_state(attempt.checkpoint.state):
            schedule.unavailable.add(reference)


def completed_execution_attempts(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    *,
    legacy: bool = False,
) -> tuple[ExecutionAttempt, ...]:
    """Load every complete planned checkpoint as a comparison-ready attempt."""

    results: list[ExecutionAttempt] = []
    sources: dict[str, _ExecutionSource] = {}
    generated = _generated_output_paths(log, plan, workspace, sources=sources)
    for planned in sorted(plan.executions, key=_execution_order):
        entry_id = _required_string(planned, "entry")
        identity = _required_string(planned, "execution_id")
        checkpoint = _load_checkpoint(workspace, entry_id, identity, legacy=legacy)
        if checkpoint is None or not successful_checkpoint_state(checkpoint.state):
            continue
        if not _checkpoint_outputs_current(
            checkpoint,
            _execution_source(log, workspace, entry_id, sources),
            workspace,
            identity,
            legacy=legacy,
        ):
            raise ActionError(
                "reproduction.checkpoint.changed",
                f"completed checkpoint is not current: {entry_id}:{identity}",
            )
        prepared = _prepare_execution(
            log,
            planned,
            workspace,
            generated,
            _PreparationOptions(
                _execution_source(log, workspace, entry_id, sources),
                legacy,
            ),
        )
        if checkpoint.state == "succeeded":
            _materialize_outputs(
                prepared,
                workspace,
                _execution_source(log, workspace, entry_id, sources),
            )
        results.append(
            ExecutionAttempt(
                entry_id,
                identity,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                prepared.stdout.relative_to(workspace.run_root).as_posix(),
                prepared.stderr.relative_to(workspace.run_root).as_posix(),
            )
        )
    return tuple(results)


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


def _execution_reference(entry: str, execution_id: str) -> str:
    return f"{entry}:{execution_id}"


def _load_checkpoint(
    workspace: ReproductionWorkspace,
    entry: str,
    execution_id: str,
    *,
    legacy: bool,
) -> ExecutionCheckpoint | None:
    path = _checkpoint_path(workspace, entry, execution_id)
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.checkpoint.invalid", f"invalid checkpoint path: {path}"
        )
    try:
        raw = path.read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("checkpoint crossed its byte bound")
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.checkpoint.invalid", str(error)) from error
    value, state, completed_at, outputs, expected_path = _checkpoint_header(
        value,
        path,
        _CheckpointLoadContext(workspace, entry, execution_id, legacy),
    )
    if text != json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n":
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint is not canonical"
        )
    decoded = _checkpoint_outputs(outputs)
    failure = _checkpoint_failure(value, state, completed_at)
    started_at, finished_at, elapsed_seconds = _checkpoint_timing(value, state)
    return ExecutionCheckpoint(
        entry,
        execution_id,
        state,
        expected_path,
        completed_at,
        decoded,
        started_at,
        finished_at,
        float(elapsed_seconds) if elapsed_seconds is not None else None,
        failure,
    )


def _checkpoint_header(
    value: object,
    path: Path,
    context: _CheckpointLoadContext,
) -> tuple[Mapping[str, object], str, str | None, list[object], str]:
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
    expected_fields = fields if context.legacy else fields | {"failure"}
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint fields are invalid"
        )
    state = value.get("state")
    completed_at = value.get("completed_at")
    outputs = value.get("outputs")
    expected_path = path.relative_to(context.workspace.run_root).as_posix()
    if (
        value.get("entry") != context.entry
        or value.get("execution_id") != context.execution_id
        or value.get("path") != expected_path
        or state
        not in (
            {"active", "complete", "partial"}
            if context.legacy
            else {"active", "succeeded", "failed", "stopped"}
        )
        or completed_at is not None
        and not isinstance(completed_at, str)
        or not isinstance(outputs, list)
        or len(outputs) > 256
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint content is invalid"
        )
    return (
        value,
        cast(str, state),
        completed_at,
        cast(list[object], outputs),
        expected_path,
    )


def _checkpoint_outputs(outputs: Sequence[object]) -> tuple[Mapping[str, object], ...]:
    decoded: list[Mapping[str, object]] = []
    for output in outputs:
        if (
            not isinstance(output, Mapping)
            or set(output) != {"artifact", "fingerprint"}
            or not isinstance(output.get("artifact"), str)
        ):
            raise ActionError(
                "reproduction.checkpoint.invalid", "checkpoint output is invalid"
            )
        parse_fingerprint(output.get("fingerprint"), str(output["artifact"]))
        decoded.append(cast(Mapping[str, object], output))
    artifacts = [cast(str, item["artifact"]) for item in decoded]
    if artifacts != sorted(set(artifacts)):
        raise ActionError(
            "reproduction.checkpoint.invalid",
            "checkpoint outputs are not canonical",
        )
    return tuple(decoded)


def _checkpoint_failure(
    value: Mapping[str, object], state: str, completed_at: str | None
) -> Mapping[str, object] | None:
    successful = state in {"complete", "succeeded"}
    failure = value.get("failure")
    if (
        failure is not None
        and (
            not isinstance(failure, Mapping)
            or set(failure) != {"code", "message", "recorded_at"}
            or not all(
                isinstance(failure.get(name), str) and failure.get(name)
                for name in ("code", "message")
            )
            or not isinstance(failure.get("recorded_at"), str)
            or TIMESTAMP_RE.fullmatch(cast(str, failure.get("recorded_at"))) is None
        )
        or state in {"active", "complete", "succeeded"}
        and failure is not None
        or state in {"failed", "stopped"}
        and failure is None
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint failure is invalid"
        )
    if successful and not isinstance(completed_at, str):
        raise ActionError(
            "reproduction.checkpoint.invalid", "complete checkpoint has no timestamp"
        )
    return cast(Mapping[str, object] | None, failure)


def _checkpoint_timing(
    value: Mapping[str, object], state: object
) -> tuple[str | None, str | None, float | int | None]:
    """Decode one explicit attempt-timing projection."""

    started_at = value.get("started_at")
    finished_at = value.get("finished_at")
    elapsed_seconds = value.get("elapsed_seconds")
    if any(
        item is not None
        and (not isinstance(item, str) or TIMESTAMP_RE.fullmatch(item) is None)
        for item in (value.get("completed_at"), started_at, finished_at)
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint timestamp is invalid"
        )
    if elapsed_seconds is not None and (
        not isinstance(elapsed_seconds, (int, float))
        or isinstance(elapsed_seconds, bool)
        or elapsed_seconds < 0
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint elapsed time is invalid"
        )
    if started_at is None and (finished_at is not None or elapsed_seconds is not None):
        raise ActionError(
            "reproduction.checkpoint.invalid", "unlaunched checkpoint has timing"
        )
    if started_at is not None and elapsed_seconds is None:
        raise ActionError(
            "reproduction.checkpoint.invalid", "launched checkpoint has no elapsed time"
        )
    if state == "active" and finished_at is not None:
        raise ActionError(
            "reproduction.checkpoint.invalid", "active checkpoint is finished"
        )
    if (
        isinstance(started_at, str)
        and isinstance(finished_at, str)
        and finished_at < started_at
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint finishes before it starts"
        )
    completed_at = value.get("completed_at")
    successful = state in {"complete", "succeeded"}
    if successful and (finished_at is None or completed_at != finished_at):
        raise ActionError(
            "reproduction.checkpoint.invalid", "complete checkpoint timing is invalid"
        )
    if not successful and completed_at is not None:
        raise ActionError(
            "reproduction.checkpoint.invalid", "incomplete checkpoint is completed"
        )
    return cast(str | None, started_at), cast(str | None, finished_at), elapsed_seconds


def _checkpoint_outputs_current(
    checkpoint: ExecutionCheckpoint,
    source: _ExecutionSource,
    workspace: ReproductionWorkspace,
    execution_id: str,
    *,
    legacy: bool = False,
) -> bool:
    entry = source.entry
    execution = source.state.executions.get(execution_id)
    if execution is None:
        return False
    project_root = (
        workspace.work_project
        if legacy
        else _attempt_root(workspace, entry.id, execution_id)
    )
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
    *,
    source_entry: Path,
    workspace: ReproductionWorkspace,
    output_paths: Mapping[str, Path],
    generated: Mapping[Path, tuple[Path, str]],
) -> tuple[list[str], Mapping[str, Path]]:
    data = load_data_file(source_entry / "data.json", entry_root=source_entry)
    interpreter_link = workspace.source_project / ".conda" / "bin" / "python"
    interpreter = interpreter_link.resolve()
    if not interpreter.is_file():
        raise ActionError(
            "reproduction.environment.missing",
            f"project-local Python is unavailable: {interpreter_link}",
        )
    script = script_target_path(
        execution.recipe.script,
        entry_root=source_entry,
        project_root=workspace.source_project,
    )
    if script.is_symlink() or not script.is_file():
        raise ActionError(
            "reproduction.script.unavailable", f"retained script is missing: {script}"
        )
    try:
        projection = project_output_bindings(
            execution.recipe.parameters,
            execution.recipe.outputs,
            entry_root=source_entry,
            project_root=workspace.source_project,
            subject=execution.recipe.script,
        )
    except OutputBindingError as error:
        raise ActionError("reproduction.output.binding_invalid", str(error)) from error
    captures = {
        binding.option: output_paths[binding.output]
        for binding in projection.captures
        if binding.option is not None
    }
    bindings = {
        binding.parameter_index: binding.substituted(output_paths[binding.output])
        for binding in projection.parameters
        if binding.parameter_index is not None
    }
    arguments = []
    for index, value in enumerate(projection.child_parameters):
        binding = bindings.get(index)
        if binding is not None:
            arguments.append(binding)
            continue
        arguments.append(
            _resolve_parameter(
                value,
                data=data,
                source_log=source_entry.parent.parent,
                workspace=workspace,
                generated=generated,
            )
        )
    return [str(interpreter), str(script), *arguments], captures


def _execution_source(
    log: LogContext,
    workspace: ReproductionWorkspace,
    entry_id: str,
    sources: dict[str, _ExecutionSource],
) -> _ExecutionSource:
    source = sources.get(entry_id)
    if source is not None:
        return source
    entry = resolve_entry(log, entry_id)
    state = load_pyrun_state(
        entry.root / "pyrun.json",
        entry_root=entry.root,
        project_root=workspace.source_project,
    )
    source = _ExecutionSource(entry, state)
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
        source = _execution_source(log, workspace, entry_id, loaded_sources)
        entry = source.entry
        execution = source.state.executions.get(
            _required_string(planned, "execution_id")
        )
        if execution is None:
            continue
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


def _resolve_parameter(
    value: str,
    *,
    data: DataFile,
    source_log: Path,
    workspace: ReproductionWorkspace,
    generated: Mapping[Path, tuple[Path, str]],
) -> str:
    value = value.replace("<project>", str(workspace.source_project)).replace(
        "<log>", str(source_log)
    )
    parts = input_token_parts(value)
    if parts is None:
        return value
    try:
        resolved = resolve_input_token(value, data)
    except DataContractError as error:
        raise ActionError("reproduction.input.invalid", str(error)) from error
    if resolved.projection == "commit":
        return resolved.value
    source = Path(resolved.value)
    mapped = _regenerated_input_path(source.resolve(), generated)
    if mapped is not None:
        if not mapped.exists():
            raise ActionError(
                "reproduction.input.unavailable",
                f"regenerated input is unavailable: {resolved.resource.name}",
            )
        return str(mapped)
    return resolved.value


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
    execution_id_value: str,
    *,
    legacy: bool = False,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(dict(execution.recipe.environment))
    execution_root = (
        workspace.runtime_root / execution_id_value.rsplit(":", 1)[-1]
        if legacy
        else _attempt_runtime_root(workspace, entry, execution_id_value)
    )
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
    environment[RUNNER_MARKER] = (
        f"{workspace.run_id}:{identity}"
        if legacy
        else f"{workspace.run_id}:{entry}:{identity}"
    )
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


def _readonly_boundaries(
    plan: ReproductionPlan, workspace: ReproductionWorkspace
) -> tuple[tuple[Path, str], ...]:
    result: set[tuple[Path, str]] = set()
    materials = cast(Sequence[Mapping[str, object]], plan.source_snapshot["materials"])
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
    execution_id: str,
    *,
    legacy: bool = False,
) -> tuple[Path, Path]:
    stdout, stderr = _diagnostic_relative_paths(
        workspace, entry, execution_id, legacy=legacy
    )
    if not legacy:
        stdout.parent.mkdir(parents=True, exist_ok=True)
    return stdout, stderr


def _diagnostic_relative_paths(
    workspace: ReproductionWorkspace,
    entry: str,
    execution_id: str,
    *,
    legacy: bool,
) -> tuple[Path, Path]:
    if not legacy:
        root = (
            workspace.diagnostics_root
            / entry
            / execution_id.removeprefix("pyrun-exec/v1:")
        )
        return root / "stdout.log", root / "stderr.log"
    stem = f"{entry}-{execution_id.removeprefix('pyrun-exec/v1:')}"
    return (
        workspace.diagnostics_root / f"{stem}.stdout.log",
        workspace.diagnostics_root / f"{stem}.stderr.log",
    )


def _attempt_root(
    workspace: ReproductionWorkspace, entry: str, execution_id: str
) -> Path:
    """Return the accepted attempt-private mirrored-project root."""

    return workspace.staging_root / entry / execution_id.rsplit(":", 1)[-1]


def _attempt_runtime_root(
    workspace: ReproductionWorkspace, entry: str, execution_id: str
) -> Path:
    return workspace.runtime_root / entry / execution_id.rsplit(":", 1)[-1]


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
            os.replace(temporary, target)
            _sync_directory(target.parent)
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


def _checkpoint_path(
    workspace: ReproductionWorkspace, entry: str, execution_id: str
) -> Path:
    digest = execution_id.removeprefix("pyrun-exec/v1:")
    return workspace.run_root / "checkpoints" / f"{entry}-{digest}.json"


def _write_checkpoint(
    path: Path, checkpoint: ExecutionCheckpoint, *, legacy: bool = False
) -> None:
    try:
        value = checkpoint.as_dict()
        if legacy:
            value.pop("failure")
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = checkpoint_temporary_path(path, os.getpid())
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
    except ReproductionControlPlaneError:
        raise
    except BaseException as error:
        raise ReproductionControlPlaneError(error) from error


def _load_checkpoint_control_plane(
    workspace: ReproductionWorkspace,
    entry: str,
    execution_id: str,
    *,
    legacy: bool,
) -> ExecutionCheckpoint | None:
    try:
        return _load_checkpoint(workspace, entry, execution_id, legacy=legacy)
    except ReproductionControlPlaneError:
        raise
    except BaseException as error:
        raise ReproductionControlPlaneError(error) from error


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


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
