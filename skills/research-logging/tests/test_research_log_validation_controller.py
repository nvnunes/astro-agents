from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import CLI, write

CONTROLLER = importlib.import_module("validation.controller")
TARGET = importlib.import_module("validation.target_records")


def make_no_semantic_log(root: Path) -> Path:
    (root / ".git").mkdir()
    summary = root / "docs" / "empty.md"
    (summary.with_suffix("") / "entries").mkdir(parents=True)
    write(
        summary,
        "# Empty Log\n\n"
        "## Summary\n\n"
        "No quantitative claims.\n\n"
        "## Entries\n",
    )
    return summary


class ValidationControllerTests(unittest.TestCase):
    def test_no_semantic_log_completes_and_publishes_only_target_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            before = summary.read_bytes()
            result = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(summary.read_bytes(), before)
            output = summary.with_suffix("")
            generated = {
                path.name
                for path in output.iterdir()
                if path.name != "entries" and not path.name.startswith(".")
            }
            self.assertEqual(
                generated,
                {
                    "validation.md",
                    TARGET.RECORD_FILENAME,
                    TARGET.CACHE_FILENAME,
                },
            )
            stored = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            self.assertEqual(stored["failures"], [])

    def test_repeat_no_semantic_validation_needs_no_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            second = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")

    def test_operational_failure_retains_prior_completed_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            completed = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            report = summary.with_suffix("") / "validation.md"
            before = report.read_bytes()

            with mock.patch.object(
                CONTROLLER, "scan_log", side_effect=RuntimeError("interrupted")
            ):
                failed = CONTROLLER.validate(
                    summary, result_date="2026-08-16", jobs=1
                )
            self.assertEqual(failed["status"], "error")
            self.assertTrue(failed["prior_report_retained"])
            self.assertTrue(failed["progress_retained"])
            self.assertEqual(report.read_bytes(), before)

    def test_cli_validate_returns_structured_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            with mock.patch("builtins.print") as printed:
                status = CLI.main(
                    [
                        "validate",
                        "--summary",
                        str(summary),
                        "--date",
                        "2026-08-15",
                        "--jobs",
                        "1",
                    ]
                )
            self.assertEqual(status, 0)
            payload = json.loads(printed.call_args.args[0])
            self.assertEqual(payload["status"], "complete")

    def test_deterministic_failures_are_durable_result_data(self) -> None:
        bundle = mock.Mock()
        bundle.state = {
            "validation_rules_version": "rules-v1",
            "component_versions": {"integrity": 1},
            "input_projection_versions": {"exact-material": 1},
            "completed_checks": [],
            "result": {
                "date": "2026-08-15",
                "failures": [
                    {"scope": "e001", "target": "missing.csv", "checks": ["Integrity"]}
                ],
            },
        }
        bundle.decisions = {"judgments": []}
        record = CONTROLLER._target_record(
            "docs/mini.md",
            bundle,
            TARGET.empty_record("docs/mini.md", "rules-v1"),
        )
        self.assertEqual(record["failures"], bundle.state["result"]["failures"])


if __name__ == "__main__":
    unittest.main()
