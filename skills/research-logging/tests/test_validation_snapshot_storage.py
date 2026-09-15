from __future__ import annotations

import importlib
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import mechanical_log, mock, unittest

CONTROLLER = importlib.import_module("validation.controller")
DOMAIN = importlib.import_module("validation.domain")
ENGINE = importlib.import_module("validation.engine")
LOCATOR = importlib.import_module("validation.locator")
QUERIES = importlib.import_module("validation.read_model")
STORE = importlib.import_module("research_log_result_store")
SNAPSHOTS = importlib.import_module("validation.snapshot_storage")


def _evaluation(workspace: Path, *, entry_target: bool = False) -> tuple[object, Path]:
    summary, entry = mechanical_log(workspace)
    (entry.parent / "data/results.csv").write_text(
        "success_rate\n0.5\n", encoding="utf-8"
    )
    target = (
        ENGINE.EntryEvaluationTarget("e001", entry.parent)
        if entry_target
        else ENGINE.FullEvaluationTarget()
    )
    result = ENGINE.evaluate_mechanical(
        ENGINE.EvaluationRequest(summary, target)
    )
    assert result.snapshot is not None
    return result, summary


def _seed_v17(root: Path) -> None:
    path = STORE.result_store_path(root)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript(STORE._SHARED_DDL)
        db.executescript(STORE._VALIDATION_V19_DDL)
        db.execute(
            "CREATE TABLE validation_batch_nodes ("
            "snapshot_pk INTEGER NOT NULL, batch_pk INTEGER NOT NULL, "
            "position INTEGER NOT NULL, node_pk INTEGER NOT NULL, "
            "PRIMARY KEY(snapshot_pk, batch_pk, position), "
            "FOREIGN KEY(snapshot_pk, batch_pk) REFERENCES "
            "validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE, "
            "FOREIGN KEY(snapshot_pk, node_pk) REFERENCES "
            "validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE"
            ") WITHOUT ROWID"
        )
        db.execute("PRAGMA user_version=17")
        db.execute(
            "INSERT INTO store_state VALUES ('validation', 4, 'old validation')"
        )
        db.execute(
            "INSERT INTO store_state VALUES ('reproduction', 7, 'reproduction')"
        )
        db.execute(
            "INSERT INTO report_materializations VALUES "
            "('validation',4,'old-validation','2026-09-13T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO report_materializations VALUES "
            "('reproduction',7,'reproduction','2026-09-13T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO validation_snapshots VALUES "
            "(1,'sentinel-old-snapshot',4,'full','log','study.md',NULL,'clear',"
            "0,0,0,0,0,'start','finish','stored','old-rules','old-source',"
            "'{}')"
        )
        db.execute(
            "INSERT INTO reproduction_metadata VALUES "
            "(1,'preserved reproduction','2026-09-13T00:00:00Z')"
        )


def _database_dump(root: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(STORE.result_store_path(root)) as db:
        tables = tuple(
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        values: list[tuple[object, ...]] = [
            ("version", db.execute("PRAGMA user_version").fetchone()[0])
        ]
        for table in tables:
            values.append(("schema", table, db.execute(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]))
            values.extend(
                ("row", table, *row)
                for row in db.execute(f"SELECT * FROM {table}")
            )
        return tuple(values)


def _reproduction_dump(root: Path) -> tuple[tuple[object, ...], ...]:
    return tuple(
        row
        for row in _database_dump(root)
        if len(row) >= 2
        and isinstance(row[1], str)
        and row[1].startswith("reproduction_")
    )


def _shared_neighborhood_snapshot(
    root: Path,
    batch_count: int,
    node_count: int,
    *,
    shared_batch: bool = False,
):
    command = DOMAIN.GraphReference("command", "e001:shared", "e001")
    repair_key = DOMAIN.RepairKey(
        DOMAIN.RepairKeyKind.COMMAND, "e001:shared", "e001"
    )
    materials = tuple(
        DOMAIN.GraphReference("material", f"data/output-{index}.csv", "e001")
        for index in range(node_count - 1)
    )
    checks = tuple(
        DOMAIN.RuleCheck(
            f"finding:{index}",
            DOMAIN.RuleArea.CONFORMANCE,
            DOMAIN.CheckOutcome.FINDING,
            f"subject-{index}",
            diagnostic=DOMAIN.CheckDiagnostic(
                "synthetic.failure",
                f"subject-{index}",
                "Synthetic failure rule",
                {"index": index},
            ),
            issue_context=DOMAIN.IssueContext(
                entry="e001",
                context_nodes=(command,),
                repair_keys=(repair_key,) if shared_batch else (),
            ),
        )
        for index in range(batch_count)
    )
    attempt = DOMAIN.ValidationAttempt.build(
        target=DOMAIN.ValidationTarget(
            DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
        ),
        source_identity="shared-neighborhood",
        rules_version="rules",
        started_at="2026-09-14T00:00:00Z",
        finished_at="2026-09-14T00:00:01Z",
        checks=checks,
    )
    nodes = (command, *materials)
    return DOMAIN.ValidationSnapshot.from_attempt(
        attempt,
        repair_context={
            "ambiguities": [],
            "nodes": [
                {
                    "attributes": {},
                    "entry": node.entry,
                    "identity": node.identity,
                    "kind": node.kind,
                    "node_id": node.node_id,
                }
                for node in nodes
            ],
            "relationships": [
                {
                    "kind": "production",
                    "source": command.node_id,
                    "target": material.node_id,
                }
                for material in materials
            ],
        },
        report_context={
            "entries": {},
            "presentations": {
                "synthetic.failure": {
                    "name": "Synthetic failure",
                    "sentence": "The synthetic condition failed.",
                    "target_kind": "record",
                }
            },
            "title": "Study",
        },
    )


def _same_named_material_snapshot(root: Path):
    materials = tuple(
        DOMAIN.GraphReference("material", f"/project/{entry}/catalog.csv", entry)
        for entry in ("entry-a", "entry-b")
    )
    checks = tuple(
        DOMAIN.RuleCheck(
            f"provenance:origin:{index}",
            DOMAIN.RuleArea.PROVENANCE,
            DOMAIN.CheckOutcome.FINDING,
            "catalog",
            diagnostic=DOMAIN.CheckDiagnostic(
                "data.origin.invalid",
                "catalog",
                "Origin boundary",
                {"producer": f"producer-{index}"},
            ),
            issue_context=DOMAIN.IssueContext(
                entry=material.entry,
                repair_keys=(
                    DOMAIN.RepairKey(
                        DOMAIN.RepairKeyKind.MATERIAL,
                        material.identity,
                        material.entry,
                    ),
                ),
                context_nodes=(material,),
                admission_owner=DOMAIN.AdmissionOwner.MATERIAL,
            ),
        )
        for index, material in enumerate(materials)
    )
    attempt = DOMAIN.ValidationAttempt.build(
        target=DOMAIN.ValidationTarget(
            DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
        ),
        source_identity="same-named-materials",
        rules_version="rules",
        started_at="2026-09-14T00:00:00Z",
        finished_at="2026-09-14T00:00:01Z",
        checks=checks,
    )
    return DOMAIN.ValidationSnapshot.from_attempt(
        attempt,
        repair_context={
            "ambiguities": [],
            "nodes": [
                {
                    "attributes": {},
                    "entry": material.entry,
                    "identity": material.identity,
                    "kind": material.kind,
                    "node_id": material.node_id,
                }
                for material in materials
            ],
            "relationships": [],
        },
        report_context={
            "entries": {},
            "presentations": {
                "data.origin.invalid": {
                    "name": "Invalid origin",
                    "sentence": "A declared origin is produced inside the log.",
                    "target_kind": "record",
                }
            },
            "title": "Study",
        },
    )


class ValidationSnapshotStorageTests(unittest.TestCase):
    def test_completed_snapshot_round_trips_through_normalized_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation, summary = _evaluation(Path(directory))
            snapshot = evaluation.snapshot
            assert snapshot is not None

            retained = SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(
                    summary.with_suffix(""),
                    snapshot,
                    {
                        "requested": ("e001",),
                        "evaluated": ("e001",),
                        "dependency": (),
                    },
                )
            )
            loaded = SNAPSHOTS.load_validation_snapshot(summary.with_suffix(""))

            self.assertEqual(
                loaded,
                replace(snapshot, stored_at=retained.stored_at),
            )
            self.assertEqual(retained.snapshot_id, snapshot.internal_snapshot_id)
            self.assertEqual(retained.generation, 1)
            with STORE.result_snapshot(summary.with_suffix("")) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 19)
                self.assertFalse(
                    {
                        "validation_results",
                        "validation_checks",
                        "validation_groups",
                    }
                    & {
                        row[0]
                        for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                )
                self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

    def test_same_named_material_owners_round_trip_without_collapsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _same_named_material_snapshot(root)

            retained = SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
            )
            loaded = SNAPSHOTS.load_validation_snapshot(root)

            self.assertEqual(loaded, replace(snapshot, stored_at=retained.stored_at))
            self.assertEqual(len(loaded.findings), 2)
            self.assertEqual(len(loaded.batches), 2)
            self.assertEqual(
                {finding.context_nodes[0].identity for finding in loaded.findings},
                {
                    "/project/entry-a/catalog.csv",
                    "/project/entry-b/catalog.csv",
                },
            )

    def test_v17_replacement_discards_validation_and_preserves_reproduction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation, summary = _evaluation(Path(directory))
            snapshot = evaluation.snapshot
            assert snapshot is not None
            root = summary.with_suffix("")
            _seed_v17(root)
            reproduction_before = _reproduction_dump(root)
            authored = {
                path: path.read_bytes()
                for path in summary.parent.rglob("*")
                if path.is_file() and ".cache" not in path.parts
            }

            retained = SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(
                    root,
                    snapshot,
                    {"requested": ("e001",), "evaluated": ("e001",)},
                )
            )

            self.assertEqual(retained.generation, 5)
            self.assertEqual(_reproduction_dump(root), reproduction_before)
            self.assertEqual(
                {path: path.read_bytes() for path in authored},
                authored,
            )
            with STORE.result_snapshot(root) as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table'"
                    )
                }
                self.assertNotIn("validation_results", tables)
                self.assertNotIn("validation_checks", tables)
                self.assertNotIn("validation_batch_nodes", tables)
                self.assertNotIn("sentinel-old-snapshot", str(tuple(db.iterdump())))
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations "
                        "WHERE kind='validation'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                )
                self.assertEqual(
                    tuple(
                        db.execute(
                        "SELECT generation,summary FROM store_state "
                        "WHERE domain='reproduction'"
                        ).fetchone()
                    ),
                    (7, "reproduction"),
                )

    def test_first_v17_entry_replacement_creates_no_full_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation, summary = _evaluation(Path(directory), entry_target=True)
            snapshot = evaluation.snapshot
            assert snapshot is not None
            root = summary.with_suffix("")
            _seed_v17(root)

            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(
                    root,
                    snapshot,
                    {"requested": ("e001",), "evaluated": ("e001",)},
                )
            )

            self.assertEqual(
                QUERIES.show_validation(root)["rows"][0]["outcome"],
                "Not validated",
            )
            selected = QUERIES.list_findings(root, entry="e001", limit=100)
            self.assertEqual(selected["selected_target"]["kind"], "entry")
            with STORE.result_snapshot(root) as db:
                self.assertEqual(
                    [
                        row[0]
                        for row in db.execute(
                            "SELECT slot FROM validation_snapshots ORDER BY slot"
                        )
                    ],
                    ["entry:e001"],
                )

    def test_every_replacement_failure_point_rolls_back_v17_exactly(self) -> None:
        for failure_phase in (
            "before_transaction",
            "after_schema_replacement",
            "after_snapshot_insert",
            "before_commit",
        ):
            with (
                self.subTest(phase=failure_phase),
                tempfile.TemporaryDirectory() as directory,
            ):
                evaluation, summary = _evaluation(Path(directory))
                snapshot = evaluation.snapshot
                assert snapshot is not None
                root = summary.with_suffix("")
                _seed_v17(root)
                before = _database_dump(root)

                def fail(phase: str) -> None:
                    if phase == failure_phase:
                        raise RuntimeError(failure_phase)

                with self.assertRaisesRegex(RuntimeError, failure_phase):
                    SNAPSHOTS.publish_validation_snapshot(
                        SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {}),
                        _test_hook=fail,
                    )

                self.assertEqual(_database_dump(root), before)

    def test_invalid_candidate_is_rejected_before_store_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation, summary = _evaluation(Path(directory))
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                evaluation.attempt,
                repair_context={
                    "ambiguities": (),
                    "nodes": (),
                    "relationships": (),
                },
            )
            root = summary.with_suffix("")

            with self.assertRaisesRegex(ValueError, "missing repair-context node"):
                SNAPSHOTS.publish_validation_snapshot(
                    SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
                )

            self.assertFalse(STORE.result_store_path(root).exists())

    def test_lossy_repair_context_is_rejected_before_store_creation(self) -> None:
        cases = ("top-level field", "node field", "duplicate candidates")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                evaluation, summary = _evaluation(Path(directory))
                original = evaluation.snapshot
                assert original is not None
                context = original.as_dict()["repair_context"]
                assert isinstance(context, dict)
                if case == "top-level field":
                    context["unsupported"] = True
                    message = "repair context has unsupported fields"
                elif case == "node field":
                    nodes = context["nodes"]
                    assert isinstance(nodes, list) and nodes
                    nodes[0]["unsupported"] = True
                    message = "repair node has unsupported fields"
                else:
                    nodes = context["nodes"]
                    assert isinstance(nodes, list) and nodes
                    node_id = nodes[0]["node_id"]
                    context["ambiguities"] = [
                        {
                            "candidates": [node_id, node_id],
                            "kind": "producer-candidate",
                            "observed": {"count": 2},
                            "subject": node_id,
                        }
                    ]
                    message = "ambiguity candidates must be unique"
                candidate = DOMAIN.ValidationSnapshot.from_attempt(
                    evaluation.attempt,
                    repair_context=context,
                    report_context=original.report_context,
                )
                root = summary.with_suffix("")

                with self.assertRaisesRegex(ValueError, message):
                    SNAPSHOTS.publish_validation_snapshot(
                        SNAPSHOTS.SnapshotPublicationRequest(root, candidate, {})
                    )

                self.assertFalse(STORE.result_store_path(root).exists())

    def test_shared_graph_is_stored_once_and_batch_detail_is_derived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _shared_neighborhood_snapshot(root, 4, 6)
            build = SNAPSHOTS._RepairNeighborhoodIndex.build
            with mock.patch.object(
                SNAPSHOTS._RepairNeighborhoodIndex,
                "build",
                side_effect=build,
            ) as indexed:
                SNAPSHOTS.publish_validation_snapshot(
                    SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
                )
                indexed.assert_not_called()
                self.assertEqual(
                    len(
                        SNAPSHOTS.batch_repair_node_ids(
                            snapshot, snapshot.batches[0].batch_id
                        )
                    ),
                    6,
                )
                indexed.assert_called_once()
            with STORE.result_snapshot(root) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_repair_nodes"
                    ).fetchone()[0],
                    6,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_finding_nodes"
                    ).fetchone()[0],
                    4,
                )
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM sqlite_schema WHERE type='table' "
                        "AND name='validation_batch_nodes'"
                    ).fetchone()
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _shared_neighborhood_snapshot(root, 4, 6)
            with (
                mock.patch.object(
                    SNAPSHOTS, "MAX_VALIDATION_SNAPSHOT_ROWS", 1
                ),
                self.assertRaisesRegex(ValueError, "row bound"),
            ):
                SNAPSHOTS.publish_validation_snapshot(
                    SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
                )
            self.assertFalse(STORE.result_store_path(root).exists())

    def test_one_batch_detail_walk_does_not_multiply_by_saved_batch_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _shared_neighborhood_snapshot(root, 50, 50)
            original = SNAPSHOTS._RepairNeighborhoodCollector._admit
            admissions = 0

            def counted(collector: object, node_id: str) -> None:
                nonlocal admissions
                admissions += 1
                original(collector, node_id)

            with (
                mock.patch.object(
                    SNAPSHOTS._RepairNeighborhoodCollector,
                    "_admit",
                    counted,
                ),
            ):
                SNAPSHOTS.publish_validation_snapshot(
                    SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
                )
                self.assertEqual(admissions, 0)
                members = SNAPSHOTS.batch_repair_node_ids(
                    snapshot, snapshot.batches[0].batch_id
                )

            self.assertEqual(len(members), 50)
            self.assertLess(admissions, 250)

    def test_finding_detail_preflight_projects_each_large_batch_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _shared_neighborhood_snapshot(
                root, 1_000, 2, shared_batch=True
            )
            original = SNAPSHOTS._batch_detail_value
            with mock.patch.object(
                SNAPSHOTS,
                "_batch_detail_value",
                side_effect=original,
            ) as projected:
                SNAPSHOTS.publish_validation_snapshot(
                    SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
                )

            self.assertEqual(len(snapshot.batches), 1)
            projected.assert_called_once()

    def test_dry_run_and_operation_failure_do_not_replace_v17(self) -> None:
        for case in ("dry-run", "source-change"):
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                _, summary = _evaluation(workspace)
                root = summary.with_suffix("")
                _seed_v17(root)
                before = _database_dump(root)
                authored_before = {
                    path: path.read_bytes()
                    for path in summary.parent.rglob("*")
                    if path.is_file() and ".cache" not in path.parts
                }

                if case == "dry-run":
                    result = CONTROLLER.validate(
                        CONTROLLER.ValidationRequest(summary, publish=False)
                    )
                    self.assertFalse(result.published)
                else:
                    original = CONTROLLER.evaluate_mechanical
                    mutation = (
                        next((root / "entries").iterdir()) / "data/concurrent.txt"
                    )

                    def mutate(*args: object, **kwargs: object):
                        result = original(*args, **kwargs)
                        mutation.write_text("changed\n", encoding="utf-8")
                        return result

                    with mock.patch.object(
                        CONTROLLER, "evaluate_mechanical", side_effect=mutate
                    ):
                        with self.assertRaises(
                            CONTROLLER.ValidationControllerError
                        ):
                            CONTROLLER.validate(CONTROLLER.ValidationRequest(summary))
                    authored_before[mutation] = b"changed\n"

                self.assertEqual(_database_dump(root), before)
                self.assertEqual(
                    {path: path.read_bytes() for path in authored_before},
                    authored_before,
                )

    def test_localized_failure_replaces_old_store_with_failed_snapshot(
        self,
    ) -> None:
        changed = LOCATOR.LocatorV2Error(
            "locator.source.changed",
            "data/results.csv",
            {"reason": "changed"},
            "Stable Source Observation",
            outcome="unavailable",
        )
        for old_version in (17, 18):
            with (
                self.subTest(old_version=old_version),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                _, summary = _evaluation(workspace)
                root = summary.with_suffix("")
                _seed_v17(root)
                with sqlite3.connect(STORE.result_store_path(root)) as db:
                    db.execute(f"PRAGMA user_version={old_version}")

                with mock.patch.object(
                    ENGINE, "require_source_unchanged", side_effect=changed
                ):
                    result = CONTROLLER.validate(
                        CONTROLLER.ValidationRequest(summary)
                    )

                self.assertTrue(result.published)
                self.assertIs(
                    result.snapshot.outcome, DOMAIN.SnapshotOutcome.FAILED
                )
                self.assertEqual(len(result.snapshot.failed_checks), 1)
                with STORE.result_snapshot(root) as db:
                    self.assertEqual(
                        db.execute("PRAGMA user_version").fetchone()[0], 19
                    )
                    self.assertNotIn(
                        "sentinel-old-snapshot", str(tuple(db.iterdump()))
                    )

    def test_entry_and_full_slots_have_replacement_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            full, summary = _evaluation(Path(directory))
            entry = ENGINE.evaluate_mechanical(
                ENGINE.EvaluationRequest(
                    summary,
                    ENGINE.EntryEvaluationTarget(
                        "e001",
                        summary.with_suffix("")
                        / "entries"
                        / "2026-08-29-e001-study",
                    ),
                )
            )
            self.assertIsNotNone(entry.snapshot)
            assert full.snapshot is not None and entry.snapshot is not None
            root = summary.with_suffix("")

            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, full.snapshot, {})
            )
            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, entry.snapshot, {})
            )
            with STORE.result_snapshot(root) as db:
                self.assertEqual(
                    {
                        row[0]
                        for row in db.execute(
                            "SELECT slot FROM validation_snapshots"
                        )
                    },
                    {"full", "entry:e001"},
                )

            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, full.snapshot, {})
            )
            with STORE.result_snapshot(root) as db:
                self.assertEqual(
                    [
                        row[0]
                        for row in db.execute(
                            "SELECT slot FROM validation_snapshots"
                        )
                    ],
                    ["full"],
                )

    def test_shared_repair_identity_preserves_each_finding_repair_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            checks = tuple(
                DOMAIN.RuleCheck(
                    f"provenance:{entry}",
                    DOMAIN.RuleArea.PROVENANCE,
                    DOMAIN.CheckOutcome.FINDING,
                    "data/shared.csv",
                    diagnostic=DOMAIN.CheckDiagnostic(
                        "producer.conflict",
                        "data/shared.csv",
                        "Producer ownership",
                        {"entry": entry},
                    ),
                    issue_context=DOMAIN.IssueContext(
                        entry=entry,
                        repair_keys=(
                            DOMAIN.RepairKey(
                                DOMAIN.RepairKeyKind.MATERIAL,
                                "data/shared.csv",
                                entry,
                            ),
                        ),
                    ),
                )
                for entry in ("e001", "e002")
            )
            attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
                ),
                source_identity="source",
                rules_version="rules",
                started_at="start",
                finished_at="finish",
                checks=checks,
            )
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                attempt,
                report_context={
                    "entries": {},
                    "presentations": {
                        "producer.conflict": {
                            "name": "Conflicting producers",
                            "sentence": "The producer ownership is conflicting.",
                            "target_kind": "record",
                        }
                    },
                    "title": "Study",
                },
            )

            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(
                    root,
                    snapshot,
                    {"evaluated": ("e001", "e002")},
                )
            )
            loaded = SNAPSHOTS.load_validation_snapshot(root)

        self.assertEqual(loaded.findings, snapshot.findings)
        self.assertEqual(loaded.batches[0].repair_entries, ("e001", "e002"))


if __name__ == "__main__":
    unittest.main()
