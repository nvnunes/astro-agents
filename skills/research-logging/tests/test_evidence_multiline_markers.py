"""Multiline definition comments retain exact Markdown presentation boundaries."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log
from test_log_command_sync import fixture
from test_log_evidence_sync import evidence, retained_files, set_results
from validation.evidence import index_entry_presentations


class MultilineEvidenceMarkerTests(unittest.TestCase):
    def test_inline_comments_sharing_a_continuation_line_keep_distinct_locations(self):
        text = (
            "## Experiment\n\n`Steps:`\n\n`Results:`\n\n"
            "`1`<!-- eid:first source=values\n"
            "select=/first --> and `2`<!-- eid:second source=values\n"
            "select=/second -->.\n"
        )
        items = index_entry_presentations(text, document="entries/example/e001.md")
        self.assertEqual(
            [(item.id, item.value, item.line) for item in items],
            [("first", "1", 7), ("second", "2", 8)],
        )

    def test_selected_summary_cell_sync_accepts_other_multiline_table_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            document.write_text(
                document.read_text().replace(
                    "./pyrun scripts/build.py", "# Read retained evidence."
                )
            )
            (entry / "scripts/build.py").unlink()
            source = entry / "data/summary.json"
            source.write_text(
                json.dumps(
                    {
                        "wavelength_m": 0.0000022,
                        "cases": [{"name": "ramp", "error": 0.25}],
                    }
                )
            )
            table = (
                "<!-- eid:coefficients source=values path=/cases identity=/name;\n"
                "column=/name render=text;\n"
                "column=/error render=fixed:2 -->\n"
                "| Case | Error |\n| --- | ---: |\n"
            )
            original = set_results(
                document,
                "| Band | Wavelength |\n| --- | ---: |\n"
                "| K | ``<!-- eid:wavelength source=values "
                "select=/wavelength_m render=scientific:5 --> |\n\n" + table,
            )
            result = evidence(
                logical,
                "sync",
                "--id",
                "wavelength",
                "--add-origin",
                "values=data/summary.json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = original.replace(
                "``<!-- eid:wavelength", "`2.2000e-6`<!-- eid:wavelength"
            )
            self.assertEqual(document.read_text(), expected)
            result = evidence(logical, "sync", "--id", "coefficients")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                document.read_text(),
                expected.replace(table, table + "| ramp | 0.25 |\n"),
            )
            before = retained_files(logical)
            for record_id in ("wavelength", "coefficients"):
                result = evidence(logical, "sync", "--id", record_id)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(json.loads(result.stdout)["changed"])
                self.assertEqual(retained_files(logical), before)
            validation = run_log(
                logical.parent,
                "validate",
                "run",
                "--path",
                str(logical),
                "--format",
                "json",
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)
            self.assertEqual(json.loads(validation.stdout)["outcome"], "clear")

    def test_multiline_inline_and_block_markers_preserve_original_line_numbers(self):
        text = (
            "## Experiment\n\n`Steps:`\n\nRead retained data.\n\n`Results:`\n\n"
            "Value `1`<!-- eid:value source=values\n"
            "select=/value render=integer -->.\n\n"
            "<!-- eid:table source=values path=/cases;\n"
            "column=/value render=integer -->\n"
            "| Value |\n| ---: |\n| 1 |\n\n"
            "<!-- eid:output source=log\nline=1 -->\n```text\ncompleted\n```\n\n"
            "<!-- eid:diff\n source=diff -->\n```diff\n-old\n+new\n```\n\n"
            "[Data](data/value.json)<!-- eid:artifact\n source=values -->\n"
        )
        items = index_entry_presentations(text, document="entries/example/e001.md")
        self.assertEqual(
            [(item.id, item.kind, item.line) for item in items],
            [
                ("value", "statistic", 9),
                ("table", "table", 14),
                ("output", "output", 20),
                ("diff", "artifact", 26),
                ("artifact", "artifact", 31),
            ],
        )
        self.assertTrue(all(item.definition for item in items))
        self.assertEqual(items[0].value, "1")

    def test_multiline_markers_do_not_relax_adjacency_or_accept_malformed_comments(
        self,
    ):
        prefix = "## Experiment\n\n`Steps:`\n\n`Results:`\n\n"
        invalid = (
            "`1` <!-- eid:value source=values\nselect=/value -->",
            "<!-- eid:table source=values;\ncolumn=/value -->\n\n| Value |\n| --- |\n",
            "<!-- eid:value source=values\nselect=/value\n`1`",
            "<!-- eid:table source=values\n"
            "<!-- eid:nested source=values -->\n| Value |\n| --- |\n",
        )
        for presentation in invalid:
            with self.subTest(presentation=presentation), self.assertRaises(ValueError):
                index_entry_presentations(
                    prefix + presentation, document="entries/x/e001.md"
                )

    def test_multiline_eid_literals_inside_fences_are_not_authored(self):
        text = (
            "## Experiment\n\n`Steps:`\n\n`Results:`\n\n"
            "```text\n<!-- eid:literal source=values\nselect=/value -->\n```\n"
            "`1`<!-- eid:value source=values select=/value -->\n"
        )
        items = index_entry_presentations(text, document="entries/example/e001.md")
        self.assertEqual([(item.id, item.line) for item in items], [("value", 11)])
