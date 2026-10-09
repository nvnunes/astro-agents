"""Metadata authoring preserves provenance while an input volume is offline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from log_commands.context import resolve_entry, resolve_log
from log_commands.graph_state import material_consumers_many
from research_log_cli_test_support import SCRIPTS, run_pyrun_process
from test_log_command_sync import fixture, sync
from test_log_evidence_sync import evidence, set_results
from test_log_graph_lifecycle import action, checked


def offline_fixture(
    root: Path, *, with_output: bool = False
) -> tuple[Path, Path, Path, dict]:
    output = ' --output "<result>"' if with_output else ""
    logical, entry, document = fixture(
        root,
        './pyrun --cid build -- scripts/build.py --input "<source>/manifest.json"'
        + output,
    )
    bundle = entry / "data/source"
    bundle.mkdir()
    (bundle / "manifest.json").write_text('{"value":7}\n', encoding="utf-8")
    (entry / "scripts/build.py").write_text(
        "from pathlib import Path\nPath('executed.txt').write_text('ran')\n"
        + (
            "Path('data/result.csv').write_text('value\\n7\\n')\n"
            if with_output
            else ""
        ),
        encoding="utf-8",
    )
    generated = ("--add-generated", "result=data/result.csv") if with_output else ()
    checked(sync(logical, "--add-origin-directory", "source=data/source", *generated))
    (entry / "pyrun").symlink_to(SCRIPTS / "pyrun")
    ran = run_pyrun_process(
        entry,
        "--cid",
        "build",
        "--",
        "scripts/build.py",
        "--input",
        "<source>/manifest.json",
        *(("--output", "<result>") if with_output else ()),
    )
    if ran.returncode:
        raise AssertionError(ran.stderr)
    state = json.loads((entry / "pyrun.json").read_text(encoding="utf-8"))
    if with_output:
        set_results(document, "`7`<!-- eid:value source=result select=/value -->")
        checked(evidence(logical, "sync", "--id", "value"))
    bundle.rename(root / "offline-source")
    (entry / "executed.txt").unlink()
    return logical, entry, document, state


def execution(state: dict, cid: str) -> dict:
    return next(iter(state["commands"][cid]["executions"].values()))


class OfflineAuthoringTests(unittest.TestCase):
    def test_data_update_accepts_equivalent_offline_absolute_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, _, _ = offline_fixture(root)
            alias = root / "logical-source"
            alias.symlink_to(entry / "data/source", target_is_directory=True)
            before = {
                name: (entry / name).read_bytes()
                for name in ("data.json", "pyrun.json")
            }
            for extra in (("--dry-run",), ()):
                updated = checked(
                    action(
                        logical,
                        "data",
                        "update",
                        "source",
                        "--target",
                        str(alias),
                        *extra,
                    )
                )
                self.assertFalse(updated["changed"])
                self.assertEqual(
                    {name: (entry / name).read_bytes() for name in before}, before
                )
            self.assertTrue(alias.is_symlink())
            self.assertFalse(alias.exists())

    def test_offline_location_update_preserves_identity_evidence_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document, _ = offline_fixture(root, with_output=True)
            document.write_text(
                document.read_text().replace("<source>", "<renamed>"),
                encoding="utf-8",
            )
            checked(action(logical, "data", "rename", "source", "renamed"))
            document.write_text(
                document.read_text().replace("<renamed>", "<source>"),
                encoding="utf-8",
            )
            checked(action(logical, "data", "rename", "renamed", "source"))
            registry = entry / "data.json"
            declared = json.loads(registry.read_text())
            source = next(
                item for item in declared["inputs"] if item["name"] == "source"
            )
            source["location"] = str(entry / "data/source")
            registry.write_text(json.dumps(declared), encoding="utf-8")
            before = {
                name: (entry / name).read_bytes()
                for name in (
                    "data.json",
                    "pyrun.json",
                    "evidence.json",
                )
            }
            self.assertTrue(
                execution(json.loads(before["pyrun.json"]), "build")[
                    "requires_reproduction"
                ]
            )
            arguments = ("--target", "data/source")
            checked(
                action(logical, "data", "update", "source", *arguments, "--dry-run")
            )
            self.assertEqual(
                {name: (entry / name).read_bytes() for name in before}, before
            )
            checked(action(logical, "data", "update", "source", *arguments))
            after = json.loads(registry.read_text())
            expected = {
                **declared,
                "inputs": [
                    {**item, "location": "data/source"}
                    if item["name"] == "source"
                    else item
                    for item in declared["inputs"]
                ],
            }
            self.assertEqual(after, expected)
            for name in ("pyrun.json", "evidence.json"):
                self.assertEqual((entry / name).read_bytes(), before[name])
            refused = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--",
                "scripts/build.py",
                "--input",
                "<source>/manifest.json",
                "--output",
                "<result>",
            )
            self.assertIn("data.target.missing", refused.stderr)
            self.assertFalse((entry / "executed.txt").exists())
            self.assertEqual((entry / "pyrun.json").read_bytes(), before["pyrun.json"])

    def test_offline_location_update_keeps_shared_acknowledgment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document, _ = offline_fixture(root)
            registry = entry / "data.json"
            declared = json.loads(registry.read_text())
            declared["inputs"][0]["location"] = str(entry / "data/source")
            registry.write_text(json.dumps(declared), encoding="utf-8")
            document.write_text(
                document.read_text() + "\n## Peer\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid peer -- scripts/build.py "
                '--input "<source>/manifest.json"\n```\n\n`Results:`\n\nPending.\n',
                encoding="utf-8",
            )
            before = registry.read_bytes()
            arguments = ("--target", "data/source")
            refused = action(logical, "data", "update", "source", *arguments)
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("data.update.shared", refused.stderr)
            self.assertEqual(registry.read_bytes(), before)
            checked(
                action(
                    logical,
                    "data",
                    "update",
                    "source",
                    *arguments,
                    "--acknowledge-shared",
                )
            )
            self.assertEqual(
                json.loads(registry.read_text())["inputs"][0]["location"], "data/source"
            )

    def test_offline_location_update_rejects_new_targets_and_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, _, _ = offline_fixture(root)
            alias = root / "logical-source"
            alias.symlink_to(entry / "data/source", target_is_directory=True)
            before = (entry / "data.json").read_bytes()
            for arguments in (
                ("--target", str(root / "different-source")),
                ("--target", str(alias), "--kind", "file"),
                ("--target", str(alias), "--identity", "file:manifest.json"),
            ):
                with self.subTest(arguments=arguments):
                    refused = action(logical, "data", "update", "source", *arguments)
                    self.assertEqual(refused.returncode, 2, refused.stderr)
                    self.assertIn("data.target.missing", refused.stderr)
                    self.assertEqual((entry / "data.json").read_bytes(), before)

    def test_command_rename_preserves_history_without_input_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document, before = offline_fixture(Path(directory))
            document.write_text(
                document.read_text().replace("--cid build", "--cid renamed"),
                encoding="utf-8",
            )
            arguments = ("--rename", "build=renamed")
            registry_before = (entry / "pyrun.json").read_bytes()
            checked(action(logical, "command", "sync", *arguments, "--dry-run"))
            self.assertEqual((entry / "pyrun.json").read_bytes(), registry_before)
            checked(action(logical, "command", "sync", *arguments))
            after = json.loads((entry / "pyrun.json").read_text())
            self.assertEqual(execution(after, "renamed"), execution(before, "build"))
            self.assertFalse((entry / "executed.txt").exists())

    def test_data_rename_preserves_observations_and_marks_reproduction_required(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document, before = offline_fixture(Path(directory))
            document.write_text(
                document.read_text().replace("<source>", "<renamed>"),
                encoding="utf-8",
            )
            checked(action(logical, "data", "rename", "source", "renamed", "--dry-run"))
            checked(action(logical, "data", "rename", "source", "renamed"))
            after = execution(json.loads((entry / "pyrun.json").read_text()), "build")
            prior = execution(before, "build")
            self.assertEqual(after["last_run_at"], prior["last_run_at"])
            self.assertEqual(
                after["observed"],
                {
                    **prior["observed"],
                    "inputs": {"renamed": prior["observed"]["inputs"]["source"]},
                },
            )
            self.assertTrue(after["requires_reproduction"])
            self.assertFalse((entry / "executed.txt").exists())

    def test_retention_keeps_connected_code_protected_with_offline_input(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _, _ = offline_fixture(Path(directory))
            (entry / "data/unused.txt").write_text("unused\n", encoding="utf-8")
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "unused",
                    "--target",
                    "data/unused.txt",
                )
            )
            refused = action(
                logical,
                "retention",
                "add",
                "--id",
                "code",
                "--target",
                "scripts/build.py",
            )
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("retention.target.connected", refused.stderr)

    def test_execution_still_refuses_unavailable_input(self):
        with tempfile.TemporaryDirectory() as directory:
            _, entry, _, before = offline_fixture(Path(directory))
            refused = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--",
                "scripts/build.py",
                "--input",
                "<source>/manifest.json",
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("data.target.missing", refused.stderr)
            self.assertFalse((entry / "executed.txt").exists())
            self.assertEqual(json.loads((entry / "pyrun.json").read_text()), before)

    def test_evidence_rooted_output_stays_connected_with_offline_input(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _, before = offline_fixture(
                Path(directory), with_output=True
            )
            refused = action(
                logical,
                "retention",
                "add",
                "--id",
                "result",
                "--target",
                "data/result.csv",
            )
            self.assertIn("retention.target.connected", refused.stderr)
            self.assertEqual(json.loads((entry / "pyrun.json").read_text()), before)

    def test_sync_still_rejects_undeclared_and_escaping_inputs(self):
        for token in ("<unknown>/manifest.json", "<source>/../manifest.json"):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as directory:
                logical, entry, document, before = offline_fixture(Path(directory))
                document.write_text(
                    document.read_text().replace("<source>/manifest.json", token),
                    encoding="utf-8",
                )
                refused = sync(logical)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(json.loads((entry / "pyrun.json").read_text()), before)

    def test_offline_evidence_member_stays_connected_and_can_be_renamed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document, _ = offline_fixture(root)
            bundle = entry / "data/source"
            (root / "offline-source").rename(bundle)
            set_results(
                document,
                "`7`<!-- eid:value source=source/manifest.json select=/value -->",
            )
            checked(evidence(logical, "sync", "--id", "value"))
            bundle.rename(root / "offline-source")
            member = bundle / "manifest.json"
            consumers = material_consumers_many(
                resolve_entry(resolve_log(logical), "e001"), (member,)
            )
            self.assertTrue(
                any(item.get("evidence") == "value" for item in consumers[member])
            )
            document.write_text(
                document.read_text()
                .replace("<source>", "<renamed>")
                .replace("source=source/", "source=renamed/"),
                encoding="utf-8",
            )
            checked(action(logical, "data", "rename", "source", "renamed"))
            self.assertFalse(bundle.exists())


if __name__ == "__main__":
    unittest.main()
