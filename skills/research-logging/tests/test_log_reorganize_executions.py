"""Transfers preserve recorded executions without running research commands."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from effective_code import analyze_effective_code
from log_commands.storage import PublicationError
from python_execution import PythonExecutionContext
from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import Fingerprint
from research_log_reservations import reserve_execution
from test_log_evidence_sync import retained_files
from test_log_reorganize import create_log, run
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    execution_id,
    load_pyrun_state,
    ordinary_execution,
)


def fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", hashlib.sha256(path.read_bytes()).hexdigest())


def code_fingerprint(script: Path, entry: Path, project: Path) -> Fingerprint:
    context = PythonExecutionContext.for_research_script(
        script, entry_root=entry, log_root=entry.parent.parent, project_root=project
    )
    result = analyze_effective_code(
        script, project_root=project, import_roots=context.import_roots
    )
    if result.fingerprint is None:
        raise AssertionError(result.unsupported)
    return Fingerprint(result.fingerprint.algorithm, result.fingerprint.digest)


def fixture(root: Path, *, stale: bool = False):
    logical, entries = create_log(root, 2)
    source, destination = entries
    (root / "input.csv").write_text("value\n7\n")
    (source / "scripts").mkdir()
    (source / "scripts/make.py").write_text(
        "from helpers import value\nprint(value())\n"
    )
    (source / "scripts/helpers.py").write_text("def value():\n    return 7\n")
    shutil.copytree(source / "scripts", destination / "scripts")
    parameters = (
        "--capture-stdout",
        "data/stdout.log",
        "--",
        "--input-data",
        "<source>",
        "--output-data",
        "data/first.txt",
        "--output-other",
        "data/second.txt",
        "--label",
        "data/first.txt",
    )
    outputs = (
        ("data/first.txt", "file"),
        ("data/second.txt", "file"),
        ("data/stdout.log", "file"),
    )
    fields = {
        "schema": "research-log-data/v6",
        "inputs": [
            {
                "name": "source",
                "kind": "file",
                "origin": True,
                "location": str(root / "input.csv"),
                "identity": {"algorithm": "sha256"},
            }
        ],
    }
    for name, (path, _) in zip(("first", "second", "stdout"), outputs, strict=True):
        (source / path).parent.mkdir(exist_ok=True)
        (source / path).write_text(name)
        fields["inputs"].append(
            {
                "name": name,
                "kind": "file",
                "origin": False,
                "location": path,
                "identity": {"algorithm": "sha256"},
            }
        )
    (source / "data.json").write_text(json.dumps(fields))
    recipe = ExecutionRecipe(
        "scripts/make.py",
        parameters,
        (("OMP_NUM_THREADS", "2"),),
        ("source",),
        outputs,
        tuple(
            sorted(
                {
                    **dict(fixture_parameter_roles(parameters, ("source",), outputs)),
                    "label": "ordinary",
                }.items()
            )
        ),
    )
    observed = ObservedExecution(
        fingerprint(source / "scripts/make.py"),
        (("source", fingerprint(root / "input.csv")),),
        code_fingerprint(source / "scripts/make.py", source, root),
        tuple((key, fingerprint(source / key)) for key, _ in outputs),
    )
    execution = ordinary_execution(
        recipe,
        observed,
        auto_reproduce=False,
        exclusive=True,
        last_run_at="2026-01-02T03:04:05Z",
    )
    execution = replace(execution, requires_reproduction=stale)
    state = PyrunFile(
        source / "pyrun.json",
        source,
        {"make": PyrunCommand({execution_id(recipe): execution})},
    )
    state.path.write_text(state.serialized())
    (destination / "data").mkdir()
    (source / "data/first.txt").rename(destination / "data/renamed.txt")
    (source / "data/second.txt").rename(destination / "data/second.txt")
    (source / "data/stdout.log").rename(destination / "data/stdout.log")
    document = destination / "e002.md"
    document.write_text(
        document.read_text()
        + (
            "\n## Moved experiment\n\n`Steps:`\n\n```bash\n"
            "./pyrun --other-parameters label --auto-reproduce=false --exclusive "
            "--env OMP_NUM_THREADS=2 --capture-stdout data/stdout.log -- "
            "scripts/make.py "
            "--input-data '<renamed-source>' --output-data '<first>' "
            "--output-other '<second>' --label data/first.txt\n```\n\n"
            "`Results:`\n\nPending.\n"
        )
    )
    arguments = (
        "reorganize",
        "transfer",
        "--path",
        str(logical),
        "--from-entry",
        "e001",
        "--to-entry",
        "e002",
        "--all",
        "--path-map",
        "data/first.txt",
        "data/renamed.txt",
        "--data-map",
        "source",
        "renamed-source",
    )
    return logical, source, destination, execution, arguments


def write_pending_execution(
    source: Path,
    destination: Path,
    execution: PyrunExecution,
    missing: set[str],
) -> PyrunExecution:
    for key in missing:
        moved = "data/renamed.txt" if key == "data/first.txt" else key
        if (destination / moved).is_dir():
            shutil.copytree(destination / moved, source / key)
        else:
            shutil.copy2(destination / moved, source / key)
    if "data/stdout.log" in missing:
        data = json.loads((source / "data.json").read_text())
        data["inputs"] = [item for item in data["inputs"] if item["name"] != "stdout"]
        (source / "data.json").write_text(json.dumps(data))
        (source / "retention.json").write_text(
            json.dumps(
                {
                    "schema": "research-log-retention/v1",
                    "records": [{"id": "capture", "paths": ["data/stdout.log"]}],
                }
            )
        )
    pending = replace(
        execution,
        requires_reproduction=True,
        observed=replace(
            execution.observed,
            outputs=tuple(
                (key, value)
                for key, value in execution.observed.outputs
                if key not in missing
            ),
        ),
    )
    state = PyrunFile(
        source / "pyrun.json",
        source,
        {"make": PyrunCommand({execution_id(pending.recipe): pending})},
    )
    state.path.write_text(state.serialized())
    return pending


class ExecutionTransferTests(unittest.TestCase):
    def test_pending_outputs_keep_missing_fingerprints_after_copy_comparison(self):
        for selection in ("one", "all", "directory"):
            with (
                self.subTest(selection=selection),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(root)
                missing = {"data/first.txt"}
                if selection == "all":
                    missing = {key for key, _ in original.recipe.outputs}
                elif selection == "directory":
                    missing = {"data/second.txt"}
                    output = destination / "data/second.txt"
                    output.unlink()
                    output.mkdir()
                    (output / "result.csv").write_text("value\n7\n")
                    data = json.loads((source / "data.json").read_text())
                    resource = next(
                        item for item in data["inputs"] if item["name"] == "second"
                    )
                    resource.update(
                        kind="directory", identity={"algorithm": "directory-sha256-v1"}
                    )
                    (source / "data.json").write_text(json.dumps(data))
                    original = replace(
                        original,
                        recipe=replace(
                            original.recipe,
                            outputs=tuple(
                                (key, "directory" if key in missing else kind)
                                for key, kind in original.recipe.outputs
                            ),
                        ),
                    )
                pending = write_pending_execution(
                    source, destination, original, missing
                )
                before = retained_files(logical)
                preview = run(root, *arguments, "--dry-run")
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertEqual(retained_files(logical), before)
                transferred = run(root, *arguments)
                self.assertEqual(transferred.returncode, 0, transferred.stderr)
                state = load_pyrun_state(
                    destination / "pyrun.json",
                    entry_root=destination,
                    project_root=root,
                )
                moved = next(iter(state.commands["make"].executions.values()))
                self.assertTrue(moved.requires_reproduction)
                self.assertEqual(moved.last_run_at, pending.last_run_at)
                self.assertEqual(moved.auto_reproduce, pending.auto_reproduce)
                self.assertEqual(moved.exclusive, pending.exclusive)
                self.assertEqual(moved.observed.script, pending.observed.script)
                self.assertEqual(
                    dict(moved.observed.outputs),
                    {
                        "data/renamed.txt" if key == "data/first.txt" else key: value
                        for key, value in pending.observed.outputs
                    },
                )

    def test_pending_output_failures_leave_both_entries_unchanged(self):
        for failure in (
            "mismatch",
            "missing-source",
            "missing-destination",
            "no-owner",
            "capture",
        ):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(root)
                key = "data/stdout.log" if failure == "capture" else "data/first.txt"
                write_pending_execution(source, destination, original, {key})
                target = destination / (
                    "data/renamed.txt" if key == "data/first.txt" else key
                )
                if failure in {"mismatch", "capture"}:
                    target.write_text("different contents\n")
                elif failure == "missing-source":
                    (source / key).unlink()
                elif failure == "missing-destination":
                    target.unlink()
                else:
                    (source / "pyrun.json").unlink()
                before = retained_files(logical)
                for extra in (("--dry-run",), ()):
                    failed = run(root, *arguments, *extra)
                    self.assertEqual(failed.returncode, 2, failed.stderr)
                    expected = (
                        "data.fingerprint.mismatch"
                        if failure in {"mismatch", "capture"}
                        else "data.fingerprint.unobserved"
                        if failure == "no-owner"
                        else "data.target.missing"
                    )
                    self.assertIn(expected, failed.stderr)
                    self.assertEqual(retained_files(logical), before)

    def test_batch_of_27_nonautomatic_commands_is_preserved_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, source, destination, original, arguments = fixture(root)
            fields = json.loads((source / "data.json").read_text())
            fields["inputs"] = fields["inputs"][:1]
            commands = {}
            for index in range(27):
                key = f"data/batch-{index}.txt"
                (destination / key).write_text(str(index))
                fields["inputs"].append(
                    {
                        "name": f"batch-{index}",
                        "location": key,
                        "kind": "file",
                        "origin": False,
                        "identity": {"algorithm": "sha256"},
                    }
                )
                parameters = ("--output-data", key, "--index", str(index))
                recipe = replace(
                    original.recipe,
                    parameters=parameters,
                    inputs=(),
                    outputs=((key, "file"),),
                    parameter_roles=fixture_parameter_roles(
                        parameters, (), ((key, "file"),)
                    ),
                )
                execution = replace(
                    original,
                    recipe=recipe,
                    observed=replace(
                        original.observed,
                        inputs=(),
                        outputs=((key, fingerprint(destination / key)),),
                    ),
                )
                commands[f"make-{index}"] = PyrunCommand(
                    {execution_id(recipe): execution}
                )
            (source / "data.json").write_text(json.dumps(fields))
            state = PyrunFile(source / "pyrun.json", source, commands)
            state.path.write_text(state.serialized())
            arguments = (
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e002",
                "--all",
                "--data-map",
                "source",
                "renamed-source",
            )
            transferred = run(root, *arguments)
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            moved = load_pyrun_state(
                destination / "pyrun.json", entry_root=destination, project_root=root
            )
            self.assertEqual(len(moved.commands), 27)
            for _, _, execution in moved.execution_items():
                self.assertFalse(execution.auto_reproduce)
                self.assertFalse(execution.requires_reproduction)
                self.assertEqual(execution.last_run_at, original.last_run_at)

    def test_unselected_source_execution_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, source, destination, original, arguments = fixture(root)
            (source / "data/left.txt").write_text("left")
            recipe = replace(
                original.recipe,
                parameters=("--output-data", "data/left.txt"),
                inputs=(),
                outputs=(("data/left.txt", "file"),),
                parameter_roles=(("output-data", "output"),),
            )
            left = replace(
                original,
                recipe=recipe,
                observed=replace(
                    original.observed,
                    inputs=(),
                    outputs=(("data/left.txt", fingerprint(source / "data/left.txt")),),
                ),
            )
            state = load_pyrun_state(
                source / "pyrun.json", entry_root=source, project_root=root
            )
            updated = PyrunFile(
                state.path,
                source,
                {**state.commands, "left": PyrunCommand({execution_id(recipe): left})},
            )
            updated.path.write_text(updated.serialized())
            fields = json.loads((source / "data.json").read_text())
            fields["inputs"].append(
                {
                    "name": "left",
                    "kind": "file",
                    "origin": False,
                    "location": "data/left.txt",
                    "identity": {"algorithm": "sha256"},
                }
            )
            (source / "data.json").write_text(json.dumps(fields))
            arguments = tuple(value for value in arguments if value != "--all") + (
                "--data",
                "source,first,second,stdout",
            )
            transferred = run(root, *arguments)
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            remaining = load_pyrun_state(
                source / "pyrun.json", entry_root=source, project_root=root
            )
            self.assertEqual(remaining.commands, {"left": updated.commands["left"]})

    def test_script_rename_and_changed_helpers_do_not_manufacture_currentness(self):
        for change in ("rename", "helper"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(root)
                if change == "rename":
                    (destination / "scripts/make.py").rename(
                        destination / "scripts/renamed.py"
                    )
                    arguments += ("--path-map", "scripts/make.py", "scripts/renamed.py")
                else:
                    (destination / "scripts/helpers.py").write_text(
                        "def value():\n    return 99\n"
                    )
                transferred = run(root, *arguments)
                self.assertEqual(transferred.returncode, 0, transferred.stderr)
                state = load_pyrun_state(
                    destination / "pyrun.json",
                    entry_root=destination,
                    project_root=root,
                )
                moved = next(iter(state.commands["make"].executions.values()))
                if change == "rename":
                    self.assertEqual(moved.recipe.script, "scripts/renamed.py")
                    self.assertEqual(
                        moved.observed.effective_code,
                        code_fingerprint(
                            destination / "scripts/renamed.py", destination, root
                        ),
                    )
                else:
                    self.assertEqual(
                        moved.observed.effective_code, original.observed.effective_code
                    )
                    self.assertNotEqual(
                        moved.observed.effective_code,
                        code_fingerprint(
                            destination / "scripts/make.py", destination, root
                        ),
                    )

    def test_active_reservations_and_publication_failure_preserve_both_entries(self):
        for problem in ("reservation", "publication"):
            with (
                self.subTest(problem=problem),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, _, arguments = fixture(root)
                before = retained_files(logical)
                if problem == "reservation":
                    with reserve_execution(
                        root, source, "busy", (), (destination / "data/renamed.txt",)
                    ):
                        failed = run(root, *arguments)
                    self.assertIn("artifact.reservation.conflict", failed.stderr)
                else:
                    with mock.patch(
                        "log_commands.reorganize_transfer.atomic_write_texts",
                        side_effect=PublicationError(
                            OSError("injected publication failure"), ()
                        ),
                    ) as publish:
                        failed = run(root, *arguments)
                    updates = publish.call_args.args[0]
                    self.assertIn(source / "pyrun.json", updates)
                    self.assertIn(destination / "pyrun.json", updates)
                    self.assertIn("reorganize.transfer.failed", failed.stderr)
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_transfer_preserves_nonautomatic_history_and_currentness(self):
        for stale in (False, True):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(
                    root, stale=stale
                )
                before = retained_files(logical)
                preview = run(root, *arguments, "--dry-run")
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertEqual(retained_files(logical), before)
                transferred = run(root, *arguments)
                self.assertEqual(transferred.returncode, 0, transferred.stderr)
                self.assertFalse((source / "pyrun.json").exists())
                state = load_pyrun_state(
                    destination / "pyrun.json",
                    entry_root=destination,
                    project_root=root,
                )
                moved = next(iter(state.commands["make"].executions.values()))
                self.assertEqual(moved.last_run_at, original.last_run_at)
                self.assertEqual(moved.requires_reproduction, stale)
                self.assertFalse(moved.auto_reproduce)
                self.assertTrue(moved.exclusive)
                self.assertEqual(moved.recipe.environment, original.recipe.environment)
                self.assertEqual(moved.observed.script, original.observed.script)
                self.assertEqual(
                    dict(moved.observed.inputs),
                    {"renamed-source": dict(original.observed.inputs)["source"]},
                )
                self.assertEqual(
                    dict(moved.observed.outputs)["data/renamed.txt"],
                    dict(original.observed.outputs)["data/first.txt"],
                )
                self.assertEqual(
                    moved.observed.effective_code,
                    code_fingerprint(
                        destination / "scripts/make.py", destination, root
                    ),
                )
                self.assertEqual(
                    moved.recipe.parameters[-2:], ("--label", "data/first.txt")
                )
                before_sync = (destination / "pyrun.json").read_bytes()
                synced = run(
                    root,
                    "command",
                    "sync",
                    "--path",
                    str(logical),
                    "--entry",
                    "e002",
                    "--cid",
                    "make",
                )
                self.assertEqual(synced.returncode, 0, synced.stderr)
                self.assertEqual((destination / "pyrun.json").read_bytes(), before_sync)

    def test_current_script_copies_preserve_historical_fingerprints_and_staleness(self):
        for change in ("comment", "semantic"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(root)
                suffix = (
                    "# Current explanation\n" if change == "comment" else "print(99)\n"
                )
                for entry in (source, destination):
                    script = entry / "scripts/make.py"
                    script.write_text(script.read_text() + suffix)
                self.assertNotEqual(
                    fingerprint(source / "scripts/make.py"), original.observed.script
                )
                before = retained_files(logical)
                preview = run(root, *arguments, "--dry-run")
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertEqual(retained_files(logical), before)
                transferred = run(root, *arguments)
                self.assertEqual(transferred.returncode, 0, transferred.stderr)
                state = load_pyrun_state(
                    destination / "pyrun.json",
                    entry_root=destination,
                    project_root=root,
                )
                moved = next(iter(state.commands["make"].executions.values()))
                self.assertEqual(moved.observed.script, original.observed.script)
                self.assertEqual(moved.last_run_at, original.last_run_at)
                self.assertEqual(
                    moved.requires_reproduction, original.requires_reproduction
                )
                self.assertEqual(moved.auto_reproduce, original.auto_reproduce)
                self.assertEqual(moved.exclusive, original.exclusive)
                self.assertCountEqual(
                    [value for _, value in moved.observed.outputs],
                    [value for _, value in original.observed.outputs],
                )
                current = code_fingerprint(
                    destination / "scripts/make.py", destination, root
                )
                if change == "semantic":
                    self.assertEqual(
                        moved.observed.effective_code, original.observed.effective_code
                    )
                    self.assertNotEqual(moved.observed.effective_code, current)
                else:
                    self.assertEqual(moved.observed.effective_code, current)

    def test_changed_material_or_execution_collision_preserves_all_registries(self):
        for conflict in ("script", "missing-script", "output", "execution"):
            with (
                self.subTest(conflict=conflict),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, source, destination, original, arguments = fixture(root)
                if conflict == "script":
                    (destination / "scripts/make.py").write_text("print('changed')\n")
                elif conflict == "missing-script":
                    (source / "scripts/make.py").unlink()
                elif conflict == "output":
                    (destination / "data/renamed.txt").write_text("changed")
                else:
                    parameters = list(original.recipe.parameters)
                    parameters[4] = "<renamed-source>"
                    parameters[6] = "data/renamed.txt"
                    recipe = replace(
                        original.recipe,
                        parameters=tuple(parameters),
                        inputs=("renamed-source",),
                        outputs=(
                            ("data/renamed.txt", "file"),
                            ("data/second.txt", "file"),
                            ("data/stdout.log", "file"),
                        ),
                    )
                    prior = replace(
                        original,
                        recipe=recipe,
                        last_run_at="2025-01-01T00:00:00Z",
                        observed=replace(
                            original.observed,
                            inputs=(
                                (
                                    "renamed-source",
                                    dict(original.observed.inputs)["source"],
                                ),
                            ),
                            outputs=tuple(
                                (
                                    "data/renamed.txt"
                                    if key == "data/first.txt"
                                    else key,
                                    value,
                                )
                                for key, value in original.observed.outputs
                            ),
                        ),
                    )
                    state = PyrunFile(
                        destination / "pyrun.json",
                        destination,
                        {"make": PyrunCommand({execution_id(recipe): prior})},
                    )
                    state.path.write_text(state.serialized())
                before = retained_files(logical)
                for extra in (("--dry-run",), ()):
                    failed = run(root, *arguments, *extra)
                    self.assertEqual(failed.returncode, 2, failed.stderr)
                    expected = {
                        "script": "reorganize.transfer.script_changed",
                        "missing-script": "provenance.observation.unavailable",
                        "output": "data.fingerprint.mismatch",
                        "execution": "reorganize.transfer.execution_conflict",
                    }[conflict]
                    self.assertIn(expected, failed.stderr)
                    self.assertEqual(retained_files(logical), before)


if __name__ == "__main__":
    unittest.main()
