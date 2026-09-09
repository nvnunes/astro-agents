from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence, cast
from unittest import mock

from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan, source_snapshot
from log_commands.reproduction_execution import (
    ConfinementBackend,
    DarwinSeatbelt,
    ExecutionAttempt,
    ExecutionCheckpoint,
    ExecutionControl,
    ReproductionControlPlaneError,
    _generated_output_paths,
    _load_checkpoint,
    _output_already_materialized,
    _seatbelt_profile,
    _write_checkpoint,
    completed_execution_attempts,
    execute_planned_recipe,
    execute_reproduction_plan,
    prepare_output_workspace,
)
from research_log_data import Fingerprint
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    execution_id,
    load_pyrun_state,
)


class _FixtureConfinement(ConfinementBackend):
    def __init__(self) -> None:
        self.writable_roots: tuple[Path, ...] = ()

    def preflight(self) -> None:
        pass

    def command(
        self,
        command: Sequence[str],
        *,
        writable_roots: Sequence[Path],
        readonly_paths: Sequence[tuple[Path, str]],
    ) -> list[str]:
        self.writable_roots = tuple(writable_roots)
        del readonly_paths
        return list(command)


def _fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", digest=hashlib.sha256(path.read_bytes()).hexdigest())


class _Fixture:
    def __init__(self, root: Path, script_text: str):
        self.project = root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        (self.project / "tmp").mkdir()
        environment = self.project / ".conda" / "bin"
        environment.mkdir(parents=True)
        (environment / "python").symlink_to(Path(sys.executable).resolve())
        self.summary = self.project / "docs" / "study.md"
        self.summary.parent.mkdir()
        self.summary.write_text("# Study\n", encoding="utf-8")
        self.log_root = self.summary.with_suffix("")
        self.entry_root = (
            self.log_root / "entries" / "2026-09-06-e001-controlled-fixture"
        )
        (self.entry_root / "data").mkdir(parents=True)
        (self.entry_root / "scripts").mkdir()
        self.script = self.entry_root / "scripts" / "produce.py"
        self.script.write_text(script_text, encoding="utf-8")
        self.source = self.entry_root / "data" / "source.txt"
        self.source.write_text("source\n", encoding="utf-8")
        self.output = self.entry_root / "data" / "result.txt"
        self.output.write_text("retained\n", encoding="utf-8")
        self.data = {
            "inputs": [
                {
                    "fingerprint": _fingerprint(self.source).as_dict(),
                    "kind": "file",
                    "location": "data/source.txt",
                    "name": "source",
                    "origin": True,
                }
            ],
            "schema": "research-log-data/v3",
        }
        (self.entry_root / "data.json").write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        recipe = ExecutionRecipe(
            "scripts/produce.py",
            ("--source", "<source>", "--output", "data/result.txt"),
            (),
            ("source",),
            (("data/result.txt", "file"),),
        )
        observed = ObservedExecution(
            _fingerprint(self.script),
            (("source", _fingerprint(self.source)),),
            (),
            (("data/result.txt", _fingerprint(self.output)),),
        )
        self.identity = execution_id(recipe)
        execution = PyrunExecution(
            False,
            True,
            None,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
            recipe,
            observed,
        )
        state = PyrunFile(
            self.entry_root / "pyrun.json", self.entry_root, {self.identity: execution}
        )
        (self.entry_root / "pyrun.json").write_text(
            state.serialized(), encoding="utf-8"
        )
        self.log = LogContext(self.summary.resolve(), self.log_root.resolve())
        self.plan = ReproductionPlan(
            "docs/study.md",
            {"entry": "e001", "kind": "entry"},
            False,
            {},
            source_snapshot(
                authority_files=(),
                executions=(),
                materials=(
                    {
                        "fingerprint": _fingerprint(self.source).as_dict(),
                        "identity": str(self.source.resolve()),
                        "kind": "file",
                        "role": "boundary",
                    },
                ),
            ),
            (),
            (),
            (),
            (),
        )
        self.planned = {"entry": "e001", "execution_id": self.identity}

    def workspace(self):
        suffix = hashlib.sha256(str(self.project).encode()).hexdigest()[:12]
        run_id = f"reproduce-controlled-{suffix}"
        return prepare_output_workspace(
            self.project,
            self.project / "tmp" / "reproduction" / "2026-09-06" / run_id,
            run_id,
        )


class ReproductionExecutionTests(unittest.TestCase):
    def test_checkpoint_writer_uses_reserved_atomic_temporary_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            identity = "pyrun-exec/v1:" + "1" * 64
            name = "e001-" + "1" * 64 + ".json"
            path = checkpoints / name
            checkpoint = ExecutionCheckpoint(
                "e001",
                identity,
                "active",
                f"checkpoints/{name}",
                None,
                (),
            )

            _write_checkpoint(path, checkpoint)

            self.assertTrue(path.is_file())
            self.assertEqual(list(checkpoints.glob(".*.tmp")), [])

    def test_plan_jobs_caps_parallel_ready_executions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            identities = tuple(
                "pyrun-exec/v1:" + str(index) * 64 for index in range(1, 5)
            )
            plan = replace(
                fixture.plan,
                jobs=2,
                executions=tuple(
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": identity,
                        "order": index,
                        "outputs": [f"data/{index}.txt"],
                        "auto_reproduce": True,
                        "exclusive": False,
                    }
                    for index, identity in enumerate(identities, 1)
                ),
            )
            lock = threading.Lock()
            active = 0
            maximum = 0

            def execute(*args, **kwargs):
                nonlocal active, maximum
                planned = args[2]
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.1)
                with lock:
                    active -= 1
                identity = planned["execution_id"]
                checkpoint = ExecutionCheckpoint(
                    "e001", identity, "succeeded", "checkpoint.json", "now", ()
                )
                return ExecutionAttempt(
                    "e001", identity, 0, False, None, None, checkpoint, (), "", ""
                )

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution._generated_output_paths",
                    return_value={},
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe",
                    side_effect=execute,
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.acquire_scheduling_permit",
                    return_value=SimpleNamespace(),
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_scheduling_permit"
                ),
            ):
                result = execute_reproduction_plan(fixture.log, plan, workspace)

            self.assertEqual(maximum, 2)
            self.assertEqual(len(result.attempts), 4)

    def test_generated_output_projection_loads_each_entry_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            executions = tuple(
                {
                    "entry": "e001",
                    "execution_id": fixture.identity,
                    "order": order,
                }
                for order in range(1, 4)
            )
            plan = replace(fixture.plan, executions=executions)

            with mock.patch(
                "log_commands.reproduction_execution.load_pyrun_state",
                wraps=load_pyrun_state,
            ) as loader:
                projected = _generated_output_paths(fixture.log, plan, workspace)

            self.assertEqual(loader.call_count, 1)
            self.assertEqual(len(projected), 1)

    def test_completed_attempts_reuse_loaded_entry_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(
                Path(directory),
                "import argparse\n"
                "from pathlib import Path\n"
                "p=argparse.ArgumentParser(); p.add_argument('--source'); "
                "p.add_argument('--output'); a=p.parse_args()\n"
                "Path(a.output).write_text(Path(a.source).read_text())\n",
            )
            plan = replace(
                fixture.plan,
                executions=(
                    {
                        "entry": "e001",
                        "execution_id": fixture.identity,
                        "order": 1,
                    },
                ),
            )
            workspace = fixture.workspace()
            attempt = execute_planned_recipe(
                fixture.log,
                plan,
                plan.executions[0],
                workspace,
                ExecutionControl(confinement=_FixtureConfinement()),
            )
            self.assertEqual(attempt.checkpoint.state, "succeeded")

            with mock.patch(
                "log_commands.reproduction_execution.load_pyrun_state",
                wraps=load_pyrun_state,
            ) as loader:
                completed = completed_execution_attempts(fixture.log, plan, workspace)

            self.assertEqual(loader.call_count, 1)
            self.assertEqual(len(completed), 1)

    def test_output_workspace_does_not_copy_retained_project_material(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            (fixture.project / ".cache").mkdir()
            (fixture.project / ".cache" / "old").write_text("old")

            workspace = fixture.workspace()

            self.assertFalse(workspace.map_source(fixture.script).exists())
            self.assertFalse((workspace.work_project / ".git").exists())
            self.assertFalse((workspace.work_project / ".conda").exists())
            self.assertFalse((workspace.work_project / ".cache").exists())
            self.assertFalse((workspace.work_project / "tmp").exists())
            self.assertFalse(workspace.staging_root.exists())

    def test_seatbelt_profile_denies_by_default_and_protects_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            profile = _seatbelt_profile(
                (root / "work", root / "runtime"),
                ((root / "work" / "source.txt", "file"),),
            )

            self.assertIn("(deny default)", profile)
            self.assertNotIn("allow network", profile)
            self.assertIn(f'(allow file-write* (subpath "{root / "work"}"))', profile)
            self.assertIn(
                f'(deny file-write* (literal "{root / "work" / "source.txt"}"))',
                profile,
            )

    def test_executes_recorded_recipe_without_changing_retained_artifacts(self) -> None:
        script = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text(Path(a.source).read_text().upper())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            retained_before = fixture.output.read_bytes()
            workspace = fixture.workspace()

            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(confinement=_FixtureConfinement()),
            )

            self.assertEqual(attempt.checkpoint.state, "succeeded")
            self.assertIsNone(attempt.failure_code)
            self.assertIsNotNone(attempt.checkpoint.started_at)
            self.assertIsNotNone(attempt.checkpoint.finished_at)
            self.assertIsNotNone(attempt.checkpoint.elapsed_seconds)
            self.assertGreaterEqual(attempt.checkpoint.elapsed_seconds or -1, 0)
            self.assertEqual(fixture.output.read_bytes(), retained_before)
            copied = workspace.map_source(fixture.output)
            self.assertEqual(copied.read_text(), "SOURCE\n")
            self.assertTrue((workspace.run_root / attempt.checkpoint.path).is_file())

    def test_attempt_uses_private_entry_runtime_and_diagnostic_roots(self) -> None:
        script = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path('scratch.txt').write_text('private')\n"
            "Path(a.output).write_text(Path(a.source).read_text())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()
            confinement = _FixtureConfinement()

            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(confinement=confinement),
            )

            tail = fixture.identity.rsplit(":", 1)[-1]
            attempt_root = workspace.staging_root / "e001" / tail
            relative_entry = fixture.entry_root.relative_to(fixture.project)
            self.assertEqual(attempt.checkpoint.state, "succeeded")
            self.assertEqual(
                (attempt_root / relative_entry / "scratch.txt").read_text(),
                "private",
            )
            self.assertFalse(
                (workspace.map_source(fixture.entry_root) / "scratch.txt").exists()
            )
            self.assertEqual(
                set(confinement.writable_roots),
                {
                    attempt_root,
                    workspace.runtime_root / "e001" / tail,
                    workspace.diagnostics_root / "e001" / tail,
                },
            )
            self.assertNotIn(workspace.work_project, confinement.writable_roots)

    def test_exception_after_permit_writes_failed_checkpoint_before_release(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            planned = {
                "depends_on": [],
                "entry": "e001",
                "execution_id": fixture.identity,
                "order": 1,
                "outputs": ["data/result.txt"],
                "auto_reproduce": True,
                "exclusive": False,
            }
            plan = replace(fixture.plan, jobs=1, executions=(planned,))

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe",
                    side_effect=ActionError("fixture.failure", "failed before launch"),
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.acquire_scheduling_permit",
                    return_value=SimpleNamespace(),
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_scheduling_permit"
                ) as release,
            ):
                batch = execute_reproduction_plan(fixture.log, plan, workspace)

            self.assertEqual(batch.attempts[0].checkpoint.state, "failed")
            self.assertEqual(batch.attempts[0].failure_code, "fixture.failure")
            checkpoint = workspace.run_root / batch.attempts[0].checkpoint.path
            self.assertEqual(json.loads(checkpoint.read_text())["state"], "failed")
            release.assert_called_once()

    def test_progress_failure_before_launch_is_control_plane_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            planned = {
                "depends_on": [],
                "entry": "e001",
                "execution_id": fixture.identity,
                "order": 1,
                "outputs": ["data/result.txt"],
                "auto_reproduce": True,
                "exclusive": False,
            }
            plan = replace(fixture.plan, jobs=1, executions=(planned,))

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe"
                ) as execute,
                mock.patch(
                    "log_commands.reproduction_scheduler.acquire_scheduling_permit",
                    return_value=SimpleNamespace(),
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_scheduling_permit"
                ) as release,
            ):
                with self.assertRaisesRegex(ActionError, "progress failed"):
                    execute_reproduction_plan(
                        fixture.log,
                        plan,
                        workspace,
                        ExecutionControl(
                            progress=mock.Mock(
                                side_effect=ActionError(
                                    "fixture.progress", "progress failed"
                                )
                            )
                        ),
                    )

            execute.assert_not_called()
            self.assertEqual(
                list((workspace.run_root / "checkpoints").glob("*.json")), []
            )
            release.assert_called_once()

    def test_worker_callback_failure_stops_launched_child_and_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            process = SimpleNamespace(pid=4217)
            launched = SimpleNamespace(process=process, pumps=(), stream_errors=[])

            with (
                mock.patch(
                    "log_commands.reproduction_execution._launch_process",
                    return_value=launched,
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.register_root"
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.records",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.stop_all",
                    return_value=(),
                ) as stop_all,
            ):
                with self.assertRaisesRegex(
                    ReproductionControlPlaneError, "worker state failed"
                ):
                    execute_planned_recipe(
                        fixture.log,
                        fixture.plan,
                        fixture.planned,
                        workspace,
                        ExecutionControl(
                            confinement=_FixtureConfinement(),
                            worker_progress=mock.Mock(
                                side_effect=ActionError(
                                    "fixture.worker_state", "worker state failed"
                                )
                            ),
                        ),
                    )

            stop_all.assert_called_once()
            checkpoint = json.loads(
                next((workspace.run_root / "checkpoints").glob("*.json")).read_text()
            )
            self.assertEqual(checkpoint["state"], "active")
            self.assertIsNotNone(checkpoint["started_at"])

    def test_checkpoint_persistence_failure_stops_launched_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            launched = SimpleNamespace(
                process=SimpleNamespace(pid=4218), pumps=(), stream_errors=[]
            )

            with (
                mock.patch(
                    "log_commands.reproduction_execution._launch_process",
                    return_value=launched,
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.stop_all",
                    return_value=(),
                ) as stop_all,
                mock.patch(
                    "log_commands.reproduction_execution.os.replace",
                    side_effect=OSError("checkpoint write failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    ReproductionControlPlaneError, "checkpoint write failed"
                ):
                    execute_planned_recipe(
                        fixture.log,
                        fixture.plan,
                        fixture.planned,
                        workspace,
                        ExecutionControl(confinement=_FixtureConfinement()),
                    )

            stop_all.assert_called_once()
            self.assertEqual(
                list((workspace.run_root / "checkpoints").glob("*.json")), []
            )

    def test_prelaunch_failure_has_no_synthetic_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            with (
                mock.patch(
                    "log_commands.reproduction_execution._launch_process",
                    side_effect=OSError("launch failed"),
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.stop_all",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_execution._WorkerRegistry.records",
                    return_value=(),
                ),
            ):
                attempt = execute_planned_recipe(
                    fixture.log,
                    fixture.plan,
                    fixture.planned,
                    workspace,
                    ExecutionControl(confinement=_FixtureConfinement()),
                )

            self.assertEqual(attempt.checkpoint.state, "failed")
            self.assertIsNone(attempt.checkpoint.started_at)
            self.assertIsNone(attempt.checkpoint.finished_at)
            self.assertIsNone(attempt.checkpoint.elapsed_seconds)
            loaded = _load_checkpoint(workspace, "e001", fixture.identity, legacy=False)
            self.assertIsNotNone(loaded)
            self.assertIsNone(cast(ExecutionCheckpoint, loaded).elapsed_seconds)

    def test_v3_loader_rejects_legacy_checkpoint_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            checkpoint = (
                workspace.run_root
                / "checkpoints"
                / ("e001-" + fixture.identity.rsplit(":", 1)[-1] + ".json")
            )
            value = ExecutionCheckpoint(
                "e001",
                fixture.identity,
                "partial",
                checkpoint.relative_to(workspace.run_root).as_posix(),
                None,
                (),
            ).as_dict()
            value.pop("failure")
            checkpoint.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ActionError, "checkpoint fields"):
                _load_checkpoint(workspace, "e001", fixture.identity, legacy=False)
            self.assertEqual(
                cast(
                    ExecutionCheckpoint,
                    _load_checkpoint(workspace, "e001", fixture.identity, legacy=True),
                ).state,
                "partial",
            )

    def test_checkpoint_loader_rejects_noncanonical_outputs_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            checkpoint = (
                workspace.run_root
                / "checkpoints"
                / ("e001-" + fixture.identity.rsplit(":", 1)[-1] + ".json")
            )
            output = {
                "artifact": "data/result.txt",
                "fingerprint": _fingerprint(fixture.output).as_dict(),
            }
            value = ExecutionCheckpoint(
                "e001",
                fixture.identity,
                "failed",
                checkpoint.relative_to(workspace.run_root).as_posix(),
                None,
                (output, output),
                failure={
                    "code": "fixture.failed",
                    "message": "failed",
                    "recorded_at": "2030-01-01T00:00:00Z",
                },
            ).as_dict()
            checkpoint.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ActionError, "outputs are not canonical"):
                _load_checkpoint(workspace, "e001", fixture.identity, legacy=False)

            value["outputs"] = []
            value["failure"] = {
                "code": "",
                "message": "failed",
                "recorded_at": "not-a-time",
            }
            checkpoint.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ActionError, "failure is invalid"):
                _load_checkpoint(workspace, "e001", fixture.identity, legacy=False)

    def test_directory_materialization_never_removes_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            target = root / "target"
            private.mkdir()
            target.mkdir()
            (private / "value.txt").write_text("new\n", encoding="utf-8")
            retained = target / "value.txt"
            retained.write_text("retained\n", encoding="utf-8")

            with self.assertRaisesRegex(OSError, "cannot be atomically replaced"):
                _output_already_materialized(
                    private, target, "data/result", "directory"
                )

            self.assertEqual(retained.read_text(encoding="utf-8"), "retained\n")

    def test_legacy_execution_preserves_v2_checkpoint_and_workspace_shape(self) -> None:
        script = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text(Path(a.source).read_text())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()
            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(confinement=_FixtureConfinement(), legacy=True),
            )

            checkpoint = json.loads(
                (workspace.run_root / attempt.checkpoint.path).read_text()
            )
            self.assertEqual(attempt.checkpoint.state, "complete")
            self.assertNotIn("failure", checkpoint)
            self.assertEqual(
                workspace.map_source(fixture.output).read_text(), "source\n"
            )

    def test_captures_declared_stream_directly_in_output_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('captured')\n")
            recipe = ExecutionRecipe(
                "scripts/produce.py",
                ("--capture-stdout", "data/result.txt", "--"),
                (),
                (),
                (("data/result.txt", "file"),),
            )
            identity = execution_id(recipe)
            execution = PyrunExecution(
                False,
                True,
                None,
                "research-log-pyrun-runner/1",
                "pyrun-standard/v1",
                "research-log-pyrun-execution/2",
                recipe,
                ObservedExecution(
                    _fingerprint(fixture.script),
                    (),
                    (),
                    (("data/result.txt", _fingerprint(fixture.output)),),
                ),
            )
            state = PyrunFile(
                fixture.entry_root / "pyrun.json",
                fixture.entry_root,
                {identity: execution},
            )
            (fixture.entry_root / "pyrun.json").write_text(
                state.serialized(), encoding="utf-8"
            )
            workspace = fixture.workspace()

            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                {"entry": "e001", "execution_id": identity},
                workspace,
                ExecutionControl(confinement=_FixtureConfinement()),
            )

            self.assertEqual(attempt.checkpoint.state, "succeeded")
            self.assertEqual(
                workspace.map_source(fixture.output).read_text(), "captured\n"
            )
            self.assertEqual(fixture.output.read_text(), "retained\n")

    def test_ambiguous_output_argument_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "raise RuntimeError('ran')\n")
            recipe = ExecutionRecipe(
                "scripts/produce.py",
                (
                    "--output",
                    "data/result.txt",
                    "--duplicate",
                    "data/result.txt",
                ),
                (),
                (),
                (("data/result.txt", "file"),),
            )
            identity = execution_id(recipe)
            execution = PyrunExecution(
                False,
                True,
                None,
                "research-log-pyrun-runner/1",
                "pyrun-standard/v1",
                "research-log-pyrun-execution/2",
                recipe,
                ObservedExecution(
                    _fingerprint(fixture.script),
                    (),
                    (),
                    (("data/result.txt", _fingerprint(fixture.output)),),
                ),
            )
            state = PyrunFile(
                fixture.entry_root / "pyrun.json",
                fixture.entry_root,
                {identity: execution},
            )
            (fixture.entry_root / "pyrun.json").write_text(
                state.serialized(), encoding="utf-8"
            )
            workspace = fixture.workspace()

            with self.assertRaisesRegex(ActionError, "pyrun.output.binding_invalid"):
                execute_planned_recipe(
                    fixture.log,
                    fixture.plan,
                    {"entry": "e001", "execution_id": identity},
                    workspace,
                    ExecutionControl(confinement=_FixtureConfinement()),
                )

            self.assertFalse(workspace.map_source(fixture.output).exists())
            self.assertEqual(
                list((workspace.run_root / "checkpoints").glob("*.json")), []
            )

    def test_failed_execution_records_terminal_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "raise SystemExit(7)\n")
            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                fixture.workspace(),
                ExecutionControl(confinement=_FixtureConfinement()),
            )

            self.assertEqual(attempt.failure_code, "execution_failed")
            self.assertEqual(attempt.checkpoint.state, "failed")
            self.assertIsNotNone(attempt.checkpoint.started_at)
            self.assertIsNotNone(attempt.checkpoint.finished_at)
            self.assertGreaterEqual(attempt.checkpoint.elapsed_seconds or -1, 0)

    def test_downstream_input_uses_regenerated_upstream_path(self) -> None:
        producer = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text(Path(a.source).read_text().upper())\n"
        )
        consumer = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--input'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text(Path(a.input).read_text() + 'downstream\\n')\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), producer)
            final = fixture.entry_root / "data" / "final.txt"
            final.write_text("retained final\n", encoding="utf-8")
            consume = fixture.entry_root / "scripts" / "consume.py"
            consume.write_text(consumer, encoding="utf-8")
            cast(list[object], fixture.data["inputs"]).append(
                {
                    "fingerprint": _fingerprint(fixture.output).as_dict(),
                    "kind": "file",
                    "location": "data/result.txt",
                    "name": "generated",
                    "origin": False,
                }
            )
            (fixture.entry_root / "data.json").write_text(
                json.dumps(fixture.data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            consumer_recipe = ExecutionRecipe(
                "scripts/consume.py",
                ("--input", "<generated>", "--output", "data/final.txt"),
                (),
                ("generated",),
                (("data/final.txt", "file"),),
            )
            consumer_id = execution_id(consumer_recipe)
            consumer_execution = PyrunExecution(
                False,
                True,
                None,
                "research-log-pyrun-runner/1",
                "pyrun-standard/v1",
                "research-log-pyrun-execution/2",
                consumer_recipe,
                ObservedExecution(
                    _fingerprint(consume),
                    (("generated", _fingerprint(fixture.output)),),
                    (),
                    (("data/final.txt", _fingerprint(final)),),
                ),
            )
            state = load_pyrun_state(
                fixture.entry_root / "pyrun.json",
                entry_root=fixture.entry_root,
                project_root=fixture.project,
            )
            executions = dict(state.executions)
            executions[consumer_id] = consumer_execution
            (fixture.entry_root / "pyrun.json").write_text(
                PyrunFile(state.path, state.entry_root, executions).serialized(),
                encoding="utf-8",
            )
            first_reference = f"e001:{fixture.identity}"
            plan = replace(
                fixture.plan,
                executions=(
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": fixture.identity,
                        "order": 1,
                        "outputs": ["data/result.txt"],
                        "auto_reproduce": True,
                    },
                    {
                        "depends_on": [first_reference],
                        "entry": "e001",
                        "execution_id": consumer_id,
                        "order": 2,
                        "outputs": ["data/final.txt"],
                        "auto_reproduce": True,
                    },
                ),
            )
            workspace = fixture.workspace()

            with mock.patch(
                "log_commands.reproduction_planner.verify_reproduction_runtime_snapshot"
            ):
                result = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(confinement=_FixtureConfinement()),
                )

            self.assertEqual(len(result.attempts), 2)
            failures = [
                (workspace.run_root / attempt.stderr).read_text()
                for attempt in result.attempts
                if attempt.failure_code is not None
            ]
            self.assertEqual(
                [
                    (attempt.checkpoint.state, attempt.failure_code)
                    for attempt in result.attempts
                ],
                [("succeeded", None), ("succeeded", None)],
                failures,
            )
            self.assertEqual(
                workspace.map_source(final).read_text(), "SOURCE\ndownstream\n"
            )
            self.assertEqual(fixture.output.read_text(), "retained\n")

    @unittest.skipUnless(
        sys.platform == "darwin" and os.environ.get("REPRODUCTION_SANDBOX_TEST") == "1",
        "requires an explicitly enabled host Seatbelt test",
    )
    def test_host_confinement_denies_boundary_write_and_network(self) -> None:
        script = (
            "import argparse, socket\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "denied=[]\n"
            "try: Path(a.source).write_text('changed')\n"
            "except PermissionError: denied.append('write')\n"
            "s=socket.socket(); s.settimeout(0.1)\n"
            "try: s.connect(('127.0.0.1', 9))\n"
            "except PermissionError: denied.append('network')\n"
            "except OSError: pass\n"
            "Path(a.output).write_text(','.join(denied))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()

            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(confinement=DarwinSeatbelt()),
            )

            self.assertEqual(attempt.checkpoint.state, "succeeded")
            self.assertEqual(
                workspace.map_source(fixture.output).read_text(), "write,network"
            )
            self.assertEqual(fixture.source.read_text(), "source\n")

    def test_resume_reuses_partial_output_in_same_workspace(self) -> None:
        script = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "path=Path(a.output); prior=path.read_text() if path.exists() else ''\n"
            "path.write_text(prior + 'continued\\n')\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()
            copied = workspace.map_source(fixture.output)

            first = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(confinement=_FixtureConfinement()),
            )
            second = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(resume=True, confinement=_FixtureConfinement()),
            )

            self.assertEqual(first.checkpoint.state, "succeeded")
            self.assertEqual(second.checkpoint.state, "succeeded")
            self.assertEqual(second.checkpoint.started_at, first.checkpoint.started_at)
            self.assertGreaterEqual(
                second.checkpoint.elapsed_seconds or -1,
                first.checkpoint.elapsed_seconds or 0,
            )
            self.assertEqual(copied.read_text(), "continued\ncontinued\n")

    def test_stop_terminates_detached_descendant_and_keeps_partial_state(self) -> None:
        script = (
            "import argparse, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "child=subprocess.Popen([sys.executable, '-c', 'import time; "
            "time.sleep(60)'], start_new_session=True)\n"
            "Path(a.output).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()
            started = time.monotonic()

            attempt = execute_planned_recipe(
                fixture.log,
                fixture.plan,
                fixture.planned,
                workspace,
                ExecutionControl(
                    stop_requested=lambda: time.monotonic() - started > 0.4,
                    confinement=_FixtureConfinement(),
                ),
            )

            self.assertTrue(attempt.stopped)
            self.assertEqual(attempt.failure_code, "stop_requested")
            self.assertEqual(attempt.checkpoint.state, "stopped")
            self.assertIsNotNone(attempt.checkpoint.started_at)
            self.assertIsNone(attempt.checkpoint.finished_at)
            self.assertGreater(attempt.checkpoint.elapsed_seconds or 0, 0)
            self.assertGreaterEqual(len(attempt.workers), 2)
            self.assertTrue(all(worker.state == "exited" for worker in attempt.workers))

    def test_plan_continues_independent_work_and_skips_failed_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            other = "pyrun-exec/v1:" + "2" * 64
            dependent = "pyrun-exec/v1:" + "3" * 64
            failed_reference = f"e001:{fixture.identity}"
            plan = replace(
                fixture.plan,
                executions=(
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": fixture.identity,
                        "order": 1,
                        "outputs": ["data/result.txt"],
                        "auto_reproduce": True,
                    },
                    {
                        "depends_on": [failed_reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "order": 2,
                        "outputs": ["data/dependent.txt"],
                        "auto_reproduce": True,
                    },
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": other,
                        "order": 3,
                        "outputs": ["data/other.txt"],
                        "auto_reproduce": True,
                    },
                ),
            )

            def attempt(identity: str, state: str) -> ExecutionAttempt:
                checkpoint = ExecutionCheckpoint(
                    "e001", identity, state, "checkpoint.json", None, ()
                )
                return ExecutionAttempt(
                    "e001",
                    identity,
                    1 if state == "partial" else 0,
                    False,
                    "execution_failed" if state == "partial" else None,
                    "failed" if state == "partial" else None,
                    checkpoint,
                    (),
                    "stdout",
                    "stderr",
                )

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe",
                    side_effect=[
                        attempt(fixture.identity, "partial"),
                        attempt(other, "complete"),
                    ],
                ) as execute,
            ):
                result = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(confinement=_FixtureConfinement()),
                )

            self.assertEqual(execute.call_count, 2)
            self.assertEqual(
                [value.execution_id for value in result.attempts],
                [fixture.identity, other],
            )
            self.assertEqual(
                result.dependency_skips,
                (
                    {
                        "depends_on": [failed_reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "reason": "dependency_failed",
                    },
                ),
            )

    def test_plan_reuses_prior_failure_without_retrying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            independent = "pyrun-exec/v1:" + "2" * 64
            dependent = "pyrun-exec/v1:" + "3" * 64
            failed_reference = f"e001:{fixture.identity}"
            plan = replace(
                fixture.plan,
                executions=(
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": fixture.identity,
                        "order": 1,
                        "outputs": ["data/result.txt"],
                        "auto_reproduce": True,
                    },
                    {
                        "depends_on": [failed_reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "order": 2,
                        "outputs": ["data/dependent.txt"],
                        "auto_reproduce": True,
                    },
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": independent,
                        "order": 3,
                        "outputs": ["data/independent.txt"],
                        "auto_reproduce": True,
                    },
                ),
            )
            checkpoint = ExecutionCheckpoint(
                "e001", independent, "complete", "checkpoint.json", "now", ()
            )
            attempt = ExecutionAttempt(
                "e001",
                independent,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                "stdout",
                "stderr",
            )

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe",
                    return_value=attempt,
                ) as execute,
            ):
                result = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(
                        confinement=_FixtureConfinement(),
                        prior_failures=frozenset({failed_reference}),
                    ),
                )

            execute.assert_called_once()
            self.assertEqual(result.reused, (failed_reference,))
            self.assertEqual(
                result.dependency_skips,
                (
                    {
                        "depends_on": [failed_reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "reason": "dependency_failed",
                    },
                ),
            )

    def test_plan_reuses_prior_complete_attempt_without_checkpoint_recheck(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            reference = f"e001:{fixture.identity}"
            planned = {
                "depends_on": [],
                "entry": "e001",
                "execution_id": fixture.identity,
                "order": 1,
                "outputs": ["data/result.txt"],
                "auto_reproduce": True,
            }
            plan = replace(fixture.plan, executions=(planned,))

            with (
                mock.patch(
                    "log_commands.reproduction_planner."
                    "verify_reproduction_runtime_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_execution.execute_planned_recipe"
                ) as execute,
            ):
                result = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(
                        confinement=_FixtureConfinement(),
                        prior_attempts=frozenset({reference}),
                    ),
                )

            execute.assert_not_called()
            self.assertEqual(result.attempts, ())
            self.assertEqual(result.reused, (reference,))

    def test_plan_resume_reuses_unchanged_complete_checkpoint(self) -> None:
        script = (
            "import argparse\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--source'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text('generated\\n')\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), script)
            workspace = fixture.workspace()
            planned = {
                "depends_on": [],
                "entry": "e001",
                "execution_id": fixture.identity,
                "order": 1,
                "outputs": ["data/result.txt"],
                "auto_reproduce": True,
            }
            execute_planned_recipe(
                fixture.log,
                fixture.plan,
                planned,
                workspace,
                ExecutionControl(confinement=_FixtureConfinement()),
            )
            plan = replace(fixture.plan, executions=(planned,))

            with mock.patch(
                "log_commands.reproduction_planner.verify_reproduction_runtime_snapshot"
            ):
                result = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(resume=True, confinement=_FixtureConfinement()),
                )

            self.assertEqual(result.attempts, ())
            self.assertEqual(result.reused, (f"e001:{fixture.identity}",))


if __name__ == "__main__":
    unittest.main()
