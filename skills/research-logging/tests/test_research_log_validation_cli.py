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
    def test_discovery_uses_summary_contract_not_filename_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary, _ = mechanical_log(root)
            named_validation = root / "docs" / "validation.md"
            (root / "docs" / "validation").mkdir()
            write(
                named_validation,
                "# Validation study\n\n"
                "Validation: [latest completed report](validation/validation.md)\n",
            )
            write(
                ordinary.with_suffix("") / "validation.md",
                "# Validation\n\n## Mechanical Validation\n"
                + ("generated finding\n" * 10_000),
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = CLI.main(["discover", "--root", str(root)])

            result = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["schema"], "research-log-discovery-result/1")
            self.assertEqual(
                result["summaries"],
                sorted(
                    (
                        ordinary.resolve().as_posix(),
                        named_validation.resolve().as_posix(),
                    )
                ),
            )

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
            ("unsupported_metadata", 0),
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

    def test_executable_unsupported_metadata_returns_preflight_result(self) -> None:
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

            self.assertEqual(completed.returncode, 0)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(result["code"], "validation.unsupported_metadata")
            self.assertEqual(
                result["observed"]["paths"],
                ["validation/.cache/upgrade-transactions"],
            )
            self.assertEqual(completed.stderr, "")
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())


if __name__ == "__main__":
    unittest.main()
