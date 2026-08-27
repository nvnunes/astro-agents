from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
DISCOVERY = importlib.import_module("validation.discovery")
VALIDATION_NOTES = importlib.import_module("validation.validation_notes")


class MarkdownDiscoveryTests(unittest.TestCase):
    def test_retention_scope_aliases_normalize_to_entry_relative_posix(self) -> None:
        entry = "docs/log/entries/fixture/e001.md"

        self.assertEqual(
            VALIDATION_NOTES.normalized_retention_scope("./data/../data/", entry),
            "data",
        )
        self.assertEqual(
            VALIDATION_NOTES.normalized_retention_scope(
                "docs/log/entries/fixture/images/", entry
            ),
            "images",
        )
        self.assertEqual(
            VALIDATION_NOTES.normalized_retention_scope("<reference_grid>", entry),
            "<reference_grid>",
        )
        self.assertIsNone(
            VALIDATION_NOTES.retention_scope(
                "- Reproduce `data/result.csv` with the stated tolerance."
            )
        )

    def test_validation_bullets_are_independent_and_preserve_continuations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_text(
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\nThe result is `1.0`.\n\n"
                "`Validation:`\n\n"
                "- Retain `data/run-a` because it is one experiment.\n"
                "- Retain\n"
                "  `images/run-a` because it presents that experiment.\n"
                "- Reproduction may use a newer supported dependency.\n",
                encoding="utf-8",
            )

            notes = DISCOVERY.parse_markdown(entry)["validation_notes"]

            self.assertEqual(len(notes), 3)
            self.assertEqual(
                [note.get("retention_scope") for note in notes],
                ["data/run-a", "images/run-a", None],
            )
            self.assertEqual(
                notes[1]["text"],
                "- Retain `images/run-a` because it presents that experiment.",
            )

    def test_validation_note_text_is_not_part_of_section_content_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            prefix = (
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\nThe result is `1.0`.\n\n`Validation:`\n\n"
            )
            entry.write_text(
                prefix + "- Retain `data/run-a` because it is one experiment.\n",
                encoding="utf-8",
            )
            original = DISCOVERY.parse_markdown(entry)["sections"][1][
                "content_identity"
            ]
            entry.write_text(
                prefix + "- Retain `data/run-b` because it is another experiment.\n",
                encoding="utf-8",
            )
            note_changed = DISCOVERY.parse_markdown(entry)["sections"][1][
                "content_identity"
            ]
            entry.write_text(
                prefix.replace("The result is `1.0`.", "The result is `2.0`.")
                + "- Retain `data/run-b` because it is another experiment.\n",
                encoding="utf-8",
            )
            result_changed = DISCOVERY.parse_markdown(entry)["sections"][1][
                "content_identity"
            ]

            self.assertEqual(note_changed, original)
            self.assertNotEqual(result_changed, original)

    def test_invalid_utf8_is_a_discovery_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_bytes(b"# Invalid\n\xff\n")

            with self.assertRaisesRegex(
                DISCOVERY.MarkdownDiscoveryError,
                "file is not valid UTF-8",
            ):
                DISCOVERY.parse_markdown(entry)

    def test_long_fence_is_not_closed_by_shorter_or_nonclosing_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_text(
                "# Fixture\n\n"
                "## Trial\n\n"
                "`Steps:`\n\n"
                "````bash\n"
                "echo start\n"
                "```\n"
                "## Not a section\n"
                "```` trailing text\n"
                "echo finish\n"
                "`````\n\n"
                "`Results:`\n\n"
                "The retained value is `1.0`.\n",
                encoding="utf-8",
            )

            parsed = DISCOVERY.parse_markdown(entry)

            self.assertEqual(
                [heading["text"] for heading in parsed["headings"]],
                ["Fixture", "Trial"],
            )
            self.assertEqual(len(parsed["fenced_blocks"]), 1)
            self.assertIn("## Not a section", parsed["fenced_blocks"][0]["text"])
            self.assertEqual(
                [item["selector"] for item in parsed["presented_items"]],
                ["1.0"],
            )

    def test_collision_selectors_are_stable_within_one_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_text(
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\nValues were `0.2` and `0.2`.\n",
                encoding="utf-8",
            )

            parsed = DISCOVERY.parse_markdown(entry)

            self.assertEqual(
                [item["selector"] for item in parsed["presented_items"]],
                ["0.2 [occurrence 1]", "0.2 [occurrence 2]"],
            )

    def test_wrapped_statistic_context_preserves_its_complete_list_item(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_text(
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\n- Retain the generated summaries.\n\n"
                "`Observations:`\n\n"
                "- The direct plots are consistency diagnostics.\n"
                "- Unstable points differ in the GB cases. GB `ZA=30 deg` has\n"
                "  ratios of `0.956` for unstable points and `0.534` for stable "
                "points;\n"
                "  GB `ZA=40 deg` has `0.824` and `0.503`.\n"
                "- The J case is mixed.\n",
                encoding="utf-8",
            )

            parsed = DISCOVERY.parse_markdown(entry)
            statistics = {
                item["base_selector"]: item
                for item in parsed["presented_items"]
                if item["kind"] == "statistic"
            }

            expected = (
                "- Unstable points differ in the GB cases. GB `ZA=30 deg` has "
                "ratios of `0.956` for unstable points and `0.534` for stable "
                "points; GB `ZA=40 deg` has `0.824` and `0.503`."
            )
            self.assertEqual(
                set(statistics), {"0.956", "0.534", "0.824", "0.503"}
            )
            for item in statistics.values():
                self.assertEqual(item["context"], expected)
                self.assertEqual(item["end_line"], 18)

    def test_uuid_identifier_is_not_a_presented_statistic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            entry.write_text(
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\n"
                "Task `019ff397-d06e-7b92-a077-88a2414445d9` consumed "
                "`2,878,914 reported input tokens`.\n",
                encoding="utf-8",
            )

            parsed = DISCOVERY.parse_markdown(entry)

            self.assertEqual(
                [item["selector"] for item in parsed["presented_items"]],
                ["2,878,914 reported input tokens"],
            )

    def test_summary_statistics_are_limited_to_summary_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "log.md"
            summary.write_text(
                "# Log\n\n"
                "## Summary\n\n- Retained result: `1.2`.\n\n"
                "## Follow-up\n\n- Run `3` additional checks.\n\n"
                "## Entries\n\n- No entries yet.\n",
                encoding="utf-8",
            )

            parsed = DISCOVERY.parse_markdown(summary)

            self.assertEqual(
                [item["selector"] for item in parsed["summary_statistics"]],
                ["1.2"],
            )


if __name__ == "__main__":
    unittest.main()
