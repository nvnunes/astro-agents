from research_log_validation_test_support import GRAPH, GRAPH_QUERIES, unittest


class GraphCoreTests(unittest.TestCase):
    def origin(self) -> object:
        return GRAPH.FactOrigin(
            kind=GRAPH.OriginKind.MECHANICAL,
            resolver="test-resolver",
            inputs=(GRAPH.OriginInput("fixture", "abc123"),),
            rules_version="test-rules",
        )

    def test_graph_builder_is_deterministic(self) -> None:
        artifact = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/output.csv"
        )
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:statistic:1.0"
        )
        graphs = []
        for order in ((artifact, presented), (presented, artifact)):
            builder = GRAPH.GraphBuilder("test-rules")
            for key in order:
                builder.add_node(key, self.origin())
            builder.add_edge(
                GRAPH.EdgeKind.SUPPORTS,
                artifact,
                presented,
                "docs/mini",
                self.origin(),
            )
            builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())
            graphs.append(builder.build())
        self.assertEqual(graphs[0].as_dict(), graphs[1].as_dict())
        self.assertEqual(graphs[0].identity, graphs[1].identity)

    def test_selected_producer_limits_provenance_closure(self) -> None:
        namespace = "docs/mini"
        selected = GRAPH.NodeKey(namespace, GRAPH.NodeKind.INVOCATION, "selected")
        alternative = GRAPH.NodeKey(namespace, GRAPH.NodeKind.INVOCATION, "alternative")
        target = GRAPH.NodeKey(namespace, GRAPH.NodeKind.ARTIFACT, "data/result.csv")
        selected_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/input.csv"
        )
        alternative_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/other"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (selected, alternative, target, selected_input, alternative_input):
            builder.add_node(node, self.origin())
        for invocation in (selected, alternative):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                invocation,
                target,
                namespace,
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            selected,
            selected_input,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            alternative,
            alternative_input,
            namespace,
            self.origin(),
        )
        graph = builder.build()
        seeds = GRAPH_QUERIES.target_provenance_seeds(
            graph,
            "e001",
            target.identity,
            [{"path": target.identity, "role": "target"}],
            selected.identity,
        )
        closure = GRAPH_QUERIES.provenance_nodes(
            graph,
            ((seed, GRAPH.RootPolicy.PRESENTED) for seed in seeds),
        )
        self.assertEqual(seeds, {selected})
        self.assertIn(selected_input, closure)
        self.assertNotIn(alternative_input, closure)

    def test_orphan_locations_keep_entry_identity(self) -> None:
        first = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INDEXED_INPUT, "e001:<shared>"
        )
        second = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INDEXED_INPUT, "e002:<shared>"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(
            first,
            self.origin(),
            {"entry": "e001", "display_identity": "<shared>", "orphanable": True},
        )
        builder.add_node(
            second,
            self.origin(),
            {"entry": "e002", "display_identity": "<shared>", "orphanable": True},
        )
        builder.add_root(first, GRAPH.RootPolicy.RECORDED_COMMAND, self.origin())
        self.assertEqual(
            GRAPH_QUERIES.orphan_locations(builder.build(), "docs/mini"),
            {("e002", "<shared>")},
        )

    def test_dependency_graph_round_trips_exactly(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin(), {"orphanable": True})
        builder.add_root(key, GRAPH.RootPolicy.RECORDED_COMMAND, self.origin())
        graph = builder.build()
        loaded = GRAPH.DependencyGraph.from_dict(graph.as_dict())
        self.assertEqual(loaded.as_dict(), graph.as_dict())
        self.assertEqual(loaded.identity, graph.identity)


if __name__ == "__main__":
    unittest.main()
