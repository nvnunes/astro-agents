from research_log_validation_test_support import (
    CLI,
    DECISIONS,
    GRAPH,
    GRAPH_ADAPTER,
    GRAPH_QUERIES,
    GRAPH_STORE,
    INVENTORY,
    RECORDS,
    RENDER,
    RUNTIME,
    Path,
    adjudication_for,
    hashlib,
    json,
    make_log,
    mock,
    tempfile,
    unittest,
    write,
)


class GraphCoreTests(unittest.TestCase):
    def origin(self, kind: object = None, reviewed_scope: str = "") -> object:
        origin_kind = kind or GRAPH.OriginKind.MECHANICAL
        return GRAPH.FactOrigin(
            kind=origin_kind,
            resolver="test-resolver",
            inputs=(GRAPH.OriginInput("fixture", "abc123"),),
            rules_version="test-rules",
            reviewed_scope=reviewed_scope,
        )

    def test_graph_builder_merges_identical_facts_deterministically(self) -> None:
        artifact = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/output.csv"
        )
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:statistic:1.0"
        )
        first = GRAPH.GraphBuilder("test-rules")
        second = GRAPH.GraphBuilder("test-rules")
        for builder, order in (
            (first, (artifact, presented)),
            (second, (presented, artifact)),
        ):
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
        self.assertEqual(first.build().as_dict(), second.build().as_dict())
        self.assertEqual(first.build().identity, second.build().identity)

    def test_selected_producer_is_the_only_provenance_root(self) -> None:
        namespace = "docs/mini"
        selected = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L20:1:selected"
        )
        alternative = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:alternative"
        )
        target = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/result.csv"
        )
        selected_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/girmos.csv"
        )
        alternative_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/tiptop"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (
            selected,
            alternative,
            target,
            selected_input,
            alternative_input,
        ):
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
        self.assertNotIn(alternative, closure)
        self.assertNotIn(alternative_input, closure)

    def test_scoped_collection_does_not_union_alternative_producers(self) -> None:
        namespace = "docs/mini"
        consumer = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L30:1:consumer"
        )
        first = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:first"
        )
        second = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L20:1:second"
        )
        collection = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/run"
        )
        member = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.MEMBER, "data/run::result.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (consumer, first, second, collection, member):
            builder.add_node(node, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            consumer,
            collection,
            namespace,
            self.origin(),
        )
        for producer in (first, second):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                producer,
                collection,
                namespace,
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            member,
            collection,
            namespace,
            self.origin(),
            {"selected": True},
        )
        graph = builder.build()

        closure = GRAPH_QUERIES.provenance_nodes(
            graph, [(consumer, GRAPH.RootPolicy.PRESENTED)]
        )

        self.assertIn(collection, closure)
        self.assertIn(member, closure)
        self.assertNotIn(first, closure)
        self.assertNotIn(second, closure)

    def test_invocation_that_consumes_material_is_not_its_upstream_producer(
        self,
    ) -> None:
        namespace = "docs/mini"
        invocation = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:sweep"
        )
        config = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/config.ini"
        )
        output = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/sweep"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (invocation, config, output):
            builder.add_node(node, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            invocation,
            config,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            invocation,
            config,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            invocation,
            output,
            namespace,
            self.origin(),
        )
        graph = builder.build()

        closure = GRAPH_QUERIES.provenance_nodes(
            graph, [(config, GRAPH.RootPolicy.PRESENTED)]
        )

        self.assertEqual(closure, {config})
        self.assertEqual(GRAPH_QUERIES.ambiguous_producer_nodes(graph, closure), {})

    def test_generated_input_follows_only_its_selected_upstream_producer(
        self,
    ) -> None:
        namespace = "docs/mini"
        consumer = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L30:1:consumer"
        )
        selected = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:girmos"
        )
        alternative = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L20:1:full"
        )
        generated = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/generated.h5"
        )
        girmos_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/girmos.yaml"
        )
        tiptop_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/tiptop"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (
            consumer,
            selected,
            alternative,
            generated,
            girmos_input,
            tiptop_input,
        ):
            builder.add_node(node, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            consumer,
            generated,
            namespace,
            self.origin(),
        )
        for producer in (selected, alternative):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                producer,
                generated,
                namespace,
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.SELECTED_PRODUCER,
            generated,
            selected,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            selected,
            girmos_input,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            alternative,
            tiptop_input,
            namespace,
            self.origin(),
        )

        closure = GRAPH_QUERIES.provenance_nodes(
            builder.build(), [(consumer, GRAPH.RootPolicy.PRESENTED)]
        )

        self.assertIn(selected, closure)
        self.assertIn(girmos_input, closure)
        self.assertNotIn(alternative, closure)
        self.assertNotIn(tiptop_input, closure)

    def test_scoped_collection_follows_selected_members_and_bound_producer(
        self,
    ) -> None:
        namespace = "docs/mini"
        consumer = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L30:1:consumer"
        )
        selected = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:girmos"
        )
        alternative = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L20:1:full"
        )
        collection = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/generated"
        )
        member = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.MEMBER, "data/generated::result.csv"
        )
        girmos_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/girmos.yaml"
        )
        tiptop_input = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.COLLECTION, "data/tiptop"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for node in (
            consumer,
            selected,
            alternative,
            collection,
            member,
            girmos_input,
            tiptop_input,
        ):
            builder.add_node(node, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            consumer,
            collection,
            namespace,
            self.origin(),
        )
        for producer in (selected, alternative):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                producer,
                collection,
                namespace,
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.SELECTED_PRODUCER,
            collection,
            selected,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            member,
            collection,
            namespace,
            self.origin(),
            {"selected": True},
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            selected,
            girmos_input,
            namespace,
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            alternative,
            tiptop_input,
            namespace,
            self.origin(),
        )

        closure = GRAPH_QUERIES.provenance_nodes(
            builder.build(), [(consumer, GRAPH.RootPolicy.PRESENTED)]
        )

        self.assertIn(member, closure)
        self.assertIn(selected, closure)
        self.assertIn(girmos_input, closure)
        self.assertNotIn(alternative, closure)
        self.assertNotIn(tiptop_input, closure)

    def test_check_graph_slice_fingerprints_upstream_candidate_set(self) -> None:
        namespace = "docs/mini"
        consumer = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L30:1:consumer"
        )
        selected = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L10:1:girmos"
        )
        alternative = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.INVOCATION, "e001:L20:1:full"
        )
        generated = GRAPH.NodeKey(
            namespace, GRAPH.NodeKind.ARTIFACT, "data/generated.h5"
        )

        def build(
            *, include_alternative: bool, command: str = "--mode girmos"
        ) -> object:
            builder = GRAPH.GraphBuilder("test-rules")
            for node in (consumer, generated):
                builder.add_node(node, self.origin())
            builder.add_node(selected, self.origin(), {"command": command})
            if include_alternative:
                builder.add_node(alternative, self.origin())
            builder.add_edge(
                GRAPH.EdgeKind.CONSUMES,
                consumer,
                generated,
                namespace,
                self.origin(),
            )
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                selected,
                generated,
                namespace,
                self.origin(),
            )
            if include_alternative:
                builder.add_edge(
                    GRAPH.EdgeKind.PRODUCES,
                    alternative,
                    generated,
                    namespace,
                    self.origin(),
                )
            builder.add_edge(
                GRAPH.EdgeKind.SELECTED_PRODUCER,
                generated,
                selected,
                namespace,
                self.origin(),
            )
            return builder.build()

        check = {
            "entry": "e001",
            "target": "data/output.csv",
            "check": "Provenance",
            "dependencies": [{"path": generated.identity, "role": "input"}],
            "resolution": {"producer_invocation": consumer.identity},
        }
        without_alternative = RENDER.check_graph_slice(
            build(include_alternative=False), check
        )
        with_alternative = RENDER.check_graph_slice(
            build(include_alternative=True), check
        )
        changed_command = RENDER.check_graph_slice(
            build(include_alternative=False, command="--mode girmos-v2"), check
        )

        self.assertNotEqual(
            without_alternative["identity"], with_alternative["identity"]
        )
        self.assertIn(
            alternative.as_dict(),
            with_alternative["nodes"],
        )
        self.assertNotEqual(
            without_alternative["identity"], changed_command["identity"]
        )

    def test_graph_builder_rejects_conflicting_node_attributes(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.ARTIFACT, "data/output.csv")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin(), {"orphanable": True})
        with self.assertRaisesRegex(GRAPH.GraphContractError, "conflicting attributes"):
            builder.add_node(key, self.origin(), {"orphanable": False})

    def test_semantic_origin_requires_reviewed_scope(self) -> None:
        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "must name its reviewed scope"
        ):
            self.origin(GRAPH.OriginKind.SEMANTIC)

    def test_graph_builder_rejects_edges_with_unknown_nodes(self) -> None:
        source = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1")
        target = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(source, self.origin())
        with self.assertRaisesRegex(GRAPH.GraphContractError, "add both edge nodes"):
            builder.add_edge(
                GRAPH.EdgeKind.INVOKES,
                source,
                target,
                "docs/mini",
                self.origin(),
            )

    def test_cross_log_edge_is_owned_by_consuming_log(self) -> None:
        invocation = GRAPH.NodeKey(
            "docs/consumer", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        artifact = GRAPH.NodeKey(
            "docs/owner", GRAPH.NodeKind.ARTIFACT, "data/shared.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(invocation, self.origin())
        builder.add_node(artifact, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.CROSS_LOG_USE,
            invocation,
            artifact,
            "docs/consumer",
            self.origin(),
        )
        edge = builder.build().edges[0]
        self.assertEqual(edge.owner_log, "docs/consumer")

    def test_graph_root_merges_independent_origins(self) -> None:
        key = GRAPH.NodeKey("docs/owner", GRAPH.NodeKind.SCRIPT, "scripts/shared.py")
        builder = GRAPH.GraphBuilder("test-rules")
        first = self.origin()
        second = GRAPH.FactOrigin(
            kind=GRAPH.OriginKind.MECHANICAL,
            resolver="second-consumer",
            inputs=(GRAPH.OriginInput("other", "def456"),),
            rules_version="test-rules",
        )
        builder.add_node(key, first)
        builder.add_root(key, GRAPH.RootPolicy.INCOMING_CROSS_LOG, first)
        builder.add_root(key, GRAPH.RootPolicy.INCOMING_CROSS_LOG, second)
        self.assertEqual(len(builder.build().roots[0].origins), 2)

    def test_recorded_command_root_retains_code_but_not_material(self) -> None:
        invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        script = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        input_artifact = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/input.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (invocation, script, input_artifact):
            builder.add_node(key, self.origin(), {"orphanable": key != invocation})
        builder.add_edge(
            GRAPH.EdgeKind.INVOKES,
            invocation,
            script,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            invocation,
            input_artifact,
            "docs/mini",
            self.origin(),
        )
        builder.add_root(invocation, GRAPH.RootPolicy.RECORDED_COMMAND, self.origin())
        reached = GRAPH_QUERIES.reachable_nodes(builder.build())
        self.assertIn(script, reached)
        self.assertNotIn(input_artifact, reached)

    def test_presented_workflow_retains_inputs_code_and_sibling_outputs(self) -> None:
        presented = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.PRESENTED, "e001:result")
        invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        script = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        target = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.ARTIFACT, "data/result.csv")
        sibling = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "images/result.png"
        )
        input_artifact = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/input.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (presented, invocation, script, target, sibling, input_artifact):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            target,
            presented,
            "docs/mini",
            self.origin(),
        )
        for output in (target, sibling):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                invocation,
                output,
                "docs/mini",
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            invocation,
            input_artifact,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.INVOKES,
            invocation,
            script,
            "docs/mini",
            self.origin(),
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())
        reached = GRAPH_QUERIES.reachable_nodes(builder.build())
        self.assertTrue(
            {presented, invocation, script, target, sibling, input_artifact} <= reached
        )
        provenance = GRAPH_QUERIES.provenance_nodes(builder.build())
        self.assertTrue(
            {presented, invocation, script, target, input_artifact} <= provenance
        )
        self.assertNotIn(sibling, provenance)

    def test_sibling_output_does_not_bridge_to_an_unrelated_producer(self) -> None:
        presented = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.PRESENTED, "e001:result")
        target = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.ARTIFACT, "data/result.csv")
        shared_output = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.COLLECTION, "images")
        selected = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:selected"
        )
        unrelated = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:unrelated"
        )
        unrelated_input = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/unrelated.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (
            presented,
            target,
            shared_output,
            selected,
            unrelated,
            unrelated_input,
        ):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            target,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            selected,
            target,
            "docs/mini",
            self.origin(),
        )
        for invocation in (selected, unrelated):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                invocation,
                shared_output,
                "docs/mini",
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            unrelated,
            unrelated_input,
            "docs/mini",
            self.origin(),
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        reached = GRAPH_QUERIES.reachable_nodes(builder.build())
        self.assertIn(shared_output, reached)
        self.assertNotIn(unrelated, reached)
        self.assertNotIn(unrelated_input, reached)

    def test_ambiguous_command_path_does_not_bridge_shared_container(self) -> None:
        presented = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.PRESENTED, "e001:result")
        target = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.ARTIFACT, "data/result.csv")
        shared = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.COLLECTION, "images")
        selected = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:selected"
        )
        unrelated = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:unrelated"
        )
        unrelated_input = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/unrelated.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (
            presented,
            target,
            shared,
            selected,
            unrelated,
            unrelated_input,
        ):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            target,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            selected,
            target,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            selected,
            shared,
            "docs/mini",
            self.origin(),
            {"semantic_direction": "unresolved-command-path"},
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            unrelated,
            shared,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.CONSUMES,
            unrelated,
            unrelated_input,
            "docs/mini",
            self.origin(),
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        reached = GRAPH_QUERIES.reachable_nodes(builder.build())
        self.assertIn(shared, reached)
        self.assertNotIn(unrelated, reached)
        self.assertNotIn(unrelated_input, reached)
        provenance = GRAPH_QUERIES.provenance_nodes(builder.build())
        self.assertNotIn(shared, provenance)

    def test_reviewed_input_does_not_turn_consumer_into_producer(self) -> None:
        namespace = "docs/mini"
        entry_path = "docs/mini/entries/2026-08-07-e001/e001.md"
        script_path = "docs/mini/entries/2026-08-07-e001/scripts/consume.py"
        source_path = "output/random/source.h5"
        result_path = "docs/mini/entries/2026-08-07-e001/data/result.csv"
        scan = {
            "summary": "docs/mini.md",
            "validation_rules_version": "test-rules",
            "entry_order": ["e001"],
            "entries": [
                {
                    "id": "e001",
                    "title": "Consumer",
                    "path": entry_path,
                    "commands": [
                        {
                            "line": 10,
                            "command": (
                                f"python {script_path} --input {source_path} "
                                f"--output {result_path}"
                            ),
                            "script": script_path,
                            "matlab_scripts": [],
                            "data_tokens": [],
                            "path_arguments": [
                                {
                                    "path": source_path,
                                    "role_hint": "input",
                                    "option": "--input",
                                },
                                {
                                    "path": result_path,
                                    "role_hint": "output",
                                    "option": "--output",
                                },
                            ],
                        }
                    ],
                    "candidate_targets": [
                        {
                            "identity": source_path,
                            "presented": True,
                            "sections": ["Results"],
                            "mechanical": {"status": "present"},
                        }
                    ],
                    "evidence_record": {"rows": []},
                    "data_index": {},
                }
            ],
            "files": {
                "docs/mini.md": {"sha256": "summary"},
                entry_path: {"sha256": "entry"},
                script_path: {"sha256": "script"},
                source_path: {"sha256": "source"},
                result_path: {"sha256": "result"},
            },
            "resolved_paths": {
                script_path: script_path,
                source_path: source_path,
                result_path: result_path,
            },
            "script_inventory": [script_path],
            "script_dependency_graph": {script_path: []},
            "directory_memberships": {},
            "mechanical_checks": {},
            "repository_dependencies": [],
            "repository_graph_edges": [],
            "summary_items": [],
            "evidence_records": {"summary": {"rows": []}},
        }
        adjudication = {
            "entries": [
                {
                    "id": "e001",
                    "targets": [
                        {
                            "target": source_path,
                            "provenance": "2026-08-11",
                            "dependencies": [
                                {"path": script_path, "role": "producer"},
                                {"path": source_path, "role": "input"},
                            ],
                        }
                    ],
                }
            ]
        }

        graph = GRAPH_ADAPTER.build_dependency_graph(scan, adjudication)
        invocation = next(
            node.key
            for node in graph.nodes
            if node.key.kind is GRAPH.NodeKind.INVOCATION
        )
        presented = next(
            node.key
            for node in graph.nodes
            if node.key.kind is GRAPH.NodeKind.PRESENTED
        )
        source = GRAPH.NodeKey(namespace, GRAPH.NodeKind.ARTIFACT, source_path)
        script = GRAPH.NodeKey(namespace, GRAPH.NodeKind.SCRIPT, script_path)

        self.assertFalse(
            any(
                edge.kind is GRAPH.EdgeKind.PRODUCES
                and edge.source == invocation
                and edge.target == source
                for edge in graph.edges
            )
        )
        provenance = GRAPH_QUERIES.provenance_nodes(
            graph, [(presented, GRAPH.RootPolicy.PRESENTED)]
        )
        self.assertIn(source, provenance)
        self.assertNotIn(invocation, provenance)
        self.assertNotIn(script, provenance)

        adjudication["entries"][0]["targets"][0]["producer_invocation"] = (
            GRAPH_ADAPTER.recorded_invocation_identity(
                "e001", 1, scan["entries"][0]["commands"][0]
            )
        )
        with self.assertRaisesRegex(
            GRAPH.GraphContractError,
            "reviewed producer mechanically consumes its target",
        ):
            GRAPH_ADAPTER.build_dependency_graph(scan, adjudication)

        collection_path = "docs/mini/data"
        command = scan["entries"][0]["commands"][0]
        command["path_arguments"] = [
            {"path": collection_path, "role_hint": "input", "option": "--root"},
            {"path": source_path, "role_hint": "output", "option": "--output"},
        ]
        scan["resolved_paths"][collection_path] = collection_path
        scan["mechanical_checks"][collection_path] = {"type": "directory"}
        adjudication["entries"][0]["targets"][0]["dependencies"] = [
            {"path": script_path, "role": "producer"},
            {"path": collection_path, "role": "input"},
        ]

        graph = GRAPH_ADAPTER.build_dependency_graph(scan, adjudication)

        self.assertTrue(
            any(
                edge.kind is GRAPH.EdgeKind.PRODUCES
                and edge.source == invocation
                and edge.target == source
                for edge in graph.edges
            )
        )

    def test_reviewed_collection_scope_controls_consumer_direction(self) -> None:
        collection = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.COLLECTION, "data"
        )
        target = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/output.csv"
        )

        self.assertFalse(
            GRAPH_ADAPTER._reviewed_input_collection_contains_target(
                collection,
                target,
                [
                    {
                        "path": "data",
                        "role": "input",
                        "members": ["input.csv"],
                    }
                ],
            )
        )
        self.assertTrue(
            GRAPH_ADAPTER._reviewed_input_collection_contains_target(
                collection,
                target,
                [
                    {
                        "path": "data",
                        "role": "input",
                        "members": ["output.csv"],
                    }
                ],
            )
        )
        self.assertTrue(
            GRAPH_ADAPTER._reviewed_input_collection_contains_target(
                collection,
                target,
                [{"path": "data", "role": "input"}],
            )
        )

    def test_explicit_producer_may_write_inside_an_input_directory(self) -> None:
        namespace = "docs/mini"
        target_path = "docs/mini/data/output.csv"
        collection_path = "docs/mini/data"
        script_path = "docs/mini/scripts/produce.py"
        command = {
            "command": "python produce.py --data-dir data --output output.csv",
            "line": 10,
            "path_arguments": [
                {"path": collection_path, "role_hint": "input"},
                {"path": "output.csv", "role_hint": "unknown"},
            ],
            "script": script_path,
        }
        scan = {
            "summary": "docs/mini.md",
            "project_root": ".",
            "validation_rules_version": "test-rules",
            "resolved_paths": {
                target_path: target_path,
                collection_path: collection_path,
                script_path: script_path,
            },
            "mechanical_checks": {collection_path: {"type": "directory"}},
            "script_inventory": [script_path],
            "entries": [
                {
                    "id": "e001",
                    "path": "docs/mini/e001.md",
                    "commands": [command],
                    "data_tokens": [],
                }
            ],
            "repository_edges": [],
            "material_owners": {},
        }
        adjudication = {
            "entries": [
                {
                    "id": "e001",
                    "targets": [
                        {
                            "target": target_path,
                            "provenance": "2026-08-12",
                            "producer_invocation": (
                                GRAPH_ADAPTER.recorded_invocation_identity(
                                    "e001", 1, command
                                )
                            ),
                            "dependencies": [
                                {"path": script_path, "role": "producer"},
                                {"path": collection_path, "role": "input"},
                            ],
                        }
                    ],
                }
            ],
            "summary": [],
        }

        graph = GRAPH_ADAPTER.build_dependency_graph(scan, adjudication)

        invocation = next(
            node.key
            for node in graph.nodes
            if node.key.kind is GRAPH.NodeKind.INVOCATION
        )
        target = GRAPH.NodeKey(namespace, GRAPH.NodeKind.ARTIFACT, target_path)
        self.assertTrue(
            any(
                edge.kind is GRAPH.EdgeKind.PRODUCES
                and edge.source == invocation
                and edge.target == target
                for edge in graph.edges
            )
        )
    def test_reviewed_target_accepts_an_exact_cross_entry_producer(self) -> None:
        invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:L10:1:producer"
        )
        script = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.SCRIPT, "docs/mini/e001/produce.py"
        )
        selection = GRAPH_ADAPTER._ReviewedProducerSelection(
            namespace="docs/mini",
            entry_id="e002",
            target_identity="docs/mini/e001/result.csv",
            producer_identity=invocation.identity,
            candidates=[],
            consumer_candidates=[],
            producer_scripts={script},
            invocation_scripts={invocation: {script}},
            required=True,
        )

        selected = GRAPH_ADAPTER._selected_reviewed_invocation(selection)

        self.assertEqual(selected, invocation)

    def test_presented_collection_retains_only_selected_members(self) -> None:
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:collection"
        )
        collection = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.COLLECTION, "data/results"
        )
        selected = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.MEMBER, "data/results/selected.csv"
        )
        unselected = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.MEMBER, "data/results/other.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (presented, collection, selected, unselected):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            collection,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            selected,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": True},
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            unselected,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": False},
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())
        reached = GRAPH_QUERIES.reachable_nodes(builder.build())
        self.assertIn(selected, reached)
        self.assertNotIn(unselected, reached)

    def test_unique_output_collection_retains_all_sibling_outputs(self) -> None:
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:result"
        )
        result = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "data/results/result.csv"
        )
        invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        collection = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.COLLECTION, "data/results"
        )
        sibling = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.MEMBER, "data/results/diagnostic.png"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (presented, result, invocation, collection, sibling):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            result,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            invocation,
            result,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            invocation,
            collection,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            sibling,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": False},
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        self.assertIn(sibling, GRAPH_QUERIES.reachable_nodes(builder.build()))

    def test_shared_output_collection_does_not_retain_unselected_outputs(self) -> None:
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:result"
        )
        result = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "images/result.png"
        )
        selected_invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        unrelated_invocation = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:2"
        )
        collection = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.COLLECTION, "images"
        )
        unrelated = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.MEMBER, "images/unrelated.png"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (
            presented,
            result,
            selected_invocation,
            unrelated_invocation,
            collection,
            unrelated,
        ):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            result,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.PRODUCES,
            selected_invocation,
            result,
            "docs/mini",
            self.origin(),
        )
        for invocation in (selected_invocation, unrelated_invocation):
            builder.add_edge(
                GRAPH.EdgeKind.PRODUCES,
                invocation,
                collection,
                "docs/mini",
                self.origin(),
            )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            unrelated,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": False},
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        self.assertNotIn(
            unrelated, GRAPH_QUERIES.reachable_nodes(builder.build())
        )

    def test_reviewed_selection_can_overlay_discovered_membership(self) -> None:
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:collection"
        )
        collection = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.COLLECTION, "data/results"
        )
        member = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.MEMBER, "data/results/selected.csv"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        for key in (presented, collection, member):
            builder.add_node(key, self.origin())
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            collection,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            member,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": False},
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            member,
            collection,
            "docs/mini",
            self.origin(GRAPH.OriginKind.SEMANTIC, "e001:data/results"),
            {"selected": True},
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        graph = builder.build()
        self.assertEqual(len(graph.edges), 3)
        self.assertIn(member, GRAPH_QUERIES.reachable_nodes(graph))

    def test_used_member_protects_container_without_expanding_provenance(self) -> None:
        presented = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.PRESENTED, "e001:artifact"
        )
        collection = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.COLLECTION, "images")
        member = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.ARTIFACT, "images/result.png"
        )
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(presented, self.origin())
        builder.add_node(collection, self.origin(), {"orphanable": True})
        builder.add_node(member, self.origin(), {"orphanable": True})
        builder.add_edge(
            GRAPH.EdgeKind.SUPPORTS,
            member,
            presented,
            "docs/mini",
            self.origin(),
        )
        builder.add_edge(
            GRAPH.EdgeKind.MEMBER_OF,
            member,
            collection,
            "docs/mini",
            self.origin(),
            {"selected": False},
        )
        builder.add_root(presented, GRAPH.RootPolicy.PRESENTED, self.origin())

        graph = builder.build()
        self.assertNotIn(collection, GRAPH_QUERIES.reachable_nodes(graph))
        self.assertNotIn(collection, GRAPH_QUERIES.orphan_nodes(graph))

    def test_unresolved_orphan_cannot_be_reachable(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin(), {"orphanable": True})
        builder.add_root(key, GRAPH.RootPolicy.RETENTION, self.origin())
        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "reachable from an applicable root"
        ):
            GRAPH_QUERIES.assert_unresolved_orphans_unreachable(builder.build(), [key])

    def test_dependency_graph_round_trips_exactly(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin(), {"orphanable": True})
        builder.add_root(key, GRAPH.RootPolicy.RECORDED_COMMAND, self.origin())
        graph = builder.build()

        loaded = GRAPH.DependencyGraph.from_dict(graph.as_dict())

        self.assertEqual(loaded.as_dict(), graph.as_dict())
        self.assertEqual(loaded.identity, graph.identity)

    def test_dependency_graph_rejects_coercible_nonstring_fields(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin())
        graph_value = builder.build().as_dict()
        graph_value["nodes"][0]["key"]["namespace"] = 7

        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "node namespace must be a string"
        ):
            GRAPH.DependencyGraph.from_dict(graph_value)

    def test_slice_record_rejects_coercible_nonstring_summary(self) -> None:
        record = GRAPH_STORE.slice_record(
            GRAPH.GraphBuilder("test-rules").build(), "docs/mini.md", {}
        )
        record["summary"] = 7

        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "summary must be a string"
        ):
            GRAPH_STORE.load_slice(record)

    def test_slice_record_deduplicates_origins_and_round_trips(self) -> None:
        first = GRAPH.NodeKey(
            "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        second = GRAPH.NodeKey(
            "docs/other", GRAPH.NodeKind.ARTIFACT, "docs/other/data/output.csv"
        )
        origin = self.origin()
        other_origin = GRAPH.FactOrigin(
            kind=GRAPH.OriginKind.MECHANICAL,
            resolver="other-test-resolver",
            inputs=(GRAPH.OriginInput("fixture-2", "def456"),),
            rules_version="test-rules",
        )
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(first, origin)
        builder.add_node(first, other_origin)
        builder.add_node(second, origin)
        builder.add_edge(
            GRAPH.EdgeKind.CROSS_LOG_USE,
            first,
            second,
            "docs/mini",
            origin,
        )
        builder.add_edge(
            GRAPH.EdgeKind.CROSS_LOG_USE,
            first,
            second,
            "docs/mini",
            other_origin,
        )
        graph = builder.build()

        record = GRAPH_STORE.slice_record(
            graph,
            "docs/mini.md",
            {
                "fixture": {"size": 1, "sha256": "a" * 64},
                "fixture-2": {"size": 1, "sha256": "b" * 64},
            },
        )
        summary, loaded = GRAPH_STORE.load_slice(record)

        self.assertEqual(record["schema_version"], 6)
        self.assertEqual(len(record["graph"]["origins"]), 2)
        self.assertEqual(summary, "docs/mini.md")
        self.assertEqual(loaded.as_dict(), graph.as_dict())

        corrupted = json.loads(json.dumps(record))
        corrupted["graph"]["origins"]["0" * 64] = origin.as_dict()
        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "origin identity is invalid"
        ):
            GRAPH_STORE.load_slice(corrupted)

    def test_owned_child_makes_its_dependency_container_local(self) -> None:
        local = {"output/logs/mini/e001/data/run/result.csv"}
        self.assertEqual(
            GRAPH_ADAPTER._material_namespace(
                "output/logs/mini/e001/data/run",
                "docs/mini",
                local,
                {},
            ),
            "docs/mini",
        )

    def test_repository_owner_map_emits_new_cross_log_command_use(self) -> None:
        entry_path = "docs/consumer/entries/2026-08-07-e001/e001.md"
        script_path = "docs/consumer/entries/2026-08-07-e001/scripts/use.py"
        shared_path = "docs/owner/entries/2026-08-07-e001/data/shared.csv"
        shared_script = "docs/owner/scripts/shared.py"
        scan = {
            "summary": "docs/consumer.md",
            "project_root": Path.cwd().as_posix(),
            "validation_rules_version": "test-rules",
            "entry_order": ["e001"],
            "entries": [
                {
                    "id": "e001",
                    "path": entry_path,
                    "commands": [
                        {
                            "line": 8,
                            "command": f"python {script_path} --input {shared_path}",
                            "script": script_path,
                            "matlab_scripts": [],
                            "data_tokens": [],
                            "path_arguments": [
                                {
                                    "path": shared_path,
                                    "role_hint": "input",
                                    "option": "--input",
                                }
                            ],
                        },
                        {
                            "line": 9,
                            "command": f"python {shared_script}",
                            "script": shared_script,
                            "matlab_scripts": [],
                            "data_tokens": [],
                            "path_arguments": [],
                        },
                    ],
                    "candidate_targets": [],
                    "orphan_inventory": [],
                    "evidence_record": {"rows": []},
                    "data_index": {},
                }
            ],
            "files": {},
            "resolved_paths": {
                script_path: script_path,
                shared_path: shared_path,
                shared_script: shared_script,
            },
            "script_inventory": [script_path],
            "script_dependency_graph": {},
            "directory_memberships": {},
            "mechanical_checks": {},
            "repository_dependencies": [],
            "repository_material_owners": {
                shared_path: {"namespace": "docs/owner", "kind": "artifact"},
                shared_script: {"namespace": "docs/owner", "kind": "script"},
            },
            "repository_graph_edges": [],
            "summary_items": [],
            "evidence_records": {"summary": {"rows": []}},
        }

        graph = GRAPH_ADAPTER.build_dependency_graph(scan)
        cross_log = [
            edge for edge in graph.edges if edge.kind is GRAPH.EdgeKind.CROSS_LOG_USE
        ]

        self.assertEqual(len(cross_log), 2)
        self.assertEqual(
            {(edge.target.identity, edge.target.kind) for edge in cross_log},
            {
                (shared_path, GRAPH.NodeKind.ARTIFACT),
                (shared_script, GRAPH.NodeKind.SCRIPT),
            },
        )
        self.assertTrue(
            all(edge.target.namespace == "docs/owner" for edge in cross_log)
        )
        self.assertTrue(all(edge.owner_log == "docs/consumer" for edge in cross_log))

    def test_repository_owner_inventory_bootstraps_without_slices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "owner.md"
            entry = root / "docs" / "owner" / "entries" / "2026-08-07-e001-owner"
            write(summary, "# Owner\n\n## Entries\n")
            write(entry / "e001.md", "# Entry\n")
            artifact = entry / "data" / "shared.csv"
            script = entry / "scripts" / "build.py"
            write(artifact, "value\n1\n")
            write(script, "VALUE = 1\n")

            owners = GRAPH_STORE.repository_material_owners(
                root, RUNTIME.MATERIAL_INVENTORY_POLICY
            )

            self.assertEqual(
                owners[artifact.relative_to(root).as_posix()],
                {"namespace": "docs/owner", "kind": "artifact"},
            )
            self.assertEqual(
                owners[script.relative_to(root).as_posix()],
                {"namespace": "docs/owner", "kind": "script"},
            )

    def test_scan_repository_view_replaces_log_when_no_slice_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            args = mock.Mock(summary=summary, repository_index=None)

            view, metrics = CLI.repository_view_for_scan(args, root)

            artifact = (entry.parent / "data" / "output.csv").relative_to(root)
            self.assertEqual(metrics["status"], "replacement")
            self.assertEqual(
                view["material_owners"][artifact.as_posix()]["namespace"],
                "docs/mini",
            )
            self.assertEqual(view["graph_edges"], [])
            self.assertEqual(view["slices"], {})

    def test_scan_repository_view_discovers_repository_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            args = mock.Mock(summary=summary, repository_index=None)
            original_walk = GRAPH_STORE.os.walk
            calls = 0

            def counted_walk(*walk_args, **kwargs):
                nonlocal calls
                if Path(walk_args[0]).resolve() == root.resolve():
                    calls += 1
                return original_walk(*walk_args, **kwargs)

            with mock.patch.object(
                GRAPH_STORE.os, "walk", side_effect=counted_walk
            ):
                CLI.repository_view_for_scan(args, root)

            self.assertEqual(calls, 1)

    def test_repository_replacement_ignores_own_malformed_slice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            write(summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME, "{}\n")
            args = mock.Mock(summary=summary, repository_index=None)

            view, _metrics = CLI.repository_view_for_scan(args, root)
            self.assertEqual(view["scope"]["kind"], "replacement")
            self.assertEqual(view["slices"], {})

    def test_repository_replacement_ignores_other_malformed_slice_during_rollout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            other_summary = root / "docs" / "other.md"
            write(other_summary, "# Other\n\n## Entries\n")
            write(
                other_summary.with_suffix("")
                / "entries"
                / "2026-08-12-e001-other"
                / "e001.md",
                "# Entry\n",
            )
            write(other_summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME, "{}\n")
            args = mock.Mock(summary=summary, repository_index=None)

            view, metrics = CLI.repository_view_for_scan(args, root)

            self.assertFalse(view["scope"]["cross_log_complete"])
            self.assertEqual(
                metrics["status"], "replacement-cross-log-incomplete"
            )

    def test_incomplete_rollout_withholds_orphan_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            other_summary = root / "docs" / "other.md"
            write(other_summary, "# Other\n\n## Entries\n")
            write(
                other_summary.with_suffix("") / "entries" / "e001.md",
                "# Other Entry\n",
            )
            write(other_summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME, "{}\n")
            args = mock.Mock(summary=summary, repository_index=None)
            view, _metrics = CLI.repository_view_for_scan(args, root)

            scan, _scan_metrics = RUNTIME.scan_log(
                summary,
                jobs=1,
                repository_index=view,
            )

            self.assertFalse(scan["repository_scope"]["cross_log_complete"])
            self.assertFalse(
                any(
                    entry.get("orphan_candidates")
                    for entry in scan["entries"]
                    if "error" not in entry
                )
            )

            adjudication = RUNTIME.prepare_adjudication_record(scan, "2026-08-12")
            DECISIONS.reconcile_graph_orphans(scan, adjudication)
            self.assertFalse(
                any(
                    item["kind"] == "orphan_candidates"
                    for item in adjudication["review_queue"]
                )
            )

            complete = adjudication_for(scan, _entry)
            for entry in complete["entries"]:
                entry["orphan_items"] = [
                    {
                        "identity": item["identity"],
                        "decision": "deferred",
                        "basis": "cross-log-incomplete",
                    }
                    for item in entry["orphan_items"]
                ]
            output = summary.with_suffix("")
            RUNTIME.render_records(complete, scan, output)
            report = (output / "validation.md").read_text(encoding="utf-8")
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            self.assertIn("Cross-log orphan review: DEFERRED", report)
            self.assertEqual(state["orphan_dispositions"], [])

            other_record = GRAPH_STORE.slice_record(
                GRAPH.GraphBuilder(RUNTIME.RULES_VERSION).build(),
                other_summary.relative_to(root).as_posix(),
                {},
                {},
            )
            write(
                other_summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(other_record),
            )
            complete_view, _metrics = CLI.repository_view_for_scan(args, root)
            rescanned, _metrics = RUNTIME.scan_log(
                summary,
                jobs=1,
                repository_index=complete_view,
            )
            self.assertTrue(rescanned["repository_scope"]["cross_log_complete"])
            self.assertTrue(
                any(
                    entry.get("orphan_candidates")
                    for entry in rescanned["entries"]
                    if "error" not in entry
                )
            )

    def test_repository_replacement_ignores_own_incompatible_slice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            builder = GRAPH.GraphBuilder("different-rules")
            record = GRAPH_STORE.slice_record(builder.build(), "docs/mini.md", {})
            write(
                summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(record),
            )
            args = mock.Mock(summary=summary, repository_index=None)

            view, _metrics = CLI.repository_view_for_scan(args, root)
            self.assertEqual(view["scope"]["kind"], "replacement")
            self.assertEqual(view["slices"], {})

    def test_repository_replacement_uses_material_owners_from_other_slice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            other_summary = root / "docs" / "other.md"
            other_entry = root / "docs" / "other" / "entries" / "e001.md"
            write(other_summary, "# Other\n\n## Entries\n")
            write(other_entry, "# Other Entry\n")
            identity = "docs/other/entries/data/output.csv"
            owners = {
                identity: {"namespace": "docs/other", "kind": "artifact"}
            }
            record = GRAPH_STORE.slice_record(
                GRAPH.GraphBuilder(RUNTIME.RULES_VERSION).build(),
                "docs/other.md",
                {},
                owners,
            )
            write(
                other_summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(record),
            )
            args = mock.Mock(summary=summary, repository_index=None)

            with mock.patch.object(
                GRAPH_STORE,
                "repository_material_owners",
                side_effect=AssertionError("replacement must not rescan all logs"),
            ):
                view, _metrics = CLI.repository_view_for_scan(args, root)

            self.assertEqual(view["material_owners"][identity], owners[identity])

    def test_slice_filters_material_owner_from_another_log(self) -> None:
        record = GRAPH_STORE.slice_record(
            GRAPH.GraphBuilder("test-rules").build(),
            "docs/mini.md",
            {},
            {
                "docs/other/data/output.csv": {
                    "namespace": "docs/other",
                    "kind": "artifact",
                }
            },
        )

        self.assertEqual(record["material_owners"], {})

    def test_cross_log_slice_source_change_invalidates_persisted_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "docs" / "consumer.md"
            write(source_path, "first\n")
            source_identity = source_path.relative_to(root).as_posix()
            invocation = GRAPH.NodeKey(
                "docs/consumer", GRAPH.NodeKind.INVOCATION, "e001:command:1"
            )
            artifact = GRAPH.NodeKey(
                "docs/owner", GRAPH.NodeKind.ARTIFACT, "docs/owner/data/shared.csv"
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="test-cross-log-source",
                inputs=(GRAPH.OriginInput(source_identity, "abc123"),),
                rules_version="test-rules",
            )
            builder = GRAPH.GraphBuilder("test-rules")
            builder.add_node(invocation, origin)
            builder.add_node(artifact, origin)
            builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                invocation,
                artifact,
                "docs/consumer",
                origin,
            )
            record = GRAPH_STORE.slice_record(
                builder.build(),
                "docs/consumer.md",
                {source_identity: INVENTORY.file_identity(source_path)},
            )

            self.assertTrue(GRAPH_STORE.validate_slice_source_inputs(root, record))
            write(source_path, "second\n")
            self.assertFalse(GRAPH_STORE.validate_slice_source_inputs(root, record))

    def test_repository_replacement_ignores_own_stale_slice_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            source_identity = summary.relative_to(root).as_posix()
            invocation = GRAPH.NodeKey(
                "docs/mini", GRAPH.NodeKind.INVOCATION, "e001:command:1"
            )
            artifact = GRAPH.NodeKey(
                "docs/other", GRAPH.NodeKind.ARTIFACT, "docs/other/data/shared.csv"
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="test-stale-slice",
                inputs=(GRAPH.OriginInput(source_identity, "fixture"),),
                rules_version=RUNTIME.RULES_VERSION,
            )
            builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            builder.add_node(invocation, origin)
            builder.add_node(artifact, origin)
            builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                invocation,
                artifact,
                "docs/mini",
                origin,
            )
            record = GRAPH_STORE.slice_record(
                builder.build(),
                "docs/mini.md",
                {source_identity: INVENTORY.file_identity(summary)},
            )
            write(
                summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(record),
            )
            write(summary, summary.read_text(encoding="utf-8") + "\nchanged\n")
            args = mock.Mock(summary=summary, repository_index=None)

            view, _metrics = CLI.repository_view_for_scan(args, root)
            self.assertEqual(view["scope"]["kind"], "replacement")
            self.assertEqual(view["slices"], {})

    def test_graph_aggregate_keeps_cross_log_edge_with_consumer_slice(self) -> None:
        invocation = GRAPH.NodeKey(
            "docs/consumer", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        artifact = GRAPH.NodeKey(
            "docs/owner", GRAPH.NodeKind.ARTIFACT, "docs/owner/data/shared.csv"
        )
        consumer_builder = GRAPH.GraphBuilder("test-rules")
        for key in (invocation, artifact):
            consumer_builder.add_node(key, self.origin())
        consumer_builder.add_edge(
            GRAPH.EdgeKind.CROSS_LOG_USE,
            invocation,
            artifact,
            "docs/consumer",
            self.origin(),
        )
        owner_builder = GRAPH.GraphBuilder("test-rules")
        owner_builder.add_node(artifact, self.origin(), {"orphanable": True})
        consumer_record = GRAPH_STORE.slice_record(
            consumer_builder.build(),
            "docs/consumer.md",
            {"fixture": {"size": 1, "sha256": "a" * 64}},
        )
        owner_record = GRAPH_STORE.slice_record(
            owner_builder.build(), "docs/owner.md", {}
        )

        aggregate = GRAPH_STORE.aggregate_records([owner_record, consumer_record])

        self.assertEqual(len(aggregate["incoming"]["docs/owner.md"]), 1)
        self.assertEqual(
            aggregate["incoming"]["docs/owner.md"][0]["owner_log"],
            "docs/consumer",
        )
        self.assertEqual(
            owner_record,
            GRAPH_STORE.slice_record(owner_builder.build(), "docs/owner.md", {}),
        )

        manifest, incoming = GRAPH_STORE.aggregate_files(aggregate)
        wrong_owner = incoming["incoming"].pop("docs/owner.md")
        incoming["incoming"]["docs/wrong.md"] = wrong_owner
        incoming_payload = dict(incoming)
        incoming_payload.pop("identity")
        incoming["identity"] = hashlib.sha256(
            json.dumps(incoming_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        manifest["incoming_identity"] = incoming["identity"]
        manifest_payload = dict(manifest)
        manifest_payload.pop("identity")
        manifest["identity"] = hashlib.sha256(
            json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "incoming edge owner is invalid"
        ):
            GRAPH_STORE.load_aggregate_files(manifest, incoming)

    def test_aggregate_loader_rejects_rehashed_malformed_log_rows(self) -> None:
        builder = GRAPH.GraphBuilder("test-rules")
        record = GRAPH_STORE.slice_record(builder.build(), "docs/mini.md", {})
        manifest, incoming = GRAPH_STORE.aggregate_files(
            GRAPH_STORE.aggregate_records([record])
        )
        manifest["logs"][0]["summary"] = 7
        payload = dict(manifest)
        payload.pop("identity")
        manifest["identity"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(GRAPH.GraphContractError, "fields must be strings"):
            GRAPH_STORE.load_aggregate_files(manifest, incoming)

    def test_graph_aggregate_rejects_duplicate_log_slices(self) -> None:
        key = GRAPH.NodeKey("docs/mini", GRAPH.NodeKind.SCRIPT, "scripts/run.py")
        builder = GRAPH.GraphBuilder("test-rules")
        builder.add_node(key, self.origin())
        record = GRAPH_STORE.slice_record(builder.build(), "docs/mini.md", {})

        with self.assertRaisesRegex(
            GRAPH.GraphContractError, "duplicate validation index slice"
        ):
            GRAPH_STORE.aggregate_records([record, record])

    def test_slice_discovery_requires_owning_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            maintained = summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME
            leaked = (
                summary.parent
                / ".mini-validation-generation-abcd"
                / GRAPH_STORE.SLICE_FILENAME
            )
            write(maintained, "{}\n")
            write(leaked, "{}\n")

            self.assertEqual(
                list(GRAPH_STORE.slice_paths(root)), [maintained.resolve()]
            )

    def test_slice_discovery_follows_non_docs_maintained_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "research" / "mini.md"
            entry = summary.with_suffix("") / "entries" / "2026-08-12-e001-mini"
            maintained = summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME
            write(summary, "# Mini\n\n## Entries\n")
            write(entry / "e001.md", "# Entry\n")
            write(maintained, "{}\n")

            self.assertEqual(
                GRAPH_STORE.discover_repository_summaries(root), [summary.resolve()]
            )
            self.assertEqual(
                list(GRAPH_STORE.slice_paths(root)), [maintained.resolve()]
            )

    def test_repository_discovery_covers_mixed_layout_and_excludes_generated_trees(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs_summary, _entry = make_log(root)
            outside = root / "research" / "outside.md"
            excluded = root / ".conda" / "fake.md"
            staging = root / ".validation-staging-test" / "staged.md"
            for summary in (outside, excluded, staging):
                write(summary, "# Log\n")
                write(summary.with_suffix("") / "entries" / "e001.md", "# Entry\n")

            discovered = GRAPH_STORE.discover_repository_summaries(root)

            self.assertEqual(
                discovered,
                sorted([docs_summary.resolve(), outside.resolve()]),
            )

    def test_replacement_view_discovers_repository_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            original_walk = GRAPH_STORE.os.walk
            calls = 0

            def counted_walk(*args, **kwargs):
                nonlocal calls
                if Path(args[0]).resolve() == root.resolve():
                    calls += 1
                return original_walk(*args, **kwargs)

            with mock.patch.object(
                GRAPH_STORE.os, "walk", side_effect=counted_walk
            ):
                GRAPH_STORE.replacement_repository_view(
                    root,
                    summary,
                    RUNTIME.RULES_VERSION,
                    RUNTIME.MATERIAL_INVENTORY_POLICY,
                )

            self.assertEqual(calls, 1)

    def test_aggregate_build_discovers_repository_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            graph = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION).build()
            record = GRAPH_STORE.slice_record(
                graph, summary.relative_to(root).as_posix(), {}
            )
            write(
                summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(record, indent=2) + "\n",
            )
            original_walk = GRAPH_STORE.os.walk
            calls = 0

            def counted_walk(*args, **kwargs):
                nonlocal calls
                if Path(args[0]).resolve() == root.resolve():
                    calls += 1
                return original_walk(*args, **kwargs)

            with mock.patch.object(
                GRAPH_STORE.os, "walk", side_effect=counted_walk
            ):
                GRAPH_STORE.build_repository_aggregate(
                    root, RUNTIME.RULES_VERSION
                )

            self.assertEqual(calls, 1)

    def test_updating_consumer_slice_leaves_owner_slice_byte_stable(self) -> None:
        invocation = GRAPH.NodeKey(
            "docs/consumer", GRAPH.NodeKind.INVOCATION, "e001:command:1"
        )
        artifact = GRAPH.NodeKey(
            "docs/owner", GRAPH.NodeKind.ARTIFACT, "docs/owner/data/shared.csv"
        )
        owner_builder = GRAPH.GraphBuilder("test-rules")
        owner_builder.add_node(artifact, self.origin(), {"orphanable": True})
        owner_record = GRAPH_STORE.slice_record(
            owner_builder.build(), "docs/owner.md", {}
        )
        owner_bytes = json.dumps(owner_record, sort_keys=True, separators=(",", ":"))

        consumer_builder = GRAPH.GraphBuilder("test-rules")
        for key in (invocation, artifact):
            consumer_builder.add_node(key, self.origin())
        consumer_builder.add_edge(
            GRAPH.EdgeKind.CROSS_LOG_USE,
            invocation,
            artifact,
            "docs/consumer",
            self.origin(),
        )
        consumer_record = GRAPH_STORE.slice_record(
            consumer_builder.build(),
            "docs/consumer.md",
            {"fixture": {"size": 1, "sha256": "a" * 64}},
        )
        aggregate = GRAPH_STORE.aggregate_records([owner_record, consumer_record])

        self.assertEqual(
            owner_bytes,
            json.dumps(owner_record, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(len(aggregate["incoming"]["docs/owner.md"]), 1)

    def test_aggregate_publisher_rejects_changed_canonical_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            graph = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION).build()
            record = GRAPH_STORE.slice_record(
                graph, summary.relative_to(root).as_posix(), {}
            )
            write(
                summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME,
                json.dumps(record, indent=2) + "\n",
            )
            output = root / GRAPH_STORE.AGGREGATE_DIRECTORY
            args = mock.Mock(project_root=root, output=output, metrics=None)
            CLI.run_index_command(args)
            marker = b'{"concurrent": true}\n'
            original = CLI.build_repository_aggregate
            calls = 0

            def change_after_build(*args: object, **kwargs: object) -> object:
                nonlocal calls
                result = original(*args, **kwargs)
                calls += 1
                if calls == 1:
                    (output / "manifest.json").write_bytes(marker)
                return result

            with mock.patch.object(
                CLI,
                "build_repository_aggregate",
                side_effect=change_after_build,
            ):
                with self.assertRaisesRegex(
                    RECORDS.RecordPublicationError,
                    "canonical validation bundle changed after scan",
                ):
                    CLI.run_index_command(args)

            self.assertEqual((output / "manifest.json").read_bytes(), marker)
