"""ID-scoped authoring validates only the selected Markdown evidence item."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_log_command_sync import fixture
from test_log_evidence_sync import evidence, retained_files, set_results
from validation.evidence import index_entry_presentations


class SelectedEvidenceSyncTests(unittest.TestCase):
    def test_compare_and_sync_ignore_unrelated_invalid_markers_and_definitions(self):
        unrelated = (
            "[Broken URL](http://[invalid)<!-- eid:broken source=missing -->",
            "| A | B |\n| --- | --- |\n"
            "| ``<!-- eid:selected-extra source=missing unsupported=yes --> | "
            "`` <!-- eid:broken source=missing --> |",
            "`9` <!-- eid:broken source=missing select=/value -->",
            "`9`<!-- eid:broken source=missing unsupported=yes -->",
            "`9`<!-- eid:broken source=missing --> "
            "`9`<!-- eid:broken source=missing -->",
            "<!-- eid:broken source=missing -->\n```text\nunclosed",
            "<!-- eid:broken source=missing; column=/value -->\n"
            "| Value |\n| not-a-separator |\n",
        )
        for broken in unrelated:
            with (
                self.subTest(broken=broken),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.json").write_text('{"value": 3}')
                original = set_results(
                    document,
                    "``<!-- eid:selected source=values select=/value "
                    "render=integer -->\n\n" + broken,
                )
                result = evidence(
                    logical, "sync", "--id", "selected",
                    "--add-origin", "values=data/value.json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = original.replace(
                    "``<!-- eid:selected ", "`3`<!-- eid:selected "
                )
                self.assertEqual(document.read_text(), expected)
                records = json.loads((entry / "evidence.json").read_text())["records"]
                self.assertEqual([record["id"] for record in records], ["selected"])
                (entry / "data/value.json").write_text('{"value": 4}')
                before = retained_files(logical)
                result = evidence(logical, "compare", "--id", "selected")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout)["records"],
                    [{"id": "selected", "before": "`3`", "after": "`4`"}],
                )
                self.assertEqual(retained_files(logical), before)

                result = evidence(logical, "sync", "--id", "selected")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    document.read_text(),
                    expected.replace("`3`<!-- eid:selected ", "`4`<!-- eid:selected "),
                )
                with self.assertRaises(ValueError):
                    index_entry_presentations(document.read_text(), document="e001.md")

    def test_split_document_selected_duplicates_reject_without_writes(self):
        for duplicate in (
            "``<!-- eid:selected source=values select=/value -->",
            "<!-- eid:selected source=values",
            "<!-- EID = selected source=values -->",
        ):
            with (
                self.subTest(duplicate=duplicate),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.json").write_text('{"value": 3}')
                set_results(
                    document,
                    "``<!-- eid:selected source=values select=/value -->",
                )
                (entry / "e001a.md").write_text(
                    "## Experiment\n\n`Steps:`\n\n`Results:`\n\n" + duplicate
                )
                before = retained_files(logical)
                result = evidence(
                    logical, "sync", "--id", "selected",
                    "--add-origin", "values=data/value.json",
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(retained_files(logical), before)

    def test_selected_item_still_requires_experimental_context(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value": 3}')
            document.write_text(
                "## Synthesis\n\n`Findings:`\n\n"
                "``<!-- eid:selected source=values select=/value -->\n"
            )
            before = retained_files(logical)
            result = evidence(
                logical, "sync", "--id", "selected",
                "--add-origin", "values=data/value.json",
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("evidence.marker.context", result.stderr)
            self.assertEqual(retained_files(logical), before)

    def test_invalid_or_duplicate_selected_marker_rejects_without_writes(self):
        selected = (
            "`` <!-- eid:selected source=values select=/value -->",
            "``<!-- eid:selected source=values unsupported=yes -->",
            "``<!-- eid:selected source=values select=/value -->\n"
            "``<!-- eid:selected source=values select=/value -->",
            "``<!-- eid:selected source=values select=/value -->\n"
            "<!-- eid:selected source=values",
            "<!-- eid:selected source=values -->\n```text\nunclosed",
        )
        for marker in selected:
            with (
                self.subTest(marker=marker),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.json").write_text('{"value": 3}')
                set_results(document, marker)
                before = retained_files(logical)
                result = evidence(
                    logical, "sync", "--id", "selected",
                    "--add-origin", "values=data/value.json",
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(retained_files(logical), before)
