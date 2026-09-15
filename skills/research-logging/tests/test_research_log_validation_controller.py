from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import fields
from pathlib import Path

from research_log_result_store import (
    ResultStoreError,
    record_report_materialization,
    result_generation,
    result_snapshot,
)
from research_log_validation_test_support import (
    mechanical_log,
    mock,
    unittest,
    write,
)
from validation.command_diagnostics import publish_command_diagnostic
from validation.snapshot_storage import (
    SnapshotPublicationRequest,
    load_validation_snapshot,
    publish_validation_snapshot,
)

CONTROLLER = importlib.import_module("validation.controller")
DATA = importlib.import_module("research_log_data")
DOMAIN = importlib.import_module("validation.domain")
ENGINE = importlib.import_module("validation.engine")
FINGERPRINT_CACHE = importlib.import_module("validation.fingerprint_cache")
REPORT_CONTEXT = importlib.import_module("validation.report_context")
LOCATOR = importlib.import_module("validation.locator")
OPERATION_STATE = importlib.import_module("validation.operation_state")
RECORDS = importlib.import_module("validation.records")
VALIDATION_CACHE = importlib.import_module("validation.validation_cache")


_log = mechanical_log


def _cache_path(summary: Path) -> Path:
    return summary.with_suffix("") / ".cache" / VALIDATION_CACHE.CACHE_FILENAME


def _cache_rows(path: Path, table: str) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _result_rows(log_root: Path) -> list[tuple[object, ...]]:
    with result_snapshot(log_root) as database:
        return [
            tuple(row)
            for row in database.execute(
                "SELECT snapshot_id, generation, slot FROM validation_snapshots "
                "ORDER BY generation"
            )
        ]


def _stored_snapshot(log_root: Path, slot: str = "full"):
    snapshot = load_validation_snapshot(log_root, slot=slot)
    return snapshot, result_generation(log_root, "validation")


def _marker(log_root: Path) -> tuple[object, ...] | None:
    with result_snapshot(log_root) as database:
        row = database.execute(
            "SELECT source_generation, content_sha256 FROM report_materializations "
            "WHERE kind='validation'"
        ).fetchone()
        return None if row is None else tuple(row)


def _attempt_outcome(result) -> str:
    return result.snapshot.outcome.value


class MechanicalControllerTests(unittest.TestCase):
    def test_completed_attempt_publishes_snapshot_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            summary_bytes = summary.read_bytes()

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            log_root = summary.with_suffix("")
            snapshot, generation = _stored_snapshot(log_root)
            cache_path = _cache_path(summary)
            report = (log_root / "validation.md").read_text()
            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertTrue(result.published)
            self.assertEqual(
                tuple(field.name for field in fields(result)),
                ("attempt", "snapshot", "published", "metrics"),
            )
            self.assertGreaterEqual(
                result.metrics["validation_cache_sqlite_writes"], 3
            )
            self.assertTrue((log_root / ".cache" / "results.sqlite").is_file())
            self.assertEqual(generation, 1)
            self.assertEqual(_marker(log_root)[0], generation)
            self.assertTrue(cache_path.is_file())
            with closing(sqlite3.connect(cache_path)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    VALIDATION_CACHE.CACHE_SCHEMA_VERSION,
                )
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)
            self.assertIn("# Validation", report)
            self.assertNotIn("## Reproduction", report)
            self.assertNotIn("not_yet_run", report)
            self.assertIn("| Conformance | 0 |", report)
            self.assertIn("| Batches | 0 |", report)
            self.assertFalse(hasattr(result, "report"))
            self.assertFalse(hasattr(result, "record"))
            self.assertFalse(snapshot.findings)
            self.assertEqual(summary.read_bytes(), summary_bytes)
            cache_names = {path.name for path in (log_root / ".cache").iterdir()}
            self.assertIn(VALIDATION_CACHE.CACHE_FILENAME, cache_names)
            self.assertIn("results.sqlite", cache_names)
            self.assertNotIn("research-log-validation.lock", cache_names)
            self.assertIn("research-log-operations", cache_names)

    def test_findings_are_complete_and_batched_without_passing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory), output_option="results")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            report = (summary.with_suffix("") / "validation.md").read_text()
            self.assertEqual(_attempt_outcome(result), "findings")
            snapshot = load_validation_snapshot(summary.with_suffix(""))
            self.assertIn("## Findings", report)
            self.assertIn("## Batches", report)
            self.assertNotIn("Command chain", report)
            self.assertNotIn("Unresolved group", report)
            for check in result.attempt.checks:
                if check.outcome is DOMAIN.CheckOutcome.PASS:
                    self.assertNotIn(check.check_id, report)
                else:
                    diagnostic = check.diagnostic
                    if check.area is DOMAIN.RuleArea.ORPHAN:
                        continue
                    if diagnostic is None:
                        self.assertEqual(
                            check.outcome, DOMAIN.CheckOutcome.BLOCKED
                        )
                        self.assertNotIn(f"`{check.check_id}`", report)
                        self.assertTrue(check.dependency_evidence)
                    else:
                        presentation = REPORT_CONTEXT.CATALOG[diagnostic.code]
                        self.assertIn(f"### {presentation.name}", report)
                        self.assertIn(presentation.sentence, report)
            self.assertEqual(
                {
                    finding_id
                    for batch in snapshot.batches
                    for finding_id in batch.finding_ids
                },
                {finding.finding_id for finding in snapshot.findings},
            )
            unmatched = next(
                check
                for check in result.attempt.checks
                if check.check_id.startswith("orphan:unmatched-output:")
            )
            self.assertIs(unmatched.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertIn("| Orphans | 2 |", report)

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
                CONTROLLER.ValidationRequest(summary)
            )

            report = (summary.with_suffix("") / "validation.md").read_text()
            dependent = next(
                check
                for check in result.attempt.checks
                if check.check_id == "provenance:e001:success-rate"
            )
            self.assertEqual(dependent.outcome, DOMAIN.CheckOutcome.BLOCKED)
            self.assertEqual(
                dependent.dependency_evidence,
                ({"dependency": "evidence:e001:success-rate"},),
            )
            self.assertNotIn("`provenance:e001:success-rate`", report)
            self.assertIn("### Undeclared Command Input", report)

    def test_dry_run_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary, publish=False
                )
            )
            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertFalse(result.published)
            self.assertFalse(hasattr(result, "report"))
            self.assertFalse((summary.with_suffix("") / "validation").exists())
            self.assertFalse((summary.with_suffix("") / "validation.md").exists())
            self.assertFalse((Path(directory) / ".cache").exists())

    def test_malformed_v2_is_a_completed_conformance_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "evidence.json", "{\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            self.assertEqual(_attempt_outcome(result), "findings")
            failures = [
                check.diagnostic.code
                for check in result.attempt.checks
                if check.diagnostic is not None
            ]
            self.assertIn("evidence.json.schema_invalid", failures)
            self.assertTrue(result.published)
            self.assertTrue(
                (summary.with_suffix("") / ".cache/results.sqlite").is_file()
            )

    def test_invalid_cache_recomputes_without_changing_the_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            first = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )
            cache_path = _cache_path(summary)
            cache_path.write_bytes(b"not a sqlite database")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            self.assertEqual(_attempt_outcome(first), "clear")
            self.assertEqual(_attempt_outcome(result), "clear")
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
                CONTROLLER.ValidationRequest(summary)
            )

            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertEqual(cache_path.read_bytes(), before)

    def test_unchanged_validation_reuses_selections_and_preserves_findings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            first = CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            first_snapshot, first_generation = _stored_snapshot(log_root)

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

            self.assertEqual(_attempt_outcome(first), "clear")
            self.assertEqual(_attempt_outcome(second), "clear")
            self.assertEqual(
                first.attempt.checks,
                second.attempt.checks,
            )
            self.assertEqual(
                first.attempt.findings,
                second.attempt.findings,
            )
            # The script, two data artifacts, and output-support file are each
            # hashed once; later consumers reuse those observations.
            self.assertEqual(first.metrics["fingerprint_cache_file_hashes"], 4)
            self.assertGreater(second.metrics["input_fingerprints_reused"], 0)
            self.assertGreater(second.metrics["selection_cache_hits"], 0)
            self.assertEqual(second.metrics["source_payload_reads"], 0)
            self.assertEqual(second.metrics["source_evaluations"], 0)
            self.assertEqual(second.metrics["fingerprint_cache_file_hashes"], 0)
            second_snapshot, second_generation = _stored_snapshot(log_root)
            self.assertNotEqual(
                second_snapshot.internal_snapshot_id,
                first_snapshot.internal_snapshot_id,
            )
            self.assertGreater(second_generation, first_generation)
            self.assertEqual(second_snapshot.findings, first_snapshot.findings)
            self.assertEqual(_marker(log_root)[0], second_generation)

    def test_renaming_an_evidence_token_preserves_selection_cache_eligibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
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
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "<results>", "<renamed-results>"
                ),
            )

            second = CONTROLLER.validate(request)

            self.assertEqual(_attempt_outcome(first), "clear")
            self.assertEqual(_attempt_outcome(second), "clear")
            self.assertGreater(second.metrics["selection_cache_hits"], 0)
            self.assertEqual(second.metrics["source_payload_reads"], 0)
            self.assertEqual(second.metrics["source_evaluations"], 0)
            self.assertEqual(second.metrics["fingerprint_cache_file_hashes"], 0)

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
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                }
            ]
            write(data_path, json.dumps(payload) + "\n")
            request = CONTROLLER.ValidationRequest(summary)

            first = CONTROLLER.validate(request)
            second = CONTROLLER.validate(request)

            self.assertEqual(first.metrics["input_fingerprints_reused"], 0)
            self.assertEqual(second.metrics["input_fingerprints_reused"], 1)
            self.assertGreaterEqual(
                second.metrics["fingerprint_cache_file_reuses"], 1
            )
            self.assertTrue(
                (Path(directory) / ".cache/research-log-fingerprints.sqlite3").is_file()
            )

    def test_completed_run_drops_obsolete_selection_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            cache_path = _cache_path(summary)
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)
            write(entry.parent / "evidence.json", "{\n")

            result = CONTROLLER.validate(request)

            self.assertEqual(_attempt_outcome(result), "findings")
            self.assertEqual(_cache_rows(cache_path, "evidence_selections"), 0)

    def test_changed_source_is_rehashed_instead_of_using_seeded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            write(entry.parent / "data" / "results.csv", "success_rate\n0.675\n")

            changed = CONTROLLER.validate(request)

            self.assertEqual(_attempt_outcome(changed), "findings")
            self.assertEqual(changed.metrics["source_hashes_reused"], 0)

    def test_changed_presentation_reuses_selection_then_compares_freshly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace("`67.6%`", "`67.5%`"),
            )

            changed = CONTROLLER.validate(request)

            self.assertEqual(_attempt_outcome(changed), "findings")
            self.assertGreater(changed.metrics["selection_cache_hits"], 0)
            self.assertEqual(changed.metrics["source_payload_reads"], 0)

    def test_recompute_bypasses_cache_and_publishes_rebuilt_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            ordinary = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(ordinary)
            CONTROLLER.validate(ordinary)

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    recompute=True,
                )
            )

            cache_path = _cache_path(summary)
            self.assertEqual(_attempt_outcome(recomputed), "clear")
            self.assertTrue(recomputed.published)
            self.assertEqual(recomputed.metrics["source_hashes_reused"], 0)
            self.assertEqual(recomputed.metrics["selection_cache_hits"], 0)
            self.assertGreater(_cache_rows(cache_path, "evidence_selections"), 0)

    def test_recompute_validation_reuses_project_fingerprints_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    recompute_validation=True,
                )
            )

            self.assertEqual(_attempt_outcome(recomputed), "clear")
            self.assertEqual(recomputed.metrics["selection_cache_hits"], 0)
            self.assertGreater(
                recomputed.metrics["fingerprint_cache_file_reuses"], 0
            )

    def test_recompute_fingerprints_reuses_per_log_validation_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            recomputed = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    recompute_fingerprints=True,
                )
            )

            self.assertEqual(_attempt_outcome(recomputed), "clear")
            self.assertGreater(recomputed.metrics["selection_cache_hits"], 0)
            self.assertEqual(recomputed.metrics["fingerprint_cache_file_reuses"], 0)
            self.assertGreater(
                recomputed.metrics["fingerprint_cache_file_hashes"], 0
            )

    def test_recompute_dry_run_neither_reads_cache_nor_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / ".cache/results.sqlite",
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
                        publish=False,
                        recompute=True,
                    )
                )

            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertFalse(result.published)
            self.assertEqual(result.metrics["source_hashes_reused"], 0)
            self.assertEqual({path: path.read_bytes() for path in tracked}, before)
            self.assertEqual(project_cache.read_bytes(), b"not a sqlite database")

    def test_oversized_selection_is_saved_as_failed_but_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            with mock.patch.object(VALIDATION_CACHE, "MAX_SELECTION_BYTES", 1):
                first = CONTROLLER.validate(request)
                result = CONTROLLER.validate(request)

            self.assertEqual(_attempt_outcome(first), "clear")
            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertGreater(result.metrics["selection_cache_oversized"], 0)
            self.assertEqual(result.metrics["selection_cache_hits"], 0)
            self.assertGreater(result.metrics["source_payload_reads"], 0)
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
                    CONTROLLER.ValidationRequest(summary)
                )
            self.assertEqual(_attempt_outcome(result), "failed")
            self.assertTrue(result.published)
            self.assertTrue((summary.with_suffix("") / "validation.md").exists())

    def test_each_obsolete_validation_path_becomes_a_orphan_finding(self) -> None:
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

                self.assertEqual(_attempt_outcome(result), "findings")
                findings = [
                    check.diagnostic.as_dict()
                    for check in result.attempt.checks
                    if check.diagnostic is not None
                    and check.diagnostic.code == "orphan.generated.residue"
                ]
                self.assertEqual(
                    findings,
                    [
                        {
                            "code": "orphan.generated.residue",
                            "observed": {"path": relative},
                            "rule": "Generated Validation Ownership",
                            "subject": path.resolve().as_posix(),
                        }
                    ],
                )
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue((log_root / ".cache/results.sqlite").exists())

    def test_unrecognized_validation_file_does_not_trigger_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(summary.with_suffix("") / "validation/unrelated.json", "{}\n")

            result = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )

            self.assertEqual(_attempt_outcome(result), "clear")
            self.assertTrue(result.published)

    def test_obsolete_report_marker_becomes_orphan_and_is_replaced(self) -> None:
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
            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(_attempt_outcome(result), "findings")
            codes = {
                check.diagnostic.code
                for check in result.attempt.checks
                if check.diagnostic is not None
            }
            self.assertIn("orphan.generated.residue", codes)
            self.assertNotIn(
                b"| Entry | Date | Checked | Reproducibility |",
                report.read_bytes(),
            )
            self.assertTrue((log_root / ".cache/results.sqlite").exists())

    def test_obsolete_transaction_state_is_published_as_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )
            log_root = summary.with_suffix("")
            transaction = (
                log_root
                / "validation/.cache/upgrade-transactions/transaction/transaction.json"
            )
            write(transaction, "{}\n")
            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(_attempt_outcome(result), "findings")
            residue = [
                check.diagnostic.observed["path"]
                for check in result.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "orphan.generated.residue"
            ]
            self.assertEqual(residue, ["validation/.cache/upgrade-transactions"])
            self.assertIn(
                "### Obsolete Validation Artifact",
                (log_root / "validation.md").read_text(encoding="utf-8"),
            )

    def test_dangling_obsolete_state_symlink_becomes_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            pending = summary.with_suffix("") / "validation/.cache/upgrade-transactions"
            pending.parent.mkdir(parents=True)
            pending.symlink_to("missing-transaction-directory")

            result = CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            self.assertEqual(_attempt_outcome(result), "findings")
            residue = [
                check.diagnostic.observed["path"]
                for check in result.attempt.checks
                if check.diagnostic is not None
                and check.diagnostic.code == "orphan.generated.residue"
            ]
            self.assertEqual(residue, ["validation/.cache/upgrade-transactions"])
            self.assertTrue(
                (summary.with_suffix("") / ".cache/results.sqlite").exists()
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

    def test_full_entry_and_diagnostic_slots_have_independent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_document = _log(Path(directory))
            log_root = summary.with_suffix("")
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            _, full_generation = _stored_snapshot(log_root)
            entry = CONTROLLER.validate_entry(
                CONTROLLER.EntryValidationRequest(
                    summary, "e001", entry_document.parent
                )
            )
            self.assertIsNotNone(entry.snapshot_id)
            entry_snapshot = load_validation_snapshot(log_root, slot="entry:e001")
            self.assertRegex(entry_snapshot.source_identity, r"^[0-9a-f]{64}$")
            self.assertTrue(entry_snapshot.started_at)
            self.assertTrue(entry_snapshot.finished_at)
            self.assertTrue(entry_snapshot.stored_at)
            self.assertEqual(
                [row[2] for row in _result_rows(log_root)], ["full", "entry:e001"]
            )
            publish_command_diagnostic(
                log_root, summary.resolve().as_posix(), "fixture", ()
            )
            self.assertEqual(
                [row[2] for row in _result_rows(log_root)],
                ["full", "entry:e001"],
            )
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            rows = _result_rows(log_root)
            self.assertEqual([row[2] for row in rows], ["full"])
            self.assertGreater(rows[0][1], full_generation)

    def test_full_publication_timestamps_bracket_mechanical_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            moments = iter(
                f"2026-09-12T10:00:0{second}.000000+00:00" for second in (1, 2)
            )
            events: list[str] = []
            original_evaluate = CONTROLLER.evaluate_mechanical

            def timestamp() -> str:
                events.append("timestamp")
                return next(moments)

            def evaluate(*args: object, **kwargs: object):
                events.append("evaluate")
                return original_evaluate(*args, **kwargs)

            with (
                mock.patch.object(ENGINE, "_utc_timestamp", side_effect=timestamp),
                mock.patch.object(
                    CONTROLLER, "evaluate_mechanical", side_effect=evaluate
                ),
            ):
                CONTROLLER.validate(
                    CONTROLLER.ValidationRequest(summary)
                )

            exported = load_validation_snapshot(log_root)
            self.assertEqual(
                events,
                ["evaluate", "timestamp", "timestamp"],
            )
            self.assertEqual(exported.started_at, "2026-09-12T10:00:01.000000+00:00")
            self.assertEqual(exported.finished_at, "2026-09-12T10:00:02.000000+00:00")

    def test_entry_publication_timestamps_bracket_mechanical_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_document = _log(Path(directory))
            log_root = summary.with_suffix("")
            moments = iter(
                f"2026-09-12T10:00:0{second}.000000+00:00" for second in (1, 2)
            )
            events: list[str] = []
            original_evaluate = CONTROLLER.evaluate_mechanical

            def timestamp() -> str:
                events.append("timestamp")
                return next(moments)

            def evaluate(*args: object, **kwargs: object):
                events.append("evaluate")
                return original_evaluate(*args, **kwargs)

            with (
                mock.patch.object(ENGINE, "_utc_timestamp", side_effect=timestamp),
                mock.patch.object(
                    CONTROLLER, "evaluate_mechanical", side_effect=evaluate
                ),
            ):
                result = CONTROLLER.validate_entry(
                    CONTROLLER.EntryValidationRequest(
                        summary, "e001", entry_document.parent
                    )
                )

            assert result.snapshot_id is not None
            exported = load_validation_snapshot(log_root, slot="entry:e001")
            self.assertEqual(events, ["evaluate", "timestamp", "timestamp"])
            self.assertEqual(exported.started_at, "2026-09-12T10:00:01.000000+00:00")
            self.assertEqual(exported.finished_at, "2026-09-12T10:00:02.000000+00:00")

    def test_failed_snapshot_transaction_preserves_completed_full_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            before = _result_rows(log_root)
            snapshot = load_validation_snapshot(log_root)

            def fail(phase: str) -> None:
                if phase == "before_commit":
                    raise RuntimeError("fixture transaction failure")

            with self.assertRaisesRegex(RuntimeError, "fixture transaction failure"):
                publish_validation_snapshot(
                    SnapshotPublicationRequest(log_root, snapshot, {}),
                    _test_hook=fail,
                )
            self.assertEqual(_result_rows(log_root), before)

    def test_report_marker_tracks_committed_generation_and_stales_on_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            snapshot, first_generation = _stored_snapshot(log_root)
            self.assertEqual(_marker(log_root)[0], first_generation)
            replacement = publish_validation_snapshot(
                SnapshotPublicationRequest(log_root, snapshot, {})
            )
            self.assertIsNone(_marker(log_root))
            report = (log_root / "validation.md").read_bytes()
            record_report_materialization(log_root, "validation", report)
            self.assertEqual(_marker(log_root)[0], replacement.generation)

    def test_render_failure_preserves_prior_report_after_snapshot_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            _, before_generation = _stored_snapshot(log_root)
            report_before = (log_root / "validation.md").read_bytes()
            with (
                mock.patch.object(
                    RECORDS,
                    "_atomic_write_bytes",
                    side_effect=OSError("fixture render failure"),
                ),
                self.assertRaises(CONTROLLER.ValidationControllerError) as raised,
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            after, after_generation = _stored_snapshot(log_root)
            self.assertEqual(raised.exception.code, "validation.report.write_failed")
            self.assertIn(after.internal_snapshot_id, str(raised.exception))
            self.assertIn(f"generation={after_generation}", str(raised.exception))
            self.assertGreater(after_generation, before_generation)
            self.assertEqual((log_root / "validation.md").read_bytes(), report_before)
            self.assertIsNone(_marker(log_root))

            from log_commands.context import LogContext
            from log_commands.validation_cli import render_validation

            with mock.patch.object(
                CONTROLLER,
                "validate",
                side_effect=AssertionError("render retry must not reevaluate"),
            ):
                self.assertEqual(
                    render_validation(LogContext(summary.resolve(), log_root)),
                    None,
                )
            self.assertTrue((log_root / "validation.md").is_file())
            self.assertEqual(_marker(log_root)[0], after_generation)

    def test_report_composition_failure_names_the_committed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            log_root = summary.with_suffix("")
            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
            report_before = (log_root / "validation.md").read_bytes()
            with (
                mock.patch.object(
                    importlib.import_module("validation.snapshot_report"),
                    "compose_snapshot_report",
                    side_effect=ValueError("fixture composition failure"),
                ),
                self.assertRaises(CONTROLLER.ValidationControllerError) as raised,
            ):
                CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))

            committed, generation = _stored_snapshot(log_root)
            self.assertEqual(raised.exception.code, "validation.report.render_failed")
            self.assertIn(committed.internal_snapshot_id, str(raised.exception))
            self.assertIn(f"generation={generation}", str(raised.exception))
            self.assertEqual((log_root / "validation.md").read_bytes(), report_before)
            self.assertIsNone(_marker(log_root))

    def test_lock_owner_metadata_is_visible_bounded_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory) / "log"
            log_root.mkdir()
            owner = {
                "log": log_root.as_posix(),
                "operation": "validation",
                "pid": 123,
                "publication": True,
                "request_fingerprint": "a" * 64,
                "schema": OPERATION_STATE.LOCK_OWNER_SCHEMA,
                "source_fingerprint": "b" * 64,
                "started_at": "2026-09-08T00:00:00Z",
            }
            directory_path = OPERATION_STATE.operation_directory(log_root)
            metadata = directory_path / "log.lock.owner.json"
            with OPERATION_STATE.operation_lock(
                log_root,
                "log.lock",
                mode="exclusive",
                owner_factory=lambda: owner,
            ):
                self.assertEqual(json.loads(metadata.read_text()), owner)
                with self.assertRaises(OPERATION_STATE.OperationLockError) as raised:
                    with OPERATION_STATE.operation_lock(
                        log_root, "log.lock", mode="exclusive"
                    ):
                        pass
                self.assertEqual(raised.exception.owner, owner)
            self.assertFalse(metadata.exists())

            metadata.write_text(json.dumps(owner), encoding="utf-8")
            replacement = {**owner, "pid": 456}
            with OPERATION_STATE.operation_lock(
                log_root,
                "log.lock",
                mode="exclusive",
                owner_factory=lambda: replacement,
            ):
                self.assertEqual(json.loads(metadata.read_text()), replacement)
            self.assertFalse(metadata.exists())

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
                    CONTROLLER.ValidationRequest(summary)
                )

            self.assertTrue(result.published)

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
                with (
                    self.subTest(publish=publish),
                    mock.patch.object(
                        CONTROLLER,
                        "_validate_request",
                        side_effect=preflight_while_locked,
                    ),
                ):
                    result = CONTROLLER.validate(
                        CONTROLLER.ValidationRequest(
                            summary,
                            publish=publish,
                        )
                    )
                self.assertEqual(result.published, publish)

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
                        publish=False,
                    )
                )
            self.assertFalse(result.published)

    def test_post_publication_cache_failure_preserves_authoritative_snapshot(
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
                    CONTROLLER.ValidationRequest(summary)
                )

            log_root = summary.with_suffix("")
            self.assertTrue(result.published)
            self.assertTrue((log_root / ".cache/results.sqlite").is_file())
            self.assertTrue((log_root / "validation.md").is_file())

    def test_symlinked_publication_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            summary, _ = _log(root)
            log_root = summary.with_suffix("")
            cache = log_root / ".cache"
            cache.mkdir(exist_ok=True)
            (cache / "results.sqlite").symlink_to(external / "results.sqlite")
            evaluation = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(summary)
            )
            assert evaluation.snapshot is not None

            with self.assertRaisesRegex(
                ResultStoreError,
                "symlink",
            ):
                publish_validation_snapshot(
                    SnapshotPublicationRequest(log_root, evaluation.snapshot, {})
                )

            self.assertFalse((external / "results.sqlite").exists())

    def test_engine_operational_error_preserves_prior_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            CONTROLLER.validate(
                CONTROLLER.ValidationRequest(summary)
            )
            log_root = summary.with_suffix("")
            tracked = (
                log_root / ".cache/results.sqlite",
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

    def test_metadata_preflight_during_publication_restores_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            before_rows = _result_rows(log_root)
            report_before = (log_root / "validation.md").read_bytes()
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
                    "generated validation residue changed",
                ):
                    CONTROLLER.validate(request)

            self.assertTrue(unsupported.is_file())
            self.assertEqual((log_root / "validation.md").read_bytes(), report_before)
            self.assertNotEqual(_result_rows(log_root), before_rows)
            self.assertIsNone(_marker(log_root))

    def test_active_research_mutation_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / ".cache/results.sqlite",
                log_root / "validation.md",
            )
            before = {path: path.read_bytes() for path in tracked}

            with (
                OPERATION_STATE.operation_lock(log_root, "log.lock", mode="shared"),
                OPERATION_STATE.operation_lock(log_root, "entry-e001.lock"),
            ):
                with self.assertRaisesRegex(
                    CONTROLLER.ValidationControllerError,
                    "research-log operation is active",
                ):
                    CONTROLLER.validate(request)

            self.assertEqual({path: path.read_bytes() for path in tracked}, before)

    def test_changed_research_snapshot_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = CONTROLLER.ValidationRequest(summary)
            CONTROLLER.validate(request)
            log_root = summary.with_suffix("")
            tracked = (
                log_root / ".cache/results.sqlite",
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
