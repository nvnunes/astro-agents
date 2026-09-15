from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import mechanical_log, unittest

ENGINE = importlib.import_module("validation.engine")
DOMAIN = importlib.import_module("validation.domain")
QUERIES = importlib.import_module("validation.read_model")
REPORT = importlib.import_module("validation.snapshot_report")
SNAPSHOTS = importlib.import_module("validation.snapshot_storage")
STORE = importlib.import_module("research_log_result_store")


def _published(workspace: Path) -> tuple[Path, object]:
    summary, entry = mechanical_log(workspace)
    (entry.parent / "data/results.csv").write_text(
        "success_rate\n0.5\n", encoding="utf-8"
    )
    evaluation = ENGINE.evaluate_mechanical(
        ENGINE.EvaluationRequest(summary)
    )
    assert evaluation.snapshot is not None
    SNAPSHOTS.publish_validation_snapshot(
        SNAPSHOTS.SnapshotPublicationRequest(
            summary.with_suffix(""),
            evaluation.snapshot,
            {"requested": ("e001",), "evaluated": ("e001",)},
        )
    )
    return summary.with_suffix(""), evaluation.snapshot


def _synthetic_snapshot(root: Path, count: int, *, entry: str = "e001") -> Any:
    areas = tuple(DOMAIN.RuleArea)
    checks = tuple(
        DOMAIN.RuleCheck(
            f"finding:{position:03d}",
            areas[position % len(areas)],
            DOMAIN.CheckOutcome.FINDING,
            f"subject-{position:03d}",
            diagnostic=DOMAIN.CheckDiagnostic(
                f"code.{position:03d}",
                f"subject-{position:03d}",
                "Synthetic pagination rule",
                {"position": position},
            ),
            issue_context=DOMAIN.IssueContext(entry=entry),
        )
        for position in range(count)
    )
    attempt = DOMAIN.ValidationAttempt.build(
        target=DOMAIN.ValidationTarget(
            DOMAIN.TargetKind.LOG,
            root.with_suffix(".md").as_posix(),
        ),
        source_identity=f"source-{count}",
        rules_version="rules",
        started_at="2026-09-14T00:00:00Z",
        finished_at="2026-09-14T00:00:01Z",
        checks=checks,
    )
    return DOMAIN.ValidationSnapshot.from_attempt(
        attempt,
        report_context={
            "entries": {},
            "presentations": {
                f"code.{position:03d}": {
                    "name": f"Synthetic finding {position:03d}",
                    "sentence": "The synthetic condition failed.",
                    "target_kind": "record",
                }
                for position in range(count)
            },
            "title": "Study",
        },
    )


def _publish(
    root: Path,
    snapshot: Any,
    *,
    evaluated: tuple[str, ...] = ("e001",),
) -> None:
    SNAPSHOTS.publish_validation_snapshot(
        SNAPSHOTS.SnapshotPublicationRequest(
            root,
            snapshot,
            {"requested": evaluated, "evaluated": evaluated},
        )
    )


def _repair_snapshot(root: Path) -> Any:
    key = DOMAIN.RepairKey(DOMAIN.RepairKeyKind.COMMAND, "e001:build", "e001")
    command = DOMAIN.GraphReference("command", "e001:build", "e001")
    output = DOMAIN.GraphReference("material", "data/output.csv", "e001")
    source = DOMAIN.GraphReference("material", "data/source.csv", "e002")
    unrelated_command = DOMAIN.GraphReference("command", "e003:other", "e003")
    unrelated_output = DOMAIN.GraphReference("material", "data/other.csv", "e003")
    checks = (
        DOMAIN.RuleCheck(
            "conformance:build",
            DOMAIN.RuleArea.CONFORMANCE,
            DOMAIN.CheckOutcome.FINDING,
            "build command",
            diagnostic=DOMAIN.CheckDiagnostic(
                "command.invalid", "build command", "Command contract", {"exit": 2}
            ),
            issue_context=DOMAIN.IssueContext(
                entry="e001",
                repair_keys=(key,),
                context_nodes=(command, output),
                source_locations=(DOMAIN.SourceLocation("entries/e001.md", 12),),
            ),
        ),
        DOMAIN.RuleCheck(
            "provenance:output",
            DOMAIN.RuleArea.PROVENANCE,
            DOMAIN.CheckOutcome.FINDING,
            "data/output.csv",
            diagnostic=DOMAIN.CheckDiagnostic(
                "producer.invalid",
                "data/output.csv",
                "Producer contract",
                {"producer": "build"},
            ),
            issue_context=DOMAIN.IssueContext(
                entry="e001",
                repair_keys=(key,),
                context_nodes=(source,),
            ),
        ),
    )
    attempt = DOMAIN.ValidationAttempt.build(
        target=DOMAIN.ValidationTarget(
            DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
        ),
        source_identity="repair-source",
        rules_version="rules",
        started_at="2026-09-14T00:00:00Z",
        finished_at="2026-09-14T00:00:01Z",
        checks=checks,
    )
    references = (command, output, source, unrelated_command, unrelated_output)
    nodes = [
        {
            "attributes": {"label": reference.identity},
            **({} if reference.entry is None else {"entry": reference.entry}),
            "identity": reference.identity,
            "kind": reference.kind,
            "node_id": reference.node_id,
        }
        for reference in references
    ]
    repair_context = {
        "ambiguities": [
            {
                "candidates": [output.node_id, source.node_id],
                "kind": "producer-candidate",
                "observed": {"count": 2},
                "subject": command.node_id,
            }
        ],
        "nodes": nodes,
        "relationships": [
            {
                "kind": "production",
                "source": command.node_id,
                "target": output.node_id,
            },
            {
                "kind": "consumption",
                "source": source.node_id,
                "target": command.node_id,
            },
            {
                "kind": "production",
                "source": unrelated_command.node_id,
                "target": unrelated_output.node_id,
            },
        ],
    }
    return DOMAIN.ValidationSnapshot.from_attempt(
        attempt,
        repair_context=repair_context,
        report_context={
            "entries": {
                "e001": {"document": "entries/e001.md", "title": "Entry one"},
                "e002": {"document": "entries/e002.md", "title": "Entry two"},
            },
            "presentations": {
                "command.invalid": {
                    "name": "Invalid command",
                    "sentence": "The command contract is invalid.",
                    "target_kind": "command",
                },
                "producer.invalid": {
                    "name": "Invalid producer",
                    "sentence": "The producer contract is invalid.",
                    "target_kind": "record",
                },
            },
            "title": "Study",
        },
    )


class ValidationReadModelTests(unittest.TestCase):
    def test_show_and_lists_share_exact_saved_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _published(Path(directory))

            shown = QUERIES.show_validation(root)
            findings = QUERIES.list_findings(root, limit=100)
            batches = QUERIES.list_batches(root, limit=100)
            blocked = QUERIES.list_blocked(root, limit=100)
            failed = QUERIES.list_failed(root, limit=100)

        row = shown["rows"][0]
        self.assertEqual(row["outcome"], "Findings")
        self.assertEqual(
            sum(row["finding_counts_by_type"].values()),
            len(snapshot.findings),
        )
        self.assertEqual(row["batch_count"], len(snapshot.batches))
        self.assertEqual(row["blocked_check_count"], len(snapshot.blocked_checks))
        self.assertEqual(row["failed_check_count"], len(snapshot.failed_checks))
        self.assertEqual(findings["total"], len(snapshot.findings))
        self.assertEqual(batches["total"], len(snapshot.batches))
        self.assertEqual(blocked["total"], len(snapshot.blocked_checks))
        self.assertEqual(failed["total"], len(snapshot.failed_checks))
        self.assertEqual(
            {item["batch_id"] for item in findings["items"]},
            {item.batch_id for item in snapshot.batches},
        )
        self.assertFalse(any("type" in item for item in batches["items"]))

    def test_single_show_discloses_blocked_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            failed = DOMAIN.RuleCheck(
                "finding:failed",
                DOMAIN.RuleArea.PROVENANCE,
                DOMAIN.CheckOutcome.FINDING,
                "data/output.csv",
                diagnostic=DOMAIN.CheckDiagnostic(
                    "producer.missing",
                    "data/output.csv",
                    "Synthetic producer rule",
                    {},
                ),
                issue_context=DOMAIN.IssueContext(
                    repair_keys=(
                        DOMAIN.RepairKey(
                            DOMAIN.RepairKeyKind.MATERIAL, "data/output.csv"
                        ),
                    ),
                ),
            )
            blocked = DOMAIN.RuleCheck(
                "consumer:blocked",
                DOMAIN.RuleArea.PROVENANCE,
                DOMAIN.CheckOutcome.BLOCKED,
                "evidence:one",
                dependencies=(failed.check_id,),
                rule="Synthetic dependent rule",
            )
            unavailable = DOMAIN.RuleCheck(
                "source:failed",
                DOMAIN.RuleArea.EVIDENCE,
                DOMAIN.CheckOutcome.FAILED,
                "data/source.csv",
                diagnostic=DOMAIN.CheckDiagnostic(
                    "locator.reader.unavailable",
                    "data/source.csv",
                    "Readable validation source",
                    {"reason": "denied"},
                ),
                failure_operation=DOMAIN.FailureOperation.READ,
            )
            attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.LOG, root.with_suffix(".md").as_posix()
                ),
                source_identity="coverage",
                rules_version="rules",
                started_at="2026-09-14T00:00:00Z",
                finished_at="2026-09-14T00:00:01Z",
                checks=(failed, blocked, unavailable),
            )
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                attempt,
                report_context={
                    "entries": {},
                    "presentations": {
                        "producer.missing": {
                            "name": "Missing producer",
                            "sentence": "The producer is missing.",
                            "target_kind": "record",
                        }
                    },
                    "title": "Study",
                },
            )
            _publish(root, snapshot, evaluated=())

            single = QUERIES.show_validation(root)["rows"][0]
            blocked_rows = QUERIES.list_blocked(root)
            failed_rows = QUERIES.list_failed(root)
            combined = QUERIES.show_validation_root((root,))

        self.assertEqual(single["blocked_check_count"], 1)
        self.assertEqual(combined["blocked_check_total"], 1)
        self.assertFalse(combined["totals_partial"])
        self.assertEqual(combined["contributing_log_count"], 1)
        self.assertNotIn("blocked_check_count", combined["rows"][0])
        self.assertEqual(blocked_rows["total"], 1)
        self.assertEqual(blocked_rows["items"][0]["check_id"], blocked.check_id)
        self.assertEqual(
            blocked_rows["items"][0]["rule"], "Synthetic dependent rule"
        )
        self.assertEqual(
            blocked_rows["items"][0]["blocked_by"],
            [failed.check_id],
        )
        self.assertEqual(failed_rows["total"], 1)
        self.assertEqual(
            failed_rows["items"][0]["rule"], "Readable validation source"
        )

    def test_report_show_lists_and_detail_share_one_saved_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _repair_snapshot(root)
            _publish(root, snapshot, evaluated=("e001", "e002"))
            loaded = SNAPSHOTS.load_validation_snapshot(root)
            report = REPORT.compose_snapshot_report(loaded)
            shown = QUERIES.show_validation(root)["rows"][0]
            findings = QUERIES.list_findings(root, limit=100)
            batches = QUERIES.list_batches(root, limit=100)
            detail = QUERIES.batch_detail(
                root,
                snapshot.batches[0].batch_id,
                section="findings",
                limit=100,
            )

        self.assertEqual(
            sum(shown["finding_counts_by_type"].values()),
            len(snapshot.findings),
        )
        self.assertEqual(shown["batch_count"], len(snapshot.batches))
        self.assertEqual(
            {item["finding_id"] for item in findings["items"]},
            {finding.finding_id for finding in snapshot.findings},
        )
        self.assertEqual(
            {item["batch_id"] for item in batches["items"]},
            {batch.batch_id for batch in snapshot.batches},
        )
        self.assertEqual(
            {item["finding_id"] for item in detail["items"]},
            set(snapshot.batches[0].finding_ids),
        )
        for finding in snapshot.findings:
            self.assertIn(f"- Finding: `{finding.finding_id}`", report)
        for batch in snapshot.batches:
            self.assertIn(f"### {batch.batch_id}", report)

    def test_finding_cursor_is_complete_and_snapshot_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _synthetic_snapshot(root, 3)
            _publish(root, snapshot)
            first = QUERIES.list_findings(root, limit=1)
            identities = [first["items"][0]["finding_id"]]
            cursor = first["next_cursor"]
            while cursor is not None:
                page = QUERIES.list_findings(root, limit=1, cursor=cursor)
                identities.extend(item["finding_id"] for item in page["items"])
                cursor = page.get("next_cursor")

            self.assertEqual(
                identities,
                [item.finding_id for item in snapshot.findings],
            )
            SNAPSHOTS.publish_validation_snapshot(
                SNAPSHOTS.SnapshotPublicationRequest(root, snapshot, {})
            )
            with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                QUERIES.list_findings(
                    root,
                    limit=1,
                    cursor=first["next_cursor"],
                )
            self.assertEqual(raised.exception.code, "validation.cursor.invalid")

    def test_list_pagination_is_complete_at_one_default_and_one_hundred(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _synthetic_snapshot(root, 105)
            _publish(root, snapshot)

            for limit in (1, QUERIES.DEFAULT_PAGE_SIZE, 100):
                with self.subTest(limit=limit):
                    finding_ids: list[str] = []
                    batch_ids: list[str] = []
                    finding_cursor = None
                    batch_cursor = None
                    while True:
                        finding_page = QUERIES.list_findings(
                            root, limit=limit, cursor=finding_cursor
                        )
                        finding_ids.extend(
                            item["finding_id"] for item in finding_page["items"]
                        )
                        self.assertLessEqual(
                            len(json.dumps(finding_page).encode()),
                            QUERIES.MAX_RESPONSE_BYTES,
                        )
                        finding_cursor = finding_page.get("next_cursor")
                        if finding_cursor is None:
                            break
                    while True:
                        batch_page = QUERIES.list_batches(
                            root, limit=limit, cursor=batch_cursor
                        )
                        batch_ids.extend(
                            item["batch_id"] for item in batch_page["items"]
                        )
                        self.assertLessEqual(
                            len(json.dumps(batch_page).encode()),
                            QUERIES.MAX_RESPONSE_BYTES,
                        )
                        batch_cursor = batch_page.get("next_cursor")
                        if batch_cursor is None:
                            break
                    self.assertEqual(
                        finding_ids,
                        [finding.finding_id for finding in snapshot.findings],
                    )
                    self.assertEqual(
                        batch_ids,
                        [batch.batch_id for batch in snapshot.batches],
                    )
                    self.assertEqual(len(finding_ids), len(set(finding_ids)))
                    self.assertEqual(len(batch_ids), len(set(batch_ids)))

    def test_cursors_bind_action_filter_entry_and_physical_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            first_root = workspace / "first"
            second_root = workspace / "second"
            first_root.mkdir()
            second_root.mkdir()
            snapshot = _synthetic_snapshot(first_root, 8)
            _publish(first_root, snapshot)
            _publish(second_root, snapshot)
            cursor = QUERIES.list_findings(
                first_root,
                finding_type="conformance",
                entry="e001",
                limit=1,
            )["next_cursor"]
            assert isinstance(cursor, str)

            invalid_queries = (
                ("type", lambda: QUERIES.list_findings(
                    first_root,
                    finding_type="evidence",
                    entry="e001",
                    limit=1,
                    cursor=cursor,
                )),
                ("entry", lambda: QUERIES.list_findings(
                    first_root,
                    finding_type="conformance",
                    limit=1,
                    cursor=cursor,
                )),
                (
                    "action",
                    lambda: QUERIES.list_batches(
                        first_root, limit=1, cursor=cursor
                    ),
                ),
                ("log", lambda: QUERIES.list_findings(
                    second_root,
                    finding_type="conformance",
                    entry="e001",
                    limit=1,
                    cursor=cursor,
                )),
            )
            for case, query in invalid_queries:
                with self.subTest(case=case):
                    with self.assertRaises(
                        QUERIES.ValidationQueryError
                    ) as raised:
                        query()
                    self.assertEqual(
                        raised.exception.code, "validation.cursor.invalid"
                    )

            for malformed in ("not-base64", "e30=", "eyJsYXN0Ijp0cnVlfQ=="):
                with self.subTest(cursor=malformed):
                    with self.assertRaises(
                        QUERIES.ValidationQueryError
                    ) as raised:
                        QUERIES.list_findings(first_root, cursor=malformed)
                    self.assertEqual(
                        raised.exception.code, "validation.cursor.invalid"
                    )

    def test_finding_type_filter_changes_only_the_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _synthetic_snapshot(root, 12)
            _publish(root, snapshot)

            all_batches = QUERIES.list_batches(root, limit=100)
            evidence = QUERIES.list_findings(
                root, finding_type="evidence", limit=100
            )

        self.assertEqual(all_batches["total"], len(snapshot.batches))
        self.assertEqual(
            {item["type"] for item in evidence["items"]},
            {"evidence"},
        )
        self.assertEqual(
            evidence["total"],
            sum(
                finding.type is DOMAIN.RuleArea.EVIDENCE
                for finding in snapshot.findings
            ),
        )

    def test_detail_is_sectioned_and_needs_no_research_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, snapshot = _published(Path(directory))
            finding = snapshot.findings[0]
            batch = snapshot.batches[0]
            root.with_suffix(".md").unlink()

            finding_value = QUERIES.finding_detail(
                root,
                finding.finding_id,
                section="nodes",
                limit=100,
            )
            batch_value = QUERIES.batch_detail(
                root,
                batch.batch_id,
                section="findings",
                limit=100,
            )

        self.assertEqual(finding_value["finding"]["finding_id"], finding.finding_id)
        presentation = snapshot.report_context["presentations"][finding.code]
        self.assertEqual(
            finding_value["finding"]["diagnosis"],
            {
                "explanation": presentation["sentence"],
                "title": presentation["name"],
            },
        )
        self.assertEqual(finding_value["batch"]["batch_id"], batch.batch_id)
        self.assertEqual(
            batch_value["membership_counts"]["findings"],
            len(batch.finding_ids),
        )
        self.assertEqual(
            {item["finding_id"] for item in batch_value["items"]},
            set(batch.finding_ids),
        )

    def test_detail_pages_complete_repair_context_and_excludes_unrelated_edges(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            snapshot = _repair_snapshot(root)
            _publish(root, snapshot, evaluated=("e001", "e002", "e003"))
            finding = snapshot.findings[0]
            batch = snapshot.batches[0]

            first_nodes = QUERIES.finding_detail(
                root,
                finding.finding_id,
                entry="e001",
                section="nodes",
                limit=1,
            )
            second_nodes = QUERIES.finding_detail(
                root,
                finding.finding_id,
                entry="e001",
                section="nodes",
                limit=1,
                cursor=first_nodes["next_cursor"],
            )
            with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                QUERIES.finding_detail(
                    root,
                    finding.finding_id,
                    section="nodes",
                    limit=1,
                    cursor=first_nodes["next_cursor"],
                )
            self.assertEqual(raised.exception.code, "validation.cursor.invalid")

            relationships = QUERIES.batch_detail(
                root, batch.batch_id, section="relationships", limit=100
            )
            ambiguities = QUERIES.batch_detail(
                root, batch.batch_id, section="ambiguities", limit=100
            )

        self.assertEqual(first_nodes["section_total"], 2)
        self.assertEqual(
            len(first_nodes["items"]) + len(second_nodes["items"]),
            2,
        )
        self.assertEqual(relationships["section_total"], 2)
        self.assertEqual(
            {item["kind"] for item in relationships["items"]},
            {"production", "consumption"},
        )
        self.assertNotIn("e003:other", str(relationships))
        self.assertEqual(ambiguities["section_total"], 1)
        self.assertEqual(len(ambiguities["items"][0]["candidates"]), 2)
        self.assertEqual(
            relationships["membership_counts"],
            {
                "ambiguities": 1,
                "findings": 2,
                "nodes": 3,
                "relationships": 2,
                "repair_keys": 1,
            },
        )

    def test_detail_default_skips_empty_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            node = DOMAIN.GraphReference("material", "data/output.csv", "e001")
            check = DOMAIN.RuleCheck(
                "evidence:output",
                DOMAIN.RuleArea.EVIDENCE,
                DOMAIN.CheckOutcome.FINDING,
                "data/output.csv",
                diagnostic=DOMAIN.CheckDiagnostic(
                    "evidence.missing",
                    "data/output.csv",
                    "Evidence output rule",
                    {"present": False},
                ),
                issue_context=DOMAIN.IssueContext(
                    entry="e001",
                    context_nodes=(node,),
                ),
            )
            attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.LOG,
                    root.with_suffix(".md").as_posix(),
                ),
                source_identity="default-section-source",
                rules_version="rules",
                started_at="2026-09-14T00:00:00Z",
                finished_at="2026-09-14T00:00:01Z",
                checks=(check,),
            )
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                attempt,
                repair_context={
                    "ambiguities": [],
                    "nodes": [
                        {
                            "attributes": {},
                            "entry": "e001",
                            "identity": node.identity,
                            "kind": node.kind,
                            "node_id": node.node_id,
                        }
                    ],
                    "relationships": [],
                },
                report_context={
                    "entries": {},
                    "presentations": {
                        "evidence.missing": {
                            "name": "Missing evidence",
                            "sentence": "The evidence is missing.",
                            "target_kind": "record",
                        }
                    },
                    "title": "Study",
                },
            )
            _publish(root, snapshot)

            detail = QUERIES.finding_detail(root, snapshot.findings[0].finding_id)

        self.assertEqual(detail["section"], "nodes")
        self.assertEqual(detail["section_total"], 1)

    def test_finding_detail_includes_repair_key_owner_and_incident_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            command = DOMAIN.GraphReference("command", "e001:build", "e001")
            output = DOMAIN.GraphReference("material", "data/output.csv", "e001")
            key = DOMAIN.RepairKey(
                DOMAIN.RepairKeyKind.COMMAND, command.identity, "e001"
            )
            check = DOMAIN.RuleCheck(
                "conformance:build",
                DOMAIN.RuleArea.CONFORMANCE,
                DOMAIN.CheckOutcome.FINDING,
                "build command",
                diagnostic=DOMAIN.CheckDiagnostic(
                    "command.invalid",
                    "build command",
                    "Command contract",
                    {"exit": 2},
                ),
                issue_context=DOMAIN.IssueContext(
                    entry="e001",
                    repair_keys=(key,),
                ),
            )
            attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.LOG,
                    root.with_suffix(".md").as_posix(),
                ),
                source_identity="repair-key-owner-source",
                rules_version="rules",
                started_at="2026-09-14T00:00:00Z",
                finished_at="2026-09-14T00:00:01Z",
                checks=(check,),
            )
            snapshot = DOMAIN.ValidationSnapshot.from_attempt(
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
                        for node in (command, output)
                    ],
                    "relationships": [
                        {
                            "kind": "production",
                            "source": command.node_id,
                            "target": output.node_id,
                        }
                    ],
                },
                report_context={
                    "entries": {},
                    "presentations": {
                        "command.invalid": {
                            "name": "Invalid command",
                            "sentence": "The command is invalid.",
                            "target_kind": "command",
                        }
                    },
                    "title": "Study",
                },
            )
            _publish(root, snapshot)

            nodes = QUERIES.finding_detail(
                root, snapshot.findings[0].finding_id, section="nodes"
            )
            relationships = QUERIES.finding_detail(
                root, snapshot.findings[0].finding_id, section="relationships"
            )
            loaded = SNAPSHOTS.load_validation_snapshot(root)

        self.assertEqual(
            [item["node_id"] for item in nodes["items"]],
            [command.node_id],
        )
        self.assertEqual(relationships["section_total"], 1)
        self.assertEqual(loaded.findings[0].context_nodes, ())
        self.assertEqual(loaded.findings[0].repair_keys, (key,))

    def test_entry_selection_prefers_only_a_newer_entry_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            full = _synthetic_snapshot(root, 4, entry="e001")
            _publish(root, full, evaluated=("e001", "e002"))
            entry_attempt = DOMAIN.ValidationAttempt.build(
                target=DOMAIN.ValidationTarget(
                    DOMAIN.TargetKind.ENTRY,
                    root.with_suffix(".md").as_posix(),
                    "e001",
                ),
                source_identity="entry-source",
                rules_version="rules",
                started_at="2026-09-14T00:00:02Z",
                finished_at="2026-09-14T00:00:03Z",
                checks=(
                    DOMAIN.RuleCheck(
                        "entry:e001:finding",
                        DOMAIN.RuleArea.EVIDENCE,
                        DOMAIN.CheckOutcome.FINDING,
                        "entry-only",
                        diagnostic=DOMAIN.CheckDiagnostic(
                            "entry.invalid", "entry-only", "Entry rule", {"bad": True}
                        ),
                        issue_context=DOMAIN.IssueContext(entry="e001"),
                    ),
                ),
            )
            entry_snapshot = DOMAIN.ValidationSnapshot.from_attempt(
                entry_attempt,
                report_context={
                    "entries": {},
                    "presentations": {
                        "entry.invalid": {
                            "name": "Invalid entry",
                            "sentence": "The entry is invalid.",
                            "target_kind": "entry",
                        }
                    },
                    "title": "Study",
                },
            )
            _publish(root, entry_snapshot)

            preferred = QUERIES.list_findings(root, entry="e001", limit=100)
            fallback = QUERIES.list_findings(root, entry="e002", limit=100)
            with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                QUERIES.list_findings(root, entry="missing")
            self.assertEqual(raised.exception.code, "validation.entry.missing")

            _publish(root, full, evaluated=("e001", "e002"))
            superseded = QUERIES.list_findings(root, entry="e001", limit=100)

        self.assertEqual(preferred["selected_target"]["kind"], "entry")
        self.assertEqual(preferred["total"], 1)
        self.assertEqual(fallback["selected_target"]["kind"], "log")
        self.assertEqual(superseded["selected_target"]["kind"], "log")
        self.assertEqual(superseded["total"], len(full.findings))

    def test_missing_and_old_projection_have_distinct_show_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            missing = QUERIES.show_validation(root)
            self.assertEqual(missing["rows"][0]["outcome"], "Not validated")
            self.assertNotIn("replacement_required", missing["rows"][0])

            path = STORE.result_store_path(root)
            path.parent.mkdir()
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version=17")
            old = QUERIES.show_validation(root)
            self.assertTrue(old["rows"][0]["replacement_required"])
            command = f"log validate run --path {root}"
            self.assertEqual(old["rows"][0]["next_command"], command)
            for query in (
                lambda: QUERIES.list_findings(root),
                lambda: QUERIES.list_batches(root),
                lambda: QUERIES.list_blocked(root),
                lambda: QUERIES.list_failed(root),
                lambda: QUERIES.finding_detail(root, "finding"),
                lambda: QUERIES.batch_detail(root, "batch"),
            ):
                with self.subTest(query=query), self.assertRaises(
                    QUERIES.ValidationQueryError
                ) as raised:
                    query()
                self.assertEqual(
                    raised.exception.code,
                    "validation.replacement_required",
                )
                self.assertIn(command, str(raised.exception))

    def test_limits_and_root_failure_isolation_are_explicit(self) -> None:
        for limit in (0, 101):
            with self.subTest(limit=limit), self.assertRaises(
                QUERIES.ValidationQueryError
            ) as raised:
                QUERIES.list_batches(Path("unused"), limit=limit)
            self.assertEqual(raised.exception.code, "validation.limit.invalid")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            missing = workspace / "missing"
            malformed = workspace / "malformed"
            missing.mkdir()
            malformed.mkdir()
            path = STORE.result_store_path(malformed)
            path.parent.mkdir()
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version=99")

            value = QUERIES.show_validation_root((missing, malformed))

        self.assertEqual(
            [row["outcome"] for row in value["rows"]],
            ["Not validated", "Error"],
        )
        self.assertFalse(any("blocked_check_count" in row for row in value["rows"]))
        self.assertEqual(value["blocked_check_total"], 0)
        self.assertEqual(value["failed_check_total"], 0)
        self.assertEqual(value["contributing_log_count"], 0)
        self.assertTrue(value["totals_partial"])

    def test_public_query_failures_have_stable_validation_codes(self) -> None:
        self.assertEqual(
            QUERIES._validation_store_code("unexpected.store.failure"),
            "validation.store.unavailable",
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root, snapshot = _published(workspace)

            cases = (
                (
                    "type",
                    "validation.type.invalid",
                    lambda: QUERIES.list_findings(root, finding_type="unknown"),
                ),
                (
                    "finding section",
                    "validation.section.invalid",
                    lambda: QUERIES.finding_detail(
                        root,
                        snapshot.findings[0].finding_id,
                        section="unknown",
                    ),
                ),
                (
                    "batch section",
                    "validation.section.invalid",
                    lambda: QUERIES.batch_detail(
                        root,
                        snapshot.batches[0].batch_id,
                        section="unknown",
                    ),
                ),
                (
                    "batch identity",
                    "validation.batch.missing",
                    lambda: QUERIES.batch_detail(root, "missing-batch"),
                ),
            )
            for case, code, query in cases:
                with self.subTest(case=case), self.assertRaises(
                    QUERIES.ValidationQueryError
                ) as raised:
                    query()
                self.assertEqual(raised.exception.code, code)

            with mock.patch.object(QUERIES, "MAX_RESPONSE_BYTES", 1):
                with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                    QUERIES.show_validation(root)
            self.assertEqual(
                raised.exception.code,
                "validation.response.too_large",
            )

            empty_root = workspace / "empty"
            empty_root.mkdir()
            with STORE.result_transaction(empty_root):
                pass
            with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                QUERIES.list_findings(empty_root)
            self.assertEqual(
                raised.exception.code,
                "validation.not_validated",
            )

    def test_finding_detail_rejects_corrupt_diagnostic_and_presentation_rows(
        self,
    ) -> None:
        corruptions = ("diagnostic-array", "empty-title", "missing-target-kind")
        for corruption in corruptions:
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                root, snapshot = _published(Path(directory))
                finding_id = snapshot.findings[0].finding_id
                with STORE.result_transaction(root) as db:
                    if corruption == "diagnostic-array":
                        db.execute(
                            "UPDATE validation_findings SET observed_json='[]' "
                            "WHERE position=0"
                        )
                    else:
                        row = db.execute(
                            "SELECT report_context_json FROM validation_snapshots "
                            "WHERE slot='full'"
                        ).fetchone()
                        context = json.loads(row[0])
                        presentation = context["presentations"][
                            snapshot.findings[0].code
                        ]
                        if corruption == "empty-title":
                            presentation["name"] = ""
                        else:
                            del presentation["target_kind"]
                        db.execute(
                            "UPDATE validation_snapshots SET report_context_json=? "
                            "WHERE slot='full'",
                            (json.dumps(context),),
                        )

                with self.assertRaises(QUERIES.ValidationQueryError) as raised:
                    QUERIES.finding_detail(root, finding_id)
                self.assertEqual(
                    raised.exception.code,
                    "validation.store.malformed",
                )

    def test_root_show_isolates_busy_malformed_and_unsupported_stores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            missing = workspace / "missing"
            busy = workspace / "busy"
            malformed = workspace / "malformed"
            unsupported = workspace / "unsupported"
            for root in (missing, busy, malformed, unsupported):
                root.mkdir()
            with STORE.result_transaction(busy):
                pass
            malformed_path = STORE.result_store_path(malformed)
            malformed_path.parent.mkdir()
            malformed_path.write_bytes(b"not sqlite")
            unsupported_path = STORE.result_store_path(unsupported)
            unsupported_path.parent.mkdir()
            with sqlite3.connect(unsupported_path) as db:
                db.execute("PRAGMA user_version=99")

            lock = sqlite3.connect(STORE.result_store_path(busy), timeout=0)
            lock.execute("BEGIN EXCLUSIVE")
            try:
                value = QUERIES.show_validation_root(
                    (missing, busy, malformed, unsupported)
                )
            finally:
                lock.rollback()
                lock.close()

        self.assertEqual(
            [row["outcome"] for row in value["rows"]],
            ["Not validated", "Error", "Error", "Error"],
        )
        self.assertEqual(
            {row["error"]["code"] for row in value["rows"] if "error" in row},
            {
                "validation.store.busy",
                "validation.store.malformed",
                "validation.schema.unsupported",
            },
        )


if __name__ == "__main__":
    unittest.main()
