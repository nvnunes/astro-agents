from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log
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
    def test_help_exposes_only_sync(self) -> None:
        family = run_log(Path.cwd(), "command", "--help")
        action = run_log(Path.cwd(), "command", "sync", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("sync", family.stdout)
        for flag in (
            "--cid",
            "--add-origin",
            "--add-generated",
            "--rename",
            "--remove",
            "--retire",
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
            self.assertIn("command.sync.retirement.required", refused.stderr)
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            stale = next(
                identity
                for identity, execution in state.commands["build"].executions.items()
                if "H" in execution.recipe.parameters
            )
            retired = sync(logical, "--retire", stale)
            self.assertEqual(retired.returncode, 0, retired.stderr)
            current = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=Path(directory)
            )
            self.assertNotIn(stale, current.commands["build"].executions)

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
                f"--retire {records[0]['execution_id']}",
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
                for name in ("data.json", "pyrun.json")
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
                    "--retire",
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
            after = {
                name: (entry / name).read_bytes()
                for name in ("data.json", "pyrun.json")
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

    def test_rename_refuses_unselected_cid_use(self) -> None:
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
            self.assertIn("command.sync.rename.unsafe", refused.stderr)
            data = json.loads((entry / "data.json").read_text())
            self.assertEqual(data["inputs"][0]["name"], "source")


if __name__ == "__main__":
    unittest.main()
