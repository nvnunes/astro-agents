"""Fresh native preparation shares checks/selection and never translates old state."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import research_log_result_store as shared
from log_commands import reproduction_planner as planner
from log_commands.model import ActionError
from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import (
    CommandOutcome,
    ProblemStage,
    ReproductionProblem,
    WorkSelection,
)
from log_commands.reproduction_saved_storage import (
    replace_reproduction_domain,
    write_saved_run,
)
from test_reproduction_canonical_records import WHEN, attempted
from test_reproduction_model_preservation import fanout_fixture
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.operation_state import research_snapshot


class NativePlanningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture, self.entry, self.identities = fanout_fixture(
            Path(self.directory.name), 2
        )

    def prepare(self, *, recheck=False, entry=True):
        evaluation = evaluate_mechanical(
            EvaluationRequest(self.fixture.summary, FullEvaluationTarget())
        )
        return planner.plan_reproduction_work(
            self.fixture.log,
            planner.prepare_reproduction_context(evaluation),
            entry=self.entry if entry else None,
            include_all=False,
            selection=planner.ReproductionSelection(
                planner.RECHECK_SELECTION if recheck else planner.INCREMENTAL_SELECTION
            ),
        )

    def seed(self, saved=None):
        path = shared.result_store_path(self.fixture.log_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text()
            )
            if saved is not None:
                db.execute("BEGIN")
                replace_reproduction_domain(db)
                write_saved_run(db, saved)
        return path

    def test_native_preparation_preserves_selection_without_old_projection(
        self,
    ):
        before = research_snapshot(self.fixture.summary)
        native = self.prepare()
        self.assertEqual(native.summary, str(self.fixture.summary.resolve()))
        self.assertEqual(
            {
                work.identity.execution_id
                for work in native.commands
                if work.selection is WorkSelection.RUN
            },
            set(self.identities.values()),
        )
        self.assertEqual(native.target.entry, "e001")
        self.assertEqual(research_snapshot(self.fixture.summary), before)
        self.assertFalse(shared.result_store_path(self.fixture.log_root).exists())
        self.assertNotIn("cases", native.as_dict())
        self.assertNotIn("failures", native.as_dict())

    def test_whole_log_target_uses_same_preparation_without_saving_a_preview(self):
        native = self.prepare(entry=False)
        self.assertEqual((native.target.kind, native.target.entry), ("log", None))
        self.assertEqual(len(native.commands), 4)
        self.assertFalse(shared.result_store_path(self.fixture.log_root).exists())

    def test_unsupported_history_is_readonly_and_explicit_recheck_never_reads_old_rows(
        self,
    ):
        path = self.seed()
        before = path.read_bytes()
        with self.assertRaises(ActionError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "reproduction.results.unsupported")
        self.assertEqual(path.read_bytes(), before)
        native = self.prepare(recheck=True)
        self.assertEqual(len(native.scheduling), 4)
        self.assertNotIn("sentinel", native.serialized())
        self.assertEqual(path.read_bytes(), before)

    def test_prior_failures_preserve_closure_and_owned_diagnoses_without_retry(
        self,
    ):
        initial = self.prepare()
        problems = tuple(
            ReproductionProblem(
                work.identity,
                "execution_failed",
                ProblemStage.EXECUTE,
                "Original execution failed.",
                {"returncode": 7},
            )
            for work in initial.commands
        )
        commands = tuple(
            attempted(work, CommandOutcome.FAILED, (problem.problem_id,))
            for work, problem in zip(initial.commands, problems)
        )
        saved = complete_saved_run(
            initial,
            RunCompletion("reproduce-native-prior", WHEN, WHEN, commands, (), problems),
        )
        self.seed(saved)
        native = self.prepare()
        self.assertEqual(native.scheduling, ())
        self.assertTrue(
            all(
                work.selection is WorkSelection.PREVIOUS_FAILURE
                for work in native.commands
            )
        )
        self.assertEqual(
            {problem.problem_id for problem in native.problems},
            {problem.problem_id for problem in problems},
        )
        self.assertEqual(
            {work.identity: work.source_digest for work in native.commands},
            {work.identity: work.source_digest for work in initial.commands},
        )

    def test_corrupt_native_history_fails_even_for_recheck_and_does_not_publish(self):
        initial = self.prepare()
        problems = tuple(
            ReproductionProblem(
                work.identity,
                "execution_failed",
                ProblemStage.EXECUTE,
                "Original execution failed.",
                {"returncode": 7},
            )
            for work in initial.commands
        )
        commands = tuple(
            attempted(work, CommandOutcome.FAILED, (problem.problem_id,))
            for work, problem in zip(initial.commands, problems)
        )
        saved = complete_saved_run(
            initial,
            RunCompletion("reproduce-native-prior", WHEN, WHEN, commands, (), problems),
        )
        path = self.seed(saved)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE reproduction_runs SET run_digest=?", ("0" * 64,))
        before = path.read_bytes()
        with self.assertRaises(ActionError) as caught:
            self.prepare(recheck=True)
        self.assertEqual(caught.exception.code, "reproduction.results.invalid")
        self.assertEqual(path.read_bytes(), before)
