"""Status/query surfaces read fixed plan state rather than attempt history."""

from __future__ import annotations

import unittest

from log_commands.reproduction_jobs import STATUS_SCHEMA
from reproduction_fixed_plan_test_support import accepted_plan


class ReproductionQueryTests(unittest.TestCase):
    def test_status_schema_and_plan_inventory_are_current(self) -> None:
        self.assertEqual(STATUS_SCHEMA, "research-log-reproduction-status/7")
        self.assertEqual(accepted_plan().commands, ())
