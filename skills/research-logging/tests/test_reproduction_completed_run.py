"""Native publication uses accepted work and actual observations, never source."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    NotComparedReason,
    ReproductionDomainError,
    WorkSelection,
)
from log_commands.reproduction_work import ArtifactWork
from log_commands.reproduction_work_plan import ReproductionPlan
from research_log_data import Fingerprint
from test_reproduction_canonical_records import blocked_plan, mixed_run


def plan_from_run(run):
    admission = blocked_plan().admission
    selected = [
        next(work for work in run.commands if work.identity.cid == cid)
        for cid in ("success", "producer", "consumer")
    ]
    selected = [work for work in selected if work.selection is WorkSelection.RUN]
    scheduling = tuple(
        {
            "identity": work.identity.as_dict(),
            "order": index,
            "read_paths": [],
            "write_paths": [],
            "run_path": f"<run>/executions/{work.identity.cid}",
            "writable_paths": [],
        }
        for index, work in enumerate(selected, 1)
    )
    return ReproductionPlan(
        run.summary,
        run.target,
        run.settings,
        admission,
        run.commands,
        run.artifacts,
        tuple(
            problem
            for problem in run.problems
            if problem.problem_id
            in {identity for work in run.commands for identity in work.problem_ids}
        ),
        (),
        (),
        scheduling,
    )


def completion(run, **changes):
    return replace(
        RunCompletion(
            run.run_id,
            run.accepted_at,
            run.finished_at,
            run.command_results,
            run.artifact_results,
            run.problems,
        ),
        **changes,
    )


class CompletedRunTests(unittest.TestCase):
    def test_actual_mixed_facts_roundtrip_without_source_or_replanning(self):
        run = mixed_run()
        plan = plan_from_run(run)
        with (
            mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("read")),
            mock.patch("pathlib.Path.read_text", side_effect=AssertionError("read")),
            mock.patch("pathlib.Path.resolve", side_effect=AssertionError("resolve")),
        ):
            self.assertEqual(complete_saved_run(plan, completion(run)), run)

    def test_failed_and_blocked_artifacts_reference_commands_not_copied_causes(self):
        run = mixed_run()
        observed = tuple(
            result
            for result in run.artifact_results
            if result.outcome is not ArtifactOutcome.NOT_COMPARED
        )
        self.assertEqual(
            complete_saved_run(plan_from_run(run), completion(run, artifacts=observed)),
            run,
        )

    def test_runnable_artifact_missing_comparison_does_not_acquire_fake_match(self):
        run = mixed_run()
        with self.assertRaisesRegex(ReproductionDomainError, "runnable artifact"):
            complete_saved_run(plan_from_run(run), completion(run, artifacts=()))

    def test_runnable_command_missing_terminal_facts_prevents_publication(self):
        run = mixed_run()
        with self.assertRaisesRegex(ReproductionDomainError, "accepted selection"):
            complete_saved_run(plan_from_run(run), completion(run, commands=()))

    def test_ownerless_scope_boundary_is_not_a_failed_producer(self):
        run = mixed_run()
        plan = plan_from_run(run)
        for kind, reason in (
            ("cross_entry", NotComparedReason.COMMAND_NOT_RUN),
            ("outside_queue", NotComparedReason.COMMAND_NOT_RUN),
            ("non_automatic", NotComparedReason.SKIPPED_BY_POLICY),
        ):
            with self.subTest(kind=kind):
                artifact = ArtifactWork(
                    ArtifactRef("e001", "data/boundary.txt"),
                    None,
                    "/project/data/boundary.txt",
                    None,
                    boundary={"kind": kind},
                )
                scoped = replace(plan, artifacts=(*plan.artifacts, artifact))
                result = complete_saved_run(scoped, completion(run))
                observed = next(
                    item
                    for item in result.artifact_results
                    if item.identity == artifact.identity
                )
                self.assertIs(observed.outcome, ArtifactOutcome.NOT_COMPARED)
                self.assertIs(observed.not_compared_reason, reason)
                self.assertEqual(observed.problem_ids, ())
                self.assertEqual(result.problems, run.problems)

    def test_ownerless_artifact_without_scope_or_diagnosis_fails_closed(self):
        run = mixed_run()
        plan = plan_from_run(run)
        artifact = ArtifactWork(
            ArtifactRef("e001", "data/unknown.txt"),
            None,
            "/project/data/unknown.txt",
            None,
        )
        with self.assertRaisesRegex(ReproductionDomainError, "runnable artifact"):
            complete_saved_run(
                replace(plan, artifacts=(*plan.artifacts, artifact)), completion(run)
            )

    def test_duplicate_native_observations_are_not_silently_collapsed(self):
        run = mixed_run()
        with self.assertRaisesRegex(ReproductionDomainError, "duplicate observation"):
            complete_saved_run(
                plan_from_run(run),
                completion(
                    run, commands=(*run.command_results, run.command_results[0])
                ),
            )

    def test_accepted_reused_comparisons_keep_original_origin_and_facts(self):
        run = mixed_run()
        commands = tuple(
            replace(work, selection=WorkSelection.NOT_NEEDED)
            if work.identity.cid == "success"
            else work
            for work in run.commands
        )
        current = replace(
            run,
            commands=commands,
            command_results=tuple(
                result
                for result in run.command_results
                if result.identity.cid != "success"
            ),
        )
        plan = plan_from_run(current)
        reuse = tuple(
            result
            for result in run.artifact_results
            if result.outcome is not ArtifactOutcome.NOT_COMPARED
        )
        referenced = {identity for result in reuse for identity in result.problem_ids}
        plan = replace(
            plan,
            reusable_artifact_results=reuse,
            problems=(
                *plan.problems,
                *(
                    problem
                    for problem in run.problems
                    if problem.problem_id in referenced
                ),
            ),
        )
        result = complete_saved_run(
            plan, completion(current, run_id="reproduce-current", artifacts=())
        )
        for original in reuse:
            self.assertIn(original, result.artifact_results)
            self.assertEqual(original.origin_run_id, run.run_id)
            self.assertEqual(original.recorded_at, run.finished_at)
        for observed in result.artifact_results:
            if observed.outcome is ArtifactOutcome.NOT_COMPARED:
                self.assertEqual(observed.origin_run_id, "reproduce-current")
        forged = replace(reuse[0], origin_run_id="reproduce-current")
        with self.assertRaisesRegex(
            ReproductionDomainError, "overrides accepted reuse"
        ):
            complete_saved_run(
                plan,
                completion(current, run_id="reproduce-current", artifacts=(forged,)),
            )

    def test_confirmed_empty_target_is_a_real_zero_view_not_unavailable(self):
        plan = plan_from_run(mixed_run())
        admission = {**plan.admission, "executions": []}
        plan = replace(
            plan,
            commands=(),
            artifacts=(),
            problems=(),
            scheduling=(),
            admission=admission,
        )
        facts = RunCompletion(
            "reproduce-empty", "2026-09-15T12:00:00Z", "2026-09-15T12:00:00Z", (), ()
        )
        result = complete_saved_run(plan, facts)
        self.assertEqual(result.commands, ())
        self.assertEqual(result.artifacts, ())
        self.assertEqual(result.artifact_results, ())
        self.assertEqual(result.problems, ())

    def test_reuse_preserves_original_baseline_and_evidence_qualification(self):
        run = mixed_run()
        plan = plan_from_run(run)
        original = next(
            result
            for result in run.artifact_results
            if result.outcome is ArtifactOutcome.MATCHED
        )
        with self.assertRaisesRegex(ReproductionDomainError, "selected work"):
            replace(plan, reusable_artifact_results=(original,))
        plan = replace(
            plan,
            commands=tuple(
                replace(work, selection=WorkSelection.NOT_NEEDED)
                if work.identity == plan.artifact(original.identity).producer
                else work
                for work in plan.commands
            ),
            scheduling=tuple(
                {**row, "order": order}
                for order, row in enumerate(
                    (
                        row
                        for row in plan.scheduling
                        if row["identity"]
                        != plan.artifact(original.identity).producer.as_dict()
                    ),
                    1,
                )
            ),
        )
        replace(plan, reusable_artifact_results=(original,))
        with self.assertRaisesRegex(ReproductionDomainError, "different baseline"):
            replace(
                plan,
                reusable_artifact_results=(
                    replace(original, expected=Fingerprint("sha256", "b" * 64)),
                ),
            )
        with self.assertRaisesRegex(ReproductionDomainError, "different evidence"):
            replace(
                plan,
                reusable_artifact_results=(
                    replace(
                        original,
                        profile="evidence",
                        definition_identity="b" * 64,
                        evidence=(
                            {
                                "definition": "c" * 64,
                                "id": "retained-value",
                                "matched": True,
                                "expected": [{"number": "1"}],
                                "regenerated": [{"number": "1"}],
                                "tolerance": None,
                            },
                        ),
                    ),
                ),
            )

    def test_current_observation_cannot_forge_a_prior_run_origin(self):
        run = mixed_run()
        observed = replace(run.artifact_results[0], origin_run_id="reproduce-other")
        facts = completion(run, artifacts=(observed, *run.artifact_results[1:]))
        with self.assertRaisesRegex(ReproductionDomainError, "different run origin"):
            complete_saved_run(plan_from_run(run), facts)
