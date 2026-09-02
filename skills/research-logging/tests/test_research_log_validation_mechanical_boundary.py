from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from typing import Any

import research_log_validation_test_support  # noqa: F401

MECHANICAL = importlib.import_module("validation.mechanical")


class MechanicalBoundaryTests(unittest.TestCase):
    def test_entry_point_composes_scan_and_evaluation_without_agent_state(self) -> None:
        calls: list[tuple[str, object]] = []
        request = MECHANICAL.MechanicalEvaluationRequest(
            Path("docs/log.md"), "2026-08-28"
        )

        def scan(actual: Any) -> tuple[dict[str, object], dict[str, object]]:
            calls.append(("scan", actual))
            return {"summary": "docs/log.md"}, {"files_hashed": 2}

        def evaluate(actual: Any, result_date: str) -> dict[str, object]:
            calls.append(("evaluate", (actual, result_date)))
            return {"status": "complete"}

        result = MECHANICAL.evaluate_mechanical(
            request,
            MECHANICAL.MechanicalEvaluationPolicy(scan, evaluate),
        )

        self.assertEqual(result.result, {"status": "complete"})
        self.assertEqual(result.scan, {"summary": "docs/log.md"})
        self.assertEqual(result.metrics, {"files_hashed": 2})
        self.assertEqual([name for name, _ in calls], ["scan", "evaluate"])


if __name__ == "__main__":
    unittest.main()
