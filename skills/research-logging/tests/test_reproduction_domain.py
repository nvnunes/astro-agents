"""Canonical identities and classifications independent of old projections."""

from __future__ import annotations

import unittest
from dataclasses import replace

from log_commands.reproduction_domain import (
    MAX_PROBLEM_BYTES,
    ArtifactOutcome,
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    NotComparedReason,
    ProblemStage,
    ReproductionProblem,
    SourceRef,
    WorkSelection,
    classify_artifact,
    classify_command,
)
from validation.domain import SourceLocation

EXECUTION_ID = "pyrun-exec/v2:" + "a" * 64


def problem(subject=None, **changes):
    """A complete synthetic diagnosis with a mechanically exact source location."""

    return ReproductionProblem(
        subject or ExecutionRef("e001", "producer", EXECUTION_ID),
        changes.pop("code", "script_unavailable"),
        changes.pop("stage", ProblemStage.PREPARE),
        changes.pop("explanation", "Recorded script is unavailable."),
        changes.pop("observed", {"path": "scripts/producer.py", "error": "ENOENT"}),
        **changes,
    )


class ReproductionDomainTests(unittest.TestCase):
    def test_compound_execution_identity_isolates_entries_and_cids(self):
        references = (
            ExecutionRef("e001", "first", EXECUTION_ID),
            ExecutionRef("e001", "second", EXECUTION_ID),
            ExecutionRef("e002", "first", EXECUTION_ID),
        )
        self.assertEqual(len(set(references)), 3)
        self.assertEqual(tuple(sorted(reversed(references))), references)
        for reference in references:
            self.assertEqual(ExecutionRef.from_dict(reference.as_dict()), reference)

    def test_identity_grammars_and_closed_decoders(self):
        for arguments in (
            ("bad", "cid", "a" * 64),
            ("e001", "!", "a" * 64),
            ("e001", "cid", "a" * 63),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ExecutionRef(*arguments)
        reference = ExecutionRef("e001", "producer", EXECUTION_ID).as_dict()
        for raw in ({**reference, "bucket": "failed"}, {"entry": "e001"}, None):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ExecutionRef.from_dict(raw)
        for path in ("data/out.txt", "<project>/data/out.txt", "/external/out.txt"):
            reference = ArtifactRef("e001", path)
            self.assertEqual(ArtifactRef.from_dict(reference.as_dict()), reference)
        for path in (
            ".",
            "/",
            "../out",
            "data/../out",
            "data//out",
            "data/./out",
            "data\\out",
            "//external/out",
            "data/out/",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ArtifactRef("e001", path)

    def test_problem_identity_uses_subject_and_mechanical_facts_not_wording(self):
        original = problem(locations=(SourceLocation("pyrun.json", 12),))
        reworded = replace(original, explanation="Different prose.", locations=())
        self.assertEqual(original.problem_id, reworded.problem_id)
        self.assertRegex(original.problem_id, r"\Aproblem-[0-9a-f]{64}\Z")
        for changed in (
            replace(original, subject=ExecutionRef("e002", "producer", EXECUTION_ID)),
            replace(original, subject=SourceRef("scripts/producer.py")),
            replace(original, code="script_changed"),
            replace(original, stage=ProblemStage.LAUNCH),
            replace(original, observed={"path": "scripts/other.py", "error": "ENOENT"}),
        ):
            self.assertNotEqual(original.problem_id, changed.problem_id)

    def test_shared_cause_references_do_not_copy_diagnosis_into_outputs(self):
        cause = problem(SourceRef("scripts/shared.py"))
        causes = {cause.problem_id: cause}
        outputs = {
            ArtifactRef("e001", f"data/out-{index}.txt"): (cause.problem_id,)
            for index in range(1000)
        }
        self.assertEqual(len(causes), 1)
        self.assertEqual(len(outputs), 1000)
        self.assertEqual(
            {identity for refs in outputs.values() for identity in refs},
            {cause.problem_id},
        )
        unrelated = problem(SourceRef("other/shared.py"))
        self.assertNotEqual(unrelated.problem_id, cause.problem_id)

    def test_observations_are_deeply_immutable_and_serialization_is_closed(self):
        observations = {"expected": {"shape": [2, 3]}, "actual": {"shape": [3, 2]}}
        cause = problem(observed=observations)
        observations["expected"]["shape"].append(9)
        self.assertEqual(cause.as_dict()["observed"]["expected"]["shape"], [2, 3])
        with self.assertRaises(TypeError):
            cause.observed["extra"] = True
        decoded = ReproductionProblem.from_dict(cause.as_dict())
        self.assertEqual(decoded.as_dict(), cause.as_dict())
        self.assertEqual(decoded.problem_id, cause.problem_id)
        for raw in (
            {**cause.as_dict(), "consumers": []},
            {**cause.as_dict(), "subject": {"kind": "unknown", "path": "x"}},
            {**cause.as_dict(), "stage": "guessed"},
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ReproductionProblem.from_dict(raw)

    def test_locations_are_deterministic_with_and_without_line(self):
        cause = problem(
            locations=(
                SourceLocation("pyrun.json", 12),
                SourceLocation("pyrun.json"),
                SourceLocation("pyrun.json", 12),
            )
        )
        self.assertEqual(
            cause.locations,
            (SourceLocation("pyrun.json"), SourceLocation("pyrun.json", 12)),
        )

    def test_invalid_or_excessive_diagnosis_fails_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "unknown mechanical"):
            problem(code="invented_reason")
        with self.assertRaisesRegex(ValueError, "unknown mechanical"):
            problem(code="reproduction.run.invalid")
        cyclic = {}
        cyclic["self"] = cyclic
        for raw in (
            {"bad": float("nan")},
            {"bad": object()},
            {"nested": {1: "coerced"}},
            cyclic,
        ):
            with self.subTest(raw=type(raw)), self.assertRaises(ValueError):
                problem(observed=raw)
        with self.assertRaisesRegex(ValueError, "byte bound"):
            problem(observed={"error": "é" * MAX_PROBLEM_BYTES})
        self.assertEqual(
            problem(observed={"error": "x" * 8000}).as_dict()["observed"],
            {"error": "x" * 8000},
        )

    def test_command_classification_exact_leaf_matrix(self):
        for selection, reason in (
            (WorkSelection.NOT_NEEDED, "not-needed"),
            (WorkSelection.PREVIOUS_FAILURE, "previous-failure"),
            (WorkSelection.PREVIOUS_BLOCK, "previous-block"),
        ):
            classification = classify_command(selection, None)
            self.assertEqual(
                (classification.status, classification.reason), ("not-run", reason)
            )
        classification = classify_command(WorkSelection.SKIPPED_BY_POLICY, None)
        self.assertEqual(
            (classification.status, classification.reason),
            ("skipped-by-policy", "skipped-by-policy"),
        )
        classification = classify_command(WorkSelection.RUN, CommandOutcome.SUCCEEDED)
        self.assertEqual(
            (classification.status, classification.reason), ("succeeded", None)
        )
        for selection, outcome, expected in (
            (WorkSelection.BLOCKED, None, "blocked"),
            (WorkSelection.RUN, CommandOutcome.BLOCKED, "blocked"),
            (WorkSelection.RUN, CommandOutcome.FAILED, "failed"),
        ):
            classification = classify_command(selection, outcome, problem())
            self.assertEqual(
                (classification.status, classification.reason),
                (expected, "script_unavailable"),
            )

    def test_impossible_command_result_combinations_fail_closed(self):
        for selection, outcome, cause in (
            (WorkSelection.RUN, None, None),
            (WorkSelection.RUN, CommandOutcome.FAILED, None),
            (WorkSelection.BLOCKED, None, None),
            (WorkSelection.BLOCKED, CommandOutcome.SUCCEEDED, problem()),
            (WorkSelection.NOT_NEEDED, CommandOutcome.FAILED, problem()),
            (WorkSelection.PREVIOUS_FAILURE, CommandOutcome.SUCCEEDED, None),
            (WorkSelection.SKIPPED_BY_POLICY, CommandOutcome.BLOCKED, problem()),
        ):
            with (
                self.subTest(selection=selection, outcome=outcome),
                self.assertRaises(ValueError),
            ):
                classify_command(selection, outcome, cause)

    def test_artifact_classification_does_not_reclassify_command(self):
        for outcome in (ArtifactOutcome.MATCHED, ArtifactOutcome.NOT_MATCHED):
            classification = classify_artifact(outcome, None)
            self.assertEqual(
                (classification.status, classification.reason), (outcome.value, None)
            )
        for reason in NotComparedReason:
            classification = classify_artifact(ArtifactOutcome.NOT_COMPARED, reason)
            self.assertEqual(
                (classification.status, classification.reason),
                ("not-compared", reason.value),
            )
        with self.assertRaises(ValueError):
            classify_artifact(ArtifactOutcome.NOT_COMPARED, None)
        with self.assertRaises(ValueError):
            classify_artifact(
                ArtifactOutcome.NOT_MATCHED, NotComparedReason.COMMAND_FAILED
            )


if __name__ == "__main__":
    unittest.main()
