from __future__ import annotations

import ast
import importlib
import re
import unittest
from pathlib import Path

import research_log_validation_test_support  # noqa: F401

HUMAN = importlib.import_module("validation.human_projection")
REPORT = importlib.import_module("validation.report")
RESULTS = importlib.import_module("validation.mechanical_results")


class HumanProjectionTests(unittest.TestCase):
    def test_catalog_covers_the_approved_emitted_code_inventory(self) -> None:
        self.assertEqual(len(HUMAN.CATALOG), 113)
        prefixes = {code.split(".", 1)[0] for code in HUMAN.CATALOG}
        candidates = set()
        scripts = Path(__file__).parents[1] / "scripts" / "validation"
        pattern = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}\Z")
        for path in scripts.glob("*.py"):
            if path.name == "human_projection.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and pattern.fullmatch(node.value)
                    and node.value.split(".", 1)[0] in prefixes
                ):
                    candidates.add(node.value)
        non_codes = {"locator.expect.identities", "locator.expect.shape"}
        self.assertLessEqual(candidates - non_codes, set(HUMAN.CATALOG))

    def test_human_report_bounds_each_issue_type_at_ten_targets(self) -> None:
        checks = []
        for number in range(11):
            subject = f"data/run-{number:04}.csv"
            checks.append(
                RESULTS.MechanicalCheck(
                    f"orphan:e001:{number:04}",
                    RESULTS.CheckScope.ORPHAN,
                    RESULTS.CheckStatus.FAIL,
                    subject,
                    failure=RESULTS.FailurePayload(
                        "orphan.material.unused", subject, {}, "Hygiene"
                    ),
                )
            )
        record = RESULTS.MechanicalGeneratedRecord.build(
            "/project/docs/study.md", "rules", "2026-09-05", checks
        )

        report = REPORT.compose_validation_report(record)

        self.assertIn("#### Unused Retained Material — 11 targets", report)
        self.assertEqual(report.count("Retained material is not used"), 10)
        self.assertIn("1 more target omitted", report)
        self.assertIn("log findings list --path <log> --entry e001", report)

    def test_incomplete_command_report_preserves_completed_area_results(self) -> None:
        unavailable = "evidence:e001:source"
        record = RESULTS.MechanicalGeneratedRecord.build(
            "/project/docs/study.md",
            "rules",
            "2026-09-05",
            (
                RESULTS.MechanicalCheck(
                    "conformance:log",
                    RESULTS.CheckScope.CONFORMANCE,
                    RESULTS.CheckStatus.PASS,
                    "summary",
                ),
                RESULTS.MechanicalCheck(
                    unavailable,
                    RESULTS.CheckScope.EVIDENCE,
                    RESULTS.CheckStatus.UNAVAILABLE,
                    "/project/docs/study/entries/e001/data/source.csv",
                    failure=RESULTS.FailurePayload(
                        "association.document_unavailable",
                        "/project/docs/study/entries/e001/data/source.csv",
                        {},
                        "Evidence",
                    ),
                ),
                RESULTS.MechanicalCheck(
                    "provenance:e001:source",
                    RESULTS.CheckScope.PROVENANCE,
                    RESULTS.CheckStatus.NOT_APPLICABLE,
                    "source",
                    ({"dependency": unavailable},),
                ),
            ),
        )
        context = HUMAN.ReportContext.empty(Path(record.summary))

        report = REPORT.compose_validation_command_report(
            record,
            context=context,
            published=False,
            human_report="/project/docs/study/validation.md",
            mechanical_report="/project/docs/study/validation/results.json",
        )

        self.assertIn("| Structure | Clear |", report)
        self.assertIn("| Evidence | Incomplete |", report)
        self.assertIn("| Provenance | — |", report)
        self.assertIn("Evidence: The declared evidence document", report)
        self.assertIn("Report: Not published.", report)


if __name__ == "__main__":
    unittest.main()
