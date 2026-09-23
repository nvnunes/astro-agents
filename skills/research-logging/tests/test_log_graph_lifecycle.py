"""Public Markdown-first graph and retention lifecycle round trips."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands import storage
from research_log_cli_test_support import run_log, run_pyrun_process
from test_log_command_sync import fixture, sync
from test_log_data import source_repository
from test_log_evidence_sync import (
    evidence,
    related_fixture,
    retained_files,
    set_results,
)


def action(logical: Path, family: str, verb: str, *extra: str, entry: str = "e001"):
    return run_log(
        logical.parent, family, verb, "--path", str(logical), "--entry", entry, *extra
    )


def checked(result):
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class GraphLifecycleTests(unittest.TestCase):
    def test_artifact_evidence_rename_preserves_accepted_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/image.png").write_bytes(b"accepted image")
            set_results(
                document, "![Image](data/image.png)<!-- eid:image source=image -->"
            )
            checked(
                evidence(
                    logical,
                    "sync",
                    "--id",
                    "image",
                    "--add-origin",
                    "image=data/image.png",
                )
            )
            before = json.loads((entry / "evidence.json").read_text())["records"][0]
            document.write_text(
                document.read_text().replace("eid:image ", "eid:figure ")
            )
            checked(action(logical, "evidence", "sync", "--rename", "image=figure"))
            after = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(after, {**before, "id": "figure"})
            self.assertEqual((entry / "data/image.png").read_bytes(), b"accepted image")
            self.assertIn("![Image](data/image.png)", document.read_text())

    def test_cross_entry_alias_blocks_physical_ownership_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py --output '<generated>'"
            )
            target = entry / "data/output.csv"
            target.write_text("value\n7\n")
            checked(sync(logical, "--add-generated", "generated=data/output.csv"))
            referenced = logical / "entries/2030-01-02-e002-reference"
            referenced.mkdir()
            ref_document = referenced / "e002.md"
            ref_document.write_text(
                "# Reference\n\n## Execution\n\n`Steps:`\n\nRecorded input.\n\n"
                "`Results:`\n\n``<!-- eid:alias source=local-alias select=/value -->\n"
            )
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text() + "- `2030-01-02` [Reference](study/entries/"
                "2030-01-02-e002-reference/e002.md)\n"
            )
            checked(
                action(
                    logical,
                    "evidence",
                    "sync",
                    "--id",
                    "alias",
                    "--add-origin",
                    f"local-alias={target.resolve()}",
                    entry="e002",
                )
            )
            document.write_text(
                document.read_text().replace(
                    "./pyrun scripts/build.py --output '<generated>'",
                    "# Removed producer",
                )
            )
            before = retained_files(logical)
            for verb, args, code in (
                (
                    "command",
                    ("delete", "--cid", "build"),
                    "command.delete.outputs_in_use",
                ),
                (
                    "retention",
                    ("add", "--id", "kept", "--target", "data/output.csv"),
                    "retention.target.connected",
                ),
            ):
                with self.subTest(family=verb):
                    rejected = action(logical, verb, *args)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(code, rejected.stderr)
                    self.assertIn("e002", rejected.stderr)
                    self.assertIn("alias", rejected.stderr)
                    self.assertEqual(retained_files(logical), before)
            ref_document.write_text(
                ref_document.read_text().replace(
                    "`7`<!-- eid:alias source=local-alias select=/value -->", "Removed."
                )
            )
            checked(
                action(logical, "evidence", "sync", "--delete", "alias", entry="e002")
            )
            checked(action(logical, "command", "delete", "--cid", "build"))
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/output.csv",
                )
            )
            self.assertEqual(target.read_text(), "value\n7\n")

    def test_retention_transfers_require_explicit_removal_before_either_sync(self):
        for family in ("command", "evidence"):
            with (
                self.subTest(family=family),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, document = fixture(
                    Path(directory), "./pyrun scripts/build.py"
                )
                (entry / "data/value.csv").write_text("value\n7\n")
                checked(
                    action(
                        logical,
                        "retention",
                        "add",
                        "--id",
                        "kept",
                        "--target",
                        "data/value.csv",
                    )
                )
                if family == "command":
                    document.write_text(
                        document.read_text().replace(
                            "./pyrun scripts/build.py",
                            "./pyrun scripts/build.py --input '<values>'",
                        )
                    )
                else:
                    set_results(
                        document, "``<!-- eid:value source=values select=/value -->"
                    )
                before = retained_files(logical)

                def operation():
                    if family == "command":
                        return sync(logical, "--add-origin", "values=data/value.csv")
                    return evidence(
                        logical,
                        "sync",
                        "--id",
                        "value",
                        "--add-origin",
                        "values=data/value.csv",
                    )

                rejected = operation()
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("retention.ownership.conflict", rejected.stderr)
                self.assertIn("kept", rejected.stderr)
                self.assertEqual(retained_files(logical), before)
                checked(action(logical, "retention", "delete", "--id", "kept"))
                checked(operation())
                self.assertTrue((entry / "data/value.csv").exists())

    def test_semantic_evidence_list_preserves_numeric_declaration_values(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/values.csv").write_text("threshold,value\n0.676,8\n")
            set_results(
                document,
                "``<!-- eid:value source=values select=/value "
                "where=/threshold:eq:decimal:0.676 parse=decimal "
                "scale=0.25 render=fixed:2 -->",
            )
            checked(
                evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/values.csv",
                )
            )
            listed = checked(action(logical, "evidence", "list"))["records"][0]
            self.assertEqual(
                listed["sources"][0]["locator"]["where"][0]["value"], 0.676
            )
            self.assertEqual(listed["transformation"]["values"][0]["scale"], 0.25)
            self.assertIn("`2.00`", document.read_text())

    def test_kind_and_boundary_changes_have_a_workable_markdown_first_order(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py --input '<values>'"
            )
            (entry / "data/values.csv").write_text("value\n7\n")
            checked(sync(logical, "--add-origin", "values=data/values.csv"))
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            checked(evidence(logical, "sync", "--id", "value"))
            bundle = entry / "data/bundle"
            bundle.mkdir()
            (bundle / "selected.csv").write_text("value\n7\n")
            before = retained_files(logical)
            rejected = action(
                logical,
                "data",
                "update",
                "values",
                "--kind",
                "directory",
                "--target",
                "data/bundle",
                "--acknowledge-shared",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("data.update.consumer_invalid", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            document.write_text(
                document.read_text()
                .replace("--input '<values>'", "--input-dir '<values>'")
                .replace("source=values ", "source=values/selected.csv ")
            )
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--kind",
                    "directory",
                    "--target",
                    "data/bundle",
                    "--acknowledge-shared",
                )
            )
            checked(evidence(logical, "sync", "--id", "value"))
            listed = checked(action(logical, "data", "list"))["records"][0]
            self.assertEqual(listed["kind"], "directory")
            self.assertEqual(listed["boundary"], "origin")
            document.write_text(
                document.read_text().replace(
                    "--input-dir '<values>'", "--output-dir '<values>'"
                )
            )
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--boundary",
                    "generated",
                    "--acknowledge-shared",
                )
            )
            listed = checked(action(logical, "data", "list"))["records"][0]
            self.assertEqual(listed["boundary"], "generated")
            self.assertEqual(listed["kind"], "directory")
            checked(evidence(logical, "sync", "--id", "value"))
            self.assertIn("`7`", document.read_text())

    def test_data_rename_requires_every_parameter_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            command = (
                "./pyrun scripts/build.py --input-one '<values>' --input-two '<values>'"
            )
            logical, entry, document = fixture(Path(directory), command)
            (entry / "data/value.csv").write_text("value\n7\n")
            checked(sync(logical, "--add-origin", "values=data/value.csv"))
            original = document.read_text()
            document.write_text(
                original.replace(
                    command, "./pyrun scripts/build.py --input-one '<renamed>'"
                )
            )
            before = retained_files(logical)
            rejected = action(logical, "data", "rename", "values", "renamed")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("data.rename.command_incomplete", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            document.write_text(original.replace("<values>", "<renamed>"))
            checked(action(logical, "data", "rename", "values", "renamed"))
            listed = checked(action(logical, "command", "list"))["records"][0]
            self.assertEqual(
                listed["parameters"],
                ["--input-one", "<renamed>", "--input-two", "<renamed>"],
            )
            self.assertTrue(listed["requires_reproduction"])

    def test_cross_entry_data_rename_preserves_presentation_and_rolls_back_late_failure(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document, ref_document, summary = related_fixture(
                Path(directory)
            )
            presented_summary = summary.read_bytes()
            document.write_text(
                document.read_text()
                .replace("<values>", "<measurements>")
                .replace("source=values", "source=measurements")
            )
            incomplete = action(logical, "data", "rename", "values", "measurements")
            self.assertNotEqual(incomplete.returncode, 0)
            self.assertIn("e002", incomplete.stderr)
            ref_document.write_text(
                ref_document.read_text().replace("source=values", "source=measurements")
            )
            before = retained_files(logical)
            dry = checked(
                action(logical, "data", "rename", "values", "measurements", "--dry-run")
            )
            self.assertTrue(dry["changed"])
            self.assertEqual(retained_files(logical), before)
            original = storage.remove_or_write
            calls = []

            def fail_once(path, value):
                calls.append(path)
                if len(calls) == 4:
                    raise OSError("injected fourth registry publication")
                return original(path, value)

            with mock.patch.object(storage, "remove_or_write", side_effect=fail_once):
                rejected = action(logical, "data", "rename", "values", "measurements")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertGreaterEqual(len(calls), 4)
            self.assertEqual(retained_files(logical), before)
            checked(action(logical, "data", "rename", "values", "measurements"))
            self.assertEqual(summary.read_bytes(), presented_summary)
            owner = checked(action(logical, "data", "list"))["records"][0]
            referenced = checked(action(logical, "data", "list", entry="e002"))[
                "records"
            ][0]
            self.assertEqual(owner["name"], "measurements")
            self.assertEqual(referenced["name"], "measurements")
            self.assertEqual(referenced["from_entry"], "e001")
            self.assertEqual(referenced["target"], owner["target"])
            self.assertIn("`1.23`", document.read_text())
            self.assertIn("`1.2`", ref_document.read_text())
            compared = checked(evidence(logical, "compare", "--source", "measurements"))
            self.assertEqual(
                compared["records"],
                [
                    {"id": "owner", "before": "`1.23`", "after": "`1.23`"},
                    {"id": "forward", "before": "`1.2`", "after": "`1.2`"},
                ],
            )

    def test_data_and_evidence_round_trip_preserves_omitted_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/values.csv").write_text("value\n0.676\n")
            set_results(
                document,
                "``<!-- eid:rate source=values select=/value form=percentage "
                "render=fixed:1 reproduction_tolerance=0.01 -->",
            )
            checked(
                evidence(
                    logical,
                    "sync",
                    "--id",
                    "rate",
                    "--add-origin",
                    "values=data/values.csv",
                )
            )
            listed = checked(action(logical, "evidence", "list"))["records"][0]
            self.assertEqual(listed["reproduction_tolerance"], "0.01")
            self.assertEqual(
                listed["sources"],
                [
                    {
                        "source": "<values>",
                        "locator": {"path": [], "select": [["value"]]},
                    }
                ],
            )
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--target",
                    "data/values.csv",
                )
            )
            data = checked(action(logical, "data", "list"))["records"][0]
            self.assertEqual(data["identity"], {"algorithm": "sha256"})
            same = checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--target",
                    "data/values.csv",
                )
            )
            self.assertFalse(same["changed"])
            before = retained_files(logical)
            rejected = action(logical, "data", "rename", "values", "measurements")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("data.rename.markdown_incomplete", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            document.write_text(
                document.read_text().replace("source=values", "source=measurements")
            )
            checked(action(logical, "data", "rename", "values", "measurements"))
            renamed = checked(action(logical, "data", "list"))["records"][0]
            self.assertEqual(renamed["name"], "measurements")
            self.assertEqual(renamed["identity"], data["identity"])
            document.write_text(
                document.read_text().replace("eid:rate ", "eid:success ")
            )
            checked(action(logical, "evidence", "sync", "--rename", "rate=success"))
            self.assertEqual(
                checked(action(logical, "evidence", "list"))["records"][0]["id"],
                "success",
            )
            rejected = action(logical, "data", "delete", "measurements")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("success", rejected.stderr)
            document.write_text(
                document.read_text().replace(
                    "`67.6%`<!-- eid:success source=measurements select=/value "
                    "form=percentage render=fixed:1 reproduction_tolerance=0.01 -->",
                    "Removed evidence.",
                )
            )
            deleted = checked(
                action(logical, "evidence", "sync", "--delete", "success")
            )
            self.assertIn({"unused_data": "measurements"}, deleted["records"])
            deleted = checked(action(logical, "data", "delete", "measurements"))
            self.assertEqual(
                deleted["records"],
                [{"disconnected": str((entry / "data/values.csv").resolve())}],
            )
            self.assertTrue((entry / "data/values.csv").exists())

    def test_shared_data_update_lists_consumers_and_requires_acknowledgment(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py --output '<values>'"
            )
            (entry / "data/values.csv").write_text("value\n7\n")
            checked(sync(logical, "--add-generated", "values=data/values.csv"))
            set_results(document, "``<!-- eid:value source=values select=/value -->")
            checked(evidence(logical, "sync", "--id", "value"))
            before = retained_files(logical)
            rejected = action(
                logical,
                "data",
                "update",
                "values",
                "--reproduction-comparison",
                "evidence",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("data.update.shared", rejected.stderr)
            self.assertIn("build", rejected.stderr)
            self.assertIn("value", rejected.stderr)
            self.assertEqual(retained_files(logical), before)
            dry = checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--reproduction-comparison",
                    "evidence",
                    "--acknowledge-shared",
                    "--dry-run",
                )
            )
            self.assertTrue(dry["changed"])
            self.assertEqual(retained_files(logical), before)
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--reproduction-comparison",
                    "evidence",
                    "--acknowledge-shared",
                )
            )
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "values",
                    "--reproduction-comparison",
                    "exact",
                    "--acknowledge-shared",
                )
            )
            self.assertNotIn(
                "reproduction_comparison",
                checked(action(logical, "data", "list"))["records"][0],
            )

    def test_directory_identity_and_git_target_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root, "./pyrun scripts/build.py --input-dir '<bundle>'"
            )
            bundle = entry / "data/bundle"
            bundle.mkdir()
            (bundle / "selected.csv").write_text("value\n7\n")
            (bundle / "ignored.csv").write_text("value\n99\n")
            checked(sync(logical, "--add-origin-directory", "bundle=data/bundle"))
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "bundle",
                    "--identity",
                    "file:selected.csv",
                )
            )
            listed = checked(action(logical, "data", "list"))["records"][0]
            self.assertEqual(
                listed["identity"],
                {"algorithm": "identity-files-sha256-v1", "files": ["selected.csv"]},
            )
            checked(
                action(
                    logical, "data", "update", "bundle", "--identity", "pattern:*.csv"
                )
            )
            self.assertEqual(
                checked(action(logical, "data", "list"))["records"][0]["identity"],
                {"algorithm": "identity-patterns-sha256-v1", "patterns": ["*.csv"]},
            )
            checked(
                action(
                    logical, "data", "update", "bundle", "--identity", "byte-complete"
                )
            )
            self.assertEqual(
                checked(action(logical, "data", "list"))["records"][0]["identity"],
                {"algorithm": "directory-sha256-v1"},
            )
            repository, commit, _ = source_repository(root)
            prior = checked(action(logical, "command", "list"))["records"][0][
                "execution_id"
            ]
            document.write_text(
                document.read_text().replace(
                    "--input-dir '<bundle>'", "--input-commit '<repo:commit>'"
                )
            )
            checked(
                sync(
                    logical,
                    "--add-origin-git",
                    f"repo={commit}:{repository}",
                    "--delete-execution",
                    prior,
                )
            )
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "repo",
                    "--target",
                    f"{commit}:{repository}",
                )
            )
            repo = next(
                record
                for record in checked(action(logical, "data", "list"))["records"]
                if record["name"] == "repo"
            )
            self.assertEqual(repo["commit"], commit)
            self.assertEqual(repo["kind"], "git-repository")
            (repository / "source.txt").write_text("updated source\n")
            subprocess.run(
                ["git", "add", "source.txt"],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Research Log Tests",
                    "-c",
                    "user.email=research-log@example.invalid",
                    "commit",
                    "-m",
                    "Update fixture",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            updated = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            self.assertNotEqual(updated, commit)
            moved = root / "moved-repository"
            shutil.copytree(repository, moved)
            checked(
                action(
                    logical, "data", "update", "repo", "--target", f"{updated}:{moved}"
                )
            )
            inputs = checked(action(logical, "data", "list"))["records"]
            repo = next(record for record in inputs if record["name"] == "repo")
            self.assertEqual(repo["commit"], updated)
            self.assertEqual(repo["target"], str(moved.resolve()))
            bundle = next(record for record in inputs if record["name"] == "bundle")
            self.assertEqual(bundle["identity"], {"algorithm": "directory-sha256-v1"})
            self.assertEqual(bundle["target"], "data/bundle")

    def test_retention_additive_round_trip_and_connected_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(Path(directory), "./pyrun scripts/build.py")
            checked(sync(logical))
            for name in ("one.txt", "two.txt", "three.txt"):
                (entry / "data" / name).write_text(name)
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/one.txt",
                    "--target",
                    "data/one.txt",
                    "--reason",
                    "Preserve context",
                )
            )
            checked(
                action(
                    logical,
                    "retention",
                    "update",
                    "--id",
                    "kept",
                    "--add-target",
                    "data/two.txt",
                )
            )
            kept = checked(action(logical, "retention", "list"))["records"][0]
            self.assertEqual(kept["reason"], "Preserve context")
            self.assertEqual(kept["targets"], ["data/one.txt", "data/two.txt"])
            redundant = checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/one.txt",
                    "--target",
                    "data/two.txt",
                )
            )
            self.assertFalse(redundant["changed"])
            before = retained_files(logical)
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "overlap",
                "--target",
                "data/two.txt",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(retained_files(logical), before)
            rejected = action(
                logical, "retention", "add", "--id", "code", "--target", "scripts"
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("retention.target.connected", rejected.stderr)
            changed = checked(
                action(
                    logical,
                    "retention",
                    "update",
                    "--id",
                    "kept",
                    "--remove-target",
                    "data/one.txt",
                    "--clear-reason",
                )
            )
            self.assertEqual(
                changed["records"],
                [{"disconnected": str((entry / "data/one.txt").resolve())}],
            )
            repeated = checked(
                action(
                    logical,
                    "retention",
                    "update",
                    "--id",
                    "kept",
                    "--remove-target",
                    "data/one.txt",
                    "--clear-reason",
                )
            )
            self.assertFalse(repeated["changed"])
            checked(action(logical, "retention", "rename", "kept", "context"))
            kept = checked(action(logical, "retention", "list"))["records"][0]
            self.assertIsNone(kept["reason"])
            self.assertEqual(kept["targets"], ["data/two.txt"])
            deleted = checked(action(logical, "retention", "delete", "--id", "context"))
            self.assertEqual(
                deleted["records"],
                [{"disconnected": str((entry / "data/two.txt").resolve())}],
            )
            self.assertEqual(
                checked(action(logical, "retention", "list"))["records"], []
            )
            self.assertTrue((entry / "data/two.txt").exists())

    def test_command_rename_delete_and_late_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "./pyrun --cid build --exclusive -- scripts/build.py "
                "--output '<generated>'",
            )
            checked(sync(logical, "--add-generated", "generated=data/output.csv"))
            (entry / "scripts/build.py").write_text(
                "import argparse\nfrom pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--output', required=True)\n"
                "Path(p.parse_args().output).write_text('value\\n7\\n')\n"
            )
            (entry / "pyrun").symlink_to(
                Path(__file__).resolve().parents[1] / "scripts/pyrun"
            )
            executed = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--exclusive",
                "--",
                "scripts/build.py",
                "--output",
                "<generated>",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            executions_before = json.loads((entry / "pyrun.json").read_text())[
                "commands"
            ]["build"]["executions"]
            listed = checked(action(logical, "command", "list"))["records"][0]
            self.assertTrue(listed["exclusive"])
            self.assertEqual(listed["script"], "scripts/build.py")
            rejected = action(logical, "command", "rename", "build", "make")
            self.assertNotEqual(rejected.returncode, 0)
            document.write_text(
                document.read_text().replace("--cid build", "--cid make")
            )
            checked(action(logical, "command", "rename", "build", "make"))
            self.assertEqual(
                json.loads((entry / "pyrun.json").read_text())["commands"]["make"][
                    "executions"
                ],
                executions_before,
            )
            self.assertTrue(
                checked(action(logical, "command", "list"))["records"][0]["exclusive"]
            )
            set_results(document, "``<!-- eid:value source=generated select=/value -->")
            checked(evidence(logical, "sync", "--id", "value"))
            document.write_text(
                document.read_text().replace(
                    "./pyrun --cid make --exclusive -- scripts/build.py "
                    "--output '<generated>'",
                    "# Removed producer",
                )
            )
            rejected = action(logical, "command", "delete", "--cid", "make")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("command.delete.outputs_in_use", rejected.stderr)
            document.write_text(
                document.read_text().replace(
                    "`7`<!-- eid:value source=generated select=/value -->",
                    "Removed evidence.",
                )
            )
            checked(action(logical, "evidence", "sync", "--delete", "value"))
            before = retained_files(logical)
            original = storage.remove_or_write
            calls = []

            def fail_once(path, value):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError("injected second publication")
                return original(path, value)

            with mock.patch.object(storage, "remove_or_write", side_effect=fail_once):
                rejected = action(logical, "command", "delete", "--cid", "make")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(retained_files(logical), before)
            deleted = checked(action(logical, "command", "delete", "--cid", "make"))
            self.assertEqual(
                deleted["records"],
                [{"disconnected": str((entry / "data/output.csv").resolve())}],
            )
            self.assertEqual(checked(action(logical, "command", "list"))["records"], [])
            self.assertEqual(checked(action(logical, "data", "list"))["records"], [])
            self.assertTrue((entry / "data/output.csv").exists())
