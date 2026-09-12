from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log as run_log_in_process
from research_log_validation_test_support import mechanical_log, write


def run_log(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log_in_process(cwd, *arguments)


class ValidationCliTests(unittest.TestCase):
    def test_entry_selector_requires_one_path_and_a_resolved_stable_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            for arguments, code in (
                (("validate", "--entry", "e001"), "validation.entry.path_required"),
                (
                    ("validate", "--root", str(root), "--entry", "e001"),
                    "validation.entry.path_required",
                ),
                (
                    ("validate", "--path", str(log), "--entry", "not-an-entry"),
                    "entry.id.invalid",
                ),
                (
                    ("validate", "--path", str(log), "--entry", "e999"),
                    "entry.identity.unresolved",
                ),
            ):
                with self.subTest(arguments=arguments):
                    result = run_log(root, *arguments)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(code, result.stderr)

    def test_entry_json_statuses_dry_run_and_retention_are_scoped(self) -> None:
        for output_option, expected_status in (
            ("output-data", "complete_clear"),
            ("results", "complete_findings"),
        ):
            with (
                self.subTest(status=expected_status),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                summary, _ = mechanical_log(root, output_option=output_option)
                log = summary.with_suffix("")
                dry = run_log(
                    root,
                    "validate",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--dry-run",
                    "--format",
                    "json",
                )
                self.assertEqual(dry.returncode, 0, dry.stderr)
                dry_result = json.loads(dry.stdout)
                self.assertEqual(
                    dry_result["schema"],
                    "research-log-entry-validation-cli-result/1",
                )
                self.assertEqual(dry_result["status"], expected_status)
                self.assertEqual(dry_result["entry"], "e001")
                self.assertFalse(dry_result["published"])
                self.assertIn("record", dry_result)
                self.assertNotIn("result_id", dry_result)

                retained = run_log(
                    root,
                    "validate",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
                self.assertEqual(retained.returncode, 0, retained.stderr)
                retained_result = json.loads(retained.stdout)
                self.assertNotIn("record", retained_result)
                self.assertTrue(retained_result["result_id"])
                listed = run_log(
                    root,
                    "results",
                    "list",
                    "--path",
                    str(log),
                    "--kind",
                    "entry",
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
                self.assertEqual(listed.returncode, 0, listed.stderr)
                self.assertEqual(json.loads(listed.stdout)["total"], 1)

    def test_entry_selector_evaluates_all_split_documents_but_rejects_a_split_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = mechanical_log(root)
            split = entry.with_name("e001a.md")
            write(split, "# Continuation\n\nNo additional command.\n")
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [Continuation](study/entries/2026-08-29-e001-study/e001a.md)\n",
            )
            log = summary.with_suffix("")
            selected = run_log(
                root,
                "validate",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
                "--dry-run",
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(
                json.loads(selected.stdout)["scope"]["selected_documents"],
                ["e001", "e001a"],
            )
            split_selector = run_log(
                root,
                "validate",
                "--path",
                str(log),
                "--entry",
                "e001a",
            )
            self.assertEqual(split_selector.returncode, 2)
            self.assertIn("entry.id.invalid", split_selector.stderr)

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
                "--format",
                "json",
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
                        "--format",
                        "json",
                        "--path",
                        str(summary.with_suffix("")),
                        "--date",
                        "2026-08-29",
                        "--dry-run",
                        *cache_flags,
                    )
                    self.assertEqual(separated.returncode, 0, separated.stderr)
            rejected = run_log(
                root,
                "validate",
                "--format",
                "json",
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
                    "--format",
                    "json",
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
                    (summary.with_suffix("") / ".cache/results.sqlite")
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
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--dry-run",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertFalse(result["published"])
            self.assertIn("record", result)

    def test_entry_validation_preserves_the_published_full_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            full = run_log(root, "validate", "--path", str(log))
            self.assertEqual(full.returncode, 0, full.stderr)
            bundle = tuple(
                path.read_bytes()
                for path in (
                    log / ".cache/results.sqlite",
                    log / "validation.md",
                )
            )

            entry = run_log(
                root,
                "validate",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )

            self.assertEqual(entry.returncode, 0, entry.stderr)
            self.assertFalse(json.loads(entry.stdout)["published"])
            # A completed entry is retained in its own slot without replacing
            # the full publication or its derived report.
            self.assertEqual(bundle[1], (log / "validation.md").read_bytes())
            full_rows = run_log(
                root,
                "results",
                "list",
                "--path",
                str(log),
                "--kind",
                "full",
                "--format",
                "json",
            )
            self.assertEqual(full_rows.returncode, 0, full_rows.stderr)
            self.assertEqual(json.loads(full_rows.stdout)["total"], 1)
            entry_rows = run_log(
                root,
                "results",
                "list",
                "--path",
                str(log),
                "--kind",
                "entry",
                "--entry",
                "e001",
                "--format",
                "json",
            )
            self.assertEqual(entry_rows.returncode, 0, entry_rows.stderr)
            self.assertEqual(json.loads(entry_rows.stdout)["total"], 1)

    def test_entry_finding_dry_run_and_failure_preserve_full_bundle(self) -> None:
        """Every non-publishing entry outcome leaves the authoritative bundle intact."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry_document = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "--path", str(log))
            self.assertEqual(published.returncode, 0, published.stderr)
            paths = (
                log / ".cache/results.sqlite",
                log / "validation.md",
            )
            original = {path: (path.stat().st_ino, path.read_bytes()) for path in paths}

            # A genuine entry finding must not publish or replace full-result files.
            entry_document.write_text(
                entry_document.read_text(encoding="utf-8").replace(
                    "--output-data '<results>'", "--results '<results>'"
                ),
                encoding="utf-8",
            )
            finding = run_log(
                root,
                "validate",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
                "--dry-run",
            )
            self.assertEqual(finding.returncode, 0, finding.stderr)
            self.assertEqual(json.loads(finding.stdout)["status"], "complete_findings")
            self.assertEqual(
                {path: (path.stat().st_ino, path.read_bytes()) for path in paths},
                original,
            )

    def test_entry_operational_error_preserves_a_published_full_bundle(self) -> None:
        """An entry exit-2 error cannot replace authoritative full output."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "--path", str(log))
            self.assertEqual(published.returncode, 0, published.stderr)
            paths = (
                log / ".cache/results.sqlite",
                log / "validation.md",
            )
            before = {path: (path.stat().st_ino, path.read_bytes()) for path in paths}
            with mock.patch(
                "validation.engine._read_text", side_effect=OSError("fixture denied")
            ):
                failed = run_log(
                    root,
                    "validate",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
            self.assertEqual(failed.returncode, 2, failed.stderr)
            self.assertIn("fixture denied", failed.stderr)
            self.assertEqual(
                {path: (path.stat().st_ino, path.read_bytes()) for path in paths},
                before,
            )

            # A source-stability operational outcome is likewise non-publishing.
            with mock.patch(
                "validation.controller.research_snapshot",
                side_effect=[(("first", (1,)),), (("changed", (2,)),)],
            ):
                failed = run_log(
                    root,
                    "validate",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
            self.assertEqual(failed.returncode, 3, failed.stderr)
            self.assertFalse(json.loads(failed.stdout)["published"])
            self.assertEqual(
                {path: (path.stat().st_ino, path.read_bytes()) for path in paths},
                before,
            )

    def test_entry_source_change_is_incomplete_without_replacing_full_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "--path", str(log))
            self.assertEqual(published.returncode, 0, published.stderr)
            bundle = {
                log / ".cache/results.sqlite": (
                    log / ".cache/results.sqlite"
                ).read_bytes(),
                log / "validation.md": (log / "validation.md").read_bytes(),
            }
            with mock.patch(
                "validation.controller.research_snapshot",
                side_effect=[(("first", (1,)),), (("changed", (2,)),)],
            ):
                result = run_log(
                    root,
                    "validate",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )

            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertFalse(json.loads(result.stdout)["published"])
            self.assertEqual({path: path.read_bytes() for path in bundle}, bundle)

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
                        "--format",
                        "json",
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
                    self.assertIn("| — | — | — | Not published |", payload["report"])
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
                            good_summary.with_suffix("") / ".cache" / "results.sqlite"
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
                "--format",
                "json",
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
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(result["code"], "validation.unsupported_metadata")
            self.assertIn("`validation/.cache/upgrade-transactions`", result["report"])
            self.assertEqual(
                result["observed"]["paths"],
                ["validation/.cache/upgrade-transactions"],
            )
            self.assertEqual(completed.stderr, "")
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())


if __name__ == "__main__":
    unittest.main()
