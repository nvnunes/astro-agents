"""Status/query surfaces read fixed plan state rather than attempt history."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from log_commands.dispatcher import main
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_jobs import STATUS_SCHEMA
from reproduction_fixed_plan_test_support import accepted_plan


class ReproductionQueryTests(unittest.TestCase):
    def test_status_schema_and_plan_inventory_are_current(self) -> None:
        self.assertEqual(STATUS_SCHEMA, "research-log-reproduction-status/7")
        self.assertEqual(accepted_plan().commands, ())

    def test_current_plan_rejects_plan8_and_execution_targets(self) -> None:
        value = accepted_plan().as_dict()
        value["schema"] = "research-log-reproduction-plan/8"
        with self.assertRaises(ValueError):
            ReproductionPlan.from_json(_canonical(value))

        value = accepted_plan().as_dict()
        value["target"] = {
            "entry": "e001",
            "execution_id": "pyrun-exec/v1:" + "1" * 64,
            "kind": "execution",
        }
        with self.assertRaises(ValueError):
            ReproductionPlan.from_json(_canonical(value))

    def test_report_dispatch_supports_current_queries_only(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "log_commands.dispatcher.resolve_log", return_value=mock.sentinel.log
            ),
            mock.patch(
                "log_commands.reproduction_queries.reproduction_report",
                return_value="report\n",
            ) as report,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                main(["reproduce", "report", "--path", "log", "--entry", "e001"]),
                0,
            )
        self.assertEqual(output.getvalue(), "report\n")
        report.assert_called_once_with(mock.sentinel.log, entry="e001")

    def test_summary_and_root_dispatch_remain_available(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "log_commands.dispatcher.resolve_log", return_value=mock.sentinel.log
            ),
            mock.patch(
                "log_commands.reproduction_queries.reproduction_summary_text",
                return_value="summary\n",
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                main(["reproduce", "report", "--path", "log", "--summary"]), 0
            )
        self.assertEqual(output.getvalue(), "summary\n")

        output = io.StringIO()
        root = mock.sentinel.root
        with (
            mock.patch(
                "log_commands.reproduction_queries.root_reproduction_summary",
                return_value={"coverage": {"unavailable": False}},
            ) as summary,
            mock.patch(
                "log_commands.reproduction_queries.compose_root_reproduction_summary",
                return_value="root\n",
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                main(["reproduce", "report", "--root", str(root), "--summary"]),
                0,
            )
        self.assertEqual(output.getvalue(), "root\n")
        summary.assert_called_once_with(Path(str(root)))

    def test_execution_report_selector_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            main(["reproduce", "report", "--path", "log", "--run-id", "old-run"])

    def test_bare_reproduction_rejects_retired_execution_selectors(self) -> None:
        for option in ("--execution-id", "--verify-repair"):
            arguments = ["reproduce", "--path", "log", option]
            if option == "--execution-id":
                arguments.append("pyrun-exec/v1:" + "1" * 64)
            with self.subTest(option=option), self.assertRaises(SystemExit):
                main(arguments)


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
