"""Accepted invocation execution boundary coverage."""

from __future__ import annotations

import importlib
import io
import json
import unittest
from unittest import mock

from log_commands.reproduction_domain import ExecutionRef
from log_commands.reproduction_work_plan import ReproductionPlan
from stream_capture import StreamCapture, StreamDestination
from test_reproduction_canonical_records import blocked_plan

EXECUTION = importlib.import_module("log_commands.reproduction_execution")


class ReproductionExecutionTests(unittest.TestCase):
    def test_required_capture_failure_is_a_reproduction_failure(self) -> None:
        class FailingDestination(io.BytesIO):
            def write(self, value: bytes) -> int:
                raise OSError("capture unavailable")

        capture = StreamCapture()
        capture.start(
            io.BytesIO(b"complete diagnostics"),
            (StreamDestination("diagnostics", FailingDestination(), True),),
        )
        launched = EXECUTION._LaunchedProcess(mock.Mock(), capture)

        outcome = EXECUTION._finish_streams(
            launched, EXECUTION._ProcessOutcome(0, False, None, None, ())
        )

        self.assertEqual(outcome.failure_code, "capture_failed")
        self.assertIn("capture unavailable", outcome.failure_message or "")
        self.assertEqual(outcome.error_type, "OSError")
        self.assertEqual(outcome.failure_stage.value, "capture")
        self.assertIsInstance(outcome.capture_failures[0].error, OSError)

    def test_launch_exception_retains_original_type_message_and_stage(self) -> None:
        prepared = mock.Mock(entry="e001", execution_id="accepted-execution")
        prepared.environment = {EXECUTION.RUNNER_MARKER: "accepted-marker"}
        registry = mock.Mock()
        registry.stop_all.return_value = ()
        registry.records.return_value = ()
        callbacks = EXECUTION._RunCallbacks(
            lambda: False, 300, lambda at: None, lambda workers: None
        )
        with (
            mock.patch.object(EXECUTION, "_WorkerRegistry", return_value=registry),
            mock.patch.object(
                EXECUTION,
                "_launch_process",
                side_effect=OSError("original launch failure"),
            ),
        ):
            outcome, started_at, elapsed = EXECUTION._run_prepared(
                prepared, ("actual-runner",), mock.Mock(), callbacks
            )
        self.assertEqual(
            (outcome.failure_code, outcome.failure_message),
            ("execution_exception", "original launch failure"),
        )
        self.assertEqual(outcome.error_type, "OSError")
        self.assertEqual(outcome.failure_stage.value, "launch")
        self.assertIsNone(started_at)
        self.assertEqual(elapsed, 0)

    def test_execution_requires_an_accepted_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside accepted work"):
            blocked_plan().command(
                ExecutionRef("e001", "fixture", "pyrun-exec/v2:" + "0" * 64)
            )

    def test_reader_rejects_noncanonical_plan_bytes_before_execution(self) -> None:
        raw = json.dumps(blocked_plan().as_dict(), indent=2).encode()
        with self.assertRaisesRegex(ValueError, "not canonical"):
            ReproductionPlan.from_json(raw)
