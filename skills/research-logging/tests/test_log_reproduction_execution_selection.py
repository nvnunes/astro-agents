"""Fresh selection versus fixed-plan resume boundary coverage."""

from __future__ import annotations

import unittest

from log_commands.reproduction_planner import (
    INCREMENTAL_SELECTION,
    RECHECK_SELECTION,
    ReproductionSelection,
)


class ReproductionExecutionSelectionTests(unittest.TestCase):
    def test_only_fresh_selection_policies_exist(self) -> None:
        self.assertEqual(ReproductionSelection().policy, INCREMENTAL_SELECTION)
        self.assertEqual(RECHECK_SELECTION, "recheck")
        policy = ReproductionSelection.__annotations__.get("policy", "")
        self.assertNotIn("resume", policy)
