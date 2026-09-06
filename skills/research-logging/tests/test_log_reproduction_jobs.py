from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast
from unittest import mock

from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_execution import ExecutionBatch
from log_commands.reproduction_jobs import (
    RUN_SCHEMA,
    _accepted_record,
    _acquire_scope_locks,
    _close_fds,
    _find_run,
    _load_run,
    _reconcile_lost_supervisor,
    _require_no_promotion_conflict,
    _status_projection,
    dry_run_reproduction,
    format_reproduction_status,
    launch_reproduction,
    resume_reproduction,
    supervise_reproduction,
)
from log_commands.reproduction_results import RunFolder, RunResult
from log_commands.storage import atomic_write_text
from validation.operation_state import operation_directory


class ReproductionJobTests(unittest.TestCase):
    def test_status_finds_run_beneath_intentional_project_tmp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            external_tmp = root / "run-storage"
            external_tmp.mkdir()
            (project / "tmp").symlink_to(external_tmp, target_is_directory=True)
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            run_id = "reproduce-20300101t000000z-fixture"
            logical_root = project / "tmp" / f"reproduce-research-e003-{run_id}"
            logical_root.mkdir()
            atomic_write_text(
                logical_root / "run.json",
                json.dumps(
                    _accepted_record(log, _plan(), run_id, logical_root, project),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            unrelated = external_tmp / (
                "reproduce-research-e003-"
                "reproduce-20300102t000000z-incompatible"
            )
            unrelated.mkdir()
            (unrelated / "run.json").write_text(
                '{"legacy_run_record": true}\n', encoding="utf-8"
            )

            self.assertEqual(_find_run(log, run_id), logical_root.resolve())

    def test_scope_locks_allow_distinct_entries_and_reject_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)

            first = _acquire_scope_locks(log, "e001")
            second: tuple[int, ...] = ()
            try:
                second = _acquire_scope_locks(log, "e002")
                with self.assertRaisesRegex(Exception, "operation is active"):
                    _acquire_scope_locks(log, "e001")
                with self.assertRaisesRegex(Exception, "operation is active"):
                    _acquire_scope_locks(log, None)
            finally:
                _close_fds(second)
                _close_fds(first)

            whole_log = _acquire_scope_locks(log, None)
            try:
                with self.assertRaisesRegex(Exception, "operation is active"):
                    _acquire_scope_locks(log, "e001")
            finally:
                _close_fds(whole_log)

    def test_dry_run_performs_final_recheck_without_writing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            entry_root = log_root / "entries" / "2030-01-01-e003-example"
            entry_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            before = tuple(
                sorted(path.relative_to(project) for path in project.rglob("*"))
            )
            for include_slow in (False, True):
                plan = replace(_plan(), include_slow=include_slow)
                with (
                    mock.patch(
                        "log_commands.reproduction_jobs.plan_reproduction",
                        return_value=plan,
                    ),
                    mock.patch(
                        "log_commands.reproduction_jobs.preflight_execution_safety"
                    ) as safety,
                    mock.patch(
                        "log_commands.reproduction_jobs.verify_reproduction_snapshot"
                    ) as verify,
                ):
                    observed = dry_run_reproduction(
                        log, entry="e003", include_slow=include_slow
                    )

                self.assertEqual(observed, plan)
                safety.assert_called_once_with()
                verify.assert_called_once_with(log, plan)
                self.assertEqual(
                    tuple(
                        sorted(
                            path.relative_to(project) for path in project.rglob("*")
                        )
                    ),
                    before,
                )

    def test_accepted_run_projects_frozen_status_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            (log_root / "entries" / "2030-01-01-e003-example").mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            plan = _plan()
            run_id = "reproduce-20300101t000000z-fixture"
            run_root = project / "tmp" / f"reproduce-research-e003-{run_id}"
            run_root.mkdir(parents=True)

            with mock.patch(
                "log_commands.reproduction_jobs._utc_now",
                return_value="2030-01-01T00:00:00Z",
            ):
                record = _accepted_record(
                    LogContext(summary, log_root),
                    plan,
                    run_id,
                    run_root,
                    project,
                )

            expected = json.loads(
                (
                    Path(__file__).parent
                    / "fixtures"
                    / "reproduction-status-accepted-v1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(_status_projection(record), expected)

    def test_frozen_status_fixtures_cover_active_and_terminal_lifecycle(self) -> None:
        names = ("executing", "stopping", "stopped", "complete", "failed")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            (log_root / "entries" / "2030-01-01-e003-example").mkdir(
                parents=True
            )
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_root = project / "tmp" / "reproduce-research-e003-fixture"
            run_root.mkdir(parents=True)
            for name in names:
                with self.subTest(name=name):
                    expected = json.loads(
                        (
                            Path(__file__).parent
                            / "fixtures"
                            / f"reproduction-status-{name}-v1.json"
                        ).read_text(encoding="utf-8")
                    )
                    record = _accepted_record(
                        LogContext(summary, log_root),
                        _plan(),
                        cast(str, expected["run_id"]),
                        run_root,
                        project,
                    )
                    cast(dict[str, object], record["progress"]).update(
                        {
                            "artifact_outcomes": expected["artifact_outcomes"],
                            "completed_executions": expected["completed_executions"],
                            "total_executions": expected["total_executions"],
                        }
                    )
                    cast(dict[str, object], record["state"]).update(
                        {
                            "current_execution": expected["current_execution"],
                            "latest_failure": expected["latest_failure"],
                            "phase": expected["phase"],
                            "status": expected["status"],
                        }
                    )
                    record["timestamps"] = expected["timestamps"]
                    record["workers"] = expected["surviving_workers"]
                    path = run_root / "run.json"
                    path.write_text(
                        json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                    self.assertEqual(_status_projection(_load_run(path)), expected)

    def test_run_loader_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_root = project / "tmp" / "reproduce-research-fixture"
            run_root.mkdir(parents=True)
            record = _accepted_record(
                LogContext(summary, log_root),
                _plan(),
                "reproduce-20300101t000000z-fixture",
                run_root,
                project,
            )
            record["unknown"] = True
            path = run_root / "run.json"
            path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "fields are invalid"):
                _load_run(path)

    def test_launch_records_plan_before_detached_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            (project / "tmp").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            (log_root / "entries" / "2030-01-01-e003-example").mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)

            with (
                mock.patch(
                    "log_commands.reproduction_jobs.plan_reproduction",
                    return_value=_plan(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._new_run_id",
                    return_value="reproduce-20300101t000000z-fixture",
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                run_id = launch_reproduction(log, entry="e003", include_slow=False)

            run_root = project / "tmp" / f"reproduce-research-e003-{run_id}"
            record = _load_run(run_root / "run.json")
            self.assertEqual(record["schema"], RUN_SCHEMA)
            self.assertEqual(
                cast(Mapping[str, object], record["plan"])["executions"],
                list(_plan().executions),
            )
            spawn.assert_called_once()

    def test_human_status_exposes_failure(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "reproduction-status-failed-v1.json"
            ).read_text(encoding="utf-8")
        )

        text = format_reproduction_status(fixture)

        self.assertIn("failed", text)
        self.assertIn("Latest failure:", text)

    def test_supervisor_publishes_artifact_failures_as_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_id = "reproduce-20300101t000000z-fixture"
            run_root = project / "tmp" / f"reproduce-research-{run_id}"
            run_root.mkdir(parents=True)
            plan = _empty_plan()
            record = _accepted_record(
                LogContext(summary, log_root),
                plan,
                run_id,
                run_root,
                project,
            )
            atomic_write_text(
                run_root / "run.json",
                json.dumps(record, indent=2, sort_keys=True) + "\n",
            )
            counts = {
                "changed": 0,
                "comparison_failed": 0,
                "failed": 1,
                "matched": 0,
                "skipped": 0,
            }
            run = RunResult(
                run_id,
                plan.target,
                False,
                "complete",
                cast(
                    str,
                    cast(Mapping[str, object], record["timestamps"])["accepted_at"],
                ),
                "2030-01-01T00:00:06Z",
                counts,
                RunFolder(run_root.relative_to(project).as_posix(), "available"),
            )

            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch(
                    "log_commands.reproduction_jobs.populate_output_workspace",
                    return_value=object(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.execute_reproduction_plan",
                    return_value=ExecutionBatch((), (), (), False),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.completed_execution_attempts",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.publish_completed_reproduction",
                    return_value=SimpleNamespace(results=SimpleNamespace(runs=(run,))),
                ),
            ):
                supervise_reproduction(
                    LogContext(summary, log_root),
                    run_root,
                    resume=False,
                    inherited_locks=(),
                )

            status = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(status["status"], "complete")
            self.assertEqual(
                cast(Mapping[str, int], status["artifact_outcomes"])["failed"],
                1,
            )

    def test_active_promotion_output_rejects_intersecting_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            material = project / "shared" / "result.csv"
            plan = replace(
                _plan(),
                source_snapshot={
                    "materials": [
                        {
                            "identity": material.resolve().as_posix(),
                            "role": "boundary",
                        }
                    ]
                },
            )
            directory_path = operation_directory(project)
            directory_path.mkdir(parents=True)
            (directory_path / "promotion-fixture.json").write_text(
                json.dumps({"outputs": [material.resolve().as_posix()]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "active promotion"):
                _require_no_promotion_conflict(LogContext(summary, log_root), plan)

    def test_lost_supervisor_stops_without_restarting_and_resume_reuses_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            run_id = "reproduce-20300101t000000z-fixture"
            run_root = project / "tmp" / f"reproduce-research-e003-{run_id}"
            run_root.mkdir(parents=True)
            atomic_write_text(
                run_root / "run.json",
                json.dumps(
                    _accepted_record(log, _plan(), run_id, run_root, project),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    return_value=[],
                ) as terminate,
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                _reconcile_lost_supervisor(log, run_root)

            stopped = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(
                cast(Mapping[str, object], stopped["latest_failure"])["code"],
                "supervisor_lost",
            )
            terminate.assert_called_once_with(run_id)
            spawn.assert_not_called()

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.verify_reproduction_snapshot"
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                self.assertEqual(resume_reproduction(log, run_id), run_id)

            spawn.assert_called_once_with(log, run_root.resolve(), (), resume=True)
            resumed = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(resumed["phase"], "accepted")
            self.assertIsNone(resumed["status"])

    def test_resume_refuses_changed_snapshot_and_preserves_stopped_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            run_id = "reproduce-20300101t000000z-fixture"
            run_root = project / "tmp" / f"reproduce-research-e003-{run_id}"
            run_root.mkdir(parents=True)
            record = _accepted_record(log, _plan(), run_id, run_root, project)
            cast(dict[str, object], record["state"]).update(
                {"phase": None, "status": "stopped"}
            )
            cast(dict[str, object], record["timestamps"])["stopped_at"] = (
                "2030-01-01T00:00:05Z"
            )
            atomic_write_text(
                run_root / "run.json",
                json.dumps(record, indent=2, sort_keys=True) + "\n",
            )

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.verify_reproduction_snapshot",
                    side_effect=ActionError(
                        "reproduction.source.changed", "source changed"
                    ),
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                with self.assertRaisesRegex(ActionError, "source changed"):
                    resume_reproduction(log, run_id)

            spawn.assert_not_called()
            preserved = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(preserved["status"], "stopped")
            self.assertIsNone(preserved["phase"])


def _plan() -> ReproductionPlan:
    execution = "pyrun-exec/v1:" + "1" * 64
    return ReproductionPlan(
        "docs/research.md",
        {"entry": "e003", "kind": "entry"},
        False,
        {},
        {"materials": []},
        (),
        (
            {
                "depends_on": [],
                "entry": "e003",
                "execution_id": execution,
                "order": 1,
                "outputs": ["data/result.txt"],
                "slow": False,
            },
        ),
        (),
        (),
    )


def _empty_plan() -> ReproductionPlan:
    return ReproductionPlan(
        "docs/research.md",
        {"entry": None, "kind": "log"},
        False,
        {},
        {"materials": []},
        (),
        (),
        (),
        (),
    )


if __name__ == "__main__":
    unittest.main()
