"""Physical job locking, lifecycle requests and process-ownership guardrails.

The native work job owns its schema and research facts. This module owns only
the genuine filesystem/SQLite safety and scheduler/process control contracts.
No accepted-plan decoder, comparison packet or result projection belongs here.
"""

from __future__ import annotations

import fcntl
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal, NoReturn, Sequence, cast

from .context import ENTRY_ID_RE
from .reproduction_paths import (
    canonical_run_root,
    is_canonical_run_path,
    job_state_path,
    project_tmp_relative,
)

MAX_JOB_STORE_BYTES = 256 * 1024 * 1024
MAX_EXECUTION_WORKERS = 1_024
MAX_RUN_WORKERS = 4_096
MAX_STRING_BYTES = 8 * 1024
MAX_PATH_BYTES = 2 * 1024
JOB_LOCK_NAME = "state.lock"
_COMPANIONS = ("-journal", "-wal", "-shm")
_EXECUTION_ID_RE = re.compile(r"pyrun-exec/v2:[0-9a-f]{64}\Z")
_CID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


class JobStoreError(RuntimeError):
    """Base class for a bounded durable job-storage failure."""

    code = "reproduction.run.invalid"

    def __init__(self, message: str, *, code: str | None = None):
        self.code = code or type(self).code
        super().__init__(message[:1024])


class JobStoreMissingError(JobStoreError):
    """The requested current-format job store is absent."""

    code = "reproduction.run.missing"


class JobStoreExistsError(JobStoreError):
    """A run has already accepted its single immutable plan."""

    code = "reproduction.run.exists"


class JobStoreBusyError(JobStoreError):
    """The run-state mutex or SQLite writer is already held."""

    code = "reproduction.run.busy"


class JobStoreSymlinkError(JobStoreError):
    """A run-state path or SQLite companion is a symlink."""

    code = "reproduction.run.path_unsafe"


class JobStoreUnsupportedError(JobStoreError):
    """The durable database uses an unsupported schema version."""

    code = "reproduction.run.unsupported"


class JobStoreMalformedError(JobStoreError):
    """The durable database or a selected typed projection is malformed."""

    code = "reproduction.run.invalid"


class JobStoreInvariantError(JobStoreError):
    """Stored rows violate a durable cross-row invariant."""

    code = "reproduction.run.invariant"


class JobStoreTransitionError(JobStoreError):
    """A compare-and-set lifecycle transition did not match prior state."""

    code = "reproduction.run.transition"


@dataclass(frozen=True)
class RunIdentity:
    """The immutable identity and canonical location of one accepted run."""

    run_id: str
    run_path: str


@dataclass(frozen=True)
class ExecutionPermitAttachment:
    """Compare-and-set request that attaches one scheduler permit."""

    entry: str
    cid: str
    execution_id: str
    permit_id: str
    checkpointed_at: str
    expected_state: Literal["absent", "stopped"] = "absent"


@dataclass(frozen=True)
class ExecutionStart:
    """Compare-and-set request that records the immediately pending launch."""

    entry: str
    cid: str
    execution_id: str
    permit_id: str
    checkpointed_at: str
    started_at: str
    scratch_path: str
    elapsed_seconds: float = 0.0
    stdout_path: str | None = None
    stderr_path: str | None = None


@dataclass(frozen=True)
class ExecutionIdentity:
    """One accepted execution identity within the run selected by its path."""

    entry: str
    cid: str
    execution_id: str


@dataclass(frozen=True)
class ExecutionReadiness:
    """One execution's bounded direct-dependency scheduling projection."""

    identity: ExecutionIdentity
    disposition: Literal["waiting", "ready", "dependency_failed"]
    pending_dependencies: tuple[ExecutionIdentity, ...]
    failed_dependencies: tuple[ExecutionIdentity, ...]


@dataclass(frozen=True)
class RunControl:
    """Small lifecycle projection polled by execution control paths."""

    status: str | None
    phase: str | None
    stop_requested_at: str | None
    operational_code: str | None


@dataclass(frozen=True)
class AcceptedSchedulingProjection:
    """Bounded immutable scheduling facts for one accepted execution."""

    run_id: str
    identity: ExecutionIdentity
    plan_order: int
    kind: Literal["ordinary", "exclusive"]
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    run_path: str
    writable_paths: tuple[str, ...]


@dataclass(frozen=True)
class WorkerRecord:
    """One supervised worker observation retained for a run."""

    worker_id: str
    parent_worker_id: str | None
    pid: int
    state: Literal["running", "exited"]
    registered_at: str
    last_observed_at: str


@dataclass(frozen=True)
class RecoveryWorkerObservation:
    """One process still carrying this run's durable worker marker."""

    identity: ExecutionIdentity | None
    worker: WorkerRecord


@dataclass(frozen=True)
class RunOwner:
    """The single current supervisor identity for a run."""

    supervisor_pid: int
    state: Literal["running", "stopped", "exited"]
    registered_at: str
    last_observed_at: str


@dataclass(frozen=True)
class RunStopRequest:
    """First durable user stop request for one active run."""

    requested_at: str


@dataclass(frozen=True)
class RunFailure:
    """Exact operational failure intent that requires quiescent cleanup."""

    code: str
    message: str
    recorded_at: str


@dataclass(frozen=True)
class RunStopCompletion:
    """Time at which quiescent stop cleanup reached its terminal state."""

    terminal_at: str


@dataclass(frozen=True)
class RunResumeRequest:
    """Activation request for one quiescent user-stopped run."""

    resumed_at: str


def recognize_run_directory(
    run_root: Path, project_root: Path
) -> Literal["current", "historical_unsupported", "absent"]:
    """Classify one project-confined canonical run without opening its state."""

    try:
        canonical_run_root(run_root, project_root, require_exists=True)
    except (OSError, RuntimeError):
        return "absent"
    return (
        "current" if (run_root / "state.sqlite").exists() else "historical_unsupported"
    )


def _before_commit(_operation: str, _db: sqlite3.Connection) -> None:
    """Test seam for interrupting one transaction immediately before commit."""


def _bounded_string(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= MAX_STRING_BYTES
    )


def _check_database_size(db: sqlite3.Connection) -> None:
    """Reject a transaction before commit when its allocated data is oversized."""

    page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    if page_count * page_size > MAX_JOB_STORE_BYTES:
        raise JobStoreInvariantError("durable job store crossed its byte bound")


def _check_store_size(path: Path) -> None:
    if _store_size(path) > MAX_JOB_STORE_BYTES:
        raise JobStoreInvariantError("durable job store crossed its byte bound")


def _checked_state_path(run_root: Path, *, writable: bool) -> Path:
    try:
        path = job_state_path(run_root)
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error
    for candidate in (
        path,
        *(Path(str(path) + suffix) for suffix in _COMPANIONS),
        run_root / JOB_LOCK_NAME,
    ):
        if candidate.is_symlink():
            raise JobStoreSymlinkError(f"unsafe job-state path: {candidate}")
    if not writable and not path.is_file():
        raise JobStoreMissingError(f"job state is absent: {path}")
    return path


@contextmanager
def _job_mutex(run_root: Path) -> Iterator[None]:
    lock_path = run_root / JOB_LOCK_NAME
    if lock_path.is_symlink():
        raise JobStoreSymlinkError(f"unsafe job-state lock: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o644)
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise JobStoreBusyError(f"job state is active: {lock_path}") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open_database(
    path: Path,
    *,
    mode: Literal["rw", "rwc"],
    allow_uninitialized: bool = False,
    expected_version: int,
) -> sqlite3.Connection:
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(
            path.as_uri() + f"?mode={mode}", uri=True, timeout=0, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0 and allow_uninitialized:
            return db
        if version != expected_version:
            raise JobStoreUnsupportedError(
                f"job store version {version} is unsupported"
            )
        _check_store_size(path)
        return db
    except BaseException as error:
        if db is not None:
            db.close()
        _raise_storage_error(error)


def _raise_storage_error(error: BaseException) -> NoReturn:
    if isinstance(error, JobStoreError):
        raise error
    if isinstance(error, sqlite3.OperationalError):
        raise _sqlite_error_code(error)(str(error)) from error
    if isinstance(error, sqlite3.Error):
        raise JobStoreMalformedError(str(error)) from error
    if isinstance(error, (KeyError, OverflowError, TypeError, ValueError)):
        raise JobStoreMalformedError(str(error)) from error
    raise error


def _regular_run_root(run_root: Path) -> Path:
    try:
        if run_root.is_symlink() or not run_root.is_dir():
            raise JobStoreSymlinkError(
                f"run root is not a regular directory: {run_root}"
            )
        return run_root.resolve(strict=True)
    except OSError as error:
        if isinstance(error, JobStoreError):
            raise
        raise JobStoreMalformedError(str(error)) from error


def _remove_failed_creation(path: Path) -> None:
    for candidate in (path, *(Path(str(path) + suffix) for suffix in _COMPANIONS)):
        try:
            if (
                candidate.exists()
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                candidate.unlink()
        except OSError:
            pass


def _require_quiescent_run(db: sqlite3.Connection, run_id: str) -> None:
    active = int(
        db.execute(
            "SELECT COUNT(*) FROM execution_checkpoints WHERE run_id=? "
            "AND (state='active' OR permit_id IS NOT NULL)",
            (run_id,),
        ).fetchone()[0]
    )
    running = int(
        db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? AND state='running'",
            (run_id,),
        ).fetchone()[0]
    )
    if active or running:
        raise JobStoreTransitionError("run retains active ownership")


def _require_recovery_workers_present(
    db: sqlite3.Connection,
    run_id: str,
    resolved: Sequence[tuple[int | None, WorkerRecord]],
) -> None:
    retained_ids = {worker.worker_id for _command_pk, worker in resolved}
    if not retained_ids:
        return
    placeholders = ",".join("?" for _item in retained_ids)
    count = int(
        db.execute(
            f"SELECT COUNT(*) FROM workers WHERE run_id=? "
            f"AND worker_id IN ({placeholders}) AND state='running'",
            (run_id, *sorted(retained_ids)),
        ).fetchone()[0]
    )
    if count != len(retained_ids):
        raise JobStoreTransitionError("recovery worker replacement lost its identity")


def _require_recovery_workers_unchanged(
    db: sqlite3.Connection,
    run_id: str,
    resolved: Sequence[tuple[int | None, WorkerRecord]],
) -> None:
    existing = {
        cast(str, row["worker_id"]): row
        for row in db.execute(
            "SELECT worker_id, pid, registered_at, last_observed_at "
            "FROM workers WHERE run_id=?",
            (run_id,),
        )
    }
    for _command_pk, worker in resolved:
        prior = existing.get(worker.worker_id)
        if prior is not None and (
            int(prior["pid"]) != worker.pid
            or worker.last_observed_at < prior["last_observed_at"]
        ):
            raise JobStoreTransitionError("recovery worker identity fields changed")


def _require_relative_path(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise JobStoreInvariantError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise JobStoreInvariantError(f"{label} is invalid")


def _require_scratch_path(value: str, label: str) -> None:
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise JobStoreInvariantError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or path.parts[:3] != ("/", "private", "tmp")
        or len(path.parts) <= 3
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise JobStoreInvariantError(f"{label} is invalid")


def _require_timestamp(value: str, label: str) -> None:
    try:
        valid = (
            isinstance(value, str)
            and _TIMESTAMP_RE.fullmatch(value) is not None
            and datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
            == value
        )
    except ValueError:
        valid = False
    if not valid:
        raise JobStoreInvariantError(f"{label} is invalid")


def _sole_row(
    db: sqlite3.Connection, query: str, parameters: Sequence[object] = ()
) -> sqlite3.Row:
    rows = db.execute(query, tuple(parameters)).fetchall()
    if len(rows) != 1:
        raise JobStoreInvariantError("durable job store has no unique run row")
    return rows[0]


def _validate_execution_start(start: ExecutionStart) -> None:
    _require_execution_identity(start.entry, start.cid, start.execution_id)
    if not _bounded_string(start.permit_id):
        raise JobStoreInvariantError("execution start permit is empty")
    _require_timestamp(start.checkpointed_at, "checkpoint time")
    _require_timestamp(start.started_at, "start time")
    if (
        isinstance(start.elapsed_seconds, bool)
        or not isinstance(start.elapsed_seconds, (int, float))
        or not math.isfinite(start.elapsed_seconds)
        or start.elapsed_seconds < 0
    ):
        raise JobStoreInvariantError("execution elapsed time is invalid")
    for value, label in (
        (start.stdout_path, "stdout path"),
        (start.stderr_path, "stderr path"),
    ):
        if value is not None:
            _require_relative_path(value, label)
    _require_scratch_path(start.scratch_path, "scratch path")


def _validate_permit_attachment(attachment: ExecutionPermitAttachment) -> None:
    _require_execution_identity(
        attachment.entry, attachment.cid, attachment.execution_id
    )
    if not _bounded_string(attachment.permit_id):
        raise JobStoreInvariantError("execution permit is invalid")
    _require_timestamp(attachment.checkpointed_at, "permit attachment time")


def _validate_recovery_workers(workers: Sequence[WorkerRecord]) -> None:
    """Validate the bounded run-wide process scan used for durable exclusion."""

    _validate_worker_forest(workers, MAX_RUN_WORKERS, "run")


def _validate_run_failure(failure: RunFailure) -> None:
    if not _bounded_string(failure.code) or not _bounded_string(failure.message):
        raise JobStoreInvariantError("run failure is incomplete")
    _require_timestamp(failure.recorded_at, "run failure time")


def _validate_run_location(
    run_root: Path,
    project_root: Path,
    run_path: str,
    accepted_at: str,
    run_id: str,
) -> None:
    try:
        actual = project_tmp_relative(run_root, project_root)
    except OSError as error:
        raise JobStoreInvariantError(str(error)) from error
    logical = PurePosixPath(run_path)
    if (
        not is_canonical_run_path(run_path)
        or actual != run_path
        or logical.parts[2] != accepted_at[:10]
        or not logical.name.endswith(f"-{run_id}")
    ):
        raise JobStoreInvariantError(
            "accepted run location does not match its identity"
        )


def _validate_run_owner(owner: RunOwner) -> None:
    if (
        isinstance(owner.supervisor_pid, bool)
        or owner.supervisor_pid <= 0
        or owner.state not in {"running", "stopped", "exited"}
    ):
        raise JobStoreInvariantError("run owner is invalid")
    _require_timestamp(owner.registered_at, "owner registration time")
    _require_timestamp(owner.last_observed_at, "owner observation time")
    if owner.last_observed_at < owner.registered_at:
        raise JobStoreInvariantError("owner observation precedes registration")


def _validate_worker_record(worker: WorkerRecord) -> None:
    if (
        not _bounded_string(worker.worker_id)
        or isinstance(worker.pid, bool)
        or worker.pid <= 0
        or worker.state not in {"running", "exited"}
        or worker.parent_worker_id == worker.worker_id
        or (
            worker.parent_worker_id is not None
            and not _bounded_string(worker.parent_worker_id)
        )
    ):
        raise JobStoreInvariantError("worker record is invalid")
    _require_timestamp(worker.registered_at, "worker registration time")
    _require_timestamp(worker.last_observed_at, "worker observation time")
    if worker.last_observed_at < worker.registered_at:
        raise JobStoreInvariantError("worker observation precedes registration")


def _validate_workers(workers: Sequence[WorkerRecord]) -> None:
    _validate_worker_forest(workers, MAX_EXECUTION_WORKERS, "execution")


def _store_size(path: Path) -> int:
    """Return bounded SQLite data bytes including safe transaction companions."""

    try:
        return sum(
            candidate.stat().st_size
            for candidate in (
                path,
                *(Path(str(path) + suffix) for suffix in _COMPANIONS),
            )
            if candidate.exists()
        )
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error


def _sqlite_error_code(error: sqlite3.OperationalError) -> type[JobStoreError]:
    text = str(error).lower()
    if "locked" in text or "busy" in text:
        return JobStoreBusyError
    return JobStoreMalformedError


def _require_execution_identity(entry: str, cid: str, execution_id: str) -> None:
    if (
        ENTRY_ID_RE.fullmatch(entry) is None
        or _CID_RE.fullmatch(cid) is None
        or _EXECUTION_ID_RE.fullmatch(execution_id) is None
    ):
        raise JobStoreInvariantError("execution identity is invalid")


def _validate_worker_forest(
    workers: Sequence[WorkerRecord], limit: int, scope: str
) -> None:
    if len(workers) > limit:
        raise JobStoreInvariantError(f"{scope} worker count crossed its bound")
    identities = [item.worker_id for item in workers]
    if len(identities) != len(set(identities)):
        raise JobStoreInvariantError("worker identity is duplicated")
    known = set(identities)
    for worker in workers:
        _validate_worker_record(worker)
        if worker.parent_worker_id is not None and worker.parent_worker_id not in known:
            raise JobStoreInvariantError("worker record is invalid")
    parents = {worker.worker_id: worker.parent_worker_id for worker in workers}
    for worker_id in identities:
        visited: set[str] = set()
        current: str | None = worker_id
        while current is not None:
            if current in visited:
                raise JobStoreInvariantError("worker parent relationship is cyclic")
            visited.add(current)
            current = parents[current]
