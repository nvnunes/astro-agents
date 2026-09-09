from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.dispatcher import main
from log_commands.reproduction_contract import ReproductionPlan, source_snapshot
from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_queries import (
    list_reproduction_artifacts,
    reproduction_report,
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
            reproduction = log_root / "reproduction"
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

            self.assertEqual(
                (listing["matched"], listing["returned"], listing["omitted"]),
                (1, 1, 0),
            )
            self.assertEqual(shown["artifact"]["outcome"], "changed")
            self.assertIn("| `data/changed.bin` | **changed** |", report)

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
                "log_commands.reproduction_queries.list_reproduction_artifacts",
                return_value={"matched": 0, "returned": 0, "omitted": 0},
            ),
            redirect_stdout(output),
        ):
            status = main(
                ["reproduce", "artifacts", "list", "--path", "/project/log"]
            )

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
            status = main(
                ["reproduce", "--path", "/project/log", "--recheck"]
            )

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
    )
    return ReproductionResults("docs/research.md", recorded_at, (changed,), (run,))


if __name__ == "__main__":
    unittest.main()
