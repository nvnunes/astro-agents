"""Exact comparisons retain artifact-owned diagnosis from the observation point."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands import reproduction_comparison as comparison
from log_commands import reproduction_planner as planner
from log_commands.reproduction_artifact_results import (
    ArtifactObservationContext,
    accepted_evidence_definition,
    compare_accepted_artifact,
)
from log_commands.reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    NotComparedReason,
    ReproductionProblem,
    WorkSelection,
)
from log_commands.reproduction_run import ArtifactResult
from log_commands.reproduction_saved_storage import PreparationHistory
from log_commands.reproduction_work import ArtifactWork
from reproduction_planning_test_support import _evidence_scoped_fixture, prepare_plan
from research_log_data import Fingerprint, observe_file_content
from test_reproduction_canonical_records import WHEN, command

RUN = "reproduce-2026-09-15-diagnosis"


def accepted_output(root, suffix, content):
    producer = command("build", outputs=(("data/output" + suffix, "file"),))
    entry_root = root / "entries/2026-09-15-e001-study"
    expected = entry_root / ("data/output" + suffix)
    expected.parent.mkdir(parents=True)
    expected.write_text(content, encoding="utf-8")
    baseline = Fingerprint("sha256", observe_file_content(expected)[0])
    execution = replace(
        producer.execution,
        observed=replace(
            producer.execution.observed, outputs=(("data/output" + suffix, baseline),)
        ),
    )
    producer = replace(
        producer,
        entry_root=entry_root.as_posix(),
        project_root=root.as_posix(),
        execution=execution,
    )
    work = ArtifactWork(
        ArtifactRef("e001", "data/output" + suffix),
        producer.identity,
        expected.as_posix(),
        baseline,
        output="data/output" + suffix,
    )
    return producer, work, expected


class ArtifactResultObservationTests(unittest.TestCase):
    def test_absent_accepted_definition_never_falls_back_to_authored_live_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _, _, _ = _evidence_scoped_fixture(Path(directory))
            plan = prepare_plan(fixture, entry)
            work = next(
                item for item in plan.artifacts if item.output == "data/output.txt"
            )
            producer = plan.command(work.producer)
            work = replace(work, definition_identity=None, evidence_records=())
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_text("stable\nruntime changed\n", encoding="utf-8")
            with (
                mock.patch(
                    "log_commands.reproduction_artifact_results.evidence_comparison_definition",
                    side_effect=AssertionError("unaccepted evidence rule"),
                ),
                mock.patch(
                    "validation.evidence.load_evidence_file",
                    side_effect=AssertionError("live evidence registry"),
                ),
            ):
                self.assertIsNone(accepted_evidence_definition(work, producer))
                result, problems = compare_accepted_artifact(
                    work,
                    producer,
                    regenerated,
                    context=ArtifactObservationContext(RUN, WHEN),
                )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_MATCHED)
            self.assertEqual(result.profile, "text")
            self.assertIsNone(result.definition_identity)
            self.assertEqual(result.evidence, ())
            self.assertEqual(problems[0].observed["difference"]["location"], "line 2")

    def test_changed_frozen_auxiliary_evidence_rejects_even_equal_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, output, _, _ = _evidence_scoped_fixture(Path(directory))
            with mock.patch.object(
                planner, "_canonical_plan", wraps=planner._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state, ordered, prepared = project.call_args.args[:3]
            plan = planner._canonical_plan(
                state, ordered, prepared, entry, PreparationHistory()
            )
            work = next(
                work for work in plan.artifacts if work.output == "data/output.txt"
            )
            producer = plan.command(work.producer)
            raw = entry.root / "data/raw.txt"
            resource = next(
                item
                for item in producer.data.inputs
                if item.canonical_target == str(raw)
            )
            context = (
                {
                    "entry": entry.id,
                    "record_id": "stable-output",
                    "resource": str(raw),
                    "kind": "file",
                    "selection": resource.identity.as_dict(),
                    "fingerprint": Fingerprint(
                        "sha256", observe_file_content(raw)[0]
                    ).as_dict(),
                    "comparisons": [work.definition_identity],
                },
            )
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_bytes(output.read_bytes())
            raw.write_text("changed auxiliary evidence\n", encoding="utf-8")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN, context),
            )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_COMPARED)
            self.assertIs(
                result.not_compared_reason, NotComparedReason.COMPARISON_FAILED
            )
            self.assertEqual(problems[0].code, "evidence_context_changed")

    def test_text_difference_retains_line_and_values_after_files_disappear(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, work, _ = accepted_output(
                Path(directory), ".txt", "same\nexpected\n"
            )
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_text("same\nactual\n", encoding="utf-8")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_MATCHED)
            self.assertEqual(
                problems[0].observed["difference"],
                {
                    "location": "line 2",
                    "expected": "expected\n",
                    "regenerated": "actual\n",
                },
            )
            payload = result.as_dict()
            diagnosis = problems[0].as_dict()
        with mock.patch(
            "pathlib.Path.read_bytes", side_effect=AssertionError("rerun/read")
        ):
            self.assertEqual(ArtifactResult.from_dict(payload), result)
            self.assertEqual(ReproductionProblem.from_dict(diagnosis), problems[0])

    def test_equal_output_has_no_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, work, _ = accepted_output(Path(directory), ".txt", "same\n")
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_text("same\n", encoding="utf-8")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(result.outcome, ArtifactOutcome.MATCHED)
            self.assertEqual(problems, ())

    def test_changed_retained_baseline_cannot_become_a_match(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, work, expected = accepted_output(
                Path(directory), ".txt", "recorded\n"
            )
            expected.write_text("changed\n", encoding="utf-8")
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_text("changed\n", encoding="utf-8")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_COMPARED)
            self.assertIs(
                result.not_compared_reason, NotComparedReason.COMPARISON_FAILED
            )
            self.assertEqual(problems[0].code, "baseline_changed")
            self.assertEqual(
                problems[0].observed["accepted_baseline"], work.baseline.as_dict()
            )
            self.assertNotEqual(result.expected, work.baseline)

    def test_caught_json_exception_type_and_message_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, work, _ = accepted_output(
                Path(directory), ".json", '{"valid": 1}'
            )
            regenerated = Path(directory) / "regenerated.json"
            regenerated.write_text('{"bad":', encoding="utf-8")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_COMPARED)
            self.assertEqual(problems[0].code, "comparator_error")
            self.assertEqual(problems[0].observed["error"]["type"], "JSONDecodeError")
            self.assertIn("Expecting value", problems[0].observed["error"]["message"])

    def test_actual_accepted_evidence_preserves_match_decisions_without_registry_reads(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _, _, _ = _evidence_scoped_fixture(Path(directory))
            with mock.patch.object(
                planner, "_canonical_plan", wraps=planner._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state, ordered, prepared = project.call_args.args[:3]
            plan = planner._canonical_plan(
                state, ordered, prepared, entry, PreparationHistory()
            )
            work = next(
                work for work in plan.artifacts if work.output == "data/output.txt"
            )
            producer = next(
                command
                for command in plan.commands
                if command.identity == work.producer
            )
            with (
                mock.patch(
                    "pathlib.Path.read_text",
                    side_effect=AssertionError("registry read"),
                ),
                mock.patch(
                    "pathlib.Path.resolve",
                    side_effect=AssertionError("source reinterpretation"),
                ),
                mock.patch(
                    "log_commands.reproduction_work.data_file_from_fields",
                    side_effect=AssertionError("reparsed accepted declaration"),
                ),
            ):
                definition = accepted_evidence_definition(work, producer)
            self.assertIs(producer.data, producer.data)
            self.assertEqual(definition.identity, work.definition_identity)
            regenerated = Path(directory) / "regenerated.txt"
            for content, outcome in (
                ("stable\nruntime changed\n", ArtifactOutcome.MATCHED),
                ("changed selected text\nruntime 1\n", ArtifactOutcome.NOT_MATCHED),
            ):
                with self.subTest(content=content):
                    regenerated.write_text(content, encoding="utf-8")
                    result, problems = compare_accepted_artifact(
                        work,
                        producer,
                        regenerated,
                        context=ArtifactObservationContext(RUN, WHEN),
                    )
                    self.assertIs(result.outcome, outcome)
                    self.assertEqual(result.profile, "evidence")
                    self.assertEqual(
                        result.definition_identity, work.definition_identity
                    )
                    self.assertNotEqual(
                        result.evidence[0]["definition"], result.definition_identity
                    )
                    self.assertEqual(
                        [record["id"] for record in result.evidence], ["stable-output"]
                    )
                    if problems:
                        self.assertEqual(
                            problems[0].observed["differing_evidence"],
                            ("stable-output",),
                        )
                    else:
                        self.assertTrue(result.evidence[0]["matched"])
            regenerated.write_bytes(Path(work.retained_path).read_bytes())
            equal, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(equal.outcome, ArtifactOutcome.MATCHED)
            self.assertEqual(equal.profile, "text")
            self.assertEqual(equal.definition_identity, work.definition_identity)
            self.assertEqual(equal.evidence, ())
            self.assertEqual(problems, ())
            current = replace(
                plan,
                commands=tuple(
                    replace(command, selection=WorkSelection.NOT_NEEDED)
                    for command in plan.commands
                ),
                scheduling=(),
                reusable_artifact_results=(equal,),
            )
            self.assertEqual(current.reusable_artifact_results, (equal,))
            with self.assertRaisesRegex(ValueError, "different evidence"):
                replace(
                    current,
                    reusable_artifact_results=(
                        replace(equal, definition_identity=None),
                    ),
                )

    def test_closed_profile_goldens_preserve_exact_existing_comparison_rules(self):
        cases = (
            (".txt", "a\nb\n", "a\nc\n", "changed", "line 2"),
            (".json", '{"x": [1, 2]}', '{"x": [1, 3]}', "changed", '$["x"][1]'),
            (".json", '{"x": 0.0}', '{"x": -0.0}', "changed", '$["x"]'),
            (".json", '{"x": 1}', '{"x": 1.0}', "changed", '$["x"]'),
            (".json", '{"x":1, "y":2}', '{"y":2, "x":1}', "matched", None),
            (".csv", "x,y\n1,true\n", "x,y\n1,false\n", "changed", "row 2"),
            (".csv", "x\n1\n", "x\n01\n", "changed", "row 2"),
            (".bin", "abc", "abd", "changed", "byte 2"),
        )
        for suffix, expected, regenerated, outcome, location in cases:
            with (
                self.subTest(suffix=suffix, expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                left, right = (
                    Path(directory) / ("expected" + suffix),
                    Path(directory) / ("actual" + suffix),
                )
                left.write_text(expected, encoding="utf-8")
                right.write_text(regenerated, encoding="utf-8")
                result = comparison.compare_artifacts(left, right)
                self.assertEqual(result.outcome, outcome)
                if location is not None:
                    self.assertEqual(
                        result.observed["difference"]["location"], location
                    )

    def test_native_preparation_freezes_completed_reuse_and_owned_mismatch_diagnosis(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _, _, _ = _evidence_scoped_fixture(Path(directory))
            with mock.patch.object(
                planner, "_canonical_plan", wraps=planner._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state, ordered, prepared = project.call_args.args[:3]
            accepted = planner._canonical_plan(
                state, ordered, prepared, entry, PreparationHistory()
            )
            work = next(
                work for work in accepted.artifacts if work.output == "data/output.txt"
            )
            producer = accepted.command(work.producer)
            regenerated = Path(directory) / "regenerated.txt"
            regenerated.write_text("changed selected text\nruntime 1\n")
            result, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertIs(result.outcome, ArtifactOutcome.NOT_MATCHED)
            history = PreparationHistory(
                artifacts={work.identity: (work, result)},
                artifact_problems={problem.problem_id: problem for problem in problems},
            )
            for key in state.command_selections:
                state.command_selections[key] = "not_needed"
            digests = dict(state.command_digests)
            with (
                mock.patch(
                    "pathlib.Path.read_text", side_effect=AssertionError("source read")
                ),
                mock.patch.object(
                    planner,
                    "observe_fingerprint",
                    side_effect=AssertionError("reobserve baseline"),
                ),
            ):
                current = planner._canonical_plan(state, (), prepared, entry, history)
            self.assertEqual(current.reusable_artifact_results, (result,))
            self.assertIs(current.reusable_artifact_results[0], result)
            self.assertIn(problems[0], current.problems)
            self.assertEqual(state.command_digests, digests)
            self.assertEqual(
                type(current).from_json(current.serialized().encode()), current
            )
            for changed in (
                replace(work, baseline=Fingerprint("sha256", "b" * 64)),
                replace(work, output="other-output.txt"),
                replace(work, definition_identity=None, evidence_records=()),
            ):
                with self.subTest(changed=changed):
                    self.assertFalse(
                        planner._comparison_reusable(changed, work, result)
                    )
            self.assertFalse(
                planner._comparison_reusable(
                    work, work, replace(result, definition_identity="b" * 64)
                )
            )

    def test_diagnostic_previews_remain_bounded_and_do_not_traverse_large_values(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, work, _ = accepted_output(
                Path(directory), ".json", json.dumps({"x": "a" * 100_000})
            )
            regenerated = Path(directory) / "regenerated.json"
            regenerated.write_text(json.dumps({"x": "b" * 100_000}), encoding="utf-8")
            _, problems = compare_accepted_artifact(
                work,
                producer,
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
            self.assertLess(len(json.dumps(problems[0].as_dict()).encode()), 16 * 1024)
            self.assertTrue(problems[0].observed["difference"]["expected"]["truncated"])
