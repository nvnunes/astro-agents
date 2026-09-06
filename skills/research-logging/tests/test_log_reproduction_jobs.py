from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast
from unittest import mock

from log_commands.context import LogContext
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_execution import ExecutionBatch
from log_commands.reproduction_jobs import (
    RUN_SCHEMA,
    _accepted_record,
    _load_run,
    _status_projection,
    format_reproduction_status,
    launch_reproduction,
    supervise_reproduction,
)
from log_commands.reproduction_results import RunFolder, RunResult
from log_commands.storage import atomic_write_text


class ReproductionJobTests(unittest.TestCase):
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
                    "log_commands.reproduction_jobs.populate_disposable_copy",
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


def _plan() -> ReproductionPlan:
    execution = "pyrun-exec/v1:" + "1" * 64
    return ReproductionPlan(
        "docs/research.md",
        {"entry": "e003", "kind": "entry"},
        False,
        {},
        {},
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
        {},
        (),
        (),
        (),
        (),
    )


if __name__ == "__main__":
    unittest.main()
