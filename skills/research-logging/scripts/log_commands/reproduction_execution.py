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
from stream_capture import StreamCapture, StreamDestination, StreamFailure
from validation.file_publication import install_path, sync_directory
from validation.output_bindings import OutputBindingError, project_output_bindings
from validation.pyrun_outputs import code_target_path, output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    script_target_path,
)

from .context import EntryContext, LogContext, resolve_project_root
from .model import ActionError
from .reproduction_domain import ExecutionRef, ProblemStage
from .reproduction_invocation import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    AcceptedInvocation,
)
from .reproduction_job_control import (
    WorkerRecord as StoredWorkerRecord,
)
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
class _ProcessOutcome:
    returncode: int | None
    stopped: bool
    failure_code: str | None
    failure_message: str | None
    workers: tuple[WorkerRecord, ...]
    failure_stage: ProblemStage | None = None
    error_type: str | None = None
    capture_failures: tuple[StreamFailure, ...] = ()


@dataclass(frozen=True)
class _LaunchedProcess:
    process: subprocess.Popen[bytes]
    capture: StreamCapture


@dataclass(frozen=True)
class _CurrentProcessResult:
    prepared: _PreparedExecution
    outcome: _ProcessOutcome
    active_elapsed: float
    scratch: Path
    started_at: str
    stdout: str
    stderr: str
    argv: tuple[str, ...]
    cwd: str


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
        entry, {(invocation.identity.cid, invocation.identity.execution_id): invocation}
    )
    prepared = _prepare_execution(
        entry.log,
        invocation.identity,
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
        invocation.identity.cid,
        invocation.identity.execution_id,
        outcome.returncode,
        outcome.stopped,
        code,
        message,
        ExecutionCheckpoint(
            entry.id,
            invocation.identity.cid,
            invocation.identity.execution_id,
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
        workspace, entry.id, invocation.identity.cid, invocation.identity.execution_id
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
    identity: ExecutionRef,
    workspace: ReproductionWorkspace,
    generated: Mapping[Path, tuple[Path, str]],
    options: _PreparationOptions = _PreparationOptions(),
) -> _PreparedExecution:
    entry_id, cid, execution_id = (identity.entry, identity.cid, identity.execution_id)
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
            outcome = _finish_streams(launched, outcome)
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
                ProblemStage.LAUNCH if started_at is None else ProblemStage.EXECUTE,
                type(error).__name__,
            ),
            started_at,
            (
                0.0
                if started_monotonic is None
                else max(0.0, time.monotonic() - started_monotonic)
            ),
        )
    return (
        replace(
            outcome,
            workers=_control_plane_call(registry.records),
            failure_stage=outcome.failure_stage
            or (ProblemStage.EXECUTE if outcome.failure_code is not None else None),
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
                    "stdout diagnostics"
                    if destination is stdout
                    else "stderr diagnostics"
                    if destination is stderr
                    else "declared capture",
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
    outcome: _ProcessOutcome,
) -> _ProcessOutcome:
    failures = launched.capture.finish(FORCED_STOP_SECONDS)
    outcome = replace(outcome, capture_failures=failures)
    required = [failure for failure in failures if failure.required]
    if required and outcome.failure_code is None:
        return replace(
            outcome,
            failure_code="capture_failed",
            failure_message=f"could not retain captured output: {required[0].error}",
            failure_stage=ProblemStage.CAPTURE,
            error_type=type(required[0].error).__name__,
        )
    return outcome


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


def _material_readonly_boundaries(
    materials: Sequence[Mapping[str, object]],
) -> tuple[tuple[Path, str], ...]:
    """Use the accepted retained boundaries for either physical run consumer."""

    result: set[tuple[Path, str]] = set()
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
