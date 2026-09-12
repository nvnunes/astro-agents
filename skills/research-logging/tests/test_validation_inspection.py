"""Current full and entry inspection retention contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_commands.inspection_queries import Query, inspect_result
from log_commands.inspection_views import render_view
from research_log_validation_test_support import mechanical_log
from validation.inspection import save_result


def _result(root: Path, *, entry: str) -> tuple:
    summary, _ = mechanical_log(root)
    finding = {
        "identity": f"entry:{entry}:missing-output",
        "code": "provenance.output.missing",
        "subject": "missing.csv",
        "status": "fail",
    }
    projection = {
        "schema": "research-log-published-validation/2",
        "validation_id": "fixture",
        "source_identity": "source",
        "unresolved": [],
        "chains": [
            {
                "chain_id": "chain",
                "entry": entry,
                "commands": [],
                "artifacts": ["missing.csv"],
                "findings": [finding],
            }
        ],
    }
    return (
        summary,
        {"status": "complete_findings", "published": False, "findings": [finding]},
        {
            "schema": "research-log-mechanical/1",
            "checks": [],
            "result_date": "2026-09-08",
            "rules_version": "fixture",
        },
        projection,
        {"kind": "entry", "entry": entry, "started_at": "2026-09-08T12:00:00Z"},
    )


class InspectionTests(unittest.TestCase):
    def test_entry_collection_is_paged_at_one_hundred_thousand_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = list(_result(Path(directory), entry="e001"))
            result[3]["chains"][0]["commands"] = [
                {
                    "identity": "cmd-e001",
                    "entry": "e001",
                    "document": "entries/e001.md",
                    "fence": 1,
                    "ordinal": 1,
                    "outputs": [],
                    "collections": [
                        {
                            "direction": "output",
                            "mechanism": "directory",
                            "members": [
                                f"data/item-{index:06d}.csv" for index in range(100_000)
                            ],
                        }
                    ],
                }
            ]
            identity = save_result(*result)
            log = result[0].with_suffix("")
            command = inspect_result(
                log, Query(action="command", result_id=identity, entity="cmd-e001")
            )
            self.assertLess(len(render_view(command).encode()), 16_384)
            reference = command["items"][0]["collections"][0]["members"]["ref"]
            first = inspect_result(
                log, Query(action="collection", result_id=identity, entity=reference)
            )
            self.assertEqual((first["total"], first["returned"]), (100_000, 20))
            self.assertIsNotNone(first["next_cursor"])
            second = inspect_result(
                log,
                Query(
                    action="collection",
                    result_id=identity,
                    entity=reference,
                    cursor=first["next_cursor"],
                ),
            )
            self.assertEqual(second["items"][0], "data/item-000020.csv")

            full_identity = save_result(
                result[0],
                {**result[1], "published": True},
                result[2],
                result[3],
                {"kind": "full", "started_at": result[4]["started_at"]},
            )
            full_command = inspect_result(
                log,
                Query(
                    action="command", result_id=full_identity, entity="cmd-e001"
                ),
            )
            full_reference = full_command["items"][0]["collections"][0]["members"][
                "ref"
            ]
            full_page = inspect_result(
                log,
                Query(
                    action="collection",
                    result_id=full_identity,
                    entity=full_reference,
                ),
            )
            self.assertEqual((full_page["total"], full_page["returned"]), (100_000, 20))

    def test_entry_replacement_preserves_other_entry_until_full_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _result(root, entry="e001")
            first_id = save_result(*first)
            second = (*first[:4], {**first[4], "entry": "e002"})
            second_id = save_result(*second)
            replacement = save_result(*first)
            log = first[0].with_suffix("")

            listed = inspect_result(log, Query(action="list", kind="entry"))
            self.assertEqual(
                {item["requested_entry"] for item in listed["items"]}, {"e001", "e002"}
            )
            with self.assertRaisesRegex(ValueError, "superseded or cleared"):
                inspect_result(log, Query(result_id=first_id))
            self.assertEqual(
                inspect_result(log, Query(result_id=second_id))["result_id"], second_id
            )
            self.assertEqual(
                inspect_result(log, Query(result_id=replacement))["status"],
                "complete_findings",
            )
            summary = inspect_result(log, Query(result_id=replacement))["metadata"]
            self.assertEqual(summary["dependency_entries"], [])
            self.assertEqual(summary["whole_log_limitations"], [])
            exported = inspect_result(
                log, Query(action="export", result_id=replacement)
            )
            self.assertEqual(exported["dependency_entries"], [])
            self.assertEqual(exported["whole_log_limitations"], [])

            full = save_result(
                first[0],
                {**first[1], "published": True},
                first[2],
                first[3],
                {"kind": "full", "started_at": first[4]["started_at"]},
            )
            self.assertEqual(
                inspect_result(log, Query(result_id=full))["status"],
                "complete_findings",
            )
            with self.assertRaisesRegex(ValueError, "superseded or cleared"):
                inspect_result(log, Query(result_id=second_id))
