"""Native acceptance and attempt transactions preserve scheduler safety."""

from __future__ import annotations

import fcntl
import multiprocessing
import os
import shutil
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands import reproduction_jobs as jobs
from log_commands import reproduction_planner as planner
from log_commands import reproduction_scheduler as scheduler
from log_commands import reproduction_work_job as storage
from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_artifact_results import (
    ArtifactObservationContext,
    compare_accepted_artifact,
)
from log_commands.reproduction_domain import (
    CommandOutcome,
    ExecutionRef,
    ProblemStage,
    ReproductionDomainError,
    ReproductionProblem,
)
from log_commands.reproduction_job_control import (
    ExecutionIdentity,
    ExecutionPermitAttachment,
    ExecutionStart,
    JobStoreExistsError,
    JobStoreInvariantError,
    JobStoreMalformedError,
    JobStoreMissingError,
    JobStoreSymlinkError,
    JobStoreTransitionError,
    JobStoreUnavailableError,
    JobStoreUnsupportedError,
    RecoveryWorkerObservation,
    RunFailure,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
    WorkerRecord,
)
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from log_commands.reproduction_run import CommandResult
from log_commands.reproduction_work_job import (
    AttemptCompletion,
    AttemptInterruption,
    WorkJobAcceptance,
    accepted_scheduling_projection,
    create_work_job,
    open_work_job,
    snapshot_work_job,
)
from log_commands.reproduction_work_plan import ReproductionPlan
from test_reproduction_canonical_records import FINGERPRINT, WHEN
from test_reproduction_model_preservation import fanout_fixture
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.operation_state import research_snapshot


def _create_job_process(
    root, run_id, run_path, project, serialized, ready, start, result
):
    plan = ReproductionPlan.from_json(serialized)
    ready.put(True)
    if not start.wait(15):
        result.put("timeout")
        return
    try:
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, run_path, project))
    except JobStoreExistsError:
        result.put("exists")
    else:
        result.put("created")


def _write_workers_process(root, identity, permit, worker_id, ready, start, result):
    ready.put(True)
    if not start.wait(15):
        result.put("timeout")
        return
    try:
        worker = WorkerRecord(worker_id, None, os.getpid(), "running", WHEN, WHEN)
        for _ in range(6):
            with open_work_job(root) as job:
                job.replace_execution_workers(identity, permit, (worker,))
    except Exception as error:
        result.put(f"{type(error).__name__}: {error}")
    else:
        result.put("written")


def _crash_before_stop_commit(root):
    with mock.patch.object(
        storage, "_before_commit", side_effect=lambda *_: os._exit(17)
    ):
        with open_work_job(root) as job:
            job.request_run_stop(RunStopRequest(WHEN))


class WorkJobTests(unittest.TestCase):
    def test_status_is_observational_and_does_not_load_plan(self):
        self.accept()
        with (
            mock.patch.object(jobs, "_pid_alive", side_effect=AssertionError("pid")),
            mock.patch.object(
                storage, "_load_accepted_work", side_effect=AssertionError("plan")
            ),
        ):
            status = jobs.reproduction_status(self.fixture.log, self.run_id)
        self.assertEqual(status["phase"], "accepted")
        with snapshot_work_job(self.root) as job:
            self.assertEqual(job._db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                job._db.execute("UPDATE run_state SET phase='stopping'")

    def test_status_snapshot_does_not_mix_committed_lifecycle_states(self):
        self.accept()
        with snapshot_work_job(self.root) as snapshot:
            self.assertEqual(snapshot.load_run_control().phase, "accepted")
            with open_work_job(self.root) as writer:
                writer.request_run_stop(RunStopRequest(WHEN))
                writer.acknowledge_run_stop()
            projected = snapshot.load_operational_status()
            self.assertEqual(projected["phase"], "accepted")
            self.assertIsNone(snapshot.load_run_control().stop_requested_at)
        with snapshot_work_job(self.root) as latest:
            projected = latest.load_operational_status()
            self.assertEqual(projected["phase"], "stopping")
            self.assertEqual(latest.load_run_control().stop_requested_at, WHEN)

    def test_active_elapsed_is_projected_without_checkpoint_write(self):
        self.accept()
        with open_work_job(self.root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            self.attach(job)
            job.record_execution_start(self.start)
        with snapshot_work_job(self.root) as job:
            status = job.load_operational_status()
            timing = status["execution_timings"][0]
            self.assertGreater(timing["elapsed_seconds"], 0.0)
            self.assertEqual(
                job.load_execution_checkpoint(self.work.identity).elapsed_seconds,
                0.0,
            )

    def test_unrelated_log_ignores_corrupt_deeper_run_state(self):
        self.accept()
        other_summary = self.project / "docs" / "other.md"
        other_root = other_summary.with_suffix("")
        other_root.mkdir()
        other_log = LogContext(other_summary, other_root)
        with sqlite3.connect(self.state) as db:
            db.execute("DELETE FROM run_state")
        with mock.patch.object(
            jobs, "_pid_alive", side_effect=AssertionError("process inspection")
        ):
            jobs._require_no_recovery_exclusion(
                other_log, None, ignore_recovery_run_id=None
            )

    def test_sustained_sqlite_writer_reports_one_bounded_control_failure(self):
        self.accept()
        holder = sqlite3.connect(self.state)
        try:
            holder.execute("BEGIN IMMEDIATE")
            with mock.patch(
                "log_commands.reproduction_job_control.JOB_WRITE_TIMEOUT_SECONDS",
                0.05,
            ):
                with open_work_job(self.root) as job:
                    with self.assertRaises(JobStoreUnavailableError):
                        job.request_run_stop(RunStopRequest(WHEN))
        finally:
            holder.rollback()
            holder.close()
        with open_work_job(self.root) as job:
            job.request_run_stop(RunStopRequest(WHEN))
            self.assertEqual(job.load_run_control().stop_requested_at, WHEN)

    def test_old_delete_journal_and_lock_open_without_data_translation(self):
        self.accept()
        with sqlite3.connect(self.state) as db:
            self.assertEqual(
                db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete"
            )
        (self.root / "state.lock").touch()
        original_bytes = self.plan.serialized()
        with snapshot_work_job(self.root) as job:
            self.assertEqual(job.accepted.plan.serialized(), original_bytes)
            self.assertEqual(job.load_run_control().phase, "accepted")
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with open_work_job(self.root) as job:
            self.assertEqual(job.accepted.plan.serialized(), original_bytes)
            self.assertEqual(job.load_run_control().phase, "accepted")
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertTrue((self.root / "state.lock").exists())

    def test_wal_growth_is_rejected_before_oversized_mutation_commits(self):
        self.accept()
        with sqlite3.connect(self.state) as db:
            logical_size = (
                db.execute("PRAGMA page_count").fetchone()[0]
                * db.execute("PRAGMA page_size").fetchone()[0]
            )
        limit = logical_size * 4
        with mock.patch(
            "log_commands.reproduction_job_control.MAX_JOB_STORE_BYTES", limit
        ):
            with open_work_job(self.root) as job:
                with self.assertRaises(JobStoreInvariantError):
                    with job._transaction("oversized_wal_growth"):
                        job._db.execute(
                            "UPDATE run_state SET operational_message=?",
                            ("x" * (2 * logical_size),),
                        )
                        self.assertLessEqual(
                            job._db.execute("PRAGMA page_count").fetchone()[0]
                            * job._db.execute("PRAGMA page_size").fetchone()[0],
                            limit,
                        )
        with open_work_job(self.root) as job:
            self.assertEqual(job.load_run_control().phase, "accepted")
            self.assertIsNone(
                job._db.execute("SELECT operational_message FROM run_state").fetchone()[
                    0
                ]
            )

    def test_racing_creators_preserve_one_complete_job(self):
        context = multiprocessing.get_context("spawn")
        ready, result = context.Queue(), context.Queue()
        start = context.Event()
        workers = [
            context.Process(
                target=_create_job_process,
                args=(
                    self.root,
                    self.run_id,
                    self.run_path,
                    self.project,
                    self.plan.serialized().encode(),
                    ready,
                    start,
                    result,
                ),
            )
            for _ in range(2)
        ]
        try:
            for worker in workers:
                worker.start()
            for _ in workers:
                self.assertTrue(ready.get(timeout=15))
            start.set()
            outcomes = [result.get(timeout=15) for _ in workers]
            self.assertCountEqual(outcomes, ["created", "exists"])
            with snapshot_work_job(self.root) as job:
                self.assertEqual(job.accepted.plan.serialized(), self.plan.serialized())
            self.assertFalse((self.root / "state.lock").exists())
        finally:
            start.set()
            for worker in workers:
                worker.join(timeout=15)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)

    def test_process_loss_before_stop_commit_rolls_back(self):
        self.accept()
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=_crash_before_stop_commit, args=(self.root,))
        worker.start()
        worker.join(timeout=15)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        self.assertEqual(worker.exitcode, 17)
        with snapshot_work_job(self.root) as job:
            self.assertIsNone(job.load_run_control().stop_requested_at)
        with open_work_job(self.root) as job:
            job.request_run_stop(RunStopRequest(WHEN))
            self.assertEqual(job.load_run_control().stop_requested_at, WHEN)

    def test_four_process_writers_and_snapshot_reader_progress(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name).resolve()
        fixture, entry, _ = fanout_fixture(project, 5)
        evaluation = evaluate_mechanical(
            EvaluationRequest(fixture.summary, FullEvaluationTarget())
        )
        plan = planner.plan_reproduction_work(
            fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=entry,
            include_all=False,
        )
        run_id = "reproduce-concurrent-writers"
        run_path = canonical_run_path(
            WHEN, run_leaf(fixture.summary.stem, "e001", run_id)
        ).as_posix()
        root = project / run_path
        root.mkdir(parents=True)
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, run_path, project))
        identities = [
            ExecutionRef.from_dict(row["identity"]) for row in plan.scheduling[:4]
        ]
        self.assertEqual(len(identities), 4)
        with open_work_job(root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            for index, identity in enumerate(identities):
                job.attach_execution_permit(
                    ExecutionPermitAttachment(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        f"grant-{index}",
                        WHEN,
                    )
                )
        context = multiprocessing.get_context("spawn")
        ready, result = context.Queue(), context.Queue()
        start = context.Event()
        workers = [
            context.Process(
                target=_write_workers_process,
                args=(
                    root,
                    identity,
                    f"grant-{index}",
                    f"worker-{index}",
                    ready,
                    start,
                    result,
                ),
            )
            for index, identity in enumerate(identities)
        ]
        try:
            for worker in workers:
                worker.start()
            for _ in workers:
                self.assertTrue(ready.get(timeout=15))
            start.set()
            for _ in range(12):
                with snapshot_work_job(root) as job:
                    status = job.load_operational_status()
                self.assertEqual(status["total_executions"], len(plan.scheduling))
                self.assertLessEqual(len(status["active_workers"]), 4)
            self.assertEqual([result.get(timeout=15) for _ in workers], ["written"] * 4)
            with snapshot_work_job(root) as job:
                self.assertEqual(
                    len(job.load_operational_status()["active_workers"]), 4
                )
        finally:
            start.set()
            for worker in workers:
                worker.join(timeout=15)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)

    def test_large_live_control_paths_load_complete_plan_once(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name).resolve()
        fixture, entry, _ = fanout_fixture(project, 50)
        evaluation = evaluate_mechanical(
            EvaluationRequest(fixture.summary, FullEvaluationTarget())
        )
        plan = planner.plan_reproduction_work(
            fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=entry,
            include_all=False,
        )
        run_id = "reproduce-large-control"
        run_path = canonical_run_path(
            WHEN, run_leaf(fixture.summary.stem, "e001", run_id)
        ).as_posix()
        root = project / run_path
        root.mkdir(parents=True)
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, run_path, project))
        identity = ExecutionRef.from_dict(plan.scheduling[0]["identity"])
        worker = WorkerRecord("worker-large", None, 12345, "running", WHEN, WHEN)

        with mock.patch.object(
            storage, "_load_accepted_work", wraps=storage._load_accepted_work
        ) as load_plan:
            with open_work_job(root) as job:
                accepted_plan = job.accepted.plan
                accepted = accepted_scheduling_projection(
                    accepted_plan,
                    run_id,
                    ExecutionIdentity(
                        identity.entry, identity.cid, identity.execution_id
                    ),
                )
                request = scheduler.SchedulerPermitRequest(
                    scheduler.SchedulerIdentity(
                        project,
                        run_id,
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        accepted.plan_order,
                    ),
                    accepted.kind,
                    os.getpid(),
                    *scheduler._accepted_scheduler_claims(accepted, root, project),
                    WHEN,
                )
                job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
                job.attach_execution_permit(
                    ExecutionPermitAttachment(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        "grant-large",
                        WHEN,
                    )
                )
                job.record_execution_start(
                    ExecutionStart(
                        identity.entry,
                        identity.cid,
                        identity.execution_id,
                        "grant-large",
                        WHEN,
                        WHEN,
                        "/private/tmp/large-control-attempt",
                    )
                )
            for _ in range(20):
                with open_work_job(root) as job:
                    job.replace_execution_workers(identity, "grant-large", (worker,))
                    scheduler._validate_accepted_request(
                        job, root, request, accepted=accepted
                    )
                    job.load_scheduler_owner()
            with open_work_job(root) as job:
                self.assertEqual(
                    job.load_execution_readiness(
                        identity, plan=accepted_plan
                    ).disposition,
                    "ready",
                )
                status = job.load_operational_status()
            self.assertEqual(status["total_executions"], len(plan.scheduling))
            self.assertEqual(len(status["active_workers"]), 1)
            self.assertEqual(load_plan.call_count, 1)

    def test_changed_schedule_claims_fail_immutable_plan_authentication(self):
        self.accept()
        with sqlite3.connect(self.state) as db:
            db.execute(
                "UPDATE accepted_work_scheduling SET claims_json="
                '\'{"read_paths":[],"write_paths":[],"writable_paths":[]}\' '
                "WHERE command_pk=1"
            )
        with self.assertRaisesRegex(ReproductionDomainError, "digest"):
            with open_work_job(self.root) as job:
                job.load_accepted_scheduling(
                    ExecutionIdentity(
                        self.work.identity.entry,
                        self.work.identity.cid,
                        self.work.identity.execution_id,
                    )
                )

    def test_changed_exclusive_policy_fails_immutable_plan_authentication(self):
        self.accept()
        with sqlite3.connect(self.state) as db:
            db.execute(
                "UPDATE accepted_work_commands SET work_json="
                "replace(work_json,'\"exclusive\":false','\"exclusive\":true') "
                "WHERE command_pk=?",
                (1,),
            )
        with self.assertRaisesRegex(ReproductionDomainError, "digest"):
            with open_work_job(self.root) as job:
                job.load_accepted_scheduling(
                    ExecutionIdentity(
                        self.work.identity.entry,
                        self.work.identity.cid,
                        self.work.identity.execution_id,
                    )
                )

    def test_status_totals_follow_acceptance_not_observed_terminal_rows(self):
        self.accept()
        self.assertFalse((self.root / "state.lock").exists())
        with open_work_job(self.root) as job:
            total = len(self.plan.commands)
            self.assertGreater(total, 1)
            self.assertEqual(job.load_operational_status()["total_executions"], total)
            self.assertEqual(job.load_operational_status()["completed_executions"], 0)
            self.attach(job)
            job.record_execution_start(self.start)
            job.record_attempt_completion(self.result())
            self.assertEqual(job.load_operational_status()["total_executions"], total)
            self.assertEqual(job.load_operational_status()["completed_executions"], 1)
            # Simulate a missing observation, without modifying immutable acceptance.
            key = job._command_pk(self.work.identity)
            job._db.execute(
                "DELETE FROM run_command_results WHERE command_pk=?", (key,)
            )
            job._db.execute(
                "UPDATE execution_checkpoints SET state='active' WHERE command_pk=?",
                (key,),
            )
            job._db.commit()
            self.assertEqual(job.load_operational_status()["total_executions"], total)
            self.assertEqual(job.load_operational_status()["completed_executions"], 0)
            self.assertEqual(job.accepted.plan, self.plan)

    def test_terminal_and_comparison_commits_preserve_sibling_and_accepted_rows(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            target = job._command_pk(self.work.identity)
            accepted_tables = [
                row[0]
                for row in job._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'accepted_%' ORDER BY name"
                )
            ]
            accepted_before = {
                name: tuple(
                    tuple(row) for row in job._db.execute(f"SELECT * FROM {name}")
                )
                for name in accepted_tables
            }
            sibling_before = tuple(
                tuple(row)
                for row in job._db.execute(
                    "SELECT * FROM execution_checkpoints WHERE command_pk!=?", (target,)
                )
            )
            job.record_attempt_completion(self.result())
            artifact = next(
                work
                for work in self.plan.artifacts
                if work.producer == self.work.identity
            )
            artifact_key = job._db.execute(
                "SELECT artifact_pk FROM accepted_work_artifacts "
                "WHERE entry=? AND artifact=?",
                (artifact.identity.entry, artifact.identity.artifact),
            ).fetchone()[0]
            comparison, problems = compare_accepted_artifact(
                artifact,
                self.work,
                Path(artifact.retained_path),
                context=ArtifactObservationContext(
                    self.run_id, WHEN, self.plan.evidence_only
                ),
            )
            siblings = tuple(
                tuple(row)
                for row in job._db.execute(
                    "SELECT * FROM run_artifact_results WHERE artifact_pk!=?",
                    (artifact_key,),
                )
            )
            job._record_artifact_comparison(comparison, problems)
            self.assertEqual(
                tuple(
                    tuple(row)
                    for row in job._db.execute(
                        "SELECT * FROM execution_checkpoints WHERE command_pk!=?",
                        (target,),
                    )
                ),
                sibling_before,
            )
            self.assertEqual(
                tuple(
                    tuple(row)
                    for row in job._db.execute(
                        "SELECT * FROM run_artifact_results WHERE artifact_pk!=?",
                        (artifact_key,),
                    )
                ),
                siblings,
            )
            self.assertEqual(
                {
                    name: tuple(
                        tuple(row) for row in job._db.execute(f"SELECT * FROM {name}")
                    )
                    for name in accepted_tables
                },
                accepted_before,
            )

    def test_named_storage_errors_remain_typed_and_nonmutating(self):
        for kind, content, expected in (
            ("missing", None, JobStoreMissingError),
            ("unsupported", b"", JobStoreUnsupportedError),
            ("malformed", b"not sqlite", JobStoreMalformedError),
            ("symlink", None, JobStoreSymlinkError),
        ):
            with self.subTest(kind=kind):
                root = self.project / kind
                root.mkdir()
                database = root / "state.sqlite"
                if kind == "unsupported":
                    with sqlite3.connect(database) as db:
                        db.execute("PRAGMA user_version=999")
                elif kind == "malformed":
                    database.write_bytes(content)
                elif kind == "symlink":
                    target = self.project / "symlink-target"
                    target.write_bytes(b"retained target")
                    database.symlink_to(target)
                with self.assertRaises(expected):
                    with open_work_job(root):
                        pass
                if kind == "symlink":
                    self.assertEqual(target.read_bytes(), b"retained target")
        self.accept()
        with open_work_job(self.root) as job:
            with self.assertRaises(JobStoreTransitionError):
                job.record_attempt_completion(self.result())
            self.attach(job)
            job.record_execution_start(self.start)
            with self.assertRaises(JobStoreInvariantError):
                job.record_attempt_completion(self.result(outputs={}))
        with (self.root / "state.lock").open("w+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with snapshot_work_job(self.root) as job:
                    self.assertEqual(job.load_run_control().phase, "executing")
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name).resolve()
        self.fixture, self.entry, _ = fanout_fixture(self.project, 2)
        evaluation = evaluate_mechanical(
            EvaluationRequest(self.fixture.summary, FullEvaluationTarget())
        )
        self.plan = planner.plan_reproduction_work(
            self.fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=self.entry,
            include_all=False,
        )
        self.run_id = "reproduce-native-job"
        self.run_path = canonical_run_path(
            WHEN, run_leaf(self.fixture.summary.stem, "e001", self.run_id)
        ).as_posix()
        self.root = self.project / self.run_path
        self.root.mkdir(parents=True)
        self.accepted = WorkJobAcceptance(
            self.run_id, self.plan, WHEN, self.run_path, self.project
        )
        # Every recipe is selected in this authored fresh-state fixture.
        self.work = min(
            self.plan.commands,
            key=lambda work: self.plan.schedule(work.identity)["order"],
        )
        self.state = self.root / "state.sqlite"
        self.start = ExecutionStart(
            self.work.identity.entry,
            self.work.identity.cid,
            self.work.identity.execution_id,
            "grant",
            WHEN,
            WHEN,
            "/private/tmp/native-job-attempt",
            stdout_path="diagnostics/stdout.log",
            stderr_path="diagnostics/stderr.log",
        )
        self.worker = WorkerRecord("worker", None, 12345, "exited", WHEN, WHEN)

    def accept(self):
        return create_work_job(self.root, self.accepted)

    def attach(self, job, *, permit="grant", expected="absent"):
        job.attach_execution_permit(
            ExecutionPermitAttachment(
                self.work.identity.entry,
                self.work.identity.cid,
                self.work.identity.execution_id,
                permit,
                WHEN,
                expected,
            )
        )

    def result(self, *, failed=False, outputs=None):
        problem = ReproductionProblem(
            self.work.identity,
            "execution_failed",
            ProblemStage.EXECUTE,
            "Producer exited with status 2.",
            {"returncode": 2},
        )
        result = CommandResult(
            self.work.identity,
            CommandOutcome.FAILED if failed else CommandOutcome.SUCCEEDED,
            WHEN,
            WHEN,
            ("python", self.work.execution.recipe.script),
            "workspace/entry",
            self.start.stdout_path,
            self.start.stderr_path,
            outputs
            if outputs is not None
            else {
                name: FINGERPRINT.as_dict()
                for name, _ in self.work.execution.recipe.outputs
            },
            (problem.problem_id,) if failed else (),
        )
        return AttemptCompletion(
            result,
            (problem,) if failed else (),
            "grant",
            WHEN,
            1.25,
            (self.worker,),
        )

    def checkpoint(self):
        with sqlite3.connect(self.state) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM execution_checkpoints").fetchone())

    def test_exhaustive_recovery_scan_retains_unattributed_survivors(self):
        self.accept()
        survivor = replace(self.worker, state="running")
        with open_work_job(self.root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            job.replace_recovery_workers(
                (RecoveryWorkerObservation(None, survivor),), observed_at=WHEN
            )
            self.assertEqual(
                job.load_scheduler_owner().running_workers, ((None, survivor),)
            )
            status = job.load_operational_status()
            self.assertEqual(status["active_workers"], [])
            self.assertEqual(status["active_executions"], [])
            self.assertEqual(len(status["surviving_workers"]), 1)
            job.request_run_stop(RunStopRequest(WHEN))
            job.acknowledge_run_stop()
            with self.assertRaises(JobStoreTransitionError):
                job.finish_run_stop(RunStopCompletion(WHEN))
            job.replace_recovery_workers((), observed_at=WHEN)
            job.finish_run_stop(RunStopCompletion(WHEN))
            self.assertEqual(job.load_run_control().status, "stopped")
            row = job._db.execute(
                "SELECT state FROM workers WHERE worker_id=?", (survivor.worker_id,)
            ).fetchone()
            self.assertEqual(row[0], "exited")

    def test_recovery_scan_preserves_active_execution_binding_and_worker_identity(self):
        self.accept()
        survivor = replace(self.worker, state="running")
        identity = ExecutionIdentity(
            self.work.identity.entry,
            self.work.identity.cid,
            self.work.identity.execution_id,
        )
        with open_work_job(self.root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            self.attach(job)
            job.replace_recovery_workers(
                (RecoveryWorkerObservation(identity, survivor),), observed_at=WHEN
            )
            self.assertEqual(
                job.load_scheduler_owner().running_workers, ((identity, survivor),)
            )
            with self.assertRaises(JobStoreTransitionError):
                job.replace_recovery_workers(
                    (RecoveryWorkerObservation(identity, replace(survivor, pid=999)),),
                    observed_at=WHEN,
                )
            self.assertEqual(
                job.load_execution_workers(self.work.identity), (survivor,)
            )

    def test_recovery_worker_for_unstarted_command_is_unattributed(self):
        self.accept()
        survivor = replace(self.worker, state="running")
        identity = ExecutionIdentity(
            self.work.identity.entry,
            self.work.identity.cid,
            self.work.identity.execution_id,
        )
        with open_work_job(self.root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            job.replace_recovery_workers(
                (RecoveryWorkerObservation(identity, survivor),), observed_at=WHEN
            )
            self.assertEqual(
                job.load_scheduler_owner().running_workers, ((None, survivor),)
            )

    def test_actual_prepared_plan_accepts_exactly_without_source_mutation(self):
        before = research_snapshot(self.fixture.summary)
        identity = self.accept()
        self.assertEqual(identity.run_id, self.run_id)
        self.assertEqual(identity.run_path, self.run_path)
        with (
            mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("source")),
            mock.patch("pathlib.Path.read_text", side_effect=AssertionError("source")),
            mock.patch.object(
                planner, "plan_reproduction_work", side_effect=AssertionError("replan")
            ),
            open_work_job(self.root) as job,
        ):
            self.assertEqual(job.accepted.plan.serialized(), self.plan.serialized())
        self.assertEqual(research_snapshot(self.fixture.summary), before)
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertFalse(
                {
                    "accepted_cases",
                    "accepted_failures",
                    "checkpoint_outputs",
                    "accepted_commands",
                }
                & tables
            )

    def test_acceptance_failure_rolls_back_only_its_new_store(self):
        sentinel = self.root / "diagnostics.txt"
        sentinel.write_text("retained", encoding="utf-8")
        with mock.patch.object(
            storage, "_before_commit", side_effect=RuntimeError("cut")
        ):
            with self.assertRaisesRegex(Exception, "cut"):
                self.accept()
        self.assertFalse(self.state.exists())
        self.assertEqual(sentinel.read_text(), "retained")

    def test_existing_or_old_job_is_not_replaced_or_translated(self):
        self.accept()
        with self.assertRaises(JobStoreExistsError):
            self.accept()
        with open_work_job(self.root) as job:
            self.assertEqual(job.accepted, self.accepted)
        with sqlite3.connect(self.state) as db:
            db.execute("PRAGMA user_version=3")
        old = self.state.read_bytes()
        with self.assertRaises(JobStoreUnsupportedError):
            with open_work_job(self.root):
                self.fail("native reader opened old job")
        self.assertEqual(self.state.read_bytes(), old)
        with sqlite3.connect(self.state) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_old_plan_and_wrong_run_location_fail_before_store_creation(self):
        for accepted in (
            replace(
                self.accepted, plan={"schema": "research-log-reproduction-plan/11"}
            ),
            replace(self.accepted, run_id="reproduce-another"),
            replace(self.accepted, project_root=self.project / "another"),
        ):
            with self.subTest(accepted=accepted.run_id):
                with self.assertRaises(JobStoreInvariantError):
                    create_work_job(self.root, accepted)
                self.assertFalse(self.state.exists())

    def test_changed_valid_work_is_not_reaccepted_by_its_original_digest(self):
        self.accept()
        with sqlite3.connect(self.state) as db:
            db.execute(
                "UPDATE runs SET plan_header=replace(plan_header,"
                "'\"include_all\":false','\"include_all\":true')"
            )
        with self.assertRaisesRegex(ReproductionDomainError, "digest"):
            with open_work_job(self.root) as job:
                _ = job.accepted.plan

    def test_terminal_result_and_problem_are_atomic_before_grant_release(self):
        self.accept()
        completion = self.result(failed=True)
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            job.record_attempt_completion(completion)
            self.assertEqual(
                job.load_command_result(self.work.identity), completion.result
            )
        self.assertEqual(self.checkpoint()["permit_id"], "grant")
        self.assertEqual(self.checkpoint()["state"], "completed")
        with sqlite3.connect(self.state) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reproduction_problems").fetchone()[0],
                len(self.plan.problems) + 1,
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with open_work_job(self.root) as job:
            job.clear_execution_permit(
                self.work.identity, "grant", checkpointed_at=WHEN
            )
            job.clear_execution_permit(
                self.work.identity, "grant", checkpointed_at=WHEN
            )
        self.assertIsNone(self.checkpoint()["permit_id"])
        self.assertEqual(self.checkpoint()["released_permit_id"], "grant")

    def test_failure_at_completion_commit_keeps_grant_active_and_no_result(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            with mock.patch.object(
                storage, "_before_commit", side_effect=RuntimeError("cut")
            ):
                with self.assertRaisesRegex(RuntimeError, "cut"):
                    job.record_attempt_completion(self.result(failed=True))
            self.assertIsNone(job.load_command_result(self.work.identity))
            with self.assertRaises(JobStoreTransitionError):
                job.clear_execution_permit(
                    self.work.identity, "grant", checkpointed_at=WHEN
                )
        self.assertEqual(self.checkpoint()["state"], "active")
        with sqlite3.connect(self.state) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM workers").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reproduction_problems").fetchone()[0],
                len(self.plan.problems),
            )

    def test_wrong_permit_and_unlaunched_attempt_cannot_complete(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            with self.assertRaises(JobStoreTransitionError):
                job.record_attempt_completion(self.result())
            job.record_execution_start(self.start)
            with self.assertRaises(JobStoreTransitionError):
                job.record_attempt_completion(replace(self.result(), permit_id="wrong"))
            self.assertIsNone(job.load_command_result(self.work.identity))

    def test_stream_paths_cannot_point_outside_accepted_diagnostics(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            for path in ("state.sqlite", "workspace/private-input.txt", "diagnostics"):
                for stream in ("stdout_path", "stderr_path"):
                    with self.subTest(path=path, stream=stream):
                        with self.assertRaises(JobStoreInvariantError):
                            job.record_execution_start(
                                replace(self.start, **{stream: path})
                            )

    def test_corrupted_checkpoint_state_fails_before_recovery(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
        with sqlite3.connect(self.state) as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("UPDATE execution_checkpoints SET state='unknown'")
        with open_work_job(self.root) as job:
            with self.assertRaises(JobStoreInvariantError):
                job.load_execution_checkpoint(self.work.identity)

    def test_corrupted_recovery_scalars_are_read_only_errors(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
        original = self.checkpoint()
        for column, value in (
            ("checkpointed_at", "yesterday"),
            ("started_at", "2026-99-99T12:00:00Z"),
            ("permit_id", ""),
            ("permit_id", None),
            ("scratch_path", "/project/scratch"),
            ("stdout_path", "workspace/private.txt"),
            ("stderr_path", "diagnostics/../state.sqlite"),
            ("stop_reason", "unexpected interrupted facts"),
        ):
            with self.subTest(column=column, value=value):
                with sqlite3.connect(self.state) as db:
                    db.execute(
                        "UPDATE execution_checkpoints SET "
                        + ",".join(f"{key}=?" for key in original),
                        tuple(original.values()),
                    )
                    db.execute(f"UPDATE execution_checkpoints SET {column}=?", (value,))
                before = self.state.read_bytes()
                with open_work_job(self.root) as job:
                    with self.assertRaises(JobStoreInvariantError):
                        job.load_execution_checkpoint(self.work.identity)
                self.assertEqual(self.state.read_bytes(), before)

    def test_success_requires_all_recipe_outputs_not_just_counted_artifacts(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            with self.assertRaises(JobStoreInvariantError):
                job.record_attempt_completion(self.result(outputs={}))
            job.record_attempt_completion(self.result())
            self.assertEqual(
                set(job.load_command_result(self.work.identity).outputs),
                {name for name, _ in self.work.execution.recipe.outputs},
            )

    def test_failed_attempt_retains_valid_partial_outputs_and_exact_owner_cause(self):
        self.accept()
        name = self.work.execution.recipe.outputs[0][0]
        completion = self.result(failed=True, outputs={name: FINGERPRINT.as_dict()})
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            job.record_attempt_completion(completion)
        with open_work_job(self.root) as job:
            self.assertEqual(
                job.load_command_result(self.work.identity), completion.result
            )

    def test_live_or_lost_worker_cannot_be_released_by_terminal_commit(self):
        self.accept()
        live = replace(self.worker, state="running")
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            job.replace_execution_workers(self.work.identity, "grant", (live,))
            with self.assertRaises(JobStoreInvariantError):
                job.record_attempt_completion(replace(self.result(), workers=(live,)))
            with self.assertRaises(JobStoreTransitionError):
                job.record_attempt_completion(replace(self.result(), workers=()))
            self.assertIsNone(job.load_command_result(self.work.identity))
            job.record_attempt_completion(self.result())

    def test_stopped_attempt_is_not_result_and_requires_cleanup_before_resume(self):
        self.accept()
        with open_work_job(self.root) as job:
            self.attach(job)
            job.record_execution_start(self.start)
            job.record_execution_stop(
                AttemptInterruption(
                    self.work.identity,
                    "grant",
                    WHEN,
                    0.5,
                    "Interrupted by stop request.",
                    {},
                    (self.worker,),
                )
            )
            self.assertIsNone(job.load_command_result(self.work.identity))
            with self.assertRaises(JobStoreTransitionError):
                self.attach(job, permit="resumed", expected="stopped")
            job.clear_execution_permit(
                self.work.identity, "grant", checkpointed_at=WHEN
            )
            with self.assertRaises(JobStoreTransitionError):
                self.attach(job, permit="resumed", expected="stopped")
            job.clear_execution_scratch(
                self.work.identity, self.start.scratch_path, checkpointed_at=WHEN
            )
            self.attach(job, permit="resumed", expected="stopped")
            job.record_execution_start(
                replace(self.start, permit_id="resumed", elapsed_seconds=0.5)
            )
            with self.assertRaises(JobStoreTransitionError):
                job.record_attempt_completion(
                    replace(self.result(), permit_id="resumed", elapsed_seconds=0.25)
                )
            job.record_attempt_completion(replace(self.result(), permit_id="resumed"))
        self.assertEqual(self.checkpoint()["elapsed_seconds"], 1.25)

    def test_run_stop_requires_released_attempt_and_resumes_same_accepted_plan(self):
        self.accept()
        with open_work_job(self.root) as job:
            owner = RunOwner(12345, "running", WHEN, WHEN)
            job.replace_run_owner(owner)
            self.assertEqual(job.load_run_owner(), owner)
            self.attach(job)
            job.request_run_stop(RunStopRequest(WHEN))
            job.request_run_stop(RunStopRequest("2030-01-01T00:00:00Z"))
            self.assertEqual(job.load_run_control().stop_requested_at, WHEN)
            job.acknowledge_run_stop()
            with self.assertRaises(JobStoreTransitionError):
                job.finish_run_stop(RunStopCompletion(WHEN))
            with self.assertRaises(JobStoreTransitionError):
                job.record_execution_start(self.start)
            job.record_execution_stop(
                AttemptInterruption(
                    self.work.identity,
                    "grant",
                    WHEN,
                    0.0,
                    "Stopped before launch.",
                    {},
                    (self.worker,),
                )
            )
            job.clear_execution_permit(
                self.work.identity, "grant", checkpointed_at=WHEN
            )
            job.finish_run_stop(RunStopCompletion(WHEN))
            self.assertEqual(job.load_run_control().status, "stopped")
            job.begin_run_resume(RunResumeRequest("2030-01-01T00:00:00Z"))
            self.assertIsNone(job.load_run_control().status)
            self.assertIsNone(job.load_run_control().stop_requested_at)
            self.assertEqual(job.accepted.plan, self.plan)
            self.attach(job, permit="resumed", expected="stopped")
            job.replace_execution_workers(self.work.identity, "resumed", ())
            self.assertEqual(job.load_execution_workers(self.work.identity), ())
            self.assertIsNone(job.load_command_result(self.work.identity))

    def test_operational_failure_retains_one_exact_intent_and_cannot_resume(self):
        self.accept()
        failure = RunFailure("supervisor_failed", "Captured operational failure.", WHEN)
        with open_work_job(self.root) as job:
            job.request_run_stop(RunStopRequest(WHEN))
            job.request_run_failure(failure)
            job.request_run_failure(failure)
            with self.assertRaises(JobStoreTransitionError):
                job.request_run_failure(replace(failure, message="Different failure."))
            job.finish_run_stop(RunStopCompletion(WHEN))
            control = job.load_run_control()
            self.assertEqual(control.status, "failed")
            self.assertEqual(control.operational_code, failure.code)
            self.assertEqual(control.stop_requested_at, WHEN)
            with self.assertRaises(JobStoreTransitionError):
                job.begin_run_resume(RunResumeRequest(WHEN))
            self.assertIsNone(job.load_command_result(self.work.identity))

    def test_owner_observation_cannot_move_backwards(self):
        self.accept()
        owner = RunOwner(12345, "running", WHEN, "2030-01-01T00:00:00Z")
        with open_work_job(self.root) as job:
            job.replace_run_owner(owner)
            with self.assertRaises(JobStoreTransitionError):
                job.replace_run_owner(replace(owner, last_observed_at=WHEN))
            self.assertEqual(job.load_run_owner(), owner)

    def scheduler_request(self, root=None, *, pid=None):
        root = root or self.root
        pid = pid or os.getpid()
        with open_work_job(root) as job:
            job.replace_run_owner(RunOwner(pid, "running", WHEN, WHEN))
            key = self.work.identity
            accepted = job.load_accepted_scheduling(
                ExecutionIdentity(key.entry, key.cid, key.execution_id)
            )
            claims = scheduler._accepted_scheduler_claims(accepted, root, self.project)
        return scheduler.SchedulerPermitRequest(
            scheduler.SchedulerIdentity(
                self.project,
                accepted.run_id,
                key.entry,
                key.cid,
                key.execution_id,
                accepted.plan_order,
            ),
            accepted.kind,
            pid,
            *claims,
            WHEN,
        )

    def poll(self, request, root=None):
        return scheduler.poll_work_permit(
            root or self.root, request, checkpointed_at=WHEN, expected_state="absent"
        )

    def test_native_coordinator_authenticates_claims_and_releases_completed_grant(self):
        self.accept()
        request = self.scheduler_request()
        with self.assertRaises(ActionError):
            self.poll(replace(request, write_paths=("/invalid/output",)))
        decision = self.poll(request)
        self.assertEqual(decision.disposition, "granted")
        grant = decision.permit.permit_id
        self.assertEqual(self.poll(request).permit.permit_id, grant)
        with open_work_job(self.root) as job:
            proof = job.load_scheduler_owner()
            self.assertEqual(proof.checkpoints[0].state, "active")
        self.assertIsNone(scheduler.reconcile_permit(request.identity, proof).delta)
        with open_work_job(self.root) as job:
            job.record_execution_start(replace(self.start, permit_id=grant))
            job.record_attempt_completion(replace(self.result(), permit_id=grant))
            proof = job.load_scheduler_owner()
            self.assertEqual(proof.checkpoints[0].state, "completed")
            self.assertFalse(hasattr(proof.checkpoints[0], "failure_message"))
        reconciled = scheduler.reconcile_permit(request.identity, proof)
        self.assertEqual(reconciled.clear_run_permit_id, grant)
        self.assertEqual(reconciled.delta.removed_permit_ids, (grant,))
        with open_work_job(self.root) as job:
            job.clear_execution_permit(self.work.identity, grant, checkpointed_at=WHEN)
            self.assertEqual(job.load_scheduler_owner().checkpoints, ())

    def test_native_grant_before_attachment_cut_recovers_exact_grant(self):
        self.accept()
        request = self.scheduler_request()
        with mock.patch.object(
            scheduler,
            "_after_scheduler_grant_before_attach",
            side_effect=OSError("cut"),
        ):
            with self.assertRaisesRegex(OSError, "cut"):
                self.poll(request)
        with open_work_job(self.root) as job:
            self.assertIsNone(job.load_execution_checkpoint(self.work.identity))
        decision = self.poll(request)
        self.assertEqual(decision.disposition, "granted")
        with open_work_job(self.root) as job:
            self.assertEqual(
                job.load_execution_checkpoint(self.work.identity).permit_id,
                decision.permit.permit_id,
            )

    def assert_preexecution_phase_admission(self, phase):
        self.accept()
        request = self.scheduler_request()
        with sqlite3.connect(self.state) as db:
            db.execute("UPDATE run_state SET phase=?", (phase,))
        decision = self.poll(request)
        self.assertEqual(decision.disposition, "granted")
        with open_work_job(self.root) as job:
            self.assertEqual(
                job.load_execution_checkpoint(self.work.identity).permit_id,
                decision.permit.permit_id,
            )

    def test_native_planning_phase_admission_matches_existing_attachment_contract(self):
        self.assert_preexecution_phase_admission("planning")

    def test_native_preflight_phase_admission_matches_existing_attachment_contract(
        self,
    ):
        self.assert_preexecution_phase_admission("preflight")

    def test_native_exclusive_waiter_blocks_ordinary_until_exact_release(self):
        self.accept()
        ordinary = self.scheduler_request()
        first = self.poll(ordinary)
        other_id = "reproduce-native-exclusive"
        path = canonical_run_path(
            WHEN, run_leaf(self.fixture.summary.stem, "e001", other_id)
        ).as_posix()
        other_root = self.project / path
        other_root.mkdir(parents=True)
        exclusive_plan = replace(
            self.plan,
            commands=tuple(
                replace(work, execution=replace(work.execution, exclusive=True))
                for work in self.plan.commands
            ),
        )
        create_work_job(
            other_root,
            WorkJobAcceptance(other_id, exclusive_plan, WHEN, path, self.project),
        )
        exclusive = self.scheduler_request(other_root)
        waiting = self.poll(exclusive, other_root)
        self.assertEqual(waiting.disposition, "waiting")
        scheduler.release_permit(ordinary.identity, first.permit.permit_id)
        admitted = self.poll(exclusive, other_root)
        self.assertEqual(admitted.disposition, "granted")
        self.assertEqual(admitted.permit.kind, "exclusive")
        # A queued/granted exclusive retains priority over later ordinary work.
        self.assertEqual(self.poll(ordinary).disposition, "blocked")

    def another_scheduler_job(self, suffix, *, exclusive=False, pid=None):
        run_id = f"reproduce-native-{suffix}"
        path = canonical_run_path(
            WHEN, run_leaf(self.fixture.summary.stem, "e001", run_id)
        ).as_posix()
        root = self.project / path
        root.mkdir(parents=True)
        plan = replace(
            self.plan,
            commands=tuple(
                replace(work, execution=replace(work.execution, exclusive=exclusive))
                for work in self.plan.commands
            ),
        )
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, path, self.project))
        return root, self.scheduler_request(root, pid=pid)

    def test_native_scheduler_schema_is_exact_and_persists_when_empty(self):
        from validation.operation_state import operation_directory

        self.accept()
        request = self.scheduler_request()
        grant = self.poll(request)
        self.assertIsNotNone(grant.permit)
        scheduler.release_permit(request.identity, grant.permit.permit_id)
        database = operation_directory(self.project) / scheduler.SCHEDULER_DATABASE_NAME
        self.assertTrue(database.is_file())
        self.assertFalse(
            (operation_directory(self.project) / "reproduction-scheduler.json").exists()
        )
        expected_columns = {
            "scheduler_state": ("singleton", "next_ticket"),
            "scheduler_waiters": (
                "ticket",
                "run_id",
                "entry",
                "cid",
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
                "cid",
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
                        row[2] for row in db.execute(f"PRAGMA index_info({index[1]})")
                    )
                    for index in indexes
                    if index[2]
                }
            self.assertEqual(
                unique_columns,
                {
                    "scheduler_state": set(),
                    "scheduler_waiters": {
                        ("run_id", "entry", "cid", "execution_id"),
                    },
                    "scheduler_permits": {
                        ("permit_id",),
                        ("run_id", "entry", "cid", "execution_id"),
                    },
                    "scheduler_claims": {
                        ("permit_id", "claim_kind", "position"),
                        ("permit_id", "claim_kind", "path"),
                    },
                },
            )
            explicit_indexes = {
                row[0]: tuple(
                    item[2] for item in db.execute(f"PRAGMA index_info({row[0]})")
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
                    for row in db.execute("PRAGMA foreign_key_list(scheduler_claims)")
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
                    "SELECT sql FROM sqlite_schema WHERE type='table' ORDER BY name"
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
        with scheduler._open_scheduler_database(self.project) as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_native_scheduler_exact_repoll_is_byte_mtime_and_dml_free(self):
        self.accept()
        request = self.scheduler_request()
        first = self.poll(request)
        database = (
            scheduler.operation_directory(self.project)
            / scheduler.SCHEDULER_DATABASE_NAME
        )
        paths = (self.state, database)
        before = tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths)
        statements = []
        original = scheduler._open_scheduler_database
        from contextlib import contextmanager

        @contextmanager
        def traced(project):
            with original(project) as db:
                db.set_trace_callback(statements.append)
                yield db

        with mock.patch.object(scheduler, "_open_scheduler_database", traced):
            repeated = self.poll(request)
        self.assertEqual(repeated.permit, first.permit)
        self.assertIsNone(repeated.delta)
        self.assertEqual(
            tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in paths),
            before,
        )
        self.assertFalse(
            any(
                statement.lstrip()
                .upper()
                .startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
                for statement in statements
            )
        )

    def test_native_scheduler_release_binds_identity_and_exact_absence_is_idempotent(
        self,
    ):
        self.accept()
        request = self.scheduler_request()
        grant = self.poll(request).permit
        with self.assertRaises(ActionError):
            scheduler.release_permit(
                replace(request.identity, run_id="reproduce-wrong"), grant.permit_id
            )
        self.assertEqual(self.poll(request).permit, grant)
        released = scheduler.release_permit(request.identity, grant.permit_id)
        self.assertEqual(released.removed_permit_ids, (grant.permit_id,))
        self.assertIsNone(scheduler.release_permit(request.identity, grant.permit_id))

    def test_native_scheduler_exclusive_tickets_are_monotonic_and_fair(self):
        self.accept()
        ordinary = self.scheduler_request()
        first = self.poll(ordinary).permit
        root_a, request_a = self.another_scheduler_job("exclusive-a", exclusive=True)
        root_b, request_b = self.another_scheduler_job("exclusive-b", exclusive=True)
        root_c, request_c = self.another_scheduler_job("ordinary-c")
        waiter_a = self.poll(request_a, root_a)
        waiter_b = self.poll(request_b, root_b)
        self.assertEqual(
            (waiter_a.disposition, waiter_b.disposition), ("waiting", "waiting")
        )
        self.assertLess(waiter_a.waiter_ticket, waiter_b.waiter_ticket)
        self.assertEqual(
            self.poll(request_a, root_a).waiter_ticket, waiter_a.waiter_ticket
        )
        self.assertEqual(self.poll(request_c, root_c).disposition, "blocked")
        scheduler.release_permit(ordinary.identity, first.permit_id)
        self.assertEqual(self.poll(request_b, root_b).disposition, "waiting")
        grant_a = self.poll(request_a, root_a).permit
        self.assertEqual(grant_a.kind, "exclusive")
        self.assertEqual(self.poll(request_c, root_c).disposition, "blocked")
        scheduler.release_permit(request_a.identity, grant_a.permit_id)
        grant_b = self.poll(request_b, root_b).permit
        self.assertEqual(grant_b.kind, "exclusive")
        scheduler.release_permit(request_b.identity, grant_b.permit_id)
        self.assertEqual(self.poll(request_c, root_c).disposition, "granted")

    def test_native_scheduler_dead_unattached_waiter_is_removed_before_admission(self):
        self.accept()
        blocker = self.scheduler_request()
        grant = self.poll(blocker).permit
        root, waiter = self.another_scheduler_job(
            "dead-waiter", exclusive=True, pid=2**30
        )
        decision = self.poll(waiter, root)
        self.assertEqual(decision.disposition, "waiting")
        with open_work_job(root) as job:
            self.assertIsNone(job.load_execution_checkpoint(self.work.identity))
        scheduler.release_permit(blocker.identity, grant.permit_id)
        live_root, request = self.another_scheduler_job("after-waiter")
        admitted = self.poll(request, live_root)
        self.assertEqual(admitted.disposition, "granted")
        self.assertEqual(
            admitted.delta.removed_waiter_tickets, (decision.waiter_ticket,)
        )

    def test_native_open_rejects_copied_job_at_another_run_root_readonly(self):
        self.accept()
        other_id = "reproduce-native-copied"
        path = canonical_run_path(
            WHEN, run_leaf(self.fixture.summary.stem, "e001", other_id)
        ).as_posix()
        other = self.project / path
        other.mkdir(parents=True)
        copied = other / "state.sqlite"
        shutil.copyfile(self.state, copied)
        before = copied.read_bytes()
        with self.assertRaises(JobStoreInvariantError):
            with open_work_job(other):
                self.fail("copied job opened")
        self.assertEqual(copied.read_bytes(), before)
