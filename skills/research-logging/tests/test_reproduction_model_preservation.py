"""Real pre-change preparation fixtures preserved through reproduction repackaging."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import test_reproduction_planning_matrix as ORIGINAL_PLANNING
from log_commands import reproduction_planner as PLANNER
from log_commands.reproduction_domain import (
    ArtifactRef,
    ExecutionRef,
    ProblemStage,
    ReproductionProblem,
    WorkSelection,
)
from log_commands.reproduction_invocation import (
    ReproductionRuntime,
)
from log_commands.reproduction_saved_storage import PreparationHistory
from log_commands.reproduction_summary import plan_selection_counts
from log_commands.reproduction_work import ArtifactWork, CommandWork, validate_producer
from reproduction_planning_test_support import _Fixture, prepare_plan
from research_log_data import Fingerprint
from validation.pyrun_state import load_pyrun_state


def fanout_fixture(root: Path, output_count: int = 10):
    """Build one producer, two consumers and an independent execution with evidence."""

    fixture = _Fixture(root)
    entry = fixture.entry(1)
    names = (
        "raw",
        *(f"output-{n}" for n in range(output_count)),
        "first",
        "second",
        "independent",
    )
    paths = {name: entry.root / "data" / f"{name}.txt" for name in names}
    for name, path in paths.items():
        path.write_text(name, encoding="utf-8")
    fixture.write_data(
        entry,
        [
            fixture.item(entry, name, path, origin=name == "raw")
            for name, path in paths.items()
        ],
    )
    fixture.evidence(entry, *names[1:])
    producer = fixture.execution(
        entry,
        "producer",
        {"raw": paths["raw"]},
        {name: paths[name] for name in names if name.startswith("output-")},
    )
    first = fixture.execution(
        entry, "first", {"output-0": paths["output-0"]}, {"first": paths["first"]}
    )
    second = fixture.execution(
        entry, "second", {"output-0": paths["output-0"]}, {"second": paths["second"]}
    )
    independent = fixture.execution(
        entry,
        "independent",
        {"raw": paths["raw"]},
        {"independent": paths["independent"]},
    )
    fixture.write_pyrun(entry, [producer, first, second, independent])
    return (
        fixture,
        entry,
        {
            "producer": producer[0],
            "first": first[0],
            "second": second[0],
            "independent": independent[0],
        },
    )


class ReproductionModelPreservationTests(unittest.TestCase):
    def test_previous_diagnoses_bind_accepted_history_without_changing_source_or_retry(
        self,
    ):
        for disposition, selection in (
            ("failed", WorkSelection.PREVIOUS_FAILURE),
            ("blocked", WorkSelection.PREVIOUS_BLOCK),
        ):
            with (
                self.subTest(previous=disposition),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, entry, identities = fanout_fixture(Path(directory), 1)
                with mock.patch.object(
                    PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
                ) as project:
                    prepare_plan(fixture, entry)
                state, _, prepared = project.call_args.args[:3]
                key = ("e001", "independent", identities["independent"])
                identity = ExecutionRef(*key)
                source_digest = state.command_digests[key]
                ordered = PLANNER._select_and_order(
                    state,
                    {key: {"disposition": disposition, "source_digest": source_digest}},
                )
                self.assertNotIn(key, ordered)
                accepted = []
                for retained_error in (
                    "first retained diagnosis",
                    "second retained diagnosis",
                ):
                    problem = ReproductionProblem(
                        identity,
                        "execution_failed"
                        if disposition == "failed"
                        else "direct_input_unavailable",
                        ProblemStage.EXECUTE
                        if disposition == "failed"
                        else ProblemStage.PREPARE,
                        retained_error,
                        {"error": retained_error},
                    )
                    plan = PLANNER._canonical_plan(
                        state,
                        ordered,
                        prepared,
                        entry,
                        PreparationHistory(problems={identity: (problem,)}),
                    )
                    work = next(
                        work for work in plan.commands if work.identity == identity
                    )
                    self.assertIs(work.selection, selection)
                    self.assertEqual(work.problem_ids, (problem.problem_id,))
                    self.assertEqual(work.source_digest, source_digest)
                    self.assertEqual(
                        PLANNER._command_source_digest(state, key), source_digest
                    )
                    self.assertNotIn(problem.problem_id, state.problems)
                    self.assertIn(problem, plan.problems)
                    accepted.append(plan.serialized())
                self.assertNotEqual(*accepted)

    def test_previous_dependency_block_retains_its_cause_at_the_actual_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory), 1)
            with mock.patch.object(
                PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state, ordered, prepared = project.call_args.args[:3]
            producer = ExecutionRef("e001", "producer", identities["producer"])
            consumer = ExecutionRef("e001", "first", identities["first"])
            producer_key = (producer.entry, producer.cid, producer.execution_id)
            consumer_key = (consumer.entry, consumer.cid, consumer.execution_id)
            digests = dict(state.command_digests)
            state.command_selections[producer_key] = "not_needed"
            state.command_selections[consumer_key] = "unchanged"
            state.prior_command_dispositions[consumer_key] = "blocked"
            independent = ExecutionRef("e001", "independent", identities["independent"])
            independent_key = (
                independent.entry,
                independent.cid,
                independent.execution_id,
            )
            state.command_selections[independent_key] = "not_needed"
            ordered = tuple(
                key
                for key in ordered
                if key not in {producer_key, consumer_key, independent_key}
            )
            cause = ReproductionProblem(
                producer,
                "execution_failed",
                ProblemStage.EXECUTE,
                "Retained prerequisite exited with status 2.",
                {"returncode": 2},
            )
            unrelated = ReproductionProblem(
                independent,
                "execution_failed",
                ProblemStage.EXECUTE,
                "Unrelated earlier failure.",
                {"returncode": 3},
            )
            plan = PLANNER._canonical_plan(
                state,
                ordered,
                prepared,
                entry,
                PreparationHistory(
                    problems={producer: (cause,), independent: (unrelated,)}
                ),
            )
            self.assertIs(plan.command(producer).selection, WorkSelection.NOT_NEEDED)
            self.assertEqual(plan.command(producer).problem_ids, (cause.problem_id,))
            self.assertIs(
                plan.command(consumer).selection, WorkSelection.PREVIOUS_BLOCK
            )
            self.assertIn(producer, plan.command(consumer).dependencies)
            self.assertEqual(plan.command(consumer).problem_ids, ())
            self.assertEqual(plan.problems, (cause,))
            self.assertEqual(plan.command(independent).problem_ids, ())
            self.assertNotIn(cause.problem_id, state.problems)
            self.assertEqual(state.command_digests, digests)
            self.assertEqual(type(plan).from_json(plan.serialized().encode()), plan)

    def test_actual_preparation_crosses_typed_plan_boundary_without_old_projections(
        self,
    ):
        for method in (
            "test_changed_script_blocks_only_its_dependants",
            "test_cycle_fails_its_outputs_but_independent_execution_remains",
            "test_log_target_reports_external_generated_input_without_aborting",
            "test_direct_nonautomatic_evidence_is_reported_as_skipped",
            "test_entry_target_uses_generated_cross_entry_input_as_boundary",
        ):
            with self.subTest(original=method):
                original = ORIGINAL_PLANNING.NativePlanningMatrixTests(method)
                with mock.patch.object(
                    PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
                ) as project:
                    getattr(original, method)()
                state, ordered, prepared = project.call_args.args[:3]
                expected_artifacts = {
                    (item.identity.entry, item.identity.artifact)
                    for item in state.artifacts.values()
                }
                with (
                    mock.patch(
                        "pathlib.Path.read_bytes",
                        side_effect=AssertionError("live bytes"),
                    ),
                    mock.patch(
                        "pathlib.Path.read_text",
                        side_effect=AssertionError("live text"),
                    ),
                    mock.patch.object(
                        PLANNER,
                        "observe_fingerprint",
                        side_effect=AssertionError("live observation"),
                    ),
                ):
                    plan = PLANNER._canonical_plan(
                        state,
                        ordered,
                        prepared,
                        project.call_args.args[3],
                        PreparationHistory(),
                    )
                    self.assertEqual(
                        type(plan).from_json(plan.serialized().encode()), plan
                    )
                self.assertEqual(
                    {
                        (work.identity.entry, work.identity.artifact)
                        for work in plan.artifacts
                    },
                    expected_artifacts,
                )
                counts = plan_selection_counts(plan.commands)
                self.assertEqual(counts["ready_to_run"], len(ordered))
                self.assertEqual(
                    {
                        work.identity
                        for work in plan.commands
                        if work.selection is WorkSelection.RUN
                    },
                    {ExecutionRef(*key) for key in ordered},
                )

    def test_source_guard_retains_secondary_cause_without_case_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory), 1)
            shared = entry.root / "scripts/shared.py"
            shared.write_text("# original code\n", encoding="utf-8")
            executions = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=fixture.root,
            )
            fixture.write_pyrun(
                entry,
                [
                    (
                        identity,
                        replace(
                            execution,
                            observed=replace(
                                execution.observed,
                                code=(
                                    (
                                        "scripts/shared.py",
                                        Fingerprint(
                                            "sha256",
                                            hashlib.sha256(
                                                shared.read_bytes()
                                            ).hexdigest(),
                                        ),
                                    ),
                                ),
                            ),
                        )
                        if cid == "producer"
                        else execution,
                    )
                    for cid, identity, execution in executions.execution_items()
                ],
            )
            shared.write_text("# changed code\n", encoding="utf-8")
            script = entry.root / "scripts/producer.py"
            key = ("e001", "producer", identities["producer"])
            digests = []
            for content in ("# changed script once\n", "# changed script twice\n"):
                script.write_text(content, encoding="utf-8")
                with mock.patch.object(
                    PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
                ) as project:
                    plan = prepare_plan(fixture, entry)
                state = project.call_args.args[0]
                self.assertEqual(
                    {item["identity"]["cid"] for item in plan.scheduling},
                    {"independent"},
                )
                self.assertEqual(
                    {
                        state.problems[reference].code
                        for reference in state.command_problem_ids[key]
                    },
                    {"script_changed", "participating_code_changed"},
                )
                digests.append(state.command_digests[key])
                # Obsolete diagnostic projections cannot influence source guards.
                self.assertEqual(
                    PLANNER._command_source_digest(state, key), digests[-1]
                )
            self.assertNotEqual(*digests)

    def test_cross_entry_pending_observation_remains_reproduce_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, upstream, identities = fanout_fixture(Path(directory), 1)
            downstream = fixture.entry(2)
            shared = upstream.root / "data/output-0.txt"
            final = downstream.root / "data/final.txt"
            final.write_text("final", encoding="utf-8")
            fixture.write_data(
                downstream,
                [
                    fixture.item(downstream, "shared", shared, origin=False),
                    fixture.item(downstream, "final", final, origin=False),
                ],
            )
            fixture.evidence(downstream, "final")
            consumer = fixture.execution(
                downstream, "consumer", {"shared": shared}, {"final": final}
            )
            fixture.write_pyrun(downstream, [consumer])
            executions = load_pyrun_state(
                upstream.root / "pyrun.json",
                entry_root=upstream.root,
                project_root=fixture.root,
            )
            fixture.write_pyrun(
                upstream,
                [
                    (
                        identity,
                        replace(
                            execution, observed=replace(execution.observed, script=None)
                        )
                        if cid == "producer"
                        else execution,
                    )
                    for cid, identity, execution in executions.execution_items()
                ],
            )
            with mock.patch.object(
                PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
            ) as project:
                plan = prepare_plan(fixture, None)
            state = project.call_args.args[0]
            problems = [
                p for p in state.problems.values() if p.code == "script_unavailable"
            ]
            self.assertEqual(len(problems), 1)
            problem = problems[0]
            producer = ("e001", "producer", identities["producer"])
            self.assertEqual(state.command_problem_ids[producer], [problem.problem_id])
            self.assertEqual(
                problem.subject.path,
                (upstream.root / "scripts/producer.py").as_posix(),
            )
            self.assertFalse(
                any(
                    item.code == "validation_blocked"
                    for item in state.problems.values()
                )
            )
            for key in (
                producer,
                ("e002", "consumer", consumer[0]),
            ):
                self.assertEqual(state.admission_decisions[key].disposition, "admitted")
            self.assertEqual(
                {item["identity"]["cid"] for item in plan.scheduling}, {"independent"}
            )

    def test_original_cycle_fixture_retains_one_cause_and_exact_consumers(self):
        original = ORIGINAL_PLANNING.NativePlanningMatrixTests(
            "test_cycle_fails_its_outputs_but_independent_execution_remains"
        )
        with mock.patch.object(
            PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
        ) as project:
            original.test_cycle_fails_its_outputs_but_independent_execution_remains()
        state = project.call_args.args[0]
        cycles = [p for p in state.problems.values() if p.code == "dependency_cycle"]
        self.assertEqual(len(cycles), 1)
        problem = cycles[0]
        members = {ExecutionRef.from_dict(item) for item in problem.observed["members"]}
        self.assertEqual({member.cid for member in members}, {"first", "second"})
        self.assertEqual(
            {
                ExecutionRef(*key)
                for key, references in state.command_problem_ids.items()
                if problem.problem_id in references
            },
            members,
        )

    def test_original_missing_input_keeps_error_and_validation_diagnosis(self):
        original = ORIGINAL_PLANNING.NativePlanningMatrixTests(
            "test_missing_direct_input_is_local_to_its_consumer"
        )
        with mock.patch.object(
            PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
        ) as project:
            original.test_missing_direct_input_is_local_to_its_consumer()
        state = project.call_args.args[0]
        problems = {p.code: p for p in state.problems.values()}
        unavailable = problems["direct_input_unavailable"]
        self.assertEqual(unavailable.observed["error_type"], "DataContractError")
        self.assertTrue(unavailable.observed["error"])
        self.assertIsNotNone(unavailable.observed["expected"])
        excluded = problems["validation_blocked"]
        self.assertTrue(excluded.subject.path.endswith("study.md"))
        self.assertTrue(excluded.observed["finding"]["finding_id"])
        self.assertIn("observed", excluded.observed["finding"])

    def test_original_ownerless_generated_input_keeps_artifact_cause(self):
        original = ORIGINAL_PLANNING.NativePlanningMatrixTests(
            "test_log_target_reports_external_generated_input_without_aborting"
        )
        with mock.patch.object(
            PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
        ) as project:
            original.test_log_target_reports_external_generated_input_without_aborting()
        state = project.call_args.args[0]
        problem = next(
            p for p in state.problems.values() if p.code == "cross_log_generated_input"
        )
        self.assertIsInstance(problem.subject, ArtifactRef)
        self.assertEqual(
            state.artifact_problem_ids[problem.subject], [problem.problem_id]
        )

    def test_real_dated_evidence_decodes_against_frozen_producer_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory))
            state = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=fixture.root,
            )
            execution = next(
                execution
                for cid, _, execution in state.execution_items()
                if cid == "producer"
            )
            identity = ExecutionRef("e001", "producer", identities["producer"])
            command = CommandWork(
                identity,
                execution,
                entry.root.as_posix(),
                fixture.root.as_posix(),
                None,
                WorkSelection.RUN,
                "a" * 64,
            )
            fields = json.loads(
                (entry.root / "evidence.json").read_text(encoding="utf-8")
            )["records"][0]
            with mock.patch(
                "pathlib.Path.resolve",
                side_effect=AssertionError("live root resolution"),
            ):
                artifact = ArtifactWork(
                    ArtifactRef("e001", "data/output-0.txt"),
                    identity,
                    "data/output-0.txt",
                    Fingerprint("sha256", "a" * 64),
                    output="data/output-0.txt",
                    definition_identity="b" * 64,
                    evidence_records=(fields,),
                )
                validate_producer(artifact, {identity: command})
                self.assertEqual(ArtifactWork.from_dict(artifact.as_dict()), artifact)
            self.assertEqual(
                artifact.evidence_records[0]["document"], fields["document"]
            )

    def test_missing_recorded_script_observation_has_one_owned_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory))
            state = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=fixture.root,
            )
            fixture.write_pyrun(
                entry,
                [
                    (
                        identity,
                        replace(
                            execution, observed=replace(execution.observed, script=None)
                        )
                        if cid == "producer"
                        else execution,
                    )
                    for cid, identity, execution in state.execution_items()
                ],
            )
            with mock.patch.object(
                PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state = project.call_args.args[0]
            problem = next(
                problem
                for problem in state.problems.values()
                if problem.code == "script_unavailable"
            )
            self.assertIsNone(problem.observed["expected"])
            self.assertEqual(
                problem.observed["availability"], "missing-recorded-observation"
            )
            self.assertEqual(
                problem.subject.path,
                (entry.root / "scripts/producer.py").resolve().as_posix(),
            )
            self.assertIn(
                problem.problem_id,
                state.command_problem_ids[("e001", "producer", identities["producer"])],
            )
            self.assertEqual(
                len(
                    [
                        p
                        for p in state.problems.values()
                        if p.code == "script_unavailable"
                    ]
                ),
                1,
            )

    def test_actual_source_problem_construction_does_not_follow_output_fanout(self):
        for output_count in (10, 100):
            with (
                self.subTest(outputs=output_count),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, entry, identities = fanout_fixture(
                    Path(directory), output_count
                )
                script = entry.root / "scripts/producer.py"
                script.write_text("# changed producer\n", encoding="utf-8")
                with (
                    mock.patch.object(
                        PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
                    ) as project,
                    mock.patch.object(
                        PLANNER,
                        "ReproductionProblem",
                        wraps=PLANNER.ReproductionProblem,
                    ) as constructed,
                ):
                    plan = prepare_plan(fixture, entry)
                state = project.call_args.args[0]
                self.assertEqual(len(state.problems), 1)
                self.assertEqual(constructed.call_count, 1)
                problem = next(iter(state.problems.values()))
                self.assertEqual(problem.code, "script_changed")
                self.assertEqual(problem.subject.path, script.resolve().as_posix())
                self.assertNotEqual(
                    problem.observed["expected"], problem.observed["actual"]
                )
                self.assertEqual(
                    dict(state.command_problem_ids),
                    {
                        ("e001", "producer", identities["producer"]): [
                            problem.problem_id
                        ],
                    },
                )
                self.assertEqual(
                    {item["identity"]["cid"] for item in plan.scheduling},
                    {"independent"},
                )

    def test_actual_baseline_problem_keeps_artifact_owner_and_producer_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory))
            baseline = entry.root / "data/output-0.txt"
            baseline.write_text("changed baseline", encoding="utf-8")
            # Keep validation evidence current while retaining the old execution
            # baseline: this isolates the reproduction baseline guard.
            evidence_path = entry.root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            for record in evidence["records"]:
                if record["sources"][0]["source"] == "<output-0>":
                    record["artifact_fingerprint"]["digest"] = hashlib.sha256(
                        baseline.read_bytes()
                    ).hexdigest()
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            with mock.patch.object(
                PLANNER, "_canonical_plan", wraps=PLANNER._canonical_plan
            ) as project:
                plan = prepare_plan(fixture, entry)
            state = project.call_args.args[0]
            problem = next(
                problem
                for problem in state.problems.values()
                if problem.code == "baseline_changed"
            )
            self.assertEqual(problem.subject.entry, "e001")
            self.assertEqual(problem.subject.artifact, "data/output-0.txt")
            self.assertEqual(
                state.artifact_problem_ids[problem.subject], [problem.problem_id]
            )
            self.assertIn(
                problem.problem_id,
                state.command_problem_ids[("e001", "producer", identities["producer"])],
            )
            self.assertNotEqual(
                problem.observed["expected"], problem.observed["actual"]
            )
            self.assertEqual(
                {item["identity"]["cid"] for item in plan.scheduling}, {"independent"}
            )

    def test_ten_output_blocker_preserves_exact_work_selection_and_nonmutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, identities = fanout_fixture(Path(directory))
            runtime = ReproductionRuntime(jobs=3, execution_timeout_seconds=17)
            initial = prepare_plan(fixture, entry, runtime=runtime)
            self.assertEqual(
                {
                    (
                        item["identity"]["entry"],
                        item["identity"]["cid"],
                        item["identity"]["execution_id"],
                    )
                    for item in initial.scheduling
                },
                {("e001", cid, identity) for cid, identity in identities.items()},
            )
            (entry.root / "scripts/producer.py").write_text(
                "# changed producer\n", encoding="utf-8"
            )
            before = {
                str(p): p.read_bytes() for p in entry.root.rglob("*") if p.is_file()
            }
            plan = prepare_plan(fixture, entry, runtime=runtime)
            self.assertEqual(
                {
                    (
                        item["identity"]["entry"],
                        item["identity"]["cid"],
                        item["identity"]["execution_id"],
                    )
                    for item in plan.scheduling
                },
                {("e001", "independent", identities["independent"])},
            )
            self.assertEqual(
                {item.identity.cid: item.selection.value for item in plan.commands},
                {
                    "producer": "blocked",
                    "first": "blocked",
                    "second": "blocked",
                    "independent": "run",
                },
            )
            self.assertEqual(plan.settings.jobs, 3)
            self.assertEqual(plan.settings.execution_timeout_seconds, 17)
            self.assertFalse(plan.settings.include_all)
            self.assertEqual(plan.target.as_dict(), {"kind": "entry", "entry": "e001"})
            self.assertEqual(
                {str(p): p.read_bytes() for p in entry.root.rglob("*") if p.is_file()},
                before,
            )
            self.assertFalse((fixture.log_root / ".cache/results.sqlite").exists())
            self.assertFalse((fixture.log_root / "reproduction.md").exists())
            self.assertFalse((fixture.root / "tmp").exists())
