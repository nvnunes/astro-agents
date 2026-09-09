from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.dispatcher import main
from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan, source_snapshot
from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_queries import (
    compose_root_reproduction_summary,
    list_reproduction_artifacts,
    reproduction_report,
    reproduction_summary,
    root_reproduction_summary,
    show_reproduction_artifact,
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
    def test_report_does_not_read_the_former_result_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "research.md"
            log_root = summary.with_suffix("")
            legacy = log_root / "reproduction" / "results.json"
            legacy.parent.mkdir(parents=True)
            summary.write_text("# Research\n", encoding="utf-8")
            legacy.write_text(_results().serialized(), encoding="utf-8")

            with self.assertRaisesRegex(ActionError, "no cached reproduction"):
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
            log, entry=None, include_all=False, jobs=4, recheck=False
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
                    "reused": 0,
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
                        "command_failed": 0,
                        "command_skipped_or_blocked": 0,
                        "comparison_failed": 0,
                        "stale": 0,
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
                    "command_failed": 2,
                    "command_skipped_or_blocked": 1,
                    "comparison_failed": 0,
                    "stale": 0,
                    "total": 3,
                },
                "total": 6,
            },
            "commands": {
                "reused": 2,
                "selected": {
                    "blocked": 1,
                    "failed": 1,
                    "succeeded": 2,
                    "total": 4,
                },
                "skipped_by_policy": 1,
                "total": 7,
            },
            "generated_at": "2030-01-01T00:00:00Z",
            "run_id": "reproduce-20300101t000000z-fixture",
            "schema": "research-log-reproduction-summary/1",
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
            {"complete": 1, "not_run": 1, "total": 2, "unavailable": 0},
        )
        self.assertEqual(result["totals"]["commands"]["total"], 7)
        self.assertEqual(result["totals"]["artifacts"]["total"], 6)
        report = compose_root_reproduction_summary(result)
        self.assertIn("| `docs/one` | 1 | 2 | 2 | 1 | 1 | 7 |", report)
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
                return_value={"schema": "research-log-reproduction-summary/1"},
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
            '{"schema": "research-log-reproduction-summary/1"}\n',
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
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), '{"schema":"fixture"}\n')
        dry_run.assert_called_once_with(
            log, entry="e003", include_all=False, jobs=1, recheck=True
        )

        output = StringIO()
        with (
            mock.patch("log_commands.dispatcher.resolve_log", return_value=log),
            mock.patch(
                "log_commands.reproduction_jobs.launch_reproduction",
                return_value="reproduce-fixture",
            ) as launch,
            redirect_stdout(output),
        ):
            status = main(["reproduce", "--path", "/project/log", "--recheck"])

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "reproduce-fixture\n")
        launch.assert_called_once_with(
            log, entry=None, include_all=False, jobs=1, recheck=True
        )

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
            "reused": 0,
            "succeeded": 1,
            "total": 1,
        },
    )
    return ReproductionResults("docs/research.md", recorded_at, (changed,), (run,))


if __name__ == "__main__":
    unittest.main()
