from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

CLI = importlib.import_module("validation.cli")


class ValidationCliTests(unittest.TestCase):
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
            ]
        )

        self.assertEqual(args.entry, "e003")
        self.assertEqual(args.target, "data/result.csv")
        self.assertEqual(args.kind, "semantic_fallback")


if __name__ == "__main__":
    unittest.main()
