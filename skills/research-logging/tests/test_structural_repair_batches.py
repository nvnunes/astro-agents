"""Primary repair work remains bounded and can verify a corrected rejected command."""

from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.inspection_queries import Query, inspect_result
from log_commands.repair_validation import _reconciled
from research_log_cli_test_support import run_log
from research_log_data import build_local_input
from research_log_validation_test_support import mechanical_log
from validation.batch_projection import build_batch_projection
from validation.inspection import save_result
from validation.inspection_store import InspectionError, connection
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from validation.repair_batch_contract import valid_repair_projection


def failure(identity, code, subject, observed=None):
    return MechanicalCheck(
        identity,
        CheckScope.PROVENANCE,
        CheckStatus.FAIL,
        subject,
        failure=FailurePayload(code, subject, observed or {}, "provenance contract"),
    )


def project(checks):
    record = MechanicalGeneratedRecord.build(
        "/project/study.md", "rules", "2026-09-08", checks
    )
    return record, build_batch_projection(
        record, invocations=(), registries=(), source_identity="source"
    )


class StructuralRepairBatchTests(unittest.TestCase):
    def test_summary_counts_primary_batches_once_and_preserves_other_areas(self):
        from validation.report import batch_area_results

        record, projection = project(
            [
                failure("entry:e001:a", "producer.missing", "/a"),
                failure("entry:e002:a", "producer.missing", "/a"),
                failure("entry:e001:b", "producer.missing", "/b"),
                failure("entry:e001:c", "producer.missing", "c"),
            ]
        )
        batches = projection["repair_batches"]
        material_b = next(
            b
            for b in batches
            if b["anchors"]
            == [{"kind": "material", "path": "/b", "defect": "producer.missing"}]
        )
        material_b["batch_type"] = "chain"
        material_b["grouping_reason"] = "command_chain"
        self.assertEqual(
            batch_area_results(record, projection),
            {
                "Structure": "1 chain + 1 structural + 1 inspection",
                "Evidence": "Clear",
                "Confirmation": "Clear",
            },
        )
        # Related-chain and multi-entry links must not add primary work.
        for batch in batches:
            batch["related_chain_ids"] = ["shared", "also-shared"]
        self.assertEqual(
            batch_area_results(record, projection)["Structure"],
            "1 chain + 1 structural + 1 inspection",
        )
        empty_record, empty = project([])
        self.assertEqual(batch_area_results(empty_record, empty)["Structure"], "Clear")
        projection["unresolved"][0]["findings"][0]["status"] = "unavailable"
        self.assertEqual(batch_area_results(record, projection)["Structure"], "—")

    def test_reused_command_position_does_not_clear_original_rejected_command(self):
        from log_commands.repair_reconciliation import ReconciliationIndex

        original = {
            "identity": "entry:e001:command:1:1",
            "scope": "conformance",
            "subject": "original.py",
            "observed": {
                "rejected_command": {
                    "entry": "e001",
                    "document": "/project/e001.md",
                    "fence": 1,
                    "ordinal": 1,
                    "script": "/project/original.py",
                    "declared_outputs": [{"path": "/project/out"}],
                }
            },
        }
        index = ReconciliationIndex(
            {
                "checks": [
                    {
                        "identity": original["identity"],
                        "status": "pass",
                    }
                ]
            },
            {"chains": []},
            {},
        )
        self.assertEqual(index.reconcile(original)["status"], "incomplete")

    def test_directory_diagnostic_does_not_imply_competing_owners(self):
        cases = [
            ({"exact_producers": [], "conflicts": []}, "exact_material"),
            ({"missing_member": "/directory/x", "producer": "owner"}, "exact_material"),
            (
                {"exact_producers": ["owner"], "conflicts": ["other"]},
                "competing_ownership",
            ),
        ]
        for observed, reason in cases:
            with self.subTest(observed=observed):
                _, projection = project(
                    [
                        failure(
                            "provenance:e001:x",
                            "directory.producer.conflict",
                            "registered",
                            {"material": "/directory", **observed},
                        )
                    ]
                )
                self.assertEqual(
                    projection["repair_batches"][0]["grouping_reason"], reason
                )

    def test_cached_structure_summary_matches_primary_report_without_expansion(self):
        from validation.report import batch_area_results

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            record, projection = project(
                [
                    failure("entry:e001:a", "producer.missing", "/shared"),
                    failure("entry:e002:a", "producer.missing", "/shared"),
                    failure("entry:e001:b", "producer.missing", "unanchored"),
                    failure(
                        "entry:e001:c", "provenance.output.unconfirmed", "/confirmed"
                    ),
                ]
            )
            projection["chains"] = [
                {
                    "chain_id": "related-chain",
                    "entry": "e001",
                    "commands": [],
                    "findings": [],
                    "artifacts": [],
                }
            ]
            batch = projection["repair_batches"][0]
            batch["related_chain_ids"] = ["related-chain"]
            result_id = save_result(
                summary,
                {"status": "complete_findings", "published": True},
                record.as_dict(),
                projection,
                {"kind": "full", "started_at": "2026-09-08"},
            )
            related = inspect_result(
                summary.with_suffix(""),
                Query(
                    result_id=result_id,
                    view="chains",
                    batch=batch["batch_id"],
                ),
            )
            self.assertEqual(related["total"], 1)
            self.assertEqual(related["items"][0]["chain_id"], "related-chain")
            result = inspect_result(summary.with_suffix(""), Query(result_id=result_id))
            self.assertEqual(
                result["metadata"]["Structure"], "1 structural + 1 inspection"
            )
            self.assertEqual(
                result["metadata"]["Structure"],
                batch_area_results(record, projection)["Structure"],
            )

    def test_large_structural_batch_uses_bounded_text_and_selective_members(self):
        from validation.inspection import timestamp

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            record, projection = project(
                [
                    failure(f"entry:e001:f:{i:04}", "producer.missing", "/x")
                    for i in range(1000)
                ]
            )
            identity = save_result(
                summary,
                {"status": "complete_findings", "published": True},
                record.as_dict(),
                projection,
                {"kind": "full", "started_at": timestamp()},
            )
            logical = summary.with_suffix("")
            result = run_log(
                root,
                "results",
                "show",
                "--path",
                str(logical),
                "--id",
                identity,
                "--view",
                "batches",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stdout.encode()), 16384)
            batch = projection["repair_batches"][0]
            members = inspect_result(
                logical,
                Query(
                    result_id=identity,
                    view="findings",
                    batch=batch["batch_id"],
                    limit=3,
                ),
            )
            self.assertEqual(members["total"], 1000)
            self.assertEqual(members["returned"], 3)
            self.assertTrue(members["next_cursor"])

    def test_primary_reassignment_preserves_provenance_membership(self):
        from validation.repair_batches import build_repair_batches, direct_findings

        command = {
            "entry": "e001",
            "document": "entries/e001.md",
            "fence": 1,
            "ordinal": 1,
            "identity": "entry:e001:command:1:1",
        }
        record, _ = project(
            [
                failure(
                    command["identity"],
                    "material.candidate.unresolved",
                    "argument",
                    {"rejected_command": command},
                )
            ]
        )
        chains = [
            {
                "chain_id": "existing",
                "entry": "e001",
                "commands": [],
                "findings": list(direct_findings(record).values()),
            }
        ]
        before = copy.deepcopy(chains)
        batches = build_repair_batches(record, chains)
        self.assertEqual(chains, before)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["batch_type"], "structural")
        self.assertEqual(batches[0]["related_chain_ids"], ["existing"])

    def test_registration_repair_uses_evaluated_registration_and_reports_followups(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            logical, entry = summary.with_suffix(""), document.parent
            source = entry / "data/catalog.csv"
            source.write_text(source.read_text() + "\n")
            first = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(first.returncode, 0, first.stderr)
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            view = inspect_result(logical, Query(result_id=result_id, view="batches"))
            batch = next(
                b
                for b in view["items"]
                if b["grouping_reason"] == "exact_material"
                and b["anchors"][0]["kind"] == "registration"
            )
            data = json.loads((entry / "data.json").read_text())
            data["inputs"] = [
                build_local_input(
                    "catalog", "file", "data/catalog.csv", entry_root=entry, origin=True
                ).as_dict()
                if r["name"] == "catalog"
                else r
                for r in data["inputs"]
            ]
            (entry / "data.json").write_text(json.dumps(data))
            projection = inspect_result(logical, Query(result_id=result_id))[
                "metadata"
            ]["validation_id"]
            result = run_log(
                root,
                "validate-batch",
                "--path",
                str(logical),
                "--validation",
                projection,
                "--batch",
                batch["batch_id"],
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            value = json.loads(result.stdout)
            self.assertTrue(
                all(r["status"] == "clear" for r in value["reconciliation"])
            )
            self.assertIn(
                "verified_registration", value["reconciliation"][0]["relationship"]
            )
            self.assertEqual(
                value["status"],
                "complete_findings" if value["findings"] else "complete_clear",
            )

    def test_reconciliation_does_not_clear_a_different_target_or_lost_rule(self):
        old = failure("provenance:e001:x", "producer.missing", "/old")
        _, published = project([old])
        batch = published["repair_batches"][0]
        for status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE):
            current = MechanicalCheck(
                old.identity,
                old.scope,
                status,
                old.identity,
                dependencies=({"evaluated_materials": ["/new"]},),
            )
            record, projection = project([current])
            result = _reconciled(
                batch,
                published,
                record.as_dict(),
                projection,
                ({"e001"}, set(), {"e001"}, ()),
            )
            self.assertEqual(result["status"], "incomplete")

    def test_successor_finding_is_reported_without_clearing_moved_work(self):
        _, published = project([failure("provenance:e001:x", "producer.missing", "/x")])
        record, projection = project(
            [failure("provenance:e001:y", "lineage.missing", "/x")]
        )
        result = _reconciled(
            published["repair_batches"][0],
            published,
            record.as_dict(),
            projection,
            ({"e001"}, set(), {"e001"}, ()),
        )
        self.assertEqual(result["status"], "complete_findings")
        self.assertEqual(
            result["reconciliation"][0]["current_finding_ids"], ["provenance:e001:y"]
        )
        self.assertEqual(
            result["current_membership"][0]["validation_id"],
            projection["validation_id"],
        )

    def test_reconciliation_keeps_registration_names_local_to_their_entry(self):
        checks = [
            failure(
                f"entry:{entry}:input:catalog-declaration",
                "data.fingerprint.mismatch",
                "catalog",
                {"registration": {"entry": entry, "name": "catalog", "path": path}},
            )
            for entry, path in (("e001", "/a"), ("e002", "/b"))
        ]
        _, published = project(checks)
        record, projection = project(checks[1:])
        batch = next(b for b in published["repair_batches"] if b["entries"] == ["e001"])
        result = _reconciled(
            batch,
            published,
            record.as_dict(),
            projection,
            (
                {"e001", "e002"},
                set(),
                {"e001", "e002"},
                ({"entry": "e001", "name": "catalog", "path": "/corrected"},),
            ),
        )
        self.assertEqual(result["status"], "complete_clear")
        self.assertEqual(result["reconciliation"][0]["status"], "clear")
        self.assertEqual(result["findings"], [])

    def test_reconciliation_keeps_cross_entry_exact_material_correspondence(self):
        _, published = project([failure("entry:e001:a", "producer.missing", "/shared")])
        record, projection = project(
            [failure("entry:e002:b", "lineage.missing", "/shared")]
        )
        result = _reconciled(
            published["repair_batches"][0],
            published,
            record.as_dict(),
            projection,
            ({"e001", "e002"}, set(), {"e001", "e002"}, ()),
        )
        self.assertEqual(result["status"], "complete_findings")
        self.assertEqual(
            result["reconciliation"][0]["current_finding_ids"], ["entry:e002:b"]
        )

    def test_shared_entry_set_is_evaluated_once_and_retry_is_bounded(self):
        import log_commands.repair_validation as validation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            document.write_text(
                document.read_text().replace("--input-catalog", "--catalog")
            )
            logical = summary.with_suffix("")
            first = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(first.returncode, 0, first.stderr)
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            batch = next(
                b
                for b in inspect_result(
                    logical, Query(result_id=result_id, view="batches")
                )["items"]
                if b["grouping_reason"] == "rejected_command"
            )
            projection = inspect_result(logical, Query(result_id=result_id))[
                "metadata"
            ]["validation_id"]
            with mock.patch.object(
                validation,
                "evaluate_entries_record",
                wraps=validation.evaluate_entries_record,
            ) as evaluated:
                outcome = run_log(
                    root,
                    "validate-batch",
                    "--path",
                    str(logical),
                    "--validation",
                    projection,
                    "--batch",
                    batch["batch_id"],
                    "--format",
                    "json",
                )
            self.assertEqual(outcome.returncode, 0, outcome.stderr + outcome.stdout)
            self.assertEqual(evaluated.call_count, 1)
            with (
                mock.patch.object(
                    validation,
                    "_snapshot",
                    side_effect=[
                        ("before",),
                        ("changed",),
                        ("stable",),
                        ("stable",),
                    ],
                ),
                mock.patch.object(
                    validation,
                    "evaluate_entries_record",
                    wraps=validation.evaluate_entries_record,
                ) as evaluated,
            ):
                retried = run_log(
                    root,
                    "validate-batch",
                    "--path",
                    str(logical),
                    "--validation",
                    projection,
                    "--batch",
                    batch["batch_id"],
                )
            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertEqual(evaluated.call_count, 2)
            with (
                mock.patch.object(
                    validation,
                    "_snapshot",
                    side_effect=[("a",), ("b",), ("c",), ("d",)],
                ),
                mock.patch.object(
                    validation,
                    "evaluate_entries_record",
                    wraps=validation.evaluate_entries_record,
                ) as evaluated,
            ):
                outcome = run_log(
                    root,
                    "validate-batch",
                    "--path",
                    str(logical),
                    "--validation",
                    projection,
                    "--batch",
                    batch["batch_id"],
                    "--format",
                    "json",
                )
            value = json.loads(outcome.stdout)
            self.assertEqual(value["reason"], "source_changed")
            self.assertEqual(evaluated.call_count, 2)
            self.assertEqual(value["status"], "incomplete")
            self.assertTrue(value["findings"])
            self.assertTrue(value["current_membership"])
            self.assertEqual(value["coverage"]["entries"], ["e001"])
            self.assertEqual(value["coverage"]["rules"], [])
            self.assertEqual(value["coverage"]["missing"], batch["primary_finding_ids"])
            self.assertTrue(value["reconciliation"])
            self.assertTrue(
                all(
                    r["status"] == "incomplete" and r["reason"] == "source_changed"
                    for r in value["reconciliation"]
                )
            )
            cached = inspect_result(logical, Query(action="list", kind="batch"))
            result_id = cached["items"][0]["result_id"]
            metadata = inspect_result(logical, Query(result_id=result_id))["metadata"]
            self.assertEqual(metadata["evaluated_scope"], ["e001"])
            self.assertGreater(metadata["evaluated_checks"], 0)
            self.assertEqual(metadata["Structure"], "—")
            findings = inspect_result(
                logical, Query(result_id=result_id, view="findings")
            )
            self.assertEqual(findings["total"], len(value["findings"]))
            document.write_text(
                document.read_text().replace("--catalog", "--input-catalog")
            )
            with mock.patch.object(
                validation,
                "_snapshot",
                side_effect=[("a",), ("b",), ("c",), ("d",)],
            ):
                repaired = run_log(
                    root,
                    "validate-batch",
                    "--path",
                    str(logical),
                    "--validation",
                    projection,
                    "--batch",
                    batch["batch_id"],
                    "--format",
                    "json",
                )
            value = json.loads(repaired.stdout)
            self.assertEqual(value["status"], "incomplete")
            self.assertEqual(value["reason"], "source_changed")
            self.assertTrue(value["reconciliation"])
            for member in value["reconciliation"]:
                self.assertEqual(member["status"], "incomplete")
                self.assertNotIn("relationship", member)
                self.assertNotIn("check_id", member)

    def test_rechecking_one_batch_preserves_other_and_original_entry_filters(self):
        from validation.inspection import timestamp
        from validation.repair_batches import direct_findings

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = mechanical_log(Path(directory))
            logical = summary.with_suffix("")

            def save(material, entries, *, coverage=None):
                record, projection = project(
                    [
                        failure(f"entry:{entry}:x", "producer.missing", material)
                        for entry in entries
                    ]
                )
                batch = projection["repair_batches"][0]
                outcome = {
                    "status": "complete_findings",
                    "findings": list(direct_findings(record).values()),
                    "coverage": {"entries": coverage or entries},
                    "reconciliation": [],
                    "current_membership": [],
                    "requested_repair_batch": batch,
                }
                request = {
                    "kind": "batch",
                    "validation": "original",
                    "batch": batch["batch_id"],
                    "entries": json.dumps(entries),
                    "started_at": timestamp(),
                }
                return save_result(
                    summary, outcome, record.as_dict(), projection, request
                ), batch

            a_id, a = save("/a", ["e001", "e002"])
            b_id, _ = save("/b", ["e003"])
            new_a, _ = save("/a", ["e001", "e002"], coverage=["e001", "e002", "e004"])
            with self.assertRaises(InspectionError):
                inspect_result(logical, Query(result_id=a_id))
            self.assertEqual(
                inspect_result(logical, Query(result_id=b_id))["result_id"], b_id
            )
            listing = inspect_result(
                logical, Query(action="list", entry="e002", batch=a["batch_id"])
            )
            self.assertEqual(listing["items"][0]["result_id"], new_a)
            self.assertEqual(
                inspect_result(logical, Query(action="list", entry="e004"))["total"], 0
            )
            filtered = inspect_result(
                logical,
                Query(
                    result_id=new_a,
                    view="batches",
                    entry="e002",
                    code="producer.missing",
                ),
            )
            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["items"][0]["entries"], ["e001", "e002"])

    def test_batch_index_grows_with_members_and_preserves_shared_entry_filters(self):
        for count in (10, 20):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                summary, _ = mechanical_log(Path(directory))
                logical = summary.with_suffix("")
                record, projection = project(
                    [
                        failure(
                            f"entry:e{i:03}:x",
                            "producer.ambiguous" if i == 1 else "lineage.ambiguous",
                            "/shared",
                        )
                        for i in range(1, count + 1)
                    ]
                )
                batch = projection["repair_batches"][0]
                command = {
                    "identity": "command",
                    "entry": "e001",
                    "document": "entry.md",
                    "fence": 1,
                    "ordinal": 1,
                    "inputs": [{"path": "/shared"}],
                    "outputs": [{"path": "/output"}],
                }
                projection["chains"] = [
                    {
                        "entry": "e001",
                        "chain_id": "related",
                        "commands": [command],
                        "findings": [],
                        "artifacts": ["/shared", "/output"],
                    }
                ]
                batch["related_chain_ids"] = ["related"]
                batch["anchors"].append({"kind": "material", "path": "/output"})
                result_id = save_result(
                    summary,
                    {"status": "complete_findings", "published": True},
                    record.as_dict(),
                    projection,
                    {"kind": "full", "started_at": "2026-09-08"},
                )
                with connection(logical) as db:
                    counts = dict(
                        db.execute(
                            "SELECT kind, count(*) FROM batch_links "
                            "WHERE result=? GROUP BY kind",
                            (result_id,),
                        )
                    )
                self.assertEqual(counts["findings"], count)
                self.assertEqual(counts["commands"], 1)
                self.assertEqual(counts["artifacts"], 2)
                # Entry scope selects the whole shared batch, even when this code
                # belongs to a different entry. Old duplicated stores stay readable.
                for legacy in (False, True):
                    with self.subTest(legacy=legacy):
                        if legacy:
                            with connection(logical, writable=True) as db:
                                db.execute(
                                    "INSERT OR IGNORE INTO batch_links "
                                    "SELECT l.result,l.kind,l.id,b.entry,"
                                    "l.batch,l.code "
                                    "FROM batch_links l JOIN batch_links b "
                                    "ON b.result=l.result AND b.batch=l.batch "
                                    "AND b.kind='batches' AND b.entry!='' "
                                    "WHERE l.result=? AND l.entry=''",
                                    (result_id,),
                                )
                                db.execute("DELETE FROM batch_links WHERE entry='' ")
                        for view, total in (("batches", 1), ("findings", 1)):
                            value = inspect_result(
                                logical,
                                Query(
                                    result_id=result_id,
                                    view=view,
                                    entry="e002",
                                    batch=batch["batch_id"]
                                    if view == "findings"
                                    else None,
                                    code="producer.ambiguous",
                                ),
                            )
                            self.assertEqual(value["total"], total)
                        for view, total in (
                            ("findings", count),
                            ("commands", 1),
                            ("artifacts", 2),
                        ):
                            value = inspect_result(
                                logical,
                                Query(
                                    result_id=result_id,
                                    view=view,
                                    entry="e002",
                                    batch=batch["batch_id"],
                                ),
                            )
                            self.assertEqual(value["total"], total)
                        self.assertEqual(
                            inspect_result(
                                logical,
                                Query(
                                    result_id=result_id,
                                    view="batches",
                                    entry="e999",
                                ),
                            )["total"],
                            0,
                        )

    def test_batch_selector_rejects_old_commands_and_invalid_targets(
        self,
    ):
        import log_commands.repair_validation as validation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            logical = summary.with_suffix("")
            document.write_text(
                document.read_text().replace("--input-catalog", "--catalog")
            )
            run_log(root, "validate", "--path", str(logical))
            path = logical / "validation/batches.json"
            published = json.loads(path.read_text())
            batch = published["repair_batches"][0]["batch_id"]
            request = (
                "validate-batch",
                "--path",
                str(logical),
                "--validation",
                published["validation_id"],
            )
            with mock.patch.object(validation, "evaluate_entries_record") as evaluated:
                for selectors in (
                    ("--entry", "e001", "--chain", "old"),
                    ("--batch", batch, "--entry", "e001"),
                    ("--batch", batch, "--chain", "old"),
                ):
                    rejected = run_log(root, *request, *selectors)
                    self.assertEqual(rejected.returncode, 2)
                unknown = run_log(root, *request, "--batch", "unknown")
                self.assertIn("findings.batch.unknown", unknown.stderr)
                stale = run_log(
                    root,
                    "validate-batch",
                    "--path",
                    str(logical),
                    "--validation",
                    "stale",
                    "--batch",
                    batch,
                )
                self.assertIn("findings.validation_superseded", stale.stderr)
                evaluated.assert_not_called()

    def test_validation_rebuilds_outdated_publication_and_cache_without_translation(
        self,
    ):
        from validation.inspection_store import STORE_NAME, STORE_VERSION

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            logical = summary.with_suffix("")
            document.write_text(
                document.read_text().replace("--input-catalog", "--catalog")
            )
            initial = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(initial.returncode, 0, initial.stderr)
            path = logical / "validation/batches.json"
            old = json.loads(path.read_text())
            old["schema"] = "research-log-batch-projection/2"
            old["projection_id"] = old.pop("validation_id")
            path.write_text(json.dumps(old))
            with sqlite3.connect(logical / ".cache" / STORE_NAME) as db:
                db.execute("ALTER TABLE results RENAME COLUMN validation TO projection")
                db.execute("UPDATE results SET metadata='obsolete content'")
                db.execute("PRAGMA user_version=2")
            before = path.read_bytes()
            inspected = run_log(root, "results", "list", "--path", str(logical))
            self.assertEqual(inspected.returncode, 2)
            self.assertIn("results.schema.unsupported", inspected.stderr)
            rejected = run_log(root, "findings", "list", "--path", str(logical))
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("run full validation", rejected.stderr)
            self.assertEqual(path.read_bytes(), before)
            refreshed = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            current = json.loads(path.read_text())
            self.assertEqual(current["schema"], "research-log-published-validation/1")
            self.assertIn("validation_id", current)
            self.assertNotIn("projection_id", current)
            self.assertNotIn("projection", refreshed.stdout)
            with sqlite3.connect(logical / ".cache" / STORE_NAME) as db:
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone()[0], STORE_VERSION
                )
                self.assertEqual(
                    db.execute("SELECT count(*) FROM results").fetchone()[0], 1
                )
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            exported = inspect_result(
                logical, Query(action="export", result_id=result_id)
            )
            self.assertEqual(exported["validation_id"], current["validation_id"])
            self.assertNotIn("projection_id", json.dumps(exported))

    def test_validation_flag_is_used_in_help_and_recovery_commands(self):
        for command in (
            ("validate-batch",),
            ("findings", "batch"),
            ("results", "show"),
            ("results", "list"),
        ):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                help_result = run_log(root, *command, "--help")
                self.assertEqual(help_result.returncode, 0)
                self.assertIn("--validation", help_result.stdout)
                self.assertNotIn("--projection", help_result.stdout)
                rejected = run_log(
                    root, *command, "--path", str(root / "log"), "--projection", "old"
                )
                self.assertEqual(rejected.returncode, 2)

    def test_publication_read_access_is_not_a_publication_change(self):
        from types import SimpleNamespace

        import log_commands.repair_validation as validation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            logical = summary.with_suffix("")
            document.write_text(
                document.read_text().replace("--input-catalog", "--catalog")
            )
            run_log(root, "validate", "--path", str(logical))
            publication = logical / "validation/batches.json"
            canonical = publication.resolve()
            published = json.loads(publication.read_text())
            batch = next(
                b
                for b in published["repair_batches"]
                if b["grouping_reason"] == "rejected_command"
            )
            original_stat = Path.stat
            original_load = validation.load_batch_projection
            for field, expected in (("st_atime_ns", 0), ("st_mtime_ns", 2)):
                with self.subTest(field=field):
                    calls = 0
                    read_finished = False

                    def load_then_change(log):
                        nonlocal read_finished
                        value = original_load(log)
                        read_finished = True
                        return value

                    def changing_stat(path, **kwargs):
                        nonlocal calls
                        stat = original_stat(path, **kwargs)
                        if path not in (publication, canonical):
                            return stat
                        calls += 1
                        fields = {
                            key: getattr(stat, key)
                            for key in dir(stat)
                            if key.startswith("st_")
                        }
                        fields[field] += int(read_finished)
                        fields[field.removesuffix("_ns")] += int(read_finished)
                        return SimpleNamespace(**fields)

                    with (
                        mock.patch.object(Path, "stat", changing_stat),
                        mock.patch.object(
                            validation, "load_batch_projection", load_then_change
                        ),
                    ):
                        outcome = run_log(
                            root,
                            "validate-batch",
                            "--path",
                            str(logical),
                            "--validation",
                            published["validation_id"],
                            "--batch",
                            batch["batch_id"],
                        )
                    self.assertGreater(calls, 1)
                    self.assertEqual(outcome.returncode, expected, outcome.stderr)
                    if expected:
                        self.assertIn("findings.validation_superseded", outcome.stderr)

    def test_fallback_bounds_scope_and_primary_coverage_are_deterministic(self):
        checks = [
            failure(f"entry:e001:check:{i:03}", "unknown.condition", "record")
            for i in range(51)
        ]
        checks.append(failure("conformance:log", "unknown.condition", "log"))
        record, projection = project(checks)
        batches = projection["repair_batches"]
        self.assertEqual(
            sorted(len(b["primary_finding_ids"]) for b in batches), [1, 1, 50]
        )
        self.assertEqual(sum(b["scope"] == "log" for b in batches), 1)
        self.assertTrue(valid_repair_projection(projection, record))
        self.assertEqual(project(list(reversed(checks)))[1]["repair_batches"], batches)

    def test_exact_materials_join_across_entries_but_scoped_names_do_not(self):
        checks = [
            failure("entry:e001:a", "producer.missing", "/shared/x"),
            failure("entry:e002:a", "producer.missing", "/shared/x"),
            failure("entry:e001:b", "data.invalid", "catalog"),
            failure("entry:e002:b", "data.invalid", "catalog"),
        ]
        record, projection = project(checks)
        shared = next(
            b
            for b in projection["repair_batches"]
            if b["grouping_reason"] == "exact_material"
        )
        self.assertEqual(shared["entries"], ["e001", "e002"])
        self.assertEqual(
            shared["primary_finding_ids"], ["entry:e001:a", "entry:e002:a"]
        )
        self.assertEqual(len(projection["repair_batches"]), 3)
        self.assertTrue(valid_repair_projection(projection, record))

    def test_independent_rejected_anchors_do_not_merge_for_shared_consumer(self):
        commands = [
            {
                "entry": "e001",
                "document": "entries/e001.md",
                "fence": 1,
                "ordinal": i,
                "identity": f"entry:e001:command:1:{i}",
            }
            for i in (1, 2)
        ]
        checks = [
            failure(
                c["identity"],
                "material.candidate.unresolved",
                "argument",
                {"rejected_command": c},
            )
            for c in commands
        ]
        checks.append(
            failure(
                "entry:e002:consumer",
                "lineage.missing",
                "/shared/x",
                {"rejected_commands": commands},
            )
        )
        _, projection = project(checks)
        batches = projection["repair_batches"]
        shared = next(b for b in batches if b["grouping_reason"] == "inspection_group")
        self.assertEqual(shared["primary_finding_ids"], ["entry:e002:consumer"])
        self.assertEqual(len(shared["related_batch_ids"]), 2)

    def test_projection_rejects_missing_duplicate_and_malformed_ownership(self):
        record, projection = project(
            [failure("entry:e001:a", "producer.missing", "/x")]
        )
        for mutation in ("missing", "duplicate", "bad_reason"):
            with self.subTest(mutation=mutation):
                broken = copy.deepcopy(projection)
                if mutation == "missing":
                    broken["repair_batches"] = []
                elif mutation == "duplicate":
                    broken["repair_batches"] *= 2
                else:
                    broken["repair_batches"][0]["grouping_reason"] = []
                self.assertFalse(valid_repair_projection(broken, record))

    def test_each_material_and_ownership_member_requires_positive_coverage(self):
        for code in ("producer.missing", "directory.producer.conflict"):
            with self.subTest(code=code):
                checks = [
                    failure("entry:e001:a", code, "/shared/x"),
                    failure("entry:e002:a", code, "/shared/x"),
                ]
                _, published = project(checks)
                batch = published["repair_batches"][0]
                passed = [
                    MechanicalCheck(
                        c.identity,
                        c.scope,
                        CheckStatus.PASS,
                        c.subject,
                        dependencies=({"evaluated_materials": ["/shared/x"]},),
                    )
                    for c in checks
                ]
                record, projection = project(passed)
                outcome = _reconciled(
                    batch,
                    published,
                    record.as_dict(),
                    projection,
                    ({"e001", "e002"}, set(), {"e001", "e002"}, ()),
                )
                self.assertEqual(outcome["status"], "complete_clear")
                incomplete = _reconciled(
                    batch,
                    published,
                    record.as_dict(),
                    projection,
                    ({"e001"}, set(), {"e001", "e002"}, ()),
                )
                self.assertEqual(incomplete["status"], "incomplete")
                self.assertIn("entry:e002:a", incomplete["coverage"]["missing"])

    def test_rejected_command_full_path_and_source_deletion_is_not_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            original = document.read_text()
            document.write_text(original.replace("--input-catalog", "--catalog"))
            logical = summary.with_suffix("")
            publication = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(publication.returncode, 0, publication.stderr)
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            batches = inspect_result(
                logical, Query(result_id=result_id, view="batches")
            )["items"]
            batch = next(
                b for b in batches if b["grouping_reason"] == "rejected_command"
            )
            projection = inspect_result(logical, Query(result_id=result_id))[
                "metadata"
            ]["validation_id"]
            inspected = run_log(
                root,
                "results",
                "batch",
                "--path",
                str(logical),
                "--id",
                result_id,
                "--batch",
                batch["batch_id"],
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertIn("rejected_command", inspected.stdout)
            self.assertNotIn('{"', inspected.stdout)
            request = (
                "validate-batch",
                "--path",
                str(logical),
                "--validation",
                projection,
                "--batch",
                batch["batch_id"],
                "--format",
                "json",
            )
            published_bytes = (logical / "validation/batches.json").read_bytes()
            document.write_text(original)
            from validation.operation_state import operation_lock

            cached_files = {
                p: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in (logical / ".cache").rglob("*")
                if p.is_file()
                and not p.name.startswith("research-log-inspection.sqlite3")
            }
            with operation_lock(logical, "log.lock", mode="exclusive"):
                checked = run_log(root, *request)
            self.assertEqual(
                cached_files,
                {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in cached_files},
            )
            self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
            result = json.loads(checked.stdout)
            self.assertEqual(result["status"], "complete_clear")
            self.assertEqual(
                len(result["reconciliation"]), batch["primary_finding_count"]
            )
            self.assertEqual(
                (logical / "validation/batches.json").read_bytes(), published_bytes
            )
            recovered = inspect_result(
                logical,
                Query(
                    action="list",
                    kind="batch",
                    batch=batch["batch_id"],
                    validation=projection,
                ),
            )
            self.assertEqual(recovered["total"], 1)
            # A removed command cannot be certified just because the failure vanished.
            document.write_text(
                original.replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data '<results>'",
                    "# command removed",
                )
            )
            if document.read_text() == original:
                document.write_text(original.replace("./pyrun", "# removed ./pyrun"))
            removed = run_log(root, *request)
            self.assertNotEqual(json.loads(removed.stdout)["status"], "complete_clear")


if __name__ == "__main__":
    unittest.main()
