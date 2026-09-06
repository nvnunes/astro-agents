from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log as run_log_in_process
from research_log_validation_test_support import mechanical_log, write


def run_log(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log_in_process(cwd, *arguments)


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
            write(
                ordinary.with_suffix("") / ".cache" / "nested.md",
                "# Cache decoy\n\n"
                "Validation: [latest completed report](nested/validation.md)\n",
            )

            completed = run_log(root, "discover", "--root", str(root))

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            accepted = run_log(
                root,
                "validate",
                "--path",
                str(summary.with_suffix("")),
                "--date",
                "2026-08-29",
                "--recompute",
                "--dry-run",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            for cache_flags in (
                ("--recompute-validation",),
                ("--recompute-fingerprints",),
                ("--recompute-validation", "--recompute-fingerprints"),
            ):
                with self.subTest(cache_flags=cache_flags):
                    separated = run_log(
                        root,
                        "validate",
                        "--path",
                        str(summary.with_suffix("")),
                        "--date",
                        "2026-08-29",
                        "--dry-run",
                        *cache_flags,
                    )
                    self.assertEqual(
                        separated.returncode, 0, separated.stderr
                    )
            rejected = run_log(
                root,
                "validate",
                "--path",
                str(summary.with_suffix("")),
                "--summary",
                "value",
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_cli_returns_zero_for_clear_and_finding_results(self) -> None:
        for output_option, expected in (
            ("output-data", "complete_clear"),
            ("results", "complete_findings"),
        ):
            with (
                self.subTest(status=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                summary, _ = mechanical_log(root, output_option=output_option)

                completed = run_log(
                    root,
                    "validate",
                    "--path",
                    str(summary.with_suffix("")),
                    "--date",
                    "2026-08-29",
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["status"], expected)
                self.assertEqual(
                    result["schema"], "research-log-validation-cli-result/1"
                )
                self.assertNotIn("record", result)
                self.assertEqual(
                    result["generated"]["mechanical"],
                    (summary.with_suffix("") / "validation/results.json")
                    .resolve()
                    .as_posix(),
                )

    def test_dry_run_retains_the_record_when_no_bundle_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)

            completed = run_log(
                root,
                "validate",
                "--path",
                str(summary.with_suffix("")),
                "--dry-run",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertFalse(result["published"])
            self.assertIn("record", result)

    def test_root_validation_reports_failures_and_continues_in_both_orders(
        self,
    ) -> None:
        for bad_name, good_name in (("a-bad", "z-good"), ("z-bad", "a-good")):
            for dry_run in (False, True):
                with (
                    self.subTest(bad_name=bad_name, dry_run=dry_run),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    (root / bad_name).mkdir()
                    (root / good_name).mkdir()
                    bad_summary, _ = mechanical_log(root / bad_name)
                    good_summary, _ = mechanical_log(root / good_name)
                    (root / bad_name / ".git").rmdir()
                    arguments = [
                        "validate",
                        "--root",
                        str(root),
                        "--date",
                        "2026-08-29",
                    ]
                    if dry_run:
                        arguments.append("--dry-run")

                    completed = run_log(root, *arguments)

                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    self.assertEqual(completed.stderr, "")
                    payload = json.loads(completed.stdout)
                    self.assertEqual(
                        payload["schema"], "research-log-validation-batch-result/1"
                    )
                    self.assertEqual(len(payload["results"]), 1)
                    self.assertEqual(
                        payload["results"][0]["summary"],
                        good_summary.resolve().as_posix(),
                    )
                    self.assertIn(
                        f"[Study](<{good_summary.resolve()}>)",
                        payload["report"],
                    )
                    self.assertIn(bad_summary.resolve().as_posix(), payload["report"])
                    self.assertIn(
                        "| — | — | — | — | Not published |", payload["report"]
                    )
                    self.assertIn("Validation could not start:", payload["report"])
                    self.assertEqual(
                        payload["report"].count("Not published"),
                        2 if dry_run else 1,
                    )
                    self.assertEqual(
                        payload["failures"],
                        [
                            {
                                "code": "validation.failed",
                                "message": (
                                    "could not resolve project root from Git "
                                    f"metadata: {bad_summary.resolve()}"
                                ),
                                "summary": bad_summary.resolve().as_posix(),
                            }
                        ],
                    )
                    self.assertEqual(
                        (
                            good_summary.with_suffix("")
                            / "validation"
                            / "results.json"
                        ).is_file(),
                        not dry_run,
                    )

    def test_cli_tool_error_is_nonzero_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)

            completed = run_log(
                root,
                "validate",
                "--path",
                str(summary.with_suffix("")),
                "--date",
                "not-a-date",
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("YYYY-MM-DD", completed.stderr)
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_cli_unsupported_metadata_returns_preflight_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            write(
                summary.with_suffix("")
                / "validation/.cache/upgrade-transactions/pending/transaction.json",
                "{}\n",
            )

            completed = run_log(
                root,
                "validate",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(result["code"], "validation.unsupported_metadata")
            self.assertIn(
                "`validation/.cache/upgrade-transactions`", result["report"]
            )
            self.assertEqual(
                result["observed"]["paths"],
                ["validation/.cache/upgrade-transactions"],
            )
            self.assertEqual(completed.stderr, "")
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())


if __name__ == "__main__":
    unittest.main()
