"""Publication operates from immutable accepted-plan content."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from log_commands.model import ActionError
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_publication import verify_publication_retry_compatibility
from reproduction_fixed_plan_test_support import accepted_plan, accepted_run
from research_log_paths import REPRODUCTION_RESULTS


class ReproductionPublicationTests(unittest.TestCase):
    def test_plan_bytes_are_stable_for_retry(self) -> None:
        plan = accepted_plan()
        self.assertEqual(plan.serialized(), plan.serialized())
        self.assertNotIn("validation_snapshot", plan.as_dict())

    def test_retry_reader_rejects_any_pending_or_lineage_field(self) -> None:
        fields = accepted_plan().as_dict()
        fields["pending_writes"] = []
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            ReproductionPlan.from_json(json.dumps(fields, sort_keys=True).encode())

    def test_corrupt_prior_inventory_blocks_retry_before_executor_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, _root = accepted_run(Path(directory))
            result_path = log.root / REPRODUCTION_RESULTS
            result_path.parent.mkdir(parents=True)
            result_path.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "invalid reproduction result"):
                verify_publication_retry_compatibility(log, accepted_plan())
        fields = accepted_plan().as_dict()
        fields["attempts"] = []
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            ReproductionPlan.from_json(json.dumps(fields, sort_keys=True).encode())
