"""Validation-specific saved query contracts."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.dispatcher import main
from research_log_result_store import ResultStoreError
from test_validation_read_model import _publish, _synthetic_snapshot
from validation.read_model import (
    ValidationQueryError,
    finding_detail,
    list_findings,
)


class SavedQueryTests(unittest.TestCase):
    def test_public_dispatcher_preserves_validation_store_failure_code(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "log_commands.dispatcher._dispatch_validate",
                side_effect=ResultStoreError("validation.store.busy", "locked"),
            ),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(main(["validate", "show"]), 2)
        self.assertIn("validation.store.busy", output.getvalue())

    def test_finding_page_has_exact_total_and_generation_bound_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            _publish(root, _synthetic_snapshot(root, 2))
            first = list_findings(root, limit=1)
            self.assertEqual(first["total"], 2)
            self.assertEqual(len(first["items"]), 1)
            self.assertIn("next_cursor", first)
            second = list_findings(
                root, limit=1, cursor=str(first["next_cursor"])
            )
            self.assertEqual(second["total"], 2)
            self.assertEqual(len(second["items"]), 1)
            self.assertNotEqual(first["items"], second["items"])

            _publish(root, _synthetic_snapshot(root, 3))
            with self.assertRaisesRegex(ValidationQueryError, "cursor"):
                list_findings(root, limit=1, cursor=str(first["next_cursor"]))

    def test_saved_lists_and_detail_expose_no_generic_result_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            _publish(root, _synthetic_snapshot(root, 1))
            listed = list_findings(root)
            self.assertEqual(
                listed["schema"], "research-log-validation-finding-list/1"
            )
            self.assertNotIn("result_id", listed)
            detail = finding_detail(root, listed["items"][0]["finding_id"])
            self.assertEqual(
                detail["schema"], "research-log-validation-finding-detail/1"
            )
            self.assertNotIn("result_id", detail)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["results"])
            self.assertEqual(raised.exception.code, 2)
