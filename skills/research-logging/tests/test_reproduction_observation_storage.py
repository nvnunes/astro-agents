"""Durable native outcomes preserve accepted facts and one owned diagnosis."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
from log_commands import dispatcher
from log_commands import reproduction_planner as planner
from log_commands.reproduction_accepted_storage import (
    ACCEPTED_WORK_DDL,
    _load_accepted_work,
    _write_accepted_work,
)
from log_commands.reproduction_artifact_results import (
    ArtifactObservationContext,
    compare_accepted_artifact,
)
from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import (
    ArtifactOutcome,
    CommandOutcome,
    NotComparedReason,
    ProblemStage,
    ReproductionDomainError,
    ReproductionProblem,
)
from log_commands.reproduction_inspection import load_inspection
from log_commands.reproduction_observation_storage import (
    OBSERVATION_DDL,
    load_observation,
    load_observation_problems,
    write_artifact_observation,
    write_command_observation,
)
from log_commands.reproduction_run import ArtifactResult, CommandResult
from log_commands.reproduction_saved_run import SavedRun
from log_commands.reproduction_saved_storage import (
    replace_reproduction_domain,
    write_saved_run,
)
from reproduction_planning_test_support import prepare_plan
from test_reproduction_accepted_storage import header
from test_reproduction_canonical_records import WHEN, attempted
from test_reproduction_model_preservation import fanout_fixture

RUN = "reproduce-2026-09-15-native"


class ObservationStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        fixture, entry, _ = fanout_fixture(Path(self.directory.name))
        self.log = fixture.log
        with mock.patch.object(
            planner, "_canonical_plan", wraps=planner._canonical_plan
        ) as project:
            prepare_plan(fixture, entry)
        state, ordered, prepared = project.call_args.args[:3]
        self.plan = planner._canonical_plan(
            state, ordered, prepared, entry, planner.PreparationHistory()
        )
        self.commands = {work.identity.cid: work for work in self.plan.commands}
        self.db = sqlite3.connect(Path(self.directory.name) / "observations.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            "CREATE TABLE runs(run_id TEXT PRIMARY KEY);"
            + ACCEPTED_WORK_DDL
            + OBSERVATION_DDL
        )
        self.db.execute("INSERT INTO runs VALUES (?)", (RUN,))
        with self.db:
            _write_accepted_work(self.db, RUN, self.plan)
        self.addCleanup(self.db.close)
        producer = self.commands["producer"]
        self.cause = ReproductionProblem(
            producer.identity,
            "execution_failed",
            ProblemStage.EXECUTE,
            "Producer exited with status 2.",
            {"returncode": 2},
        )
        self.failure = replace(
            attempted(producer, CommandOutcome.FAILED, (self.cause.problem_id,)),
            outputs={"data/output-0.txt": {"available": True}},
        )

    def write_failure(self):
        with self.db:
            write_command_observation(self.db, RUN, self.failure, (self.cause,))

    def test_failed_producer_dependents_and_independent_work_roundtrip_as_saved_facts(
        self,
    ):
        self.write_failure()
        results = [self.failure]
        for cid in ("first", "second"):
            result = CommandResult(
                self.commands[cid].identity,
                CommandOutcome.BLOCKED,
                None,
                None,
                (),
                None,
                None,
                None,
                {},
                blocked_by=(self.failure.identity,),
            )
            with self.db:
                write_command_observation(self.db, RUN, result)
            results.append(result)
        independent = attempted(self.commands["independent"])
        with self.db:
            write_command_observation(self.db, RUN, independent)
        results.append(independent)
        artifact_results = []
        for work in self.plan.artifacts:
            if work.producer.cid == "independent":
                regenerated = Path(self.directory.name) / "regenerated.txt"
                regenerated.write_text("independent", encoding="utf-8")
                result, problems = compare_accepted_artifact(
                    work,
                    self.commands["independent"],
                    regenerated,
                    context=ArtifactObservationContext(RUN, WHEN),
                )
            else:
                result = ArtifactResult(
                    work.identity,
                    ArtifactOutcome.NOT_COMPARED,
                    WHEN,
                    RUN,
                    None,
                    None,
                    work.baseline,
                    None,
                    NotComparedReason.COMMAND_FAILED
                    if work.producer.cid == "producer"
                    else NotComparedReason.COMMAND_BLOCKED,
                )
                problems = ()
            with self.db:
                write_artifact_observation(self.db, RUN, result, problems)
            artifact_results.append(result)
        with (
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch.object(
                planner,
                "plan_reproduction_work",
                side_effect=AssertionError("replanning"),
            ),
        ):
            loaded_commands = tuple(
                load_observation(self.db, RUN, result.identity) for result in results
            )
            loaded_artifacts = tuple(
                load_observation(self.db, RUN, result.identity)
                for result in artifact_results
            )
            saved = complete_saved_run(
                self.plan,
                RunCompletion(
                    RUN,
                    WHEN,
                    WHEN,
                    loaded_commands,
                    loaded_artifacts,
                    (
                        *self.plan.problems,
                        *load_observation_problems(self.db, RUN, self.failure),
                    ),
                ),
            )
            self.assertEqual(SavedRun.from_json(saved.serialized().encode()), saved)
        self.assertCountEqual(loaded_commands, results)
        self.assertEqual(len(saved.problems), 1)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM reproduction_problems").fetchone()[0],
            1,
        )
        self.assertEqual(
            _load_accepted_work(self.db, RUN, header(self.plan)), self.plan
        )
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            load_observation_problems(self.db, RUN, self.failure)[0], self.cause
        )
        self.assertEqual(self.failure.stdout_path, "tmp/run/stdout.log")
        self.assertEqual(self.failure.outputs["data/output-0.txt"]["available"], True)
        self.assertEqual(saved.summary, self.log.summary.as_posix())
        path = shared.result_store_path(self.log.root)
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text()
            )
            db.execute("BEGIN")
            replace_reproduction_domain(db)
            write_saved_run(db, saved)
            db.execute("INSERT INTO store_state VALUES ('reproduction',7,'native')")
        with (
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
        ):
            self.assertEqual(load_inspection(self.log).run, saved)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = dispatcher.main(
                    [
                        "reproduce",
                        "show",
                        "--path",
                        str(self.log.root),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["commands"]["total"], 4)

    def test_repeated_exact_terminal_commit_is_idempotent(self):
        self.write_failure()
        before = self.db.total_changes
        self.write_failure()
        self.assertEqual(self.db.total_changes, before)

    def test_differing_artifact_diagnosis_survives_sqlite_without_files(
        self,
    ):
        work = next(
            work for work in self.plan.artifacts if work.output == "data/output-0.txt"
        )
        regenerated = Path(self.directory.name) / "different.txt"
        regenerated.write_text("different selected output", encoding="utf-8")
        result, problems = compare_accepted_artifact(
            work,
            self.commands["producer"],
            regenerated,
            context=ArtifactObservationContext(RUN, WHEN),
        )
        self.assertIs(result.outcome, ArtifactOutcome.NOT_MATCHED)
        self.assertEqual(
            problems[0].observed["difference"],
            {
                "location": "line 1",
                "expected": "output-0",
                "regenerated": "different selected output",
            },
        )
        with self.db:
            write_artifact_observation(self.db, RUN, result, problems)
        self.directory.cleanup()
        self.assertFalse(Path(work.retained_path).exists())
        with (
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch.object(
                planner,
                "plan_reproduction_work",
                side_effect=AssertionError("replanning"),
            ),
        ):
            loaded = load_observation(self.db, RUN, work.identity)
            diagnoses = load_observation_problems(self.db, RUN, loaded)
        self.assertEqual(loaded, result)
        self.assertEqual(diagnoses, problems)
        self.assertEqual(loaded.evidence, result.evidence)
        self.assertEqual(
            diagnoses[0].observed["difference"],
            problems[0].observed["difference"],
        )

    def test_comparison_exception_survives_sqlite_without_files(self):
        work = next(
            work
            for work in self.plan.artifacts
            if work.output == "data/independent.txt"
        )
        regenerated = Path(self.directory.name) / "invalid.txt"
        regenerated.write_text("regenerated", encoding="utf-8")
        with mock.patch(
            "log_commands.reproduction_comparison._compare_with_profile",
            side_effect=ValueError("precise caught comparison error"),
        ):
            result, problems = compare_accepted_artifact(
                work,
                self.commands["independent"],
                regenerated,
                context=ArtifactObservationContext(RUN, WHEN),
            )
        self.assertIs(result.not_compared_reason, NotComparedReason.COMPARISON_FAILED)
        with self.db:
            write_artifact_observation(self.db, RUN, result, problems)
        self.directory.cleanup()
        with (
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
        ):
            loaded = load_observation(self.db, RUN, work.identity)
            diagnoses = load_observation_problems(self.db, RUN, loaded)
        self.assertEqual(loaded, result)
        self.assertEqual(diagnoses, problems)
        self.assertEqual(
            diagnoses[0].observed["error"],
            {
                "type": "ValueError",
                "message": "precise caught comparison error",
                "truncated": False,
            },
        )

    def test_different_terminal_facts_cannot_replace_a_finished_attempt(self):
        self.write_failure()
        with (
            self.assertRaisesRegex(ReproductionDomainError, "already immutable"),
            self.db,
        ):
            write_command_observation(
                self.db, RUN, replace(self.failure, argv=("different",)), (self.cause,)
            )
        self.assertEqual(
            load_observation(self.db, RUN, self.failure.identity), self.failure
        )

    def test_invalid_problem_reference_rolls_back_result_and_cause_rows(self):
        extra = ReproductionProblem(
            self.failure.identity,
            "capture_failed",
            ProblemStage.CAPTURE,
            "Output capture failed.",
            {"path": "partial"},
        )
        result = replace(
            self.failure, problem_ids=(self.cause.problem_id, extra.problem_id)
        )
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            write_command_observation(self.db, RUN, result, (self.cause,))
        self.assertIsNone(load_observation(self.db, RUN, self.failure.identity))
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM reproduction_problems").fetchone()[0],
            len(self.plan.problems),
        )
        self.assertEqual(
            _load_accepted_work(self.db, RUN, header(self.plan)), self.plan
        )

    def test_unknown_or_unrelated_work_is_rejected(self):
        unrelated = replace(self.cause, subject=self.commands["independent"].identity)
        with (
            self.assertRaisesRegex(ReproductionDomainError, "unrelated owner"),
            self.db,
        ):
            write_command_observation(
                self.db,
                RUN,
                replace(self.failure, problem_ids=(unrelated.problem_id,)),
                (unrelated,),
            )

    def test_valid_changed_result_payload_is_detected_read_only(self):
        self.write_failure()
        self.db.execute(
            "UPDATE run_command_results SET result_json="
            "json_set(result_json, '$.stdout_path', 'elsewhere.log')"
        )
        changes = self.db.total_changes
        with self.assertRaisesRegex(ReproductionDomainError, "immutable digest"):
            load_observation(self.db, RUN, self.failure.identity)
        self.assertEqual(self.db.total_changes, changes)

    def test_changed_problem_identity_is_detected_without_current_source(self):
        self.write_failure()
        self.db.execute(
            "UPDATE reproduction_problems SET problem_json="
            "json_set(problem_json, '$.observed.returncode', 3)"
        )
        with self.assertRaisesRegex(ReproductionDomainError, "identity disagrees"):
            load_observation_problems(self.db, RUN, self.failure)
