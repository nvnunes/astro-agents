from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import cast

from log_commands.model import ActionError
from log_commands.reproduction_scheduler import (
    _claims_conflict,
    _remove_dead,
    acquire_scheduling_permit,
    release_scheduling_permit,
)
from validation.operation_state import operation_directory


def _planned(order: int, *, exclusive: bool, leaf: str) -> dict[str, object]:
    return {
        "entry": f"e{order:03d}",
        "execution_id": "pyrun-exec/v1:" + f"{order:x}" * 64,
        "order": order,
        "exclusive": exclusive,
        "read_paths": [f"<project>/origins/{leaf}.csv"],
        "write_paths": [f"<run>/workspace/data/{leaf}.csv"],
        "run_path": f"<run>/executions/e{order:03d}/{leaf}",
        "writable_paths": [f"<run>/runtime/e{order:03d}/{leaf}"],
    }


class ReproductionSchedulerTests(unittest.TestCase):
    def test_frozen_parallel_fixture_names_remain_covered(self) -> None:
        path = (
            Path(__file__).parent / "fixtures" / "parallel-reproduction-contract.json"
        )
        fixture = json.loads(path.read_text(encoding="utf-8"))
        names = {item["name"] for item in fixture["scenarios"]}

        self.assertEqual(
            names,
            {
                "two-independent-commands",
                "dependency-failure-isolation",
                "exclusive-waiter-across-two-runs",
                "stop-recovery-and-surviving-worker",
                "legacy-run-cutover-compatibility",
            },
        )

    def test_independent_ordinary_permits_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            run_a = project / "tmp/run-a"
            run_b = project / "tmp/run-b"
            first = acquire_scheduling_permit(
                project,
                run_a,
                "reproduce-run-a",
                _planned(1, exclusive=False, leaf="a"),
                stop_requested=lambda: False,
            )
            second = acquire_scheduling_permit(
                project,
                run_b,
                "reproduce-run-b",
                _planned(2, exclusive=False, leaf="b"),
                stop_requested=lambda: False,
            )
            assert first is not None and second is not None
            state = json.loads(
                (
                    operation_directory(project) / "reproduction-scheduler.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(len(state["active"]), 2)
            release_scheduling_permit(first)
            release_scheduling_permit(second)
            self.assertFalse(
                (operation_directory(project) / "reproduction-scheduler.json").exists()
            )

    def test_claims_allow_shared_reads_but_block_every_mutable_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            run_a = project / "tmp/run-a"
            run_b = project / "tmp/run-b"
            first = _planned(1, exclusive=False, leaf="a")
            shared = str((project / "origins/shared.csv").resolve())
            first["read_paths"] = [shared]
            permit = {
                "kind": "ordinary",
                "read_paths": [shared],
                "write_paths": [str((run_a / "workspace/data/a.csv").resolve())],
                "writable_paths": [str((run_a / "runtime/e001/a").resolve())],
                "run_path": str((run_a / "executions/e001/a").resolve()),
            }
            second = _planned(2, exclusive=False, leaf="b")
            second["read_paths"] = [shared]
            self.assertFalse(_claims_conflict(permit, second, run_b, project))

            for conflict in (
                permit["write_paths"][0],
                permit["writable_paths"][0],
                permit["run_path"],
            ):
                candidate = _planned(2, exclusive=False, leaf="b")
                candidate["read_paths"] = [conflict]
                self.assertTrue(
                    _claims_conflict(permit, candidate, run_b, project), conflict
                )

    def test_dead_supervisor_retains_unproved_active_permit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state: dict[str, object] = {
                "schema": "research-log-reproduction-scheduler/1",
                "next_ticket": 0,
                "waiters": [],
                "active": [{"supervisor_pid": 2**30}],
            }

            unresolved = _remove_dead(Path(directory), state)

        self.assertEqual(len(cast(list[object], state["active"])), 1)
        self.assertEqual(list(unresolved), state["active"])

    def test_dead_unreconciled_permit_refuses_instead_of_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            permit = acquire_scheduling_permit(
                project,
                project / "tmp/run-a",
                "reproduce-run-a",
                _planned(1, exclusive=False, leaf="a"),
                stop_requested=lambda: False,
            )
            assert permit is not None
            scheduler = operation_directory(project) / "reproduction-scheduler.json"
            state = json.loads(scheduler.read_text(encoding="utf-8"))
            state["active"][0]["supervisor_pid"] = 2**30
            scheduler.write_text(
                json.dumps(state, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ActionError) as raised:
                acquire_scheduling_permit(
                    project,
                    project / "tmp/run-b",
                    "reproduce-run-b",
                    _planned(2, exclusive=True, leaf="b"),
                    stop_requested=lambda: False,
                )

            self.assertEqual(
                raised.exception.code,
                "reproduction.scheduler.reconciliation_required",
            )

    def test_malformed_coordinator_item_refuses_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            permit = acquire_scheduling_permit(
                project,
                project / "tmp/run-a",
                "reproduce-run-a",
                _planned(1, exclusive=False, leaf="a"),
                stop_requested=lambda: False,
            )
            assert permit is not None
            scheduler = operation_directory(project) / "reproduction-scheduler.json"
            state = json.loads(scheduler.read_text(encoding="utf-8"))
            state["active"][0]["supervisor_pid"] = None
            scheduler.write_text(
                json.dumps(state, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ActionError):
                acquire_scheduling_permit(
                    project,
                    project / "tmp/run-b",
                    "reproduce-run-b",
                    _planned(2, exclusive=False, leaf="b"),
                    stop_requested=lambda: False,
                )

    def test_exclusive_waiter_closes_ordinary_admission_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            ordinary = acquire_scheduling_permit(
                project,
                project / "tmp/run-a",
                "reproduce-run-a",
                _planned(1, exclusive=False, leaf="a"),
                stop_requested=lambda: False,
            )
            assert ordinary is not None
            exclusive_granted = threading.Event()
            release_exclusive = threading.Event()
            later_ordinary_granted = threading.Event()

            def exclusive_worker() -> None:
                permit = acquire_scheduling_permit(
                    project,
                    project / "tmp/run-b",
                    "reproduce-run-b",
                    _planned(2, exclusive=True, leaf="exclusive"),
                    stop_requested=lambda: False,
                )
                assert permit is not None
                exclusive_granted.set()
                release_exclusive.wait(5)
                release_scheduling_permit(permit)

            def ordinary_worker() -> None:
                permit = acquire_scheduling_permit(
                    project,
                    project / "tmp/run-c",
                    "reproduce-run-c",
                    _planned(3, exclusive=False, leaf="c"),
                    stop_requested=lambda: False,
                )
                assert permit is not None
                later_ordinary_granted.set()
                release_scheduling_permit(permit)

            exclusive_thread = threading.Thread(target=exclusive_worker)
            exclusive_thread.start()
            scheduler = operation_directory(project) / "reproduction-scheduler.json"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = json.loads(scheduler.read_text(encoding="utf-8"))
                if state["waiters"]:
                    break
                time.sleep(0.02)
            else:
                self.fail("exclusive waiter was not registered")
            ordinary_thread = threading.Thread(target=ordinary_worker)
            ordinary_thread.start()
            time.sleep(0.2)
            self.assertFalse(later_ordinary_granted.is_set())

            release_scheduling_permit(ordinary)
            self.assertTrue(exclusive_granted.wait(5))
            self.assertFalse(later_ordinary_granted.is_set())
            release_exclusive.set()
            self.assertTrue(later_ordinary_granted.wait(5))
            exclusive_thread.join(5)
            ordinary_thread.join(5)
            self.assertFalse(exclusive_thread.is_alive())
            self.assertFalse(ordinary_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
