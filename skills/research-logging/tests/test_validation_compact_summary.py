"""Exact presentation and saved-authority tests for compact validation summaries."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from log_commands.validation_cli import _render_show
from validation.domain import (
    CheckDiagnostic,
    CheckOutcome,
    FailureOperation,
    RuleArea,
    RuleCheck,
    TargetKind,
    ValidationAttempt,
    ValidationSnapshot,
    ValidationTarget,
)
from validation.read_model import show_validation
from validation.snapshot_report import compose_snapshot_report
from validation.snapshot_storage import (
    SnapshotPublicationRequest,
    load_validation_snapshot,
    publish_validation_snapshot,
)


class CompactValidationSummaryTests(unittest.TestCase):
    def test_saved_report_is_exact_summary_after_source_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            summary = root.with_suffix(".md")
            summary.write_text("# Study\n", encoding="utf-8")
            for mixed in (False, True):
                with self.subTest(mixed=mixed):
                    checks = (
                        ()
                        if not mixed
                        else (
                            RuleCheck(
                                "finding",
                                RuleArea.CONFORMANCE,
                                CheckOutcome.FINDING,
                                "command",
                                diagnostic=CheckDiagnostic(
                                    "command.invalid",
                                    "command",
                                    "Command",
                                    {"error": "invalid"},
                                ),
                            ),
                            RuleCheck(
                                "blocked",
                                RuleArea.EVIDENCE,
                                CheckOutcome.BLOCKED,
                                "evidence",
                                dependencies=("finding",),
                            ),
                            RuleCheck(
                                "failed",
                                RuleArea.PROVENANCE,
                                CheckOutcome.FAILED,
                                "resource",
                                diagnostic=CheckDiagnostic(
                                    "read.failed",
                                    "resource",
                                    "Read",
                                    {"error": "unreadable"},
                                ),
                                failure_operation=FailureOperation.READ,
                            ),
                        )
                    )
                    snapshot = ValidationSnapshot.from_attempt(
                        ValidationAttempt.build(
                            target=ValidationTarget(TargetKind.LOG, str(summary)),
                            source_identity="source",
                            rules_version="rules",
                            started_at="2030-01-01T00:00:00Z",
                            finished_at="2030-01-01T00:00:01Z",
                            checks=checks,
                        ),
                        report_context={
                            "entries": {},
                            "title": "Study",
                            "presentations": {}
                            if not mixed
                            else {
                                "command.invalid": {
                                    "name": "Invalid command",
                                    "sentence": "Invalid recipe.",
                                    "target_kind": "command",
                                },
                            },
                        },
                    )
                    with mock.patch("validation.snapshot_storage.datetime") as clock:
                        clock.now.return_value = datetime(
                            2030, 1, 2, tzinfo=timezone.utc
                        )
                        publish_validation_snapshot(
                            SnapshotPublicationRequest(root, snapshot, {})
                        )
                    saved = load_validation_snapshot(root)
                    expected = (
                        "| Field | Value |\n| --- | --- |\n"
                        f"| Log | {root} |\n| Saved | Jan 2 |\n"
                        f"| Outcome | {'Failed' if mixed else 'Clear'} |\n"
                        f"| Conformance | {int(mixed)} |\n| Evidence | 0 |\n"
                        "| Provenance | 0 |\n| Orphans | 0 |\n"
                        f"| Batches | {int(mixed)} |\n| Blocked | {int(mixed)} |\n"
                        f"| Failed | {int(mixed)} |"
                    )
                    self.assertEqual(
                        compose_snapshot_report(saved),
                        "# Validation\n\n" + expected + "\n",
                    )
                    self.assertEqual(_render_show(show_validation(root)), expected)
            summary.unlink()
            self.assertEqual(
                compose_snapshot_report(load_validation_snapshot(root)),
                "# Validation\n\n" + expected + "\n",
            )

    def test_clear_and_mixed_summaries_have_exact_vertical_rows(self) -> None:
        for outcome, counts, batches, blocked, failed in (
            ("Clear", (0, 0, 0, 0), 0, 0, 0),
            ("Failed", (2, 3, 4, 5), 6, 7, 8),
        ):
            with self.subTest(outcome=outcome):
                row = {
                    "log": "/workspace/research/MASTSEL",
                    "saved_at": "2030-01-02T01:00:00+02:00",
                    "outcome": outcome,
                    "finding_counts_by_type": dict(
                        zip(("conformance", "evidence", "provenance", "orphan"), counts)
                    ),
                    "batch_count": batches,
                    "blocked_check_count": blocked,
                    "failed_check_count": failed,
                }
                expected = (
                    "| Field | Value |\n| --- | --- |\n"
                    "| Log | /workspace/research/MASTSEL |\n"
                    "| Saved | Jan 1 |\n"
                    f"| Outcome | {outcome} |\n"
                    f"| Conformance | {counts[0]} |\n"
                    f"| Evidence | {counts[1]} |\n"
                    f"| Provenance | {counts[2]} |\n"
                    f"| Orphans | {counts[3]} |\n"
                    f"| Batches | {batches} |\n"
                    f"| Blocked | {blocked} |\n"
                    f"| Failed | {failed} |"
                )
                self.assertEqual(_render_show({"rows": [row]}), expected)

    def test_missing_summary_does_not_invent_counts(self) -> None:
        self.assertEqual(
            _render_show(
                {"rows": [{"log": "/workspace/study", "outcome": "Not validated"}]}
            ),
            "| Field | Value |\n| --- | --- |\n"
            "| Log | /workspace/study |\n| Saved | — |\n"
            "| Outcome | Not Validated |\n| Conformance | — |\n"
            "| Evidence | — |\n| Provenance | — |\n| Orphans | — |\n"
            "| Batches | — |\n| Blocked | — |\n| Failed | — |",
        )
