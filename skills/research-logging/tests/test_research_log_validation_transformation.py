from __future__ import annotations

import importlib
import struct
from decimal import Decimal
from pathlib import Path

from research_log_validation_test_support import unittest

VALUES = importlib.import_module("validation.mechanical_values")
TRANSFORM = importlib.import_module("validation.transformation")
V2JSON = importlib.import_module("validation.json_codec")
CORPUS = Path(__file__).parent / "fixtures" / "transformation-retained-corpus.json"


def _selection(
    *values: object,
    records: tuple[int, ...] | None = None,
    identities: tuple[tuple[object, ...], ...] = (),
) -> object:
    canonical = tuple(_canonical(value) for value in values)
    items = tuple(
        VALUES.SelectionItem(
            ("item", index),
            value,
            record=records[index] if records is not None else None,
        )
        for index, value in enumerate(canonical)
    )
    return VALUES.SelectionResult(
        locator_identity="v2:test",
        source_identity="sha256:test",
        source_profile="fixture",
        items=items,
        matches=len(set(records)) if records is not None else len(items),
        membership=tuple(f"member-{index}" for index in range(len(items))),
        identities=tuple(
            tuple(_canonical(value) for value in identity) for identity in identities
        ),
        dependency_projection="selection:test",
    )


def _canonical(value: object) -> object:
    if isinstance(value, VALUES.CanonicalValue):
        return value
    if value is None:
        return VALUES.null_value()
    if isinstance(value, bool):
        return VALUES.boolean_value(value)
    if isinstance(value, int):
        return VALUES.integer_value(value)
    if isinstance(value, Decimal):
        return VALUES.decimal_value(value)
    if isinstance(value, str):
        return VALUES.string_value(value)
    raise TypeError(value)


def _value(item: int, *, places: int = 2, input_index: int = 0) -> dict:
    return {
        "parse": "decimal",
        "render": {"decimal_places": places, "mode": "fixed"},
        "source": {"input": input_index, "item": item},
    }


class TransformationV2ScalarTests(unittest.TestCase):
    def test_percentage_default_and_override_are_exact(self) -> None:
        default = TRANSFORM.evaluate_transformation(
            {"form": "percentage", "source": {"input": 0, "item": 0}},
            [_selection("0.676")],
            presentation_kind="statistic",
        )
        explicit = TRANSFORM.evaluate_transformation(
            {
                "decimal_places": 2,
                "form": "percentage",
                "source": {"input": 0, "item": 0},
            },
            [_selection("0.676")],
            presentation_kind="statistic",
        )

        self.assertEqual(default.accepted_spellings, ("67.6%",))
        self.assertEqual(explicit.accepted_spellings, ("67.60%",))
        self.assertNotIn("decimal_places", default.identity)
        TRANSFORM.compare_presentation(
            default, presented_kind="statistic", presented="67.6%"
        )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error,
            "transformation.presentation.mismatch",
        ):
            TRANSFORM.compare_presentation(
                default, presented_kind="statistic", presented="67.60 %"
            )

    def test_range_plus_minus_interval_and_tuple_have_closed_spelling(self) -> None:
        range_result = TRANSFORM.evaluate_transformation(
            {
                "form": "range",
                "unit": "ms",
                "values": [_value(0), _value(1)],
            },
            [_selection("3.417", "4.184")],
            presentation_kind="statistic",
        )
        plus_minus = TRANSFORM.evaluate_transformation(
            {
                "form": "plus_minus",
                "unit": "mas",
                "values": [_value(0), _value(1)],
            },
            [_selection("3.417", "0.084")],
            presentation_kind="statistic",
        )
        interval = TRANSFORM.evaluate_transformation(
            {
                "form": "interval",
                "unit": "%",
                "values": [
                    _value(0, places=1),
                    _value(1, places=1),
                    _value(2, places=1),
                ],
            },
            [_selection("67.55", "64.14", "70.84")],
            presentation_kind="statistic",
        )
        tuple_result = TRANSFORM.evaluate_transformation(
            {"form": "tuple", "values": [_value(0), _value(1)]},
            [_selection("0.314", "0.474")],
            presentation_kind="statistic",
        )

        self.assertEqual(range_result.accepted_spellings, ("3.42–4.18 ms",))
        self.assertEqual(
            plus_minus.accepted_spellings,
            ("3.42 ± 0.08 mas", "3.42 +/- 0.08 mas"),
        )
        for spelling in plus_minus.accepted_spellings:
            TRANSFORM.compare_presentation(
                plus_minus,
                presented_kind="statistic",
                presented=spelling,
            )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error,
            "transformation.presentation.mismatch",
        ):
            TRANSFORM.compare_presentation(
                plus_minus,
                presented_kind="statistic",
                presented="3.42±0.08 mas",
            )
        self.assertEqual(interval.accepted_spellings, ("67.6 [64.1, 70.8]%",))
        self.assertEqual(tuple_result.accepted_spellings, ("(0.31, 0.47)",))

    def test_units_grouping_sign_and_scientific_rendering(self) -> None:
        grouped = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "parse": "decimal",
                        "render": {"mode": "grouped_integer"},
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection("3270000")],
            presentation_kind="statistic",
        )
        multiplier = TRANSFORM.evaluate_transformation(
            {"form": "scalar", "unit": "x", "values": [_value(0)]},
            [_selection("3.38682391")],
            presentation_kind="statistic",
        )
        signed = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "unit": "%",
                "values": [
                    {
                        **_value(0),
                        "render": {
                            "decimal_places": 2,
                            "mode": "fixed",
                            "sign": "always",
                        },
                    }
                ],
            },
            [_selection("0.0519")],
            presentation_kind="statistic",
        )
        scientific = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "parse": "decimal",
                        "render": {
                            "mode": "scientific",
                            "significant_figures": 3,
                        },
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection("0.00000000000004255")],
            presentation_kind="statistic",
        )

        self.assertEqual(grouped.accepted_spellings, ("3,270,000",))
        self.assertEqual(multiplier.accepted_spellings, ("3.39x",))
        self.assertEqual(signed.accepted_spellings, ("+0.05%",))
        self.assertEqual(scientific.accepted_spellings, ("4.26e-14",))

    def test_round_half_even_and_exact_binary_float_bits(self) -> None:
        two = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "render": {"decimal_places": 0, "mode": "fixed"},
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection(Decimal("2.5"))],
            presentation_kind="statistic",
        )
        four = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "render": {"decimal_places": 0, "mode": "fixed"},
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection(Decimal("3.5"))],
            presentation_kind="statistic",
        )
        binary = VALUES.binary_float_value(64, struct.pack(">d", 1.5))
        rendered = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "render": {"decimal_places": 2, "mode": "fixed"},
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection(binary)],
            presentation_kind="statistic",
        )

        self.assertEqual(two.accepted_spellings, ("2",))
        self.assertEqual(four.accepted_spellings, ("4",))
        self.assertEqual(rendered.accepted_spellings, ("1.50",))

    def test_type_consumption_and_kind_failures_are_explicit(self) -> None:
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error, "transformation.input.unused"
        ):
            TRANSFORM.evaluate_transformation(
                {"form": "scalar", "values": [_value(0)]},
                [_selection("1.0", "2.0")],
                presentation_kind="statistic",
            )

    def test_magnitude_scale_significant_zero_and_negative_zero(self) -> None:
        significant = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "magnitude": True,
                        "parse": "decimal",
                        "render": {
                            "mode": "significant",
                            "significant_figures": 3,
                        },
                        "scale": Decimal("0.1"),
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection("-123.45")],
            presentation_kind="statistic",
        )
        negative_zero = VALUES.binary_float_value(64, bytes.fromhex("8000000000000000"))
        zero = TRANSFORM.evaluate_transformation(
            {
                "form": "scalar",
                "values": [
                    {
                        "render": {
                            "mode": "scientific",
                            "sign": "always",
                            "significant_figures": 3,
                        },
                        "source": {"input": 0, "item": 0},
                    }
                ],
            },
            [_selection(negative_zero)],
            presentation_kind="statistic",
        )

        self.assertEqual(significant.accepted_spellings, ("12.3",))
        self.assertEqual(zero.accepted_spellings, ("+0.00e0",))

    def test_unknown_fields_partial_parse_and_nonintegral_integer_fail(self) -> None:
        for recipe, selected, code in (
            (
                {
                    "form": "percentage",
                    "source": {"input": 0, "item": 0},
                    "unit": "%",
                },
                "1.2",
                "transformation.syntax.invalid",
            ),
            (
                {
                    "form": "scalar",
                    "values": [
                        {
                            "parse": "decimal",
                            "render": {"decimal_places": 1, "mode": "fixed"},
                            "source": {"input": 0, "item": 0},
                        }
                    ],
                },
                "1.2 trailing",
                "transformation.parse_failed",
            ),
            (
                {
                    "form": "scalar",
                    "values": [
                        {
                            "parse": "decimal",
                            "render": {"mode": "integer"},
                            "source": {"input": 0, "item": 0},
                        }
                    ],
                },
                "1.2",
                "transformation.render.invalid",
            ),
        ):
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(TRANSFORM.TransformationV2Error, code),
            ):
                TRANSFORM.evaluate_transformation(
                    recipe,
                    [_selection(selected)],
                    presentation_kind="statistic",
                )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error, "transformation.input.reused"
        ):
            TRANSFORM.evaluate_transformation(
                {"form": "tuple", "values": [_value(0), _value(0)]},
                [_selection("1.0")],
                presentation_kind="statistic",
            )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error, "transformation.type.mismatch"
        ):
            TRANSFORM.evaluate_transformation(
                {"form": "text", "values": [{"source": {"input": 0, "item": 0}}]},
                [_selection(None)],
                presentation_kind="output",
            )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error, "association.kind_mismatch"
        ):
            TRANSFORM.evaluate_transformation(
                {"form": "text", "values": [{"source": {"input": 0, "item": 0}}]},
                [_selection("complete output")],
                presentation_kind="statistic",
            )


class TransformationV2TableTests(unittest.TestCase):
    def test_direct_table_and_strict_markdown_comparison(self) -> None:
        recipe = {
            "columns": [
                {"form": "text"},
                {
                    "form": "scalar",
                    "unit": "%",
                    "value": {
                        "parse": "decimal",
                        "render": {"decimal_places": 2, "mode": "fixed"},
                    },
                },
            ],
            "form": "table",
            "headings": ["Case", "Error"],
            "mode": "direct",
        }
        result = TRANSFORM.evaluate_transformation(
            recipe,
            [_selection("case-8", "1.118", "case-15", "1.143", records=(0, 0, 1, 1))],
            presentation_kind="table",
        )

        self.assertEqual(result.rows, (("case-8", "1.12%"), ("case-15", "1.14%")))
        TRANSFORM.compare_presentation(
            result,
            presented_kind="table",
            presented=(
                "| Case | Error |\n"
                "| :--- | ---: |\n"
                "| case-8 | `1.12%` |\n"
                "| case-15 | 1.14% |"
            ),
        )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error,
            "transformation.presentation.mismatch",
        ) as raised:
            TRANSFORM.compare_presentation(
                result,
                presented_kind="table",
                presented="Case | Error\n--- | ---\ncase-8 | 1.1%\ncase-15 | 1.14%",
            )
        self.assertEqual(raised.exception.observed["difference_count"], 1)
        self.assertEqual(
            raised.exception.observed["differences"],
            [
                {
                    "location": "cell",
                    "row": 1,
                    "column": 2,
                    "expected": "1.12%",
                    "observed": "1.1%",
                }
            ],
        )
        self.assertFalse(raised.exception.observed["differences_truncated"])

    def test_direct_table_accepts_one_rectangular_canonical_array(self) -> None:
        array = VALUES.array_value(
            tuple(
                _canonical(value) for value in ("case-8", "1.118", "case-15", "1.143")
            ),
            shape=(2, 2),
            dtype="mixed",
        )
        result = TRANSFORM.evaluate_transformation(
            {
                "columns": [
                    {"form": "text"},
                    {
                        "form": "scalar",
                        "unit": "%",
                        "value": {
                            "parse": "decimal",
                            "render": {"decimal_places": 2, "mode": "fixed"},
                        },
                    },
                ],
                "form": "table",
                "headings": ["Case", "Error"],
                "mode": "direct",
            },
            [_selection(array)],
            presentation_kind="table",
        )

        self.assertEqual(
            result.rows,
            (("case-8", "1.12%"), ("case-15", "1.14%")),
        )

    def test_structured_range_and_explicit_identity_order(self) -> None:
        def field_value(field: int) -> dict:
            value = _value(0)
            value["source"] = {"field": field, "input": 0}
            return value

        recipe = {
            "columns": [
                {"form": "text", "values": [{"source": {"field": 0, "input": 0}}]},
                {
                    "form": "range",
                    "unit": "%",
                    "values": [field_value(1), field_value(2)],
                },
            ],
            "form": "table",
            "headings": ["Case", "Error range"],
            "mode": "structured",
            "rows": {"input": 0, "order": [["case-15"], ["case-8"]]},
        }
        source = _selection(
            "case-8",
            "1.118",
            "1.449",
            "case-15",
            "1.143",
            "1.319",
            records=(0, 0, 0, 1, 1, 1),
            identities=(("case-8",), ("case-15",)),
        )
        result = TRANSFORM.evaluate_transformation(
            recipe, [source], presentation_kind="table"
        )

        self.assertEqual(
            result.rows,
            (("case-15", "1.14–1.32%"), ("case-8", "1.12–1.45%")),
        )

    def test_summary_labels_boolean_and_sequences(self) -> None:
        recipe = {
            "form": "table",
            "headings": ["Metric", "Status", "Dimensions"],
            "mode": "summary",
            "rows": [
                [
                    {"form": "label", "text": "Detector"},
                    {
                        "form": "boolean",
                        "style": "pass_fail",
                        "values": [{"source": {"input": 0, "item": 0}}],
                    },
                    {
                        "form": "sequence",
                        "style": "dimensions",
                        "values": [
                            {
                                "render": {"mode": "integer"},
                                "source": {"input": 1, "item": index},
                            }
                            for index in range(3)
                        ],
                    },
                ]
            ],
        }
        result = TRANSFORM.evaluate_transformation(
            recipe,
            [_selection(True), _selection(109, 400, 400)],
            presentation_kind="table",
        )

        self.assertEqual(result.rows, (("Detector", "Pass", "109 x 400 x 400"),))
        self.assertEqual(result.numerical_cells, frozenset({(1, 3)}))

    def test_table_boolean_parser_and_sequence_styles_are_closed(self) -> None:
        rows = [
            [
                {
                    "form": "boolean",
                    "style": "yes_no",
                    "values": [{"parse": "boolean", "source": {"input": 0, "item": 0}}],
                },
                {
                    "form": "sequence",
                    "style": "slash",
                    "unit": "%",
                    "values": [
                        _value(0, places=1, input_index=1),
                        _value(1, places=1, input_index=1),
                    ],
                },
                {
                    "form": "sequence",
                    "style": "comma",
                    "unit": "nm",
                    "values": [
                        {
                            "render": {"mode": "integer"},
                            "source": {"input": 2, "item": index},
                        }
                        for index in range(2)
                    ],
                },
            ]
        ]
        result = TRANSFORM.evaluate_transformation(
            {
                "form": "table",
                "headings": ["Valid", "Fractions", "Bands"],
                "mode": "summary",
                "rows": rows,
            },
            [_selection("True"), _selection("1.3", "0.0"), _selection(211, 231)],
            presentation_kind="table",
        )

        self.assertEqual(result.rows, (("yes", "1.3 / 0.0%", "211, 231 nm"),))
        TRANSFORM.compare_presentation(
            result,
            presented_kind="table",
            presented=(
                "| Valid | Fractions | Bands |\n"
                "| --- | --- | --- |\n"
                "| YES | 1.3 / 0.0% | 211, 231 nm |"
            ),
        )

    def test_direct_boolean_presentation_is_case_insensitive(self) -> None:
        result = TRANSFORM.evaluate_transformation(
            {
                "form": "table",
                "headings": ["Valid"],
                "mode": "direct",
                "columns": [{"form": "boolean", "parse": "boolean", "style": "yes_no"}],
            },
            [_selection("True", records=(0,))],
            presentation_kind="table",
        )

        TRANSFORM.compare_presentation(
            result,
            presented_kind="table",
            presented="| Valid |\n| --- |\n| Yes |",
        )

    def test_boolean_case_insensitivity_does_not_apply_to_text_cells(self) -> None:
        result = TRANSFORM.evaluate_transformation(
            {
                "form": "table",
                "headings": ["Valid", "Label"],
                "mode": "direct",
                "columns": [
                    {"form": "boolean", "parse": "boolean", "style": "yes_no"},
                    {"form": "text"},
                ],
            },
            [_selection("True", "Exact", records=(0, 0))],
            presentation_kind="table",
        )

        with self.assertRaises(TRANSFORM.TransformationV2Error) as raised:
            TRANSFORM.compare_presentation(
                result,
                presented_kind="table",
                presented="| Valid | Label |\n| --- | --- |\n| Yes | exact |",
            )

        self.assertEqual(raised.exception.observed["difference_count"], 1)
        self.assertEqual(raised.exception.observed["differences"][0]["column"], 2)

    def test_table_presentation_difference_is_bounded(self) -> None:
        result = TRANSFORM.TransformationResult(
            kind="table",
            identity="test",
            headings=("Value",),
            rows=tuple((str(index),) for index in range(20)),
        )
        presented = "\n".join(
            ["| Value |", "| --- |", *("| mismatch |" for _ in range(20))]
        )

        with self.assertRaises(TRANSFORM.TransformationV2Error) as raised:
            TRANSFORM.compare_presentation(
                result, presented_kind="table", presented=presented
            )

        self.assertEqual(raised.exception.observed["difference_count"], 20)
        self.assertEqual(len(raised.exception.observed["differences"]), 16)
        self.assertTrue(raised.exception.observed["differences_truncated"])

    def test_summary_label_and_direct_text_restrictions_fail(self) -> None:
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error,
            "transformation.table.label_invalid",
        ):
            TRANSFORM.evaluate_transformation(
                {
                    "form": "table",
                    "headings": ["Only"],
                    "mode": "summary",
                    "rows": [[{"form": "label", "text": "Not evidence"}]],
                },
                [_selection("unused")],
                presentation_kind="table",
            )
        with self.assertRaisesRegex(
            TRANSFORM.TransformationV2Error,
            "transformation.type.mismatch",
        ):
            TRANSFORM.evaluate_transformation(
                {
                    "columns": [{"form": "text"}],
                    "form": "table",
                    "headings": ["Value"],
                    "mode": "direct",
                },
                [_selection(None, records=(0,))],
                presentation_kind="table",
            )


class TransformationV2RetainedCorpusTests(unittest.TestCase):
    def test_representative_retained_declarations_reproduce_presentations(self) -> None:
        fixtures = V2JSON.decode_json(
            CORPUS.read_text(encoding="utf-8"),
            maximum_bytes=CORPUS.stat().st_size,
            subject="retained transformation corpus",
        )
        for fixture in fixtures:
            with self.subTest(id=fixture["id"]):
                standalone = "v2:" + V2JSON.canonical_json(fixture["transformation"])
                recipe = TRANSFORM.parse_transformation(standalone)
                result = TRANSFORM.evaluate_transformation(
                    recipe,
                    [_selection(*fixture["values"])],
                    presentation_kind="statistic",
                )
                TRANSFORM.compare_presentation(
                    result,
                    presented_kind="statistic",
                    presented=fixture["presentation"],
                )


if __name__ == "__main__":
    unittest.main()
