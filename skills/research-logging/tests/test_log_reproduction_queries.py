from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.dispatcher import main
from log_commands.model import ActionError
from log_commands.reproduction_contract import (
    ReproductionPlan,
    ReproductionRuntime,
    source_snapshot,
)
from log_commands.reproduction_jobs import ReproductionLaunch
from log_commands.reproduction_planner import (
    ReproductionCommandInventory,
    ReproductionSelection,
    ReproductionStateProjection,
)
from log_commands.reproduction_queries import (
    compose_reproduction_command,
    compose_reproduction_command_list,
    compose_root_reproduction_summary,
    list_reproduction_artifacts,
    list_reproduction_commands,
    reproduction_reconciliation_text,
    reproduction_report,
    reproduction_summary,
    root_reproduction_summary,
    show_reproduction_artifact,
    show_reproduction_command,
)
from log_commands.reproduction_results import (
    ArtifactResult,
    ComparisonRecord,
    ReproductionResults,
    RunFolder,
    RunResult,
)
from research_log_data import Fingerprint


class ReproductionQueryTests(unittest.TestCase):
    def test_no_work_reconciliation_counts_commands_not_needing_reproduction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "research.md"
            log_root = summary.with_suffix("")
            reproduction = log_root / ".cache" / "reproduction"
            reproduction.mkdir(parents=True)
            summary.write_text("# Research\n", encoding="utf-8")
            results = _results()
            (reproduction / "results.json").write_text(
                results.serialized(), encoding="utf-8"
            )
            commands = tuple(
                {
                    "auto_reproduce": True,
                    "entry": "e003",
                    "execution_id": "pyrun-exec/v1:" + str(number) * 64,
                    "prior_disposition": None,
                    "selection": "not_needed",
                    "source_digest": str(number) * 64,
                }
                for number in (1,)
            )
            plan = ReproductionPlan(
                "docs/research.md",
                {"entry": None, "kind": "log"},
                False,
                {},
                source_snapshot(
                    authority_files=(),
                    commands=commands,
                    executions=(),
                    materials=(),
                ),
                (),
                (),
                (),
                (),
            )
            artifact = results.artifacts[0]
            state = ReproductionStateProjection(
                frozenset({(artifact.entry, artifact.artifact)}),
                {(artifact.entry, artifact.artifact): artifact.execution_id},
                {(artifact.entry, artifact.execution_id): artifact.recorded_at},
            )
            with (
                mock.patch(
                    "log_commands.reproduction_queries.project_reproduction_state",
                    return_value=state,
                ),
                mock.patch(
                    "log_commands.reproduction_queries."
                    "project_reproduction_command_inventory",
                    return_value=ReproductionCommandInventory(3, 1),
                ),
            ):
                text = reproduction_reconciliation_text(
                    LogContext(summary.resolve(), log_root.resolve()),
                    plan,
                    generated_at="2030-01-02T00:00:00Z",
                )

        self.assertIn(
            "3 total\n"
            "├─ 2 reproduction not retried\n"
            "├─ 1 skipped by policy (not automatic)\n"
            "└─ 0 selected for execution",
            text,
        )

    def test_report_does_not_read_the_former_result_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "research.md"
            log_root = summary.with_suffix("")
            legacy = log_root / "reproduction" / "results.json"
            legacy.parent.mkdir(parents=True)
            summary.write_text("# Research\n", encoding="utf-8")
            legacy.write_text(_results().serialized(), encoding="utf-8")

            with self.assertRaisesRegex(
                ActionError, "no cached reproduction.*--recheck"
            ):
                reproduction_report(
                    LogContext(summary.resolve(), log_root.resolve()), entry=None
                )

    def test_dispatcher_exposes_bounded_human_dry_run_summary(self) -> None:
        log = mock.sentinel.log
        executions = tuple(
            {
                "entry": f"e{number:03d}",
                "exclusive": number == 1,
                "read_paths": [],
                "run_path": f"<run>/executions/e{number:03d}/fixture",
                "writable_paths": [],
                "write_paths": [],
            }
            for number in range(1, 22)
        )
        plan = ReproductionPlan(
            "docs/research.md",
            {"entry": None, "kind": "log"},
            False,
            {},
            source_snapshot(authority_files=(), executions=(), materials=()),
            ({},) * 25,
            executions,
            ({},) * 3,
            ({},) * 2,
            4,
        )
        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_jobs.dry_run_reproduction",
                return_value=plan,
            ) as dry_run,
            redirect_stdout(output),
        ):
            status = main(
                [
                    "reproduce",
                    "--path",
                    "/project/log",
                    "--jobs",
                    "4",
                    "--dry-run",
                    "--summary",
                ]
            )

        self.assertEqual(status, 0)
        self.assertIn("- Admission: Ready with localized failures", output.getvalue())
        self.assertIn("- Selection: Incremental; automatic only", output.getvalue())
        self.assertIn("- Concurrency cap: 4", output.getvalue())
        self.assertIn("- Per-command runtime limit: 300 seconds", output.getvalue())
        self.assertIn("- Artifact cases: 25", output.getvalue())
        self.assertIn(
            "- Runnable executions: 21 (20 ordinary, 1 exclusive)",
            output.getvalue(),
        )
        self.assertIn("- Local planning failures: 2 artifacts", output.getvalue())
        self.assertIn("- Scheduling path claims: Complete", output.getvalue())
        self.assertIn("| `e020` | 1 | 0 |", output.getvalue())
        self.assertNotIn("| `e021` |", output.getvalue())
        self.assertIn("1 additional entry omitted.", output.getvalue())
        dry_run.assert_called_once_with(
            log,
            entry=None,
            include_all=False,
            runtime=ReproductionRuntime(4, 300),
            selection=ReproductionSelection(),
        )

    def test_dispatcher_rejects_summary_for_a_real_launch(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main(["reproduce", "--path", "/project/log", "--summary"])

    def test_report_list_and_show_share_current_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "research.md"
            log_root = summary.with_suffix("")
            reproduction = log_root / ".cache" / "reproduction"
            reproduction.mkdir(parents=True)
            summary.write_text("# Research\n", encoding="utf-8")
            text = _results().serialized()
            (reproduction / "results.json").write_text(text, encoding="utf-8")
            results = ReproductionResults.from_json(text)
            reachable = frozenset(
                (item.entry, item.artifact) for item in results.artifacts
            )
            execution_map = {
                (item.entry, item.artifact): item.execution_id
                for item in results.artifacts
                if item.execution_id is not None
            }
            last_runs = {
                (item.entry, item.execution_id): item.recorded_at
                for item in results.artifacts
                if item.execution_id is not None
            }
            state = ReproductionStateProjection(reachable, execution_map, last_runs)
            log = LogContext(summary.resolve(), log_root.resolve())

            with mock.patch(
                "log_commands.reproduction_queries.project_reproduction_state",
                return_value=state,
            ):
                listing = list_reproduction_artifacts(
                    log, entry="e003", outcome="changed", artifact=None
                )
                shown = show_reproduction_artifact(
                    log, entry="e003", artifact="data/changed.bin"
                )
                report = reproduction_report(log, entry="e003")
                summary_result = reproduction_summary(log)

            self.assertEqual(
                (listing["matched"], listing["returned"], listing["omitted"]),
                (1, 1, 0),
            )
            self.assertEqual(shown["artifact"]["outcome"], "changed")
            self.assertIn("| `data/changed.bin` | **changed** |", report)
            self.assertEqual(
                summary_result["commands"],
                {
                    "reproduction_not_retried": 0,
                    "selected": {
                        "blocked": 0,
                        "failed": 0,
                        "succeeded": 1,
                        "total": 1,
                    },
                    "skipped_by_policy": 0,
                    "total": 1,
                },
            )
            self.assertEqual(
                summary_result["artifacts"],
                {
                    "matched": 0,
                    "not_matched": 1,
                    "not_compared": {
                        "command_blocked": 0,
                        "command_failed": 0,
                        "command_skipped": 0,
                        "comparison_failed": 0,
                        "total": 0,
                    },
                    "total": 1,
                },
            )

    def test_root_summary_preserves_log_coverage_and_separate_totals(self) -> None:
        complete = {
            "artifacts": {
                "matched": 2,
                "not_matched": 1,
                "not_compared": {
                    "command_blocked": 1,
                    "command_failed": 2,
                    "command_skipped": 0,
                    "comparison_failed": 0,
                    "total": 3,
                },
                "total": 6,
            },
            "commands": {
                "reproduction_not_retried": 4,
                "selected": {
                    "blocked": 1,
                    "failed": 1,
                    "succeeded": 2,
                    "total": 4,
                },
                "skipped_by_policy": 1,
                "total": 9,
            },
            "generated_at": "2030-01-01T00:00:00Z",
            "run_id": "reproduce-20300101t000000z-fixture",
            "resolved": False,
            "schema": "research-log-reproduction-summary/5",
            "status": "complete",
            "summary": "docs/one.md",
        }
        missing = ActionError("reproduction.results.missing", "not published")
        with (
            mock.patch(
                "log_commands.reproduction_queries.discover_summaries",
                return_value={
                    "root": "/project",
                    "schema": "research-log-discovery-result/1",
                    "summaries": ["/project/docs/one.md", "/project/docs/two.md"],
                },
            ),
            mock.patch(
                "log_commands.reproduction_queries.resolve_log",
                side_effect=(mock.sentinel.one, mock.sentinel.two),
            ),
            mock.patch(
                "log_commands.reproduction_queries.reproduction_summary",
                side_effect=(complete, missing),
            ),
        ):
            result = root_reproduction_summary(Path("/project"))

        self.assertEqual(
            result["coverage"],
            {
                "complete": 1,
                "not_run": 1,
                "resolved": 0,
                "total": 2,
                "unavailable": 0,
                "unresolved": 1,
            },
        )
        self.assertEqual(result["totals"]["commands"]["total"], 9)
        self.assertEqual(result["totals"]["artifacts"]["total"], 6)
        report = compose_root_reproduction_summary(result)
        self.assertIn("0 resolved, 1 unresolved", report)
        self.assertIn("| `docs/one` | 4 | 1 | 2 | 1 | 1 | 9 |", report)
        self.assertIn(
            "| `docs/two` — not yet reproduced | — | — | — | — | — | — |",
            report,
        )
        self.assertIn("| `docs/one` | 2 | 1 | 3 | 6 |", report)

    def test_dispatcher_exposes_report_and_artifact_routes(self) -> None:
        log = mock.sentinel.log
        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_queries.reproduction_report",
                return_value="ready report\n",
            ),
            redirect_stdout(output),
        ):
            status = main(["reproduce", "report", "--path", "/project/log"])

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "ready report\n")

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_queries.reproduction_summary",
                return_value={"schema": "research-log-reproduction-summary/5"},
            ),
            redirect_stdout(output),
        ):
            status = main(
                [
                    "reproduce",
                    "report",
                    "--path",
                    "/project/log",
                    "--summary",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            output.getvalue(),
            '{"schema": "research-log-reproduction-summary/5"}\n',
        )

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_queries.list_reproduction_artifacts",
                return_value={"matched": 0, "returned": 0, "omitted": 0},
            ),
            redirect_stdout(output),
        ):
            status = main(["reproduce", "artifacts", "list", "--path", "/project/log"])

        self.assertEqual(status, 0)
        self.assertIn('"matched": 0', output.getvalue())

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_queries.list_reproduction_commands",
                return_value={
                    "filters": {"bucket": "skipped-by-policy"},
                    "matched": 30,
                    "omitted": 0,
                    "records": [],
                    "returned": 30,
                    "run_id": "reproduce-fixture",
                },
            ) as listing,
            mock.patch(
                "log_commands.reproduction_queries.compose_reproduction_command_list",
                return_value="30 commands\n",
            ) as compose,
            redirect_stdout(output),
        ):
            status = main(
                [
                    "reproduce",
                    "commands",
                    "list",
                    "--path",
                    "/project/log",
                    "--bucket",
                    "skipped-by-policy",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "30 commands\n")
        listing.assert_called_once_with(
            log,
            bucket="skipped-by-policy",
            entry=None,
            reason=None,
            run_id=None,
        )
        compose.assert_called_once_with(
            mock.ANY,
            path=Path("/project/log"),
            program=mock.ANY,
        )

    def test_legacy_command_queries_require_reproduction_metadata_upgrade(self) -> None:
        run_id = "reproduce-20300101t000000z-commands"
        run = RunResult(
            run_id,
            {"entry": None, "kind": "log"},
            False,
            "complete",
            "2030-01-01T00:00:00Z",
            "2030-01-01T00:05:00Z",
            {
                name: 0
                for name in (
                    "matched",
                    "changed",
                    "failed",
                    "comparison_failed",
                    "skipped",
                )
            },
            RunFolder("tmp/reproduction/2030-01-01/reproduce-commands", "available"),
            (),
            {
                "blocked": 0,
                "failed": 0,
                "not_automatic": 1,
                "reproduction_not_needed": 0,
                "unchanged_blocked": 0,
                "unchanged_failed": 1,
                "succeeded": 1,
                "total": 3,
            },
        )
        results = ReproductionResults(
            "docs/research.md", "2030-01-01T00:05:00Z", (), (run,)
        )

        with (
            mock.patch(
                "log_commands.reproduction_queries._published_results",
                return_value=results,
            ),
            mock.patch("log_commands.reproduction_jobs._find_run") as find_run,
            mock.patch(
                "log_commands.reproduction_queries."
                "project_reproduction_command_inventory"
            ) as current_inventory,
        ):
            with self.assertRaises(ActionError) as list_error:
                list_reproduction_commands(
                    mock.sentinel.log,
                    bucket="skipped-by-policy",
                    entry=None,
                    reason=None,
                    run_id=None,
                )
            with self.assertRaises(ActionError) as show_error:
                show_reproduction_command(
                    mock.sentinel.log,
                    entry="e003",
                    execution_id="pyrun-exec/v1:" + "1" * 64,
                    run_id=run_id,
                )

        for error in (list_error.exception, show_error.exception):
            self.assertEqual(error.code, "reproduction.command.schema_unsupported")
            self.assertIn("unsupported command-query metadata", str(error))
            self.assertIn("run reproduction with --recheck to rebuild it", str(error))
            self.assertNotIn("result-v7", str(error))
        find_run.assert_not_called()
        current_inventory.assert_not_called()

    def test_completed_run_queries_use_immutable_records_after_reclassification(
        self,
    ) -> None:
        run_id = "reproduce-20300101t000000z-immutable"

        def record(index: int, *, failed: bool) -> dict[str, object]:
            identity = f"pyrun-exec/v1:{index:064x}"
            return {
                "auto_reproduce": failed,
                "bucket": "failed" if failed else "skipped-by-policy",
                "cwd": f"docs/research/entries/2030-01-{index:02d}-e{index:03d}",
                "details": [],
                "entry": f"e{index:03d}",
                "execution_id": identity,
                "exclusive": False,
                "prior_disposition": None,
                "queued": failed,
                "reason": "failed" if failed else "not_automatic",
                "recipe": {
                    "environment": {},
                    "inputs": [],
                    "outputs": {"data/result.txt": "file"},
                    "parameters": [],
                    "script": "scripts/build.py",
                },
                "requires_reproduction": True,
                "run_selection": "run" if failed else "policy",
                "source_digest": "a" * 64 if failed else None,
                "terminal_disposition": "failed" if failed else None,
            }

        records = tuple(record(index, failed=index == 1) for index in range(1, 32))
        run = RunResult(
            run_id,
            {"entry": None, "kind": "log"},
            False,
            "complete",
            "2030-01-01T00:00:00Z",
            "2030-01-01T00:05:00Z",
            {
                name: 0
                for name in (
                    "matched",
                    "changed",
                    "failed",
                    "comparison_failed",
                    "skipped",
                )
            },
            RunFolder("tmp/reproduction/2030-01-01/reproduce-immutable", "available"),
            (),
            {
                "blocked": 0,
                "failed": 1,
                "not_automatic": 30,
                "reproduction_not_needed": 0,
                "unchanged_blocked": 0,
                "unchanged_failed": 0,
                "succeeded": 0,
                "total": 31,
            },
            records,
        )
        results = ReproductionResults(
            "docs/research.md", "2030-01-01T00:05:00Z", (), (run,)
        )
        with (
            mock.patch(
                "log_commands.reproduction_queries._published_results",
                return_value=results,
            ),
            mock.patch(
                "log_commands.reproduction_queries._command_diagnostics",
                return_value={
                    "attempt": None,
                    "availability": "unavailable",
                    "checkpoint": None,
                    "reason": "fixture",
                    "stderr": None,
                    "stdout": None,
                },
            ),
        ):
            failed = list_reproduction_commands(
                mock.sentinel.log,
                bucket="failed",
                entry=None,
                reason=None,
                run_id=run_id,
            )
            skipped = list_reproduction_commands(
                mock.sentinel.log,
                bucket="skipped-by-policy",
                entry=None,
                reason=None,
                run_id=run_id,
            )
            shown = show_reproduction_command(
                mock.sentinel.log,
                entry="e001",
                execution_id="pyrun-exec/v1:" + f"{1:064x}",
                run_id=run_id,
            )

        self.assertEqual((failed["matched"], failed["returned"]), (1, 1))
        self.assertEqual((skipped["matched"], skipped["returned"]), (30, 30))
        self.assertEqual(shown["command"]["bucket"], "failed")
        self.assertNotIn("details_unavailable", failed)
        self.assertNotIn("details_unavailable", shown)

    def test_command_show_reads_bounded_retained_failure_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            summary = project / "docs" / "research.md"
            summary.parent.mkdir()
            summary.write_text("# Research\n", encoding="utf-8")
            log = LogContext(summary.resolve(), summary.with_suffix("").resolve())
            run_id = "reproduce-20300101t000000z-diagnostics"
            execution_id = "pyrun-exec/v1:" + "1" * 64
            relative_root = (
                "tmp/reproduction/2030-01-01/"
                "reproduce-research-reproduce-20300101t000000z-diagnostics"
            )
            run_root = project / relative_root
            diagnostic_root = (
                run_root / "diagnostics" / "e003" / execution_id.rsplit(":", 1)[1]
            )
            diagnostic_root.mkdir(parents=True)
            (diagnostic_root / "stderr.log").write_bytes(
                b"discarded prefix\n"
                + b"x" * (20 * 1024)
                + b"\n\x1b[31mValueError: broken\x1b[0m\n"
            )
            (diagnostic_root / "stdout.log").write_text(
                "preparing fixture\n", encoding="utf-8"
            )
            command = {
                "auto_reproduce": True,
                "bucket": "failed",
                "cwd": "docs/research/entries/2030-01-01-e003-fixture",
                "details": [],
                "entry": "e003",
                "execution_id": execution_id,
                "exclusive": False,
                "prior_disposition": None,
                "queued": True,
                "reason": "failed",
                "recipe": {
                    "environment": {},
                    "inputs": [],
                    "outputs": {"data/result.txt": "file"},
                    "parameters": [],
                    "script": "scripts/build.py",
                },
                "requires_reproduction": True,
                "run_selection": "run",
                "source_digest": "a" * 64,
                "terminal_disposition": "failed",
            }
            run = RunResult(
                run_id,
                {"entry": None, "kind": "log"},
                False,
                "complete",
                "2030-01-01T00:00:00Z",
                "2030-01-01T00:05:00Z",
                {
                    "changed": 0,
                    "comparison_failed": 0,
                    "failed": 1,
                    "matched": 0,
                    "skipped": 0,
                },
                RunFolder(relative_root, "available"),
                (),
                {
                    "blocked": 0,
                    "failed": 1,
                    "not_automatic": 0,
                    "reproduction_not_needed": 0,
                    "unchanged_blocked": 0,
                    "unchanged_failed": 0,
                    "succeeded": 0,
                    "total": 1,
                },
                (command,),
            )
            results = ReproductionResults(
                "docs/research.md", "2030-01-01T00:05:00Z", (), (run,)
            )
            checkpoint = {
                "completed_at": None,
                "elapsed_seconds": 2.5,
                "entry": "e003",
                "execution_id": execution_id,
                "failure": {
                    "code": "execution_failed",
                    "message": "execution exited with status 1",
                    "recorded_at": "2030-01-01T00:00:04Z",
                },
                "finished_at": "2030-01-01T00:00:04Z",
                "outputs": [],
                "path": "checkpoints/e003-fixture.json",
                "started_at": "2030-01-01T00:00:01Z",
                "state": "failed",
            }
            record = {"attempt": 1, "attempts": [], "checkpoints": [checkpoint]}
            with (
                mock.patch(
                    "log_commands.reproduction_queries._published_results",
                    return_value=results,
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._find_run",
                    return_value=run_root.resolve(),
                ),
                mock.patch(
                    "log_commands.reproduction_jobs._load_run",
                    return_value=record,
                ),
            ):
                shown = show_reproduction_command(
                    log,
                    entry="e003",
                    execution_id=execution_id,
                    run_id=run_id,
                )

            diagnostics = shown["diagnostics"]
            self.assertEqual(shown["schema"], "research-log-reproduction-command/3")
            self.assertEqual(diagnostics["availability"], "available")
            self.assertEqual(diagnostics["attempt"], 1)
            self.assertEqual(
                diagnostics["checkpoint"]["failure"]["message"],
                "execution exited with status 1",
            )
            self.assertIn("ValueError: broken", diagnostics["stderr"]["excerpt"])
            self.assertNotIn("discarded prefix", diagnostics["stderr"]["excerpt"])
            self.assertNotIn("\x1b", diagnostics["stderr"]["excerpt"])
            self.assertTrue(diagnostics["stderr"]["truncated"])
            self.assertTrue(
                diagnostics["stderr"]["path"].endswith(
                    "diagnostics/e003/" + "1" * 64 + "/stderr.log"
                )
            )
            text = compose_reproduction_command(shown)
            self.assertIn(
                "Failure: execution_failed: execution exited with status 1", text
            )
            self.assertIn("Stderr:", text)
            self.assertIn("ValueError: broken", text)

            with mock.patch(
                "log_commands.reproduction_queries._published_results",
                return_value=results,
            ):
                listed = list_reproduction_commands(
                    log,
                    bucket="failed",
                    entry=None,
                    reason=None,
                    run_id=run_id,
                )
            listing = compose_reproduction_command_list(
                listed,
                path=Path("/project/docs/research"),
                program=Path("/skill/scripts/log"),
            )
            self.assertIn(
                "/skill/scripts/log reproduce commands show "
                "--path /project/docs/research --entry e003 "
                f"--execution-id {execution_id} --run-id {run_id} --format text",
                listing,
            )

            unavailable_results = replace(
                results,
                runs=(
                    replace(
                        run,
                        folder=RunFolder(relative_root, "unknown"),
                    ),
                ),
            )
            with (
                mock.patch(
                    "log_commands.reproduction_queries._published_results",
                    return_value=unavailable_results,
                ),
                mock.patch("log_commands.reproduction_jobs._find_run") as find_run,
            ):
                unavailable = show_reproduction_command(
                    log,
                    entry="e003",
                    execution_id=execution_id,
                    run_id=run_id,
                )
            self.assertEqual(
                unavailable["diagnostics"]["availability"], "unavailable"
            )
            self.assertEqual(
                unavailable["diagnostics"]["reason"], "run_directory_unavailable"
            )
            find_run.assert_not_called()

    def test_dispatcher_limits_recheck_to_launch_and_dry_run(self) -> None:
        log = mock.sentinel.log
        plan = mock.Mock()
        plan.serialized.return_value = '{"schema":"fixture"}'
        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_jobs.dry_run_reproduction",
                return_value=plan,
            ) as dry_run,
            redirect_stdout(output),
        ):
            status = main(
                [
                    "reproduce",
                    "--path",
                    "/project/log",
                    "--entry",
                    "e003",
                    "--recheck",
                    "--dry-run",
                    "--execution-timeout-seconds",
                    "17",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), '{"schema":"fixture"}\n')
        dry_run.assert_called_once_with(
            log,
            entry="e003",
            include_all=False,
            runtime=ReproductionRuntime(1, 17),
            selection=ReproductionSelection("recheck"),
        )

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_jobs.launch_reproduction",
                return_value=ReproductionLaunch(run_id="reproduce-fixture"),
            ) as launch,
            redirect_stdout(output),
        ):
            status = main(["reproduce", "--path", "/project/log", "--recheck"])

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "reproduce-fixture\n")
        launch.assert_called_once_with(
            log,
            entry=None,
            include_all=False,
            runtime=ReproductionRuntime(),
            selection=ReproductionSelection("recheck"),
        )

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_jobs.launch_reproduction",
                return_value=ReproductionLaunch(
                    summary="# Reproduction Summary\n\n3 total\n"
                ),
            ),
            redirect_stdout(output),
        ):
            status = main(["reproduce", "--path", "/project/log"])

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "# Reproduction Summary\n\n3 total\n")

        rejected = (
            ["status", "--path", "/project/log", "--run-id", "run"],
            ["stop", "--path", "/project/log", "--run-id", "run"],
            ["resume", "--path", "/project/log", "--run-id", "run"],
            [
                "promote",
                "--path",
                "/project/log",
                "--run-id",
                "run",
                "--execution-id",
                "execution",
            ],
            ["report", "--path", "/project/log"],
            ["artifacts", "list", "--path", "/project/log"],
            ["commands", "list", "--path", "/project/log"],
            [
                "artifacts",
                "show",
                "--path",
                "/project/log",
                "--entry",
                "e003",
                "--artifact",
                "data/result.txt",
            ],
        )
        for arguments in rejected:
            with self.subTest(action=arguments[0]), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(["reproduce", *arguments, "--recheck"])


def _results() -> ReproductionResults:
    run_id = "reproduce-20300101t000000z-fixture"
    recorded_at = "2030-01-01T00:05:00Z"
    changed = ArtifactResult(
        "e003",
        "data/changed.bin",
        "pyrun-exec/v1:" + "1" * 64,
        "changed",
        "content_changed",
        recorded_at,
        run_id,
        ComparisonRecord(
            "opaque_file",
            Fingerprint("sha256", digest="a" * 64),
            Fingerprint("sha256", digest="b" * 64),
        ),
    )
    run = RunResult(
        run_id,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        "2030-01-01T00:00:00Z",
        recorded_at,
        {
            "changed": 1,
            "comparison_failed": 0,
            "failed": 0,
            "matched": 0,
            "skipped": 0,
        },
        RunFolder("tmp/reproduction/2030-01-01/reproduce-query", "available"),
        (),
        {
            "blocked": 0,
            "failed": 0,
            "not_automatic": 0,
            "reproduction_not_needed": 0,
            "unchanged_blocked": 0,
            "unchanged_failed": 0,
            "succeeded": 1,
            "total": 1,
        },
    )
    return ReproductionResults("docs/research.md", recorded_at, (changed,), (run,))


if __name__ == "__main__":
    unittest.main()
