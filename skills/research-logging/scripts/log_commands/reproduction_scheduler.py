"""Project-wide ordinary and exclusive reproduction scheduling permits."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping, Sequence, cast

from validation.operation_state import (
    OperationLockError,
    operation_directory,
    operation_lock,
)

from .context import ENTRY_ID_RE
from .model import ActionError
from .reproduction_job_control import (
    AcceptedSchedulingProjection,
    ExecutionIdentity,
    ExecutionPermitAttachment,
    JobStoreError,
)
from .reproduction_work_job import (
    PERMIT_ADMISSION_PHASES,
    LockedWorkJob,
    WorkPermitCheckpoint,
    WorkSchedulerOwner,
    open_work_job,
)

JobOpener = Callable[[Path], AbstractContextManager[LockedWorkJob]]

SCHEDULER_STORE_VERSION = 2
SCHEDULER_DATABASE_NAME = "reproduction-scheduler.sqlite"
POLL_SECONDS = 0.1
MAX_WAITERS = 10_000
MAX_PERMITS = 4_096
MAX_CLAIMS_PER_KIND = 4_096
MAX_SCHEDULER_BYTES = 64 * 1024 * 1024
MAX_RUN_PATH_ANCESTORS = 8
_THREAD_LOCK = threading.Lock()
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
CID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
EXECUTION_ID_RE = re.compile(r"pyrun-exec/v2:[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")

_SCHEDULER_DDL = """
CREATE TABLE scheduler_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    next_ticket INTEGER NOT NULL CHECK(next_ticket >= 0)
);
CREATE TABLE scheduler_waiters (
    ticket INTEGER PRIMARY KEY CHECK(ticket >= 0),
    run_id TEXT NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 1),
    supervisor_pid INTEGER NOT NULL CHECK(supervisor_pid >= 1),
    registered_at TEXT NOT NULL,
    UNIQUE(run_id, entry, cid, execution_id)
);
CREATE TABLE scheduler_permits (
    permit_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('ordinary', 'exclusive')),
    run_id TEXT NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 1),
    supervisor_pid INTEGER NOT NULL CHECK(supervisor_pid >= 1),
    run_path TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    UNIQUE(run_id, entry, cid, execution_id)
);
CREATE TABLE scheduler_claims (
    permit_id TEXT NOT NULL REFERENCES scheduler_permits(permit_id) ON DELETE CASCADE,
    claim_kind TEXT NOT NULL CHECK(claim_kind IN ('read', 'write', 'writable')),
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    PRIMARY KEY(permit_id, claim_kind, position),
    UNIQUE(permit_id, claim_kind, path)
) WITHOUT ROWID;
CREATE INDEX scheduler_permits_kind ON scheduler_permits(kind, run_id, plan_order);
CREATE INDEX scheduler_claims_path ON scheduler_claims(claim_kind, path, permit_id);
"""


@dataclass(frozen=True)
class SchedulerIdentity:
    """Complete identity of one accepted execution in one project scheduler."""

    project_root: Path
    run_id: str
    entry: str
    cid: str
    execution_id: str
    plan_order: int


@dataclass(frozen=True)
class SchedulerPermitRequest:
    """One strict permit poll with canonical scheduling claims."""

    identity: SchedulerIdentity
    kind: Literal["ordinary", "exclusive"]
    supervisor_pid: int
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    run_path: str
    writable_paths: tuple[str, ...]
    polled_at: str


@dataclass(frozen=True)
class SchedulerPermitProjection:
    """Complete bounded projection of one durable scheduler grant."""

    permit_id: str
    identity: SchedulerIdentity
    kind: Literal["ordinary", "exclusive"]
    supervisor_pid: int
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    run_path: str
    writable_paths: tuple[str, ...]
    granted_at: str


@dataclass(frozen=True)
class SchedulerDelta:
    """Exact scheduler rows changed by one typed operation."""

    inserted_waiter_ticket: int | None = None
    removed_waiter_tickets: tuple[int, ...] = ()
    inserted_permit_id: str | None = None
    removed_permit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchedulerDecision:
    """Closed result of one permit poll."""

    disposition: Literal["waiting", "granted", "blocked"]
    permit: SchedulerPermitProjection | None
    waiter_ticket: int | None
    delta: SchedulerDelta | None


@dataclass(frozen=True)
class SchedulerReconciliation:
    """One cross-store reconciliation result without implied execution outcome."""

    clear_run_permit_id: str | None
    delta: SchedulerDelta | None


@dataclass(frozen=True)
class _PollMutation:
    removed_waiters: tuple[int, ...]
    removed_permits: tuple[str, ...] = ()
    inserted_waiter: int | None = None
    permit_id: str | None = None
    granted_waiter_ticket: int | None = None


@dataclass(frozen=True)
class _SchedulingRequest:
    project_root: Path
    run_root: Path
    run_id: str
    planned: Mapping[str, object]
    supervisor_pid: int


def poll_work_permit(
    run_root: Path,
    request: SchedulerPermitRequest,
    *,
    checkpointed_at: str,
    expected_state: Literal["absent", "stopped"],
) -> SchedulerDecision:
    """Admit native Job4 work using the shared fairness and conflict rules.

    Only the native job authority is opened, including other dead permit owners.
    Unsupported jobs require explicit resolution; no version fallback occurs.
    """

    return _poll_with_job(
        run_root, request, checkpointed_at, expected_state, open_work_job
    )


def _poll_with_job(
    run_root: Path,
    request: SchedulerPermitRequest,
    checkpointed_at: str,
    expected_state: Literal["absent", "stopped"],
    open_job: JobOpener,
) -> SchedulerDecision:

    _validate_scheduler_request(request)
    if not _timestamp(checkpointed_at) or expected_state not in {"absent", "stopped"}:
        raise ActionError("reproduction.scheduler.invalid", "invalid permit attachment")
    with _coordinator_lock(request.identity.project_root):
        with open_job(run_root) as store:
            _validate_accepted_request(store, run_root, request)
            with _open_scheduler_database(request.identity.project_root) as db:
                decision = _poll_permit_locked(
                    db, request, run_root.resolve(), store, open_job=open_job
                )
            if decision.disposition == "granted":
                assert decision.permit is not None
                _after_scheduler_grant_before_attach(decision, store)
                store.attach_execution_permit(
                    ExecutionPermitAttachment(
                        request.identity.entry,
                        request.identity.cid,
                        request.identity.execution_id,
                        decision.permit.permit_id,
                        checkpointed_at,
                        expected_state,
                    )
                )
                _after_run_permit_attachment(decision, store)
            return decision


def _poll_permit_locked(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    current_run_root: Path,
    current_store: LockedWorkJob,
    *,
    open_job: JobOpener = open_work_job,
) -> SchedulerDecision:
    """Compute and commit one scheduler decision under the caller's mutex."""

    db.execute("BEGIN")
    _audit_scheduler_state(db, request.identity.project_root)
    removed_permits = _recoverable_dead_permit_ids(
        db,
        request.identity.project_root,
        current_run_root,
        current_store,
        open_job=open_job,
    )
    removed_waiters = _dead_waiter_tickets(db)
    existing = _permit_for_identity(db, request.identity)
    if existing is not None and existing.permit_id in removed_permits:
        existing = None
    if existing is not None:
        return _poll_existing_permit(
            db, request, existing, removed_waiters, removed_permits
        )
    if request.kind == "ordinary":
        return _poll_ordinary(db, request, removed_waiters, removed_permits)
    return _poll_exclusive(db, request, removed_waiters, removed_permits)


def _validate_accepted_request(
    store: LockedWorkJob, run_root: Path, request: SchedulerPermitRequest
) -> None:
    accepted = store.load_accepted_scheduling(
        ExecutionIdentity(
            request.identity.entry,
            request.identity.cid,
            request.identity.execution_id,
        )
    )
    owner = store.load_scheduler_owner()
    expected = _accepted_scheduler_claims(
        accepted, run_root.resolve(), request.identity.project_root
    )
    if (
        accepted.run_id != request.identity.run_id
        or accepted.identity.entry != request.identity.entry
        or accepted.identity.cid != request.identity.cid
        or accepted.identity.execution_id != request.identity.execution_id
        or accepted.plan_order != request.identity.plan_order
        or accepted.kind != request.kind
        or expected
        != (
            request.read_paths,
            request.write_paths,
            request.run_path,
            request.writable_paths,
        )
    ):
        raise ActionError(
            "reproduction.scheduler.invalid",
            "permit request does not match immutable accepted scheduling facts",
        )
    if (
        owner.run_id != request.identity.run_id
        or owner.status is not None
        or owner.phase not in PERMIT_ADMISSION_PHASES
        or owner.stop_requested_at is not None
        or owner.owner is None
        or owner.owner.state != "running"
        or owner.owner.supervisor_pid != request.supervisor_pid
    ):
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "permit request does not match the durable running supervisor",
        )


def _accepted_scheduler_claims(
    accepted: AcceptedSchedulingProjection, run_root: Path, project_root: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str, tuple[str, ...]]:
    return (
        tuple(_resolve_claims(list(accepted.read_paths), run_root, project_root)),
        tuple(_resolve_claims(list(accepted.write_paths), run_root, project_root)),
        _resolve_claim(accepted.run_path, run_root, project_root),
        tuple(_resolve_claims(list(accepted.writable_paths), run_root, project_root)),
    )


def _poll_existing_permit(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    existing: SchedulerPermitProjection,
    removed_waiters: tuple[int, ...],
    removed_permits: tuple[str, ...],
) -> SchedulerDecision:
    _require_matching_request(existing, request)
    if not removed_waiters and not removed_permits:
        db.rollback()
        return SchedulerDecision("granted", existing, None, None)
    delta = _apply_scheduler_decision(
        db, request, _PollMutation(removed_waiters, removed_permits)
    )
    return SchedulerDecision("granted", existing, None, delta)


def _poll_ordinary(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    removed_waiters: tuple[int, ...],
    removed_permits: tuple[str, ...],
) -> SchedulerDecision:
    if _waiter_for_identity(db, request.identity) is not None:
        raise ActionError(
            "reproduction.scheduler.invalid",
            "ordinary request owns an exclusive waiter",
        )
    if _ordinary_request_is_blocked(
        db,
        request,
        ignored_waiter_tickets=removed_waiters,
        ignored_permit_ids=removed_permits,
    ):
        if not removed_waiters and not removed_permits:
            db.rollback()
            return SchedulerDecision("blocked", None, None, None)
        delta = _apply_scheduler_decision(
            db, request, _PollMutation(removed_waiters, removed_permits)
        )
        return SchedulerDecision("blocked", None, None, delta)
    _require_permit_capacity(db, removed_permits)
    permit_id = f"permit-{secrets.token_hex(12)}"
    delta = _apply_scheduler_decision(
        db,
        request,
        _PollMutation(removed_waiters, removed_permits, permit_id=permit_id),
    )
    return SchedulerDecision(
        "granted", _require_granted_permit(db, request), None, delta
    )


def _poll_exclusive(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    removed_waiters: tuple[int, ...],
    removed_permits: tuple[str, ...],
) -> SchedulerDecision:
    waiter = _waiter_for_identity(db, request.identity)
    if waiter is None:
        inserted_waiter = _next_waiter_ticket(db)
        waiter_ticket = inserted_waiter
    else:
        inserted_waiter = None
        _require_matching_waiter(waiter, request)
        waiter_ticket = int(waiter["ticket"])
    first_ticket = _first_retained_waiter_ticket(db, removed_waiters, inserted_waiter)
    grant = (
        not _scheduler_has_permits(db, removed_permits)
        and first_ticket == waiter_ticket
    )
    if (
        not grant
        and inserted_waiter is None
        and not removed_waiters
        and not removed_permits
    ):
        db.rollback()
        return SchedulerDecision("waiting", None, waiter_ticket, None)
    permit_id = f"permit-{secrets.token_hex(12)}" if grant else None
    mutation = _PollMutation(
        removed_waiters,
        removed_permits,
        inserted_waiter,
        permit_id,
        waiter_ticket if grant else None,
    )
    delta = _apply_scheduler_decision(db, request, mutation)
    if not grant:
        return SchedulerDecision("waiting", None, waiter_ticket, delta)
    return SchedulerDecision(
        "granted", _require_granted_permit(db, request), None, delta
    )


def _require_granted_permit(
    db: sqlite3.Connection, request: SchedulerPermitRequest
) -> SchedulerPermitProjection:
    permit = _permit_for_identity(db, request.identity)
    if permit is None:
        raise ActionError("reproduction.scheduler.invalid", "granted permit is missing")
    return permit


def _apply_scheduler_decision(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    mutation: _PollMutation,
) -> SchedulerDelta:
    """Commit one precomputed scheduler mutation under the held mutex."""

    removed = set(mutation.removed_waiters)
    if mutation.granted_waiter_ticket is not None:
        removed.add(mutation.granted_waiter_ticket)
    try:
        db.rollback()
        db.execute("BEGIN IMMEDIATE")
        _apply_waiter_removals(db, mutation.removed_waiters)
        _apply_permit_removals(db, mutation.removed_permits)
        _apply_waiter_insertion(db, request, mutation.inserted_waiter)
        _apply_granted_waiter_removal(db, mutation.granted_waiter_ticket)
        _apply_permit_insertion(db, request, mutation.permit_id)
        _require_scheduler_size(db)
        _before_scheduler_commit("permit_poll", db)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return SchedulerDelta(
        inserted_waiter_ticket=mutation.inserted_waiter,
        removed_waiter_tickets=tuple(sorted(removed)),
        inserted_permit_id=mutation.permit_id,
        removed_permit_ids=mutation.removed_permits,
    )


def _apply_waiter_removals(db: sqlite3.Connection, tickets: tuple[int, ...]) -> None:
    if not tickets:
        return
    placeholders = ",".join("?" for _item in tickets)
    changed = db.execute(
        f"DELETE FROM scheduler_waiters WHERE ticket IN ({placeholders})", tickets
    ).rowcount
    if changed != len(tickets):
        raise ActionError(
            "reproduction.scheduler.invalid",
            "scheduler waiter cleanup lost its identity",
        )


def _apply_permit_removals(db: sqlite3.Connection, permit_ids: tuple[str, ...]) -> None:
    if not permit_ids:
        return
    placeholders = ",".join("?" for _item in permit_ids)
    changed = db.execute(
        f"DELETE FROM scheduler_permits WHERE permit_id IN ({placeholders})",
        permit_ids,
    ).rowcount
    if changed != len(permit_ids):
        raise ActionError(
            "reproduction.scheduler.invalid",
            "scheduler permit cleanup lost its identity",
        )


def _apply_waiter_insertion(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    ticket: int | None,
) -> None:
    if ticket is None:
        return
    state_changed = db.execute(
        "UPDATE scheduler_state SET next_ticket=? WHERE singleton=1 AND next_ticket=?",
        (ticket + 1, ticket),
    ).rowcount
    if state_changed != 1:
        raise ActionError("reproduction.scheduler.invalid", "waiter ticket changed")
    db.execute(
        "INSERT INTO scheduler_waiters VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket,
            request.identity.run_id,
            request.identity.entry,
            request.identity.cid,
            request.identity.execution_id,
            request.identity.plan_order,
            request.supervisor_pid,
            request.polled_at,
        ),
    )


def _apply_granted_waiter_removal(db: sqlite3.Connection, ticket: int | None) -> None:
    if ticket is None:
        return
    changed = db.execute(
        "DELETE FROM scheduler_waiters WHERE ticket=?", (ticket,)
    ).rowcount
    if changed != 1:
        raise ActionError(
            "reproduction.scheduler.invalid", "granted waiter disappeared"
        )


def _apply_permit_insertion(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    permit_id: str | None,
) -> None:
    if permit_id is None:
        return
    db.execute(
        "INSERT INTO scheduler_permits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            permit_id,
            request.kind,
            request.identity.run_id,
            request.identity.entry,
            request.identity.cid,
            request.identity.execution_id,
            request.identity.plan_order,
            request.supervisor_pid,
            request.run_path,
            request.polled_at,
        ),
    )
    _insert_scheduler_claims(db, permit_id, request)


def release_permit(
    identity: SchedulerIdentity, permit_id: str
) -> SchedulerDelta | None:
    """Delete only one exact scheduler grant after terminal run-state proof."""

    _validate_scheduler_identity(identity)
    if re.fullmatch(r"permit-[0-9a-f]{24}", permit_id) is None:
        raise ActionError("reproduction.scheduler.invalid", "invalid permit identity")
    with _coordinator_lock(identity.project_root):
        with _open_scheduler_database(identity.project_root) as db:
            _audit_scheduler_state(db, identity.project_root)
            current = _permit_for_identity(db, identity)
            if current is None:
                collision = db.execute(
                    "SELECT 1 FROM scheduler_permits WHERE permit_id=?", (permit_id,)
                ).fetchone()
                if collision is not None:
                    raise ActionError(
                        "reproduction.scheduler.invalid",
                        "permit belongs to a different execution",
                    )
                return None
            if current.permit_id != permit_id:
                raise ActionError(
                    "reproduction.scheduler.invalid",
                    "execution owns a different scheduler permit",
                )
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "DELETE FROM scheduler_permits WHERE permit_id=? AND run_id=? "
                "AND entry=? AND cid=? AND execution_id=? AND plan_order=?",
                (
                    permit_id,
                    identity.run_id,
                    identity.entry,
                    identity.cid,
                    identity.execution_id,
                    identity.plan_order,
                ),
            ).rowcount
            if changed != 1:
                db.rollback()
                raise ActionError(
                    "reproduction.scheduler.invalid", "permit release lost its identity"
                )
            _before_scheduler_commit("permit_release", db)
            db.commit()
            return SchedulerDelta(removed_permit_ids=(permit_id,))


def cancel_run_waiters(project_root: Path, run_id: str) -> SchedulerDelta | None:
    """Delete only the durable not-yet-granted waiters for one current run."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ActionError("reproduction.scheduler.invalid", "invalid run identity")
    with _coordinator_lock(project_root):
        with _open_scheduler_database(project_root) as db:
            _audit_scheduler_state(db, project_root)
            rows = db.execute(
                "SELECT ticket FROM scheduler_waiters WHERE run_id=? ORDER BY ticket",
                (run_id,),
            ).fetchall()
            tickets = tuple(int(row["ticket"]) for row in rows)
            if not tickets:
                return None
            db.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _item in tickets)
            changed = db.execute(
                f"DELETE FROM scheduler_waiters WHERE ticket IN ({placeholders})",
                tickets,
            ).rowcount
            if changed != len(tickets):
                db.rollback()
                raise ActionError(
                    "reproduction.scheduler.invalid",
                    "run waiter cancellation lost its identity",
                )
            _before_scheduler_commit("waiter_cancel", db)
            db.commit()
            return SchedulerDelta(removed_waiter_tickets=tickets)


def reconcile_work_admission(
    project_root: Path, run_root: Path
) -> SchedulerDelta | None:
    """Reconcile native grants using only native owner and accepted claim proof."""

    return _reconcile_run_with_job(project_root, run_root, open_work_job)


def _reconcile_run_with_job(
    project_root: Path, run_root: Path, open_job: JobOpener
) -> SchedulerDelta | None:

    removed_waiters: list[int] = []
    removed_permits: list[str] = []
    with _coordinator_lock(project_root):
        with open_job(run_root) as store:
            owner = store.load_scheduler_owner()
            with _open_scheduler_database(project_root) as db:
                _audit_scheduler_state(db, project_root)
                rows = db.execute(
                    "SELECT entry, cid, execution_id, plan_order "
                    "FROM scheduler_waiters WHERE run_id=? "
                    "UNION SELECT entry, cid, execution_id, plan_order "
                    "FROM scheduler_permits WHERE run_id=? "
                    "ORDER BY plan_order, entry, execution_id LIMIT ?",
                    (owner.run_id, owner.run_id, MAX_WAITERS + MAX_PERMITS + 1),
                ).fetchall()
                if len(rows) > MAX_WAITERS + MAX_PERMITS:
                    raise ActionError(
                        "reproduction.scheduler.resource_limit",
                        "run scheduler row count crossed its bound",
                    )
                for row in rows:
                    identity = SchedulerIdentity(
                        project_root,
                        owner.run_id,
                        cast(str, row["entry"]),
                        cast(str, row["cid"]),
                        cast(str, row["execution_id"]),
                        int(row["plan_order"]),
                    )
                    _validate_scheduler_identity(identity)
                    try:
                        accepted = store.load_accepted_scheduling(
                            ExecutionIdentity(
                                identity.entry,
                                identity.cid,
                                identity.execution_id,
                            )
                        )
                    except JobStoreError as error:
                        raise ActionError(
                            "reproduction.scheduler.reconciliation_required",
                            "scheduler row is not an accepted execution",
                        ) from error
                    if accepted.plan_order != identity.plan_order:
                        raise ActionError(
                            "reproduction.scheduler.reconciliation_required",
                            "scheduler row does not match accepted run ownership",
                        )
                    permit = _permit_for_identity(db, identity)
                    if permit is not None:
                        expected = _accepted_scheduler_claims(
                            accepted, run_root.resolve(), project_root
                        )
                        if (
                            permit.kind != accepted.kind
                            or (
                                permit.read_paths,
                                permit.write_paths,
                                permit.run_path,
                                permit.writable_paths,
                            )
                            != expected
                        ):
                            raise ActionError(
                                "reproduction.scheduler.reconciliation_required",
                                "scheduler permit changed accepted claims",
                            )
                    checkpoint = next(
                        (
                            item
                            for item in owner.checkpoints
                            if (item.entry, item.cid, item.execution_id)
                            == (identity.entry, identity.cid, identity.execution_id)
                        ),
                        None,
                    )
                    has_running_worker = any(
                        worker_identity
                        == ExecutionIdentity(
                            identity.entry, identity.cid, identity.execution_id
                        )
                        for worker_identity, _worker in owner.running_workers
                    )
                    reconciliation = _reconcile_scheduler_state(
                        db,
                        identity,
                        owner,
                        checkpoint,
                        has_running_worker,
                    )
                    if reconciliation.delta is not None:
                        removed_waiters.extend(
                            reconciliation.delta.removed_waiter_tickets
                        )
                        removed_permits.extend(reconciliation.delta.removed_permit_ids)
    if not removed_waiters and not removed_permits:
        return None
    return SchedulerDelta(
        removed_waiter_tickets=tuple(sorted(removed_waiters)),
        removed_permit_ids=tuple(sorted(removed_permits)),
    )


def reconcile_permit(
    identity: SchedulerIdentity,
    owner_projection: WorkSchedulerOwner,
) -> SchedulerReconciliation:
    """Reconcile only an exact terminal permit across scheduler and run state."""

    _validate_scheduler_identity(identity)
    if owner_projection.run_id != identity.run_id:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "scheduler and run owner identities disagree",
        )
    checkpoints = [
        checkpoint
        for checkpoint in owner_projection.checkpoints
        if (checkpoint.entry, checkpoint.cid, checkpoint.execution_id)
        == (identity.entry, identity.cid, identity.execution_id)
    ]
    if len(checkpoints) > 1:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "run owner has duplicate permit checkpoints",
        )
    has_running_worker = any(
        worker_identity is not None
        and (worker_identity.entry, worker_identity.cid, worker_identity.execution_id)
        == (identity.entry, identity.cid, identity.execution_id)
        for worker_identity, _worker in owner_projection.running_workers
    )
    checkpoint = checkpoints[0] if checkpoints else None
    with _coordinator_lock(identity.project_root):
        with _open_scheduler_database(identity.project_root) as db:
            _audit_scheduler_state(db, identity.project_root)
            return _reconcile_scheduler_state(
                db,
                identity,
                owner_projection,
                checkpoint,
                has_running_worker,
            )


def _reconcile_scheduler_state(
    db: sqlite3.Connection,
    identity: SchedulerIdentity,
    owner: WorkSchedulerOwner,
    checkpoint: WorkPermitCheckpoint | None,
    has_running_worker: bool,
) -> SchedulerReconciliation:
    current = _permit_for_identity(db, identity)
    waiter = _waiter_for_identity(db, identity)
    if current is not None and waiter is not None:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "execution owns both a waiter and permit",
        )
    if waiter is not None:
        return _reconcile_waiter(db, owner, waiter, has_running_worker)
    if current is None:
        return _reconcile_absent_permit(checkpoint, has_running_worker)
    if checkpoint is None:
        return _reconcile_unattached_permit(
            db, identity, owner, current, has_running_worker
        )
    return _reconcile_attached_permit(
        db, identity, current, checkpoint, has_running_worker
    )


def _reconcile_waiter(
    db: sqlite3.Connection,
    owner: WorkSchedulerOwner,
    waiter: sqlite3.Row,
    has_running_worker: bool,
) -> SchedulerReconciliation:
    if has_running_worker or (
        owner.status is None
        and owner.phase != "stopping"
        and _pid_is_alive(int(waiter["supervisor_pid"]))
    ):
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "exclusive waiter still has live ownership",
        )
    return SchedulerReconciliation(
        None, _delete_scheduler_waiter(db, int(waiter["ticket"]))
    )


def _reconcile_absent_permit(
    checkpoint: WorkPermitCheckpoint | None, has_running_worker: bool
) -> SchedulerReconciliation:
    if checkpoint is None:
        return SchedulerReconciliation(None, None)
    if (
        checkpoint.state in {"succeeded", "failed", "completed", "stopped"}
        and not has_running_worker
    ):
        return SchedulerReconciliation(checkpoint.permit_id, None)
    raise ActionError(
        "reproduction.scheduler.reconciliation_required",
        "run checkpoint has no exact scheduler permit",
    )


def _reconcile_unattached_permit(
    db: sqlite3.Connection,
    identity: SchedulerIdentity,
    owner: WorkSchedulerOwner,
    current: SchedulerPermitProjection,
    has_running_worker: bool,
) -> SchedulerReconciliation:
    if has_running_worker:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "unattached scheduler permit retains a live worker",
        )
    stale = (
        owner.status is not None
        or owner.phase == "stopping"
        or not _pid_is_alive(current.supervisor_pid)
    )
    if not stale:
        return SchedulerReconciliation(None, None)
    return SchedulerReconciliation(
        None, _delete_scheduler_permit(db, identity, current.permit_id)
    )


def _reconcile_attached_permit(
    db: sqlite3.Connection,
    identity: SchedulerIdentity,
    current: SchedulerPermitProjection,
    checkpoint: WorkPermitCheckpoint,
    has_running_worker: bool,
) -> SchedulerReconciliation:
    if checkpoint.permit_id != current.permit_id:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "scheduler permit has no exact run checkpoint",
        )
    if checkpoint.state == "active":
        return SchedulerReconciliation(None, None)
    if (
        checkpoint.state not in {"succeeded", "failed", "completed", "stopped"}
        or has_running_worker
    ):
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            "terminal scheduler permit retains live ownership",
        )
    delta = _delete_scheduler_permit(db, identity, current.permit_id)
    return SchedulerReconciliation(current.permit_id, delta)


def _delete_scheduler_waiter(db: sqlite3.Connection, ticket: int) -> SchedulerDelta:
    db.execute("BEGIN IMMEDIATE")
    changed = db.execute(
        "DELETE FROM scheduler_waiters WHERE ticket=?", (ticket,)
    ).rowcount
    if changed != 1:
        db.rollback()
        raise ActionError(
            "reproduction.scheduler.invalid", "waiter removal lost its identity"
        )
    _before_scheduler_commit("waiter_release", db)
    db.commit()
    return SchedulerDelta(removed_waiter_tickets=(ticket,))


def _delete_scheduler_permit(
    db: sqlite3.Connection, identity: SchedulerIdentity, permit_id: str
) -> SchedulerDelta:
    db.execute("BEGIN IMMEDIATE")
    changed = db.execute(
        "DELETE FROM scheduler_permits WHERE permit_id=? AND run_id=? "
        "AND entry=? AND cid=? AND execution_id=? AND plan_order=?",
        (
            permit_id,
            identity.run_id,
            identity.entry,
            identity.cid,
            identity.execution_id,
            identity.plan_order,
        ),
    ).rowcount
    if changed != 1:
        db.rollback()
        raise ActionError(
            "reproduction.scheduler.invalid", "permit release lost its identity"
        )
    _before_scheduler_commit("permit_reconciliation", db)
    db.commit()
    return SchedulerDelta(removed_permit_ids=(permit_id,))


def _permit_for_identity(
    db: sqlite3.Connection, identity: SchedulerIdentity
) -> SchedulerPermitProjection | None:
    row = db.execute(
        "SELECT permit_id, kind, supervisor_pid, run_path, granted_at "
        "FROM scheduler_permits "
        "WHERE run_id=? AND entry=? AND cid=? AND execution_id=? "
        "AND plan_order=?",
        (
            identity.run_id,
            identity.entry,
            identity.cid,
            identity.execution_id,
            identity.plan_order,
        ),
    ).fetchone()
    if row is None:
        return None
    claims: dict[str, list[str]] = {"read": [], "write": [], "writable": []}
    claim_rows = db.execute(
        "SELECT claim_kind, position, path FROM scheduler_claims "
        "WHERE permit_id=? ORDER BY claim_kind, position LIMIT ?",
        (row["permit_id"], MAX_CLAIMS_PER_KIND * 3 + 1),
    ).fetchall()
    if len(claim_rows) > MAX_CLAIMS_PER_KIND * 3:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "permit claim bound was crossed"
        )
    positions: dict[str, list[int]] = {"read": [], "write": [], "writable": []}
    for claim in claim_rows:
        kind = cast(str, claim["claim_kind"])
        if kind not in claims:
            raise ActionError(
                "reproduction.scheduler.invalid", "stored claim kind is invalid"
            )
        claims[kind].append(cast(str, claim["path"]))
        positions[kind].append(int(claim["position"]))
    if any(values != list(range(len(values))) for values in positions.values()):
        raise ActionError(
            "reproduction.scheduler.invalid", "stored claim positions are invalid"
        )
    permit = SchedulerPermitProjection(
        cast(str, row["permit_id"]),
        identity,
        cast(Literal["ordinary", "exclusive"], row["kind"]),
        int(row["supervisor_pid"]),
        tuple(claims["read"]),
        tuple(claims["write"]),
        cast(str, row["run_path"]),
        tuple(claims["writable"]),
        cast(str, row["granted_at"]),
    )
    _validate_scheduler_permit(permit)
    return permit


def _validate_scheduler_permit(permit: SchedulerPermitProjection) -> None:
    if (
        re.fullmatch(r"permit-[0-9a-f]{24}", permit.permit_id) is None
        or permit.kind not in {"ordinary", "exclusive"}
        or not _positive_pid(permit.supervisor_pid)
        or not _timestamp(permit.granted_at)
        or not _valid_path(permit.run_path)
    ):
        raise ActionError("reproduction.scheduler.invalid", "invalid stored permit")
    for paths in (permit.read_paths, permit.write_paths, permit.writable_paths):
        if (
            tuple(sorted(set(paths))) != paths
            or not all(_valid_path(path) for path in paths)
            or len(paths) > MAX_CLAIMS_PER_KIND
        ):
            raise ActionError("reproduction.scheduler.invalid", "invalid stored claims")


def _require_matching_request(
    permit: SchedulerPermitProjection, request: SchedulerPermitRequest
) -> None:
    requested = (
        request.kind,
        request.supervisor_pid,
        request.read_paths,
        request.write_paths,
        request.run_path,
        request.writable_paths,
    )
    stored = (
        permit.kind,
        permit.supervisor_pid,
        permit.read_paths,
        permit.write_paths,
        permit.run_path,
        permit.writable_paths,
    )
    if requested != stored:
        raise ActionError(
            "reproduction.scheduler.invalid", "existing permit request changed"
        )


def _waiter_for_identity(
    db: sqlite3.Connection, identity: SchedulerIdentity
) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT ticket, plan_order, supervisor_pid, registered_at "
        "FROM scheduler_waiters "
        "WHERE run_id=? AND entry=? AND cid=? AND execution_id=?",
        (identity.run_id, identity.entry, identity.cid, identity.execution_id),
    ).fetchone()
    if row is not None and int(row["plan_order"]) != identity.plan_order:
        raise ActionError("reproduction.scheduler.invalid", "waiter plan order changed")
    return row


def _require_matching_waiter(
    waiter: sqlite3.Row, request: SchedulerPermitRequest
) -> None:
    if int(waiter["supervisor_pid"]) != request.supervisor_pid:
        raise ActionError(
            "reproduction.scheduler.invalid", "existing waiter request changed"
        )


def _next_waiter_ticket(db: sqlite3.Connection) -> int:
    if (
        int(db.execute("SELECT COUNT(*) FROM scheduler_waiters").fetchone()[0])
        >= MAX_WAITERS
    ):
        raise ActionError(
            "reproduction.scheduler.resource_limit",
            "exclusive waiter bound was reached",
        )
    return int(
        _sole_scheduler_row(
            db, "SELECT next_ticket FROM scheduler_state WHERE singleton=1"
        )["next_ticket"]
    )


def _first_retained_waiter_ticket(
    db: sqlite3.Connection,
    removed_waiters: Sequence[int],
    inserted_waiter: int | None,
) -> int | None:
    excluded = set(removed_waiters)
    tickets = [
        int(row["ticket"])
        for row in db.execute(
            "SELECT ticket FROM scheduler_waiters ORDER BY ticket LIMIT ?",
            (MAX_WAITERS + 1,),
        )
        if int(row["ticket"]) not in excluded
    ]
    if len(tickets) > MAX_WAITERS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "waiter bound was crossed"
        )
    if inserted_waiter is not None:
        tickets.append(inserted_waiter)
    return min(tickets, default=None)


def _scheduler_has_permits(
    db: sqlite3.Connection, ignored_permit_ids: Sequence[str] = ()
) -> bool:
    rows = db.execute(
        "SELECT permit_id FROM scheduler_permits ORDER BY permit_id LIMIT ?",
        (MAX_PERMITS + 1,),
    ).fetchall()
    if len(rows) > MAX_PERMITS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "permit bound was crossed"
        )
    ignored = set(ignored_permit_ids)
    return any(cast(str, row["permit_id"]) not in ignored for row in rows)


def _require_permit_capacity(
    db: sqlite3.Connection, removed_permit_ids: Sequence[str] = ()
) -> None:
    permit_count = int(
        db.execute("SELECT COUNT(*) FROM scheduler_permits").fetchone()[0]
    )
    if permit_count - len(set(removed_permit_ids)) >= MAX_PERMITS:
        raise ActionError(
            "reproduction.scheduler.resource_limit",
            "active permit bound was reached",
        )


def _dead_waiter_tickets(db: sqlite3.Connection) -> tuple[int, ...]:
    rows = db.execute(
        "SELECT ticket, supervisor_pid FROM scheduler_waiters ORDER BY ticket LIMIT ?",
        (MAX_WAITERS + 1,),
    ).fetchall()
    if len(rows) > MAX_WAITERS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "waiter bound was crossed"
        )
    return tuple(
        int(row["ticket"])
        for row in rows
        if not _pid_is_alive(int(row["supervisor_pid"]))
    )


def _dead_permits(
    db: sqlite3.Connection, project_root: Path
) -> tuple[SchedulerPermitProjection, ...]:
    rows = db.execute(
        "SELECT run_id, entry, cid, execution_id, plan_order, supervisor_pid "
        "FROM scheduler_permits ORDER BY permit_id LIMIT ?",
        (MAX_PERMITS + 1,),
    ).fetchall()
    if len(rows) > MAX_PERMITS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "permit bound was crossed"
        )
    return tuple(
        permit
        for row in rows
        if not _pid_is_alive(int(row["supervisor_pid"]))
        and (
            permit := _permit_for_identity(
                db,
                SchedulerIdentity(
                    project_root,
                    cast(str, row["run_id"]),
                    cast(str, row["entry"]),
                    cast(str, row["cid"]),
                    cast(str, row["execution_id"]),
                    int(row["plan_order"]),
                ),
            )
        )
        is not None
    )


def _recoverable_dead_permit_ids(
    db: sqlite3.Connection,
    project_root: Path,
    current_run_root: Path,
    current_store: LockedWorkJob,
    *,
    open_job: JobOpener = open_work_job,
) -> tuple[str, ...]:
    permit_ids: list[str] = []
    for permit in _dead_permits(db, project_root):
        run_root = _dead_permit_run_root(project_root, permit)
        try:
            if run_root == current_run_root:
                owner = current_store.load_scheduler_owner()
            else:
                with open_job(run_root) as store:
                    owner = store.load_scheduler_owner()
        except (JobStoreError, OSError) as error:
            raise ActionError(
                "reproduction.scheduler.reconciliation_required",
                f"dead permit owner cannot be inspected: {permit.permit_id}",
            ) from error
        _require_recoverable_dead_permit(permit, owner)
        permit_ids.append(permit.permit_id)
    return tuple(sorted(permit_ids))


def _dead_permit_run_root(
    project_root: Path, permit: SchedulerPermitProjection
) -> Path:
    execution_path = Path(permit.run_path)
    candidates = tuple(
        candidate
        for candidate in (execution_path, *execution_path.parents)[
            : MAX_RUN_PATH_ANCESTORS + 1
        ]
        if project_root in candidate.parents
        and (candidate / "state.sqlite").is_file()
        and not (candidate / "state.sqlite").is_symlink()
    )
    if len(candidates) != 1:
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            f"dead permit has no unique current owner: {permit.permit_id}",
        )
    return candidates[0].resolve()


def _require_recoverable_dead_permit(
    permit: SchedulerPermitProjection, owner: WorkSchedulerOwner
) -> None:
    identity = permit.identity
    matching_checkpoints = tuple(
        checkpoint
        for checkpoint in owner.checkpoints
        if (checkpoint.entry, checkpoint.cid, checkpoint.execution_id)
        == (identity.entry, identity.cid, identity.execution_id)
    )
    durable_owner = owner.owner
    if (
        owner.run_id != identity.run_id
        or durable_owner is None
        or durable_owner.supervisor_pid != permit.supervisor_pid
        or matching_checkpoints
        or owner.running_workers
    ):
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            f"dead permit retains ambiguous ownership: {permit.permit_id}",
        )
    lost_active_owner = owner.status is None and durable_owner.state == "running"
    quiescent_terminal_owner = (
        owner.status in {"complete", "stopped", "failed"}
        and owner.phase is None
        and durable_owner.state != "running"
    )
    if not (lost_active_owner or quiescent_terminal_owner):
        raise ActionError(
            "reproduction.scheduler.reconciliation_required",
            f"dead permit owner is not safely recoverable: {permit.permit_id}",
        )


def _ordinary_request_is_blocked(
    db: sqlite3.Connection,
    request: SchedulerPermitRequest,
    *,
    ignored_waiter_tickets: Sequence[int] = (),
    ignored_permit_ids: Sequence[str] = (),
) -> bool:
    waiters = db.execute(
        "SELECT ticket FROM scheduler_waiters ORDER BY ticket LIMIT ?",
        (MAX_WAITERS + 1,),
    ).fetchall()
    if len(waiters) > MAX_WAITERS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "waiter bound was crossed"
        )
    ignored = set(ignored_waiter_tickets)
    if any(int(row["ticket"]) not in ignored for row in waiters):
        return True
    rows = db.execute(
        "SELECT run_id, entry, cid, execution_id, plan_order FROM scheduler_permits "
        "ORDER BY run_id, plan_order, entry, execution_id LIMIT ?",
        (MAX_PERMITS + 1,),
    ).fetchall()
    if len(rows) > MAX_PERMITS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "active permit bound was crossed"
        )
    ignored_permits = set(ignored_permit_ids)
    for row in rows:
        active = _permit_for_identity(
            db,
            SchedulerIdentity(
                request.identity.project_root,
                cast(str, row["run_id"]),
                cast(str, row["entry"]),
                cast(str, row["cid"]),
                cast(str, row["execution_id"]),
                int(row["plan_order"]),
            ),
        )
        if active is None:
            raise ActionError("reproduction.scheduler.invalid", "permit disappeared")
        if active.permit_id in ignored_permits:
            continue
        if _scheduler_permits_conflict(active, request):
            return True
    return False


def _scheduler_permits_conflict(
    active: SchedulerPermitProjection, request: SchedulerPermitRequest
) -> bool:
    if active.kind == "exclusive" or request.kind == "exclusive":
        return True
    mutable = (*request.write_paths, *request.writable_paths, request.run_path)
    active_mutable = (*active.write_paths, *active.writable_paths, active.run_path)
    return (
        _sets_overlap(mutable, active.read_paths)
        or _sets_overlap(active_mutable, request.read_paths)
        or _sets_overlap(mutable, active_mutable)
    )


def _insert_scheduler_claims(
    db: sqlite3.Connection, permit_id: str, request: SchedulerPermitRequest
) -> None:
    for kind, paths in (
        ("read", request.read_paths),
        ("write", request.write_paths),
        ("writable", request.writable_paths),
    ):
        db.executemany(
            "INSERT INTO scheduler_claims VALUES (?, ?, ?, ?)",
            ((permit_id, kind, position, path) for position, path in enumerate(paths)),
        )


def _validate_scheduler_request(request: SchedulerPermitRequest) -> None:
    _validate_scheduler_identity(request.identity)
    if request.kind not in {"ordinary", "exclusive"}:
        raise ActionError("reproduction.scheduler.invalid", "invalid permit kind")
    if not _positive_pid(request.supervisor_pid) or not _timestamp(request.polled_at):
        raise ActionError("reproduction.scheduler.invalid", "invalid permit poll")
    for paths in (request.read_paths, request.write_paths, request.writable_paths):
        if (
            tuple(sorted(set(paths))) != paths
            or not all(_valid_path(path) for path in paths)
            or len(paths) > MAX_CLAIMS_PER_KIND
        ):
            raise ActionError(
                "reproduction.scheduler.invalid_claim", "invalid path claims"
            )
    if not _valid_path(request.run_path):
        raise ActionError("reproduction.scheduler.invalid_claim", "invalid run path")


def _validate_scheduler_identity(identity: SchedulerIdentity) -> None:
    project_root = identity.project_root
    if (
        not project_root.is_absolute()
        or project_root.resolve() != project_root
        or not (project_root / ".git").exists()
        or RUN_ID_RE.fullmatch(identity.run_id) is None
        or ENTRY_ID_RE.fullmatch(identity.entry) is None
        or CID_RE.fullmatch(identity.cid) is None
        or EXECUTION_ID_RE.fullmatch(identity.execution_id) is None
        or not _positive_int(identity.plan_order)
    ):
        raise ActionError(
            "reproduction.scheduler.invalid", "invalid scheduler identity"
        )


def _scheduler_database_path(project_root: Path) -> Path:
    return operation_directory(project_root) / SCHEDULER_DATABASE_NAME


def _sole_scheduler_row(
    db: sqlite3.Connection, statement: str, parameters: Sequence[object] = ()
) -> sqlite3.Row:
    rows = db.execute(statement, parameters).fetchmany(2)
    if len(rows) != 1:
        raise ActionError(
            "reproduction.scheduler.invalid", "scheduler singleton is invalid"
        )
    return rows[0]


def _audit_scheduler_state(db: sqlite3.Connection, project_root: Path) -> None:
    """Validate bounded scheduler rows before any admission decision."""

    state = _sole_scheduler_row(
        db, "SELECT singleton, next_ticket FROM scheduler_state"
    )
    if state["singleton"] != 1 or not _nonnegative_int(state["next_ticket"]):
        raise ActionError(
            "reproduction.scheduler.invalid", "scheduler state is invalid"
        )
    waiter_rows = db.execute(
        "SELECT ticket, run_id, entry, cid, execution_id, plan_order, "
        "supervisor_pid, registered_at FROM scheduler_waiters "
        "ORDER BY ticket LIMIT ?",
        (MAX_WAITERS + 1,),
    ).fetchall()
    permit_rows = db.execute(
        "SELECT run_id, entry, cid, execution_id, plan_order FROM scheduler_permits "
        "ORDER BY run_id, entry, execution_id LIMIT ?",
        (MAX_PERMITS + 1,),
    ).fetchall()
    if len(waiter_rows) > MAX_WAITERS or len(permit_rows) > MAX_PERMITS:
        raise ActionError(
            "reproduction.scheduler.resource_limit", "scheduler row bound was crossed"
        )
    next_ticket = int(state["next_ticket"])
    for row in waiter_rows:
        identity = SchedulerIdentity(
            project_root,
            cast(str, row["run_id"]),
            cast(str, row["entry"]),
            cast(str, row["cid"]),
            cast(str, row["execution_id"]),
            int(row["plan_order"]),
        )
        _validate_scheduler_identity(identity)
        if (
            not _nonnegative_int(row["ticket"])
            or int(row["ticket"]) >= next_ticket
            or not _positive_pid(row["supervisor_pid"])
            or not _timestamp(row["registered_at"])
        ):
            raise ActionError(
                "reproduction.scheduler.invalid", "stored waiter is invalid"
            )
    permits = tuple(
        permit
        for row in permit_rows
        if (
            permit := _permit_for_identity(
                db,
                SchedulerIdentity(
                    project_root,
                    cast(str, row["run_id"]),
                    cast(str, row["entry"]),
                    cast(str, row["cid"]),
                    cast(str, row["execution_id"]),
                    int(row["plan_order"]),
                ),
            )
        )
        is not None
    )
    waiter_identities = {
        (row["run_id"], row["entry"], row["cid"], row["execution_id"])
        for row in waiter_rows
    }
    permit_identities = {
        (row["run_id"], row["entry"], row["cid"], row["execution_id"])
        for row in permit_rows
    }
    if (
        waiter_identities & permit_identities
        or len(permits) != len(permit_rows)
        or (any(permit.kind == "exclusive" for permit in permits) and len(permits) != 1)
    ):
        raise ActionError(
            "reproduction.scheduler.invalid", "stored permits are inconsistent"
        )


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        raise ActionError(
            "reproduction.scheduler.inspection_unavailable", str(error)
        ) from error
    return True


@contextmanager
def _open_scheduler_database(project_root: Path) -> Iterator[sqlite3.Connection]:
    path = _scheduler_database_path(project_root)
    _require_scheduler_paths(path)
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(path, timeout=0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        _initialize_scheduler_schema(db)
        _require_scheduler_size(db)
        yield db
    except sqlite3.Error as error:
        if db is not None:
            db.rollback()
        raise ActionError("reproduction.scheduler.invalid", str(error)) from error
    finally:
        if db is not None:
            db.close()


def _require_scheduler_paths(path: Path) -> None:
    companions = tuple(
        Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")
    )
    if any(candidate.is_symlink() for candidate in (path, *companions)):
        raise ActionError("reproduction.scheduler.invalid", "unsafe scheduler path")


def _initialize_scheduler_schema(db: sqlite3.Connection) -> None:
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEDULER_STORE_VERSION:
        return
    if version != 0:
        raise ActionError(
            "reproduction.scheduler.unsupported",
            f"scheduler store version {version} is unsupported",
        )
    try:
        db.execute("BEGIN IMMEDIATE")
        for statement in _SCHEDULER_DDL.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute(f"PRAGMA user_version={SCHEDULER_STORE_VERSION}")
        db.execute("INSERT INTO scheduler_state VALUES (1, 0)")
        _before_scheduler_commit("schema_creation", db)
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _require_scheduler_size(db: sqlite3.Connection) -> None:
    page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    if page_count * page_size > MAX_SCHEDULER_BYTES:
        raise ActionError(
            "reproduction.scheduler.resource_limit",
            "scheduler store crossed its byte bound",
        )


def _before_scheduler_commit(_operation: str, _db: sqlite3.Connection) -> None:
    """Test injection point immediately before a scheduler commit."""


def _after_scheduler_grant_before_attach(
    _decision: SchedulerDecision, _store: LockedWorkJob
) -> None:
    """Test injection point after scheduler commit and before run attachment."""


def _after_run_permit_attachment(
    _decision: SchedulerDecision, _store: LockedWorkJob
) -> None:
    """Test injection point after run attachment and before poll return."""


@contextmanager
def _coordinator_lock(project_root: Path) -> Iterator[None]:
    """Serialize threads locally and wait briefly for the cross-process mutex."""

    with _THREAD_LOCK:
        while True:
            manager = operation_lock(project_root, "reproduction-scheduler.lock")
            try:
                manager.__enter__()
            except OperationLockError:
                time.sleep(POLL_SECONDS)
                continue
            try:
                yield
            finally:
                manager.__exit__(None, None, None)
            return


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and Path(value).is_absolute()
        and str(Path(value).resolve()) == value
    )


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_pid(value: object) -> bool:
    return _positive_int(value) and cast(int, value) > 1


def _timestamp(value: object) -> bool:
    return isinstance(value, str) and TIMESTAMP_RE.fullmatch(value) is not None


def _sets_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    return any(_path_overlap(a, b) for a in left for b in right)


def _path_overlap(left: str, right: str) -> bool:
    a = Path(left)
    b = Path(right)
    return a == b or a in b.parents or b in a.parents


def _resolve_claims(value: object, run_root: Path, project_root: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ActionError("reproduction.scheduler.invalid_claim", "invalid path claims")
    return sorted({_resolve_claim(item, run_root, project_root) for item in value})


def _resolve_claim(value: str, run_root: Path, project_root: Path) -> str:
    if value == "<run>":
        return str(run_root.resolve())
    if value.startswith("<run>/"):
        return str((run_root / value.removeprefix("<run>/")).resolve())
    if value.startswith("<project>/"):
        return str((project_root / value.removeprefix("<project>/")).resolve())
    return str(Path(value).resolve())
