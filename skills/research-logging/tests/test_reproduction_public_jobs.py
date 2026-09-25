"""Ordinary native public runs preserve execution, effects and saved authority."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
import test_reproduction_work_supervision as fixture_support
from log_commands import dispatcher
from log_commands import reproduction_jobs as jobs
from log_commands.model import ActionError
from log_commands.reproduction_domain import CommandOutcome
from log_commands.reproduction_inspection import (
    EmptyInspection,
    ListSelection,
    inspection_summary,
    list_saved,
    load_inspection,
)
from log_commands.reproduction_job_control import (
    RunOwner,
)
from log_commands.reproduction_promotion import promote_execution
from log_commands.reproduction_reconciliation import reconcile_completed_source
from log_commands.reproduction_saved_run import RunSettings, RunTarget
from log_commands.reproduction_work_execution import compare_work_outputs
from log_commands.reproduction_work_job import WorkJob, open_work_job
from log_commands.reproduction_work_supervision import execute_work_plan
from reproduction_planning_test_support import _Fixture
from test_reproduction_work_execution import TestConfinement
from validation.operation_state import operation_lock
from validation.pyrun_state import load_pyrun_state


class PublicNativeJobsTests(unittest.TestCase):
    def dispatch(self, fixture, action, *options):
        output = io.StringIO()
        with redirect_stdout(output):
            result = dispatcher.main(
                ["reproduce", action, "--path", str(fixture.log.root), *options]
            )
        self.assertEqual(result, 0, output.getvalue())
        return output.getvalue().strip()

    def empty_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fixture = _Fixture(Path(directory.name))
        fixture.summary.write_text(
            "# Study\n\nValidation: [latest completed report](study/validation.md)\n\n"
            "## Summary\n\nNo investigations yet.\n\n## Entries\n",
            encoding="utf-8",
        )
        entry = fixture.entry(1)
        fixture.write_data(entry, [])
        fixture.evidence(entry)
        path = shared.result_store_path(fixture.log.root)
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text()
            )
        return fixture

    def test_explicit_empty_recheck_confirms_without_creating_job_or_run(self):
        fixture = self.empty_fixture()
        with mock.patch.object(
            jobs, "_spawn_supervisor", side_effect=AssertionError("spawn")
        ):
            launch = jobs.launch_reproduction(
                fixture.log, RunTarget("log"), RunSettings(recheck=True)
            )
        self.assertIsNone(launch.run_id)
        inspection = load_inspection(fixture.log)
        self.assertIsInstance(inspection, EmptyInspection)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                dispatcher.main(
                    [
                        "reproduce",
                        "list",
                        "commands",
                        "--path",
                        str(fixture.log.root),
                    ]
                ),
                0,
            )
        self.assertIn("Run: — (empty confirmed)", output.getvalue())
        self.assertNotIn("None", output.getvalue())
        self.assertFalse((fixture.root / "tmp/log/reproduce").exists())
        with mock.patch(
            "pathlib.Path.read_text", side_effect=AssertionError("live source")
        ):
            summary = inspection_summary(load_inspection(fixture.log))
            self.assertIsNone(summary["run_id"])
            self.assertEqual(summary["commands"]["total"], 0)
            self.assertEqual(summary["artifacts"]["total"], 0)
            self.assertEqual(
                list_saved(inspection, ListSelection("commands"))["items"], []
            )
        with self.assertRaises(ActionError) as error:
            load_inspection(fixture.log, "reproduce-never-created")
        self.assertEqual(error.exception.code, "reproduction.run.unknown")

    def test_empty_recheck_recovers_report_without_replacing_receipt(self):
        fixture = self.empty_fixture()
        from log_commands import reproduction_saved_report as reports

        with mock.patch.object(
            reports, "atomic_write_texts", side_effect=OSError("report cut")
        ):
            with self.assertRaisesRegex(ActionError, "report cut"):
                jobs.launch_reproduction(
                    fixture.log, RunTarget("log"), RunSettings(recheck=True)
                )
        receipt = load_inspection(fixture.log).confirmation
        jobs.launch_reproduction(
            fixture.log, RunTarget("log"), RunSettings(recheck=True)
        )
        self.assertEqual(load_inspection(fixture.log).confirmation, receipt)
        self.assertIn(
            "Status: Empty confirmed",
            (fixture.log.root / "reproduction.md").read_text(),
        )

    def test_public_invalid_options_fail_before_preparation_or_mutation(self):
        fixture = self.empty_fixture()
        before = shared.result_store_path(fixture.log.root).read_bytes()
        for action in ("plan", "run"):
            for option, value, code in (
                ("--jobs", "0", "reproduction.jobs.invalid"),
                (
                    "--execution-timeout-seconds",
                    "0",
                    "reproduction.execution_timeout.invalid",
                ),
                ("--entry", "not-an-entry", "reproduction.selector.invalid"),
            ):
                with self.subTest(action=action, option=option):
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            jobs, "_prepare_work", side_effect=AssertionError("prepare")
                        ),
                        redirect_stderr(stderr),
                    ):
                        self.assertEqual(
                            dispatcher.main(
                                [
                                    "reproduce",
                                    action,
                                    "--path",
                                    str(fixture.log.root),
                                    option,
                                    value,
                                ]
                            ),
                            2,
                        )
                    self.assertIn(code, stderr.getvalue())
        self.assertEqual(
            shared.result_store_path(fixture.log.root).read_bytes(), before
        )

    def fixture(self, *, failed=False):
        owner = fixture_support.NativeSupervisionTests()
        self.addCleanup(owner.doCleanups)
        fixture, workspace = owner.prepare_graph(fail_producer=failed)
        return owner, fixture, workspace

    def test_public_entry_run_freezes_all_selector_and_runtime_settings(self):
        owner, fixture, workspace = self.fixture()
        shutil.rmtree(workspace.run_root)
        accepted = []

        def handoff(log, root, fds, *, mode):
            # Preparation and publication locks must not travel to the child.
            with operation_lock(log.root, "log.lock", mode="exclusive"):
                with operation_lock(log.root, "reproduction-publication.lock"):
                    with open_work_job(root) as job:
                        accepted.append(job.accepted)

        with mock.patch.object(jobs, "_spawn_supervisor", side_effect=handoff):
            run_id = self.dispatch(
                fixture,
                "run",
                "--entry",
                "e001",
                "--include-all",
                "--recheck",
                "--jobs",
                "3",
                "--execution-timeout-seconds",
                "17",
            )
        self.assertEqual(accepted[0].run_id, run_id)
        self.assertEqual(accepted[0].plan.target, RunTarget("entry", "e001"))
        self.assertEqual(
            accepted[0].plan.settings,
            RunSettings(
                include_all=True, recheck=True, jobs=3, execution_timeout_seconds=17
            ),
        )

    def test_scope_lock_probe_rechecks_exclusion_after_descriptors_are_owned(self):
        owner, fixture, workspace = self.fixture()
        with mock.patch.object(
            jobs,
            "_require_no_recovery_exclusion",
            side_effect=[
                None,
                ActionError("reproduction.recovery.active", "late survivor"),
            ],
        ) as probe:
            with self.assertRaisesRegex(ActionError, "late survivor"):
                jobs._acquire_scope_locks(fixture.log, None)
        self.assertEqual(probe.call_count, 2)
        with mock.patch.object(
            jobs, "_require_no_recovery_exclusion", return_value=None
        ):
            fds = jobs._acquire_scope_locks(fixture.log, None)
        jobs._close_fds(fds)

    def test_real_detached_supervisor_registers_child_and_completes_native_run(self):
        owner, fixture, workspace = self.fixture()
        shutil.rmtree(workspace.run_root)
        children = []
        actual_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            child = actual_popen(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(jobs.subprocess, "Popen", side_effect=spawn):
            run_id = self.dispatch(fixture, "run", "--jobs", "2")
        self.assertEqual(len(children), 1)
        child = children[0]
        self.addCleanup(lambda: child.poll() is None and child.kill())
        root = jobs._find_run(fixture.log, run_id)
        with open_work_job(root) as job:
            self.assertEqual(job.load_run_owner().supervisor_pid, child.pid)
            self.assertNotEqual(child.pid, os.getpid())
        try:
            self.assertEqual(
                child.wait(timeout=30), 0, (root / "supervisor.log").read_text()
            )
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
            self.fail((root / "supervisor.log").read_text())
        self.assertEqual(
            jobs.reproduction_status(fixture.log, run_id)["status"], "complete"
        )
        self.assertEqual(load_inspection(fixture.log).run.run_id, run_id)

    def test_public_status_finds_run_through_project_tmp_symlink(self):
        owner, fixture, workspace = self.fixture()
        physical_tmp = fixture.root / "retained-tmp"
        (fixture.root / "tmp").rename(physical_tmp)
        (fixture.root / "tmp").symlink_to(physical_tmp, target_is_directory=True)

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                dispatcher.main(
                    [
                        "reproduce",
                        "status",
                        "--path",
                        str(fixture.log.root),
                        "--run-id",
                        "reproduce-native-graph",
                        "--json",
                    ]
                ),
                0,
            )
        self.assertEqual(
            json.loads(output.getvalue())["run_id"], "reproduce-native-graph"
        )

    def run_public(self, *, failed=False):
        owner, fixture, workspace = self.fixture(failed=failed)
        with open_work_job(workspace.run_root) as job:
            identities = {work.identity for work in job.accepted.plan.commands}
        # Discard only this test's seeded helper job; launch must accept its own.
        shutil.rmtree(workspace.run_root)
        accepted = []

        def supervise(log, root, fds, *, mode):
            with open_work_job(root) as job:
                when = job.accepted.accepted_at
                job.replace_run_owner(RunOwner(os.getpid(), "running", when, when))
                accepted.append(job.accepted)
            jobs.supervise_reproduction(
                log,
                root,
                mode=mode,
                inherited_locks=fds,
                confinement=TestConfinement(),
            )

        output = io.StringIO()
        with (
            mock.patch.object(jobs, "_spawn_supervisor", side_effect=supervise),
            redirect_stdout(output),
        ):
            result = dispatcher.main(
                ["reproduce", "run", "--path", str(fixture.log.root), "--jobs", "2"]
            )
        self.assertEqual(result, 0, output.getvalue())
        self.assertEqual(len(accepted), 1)
        self.assertEqual(
            {work.identity for work in accepted[0].plan.commands}, identities
        )
        run_id = output.getvalue().strip()
        self.assertEqual(run_id, accepted[0].run_id)
        status = jobs.reproduction_status(fixture.log, run_id)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["completed_executions"], 4)
        self.assertEqual(status["surviving_workers"], [])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                dispatcher.main(
                    [
                        "reproduce",
                        "status",
                        "--path",
                        str(fixture.log.root),
                        "--run-id",
                        run_id,
                        "--json",
                    ]
                ),
                0,
            )
        public_status = json.loads(output.getvalue())
        self.assertEqual(public_status["schema"], "research-log-reproduction-status/7")
        timing_keys = {
            "entry",
            "cid",
            "execution_id",
            "state",
            "started_at",
            "finished_at",
            "elapsed_seconds",
            "failure",
        }
        for timing in public_status["execution_timings"]:
            self.assertEqual(set(timing), timing_keys)
        return fixture, accepted[0], load_inspection(fixture.log)

    def test_ordinary_run_executes_compares_clears_and_publishes(self):
        fixture, accepted, inspection = self.run_public()
        self.assertTrue(
            all(
                result.outcome is CommandOutcome.SUCCEEDED
                for result in inspection.run.command_results
            )
        )
        self.assertEqual(len(inspection.run.artifact_results), 5)
        for work in accepted.plan.commands:
            state = load_pyrun_state(
                Path(work.entry_root) / "pyrun.json",
                entry_root=Path(work.entry_root),
            )
            self.assertFalse(
                state.execution(
                    work.identity.cid, work.identity.execution_id
                ).requires_reproduction
            )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                dispatcher.main(
                    [
                        "reproduce",
                        "show",
                        "--path",
                        str(fixture.log.root),
                        "--format",
                        "json",
                    ]
                ),
                0,
            )
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["commands"]["total"], 4)
        self.assertEqual(summary["artifacts"]["total"], 5)
        self.assertIn("Commands", (fixture.log.root / "reproduction.md").read_text())
        with mock.patch.object(jobs, "_spawn_supervisor", side_effect=AssertionError):
            no_work = jobs.launch_reproduction(fixture.log, RunTarget(), RunSettings())
        self.assertIsNone(no_work.run_id)
        self.assertEqual(load_inspection(fixture.log).run, inspection.run)

    def test_clearing_saved_results_preserves_native_jobs_and_research_bytes(self):
        fixture, accepted, inspection = self.run_public()
        root = jobs._find_run(fixture.log, accepted.run_id)
        job_before = (root / "state.sqlite").read_bytes()
        owned = {
            path: path.read_bytes()
            for path in fixture.log.root.rglob("*")
            if path.is_file() and ".cache" not in path.parts
        }
        with shared.result_snapshot(fixture.log.root) as db:
            states = tuple(
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM store_state WHERE domain!='reproduction' "
                    "ORDER BY domain"
                )
            )
        shared.clear_reproduction_results(fixture.log.root)
        self.assertEqual((root / "state.sqlite").read_bytes(), job_before)
        self.assertEqual({path: path.read_bytes() for path in owned}, owned)
        with shared.result_snapshot(fixture.log.root) as db:
            self.assertEqual(
                tuple(
                    tuple(row)
                    for row in db.execute(
                        "SELECT * FROM store_state WHERE domain!='reproduction' "
                        "ORDER BY domain"
                    )
                ),
                states,
            )
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name GLOB 'reproduction_*'"
            ):
                self.assertEqual(
                    db.execute(f"SELECT COUNT(*) FROM {row[0]}").fetchone()[0], 0
                )

    def test_failed_producer_blocks_dependents_but_independent_work_runs(self):
        fixture, accepted, inspection = self.run_public(failed=True)
        outcomes = {
            result.identity.cid: result.outcome
            for result in inspection.run.command_results
        }
        self.assertEqual(
            outcomes,
            {
                "producer": CommandOutcome.FAILED,
                "first": CommandOutcome.BLOCKED,
                "second": CommandOutcome.BLOCKED,
                "independent": CommandOutcome.SUCCEEDED,
            },
        )
        root = jobs._find_run(fixture.log, accepted.run_id)
        with open_work_job(root) as job:
            for work in accepted.plan.commands:
                self.assertFalse(job.source_reconciliation_ready(work.identity))
        failed = next(
            result
            for result in inspection.run.command_results
            if result.outcome is CommandOutcome.FAILED
        )
        self.assertEqual(len(failed.problem_ids), 1)

    def test_requirement_write_before_ack_retry_does_not_execute_again(self):
        owner, fixture, workspace = self.fixture()
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, owner.control()), "completed"
        )
        with open_work_job(workspace.run_root) as job:
            identity = next(
                work.identity
                for work in job.accepted.plan.commands
                if work.identity.cid == "producer"
            )
        with mock.patch.object(
            WorkJob,
            "acknowledge_source_reconciliation",
            side_effect=ActionError("test.ack.failed", "cut"),
        ):
            with self.assertRaises(ActionError):
                reconcile_completed_source(fixture.log, workspace.run_root, identity)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("reexecution")):
            self.assertTrue(
                reconcile_completed_source(fixture.log, workspace.run_root, identity)
            )
            self.assertFalse(
                reconcile_completed_source(fixture.log, workspace.run_root, identity)
            )

    def test_requirement_interruption_before_write_has_no_effect_or_ack(self):
        owner, fixture, workspace = self.fixture()
        self.assertEqual(
            execute_work_plan(fixture.log, workspace, owner.control()), "completed"
        )
        compare_work_outputs(workspace)
        with open_work_job(workspace.run_root) as job:
            work = next(
                item
                for item in job.accepted.plan.commands
                if item.identity.cid == "producer"
            )
        pyrun = Path(work.entry_root) / "pyrun.json"
        original = pyrun.read_bytes()
        from log_commands import reproduction_reconciliation as reconciliation

        with mock.patch.object(
            reconciliation, "atomic_write_text", side_effect=OSError("before write")
        ):
            with self.assertRaisesRegex(OSError, "before write"):
                reconcile_completed_source(
                    fixture.log, workspace.run_root, work.identity
                )
        self.assertEqual(pyrun.read_bytes(), original)
        with open_work_job(workspace.run_root) as job:
            self.assertTrue(job.source_reconciliation_ready(work.identity))
        real_write = reconciliation.atomic_write_text
        with mock.patch.object(
            reconciliation, "atomic_write_text", wraps=real_write
        ) as write:
            self.assertTrue(
                reconcile_completed_source(
                    fixture.log, workspace.run_root, work.identity
                )
            )
            self.assertFalse(
                reconcile_completed_source(
                    fixture.log, workspace.run_root, work.identity
                )
            )
        self.assertEqual(write.call_count, 1)

    def test_native_promotion_copies_complete_outputs_without_rewriting_saved_run(self):
        fixture, accepted, before = self.run_public()
        work = next(
            work for work in accepted.plan.commands if work.identity.cid == "producer"
        )
        outputs = tuple(
            artifact
            for artifact in before.run.artifact_results
            if artifact.identity.entry == work.identity.entry
            and artifact.identity.artifact in dict(work.execution.recipe.outputs)
        )
        expected = {
            output.identity.artifact: Path(output.regenerated_path).read_bytes()
            for output in outputs
        }
        self.assertEqual(len(expected), 2)
        result = json.loads(
            self.dispatch(
                fixture,
                "promote",
                "--run-id",
                accepted.run_id,
                "--cid",
                work.identity.cid,
                "--execution-id",
                work.identity.execution_id,
            )
        )
        self.assertEqual(result["outputs"], list(dict(work.execution.recipe.outputs)))
        for output in outputs:
            self.assertEqual(
                (Path(work.entry_root) / output.identity.artifact).read_bytes(),
                expected[output.identity.artifact],
            )
            self.assertEqual(
                Path(output.regenerated_path).read_bytes(),
                expected[output.identity.artifact],
            )
        current = load_pyrun_state(
            Path(work.entry_root) / "pyrun.json", entry_root=Path(work.entry_root)
        )
        promoted = current.execution(work.identity.cid, work.identity.execution_id)
        self.assertFalse(promoted.requires_reproduction)
        self.assertEqual(
            dict(promoted.observed.outputs),
            {output.identity.artifact: output.regenerated for output in outputs},
        )
        self.assertEqual(promoted.recipe, work.execution.recipe)
        self.assertEqual(promoted.observed.inputs, work.execution.observed.inputs)
        assert work.accepted_source is not None
        self.assertEqual(promoted.observed.script, work.accepted_source.script)
        self.assertEqual(
            promoted.observed.effective_code,
            work.accepted_source.effective_code,
        )
        self.assertEqual(load_inspection(fixture.log).run, before.run)

    def test_native_promotion_rejects_failed_unknown_and_foreign_staging(
        self,
    ):
        from dataclasses import replace

        from log_commands.reproduction_invocation import canonical_record_digest

        fixture, accepted, before = self.run_public(failed=True)
        producer = next(
            work for work in accepted.plan.commands if work.identity.cid == "producer"
        )
        independent = next(
            work
            for work in accepted.plan.commands
            if work.identity.cid == "independent"
        )
        preserved = {
            Path(work.entry_root) / name: (Path(work.entry_root) / name).read_bytes()
            for work in accepted.plan.commands
            for name, _ in work.execution.recipe.outputs
        }
        pyrun = Path(independent.entry_root) / "pyrun.json"
        original = pyrun.read_bytes()
        for cid, execution_id, code in (
            (
                producer.identity.cid,
                producer.identity.execution_id,
                "reproduction.promotion.incomplete",
            ),
            (
                "unknown",
                independent.identity.execution_id,
                "reproduction.promotion.execution_missing",
            ),
            (
                independent.identity.cid,
                "pyrun-exec/v2:" + "f" * 64,
                "reproduction.promotion.execution_missing",
            ),
        ):
            with self.assertRaises(ActionError) as caught:
                promote_execution(
                    fixture.log,
                    run_id=accepted.run_id,
                    cid=cid,
                    execution_id=execution_id,
                )
            self.assertEqual(caught.exception.code, code)
        output = next(
            item
            for item in before.run.artifact_results
            if item.identity.artifact == "data/independent.txt"
        )
        staged = Path(output.regenerated_path)
        staged_before = staged.read_bytes()
        outside = fixture.root / "outside.txt"
        outside.write_bytes(staged_before)
        forged = replace(output, regenerated_path=str(outside))
        payload = forged.as_dict()
        del payload["identity"]
        del payload["problem_ids"]
        with sqlite3.connect(fixture.root / accepted.run_path / "state.sqlite") as db:
            key = db.execute(
                "SELECT artifact_pk FROM accepted_work_artifacts "
                "WHERE entry=? AND artifact=?",
                (output.identity.entry, output.identity.artifact),
            ).fetchone()[0]
            db.execute(
                "UPDATE run_artifact_results SET result_json=?,result_digest=? "
                "WHERE artifact_pk=?",
                (json.dumps(payload), canonical_record_digest(forged.as_dict()), key),
            )
        job_path = fixture.root / accepted.run_path / "state.sqlite"
        job_before = job_path.read_bytes()
        with self.assertRaises(ActionError) as caught:
            promote_execution(
                fixture.log,
                run_id=accepted.run_id,
                cid=independent.identity.cid,
                execution_id=independent.identity.execution_id,
            )
        self.assertEqual(
            caught.exception.code, "reproduction.promotion.staging_invalid"
        )
        self.assertEqual(job_path.read_bytes(), job_before)
        self.assertEqual(pyrun.read_bytes(), original)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        self.assertEqual(staged.read_bytes(), staged_before)
        self.assertEqual(outside.read_bytes(), staged_before)
        self.assertEqual(load_inspection(fixture.log).run, before.run)

    def test_native_promotion_conflicts_with_actual_outside_entry_boundary_reader(self):
        from log_commands import reproduction_planner as planner
        from log_commands.reproduction_job_control import (
            RunStopCompletion,
            RunStopRequest,
        )
        from log_commands.reproduction_paths import canonical_run_path, run_leaf
        from log_commands.reproduction_work_job import (
            WorkJobAcceptance,
            create_work_job,
        )
        from test_reproduction_canonical_records import WHEN
        from validation.engine import (
            EvaluationRequest,
            FullEvaluationTarget,
            evaluate_mechanical,
        )

        fixture, accepted, before = self.run_public()
        producer = next(
            work for work in accepted.plan.commands if work.identity.cid == "producer"
        )
        source = Path(producer.entry_root) / "data/output-0.txt"
        entry = fixture.entry(2)
        destination = entry.root / "data/consumer.txt"
        destination.write_text("consumer", encoding="utf-8")
        fixture.write_data(
            entry,
            [
                fixture.item(entry, "upstream", source, origin=False),
                fixture.item(entry, "consumer", destination, origin=False),
            ],
        )
        fixture.evidence(entry, "consumer")
        execution = fixture.execution(
            entry, "boundary-reader", {"upstream": source}, {"consumer": destination}
        )
        fixture.write_pyrun(entry, [execution])
        evaluation = evaluate_mechanical(
            EvaluationRequest(fixture.summary, FullEvaluationTarget())
        )
        plan = planner.plan_reproduction_work(
            fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=entry,
            include_all=False,
        )
        self.assertTrue(
            any(
                item["role"] == "boundary" and item["identity"] == str(source)
                for item in plan.materials
            )
        )
        run_id = "reproduce-active-boundary"
        run_path = canonical_run_path(
            WHEN, run_leaf(fixture.summary.stem, entry.id, run_id)
        ).as_posix()
        run_root = fixture.root / run_path
        run_root.mkdir(parents=True)
        create_work_job(
            run_root, WorkJobAcceptance(run_id, plan, WHEN, run_path, fixture.root)
        )
        pyrun = Path(producer.entry_root) / "pyrun.json"
        original, source_before = pyrun.read_bytes(), source.read_bytes()
        with self.assertRaises(ActionError) as caught:
            promote_execution(
                fixture.log,
                run_id=accepted.run_id,
                cid=producer.identity.cid,
                execution_id=producer.identity.execution_id,
            )
        self.assertEqual(caught.exception.code, "reproduction.promotion.conflict")
        self.assertEqual(pyrun.read_bytes(), original)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(load_inspection(fixture.log).run, before.run)
        with open_work_job(run_root) as job:
            job.request_run_stop(RunStopRequest(WHEN))
            job.acknowledge_run_stop()
            job.finish_run_stop(RunStopCompletion(WHEN))
        promoted = promote_execution(
            fixture.log,
            run_id=accepted.run_id,
            cid=producer.identity.cid,
            execution_id=producer.identity.execution_id,
        )
        self.assertEqual(
            set(promoted.outputs),
            {name for name, _ in producer.execution.recipe.outputs},
        )
        self.assertEqual(load_inspection(fixture.log).run, before.run)

    def test_native_promotion_report_failure_rolls_back_outputs_and_pyrun(self):
        fixture, accepted, before = self.run_public()
        work = next(
            work
            for work in accepted.plan.commands
            if work.identity.cid == "independent"
        )
        pyrun = Path(work.entry_root) / "pyrun.json"
        original = pyrun.read_bytes()
        outputs = {
            Path(work.entry_root) / name: (Path(work.entry_root) / name).read_bytes()
            for name, _ in work.execution.recipe.outputs
        }
        with mock.patch(
            "log_commands.reproduction_promotion._report_candidates",
            side_effect=ActionError("test.report.failed", "cut"),
        ):
            with self.assertRaises(ActionError):
                promote_execution(
                    fixture.log,
                    run_id=accepted.run_id,
                    cid=work.identity.cid,
                    execution_id=work.identity.execution_id,
                )
        self.assertEqual(pyrun.read_bytes(), original)
        self.assertEqual({path: path.read_bytes() for path in outputs}, outputs)
        self.assertEqual(load_inspection(fixture.log).run, before.run)

    def test_public_prestart_stop_resumes_the_same_acceptance_without_planning(self):
        owner, fixture, workspace = self.fixture()
        shutil.rmtree(workspace.run_root)
        accepted = []

        def supervise(log, root, fds, *, mode):
            with open_work_job(root) as job:
                when = jobs._utc_now()
                job.replace_run_owner(RunOwner(os.getpid(), "running", when, when))
                accepted.append(job.accepted)
                if mode == jobs.FRESH_RUN:
                    from log_commands.reproduction_job_control import (
                        RunStopRequest,
                    )

                    job.request_run_stop(RunStopRequest(when))
            jobs.supervise_reproduction(
                log,
                root,
                mode=mode,
                inherited_locks=fds,
                confinement=TestConfinement(),
            )

        with mock.patch.object(jobs, "_spawn_supervisor", side_effect=supervise):
            launch = jobs.launch_reproduction(
                fixture.log, RunTarget(), RunSettings(jobs=2)
            )
            self.assertEqual(
                self.dispatch(fixture, "stop", "--run-id", launch.run_id), launch.run_id
            )
            with mock.patch.object(
                jobs, "_prepare_work", side_effect=AssertionError("replan")
            ):
                self.assertEqual(
                    self.dispatch(fixture, "resume", "--run-id", launch.run_id),
                    launch.run_id,
                )
        self.assertEqual(len(accepted), 2)
        self.assertEqual(accepted[0], accepted[1])
        self.assertEqual(
            jobs.reproduction_status(fixture.log, launch.run_id)["status"], "complete"
        )

    def test_public_failed_publication_resumes_without_workspace_or_execution(self):
        owner, fixture, workspace = self.fixture()
        shutil.rmtree(workspace.run_root)
        errors = []

        def supervise(log, root, fds, *, mode):
            with open_work_job(root) as job:
                when = jobs._utc_now()
                job.replace_run_owner(RunOwner(os.getpid(), "running", when, when))
            try:
                jobs.supervise_reproduction(
                    log,
                    root,
                    mode=mode,
                    inherited_locks=fds,
                    confinement=TestConfinement(),
                )
            except ActionError as error:
                errors.append(error)

        with mock.patch.object(jobs, "_spawn_supervisor", side_effect=supervise):
            with mock.patch(
                "log_commands.reproduction_work_publication.materialize_saved_report_locked",
                side_effect=ActionError("test.report.failed", "cut"),
            ):
                launch = jobs.launch_reproduction(
                    fixture.log, RunTarget(), RunSettings(jobs=2)
                )
            self.assertEqual(len(errors), 1)
            status = jobs.reproduction_status(fixture.log, launch.run_id)
            self.assertEqual(status["status"], "failed")
            self.assertTrue(status["resumable"])
            saved = load_inspection(fixture.log).run
            with (
                mock.patch(
                    "log_commands.reproduction_work_supervision.execute_work_plan",
                    side_effect=AssertionError("execution"),
                ),
                mock.patch(
                    "log_commands.reproduction_work_supervision.open_current_workspace",
                    side_effect=AssertionError("workspace"),
                ),
            ):
                self.assertEqual(
                    self.dispatch(fixture, "resume", "--run-id", launch.run_id),
                    launch.run_id,
                )
        self.assertEqual(load_inspection(fixture.log).run, saved)
        self.assertEqual(
            jobs.reproduction_status(fixture.log, launch.run_id)["status"], "complete"
        )

    def test_racing_resumes_handoff_exactly_one_supervisor(self):
        owner, fixture, workspace = self.fixture()
        from log_commands.reproduction_job_control import (
            RunStopCompletion,
            RunStopRequest,
        )

        with open_work_job(workspace.run_root) as job:
            when = jobs._utc_now()
            job.request_run_stop(RunStopRequest(when))
            job.acknowledge_run_stop()
            job.replace_run_owner(RunOwner(os.getpid(), "stopped", when, when))
            job.finish_run_stop(RunStopCompletion(when))
        start = threading.Barrier(2)
        handed_off = threading.Event()
        release = threading.Event()
        successes, failures, children = [], [], []

        def spawn(log, root, fds, *, mode):
            with open_work_job(root) as job:
                when = jobs._utc_now()
                job.replace_run_owner(RunOwner(os.getpid(), "running", when, when))
            children.append(root)
            handed_off.set()
            self.assertTrue(release.wait(timeout=5))

        def resume():
            start.wait(timeout=5)
            try:
                successes.append(
                    jobs.resume_reproduction(fixture.log, workspace.run_id)
                )
            except Exception as error:
                failures.append(error)

        with mock.patch.object(jobs, "_spawn_supervisor", side_effect=spawn):
            threads = [threading.Thread(target=resume) for _ in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(handed_off.wait(timeout=5))
            release.set()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(successes, [workspace.run_id])
        self.assertEqual(len(failures), 1)
        self.assertEqual(children, [workspace.run_root])

    def test_public_corrupt_current_job_is_typed_and_nonmutating(self):
        owner, fixture, workspace = self.fixture()
        path = workspace.run_root / "state.sqlite"
        with open_work_job(workspace.run_root) as job:
            identity = next(
                work.identity
                for work in job.accepted.plan.commands
                if work.identity.cid == "producer"
            )
        path.write_bytes(b"not a SQLite job")
        before = path.read_bytes()
        for action in ("status", "stop", "resume", "promote"):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = dispatcher.main(
                    [
                        "reproduce",
                        action,
                        "--path",
                        str(fixture.log.root),
                        "--run-id",
                        workspace.run_id,
                    ]
                    + (
                        ["--cid", identity.cid, "--execution-id", identity.execution_id]
                        if action == "promote"
                        else []
                    )
                )
            self.assertEqual(result, 2)
            self.assertIn("reproduction.run.invalid", stderr.getvalue())
            self.assertEqual(path.read_bytes(), before)

    def test_public_duplicate_run_id_is_integrity_error_without_decoding_history(self):
        owner, fixture, workspace = self.fixture()
        duplicate = (
            workspace.run_root.parent.parent / "2099-01-01" / workspace.run_root.name
        )
        self.assertNotEqual(duplicate, workspace.run_root)
        duplicate.mkdir(parents=True)
        (duplicate / "status.json").write_text("not historical JSON")
        before = (workspace.run_root / "state.sqlite").read_bytes()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = dispatcher.main(
                [
                    "reproduce",
                    "status",
                    "--path",
                    str(fixture.log.root),
                    "--run-id",
                    workspace.run_id,
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("reproduction.run.integrity", stderr.getvalue())
        self.assertEqual((workspace.run_root / "state.sqlite").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
