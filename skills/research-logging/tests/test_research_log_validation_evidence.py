from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
EVIDENCE = importlib.import_module("validation.evidence")


def structure(path: Path) -> dict[str, str]:
    return {"status": "ok", "type": path.suffix.lstrip(".")}


class EvidenceLocatorTests(unittest.TestCase):
    def test_numeric_equivalence_applies_declared_binary_unit_conversion(self) -> None:
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "3.5 GiB",
                [3540.4],
                "MiB to GiB and round to one decimal place",
            )
        )

    def test_numeric_equivalence_does_not_infer_percent_scaling(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence("20%", [2000])

        self.assertEqual(status, "unresolved")
        self.assertIn("not declared", detail)

    def test_numeric_equivalence_applies_declared_fraction_to_percent(self) -> None:
        status, _detail = EVIDENCE.numeric_equivalence(
            "68%", [0.676], "Converted to percent and rounded"
        )

        self.assertEqual(status, "pass")

    def test_numeric_equivalence_does_not_infer_thousands_scaling(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence("20k", [20_000])

        self.assertEqual(status, "unresolved")
        self.assertIn("not declared", detail)

    def test_numeric_equivalence_applies_declared_thousands_scaling(self) -> None:
        status, _detail = EVIDENCE.numeric_equivalence(
            "23.4k",
            [23_377.435685875138],
            "Divided by 1,000, rounded to one decimal, and added k suffix",
        )

        self.assertEqual(status, "pass")

    def test_numeric_equivalence_does_not_reuse_one_retained_value(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence("1.0 and 1.0", [1.0])

        self.assertEqual(status, "unresolved")
        self.assertIn("cardinality differs", detail)

    def test_numeric_equivalence_matches_repeated_values_one_to_one(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence("1.0 and 1.0", [1.0, 1.0])

        self.assertEqual(status, "pass")
        self.assertIn("one-to-one", detail)

    def test_numeric_equivalence_requires_retained_unit_context(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence("10 ms", [10.0])

        self.assertEqual(status, "unresolved")
        self.assertIn("units are not established", detail)

        supported, _detail = EVIDENCE.numeric_equivalence(
            "10 ms", [10.0], retained_context="field=latency_ms"
        )
        self.assertEqual(supported, "pass")

    def test_mechanical_numeric_match_requires_semantic_context_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text("metric,value\nrms,1.25\n", encoding="utf-8")

            result = EVIDENCE.mechanical_evidence_support(
                {
                    "kind": "statistic",
                    "evidence": "`1.3`",
                    "transformation": "round to one decimal place",
                },
                {
                    "status": "resolved",
                    "source": str(path),
                    "path": str(path),
                    "locator": "where.metric=rms; field=value",
                },
                structure,
            )

            self.assertEqual(result["status"], "unresolved")
            self.assertIn("matched 1 numeric value", result["detail"])
            self.assertIn("requires semantic field/context", result["detail"])

    def test_numeric_equivalence_rejects_dimensionally_unrelated_units(self) -> None:
        for presented, retained in (
            ("1 kg", "1 m"),
            ("500 K", "500 W"),
            ("12 pixels", "12 seconds"),
        ):
            with self.subTest(presented=presented, retained=retained):
                status, detail = EVIDENCE.numeric_equivalence(
                    presented,
                    [retained],
                    retained_context=f"field={retained.split()[-1]}",
                )
                self.assertEqual(status, "unresolved")
                self.assertIn("units are not established", detail)

    def test_numeric_equivalence_rejects_unknown_unit_like_suffix(self) -> None:
        status, detail = EVIDENCE.numeric_equivalence(
            "12 widgets", [12], retained_context="field=widget_count"
        )

        self.assertEqual(status, "unresolved")
        self.assertIn("outside the mechanical vocabulary", detail)

    def test_csv_locator_selects_named_rows_and_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text(
                "case,metric,value\na,rms,1.25\nb,rms,2.5\n",
                encoding="utf-8",
            )

            status, values, detail = EVIDENCE.locator_values(
                path,
                "where.case=b; field=value",
                structure,
            )

            self.assertEqual(status, "ok")
            self.assertEqual(values, ["2.5"])
            self.assertIn("selected 1 row", detail)

    def test_json_locator_rejects_unbounded_compound_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.json"
            path.write_text('{"results": [{"value": 1.0}]}', encoding="utf-8")

            status, values, detail = EVIDENCE.locator_values(
                path,
                "path=$.results",
                structure,
            )

            self.assertEqual(status, "unresolved")
            self.assertEqual(values, [])
            self.assertIn("compound value", detail)

    def test_table_equivalence_preserves_labels_and_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text(
                "name,value\nalpha,1.0\nbeta,2.0\n",
                encoding="utf-8",
            )

            status, detail = EVIDENCE.table_equivalence(
                path,
                "",
                "name | value\n--- | ---:\nalpha | `2.0`\nbeta | `1.0`",
            )

            self.assertEqual(status, "unresolved")
            self.assertIn("do not align", detail)

    def test_table_equivalence_requires_equal_row_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text(
                "name,value\nalpha,1.0\nbeta,2.0\n",
                encoding="utf-8",
            )

            status, detail = EVIDENCE.table_equivalence(
                path,
                "",
                "name | value\n--- | ---:\nalpha | `1.0`",
            )

            self.assertEqual(status, "unresolved")
            self.assertIn("cardinality differs", detail)

    def test_csv_locator_stops_at_bounded_row_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text("case,value\na,1\nb,2\nc,3\n", encoding="utf-8")

            with mock.patch.object(EVIDENCE, "LOCATOR_ROW_LIMIT", 2):
                status, values, detail = EVIDENCE.locator_values(
                    path, "field=value", structure
                )

            self.assertEqual(status, "unresolved")
            self.assertEqual(values, [])
            self.assertIn("row limit", detail)

    def test_csv_locator_stops_before_materializing_excess_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.csv"
            path.write_text("case,value\na,1\nb,2\n", encoding="utf-8")

            with mock.patch.object(EVIDENCE, "LOCATOR_VALUE_LIMIT", 1):
                status, values, detail = EVIDENCE.locator_values(
                    path, "field=value", structure
                )

            self.assertEqual(status, "unresolved")
            self.assertEqual(values, [])
            self.assertIn("more than 1 values", detail)

    def test_json_locator_preflights_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.json"
            path.write_text('{"value": 1}', encoding="utf-8")

            with mock.patch.object(EVIDENCE, "LOCATOR_JSON_BYTE_LIMIT", 4):
                status, values, detail = EVIDENCE.locator_values(
                    path, "path=$.value", structure
                )

            self.assertEqual(status, "unresolved")
            self.assertEqual(values, [])
            self.assertIn("byte limit", detail)

    def test_text_comparison_preflights_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.log"
            path.write_text("retained output\n", encoding="utf-8")

            with mock.patch.object(EVIDENCE, "LOCATOR_TEXT_BYTE_LIMIT", 4):
                status, detail = EVIDENCE.normalized_text_equivalence(
                    path, "retained output"
                )

            self.assertEqual(status, "unresolved")
            self.assertIn("byte limit", detail)

    def test_array_value_limit_is_checked_before_materialization(self) -> None:
        class OversizedArray:
            shape = (EVIDENCE.LOCATOR_VALUE_LIMIT + 1,)
            size = EVIDENCE.LOCATOR_VALUE_LIMIT + 1

            def __getitem__(self, _key: object) -> object:
                raise AssertionError("oversized array must not be materialized")

        ok, values, detail = EVIDENCE._plain_values(OversizedArray())

        self.assertFalse(ok)
        self.assertEqual(values, [])
        self.assertIn("above the bounded limit", detail)


if __name__ == "__main__":
    unittest.main()
