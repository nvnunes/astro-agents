"""Accepted-plan job boundary coverage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_comparison import (
    ExecutionComparison,
    compare_execution_outputs,
    load_recorded_comparisons,
)
from log_commands.reproduction_contract import ReproductionRuntime
from log_commands.reproduction_execution import (
    ExecutionAttempt,
    ExecutionBatch,
    ExecutionCheckpoint,
    ExecutionControl,
    ReproductionControlPlaneError,
    ReproductionWorkspace,
    _checkpoint_path,
    _write_checkpoint,
    cleanup_reproduction_scratch,
    execute_reproduction_plan,
)
from log_commands.reproduction_jobs import (
    ACTIVE_PHASES,
    RUN_SCHEMA,
    TERMINAL_STATUSES,
    _acquire_scope_locks,
    _canonical,
    _clear_recovery_guard,
    _finish_stopped,
    _load_run,
    _publication_retry_skips,
    _status_projection,
    _verify_accepted_materials,
    _verify_checkpoint_inventory,
    dry_run_reproduction,
    launch_reproduction,
    load_accepted_plan,
    resume_reproduction,
    supervise_reproduction,
)
from log_commands.reproduction_result_storage import (
    initialize_empty_reproduction_results,
)
from reproduction_fixed_plan_test_support import accepted_plan, accepted_run
from research_log_data import InputResource, ResourceIdentity, observe_fingerprint
from research_log_paths import REPRODUCTION_REPORT, RESULTS_STORE
from research_log_result_store import clear_result_store, result_generation
from test_log_reproduction_planning import _Fixture
from validation.operation_state import operation_lock
from validation.pyrun_state import load_pyrun_state


class ReproductionJobTests(unittest.TestCase):
    def test_dry_run_keeps_validation_result_state_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            fixture = _Fixture(project)
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
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )

            plan = dry_run_reproduction(
                fixture.log, entry=entry.id, include_all=False
            )

            self.assertEqual(plan.admission["validation_result_id"], "provisional")
            self.assertFalse((fixture.log_root / ".cache" / "results.sqlite").exists())
            self.assertFalse((fixture.log_root / "validation.md").exists())

    def test_launch_captures_validation_context_for_rerender(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            fixture = _Fixture(project)
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
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )
            with mock.patch(
                "log_commands.reproduction_jobs._spawn_supervisor"
            ) as handoff:
                launch_reproduction(fixture.log, entry=entry.id, include_all=False)
            run_root = handoff.call_args.args[1]
            plan = load_accepted_plan(run_root)
            self.assertNotEqual(plan.admission["validation_result_id"], "provisional")
            self.assertTrue((fixture.log_root / "validation.md").is_file())
            generation = result_generation(fixture.log_root, "validation")
            fixture.summary.write_text("# Changed\n", encoding="utf-8")
            from log_commands.inspection_cli import _render
            from validation.result_storage import load_validation_report_projection

            _render(fixture.log, "validation")
            projection = load_validation_report_projection(fixture.log_root)
            self.assertEqual(
                projection.stored.result_id,
                plan.admission["validation_result_id"],
            )
            self.assertEqual(
                result_generation(fixture.log_root, "validation"), generation
            )

    def test_fixed_plan_stop_resume_preserves_completed_branches_and_waits_for_b(
        self,
    ) -> None:
        """The fixed scheduler preserves A/C and gates consumer on B's comparison."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            outputs = {
                name: entry.root / "data" / f"{name}.txt"
                for name in ("a", "b", "c", "consumer")
            }
            raw.write_text("raw\n", encoding="utf-8")
            for path in outputs.values():
                path.write_text(f"{path.stem}\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [fixture.item(entry, "raw", raw, origin=True)]
                + [
                    fixture.item(entry, name, path, origin=False)
                    for name, path in outputs.items()
                ],
            )
            fixture.evidence(entry, *outputs)
            a_id, a = fixture.execution(entry, "a", {"raw": raw}, {"a": outputs["a"]})
            b_id, b = fixture.execution(
                entry, "b", {"a": outputs["a"]}, {"b": outputs["b"]}
            )
            c_id, c = fixture.execution(entry, "c", {"raw": raw}, {"c": outputs["c"]})
            consumer_id, consumer = fixture.execution(
                entry,
                "consumer",
                {"b": outputs["b"]},
                {"consumer": outputs["consumer"]},
            )
            fixture.write_pyrun(
                entry, [(a_id, a), (b_id, b), (c_id, c), (consumer_id, consumer)]
            )
            from test_log_reproduction_planning import _plan

            plan = _plan(fixture, entry, runtime=ReproductionRuntime(jobs=2))
            root = project / "reproduce-fixed-plan"
            workspace = ReproductionWorkspace(
                "reproduce-fixed-plan",
                root,
                project,
                root / "workspace",
                root / "runtime",
                root / "diagnostics",
                root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
                root / "checkpoints",
            ):
                path.mkdir(parents=True, exist_ok=True)
            comparisons: dict[tuple[str, str], ExecutionComparison] = {}
            launched: list[str] = []

            def checkpoint(identity: str, state: str) -> ExecutionAttempt:
                path = _checkpoint_path(workspace, entry.id, identity)
                finished = "2030-01-01T00:00:01Z"
                outputs = ()
                if state == "succeeded":
                    output = outputs_by_identity[identity]
                    private = (
                        workspace.staging_root
                        / entry.id
                        / identity.rsplit(":", 1)[-1]
                        / entry.root.relative_to(project)
                        / output.relative_to(entry.root)
                    )
                    private.parent.mkdir(parents=True, exist_ok=True)
                    private.write_bytes(output.read_bytes())
                    materialized = workspace.map_source(output)
                    materialized.parent.mkdir(parents=True, exist_ok=True)
                    materialized.write_bytes(output.read_bytes())
                    outputs = (
                        {
                            "artifact": f"data/{output.name}",
                            "fingerprint": dict(
                                executions_by_identity[identity].observed.outputs
                            )[f"data/{output.name}"].as_dict(),
                        },
                    )
                value = ExecutionCheckpoint(
                    entry.id,
                    identity,
                    state,
                    path.relative_to(workspace.run_root).as_posix(),
                    finished if state == "succeeded" else None,
                    outputs,
                    "2030-01-01T00:00:00Z",
                    finished,
                    1.0,
                    (
                        None
                        if state == "succeeded"
                        else {
                            "code": "stop_requested",
                            "message": "stopped",
                            "recorded_at": finished,
                        }
                    ),
                )
                _write_checkpoint(path, value)
                diagnostic = workspace.diagnostics_root / f"{identity[-8:]}.log"
                diagnostic.write_text(f"{state}\n", encoding="utf-8")
                return ExecutionAttempt(
                    entry.id,
                    identity,
                    0 if state == "succeeded" else None,
                    state == "stopped",
                    "stop_requested" if state == "stopped" else None,
                    "stopped" if state == "stopped" else None,
                    value,
                    (),
                    diagnostic.relative_to(root).as_posix(),
                    diagnostic.relative_to(root).as_posix(),
                )

            outputs_by_identity = {
                a_id: outputs["a"],
                b_id: outputs["b"],
                c_id: outputs["c"],
                consumer_id: outputs["consumer"],
            }
            executions_by_identity = {
                a_id: a,
                b_id: b,
                c_id: c,
                consumer_id: consumer,
            }

            def durable_comparison(attempt: ExecutionAttempt) -> bool:
                value = compare_execution_outputs(
                    fixture.log, plan, workspace, attempt
                )
                comparisons[(attempt.entry, attempt.execution_id)] = value
                return value.matched

            first_pass = True

            def run_recipe(_context: object, planned: object) -> ExecutionAttempt:
                assert isinstance(planned, dict)
                identity = str(planned["execution_id"])
                launched.append(identity)
                if first_pass and identity == b_id:
                    suffix = identity.rsplit(":", 1)[-1]
                    incomplete = workspace.staging_root / entry.id / suffix
                    incomplete.mkdir(parents=True)
                    (incomplete / "partial.txt").write_text(
                        "partial\n", encoding="utf-8"
                    )
                    scratch = Path(
                        tempfile.mkdtemp(
                            prefix="reproduction-scratch-", dir="/private/tmp"
                        )
                    )
                    record = root / "scratch" / entry.id / f"{suffix}.json"
                    record.parent.mkdir(parents=True, exist_ok=True)
                    record.write_text(json.dumps(str(scratch)) + "\n", encoding="utf-8")
                    return checkpoint(identity, "stopped")
                return checkpoint(identity, "succeeded")

            with mock.patch(
                "log_commands.reproduction_execution._execute_scheduled_recipe",
                side_effect=run_recipe,
            ):
                first = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(attempt_completed=durable_comparison),
                )
                self.assertTrue(first.stopped)
                self.assertCountEqual(launched, [a_id, b_id, c_id])
                self.assertNotIn(consumer_id, launched)
                a_diagnostic = workspace.diagnostics_root / f"{a_id[-8:]}.log"
                c_diagnostic = workspace.diagnostics_root / f"{c_id[-8:]}.log"
                sentinels = (a_diagnostic.read_bytes(), c_diagnostic.read_bytes())
                incomplete = workspace.staging_root / entry.id / b_id.rsplit(":", 1)[-1]
                self.assertTrue(incomplete.exists())
                cleanup_reproduction_scratch(root)
                self.assertFalse(
                    (
                        root
                        / "scratch"
                        / entry.id
                        / f"{b_id.rsplit(':', 1)[-1]}.json"
                    ).exists()
                )
                first_pass = False
                launched.clear()
                prior_comparisons = load_recorded_comparisons(plan, workspace)
                self.assertEqual(
                    {(item.entry, item.execution_id) for item in prior_comparisons},
                    {(entry.id, a_id), (entry.id, b_id), (entry.id, c_id)},
                )
                second = execute_reproduction_plan(
                    fixture.log,
                    plan,
                    workspace,
                    ExecutionControl(
                        resume=True,
                        prior_attempts=frozenset(
                            f"{item.entry}:{item.execution_id}"
                            for item in prior_comparisons
                            if item.execution_id != b_id
                        ),
                        attempt_completed=durable_comparison,
                    ),
                )
            self.assertFalse(second.stopped)
            self.assertEqual(launched, [b_id, consumer_id])
            self.assertTrue(comparisons[(entry.id, b_id)].matched)
            self.assertFalse((incomplete / "partial.txt").exists())
            self.assertEqual(
                (a_diagnostic.read_bytes(), c_diagnostic.read_bytes()), sentinels
            )

    def test_publication_reobserves_selected_file_and_pattern_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "inputs"
            root.mkdir()
            selected = root / "selected.txt"
            selected.write_text("selected\n", encoding="utf-8")
            (root / "ignored.bin").write_bytes(b"ignored")
            files = InputResource(
                "files",
                "directory",
                str(root),
                ResourceIdentity("identity-files-sha256-v1", files=("selected.txt",)),
                True,
                str(root),
            )
            patterns = InputResource(
                "patterns",
                "directory",
                str(root),
                ResourceIdentity("identity-patterns-sha256-v1", patterns=("*.txt",)),
                True,
                str(root),
            )
            materials = [
                {
                    "identity": str(root),
                    "kind": "directory",
                    "role": "input",
                    "fingerprint": observe_fingerprint(resource).fingerprint.as_dict(),
                }
                for resource in (files, patterns)
            ]
            plan = replace(
                accepted_plan(),
                comparison_context={
                    "schema": "research-log-reproduction-comparison-context/1",
                    "comparisons": [],
                    "materials": materials,
                    "result_schema": "research-log-reproduction-result/10",
                },
            )
            _verify_accepted_materials(plan)
            (root / "ignored.bin").write_bytes(b"still ignored")
            _verify_accepted_materials(plan)
            selected.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "accepted material changed"):
                _verify_accepted_materials(plan)

    def test_publication_retry_requires_complete_terminal_accounting(self) -> None:
        first = "pyrun-exec/v1:" + "1" * 64
        second = "pyrun-exec/v1:" + "2" * 64
        plan = replace(
            accepted_plan(),
            executions=(
                {"entry": "e001", "execution_id": first, "depends_on": []},
                {
                    "entry": "e001",
                    "execution_id": second,
                    "depends_on": [f"e001:{first}"],
                },
            ),
        )
        failed = ExecutionComparison("e001", first, (), None, False)
        self.assertEqual(
            _publication_retry_skips(plan, {("e001", first): failed}),
            (
                {
                    "depends_on": [f"e001:{first}"],
                    "entry": "e001",
                    "execution_id": second,
                    "reason": "dependency_failed",
                },
            ),
        )
        with self.assertRaisesRegex(ActionError, "terminal accounting"):
            _publication_retry_skips(accepted_plan(), {})

    def test_recovery_guard_outlives_the_inspecting_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, _root = accepted_run(project)
            run_id = "reproduce-20300101t000000z-survivor"
            script = "\n".join(
                (
                    "from pathlib import Path",
                    "from log_commands.context import LogContext",
                    "from log_commands.reproduction_jobs import _write_recovery_guard",
                    "args = __import__('sys').argv",
                    "log = LogContext(Path(args[1]), Path(args[2]))",
                    "_write_recovery_guard(log, args[3], None, [{'pid': 1}])",
                )
            )
            environment = dict(os.environ)
            scripts = str(Path.cwd() / "skills/research-logging/scripts")
            environment["PYTHONPATH"] = os.pathsep.join(
                (scripts, environment.get("PYTHONPATH", ""))
            )
            subprocess.run(
                [sys.executable, "-c", script, str(log.summary), str(log.root), run_id],
                check=True,
                env=environment,
            )
            try:
                with self.assertRaisesRegex(ActionError, "orphaned worker cleanup"):
                    _acquire_scope_locks(log, None)
            finally:
                _clear_recovery_guard(log, run_id)

    def test_cleanup_error_preserves_its_scratch_record_for_recovery(self) -> None:
        """A malformed owned scratch record blocks cleanup instead of being lost."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "scratch" / "e001" / f"{'a' * 64}.json"
            record.parent.mkdir(parents=True)
            record.write_text(
                '"/private/tmp/not-a-reproduction-scratch"\n', encoding="utf-8"
            )

            with self.assertRaises(ReproductionControlPlaneError) as raised:
                cleanup_reproduction_scratch(root)

            self.assertEqual(raised.exception.code, "scratch_cleanup_incomplete")
            self.assertTrue(raised.exception.cleanup_incomplete)
            self.assertTrue(record.is_file())

    def test_scope_lock_rechecks_guard_installed_after_initial_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, _root = accepted_run(project)
            late_guard = ActionError(
                "reproduction.recovery.active", "late survivor guard"
            )
            with mock.patch(
                "log_commands.reproduction_jobs._require_no_recovery_guard",
                side_effect=(None, late_guard),
            ):
                with self.assertRaisesRegex(ActionError, "late survivor guard"):
                    _acquire_scope_locks(log, None)
            fds = _acquire_scope_locks(log, None)
            for descriptor in fds:
                os.close(descriptor)

    def test_fixed_plan_has_no_mutable_run_members(self) -> None:
        keys = set(accepted_plan().as_dict())
        legacy = {
            "attempt",
            "attempts",
            "checkpoints",
            "queue",
            "plan",
            "source_snapshot",
            "validation_snapshot",
        }
        self.assertFalse(keys & legacy)

    def test_run_lifecycle_has_stopped_and_publication_terminal_states(self) -> None:
        self.assertIn("publishing", ACTIVE_PHASES)
        self.assertTrue({"complete", "stopped", "failed"} <= TERMINAL_STATUSES)
        self.assertEqual(RUN_SCHEMA, "research-log-reproduction-run/7")

    def test_plan_and_run_are_separate_strict_canonical_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _log, root = accepted_run(Path(directory))
            self.assertEqual(load_accepted_plan(root), accepted_plan())
            self.assertEqual(_load_run(root / "run.json")["schema"], RUN_SCHEMA)
            record = json.loads((root / "run.json").read_text())
            record["plan"] = {}
            (root / "run.json").write_text(
                _canonical(record),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ActionError, "fields are invalid"):
                _load_run(root / "run.json")

    def test_checkpoint_inventory_rejects_unowned_or_corrupt_recovery_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _log, root = accepted_run(Path(directory))
            record = _load_run(root / "run.json")
            _verify_checkpoint_inventory(root, record)
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "foreign.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "checkpoint"):
                _verify_checkpoint_inventory(root, record)

    def test_launch_releases_preparation_lock_before_worker_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / ".git").mkdir()
            fixture = _Fixture(root)
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
            execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [execution])
            observed: list[bool] = []

            def handoff(log: object, _root: Path, _fds: object, *, mode: str) -> None:
                with operation_lock(getattr(log, "root"), "log.lock", mode="exclusive"):
                    observed.append(mode == "fresh")

            with mock.patch(
                "log_commands.reproduction_jobs._spawn_supervisor", side_effect=handoff
            ):
                launched = launch_reproduction(
                    fixture.log, entry=entry.id, include_all=False
                )
            self.assertIsNotNone(launched.run_id)
            self.assertEqual(observed, [True])

    def test_whole_log_ordinary_launch_creates_a_fresh_run(self) -> None:
        """Omitting --entry remains an ordinary durable launch, not repair mode."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / ".git").mkdir()
            fixture = _Fixture(root)
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
            fixture.write_pyrun(
                entry,
                [fixture.execution(entry, "build", {"raw": raw}, {"output": output})],
            )
            with mock.patch(
                "log_commands.reproduction_jobs._spawn_supervisor"
            ) as handoff:
                launched = launch_reproduction(
                    fixture.log, entry=None, include_all=False
                )
            self.assertIsNotNone(launched.run_id)
            run_root = handoff.call_args.args[1]
            self.assertIsInstance(run_root, Path)
            self.assertTrue((run_root / "run.json").is_file())
            self.assertTrue((run_root / "plan.json").is_file())

    def test_status_derives_totals_from_the_accepted_plan_not_run_copies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _log, root = accepted_run(Path(directory))
            record = _load_run(root / "run.json")
            status = _status_projection(record, accepted_plan(), root)
            self.assertEqual(status["total_executions"], 0)
            self.assertEqual(status["completed_executions"], 0)
            self.assertNotIn("attempt", status)

    def test_racing_resumes_install_one_supervisor_for_one_fixed_plan(self) -> None:
        """The state lock, rather than a stale read, decides the resume winner."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, root = accepted_run(project)
            run_id = root.name
            record = _load_run(root / "run.json")
            state = record["state"]
            assert isinstance(state, dict)
            state.update({"phase": None, "status": "stopped"})
            timestamps = record["timestamps"]
            assert isinstance(timestamps, dict)
            timestamps["stopped_at"] = "2030-01-01T00:00:01Z"
            (root / "run.json").write_text(_canonical(record), encoding="utf-8")

            started = threading.Barrier(2)
            supervisors: list[str] = []
            failures: list[BaseException] = []

            def resume() -> None:
                try:
                    started.wait()
                    resume_reproduction(log, run_id)
                except BaseException as error:  # one caller must lose the race
                    failures.append(error)

            def spawn(_log: object, _root: Path, _fds: object, *, mode: str) -> None:
                self.assertEqual(mode, "stopped")
                supervisors.append(mode)

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._find_run", return_value=root
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._acquire_scope_locks",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=spawn,
                ),
            ):
                threads = [threading.Thread(target=resume) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(supervisors, ["stopped"])
            self.assertEqual(len(failures), 1)
            # Depending on which caller acquires the promotion mutex first, the
            # loser sees either that contention or the rechecked run state.
            self.assertIn(
                getattr(failures[0], "code", ""),
                {"operation.lock.conflict", "reproduction.resume.invalid_state"},
            )
            latest = _load_run(root / "run.json")
            self.assertIsNone(latest["state"]["status"])
            self.assertEqual(latest["state"]["phase"], "accepted")
            self.assertEqual(
                load_accepted_plan(root).serialized(), accepted_plan().serialized()
            )

    def test_clearing_results_preserves_an_actual_stopped_job_for_resume(self) -> None:
        """Result deletion does not cross the stopped run's durable boundary."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, run_root = accepted_run(project)
            run_id = run_root.name
            workspace = ReproductionWorkspace(
                run_id,
                run_root,
                project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for directory_path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
            ):
                directory_path.mkdir(parents=True, exist_ok=True)
            checkpoint = _checkpoint_path(
                workspace, "e001", "pyrun-exec/v1:" + "1" * 64
            )
            checkpoint.parent.mkdir(parents=True)
            _write_checkpoint(
                checkpoint,
                ExecutionCheckpoint(
                    "e001", "pyrun-exec/v1:" + "1" * 64, "stopped",
                    checkpoint.relative_to(run_root).as_posix(), None, (),
                    "2030-01-01T00:00:00Z", "2030-01-01T00:00:01Z", 1.0,
                    {
                        "code": "stop_requested",
                        "message": "stopped",
                        "recorded_at": "2030-01-01T00:00:01Z",
                    },
                ),
            )
            staged = workspace.staging_root / "e001" / "retained.txt"
            diagnostic = workspace.diagnostics_root / "retained.log"
            for path, content in ((staged, b"staged\n"), (diagnostic, b"diagnostic\n")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            attempt = ExecutionAttempt(
                "e001", "pyrun-exec/v1:" + "1" * 64, None, True,
                "stop_requested", "stopped", None, (),
                diagnostic.relative_to(run_root).as_posix(),
                diagnostic.relative_to(run_root).as_posix(),
            )
            with (
                mock.patch(
                    "log_commands.reproduction_jobs._stop_workers_and_clean_scratch",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_scheduler.release_run_scheduling"
                ),
            ):
                _finish_stopped(
                    log, run_root, run_id, (attempt,), wait_for_retry=False
                )
            self.assertEqual(
                _load_run(run_root / "run.json")["state"]["status"], "stopped"
            )

            initialize_empty_reproduction_results(
                log.root / RESULTS_STORE,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:00Z",
            )
            preserved = {
                run_root / "plan.json": (run_root / "plan.json").read_bytes(),
                checkpoint: checkpoint.read_bytes(),
                staged: staged.read_bytes(),
                diagnostic: diagnostic.read_bytes(),
            }
            clear_result_store(log.root)
            self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
            resumed_boundary: dict[Path, bytes] = {}

            def observe_resume(
                _log: object, _root: Path, _fds: object, *, mode: str
            ) -> None:
                self.assertEqual(mode, "stopped")
                resumed_boundary.update({path: path.read_bytes() for path in preserved})

            with (
                mock.patch(
                    "log_commands.reproduction_jobs._find_run", return_value=run_root
                ),
                mock.patch("log_commands.reproduction_jobs._verify_checkpoint_inventory"),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=observe_resume,
                ),
            ):
                self.assertEqual(resume_reproduction(log, run_id), run_id)
            self.assertEqual(resumed_boundary, preserved)

    def test_failed_dependency_skips_only_its_descendant_while_independent_work_runs(
        self,
    ) -> None:
        """A failed comparison blocks B but must not suppress independent C."""

        identity_a = "pyrun-exec/v1:" + "a" * 64
        identity_b = "pyrun-exec/v1:" + "b" * 64
        identity_c = "pyrun-exec/v1:" + "c" * 64
        executions = (
            {"entry": "e001", "execution_id": identity_a, "depends_on": [], "order": 1},
            {
                "entry": "e001",
                "execution_id": identity_b,
                "depends_on": [f"e001:{identity_a}"],
                "order": 2,
            },
            {"entry": "e002", "execution_id": identity_c, "depends_on": [], "order": 3},
        )
        plan = replace(accepted_plan(executions=executions), jobs=2)
        workspace = ReproductionWorkspace(
            "reproduce-fixed-plan",
            Path("/private/tmp/run"),
            Path("/private/tmp"),
            Path("/private/tmp/work"),
            Path("/private/tmp/runtime"),
            Path("/private/tmp/diagnostics"),
            Path("/private/tmp/executions"),
        )
        launched: list[str] = []

        def attempt(planned: object) -> ExecutionAttempt:
            assert isinstance(planned, dict)
            identity = str(planned["execution_id"])
            launched.append(identity)
            checkpoint = ExecutionCheckpoint(
                str(planned["entry"]),
                identity,
                "succeeded",
                "checkpoints/x.json",
                "2030-01-01T00:00:01Z",
                (),
            )
            return ExecutionAttempt(
                str(planned["entry"]),
                identity,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                "diagnostics/out",
                "diagnostics/err",
            )

        with (
            mock.patch(
                "log_commands.reproduction_execution._generated_output_paths",
                return_value={},
            ),
            mock.patch(
                "log_commands.reproduction_execution._execution_source",
                return_value=mock.sentinel.source,
            ),
            mock.patch(
                "log_commands.reproduction_execution._execute_scheduled_recipe",
                side_effect=lambda _runtime, planned: attempt(planned),
            ),
        ):
            batch = execute_reproduction_plan(
                mock.sentinel.log,
                plan,
                workspace,
                ExecutionControl(
                    attempt_completed=lambda item: item.execution_id != identity_a
                ),
            )

        self.assertCountEqual(launched, [identity_a, identity_c])
        self.assertEqual(
            batch.dependency_skips,
            (
                {
                    "depends_on": [f"e001:{identity_a}"],
                    "entry": "e001",
                    "execution_id": identity_b,
                    "reason": "dependency_failed",
                },
            ),
        )

    def test_publication_retry_enters_no_executor_branch_with_same_run_identity(
        self,
    ) -> None:
        """A retry is publication work, not a disguised second execution pass."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, root = accepted_run(project)
            seen: list[str] = []

            def retry(
                _log: object,
                state: object,
                _plan: object,
                _comparisons: object,
                _record: object,
            ) -> None:
                seen.append(getattr(state, "run_id"))

            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch(
                    "log_commands.reproduction_jobs.open_existing_workspace",
                    return_value=mock.sentinel.workspace,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.load_recorded_comparisons",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._retry_publication",
                    side_effect=retry,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.execute_reproduction_plan"
                ) as execute,
            ):
                supervise_reproduction(
                    log, root, mode="publication", inherited_locks=()
                )

            self.assertEqual(seen, [root.name])
            execute.assert_not_called()

    def test_publication_retry_replays_terminal_fixed_plan_without_execution(
        self,
    ) -> None:
        """A failed final transaction retries durable mixed outcomes only.

        This deliberately uses the planner's accepted plan, canonical run
        storage, checkpoint/staging codecs, and the real requirement update.
        The executor is deterministic solely to make the terminal failure and
        dependency skip reproducible; it is never entered by any retry.
        """

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            good_output = entry.root / "data" / "good.txt"
            failed_output = entry.root / "data" / "failed.txt"
            dependent_output = entry.root / "data" / "dependent.txt"
            raw.write_text("raw\n", encoding="utf-8")
            good_output.write_text("good\n", encoding="utf-8")
            failed_output.write_text("failed\n", encoding="utf-8")
            dependent_output.write_text("dependent\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [fixture.item(entry, "raw", raw, origin=True)]
                + [
                    fixture.item(entry, name, path, origin=False)
                    for name, path in (
                        ("good", good_output),
                        ("failed", failed_output),
                        ("dependent", dependent_output),
                    )
                ],
            )
            fixture.evidence(entry, "good", "failed", "dependent")
            good_id, good = fixture.execution(
                entry, "good", {"raw": raw}, {"good": good_output}
            )
            failed_id, failed = fixture.execution(
                entry, "failed", {"raw": raw}, {"failed": failed_output}
            )
            dependent_id, dependent = fixture.execution(
                entry,
                "dependent",
                {"failed": failed_output},
                {"dependent": dependent_output},
            )
            fixture.write_pyrun(
                entry,
                [(good_id, good), (failed_id, failed), (dependent_id, dependent)],
            )
            from log_commands.reproduction_jobs import _accepted_record, _new_run_root
            from test_log_reproduction_planning import _plan

            plan = _plan(fixture, entry)
            self.assertEqual(
                {(item["entry"], item["execution_id"]) for item in plan.executions},
                {(entry.id, good_id), (entry.id, failed_id), (entry.id, dependent_id)},
            )
            run_id = "reproduce-20300101t000000z-publication-retry"
            run_root = _new_run_root(
                project, fixture.log, entry.id, run_id, "2030-01-01T00:00:00Z"
            )
            run_root.mkdir(parents=True)
            plan_path = run_root / "plan.json"
            plan_path.write_text(plan.serialized(), encoding="utf-8")
            run_path = run_root / "run.json"
            run_path.write_text(
                _canonical(
                    _accepted_record(
                        fixture.log,
                        plan,
                        run_id,
                        run_root,
                        accepted_at="2020-01-01T00:00:00Z",
                    )
                ),
                encoding="utf-8",
            )
            accepted_plan_bytes = plan_path.read_bytes()
            published_results = fixture.log.root / RESULTS_STORE
            published_report = fixture.log.root / REPRODUCTION_REPORT
            published_results.parent.mkdir(parents=True, exist_ok=True)
            initialize_empty_reproduction_results(
                published_results,
                summary="docs/study.md",
                updated_at="2030-01-01T00:00:00Z",
            )
            published_report.write_text("previous report\n", encoding="utf-8")
            previous_bundle = (
                published_results.read_bytes(),
                published_report.read_bytes(),
            )
            executor_calls: list[str] = []
            output_observations = {
                good_output: dict(good.observed.outputs)["data/good.txt"],
                failed_output: dict(failed.observed.outputs)["data/failed.txt"],
            }

            def checkpoint(
                workspace: ReproductionWorkspace,
                identity: str,
                state: str,
                output: Path | None,
            ) -> ExecutionAttempt:
                checkpoint_path = _checkpoint_path(workspace, entry.id, identity)
                finished = "2030-01-01T00:00:01Z"
                outputs = ()
                if output is not None:
                    observed = output_observations[output]
                    outputs = (
                        {
                            "artifact": f"data/{output.name}",
                            "fingerprint": observed.as_dict(),
                        },
                    )
                value = ExecutionCheckpoint(
                    entry.id,
                    identity,
                    state,
                    checkpoint_path.relative_to(workspace.run_root).as_posix(),
                    finished if state == "succeeded" else None,
                    outputs,
                    "2030-01-01T00:00:00Z",
                    finished,
                    1.0,
                    (
                        None
                        if state == "succeeded"
                        else {
                            "code": "execution_failed",
                            "message": "failed",
                            "recorded_at": finished,
                        }
                    ),
                )
                _write_checkpoint(checkpoint_path, value)
                return ExecutionAttempt(
                    entry.id,
                    identity,
                    0 if state == "succeeded" else 1,
                    False,
                    None if state == "succeeded" else "execution_failed",
                    None if state == "succeeded" else "failed",
                    value,
                    (),
                    "diagnostics/stdout.log",
                    "diagnostics/stderr.log",
                )

            def execute_once(
                _log: object,
                _plan: object,
                workspace: ReproductionWorkspace,
                control: ExecutionControl,
            ) -> ExecutionBatch:
                executor_calls.append("fresh")
                workspace.map_source(good_output).parent.mkdir(
                    parents=True, exist_ok=True
                )
                workspace.map_source(good_output).write_bytes(good_output.read_bytes())
                succeeded = checkpoint(workspace, good_id, "succeeded", good_output)
                self.assertTrue(control.attempt_completed(succeeded))
                workspace.map_source(failed_output).write_bytes(
                    failed_output.read_bytes()
                )
                failed_attempt = checkpoint(
                    workspace, failed_id, "failed", failed_output
                )
                self.assertFalse(control.attempt_completed(failed_attempt))
                return ExecutionBatch(
                    (succeeded, failed_attempt),
                    (),
                    (
                        {
                            "depends_on": [f"{entry.id}:{failed_id}"],
                            "entry": entry.id,
                            "execution_id": dependent_id,
                            "reason": "dependency_failed",
                        },
                    ),
                    False,
                )

            publication_failure = ActionError(
                "reproduction.publication.failed", "fixture publication failure"
            )
            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch(
                    "log_commands.reproduction_jobs.execute_reproduction_plan",
                    side_effect=execute_once,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.completed_execution_attempts",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.publish_completed_reproduction",
                    side_effect=publication_failure,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._stop_workers_and_clean_scratch",
                    return_value=(),
                ),
            ):
                supervise_reproduction(
                    fixture.log, run_root, mode="fresh", inherited_locks=()
                )
            first_failure = _load_run(run_path)
            self.assertEqual(first_failure["run_id"], run_id)
            self.assertEqual(first_failure["state"]["status"], "failed")
            self.assertEqual(
                first_failure["state"]["operational_failure"]["code"],
                "reproduction.publication.failed",
                first_failure["state"]["operational_failure"],
            )
            self.assertEqual(plan_path.read_bytes(), accepted_plan_bytes)
            self.assertEqual(executor_calls, ["fresh"])
            current = load_pyrun_state(
                entry.root / "pyrun.json", entry_root=entry.root, project_root=project
            )
            self.assertFalse(current.executions[good_id].requires_reproduction)

            # The replay reaches the guarded requirement update again, but it
            # is idempotent because the first durable completion cleared it.
            requirement_replays: list[tuple[str, str]] = []
            from log_commands.reproduction_comparison import (
                clear_execution_reproduction_requirement_locked as clear_requirement,
            )

            def observe_requirement(*args: object, **kwargs: object) -> bool:
                result = args[2]
                requirement_replays.append((result.entry, result.execution_id))
                return clear_requirement(*args, **kwargs)

            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch(
                    "log_commands.reproduction_jobs.execute_reproduction_plan"
                ) as execute_retry,
                mock.patch(
                    "log_commands.reproduction_jobs.clear_execution_reproduction_requirement_locked",
                    side_effect=observe_requirement,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs.publish_completed_reproduction",
                    side_effect=publication_failure,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._stop_workers_and_clean_scratch",
                    return_value=(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=lambda log, root, fds, *, mode: supervise_reproduction(
                        log, root, mode=mode, inherited_locks=fds
                    ),
                ),
            ):
                self.assertEqual(resume_reproduction(fixture.log, run_id), run_id)
            execute_retry.assert_not_called()
            self.assertEqual(
                requirement_replays,
                [(entry.id, good_id), (entry.id, failed_id)],
                _load_run(run_path)["state"].get("operational_failure"),
            )
            self.assertEqual(
                (published_results.read_bytes(), published_report.read_bytes()),
                previous_bundle,
            )
            self.assertEqual(plan_path.read_bytes(), accepted_plan_bytes)
            self.assertEqual(_load_run(run_path)["state"]["status"], "failed")

            with (
                mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
                mock.patch(
                    "log_commands.reproduction_jobs.execute_reproduction_plan"
                ) as execute_retry,
                mock.patch(
                    "log_commands.reproduction_jobs._spawn_supervisor",
                    side_effect=lambda log, root, fds, *, mode: supervise_reproduction(
                        log, root, mode=mode, inherited_locks=fds
                    ),
                ),
            ):
                self.assertEqual(resume_reproduction(fixture.log, run_id), run_id)
            execute_retry.assert_not_called()
            complete = _load_run(run_path)
            self.assertEqual(complete["run_id"], run_id)
            self.assertEqual(complete["state"]["status"], "complete")
            self.assertEqual(plan_path.read_bytes(), accepted_plan_bytes)
