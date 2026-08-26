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
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
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

            first = EXCHANGE.create_exchange(scan, adjudication, {})
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
            )
            state = json.loads(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            internal = json.loads(
                (
                    Path(first["decision_file"]).parent
                    / EXCHANGE.INTERNAL_FILENAME
                ).read_text(encoding="utf-8")
            )

            self.assertEqual(first["review_kind"], "bounded_review")
            self.assertEqual(first["session_identity"], state["session_identity"])
            self.assertEqual(state["total_items"], 2)
            self.assertEqual(state["current"]["count"], 2)
            self.assertEqual(state["accepted_batches"], [])
            self.assertIn("review_session", internal)
            self.assertNotIn("ordinary_session", internal)
            self.assertNotIn("deferred_orphan", internal)

    def test_pre_pass_3_paged_packet_remains_acceptable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root, count=2)
            first = EXCHANGE.create_exchange(scan, adjudication, {})
            decision_path = Path(first["decision_file"])
            session_dir = decision_path.parent.parent
            self.addCleanup(
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
            )
            internal_path = decision_path.parent / EXCHANGE.INTERNAL_FILENAME
            internal = json.loads(internal_path.read_text(encoding="utf-8"))
            internal["deferred_orphan"] = internal.pop("review_session")
            write(internal_path, json.dumps(internal) + "\n")
            fill_page(decision_path)

            decisions, loaded = EXCHANGE.load_decisions(decision_path)
            ready = EXCHANGE.accept_review_page(decisions, loaded)

            self.assertEqual(ready["status"], "ready")
            self.assertEqual(len(ready["decisions"]["items"]), 2)

    def test_collection_context_expansion_lists_nested_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "output" / "collection"
            write(collection / "nested" / "member.pkl", "payload")
            scan = {
                "project_root": root.as_posix(),
                "entries": [
                    {
                        "id": "e001",
                        "path": "docs/mini/e001.md",
                        "validation_notes": [],
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
            }
            write(root / "docs/mini/e001.md", "## Evidence\ncase mapping\n")
            item = {
                "kind": "collection_scope",
                "entry": "e001",
                "identity": "docs/mini/result.csv",
                "collections": ["output/collection"],
                "sections": ["Evidence"],
            }

            expanded = EXCHANGE._expanded_context(scan, item, {})

            self.assertEqual(
                expanded["focused_expansion"]["recursive_member_inventory"],
                {"output/collection": ["nested/member.pkl"]},
            )
            self.assertEqual(
                expanded["focused_expansion"]["entry_section_passages"],
                {"Evidence": "## Evidence\ncase mapping"},
            )

    def test_collection_context_expansion_truncates_to_its_byte_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "output" / "collection"
            for number in range(1000):
                write(
                    collection / f"member-{number:04d}-{'x' * 48}.pkl",
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
                    }
                ],
                "resolved_paths": {
                    "output/collection": collection.as_posix(),
                },
            }
            item = {
                "kind": "collection_scope",
                "entry": "e001",
                "identity": "docs/mini/result.csv",
                "collections": ["output/collection"],
                "sections": [],
            }

            expanded = EXCHANGE._expanded_context(scan, item, {})
            focused = expanded["focused_expansion"]

            self.assertEqual(
                focused["truncated_recursive_inventories"],
                ["output/collection"],
            )
            self.assertLess(
                len(focused["recursive_member_inventory"]["output/collection"]),
                1000,
            )
            self.assertLessEqual(
                len(json.dumps(expanded).encode("utf-8")),
                EXCHANGE.MAX_EXPANDED_CONTEXT_BYTES,
            )

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
                            f"member-{number:04d}.pkl"
                            for number in range(1000)
                        ],
                    }
                ],
                "recorded_invocations": [],
            },
            "focused_expansion": {
                "recursive_member_inventory": {
                    "output/collection": [
                        f"member-{number:04d}-{'x' * 48}.pkl"
                        for number in range(1000)
                    ]
                },
                "truncated_recursive_inventories": ["output/collection"],
            },
        }

        packet, projected = EXCHANGE._render_contexts(
            "docs/mini.md", [item], "continuation", [context]
        )

        self.assertLessEqual(
            len(packet.encode("utf-8")), EXCHANGE.MAX_PACKET_BYTES
        )
        self.assertEqual(
            projected[0]["context_projection"],
            "bounded-terminal-collection",
        )
        self.assertTrue(
            projected[0]["member_inventory"]["output/collection"]["truncated"]
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
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
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
                sum(
                    len(item["allowed_decisions"])
                    for item in items
                ),
                120,
            )
            self.assertTrue(
                all(item["material"].endswith(".csv") for item in items)
            )

    def test_pages_append_fragments_and_merge_once_at_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = review_fixture(root)
            first = EXCHANGE.create_exchange(
                scan, adjudication, {"record": {}, "cache": {}}
            )
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
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
            decisions, internal = EXCHANGE.load_decisions(
                Path(first["decision_file"])
            )
            second = EXCHANGE.accept_review_page(
                decisions, internal, lambda *_: ["a" * 64]
            )
            self.assertEqual(second["status"], "review_required")
            second_count = second["item_count"]
            self.assertLessEqual(second_count, 200)
            self.assertEqual(base_path.read_bytes(), base_before)

            fill_page(Path(second["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(second["decision_file"])
            )
            third = EXCHANGE.accept_review_page(
                decisions, internal, lambda *_: ["b" * 64]
            )
            self.assertEqual(third["status"], "review_required")
            self.assertEqual(
                third["item_count"], 401 - first_count - second_count
            )

            fill_page(Path(third["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(third["decision_file"])
            )
            ready = EXCHANGE.accept_review_page(
                decisions, internal, lambda *_: ["c" * 64]
            )

            self.assertEqual(ready["status"], "ready")
            self.assertEqual(len(ready["decisions"]["items"]), 401)
            self.assertEqual(
                ready["judgment_identities"],
                ["a" * 64, "b" * 64, "c" * 64],
            )
            self.assertEqual(base_path.read_bytes(), base_before)
            self.assertEqual(
                len(list(session_dir.glob("accepted-*.json"))), 3
            )
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
                lambda: EXCHANGE.finish_review_session(session_dir)
                if session_dir.exists()
                else None
            )
            decision_path = Path(first["decision_file"])
            fill_page(decision_path)
            decisions, internal = EXCHANGE.load_decisions(decision_path)
            EXCHANGE.accept_review_page(decisions, internal)

            with self.assertRaisesRegex(Exception, "stale"):
                EXCHANGE.accept_review_page(decisions, internal)


if __name__ == "__main__":
    unittest.main()
