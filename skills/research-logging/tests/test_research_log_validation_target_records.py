from __future__ import annotations

import copy
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TARGET = importlib.import_module("validation.target_records")


def native_record() -> dict:
    record = TARGET.empty_record("docs/mini.md", "rules-v1")
    record["rule_dependencies"] = {
        "components": {"integrity": 1},
        "input_projections": {"exact-material": 1},
    }
    record["outcomes"] = [
        {
            "entry": "e001",
            "target": "docs/mini/result.csv",
            "check": "Integrity",
            "result": "2026-08-15",
            "dependencies": [
                {
                    "path": "docs/mini/result.csv",
                    "role": "target",
                    "identity": {
                        "size": 2,
                        "mtime_ns": 1,
                        "ctime_ns": 1,
                        "sha256": "a" * 64,
                    },
                }
            ],
            "rule_dependencies": {"integrity": 1},
            "input_dependencies": [
                {
                    "kind": "exact-material",
                    "semantic_identity": "docs/mini/result.csv",
                    "projection_version": 1,
                    "content_identity": "a" * 64,
                    "relationship": "target",
                }
            ],
            "compatibility_identity": "b" * 64,
        }
    ]
    record["result"] = {"date": "2026-08-15"}
    return record


class TargetRecordTests(unittest.TestCase):
    def test_target_round_trip_preserves_durable_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            cache = TARGET.empty_cache()
            TARGET.write_record_and_cache(root, record, cache)
            self.assertEqual(
                TARGET.load_record(root / TARGET.RECORD_FILENAME), record
            )

    def test_malformed_durable_judgment_fails_actionably(self) -> None:
        record = native_record()
        record["judgments"] = [
            {
                "identity": "decision-1",
                "kind": "semantic",
                "result": "accepted",
                "decision_date": "2026-08-15",
                "subject": {},
                "rule_dependencies": {"semantic": 1},
                "input_dependencies": [],
            }
        ]
        with self.assertRaisesRegex(TARGET.TargetRecordError, "rationale"):
            TARGET.decode_record(record)

    def test_compatible_distinct_outcomes_remain_separate(self) -> None:
        record = native_record()
        second = copy.deepcopy(record["outcomes"][0])
        second["entry"] = "e002"
        record["outcomes"].append(second)
        self.assertEqual(len(TARGET.decode_record(record)["outcomes"]), 2)

    def test_exact_duplicate_outcome_is_rejected(self) -> None:
        record = native_record()
        record["outcomes"].append(copy.deepcopy(record["outcomes"][0]))
        with self.assertRaisesRegex(
            TARGET.TargetRecordError, "outcomes contains duplicate identities"
        ):
            TARGET.decode_record(record)

    def test_unknown_outcome_field_has_no_durable_owner(self) -> None:
        record = native_record()
        record["outcomes"][0]["obsolete_projection"] = {}
        with self.assertRaisesRegex(TARGET.TargetRecordError, "unsupported fields"):
            TARGET.decode_record(record)

    def test_missing_or_malformed_cache_is_a_recomputation_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / TARGET.CACHE_FILENAME
            missing, status = TARGET.load_cache(path)
            self.assertEqual((status, missing), ("missing", TARGET.empty_cache()))
            path.write_text("{broken", encoding="utf-8")
            malformed, status = TARGET.load_cache(path)
            self.assertEqual((status, malformed), ("malformed", TARGET.empty_cache()))

    def test_cache_rebuild_does_not_change_durable_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            (root / TARGET.CACHE_FILENAME).write_text("invalid", encoding="utf-8")
            cache, status = TARGET.load_cache(root / TARGET.CACHE_FILENAME)
            self.assertEqual(status, "malformed")
            TARGET.write_record_and_cache(root, record, cache)
            self.assertEqual(TARGET.load_record(root / TARGET.RECORD_FILENAME), record)

    def test_retired_artifacts_fail_with_migration_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "validation-state.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                TARGET.TargetRecordError, "pre-transition astro-agents checkout"
            ):
                TARGET.assert_no_retired_artifacts(root)
            with self.assertRaisesRegex(
                TARGET.TargetRecordError, "retired validation artifacts remain"
            ):
                TARGET.publish_target_bundle(
                    root, "report\n", native_record(), TARGET.empty_cache()
                )
            self.assertFalse((root / TARGET.RECORD_FILENAME).exists())

    def test_failed_record_write_leaves_prior_record_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            cache = TARGET.empty_cache()
            TARGET.write_record_and_cache(root, record, cache)
            before = (root / TARGET.RECORD_FILENAME).read_bytes()
            changed = copy.deepcopy(record)
            changed["result"]["date"] = "2026-08-16"
            original = TARGET._atomic_write_bytes

            def fail_record(path: Path, payload: bytes) -> None:
                if path.name == TARGET.RECORD_FILENAME:
                    raise OSError("simulated record failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_record
            ):
                with self.assertRaises(TARGET.RecordPublicationError):
                    TARGET.write_record_and_cache(root, changed, cache)
            self.assertEqual((root / TARGET.RECORD_FILENAME).read_bytes(), before)

    def test_failed_report_write_preserves_report_and_durable_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            cache = TARGET.empty_cache()
            TARGET.publish_target_bundle(root, "prior report\n", record, cache)
            before = (root / "validation.md").read_bytes()
            changed = copy.deepcopy(record)
            changed["result"]["date"] = "2026-08-16"
            original = TARGET._atomic_write_bytes

            def fail_report(path: Path, payload: bytes) -> None:
                if path.name == "validation.md":
                    raise OSError("simulated report failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_report
            ):
                with self.assertRaises(TARGET.RecordPublicationError):
                    TARGET.publish_target_bundle(root, "new report\n", changed, cache)
            self.assertEqual((root / "validation.md").read_bytes(), before)
            self.assertEqual(TARGET.load_record(root / TARGET.RECORD_FILENAME), changed)

    def test_target_publication_rejects_output_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                TARGET.RecordPublicationError, "must not be a symlink"
            ):
                TARGET.publish_target_bundle(
                    alias, "report\n", native_record(), TARGET.empty_cache()
                )


if __name__ == "__main__":
    unittest.main()
