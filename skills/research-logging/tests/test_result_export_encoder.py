"""Pre-aggregation byte bounds for both result-domain encoders."""

from __future__ import annotations

import unittest
from unittest import mock

from result_export_encoder import (
    CappedJsonEncoder,
    ExportTooLarge,
    encode_json_sequence,
    encode_json_value,
    measure_json_value,
)


class ResultExportEncoderTests(unittest.TestCase):
    def test_encoder_stops_before_requesting_the_next_value(self) -> None:
        requested = 0

        def values():
            nonlocal requested
            for value in ("a", "b", "c"):
                requested += 1
                yield value

        with self.assertRaises(ExportTooLarge):
            encode_json_sequence(values(), 5)
        self.assertEqual(requested, 2)

    def test_encoder_accepts_an_exact_limit(self) -> None:
        self.assertEqual(encode_json_sequence(("a",), 5), '["a"]')

    def test_value_encoder_is_canonical_without_building_one_json_string(self) -> None:
        self.assertEqual(
            encode_json_value({"z": 1, "a": "é"}, 17), '{"a":"é","z":1}'
        )

    def test_spooled_encoder_materializes_only_after_the_cap_passes(self) -> None:
        encoder = CappedJsonEncoder(9, spool_limit=1)
        encoder.write_object((("a", [1]),))
        self.assertEqual(encoder.materialize(), {"a": [1]})

    def test_measurement_does_not_materialize_an_encoded_string(self) -> None:
        with mock.patch.object(
            CappedJsonEncoder,
            "value",
            side_effect=AssertionError("measurement materialized JSON"),
        ):
            self.assertEqual(measure_json_value({"a": [1]}, 9), 9)
