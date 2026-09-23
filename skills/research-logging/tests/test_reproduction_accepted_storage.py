"""Replacement accepted-work rows preserve facts without issue projections."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands import reproduction_accepted_storage as storage
from log_commands import reproduction_planner as planner
from log_commands.reproduction_accepted_storage import (
    ACCEPTED_WORK_DDL,
    _load_accepted_work,
    _write_accepted_work,
)
from log_commands.reproduction_domain import ReproductionDomainError, WorkSelection
from reproduction_planning_test_support import prepare_plan
from test_reproduction_canonical_records import blocked_plan
from test_reproduction_model_preservation import fanout_fixture


def header(plan):
    fields = plan.as_dict()
    return {key: fields[key] for key in ("summary", "target", "settings", "admission")}


class AcceptedWorkStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            "CREATE TABLE runs(run_id TEXT PRIMARY KEY);" + ACCEPTED_WORK_DDL
        )
        self.db.execute("INSERT INTO runs VALUES ('run')")
        self.db.commit()
        self.addCleanup(self.db.close)
        self.plan = blocked_plan()

    def save(self, plan=None):
        with self.db:
            _write_accepted_work(self.db, "run", plan or self.plan)

    def load(self, plan=None):
        return _load_accepted_work(self.db, "run", header(plan or self.plan))

    def test_exact_typed_plan_roundtrip_is_read_only_and_source_independent(self):
        self.save()
        changes = self.db.total_changes
        with (
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.resolve",
                side_effect=AssertionError("live path resolution"),
            ),
        ):
            loaded = self.load()
        self.assertEqual(loaded.serialized(), self.plan.serialized())
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_store_write_and_load_do_not_decode_whole_plan_json(self):
        with mock.patch.object(
            storage.ReproductionPlan,
            "from_json",
            side_effect=AssertionError("whole-plan JSON decode"),
        ):
            self.save()
            self.assertEqual(self.load().serialized(), self.plan.serialized())

    def test_identities_and_references_are_not_duplicated_in_work_payloads(self):
        self.save()
        for table, removed in (
            (
                "accepted_work_commands",
                {
                    "identity",
                    "dependencies",
                    "problem_ids",
                    "details",
                    "queued",
                    "auto_reproduce",
                    "exclusive",
                    "requires_reproduction",
                },
            ),
            (
                "accepted_work_artifacts",
                {"identity", "producer", "problem_ids", "cases", "failures"},
            ),
        ):
            for row in self.db.execute(f"SELECT work_json FROM {table}"):
                self.assertFalse(removed & set(json.loads(row[0])))
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM accepted_work_problems").fetchone()[
                0
            ],
            len(self.plan.problems),
        )

    def test_actual_fanout_plan_stores_one_source_cause_not_output_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _ = fanout_fixture(Path(directory))
            (entry.root / "scripts/producer.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            with mock.patch.object(
                planner, "_canonical_plan", wraps=planner._canonical_plan
            ) as project:
                prepare_plan(fixture, entry)
            state, ordered, prepared = project.call_args.args[:3]
            plan = planner._canonical_plan(
                state, ordered, prepared, entry, planner.PreparationHistory()
            )
            self.save(plan)
        self.assertEqual(self.load(plan), plan)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM accepted_work_commands").fetchone()[
                0
            ],
            4,
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM accepted_work_artifacts").fetchone()[
                0
            ],
            13,
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM accepted_work_problems").fetchone()[
                0
            ],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM accepted_work_command_problems"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM accepted_work_artifact_problems"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM accepted_work_dependencies"
            ).fetchone()[0],
            2,
        )

    def test_old_plan_is_rejected_without_inserting_or_translating_rows(self):
        before = self.db.total_changes
        with self.assertRaisesRegex(ReproductionDomainError, "replacement typed plan"):
            _write_accepted_work(
                self.db,
                "run",
                {"schema": "research-log-reproduction-plan/11", "cases": []},
            )
        self.assertEqual(self.db.total_changes, before)

    def test_payload_cannot_shadow_relational_identity_or_links(self):
        self.save()
        row = self.db.execute(
            "SELECT command_pk, work_json FROM accepted_work_commands LIMIT 1"
        ).fetchone()
        payload = json.loads(row["work_json"])
        payload["dependencies"] = []
        self.db.execute(
            "UPDATE accepted_work_commands SET work_json=? WHERE command_pk=?",
            (json.dumps(payload), row["command_pk"]),
        )
        with self.assertRaisesRegex(
            ReproductionDomainError, "duplicates relational fields"
        ):
            self.load()

    def test_changed_problem_observation_cannot_keep_its_old_identity(self):
        self.save()
        row = self.db.execute(
            "SELECT problem_id, problem_json FROM reproduction_problems LIMIT 1"
        ).fetchone()
        problem = json.loads(row["problem_json"])
        problem["observed"]["changed"] = True
        self.db.execute(
            "UPDATE reproduction_problems SET problem_json=? WHERE problem_id=?",
            (json.dumps(problem), row["problem_id"]),
        )
        with self.assertRaisesRegex(ReproductionDomainError, "identity disagrees"):
            self.load()

    def test_invalid_reference_fails_transaction_and_preserves_original_plan(self):
        self.save()
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute(
                "INSERT INTO accepted_work_dependencies VALUES ('run', 1, 999, 999)"
            )
        self.assertEqual(self.load(), self.plan)

    def test_corrupt_unknown_execution_reference_has_domain_error(self):
        self.save()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("UPDATE accepted_work_dependencies SET dependency_pk=999")
        with self.assertRaisesRegex(ReproductionDomainError, "unknown execution"):
            self.load()

    def test_unknown_header_fields_are_not_silently_replaced(self):
        self.save()
        with self.assertRaisesRegex(
            ReproductionDomainError, "header has invalid fields"
        ):
            _load_accepted_work(self.db, "run", {**header(self.plan), "commands": []})

    def test_malformed_infrastructure_has_domain_error_without_writes(self):
        self.save()
        self.db.execute("INSERT INTO accepted_plan_materials VALUES ('run', 0, '{bad')")
        changes = self.db.total_changes
        with self.assertRaisesRegex(ReproductionDomainError, "not JSON"):
            self.load()
        self.assertEqual(self.db.total_changes, changes)

    def test_valid_dependency_deletion_cannot_change_the_accepted_plan(self):
        self.plan = replace(
            self.plan,
            commands=tuple(
                replace(work, selection=WorkSelection.NOT_NEEDED)
                if work.dependencies
                else work
                for work in self.plan.commands
            ),
        )
        self.save()
        self.db.execute("DELETE FROM accepted_work_dependencies")
        changes = self.db.total_changes
        with self.assertRaisesRegex(ReproductionDomainError, "immutable digest"):
            self.load()
        self.assertEqual(self.db.total_changes, changes)

    def test_missing_manifest_cannot_reaccept_reconstructed_rows(self):
        self.save()
        self.db.execute("DELETE FROM accepted_work_manifest")
        with self.assertRaisesRegex(ReproductionDomainError, "immutable digest"):
            self.load()

    def test_each_relation_inventory_is_bounded_before_materialization(self):
        self.save()
        for table, owner_column in (
            ("accepted_work_dependencies", "command_pk"),
            ("accepted_work_command_problems", "command_pk"),
            ("accepted_work_artifact_problems", "artifact_pk"),
        ):
            with (
                self.subTest(table=table),
                mock.patch.object(storage, "MAX_WORK_RECORDS", 2),
            ):
                self.db.commit()
                self.db.execute("PRAGMA foreign_keys=OFF")
                self.db.executemany(
                    f"INSERT INTO {table} VALUES ('run', 999, ?, ?)",
                    ((position, str(position)) for position in range(3)),
                )
                with self.assertRaisesRegex(
                    ReproductionDomainError, "relations exceed"
                ):
                    storage._related_rows(self.db, table, "run", owner_column, 999)
                self.db.execute(f"DELETE FROM {table} WHERE {owner_column}=999")

    def test_multi_owner_overflow_is_bounded_before_parent_reconstruction(self):
        self.save()
        self.db.commit()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executemany(
            "INSERT INTO accepted_work_dependencies VALUES ('run', ?, 0, 1)",
            ((owner,) for owner in (997, 998, 999)),
        )
        with (
            mock.patch.object(storage, "MAX_RELATION_ROWS", 2),
            mock.patch.object(
                storage, "_rows", side_effect=AssertionError("parents read")
            ),
            self.assertRaisesRegex(ReproductionDomainError, "byte-derived row bound"),
        ):
            self.load()
