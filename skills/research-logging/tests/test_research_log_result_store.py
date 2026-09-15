from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.reproduction_result_storage import (
    CommandProjectionRequest,
    ReproductionPublicationRequest,
    ReproductionStorageError,
    _stream_reproduction_export,
    export_reproduction_results,
    initialize_empty_reproduction_results,
    publish_reproduction_results,
    reproduction_artifact_projection,
    reproduction_command_projection,
)
from log_commands.reproduction_results import (
    MAX_RESULT_BYTES,
    ArtifactResult,
    CommandResult,
    ReproductionResults,
    RunFolder,
    RunResult,
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
from result_export_encoder import ExportTooLarge
from validation.operation_state import OperationLockError


class ResultStoreTests(unittest.TestCase):
    def test_reproduction_export_bounds_before_loading_the_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run_with_detail("export-bound", 128)
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest("study.md", run, (), (), (), ()),
            )
            with result_snapshot(root) as db:
                exact_size = _stream_reproduction_export(db, MAX_RESULT_BYTES)
                self.assertEqual(
                    _stream_reproduction_export(db, exact_size), exact_size
                )
                self.assertEqual(
                    _stream_reproduction_export(db, exact_size + 1), exact_size
                )
                with self.assertRaises(ExportTooLarge):
                    _stream_reproduction_export(db, exact_size - 1)
                with mock.patch(
                    "log_commands.reproduction_result_storage._write_run_export",
                    side_effect=AssertionError("decoded a later run"),
                ) as later:
                    with self.assertRaises(ExportTooLarge):
                        _stream_reproduction_export(db, 1)
                later.assert_not_called()
            with (
                mock.patch(
                    "log_commands.reproduction_result_storage."
                    "_stream_reproduction_export",
                    side_effect=ExportTooLarge("too large"),
                ),
                mock.patch(
                    "log_commands.reproduction_result_storage."
                    "_load_reproduction_projection"
                ) as load,
            ):
                with self.assertRaisesRegex(ReproductionStorageError, "exceeds 64 MiB"):
                    export_reproduction_results(path)
            load.assert_not_called()

    def test_reproduction_publication_byte_bound_is_exact_and_atomic(self) -> None:
        for offset, succeeds in ((-1, False), (0, True), (1, True)):
            with (
                self.subTest(offset=offset),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                baseline = _run("baseline")
                candidate = _run_with_detail("candidate", 128)
                measure_root = workspace / "measure"
                measure_root.mkdir()
                measure_path = result_store_path(measure_root)
                for run in (baseline, candidate):
                    publish_reproduction_results(
                        measure_path,
                        ReproductionPublicationRequest("study.md", run, (), (), (), ()),
                    )
                with result_snapshot(measure_root) as db:
                    exact_size = _stream_reproduction_export(db, MAX_RESULT_BYTES)

                root = workspace / "target"
                root.mkdir()
                path = result_store_path(root)
                publish_reproduction_results(
                    path,
                    ReproductionPublicationRequest(
                        "study.md", baseline, (), (), (), ()
                    ),
                )
                self.assertTrue(
                    record_report_materialization(
                        root, "reproduction", b"baseline", expected_generation=1
                    )
                )
                with mock.patch(
                    "log_commands.reproduction_results.MAX_RESULT_BYTES",
                    exact_size + offset,
                ):
                    if succeeds:
                        generation = publish_reproduction_results(
                            path,
                            ReproductionPublicationRequest(
                                "study.md", candidate, (), (), (), ()
                            ),
                        )
                    else:
                        with self.assertRaisesRegex(
                            ReproductionStorageError, "cumulative byte bound"
                        ):
                            publish_reproduction_results(
                                path,
                                ReproductionPublicationRequest(
                                    "study.md", candidate, (), (), (), ()
                                ),
                            )
                with result_snapshot(root) as db:
                    stored_generation = db.execute(
                        "SELECT generation FROM store_state WHERE domain='reproduction'"
                    ).fetchone()[0]
                    marker = db.execute(
                        "SELECT source_generation FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                    run_ids = {
                        row[0]
                        for row in db.execute("SELECT run_id FROM reproduction_runs")
                    }
                if succeeds:
                    self.assertEqual(generation, 2)
                    self.assertEqual(stored_generation, 2)
                    self.assertIsNone(marker)
                    self.assertEqual(run_ids, {baseline.run_id, candidate.run_id})
                else:
                    self.assertEqual(stored_generation, 1)
                    self.assertEqual(marker[0], 1)
                    self.assertEqual(run_ids, {baseline.run_id})

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
            path = result_store_path(root)
            initialize_empty_reproduction_results(
                path, summary="study.md", updated_at="2030-01-01T00:00:00Z"
            )
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

    def test_validation_only_store_has_cold_reproduction_schema(self) -> None:
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
                self.assertTrue(
                    {
                        "reproduction_metadata",
                        "reproduction_runs",
                        "reproduction_run_commands",
                        "reproduction_run_executions",
                        "reproduction_execution_results",
                        "reproduction_artifact_results",
                        "reproduction_comparison_evidence",
                    }.issubset(tables)
                )
                indexes = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertTrue(
                    {
                        "reproduction_runs_order",
                        "reproduction_run_commands_order",
                        "reproduction_run_executions_order",
                        "reproduction_artifact_outcome",
                    }.issubset(indexes)
                )
                self.assertEqual(
                    db.execute("SELECT count(*) FROM reproduction_metadata").fetchone()[
                        0
                    ],
                    0,
                )

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
            with result_transaction(root):
                pass
            with self.assertRaises(RuntimeError):
                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO reproduction_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",  # noqa: E501
                        (
                            "run-1",
                            "log",
                            None,
                            None,
                            0,
                            "complete",
                            "2030-01-01T00:00:00Z",
                            "2030-01-01T00:00:00Z",
                            "tmp/run-1",
                            "available",
                            "{}",
                            None,
                        ),
                    )
                    db.execute(
                        "INSERT INTO reproduction_execution_results VALUES (?,?,?,?,?,?)",  # noqa: E501
                        ("e001", "execution-1", "matched", "digest", "now", "run-1"),
                    )
                    raise RuntimeError("inject reproduction failure")
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM reproduction_runs").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM reproduction_execution_results"
                    ).fetchone()[0],
                    0,
                )

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

    def test_reproduction_rows_export_only_from_normalized_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            expected = ReproductionResults("study.md", "2030-01-01T00:00:00Z", (), ())
            initialize_empty_reproduction_results(
                path, summary=expected.summary, updated_at=expected.updated_at
            )
            self.assertEqual(export_reproduction_results(path), expected)

    def test_reproduction_run_round_trip_preserves_recorded_folder_availability(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            run = RunResult(
                "reproduce-20300101t000000z-fixture",
                {"entry": None, "kind": "log"},
                False,
                "complete",
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:00:00Z",
                {
                    "changed": 0,
                    "comparison_failed": 0,
                    "failed": 0,
                    "matched": 0,
                    "skipped": 0,
                },
                RunFolder(
                    "tmp/reproduction/2030-01-01/reproduce-research-fixture",
                    "available",
                ),
                (
                    {
                        "elapsed_seconds": 12.86837249994278,
                        "entry": "e002",
                        "cid": "build",
                        "execution_id": "pyrun-exec/v2:" + "2" * 64,
                        "finished_at": "2030-01-01T00:01:00Z",
                        "started_at": "2030-01-01T00:00:00Z",
                    },
                    {
                        "elapsed_seconds": 1.5302276252768934,
                        "entry": "e001",
                        "cid": "build",
                        "execution_id": "pyrun-exec/v2:" + "1" * 64,
                        "finished_at": "2030-01-01T00:02:00Z",
                        "started_at": "2030-01-01T00:01:00Z",
                    },
                ),
            )
            expected = ReproductionResults(
                "study.md", "2030-01-01T00:00:00Z", (), (run,)
            )
            publish_reproduction_results(
                result_store_path(root),
                ReproductionPublicationRequest(expected.summary, run, (), (), (), ()),
            )
            exported = export_reproduction_results(result_store_path(root))
            self.assertEqual(exported.runs[0].folder.availability, "unknown")
            self.assertEqual(exported.runs[0].folder.path, run.folder.path)

    def test_reproduction_command_detail_round_trips_without_duplicate_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run_with_detail("detail", 128)

            publish_reproduction_results(
                path,
                ReproductionPublicationRequest("study.md", run, (), (), (), ()),
            )

            exported = export_reproduction_results(path)
            self.assertEqual(exported.runs[0].command_records, run.command_records)
            _, projected_run, matched, records = reproduction_command_projection(
                path, CommandProjectionRequest(run_id=run.run_id)
            )
            self.assertEqual(projected_run.run_id, run.run_id)
            self.assertEqual(matched, 1)
            self.assertEqual(records, run.command_records)
            with result_snapshot(root) as db:
                columns = {
                    row[1]
                    for row in db.execute(
                        "PRAGMA table_info(reproduction_run_commands)"
                    )
                }
                stored = db.execute(
                    "SELECT details_json,recipe_json,cwd,run_selection "
                    "FROM reproduction_run_commands"
                ).fetchone()
            self.assertNotIn("detail_json", columns)
            self.assertEqual(stored[0], '["' + "x" * 128 + '"]')
            self.assertIsNotNone(stored[1])
            self.assertEqual(stored[2], run.command_records[0]["cwd"])
            self.assertEqual(stored[3], run.command_records[0]["run_selection"])

    def test_partial_publication_prunes_selected_stale_artifacts_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            x = ("e001", "build", "pyrun-exec/v2:" + "1" * 64)
            y = ("e002", "build", "pyrun-exec/v2:" + "2" * 64)
            r1 = _run("run-1")
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    r1,
                    (
                        _artifact("e001", "data/x-current", x[2], r1.run_id),
                        _artifact("e001", "data/x-stale", x[2], r1.run_id),
                        _artifact("e002", "data/y", y[2], r1.run_id),
                        _artifact("e001", "data/pre", None, r1.run_id),
                    ),
                    (_command(*x, r1.run_id), _command(*y, r1.run_id)),
                    (x, y),
                    (("e001", "data/pre"),),
                ),
            )
            r2 = _run("run-2")
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    r2,
                    (
                        _artifact("e001", "data/x-current", x[2], r2.run_id),
                        _artifact("e001", "data/pre", None, r2.run_id),
                    ),
                    (_command(*x, r2.run_id),),
                    (x,),
                    (("e001", "data/pre"),),
                ),
            )
            stored = export_reproduction_results(path)
            artifacts = {(item.entry, item.artifact): item for item in stored.artifacts}
            commands = {
                (item.entry, item.cid, item.execution_id): item
                for item in stored.commands
            }
            self.assertNotIn(("e001", "data/x-stale"), artifacts)
            self.assertEqual(artifacts[("e001", "data/x-current")].run_id, r2.run_id)
            self.assertEqual(artifacts[("e001", "data/pre")].run_id, r2.run_id)
            self.assertEqual(artifacts[("e002", "data/y")].run_id, r1.run_id)
            self.assertEqual(commands[x].run_id, r2.run_id)
            self.assertEqual(commands[y].run_id, r1.run_id)

    def test_near_bound_r1_rejects_partial_r2_without_changing_current_or_history(
        self,
    ) -> None:
        """The exact staged export bound rolls back every R2 replacement row."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            x = ("e001", "build", "pyrun-exec/v2:" + "1" * 64)
            y = ("e002", "build", "pyrun-exec/v2:" + "2" * 64)
            r1 = _run_with_detail("r1", MAX_RESULT_BYTES - 128 * 1024)
            request = ReproductionPublicationRequest(
                "study.md",
                r1,
                (
                    _artifact("e001", "data/x", x[2], r1.run_id),
                    _artifact("e002", "data/y", y[2], r1.run_id),
                ),
                (_command(*x, r1.run_id), _command(*y, r1.run_id)),
                (x, y),
                (),
            )
            publish_reproduction_results(path, request)
            self.assertEqual(len(export_reproduction_results(path).runs), 1)
            with result_snapshot(root) as db:
                before = {
                    table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                    for table in (
                        "reproduction_runs",
                        "reproduction_run_commands",
                        "reproduction_run_executions",
                        "reproduction_execution_results",
                        "reproduction_artifact_results",
                        "reproduction_comparison_evidence",
                        "reproduction_metadata",
                        "store_state",
                        "report_materializations",
                    )
                }
            r2 = _run_with_detail("r2", 256 * 1024)
            with self.assertRaisesRegex(
                ReproductionStorageError, "cumulative byte bound"
            ):
                publish_reproduction_results(
                    path,
                    ReproductionPublicationRequest(
                        "study.md",
                        r2,
                        (_artifact("e001", "data/x", x[2], r2.run_id),),
                        (_command(*x, r2.run_id),),
                        (x,),
                        (),
                    ),
                )
            with result_snapshot(root) as db:
                after = {
                    table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                    for table in before
                }
            self.assertEqual(after, before)

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

    def test_artifact_projection_filters_before_bounded_row_decode(self) -> None:
        """The ordinary list projection does not reconstruct unrelated rows."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run("projection")
            execution = "pyrun-exec/v2:" + "1" * 64
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    run,
                    (
                        _artifact("e001", "data/matched", execution, run.run_id),
                        _artifact("e002", "data/other", None, run.run_id),
                    ),
                    (_command("e001", "build", execution, run.run_id),),
                    (("e001", "build", execution),),
                    (("e002", "data/other"),),
                ),
            )
            summary, matched, records = reproduction_artifact_projection(
                path, entry="e001", limit=1
            )
            self.assertEqual(summary, "study.md")
            self.assertEqual(matched, 1)
            self.assertEqual(
                [(item.entry, item.artifact) for item in records],
                [("e001", "data/matched")],
            )

    def test_command_projection_filters_before_decoding_unrelated_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run("commands")
            execution = "pyrun-exec/v2:" + "1" * 64
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    run,
                    (),
                    (_command("e001", "build", execution, run.run_id),),
                    (("e001", "build", execution),),
                    (),
                ),
            )
            with result_transaction(root) as db:
                run_pk = db.execute(
                    "SELECT run_pk FROM reproduction_runs WHERE run_id=?",
                    (run.run_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO reproduction_run_commands "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_pk,
                        "e001",
                        "build",
                        execution,
                        0,
                        "succeeded",
                        "succeeded",
                        "succeeded",
                        None,
                        1,
                        ".",
                        0,
                        None,
                        1,
                        1,
                        "run",
                        "[]",
                        "{}",
                    ),
                )
                db.execute(
                    "INSERT INTO reproduction_run_commands "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_pk,
                        "e999",
                        "build",
                        "pyrun-exec/v2:" + "9" * 64,
                        99,
                        "failed",
                        "failed",
                        "failed",
                        None,
                        1,
                        ".",
                        0,
                        None,
                        1,
                        1,
                        "run",
                        "not-json",
                        "{}",
                    ),
                )
            with self.assertRaisesRegex(ReproductionStorageError, "incorrect fields"):
                reproduction_command_projection(
                    path, CommandProjectionRequest(run.run_id, entry="e001", limit=1)
                )

    def test_null_comparison_rejects_residual_columns_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run("contracts")
            artifact = _artifact("e001", "data/x", None, run.run_id)
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    run,
                    (artifact,),
                    (),
                    (),
                    ((artifact.entry, artifact.artifact),),
                ),
            )
            for column, value in (
                ("comparison_contract", "x"),
                ("expected_json", "{}"),
                ("regenerated_json", "{}"),
                ("evidence_contract", "x"),
                ("evidence_definition", "x"),
            ):
                with self.subTest(column=column), result_transaction(root) as db:
                    db.execute(
                        f"UPDATE reproduction_artifact_results SET {column}=?", (value,)
                    )
                with self.assertRaisesRegex(ReproductionStorageError, "comparison"):
                    reproduction_artifact_projection(path)
                with result_transaction(root) as db:
                    db.execute(
                        f"UPDATE reproduction_artifact_results SET {column}=NULL"
                    )
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO reproduction_comparison_evidence "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    ("e001", "data/x", 0, None, "{}", "{}", "{}", 1),
                )
            with self.assertRaisesRegex(ReproductionStorageError, "orphan"):
                reproduction_artifact_projection(path)

    def test_invalid_selected_projection_preserves_prior_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = result_store_path(root)
            run = _run("run-1")
            execution = "pyrun-exec/v2:" + "1" * 64
            command = _command("e001", "build", execution, run.run_id)
            artifact = _artifact("e001", "data/x", execution, run.run_id)
            publish_reproduction_results(
                path,
                ReproductionPublicationRequest(
                    "study.md",
                    run,
                    (artifact,),
                    (command,),
                    ((command.entry, command.cid, command.execution_id),),
                    (),
                ),
            )
            before = export_reproduction_results(path)
            with self.assertRaisesRegex(
                Exception, "selected reproduction identities are invalid"
            ):
                publish_reproduction_results(
                    path,
                    ReproductionPublicationRequest(
                        "study.md",
                        _run("run-2"),
                        (_artifact("e001", "data/x", execution, "reproduce-run-2"),),
                        (_command("e001", "build", execution, "reproduce-run-2"),),
                        (),
                        (),
                    ),
                )
            self.assertEqual(export_reproduction_results(path), before)


def _run(run_id: str) -> RunResult:
    run_id = f"reproduce-{run_id}"
    return RunResult(
        run_id,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        "2030-01-01T00:00:00Z",
        "2030-01-01T00:00:00Z",
        {"changed": 0, "comparison_failed": 0, "failed": 0, "matched": 0, "skipped": 0},
        RunFolder(f"tmp/reproduction/2030-01-01/reproduce-{run_id}", "available"),
        (),
    )


def _run_with_detail(run_id: str, detail_bytes: int) -> RunResult:
    """Build one valid command record whose export contribution is near the cap."""

    run = _run(run_id)
    record = {
        "auto_reproduce": True,
        "bucket": "succeeded",
        "cwd": "docs/research/entries/2030-01-01-e001-example",
        "details": ["x" * detail_bytes],
        "entry": "e001",
        "cid": "build",
        "execution_id": "pyrun-exec/v2:" + "1" * 64,
        "exclusive": False,
        "prior_disposition": None,
        "queued": True,
        "reason": "succeeded",
        "recipe": {
            "environment": {},
            "inputs": [],
            "outputs": {"data/result.txt": "file"},
            "parameters": [],
            "parameter_roles": {},
            "script": "scripts/build.py",
        },
        "requires_reproduction": True,
        "run_selection": "run",
        "source_digest": "1" * 64,
        "terminal_disposition": "succeeded",
    }
    return replace(
        run,
        command_outcomes={
            "blocked": 0,
            "failed": 0,
            "not_automatic": 0,
            "reproduction_not_needed": 0,
            "succeeded": 1,
            "total": 1,
            "unchanged_blocked": 0,
            "unchanged_failed": 0,
        },
        command_records=(record,),
    )


def _command(entry: str, cid: str, execution_id: str, run_id: str) -> CommandResult:
    return CommandResult(
        entry,
        cid,
        execution_id,
        "succeeded",
        "d" * 64,
        "2030-01-01T00:00:00Z",
        run_id,
    )


def _artifact(
    entry: str, artifact: str, execution_id: str | None, run_id: str
) -> ArtifactResult:
    return ArtifactResult(
        entry,
        artifact,
        "build" if execution_id is not None else None,
        execution_id,
        "skipped",
        "dependency_failed",
        "2030-01-01T00:00:00Z",
        run_id,
        None,
    )
