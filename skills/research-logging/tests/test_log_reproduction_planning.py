from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from log_commands.context import EntryContext, LogContext
from log_commands.model import ActionError
from log_commands.reproduction_planner import (
    RECHECK_SELECTION,
    SelectionPolicy,
    _admit_validation,
    plan_reproduction,
    project_reproduction_state,
    verify_reproduction_runtime_snapshot,
)
from research_log_data import (
    Fingerprint,
    InputResource,
    observe_fingerprint,
)
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
from validation.repair_batches import build_repair_batches
from validation.source_projection import research_source_projection


def _fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", hashlib.sha256(path.read_bytes()).hexdigest())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _projected_command(
    *, outputs: tuple[str, ...], inputs: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "collections": [],
        "inputs": [
            {"direction": "input", "path": value, "proof": "fixture"}
            for value in inputs
        ],
        "outputs": [
            {"direction": "output", "path": value, "proof": "fixture"}
            for value in outputs
        ],
    }


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
        auto_reproduce: bool = True,
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
            auto_reproduce,
            last_run_at,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
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
    include_all: bool = False,
    recheck: bool = False,
):
    admission = _admission(fixture)
    with mock.patch(
        "log_commands.reproduction_planner._admit_validation",
        return_value=(admission, mock.sentinel.record),
    ):
        return plan_reproduction(
            fixture.log,
            entry=entry,
            include_all=include_all,
            selection_policy=RECHECK_SELECTION if recheck else "incremental",
        )


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


def _write_projection(
    fixture: _Fixture,
    record: MechanicalGeneratedRecord,
    *,
    unresolved: list[dict[str, object]],
) -> None:
    body: dict[str, object] = {
        "chains": [],
        "record_identity": hashlib.sha256(
            record.canonical_json().encode("utf-8")
        ).hexdigest(),
        "result_date": record.result_date,
        "rules_version": record.rules_version,
        "schema": "research-log-published-validation/2",
        "source_identity": "source",
        "summary": record.summary,
        "unresolved": unresolved,
    }
    body["repair_batches"] = build_repair_batches(record, [])
    body["validation_id"] = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    (fixture.log_root / "validation" / "batches.json").write_text(
        json.dumps(body) + "\n", encoding="utf-8"
    )


class ReproductionPlanningTests(unittest.TestCase):
    def test_fresh_incremental_run_retries_a_prior_failed_automatic_case(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            final = entry.root / "data" / "final.txt"
            raw.write_text("raw", encoding="utf-8")
            final.write_text("retained", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            identity, execution = fixture.execution(
                entry,
                "produce",
                {"raw": raw},
                {"final": final},
                confirmed=True,
            )
            fixture.write_pyrun(entry, [(identity, execution)])

            with mock.patch(
                "log_commands.reproduction_planner._load_prior_results",
                return_value={
                    (entry.id, "data/final.txt"): {
                        "outcome": "failed",
                        "recorded_at": "2030-01-01T00:00:00Z",
                    }
                },
            ):
                plan = _plan(fixture, entry)

            self.assertEqual(
                [item["execution_id"] for item in plan.executions], [identity]
            )

    def test_batch_admission_keeps_independent_work_and_blocks_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)

            def owner(identity: str, output: str):
                value = mock.Mock()
                value.entry.context = entry
                value.execution_id = identity
                value.execution.recipe.outputs = ((output, "file"),)
                return value

            blocked = owner("blocked", "data/blocked.csv")
            independent = owner("independent", "data/independent.csv")
            dependent = owner("dependent", "data/dependent.csv")
            state = mock.Mock()
            state.project_root = fixture.root
            state.selected = {
                ("e001", "blocked"): blocked,
                ("e001", "dependent"): dependent,
                ("e001", "independent"): independent,
            }
            state.blocked = set()
            state.admitted_batches = set()
            state.excluded_batches = {}
            state.failures = {}
            state.cases = {}
            state.dependencies = {
                ("e001", "dependent"): {("e001", "blocked")},
            }
            state.cycle_members = set()
            projection = {
                "chains": [
                    {
                        "artifacts": [(entry.root / "data" / "blocked.csv").as_posix()],
                        "chain_id": "blocked-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (entry.root / "data" / "blocked.csv").as_posix(),
                                )
                            )
                        ],
                        "entry": "e001",
                        "findings": [
                            {
                                "admission_effect": "chain",
                                "affected_chains": ["blocked-chain"],
                                "affected_entries": ["e001"],
                                "code": "lineage.missing",
                                "identity": "provenance:e001:blocked",
                                "scope": "provenance",
                                "status": "fail",
                            }
                        ],
                    },
                    {
                        "artifacts": [
                            (entry.root / "data" / "dependent.csv").as_posix()
                        ],
                        "chain_id": "dependent-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (entry.root / "data" / "dependent.csv").as_posix(),
                                )
                            )
                        ],
                        "entry": "e001",
                        "findings": [],
                    },
                    {
                        "artifacts": [
                            (entry.root / "data" / "independent.csv").as_posix()
                        ],
                        "chain_id": "independent-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (
                                        entry.root / "data" / "independent.csv"
                                    ).as_posix(),
                                )
                            )
                        ],
                        "entry": "e001",
                        "findings": [
                            {
                                "admission_effect": "none",
                                "affected_chains": [],
                                "affected_entries": [],
                                "code": "orphan.material.unused",
                                "identity": "orphan:e001:independent",
                                "scope": "orphan",
                                "status": "fail",
                            }
                        ],
                    },
                ],
                "unresolved": [],
            }
            from log_commands.reproduction_planner import (
                _apply_cycle_and_dependency_failures,
                _apply_validation_admission,
            )

            _apply_validation_admission(state, projection)
            _apply_cycle_and_dependency_failures(state)

            self.assertEqual(
                state.blocked,
                {("e001", "blocked"), ("e001", "dependent")},
            )
            self.assertIn(("e001", "independent-chain"), state.admitted_batches)
            self.assertEqual(
                state.excluded_batches[("e001", "blocked-chain")],
                ("provenance:e001:blocked",),
            )

    def test_entry_unresolved_blocker_preserves_independent_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            first = fixture.entry(1)
            second = fixture.entry(2)

            def owner(entry: EntryContext, identity: str, output: str):
                value = mock.Mock()
                value.entry.context = entry
                value.execution_id = identity
                value.execution.recipe.outputs = ((output, "file"),)
                return value

            blocked = owner(first, "blocked", "data/blocked.csv")
            independent = owner(second, "independent", "data/independent.csv")
            state = mock.Mock()
            state.project_root = fixture.root
            state.selected = {
                ("e001", "blocked"): blocked,
                ("e002", "independent"): independent,
            }
            state.blocked = set()
            state.admitted_batches = set()
            state.excluded_batches = {}
            state.failures = {}
            state.cases = {}
            projection = {
                "chains": [
                    {
                        "artifacts": [
                            (second.root / "data" / "independent.csv").as_posix()
                        ],
                        "chain_id": "independent-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (
                                        second.root / "data" / "independent.csv"
                                    ).as_posix(),
                                )
                            )
                        ],
                        "entry": "e002",
                        "findings": [],
                    }
                ],
                "unresolved": [
                    {
                        "chain_id": "unresolved-entry",
                        "entry": "e001b",
                        "findings": [
                            {
                                "admission_effect": "entry",
                                "affected_chains": [],
                                "affected_entries": ["e001"],
                                "code": "lineage.missing",
                                "identity": "provenance:e001:blocked",
                                "scope": "provenance",
                                "status": "fail",
                            }
                        ],
                    }
                ],
            }

            from log_commands.reproduction_planner import _apply_validation_admission

            _apply_validation_admission(state, projection)

            self.assertEqual(state.blocked, {("e001", "blocked")})
            self.assertEqual(state.admitted_batches, {("e002", "independent-chain")})
            self.assertEqual(
                state.excluded_batches,
                {("e001", "unresolved-entry"): ("provenance:e001:blocked",)},
            )

    def test_subdocument_chains_admit_and_block_their_physical_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)

            def owner(identity: str, output: str):
                value = mock.Mock()
                value.entry.context = entry
                value.execution_id = identity
                value.execution.recipe.outputs = ((output, "file"),)
                return value

            admitted = owner("admitted", "data/admitted.csv")
            blocked = owner("blocked", "data/blocked.csv")
            state = mock.Mock()
            state.project_root = fixture.root
            state.selected = {
                ("e001", "admitted"): admitted,
                ("e001", "blocked"): blocked,
            }
            state.blocked = set()
            state.admitted_batches = set()
            state.excluded_batches = {}
            state.failures = {}
            state.cases = {}
            projection = {
                "chains": [
                    {
                        "artifacts": [
                            (entry.root / "data" / "admitted.csv").as_posix()
                        ],
                        "chain_id": "admitted-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (entry.root / "data" / "admitted.csv").as_posix(),
                                )
                            )
                        ],
                        "entry": "e001a",
                        "findings": [],
                    },
                    {
                        "artifacts": [(entry.root / "data" / "blocked.csv").as_posix()],
                        "chain_id": "blocked-chain",
                        "commands": [
                            _projected_command(
                                outputs=(
                                    (entry.root / "data" / "blocked.csv").as_posix(),
                                )
                            )
                        ],
                        "entry": "e001b",
                        "findings": [
                            {
                                "admission_effect": "chain",
                                "affected_chains": ["blocked-chain"],
                                "affected_entries": ["e001"],
                                "code": "lineage.missing",
                                "identity": "provenance:e001b:blocked",
                                "scope": "provenance",
                                "status": "fail",
                            }
                        ],
                    },
                ],
                "unresolved": [],
            }

            from log_commands.reproduction_planner import _apply_validation_admission

            _apply_validation_admission(state, projection)

            self.assertEqual(state.blocked, {("e001", "blocked")})
            self.assertEqual(state.admitted_batches, {("e001", "admitted-chain")})
            self.assertEqual(
                state.excluded_batches,
                {("e001", "blocked-chain"): ("provenance:e001b:blocked",)},
            )

    def test_subdocument_consumer_presence_does_not_create_a_chain_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            owner = mock.Mock()
            owner.entry.context = entry
            owner.execution_id = "selected"
            owner.execution.recipe.outputs = (("data/result.csv", "file"),)
            target = (entry.root / "data" / "result.csv").as_posix()
            final = (entry.root / "data" / "final.csv").as_posix()
            state = mock.Mock()
            state.project_root = fixture.root
            state.selected = {("e001", "selected"): owner}
            state.blocked = set()
            state.admitted_batches = set()
            state.excluded_batches = {}
            state.failures = {}
            state.cases = {}
            projection = {
                "chains": [
                    {
                        "artifacts": [target],
                        "chain_id": "producer-chain",
                        "commands": [_projected_command(outputs=(target,))],
                        "entry": "e001a",
                        "findings": [],
                    },
                    {
                        "artifacts": [target, final],
                        "chain_id": "consumer-chain",
                        "commands": [
                            _projected_command(outputs=(final,), inputs=(target,))
                        ],
                        "entry": "e001b",
                        "findings": [],
                    },
                ],
                "unresolved": [],
            }

            from log_commands.reproduction_planner import _apply_validation_admission

            _apply_validation_admission(state, projection)

            self.assertEqual(state.admitted_batches, {("e001", "producer-chain")})

    def test_subdocument_producer_matching_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            owner = mock.Mock()
            owner.entry.context = entry
            owner.execution_id = "selected"
            owner.execution.recipe.outputs = (("data/result.csv", "file"),)
            target = (entry.root / "data" / "result.csv").as_posix()

            for label, chains, expected in (
                (
                    "absent",
                    [
                        {
                            "artifacts": [
                                (entry.root / "data" / "other.csv").as_posix()
                            ],
                            "chain_id": "other-chain",
                            "commands": [
                                _projected_command(
                                    outputs=(
                                        (entry.root / "data" / "other.csv").as_posix(),
                                    ),
                                    inputs=(target,),
                                )
                            ],
                            "entry": "e001a",
                            "findings": [],
                        }
                    ],
                    "0 projected batch matches",
                ),
                (
                    "ambiguous",
                    [
                        {
                            "artifacts": [target],
                            "chain_id": "first-chain",
                            "commands": [_projected_command(outputs=(target,))],
                            "entry": "e001a",
                            "findings": [],
                        },
                        {
                            "artifacts": [target],
                            "chain_id": "second-chain",
                            "commands": [_projected_command(outputs=(target,))],
                            "entry": "e001b",
                            "findings": [],
                        },
                    ],
                    "2 projected batch matches",
                ),
            ):
                with self.subTest(label=label):
                    state = mock.Mock()
                    state.project_root = fixture.root
                    state.selected = {("e001", "selected"): owner}
                    state.blocked = set()
                    state.admitted_batches = set()
                    state.excluded_batches = {}
                    state.failures = {}
                    state.cases = {}

                    from log_commands.reproduction_planner import (
                        _apply_validation_admission,
                    )

                    with self.assertRaisesRegex(ActionError, expected):
                        _apply_validation_admission(
                            state, {"chains": chains, "unresolved": []}
                        )

    def test_unknown_selection_policy_is_rejected_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))

            with self.assertRaisesRegex(ActionError, "selection policy"):
                plan_reproduction(
                    fixture.log,
                    entry=None,
                    include_all=False,
                    selection_policy=cast(SelectionPolicy, "unsupported"),
                )

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

            ordinary = _plan(fixture, entry)
            self.assertEqual(
                [value["execution_id"] for value in ordinary.executions],
                [analysis[0]],
            )
            self.assertEqual(
                [(value["kind"], value["name"]) for value in ordinary.boundaries],
                [("non_automatic", "seed")],
            )
            self.assertFalse((fixture.log_root / "reproduction").exists())
            self.assertFalse((fixture.log_root / ".cache").exists())

            complete = _plan(fixture, entry, include_all=True)
            self.assertEqual(
                [value["execution_id"] for value in complete.executions],
                [excluded[0], analysis[0]],
            )
            recheck = _plan(fixture, entry, recheck=True)
            self.assertEqual(
                [value["execution_id"] for value in recheck.executions],
                [analysis[0]],
            )
            complete_recheck = _plan(fixture, entry, include_all=True, recheck=True)
            self.assertEqual(
                [value["execution_id"] for value in complete_recheck.executions],
                [excluded[0], analysis[0]],
            )
            self.assertEqual(
                [value["kind"] for value in complete.boundaries], ["origin"]
            )

    def test_directory_output_member_creates_execution_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            bundle = entry.root / "data" / "bundle"
            member = bundle / "member.txt"
            final = entry.root / "data" / "final.txt"
            raw.write_text("raw\n", encoding="utf-8")
            bundle.mkdir()
            member.write_text("member\n", encoding="utf-8")
            final.write_text("final\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "member", member, origin=False),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            producer_script = entry.root / "scripts" / "produce.py"
            producer_script.write_text("# produce\n", encoding="utf-8")
            producer_recipe = ExecutionRecipe(
                "scripts/produce.py",
                (),
                (),
                ("raw",),
                (("data/bundle", "directory"),),
            )
            producer = (
                execution_id(producer_recipe),
                PyrunExecution(
                    False,
                    True,
                    None,
                    "research-log-pyrun-runner/1",
                    "pyrun-standard/v1",
                    "research-log-pyrun-execution/2",
                    producer_recipe,
                    ObservedExecution(
                        _fingerprint(producer_script),
                        (("raw", _fingerprint(raw)),),
                        (),
                        (
                            (
                                "data/bundle",
                                observe_fingerprint(
                                    InputResource(
                                        "bundle",
                                        "directory",
                                        "data/bundle",
                                        Fingerprint("directory-sha256-v1", "0" * 64),
                                        False,
                                        bundle.resolve().as_posix(),
                                    )
                                ).fingerprint,
                            ),
                        ),
                    ),
                ),
            )
            consumer = fixture.execution(
                entry, "consume", {"member": member}, {"final": final}
            )
            fixture.write_pyrun(entry, [producer, consumer])

            plan = _plan(fixture, entry)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [producer[0], consumer[0]],
            )
            self.assertEqual(
                plan.executions[1]["depends_on"],
                [f"{entry.id}:{producer[0]}"],
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
            admission = _admission(fixture)

            with mock.patch(
                "log_commands.reproduction_planner._admit_validation",
                return_value=(admission, mock.sentinel.record),
            ):
                plan = plan_reproduction(fixture.log, entry=None, include_all=False)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [independent[0]],
            )
            self.assertEqual(
                [
                    (value["artifact"], value["outcome"], value["reason"])
                    for value in plan.failures
                ],
                [(external.as_posix(), "failed", "cross_log_generated_input")],
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

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.failures, ())
            self.assertEqual(
                [(value["artifact"], value["kind"]) for value in plan.boundaries],
                [(external.as_posix(), "origin")],
            )

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

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(
                [(value["disposition"], value["reason"]) for value in plan.cases],
                [("skipped", "non_automatic")],
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
            self.assertTrue(
                all(
                    value["disposition"] == "failed"
                    for value in plan.cases
                    if value["reason"] == "dependency_cycle"
                )
            )

    def test_changed_script_blocks_only_its_dependants(self) -> None:
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
                "# changed\n", encoding="utf-8"
            )

            plan = _plan(fixture, entry)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [independent[0]],
            )
            self.assertEqual(
                {
                    (value["artifact"], value["disposition"], value["reason"])
                    for value in plan.cases
                },
                {
                    ("data/middle.txt", "failed", "script_changed"),
                    ("data/final.txt", "skipped", "dependency_failed"),
                    ("data/independent.txt", "run", None),
                },
            )
            snapshotted = {
                value["execution_id"] for value in plan.source_snapshot["executions"]
            }
            self.assertNotIn(upstream[0], snapshotted)
            self.assertIn(independent[0], snapshotted)

    def test_missing_baseline_can_produce_a_valid_no_work_plan(self) -> None:
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
            final.unlink()

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.cases[0]["disposition"], "failed")
            self.assertEqual(plan.cases[0]["reason"], "baseline_unavailable")

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

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.cases[0]["reason"], "direct_input_unavailable")

    def test_shared_changed_code_blocks_each_consumer(self) -> None:
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
                (shared, "# shared"),
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
                executions.append(
                    (
                        identity,
                        replace(
                            execution,
                            observed=replace(
                                execution.observed,
                                code=(("scripts/shared.py", _fingerprint(shared)),),
                            ),
                        ),
                    )
                )
            fixture.write_pyrun(entry, executions)
            shared.write_text("# changed", encoding="utf-8")

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(
                {value["reason"] for value in plan.cases},
                {"participating_code_changed"},
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
                (),
                (),
                (),
                (("<project>/shared/result.txt", "file"),),
            )
            external = PyrunExecution(
                False,
                True,
                None,
                execution[1].runner,
                execution[1].environment_profile,
                execution[1].execution_contract,
                recipe,
                ObservedExecution(
                    execution[1].observed.script,
                    (),
                    (),
                    (("<project>/shared/result.txt", _fingerprint(shared)),),
                ),
            )
            external_id = execution_id(recipe)
            fixture.write_pyrun(producer_entry, [(external_id, external)])

            plan = _plan(fixture, evidence_entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(
                [
                    (value["entry"], value["disposition"], value["reason"])
                    for value in plan.cases
                ],
                [(evidence_entry.id, "skipped", "outside_entry")],
            )
            self.assertEqual(plan.cases[0]["execution_id"], external_id)
            projection = project_reproduction_state(fixture.log)
            self.assertIn(
                (evidence_entry.id, "<project>/shared/result.txt"),
                projection.reachable,
            )

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
                            "comparison": {
                                "contract": "research-log-reproduction-comparison/1",
                                "expected": _fingerprint(final).as_dict(),
                                "profile": "text",
                                "regenerated": _fingerprint(final).as_dict(),
                            },
                            "entry": entry.id,
                            "execution_id": execution[0],
                            "outcome": "matched",
                            "reason": None,
                            "recorded_at": "2026-09-06T00:01:00Z",
                            "run_id": "reproduce-20260906t000000z-current",
                        }
                    ],
                    "runs": [
                        {
                            "accepted_at": "2026-09-06T00:00:00Z",
                            "artifact_outcomes": {
                                "changed": 0,
                                "comparison_failed": 0,
                                "failed": 0,
                                "matched": 1,
                                "skipped": 0,
                            },
                            "finished_at": "2026-09-06T00:01:00Z",
                            "folder": {
                                "availability": "unknown",
                                "path": (
                                    "tmp/reproduction/2030-01-01/"
                                    "reproduce-study-current"
                                ),
                            },
                            "executions": [],
                            "include_all": False,
                            "run_id": "reproduce-20260906t000000z-current",
                            "status": "complete",
                            "target": {"entry": entry.id, "kind": "entry"},
                        }
                    ],
                    "schema": "research-log-reproduction-result/2",
                    "summary": "docs/study.md",
                    "updated_at": "2026-09-06T00:01:00Z",
                },
            )

            plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.cases[0]["disposition"], "current")

            recheck = _plan(fixture, entry, recheck=True)
            self.assertEqual(
                [value["execution_id"] for value in recheck.executions],
                [execution[0]],
            )
            self.assertEqual(recheck.cases[0]["disposition"], "run")
            self.assertEqual(
                set(json.loads(recheck.serialized())),
                {
                    "boundaries",
                    "cases",
                    "executions",
                    "failures",
                    "include_all",
                    "jobs",
                    "schema",
                    "source_snapshot",
                    "summary",
                    "target",
                    "validation_snapshot",
                },
            )

            projection = project_reproduction_state(fixture.log)
            self.assertEqual(
                projection.reachable, frozenset({(entry.id, "data/final.txt")})
            )
            self.assertEqual(
                projection.output_executions[(entry.id, "data/final.txt")],
                execution[0],
            )

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
                    plan_reproduction(fixture.log, entry=entry, include_all=False)

    def test_runtime_snapshot_allows_only_confirmation_change(self) -> None:
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
            identity, execution = fixture.execution(
                entry, "analyze", {"raw": raw}, {"final": final}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)

            fixture.write_pyrun(entry, [(identity, replace(execution, confirmed=True))])
            verify_reproduction_runtime_snapshot(fixture.log, plan)

            fixture.write_pyrun(
                entry,
                [
                    (
                        identity,
                        replace(execution, confirmed=True, auto_reproduce=False),
                    )
                ],
            )
            with self.assertRaisesRegex(ActionError, "execution recipe changed"):
                verify_reproduction_runtime_snapshot(fixture.log, plan)

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
                True,
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
                plan = plan_reproduction(fixture.log, entry=None, include_all=False)
                recheck = plan_reproduction(
                    fixture.log,
                    entry=None,
                    include_all=False,
                    selection_policy=RECHECK_SELECTION,
                )

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [upstream[0], downstream[0]],
            )
            self.assertFalse(
                any(value["kind"] == "cross_entry" for value in plan.boundaries)
            )
            self.assertEqual(recheck.executions, plan.executions)
            self.assertEqual(recheck.target, {"entry": None, "kind": "log"})

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
            _write_projection(
                fixture,
                record,
                unresolved=[
                    {
                        "chain_id": "unresolved-unconfirmed",
                        "entry": "e001",
                        "findings": [
                            {
                                "admission_effect": "none",
                                "affected_chains": [],
                                "affected_entries": [],
                                "code": "provenance.output.unconfirmed",
                                "dependencies": [],
                                "identity": unconfirmed.identity,
                                "observed": dict(unconfirmed.failure.observed),
                                "rule": unconfirmed.failure.rule,
                                "scope": "provenance",
                                "status": "fail",
                                "subject": unconfirmed.subject,
                            }
                        ],
                        "reason": "finding_scope_unresolved",
                    }
                ],
            )

            with mock.patch(
                "log_commands.reproduction_planner.evaluate_current_record",
                return_value=record,
            ):
                snapshot, admitted, projection = _admit_validation(fixture.log)

            self.assertEqual(admitted, record)
            self.assertEqual(snapshot["rules_version"], RULES_VERSION)
            self.assertEqual(
                projection["schema"], "research-log-published-validation/2"
            )

    def test_validation_admission_blocks_graph_failure_beside_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            fixture.entry(1)
            artifact = "/result.csv"
            checks = (
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
                MechanicalCheck(
                    "provenance:e001:result",
                    CheckScope.PROVENANCE,
                    CheckStatus.FAIL,
                    artifact,
                    ({"artifacts": [artifact]},),
                    FailurePayload(
                        "lineage.missing",
                        artifact,
                        {"consumer": "fixture"},
                        "Recorded-Command Provenance And Material Graph",
                    ),
                ),
                MechanicalCheck(
                    "provenance:e001:result:finding:1",
                    CheckScope.PROVENANCE,
                    CheckStatus.FAIL,
                    artifact,
                    ({"artifacts": [artifact]},),
                    FailurePayload(
                        "provenance.output.unconfirmed",
                        artifact,
                        {"output": "data/result.csv", "producer": "fixture"},
                        "Pyrun Output Support Records",
                    ),
                ),
            )
            record = MechanicalGeneratedRecord.build(
                fixture.summary.resolve().as_posix(),
                RULES_VERSION,
                "2026-09-06",
                checks,
            )
            path = fixture.log_root / "validation" / "results.json"
            path.parent.mkdir()
            path.write_text(record.canonical_json() + "\n", encoding="utf-8")
            blocking = checks[2]
            assert blocking.failure is not None
            _write_projection(
                fixture,
                record,
                unresolved=[
                    {
                        "chain_id": "unresolved-blocked",
                        "entry": "log",
                        "findings": [
                            {
                                "admission_effect": "log",
                                "affected_chains": [],
                                "affected_entries": [],
                                "code": blocking.failure.code,
                                "dependencies": [],
                                "identity": blocking.identity,
                                "observed": dict(blocking.failure.observed),
                                "rule": blocking.failure.rule,
                                "scope": "provenance",
                                "status": "fail",
                                "subject": blocking.subject,
                            }
                        ],
                        "reason": "finding_scope_unresolved",
                    }
                ],
            )

            with mock.patch(
                "log_commands.reproduction_planner.evaluate_current_record",
                return_value=record,
            ):
                _snapshot, _record, projection = _admit_validation(fixture.log)
            state = mock.Mock()
            state.selected = {}
            state.blocked = set()
            state.admitted_batches = set()
            state.excluded_batches = {}
            with self.assertRaisesRegex(ActionError, "no safe batch scope"):
                from log_commands.reproduction_planner import (
                    _apply_validation_admission,
                )

                _apply_validation_admission(state, projection)

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
                plan = plan_reproduction(fixture.log, entry=None, include_all=False)

            self.assertEqual(len(plan.executions), 2)
            self.assertEqual(
                [value["entry"] for value in plan.executions], ["e001", "e002"]
            )
            self.assertEqual(
                len({value["execution_id"] for value in plan.executions}), 1
            )
            for value in plan.executions:
                entry = value["entry"]
                digest = str(value["execution_id"]).rsplit(":", 1)[-1]
                self.assertEqual(
                    value["run_path"], f"<run>/executions/{entry}/{digest}"
                )
                self.assertIn(
                    f"<run>/diagnostics/{entry}/{digest}",
                    value["writable_paths"],
                )
                self.assertIn(
                    f"<run>/runtime/{entry}/{digest}", value["writable_paths"]
                )

    def test_existing_overlapping_entry_lock_blocks_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            with operation_lock(fixture.log_root, "entry-e001.lock"):
                with self.assertRaisesRegex(ActionError, "active operation"):
                    plan_reproduction(fixture.log, entry=entry, include_all=False)


if __name__ == "__main__":
    unittest.main()
