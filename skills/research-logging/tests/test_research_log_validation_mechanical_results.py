from __future__ import annotations

import importlib
import unittest

import research_log_validation_test_support  # noqa: F401

RESULTS = importlib.import_module("validation.mechanical_results")


def check(
    identity: str,
    scope: object,
    status: object,
    *,
    code: str | None = None,
    dependency: str | None = None,
) -> object:
    failure = None
    if code is not None:
        failure = RESULTS.FailurePayload(
            code=code,
            subject=identity,
            observed={"state": "observed"},
            rule="Mechanical Validation Evaluation And Outcomes",
            dependency=dependency,
        )
    return RESULTS.MechanicalCheck(
        identity=identity,
        scope=scope,
        status=status,
        subject=identity,
        failure=failure,
    )


class MechanicalResultTests(unittest.TestCase):
    def test_evidence_and_provenance_remain_independent(self) -> None:
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/log.md",
            "mechanical-v2",
            "2026-08-28",
            [
                check(
                    "record:evidence",
                    RESULTS.CheckScope.EVIDENCE,
                    RESULTS.CheckStatus.PASS,
                ),
                check(
                    "record:provenance",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.FAIL,
                    code="producer.missing",
                ),
            ],
        )

        scopes = {item.scope: item.status for item in record.scopes}
        self.assertEqual(scopes[RESULTS.CheckScope.EVIDENCE], RESULTS.CheckStatus.PASS)
        self.assertEqual(
            scopes[RESULTS.CheckScope.PROVENANCE], RESULTS.CheckStatus.FAIL
        )
        self.assertEqual(record.completion, RESULTS.CompletionState.COMPLETE_FINDINGS)

    def test_unavailable_makes_completion_incomplete(self) -> None:
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/log.md",
            "mechanical-v2",
            "2026-08-28",
            [
                check(
                    "source:changed",
                    RESULTS.CheckScope.EVIDENCE,
                    RESULTS.CheckStatus.UNAVAILABLE,
                    code="locator.source.changed",
                )
            ],
        )

        self.assertEqual(record.completion, RESULTS.CompletionState.INCOMPLETE)

    def test_failure_payload_contains_no_repair_scaffold(self) -> None:
        failure = RESULTS.FailurePayload(
            code="producer.missing",
            subject="data/result.csv",
            observed={"producers": 0},
            rule="Producer And Upstream Lineage",
            dependency="association:result",
        )

        projected = failure.as_dict()

        self.assertEqual(projected["code"], "producer.missing")
        self.assertEqual(projected["subject"], "data/result.csv")
        self.assertNotIn("repair", projected)
        self.assertNotIn("suggestion", projected)

    def test_public_record_round_trips_strict_json(self) -> None:
        record = RESULTS.MechanicalGeneratedRecord.build(
            "docs/log.md",
            "mechanical-v2",
            "2026-08-28",
            [
                check(
                    "evidence:item",
                    RESULTS.CheckScope.EVIDENCE,
                    RESULTS.CheckStatus.PASS,
                )
            ],
        )

        decoded = RESULTS.MechanicalGeneratedRecord.from_json(record.canonical_json())

        self.assertEqual(decoded, record)
        with self.assertRaises(RESULTS.MechanicalResultContractError):
            RESULTS.MechanicalGeneratedRecord.from_json(
                record.canonical_json().replace(
                    '"schema":', '"schema":"duplicate","schema":', 1
                )
            )

    def test_check_contract_rejects_missing_or_extra_failure(self) -> None:
        with self.assertRaises(RESULTS.MechanicalResultContractError):
            check(
                "failed",
                RESULTS.CheckScope.CONFORMANCE,
                RESULTS.CheckStatus.FAIL,
            )
        with self.assertRaises(RESULTS.MechanicalResultContractError):
            check(
                "passing",
                RESULTS.CheckScope.CONFORMANCE,
                RESULTS.CheckStatus.PASS,
                code="not.allowed",
            )


if __name__ == "__main__":
    unittest.main()
