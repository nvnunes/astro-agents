from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

ADJUDICATION = importlib.import_module("validation.adjudication")
CONTRACTS = importlib.import_module("validation.contracts")
DECISIONS = importlib.import_module("validation.decisions")
EVIDENCE = importlib.import_module("validation.evidence")
PRODUCER_BINDINGS = importlib.import_module("validation.producer_bindings")


class AdjudicationAssemblyTests(unittest.TestCase):
    def test_typed_assembly_serializes_the_exact_adjudication_contract(self) -> None:
        record = ADJUDICATION.AdjudicationAssembly(
            schema_version=5,
            rules_version="test-rules",
            log="docs/example.md",
            date="2026-08-11",
            mode="standard",
            entry_order=[],
            summary_rows=[],
            entry_rows=[],
            review_queue=[],
        ).record()

        decoded = CONTRACTS.decode_adjudication_record(record, schema_version=5)

        self.assertEqual(decoded["requested_scope"], "complete standard scope")
        self.assertEqual(decoded["scope"], {"summary": True, "entries": []})
        self.assertEqual(decoded["review_queue"], [])


class CandidateCommandTests(unittest.TestCase):
    def test_exact_consumer_does_not_hide_section_local_producer(self) -> None:
        consumer = {
            "command": "python plot.py --input data/result.csv",
            "section": "Results",
            "path_arguments": [
                {
                    "path": "/project/data/result.csv",
                    "role_hint": "input",
                }
            ],
        }
        producer = {
            "command": "python run.py --fixed-output",
            "section": "Results",
            "path_arguments": [],
        }
        scan = {
            "entries": [{"id": "e001", "commands": [producer, consumer]}],
            "resolved_paths": {
                "data/result.csv": "/project/data/result.csv"
            },
            "mechanical_checks": {},
        }

        candidates = ADJUDICATION.candidate_commands(
            scan, "e001", "data/result.csv", ["Results"]
        )

        self.assertEqual(candidates, [producer, consumer])

    def test_reviewed_output_container_ranks_a_producer_before_a_consumer(
        self,
    ) -> None:
        producer = {
            "command": "python run.py --output-dir data/run",
            "section": "Results",
            "path_arguments": [
                {"path": "/project/data/run", "role_hint": "output"}
            ],
        }
        consumer = {
            "command": "python summarize.py --input data/run/summary.csv",
            "section": "Results",
            "path_arguments": [
                {
                    "path": "/project/data/run/summary.csv",
                    "role_hint": "input",
                }
            ],
        }
        scan = {
            "entries": [{"id": "e001", "commands": [consumer, producer]}],
            "resolved_paths": {
                "data/run": "/project/data/run",
                "data/run/summary.csv": "/project/data/run/summary.csv",
            },
            "mechanical_checks": {"data/run": {"type": "directory"}},
        }

        candidates = ADJUDICATION.candidate_commands(
            scan, "e001", "data/run/summary.csv", ["Results"]
        )

        self.assertEqual(candidates, [producer, consumer])

    def test_section_local_output_container_precedes_unrelated_containers(
        self,
    ) -> None:
        unrelated = [
            {
                "command": f"python unrelated_{index}.py --output-dir images",
                "section": "Other Results",
                "path_arguments": [
                    {"path": "/project/images", "role_hint": "output"}
                ],
            }
            for index in range(5)
        ]
        producer = {
            "command": "python compare.py --output-dir images",
            "section": "Tensor Results",
            "path_arguments": [
                {"path": "/project/images", "role_hint": "output"}
            ],
        }
        scan = {
            "entries": [
                {"id": "e001", "commands": [*unrelated, producer]}
            ],
            "resolved_paths": {
                "images": "/project/images",
                "images/tensor.png": "/project/images/tensor.png",
            },
            "mechanical_checks": {"images": {"type": "directory"}},
        }

        candidates = ADJUDICATION.candidate_commands(
            scan, "e001", "images/tensor.png", ["Tensor Results"]
        )

        self.assertEqual(candidates, unrelated + [producer])

    def test_target_owner_entry_contributes_producer_candidates(self) -> None:
        consumer = {
            "command": "python plot.py --input entries/run/data/result.npz",
            "section": "Comparison",
            "path_arguments": [
                {
                    "path": "/project/entries/run/data/result.npz",
                    "role_hint": "input",
                }
            ],
        }
        producer = {
            "command": "python run.py --output-dir data",
            "section": "Simulation",
            "path_arguments": [
                {"path": "/project/entries/run/data", "role_hint": "output"}
            ],
        }
        scan = {
            "entries": [
                {
                    "id": "consumer",
                    "path": "entries/report/report.md",
                    "commands": [consumer],
                },
                {
                    "id": "producer",
                    "path": "entries/run/run.md",
                    "commands": [producer],
                },
            ],
            "resolved_paths": {
                "entries/run/data": "/project/entries/run/data",
                "entries/run/data/result.npz": (
                    "/project/entries/run/data/result.npz"
                ),
            },
            "mechanical_checks": {
                "entries/run/data": {"type": "directory"}
            },
        }

        candidates = ADJUDICATION.candidate_commands(
            scan,
            "consumer",
            "entries/run/data/result.npz",
            ["Comparison"],
        )

        self.assertEqual(candidates, [producer, consumer])

    def test_unknown_container_with_source_named_output_is_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "plot.py"
            script.write_text(
                'save_figure(image_dir, "normalization-sr.png")\n',
                encoding="utf-8",
            )
            producer = {
                "command": "python plot.py --image-dir images",
                "section": "Other",
                "script": str(script),
                "path_arguments": [
                    {"path": str(root / "images"), "role_hint": "unknown"}
                ],
            }
            scan = {
                "entries": [{"id": "e001", "commands": [producer]}],
                "resolved_paths": {
                    "images": str(root / "images"),
                    "images/normalization-sr.png": str(
                        root / "images" / "normalization-sr.png"
                    ),
                },
                "mechanical_checks": {"images": {"type": "directory"}},
            }

            candidates = ADJUDICATION.candidate_commands(
                scan,
                "e001",
                "images/normalization-sr.png",
                ["Normalization"],
            )

            self.assertEqual(candidates, [producer])


class DecisionApplicationTests(unittest.TestCase):
    def test_decision_owner_applies_and_reconciles_an_empty_queue(self) -> None:
        decided, counts = DECISIONS.apply_review_decisions(
            {
                "summary": "docs/example.md",
                "project_root": "/tmp/project",
                "validation_rules_version": "test-rules",
                "resolved_paths": {},
                "script_inventory": [],
                "entries": [],
            },
            {
                "date": "2026-08-11",
                "entries": [],
                "summary": [],
                "review_queue": [],
            },
            {"schema_version": DECISIONS.DECISION_SCHEMA_VERSION, "actions": []},
        )

        self.assertEqual(decided["review_queue"], [])
        self.assertEqual(counts, {"remaining": 0})

    def test_failure_basis_is_owned_with_decision_semantics(self) -> None:
        self.assertEqual(
            DECISIONS.semantic_failure_bases(
                {
                    "workflow": {"status": "unresolved"},
                    "evidence": [{"result": {"status": "pass"}}],
                }
            ),
            {"workflow"},
        )

    def test_collection_scope_collapses_duplicate_command_dependencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            (run / "input.csv").write_text("value\n1\n")
            dependencies = [
                {"path": "data/run", "role": "input"},
                {"path": "data/run", "role": "input"},
            ]
            context = DECISIONS._ReviewDecisionContext(
                {
                    "resolved_paths": {"data/run": str(run)},
                    "mechanical_checks": {
                        "data/run": {"type": "directory"},
                    },
                },
                {},
                {},
                {
                    "members": {"data/run": ["input.csv"]},
                },
                "scope",
                "2026-08-12",
                "e001",
                "collection_scope",
                {"dependencies": dependencies},
                {},
            )

            DECISIONS._apply_decision_dependencies(context)

            self.assertEqual(
                context.row["dependencies"],
                [
                    {
                        "path": "data/run",
                        "role": "input",
                        "members": ["input.csv"],
                    }
                ],
            )

    def test_dependency_replacement_removes_before_adding_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection = Path(directory) / "run"
            collection.mkdir()
            (collection / "result.csv").write_text("value\n1\n", encoding="utf-8")
            context = DECISIONS._ReviewDecisionContext(
                {
                    "resolved_paths": {"data/run": str(collection)},
                    "mechanical_checks": {"data/run": {"type": "directory"}},
                },
                {},
                {},
                {
                    "remove_dependencies": ["data/run"],
                    "add_dependencies": [
                        {
                            "path": "data/run",
                            "role": "producer",
                            "members": ["result.csv"],
                        }
                    ],
                },
                "scope",
                "2026-08-15",
                "e001",
                "collection_scope",
                {
                    "dependencies": [
                        {
                            "path": "data/run",
                            "role": "input",
                            "members": ["result.csv"],
                        }
                    ]
                },
                {},
            )

            DECISIONS._apply_decision_dependencies(context)

            self.assertEqual(
                context.row["dependencies"],
                [
                    {
                        "path": "data/run",
                        "role": "producer",
                        "members": ["result.csv"],
                    }
                ],
            )

    def test_collection_binding_requires_producer_role_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "data" / "run"
            collection.mkdir(parents=True)
            target = collection / "result.csv"
            target.write_text("value\n1\n", encoding="utf-8")
            command = {
                "command": "python produce.py --output-dir data/run",
                "section": "Results",
                "path_arguments": [
                    {"path": str(collection), "role_hint": "output"}
                ],
            }
            invocation = PRODUCER_BINDINGS.invocation_identities(
                "e001", [command]
            )[0]
            scan = {
                "project_root": str(root),
                "entries": [{"id": "e001", "commands": [command]}],
                "resolved_paths": {
                    "data/run": str(collection),
                    "data/run/result.csv": str(target),
                },
                "mechanical_checks": {"data/run": {"type": "directory"}},
            }
            input_scope = {
                "path": "data/run",
                "role": "input",
                "members": ["result.csv"],
            }

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "omits the target",
            ):
                PRODUCER_BINDINGS.verify_producer_binding(
                    scan,
                    "data/run/result.csv",
                    invocation,
                    [input_scope],
                )

            binding = PRODUCER_BINDINGS.verify_producer_binding(
                scan,
                "data/run/result.csv",
                invocation,
                [
                    input_scope,
                    {
                        "path": "data/run",
                        "role": "producer",
                        "members": ["result.csv"],
                    },
                ],
            )

            self.assertEqual(binding["kind"], "scoped-collection")
            self.assertEqual(binding["target_member"], "result.csv")

    def test_copied_sibling_dependencies_recheck_destination_coverage(self) -> None:
        source = {
            "target": "summary.csv",
            "producer_invocation": "e001:L12:1:producer",
            "dependencies": [
                {"path": "summary.csv", "role": "target"},
                {"path": "produce.py", "role": "producer"},
                {"path": "input.csv", "role": "input"},
            ],
        }
        sibling = {
            "target": "figure.png",
            "dependencies": [{"path": "figure.png", "role": "target"}],
            "integrity": None,
            "provenance": None,
            "findings": [],
        }
        adjudication = {
            "entries": [{"id": "e001", "targets": [source, sibling]}]
        }
        context = DECISIONS._ReviewDecisionContext(
            {},
            adjudication,
            {"workflow": {"status": "unresolved"}},
            {
                "decision": "pass",
                "copy_dependencies_from": "summary.csv",
            },
            "pass",
            "2026-08-12",
            "e001",
            "entry",
            sibling,
            {},
        )

        with self.assertRaisesRegex(
            CONTRACTS.ValidationToolError,
            "missing recorded invocation",
        ):
            DECISIONS._apply_decision_dependencies(context)


class PreparationTests(unittest.TestCase):
    def test_orphan_preparation_preserves_complete_item_dispositions(self) -> None:
        prepared = ADJUDICATION.prepare_orphan_items(
            {
                "id": "e001",
                "path": "docs/example/e001.md",
                "orphan_inventory": [
                    {"identity": "unused.py", "kind": "script"},
                    {"identity": "used.csv", "kind": "artifact"},
                ],
                "orphan_candidates": [
                    {"identity": "unused.py", "kind": "script"},
                ],
                "validation_notes": [],
            },
            {},
        )

        self.assertEqual(
            prepared.orphan_items,
            [
                {"identity": "unused.py", "decision": "pending", "basis": "-"},
                {"identity": "used.csv", "decision": "accepted", "basis": "graph"},
            ],
        )
        self.assertEqual(prepared.targets[0]["target"], ADJUDICATION.ORPHAN_TARGET)
        self.assertEqual(prepared.review_items[0]["kind"], "orphan_candidates")

    def test_reusable_result_restores_reviewed_dependency_scope(self) -> None:
        targets = [
            {
                "target": "result.csv",
                "integrity": None,
                "provenance": None,
                "reproducibility": "-",
                "dependencies": [{"path": "result.csv", "role": "target"}],
                "findings": [{"check": "Provenance", "finding": "requires review"}],
            }
        ]
        review = [
            {
                "entry": "e001",
                "identity": "result.csv",
                "kind": "mechanical_failure",
            }
        ]
        retained = ADJUDICATION.apply_reusable_target_results(
            "e001",
            targets,
            review,
            {
                ("e001", "result.csv", "Integrity"): {
                    "result": "2026-08-11",
                    "findings": [],
                    "dependencies": [{"path": "result.csv", "role": "target"}],
                },
                ("e001", "result.csv", "Provenance"): {
                    "result": "2026-08-11",
                    "resolution": {"producer_invocation": "invocation-1"},
                    "findings": [],
                    "dependencies": [{"path": "producer.py", "role": "producer"}],
                },
            },
            "standard",
        )

        self.assertEqual(retained, [])
        self.assertEqual(targets[0]["provenance"], "2026-08-11")
        self.assertEqual(targets[0]["producer_invocation"], "invocation-1")
        self.assertIn(
            {"path": "producer.py", "role": "producer"},
            targets[0]["dependencies"],
        )

    def test_collection_scope_is_attached_to_an_existing_review_item(self) -> None:
        review = [
            {
                "entry": "e001",
                "identity": "result.csv",
                "kind": "semantic_fallback",
            }
        ]
        ADJUDICATION.queue_collection_scopes(
            {
                "mechanical_checks": {
                    "inputs": {"type": "directory"},
                }
            },
            [
                {
                    "id": "e001",
                    "targets": [
                        {
                            "target": "result.csv",
                            "integrity": "2026-08-11",
                            "provenance": None,
                            "dependencies": [{"path": "inputs", "role": "input"}],
                        }
                    ],
                }
            ],
            review,
        )

        self.assertEqual(review[0]["collections"], ["inputs"])

    def test_unprovenanced_item_is_a_deterministic_dual_failure(self) -> None:
        prepared = ADJUDICATION.unprovenanced_items(
            {
                "id": "e001",
                "path": "docs/example/e001.md",
                "evidence_record": {"rows": []},
                "presented_items": [
                    {
                        "section": "Results",
                        "kind": "statistic",
                        "selector": "`1.3`",
                    }
                ],
            }
        )

        self.assertEqual(prepared.targets[0]["integrity"], "FAIL")
        self.assertEqual(prepared.targets[0]["provenance"], "FAIL")
        self.assertEqual(
            prepared.review_items[0]["hard_failures"],
            ["Integrity", "Provenance"],
        )

    def test_target_preparation_combines_integrity_workflow_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "produce.py"
            target = root / "result.csv"
            script.write_text("print('ok')\n", encoding="utf-8")
            target.write_text("value\n1.3\n", encoding="utf-8")
            scan = {
                "project_root": str(root),
                "resolved_paths": {
                    "produce.py": str(script),
                    "result.csv": str(target),
                },
                "mechanical_checks": {
                    "produce.py": {"status": "ok", "type": "python"},
                    "result.csv": {"status": "ok", "type": "table"},
                },
            }
            command = {
                "line": 12,
                "command": "python produce.py --output result.csv",
                "script": str(script),
                "unknown_options": [],
                "data_tokens": [],
                "path_arguments": [
                    {"path": str(target), "role_hint": "output"},
                ],
            }
            entry = {
                "id": "e001",
                "path": "docs/example/e001.md",
                "commands": [command],
                "evidence_record": {"identity": "", "rows": []},
            }
            source = {
                "identity": "result.csv",
                "path": str(target),
                "status": "resolved",
                "source": "result.csv",
                "locator": "field=value",
            }
            grouped = {
                "source": source,
                "associations": [
                    {
                        "row": {
                            "kind": "statistic",
                            "evidence": "`1.3`",
                            "transformation": "",
                            "presented_item": {"context": "1.3"},
                        },
                        "source": source,
                    }
                ],
                "sections": ["Results"],
            }
            context = ADJUDICATION.TargetPreparationContext(
                scan,
                {},
                "2026-08-11",
                "standard",
                lambda _row, _source: {
                    "status": "pass",
                    "detail": "matched",
                },
            )

            prepared = ADJUDICATION.prepare_evidence_target(
                entry,
                "result.csv",
                grouped,
                context,
            )

            self.assertEqual(prepared.review_items, [])
            self.assertEqual(prepared.targets[0]["integrity"], "2026-08-11")
            self.assertEqual(prepared.targets[0]["provenance"], "2026-08-11")
            self.assertEqual(prepared.targets[0]["reproducibility"], "-")

    def test_numeric_candidate_cannot_date_provenance_without_semantic_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "produce.py"
            target = root / "result.csv"
            script.write_text("print('ok')\n", encoding="utf-8")
            target.write_text("pressure_kg\n1\n", encoding="utf-8")
            scan = {
                "project_root": str(root),
                "resolved_paths": {
                    "produce.py": str(script),
                    "result.csv": str(target),
                },
                "mechanical_checks": {
                    "produce.py": {"status": "ok", "type": "python"},
                    "result.csv": {"status": "ok", "type": "table"},
                },
            }
            entry = {
                "id": "e001",
                "path": "docs/example/e001.md",
                "commands": [
                    {
                        "line": 12,
                        "command": "python produce.py --output result.csv",
                        "script": str(script),
                        "unknown_options": [],
                        "data_tokens": [],
                        "path_arguments": [
                            {"path": str(target), "role_hint": "output"}
                        ],
                    }
                ],
                "evidence_record": {"identity": "", "rows": []},
            }
            source = {
                "identity": "result.csv",
                "path": str(target),
                "status": "resolved",
                "source": "result.csv",
                "locator": "field=pressure_kg",
            }
            row = {
                "kind": "statistic",
                "evidence": "`1 m`",
                "transformation": "",
                "presented_item": {"context": "Distance is 1 m."},
            }
            grouped = {
                "source": source,
                "associations": [{"row": row, "source": source}],
                "sections": ["Results"],
            }
            context = ADJUDICATION.TargetPreparationContext(
                scan,
                {},
                "2026-08-12",
                "standard",
                lambda selected_row, selected_source: (
                    EVIDENCE.mechanical_evidence_support(
                        selected_row,
                        selected_source,
                        lambda path: {
                            "status": "ok",
                            "type": path.suffix.lstrip("."),
                        },
                    )
                ),
            )

            prepared = ADJUDICATION.prepare_evidence_target(
                entry, "result.csv", grouped, context
            )

            self.assertEqual(prepared.targets[0]["integrity"], "2026-08-12")
            self.assertIsNone(prepared.targets[0]["provenance"])
            self.assertEqual(len(prepared.review_items), 1)
            self.assertEqual(prepared.review_items[0]["kind"], "semantic_fallback")


class ReviewPacketTests(unittest.TestCase):
    def test_owner_filters_and_renders_review_items(self) -> None:
        scan = {
            "summary": "docs/example.md",
            "project_root": "/tmp/project",
            "resolved_paths": {},
            "entries": [],
        }
        adjudication = {
            "summary": [],
            "review_queue": [
                {
                    "entry": "e001",
                    "identity": "result.csv",
                    "kind": "semantic_fallback",
                    "reason": "Confirm logical equivalence.",
                    "sections": [],
                    "evidence": [],
                },
                {
                    "entry": "e002",
                    "identity": "other.csv",
                    "kind": "semantic_fallback",
                    "sections": [],
                    "evidence": [],
                },
            ],
        }

        packet, counts = ADJUDICATION.make_review_packet(
            scan,
            adjudication,
            ADJUDICATION.ReviewPacketRequest(entry="e001"),
        )

        self.assertEqual(counts, {"semantic_fallback": 1})
        self.assertIn("Q001 — e001: result.csv", packet)
        self.assertIn("Confirm logical equivalence.", packet)
        self.assertNotIn("other.csv", packet)

    def test_collection_packet_uses_bounded_shallow_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "collection"
            nested = root / "nested"
            deep = nested / "deep"
            excluded = deep / "excluded"
            excluded.mkdir(parents=True)
            (root / "root.csv").write_text("root\n", encoding="utf-8")
            (nested / "nested.csv").write_text("nested\n", encoding="utf-8")
            (deep / "deep.csv").write_text("deep\n", encoding="utf-8")
            (excluded / "excluded.csv").write_text("excluded\n", encoding="utf-8")
            scan = {
                "resolved_paths": {"<collection>": str(root)},
            }

            lines = ADJUDICATION.collection_packet_lines(scan, "<collection>")
            packet = "\n".join(lines)

            self.assertIn("root.csv", packet)
            self.assertIn("nested/nested.csv", packet)
            self.assertIn("nested/deep/deep.csv", packet)
            self.assertNotIn("excluded.csv", packet)


class WorkflowCheckTests(unittest.TestCase):
    def test_explicit_output_command_produces_a_stable_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "produce.py"
            target = root / "result.csv"
            script.write_text("print('ok')\n", encoding="utf-8")
            target.write_text("value\n1\n", encoding="utf-8")
            scan = {
                "project_root": str(root),
                "resolved_paths": {
                    "produce.py": str(script),
                    "result.csv": str(target),
                },
                "mechanical_checks": {
                    "produce.py": {"status": "ok"},
                },
            }
            command = {
                "line": 12,
                "command": "python produce.py --output result.csv",
                "script": str(script),
                "unknown_options": [],
                "data_tokens": [],
                "path_arguments": [
                    {
                        "path": str(target),
                        "role_hint": "output",
                    }
                ],
            }
            entry = {"id": "e001", "commands": [command]}

            result, dependencies = ADJUDICATION.workflow_check(
                entry,
                "result.csv",
                scan,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["matched_commands"], 1)
            self.assertIn("producer_invocation", result)
            self.assertEqual(
                dependencies,
                [{"path": "produce.py", "role": "producer"}],
            )

    def test_unknown_path_direction_requires_semantic_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "produce.py"
            target = root / "result.csv"
            script.write_text("print('ok')\n", encoding="utf-8")
            target.write_text("value\n1\n", encoding="utf-8")
            scan = {
                "project_root": str(root),
                "resolved_paths": {
                    "produce.py": str(script),
                    "result.csv": str(target),
                },
                "mechanical_checks": {
                    "produce.py": {"status": "ok"},
                },
            }
            entry = {
                "id": "e001",
                "commands": [
                    {
                        "line": 12,
                        "command": "python produce.py result.csv",
                        "script": str(script),
                        "unknown_options": [],
                        "data_tokens": [],
                        "path_arguments": [
                            {
                                "path": str(target),
                                "role_hint": "unknown",
                            }
                        ],
                    }
                ],
            }

            result, _dependencies = ADJUDICATION.workflow_check(
                entry,
                "result.csv",
                scan,
            )

            self.assertEqual(result["status"], "unresolved")
            self.assertIn("semantic producer confirmation", result["detail"])

    def test_external_recorded_input_is_a_terminal_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            script = project / "consume.py"
            target = root / "retained" / "summary.csv"
            target.parent.mkdir()
            script.write_text("print('ok')\n", encoding="utf-8")
            target.write_text("value\n1\n", encoding="utf-8")
            scan = {
                "project_root": str(project),
                "resolved_paths": {
                    "consume.py": str(script),
                    str(target): str(target),
                },
                "mechanical_checks": {
                    "consume.py": {"status": "ok"},
                },
            }
            entry = {
                "id": "e001",
                "commands": [
                    {
                        "line": 12,
                        "command": f"python consume.py --input {target}",
                        "script": str(script),
                        "unknown_options": [],
                        "data_tokens": [],
                        "path_arguments": [
                            {
                                "path": str(target),
                                "role_hint": "input",
                            }
                        ],
                    }
                ],
            }

            result, dependencies = ADJUDICATION.workflow_check(
                entry,
                str(target),
                scan,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["matched_commands"], 1)
            self.assertNotIn("producer_invocation", result)
            self.assertEqual(dependencies, [])

    def test_retained_research_entry_is_a_terminal_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_entry = root / "docs/log/entries/source/e002.md"
            source_entry.parent.mkdir(parents=True)
            source_entry.write_text("# Source\n", encoding="utf-8")
            scan = {
                "project_root": str(root),
                "resolved_paths": {
                    "docs/log/entries/source/e002.md": str(source_entry)
                },
                "mechanical_checks": {},
                "entries": [
                    {
                        "id": "e002",
                        "path": "docs/log/entries/source/e002.md",
                        "commands": [],
                    }
                ],
            }
            entry = {"id": "e003", "commands": []}

            result, dependencies = ADJUDICATION.workflow_check(
                entry,
                "docs/log/entries/source/e002.md",
                scan,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["matched_commands"], 0)
            self.assertNotIn("producer_invocation", result)
            self.assertEqual(dependencies, [])


if __name__ == "__main__":
    unittest.main()
