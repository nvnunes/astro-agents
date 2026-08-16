from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIRECTORY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TARGET = importlib.import_module("validation.target_records")
STORE = importlib.import_module("validation.sharded_state")
MIGRATION = importlib.import_module("validation.layout_migration")

from test_research_log_validation_target_records import native_record  # noqa: E402


class LayoutMigrationTests(unittest.TestCase):
    def make_old_bundle(self, root: Path) -> tuple[Path, dict]:
        output = root / "docs" / "mini"
        record = native_record()
        prepared = STORE.prepare_state(record)
        old_manifest = dict(prepared.manifest)
        old_files: dict[str, bytes] = {}
        old_refs: dict[str, list[dict]] = {}
        subjects: dict = {}
        for kind in STORE.ROW_KINDS:
            old_refs[kind] = []
            for ref in prepared.manifest["shards"][kind]:
                projected = dict(ref)
                projected["path"] = f"validation-state/{ref['path']}"
                old_refs[kind].append(projected)
                payload = prepared.files[ref["path"]]
                old_files[projected["path"]] = payload
                if kind in STORE.SUBJECT_KINDS:
                    rows = [json.loads(line) for line in payload.decode().splitlines()]
                    STORE._add_index_rows(subjects, kind, projected, rows)
        index = {"schema_version": 1, "subjects": subjects}
        index_bytes = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
        identity = TARGET.sha256_bytes(index_bytes)
        index_path = f"validation-state/index/{identity}.json"
        old_files[index_path] = index_bytes
        old_manifest["shards"] = old_refs
        old_manifest["subject_index"] = {
            "path": index_path,
            "sha256": identity,
            "byte_count": len(index_bytes),
            "subject_count": sum(len(entries) for entries in subjects.values()),
        }
        for relative, payload in old_files.items():
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (output / "validation-record.json").write_bytes(
            TARGET._json_bytes(old_manifest)
        )
        (output / "validation-cache.json").write_bytes(
            TARGET._json_bytes(TARGET.empty_cache())
        )
        return output, record

    def test_storage_only_migration_preserves_logical_state_and_is_rerunnable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output, record = self.make_old_bundle(root)

            before = MIGRATION.project_old_layout(output, "docs/mini.md")
            after = MIGRATION.migrate_layout(output, "docs/mini.md")

            self.assertEqual(after["layout"], "final")
            self.assertEqual(
                TARGET.load_record(output / TARGET.RECORD_FILENAME), record
            )
            self.assertFalse((output / "validation-record.json").exists())
            self.assertFalse((output / "validation-state").exists())
            index = STORE.ensure_subject_index(
                TARGET.validation_directory(output), before.new_manifest
            )
            self.assertEqual(index["subjects"], before.index["subjects"])

    def test_mixed_state_requires_exact_equivalence_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output, _ = self.make_old_bundle(root)
            projection = MIGRATION.project_old_layout(output, "docs/mini.md")
            STORE.publish_immutable_files(
                TARGET.validation_directory(output),
                projection.files,
                TARGET._atomic_write_bytes,
            )
            conflicting = dict(projection.new_manifest)
            conflicting["result"] = {"date": "2099-01-01"}
            manifest_path = output / TARGET.RECORD_FILENAME
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(TARGET._json_bytes(conflicting))

            with self.assertRaisesRegex(
                MIGRATION.LayoutMigrationError, "not exact projections"
            ):
                MIGRATION.migrate_layout(output, "docs/mini.md")

            self.assertTrue((output / "validation-record.json").exists())
            self.assertTrue((output / "validation-state").exists())

    def test_active_continuation_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output, _ = self.make_old_bundle(root)
            path = output / "validation-record.json"
            manifest = json.loads(path.read_text())
            manifest["continuation"] = {
                "kind": "paged",
                "session": f"work/{'a' * 64}",
                "session_identity": "a" * 64,
                "review_kind": "orphan_candidates",
            }
            path.write_bytes(TARGET._json_bytes(manifest))

            with self.assertRaisesRegex(
                MIGRATION.LayoutMigrationError, "controlled session relocation"
            ):
                MIGRATION.migrate_layout(output, "docs/mini.md")

            self.assertFalse((output / TARGET.RECORD_FILENAME).exists())
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
