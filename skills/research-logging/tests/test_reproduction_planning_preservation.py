"""Native incremental history and remaining original preparation fixtures."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from log_commands import reproduction_planner as planner
from log_commands.context import EntryContext
from log_commands.model import ActionError
from log_commands.reproduction_artifact_results import (
    ArtifactObservationContext,
    compare_accepted_artifact,
)
from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import (
    ArtifactOutcome,
    CommandOutcome,
    WorkSelection,
)
from log_commands.reproduction_invocation import ReproductionRuntime
from log_commands.reproduction_reconciliation import _reconcile_state
from log_commands.reproduction_saved_storage import publish_saved_run
from reproduction_planning_test_support import (
    _effective_fingerprint,
    _fingerprint,
    _Fixture,
    prepare_plan,
)
from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import Fingerprint, InputResource, observe_fingerprint
from test_reproduction_canonical_records import WHEN, attempted
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


def run_ids(plan):
    return [item["identity"]["execution_id"] for item in plan.scheduling]


def seed_success(fixture, plan):
    selected = tuple(
        work for work in plan.commands if work.selection is WorkSelection.RUN
    )
    if not selected:
        return
    counter = getattr(fixture, "history_counter", 0) + 1
    fixture.history_counter = counter
    stamp = (
        datetime.fromisoformat(WHEN.replace("Z", "+00:00")) + timedelta(seconds=counter)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = tuple(
        replace(
            attempted(work, CommandOutcome.SUCCEEDED),
            started_at=stamp,
            finished_at=stamp,
            outputs={
                name: fingerprint.as_dict()
                for name, fingerprint in work.execution.observed.outputs
            },
        )
        for work in selected
    )
    selected_ids = {work.identity for work in selected}
    compared = [
        compare_accepted_artifact(
            artifact,
            plan.command(artifact.producer),
            Path(artifact.retained_path),
            context=ArtifactObservationContext(f"reproduce-seed-{counter}", stamp),
        )
        for artifact in plan.artifacts
        if artifact.producer in selected_ids
    ]
    saved = complete_saved_run(
        plan,
        RunCompletion(
            f"reproduce-seed-{counter}",
            stamp,
            stamp,
            results,
            tuple(item[0] for item in compared),
            tuple(problem for _, problems in compared for problem in problems),
        ),
    )
    publish_saved_run(fixture.log.root, saved)
    identities = {work.identity for work in selected}
    from log_commands.reproduction_domain import ExecutionRef

    for path in sorted((fixture.log_root / "entries").glob("*/pyrun.json")):
        entry_id = path.parent.name.split("-")[3]
        state = load_pyrun_state(
            path, entry_root=path.parent, project_root=fixture.root
        )
        commands = {
            cid: PyrunCommand(
                {
                    identity: replace(execution, requires_reproduction=False)
                    if ExecutionRef(entry_id, cid, identity) in identities
                    else execution
                    for identity, execution in command.executions.items()
                }
            )
            for cid, command in state.commands.items()
        }
        path.write_text(
            PyrunFile(path, path.parent, commands).serialized(), encoding="utf-8"
        )


class NativePlanningPreservationTests(unittest.TestCase):
    def test_unfingerprintable_source_is_never_suppressed_as_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data/raw.txt"
            output = entry.root / "data/output.txt"
            raw.write_text("raw", encoding="utf-8")
            output.write_text("retained", encoding="utf-8")
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
            script.write_text("from math import *\n", encoding="utf-8")

            first = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(first), [identity])
            assert first.commands[0].accepted_source is not None
            self.assertIsNone(first.commands[0].accepted_source.effective_code)
            _reconcile_state(
                fixture.log,
                first.commands[0],
                adopt_accepted_source=True,
            )
            reconciled = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=fixture.root,
            ).execution("build", identity)
            assert reconciled is not None
            self.assertIsNone(reconciled.observed.effective_code)
            seed_success(fixture, first)

            second = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(second), [identity])
            self.assertEqual(second.commands[0].selection, WorkSelection.RUN)

    def test_source_reconciliation_adopts_only_a_complete_match(self):
        for adopt in (False, True):
            with self.subTest(adopt=adopt), tempfile.TemporaryDirectory() as directory:
                fixture = _Fixture(Path(directory))
                entry = fixture.entry(1)
                raw = entry.root / "data/raw.txt"
                output = entry.root / "data/output.txt"
                raw.write_text("raw", encoding="utf-8")
                output.write_text("retained", encoding="utf-8")
                fixture.write_data(
                    entry,
                    [
                        fixture.item(entry, "raw", raw, origin=True),
                        fixture.item(entry, "output", output, origin=False),
                    ],
                )
                fixture.evidence(entry, "output")
                identity, execution = fixture.execution(
                    entry, "build", {"raw": raw}, {"output": output}
                )
                fixture.write_pyrun(entry, [(identity, execution)])
                script = entry.root / execution.recipe.script
                script.write_text("VALUE = 2\n", encoding="utf-8")
                work = prepare_plan(fixture, entry).commands[0]
                assert work.accepted_source is not None

                _reconcile_state(
                    fixture.log,
                    work,
                    adopt_accepted_source=adopt,
                )
                # A lost acknowledgment can repeat the same reconciliation safely.
                _reconcile_state(
                    fixture.log,
                    work,
                    adopt_accepted_source=adopt,
                )
                current = load_pyrun_state(
                    entry.root / "pyrun.json",
                    entry_root=entry.root,
                    project_root=fixture.root,
                ).execution(work.identity.cid, work.identity.execution_id)
                assert current is not None
                self.assertFalse(current.requires_reproduction)
                self.assertEqual(
                    current.observed.script,
                    (
                        work.accepted_source.script
                        if adopt
                        else work.execution.observed.script
                    ),
                )
                self.assertEqual(
                    current.observed.effective_code,
                    (
                        work.accepted_source.effective_code
                        if adopt
                        else work.execution.observed.effective_code
                    ),
                )
                if adopt:
                    next_plan = prepare_plan(fixture, entry)
                    self.assertEqual(run_ids(next_plan), [])
                    self.assertEqual(
                        next_plan.commands[0].selection,
                        WorkSelection.NOT_NEEDED,
                    )

    def test_successful_unequal_changed_source_is_reused_until_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data/raw.txt"
            output = entry.root / "data/output.txt"
            raw.write_text("raw", encoding="utf-8")
            output.write_text("retained", encoding="utf-8")
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
            script.write_text("VALUE = 2\n", encoding="utf-8")

            changed = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(changed), [identity])
            work = changed.commands[0]
            regenerated = fixture.root / "regenerated.txt"
            regenerated.write_text("different", encoding="utf-8")
            artifact = next(item for item in changed.artifacts if item.producer)
            comparison, problems = compare_accepted_artifact(
                artifact,
                work,
                regenerated,
                context=ArtifactObservationContext("reproduce-source-difference", WHEN),
            )
            self.assertIs(comparison.outcome, ArtifactOutcome.NOT_MATCHED)
            assert artifact.output is not None
            assert comparison.regenerated is not None
            command = replace(
                attempted(work, CommandOutcome.SUCCEEDED),
                outputs={artifact.output: comparison.regenerated.as_dict()},
            )
            saved = complete_saved_run(
                changed,
                RunCompletion(
                    "reproduce-source-difference",
                    WHEN,
                    WHEN,
                    (command,),
                    (comparison,),
                    problems,
                ),
            )
            publish_saved_run(fixture.log.root, saved)

            incremental = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(incremental), [])
            self.assertIs(
                incremental.command(work.identity).selection,
                WorkSelection.NOT_NEEDED,
            )
            self.assertEqual(
                incremental.reusable_artifact_results,
                (comparison,),
            )
            self.assertEqual(
                run_ids(prepare_plan(fixture, entry, recheck=True)), [identity]
            )

            script.write_text("VALUE = 2\n# raw-only change\n", encoding="utf-8")
            self.assertEqual(run_ids(prepare_plan(fixture, entry)), [])

            mutated_baseline = replace(
                execution,
                observed=replace(
                    execution.observed,
                    effective_code=Fingerprint(
                        "python-effective-code-sha256-v1", digest="c" * 64
                    ),
                ),
            )
            fixture.write_pyrun(entry, [(identity, mutated_baseline)])
            self.assertEqual(run_ids(prepare_plan(fixture, entry)), [identity])

            script.write_text("VALUE = 3\n", encoding="utf-8")
            self.assertEqual(run_ids(prepare_plan(fixture, entry)), [identity])

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
            plan = planner.plan_reproduction_work(
                fixture.log,
                prepared,
                entry=entry,
                include_all=False,
            )
            dependencies = {
                work.identity.execution_id: work.dependencies for work in plan.commands
            }
            self.assertEqual(dependencies[consumer[0]], ())

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
                        _effective_fingerprint(
                            producer_script, fixture.root.resolve()
                        ),
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

            plan = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(plan), [producer[0], consumer[0]])
            producer_work = next(
                item for item in plan.commands if item.identity.cid == "produce"
            )
            consumer_work = next(
                item for item in plan.commands if item.identity.cid == "consume"
            )
            self.assertEqual(consumer_work.dependencies, (producer_work.identity,))

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
            plan = prepare_plan(fixture, None)
            self.assertEqual(len(plan.scheduling), 2)
            self.assertEqual(
                [item["identity"]["entry"] for item in plan.scheduling],
                ["e001", "e002"],
            )
            self.assertEqual(len(set(run_ids(plan))), 1)
            for item in plan.scheduling:
                entry = item["identity"]["entry"]
                digest = item["identity"]["execution_id"].rsplit(":", 1)[-1]
                self.assertEqual(item["run_path"], f"<run>/executions/{entry}/{digest}")
                self.assertIn(
                    f"<run>/diagnostics/{entry}/{digest}", item["writable_paths"]
                )
                self.assertIn(f"<run>/runtime/{entry}/{digest}", item["writable_paths"])

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
                prepare_plan(fixture, entry)

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
                    upstream[1].observed.effective_code,
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
            plan = prepare_plan(fixture, None)
            recheck = prepare_plan(fixture, None, recheck=True)
            self.assertEqual(run_ids(plan), [])
            self.assertEqual(run_ids(recheck), run_ids(plan))
            self.assertEqual(recheck.target.as_dict(), {"entry": None, "kind": "log"})
            self.assertTrue(
                all(
                    item.boundary is None or item.boundary["kind"] != "cross_entry"
                    for item in plan.artifacts
                )
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
                plan = prepare_plan(fixture, entry)

            self.assertEqual(run_ids(plan), [execution[0]])

    def test_execution_timeout_must_be_within_the_fixed_bound(self) -> None:
        for value in (0, 604_801, True):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ActionError,
                    "--execution-timeout-seconds must be between 1 and 604800",
                ),
            ):
                planner.plan_reproduction_work(
                    mock.sentinel.log,
                    mock.sentinel.prepared,
                    entry=None,
                    include_all=False,
                    runtime=ReproductionRuntime(execution_timeout_seconds=value),
                )

    def test_unknown_selection_policy_is_rejected_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))

            with self.assertRaisesRegex(ActionError, "selection policy"):
                planner.plan_reproduction_work(
                    fixture.log,
                    mock.sentinel.prepared,
                    entry=None,
                    include_all=False,
                    selection=planner.ReproductionSelection("unsupported"),
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
                (participating_code, "VALUE = 1\n"),
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
            first_script = entry.root / first[1].recipe.script
            first_script.write_text("import shared\n", encoding="utf-8")
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        script=_fingerprint(first_script),
                        effective_code=_effective_fingerprint(
                            first_script, fixture.root.resolve()
                        ),
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
            seed_success(fixture, prepare_plan(fixture, entry))
            second = (second[0], replace(second[1], requires_reproduction=False))

            unchanged = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(unchanged), [])

            first_script.write_text("import shared\nVALUE = 1\n", encoding="utf-8")
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        script=_fingerprint(first_script),
                        effective_code=_effective_fingerprint(
                            first_script, fixture.root.resolve()
                        ),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            script_changed = prepare_plan(fixture, entry)
            self.assertEqual(
                run_ids(script_changed),
                [first[0]],
            )
            seed_success(fixture, script_changed)

            participating_code.write_text("VALUE = 2\n", encoding="utf-8")
            first = (
                first[0],
                replace(
                    first[1],
                    observed=replace(
                        first[1].observed,
                        effective_code=_effective_fingerprint(
                            first_script, fixture.root.resolve()
                        ),
                    ),
                ),
            )
            fixture.write_pyrun(entry, [first, second])
            code_changed = prepare_plan(fixture, entry)
            self.assertEqual(
                run_ids(code_changed),
                [first[0]],
            )
            seed_success(fixture, code_changed)

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
            input_changed = prepare_plan(fixture, entry)
            self.assertEqual(
                run_ids(input_changed),
                [first[0]],
            )
            seed_success(fixture, input_changed)

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
            expected_artifact_changed = prepare_plan(fixture, entry)
            self.assertEqual(
                run_ids(expected_artifact_changed),
                [],
            )
            seed_success(fixture, expected_artifact_changed)

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
                prepare_plan(fixture, entry)
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
                comparison_a = prepare_plan(fixture, entry)
                seed_success(fixture, comparison_a)

            def changed_comparison_identity(
                _owner: object, output: str, _project_root: Path
            ) -> str | None:
                return "b" * 64 if output == "data/first.txt" else None

            with mock.patch(
                "log_commands.reproduction_planner._comparison_identity",
                side_effect=changed_comparison_identity,
            ):
                comparison_changed = prepare_plan(fixture, entry)
            self.assertEqual(run_ids(comparison_changed), [])

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
            seed_success(fixture, prepare_plan(fixture, entry))
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

            plan = prepare_plan(fixture, entry)

            self.assertEqual(
                set(run_ids(plan)),
                {upstream[0], downstream[0]},
            )
            selections = {
                value.identity.execution_id: value.selection.value
                for value in plan.commands
            }
            self.assertEqual(selections[independent[0]], "not_needed")
