"""Native planner preserves the original policy, closure and failure fixtures."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from log_commands import reproduction_planner as planner
from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import NotComparedReason, WorkSelection
from log_commands.reproduction_work_plan import ReproductionPlan
from reproduction_planning_test_support import (
    _effective_fingerprint,
    _fingerprint,
    _Fixture,
)
from research_log_cli_test_support import fixture_parameter_roles
from test_reproduction_canonical_records import WHEN
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    execution_id,
)


def prepare(fixture, entry, *, include_all=False, recheck=False):
    evaluation = evaluate_mechanical(
        EvaluationRequest(fixture.summary, FullEvaluationTarget())
    )
    return planner.plan_reproduction_work(
        fixture.log,
        planner.prepare_reproduction_context(evaluation),
        entry=entry,
        include_all=include_all,
        selection=planner.ReproductionSelection(
            "recheck" if recheck else "incremental"
        ),
    )


def run_ids(plan):
    # Scheduling, not sorted work inventory, owns topological execution order.
    return [item["identity"]["execution_id"] for item in plan.scheduling]


def boundary_paths(plan):
    return {item["identity"] for item in plan.materials if item["role"] == "boundary"}


class NativePlanningMatrixTests(unittest.TestCase):
    def test_default_stops_at_nonautomatic_boundary_and_include_all_runs_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            seed = entry.root / "data" / "seed.txt"
            final = entry.root / "data" / "final.txt"
            raw.write_text("raw\n", encoding="utf-8")
            seed.write_text("seed\n", encoding="utf-8")
            final.write_text("final\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "seed", seed, origin=False),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            excluded = fixture.execution(
                entry,
                "simulate",
                {"raw": raw},
                {"seed": seed},
                auto_reproduce=False,
            )
            analysis = fixture.execution(
                entry, "analyze", {"seed": seed}, {"final": final}
            )
            fixture.write_pyrun(entry, [excluded, analysis])

            ordinary = prepare(fixture, entry)
            self.assertEqual(run_ids(ordinary), [analysis[0]])
            self.assertEqual(boundary_paths(ordinary), {seed.resolve().as_posix()})
            self.assertEqual(
                ReproductionPlan.from_json(ordinary.serialized().encode()), ordinary
            )
            self.assertEqual(
                len(ordinary.admission["executions"]), len(ordinary.commands)
            )
            self.assertFalse((fixture.log_root / ".cache/reproduction").exists())
            for recheck in (False, True):
                complete = prepare(fixture, entry, include_all=True, recheck=recheck)
                self.assertEqual(run_ids(complete), [excluded[0], analysis[0]])
                self.assertEqual(
                    run_ids(prepare(fixture, entry, recheck=recheck)), [analysis[0]]
                )
                self.assertEqual(boundary_paths(complete), {raw.resolve().as_posix()})

    def test_entry_target_uses_generated_cross_entry_input_as_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            upstream_entry = fixture.entry(1)
            entry = fixture.entry(2)
            shared = fixture.root / "shared" / "upstream.txt"
            shared.parent.mkdir()
            shared.write_text("shared\n", encoding="utf-8")
            final = entry.root / "data" / "final.txt"
            final.write_text("final\n", encoding="utf-8")
            fixture.write_data(
                upstream_entry,
                [fixture.item(upstream_entry, "shared", shared, origin=False)],
            )
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "upstream", shared, origin=False),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            downstream = fixture.execution(
                entry, "analyze", {"upstream": shared}, {"final": final}
            )
            fixture.write_pyrun(entry, [downstream])

            plan = prepare(fixture, entry)
            # This original fixture has no upstream producer record. Admission
            # excludes the consumer, but its accepted input identity is retained.
            self.assertEqual(plan.commands[0].identity.execution_id, downstream[0])
            self.assertEqual(plan.commands[0].selection, WorkSelection.BLOCKED)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(
                {item.code for item in plan.problems}, {"validation_blocked"}
            )
            finding = plan.problems[0].observed["finding"]
            self.assertEqual(finding["code"], "lineage.missing")
            self.assertEqual(finding["subject"], shared.resolve().as_posix())
            self.assertEqual(
                dict(plan.commands[0].execution.observed.inputs)["upstream"],
                _fingerprint(shared),
            )
            frozen_input = plan.commands[0].data_declaration["inputs"][0]
            self.assertEqual(frozen_input["name"], "upstream")
            self.assertEqual(
                Path(frozen_input["canonical_target"]).resolve(), shared.resolve()
            )

    def test_log_target_reports_external_generated_input_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project").mkdir()
            fixture = _Fixture(root / "project")
            entry = fixture.entry(1)
            external = root / "external.txt"
            raw = entry.root / "data" / "raw.txt"
            final = entry.root / "data" / "final.txt"
            external.write_text("external\n", encoding="utf-8")
            raw.write_text("raw\n", encoding="utf-8")
            final.write_text("final\n", encoding="utf-8")
            external_item = fixture.item(entry, "external", external, origin=False)
            external_item["location"] = external.as_posix()
            fixture.write_data(
                entry,
                [
                    external_item,
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "external", "final")
            independent = fixture.execution(
                entry, "independent", {"raw": raw}, {"final": final}
            )
            fixture.write_pyrun(entry, [independent])
            plan = prepare(fixture, None)
            self.assertEqual(run_ids(plan), [independent[0]])
            self.assertEqual(
                {item.code for item in plan.problems}, {"cross_log_generated_input"}
            )
            self.assertIn(
                external.as_posix(), {item.identity.artifact for item in plan.artifacts}
            )

    def test_external_origin_uses_authored_location_as_boundary_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project").mkdir()
            fixture = _Fixture(root / "project")
            entry = fixture.entry(1)
            external = root / "external.txt"
            external.write_text("external\n", encoding="utf-8")
            item = fixture.item(entry, "external", external, origin=True)
            item["location"] = external.as_posix()
            fixture.write_data(entry, [item])
            fixture.evidence(entry, "external")

            plan = prepare(fixture, entry)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(plan.problems, ())
            self.assertEqual(boundary_paths(plan), {external.resolve().as_posix()})

    def test_direct_nonautomatic_evidence_is_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            result = entry.root / "data" / "result.txt"
            raw.write_text("raw\n", encoding="utf-8")
            result.write_text("result\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "result", result, origin=False),
                ],
            )
            fixture.evidence(entry, "result")
            excluded = fixture.execution(
                entry,
                "simulate",
                {"raw": raw},
                {"result": result},
                auto_reproduce=False,
            )
            fixture.write_pyrun(entry, [excluded])

            plan = prepare(fixture, entry)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(
                plan.commands[0].selection, WorkSelection.SKIPPED_BY_POLICY
            )
            self.assertEqual(plan.commands[0].identity.execution_id, excluded[0])
            saved = complete_saved_run(
                plan, RunCompletion("reproduce-policy", WHEN, WHEN, (), ())
            )
            self.assertEqual(
                saved.artifact_results[0].not_compared_reason,
                NotComparedReason.SKIPPED_BY_POLICY,
            )

    def test_reproduction_not_needed_precedes_nonautomatic_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            result = entry.root / "data" / "result.txt"
            raw.write_text("raw\n", encoding="utf-8")
            result.write_text("result\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=False),
                    fixture.item(entry, "result", result, origin=False),
                ],
            )
            fixture.evidence(entry, "result")
            upstream = fixture.execution(
                entry,
                "produce-raw",
                {},
                {"raw": raw},
            )
            current = fixture.execution(
                entry,
                "simulate",
                {"raw": raw},
                {"result": result},
                auto_reproduce=False,
                requires_reproduction=False,
            )
            fixture.write_pyrun(entry, [upstream, current])

            plan = prepare(fixture, entry)
            self.assertEqual(run_ids(plan), [upstream[0]])
            self.assertEqual(
                next(
                    item.selection
                    for item in plan.commands
                    if item.identity.execution_id == current[0]
                ),
                WorkSelection.NOT_NEEDED,
            )
            recheck = prepare(fixture, entry, recheck=True)
            self.assertEqual(run_ids(recheck), [upstream[0]])
            policy = next(
                item
                for item in recheck.commands
                if item.identity.execution_id == current[0]
            )
            self.assertEqual(policy.selection, WorkSelection.SKIPPED_BY_POLICY)
            self.assertIsNone(policy.source_digest)

    def test_cycle_fails_its_outputs_but_independent_execution_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            paths = {name: entry.root / "data" / f"{name}.txt" for name in "abcr"}
            for name, path in paths.items():
                path.write_text(name, encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "a", paths["a"], origin=False),
                    fixture.item(entry, "b", paths["b"], origin=False),
                    fixture.item(entry, "c", paths["c"], origin=False),
                    fixture.item(entry, "raw", paths["r"], origin=True),
                ],
            )
            fixture.evidence(entry, "a", "c")
            first = fixture.execution(
                entry, "first", {"b": paths["b"]}, {"a": paths["a"]}
            )
            second = fixture.execution(
                entry, "second", {"a": paths["a"]}, {"b": paths["b"]}
            )
            independent = fixture.execution(
                entry, "independent", {"raw": paths["r"]}, {"c": paths["c"]}
            )
            fixture.write_pyrun(entry, [first, second, independent])

            plan = prepare(fixture, entry)
            self.assertEqual(run_ids(plan), [independent[0]])
            self.assertEqual(
                {item.identity.cid: item.selection for item in plan.commands},
                {
                    "first": WorkSelection.BLOCKED,
                    "second": WorkSelection.BLOCKED,
                    "independent": WorkSelection.RUN,
                },
            )
            self.assertEqual(
                {
                    item.identity.artifact
                    for item in plan.artifacts
                    if item.producer is not None
                    and plan.command(item.producer).selection is WorkSelection.BLOCKED
                },
                {"data/a.txt", "data/b.txt"},
            )
            self.assertEqual(
                {item.code for item in plan.problems},
                {"dependency_cycle", "validation_blocked"},
            )

    def test_changed_script_runs_with_its_dependants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            paths = {
                name: entry.root / "data" / f"{name}.txt"
                for name in ("raw", "middle", "final", "independent")
            }
            for name, path in paths.items():
                path.write_text(name, encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", paths["raw"], origin=True),
                    fixture.item(entry, "middle", paths["middle"], origin=False),
                    fixture.item(entry, "final", paths["final"], origin=False),
                    fixture.item(
                        entry,
                        "independent",
                        paths["independent"],
                        origin=False,
                    ),
                ],
            )
            fixture.evidence(entry, "final", "independent")
            upstream = fixture.execution(
                entry,
                "upstream",
                {"raw": paths["raw"]},
                {"middle": paths["middle"]},
            )
            downstream = fixture.execution(
                entry,
                "downstream",
                {"middle": paths["middle"]},
                {"final": paths["final"]},
            )
            independent = fixture.execution(
                entry,
                "independent",
                {"raw": paths["raw"]},
                {"independent": paths["independent"]},
            )
            fixture.write_pyrun(entry, [upstream, downstream, independent])
            (entry.root / upstream[1].recipe.script).write_text(
                "VALUE = 1\n", encoding="utf-8"
            )

            plan = prepare(fixture, entry)
            self.assertCountEqual(
                run_ids(plan),
                [upstream[0], downstream[0], independent[0]],
            )
            self.assertEqual(
                {item.identity.cid: item.selection for item in plan.commands},
                {
                    "upstream": WorkSelection.RUN,
                    "downstream": WorkSelection.RUN,
                    "independent": WorkSelection.RUN,
                },
            )
            self.assertEqual(
                {item.code for item in plan.problems},
                {"effective_code_changed"},
            )
            self.assertEqual(len(plan.problems), 1)
            upstream_work = next(
                item for item in plan.commands if item.identity.cid == "upstream"
            )
            downstream_work = next(
                item for item in plan.commands if item.identity.cid == "downstream"
            )
            self.assertEqual(downstream_work.dependencies, (upstream_work.identity,))
            self.assertFalse(downstream_work.problem_ids)
            self.assertIsNotNone(upstream_work.accepted_source)

    def test_effective_code_owns_currentness_not_raw_script_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data/raw.txt"
            output = entry.root / "data/output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry,
                "build",
                {"raw": raw},
                {"output": output},
                requires_reproduction=False,
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            script = entry.root / execution.recipe.script
            script.write_text("# build\n# raw bytes changed\n", encoding="utf-8")

            raw_only = prepare(fixture, entry)

            self.assertEqual(run_ids(raw_only), [])
            self.assertEqual(
                raw_only.commands[0].selection,
                WorkSelection.NOT_NEEDED,
            )
            self.assertEqual(raw_only.problems, ())

            missing = replace(
                execution,
                requires_reproduction=True,
                observed=replace(execution.observed, effective_code=None),
            )
            fixture.write_pyrun(entry, [(identity, missing)])

            unavailable = prepare(fixture, entry)

            self.assertEqual(run_ids(unavailable), [identity])
            self.assertEqual(unavailable.commands[0].selection, WorkSelection.RUN)
            self.assertIsNotNone(unavailable.commands[0].accepted_source)
            self.assertEqual(
                {item.code for item in unavailable.problems},
                {"effective_code_unavailable"},
            )

            provisional = replace(
                execution,
                requires_reproduction=True,
                observed=replace(
                    execution.observed,
                    script=_fingerprint(script),
                    effective_code=_effective_fingerprint(
                        script, fixture.root.resolve()
                    ),
                ),
            )
            fixture.write_pyrun(entry, [(identity, provisional)])

            selected = prepare(fixture, entry)

            self.assertEqual(run_ids(selected), [identity])
            self.assertEqual(selected.problems, ())

            fixture.write_pyrun(entry, [(identity, execution)])
            script.write_text("from math import *\n", encoding="utf-8")

            unfingerprintable = prepare(fixture, entry)

            self.assertEqual(run_ids(unfingerprintable), [identity])
            work = unfingerprintable.commands[0]
            self.assertEqual(work.selection, WorkSelection.RUN)
            self.assertIsNotNone(work.accepted_source)
            assert work.accepted_source is not None
            self.assertIsNone(work.accepted_source.effective_code)
            problem = unfingerprintable.problems[0]
            self.assertEqual(problem.code, "effective_code_unavailable")
            self.assertEqual(problem.observed["availability"], "unsupported")
            self.assertEqual(problem.locations[0].line, 1)

            script.write_text("def broken(:\n", encoding="utf-8")
            analysis_failed = prepare(fixture, entry)
            self.assertEqual(run_ids(analysis_failed), [identity])
            self.assertEqual(
                analysis_failed.problems[0].observed["availability"],
                "analysis-failed",
            )
            assert analysis_failed.commands[0].accepted_source is not None
            self.assertIsNone(
                analysis_failed.commands[0].accepted_source.effective_code
            )

    def test_missing_direct_input_is_local_to_its_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            final = entry.root / "data" / "final.txt"
            raw.write_text("raw", encoding="utf-8")
            final.write_text("final", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            execution = fixture.execution(
                entry, "analyze", {"raw": raw}, {"final": final}
            )
            fixture.write_pyrun(entry, [execution])
            raw.unlink()

            plan = prepare(fixture, entry)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(plan.commands[0].selection, WorkSelection.BLOCKED)
            self.assertEqual(
                {item.code for item in plan.problems},
                {"validation_blocked", "direct_input_unavailable"},
            )

    def test_shared_changed_code_runs_each_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            first_output = entry.root / "data" / "first.txt"
            second_output = entry.root / "data" / "second.txt"
            shared = entry.root / "scripts" / "shared.py"
            for path, value in (
                (raw, "raw"),
                (first_output, "first"),
                (second_output, "second"),
                (shared, "VALUE = 1\n"),
            ):
                path.write_text(value, encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "first", first_output, origin=False),
                    fixture.item(entry, "second", second_output, origin=False),
                ],
            )
            fixture.evidence(entry, "first", "second")
            executions = []
            for name, output in (
                ("first", first_output),
                ("second", second_output),
            ):
                identity, execution = fixture.execution(
                    entry, name, {"raw": raw}, {name: output}
                )
                script = entry.root / execution.recipe.script
                script.write_text("import shared\n", encoding="utf-8")
                executions.append(
                    (
                        identity,
                        replace(
                            execution,
                            observed=replace(
                                execution.observed,
                                effective_code=_effective_fingerprint(
                                    script, fixture.root.resolve()
                                ),
                            ),
                        ),
                    )
                )
            fixture.write_pyrun(entry, executions)
            shared.write_text("VALUE = 2\n", encoding="utf-8")

            plan = prepare(fixture, entry)
            self.assertCountEqual(run_ids(plan), [item[0] for item in executions])
            self.assertTrue(
                all(item.selection is WorkSelection.RUN for item in plan.commands)
            )
            self.assertEqual(
                {item.code for item in plan.problems}, {"effective_code_changed"}
            )
            self.assertEqual(len(plan.problems), 2)
            self.assertTrue(
                all(
                    len(item.problem_ids) == 1
                    for item in plan.commands
                )
            )

    def test_entry_evidence_from_another_entry_is_skipped_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            producer_entry = fixture.entry(1)
            evidence_entry = fixture.entry(2)
            shared = fixture.root / "shared" / "result.txt"
            shared.parent.mkdir()
            shared.write_text("result\n", encoding="utf-8")
            fixture.write_data(
                producer_entry,
                [fixture.item(producer_entry, "result", shared, origin=False)],
            )
            fixture.write_data(
                evidence_entry,
                [fixture.item(evidence_entry, "result", shared, origin=False)],
            )
            fixture.evidence(evidence_entry, "result")
            execution = fixture.execution(
                producer_entry, "produce", {}, {"result": shared}
            )
            recipe = ExecutionRecipe(
                execution[1].recipe.script,
                ("--output-data", "<project>/shared/result.txt"),
                (),
                (),
                (("<project>/shared/result.txt", "file"),),
                parameter_roles=fixture_parameter_roles(
                    ("--output-data", "<project>/shared/result.txt"),
                    (),
                    (("<project>/shared/result.txt", "file"),),
                ),
            )
            external = PyrunExecution(
                True,
                True,
                None,
                execution[1].runner,
                execution[1].environment_profile,
                execution[1].execution_contract,
                recipe,
                ObservedExecution(
                    execution[1].observed.script,
                    (),
                    execution[1].observed.effective_code,
                    (("<project>/shared/result.txt", _fingerprint(shared)),),
                ),
            )
            external_id = execution_id(recipe)
            fixture.write_pyrun(producer_entry, [(external_id, external)])

            plan = prepare(fixture, evidence_entry)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(plan.commands, ())
            self.assertEqual(plan.artifacts[0].identity.entry, evidence_entry.id)
            self.assertEqual(plan.artifacts[0].boundary["kind"], "cross_entry")
            self.assertEqual(
                plan.artifacts[0].retained_path, shared.resolve().as_posix()
            )
