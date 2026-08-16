from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

CLI = importlib.import_module("validation.cli")


class ValidationCliTests(unittest.TestCase):
    def test_only_validate_is_public(self) -> None:
        for command in (
            "scan",
            "prepare",
            "review",
            "decide",
            "render",
            "lint",
            "update-summary",
        ):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                CLI.build_parser().parse_args([command])

    def test_validate_accepts_target_workflow_arguments(self) -> None:
        args = CLI.build_parser().parse_args(
            [
                "validate",
                "--summary",
                "mini.md",
                "--decisions",
                "review-decisions.json",
                "--date",
                "2026-08-16",
                "--jobs",
                "3",
            ]
        )

        self.assertEqual(args.summary, Path("mini.md"))
        self.assertEqual(args.decisions, Path("review-decisions.json"))
        self.assertEqual(args.date, "2026-08-16")
        self.assertEqual(args.jobs, 3)


if __name__ == "__main__":
    unittest.main()
