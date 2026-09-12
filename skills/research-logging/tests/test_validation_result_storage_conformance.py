"""Focused transactional contracts for normalized validation ingestion."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Callable, TypeVar
from unittest import mock

import research_log_result_store as result_store
from log_commands.inspection_queries import InspectionError, Query, inspect_result
from research_log_result_store import (
    ResultStoreError,
    record_report_materialization,
    result_snapshot,
)
from validation.batch_projection import build_batch_projection
from validation.human_projection import ReportContext
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from validation.result_storage import (
    ValidationPublicationRequest,
    audit_validation_result,
    export_validation_result,
    latest_full_validation,
    load_finding_group,
    load_validation_admission,
    load_validation_report_projection,
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


_T = TypeVar("_T")


def _traced_read_tables(
    call: Callable[[], _T],
) -> tuple[_T, set[str], list[str]]:
    statements: list[str] = []
    original_open = result_store._open

    def traced_open(path: Path, *, writable: bool) -> sqlite3.Connection:
        db = original_open(path, writable=writable)
        if not writable:
            db.set_trace_callback(statements.append)
        return db

    with mock.patch("research_log_result_store._open", side_effect=traced_open):
        value = call()
    tables = {
        match.group(1)
        for statement in statements
        for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+([a-z][a-z0-9_]*)", statement, re.IGNORECASE
        )
    }
    return value, tables, statements


def _registry_projection(record: MechanicalGeneratedRecord) -> dict[str, object]:
    projection = _projection(record)
    unresolved = projection["unresolved"]
    batches = projection["repair_batches"]
    assert isinstance(unresolved, list) and len(unresolved) == 1
    assert isinstance(batches, list) and len(batches) == 1
    group = unresolved[0]
    batch = batches[0]
    assert isinstance(group, dict) and isinstance(batch, dict)
    findings = group["findings"]
    assert isinstance(findings, list) and len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, dict)
    finding["admission_effect"] = "chain"
    finding["affected_chains"] = ["chain"]
    finding["affected_entries"] = ["e001"]
    projection["chains"] = [
        {
            "chain_id": "chain",
            "entry": "e001",
            "findings": findings,
            "commands": [],
            "artifacts": [],
            "edges": [],
            "signals": [],
            "registry": [
                {
                    "entry": "e001",
                    "name": "files",
                    "kind": "directory",
                    "location": "data/files",
                    "path": "entries/e001/data/files",
                    "origin": False,
                    "identity": {
                        "algorithm": "identity-files-sha256-v1",
                        "files": ["z.csv", "a.csv"],
                    },
                }
            ],
        }
    ]
    projection["unresolved"] = []
    batch["batch_type"] = "chain"
    batch["grouping_reason"] = "command_chain"
    batch["related_chain_ids"] = ["chain"]
    projection.pop("validation_id")
    projection["validation_id"] = hashlib.sha256(
        json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return projection


class ValidationResultStorageConformanceTests(unittest.TestCase):
    def test_report_projection_does_not_audit_unconsumed_batch_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            with mock.patch(
                "validation.human_projection.project_findings", return_value=()
            ):
                stored = publish_validation_result(
                    ValidationPublicationRequest(
                        root,
                        record,
                        _projection(record),
                        report_context=ReportContext.empty(root / "study.md"),
                    )
                )
            with sqlite3.connect(root / ".cache" / "results.sqlite") as raw:
                raw.execute(
                    "UPDATE validation_batches SET primary_finding_count=2 "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?)",
                    (stored.result_id,),
                )
            with (
                mock.patch(
                    "validation.result_storage._audit_validation_result",
                    side_effect=AssertionError("report performed whole audit"),
                ),
                mock.patch(
                    "validation.human_projection.project_findings", return_value=()
                ),
            ):
                projection, tables, _ = _traced_read_tables(
                    lambda: load_validation_report_projection(root)
                )
            self.assertEqual(projection.stored.result_id, stored.result_id)
            self.assertLessEqual(
                tables,
                {
                    "validation_results",
                    "validation_checks",
                    "validation_codes",
                    "validation_check_dependencies",
                    "validation_findings",
                },
            )

    def test_admission_is_bound_to_accepted_result_and_indexed_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )

            admission, tables, _ = _traced_read_tables(
                lambda: load_validation_admission(root, stored.result_id)
            )

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
            self.assertLessEqual(
                tables,
                {
                    "validation_results",
                    "validation_groups",
                    "validation_findings",
                    "validation_checks",
                    "validation_finding_affected_chains",
                    "validation_finding_affected_entries",
                    "validation_commands",
                    "validation_command_relationships",
                    "validation_artifacts",
                    "validation_command_collections",
                    "validation_collection_members",
                },
            )
            self.assertTrue(
                {
                    "validation_group_registry",
                    "validation_registry_records",
                    "validation_group_edges",
                    "validation_group_signals",
                    "validation_batches",
                    "validation_batch_anchors",
                    "validation_batch_command_links",
                }.isdisjoint(tables)
            )

    def test_admission_rejects_invalid_consumed_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with sqlite3.connect(root / ".cache" / "results.sqlite") as raw:
                raw.execute(
                    "UPDATE validation_findings SET admission_effect='invalid' "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?)",
                    (stored.result_id,),
                )

            with self.assertRaisesRegex(ResultStoreError, "invalid admission effect"):
                load_validation_admission(root, stored.result_id)

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
                    "SELECT r.owner_entry,r.name,r.kind,r.location,r.path,r.origin,"
                    "r.identity_algorithm,r.identity_commit "
                    "FROM validation_group_registry AS m "
                    "JOIN validation_registry_records AS r "
                    "USING (result_pk,registry_pk)"
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
                self.assertEqual(tuple(registry[6:]), ("sha256", None))
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_registry_records"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_registry_identity_members"
                    ).fetchone()[0],
                    0,
                )
                check = db.execute(
                    "SELECT c.check_id,k.code,f.admission_effect "
                    "FROM validation_findings AS f "
                    "JOIN validation_checks AS c USING (result_pk,check_pk) "
                    "JOIN validation_codes AS k USING (result_pk,code_pk)"
                ).fetchone()
                self.assertEqual(
                    tuple(check),
                    ("entry:e001:check", "fixture.failure", "chain"),
                )

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
                    "validation_codes",
                    "validation_groups",
                    "validation_findings",
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
                    "SELECT count(*) FROM validation_checks AS c "
                    "LEFT JOIN validation_results AS r USING (result_pk) "
                    "WHERE r.result_id IS NULL OR r.result_id != ?",
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
            with sqlite3.connect(database) as raw:
                result_pk = raw.execute(
                    "SELECT result_pk FROM validation_results WHERE result_id=?",
                    (stored.result_id,),
                ).fetchone()[0]
                batch_pk = raw.execute(
                    "SELECT batch_pk FROM validation_batches "
                    "WHERE result_pk=? AND batch_id=?",
                    (result_pk, batch_id),
                ).fetchone()[0]
                code_pk = raw.execute(
                    "SELECT code_pk FROM validation_codes "
                    "WHERE result_pk=? AND code='fixture.failure'",
                    (result_pk,),
                ).fetchone()[0]
                command_pks = dict(
                    raw.execute(
                        "SELECT command_id,command_pk FROM validation_commands "
                        "WHERE result_pk=?",
                        (result_pk,),
                    )
                )
                raw.execute(
                    "DELETE FROM validation_batch_command_links "
                    "WHERE result_pk=? AND batch_pk=? AND command_pk=? AND code_pk=?",
                    (result_pk, batch_pk, command_pks["c1"], code_pk),
                )
            with self.assertRaisesRegex(ResultStoreError, "links are not exact"):
                audit_validation_result(root, stored.result_id)
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "INSERT INTO validation_batch_command_links VALUES (?, ?, ?, ?)",
                    (result_pk, batch_pk, command_pks["c1"], code_pk),
                )
                raw.execute(
                    "INSERT INTO validation_batch_command_links VALUES (?, ?, ?, ?)",
                    (result_pk, batch_pk, command_pks["c2"], code_pk),
                )
            with self.assertRaisesRegex(ResultStoreError, "links are not exact"):
                audit_validation_result(root, stored.result_id)

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

            with result_snapshot(root) as db:
                result_pk = db.execute(
                    "SELECT result_pk FROM validation_results WHERE result_id=?",
                    (stored.result_id,),
                ).fetchone()[0]
                batch_pk = db.execute(
                    "SELECT batch_pk FROM validation_batches WHERE result_pk=?",
                    (result_pk,),
                ).fetchone()[0]
                code_pk = db.execute(
                    "SELECT code_pk FROM validation_codes WHERE result_pk=?",
                    (result_pk,),
                ).fetchone()[0]
            missing_command_pk = 999_999
            with self.assertRaises(ResultStoreError):
                with result_transaction(root) as db:
                    db.execute(
                        "INSERT INTO validation_batch_command_links "
                        "VALUES (?, ?, ?, ?)",
                        (result_pk, batch_pk, missing_command_pk, code_pk),
                    )
            with result_snapshot(root) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_batch_command_links "
                        "WHERE result_pk=? AND command_pk=?",
                        (result_pk, missing_command_pk),
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

    def test_exported_code_members_preserve_group_projection_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            checks = tuple(
                MechanicalCheck(
                    identity,
                    CheckScope.CONFORMANCE,
                    CheckStatus.FAIL,
                    f"entries/{entry}/data.csv",
                    failure=FailurePayload(
                        "fixture.failure",
                        f"entries/{entry}/data.csv",
                        {},
                        "Fixture",
                    ),
                )
                for identity, entry in (
                    ("entry:e001:z-check", "e001"),
                    ("entry:e002:a-check", "e002"),
                )
            )
            record = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(),
                "fixture-rules",
                "2026-09-12",
                checks,
            )
            projection = _projection(record)
            projection["unresolved"].reverse()
            expected = [
                group["findings"][0]["identity"]
                for group in projection["unresolved"]
            ]
            projection.pop("validation_id")
            projection["validation_id"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )

            exported = export_validation_result(root, stored.result_id)

            self.assertEqual(
                exported["codes"]["fixture.failure"], expected
            )

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
            self.assertEqual(latest_full_validation(root), stored)
            with self.assertRaisesRegex(ResultStoreError, "metadata counts disagree"):
                audit_validation_result(root, stored.result_id)

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
                result_pk, check_pk = raw.execute(
                    "SELECT r.result_pk,c.check_pk FROM validation_results AS r "
                    "JOIN validation_checks AS c USING (result_pk) "
                    "WHERE r.result_id=? AND c.check_id=?",
                    (stored.result_id, "entry:e001:check"),
                ).fetchone()
                raw.execute(
                    "INSERT INTO validation_finding_affected_entries "
                    "VALUES (?, ?, 1, 'e002')",
                    (result_pk, check_pk),
                )
            self.assertEqual(latest_full_validation(root), stored)
            with self.assertRaisesRegex(ResultStoreError, "affected members"):
                audit_validation_result(root, stored.result_id)
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "DELETE FROM validation_finding_affected_entries "
                    "WHERE result_pk=? AND check_pk=? AND position=1",
                    (result_pk, check_pk),
                )
                raw.execute(
                    "UPDATE validation_batches SET primary_finding_count=2 "
                    "WHERE result_pk=?",
                    (result_pk,),
                )
            with self.assertRaisesRegex(ResultStoreError, "primary findings"):
                audit_validation_result(root, stored.result_id)

    def test_selected_batch_validates_only_its_unique_local_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            checks = tuple(
                MechanicalCheck(
                    f"entry:{entry}:check",
                    CheckScope.CONFORMANCE,
                    CheckStatus.FAIL,
                    f"entries/{entry}/data.csv",
                    failure=FailurePayload(
                        "fixture.failure",
                        f"entries/{entry}/data.csv",
                        {},
                        "Fixture",
                    ),
                )
                for entry in ("e001", "e002")
            )
            record = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(),
                "fixture-rules",
                "2026-09-12",
                checks,
            )
            projection = _projection(record)
            chains = []
            for unresolved, batch in zip(
                projection["unresolved"], projection["repair_batches"], strict=True
            ):
                finding = unresolved["findings"][0]
                finding["admission_effect"] = "chain"
                finding["affected_chains"] = [unresolved["chain_id"]]
                chains.append(
                    {
                        "chain_id": unresolved["chain_id"],
                        "entry": unresolved["entry"],
                        "findings": unresolved["findings"],
                        "commands": [],
                        "artifacts": [],
                        "edges": [],
                        "signals": [],
                        "registry": [],
                    }
                )
                batch["batch_type"] = "chain"
                batch["related_chain_ids"] = [unresolved["chain_id"]]
            projection["chains"] = chains
            projection["unresolved"] = []
            projection.pop("validation_id")
            projection["validation_id"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                batches = list(
                    raw.execute(
                        "SELECT result_pk,batch_pk,batch_id FROM validation_batches "
                        "ORDER BY batch_pk"
                    )
                )
            self.assertEqual(len(batches), 2)
            result_pk, selected_pk, selected_id = batches[0]
            _, _, unrelated_id = batches[1]

            def read(batch_id: str) -> dict[str, object]:
                return inspect_result(
                    root,
                    Query(
                        action="batch",
                        entity=batch_id,
                        result_id=stored.result_id,
                    ),
                )

            for table, column in (
                ("validation_batch_entries", "entry"),
                ("validation_batch_findings", "check_pk"),
                ("validation_batch_groups", "group_pk"),
            ):
                with self.subTest(table=table):
                    with sqlite3.connect(database) as raw:
                        raw.execute(
                            f"INSERT INTO {table} "
                            f"(result_pk,batch_pk,position,{column}) "
                            f"SELECT result_pk,batch_pk,1,{column} FROM {table} "
                            "WHERE result_pk=? AND batch_pk=? AND position=0",
                            (result_pk, selected_pk),
                        )
                    self.assertEqual(read(unrelated_id)["returned"], 1)
                    with self.assertRaisesRegex(InspectionError, "duplicate"):
                        read(selected_id)
                    with sqlite3.connect(database) as raw:
                        raw.execute(
                            f"DELETE FROM {table} WHERE result_pk=? "
                            "AND batch_pk=? AND position=1",
                            (result_pk, selected_pk),
                        )

            for table, column in (
                ("validation_batch_findings", "check_pk"),
                ("validation_batch_groups", "group_pk"),
            ):
                with self.subTest(missing_parent=table):
                    with sqlite3.connect(database) as raw:
                        original = raw.execute(
                            f"SELECT {column} FROM {table} WHERE result_pk=? "
                            "AND batch_pk=? AND position=0",
                            (result_pk, selected_pk),
                        ).fetchone()[0]
                        raw.execute(
                            f"UPDATE {table} SET {column}=999999 WHERE result_pk=? "
                            "AND batch_pk=? AND position=0",
                            (result_pk, selected_pk),
                        )
                    self.assertEqual(read(unrelated_id)["returned"], 1)
                    with self.assertRaisesRegex(InspectionError, "member is absent"):
                        read(selected_id)
                    with sqlite3.connect(database) as raw:
                        raw.execute(
                            f"UPDATE {table} SET {column}=? WHERE result_pk=? "
                            "AND batch_pk=? AND position=0",
                            (original, result_pk, selected_pk),
                        )

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
        for column, value in (
            ("scope", "not-a-scope"),
            ("status", "not-a-status"),
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
                    raw.execute("PRAGMA ignore_check_constraints=ON")
                    raw.execute(
                        f"UPDATE validation_checks SET {column}=? WHERE result_pk="
                        "(SELECT result_pk FROM validation_results WHERE result_id=?)",
                        (value, stored.result_id),
                    )
                with self.assertRaisesRegex(ResultStoreError, "scope or status"):
                    audit_validation_result(root, stored.result_id)

    def test_reader_rejects_inconsistent_stored_check_failure_status(self) -> None:
        for status, clear_payload, message in (
            ("pass", False, "stored successful check has failure payload"),
            ("fail", True, "stored failing check has no failure payload"),
            ("pass", True, "stored finding has no failed check"),
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
                    raw.execute("PRAGMA ignore_check_constraints=ON")
                    if clear_payload:
                        raw.execute(
                            "UPDATE validation_checks SET status=?, code_pk=NULL, "
                            "rule=NULL, observed_json=NULL, failure_dependency=NULL "
                            "WHERE result_pk=(SELECT result_pk FROM validation_results "
                            "WHERE result_id=?)",
                            (status, stored.result_id),
                        )
                    else:
                        raw.execute(
                            "UPDATE validation_checks SET status=? WHERE result_pk="
                            "(SELECT result_pk FROM validation_results "
                            "WHERE result_id=?)",
                            (status, stored.result_id),
                        )
                with self.assertRaisesRegex(ResultStoreError, message):
                    audit_validation_result(root, stored.result_id)

    def test_nonfailing_check_attached_to_finding_is_locally_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            checks = tuple(
                MechanicalCheck(
                    f"entry:{entry}:check",
                    CheckScope.CONFORMANCE,
                    CheckStatus.FAIL,
                    f"entries/{entry}/data.csv",
                    failure=FailurePayload(
                        "fixture.failure",
                        f"entries/{entry}/data.csv",
                        {},
                        "Fixture",
                    ),
                )
                for entry in ("e001", "e002")
            )
            record = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(),
                "fixture-rules",
                "2026-09-12",
                checks,
            )
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with sqlite3.connect(root / ".cache" / "results.sqlite") as raw:
                raw.execute("PRAGMA ignore_check_constraints=ON")
                raw.execute(
                    "UPDATE validation_checks SET status='pass',code_pk=NULL,"
                    "rule=NULL,observed_json=NULL,failure_dependency=NULL "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?) AND check_id='entry:e002:check'",
                    (stored.result_id,),
                )

            self.assertEqual(
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check",
                        result_id=stored.result_id,
                    ),
                )["returned"],
                1,
            )
            with self.assertRaisesRegex(InspectionError, "no failed check"):
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e002:check",
                        result_id=stored.result_id,
                    ),
                )
            with self.assertRaises(ResultStoreError):
                audit_validation_result(root, stored.result_id)
            with self.assertRaisesRegex(ResultStoreError, "no failed check"):
                load_validation_admission(root, stored.result_id)

    def test_finding_rows_do_not_duplicate_check_machine_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            with result_snapshot(root) as db:
                columns = {
                    row[1]
                    for row in db.execute("PRAGMA table_info(validation_findings)")
                }
            self.assertTrue(
                {"scope", "status", "code", "rule", "observed_json"}.isdisjoint(columns)
            )

    def test_finding_projection_uses_check_subject_as_sole_authority(self) -> None:
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
                    "UPDATE validation_checks SET subject='wrong-subject' "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?)",
                    (stored.result_id,),
                )
            exported = export_validation_result(root, stored.result_id)
            self.assertEqual(
                exported["findings"]["entry:e001:check"]["subject"],
                "wrong-subject",
            )

    def test_registry_corruption_is_local_to_readers_that_hydrate_it(self) -> None:
        for corruption, message in (
            ("hash", "payload hash"),
            ("member-order", "positions"),
        ):
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                record = _record(root)
                stored = publish_validation_result(
                    ValidationPublicationRequest(
                        root, record, _registry_projection(record)
                    )
                )
                database = root / ".cache" / "results.sqlite"
                with sqlite3.connect(database) as raw:
                    if corruption == "hash":
                        raw.execute(
                            "UPDATE validation_registry_records "
                            "SET payload_sha256=? WHERE result_pk=(SELECT result_pk "
                            "FROM validation_results WHERE result_id=?)",
                            ("0" * 64, stored.result_id),
                        )
                    else:
                        raw.execute(
                            "UPDATE validation_registry_identity_members "
                            "SET position=2 WHERE result_pk=(SELECT result_pk "
                            "FROM validation_results WHERE result_id=?) "
                            "AND position=0",
                            (stored.result_id,),
                        )
                self.assertEqual(
                    inspect_result(
                        root,
                        Query(
                            result_id=stored.result_id,
                            view="summary",
                            limit=1,
                        ),
                    )["result_id"],
                    stored.result_id,
                )
                with self.assertRaisesRegex(ResultStoreError, message):
                    audit_validation_result(root, stored.result_id)
                with self.assertRaisesRegex(ResultStoreError, message):
                    load_finding_group(root, entry="e001", group_id="chain")

    def test_admission_rejects_cross_result_compact_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            full = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            entry_record = _record(root, "entry:e002:check")
            entry = publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    entry_record,
                    _projection(entry_record),
                    kind="entry",
                    entry="e002",
                )
            )
            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                entry_pk = raw.execute(
                    "SELECT result_pk FROM validation_results WHERE result_id=?",
                    (entry.result_id,),
                ).fetchone()[0]
                raw.execute(
                    "INSERT INTO validation_groups VALUES "
                    "(?,2,'foreign-group','unresolved','e002',"
                    "'finding_scope_unresolved',1)",
                    (entry_pk,),
                )
                full_pk, check_pk = raw.execute(
                    "SELECT f.result_pk,f.check_pk FROM validation_findings AS f "
                    "JOIN validation_results AS r USING (result_pk) "
                    "WHERE r.result_id=?",
                    (full.result_id,),
                ).fetchone()
                raw.execute(
                    "UPDATE validation_findings SET admission_effect='chain' "
                    "WHERE result_pk=? AND check_pk=?",
                    (full_pk, check_pk),
                )
                raw.execute(
                    "INSERT INTO validation_finding_affected_chains "
                    "VALUES (?,?,0,2)",
                    (full_pk, check_pk),
                )

            self.assertEqual(
                inspect_result(
                    root,
                    Query(result_id=full.result_id, view="summary", limit=1),
                )["result_id"],
                full.result_id,
            )
            with self.assertRaisesRegex(ResultStoreError, "foreign group"):
                load_validation_admission(root, full.result_id)
            with self.assertRaisesRegex(ResultStoreError, "invalid affected members"):
                audit_validation_result(root, full.result_id)

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
            passing = MechanicalCheck(
                "entry:e001:pass",
                CheckScope.CONFORMANCE,
                CheckStatus.PASS,
                "entries/e001/pass.csv",
            )
            record = MechanicalGeneratedRecord.build(
                (root / "study.md").as_posix(),
                "fixture-rules",
                "2026-09-12",
                (passing, *checks),
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
            registries = [
                {
                    "entry": "e001",
                    "name": "files",
                    "kind": "directory",
                    "location": "data/files",
                    "path": "entries/e001/data/files",
                    "origin": False,
                    "identity": {
                        "algorithm": "identity-files-sha256-v1",
                        "files": ["z.csv", "a.csv"],
                    },
                },
                {
                    "entry": "e001",
                    "name": "patterns",
                    "kind": "directory",
                    "location": "data/patterns",
                    "path": "entries/e001/data/patterns",
                    "origin": False,
                    "identity": {
                        "algorithm": "identity-patterns-sha256-v1",
                        "patterns": ["z/*.csv", "a/*.csv"],
                    },
                },
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
                                    },
                                    {
                                        "direction": "input",
                                        "path": "unmatched-external.csv",
                                        "proof": "declared",
                                    },
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
                            },
                        ],
                        "artifacts": [
                            "isolated-input.csv",
                            "isolated-output.csv",
                            *[f"input-{number}.csv" for number in range(200)],
                            *[f"output-{number}.csv" for number in range(200)],
                        ],
                        "edges": [],
                        "signals": [],
                        "registry": registries,
                    },
                    {
                        "chain_id": "registry-only",
                        "entry": "e001",
                        "findings": [],
                        "commands": [],
                        "artifacts": [],
                        "edges": [],
                        "signals": [],
                        "registry": registries,
                    },
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
            audit_validation_result(root, stored.result_id)
            with result_snapshot(root) as db:
                links = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT c.command_id,k.code "
                        "FROM validation_batch_command_links AS l "
                        "JOIN validation_results AS r USING (result_pk) "
                        "JOIN validation_commands AS c "
                        "USING (result_pk,command_pk) "
                        "JOIN validation_codes AS k USING (result_pk,code_pk) "
                        "WHERE r.result_id=? ORDER BY c.command_id,k.code",
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
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_registry_records"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM validation_group_registry"
                    ).fetchone()[0],
                    4,
                )
                identity_members = [
                    tuple(row)
                    for row in db.execute(
                        "SELECT r.name,m.position,m.member "
                        "FROM validation_registry_records AS r "
                        "JOIN validation_registry_identity_members AS m "
                        "USING (result_pk,registry_pk) ORDER BY r.name,m.position"
                    )
                ]
                relationship_storage = {
                    tuple(row)
                    for row in db.execute(
                        "SELECT path_artifact_pk IS NOT NULL,path_text "
                        "FROM validation_command_relationships"
                    )
                }
                direct_counts = {
                    table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in (
                        "validation_findings",
                        "validation_command_relationships",
                        "validation_batch_command_links",
                    )
                }
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
            self.assertEqual(
                direct_counts,
                {
                    "validation_findings": finding_count,
                    "validation_command_relationships": 403,
                    "validation_batch_command_links": 3,
                },
            )
            self.assertLess(
                actual_rows,
                finding_count * 403,
                "stored rows must not track the finding/path cross-product",
            )
            self.assertEqual(
                identity_members,
                [
                    ("files", 0, "z.csv"),
                    ("files", 1, "a.csv"),
                    ("patterns", 0, "z/*.csv"),
                    ("patterns", 1, "a/*.csv"),
                ],
            )
            self.assertIn((0, "unmatched-external.csv"), relationship_storage)
            self.assertIn((1, None), relationship_storage)
            with mock.patch(
                "validation.result_storage._audit_validation_result",
                side_effect=AssertionError("ordinary selector performed full audit"),
            ), mock.patch(
                "validation.result_storage._expected_batch_command_links",
                side_effect=AssertionError("ordinary selector reconstructed links"),
            ), mock.patch(
                "validation.result_storage._registry_public_value",
                side_effect=AssertionError("ordinary selector hydrated registry"),
            ), mock.patch(
                "validation.result_storage._group",
                side_effect=AssertionError("ordinary selector hydrated a group"),
            ):
                summary, summary_tables, _ = _traced_read_tables(
                    lambda: inspect_result(
                        root,
                        Query(
                            result_id=stored.result_id,
                            view="summary",
                            limit=1,
                        ),
                    )
                )
                self.assertEqual(summary["result_id"], stored.result_id)
                self.assertEqual(summary_tables, {"validation_results"})
                selected, selected_tables, selected_statements = _traced_read_tables(
                    lambda: inspect_result(
                        root,
                        Query(
                            action="finding",
                            entity="entry:e001:check:0",
                            result_id=stored.result_id,
                            limit=1,
                        ),
                    )
                )
                self.assertEqual(selected["returned"], 1)
                self.assertTrue(
                    any(" LIMIT 2" in statement for statement in selected_statements)
                )
                self.assertTrue(
                    {
                        "validation_group_registry",
                        "validation_registry_records",
                        "validation_batch_command_links",
                    }.isdisjoint(selected_tables)
                )
                for action, entity in (
                    ("command", "anchor"),
                    ("artifact", "input-0.csv"),
                ):
                    self.assertEqual(
                        inspect_result(
                            root,
                            Query(
                                action=action,
                                entity=entity,
                                result_id=stored.result_id,
                                limit=1,
                            ),
                        )["returned"],
                        1,
                    )
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
                {item["artifact_id"] for page in pages for item in page["items"]},
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
                        ValidationPublicationRequest(bounded_root, record, projection)
                    )

            database = root / ".cache" / "results.sqlite"
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "UPDATE validation_checks SET observed_json='not-json' "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?) AND check_id='entry:e001:check:39'",
                    (stored.result_id,),
                )
            self.assertEqual(
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:0",
                        result_id=stored.result_id,
                    ),
                )["returned"],
                1,
            )
            with self.assertRaisesRegex(InspectionError, "selected finding JSON"):
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:39",
                        result_id=stored.result_id,
                    ),
                )
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "UPDATE validation_checks SET observed_json='{}' "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?) AND check_id='entry:e001:check:39'",
                    (stored.result_id,),
                )
                raw.execute(
                    "UPDATE validation_finding_affected_entries SET position=2 "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?) AND check_pk=(SELECT check_pk "
                    "FROM validation_checks WHERE result_pk=(SELECT result_pk "
                    "FROM validation_results WHERE result_id=?) "
                    "AND check_id='entry:e001:check:39')",
                    (stored.result_id, stored.result_id),
                )
            self.assertEqual(
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:0",
                        result_id=stored.result_id,
                    ),
                )["returned"],
                1,
            )
            with self.assertRaisesRegex(InspectionError, "affected-entry positions"):
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:39",
                        result_id=stored.result_id,
                    ),
                )
            with sqlite3.connect(database) as raw:
                raw.execute(
                    "UPDATE validation_checks SET scope='invalid-scope' "
                    "WHERE result_pk=(SELECT result_pk FROM validation_results "
                    "WHERE result_id=?) AND check_id='entry:e001:check:38'",
                    (stored.result_id,),
                )
            self.assertEqual(
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:0",
                        result_id=stored.result_id,
                    ),
                )["returned"],
                1,
            )
            with self.assertRaisesRegex(InspectionError, "invalid scope"):
                inspect_result(
                    root,
                    Query(
                        action="finding",
                        entity="entry:e001:check:38",
                        result_id=stored.result_id,
                    ),
                )

    def test_export_bound_fails_before_materialization(self) -> None:
        """The real row exporter accepts its exact cap and stops one byte over."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            record = _record(root)
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, _projection(record))
            )
            expected = export_validation_result(root, stored.result_id)
            encoded_size = len(
                json.dumps(
                    expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            )
            with mock.patch(
                "validation.result_storage.MAX_VALIDATION_RESULT_BYTES",
                encoded_size,
            ):
                self.assertEqual(
                    export_validation_result(root, stored.result_id), expected
                )
            with mock.patch(
                "validation.result_storage.MAX_VALIDATION_RESULT_BYTES",
                encoded_size + 1,
            ):
                self.assertEqual(
                    export_validation_result(root, stored.result_id), expected
                )
            with (
                mock.patch(
                    "validation.result_storage.MAX_VALIDATION_RESULT_BYTES",
                    encoded_size - 1,
                ),
                mock.patch(
                    "validation.result_storage.CappedJsonEncoder.materialize"
                ) as materialize,
            ):
                with self.assertRaises(ResultStoreError) as raised:
                    export_validation_result(root, stored.result_id)
            materialize.assert_not_called()
            self.assertEqual(raised.exception.code, "results.export.too_large")
            with (
                mock.patch("validation.result_storage.MAX_VALIDATION_RESULT_BYTES", 1),
                mock.patch(
                    "validation.result_storage._write_batch_export",
                    side_effect=AssertionError("decoded a later batch"),
                ) as later,
            ):
                with self.assertRaises(ResultStoreError):
                    export_validation_result(root, stored.result_id)
            later.assert_not_called()

    def test_validation_publication_byte_bound_is_exact_and_atomic(self) -> None:
        for offset, succeeds in ((-1, False), (0, True), (1, True)):
            with (
                self.subTest(offset=offset),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "study"
                root.mkdir()
                baseline_record = _record(root)
                baseline = publish_validation_result(
                    ValidationPublicationRequest(
                        root, baseline_record, _projection(baseline_record)
                    )
                )
                self.assertTrue(
                    record_report_materialization(
                        root, "validation", b"baseline", expected_generation=1
                    )
                )
                candidate_record = _record(root, "entry:e002:check")
                candidate_projection = _projection(candidate_record)
                bounded_candidate = {
                    "record": candidate_record.as_dict(),
                    "projection": candidate_projection,
                    "metadata": {
                        "requested_entries": [],
                        "evaluated_entries": [],
                        "dependency_entries": [],
                        "whole_log_limitations": [],
                    },
                    "report_context": None,
                }
                exact_size = len(
                    json.dumps(
                        bounded_candidate,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                with mock.patch(
                    "validation.result_storage.MAX_VALIDATION_RESULT_BYTES",
                    exact_size + offset,
                ):
                    if succeeds:
                        candidate = publish_validation_result(
                            ValidationPublicationRequest(
                                root, candidate_record, candidate_projection
                            )
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, "byte bound"):
                            publish_validation_result(
                                ValidationPublicationRequest(
                                    root, candidate_record, candidate_projection
                                )
                            )
                with result_snapshot(root) as db:
                    generation = db.execute(
                        "SELECT generation FROM store_state WHERE domain='validation'"
                    ).fetchone()[0]
                    marker = db.execute(
                        "SELECT source_generation FROM report_materializations "
                        "WHERE kind='validation'"
                    ).fetchone()
                if succeeds:
                    self.assertEqual(latest_full_validation(root), candidate)
                    self.assertEqual(generation, 2)
                    self.assertIsNone(marker)
                else:
                    self.assertEqual(latest_full_validation(root), baseline)
                    self.assertEqual(generation, 1)
                    self.assertEqual(marker[0], 1)

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
                        "SELECT c.command_id,k.code "
                        "FROM validation_batch_command_links AS l "
                        "JOIN validation_results AS r USING (result_pk) "
                        "JOIN validation_commands AS c "
                        "USING (result_pk,command_pk) "
                        "JOIN validation_codes AS k USING (result_pk,code_pk) "
                        "WHERE r.result_id=? ORDER BY c.command_id,k.code",
                        (stored.result_id,),
                    )
                }
            self.assertIn(("c1", "fixture.failure"), links)
            self.assertNotIn(("c2", "fixture.failure"), links)
