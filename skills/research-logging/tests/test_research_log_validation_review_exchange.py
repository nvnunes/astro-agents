from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from research_log_validation_test_support import write

EXCHANGE = importlib.import_module("validation.review_exchange")


def deferred_fixture(root: Path, count: int = 401):
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


class DeferredOrphanReviewTests(unittest.TestCase):
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
            scan, adjudication = deferred_fixture(root, count=250)
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
                lambda: EXCHANGE.finish_deferred_orphan_session(session_dir)
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
            scan, adjudication = deferred_fixture(root)
            first = EXCHANGE.create_exchange(
                scan, adjudication, {"record": {}, "cache": {}}
            )
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: EXCHANGE.finish_deferred_orphan_session(session_dir)
                if session_dir.exists()
                else None
            )
            base_path = session_dir / EXCHANGE.DEFERRED_BASE_FILENAME
            base_before = base_path.read_bytes()
            first_count = first["item_count"]
            self.assertLessEqual(first_count, 200)
            self.assertLessEqual(first["byte_count"], EXCHANGE.MAX_PACKET_BYTES)

            fill_page(Path(first["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(first["decision_file"])
            )
            second = EXCHANGE.accept_deferred_orphan_page(decisions, internal)
            self.assertEqual(second["status"], "review_required")
            second_count = second["item_count"]
            self.assertLessEqual(second_count, 200)
            self.assertEqual(base_path.read_bytes(), base_before)

            fill_page(Path(second["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(second["decision_file"])
            )
            third = EXCHANGE.accept_deferred_orphan_page(decisions, internal)
            self.assertEqual(third["status"], "review_required")
            self.assertEqual(
                third["item_count"], 401 - first_count - second_count
            )

            fill_page(Path(third["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(third["decision_file"])
            )
            ready = EXCHANGE.accept_deferred_orphan_page(decisions, internal)

            self.assertEqual(ready["status"], "ready")
            self.assertEqual(len(ready["decisions"]["items"]), 401)
            self.assertEqual(base_path.read_bytes(), base_before)
            self.assertEqual(
                len(list(session_dir.glob("accepted-*.json"))), 3
            )
            self.assertLess(
                (session_dir / EXCHANGE.SESSION_STATE_FILENAME).stat().st_size,
                4096,
            )
            EXCHANGE.finish_deferred_orphan_session(session_dir)
            self.assertFalse(session_dir.exists())

    def test_accepted_page_cannot_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scan, adjudication = deferred_fixture(root)
            first = EXCHANGE.create_exchange(scan, adjudication, {})
            session_dir = Path(first["decision_file"]).parent.parent
            self.addCleanup(
                lambda: EXCHANGE.finish_deferred_orphan_session(session_dir)
                if session_dir.exists()
                else None
            )
            decision_path = Path(first["decision_file"])
            fill_page(decision_path)
            decisions, internal = EXCHANGE.load_decisions(decision_path)
            EXCHANGE.accept_deferred_orphan_page(decisions, internal)

            with self.assertRaisesRegex(Exception, "stale"):
                EXCHANGE.accept_deferred_orphan_page(decisions, internal)


if __name__ == "__main__":
    unittest.main()
