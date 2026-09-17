"""Native dead-owner recovery preserves interrupted facts and exact grants."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_reproduction_work_execution as execution_fixture
import test_reproduction_work_publication as publication_fixture
import test_reproduction_work_supervision as graph_fixture
from log_commands import reproduction_jobs as jobs
from log_commands import reproduction_work_execution as execution
from log_commands import reproduction_work_job as storage
from log_commands import reproduction_work_publication as publication
from log_commands import reproduction_work_recovery as recovery
from log_commands import reproduction_work_supervision as supervision
from log_commands.model import ActionError
from log_commands.reproduction_domain import CommandOutcome
from log_commands.reproduction_inspection import load_inspection
from log_commands.reproduction_job_control import (
    RunOwner,
    RunResumeRequest,
)
from log_commands.reproduction_work_job import open_work_job


class NativeRecoveryTests(unittest.TestCase):
    register = graph_fixture.NativeSupervisionTests.register
    prepare_graph = graph_fixture.NativeSupervisionTests.prepare_graph
    control = graph_fixture.NativeSupervisionTests.control
    prepare = publication_fixture.NativePublicationTests.prepare

    def test_obsolete_job_format_does_not_enter_current_recovery(self):
        fixture, workspace = self.prepare_graph()
        with sqlite3.connect(workspace.run_root / "state.sqlite") as db:
            db.execute("PRAGMA user_version=4")

        jobs._require_no_recovery_exclusion(
            fixture.log,
            None,
            ignore_recovery_run_id=None,
        )

    def test_terminal_publication_retains_dead_owner_exclusion_until_survivors_exit(
        self,
    ):
        fixture, workspace = self.prepare()
        publication.publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log)
        report = (fixture.log.root / "reproduction.md").read_bytes()
        with open_work_job(workspace.run_root) as job:
            owner = job.load_run_owner()
            # Crash cut: publication committed, but the physical owner remains unclosed.
            job.replace_run_owner(
                RunOwner(98764, "running", owner.registered_at, owner.last_observed_at)
            )
        survivor = {
            "worker_id": "worker-98765",
            "parent_worker_id": None,
            "pid": 98765,
            "registered_at": owner.registered_at,
            "last_observed_at": owner.last_observed_at,
        }
        with (
            mock.patch.object(recovery, "_pid_alive", return_value=False),
            mock.patch.object(jobs, "_pid_alive", return_value=False),
            mock.patch.object(
                recovery, "_terminate_marked_workers", side_effect=[[survivor], []]
            ),
        ):
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "incomplete"
            )
            with open_work_job(workspace.run_root) as job:
                self.assertEqual(job.load_run_control().status, "complete")
                self.assertEqual(job.load_run_owner().state, "running")
            with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                jobs._require_no_recovery_exclusion(
                    fixture.log, None, ignore_recovery_run_id=None
                )
            self.assertEqual(load_inspection(fixture.log), saved)
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "recovered"
            )
            jobs._require_no_recovery_exclusion(
                fixture.log, None, ignore_recovery_run_id=None
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_owner().state, "exited")
            self.assertEqual(job.load_run_owner().registered_at, owner.registered_at)
            self.assertEqual(job.load_run_control().status, "complete")
        self.assertEqual(load_inspection(fixture.log), saved)
        self.assertEqual((fixture.log.root / "reproduction.md").read_bytes(), report)

    def test_repeated_survivor_scan_preserves_registration(self):
        fixture, workspace = self.prepare_graph()
        first = "2030-01-01T00:00:00Z"
        later = "2030-01-01T00:00:01Z"
        survivor = {
            "worker_id": "worker-98765",
            "parent_worker_id": None,
            "pid": 98765,
            "registered_at": first,
            "last_observed_at": first,
        }
        with (
            mock.patch.object(recovery, "_pid_alive", return_value=False),
            mock.patch.object(recovery, "_utc_now", return_value=later),
            mock.patch.object(
                recovery,
                "_terminate_marked_workers",
                side_effect=[
                    [survivor],
                    [{**survivor, "registered_at": later, "last_observed_at": later}],
                    [],
                ],
            ),
        ):
            for _ in range(2):
                self.assertEqual(
                    recovery.recover_work_job(fixture.log, workspace.run_root),
                    "incomplete",
                )
            with open_work_job(workspace.run_root) as job:
                worker = job.load_scheduler_owner().running_workers[0][1]
                self.assertEqual(worker.registered_at, first)
                self.assertEqual(worker.last_observed_at, later)
                self.assertEqual(job.load_run_owner().state, "running")
                self.assertIsNone(job.load_run_control().status)
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "recovered"
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "stopped")

    def test_live_owner_is_nonmutating_without_process_scan(self):
        fixture, workspace = self.prepare_graph()
        with mock.patch.object(
            recovery, "_terminate_marked_workers", side_effect=AssertionError("scan")
        ):
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "live"
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().phase, "accepted")
            self.assertEqual(job.load_run_owner().state, "running")

    def test_unlaunched_grant_becomes_stopped_without_research_failure(self):
        fixture, work, workspace, _ = execution_fixture.NativeExecutionTests.prepare(
            self, "raise SystemExit(0)\n"
        )
        self.register(workspace)
        with mock.patch.object(recovery, "_pid_alive", return_value=False):
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "recovered"
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "stopped")
            self.assertEqual(job.load_run_owner().state, "stopped")
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertEqual(checkpoint.state, "stopped")
            self.assertIsNone(checkpoint.permit_id)
            self.assertIsNone(checkpoint.started_at)
            self.assertIsNone(job.load_command_result(work.identity))

    def test_terminal_write_cut_cleans_same_interrupted_attempt_without_execution(self):
        fixture, work, workspace, _ = execution_fixture.NativeExecutionTests.prepare(
            self, "raise SystemExit(3)\n"
        )
        self.register(workspace)

        def cut(operation, _db):
            if operation == "work_attempt_completion":
                raise RuntimeError("terminal cut")

        with mock.patch.object(storage, "_before_commit", side_effect=cut):
            with self.assertRaisesRegex(RuntimeError, "terminal cut"):
                execution.execute_work_recipe(
                    fixture.log,
                    work.identity,
                    workspace,
                    execution.WorkExecutionControl(
                        "native-grant",
                        lambda: None,
                        confinement=execution_fixture.TestConfinement(),
                    ),
                )
        with open_work_job(workspace.run_root) as job:
            scratch = job.load_execution_checkpoint(work.identity).scratch_path
        with (
            mock.patch.object(recovery, "_pid_alive", return_value=False),
            mock.patch.object(
                execution, "execute_work_recipe", side_effect=AssertionError("rerun")
            ),
        ):
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "recovered"
            )
        self.assertFalse(Path(scratch).exists())
        with open_work_job(workspace.run_root) as job:
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertEqual(checkpoint.state, "stopped")
            self.assertIsNone(checkpoint.permit_id)
            self.assertIsNone(checkpoint.scratch_path)
            self.assertIsNone(job.load_command_result(work.identity))
            self.assertTrue(
                all(
                    worker.state == "exited"
                    for worker in job.load_execution_workers(work.identity)
                )
            )

    def test_publication_interruption_recovers_only_frozen_publication(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            publication,
            "materialize_saved_report_locked",
            side_effect=OSError("report cut"),
        ):
            with self.assertRaises(OSError):
                publication.publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        with mock.patch.object(recovery, "_pid_alive", return_value=False):
            self.assertEqual(
                recovery.recover_work_job(fixture.log, workspace.run_root), "recovered"
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "failed")
            self.assertEqual(
                job.load_run_control().operational_code,
                "reproduction.publication.failed",
            )
            self.assertEqual(job.load_run_owner().state, "exited")
            stamp = "2099-01-01T00:00:00Z"
            job.begin_publication_resume(RunResumeRequest(stamp))
            job.replace_run_owner(RunOwner(os.getpid(), "running", stamp, stamp))
        with mock.patch.object(
            supervision, "execute_work_plan", side_effect=AssertionError("rerun")
        ):
            publication.publish_work_job(fixture.log, workspace.run_root)
        self.assertEqual(load_inspection(fixture.log).run, saved)
        self.assertTrue(
            all(
                result.outcome is CommandOutcome.SUCCEEDED
                for result in saved.command_results
            )
        )

    def test_recovery_refuses_replaced_scratch_symlink_and_preserves_target(self):
        fixture, work, workspace, _ = execution_fixture.NativeExecutionTests.prepare(
            self, "raise SystemExit(3)\n"
        )
        self.register(workspace)

        def cut(operation, _db):
            if operation == "work_attempt_completion":
                raise RuntimeError("terminal cut")

        with mock.patch.object(storage, "_before_commit", side_effect=cut):
            with self.assertRaisesRegex(RuntimeError, "terminal cut"):
                execution.execute_work_recipe(
                    fixture.log,
                    work.identity,
                    workspace,
                    execution.WorkExecutionControl(
                        "native-grant",
                        lambda: None,
                        confinement=execution_fixture.TestConfinement(),
                    ),
                )
        with open_work_job(workspace.run_root) as job:
            scratch = Path(job.load_execution_checkpoint(work.identity).scratch_path)
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        marker = Path(external.name) / "do-not-delete"
        marker.write_text("retained")
        shutil.rmtree(scratch)
        scratch.symlink_to(Path(external.name), target_is_directory=True)
        self.addCleanup(scratch.unlink)
        with mock.patch.object(recovery, "_pid_alive", return_value=False):
            with self.assertRaisesRegex(
                ActionError, "stored scratch path is not owned"
            ):
                recovery.recover_work_job(fixture.log, workspace.run_root)
        self.assertEqual(marker.read_text(), "retained")
        self.assertTrue(scratch.is_symlink())
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_run_control().status)
            self.assertEqual(job.load_run_owner().state, "running")
            self.assertEqual(
                job.load_execution_checkpoint(work.identity).scratch_path, str(scratch)
            )

    def test_recovery_completes_reachable_cleanup_cuts_without_reexecution(self):
        for operation in ("work_permit_clear", "work_scratch_clear"):
            with self.subTest(operation=operation):
                fixture, work, workspace, _ = (
                    execution_fixture.NativeExecutionTests.prepare(
                        self, "raise SystemExit(3)\n"
                    )
                )
                self.register(workspace)

                def cut(name, _db):
                    if name == operation:
                        raise RuntimeError("cleanup cut")

                with mock.patch.object(storage, "_before_commit", side_effect=cut):
                    with self.assertRaisesRegex(RuntimeError, "cleanup cut"):
                        execution.execute_work_recipe(
                            fixture.log,
                            work.identity,
                            workspace,
                            execution.WorkExecutionControl(
                                "native-grant",
                                lambda: None,
                                confinement=execution_fixture.TestConfinement(),
                            ),
                        )
                with open_work_job(workspace.run_root) as job:
                    before = job.load_command_result(work.identity)
                with mock.patch.object(recovery, "_pid_alive", return_value=False):
                    self.assertEqual(
                        recovery.recover_work_job(fixture.log, workspace.run_root),
                        "recovered",
                    )
                with open_work_job(workspace.run_root) as job:
                    checkpoint = job.load_execution_checkpoint(work.identity)
                    self.assertIsNone(checkpoint.permit_id)
                    self.assertIsNone(checkpoint.scratch_path)
                    self.assertEqual(job.load_command_result(work.identity), before)
                    self.assertEqual(job.load_run_control().status, "stopped")
