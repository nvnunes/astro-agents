"""Origin identity edits observe selected material and matching producers only."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import replace_fixture_recipe
from research_log_data import load_data_file, observe_fingerprint
from test_log_evidence_sync import retained_files
from test_log_reorganize import create_log, run, write_execution_state
from validation.pyrun_state import (
    PyrunCommand,
    PyrunFile,
    execution_id,
    load_pyrun_state,
)


def identity_fixture(root: Path):
    logical, entries = create_log(root, 2)
    unrelated, selected = entries
    for entry, name in ((unrelated, "unrelated"), (selected, "raw")):
        (entry / "data" / name).mkdir(parents=True)
        (entry / "scripts").mkdir()
        (entry / "scripts/make.py").write_text("raise RuntimeError('must not run')\n")
        (entry / "data.json").write_text(
            json.dumps(
                {
                    "schema": "research-log-data/v6",
                    "inputs": [
                        {
                            "name": name,
                            "location": f"data/{name}",
                            "kind": "directory",
                            "origin": True,
                            "identity": {"algorithm": "directory-sha256-v1"},
                        }
                    ],
                }
            )
        )
        entry_id = "e001" if entry == unrelated else "e002"
        (entry / f"{entry_id}.md").write_text(
            "# Trial\n\n## Experiment\n\n`Steps:`\n\n```bash\n"
            f"./pyrun --cid analyze -- scripts/make.py --input-dir '<{name}>' "
            "--output data/result.json\n```\n\n`Results:`\n\nPending.\n"
        )
    (unrelated / "data/unrelated/expensive.h5").write_bytes(b"unrelated bytes")
    (selected / "data/raw/selected.json").write_text('{"value":7}')
    (selected / "data/raw/ignored.h5").write_bytes(b"unselected bytes")
    return logical, unrelated, selected


def record_producer(
    entry: Path, *, member: bool, pending: bool, excluded: bool = False
):
    output = (
        "data/raw/ignored.h5"
        if excluded
        else "data/raw/selected.json"
        if member
        else "data/raw"
    )
    # Seed a complete observation; the fixture never invokes a research script.
    write_execution_state(entry, (output if member else "data/raw/selected.json",))
    state = load_pyrun_state(
        entry / "pyrun.json", entry_root=entry, project_root=entry.parents[3]
    )
    execution = next(iter(state.commands["transfer-fixture"].executions.values()))
    if not member:
        resource = load_data_file(entry / "data.json", entry_root=entry).by_name["raw"]
        execution = replace(
            execution,
            recipe=replace_fixture_recipe(
                execution.recipe,
                parameters=("--output-dir", output),
                outputs=((output, "directory"),),
            ),
            observed=replace(
                execution.observed,
                outputs=((output, observe_fingerprint(resource).fingerprint),),
            ),
        )
    execution = replace(execution, requires_reproduction=pending)
    updated = PyrunFile(
        state.path,
        entry,
        {"producer": PyrunCommand({execution_id(execution.recipe): execution})},
    )
    updated.path.write_text(updated.serialized())
    document = entry / "e002.md"
    option = "--output" if member else "--output-dir"
    document.write_text(
        document.read_text()
        + (
            "\n## Producer\n\n`Steps:`\n\n```bash\n"
            f"./pyrun --cid producer -- scripts/make.py {option} {output}\n"
            "```\n\n`Results:`\n\nPending.\n"
        )
    )


class OriginIdentityTests(unittest.TestCase):
    def test_preview_and_apply_read_only_selected_identity_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, unrelated, selected = identity_fixture(root)
            before = retained_files(logical)
            opened = []
            original_open = Path.open

            def checked_open(path, *args, **kwargs):
                if path.suffix == ".h5":
                    raise AssertionError(f"unrelated content read: {path}")
                opened.append(path.resolve())
                return original_open(path, *args, **kwargs)

            arguments = (
                "data",
                "update",
                "raw",
                "--identity",
                "file:selected.json",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "--target",
                "data/raw",
                "--kind",
                "directory",
                "--boundary",
                "origin",
            )
            for extra in (("--dry-run",), ()):
                opened.clear()
                with mock.patch.object(Path, "open", checked_open):
                    updated = run(root, *arguments, *extra)
                self.assertEqual(updated.returncode, 0, updated.stderr)
                self.assertIn((selected / "data/raw/selected.json").resolve(), opened)
                if extra:
                    self.assertEqual(retained_files(logical), before)
            after = retained_files(logical)
            self.assertEqual(
                [path for path in before if before[path] != after[path]],
                [selected / "data.json"],
            )
            self.assertEqual(
                load_data_file(selected / "data.json", entry_root=selected)
                .by_name["raw"]
                .identity.files,
                ("selected.json",),
            )
            self.assertEqual(
                (unrelated / "data/unrelated/expensive.h5").read_bytes(),
                b"unrelated bytes",
            )

    def test_missing_selected_identity_fails_without_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, _, _ = identity_fixture(root)
            before = retained_files(logical)
            for extra in (("--dry-run",), ()):
                failed = run(
                    root,
                    "data",
                    "update",
                    "raw",
                    "--identity",
                    "file:missing.json",
                    "--path",
                    str(logical),
                    "--entry",
                    "e002",
                    *extra,
                )
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_matching_producers_are_verified_without_observing_other_commands(self):
        for kind in ("selected-member", "excluded-member", "directory"):
            for pending in (True, False):
                with (
                    self.subTest(kind=kind, pending=pending),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory).resolve()
                    logical, _, selected = identity_fixture(root)
                    record_producer(
                        selected,
                        member=kind != "directory",
                        pending=pending,
                        excluded=kind == "excluded-member",
                    )
                    before = retained_files(logical)
                    original_open = Path.open

                    def checked_open(path, *args, **kwargs):
                        if path.suffix == ".h5":
                            raise AssertionError(f"unrelated content read: {path}")
                        return original_open(path, *args, **kwargs)

                    for extra in (("--dry-run",), ()):
                        with mock.patch.object(Path, "open", checked_open):
                            updated = run(
                                root,
                                "data",
                                "update",
                                "raw",
                                "--identity",
                                "file:selected.json",
                                "--path",
                                str(logical),
                                "--entry",
                                "e002",
                                "--acknowledge-shared",
                                *extra,
                            )
                        self.assertEqual(
                            updated.returncode, 0 if pending else 2, updated.stderr
                        )
                        if not pending:
                            self.assertIn("directory.origin.conflict", updated.stderr)
                        if extra or not pending:
                            self.assertEqual(retained_files(logical), before)

    def test_unreadable_identity_or_unverifiable_producer_publishes_nothing(self):
        for problem in ("identity", "producer"):
            with (
                self.subTest(problem=problem),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, _, selected = identity_fixture(root)
                if problem == "producer":
                    record_producer(selected, member=True, pending=False)
                    (selected / "scripts/make.py").unlink()
                before = retained_files(logical)
                original_open = Path.open

                def checked_open(path, *args, **kwargs):
                    if problem == "identity" and path.name == "selected.json":
                        raise PermissionError("selected identity is unreadable")
                    if path.suffix == ".h5":
                        raise AssertionError(f"unrelated content read: {path}")
                    return original_open(path, *args, **kwargs)

                for extra in (("--dry-run",), ()):
                    with mock.patch.object(Path, "open", checked_open):
                        failed = run(
                            root,
                            "data",
                            "update",
                            "raw",
                            "--identity",
                            "file:selected.json",
                            "--path",
                            str(logical),
                            "--entry",
                            "e002",
                            "--acknowledge-shared",
                            *extra,
                        )
                    self.assertEqual(failed.returncode, 2, failed.stderr)
                    self.assertEqual(retained_files(logical), before)


if __name__ == "__main__":
    unittest.main()
