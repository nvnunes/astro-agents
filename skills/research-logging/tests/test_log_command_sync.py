from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import (
    PROCESS_TIMEOUT_SECONDS,
    run_log,
    run_pyrun_process,
)
from research_log_reservations import reserve_execution
from validation.pyrun_state import load_pyrun_state


def fixture(root: Path, command: str) -> tuple[Path, Path, Path]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    logical = root / "docs/study"
    entry = logical / "entries/2030-01-01-e001-test"
    (entry / "scripts").mkdir(parents=True)
    (entry / "data").mkdir()
    summary = root / "docs/study.md"
    summary.write_text(
        "# Study\n\n## Entries\n\n"
        "- `2030-01-01` [Test](study/entries/2030-01-01-e001-test/e001.md)\n",
        encoding="utf-8",
    )
    document = entry / "e001.md"
    document.write_text(
        "# Test\n\n## Execution\n\n`Steps:`\n\n```bash\n"
        + command
        + "\n```\n\n`Results:`\n\nPending.\n",
        encoding="utf-8",
    )
    (entry / "scripts/build.py").write_text("raise RuntimeError('never run')\n")
    return logical, entry, document


def sync(logical: Path, *extra: str):
    return run_log(
        logical.parent,
        "command",
        "sync",
        "--path",
        str(logical),
        "--entry",
        "e001",
        "--cid",
        "build",
        *extra,
    )


class LogCommandSyncTests(unittest.TestCase):
    def test_pinned_git_origin_allows_outputs_beneath_repository_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root,
                "./pyrun --other-inputs baseline-commit -- scripts/build.py "
                '--baseline-repository-input "<baseline>" '
                '--baseline-commit "<baseline:commit>" --output-dir "<result>"',
            )
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Other\n\n`Steps:`\n\n```bash\n"
                + "./pyrun --cid other -- scripts/build.py --output data/other.txt\n"
                + "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )
            (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tracked.txt"],
                cwd=root,
                check=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
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
                    "fixture",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                text=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
            ).strip()
            arguments = (
                "--add-origin-git",
                f"baseline={commit}:{root}",
                "--add-generated-directory",
                "result=data/generated",
            )

            with reserve_execution(
                root, entry, "other", (), (entry / "data/other.txt",)
            ):
                reservations = tuple(
                    (root / ".cache/research-log-operations").glob(
                        "ordinary-execution-*.json"
                    )
                )
                reservation_bytes = {path: path.read_bytes() for path in reservations}
                preview = sync(logical, *arguments, "--dry-run")

                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertFalse((entry / "data.json").exists())
                self.assertFalse((entry / "pyrun.json").exists())
                published = sync(logical, *arguments)
                self.assertEqual(published.returncode, 0, published.stderr)
                resources = {
                    item["name"]: item
                    for item in json.loads(
                        (entry / "data.json").read_text(encoding="utf-8")
                    )["inputs"]
                }
                self.assertEqual(resources["baseline"]["identity"]["commit"], commit)
                self.assertEqual(resources["baseline"]["kind"], "git-repository")
                before = {
                    name: (entry / name).read_bytes()
                    for name in ("data.json", "pyrun.json")
                }
                repeated = sync(logical, *arguments)
                self.assertEqual(repeated.returncode, 0, repeated.stderr)
                self.assertEqual(
                    {name: (entry / name).read_bytes() for name in before}, before
                )
                self.assertFalse((entry / "data/generated").exists())

                mirror = root / "mirror"
                subprocess.run(
                    ["git", "clone", "--quiet", str(root), str(mirror)],
                    check=True,
                    capture_output=True,
                    timeout=PROCESS_TIMEOUT_SECONDS,
                )
                retargeted = run_log(
                    root,
                    "data",
                    "update",
                    "baseline",
                    "--target",
                    f"{commit}:{mirror}",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                )
                self.assertEqual(retargeted.returncode, 0, retargeted.stderr)
                document.write_text(
                    document.read_text(encoding="utf-8")
                    .replace("<baseline>", "<renamed>")
                    .replace("<baseline:commit>", "<renamed:commit>"),
                    encoding="utf-8",
                )
                rename_arguments = (
                    "data",
                    "rename",
                    "baseline",
                    "renamed",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                )
                before_rename = {
                    name: (entry / name).read_bytes()
                    for name in ("data.json", "pyrun.json")
                }
                rename_preview = run_log(root, *rename_arguments, "--dry-run")
                self.assertEqual(rename_preview.returncode, 0, rename_preview.stderr)
                self.assertEqual(
                    {name: (entry / name).read_bytes() for name in before_rename},
                    before_rename,
                )
                renamed = run_log(root, *rename_arguments)
                self.assertEqual(renamed.returncode, 0, renamed.stderr)
                state = load_pyrun_state(
                    entry / "pyrun.json", entry_root=entry, project_root=root
                )
                for execution in state.commands["build"].executions.values():
                    self.assertIn("<renamed>", execution.recipe.parameters)
                    self.assertIn("<renamed:commit>", execution.recipe.parameters)
                    self.assertEqual(execution.recipe.inputs, ("renamed",))
                renamed_resources = json.loads(
                    (entry / "data.json").read_text(encoding="utf-8")
                )["inputs"]
                self.assertNotIn(
                    "baseline", {item["name"] for item in renamed_resources}
                )
                renamed_resource = next(
                    item for item in renamed_resources if item["name"] == "renamed"
                )
                self.assertEqual(renamed_resource["identity"]["commit"], commit)
                self.assertEqual(
                    {path: path.read_bytes() for path in reservations},
                    reservation_bytes,
                )

    def test_live_file_and_directory_origins_still_reject_recorded_producers(
        self,
    ) -> None:
        for kind in ("file", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                logical, entry, document = fixture(
                    Path(directory), './pyrun scripts/build.py --input "<source>"'
                )
                source = entry / "data/source"
                if kind == "directory":
                    source.mkdir()
                    (source / "member.txt").write_text("source\n", encoding="utf-8")
                    output = "data/source/member.txt"
                else:
                    source.write_text("source\n", encoding="utf-8")
                    output = "data/source"
                document.write_text(
                    document.read_text(encoding="utf-8")
                    + "\n## Producer\n\n`Steps:`\n\n```bash\n"
                    + f"./pyrun --cid other -- scripts/build.py --output {output}\n"
                    + "```\n\n`Results:`\n\nPending.\n",
                    encoding="utf-8",
                )
                flag = (
                    "--add-origin-directory" if kind == "directory" else "--add-origin"
                )

                result = sync(logical, flag, "source=data/source", "--dry-run")

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("command.sync.origin.produced", result.stderr)
                self.assertFalse((entry / "data.json").exists())
                self.assertFalse((entry / "pyrun.json").exists())

    def test_help_exposes_only_sync(self) -> None:
        family = run_log(Path.cwd(), "command", "--help")
        action = run_log(Path.cwd(), "command", "sync", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("sync", family.stdout)
        for flag in (
            "--cid",
            "--add-origin",
            "--add-origin-directory",
            "--add-origin-git",
            "--add-generated",
            "--add-generated-directory",
            "--add-from-entry",
            "--change-target",
            "--delete-execution",
            "--dry-run",
        ):
            self.assertIn(flag, action.stdout)
        self.assertEqual(run_log(Path.cwd(), "pyrun", "--help").returncode, 2)

    def test_dry_run_has_two_diffs_and_zero_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 2",
            )
            before = {
                path.relative_to(Path(directory)): path.read_bytes()
                for path in Path(directory).rglob("*")
                if path.is_file()
            }
            result = sync(logical, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["records"]), 2)
            self.assertTrue(all("diff" in item for item in payload["records"]))
            after = {
                path.relative_to(Path(directory)): path.read_bytes()
                for path in Path(directory).rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertFalse((entry / "pyrun.json").exists())

    def test_unsupported_effective_code_is_a_structured_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 2",
            )
            (entry / "scripts/build.py").write_text(
                "import importlib\nimportlib.import_module('helper')\n",
                encoding="utf-8",
            )

            result = sync(logical)

            self.assertEqual(result.returncode, 0, result.stderr)
            warnings = [
                item
                for item in json.loads(result.stdout)["records"]
                if item.get("status") == "warning"
            ]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(
                warnings[0]["code"],
                "command.sync.effective_code.unsupported",
            )
            self.assertEqual(warnings[0]["construct"], "dynamic_import")
            self.assertIn(
                "reproduction will select this command on every incremental plan",
                warnings[0]["consequence"],
            )
            self.assertIn("run pyrun after repairing", warnings[0]["consequence"])
            self.assertTrue((entry / "pyrun.json").is_file())

    def test_log_shared_import_is_supported_without_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 2",
            )
            log_scripts = entry.parent.parent / "scripts"
            log_scripts.mkdir()
            (log_scripts / "shared_helper.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )
            (entry / "scripts/build.py").write_text(
                "from shared_helper import VALUE\nprint(VALUE)\n",
                encoding="utf-8",
            )

            result = sync(logical)

            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(result.stdout)["records"]
            self.assertFalse(any(item.get("status") == "warning" for item in records))
            self.assertTrue((entry / "pyrun.json").is_file())

    def test_effective_code_operational_failure_warns_and_syncs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 2",
            )
            (entry / "scripts/build.py").write_text("if:\n", encoding="utf-8")

            result = sync(logical)

            self.assertEqual(result.returncode, 0, result.stderr)
            warnings = [
                item
                for item in json.loads(result.stdout)["records"]
                if item.get("status") == "warning"
            ]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(
                warnings[0]["code"],
                "command.sync.effective_code.unavailable",
            )
            self.assertEqual(
                warnings[0]["construct"], "effective_code.syntax_invalid"
            )
            self.assertEqual(warnings[0]["line"], 1)
            self.assertIn(
                "reproduction will select this command on every incremental plan",
                warnings[0]["consequence"],
            )
            self.assertIn("repair the analysis failure", warnings[0]["consequence"])
            self.assertTrue((entry / "pyrun.json").is_file())

    def test_implicit_cid_selects_the_python_program_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun scripts/build.py --count 2",
            )

            result = sync(logical)

            self.assertEqual(result.returncode, 0, result.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertEqual(set(state.commands), {"build"})

    def test_generated_directory_bootstraps_and_runs_through_pyrun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root,
                './pyrun scripts/build.py --output "<result>"',
            )
            document_text = document.read_text(encoding="utf-8")
            document.write_text(document_text, encoding="utf-8")
            (entry / "scripts/build.py").write_text(
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output', required=True)\n"
                "args = parser.parse_args()\n"
                "target = Path(args.output)\n"
                "target.mkdir(parents=True, exist_ok=True)\n"
                "(target / 'value.txt').write_text('made\\n')\n",
                encoding="utf-8",
            )
            (entry / "pyrun").symlink_to(
                Path(__file__).resolve().parents[1] / "scripts/pyrun"
            )

            synchronized = sync(
                logical,
                "--add-generated-directory",
                "result=data/generated",
            )

            self.assertEqual(synchronized.returncode, 0, synchronized.stderr)
            data = json.loads((entry / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "research-log-data/v6")
            self.assertEqual(data["inputs"][0]["kind"], "directory")
            self.assertFalse((entry / "data/generated").exists())

            executed = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--",
                "scripts/build.py",
                "--output",
                "<result>",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(
                (entry / "data/generated/value.txt").read_text(encoding="utf-8"),
                "made\n",
            )

    def test_declaration_ensure_is_additive_idempotent_and_conflict_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, _ = fixture(
                root,
                './pyrun scripts/build.py --input "<source>" --output "<result>"',
            )
            source = entry / "data/source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            created = sync(
                logical,
                "--add-origin",
                "source=data/source.csv",
                "--add-generated",
                "result=data/result.csv",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            data_path = entry / "data.json"
            seeded = json.loads(data_path.read_text(encoding="utf-8"))
            result_item = next(
                item for item in seeded["inputs"] if item["name"] == "result"
            )
            result_item["reproduction_comparison"] = {
                "contract": "research-log-evidence-scoped-comparison/1",
                "profile": "evidence",
            }
            data_path.write_text(
                json.dumps(seeded, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            before = {
                name: (entry / name).read_bytes()
                for name in ("data.json", "pyrun.json")
            }

            repeated = sync(
                logical,
                "--add-origin",
                f"source={source}",
                "--add-generated",
                "result=data/result.csv",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(
                {
                    name: (entry / name).read_bytes()
                    for name in ("data.json", "pyrun.json")
                },
                before,
            )
            omitted = sync(logical)
            self.assertEqual(omitted.returncode, 0, omitted.stderr)

            conflict = sync(
                logical,
                "--add-generated",
                "source=data/other.csv",
                "--dry-run",
            )
            self.assertEqual(conflict.returncode, 2)
            self.assertIn("command.sync.declaration.conflict", conflict.stderr)
            diagnostic = json.loads(conflict.stdout.splitlines()[-1])
            record = diagnostic["records"][0]
            self.assertEqual(record["name"], "source")
            self.assertNotEqual(record["maintained"], record["requested"])
            self.assertEqual(
                {
                    name: (entry / name).read_bytes()
                    for name in ("data.json", "pyrun.json")
                },
                before,
            )

    def test_numeric_cid_resolves_before_full_owner_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid 2 -- scripts/build.py --count 2",
            )

            result = run_log(
                logical.parent,
                "command",
                "sync",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--cid",
                "build-2",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertEqual(set(state.commands), {"build-2"})

    def test_full_override_selects_a_cid_different_from_the_program_stem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid publish-results -- scripts/build.py --count 2",
            )

            result = run_log(
                logical.parent,
                "command",
                "sync",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--cid",
                "publish-results",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertEqual(set(state.commands), {"publish-results"})

    def test_numeric_cid_distinguishes_multiple_invocations_of_one_program(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "./pyrun scripts/build.py --count 1",
            )
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Second execution\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid 2 -- scripts/build.py --count 2\n"
                "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )

            first = sync(logical)
            second = run_log(
                logical.parent,
                "command",
                "sync",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--cid",
                "build-2",
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertEqual(set(state.commands), {"build", "build-2"})

    def test_explicit_file_directory_git_and_cross_entry_add_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, producer, _ = fixture(
                root,
                './pyrun scripts/build.py --output "<shared>"',
            )
            self.assertEqual(
                sync(logical, "--add-generated", "shared=data/shared.csv").returncode,
                0,
            )
            consumer = logical / "entries/2030-01-02-e002-consumer"
            (consumer / "scripts").mkdir(parents=True)
            (consumer / "data/origin-dir").mkdir(parents=True)
            (consumer / "data/input.csv").write_text("value\n1\n", encoding="utf-8")
            (consumer / "scripts/consume.py").write_text("pass\n", encoding="utf-8")
            (consumer / "e002.md").write_text(
                "# Consumer\n\n## Execution\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid consume -- scripts/consume.py "
                '--input-file "<input>" --input-directory "<origin_dir>" '
                '--input-repository "<repository>" '
                '--input-commit "<repository:commit>" '
                '--input-shared "<shared>"\n'
                "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )
            summary = logical.parent / "study.md"
            summary.write_text(
                summary.read_text(encoding="utf-8") + "- `2030-01-02` [Consumer]"
                "(study/entries/2030-01-02-e002-consumer/e002.md)\n",
                encoding="utf-8",
            )
            tracked = root / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", tracked.name],
                cwd=root,
                check=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
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
                    "fixture",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                text=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
            ).strip()
            result = run_log(
                logical.parent,
                "command",
                "sync",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "--cid",
                "consume",
                "--add-origin",
                "input=data/input.csv",
                "--add-origin-directory",
                "origin_dir=data/origin-dir",
                "--add-origin-git",
                f"repository={commit}:{root}",
                "--add-from-entry",
                "shared=e001",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            items = {
                item["name"]: item
                for item in json.loads(
                    (consumer / "data.json").read_text(encoding="utf-8")
                )["inputs"]
            }
            self.assertEqual(items["input"]["kind"], "file")
            self.assertEqual(items["origin_dir"]["kind"], "directory")
            self.assertEqual(items["repository"]["kind"], "git-repository")
            self.assertEqual(items["shared"], {"from_entry": "e001", "name": "shared"})
            self.assertEqual(producer.name, "2030-01-01-e001-test")

    def test_target_change_preserves_identity_and_rejects_shared_consumers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root,
                './pyrun scripts/build.py --input "<source>"',
            )
            for name in ("old", "new"):
                target = entry / f"data/{name}"
                target.mkdir()
                (target / "manifest.json").write_text("{}\n", encoding="utf-8")
            (entry / "data.json").write_text(
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [
                            {
                                "identity": {
                                    "algorithm": "identity-files-sha256-v1",
                                    "files": ["manifest.json"],
                                },
                                "kind": "directory",
                                "location": "data/old",
                                "name": "source",
                                "origin": True,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            changed = sync(logical, "--change-target", "source=data/new")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            item = json.loads((entry / "data.json").read_text(encoding="utf-8"))[
                "inputs"
            ][0]
            self.assertEqual(item["location"], "data/new")
            self.assertEqual(item["identity"]["files"], ["manifest.json"])

            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Other\n\n`Steps:`\n\n```bash\n"
                './pyrun --cid other -- scripts/build.py --input "<source>"\n'
                "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )
            refused = sync(
                logical,
                "--change-target",
                "source=data/old",
                "--dry-run",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("command.sync.target.shared", refused.stderr)
            record = json.loads(refused.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["command_consumers"], ["other"])
            self.assertEqual(record["owner"], "log data update")

    def test_wrong_add_kind_fails_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, _ = fixture(
                root,
                './pyrun scripts/build.py --input "<source>"',
            )
            (entry / "data/source").mkdir()
            wrong_kind = sync(
                logical,
                "--add-origin",
                "source=data/source",
                "--dry-run",
            )
            self.assertEqual(wrong_kind.returncode, 2)
            self.assertIn("data.kind.conflict", wrong_kind.stderr)
            record = json.loads(wrong_kind.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["required"], "file")
            self.assertFalse((entry / "data.json").exists())
            self.assertFalse((entry / "pyrun.json").exists())

    def test_shared_target_change_lists_command_evidence_and_entry_consumers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), './pyrun scripts/build.py --output "<result>"'
            )
            self.assertEqual(
                sync(logical, "--add-generated", "result=data/result.json").returncode,
                0,
            )
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Other\n\n`Steps:`\n\n```bash\n"
                './pyrun --cid other -- scripts/build.py --input "<result>"\n'
                "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )
            (entry / "evidence.json").write_text(
                json.dumps(
                    {
                        "schema": "research-log-evidence/v5",
                        "records": [
                            {
                                "id": "value",
                                "document": "entries/2030-01-01-e001-test/e001.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<result>",
                                        "locator": {"select": [["value"]]},
                                    }
                                ],
                                "transformation": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            referencing = logical / "entries/2030-01-02-e002-reference"
            referencing.mkdir()
            (referencing / "e002.md").write_text("# Reference\n", encoding="utf-8")
            (referencing / "data.json").write_text(
                json.dumps(
                    {
                        "schema": "research-log-data/v6",
                        "inputs": [{"from_entry": "e001", "name": "result"}],
                    }
                ),
                encoding="utf-8",
            )

            same = sync(logical, "--change-target", "result=data/result.json")
            self.assertEqual(same.returncode, 0, same.stderr)
            refused = sync(
                logical,
                "--change-target",
                "result=data/moved.json",
                "--dry-run",
            )

            self.assertEqual(refused.returncode, 2)
            record = json.loads(refused.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["command_consumers"], ["other"])
            self.assertEqual(record["evidence_consumers"], ["value"])
            self.assertEqual(
                record["cross_entry_consumers"], ["2030-01-02-e002-reference"]
            )
            self.assertEqual(record["owner"], "log data update")

    def test_missing_name_shows_file_and_directory_declaration_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory), './pyrun scripts/build.py --output "<result>"'
            )

            refused = sync(logical, "--dry-run")

            self.assertEqual(refused.returncode, 2)
            record = json.loads(refused.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["name"], "result")
            self.assertEqual(
                record["required_flags"],
                [
                    "--add-generated result=PATH",
                    "--add-generated-directory result=PATH",
                ],
            )
            self.assertFalse((entry / "data.json").exists())

    def test_missing_generated_producer_names_the_bootstrap_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                './pyrun scripts/build.py --input "<generated>"',
            )

            refused = sync(
                logical,
                "--add-generated",
                "generated=data/generated.csv",
                "--dry-run",
            )

            self.assertEqual(refused.returncode, 2)
            self.assertIn("producer.missing", refused.stderr)
            record = json.loads(refused.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["name"], "generated")
            self.assertEqual(record["owner"], "log command sync")
            self.assertFalse((entry / "data.json").exists())
            self.assertFalse((entry / "pyrun.json").exists())

    def test_malformed_registry_requires_direct_repair_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory), "./pyrun scripts/build.py --count 1"
            )
            (entry / "data.json").write_text('{"schema":', encoding="utf-8")
            before = (entry / "data.json").read_bytes()
            malformed = sync(logical, "--dry-run")
            self.assertEqual(malformed.returncode, 2)
            self.assertIn("command.sync.registry.invalid", malformed.stderr)
            repair = json.loads(malformed.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(repair["required_action"], "direct Repair")
            self.assertEqual((entry / "data.json").read_bytes(), before)
            self.assertFalse((entry / "pyrun.json").exists())

    def test_success_creates_pending_no_output_member_without_observing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 2",
            )
            with (
                mock.patch(
                    "validation.commands._observe_script",
                    side_effect=AssertionError("must not observe script"),
                ),
                mock.patch(
                    "validation.commands.observe_fingerprint",
                    side_effect=AssertionError("must not observe inputs"),
                ),
            ):
                result = sync(logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            execution = next(iter(state.commands["build"].executions.values()))
            self.assertTrue(execution.requires_reproduction)
            self.assertIsNone(execution.observed.script)
            self.assertEqual(execution.observed.inputs, ())
            self.assertEqual(execution.observed.outputs, ())

    def test_combined_declarations_and_policy_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py "
                '--input "<source>" --output "<result>"',
            )
            source = entry / "data/source.csv"
            source.write_text("value\n1\n")
            created = sync(
                logical,
                "--add-origin",
                f"source={source}",
                "--add-generated",
                "result=data/result.csv",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            data = json.loads((entry / "data.json").read_text())
            self.assertEqual(
                [item["name"] for item in data["inputs"]], ["result", "source"]
            )
            before = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            identity, old = next(iter(before.commands["build"].executions.items()))
            document.write_text(
                document.read_text().replace(
                    "--cid build --",
                    "--cid build --auto-reproduce=false --exclusive --",
                )
            )
            updated = sync(logical)
            self.assertEqual(updated.returncode, 0, updated.stderr)
            after = (
                load_pyrun_state(
                    entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
                )
                .commands["build"]
                .executions[identity]
            )
            self.assertFalse(after.auto_reproduce)
            self.assertTrue(after.exclusive)
            self.assertEqual(after.observed, old.observed)
            self.assertEqual(after.requires_reproduction, old.requires_reproduction)

    def test_stale_member_requires_exact_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "for case in H J; do\n"
                '  ./pyrun --cid build -- scripts/build.py --case "$case"\n'
                "done",
            )
            first = sync(logical)
            self.assertEqual(first.returncode, 0, first.stderr)
            document.write_text(document.read_text().replace("H J", "J K"))
            refused = sync(logical)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("command.sync.execution.deletion_required", refused.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            stale = next(
                identity
                for identity, execution in state.commands["build"].executions.items()
                if "H" in execution.recipe.parameters
            )
            retired = sync(logical, "--delete-execution", stale)
            self.assertEqual(retired.returncode, 0, retired.stderr)
            current = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertNotIn(stale, current.commands["build"].executions)

    def test_current_execution_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun scripts/build.py --count 1",
            )
            self.assertEqual(sync(logical).returncode, 0)
            identity = next(
                iter(
                    load_pyrun_state(
                        entry / "pyrun.json",
                        entry_root=entry,
                        project_root=Path(directory),
                    )
                    .commands["build"]
                    .executions
                )
            )

            refused = sync(
                logical,
                "--delete-execution",
                identity,
                "--dry-run",
            )

            self.assertEqual(refused.returncode, 2)
            self.assertIn("command.sync.execution.not_stale", refused.stderr)
            record = json.loads(refused.stdout.splitlines()[-1])["records"][0]
            self.assertEqual(record["execution_id"], identity)
            self.assertEqual(record["status"], "current")

    def test_stale_dry_run_returns_exact_retry_flags_without_diagnostic_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "for case in H J; do\n"
                '  ./pyrun --cid build -- scripts/build.py --case "$case"\n'
                "done",
            )
            self.assertEqual(sync(logical).returncode, 0)
            document.write_text(document.read_text().replace("H J", "J K"))
            before = tuple(sorted(path.as_posix() for path in logical.rglob("*")))
            refused = sync(logical, "--dry-run")
            self.assertEqual(refused.returncode, 2)
            payload = json.loads(refused.stdout.splitlines()[-1])
            records = payload["records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["retry_flag"],
                f"--delete-execution {records[0]['execution_id']}",
            )
            after = tuple(sorted(path.as_posix() for path in logical.rglob("*")))
            self.assertEqual(after, before)

    def test_second_file_failure_rolls_back_both_registries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                './pyrun --cid build -- scripts/build.py --input "<source>" --count 1',
            )
            source = entry / "data/source.csv"
            source.write_text("value\n1\n")
            self.assertEqual(
                sync(logical, "--add-origin", f"source={source}").returncode, 0
            )
            document.write_text(document.read_text().replace("--count 1", "--count 2"))
            before = {
                name: (entry / name).read_bytes()
                for name in ("e001.md", "data.json", "pyrun.json")
                if (entry / name).exists()
            }
            from log_commands import storage

            original = storage.atomic_write_text
            calls = 0

            def fail_second(path: Path, text: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fixture second publication failure")
                original(path, text)

            with mock.patch(
                "log_commands.storage.atomic_write_text", side_effect=fail_second
            ):
                failed = sync(
                    logical,
                    "--delete-execution",
                    next(
                        iter(
                            load_pyrun_state(
                                entry / "pyrun.json",
                                entry_root=entry,
                                project_root=Path(directory),
                            )
                            .commands["build"]
                            .executions
                        )
                    ),
                )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("fixture second publication failure", failed.stderr)
            self.assertGreaterEqual(calls, 2)
            after = {
                name: (entry / name).read_bytes()
                for name in ("e001.md", "data.json", "pyrun.json")
                if (entry / name).exists()
            }
            self.assertEqual(after, before)

    def test_unrelated_parse_failure_is_reported_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --count 1",
            )
            split = entry / "e001a.md"
            split.write_text(
                "# Split\n\n## Broken\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid unrelated -- scripts/build.py | tee output.txt\n"
                "```\n\n`Results:`\n\nUnavailable.\n"
            )
            summary = logical.parent / "study.md"
            summary.write_text(
                summary.read_text().replace(
                    ")\n", ")\n  [Split](study/entries/2030-01-01-e001-test/e001a.md)\n"
                )
            )
            result = sync(logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(result.stdout)["records"]
            self.assertTrue(
                any(item.get("status") == "unrelated-failure" for item in records)
            )

    def test_competing_producer_blocks_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --output data/result.csv",
            )
            document.write_text(
                document.read_text() + "\n## Other\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid other -- scripts/build.py --output data/result.csv\n"
                "```\n\n`Results:`\n\nPending.\n"
            )
            result = sync(logical)
            self.assertEqual(result.returncode, 2)
            self.assertIn("command.sync.producer.ambiguous", result.stderr)
            self.assertFalse((entry / "pyrun.json").exists())

    def test_rejected_competing_output_blocks_selected_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                "./pyrun --cid build -- scripts/build.py --output data/result.csv",
            )
            document.write_text(
                document.read_text() + "\n## Broken\n\n`Steps:`\n\n```bash\n"
                "./pyrun --cid other -- scripts/build.py "
                "--output data/result.csv | tee x\n"
                "```\n\n`Results:`\n\nPending.\n"
            )
            result = sync(logical)
            self.assertEqual(result.returncode, 2)
            self.assertIn("command.sync.producer.unresolved", result.stderr)
            self.assertFalse((entry / "pyrun.json").exists())

    def test_removed_command_local_rename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                './pyrun --cid build -- scripts/build.py --input "<source>"',
            )
            source = entry / "data/source.csv"
            source.write_text("value\n1\n")
            self.assertEqual(
                sync(logical, "--add-origin", f"source={source}").returncode, 0
            )
            document.write_text(
                document.read_text().replace("<source>", "<renamed>")
                + "\n## Other\n\n`Steps:`\n\n```bash\n"
                './pyrun --cid other -- scripts/build.py --input "<source>"\n'
                "```\n\n`Results:`\n\nPending.\n"
            )
            refused = sync(logical, "--rename", "source=renamed")
            self.assertEqual(refused.returncode, 2)
            self.assertIn("cli.arguments.invalid", refused.stderr)
            data = json.loads((entry / "data.json").read_text())
            self.assertEqual(data["inputs"][0]["name"], "source")


if __name__ == "__main__":
    unittest.main()
