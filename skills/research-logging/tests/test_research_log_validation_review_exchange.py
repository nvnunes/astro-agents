from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import write

EXCHANGE = importlib.import_module("validation.review_exchange")


def review_fixture(root: Path, count: int = 401):
    candidates = [
        {
            "identity": f"docs/mini/entries/e001/data/item-{number:04d}.csv",
            "kind": "artifact",
        }
        for number in range(count)
    ]
    scan = {
        "summary": "docs/mini.md",
        "project_root": root.as_posix(),
        "validation_rules_version": "rules-v1",
        "input_fingerprint": "scan-v1",
        "schema_version": 1,
        "entries": [
            {
                "id": "e001",
                "commands": [],
                "data_index": {},
                "validation_notes": [],
                "orphan_inventory": candidates,
            }
        ],
    }
    adjudication = {
        "schema_version": 1,
        "date": "2026-08-16",
        "review_queue": [
            {
                "entry": "e001",
                "kind": "orphan_candidates",
                "candidates": candidates,
                "subtree_splits": ["docs/mini/entries/e001/data"],
                "validation_notes": [],
            }
        ],
        "entries": [],
    }
    return scan, adjudication


def fill_page(path: Path) -> None:
    template = json.loads(path.read_text(encoding="utf-8"))
    for item in template["items"]:
        item["decision"] = "unresolved"
        item["rationale"] = "No local evidence connection is recorded."
    write(path, json.dumps(template, indent=2) + "\n")


class ReviewSessionTests(unittest.TestCase):
    @staticmethod
    def indexed_item(
        number: int,
        *,
        context: dict[str, object] | None = None,
        locality: str = "shared-locality",
    ) -> dict[str, object]:
        return {
            "template": {
                "id": f"question-{number}",
                "kind": "semantic_fallback",
                "entry": "e001",
                "identity": f"docs/mini/result-{number}.csv",
                "question": "Does the evidence satisfy the contract?",
                "allowed_decisions": ["pass", "fail"],
                "context_level": 0,
                "context_identity": f"context-{number}",
                "decision": None,
                "rationale": None,
            },
            "context": context or {"payload": f"context-{number}"},
            "fingerprint": f"fingerprint-{number}",
            "locality_identity": locality,
        }

    def test_exchange_builds_one_query_index_and_projects_each_context_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root, count=2)
            scan["resolved_paths"] = {}
            scan["mechanical_checks"] = {}
            adjudication["review_queue"][:0] = [
                {
                    "entry": "e001",
                    "kind": "reproduction",
                    "identity": f"docs/mini/result-{index}.csv",
                    "sections": [],
                }
                for index in range(2)
            ]
            original_build = EXCHANGE.ReviewContextIndex.build
            original_context = EXCHANGE._packet_context
            with (
                mock.patch.object(
                    EXCHANGE.ReviewContextIndex,
                    "build",
                    side_effect=original_build,
                ) as build,
                mock.patch.object(
                    EXCHANGE,
                    "_packet_context",
                    wraps=original_context,
                ) as project,
            ):
                first = EXCHANGE.create_exchange(scan, adjudication, {})
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            state = json.loads(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(build.call_count, 1)
            self.assertEqual(project.call_count, state["total_items"])

    def test_durable_judgment_identity_changes_with_dependency_projection(
        self,
    ) -> None:
        row = {
            "id": "template-subject",
            "kind": "upstream_producer",
            "entry": "e001",
            "identity": "docs/mini/result.csv",
            "material": "docs/mini/input.csv",
            "decision": "e001:invocation",
            "rationale": "The recorded invocation produces this material.",
        }
        decisions = {"schema_version": 1, "items": [row]}
        scan = {"entries": []}
        adjudication = {
            "review_queue": [
                {
                    "kind": "upstream_producer",
                    "entry": "e001",
                    "identity": "docs/mini/result.csv",
                }
            ]
        }
        dependency = {
            "kind": "exact-material",
            "semantic_identity": "exact-material:docs/mini/input.csv",
            "projection_version": 1,
            "content_identity": "a" * 64,
            "relationship": "input",
        }

        with mock.patch.object(EXCHANGE, "review_judgment_inputs", return_value=[]):
            legacy = EXCHANGE.durable_review_judgments(
                decisions, "2026-08-16", scan, adjudication
            )[0]
        with mock.patch.object(
            EXCHANGE, "review_judgment_inputs", return_value=[dependency]
        ):
            current = EXCHANGE.durable_review_judgments(
                decisions, "2026-08-16", scan, adjudication
            )[0]

        self.assertEqual(legacy["subject"], current["subject"])
        self.assertEqual(legacy["decision"], current["decision"])
        self.assertNotEqual(legacy["identity"], current["identity"])

    def test_legacy_ordinary_resume_rejects_a_superseded_rules_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, _ = review_fixture(root, count=2)
            identity = "a" * 64
            output_dir = root / "docs" / "mini"
            session = EXCHANGE._session_locator(identity)
            session_dir = EXCHANGE._session_path(output_dir, session)
            session_dir.mkdir(parents=True)
            internal = {
                "schema_version": EXCHANGE.EXCHANGE_SCHEMA_VERSION,
                "continuation": identity,
                "scan": scan,
                "ordinary_session": {
                    "output_dir": output_dir.as_posix(),
                    "session": session,
                },
            }
            write(
                session_dir / EXCHANGE.INTERNAL_FILENAME,
                json.dumps(internal) + "\n",
            )
            write(session_dir / "review-packet.md", "packet\n")
            write(session_dir / "review-decisions.json", "{}\n")
            continuation = {
                "identity": identity,
                "item_count": 2,
                "kind": "ordinary",
            }

            current = EXCHANGE.resume_legacy_ordinary_exchange(
                root / "docs" / "mini",
                "docs/mini.md",
                continuation,
                "rules-v1",
            )
            superseded = EXCHANGE.resume_legacy_ordinary_exchange(
                root / "docs" / "mini",
                "docs/mini.md",
                continuation,
                "rules-v2",
            )

            self.assertEqual(current["status"], "review_required")
            self.assertEqual(superseded, {"status": "superseded_rules"})

    def test_small_review_uses_one_page_session_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root, count=2)

            first = EXCHANGE.create_exchange(
                scan, adjudication, {}, review_diagnostics=True
            )
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            state = json.loads(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            internal = json.loads(
                (
                    Path(first["decision_file"]).parent / EXCHANGE.INTERNAL_FILENAME
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(first["review_kind"], "bounded_review")
            self.assertEqual(first["session_identity"], state["session_identity"])
            self.assertEqual(state["total_items"], 2)
            self.assertEqual(state["current"]["count"], 2)
            self.assertIn("issued_at_epoch_seconds", state["current"])
            self.assertEqual(state["accepted_batches"], [])
            self.assertEqual(first["page_diagnostics"]["item_count"], 2)
            self.assertLessEqual(
                first["page_diagnostics"]["packet_bytes"],
                EXCHANGE.MAX_PACKET_BYTES,
            )
            self.assertIn(
                "orphan_candidate",
                first["page_diagnostics"]["context_bytes_by_projection_family"],
            )
            self.assertIn("review_session", internal)
            self.assertNotIn("ordinary_session", internal)
            self.assertNotIn("deferred_orphan", internal)

    def test_repeated_exact_context_is_rendered_once_and_referenced_per_item(
        self,
    ) -> None:
        context = {"shared": "same projection"}
        indexed = [
            self.indexed_item(number, context=context) for number in range(2)
        ]
        items = [item["template"] for item in indexed]

        packet = EXCHANGE._render_packet_with_contexts(
            "docs/mini.md", items, "continuation", [context, context]
        )

        self.assertEqual(packet.count('"shared": "same projection"'), 1)
        self.assertEqual(packet.count("Shared context: shared context C001"), 2)
        self.assertEqual(packet.count("## Shared Context C001"), 1)

    def test_collection_projection_shares_structure_but_keeps_target_context(
        self,
    ) -> None:
        items = []
        contexts = []
        for number in range(2):
            indexed = self.indexed_item(number)
            indexed["template"]["kind"] = "collection_scope"
            items.append(indexed["template"])
            contexts.append(
                {
                    "identity": f"docs/mini/result-{number}.csv",
                    "reason": "select exact members",
                    "selection_contract": "select one exact scope",
                    "collections": ["output/shared"],
                    "collection_structure": {
                        "output/shared": {"direct_sibling_files": ["one.csv"]}
                    },
                    "recorded_invocations": [{"invocation": "e001:shared"}],
                    "target_dependencies": [
                        {"path": f"docs/mini/result-{number}.csv", "role": "target"}
                    ],
                }
            )

        packet = EXCHANGE._render_packet_with_contexts(
            "docs/mini.md", items, "continuation", contexts
        )

        self.assertEqual(packet.count('"collection_structure"'), 1)
        self.assertEqual(packet.count('"target_dependencies"'), 2)
        self.assertEqual(packet.count("Shared context: shared context C001"), 2)

    def test_collection_locality_orders_equal_shared_projections_together(
        self,
    ) -> None:
        scan = {
            "summary": "docs/mini.md",
            "validation_rules_version": "rules-v1",
            "input_fingerprint": "scan-v1",
        }
        adjudication = {"date": "2026-08-16"}
        items = []
        contexts = []
        for number, invocation in enumerate(["shared", "other", "shared"]):
            indexed = self.indexed_item(number)
            indexed["template"]["kind"] = "collection_scope"
            items.append(indexed["template"])
            contexts.append(
                {
                    "identity": f"docs/mini/result-{number}.csv",
                    "collections": ["output/shared"],
                    "collection_structure": {"output/shared": {}},
                    "recorded_invocations": [{"invocation": invocation}],
                    "target_dependencies": [],
                }
            )

        index = EXCHANGE._bounded_session_index(
            scan, adjudication, items, {}, contexts
        )
        ordered = [item["template"]["identity"] for item in index["items"]]

        shared_positions = [
            position
            for position, identity in enumerate(ordered)
            if identity in {"docs/mini/result-0.csv", "docs/mini/result-2.csv"}
        ]
        self.assertEqual(shared_positions[1] - shared_positions[0], 1)

    def test_page_can_cross_normal_target_to_finish_one_locality_cluster(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)
            indexed = [
                self.indexed_item(
                    number,
                    context={"payload": f"{number}-" + "x" * 180},
                )
                for number in range(3)
            ]
            index = {"summary": "docs/mini.md", "items": indexed}
            state = {
                "session_identity": "session",
                "next_offset": 0,
                "next_page_number": 1,
                "output_dir": directory,
                "session": "work/session",
                "summary_path": "docs/mini.md",
                "summary": "docs/mini.md",
                "review_kind": "bounded_review",
            }

            with (
                mock.patch.object(EXCHANGE, "TARGET_PACKET_BYTES", 1_000),
                mock.patch.object(EXCHANGE, "MAX_PACKET_BYTES", 2_500),
            ):
                page = EXCHANGE._session_page(
                    session_dir, index, state, review_diagnostics=True
                )

            self.assertEqual(page["item_count"], 3)
            self.assertGreater(page["byte_count"], 1_000)
            self.assertLessEqual(page["byte_count"], 2_500)
            self.assertTrue(page["page_diagnostics"]["locality_overflow_used"])
            self.assertFalse(page["page_diagnostics"]["split_locality_cluster"])

    def test_page_splits_cluster_instead_of_crossing_hard_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)
            indexed = [
                self.indexed_item(
                    number,
                    context={"payload": f"{number}-" + "x" * 180},
                )
                for number in range(4)
            ]
            index = {"summary": "docs/mini.md", "items": indexed}
            state = {
                "session_identity": "session",
                "next_offset": 0,
                "next_page_number": 1,
                "output_dir": directory,
                "session": "work/session",
                "summary_path": "docs/mini.md",
                "summary": "docs/mini.md",
                "review_kind": "bounded_review",
            }

            with (
                mock.patch.object(EXCHANGE, "TARGET_PACKET_BYTES", 1_000),
                mock.patch.object(EXCHANGE, "MAX_PACKET_BYTES", 1_200),
            ):
                page = EXCHANGE._session_page(
                    session_dir, index, state, review_diagnostics=True
                )

            self.assertLess(page["item_count"], 4)
            self.assertLessEqual(page["byte_count"], 1_000)
            self.assertFalse(page["page_diagnostics"]["locality_overflow_used"])
            self.assertTrue(page["page_diagnostics"]["split_locality_cluster"])

    def test_indivisible_question_can_cross_normal_target_below_hard_ceiling(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)
            indexed = [
                self.indexed_item(0, context={"payload": "x" * 800})
            ]
            index = {"summary": "docs/mini.md", "items": indexed}
            state = {
                "session_identity": "session",
                "next_offset": 0,
                "next_page_number": 1,
                "output_dir": directory,
                "session": "work/session",
                "summary_path": "docs/mini.md",
                "summary": "docs/mini.md",
                "review_kind": "bounded_review",
            }

            with (
                mock.patch.object(EXCHANGE, "TARGET_PACKET_BYTES", 500),
                mock.patch.object(EXCHANGE, "MAX_PACKET_BYTES", 2_000),
            ):
                page = EXCHANGE._session_page(
                    session_dir, index, state, review_diagnostics=True
                )

            self.assertEqual(page["item_count"], 1)
            self.assertGreater(page["byte_count"], 500)
            self.assertLessEqual(page["byte_count"], 2_000)

    def test_pre_pass_3_paged_packet_remains_acceptable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root, count=2)
            first = EXCHANGE.create_exchange(scan, adjudication, {})
            decision_path = Path(first["decision_file"])
            session_dir = decision_path.parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )

            state = json.loads(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("page_diagnostics", first)
            self.assertNotIn("issued_at_epoch_seconds", state["current"])
            internal_path = decision_path.parent / EXCHANGE.INTERNAL_FILENAME
            internal = json.loads(internal_path.read_text(encoding="utf-8"))
            internal["deferred_orphan"] = internal.pop("review_session")
            write(internal_path, json.dumps(internal) + "\n")
            fill_page(decision_path)

            decisions, loaded = EXCHANGE.load_decisions(decision_path)
            ready = EXCHANGE.accept_review_page(decisions, loaded)

            self.assertEqual(ready["status"], "ready")
            self.assertEqual(len(ready["decisions"]["items"]), 2)

    def test_collection_context_presents_a_compact_directory_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "output" / "collection"
            write(collection / "nested" / "member.pkl", "payload")
            write(collection / "summary.csv", "value\n1\n")
            scan = {
                "project_root": root.as_posix(),
                "entries": [
                    {
                        "id": "e001",
                        "path": "docs/mini/e001.md",
                        "validation_notes": [],
                        "commands": [],
                        "sections": [
                            {
                                "section": "Evidence",
                                "line": 1,
                                "end_line": 2,
                            }
                        ],
                    }
                ],
                "resolved_paths": {
                    "docs/mini/e001.md": (root / "docs/mini/e001.md").as_posix(),
                    "output/collection": collection.as_posix(),
                },
                "mechanical_checks": {},
            }
            write(root / "docs/mini/e001.md", "## Evidence\ncase mapping\n")
            item = {
                "kind": "collection_scope",
                "entry": "e001",
                "identity": "docs/mini/result.csv",
                "collections": ["output/collection"],
                "sections": ["Evidence"],
            }

            adjudication = {
                "entries": [
                    {
                        "id": "e001",
                        "targets": [
                            {
                                "target": item["identity"],
                                "dependencies": [
                                    {"path": "output/collection", "role": "input"}
                                ],
                            }
                        ],
                    }
                ]
            }

            context = EXCHANGE._collection_context(scan, adjudication, item)
            choice = context["collection_structure"]["output/collection"][
                "directory_choices"
            ][0]

            self.assertEqual(
                {
                    key: choice[key]
                    for key in (
                        "relative_directory",
                        "regular_file_descendant_count",
                    )
                },
                {
                    "relative_directory": "nested",
                    "regular_file_descendant_count": 1,
                },
            )
            self.assertEqual(
                context["collection_structure"]["output/collection"][
                    "direct_sibling_files"
                ],
                ["summary.csv"],
            )
            self.assertEqual(len(choice["membership_identity"]), 64)
            self.assertEqual(
                context["authored_entry_passages"],
                {"Evidence": "## Evidence\ncase mapping"},
            )
            self.assertNotIn("nested/member.pkl", json.dumps(context))

    def test_collection_context_size_does_not_scale_with_descendant_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "output" / "collection"
            nested = collection / "retained-run"
            for number in range(1000):
                write(
                    nested / f"member-{number:04d}-{'x' * 48}.pkl",
                    "payload",
                )
            scan = {
                "project_root": root.as_posix(),
                "entries": [
                    {
                        "id": "e001",
                        "path": "docs/mini/e001.md",
                        "validation_notes": [],
                        "sections": [],
                        "commands": [],
                    }
                ],
                "resolved_paths": {
                    "output/collection": collection.as_posix(),
                },
                "mechanical_checks": {},
            }
            item = {
                "kind": "collection_scope",
                "entry": "e001",
                "identity": "docs/mini/result.csv",
                "collections": ["output/collection"],
                "sections": [],
            }

            adjudication = {
                "entries": [
                    {
                        "id": "e001",
                        "targets": [
                            {"target": item["identity"], "dependencies": []}
                        ],
                    }
                ]
            }
            context = EXCHANGE._collection_context(scan, adjudication, item)
            choice = context["collection_structure"]["output/collection"][
                "directory_choices"
            ][0]

            self.assertEqual(choice["regular_file_descendant_count"], 1000)
            self.assertLess(len(json.dumps(context).encode("utf-8")), 4096)
            self.assertNotIn("member-0999", json.dumps(context))

    def test_oversized_collection_context_gets_terminal_bounded_projection(
        self,
    ) -> None:
        item = {
            "kind": "collection_scope",
            "entry": "e001",
            "identity": "docs/mini/result.csv",
            "question": "Which members support this target?",
            "allowed_decisions": ["fail"],
        }
        context = {
            "minimum": {
                "identity": item["identity"],
                "collections": ["output/collection"],
                "reason": "select exact material members",
                "target_dependencies": [
                    {
                        "path": "output/collection",
                        "role": "input",
                        "members": [
                            f"member-{number:04d}.pkl" for number in range(1000)
                        ],
                    }
                ],
                "recorded_invocations": [],
                "collection_structure": {
                    "output/collection": {
                        "directory_choices": [
                            {
                                "relative_directory": f"run-{number:04d}",
                                "regular_file_descendant_count": 1,
                                "membership_identity": f"{number:064x}",
                                "selector": {
                                    "directory": f"run-{number:04d}",
                                    "membership_identity": f"{number:064x}",
                                },
                            }
                            for number in range(1000)
                        ]
                    }
                },
            },
            "focused_expansion": {},
        }

        packet, projected = EXCHANGE._render_contexts(
            "docs/mini.md", [item], "continuation", [context]
        )

        self.assertLessEqual(len(packet.encode("utf-8")), EXCHANGE.MAX_PACKET_BYTES)
        self.assertEqual(
            projected[0]["context_projection"],
            "bounded-terminal-collection",
        )
        self.assertTrue(
            projected[0]["collection_structure"]["output/collection"][
                "choices_truncated"
            ]
        )

    def test_paged_collection_scope_uses_cli_owned_collection_set(self) -> None:
        row = {
            "kind": "collection_scope",
            "entry": "e001",
            "identity": "docs/mini/result.csv",
            "allowed_decisions": [
                {
                    "members": {
                        "output/one": ["<relative/member>"],
                        "output/two": ["<relative/member>"],
                    }
                },
                "fail",
                "needs_context",
            ],
        }
        decision = {
            "members": {
                "output/one": ["one.pkl"],
                "output/two": ["two.pkl"],
            }
        }

        self.assertTrue(EXCHANGE._valid_collection_scope(row, decision, {}))

    def test_mixed_bounded_session_indexes_every_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root, count=250)
            adjudication["review_queue"].insert(
                0,
                {
                    "entry": "e001",
                    "kind": "semantic_fallback",
                    "identity": "docs/mini/result.csv",
                    "workflow": {"status": "pass"},
                    "evidence": [],
                    "sections": [],
                },
            )

            first = EXCHANGE.create_exchange(scan, adjudication, {})
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            state = json.loads(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(first["review_kind"], "bounded_review")
            self.assertEqual(state["total_items"], 251)

    def test_upstream_questions_grow_by_material_not_cartesian_product(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = [
                {
                    "material": f"docs/mini/data/material-{material}.csv",
                    "invocation": f"invocation-{candidate}",
                    "entry": "e001",
                    "line": candidate + 1,
                    "command": f"python produce.py --case {candidate}",
                }
                for material in range(10)
                for candidate in range(10)
            ]
            queue_item = {
                "entry": "e001",
                "kind": "upstream_producer",
                "identity": "docs/mini/data/result.csv",
                "producer_candidates": candidates,
                "workflow": {"status": "unresolved"},
                "evidence": [],
            }
            scan = {
                "summary": "docs/mini.md",
                "project_root": root.as_posix(),
                "validation_rules_version": "rules-v1",
                "input_fingerprint": "scan-v1",
                "entries": [{"id": "e001", "commands": []}],
            }
            adjudication = {"review_queue": [queue_item], "summary": []}

            items, _ = EXCHANGE._template_items(scan, adjudication)

            self.assertEqual(len(items), 10)
            self.assertEqual(
                sum(len(item["allowed_decisions"]) for item in items),
                120,
            )
            self.assertTrue(all(item["material"].endswith(".csv") for item in items))

    def test_pages_append_fragments_and_merge_once_at_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root)
            first = EXCHANGE.create_exchange(
                scan,
                adjudication,
                {"record": {}, "cache": {}},
                review_diagnostics=True,
            )
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            base_path = session_dir / EXCHANGE.SESSION_BASE_FILENAME
            base_before = base_path.read_bytes()
            index = json.loads(
                (session_dir / EXCHANGE.SESSION_INDEX_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("items", index)
            self.assertEqual(index["item_count"], 401)
            self.assertEqual(len(index["item_shards"]), 3)
            first_count = first["item_count"]
            self.assertLessEqual(first_count, 200)
            self.assertLessEqual(first["byte_count"], EXCHANGE.MAX_PACKET_BYTES)

            fill_page(Path(first["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(Path(first["decision_file"]))
            second = EXCHANGE.accept_review_page(
                decisions,
                internal,
                lambda *_: ["a" * 64],
                review_diagnostics=True,
            )
            self.assertEqual(second["status"], "review_required")
            self.assertGreaterEqual(
                second["accepted_page_diagnostics"]["review_wait_seconds"],
                0,
            )
            self.assertEqual(
                second["accepted_page_diagnostics"]["items_by_kind"],
                {"orphan_candidate": first_count},
            )
            second_count = second["item_count"]
            self.assertLessEqual(second_count, 200)
            self.assertEqual(base_path.read_bytes(), base_before)

            fill_page(Path(second["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(Path(second["decision_file"]))
            third = EXCHANGE.accept_review_page(
                decisions,
                internal,
                lambda *_: ["b" * 64],
                review_diagnostics=True,
            )
            self.assertEqual(third["status"], "review_required")
            self.assertEqual(third["item_count"], 401 - first_count - second_count)

            fill_page(Path(third["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(Path(third["decision_file"]))
            ready = EXCHANGE.accept_review_page(
                decisions,
                internal,
                lambda *_: ["c" * 64],
                review_diagnostics=True,
            )

            self.assertEqual(ready["status"], "ready")
            self.assertEqual(len(ready["decisions"]["items"]), 401)
            self.assertEqual(
                ready["judgment_identities"],
                ["a" * 64, "b" * 64, "c" * 64],
            )
            self.assertEqual(base_path.read_bytes(), base_before)
            self.assertEqual(len(list(session_dir.glob("accepted-*.json"))), 3)
            self.assertLess(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).stat().st_size,
                4096,
            )
            EXCHANGE.finish_review_session(session_dir)
            self.assertFalse(session_dir.exists())

    def test_accepted_page_cannot_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root)
            first = EXCHANGE.create_exchange(scan, adjudication, {})
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            decision_path = Path(first["decision_file"])
            fill_page(decision_path)
            decisions, internal = EXCHANGE.load_decisions(decision_path)
            EXCHANGE.accept_review_page(decisions, internal)

            with self.assertRaisesRegex(Exception, "stale"):
                EXCHANGE.accept_review_page(decisions, internal)


if __name__ == "__main__":
    unittest.main()
