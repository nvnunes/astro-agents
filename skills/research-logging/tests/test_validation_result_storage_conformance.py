"""Focused transactional contracts for normalized validation ingestion."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.inspection_queries import Query, inspect_result
from research_log_result_store import ResultStoreError, result_snapshot
from validation.batch_projection import build_batch_projection
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from validation.result_storage import (
    ValidationPublicationRequest,
    export_validation_result,
    latest_full_validation,
    load_validation_admission,
    publish_diagnostic_commands,
    publish_validation_result,
)


def _record(
    root: Path, identity: str = "entry:e001:check"
) -> MechanicalGeneratedRecord:
    return MechanicalGeneratedRecord.build(
        (root / "study.md").as_posix(),
        "fixture-rules",
        "2026-09-12",
        (
            MechanicalCheck(
                identity,
                CheckScope.CONFORMANCE,
                CheckStatus.FAIL,
                "entries/e001/data.csv",
                failure=FailurePayload(
                    "fixture.failure", "entries/e001/data.csv", {}, "Fixture"
                ),
            ),
        ),
    )


def _projection(record: MechanicalGeneratedRecord) -> dict[str, object]:
    return build_batch_projection(
        record, invocations=(), registries=(), source_identity="fixture-source"
    )


class ValidationResultStorageConformanceTests(unittest.TestCase):
    def test_admission_is_bound_to_accepted_result_and_indexed_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )

            admission = load_validation_admission(root, stored.result_id)

            self.assertEqual(admission.result_id, stored.result_id)
            self.assertEqual(admission.validation_id, stored.validation_id)
            self.assertEqual(len(admission.groups), 1)
            self.assertEqual(admission.groups[0].kind, "unresolved")
            self.assertEqual(admission.groups[0].entry, "e001")
            self.assertEqual(admission.commands, ())
            self.assertEqual(
                admission.findings[0].group_id, admission.groups[0].identity
            )
            self.assertEqual(admission.findings[0].affected_chains, ())
            self.assertEqual(admission.findings[0].affected_entries, ("e001",))

    def test_admission_rejects_cold_or_replaced_result_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            with self.assertRaisesRegex(ResultStoreError, "result store is absent"):
                load_validation_admission(root, "cold")
            first = publish_validation_result(
                ValidationPublicationRequest(
                    root, _record(root), _projection(_record(root))
                )
            )
            replacement_record = _record(root, "entry:e002:check")
            publish_validation_result(
                ValidationPublicationRequest(
                    root, replacement_record, _projection(replacement_record)
                )
            )
            with self.assertRaisesRegex(
                ResultStoreError, "full validation result is absent"
            ):
                load_validation_admission(root, first.result_id)

    def test_collection_members_are_inserted_under_their_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            projection: dict[str, object] = {
                "schema": "research-log-published-validation/2",
                "record_identity": hashlib.sha256(
                    record.canonical_json().encode()
                ).hexdigest(),
                "summary": record.summary,
                "result_date": record.result_date,
                "rules_version": record.rules_version,
                "source_identity": "fixture-source",
                "chains": [
                    {
                        "chain_id": "chain-1",
                        "entry": "e001",
                        "artifacts": [],
                        "edges": [],
                        "signals": [],
                        "registry": [
                            {
                                "entry": "e001",
                                "name": "source",
                                "kind": "file",
                                "location": "data/source.csv",
                                "path": "entries/e001/data/source.csv",
                                "origin": False,
                                "identity": {"algorithm": "sha256"},
                            }
                        ],
                        "commands": [
                            {
                                "identity": "command-1",
                                "entry": "e001",
                                "document": "entry.md",
                                "fence": 1,
                                "ordinal": 1,
                                "script": "run.py",
                                "tokens": ["python"],
                                "inputs": [],
                                "outputs": [],
                                "collections": [
                                    {
                                        "direction": "input",
                                        "mechanism": "glob",
                                        "root": None,
                                        "target": "data",
                                        "members": ["data/a.csv"],
                                    }
                                ],
                            }
                        ],
                        "findings": [
                            {
                                "identity": "entry:e001:check",
                                "scope": "conformance",
                                "status": "fail",
                                "code": "fixture.failure",
                                "subject": "entries/e001/data.csv",
                                "rule": "Fixture",
                                "observed": {},
                                "admission_effect": "chain",
                                "dependencies": [],
                                "affected_chains": ["chain-1"],
                                "affected_entries": ["e001"],
                            }
                        ],
                    }
                ],
                "unresolved": [],
                "repair_batches": [
                    {
                        "batch_id": "batch-1",
                        "batch_type": "chain",
                        "grouping_reason": "command_chain",
                        "scope": "entries",
                        "entries": ["e001"],
                        "anchors": [],
                        "primary_finding_ids": ["entry:e001:check"],
                        "related_chain_ids": [],
                        "related_batch_ids": [],
                    }
                ],
            }
            projection["validation_id"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT path FROM validation_collection_members"
                    ).fetchone()[0],
                    "data/a.csv",
                )
                registry = db.execute(
                    "SELECT owner_entry, name, kind, location, path, origin, "
                    "identity_json "
                    "FROM validation_group_registry"
                ).fetchone()
                self.assertEqual(
                    tuple(registry[:6]),
                    (
                        "e001",
                        "source",
                        "file",
                        "data/source.csv",
                        "entries/e001/data/source.csv",
                        0,
                    ),
                )
                self.assertEqual(json.loads(registry[6]), {"algorithm": "sha256"})

    def test_full_replacement_cascades_every_child_and_supersedes_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            first = _record(root)
            publish_validation_result(
                ValidationPublicationRequest(root, first, _projection(first))
            )
            publish_validation_result(
                ValidationPublicationRequest(
                    root, first, _projection(first), kind="entry", entry="e001"
                )
            )
            publish_diagnostic_commands(root, first.summary, "fixture", [])

            second = _record(root, "entry:e002:check")
            stored = publish_validation_result(
                ValidationPublicationRequest(root, second, _projection(second))
            )
            with result_snapshot(root) as db:
                self.assertEqual(
                    [
                        row[0]
                        for row in db.execute("SELECT slot FROM validation_results")
                    ],
                    ["full"],
                )
                self.assertEqual(
                    latest_full_validation(root).result_id, stored.result_id
                )
                self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])
                for table in (
                    "validation_checks",
                    "validation_check_dependencies",
                    "validation_groups",
                    "validation_findings",
                    "validation_finding_dependencies",
                    "validation_finding_affected_chains",
                    "validation_finding_affected_entries",
                    "validation_batches",
                    "validation_batch_findings",
                    "validation_batch_command_links",
                ):
                    self.assertGreaterEqual(
                        db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
                    )
                orphan_rows = db.execute(
                    "SELECT count(*) FROM validation_checks WHERE result_id != ?",
                    (stored.result_id,),
                ).fetchone()[0]
                self.assertEqual(orphan_rows, 0)

    def test_full_publication_failure_rolls_back_every_prior_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            first = _record(root)
            full = publish_validation_result(
                ValidationPublicationRequest(root, first, _projection(first))
            )
            entry_one = publish_validation_result(
                ValidationPublicationRequest(
                    root, first, _projection(first), kind="entry", entry="e001"
                )
            )
            entry_two_record = _record(root, "entry:e002:check")
            entry_two = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    entry_two_record,
                    _projection(entry_two_record),
                    kind="entry",
                    entry="e002",
                )
            )
            diagnostic = publish_diagnostic_commands(root, first.summary, "fixture", [])
            replacement = _record(root, "entry:e003:check")
            with mock.patch(
                "validation.result_storage._insert_projection",
                side_effect=RuntimeError("injected publication failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "injected publication failure"
                ):
                    publish_validation_result(
                        ValidationPublicationRequest(
                            root, replacement, _projection(replacement)
                        )
                    )
            self.assertEqual(latest_full_validation(root), full)
            with result_snapshot(root) as db:
                self.assertEqual(
                    {
                        row["result_id"]
                        for row in db.execute(
                            "SELECT result_id FROM validation_results"
                        )
                    },
                    {
                        full.result_id,
                        entry_one.result_id,
                        entry_two.result_id,
                        diagnostic,
                    },
                )

    def test_entry_replacement_preserves_other_slot_ids_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            full_record = _record(root)
            full = publish_validation_result(
                ValidationPublicationRequest(
                    root, full_record, _projection(full_record)
                )
            )
            entry_one = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    full_record,
                    _projection(full_record),
                    kind="entry",
                    entry="e001",
                )
            )
            entry_two_record = _record(root, "entry:e002:check")
            entry_two = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    entry_two_record,
                    _projection(entry_two_record),
                    kind="entry",
                    entry="e002",
                )
            )
            full_export = export_validation_result(root, full.result_id)
            entry_two_export = export_validation_result(root, entry_two.result_id)
            replacement_record = _record(root, "entry:e001:replacement")
            replacement = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    replacement_record,
                    _projection(replacement_record),
                    kind="entry",
                    entry="e001",
                )
            )
            self.assertNotEqual(replacement.result_id, entry_one.result_id)
            self.assertEqual(
                export_validation_result(root, full.result_id), full_export
            )
            self.assertEqual(
                export_validation_result(root, entry_two.result_id), entry_two_export
            )

    def test_reader_rejects_missing_extra_and_forged_existing_batch_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            projection = _projection(record)
            # Use two existing commands/artifacts so corruption can name valid rows.
            finding = projection["unresolved"][0]["findings"][0]
            command = {
                "identity": "c1",
                "entry": "e001",
                "document": "entry.md",
                "fence": 1,
                "ordinal": 1,
                "script": "run.py",
                "tokens": ["run"],
                "inputs": [],
                "outputs": [
                    {"direction": "output", "path": "a.csv", "proof": "declared"}
                ],
                "collections": [],
            }
            alternate = dict(
                command,
                identity="c2",
                ordinal=2,
                outputs=[{"direction": "output", "path": "b.csv", "proof": "declared"}],
            )
            chain = {
                "chain_id": "chain",
                "entry": "e001",
                "findings": [finding],
                "commands": [command, alternate],
                "artifacts": ["a.csv", "b.csv"],
                "edges": [],
                "signals": [],
                "registry": [],
            }
            finding.update(
                admission_effect="chain",
                affected_chains=["chain"],
                affected_entries=["e001"],
            )
            projection.update(chains=[chain], unresolved=[])
            projection["repair_batches"][0].update(
                anchors=[
                    {
                        "kind": "command",
                        "entry": "e001",
                        "document": "entry.md",
                        "fence": 1,
                        "ordinal": 1,
                    }
                ],
                related_chain_ids=["chain"],
            )
            projection["validation_id"] = hashlib.sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in projection.items()
                        if key != "validation_id"
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            database = root / ".cache" / "results.sqlite"
            batch_id = projection["repair_batches"][0]["batch_id"]
            cases = (
                "DELETE FROM validation_batch_command_links WHERE result_id=? "
                "AND command_id='c1'",
                "INSERT INTO validation_batch_command_links VALUES "
                f"(?, '{batch_id}', 'c2', 'fixture.failure')",
            )
            for statement in cases:
                with sqlite3.connect(database) as raw:
                    raw.execute(statement, (stored.result_id,))
                with self.assertRaisesRegex(ResultStoreError, "links are not exact"):
                    export_validation_result(root, stored.result_id)

    def test_malformed_projection_rejects_before_replacement_and_rolls_back(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            original = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            malformed = _projection(record)
            malformed["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "incorrect fields"):
                publish_validation_result(
                    ValidationPublicationRequest(root, record, malformed)
                )
            self.assertEqual(latest_full_validation(root), original)
            with result_snapshot(root) as db:
                self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])

    def test_dangling_child_write_fails_under_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with self.assertRaises(ResultStoreError):
                from research_log_result_store import result_transaction

                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO validation_command_tokens VALUES (?, ?, ?, ?)",
                        ("missing", "missing", 0, "x"),
                    )

    def test_malformed_batch_reference_preserves_the_completed_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            original = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            malformed = _projection(record)
            malformed["repair_batches"][0]["related_chain_ids"] = ["missing"]
            malformed["validation_id"] = hashlib.sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in malformed.items()
                        if key != "validation_id"
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "unknown group"):
                publish_validation_result(
                    ValidationPublicationRequest(root, record, malformed)
                )
            self.assertEqual(latest_full_validation(root), original)

    def test_batch_command_link_target_and_code_are_checked_by_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            projection = _projection(record)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            from research_log_result_store import result_transaction

            with self.assertRaises(ResultStoreError):
                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO validation_batch_command_links "
                        "VALUES (?, ?, ?, ?)",
                        (
                            stored.result_id,
                            projection["repair_batches"][0]["batch_id"],
                            "missing-command",
                            "fixture.failure",
                        ),
                    )
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_batch_command_links "
                        "WHERE command_id='missing-command'"
                    ).fetchone()[0],
                    0,
                )

    def test_incomplete_record_cannot_replace_a_completed_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            original = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            incomplete = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(),
                "fixture-rules",
                "2026-09-12",
                (
                    MechanicalCheck(
                        "entry:e001:unavailable",
                        CheckScope.CONFORMANCE,
                        CheckStatus.UNAVAILABLE,
                        "entries/e001/data.csv",
                        failure=FailurePayload(
                            "fixture.unavailable",
                            "entries/e001/data.csv",
                            {},
                            "Fixture",
                        ),
                    ),
                ),
            )
            with self.assertRaisesRegex(ValueError, "incomplete validation records"):
                publish_validation_result(
                    ValidationPublicationRequest(
                        root, incomplete, kind="entry", entry="e001"
                    )
                )
            self.assertEqual(latest_full_validation(root), original)

    def test_result_metadata_entries_limitations_and_counts_are_exported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    record,
                    _projection(record),
                    result_metadata={
                        "reason": "fixture limitation",
                        "requested_entries": ["e001"],
                        "evaluated_entries": ["e001"],
                        "dependency_entries": ["e002"],
                        "whole_log_limitations": ["fixture.limited"],
                    },
                )
            )
            metadata = export_validation_result(root, stored.result_id)["metadata"]
            self.assertEqual(metadata["reason"], "fixture limitation")
            self.assertEqual(metadata["evaluated_checks"], 1)
            self.assertEqual(metadata["finding_count"], 1)
            self.assertEqual(metadata["source_identity"], "fixture-source")
            self.assertTrue(metadata["started_at"])
            self.assertTrue(metadata["finished_at"])
            self.assertTrue(metadata["stored_at"])
            self.assertEqual(
                metadata["entries"],
                {
                    "requested": ["e001"],
                    "evaluated": ["e001"],
                    "dependency": ["e002"],
                },
            )
            self.assertEqual(metadata["whole_log_limitations"], ["fixture.limited"])

    def test_reader_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            projection = _projection(record)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            from research_log_result_store import result_transaction

            with result_transaction(root) as db:
                db.execute(
                    "UPDATE validation_results SET finding_count=99 WHERE result_id=?",
                    (stored.result_id,),
                )
            with self.assertRaisesRegex(ResultStoreError, "metadata counts disagree"):
                latest_full_validation(root)

    def test_reader_rejects_malformed_affected_scope_and_batch_cardinality(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "INSERT INTO validation_finding_affected_entries "
                    "VALUES (?, ?, 1, 'e002')",
                    (stored.result_id, "entry:e001:check"),
                )
            with self.assertRaisesRegex(ResultStoreError, "affected members"):
                export_validation_result(root, stored.result_id)
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "DELETE FROM validation_finding_affected_entries "
                    "WHERE result_id=? AND finding_id=? AND position=1",
                    (stored.result_id, "entry:e001:check"),
                )
                raw.execute(
                    "UPDATE validation_batches SET primary_finding_count=2 "
                    "WHERE result_id=?",
                    (stored.result_id,),
                )
            with self.assertRaisesRegex(ResultStoreError, "primary findings"):
                export_validation_result(root, stored.result_id)

    def test_reader_rejects_invalid_stored_entry_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(
                    root, record, _projection(record), kind="entry", entry="e001"
                )
            )
            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "UPDATE validation_results SET entry='not-an-entry' "
                    "WHERE result_id=?",
                    (stored.result_id,),
                )
            with self.assertRaisesRegex(ResultStoreError, "invalid stored .*entry"):
                export_validation_result(root, stored.result_id)

    def test_reader_rejects_invalid_stored_check_scope_and_status(self) -> None:
        for column, value, message in (
            ("scope", "not-a-scope", "invalid stored check scope"),
            ("status", "not-a-status", "invalid stored check status"),
        ):
            with (
                self.subTest(column=column),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                record = _record(root)
                stored = publish_validation_result(
                    ValidationPublicationRequest(root, record, _projection(record))
                )
                database = root / ".cache" / "results.sqlite"
                with sqlite3.connect(database) as raw:
                    raw.execute(
                        f"UPDATE validation_checks SET {column}=? WHERE result_id=?",
                        (value, stored.result_id),
                    )
                with self.assertRaisesRegex(ResultStoreError, message):
                    export_validation_result(root, stored.result_id)

    def test_reader_rejects_inconsistent_stored_check_failure_status(self) -> None:
        for status, clear_payload, message in (
            ("pass", False, "stored successful check has failure payload"),
            ("fail", True, "stored failing check has no failure payload"),
        ):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                record = _record(root)
                stored = publish_validation_result(
                    ValidationPublicationRequest(root, record, _projection(record))
                )
                database = root / ".cache" / "results.sqlite"
                with sqlite3.connect(database) as raw:
                    if clear_payload:
                        raw.execute(
                            "UPDATE validation_checks SET status=?, failure_code=NULL, "
                            "rule=NULL, observed_json=NULL, failure_dependency=NULL "
                            "WHERE result_id=?",
                            (status, stored.result_id),
                        )
                    else:
                        raw.execute(
                            "UPDATE validation_checks SET status=? WHERE result_id=?",
                            (status, stored.result_id),
                        )
                with self.assertRaisesRegex(ResultStoreError, message):
                    export_validation_result(root, stored.result_id)

    def test_reader_rejects_invalid_stored_finding_scope_and_status(self) -> None:
        for column, value, message in (
            ("scope", "not-a-scope", "invalid stored finding scope"),
            ("status", "pass", "invalid stored finding status"),
        ):
            with (
                self.subTest(column=column),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                record = _record(root)
                stored = publish_validation_result(
                    ValidationPublicationRequest(root, record, _projection(record))
                )
                database = root / ".cache" / "results.sqlite"
                with sqlite3.connect(database) as raw:
                    raw.execute(
                        f"UPDATE validation_findings SET {column}=? WHERE result_id=?",
                        (value, stored.result_id),
                    )
                with self.assertRaisesRegex(ResultStoreError, message):
                    export_validation_result(root, stored.result_id)

    def test_reader_rejects_finding_subject_that_differs_from_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "UPDATE validation_findings SET projection_subject='wrong-subject' "
                    "WHERE result_id=?",
                    (stored.result_id,),
                )
            with self.assertRaisesRegex(
                ResultStoreError, "stored finding does not match its failed check"
            ):
                export_validation_result(root, stored.result_id)

    def test_over_limit_candidate_rolls_back_before_slot_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            original = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with mock.patch("validation.result_storage.MAX_VALIDATION_RESULT_ROWS", 0):
                with self.assertRaisesRegex(ValueError, "row bound"):
                    publish_validation_result(
                        ValidationPublicationRequest(root, record, _projection(record))
                    )
            self.assertEqual(latest_full_validation(root), original)

    def test_high_fanout_batches_store_direct_command_links_only(self) -> None:
        """Artifact fan-out remains derived while the public selector remains exact."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            finding_count = 40
            checks = tuple(
                MechanicalCheck(
                    f"entry:e001:check:{number}",
                    CheckScope.CONFORMANCE,
                    CheckStatus.FAIL,
                    f"entries/e001/{number}.csv",
                    failure=FailurePayload(
                        f"fixture.{('one', 'two')[number % 2]}",
                        f"entries/e001/{number}.csv",
                        (
                            {"rejected_commands": [{"identity": "rejected"}]}
                            if number == 0
                            else {}
                        ),
                        "Fixture",
                    ),
                )
                for number in range(finding_count)
            )
            record = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(), "fixture-rules", "2026-09-12", checks
            )
            findings = [
                {
                    "identity": check.identity,
                    "scope": check.scope.value,
                    "status": check.status.value,
                    "code": f"fixture.{('one', 'two')[number % 2]}",
                    "subject": check.subject,
                    "rule": "Fixture",
                    "observed": (
                        {"rejected_commands": [{"identity": "rejected"}]}
                        if number == 0
                        else {}
                    ),
                    "admission_effect": "chain",
                    "dependencies": [],
                    "affected_chains": ["chain"],
                    "affected_entries": ["e001"],
                }
                for number, check in enumerate(checks)
            ]
            projection: dict[str, object] = {
                "schema": "research-log-published-validation/2",
                "record_identity": hashlib.sha256(
                    record.canonical_json().encode()
                ).hexdigest(),
                "summary": record.summary,
                "result_date": record.result_date,
                "rules_version": record.rules_version,
                "source_identity": "fixture-source",
                "chains": [
                    {
                        "chain_id": "chain",
                        "entry": "e001",
                        "findings": findings,
                        "commands": [
                            {
                                "identity": "rejected",
                                "entry": "e001",
                                "document": "entry.md",
                                "fence": 1,
                                "ordinal": 1,
                                "script": "run.py",
                                "tokens": [],
                                "inputs": [
                                    {
                                        "direction": "input",
                                        "path": "isolated-input.csv",
                                        "proof": "declared",
                                    }
                                ],
                                "outputs": [
                                    {
                                        "direction": "output",
                                        "path": "isolated-output.csv",
                                        "proof": "declared",
                                    }
                                ],
                                "collections": [],
                            },
                            {
                                "identity": "anchor",
                                "entry": "e001",
                                "document": "entry.md",
                                "fence": 1,
                                "ordinal": 2,
                                "script": "run.py",
                                "tokens": [],
                                "inputs": [
                                    {
                                        "direction": "input",
                                        "path": f"input-{number}.csv",
                                        "proof": "declared",
                                    }
                                    for number in range(200)
                                ],
                                "outputs": [
                                    {
                                        "direction": "output",
                                        "path": f"output-{number}.csv",
                                        "proof": "declared",
                                    }
                                    for number in range(200)
                                ],
                                "collections": [],
                            }
                        ],
                        "artifacts": [
                            "isolated-input.csv",
                            "isolated-output.csv",
                            *[f"input-{number}.csv" for number in range(200)],
                            *[f"output-{number}.csv" for number in range(200)],
                        ],
                        "edges": [],
                        "signals": [],
                        "registry": [],
                    }
                ],
                "unresolved": [],
                "repair_batches": [
                    {
                        "batch_id": "batch",
                        "batch_type": "chain",
                        "grouping_reason": "command_chain",
                        "scope": "entries",
                        "entries": ["e001"],
                        "anchors": [
                            {
                                "kind": "command",
                                "entry": "e001",
                                "document": "entry.md",
                                "fence": 1,
                                "ordinal": 2,
                            }
                        ],
                        "primary_finding_ids": [check.identity for check in checks],
                        "related_chain_ids": ["chain"],
                        "related_batch_ids": [],
                    }
                ],
            }
            projection["validation_id"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            with result_snapshot(root) as db:
                links = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT command_id,code FROM validation_batch_command_links "
                        "WHERE result_id=? ORDER BY command_id,code",
                        (stored.result_id,),
                    )
                }
                tables = [
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name LIKE 'validation_%'"
                    )
                ]
                actual_rows = sum(
                    db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in tables
                )
            self.assertNotIn("validation_batch_entity_links", tables)
            self.assertEqual(
                links,
                {
                    ("anchor", "fixture.one"),
                    ("anchor", "fixture.two"),
                    ("rejected", "fixture.one"),
                },
            )
            self.assertLess(actual_rows, 100_000)
            first = inspect_result(
                root,
                Query(
                    result_id=stored.result_id,
                    view="artifacts",
                    batch="batch",
                    code="fixture.one",
                    limit=100,
                ),
            )
            pages = [first]
            while pages[-1]["next_cursor"]:
                pages.append(
                    inspect_result(
                        root,
                        Query(
                            result_id=stored.result_id,
                            view="artifacts",
                            batch="batch",
                            code="fixture.one",
                            limit=100,
                            cursor=pages[-1]["next_cursor"],
                        ),
                    )
                )
            expected_artifacts = {
                "isolated-input.csv",
                "isolated-output.csv",
                *[f"input-{number}.csv" for number in range(200)],
                *[f"output-{number}.csv" for number in range(200)],
            }
            self.assertEqual(first["total"], len(expected_artifacts))
            self.assertEqual(
                {
                    item["artifact_id"]
                    for page in pages
                    for item in page["items"]
                },
                expected_artifacts,
            )
            two_commands = inspect_result(
                root,
                Query(
                    result_id=stored.result_id,
                    view="commands",
                    batch="batch",
                    code="fixture.two",
                    limit=100,
                ),
            )
            self.assertEqual(
                [item["command_id"] for item in two_commands["items"]], ["anchor"]
            )

            bounded_root = Path(directory) / "bounded-study"
            bounded_root.mkdir()
            with mock.patch(
                "validation.result_storage.MAX_VALIDATION_RESULT_ROWS", actual_rows
            ):
                publish_validation_result(
                    ValidationPublicationRequest(bounded_root, record, projection)
                )
            with mock.patch(
                "validation.result_storage.MAX_VALIDATION_RESULT_ROWS", actual_rows - 1
            ):
                with self.assertRaisesRegex(ValueError, "row bound"):
                    publish_validation_result(
                        ValidationPublicationRequest(
                            bounded_root, record, projection
                        )
                    )

    def test_export_postassembly_bound_has_the_public_too_large_code(self) -> None:
        """The 64 MiB export limit applies after the stored result is assembled."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with mock.patch("validation.result_storage.MAX_VALIDATION_RESULT_BYTES", 1):
                with self.assertRaises(ResultStoreError) as raised:
                    export_validation_result(root, stored.result_id)
            self.assertEqual(raised.exception.code, "results.export.too_large")

    def test_entry_projection_is_retained_and_exportable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            projection = _projection(record)
            stored = publish_validation_result(
                ValidationPublicationRequest(
                    root, record, projection, kind="entry", entry="e001"
                )
            )
            exported = export_validation_result(root, stored.result_id)
            self.assertEqual(
                set(exported["chains"]), {projection["unresolved"][0]["chain_id"]}
            )
            self.assertEqual(set(exported["findings"]), {"entry:e001:check"})
            self.assertEqual(exported["metadata"]["entries"]["requested"], ["e001"])
            self.assertEqual(exported["metadata"]["entries"]["evaluated"], ["e001"])

    def test_diagnostic_export_has_no_evaluation_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            result_id = publish_diagnostic_commands(root, "study.md", "fixture", [])
            exported = export_validation_result(root, result_id)
            self.assertEqual(exported["checks"], {})
            self.assertEqual(exported["findings"], {})
            self.assertEqual(exported["metadata"]["evaluated_checks"], 0)
            self.assertEqual(exported["metadata"]["finding_count"], 0)
            self.assertEqual(exported["metadata"]["reason"], "fixture")

    def test_source_identity_mismatch_preserves_prior_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            original = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with self.assertRaisesRegex(ValueError, "source identity"):
                publish_validation_result(
                    ValidationPublicationRequest(
                        root,
                        record,
                        _projection(record),
                        source_identity="wrong-source",
                    )
                )
            self.assertEqual(latest_full_validation(root), original)

    def test_batch_links_only_anchor_matched_command_and_its_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            finding = _projection(record)["unresolved"][0]["findings"][0]

            def command(
                identity: str, artifact: str, ordinal: int
            ) -> dict[str, object]:
                return {
                    "identity": identity,
                    "entry": "e001",
                    "document": "entry.md",
                    "fence": 1,
                    "ordinal": ordinal,
                    "script": "run.py",
                    "tokens": ["run"],
                    "inputs": [],
                    "outputs": [
                        {"direction": "output", "path": artifact, "proof": "declared"}
                    ],
                    "collections": [],
                }

            projection = {
                "schema": "research-log-published-validation/2",
                "record_identity": hashlib.sha256(
                    record.canonical_json().encode()
                ).hexdigest(),
                "summary": record.summary,
                "result_date": record.result_date,
                "rules_version": record.rules_version,
                "source_identity": "fixture-source",
                "chains": [
                    {
                        "chain_id": "chain",
                        "entry": "e001",
                        "findings": [finding],
                        "commands": [
                            command("c1", "a.csv", 1),
                            command("c2", "b.csv", 2),
                        ],
                        "artifacts": ["a.csv", "b.csv"],
                        "edges": [],
                        "signals": [],
                        "registry": [],
                    }
                ],
                "unresolved": [],
                "repair_batches": [
                    {
                        "batch_id": "batch",
                        "batch_type": "chain",
                        "grouping_reason": "command_chain",
                        "scope": "entries",
                        "entries": ["e001"],
                        "anchors": [
                            {
                                "kind": "command",
                                "entry": "e001",
                                "document": "entry.md",
                                "fence": 1,
                                "ordinal": 1,
                            }
                        ],
                        "primary_finding_ids": ["entry:e001:check"],
                        "related_chain_ids": ["chain"],
                        "related_batch_ids": [],
                    }
                ],
            }
            finding["admission_effect"] = "chain"
            finding["affected_chains"] = ["chain"]
            finding["affected_entries"] = ["e001"]
            projection["validation_id"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            with result_snapshot(root) as db:
                links = {
                    (row[0], row[1])
                    for row in db.execute(
                        "SELECT command_id, code "
                        "FROM validation_batch_command_links "
                        "WHERE result_id=? ORDER BY command_id, code",
                        (stored.result_id,),
                    )
                }
            self.assertIn(("c1", "fixture.failure"), links)
            self.assertNotIn(("c2", "fixture.failure"), links)
