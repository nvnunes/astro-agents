"""Promotion consumes fixed accepted-plan observations."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.model import ActionError
from log_commands.reproduction_execution import _fingerprint
from log_commands.reproduction_promotion import (
    _install_outputs,
    _load_bundle,
    _overlapping_paths,
    _PromotedOutput,
    _safe_run_path,
)
from reproduction_fixed_plan_test_support import accepted_plan


class ReproductionPromotionTests(unittest.TestCase):
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
                "first.txt", "file", first_staged, first_destination,
                _fingerprint(first_destination, "file"),
                _fingerprint(first_staged, "file"),
            )
            second = _PromotedOutput(
                "second.txt", "file", second_staged, second_destination,
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "reproduce-20300101t000000z-promotion"
            execution_id = "pyrun-exec/v1:" + "1" * 64
            staging = {
                "schema": "research-log-reproduction-staging/2",
                "run_id": run_id,
                "target": {"kind": "log", "entry": None},
                "executions": [
                    {
                        "bytes": 0,
                        "complete": False,
                        "diagnostics": [],
                        "entry": "e001",
                        "execution_id": execution_id,
                        "outputs": [],
                        "path": "executions/e001",
                    }
                ],
            }
            (root / "staging.json").write_text(json.dumps(staging), encoding="utf-8")
            with self.assertRaisesRegex(ActionError, "incomplete"):
                _load_bundle(root, run_id, execution_id)
            with self.assertRaisesRegex(ActionError, "expected one staged"):
                _load_bundle(root, run_id, "pyrun-exec/v1:" + "2" * 64)

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
