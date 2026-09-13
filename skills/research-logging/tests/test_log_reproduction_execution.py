"""Accepted invocation execution boundary coverage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.reproduction_contract import (
    ReproductionPlan,
    accepted_command,
    accepted_invocation,
)
from reproduction_fixed_plan_test_support import accepted_plan
from test_log_reproduction_planning import _Fixture, _plan


class ReproductionExecutionTests(unittest.TestCase):
    def test_execution_requires_an_accepted_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            accepted_command(accepted_plan(), "e001", "pyrun-exec/v1:" + "0" * 64)

    def test_plan_never_carries_attempt_lineage(self) -> None:
        self.assertNotIn("attempt", accepted_plan().serialized())

    def test_reader_rejects_noncanonical_plan_bytes_before_execution(self) -> None:
        raw = json.dumps(accepted_plan().as_dict(), indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            ReproductionPlan.from_json(raw)

    def test_accepted_invocation_does_not_reload_current_entry_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory))
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("output\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)
            with mock.patch(
                "log_commands.reproduction_contract.load_pyrun_state",
                side_effect=AssertionError("must not reload pyrun"),
                create=True,
            ):
                accepted = accepted_invocation(plan, entry.id, identity)
            self.assertEqual(accepted.execution_id, identity)
