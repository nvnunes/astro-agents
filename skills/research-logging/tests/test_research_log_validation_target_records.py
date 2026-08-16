from __future__ import annotations

import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TARGET = importlib.import_module("validation.target_records")
STORE = importlib.import_module("validation.sharded_state")


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
    record["completion_dependencies"] = copy.deepcopy(
        record["outcomes"][0]["dependencies"]
    )
    record["projection"] = {
        "identity": "c" * 64,
        "report_sha256": "d" * 64,
    }
    return record


def review_judgment(number: int) -> dict:
    return {
        "identity": f"decision-{number}",
        "kind": "review-decision",
        "result": "pass",
        "decision": "pass",
        "decision_date": "2026-08-16",
        "subject": {
            "kind": "semantic_provenance",
            "entry": "Summary",
            "identity": f"claim-{number}",
        },
        "rule_dependencies": {"semantic_review": 1},
        "input_dependencies": [],
        "rationale": "Exact prior decision.",
        "rationale_provenance": "recorded",
        "provenance": "native-reviewed",
    }


class TargetRecordTests(unittest.TestCase):
    def test_v2_record_rejects_absolute_summary(self) -> None:
        record = native_record()
        record["summary"] = "/tmp/project/docs/mini.md"
        with self.assertRaisesRegex(TARGET.TargetRecordError, "project-relative"):
            TARGET.decode_record(record)

    def test_v2_record_rejects_malformed_projection(self) -> None:
        record = native_record()
        record["projection"] = {
            "identity": "not-a-sha",
            "report_sha256": "d" * 64,
        }
        with self.assertRaisesRegex(TARGET.TargetRecordError, "projection.identity"):
            TARGET.decode_record(record)

    def test_v2_record_distinguishes_ordinary_and_paged_continuations(self) -> None:
        ordinary = native_record()
        ordinary["continuation"] = {
            "kind": "ordinary",
            "identity": "a" * 64,
            "item_count": 2,
        }
        self.assertEqual(
            TARGET.decode_record(ordinary)["continuation"],
            ordinary["continuation"],
        )
        paged = native_record()
        paged["continuation"] = {
            "kind": "paged",
            "session": ".astro-agents-validation-work/summary/session",
            "session_identity": "b" * 64,
            "review_kind": "orphan_candidates",
        }
        self.assertEqual(
            TARGET.decode_record(paged)["continuation"], paged["continuation"]
        )
        paged["continuation"]["current"] = {"page": 2}
        with self.assertRaisesRegex(TARGET.TargetRecordError, "incorrect fields"):
            TARGET.decode_record(paged)

    def test_target_round_trip_preserves_durable_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            cache = TARGET.empty_cache()
            TARGET.write_record_and_cache(root, record, cache)
            self.assertEqual(
                TARGET.load_record(root / TARGET.RECORD_FILENAME), record
            )
            manifest = json.loads(
                (root / TARGET.RECORD_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["storage_layout"], "sharded-v1")
            self.assertNotIn("outcomes", manifest)
            self.assertNotIn("judgments", manifest)
            self.assertEqual(manifest["row_counts"]["outcomes"], 1)
            self.assertTrue((root / STORE.STATE_DIRECTORY).is_dir())

    def test_subject_lookup_reads_only_the_mapped_judgment_shard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(number) for number in range(2)]
            with mock.patch.object(STORE, "MAX_SHARD_ROWS", 1):
                TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            manifest = json.loads(
                (root / TARGET.RECORD_FILENAME).read_text(encoding="utf-8")
            )
            subject = record["judgments"][1]["subject"]
            original = STORE._read_owned_bytes
            with mock.patch.object(
                STORE, "_read_owned_bytes", wraps=original
            ) as reader:
                rows = STORE.load_subject_rows(
                    root, manifest, "judgments", [subject]
                )
            self.assertEqual(rows, [record["judgments"][1]])
            read_paths = [call.args[1]["path"] for call in reader.call_args_list]
            self.assertEqual(len(read_paths), 2)
            self.assertIn(manifest["subject_index"]["path"], read_paths)

    def test_accepted_batch_appends_one_shard_and_compact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            cache = TARGET.empty_cache()
            TARGET.write_record_and_cache(root, record, cache)
            shell, _ = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME,
                expected_summary="docs/mini.md",
                project_root=root,
            )
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in (root / STORE.STATE_DIRECTORY).rglob("*")
                if path.is_file()
            }
            cache_before = (root / TARGET.CACHE_FILENAME).read_bytes()

            updated = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in (root / STORE.STATE_DIRECTORY).rglob("*")
                if path.is_file()
            }
            self.assertTrue(
                all(after[path] == payload for path, payload in before.items())
            )
            new_paths = set(after) - set(before)
            self.assertEqual(
                sum(
                    path.startswith("validation-state/judgments/")
                    for path in new_paths
                ),
                1,
            )
            self.assertEqual(
                sum(path.startswith("validation-state/index/") for path in new_paths),
                1,
            )
            self.assertEqual((root / TARGET.CACHE_FILENAME).read_bytes(), cache_before)
            self.assertEqual(TARGET.record_row_count(updated, "judgments"), 2)
            self.assertEqual(
                TARGET.append_judgment_batch(root, updated, [review_judgment(2)]),
                updated,
            )

    def test_interrupted_batch_manifest_publish_is_idempotently_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME,
                expected_summary="docs/mini.md",
                project_root=root,
            )
            manifest_before = (root / TARGET.RECORD_FILENAME).read_bytes()
            original = TARGET._atomic_write_bytes

            def fail_manifest(path: Path, payload: bytes) -> None:
                if path.name == TARGET.RECORD_FILENAME:
                    raise OSError("simulated manifest interruption")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_manifest
            ):
                with self.assertRaises(TARGET.RecordPublicationError):
                    TARGET.append_judgment_batch(
                        root, shell, [review_judgment(2)]
                    )
            self.assertEqual(
                (root / TARGET.RECORD_FILENAME).read_bytes(), manifest_before
            )
            repaired = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )
            self.assertEqual(TARGET.record_row_count(repaired, "judgments"), 2)

    def test_interrupted_batch_shard_publish_retains_prior_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME,
                expected_summary="docs/mini.md",
                project_root=root,
            )
            manifest_before = (root / TARGET.RECORD_FILENAME).read_bytes()
            original = TARGET._atomic_write_bytes

            def fail_judgment_shard(path: Path, payload: bytes) -> None:
                if path.parent.name == "judgments":
                    raise OSError("simulated shard interruption")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_judgment_shard
            ):
                with self.assertRaises(TARGET.RecordPublicationError):
                    TARGET.append_judgment_batch(
                        root, shell, [review_judgment(2)]
                    )
            self.assertEqual(
                (root / TARGET.RECORD_FILENAME).read_bytes(), manifest_before
            )
            repaired = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )
            self.assertEqual(TARGET.record_row_count(repaired, "judgments"), 2)

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

    def test_target_publication_rejects_validation_state_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            (root / STORE.STATE_DIRECTORY).symlink_to(
                external, target_is_directory=True
            )
            with self.assertRaisesRegex(
                TARGET.RecordPublicationError, "must not be a symlink"
            ):
                TARGET.publish_target_bundle(
                    root, "report\n", native_record(), TARGET.empty_cache()
                )


if __name__ == "__main__":
    unittest.main()
