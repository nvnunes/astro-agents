from __future__ import annotations

import importlib
import tempfile
from dataclasses import replace
from pathlib import Path

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import mechanical_log, mock, unittest

DOMAIN = importlib.import_module("validation.domain")
ENGINE = importlib.import_module("validation.engine")
LOCATOR = importlib.import_module("validation.locator")


def diagnostic(code: str, subject: str) -> object:
    return DOMAIN.CheckDiagnostic(
        code,
        subject,
        "Mechanical Validation Evaluation And Outcomes",
        {"state": "observed"},
    )


def target() -> object:
    return DOMAIN.ValidationTarget(DOMAIN.TargetKind.LOG, "docs/log")


class ValidationDomainTests(unittest.TestCase):
    def test_real_engine_builds_one_clear_snapshot_and_shared_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))

            result = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(summary)
            )

        self.assertEqual(len(result.attempt.checks), 6)
        self.assertFalse(result.attempt.findings)
        self.assertEqual(result.snapshot.outcome, DOMAIN.SnapshotOutcome.CLEAR)
        self.assertEqual(result.snapshot.passed_check_count, 6)
        self.assertGreaterEqual(len(result.context.graph.nodes), 10)
        self.assertGreaterEqual(len(result.context.graph.edges), 10)
        self.assertTrue(
            {
                "command",
                "data_record",
                "document",
                "entry",
                "evidence_record",
                "material",
                "presentation",
                "script",
            }
            <= {node.kind.value for node in result.context.graph.nodes}
        )
        self.assertFalse(result.context.graph.ambiguities)

    def test_real_engine_packages_related_evidence_and_provenance_for_repair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = mechanical_log(Path(directory))
            result_path = entry.parent / "data/results.csv"
            result_path.write_text("success_rate\n0.5\n", encoding="utf-8")

            result = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(summary)
            )

        self.assertEqual(
            {
                (finding.type, finding.code)
                for finding in result.attempt.findings
            },
            {
                (
                    DOMAIN.RuleArea.EVIDENCE,
                    "transformation.presentation.mismatch",
                ),
            },
        )
        self.assertEqual(len(result.snapshot.blocked_checks), 1)
        self.assertEqual(
            result.snapshot.blocked_checks[0].rule,
            "Summary Association",
        )
        self.assertFalse(result.snapshot.failed_checks)
        self.assertTrue(result.context.currentness)
        self.assertEqual(len(result.snapshot.batches), 1)
        self.assertEqual(
            result.snapshot.batches[0].repair_entries,
            ("e001",),
        )
        self.assertGreaterEqual(
            len(result.snapshot.repair_context["relationships"]),
            3,
        )
        graph_node_ids = {node.node_id for node in result.context.graph.nodes}
        self.assertFalse(
            {
                reference.node_id
                for finding in result.attempt.findings
                for reference in finding.context_nodes
            }
            - graph_node_ids
        )
        self.assertTrue(
            all(
                finding.source_locations
                or finding.type is DOMAIN.RuleArea.PROVENANCE
                for finding in result.attempt.findings
            )
        )

    def test_transient_source_change_creates_failed_check_and_snapshot(
        self,
    ) -> None:
        changed = LOCATOR.LocatorV2Error(
            "locator.source.changed",
            "data/results.csv",
            {"reason": "changed"},
            "Stable Source Observation",
            outcome="unavailable",
        )
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            with mock.patch.object(
                ENGINE,
                "require_source_unchanged",
                side_effect=changed,
            ):
                result = ENGINE.evaluate_mechanical(
                    ENGINE.EvaluationRequest(summary)
                )

        self.assertEqual(result.snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED)
        self.assertEqual(len(result.snapshot.failed_checks), 1)
        self.assertEqual(
            result.snapshot.failed_checks[0].code,
            "locator.source.changed",
        )
        self.assertEqual(
            result.snapshot.failed_checks[0].operation,
            DOMAIN.FailureOperation.OBSERVE,
        )

    def test_finding_checks_become_exact_findings_and_passes_remain_private(
        self,
    ) -> None:
        context = DOMAIN.IssueContext(
            entry="e001",
            repair_keys=(
                DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.RECORD,
                    "e001:evidence:result",
                    "e001",
                ),
            ),
            context_nodes=(
                DOMAIN.GraphReference("material", "data/result.csv", "e001"),
            ),
            admission_owner=DOMAIN.AdmissionOwner.MATERIAL,
        )
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="2026-09-13T12:00:00Z",
            finished_at="2026-09-13T12:00:01Z",
            checks=(
                DOMAIN.RuleCheck(
                    "evidence:result",
                    DOMAIN.RuleArea.EVIDENCE,
                    DOMAIN.CheckOutcome.FINDING,
                    "data/result.csv",
                    diagnostic=diagnostic("association.missing", "data/result.csv"),
                    issue_context=context,
                ),
                DOMAIN.RuleCheck(
                    "conformance:log",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.PASS,
                    "conformance:log",
                ),
            ),
        )

        self.assertEqual(len(attempt.findings), 1)
        finding = attempt.findings[0]
        self.assertEqual(finding.finding_id, "evidence:result")
        self.assertEqual(finding.type, DOMAIN.RuleArea.EVIDENCE)
        self.assertEqual(finding.code, "association.missing")
        self.assertEqual(finding.entry, "e001")
        self.assertEqual(finding.subject, "data/result.csv")
        self.assertEqual(finding.repair_keys, context.repair_keys)
        self.assertFalse(hasattr(finding, "status"))

    def test_one_failed_root_blocks_all_dependent_checks(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="2026-09-13T12:00:00Z",
            finished_at="2026-09-13T12:00:01Z",
            checks=(
                DOMAIN.RuleCheck(
                    "source:changed",
                    DOMAIN.RuleArea.EVIDENCE,
                    DOMAIN.CheckOutcome.FAILED,
                    "docs/log.md",
                    diagnostic=diagnostic("locator.source.changed", "docs/log.md"),
                    failure_operation=DOMAIN.FailureOperation.OBSERVE,
                ),
                DOMAIN.RuleCheck(
                    "evidence:item",
                    DOMAIN.RuleArea.EVIDENCE,
                    DOMAIN.CheckOutcome.FAILED,
                    "evidence:item",
                    dependencies=("source:changed",),
                    diagnostic=diagnostic("locator.source.changed", "evidence:item"),
                    failure_operation=DOMAIN.FailureOperation.OBSERVE,
                ),
                DOMAIN.RuleCheck(
                    "provenance:item",
                    DOMAIN.RuleArea.PROVENANCE,
                    DOMAIN.CheckOutcome.FAILED,
                    "provenance:item",
                    dependencies=("evidence:item",),
                    diagnostic=diagnostic(
                        "provenance.observation.unavailable", "provenance:item"
                    ),
                    failure_operation=DOMAIN.FailureOperation.OBSERVE,
                ),
            ),
        )

        outcomes = {check.check_id: check.outcome for check in attempt.checks}
        self.assertEqual(outcomes["source:changed"], DOMAIN.CheckOutcome.FAILED)
        self.assertEqual(outcomes["evidence:item"], DOMAIN.CheckOutcome.BLOCKED)
        self.assertEqual(outcomes["provenance:item"], DOMAIN.CheckOutcome.BLOCKED)
        self.assertFalse(attempt.findings)
        snapshot = DOMAIN.ValidationSnapshot.from_attempt(attempt)
        self.assertEqual(snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED)
        self.assertEqual(
            tuple(item.check_id for item in snapshot.failed_checks),
            ("source:changed",),
        )
        self.assertEqual(
            {item.check_id: item.blocked_by for item in snapshot.blocked_checks},
            {
                "evidence:item": ("source:changed",),
                "provenance:item": ("source:changed",),
            },
        )

    def test_stable_failed_prerequisite_keeps_dependent_coverage_complete(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "conformance:command",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.FINDING,
                    "command",
                    diagnostic=diagnostic("command.invalid", "command"),
                ),
                DOMAIN.RuleCheck(
                    "provenance:output",
                    DOMAIN.RuleArea.PROVENANCE,
                    DOMAIN.CheckOutcome.BLOCKED,
                    "output",
                    dependencies=("conformance:command",),
                ),
            ),
        )

        snapshot = DOMAIN.ValidationSnapshot.from_attempt(attempt)
        self.assertEqual(
            snapshot.blocked_checks[0].blocked_by,
            ("conformance:command",),
        )
        self.assertEqual(
            tuple(finding.finding_id for finding in snapshot.findings),
            ("conformance:command",),
        )

    def test_snapshot_is_strictly_canonical_and_contains_no_private_check_status(
        self,
    ) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="2026-09-13T12:00:00Z",
            finished_at="2026-09-13T12:00:01Z",
            checks=(
                DOMAIN.RuleCheck(
                    "conformance:log",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.PASS,
                    "conformance:log",
                ),
            ),
            metrics={"reads": 1},
        )

        snapshot = DOMAIN.ValidationSnapshot.from_attempt(attempt)
        payload = snapshot.as_dict()

        self.assertEqual(snapshot.outcome, DOMAIN.SnapshotOutcome.CLEAR)
        self.assertEqual(payload["passed_check_count"], 1)
        self.assertEqual(payload["blocked_checks"], [])
        self.assertEqual(payload["failed_checks"], [])
        self.assertNotIn("checks", payload)
        self.assertNotIn("status", snapshot.canonical_json())

        loaded = DOMAIN.ValidationSnapshot.from_json(snapshot.canonical_json())
        self.assertEqual(loaded.canonical_json(), snapshot.canonical_json())

    def test_snapshot_reader_rejects_extra_fields_and_duplicate_json_keys(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "conformance:log",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.PASS,
                    "conformance:log",
                ),
            ),
        )
        payload = DOMAIN.ValidationSnapshot.from_attempt(attempt).as_dict()
        payload["legacy_status"] = "complete_clear"

        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.ValidationSnapshot.from_dict(payload)
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.ValidationSnapshot.from_json('{"schema":"one","schema":"two"}')

    def test_graph_capacity_exhaustion_fails_the_validation_operation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            with self.assertRaisesRegex(ValueError, "capacity was exceeded"):
                ENGINE.evaluate_mechanical(
                    ENGINE.EvaluationRequest(
                        summary,
                        graph_max_nodes=1,
                    )
                )

    def test_attempt_rejects_a_forged_finding_projection(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "conformance:bad",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.FINDING,
                    "bad",
                    diagnostic=diagnostic("conformance.bad", "bad"),
                ),
            ),
        )

        with self.assertRaises(DOMAIN.ValidationDomainError):
            replace(
                attempt,
                findings=(replace(attempt.findings[0], rule="forged rule"),),
            )

    def test_snapshot_rejects_a_forged_batch_projection(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "orphan:unused",
                    DOMAIN.RuleArea.ORPHAN,
                    DOMAIN.CheckOutcome.FINDING,
                    "unused",
                    diagnostic=diagnostic("orphan.unused", "unused"),
                ),
            ),
        )
        snapshot = DOMAIN.ValidationSnapshot.from_attempt(attempt)

        with self.assertRaises(DOMAIN.ValidationDomainError):
            replace(
                snapshot,
                batches=(replace(snapshot.batches[0], rationale=("forged",)),),
            )

    def test_snapshot_rejects_an_outcome_that_ignores_findings(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "orphan:unused",
                    DOMAIN.RuleArea.ORPHAN,
                    DOMAIN.CheckOutcome.FINDING,
                    "unused",
                    diagnostic=diagnostic("orphan.unused", "unused"),
                ),
            ),
        )
        snapshot = DOMAIN.ValidationSnapshot.from_attempt(attempt)

        with self.assertRaisesRegex(
            DOMAIN.ValidationDomainError,
            "outcome does not match",
        ):
            replace(
                snapshot,
                outcome=DOMAIN.SnapshotOutcome.CLEAR,
            )

    def test_snapshot_identity_covers_authoritative_context(self) -> None:
        attempt = DOMAIN.ValidationAttempt.build(
            target=target(),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "conformance:log",
                    DOMAIN.RuleArea.CONFORMANCE,
                    DOMAIN.CheckOutcome.PASS,
                    "conformance:log",
                ),
            ),
        )

        first = DOMAIN.ValidationSnapshot.from_attempt(
            attempt,
            repair_context={"relationships": []},
            report_context={"entries": ["e001"]},
        )
        second = DOMAIN.ValidationSnapshot.from_attempt(
            attempt,
            repair_context={"relationships": [{"kind": "material"}]},
            report_context={"entries": ["e001"]},
        )

        self.assertNotEqual(first.internal_snapshot_id, second.internal_snapshot_id)
        with self.assertRaisesRegex(
            DOMAIN.ValidationDomainError,
            "snapshot identity does not match",
        ):
            replace(second, internal_snapshot_id=first.internal_snapshot_id)

    def test_closed_contracts_reject_invalid_cross_references(self) -> None:
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.RuleCheck(
                "failed",
                DOMAIN.RuleArea.CONFORMANCE,
                DOMAIN.CheckOutcome.FINDING,
                "failed",
            )
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.RuleCheck(
                "passing",
                DOMAIN.RuleArea.CONFORMANCE,
                DOMAIN.CheckOutcome.PASS,
                "passing",
                diagnostic=diagnostic("not.allowed", "passing"),
            )
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.ValidationAttempt(
                target(),
                "source-1",
                "rules-1",
                "start",
                "finish",
                (
                    DOMAIN.RuleCheck(
                        "dependent",
                        DOMAIN.RuleArea.CONFORMANCE,
                        DOMAIN.CheckOutcome.BLOCKED,
                        "dependent",
                        dependencies=("missing",),
                    ),
                ),
                (),
                {},
            )
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.ValidationAttempt.build(
                target=target(),
                source_identity="source-1",
                rules_version="rules-1",
                started_at="start",
                finished_at="finish",
                checks=(
                    DOMAIN.RuleCheck(
                        "cycle:a",
                        DOMAIN.RuleArea.CONFORMANCE,
                        DOMAIN.CheckOutcome.BLOCKED,
                        "cycle:a",
                        dependencies=("cycle:b",),
                    ),
                    DOMAIN.RuleCheck(
                        "cycle:b",
                        DOMAIN.RuleArea.CONFORMANCE,
                        DOMAIN.CheckOutcome.BLOCKED,
                        "cycle:b",
                        dependencies=("cycle:a",),
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
