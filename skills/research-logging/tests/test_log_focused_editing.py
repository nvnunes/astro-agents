"""Focused editing leaves unrelated broken Markdown and records untouched."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_log_command_sync import fixture
from test_log_evidence_sync import evidence, retained_files, set_results
from test_log_graph_lifecycle import action, checked
from validation.evidence import index_summary_references

SELECTED = "`3`<!-- eid:selected source=values select=/value render=integer -->"
UNRELATED = "`9` <!-- eid:broken source=other select=/value -->"
BAD_REFERENCE = "`9`<!-- ref unrelated malformed -->"


def prepared(root: Path) -> tuple[Path, Path, Path]:
    logical, entry, document = fixture(root, "./pyrun scripts/build.py")
    (entry / "data/value.json").write_text('{"value":3}')
    (entry / "data/other.json").write_text('{"value":9}')
    set_results(
        document,
        SELECTED + "\n`9`<!-- eid:other source=other select=/value render=integer -->",
    )
    for record_id, name, path in (
        ("selected", "values", "data/value.json"),
        ("other", "other", "data/other.json"),
    ):
        checked(
            evidence(
                logical, "sync", "--id", record_id, "--add-origin", f"{name}={path}"
            )
        )
    return logical, entry, document


class FocusedEditingTests(unittest.TestCase):
    def test_sync_preserves_unrelated_bad_references_and_refreshes_selected_only(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = prepared(Path(directory))
            summary = logical.with_suffix(".md")
            original = summary.read_text() + (
                "\n`3`<!-- ref entry = e001; eid = selected --> and "
                + BAD_REFERENCE
                + " and `9`<!-- ref entry = e002; eid = selected; "
                "row = nope --> and `9`<!-- ref entry = e001; "
                "eid = selected_more -->\n"
            )
            summary.write_text(original)
            other = json.loads((entry / "evidence.json").read_text())["records"][0]
            (entry / "data/value.json").write_text('{"value":4}')
            checked(evidence(logical, "sync", "--id", "selected"))
            self.assertEqual(
                summary.read_text(), original.replace("`3`<!--", "`4`<!--")
            )
            self.assertIn("`4`<!-- eid:selected ", document.read_text())
            records = json.loads((entry / "evidence.json").read_text())["records"]
            self.assertEqual(
                next(record for record in records if record["id"] == "other"), other
            )
            with self.assertRaises(ValueError):
                index_summary_references(summary.read_text())

    def test_evidence_rename_and_delete_ignore_unrelated_broken_markdown(self):
        for verb in ("rename", "delete"):
            with self.subTest(verb=verb), tempfile.TemporaryDirectory() as directory:
                logical, entry, document = prepared(Path(directory))
                records = json.loads((entry / "evidence.json").read_text())["records"]
                other = next(record for record in records if record["id"] == "other")
                text = document.read_text()
                if verb == "rename":
                    text = text.replace("eid:selected ", "eid:renamed ")
                    args = ("selected", "renamed")
                else:
                    text = text.replace(SELECTED, "")
                    args = ("--id", "selected")
                text += "\n" + UNRELATED + "\n"
                document.write_text(text)
                summary = logical.with_suffix(".md")
                original_summary = summary.read_text() + "\n" + BAD_REFERENCE + "\n"
                summary.write_text(original_summary)
                before = retained_files(logical)
                checked(action(logical, "evidence", verb, *args, "--dry-run"))
                self.assertEqual(retained_files(logical), before)
                checked(action(logical, "evidence", verb, *args))
                self.assertEqual(document.read_text(), text)
                self.assertEqual(summary.read_text(), original_summary)
                records = json.loads((entry / "evidence.json").read_text())["records"]
                self.assertEqual(
                    next(record for record in records if record["id"] == "other"), other
                )
                self.assertNotIn("selected", [record["id"] for record in records])

    def test_data_rename_validates_only_affected_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = prepared(Path(directory))
            records = json.loads((entry / "evidence.json").read_text())["records"]
            other = next(record for record in records if record["id"] == "other")
            text = document.read_text().replace("source=values ", "source=renamed ")
            text += "\n" + UNRELATED + "\n"
            document.write_text(text)
            before = retained_files(logical)
            checked(action(logical, "data", "rename", "values", "renamed", "--dry-run"))
            self.assertEqual(retained_files(logical), before)
            checked(action(logical, "data", "rename", "values", "renamed"))
            self.assertEqual(document.read_text(), text)
            records = json.loads((entry / "evidence.json").read_text())["records"]
            self.assertEqual(
                next(record for record in records if record["id"] == "other"), other
            )
            selected = next(record for record in records if record["id"] == "selected")
            self.assertEqual(selected["sources"][0]["source"], "<renamed>")

    def test_selected_bad_summary_references_reject_without_writes(self):
        references = (
            "`3`<!-- ref entry = e001; eid = selected; row = nope -->",
            "`3` <!-- ref entry = e001; eid = selected -->",
            "<!-- ref entry = e001; eid = selected -->",
            "`3`<!-- ref entry = e001;\neid = selected -->",
            "`3`<!-- ref entry = e001; eid = selected",
        )
        for reference in references:
            with (
                self.subTest(reference=reference),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, _, _ = prepared(Path(directory))
                summary = logical.with_suffix(".md")
                summary.write_text(summary.read_text() + "\n" + reference + "\n")
                before = retained_files(logical)
                result = evidence(logical, "sync", "--id", "selected")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(retained_files(logical), before)

    def test_selected_remaining_references_block_evidence_lifecycles(self):
        for verb in ("rename", "delete"):
            for raw in (
                "`3`<!-- ref entry = e001; eid = selected -->",
                "`3`<!-- ref entry = e001; eid = selected; row = nope -->",
            ):
                with (
                    self.subTest(verb=verb, reference=raw),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    logical, _, document = prepared(Path(directory))
                    text = document.read_text()
                    if verb == "rename":
                        text = text.replace("eid:selected ", "eid:renamed ")
                        args = ("selected", "renamed")
                    else:
                        text = text.replace(SELECTED, "")
                        args = ("--id", "selected")
                    document.write_text(text)
                    summary = logical.with_suffix(".md")
                    summary.write_text(summary.read_text() + "\n" + raw + "\n")
                    before = retained_files(logical)
                    result = action(logical, "evidence", verb, *args)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(retained_files(logical), before)

    def test_renamed_selected_marker_must_be_unique_across_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = prepared(Path(directory))
            text = document.read_text().replace("eid:selected ", "eid:renamed ")
            document.write_text(text)
            (entry / "e001a.md").write_text(
                "## Experiment\n\n`Steps:`\n\n`Results:`\n\n"
                + SELECTED.replace("eid:selected ", "eid:renamed ")
            )
            before = retained_files(logical)
            result = action(logical, "evidence", "rename", "selected", "renamed")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(retained_files(logical), before)
