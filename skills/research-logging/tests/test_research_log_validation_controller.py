from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from research_log_validation_test_support import (
    mechanical_log,
    mock,
    unittest,
    write,
)

CONTROLLER = importlib.import_module("validation.controller")
DATA = importlib.import_module("research_log_data")
ENGINE = importlib.import_module("validation.engine")
FILESYSTEM = importlib.import_module("validation.filesystem")
FINGERPRINT_CACHE = importlib.import_module("validation.fingerprint_cache")
LOCATOR = importlib.import_module("validation.locator")
OPERATION_STATE = importlib.import_module("validation.operation_state")
RECORDS = importlib.import_module("validation.records")
REPORT = importlib.import_module("validation.report")
RESULTS = importlib.import_module("validation.mechanical_results")
VALIDATION_CACHE = importlib.import_module("validation.validation_cache")


_log = mechanical_log


def _cache_path(summary: Path) -> Path:
    return summary.with_suffix("") / ".cache" / VALIDATION_CACHE.CACHE_FILENAME


def _cache_rows(path: Path, table: str) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class MechanicalControllerTests(unittest.TestCase):
    def test_report_leaves_zero_check_scope_status_blank(self) -> None:
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    "conformance:log",
                    RESULTS.CheckScope.CONFORMANCE,
                    RESULTS.CheckStatus.FAIL,
                    "summary",
                    failure=RESULTS.FailurePayload(
                        "association.declaration_missing",
                        "summary",
                        {"entries": 0},
                        "Evidence Files And Unsupported Metadata",
                    ),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn("| structure | checks | `fail` | 0 | 1 | 0 | 0 | 1 |", report)
        self.assertNotIn("| conformance | checks |", report)
        self.assertIn("| evidence | checks |  | 0 | 0 | 0 | 0 | 0 |", report)
        self.assertNotIn("| evidence | checks | `not_applicable`", report)

    def test_report_counts_unique_provenance_artifacts_not_evidence_checks(
        self,
    ) -> None:
        artifact = "/project/data/result.csv"
        second = "/project/data/second.csv"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    "provenance:e001:first",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.PASS,
                    "first",
                    ({"artifacts": [artifact]},),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:second",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    "second",
                    ({"artifacts": [artifact]},),
                    RESULTS.FailurePayload(
                        "producer.missing",
                        "second",
                        {"material": artifact},
                        "Provenance Starting Points And Traversal",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:third",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.PASS,
                    "third",
                    ({"artifacts": [second]},),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:summary:1",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    "summary:1",
                    ({"target": "provenance:e001:second"},),
                    RESULTS.FailurePayload(
                        "summary.reference.target_invalid",
                        "summary:1",
                        {"target_status": "fail"},
                        "Summary Association",
                    ),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `fail` | 1 | 1 | 0 | 0 | 2 |",
            report,
        )

    def test_batch_report_composes_ready_to_present_shared_scope_counts(
        self,
    ) -> None:
        failed_artifact = "/project/data/failed.csv"
        unconfirmed_artifact = "/project/data/unconfirmed.csv"
        checks = (
            RESULTS.MechanicalCheck(
                "conformance:log",
                RESULTS.CheckScope.CONFORMANCE,
                RESULTS.CheckStatus.FAIL,
                "summary",
                failure=RESULTS.FailurePayload(
                    "summary.invalid", "summary", {}, "Summary"
                ),
            ),
            RESULTS.MechanicalCheck(
                "evidence:e001:pass",
                RESULTS.CheckScope.EVIDENCE,
                RESULTS.CheckStatus.PASS,
                "pass",
            ),
            RESULTS.MechanicalCheck(
                "evidence:e001:fail",
                RESULTS.CheckScope.EVIDENCE,
                RESULTS.CheckStatus.FAIL,
                "fail",
                failure=RESULTS.FailurePayload(
                    "evidence.invalid", "fail", {}, "Evidence"
                ),
            ),
            RESULTS.MechanicalCheck(
                "provenance:e001:failed",
                RESULTS.CheckScope.PROVENANCE,
                RESULTS.CheckStatus.FAIL,
                failed_artifact,
                ({"artifacts": [failed_artifact]},),
                RESULTS.FailurePayload(
                    "producer.missing",
                    failed_artifact,
                    {},
                    "Provenance",
                ),
            ),
            RESULTS.MechanicalCheck(
                "provenance:e001:unconfirmed",
                RESULTS.CheckScope.PROVENANCE,
                RESULTS.CheckStatus.FAIL,
                unconfirmed_artifact,
                ({"artifacts": [unconfirmed_artifact]},),
                RESULTS.FailurePayload(
                    "provenance.output.unconfirmed",
                    unconfirmed_artifact,
                    {},
                    "Provenance",
                ),
            ),
            *(
                RESULTS.MechanicalCheck(
                    f"orphan:e001:{number}",
                    RESULTS.CheckScope.ORPHAN,
                    RESULTS.CheckStatus.FAIL,
                    f"orphan-{number}",
                    failure=RESULTS.FailurePayload(
                        "orphan.output",
                        f"orphan-{number}",
                        {},
                        "Hygiene",
                    ),
                )
                for number in range(2)
            ),
        )
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md", "test-rules", "2026-08-30", checks
        )
        row = REPORT.ValidationBatchReportRow(
            "Study | One",
            "/project/docs/study.md",
            "/project/docs/study/validation.md",
            "/project/docs/study/validation/results.json",
            True,
            record,
        )

        report = REPORT.compose_validation_batch_report((row,))

        self.assertIn(
            "| [Study \\| One](</project/docs/study.md>) | 1 | 1/2 | "
            "1 failed · 1 unconfirmed | 2 | "
            "[Human](</project/docs/study/validation.md>) · "
            "[JSON](</project/docs/study/validation/results.json>) |",
            report,
        )

    def test_report_counts_unconfirmed_output_as_unavailable_artifact(self) -> None:
        artifact = "/project/data/migrated.csv"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    "provenance:e001:migrated",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    artifact,
                    ({"artifacts": [artifact]},),
                    RESULTS.FailurePayload(
                        "provenance.output.unconfirmed",
                        artifact,
                        {"output": "data/migrated.csv"},
                        "Mechanical Validation Evaluation And Outcomes",
                    ),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `unavailable` | 0 | 0 | 1 | 0 | 1 |",
            report,
        )

    def test_report_prefers_actual_failure_over_unconfirmed_output(self) -> None:
        artifact = "/project/data/migrated.csv"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    "provenance:e001:migrated",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    artifact,
                    ({"artifacts": [artifact]},),
                    RESULTS.FailurePayload(
                        "provenance.output.unconfirmed",
                        artifact,
                        {"output": "data/migrated.csv"},
                        "Mechanical Validation Evaluation And Outcomes",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:missing-producer",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    artifact,
                    ({"artifacts": [artifact]},),
                    RESULTS.FailurePayload(
                        "producer.missing",
                        artifact,
                        {"material": artifact},
                        "Provenance Starting Points And Traversal",
                    ),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `fail` | 0 | 1 | 0 | 0 | 1 |",
            report,
        )

    def test_report_status_prefers_failed_artifact_over_distinct_unconfirmed(
        self,
    ) -> None:
        failed = "/project/data/failed.csv"
        unconfirmed = "/project/data/unconfirmed.csv"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    "provenance:e001:failed",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    failed,
                    ({"artifacts": [failed]},),
                    RESULTS.FailurePayload(
                        "producer.missing",
                        failed,
                        {"material": failed},
                        "Provenance Starting Points And Traversal",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:unconfirmed",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    unconfirmed,
                    ({"artifacts": [unconfirmed]},),
                    RESULTS.FailurePayload(
                        "provenance.output.unconfirmed",
                        unconfirmed,
                        {"output": "data/unconfirmed.csv"},
                        "Mechanical Validation Evaluation And Outcomes",
                    ),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `fail` | 0 | 1 | 1 | 0 | 2 |",
            report,
        )

    def test_report_counts_artifact_blocked_by_provenance_failure_as_failed(
        self,
    ) -> None:
        artifact = "/project/data/downstream.csv"
        declaration = "entry:e001:input:catalog-declaration"
        command = "entry:e001:command:1:1"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    declaration,
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    "catalog",
                    failure=RESULTS.FailurePayload(
                        "data.fingerprint.mismatch",
                        "catalog",
                        {"expected": "old", "observed": "current"},
                        "Fingerprints",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    command,
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.NOT_APPLICABLE,
                    command,
                    ({"dependency": declaration},),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:downstream",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.NOT_APPLICABLE,
                    artifact,
                    ({"artifacts": [artifact]}, {"dependency": command}),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `fail` | 0 | 1 | 0 | 0 | 1 |",
            report,
        )

    def test_report_keeps_artifact_blocked_by_other_scope_not_applicable(
        self,
    ) -> None:
        artifact = "/project/data/downstream.csv"
        evidence = "evidence:e001:downstream"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/study.md",
            "test-rules",
            "2026-08-30",
            (
                RESULTS.MechanicalCheck(
                    evidence,
                    RESULTS.CheckScope.EVIDENCE,
                    RESULTS.CheckStatus.FAIL,
                    artifact,
                    failure=RESULTS.FailurePayload(
                        "evidence.value.mismatch",
                        artifact,
                        {"expected": 1, "observed": 2},
                        "Evidence Values",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:downstream",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.NOT_APPLICABLE,
                    artifact,
                    ({"artifacts": [artifact]}, {"dependency": evidence}),
                ),
            ),
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn(
            "| provenance | artifacts | `not_applicable` | 0 | 0 | 0 | 1 | 1 |",
            report,
        )

    def test_completed_result_publishes_public_bundle_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            summary_bytes = summary.read_bytes()

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            log_root = summary.with_suffix("")
            record = json.loads(
                (log_root / "validation" / "results.json").read_text()
            )
            cache_path = _cache_path(summary)
            report = (log_root / "validation.md").read_text()
            self.assertEqual(result["status"], "complete_clear")
            self.assertTrue(result["published"])
            self.assertGreaterEqual(
                result["metrics"]["validation_cache_sqlite_writes"], 4
            )
            self.assertEqual(record["schema"], "research-log-mechanical/1")
            self.assertTrue(cache_path.is_file())
            with closing(sqlite3.connect(cache_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    VALIDATION_CACHE.CACHE_SCHEMA_VERSION,
                )
            self.assertGreater(_cache_rows(cache_path, "check_comparison"), 0)
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)
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
                ["results.json"],
            )
            cache_names = {path.name for path in (log_root / ".cache").iterdir()}
            self.assertIn(VALIDATION_CACHE.CACHE_FILENAME, cache_names)
            self.assertNotIn("research-log-validation.lock", cache_names)
            self.assertIn("research-log-operations", cache_names)

    def test_findings_are_complete_and_grouped_without_passing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory), output_option="results")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            report = (summary.with_suffix("") / "validation.md").read_text()
            self.assertEqual(result["status"], "complete_findings")
            self.assertIn("#### e001", report)
            self.assertIn("`material.candidate.unresolved`", report)
            self.assertIn("Status: `not_applicable`", report)
            self.assertIn("Dependencies:", report)
            self.assertIn("Observed:", report)
            self.assertIn("Violated rule:", report)
            for check in result["record"]["checks"]:
                if check["status"] == "pass":
                    self.assertNotIn(check["identity"], report)
                else:
                    failure = check.get("failure")
                    if check["scope"] == "orphan":
                        continue
                    if failure is None:
                        self.assertEqual(check["status"], "not_applicable")
                        self.assertIn(f"`{check['identity']}`", report)
                        self.assertTrue(check["dependencies"])
                    else:
                        self.assertIn(f"`{failure['code']}`", report)
                        if failure["code"] != "orphan.material.unused":
                            self.assertIn(f"`{failure['subject']}`", report)
            self.assertIn("| hygiene | findings | `fail`", report)

    def test_report_renders_the_cause_of_dependent_not_applicable_checks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"][0]["sources"][0]["source"] = "<missing>"
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
            self.assertFalse((Path(directory) / ".cache").exists())

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
                (summary.with_suffix("") / "validation/results.json").is_file()
            )

    def test_invalid_date_is_an_operational_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            with self.assertRaisesRegex(
                CONTROLLER.ValidationControllerError, "YYYY-MM-DD"
            ):
                CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-8-29")
                )
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_invalid_cache_recomputes_without_changing_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            first = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            cache_path = _cache_path(summary)
            cache_path.write_bytes(b"not a sqlite database")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            self.assertEqual(first["status"], "complete_clear")
            self.assertEqual(result["status"], "complete_clear")
            self.assertEqual(result["metrics"]["checks_unchanged"], 0)
            with closing(sqlite3.connect(cache_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    VALIDATION_CACHE.CACHE_SCHEMA_VERSION,
                )

    def test_future_cache_schema_is_preserved_and_bypassed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            cache_path = _cache_path(summary)
            cache_path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(cache_path)) as connection:
                connection.execute(
                    f"PRAGMA user_version={VALIDATION_CACHE.CACHE_SCHEMA_VERSION + 1}"
                )
            before = cache_path.read_bytes()

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            self.assertEqual(result["status"], "complete_clear")
            self.assertEqual(result["metrics"]["checks_unchanged"], 0)
            self.assertEqual(cache_path.read_bytes(), before)

    def test_unchanged_validation_reports_matching_checks_and_reuses_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            first = CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}
            mechanical_before = (log_root / "validation/results.json").stat()

            with (
                mock.patch.object(
                    LOCATOR,
                    "_bounded_text_payload",
                    side_effect=AssertionError("warm hit must not read full payload"),
                ),
                mock.patch.object(
                    LOCATOR,
                    "_evaluate_record_table",
                    side_effect=AssertionError("warm hit must not parse the source"),
                ),
            ):
                second = CONTROLLER.validate(request)

            self.assertEqual(first["status"], "complete_clear")
            self.assertEqual(second["status"], "complete_clear")
            self.assertEqual(first["record"], second["record"])
            # The script, two data artifacts, and output-support file are each
            # hashed once; later consumers reuse those observations.
            self.assertEqual(first["metrics"]["fingerprint_cache_file_hashes"], 4)
            self.assertGreater(second["metrics"]["checks_unchanged"], 0)
            self.assertGreater(second["metrics"]["input_fingerprints_reused"], 0)
            self.assertGreater(second["metrics"]["selection_cache_hits"], 0)
            self.assertEqual(second["metrics"]["source_payload_reads"], 0)
            self.assertEqual(second["metrics"]["source_evaluations"], 0)
            self.assertEqual(second["metrics"]["fingerprint_cache_file_hashes"], 0)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)
            mechanical_after = (log_root / "validation/results.json").stat()
            self.assertEqual(
                (
                    mechanical_after.st_ino,
                    mechanical_after.st_mtime_ns,
                    mechanical_after.st_ctime_ns,
                ),
                (
                    mechanical_before.st_ino,
                    mechanical_before.st_mtime_ns,
                    mechanical_before.st_ctime_ns,
                ),
            )

    def test_renaming_an_evidence_token_preserves_selection_cache_eligibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            first = CONTROLLER.validate(request)
            entry_root = entry.parent
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["name"] = "renamed-results"
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry_root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "<renamed-results>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            second = CONTROLLER.validate(request)

            self.assertEqual(first["status"], "complete_clear")
            self.assertEqual(second["status"], "complete_clear")
            self.assertGreater(second["metrics"]["selection_cache_hits"], 0)
            self.assertEqual(second["metrics"]["source_payload_reads"], 0)
            self.assertEqual(second["metrics"]["source_evaluations"], 0)
            self.assertEqual(second["metrics"]["fingerprint_cache_file_hashes"], 0)

    def test_unchanged_local_input_reuses_its_verified_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            catalog = entry_root / "inputs" / "catalog.csv"
            write(catalog, "id\n1\n")
            data_path = entry_root / "data.json"
            payload = json.loads(data_path.read_text())
            payload["inputs"] = [
                {
                    "name": "catalog",
                    "kind": "file",
                    "location": "inputs/catalog.csv",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                    },
                    "origin": True,
                }
            ]
            write(data_path, json.dumps(payload) + "\n")
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")

            first = CONTROLLER.validate(request)
            second = CONTROLLER.validate(request)

            self.assertEqual(first["metrics"]["input_fingerprints_reused"], 0)
            self.assertEqual(second["metrics"]["input_fingerprints_reused"], 1)
            self.assertGreaterEqual(
                second["metrics"]["fingerprint_cache_file_reuses"], 1
            )
            self.assertTrue(
                (Path(directory) / ".cache/research-log-fingerprints.sqlite3").is_file()
            )

    def test_completed_run_drops_obsolete_selection_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            cache_path = _cache_path(summary)
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)
            write(entry.parent / "evidence.json", "{\n")

            result = CONTROLLER.validate(request)

            self.assertEqual(result["status"], "complete_findings")
            self.assertEqual(_cache_rows(cache_path, "evidence_selections"), 0)

    def test_rules_change_invalidates_checks_but_preserves_selection_reuse(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            cache_path = _cache_path(summary)
            with closing(sqlite3.connect(cache_path)) as connection:
                connection.execute(
                    "UPDATE check_comparison SET rules_version = ?",
                    ("superseded-rules",),
                )
                connection.commit()

            rebuilt = CONTROLLER.validate(request)

            self.assertEqual(rebuilt["metrics"]["checks_unchanged"], 0)
            self.assertGreater(rebuilt["metrics"]["selection_cache_hits"], 0)
            self.assertEqual(rebuilt["metrics"]["source_payload_reads"], 0)
            self.assertGreater(rebuilt["metrics"]["input_fingerprints_reused"], 0)

    def test_changed_source_is_rehashed_instead_of_using_seeded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            write(entry.parent / "data" / "results.csv", "success_rate\n0.675\n")

            changed = CONTROLLER.validate(request)

            self.assertEqual(changed["status"], "complete_findings")
            self.assertEqual(changed["metrics"]["source_hashes_reused"], 0)

    def test_changed_presentation_reuses_selection_then_compares_freshly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace("`67.6%`", "`67.5%`"),
            )

            changed = CONTROLLER.validate(request)

            self.assertEqual(changed["status"], "complete_findings")
            self.assertGreater(changed["metrics"]["selection_cache_hits"], 0)
            self.assertEqual(changed["metrics"]["source_payload_reads"], 0)

    def test_recompute_bypasses_cache_and_publishes_rebuilt_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            ordinary = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(ordinary)
            unchanged = CONTROLLER.validate(ordinary)
            self.assertGreater(unchanged["metrics"]["checks_unchanged"], 0)

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    result_date="2026-08-29",
                    recompute=True,
                )
            )

            cache_path = _cache_path(summary)
            self.assertEqual(recomputed["status"], "complete_clear")
            self.assertTrue(recomputed["published"])
            self.assertEqual(recomputed["metrics"]["checks_unchanged"], 0)
            self.assertEqual(recomputed["metrics"]["source_hashes_reused"], 0)
            self.assertEqual(recomputed["metrics"]["selection_cache_hits"], 0)
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)

    def test_recompute_validation_reuses_project_fingerprints_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    result_date="2026-08-29",
                    recompute_validation=True,
                )
            )

            self.assertEqual(recomputed["status"], "complete_clear")
            self.assertEqual(recomputed["metrics"]["checks_unchanged"], 0)
            self.assertEqual(recomputed["metrics"]["selection_cache_hits"], 0)
            self.assertGreater(
                recomputed["metrics"]["fingerprint_cache_file_reuses"], 0
            )

    def test_recompute_fingerprints_reuses_per_log_validation_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    result_date="2026-08-29",
                    recompute_fingerprints=True,
                )
            )

            self.assertEqual(recomputed["status"], "complete_clear")
            self.assertGreater(recomputed["metrics"]["checks_unchanged"], 0)
            self.assertGreater(recomputed["metrics"]["selection_cache_hits"], 0)
            self.assertEqual(
                recomputed["metrics"]["fingerprint_cache_file_reuses"], 0
            )
            self.assertGreater(
                recomputed["metrics"]["fingerprint_cache_file_hashes"], 0
            )

    def test_recompute_dry_run_neither_reads_cache_nor_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
                _cache_path(summary),
            )
            before = {path: path.read_bytes() for path in tracked}
            project_cache = Path(directory) / ".cache/research-log-fingerprints.sqlite3"
            project_cache.write_bytes(b"not a sqlite database")

            with (
                mock.patch.object(
                    VALIDATION_CACHE.ValidationCache,
                    "_open_once",
                    side_effect=AssertionError("recompute dry-run must not open cache"),
                ),
                mock.patch.object(
                    FINGERPRINT_CACHE.FingerprintCache,
                    "_open_once",
                    side_effect=AssertionError("recompute dry-run must not open cache"),
                ),
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
            self.assertEqual(result["metrics"]["source_hashes_reused"], 0)
            self.assertEqual(result["metrics"]["checks_unchanged"], 0)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)
            self.assertEqual(project_cache.read_bytes(), b"not a sqlite database")

    def test_oversized_selection_is_valid_but_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            with mock.patch.object(VALIDATION_CACHE, "MAX_SELECTION_BYTES", 1):
                first = CONTROLLER.validate(request)
                result = CONTROLLER.validate(request)

            self.assertEqual(first["status"], "complete_clear")
            self.assertEqual(result["status"], "complete_clear")
            self.assertGreater(result["metrics"]["selection_cache_oversized"], 0)
            self.assertEqual(result["metrics"]["selection_cache_hits"], 0)
            self.assertGreater(result["metrics"]["source_payload_reads"], 0)
            self.assertEqual(
                _cache_rows(_cache_path(summary), "evidence_selections"), 0
            )

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            unavailable = LOCATOR.LocatorV2Error(
                "locator.reader.unavailable",
                "data/results.csv",
                {"error": "unavailable"},
                "V2: Expanded Mechanical Locator Language",
                outcome="unavailable",
            )
            with mock.patch.object(
                ENGINE, "observe_source_identity", side_effect=unavailable
            ):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["published"])
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())

    def test_each_exact_unsupported_path_is_recognized_without_decoding(self) -> None:
        for relative in CONTROLLER.UNSUPPORTED_GENERATED_PATHS:
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory() as (directory),
            ):
                summary, _ = _log(Path(directory))
                log_root = summary.with_suffix("")
                path = log_root / relative
                write(path, "not valid unsupported content\n")
                before = path.read_bytes()

                result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

                self.assertEqual(result["status"], "unsupported_metadata")
                self.assertEqual(result["code"], "validation.unsupported_metadata")
                self.assertEqual(result["observed"]["paths"], [relative])
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((log_root / "validation/results.json").exists())

    def test_unrecognized_validation_file_does_not_trigger_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(summary.with_suffix("") / "validation/unrelated.json", "{}\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )

            self.assertEqual(result["status"], "complete_clear")
            self.assertTrue(result["published"])

    def test_unsupported_report_marker_is_a_precise_preflight_condition(self) -> None:
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

            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(result["observed"]["paths"], ["validation.md"])
            self.assertEqual(report.read_bytes(), before)
            self.assertFalse((log_root / "validation/results.json").exists())

    def test_unsupported_transaction_state_is_reported_without_writing(self) -> None:
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
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)
            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(
                result["observed"]["paths"],
                ["validation/.cache/upgrade-transactions"],
            )

    def test_dangling_unsupported_state_symlink_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            pending = summary.with_suffix("") / "validation/.cache/upgrade-transactions"
            pending.parent.mkdir(parents=True)
            pending.symlink_to("missing-transaction-directory")

            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(result["status"], "unsupported_metadata")
            self.assertEqual(
                result["observed"]["paths"],
                ["validation/.cache/upgrade-transactions"],
            )
            self.assertFalse(
                (summary.with_suffix("") / "validation/results.json").exists()
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
                "validation/results.json": b"old record\n",
                "validation.md": b"old report\n",
            }
            for relative, payload in old.items():
                path = log_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            original = RECORDS._atomic_write_bytes
            calls = 0

            def fail_second(path: Path, payload: bytes):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fixture failure")
                return original(path, payload)

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

    def test_publication_snapshots_prior_files_without_whole_file_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            prior = log_root / "validation/results.json"
            write(prior, "old record\n")

            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("whole-file read is forbidden"),
            ):
                RECORDS.publish_validation_outputs(
                    log_root,
                    {"validation/results.json": b"new record\n"},
                )

            self.assertEqual(prior.read_text(encoding="utf-8"), "new record\n")

    def test_locked_publication_returns_exact_installed_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            log_root.mkdir()
            relative = "validation/results.json"
            with OPERATION_STATE.operation_lock(
                log_root, "log.lock", mode="exclusive"
            ):
                identities = RECORDS.publish_validation_outputs_locked(
                    log_root,
                    {relative: b"new record\n"},
                )

            published = log_root / relative
            self.assertEqual(
                identities[relative], FILESYSTEM.file_identity(published.lstat())
            )

    def test_incomplete_rollback_is_reported_truthfully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            path = log_root / "validation/results.json"
            write(path, "old record\n")

            with (
                mock.patch.object(
                    RECORDS,
                    "_atomic_write_bytes",
                    side_effect=OSError("publication failed"),
                ),
                mock.patch.object(
                    RECORDS,
                    "_atomic_copy_file",
                    side_effect=OSError("rollback failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    RECORDS.RecordPublicationError, "rollback was incomplete"
                ):
                    RECORDS.publish_validation_outputs(
                        log_root,
                        {"validation/results.json": b"new record\n"},
                    )

    def test_log_lock_rejects_a_second_validation_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            log_root.mkdir()
            with OPERATION_STATE.operation_lock(
                log_root, "log.lock", mode="exclusive"
            ):
                with self.assertRaisesRegex(
                    OPERATION_STATE.OperationLockError,
                    "research-log operation is active",
                ):
                    RECORDS.publish_validation_outputs(
                        log_root,
                        {"validation/results.json": b"new record\n"},
                    )
            self.assertFalse((log_root / "validation/results.json").exists())

    def test_controller_holds_log_lock_through_evaluation_and_promotion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            original_evaluate = CONTROLLER.evaluate_mechanical
            original_finish = VALIDATION_CACHE.ValidationCache.finish_published_run

            def evaluate_while_locked(*args: object, **kwargs: object):
                with self.assertRaisesRegex(
                    OPERATION_STATE.OperationLockError,
                    "research-log operation is active",
                ):
                    with OPERATION_STATE.operation_lock(
                        log_root, "log.lock", mode="exclusive"
                    ):
                        pass
                return original_evaluate(*args, **kwargs)

            def finish_while_locked(cache: object, *args: object, **kwargs: object):
                with self.assertRaisesRegex(
                    OPERATION_STATE.OperationLockError,
                    "research-log operation is active",
                ):
                    with OPERATION_STATE.operation_lock(
                        log_root, "log.lock", mode="exclusive"
                    ):
                        pass
                return original_finish(cache, *args, **kwargs)

            with (
                mock.patch.object(
                    CONTROLLER,
                    "evaluate_mechanical",
                    side_effect=evaluate_while_locked,
                ),
                mock.patch.object(
                    VALIDATION_CACHE.ValidationCache,
                    "finish_published_run",
                    autospec=True,
                    side_effect=finish_while_locked,
                ),
            ):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )

            self.assertTrue(result["published"])

    def test_controller_acquires_log_lock_before_preflight_in_both_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            original = CONTROLLER._validate_request

            def preflight_while_locked(selected: Path) -> None:
                with self.assertRaisesRegex(
                    OPERATION_STATE.OperationLockError,
                    "research-log operation is active",
                ):
                    with OPERATION_STATE.operation_lock(
                        log_root, "log.lock", mode="exclusive"
                    ):
                        pass
                original(selected)

            for publish in (False, True):
                with self.subTest(publish=publish), mock.patch.object(
                    CONTROLLER,
                    "_validate_request",
                    side_effect=preflight_while_locked,
                ):
                    result = CONTROLLER.validate(
                        CONTROLLER.ValidationRequest(
                            summary,
                            result_date="2026-08-29",
                            publish=publish,
                        )
                    )
                self.assertEqual(result["published"], publish)

    def test_validation_locks_are_independent_between_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            first, _ = _log(root / "first")
            second, _ = _log(root / "second")
            with OPERATION_STATE.operation_lock(
                first.with_suffix(""), "log.lock", mode="exclusive"
            ):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(
                        second,
                        result_date="2026-08-29",
                        publish=False,
                    )
                )
            self.assertFalse(result["published"])

    def test_post_publication_cache_failure_preserves_authoritative_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))

            with mock.patch.object(
                VALIDATION_CACHE.ValidationCache,
                "finish_published_run",
                return_value=False,
            ):
                result = CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
                )

            log_root = summary.with_suffix("")
            self.assertTrue(result["published"])
            self.assertTrue((log_root / "validation/results.json").is_file())
            self.assertTrue((log_root / "validation.md").is_file())

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
                    {"validation/results.json": b"new record\n"},
                )

            self.assertFalse((external / "results.json").exists())

    def test_engine_operational_error_preserves_prior_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with mock.patch.object(
                CONTROLLER, "evaluate_mechanical", side_effect=OSError("fixture")
            ):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError, "fixture"
                ):
                    CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_metadata_preflight_during_publication_restores_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}
            original = RECORDS._atomic_write_bytes
            introduced = False

            unsupported = log_root / "validation/manifest.json"

            def introduce_unsupported_metadata(path: Path, payload: bytes):
                nonlocal introduced
                identity = original(path, payload)
                if path.name == "validation.md" and not introduced:
                    introduced = True
                    write(unsupported, "{}\n")
                return identity

            with mock.patch.object(
                RECORDS,
                "_atomic_write_bytes",
                side_effect=introduce_unsupported_metadata,
            ):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "acquired unsupported metadata",
                ):
                    CONTROLLER.validate(request)

            self.assertTrue(unsupported.is_file())
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_active_research_mutation_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with OPERATION_STATE.operation_lock(
                log_root, "log.lock", mode="shared"
            ), OPERATION_STATE.operation_lock(log_root, "entry-e001.lock"):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "research-log operation is active",
                ):
                    CONTROLLER.validate(request)

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_changed_research_snapshot_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary, result_date="2026-08-29")
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / "validation/results.json",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}
            original = CONTROLLER.evaluate_mechanical

            def change_after_evaluation(*args: object, **kwargs: object):
                result = original(*args, **kwargs)
                write(entry.parent / "data/concurrent.txt", "changed\n")
                return result

            with mock.patch.object(
                CONTROLLER,
                "evaluate_mechanical",
                side_effect=change_after_evaluation,
            ):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "research-owned state changed",
                ):
                    CONTROLLER.validate(request)

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)


if __name__ == "__main__":
    unittest.main()
