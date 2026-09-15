"""Rejected-producer command diagnostic replacement contracts."""

import tempfile
import unittest
from pathlib import Path

from research_log_result_store import result_transaction
from validation.command_diagnostics import (
    load_command_diagnostic,
    publish_command_diagnostic,
)


class RejectedProducerTests(unittest.TestCase):
    def test_diagnostic_slot_replaces_prior_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            root.mkdir(exist_ok=True)
            with result_transaction(root):
                pass
            a = {
                "identity": "cmd:one",
                "entry": "e001",
                "document": "e001.md",
                "fence": 1,
                "ordinal": 1,
                "script": "x.py",
            }
            b = {**a, "identity": "cmd:two"}
            publish_command_diagnostic(root, "study.md", "producer.missing", [a])
            second = publish_command_diagnostic(
                root, "study.md", "producer.missing", [b]
            )
            self.assertEqual(
                load_command_diagnostic(root)["diagnostic_id"],
                second,
            )
            self.assertEqual(load_command_diagnostic(root)["records"], [b])
