"""Typed synthetic records test canonical packaging, not engine behavior parity."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest import mock

from log_commands.reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    NotComparedReason,
    ProblemStage,
    ReproductionProblem,
    SourceRef,
    WorkSelection,
)
from log_commands.reproduction_run import ArtifactResult, CommandResult
from log_commands.reproduction_saved_run import RunSettings, RunTarget, SavedRun
from log_commands.reproduction_summary import plan_selection_counts, saved_counts
from log_commands.reproduction_work import ArtifactWork, CommandWork
from log_commands.reproduction_work_plan import ReproductionPlan
from research_log_data import Fingerprint
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_RUNNER,
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    execution_id,
)

WHEN = "2026-09-15T12:00:00Z"
FINGERPRINT = Fingerprint("sha256", "a" * 64)


def command(
    cid,
    selection=WorkSelection.RUN,
    *,
    entry="e001",
    dependencies=(),
    problems=(),
    outputs=None,
):
    recipe = ExecutionRecipe(
        f"scripts/{cid}.py", (), (), (), outputs or ((f"data/{cid}.txt", "file"),), ()
    )
    execution = PyrunExecution(
        selection is not WorkSelection.NOT_NEEDED,
        selection is not WorkSelection.SKIPPED_BY_POLICY,
        None,
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        ObservedExecution(
            FINGERPRINT,
            (),
            Fingerprint("python-effective-code-sha256-v1", "b" * 64),
            tuple(
                (
                    name,
                    FINGERPRINT
                    if kind == "file"
                    else Fingerprint("directory-sha256-v1", "a" * 64),
                )
                for name, kind in recipe.outputs
            ),
        ),
    )
    return CommandWork(
        ExecutionRef(entry, cid, execution_id(recipe)),
        execution,
        f"/project/docs/study/entries/2026-09-15-{entry}-study",
        "/project",
        None,
        selection,
        "a" * 64,
        dependencies,
        problems,
    )


def attempted(work, outcome=CommandOutcome.SUCCEEDED, problems=()):
    return CommandResult(
        work.identity,
        outcome,
        WHEN,
        WHEN,
        ("python", work.execution.recipe.script),
        f"tmp/run/{work.identity.cid}",
        "tmp/run/stdout.log",
        "tmp/run/stderr.log",
        {},
        problems,
    )


def mixed_run():
    """Seven distinct leaves, one failed producer, and retained unequal output."""

    not_needed = command("current", WorkSelection.NOT_NEEDED)
    previous_failure = command("prior-failed")
    prior_error = ReproductionProblem(
        previous_failure.identity,
        "execution_failed",
        ProblemStage.EXECUTE,
        "Earlier execution exited with status 1.",
        {"returncode": 1},
    )
    previous_failure = replace(
        previous_failure,
        selection=WorkSelection.PREVIOUS_FAILURE,
        problem_ids=(prior_error.problem_id,),
    )
    previous_block = command("prior-blocked")
    prior_block = ReproductionProblem(
        previous_block.identity,
        "effective_code_unavailable",
        ProblemStage.PREPARE,
        "Earlier required script was unavailable.",
        {"path": "scripts/prior-blocked.py", "error": "ENOENT"},
    )
    previous_block = replace(
        previous_block,
        selection=WorkSelection.PREVIOUS_BLOCK,
        problem_ids=(prior_block.problem_id,),
    )
    policy = command("manual", WorkSelection.SKIPPED_BY_POLICY)
    success = command(
        "success", outputs=(("data/equal.txt", "file"), ("data/unequal.txt", "file"))
    )
    failure = command("producer")
    cause = ReproductionProblem(
        failure.identity,
        "execution_failed",
        ProblemStage.EXECUTE,
        "Producer exited with status 2.",
        {"returncode": 2},
    )
    blocked = command("consumer", dependencies=(failure.identity,))
    works = (
        not_needed,
        previous_failure,
        previous_block,
        policy,
        success,
        failure,
        blocked,
    )
    results = (
        attempted(success),
        attempted(failure, CommandOutcome.FAILED, (cause.problem_id,)),
        CommandResult(
            blocked.identity,
            CommandOutcome.BLOCKED,
            None,
            None,
            (),
            None,
            None,
            None,
            {},
            blocked_by=(failure.identity,),
        ),
    )
    equal = ArtifactWork(
        ArtifactRef("e001", "data/equal.txt"),
        success.identity,
        "data/equal.txt",
        FINGERPRINT,
        output="data/equal.txt",
    )
    unequal = ArtifactWork(
        ArtifactRef("e001", "data/unequal.txt"),
        success.identity,
        "data/unequal.txt",
        FINGERPRINT,
        output="data/unequal.txt",
    )
    difference = ReproductionProblem(
        unequal.identity,
        "content_changed",
        ProblemStage.COMPARE,
        "Line 3 differs.",
        {"line": 3, "expected": "2", "actual": "3"},
    )
    failed_output = ArtifactWork(
        ArtifactRef("e001", "data/producer.txt"),
        failure.identity,
        "data/producer.txt",
        FINGERPRINT,
        output="data/producer.txt",
    )
    blocked_output = ArtifactWork(
        ArtifactRef("e001", "data/consumer.txt"),
        blocked.identity,
        "data/consumer.txt",
        FINGERPRINT,
        output="data/consumer.txt",
    )
    observations = (
        ArtifactResult(
            equal.identity,
            ArtifactOutcome.MATCHED,
            WHEN,
            "reproduce-mixed",
            "tmp/run/equal.txt",
            "text",
            FINGERPRINT,
            FINGERPRINT,
        ),
        ArtifactResult(
            unequal.identity,
            ArtifactOutcome.NOT_MATCHED,
            WHEN,
            "reproduce-mixed",
            "tmp/run/unequal.txt",
            "text",
            FINGERPRINT,
            Fingerprint("sha256", "b" * 64),
            problem_ids=(difference.problem_id,),
        ),
        ArtifactResult(
            failed_output.identity,
            ArtifactOutcome.NOT_COMPARED,
            WHEN,
            "reproduce-mixed",
            None,
            None,
            FINGERPRINT,
            None,
            NotComparedReason.COMMAND_FAILED,
        ),
        ArtifactResult(
            blocked_output.identity,
            ArtifactOutcome.NOT_COMPARED,
            WHEN,
            "reproduce-mixed",
            None,
            None,
            FINGERPRINT,
            None,
            NotComparedReason.COMMAND_BLOCKED,
        ),
    )
    return SavedRun(
        "/project/docs/study.md",
        "reproduce-mixed",
        RunTarget(),
        RunSettings(),
        WHEN,
        WHEN,
        works,
        (equal, unequal, failed_output, blocked_output),
        results,
        observations,
        (cause, difference, prior_error, prior_block),
    )


def blocked_plan():
    """A current preparation fixture with one missing script and ready work."""

    run = mixed_run()
    producer = next(work for work in run.commands if work.identity.cid == "producer")
    cause = ReproductionProblem(
        producer.identity,
        "effective_code_unavailable",
        ProblemStage.PREPARE,
        "Recorded producer script is unavailable.",
        {"path": "scripts/producer.py", "error": "ENOENT"},
    )
    commands = tuple(
        replace(work, selection=WorkSelection.BLOCKED, problem_ids=(cause.problem_id,))
        if work.identity.cid == "producer"
        else replace(work, selection=WorkSelection.BLOCKED)
        if work.identity.cid == "consumer"
        else work
        for work in run.commands
    )
    ready = next(work for work in commands if work.identity.cid == "success")
    admission = {
        "schema": "research-log-reproduction-admission/1",
        "validation_snapshot_id": "test-snapshot",
        "rules_version": "test-rules",
        "evaluated_at": WHEN,
        "executions": [
            {
                **work.identity.as_dict(),
                "disposition": "admitted",
                "blocking_finding_ids": [],
            }
            for work in commands
        ],
    }
    claims = {
        "identity": ready.identity.as_dict(),
        "order": 1,
        "read_paths": [],
        "write_paths": ["<run>/workspace/data/success.txt"],
        "run_path": "<run>/executions/success",
        "writable_paths": ["<run>/workspace/data/success.txt"],
    }
    return ReproductionPlan(
        run.summary,
        run.target,
        run.settings,
        admission,
        commands,
        run.artifacts,
        (
            cause,
            *(
                problem
                for problem in run.problems
                if isinstance(problem.subject, ExecutionRef)
                and problem.subject.cid.startswith("prior-")
            ),
        ),
        (),
        (),
        (claims,),
    )


class ReproductionCanonicalRecordTests(unittest.TestCase):
    def test_artifact_baseline_cause_may_block_only_its_recorded_producer(self):
        producer = command("baseline")
        identity = ArtifactRef("e001", "data/baseline.txt")
        cause = ReproductionProblem(
            identity,
            "baseline_changed",
            ProblemStage.PREPARE,
            "Accepted baseline changed.",
            {"path": "data/baseline.txt"},
        )
        producer = replace(
            producer, selection=WorkSelection.BLOCKED, problem_ids=(cause.problem_id,)
        )
        artifact = ArtifactWork(
            identity,
            producer.identity,
            identity.artifact,
            FINGERPRINT,
            output=identity.artifact,
            problem_ids=(cause.problem_id,),
        )
        observation = ArtifactResult(
            identity,
            ArtifactOutcome.NOT_COMPARED,
            WHEN,
            "reproduce-baseline",
            None,
            None,
            FINGERPRINT,
            None,
            NotComparedReason.COMMAND_BLOCKED,
        )
        run = SavedRun(
            "/project/docs/study.md",
            "reproduce-baseline",
            RunTarget(),
            RunSettings(),
            WHEN,
            WHEN,
            (producer,),
            (artifact,),
            (),
            (observation,),
            (cause,),
        )
        self.assertEqual(
            run.command_classification(producer.identity).reason, "baseline_changed"
        )
        self.assertEqual(SavedRun.from_json(run.serialized().encode()), run)
        unrelated = command(
            "other", WorkSelection.BLOCKED, problems=(cause.problem_id,)
        )
        with self.assertRaisesRegex(ValueError, "unrelated owner"):
            replace(run, commands=(producer, unrelated))

    def test_self_cycle_requires_an_actual_retained_problem(self):
        work = command("cycle")
        with self.assertRaisesRegex(ValueError, "no cause"):
            replace(
                work, selection=WorkSelection.BLOCKED, dependencies=(work.identity,)
            )
        cause = ReproductionProblem(
            work.identity,
            "dependency_cycle",
            ProblemStage.PREPARE,
            "Command depends on itself.",
            {"cycle": [work.identity.as_dict()]},
        )
        blocked = replace(
            work,
            selection=WorkSelection.BLOCKED,
            dependencies=(work.identity,),
            problem_ids=(cause.problem_id,),
        )
        self.assertEqual(CommandWork.from_dict(blocked.as_dict()), blocked)

    def test_evidence_only_context_preserves_definition_entry_and_target(self):
        plan = blocked_plan()
        definition = "d" * 64
        artifact = replace(plan.artifacts[0], definition_identity=definition)
        row = {
            "comparisons": [definition],
            "entry": "e001",
            "record_id": "value",
            "resource": "/retained/input.json",
            "kind": "file",
            "fingerprint": FINGERPRINT.as_dict(),
            "selection": {"algorithm": "sha256"},
        }
        plan = replace(
            plan,
            artifacts=(artifact, *plan.artifacts[1:]),
            evidence_only=(row,),
            target=RunTarget("entry", "e001"),
        )
        self.assertEqual(ReproductionPlan.from_json(plan.serialized().encode()), plan)
        for entry in ("e999", "wrong"):
            with self.assertRaises(ValueError):
                replace(plan, evidence_only=({**row, "entry": entry},))
        with self.assertRaisesRegex(ValueError, "comparison definition"):
            replace(plan, target=RunTarget(), evidence_only=({**row, "entry": "e002"},))

    def test_problem_links_have_actual_owners_and_no_unused_diagnoses(self):
        run = mixed_run()
        cause = next(
            problem
            for problem in run.problems
            if problem.code == "execution_failed" and problem.subject.cid == "producer"
        )
        consumer = next(
            work for work in run.commands if work.identity.cid == "consumer"
        )
        with self.assertRaisesRegex(ValueError, "unrelated owner"):
            replace(
                run,
                commands=tuple(
                    replace(work, problem_ids=(cause.problem_id,))
                    if work == consumer
                    else work
                    for work in run.commands
                ),
            )
        extra = ReproductionProblem(
            consumer.identity,
            "effective_code_changed",
            ProblemStage.PREPARE,
            "Unreferenced diagnosis.",
            {"path": "scripts/consumer.py"},
        )
        with self.assertRaisesRegex(ValueError, "unreferenced"):
            replace(run, problems=(*run.problems, extra))
        plan = blocked_plan()
        cause = next(
            problem for problem in plan.problems if problem.subject.cid == "producer"
        )
        with self.assertRaisesRegex(ValueError, "unrelated owner"):
            replace(
                plan,
                commands=tuple(
                    replace(work, problem_ids=(cause.problem_id,))
                    if work.identity.cid == "consumer"
                    else work
                    for work in plan.commands
                ),
            )

    def test_shared_source_problems_link_only_to_accepted_consumers(self):
        plan = blocked_plan()
        producer = next(
            work for work in plan.commands if work.identity.cid == "producer"
        )
        original = next(
            problem for problem in plan.problems if problem.subject == producer.identity
        )
        source = replace(
            original, subject=SourceRef(producer.entry_root + "/scripts/producer.py")
        )
        commands = tuple(
            replace(work, problem_ids=(source.problem_id,))
            if work == producer
            else work
            for work in plan.commands
        )
        plan = replace(
            plan,
            commands=commands,
            problems=tuple(
                source if problem == original else problem for problem in plan.problems
            ),
        )
        self.assertEqual(ReproductionPlan.from_json(plan.serialized().encode()), plan)
        unrelated = replace(source, subject=SourceRef("/elsewhere/producer.py"))
        with self.assertRaisesRegex(ValueError, "unrelated"):
            replace(
                plan,
                commands=tuple(
                    replace(work, problem_ids=(unrelated.problem_id,))
                    if work.identity == producer.identity
                    else work
                    for work in plan.commands
                ),
                problems=tuple(
                    unrelated if problem == source else problem
                    for problem in plan.problems
                ),
            )

    def test_accepted_command_decoding_does_not_recheck_any_filesystem_paths(self):
        work = command("saved", outputs=(("<project>/outputs/saved.txt", "file"),))
        execution = replace(
            work.execution,
            observed=replace(
                work.execution.observed,
                effective_code=Fingerprint(
                    "python-effective-code-sha256-v1", "c" * 64
                ),
            ),
        )
        declaration = {
            "schema": "research-log-data/v6",
            "inputs": [
                {
                    "name": "source",
                    "kind": "file",
                    "location": "data/source.json",
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                    "canonical_target": "/accepted-material/source.json",
                    "reference_entry": None,
                }
            ],
        }
        with mock.patch(
            "pathlib.Path.resolve", side_effect=AssertionError("live path resolution")
        ):
            work = replace(work, execution=execution, data_declaration=declaration)
            self.assertEqual(CommandWork.from_dict(work.as_dict()), work)
            run = SavedRun(
                "/project/docs/study.md",
                "reproduce-saved",
                RunTarget(),
                RunSettings(),
                WHEN,
                WHEN,
                (work,),
                (),
                (attempted(work),),
                (),
                (),
            )
            self.assertEqual(SavedRun.from_json(run.serialized().encode()), run)

    def test_exact_current_plan_and_closed_acceptance_round_trip(self):
        plan = blocked_plan()
        self.assertEqual(
            plan_selection_counts(plan.commands),
            {
                "total": 7,
                "ready_to_run": 1,
                "blocked": 2,
                "not_needed": 1,
                "previous_failure": 1,
                "previous_block": 1,
                "skipped_by_policy": 1,
            },
        )
        self.assertEqual(ReproductionPlan.from_json(plan.serialized().encode()), plan)
        self.assertNotIn("cases", plan.as_dict())
        self.assertNotIn("failures", plan.as_dict())
        self.assertEqual(len(plan.problems), 3)

    def test_plan_infrastructure_and_identity_integrity(self):
        plan = blocked_plan()
        with self.assertRaisesRegex(ValueError, "selected work"):
            replace(plan, scheduling=())
        raw = plan.as_dict()
        raw["schema"] = "research-log-reproduction-plan/11"
        with self.assertRaisesRegex(ValueError, "no legacy resume"):
            ReproductionPlan.from_json(json.dumps(raw).encode())
        raw = plan.as_dict()
        raw["admission"]["executions"].pop()
        with self.assertRaisesRegex(ValueError, "cover"):
            ReproductionPlan.from_json(json.dumps(raw).encode())
        with self.assertRaisesRegex(ValueError, "unknown retained material role"):
            replace(
                plan,
                materials=(
                    {
                        "role": "invented",
                        "identity": "/missing/input",
                        "kind": "file",
                        "fingerprint": FINGERPRINT.as_dict(),
                    },
                ),
            )
        with mock.patch("log_commands.reproduction_work_plan.MAX_PLAN_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "byte bound"):
                plan.serialized()

    def test_native_lookups_return_the_accepted_objects_without_decoding(self):
        plan = blocked_plan()
        with mock.patch(
            "log_commands.reproduction_work.data_file_from_fields",
            side_effect=AssertionError("decoded again"),
        ):
            for work in plan.commands:
                self.assertIs(plan.command(work.identity), work)
            for work in plan.artifacts:
                self.assertIs(plan.artifact(work.identity), work)
            for row in plan.scheduling:
                identity = ExecutionRef.from_dict(row["identity"])
                self.assertIs(plan.schedule(identity), row)
        unknown = ExecutionRef(
            "e002", "unknown", plan.commands[0].identity.execution_id
        )
        with self.assertRaisesRegex(ValueError, "outside accepted work"):
            plan.command(unknown)
        with self.assertRaisesRegex(ValueError, "outside accepted work"):
            plan.artifact(ArtifactRef("e002", "data/unknown.txt"))
        with self.assertRaisesRegex(ValueError, "no accepted schedule"):
            plan.schedule(plan.commands[0].identity)
        self.assertFalse(any(key.startswith("_") for key in plan.as_dict()))

    def test_closed_work_and_result_round_trips(self):
        run = mixed_run()
        for records, kind in (
            (run.commands, CommandWork),
            (run.artifacts, ArtifactWork),
            (run.command_results, CommandResult),
            (run.artifact_results, ArtifactResult),
        ):
            for record in records:
                self.assertEqual(
                    kind.from_dict(record.as_dict()).as_dict(), record.as_dict()
                )
                with self.assertRaises(ValueError):
                    kind.from_dict({**record.as_dict(), "bucket": "invented"})
        self.assertEqual(SavedRun.from_json(run.serialized().encode()), run)

    def test_exact_mixed_counts_and_parent_equations(self):
        run = mixed_run()
        self.assertEqual(
            saved_counts(run),
            {
                "commands": {
                    "total": 7,
                    "not_run": {
                        "total": 3,
                        "not_needed": 1,
                        "previous_failure": 1,
                        "previous_block": 1,
                    },
                    "skipped_by_policy": 1,
                    "selected": {
                        "total": 3,
                        "succeeded": 1,
                        "failed": {"total": 1, "reasons": {"execution_failed": 1}},
                        "blocked": {"total": 1, "reasons": {"execution_failed": 1}},
                    },
                },
                "artifacts": {
                    "total": 4,
                    "matched": 1,
                    "not_matched": 1,
                    "not_compared": {
                        "total": 2,
                        "reasons": {"command-blocked": 1, "command-failed": 1},
                    },
                },
            },
        )
        self.assertEqual(
            run.primary_problem(
                next(
                    work.identity
                    for work in run.commands
                    if work.identity.cid == "consumer"
                )
            ).explanation,
            "Producer exited with status 2.",
        )

    def test_current_plan_counts_do_not_predict_results(self):
        run = mixed_run()
        self.assertEqual(
            plan_selection_counts(run.commands),
            {
                "total": 7,
                "ready_to_run": 3,
                "blocked": 0,
                "not_needed": 1,
                "previous_failure": 1,
                "previous_block": 1,
                "skipped_by_policy": 1,
            },
        )

    def test_empty_saved_target_has_explicit_zeroes_not_unavailable_counts(self):
        run = SavedRun(
            "/project/docs/empty.md",
            "reproduce-empty",
            RunTarget(),
            RunSettings(),
            WHEN,
            WHEN,
            (),
            (),
            (),
            (),
            (),
        )
        counts = saved_counts(SavedRun.from_json(run.serialized().encode()))
        self.assertEqual(
            counts["commands"],
            {
                "total": 0,
                "not_run": {
                    "total": 0,
                    "not_needed": 0,
                    "previous_failure": 0,
                    "previous_block": 0,
                },
                "skipped_by_policy": 0,
                "selected": {
                    "total": 0,
                    "succeeded": 0,
                    "failed": {"total": 0, "reasons": {}},
                    "blocked": {"total": 0, "reasons": {}},
                },
            },
        )
        self.assertEqual(
            counts["artifacts"],
            {
                "total": 0,
                "matched": 0,
                "not_matched": 0,
                "not_compared": {"total": 0, "reasons": {}},
            },
        )

    def test_equal_execution_digests_in_different_cids_and_entries_stay_distinct(self):
        commands = tuple(
            command(cid, entry=entry)
            for entry in ("e001", "e002")
            for cid in ("first", "second")
        )
        self.assertEqual(len({work.identity.execution_id for work in commands}), 1)
        run = SavedRun(
            "/project/docs/study.md",
            "reproduce-identities",
            RunTarget(),
            RunSettings(),
            WHEN,
            WHEN,
            commands,
            (),
            tuple(attempted(work) for work in commands),
            (),
            (),
        )
        restored = SavedRun.from_json(run.serialized().encode())
        self.assertEqual(saved_counts(restored)["commands"]["total"], 4)
        self.assertEqual(
            {work.identity for work in restored.commands},
            {work.identity for work in commands},
        )

    def test_directory_member_fanout_retains_one_diagnosis_and_linear_record_growth(
        self,
    ):
        initial = command("producer", outputs=(("data/out", "directory"),))
        cause = ReproductionProblem(
            initial.identity,
            "effective_code_unavailable",
            ProblemStage.PREPARE,
            "Producer script is unavailable.",
            {"path": "scripts/producer.py", "error": "ENOENT"},
        )
        producer = replace(
            initial, selection=WorkSelection.BLOCKED, problem_ids=(cause.problem_id,)
        )
        sizes = {}
        for size in (10, 1000):
            artifacts = tuple(
                ArtifactWork(
                    ArtifactRef("e001", f"data/out/member-{index}.txt"),
                    producer.identity,
                    f"data/out/member-{index}.txt",
                    FINGERPRINT,
                    output="data/out",
                )
                for index in range(size)
            )
            results = tuple(
                ArtifactResult(
                    work.identity,
                    ArtifactOutcome.NOT_COMPARED,
                    WHEN,
                    "reproduce-fanout",
                    None,
                    None,
                    FINGERPRINT,
                    None,
                    NotComparedReason.COMMAND_BLOCKED,
                )
                for work in artifacts
            )
            run = SavedRun(
                "/project/docs/study.md",
                "reproduce-fanout",
                RunTarget(),
                RunSettings(),
                WHEN,
                WHEN,
                (producer,),
                artifacts,
                (),
                results,
                (cause,),
            )
            self.assertEqual(len(run.problems), 1)
            self.assertEqual(sum(len(work.problem_ids) for work in run.artifacts), 0)
            self.assertEqual(
                saved_counts(run)["artifacts"]["not_compared"],
                {"total": size, "reasons": {"command-blocked": size}},
            )
            sizes[size] = len(run.serialized().encode())
            self.assertLess(sizes[size], 2 * 1024 * 1024)
        self.assertLess(sizes[1000], sizes[10] * 100)

    def test_unknown_output_binding_and_lost_prior_diagnosis_fail_closed(self):
        run = mixed_run()
        wrong = replace(run.artifacts[0], output="data/not-declared.txt")
        with self.assertRaisesRegex(ValueError, "not declared"):
            replace(
                run,
                artifacts=tuple(
                    wrong if work.identity == wrong.identity else work
                    for work in run.artifacts
                ),
            )
        with self.assertRaisesRegex(ValueError, "retained diagnosis"):
            command("prior", WorkSelection.PREVIOUS_FAILURE)

    def test_frozen_evidence_uses_original_grammar_without_current_roots(self):
        work = mixed_run().artifacts[0]
        record = {
            "id": "retained-artifact",
            "document": "entries/2026-09-15-e001-study/e001.md",
            "kind": "artifact",
            "sources": [{"source": "<equal>", "locator": None}],
            "transformation": None,
        }
        accepted = replace(
            work, definition_identity="a" * 64, evidence_records=(record,)
        )
        with mock.patch(
            "pathlib.Path.resolve", side_effect=AssertionError("current root lookup")
        ):
            decoded = ArtifactWork.from_dict(accepted.as_dict())
            self.assertEqual(decoded.as_dict(), accepted.as_dict())
        run = mixed_run()
        with self.assertRaisesRegex(ValueError, "frozen producer root"):
            wrong_root = replace(
                accepted,
                evidence_records=(
                    {**record, "document": "entries/2026-09-14-e001-other/e001.md"},
                ),
            )
            replace(
                run,
                artifacts=tuple(
                    wrong_root if item.identity == accepted.identity else item
                    for item in run.artifacts
                ),
            )
        with self.assertRaisesRegex(ValueError, "owning entry"):
            replace(
                accepted,
                evidence_records=(
                    {**record, "document": "entries/2026-09-15-e002-study/e002.md"},
                ),
            )
        with self.assertRaises(ValueError):
            replace(
                work,
                definition_identity="a" * 64,
                evidence_records=({"invented": True},),
            )

    def test_evidence_comparison_observations_retain_existing_closed_fields(self):
        result = next(
            item
            for item in mixed_run().artifact_results
            if item.outcome is ArtifactOutcome.MATCHED
        )
        record = {
            "definition": "a" * 64,
            "id": "retained-value",
            "matched": True,
            "expected": [{"number": "1"}],
            "regenerated": [{"number": "1"}],
            "tolerance": None,
        }
        observed = replace(
            result, profile="evidence", evidence=(record,), definition_identity="a" * 64
        )
        self.assertEqual(
            ArtifactResult.from_dict(observed.as_dict()).as_dict(), observed.as_dict()
        )
        for changed in (
            {**record, "matched": 1},
            {**record, "extra": True},
            {**record, "regenerated": []},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(result, evidence=(changed,))

    def test_reordering_preserves_canonical_identity_and_count_results(self):
        run = mixed_run()
        reversed_run = replace(
            run,
            commands=tuple(reversed(run.commands)),
            artifacts=tuple(reversed(run.artifacts)),
            problems=tuple(reversed(run.problems)),
        )
        self.assertEqual(reversed_run.serialized(), run.serialized())

    def test_saved_deserialization_and_classification_need_no_research_reads(self):
        raw = mixed_run().serialized().encode()
        with (
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("research read")
            ),
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("research read")
            ),
            mock.patch(
                "pathlib.Path.resolve",
                side_effect=AssertionError("live path resolution"),
            ),
        ):
            self.assertEqual(
                saved_counts(SavedRun.from_json(raw))["commands"]["total"], 7
            )

    def test_identity_collisions_and_unknown_problem_links_fail(self):
        run = mixed_run()
        with self.assertRaisesRegex(ValueError, "duplicate commands"):
            replace(run, commands=run.commands + (run.commands[0],))
        failure = next(
            result
            for result in run.command_results
            if result.outcome is CommandOutcome.FAILED
        )
        altered = replace(failure, problem_ids=("problem-" + "c" * 64,))
        with self.assertRaisesRegex(ValueError, "unknown problem"):
            replace(
                run,
                command_results=tuple(
                    altered if item.identity == altered.identity else item
                    for item in run.command_results
                ),
            )
        with self.assertRaisesRegex(ValueError, "cover"):
            replace(run, artifact_results=run.artifact_results[:-1])
        with self.assertRaisesRegex(ValueError, "accepted selection"):
            replace(run, command_results=())

    def test_scope_and_no_legacy_schema_or_execution_target(self):
        run = mixed_run()
        with self.assertRaises(ValueError):
            RunTarget.from_dict(
                {"kind": "execution", "entry": "e001", "cid": "x", "execution_id": "x"}
            )
        with self.assertRaisesRegex(ValueError, "entry coverage"):
            replace(run, target=RunTarget("entry", "e002"))
        value = run.as_dict()
        value["schema"] = "research-log-reproduction-result/11"
        with self.assertRaisesRegex(ValueError, "--recheck"):
            SavedRun.from_json(json.dumps(value).encode())
        value = run.as_dict()
        value["status"] = "stopped"
        with self.assertRaisesRegex(ValueError, "unpublished job"):
            SavedRun.from_json(json.dumps(value).encode())

    def test_bool_counts_and_invalid_runtime_controls_are_rejected(self):
        for settings in (
            {"jobs": True},
            {"jobs": 0},
            {"recheck": 1},
            {"execution_timeout_seconds": True},
            {"execution_timeout_seconds": 604801},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RunSettings(**settings)

    def test_attempt_and_comparison_integrity_and_bounded_records(self):
        run = mixed_run()
        success = next(
            item
            for item in run.command_results
            if item.outcome is CommandOutcome.SUCCEEDED
        )
        with self.assertRaisesRegex(ValueError, "timing"):
            replace(success, started_at=None)
        with self.assertRaisesRegex(ValueError, "before"):
            replace(success, finished_at="2026-09-14T12:00:00Z")
        equal = next(
            item
            for item in run.artifact_results
            if item.outcome is ArtifactOutcome.MATCHED
        )
        with self.assertRaisesRegex(ValueError, "observations"):
            replace(equal, regenerated=None)
        with mock.patch("log_commands.reproduction_saved_run.MAX_WORK_RECORDS", 1):
            with self.assertRaisesRegex(ValueError, "record bound"):
                replace(run)
        with mock.patch("log_commands.reproduction_saved_run.MAX_RESULT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "byte bound"):
                run.serialized()

    def test_artifact_command_reasons_match_recorded_producer(self):
        run = mixed_run()
        failed = next(
            item
            for item in run.artifact_results
            if item.not_compared_reason is NotComparedReason.COMMAND_FAILED
        )
        for reason in (
            NotComparedReason.COMMAND_BLOCKED,
            NotComparedReason.COMMAND_NOT_RUN,
            NotComparedReason.SKIPPED_BY_POLICY,
        ):
            altered = replace(failed, not_compared_reason=reason)
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(ValueError, "contradicts"),
            ):
                replace(
                    run,
                    artifact_results=tuple(
                        altered if item.identity == altered.identity else item
                        for item in run.artifact_results
                    ),
                )
        ownerless = replace(
            next(work for work in run.artifacts if work.identity == failed.identity),
            producer=None,
            output=None,
        )
        with self.assertRaisesRegex(ValueError, "no producer"):
            replace(
                run,
                artifacts=tuple(
                    ownerless if work.identity == ownerless.identity else work
                    for work in run.artifacts
                ),
            )


if __name__ == "__main__":
    unittest.main()
