from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from research_log_validation_test_support import CLI, make_log, write  # noqa: E402

ACTIVITY = importlib.import_module("validation.activity")
BENCHMARK = importlib.import_module("benchmark_validation_review")


class ValidationActivityLogTests(unittest.TestCase):
    def test_activity_overhead_benchmark_pairs_enabled_and_disabled_paths(
        self,
    ) -> None:
        disabled = BENCHMARK._activity_overhead_sample(False, 1)
        enabled = BENCHMARK._activity_overhead_sample(True, 1)

        self.assertEqual(disabled["event_count"], 40)
        self.assertEqual(disabled["log_lines"], 0)
        self.assertEqual(enabled["event_count"], 40)
        self.assertEqual(enabled["log_lines"], 42)

    def test_heartbeat_identifies_oldest_active_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "docs" / "mini.md"
            write(summary, "# Mini\n")
            activity = ACTIVITY.ValidationActivityLog(
                ACTIVITY.ValidationActivityRequest(
                    summary.with_suffix(""),
                    summary,
                    "standard",
                    3,
                    True,
                    heartbeat_seconds=0.01,
                )
            )

            activity.phase("scan.inspect-identities", paths=12)
            with activity.operation(
                "inspect-identity", subject="docs/mini/data/large.h5"
            ):
                time.sleep(0.035)
            activity.finish("complete", progress_retained=True)

            text = activity.path.read_text(encoding="utf-8")
            self.assertIn('event="run-start"', text)
            self.assertIn('phase="scan.inspect-identities"', text)
            self.assertIn('event="heartbeat"', text)
            self.assertIn('oldest_operation="inspect-identity"', text)
            self.assertIn('oldest_subject="docs/mini/data/large.h5"', text)
            self.assertIn('event="operation-complete"', text)
            self.assertIn('event="run-finish" status="complete"', text)
            self.assertTrue(text.endswith("\n"))

    def test_cli_keeps_result_on_stdout_and_detail_in_activity_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary, _ = make_log(Path(temporary))
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_status = CLI.main(
                    [
                        "validate",
                        "--summary",
                        summary.as_posix(),
                        "--jobs",
                        "1",
                        "--dry-run",
                    ]
                )

            self.assertEqual(exit_status, 0)
            self.assertEqual(stderr.getvalue(), "")
            result = json.loads(stdout.getvalue())
            activity_path = Path(result["activity_log"])
            self.assertEqual(
                activity_path,
                (
                    summary.with_suffix("")
                    / "validation"
                    / ".cache"
                    / "validation.log"
                ).resolve(),
            )
            text = activity_path.read_text(encoding="utf-8")
            self.assertIn('phase="scan.discover-entries"', text)
            self.assertIn('operation="scan-entry"', text)
            self.assertIn('operation="inspect-identity"', text)
            self.assertIn('operation="resolve-reusable-subjects"', text)
            self.assertIn('operation="load-reusable-judgments"', text)
            self.assertIn('operation="apply-reusable-judgments"', text)
            self.assertIn(
                f'event="run-finish" status="{result["status"]}"', text
            )

    def test_unavailable_activity_log_does_not_change_cli_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "mini.md"
            write(summary, "# Mini\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            result = {"status": "complete", "progress_retained": False}

            with (
                mock.patch.object(
                    CLI, "ValidationActivityLog", side_effect=OSError("read only")
                ),
                mock.patch.object(CLI, "validate", return_value=result),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_status = CLI.main(
                    ["validate", "--summary", summary.as_posix()]
                )

            self.assertEqual(exit_status, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
