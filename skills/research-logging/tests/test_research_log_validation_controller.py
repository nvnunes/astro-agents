from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

from research_log_validation_test_support import (
    mechanical_log,
    mock,
    unittest,
    write,
)

CONTROLLER = importlib.import_module("validation.controller")
ENGINE = importlib.import_module("validation.engine")
LOCATOR = importlib.import_module("validation.locator")
RECORDS = importlib.import_module("validation.records")


_log = mechanical_log


class MechanicalControllerTests(unittest.TestCase):
    def test_completed_result_publishes_public_bundle_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            summary_bytes = summary.read_bytes()

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            log_root = summary.with_suffix("")
            record = json.loads(
                (log_root / "validation" / "mechanical.json").read_text()
            )
            cache = json.loads(
                (log_root / "validation" / ".cache" / "mechanical.json").read_text()
            )
            report = (log_root / "validation.md").read_text()
            self.assertEqual(result["status"], "complete_clear")
            self.assertTrue(result["published"])
            self.assertEqual(record["schema"], "research-log-mechanical/1")
            self.assertEqual(cache["schema"], "research-log-mechanical-cache/1")
            self.assertIn("## Mechanical Validation", report)
            self.assertIn("## Reproduction", report)
            self.assertIn("Status: `not_yet_run`", report)
            self.assertIn("### Counts", report)
            for check in record["checks"]:
                if check["status"] == "pass":
                    self.assertNotIn(check["identity"], report)
            self.assertEqual(summary.read_bytes(), summary_bytes)
            self.assertEqual(
                sorted(
                    path.relative_to(log_root / "validation").as_posix()
                    for path in (log_root / "validation").rglob("*")
                    if path.is_file()
                ),
                [".cache/lock", ".cache/mechanical.json", "mechanical.json"],
            )

    def test_findings_are_complete_and_grouped_without_passing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory), output_option="results")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            report = (summary.with_suffix("") / "validation.md").read_text()
            self.assertEqual(result["status"], "complete_findings")
            self.assertIn("#### e001", report)
            self.assertIn("`producer.missing`", report)
            self.assertIn("Observed:", report)
            self.assertIn("Violated rule:", report)
            for check in result["record"]["checks"]:
                if check["status"] == "pass":
                    self.assertNotIn(check["identity"], report)
                else:
                    failure = check["failure"]
                    self.assertIn(f"`{failure['code']}`", report)
                    self.assertIn(f"`{failure['subject']}`", report)

    def test_report_renders_the_cause_of_dependent_not_applicable_checks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"][0]["sources"][0]["source"] = "data/missing.csv"
            write(evidence_path, json.dumps(evidence) + "\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            report = (summary.with_suffix("") / "validation.md").read_text()
            dependent = next(
                check
                for check in result["record"]["checks"]
                if check["identity"] == "provenance:e001:success-rate"
            )
            self.assertEqual(dependent["status"], "not_applicable")
            self.assertEqual(
                dependent["dependencies"],
                [{"dependency": "evidence:e001:success-rate"}],
            )
            self.assertIn("`provenance:e001:success-rate`", report)
            self.assertIn(
                'Dependencies: `[{"dependency":"evidence:e001:success-rate"}]`',
                report,
            )

    def test_dry_run_and_incomplete_evaluation_publish_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary, result_date="2026-08-29", publish=False
                )
            )
            self.assertEqual(result["status"], "complete_clear")
            self.assertFalse(result["published"])
            self.assertFalse((summary.with_suffix("") / "validation").exists())
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_malformed_v2_is_a_completed_conformance_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "evidence.json", "{\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            self.assertEqual(result["status"], "complete_findings")
            failures = [
                check["failure"]["code"]
                for check in result["record"]["checks"]
                if "failure" in check
            ]
            self.assertIn("evidence.json.schema_invalid", failures)
            self.assertTrue(result["published"])
            self.assertTrue(
                (summary.with_suffix("") / "validation/mechanical.json").is_file()
            )

    def test_invalid_date_and_worker_bound_are_operational_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError, "YYYY-MM-DD"
            ):
                CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-8-29")
                )
            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError, "positive integer"
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(summary, jobs=0))
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_invalid_cache_recomputes_without_changing_the_result(self) -> None:
        invalid_caches = (
            b"{\n",
            b'{"checks":{},"rules_version":"old","schema":'
            b'"research-log-mechanical-cache/1"}\n',
            b'{"checks":{},"rules_version":"research-log-evidence/v2-initial",'
            b'"schema":"unsupported"}\n',
        )
        for invalid in invalid_caches:
            with self.subTest(cache=invalid), tempfile.TemporaryDirectory() as (
                directory
            ):
                summary, _ = _log(Path(directory))
                first = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )
                cache_path = (
                    summary.with_suffix("") / "validation/.cache/mechanical.json"
                )
                cache_path.write_bytes(invalid)

                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )

                self.assertEqual(first["status"], "complete_clear")
                self.assertEqual(result["status"], "complete_clear")
                self.assertEqual(result["metrics"]["checks_reused"], 0)
                self.assertEqual(
                    json.loads(cache_path.read_text())["schema"],
                    "research-log-mechanical-cache/1",
                )

    def test_unchanged_public_validation_reuses_the_published_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(
                summary, result_date="2026-08-29"
            )
            first = CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/mechanical.json",
                log_root / "validation/.cache/mechanical.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            second = CONTROLLER.validate(request)

            self.assertEqual(first["status"], "complete_clear")
            self.assertEqual(second["status"], "complete_clear")
            self.assertGreater(second["metrics"]["checks_reused"], 0)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_recompute_bypasses_cache_and_publishes_rebuilt_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            ordinary = CONTROLLER.ValidationRequest(
                summary, result_date="2026-08-29"
            )
            CONTROLLER.validate(ordinary)
            reused = CONTROLLER.validate(ordinary)
            self.assertGreater(reused["metrics"]["checks_reused"], 0)

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    result_date="2026-08-29",
                    recompute=True,
                )
            )

            cache_path = (
                summary.with_suffix("") / "validation/.cache/mechanical.json"
            )
            self.assertEqual(recomputed["status"], "complete_clear")
            self.assertTrue(recomputed["published"])
            self.assertEqual(recomputed["metrics"]["checks_reused"], 0)
            self.assertEqual(
                json.loads(cache_path.read_text())["schema"],
                "research-log-mechanical-cache/1",
            )

    def test_recompute_dry_run_neither_reads_cache_nor_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/mechanical.json",
                log_root / "validation/.cache/mechanical.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with mock.patch.object(
                CONTROLLER,
                "_load_cache",
                side_effect=AssertionError("recompute must not read the cache"),
            ):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(
                        summary,
                        result_date="2026-08-29",
                        publish=False,
                        recompute=True,
                    )
                )

            self.assertEqual(result["status"], "complete_clear")
            self.assertFalse(result["published"])
            self.assertEqual(result["metrics"]["checks_reused"], 0)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_oversized_cache_is_ignored_and_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(
                summary, result_date="2026-08-29"
            )
            CONTROLLER.validate(request)
            cache_path = (
                summary.with_suffix("") / "validation/.cache/mechanical.json"
            )
            self.assertGreater(cache_path.stat().st_size, 1)

            with mock.patch.object(CONTROLLER, "MAX_CACHE_BYTES", 1):
                result = CONTROLLER.validate(request)

            self.assertEqual(result["status"], "complete_clear")
            self.assertEqual(result["metrics"]["checks_reused"], 0)
            self.assertEqual(
                json.loads(cache_path.read_text())["schema"],
                "research-log-mechanical-cache/1",
            )

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            unavailable = LOCATOR.LocatorV2Error(
                "locator.reader.unavailable",
                "data/results.csv",
                {"error": "unavailable"},
                "V2 Expanded Mechanical Locator Language",
                outcome="unavailable",
            )
            with mock.patch.object(ENGINE, "observe_source", side_effect=unavailable):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["published"])
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_upgrade_preflight_reports_both_conditions_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            log_root = summary.with_suffix("")
            write(entry.parent / "evidence.csv", "entry\n")
            write(log_root / "validation" / "manifest.json", "{\"schema_version\":2}\n")
            write(log_root / "validation.md", "legacy report\n")
            before = {
                path: path.read_bytes()
                for path in (
                    entry.parent / "evidence.csv",
                    log_root / "validation" / "manifest.json",
                    log_root / "validation.md",
                )
            }

            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(result["status"], "upgrade_required")
            self.assertEqual(result["code"], "validation.upgrade_required")
            self.assertEqual(
                result["observed"]["evidence_csv"],
                ["entries/2026-08-29-e001-study/evidence.csv"],
            )
            self.assertIn(
                "validation/manifest.json",
                result["observed"]["legacy_generated_state"],
            )
            self.assertFalse(result["published"])
            self.assertFalse((log_root / "validation" / "mechanical.json").exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_upgrade_preflight_reports_each_condition_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "evidence.csv", "entry\n")
            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            self.assertTrue(result["observed"]["evidence_csv"])
            self.assertFalse(result["observed"]["legacy_generated_state"])

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(summary.with_suffix("") / "validation" / "manifest.json", "{}\n")
            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            self.assertFalse(result["observed"]["evidence_csv"])
            self.assertTrue(result["observed"]["legacy_generated_state"])

    def test_each_exact_legacy_path_is_recognized_without_decoding(self) -> None:
        for relative in CONTROLLER.LEGACY_PATHS:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as (
                directory
            ):
                summary, _ = _log(Path(directory))
                log_root = summary.with_suffix("")
                path = log_root / relative
                write(path, "not valid legacy content\n")
                before = path.read_bytes()

                result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

                self.assertEqual(result["status"], "upgrade_required")
                self.assertEqual(
                    result["observed"]["legacy_generated_state"], [relative]
                )
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(
                    (log_root / "validation/mechanical.json").exists()
                )

    def test_unrecognized_validation_file_does_not_trigger_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(summary.with_suffix("") / "validation/unrelated.json", "{}\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            self.assertEqual(result["status"], "complete_clear")
            self.assertTrue(result["published"])

    def test_legacy_report_marker_is_a_precise_cutover_condition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            report = log_root / "validation.md"
            write(
                report,
                "# Validation\n\n"
                "| Entry | Date | Checked | Reproducibility |\n"
                "| --- | --- | --- | --- |\n",
            )
            before = report.read_bytes()

            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(result["status"], "upgrade_required")
            self.assertEqual(
                result["observed"]["legacy_generated_state"], ["validation.md"]
            )
            self.assertEqual(report.read_bytes(), before)
            self.assertFalse((log_root / "validation/mechanical.json").exists())

    def test_cutover_inventory_is_bounded_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "evidence.csv", "entry\n")
            write(entry.parent.parent / "evidence.csv", "entry\n")

            with mock.patch.object(CONTROLLER, "MAX_UPGRADE_PATHS", 1):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "legacy evidence inventory exceeds 1 paths",
                ):
                    CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertFalse(
                (summary.with_suffix("") / "validation/mechanical.json").exists()
            )

    def test_pending_upgrade_transaction_blocks_and_preserves_prior_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            log_root = summary.with_suffix("")
            transaction = (
                log_root
                / "validation/.cache/upgrade-transactions/transaction/transaction.json"
            )
            write(transaction, "{}\n")
            tracked = (
                log_root / "validation/mechanical.json",
                log_root / "validation/.cache/mechanical.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError,
                "upgrade.recovery.required",
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_dangling_upgrade_transaction_symlink_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            pending = (
                summary.with_suffix("")
                / "validation/.cache/upgrade-transactions"
            )
            pending.parent.mkdir(parents=True)
            pending.symlink_to("missing-transaction-directory")

            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError,
                "upgrade.recovery.required",
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertFalse(
                (summary.with_suffix("") / "validation/mechanical.json").exists()
            )

    def test_summary_symlink_is_rejected_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            link = summary.with_name("linked.md")
            link.symlink_to(summary.name)

            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError, "must not be a symlink"
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(link))

    def test_publication_error_restores_prior_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            old = {
                "validation/mechanical.json": b"old record\n",
                "validation/.cache/mechanical.json": b"old cache\n",
                "validation.md": b"old report\n",
            }
            for relative, payload in old.items():
                path = log_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            original = RECORDS._atomic_write_bytes
            calls = 0

            def fail_second(path: Path, payload: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fixture failure")
                original(path, payload)

            with mock.patch.object(
                RECORDS, "_atomic_write_bytes", side_effect=fail_second
            ):
                with self.assertRaises(RECORDS.RecordPublicationError):
                    RECORDS.publish_validation_outputs(
                        log_root,
                        {relative: b"new\n" for relative in old},
                    )
            for relative, payload in old.items():
                self.assertEqual((log_root / relative).read_bytes(), payload)

    def test_publication_lock_rejects_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            with RECORDS.validation_lock(log_root):
                with self.assertRaisesRegex(
                    RECORDS.RecordPublicationError,
                    "another validation writer",
                ):
                    RECORDS.publish_validation_outputs(
                        log_root,
                        {"validation/mechanical.json": b"new record\n"},
                    )
            self.assertFalse((log_root / "validation/mechanical.json").exists())

    def test_symlinked_publication_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_root = root / "log"
            external = root / "external"
            external.mkdir()
            log_root.mkdir()
            (log_root / "validation").symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(
                RECORDS.RecordPublicationError,
                "must not contain a symlink",
            ):
                RECORDS.publish_validation_outputs(
                    log_root,
                    {"validation/mechanical.json": b"new record\n"},
                )

            self.assertFalse((external / "mechanical.json").exists())

    def test_engine_operational_error_preserves_prior_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/mechanical.json",
                log_root / "validation/.cache/mechanical.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with mock.patch.object(
                CONTROLLER, "evaluate_mechanical", side_effect=OSError("fixture")
            ):
                with self.assertRaisesRegex(OSError, "fixture"):
                    CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_cutover_change_during_publication_restores_prior_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(
                summary, result_date="2026-08-29"
            )
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/mechanical.json",
                log_root / "validation/.cache/mechanical.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}
            original = RECORDS._atomic_write_bytes
            introduced = False

            def introduce_v1(path: Path, payload: bytes) -> None:
                nonlocal introduced
                original(path, payload)
                if path.name == "validation.md" and not introduced:
                    introduced = True
                    write(entry.parent / "evidence.csv", "entry\n")

            with mock.patch.object(
                RECORDS, "_atomic_write_bytes", side_effect=introduce_v1
            ):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "became upgrade-required",
                ):
                    CONTROLLER.validate(request)

            self.assertTrue((entry.parent / "evidence.csv").is_file())
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)


if __name__ == "__main__":
    unittest.main()
