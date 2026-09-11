from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest import mock

from log_commands.dispatcher import main
from log_commands.model import ActionError
from log_commands.reproduction_contract import REPRODUCTION_RESULT_SCHEMA
from log_commands.reproduction_jobs import launch_reproduction
from log_commands.reproduction_planner import ReproductionSelection, plan_reproduction
from log_commands.reproduction_publication import (
    empty_reproduction_recovery_needed,
    recover_empty_reproduction_results,
)
from log_commands.reproduction_results import (
    ReproductionResults,
    load_reproduction_results,
)
from test_log_reproduction_planning import _admission, _Fixture


class EmptyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.fixture = _Fixture(Path(self.scratch.name))
        self.fixture.entry(1)
        self.result = self.fixture.log_root / ".cache/reproduction/results.json"
        self.result.parent.mkdir(parents=True)
        self.old = ReproductionResults(
            "docs/study.md", "2030-01-01T00:00:00Z", (), ()
        ).as_dict()
        self.old["schema"] = "research-log-reproduction-result/7"
        self.result.write_text(json.dumps(self.old))
        self.admission = _admission(self.fixture)
        self.enterContext(
            mock.patch(
                "log_commands.reproduction_planner._admit_validation",
                return_value=(self.admission, mock.sentinel.validation),
            )
        )

    def plan(self):
        return plan_reproduction(
            self.fixture.log,
            entry=None,
            include_all=False,
            selection=ReproductionSelection("recheck"),
        )

    def test_cli_preview_is_read_only_and_launch_recovers_without_a_run(self):
        args = ["reproduce", "--path", str(self.fixture.log_root), "--recheck"]
        before = self.result.read_bytes()
        with (
            redirect_stdout(StringIO()),
            mock.patch("log_commands.reproduction_jobs.preflight_execution_safety"),
        ):
            self.assertEqual(main([*args, "--dry-run", "--summary"]), 0)
        self.assertEqual(self.result.read_bytes(), before)
        with (
            redirect_stdout(StringIO()) as output,
            mock.patch("log_commands.reproduction_jobs._spawn_supervisor") as spawn,
        ):
            self.assertEqual(main(args), 0)
        spawn.assert_not_called()
        current = load_reproduction_results(self.result)
        self.assertEqual(current.as_dict()["schema"], REPRODUCTION_RESULT_SCHEMA)
        self.assertEqual(current.runs, ())
        self.assertEqual(current.artifacts, ())
        self.assertIn("no commands executed", output.getvalue().lower())
        self.assertTrue((self.fixture.log_root / "reproduction.md").is_file())
        self.assertFalse((self.fixture.root / "tmp/reproduction").exists())
        supported = self.result.read_bytes()
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args), 0)
        self.assertEqual(self.result.read_bytes(), supported)

    def test_incremental_does_not_replace_unsupported_history(self):
        before = self.result.read_bytes()
        with self.assertRaises(ActionError) as caught:
            launch_reproduction(self.fixture.log, entry=None, include_all=False)
        self.assertEqual(
            caught.exception.code, "reproduction.results.schema_unsupported"
        )
        self.assertEqual(self.result.read_bytes(), before)

    def test_partial_or_blocked_plan_cannot_recover(self):
        plan = self.plan()
        for other in (
            replace(plan, target={"kind": "entry", "entry": "e001"}),
            replace(plan, cases=({"artifact": "data/retained"},)),
            replace(plan, failures=({"reason": "validation_blocked"},)),
        ):
            self.assertFalse(
                empty_reproduction_recovery_needed(self.fixture.log, other)
            )

    def test_recorded_commands_prevent_empty_recovery_even_if_plan_is_empty(self):
        from log_commands.reproduction_planner import ReproductionCommandInventory

        before = self.result.read_bytes()
        with mock.patch(
            "log_commands.reproduction_publication.project_reproduction_command_inventory",
            return_value=ReproductionCommandInventory(1, 1),
        ):
            self.assertFalse(
                recover_empty_reproduction_results(
                    self.fixture.log, self.plan(), updated_at="2030-01-02T00:00:00Z"
                )
            )
        self.assertEqual(self.result.read_bytes(), before)

    def test_supported_malformed_results_are_not_outdated(self):
        plan = self.plan()
        self.result.write_text(json.dumps({"schema": REPRODUCTION_RESULT_SCHEMA}))
        with self.assertRaises(ActionError) as caught:
            empty_reproduction_recovery_needed(self.fixture.log, plan)
        self.assertEqual(caught.exception.code, "reproduction.results.invalid")

    def test_source_change_prevents_recovery_without_touching_results(self):
        plan = self.plan()
        before = self.result.read_bytes()
        self.fixture.summary.write_text("# Changed after preview\n")
        with self.assertRaises(ActionError):
            recover_empty_reproduction_results(
                self.fixture.log, plan, updated_at="2030-01-02T00:00:00Z"
            )
        self.assertEqual(self.result.read_bytes(), before)
        self.assertFalse((self.fixture.log_root / "reproduction.md").exists())


if __name__ == "__main__":
    unittest.main()
