from __future__ import annotations

import importlib
import io
import json
import sqlite3
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import research_log_validation_test_support  # noqa: F401
from log_commands.diagnostic_errors import report_diagnostic
from log_commands.dispatcher import _dispatch_command
from log_commands.model import ActionError
from research_log_validation_test_support import mechanical_log, mock, unittest

DIAGNOSTICS = importlib.import_module("validation.command_diagnostics")
ENGINE = importlib.import_module("validation.engine")
RESULTS = importlib.import_module("research_log_result_store")
SNAPSHOTS = importlib.import_module("validation.snapshot_storage")


class CommandDiagnosticStorageTests(unittest.TestCase):
    def test_unrelated_authoring_error_is_not_retained_as_command_diagnostic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            error = ActionError(
                "evidence.syntax.invalid",
                "authored evidence is invalid",
                records=({"kind": "evidence"},),
            )

            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                report_diagnostic(error)

            self.assertFalse(RESULTS.result_store_path(root).exists())

    def test_command_show_loads_latest_or_exact_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            root = summary.with_suffix("")
            with RESULTS.result_transaction(root):
                pass
            identity = DIAGNOSTICS.publish_command_diagnostic(
                root,
                summary.name,
                "command.discovery.failed",
                ({"identity": "command-1", "kind": "rejected"},),
            )

            output = io.StringIO()
            with redirect_stdout(output):
                status = _dispatch_command(
                    (
                        "show",
                        "--path",
                        str(root),
                        "--id",
                        identity,
                        "--format",
                        "json",
                    )
                )
            self.assertEqual(status, 0)
            value = json.loads(output.getvalue())
            self.assertEqual(value["schema"], DIAGNOSTICS.COMMAND_DIAGNOSTIC_SCHEMA)
            self.assertEqual(value["diagnostic_id"], identity)
            self.assertEqual(value["records"][0]["identity"], "command-1")

            text = io.StringIO()
            with redirect_stdout(text):
                self.assertEqual(
                    _dispatch_command(("show", "--path", str(root))), 0
                )
            self.assertIn(identity, text.getvalue())
            self.assertIn("command.discovery.failed", text.getvalue())

    def test_latest_diagnostic_is_command_owned_and_validation_independent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            root = summary.with_suffix("")
            with RESULTS.result_transaction(root):
                pass
            first = DIAGNOSTICS.publish_command_diagnostic(
                root,
                summary.name,
                "command.discovery.failed",
                ({"identity": "command-1", "kind": "rejected"},),
            )
            second = DIAGNOSTICS.publish_command_diagnostic(
                root,
                summary.name,
                "command.declaration.failed",
                ({"identity": "command-2", "kind": "rejected"},),
            )

            with self.assertRaises(RESULTS.ResultStoreError):
                DIAGNOSTICS.load_command_diagnostic(root, diagnostic_id=first)
            retained = DIAGNOSTICS.load_command_diagnostic(
                root, diagnostic_id=second
            )
            evaluation = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(summary)
            )
            assert evaluation.snapshot is not None
            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(
                    root,
                    evaluation.snapshot,
                    {},
                )
            )

            after_validation = DIAGNOSTICS.load_command_diagnostic(root)

        self.assertEqual(retained, after_validation)
        self.assertEqual(after_validation["diagnostic_id"], second)
        self.assertEqual(
            after_validation["records"],
            [{"identity": "command-2", "kind": "rejected"}],
        )

    def test_bounds_reject_before_store_creation(self) -> None:
        for name, value, records in (
            ("MAX_DIAGNOSTIC_RECORDS", 0, ({"kind": "command"},)),
            ("MAX_DIAGNOSTIC_BYTES", 1, ()),
        ):
            with (
                self.subTest(bound=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                with mock.patch.object(DIAGNOSTICS, name, value):
                    with self.assertRaisesRegex(ValueError, "bound"):
                        DIAGNOSTICS.publish_command_diagnostic(
                            root, "study.md", "command.failed", records
                        )
                self.assertFalse(RESULTS.result_store_path(root).exists())

    def test_read_rejects_payload_column_record_and_bound_corruption(self) -> None:
        mutations = (
            ("payload-fields", "UPDATE command_diagnostics SET payload_json='{}'"),
            ("indexed-code", "UPDATE command_diagnostics SET code='different'"),
            (
                "record-json",
                "UPDATE command_diagnostic_records SET record_json='[]'",
            ),
            ("record-position", "UPDATE command_diagnostic_records SET position=1"),
            ("record-kind", "UPDATE command_diagnostic_records SET kind='different'"),
            (
                "state-marker",
                "UPDATE store_state SET summary='different' WHERE domain='command'",
            ),
        )
        for name, statement in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                summary, _ = mechanical_log(Path(directory))
                root = summary.with_suffix("")
                with RESULTS.result_transaction(root):
                    pass
                DIAGNOSTICS.publish_command_diagnostic(
                    root,
                    summary.name,
                    "command.failed",
                    ({"kind": "command", "message": "diagnostic"},),
                )
                with sqlite3.connect(RESULTS.result_store_path(root)) as database:
                    database.execute(statement)

                with self.assertRaises(RESULTS.ResultStoreError) as raised:
                    DIAGNOSTICS.load_command_diagnostic(root)
                self.assertEqual(
                    raised.exception.code, "command.diagnostic.malformed"
                )

    def test_read_rejects_more_than_one_current_diagnostic_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            root = summary.with_suffix("")
            with RESULTS.result_transaction(root):
                pass
            DIAGNOSTICS.publish_command_diagnostic(
                root,
                summary.name,
                "command.failed",
                ({"kind": "command"},),
            )
            payload = json.dumps(
                {
                    "code": "command.failed",
                    "entry": None,
                    "operation": "authoring",
                    "schema": DIAGNOSTICS.COMMAND_DIAGNOSTIC_SCHEMA,
                    "summary": summary.name,
                }
            )
            with sqlite3.connect(RESULTS.result_store_path(root)) as database:
                database.execute(
                    "INSERT INTO command_diagnostics VALUES (2,?,?,?,?,?,?,?,?)",
                    (
                        "duplicate",
                        2,
                        "authoring",
                        "command.failed",
                        None,
                        summary.name,
                        "2026-09-14T00:00:00+00:00",
                        payload,
                    ),
                )

            with self.assertRaises(RESULTS.ResultStoreError) as raised:
                DIAGNOSTICS.load_command_diagnostic(root)
            self.assertEqual(raised.exception.code, "command.diagnostic.malformed")

    def test_read_reapplies_record_and_utf8_byte_bounds(self) -> None:
        for name, bound in (
            ("MAX_DIAGNOSTIC_RECORDS", 0),
            ("MAX_DIAGNOSTIC_BYTES", 8),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                summary, _ = mechanical_log(Path(directory))
                root = summary.with_suffix("")
                with RESULTS.result_transaction(root):
                    pass
                DIAGNOSTICS.publish_command_diagnostic(
                    root,
                    summary.name,
                    "command.failed",
                    ({"kind": "command", "message": "é" * 32},),
                )

                with (
                    mock.patch.object(DIAGNOSTICS, name, bound),
                    self.assertRaises(RESULTS.ResultStoreError) as raised,
                ):
                    DIAGNOSTICS.load_command_diagnostic(root)
                self.assertEqual(
                    raised.exception.code, "command.diagnostic.malformed"
                )

    def test_version_17_store_is_not_replaced_by_command_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            path = RESULTS.result_store_path(root)
            path.parent.mkdir()
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version=17")
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "replacement validation"):
                DIAGNOSTICS.publish_command_diagnostic(
                    root, "study.md", "command.failed", ()
                )

            self.assertEqual(path.read_bytes(), before)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 17)

    def test_read_normalizes_missing_and_unsupported_store_errors(self) -> None:
        for version, code in (
            (None, "command.diagnostic.missing"),
            (99, "command.diagnostic.schema.unsupported"),
        ):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                if version is not None:
                    path = RESULTS.result_store_path(root)
                    path.parent.mkdir()
                    with sqlite3.connect(path) as database:
                        database.execute(f"PRAGMA user_version={version}")

                with self.assertRaises(RESULTS.ResultStoreError) as raised:
                    DIAGNOSTICS.load_command_diagnostic(root)
                self.assertEqual(raised.exception.code, code)

    def test_read_normalizes_shared_store_contention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with (
                mock.patch.object(
                    DIAGNOSTICS,
                    "result_snapshot",
                    side_effect=RESULTS.ResultStoreError(
                        "results.store.busy", "result store is busy"
                    ),
                ),
                self.assertRaises(RESULTS.ResultStoreError) as raised,
            ):
                DIAGNOSTICS.load_command_diagnostic(root)
            self.assertEqual(raised.exception.code, "command.diagnostic.busy")

    def test_diagnostic_publication_preserves_validation_marker_and_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            root = summary.with_suffix("")
            evaluation = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(summary)
            )
            assert evaluation.snapshot is not None
            retained = SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, evaluation.snapshot, {})
            )
            RESULTS.record_report_materialization(
                root,
                "validation",
                b"report",
                expected_generation=retained.generation,
            )
            with RESULTS.result_snapshot(root) as db:
                state_before = tuple(
                    db.execute(
                        "SELECT generation,summary FROM store_state "
                        "WHERE domain='validation'"
                    ).fetchone()
                )
                marker_before = tuple(
                    db.execute(
                        "SELECT * FROM report_materializations "
                        "WHERE kind='validation'"
                    ).fetchone()
                )

            DIAGNOSTICS.publish_command_diagnostic(
                root, summary.name, "command.failed", ()
            )

            with RESULTS.result_snapshot(root) as db:
                self.assertEqual(
                    tuple(
                        db.execute(
                            "SELECT generation,summary FROM store_state "
                            "WHERE domain='validation'"
                        ).fetchone()
                    ),
                    state_before,
                )
                self.assertEqual(
                    tuple(
                        db.execute(
                            "SELECT * FROM report_materializations "
                            "WHERE kind='validation'"
                        ).fetchone()
                    ),
                    marker_before,
                )


if __name__ == "__main__":
    unittest.main()
