from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log as run_log_in_process
from research_log_validation_test_support import mechanical_log, write
from validation.discovery import discover_summaries

VALIDATION_RUN = importlib.import_module("log_commands.validation_run")


def run_log(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log_in_process(cwd, *arguments)


class ValidationCliTests(unittest.TestCase):
    def test_run_selection_failure_has_a_stable_code(self) -> None:
        calls = (
            (
                "selection",
                "validation.selection.invalid",
                lambda: VALIDATION_RUN.run_validation(
                    path=Path("log"),
                    root=Path("project"),
                    options=VALIDATION_RUN.ValidationOptions(),
                ),
            ),
        )
        for case, code, call in calls:
            with self.subTest(case=case), self.assertRaises(
                VALIDATION_RUN.ActionError
            ) as raised:
                call()
            self.assertEqual(raised.exception.code, code)

    def test_help_exposes_only_the_settled_validation_and_command_grammars(
        self,
    ) -> None:
        validation = run_log(Path.cwd(), "validate", "--help")
        listing = run_log(Path.cwd(), "validate", "list", "--help")
        command = run_log(Path.cwd(), "command", "--help")
        verify = run_log(Path.cwd(), "command", "verify", "--help")

        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertIn("{run,show,list,detail,render}", validation.stdout)
        self.assertNotIn("results", validation.stdout)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("{findings,batches,blocked,failed}", listing.stdout)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertIn("{sync,rename,delete,list,verify,show}", command.stdout)
        self.assertNotIn("repair-check", command.stdout)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn("--path PATH", verify.stdout)
        self.assertNotIn("[--path PATH]", verify.stdout)

    def test_command_verify_requires_a_log_path(self) -> None:
        result = run_log(
            Path.cwd(),
            "command",
            "verify",
            "--entry",
            "e001",
            "--cid",
            "example",
            "--execution-id",
            "run-1",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("the following arguments are required: --path", result.stderr)

    def test_entry_selector_requires_one_path_and_a_resolved_stable_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            for arguments, message in (
                (
                    ("validate", "run", "--entry", "e001"),
                    "--path --root is required",
                ),
                (
                    ("validate", "run", "--root", str(root), "--entry", "e001"),
                    "--entry requires --path",
                ),
                (
                    ("validate", "run", "--path", str(log), "--entry", "not-an-entry"),
                    "entry.id.invalid",
                ),
                (
                    ("validate", "run", "--path", str(log), "--entry", "e999"),
                    "entry.identity.unresolved",
                ),
            ):
                with self.subTest(arguments=arguments):
                    result = run_log(root, *arguments)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)

    def test_entry_json_statuses_dry_run_and_retention_are_scoped(self) -> None:
        for output_option, expected_outcome in (
            ("output-data", "clear"),
            ("results", "findings"),
        ):
            with (
                self.subTest(outcome=expected_outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                summary, _ = mechanical_log(root, output_option=output_option)
                log = summary.with_suffix("")
                dry = run_log(
                    root,
                    "validate",
                    "run",
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
                    "research-log-validation-run/1",
                )
                self.assertEqual(dry_result["outcome"], expected_outcome)
                self.assertEqual(dry_result["target"]["entry"], "e001")
                self.assertFalse(dry_result["saved"])
                self.assertNotIn("record", dry_result)
                self.assertNotIn("snapshot_id", dry_result)

                retained = run_log(
                    root,
                    "validate",
                    "run",
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
                self.assertTrue(retained_result["saved"])
                self.assertIn("saved_at", retained_result)
                listed = run_log(
                    root,
                    "validate",
                    "list",
                    "findings",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
                self.assertEqual(listed.returncode, 0, listed.stderr)
                total = json.loads(listed.stdout)["total"]
                self.assertEqual(total > 0, expected_outcome == "findings")

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
                "run",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(json.loads(selected.stdout)["target"]["entry"], "e001")
            with sqlite3.connect(log / ".cache/results.sqlite") as database:
                evaluated = database.execute(
                    "SELECT entry FROM validation_snapshot_entries "
                    "WHERE relation='evaluated' ORDER BY position"
                ).fetchall()
            self.assertEqual(evaluated, [("e001",), ("e001a",)])
            split_selector = run_log(
                root,
                "validate",
                "run",
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
            (root / "docs" / "validation" / "entries").mkdir(parents=True)
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

    def test_discovery_uses_only_the_regular_filesystem_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            malformed = root / "docs" / "malformed.md"
            malformed.write_bytes(b"\xffnot markdown")
            (root / "docs" / "malformed" / "entries").mkdir(parents=True)
            missing_entries = root / "docs" / "missing.md"
            missing_entries.write_text("# Missing entries\n", encoding="utf-8")
            (root / "docs" / "missing").mkdir()
            linked_entries = root / "docs" / "linked" / "entries"
            linked_entries.parent.mkdir()
            linked_entries.symlink_to(malformed.with_suffix("") / "entries")
            linked_summary = root / "docs" / "linked.md"
            linked_summary.write_text("# Linked entries\n", encoding="utf-8")
            summary_link = root / "docs" / "summary-link.md"
            summary_link.symlink_to(summary)

            with mock.patch.object(
                Path,
                "open",
                side_effect=AssertionError("discovery opened Markdown"),
            ):
                discovered = discover_summaries(root)

            self.assertEqual(
                discovered["summaries"],
                sorted(
                    (
                        summary.resolve().as_posix(),
                        malformed.resolve().as_posix(),
                    )
                ),
            )

            validated = run_log(
                root,
                "validate",
                "run",
                "--root",
                str(root),
                "--dry-run",
                "--format",
                "json",
            )
            self.assertEqual(validated.returncode, 2)
            failures = [
                row
                for row in json.loads(validated.stdout)["rows"]
                if row["outcome"] == "error"
            ]
            self.assertTrue(
                any(
                    item["log"] == str(malformed.with_suffix("").resolve())
                    for item in failures
                )
            )

    def test_only_mechanical_validation_arguments_are_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            accepted = run_log(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
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
                        "run",
                        "--format",
                        "json",
                        "--path",
                        str(summary.with_suffix("")),
                        "--dry-run",
                        *cache_flags,
                    )
                    self.assertEqual(separated.returncode, 0, separated.stderr)
            for removed in ("--summary", "--date"):
                with self.subTest(removed=removed):
                    rejected = run_log(
                        root,
                        "validate",
                        "run",
                        "--format",
                        "json",
                        "--path",
                        str(summary.with_suffix("")),
                        removed,
                        "value",
                    )
                    self.assertNotEqual(rejected.returncode, 0)

    def test_cli_returns_zero_for_clear_and_finding_outcomes(self) -> None:
        for output_option, expected in (
            ("output-data", "clear"),
            ("results", "findings"),
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
                    "run",
                    "--format",
                    "json",
                    "--path",
                    str(summary.with_suffix("")),
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["outcome"], expected)
                self.assertEqual(result["schema"], "research-log-validation-run/1")
                self.assertNotIn("record", result)
                self.assertTrue(result["saved"])
                self.assertNotIn("generated", result)
                self.assertIn("next_command", result)

    def test_dry_run_returns_attempt_without_publishing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)

            completed = run_log(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--dry-run",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertFalse(result["saved"])
            self.assertNotIn("record", result)
            self.assertFalse(
                (summary.with_suffix("") / ".cache/results.sqlite").exists()
            )

    def test_entry_validation_preserves_the_published_full_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            full = run_log(root, "validate", "run", "--path", str(log))
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
                "run",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )

            self.assertEqual(entry.returncode, 0, entry.stderr)
            self.assertTrue(json.loads(entry.stdout)["saved"])
            # A completed entry is retained in its own slot without replacing
            # the full publication or its derived report.
            self.assertEqual(bundle[1], (log / "validation.md").read_bytes())
            full_rows = run_log(
                root,
                "validate",
                "show",
                "--path",
                str(log),
                "--format",
                "json",
            )
            self.assertEqual(full_rows.returncode, 0, full_rows.stderr)
            self.assertEqual(
                json.loads(full_rows.stdout)["rows"][0]["outcome"], "Clear"
            )
            entry_rows = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )
            self.assertEqual(entry_rows.returncode, 0, entry_rows.stderr)
            self.assertEqual(json.loads(entry_rows.stdout)["total"], 0)

    def test_entry_finding_dry_run_and_failure_preserve_full_snapshot(self) -> None:
        """Every nonpublishing entry outcome leaves the full snapshot intact."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry_document = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "run", "--path", str(log))
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
                "run",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
                "--dry-run",
            )
            self.assertEqual(finding.returncode, 0, finding.stderr)
            self.assertEqual(json.loads(finding.stdout)["outcome"], "findings")
            self.assertEqual(
                {path: (path.stat().st_ino, path.read_bytes()) for path in paths},
                original,
            )

    def test_entry_operational_error_preserves_a_published_full_snapshot(self) -> None:
        """An entry exit-2 error cannot replace authoritative full output."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "run", "--path", str(log))
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
                    "run",
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
                    "run",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )
            self.assertEqual(failed.returncode, 2, failed.stderr)
            self.assertEqual(
                json.loads(failed.stdout)["error"]["code"],
                "validation.source_changed",
            )
            self.assertEqual(
                {path: (path.stat().st_ino, path.read_bytes()) for path in paths},
                before,
            )

    def test_entry_source_change_is_operation_failure_without_replacing_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            published = run_log(root, "validate", "run", "--path", str(log))
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
                    "run",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                    "--format",
                    "json",
                )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["error"]["code"],
                "validation.source_changed",
            )
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
                        "run",
                        "--format",
                        "json",
                        "--root",
                        str(root),
                    ]
                    if dry_run:
                        arguments.append("--dry-run")

                    completed = run_log(root, *arguments)

                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertEqual(completed.stderr, "")
                    payload = json.loads(completed.stdout)
                    self.assertEqual(
                        payload["schema"], "research-log-validation-root-run/1"
                    )
                    self.assertEqual(len(payload["rows"]), 2)
                    by_log = {row["log"]: row for row in payload["rows"]}
                    self.assertEqual(
                        by_log[str(good_summary.with_suffix("").resolve())]["outcome"],
                        "clear",
                    )
                    failure = by_log[str(bad_summary.with_suffix("").resolve())]
                    self.assertEqual(failure["outcome"], "error")
                    self.assertEqual(failure["error"]["code"], "validation.failed")
                    self.assertIn(
                        "could not resolve project root", failure["error"]["message"]
                    )
                    self.assertEqual(payload["error_count"], 1)
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
            missing = root / "missing"

            completed = run_log(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--path",
                str(missing),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stderr, "")
            error = json.loads(completed.stdout)
            self.assertEqual(error["schema"], "research-log-validation-run/1")
            self.assertIn("missing", error["error"]["message"])
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_equals_option_forms_preserve_json_error_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            for arguments, schema in (
                (
                    (
                        "validate",
                        "run",
                        f"--path={log}",
                        "--entry=missing",
                        "--format=json",
                    ),
                    "research-log-validation-run/1",
                ),
                (
                    (
                        "validate",
                        "run",
                        f"--root={root}",
                        "--entry=e001",
                        "--format=json",
                    ),
                    "research-log-validation-root-run/1",
                ),
            ):
                with self.subTest(schema=schema):
                    result = run_log(root, *arguments)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout)["schema"], schema)

    def test_single_action_json_error_message_has_a_utf8_byte_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            # The public CLI layer handles typed action errors. Use one here so
            # the adversarial payload exercises the JSON failure envelope.
            from log_commands.model import ActionError

            with mock.patch(
                "log_commands.validation_cli.run_validation",
                side_effect=ActionError("validation.failed", "é" * 20_000),
            ):
                result = run_log(
                    root,
                    "validate",
                    "run",
                    "--path",
                    str(summary.with_suffix("")),
                    "--format=json",
                )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            message = json.loads(result.stdout)["error"]["message"]
            self.assertLessEqual(len(message.encode("utf-8")), 2_048)
            self.assertTrue(message.endswith("..."))

    def test_run_projects_the_snapshot_returned_under_the_controller_lock(self) -> None:
        controller = importlib.import_module("validation.controller")
        snapshots = importlib.import_module("validation.snapshot_storage")
        run_module = importlib.import_module("log_commands.validation_run")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = mechanical_log(root, output_option="results")
            log = summary.with_suffix("")
            pyrun_outputs = next(log.glob("entries/*/pyrun-outputs.json"))
            original_validate = controller.validate
            first_saved_at: list[str] = []

            def validate_then_replace(request):
                first = original_validate(request)
                first_saved_at.append(first.snapshot.stored_at)
                write(
                    entry,
                    entry.read_text(encoding="utf-8").replace(
                        "--results", "--output-data"
                    ),
                )
                write(
                    pyrun_outputs,
                    pyrun_outputs.read_text(encoding="utf-8").replace(
                        '"--results"', '"--output-data"'
                    ),
                )
                replacement = original_validate(request)
                self.assertFalse(replacement.snapshot.findings)
                return first

            with mock.patch.object(
                run_module, "validate", side_effect=validate_then_replace
            ):
                result = run_log(
                    root,
                    "validate",
                    "run",
                    "--path",
                    str(log),
                    "--format=json",
                )

            value = json.loads(result.stdout)
            current = snapshots.load_validation_snapshot(log)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(value["outcome"], "findings")
            self.assertEqual(value["saved_at"], first_saved_at[0])
            self.assertFalse(current.findings)
            self.assertNotEqual(current.stored_at, value["saved_at"])

    def test_root_run_isolates_a_post_commit_snapshot_read_failure(self) -> None:
        from research_log_result_store import ResultStoreError

        snapshots = importlib.import_module("validation.snapshot_storage")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a-bad").mkdir()
            (root / "z-good").mkdir()
            bad_summary, _ = mechanical_log(root / "a-bad")
            good_summary, _ = mechanical_log(root / "z-good")
            bad_log = bad_summary.with_suffix("").resolve()
            original_load = snapshots.load_validation_snapshot

            def fail_one(log_root, *, slot="full"):
                if log_root.resolve() == bad_log:
                    raise ResultStoreError(
                        "validation.store.malformed", "fixture committed read failure"
                    )
                return original_load(log_root, slot=slot)

            with mock.patch.object(
                snapshots, "load_validation_snapshot", side_effect=fail_one
            ):
                result = run_log(
                    root,
                    "validate",
                    "run",
                    "--root",
                    str(root),
                    "--format=json",
                )

            value = json.loads(result.stdout)
            rows = {row["log"]: row for row in value["rows"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(rows[str(bad_log)]["outcome"], "error")
            self.assertEqual(
                rows[str(bad_log)]["error"]["code"],
                "validation.report.render_failed",
            )
            self.assertEqual(
                rows[str(good_summary.with_suffix("").resolve())]["outcome"],
                "clear",
            )
            self.assertTrue(
                good_summary.with_suffix("")
                .joinpath(".cache/results.sqlite")
                .is_file()
            )

    def test_cli_generated_residue_is_a_published_orphan_finding(self) -> None:
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
                "run",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["outcome"], "findings")
            self.assertTrue(result["saved"])
            self.assertEqual(result["finding_counts_by_type"]["orphan"], 1)
            self.assertEqual(result["batch_count"], 1)
            self.assertNotIn("report", result)
            self.assertEqual(completed.stderr, "")
            self.assertTrue((summary.with_suffix("") / "validation.md").exists())

    def test_show_list_detail_and_render_share_one_saved_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            log = summary.with_suffix("")
            run = run_log(
                root, "validate", "run", "--path", str(log), "--format", "json"
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            run_value = json.loads(run.stdout)

            shown = run_log(
                root, "validate", "show", "--path", str(log), "--format", "json"
            )
            findings = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--path",
                str(log),
                "--type",
                "conformance",
                "--format",
                "json",
            )
            batches = run_log(
                root,
                "validate",
                "list",
                "batches",
                "--path",
                str(log),
                "--format",
                "json",
            )
            for result in (shown, findings, batches):
                self.assertEqual(result.returncode, 0, result.stderr)
            show_value = json.loads(shown.stdout)["rows"][0]
            finding_value = json.loads(findings.stdout)
            batch_value = json.loads(batches.stdout)
            self.assertEqual(
                show_value["finding_counts_by_type"],
                run_value["finding_counts_by_type"],
            )
            self.assertTrue(
                run_value["next_command"].startswith("log validate list batches")
            )
            self.assertNotIn(".cache", run_value["next_command"])
            self.assertNotIn("snapshot-", run_value["next_command"])
            self.assertEqual(show_value["batch_count"], run_value["batch_count"])
            self.assertEqual(finding_value["total"], 1)
            self.assertEqual(batch_value["total"], run_value["batch_count"])

            finding_id = finding_value["items"][0]["finding_id"]
            batch_id = finding_value["items"][0]["batch_id"]
            finding_detail = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--path",
                str(log),
                "--id",
                finding_id,
                "--format",
                "json",
            )
            batch_detail = run_log(
                root,
                "validate",
                "detail",
                "batch",
                "--path",
                str(log),
                "--id",
                batch_id,
                "--section",
                "findings",
                "--format",
                "json",
            )
            self.assertEqual(finding_detail.returncode, 0, finding_detail.stderr)
            self.assertEqual(batch_detail.returncode, 0, batch_detail.stderr)
            self.assertEqual(
                json.loads(finding_detail.stdout)["finding"]["finding_id"],
                finding_id,
            )
            self.assertEqual(
                json.loads(batch_detail.stdout)["items"][0]["finding_id"],
                finding_id,
            )

            original = (log / "validation.md").read_text(encoding="utf-8")
            (log / "validation.md").write_text("stale\n", encoding="utf-8")
            rendered = run_log(root, "validate", "render", "--path", str(log))
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertEqual(
                (log / "validation.md").read_text(encoding="utf-8"), original
            )

    def test_show_is_read_only_and_root_includes_unvalidated_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            first, _ = mechanical_log(root / "first")
            second, _ = mechanical_log(root / "second")
            first_log = first.with_suffix("")
            run = run_log(root, "validate", "run", "--path", str(first_log))
            self.assertEqual(run.returncode, 0, run.stderr)
            before = first_log.joinpath(".cache/results.sqlite").read_bytes()
            with mock.patch(
                "validation.engine.evaluate_mechanical",
                side_effect=AssertionError("show must not evaluate"),
            ):
                shown = run_log(
                    root,
                    "validate",
                    "show",
                    "--root",
                    str(root),
                    "--format",
                    "json",
                )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            value = json.loads(shown.stdout)
            rows = {row["log"]: row for row in value["rows"]}
            self.assertEqual(rows[str(first_log.resolve())]["outcome"], "Clear")
            self.assertEqual(
                rows[str(second.with_suffix("").resolve())]["outcome"],
                "Not validated",
            )
            self.assertTrue(value["totals_partial"])
            self.assertEqual(value["contributing_log_count"], 1)
            self.assertEqual(
                first_log.joinpath(".cache/results.sqlite").read_bytes(), before
            )

    def test_root_text_summaries_use_compact_labels_and_keep_json_exact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")

            root_run = run_log(root, "validate", "run", "--root", str(root))
            root_show = run_log(root, "validate", "show", "--root", str(root))
            single_show = run_log(root, "validate", "show", "--path", str(log))
            structured_show = run_log(
                root,
                "validate",
                "show",
                "--root",
                str(root),
                "--format",
                "json",
            )

            for result in (root_run, root_show, single_show, structured_show):
                self.assertEqual(result.returncode, 0, result.stderr)
            resolved = str(log.resolve())
            for result in (root_run, root_show):
                self.assertTrue(
                    any(
                        line.startswith(f"{log.name} | ")
                        for line in result.stdout.splitlines()
                    )
                )
                self.assertNotIn(resolved, result.stdout)
                self.assertRegex(
                    result.stdout,
                    rf"(?m)^{log.name} \| [A-Z][a-z]{{2}} \d{{1,2}} \| Clear \|",
                )
            self.assertIn(resolved, single_show.stdout)
            structured_row = json.loads(structured_show.stdout)["rows"][0]
            self.assertEqual(structured_row["log"], resolved)
            self.assertNotIn("blocked_check_count", structured_row)
            self.assertNotIn("failed_check_count", structured_row)
            self.assertIn("Blocked: 0", root_show.stdout)
            self.assertIn("Failed: 0", root_show.stdout)
            self.assertIn("Blocked", single_show.stdout)
            self.assertIn("Failed", single_show.stdout)
            self.assertNotIn(structured_row["saved_at"], root_show.stdout)
            self.assertRegex(
                single_show.stdout,
                r"(?m)^\| Saved \| [A-Z][a-z]{2} \d{1,2} \|$",
            )

    def test_removed_cli_spellings_have_no_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            for arguments in (
                ("results",),
                ("findings",),
                ("repair-check",),
                ("validate", "--path", str(log)),
            ):
                with self.subTest(arguments=arguments):
                    result = run_log(root, *arguments)
                    self.assertEqual(result.returncode, 2)

    def test_validation_parser_rejects_conflicting_or_invalid_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            self.assertEqual(
                run_log(
                    root,
                    "validate",
                    "run",
                    "--path",
                    str(log),
                    "--root",
                    str(root),
                ).returncode,
                2,
            )
            self.assertEqual(
                run_log(
                    root,
                    "validate",
                    "show",
                    "--path",
                    str(log),
                    "--entry",
                    "e001",
                ).returncode,
                2,
            )
            self.assertEqual(
                run_log(
                    root,
                    "validate",
                    "list",
                    "findings",
                    "--path",
                    str(log),
                    "--type",
                    "structure",
                ).returncode,
                2,
            )
            self.assertEqual(
                run_log(
                    root,
                    "validate",
                    "render",
                    "--path",
                    str(log),
                    "--format",
                    "json",
                ).returncode,
                2,
            )
            invalid_json = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--path",
                str(log),
                "--type",
                "structure",
                "--format",
                "json",
            )
            self.assertEqual(invalid_json.returncode, 2)
            self.assertEqual(invalid_json.stderr, "")
            invalid_value = json.loads(invalid_json.stdout)
            self.assertEqual(
                invalid_value["schema"],
                "research-log-validation-finding-list/1",
            )
            self.assertEqual(
                invalid_value["error"]["code"], "validation.arguments.invalid"
            )
            self.assertIn("invalid choice", invalid_value["error"]["message"])
            self.assertIn("conformance", invalid_value["error"]["message"])
            self.assertEqual(
                run_log(root, "validate", "run", "--path", str(log)).returncode,
                0,
            )
            for option, value, code in (
                ("--limit", "0", "validation.limit.invalid"),
                ("--limit", "101", "validation.limit.invalid"),
                ("--cursor", "invalid", "validation.cursor.invalid"),
            ):
                result = run_log(
                    root,
                    "validate",
                    "list",
                    "findings",
                    "--path",
                    str(log),
                    option,
                    value,
                    "--format",
                    "json",
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], code)

    def test_text_show_renders_the_same_summary_counts_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            log = summary.with_suffix("")
            self.assertEqual(
                run_log(root, "validate", "run", "--path", str(log)).returncode,
                0,
            )
            text = run_log(root, "validate", "show", "--path", str(log))
            data = run_log(
                root, "validate", "show", "--path", str(log), "--format", "json"
            )
            self.assertEqual(text.returncode, 0, text.stderr)
            row = json.loads(data.stdout)["rows"][0]
            self.assertIn("Conformance", text.stdout)
            self.assertIn("Blocked", text.stdout)
            self.assertIn("Failed", text.stdout)
            self.assertIn("Batches", text.stdout)
            self.assertIn(
                str(row["finding_counts_by_type"]["conformance"]), text.stdout
            )
            self.assertIn(str(row["batch_count"]), text.stdout)
            self.assertIn(str(row["blocked_check_count"]), text.stdout)
            self.assertIn(str(row["failed_check_count"]), text.stdout)

    def test_text_and_json_share_run_list_and_detail_projections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            log = summary.with_suffix("")
            structured_run = run_log(
                root,
                "validate",
                "run",
                "--path",
                str(log),
                "--format",
                "json",
            )
            text_run = run_log(root, "validate", "run", "--path", str(log))
            self.assertEqual(structured_run.returncode, 0, structured_run.stderr)
            self.assertEqual(text_run.returncode, 0, text_run.stderr)
            run_value = json.loads(structured_run.stdout)
            self.assertIn("Outcome: Findings", text_run.stdout)
            self.assertIn(f"Batches: {run_value['batch_count']}", text_run.stdout)
            self.assertIn(
                f"Blocked: {run_value['blocked_check_count']}",
                text_run.stdout,
            )
            self.assertIn(
                f"Failed: {run_value['failed_check_count']}",
                text_run.stdout,
            )
            for finding_type, count in run_value["finding_counts_by_type"].items():
                self.assertIn(f"{finding_type}={count}", text_run.stdout)

            finding_json = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--path",
                str(log),
                "--format",
                "json",
            )
            finding_text = run_log(
                root, "validate", "list", "findings", "--path", str(log)
            )
            batch_json = run_log(
                root,
                "validate",
                "list",
                "batches",
                "--path",
                str(log),
                "--format",
                "json",
            )
            batch_text = run_log(
                root, "validate", "list", "batches", "--path", str(log)
            )
            blocked_json = run_log(
                root,
                "validate",
                "list",
                "blocked",
                "--path",
                str(log),
                "--format",
                "json",
            )
            blocked_text = run_log(
                root, "validate", "list", "blocked", "--path", str(log)
            )
            finding_value = json.loads(finding_json.stdout)
            batch_value = json.loads(batch_json.stdout)
            blocked_value = json.loads(blocked_json.stdout)
            for result in (
                finding_json,
                finding_text,
                batch_json,
                batch_text,
                blocked_json,
                blocked_text,
            ):
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Total: {finding_value['total']}", finding_text.stdout)
            self.assertIn(f"Total: {batch_value['total']}", batch_text.stdout)
            self.assertIn(
                f"Total: {blocked_value['total']}",
                blocked_text.stdout,
            )
            if blocked_value["items"]:
                blocked_rule = blocked_value["items"][0]["rule"]
                self.assertIn(blocked_rule, blocked_text.stdout)
            finding_id = finding_value["items"][0]["finding_id"]
            batch_id = finding_value["items"][0]["batch_id"]
            self.assertIn(finding_id, finding_text.stdout)
            self.assertIn(batch_id, batch_text.stdout)

            root_text = run_log(root, "validate", "run", "--root", str(root))
            self.assertEqual(root_text.returncode, 0, root_text.stderr)
            self.assertIn(
                f"Blocked: {run_value['blocked_check_count']}", root_text.stdout
            )
            self.assertIn(
                f"Failed: {run_value['failed_check_count']}", root_text.stdout
            )
            self.assertIn("Next:", root_text.stdout)
            self.assertIn("log validate list batches", root_text.stdout)

            finding_detail_json = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--path",
                str(log),
                "--id",
                finding_id,
                "--format",
                "json",
            )
            finding_detail_text = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--path",
                str(log),
                "--id",
                finding_id,
            )
            batch_detail_json = run_log(
                root,
                "validate",
                "detail",
                "batch",
                "--path",
                str(log),
                "--id",
                batch_id,
                "--format",
                "json",
            )
            batch_detail_text = run_log(
                root,
                "validate",
                "detail",
                "batch",
                "--path",
                str(log),
                "--id",
                batch_id,
            )
            for structured, text_result in (
                (finding_detail_json, finding_detail_text),
                (batch_detail_json, batch_detail_text),
            ):
                self.assertEqual(structured.returncode, 0, structured.stderr)
                self.assertEqual(text_result.returncode, 0, text_result.stderr)
                detail = json.loads(structured.stdout)
                self.assertIn(detail["section"], text_result.stdout)
            self.assertIn(finding_id, finding_detail_text.stdout)
            self.assertIn(batch_id, batch_detail_text.stdout)
            self.assertIn("Issue: ", finding_detail_text.stdout)
            self.assertIn("Explanation: ", finding_detail_text.stdout)
            self.assertIn("Diagnostic:", finding_detail_text.stdout)
            finding_payload = json.loads(finding_detail_json.stdout)["finding"]
            self.assertEqual(
                set(finding_payload["diagnosis"]), {"explanation", "title"}
            )
            for name, item in finding_payload["observed"].items():
                self.assertIn(
                    name.replace("_", " ").title(), finding_detail_text.stdout
                )

    def test_batch_list_text_exposes_residual_orphan_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = mechanical_log(root)
            log = summary.with_suffix("")
            write(entry.parent / "orphan-a.txt", "a\n")
            write(entry.parent / "orphan-b.txt", "b\n")

            validated = run_log(root, "validate", "run", "--path", str(log))
            structured = run_log(
                root,
                "validate",
                "list",
                "batches",
                "--path",
                str(log),
                "--format",
                "json",
            )
            rendered = run_log(
                root, "validate", "list", "batches", "--path", str(log)
            )

            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(structured.returncode, 0, structured.stderr)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            fallback = next(
                item
                for item in json.loads(structured.stdout)["items"]
                if item["rationale"]
                == ["orphan-singletons:e001:orphan.material.unused"]
            )
            self.assertIn(fallback["batch_id"], rendered.stdout)
            self.assertIn(
                "rationale=orphan-singletons:e001:orphan.material.unused",
                rendered.stdout,
            )


if __name__ == "__main__":
    unittest.main()
