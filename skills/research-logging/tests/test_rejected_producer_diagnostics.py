"""Typed authoring diagnostic slot contracts."""

import tempfile
import unittest
from pathlib import Path

from log_commands.inspection_queries import Query, inspect_result
from validation.result_storage import publish_diagnostic_commands


class RejectedProducerTests(unittest.TestCase):
    def test_diagnostic_slot_replaces_prior_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = {
                "identity": "cmd:one",
                "entry": "e001",
                "document": "e001.md",
                "fence": 1,
                "ordinal": 1,
                "script": "x.py",
            }
            b = {**a, "identity": "cmd:two"}
            publish_diagnostic_commands(root, "study.md", "producer.missing", [a])
            second = publish_diagnostic_commands(
                root, "study.md", "producer.missing", [b]
            )
            self.assertEqual(
                inspect_result(root, Query(action="list", kind="diagnostic"))["items"][
                    0
                ]["result_id"],
                second,
            )
