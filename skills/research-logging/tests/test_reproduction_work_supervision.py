"""Native graph execution preserves real dependency, reuse and stop behavior."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import test_reproduction_work_execution as execution_fixture
from log_commands import reproduction_planner as planner
from log_commands import reproduction_work_supervision as supervision
from log_commands.model import ActionError
from log_commands.reproduction_domain import ArtifactOutcome, CommandOutcome
from log_commands.reproduction_execution import (
    ReproductionControlPlaneError,
    populate_current_output_workspace,
)
from log_commands.reproduction_job_control import (
    RunOwner,
    RunResumeRequest,
    RunStopRequest,
)
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from log_commands.reproduction_work_execution import compare_work_outputs
from log_commands.reproduction_work_job import (
    WorkJobAcceptance,
    create_work_job,
    open_work_job,
)
from log_commands.reproduction_work_supervision import (
    WorkPlanControl,
    execute_work_plan,
)
from reproduction_planning_test_support import _effective_fingerprint, _fingerprint
from test_reproduction_canonical_records import WHEN
from test_reproduction_model_preservation import fanout_fixture
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.pyrun_state import load_pyrun_state


class NativeSupervisionTests(unittest.TestCase):
    def control(self, **options):
        return WorkPlanControl(
            os.getpid(), confinement=execution_fixture.TestConfinement(), **options
        )

    def register(self, workspace):
        with open_work_job(workspace.run_root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))

    def prepare_graph(self, *, fail_producer=False, linked_data=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name).resolve()
        fixture, entry, _ = fanout_fixture(project, 2)
        if linked_data:
            data = entry.root / "data"
            retained = project / "output" / "logs" / "study" / entry.id / "data"
            retained.parent.mkdir(parents=True)
            data.rename(retained)
            data.symlink_to(
                os.path.relpath(retained, data.parent), target_is_directory=True
            )
        (project / ".conda/bin").mkdir(parents=True)
        (project / ".conda/bin/python").symlink_to(sys.executable)
        state = load_pyrun_state(entry.root / "pyrun.json", entry_root=entry.root)
        executions = []
        for cid, command in state.commands.items():
            for identity, execution in command.executions.items():
                script = entry.root / execution.recipe.script
                script.write_text(
                    "import argparse\nfrom pathlib import Path\n"
                    "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
                    "p.add_argument('--output-data', action='append')\n"
                    "a=p.parse_args()\n"
                    + (
                        "raise SystemExit(7)\n"
                        if fail_producer and cid == "producer"
                        else "for output in a.output_data:\n"
                        "    Path(output).write_text(Path(output).stem)\n"
                        if cid == "producer"
                        else "for output in a.output_data:\n"
                        "    value = Path(a.input_data).read_text()\n"
                        f"    Path(output).write_text(value + '|{cid}')\n"
                    ),
                    encoding="utf-8",
                )
                executions.append(
                    (
                        identity,
                        replace(
                            execution,
                            observed=replace(
                                execution.observed,
                                script=_fingerprint(script),
                                effective_code=_effective_fingerprint(
                                    script, project
                                ),
                            ),
                        ),
                    )
                )
        fixture.write_pyrun(entry, executions)
        evaluation = evaluate_mechanical(
            EvaluationRequest(fixture.summary, FullEvaluationTarget())
        )
        plan = planner.plan_reproduction_work(
            fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=entry,
            include_all=False,
        )
        plan = replace(plan, settings=replace(plan.settings, jobs=2))
        run_id = "reproduce-native-graph"
        path = canonical_run_path(
            WHEN, run_leaf(fixture.summary.stem, entry.id, run_id)
        ).as_posix()
        root = project / path
        root.mkdir(parents=True)
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, path, project))
        workspace = populate_current_output_workspace(project, root, run_id)
        self.register(workspace)
        return fixture, workspace

    def test_real_graph_runs_dependencies_and_uses_generated_inputs(self):
        fixture, workspace = self.prepare_graph()
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            saved = job.load_completed_run(finished_at="2030-01-01T00:00:00Z")
            self.assertEqual(len(saved.command_results), 4)
            self.assertTrue(
                all(
                    result.outcome is CommandOutcome.SUCCEEDED
                    for result in saved.command_results
                )
            )
            self.assertEqual(job.load_scheduler_owner().checkpoints, ())
        entry = next(work for work in saved.commands if work.identity.cid == "first")
        generated = workspace.map_source(Path(entry.entry_root) / "data/first.txt")
        self.assertEqual(generated.read_text(), "output-0|first")

    def test_comparison_finds_outputs_from_linked_entry_data_directory(self):
        fixture, workspace = self.prepare_graph(linked_data=True)
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            results = tuple(
                job.load_artifact_result(artifact.identity)
                for artifact in job.accepted.plan.artifacts
            )
        self.assertTrue(results)
        for result in results:
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIsNot(result.outcome, ArtifactOutcome.NOT_COMPARED)
            self.assertIsNotNone(result.regenerated)

    def fresh_graph(self):
        fixture, workspace = self.prepare_graph()
        for path in (
            workspace.work_project,
            workspace.runtime_root,
            workspace.diagnostics_root,
            workspace.staging_root,
        ):
            path.rmdir()
        return fixture, workspace

    def test_supervisor_publishes_and_closes_exact_owner(self):
        fixture, workspace = self.fresh_graph()
        with open_work_job(workspace.run_root) as job:
            installed = job.load_run_owner()
        supervision.supervise_work_job(
            fixture.log, workspace.run_root, mode="fresh", control=self.control()
        )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "complete")
            self.assertEqual(job.load_run_owner().state, "exited")
            self.assertEqual(
                job.load_run_owner().registered_at, installed.registered_at
            )
            self.assertEqual(
                job.load_run_owner().supervisor_pid, installed.supervisor_pid
            )
            self.assertIsNotNone(job.load_publication().report_generation)
        self.assertTrue((fixture.log.root / "reproduction.md").is_file())

    def test_full_supervisor_survivor_failure_keeps_lease_and_public_control_exclusion(
        self,
    ):
        from log_commands import reproduction_jobs as jobs
        from log_commands.reproduction_job_control import (
            RecoveryWorkerObservation,
            WorkerRecord,
        )
        from test_reproduction_canonical_records import WHEN

        fixture, workspace = self.fresh_graph()
        worker = WorkerRecord("worker-98765", None, 98765, "running", WHEN, WHEN)

        def surviving_execution(_log, _workspace, _control):
            with open_work_job(workspace.run_root) as job:
                job.replace_recovery_workers(
                    (RecoveryWorkerObservation(None, worker),), observed_at=WHEN
                )
            raise ReproductionControlPlaneError(
                RuntimeError("worker cleanup incomplete"), cleanup_incomplete=True
            )

        with mock.patch.object(
            supervision, "execute_work_plan", side_effect=surviving_execution
        ):
            with self.assertRaisesRegex(
                ReproductionControlPlaneError, "worker cleanup incomplete"
            ):
                supervision.supervise_work_job(
                    fixture.log,
                    workspace.run_root,
                    mode="fresh",
                    control=self.control(),
                )
        with open_work_job(workspace.run_root) as job:
            accepted = job.accepted
            self.assertIsNone(job.load_run_control().status)
            self.assertEqual(job.load_run_control().phase, "stopping")
            self.assertEqual(job.load_run_owner().state, "running")
            self.assertEqual(
                job.load_scheduler_owner().running_workers, ((None, worker),)
            )
            self.assertIsNone(job.load_publication())
        with self.assertRaises(ActionError) as caught:
            jobs._require_no_recovery_exclusion(
                fixture.log, None, ignore_recovery_run_id=None
            )
        self.assertEqual(caught.exception.code, "reproduction.recovery.active")
        with self.assertRaises(ActionError) as caught:
            jobs.resume_reproduction(fixture.log, accepted.run_id)
        self.assertEqual(caught.exception.code, "reproduction.resume.invalid_state")
        with (
            mock.patch.object(jobs, "STOP_WAIT_SECONDS", 0.02),
            mock.patch.object(jobs, "STATUS_POLL_SECONDS", 0.005),
        ):
            with self.assertRaises(ActionError) as caught:
                jobs.stop_reproduction(fixture.log, accepted.run_id)
        self.assertEqual(caught.exception.code, "reproduction.stop.incomplete")
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_owner().state, "running")
            self.assertEqual(
                job.load_scheduler_owner().running_workers, ((None, worker),)
            )
            self.assertIsNone(job.load_run_control().status)

    def test_durable_stop_before_supervisor_start_remains_resumable(self):
        fixture, workspace = self.fresh_graph()
        with open_work_job(workspace.run_root) as job:
            job.request_run_stop(RunStopRequest(WHEN))
        with mock.patch.object(
            supervision,
            "execute_work_recipe",
            side_effect=AssertionError("launch after stop"),
        ):
            supervision.supervise_work_job(
                fixture.log, workspace.run_root, mode="fresh", control=self.control()
            )
        self.assertTrue(workspace.work_project.is_dir())
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "stopped")
            self.assertEqual(job.load_run_owner().state, "stopped")
            self.assertTrue(
                all(
                    job.load_command_result(work.identity) is None
                    for work in job.accepted.plan.commands
                )
            )

    def test_supervisor_stops_then_resumes_same_acceptance(self):
        fixture, workspace = self.fresh_graph()
        with open_work_job(workspace.run_root) as job:
            accepted = job.accepted
        supervision.supervise_work_job(
            fixture.log,
            workspace.run_root,
            mode="fresh",
            control=self.control(stop_requested=lambda: True),
        )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "stopped")
            self.assertEqual(job.load_run_owner().state, "stopped")
            self.assertEqual(job.accepted, accepted)
            job.begin_run_resume(RunResumeRequest("2030-01-01T00:00:00Z"))
            job.replace_run_owner(
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:00Z",
                )
            )
        supervision.supervise_work_job(
            fixture.log,
            workspace.run_root,
            mode="stopped",
            control=self.control(resume=True),
        )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "complete")
            self.assertEqual(job.accepted, accepted)

    def test_supervisor_publication_retry_never_executes_or_opens_workspace(self):
        from log_commands import reproduction_work_publication as publication

        fixture, workspace = self.fresh_graph()
        with mock.patch.object(
            publication,
            "materialize_saved_report_locked",
            side_effect=OSError("report cut"),
        ):
            with self.assertRaisesRegex(OSError, "report cut"):
                supervision.supervise_work_job(
                    fixture.log,
                    workspace.run_root,
                    mode="fresh",
                    control=self.control(),
                )
        with open_work_job(workspace.run_root) as job:
            state = job.load_run_control()
            self.assertEqual(state.status, "failed")
            self.assertEqual(state.operational_code, "reproduction.publication.failed")
            self.assertEqual(job.load_run_owner().state, "exited")
            accepted = job.accepted
            job.begin_publication_resume(RunResumeRequest("2030-01-01T00:00:00Z"))
            job.replace_run_owner(
                RunOwner(
                    os.getpid(),
                    "running",
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:00:00Z",
                )
            )
        with (
            mock.patch.object(
                supervision, "execute_work_plan", side_effect=AssertionError("execute")
            ),
            mock.patch.object(
                supervision,
                "open_current_workspace",
                side_effect=AssertionError("workspace"),
            ),
        ):
            supervision.supervise_work_job(
                fixture.log,
                workspace.run_root,
                mode="publication",
                control=self.control(),
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().status, "complete")
            self.assertEqual(job.accepted, accepted)

    def test_failed_root_blocks_only_dependents_without_attempts_or_copied_problems(
        self,
    ):
        fixture, workspace = self.prepare_graph(fail_producer=True)
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            saved = job.load_completed_run(finished_at="2030-01-01T00:00:00Z")
            results = {result.identity.cid: result for result in saved.command_results}
            self.assertEqual(results["producer"].outcome, CommandOutcome.FAILED)
            self.assertEqual(results["independent"].outcome, CommandOutcome.SUCCEEDED)
            for cid in ("first", "second"):
                result = results[cid]
                self.assertEqual(result.outcome, CommandOutcome.BLOCKED)
                self.assertEqual(result.blocked_by, (results["producer"].identity,))
                self.assertEqual(result.problem_ids, ())
                self.assertEqual(result.argv, ())
                self.assertIsNone(result.started_at)
                self.assertIsNone(job.load_execution_checkpoint(result.identity))
            self.assertEqual(
                len(
                    [
                        problem
                        for problem in saved.problems
                        if problem.subject == results["producer"].identity
                    ]
                ),
                1,
            )

    def test_same_fixed_run_reuses_outputs_without_another_subprocess(self):
        fixture, workspace = self.prepare_graph()
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        with mock.patch.object(
            supervision, "execute_work_recipe", side_effect=AssertionError("rerun")
        ):
            self.assertEqual(
                execute_work_plan(fixture.log, workspace, self.control()), "completed"
            )

    def test_directory_member_evidence_keeps_declared_output_comparison_and_reuse(self):
        fixture, work, workspace, retained = (
            execution_fixture.NativeExecutionTests.prepare(
                self,
                "import argparse\nfrom pathlib import Path\n"
                "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
                "p.add_argument('--output-data')\na=p.parse_args()\n"
                "root=Path(a.output_data)\nroot.mkdir()\n"
                "(root/'member.txt').write_text('baseline')\n",
                directory_output=True,
                attach=False,
            )
        )
        self.register(workspace)
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            plan = job.accepted.plan
            self.assertEqual(len(plan.artifacts), 1)
            self.assertEqual(plan.artifacts[0].identity.artifact, "data/output.txt")
            self.assertEqual(plan.artifacts[0].retained_path, str(retained))
            saved = job.load_completed_run(finished_at="2030-01-01T00:00:00Z")
            self.assertEqual(saved.artifact_results[0].outcome.value, "matched")
            self.assertEqual(saved.command_results[0].identity, work.identity)
        with mock.patch.object(
            supervision, "execute_work_recipe", side_effect=AssertionError("rerun")
        ):
            self.assertEqual(
                execute_work_plan(fixture.log, workspace, self.control()), "completed"
            )
        self.assertEqual((retained / "member.txt").read_text(), "baseline")

    def test_changed_private_completed_output_rejects_reuse(self):
        fixture, workspace = self.prepare_graph()
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, self.control()), "completed"
        )
        with open_work_job(workspace.run_root) as job:
            key = next(
                work.identity
                for work in job.accepted.plan.commands
                if work.identity.cid == "producer"
            )
            result = job.load_command_result(key)
        private_output = (
            Path(result.cwd) / result.argv[result.argv.index("--output-data") + 1]
        )
        private_output.write_text("changed")
        with self.assertRaisesRegex(ReproductionControlPlaneError, "not reusable"):
            execute_work_plan(fixture.log, workspace, self.control())

    def test_local_stop_is_durable_and_does_not_launch_pending_work(self):
        fixture, workspace = self.prepare_graph()
        with mock.patch.object(
            supervision, "execute_work_recipe", side_effect=AssertionError("launch")
        ):
            self.assertEqual(
                execute_work_plan(
                    fixture.log, workspace, self.control(stop_requested=lambda: True)
                ),
                "stopped",
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().phase, "stopping")
            self.assertIsNotNone(job.load_run_control().stop_requested_at)
            self.assertTrue(
                all(
                    job.load_command_result(work.identity) is None
                    for work in job.accepted.plan.commands
                )
            )

    def test_owner_must_match_before_any_subprocess(self):
        fixture, work, workspace, _ = execution_fixture.NativeExecutionTests.prepare(
            self,
            "raise SystemExit(0)\n",
            attach=False,
        )
        with self.assertRaisesRegex(
            ReproductionControlPlaneError, "matching running supervisor"
        ):
            execute_work_plan(fixture.log, workspace, self.control())
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_execution_checkpoint(work.identity))

    def test_stop_between_outer_poll_and_dependency_resolution_drains_cleanly(self):
        fixture, workspace = self.prepare_graph(fail_producer=True)
        original = supervision._resolve_pending
        interrupted = []

        def stop_at_block_boundary(stage, pending):
            with open_work_job(workspace.run_root) as job:
                if not interrupted and any(
                    job.load_execution_readiness(key).disposition == "dependency_failed"
                    for key in pending
                ):
                    job.request_run_stop(RunStopRequest("2030-01-01T00:00:00Z"))
                    interrupted.append(True)
            return original(stage, pending)

        with mock.patch.object(
            supervision, "_resolve_pending", side_effect=stop_at_block_boundary
        ):
            self.assertEqual(
                execute_work_plan(fixture.log, workspace, self.control()), "stopped"
            )
        self.assertEqual(interrupted, [True])
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_run_control().phase, "stopping")
            for work in job.accepted.plan.commands:
                if work.identity.cid in {"first", "second"}:
                    self.assertIsNone(job.load_command_result(work.identity))
