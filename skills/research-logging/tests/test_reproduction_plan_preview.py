"""Canonical fresh preview, immutable pagination and write-free public dispatch."""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
from log_commands import (
    dispatcher,
)
from log_commands import (
    reproduction_jobs as jobs,
)
from log_commands.model import ActionError
from log_commands.reproduction_plan_preview import plan_page, render_plan_page
from reproduction_planning_test_support import prepare_plan
from test_reproduction_canonical_records import blocked_plan, command
from test_reproduction_model_preservation import fanout_fixture
from validation.operation_state import research_snapshot


class ReproductionPreviewTests(unittest.TestCase):
    def test_unfingerprintable_source_is_runnable_with_exact_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _ = fanout_fixture(Path(directory), 1)
            script = entry.root / "scripts/producer.py"
            script.write_text("from math import *\n", encoding="utf-8")
            value = plan_page(prepare_plan(fixture, entry), "source")
            row = next(
                item for item in value["items"] if item["identity"]["cid"] == "producer"
            )
            self.assertEqual(row["selection"], "run")
            self.assertEqual(row["reason"], "effective_code_unavailable")
            self.assertEqual(
                row["diagnosis"]["observed"]["availability"], "unsupported"
            )
            self.assertIn(
                "selected on every incremental plan",
                row["explanation"],
            )
            text = render_plan_page(value)
            self.assertIn("selected on every incremental plan", text)
            self.assertIn("wildcard_import", text)

    def test_changed_supported_source_is_runnable_with_expected_and_current_facts(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, _ = fanout_fixture(Path(directory), 1)
            script = entry.root / "scripts/producer.py"
            script.write_text("VALUE = 2\n", encoding="utf-8")
            value = plan_page(prepare_plan(fixture, entry), "source")
            row = next(
                item for item in value["items"] if item["identity"]["cid"] == "producer"
            )
            self.assertEqual(row["selection"], "run")
            self.assertEqual(row["diagnosis"]["code"], "effective_code_changed")
            self.assertNotEqual(
                row["diagnosis"]["observed"]["expected"],
                row["diagnosis"]["observed"]["actual"],
            )
            text = render_plan_page(value)
            self.assertIn(f"Source: {script}", text)
            self.assertIn("Retained effective code:", text)
            self.assertIn("Current effective code:", text)

    def large_plan(self):
        plan = blocked_plan()
        commands = tuple(command(f"build-{number:03d}") for number in range(103))
        admission = dict(plan.admission)
        admission["executions"] = [
            {
                **work.identity.as_dict(),
                "disposition": "admitted",
                "blocking_finding_ids": [],
            }
            for work in commands
        ]
        claims = tuple(
            {
                **dict(plan.scheduling[0]),
                "identity": work.identity.as_dict(),
                "order": number,
            }
            for number, work in enumerate(commands, 1)
        )
        return replace(
            plan,
            commands=commands,
            artifacts=(),
            problems=(),
            admission=admission,
            scheduling=claims,
        )

    def test_public_plan_uses_native_preparation_without_saved_or_validation_writes(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture, _, _ = fanout_fixture(Path(directory), 2)
            before = research_snapshot(fixture.summary)
            output = io.StringIO()
            with (
                mock.patch.object(jobs, "preflight_execution_safety"),
                contextlib.redirect_stdout(output),
            ):
                status = dispatcher.main(
                    [
                        "reproduce",
                        "plan",
                        "--path",
                        str(fixture.log.root),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(status, 0)
            page = json.loads(output.getvalue())
            self.assertEqual(page["commands"]["total"], 4)
            self.assertEqual(page["commands"]["ready_to_run"], 4)
            self.assertEqual(page["artifacts"], {"total": 5})
            self.assertNotIn("admission", page)
            self.assertNotIn("problems", page)
            self.assertEqual(research_snapshot(fixture.summary), before)
            self.assertFalse(shared.result_store_path(fixture.log.root).exists())
            self.assertFalse((fixture.log.root / "validation.md").exists())
            self.assertFalse((fixture.log.root / "reproduction.md").exists())

    def test_all_actionable_commands_are_accessible_in_bounded_pages(self):
        plan = self.large_plan()
        first = plan_page(plan, "source", format="json")
        second = plan_page(plan, "source", cursor=first["cursor"], format="json")
        third = plan_page(plan, "source", cursor=second["cursor"], format="json")
        self.assertEqual(
            [page["returned"] for page in (first, second, third)], [50, 50, 3]
        )
        self.assertEqual(
            [page["remaining"] for page in (first, second, third)], [53, 3, 0]
        )
        self.assertTrue(
            all(page["commands"]["total"] == 103 for page in (first, second, third))
        )
        self.assertEqual(
            len(
                {
                    row["identity"]["cid"]
                    for page in (first, second, third)
                    for row in page["items"]
                }
            ),
            103,
        )
        argv = shlex.split(first["next"])
        self.assertEqual(argv[:3], ["log", "reproduce", "plan"])
        self.assertEqual(argv[argv.index("--format") + 1], "json")
        self.assertIsNone(third["cursor"])

    def test_cursor_ignores_evaluation_time_but_rejects_sources_settings_and_format(
        self,
    ):
        plan = self.large_plan()
        first = plan_page(plan, "source", format="json")
        admission = {
            **dict(plan.admission),
            "evaluated_at": "2099-01-01T00:00:00Z",
            "validation_snapshot_id": "new-evaluation",
        }
        later = replace(plan, admission=admission)
        self.assertEqual(
            plan_page(later, "source", cursor=first["cursor"], format="json")[
                "returned"
            ],
            50,
        )
        for changed, source, format in (
            (plan, "changed", "json"),
            (replace(plan, settings=replace(plan.settings, jobs=3)), "source", "json"),
            (plan, "source", "text"),
        ):
            with self.subTest(source=source, format=format):
                with self.assertRaises(ActionError) as caught:
                    plan_page(changed, source, cursor=first["cursor"], format=format)
                self.assertEqual(caught.exception.code, "reproduction.cursor.stale")

    def test_preview_counts_blocked_without_predicting_attempts_or_matches(self):
        value = plan_page(blocked_plan(), "source")
        self.assertEqual(
            value["commands"],
            {
                "total": 7,
                "ready_to_run": 1,
                "blocked": 2,
                "not_needed": 1,
                "previous_failure": 1,
                "previous_block": 1,
                "skipped_by_policy": 1,
            },
        )
        self.assertEqual(value["matched"], 3)
        text = render_plan_page(value)
        self.assertIn("Commands — 7\n  Ready to run — 1\n  Blocked — 2", text)
        self.assertIn("Recorded producer script is unavailable.", text)
        producer = next(
            work for work in blocked_plan().commands if work.identity.cid == "producer"
        )
        self.assertIn(
            f"Prerequisites: {producer.identity.entry} / {producer.identity.cid} / "
            f"{producer.identity.execution_id}",
            text,
        )
        self.assertNotIn("matched —", text.lower())


if __name__ == "__main__":
    unittest.main()
