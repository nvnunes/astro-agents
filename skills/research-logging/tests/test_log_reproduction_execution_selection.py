from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_accounting import project_command_selection
from log_commands.reproduction_contract import (
    PREEXECUTION_RESULT_SCHEMA,
    format_reproduction_plan_summary,
    valid_reproduction_target,
)
from log_commands.reproduction_jobs import (
    PREEXECUTION_RUN_SCHEMA,
    _accepted_record,
    _load_run,
    _plan_from_record,
    _resume_plan,
    _ResumeContext,
    dry_run_reproduction,
)
from log_commands.reproduction_planner import (
    ReproductionSelection,
    plan_reproduction,
    project_reproduction_command_details,
    project_reproduction_command_inventory,
)
from log_commands.reproduction_results import ReproductionResults
from test_log_reproduction_planning import _admission, _Fixture, _seed_command_results


class ExecutionSelectionTests(unittest.TestCase):
    def test_individual_report_has_complete_outputs_without_log_counts(self) -> None:
        from log_commands.reproduction_comparison import (
            ArtifactComparison,
            ExecutionComparison,
        )
        from log_commands.reproduction_reporting import reproduction_execution_report

        for repair in (False, True):
            with self.subTest(repair=repair):
                plan = self.plan(policy="recheck", verify_repair=repair)
                root = self.fixture.root / "reproduce-study-e001-reproduce-report"
                root.mkdir(exist_ok=True)
                (root / "staging.json").write_text("fixture")
                record = _accepted_record(
                    self.fixture.log,
                    plan,
                    "reproduce-report",
                    root,
                    accepted_at="2030-01-01T00:00:00Z",
                )
                record["checkpoints"] = [
                    {
                        "entry": "e001",
                        "execution_id": self.target[0],
                        "state": "succeeded",
                        "failure": None,
                    }
                ]
                record["state"]["status"] = "complete"
                comparison = ExecutionComparison(
                    "e001",
                    self.target[0],
                    (
                        ArtifactComparison(
                            "data/a.txt", "matched", None, None, None, None
                        ),
                        ArtifactComparison(
                            "data/b.txt", "changed", "content_changed", None, None, None
                        ),
                    ),
                    None,
                    True,
                )
                with (
                    mock.patch(
                        "log_commands.reproduction_jobs._find_run", return_value=root
                    ),
                    mock.patch(
                        "log_commands.reproduction_jobs._load_run", return_value=record
                    ),
                    mock.patch(
                        "log_commands.reproduction_reporting.open_existing_workspace"
                    ),
                    mock.patch(
                        "log_commands.reproduction_reporting.load_recorded_comparisons",
                        return_value=(comparison,),
                    ),
                ):
                    result = reproduction_execution_report(
                        self.fixture.log, "reproduce-report"
                    )
                    record["checkpoints"][0].update(
                        state="failed",
                        failure={
                            "code": "execution.failed",
                            "message": "script exited 1",
                        },
                    )
                    failed = reproduction_execution_report(
                        self.fixture.log, "reproduce-report"
                    )
                    self.assertIn(": failed", failed)
                    self.assertIn("execution.failed: script exited 1", failed)
                self.assertIn(": succeeded", result)
                self.assertIn("data/a.txt: matched", result)
                self.assertIn("data/b.txt: changed (content_changed)", result)
                self.assertEqual("repaired-source verification" in result, repair)
                self.assertNotIn("sibling", result)
                self.assertNotIn("Reproduction Summary", result)
                self.assertNotIn("Artifacts", result)

    def test_individual_no_work_reports_blockers_without_historical_counts(
        self,
    ) -> None:
        from log_commands.reproduction_queries import reproduction_reconciliation_text

        self.input.write_text("changed prerequisite")
        self.admission.update(_admission(self.fixture))
        for repair in (False, True):
            with self.subTest(repair=repair):
                plan = self.plan(policy="recheck", verify_repair=repair)
                text = reproduction_reconciliation_text(
                    self.fixture.log, plan, generated_at="2030-01-01T00:00:00Z"
                )
                self.assertIn("No command executed", text)
                self.assertIn("data/a.txt", text)
                self.assertIn("data/b.txt", text)
                self.assertIn("input_changed", text)
                self.assertNotIn("Reproduction Summary", text)
                self.assertNotIn("sibling", text)

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.fixture = _Fixture(Path(self.scratch.name))
        (self.fixture.root / "tmp").mkdir()
        self.entry = self.fixture.entry(1)
        self.other = self.fixture.entry(2)
        self.input = self.entry.root / "data" / "input.txt"
        self.input.write_text("input\n")
        self.outputs = [self.entry.root / "data" / name for name in ("a.txt", "b.txt")]
        for output in self.outputs:
            output.write_text("output\n")
        self.prerequisite = self.fixture.execution(
            self.entry, "producer", {}, {"input": self.input}
        )
        self.target = self.fixture.execution(
            self.entry,
            "target",
            {"input": self.input},
            {p.name: p for p in self.outputs},
        )
        sibling_path = self.entry.root / "data" / "sibling.txt"
        sibling_path.write_text("sibling\n")
        self.sibling = self.fixture.execution(
            self.entry, "sibling", {}, {"sibling": sibling_path}
        )
        self.fixture.write_pyrun(
            self.entry, [self.prerequisite, self.target, self.sibling]
        )
        self.fixture.write_data(
            self.entry,
            [self.fixture.item(self.entry, "input", self.input, origin=False)],
        )
        self.admission = _admission(self.fixture)
        self.patch = mock.patch(
            "log_commands.reproduction_planner._admit_validation",
            return_value=(self.admission, mock.sentinel.validation),
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def plan(
        self,
        *,
        policy="incremental",
        include_all=False,
        identity=None,
        verify_repair=False,
    ):
        return plan_reproduction(
            self.fixture.log,
            entry=self.entry,
            include_all=include_all,
            selection=ReproductionSelection(
                policy,
                execution_id=identity or self.target[0],
                verify_repair=verify_repair,
            ),
        )

    def test_exact_command_without_evidence_and_complete_outputs(self) -> None:
        plan = self.plan()
        self.assertEqual(
            plan.target,
            {"kind": "execution", "entry": "e001", "execution_id": self.target[0]},
        )
        self.assertEqual([x["execution_id"] for x in plan.executions], [self.target[0]])
        self.assertEqual(plan.executions[0]["outputs"], ["data/a.txt", "data/b.txt"])
        self.assertEqual(len(plan.cases), 2)
        self.assertEqual(plan.executions[0]["depends_on"], [])
        self.assertEqual(plan.boundaries[0]["kind"], "outside_queue")
        self.assertEqual(
            plan.boundaries[0]["producers"], [f"e001:{self.prerequisite[0]}"]
        )
        self.assertEqual(len(plan.source_snapshot["commands"]), 1)
        self.assertEqual(
            project_reproduction_command_inventory(self.fixture.log, plan.target).total,
            1,
        )
        self.assertEqual(
            len(project_reproduction_command_details(self.fixture.log, plan.target)), 1
        )
        self.assertEqual(project_command_selection(plan, None).total, 1)

    def test_unrelated_evidence_does_not_seed_siblings(self) -> None:
        self.fixture.write_data(
            self.entry,
            [self.fixture.item(self.entry, "input", self.input, origin=False)],
        )
        self.fixture.evidence(self.entry, "input")
        self.admission.update(_admission(self.fixture))
        plan = self.plan()
        self.assertEqual([x["execution_id"] for x in plan.executions], [self.target[0]])
        self.assertEqual(
            {x["artifact"] for x in plan.cases}, {"data/a.txt", "data/b.txt"}
        )

    def test_changed_and_missing_same_entry_prerequisites_block_recheck(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.input.unlink()
                else:
                    self.input.write_text("changed\n")
                self.admission.update(_admission(self.fixture))
                plan = self.plan(policy="recheck")
                self.assertFalse(plan.executions)
                self.assertEqual(
                    plan.source_snapshot["commands"][0]["selection"], "blocked"
                )
                self.assertEqual(len(plan.failures), 2)
                self.assertTrue(
                    all(
                        f"prerequisite=e001:{self.prerequisite[0]}" in x["dependencies"]
                        for x in plan.failures
                    )
                )
                self.assertEqual(
                    {x["execution_id"] for x in plan.cases}, {self.target[0]}
                )
                summary = format_reproduction_plan_summary(plan, recheck=True)
                self.assertIn("Zero executions", summary)
                self.assertIn(self.prerequisite[0], summary)
                self.assertTrue(all(x["reason"] in summary for x in plan.failures))

    def test_cross_entry_prerequisite_is_verified_without_scheduling(self) -> None:
        external = self.other.root / "data" / "input.txt"
        external.write_text("input\n")
        producer = self.fixture.execution(
            self.other, "external", {}, {"input": external}
        )
        self.fixture.write_pyrun(self.other, [producer])
        self.fixture.write_data(
            self.entry, [self.fixture.item(self.entry, "input", external, origin=False)]
        )
        self.admission.update(_admission(self.fixture))
        plan = self.plan()
        self.assertEqual(len(plan.executions), 1)
        self.assertEqual(plan.boundaries[0]["producers"], [f"e002:{producer[0]}"])
        external.write_text("changed\n")
        self.admission.update(_admission(self.fixture))
        self.assertFalse(self.plan().executions)

    def test_invalid_unknown_and_wrong_entry_ids_fail_before_launch(self) -> None:
        for identity in ("target.py", self.target[0][:-1], "pyrun-exec/v1:" + "0" * 64):
            with self.subTest(identity=identity), self.assertRaises(ActionError):
                self.plan(identity=identity)
        for entry in (None, self.other):
            with self.subTest(entry=entry), self.assertRaises(ActionError):
                plan_reproduction(
                    self.fixture.log,
                    entry=entry,
                    include_all=False,
                    selection=ReproductionSelection(execution_id=self.target[0]),
                )

    def test_incremental_recheck_and_policy_combinations(self) -> None:
        for automatic in (False, True):
            for required in (False, True):
                self.fixture.write_pyrun(
                    self.entry,
                    [
                        self.prerequisite,
                        (
                            self.target[0],
                            replace(
                                self.target[1],
                                auto_reproduce=automatic,
                                requires_reproduction=required,
                            ),
                        ),
                        self.sibling,
                    ],
                )
                self.admission.update(_admission(self.fixture))
                for policy in ("incremental", "recheck"):
                    for include_all in (False, True):
                        with self.subTest(
                            automatic=automatic,
                            required=required,
                            policy=policy,
                            include_all=include_all,
                        ):
                            plan = self.plan(policy=policy, include_all=include_all)
                            selection = plan.source_snapshot["commands"][0]["selection"]
                            expected = (
                                "policy"
                                if not automatic
                                and not include_all
                                and (required or policy == "recheck")
                                else "run"
                                if required or policy == "recheck"
                                else "not_needed"
                            )
                            self.assertEqual(selection, expected)
                            self.assertEqual(
                                len(plan.executions), int(expected == "run")
                            )
                            self.assertEqual(
                                project_command_selection(plan, None).total, 1
                            )

    def test_unchanged_failed_target_requires_recheck(self) -> None:
        plan = self.plan()
        _seed_command_results(self.fixture, plan, disposition="failed")
        self.assertEqual(
            self.plan().source_snapshot["commands"][0]["selection"], "unchanged"
        )
        self.assertEqual(len(self.plan(policy="recheck").executions), 1)

    def test_dry_run_is_write_free_and_summary_names_complete_scope(self) -> None:
        before = {
            p.relative_to(self.fixture.root): p.read_bytes()
            for p in self.fixture.root.rglob("*")
            if p.is_file()
        }
        with mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"):
            plan = dry_run_reproduction(
                self.fixture.log,
                entry=self.entry.id,
                include_all=False,
                selection=ReproductionSelection("recheck", execution_id=self.target[0]),
            )
        after = {
            p.relative_to(self.fixture.root): p.read_bytes()
            for p in self.fixture.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        summary = format_reproduction_plan_summary(plan, recheck=True)
        for value in (
            self.target[0],
            "scripts/target.py",
            "data/a.txt",
            "data/b.txt",
            "Selection reason: run",
            self.prerequisite[0],
        ):
            self.assertIn(value, summary)
        self.assertEqual(json.loads(plan.serialized())["target"], dict(plan.target))

    def test_resume_replans_only_immutable_target_and_rejects_queue_widening(
        self,
    ) -> None:
        plan = self.plan()
        run_root = (
            self.fixture.root
            / "tmp/reproduction/2030-01-01/reproduce-study-e001-reproduce-fixture"
        )
        run_root.mkdir(parents=True)
        record = _accepted_record(
            self.fixture.log,
            plan,
            "reproduce-fixture",
            run_root,
            accepted_at="2030-01-01T00:00:00Z",
        )
        context = _ResumeContext(run_root, record, record["state"], False, True, "e001")
        resumed = _resume_plan(self.fixture.log, context)
        self.assertEqual(resumed.target, plan.target)
        self.assertEqual(
            [x["execution_id"] for x in resumed.executions], [self.target[0]]
        )
        path = run_root / "run.json"
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        self.assertEqual(_plan_from_record(_load_run(path)).target, plan.target)
        record["queue"].append(
            {"entry": "e001", "execution_id": self.sibling[0], "queued": True}
        )
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        with self.assertRaises(ActionError):
            _load_run(path)
        with self.assertRaises(ActionError):
            _resume_plan(self.fixture.log, context)

    def test_old_entry_and_log_run_targets_and_results_remain_readable(self) -> None:
        for target in (
            {"kind": "entry", "entry": "e001"},
            {"kind": "log", "entry": None},
        ):
            plan = replace(self.plan(), target=target)
            suffix = "-e001" if target["kind"] == "entry" else ""
            run_root = (
                self.fixture.root
                / "tmp/reproduction/2030-01-01"
                / f"reproduce-study{suffix}-reproduce-fixture"
            )
            run_root.mkdir(parents=True, exist_ok=True)
            record = _accepted_record(
                self.fixture.log,
                plan,
                "reproduce-fixture",
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
            record["schema"] = PREEXECUTION_RUN_SCHEMA
            path = run_root / "run.json"
            path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
            self.assertEqual(_plan_from_record(_load_run(path)).target, target)
        results = ReproductionResults("docs/study.md", "2030-01-01T00:00:00Z", (), ())
        old = results.as_dict()
        old["schema"] = PREEXECUTION_RESULT_SCHEMA
        self.assertEqual(
            ReproductionResults.from_json(
                json.dumps(old, sort_keys=True, indent=2) + "\n"
            ),
            results,
        )
        self.assertFalse(
            valid_reproduction_target({**plan.target, "execution_id": "short"})
        )

    def test_targeted_publication_preserves_unrelated_results_and_exposes_outputs(
        self,
    ) -> None:
        from log_commands.reproduction_publication import (
            CompletedPublication,
            publish_completed_reproduction,
        )
        from log_commands.reproduction_queries import (
            show_reproduction_artifact,
            show_reproduction_command,
        )
        from log_commands.reproduction_results import (
            ArtifactResult,
            load_reproduction_results,
        )

        sibling_plan = self.plan(identity=self.sibling[0])
        _seed_command_results(self.fixture, sibling_plan, disposition="failed")
        result_path = self.fixture.log_root / ".cache/reproduction/results.json"
        previous = load_reproduction_results(result_path)
        unrelated = ArtifactResult(
            "e001",
            "data/sibling.txt",
            self.sibling[0],
            "failed",
            "validation_blocked",
            "2026-09-06T00:01:00Z",
            "reproduce-20260906t000000z-seed",
            None,
        )
        previous = replace(previous, artifacts=(unrelated,))
        result_path.write_text(previous.serialized())
        self.input.write_text("changed\n")
        self.admission.update(_admission(self.fixture))
        plan = self.plan()
        run_id = "reproduce-20300101t000000z-targeted"
        run_root = (
            self.fixture.root
            / "tmp/reproduction/2030-01-01"
            / f"reproduce-study-e001-{run_id}"
        )
        run_root.mkdir(parents=True)
        published = publish_completed_reproduction(
            self.fixture.log,
            CompletedPublication(
                plan,
                (),
                run_id,
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:01:00Z",
                run_root,
            ),
        )
        self.assertIn(unrelated, published.results.artifacts)
        self.assertIn(previous.commands[0], published.results.commands)
        self.assertEqual(len(published.results.commands), 2)
        self.assertEqual(len(published.results.artifacts), 3)
        self.assertEqual(published.results.runs[-1].target, plan.target)
        artifact = show_reproduction_artifact(
            self.fixture.log, entry="e001", artifact="data/a.txt"
        )
        self.assertIn(run_id, json.dumps(artifact))
        command = show_reproduction_command(
            self.fixture.log, entry="e001", execution_id=self.target[0], run_id=run_id
        )
        self.assertIn("blocked", json.dumps(command))
        self.assertIn(self.target[0], published.report)

        from contextlib import redirect_stdout
        from io import StringIO

        from log_commands.dispatcher import main

        for command in (
            ["commands", "list", "--entry", "e001"],
            [
                "commands",
                "show",
                "--entry",
                "e001",
                "--execution-id",
                self.target[0],
                "--run-id",
                run_id,
            ],
            ["artifacts", "show", "--entry", "e001", "--artifact", "data/a.txt"],
        ):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["reproduce", *command, "--path", str(self.fixture.log_root)]),
                    0,
                )
            self.assertIn(self.target[0], output.getvalue())

    def test_repair_guide_preview_launch_status_and_resume_commands(self) -> None:
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from log_commands.dispatcher import main
        from log_commands.reproduction_jobs import _find_run

        self.enterContext(
            mock.patch("log_commands.reproduction_jobs._reconcile_lost_supervisor")
        )

        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        self.admission.update(_admission(self.fixture))
        recorded = (self.entry.root / "pyrun.json").read_bytes()
        args = [
            "reproduce",
            "--path",
            str(self.fixture.log_root),
            "--entry",
            "e001",
            "--execution-id",
            self.target[0],
            "--recheck",
            "--verify-repair",
        ]
        before = {
            p.relative_to(self.fixture.root): p.read_bytes()
            for p in self.fixture.root.rglob("*")
            if p.is_file()
        }
        output = StringIO()
        with (
            redirect_stdout(output),
            mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
        ):
            self.assertEqual(main([*args, "--dry-run", "--summary"]), 0)
        after = {
            p.relative_to(self.fixture.root): p.read_bytes()
            for p in self.fixture.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertIn(self.target[0], output.getvalue())
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main([*args, "--summary"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(
                [
                    "reproduce",
                    "--path",
                    str(self.fixture.log_root),
                    "--execution-id",
                    self.target[0],
                ]
            )
        output = StringIO()
        with (
            redirect_stdout(output),
            mock.patch("log_commands.reproduction_jobs._spawn_supervisor"),
        ):
            self.assertEqual(main(args), 0)
        run_id = output.getvalue().strip()
        run_root = _find_run(self.fixture.log, run_id)
        path = run_root / "run.json"
        record = _load_run(path)
        self.assertEqual(record["target"], self.plan(policy="recheck").target)
        status_output = StringIO()
        with redirect_stdout(status_output):
            self.assertEqual(
                main(
                    [
                        "reproduce",
                        "status",
                        "--path",
                        str(self.fixture.log_root),
                        "--run-id",
                        run_id,
                    ]
                ),
                0,
            )
        report_output = StringIO()
        with redirect_stdout(report_output):
            self.assertEqual(
                main(
                    [
                        "reproduce",
                        "report",
                        "--path",
                        str(self.fixture.log_root),
                        "--run-id",
                        run_id,
                    ]
                ),
                0,
            )
        self.assertEqual(report_output.getvalue(), status_output.getvalue())
        self.assertIn("data/a.txt: not compared", report_output.getvalue())
        self.assertIn("data/b.txt: not compared", report_output.getvalue())
        self.assertNotIn("Reproduction Summary", report_output.getvalue())
        record["state"].update({"phase": None, "status": "stopped"})
        record["timestamps"]["stopped_at"] = record["timestamps"]["updated_at"]
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        with (
            redirect_stdout(StringIO()),
            mock.patch("log_commands.reproduction_jobs._spawn_supervisor"),
        ):
            self.assertEqual(
                main(
                    [
                        "reproduce",
                        "resume",
                        "--path",
                        str(self.fixture.log_root),
                        "--run-id",
                        run_id,
                    ]
                ),
                0,
            )
        resumed = _load_run(path)
        self.assertEqual(resumed["target"], record["target"])
        self.assertTrue(resumed["source_snapshot"]["repair_verification"])
        self.assertEqual((self.entry.root / "pyrun.json").read_bytes(), recorded)
        self.assertEqual(len(resumed["queue"]), 1)
        self.assertEqual(resumed["queue"][0]["execution_id"], self.target[0])

    def test_repaired_script_and_known_code_need_explicit_verification(self) -> None:
        from log_commands.reproduction_planner import verify_reproduction_snapshot
        from test_log_reproduction_planning import _fingerprint

        helper = self.entry.root / "scripts/helper.py"
        helper.write_text("# recorded helper\n")
        target = replace(
            self.target[1],
            observed=replace(
                self.target[1].observed,
                code=(("scripts/helper.py", _fingerprint(helper)),),
            ),
        )
        self.fixture.write_pyrun(
            self.entry, [self.prerequisite, (self.target[0], target), self.sibling]
        )
        recorded = (self.entry.root / "pyrun.json").read_bytes()
        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        helper.write_text("# repaired helper\n")
        self.admission.update(_admission(self.fixture))
        ordinary = self.plan(policy="recheck")
        self.assertFalse(ordinary.executions)
        repaired = self.plan(policy="recheck", verify_repair=True)
        self.assertEqual(
            [item["execution_id"] for item in repaired.executions], [self.target[0]]
        )
        sources = [
            item
            for item in repaired.source_snapshot["materials"]
            if "recorded_fingerprint" in item
        ]
        self.assertEqual(len(sources), 2)
        self.assertTrue(
            all(item["fingerprint"] != item["recorded_fingerprint"] for item in sources)
        )
        self.assertTrue(repaired.source_snapshot["repair_verification"])
        self.assertEqual((self.entry.root / "pyrun.json").read_bytes(), recorded)
        summary = format_reproduction_plan_summary(repaired, recheck=True)
        self.assertIn("repaired-source verification", summary)
        self.assertIn("scripts/target.py", summary)
        self.assertIn("scripts/helper.py", summary)
        helper.write_text("# changed after acceptance\n")
        with self.assertRaises(ActionError):
            verify_reproduction_snapshot(self.fixture.log, repaired)

    def test_repair_verification_does_not_bypass_prerequisites_or_baselines(
        self,
    ) -> None:
        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        self.input.write_text("changed prerequisite\n")
        self.admission.update(_admission(self.fixture))
        blocked = self.plan(policy="recheck", verify_repair=True)
        self.assertFalse(blocked.executions)
        self.assertEqual(
            {x["reason"] for x in blocked.failures}, {"direct_input_changed"}
        )
        self.input.write_text("input\n")
        self.outputs[0].write_text("changed baseline\n")
        self.admission.update(_admission(self.fixture))
        blocked = self.plan(policy="recheck", verify_repair=True)
        self.assertFalse(blocked.executions)
        self.assertEqual({x["reason"] for x in blocked.failures}, {"baseline_changed"})

    def test_repair_verification_keeps_policy_and_requires_exact_scope(self) -> None:
        self.fixture.write_pyrun(
            self.entry,
            [
                self.prerequisite,
                (self.target[0], replace(self.target[1], auto_reproduce=False)),
                self.sibling,
            ],
        )
        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        self.admission.update(_admission(self.fixture))
        self.assertFalse(self.plan(policy="recheck", verify_repair=True).executions)
        self.assertEqual(
            len(
                self.plan(
                    policy="recheck", verify_repair=True, include_all=True
                ).executions
            ),
            1,
        )
        with self.assertRaises(ActionError):
            self.plan(verify_repair=True)
        with self.assertRaises(ActionError):
            plan_reproduction(
                self.fixture.log,
                entry=self.entry,
                include_all=False,
                selection=ReproductionSelection("recheck", verify_repair=True),
            )

    def test_repair_verification_snapshot_survives_resume_and_rejects_promotion(
        self,
    ) -> None:
        from log_commands.reproduction_contract import is_repair_verification
        from log_commands.reproduction_planner import verify_reproduction_snapshot
        from log_commands.reproduction_promotion import promote_execution

        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        self.admission.update(_admission(self.fixture))
        plan = self.plan(policy="recheck", verify_repair=True)
        run_root = (
            self.fixture.root
            / "tmp/reproduction/2030-01-01/reproduce-study-e001-reproduce-repair"
        )
        run_root.mkdir(parents=True)
        record = _accepted_record(
            self.fixture.log,
            plan,
            "reproduce-repair",
            run_root,
            accepted_at="2030-01-01T00:00:00Z",
        )
        path = run_root / "run.json"
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        loaded = _plan_from_record(_load_run(path))
        self.assertTrue(is_repair_verification(loaded))
        context = _ResumeContext(run_root, record, record["state"], False, True, "e001")
        resumed = _resume_plan(self.fixture.log, context)
        self.assertTrue(is_repair_verification(resumed))
        self.assertEqual(resumed.target, plan.target)
        self.assertEqual(len(resumed.executions), 1)
        with self.assertRaises(ActionError) as caught:
            promote_execution(
                self.fixture.log, run_id="reproduce-repair", execution_id=self.target[0]
            )
        self.assertEqual(
            caught.exception.code, "reproduction.promotion.repair_verification"
        )
        (self.entry.root / "scripts/target.py").write_text("# second repair\n")
        with self.assertRaises(ActionError):
            verify_reproduction_snapshot(self.fixture.log, loaded)

    def test_repair_snapshot_rejects_changed_history_and_missing_source_material(
        self,
    ) -> None:
        from copy import deepcopy

        from log_commands.reproduction_planner import (
            verify_reproduction_runtime_snapshot,
        )

        (self.entry.root / "scripts/target.py").write_text("# repaired script\n")
        self.admission.update(_admission(self.fixture))
        plan = self.plan(policy="recheck", verify_repair=True)
        snapshot = deepcopy(dict(plan.source_snapshot))
        source = next(
            item for item in snapshot["materials"] if item["role"] == "script"
        )
        source["recorded_fingerprint"] = source["fingerprint"]
        with self.assertRaises(ActionError):
            verify_reproduction_runtime_snapshot(
                self.fixture.log, replace(plan, source_snapshot=snapshot)
            )
        snapshot = deepcopy(dict(plan.source_snapshot))
        snapshot["materials"] = [
            item for item in snapshot["materials"] if item["role"] != "script"
        ]
        with self.assertRaises(ActionError):
            verify_reproduction_runtime_snapshot(
                self.fixture.log, replace(plan, source_snapshot=snapshot)
            )
        snapshot = {
            **plan.source_snapshot,
            "schema": "research-log-reproduction-source-snapshot/8",
        }
        with self.assertRaises(ActionError):
            verify_reproduction_runtime_snapshot(
                self.fixture.log, replace(plan, source_snapshot=snapshot)
            )

    def test_repair_verification_still_requires_source_and_validation(self) -> None:
        (self.entry.root / "scripts/target.py").unlink()
        self.admission.update(_admission(self.fixture))
        plan = self.plan(policy="recheck", verify_repair=True)
        self.assertFalse(plan.executions)
        self.assertEqual(
            {item["reason"] for item in plan.failures}, {"script_unavailable"}
        )
        with mock.patch(
            "log_commands.reproduction_planner._admit_validation",
            side_effect=ActionError(
                "reproduction.validation.unavailable", "fixture validation unavailable"
            ),
        ):
            with self.assertRaises(ActionError) as caught:
                self.plan(policy="recheck", verify_repair=True)
        self.assertEqual(caught.exception.code, "reproduction.validation.unavailable")
