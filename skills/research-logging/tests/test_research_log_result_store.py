from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from log_commands.reproduction_saved_storage import (
    confirm_empty_replacement,
    load_saved_run,
    publish_saved_run,
    write_saved_run,
)
from research_log_result_store import (
    STORE_VERSION,
    ResultStoreError,
    clear_reproduction_results,
    clear_result_store,
    clear_validation_snapshots,
    record_report_materialization,
    result_snapshot,
    result_store_path,
    result_transaction,
    results_lock,
)
from test_reproduction_canonical_records import mixed_run
from validation.operation_state import OperationLockError


class ResultStoreTests(unittest.TestCase):
    def test_results_lock_excludes_a_concurrent_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with results_lock(root):
                with self.assertRaises(OperationLockError):
                    with results_lock(root):
                        pass

    def test_generation_conditional_marker_does_not_mark_newer_result(self) -> None:
        for kind in ("validation", "reproduction"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "study"
                root.mkdir()
                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO store_state VALUES (?, 2, 'study.md')", (kind,)
                    )
                self.assertFalse(
                    record_report_materialization(
                        root, kind, b"old", expected_generation=1
                    )
                )
                with result_snapshot(root) as db:
                    self.assertIsNone(
                        db.execute("SELECT 1 FROM report_materializations").fetchone()
                    )

    def test_domain_clearing_preserves_other_rows_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES ('validation', 1, 'study.md')"
                )
                db.execute(
                    "INSERT INTO store_state VALUES ('reproduction', 7, 'study.md')"
                )
            record_report_materialization(root, "validation", b"validation")
            record_report_materialization(root, "reproduction", b"reproduction")
            report = root / "validation.md"
            report.write_bytes(b"retained report")
            preserved = {
                root / ".cache" / "reproduction" / "state.sqlite": b"future-state",
                root / ".cache" / "reproduction" / "checkpoint.json": b"stopped",
                root / "tmp" / "reproduction" / "staged" / "output.txt": b"staged",
                root / "evidence.json": b"evidence",
            }
            for candidate, content in preserved.items():
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(content)

            clear_validation_snapshots(root)

            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT generation FROM store_state WHERE domain='reproduction'"
                    ).fetchone()[0],
                    7,
                )
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM store_state WHERE domain='validation'"
                    ).fetchone()
                )
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations WHERE kind='validation'"
                    ).fetchone()
                )
            self.assertEqual(report.read_bytes(), b"retained report")
            clear_result_store(root)
            self.assertFalse(result_store_path(root).exists())
            self.assertEqual(report.read_bytes(), b"retained report")
            self.assertEqual(
                {candidate: candidate.read_bytes() for candidate in preserved},
                preserved,
            )

    def test_reproduction_clear_preserves_owned_nonresult_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            confirm_empty_replacement(root, "2030-01-01T00:00:00Z")
            preserved = {
                root / "evidence.json": b'{"record":"retained"}\n',
                root / ".cache" / "validation" / "cache.json": b"validation\n",
                root / ".cache" / "reproduction" / "jobs.json": b"stopped-job\n",
                root / ".cache" / "pyrun" / "state.json": b"pyrun\n",
                root / "tmp" / "reproduction" / "staged" / "output.txt": b"staged\n",
            }
            for candidate, content in preserved.items():
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(content)

            clear_reproduction_results(root)

            with result_snapshot(root) as db:
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM store_state WHERE domain='reproduction'"
                    ).fetchone()
                )
            self.assertEqual(
                {candidate: candidate.read_bytes() for candidate in preserved},
                preserved,
            )

    def test_writer_initializes_shared_store_and_reader_observes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES (?, ?, ?)",
                    ("validation", 1, "study.md"),
                )
            self.assertEqual(result_store_path(root), root / ".cache/results.sqlite")
            with result_snapshot(root) as db:
                self.assertEqual(
                    [
                        tuple(row)
                        for row in db.execute(
                            "SELECT domain, generation, summary FROM store_state"
                        )
                    ],
                    [("validation", 1, "study.md")],
                )

    def test_validation_only_store_has_no_reproduction_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES ('validation', 1, 'study.md')"
                )
            with result_snapshot(root) as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertFalse(
                    any(name.startswith("reproduction_") for name in tables)
                )
                indexes = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertFalse(
                    any(name.startswith("reproduction_") for name in indexes)
                )
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 19)

    def test_v19_schema_uses_canonical_validation_tables_and_query_indexes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with result_transaction(root):
                pass
            with result_snapshot(root) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 19)
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                self.assertTrue(
                    {
                        "validation_snapshots",
                        "validation_blocked_checks",
                        "validation_failed_checks",
                        "validation_snapshot_entries",
                        "validation_findings",
                        "validation_finding_causes",
                        "validation_finding_source_locations",
                        "validation_repair_keys",
                        "validation_finding_repair_keys",
                        "validation_repair_nodes",
                        "validation_repair_edges",
                        "validation_repair_edge_targets",
                        "validation_finding_nodes",
                        "validation_batches",
                        "validation_batch_rationale",
                        "validation_batch_findings",
                        "validation_batch_repair_keys",
                        "validation_batch_entries",
                        "command_diagnostics",
                        "command_diagnostic_records",
                    }
                    <= tables
                )
                self.assertFalse(
                    {
                        "validation_results",
                        "validation_checks",
                        "validation_groups",
                        "validation_commands",
                        "validation_artifacts",
                        "validation_batch_nodes",
                    }
                    & tables
                )
                indexes = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_schema WHERE type='index'"
                    )
                }
                self.assertTrue(
                    {
                        "validation_snapshots_slot_generation",
                        "validation_snapshot_entries_lookup",
                        "validation_findings_type_order",
                        "validation_findings_entry_order",
                        "validation_findings_id",
                        "validation_batches_order",
                        "validation_batches_id",
                        "validation_batch_entries_lookup",
                        "validation_batch_findings_finding",
                        "validation_finding_nodes_node",
                        "validation_repair_edges_subject",
                        "validation_repair_edge_targets_target",
                        "command_diagnostics_generation",
                    }
                    <= indexes
                )
                self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

    def test_failed_writer_rolls_back_and_missing_reader_is_cold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with self.assertRaises(ResultStoreError) as raised:
                with result_snapshot(root):
                    pass
            self.assertEqual(raised.exception.code, "results.store.missing")
            with result_transaction(root):
                pass
            with self.assertRaises(RuntimeError):
                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO store_state VALUES (?, ?, ?)",
                        ("validation", 1, "study.md"),
                    )
                    raise RuntimeError("inject failure")
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM store_state").fetchone()[0], 0
                )

    def test_interrupted_first_initialization_leaves_a_retryable_version_zero_store(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with self.assertRaises(RuntimeError):
                with result_transaction(root):
                    raise RuntimeError("interrupt initialization")
            path = result_store_path(root)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM sqlite_master WHERE type='table'"
                    ).fetchone()[0],
                    0,
                )
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES ('validation', 1, 'study.md')"
                )
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone()[0], STORE_VERSION
                )

    def test_failed_reproduction_dml_rolls_back_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            run = replace(mixed_run(), summary=str(root.with_suffix(".md")))
            publish_saved_run(root, run)
            new_run = replace(run, run_id="reproduce-rollback")
            before = result_store_path(root).read_bytes()
            with self.assertRaisesRegex(RuntimeError, "inject reproduction failure"):
                with result_transaction(root) as db:
                    write_saved_run(db, new_run)
                    self.assertEqual(load_saved_run(db, new_run.run_id), new_run)
                    raise RuntimeError("inject reproduction failure")
            with result_snapshot(root) as db:
                self.assertEqual(load_saved_run(db, run.run_id), run)
                self.assertIsNone(load_saved_run(db, new_run.run_id))
                self.assertEqual(
                    db.execute("SELECT count(*) FROM reproduction_runs").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT DISTINCT run_id FROM reproduction_latest_commands"
                    ).fetchall()[0][0],
                    run.run_id,
                )
            self.assertEqual(result_store_path(root).read_bytes(), before)

    def test_symlinked_store_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            cache = root / ".cache"
            cache.mkdir()
            (cache / "results.sqlite").symlink_to(Path(directory) / "outside.sqlite")
            with self.assertRaises(ResultStoreError) as raised:
                with result_transaction(root):
                    pass
            self.assertEqual(raised.exception.code, "results.store.malformed")

    def test_public_store_error_matrix_has_exact_read_and_write_codes(self) -> None:
        """Contended, malformed, and future stores have stable public codes."""

        cases = (
            ("busy", "results.store.busy"),
            ("malformed", "results.store.malformed"),
            ("unsupported", "results.schema.unsupported"),
        )
        for mode, expected_code in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "study"
                root.mkdir()
                path = result_store_path(root)
                path.parent.mkdir()
                holder: sqlite3.Connection | None = None
                if mode == "busy":
                    with result_transaction(root) as db:
                        db.execute(
                            "INSERT INTO store_state VALUES "
                            "('validation', 1, 'study.md')"
                        )
                    holder = sqlite3.connect(path, isolation_level=None)
                    holder.execute("BEGIN EXCLUSIVE")
                elif mode == "malformed":
                    path.write_bytes(b"not a sqlite database")
                else:
                    with sqlite3.connect(path) as db:
                        db.execute("PRAGMA user_version=999")
                try:
                    for operation in (result_snapshot, result_transaction):
                        with self.subTest(operation=operation.__name__):
                            with self.assertRaises(ResultStoreError) as raised:
                                with operation(root):
                                    pass
                            self.assertEqual(raised.exception.code, expected_code)
                finally:
                    if holder is not None:
                        holder.rollback()
                        holder.close()
