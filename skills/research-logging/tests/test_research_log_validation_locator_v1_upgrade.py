from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from research_log_validation_test_support import write

V1 = importlib.import_module("validation.locator_v1_upgrade")
LEGACY = importlib.import_module("validation.evidence_v1_upgrade")


class LocatorV1UpgradeTests(unittest.TestCase):
    def test_source_expressions_and_locator_canonicalization_are_frozen(self) -> None:
        expressions = V1.parse_source_expressions(
            "data/a.csv :: kind=b|a; fields=id|value | data/b.json :: path=$.value"
        )

        self.assertEqual(len(expressions), 2)
        self.assertEqual(
            expressions[0].locator.canonical,
            "v1:kind=a|b; fields=id|value",
        )
        self.assertEqual(expressions[1].locator.canonical, "v1:path=$.value")

    def test_malformed_or_other_version_locators_fail_without_fallback(self) -> None:
        with self.assertRaisesRegex(
            V1.LocatorV1UpgradeError, "locator.v1.duplicate_clause"
        ):
            V1.parse_locator("item=a; item=b; field=value")
        with self.assertRaisesRegex(
            V1.LocatorV1UpgradeError, "locator.version.unsupported"
        ):
            V1.parse_locator('v2:{"path":[]}')
        with self.assertRaisesRegex(V1.LocatorV1UpgradeError, "locator.syntax.invalid"):
            V1.parse_locator("fields=a||b")

    def test_evaluation_is_isolated_and_returns_the_common_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(source, "case_id,score\n8,0.90\n15,0.95\n")
            locator = V1.parse_locator("case_id=15; field=score")

            result = V1.evaluate_locator(source, locator)

            self.assertEqual(result.effective_version, "v1")
            self.assertEqual(result.locator_identity, "v1:case_id=15; field=score")
            self.assertEqual(result.items[0].value.value, "0.95")

    def test_isolated_reader_contains_only_locator_owned_v1_behavior(self) -> None:
        for retired in (
            "mechanical_evidence_support",
            "normalized_text_equivalence",
            "numeric_equivalence",
            "table_equivalence",
        ):
            with self.subTest(retired=retired):
                self.assertFalse(hasattr(LEGACY, retired))


if __name__ == "__main__":
    unittest.main()
