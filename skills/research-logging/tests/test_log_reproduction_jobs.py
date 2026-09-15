"""Accepted-plan job boundary coverage."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping
from unittest import mock

from log_commands import reproduction_jobs, reproduction_scheduler
from log_commands.model import ActionError
from log_commands.reproduction_comparison import (
    CurrentRequirementContext,
    ExecutionComparison,
    clear_current_reproduction_requirement,
    compare_current_execution_outputs,
)
from log_commands.reproduction_execution import (
    CurrentExecutionControl,
    ReproductionControlPlaneError,
    ReproductionWorkspace,
    _ProcessOutcome,
    execute_current_planned_recipe,
)
from log_commands.reproduction_execution import (
    WorkerRecord as RuntimeWorkerRecord,
)
from log_commands.reproduction_job_storage import (
    AcceptedJob,
    CheckpointOutput,
    ExecutionIdentity,
    ExecutionStart,
    ExecutionTerminal,
    PublicationFailure,
    PublicationResumeRequest,
    RequirementEffect,
    RunIdentity,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
    audit_job_state,
    begin_publication,
    begin_publication_resume,
    begin_run_resume,
    clear_execution_permit,
    clear_execution_scratch,
    create_job,
    finish_run_stop,
    load_execution_checkpoint,
    load_execution_readiness,
    load_publication_projection,
    load_requirement_effect,
    load_run_owner,
    load_run_status,
    load_scheduler_owner,
    open_locked_job,
    prepare_publication,
    record_execution_comparison,
    record_execution_start,
    record_execution_terminal,
    record_publication_failure,
    record_report_commit,
    record_requirement_effect,
    record_result_commit,
    replace_run_owner,
    request_run_stop,
)
from log_commands.reproduction_jobs import (
    _acquire_scope_locks,
    _compare_current_stage,
    _CurrentSupervisorContext,
    _execute_current_stage,
    _publish_current_stage,
    _reconcile_current_lost_supervisor,
    _supervise_current_job,
    _verify_accepted_materials,
    dry_run_reproduction,
    launch_reproduction,
    load_accepted_plan,
    reproduction_status,
    resume_reproduction,
    stop_reproduction,
    supervise_reproduction,
    supervisor_main,
)
from log_commands.reproduction_promotion import promote_execution
from log_commands.reproduction_result_storage import (
    initialize_empty_reproduction_results,
)
from log_commands.reproduction_scheduler import (
    SCHEDULER_DATABASE_NAME,
    SchedulerIdentity,
    SchedulerPermitRequest,
    _resolve_claim,
    _resolve_claims,
    poll_permit,
    reconcile_permit,
    release_permit,
)
from reproduction_fixed_plan_test_support import (
    accepted_plan,
    current_supervisor_callbacks,
    historical_run,
    publication_run,
)
from research_log_data import InputResource, ResourceIdentity, observe_fingerprint
from research_log_paths import REPRODUCTION_REPORT, RESULTS_STORE
from research_log_result_store import (
    clear_reproduction_results,
    clear_result_store,
    clear_validation_snapshots,
    result_generation,
    result_snapshot,
    result_transaction,
)
from test_log_reproduction_planning import _Fixture
from test_reproduction_job_storage import (
    _cid,
    _comparison,
    _job_fixture,
    _start_and_finish,
)
from validation.fingerprint_cache import (
    CACHE_COMPANION_SUFFIXES as FINGERPRINT_COMPANIONS,
)
from validation.fingerprint_cache import FingerprintCache
from validation.mechanical_values import (
    SelectionItem,
    SelectionResult,
    integer_value,
    selection_dependency,
)
from validation.operation_state import operation_directory, operation_lock
from validation.pyrun_state import load_pyrun_state
from validation.validation_cache import (
    CACHE_COMPANION_SUFFIXES as VALIDATION_COMPANIONS,
)
from validation.validation_cache import ValidationCache


def _populate_generated_caches(
    project: Path, log_root: Path, source: Path
) -> tuple[Path, Path]:
    """Create real fingerprint and cached-selection SQLite state."""

    with FingerprintCache(project, writable=True) as cache:
        cache.observe_regular_file(source)
        fingerprint_path = cache.path
    value = integer_value(1)
    locator = 'v2:{"select":[["value"]]}'
    source_identity = "sha256:" + "a" * 64
    items = (SelectionItem((0, "value"), value, 0, ("value",)),)
    selection = SelectionResult(
        locator_identity=locator,
        source_identity=source_identity,
        source_profile="csv",
        items=items,
        matches=1,
        membership=("value",),
        identities=((integer_value(0),),),
        shape=(1,),
        dependency_projection=selection_dependency(
            source_identity=source_identity,
            locator_identity=locator,
            items=items,
        ),
    )
    with ValidationCache(log_root, writable=True) as cache:
        cache.store_selection(selection, evaluator_version="evaluator/1")
        cache.finish_published_run()
        validation_path = cache.path
    with sqlite3.connect(validation_path) as db:
        count = db.execute("SELECT COUNT(*) FROM evidence_selections").fetchone()[0]
    if count != 1:
        raise AssertionError("fixture did not populate a cached selection")
    return fingerprint_path, validation_path


def _discard_generated_cache(path: Path, suffixes: tuple[str, ...]) -> None:
    """Remove one populated disposable SQLite cache and all safe companions."""

    for suffix in suffixes:
        Path(str(path) + suffix).unlink(missing_ok=True)


class ReproductionJobTests(unittest.TestCase):
    def test_public_supervisor_preserves_the_installed_owner_identity(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            _accepted,
            run_root,
        ):
            registered_at = "2030-01-01T00:00:03Z"
            replace_run_owner(
                run_root,
                RunOwner(os.getpid(), "running", registered_at, registered_at),
            )
            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch("log_commands.reproduction_jobs._supervise_current_job"),
            ):
                supervise_reproduction(
                    fixture.log, run_root, mode="fresh", inherited_locks=()
                )
            self.assertTrue((run_root / "executions").is_dir())
            owner = load_scheduler_owner(run_root).owner
            self.assertIsNotNone(owner)
            if owner is None:
                self.fail("supervisor owner disappeared")
            self.assertEqual(owner.supervisor_pid, os.getpid())
            self.assertEqual(owner.state, "exited")
            self.assertEqual(owner.registered_at, registered_at)

    def test_public_supervisor_cannot_replace_another_durable_owner(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            run_root,
        ):
            foreign = RunOwner(
                os.getpid() + 1,
                "running",
                accepted.accepted_at,
                accepted.accepted_at,
            )
            replace_run_owner(run_root, foreign)
            with self.assertRaises(ActionError) as raised:
                supervise_reproduction(
                    fixture.log, run_root, mode="fresh", inherited_locks=()
                )
            self.assertEqual(raised.exception.code, "reproduction.run.owner_invalid")
            self.assertEqual(load_scheduler_owner(run_root).owner, foreign)
            self.assertIsNone(load_run_status(run_root).status)

    def test_current_dead_owner_recovery_stops_without_restarting_work(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            run_root,
        ):
            replace_run_owner(
                run_root,
                RunOwner(
                    999_999,
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            with (
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    return_value=(),
                ),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            status = load_run_status(run_root)
            self.assertEqual(status.status, "stopped")
            self.assertIsNone(status.phase)
            self.assertEqual(status.checkpoints, ())
            self.assertEqual(load_scheduler_owner(run_root).owner.state, "stopped")

    def test_dead_owner_survivors_are_excluded_by_sqlite_until_exit(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            run_root,
        ):
            replace_run_owner(
                run_root,
                RunOwner(
                    999_999,
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            survivors = (
                {
                    "pid": 888_888,
                    "worker_id": "worker-888888",
                    "state": "running",
                },
            )
            first_registration: str | None = None
            with mock.patch(
                "log_commands.reproduction_jobs._terminate_marked_workers",
                side_effect=(survivors, survivors, ()),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
                status = load_run_status(run_root)
                self.assertIsNone(status.status)
                self.assertEqual(status.workers[0][1].pid, 888_888)
                first_registration = status.workers[0][1].registered_at
                self.assertFalse(
                    any(path.suffix == ".json" for path in run_root.rglob("*"))
                )
                with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                    _acquire_scope_locks(fixture.log, None)
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
                self.assertEqual(
                    load_run_status(run_root).workers[0][1].registered_at,
                    first_registration,
                )
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            self.assertEqual(load_run_status(run_root).status, "stopped")

    def test_dead_owner_recovery_closes_every_checkpoint_crash_boundary(self) -> None:
        for boundary in (
            "active",
            "terminal",
            "scheduler_released",
            "permit_cleared",
            "scratch_removed",
        ):
            with (
                self.subTest(boundary=boundary),
                _job_fixture(executions=1) as (
                    project,
                    fixture,
                    _entry,
                    plan,
                    accepted,
                    run_root,
                ),
            ):
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "running",
                        accepted.accepted_at,
                        accepted.accepted_at,
                    ),
                )
                planned = plan.executions[0]
                entry = str(planned["entry"])
                cid = str(planned["cid"])
                execution_id = str(planned["execution_id"])
                scheduler_identity = SchedulerIdentity(
                    project,
                    accepted.run_id,
                    entry,
                    cid,
                    execution_id,
                    int(planned["order"]),
                )
                request = SchedulerPermitRequest(
                    scheduler_identity,
                    "ordinary",
                    os.getpid(),
                    tuple(_resolve_claims(planned["read_paths"], run_root, project)),
                    tuple(_resolve_claims(planned["write_paths"], run_root, project)),
                    _resolve_claim(str(planned["run_path"]), run_root, project),
                    tuple(
                        _resolve_claims(planned["writable_paths"], run_root, project)
                    ),
                    "2030-01-01T00:00:01Z",
                )
                decision = poll_permit(
                    run_root,
                    request,
                    checkpointed_at="2030-01-01T00:00:02Z",
                    expected_state="absent",
                )
                self.assertIsNotNone(decision.permit)
                if decision.permit is None:
                    self.fail("fixture permit was not granted")
                permit_id = decision.permit.permit_id
                identity = ExecutionIdentity(
                    entry, _cid(plan, entry, execution_id), execution_id
                )
                scratch = Path(
                    tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp")
                )
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        entry,
                        cid,
                        execution_id,
                        permit_id,
                        "2030-01-01T00:00:03Z",
                        "2030-01-01T00:00:03Z",
                        str(scratch),
                    ),
                )
                if boundary != "active":
                    command = next(
                        item
                        for item in plan.commands
                        if (item["entry"], item["cid"], item["execution_id"])
                        == (entry, cid, execution_id)
                    )
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
                            entry,
                            cid,
                            execution_id,
                            permit_id,
                            "succeeded",
                            "2030-01-01T00:00:04Z",
                            "2030-01-01T00:00:04Z",
                            1.0,
                            (CheckpointOutput(artifact, fingerprints[artifact]),),
                        ),
                    )
                if boundary in {
                    "scheduler_released",
                    "permit_cleared",
                    "scratch_removed",
                }:
                    release_permit(scheduler_identity, permit_id)
                if boundary in {"permit_cleared", "scratch_removed"}:
                    clear_execution_permit(
                        run_root,
                        identity,
                        permit_id,
                        "succeeded",
                        "2030-01-01T00:00:05Z",
                    )
                if boundary == "scratch_removed":
                    shutil.rmtree(scratch)
                replace_run_owner(
                    run_root,
                    RunOwner(
                        999_999,
                        "running",
                        accepted.accepted_at,
                        "2030-01-01T00:00:06Z",
                    ),
                )
                with mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    return_value=(),
                ):
                    _reconcile_current_lost_supervisor(
                        fixture.log, run_root, accepted.run_id
                    )
                checkpoint = load_execution_checkpoint(run_root, identity)
                self.assertIsNotNone(checkpoint)
                if checkpoint is None:
                    self.fail("recovery checkpoint disappeared")
                self.assertEqual(
                    checkpoint.state,
                    "stopped" if boundary == "active" else "succeeded",
                )
                self.assertIsNone(checkpoint.permit_id)
                self.assertIsNone(checkpoint.scratch_path)
                self.assertFalse(scratch.exists())
                self.assertEqual(load_run_status(run_root).status, "stopped")

    def test_dead_owner_recovery_removes_unattached_grant_and_waiter(self) -> None:
        with _job_fixture(executions=2) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            dead_pid = 2**30
            replace_run_owner(
                run_root,
                RunOwner(
                    dead_pid,
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            first, second = plan.executions
            first_request = SchedulerPermitRequest(
                SchedulerIdentity(
                    project,
                    accepted.run_id,
                    str(first["entry"]),
                    str(first["cid"]),
                    str(first["execution_id"]),
                    int(first["order"]),
                ),
                "ordinary",
                dead_pid,
                tuple(_resolve_claims(first["read_paths"], run_root, project)),
                tuple(_resolve_claims(first["write_paths"], run_root, project)),
                _resolve_claim(str(first["run_path"]), run_root, project),
                tuple(_resolve_claims(first["writable_paths"], run_root, project)),
                "2030-01-01T00:00:01Z",
            )
            with mock.patch.object(
                reproduction_scheduler,
                "_after_scheduler_grant_before_attach",
                side_effect=RuntimeError("grant-before-attach"),
            ):
                with self.assertRaisesRegex(RuntimeError, "grant-before-attach"):
                    poll_permit(
                        run_root,
                        first_request,
                        checkpointed_at="2030-01-01T00:00:02Z",
                        expected_state="absent",
                    )
            database = operation_directory(project) / SCHEDULER_DATABASE_NAME
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO scheduler_waiters VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        0,
                        accepted.run_id,
                        str(second["entry"]),
                        str(second["cid"]),
                        str(second["execution_id"]),
                        int(second["order"]),
                        dead_pid,
                        "2030-01-01T00:00:02Z",
                    ),
                )
                db.execute("UPDATE scheduler_state SET next_ticket=1 WHERE singleton=1")
            with mock.patch(
                "log_commands.reproduction_jobs._terminate_marked_workers",
                return_value=(),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            with sqlite3.connect(database) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM scheduler_permits WHERE run_id=?",
                        (accepted.run_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM scheduler_waiters WHERE run_id=?",
                        (accepted.run_id,),
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual(load_run_status(run_root).status, "stopped")

    def test_attached_unlaunched_checkpoint_recovers_and_resumes_first_start(
        self,
    ) -> None:
        with _job_fixture(executions=1) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            for name in ("workspace", "runtime", "diagnostics", "executions"):
                (run_root / name).mkdir()
            planned = plan.executions[0]
            identity = SchedulerIdentity(
                project,
                accepted.run_id,
                str(planned["entry"]),
                str(planned["cid"]),
                str(planned["execution_id"]),
                int(planned["order"]),
            )

            def request(second: int) -> SchedulerPermitRequest:
                return SchedulerPermitRequest(
                    identity,
                    "ordinary",
                    os.getpid(),
                    tuple(_resolve_claims(planned["read_paths"], run_root, project)),
                    tuple(_resolve_claims(planned["write_paths"], run_root, project)),
                    _resolve_claim(str(planned["run_path"]), run_root, project),
                    tuple(
                        _resolve_claims(planned["writable_paths"], run_root, project)
                    ),
                    f"2030-01-01T00:00:{second:02d}Z",
                )

            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            attached = poll_permit(
                run_root,
                request(1),
                checkpointed_at="2030-01-01T00:00:02Z",
                expected_state="absent",
            )
            self.assertIsNotNone(attached.permit)
            replace_run_owner(
                run_root,
                RunOwner(
                    2**30,
                    "running",
                    accepted.accepted_at,
                    "2030-01-01T00:00:03Z",
                ),
            )
            with mock.patch(
                "log_commands.reproduction_jobs._terminate_marked_workers",
                return_value=(),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            stopped = load_execution_checkpoint(
                run_root,
                ExecutionIdentity(identity.entry, identity.cid, identity.execution_id),
            )
            self.assertIsNotNone(stopped)
            if stopped is None:
                self.fail("unlaunched checkpoint disappeared")
            self.assertEqual(stopped.state, "stopped")
            self.assertIsNone(stopped.started_at)
            begin_run_resume(run_root, RunResumeRequest("2030-01-01T00:00:04Z"))
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:04Z",
                    "2030-01-01T00:00:04Z",
                ),
            )
            resumed = poll_permit(
                run_root,
                request(5),
                checkpointed_at="2030-01-01T00:00:06Z",
                expected_state="stopped",
            )
            if resumed.permit is None:
                self.fail("resumed permit was not granted")
            workspace = ReproductionWorkspace(
                accepted.run_id,
                run_root,
                project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()

            def succeed(prepared: object, *_args: object):
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("output-00\n", encoding="utf-8")
                return (
                    _ProcessOutcome(0, False, None, None, ()),
                    "2030-01-01T00:00:07Z",
                    1.0,
                )

            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=succeed,
            ):
                execute_current_planned_recipe(
                    fixture.log,
                    plan,
                    planned,
                    workspace,
                    CurrentExecutionControl(
                        resumed.permit.permit_id,
                        resume=True,
                        confinement=confinement,
                    ),
                )
            completed = load_execution_checkpoint(
                run_root,
                ExecutionIdentity(identity.entry, identity.cid, identity.execution_id),
            )
            self.assertIsNotNone(completed)
            if completed is None:
                self.fail("resumed checkpoint disappeared")
            self.assertEqual(completed.state, "succeeded")
            self.assertIsNotNone(completed.started_at)

    def test_dead_owner_recovery_refuses_replaced_scratch_symlink(self) -> None:
        with _job_fixture(executions=1) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            planned = plan.executions[0]
            entry = str(planned["entry"])
            cid = str(planned["cid"])
            execution_id = str(planned["execution_id"])
            scheduler_identity = SchedulerIdentity(
                project,
                accepted.run_id,
                entry,
                cid,
                execution_id,
                int(planned["order"]),
            )
            request = SchedulerPermitRequest(
                scheduler_identity,
                "ordinary",
                os.getpid(),
                tuple(_resolve_claims(planned["read_paths"], run_root, project)),
                tuple(_resolve_claims(planned["write_paths"], run_root, project)),
                _resolve_claim(str(planned["run_path"]), run_root, project),
                tuple(_resolve_claims(planned["writable_paths"], run_root, project)),
                "2030-01-01T00:00:01Z",
            )
            decision = poll_permit(
                run_root,
                request,
                checkpointed_at="2030-01-01T00:00:02Z",
                expected_state="absent",
            )
            if decision.permit is None:
                self.fail("fixture permit was not granted")
            permit_id = decision.permit.permit_id
            scratch = Path(
                tempfile.mkdtemp(prefix="reproduction-scratch-", dir="/private/tmp")
            )
            target = Path(
                tempfile.mkdtemp(
                    prefix="reproduction-scratch-target-", dir="/private/tmp"
                )
            )
            sentinel = target / "sentinel"
            sentinel.write_text("retained\n", encoding="utf-8")
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry,
                    cid,
                    execution_id,
                    permit_id,
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    str(scratch),
                ),
            )
            scratch.rmdir()
            scratch.symlink_to(target, target_is_directory=True)
            replace_run_owner(
                run_root,
                RunOwner(
                    999_999,
                    "running",
                    accepted.accepted_at,
                    "2030-01-01T00:00:04Z",
                ),
            )
            try:
                with (
                    mock.patch(
                        "log_commands.reproduction_jobs._terminate_marked_workers",
                        return_value=(),
                    ),
                    self.assertRaises(ActionError) as raised,
                ):
                    _reconcile_current_lost_supervisor(
                        fixture.log, run_root, accepted.run_id
                    )
                self.assertEqual(raised.exception.code, "reproduction.scratch.invalid")
                self.assertTrue(scratch.is_symlink())
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "retained\n")
                checkpoint = load_execution_checkpoint(
                    run_root,
                    ExecutionIdentity(
                        entry, _cid(plan, entry, execution_id), execution_id
                    ),
                )
                self.assertIsNotNone(checkpoint)
                if checkpoint is None:
                    self.fail("recovery checkpoint disappeared")
                self.assertEqual(checkpoint.scratch_path, str(scratch))
                scratch.unlink()
                scratch.mkdir()
                with mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    return_value=(),
                ):
                    _reconcile_current_lost_supervisor(
                        fixture.log, run_root, accepted.run_id
                    )
                self.assertFalse(scratch.exists())
                self.assertEqual(load_run_status(run_root).status, "stopped")
            finally:
                if scratch.is_symlink():
                    scratch.unlink()
                elif scratch.exists():
                    shutil.rmtree(scratch)
                shutil.rmtree(target)

    def test_terminal_run_recovery_exits_dead_owner_after_report_commit(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            _start_and_finish(run_root, plan, 0)
            comparison = _comparison(plan, 0)
            record_execution_comparison(run_root, comparison)
            record_requirement_effect(
                run_root,
                RequirementEffect(
                    comparison.entry,
                    comparison.cid,
                    comparison.execution_id,
                    "2030-01-01T00:00:03Z",
                ),
            )
            publication_identity = "a" * 64
            prepare_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:00:04Z",
            )
            begin_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:00:05Z",
            )
            record_result_commit(
                run_root,
                publication_identity,
                1,
                updated_at="2030-01-01T00:00:06Z",
            )
            record_report_commit(
                run_root,
                publication_identity,
                1,
                updated_at="2030-01-01T00:00:07Z",
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    999_999,
                    "running",
                    accepted.accepted_at,
                    "2030-01-01T00:00:07Z",
                ),
            )
            owner = load_run_owner(run_root)
            self.assertIsNotNone(owner)
            if owner is None:
                self.fail("terminal fixture owner disappeared")
            self.assertEqual(owner.state, "running")
            survivor = ({"pid": 888_888, "worker_id": "worker-888888"},)
            with mock.patch(
                "log_commands.reproduction_jobs._terminate_marked_workers",
                side_effect=(survivor, ()),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
                retained = load_run_owner(run_root)
                self.assertIsNotNone(retained)
                if retained is None:
                    self.fail("terminal recovery lease disappeared")
                self.assertEqual(retained.state, "running")
                with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                    _acquire_scope_locks(fixture.log, None)
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            owner = load_run_owner(run_root)
            self.assertIsNotNone(owner)
            if owner is None:
                self.fail("reconciled owner disappeared")
            self.assertEqual(owner.state, "exited")
            scheduler_owner = load_scheduler_owner(run_root).owner
            self.assertIsNotNone(scheduler_owner)
            if scheduler_owner is None:
                self.fail("scheduler owner projection disappeared")
            self.assertEqual(scheduler_owner.state, "exited")
            audit_job_state(run_root)

    def test_current_dead_publication_owner_becomes_result_retry(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            _start_and_finish(run_root, plan, 0)
            comparison = _comparison(plan, 0)
            record_execution_comparison(run_root, comparison)
            record_requirement_effect(
                run_root,
                RequirementEffect(
                    comparison.entry,
                    comparison.cid,
                    comparison.execution_id,
                    "2030-01-01T00:01:00Z",
                ),
            )
            publication_identity = "a" * 64
            prepare_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:01:01Z",
            )
            begin_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:01:02Z",
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    999_999,
                    "running",
                    accepted.accepted_at,
                    "2030-01-01T00:01:02Z",
                ),
            )
            with mock.patch(
                "log_commands.reproduction_jobs._acquire_scope_locks",
                return_value=(),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            status = load_run_status(run_root)
            publication = load_publication_projection(run_root).publication
            self.assertEqual(status.status, "failed")
            self.assertIsNotNone(status.operational_failure)
            if status.operational_failure is None:
                self.fail("publication interruption did not record a failure")
            self.assertEqual(
                status.operational_failure.code, "reproduction.publication.failed"
            )
            self.assertEqual(publication.stage, "ready")
            self.assertEqual(
                publication.failure_code, "reproduction.supervisor.interrupted"
            )

    def test_current_publication_recovers_after_result_commit(self) -> None:
        """A reopened publication records the commit and renders only the report."""

        from log_commands import reproduction_publication

        with _job_fixture(executions=1) as (
            project,
            fixture,
            entry,
            plan,
            accepted,
            run_root,
        ):
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            for name in ("workspace", "runtime", "diagnostics", "executions"):
                (run_root / name).mkdir()
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()

            def succeed(prepared: object, *_args: object):
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("output-00\n", encoding="utf-8")
                return _ProcessOutcome(0, False, None, None, ()), None, 1.0

            context = _CurrentSupervisorContext(fixture.log, run_root, plan, "fresh")
            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=succeed,
            ):
                self.assertEqual(
                    _execute_current_stage(context, confinement=confinement),
                    "completed",
                )
            _compare_current_stage(context)
            terminal = load_publication_projection(run_root)
            pyrun_before = (entry.root / "pyrun.json").read_bytes()
            original_commit = (
                reproduction_publication.LockedReproductionPublication
                .publish_result_transaction
            )

            def commit_then_die(publisher: object, request: object):
                original_commit(publisher, request)
                raise SystemExit("injected post-result-commit death")

            with (
                mock.patch(
                    "log_commands.reproduction_publication.publish_reproduction_results",
                    wraps=reproduction_publication.publish_reproduction_results,
                ) as result_transactions,
                mock.patch.object(
                    reproduction_publication.LockedReproductionPublication,
                    "publish_result_transaction",
                    autospec=True,
                    side_effect=commit_then_die,
                ),
                self.assertRaisesRegex(SystemExit, "post-result-commit"),
            ):
                _publish_current_stage(context)
            interrupted = load_publication_projection(run_root)
            self.assertEqual(interrupted.publication.stage, "publishing")
            self.assertIsNone(interrupted.publication.result_generation)
            self.assertEqual(result_transactions.call_count, 1)

            calls: list[str] = []

            def never_execute(_context: object) -> Literal["completed"]:
                self.fail("publication recovery must not execute")

            def never_compare(_context: object) -> None:
                self.fail("publication recovery must not compare")

            with (
                mock.patch(
                    "log_commands.reproduction_execution._run_prepared"
                ) as child,
                mock.patch(
                    "log_commands.reproduction_jobs.compare_current_execution_outputs"
                ) as comparison,
                mock.patch(
                    "log_commands.reproduction_jobs."
                    "clear_current_reproduction_requirement"
                ) as effect,
                mock.patch(
                    "log_commands.reproduction_publication.publish_reproduction_results",
                    wraps=reproduction_publication.publish_reproduction_results,
                ) as retried_transactions,
            ):
                _supervise_current_job(
                    fixture.log,
                    run_root,
                    mode="publication",
                    callbacks=current_supervisor_callbacks(
                        calls,
                        execute=never_execute,
                        compare=never_compare,
                        publish=_publish_current_stage,
                    ),
                )
            self.assertEqual(calls, ["publish"])
            child.assert_not_called()
            comparison.assert_not_called()
            effect.assert_not_called()
            retried_transactions.assert_not_called()
            completed = load_publication_projection(run_root)
            self.assertEqual(completed.publication.stage, "complete")
            self.assertEqual(completed.status, "complete")
            self.assertEqual(
                completed.publication.result_generation,
                completed.publication.report_generation,
            )
            self.assertEqual(completed.checkpoints, terminal.checkpoints)
            self.assertEqual(completed.comparisons, terminal.comparisons)
            self.assertEqual((entry.root / "pyrun.json").read_bytes(), pyrun_before)
            self.assertTrue((fixture.log_root / REPRODUCTION_REPORT).is_file())

    def test_current_publication_failures_resume_at_the_exact_durable_stage(
        self,
    ) -> None:
        """Pre-result and post-result failures reopen without repeating prior work."""

        from log_commands import reproduction_publication

        for failure in ("result", "report"):
            with (
                self.subTest(failure=failure),
                _job_fixture(executions=1) as (
                    project,
                    fixture,
                    entry,
                    plan,
                    accepted,
                    run_root,
                ),
            ):
                interpreter = project / ".conda" / "bin" / "python"
                interpreter.parent.mkdir(parents=True)
                interpreter.symlink_to(sys.executable)
                for name in ("workspace", "runtime", "diagnostics", "executions"):
                    (run_root / name).mkdir()
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "running",
                        accepted.accepted_at,
                        accepted.accepted_at,
                    ),
                )
                confinement = type(
                    "FixtureConfinement",
                    (),
                    {
                        "preflight": lambda self: None,
                        "command": lambda self, command, **_kwargs: list(command),
                    },
                )()

                def succeed(prepared: object, *_args: object):
                    for path in getattr(prepared, "output_paths").values():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("output-00\n", encoding="utf-8")
                    return _ProcessOutcome(0, False, None, None, ()), None, 1.0

                context = _CurrentSupervisorContext(
                    fixture.log, run_root, plan, "fresh"
                )
                with mock.patch(
                    "log_commands.reproduction_execution._run_prepared",
                    side_effect=succeed,
                ):
                    self.assertEqual(
                        _execute_current_stage(context, confinement=confinement),
                        "completed",
                    )
                _compare_current_stage(context)
                terminal = load_publication_projection(run_root)
                pyrun_before = (entry.root / "pyrun.json").read_bytes()
                method = (
                    "publish_result_transaction"
                    if failure == "result"
                    else "materialize_report"
                )
                code = (
                    "reproduction.results.invalid"
                    if failure == "result"
                    else "results.report.render_failed"
                )

                def publish_only(
                    _log: object,
                    _root: Path,
                    *,
                    mode: str,
                    callbacks: object,
                ) -> None:
                    self.assertEqual(mode, "fresh")
                    getattr(callbacks, "publish")(context)

                with (
                    mock.patch(
                        "log_commands.reproduction_jobs.preflight_execution_safety"
                    ),
                    mock.patch(
                        "log_commands.reproduction_jobs.populate_current_output_workspace"
                    ),
                    mock.patch(
                        "log_commands.reproduction_jobs._supervise_current_job",
                        side_effect=publish_only,
                    ),
                    mock.patch.object(
                        reproduction_publication.LockedReproductionPublication,
                        method,
                        side_effect=ActionError(code, f"fixture {failure} failure"),
                    ),
                    self.assertRaises(ActionError) as raised,
                ):
                    supervise_reproduction(
                        fixture.log, run_root, mode="fresh", inherited_locks=()
                    )
                self.assertEqual(raised.exception.code, code)
                failed = load_publication_projection(run_root)
                self.assertEqual(failed.status, "failed")
                self.assertEqual(
                    failed.publication.stage,
                    "ready" if failure == "result" else "result_committed",
                )
                self.assertIsNotNone(failed.publication.publication_identity)
                if failed.publication.publication_identity is None:
                    self.fail("publication identity disappeared")
                begin_publication_resume(
                    run_root,
                    PublicationResumeRequest(
                        failed.publication.publication_identity,
                        failed.publication.stage,
                        "2030-01-01T00:02:00Z",
                    ),
                )
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "running",
                        accepted.accepted_at,
                        "2030-01-01T00:02:00Z",
                    ),
                )
                with mock.patch(
                    "log_commands.reproduction_publication.publish_reproduction_results",
                    wraps=reproduction_publication.publish_reproduction_results,
                ) as result_transactions:
                    _publish_current_stage(replace(context, mode="publication"))
                self.assertEqual(
                    result_transactions.call_count, 1 if failure == "result" else 0
                )
                completed = load_publication_projection(run_root)
                self.assertEqual(completed.status, "complete")
                self.assertEqual(completed.publication.stage, "complete")
                self.assertEqual(completed.checkpoints, terminal.checkpoints)
                self.assertEqual(completed.comparisons, terminal.comparisons)
                self.assertEqual((entry.root / "pyrun.json").read_bytes(), pyrun_before)

    def test_current_timeout_and_surviving_worker_outcomes(self) -> None:
        outcomes = {
            "timeout": _ProcessOutcome(
                1,
                False,
                "execution_timeout",
                "command exceeded the runtime limit of 1 seconds",
                (),
            ),
            "survivor": _ProcessOutcome(
                None,
                True,
                "worker_cleanup_incomplete",
                "supervised workers survived cleanup: 999999",
                (
                    RuntimeWorkerRecord(
                        "worker-999999",
                        None,
                        999999,
                        "pyrun-exec/v2:" + "0" * 64,
                        "running",
                        "2030-01-01T00:00:01Z",
                        "2030-01-01T00:00:02Z",
                        "e001",
                    ),
                ),
            ),
            "lost": _ProcessOutcome(
                1,
                False,
                None,
                None,
                (
                    RuntimeWorkerRecord(
                        "worker-999998",
                        None,
                        999998,
                        "pyrun-exec/v2:" + "0" * 64,
                        "exited",
                        "2030-01-01T00:00:01Z",
                        "2030-01-01T00:00:02Z",
                        "e001",
                    ),
                ),
            ),
        }
        for name, configured in outcomes.items():
            with (
                self.subTest(outcome=name),
                _job_fixture(executions=1) as (
                    project,
                    fixture,
                    _entry,
                    plan,
                    accepted,
                    run_root,
                ),
            ):
                interpreter = project / ".conda" / "bin" / "python"
                interpreter.parent.mkdir(parents=True)
                interpreter.symlink_to(sys.executable)
                for folder in ("workspace", "runtime", "diagnostics", "executions"):
                    (run_root / folder).mkdir()
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "running",
                        accepted.accepted_at,
                        accepted.accepted_at,
                    ),
                )
                confinement = type(
                    "FixtureConfinement",
                    (),
                    {
                        "preflight": lambda self: None,
                        "command": lambda self, command, **_kwargs: list(command),
                    },
                )()
                context = _CurrentSupervisorContext(
                    fixture.log, run_root, plan, "fresh"
                )

                def result(*args: object):
                    callbacks = args[3]
                    if configured.workers:
                        getattr(callbacks, "on_workers")(configured.workers)
                    return configured, "2030-01-01T00:00:01Z", 1.0

                patch = mock.patch(
                    "log_commands.reproduction_execution._run_prepared",
                    side_effect=result,
                )
                if name in {"timeout", "lost"}:
                    with patch:
                        self.assertEqual(
                            _execute_current_stage(context, confinement=confinement),
                            "completed",
                        )
                    checkpoint = load_run_status(run_root).checkpoints[0]
                    self.assertEqual(checkpoint.state, "failed")
                    self.assertEqual(
                        checkpoint.failure_code,
                        (
                            "execution_timeout"
                            if name == "timeout"
                            else "execution_failed"
                        ),
                    )
                    self.assertIsNone(checkpoint.permit_id)
                    self.assertIsNone(checkpoint.scratch_path)
                    if name == "lost":
                        self.assertEqual(
                            [
                                worker.state
                                for _identity, worker in load_run_status(
                                    run_root
                                ).workers
                            ],
                            ["exited"],
                        )
                else:
                    with patch, self.assertRaises(ReproductionControlPlaneError):
                        _execute_current_stage(context, confinement=confinement)
                    checkpoint = load_run_status(run_root).checkpoints[0]
                    self.assertEqual(checkpoint.state, "active")
                    self.assertIsNotNone(checkpoint.permit_id)
                    self.assertIsNotNone(checkpoint.scratch_path)
                    self.assertEqual(
                        [
                            worker.state
                            for _identity, worker in load_run_status(run_root).workers
                        ],
                        ["running"],
                    )
                    assert checkpoint.scratch_path is not None
                    shutil.rmtree(checkpoint.scratch_path, ignore_errors=True)

    def test_cleanup_incomplete_supervisor_main_keeps_sqlite_exclusion_until_recovery(
        self,
    ) -> None:
        with _job_fixture(executions=1) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            survivor = RuntimeWorkerRecord(
                "worker-999999",
                None,
                999999,
                str(plan.executions[0]["execution_id"]),
                "running",
                "2030-01-01T00:00:01Z",
                "2030-01-01T00:00:02Z",
                str(plan.executions[0]["entry"]),
            )

            def incomplete(*args: object):
                getattr(args[3], "on_workers")((survivor,))
                return (
                    _ProcessOutcome(
                        None,
                        True,
                        "worker_cleanup_incomplete",
                        "fixture survivor",
                        (survivor,),
                    ),
                    "2030-01-01T00:00:01Z",
                    1.0,
                )

            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()
            inherited = _acquire_scope_locks(fixture.log, None)
            original_close = reproduction_jobs._close_fds
            boundary_checked = False

            def controlled_supervisor(*args: object, **kwargs: object) -> None:
                supervise_reproduction(
                    args[0],
                    args[1],
                    mode=kwargs["mode"],
                    inherited_locks=kwargs["inherited_locks"],
                    confinement=confinement,
                )

            def close_then_probe(values: tuple[int, ...]) -> None:
                nonlocal boundary_checked
                original_close(values)
                if boundary_checked:
                    return
                boundary_checked = True
                with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                    _acquire_scope_locks(fixture.log, None)

            with (
                mock.patch(
                    "log_commands.reproduction_execution._run_prepared",
                    side_effect=incomplete,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.resolve_log",
                    return_value=fixture.log,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.supervise_reproduction",
                    side_effect=controlled_supervisor,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._close_fds",
                    side_effect=close_then_probe,
                ),
                self.assertRaises(ReproductionControlPlaneError),
            ):
                supervisor_main(
                    (
                        str(fixture.log.summary),
                        str(run_root),
                        ",".join(str(value) for value in inherited),
                        "0",
                    )
                )
            self.assertTrue(boundary_checked)
            failed = load_run_status(run_root)
            self.assertIsNone(failed.status)
            self.assertEqual(failed.phase, "stopping")
            owner = load_run_owner(run_root)
            self.assertIsNotNone(owner)
            if owner is None:
                self.fail("cleanup exclusion lost its owner")
            self.assertEqual(owner.state, "running")
            replace_run_owner(
                run_root,
                RunOwner(
                    2**30,
                    "running",
                    owner.registered_at,
                    owner.last_observed_at,
                ),
            )
            with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                _acquire_scope_locks(fixture.log, None)
            with mock.patch(
                "log_commands.reproduction_jobs._terminate_marked_workers",
                return_value=(),
            ):
                _reconcile_current_lost_supervisor(
                    fixture.log, run_root, accepted.run_id
                )
            terminal = load_run_status(run_root)
            self.assertEqual(terminal.status, "failed")
            self.assertEqual(terminal.workers[0][1].state, "exited")
            self.assertIsNone(terminal.checkpoints[0].permit_id)
            self.assertIsNone(terminal.checkpoints[0].scratch_path)
            recovered_owner = load_run_owner(run_root)
            self.assertIsNotNone(recovered_owner)
            if recovered_owner is None:
                self.fail("quiescent recovery lost its owner")
            self.assertEqual(recovered_owner.state, "stopped")
            with sqlite3.connect(
                operation_directory(project) / SCHEDULER_DATABASE_NAME
            ) as scheduler:
                self.assertEqual(
                    scheduler.execute(
                        "SELECT COUNT(*) FROM scheduler_waiters WHERE run_id=?",
                        (accepted.run_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    scheduler.execute(
                        "SELECT COUNT(*) FROM scheduler_permits WHERE run_id=?",
                        (accepted.run_id,),
                    ).fetchone()[0],
                    0,
                )
            descriptors = _acquire_scope_locks(fixture.log, None)
            for descriptor in descriptors:
                os.close(descriptor)

    def test_current_requirement_effect_recovers_both_crash_sides(self) -> None:
        for failure_side in ("before_file", "after_file"):
            with (
                self.subTest(failure_side=failure_side),
                _job_fixture(executions=1) as (
                    project,
                    fixture,
                    entry,
                    plan,
                    _accepted,
                    run_root,
                ),
            ):
                entry_id, execution_id, _baseline = _start_and_finish(run_root, plan, 0)
                record_execution_comparison(run_root, _comparison(plan, 0))
                result = ExecutionComparison(
                    entry_id,
                    _cid(plan, entry_id, execution_id),
                    execution_id,
                    (),
                    "workspace",
                    True,
                )
                if failure_side == "before_file":
                    target = "log_commands.reproduction_comparison.atomic_write_text"
                else:
                    target = (
                        "log_commands.reproduction_job_storage."
                        "LockedJobStore.record_requirement_effect"
                    )
                with mock.patch(target, side_effect=OSError("injected death")):
                    with self.assertRaises(OSError):
                        clear_current_reproduction_requirement(
                            CurrentRequirementContext(
                                fixture.log, plan, run_root, project
                            ),
                            result,
                            recorded_at="2030-01-01T00:01:01Z",
                        )
                effect = load_requirement_effect(
                    run_root,
                    ExecutionIdentity(
                        entry_id, _cid(plan, entry_id, execution_id), execution_id
                    ),
                )
                self.assertIsNone(effect.requirement_cleared_at)
                state = load_pyrun_state(
                    entry.root / "pyrun.json",
                    entry_root=entry.root,
                    project_root=project,
                )
                updated = state.execution(
                    _cid(plan, entry_id, execution_id), execution_id
                )
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(
                    updated.requires_reproduction, failure_side == "before_file"
                )
                with mock.patch(
                    "log_commands.reproduction_comparison.atomic_write_text",
                    wraps=__import__(
                        "log_commands.reproduction_comparison",
                        fromlist=["atomic_write_text"],
                    ).atomic_write_text,
                ) as writer:
                    clear_current_reproduction_requirement(
                        CurrentRequirementContext(fixture.log, plan, run_root, project),
                        result,
                        recorded_at="2030-01-01T00:01:02Z",
                    )
                self.assertEqual(
                    writer.call_count, 1 if failure_side == "before_file" else 0
                )
                self.assertEqual(
                    load_requirement_effect(
                        run_root,
                        ExecutionIdentity(
                            entry_id, _cid(plan, entry_id, execution_id), execution_id
                        ),
                    ).requirement_cleared_at,
                    "2030-01-01T00:01:02Z",
                )

    def test_current_failure_skips_dependent_and_never_relaunches(self) -> None:
        with _job_fixture(executions=2, create=False) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['cid']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            for name in ("workspace", "runtime", "diagnostics", "executions"):
                (run_root / name).mkdir()
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()
            context = _CurrentSupervisorContext(fixture.log, run_root, plan, "fresh")

            def fail_after_output(prepared: object, *_args: object):
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("partial\n", encoding="utf-8")
                return (
                    _ProcessOutcome(1, False, None, None, ()),
                    "2030-01-01T00:00:02Z",
                    1.0,
                )

            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=fail_after_output,
            ) as child:
                self.assertEqual(
                    _execute_current_stage(context, confinement=confinement),
                    "completed",
                )
            child.assert_called_once()
            _compare_current_stage(context)
            projection = load_publication_projection(run_root)
            self.assertEqual(
                [item.artifacts[0].outcome for item in projection.comparisons],
                ["failed", "skipped"],
            )
            partial = projection.comparisons[0].artifacts[0]
            self.assertTrue(partial.available)
            self.assertIsNotNone(partial.staged_path)
            assert partial.staged_path is not None
            staged = run_root / partial.staged_path
            self.assertEqual(staged.read_text(encoding="utf-8"), "partial\n")
            retained_state = (run_root / "state.sqlite").read_bytes()
            initialize_empty_reproduction_results(
                fixture.log_root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:03Z",
            )
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, _entry.root / "pyrun.json"
            )
            clear_result_store(fixture.log_root)
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            _discard_generated_cache(selection_cache, VALIDATION_COMPANIONS)
            self.assertEqual(staged.read_text(encoding="utf-8"), "partial\n")
            self.assertEqual(
                load_publication_projection(run_root).plan.serialized(),
                plan.serialized(),
            )
            self.assertEqual((run_root / "state.sqlite").read_bytes(), retained_state)
            self.assertEqual(load_run_status(run_root).completed_executions, 2)
            with mock.patch(
                "log_commands.reproduction_execution._run_prepared"
            ) as child:
                self.assertEqual(
                    _execute_current_stage(context, confinement=confinement),
                    "completed",
                )
            child.assert_not_called()
            _compare_current_stage(context)
            self.assertEqual(
                load_publication_projection(run_root).comparisons,
                projection.comparisons,
            )

    def test_current_production_execution_and_comparison_callbacks(self) -> None:
        """Production stage callbacks use durable readiness and effect rows."""

        with _job_fixture(executions=2, create=False) as (
            project,
            fixture,
            entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['cid']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            for name in ("workspace", "runtime", "diagnostics", "executions"):
                (run_root / name).mkdir()
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()
            launch_order: list[str] = []

            def succeed(prepared: object, *_args: object):
                launch_order.append(getattr(prepared, "execution_id"))
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"{path.stem}\n", encoding="utf-8")
                return (
                    _ProcessOutcome(0, False, None, None, ()),
                    "2030-01-01T00:00:02Z",
                    1.0,
                )

            context = _CurrentSupervisorContext(fixture.log, run_root, plan, "fresh")
            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=succeed,
            ):
                self.assertEqual(
                    _execute_current_stage(context, confinement=confinement),
                    "completed",
                )
            self.assertEqual(
                launch_order,
                [
                    str(executions[0]["execution_id"]),
                    str(executions[1]["execution_id"]),
                ],
            )
            _compare_current_stage(context)
            projection = load_publication_projection(run_root)
            self.assertEqual(len(projection.comparisons), 2)
            self.assertTrue(all(item.complete for item in projection.comparisons))
            state = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=project,
            )
            self.assertTrue(
                all(
                    not item.requires_reproduction
                    for _cid_value, _identity, item in state.execution_items()
                )
            )
            self.assertFalse(
                any(
                    path.suffix == ".json"
                    for path in run_root.rglob("*")
                    if path.is_file()
                )
            )

    def test_current_execution_first_identity_stop_resume_and_compare(self) -> None:
        """The production SQLite callbacks reach the no-JSON first milestone."""

        with _job_fixture(executions=2, create=False) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['cid']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:00Z",
                ),
            )
            workspace = ReproductionWorkspace(
                accepted.run_id,
                run_root,
                project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
            ):
                path.mkdir()

            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()
            first = executions[0]
            second = executions[1]
            first_identity = ExecutionIdentity(
                str(first["entry"]), str(first["cid"]), str(first["execution_id"])
            )
            second_identity = ExecutionIdentity(
                str(second["entry"]), str(second["cid"]), str(second["execution_id"])
            )

            def permit(expected_state: Literal["absent", "stopped"], second: int):
                request = SchedulerPermitRequest(
                    SchedulerIdentity(
                        project,
                        accepted.run_id,
                        first_identity.entry,
                        first_identity.cid,
                        first_identity.execution_id,
                        int(first["order"]),
                    ),
                    "ordinary",
                    os.getpid(),
                    tuple(_resolve_claims(first["read_paths"], run_root, project)),
                    tuple(_resolve_claims(first["write_paths"], run_root, project)),
                    _resolve_claim(str(first["run_path"]), run_root, project),
                    tuple(_resolve_claims(first["writable_paths"], run_root, project)),
                    f"2030-01-01T00:00:{second:02d}Z",
                )
                decision = poll_permit(
                    run_root,
                    request,
                    checkpointed_at=f"2030-01-01T00:00:{second + 1:02d}Z",
                    expected_state=expected_state,
                )
                assert decision.permit is not None
                return decision.permit.permit_id

            self.assertEqual(
                load_execution_readiness(run_root, second_identity).disposition,
                "waiting",
            )
            first_permit = permit("absent", 1)

            def stop_attempt(*_args: object):
                request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:03Z"))
                return (
                    _ProcessOutcome(1, True, "stop_requested", "fixture stop", ()),
                    "2030-01-01T00:00:02Z",
                    1.25,
                )

            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=stop_attempt,
            ):
                stopped = execute_current_planned_recipe(
                    fixture.log,
                    plan,
                    first,
                    workspace,
                    CurrentExecutionControl(
                        first_permit,
                        confinement=confinement,
                    ),
                )
            self.assertTrue(stopped.stopped)
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "stopped",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:04Z",
                ),
            )
            finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:04Z"))
            initialize_empty_reproduction_results(
                fixture.log_root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:04Z",
            )
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, _entry.root / "pyrun.json"
            )
            clear_result_store(fixture.log_root)
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            _discard_generated_cache(selection_cache, VALIDATION_COMPANIONS)
            begin_run_resume(run_root, RunResumeRequest("2030-01-01T00:00:05Z"))
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:05Z",
                    "2030-01-01T00:00:05Z",
                ),
            )
            resumed_permit = permit("stopped", 6)

            def succeed(prepared: object, *_args: object):
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("output-00\n", encoding="utf-8")
                return (
                    _ProcessOutcome(0, False, None, None, ()),
                    "2030-01-01T00:00:07Z",
                    2.0,
                )

            with mock.patch(
                "log_commands.reproduction_execution._run_prepared",
                side_effect=succeed,
            ):
                completed = execute_current_planned_recipe(
                    fixture.log,
                    plan,
                    first,
                    workspace,
                    CurrentExecutionControl(
                        resumed_permit,
                        resume=True,
                        confinement=confinement,
                    ),
                )
            checkpoint = load_run_status(run_root).checkpoints[0]
            self.assertEqual(checkpoint.state, "succeeded")
            self.assertGreaterEqual(checkpoint.elapsed_seconds, 3.25)
            compare_current_execution_outputs(
                fixture.log,
                plan,
                workspace,
                completed,
                recorded_at="2030-01-01T00:00:09Z",
            )
            self.assertEqual(
                load_execution_readiness(run_root, second_identity).disposition,
                "ready",
            )
            self.assertTrue(
                load_publication_projection(run_root).comparisons[0].complete
            )
            forbidden = {
                "plan.json",
                "run.json",
                "staging.json",
                "stop.request",
                "scratch-owner.json",
            }
            self.assertTrue(
                forbidden.isdisjoint(
                    path.name for path in run_root.rglob("*") if path.is_file()
                )
            )

    def test_current_execution_callback_is_identity_local(self) -> None:
        """The production callback never serializes or mutates sibling history."""

        from log_commands import reproduction_job_storage

        with _job_fixture(executions=2) as (
            project,
            fixture,
            _entry,
            plan,
            accepted,
            run_root,
        ):
            interpreter = project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            workspace = ReproductionWorkspace(
                accepted.run_id,
                run_root,
                project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
            ):
                path.mkdir()
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    accepted.accepted_at,
                    accepted.accepted_at,
                ),
            )
            planned = plan.executions[0]
            identity = ExecutionIdentity(
                str(planned["entry"]),
                str(planned["cid"]),
                str(planned["execution_id"]),
            )
            decision = poll_permit(
                run_root,
                SchedulerPermitRequest(
                    SchedulerIdentity(
                        project,
                        accepted.run_id,
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        int(planned["order"]),
                    ),
                    "ordinary",
                    os.getpid(),
                    tuple(_resolve_claims(planned["read_paths"], run_root, project)),
                    tuple(_resolve_claims(planned["write_paths"], run_root, project)),
                    _resolve_claim(str(planned["run_path"]), run_root, project),
                    tuple(
                        _resolve_claims(planned["writable_paths"], run_root, project)
                    ),
                    "2030-01-01T00:00:01Z",
                ),
                checkpointed_at="2030-01-01T00:00:02Z",
                expected_state="absent",
            )
            assert decision.permit is not None
            traces: list[str] = []
            writes: list[tuple[str, str]] = []
            original_open = open_locked_job

            @contextmanager
            def traced_open(root: Path) -> Iterator[object]:
                with original_open(
                    root,
                    trace=traces.append,
                    update_hook=lambda operation, table: writes.append(
                        (operation, table)
                    ),
                ) as store:
                    yield store

            def succeed(prepared: object, *_args: object):
                for path in getattr(prepared, "output_paths").values():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("output-00\n", encoding="utf-8")
                return _ProcessOutcome(0, False, None, None, ()), None, 1.0

            confinement = type(
                "FixtureConfinement",
                (),
                {
                    "preflight": lambda self: None,
                    "command": lambda self, command, **_kwargs: list(command),
                },
            )()
            with (
                mock.patch.object(
                    reproduction_job_storage,
                    "open_locked_job",
                    side_effect=traced_open,
                ),
                mock.patch(
                    "log_commands.reproduction_execution._run_prepared",
                    side_effect=succeed,
                ),
            ):
                execute_current_planned_recipe(
                    fixture.log,
                    plan,
                    planned,
                    workspace,
                    CurrentExecutionControl(
                        decision.permit.permit_id, confinement=confinement
                    ),
                )
            sibling = plan.executions[1]
            self.assertIsNone(
                load_execution_checkpoint(
                    run_root,
                    ExecutionIdentity(
                        str(sibling["entry"]),
                        str(sibling["cid"]),
                        str(sibling["execution_id"]),
                    ),
                )
            )
            self.assertFalse(
                any(table.startswith("accepted_") for _operation, table in writes)
            )
            identity_mutations = [
                statement.lower()
                for statement in traces
                if statement.lstrip().lower().startswith(("insert", "update", "delete"))
                and (
                    "execution_checkpoints" in statement.lower()
                    or "checkpoint_outputs" in statement.lower()
                    or "workers" in statement.lower()
                )
            ]
            self.assertTrue(identity_mutations)
            self.assertTrue(
                all(
                    "command_pk" in statement
                    or statement.startswith("insert into checkpoint_outputs values")
                    for statement in identity_mutations
                ),
                identity_mutations,
            )

    def test_current_supervisor_rejects_mode_and_callback_state_mismatch(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            _accepted,
            run_root,
        ):
            calls: list[str] = []

            def stopped_without_state(_context: object) -> Literal["stopped"]:
                return "stopped"

            def unused(_context: object) -> None:
                self.fail("invalid mode must not invoke a stage")

            callbacks = current_supervisor_callbacks(
                calls,
                execute=stopped_without_state,
                compare=unused,
                publish=unused,
            )
            for mode in ("stopped", "publication"):
                with self.assertRaises(ActionError):
                    _supervise_current_job(
                        fixture.log,
                        run_root,
                        mode=mode,
                        callbacks=callbacks,
                    )
                self.assertEqual(calls, [])
            with self.assertRaises(ActionError):
                _supervise_current_job(
                    fixture.log,
                    run_root,
                    mode="fresh",
                    callbacks=callbacks,
                )
            self.assertEqual(calls, ["execute"])

    def test_current_supervisor_routes_stop_resume_and_publication_retry(self) -> None:
        with _job_fixture(executions=2, create=False) as (
            project,
            fixture,
            entry,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['cid']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            from log_commands.reproduction_job_storage import create_job

            create_job(run_root, replace(accepted, plan=plan))
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:01Z",
                ),
            )
            publication_identity = "a" * 64

            def scheduler_request(index: int, timestamp: str) -> SchedulerPermitRequest:
                planned = plan.executions[index]
                identity = SchedulerIdentity(
                    project,
                    accepted.run_id,
                    str(planned["entry"]),
                    str(planned["cid"]),
                    str(planned["execution_id"]),
                    int(planned["order"]),
                )
                return SchedulerPermitRequest(
                    identity,
                    "ordinary",
                    os.getpid(),
                    tuple(_resolve_claims(planned["read_paths"], run_root, project)),
                    tuple(_resolve_claims(planned["write_paths"], run_root, project)),
                    _resolve_claim(str(planned["run_path"]), run_root, project),
                    tuple(
                        _resolve_claims(planned["writable_paths"], run_root, project)
                    ),
                    timestamp,
                )

            def checkpoint_output(index: int) -> CheckpointOutput:
                planned = plan.executions[index]
                command = next(
                    item
                    for item in plan.commands
                    if (item["entry"], item["execution_id"])
                    == (planned["entry"], planned["execution_id"])
                )
                state = command["execution_state"]
                assert isinstance(state, dict)
                observed = state["observed"]
                assert isinstance(observed, dict)
                fingerprints = observed["outputs"]
                assert isinstance(fingerprints, dict)
                artifact = str(planned["outputs"][0])
                return CheckpointOutput(artifact, fingerprints[artifact])

            def finish_success(
                index: int,
                timestamp: int,
                *,
                expected_state: Literal["absent", "stopped"] = "absent",
                elapsed_seconds: float = 1.0,
            ) -> None:
                request = scheduler_request(index, f"2030-01-01T00:00:{timestamp:02d}Z")
                decision = poll_permit(
                    run_root,
                    request,
                    checkpointed_at=f"2030-01-01T00:00:{timestamp + 1:02d}Z",
                    expected_state=expected_state,
                )
                assert decision.permit is not None
                permit_id = decision.permit.permit_id
                identity = ExecutionIdentity(
                    request.identity.entry,
                    request.identity.cid,
                    request.identity.execution_id,
                )
                scratch = f"/private/tmp/current-supervisor-{index}"
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        permit_id,
                        f"2030-01-01T00:00:{timestamp + 2:02d}Z",
                        f"2030-01-01T00:00:{timestamp + 2:02d}Z",
                        scratch,
                        elapsed_seconds=(
                            elapsed_seconds - 1.0
                            if expected_state == "stopped"
                            else 0.0
                        ),
                    ),
                )
                record_execution_terminal(
                    run_root,
                    ExecutionTerminal(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        permit_id,
                        "succeeded",
                        f"2030-01-01T00:00:{timestamp + 3:02d}Z",
                        f"2030-01-01T00:00:{timestamp + 3:02d}Z",
                        elapsed_seconds,
                        (checkpoint_output(index),),
                    ),
                )
                reconciliation = reconcile_permit(
                    request.identity, load_scheduler_owner(run_root)
                )
                assert reconciliation.clear_run_permit_id == permit_id
                clear_execution_permit(
                    run_root,
                    identity,
                    permit_id,
                    "succeeded",
                    f"2030-01-01T00:00:{timestamp + 4:02d}Z",
                )
                clear_execution_scratch(run_root, identity, scratch)

            first_calls: list[str] = []

            def stop_after_producer(_context: object) -> Literal["stopped"]:
                finish_success(0, 1)
                producer_before_stop = load_run_status(run_root).checkpoints[0]
                request = scheduler_request(1, "2030-01-01T00:00:06Z")
                decision = poll_permit(
                    run_root,
                    request,
                    checkpointed_at="2030-01-01T00:00:07Z",
                    expected_state="absent",
                )
                assert decision.permit is not None
                permit_id = decision.permit.permit_id
                identity = ExecutionIdentity(
                    request.identity.entry,
                    request.identity.cid,
                    request.identity.execution_id,
                )
                scratch = "/private/tmp/current-supervisor-stopped"
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        permit_id,
                        "2030-01-01T00:00:08Z",
                        "2030-01-01T00:00:08Z",
                        scratch,
                    ),
                )
                request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:09Z"))
                record_execution_terminal(
                    run_root,
                    ExecutionTerminal(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        permit_id,
                        "stopped",
                        "2030-01-01T00:00:10Z",
                        None,
                        2.0,
                        failure_code="execution_stopped",
                        failure_message="fixture stop",
                        failure_recorded_at="2030-01-01T00:00:10Z",
                    ),
                )
                reconciliation = reconcile_permit(
                    request.identity, load_scheduler_owner(run_root)
                )
                clear_execution_permit(
                    run_root,
                    identity,
                    permit_id,
                    "stopped",
                    "2030-01-01T00:00:11Z",
                )
                self.assertEqual(reconciliation.clear_run_permit_id, permit_id)
                clear_execution_scratch(run_root, identity, scratch)
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "stopped",
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:00:12Z",
                    ),
                )
                finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:12Z"))
                producer_after_stop = load_run_status(run_root).checkpoints[0]
                self.assertEqual(producer_after_stop, producer_before_stop)
                return "stopped"

            def never_stage(_context: object) -> None:
                self.fail("later stage must be suppressed")

            def never_execute(
                _context: object,
            ) -> Literal["completed", "stopped"]:
                self.fail("execution stage must be suppressed")

            _supervise_current_job(
                fixture.log,
                run_root,
                mode="fresh",
                callbacks=current_supervisor_callbacks(
                    first_calls,
                    execute=stop_after_producer,
                    compare=never_stage,
                    publish=never_stage,
                ),
            )
            self.assertEqual(first_calls, ["execute"])
            self.assertEqual(load_run_status(run_root).status, "stopped")
            accepted_bytes = plan.serialized()
            pyrun_stopped = (entry.root / "pyrun.json").read_bytes()

            def assert_stopped_state_retained() -> None:
                self.assertEqual(
                    load_publication_projection(run_root).plan.serialized(),
                    accepted_bytes,
                )
                self.assertEqual(load_run_status(run_root).status, "stopped")
                self.assertEqual(
                    (entry.root / "pyrun.json").read_bytes(), pyrun_stopped
                )

            initialize_empty_reproduction_results(
                fixture.log_root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:12Z",
            )
            with result_transaction(fixture.log_root) as db:
                db.execute(
                    "INSERT OR REPLACE INTO store_state VALUES "
                    "('validation', 1, 'docs/study.md')"
                )
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, entry.root / "pyrun.json"
            )
            clear_validation_snapshots(fixture.log_root)
            with result_snapshot(fixture.log_root) as db:
                self.assertIsNotNone(
                    db.execute(
                        "SELECT 1 FROM store_state WHERE domain='reproduction'"
                    ).fetchone()
                )
            assert_stopped_state_retained()
            with result_transaction(fixture.log_root) as db:
                db.execute(
                    "INSERT OR REPLACE INTO store_state VALUES "
                    "('validation', 2, 'docs/study.md')"
                )
            clear_reproduction_results(fixture.log_root)
            with result_snapshot(fixture.log_root) as db:
                self.assertIsNotNone(
                    db.execute(
                        "SELECT 1 FROM store_state WHERE domain='validation'"
                    ).fetchone()
                )
            assert_stopped_state_retained()
            initialize_empty_reproduction_results(
                fixture.log_root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:12Z",
            )
            clear_validation_snapshots(fixture.log_root)
            clear_reproduction_results(fixture.log_root)
            assert_stopped_state_retained()
            clear_result_store(fixture.log_root)
            assert_stopped_state_retained()
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            self.assertTrue(selection_cache.is_file())
            assert_stopped_state_retained()
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, entry.root / "pyrun.json"
            )
            _discard_generated_cache(selection_cache, VALIDATION_COMPANIONS)
            self.assertTrue(fingerprint_cache.is_file())
            assert_stopped_state_retained()
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, entry.root / "pyrun.json"
            )
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            _discard_generated_cache(selection_cache, VALIDATION_COMPANIONS)
            assert_stopped_state_retained()

            begin_run_resume(run_root, RunResumeRequest("2030-01-01T00:00:13Z"))
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:14Z",
                ),
            )
            resumed_calls: list[str] = []

            def resume_execution(_context: object) -> Literal["completed"]:
                before = load_run_status(run_root).checkpoints[0]
                finish_success(1, 14, expected_state="stopped", elapsed_seconds=3.0)
                self.assertEqual(load_run_status(run_root).checkpoints[0], before)
                return "completed"

            def compare_all(_context: object) -> None:
                for index in range(2):
                    comparison = _comparison(
                        plan,
                        index,
                        recorded_at=f"2030-01-01T00:00:{20 + index:02d}Z",
                    )
                    staged = comparison.artifacts[0].staged_path
                    diagnostic = comparison.diagnostics[0]
                    assert staged is not None
                    staged_path = run_root / staged
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_bytes(f"staged-{index}\n".encode())
                    diagnostic_path = run_root / diagnostic
                    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
                    diagnostic_path.write_text(
                        f"diagnostic-{index}\n", encoding="utf-8"
                    )
                    record_execution_comparison(run_root, comparison)
                    command = next(
                        item
                        for item in plan.commands
                        if (item["entry"], item["execution_id"])
                        == (comparison.entry, comparison.execution_id)
                    )
                    if command["requires_reproduction"] is True:
                        record_requirement_effect(
                            run_root,
                            RequirementEffect(
                                comparison.entry,
                                comparison.cid,
                                comparison.execution_id,
                                f"2030-01-01T00:00:{20 + index:02d}Z",
                            ),
                        )

            def fail_publication(_context: object) -> None:
                prepare_publication(
                    run_root,
                    publication_identity,
                    updated_at="2030-01-01T00:00:22Z",
                )
                begin_publication(
                    run_root,
                    publication_identity,
                    updated_at="2030-01-01T00:00:23Z",
                )
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "exited",
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:00:24Z",
                    ),
                )
                record_publication_failure(
                    run_root,
                    "publishing",
                    PublicationFailure(
                        "results.write_failed",
                        "synthetic pre-result failure",
                        "2030-01-01T00:00:24Z",
                    ),
                )
                raise ActionError(
                    "results.write_failed", "synthetic pre-result failure"
                )

            with self.assertRaises(ActionError) as raised:
                _supervise_current_job(
                    fixture.log,
                    run_root,
                    mode="stopped",
                    callbacks=current_supervisor_callbacks(
                        resumed_calls,
                        execute=resume_execution,
                        compare=compare_all,
                        publish=fail_publication,
                    ),
                )
            self.assertEqual(raised.exception.code, "results.write_failed")
            self.assertEqual(resumed_calls, ["execute", "compare", "publish"])
            self.assertEqual(load_run_status(run_root).status, "failed")
            self.assertEqual(
                load_publication_projection(run_root).publication.stage, "ready"
            )

            pyrun_before = (entry.root / "pyrun.json").read_bytes()
            staged_before = {
                path.relative_to(run_root): path.read_bytes()
                for path in (run_root / "workspace").rglob("*")
                if path.is_file()
            }
            diagnostics_before = {
                path.relative_to(run_root): path.read_bytes()
                for path in (run_root / "diagnostics").rglob("*")
                if path.is_file()
            }
            projection_before = load_publication_projection(run_root)

            def assert_publication_retry_state_retained() -> None:
                self.assertEqual(
                    load_publication_projection(run_root), projection_before
                )
                self.assertEqual((entry.root / "pyrun.json").read_bytes(), pyrun_before)
                self.assertEqual(
                    {
                        path.relative_to(run_root): path.read_bytes()
                        for path in (run_root / "workspace").rglob("*")
                        if path.is_file()
                    },
                    staged_before,
                )
                self.assertEqual(
                    {
                        path.relative_to(run_root): path.read_bytes()
                        for path in (run_root / "diagnostics").rglob("*")
                        if path.is_file()
                    },
                    diagnostics_before,
                )

            initialize_empty_reproduction_results(
                fixture.log_root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:24Z",
            )
            with result_transaction(fixture.log_root) as db:
                db.execute(
                    "INSERT OR REPLACE INTO store_state VALUES "
                    "('validation', 3, 'docs/study.md')"
                )
            fingerprint_cache, selection_cache = _populate_generated_caches(
                project, fixture.log_root, entry.root / "pyrun.json"
            )
            clear_validation_snapshots(fixture.log_root)
            assert_publication_retry_state_retained()
            clear_reproduction_results(fixture.log_root)
            assert_publication_retry_state_retained()
            clear_result_store(fixture.log_root)
            assert_publication_retry_state_retained()
            _discard_generated_cache(fingerprint_cache, FINGERPRINT_COMPANIONS)
            _discard_generated_cache(selection_cache, VALIDATION_COMPANIONS)
            assert_publication_retry_state_retained()

            begin_publication_resume(
                run_root,
                PublicationResumeRequest(
                    publication_identity,
                    "ready",
                    "2030-01-01T00:00:25Z",
                ),
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:25Z",
                ),
            )
            publication_calls: list[str] = []

            def finish_publication(_context: object) -> None:
                begin_publication(
                    run_root,
                    publication_identity,
                    updated_at="2030-01-01T00:00:26Z",
                )
                record_result_commit(
                    run_root,
                    publication_identity,
                    1,
                    updated_at="2030-01-01T00:00:27Z",
                )
                replace_run_owner(
                    run_root,
                    RunOwner(
                        os.getpid(),
                        "exited",
                        "2030-01-01T00:00:00Z",
                        "2030-01-01T00:00:28Z",
                    ),
                )
                record_report_commit(
                    run_root,
                    publication_identity,
                    1,
                    updated_at="2030-01-01T00:00:28Z",
                )

            _supervise_current_job(
                fixture.log,
                run_root,
                mode="publication",
                callbacks=current_supervisor_callbacks(
                    publication_calls,
                    execute=never_execute,
                    compare=never_stage,
                    publish=finish_publication,
                ),
            )
            self.assertEqual(publication_calls, ["publish"])
            self.assertEqual(load_run_status(run_root).status, "complete")
            forbidden = {
                "plan.json",
                "run.json",
                "staging.json",
                "stop.request",
                "supervisor.json",
                "scratch-owner.json",
                "reproduction-scheduler.json",
            }
            self.assertTrue(
                forbidden.isdisjoint(
                    path.name for path in project.rglob("*") if path.is_file()
                )
            )
            self.assertFalse(
                any(
                    path.suffix == ".json"
                    for path in run_root.rglob("*")
                    if path.is_file()
                )
            )

    def test_dry_run_keeps_validation_snapshot_state_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )

            plan = dry_run_reproduction(fixture.log, entry=entry.id, include_all=False)

            self.assertTrue(plan.admission["validation_snapshot_id"])
            self.assertEqual(
                plan.admission["schema"],
                "research-log-reproduction-admission/1",
            )
            self.assertFalse((fixture.log_root / ".cache" / "results.sqlite").exists())
            self.assertFalse((fixture.log_root / "validation.md").exists())

    def test_launch_captures_validation_context_for_rerender(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )
            with mock.patch(
                "log_commands.reproduction_jobs._spawn_supervisor"
            ) as handoff:
                launch_reproduction(fixture.log, entry=entry.id, include_all=False)
            run_root = handoff.call_args.args[1]
            plan = load_accepted_plan(run_root)
            self.assertTrue(plan.admission["validation_snapshot_id"])
            self.assertTrue((fixture.log_root / "validation.md").is_file())
            generation = result_generation(fixture.log_root, "validation")
            fixture.summary.write_text("# Changed\n", encoding="utf-8")
            from log_commands.validation_cli import render_validation
            from validation.snapshot_storage import load_validation_snapshot

            render_validation(fixture.log)
            load_validation_snapshot(fixture.log_root)
            self.assertEqual(
                result_generation(fixture.log_root, "validation"), generation
            )

    def test_publication_reobserves_selected_file_and_pattern_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "inputs"
            root.mkdir()
            selected = root / "selected.txt"
            selected.write_text("selected\n", encoding="utf-8")
            (root / "ignored.bin").write_bytes(b"ignored")
            files = InputResource(
                "files",
                "directory",
                str(root),
                ResourceIdentity("identity-files-sha256-v1", files=("selected.txt",)),
                True,
                str(root),
            )
            patterns = InputResource(
                "patterns",
                "directory",
                str(root),
                ResourceIdentity("identity-patterns-sha256-v1", patterns=("*.txt",)),
                True,
                str(root),
            )
            materials = [
                {
                    "identity": str(root),
                    "kind": "directory",
                    "role": "input",
                    "fingerprint": observe_fingerprint(resource).fingerprint.as_dict(),
                }
                for resource in (files, patterns)
            ]
            plan = replace(
                accepted_plan(),
                comparison_context={
                    "schema": "research-log-reproduction-comparison-context/1",
                    "comparisons": [],
                    "materials": materials,
                    "result_schema": "research-log-reproduction-result/11",
                },
            )
            _verify_accepted_materials(plan)
            (root / "ignored.bin").write_bytes(b"still ignored")
            _verify_accepted_materials(plan)
            selected.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "accepted material changed"):
                _verify_accepted_materials(plan)

    def test_scope_lock_rechecks_sqlite_exclusion_after_initial_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, _root = publication_run(project)
            late_guard = ActionError(
                "reproduction.recovery.active", "late survivor guard"
            )
            with mock.patch(
                "log_commands.reproduction_jobs._require_no_recovery_exclusion",
                side_effect=(None, late_guard),
            ):
                with self.assertRaisesRegex(ActionError, "late survivor guard"):
                    _acquire_scope_locks(log, None)
            fds = _acquire_scope_locks(log, None)
            for descriptor in fds:
                os.close(descriptor)

    def test_fixed_plan_has_no_mutable_run_members(self) -> None:
        keys = set(accepted_plan().as_dict())
        legacy = {
            "attempt",
            "attempts",
            "checkpoints",
            "queue",
            "plan",
            "source_snapshot",
            "validation_snapshot",
        }
        self.assertFalse(keys & legacy)

    def test_plan_and_run_are_separate_strict_canonical_documents(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            _fixture,
            _entry,
            plan,
            _accepted,
            root,
        ):
            self.assertEqual(load_accepted_plan(root), plan)
            self.assertTrue((root / "state.sqlite").is_file())
            self.assertFalse((root / "plan.json").exists())
            self.assertFalse((root / "run.json").exists())

    def test_launch_releases_preparation_lock_before_worker_handoff(self) -> None:
        from log_commands import reproduction_jobs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / ".git").mkdir()
            fixture = _Fixture(root)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [execution])
            observed: list[bool] = []
            held_operation_locks: list[str] = []

            @contextmanager
            def tracked_operation_lock(
                root: Path,
                name: str,
                *,
                mode: Literal["shared", "exclusive"] = "exclusive",
                owner_factory: Callable[[], Mapping[str, object]] | None = None,
            ) -> Iterator[None]:
                with operation_lock(
                    root,
                    name,
                    mode=mode,
                    owner_factory=owner_factory,
                ):
                    held_operation_locks.append(name)
                    try:
                        yield
                    finally:
                        self.assertEqual(held_operation_locks.pop(), name)

            original_create_job = reproduction_jobs.create_job

            def create_after_publication_lock(
                run_root: Path, accepted_job: AcceptedJob
            ) -> RunIdentity:
                self.assertNotIn("reproduction-publication.lock", held_operation_locks)
                return original_create_job(run_root, accepted_job)

            def handoff(log: object, _root: Path, _fds: object, *, mode: str) -> None:
                with operation_lock(getattr(log, "root"), "log.lock", mode="exclusive"):
                    observed.append(mode == "fresh")

            with (
                mock.patch(
                    "log_commands.reproduction_jobs.operation_lock",
                    side_effect=tracked_operation_lock,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.create_job",
                    side_effect=create_after_publication_lock,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=handoff,
                ),
            ):
                launched = launch_reproduction(
                    fixture.log, entry=entry.id, include_all=False
                )
            self.assertIsNotNone(launched.run_id)
            self.assertEqual(observed, [True])

    def test_whole_log_ordinary_launch_creates_a_fresh_run(self) -> None:
        """Omitting --entry remains an ordinary durable launch, not repair mode."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / ".git").mkdir()
            fixture = _Fixture(root)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )
            with mock.patch(
                "log_commands.reproduction_jobs._spawn_supervisor"
            ) as handoff:
                launched = launch_reproduction(
                    fixture.log, entry=None, include_all=False
                )
            self.assertIsNotNone(launched.run_id)
            run_root = handoff.call_args.args[1]
            self.assertIsInstance(run_root, Path)
            self.assertTrue((run_root / "state.sqlite").is_file())
            for retired in (
                "plan.json",
                "run.json",
                "staging.json",
                "stop.request",
                "supervisor.json",
                "checkpoints",
            ):
                self.assertFalse((run_root / retired).exists())
            self.assertEqual(
                load_accepted_plan(run_root).summary,
                fixture.log.summary.resolve().relative_to(root.resolve()).as_posix(),
            )

    def test_status_derives_totals_from_the_accepted_plan_not_run_copies(self) -> None:
        with _job_fixture(executions=2) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            _root,
        ):
            status = reproduction_status(fixture.log, accepted.run_id, reconcile=False)
            self.assertEqual(status["total_executions"], 2)
            self.assertEqual(status["completed_executions"], 0)
            self.assertNotIn("attempt", status)

    def test_racing_resumes_install_one_supervisor_for_one_fixed_plan(self) -> None:
        """The state lock, rather than a stale read, decides the resume winner."""

        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            plan,
            accepted,
            root,
        ):
            run_id = accepted.run_id
            request_run_stop(root, RunStopRequest("2030-01-01T00:00:01Z"))
            finish_run_stop(root, RunStopCompletion("2030-01-01T00:00:02Z"))

            started = threading.Barrier(2)
            supervisors: list[str] = []
            failures: list[BaseException] = []

            def resume() -> None:
                try:
                    started.wait()
                    resume_reproduction(fixture.log, run_id)
                except BaseException as error:  # one caller must lose the race
                    failures.append(error)

            def spawn(_log: object, _root: Path, _fds: object, *, mode: str) -> None:
                self.assertEqual(mode, "stopped")
                supervisors.append(mode)

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._find_run", return_value=root
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._reconcile_current_lost_supervisor"
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=spawn,
                ),
            ):
                threads = [threading.Thread(target=resume) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(supervisors, ["stopped"])
            self.assertEqual(len(failures), 1)
            # Depending on which caller acquires the promotion mutex first, the
            # loser sees either that contention or the rechecked run state.
            self.assertIn(
                getattr(failures[0], "code", ""),
                {
                    "operation.lock.conflict",
                    "reproduction.resume.invalid_state",
                    "reproduction.run.busy",
                },
            )
            latest = load_run_status(root)
            self.assertIsNone(latest.status)
            self.assertEqual(latest.phase, "accepted")
            self.assertEqual(load_accepted_plan(root).serialized(), plan.serialized())

    def test_clearing_results_preserves_an_actual_stopped_job_for_resume(self) -> None:
        """Result deletion does not cross the stopped run's durable boundary."""

        with _job_fixture(executions=1) as (
            _project,
            fixture,
            entry,
            plan,
            accepted,
            run_root,
        ):
            run_id = accepted.run_id
            request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:01Z"))
            finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:02Z"))
            self.assertEqual(load_run_status(run_root).status, "stopped")
            staged = run_root / "executions" / "retained.txt"
            diagnostic = run_root / "diagnostics" / "retained.log"
            for path, content in ((staged, b"staged\n"), (diagnostic, b"diagnostic\n")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            initialize_empty_reproduction_results(
                fixture.log.root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:00Z",
            )
            state_before_clear = (run_root / "state.sqlite").read_bytes()
            preserved = {
                staged: staged.read_bytes(),
                diagnostic: diagnostic.read_bytes(),
                entry.root / "pyrun.json": (entry.root / "pyrun.json").read_bytes(),
            }
            clear_result_store(fixture.log.root)
            self.assertEqual(
                (run_root / "state.sqlite").read_bytes(), state_before_clear
            )
            self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
            resumed_boundary: dict[Path, bytes] = {}

            def observe_resume(
                _log: object, _root: Path, _fds: object, *, mode: str
            ) -> None:
                self.assertEqual(mode, "stopped")
                resumed_boundary.update({path: path.read_bytes() for path in preserved})

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._find_run", return_value=run_root
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._reconcile_current_lost_supervisor"
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=observe_resume,
                ),
            ):
                self.assertEqual(resume_reproduction(fixture.log, run_id), run_id)
            self.assertEqual(resumed_boundary, preserved)
            self.assertEqual(
                load_accepted_plan(run_root).serialized(), plan.serialized()
            )

    def test_every_run_management_surface_refuses_historical_json_unchanged(
        self,
    ) -> None:
        """Historical JSON jobs are classified without decoding or mutation."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, root = historical_run(project)
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            run_id = "reproduce-20300101t000000z-fixture"
            actions = {
                "status/recovery": lambda: reproduction_status(log, run_id),
                "stop": lambda: stop_reproduction(log, run_id),
                "resume": lambda: resume_reproduction(log, run_id),
                "promotion": lambda: promote_execution(
                    log, run_id=run_id, cid="missing", execution_id="missing"
                ),
                "publication": lambda: supervise_reproduction(
                    log, root, mode="publication", inherited_locks=()
                ),
            }
            for surface, action in actions.items():
                with (
                    self.subTest(surface=surface),
                    self.assertRaises(ActionError) as raised,
                ):
                    action()
                self.assertEqual(raised.exception.code, "reproduction.run.unsupported")
                after = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(after, before)

    def test_status_reports_a_corrupt_current_database_as_typed_invalid_state(
        self,
    ) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            run_root,
        ):
            state = run_root / "state.sqlite"
            corrupt = b"not a sqlite database\n"
            state.write_bytes(corrupt)
            with self.assertRaises(ActionError) as raised:
                reproduction_status(fixture.log, accepted.run_id, reconcile=False)
            self.assertEqual(raised.exception.code, "reproduction.run.invalid")
            self.assertEqual(state.read_bytes(), corrupt)

    def test_current_and_historical_duplicate_run_id_is_an_integrity_error(
        self,
    ) -> None:
        with _job_fixture(executions=1) as (
            _project,
            fixture,
            _entry,
            _plan,
            accepted,
            run_root,
        ):
            duplicate = run_root.parent.parent / "2030-01-02" / run_root.name
            duplicate.mkdir(parents=True)
            historical = b"{ malformed historical bytes\n"
            (duplicate / "run.json").write_bytes(historical)
            with self.assertRaises(ActionError) as raised:
                reproduction_status(fixture.log, accepted.run_id, reconcile=False)
            self.assertEqual(raised.exception.code, "reproduction.run.integrity")
            self.assertEqual((duplicate / "run.json").read_bytes(), historical)
