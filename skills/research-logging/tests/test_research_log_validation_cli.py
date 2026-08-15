from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

CLI = importlib.import_module("validation.cli")


class ValidationCliTests(unittest.TestCase):
    def test_update_summary_command_is_retired(self) -> None:
        with self.assertRaises(SystemExit):
            CLI.build_parser().parse_args(
                ["update-summary", "--summary", "mini.md", "--output-dir", "mini"]
            )

    def test_review_accepts_combined_diagnostic_filters(self) -> None:
        args = CLI.build_parser().parse_args(
            [
                "review",
                "--scan",
                "scan.json",
                "--adjudication",
                "adjudication.json",
                "--output",
                "review.md",
                "--entry",
                "e003",
                "--target",
                "data/result.csv",
                "--kind",
                "semantic_fallback",
                "--batch-size",
                "75",
                "--batch-number",
                "3",
                "--metrics",
                "review-metrics.json",
            ]
        )

        self.assertEqual(args.entry, "e003")
        self.assertEqual(args.target, "data/result.csv")
        self.assertEqual(args.kind, "semantic_fallback")
        self.assertEqual(args.batch_size, 75)
        self.assertEqual(args.batch_number, 3)
        self.assertEqual(args.metrics, Path("review-metrics.json"))


if __name__ == "__main__":
    unittest.main()
