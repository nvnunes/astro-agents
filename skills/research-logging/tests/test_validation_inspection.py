"""Normalized validation inspection contracts."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.dispatcher import main
from log_commands.inspection_queries import InspectionError, Query, inspect_result
from research_log_result_store import ResultStoreError
from validation.batch_projection import build_batch_projection
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from validation.result_storage import (
    ValidationPublicationRequest,
    publish_validation_result,
)


class InspectionTests(unittest.TestCase):
    def test_public_dispatcher_preserves_result_store_failure_code(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "log_commands.dispatcher._dispatch_results",
                side_effect=ResultStoreError("results.store.busy", "locked"),
            ),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(main(["results", "show"]), 2)
        self.assertIn("results.store.busy", output.getvalue())

    def test_codes_page_has_exact_total_and_generation_bound_cursor(self):
        """Code aggregation is keyset-paged, rather than treating a page as all rows."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            record = MechanicalGeneratedRecord.build(
                str(root / "study.md"),
                "fixture",
                "2026-09-08",
                (
                    MechanicalCheck(
                        "entry:e001:a",
                        CheckScope.CONFORMANCE,
                        CheckStatus.FAIL,
                        "one",
                        failure=FailurePayload("fixture.one", "one", {}, "rule"),
                    ),
                    MechanicalCheck(
                        "entry:e001:b",
                        CheckScope.CONFORMANCE,
                        CheckStatus.FAIL,
                        "two",
                        failure=FailurePayload("fixture.two", "two", {}, "rule"),
                    ),
                ),
            )
            projection = build_batch_projection(
                record, invocations=(), registries=(), source_identity="source"
            )
            stored = publish_validation_result(
                ValidationPublicationRequest(root, record, projection)
            )
            first = inspect_result(
                root, Query(result_id=stored.result_id, view="codes", limit=1)
            )
            self.assertEqual(first["total"], 2)
            self.assertEqual(first["returned"], 1)
            self.assertIsNotNone(first["next_cursor"])
            second = inspect_result(
                root,
                Query(
                    result_id=stored.result_id,
                    view="codes",
                    limit=1,
                    cursor=first["next_cursor"],
                ),
            )
            self.assertEqual(second["total"], 2)
            self.assertEqual(second["returned"], 1)
            publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    record,
                    projection,
                    kind="entry",
                    entry="e001",
                )
            )
            with self.assertRaisesRegex(InspectionError, "cursor"):
                inspect_result(
                    root,
                    Query(
                        result_id=stored.result_id,
                        view="codes",
                        limit=1,
                        cursor=first["next_cursor"],
                    ),
                )
            # A replacement increments validation generation, invalidating stale pages.
            replacement = MechanicalGeneratedRecord.build(
                str(root / "study.md"), "fixture", "2026-09-09", record.checks
            )
            publish_validation_result(
                ValidationPublicationRequest(
                    root,
                    replacement,
                    build_batch_projection(
                        replacement,
                        invocations=(),
                        registries=(),
                        source_identity="source",
                    ),
                )
            )
            with self.assertRaisesRegex(InspectionError, "cursor"):
                inspect_result(
                    root,
                    Query(
                        latest=True,
                        kind="full",
                        view="codes",
                        limit=1,
                        cursor=first["next_cursor"],
                    ),
                )

    def test_sqlite_result_lists_and_exports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            r = MechanicalGeneratedRecord.build(
                str(root / "study.md"),
                "fixture",
                "2026-09-08",
                (
                    MechanicalCheck(
                        "entry:e001:check",
                        CheckScope.CONFORMANCE,
                        CheckStatus.FAIL,
                        "subject",
                        failure=FailurePayload(
                            "fixture.failure", "subject", {}, "rule"
                        ),
                    ),
                ),
            )
            p = build_batch_projection(
                r, invocations=(), registries=(), source_identity="source"
            )
            stored = publish_validation_result(ValidationPublicationRequest(root, r, p))
            self.assertEqual(
                inspect_result(root, Query(action="list", kind="full"))["items"][0][
                    "result_id"
                ],
                stored.result_id,
            )
            self.assertEqual(
                inspect_result(
                    root, Query(action="export", result_id=stored.result_id)
                )["schema"],
                "research-log-retained-result/2",
            )
