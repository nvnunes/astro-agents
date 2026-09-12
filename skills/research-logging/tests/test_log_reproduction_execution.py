"""Accepted invocation execution boundary coverage."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_contract import (
    ReproductionPlan,
    accepted_command,
    accepted_invocation,
)
from log_commands.reproduction_execution import (
    ExecutionControl,
    ReproductionWorkspace,
    _ProcessOutcome,
    execute_planned_recipe,
)
from reproduction_fixed_plan_test_support import accepted_plan
from test_log_reproduction_planning import _Fixture, _plan


class ReproductionExecutionTests(unittest.TestCase):
    def test_execution_requires_an_accepted_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            accepted_command(accepted_plan(), "e001", "pyrun-exec/v1:" + "0" * 64)

    def test_plan_never_carries_attempt_lineage(self) -> None:
        self.assertNotIn("attempt", accepted_plan().serialized())

    def test_reader_rejects_noncanonical_plan_bytes_before_execution(self) -> None:
        raw = json.dumps(accepted_plan().as_dict(), indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            ReproductionPlan.from_json(raw)

    def test_accepted_invocation_does_not_reload_current_entry_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)
            with mock.patch(
                "log_commands.reproduction_contract.load_pyrun_state",
                side_effect=AssertionError("must not reload pyrun"),
                create=True,
            ):
                accepted = accepted_invocation(plan, entry.id, identity)
            self.assertEqual(accepted.execution_id, identity)

    def test_changed_script_after_stop_blocks_fixed_plan_invocation_without_replanning(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)
            source_project = fixture.root.resolve()
            interpreter = source_project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            run_root = source_project / "run"
            workspace = ReproductionWorkspace(
                "reproduce-source-change",
                run_root,
                source_project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
                run_root / "checkpoints",
            ):
                path.mkdir(parents=True)

            # This is the stopped run's original command.  Resume must use the
            # frozen recipe and reject the edit before a child can launch.
            (entry.root / "scripts" / "build.py").write_text(
                "# changed after stop\n", encoding="utf-8"
            )
            planned = next(
                item for item in plan.executions if item["execution_id"] == identity
            )
            with mock.patch(
                "log_commands.reproduction_execution._run_with_scratch"
            ) as execute:
                with self.assertRaisesRegex(ActionError, "script observation changed"):
                    execute_planned_recipe(
                        fixture.log,
                        plan,
                        planned,
                        workspace,
                        ExecutionControl(resume=True),
                    )
            execute.assert_not_called()

    def test_resume_clears_only_interrupted_invocation_workspace_output(self) -> None:
        """Completed sibling outputs remain untouched while B starts cleanly."""

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "b.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("accepted\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry, "b", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)
            source_project = fixture.root.resolve()
            interpreter = source_project / ".conda" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.symlink_to(sys.executable)
            run_root = source_project / "run"
            workspace = ReproductionWorkspace(
                "reproduce-resume",
                run_root,
                source_project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
                run_root / "checkpoints",
            ):
                path.mkdir(parents=True)
            stale_b = (
                workspace.staging_root
                / entry.id
                / identity.rsplit(":", 1)[-1]
                / output.resolve().relative_to(source_project)
            )
            stale_b.parent.mkdir(parents=True, exist_ok=True)
            stale_b.write_text("interrupted B\n", encoding="utf-8")
            completed_a = workspace.work_project / "a-complete.txt"
            completed_c = workspace.diagnostics_root / "c-complete.log"
            completed_a.write_text("A\n", encoding="utf-8")
            completed_c.write_text("C\n", encoding="utf-8")
            planned = next(
                item for item in plan.executions if item["execution_id"] == identity
            )

            def child(prepared: object, *_args: object) -> tuple[object, None, float]:
                self.assertEqual(
                    tuple(getattr(prepared, "output_paths").values()), (stale_b,)
                )
                self.assertFalse(stale_b.exists())
                return (
                    _ProcessOutcome(1, True, "stop_requested", "stopped", ()),
                    None,
                    0.0,
                )

            with mock.patch(
                "log_commands.reproduction_execution._run_with_scratch",
                side_effect=child,
            ):
                attempt = execute_planned_recipe(
                    fixture.log, plan, planned, workspace, ExecutionControl(resume=True)
                )

            self.assertTrue(attempt.stopped)
            self.assertEqual(completed_a.read_text(encoding="utf-8"), "A\n")
            self.assertEqual(completed_c.read_text(encoding="utf-8"), "C\n")
