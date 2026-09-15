from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_report_render import render_reproduction_report
from log_commands.validation_cli import render_validation
from research_log_result_store import result_snapshot, result_transaction
from research_log_result_store import results_lock as store_results_lock
from research_log_validation_test_support import mechanical_log
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
            log = SimpleNamespace(root=root, summary=root / "study.md")

            with (
                mock.patch(
                    "log_commands.reproduction_report_render."
                    "compose_reproduction_render_input",
                    return_value="new report\n",
                ),
                mock.patch(
                    "log_commands.reproduction_report_render.atomic_write_texts",
                    side_effect=OSError("injected write failure"),
                ),
                self.assertRaisesRegex(ActionError, "injected write failure") as raised,
            ):
                render_reproduction_report(log)

            self.assertEqual(report.read_text(encoding="utf-8"), "prior report\n")
            self.assertFalse(_has_marker(root))
            self.assertEqual(raised.exception.code, "reproduction.report.write_failed")

    def test_retry_writes_report_and_records_marker_without_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            _reproduction_result(root)
            log = SimpleNamespace(root=root, summary=root / "study.md")

            with mock.patch(
                "log_commands.reproduction_report_render."
                "compose_reproduction_render_input",
                return_value="rendered only\n",
            ) as report:
                render_reproduction_report(log)

            self.assertEqual(
                (root / "reproduction.md").read_text(encoding="utf-8"),
                "rendered only\n",
            )
            self.assertTrue(_has_marker(root))
            report.assert_called_once_with(log)

    def test_reproduction_source_composition_precedes_results_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            _reproduction_result(root)
            log = SimpleNamespace(root=root, summary=root / "study.md")
            active = False

            @contextmanager
            def observed_lock(path: Path):
                nonlocal active
                with store_results_lock(path):
                    active = True
                    try:
                        yield
                    finally:
                        active = False

            def compose_source_inputs(_log: object) -> str:
                self.assertFalse(active)
                return "source-stable report\n"

            with (
                mock.patch(
                    "log_commands.reproduction_report_render.results_lock",
                    observed_lock,
                ),
                mock.patch(
                    "log_commands.reproduction_report_render."
                    "compose_reproduction_render_input",
                    side_effect=compose_source_inputs,
                ),
            ):
                render_reproduction_report(log)

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
    with result_transaction(root) as db:
        db.execute("INSERT INTO store_state VALUES ('reproduction', 1, 'study.md')")


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
