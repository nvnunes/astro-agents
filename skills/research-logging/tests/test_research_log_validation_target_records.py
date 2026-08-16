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


def orphan_judgment(number: int) -> dict:
    judgment = review_judgment(number)
    judgment["subject"] = {
        "kind": "orphan_candidate",
        "entry": "e001",
        "identity": f"docs/mini/entries/e001/data/item-{number:04d}.csv",
    }
    return judgment


def subject_index_path(root: Path) -> Path:
    return (
        TARGET.validation_directory(root)
        / STORE.LOCAL_CACHE_DIRECTORY
        / STORE.INDEX_FILENAME
    )


def index_delta_directory(root: Path) -> Path:
    return (
        TARGET.validation_directory(root)
        / STORE.LOCAL_CACHE_DIRECTORY
        / STORE.INDEX_DELTA_DIRECTORY
    )


class TargetRecordTests(unittest.TestCase):
    def test_terminal_compaction_replaces_only_affected_shards_and_collects_old_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [
                orphan_judgment(number) for number in range(2050)
            ]
            TARGET.publish_target_bundle(root, "report\n", record, TARGET.empty_cache())
            before = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME
            )[0]["_sharded_manifest"]
            subjects = [
                orphan_judgment(number)["subject"] for number in range(1950)
            ]

            loaded = TARGET.hydrate_record_shell(
                TARGET.load_record_header_with_source(
                    root / TARGET.RECORD_FILENAME
                )[0],
                root,
            )
            cleanup = TARGET.publish_target_bundle(
                root,
                "report\n",
                loaded,
                TARGET.empty_cache(),
                subjects,
            )

            after = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME
            )[0]["_sharded_manifest"]
            remaining = TARGET.load_record(root / TARGET.RECORD_FILENAME)["judgments"]
            self.assertEqual(cleanup["rows_removed"], 1950)
            self.assertEqual(cleanup["shards_replaced"], 10)
            self.assertEqual(len(remaining), 100)
            self.assertIn(
                before["shards"]["judgments"][-1],
                after["shards"]["judgments"],
            )
            self.assertEqual(cleanup["unreachable_shards"], 10)
            self.assertEqual(cleanup["shards_deleted"], 10)
            self.assertEqual(
                TARGET.cleanup_unreachable_shards(root, after, publish=False)[
                    "unreachable_shards"
                ],
                0,
            )

    def test_cleanup_dry_run_is_read_only_and_collection_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [orphan_judgment(number) for number in range(3)]
            TARGET.publish_target_bundle(root, "report\n", record, TARGET.empty_cache())
            shell = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME
            )[0]
            loaded = TARGET.hydrate_record_shell(shell, root)
            before_manifest = (root / TARGET.RECORD_FILENAME).read_bytes()
            before_files = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )

            report = TARGET.inspect_target_cleanup(
                root, loaded, [orphan_judgment(0)["subject"]]
            )

            self.assertEqual(report["rows_removed"], 1)
            self.assertEqual(
                (root / TARGET.RECORD_FILENAME).read_bytes(), before_manifest
            )
            self.assertEqual(
                sorted(
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                ),
                before_files,
            )
            manifest = shell["_sharded_manifest"]
            first = TARGET.cleanup_unreachable_shards(root, manifest, publish=True)
            second = TARGET.cleanup_unreachable_shards(root, manifest, publish=True)
            self.assertEqual(first["shards_deleted"], 0)
            self.assertEqual(second["shards_deleted"], 0)

    def test_interrupted_collection_leaves_new_manifest_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [orphan_judgment(number) for number in range(3)]
            TARGET.publish_target_bundle(root, "report\n", record, TARGET.empty_cache())
            loaded = TARGET.load_record(root / TARGET.RECORD_FILENAME)
            subject = orphan_judgment(0)["subject"]

            with mock.patch.object(
                TARGET.sharded_state,
                "collect_unreachable_shards",
                side_effect=OSError("simulated post-manifest interruption"),
            ):
                cleanup = TARGET.publish_target_bundle(
                    root,
                    "report\n",
                    loaded,
                    TARGET.empty_cache(),
                    [subject],
                )

            self.assertEqual(cleanup["cleanup_pending"], 1)
            current = TARGET.load_record(root / TARGET.RECORD_FILENAME)
            self.assertEqual(len(current["judgments"]), 2)
            manifest = TARGET.load_record_header_with_source(
                root / TARGET.RECORD_FILENAME
            )[0]["_sharded_manifest"]
            resumed = TARGET.cleanup_unreachable_shards(
                root, manifest, publish=True
            )
            self.assertGreaterEqual(resumed["shards_deleted"], 1)

    def test_compaction_failure_before_manifest_keeps_prior_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [orphan_judgment(number) for number in range(3)]
            TARGET.publish_target_bundle(root, "report\n", record, TARGET.empty_cache())
            before = (root / TARGET.RECORD_FILENAME).read_bytes()
            loaded = TARGET.load_record(root / TARGET.RECORD_FILENAME)
            original = TARGET._atomic_write_bytes

            def fail_manifest(path: Path, payload: bytes) -> None:
                if path == TARGET.manifest_path(root):
                    raise OSError("simulated manifest interruption")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_manifest
            ):
                with self.assertRaises(TARGET.RecordPublicationError):
                    TARGET.publish_target_bundle(
                        root,
                        "report\n",
                        loaded,
                        TARGET.empty_cache(),
                        [orphan_judgment(0)["subject"]],
                    )

            self.assertEqual((root / TARGET.RECORD_FILENAME).read_bytes(), before)
            self.assertEqual(
                len(TARGET.load_record(root / TARGET.RECORD_FILENAME)["judgments"]),
                3,
            )

    def test_cleanup_rejects_a_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = {"storage_layout": STORE.STORAGE_LAYOUT, "shards": {}}
            with self.assertRaises(STORE.ShardedStateError):
                STORE.collect_unreachable_shards(
                    TARGET.validation_directory(root), malformed, delete=False
                )
    def test_native_v1_record_fails_with_retired_format_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / TARGET.RECORD_FILENAME
            path.parent.mkdir(parents=True)
            value = native_record()
            value["schema_version"] = 1
            path.write_bytes(TARGET._json_bytes(value))

            with self.assertRaisesRegex(
                TARGET.TargetRecordError,
                "native-v1 validation records are retired.*pre-transition",
            ):
                TARGET.load_record(path, expected_summary="docs/mini.md")

    def test_monolithic_v2_record_fails_with_retired_format_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / TARGET.RECORD_FILENAME
            path.parent.mkdir(parents=True)
            path.write_bytes(TARGET._json_bytes(native_record()))

            with self.assertRaisesRegex(
                TARGET.TargetRecordError,
                "monolithic native-v2 validation records are retired",
            ):
                TARGET.load_record(path, expected_summary="docs/mini.md")

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
            "session": f"work/{'b' * 64}",
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
            self.assertNotIn("subject_index", manifest)
            self.assertNotIn("outcomes", manifest)
            self.assertNotIn("judgments", manifest)
            self.assertEqual(manifest["row_counts"]["outcomes"], 1)
            self.assertTrue(TARGET.validation_directory(root).is_dir())
            self.assertTrue(
                all(
                    ref["path"].startswith(f"{kind}/")
                    for kind, refs in manifest["shards"].items()
                    for ref in refs
                )
            )

    def test_manifest_rejects_noncanonical_and_duplicate_shard_references(
        self,
    ) -> None:
        manifest = STORE.prepare_state(native_record()).manifest
        ref = manifest["shards"]["outcomes"][0]
        for invalid in (
            f"/tmp/{ref['sha256']}.jsonl",
            f"../outcomes/{ref['sha256']}.jsonl",
            f"judgments/{ref['sha256']}.jsonl",
        ):
            changed = copy.deepcopy(manifest)
            changed["shards"]["outcomes"][0]["path"] = invalid
            with self.assertRaisesRegex(STORE.ShardedStateError, "path"):
                TARGET.decode_sharded_manifest(changed)

        duplicate = copy.deepcopy(manifest)
        duplicate["shards"]["outcomes"].append(copy.deepcopy(ref))
        duplicate["row_counts"]["outcomes"] += ref["row_count"]
        with self.assertRaisesRegex(STORE.ShardedStateError, "duplicate"):
            TARGET.decode_sharded_manifest(duplicate)

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
                    TARGET.validation_directory(root),
                    manifest,
                    "judgments",
                    [subject],
                )
            self.assertEqual(rows, [record["judgments"][1]])
            read_paths = [call.args[1]["path"] for call in reader.call_args_list]
            self.assertEqual(read_paths, [manifest["shards"]["judgments"][1]["path"]])

    def test_missing_malformed_or_stale_index_rebuilds_from_durable_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(number) for number in range(2)]
            with mock.patch.object(STORE, "MAX_SHARD_ROWS", 1):
                TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                TARGET.manifest_path(root), expected_summary="docs/mini.md"
            )
            subject = record["judgments"][1]["subject"]
            index_path = subject_index_path(root)

            for replacement in (None, b"{broken\n", b'{"schema_version":2}\n'):
                if replacement is None:
                    index_path.unlink(missing_ok=True)
                else:
                    index_path.write_bytes(replacement)
                rows = TARGET.load_judgments_for_subjects(root, shell, [subject])
                self.assertEqual(rows, [record["judgments"][1]])
                rebuilt = json.loads(index_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    rebuilt["closure_identity"],
                    STORE.manifest_closure_identity(shell["_sharded_manifest"]),
                )

    def test_warm_base_plus_delta_lookup_opens_only_mapped_shard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                TARGET.manifest_path(root), expected_summary="docs/mini.md"
            )
            updated = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )

            original = STORE._read_owned_bytes
            with mock.patch.object(
                STORE, "_read_owned_bytes", wraps=original
            ) as reader:
                rows = TARGET.load_judgments_for_subjects(
                    root, updated, [review_judgment(2)["subject"]]
                )

            self.assertEqual(rows, [review_judgment(2)])
            self.assertEqual(len(reader.call_args_list), 1)
            self.assertEqual(
                reader.call_args_list[0].args[1]["path"],
                updated["_sharded_manifest"]["shards"]["judgments"][-1]["path"],
            )

    def test_lost_delta_rebuilds_without_losing_accepted_judgment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                TARGET.manifest_path(root), expected_summary="docs/mini.md"
            )
            updated = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )
            for delta in index_delta_directory(root).glob("*.json"):
                delta.unlink()

            rows = TARGET.load_judgments_for_subjects(
                root, updated, [review_judgment(2)["subject"]]
            )

            self.assertEqual(rows, [review_judgment(2)])
            rebuilt = json.loads(subject_index_path(root).read_text(encoding="utf-8"))
            self.assertEqual(
                rebuilt["closure_identity"],
                STORE.manifest_closure_identity(updated["_sharded_manifest"]),
            )

    def test_local_cache_write_failure_does_not_invalidate_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            original = TARGET._atomic_write_bytes

            def fail_local(path: Path, payload: bytes) -> None:
                if STORE.LOCAL_CACHE_DIRECTORY in path.parts:
                    raise OSError("simulated local cache failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_local
            ):
                TARGET.write_record_and_cache(root, record, TARGET.empty_cache())

            self.assertEqual(TARGET.load_record(TARGET.manifest_path(root)), record)
            self.assertFalse(TARGET.cache_path(root).exists())

    def test_terminal_publication_compacts_only_ignored_index_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = native_record()
            record["judgments"] = [review_judgment(1)]
            TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
            shell, _ = TARGET.load_record_header_with_source(
                TARGET.manifest_path(root), expected_summary="docs/mini.md"
            )
            updated = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )
            durable_before = {
                path.relative_to(TARGET.validation_directory(root)).as_posix():
                    path.read_bytes()
                for kind in STORE.ROW_KINDS
                for path in (TARGET.validation_directory(root) / kind).glob("*.jsonl")
            }
            logical = TARGET.hydrate_record_shell(
                updated, root, preserve_manifest=True
            )

            TARGET.publish_target_bundle(
                root, "current report\n", logical, TARGET.empty_cache()
            )

            durable_after = {
                path.relative_to(TARGET.validation_directory(root)).as_posix():
                    path.read_bytes()
                for kind in STORE.ROW_KINDS
                for path in (TARGET.validation_directory(root) / kind).glob("*.jsonl")
            }
            self.assertTrue(
                all(
                    durable_after[path] == payload
                    for path, payload in durable_before.items()
                )
            )
            self.assertFalse(index_delta_directory(root).exists())
            index = json.loads(subject_index_path(root).read_text(encoding="utf-8"))
            self.assertEqual(index["sequence"], 0)

    def test_batch_index_storage_grows_approximately_linearly(self) -> None:
        def footprint(batch_count: int) -> int:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                record = native_record()
                TARGET.write_record_and_cache(root, record, TARGET.empty_cache())
                shell, _ = TARGET.load_record_header_with_source(
                    TARGET.manifest_path(root), expected_summary="docs/mini.md"
                )
                for batch in range(batch_count):
                    rows = [
                        review_judgment(batch * 50 + offset)
                        for offset in range(50)
                    ]
                    shell = TARGET.append_judgment_batch(root, shell, rows)
                paths = [subject_index_path(root)]
                paths.extend(index_delta_directory(root).glob("*.json"))
                return sum(path.stat().st_size for path in paths)

        smaller = footprint(10)
        larger = footprint(20)

        self.assertGreater(larger, smaller * 1.7)
        self.assertLess(larger, smaller * 2.3)

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
            )
            state_dir = TARGET.validation_directory(root)
            before = {
                path.relative_to(state_dir).as_posix(): path.read_bytes()
                for kind in STORE.ROW_KINDS
                for path in (state_dir / kind).glob("*.jsonl")
            }
            cache_before = (root / TARGET.CACHE_FILENAME).read_bytes()
            index_before = (
                state_dir / STORE.LOCAL_CACHE_DIRECTORY / STORE.INDEX_FILENAME
            ).read_bytes()

            updated = TARGET.append_judgment_batch(
                root, shell, [review_judgment(2)]
            )

            after = {
                path.relative_to(state_dir).as_posix(): path.read_bytes()
                for kind in STORE.ROW_KINDS
                for path in (state_dir / kind).glob("*.jsonl")
            }
            self.assertTrue(
                all(after[path] == payload for path, payload in before.items())
            )
            new_paths = set(after) - set(before)
            self.assertEqual(
                sum(
                    path.startswith("judgments/")
                    for path in new_paths
                ),
                1,
            )
            self.assertEqual(
                sum(path.startswith("outcomes/") for path in new_paths),
                0,
            )
            self.assertEqual(
                sum(path.startswith("failures/") for path in new_paths),
                0,
            )
            self.assertEqual((root / TARGET.CACHE_FILENAME).read_bytes(), cache_before)
            self.assertEqual(
                (
                    state_dir
                    / STORE.LOCAL_CACHE_DIRECTORY
                    / STORE.INDEX_FILENAME
                ).read_bytes(),
                index_before,
            )
            deltas = list(
                (
                    state_dir
                    / STORE.LOCAL_CACHE_DIRECTORY
                    / STORE.INDEX_DELTA_DIRECTORY
                ).glob("*.json")
            )
            self.assertEqual(len(deltas), 1)
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
            )
            manifest_before = (root / TARGET.RECORD_FILENAME).read_bytes()
            original = TARGET._atomic_write_bytes

            def fail_manifest(path: Path, payload: bytes) -> None:
                if path == TARGET.manifest_path(root):
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
            path.parent.mkdir(parents=True)
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
                if path == TARGET.manifest_path(root):
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
            TARGET.validation_directory(root).symlink_to(
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
