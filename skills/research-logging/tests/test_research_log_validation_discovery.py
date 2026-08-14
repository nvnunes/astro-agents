from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
DISCOVERY = importlib.import_module("validation.discovery")


class MarkdownDiscoveryTests(unittest.TestCase):
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
