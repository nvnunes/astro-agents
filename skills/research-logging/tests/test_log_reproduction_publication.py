"""Publication operates from immutable accepted-plan content."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock

from log_commands.inspection_cli import _render
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_planner import (
    ReproductionCommandInventory,
    ReproductionStateProjection,
)
from log_commands.reproduction_publication import (
    CompletedPublication,
    open_reproduction_publication,
    publish_completed_reproduction,
    verify_publication_retry_compatibility,
)
from log_commands.reproduction_result_storage import PublicationCommitQuery
from log_commands.reproduction_results import RunFolder, RunResult
from reproduction_fixed_plan_test_support import accepted_plan, publication_run
from research_log_paths import RESULTS_STORE
from research_log_result_store import result_snapshot, result_transaction


class ReproductionPublicationTests(unittest.TestCase):
    def test_locked_lookup_distinguishes_absent_exact_conflict_and_new_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, first_root = publication_run(project)
            (log.root / "entries").mkdir()
            first = CompletedPublication(
                accepted_plan(),
                (),
                first_root.name,
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:01:00Z",
                first_root,
            )
            query = PublicationCommitQuery(
                first_root.name,
                "log",
                None,
                None,
                False,
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:01:00Z",
                "complete",
                "tmp/reproduction/2030-01-01/reproduce-publication-fixture",
            )
            with (
                _publisher_run_fixture(first_root),
                open_reproduction_publication(log) as publisher,
            ):
                self.assertEqual(
                    publisher.lookup_run_commit(query).disposition, "absent"
                )
                first_generation = publisher.publish_result_transaction(
                    first
                ).generation
                self.assertEqual(
                    publisher.lookup_run_commit(query).observed_generation,
                    first_generation,
                )

            second_root = first_root.with_name("reproduce-publication-second")
            second_root.mkdir()
            second = CompletedPublication(
                accepted_plan(),
                (),
                second_root.name,
                "2030-01-01T00:02:00Z",
                "2030-01-01T00:03:00Z",
                second_root,
            )
            with (
                _publisher_run_fixture(second_root),
                open_reproduction_publication(log) as publisher,
            ):
                later_generation = publisher.publish_result_transaction(
                    second
                ).generation
                exact = publisher.lookup_run_commit(query)
                self.assertEqual(exact.disposition, "exact")
                self.assertEqual(exact.observed_generation, later_generation)
                report = publisher.materialize_report(first_generation)
                self.assertEqual(report.generation, later_generation)
                conflict = publisher.lookup_run_commit(
                    PublicationCommitQuery(
                        query.run_id,
                        query.target_kind,
                        query.target_entry,
                        query.target_execution_id,
                        query.include_all,
                        query.accepted_at,
                        "2030-01-01T00:01:01Z",
                        query.status,
                        query.folder_path,
                    )
                )
                self.assertEqual(conflict.disposition, "conflict")

    def test_plan_bytes_are_stable_for_retry(self) -> None:
        plan = accepted_plan()
        self.assertEqual(plan.serialized(), plan.serialized())
        self.assertNotIn("validation_snapshot", plan.as_dict())

    def test_retry_reader_rejects_any_pending_or_lineage_field(self) -> None:
        fields = accepted_plan().as_dict()
        fields["pending_writes"] = []
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            ReproductionPlan.from_json(json.dumps(fields, sort_keys=True).encode())

    def test_corrupt_prior_inventory_blocks_retry_before_executor_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, _root = publication_run(Path(directory))
            result_path = log.root / RESULTS_STORE
            result_path.parent.mkdir(parents=True)
            result_path.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "file is not a database"):
                verify_publication_retry_compatibility(log, accepted_plan())

    def test_cold_reproduction_domain_permits_publication_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, _root = publication_run(Path(directory))
            with result_transaction(log.root) as db:
                db.execute(
                    "INSERT INTO store_state VALUES ('validation', 1, 'study.md')"
                )
            verify_publication_retry_compatibility(log, accepted_plan())
        fields = accepted_plan().as_dict()
        fields["attempts"] = []
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            ReproductionPlan.from_json(json.dumps(fields, sort_keys=True).encode())

    def test_normal_publisher_report_failures_preserve_committed_state(
        self,
    ) -> None:
        """A post-commit report failure is repaired by render-only recovery."""

        for failure, code in (
            ("render", "results.report.render_failed"),
            ("write", "results.report.write_failed"),
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                project = Path(directory)
                (project / ".git").mkdir()
                log, run_root = publication_run(project)
                (log.root / "entries").mkdir()
                report = log.root / "reproduction.md"
                report.write_text("prior report\n", encoding="utf-8")
                request = CompletedPublication(
                    accepted_plan(),
                    (),
                    run_root.name,
                    "2030-01-01T00:00:00Z",
                    "2030-01-01T00:01:00Z",
                    run_root,
                )
                target = (
                    "log_commands.reproduction_publication.compose_reproduction_report"
                    if failure == "render"
                    else "log_commands.reproduction_publication.atomic_write_texts"
                )
                with (
                    _publisher_run_fixture(run_root),
                    mock.patch(target, side_effect=OSError("fixture report failure")),
                ):
                    with self.assertRaises(ActionError) as raised:
                        publish_completed_reproduction(log, request)
                self.assertEqual(raised.exception.code, code)
                self.assertIn("generation", str(raised.exception))
                self.assertEqual(report.read_text(encoding="utf-8"), "prior report\n")
                with result_snapshot(log.root) as db:
                    self.assertEqual(
                        db.execute("SELECT run_id FROM reproduction_runs").fetchone()[
                            0
                        ],
                        run_root.name,
                    )
                    self.assertIsNone(
                        db.execute(
                            "SELECT 1 FROM report_materializations "
                            "WHERE kind='reproduction'"
                        ).fetchone()
                    )
                _render(log, "reproduction")
                self.assertNotEqual(
                    report.read_text(encoding="utf-8"), "prior report\n"
                )

    def test_normal_publisher_marker_failure_leaves_stale_marker_for_report_only_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, run_root = publication_run(project)
            (log.root / "entries").mkdir()
            request = CompletedPublication(
                accepted_plan(),
                (),
                run_root.name,
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:01:00Z",
                run_root,
            )
            with (
                _publisher_run_fixture(run_root),
                mock.patch(
                    "research_log_result_store.record_report_materialization",
                    side_effect=OSError("fixture marker failure"),
                ),
            ):
                with self.assertRaises(ActionError) as raised:
                    publish_completed_reproduction(log, request)
            self.assertEqual(raised.exception.code, "results.report.write_failed")
            self.assertTrue((log.root / "reproduction.md").is_file())
            with result_snapshot(log.root) as db:
                self.assertEqual(
                    db.execute("SELECT run_id FROM reproduction_runs").fetchone()[0],
                    run_root.name,
                )
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                )
            _render(log, "reproduction")
            with result_snapshot(log.root) as db:
                self.assertIsNotNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                )

    def test_normal_publisher_rejects_repeated_false_marker_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, run_root = publication_run(project)
            (log.root / "entries").mkdir()
            request = CompletedPublication(
                accepted_plan(),
                (),
                run_root.name,
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:01:00Z",
                run_root,
            )
            with (
                _publisher_run_fixture(run_root),
                mock.patch(
                    "research_log_result_store.record_report_materialization",
                    return_value=False,
                ) as marker,
                self.assertRaises(ActionError) as raised,
            ):
                publish_completed_reproduction(log, request)
            self.assertEqual(raised.exception.code, "results.report.write_failed")
            self.assertEqual(marker.call_count, 2)


@contextmanager
def _publisher_run_fixture(run_root: Path) -> Iterator[None]:
    run = RunResult(
        run_root.name,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        "2030-01-01T00:00:00Z",
        "2030-01-01T00:01:00Z",
        {
            name: 0
            for name in ("changed", "comparison_failed", "failed", "matched", "skipped")
        },
        RunFolder(
            "tmp/reproduction/2030-01-01/reproduce-publication-fixture",
            "available",
        ),
        (),
    )
    with (
        mock.patch(
            "log_commands.reproduction_publication._run_result", return_value=run
        ),
        mock.patch(
            "log_commands.reproduction_publication.project_reproduction_state",
            return_value=ReproductionStateProjection(frozenset(), {}, {}),
        ),
        mock.patch(
            "log_commands.reproduction_publication.project_reproduction_command_inventory",
            return_value=ReproductionCommandInventory(0, 0),
        ),
    ):
        yield
