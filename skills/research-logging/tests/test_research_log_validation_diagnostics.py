from __future__ import annotations

import importlib
import unittest

import research_log_validation_test_support  # noqa: F401

DIAGNOSTICS = importlib.import_module("validation.diagnostics")


def exact_producer_item() -> dict:
    target = "docs/mini/entries/e001/data/result.csv"
    return {
        "kind": "semantic_fallback",
        "entry": "e001",
        "identity": target,
        "hard_failures": [],
        "integrity_status": "pass",
        "workflow": {"status": "unresolved"},
        "evidence": [{"result": {"status": "pass"}}],
        "producer_candidates": [
            {
                "invocation": "e001:1",
                "normalized_command": "python produce.py --output result.csv",
                "coverage_kind": "exact-target",
                "coverage_identity": target,
                "target_member": None,
                "path_arguments": [
                    {
                        "exists": True,
                        "role_hint": "output",
                    }
                ],
            }
        ],
    }


class ValidationDiagnosticsTests(unittest.TestCase):
    def test_structured_summary_combines_lifecycle_reuse_and_pages(self) -> None:
        diagnostics = DIAGNOSTICS.ValidationDiagnostics()
        diagnostics.record_queue(
            "initial_adjudication",
            [exact_producer_item(), {"kind": "collection_scope"}],
        )
        diagnostics.record_reuse(
            {
                "questions_considered": 2,
                "answers_found": 1,
                "misses_by_reason": {"subject_not_found": 1},
            },
            items_before=2,
            items_after=1,
        )
        diagnostics.record_page(
            {
                "page_diagnostics": {
                    "page_number": 1,
                    "item_count": 1,
                    "packet_bytes": 512,
                }
            }
        )
        diagnostics.record_page_acceptance(
            {
                "accepted_page_diagnostics": {
                    "page_number": 1,
                    "item_count": 1,
                    "review_wait_seconds": 2.5,
                }
            }
        )

        result = diagnostics.as_dict()

        self.assertGreaterEqual(result["execution_seconds"], 0)
        self.assertEqual(result["review_wait_seconds"], 2.5)
        self.assertEqual(
            result["lifecycle"][0]["items_by_kind"],
            {"collection_scope": 1, "semantic_fallback": 1},
        )
        self.assertEqual(result["reuse"]["items_removed"], 1)
        self.assertEqual(result["reuse"]["misses_by_reason"]["subject_not_found"], 1)
        self.assertEqual(result["pages"][0]["packet_bytes"], 512)


if __name__ == "__main__":
    unittest.main()
