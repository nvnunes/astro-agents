from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from log_commands.inspection_cli import _render
from log_commands.model import ActionError
from research_log_result_store import result_snapshot, result_transaction
from research_log_result_store import results_lock as store_results_lock
from validation.human_projection import ReportContext
from validation.mechanical_results import MechanicalGeneratedRecord
from validation.result_storage import StoredValidationResult


class ResultRenderRecoveryTests(unittest.TestCase):
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
                    "log_commands.inspection_cli._reproduction_render_input",
                    return_value="new report\n",
                ),
                mock.patch(
                    "log_commands.storage.atomic_write_texts",
                    side_effect=OSError("injected write failure"),
                ),
                self.assertRaisesRegex(ActionError, "injected write failure") as raised,
            ):
                _render(log, "reproduction")

            self.assertEqual(report.read_text(encoding="utf-8"), "prior report\n")
            self.assertFalse(_has_marker(root))
            self.assertEqual(raised.exception.code, "results.report.write_failed")

    def test_retry_writes_report_and_records_marker_without_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            _reproduction_result(root)
            log = SimpleNamespace(root=root, summary=root / "study.md")

            with mock.patch(
                "log_commands.inspection_cli._reproduction_render_input",
                return_value="rendered only\n",
            ) as report:
                _render(log, "reproduction")

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
                    "research_log_result_store.results_lock", observed_lock
                ),
                mock.patch(
                    "log_commands.inspection_cli._reproduction_render_input",
                    side_effect=compose_source_inputs,
                ),
            ):
                _render(log, "reproduction")

            self.assertTrue(_has_marker(root))

    def test_validation_render_uses_the_stored_projection_after_source_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            root.mkdir()
            summary = root / "study.md"
            summary.write_text("# Changed source\n", encoding="utf-8")
            with result_transaction(root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES ('validation', 1, 'study.md')"
                )
            projection = SimpleNamespace(
                stored=StoredValidationResult("stored", "validation", 1),
                record=MechanicalGeneratedRecord.build(
                    "study.md", "rules", "2030-01-01", ()
                ),
                context=ReportContext.empty(summary),
                groups=(),
            )
            log = SimpleNamespace(root=root, summary=summary)

            with (
                mock.patch(
                    "validation.result_storage.load_validation_report_projection",
                    return_value=projection,
                ),
                mock.patch(
                    "validation.human_projection.load_report_context",
                    side_effect=AssertionError("must not read current source"),
                ),
            ):
                _render(log, "validation")

            self.assertIn(
                "No mechanical findings.",
                (root / "validation.md").read_text(encoding="utf-8"),
            )
            self.assertTrue(_has_marker(root, "validation"))


def _reproduction_result(root: Path) -> None:
    with result_transaction(root) as db:
        db.execute("INSERT INTO store_state VALUES ('reproduction', 1, 'study.md')")


def _has_marker(root: Path, kind: str = "reproduction") -> bool:
    with result_snapshot(root) as db:
        return (
            db.execute(
                "SELECT 1 FROM report_materializations WHERE kind=?", (kind,)
            ).fetchone()
            is not None
        )
