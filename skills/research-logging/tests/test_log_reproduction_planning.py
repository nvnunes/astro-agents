from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Mapping, cast
from unittest import mock

from log_commands.context import EntryContext, LogContext
from log_commands.model import ActionError
from log_commands.reproduction_accounting import project_command_selection
from log_commands.reproduction_contract import ReproductionPlan, ReproductionRuntime
from log_commands.reproduction_planner import (
    RECHECK_SELECTION,
    ReproductionSelection,
    SelectionPolicy,
    plan_reproduction,
    prepare_reproduction_context,
    project_reproduction_command_inventory,
    project_reproduction_state,
)
from log_commands.reproduction_result_storage import (
    ReproductionPublicationRequest,
    publish_reproduction_results,
)
from log_commands.reproduction_results import (
    ArtifactResult,
    CommandResult,
    RunFolder,
    RunResult,
)
from research_log_cli_test_support import (
    fixture_parameter_roles,
)
from research_log_data import (
    Fingerprint,
    InputResource,
    observe_fingerprint,
)
from research_log_paths import RESULTS_STORE
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.operation_state import operation_lock
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    execution_id,
    load_pyrun_state,
)
from validation.research_graph import (
    AmbiguityKind,
    AmbiguityObservation,
    EdgeKind,
    ResearchGraph,
)


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
        self.summary.write_text("# Study\n\n## Entries\n", encoding="utf-8")
        self.log = LogContext(self.summary.resolve(), self.log_root.resolve())

    def entry(self, number: int) -> EntryContext:
        entry_id = f"e{number:03d}"
        root = self.log_root / "entries" / f"2026-09-{number:02d}-{entry_id}-study"
        (root / "data").mkdir(parents=True)
        (root / "scripts").mkdir()
        relative = (Path("study") / "entries" / root.name / f"{entry_id}.md").as_posix()
        with self.summary.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- [Fixture]({relative})\n")
        return EntryContext(self.log, entry_id, root.resolve())

    def write_data(self, entry: EntryContext, items: list[dict[str, object]]) -> None:
        _write_json(
            entry.root / "data.json",
            {"inputs": items, "schema": "research-log-data/v5"},
        )

    def item(
        self, entry: EntryContext, name: str, path: Path, *, origin: bool
    ) -> dict[str, object]:
        return {
            "identity": {"algorithm": "sha256"},
            "kind": "file",
            "location": os.path.relpath(path, entry.root),
            "name": name,
            "origin": origin,
        }

    def evidence(self, entry: EntryContext, *names: str) -> None:
        document = entry.root / f"{entry.id}.md"
        data = json.loads((entry.root / "data.json").read_text(encoding="utf-8"))
        locations = {
            item["name"]: item["location"]
            for item in data["inputs"]
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("location"), str)
        }
        artifacts = []
        for number, name in enumerate(names, 1):
            location = locations[name]
            artifacts.append(f"[artifact]({location})<!-- eid:result-{number} -->")
        document.write_text(
            "# Entry\n\n## Evidence\n\n`Background:`\n\n"
            "Fixture evidence.\n\n`Steps:`\n\nInspect retained output.\n\n"
            "`Results:`\n\n" + "\n".join(artifacts) + "\n",
            encoding="utf-8",
        )
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
                        "artifact_fingerprint": _fingerprint(
                            entry.root / locations[name]
                        ).as_dict(),
                    }
                    for number, name in enumerate(names, 1)
                ],
                "schema": "research-log-evidence/v4",
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
        requires_reproduction: bool = True,
        last_run_at: str | None = None,
    ) -> tuple[str, PyrunExecution]:
        script = entry.root / "scripts" / f"{name}.py"
        script.write_text(f"# {name}\n", encoding="utf-8")
        parameters = tuple(
            token
            for input_name in sorted(inputs)
            for token in ("--input-data", f"<{input_name}>")
        ) + tuple(
            token
            for _output_name, output_path in sorted(outputs.items())
            for token in ("--output-data", f"data/{output_path.name}")
        )
        recipe = ExecutionRecipe(
            f"scripts/{name}.py",
            parameters,
            (),
            tuple(sorted(inputs)),
            tuple(sorted((f"data/{path.name}", "file") for path in outputs.values())),
            parameter_roles=fixture_parameter_roles(
                parameters,
                tuple(sorted(inputs)),
                tuple(
                    sorted((f"data/{path.name}", "file") for path in outputs.values())
                ),
            ),
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
            requires_reproduction,
            auto_reproduce,
            last_run_at,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
            recipe,
            observed,
        )
        document = entry.root / f"{entry.id}.md"
        if not document.exists():
            document.write_text("# Entry\n", encoding="utf-8")
        command = f"./pyrun --cid {name}"
        if not auto_reproduce:
            command += " --auto-reproduce=false"
        command += " -- scripts/{name}.py".format(name=name)
        command += "".join(
            f" --input-data '<{input_name}>'" for input_name in sorted(inputs)
        )
        command += "".join(
            f" --output-data '<{output_name}>'"
            for output_name, _output_path in sorted(outputs.items())
        )
        document.write_text(
            document.read_text(encoding="utf-8")
            + f"\n## {name}\n\n`Background:`\n\nFixture command.\n\n"
            "`Steps:`\n\n```bash\n" + command + "\n```\n\n`Results:`\n\nRecorded.\n",
            encoding="utf-8",
        )
        return execution_id(recipe), execution

    def write_pyrun(
        self, entry: EntryContext, executions: list[tuple[str, PyrunExecution]]
    ) -> None:
        state = PyrunFile(
            entry.root / "pyrun.json",
            entry.root,
            {
                execution.recipe.script.rsplit("/", 1)[-1].removesuffix(
                    ".py"
                ): PyrunCommand({identity: execution})
                for identity, execution in executions
            },
        )
        (entry.root / "pyrun.json").write_text(state.serialized(), encoding="utf-8")


def _plan(
    fixture: _Fixture,
    entry: EntryContext | None,
    *,
    include_all: bool = False,
    recheck: bool = False,
    runtime: ReproductionRuntime = ReproductionRuntime(),
):
    from log_commands.reproduction_planner import prepare_reproduction_context

    evaluation = evaluate_mechanical(
        EvaluationRequest(fixture.summary, FullEvaluationTarget())
    )
    prepared = prepare_reproduction_context(evaluation)
    return plan_reproduction(
        fixture.log,
        prepared,
        entry=entry,
        include_all=include_all,
        runtime=runtime,
        selection=ReproductionSelection(
            RECHECK_SELECTION if recheck else "incremental"
        ),
    )


def _seed_command_results(
    fixture: _Fixture,
    plan: ReproductionPlan,
    *,
    disposition: str = "succeeded",
) -> None:
    commands = tuple(
        CommandResult(
            cast(str, value["entry"]),
            cast(str, value["cid"]),
            cast(str, value["execution_id"]),
            disposition,
            cast(str, value["source_digest"]),
            "2026-09-06T00:01:00Z",
            "reproduce-20260906t000000z-seed",
        )
        for value in plan.commands
    )
    run_id = "reproduce-20260906t000000z-seed"
    counts = {
        "not_automatic": 0,
        "reproduction_not_needed": 0,
        "unchanged_failed": 0,
        "unchanged_blocked": 0,
        "succeeded": sum(item.disposition == "succeeded" for item in commands),
        "failed": sum(item.disposition == "failed" for item in commands),
        "blocked": sum(item.disposition == "blocked" for item in commands),
        "total": len(commands),
    }
    run = RunResult(
        run_id,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        "2026-09-06T00:00:00Z",
        "2026-09-06T00:01:00Z",
        {
            name: 0
            for name in (
                "matched",
                "changed",
                "failed",
                "comparison_failed",
                "skipped",
            )
        },
        RunFolder(
            "tmp/reproduction/2026-09-06/reproduce-20260906t000000z-seed",
            "unknown",
        ),
        (),
        counts,
    )
    publish_reproduction_results(
        fixture.log_root / RESULTS_STORE,
        ReproductionPublicationRequest(
            "docs/study.md",
            run,
            (),
            commands,
            tuple((item.entry, item.cid, item.execution_id) for item in commands),
            (),
        ),
    )
    if disposition == "succeeded":
        selected = {
            (
                cast(str, item["entry"]),
                cast(str, item["cid"]),
                cast(str, item["execution_id"]),
            )
            for item in plan.executions
        }
        for state_path in sorted((fixture.log_root / "entries").glob("*/pyrun.json")):
            entry_root = state_path.parent
            entry_id = entry_root.name.split("-")[3]
            state = load_pyrun_state(
                state_path,
                entry_root=entry_root,
                project_root=fixture.root,
            )
            commands = {
                cid: PyrunCommand(
                    {
                        identity: (
                            replace(execution, requires_reproduction=False)
                            if (entry_id, cid, identity) in selected
                            else execution
                        )
                        for identity, execution in command.executions.items()
                    }
                )
                for cid, command in state.commands.items()
            }
            state_path.write_text(
                PyrunFile(state_path, entry_root, commands).serialized(),
                encoding="utf-8",
            )


class ReproductionCommandInventoryTests(unittest.TestCase):
    def test_inventory_counts_only_remaining_policy_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            first = fixture.entry(1)
            fixture.entry(2)
            automatic_output = first.root / "data" / "automatic.txt"
            manual_output = first.root / "data" / "manual.txt"
            current_manual_output = first.root / "data" / "current-manual.txt"
            automatic_output.write_text("automatic", encoding="utf-8")
            manual_output.write_text("manual", encoding="utf-8")
            current_manual_output.write_text("current manual", encoding="utf-8")
            automatic = fixture.execution(
                first, "automatic", {}, {"automatic": automatic_output}
            )
            manual = fixture.execution(
                first,
                "manual",
                {},
                {"manual": manual_output},
                auto_reproduce=False,
            )
            current_manual = fixture.execution(
                first,
                "current-manual",
                {},
                {"current-manual": current_manual_output},
                auto_reproduce=False,
                requires_reproduction=False,
            )
            fixture.write_pyrun(first, [automatic, manual, current_manual])

            whole_log = project_reproduction_command_inventory(
                fixture.log, {"entry": None, "kind": "log"}
            )
            one_entry = project_reproduction_command_inventory(
                fixture.log, {"entry": first.id, "kind": "entry"}
            )

            self.assertEqual((whole_log.total, whole_log.policy_skipped), (3, 1))
            self.assertEqual(one_entry, whole_log)


class ReproductionPlanningTests(unittest.TestCase):
    def test_required_automatic_command_outside_evidence_closure_still_runs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "unpresented.txt"
            raw.write_text("raw", encoding="utf-8")
            output.write_text("unpresented", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "unpresented", output, origin=False),
                ],
            )
            execution = fixture.execution(
                entry,
                "analyze",
                {"raw": raw},
                {"unpresented": output},
                requires_reproduction=True,
            )
            fixture.write_pyrun(entry, [execution])

            evaluation = evaluate_mechanical(
                EvaluationRequest(fixture.summary, FullEvaluationTarget())
            )
            self.assertFalse(evaluation.context.currentness)

            plan = plan_reproduction(
                fixture.log,
                prepare_reproduction_context(evaluation),
                entry=entry,
                include_all=False,
            )

            self.assertEqual(
                [item["execution_id"] for item in plan.executions],
                [execution[0]],
            )

    def test_shared_currentness_selects_changed_script_when_state_flag_is_clear(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            result = entry.root / "data" / "result.txt"
            raw.write_text("raw", encoding="utf-8")
            result.write_text("result", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "result", result, origin=False),
                ],
            )
            fixture.evidence(entry, "result")
            execution = fixture.execution(
                entry,
                "analyze",
                {"raw": raw},
                {"result": result},
                requires_reproduction=False,
            )
            fixture.write_pyrun(entry, [execution])
            (entry.root / execution[1].recipe.script).write_text(
                "# changed\n", encoding="utf-8"
            )

            evaluation = evaluate_mechanical(
                EvaluationRequest(fixture.summary, FullEvaluationTarget())
            )
            self.assertEqual(
                {item.reason for item in evaluation.context.currentness},
                {"signature_mismatch"},
            )
            assert evaluation.snapshot is not None
            self.assertFalse(evaluation.snapshot.findings)
            self.assertFalse(evaluation.snapshot.blocked_checks)

            plan = plan_reproduction(
                fixture.log,
                prepare_reproduction_context(evaluation),
                entry=entry,
                include_all=False,
            )

            self.assertFalse(plan.executions)
            self.assertEqual(plan.commands[0]["execution_id"], execution[0])
            self.assertEqual(plan.commands[0]["selection"], "blocked")
            self.assertEqual(plan.commands[0]["details"], ["script_changed"])
            self.assertEqual(plan.cases[0]["reason"], "script_changed")

    def test_shared_graph_is_the_planning_topology_authority(self) -> None:
        from log_commands.reproduction_planner import (
            _graph_owner_index,
            _prepared_entries,
            prepare_reproduction_context,
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            intermediate = entry.root / "data" / "intermediate.txt"
            final = entry.root / "data" / "final.txt"
            for path, value in (
                (raw, "raw"),
                (intermediate, "intermediate"),
                (final, "final"),
            ):
                path.write_text(value, encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(
                        entry,
                        "intermediate",
                        intermediate,
                        origin=False,
                    ),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            producer = fixture.execution(
                entry,
                "produce",
                {"raw": raw},
                {"intermediate": intermediate},
            )
            consumer = fixture.execution(
                entry,
                "consume",
                {"intermediate": intermediate},
                {"final": final},
            )
            fixture.write_pyrun(entry, [producer, consumer])
            evaluation = evaluate_mechanical(
                EvaluationRequest(
                    fixture.summary,
                    FullEvaluationTarget(),
                )
            )
            graph = evaluation.context.graph
            dependency_free = ResearchGraph(
                graph.nodes,
                tuple(
                    edge
                    for edge in graph.edges
                    if edge.kind is not EdgeKind.EXECUTION_DEPENDENCY
                ),
                graph.ambiguities,
                graph.limit_observation,
            )
            prepared = prepare_reproduction_context(
                replace(
                    evaluation,
                    context=replace(evaluation.context, graph=dependency_free),
                )
            )
            plan = plan_reproduction(
                fixture.log,
                prepared,
                entry=entry,
                include_all=False,
            )
            dependencies = {
                value["execution_id"]: value["depends_on"]
                for value in plan.executions
            }
            self.assertEqual(dependencies[consumer[0]], [])

            production = next(
                edge
                for edge in graph.edges
                if edge.kind is EdgeKind.PRODUCTION
                and edge.target.endswith(intermediate.resolve().as_posix())
            )
            without_production = ResearchGraph(
                graph.nodes,
                tuple(edge for edge in graph.edges if edge != production),
                graph.ambiguities,
                graph.limit_observation,
            )
            entries = _prepared_entries(
                fixture.log,
                fixture.root.resolve(),
                evaluation.context.materials,
            )
            target = intermediate.resolve().as_posix()
            self.assertNotIn(
                target,
                _graph_owner_index(entries, fixture.root.resolve(), without_production),
            )
            rejected = ResearchGraph(
                without_production.nodes,
                without_production.edges,
                (
                    *without_production.ambiguities,
                    AmbiguityObservation(
                        AmbiguityKind.REJECTED_COMMAND,
                        production.target,
                        (production.source,),
                    ),
                ),
                without_production.limit_observation,
            )
            self.assertIn(
                target,
                _graph_owner_index(entries, fixture.root.resolve(), rejected),
            )

    def test_execution_timeout_must_be_within_the_fixed_bound(self) -> None:
        for value in (0, 604_801, True):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ActionError,
                    "--execution-timeout-seconds must be between 1 and 604800",
                ),
            ):
                plan_reproduction(
                    mock.sentinel.log,
                    mock.sentinel.prepared,
                    entry=None,
                    include_all=False,
                    runtime=ReproductionRuntime(execution_timeout_seconds=value),
                )

    def test_incremental_run_uses_current_pyrun_without_saved_command_state(
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
                requires_reproduction=False,
            )
            fixture.write_pyrun(entry, [(identity, execution)])

            with mock.patch(
                "log_commands.reproduction_planner._load_prior_results",
                return_value={},
            ):
                plan = _plan(fixture, entry)

            self.assertEqual(plan.executions, ())
            self.assertEqual(plan.commands[0]["selection"], "not_needed")

    def test_unknown_selection_policy_is_rejected_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))

            with self.assertRaisesRegex(ActionError, "selection policy"):
                plan_reproduction(
                    fixture.log,
                    mock.sentinel.prepared,
                    entry=None,
                    include_all=False,
                    selection=ReproductionSelection(
                        cast(SelectionPolicy, "unsupported")
                    ),
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
            reopened = ReproductionPlan.from_json(ordinary.serialized().encode())
            self.assertEqual(reopened, ordinary)
            self.assertEqual(
                len(ordinary.admission["executions"]),
                len(ordinary.commands),
            )
            self.assertEqual(
                [value["execution_id"] for value in ordinary.executions],
                [analysis[0]],
            )
            self.assertEqual(
                [(value["kind"], value["name"]) for value in ordinary.boundaries],
                [("non_automatic", "seed")],
            )
            self.assertFalse((fixture.log_root / ".cache" / "reproduction").exists())

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
                    {
                        "identity": {"algorithm": "directory-sha256-v1"},
                        "kind": "directory",
                        "location": "data/bundle",
                        "name": "bundle",
                        "origin": False,
                    },
                    fixture.item(entry, "member", member, origin=False),
                    fixture.item(entry, "final", final, origin=False),
                ],
            )
            fixture.evidence(entry, "final")
            producer_script = entry.root / "scripts" / "produce.py"
            producer_script.write_text("# produce\n", encoding="utf-8")
            producer_recipe = ExecutionRecipe(
                "scripts/produce.py",
                ("--input-data", "<raw>", "--output-data", "data/bundle"),
                (),
                ("raw",),
                (("data/bundle", "directory"),),
                parameter_roles=fixture_parameter_roles(
                    ("--input-data", "<raw>", "--output-data", "data/bundle"),
                    ("raw",),
                    (("data/bundle", "directory"),),
                ),
            )
            producer = (
                execution_id(producer_recipe),
                PyrunExecution(
                    True,
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
            document = entry.root / f"{entry.id}.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Produce bundle\n\n`Background:`\n\nFixture command.\n\n"
                "`Steps:`\n\n```bash\n"
                "./pyrun --cid produce -- scripts/produce.py --input-data '<raw>' "
                "--output-data '<bundle>'\n```\n\n`Results:`\n\nRecorded.\n",
                encoding="utf-8",
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
                [f"{entry.id}:produce:{producer[0]}"],
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
            frozen_input = plan.commands[0]["data_declaration"]["inputs"][0]
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
            plan = _plan(fixture, None)

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

            plan = _plan(fixture, entry)
            recheck = _plan(fixture, entry, recheck=True)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions], [upstream[0]]
            )
            self.assertEqual(plan.boundaries, ())
            self.assertEqual(
                next(
                    value["disposition"]
                    for value in plan.cases
                    if value["execution_id"] == current[0]
                ),
                "current",
            )
            commands = {value["execution_id"]: value for value in plan.commands}
            self.assertEqual(commands[current[0]]["selection"], "not_needed")
            self.assertFalse(commands[current[0]]["queued"])
            self.assertEqual(
                [value["execution_id"] for value in recheck.executions],
                [upstream[0]],
            )
            rechecked_commands = {
                value["execution_id"]: value for value in recheck.commands
            }
            self.assertEqual(rechecked_commands[current[0]]["selection"], "policy")
            self.assertIsNone(rechecked_commands[current[0]]["source_digest"])
            self.assertFalse(rechecked_commands[current[0]]["queued"])
            accounting = project_command_selection(recheck, None)
            self.assertEqual(accounting.not_automatic, 1)
            self.assertEqual(
                accounting.run_keys, {(entry.id, "produce-raw", upstream[0])}
            )
            self.assertEqual(accounting.total, 2)

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
            snapshotted = {value["execution_id"] for value in plan.executions}
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

            with self.assertRaisesRegex(
                ActionError, "prepared validation has failed checks"
            ):
                _plan(fixture, entry)

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
            self.assertEqual(plan.cases[0]["reason"], "validation_blocked")

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

    def test_outdated_state_requires_recheck_then_retains_terminal_failures(
        self,
    ) -> None:
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
                requires_reproduction=True,
                last_run_at="2026-09-06T00:00:00Z",
            )
            fixture.write_pyrun(entry, [execution])
            result_path = fixture.log_root / ".cache" / "results.sqlite"
            stored = {
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
                        "cid": "analyze",
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
                                "tmp/reproduction/2030-01-01/reproduce-study-current"
                            ),
                        },
                        "executions": [],
                        "include_all": False,
                        "command_outcomes": {
                            "blocked": 0,
                            "failed": 0,
                            "not_automatic": 0,
                            "reproduction_not_needed": 0,
                            "unchanged_blocked": 0,
                            "unchanged_failed": 0,
                            "succeeded": 1,
                            "total": 1,
                        },
                        "run_id": "reproduce-20260906t000000z-current",
                        "status": "complete",
                        "target": {"entry": entry.id, "kind": "entry"},
                    }
                ],
                "summary": "docs/study.md",
                "updated_at": "2026-09-06T00:01:00Z",
            }
            result_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(result_path) as database:
                database.execute("PRAGMA user_version=999")

            with self.assertRaises(ActionError) as caught:
                _plan(fixture, entry)

            self.assertEqual(
                caught.exception.code, "reproduction.results.schema_unsupported"
            )
            self.assertIn(
                "run whole-log reproduction with --recheck", str(caught.exception)
            )
            first = _plan(fixture, entry, recheck=True)

            self.assertEqual(
                [value["execution_id"] for value in first.executions],
                [execution[0]],
            )
            snapshot = first.commands[0]
            result_path.unlink()
            stored["runs"][0]["command_records"] = None
            commands = [
                {
                    "disposition": "succeeded",
                    "entry": entry.id,
                    "cid": "analyze",
                    "execution_id": execution[0],
                    "recorded_at": "2026-09-06T00:01:00Z",
                    "run_id": "reproduce-20260906t000000z-current",
                    "source_digest": snapshot["source_digest"],
                }
            ]
            stored["commands"] = commands
            for artifact_outcome, artifact_reason, disposition in (
                ("failed", "execution_failed", "failed"),
                ("skipped", "dependency_failed", "blocked"),
            ):
                with self.subTest(
                    artifact_outcome=artifact_outcome, disposition=disposition
                ):
                    stored["artifacts"][0]["outcome"] = artifact_outcome
                    stored["artifacts"][0]["reason"] = artifact_reason
                    if artifact_outcome in {"failed", "skipped"}:
                        stored["artifacts"][0]["comparison"] = None
                    else:
                        stored["artifacts"][0]["comparison"] = {
                            "contract": "research-log-reproduction-comparison/1",
                            "expected": _fingerprint(final).as_dict(),
                            "profile": "text",
                            "regenerated": _fingerprint(final).as_dict(),
                        }
                    commands[0]["disposition"] = disposition
                    stored_run = stored["runs"][0]
                    run = RunResult(
                        cast(str, stored_run["run_id"]),
                        cast(Mapping[str, object], stored_run["target"]),
                        cast(bool, stored_run["include_all"]),
                        cast(str, stored_run["status"]),
                        cast(str, stored_run["accepted_at"]),
                        cast(str | None, stored_run["finished_at"]),
                        cast(Mapping[str, int], stored_run["artifact_outcomes"]),
                        RunFolder(
                            cast(Mapping[str, str], stored_run["folder"])["path"],
                            cast(Mapping[str, str], stored_run["folder"])[
                                "availability"
                            ],
                        ),
                        (),
                        cast(Mapping[str, int], stored_run["command_outcomes"]),
                    )
                    artifacts = tuple(
                        ArtifactResult(
                            cast(str, item["entry"]),
                            cast(str, item["artifact"]),
                            cast(str | None, item["cid"]),
                            cast(str | None, item["execution_id"]),
                            cast(str, item["outcome"]),
                            cast(str | None, item["reason"]),
                            cast(str, item["recorded_at"]),
                            cast(str, item["run_id"]),
                            None,
                        )
                        for item in cast(
                            list[Mapping[str, object]], stored["artifacts"]
                        )
                    )
                    command_rows = tuple(
                        CommandResult(
                            cast(str, item["entry"]),
                            cast(str, item["cid"]),
                            cast(str, item["execution_id"]),
                            cast(str, item["disposition"]),
                            cast(str, item["source_digest"]),
                            cast(str, item["recorded_at"]),
                            cast(str, item["run_id"]),
                        )
                        for item in commands
                    )
                    publish_reproduction_results(
                        result_path,
                        ReproductionPublicationRequest(
                            "docs/study.md",
                            run,
                            artifacts,
                            command_rows,
                            tuple(
                                (item.entry, item.cid, item.execution_id)
                                for item in command_rows
                            ),
                            tuple(
                                (item.entry, item.artifact)
                                for item in artifacts
                                if item.execution_id is None
                            ),
                        ),
                    )

                    second = _plan(fixture, entry)

                    self.assertEqual(second.executions, ())
                    self.assertEqual(second.cases[0]["disposition"], "current")
                    self.assertEqual(
                        second.commands[0]["selection"],
                        "unchanged",
                    )
                    self.assertEqual(
                        second.commands[0]["prior_disposition"],
                        disposition,
                    )

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
                    "admission",
                    "cases",
                    "commands",
                    "comparison_context",
                    "executions",
                    "execution_timeout_seconds",
                    "failures",
                    "include_all",
                    "jobs",
                    "schema",
                    "summary",
                    "target",
                },
            )

            projection = project_reproduction_state(fixture.log)
            self.assertEqual(
                projection.reachable, frozenset({(entry.id, "data/final.txt")})
            )
            self.assertEqual(
                projection.output_executions[(entry.id, "data/final.txt")],
                (entry.id, "analyze", execution[0]),
            )
            self.assertEqual(
                projection.reachable_commands,
                frozenset({(entry.id, "analyze", execution[0])}),
            )

    def test_incremental_command_closure_invalidates_only_affected_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw_first = entry.root / "data" / "raw-first.txt"
            raw_second = entry.root / "data" / "raw-second.txt"
            first_output = entry.root / "data" / "first.txt"
            second_output = entry.root / "data" / "second.txt"
            participating_code = entry.root / "scripts" / "shared.py"
            for path, value in (
                (raw_first, "raw first"),
                (raw_second, "raw second"),
                (first_output, "first"),
                (second_output, "second"),
                (participating_code, "# shared"),
            ):
                path.write_text(value, encoding="utf-8")

            def write_data() -> None:
                fixture.write_data(
                    entry,
                    [
                        fixture.item(entry, "raw_first", raw_first, origin=True),
                        fixture.item(entry, "raw_second", raw_second, origin=True),
                        fixture.item(entry, "first", first_output, origin=False),
                        fixture.item(entry, "second", second_output, origin=False),
                    ],
                )

            write_data()
            fixture.evidence(entry, "first", "second")
            first = fixture.execution(
                entry,
                "first",
                {"raw_first": raw_first},
                {"first": first_output},
            )
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        code=(("scripts/shared.py", _fingerprint(participating_code)),),
                    ),
                ),
            )
            second = fixture.execution(
                entry,
                "second",
                {"raw_second": raw_second},
                {"second": second_output},
            )
            fixture.write_pyrun(entry, [first, second])
            _seed_command_results(fixture, _plan(fixture, entry))
            second = (second[0], replace(second[1], requires_reproduction=False))

            unchanged = _plan(fixture, entry)
            self.assertEqual(unchanged.executions, ())

            first_script = entry.root / first[1].recipe.script
            first_script.write_text("# first changed\n", encoding="utf-8")
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed, script=_fingerprint(first_script)
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            script_changed = _plan(fixture, entry)
            self.assertEqual(
                [value["execution_id"] for value in script_changed.executions],
                [first[0]],
            )
            _seed_command_results(fixture, script_changed)

            participating_code.write_text("# shared changed", encoding="utf-8")
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        code=(("scripts/shared.py", _fingerprint(participating_code)),),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            code_changed = _plan(fixture, entry)
            self.assertEqual(
                [value["execution_id"] for value in code_changed.executions],
                [first[0]],
            )
            _seed_command_results(fixture, code_changed)

            raw_first.write_text("raw first changed", encoding="utf-8")
            write_data()
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        inputs=(("raw_first", _fingerprint(raw_first)),),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            input_changed = _plan(fixture, entry)
            self.assertEqual(
                [value["execution_id"] for value in input_changed.executions],
                [first[0]],
            )
            _seed_command_results(fixture, input_changed)

            first_output.write_text("first changed", encoding="utf-8")
            write_data()
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        outputs=(("data/first.txt", _fingerprint(first_output)),),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            expected_artifact_changed = _plan(fixture, entry)
            self.assertEqual(
                [
                    value["execution_id"]
                    for value in expected_artifact_changed.executions
                ],
                [],
            )
            _seed_command_results(fixture, expected_artifact_changed)

            authored_first = first
            changed_recipe = replace(
                first[1].recipe, environment=(("REPRODUCTION_MODE", "changed"),)
            )
            first = (
                execution_id(changed_recipe),
                replace(first[1], recipe=changed_recipe),
            )
            fixture.write_pyrun(entry, [first, second])
            with self.assertRaisesRegex(
                ActionError,
                "command bindings",
            ):
                _plan(fixture, entry)
            first = authored_first
            fixture.write_pyrun(entry, [first, second])

            def comparison_identity(
                _owner: object, output: str, _project_root: Path
            ) -> str | None:
                return "a" * 64 if output == "data/first.txt" else None

            with mock.patch(
                "log_commands.reproduction_planner._comparison_identity",
                side_effect=comparison_identity,
            ):
                comparison_a = _plan(fixture, entry)
                _seed_command_results(fixture, comparison_a)

            def changed_comparison_identity(
                _owner: object, output: str, _project_root: Path
            ) -> str | None:
                return "b" * 64 if output == "data/first.txt" else None

            with mock.patch(
                "log_commands.reproduction_planner._comparison_identity",
                side_effect=changed_comparison_identity,
            ):
                comparison_changed = _plan(fixture, entry)
            self.assertEqual(comparison_changed.executions, ())

    def test_changed_dependency_output_invalidates_only_affected_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            paths = {
                name: entry.root / "data" / f"{name}.txt"
                for name in ("raw", "middle", "final", "other_raw", "other")
            }
            for name, path in paths.items():
                path.write_text(name, encoding="utf-8")

            def write_data() -> None:
                fixture.write_data(
                    entry,
                    [
                        fixture.item(entry, "raw", paths["raw"], origin=True),
                        fixture.item(entry, "middle", paths["middle"], origin=False),
                        fixture.item(entry, "final", paths["final"], origin=False),
                        fixture.item(
                            entry, "other_raw", paths["other_raw"], origin=True
                        ),
                        fixture.item(entry, "other", paths["other"], origin=False),
                    ],
                )

            write_data()
            fixture.evidence(entry, "final", "other")
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
                {"other_raw": paths["other_raw"]},
                {"other": paths["other"]},
            )
            fixture.write_pyrun(entry, [upstream, downstream, independent])
            _seed_command_results(fixture, _plan(fixture, entry))
            independent = (
                independent[0],
                replace(independent[1], requires_reproduction=False),
            )

            paths["middle"].write_text("middle changed", encoding="utf-8")
            write_data()
            upstream = (
                upstream[0],
                replace(
                    upstream[1],
                    observed=replace(
                        upstream[1].observed,
                        outputs=(("data/middle.txt", _fingerprint(paths["middle"])),),
                    ),
                ),
            )
            downstream = (
                downstream[0],
                replace(
                    downstream[1],
                    observed=replace(
                        downstream[1].observed,
                        inputs=(("middle", _fingerprint(paths["middle"])),),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [upstream, downstream, independent])

            plan = _plan(fixture, entry)

            self.assertEqual(
                {value["execution_id"] for value in plan.executions},
                {upstream[0], downstream[0]},
            )
            selections = {
                value["execution_id"]: value["selection"] for value in plan.commands
            }
            self.assertEqual(selections[independent[0]], "not_needed")

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
            upstream_parameters = (
                "--input-data",
                "<raw>",
                "--output-data",
                "<project>/shared/upstream.txt",
            )
            upstream_recipe = ExecutionRecipe(
                upstream[1].recipe.script,
                upstream_parameters,
                (),
                ("raw",),
                (("<project>/shared/upstream.txt", "file"),),
                parameter_roles=fixture_parameter_roles(
                    upstream_parameters,
                    ("raw",),
                    (("<project>/shared/upstream.txt", "file"),),
                ),
            )
            upstream_execution = PyrunExecution(
                True,
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
            document = first_entry.root / f"{first_entry.id}.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "--output-data '<shared>'",
                    "--output-data '<project>/shared/upstream.txt'",
                ),
                encoding="utf-8",
            )
            downstream = fixture.execution(
                second_entry, "downstream", {"shared": shared}, {"final": final}
            )
            fixture.write_pyrun(first_entry, [upstream])
            fixture.write_pyrun(second_entry, [downstream])
            plan = _plan(fixture, None)
            recheck = _plan(fixture, None, recheck=True)

            self.assertEqual(
                [value["execution_id"] for value in plan.executions],
                [],
            )
            self.assertFalse(
                any(value["kind"] == "cross_entry" for value in plan.boundaries)
            )
            self.assertEqual(recheck.executions, plan.executions)
            self.assertEqual(recheck.target, {"entry": None, "kind": "log"})

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
            plan = _plan(fixture, None)

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

    def test_planner_is_lock_free_after_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
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
            execution = fixture.execution(
                entry, "analyze", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [execution])
            with operation_lock(fixture.log_root, "entry-e001.lock"):
                plan = _plan(fixture, entry)

            self.assertEqual(
                [item["execution_id"] for item in plan.executions], [execution[0]]
            )


if __name__ == "__main__":
    unittest.main()
