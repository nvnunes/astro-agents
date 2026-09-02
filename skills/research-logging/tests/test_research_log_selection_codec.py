from __future__ import annotations

import importlib
import json
from decimal import Decimal

from research_log_validation_test_support import unittest

CODEC = importlib.import_module("validation.selection_codec")
VALUES = importlib.import_module("validation.mechanical_values")


def _selection() -> object:
    scalar_values = (
        VALUES.null_value(),
        VALUES.boolean_value(True),
        VALUES.integer_value(-12),
        VALUES.decimal_value(Decimal("1.2300")),
        VALUES.binary_float_value(32, bytes.fromhex("3f800000")),
        VALUES.string_value("café"),
        VALUES.bytes_value(b"\x00\xff"),
        VALUES.string_value(""),
        VALUES.bytes_value(b""),
        VALUES.CanonicalValue("date", "2026-09-02", (("resolution", "day"),)),
        VALUES.CanonicalValue(
            "datetime", "2026-09-02T12:30:00Z", (("resolution", "second"),)
        ),
        VALUES.CanonicalValue("time", "12:30:00", (("resolution", "second"),)),
        VALUES.CanonicalValue(
            "duration", VALUES.decimal_value(Decimal("2.5")), (("unit", "s"),)
        ),
        VALUES.CanonicalValue("quantity", VALUES.integer_value(3), (("unit", "m"),)),
    )
    compound_values = (
        VALUES.array_value(scalar_values[:3], shape=(3,), dtype="object"),
        VALUES.CanonicalValue(
            "record", {"name": scalar_values[5], "value": scalar_values[2]}
        ),
        VALUES.CanonicalValue(
            "mapping", {"left": scalar_values[0], "right": scalar_values[1]}
        ),
        VALUES.CanonicalValue(
            "table",
            (
                VALUES.CanonicalValue("record", {"row": scalar_values[2]}),
                VALUES.CanonicalValue("record", {"row": scalar_values[3]}),
            ),
        ),
    )
    all_values = scalar_values + compound_values
    items = tuple(
        VALUES.SelectionItem(
            coordinate=("rows", index),
            value=value,
            record=index,
            field=("field", index),
        )
        for index, value in enumerate(all_values)
    )
    source_identity = "sha256:" + "a" * 64
    locator_identity = 'v2:{"select":[["value"]]}'
    return VALUES.SelectionResult(
        locator_identity=locator_identity,
        source_identity=source_identity,
        source_profile="json",
        items=items,
        matches=len(items),
        membership=("alpha", "beta"),
        identities=((scalar_values[2], scalar_values[5]),),
        shape=(len(items),),
        dependency_projection=VALUES.selection_dependency(
            source_identity=source_identity,
            locator_identity=locator_identity,
            items=items,
        ),
        limit_profile="v2-initial",
        declared_version="v2",
        effective_version="v2",
    )


class SelectionCodecTests(unittest.TestCase):
    def test_every_selection_field_and_canonical_value_round_trips(self) -> None:
        selection = _selection()

        encoded = CODEC.encode_selection(selection)
        decoded = CODEC.decode_selection(encoded)

        self.assertEqual(decoded, selection)
        self.assertEqual(CODEC.encode_selection(decoded), encoded)

    def test_wrong_schema_extra_fields_and_invalid_dependency_are_rejected(
        self,
    ) -> None:
        payload = json.loads(CODEC.encode_selection(_selection()))
        mutations = (
            lambda value: value.update(schema="future-selection/2"),
            lambda value: value.update(extra=True),
            lambda value: value["selection"].update(dependency_projection="0" * 64),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                candidate = json.loads(json.dumps(payload))
                mutate(candidate)
                encoded = json.dumps(candidate, sort_keys=True).encode()
                with self.assertRaises(CODEC.SelectionCodecError):
                    CODEC.decode_selection(encoded)

    def test_binary_float_width_and_noncanonical_bytes_are_rejected(self) -> None:
        payload = json.loads(CODEC.encode_selection(_selection()))
        binary = payload["selection"]["items"][4]["value"]
        binary["value"] = "00"
        with self.assertRaisesRegex(CODEC.SelectionCodecError, "width"):
            CODEC.decode_selection(json.dumps(payload).encode())

        payload = json.loads(CODEC.encode_selection(_selection()))
        encoded_bytes = payload["selection"]["items"][6]["value"]
        encoded_bytes["value"] = "AP8"
        with self.assertRaisesRegex(CODEC.SelectionCodecError, "base64"):
            CODEC.decode_selection(json.dumps(payload).encode())

    def test_source_payload_is_not_embedded_in_cache_projection(self) -> None:
        payload = json.loads(CODEC.encode_selection(_selection()))

        self.assertEqual(set(payload), {"schema", "selection"})
        self.assertNotIn("path", payload["selection"])
        self.assertNotIn("payload", payload["selection"])

    def test_value_constructor_failures_are_normalized_as_codec_errors(self) -> None:
        payload = json.loads(CODEC.encode_selection(_selection()))
        payload["selection"]["items"] = []

        with self.assertRaises(CODEC.SelectionCodecError):
            CODEC.decode_selection(json.dumps(payload).encode())


if __name__ == "__main__":
    unittest.main()
