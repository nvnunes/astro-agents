"""Promotion consumes fixed accepted-plan observations."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_execution import _fingerprint
from log_commands.reproduction_job_storage import (
    RequirementEffect,
    record_execution_comparison,
    record_requirement_effect,
)
from log_commands.reproduction_jobs import (
    _CurrentSupervisorContext,
    _publish_current_stage,
)
from log_commands.reproduction_promotion import (
    _begin_promotion,
    _install_outputs,
    _load_current_bundle,
    _overlapping_paths,
    _PromotedOutput,
    _safe_run_path,
    promote_execution,
)
from log_commands.reproduction_queries import show_reproduction_command
from reproduction_fixed_plan_test_support import accepted_plan, publication_run
from research_log_result_store import ResultStoreError, result_snapshot
from test_reproduction_job_storage import _comparison, _job_fixture, _start_and_finish
from validation.operation_state import operation_lock


class ReproductionPromotionTests(unittest.TestCase):
    def test_promotion_reads_run_state_before_publication_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".git").mkdir()
            log, _run_root = publication_run(project)
            held_operation_locks: list[str] = []

            @contextmanager
            def tracked_operation_lock(
                root: Path,
                name: str,
                *,
                mode: Literal["shared", "exclusive"] = "exclusive",
                owner_factory: Callable[[], Mapping[str, object]] | None = None,
            ) -> Iterator[None]:
                with operation_lock(
                    root,
                    name,
                    mode=mode,
                    owner_factory=owner_factory,
                ):
                    held_operation_locks.append(name)
                    try:
                        yield
                    finally:
                        self.assertEqual(held_operation_locks.pop(), name)

            def require_overlap_before_publication(
                _log: object, _outputs: object
            ) -> None:
                self.assertIn("reproduction-promotion-index.lock", held_operation_locks)
                self.assertNotIn("reproduction-publication.lock", held_operation_locks)

            with (
                mock.patch(
                    "log_commands.reproduction_promotion.operation_lock",
                    side_effect=tracked_operation_lock,
                ),
                mock.patch(
                    "log_commands.reproduction_promotion._require_no_active_input_overlap",
                    side_effect=require_overlap_before_publication,
                ),
            ):
                marker = _begin_promotion(log, "run-id", "execution-id", ())
            self.assertTrue(marker.is_file())
            marker.unlink()

    def test_current_staging_promotes_before_and_after_publication(self) -> None:
        for published in (False, True):
            with (
                self.subTest(published=published),
                _job_fixture(executions=1) as (
                    _project,
                    fixture,
                    entry,
                    plan,
                    accepted,
                    root,
                ),
            ):
                _start_and_finish(root, plan, 0)
                comparison = _comparison(plan, 0)
                record_execution_comparison(root, comparison)
                for name in ("workspace", "runtime", "diagnostics", "executions"):
                    (root / name).mkdir()
                staged_value = comparison.artifacts[0].staged_path
                self.assertIsNotNone(staged_value)
                if staged_value is None:
                    self.fail("comparison omitted its staged path")
                staged = root / staged_value
                staged.parent.mkdir(parents=True)
                artifact = comparison.artifacts[0].artifact
                destination = entry.root / artifact
                staged.write_bytes(destination.read_bytes())
                if published:
                    record_requirement_effect(
                        root,
                        RequirementEffect(
                            comparison.entry,
                            comparison.cid,
                            comparison.execution_id,
                            "2030-01-01T00:02:00Z",
                        ),
                    )
                    _publish_current_stage(
                        _CurrentSupervisorContext(fixture.log, root, plan, "fresh")
                    )
                    with result_snapshot(fixture.log.root) as db:
                        identities = [
                            tuple(row)
                            for row in db.execute(
                                "SELECT entry, cid, execution_id "
                                "FROM reproduction_run_commands"
                            )
                        ]
                    self.assertEqual(
                        identities,
                        [(comparison.entry, comparison.cid, comparison.execution_id)],
                    )
                    shown = show_reproduction_command(
                        fixture.log,
                        entry=comparison.entry,
                        cid=comparison.cid,
                        execution_id=comparison.execution_id,
                        run_id=accepted.run_id,
                    )
                    diagnostics = shown["diagnostics"]
                    self.assertIsInstance(diagnostics, dict)
                    if not isinstance(diagnostics, dict):
                        self.fail("command diagnostics projection is invalid")
                    self.assertEqual(diagnostics["availability"], "available")
                    checkpoint = diagnostics["checkpoint"]
                    self.assertIsInstance(checkpoint, dict)
                    if not isinstance(checkpoint, dict):
                        self.fail("command checkpoint projection is invalid")
                    self.assertEqual(checkpoint["path"], "state.sqlite")
                if not published:
                    with self.assertRaises(ResultStoreError) as raised:
                        promote_execution(
                            fixture.log,
                            run_id=accepted.run_id,
                            cid=comparison.cid,
                            execution_id=comparison.execution_id,
                        )
                    self.assertEqual(raised.exception.code, "results.store.missing")
                    self.assertTrue(staged.is_file())
                    self.assertTrue(destination.is_file())
                    continue
                result = promote_execution(
                    fixture.log,
                    run_id=accepted.run_id,
                    cid=comparison.cid,
                    execution_id=comparison.execution_id,
                )
                self.assertEqual(result.outputs, (artifact,))
                self.assertTrue(staged.is_file())
                self.assertTrue(destination.is_file())

    def test_install_rechecks_missing_and_changed_destination_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = project / "destination.txt"
            staged = project / "staged.txt"
            destination.write_text("old\n", encoding="utf-8")
            staged.write_text("new\n", encoding="utf-8")
            baseline = _fingerprint(destination, "file")
            promoted = _PromotedOutput(
                "destination.txt",
                "file",
                staged,
                destination,
                baseline,
                _fingerprint(staged, "file"),
            )
            destination.write_text("raced\n", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "destination.txt"):
                _install_outputs(project, (promoted,))
            self.assertEqual(destination.read_text(encoding="utf-8"), "raced\n")
            destination.unlink()
            with self.assertRaisesRegex(ActionError, "destination.txt"):
                _install_outputs(project, (promoted,))

    def test_install_rolls_back_earlier_output_when_later_baseline_races(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first_destination = project / "first.txt"
            second_destination = project / "second.txt"
            first_staged = project / "first-staged.txt"
            second_staged = project / "second-staged.txt"
            for path, text in (
                (first_destination, "first old\n"),
                (second_destination, "second old\n"),
                (first_staged, "first new\n"),
                (second_staged, "second new\n"),
            ):
                path.write_text(text, encoding="utf-8")
            first = _PromotedOutput(
                "first.txt",
                "file",
                first_staged,
                first_destination,
                _fingerprint(first_destination, "file"),
                _fingerprint(first_staged, "file"),
            )
            second = _PromotedOutput(
                "second.txt",
                "file",
                second_staged,
                second_destination,
                _fingerprint(second_destination, "file"),
                _fingerprint(second_staged, "file"),
            )
            second_destination.write_text("second raced\n", encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "second.txt"):
                _install_outputs(project, (first, second))
            self.assertEqual(
                first_destination.read_text(encoding="utf-8"), "first old\n"
            )
            self.assertEqual(
                second_destination.read_text(encoding="utf-8"), "second raced\n"
            )

    def test_failed_restore_keeps_displaced_original_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first_destination = project / "first.txt"
            second_destination = project / "second.txt"
            first_staged = project / "first-staged.txt"
            second_staged = project / "second-staged.txt"
            for path, text in (
                (first_destination, "first old\n"),
                (second_destination, "second old\n"),
                (first_staged, "first new\n"),
                (second_staged, "second new\n"),
            ):
                path.write_text(text, encoding="utf-8")
            first = _PromotedOutput(
                "first.txt",
                "file",
                first_staged,
                first_destination,
                _fingerprint(first_destination, "file"),
                _fingerprint(first_staged, "file"),
            )
            second = _PromotedOutput(
                "second.txt",
                "file",
                second_staged,
                second_destination,
                _fingerprint(second_destination, "file"),
                _fingerprint(second_staged, "file"),
            )
            second_destination.write_text("second raced\n", encoding="utf-8")
            real_replace = os.replace

            def fail_restore(source: object, destination: object) -> None:
                if Path(source).name == "displaced-0":
                    raise OSError("forced restore failure")
                real_replace(source, destination)

            with mock.patch(
                "log_commands.reproduction_promotion.os.replace",
                side_effect=fail_restore,
            ):
                with self.assertRaises(ActionError) as raised:
                    _install_outputs(project, (first, second))
            self.assertEqual(
                raised.exception.code, "reproduction.promotion.rollback_failed"
            )
            backups = tuple((project / "tmp").glob("promotion-*/displaced-0"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "first old\n")

    def test_current_replacement_and_restore_failure_keeps_its_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            destination = project / "destination.txt"
            staged = project / "staged.txt"
            destination.write_text("original\n", encoding="utf-8")
            staged.write_text("replacement\n", encoding="utf-8")
            promoted = _PromotedOutput(
                "destination.txt",
                "file",
                staged,
                destination,
                _fingerprint(destination, "file"),
                _fingerprint(staged, "file"),
            )
            real_replace = os.replace

            def fail_current_install_and_restore(
                source: object, target: object
            ) -> None:
                name = Path(source).name
                if name == "displaced-0" or name.startswith(
                    ".destination.txt.promotion-"
                ):
                    raise OSError("forced current-output failure")
                real_replace(source, target)

            with mock.patch(
                "log_commands.reproduction_promotion.os.replace",
                side_effect=fail_current_install_and_restore,
            ):
                with self.assertRaises(ActionError) as raised:
                    _install_outputs(project, (promoted,))
            self.assertEqual(
                raised.exception.code, "reproduction.promotion.rollback_failed"
            )
            backups = tuple((project / "tmp").glob("promotion-*/displaced-0"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "original\n")

    def test_plan_keeps_frozen_comparison_context_not_source_snapshot(self) -> None:
        fields = accepted_plan().as_dict()
        self.assertIn("comparison_context", fields)
        self.assertNotIn("source_snapshot", fields)

    def test_incomplete_or_unbound_staging_cannot_reach_promotion(self) -> None:
        with _job_fixture(executions=1) as (
            _project,
            _fixture,
            _entry,
            plan,
            accepted,
            root,
        ):
            execution_id = str(plan.executions[0]["execution_id"])
            cid = str(plan.executions[0]["cid"])
            _start_and_finish(root, plan, 0)
            record_execution_comparison(
                root, replace(_comparison(plan, 0), complete=False)
            )
            with self.assertRaisesRegex(ActionError, "incomplete"):
                _load_current_bundle(root, accepted.run_id, cid, execution_id)
            with self.assertRaisesRegex(ActionError, "expected one staged"):
                _load_current_bundle(
                    root,
                    accepted.run_id,
                    cid,
                    "pyrun-exec/v2:" + "2" * 64,
                )

    def test_staged_paths_cannot_escape_the_accepted_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                _safe_run_path(root, "outputs/result.txt"),
                root / "outputs" / "result.txt",
            )
            for value in ("../result.txt", "/tmp/result.txt", "outputs/../../x"):
                with (
                    self.subTest(value=value),
                    self.assertRaisesRegex(ActionError, "staged path is invalid"),
                ):
                    _safe_run_path(root, value)

    def test_promotion_overlap_detects_file_and_directory_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "data" / "output.txt"
            self.assertEqual(
                _overlapping_paths({output}, {root / "data" / "input.txt"}), set()
            )
            self.assertEqual(_overlapping_paths({output}, {root / "data"}), {output})
            self.assertEqual(
                _overlapping_paths({root / "data"}, {output}), {root / "data"}
            )
