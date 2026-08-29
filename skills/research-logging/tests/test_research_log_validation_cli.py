from __future__ import annotations

import importlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import SCRIPT, mechanical_log, write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

CLI = importlib.import_module("validation.cli")


class ValidationCliTests(unittest.TestCase):
    def test_only_mechanical_validation_arguments_are_public(self) -> None:
        args = CLI.build_parser().parse_args(
            [
                "validate",
                "--summary",
                "mini.md",
                "--date",
                "2026-08-29",
                "--jobs",
                "3",
                "--recompute",
                "--dry-run",
            ]
        )
        self.assertEqual(args.summary, Path("mini.md"))
        self.assertEqual(args.date, "2026-08-29")
        self.assertEqual(args.jobs, 3)
        self.assertTrue(args.recompute)
        self.assertTrue(args.dry_run)
        for retired in ("--decisions", "--mode", "--review-diagnostics"):
            with self.subTest(retired=retired), self.assertRaises(SystemExit):
                CLI.build_parser().parse_args(
                    ["validate", "--summary", "mini.md", retired, "value"]
                )

    def test_completed_statuses_exit_zero_and_incomplete_is_nonzero(self) -> None:
        for status, expected in (
            ("complete_clear", 0),
            ("complete_findings", 0),
            ("upgrade_required", 0),
            ("incomplete", 3),
        ):
            with self.subTest(status=status):
                output = io.StringIO()
                with mock.patch.object(
                    CLI,
                    "validate",
                    return_value={"status": status},
                ), redirect_stdout(output):
                    exit_code = CLI.main(
                        ["validate", "--summary", "mini.md"]
                    )
                self.assertEqual(exit_code, expected)
                self.assertIn(f'"status": "{status}"', output.getvalue())

    def test_tool_failure_is_clear_and_nonzero(self) -> None:
        error = CLI.ValidationControllerError("cannot complete")
        stderr = io.StringIO()
        with mock.patch.object(CLI, "validate", side_effect=error), redirect_stderr(
            stderr
        ):
            exit_code = CLI.main(["validate", "--summary", "mini.md"])
        self.assertEqual(exit_code, 2)
        self.assertIn("ValidationControllerError: cannot complete", stderr.getvalue())

    def test_executable_returns_zero_upgrade_result_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs" / "legacy.md"
            log_root = root / "docs" / "legacy"
            write(summary, "# Legacy\n")
            write(log_root / "entries" / "e001" / "evidence.csv", "entry\n")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "validate",
                    "--summary",
                    str(summary),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            result = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(result["code"], "validation.upgrade_required")
            self.assertFalse(result["published"])
            self.assertFalse((log_root / "validation" / "mechanical.json").exists())

    def test_executable_returns_zero_for_clear_and_finding_results(self) -> None:
        for output_option, expected in (
            ("output-data", "complete_clear"),
            ("results", "complete_findings"),
        ):
            with self.subTest(status=expected), tempfile.TemporaryDirectory() as (
                directory
            ):
                summary, _ = mechanical_log(
                    Path(directory), output_option=output_option
                )

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "validate",
                        "--summary",
                        str(summary),
                        "--date",
                        "2026-08-29",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["status"], expected)

    def test_executable_tool_error_is_nonzero_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "validate",
                    "--summary",
                    str(summary),
                    "--date",
                    "not-a-date",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("YYYY-MM-DD", completed.stderr)
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_executable_pending_upgrade_is_a_nonzero_operational_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            write(
                summary.with_suffix("")
                / "validation/.cache/upgrade-transactions/pending/transaction.json",
                "{}\n",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "validate",
                    "--summary",
                    str(summary),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("upgrade.recovery.required", completed.stderr)
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())


if __name__ == "__main__":
    unittest.main()
