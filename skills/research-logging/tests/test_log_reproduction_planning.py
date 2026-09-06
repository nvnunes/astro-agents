from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.context import EntryContext, LogContext
from log_commands.model import ActionError
from log_commands.reproduction_planner import _admit_validation, plan_reproduction
from research_log_data import Fingerprint
from validation.engine import RULES_VERSION
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from validation.operation_state import operation_lock
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    execution_id,
)
from validation.source_projection import research_source_projection


def _fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", hashlib.sha256(path.read_bytes()).hexdigest())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class _Fixture:
    def __init__(self, root: Path):
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        self.root = root
        self.summary = root / "docs" / "study.md"
        self.log_root = self.summary.with_suffix("")
        self.log_root.mkdir(parents=True)
        self.summary.write_text("# Study\n", encoding="utf-8")
        self.log = LogContext(self.summary.resolve(), self.log_root.resolve())

    def entry(self, number: int) -> EntryContext:
        entry_id = f"e{number:03d}"
        root = self.log_root / "entries" / f"2026-09-{number:02d}-{entry_id}-study"
        (root / "data").mkdir(parents=True)
        (root / "scripts").mkdir()
        return EntryContext(self.log, entry_id, root.resolve())

    def write_data(self, entry: EntryContext, items: list[dict[str, object]]) -> None:
        _write_json(
            entry.root / "data.json",
            {"inputs": items, "schema": "research-log-data/v3"},
        )

    def item(
        self, entry: EntryContext, name: str, path: Path, *, origin: bool
    ) -> dict[str, object]:
        return {
            "fingerprint": _fingerprint(path).as_dict(),
            "kind": "file",
            "location": os.path.relpath(path, entry.root),
            "name": name,
            "origin": origin,
        }

    def evidence(self, entry: EntryContext, *names: str) -> None:
        (entry.root / f"{entry.id}.md").write_text("# Entry\n", encoding="utf-8")
        _write_json(
            entry.root / "evidence.json",
            {
                "records": [
                    {
                        "document": (f"entries/{entry.root.name}/{entry.id}.md"),
                        "id": f"result-{number}",
                        "kind": "artifact",
                        "sources": [{"locator": None, "source": f"<{name}>"}],
                        "transformation": None,
                    }
                    for number, name in enumerate(names, 1)
                ],
                "schema": "research-log-evidence/v3",
            },
        )

    def execution(
        self,
        entry: EntryContext,
        name: str,
        inputs: dict[str, Path],
        outputs: dict[str, Path],
        *,
        slow: bool = False,
        confirmed: bool = False,
        last_run_at: str | None = None,
    ) -> tuple[str, PyrunExecution]:
        script = entry.root / "scripts" / f"{name}.py"
        script.write_text(f"# {name}\n", encoding="utf-8")
        recipe = ExecutionRecipe(
            f"scripts/{name}.py",
            (),
            (),
            tuple(sorted(inputs)),
            tuple(sorted((f"data/{path.name}", "file") for path in outputs.values())),
        )
        observed = ObservedExecution(
            _fingerprint(script),
            tuple(sorted((key, _fingerprint(path)) for key, path in inputs.items())),
            (),
            tuple(
                sorted(
                    (f"data/{path.name}", _fingerprint(path))
                    for path in outputs.values()
                )
            ),
        )
        execution = PyrunExecution(
            confirmed,
            slow,
            last_run_at,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/1",
            recipe,
            observed,
        )
        return execution_id(recipe), execution

    def write_pyrun(
        self, entry: EntryContext, executions: list[tuple[str, PyrunExecution]]
    ) -> None:
        state = PyrunFile(
            entry.root / "pyrun.json",
            entry.root,
            dict(executions),
        )
        (entry.root / "pyrun.json").write_text(state.serialized(), encoding="utf-8")


def _plan(
    fixture: _Fixture,
    entry: EntryContext,
    *,
    include_slow: bool = False,
):
    admission = _admission(fixture)
    with mock.patch(
        "log_commands.reproduction_planner._admit_validation",
        return_value=(admission, mock.sentinel.record),
    ):
        return plan_reproduction(fixture.log, entry=entry, include_slow=include_slow)


def _admission(fixture: _Fixture) -> dict[str, object]:
    path = fixture.log_root / "validation" / "results.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text("fixture\n", encoding="utf-8")
    digest, _ = research_source_projection(fixture.summary)
    return {
        "result_date": "2026-09-06",
        "result_digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        "result_path": "validation/results.json",
        "rules_version": "fixture/1",
        "source_projection_digest": digest,
    }


class ReproductionPlanningTests(unittest.TestCase):
    def test_default_stops_at_slow_boundary_and_include_slow_runs_it(self) -> None:
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
            slow = fixture.execution(
                entry, "simulate", {"raw": raw}, {"seed": seed}, slow=True
            )
            analysis = fixture.execution(
                entry, "analyze", {"seed": seed}, {"final": final}
            )
            fixture.write_pyrun(entry, [slow, analysis])

            ordinary = _plan(fixture, entry)
            self.assertEqual(
                [value["execution_id"] for value in ordinary.executions],
                [analysis[0]],
            )
            self.assertEqual(
                [(value["kind"], value["name"]) for value in ordinary.boundaries],
                [("slow", "seed")],
            )
            self.assertFalse((fixture.log_root / "reproduction").exists())
            self.assertFalse((fixture.log_root / ".cache").exists())

            complete = _plan(fixture, entry, include_slow=True)
            self.assertEqual(
                [value["execution_id"] for value in complete.executions],
                [slow[0], analysis[0]],
            )
            self.assertEqual(
                [value["kind"] for value in complete.boundaries], ["origin"]
            )

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

            plan = _plan(fixture, entry)

            self.assertEqual(
                [value["kind"] for value in plan.boundaries], ["cross_entry"]
            )
            authority = {
                value["path"] for value in plan.source_snapshot["authority_files"]
            }
            self.assertTrue(all(entry.root.name in value for value in authority))
            self.assertFalse(
                any(upstream_entry.root.name in value for value in authority)
            )

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

            plan = _plan(fixture, entry)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions], [independent[0]]
            )
            cycle_artifacts = {
                value["artifact"]
                for value in plan.failures
                if value["reason"] == "dependency_cycle"
            }
            self.assertEqual(cycle_artifacts, {"data/a.txt", "data/b.txt"})

    def test_current_matched_result_is_not_selected_again(self) -> None:
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
                entry,
                "analyze",
                {"raw": raw},
                {"final": final},
                confirmed=True,
                last_run_at="2026-09-06T00:00:00Z",
            )
            fixture.write_pyrun(entry, [execution])
            _write_json(
                fixture.log_root / "reproduction" / "results.json",
                {
                    "artifacts": [
                        {
                            "artifact": "data/final.txt",
                            "entry": entry.id,
                            "outcome": "matched",
                            "recorded_at": "2026-09-06T00:01:00Z",
                        }
                    ],
                    "schema": "research-log-reproduction-result/1",
                },
            )

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.cases[0]["disposition"], "current")

    def test_changed_source_during_dry_run_is_rejected(self) -> None:
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
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "analyze", {"raw": raw}, {"final": final})],
            )
            admission = _admission(fixture)
            digest, projection = research_source_projection(fixture.summary)
            changed = projection + (("changed", (1, 1, 1, 1, 1, 1)),)
            with (
                mock.patch(
                    "log_commands.reproduction_planner._admit_validation",
                    return_value=(admission, mock.sentinel.record),
                ),
                mock.patch(
                    "log_commands.reproduction_planner.research_source_projection",
                    side_effect=[(digest, projection), ("b" * 64, changed)],
                ),
            ):
                with self.assertRaisesRegex(ActionError, "source changed"):
                    plan_reproduction(fixture.log, entry=entry, include_slow=False)

    def test_log_target_orders_cross_entry_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            first_entry = fixture.entry(1)
            second_entry = fixture.entry(2)
            raw = first_entry.root / "data" / "raw.txt"
            shared = fixture.root / "shared" / "upstream.txt"
            final = second_entry.root / "data" / "final.txt"
            shared.parent.mkdir()
            raw.write_text("raw", encoding="utf-8")
            shared.write_text("shared", encoding="utf-8")
            final.write_text("final", encoding="utf-8")
            fixture.write_data(
                first_entry,
                [
                    fixture.item(first_entry, "raw", raw, origin=True),
                    fixture.item(first_entry, "shared", shared, origin=False),
                ],
            )
            fixture.write_data(
                second_entry,
                [
                    fixture.item(second_entry, "shared", shared, origin=False),
                    fixture.item(second_entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(second_entry, "final")
            upstream = fixture.execution(
                first_entry, "upstream", {"raw": raw}, {"shared": shared}
            )
            # The helper emits entry-local output identities, so use a project
            # output recipe for the shared cross-entry material.
            upstream_recipe = ExecutionRecipe(
                upstream[1].recipe.script,
                (),
                (),
                ("raw",),
                (("<project>/shared/upstream.txt", "file"),),
            )
            upstream_execution = PyrunExecution(
                False,
                False,
                None,
                upstream[1].runner,
                upstream[1].environment_profile,
                upstream[1].execution_contract,
                upstream_recipe,
                ObservedExecution(
                    upstream[1].observed.script,
                    upstream[1].observed.inputs,
                    (),
                    (("<project>/shared/upstream.txt", _fingerprint(shared)),),
                ),
            )
            upstream = (execution_id(upstream_recipe), upstream_execution)
            downstream = fixture.execution(
                second_entry, "downstream", {"shared": shared}, {"final": final}
            )
            fixture.write_pyrun(first_entry, [upstream])
            fixture.write_pyrun(second_entry, [downstream])
            admission = _admission(fixture)
            with mock.patch(
                "log_commands.reproduction_planner._admit_validation",
                return_value=(admission, mock.sentinel.record),
            ):
                plan = plan_reproduction(fixture.log, entry=None, include_slow=False)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [upstream[0], downstream[0]],
            )
            self.assertFalse(
                any(value["kind"] == "cross_entry" for value in plan.boundaries)
            )

    def test_validation_admission_allows_only_unconfirmed_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            fixture.entry(1)
            unconfirmed = MechanicalCheck(
                "provenance:e001:result",
                CheckScope.PROVENANCE,
                CheckStatus.FAIL,
                "/result.csv",
                failure=FailurePayload(
                    "provenance.output.unconfirmed",
                    "/result.csv",
                    {"output": "data/result.csv", "producer": "fixture"},
                    "Pyrun Output Support Records",
                ),
            )
            record = MechanicalGeneratedRecord.build(
                fixture.summary.resolve().as_posix(),
                RULES_VERSION,
                "2026-09-06",
                (
                    MechanicalCheck(
                        "conformance:log",
                        CheckScope.CONFORMANCE,
                        CheckStatus.PASS,
                        "conformance:log",
                    ),
                    MechanicalCheck(
                        "evidence:e001:result",
                        CheckScope.EVIDENCE,
                        CheckStatus.PASS,
                        "evidence:e001:result",
                    ),
                    unconfirmed,
                ),
            )
            path = fixture.log_root / "validation" / "results.json"
            path.parent.mkdir()
            path.write_text(record.canonical_json() + "\n", encoding="utf-8")

            with mock.patch(
                "log_commands.reproduction_planner.evaluate_current_record",
                return_value=record,
            ):
                snapshot, admitted = _admit_validation(fixture.log)

            self.assertEqual(admitted, record)
            self.assertEqual(snapshot["rules_version"], RULES_VERSION)

    def test_equal_execution_ids_in_distinct_entries_remain_distinct_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            executions: list[tuple[EntryContext, tuple[str, PyrunExecution]]] = []
            for number in (1, 2):
                entry = fixture.entry(number)
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
                executions.append((entry, execution))
            self.assertEqual(executions[0][1][0], executions[1][1][0])
            admission = _admission(fixture)
            with mock.patch(
                "log_commands.reproduction_planner._admit_validation",
                return_value=(admission, mock.sentinel.record),
            ):
                plan = plan_reproduction(fixture.log, entry=None, include_slow=False)

            self.assertEqual(len(plan.executions), 2)
            self.assertEqual(
                [value["entry"] for value in plan.executions], ["e001", "e002"]
            )
            self.assertEqual(
                len({value["execution_id"] for value in plan.executions}), 1
            )

    def test_existing_overlapping_entry_lock_blocks_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            with operation_lock(fixture.log_root, "entry-e001.lock"):
                with self.assertRaisesRegex(ActionError, "active operation"):
                    plan_reproduction(fixture.log, entry=entry, include_slow=False)


if __name__ == "__main__":
    unittest.main()
