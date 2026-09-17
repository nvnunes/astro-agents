"""Native accepted work and atomic attempt facts for replacement durable jobs.

This authority stores Plan13 directly, separately from genuine scheduler state.
Job5 owns ordinary launch and lifecycle control; older jobs are never decoded.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, cast

from research_log_data import parse_fingerprint

from .reproduction_accepted_storage import (
    ACCEPTED_WORK_DDL,
    _json,
    _load_accepted_work,
    _payload,
    _write_accepted_work,
)
from .reproduction_command_results import blocked_command_observation
from .reproduction_completed_run import RunCompletion, complete_saved_run
from .reproduction_domain import (
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    ReproductionDomainError,
    ReproductionProblem,
    WorkSelection,
)
from .reproduction_job_control import (
    MAX_RUN_WORKERS,
    AcceptedSchedulingProjection,
    ExecutionIdentity,
    ExecutionPermitAttachment,
    ExecutionReadiness,
    ExecutionStart,
    JobStoreExistsError,
    JobStoreInvariantError,
    JobStoreMalformedError,
    JobStoreTransitionError,
    RecoveryWorkerObservation,
    RunControl,
    RunFailure,
    RunIdentity,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
    WorkerRecord,
    _before_commit,
    _bounded_string,
    _check_database_size,
    _check_store_size,
    _checked_state_path,
    _job_mutex,
    _open_database,
    _raise_storage_error,
    _regular_run_root,
    _remove_failed_creation,
    _require_quiescent_run,
    _require_recovery_workers_present,
    _require_recovery_workers_unchanged,
    _require_relative_path,
    _require_scratch_path,
    _require_timestamp,
    _sole_row,
    _validate_execution_start,
    _validate_permit_attachment,
    _validate_recovery_workers,
    _validate_run_failure,
    _validate_run_location,
    _validate_run_owner,
    _validate_worker_record,
    _validate_workers,
)
from .reproduction_observation_storage import (
    OBSERVATION_DDL,
    load_observation,
    load_observation_problems,
    write_artifact_observation,
    write_command_observation,
)
from .reproduction_run import RUN_ID_RE, ArtifactResult, CommandResult
from .reproduction_saved_run import MAX_WORK_RECORDS, SavedRun
from .reproduction_work_plan import MAX_PLAN_BYTES, PLAN_SCHEMA, ReproductionPlan

WORK_JOB_VERSION = 5
_THREAD_LOCK = threading.RLock()
PERMIT_ADMISSION_PHASES = frozenset(
    {"accepted", "planning", "preflight", "executing", "comparing"}
)

_CONTROL_DDL = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    accepted_at TEXT NOT NULL,
    run_path TEXT NOT NULL,
    project_root TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    diagnostics_path TEXT NOT NULL,
    plan_header TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE run_state (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    phase TEXT CHECK(phase IN
        ('accepted','planning','preflight','executing','comparing','publishing','stopping')),
    status TEXT CHECK(status IN ('complete','stopped','failed')),
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    resumed_at TEXT,
    stopped_at TEXT,
    stop_requested_at TEXT,
    operational_code TEXT,
    operational_message TEXT,
    operational_recorded_at TEXT
) WITHOUT ROWID;
CREATE TABLE run_owner (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    supervisor_pid INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running','stopped','exited')),
    registered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE run_publication (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    finished_at TEXT NOT NULL,
    result_generation INTEGER CHECK(result_generation > 0),
    report_generation INTEGER CHECK(report_generation > 0)
) WITHOUT ROWID;
CREATE TABLE execution_checkpoints (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','stopped','completed')),
    permit_id TEXT,
    released_permit_id TEXT,
    checkpointed_at TEXT NOT NULL,
    started_at TEXT,
    elapsed_seconds REAL NOT NULL CHECK(elapsed_seconds >= 0),
    scratch_path TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    stop_reason TEXT,
    stopped_outputs TEXT,
    PRIMARY KEY(run_id, command_pk),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_scheduling(run_id, command_pk)
) WITHOUT ROWID;
CREATE TABLE workers (
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    parent_worker_id TEXT,
    command_pk INTEGER,
    pid INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running','exited')),
    registered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    PRIMARY KEY(run_id, worker_id),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_scheduling(run_id, command_pk)
) WITHOUT ROWID;
CREATE TABLE requirement_acknowledgments (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    cleared_at TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_commands(run_id, command_pk)
) WITHOUT ROWID;
"""


@dataclass(frozen=True)
class WorkJobAcceptance:
    """Immutable run location and Plan13 facts, accepted in one transaction.

    ``project_root`` binds canonical temporary paths, including empty targets.
    Workspace/diagnostics are safe run-relative directories. There is no prior
    preview or old job authority: callers supply a freshly prepared typed plan.
    """

    run_id: str
    plan: ReproductionPlan
    accepted_at: str
    run_path: str
    project_root: Path
    workspace_path: str = "workspace"
    diagnostics_path: str = "diagnostics"


@dataclass(frozen=True)
class AttemptCompletion:
    """Actual research result plus the scheduler grant being completed.

    Result owns identity, invocation, finish time, outputs and problem links.
    Other fields concern only checkpoint timing and worker/permit lifecycle.
    Workers must all be exited before the result is durable or grant released.
    """

    result: CommandResult
    problems: tuple[ReproductionProblem, ...]
    permit_id: str
    checkpointed_at: str
    elapsed_seconds: float
    workers: tuple[WorkerRecord, ...] = ()


@dataclass(frozen=True)
class AttemptInterruption:
    """Interrupted grant's recovery/debug facts, never a terminal research result."""

    identity: ExecutionRef
    permit_id: str
    checkpointed_at: str
    elapsed_seconds: float
    reason: str
    outputs: dict[str, object]
    workers: tuple[WorkerRecord, ...] = ()


@dataclass(frozen=True)
class WorkCheckpoint:
    """Scheduler recovery facts; terminal research outcome lives in CommandResult."""

    identity: ExecutionRef
    state: str
    permit_id: str | None
    started_at: str | None
    elapsed_seconds: float
    scratch_path: str | None
    stdout_path: str | None
    stderr_path: str | None


@dataclass(frozen=True)
class WorkPermitCheckpoint:
    """Only the command key and grant lifecycle needed by the coordinator."""

    entry: str
    cid: str
    execution_id: str
    state: str
    permit_id: str
    released_permit_id: str | None


@dataclass(frozen=True)
class WorkSchedulerOwner:
    """Native supervisor, attached grants and live workers; no research diagnosis."""

    run_id: str
    status: str | None
    phase: str | None
    stop_requested_at: str | None
    owner: RunOwner | None
    checkpoints: tuple[WorkPermitCheckpoint, ...]
    running_workers: tuple[tuple[ExecutionIdentity | None, WorkerRecord], ...]


@dataclass(frozen=True)
class WorkPublication:
    """Frozen completion time and actual external publication acknowledgments.

    There is no copied saved-run payload or issue/status projection. Generations
    acknowledge the current shared domain and may advance on report-only retry.
    """

    finished_at: str
    result_generation: int | None
    report_generation: int | None


def _header(plan: ReproductionPlan) -> dict[str, object]:
    fields = plan.as_dict()
    return {
        "schema": PLAN_SCHEMA,
        **{key: fields[key] for key in ("summary", "target", "settings", "admission")},
    }


def _initialize(db: sqlite3.Connection) -> None:
    statement = ""
    schema = "\n".join((_CONTROL_DDL, ACCEPTED_WORK_DDL, OBSERVATION_DDL))
    for line in schema.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise JobStoreMalformedError("incomplete native job schema")
    db.execute(f"PRAGMA user_version={WORK_JOB_VERSION}")


def _validate_acceptance(run_root: Path, accepted: WorkJobAcceptance) -> None:
    if not isinstance(accepted.plan, ReproductionPlan):
        raise JobStoreInvariantError("acceptance requires a replacement typed plan")
    if not RUN_ID_RE.fullmatch(accepted.run_id):
        raise JobStoreInvariantError("accepted run ID is invalid")
    _require_timestamp(accepted.accepted_at, "accepted time")
    for value, label in (
        (accepted.workspace_path, "workspace path"),
        (accepted.diagnostics_path, "diagnostics path"),
    ):
        _require_relative_path(value, label)
    if not accepted.project_root.is_absolute() or any(
        work.project_root != accepted.project_root.as_posix()
        for work in accepted.plan.commands
    ):
        raise JobStoreInvariantError("accepted work has a different project root")
    _validate_run_location(
        run_root,
        accepted.project_root,
        accepted.run_path,
        accepted.accepted_at,
        accepted.run_id,
    )


def create_work_job(run_root: Path, accepted: WorkJobAcceptance) -> RunIdentity:
    """Atomically create Job5 and its immutable native plan at a canonical root.

    Existing jobs, unsafe paths and malformed acceptance fail without replacement.
    Failure before commit rolls back and removes only newly created state files.
    """

    run_root = _regular_run_root(run_root)
    _validate_acceptance(run_root, accepted)
    with _THREAD_LOCK, _job_mutex(run_root):
        path = _checked_state_path(run_root, writable=True)
        if path.exists():
            raise JobStoreExistsError(f"job state already exists: {path}")
        try:
            db = _open_database(
                path,
                mode="rwc",
                allow_uninitialized=True,
                expected_version=WORK_JOB_VERSION,
            )
            try:
                db.execute("BEGIN IMMEDIATE")
                _initialize(db)
                db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        accepted.run_id,
                        accepted.accepted_at,
                        accepted.run_path,
                        accepted.project_root.as_posix(),
                        accepted.workspace_path,
                        accepted.diagnostics_path,
                        _json(_header(accepted.plan)),
                    ),
                )
                _write_accepted_work(db, accepted.run_id, accepted.plan)
                db.execute(
                    "INSERT INTO run_state(run_id, phase, updated_at) "
                    "VALUES (?, 'accepted', ?)",
                    (accepted.run_id, accepted.accepted_at),
                )
                _check_database_size(db)
                _before_commit("work_acceptance", db)
                db.commit()
                _check_store_size(path)
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        except BaseException as error:
            _remove_failed_creation(path)
            _raise_storage_error(error)
    return RunIdentity(accepted.run_id, accepted.run_path)


@contextmanager
def open_work_job(run_root: Path) -> Iterator["LockedWorkJob"]:
    """Hold the job mutex and expose only typed, native Job5 operations.

    Opening authenticates accepted work and its canonical location. No earlier
    schema is decoded and no current registry/source is needed for reconstruction.
    """

    run_root = _regular_run_root(run_root)
    with _THREAD_LOCK, _job_mutex(run_root):
        path = _checked_state_path(run_root, writable=False)
        db = _open_database(path, mode="rw", expected_version=WORK_JOB_VERSION)
        try:
            rows = db.execute("SELECT * FROM runs LIMIT 2").fetchall()
            if len(rows) != 1:
                raise JobStoreMalformedError("native job requires exactly one run")
            run = rows[0]
            fields = _payload(run["plan_header"], set())
            if fields.pop("schema", None) != PLAN_SCHEMA:
                raise JobStoreMalformedError("native job has unsupported accepted plan")
            plan = _load_accepted_work(db, run["run_id"], fields)
            accepted = WorkJobAcceptance(
                run["run_id"],
                plan,
                run["accepted_at"],
                run["run_path"],
                Path(run["project_root"]),
                run["workspace_path"],
                run["diagnostics_path"],
            )
            _validate_acceptance(run_root, accepted)
            yield LockedWorkJob(db, accepted)
        finally:
            db.close()


class LockedWorkJob:
    """Native accepted-work authority under one run-state mutex.

    Terminal command facts have one owner in observation tables. Checkpoints
    store only active/stopped/completed state and scheduler recovery information.
    """

    def __init__(self, db: sqlite3.Connection, accepted: WorkJobAcceptance):
        self._db = db
        self.accepted = accepted

    @contextmanager
    def _transaction(self, operation: str) -> Iterator[None]:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield
            _check_database_size(self._db)
            _before_commit(operation, self._db)
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def request_run_stop(self, request: RunStopRequest) -> None:
        """Record stop intent without overwriting the first request time."""

        _require_timestamp(request.requested_at, "stop request time")
        with self._transaction("stop_request"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, stop_requested_at, operational_code "
                "FROM run_state",
            )
            if row["status"] is not None:
                raise JobStoreTransitionError("terminal run cannot request stop")
            if row["phase"] == "stopping":
                return
            if row["stop_requested_at"] is not None:
                raise JobStoreInvariantError("run stop intent has an invalid phase")
            if row["operational_code"] is not None:
                raise JobStoreTransitionError(
                    "failed run intent cannot become user stop"
                )
            changed = self._db.execute(
                "UPDATE run_state SET phase='stopping', stop_requested_at=?, "
                "updated_at=? WHERE run_id=? AND status IS NULL "
                "AND phase<>'stopping' AND stop_requested_at IS NULL "
                "AND operational_code IS NULL",
                (
                    request.requested_at,
                    request.requested_at,
                    row["run_id"],
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("stop request lost its state")

    def request_run_failure(self, failure: RunFailure) -> None:
        """Record one exact failed-terminal intent without losing user stop time."""

        _validate_run_failure(failure)
        with self._transaction("failure_request"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, operational_code, operational_message, "
                "operational_recorded_at FROM run_state",
            )
            if row["status"] is not None:
                raise JobStoreTransitionError("terminal run cannot request failure")
            current = (
                row["operational_code"],
                row["operational_message"],
                row["operational_recorded_at"],
            )
            requested = (failure.code, failure.message, failure.recorded_at)
            if current == requested and row["phase"] == "stopping":
                return
            if current == requested:
                raise JobStoreInvariantError("run failure intent has an invalid phase")
            if any(value is not None for value in current):
                raise JobStoreTransitionError("run has different operational failure")
            changed = self._db.execute(
                "UPDATE run_state SET phase='stopping', operational_code=?, "
                "operational_message=?, operational_recorded_at=?, updated_at=? "
                "WHERE run_id=? AND status IS NULL AND operational_code IS NULL "
                "AND operational_message IS NULL AND operational_recorded_at IS NULL",
                (*requested, failure.recorded_at, row["run_id"]),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("failure request lost its state")

    def finish_run_stop(self, completion: RunStopCompletion) -> None:
        """Derive failed or stopped only after active ownership is reconciled."""

        _require_timestamp(completion.terminal_at, "stop completion time")
        with self._transaction("stop_finish"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, operational_code, "
                "operational_message, operational_recorded_at FROM run_state",
            )
            if row["status"] is not None or row["phase"] != "stopping":
                raise JobStoreTransitionError("run is not stopping")
            run_id = cast(str, row["run_id"])
            _require_quiescent_run(self._db, run_id)
            operational = (
                row["operational_code"],
                row["operational_message"],
                row["operational_recorded_at"],
            )
            if any(value is None for value in operational) and any(
                value is not None for value in operational
            ):
                raise JobStoreInvariantError("run operational failure is incomplete")
            failed = all(value is not None for value in operational)
            status = "failed" if failed else "stopped"
            stopped_at = None if failed else completion.terminal_at
            finished_at = completion.terminal_at if failed else None
            changed = self._db.execute(
                "UPDATE run_state SET status=?, phase=NULL, stopped_at=?, "
                "finished_at=?, updated_at=? WHERE run_id=? AND status IS NULL "
                "AND phase='stopping'",
                (
                    status,
                    stopped_at,
                    finished_at,
                    completion.terminal_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("stop completion lost its state")

    def begin_run_resume(self, request: RunResumeRequest) -> None:
        """Reactivate one fully stopped fixed-plan run without replanning."""

        _require_timestamp(request.resumed_at, "resume time")
        with self._transaction("run_resume"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, finished_at, stopped_at, "
                "operational_code, operational_message, operational_recorded_at "
                "FROM run_state",
            )
            if (
                row["status"] != "stopped"
                or row["phase"] is not None
                or row["finished_at"] is not None
                or row["stopped_at"] is None
                or row["operational_code"] is not None
                or row["operational_message"] is not None
                or row["operational_recorded_at"] is not None
            ):
                raise JobStoreTransitionError("run is not stopped")
            run_id = cast(str, row["run_id"])
            _require_quiescent_run(self._db, run_id)
            changed = self._db.execute(
                "UPDATE run_state SET status=NULL, phase='accepted', "
                "stop_requested_at=NULL, resumed_at=?, stopped_at=NULL, updated_at=?, "
                "operational_code=NULL, operational_message=NULL, "
                "operational_recorded_at=NULL WHERE run_id=? AND status='stopped' "
                "AND phase IS NULL AND finished_at IS NULL AND stopped_at IS NOT NULL "
                "AND operational_code IS NULL AND operational_message IS NULL "
                "AND operational_recorded_at IS NULL",
                (
                    request.resumed_at,
                    request.resumed_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("resume lost its state")

    def begin_publication_resume(self, request: RunResumeRequest) -> None:
        """Reopen only a failed publication, keeping its frozen completion facts.

        A quiescent failed run must already have a publication journal and the
        publication failure intent. This clears operational failure, not accepted
        work or execution results. The caller registers the new supervisor before
        publishing; execution permits remain inadmissible in this phase.
        """

        _require_timestamp(request.resumed_at, "resume time")
        with self._transaction("publication_resume"):
            state = self.load_run_control()
            publication = self.load_publication()
            owner = self.load_run_owner()
            if (
                state.status != "failed"
                or state.operational_code != "reproduction.publication.failed"
                or publication is None
                or publication.report_generation is not None
                or (owner is not None and owner.state != "exited")
            ):
                raise JobStoreTransitionError("run has no failed publication to resume")
            _require_quiescent_run(self._db, self.accepted.run_id)
            self.load_completed_run(finished_at=publication.finished_at)
            changed = self._db.execute(
                "UPDATE run_state SET status=NULL, phase='publishing', "
                "finished_at=NULL, stopped_at=NULL, stop_requested_at=NULL, "
                "operational_code=NULL, operational_message=NULL, "
                "operational_recorded_at=NULL, resumed_at=?, updated_at=? "
                "WHERE run_id=? AND status='failed' AND phase IS NULL "
                "AND operational_code='reproduction.publication.failed'",
                (request.resumed_at, request.resumed_at, self.accepted.run_id),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("publication resume lost its state")

    def replace_run_owner(self, owner: RunOwner) -> None:
        """Replace the one supervisor row without creating owner history."""

        _validate_run_owner(owner)
        with self._transaction("run_owner"):
            run_id = cast(str, _sole_row(self._db, "SELECT run_id FROM runs")[0])
            current = self._db.execute(
                "SELECT supervisor_pid, last_observed_at FROM run_owner WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if (
                current is not None
                and int(current["supervisor_pid"]) == owner.supervisor_pid
                and owner.last_observed_at < current["last_observed_at"]
            ):
                raise JobStoreTransitionError("owner observation moved backward")
            self._db.execute(
                "INSERT INTO run_owner VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "supervisor_pid=excluded.supervisor_pid, state=excluded.state, "
                "registered_at=excluded.registered_at, "
                "last_observed_at=excluded.last_observed_at",
                (
                    run_id,
                    owner.supervisor_pid,
                    owner.state,
                    owner.registered_at,
                    owner.last_observed_at,
                ),
            )

    def _command_pk(self, identity: ExecutionRef) -> int:
        self.accepted.plan.schedule(identity)
        row = self._db.execute(
            "SELECT command_pk FROM accepted_work_commands WHERE run_id=? "
            "AND entry=? AND cid=? AND execution_id=?",
            (self.accepted.run_id, identity.entry, identity.cid, identity.execution_id),
        ).fetchone()
        if row is None:
            raise JobStoreInvariantError("scheduled command has no accepted identity")
        return row[0]

    def _checkpoint(self, command_pk: int) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM execution_checkpoints WHERE run_id=? AND command_pk=?",
            (self.accepted.run_id, command_pk),
        ).fetchone()

    def _require_active_work(self) -> None:
        row = self._db.execute(
            "SELECT status, phase, stop_requested_at, operational_code "
            "FROM run_state WHERE run_id=?",
            (self.accepted.run_id,),
        ).fetchone()
        if row is None or (
            row["status"] is not None
            or row["phase"] not in PERMIT_ADMISSION_PHASES
            or row["stop_requested_at"] is not None
            or row["operational_code"] is not None
        ):
            raise JobStoreTransitionError("run is not accepting active work")

    def load_run_control(self) -> RunControl:
        """Read bounded native operational intent, not research classifications."""

        row = _sole_row(
            self._db, "SELECT * FROM run_state WHERE run_id=?", (self.accepted.run_id,)
        )
        active_phases = {
            "accepted",
            "planning",
            "preflight",
            "executing",
            "comparing",
            "publishing",
            "stopping",
        }
        if row["status"] is None:
            if row["phase"] not in active_phases:
                raise JobStoreInvariantError("active run phase is invalid")
        elif (
            row["status"] not in {"complete", "stopped", "failed"}
            or row["phase"] is not None
        ):
            raise JobStoreInvariantError("terminal run state is invalid")
        for key in (
            "updated_at",
            "started_at",
            "finished_at",
            "resumed_at",
            "stopped_at",
            "stop_requested_at",
            "operational_recorded_at",
        ):
            if row[key] is not None:
                _require_timestamp(row[key], key)
        if row["operational_code"] is not None:
            _validate_run_failure(
                RunFailure(
                    row["operational_code"],
                    row["operational_message"],
                    row["operational_recorded_at"],
                )
            )
        elif (
            row["operational_message"] is not None
            or row["operational_recorded_at"] is not None
        ):
            raise JobStoreInvariantError("run operational failure is incomplete")
        return RunControl(
            row["status"],
            row["phase"],
            row["stop_requested_at"],
            row["operational_code"],
        )

    def load_run_owner(self) -> RunOwner | None:
        """Read the single genuine supervisor identity for exclusion and recovery."""

        row = self._db.execute(
            "SELECT * FROM run_owner WHERE run_id=?", (self.accepted.run_id,)
        ).fetchone()
        if row is None:
            return None
        owner = RunOwner(
            row["supervisor_pid"],
            row["state"],
            row["registered_at"],
            row["last_observed_at"],
        )
        _validate_run_owner(owner)
        return owner

    def load_accepted_scheduling(
        self, identity: ExecutionIdentity
    ) -> AcceptedSchedulingProjection:
        """Derive genuine claims from authenticated Plan13, never copied flags."""

        key = ExecutionRef(identity.entry, identity.cid, identity.execution_id)
        work = self.accepted.plan.command(key)
        row = self.accepted.plan.schedule(key)
        return AcceptedSchedulingProjection(
            self.accepted.run_id,
            identity,
            cast(int, row["order"]),
            "exclusive" if work.execution.exclusive else "ordinary",
            tuple(cast(list[str], row["read_paths"])),
            tuple(cast(list[str], row["write_paths"])),
            cast(str, row["run_path"]),
            tuple(cast(list[str], row["writable_paths"])),
        )

    def load_scheduler_owner(self) -> WorkSchedulerOwner:
        """Read bounded native coordinator proof without reconstructing results.

        Attached checkpoints are authenticated against accepted scheduling and
        actual result presence. Only running workers enter this proof; complete
        attempt/recovery detail remains owned by its separate native readers.
        """

        control = self.load_run_control()
        owner = self.load_run_owner()
        rows = self._db.execute(
            "SELECT p.*, c.entry, c.cid, c.execution_id, r.command_pk AS result_pk "
            "FROM execution_checkpoints p JOIN accepted_work_commands c "
            "USING(run_id, command_pk) LEFT JOIN run_command_results r "
            "USING(run_id, command_pk) WHERE p.run_id=? AND p.permit_id IS NOT NULL "
            "ORDER BY p.command_pk LIMIT 4097",
            (self.accepted.run_id,),
        ).fetchall()
        if len(rows) > 4096:
            raise JobStoreInvariantError("attached permit count crossed its bound")
        checkpoints = []
        for row in rows:
            _validate_checkpoint_recovery(row, self.accepted.diagnostics_path)
            if (row["state"] == "completed") != (row["result_pk"] is not None):
                raise JobStoreInvariantError(
                    "checkpoint/result completion does not agree"
                )
            self.accepted.plan.schedule(
                ExecutionRef(row["entry"], row["cid"], row["execution_id"])
            )
            checkpoints.append(
                WorkPermitCheckpoint(
                    row["entry"],
                    row["cid"],
                    row["execution_id"],
                    row["state"],
                    row["permit_id"],
                    row["released_permit_id"],
                )
            )
        running = self._load_running_workers()
        if control.status is not None and (
            checkpoints or (owner is not None and owner.state == "running")
        ):
            raise JobStoreInvariantError("terminal run retains active ownership")
        if running and (owner is None or owner.state != "running"):
            raise JobStoreInvariantError("live workers have no running owner")
        return WorkSchedulerOwner(
            self.accepted.run_id,
            control.status,
            control.phase,
            control.stop_requested_at,
            owner,
            tuple(checkpoints),
            running,
        )

    def _load_running_workers(
        self,
    ) -> tuple[tuple[ExecutionIdentity | None, WorkerRecord], ...]:
        rows = self._db.execute(
            "SELECT w.*, c.entry, c.cid, c.execution_id FROM workers w "
            "LEFT JOIN accepted_work_commands c USING(run_id, command_pk) "
            "WHERE w.run_id=? AND w.state='running' "
            "ORDER BY w.command_pk, w.worker_id LIMIT ?",
            (self.accepted.run_id, MAX_RUN_WORKERS + 1),
        ).fetchall()
        if len(rows) > MAX_RUN_WORKERS:
            raise JobStoreInvariantError("running worker count crossed its bound")
        running: list[tuple[ExecutionIdentity | None, WorkerRecord]] = []
        for row in rows:
            identity = None
            if row["command_pk"] is not None:
                identity = ExecutionIdentity(
                    row["entry"], row["cid"], row["execution_id"]
                )
                self.accepted.plan.schedule(
                    ExecutionRef(identity.entry, identity.cid, identity.execution_id)
                )
            worker = WorkerRecord(
                row["worker_id"],
                row["parent_worker_id"],
                row["pid"],
                "running",
                row["registered_at"],
                row["last_observed_at"],
            )
            _validate_worker_record(worker)
            running.append((identity, worker))
        return tuple(running)

    def attach_execution_permit(self, attachment: ExecutionPermitAttachment) -> None:
        """CAS-attach one scheduler grant before launch; same-grant retry is safe."""

        _validate_permit_attachment(attachment)
        identity = ExecutionRef(
            attachment.entry, attachment.cid, attachment.execution_id
        )
        with self._transaction("work_permit_attachment"):
            command_pk = self._command_pk(identity)
            self._require_active_work()
            row = self._checkpoint(command_pk)
            if row is not None and row["state"] == "active":
                if row["permit_id"] == attachment.permit_id:
                    return
                raise JobStoreTransitionError("execution has a different permit")
            if attachment.expected_state == "absent":
                if row is not None:
                    raise JobStoreTransitionError("permit expected no prior checkpoint")
                self._db.execute(
                    "INSERT INTO execution_checkpoints "
                    "(run_id,command_pk,state,permit_id,checkpointed_at,"
                    "elapsed_seconds) "
                    "VALUES (?, ?, 'active', ?, ?, 0)",
                    (
                        self.accepted.run_id,
                        command_pk,
                        attachment.permit_id,
                        attachment.checkpointed_at,
                    ),
                )
            else:
                if (
                    row is None
                    or row["state"] != "stopped"
                    or (row["permit_id"] is not None or row["scratch_path"] is not None)
                ):
                    raise JobStoreTransitionError("stopped checkpoint is not resumable")
                self._require_no_live_workers(command_pk)
                self._db.execute(
                    "UPDATE execution_checkpoints SET state='active',permit_id=?, "
                    "checkpointed_at=?,stop_reason=NULL,stopped_outputs=NULL "
                    "WHERE run_id=? AND command_pk=?",
                    (
                        attachment.permit_id,
                        attachment.checkpointed_at,
                        self.accepted.run_id,
                        command_pk,
                    ),
                )
            self._updated(attachment.checkpointed_at)

    def record_execution_start(self, start: ExecutionStart) -> None:
        """Record pending launch after exact permit/elapsed CAS, before any spawn."""

        _validate_execution_start(start)
        _validate_stream_paths(
            start.stdout_path, start.stderr_path, self.accepted.diagnostics_path
        )
        identity = ExecutionRef(start.entry, start.cid, start.execution_id)
        with self._transaction("work_execution_start"):
            command_pk = self._command_pk(identity)
            self._require_active_work()
            row = self._require_permit(command_pk, start.permit_id)
            if row["scratch_path"] is not None or (
                float(row["elapsed_seconds"]) != start.elapsed_seconds
            ):
                raise JobStoreTransitionError("execution start lost its attachment")
            self._db.execute(
                "UPDATE execution_checkpoints SET checkpointed_at=?, "
                "started_at=COALESCE(started_at,?),stdout_path=?,stderr_path=?, "
                "scratch_path=? WHERE run_id=? AND command_pk=?",
                (
                    start.checkpointed_at,
                    start.started_at,
                    start.stdout_path,
                    start.stderr_path,
                    start.scratch_path,
                    self.accepted.run_id,
                    command_pk,
                ),
            )
            self._db.execute(
                "UPDATE run_state SET phase='executing',"
                "started_at=COALESCE(started_at,?), "
                "updated_at=? WHERE run_id=?",
                (start.started_at, start.checkpointed_at, self.accepted.run_id),
            )

    def _require_permit(self, command_pk: int, permit_id: str) -> sqlite3.Row:
        row = self._checkpoint(command_pk)
        if row is None or row["state"] != "active" or row["permit_id"] != permit_id:
            raise JobStoreTransitionError(
                "attempt expected one permitted active execution"
            )
        return row

    def _updated(self, when: str) -> None:
        self._db.execute(
            "UPDATE run_state SET updated_at=? WHERE run_id=?",
            (when, self.accepted.run_id),
        )

    def _require_no_live_workers(self, command_pk: int) -> None:
        row = self._db.execute(
            "SELECT 1 FROM workers WHERE run_id=? AND command_pk=? "
            "AND state='running' LIMIT 1",
            (self.accepted.run_id, command_pk),
        ).fetchone()
        if row is not None:
            raise JobStoreTransitionError("execution retains a live worker")

    def _replace_workers(
        self, command_pk: int, workers: tuple[WorkerRecord, ...]
    ) -> None:
        _validate_workers(workers)
        stored = {
            row["worker_id"]: row
            for row in self._db.execute(
                "SELECT * FROM workers WHERE run_id=? AND command_pk=?",
                (self.accepted.run_id, command_pk),
            )
        }
        current = {worker.worker_id: worker for worker in workers}
        if not stored.keys() <= current.keys():
            raise JobStoreTransitionError("worker replacement loses registered workers")
        other_count = self._db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? "
            "AND (command_pk IS NULL OR command_pk!=?)",
            (self.accepted.run_id, command_pk),
        ).fetchone()[0]
        if other_count + len(workers) > MAX_RUN_WORKERS:
            raise JobStoreInvariantError("run worker count crossed its bound")
        for identity, row in stored.items():
            worker = current[identity]
            if (
                (row["pid"], row["parent_worker_id"], row["registered_at"])
                != (worker.pid, worker.parent_worker_id, worker.registered_at)
                or worker.last_observed_at < row["last_observed_at"]
                or (row["state"] == "exited" and worker.state != "exited")
            ):
                raise JobStoreTransitionError(
                    "worker replacement changes registered identity"
                )
        self._db.execute(
            "DELETE FROM workers WHERE run_id=? AND command_pk=?",
            (self.accepted.run_id, command_pk),
        )
        self._db.executemany(
            "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    self.accepted.run_id,
                    worker.worker_id,
                    worker.parent_worker_id,
                    command_pk,
                    worker.pid,
                    worker.state,
                    worker.registered_at,
                    worker.last_observed_at,
                )
                for worker in workers
            ),
        )

    def replace_execution_workers(
        self, identity: ExecutionRef, permit_id: str, workers: tuple[WorkerRecord, ...]
    ) -> None:
        """Persist the complete worker forest for one still-active scheduler grant."""

        with self._transaction("work_workers"):
            command_pk = self._command_pk(identity)
            self._require_permit(command_pk, permit_id)
            self._replace_workers(command_pk, workers)

    def load_execution_workers(
        self, identity: ExecutionRef
    ) -> tuple[WorkerRecord, ...]:
        """Read the bounded complete forest, including exited workers, for recovery."""

        rows = self._db.execute(
            "SELECT * FROM workers WHERE run_id=? AND command_pk=? "
            "ORDER BY worker_id LIMIT ?",
            (self.accepted.run_id, self._command_pk(identity), MAX_RUN_WORKERS + 1),
        ).fetchall()
        workers = tuple(
            WorkerRecord(
                row["worker_id"],
                row["parent_worker_id"],
                row["pid"],
                row["state"],
                row["registered_at"],
                row["last_observed_at"],
            )
            for row in rows
        )
        _validate_workers(workers)
        return workers

    def replace_recovery_workers(
        self, observations: tuple[RecoveryWorkerObservation, ...], *, observed_at: str
    ) -> None:
        """Persist an exhaustive live-process scan using original recovery rules.

        Unattributed survivors retain run-level exclusion, not fabricated command
        identity. Known identities bind only to active accepted attempts. The
        scan preserves PID/time identity and marks previously live absent workers
        exited, without dropping worker history or creating research results.
        """

        _require_timestamp(observed_at, "recovery observation time")
        workers = tuple(item.worker for item in observations)
        _validate_recovery_workers(workers)
        if any(worker.state != "running" for worker in workers):
            raise JobStoreInvariantError("recovery observation is not live")
        with self._transaction("recovery_workers"):
            state = self.load_run_control()
            if observations and state.status is not None:
                raise JobStoreTransitionError(
                    "terminal run cannot retain recovery workers"
                )
            resolved = self._resolve_recovery_workers(observations)
            run_id = self.accepted.run_id
            _require_recovery_workers_unchanged(self._db, run_id, resolved)
            self._db.execute(
                "UPDATE workers SET state='exited', "
                "last_observed_at=MAX(last_observed_at,?) "
                "WHERE run_id=? AND state='running'",
                (observed_at, run_id),
            )
            for command_pk, worker in resolved:
                self._db.execute(
                    "INSERT INTO workers VALUES (?, ?, ?, ?, ?, 'running', ?, ?) "
                    "ON CONFLICT(run_id, worker_id) DO UPDATE SET "
                    "parent_worker_id=excluded.parent_worker_id, "
                    "command_pk=excluded.command_pk, state='running', "
                    "last_observed_at=excluded.last_observed_at",
                    (
                        run_id,
                        worker.worker_id,
                        worker.parent_worker_id,
                        command_pk,
                        worker.pid,
                        worker.registered_at,
                        worker.last_observed_at,
                    ),
                )
            _require_recovery_workers_present(self._db, run_id, resolved)
            self._updated(observed_at)

    def _resolve_recovery_workers(
        self, observations: tuple[RecoveryWorkerObservation, ...]
    ) -> tuple[tuple[int | None, WorkerRecord], ...]:
        resolved = []
        for observation in observations:
            command_pk = None
            if observation.identity is not None:
                identity = observation.identity
                command_pk = self._command_pk(
                    ExecutionRef(identity.entry, identity.cid, identity.execution_id)
                )
                checkpoint = self._checkpoint(command_pk)
                if checkpoint is None or checkpoint["state"] != "active":
                    command_pk = None
            resolved.append((command_pk, observation.worker))
        return tuple(resolved)

    def record_attempt_completion(self, completion: AttemptCompletion) -> None:
        """Atomically retain actual result/problems and exited forest before release.

        No checkpoint failure/outcome/output projection competes with the result.
        Partial observations must belong to accepted recipe outputs; success must
        cover that complete set, including outputs outside counted artifacts.
        """

        result = completion.result
        if result.outcome is CommandOutcome.BLOCKED:
            raise JobStoreInvariantError("attempt completion cannot describe a block")
        _require_timestamp(completion.checkpointed_at, "checkpoint time")
        _require_elapsed(completion.elapsed_seconds)
        _validate_workers(completion.workers)
        if any(worker.state != "exited" for worker in completion.workers):
            raise JobStoreInvariantError("terminal attempt retains a live worker")
        with self._transaction("work_attempt_completion"):
            command_pk = self._command_pk(result.identity)
            row = self._require_permit(command_pk, completion.permit_id)
            if row["started_at"] is None or row["scratch_path"] is None:
                raise JobStoreTransitionError("unlaunched execution cannot complete")
            if completion.elapsed_seconds < row["elapsed_seconds"] or (
                result.started_at != row["started_at"]
                or result.stdout_path != row["stdout_path"]
                or result.stderr_path != row["stderr_path"]
            ):
                raise JobStoreTransitionError(
                    "terminal facts do not match pending invocation"
                )
            work = self.accepted.plan.command(result.identity)
            outputs = dict(work.execution.recipe.outputs)
            if not result.outputs.keys() <= outputs.keys() or (
                result.outcome is CommandOutcome.SUCCEEDED
                and result.outputs.keys() != outputs.keys()
            ):
                raise JobStoreInvariantError(
                    "terminal output inventory is not accepted"
                )
            for name, fingerprint in result.outputs.items():
                parse_fingerprint(fingerprint, kind=outputs[name], subject=name)
            write_command_observation(
                self._db, self.accepted.run_id, result, completion.problems
            )
            self._replace_workers(command_pk, completion.workers)
            self._db.execute(
                "UPDATE execution_checkpoints SET state='completed',checkpointed_at=?, "
                "elapsed_seconds=? WHERE run_id=? AND command_pk=?",
                (
                    completion.checkpointed_at,
                    completion.elapsed_seconds,
                    self.accepted.run_id,
                    command_pk,
                ),
            )
            self._updated(completion.checkpointed_at)

    def load_command_result(self, identity: ExecutionRef) -> CommandResult | None:
        """Read native immutable facts; absence never becomes a synthesized failure."""

        result = load_observation(self._db, self.accepted.run_id, identity)
        if result is not None and not isinstance(result, CommandResult):
            raise ReproductionDomainError("command has an artifact observation")
        return result

    def load_execution_readiness(self, identity: ExecutionRef) -> ExecutionReadiness:
        """Apply existing direct-dependency readiness to native research results.

        Only selected prerequisites require this run's completed observation.
        A failed/blocked prerequisite blocks without fabricating an invocation;
        otherwise unfinished selected prerequisites wait. Nonselected accepted
        inputs remain guarded by the physical execution material checks.
        """

        self.accepted.plan.schedule(identity)
        work = self.accepted.plan.command(identity)
        pending = []
        failed = []
        for key in work.dependencies:
            if self.accepted.plan.command(key).selection is not WorkSelection.RUN:
                continue
            result = self.load_command_result(key)
            dependency = ExecutionIdentity(key.entry, key.cid, key.execution_id)
            if result is None:
                pending.append(dependency)
            elif result.outcome in {CommandOutcome.FAILED, CommandOutcome.BLOCKED}:
                failed.append(dependency)
        owner = ExecutionIdentity(identity.entry, identity.cid, identity.execution_id)
        if failed:
            return ExecutionReadiness(owner, "dependency_failed", (), tuple(failed))
        if pending:
            return ExecutionReadiness(owner, "waiting", tuple(pending), ())
        return ExecutionReadiness(owner, "ready", (), ())

    def record_dependency_block(self, identity: ExecutionRef) -> CommandResult:
        """Commit only the actual failed-prerequisite links, with no grant/attempt.

        The native owner derives the block from durable prerequisite results;
        callers cannot submit a fabricated failure or overwrite an invocation.
        """

        with self._transaction("work_dependency_block"):
            self._require_active_work()
            if self._checkpoint(self._command_pk(identity)) is not None:
                raise JobStoreTransitionError(
                    "launched/attached command cannot become blocked"
                )
            existing = self.load_command_result(identity)
            if existing is not None:
                if existing.outcome is CommandOutcome.BLOCKED:
                    return existing
                raise JobStoreTransitionError("completed command cannot become blocked")
            readiness = self.load_execution_readiness(identity)
            if readiness.disposition != "dependency_failed":
                raise JobStoreTransitionError("command has no failed prerequisite")
            result = blocked_command_observation(
                identity,
                tuple(
                    ExecutionRef(key.entry, key.cid, key.execution_id)
                    for key in readiness.failed_dependencies
                ),
            )
            write_command_observation(self._db, self.accepted.run_id, result)
            return result

    def load_artifact_result(self, identity: ArtifactRef) -> ArtifactResult | None:
        """Read an exact durable comparison for resume, without recomputing it."""

        result = load_observation(self._db, self.accepted.run_id, identity)
        if result is not None and not isinstance(result, ArtifactResult):
            raise ReproductionDomainError("artifact has a command observation")
        return result

    def load_execution_checkpoint(
        self, identity: ExecutionRef
    ) -> WorkCheckpoint | None:
        """Read genuine recovery facts without a copied research-outcome projection."""

        row = self._checkpoint(self._command_pk(identity))
        if row is None:
            return None
        _validate_checkpoint_recovery(row, self.accepted.diagnostics_path)
        result = self.load_command_result(identity)
        if (row["state"] == "completed") != (result is not None):
            raise JobStoreInvariantError("checkpoint/result completion does not agree")
        _require_elapsed(row["elapsed_seconds"])
        return WorkCheckpoint(
            identity,
            row["state"],
            row["permit_id"],
            row["started_at"],
            row["elapsed_seconds"],
            row["scratch_path"],
            row["stdout_path"],
            row["stderr_path"],
        )

    def _record_artifact_comparison(
        self,
        result: ArtifactResult,
        problems: tuple[ReproductionProblem, ...] = (),
    ) -> None:
        """Commit trusted comparator facts, never a public result-submission API.

        Only compare_work_outputs supplies results here, after binding accepted
        artifact/producer and generated workspace paths to the original comparator.
        """

        work = self.accepted.plan.artifact(result.identity)
        if result.origin_run_id != self.accepted.run_id or (
            result.definition_identity != work.definition_identity
        ):
            raise JobStoreInvariantError(
                "comparison differs from accepted run/definition"
            )
        if work.producer is None:
            raise JobStoreInvariantError("comparison has no accepted producer")
        with self._transaction("work_artifact_comparison"):
            self._require_active_work()
            producer = self.load_command_result(work.producer)
            if producer is None or producer.outcome is not CommandOutcome.SUCCEEDED:
                raise JobStoreTransitionError(
                    "comparison requires a successful producer"
                )
            write_artifact_observation(self._db, self.accepted.run_id, result, problems)
            self._updated(result.recorded_at)

    def load_operational_status(self) -> dict[str, object]:
        """Derive lifecycle progress/diagnostics without requiring publication.

        Accepted scope/settings, workers and interrupted checkpoints remain
        visible for stopped or failed unpublished runs. Saved research outcomes
        are not reinterpreted and no current registry is read.
        """

        self._require_observation_bounds()
        control = self.load_run_control()
        row = _sole_row(self._db, "SELECT * FROM run_state")
        owner = self.load_scheduler_owner()
        checkpoints = []
        timings = []
        completed = 0
        diagnostic = None
        diagnostic_at = ""
        artifact_outcomes: dict[str, int] = {}
        for work in self.accepted.plan.commands:
            if work.selection is not WorkSelection.RUN:
                continue
            checkpoint = self.load_execution_checkpoint(work.identity)
            if checkpoint is not None:
                checkpoints.append({**asdict(checkpoint), **work.identity.as_dict()})
            result = load_observation(self._db, self.accepted.run_id, work.identity)
            if result is not None and not isinstance(result, CommandResult):
                raise JobStoreInvariantError("command observation has wrong type")
            finished_at = None if result is None else result.finished_at
            problems = (
                ()
                if result is None
                else load_observation_problems(self._db, self.accepted.run_id, result)
            )
            completed += int(result is not None)
            if checkpoint is not None and checkpoint.started_at is not None:
                failure = problems[0] if problems else None
                timings.append(
                    {
                        **work.identity.as_dict(),
                        "state": checkpoint.state,
                        "started_at": checkpoint.started_at,
                        "finished_at": finished_at,
                        "elapsed_seconds": checkpoint.elapsed_seconds,
                        "failure": None
                        if failure is None
                        else {
                            "code": failure.code,
                            "message": failure.explanation,
                            "recorded_at": finished_at,
                        },
                    }
                )
            for problem in problems:
                when = finished_at or ""
                if when >= diagnostic_at:
                    diagnostic_at = when
                    diagnostic = {
                        "code": problem.code,
                        "message": problem.explanation,
                        "recorded_at": finished_at,
                        **work.identity.as_dict(),
                    }
        for artifact in self.accepted.plan.artifacts:
            result = self.load_artifact_result(artifact.identity)
            if result is not None:
                key = result.outcome.value
                artifact_outcomes[key] = artifact_outcomes.get(key, 0) + 1
        active = [
            item
            for item in checkpoints
            if item["state"] == "active" and item["permit_id"] is not None
        ]
        active_keys = {
            (item["entry"], item["cid"], item["execution_id"]) for item in active
        }
        workers = [
            {
                **asdict(worker),
                "entry": None if identity is None else identity.entry,
                "cid": None if identity is None else identity.cid,
                "execution_id": None if identity is None else identity.execution_id,
            }
            for identity, worker in owner.running_workers
        ]
        publication = self.load_publication()
        resumable = control.status == "stopped" or (
            control.status == "failed"
            and control.operational_code == "reproduction.publication.failed"
            and publication is not None
        )
        return {
            "schema": "research-log-reproduction-status/7",
            "run_id": self.accepted.run_id,
            "summary": self.accepted.plan.summary,
            "target": self.accepted.plan.target.as_dict(),
            **self.accepted.plan.settings.as_dict(),
            "status": control.status,
            "phase": control.phase,
            "resumable": resumable,
            "total_executions": sum(
                work.selection is WorkSelection.RUN
                for work in self.accepted.plan.commands
            ),
            "completed_executions": completed,
            "active_executions": [
                {key: item[key] for key in ("entry", "cid", "execution_id")}
                for item in active
            ],
            "execution_timings": timings,
            "active_workers": [
                item
                for item in workers
                if (item["entry"], item["cid"], item["execution_id"]) in active_keys
            ],
            "surviving_workers": workers,
            "artifact_outcomes": artifact_outcomes,
            "latest_execution_diagnostic": diagnostic,
            "operational_failure": None
            if control.operational_code is None
            else {
                "code": control.operational_code,
                "message": row["operational_message"],
                "recorded_at": row["operational_recorded_at"],
            },
            "timestamps": {
                "accepted_at": self.accepted.accepted_at,
                **{
                    key: row[key]
                    for key in (
                        "started_at",
                        "finished_at",
                        "resumed_at",
                        "stopped_at",
                        "updated_at",
                    )
                },
            },
        }

    def requirement_clear_ready(self, identity: ExecutionRef) -> bool:
        """Require accepted need and durable complete production/comparisons.

        Equality is not a prerequisite. An acknowledgment is an external-write
        receipt, not a second command/comparison outcome.
        """

        work = self.accepted.plan.command(identity)
        result = self.load_command_result(identity)
        if not work.execution.requires_reproduction or result is None:
            return False
        if result.outcome is not CommandOutcome.SUCCEEDED:
            return False
        outputs = dict(work.execution.recipe.outputs)
        if set(result.outputs) != set(outputs):
            raise JobStoreInvariantError("successful production has incomplete outputs")
        compared = {
            artifact.identity.artifact
            for artifact in self.accepted.plan.artifacts
            if artifact.producer == identity
            and self.load_artifact_result(artifact.identity) is not None
        }
        if compared != set(outputs):
            return False
        row = self._db.execute(
            "SELECT cleared_at FROM requirement_acknowledgments "
            "WHERE run_id=? AND command_pk=?",
            (self.accepted.run_id, self._command_pk(identity)),
        ).fetchone()
        if row is not None:
            _require_timestamp(row[0], "requirement acknowledgment")
        return row is None

    def acknowledge_requirement_clear(
        self, identity: ExecutionRef, *, cleared_at: str
    ) -> None:
        """Acknowledge the exact external flag write after durable comparison."""

        _require_timestamp(cleared_at, "requirement acknowledgment")
        with self._transaction("work_requirement_clear"):
            if not self.requirement_clear_ready(identity):
                raise JobStoreTransitionError("requirement clearing is not ready")
            self._db.execute(
                "INSERT INTO requirement_acknowledgments VALUES (?,?,?)",
                (self.accepted.run_id, self._command_pk(identity), cleared_at),
            )
            self._updated(cleared_at)

    def _require_observation_bounds(self) -> None:
        total = 0
        for table, column in (
            ("run_command_results", "result_json"),
            ("run_artifact_results", "result_json"),
            ("reproduction_problems", "problem_json"),
        ):
            size = self._db.execute(
                f"SELECT COUNT(*),COALESCE(SUM(length(CAST({column} AS BLOB))),0) "
                f"FROM {table} WHERE run_id=?",
                (self.accepted.run_id,),
            ).fetchone()
            total += size[1]
            if size[0] > MAX_WORK_RECORDS or total > MAX_PLAN_BYTES:
                raise ReproductionDomainError(
                    "run observations exceed the native read bound"
                )

    def load_completed_run(self, *, finished_at: str) -> SavedRun:
        """Assemble publication only from immutable accepted work and durable facts.

        An active grant, uncleared scratch or running worker prevents publication.
        Missing runnable/comparison facts fail closed in the native completion
        owner. This reads no research sources and does not mutate lifecycle state.
        """

        _require_timestamp(finished_at, "finish time")
        active = self._db.execute(
            "SELECT 1 FROM execution_checkpoints WHERE run_id=? AND "
            "(state='active' OR permit_id IS NOT NULL OR scratch_path IS NOT NULL) "
            "LIMIT 1",
            (self.accepted.run_id,),
        ).fetchone()
        live = self._db.execute(
            "SELECT 1 FROM workers WHERE run_id=? AND state='running' LIMIT 1",
            (self.accepted.run_id,),
        ).fetchone()
        if active is not None or live is not None:
            raise JobStoreTransitionError(
                "publication requires quiescent cleaned attempts"
            )
        self._require_observation_bounds()
        commands = []
        artifacts = []
        for work in self.accepted.plan.commands:
            result = self.load_command_result(work.identity)
            if result is not None:
                commands.append(result)
        for artifact in self.accepted.plan.artifacts:
            observed = load_observation(
                self._db, self.accepted.run_id, artifact.identity
            )
            if observed is not None:
                if not isinstance(observed, ArtifactResult):
                    raise ReproductionDomainError("artifact has a command observation")
                artifacts.append(observed)
        problems = tuple(
            ReproductionProblem.from_dict(_payload(row[0], set()))
            for row in self._db.execute(
                "SELECT problem_json FROM reproduction_problems WHERE run_id=? "
                "ORDER BY problem_id",
                (self.accepted.run_id,),
            )
        )
        return complete_saved_run(
            self.accepted.plan,
            RunCompletion(
                self.accepted.run_id,
                self.accepted.accepted_at,
                finished_at,
                tuple(commands),
                tuple(artifacts),
                problems,
            ),
        )

    def load_publication(self) -> WorkPublication | None:
        """Read bounded publication recovery state without sources or results."""

        row = self._db.execute(
            "SELECT * FROM run_publication WHERE run_id=?", (self.accepted.run_id,)
        ).fetchone()
        if row is None:
            return None
        _require_timestamp(row["finished_at"], "publication finish time")
        for key in ("result_generation", "report_generation"):
            if row[key] is not None and (type(row[key]) is not int or row[key] < 1):
                raise JobStoreInvariantError("publication generation is invalid")
        if row["report_generation"] is not None and row["result_generation"] is None:
            raise JobStoreInvariantError("report acknowledgment has no result commit")
        return WorkPublication(
            row["finished_at"], row["result_generation"], row["report_generation"]
        )

    def prepare_publication(self, *, finished_at: str) -> SavedRun:
        """Freeze completion time before any shared result write; exact retry reuses it.

        All required native observations and cleaned attempt ownership must be
        complete. Accepted work/results remain their only durable fact owners.
        """

        _require_timestamp(finished_at, "publication finish time")
        with self._transaction("work_publication_prepare"):
            self._require_publication_owner()
            state = self.load_run_control()
            if state.status is not None or state.phase == "stopping":
                raise JobStoreTransitionError("terminal/stopping run cannot publish")
            publication = self.load_publication()
            if publication is not None:
                if state.phase != "publishing":
                    raise JobStoreInvariantError(
                        "publication recovery has invalid phase"
                    )
                return self.load_completed_run(finished_at=publication.finished_at)
            if state.phase not in PERMIT_ADMISSION_PHASES:
                raise JobStoreTransitionError("run is not ready for publication")
            run = self.load_completed_run(finished_at=finished_at)
            self._db.execute(
                "INSERT INTO run_publication VALUES (?, ?, NULL, NULL)",
                (self.accepted.run_id, finished_at),
            )
            self._db.execute(
                "UPDATE run_state SET phase='publishing', updated_at=? WHERE run_id=?",
                (finished_at, self.accepted.run_id),
            )
            return run

    def _require_publication_owner(self) -> RunOwner:
        owner = self.load_run_owner()
        if owner is None or owner.state != "running":
            raise JobStoreTransitionError("publication requires a running supervisor")
        return owner

    def _record_result_commit(self, generation: int, *, updated_at: str) -> None:
        """Private publisher acknowledgment after its exact native transaction.

        Only publish_work_job calls this under publication/results locks after
        publish_saved_run has authenticated/committed the frozen SavedRun.
        Recovery may acknowledge a newer current generation or a recreated
        disposable result domain. This changes no accepted or research result.
        """

        _require_timestamp(updated_at, "publication update time")
        if type(generation) is not int or generation < 1:
            raise JobStoreInvariantError("publication generation is invalid")
        with self._transaction("work_result_commit"):
            self._require_publication_owner()
            publication = self.load_publication()
            state = self.load_run_control()
            if (
                publication is None
                or state.status is not None
                or state.phase != "publishing"
            ):
                raise JobStoreTransitionError("run has no active publication")
            self._db.execute(
                "UPDATE run_publication SET result_generation=?, "
                "report_generation=NULL WHERE run_id=?",
                (generation, self.accepted.run_id),
            )
            self._updated(updated_at)

    def _finish_publication(self, report_generation: int, *, updated_at: str) -> None:
        """Private publisher close after successful exact-generation report marking.

        Only publish_work_job calls this, holding publication/results locks after
        materialize_saved_report_locked proves the actual current report marker.
        No public arbitrary-generation submission operation is exposed.
        """

        _require_timestamp(updated_at, "publication finish update time")
        if type(report_generation) is not int or report_generation < 1:
            raise JobStoreInvariantError("publication generation is invalid")
        with self._transaction("work_report_commit"):
            owner = self._require_publication_owner()
            publication = self.load_publication()
            state = self.load_run_control()
            if (
                publication is None
                or publication.result_generation is None
                or state.status is not None
                or state.phase != "publishing"
            ):
                raise JobStoreTransitionError(
                    "report completion has no acknowledged result"
                )
            _require_quiescent_run(self._db, self.accepted.run_id)
            if publication.result_generation != report_generation:
                raise JobStoreInvariantError(
                    "report differs from acknowledged current generation"
                )
            self._db.execute(
                "UPDATE run_publication SET report_generation=? WHERE run_id=?",
                (report_generation, self.accepted.run_id),
            )
            self._db.execute(
                "UPDATE run_state SET phase=NULL, status='complete', "
                "finished_at=?, updated_at=? WHERE run_id=?",
                (publication.finished_at, updated_at, self.accepted.run_id),
            )
            changed = self._db.execute(
                "UPDATE run_owner SET state='exited', "
                "last_observed_at=MAX(last_observed_at,?) "
                "WHERE run_id=? AND state='running' "
                "AND supervisor_pid=? AND registered_at=?",
                (
                    updated_at,
                    self.accepted.run_id,
                    owner.supervisor_pid,
                    owner.registered_at,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("publication lost its running supervisor")

    def record_execution_stop(self, interruption: AttemptInterruption) -> None:
        """Keep interrupted lifecycle/debug facts without a terminal research result.

        Release and scratch cleanup remain separate CAS steps. A resume must
        finish both before another grant can attach; elapsed time is retained.
        """

        identity = interruption.identity
        _require_timestamp(interruption.checkpointed_at, "checkpoint time")
        _require_elapsed(interruption.elapsed_seconds)
        reason = interruption.reason
        outputs = interruption.outputs
        workers = interruption.workers
        if not isinstance(reason, str) or not reason or len(reason) > 16 * 1024:
            raise JobStoreInvariantError("stop reason is invalid")
        _validate_workers(workers)
        if any(worker.state != "exited" for worker in workers):
            raise JobStoreInvariantError("stopped attempt retains a live worker")
        with self._transaction("work_execution_stop"):
            command_pk = self._command_pk(identity)
            row = self._require_permit(command_pk, interruption.permit_id)
            if interruption.elapsed_seconds < row["elapsed_seconds"]:
                raise JobStoreTransitionError("stopped elapsed time moved backward")
            declared = dict(
                self.accepted.plan.command(identity).execution.recipe.outputs
            )
            if not outputs.keys() <= declared.keys():
                raise JobStoreInvariantError("stopped output inventory is not accepted")
            for name, fingerprint in outputs.items():
                parse_fingerprint(fingerprint, kind=declared[name], subject=name)
            self._replace_workers(command_pk, workers)
            self._db.execute(
                "UPDATE execution_checkpoints SET state='stopped',checkpointed_at=?, "
                "elapsed_seconds=?,stop_reason=?,stopped_outputs=? "
                "WHERE run_id=? AND command_pk=?",
                (
                    interruption.checkpointed_at,
                    interruption.elapsed_seconds,
                    reason,
                    _json(outputs),
                    self.accepted.run_id,
                    command_pk,
                ),
            )
            self._updated(interruption.checkpointed_at)

    def clear_execution_scratch(
        self, identity: ExecutionRef, scratch_path: str, *, checkpointed_at: str
    ) -> None:
        """CAS-clear the cleaned terminal scratch path after grant release."""

        _require_timestamp(checkpointed_at, "checkpoint time")
        with self._transaction("work_scratch_clear"):
            command_pk = self._command_pk(identity)
            row = self._checkpoint(command_pk)
            if row is None or row["state"] == "active" or row["permit_id"] is not None:
                raise JobStoreTransitionError(
                    "scratch cleanup requires a released terminal grant"
                )
            self._require_no_live_workers(command_pk)
            if row["scratch_path"] is None:
                return
            if row["scratch_path"] != scratch_path:
                raise JobStoreTransitionError("checkpoint has a different scratch path")
            self._db.execute(
                "UPDATE execution_checkpoints SET scratch_path=NULL,checkpointed_at=? "
                "WHERE run_id=? AND command_pk=?",
                (checkpointed_at, self.accepted.run_id, command_pk),
            )
            self._updated(checkpointed_at)

    def clear_execution_permit(
        self, identity: ExecutionRef, permit_id: str, *, checkpointed_at: str
    ) -> None:
        """CAS-release only a terminal, quiescent grant; exact retry is harmless."""

        _require_timestamp(checkpointed_at, "checkpoint time")
        with self._transaction("work_permit_clear"):
            command_pk = self._command_pk(identity)
            row = self._checkpoint(command_pk)
            if row is None or row["state"] == "active":
                raise JobStoreTransitionError(
                    "active or absent checkpoint cannot release"
                )
            self._require_no_live_workers(command_pk)
            if row["permit_id"] is None and row["released_permit_id"] == permit_id:
                return
            if row["permit_id"] != permit_id:
                raise JobStoreTransitionError("checkpoint has a different permit")
            self._db.execute(
                "UPDATE execution_checkpoints SET permit_id=NULL,released_permit_id=?, "
                "checkpointed_at=? WHERE run_id=? AND command_pk=?",
                (permit_id, checkpointed_at, self.accepted.run_id, command_pk),
            )
            self._updated(checkpointed_at)


def _require_elapsed(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (not math.isfinite(value) or value < 0)
    ):
        raise JobStoreInvariantError("execution elapsed time is invalid")


def _validate_stream_paths(
    stdout: str | None,
    stderr: str | None,
    diagnostics_path: str,
) -> None:
    diagnostics = PurePosixPath(diagnostics_path)
    for value in (stdout, stderr):
        if value is not None:
            _require_relative_path(value, "stream path")
            if diagnostics not in PurePosixPath(value).parents:
                raise JobStoreInvariantError(
                    "stream path is outside accepted diagnostics"
                )


def _validate_checkpoint_recovery(row: sqlite3.Row, diagnostics_path: str) -> None:
    """Reject corrupt control scalars before typed native recovery consumes them."""

    if row["state"] not in {"active", "stopped", "completed"}:
        raise JobStoreInvariantError("checkpoint state is invalid")
    _require_timestamp(row["checkpointed_at"], "checkpoint time")
    if row["started_at"] is not None:
        _require_timestamp(row["started_at"], "start time")
        if row["started_at"] > row["checkpointed_at"]:
            raise JobStoreInvariantError("checkpoint predates its start")
    for key in ("permit_id", "released_permit_id"):
        if row[key] is not None and not _bounded_string(row[key]):
            raise JobStoreInvariantError("checkpoint permit identity is invalid")
    if row["permit_id"] is None and (
        row["state"] == "active" or row["released_permit_id"] is None
    ):
        raise JobStoreInvariantError("checkpoint has no attached or released grant")
    if row["scratch_path"] is not None:
        _require_scratch_path(row["scratch_path"], "scratch path")
        if row["started_at"] is None:
            raise JobStoreInvariantError("unlaunched checkpoint has scratch ownership")
    _validate_stream_paths(row["stdout_path"], row["stderr_path"], diagnostics_path)
    _validate_interrupted_checkpoint(row)


def _validate_interrupted_checkpoint(row: sqlite3.Row) -> None:
    reason = row["stop_reason"]
    if row["state"] == "stopped":
        if (
            not isinstance(reason, str)
            or not reason
            or len(reason.encode()) > 16 * 1024
        ):
            raise JobStoreInvariantError("stopped checkpoint needs a bounded reason")
        _payload(row["stopped_outputs"], set())
    elif reason is not None or row["stopped_outputs"] is not None:
        raise JobStoreInvariantError("nonstopped checkpoint has interrupted facts")
