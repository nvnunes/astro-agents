"""Fixed-plan no-work and recovery contract coverage."""

from __future__ import annotations

import unittest

from log_commands.reproduction_contract import ReproductionPlan
from reproduction_fixed_plan_test_support import accepted_plan


class EmptyRecoveryTests(unittest.TestCase):
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
