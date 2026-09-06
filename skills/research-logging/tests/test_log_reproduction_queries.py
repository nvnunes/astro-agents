from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from log_commands.context import LogContext
from log_commands.dispatcher import main
from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_queries import (
    list_reproduction_artifacts,
    reproduction_report,
    show_reproduction_artifact,
)
from log_commands.reproduction_results import ReproductionResults

FIXTURES = Path(__file__).parent / "fixtures"


class ReproductionQueryTests(unittest.TestCase):
    def test_report_list_and_show_share_current_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "research.md"
            log_root = summary.with_suffix("")
            reproduction = log_root / "reproduction"
            reproduction.mkdir(parents=True)
            summary.write_text("# Research\n", encoding="utf-8")
            text = (FIXTURES / "reproduction-result-complete-v1.json").read_text()
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


if __name__ == "__main__":
    unittest.main()
