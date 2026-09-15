"""Unconfirmed summary targets remain eligible for reproduction."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_log_cli_test_support import run_log

# isort: split
from test_research_log_validation_engine import (
    _evaluate,
    _evaluate_current_fixture,
    _log,
)
from validation.domain import (
    CheckDiagnostic,
    CheckOutcome,
    FailureOperation,
    IssueContext,
    RuleArea,
    RuleCheck,
)
from validation.engine import EvaluationRequest, _summary_provenance


class SummaryConfirmationTests(unittest.TestCase):
    def test_unconfirmed_target_does_not_create_a_reproduction_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            summary, entry = _log(root)
            self.assertFalse(
                any(c.diagnostic for c in _evaluate(summary).attempt.checks)
            )
            path = entry.parent / "pyrun-outputs.json"
            support = json.loads(path.read_text())
            support["outputs"]["data/results.csv"]["confirmed"] = False
            path.write_text(json.dumps(support))

            evaluated = _evaluate(summary)
            checks = {c.check_id: c for c in evaluated.attempt.checks}
            self.assertFalse(any(c.diagnostic for c in checks.values()))
            dependent = checks["provenance:summary:5"]
            self.assertEqual(dependent.outcome, CheckOutcome.PASS)
            self.assertTrue(evaluated.context.currentness)
            current = _evaluate_current_fixture(
                EvaluationRequest(summary)
            )
            snapshot = current.snapshot
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertFalse(snapshot.findings)
            self.assertFalse(snapshot.blocked_checks)

            logical = summary.with_suffix("")
            published = run_log(root, "validate", "run", "--path", str(logical))
            self.assertEqual(published.returncode, 0, published.stderr)

    def test_finding_and_failed_targets_block_summary_provenance(self):
        for outcome, code in (
            (CheckOutcome.FINDING, "provenance.output.unrecorded"),
            (CheckOutcome.FINDING, "lineage.missing"),
            (CheckOutcome.FAILED, "provenance.observation.unavailable"),
        ):
            with self.subTest(code=code):
                target = RuleCheck(
                    "provenance:e001:result",
                    RuleArea.PROVENANCE,
                    outcome,
                    "/result.csv",
                    diagnostic=CheckDiagnostic(
                        code, "/result.csv", "rule", {}
                    ),
                    issue_context=IssueContext(),
                    failure_operation=(
                        FailureOperation.OBSERVE
                        if outcome is CheckOutcome.FAILED
                        else None
                    ),
                )
                check = _summary_provenance(
                    "summary:5",
                    ("e001", "result"),
                    SimpleNamespace(provenance_check=target),
                    IssueContext(),
                )
                self.assertEqual(check.outcome, CheckOutcome.BLOCKED)
                self.assertIsNone(check.diagnostic)
                self.assertEqual(check.dependencies, (target.check_id,))
                self.assertEqual(check.rule, "Summary Association")
