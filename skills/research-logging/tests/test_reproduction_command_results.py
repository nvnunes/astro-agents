"""Actual invocation facts determine outcomes without a second checkpoint state."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.reproduction_command_results import (
    blocked_command_observation,
    completed_command_observation,
)
from log_commands.reproduction_domain import (
    CommandOutcome,
    ProblemStage,
    ReproductionDomainError,
)
from log_commands.reproduction_execution import _CurrentProcessResult, _ProcessOutcome
from log_commands.reproduction_run import CommandResult
from stream_capture import StreamFailure
from test_reproduction_canonical_records import FINGERPRINT, WHEN, command


class CommandObservationTests(unittest.TestCase):
    def setUp(self):
        self.work = command(
            "producer",
            outputs=(("data/first.txt", "file"), ("data/second.txt", "file")),
        )
        prepared = mock.Mock(
            entry=self.work.identity.entry,
            cid=self.work.identity.cid,
            execution_id=self.work.identity.execution_id,
            execution=self.work.execution,
        )
        self.invocation = _CurrentProcessResult(
            prepared,
            _ProcessOutcome(0, False, None, None, ()),
            1.25,
            Path("/private/tmp/recorded-scratch"),
            WHEN,
            "diagnostics/producer/stdout.log",
            "diagnostics/producer/stderr.log",
            (
                "/usr/bin/sandbox-exec",
                "-p",
                "recorded policy",
                "/env/bin/python",
                "scripts/producer.py",
            ),
            "/run/workspace/entry",
        )
        self.outputs = tuple(
            {"artifact": name, "fingerprint": FINGERPRINT.as_dict()}
            for name, _ in self.work.execution.recipe.outputs
        )

    def observe(self, *, outcome=None, outputs=None):
        invocation = (
            self.invocation
            if outcome is None
            else replace(self.invocation, outcome=outcome)
        )
        return completed_command_observation(
            self.work, invocation, WHEN, self.outputs if outputs is None else outputs
        )

    def test_success_retains_actual_confined_arguments_cwd_streams_and_outputs(self):
        with mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("read")):
            result, problems = self.observe()
        self.assertEqual(problems, ())
        self.assertEqual(result.outcome, CommandOutcome.SUCCEEDED)
        self.assertEqual(result.argv, self.invocation.argv)
        self.assertEqual(result.cwd, "/run/workspace/entry")
        self.assertEqual(result.stdout_path, "diagnostics/producer/stdout.log")
        self.assertEqual(set(result.outputs), {"data/first.txt", "data/second.txt"})
        self.assertEqual(CommandResult.from_dict(result.as_dict()), result)

    def test_launch_exception_keeps_original_type_message_and_attempt_timestamp(self):
        result, problems = self.observe(
            outcome=_ProcessOutcome(
                None,
                False,
                "execution_exception",
                "original launch error",
                (),
                ProblemStage.LAUNCH,
                "OSError",
            )
        )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(result.started_at, WHEN)
        self.assertEqual(problems[0].stage, ProblemStage.LAUNCH)
        self.assertEqual(problems[0].observed["error_type"], "OSError")
        self.assertEqual(problems[0].explanation, "original launch error")
        self.assertEqual(
            problems[0].locations[0].path, self.work.entry_root + "/scripts/producer.py"
        )

    def test_nonzero_exit_is_failed_even_when_all_outputs_are_present(self):
        result, problems = self.observe(
            outcome=_ProcessOutcome(7, False, None, None, ())
        )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(result.problem_ids, (problems[0].problem_id,))
        self.assertEqual(problems[0].subject, self.work.identity)
        self.assertEqual(problems[0].observed["returncode"], 7)
        self.assertEqual(problems[0].code, "execution_failed")

    def test_missing_outputs_derives_failure_and_names_exact_missing_members(self):
        result, problems = self.observe(outputs=self.outputs[:1])
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(problems[0].observed["missing_outputs"], ("data/second.txt",))
        self.assertEqual(set(result.outputs), {"data/first.txt"})
        self.assertEqual(problems[0].code, "output_missing")

    def test_capture_errors_survive_even_when_nonzero_exit_is_primary(self):
        captures = (
            StreamFailure(
                "stdout diagnostics", True, OSError("original capture failure")
            ),
        )
        _, problems = self.observe(
            outcome=_ProcessOutcome(7, False, None, None, (), capture_failures=captures)
        )
        self.assertEqual(len(problems), 1)
        observed = problems[0].observed["capture_failures"][0]
        self.assertEqual(observed["message"], "original capture failure")
        self.assertEqual(observed["error_type"], "OSError")
        self.assertTrue(observed["required"])

    def test_materialization_retains_stage_type_and_original_specific_code(self):
        result, problems = self.observe(
            outcome=_ProcessOutcome(
                0,
                False,
                "specific.materialization.guard",
                "original source changed",
                (),
                ProblemStage.MATERIALIZE,
                "ActionError",
            )
        )
        self.assertEqual(result.outcome, CommandOutcome.FAILED)
        self.assertEqual(problems[0].code, "output_materialization_failed")
        self.assertEqual(problems[0].stage, ProblemStage.MATERIALIZE)
        self.assertEqual(
            problems[0].observed["original_code"], "specific.materialization.guard"
        )
        self.assertEqual(problems[0].observed["error_type"], "ActionError")

    def test_stopped_attempt_has_no_terminal_research_result(self):
        self.assertEqual(
            self.observe(
                outcome=_ProcessOutcome(None, True, "stop_requested", "stop", ())
            ),
            (None, ()),
        )

    def test_prerequisite_block_records_links_without_invocation_or_copied_problem(
        self,
    ):
        consumer = command("consumer", dependencies=(self.work.identity,))
        result = blocked_command_observation(consumer.identity, (self.work.identity,))
        self.assertEqual(result.outcome, CommandOutcome.BLOCKED)
        self.assertEqual(result.blocked_by, (self.work.identity,))
        self.assertEqual(
            (result.argv, result.problem_ids, result.outputs), ((), (), {})
        )

    def test_different_invocation_owner_is_rejected(self):
        invocation = replace(
            self.invocation,
            prepared=mock.Mock(
                entry="e001",
                cid="different",
                execution_id=self.work.identity.execution_id,
            ),
        )
        with self.assertRaises(ReproductionDomainError):
            completed_command_observation(self.work, invocation, WHEN, self.outputs)

    def test_duplicate_undeclared_and_malformed_output_records_are_rejected(self):
        first = self.outputs[0]
        for outputs in (
            (first, first),
            (
                *self.outputs,
                {
                    "artifact": "data/undeclared.txt",
                    "fingerprint": FINGERPRINT.as_dict(),
                },
            ),
            ({"artifact": "data/first.txt", "fingerprint": {"digest": "invalid"}},),
            ({"artifact": 42, "fingerprint": FINGERPRINT.as_dict()},),
            ({"artifact": "data/first.txt", "extra": True},),
        ):
            with (
                self.subTest(outputs=outputs),
                self.assertRaises(ReproductionDomainError),
            ):
                self.observe(outputs=outputs)
