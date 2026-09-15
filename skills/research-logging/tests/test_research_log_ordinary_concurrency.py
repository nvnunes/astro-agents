"""Bounded ordinary-operation concurrency without executing research recipes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from log_commands import command_sync, evidence_sync
from research_log_cli_test_support import run_log_process, run_pyrun_process
from research_log_reservations import (
    ArtifactReservationError,
    artifact_transaction,
    release_abandoned,
    require_artifact_access,
    reserve_execution,
)
from test_log_command_sync import fixture, sync
from test_log_evidence_sync import evidence, retained_files, set_results
from test_log_reorganize import create_log, declare_fixture_input
from test_pyrun import (
    install_entry_runner,
    install_project_python,
    make_entry,
    make_repo,
)


def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"controlled worker did not publish {path}")
        time.sleep(0.02)


def concurrent_sync(logical: Path, family: str, *arguments: str):
    return run_log_process(
        logical.parent,
        family,
        "sync",
        "--path",
        str(logical),
        "--entry",
        "e001",
        *arguments,
    )


class OrdinarySyncConcurrencyTests(unittest.TestCase):
    def test_redundant_command_add_preserves_concurrent_comparison_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entry, _ = fixture(
                root, "./pyrun scripts/build.py --output '<product>'"
            )
            prepare = command_sync._candidate_data

            def during(*args, **kwargs):
                result = prepare(*args, **kwargs)
                added = concurrent_sync(
                    logical,
                    "command",
                    "--cid",
                    "build",
                    "--add-generated",
                    "product=data/product.json",
                )
                self.assertEqual(added.returncode, 0, added.stderr)
                policy = run_log_process(
                    root,
                    "data",
                    "update",
                    "product",
                    "--reproduction-comparison",
                    "evidence",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                )
                self.assertEqual(policy.returncode, 0, policy.stderr)
                return result

            with mock.patch.object(command_sync, "_candidate_data", side_effect=during):
                result = sync(logical, "--add-generated", "product=data/product.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            item = json.loads((entry / "data.json").read_text())["inputs"][0]
            self.assertEqual(item["reproduction_comparison"]["profile"], "evidence")

    def test_evidence_operations_refuse_an_active_output_source_without_publication(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entry, document = fixture(
                root, "./pyrun scripts/build.py --output-dir '<product>'"
            )
            result = sync(logical, "--add-generated-directory", "product=data/product")
            self.assertEqual(result.returncode, 0, result.stderr)
            (entry / "data/product").mkdir()
            (entry / "data/product/summary.json").write_text('{"value": 3}')
            set_results(
                document,
                "``<!-- eid:value source=product/summary.json "
                "select=/value render=integer -->",
            )
            with reserve_execution(root, entry, "build", (), (entry / "data/product",)):
                before = retained_files(logical)
                for action, extra in (
                    ("compare", ()),
                    ("sync", ()),
                    ("sync", ("--dry-run",)),
                ):
                    result = evidence(logical, action, "--id", "value", *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("artifact.reservation.conflict", result.stderr)
                    self.assertEqual(retained_files(logical), before)

    def test_evidence_extraction_allows_command_sync_and_preserves_fresh_text_and_data(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value": 3}')
            (entry / "data/command.txt").write_text("command input")
            set_results(
                document,
                "``<!-- eid:value source=values select=/value render=integer -->",
            )
            evaluate = evidence_sync._evaluate_edit

            def during(*args, **kwargs):
                result = evaluate(*args, **kwargs)
                text = document.read_text().replace(
                    "scripts/build.py", "scripts/build.py --input '<command-input>'"
                )
                document.write_text(
                    text.replace("## Execution", "Concurrent prose.\n\n## Execution")
                )
                other = concurrent_sync(
                    logical,
                    "command",
                    "--cid",
                    "build",
                    "--add-origin",
                    "command-input=data/command.txt",
                )
                self.assertEqual(other.returncode, 0, other.stderr)
                return result

            with mock.patch.object(evidence_sync, "_evaluate_edit", side_effect=during):
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.json",
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            text = document.read_text()
            self.assertIn("Concurrent prose.", text)
            self.assertIn("--input '<command-input>'", text)
            self.assertIn("`3`<!-- eid:value", text)
            names = {
                item["name"]
                for item in json.loads((entry / "data.json").read_text())["inputs"]
            }
            self.assertEqual(names, {"values", "command-input"})

    def test_command_preparation_preserves_concurrent_evidence_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py --input '<command-input>'"
            )
            (entry / "data/value.json").write_text('{"value": 3}')
            (entry / "data/command.txt").write_text("command input")
            set_results(
                document,
                "``<!-- eid:value source=values select=/value render=integer -->",
            )
            prepare = command_sync._candidate_data

            def during(*args, **kwargs):
                result = prepare(*args, **kwargs)
                other = concurrent_sync(
                    logical,
                    "evidence",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.json",
                )
                self.assertEqual(other.returncode, 0, other.stderr)
                return result

            with mock.patch.object(command_sync, "_candidate_data", side_effect=during):
                result = sync(logical, "--add-origin", "command-input=data/command.txt")
            self.assertEqual(result.returncode, 0, result.stderr)
            names = {
                item["name"]
                for item in json.loads((entry / "data.json").read_text())["inputs"]
            }
            self.assertEqual(names, {"values", "command-input"})
            self.assertIn("`3`<!-- eid:value", document.read_text())

    def test_relevant_evidence_edit_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.json").write_text('{"value": 3}')
            set_results(
                document,
                "``<!-- eid:value source=values select=/value render=integer -->",
            )
            evaluate = evidence_sync._evaluate_edit
            saved = {}

            def during(*args, **kwargs):
                result = evaluate(*args, **kwargs)
                document.write_text(
                    document.read_text().replace("render=integer", "render=fixed:1")
                )
                saved.update(retained_files(logical))
                return result

            with mock.patch.object(evidence_sync, "_evaluate_edit", side_effect=during):
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.json",
                )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("authoring.state.changed", result.stderr)
            self.assertEqual(retained_files(logical), saved)

    def test_relevant_command_edit_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, _, document = fixture(
                Path(directory), "./pyrun scripts/build.py --count 1"
            )
            prepare = command_sync._candidate_data
            saved = {}

            def during(*args, **kwargs):
                result = prepare(*args, **kwargs)
                document.write_text(
                    document.read_text().replace("--count 1", "--count 2")
                )
                saved.update(retained_files(logical))
                return result

            with mock.patch.object(command_sync, "_candidate_data", side_effect=during):
                result = sync(logical)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("authoring.state.changed", result.stderr)
            self.assertEqual(retained_files(logical), saved)


class OrdinaryExecutionConcurrencyTests(unittest.TestCase):
    def test_comparison_policy_can_change_without_relocating_a_reserved_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entry, _ = fixture(
                root, "./pyrun scripts/build.py --output '<product>'"
            )
            result = sync(logical, "--add-generated", "product=data/product.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            with reserve_execution(
                root, entry, "build", (), (entry / "data/product.json",)
            ):
                before = json.loads((entry / "data.json").read_text())["inputs"][0]
                result = run_log_process(
                    root,
                    "data",
                    "update",
                    "product",
                    "--reproduction-comparison",
                    "evidence",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                after = json.loads((entry / "data.json").read_text())["inputs"][0]
                self.assertIn("reproduction_comparison", after)
                after.pop("reproduction_comparison")
                self.assertEqual(after, before)

    def test_live_cleanup_and_data_relocation_are_refused_but_unrelated_edits_work(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entry, _ = fixture(
                root, "./pyrun scripts/build.py --output-dir '<product>'"
            )
            result = sync(logical, "--add-generated-directory", "product=data/product")
            self.assertEqual(result.returncode, 0, result.stderr)
            with reserve_execution(root, entry, "build", (), (entry / "data/product",)):
                before = retained_files(logical)
                for arguments in (
                    ("command", "release", "--cid", "build"),
                    (
                        "data",
                        "update",
                        "product",
                        "--target",
                        "data/moved",
                        "--acknowledge-shared",
                    ),
                ):
                    result = run_log_process(
                        root, *arguments, "--path", str(logical), "--entry", "e001"
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("artifact.reservation.conflict", result.stderr)
                    self.assertEqual(retained_files(logical), before)
                result = sync(logical)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_worker_releases_its_reservation_without_recording_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            install_project_python(root)
            install_entry_runner(entry)
            (entry / "scripts/fail.py").write_text(
                "raise RuntimeError('expected fixture failure')\n"
            )
            result = run_pyrun_process(
                entry, "scripts/fail.py", "--output-dir", "data/failure"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((entry / "pyrun.json").exists())
            self.assertFalse(list(root.rglob("ordinary-execution-*.json")))


class ReservedReorganizationTests(unittest.TestCase):
    def test_entry_update_reorder_and_log_relocation_refuse_active_reservations(self):
        for action in ("update-entry", "reorder", "relocate-log"):
            with (
                self.subTest(action=action),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, entries = create_log(root, 2)
                summary = logical.with_suffix(".md")
                text = summary.read_text()
                if action == "update-entry":
                    summary.write_text(text.replace("trial-1", "changed"))
                    arguments = ("--entry", "e001", "--slug", "changed")
                elif action == "relocate-log":
                    summary.write_text(text.replace("study/", "moved/"))
                    arguments = ("--to", str(logical.parent / "moved"))
                else:
                    lines = [
                        line for line in text.splitlines() if line.startswith("- `")
                    ]
                    swapped = [
                        lines[1].replace("e002", "e001"),
                        lines[0].replace("e001", "e002"),
                    ]
                    start, end = (
                        text.index(lines[0]),
                        text.index(lines[-1]) + len(lines[-1]),
                    )
                    summary.write_text(text[:start] + "\n".join(swapped) + text[end:])
                    arguments = ("--entries", "e002,e001")
                with reserve_execution(root, entries[0], "active", (), ()):
                    before = retained_files(logical)
                    result = run_log_process(
                        root, "reorganize", action, "--path", str(logical), *arguments
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("artifact.reservation.conflict", result.stderr)
                    self.assertEqual(retained_files(logical), before)
                    self.assertTrue(entries[0].exists())

    def test_data_transfer_refuses_an_in_use_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entries = create_log(root, 2)
            source, destination = entries
            for entry in entries:
                (entry / "data").mkdir()
                (entry / "data/values.txt").write_text("values")
            declare_fixture_input(logical, "e001", "values", "data/values.txt")
            with reserve_execution(
                root, source, "active", (source / "data/values.txt",), ()
            ):
                before = retained_files(logical)
                result = run_log_process(
                    root,
                    "reorganize",
                    "transfer",
                    "--path",
                    str(logical),
                    "--from-entry",
                    "e001",
                    "--to-entry",
                    "e002",
                    "--data",
                    "values",
                    "--path-map",
                    "data/values.txt",
                    "data/values.txt",
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("artifact.reservation.conflict", result.stderr)
                self.assertEqual(retained_files(logical), before)
                self.assertFalse((destination / "data.json").exists())


class ReservationRecordTests(unittest.TestCase):
    def test_malformed_and_oversize_records_fail_closed(self):
        for defect in ("schema", "relative-entry", "negative-owner", "oversize"):
            with (
                self.subTest(defect=defect),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                operations = root / ".cache/research-log-operations"
                operations.mkdir(parents=True)
                record = {
                    "schema": "research-log-artifact-reservation/1",
                    "identity": "a" * 32,
                    "entry": str(root / "entry"),
                    "cid": "build",
                    "reads": [],
                    "writes": [str(root / "out")],
                    "parent_pid": os.getpid(),
                    "worker_pid": None,
                }
                if defect == "schema":
                    record["schema"] = "wrong"
                elif defect == "relative-entry":
                    record["entry"] = "entry"
                elif defect == "negative-owner":
                    record["parent_pid"] = -1
                path = operations / f"ordinary-execution-{'a' * 32}.json"
                path.write_text(
                    "x" * (64 * 1024 + 1)
                    if defect == "oversize"
                    else json.dumps(record)
                )
                with self.assertRaises(ArtifactReservationError):
                    require_artifact_access(root, writes=(root / "unrelated",))
                self.assertTrue(path.exists())

    def test_reservation_count_bound_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            operations = root / ".cache/research-log-operations"
            operations.mkdir(parents=True)
            for index in range(1001):
                (operations / f"ordinary-execution-{index:032x}.json").write_text("{}")
            with self.assertRaisesRegex(ArtifactReservationError, "too many"):
                require_artifact_access(root)


class OrdinaryExecutionInterruptionTests(unittest.TestCase):
    def test_killed_launcher_keeps_live_worker_reserved_until_explicit_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            install_project_python(root)
            install_entry_runner(entry)
            ready, release = entry / "data/ready", root / "release"
            (entry / "scripts/orphan.py").write_text(
                "import pathlib, sys, time\n"
                "pathlib.Path(sys.argv[1]).write_text('ready')\n"
                "deadline = time.monotonic() + 15\n"
                "while not pathlib.Path(sys.argv[2]).exists():\n"
                "    if time.monotonic() > deadline: sys.exit(1)\n"
                "    time.sleep(0.02)\n"
            )
            process = subprocess.Popen(
                [
                    "/bin/sh",
                    str(entry / "pyrun"),
                    "--cid",
                    "orphan",
                    "--other-outputs",
                    "@1",
                    "--",
                    "scripts/orphan.py",
                    "data/ready",
                    str(release),
                ],
                cwd=entry,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                wait_for(ready)
                paths = list(root.rglob("ordinary-execution-*.json"))
                self.assertEqual(len(paths), 1)
                worker_pid = json.loads(paths[0].read_text())["worker_pid"]
                self.assertIsInstance(worker_pid, int)
                process.kill()
                process.wait(timeout=10)
                with self.assertRaises(ArtifactReservationError):
                    release_abandoned(root, entry, "orphan", dry_run=False)
                with self.assertRaises(ArtifactReservationError):
                    with artifact_transaction(root, writes=(ready,)):
                        self.fail("orphaned writer became unprotected")
                release.write_text("go")
                process.communicate(timeout=20)
                deadline = time.monotonic() + 10
                while True:
                    try:
                        os.killpg(worker_pid, 0)
                    except ProcessLookupError:
                        break
                    if time.monotonic() > deadline:
                        self.fail("controlled orphan worker did not finish")
                    time.sleep(0.02)
                self.assertTrue(paths[0].exists())
                before = paths[0].read_bytes()
                self.assertEqual(
                    release_abandoned(root, entry, "orphan", dry_run=True), 1
                )
                self.assertEqual(paths[0].read_bytes(), before)
                self.assertEqual(
                    release_abandoned(root, entry, "orphan", dry_run=False), 1
                )
                self.assertFalse(paths[0].exists())
            finally:
                release.write_text("go")
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=20)

    def test_reader_sharing_and_directory_overlap_are_path_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shared = root / "input"
            with reserve_execution(
                root, root / "entry", "one", (shared,), (root / "out",)
            ):
                with reserve_execution(
                    root, root / "entry", "two", (shared,), (root / "other",)
                ):
                    pass
                for reads, writes in (
                    ((root / "out/member",), ()),
                    ((), (shared,)),
                    ((), (root / "out/member",)),
                ):
                    with self.assertRaises(ArtifactReservationError):
                        with artifact_transaction(root, reads=reads, writes=writes):
                            self.fail("overlap was accepted")
            self.assertFalse(list(root.rglob("ordinary-execution-*.json")))

    def test_paused_execution_allows_disjoint_work_and_blocks_writers(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            install_project_python(root)
            install_entry_runner(entry)
            (entry / "scripts/pause.py").write_text(
                "import argparse, pathlib, time\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--output-dir'); p.add_argument('--release')\n"
                "p.add_argument('--input-data')\n"
                "a = p.parse_args(); out = pathlib.Path(a.output_dir)\n"
                "pathlib.Path(a.input_data).read_bytes()\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'ready').write_text('ready')\n"
                "deadline = time.monotonic() + 15\n"
                "while not pathlib.Path(a.release).exists():\n"
                "    if time.monotonic() > deadline:\n"
                "        raise RuntimeError('fixture timeout')\n"
                "    time.sleep(0.02)\n"
                "(out/'result').write_text('done')\n"
            )
            (entry / "scripts/quick.py").write_text(
                "import argparse, pathlib\n"
                "p = argparse.ArgumentParser(); p.add_argument('--output-dir')\n"
                "p.add_argument('--input-data')\n"
                "a = p.parse_args(); out = pathlib.Path(a.output_dir)\n"
                "if a.input_data: pathlib.Path(a.input_data).read_bytes()\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "(out/'result').write_text('quick')\n"
            )
            release = root / "release"
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(
                    run_pyrun_process,
                    entry,
                    "--cid",
                    "pause",
                    "--",
                    "scripts/pause.py",
                    "--output-dir",
                    "data/product",
                    "--release",
                    str(release),
                    "--input-data",
                    "<input_csv>",
                )
                try:
                    wait_for(entry / "data/product/ready")
                    other = run_pyrun_process(
                        entry,
                        "--cid",
                        "quick",
                        "--",
                        "scripts/quick.py",
                        "--output-dir",
                        "data/other",
                        "--input-data",
                        "<input_csv>",
                    )
                    self.assertEqual(other.returncode, 0, other.stderr)
                    second_entry = entry.parent / "2026-05-01-e002-other"
                    (second_entry / "scripts").mkdir(parents=True)
                    (second_entry / "data").mkdir()
                    (second_entry / "scripts/quick.py").write_text(
                        (entry / "scripts/quick.py").read_text()
                    )
                    install_entry_runner(second_entry)
                    other_entry = run_pyrun_process(
                        second_entry, "scripts/quick.py", "--output-dir", "data/other"
                    )
                    self.assertEqual(other_entry.returncode, 0, other_entry.stderr)
                    self.assertTrue((second_entry / "pyrun.json").exists())
                    for arguments in (
                        ("--", "scripts/quick.py", "--output-dir", "data/product"),
                        (
                            "--capture-stdout",
                            "data/product/ready",
                            "--",
                            "scripts/print_args.py",
                        ),
                    ):
                        conflict = run_pyrun_process(
                            entry, "--cid", "conflict", *arguments
                        )
                        self.assertNotEqual(conflict.returncode, 0)
                        self.assertIn("artifact.reservation.conflict", conflict.stderr)
                        self.assertEqual(
                            (entry / "data/product/ready").read_text(), "ready"
                        )
                finally:
                    release.write_text("go")
                result = first.result(timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = json.loads((entry / "pyrun.json").read_text())["commands"]
            self.assertEqual(set(commands), {"pause", "quick"})
            self.assertFalse(list(root.rglob("ordinary-execution-*.json")))
