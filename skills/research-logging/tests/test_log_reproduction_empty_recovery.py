"""Fixed-plan no-work and recovery contract coverage."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_publication import (
    empty_reproduction_recovery_needed,
    recover_empty_reproduction_results,
)
from log_commands.reproduction_result_storage import (
    load_reproduction_report_projection,
)
from reproduction_fixed_plan_test_support import accepted_plan, publication_run
from research_log_paths import REPRODUCTION_REPORT, RESULTS_STORE
from research_log_result_store import result_generation, result_snapshot


class EmptyRecoveryTests(unittest.TestCase):
    def test_empty_whole_log_recovery_replaces_only_unsupported_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, _run_root = publication_run(project)
            (log.root / "entries").mkdir()
            path = log.root / RESULTS_STORE
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE obsolete(value TEXT)")
            plan = accepted_plan()
            self.assertTrue(empty_reproduction_recovery_needed(log, plan))
            self.assertTrue(
                recover_empty_reproduction_results(
                    log, plan, updated_at="2030-01-01T00:00:00Z"
                )
            )
            self.assertEqual(load_reproduction_report_projection(path).runs, ())
            self.assertEqual(result_generation(log.root, "reproduction"), 1)
            self.assertTrue((log.root / REPRODUCTION_REPORT).is_file())
            with result_snapshot(log.root) as db:
                self.assertIsNotNone(
                    db.execute(
                        "SELECT 1 FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                )
            self.assertFalse(empty_reproduction_recovery_needed(log, plan))
            self.assertFalse(
                recover_empty_reproduction_results(
                    log, plan, updated_at="2030-01-01T00:00:01Z"
                )
            )
            self.assertEqual(result_generation(log.root, "reproduction"), 1)

    def test_no_work_plan_is_canonical_and_has_no_lifecycle_history(self) -> None:
        plan = accepted_plan()
        raw = plan.serialized().encode()
        self.assertEqual(ReproductionPlan.from_json(raw), plan)
        self.assertNotIn("source_snapshot", plan.as_dict())
        self.assertNotIn("attempts", plan.as_dict())

    def test_old_plan_schema_is_rejected_without_recovery(self) -> None:
        raw = accepted_plan().as_dict()
        raw["schema"] = "research-log-reproduction-plan/7"
        import json

        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            ReproductionPlan.from_json(json.dumps(raw, sort_keys=True).encode())


if __name__ == "__main__":
    unittest.main()
