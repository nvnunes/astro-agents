from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast
from unittest import mock

from log_commands import reproduction_scheduler as scheduler
from log_commands.context import EntryContext
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_job_storage import (
    AcceptedJob,
    CheckpointOutput,
    ExecutionIdentity,
    ExecutionStart,
    ExecutionTerminal,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
    WorkerRecord,
    begin_run_resume,
    clear_execution_permit,
    clear_execution_scratch,
    create_job,
    finish_run_stop,
    load_execution_readiness,
    load_run_status,
    load_scheduler_owner,
    record_execution_start,
    record_execution_terminal,
    replace_execution_workers,
    replace_run_owner,
    request_run_stop,
)
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from log_commands.reproduction_scheduler import (
    SCHEDULER_DATABASE_NAME,
    SchedulerDecision,
    SchedulerIdentity,
    SchedulerPermitRequest,
    _open_scheduler_database,
    _resolve_claim,
    _resolve_claims,
    poll_permit,
    reconcile_permit,
    release_permit,
)
from test_log_reproduction_planning import _Fixture
from test_reproduction_job_storage import _job_fixture
from validation.operation_state import operation_directory


def _planned(order: int, *, exclusive: bool, leaf: str) -> dict[str, object]:
    return {
        "entry": f"e{order:03d}",
        "execution_id": "pyrun-exec/v1:" + f"{order:x}" * 64,
        "order": order,
        "exclusive": exclusive,
        "read_paths": [f"<project>/origins/{leaf}.csv"],
        "write_paths": [f"<run>/workspace/data/{leaf}.csv"],
        "run_path": f"<run>/executions/e{order:03d}/{leaf}",
        "writable_paths": [f"<run>/runtime/e{order:03d}/{leaf}"],
    }


def _typed_request(
    project: Path,
    run_root: Path,
    run_id: str,
    planned: dict[str, object],
    *,
    supervisor_pid: int | None = None,
    polled_at: str = "2030-01-01T00:00:01Z",
) -> SchedulerPermitRequest:
    identity = SchedulerIdentity(
        project,
        run_id,
        str(planned["entry"]),
        str(planned["execution_id"]),
        int(planned["order"]),
    )
    return SchedulerPermitRequest(
        identity,
        "exclusive" if planned["exclusive"] is True else "ordinary",
        supervisor_pid if supervisor_pid is not None else os.getpid(),
        tuple(_resolve_claims(planned["read_paths"], run_root, project)),
        tuple(_resolve_claims(planned["write_paths"], run_root, project)),
        _resolve_claim(str(planned["run_path"]), run_root, project),
        tuple(_resolve_claims(planned["writable_paths"], run_root, project)),
        polled_at,
    )


def _create_scheduler_run(
    project: Path,
    fixture: _Fixture,
    entry: EntryContext,
    plan: ReproductionPlan,
    *,
    suffix: str,
    exclusive: bool = False,
    supervisor_pid: int | None = None,
    polled_at: str = "2030-01-01T00:00:01Z",
    read_paths: list[str] | None = None,
) -> tuple[Path, SchedulerPermitRequest]:
    executions = list(plan.executions)
    if len(executions) != 1:
        raise AssertionError("scheduler run helper requires one execution")
    execution = dict(executions[0])
    execution.update(
        exclusive=exclusive,
        read_paths=(
            read_paths
            if read_paths is not None
            else [f"<project>/origins/{suffix}.csv"]
        ),
        write_paths=[f"<run>/workspace/data/{suffix}.csv"],
        run_path=f"<run>/executions/{execution['entry']}/{suffix}",
        writable_paths=[f"<run>/runtime/{execution['entry']}/{suffix}"],
    )
    commands = tuple(
        dict(command, exclusive=exclusive)
        if (
            command["entry"],
            command["execution_id"],
        )
        == (execution["entry"], execution["execution_id"])
        else command
        for command in plan.commands
    )
    revised_plan = replace(plan, commands=commands, executions=(execution,))
    run_id = f"reproduce-20300101t000000z-{suffix}"
    accepted_at = "2030-01-01T00:00:00Z"
    leaf = run_leaf(
        fixture.log_root.name,
        entry.id,
        run_id,
    )
    logical = canonical_run_path(accepted_at, leaf).as_posix()
    run_root = project / logical
    run_root.mkdir(parents=True)
    create_job(run_root, AcceptedJob(run_id, revised_plan, accepted_at, logical))
    pid = supervisor_pid if supervisor_pid is not None else os.getpid()
    replace_run_owner(
        run_root,
        RunOwner(pid, "running", "2030-01-01T00:00:00Z", polled_at),
    )
    return run_root, _typed_request(
        project,
        run_root,
        run_id,
        execution,
        supervisor_pid=pid,
        polled_at=polled_at,
    )


def _poll(
    run_root: Path,
    request: SchedulerPermitRequest,
    *,
    checkpointed_at: str = "2030-01-01T00:00:02Z",
    expected_state: Literal["absent", "stopped"] = "absent",
) -> SchedulerDecision:
    return poll_permit(
        run_root,
        request,
        checkpointed_at=checkpointed_at,
        expected_state=expected_state,
    )


class ReproductionSchedulerTests(unittest.TestCase):
    def test_sqlite_poll_binds_accepted_claims_and_durable_owner(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            run_root, request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="binding"
            )
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with self.assertRaises(ActionError) as wrong_claims:
                _poll(
                    run_root,
                    replace(request, read_paths=(str(project / "wrong"),)),
                )
            self.assertEqual(
                wrong_claims.exception.code, "reproduction.scheduler.invalid"
            )
            with self.assertRaises(ActionError) as wrong_owner:
                _poll(run_root, replace(request, supervisor_pid=os.getpid() + 1))
            self.assertEqual(
                wrong_owner.exception.code,
                "reproduction.scheduler.reconciliation_required",
            )
            self.assertFalse(database.exists())
            granted = _poll(run_root, request)
            assert granted.permit is not None
            checkpoint = load_run_status(run_root).checkpoints[0]
            self.assertEqual(checkpoint.state, "active")
            self.assertEqual(checkpoint.permit_id, granted.permit.permit_id)

    def test_sqlite_combined_poll_retries_both_attachment_crash_windows(
        self,
    ) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            before_root, before_request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="crash-before"
            )
            with mock.patch.object(
                scheduler,
                "_after_scheduler_grant_before_attach",
                side_effect=RuntimeError("after scheduler grant"),
            ):
                with self.assertRaisesRegex(RuntimeError, "after scheduler grant"):
                    _poll(before_root, before_request)
            self.assertEqual(load_run_status(before_root).checkpoints, ())
            attached = _poll(before_root, before_request)
            assert attached.permit is not None
            self.assertEqual(
                load_run_status(before_root).checkpoints[0].permit_id,
                attached.permit.permit_id,
            )
            scheduler_path = operation_directory(project) / SCHEDULER_DATABASE_NAME
            run_path = before_root / "state.sqlite"
            before_bytes = (scheduler_path.read_bytes(), run_path.read_bytes())
            before_mtimes = (
                scheduler_path.stat().st_mtime_ns,
                run_path.stat().st_mtime_ns,
            )
            repeated = _poll(before_root, before_request)
            self.assertEqual(repeated.permit, attached.permit)
            self.assertIsNone(repeated.delta)
            self.assertEqual(
                (scheduler_path.read_bytes(), run_path.read_bytes()), before_bytes
            )
            self.assertEqual(
                (scheduler_path.stat().st_mtime_ns, run_path.stat().st_mtime_ns),
                before_mtimes,
            )

            after_root, after_request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="crash-after"
            )
            with mock.patch.object(
                scheduler,
                "_after_run_permit_attachment",
                side_effect=RuntimeError("after run attachment"),
            ):
                with self.assertRaisesRegex(RuntimeError, "after run attachment"):
                    _poll(after_root, after_request)
            durable = load_run_status(after_root).checkpoints[0]
            after_retry = _poll(after_root, after_request)
            assert after_retry.permit is not None
            self.assertEqual(durable.permit_id, after_retry.permit.permit_id)
            self.assertIsNone(after_retry.delta)

    def test_sqlite_admission_reconciles_quiescent_terminal_dead_owner(
        self,
    ) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            dead_root, dead_request = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="terminal-dead",
                supervisor_pid=2**30,
            )
            with mock.patch.object(
                scheduler,
                "_after_scheduler_grant_before_attach",
                side_effect=RuntimeError("terminal crash window"),
            ):
                with self.assertRaisesRegex(RuntimeError, "terminal crash window"):
                    _poll(dead_root, dead_request)
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with sqlite3.connect(database) as db:
                stale_permit = cast(
                    str,
                    db.execute(
                        "SELECT permit_id FROM scheduler_permits"
                    ).fetchone()[0],
                )
            historical = dead_root / "run.json"
            historical.write_bytes(b"{not-json")
            request_run_stop(dead_root, RunStopRequest("2030-01-01T00:00:03Z"))
            replace_run_owner(
                dead_root,
                RunOwner(
                    2**30,
                    "stopped",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:04Z",
                ),
            )
            finish_run_stop(
                dead_root, RunStopCompletion("2030-01-01T00:00:04Z")
            )
            live_root, live_request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="terminal-live"
            )
            admitted = _poll(live_root, live_request)
            assert admitted.delta is not None
            self.assertEqual(admitted.disposition, "granted")
            self.assertEqual(admitted.delta.removed_permit_ids, (stale_permit,))
            self.assertEqual(historical.read_bytes(), b"{not-json")

    def test_sqlite_admission_refuses_dead_owner_with_live_worker(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            dead_root, dead_request = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="worker-dead",
                supervisor_pid=2**30,
            )
            granted = _poll(dead_root, dead_request)
            assert granted.permit is not None
            identity = ExecutionIdentity(
                dead_request.identity.entry, dead_request.identity.execution_id
            )
            record_execution_start(
                dead_root,
                ExecutionStart(
                    identity.entry,
                    identity.execution_id,
                    granted.permit.permit_id,
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    "/private/tmp/reproduction-live-worker",
                ),
            )
            replace_execution_workers(
                dead_root,
                identity,
                (
                    WorkerRecord(
                        "live-worker",
                        None,
                        os.getpid(),
                        "running",
                        "2030-01-01T00:00:03Z",
                        "2030-01-01T00:00:03Z",
                    ),
                ),
            )
            live_root, live_request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="worker-live"
            )
            with self.assertRaises(ActionError) as raised:
                _poll(live_root, live_request)
            self.assertEqual(
                raised.exception.code,
                "reproduction.scheduler.reconciliation_required",
            )

    def test_sqlite_release_is_exact_and_idempotent_only_when_absent(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _run_root,
        ):
            first_root, first = _create_scheduler_run(
                project, fixture, entry, plan, suffix="release-a"
            )
            second_root, second = _create_scheduler_run(
                project, fixture, entry, plan, suffix="release-b"
            )
            first_grant = _poll(first_root, first)
            second_grant = _poll(second_root, second)
            assert first_grant.permit is not None
            assert second_grant.permit is not None
            with self.assertRaises(ActionError):
                release_permit(
                    first.identity, second_grant.permit.permit_id
                )
            self.assertEqual(
                _poll(first_root, first).permit, first_grant.permit
            )
            released = release_permit(
                first.identity, first_grant.permit.permit_id
            )
            self.assertEqual(
                released.removed_permit_ids, (first_grant.permit.permit_id,)
            )
            self.assertIsNone(
                release_permit(first.identity, first_grant.permit.permit_id)
            )
            with self.assertRaises(ActionError):
                release_permit(
                    first.identity, second_grant.permit.permit_id
                )

    def test_sqlite_failed_dependency_is_not_launch_ready(self) -> None:
        with _job_fixture(executions=2, create=False) as (
            project,
            _fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))
            request = _typed_request(
                project,
                run_root,
                accepted.run_id,
                dict(executions[0]),
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:01Z",
                ),
            )
            grant = _poll(run_root, request)
            assert grant.permit is not None
            producer = ExecutionIdentity(
                request.identity.entry, request.identity.execution_id
            )
            dependent = ExecutionIdentity(
                str(executions[1]["entry"]), str(executions[1]["execution_id"])
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    producer.entry,
                    producer.execution_id,
                    grant.permit.permit_id,
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    "/private/tmp/reproduction-scheduler-failed",
                ),
            )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    producer.entry,
                    producer.execution_id,
                    grant.permit.permit_id,
                    "failed",
                    "2030-01-01T00:00:04Z",
                    "2030-01-01T00:00:04Z",
                    1.0,
                    failure_code="execution_failed",
                    failure_message="fixture failure",
                    failure_recorded_at="2030-01-01T00:00:04Z",
                ),
            )
            readiness = load_execution_readiness(run_root, dependent)
            self.assertEqual(readiness.disposition, "dependency_failed")
            self.assertEqual(readiness.failed_dependencies, (producer,))
            self.assertEqual(readiness.pending_dependencies, ())

    def test_sqlite_scheduler_ignores_legacy_coordination_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            (project / ".git").mkdir()
            operation_root = operation_directory(project)
            operation_root.mkdir(parents=True, exist_ok=True)
            legacy = operation_root / "reproduction-scheduler.json"
            legacy.write_text(
                json.dumps(
                    {
                        "schema": "research-log-reproduction-scheduler/1",
                        "next_ticket": 0,
                        "waiters": [],
                        "active": [],
                    }
                ),
                encoding="utf-8",
            )
            before = legacy.read_bytes()
            with _open_scheduler_database(project):
                pass
            self.assertEqual(legacy.read_bytes(), before)
            self.assertTrue((operation_root / SCHEDULER_DATABASE_NAME).is_file())
            self.assertTrue(legacy.is_file())

    def test_sqlite_waiter_reconciliation_requires_stopped_or_dead_owner(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            blocker_root, blocker = _create_scheduler_run(
                project, fixture, entry, plan, suffix="blocker"
            )
            blocker_grant = _poll(blocker_root, blocker)
            assert blocker_grant.permit is not None
            run_root, waiter = _create_scheduler_run(
                project, fixture, entry, plan, suffix="waiter", exclusive=True
            )
            decision = _poll(run_root, waiter)
            self.assertEqual(decision.disposition, "waiting")
            with self.assertRaises(ActionError) as raised:
                reconcile_permit(waiter.identity, load_scheduler_owner(run_root))
            self.assertEqual(
                raised.exception.code,
                "reproduction.scheduler.reconciliation_required",
            )

            request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:02Z"))
            reconciliation = reconcile_permit(
                waiter.identity, load_scheduler_owner(run_root)
            )
            self.assertEqual(
                reconciliation.delta.removed_waiter_tickets,
                (decision.waiter_ticket,),
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "stopped",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:03Z",
                ),
            )
            finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:03Z"))
            self.assertEqual(load_run_status(run_root).status, "stopped")
            release_permit(
                blocker.identity, blocker_grant.permit.permit_id
            )

    def test_sqlite_claim_conflicts_and_dead_waiter_cleanup_are_typed(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _run_root,
        ):
            first_root, first = _create_scheduler_run(
                project, fixture, entry, plan, suffix="claim-a"
            )
            first_grant = _poll(first_root, first)
            assert first_grant.permit is not None
            shared_root, shared = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="claim-shared",
                read_paths=["<project>/origins/claim-a.csv"],
            )
            shared_grant = _poll(shared_root, shared)
            self.assertEqual(shared_grant.disposition, "granted")
            assert shared_grant.permit is not None

            conflict_root, conflict = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="claim-c",
                read_paths=[first.write_paths[0]],
            )
            self.assertEqual(_poll(conflict_root, conflict).disposition, "blocked")

            dead_root, dead = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="claim-dead",
                exclusive=True,
                supervisor_pid=2**30,
            )
            dead_wait = _poll(dead_root, dead)
            self.assertEqual(dead_wait.disposition, "waiting")
            dead_ticket = dead_wait.waiter_ticket
            release_permit(first.identity, first_grant.permit.permit_id)
            release_permit(shared.identity, shared_grant.permit.permit_id)
            after_cleanup = _poll(conflict_root, conflict)
            self.assertEqual(after_cleanup.disposition, "granted")
            self.assertEqual(
                after_cleanup.delta.removed_waiter_tickets, (dead_ticket,)
            )

    def test_sqlite_scheduler_schema_is_exact_and_persists_when_empty(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _run_root,
        ):
            run_root, request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="schema"
            )
            grant = _poll(run_root, request)
            assert grant.permit is not None
            release_permit(request.identity, grant.permit.permit_id)
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            self.assertTrue(database.is_file())
            self.assertFalse(
                (operation_directory(project) / "reproduction-scheduler.json").exists()
            )
            expected_columns = {
                "scheduler_state": ("singleton", "next_ticket"),
                "scheduler_waiters": (
                    "ticket",
                    "run_id",
                    "entry",
                    "execution_id",
                    "plan_order",
                    "supervisor_pid",
                    "registered_at",
                ),
                "scheduler_permits": (
                    "permit_id",
                    "kind",
                    "run_id",
                    "entry",
                    "execution_id",
                    "plan_order",
                    "supervisor_pid",
                    "run_path",
                    "granted_at",
                ),
                "scheduler_claims": (
                    "permit_id",
                    "claim_kind",
                    "position",
                    "path",
                ),
            }
            with sqlite3.connect(database) as db:
                expected_primary_keys = {
                    "scheduler_state": ("singleton",),
                    "scheduler_waiters": ("ticket",),
                    "scheduler_permits": ("permit_id",),
                    "scheduler_claims": (
                        "permit_id",
                        "claim_kind",
                        "position",
                    ),
                }
                for table, columns in expected_columns.items():
                    info = db.execute(f"PRAGMA table_info({table})").fetchall()
                    self.assertEqual(tuple(row[1] for row in info), columns)
                    self.assertEqual(
                        tuple(
                            row[1]
                            for row in sorted(info, key=lambda item: item[5])
                            if row[5]
                        ),
                        expected_primary_keys[table],
                    )
                unique_columns: dict[str, set[tuple[str, ...]]] = {}
                for table in expected_columns:
                    indexes = db.execute(f"PRAGMA index_list({table})").fetchall()
                    unique_columns[table] = {
                        tuple(
                            row[2]
                            for row in db.execute(
                                f"PRAGMA index_info({index[1]})"
                            )
                        )
                        for index in indexes
                        if index[2]
                    }
                self.assertEqual(
                    unique_columns,
                    {
                        "scheduler_state": set(),
                        "scheduler_waiters": {
                            ("run_id", "entry", "execution_id"),
                        },
                        "scheduler_permits": {
                            ("permit_id",),
                            ("run_id", "entry", "execution_id"),
                        },
                        "scheduler_claims": {
                            ("permit_id", "claim_kind", "position"),
                            ("permit_id", "claim_kind", "path"),
                        },
                    },
                )
                explicit_indexes = {
                    row[0]: tuple(
                        item[2]
                        for item in db.execute(f"PRAGMA index_info({row[0]})")
                    )
                    for row in db.execute(
                        "SELECT name FROM sqlite_schema "
                        "WHERE type='index' AND sql IS NOT NULL"
                    )
                }
                self.assertEqual(
                    explicit_indexes,
                    {
                        "scheduler_permits_kind": (
                            "kind",
                            "run_id",
                            "plan_order",
                        ),
                        "scheduler_claims_path": (
                            "claim_kind",
                            "path",
                            "permit_id",
                        ),
                    },
                )
                self.assertEqual(
                    tuple(
                        (row[3], row[4], row[5], row[6])
                        for row in db.execute(
                            "PRAGMA foreign_key_list(scheduler_claims)"
                        )
                    ),
                    (("permit_id", "permit_id", "NO ACTION", "CASCADE"),),
                )
                claims_sql = db.execute(
                    "SELECT sql FROM sqlite_schema "
                    "WHERE type='table' AND name='scheduler_claims'"
                ).fetchone()[0]
                self.assertIn("WITHOUT ROWID", claims_sql.upper())
                table_sql = " ".join(
                    row[0].lower().replace("\n", " ")
                    for row in db.execute(
                        "SELECT sql FROM sqlite_schema "
                        "WHERE type='table' ORDER BY name"
                    )
                )
                for expression in (
                    "check(singleton = 1)",
                    "check(next_ticket >= 0)",
                    "check(ticket >= 0)",
                    "check(plan_order >= 1)",
                    "check(supervisor_pid >= 1)",
                    "check(kind in ('ordinary', 'exclusive'))",
                    "check(claim_kind in ('read', 'write', 'writable'))",
                    "check(position >= 0)",
                ):
                    self.assertIn(expression, table_sql)
            with _open_scheduler_database(project) as db:
                self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(
                    db.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                )
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_sqlite_stop_release_and_resume_preserve_checkpoint_history(self) -> None:
        with _job_fixture(executions=1) as (
            project,
            _fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            planned = dict(plan.executions[0])
            request = _typed_request(
                project, run_root, accepted.run_id, planned
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:01Z",
                ),
            )
            first = _poll(run_root, request)
            assert first.permit is not None
            identity = ExecutionIdentity(
                request.identity.entry, request.identity.execution_id
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    identity.entry,
                    identity.execution_id,
                    first.permit.permit_id,
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    "/private/tmp/reproduction-scheduler-stop",
                ),
            )
            request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:04Z"))
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    identity.entry,
                    identity.execution_id,
                    first.permit.permit_id,
                    "stopped",
                    "2030-01-01T00:00:05Z",
                    None,
                    4.0,
                    failure_code="execution_stopped",
                    failure_message="stopped by fixture",
                    failure_recorded_at="2030-01-01T00:00:05Z",
                ),
            )
            release = reconcile_permit(
                request.identity, load_scheduler_owner(run_root)
            )
            self.assertEqual(
                release.clear_run_permit_id, first.permit.permit_id
            )
            clear_execution_permit(
                run_root,
                identity,
                first.permit.permit_id,
                "stopped",
                "2030-01-01T00:00:06Z",
            )
            clear_execution_scratch(
                run_root, identity, "/private/tmp/reproduction-scheduler-stop"
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "stopped",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:07Z",
                ),
            )
            finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:07Z"))
            self.assertEqual(load_run_status(run_root).status, "stopped")

            begin_run_resume(run_root, RunResumeRequest("2030-01-01T00:00:08Z"))
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:09Z",
                ),
            )
            resumed_request = replace(
                request, polled_at="2030-01-01T00:00:09Z"
            )
            resumed = _poll(
                run_root,
                resumed_request,
                checkpointed_at="2030-01-01T00:00:10Z",
                expected_state="stopped",
            )
            assert resumed.permit is not None
            record_execution_start(
                run_root,
                ExecutionStart(
                    identity.entry,
                    identity.execution_id,
                    resumed.permit.permit_id,
                    "2030-01-01T00:00:11Z",
                    "2030-01-01T00:00:11Z",
                    "/private/tmp/reproduction-scheduler-resume",
                    elapsed_seconds=4.0,
                ),
            )
            command = plan.commands[0]
            state = command["execution_state"]
            assert isinstance(state, dict)
            observed = state["observed"]
            assert isinstance(observed, dict)
            fingerprints = observed["outputs"]
            assert isinstance(fingerprints, dict)
            artifact = str(planned["outputs"][0])
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    identity.entry,
                    identity.execution_id,
                    resumed.permit.permit_id,
                    "succeeded",
                    "2030-01-01T00:00:12Z",
                    "2030-01-01T00:00:12Z",
                    5.0,
                    (CheckpointOutput(artifact, fingerprints[artifact]),),
                ),
            )
            resumed_release = reconcile_permit(
                request.identity, load_scheduler_owner(run_root)
            )
            clear_execution_permit(
                run_root,
                identity,
                resumed_release.clear_run_permit_id,
                "succeeded",
                "2030-01-01T00:00:13Z",
            )
            final = load_run_status(run_root).checkpoints[0]
            self.assertEqual(final.state, "succeeded")
            self.assertEqual(final.started_at, "2030-01-01T00:00:03Z")
            self.assertEqual(final.elapsed_seconds, 5.0)

    def test_sqlite_grant_and_release_interruptions_fail_closed(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            run_root, dead_request = _create_scheduler_run(
                project,
                fixture,
                entry,
                plan,
                suffix="interrupted",
                supervisor_pid=2**30,
            )
            with mock.patch.object(
                scheduler,
                "_after_scheduler_grant_before_attach",
                side_effect=RuntimeError("interrupted permit attachment"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "interrupted permit attachment"
                ):
                    _poll(run_root, dead_request)
            self.assertEqual(load_run_status(run_root).checkpoints, ())
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with sqlite3.connect(database) as db:
                interrupted_permit = cast(
                    str,
                    db.execute(
                        "SELECT permit_id FROM scheduler_permits"
                    ).fetchone()[0],
                )
            unrelated_root, unrelated = _create_scheduler_run(
                project, fixture, entry, plan, suffix="unrelated"
            )
            admitted = _poll(unrelated_root, unrelated)
            self.assertEqual(admitted.disposition, "granted")
            assert admitted.permit is not None
            assert admitted.delta is not None
            self.assertEqual(
                admitted.delta.removed_permit_ids,
                (interrupted_permit,),
            )
            release_permit(unrelated.identity, admitted.permit.permit_id)

            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                ),
            )
            live_request = replace(
                dead_request,
                supervisor_pid=os.getpid(),
                polled_at="2030-01-01T00:00:03Z",
            )
            with mock.patch.object(
                scheduler,
                "_after_run_permit_attachment",
                side_effect=RuntimeError("interrupted permit return"),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted permit return"):
                    _poll(
                        run_root,
                        live_request,
                        checkpointed_at="2030-01-01T00:00:04Z",
                    )
            durable = _poll(
                run_root,
                live_request,
                checkpointed_at="2030-01-01T00:00:04Z",
            )
            assert durable.permit is not None
            surviving = reconcile_permit(
                live_request.identity, load_scheduler_owner(run_root)
            )
            self.assertIsNone(surviving.clear_run_permit_id)
            self.assertIsNone(surviving.delta)
            release_permit(live_request.identity, durable.permit.permit_id)
            with self.assertRaises(ActionError) as raised:
                reconcile_permit(
                    live_request.identity, load_scheduler_owner(run_root)
                )
            self.assertEqual(
                raised.exception.code,
                "reproduction.scheduler.reconciliation_required",
            )
            checkpoint = load_run_status(run_root).checkpoints[0]
            self.assertEqual(checkpoint.state, "active")
            self.assertEqual(checkpoint.permit_id, durable.permit.permit_id)

    def test_sqlite_grant_transaction_rolls_back_as_one_unit(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            run_root, request = _create_scheduler_run(
                project, fixture, entry, plan, suffix="transaction"
            )
            with mock.patch.object(
                scheduler,
                "_insert_scheduler_claims",
                side_effect=RuntimeError("interrupted scheduler grant"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "interrupted scheduler grant"
                ):
                    _poll(run_root, request)
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with sqlite3.connect(database) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM scheduler_permits").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM scheduler_claims").fetchone()[0],
                    0,
                )
            durable = _poll(run_root, request)
            assert durable.permit is not None
            with mock.patch.object(
                scheduler,
                "_before_scheduler_commit",
                side_effect=lambda operation, _db: (
                    (_ for _ in ()).throw(
                        RuntimeError("interrupted scheduler release")
                    )
                    if operation == "permit_release"
                    else None
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "interrupted scheduler release"
                ):
                    release_permit(
                        request.identity, durable.permit.permit_id
                    )
            reopened = _poll(run_root, request)
            self.assertEqual(reopened.permit, durable.permit)
            self.assertIsNone(reopened.delta)

    def test_sqlite_exclusive_tickets_are_monotonic_and_fair(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            ordinary_root, ordinary = _create_scheduler_run(
                project, fixture, entry, plan, suffix="fair-a"
            )
            first_root, first_exclusive = _create_scheduler_run(
                project, fixture, entry, plan, suffix="fair-b", exclusive=True
            )
            second_root, second_exclusive = _create_scheduler_run(
                project, fixture, entry, plan, suffix="fair-c", exclusive=True
            )
            later_root, later_ordinary = _create_scheduler_run(
                project, fixture, entry, plan, suffix="fair-d"
            )

            ordinary_grant = _poll(ordinary_root, ordinary)
            first_wait = _poll(first_root, first_exclusive)
            second_wait = _poll(second_root, second_exclusive)
            self.assertEqual(first_wait.disposition, "waiting")
            self.assertEqual(second_wait.disposition, "waiting")
            self.assertEqual(first_wait.waiter_ticket, 0)
            self.assertEqual(second_wait.waiter_ticket, 1)
            self.assertEqual(_poll(later_root, later_ordinary).disposition, "blocked")

            assert ordinary_grant.permit is not None
            release_permit(ordinary.identity, ordinary_grant.permit.permit_id)
            self.assertEqual(
                _poll(second_root, second_exclusive).disposition, "waiting"
            )
            first_grant = _poll(first_root, first_exclusive)
            self.assertEqual(first_grant.disposition, "granted")
            self.assertEqual(
                _poll(second_root, second_exclusive).disposition, "waiting"
            )
            assert first_grant.permit is not None
            release_permit(
                first_exclusive.identity, first_grant.permit.permit_id
            )
            second_grant = _poll(second_root, second_exclusive)
            self.assertEqual(second_grant.disposition, "granted")
            assert second_grant.permit is not None
            release_permit(
                second_exclusive.identity, second_grant.permit.permit_id
            )

            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with sqlite3.connect(database) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT next_ticket FROM scheduler_state"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM scheduler_waiters").fetchone()[0],
                    0,
                )

    def test_sqlite_unchanged_polls_issue_no_dml(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            project,
            fixture,
            entry,
            plan,
            _accepted,
            _unused_run_root,
        ):
            ordinary_root, ordinary = _create_scheduler_run(
                project, fixture, entry, plan, suffix="nowrite-a"
            )
            exclusive_root, exclusive = _create_scheduler_run(
                project, fixture, entry, plan, suffix="nowrite-b", exclusive=True
            )
            blocked_root, blocked = _create_scheduler_run(
                project, fixture, entry, plan, suffix="nowrite-c"
            )
            ordinary_grant = _poll(ordinary_root, ordinary)
            self.assertEqual(_poll(exclusive_root, exclusive).disposition, "waiting")
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            observer = sqlite3.connect(database)
            try:
                before_version = observer.execute("PRAGMA data_version").fetchone()[0]
                before_bytes = database.read_bytes()
                before_mtime = database.stat().st_mtime_ns
                statements: list[str] = []
                total_changes: list[int] = []
                real_connect = sqlite3.connect

                class TracedConnection(sqlite3.Connection):
                    def close(self) -> None:
                        total_changes.append(self.total_changes)
                        super().close()

                def traced_connect(
                    *args: object, **kwargs: object
                ) -> sqlite3.Connection:
                    kwargs["factory"] = TracedConnection
                    connection = real_connect(*args, **kwargs)
                    connection.set_trace_callback(statements.append)
                    return connection

                with mock.patch(
                    "log_commands.reproduction_scheduler.sqlite3.connect",
                    side_effect=traced_connect,
                ):
                    repeated = _poll(exclusive_root, exclusive)
                    ordinary_blocked = _poll(blocked_root, blocked)
                self.assertEqual(repeated.disposition, "waiting")
                self.assertIsNone(repeated.delta)
                self.assertEqual(ordinary_blocked.disposition, "blocked")
                self.assertIsNone(ordinary_blocked.delta)
                self.assertEqual(total_changes, [0, 0, 0, 0])
                self.assertFalse(
                    any(
                        statement.lstrip().upper().startswith(
                            ("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE")
                        )
                        for statement in statements
                    ),
                    statements,
                )
                self.assertEqual(database.read_bytes(), before_bytes)
                self.assertEqual(database.stat().st_mtime_ns, before_mtime)
                self.assertEqual(
                    observer.execute("PRAGMA data_version").fetchone()[0],
                    before_version,
                )
            finally:
                observer.close()
            assert ordinary_grant.permit is not None
            release_permit(ordinary.identity, ordinary_grant.permit.permit_id)

    def test_sqlite_producer_commit_controls_dependent_readiness(self) -> None:
        with _job_fixture(executions=2, create=False) as (
            project,
            _fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            producer = executions[0]
            dependent = executions[1]
            dependent = dict(
                dependent,
                depends_on=[
                    f"{producer['entry']}:{producer['execution_id']}"
                ],
            )
            executions[1] = dependent
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))

            producer_run_identity = ExecutionIdentity(
                str(producer["entry"]), str(producer["execution_id"])
            )
            dependent_run_identity = ExecutionIdentity(
                str(dependent["entry"]), str(dependent["execution_id"])
            )
            scheduler_identity = SchedulerIdentity(
                project,
                accepted.run_id,
                producer_run_identity.entry,
                producer_run_identity.execution_id,
                int(producer["order"]),
            )
            request = SchedulerPermitRequest(
                scheduler_identity,
                "ordinary",
                os.getpid(),
                tuple(_resolve_claims(producer["read_paths"], run_root, project)),
                tuple(_resolve_claims(producer["write_paths"], run_root, project)),
                _resolve_claim(str(producer["run_path"]), run_root, project),
                tuple(
                    _resolve_claims(producer["writable_paths"], run_root, project)
                ),
                "2030-01-01T00:00:01Z",
            )

            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:01Z",
                ),
            )
            decision = _poll(run_root, request)
            self.assertEqual(decision.disposition, "granted")
            self.assertIsNotNone(decision.permit)
            self.assertIsNotNone(decision.delta)
            permit = decision.permit
            assert permit is not None
            self.assertEqual(decision.delta.inserted_permit_id, permit.permit_id)
            waiting = load_execution_readiness(run_root, dependent_run_identity)
            self.assertEqual(waiting.disposition, "waiting")
            self.assertEqual(
                waiting.pending_dependencies, (producer_run_identity,)
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    producer_run_identity.entry,
                    producer_run_identity.execution_id,
                    permit.permit_id,
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    "/private/tmp/reproduction-scheduler-milestone",
                ),
            )
            command = next(
                item
                for item in plan.commands
                if (
                    item["entry"],
                    item["execution_id"],
                )
                == (
                    producer_run_identity.entry,
                    producer_run_identity.execution_id,
                )
            )
            state = command["execution_state"]
            assert isinstance(state, dict)
            observed = state["observed"]
            assert isinstance(observed, dict)
            fingerprints = observed["outputs"]
            assert isinstance(fingerprints, dict)
            artifact = str(producer["outputs"][0])
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    producer_run_identity.entry,
                    producer_run_identity.execution_id,
                    permit.permit_id,
                    "succeeded",
                    "2030-01-01T00:00:04Z",
                    "2030-01-01T00:00:04Z",
                    1.0,
                    (CheckpointOutput(artifact, fingerprints[artifact]),),
                ),
            )

            ready = load_execution_readiness(run_root, dependent_run_identity)
            self.assertEqual(ready.disposition, "ready")
            self.assertEqual(ready.pending_dependencies, ())
            with _open_scheduler_database(project) as db:
                reopened = scheduler._permit_for_identity(db, scheduler_identity)
            self.assertEqual(reopened, permit)
            owner = load_scheduler_owner(run_root)
            self.assertEqual(owner.checkpoints[0].permit_id, permit.permit_id)

            reconciliation = reconcile_permit(scheduler_identity, owner)
            self.assertEqual(reconciliation.clear_run_permit_id, permit.permit_id)
            self.assertEqual(
                reconciliation.delta.removed_permit_ids, (permit.permit_id,)
            )
            clear_execution_permit(
                run_root,
                producer_run_identity,
                permit.permit_id,
                "succeeded",
                "2030-01-01T00:00:05Z",
            )
            self.assertIsNone(release_permit(scheduler_identity, permit.permit_id))
            reopened_status = load_run_status(run_root)
            self.assertEqual(reopened_status.checkpoints[0].state, "succeeded")
            self.assertIsNone(reopened_status.checkpoints[0].permit_id)
            self.assertEqual(
                reopened_status.checkpoints[0].released_permit_id,
                permit.permit_id,
            )

            scheduler_path = (
                operation_directory(project) / SCHEDULER_DATABASE_NAME
            )
            with sqlite3.connect(scheduler_path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table'"
                    )
                }
            self.assertEqual(
                tables,
                {
                    "scheduler_state",
                    "scheduler_waiters",
                    "scheduler_permits",
                    "scheduler_claims",
                },
            )

    def test_frozen_parallel_fixture_names_remain_covered(self) -> None:
        path = (
            Path(__file__).parent / "fixtures" / "parallel-reproduction-contract.json"
        )
        fixture = json.loads(path.read_text(encoding="utf-8"))
        names = {item["name"] for item in fixture["scenarios"]}

        self.assertEqual(
            names,
            {
                "two-independent-commands",
                "dependency-failure-isolation",
                "exclusive-waiter-across-two-runs",
                "stop-recovery-and-surviving-worker",
                "legacy-run-cutover-compatibility",
            },
        )

if __name__ == "__main__":
    unittest.main()
