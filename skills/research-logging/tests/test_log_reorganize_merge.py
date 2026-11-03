"""Entry merges preserve shared declarations and explicit evidence ownership."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import Fingerprint, load_data_file, observe_fingerprint
from test_log_evidence_sync import retained_files
from test_log_reorganize import (
    create_log,
    declare_fixture_input,
    result,
    run,
    write_execution_state,
)
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_RUNNER,
    ExecutionRecipe,
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    execution_id,
)


def record_output(entry: Path) -> str:
    """Seed a successful directory observation without executing a recipe."""

    resource = load_data_file(entry / "data.json", entry_root=entry).by_name[
        "reference-analysis"
    ]
    parameters = ("--output-dir", "data/reference-analysis")
    outputs = (("data/reference-analysis", "directory"),)
    recipe = ExecutionRecipe(
        "scripts/make.py",
        parameters,
        (),
        (),
        outputs,
        parameter_roles=fixture_parameter_roles(parameters, None, outputs),
    )
    observation = observe_fingerprint(resource).fingerprint
    execution = PyrunExecution(
        False,
        True,
        "2030-01-01T00:00:00Z",
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        ObservedExecution(
            Fingerprint("sha256", digest="a" * 64),
            (),
            Fingerprint("python-effective-code-sha256-v1", digest="b" * 64),
            (("data/reference-analysis", observation),),
        ),
    )
    identity = execution_id(recipe)
    state = PyrunFile(
        entry / "pyrun.json",
        entry,
        {"reference-producer": PyrunCommand({identity: execution})},
    )
    state.path.write_text(state.serialized())
    return identity


def merge_fixture(root: Path) -> tuple[Path, Path, Path, tuple[str, ...], str]:
    """Prepare the mapped origin/forwarded-producer shape of the reported merge."""

    logical, entries = create_log(root, 3)
    source, destination, _ = entries
    for name in ("prior.pkl", "cases.csv"):
        (root / name).write_text(name)
    (source / "configs").mkdir()
    (source / "configs/statistics.json").write_text('{"precision":3}')
    output = source / "data/reference-analysis"
    output.mkdir(parents=True)
    (output / "summary.json").write_text('{"value":7}')
    (source / "scripts").mkdir()
    (source / "scripts/make.py").write_text("raise RuntimeError('must not run')\n")
    for name, path in (
        ("science-prior", str(root / "prior.pkl")),
        ("reference-csv", str(root / "cases.csv")),
        ("statistics", "configs/statistics.json"),
    ):
        declare_fixture_input(logical, "e001", name, path)
    registry = source / "data.json"
    fields = json.loads(registry.read_text())
    fields["inputs"].append(
        {
            "name": "reference-analysis",
            "kind": "directory",
            "location": "data/reference-analysis",
            "origin": False,
            "identity": {"algorithm": "directory-sha256-v1"},
        }
    )
    registry.write_text(json.dumps(fields))
    identity = record_output(source)
    for name, path in (
        ("science-prior", str(root / "prior.pkl")),
        ("comparison-csv", str(root / "cases.csv")),
        ("precision-statistics", str(source / "configs/statistics.json")),
    ):
        declare_fixture_input(logical, "e002", name, path)
    registry = destination / "data.json"
    fields = json.loads(registry.read_text())
    fields["inputs"].append({"name": "reference-analysis", "from_entry": "e001"})
    registry.write_text(json.dumps(fields))
    marker = (
        "`7`<!-- eid:reference source=reference-analysis/summary.json select=/value -->"
    )
    for entry_id, entry, eid in (
        ("e001", source, "reference"),
        ("e002", destination, "consumer"),
    ):
        document = entry / f"{entry_id}.md"
        document.write_text(
            document.read_text()
            + "\n## Trial\n\n`Steps:`\n\nRecorded input.\n\n`Results:`\n\n"
            + marker.replace("eid:reference", f"eid:{eid}")
            + "\n"
        )
        synced = run(
            root,
            "evidence",
            "sync",
            "--path",
            str(logical),
            "--entry",
            entry_id,
            "--id",
            eid,
        )
        if synced.returncode:
            raise AssertionError(synced.stderr)
    document = source / "e001.md"
    document.write_text(document.read_text().replace(marker, ""))
    moved_document = destination / "e002b.md"
    moved_document.write_text(
        "# Reference PSFs\n\n## Trial\n\n`Steps:`\n\nRecorded input.\n\n"
        "`Results:`\n\n" + marker + "\n"
    )
    summary = logical.with_suffix(".md")
    summary.write_text(
        summary.read_text()
        + f"\n- [Reference]({moved_document.relative_to(logical.parent).as_posix()})\n"
    )
    (destination / "data").mkdir()
    output.rename(destination / "data/reference-analysis")
    (destination / "configs").mkdir()
    (source / "configs/statistics.json").rename(destination / "configs/statistics.json")
    (destination / "scripts").mkdir()
    (destination / "scripts/make.py").write_text("raise RuntimeError('must not run')\n")
    (destination / "data/unrelated.txt").write_text("preserved")
    write_execution_state(destination, ("data/unrelated.txt",))
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
        "--document-map",
        document.relative_to(logical).as_posix(),
        moved_document.relative_to(logical).as_posix(),
        "--path-map",
        "data/reference-analysis",
        "data/reference-analysis",
        "--path-map",
        "configs/statistics.json",
        "configs/statistics.json",
        "--data-map",
        "reference-csv",
        "comparison-csv",
        "--data-map",
        "statistics",
        "precision-statistics",
    )
    return logical, source, destination, arguments, identity


class EntryMergeTests(unittest.TestCase):
    def test_equivalent_mapped_declarations_localize_the_incoming_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, source, destination, arguments, identity = merge_fixture(root)
            before = retained_files(logical)
            consumer = json.loads((destination / "evidence.json").read_text())[
                "records"
            ][0]
            preview = run(root, *arguments, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(retained_files(logical), before)
            transferred = run(root, *arguments)
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            inputs = load_data_file(
                destination / "data.json", entry_root=destination
            ).by_name
            self.assertEqual(
                set(inputs),
                {
                    "science-prior",
                    "comparison-csv",
                    "precision-statistics",
                    "reference-analysis",
                },
            )
            self.assertIsNone(inputs["reference-analysis"].reference_entry)
            self.assertFalse(inputs["reference-analysis"].origin)
            self.assertEqual(
                inputs["reference-analysis"].location, "data/reference-analysis"
            )
            self.assertEqual(
                inputs["precision-statistics"].location, "configs/statistics.json"
            )
            evidence = json.loads((destination / "evidence.json").read_text())[
                "records"
            ]
            self.assertIn(consumer, evidence)
            moved = next(record for record in evidence if record["id"] == "reference")
            self.assertTrue(moved["document"].endswith("/e002b.md"))
            for name in ("data.json", "evidence.json", "pyrun.json"):
                self.assertFalse((source / name).exists())
            self.assertEqual(
                (destination / "pyrun.json").read_bytes(),
                before[destination / "pyrun.json"],
            )
            self.assertEqual(
                result(transferred)["records"][0]["execution_id"], identity
            )
            self.assertEqual(result(transferred)["records"][0]["entry"], "e002")

    def test_incompatible_collisions_and_remaining_forwarders_publish_nothing(self):
        for conflict in (
            "target",
            "kind",
            "origin",
            "identity",
            "forwarder",
        ):
            with (
                self.subTest(conflict=conflict),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                logical, _, destination, arguments, _ = merge_fixture(root)
                registry = destination / "data.json"
                fields = json.loads(registry.read_text())
                if conflict == "forwarder":
                    third = next((logical / "entries").glob("*-e003-*"))
                    (third / "data.json").write_text(
                        json.dumps(
                            {
                                "schema": "research-log-data/v6",
                                "inputs": [
                                    {"name": "reference-analysis", "from_entry": "e001"}
                                ],
                            }
                        )
                    )
                else:
                    item = next(
                        item
                        for item in fields["inputs"]
                        if item["name"] == "science-prior"
                    )
                    if conflict == "target":
                        (root / "other.pkl").write_text("prior.pkl")
                        item["location"] = str(root / "other.pkl")
                    elif conflict == "origin":
                        item["origin"] = False
                    elif conflict == "kind":
                        item["kind"] = "directory"
                        item["identity"] = {"algorithm": "directory-sha256-v1"}
                    else:
                        item = next(
                            item
                            for item in fields["inputs"]
                            if item["name"] == "reference-analysis"
                        )
                        item.clear()
                        item.update(
                            {
                                "name": "reference-analysis",
                                "kind": "directory",
                                "origin": False,
                                "location": str(
                                    destination / "data/reference-analysis"
                                ),
                                "identity": {
                                    "algorithm": "identity-files-sha256-v1",
                                    "files": ["summary.json"],
                                },
                            }
                        )
                    registry.write_text(json.dumps(fields))
                before = retained_files(logical)
                for extra in (("--dry-run",), ()):
                    failed = run(root, *arguments, *extra)
                    self.assertEqual(failed.returncode, 2, failed.stderr)
                    self.assertIn(
                        "reorganize.transfer.dependency_present"
                        if conflict == "forwarder"
                        else "reorganize.transfer.data_conflict",
                        failed.stderr,
                    )
                    self.assertEqual(retained_files(logical), before)

    def test_generated_file_comparison_policy_conflict_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entries = create_log(root, 2)
            source, destination = entries
            for entry in entries:
                (entry / "data").mkdir()
                declaration = {
                    "name": "result",
                    "kind": "file",
                    "origin": False,
                    "location": "data/result.json",
                    "identity": {"algorithm": "sha256"},
                }
                if entry == destination:
                    declaration["reproduction_comparison"] = {
                        "contract": "research-log-evidence-scoped-comparison/1",
                        "profile": "evidence",
                    }
                (entry / "data.json").write_text(
                    json.dumps(
                        {
                            "schema": "research-log-data/v6",
                            "inputs": [declaration],
                        }
                    )
                )
            (source / "data/result.json").write_text('{"value":7}')
            (source / "scripts").mkdir()
            (source / "scripts/make.py").write_text(
                "raise RuntimeError('must not run')\n"
            )
            write_execution_state(source, ("data/result.json",))
            (source / "data/result.json").rename(destination / "data/result.json")
            before = retained_files(logical)
            arguments = (
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e002",
                "--data",
                "result",
            )
            for extra in (("--dry-run",), ()):
                failed = run(root, *arguments, *extra)
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertIn("reorganize.transfer.data_conflict", failed.stderr)
                self.assertEqual(retained_files(logical), before)

    def test_same_entry_split_rename_persists_selected_associations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            logical, entries = create_log(root, 1)
            entry = entries[0]
            (entry / "data").mkdir()
            (entry / "data/value.json").write_text('{"a":1,"b":7}')
            declare_fixture_input(logical, "e001", "values", "data/value.json")
            (entry / "e001.md").unlink()
            summary = logical.with_suffix(".md")
            links = []
            for suffix, value in (("a", 1), ("b", 7)):
                document = entry / f"e001{suffix}.md"
                document.write_text(
                    f"# Split {suffix}\n\n## Trial\n\n`Steps:`\n\nRecorded input.\n\n"
                    "`Results:`\n\n"
                    f"`{value}`<!-- eid:{suffix} source=values select=/{suffix} -->\n"
                )
                links.append(
                    f"  - [{suffix}]({document.relative_to(logical.parent).as_posix()})"
                )
            summary.write_text(
                "# Study\n\n## Entries\n\n- `2026-09-01` Trial:\n"
                + "\n".join(links)
                + "\n"
            )
            for eid in ("a", "b"):
                synced = run(
                    root,
                    "evidence",
                    "sync",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                    "--id",
                    eid,
                )
                self.assertEqual(synced.returncode, 0, synced.stderr)
            (entry / "e001b.md").rename(entry / "e001c.md")
            summary.write_text(summary.read_text().replace("e001b.md", "e001c.md"))
            before = retained_files(logical)
            prior = json.loads((entry / "evidence.json").read_text())["records"]
            arguments = (
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e001",
                "--evidence",
                "b",
                "--document-map",
                (entry / "e001b.md").relative_to(logical).as_posix(),
                (entry / "e001c.md").relative_to(logical).as_posix(),
            )
            preview = run(root, *arguments, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(retained_files(logical), before)
            invalid = run(
                root,
                *arguments[:-1],
                (entry / "e001a.md").relative_to(logical).as_posix(),
            )
            self.assertEqual(invalid.returncode, 2, invalid.stderr)
            self.assertIn("reorganize.transfer.markdown_incomplete", invalid.stderr)
            self.assertEqual(retained_files(logical), before)
            transferred = run(root, *arguments)
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            after = retained_files(logical)
            self.assertEqual(
                [path for path in before if before[path] != after[path]],
                [entry / "evidence.json"],
            )
            records = json.loads((entry / "evidence.json").read_text())["records"]
            self.assertEqual(records[0], prior[0])
            self.assertTrue(records[1]["document"].endswith("/e001c.md"))


if __name__ == "__main__":
    unittest.main()
