from __future__ import annotations

import importlib
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import mechanical_log, mock, unittest

DOMAIN = importlib.import_module("validation.domain")
ENGINE = importlib.import_module("validation.engine")
ENCODER = importlib.import_module("result_export_encoder")
SNAPSHOTS = importlib.import_module("validation.snapshot_storage")
STORE = importlib.import_module("research_log_result_store")


def _snapshot(workspace: Path) -> tuple[Path, object]:
    summary, entry = mechanical_log(workspace)
    (entry.parent / "data/results.csv").write_text(
        "success_rate\n0.5\n", encoding="utf-8"
    )
    evaluation = ENGINE.evaluate_mechanical(
        ENGINE.EvaluationRequest(summary)
    )
    assert evaluation.snapshot is not None
    return summary.with_suffix(""), evaluation.snapshot


def _publish(root: Path, snapshot: object) -> object:
    return SNAPSHOTS.publish_validation_snapshot(
        SNAPSHOTS.SnapshotPublicationRequest(
            root,
            snapshot,
            {"requested": ("e001",), "evaluated": ("e001",)},
        )
    )


def _rows(root: Path) -> tuple[tuple[object, ...], ...]:
    with STORE.result_snapshot(root) as db:
        rows: list[tuple[object, ...]] = []
        for table in (
            "validation_snapshots",
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
        ):
            rows.extend(
                (table, *tuple(row))
                for row in db.execute(f"SELECT * FROM {table}")
            )
        return tuple(rows)


class ValidationSnapshotStorageConformanceTests(unittest.TestCase):
    def test_normalized_rows_have_exact_canonical_counts_and_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _snapshot(Path(directory))
            retained = _publish(root, snapshot)

            with STORE.result_snapshot(root) as db:
                header = db.execute(
                    "SELECT finding_count,batch_count,generation "
                    "FROM validation_snapshots WHERE slot='full'"
                ).fetchone()
                memberships = db.execute(
                    "SELECT count(*) FROM validation_batch_findings"
                ).fetchone()[0]
                unique_memberships = db.execute(
                    "SELECT count(DISTINCT finding_pk) "
                    "FROM validation_batch_findings"
                ).fetchone()[0]
                self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

        self.assertEqual(
            tuple(header),
            (len(snapshot.findings), len(snapshot.batches), retained.generation),
        )
        self.assertEqual(memberships, len(snapshot.findings))
        self.assertEqual(unique_memberships, len(snapshot.findings))

    def test_reader_rejects_corrupt_canonical_json_and_counts(self) -> None:
        corruptions = (
            (
                "observed",
                "UPDATE validation_findings SET observed_json='not json' "
                "WHERE position=0",
            ),
            (
                "count",
                "UPDATE validation_snapshots SET finding_count=finding_count+1",
            ),
        )
        for name, statement in corruptions:
            with (
                self.subTest(corruption=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root, snapshot = _snapshot(Path(directory))
                _publish(root, snapshot)
                with sqlite3.connect(STORE.result_store_path(root)) as db:
                    db.execute(statement)

                with self.assertRaises(STORE.ResultStoreError) as raised:
                    SNAPSHOTS.load_validation_snapshot(root)
                self.assertEqual(raised.exception.code, "results.store.malformed")

    def test_foreign_keys_reject_dangling_context_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _snapshot(Path(directory))
            _publish(root, snapshot)

            with STORE.result_transaction(root) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "INSERT INTO validation_finding_nodes "
                        "VALUES (1,1,999,'context',999)"
                    )

    def test_publication_byte_bound_is_exact_and_atomic(self) -> None:
        fixed = "2026-09-14T00:00:02.000000+00:00"
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _snapshot(Path(directory))
            baseline = _publish(root, snapshot)
            before = _rows(root)
            prepared = replace(snapshot, stored_at=fixed)
            exact = ENCODER.measure_json_value(prepared.as_dict(), 2**31)

            fixed_now = mock.Mock()
            fixed_now.isoformat.return_value = fixed
            fixed_clock = mock.Mock()
            fixed_clock.now.return_value = fixed_now
            with (
                mock.patch.object(SNAPSHOTS, "datetime", fixed_clock),
                mock.patch.object(SNAPSHOTS, "MAX_VALIDATION_SNAPSHOT_BYTES", exact),
            ):
                accepted = _publish(root, snapshot)
            self.assertGreater(accepted.generation, baseline.generation)
            accepted_rows = _rows(root)

            with (
                mock.patch.object(SNAPSHOTS, "datetime", fixed_clock),
                mock.patch.object(
                    SNAPSHOTS, "MAX_VALIDATION_SNAPSHOT_BYTES", exact - 1
                ),
                self.assertRaisesRegex(ValueError, "byte bound"),
            ):
                _publish(root, snapshot)

            self.assertNotEqual(accepted_rows, before)
            self.assertEqual(_rows(root), accepted_rows)

    def test_publication_rejects_a_finding_detail_that_cannot_be_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            check = DOMAIN.RuleCheck(
                "oversized:finding",
                DOMAIN.RuleArea.CONFORMANCE,
                DOMAIN.CheckOutcome.FINDING,
                "oversized finding",
                diagnostic=DOMAIN.CheckDiagnostic(
                    "synthetic.oversized",
                    "oversized finding",
                    "Synthetic diagnostic bound",
                    {"detail": "x" * SNAPSHOTS.MAX_VALIDATION_RESPONSE_BYTES},
                ),
                issue_context=DOMAIN.IssueContext(entry="e001"),
            )
            attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
                ),
                source_identity="oversized-source",
                rules_version="rules",
                started_at="2026-09-14T00:00:00Z",
                finished_at="2026-09-14T00:00:01Z",
                checks=(check,),
            )
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                attempt,
                report_context={
                    "entries": {},
                    "presentations": {
                        "synthetic.oversized": {
                            "name": "Oversized finding",
                            "sentence": "The synthetic diagnostic is oversized.",
                            "target_kind": "record",
                        }
                    },
                    "title": "Study",
                },
            )

            with self.assertRaisesRegex(ValueError, "finding detail header"):
                _publish(root, snapshot)

            self.assertFalse(STORE.result_store_path(root).exists())

    def test_row_bound_rejects_before_slot_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _snapshot(Path(directory))
            _publish(root, snapshot)
            before = _rows(root)

            with (
                mock.patch.object(SNAPSHOTS, "MAX_VALIDATION_SNAPSHOT_ROWS", 0),
                self.assertRaisesRegex(ValueError, "row bound"),
            ):
                _publish(root, snapshot)

            self.assertEqual(_rows(root), before)

    def test_stored_finding_machine_fields_have_one_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _snapshot(Path(directory))
            _publish(root, snapshot)

            with STORE.result_snapshot(root) as db:
                columns = {
                    row[1]
                    for row in db.execute(
                        "PRAGMA table_info(validation_findings)"
                    )
                }
                values = [
                    tuple(row)
                    for row in db.execute(
                        "SELECT finding_id,type,code,entry,subject,rule,observed_json "
                        "FROM validation_findings ORDER BY position"
                    )
                ]

        self.assertNotIn("status", columns)
        self.assertNotIn("display_subject", columns)
        self.assertEqual(
            [item[:6] for item in values],
            [
                (
                    finding.finding_id,
                    finding.type.value,
                    finding.code,
                    finding.entry,
                    finding.subject,
                    finding.rule,
                )
                for finding in snapshot.findings
            ],
        )


if __name__ == "__main__":
    unittest.main()
