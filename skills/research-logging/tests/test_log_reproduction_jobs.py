from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence, cast
from unittest import mock

from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan, source_snapshot
from log_commands.reproduction_execution import (
    ExecutionAttempt,
    ExecutionBatch,
    ExecutionCheckpoint,
    WorkerRecord,
)
from log_commands.reproduction_jobs import (
    LEGACY_RUN_SCHEMA,
    LEGACY_VALIDATION_BLOCKED_PUBLICATION_FAILURE,
    PUBLICATION_RETRY,
    RUN_SCHEMA,
    _accepted_record,
    _acquire_scope_locks,
    _checkpoint_dicts,
    _close_fds,
    _combined_attempt_workers,
    _continue_failed_cleanup,
    _execution_progress,
    _failed_checkpoint_references,
    _find_run,
    _finish_failed,
    _is_publication_retry,
    _load_run,
    _marker_identity,
    _reconcile_lost_supervisor,
    _reconciled_worker_history,
    _require_no_promotion_conflict,
    _resumable_execution_references,
    _status_projection,
    _verify_checkpoint_inventory,
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
    def test_exact_legacy_publication_failure_is_retry_only(self) -> None:
        record = {
            "schema": RUN_SCHEMA,
            "state": {
                "active_executions": [],
                "operational_failure": {
                    "code": "reproduction.job.failed",
                    "entry": None,
                    "execution_id": None,
                    "message": LEGACY_VALIDATION_BLOCKED_PUBLICATION_FAILURE,
                },
                "phase": None,
                "status": "failed",
            },
            "timestamps": {"finished_at": "2030-01-01T00:00:05Z"},
            "workers": [{"state": "exited"}],
            "checkpoints": [{"state": "succeeded"}, {"state": "failed"}],
        }

        self.assertTrue(_is_publication_retry(record))
        self.assertEqual(
            _resumable_execution_references(record, mode=PUBLICATION_RETRY),
            frozenset(),
        )

        unrelated = cast(dict[str, object], record["state"])[
            "operational_failure"
        ]
        assert isinstance(unrelated, dict)
        unrelated["message"] = "unrelated job failure"
        self.assertFalse(_is_publication_retry(record))

        unrelated["message"] = LEGACY_VALIDATION_BLOCKED_PUBLICATION_FAILURE
        cast(dict[str, object], record["state"])["active_executions"] = [
            {"entry": "e001", "execution_id": "pyrun-exec/v1:" + "1" * 64}
        ]
        self.assertFalse(_is_publication_retry(record))

    def test_resume_sets_use_every_schema_specific_terminal_checkpoint(self) -> None:
        identity_a = "pyrun-exec/v1:" + "1" * 64
        identity_b = "pyrun-exec/v1:" + "2" * 64
        identity_c = "pyrun-exec/v1:" + "3" * 64
        record = {
            "schema": RUN_SCHEMA,
            "checkpoints": [
                {"entry": "e001", "execution_id": identity_a, "state": "stopped"},
                {"entry": "e002", "execution_id": identity_b, "state": "stopped"},
                {"entry": "e003", "execution_id": identity_c, "state": "failed"},
            ],
        }

        self.assertEqual(
            _resumable_execution_references(record, mode="stopped"),
            frozenset((f"e001:{identity_a}", f"e002:{identity_b}")),
        )
        self.assertEqual(
            _failed_checkpoint_references(record),
            frozenset((f"e003:{identity_c}",)),
        )

    def test_cleanup_retry_preserves_compound_worker_identity_and_history(
        self,
    ) -> None:
        identity = "pyrun-exec/v1:" + "1" * 64
        prior = {
            "entry": "e003",
            "execution_id": identity,
            "last_observed_at": "2030-01-01T00:00:03Z",
            "parent_worker_id": None,
            "pid": 4321,
            "registered_at": "2030-01-01T00:00:02Z",
            "state": "running",
            "worker_id": "worker-4321",
        }
        survivor = {
            **prior,
            "last_observed_at": "2030-01-01T00:00:04Z",
            "registered_at": "2030-01-01T00:00:04Z",
        }

        workers = _reconciled_worker_history([prior], [survivor], legacy=False)

        self.assertEqual(workers[0]["registered_at"], "2030-01-01T00:00:02Z")
        self.assertEqual(workers[0]["last_observed_at"], "2030-01-01T00:00:04Z")
        self.assertEqual(
            _marker_identity("reproduce-fixture", "reproduce-fixture:e003:" + "1" * 64),
            ("e003", identity),
        )

    def test_failed_cleanup_retry_retains_failed_intent_without_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, run_root, run_id = _write_active_run(Path(directory))
            identity = "pyrun-exec/v1:" + "1" * 64
            survivor = {
                "entry": "e003",
                "execution_id": identity,
                "last_observed_at": "2030-01-01T00:00:04Z",
                "parent_worker_id": None,
                "pid": 4321,
                "registered_at": "2030-01-01T00:00:04Z",
                "state": "running",
                "worker_id": "worker-4321",
            }

            def retry(
                retry_log: LogContext,
                retry_root: Path,
                *,
                terminal_status: str,
            ) -> None:
                self.assertEqual(terminal_status, "failed")
                self.assertTrue(_continue_failed_cleanup(retry_log, retry_root))

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    side_effect=([survivor], []),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._wait_for_cleanup_retry",
                    side_effect=retry,
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_run_scheduling"
                ) as release,
            ):
                _finish_failed(log, run_root, ActionError("fixture.failed", "failed"))

            record = _load_run(run_root / "run.json")
            self.assertEqual(
                cast(Mapping[str, object], record["state"])["status"], "failed"
            )
            self.assertEqual(
                cast(Sequence[Mapping[str, object]], record["checkpoints"])[0]["state"],
                "failed",
            )
            self.assertEqual(
                cast(Sequence[Mapping[str, object]], record["workers"])[0][
                    "registered_at"
                ],
                "2030-01-01T00:00:01Z",
            )
            release.assert_called_once_with(Path(directory).resolve(), run_id)

    def test_lost_supervisor_recovers_durable_failed_cleanup_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, run_root, run_id = _write_active_run(
                Path(directory), failure_intent=True
            )
            with (
                mock.patch(
                    "log_commands.reproduction_jobs._terminate_marked_workers",
                    return_value=[],
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_run_scheduling"
                ) as release,
            ):
                _reconcile_lost_supervisor(log, run_root)

            record = _load_run(run_root / "run.json")
            state = cast(Mapping[str, object], record["state"])
            self.assertEqual(state["status"], "failed")
            self.assertIsNone(state["phase"])
            self.assertEqual(
                cast(Sequence[Mapping[str, object]], record["checkpoints"])[0]["state"],
                "failed",
            )
            release.assert_called_once_with(Path(directory).resolve(), run_id)

    def test_checkpoint_inventory_is_bounded_and_schema_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            checkpoints = run_root / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "foreign.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "checkpoint record"):
                _checkpoint_dicts(run_root, legacy=False)

        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            checkpoints = run_root / "checkpoints"
            checkpoints.mkdir()
            for index in range(2_049):
                (checkpoints / f"{index:04d}.json").touch()
            with self.assertRaisesRegex(ActionError, "entry bound"):
                _checkpoint_dicts(run_root, legacy=False)

        with tempfile.TemporaryDirectory() as directory:
            _log, run_root, _run_id = _write_active_run(Path(directory))
            identity = "pyrun-exec/v1:" + "2" * 64
            foreign = {
                "completed_at": None,
                "elapsed_seconds": None,
                "entry": "e999",
                "execution_id": identity,
                "failure": None,
                "finished_at": None,
                "outputs": [],
                "path": "checkpoints/e999-" + "2" * 64 + ".json",
                "started_at": None,
                "state": "active",
            }
            atomic_write_text(
                run_root / cast(str, foreign["path"]),
                json.dumps(foreign, indent=2, sort_keys=True) + "\n",
            )
            with self.assertRaisesRegex(ActionError, "absent from the accepted plan"):
                _checkpoint_dicts(run_root, legacy=False)

        with tempfile.TemporaryDirectory() as directory:
            _log, run_root, _run_id = _write_active_run(Path(directory))
            checkpoint = next((run_root / "checkpoints").iterdir())
            checkpoint.rename(checkpoint.with_name("foreign.json"))
            with self.assertRaisesRegex(ActionError, "file location"):
                _checkpoint_dicts(run_root, legacy=False)

        with tempfile.TemporaryDirectory() as directory:
            _log, run_root, _run_id = _write_active_run(Path(directory))
            record = _load_run(run_root / "run.json")
            checkpoint_path = next((run_root / "checkpoints").iterdir())
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["state"] = "stopped"
            checkpoint["failure"] = {
                "code": "stop_requested",
                "message": "stopped",
                "recorded_at": "2030-01-01T00:00:03Z",
            }
            atomic_write_text(
                checkpoint_path,
                json.dumps(checkpoint, indent=2, sort_keys=True) + "\n",
            )
            with self.assertRaisesRegex(ActionError, "inventory changed"):
                _verify_checkpoint_inventory(run_root, record)

    def test_parallel_stop_retains_workers_from_every_attempt(self) -> None:
        identity_a = "pyrun-exec/v1:" + "1" * 64
        identity_b = "pyrun-exec/v1:" + "2" * 64

        def attempt(
            entry: str, identity: str, pid: int, state: str
        ) -> ExecutionAttempt:
            checkpoint = ExecutionCheckpoint(
                entry, identity, "stopped", "checkpoint.json", None, ()
            )
            worker = WorkerRecord(
                f"worker-{pid}",
                None,
                pid,
                identity,
                state,
                "2030-01-01T00:00:01Z",
                "2030-01-01T00:00:02Z",
                entry,
            )
            return ExecutionAttempt(
                entry,
                identity,
                None,
                True,
                "stop_requested",
                "stopped",
                checkpoint,
                (worker,),
                "",
                "",
            )

        workers = _combined_attempt_workers(
            (
                attempt("e001", identity_a, 4001, "running"),
                attempt("e002", identity_b, 4002, "exited"),
            ),
            legacy=False,
        )

        self.assertEqual(
            [(item["entry"], item["state"]) for item in workers],
            [("e001", "running"), ("e002", "exited")],
        )

    def test_parallel_progress_uses_compound_identity_for_reversed_finishes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs/research"
            log_root.mkdir(parents=True)
            summary = project / "docs/research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            run_id = "reproduce-20300101t000000z-compound"
            run_root = (
                project / "tmp/reproduction/2030-01-01" / f"reproduce-research-{run_id}"
            )
            run_root.mkdir(parents=True)
            identity = "pyrun-exec/v1:" + "1" * 64
            items = tuple(
                {
                    "depends_on": [],
                    "entry": entry,
                    "execution_id": identity,
                    "order": order,
                    "outputs": [f"data/{entry}.txt"],
                    "auto_reproduce": True,
                    "exclusive": False,
                    "read_paths": [],
                    "run_path": f"<run>/executions/{entry}/" + "1" * 64,
                    "writable_paths": [
                        f"<run>/diagnostics/{entry}/" + "1" * 64,
                        f"<run>/runtime/{entry}/" + "1" * 64,
                        f"<run>/workspace/data/{entry}.txt",
                    ],
                    "write_paths": [f"<run>/workspace/data/{entry}.txt"],
                }
                for order, entry in enumerate(("e001", "e002"), 1)
            )
            plan = replace(
                _plan(),
                target={"entry": None, "kind": "log"},
                jobs=2,
                executions=items,
            )
            record = _accepted_record(
                log, plan, run_id, run_root, accepted_at="2030-01-01T00:00:00Z"
            )
            atomic_write_text(
                run_root / "run.json",
                json.dumps(record, indent=2, sort_keys=True) + "\n",
            )

            _execution_progress(log, run_root, "started", ("e001", identity), None)
            _execution_progress(log, run_root, "started", ("e002", identity), None)
            _execution_progress(log, run_root, "finished", ("e002", identity), None)

            state = cast(
                Mapping[str, object], _load_run(run_root / "run.json")["state"]
            )
            self.assertEqual(
                state["active_executions"],
                [{"entry": "e001", "execution_id": identity}],
            )
            _execution_progress(log, run_root, "finished", ("e001", identity), None)
            state = cast(
                Mapping[str, object], _load_run(run_root / "run.json")["state"]
            )
            self.assertEqual(state["active_executions"], [])

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
            logical_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
            logical_root.mkdir(parents=True)
            atomic_write_text(
                logical_root / "run.json",
                json.dumps(
                    _accepted_record(
                        log,
                        _plan(),
                        run_id,
                        logical_root,
                        accepted_at="2030-01-01T00:00:00Z",
                    ),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            unrelated = external_tmp / (
                "reproduce-research-e003-reproduce-20300102t000000z-incompatible"
            )
            unrelated.mkdir()
            (unrelated / "run.json").write_text(
                '{"legacy_run_record": true}\n', encoding="utf-8"
            )

            self.assertEqual(_find_run(log, run_id), logical_root.resolve())

    def test_run_lookup_rejects_duplicate_run_id_across_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary, log_root)
            run_id = "reproduce-20300101t000000z-fixture"
            leaf = f"reproduce-research-e003-{run_id}"
            for run_date in ("2030-01-01", "2030-01-02"):
                run_root = project / "tmp" / "reproduction" / run_date / leaf
                run_root.mkdir(parents=True)
                record = _accepted_record(
                    log,
                    _plan(),
                    run_id,
                    run_root,
                    accepted_at=f"{run_date}T00:00:00Z",
                )
                atomic_write_text(
                    run_root / "run.json",
                    json.dumps(record, indent=2, sort_keys=True) + "\n",
                )

            with self.assertRaisesRegex(ActionError, "expected one run, found 2"):
                _find_run(log, run_id)

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
            for include_all in (False, True):
                plan = replace(_plan(), include_all=include_all)
                with (
                    mock.patch(
                        "log_commands.reproduction_jobs.plan_reproduction",
                        return_value=plan,
                    ) as planner,
                    mock.patch(
                        "log_commands.reproduction_jobs.preflight_execution_safety"
                    ) as safety,
                    mock.patch(
                        "log_commands.reproduction_jobs.verify_reproduction_snapshot"
                    ) as verify,
                ):
                    observed = dry_run_reproduction(
                        log,
                        entry="e003",
                        include_all=include_all,
                        recheck=True,
                    )

                self.assertEqual(observed, plan)
                planner.assert_called_once_with(
                    log,
                    entry=mock.ANY,
                    include_all=include_all,
                    jobs=1,
                    selection_policy="recheck",
                )
                safety.assert_called_once_with()
                verify.assert_called_once_with(log, plan)
                self.assertEqual(
                    tuple(
                        sorted(path.relative_to(project) for path in project.rglob("*"))
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
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
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
                )

            expected = _status_fixture("accepted")
            self.assertEqual(_status_projection(record), expected)

    def test_frozen_status_fixtures_cover_active_and_terminal_lifecycle(self) -> None:
        names = ("executing", "stopping", "stopped", "complete", "failed")
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            (log_root / "entries" / "2030-01-01-e003-example").mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / "reproduce-research-e003-reproduce-20300101t000000z-fixture"
            )
            run_root.mkdir(parents=True)
            for name in names:
                with self.subTest(name=name):
                    expected = _status_fixture(name)
                    record = _accepted_record(
                        LogContext(summary, log_root),
                        _plan(),
                        cast(str, expected["run_id"]),
                        run_root,
                        accepted_at="2030-01-01T00:00:00Z",
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
                            "active_executions": expected["active_executions"],
                            "latest_execution_diagnostic": expected[
                                "latest_execution_diagnostic"
                            ],
                            "operational_failure": expected["operational_failure"],
                            "phase": expected["phase"],
                            "status": expected["status"],
                        }
                    )
                    record["timestamps"] = expected["timestamps"]
                    record["workers"] = expected["surviving_workers"]
                    record["checkpoints"] = [
                        {
                            "completed_at": (
                                item["finished_at"]
                                if item["state"] == "succeeded"
                                else None
                            ),
                            "elapsed_seconds": item["elapsed_seconds"],
                            "entry": item["entry"],
                            "execution_id": item["execution_id"],
                            "finished_at": item["finished_at"],
                            "failure": item["failure"],
                            "outputs": [],
                            "path": (
                                "checkpoints/"
                                + item["entry"]
                                + "-"
                                + item["execution_id"].removeprefix("pyrun-exec/v1:")
                                + ".json"
                            ),
                            "started_at": item["started_at"],
                            "state": item["state"],
                        }
                        for item in cast(
                            list[Mapping[str, object]], expected["execution_timings"]
                        )
                    ]
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
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / "reproduce-research-fixture"
            )
            run_root.mkdir(parents=True)
            record = _accepted_record(
                LogContext(summary, log_root),
                _plan(),
                "reproduce-20300101t000000z-fixture",
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
            record["unknown"] = True
            path = run_root / "run.json"
            path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "fields are invalid"):
                _load_run(path)

    def test_legacy_run_remains_readable_with_serial_status_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_root = (
                project
                / "tmp/reproduction/2030-01-01"
                / "reproduce-research-e003-reproduce-20300101t000000z-fixture"
            )
            run_root.mkdir(parents=True)
            record = _accepted_record(
                LogContext(summary, log_root),
                _plan(),
                "reproduce-20300101t000000z-fixture",
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
            record["schema"] = LEGACY_RUN_SCHEMA
            record.pop("jobs")
            plan = cast(dict[str, object], record["plan"])
            plan.pop("jobs")
            execution = cast(list[dict[str, object]], plan["executions"])[0]
            for field in (
                "exclusive",
                "read_paths",
                "run_path",
                "writable_paths",
                "write_paths",
            ):
                execution.pop(field)
            state = cast(dict[str, object], record["state"])
            state["current_execution"] = None
            state.pop("active_executions")
            path = run_root / "run.json"
            atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")

            loaded = _load_run(path)
            status = _status_projection(loaded)

            self.assertEqual(status["schema"], "research-log-reproduction-status/2")
            self.assertNotIn("jobs", status)
            self.assertIsNone(status["current_execution"])

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
                ) as planner,
                mock.patch(
                    "log_commands.reproduction_jobs._new_run_id",
                    return_value="reproduce-20300101t000000z-fixture",
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._utc_now",
                    return_value="2030-01-01T00:00:00Z",
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                run_id = launch_reproduction(
                    log, entry="e003", include_all=False, recheck=True
                )

            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
            record = _load_run(run_root / "run.json")
            self.assertEqual(record["schema"], RUN_SCHEMA)
            self.assertEqual(
                cast(Mapping[str, object], record["plan"])["executions"],
                list(_plan().executions),
            )
            self.assertNotIn("recheck", record)
            self.assertNotIn("recheck", cast(Mapping[str, object], record["plan"]))
            planner.assert_called_once_with(
                log,
                entry=mock.ANY,
                include_all=False,
                jobs=1,
                selection_policy="recheck",
            )
            spawn.assert_called_once()

    def test_human_status_exposes_failure(self) -> None:
        fixture = _status_fixture("failed")

        text = format_reproduction_status(fixture)

        self.assertIn("failed", text)
        self.assertIn("Operational failure:", text)

    def test_status_exposes_active_execution_timing_without_queued_work(self) -> None:
        record = {
            "checkpoints": [
                {
                    "completed_at": None,
                    "elapsed_seconds": 12.5,
                    "entry": "e003",
                    "execution_id": "pyrun-exec/v1:" + "1" * 64,
                    "finished_at": None,
                    "outputs": [],
                    "path": "executions/example/checkpoint.json",
                    "started_at": "2030-01-01T00:00:01Z",
                    "state": "active",
                }
            ],
            "include_all": False,
            "jobs": 1,
            "progress": {
                "artifact_outcomes": {},
                "completed_executions": 0,
                "total_executions": 2,
            },
            "run_id": "reproduce-20300101t000000z-fixture",
            "schema": RUN_SCHEMA,
            "state": {
                "active_executions": [
                    {
                        "entry": "e003",
                        "execution_id": "pyrun-exec/v1:" + "1" * 64,
                    }
                ],
                "latest_execution_diagnostic": None,
                "operational_failure": None,
                "phase": "executing",
                "status": None,
            },
            "summary": "docs/research.md",
            "target": {"entry": "e003", "kind": "entry"},
            "timestamps": {},
            "workers": [],
        }

        status = _status_projection(record)

        self.assertEqual(len(cast(list[object], status["execution_timings"])), 1)
        self.assertIn(
            "Active execution time (e003): 12.5 seconds",
            format_reproduction_status(status),
        )

    def test_supervisor_publishes_artifact_failures_as_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log_root = project / "docs" / "research"
            log_root.mkdir(parents=True)
            summary = project / "docs" / "research.md"
            summary.write_text("# Research\n", encoding="utf-8")
            run_id = "reproduce-20300101t000000z-fixture"
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-{run_id}"
            )
            run_root.mkdir(parents=True)
            plan = _empty_plan()
            record = _accepted_record(
                LogContext(summary, log_root),
                plan,
                run_id,
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
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
                    "log_commands.reproduction_jobs.load_recorded_comparisons",
                    return_value=(),
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
                    "log_commands.reproduction_jobs."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.publish_completed_reproduction",
                    return_value=SimpleNamespace(results=SimpleNamespace(runs=(run,))),
                ),
                mock.patch(
                    "log_commands.validation_adapter.evaluate_validation",
                    side_effect=ActionError(
                        "validation.failed", "validation did not complete"
                    ),
                ) as validate,
            ):
                supervise_reproduction(
                    LogContext(summary, log_root),
                    run_root,
                    mode="fresh",
                    inherited_locks=(),
                )

            status = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(status["status"], "complete")
            self.assertEqual(
                cast(Mapping[str, int], status["artifact_outcomes"])["failed"],
                1,
            )
            self.assertIsNone(status["operational_failure"])
            validate.assert_called_once_with(summary)

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
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
            run_root.mkdir(parents=True)
            identity = "pyrun-exec/v1:" + "1" * 64
            checkpoint = {
                "completed_at": None,
                "elapsed_seconds": 2.0,
                "entry": "e003",
                "execution_id": identity,
                "failure": None,
                "finished_at": None,
                "outputs": [],
                "path": "checkpoints/e003-" + "1" * 64 + ".json",
                "started_at": "2030-01-01T00:00:01Z",
                "state": "active",
            }
            (run_root / "checkpoints").mkdir()
            atomic_write_text(
                run_root / cast(str, checkpoint["path"]),
                json.dumps(checkpoint, indent=2, sort_keys=True) + "\n",
            )
            record = _accepted_record(
                log,
                _plan(),
                run_id,
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
            cast(dict[str, object], record["state"]).update(
                {
                    "active_executions": [{"entry": "e003", "execution_id": identity}],
                    "phase": "executing",
                }
            )
            record["checkpoints"] = [checkpoint]
            record["workers"] = [
                {
                    "entry": "e003",
                    "execution_id": identity,
                    "last_observed_at": "2030-01-01T00:00:03Z",
                    "parent_worker_id": None,
                    "pid": 4321,
                    "registered_at": "2030-01-01T00:00:02Z",
                    "state": "exited",
                    "worker_id": "worker-4321",
                }
            ]
            atomic_write_text(
                run_root / "run.json",
                json.dumps(record, indent=2, sort_keys=True) + "\n",
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
                cast(Mapping[str, object], stopped["latest_execution_diagnostic"])[
                    "code"
                ],
                "supervisor_lost",
            )
            terminal = cast(
                list[Mapping[str, object]],
                _load_run(run_root / "run.json")["checkpoints"],
            )
            self.assertEqual(terminal[0]["state"], "stopped")
            self.assertEqual(
                cast(Mapping[str, object], terminal[0]["failure"])["code"],
                "supervisor_lost",
            )
            self.assertEqual(
                cast(
                    list[Mapping[str, object]],
                    _load_run(run_root / "run.json")["workers"],
                )[0]["registered_at"],
                "2030-01-01T00:00:02Z",
            )
            terminate.assert_called_once_with(run_id)
            spawn.assert_not_called()

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                self.assertEqual(resume_reproduction(log, run_id), run_id)

            spawn.assert_called_once_with(
                log,
                run_root.resolve(),
                (),
                resume=True,
                retry_publication=False,
            )
            resumed = _status_projection(_load_run(run_root / "run.json"))
            self.assertEqual(resumed["phase"], "accepted")
            self.assertIsNone(resumed["status"])

    def test_resume_retries_failed_publication_without_starting_a_new_run(
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
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
            run_root.mkdir(parents=True)
            record = _accepted_record(
                log,
                _plan(),
                run_id,
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
            state = cast(dict[str, object], record["state"])
            state.update(
                {
                    "phase": None,
                    "status": "failed",
                    "operational_failure": {
                        "code": "reproduction.publication.failed",
                        "entry": None,
                        "execution_id": None,
                        "message": "publication failed",
                        "recorded_at": "2030-01-01T00:00:05Z",
                    },
                }
            )
            cast(dict[str, object], record["timestamps"])["finished_at"] = (
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
                    "log_commands.reproduction_jobs."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
            ):
                self.assertEqual(resume_reproduction(log, run_id), run_id)

            spawn.assert_called_once_with(
                log,
                run_root.resolve(),
                (),
                resume=True,
                retry_publication=True,
            )
            resumed = _status_projection(_load_run(run_root / "run.json"))
            self.assertIsNone(resumed["operational_failure"])
            self.assertEqual(resumed["phase"], "accepted")

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
            run_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / f"reproduce-research-e003-{run_id}"
            )
            run_root.mkdir(parents=True)
            record = _accepted_record(
                log,
                _plan(),
                run_id,
                run_root,
                accepted_at="2030-01-01T00:00:00Z",
            )
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
                    "log_commands.reproduction_jobs."
                    "verify_reproduction_runtime_snapshot",
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


def _write_active_run(
    project: Path, *, failure_intent: bool = False
) -> tuple[LogContext, Path, str]:
    (project / ".git").mkdir()
    log_root = project / "docs" / "research"
    log_root.mkdir(parents=True)
    summary = project / "docs" / "research.md"
    summary.write_text("# Research\n", encoding="utf-8")
    log = LogContext(summary, log_root)
    run_id = "reproduce-20300101t000000z-fixture"
    run_root = (
        project / "tmp/reproduction/2030-01-01" / f"reproduce-research-e003-{run_id}"
    )
    run_root.mkdir(parents=True)
    identity = "pyrun-exec/v1:" + "1" * 64
    checkpoint = {
        "completed_at": None,
        "elapsed_seconds": 2.0,
        "entry": "e003",
        "execution_id": identity,
        "failure": None,
        "finished_at": None,
        "outputs": [],
        "path": "checkpoints/e003-" + "1" * 64 + ".json",
        "started_at": "2030-01-01T00:00:01Z",
        "state": "active",
    }
    (run_root / "checkpoints").mkdir()
    atomic_write_text(
        run_root / cast(str, checkpoint["path"]),
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n",
    )
    record = _accepted_record(
        log,
        _plan(),
        run_id,
        run_root,
        accepted_at="2030-01-01T00:00:00Z",
    )
    state = cast(dict[str, object], record["state"])
    state.update(
        {
            "active_executions": [{"entry": "e003", "execution_id": identity}],
            "phase": "stopping" if failure_intent else "executing",
        }
    )
    if failure_intent:
        state["operational_failure"] = {
            "code": "fixture.failed",
            "entry": None,
            "execution_id": None,
            "message": "failed",
            "recorded_at": "2030-01-01T00:00:02Z",
        }
    record["checkpoints"] = [checkpoint]
    record["workers"] = [
        {
            "entry": "e003",
            "execution_id": identity,
            "last_observed_at": "2030-01-01T00:00:02Z",
            "parent_worker_id": None,
            "pid": 4321,
            "registered_at": "2030-01-01T00:00:01Z",
            "state": "running",
            "worker_id": "worker-4321",
        }
    ]
    atomic_write_text(
        run_root / "run.json", json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    return log, run_root, run_id


def _plan() -> ReproductionPlan:
    execution = "pyrun-exec/v1:" + "1" * 64
    return ReproductionPlan(
        "docs/research.md",
        {"entry": "e003", "kind": "entry"},
        False,
        {},
        source_snapshot(authority_files=(), executions=(), materials=()),
        (),
        (
            {
                "depends_on": [],
                "entry": "e003",
                "execution_id": execution,
                "order": 1,
                "outputs": ["data/result.txt"],
                "auto_reproduce": True,
                "exclusive": False,
                "read_paths": [],
                "run_path": "<run>/executions/e003/" + "1" * 64,
                "writable_paths": [
                    "<run>/diagnostics/e003/" + "1" * 64,
                    "<run>/runtime/e003/" + "1" * 64,
                    "<run>/workspace/data/result.txt",
                ],
                "write_paths": ["<run>/workspace/data/result.txt"],
            },
        ),
        (),
        (),
    )


def _status_fixture(name: str) -> dict[str, object]:
    execution = "pyrun-exec/v1:" + "1" * 64
    timestamps: dict[str, str | None] = {
        "accepted_at": "2030-01-01T00:00:00Z",
        "finished_at": None,
        "resumed_at": None,
        "started_at": None,
        "stopped_at": None,
        "updated_at": "2030-01-01T00:00:00Z",
    }
    value: dict[str, object] = {
        "artifact_outcomes": {
            "changed": 0,
            "comparison_failed": 0,
            "failed": 0,
            "matched": 0,
            "skipped": 0,
        },
        "completed_executions": 0,
        "active_executions": [],
        "active_workers": [],
        "execution_timings": [],
        "include_all": False,
        "jobs": 1,
        "latest_execution_diagnostic": None,
        "operational_failure": None,
        "phase": name,
        "run_id": "reproduce-20300101t000000z-fixture",
        "schema": "research-log-reproduction-status/3",
        "status": None,
        "summary": "docs/research.md",
        "surviving_workers": [],
        "target": {"entry": "e003", "kind": "entry"},
        "timestamps": timestamps,
        "total_executions": 1,
    }
    if name != "accepted":
        timestamps["started_at"] = "2030-01-01T00:00:01Z"
    updated = {
        "planning": "01",
        "preflight": "02",
        "executing": "02",
        "comparing": "04",
        "stopping": "04",
        "publishing": "05",
        "stopped": "05",
        "complete": "06",
        "failed": "06",
    }.get(name)
    if updated is not None:
        timestamps["updated_at"] = f"2030-01-01T00:00:{updated}Z"
    if name in {"executing", "comparing"}:
        if name == "executing":
            value["active_executions"] = [{"entry": "e003", "execution_id": execution}]
        value["execution_timings"] = [
            {
                "elapsed_seconds": 12.5,
                "entry": "e003",
                "execution_id": execution,
                "failure": None,
                "finished_at": (
                    "2030-01-01T00:00:02Z" if name == "comparing" else None
                ),
                "started_at": "2030-01-01T00:00:01Z",
                "state": "succeeded" if name == "comparing" else "active",
            }
        ]
    if name in {"publishing", "complete"}:
        value["completed_executions"] = 1
        cast(dict[str, int], value["artifact_outcomes"])["matched"] = 1
    if name == "stopping":
        value["latest_execution_diagnostic"] = {
            "code": "worker_cleanup_incomplete",
            "entry": "e003",
            "execution_id": execution,
            "message": "One worker survived shutdown.",
            "recorded_at": "2030-01-01T00:00:04Z",
        }
        value["surviving_workers"] = [
            {
                "entry": "e003",
                "execution_id": execution,
                "last_observed_at": "2030-01-01T00:00:04Z",
                "parent_worker_id": None,
                "pid": 4321,
                "registered_at": "2030-01-01T00:00:02Z",
                "state": "running",
                "worker_id": "worker-4321",
            }
        ]
        value["active_workers"] = value["surviving_workers"]
        value["active_executions"] = [{"entry": "e003", "execution_id": execution}]
    if name == "stopped":
        value["phase"] = None
        value["status"] = "stopped"
        timestamps["stopped_at"] = "2030-01-01T00:00:05Z"
        value["latest_execution_diagnostic"] = {
            "code": "stop_requested",
            "entry": "e003",
            "execution_id": execution,
            "message": "Reproduction was stopped by request.",
            "recorded_at": "2030-01-01T00:00:05Z",
        }
    if name in {"complete", "failed"}:
        value["phase"] = None
        value["status"] = name
        timestamps["finished_at"] = "2030-01-01T00:00:06Z"
    if name == "failed":
        value["operational_failure"] = {
            "code": "publication_failed",
            "entry": None,
            "execution_id": None,
            "message": "Final publication failed.",
            "recorded_at": "2030-01-01T00:00:06Z",
        }
    return value


def _empty_plan() -> ReproductionPlan:
    return ReproductionPlan(
        "docs/research.md",
        {"entry": None, "kind": "log"},
        False,
        {},
        source_snapshot(authority_files=(), executions=(), materials=()),
        (),
        (),
        (),
        (),
    )


if __name__ == "__main__":
    unittest.main()
