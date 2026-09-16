from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_saved_report import render_saved_report
from log_commands.reproduction_saved_storage import publish_saved_run
from log_commands.validation_cli import render_validation
from research_log_result_store import result_snapshot
from research_log_validation_test_support import mechanical_log
from test_reproduction_canonical_records import mixed_run
from validation.controller import ValidationRequest, validate
from validation.domain import (
    CheckDiagnostic,
    CheckOutcome,
    RuleArea,
    RuleCheck,
    TargetKind,
    ValidationAttempt,
    ValidationSnapshot,
    ValidationTarget,
)
from validation.snapshot_report import SnapshotReportError, compose_snapshot_report
from validation.snapshot_storage import (
    SnapshotPublicationRequest,
    publish_validation_snapshot,
)


class ReportRenderRecoveryTests(unittest.TestCase):
    def test_validation_initial_and_recovery_reports_are_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = mechanical_log(Path(directory))
            (entry.parent / "data/results.csv").write_text(
                "success_rate\n0.5\n", encoding="utf-8"
            )
            validate(ValidationRequest(summary))
            root = summary.with_suffix("")
            report = root / "validation.md"
            initial = report.read_bytes()
            report.write_text("stale report\n", encoding="utf-8")

            render_validation(SimpleNamespace(root=root, summary=summary))

            self.assertEqual(report.read_bytes(), initial)

    def test_validation_render_rejects_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "log"
            root.mkdir()
            summary = root / "study.md"
            summary.write_text("# Study\n", encoding="utf-8")
            _publish_clear_snapshot(root, summary)
            target = workspace / "outside.md"
            target.write_text("outside\n", encoding="utf-8")
            report = root / "validation.md"
            report.symlink_to(target)

            with self.assertRaises(ActionError) as raised:
                render_validation(SimpleNamespace(root=root, summary=summary))

            self.assertEqual(raised.exception.code, "validation.report.write_failed")
            self.assertTrue(report.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "outside\n")

    def test_validation_report_rejects_unknown_presentation_code(self) -> None:
        attempt = ValidationAttempt.build(
            target=ValidationTarget(TargetKind.LOG, "study.md"),
            source_identity="source",
            rules_version="rules",
            started_at="2030-01-01T00:00:00Z",
            finished_at="2030-01-01T00:00:01Z",
            checks=(
                RuleCheck(
                    "finding",
                    RuleArea.CONFORMANCE,
                    CheckOutcome.FINDING,
                    "subject",
                    diagnostic=CheckDiagnostic(
                        "unknown.code", "subject", "Rule", {"invalid": True}
                    ),
                ),
            ),
        )
        snapshot = ValidationSnapshot.from_attempt(
            attempt,
            report_context={
                "entries": {},
                "presentations": {},
                "title": "Study",
            },
        )

        with self.assertRaisesRegex(SnapshotReportError, "missing finding codes"):
            compose_snapshot_report(snapshot)

    def test_render_failure_preserves_prior_report_and_leaves_marker_stale(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            report = root / "reproduction.md"
            report.write_text("prior report\n", encoding="utf-8")
            _reproduction_result(root)
            log = LogContext(root.with_suffix(".md"), root)

            with (
                mock.patch(
                    "log_commands.reproduction_saved_report.compose_saved_report",
                    return_value="new report\n",
                ),
                mock.patch(
                    "log_commands.reproduction_saved_report.atomic_write_texts",
                    side_effect=OSError("injected write failure"),
                ),
                self.assertRaisesRegex(ActionError, "injected write failure") as raised,
            ):
                render_saved_report(log)

            self.assertEqual(report.read_text(encoding="utf-8"), "prior report\n")
            self.assertFalse(_has_marker(root))
            self.assertEqual(raised.exception.code, "results.report.write_failed")

    def test_retry_writes_report_and_records_marker_without_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            _reproduction_result(root)
            log = LogContext(root.with_suffix(".md"), root)

            with mock.patch(
                "log_commands.reproduction_saved_report.compose_saved_report",
                return_value="rendered only\n",
            ) as report:
                render_saved_report(log)

            self.assertEqual(
                (root / "reproduction.md").read_text(encoding="utf-8"),
                "rendered only\n",
            )
            self.assertTrue(_has_marker(root))
            report.assert_called_once()
            self.assertEqual(report.call_args.args[0].run, _saved_result(root))

    def test_reproduction_render_uses_saved_facts_without_live_composition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            _reproduction_result(root)
            log = LogContext(root.with_suffix(".md"), root)

            with (
                mock.patch(
                    "log_commands.reproduction_planner.plan_reproduction_work",
                    side_effect=AssertionError("must not replan current sources"),
                ),
                mock.patch(
                    "log_commands.reproduction_work_supervision.execute_work_plan",
                    side_effect=AssertionError("must not reproduce"),
                ),
            ):
                report = render_saved_report(log)

            self.assertIn("Commands — 7", report)
            self.assertIn("Artifacts — 4", report)
            self.assertIn("Previous failure — 1", report)
            self.assertEqual((root / "reproduction.md").read_text(), report)
            self.assertTrue(_has_marker(root))

    def test_validation_render_uses_the_stored_projection_after_source_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            summary = root / "study.md"
            summary.write_text("# Original source\n", encoding="utf-8")
            attempt = ValidationAttempt.build(
                target=ValidationTarget(TargetKind.LOG, summary.as_posix()),
                source_identity="original-source",
                rules_version="rules",
                started_at="2030-01-01T00:00:00Z",
                finished_at="2030-01-01T00:00:01Z",
                checks=(
                    RuleCheck(
                        "conformance:summary",
                        RuleArea.CONFORMANCE,
                        CheckOutcome.PASS,
                        summary.as_posix(),
                    ),
                ),
            )
            publish_validation_snapshot(
                SnapshotPublicationRequest(
                    root,
                    ValidationSnapshot.from_attempt(
                        attempt,
                        report_context={
                            "entries": {},
                            "presentations": {},
                            "title": "Original source",
                        },
                    ),
                    {},
                )
            )
            summary.write_text("# Changed source\n", encoding="utf-8")
            log = SimpleNamespace(root=root, summary=summary)

            with mock.patch(
                "validation.engine.evaluate_mechanical",
                side_effect=AssertionError("must not evaluate current source"),
            ):
                render_validation(log)

            self.assertIn(
                "| Outcome | Clear |",
                (root / "validation.md").read_text(encoding="utf-8"),
            )
            self.assertTrue(_has_marker(root, "validation"))


def _reproduction_result(root: Path) -> None:
    publish_saved_run(root, _saved_result(root))


def _saved_result(root: Path):
    run = mixed_run()
    return replace(
        run,
        summary=str(root.with_suffix(".md")),
        commands=tuple(
            replace(command, project_root=str(root.parent)) for command in run.commands
        ),
    )


def _publish_clear_snapshot(root: Path, summary: Path) -> None:
    attempt = ValidationAttempt.build(
        target=ValidationTarget(TargetKind.LOG, summary.as_posix()),
        source_identity="source",
        rules_version="rules",
        started_at="2030-01-01T00:00:00Z",
        finished_at="2030-01-01T00:00:01Z",
        checks=(
            RuleCheck(
                "conformance:summary",
                RuleArea.CONFORMANCE,
                CheckOutcome.PASS,
                summary.as_posix(),
            ),
        ),
    )
    publish_validation_snapshot(
        SnapshotPublicationRequest(
            root,
            ValidationSnapshot.from_attempt(
                attempt,
                report_context={
                    "entries": {},
                    "presentations": {},
                    "title": "Study",
                },
            ),
            {},
        )
    )


def _has_marker(root: Path, kind: str = "reproduction") -> bool:
    with result_snapshot(root) as db:
        return (
            db.execute(
                "SELECT 1 FROM report_materializations WHERE kind=?", (kind,)
            ).fetchone()
            is not None
        )
