from __future__ import annotations

import importlib
from unittest import mock

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import unittest

ADMISSION = importlib.import_module("log_commands.reproduction_admission")
DOMAIN = importlib.import_module("validation.domain")
GRAPH = importlib.import_module("validation.research_graph")
MODEL = importlib.import_module("log_commands.model")


def _node(kind: object, identity: str, entry: str | None = None) -> object:
    return GRAPH.ResearchNode(kind, identity, entry)


def _graph(*, dependency: bool = False, second: bool = False) -> object:
    entry_1 = _node(GRAPH.NodeKind.ENTRY, "e001")
    command_1 = _node(GRAPH.NodeKind.COMMAND, "command-1", "e001")
    execution_1 = _node(GRAPH.NodeKind.EXECUTION, "run:execution-1", "e001")
    input_1 = _node(GRAPH.NodeKind.MATERIAL, "/project/input-1.csv")
    output_1 = _node(GRAPH.NodeKind.MATERIAL, "/project/output-1.csv")
    nodes = [entry_1, command_1, execution_1, input_1, output_1]
    edges = [
        GRAPH.ResearchEdge(
            GRAPH.EdgeKind.DECLARATION,
            entry_1.node_id,
            command_1.node_id,
        ),
        GRAPH.ResearchEdge(
            GRAPH.EdgeKind.COMMAND_EXECUTION,
            command_1.node_id,
            execution_1.node_id,
        ),
        GRAPH.ResearchEdge(
            GRAPH.EdgeKind.CONSUMPTION,
            input_1.node_id,
            command_1.node_id,
        ),
        GRAPH.ResearchEdge(
            GRAPH.EdgeKind.PRODUCTION,
            command_1.node_id,
            output_1.node_id,
        ),
    ]
    if second:
        entry_2 = _node(GRAPH.NodeKind.ENTRY, "e002")
        command_2 = _node(GRAPH.NodeKind.COMMAND, "command-2", "e002")
        execution_2 = _node(GRAPH.NodeKind.EXECUTION, "run:execution-2", "e002")
        output_2 = _node(GRAPH.NodeKind.MATERIAL, "/project/output-2.csv")
        nodes.extend((entry_2, command_2, execution_2, output_2))
        edges.extend(
            (
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.DECLARATION,
                    entry_2.node_id,
                    command_2.node_id,
                ),
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.COMMAND_EXECUTION,
                    command_2.node_id,
                    execution_2.node_id,
                ),
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.PRODUCTION,
                    command_2.node_id,
                    output_2.node_id,
                ),
            )
        )
        if dependency:
            edges.append(
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.EXECUTION_DEPENDENCY,
                    execution_1.node_id,
                    execution_2.node_id,
                )
            )
    return GRAPH.ResearchGraph(tuple(nodes), tuple(edges), ())


def _selected(*, second: bool = False) -> tuple[object, ...]:
    values = [
        ADMISSION.SelectedExecution(
            "e001",
            "run",
            "execution-1",
            ("/project/output-1.csv",),
        )
    ]
    if second:
        values.append(
            ADMISSION.SelectedExecution(
                "e002",
                "run",
                "execution-2",
                ("/project/output-2.csv",),
            )
        )
    return tuple(values)


def _wide_inventory(
    size: int,
    *,
    dependency_chain: bool = False,
) -> tuple[object, tuple[object, ...]]:
    nodes: list[object] = []
    edges: list[object] = []
    selected: list[object] = []
    for number in range(size):
        entry_id = f"e{number:03d}"
        command_id = f"command-{number}"
        execution_id = f"execution-{number}"
        output = f"/project/output-{number}.csv"
        entry = _node(GRAPH.NodeKind.ENTRY, entry_id)
        command = _node(GRAPH.NodeKind.COMMAND, command_id, entry_id)
        execution = _node(
            GRAPH.NodeKind.EXECUTION,
            f"run:{execution_id}",
            entry_id,
        )
        material = _node(GRAPH.NodeKind.MATERIAL, output)
        nodes.extend((entry, command, execution, material))
        edges.extend(
            (
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.DECLARATION,
                    entry.node_id,
                    command.node_id,
                ),
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.COMMAND_EXECUTION,
                    command.node_id,
                    execution.node_id,
                ),
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.PRODUCTION,
                    command.node_id,
                    material.node_id,
                ),
            )
        )
        selected.append(
            ADMISSION.SelectedExecution(
                entry_id,
                "run",
                execution_id,
                (output,),
            )
        )
        if dependency_chain and number:
            prior_entry = f"e{number - 1:03d}"
            prior_execution = _node(
                GRAPH.NodeKind.EXECUTION,
                f"run:execution-{number - 1}",
                prior_entry,
            )
            edges.append(
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.EXECUTION_DEPENDENCY,
                    prior_execution.node_id,
                    execution.node_id,
                )
            )
    return GRAPH.ResearchGraph(tuple(nodes), tuple(edges), ()), tuple(selected)


def _check(
    identity: str,
    area: object,
    code: str,
    *,
    subject: str | None = None,
    entry: str | None = None,
    owner: object | None = None,
    references: tuple[object, ...] = (),
    keys: tuple[object, ...] = (),
) -> object:
    diagnostic_subject = subject or identity
    return DOMAIN.RuleCheck(
        identity,
        area,
        DOMAIN.CheckOutcome.FINDING,
        diagnostic_subject,
        diagnostic=DOMAIN.CheckDiagnostic(
            code, diagnostic_subject, "test rule", {}
        ),
        issue_context=DOMAIN.IssueContext(
            entry=entry,
            repair_keys=keys,
            context_nodes=references,
            admission_owner=owner,
        ),
    )


def _snapshot(*checks: object) -> object:
    attempt = DOMAIN.ValidationAttempt.build(
        target=DOMAIN.ValidationTarget(DOMAIN.TargetKind.LOG, "/project/study.md"),
        source_identity="source-1",
        rules_version="rules-1",
        started_at="start",
        finished_at="finish",
        checks=checks,
    )
    return DOMAIN.ValidationSnapshot.from_attempt(attempt)


class ReproductionAdmissionTests(unittest.TestCase):
    def test_clear_snapshot_admits_exactly_bound_execution(self) -> None:
        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(),
            _graph(),
            _selected(),
        )

        self.assertFalse(result.global_blocking_finding_ids)
        self.assertEqual(result.executions[0].disposition, "admitted")
        self.assertFalse(result.executions[0].blocking_finding_ids)

    def test_binding_requires_one_command_and_the_complete_output_set(self) -> None:
        graph = _graph()
        without_binding = GRAPH.ResearchGraph(
            graph.nodes,
            tuple(
                edge
                for edge in graph.edges
                if edge.kind is not GRAPH.EdgeKind.COMMAND_EXECUTION
            ),
            (),
        )
        wrong_output = ADMISSION.SelectedExecution(
            "e001",
            "run",
            "execution-1",
            ("/project/other.csv",),
        )

        for candidate, selected in (
            (without_binding, _selected()),
            (graph, (wrong_output,)),
        ):
            with self.subTest(selected=selected):
                with self.assertRaises(MODEL.ActionError) as raised:
                    ADMISSION.evaluate_reproduction_admission(
                        _snapshot(), candidate, selected
                    )
                self.assertEqual(
                    raised.exception.code,
                    "reproduction.validation.scope_unresolved",
                )

    def test_binding_rejects_multiple_commands_for_one_execution(self) -> None:
        graph = _graph()
        second_command = _node(
            GRAPH.NodeKind.COMMAND,
            "command-2",
            "e001",
        )
        execution = _node(
            GRAPH.NodeKind.EXECUTION,
            "run:execution-1",
            "e001",
        )
        ambiguous = GRAPH.ResearchGraph(
            (*graph.nodes, second_command),
            (
                *graph.edges,
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.COMMAND_EXECUTION,
                    second_command.node_id,
                    execution.node_id,
                ),
            ),
            (),
        )

        with self.assertRaises(MODEL.ActionError) as raised:
            ADMISSION.evaluate_reproduction_admission(
                _snapshot(), ambiguous, _selected()
            )
        self.assertEqual(
            raised.exception.code,
            "reproduction.validation.scope_unresolved",
        )

    def test_rejected_command_ambiguity_retains_output_finding_scope(self) -> None:
        graph = _graph()
        command = _node(GRAPH.NodeKind.COMMAND, "command-1", "e001")
        material = _node(GRAPH.NodeKind.MATERIAL, "/project/output-1.csv")
        rejected = GRAPH.ResearchGraph(
            graph.nodes,
            tuple(
                edge
                for edge in graph.edges
                if not (
                    edge.kind is GRAPH.EdgeKind.PRODUCTION
                    and edge.source == command.node_id
                    and edge.target == material.node_id
                )
            ),
            (
                GRAPH.AmbiguityObservation(
                    GRAPH.AmbiguityKind.REJECTED_COMMAND,
                    material.node_id,
                    (command.node_id,),
                ),
            ),
        )
        missing_output = _check(
            "provenance:missing-output",
            DOMAIN.RuleArea.PROVENANCE,
            "provenance.output.missing",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.MATERIAL,
            references=(
                DOMAIN.GraphReference("material", "/project/output-1.csv"),
            ),
        )

        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(missing_output),
            rejected,
            _selected(),
        )

        self.assertEqual(
            result.executions[0].blocking_finding_ids,
            ("provenance:missing-output",),
        )

    def test_finding_ownership_blocks_only_its_execution_or_entry(self) -> None:
        command = DOMAIN.GraphReference("command", "command-1", "e001")
        command_failure = _check(
            "conformance:command-1",
            DOMAIN.RuleArea.CONFORMANCE,
            "invocation.command.unsupported",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.COMMAND,
            references=(command,),
        )
        entry_failure = _check(
            "evidence:entry-2",
            DOMAIN.RuleArea.EVIDENCE,
            "evidence.json.schema_invalid",
            entry="e002",
            owner=DOMAIN.AdmissionOwner.ENTRY,
        )

        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(command_failure, entry_failure),
            _graph(second=True),
            _selected(second=True),
        )

        decisions = {item.key: item for item in result.executions}
        self.assertEqual(
            decisions[("e001", "run", "execution-1")].blocking_finding_ids,
            ("conformance:command-1",),
        )
        self.assertEqual(
            decisions[("e002", "run", "execution-2")].blocking_finding_ids,
            ("evidence:entry-2",),
        )

    def test_malformed_output_support_blocks_entry_reproduction(self) -> None:
        malformed_support = _check(
            "entry:e001:pyrun",
            DOMAIN.RuleArea.PROVENANCE,
            "pyrun.outputs.invalid",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.ENTRY,
        )

        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(malformed_support),
            _graph(second=True),
            _selected(second=True),
        )

        decisions = {item.key: item for item in result.executions}
        self.assertEqual(
            decisions[("e001", "run", "execution-1")].blocking_finding_ids,
            ("entry:e001:pyrun",),
        )
        self.assertFalse(
            decisions[("e002", "run", "execution-2")].blocking_finding_ids
        )

    def test_execution_owner_and_record_attachment_are_exact(self) -> None:
        execution_failure = _check(
            "provenance:execution-1",
            DOMAIN.RuleArea.PROVENANCE,
            "provenance.execution.invalid",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.EXECUTION,
            references=(
                DOMAIN.GraphReference("execution", "run:execution-1", "e001"),
            ),
        )
        execution_result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(execution_failure),
            _graph(second=True),
            _selected(second=True),
        )
        self.assertEqual(
            tuple(item.blocking_finding_ids for item in execution_result.executions),
            (("provenance:execution-1",), ()),
        )

        graph = _graph()
        record = _node(
            GRAPH.NodeKind.DATA_RECORD,
            "e001:data:input",
            "e001",
        )
        command = _node(GRAPH.NodeKind.COMMAND, "command-1", "e001")
        with_record = GRAPH.ResearchGraph(
            (*graph.nodes, record),
            (
                *graph.edges,
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.DECLARATION,
                    record.node_id,
                    command.node_id,
                ),
            ),
            (),
        )
        record_failure = _check(
            "evidence:record",
            DOMAIN.RuleArea.EVIDENCE,
            "evidence.record.invalid",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.MATERIAL,
            keys=(
                DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.RECORD,
                    "e001:data:input",
                ),
            ),
        )
        record_result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(record_failure),
            with_record,
            _selected(),
        )
        self.assertEqual(
            record_result.executions[0].blocking_finding_ids,
            ("evidence:record",),
        )

    def test_orphan_findings_never_block_reproduction(self) -> None:
        material = DOMAIN.GraphReference("material", "/project/output-1.csv")
        checks = (
            _check(
                "orphan:output-1",
                DOMAIN.RuleArea.ORPHAN,
                "orphan.material.unused",
                entry="e001",
                owner=DOMAIN.AdmissionOwner.MATERIAL,
                references=(material,),
            ),
        )

        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(*checks),
            _graph(),
            _selected(),
        )

        self.assertEqual(result.executions[0].disposition, "admitted")

    def test_cross_type_batch_does_not_share_admission_authority(self) -> None:
        repair_key = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.COMMAND,
            "e001:command-1",
        )
        blocking = _check(
            "conformance:command-1",
            DOMAIN.RuleArea.CONFORMANCE,
            "invocation.command.unsupported",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.COMMAND,
            keys=(repair_key,),
        )
        nonblocking = _check(
            "orphan:command-1",
            DOMAIN.RuleArea.ORPHAN,
            "orphan.material.unused",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.COMMAND,
            keys=(repair_key,),
        )
        snapshot = _snapshot(blocking, nonblocking)
        self.assertEqual(len(snapshot.batches), 1)

        result = ADMISSION.evaluate_reproduction_admission(
            snapshot,
            _graph(),
            _selected(),
        )

        self.assertEqual(
            result.executions[0].blocking_finding_ids,
            ("conformance:command-1",),
        )

    def test_same_named_material_findings_block_their_exact_executions(self) -> None:
        first = _check(
            "provenance:origin:first",
            DOMAIN.RuleArea.PROVENANCE,
            "data.origin.invalid",
            subject="catalog",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.MATERIAL,
            references=(
                DOMAIN.GraphReference("material", "/project/output-1.csv"),
            ),
            keys=(
                DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.MATERIAL, "/project/output-1.csv", "e001"
                ),
            ),
        )
        second = _check(
            "provenance:origin:second",
            DOMAIN.RuleArea.PROVENANCE,
            "data.origin.invalid",
            subject="catalog",
            entry="e002",
            owner=DOMAIN.AdmissionOwner.MATERIAL,
            references=(
                DOMAIN.GraphReference("material", "/project/output-2.csv"),
            ),
            keys=(
                DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.MATERIAL, "/project/output-2.csv", "e002"
                ),
            ),
        )
        snapshot = _snapshot(first, second)

        result = ADMISSION.evaluate_reproduction_admission(
            snapshot,
            _graph(second=True),
            _selected(second=True),
        )

        self.assertEqual(len(snapshot.batches), 2)
        self.assertEqual(
            result.executions[0].blocking_finding_ids,
            ("provenance:origin:first",),
        )
        self.assertEqual(
            result.executions[1].blocking_finding_ids,
            ("provenance:origin:second",),
        )

    def test_global_and_unanchored_findings_follow_fail_closed_policy(self) -> None:
        global_failure = _check(
            "conformance:log",
            DOMAIN.RuleArea.CONFORMANCE,
            "summary.invalid",
            owner=DOMAIN.AdmissionOwner.LOG,
        )
        unanchored_producer = _check(
            "provenance:unrelated",
            DOMAIN.RuleArea.PROVENANCE,
            "producer.missing",
            entry="e009",
            owner=DOMAIN.AdmissionOwner.MATERIAL,
            references=(
                DOMAIN.GraphReference("material", "/project/unrelated.csv"),
            ),
        )

        result = ADMISSION.evaluate_reproduction_admission(
            _snapshot(global_failure, unanchored_producer),
            _graph(),
            _selected(),
        )

        self.assertEqual(
            result.global_blocking_finding_ids,
            ("conformance:log",),
        )
        self.assertEqual(result.executions[0].disposition, "admitted")

    def test_dependency_propagation_excludes_downstream_not_unrelated_work(
        self,
    ) -> None:
        blocking = _check(
            "conformance:upstream",
            DOMAIN.RuleArea.CONFORMANCE,
            "invocation.command.unsupported",
            entry="e001",
            owner=DOMAIN.AdmissionOwner.COMMAND,
            references=(
                DOMAIN.GraphReference("command", "command-1", "e001"),
            ),
        )

        dependent = ADMISSION.evaluate_reproduction_admission(
            _snapshot(blocking),
            _graph(dependency=True, second=True),
            _selected(second=True),
        )
        unrelated = ADMISSION.evaluate_reproduction_admission(
            _snapshot(blocking),
            _graph(second=True),
            _selected(second=True),
        )

        self.assertEqual(
            tuple(item.disposition for item in dependent.executions),
            ("excluded", "excluded"),
        )
        self.assertEqual(
            tuple(item.disposition for item in unrelated.executions),
            ("excluded", "admitted"),
        )

    def test_finding_scope_lookup_does_not_cross_multiply_inventory(self) -> None:
        size = 64
        graph, selected = _wide_inventory(size, dependency_chain=True)
        checks = tuple(
            _check(
                f"conformance:command-{number}",
                DOMAIN.RuleArea.CONFORMANCE,
                "invocation.command.unsupported",
                entry=f"e{number:03d}",
                owner=DOMAIN.AdmissionOwner.COMMAND,
                references=(
                    DOMAIN.GraphReference(
                        "command",
                        f"command-{number}",
                        f"e{number:03d}",
                    ),
                ),
            )
            for number in range(size)
        )
        original = ADMISSION._AdmissionScopeIndex._keys_for_node
        original_merge = ADMISSION._merge_dependency_blockers
        lookups = 0
        dependency_edges = 0

        def counted(scope: object, node_id: str) -> object:
            nonlocal lookups
            lookups += 1
            return original(scope, node_id)

        def counted_merge(upstream: set[str], downstream: set[str]) -> None:
            nonlocal dependency_edges
            dependency_edges += 1
            original_merge(upstream, downstream)

        with mock.patch.object(
            ADMISSION._AdmissionScopeIndex,
            "_keys_for_node",
            counted,
        ), mock.patch.object(
            ADMISSION,
            "_merge_dependency_blockers",
            counted_merge,
        ):
            result = ADMISSION.evaluate_reproduction_admission(
                _snapshot(*checks),
                graph,
                selected,
            )

        self.assertEqual(lookups, size)
        self.assertEqual(dependency_edges, size - 1)
        self.assertEqual(
            sum(len(item.blocking_finding_ids) for item in result.executions),
            size * (size + 1) // 2,
        )

    def test_entry_owner_lookup_does_not_cross_multiply_inventory(self) -> None:
        size = 64
        graph, selected = _wide_inventory(size)
        checks = tuple(
            _check(
                f"evidence:entry-{number}",
                DOMAIN.RuleArea.EVIDENCE,
                "evidence.json.schema_invalid",
                entry=f"e{number:03d}",
                owner=DOMAIN.AdmissionOwner.ENTRY,
            )
            for number in range(size)
        )
        original = ADMISSION._AdmissionScopeIndex.keys_for_entry
        lookups = 0

        def counted(scope: object, entry: str | None) -> object:
            nonlocal lookups
            lookups += 1
            return original(scope, entry)

        with mock.patch.object(
            ADMISSION._AdmissionScopeIndex,
            "keys_for_entry",
            counted,
        ):
            result = ADMISSION.evaluate_reproduction_admission(
                _snapshot(*checks),
                graph,
                selected,
            )

        self.assertEqual(lookups, size)
        self.assertEqual(
            sum(len(item.blocking_finding_ids) for item in result.executions),
            size,
        )


if __name__ == "__main__":
    unittest.main()
