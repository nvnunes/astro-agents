from __future__ import annotations

import importlib
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

import h5py
import numpy as np
from research_log_validation_test_support import write

LOCATOR = importlib.import_module("validation.locator_v2")


class LocatorV2GrammarTests(unittest.TestCase):
    def test_canonicalization_sorts_conditions_and_deduplicates_in_values(self) -> None:
        parsed = LOCATOR.parse_locator(
            {
                "select": [["value"]],
                "where": [
                    {"path": ["kind"], "op": "in", "values": ["b", "a", "a"]},
                    {"path": ["score"], "op": "eq", "value": Decimal("1.00")},
                ],
            }
        )

        self.assertTrue(parsed.identity.startswith("v2:"))
        self.assertIn('"values":["a","b"]', parsed.identity)
        self.assertIn('"value":1.0', parsed.identity)

    def test_paths_literals_and_relationships_are_closed(self) -> None:
        literal = LOCATOR.authored_literal(
            {"bits": 64, "hex": "8000000000000000", "type": "binary_float"}
        )
        self.assertEqual(literal.kind, "binary_float")
        with self.assertRaisesRegex(LOCATOR.LocatorV2Error, "locator.syntax.invalid"):
            LOCATOR.parse_locator({"path": [-1]})
        with self.assertRaisesRegex(LOCATOR.LocatorV2Error, "locator.literal.invalid"):
            LOCATOR.authored_literal(
                {"bits": 64, "hex": "3FF0000000000000", "type": "binary_float"}
            )
        with self.assertRaisesRegex(LOCATOR.LocatorV2Error, "locator.syntax.invalid"):
            LOCATOR.parse_locator({"path": [], "text": {"contains": "not combinable"}})

        tagged = (
            ({"base64": "AQ==", "type": "bytes"}, "bytes"),
            ({"resolution": "day", "type": "date", "value": "2026-08-29"}, "date"),
            ({"resolution": "second", "type": "time", "value": "12:00:00"}, "time"),
            (
                {
                    "resolution": "second",
                    "type": "datetime",
                    "value": "2026-08-29T12:00:00Z",
                },
                "datetime",
            ),
            ({"type": "duration", "unit": "s", "value": Decimal("1.5")}, "duration"),
            ({"type": "quantity", "unit": "ms", "value": 2}, "quantity"),
        )
        for declaration, expected in tagged:
            with self.subTest(kind=expected):
                self.assertEqual(LOCATOR.authored_literal(declaration).kind, expected)


class LocatorV2SourceProfileTests(unittest.TestCase):
    def test_csv_select_filter_identity_and_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(source, "case_id,score\n8,0.90\n15,0.95\n")

            result = LOCATOR.evaluate_locator(
                source,
                {
                    "select": [["case_id"], ["score"]],
                    "where": [
                        {
                            "op": "eq",
                            "parse": "decimal",
                            "path": ["score"],
                            "value": Decimal("0.95"),
                        }
                    ],
                    "identity": [["case_id"]],
                    "expect": {
                        "identities": [["15"]],
                        "items": 2,
                        "matches": 1,
                    },
                },
            )

            self.assertEqual(result.source_profile, "csv")
            self.assertEqual(
                [item.value.value for item in result.items], ["15", "0.95"]
            )
            self.assertEqual(result.matches, 1)
            self.assertEqual(len(result.membership), 1)

    def test_csv_requires_explicit_selection_and_stable_multirow_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(source, "case_id,score\n8,0.90\n15,0.95\n")

            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.syntax.invalid"
            ):
                LOCATOR.evaluate_locator(source, {"path": []})
            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.selection.ambiguous"
            ):
                LOCATOR.evaluate_locator(source, {"select": [["score"]]})
            row_count = LOCATOR.evaluate_locator(source, {"property": "row_count"})
            self.assertEqual(row_count.items[0].value.value, "2")

    def test_every_predicate_is_checked_even_after_another_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(source, "case_id,score\n8,not-a-number\n")

            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.predicate.parse_failed"
            ):
                LOCATOR.evaluate_locator(
                    source,
                    {
                        "select": [["score"]],
                        "where": [
                            {"op": "eq", "path": ["case_id"], "value": "other"},
                            {
                                "op": "eq",
                                "parse": "decimal",
                                "path": ["score"],
                                "value": Decimal("1.0"),
                            },
                        ],
                    },
                )

    def test_json_paths_slices_filters_and_exact_decimals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.json"
            write(
                source,
                '{"trials":[{"id":"a","score":0.90},'
                '{"id":"b","score":0.95},{"id":"c","score":1.0}]}',
            )

            result = LOCATOR.evaluate_locator(
                source,
                {
                    "path": ["trials", {"slice": [1, 3]}],
                    "select": [["score"]],
                    "where": [{"op": "in", "path": ["id"], "values": ["b", "c"]}],
                    "identity": [["id"]],
                    "expect": {"matches": 2, "items": 2},
                },
            )

            self.assertEqual(
                [item.value.kind for item in result.items], ["decimal", "decimal"]
            )
            self.assertEqual(result.matches, 2)

    def test_text_selection_is_exact_and_occurrence_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "run.log"
            write(
                source,
                "start\nBenchmark simulations: one\nBenchmark simulations: two\n",
            )

            result = LOCATOR.evaluate_locator(
                source,
                {
                    "text": {
                        "contains": "Benchmark simulations",
                        "occurrence": "all",
                    },
                    "expect": {"matches": 2, "items": 2},
                },
            )

            self.assertEqual(len(result.items), 2)
            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.selection.ambiguous"
            ):
                LOCATOR.evaluate_locator(
                    source, {"text": {"contains": "Benchmark simulations"}}
                )

    def test_npz_aligned_records_and_binary_float_bits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.npz"
            np.savez(
                source,
                case_id=np.array([8, 15], dtype=np.int64),
                score=np.array([-0.0, 0.95], dtype=np.float64),
            )

            result = LOCATOR.evaluate_locator(
                source,
                {
                    "path": [],
                    "select": [["score"]],
                    "where": [{"op": "eq", "path": ["case_id"], "value": 8}],
                    "identity": [["case_id"]],
                },
            )

            self.assertEqual(result.items[0].value.kind, "binary_float")
            self.assertEqual(result.items[0].value.value, "8000000000000000")

    def test_npz_object_arrays_fail_as_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unsafe.npz"
            np.savez(source, values=np.array([{"x": 1}], dtype=object))

            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.source.unsafe"
            ):
                LOCATOR.evaluate_locator(source, {"path": ["values", 0]})

    def test_hdf5_paths_properties_and_external_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "results.h5"
            with h5py.File(source, "w") as handle:
                group = handle.create_group("metrics")
                group.create_dataset("score", data=np.array([0.9, 0.95]))

            result = LOCATOR.evaluate_locator(
                source,
                {"path": ["metrics", "score"], "property": "shape[0]"},
            )
            self.assertEqual(result.items[0].value.value, "2")

            external = root / "external.h5"
            with h5py.File(external, "w") as handle:
                handle.create_dataset("data", data=np.array([1]))
            linked = root / "linked.h5"
            with h5py.File(linked, "w") as handle:
                handle["outside"] = h5py.ExternalLink(external.name, "/data")
            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.source.unsafe"
            ):
                LOCATOR.evaluate_locator(linked, {"path": ["outside", 0]})

            recursive = root / "recursive.h5"
            with h5py.File(recursive, "w") as handle:
                group = handle.create_group("group")
                group["self"] = group
            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.source.unsafe"
            ):
                LOCATOR.evaluate_locator(recursive, {"path": ["group"]})

    def test_expectation_mismatch_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "one.json"
            write(source, '{"value":1}')

            with self.assertRaisesRegex(
                LOCATOR.LocatorV2Error, "locator.expectation.mismatch"
            ):
                LOCATOR.evaluate_locator(
                    source,
                    {"path": ["value"], "expect": {"items": 2}},
                )

    def test_textual_and_binary_source_resource_bounds_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            textual_cases = (
                ("results.csv", "value\n1\n", {"select": [["value"]]}),
                ("results.tsv", "value\n1\n", {"select": [["value"]]}),
                ("results.json", '{"value":1}', {"path": ["value"]}),
                ("results.txt", "value\n", {"text": {"contains": "value"}}),
            )
            with mock.patch.object(LOCATOR, "MAX_TEXT_OR_JSON_BYTES", 4):
                for filename, content, locator in textual_cases:
                    with self.subTest(filename=filename):
                        source = root / filename
                        write(source, content)
                        with self.assertRaisesRegex(
                            LOCATOR.LocatorV2Error, "locator.source.too_large"
                        ):
                            LOCATOR.evaluate_locator(source, locator)

            npz = root / "results.npz"
            np.savez(npz, values=np.array([1], dtype=np.int64))
            hdf5 = root / "results.h5"
            with h5py.File(hdf5, "w") as handle:
                handle.create_dataset("values", data=np.array([1], dtype=np.int64))
            with mock.patch.object(LOCATOR, "MAX_BINARY_MEMBER_BYTES", 1):
                for source in (npz, hdf5):
                    with self.subTest(filename=source.name):
                        with self.assertRaisesRegex(
                            LOCATOR.LocatorV2Error, "locator.source.too_large"
                        ):
                            LOCATOR.evaluate_locator(source, {"path": ["values", 0]})

            bomb = root / "bomb.npz"
            with zipfile.ZipFile(
                bomb, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("values.npy", b"0" * (1024 * 1024 + 2))
            with (
                mock.patch.object(LOCATOR, "MAX_BINARY_MEMBER_BYTES", 1),
                mock.patch.object(LOCATOR, "MAX_BINARY_MEMBER_OVERHEAD_BYTES", 0),
                mock.patch("numpy.load", side_effect=AssertionError("materialized")),
            ):
                with self.assertRaisesRegex(
                    LOCATOR.LocatorV2Error, "locator.source.too_large"
                ):
                    LOCATOR.evaluate_locator(bomb, {"path": []})

    def test_temporary_source_read_failure_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(source, "value\n1\n")

            with mock.patch.object(
                Path, "read_bytes", side_effect=OSError("temporarily unavailable")
            ):
                with self.assertRaises(LOCATOR.LocatorV2Error) as raised:
                    LOCATOR.evaluate_locator(source, {"select": [["value"]]})

            self.assertEqual(raised.exception.code, "locator.reader.unavailable")
            self.assertEqual(raised.exception.outcome, "unavailable")

    def test_each_source_profile_rejects_malformed_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                ("bad.csv", b"a,b\n1\n", {"select": [["a"]]}, "format_mismatch"),
                ("bad.tsv", b"a\tb\n1\n", {"select": [["a"]]}, "format_mismatch"),
                ("bad.json", b"{", {"path": []}, "format_mismatch"),
                ("bad.npz", b"PK\x03\x04junk", {"path": []}, "format_mismatch"),
                (
                    "bad.h5",
                    LOCATOR.HDF_SIGNATURE + b"junk",
                    {"path": []},
                    "format_mismatch",
                ),
                (
                    "bad.txt",
                    b"\xff",
                    {"text": {"contains": "value"}},
                    "text.decode",
                ),
            )
            for filename, payload, locator, code in cases:
                with self.subTest(filename=filename):
                    source = root / filename
                    source.write_bytes(payload)
                    with self.assertRaisesRegex(LOCATOR.LocatorV2Error, code):
                        LOCATOR.evaluate_locator(source, locator)


if __name__ == "__main__":
    unittest.main()
