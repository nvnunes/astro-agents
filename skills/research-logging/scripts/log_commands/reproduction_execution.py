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
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterable, Mapping, Protocol, Sequence, cast

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
from validation.errors import MechanicalContractError
from validation.pyrun_contract import PYRUN_CAPTURE_STREAMS, PYRUN_ENV_OPTION
from validation.pyrun_outputs import output_target_path, portable_output_path
from validation.pyrun_state import (
    PyrunExecution,
    load_pyrun_state,
    script_target_path,
)

from .context import LogContext, resolve_entry
from .model import ActionError
from .reproduction_contract import ReproductionPlan

RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
RUNNER_MARKER = "RESEARCH_LOG_REPRODUCTION_RUN_ID"
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

    def as_dict(self) -> dict[str, object]:
        return {
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

    def as_dict(self) -> dict[str, object]:
        return {
            "completed_at": self.completed_at,
            "execution_id": self.execution_id,
            "outputs": [dict(value) for value in self.outputs],
            "path": self.path,
            "state": self.state,
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


@dataclass(frozen=True)
class ExecutionControl:
    """Runtime controls shared by one recipe or complete plan execution."""

    resume: bool = False
    stop_requested: Callable[[], bool] = lambda: False
    confinement: ConfinementBackend | None = None
    generated_paths: Mapping[Path, tuple[Path, str]] | None = None
    progress: Callable[[str, str, ExecutionAttempt | None], None] = (
        lambda _event, _execution_id, _attempt: None
    )


@dataclass(frozen=True)
class _PreparedExecution:
    entry: str
    execution_id: str
    execution: PyrunExecution
    work_entry: Path
    output_paths: Mapping[str, Path]
    command: tuple[str, ...]
    environment: Mapping[str, str]
    captures: Mapping[str, Path]
    stdout: Path
    stderr: Path
    checkpoint: Path


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
    temporary_root = (source / "tmp").resolve()
    if run_root.parent.resolve() != temporary_root:
        raise ActionError(
            "reproduction.run.path_invalid",
            "run directory must be an immediate child of the project tmp root",
        )
    target_root = run_root.resolve()
    if target_root.exists() or target_root.is_symlink():
        raise ActionError(
            "reproduction.run.exists", f"run directory already exists: {run_root}"
        )
    temporary_root.mkdir(parents=True, exist_ok=True)
    target_root.mkdir()
    return _populate_output_workspace(source, target_root, run_id, cleanup_root=True)


def populate_output_workspace(
    project_root: Path, run_root: Path, run_id: str
) -> ReproductionWorkspace:
    """Populate an accepted run directory containing only durable job state."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.run_id.invalid", f"invalid run ID: {run_id}")
    source = project_root.resolve()
    temporary_root = (source / "tmp").resolve()
    root = run_root.resolve()
    if root.parent != temporary_root or root.is_symlink() or not root.is_dir():
        raise ActionError(
            "reproduction.run.path_invalid", "accepted run directory is invalid"
        )
    allowed = {"run.json", "supervisor.json", "supervisor.log"}
    if any(path.name not in allowed for path in root.iterdir()):
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

    generated = control.generated_paths
    if generated is None:
        generated = _generated_output_paths(log, plan, workspace)
    prepared = _prepare_execution(log, planned, workspace, generated)
    _preflight_output_paths(prepared.output_paths.values(), workspace.work_project)
    if not control.resume:
        _clear_outputs(prepared.output_paths.values())
    active = _active_checkpoint(prepared, workspace)
    _write_checkpoint(prepared.checkpoint, active)
    backend = control.confinement or DarwinSeatbelt()
    confined = _confined_command(backend, prepared.command, plan, workspace)
    outcome = _run_prepared(prepared, confined, workspace, control.stop_requested)
    outputs = _observe_available_outputs(prepared.output_paths, prepared.execution)
    state, failure_code, failure_message = _attempt_state(
        outcome, len(outputs), len(prepared.output_paths)
    )
    checkpoint = ExecutionCheckpoint(
        prepared.entry,
        prepared.execution_id,
        state,
        active.path,
        _utc_now() if state == "complete" else None,
        outputs,
    )
    _write_checkpoint(prepared.checkpoint, checkpoint)
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


def _prepare_execution(
    log: LogContext,
    planned: Mapping[str, object],
    workspace: ReproductionWorkspace,
    generated: Mapping[Path, tuple[Path, str]],
) -> _PreparedExecution:
    entry_id = _required_string(planned, "entry")
    execution_id = _required_string(planned, "execution_id")
    source_entry = resolve_entry(log, entry_id)
    source_state = load_pyrun_state(
        source_entry.root / "pyrun.json",
        entry_root=source_entry.root,
        project_root=workspace.source_project,
    )
    execution = source_state.executions.get(execution_id)
    if execution is None:
        raise ActionError(
            "reproduction.execution.missing",
            f"accepted execution is no longer present: {entry_id}:{execution_id}",
    )
    work_entry = workspace.map_source(source_entry.root)
    work_entry.mkdir(parents=True, exist_ok=True)
    output_paths = _output_paths(
        execution,
        entry_root=work_entry,
        project_root=workspace.work_project,
    )
    command, captures = _execution_command(
        execution,
        source_entry=source_entry.root,
        workspace=workspace,
        output_paths=output_paths,
        generated=generated,
    )
    stdout_path, stderr_path = _diagnostic_paths(workspace, entry_id, execution_id)
    checkpoint_path = _checkpoint_path(workspace, entry_id, execution_id)
    return _PreparedExecution(
        entry_id,
        execution_id,
        execution,
        work_entry,
        output_paths,
        tuple(command),
        _execution_environment(execution, workspace),
        captures,
        stdout_path,
        stderr_path,
        checkpoint_path,
    )


def _active_checkpoint(
    prepared: _PreparedExecution, workspace: ReproductionWorkspace
) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        prepared.entry,
        prepared.execution_id,
        "active",
        prepared.checkpoint.relative_to(workspace.run_root).as_posix(),
        None,
        (),
    )


def _confined_command(
    backend: ConfinementBackend,
    command: Sequence[str],
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
) -> list[str]:
    backend.preflight()
    readonly = _readonly_boundaries(plan, workspace)
    return backend.command(
        command,
        writable_roots=(workspace.work_project, workspace.runtime_root),
        readonly_paths=readonly,
    )


def _run_prepared(
    prepared: _PreparedExecution,
    command: Sequence[str],
    workspace: ReproductionWorkspace,
    stop_requested: Callable[[], bool],
) -> _ProcessOutcome:
    registry = _WorkerRegistry(prepared.execution_id, workspace.run_id)
    try:
        with ExitStack() as stack:
            launched = _launch_process(prepared, command, stack)
            registry.register_root(launched.process.pid)
            outcome = _monitor_process(launched.process, registry, stop_requested)
            failure_code, failure_message = _finish_streams(
                launched, outcome.failure_code, outcome.failure_message
            )
    except BaseException:
        registry.stop_all()
        raise
    return _ProcessOutcome(
        outcome.returncode,
        outcome.stopped,
        failure_code,
        failure_message,
        registry.records(),
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
    stop_requested: Callable[[], bool],
) -> _ProcessOutcome:
    stopped = False
    failure_code: str | None = None
    failure_message: str | None = None
    while process.poll() is None:
        registry.refresh()
        if stop_requested():
            stopped = True
            survivors = registry.stop_all()
            if survivors:
                failure_code = "worker_cleanup_incomplete"
                failure_message = _survivor_message(survivors)
            break
        time.sleep(POLL_SECONDS)
    returncode = process.poll()
    if returncode is None:
        try:
            returncode = process.wait(timeout=FORCED_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            returncode = None
    registry.refresh()
    if not stopped:
        survivors = registry.wait_for_descendants(WORKER_SETTLE_SECONDS)
        if survivors:
            remaining = registry.stop_all()
            failure_code = "worker_survived"
            failure_message = _survivor_message(remaining or survivors)
    return _ProcessOutcome(
        returncode, stopped, failure_code, failure_message, registry.records()
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
    outcome: _ProcessOutcome, observed_outputs: int, declared_outputs: int
) -> tuple[str, str | None, str | None]:
    if outcome.stopped:
        return (
            "partial",
            outcome.failure_code or "stop_requested",
            outcome.failure_message or "Reproduction was stopped by request.",
        )
    if outcome.failure_code is not None:
        return "partial", outcome.failure_code, outcome.failure_message
    if outcome.returncode != 0:
        return (
            "partial",
            "execution_failed",
            f"execution exited with status {outcome.returncode}",
        )
    if observed_outputs != declared_outputs:
        return (
            "partial",
            "output_missing",
            "one or more declared outputs were not generated",
        )
    return "complete", None, None


def execute_reproduction_plan(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
    control: ExecutionControl = ExecutionControl(),
) -> ExecutionBatch:
    """Execute every runnable component without crossing dependency failures."""

    from .reproduction_planner import verify_reproduction_snapshot

    verify_reproduction_snapshot(log, plan)
    ordered = sorted(plan.executions, key=_execution_order)
    _require_execution_order(ordered)
    attempts: list[ExecutionAttempt] = []
    reused: list[str] = []
    skips: list[Mapping[str, object]] = []
    unavailable: set[str] = set()
    backend = control.confinement or DarwinSeatbelt()
    generated = _generated_output_paths(log, plan, workspace)
    for planned in ordered:
        if control.stop_requested():
            return ExecutionBatch(tuple(attempts), tuple(reused), tuple(skips), True)
        entry_id = _required_string(planned, "entry")
        identity = _required_string(planned, "execution_id")
        reference = _execution_reference(entry_id, identity)
        dependencies = _dependency_references(planned)
        blocked = tuple(sorted(set(dependencies) & unavailable))
        if blocked:
            skips.append(
                {
                    "depends_on": list(blocked),
                    "entry": entry_id,
                    "execution_id": identity,
                    "reason": "dependency_failed",
                }
            )
            unavailable.add(reference)
            continue
        checkpoint = _load_checkpoint(workspace, entry_id, identity)
        if checkpoint is not None and checkpoint.state == "complete":
            if not control.resume or not _checkpoint_outputs_current(
                checkpoint, log, workspace, entry_id, identity
            ):
                raise ActionError(
                    "reproduction.checkpoint.changed",
                    f"completed checkpoint is not reusable: {reference}",
                )
            reused.append(reference)
            control.progress("reused", identity, None)
            continue
        control.progress("started", identity, None)
        attempt = execute_planned_recipe(
            log,
            plan,
            planned,
            workspace,
            ExecutionControl(
                resume=control.resume and checkpoint is not None,
                stop_requested=control.stop_requested,
                confinement=backend,
                generated_paths=generated,
                progress=control.progress,
            ),
        )
        attempts.append(attempt)
        control.progress("finished", identity, attempt)
        if attempt.stopped:
            return ExecutionBatch(tuple(attempts), tuple(reused), tuple(skips), True)
        if attempt.checkpoint.state != "complete":
            unavailable.add(reference)
    return ExecutionBatch(tuple(attempts), tuple(reused), tuple(skips), False)


def completed_execution_attempts(
    log: LogContext,
    plan: ReproductionPlan,
    workspace: ReproductionWorkspace,
) -> tuple[ExecutionAttempt, ...]:
    """Load every complete planned checkpoint as a comparison-ready attempt."""

    results: list[ExecutionAttempt] = []
    generated = _generated_output_paths(log, plan, workspace)
    for planned in sorted(plan.executions, key=_execution_order):
        entry_id = _required_string(planned, "entry")
        identity = _required_string(planned, "execution_id")
        checkpoint = _load_checkpoint(workspace, entry_id, identity)
        if checkpoint is None or checkpoint.state != "complete":
            continue
        if not _checkpoint_outputs_current(
            checkpoint, log, workspace, entry_id, identity
        ):
            raise ActionError(
                "reproduction.checkpoint.changed",
                f"completed checkpoint is not current: {entry_id}:{identity}",
            )
        prepared = _prepare_execution(log, planned, workspace, generated)
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
    workspace: ReproductionWorkspace, entry: str, execution_id: str
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
        value = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.checkpoint.invalid", str(error)) from error
    fields = {"completed_at", "execution_id", "outputs", "path", "state"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint fields are invalid"
        )
    state = value.get("state")
    completed_at = value.get("completed_at")
    outputs = value.get("outputs")
    expected_path = path.relative_to(workspace.run_root).as_posix()
    if (
        value.get("execution_id") != execution_id
        or value.get("path") != expected_path
        or state not in {"active", "complete", "partial"}
        or completed_at is not None
        and not isinstance(completed_at, str)
        or not isinstance(outputs, list)
        or len(outputs) > 256
    ):
        raise ActionError(
            "reproduction.checkpoint.invalid", "checkpoint content is invalid"
        )
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
    if state == "complete" and not isinstance(completed_at, str):
        raise ActionError(
            "reproduction.checkpoint.invalid", "complete checkpoint has no timestamp"
        )
    return ExecutionCheckpoint(
        entry,
        execution_id,
        cast(str, state),
        expected_path,
        completed_at,
        tuple(decoded),
    )


def _checkpoint_outputs_current(
    checkpoint: ExecutionCheckpoint,
    log: LogContext,
    workspace: ReproductionWorkspace,
    entry_id: str,
    execution_id: str,
) -> bool:
    entry = resolve_entry(log, entry_id)
    state = load_pyrun_state(
        entry.root / "pyrun.json",
        entry_root=entry.root,
        project_root=workspace.source_project,
    )
    execution = state.executions.get(execution_id)
    if execution is None:
        return False
    paths = _output_paths(
        execution,
        entry_root=workspace.map_source(entry.root),
        project_root=workspace.work_project,
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
    parameters, captures = _recorded_parameter_layout(
        execution.recipe.parameters,
        source_entry=source_entry,
        workspace=workspace,
        output_paths=output_paths,
    )
    bindings = _output_argument_bindings(
        parameters,
        source_entry=source_entry,
        workspace=workspace,
        output_paths=output_paths,
        captured_outputs=frozenset(captures.values()),
    )
    arguments = []
    for index, value in enumerate(parameters):
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


def _recorded_parameter_layout(
    parameters: Sequence[str],
    *,
    source_entry: Path,
    workspace: ReproductionWorkspace,
    output_paths: Mapping[str, Path],
) -> tuple[tuple[str, ...], Mapping[str, Path]]:
    """Separate runner-owned capture/environment prefixes from child arguments."""

    index = 0
    captures: dict[str, Path] = {}
    runner_prefix = False
    while index < len(parameters) and parameters[index] in {
        *PYRUN_CAPTURE_STREAMS,
        PYRUN_ENV_OPTION,
    }:
        runner_prefix = True
        option = parameters[index]
        if index + 1 >= len(parameters):
            raise ActionError(
                "reproduction.output.binding_invalid",
                "recorded runner option has no value",
            )
        value = parameters[index + 1]
        if option in PYRUN_CAPTURE_STREAMS:
            try:
                identity = portable_output_path(
                    value,
                    entry_root=source_entry,
                    project_root=workspace.source_project,
                    authored=True,
                )
            except MechanicalContractError as error:
                raise ActionError(
                    "reproduction.output.binding_invalid", str(error)
                ) from error
            destination = output_paths.get(identity)
            if destination is None or option in captures:
                raise ActionError(
                    "reproduction.output.binding_invalid",
                    f"capture output has no unique declaration: {identity}",
                )
            captures[option] = destination
        index += 2
    if runner_prefix:
        if index >= len(parameters) or parameters[index] != "--":
            raise ActionError(
                "reproduction.output.binding_invalid",
                "recorded runner prefix has no separator",
            )
        index += 1
    return tuple(parameters[index:]), captures


def _output_argument_bindings(
    parameters: Sequence[str],
    *,
    source_entry: Path,
    workspace: ReproductionWorkspace,
    output_paths: Mapping[str, Path],
    captured_outputs: frozenset[Path],
) -> Mapping[int, str]:
    """Bind every ordinary output identity to one exact child argument."""

    candidates: dict[str, list[tuple[int, str | None]]] = {
        identity: []
        for identity, path in output_paths.items()
        if path not in captured_outputs
    }
    for index, parameter in enumerate(parameters):
        raw = parameter
        prefix: str | None = None
        if parameter.startswith("-") and "=" in parameter:
            prefix, raw = parameter.split("=", 1)
        if raw.startswith("-"):
            continue
        try:
            identity = portable_output_path(
                raw,
                entry_root=source_entry,
                project_root=workspace.source_project,
                authored=True,
            )
        except MechanicalContractError:
            continue
        if identity in candidates:
            candidates[identity].append((index, prefix))
    result: dict[int, str] = {}
    for identity, occurrences in candidates.items():
        if len(occurrences) != 1:
            raise ActionError(
                "reproduction.output.binding_invalid",
                f"output does not have one unambiguous parameter: {identity}",
            )
        index, prefix = occurrences[0]
        destination = str(output_paths[identity])
        result[index] = f"{prefix}={destination}" if prefix is not None else destination
    return result


def _generated_output_paths(
    log: LogContext, plan: ReproductionPlan, workspace: ReproductionWorkspace
) -> Mapping[Path, tuple[Path, str]]:
    """Map retained generated identities to run-local graph paths."""

    result: dict[Path, tuple[Path, str]] = {}
    for planned in plan.executions:
        entry = resolve_entry(log, _required_string(planned, "entry"))
        state = load_pyrun_state(
            entry.root / "pyrun.json",
            entry_root=entry.root,
            project_root=workspace.source_project,
        )
        execution = state.executions.get(_required_string(planned, "execution_id"))
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
    execution: PyrunExecution, workspace: ReproductionWorkspace
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(dict(execution.recipe.environment))
    roots = {
        "MPLCONFIGDIR": workspace.runtime_root / "matplotlib",
        "XDG_CACHE_HOME": workspace.runtime_root / "cache",
        "MATLAB_PREFDIR": workspace.runtime_root / "matlab",
        "TMPDIR": workspace.runtime_root / "tmp",
    }
    for path in roots.values():
        path.mkdir(exist_ok=True)
    environment.update({name: str(path) for name, path in roots.items()})
    environment[RUNNER_MARKER] = workspace.run_id
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
    def __init__(self, execution_id: str, run_id: str):
        self.execution_id = execution_id
        self.marker = f"{RUNNER_MARKER}={run_id}"
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
    workspace: ReproductionWorkspace, entry: str, execution_id: str
) -> tuple[Path, Path]:
    stem = f"{entry}-{execution_id.removeprefix('pyrun-exec/v1:')}"
    return (
        workspace.diagnostics_root / f"{stem}.stdout.log",
        workspace.diagnostics_root / f"{stem}.stderr.log",
    )


def _checkpoint_path(
    workspace: ReproductionWorkspace, entry: str, execution_id: str
) -> Path:
    digest = execution_id.removeprefix("pyrun-exec/v1:")
    return workspace.run_root / "checkpoints" / f"{entry}-{digest}.json"


def _write_checkpoint(path: Path, checkpoint: ExecutionCheckpoint) -> None:
    payload = (
        json.dumps(checkpoint.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
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
