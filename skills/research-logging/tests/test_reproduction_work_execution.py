"""Physical native attempts retain facts before releasing scheduler ownership."""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands import dispatcher
from log_commands import reproduction_planner as planner
from log_commands import reproduction_scheduler as scheduler
from log_commands import reproduction_work_execution as execution
from log_commands import reproduction_work_job as job_storage
from log_commands.model import ActionError
from log_commands.reproduction_domain import CommandOutcome, ProblemStage
from log_commands.reproduction_execution import (
    ReproductionControlPlaneError,
    observe_output_fingerprint,
    populate_current_output_workspace,
)
from log_commands.reproduction_inspection import (
    artifact_detail,
    command_detail,
    load_inspection,
    render_saved_summary,
)
from log_commands.reproduction_job_control import (
    ExecutionIdentity,
    ExecutionPermitAttachment,
    RunOwner,
)
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from log_commands.reproduction_saved_report import render_saved_report
from log_commands.reproduction_saved_storage import publish_saved_run
from log_commands.reproduction_work_execution import (
    WorkExecutionControl,
    execute_work_recipe,
)
from log_commands.reproduction_work_job import (
    AttemptCompletion,
    WorkJobAcceptance,
    create_work_job,
    open_work_job,
)
from reproduction_planning_test_support import (
    _effective_fingerprint,
    _fingerprint,
    _Fixture,
)
from test_reproduction_canonical_records import WHEN
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.pyrun_state import execution_id


class TestConfinement:
    """Synthetic tests exercise actual subprocesses without production confinement."""

    def preflight(self):
        return None

    def command(self, command, **_options):
        return list(command)


class NativeExecutionTests(unittest.TestCase):
    @unittest.skipUnless(
        sys.platform == "darwin" and os.environ.get("REPRODUCTION_SANDBOX_TEST") == "1",
        "requires the opt-in macOS Seatbelt smoke host",
    )
    def test_production_seatbelt_denies_network_and_retained_write(self):
        fixture, work, workspace, retained = self.prepare(
            "import argparse, socket\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data')\na=p.parse_args()\n"
            "try:\n    Path(a.input_data).write_text('unsafe')\n    write='allowed'\n"
            "except OSError:\n    write='denied'\n"
            "try:\n    socket.create_connection(('1.1.1.1',53),timeout=0.2)\n"
            "    network='allowed'\n"
            "except OSError:\n    network='denied'\n"
            "print(f'write={write} network={network}')\n"
            "Path(a.output_data).write_text(Path(a.input_data).read_text())\n"
        )
        result = execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl("native-grant", lambda: None),
        )
        self.assertIs(result.outcome, CommandOutcome.SUCCEEDED)
        self.assertIn(
            "write=denied network=denied",
            (workspace.run_root / result.stdout_path).read_text(),
        )
        self.assertEqual(retained.read_text(), "baseline")
        self.assertEqual((Path(work.entry_root) / "data/raw.txt").read_text(), "input")

    def prepare(
        self,
        script,
        *,
        second_output=False,
        directory_output=False,
        uncited_second=False,
        attach=True,
        log_helper: str | None = None,
    ):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name).resolve()
        fixture = _Fixture(project)
        entry = fixture.entry(1)
        (project / ".conda/bin").mkdir(parents=True)
        (project / ".conda/bin/python").symlink_to(sys.executable)
        raw = entry.root / "data/raw.txt"
        output = entry.root / "data/output.txt"
        raw.write_text("input", encoding="utf-8")
        output.write_text("baseline", encoding="utf-8")
        outputs = {"output": output}
        if second_output:
            second = entry.root / "data/second.txt"
            second.write_text("second baseline", encoding="utf-8")
            outputs["second"] = second
        fixture.write_data(
            entry,
            [
                fixture.item(entry, "raw", raw, origin=True),
                *(
                    fixture.item(entry, name, path, origin=False)
                    for name, path in outputs.items()
                ),
            ],
        )
        fixture.evidence(
            entry,
            *(name for name in outputs if not (uncited_second and name == "second")),
        )
        identity, authored = fixture.execution(entry, "producer", {"raw": raw}, outputs)
        if log_helper is not None:
            log_scripts = fixture.log_root / "scripts"
            log_scripts.mkdir(exist_ok=True)
            (log_scripts / "shared_helper.py").write_text(
                log_helper,
                encoding="utf-8",
            )
        if directory_output:
            output.unlink()
            output.mkdir()
            member = output / "member.txt"
            member.write_text("baseline", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    {
                        "identity": {"algorithm": "directory-sha256-v1"},
                        "kind": "directory",
                        "location": "data/output.txt",
                        "name": "output",
                        "origin": False,
                    },
                    fixture.item(entry, "member", member, origin=False),
                ],
            )
            fixture.evidence(entry, "member")
            # Evidence rewrites the document; restore its authored invocation.
            _, declaration = fixture.execution(
                entry, "producer", {"raw": raw}, {"member": member}
            )
            document = entry.root / f"{entry.id}.md"
            document.write_text(
                document.read_text().replace("'<member>'", "'<output>'")
            )
            recipe = replace(
                authored.recipe, outputs=(("data/output.txt", "directory"),)
            )
            authored = replace(
                authored,
                recipe=recipe,
                observed=replace(
                    declaration.observed,
                    outputs=(
                        (
                            "data/output.txt",
                            observe_output_fingerprint(output, "directory"),
                        ),
                    ),
                ),
            )
            identity = execution_id(recipe)
        script_path = entry.root / "scripts/producer.py"
        script_path.write_text(script, encoding="utf-8")
        fixture.write_pyrun(
            entry,
            [
                (
                    identity,
                    replace(
                        authored,
                        observed=replace(
                            authored.observed,
                            script=_fingerprint(script_path),
                            effective_code=_effective_fingerprint(
                                script_path, project
                            ),
                        ),
                    ),
                )
            ],
        )
        evaluation = evaluate_mechanical(
            EvaluationRequest(fixture.summary, FullEvaluationTarget())
        )
        plan = planner.plan_reproduction_work(
            fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=entry,
            include_all=False,
        )
        run_id = "reproduce-native-physical"
        run_path = canonical_run_path(
            WHEN, run_leaf(fixture.summary.stem, entry.id, run_id)
        ).as_posix()
        root = project / run_path
        root.mkdir(parents=True)
        create_work_job(root, WorkJobAcceptance(run_id, plan, WHEN, run_path, project))
        workspace = populate_current_output_workspace(project, root, run_id)
        work = plan.commands[0]
        if attach:
            with open_work_job(root) as job:
                job.attach_execution_permit(
                    ExecutionPermitAttachment(
                        work.identity.entry,
                        work.identity.cid,
                        work.identity.execution_id,
                        "native-grant",
                        WHEN,
                    )
                )
        return fixture, work, workspace, output

    def test_native_execution_uses_the_log_shared_import_context(self):
        fixture, work, workspace, retained = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "from shared_helper import render\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data')\na=p.parse_args()\n"
            "Path(a.output_data).write_text(render(Path(a.input_data).read_text()))\n",
            log_helper="def render(value):\n    return value.upper()\n",
        )

        result = execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant",
                lambda: None,
                confinement=TestConfinement(),
            ),
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCEEDED)
        regenerated = workspace.map_source(retained)
        self.assertEqual(regenerated.read_text(), "INPUT")
        self.assertEqual(retained.read_text(), "baseline")

    def test_native_scheduler_admits_real_process_and_reconciles_terminal_grant(self):
        fixture, work, workspace, retained = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data')\na=p.parse_args()\n"
            "Path(a.output_data).write_text('scheduled output')\n",
            attach=False,
        )
        root = workspace.run_root
        with open_work_job(root) as job:
            job.replace_run_owner(RunOwner(os.getpid(), "running", WHEN, WHEN))
            key = work.identity
            accepted = job.load_accepted_scheduling(
                ExecutionIdentity(key.entry, key.cid, key.execution_id)
            )
            request = scheduler.SchedulerPermitRequest(
                scheduler.SchedulerIdentity(
                    workspace.source_project,
                    workspace.run_id,
                    key.entry,
                    key.cid,
                    key.execution_id,
                    accepted.plan_order,
                ),
                accepted.kind,
                os.getpid(),
                *scheduler._accepted_scheduler_claims(
                    accepted, root, workspace.source_project
                ),
                WHEN,
            )
        decision = scheduler.poll_work_permit(
            root, request, checkpointed_at=WHEN, expected_state="absent"
        )
        self.assertEqual(decision.disposition, "granted")
        grant = decision.permit.permit_id
        released = []

        def release():
            with open_work_job(root) as job:
                proof = job.load_scheduler_owner()
                self.assertEqual(
                    job.load_command_result(key).outcome, CommandOutcome.SUCCEEDED
                )
            reconciliation = scheduler.reconcile_permit(request.identity, proof)
            self.assertEqual(reconciliation.clear_run_permit_id, grant)
            released.append(reconciliation.delta.removed_permit_ids)

        result = execute_work_recipe(
            fixture.log,
            key,
            workspace,
            WorkExecutionControl(grant, release, confinement=TestConfinement()),
        )
        self.assertEqual(result.outcome, CommandOutcome.SUCCEEDED)
        self.assertEqual(released, [(grant,)])
        self.assertEqual(retained.read_text(), "baseline")
        with open_work_job(root) as job:
            self.assertEqual(job.load_scheduler_owner().checkpoints, ())

    def test_real_success_retains_actual_invocation_and_commits_before_release(self):
        fixture, work, workspace, retained = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\n"
            "p.add_argument('--input-data')\np.add_argument('--output-data')\n"
            "a=p.parse_args()\nPath(a.output_data).write_text('regenerated')\n"
            "print('native stdout')\n"
        )
        releases = []

        def release():
            with open_work_job(workspace.run_root) as job:
                observation = job.load_command_result(work.identity)
                self.assertEqual(observation.outcome, CommandOutcome.SUCCEEDED)
                self.assertEqual(
                    job.load_execution_checkpoint(work.identity).permit_id,
                    "native-grant",
                )
                releases.append(observation)

        with ExitStack() as readers:
            readers.enter_context(
                mock.patch.object(
                    planner,
                    "plan_reproduction_work",
                    side_effect=AssertionError("replan"),
                )
            )
            for module_name in (
                "research_log_data",
                "validation.pyrun_state",
                "validation.engine",
                "log_commands.reproduction_execution",
                "log_commands.reproduction_work_execution",
            ):
                module = __import__(module_name, fromlist=["*"])
                for name in (
                    "load_pyrun_state",
                    "load_data_file",
                    "evaluate_mechanical",
                ):
                    if hasattr(module, name):
                        readers.enter_context(
                            mock.patch.object(
                                module,
                                name,
                                side_effect=AssertionError("live metadata read"),
                            )
                        )
            result = execute_work_recipe(
                fixture.log,
                work.identity,
                workspace,
                WorkExecutionControl(
                    "native-grant", release, confinement=TestConfinement()
                ),
            )
        self.assertEqual(releases, [result])
        self.assertEqual(result.outcome, CommandOutcome.SUCCEEDED)
        self.assertEqual(
            result.argv[:2],
            (
                str(Path(sys.executable).resolve()),
                work.entry_root + "/scripts/producer.py",
            ),
        )
        self.assertIn("--output-data", result.argv)
        self.assertEqual(retained.read_text(), "baseline")
        self.assertEqual(workspace.map_source(retained).read_text(), "regenerated")
        self.assertIn(
            "native stdout", (workspace.run_root / result.stdout_path).read_text()
        )
        with open_work_job(workspace.run_root) as job:
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertEqual(checkpoint.state, "completed")
            self.assertIsNone(checkpoint.permit_id)
            self.assertIsNone(checkpoint.scratch_path)
            self.assertEqual(job.load_command_result(work.identity), result)
            artifact = job.accepted.plan.artifacts[0]
        execution.compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            with mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source")
            ):
                saved = job.load_completed_run(finished_at=execution._utc_now())
        self.assertEqual(publish_saved_run(fixture.log_root, saved), 1)
        inspection = load_inspection(fixture.log)
        self.assertEqual(inspection.run, saved)
        self.assertEqual(saved.command_result(work.identity), result)
        compared = saved.artifact_result(artifact.identity)
        self.assertEqual(compared.outcome.value, "not-matched")
        self.assertEqual(compared.expected, _fingerprint(retained))
        self.assertEqual(
            compared.regenerated, _fingerprint(workspace.map_source(retained))
        )
        self.assertIn("Succeeded — 1", render_saved_summary(inspection))
        self.assertIn("Not matched — 1", render_saved_summary(inspection))
        self.assertEqual(
            command_detail(inspection, work.identity)["stdout"]["available"], True
        )
        detail = artifact_detail(inspection, artifact.identity)
        self.assertEqual(detail["status"], "not-matched")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = dispatcher.main(
                ["reproduce", "show", "--path", str(fixture.log_root)]
            )
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), render_saved_summary(inspection))
        report = render_saved_report(fixture.log)
        self.assertEqual(report, "# Reproduction\n\n" + stdout.getvalue())
        self.assertEqual(load_inspection(fixture.log).run, saved)

    def test_real_nonzero_exit_keeps_one_original_diagnosis_and_partial_output(self):
        fixture, work, workspace, retained = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data')\na=p.parse_args()\n"
            "Path(a.output_data).write_text('partial')\n"
            "print('failure context')\nraise SystemExit(7)\n"
        )
        releases = []
        result = execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant",
                lambda: releases.append(True),
                confinement=TestConfinement(),
            ),
        )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(len(result.problem_ids), 1)
        self.assertEqual(set(result.outputs), {"data/output.txt"})
        self.assertEqual(releases, [True])
        self.assertEqual(retained.read_text(), "baseline")
        self.assertFalse(workspace.map_source(retained).exists())
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_command_result(work.identity), result)

            saved = job.load_completed_run(finished_at=execution._utc_now())
        self.assertEqual(len(saved.problems), 1)
        self.assertEqual(saved.problems[0].stage, ProblemStage.EXECUTE)
        self.assertEqual(saved.problems[0].observed["returncode"], 7)
        self.assertEqual(saved.command_result(work.identity), result)
        self.assertEqual(
            saved.artifact_results[0].not_compared_reason.value, "command-failed"
        )

    def test_real_stop_keeps_recovery_state_without_failed_research_result(self):
        fixture, work, workspace, _ = self.prepare("import time\ntime.sleep(30)\n")
        released = []

        def release():
            with open_work_job(workspace.run_root) as job:
                self.assertIsNone(job.load_command_result(work.identity))
                checkpoint = job.load_execution_checkpoint(work.identity)
                self.assertEqual(checkpoint.state, "stopped")
                self.assertEqual(checkpoint.permit_id, "native-grant")
            released.append(True)

        result = execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant",
                release,
                stop_requested=lambda: True,
                confinement=TestConfinement(),
            ),
        )
        self.assertIsNone(result)
        self.assertEqual(released, [True])
        with open_work_job(workspace.run_root) as job:
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertEqual(checkpoint.state, "stopped")
            self.assertIsNone(checkpoint.permit_id)
            self.assertIsNone(checkpoint.scratch_path)
            self.assertIsNone(job.load_command_result(work.identity))

    def test_real_timeout_records_failure_before_releasing_permit_and_scratch(self):
        fixture, work, workspace, retained = self.prepare(
            "import time\ntime.sleep(30)\n"
        )
        released = []

        def release():
            with open_work_job(workspace.run_root) as job:
                result = job.load_command_result(work.identity)
                self.assertEqual(result.outcome, CommandOutcome.FAILED)
                checkpoint = job.load_execution_checkpoint(work.identity)
                self.assertEqual(checkpoint.permit_id, "native-grant")
                released.append(checkpoint.scratch_path)

        result = execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant",
                release,
                execution_timeout_seconds=1,
                confinement=TestConfinement(),
            ),
        )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(len(released), 1)
        self.assertFalse(Path(released[0]).exists())
        self.assertEqual(retained.read_text(), "baseline")
        with open_work_job(workspace.run_root) as job:
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertIsNone(checkpoint.permit_id)
            self.assertIsNone(checkpoint.scratch_path)
            saved = job.load_completed_run(finished_at=execution._utc_now())
            self.assertEqual(saved.problems[0].code, "execution_timeout")
            self.assertTrue(
                all(
                    worker.state == "exited"
                    for worker in job.load_execution_workers(work.identity)
                )
            )

    def test_grant_release_failure_keeps_native_completion_for_recovery(self):
        fixture, work, workspace, _ = self.prepare("raise SystemExit(3)\n")
        with self.assertRaises(ReproductionControlPlaneError):
            execute_work_recipe(
                fixture.log,
                work.identity,
                workspace,
                WorkExecutionControl(
                    "native-grant",
                    mock.Mock(side_effect=RuntimeError("release failed")),
                    confinement=TestConfinement(),
                ),
            )
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(
                job.load_command_result(work.identity).outcome, CommandOutcome.FAILED
            )
            checkpoint = job.load_execution_checkpoint(work.identity)
            self.assertEqual(checkpoint.state, "completed")
            self.assertEqual(checkpoint.permit_id, "native-grant")
            self.assertFalse(Path(checkpoint.scratch_path).exists())

    def test_completion_write_failure_never_releases_uncertain_grant(self):
        fixture, work, workspace, _ = self.prepare("raise SystemExit(3)\n")
        release = mock.Mock()
        processes, observations = [], []
        run = execution._run_attempt
        observe = execution.completed_command_observation

        def capture_process(*args):
            result = run(*args)
            processes.append(result)
            return result

        def capture_observation(*args):
            result = observe(*args)
            observations.append(result)
            return result

        def cut(operation, _db):
            if operation == "work_attempt_completion":
                raise RuntimeError("terminal write cut")

        with (
            mock.patch.object(execution, "_run_attempt", side_effect=capture_process),
            mock.patch.object(
                execution,
                "completed_command_observation",
                side_effect=capture_observation,
            ),
            mock.patch.object(job_storage, "_before_commit", side_effect=cut),
        ):
            with self.assertRaisesRegex(RuntimeError, "terminal write cut"):
                execute_work_recipe(
                    fixture.log,
                    work.identity,
                    workspace,
                    WorkExecutionControl(
                        "native-grant", release, confinement=TestConfinement()
                    ),
                )
        release.assert_not_called()
        process = processes[0]
        self.assertTrue(process.scratch.is_dir())
        with open_work_job(workspace.run_root) as job:
            self.assertIsNone(job.load_command_result(work.identity))
            self.assertEqual(
                job.load_execution_checkpoint(work.identity).state, "active"
            )
            result, problems = observations[0]
            job.record_attempt_completion(
                AttemptCompletion(
                    result,
                    problems,
                    "native-grant",
                    execution._utc_now(),
                    process.active_elapsed,
                    tuple(
                        execution._stored_worker(item)
                        for item in process.outcome.workers
                    ),
                )
            )
        shutil.rmtree(process.scratch)
        release()
        with open_work_job(workspace.run_root) as job:
            job.clear_execution_permit(
                work.identity, "native-grant", checkpointed_at=execution._utc_now()
            )
            job.clear_execution_scratch(
                work.identity,
                str(process.scratch),
                checkpointed_at=execution._utc_now(),
            )
            self.assertEqual(job.load_command_result(work.identity), result)
            self.assertIsNone(job.load_execution_checkpoint(work.identity).scratch_path)
        self.assertEqual(len(processes), 1)
        release.assert_called_once()

    def test_comparison_api_rejects_foreign_workspace_not_result_submissions(self):
        fixture, _work, workspace, _ = self.prepare("raise SystemExit(3)\n")
        foreign = fixture.root / "foreign-workspace"
        foreign.mkdir()
        with self.assertRaisesRegex(ActionError, "accepted job"):
            execution.compare_work_outputs(replace(workspace, work_project=foreign))
        with open_work_job(workspace.run_root) as job:
            self.assertFalse(hasattr(job, "record_artifact_comparison"))

    def test_partial_comparison_retry_keeps_first_fact_and_completes_second(self):
        fixture, work, workspace, _ = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data',action='append')\na=p.parse_args()\n"
            "for path in a.output_data: Path(path).write_text('regenerated')\n",
            second_output=True,
        )
        execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant", lambda: None, confinement=TestConfinement()
            ),
        )
        compare = execution.compare_accepted_artifact
        calls = []

        def interrupt(artifact, *args, **kwargs):
            calls.append(artifact.identity)
            if len(calls) == 2:
                raise OSError("comparison interrupted")
            return compare(artifact, *args, **kwargs)

        with mock.patch.object(
            execution, "compare_accepted_artifact", side_effect=interrupt
        ):
            with self.assertRaisesRegex(OSError, "comparison interrupted"):
                execution.compare_work_outputs(workspace)
        first, second = calls
        with open_work_job(workspace.run_root) as job:
            recorded = job.load_artifact_result(first)
            self.assertIsNotNone(recorded)
            self.assertIsNone(job.load_artifact_result(second))

        def retry(artifact, *args, **kwargs):
            self.assertEqual(artifact.identity, second, "durable comparison repeated")
            return compare(artifact, *args, **kwargs)

        with (
            mock.patch.object(
                execution, "compare_accepted_artifact", side_effect=retry
            ),
            mock.patch.object(
                execution, "_utc_now", return_value="2030-01-01T00:00:00Z"
            ),
        ):
            execution.compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_artifact_result(first), recorded)
            self.assertIsNotNone(job.load_artifact_result(second))

    def test_uncited_output_keeps_full_comparison_coverage(self):
        fixture, work, workspace, _ = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data',action='append')\na=p.parse_args()\n"
            "for path in a.output_data: Path(path).write_text('regenerated')\n",
            second_output=True,
            uncited_second=True,
        )
        execute_work_recipe(
            fixture.log,
            work.identity,
            workspace,
            WorkExecutionControl(
                "native-grant", lambda: None, confinement=TestConfinement()
            ),
        )
        execution.compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            plan = job.accepted.plan
            self.assertEqual(
                {
                    item.output
                    for item in plan.artifacts
                    if item.producer == work.identity
                },
                set(dict(work.execution.recipe.outputs)),
            )
            saved = job.load_completed_run(finished_at=execution._utc_now())
        self.assertEqual(len(saved.artifacts), 2)
        self.assertEqual(len(saved.artifact_results), 2)
        self.assertEqual(saved.command_results[0].outcome, CommandOutcome.SUCCEEDED)
        for result in saved.artifact_results:
            self.assertEqual(result.outcome.value, "not-matched")
            problem = saved.problem(result.problem_ids[0])
            self.assertEqual(problem.subject, result.identity)
            self.assertEqual(problem.observed["difference"]["location"], "line 1")
        self.assertEqual(publish_saved_run(fixture.log_root, saved), 1)
        self.assertEqual(load_inspection(fixture.log).run, saved)
        with mock.patch.object(
            execution,
            "compare_accepted_artifact",
            side_effect=AssertionError("recompare"),
        ):
            execution.compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(
                job.load_completed_run(finished_at=saved.finished_at), saved
            )

    def test_materialization_error_retains_original_stage_type_and_message(self):
        fixture, work, workspace, _ = self.prepare(
            "import argparse\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\np.add_argument('--input-data')\n"
            "p.add_argument('--output-data')\na=p.parse_args()\n"
            "Path(a.output_data).write_text('output')\n"
        )
        observed = []
        original = execution.completed_command_observation

        def capture(*args):
            result, problems = original(*args)
            observed.extend(problems)
            return result, problems

        with (
            mock.patch.object(
                execution,
                "_materialize_outputs",
                side_effect=OSError("original install failure"),
            ),
            mock.patch.object(
                execution, "completed_command_observation", side_effect=capture
            ),
        ):
            result = execute_work_recipe(
                fixture.log,
                work.identity,
                workspace,
                WorkExecutionControl(
                    "native-grant", lambda: None, confinement=TestConfinement()
                ),
            )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(observed[0].stage, ProblemStage.MATERIALIZE)
        self.assertEqual(observed[0].observed["error_type"], "OSError")
        self.assertEqual(observed[0].explanation, "original install failure")
        with open_work_job(workspace.run_root) as job:
            self.assertEqual(job.load_command_result(work.identity), result)
