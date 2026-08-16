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
            self.assertEqual(first["item_count"], 200)

            fill_page(Path(first["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(first["decision_file"])
            )
            second = EXCHANGE.accept_deferred_orphan_page(decisions, internal)
            self.assertEqual(second["status"], "review_required")
            self.assertEqual(second["item_count"], 200)
            self.assertEqual(base_path.read_bytes(), base_before)

            fill_page(Path(second["decision_file"]))
            decisions, internal = EXCHANGE.load_decisions(
                Path(second["decision_file"])
            )
            third = EXCHANGE.accept_deferred_orphan_page(decisions, internal)
            self.assertEqual(third["status"], "review_required")
            self.assertEqual(third["item_count"], 1)

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
                (session_dir / EXCHANGE.DEFERRED_MANIFEST_FILENAME).stat().st_size,
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
