"""Unconfirmed summary targets remain eligible for reproduction."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_log_cli_test_support import run_log

# isort: split
from log_commands.context import resolve_log
from log_commands.reproduction_planner import (
    _admit_validation,
    _blocked_validation_batches,
    _entry_validation_blockers,
    _require_resolved_validation_blockers,
)
from test_research_log_validation_engine import _evaluate, _log
from validation.batch_projection import build_batch_projection
from validation.engine import _summary_provenance
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
)


class SummaryConfirmationTests(unittest.TestCase):
    def test_unconfirmed_target_does_not_create_a_reproduction_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            summary, entry = _log(root)
            self.assertFalse(any(c.failure for c in _evaluate(summary).result.checks))
            path = entry.parent / "pyrun-outputs.json"
            support = json.loads(path.read_text())
            support["outputs"]["data/results.csv"]["confirmed"] = False
            path.write_text(json.dumps(support))

            evaluated = _evaluate(summary)
            checks = {c.identity: c for c in evaluated.result.checks}
            self.assertEqual(
                [c.failure.code for c in checks.values() if c.failure],
                ["provenance.output.reproduction_required"],
            )
            dependent = checks["provenance:summary:5"]
            self.assertEqual(dependent.status, CheckStatus.NOT_APPLICABLE)
            self.assertEqual(
                dependent.dependencies,
                ({"dependency": "provenance:e001:success-rate"},),
            )
            projection = build_batch_projection(
                evaluated.result,
                invocations=evaluated.scan["invocations"],
                registries=evaluated.scan["registries"],
                source_identity="fixture",
            )
            self.assertFalse(_blocked_validation_batches(projection))
            self.assertFalse(_entry_validation_blockers(projection))
            _require_resolved_validation_blockers(projection)

            logical = summary.with_suffix("")
            published = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(published.returncode, 0, published.stderr)
            _admit_validation(resolve_log(logical))

    def test_other_failures_and_unavailable_targets_remain_failures(self):
        for status, code in (
            (CheckStatus.FAIL, "provenance.output.unrecorded"),
            (CheckStatus.FAIL, "lineage.missing"),
            (CheckStatus.UNAVAILABLE, "provenance.observation.unavailable"),
        ):
            with self.subTest(code=code):
                target = MechanicalCheck(
                    "provenance:e001:result",
                    CheckScope.PROVENANCE,
                    status,
                    "/result.csv",
                    failure=FailurePayload(code, "/result.csv", {}, "rule"),
                )
                check = _summary_provenance(
                    "summary:5",
                    ("e001", "result"),
                    SimpleNamespace(provenance_check=target),
                )
                self.assertEqual(check.status, status)
                self.assertEqual(check.failure.code, "summary.reference.target_invalid")
                self.assertEqual(check.failure.dependency, target.identity)
