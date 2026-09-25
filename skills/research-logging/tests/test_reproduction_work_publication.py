"""Native publication recovers actual result/report cuts without another execution."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
import test_reproduction_work_supervision as graph_fixture
from log_commands import reproduction_planner as planner
from log_commands import reproduction_saved_report as reports
from log_commands import reproduction_work_job as jobs
from log_commands import reproduction_work_publication as publication
from log_commands import reproduction_work_supervision as supervision
from log_commands.model import ActionError
from log_commands.reproduction_artifact_results import (
    ArtifactObservationContext,
    compare_accepted_artifact,
)
from log_commands.reproduction_domain import ReproductionDomainError
from log_commands.reproduction_inspection import load_inspection, render_saved_summary
from log_commands.reproduction_job_control import (
    ExecutionPermitAttachment,
    ExecutionStart,
    JobStoreTransitionError,
    RunFailure,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
)
from log_commands.reproduction_work_execution import compare_work_outputs
from log_commands.reproduction_work_job import open_work_job
from log_commands.reproduction_work_publication import publish_work_job
from test_reproduction_canonical_records import WHEN


class NativePublicationTests(unittest.TestCase):
    def test_stopping_and_publishing_reject_comparison_and_new_work(self):
        for phase in ("stopping", "publishing"):
            with self.subTest(phase=phase):
                fixture, workspace = self.prepare_graph()
                self.assertEqual(
                    supervision.execute_work_plan(
                        fixture.log, workspace, self.control()
                    ),
                    "completed",
                )
                if phase == "publishing":
                    compare_work_outputs(workspace)
                with open_work_job(workspace.run_root) as job:
                    plan = job.accepted.plan
                    artifact = next(
                        work
                        for work in plan.artifacts
                        if work.producer is not None
                        and job.load_command_result(work.producer) is not None
                    )
                    producer = plan.command(artifact.producer)
                    comparison, problems = compare_accepted_artifact(
                        artifact,
                        producer,
                        workspace.map_source(Path(artifact.retained_path)),
                        context=ArtifactObservationContext(
                            workspace.run_id,
                            plan.commands[0].execution.last_run_at or WHEN,
                            plan.evidence_only,
                        ),
                    )
                    if phase == "stopping":
                        job.request_run_stop(RunStopRequest(WHEN))
                        job.acknowledge_run_stop()
                    else:
                        job.prepare_publication(finished_at=WHEN)
                    before = (workspace.run_root / "state.sqlite").read_bytes()
                    for action in (
                        lambda: job._record_artifact_comparison(comparison, problems),
                        lambda: job.attach_execution_permit(
                            ExecutionPermitAttachment(
                                producer.identity.entry,
                                producer.identity.cid,
                                producer.identity.execution_id,
                                "forbidden",
                                WHEN,
                                "absent",
                            )
                        ),
                        lambda: job.record_execution_start(
                            ExecutionStart(
                                producer.identity.entry,
                                producer.identity.cid,
                                producer.identity.execution_id,
                                "forbidden",
                                WHEN,
                                WHEN,
                                "/private/tmp/forbidden-phase-attempt",
                            )
                        ),
                    ):
                        with self.assertRaises(JobStoreTransitionError):
                            action()
                        self.assertEqual(
                            (workspace.run_root / "state.sqlite").read_bytes(), before
                        )
                    if phase == "stopping":
                        with self.assertRaises(JobStoreTransitionError):
                            job.prepare_publication(finished_at=WHEN)
                    self.assertEqual(job.load_run_control().phase, phase)
                    self.assertEqual(
                        (workspace.run_root / "state.sqlite").read_bytes(), before
                    )

    register = graph_fixture.NativeSupervisionTests.register
    prepare_graph = graph_fixture.NativeSupervisionTests.prepare_graph
    control = graph_fixture.NativeSupervisionTests.control

    def prepare(self):
        fixture, workspace = self.prepare_graph()
        self.assertEqual(
            supervision.execute_work_plan(fixture.log, workspace, self.control()),
            "completed",
        )
        compare_work_outputs(workspace)
        return fixture, workspace

    def assert_recovery(self, fixture, workspace, saved):
        with (
            mock.patch.object(
                supervision, "execute_work_plan", side_effect=AssertionError("execute")
            ),
            mock.patch.object(
                planner, "plan_reproduction_work", side_effect=AssertionError("plan")
            ),
            mock.patch.object(Path, "read_bytes", side_effect=AssertionError("source")),
        ):
            self.assertEqual(publish_work_job(fixture.log, workspace.run_root), 1)
        inspection = load_inspection(fixture.log)
        self.assertEqual(inspection.run, saved)
        self.assertEqual(inspection.generation, 1)
        self.assertEqual(
            (fixture.log.root / "reproduction.md").read_text(),
            "# Reproduction\n\n" + render_saved_summary(inspection),
        )
        with open_work_job(workspace.run_root) as job:
            journal = job.load_publication()
            self.assertEqual(journal.result_generation, 1)
            self.assertEqual(journal.report_generation, 1)
            self.assertEqual(journal.finished_at, saved.finished_at)
            self.assertEqual(job.load_run_control().status, "complete")
            self.assertEqual(job.load_run_owner().state, "exited")

    def test_real_native_graph_publishes_and_closes_run_atomically(self):
        fixture, workspace = self.prepare()
        self.assertEqual(publish_work_job(fixture.log, workspace.run_root), 1)
        saved = load_inspection(fixture.log).run
        self.assert_recovery(fixture, workspace, saved)

    def test_lost_result_acknowledgment_reuses_exact_frozen_completion(self):
        fixture, workspace = self.prepare()
        original = publication.publish_saved_run

        def committed_then_cut(*args):
            original(*args)
            raise OSError("lost acknowledgment")

        with mock.patch.object(
            publication, "publish_saved_run", side_effect=committed_then_cut
        ):
            with self.assertRaisesRegex(OSError, "lost acknowledgment"):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_publication().result_generation)
            self.assertEqual(job.load_run_control().phase, "publishing")
        self.assert_recovery(fixture, workspace, saved)

    def test_report_failure_keeps_committed_facts_queryable_and_recovers(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            publication,
            "materialize_saved_report_locked",
            side_effect=OSError("report cut"),
        ):
            with self.assertRaisesRegex(OSError, "report cut"):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_publication().result_generation, 1)
            self.assertIsNone(job.load_publication().report_generation)
        self.assert_recovery(fixture, workspace, saved)

    def test_terminal_job_write_cut_recovers_already_materialized_report(self):
        fixture, workspace = self.prepare()

        def cut(operation, _db):
            if operation == "work_report_commit":
                raise OSError("terminal acknowledgment cut")

        with mock.patch.object(jobs, "_before_commit", side_effect=cut):
            with self.assertRaisesRegex(OSError, "terminal acknowledgment cut"):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        self.assertTrue((fixture.log.root / "reproduction.md").is_file())
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().phase, "publishing")
            self.assertEqual(job.load_run_owner().state, "running")
        self.assert_recovery(fixture, workspace, saved)

    def test_missing_disposable_saved_domain_is_recreated_from_frozen_job(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            publication,
            "materialize_saved_report_locked",
            side_effect=OSError("report cut"),
        ):
            with self.assertRaises(OSError):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        shared.result_store_path(fixture.log.root).unlink()
        self.assert_recovery(fixture, workspace, saved)

    def test_missing_execution_facts_cannot_begin_publication(self):
        fixture, workspace = self.prepare_graph()
        with self.assertRaises(ReproductionDomainError):
            publish_work_job(fixture.log, workspace.run_root)
        self.assertFalse(shared.result_store_path(fixture.log.root).exists())
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_publication())
            self.assertEqual(job.load_run_control().phase, "accepted")

    def test_missing_running_owner_rejects_before_journal_or_shared_write(self):
        fixture, workspace = self.prepare()
        with open_work_job(workspace.run_root) as job:
            job._db.execute("DELETE FROM run_owner")
        with self.assertRaises(JobStoreTransitionError):
            publish_work_job(fixture.log, workspace.run_root)
        self.assertFalse(shared.result_store_path(fixture.log.root).exists())
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_publication())
            self.assertIsNone(job.load_run_control().status)

    def test_job_has_no_public_arbitrary_generation_submission_api(self):
        fixture, workspace = self.prepare()
        with open_work_job(workspace.run_root) as job:
            self.assertFalse(hasattr(job, "record_result_commit"))
            self.assertFalse(hasattr(job, "finish_publication"))

    def test_actual_report_marker_failure_cannot_terminalize_job(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            reports, "record_report_materialization", return_value=False
        ):
            with self.assertRaises(ActionError):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_publication().report_generation)
            self.assertEqual(job.load_run_control().phase, "publishing")
            self.assertEqual(job.load_run_owner().state, "running")
        self.assert_recovery(fixture, workspace, saved)

    def test_failed_publication_resumes_only_frozen_publication(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            publication, "materialize_saved_report_locked", side_effect=OSError("cut")
        ):
            with self.assertRaises(OSError):
                publish_work_job(fixture.log, workspace.run_root)
        saved = load_inspection(fixture.log).run
        stamp = "2099-01-01T00:00:00Z"
        with open_work_job(workspace.run_root) as job:
            owner = job.load_run_owner()
            job.request_run_failure(
                RunFailure("reproduction.publication.failed", "cut", stamp)
            )
            job.replace_run_owner(
                RunOwner(owner.supervisor_pid, "exited", owner.registered_at, stamp)
            )
            job.finish_run_stop(RunStopCompletion(stamp))
            with self.assertRaises(JobStoreTransitionError):
                job.begin_run_resume(RunResumeRequest(stamp))
            job.begin_publication_resume(RunResumeRequest(stamp))
            self.assertEqual(job.load_run_control().phase, "publishing")
            self.assertIsNone(job.load_run_control().operational_code)
            self.assertEqual(job.load_publication().finished_at, saved.finished_at)
            job.replace_run_owner(
                RunOwner(owner.supervisor_pid, "running", stamp, stamp)
            )
        self.assert_recovery(fixture, workspace, saved)

    def test_other_run_failures_cannot_reopen_publication(self):
        fixture, workspace = self.prepare()
        stamp = "2099-01-01T00:00:00Z"
        with open_work_job(workspace.run_root) as job:
            owner = job.load_run_owner()
            job.request_run_failure(
                RunFailure("reproduction.execution.failed", "other failure", stamp)
            )
            job.replace_run_owner(
                RunOwner(owner.supervisor_pid, "exited", owner.registered_at, stamp)
            )
            job.finish_run_stop(RunStopCompletion(stamp))
            with self.assertRaises(JobStoreTransitionError):
                job.begin_publication_resume(RunResumeRequest(stamp))
            self.assertEqual(job.load_run_control().status, "failed")

    def test_running_prior_owner_prevents_publication_resume(self):
        fixture, workspace = self.prepare()
        with mock.patch.object(
            publication, "materialize_saved_report_locked", side_effect=OSError("cut")
        ):
            with self.assertRaises(OSError):
                publish_work_job(fixture.log, workspace.run_root)
        stamp = "2099-01-01T00:00:00Z"
        with open_work_job(workspace.run_root) as job:
            owner = job.load_run_owner()
            job.request_run_failure(
                RunFailure("reproduction.publication.failed", "cut", stamp)
            )
            job.finish_run_stop(RunStopCompletion(stamp))
            with self.assertRaises(JobStoreTransitionError):
                job.begin_publication_resume(RunResumeRequest(stamp))
            self.assertEqual(job.load_run_control().status, "failed")
            self.assertEqual(job.load_run_owner(), owner)
