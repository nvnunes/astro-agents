"""Native saved-run replacement, immutable history and unrelated-domain safety."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
from log_commands.reproduction_completed_run import RunCompletion, complete_saved_run
from log_commands.reproduction_domain import (
    ProblemStage,
    ReproductionDomainError,
    ReproductionProblem,
    SourceRef,
    WorkSelection,
)
from log_commands.reproduction_saved_storage import (
    confirm_empty_replacement,
    latest_artifact_run,
    latest_command_run,
    load_empty_confirmation,
    load_preparation_history,
    load_saved_run,
    lookup_saved_run_generation,
    publish_saved_run,
    replace_reproduction_domain,
    write_saved_run,
)
from test_reproduction_canonical_records import WHEN, attempted, blocked_plan, mixed_run
from test_validation_snapshot_storage import SNAPSHOTS, _evaluation
from validation import command_diagnostics as diagnostics


def domain_dump(db):
    tables = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "(name LIKE 'validation_%' OR name LIKE 'command_%') ORDER BY name"
        )
    ]
    rows = []
    for table in tables:
        ddl = db.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()[0]
        contents = tuple(
            tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
        )
        rows.append((table, ddl, contents))
    rows.append(
        (
            "states",
            tuple(
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM store_state WHERE domain!='reproduction' "
                    "ORDER BY domain"
                )
            ),
        )
    )
    rows.append(
        (
            "reports",
            tuple(
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM report_materializations "
                    "WHERE kind!='reproduction' ORDER BY kind"
                )
            ),
        )
    )
    return tuple(rows)


class SavedRunStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        evaluation, summary = _evaluation(Path(self.directory.name))
        self.evaluation = evaluation
        self.root = summary.with_suffix("")
        path = shared.result_store_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as baseline:
            baseline.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text(encoding="utf-8")
            )
        SNAPSHOTS.publish_validation_snapshot(
            SNAPSHOTS.SnapshotPublicationRequest(
                self.root,
                evaluation.snapshot,
                {"requested": ("e001",), "evaluated": ("e001",), "dependency": ()},
            )
        )
        self.validation_before = SNAPSHOTS.load_validation_snapshot(self.root)
        self.db = sqlite3.connect(shared.result_store_path(self.root))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("INSERT INTO store_state VALUES ('command', 2, 'command')")
        self.db.execute(
            "INSERT INTO store_state VALUES ('reproduction', 7, 'old reproduction')"
        )
        self.db.execute(
            "INSERT INTO report_materializations VALUES "
            "('validation',1,'validation-hash','2026-09-15T00:00:00Z')"
        )
        self.db.execute(
            "INSERT INTO report_materializations VALUES "
            "('reproduction',7,'reproduction-hash','2026-09-15T00:00:00Z')"
        )
        self.db.execute(
            "INSERT INTO command_diagnostics VALUES "
            "(1,'preserved',2,'verify','example','e001','preserved diagnostic',"
            "'2026-09-15T00:00:00Z','{}')"
        )
        self.db.execute(
            "INSERT INTO command_diagnostic_records VALUES "
            "(1,0,'failure','{\"exact\":true}')"
        )
        self.db.execute(
            "INSERT INTO reproduction_metadata VALUES "
            "(1,'impossible-sentinel-old-problem','2026-09-15T00:00:00Z')"
        )
        self.db.execute(
            "INSERT INTO reproduction_runs VALUES "
            "(1,'reproduce-old','log',NULL,NULL,NULL,0,'complete',"
            "'2026-09-15T00:00:00Z','2026-09-15T00:00:00Z','tmp/old',"
            "0,0,0,0,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)"
        )
        self.db.execute(
            "INSERT INTO reproduction_run_commands "
            "(run_pk,entry,cid,execution_id,plan_order,bucket,reason,details_json) "
            "VALUES (1,'e001','old',?,0,'failed','generation_failed',?)",
            ("pyrun-exec/v2:" + "a" * 64, '["impossible-sentinel-old-problem"]'),
        )
        self.db.commit()
        self.addCleanup(self.db.close)
        self.run = mixed_run()

    def replace(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            replace_reproduction_domain(self.db)

    def test_native_publication_exact_byte_bound_preserves_history_on_rejection(self):
        for offset in (-1, 0, 1):
            with self.subTest(offset=offset):
                root = Path(self.directory.name) / f"byte-bound-{offset}"
                root.mkdir()
                baseline = replace(self.run, summary=str(root.with_suffix(".md")))
                candidate = replace(
                    baseline, run_id="reproduce-native-byte-bound-candidate"
                )
                self.assertEqual(publish_saved_run(root, baseline), 1)
                shared.record_report_materialization(
                    root, "reproduction", b"baseline", expected_generation=1
                )
                before = shared.result_store_path(root).read_bytes()
                exact = len(candidate.serialized().encode())
                with mock.patch(
                    "log_commands.reproduction_saved_run.MAX_RESULT_BYTES",
                    exact + offset,
                ):
                    if offset < 0:
                        with self.assertRaisesRegex(
                            ReproductionDomainError, "byte bound"
                        ):
                            publish_saved_run(root, candidate)
                    else:
                        self.assertEqual(publish_saved_run(root, candidate), 2)
                if offset < 0:
                    self.assertEqual(
                        shared.result_store_path(root).read_bytes(), before
                    )
                with shared.result_snapshot(root) as db:
                    self.assertEqual(load_saved_run(db, baseline.run_id), baseline)
                    marker = db.execute(
                        "SELECT source_generation FROM report_materializations "
                        "WHERE kind='reproduction'"
                    ).fetchone()
                    if offset < 0:
                        self.assertEqual(marker[0], 1)
                    else:
                        self.assertIsNone(marker)
                        self.assertEqual(
                            load_saved_run(db, candidate.run_id), candidate
                        )

    def test_native_publication_atomically_replaces_only_reproduction_and_is_idempotent(
        self,
    ):
        run = replace(self.run, summary=str(self.root.with_suffix(".md")))
        before = domain_dump(self.db)
        with mock.patch(
            "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
        ):
            self.assertEqual(publish_saved_run(self.root, run), 1)
        self.assertEqual(domain_dump(self.db), before)
        self.assertEqual(load_saved_run(self.db, run.run_id), run)
        self.assertEqual(lookup_saved_run_generation(self.root, run), 1)
        bytes_before = shared.result_store_path(self.root).read_bytes()
        self.assertEqual(publish_saved_run(self.root, run), 1)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), bytes_before)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM report_materializations WHERE kind='reproduction'"
            ).fetchone()[0],
            0,
        )

    def test_retry_recognizes_history_without_reverting_newer_latest_indexes(self):
        run = replace(self.run, summary=str(self.root.with_suffix(".md")))
        newer = replace(
            run, run_id="reproduce-native-newer", finished_at="2026-09-15T13:00:00Z"
        )
        self.assertEqual(publish_saved_run(self.root, run), 1)
        self.assertEqual(publish_saved_run(self.root, newer), 2)
        self.assertEqual(lookup_saved_run_generation(self.root, run), 2)
        self.assertEqual(publish_saved_run(self.root, run), 2)
        self.assertEqual(
            latest_command_run(self.db, run.command_results[0].identity), newer.run_id
        )
        with self.assertRaises(ReproductionDomainError):
            lookup_saved_run_generation(
                self.root, replace(run, finished_at="2026-09-15T14:00:00Z")
            )

    def test_native_publication_insertion_failure_rolls_back_replacement_and_generation(
        self,
    ):
        run = replace(self.run, summary=str(self.root.with_suffix(".md")))
        before = shared.result_store_path(self.root).read_bytes()
        original = write_saved_run

        def fail(db, saved):
            original(db, saved)
            raise RuntimeError("injected insertion failure")

        with (
            mock.patch(
                "log_commands.reproduction_saved_storage.write_saved_run",
                side_effect=fail,
            ),
            self.assertRaisesRegex(RuntimeError, "injected insertion failure"),
        ):
            publish_saved_run(self.root, run)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 19)

    def test_exact_native_commit_is_recognized_after_lost_acknowledgement(self):
        run = replace(self.run, summary=str(self.root.with_suffix(".md")))
        original = shared.result_transaction

        @contextmanager
        def lose_ack(root):
            with original(root) as db:
                yield db
            raise RuntimeError("injected lost acknowledgement")

        with (
            mock.patch.object(shared, "result_transaction", lose_ack),
            self.assertRaisesRegex(RuntimeError, "injected lost acknowledgement"),
        ):
            publish_saved_run(self.root, run)
        self.assertEqual(lookup_saved_run_generation(self.root, run), 1)
        self.assertEqual(publish_saved_run(self.root, run), 1)

    def test_empty_replacement_is_receipt_only_and_preserves_other_domains(self):
        before = domain_dump(self.db)
        self.assertEqual(confirm_empty_replacement(self.root, WHEN), 8)
        self.assertEqual(domain_dump(self.db), before)
        receipt = load_empty_confirmation(self.db)
        self.assertEqual(receipt.summary, str(self.root.with_suffix(".md")))
        self.assertEqual(receipt.confirmed_at, WHEN)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM reproduction_runs").fetchone()[0], 0
        )
        bytes_before = shared.result_store_path(self.root).read_bytes()
        self.assertEqual(
            confirm_empty_replacement(self.root, "2026-09-15T13:00:00Z"), 8
        )
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), bytes_before)
        run = replace(self.run, summary=str(self.root.with_suffix(".md")))
        self.assertEqual(publish_saved_run(self.root, run), 9)
        self.assertIsNone(load_empty_confirmation(self.db))

    def test_empty_replacement_transaction_failure_preserves_obsolete_history(self):
        before = shared.result_store_path(self.root).read_bytes()
        original = replace_reproduction_domain

        def fail(db):
            original(db)
            raise RuntimeError("empty cutover failure")

        with mock.patch(
            "log_commands.reproduction_saved_storage.replace_reproduction_domain",
            side_effect=fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "empty cutover failure"):
                confirm_empty_replacement(self.root, WHEN)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)

    def save(self, run=None):
        with self.db:
            write_saved_run(self.db, run or self.run)

    def test_replacement_discards_old_only_and_roundtrips_native_frozen_facts(self):
        before = domain_dump(self.db)
        research_before = {
            path.relative_to(Path(self.directory.name)).as_posix(): path.read_bytes()
            for path in Path(self.directory.name).rglob("*")
            if path.is_file() and ".cache" not in path.parts
        }
        self.replace()
        self.save()
        self.assertEqual(domain_dump(self.db), before)
        self.assertEqual(
            {
                path.relative_to(
                    Path(self.directory.name)
                ).as_posix(): path.read_bytes()
                for path in Path(self.directory.name).rglob("*")
                if path.is_file() and ".cache" not in path.parts
            },
            research_before,
        )
        changes = self.db.total_changes
        with (
            mock.patch(
                "pathlib.Path.resolve", side_effect=AssertionError("source resolve")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
        ):
            loaded = load_saved_run(self.db, self.run.run_id)
        self.assertEqual(loaded, self.run)
        self.assertEqual(self.db.total_changes, changes)
        self.assertNotIn(
            "impossible-sentinel-old-problem", "\n".join(self.db.iterdump())
        )
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            SNAPSHOTS.load_validation_snapshot(self.root), self.validation_before
        )

    def test_unchanged_domain_publication_preserves_native_reproduction_history(self):
        self.replace()
        self.save()
        SNAPSHOTS.publish_validation_snapshot(
            SNAPSHOTS.SnapshotPublicationRequest(
                self.root,
                self.evaluation.snapshot,
                {"requested": ("e001",), "evaluated": ("e001",), "dependency": ()},
            )
        )
        published_validation = SNAPSHOTS.load_validation_snapshot(self.root)
        identity = diagnostics.publish_command_diagnostic(
            self.root,
            "Discovery rejected a recorded command",
            "command.discovery.failed",
            ({"identity": "command-1", "kind": "rejected"},),
        )
        self.assertEqual(
            diagnostics.load_command_diagnostic(self.root)["diagnostic_id"], identity
        )
        self.assertEqual(load_saved_run(self.db, self.run.run_id), self.run)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 22)
        self.assertEqual(
            SNAPSHOTS.load_validation_snapshot(self.root), published_validation
        )

    def test_replacement_and_insert_failure_roll_back_the_exact_baseline(self):
        before = tuple(self.db.iterdump())
        with self.assertRaisesRegex(RuntimeError, "injected"), self.db:
            self.db.execute("BEGIN IMMEDIATE")
            replace_reproduction_domain(self.db)
            write_saved_run(self.db, self.run)
            raise RuntimeError("injected")
        self.assertEqual(tuple(self.db.iterdump()), before)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 19)

    def test_unsupported_read_is_nonmutating_and_does_not_decode_old_reproduction(self):
        before = tuple(self.db.iterdump())
        changes = self.db.total_changes
        with self.assertRaisesRegex(ReproductionDomainError, "rerun reproduction"):
            load_saved_run(self.db, self.run.run_id)
        self.assertEqual(tuple(self.db.iterdump()), before)
        self.assertEqual(self.db.total_changes, changes)

    def test_v20_native_history_requires_recheck_and_is_replaced_without_decode(self):
        self.replace()
        self.save()
        self.db.execute(
            "UPDATE reproduction_run_commands SET record_json='not-json'"
        )
        self.db.execute("PRAGMA user_version=20")
        self.db.commit()
        before = tuple(self.db.iterdump())
        changes = self.db.total_changes

        with self.assertRaisesRegex(ReproductionDomainError, "rerun reproduction"):
            load_saved_run(self.db, self.run.run_id)

        self.assertEqual(tuple(self.db.iterdump()), before)
        self.assertEqual(self.db.total_changes, changes)
        self.replace()
        self.save()
        self.assertEqual(load_saved_run(self.db, self.run.run_id), self.run)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 22)

    def test_same_saved_run_is_idempotent_and_changed_facts_conflict(self):
        self.replace()
        self.save()
        changes = self.db.total_changes
        self.save()
        self.assertEqual(self.db.total_changes, changes)
        with self.assertRaisesRegex(ReproductionDomainError, "conflicts"), self.db:
            write_saved_run(
                self.db, replace(self.run, finished_at="2026-09-15T12:01:00Z")
            )
        self.assertEqual(load_saved_run(self.db, self.run.run_id), self.run)

    def test_later_history_keeps_previous_records_and_updates_only_identity_indexes(
        self,
    ):
        self.replace()
        self.save()
        later = replace(
            self.run,
            run_id="reproduce-2026-09-15-later",
            finished_at="2026-09-15T12:01:00Z",
        )
        self.save(later)
        self.assertEqual(load_saved_run(self.db, self.run.run_id), self.run)
        self.assertEqual(load_saved_run(self.db, later.run_id), later)
        for result in later.command_results:
            self.assertEqual(latest_command_run(self.db, result.identity), later.run_id)
        for result in later.artifact_results:
            self.assertEqual(
                latest_artifact_run(self.db, result.identity), later.run_id
            )

    def test_preparation_reads_each_native_origin_once_and_retains_actual_roots(self):
        self.replace()
        self.save()
        consumer = next(
            work for work in self.run.commands if work.identity.cid == "consumer"
        )
        independent = next(
            work for work in self.run.commands if work.identity.cid == "success"
        )
        independent = replace(
            independent, identity=replace(independent.identity, cid="independent")
        )
        later = replace(
            self.run,
            run_id="reproduce-later-origin",
            finished_at="2026-09-15T12:01:00Z",
            commands=(independent,),
            command_results=(attempted(independent),),
            artifacts=(),
            artifact_results=(),
            problems=(),
        )
        self.save(later)
        equal = next(
            work
            for work in self.run.artifacts
            if work.identity.artifact == "data/equal.txt"
        )
        unequal = next(
            work
            for work in self.run.artifacts
            if work.identity.artifact == "data/unequal.txt"
        )
        changes = self.db.total_changes
        with (
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.resolve", side_effect=AssertionError("source resolve")
            ),
            mock.patch(
                "log_commands.reproduction_saved_storage.load_saved_run",
                wraps=load_saved_run,
            ) as load,
        ):
            history = load_preparation_history(
                self.db,
                (consumer.identity, consumer.identity, independent.identity),
                (equal.identity, unequal.identity),
            )
        self.assertEqual(load.call_count, 2)
        self.assertEqual(
            set(history.commands), {consumer.identity, independent.identity}
        )
        self.assertEqual(history.commands[consumer.identity][0], consumer)
        self.assertEqual(
            history.commands[independent.identity],
            (independent, attempted(independent)),
        )
        failed = next(
            result
            for result in self.run.command_results
            if result.identity.cid == "producer"
        )
        cause = next(
            problem
            for problem in self.run.problems
            if problem.problem_id == failed.problem_ids[0]
        )
        self.assertEqual(history.problems, {cause.subject: (cause,)})
        self.assertNotIn(consumer.identity, history.problems)
        comparison = next(
            result
            for result in self.run.artifact_results
            if result.identity == equal.identity
        )
        self.assertEqual(history.artifacts[equal.identity], (equal, comparison))
        self.assertEqual(comparison.outcome.value, "matched")
        difference = next(
            problem
            for problem in self.run.problems
            if problem.code == "content_changed"
        )
        self.assertEqual(history.artifact_problems, {difference.problem_id: difference})
        self.assertEqual(self.db.total_changes, changes)
        with self.assertRaises(TypeError):
            history.commands[consumer.identity] = (consumer, None)

    def test_preparation_keeps_a_local_block_without_inventing_an_attempt(self):
        self.replace()
        plan = blocked_plan()
        selected = next(
            work for work in plan.commands if work.selection is WorkSelection.RUN
        )
        comparisons = tuple(
            result for result in self.run.artifact_results if result.profile is not None
        )
        comparison_ids = {
            identity for result in comparisons for identity in result.problem_ids
        }
        run = complete_saved_run(
            plan,
            RunCompletion(
                self.run.run_id,
                WHEN,
                WHEN,
                (attempted(selected),),
                comparisons,
                tuple(
                    problem
                    for problem in self.run.problems
                    if problem.problem_id in comparison_ids
                ),
            ),
        )
        self.save(run)
        producer = next(
            work for work in run.commands if work.identity.cid == "producer"
        )
        history = load_preparation_history(self.db, (producer.identity,), ())
        self.assertEqual(history.commands[producer.identity], (producer, None))
        self.assertEqual(
            history.problems[producer.identity][0].code,
            "effective_code_unavailable",
        )

    def test_preparation_aggregate_budget_covers_multiple_individually_valid_origins(
        self,
    ):
        self.replace()
        work = next(
            work for work in self.run.commands if work.identity.cid == "success"
        )
        first = replace(
            self.run,
            run_id="reproduce-minimal-one",
            commands=(work,),
            command_results=(attempted(work),),
            artifacts=(),
            artifact_results=(),
            problems=(),
        )
        another = replace(work, identity=replace(work.identity, cid="another"))
        second = replace(
            first,
            run_id="reproduce-minimal-two",
            commands=(another,),
            command_results=(attempted(another),),
        )
        self.save(first)
        self.save(second)
        bound = max(len(first.serialized().encode()), len(second.serialized().encode()))
        with mock.patch(
            "log_commands.reproduction_saved_storage.MAX_RESULT_BYTES", bound
        ):
            self.assertEqual(load_saved_run(self.db, first.run_id), first)
            self.assertEqual(load_saved_run(self.db, second.run_id), second)
            with (
                self.assertRaisesRegex(
                    ReproductionDomainError, "preparation history.*aggregate"
                ),
                mock.patch(
                    "log_commands.reproduction_saved_storage.load_saved_run",
                    side_effect=AssertionError(
                        "origin decoded before aggregate preflight"
                    ),
                ),
            ):
                load_preparation_history(self.db, (work.identity, another.identity), ())

    def test_preparation_rejects_conflicting_same_id_diagnoses_across_origins(self):
        self.replace()
        original = next(
            work for work in self.run.commands if work.identity.cid == "success"
        )
        cause = ReproductionProblem(
            SourceRef(self.run.summary),
            "validation_blocked",
            ProblemStage.PREPARE,
            "First retained diagnosis.",
            {"finding_id": "example"},
        )
        work = replace(
            original, selection=WorkSelection.BLOCKED, problem_ids=(cause.problem_id,)
        )
        first = replace(
            self.run,
            run_id="reproduce-conflict-one",
            commands=(work,),
            command_results=(),
            artifacts=(),
            artifact_results=(),
            problems=(cause,),
        )
        another = replace(work, identity=replace(work.identity, cid="another"))
        changed = replace(cause, explanation="Revised retained diagnosis.")
        self.assertEqual(cause.problem_id, changed.problem_id)
        second = replace(
            first,
            run_id="reproduce-conflict-two",
            commands=(another,),
            problems=(changed,),
        )
        self.save(first)
        self.save(second)
        changes = self.db.total_changes
        with self.assertRaisesRegex(
            ReproductionDomainError, "conflicting native history diagnosis"
        ):
            load_preparation_history(self.db, (work.identity, another.identity), ())
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual(load_saved_run(self.db, second.run_id), second)
        self.save(replace(second, run_id="reproduce-exact-shared", problems=(cause,)))
        history = load_preparation_history(
            self.db, (work.identity, another.identity), ()
        )
        self.assertIs(
            history.problems[work.identity][0], history.problems[another.identity][0]
        )

    def test_native_record_payload_cannot_shadow_identity_columns(self):
        self.replace()
        self.save()
        self.db.execute(
            "UPDATE reproduction_run_commands SET record_json="
            "json_set(record_json,'$.identity',json('{}'))"
        )
        with self.assertRaisesRegex(ReproductionDomainError, "duplicates relational"):
            load_saved_run(self.db, self.run.run_id)

    def test_selected_producer_prunes_old_output_indexes_but_preserves_history(self):
        self.replace()
        self.save()
        omitted = next(
            work for work in self.run.artifacts if work.output == "data/equal.txt"
        )
        later = replace(
            self.run,
            run_id="reproduce-later-prune",
            artifacts=tuple(work for work in self.run.artifacts if work != omitted),
            artifact_results=tuple(
                result
                for result in self.run.artifact_results
                if result.identity != omitted.identity
            ),
        )
        self.save(later)
        self.assertIsNone(latest_artifact_run(self.db, omitted.identity))
        self.assertEqual(load_saved_run(self.db, self.run.run_id), self.run)
        self.assertEqual(load_saved_run(self.db, later.run_id), later)

    def test_nonselected_previous_failure_keeps_original_latest_origin(self):
        self.replace()
        self.save()
        producer = next(
            work for work in self.run.commands if work.identity.cid == "producer"
        )
        omitted = next(
            work for work in self.run.artifacts if work.producer == producer.identity
        )
        original_result = next(
            result
            for result in self.run.command_results
            if result.identity == producer.identity
        )
        later = replace(
            self.run,
            run_id="reproduce-later-unchanged",
            commands=tuple(
                replace(
                    work,
                    selection=WorkSelection.PREVIOUS_FAILURE,
                    problem_ids=original_result.problem_ids,
                )
                if work == producer
                else work
                for work in self.run.commands
            ),
            command_results=tuple(
                result
                for result in self.run.command_results
                if result.identity != producer.identity
            ),
            artifacts=tuple(work for work in self.run.artifacts if work != omitted),
            artifact_results=tuple(
                result
                for result in self.run.artifact_results
                if result.identity != omitted.identity
            ),
        )
        self.save(later)
        self.assertEqual(
            latest_command_run(self.db, producer.identity), self.run.run_id
        )
        self.assertEqual(
            latest_artifact_run(self.db, omitted.identity), self.run.run_id
        )
        self.assertEqual(load_saved_run(self.db, later.run_id), later)

    def test_valid_changed_header_is_caught_by_immutable_digest(self):
        self.replace()
        self.save()
        self.db.execute(
            "UPDATE reproduction_runs SET finished_at='2026-09-15T12:01:00Z'"
        )
        with self.assertRaisesRegex(ReproductionDomainError, "immutable digest"):
            load_saved_run(self.db, self.run.run_id)

    def test_accepted_history_cannot_be_discarded_by_replacement_again(self):
        self.replace()
        self.save()
        before = tuple(self.db.iterdump())
        with (
            self.assertRaisesRegex(ReproductionDomainError, "execution-baseline"),
            self.db,
        ):
            self.db.execute("BEGIN IMMEDIATE")
            replace_reproduction_domain(self.db)
        self.assertEqual(tuple(self.db.iterdump()), before)

    def test_corrupted_inventory_is_bounded_before_typed_reconstruction(self):
        self.replace()
        self.save()
        with (
            mock.patch("log_commands.reproduction_saved_storage.MAX_WORK_RECORDS", 1),
            self.assertRaisesRegex(ReproductionDomainError, "row bound"),
        ):
            load_saved_run(self.db, self.run.run_id)

    def test_cross_log_collision_fails_without_changing_saved_history(self):
        self.replace()
        self.save()
        before = tuple(self.db.iterdump())
        with self.assertRaisesRegex(ReproductionDomainError, "different log"), self.db:
            write_saved_run(
                self.db,
                replace(
                    self.run,
                    summary="other/study.md",
                    run_id="reproduce-2026-09-15-other",
                ),
            )
        self.assertEqual(tuple(self.db.iterdump()), before)

    def test_aggregate_byte_budget_is_checked_before_fetching_native_payloads(self):
        self.replace()
        self.save()
        # Each corrupted row fits the patched budget; the collection does not.
        self.db.execute(
            "UPDATE reproduction_run_commands SET record_json=?",
            ('{"padding":"' + "x" * 3000 + '"}',),
        )
        with (
            mock.patch(
                "log_commands.reproduction_saved_storage.MAX_RESULT_BYTES", 16000
            ),
            mock.patch(
                "log_commands.reproduction_saved_storage._payload",
                side_effect=AssertionError("payload fetched before preflight"),
            ),
            self.assertRaisesRegex(ReproductionDomainError, "aggregate read budget"),
        ):
            load_saved_run(self.db, self.run.run_id)

    def test_header_byte_budget_is_checked_before_fetching_settings(self):
        self.replace()
        self.save()
        self.db.execute("UPDATE reproduction_runs SET settings_json=?", ("x" * 16000,))
        with (
            mock.patch(
                "log_commands.reproduction_saved_storage.MAX_RESULT_BYTES", 8000
            ),
            mock.patch(
                "log_commands.reproduction_saved_storage._payload",
                side_effect=AssertionError("header fetched before preflight"),
            ),
            self.assertRaisesRegex(ReproductionDomainError, "aggregate read budget"),
        ):
            load_saved_run(self.db, self.run.run_id)
