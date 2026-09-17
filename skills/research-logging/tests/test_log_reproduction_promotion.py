"""Promotion consumes fixed accepted-plan observations."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping
from unittest import mock

import test_reproduction_work_supervision as supervision_fixture
from log_commands.model import ActionError
from log_commands.reproduction_execution import _fingerprint
from log_commands.reproduction_promotion import (
    _begin_promotion,
    _install_outputs,
    _load_staging_bundle,
    _overlapping_paths,
    _PromotedOutput,
    _safe_run_path,
)
from reproduction_planning_test_support import _Fixture
from validation.operation_state import operation_lock


class ReproductionPromotionTests(unittest.TestCase):
    def test_promotion_reads_run_state_before_publication_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            log = _Fixture(project).log
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

    def test_incomplete_or_unbound_staging_cannot_reach_promotion(self) -> None:
        owner = supervision_fixture.NativeSupervisionTests()
        self.addCleanup(owner.doCleanups)
        fixture, workspace = owner.prepare_graph()
        from log_commands.reproduction_work_job import open_work_job
        from log_commands.reproduction_work_supervision import execute_work_plan

        self.assertEqual(
            execute_work_plan(fixture.log, workspace, owner.control()), "completed"
        )
        with open_work_job(workspace.run_root) as job:
            accepted = job.accepted
            work = next(
                item
                for item in accepted.plan.commands
                if item.identity.cid == "producer"
            )
            self.assertIsNotNone(job.load_command_result(work.identity))
            artifact = next(
                item
                for item in accepted.plan.artifacts
                if item.producer == work.identity
            )
            with job._transaction("test_remove_comparison"):
                job._db.execute(
                    "DELETE FROM run_artifact_results WHERE run_id=? AND "
                    "artifact_pk=(SELECT artifact_pk FROM accepted_work_artifacts "
                    "WHERE run_id=? AND entry=? AND artifact=?)",
                    (
                        accepted.run_id,
                        accepted.run_id,
                        artifact.identity.entry,
                        artifact.identity.artifact,
                    ),
                )
        with self.assertRaisesRegex(ActionError, "comparisons are missing"):
            _load_staging_bundle(
                workspace.run_root,
                accepted.run_id,
                work.identity.cid,
                work.identity.execution_id,
            )
        with self.assertRaisesRegex(ActionError, "expected one staged"):
            _load_staging_bundle(
                workspace.run_root,
                accepted.run_id,
                work.identity.cid,
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
