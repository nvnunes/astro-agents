from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import log_commands.pyrun_migration as migration
from log_commands.model import ActionError
from log_commands.pyrun_migration import (
    MIGRATION_RECORD,
    build_migration_plan,
    migrate_project,
)
from research_log_cli_test_support import run_log
from research_log_data import Fingerprint
from validation.pyrun_outputs import (
    OutputSupport,
    PyrunOutputsFile,
    ScriptSupport,
)
from validation.pyrun_state import load_pyrun_state


def _fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", digest=hashlib.sha256(path.read_bytes()).hexdigest())


def _write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _fixture(root: Path, *, obsolete: bool = False) -> tuple[Path, Path, str | None]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    log = root / "docs/study"
    entry = log / "entries/2030-01-01-e001-study"
    scripts = entry / "scripts"
    data = entry / "data"
    scripts.mkdir(parents=True)
    data.mkdir()
    _write(
        root / "docs/study.md",
        "# Study\n\n"
        "Validation: [latest completed report](study/validation.md)\n\n"
        "## Summary\n\n"
        "## Entries\n\n"
        "- `2030-01-01` [Study](study/entries/2030-01-01-e001-study/e001.md)\n",
    )
    _write(scripts / "helper_old.py", "VALUE = 'old'\n")
    _write(scripts / "helper_new.py", "VALUE = 'new'\n")
    old_script = _write(
        scripts / "old.py", "import helper_old\nprint(helper_old.VALUE)\n"
    )
    _write(scripts / "new.py", "import helper_new\nprint(helper_new.VALUE)\n")
    source = _write(data / "source.csv", "case,value\nfixture,1\n")
    old_output = _write(data / "old.csv", "case,value\nold,1\n")
    new_output = _write(data / "new.csv", "case,value\nnew,2\n")
    _write(
        entry / "e001.md",
        "# Study\n\n"
        "## Execution\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/old.py --input-data '<source>' "
        "--output-data data/old.csv\n"
        "\n"
        "./pyrun --slow -- scripts/new.py --input-data '<source>' "
        "--output-data data/new.csv\n"
        "```\n\n"
        "`Results:`\n\n"
        "Recorded outputs.\n",
    )
    _write(
        entry / "data.json",
        json.dumps(
            {
                "inputs": [
                    {
                        "fingerprint": _fingerprint(source).as_dict(),
                        "kind": "file",
                        "location": "data/source.csv",
                        "name": "source",
                        "origin": True,
                    }
                ],
                "schema": "research-log-data/v3",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    old_record = OutputSupport(
        True,
        _fingerprint(old_output),
        ScriptSupport("scripts/old.py", _fingerprint(old_script)),
        ("--input-data", "<source>", "--output-data", "data/old.csv"),
        (("source", _fingerprint(source)),),
        (("scripts/helper_old.py", _fingerprint(scripts / "helper_old.py")),),
    )
    outputs: dict[str, OutputSupport] = {"data/old.csv": old_record}
    retirement: str | None = None
    if obsolete:
        obsolete_script = _write(scripts / "obsolete.py", "print('obsolete')\n")
        obsolete_record = OutputSupport(
            True,
            _fingerprint(new_output),
            ScriptSupport("scripts/obsolete.py", _fingerprint(obsolete_script)),
            ("--output-data", "data/new.csv"),
            (),
            (),
        )
        outputs["data/new.csv"] = obsolete_record
        signature = json.dumps(
            {
                "code": {},
                "inputs": {},
                "parameters": ["--output-data", "data/new.csv"],
                "script": obsolete_record.script.as_dict(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        value = json.dumps(
            [entry.relative_to(root).as_posix(), signature],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        retirement = "migration-" + hashlib.sha256(value.encode()).hexdigest()[:12]
    state = PyrunOutputsFile(entry / "pyrun-outputs.json", entry, outputs)
    _write(entry / "pyrun-outputs.json", state.serialized())
    return log, entry, retirement


def _role_repair_fixture(root: Path) -> tuple[Path, Path]:
    log, entry, _ = _fixture(root)
    scripts = entry / "scripts"
    data = entry / "data"
    first_script = _write(scripts / "first.py", "print('first')\n")
    second_script = _write(scripts / "second.py", "print('second')\n")
    shared = _write(data / "shared.csv", "case,value\nshared,1\n")
    first = _write(data / "first.csv", "case,value\nfirst,1\n")
    second = _write(data / "second.csv", "case,value\nsecond,1\n")
    _write(
        entry / "e001.md",
        "# Study\n\n"
        "## Execution\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/first.py --output-shared data/shared.csv "
        "--output-data data/first.csv\n\n"
        "./pyrun --other-inputs output-shared -- scripts/second.py "
        "--output-shared '<shared>' --output-data data/second.csv\n"
        "```\n\n"
        "`Results:`\n\n"
        "Recorded outputs.\n",
    )
    payload = json.loads((entry / "data.json").read_text(encoding="utf-8"))
    payload["inputs"].append(
        {
            "fingerprint": _fingerprint(shared).as_dict(),
            "kind": "file",
            "location": "data/shared.csv",
            "name": "shared",
            "origin": False,
        }
    )
    _write(
        entry / "data.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    first_record = OutputSupport(
        True,
        _fingerprint(first),
        ScriptSupport("scripts/first.py", _fingerprint(first_script)),
        (
            "--output-shared",
            "data/shared.csv",
            "--output-data",
            "data/first.csv",
        ),
        (),
        (),
    )
    second_record = OutputSupport(
        True,
        _fingerprint(second),
        ScriptSupport("scripts/second.py", _fingerprint(second_script)),
        (
            "--output-shared",
            "data/shared.csv",
            "--output-data",
            "data/second.csv",
        ),
        (),
        (),
    )
    shared_record = OutputSupport(
        second_record.confirmed,
        _fingerprint(shared),
        second_record.script,
        second_record.parameters,
        second_record.inputs,
        second_record.code,
    )
    state = PyrunOutputsFile(
        entry / "pyrun-outputs.json",
        entry,
        {
            "data/first.csv": first_record,
            "data/second.csv": second_record,
            "data/shared.csv": shared_record,
        },
    )
    _write(entry / "pyrun-outputs.json", state.serialized())
    return log, entry


class PyrunMigrationTests(unittest.TestCase):
    def test_publication_rejects_changed_observed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)
            plan = build_migration_plan(root)
            legacy = (entry / "pyrun-outputs.json").read_bytes()
            (entry / "data/old.csv").write_text(
                "case,value\nold,changed\n", encoding="utf-8"
            )

            with self.assertRaises(ActionError) as changed:
                migration._publish_plan(plan)

            self.assertEqual(
                changed.exception.code, "pyrun.migration.source_changed"
            )
            self.assertEqual((entry / "pyrun-outputs.json").read_bytes(), legacy)
            self.assertFalse((entry / "pyrun.json").exists())
            self.assertFalse((root / MIGRATION_RECORD).exists())

    def test_publication_failure_restores_exact_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)
            plan = build_migration_plan(root)
            legacy = (entry / "pyrun-outputs.json").read_bytes()
            original = migration.remove_or_write
            failed = False

            def fail_legacy_removal(path: Path, text: str | None) -> None:
                nonlocal failed
                if path.name == "pyrun-outputs.json" and text is None and not failed:
                    failed = True
                    raise OSError("injected publication failure")
                original(path, text)

            with (
                patch.object(
                    migration, "remove_or_write", side_effect=fail_legacy_removal
                ),
                self.assertRaises(ActionError) as publication,
            ):
                migration._publish_plan(plan)

            self.assertEqual(
                publication.exception.code,
                "pyrun.migration.publication_failed",
            )
            self.assertEqual((entry / "pyrun-outputs.json").read_bytes(), legacy)
            self.assertFalse((entry / "pyrun.json").exists())
            self.assertFalse((root / MIGRATION_RECORD).exists())

    def test_role_repair_moves_one_legacy_output_to_a_generated_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry = _role_repair_fixture(root)

            plan = build_migration_plan(root)

            target = next(
                value
                for path, value in plan.updates
                if path == (entry / "pyrun.json").resolve()
            )
            assert target is not None
            payload = json.loads(target)
            executions = tuple(payload["executions"].values())
            self.assertEqual(len(executions), 2)
            second = next(
                item
                for item in executions
                if item["recipe"]["script"] == "scripts/second.py"
            )
            self.assertEqual(second["recipe"]["inputs"], ["shared"])
            self.assertEqual(
                second["recipe"]["outputs"], {"data/second.csv": "file"}
            )
            self.assertFalse(second["confirmed"])
            self.assertEqual(
                plan.record["counts"]["migrated_legacy_groups"], 2
            )

    def test_cli_exposes_write_free_project_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)

            family = run_log(root, "pyrun", "--help")
            preview = run_log(
                root, "pyrun", "migrate", "--root", str(root), "--dry-run"
            )

            self.assertEqual(family.returncode, 0, family.stderr)
            self.assertIn("migrate", family.stdout)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            payload = json.loads(preview.stdout)
            self.assertEqual(payload["task"], "pyrun.migrate")
            self.assertEqual(payload["status"], "dry-run")
            self.assertFalse((entry / "pyrun.json").exists())

    def test_dry_run_builds_complete_state_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)

            result = migrate_project(root, dry_run=True)

            self.assertEqual(result.status, "dry-run")
            self.assertEqual(
                result.records,
                (
                    {
                        "confirmed_executions": 1,
                        "entries": 1,
                        "executions": 2,
                        "legacy_execution_groups": 1,
                        "legacy_files": 1,
                        "legacy_output_records": 1,
                        "migrated_legacy_groups": 1,
                        "retired_legacy_groups": 0,
                        "slow_executions": 1,
                        "unconfirmed_executions": 1,
                    },
                ),
            )
            self.assertTrue((entry / "pyrun-outputs.json").is_file())
            self.assertFalse((entry / "pyrun.json").exists())
            self.assertFalse((root / MIGRATION_RECORD).exists())

    def test_publication_migrates_confirmed_and_current_only_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)

            result = migrate_project(root, dry_run=False)

            self.assertEqual(result.status, "changed")
            self.assertFalse((entry / "pyrun-outputs.json").exists())
            state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=root
            )
            self.assertEqual(len(state.executions), 2)
            old = next(
                value
                for value in state.executions.values()
                if value.recipe.script == "scripts/old.py"
            )
            new = next(
                value
                for value in state.executions.values()
                if value.recipe.script == "scripts/new.py"
            )
            self.assertTrue(old.confirmed)
            self.assertFalse(old.slow)
            self.assertIsNone(old.last_run_at)
            self.assertEqual(
                tuple(name for name, _ in old.observed.code),
                ("scripts/helper_old.py",),
            )
            self.assertFalse(new.confirmed)
            self.assertTrue(new.slow)
            self.assertIsNone(new.last_run_at)
            self.assertEqual(
                tuple(name for name, _ in new.observed.code),
                ("scripts/helper_new.py",),
            )
            record = json.loads((root / MIGRATION_RECORD).read_text())
            self.assertEqual(record["schema"], "research-log-pyrun-migration/1")
            self.assertEqual(record["counts"]["executions"], 2)
            self.assertEqual(len(record["legacy_groups"]), 1)

    def test_unresolved_legacy_group_requires_exact_approved_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, retirement = _fixture(root, obsolete=True)
            assert retirement is not None

            with self.assertRaises(ActionError) as unresolved:
                build_migration_plan(root)
            self.assertEqual(
                unresolved.exception.code, "pyrun.migration.legacy_unresolved"
            )

            plan = build_migration_plan(
                root, approved_retirements=(retirement,)
            )

            counts = plan.record["counts"]
            self.assertEqual(counts["retired_legacy_groups"], 1)
            retired = [
                item
                for item in plan.record["legacy_groups"]
                if item["disposition"] == "retired"
            ]
            self.assertEqual([item["case_id"] for item in retired], [retirement])
            self.assertFalse((entry / "pyrun.json").exists())

    def test_existing_target_refuses_cutover_without_changing_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry, _ = _fixture(root)
            target = _write(entry / "pyrun.json", "sentinel\n")
            before = (entry / "pyrun-outputs.json").read_bytes()

            with self.assertRaises(ActionError) as conflict:
                migrate_project(root, dry_run=False)
            self.assertEqual(conflict.exception.code, "pyrun.migration.target_exists")

            self.assertEqual(target.read_text(), "sentinel\n")
            self.assertEqual((entry / "pyrun-outputs.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
