from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import unittest

GRAPH = importlib.import_module("validation.research_graph")


class ResearchGraphTests(unittest.TestCase):
    def test_retention_edges_use_entry_root_canonical_materials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry_root = Path(directory) / "entries/e001"
            target = (entry_root / "data/notes.txt").resolve()
            entry = SimpleNamespace(
                entry_id="e001",
                material_owner="entries/e001",
                entry_root=entry_root,
                document=entry_root / "e001.md",
                data=None,
                evidence=None,
                retention=SimpleNamespace(
                    path=entry_root / "retention.json",
                    records=(
                        SimpleNamespace(
                            id="notes",
                            paths=("data/notes.txt",),
                            directory=None,
                        ),
                    ),
                ),
                pyrun=None,
            )

            graph = GRAPH.build_evaluation_graph(
                GRAPH.EvaluationGraphInputs(entries=(entry,), invocations=())
            )
            retention_edge = next(
                edge
                for edge in graph.edges
                if edge.kind is GRAPH.EdgeKind.RETENTION
            )

            self.assertEqual(
                graph.node(retention_edge.target).identity,
                target.as_posix(),
            )

    def test_rejected_invocation_keeps_context_without_production_edges(self) -> None:
        resource = SimpleNamespace(
            name="source",
            kind="file",
            canonical_target="data/source.csv",
        )
        entry = SimpleNamespace(
            entry_id="e001",
            material_owner="entries/e001",
            document="entries/e001/e001.md",
            data=SimpleNamespace(inputs=(resource,)),
            evidence=None,
            retention=None,
            pyrun=None,
        )
        source = SimpleNamespace(
            path="data/source.csv",
            origin=False,
            input_resource=resource,
        )
        output = SimpleNamespace(
            path="data/result.csv",
            origin=False,
            input_resource=None,
        )
        collection = SimpleNamespace(
            direction="output",
            mechanism="explicit",
            target="result",
            root=None,
            members=("data/result.csv",),
        )
        invocation = SimpleNamespace(
            entry="e001",
            material_owner="entries/e001",
            identity="rejected-command",
            cid="build",
            document="entries/e001/e001.md",
            fence=1,
            ordinal=1,
            sequence=1,
            inputs=(source,),
            outputs=(output,),
            collections=(collection,),
            script="scripts/build.py",
        )

        graph = GRAPH.build_evaluation_graph(
            GRAPH.EvaluationGraphInputs(
                entries=(entry,),
                invocations=(),
                rejected_invocations=(invocation,),
            )
        )
        command = next(
            node for node in graph.nodes if node.kind is GRAPH.NodeKind.COMMAND
        )
        neighboring_kinds = {
            graph.node(edge.source).kind
            for edge in graph.edges
            if edge.target == command.node_id
        }

        self.assertTrue(
            {
                GRAPH.NodeKind.DATA_RECORD,
                GRAPH.NodeKind.MATERIAL,
                GRAPH.NodeKind.SCRIPT,
            }.issubset(neighboring_kinds)
        )
        self.assertFalse(
            any(
                edge.kind is GRAPH.EdgeKind.PRODUCTION
                and edge.source == command.node_id
                for edge in graph.edges
            )
        )
        self.assertTrue(
            any(
                observation.kind is GRAPH.AmbiguityKind.REJECTED_COMMAND
                and command.node_id in observation.candidates
                for observation in graph.ambiguities
            )
        )

    def test_builder_keeps_established_edges_separate_from_ambiguities(self) -> None:
        builder = GRAPH.ResearchGraphBuilder()
        command = GRAPH.ResearchNode(
            GRAPH.NodeKind.COMMAND,
            "cmd-1",
            "e001",
            {"cid": "build", "fence": 1, "ordinal": 1},
        )
        material = GRAPH.ResearchNode(
            GRAPH.NodeKind.MATERIAL,
            "data/result.csv",
            "e001",
        )
        competing = GRAPH.ResearchNode(
            GRAPH.NodeKind.COMMAND,
            "cmd-2",
            "e002",
        )
        for node in (command, material, competing):
            builder.add_node(node)
        builder.add_edge(
            GRAPH.ResearchEdge(
                GRAPH.EdgeKind.PRODUCTION,
                command.node_id,
                material.node_id,
            )
        )
        builder.add_ambiguity(
            GRAPH.AmbiguityObservation(
                GRAPH.AmbiguityKind.MULTIPLE_PRODUCERS,
                material.node_id,
                (command.node_id, competing.node_id),
                {"candidate_count": 2},
            )
        )

        graph = builder.build()

        self.assertTrue(graph.complete)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(len(graph.ambiguities), 1)
        self.assertNotIn(competing.node_id, {edge.source for edge in graph.edges})
        self.assertEqual(command.reference.entry, "e001")

    def test_graph_identity_uses_kind_entry_and_domain_identity(self) -> None:
        left = GRAPH.ResearchNode(GRAPH.NodeKind.COMMAND, "same", "e001")
        right = GRAPH.ResearchNode(GRAPH.NodeKind.COMMAND, "same", "e002")
        material = GRAPH.ResearchNode(GRAPH.NodeKind.MATERIAL, "same", "e001")

        self.assertEqual(
            {left.node_id, right.node_id, material.node_id},
            {"command:e001:same", "command:e002:same", "material:e001:same"},
        )

    def test_bound_returns_a_typed_partial_graph(self) -> None:
        builder = GRAPH.ResearchGraphBuilder(max_nodes=1)

        self.assertTrue(
            builder.add_node(GRAPH.ResearchNode(GRAPH.NodeKind.ENTRY, "e001"))
        )
        self.assertFalse(
            builder.add_node(GRAPH.ResearchNode(GRAPH.NodeKind.ENTRY, "e002"))
        )
        graph = builder.build()

        self.assertFalse(graph.complete)
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.limit_observation.dimension, "nodes")
        self.assertEqual(graph.limit_observation.observed, 2)
        self.assertEqual(graph.limit_observation.limit, 1)

    def test_edges_and_ambiguities_reject_unknown_nodes(self) -> None:
        builder = GRAPH.ResearchGraphBuilder()
        node = GRAPH.ResearchNode(GRAPH.NodeKind.ENTRY, "e001")
        builder.add_node(node)

        with self.assertRaises(GRAPH.ValidationDomainError):
            builder.add_edge(
                GRAPH.ResearchEdge(
                    GRAPH.EdgeKind.DECLARATION,
                    node.node_id,
                    "document:e001:missing.md",
                )
            )
        with self.assertRaises(GRAPH.ValidationDomainError):
            builder.add_ambiguity(
                GRAPH.AmbiguityObservation(
                    GRAPH.AmbiguityKind.UNRESOLVED_BINDING,
                    node.node_id,
                    ("command:e001:missing",),
                )
            )
        missing_subject = GRAPH.AmbiguityObservation(
            GRAPH.AmbiguityKind.NO_PRODUCER,
            "material:missing",
            (node.node_id,),
        )
        with self.assertRaises(GRAPH.ValidationDomainError):
            builder.add_ambiguity(missing_subject)

    def test_evaluation_graph_directly_owns_all_repair_node_families(self) -> None:
        execution = SimpleNamespace(
            auto_reproduce=True,
            requires_reproduction=False,
        )
        pyrun = SimpleNamespace(
            execution_items=lambda: (("build", "execution-1", execution),)
        )
        resource = SimpleNamespace(
            name="catalog",
            kind="file",
            canonical_target="data/catalog.csv",
        )
        retention_record = SimpleNamespace(
            id="keep-result",
            paths=("data/result.csv",),
            directory=None,
        )
        entry = SimpleNamespace(
            entry_id="e001",
            document="entries/e001/e001.md",
            data=SimpleNamespace(inputs=(resource,)),
            retention=SimpleNamespace(records=(retention_record,)),
            pyrun=pyrun,
        )
        relationship = SimpleNamespace(
            path="data/catalog.csv",
            origin=False,
        )
        output = SimpleNamespace(path="data/result.csv", origin=False)
        collection = SimpleNamespace(
            direction="output",
            mechanism="explicit",
            target="results",
            members=("data/result.csv",),
        )
        invocation = SimpleNamespace(
            entry="e001",
            identity="command-1",
            cid="build",
            document="entries/e001/e001.md",
            fence=1,
            ordinal=1,
            inputs=(relationship,),
            outputs=(output,),
            collections=(collection,),
            script="scripts/build.py",
        )
        evidence = SimpleNamespace(
            entry="e001",
            record="result",
            presentation="entries/e001/e001.md:result",
            materials=("data/result.csv",),
            origin_materials=frozenset(),
        )

        graph = GRAPH.build_evaluation_graph(
            GRAPH.EvaluationGraphInputs(
                entries=(entry,),
                invocations=(invocation,),
                evidence_connections=(evidence,),
                code_inputs={"command-1": ("src/shared.py",)},
                execution_bindings={
                    "command-1": ("e001", "build:execution-1")
                },
            )
        )

        self.assertEqual(
            {
                GRAPH.NodeKind.CODE,
                GRAPH.NodeKind.COLLECTION,
                GRAPH.NodeKind.COMMAND,
                GRAPH.NodeKind.DATA_RECORD,
                GRAPH.NodeKind.DOCUMENT,
                GRAPH.NodeKind.ENTRY,
                GRAPH.NodeKind.EVIDENCE_RECORD,
                GRAPH.NodeKind.EXECUTION,
                GRAPH.NodeKind.MATERIAL,
                GRAPH.NodeKind.PRESENTATION,
                GRAPH.NodeKind.RETENTION_RECORD,
                GRAPH.NodeKind.SCRIPT,
            },
            {node.kind for node in graph.nodes},
        )
        self.assertTrue(
            any(edge.kind is GRAPH.EdgeKind.COMMAND_EXECUTION for edge in graph.edges)
        )

    def test_builder_records_unresolved_observations_and_execution_dependencies(
        self,
    ) -> None:
        execution = SimpleNamespace(
            auto_reproduce=True,
            requires_reproduction=False,
        )
        entry = SimpleNamespace(
            entry_id="e001",
            document="entries/e001/e001.md",
            data=None,
            retention=None,
            pyrun=SimpleNamespace(
                execution_items=lambda: (
                    ("build", "build-1", execution),
                    ("use", "use-1", execution),
                    ("orphan", "orphan-1", execution),
                )
            ),
        )
        produced = SimpleNamespace(path="data/shared.csv", origin=False)
        missing = SimpleNamespace(path="data/missing.csv", origin=False)
        producer = SimpleNamespace(
            entry="e001",
            identity="producer",
            cid="build",
            document="entries/e001/e001.md",
            fence=1,
            ordinal=1,
            sequence=1,
            inputs=(),
            outputs=(produced,),
            collections=(),
            script=None,
        )
        consumer = SimpleNamespace(
            entry="e001",
            identity="consumer",
            cid="use",
            document="entries/e001/e001.md",
            fence=2,
            ordinal=1,
            sequence=2,
            inputs=(produced, missing),
            outputs=(),
            collections=(),
            script=None,
        )
        rejected = {
            "identity": "rejected",
            "entry": "e001",
            "document": "entries/e001/e001.md",
            "fence": 3,
            "ordinal": 1,
            "code": "material.candidate.unresolved",
            "declared_outputs": (
                {"path": "data/rejected.csv", "kind": "file"},
            ),
        }

        graph = GRAPH.build_evaluation_graph(
            GRAPH.EvaluationGraphInputs(
                (entry,),
                (producer, consumer),
                execution_bindings={
                    "producer": ("e001", "build:build-1"),
                    "consumer": ("e001", "use:use-1"),
                },
                rejected_commands=(rejected,),
            )
        )

        self.assertTrue(
            any(
                edge.kind is GRAPH.EdgeKind.EXECUTION_DEPENDENCY
                for edge in graph.edges
            )
        )
        self.assertEqual(
            {
                GRAPH.AmbiguityKind.NO_PRODUCER,
                GRAPH.AmbiguityKind.REJECTED_COMMAND,
                GRAPH.AmbiguityKind.UNRESOLVED_BINDING,
            },
            {item.kind for item in graph.ambiguities},
        )

    def test_builder_records_multiple_producers_without_dependency_edge(self) -> None:
        output = SimpleNamespace(path="data/shared.csv", origin=False)

        def command(identity: str, sequence: int) -> object:
            return SimpleNamespace(
                entry="e001",
                identity=identity,
                cid=identity,
                document="entries/e001/e001.md",
                fence=sequence,
                ordinal=1,
                sequence=sequence,
                inputs=(),
                outputs=(output,),
                collections=(),
                script=None,
            )

        graph = GRAPH.build_evaluation_graph(
            GRAPH.EvaluationGraphInputs((), (command("one", 1), command("two", 2)))
        )

        ambiguity = next(
            item
            for item in graph.ambiguities
            if item.kind is GRAPH.AmbiguityKind.MULTIPLE_PRODUCERS
        )
        self.assertEqual(len(ambiguity.candidates), 2)
        self.assertFalse(
            any(
                edge.kind is GRAPH.EdgeKind.EXECUTION_DEPENDENCY
                for edge in graph.edges
            )
        )


if __name__ == "__main__":
    unittest.main()
