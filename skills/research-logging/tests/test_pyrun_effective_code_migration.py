from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import migrate_pyrun_effective_code as migration
from reproduction_planning_test_support import _Fixture
from validation.pyrun_state import ExecutionRecipe, execution_id


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _v6_registry(root: Path, *, unsupported: bool = False) -> tuple[Path, dict]:
    fixture = _Fixture(root)
    entry = fixture.entry(1)
    source = entry.root / "data/source.txt"
    output = entry.root / "data/output.txt"
    source.write_text("source\n", encoding="utf-8")
    output.write_text("output\n", encoding="utf-8")
    identity, execution = fixture.execution(
        entry,
        "build",
        {"source": source},
        {"output": output},
        auto_reproduce=False,
        requires_reproduction=False,
        last_run_at="2030-01-02T03:04:05Z",
    )
    script = entry.root / execution.recipe.script
    if unsupported:
        script.write_text(
            "import importlib\nimportlib.import_module('helper')\n",
            encoding="utf-8",
        )
    fixture.write_pyrun(entry, [(identity, execution)])
    path = entry.root / "pyrun.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema"] = migration.SOURCE_SCHEMA
    record = value["commands"]["build"]["executions"][identity]
    record["observed"]["code"] = {
        "scripts/old_helper.py": {"algorithm": "sha256", "digest": "a" * 64}
    }
    del record["observed"]["effective_code"]
    path.write_text(_canonical(value), encoding="utf-8")
    return path, value


def _preserved_execution(value: dict) -> dict:
    record = json.loads(json.dumps(value))
    record.pop("requires_reproduction")
    observed = record["observed"]
    observed.pop("code", None)
    observed.pop("effective_code", None)
    return record


class PyrunEffectiveCodeMigrationTests(unittest.TestCase):
    def test_dry_run_apply_preservation_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, before = _v6_registry(root)
            before_bytes = path.read_bytes()
            before_execution = next(
                iter(before["commands"]["build"]["executions"].values())
            )

            preview = migration.migrate_project(root, apply=False)

            self.assertEqual(
                (
                    preview.registries,
                    preview.migrated_registries,
                    preview.executions,
                    preview.supported,
                    preview.unsupported,
                ),
                (1, 1, 1, 1, 0),
            )
            self.assertEqual(path.read_bytes(), before_bytes)

            applied = migration.migrate_project(root, apply=True)

            self.assertTrue(applied.changed)
            after = json.loads(path.read_text(encoding="utf-8"))
            after_execution = next(
                iter(after["commands"]["build"]["executions"].values())
            )
            self.assertEqual(after["schema"], "research-log-pyrun/v7")
            self.assertTrue(after_execution["requires_reproduction"])
            self.assertEqual(
                after_execution["observed"]["effective_code"]["algorithm"],
                "python-effective-code-sha256-v1",
            )
            self.assertNotIn("code", after_execution["observed"])
            self.assertEqual(
                _preserved_execution(after_execution),
                _preserved_execution(before_execution),
            )

            repeated = migration.migrate_project(root, apply=False)

            self.assertFalse(repeated.changed)
            self.assertEqual(repeated.migrated_registries, 0)
            self.assertEqual(repeated.current_registries, 1)

    def test_unsupported_and_unavailable_sources_migrate_to_null(self) -> None:
        for case in ("unsupported", "unavailable"):
            with tempfile.TemporaryDirectory() as directory, self.subTest(case=case):
                root = Path(directory)
                path, _ = _v6_registry(root, unsupported=case == "unsupported")
                if case == "unavailable":
                    next(path.parent.glob("scripts/*.py")).unlink()

                report = migration.migrate_project(root, apply=True)

                execution = next(
                    iter(
                        json.loads(path.read_text(encoding="utf-8"))["commands"][
                            "build"
                        ]["executions"].values()
                    )
                )
                self.assertIsNone(execution["observed"]["effective_code"])
                self.assertEqual(report.unsupported, 1)
                self.assertTrue(report.warnings)
                self.assertEqual(report.warning_count, 1)
                self.assertFalse(report.warnings_truncated)

    def test_refresh_current_v7_recomputes_only_effective_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = _v6_registry(root)
            migration.migrate_project(root, apply=True)
            before = json.loads(path.read_text(encoding="utf-8"))
            before_execution = next(
                iter(before["commands"]["build"]["executions"].values())
            )
            script = path.parent / before_execution["recipe"]["script"]
            script.write_text("VALUE = 2\nprint(VALUE)\n", encoding="utf-8")

            preview = migration.migrate_project(
                root, apply=False, refresh_current=True
            )

            self.assertTrue(preview.changed)
            self.assertEqual(preview.migrated_registries, 1)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                before,
            )

            migration.migrate_project(root, apply=True, refresh_current=True)
            after = json.loads(path.read_text(encoding="utf-8"))
            after_execution = next(
                iter(after["commands"]["build"]["executions"].values())
            )
            self.assertTrue(after_execution["requires_reproduction"])
            self.assertNotEqual(
                after_execution["observed"]["effective_code"],
                before_execution["observed"]["effective_code"],
            )
            without_effective_before = json.loads(json.dumps(before_execution))
            without_effective_after = json.loads(json.dumps(after_execution))
            without_effective_before["observed"].pop("effective_code")
            without_effective_after["observed"].pop("effective_code")
            self.assertEqual(without_effective_after, without_effective_before)
            self.assertFalse(
                migration.migrate_project(
                    root, apply=False, refresh_current=True
                ).changed
            )

    def test_analysis_is_cached_and_report_warnings_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = _v6_registry(root, unsupported=True)
            first = value["commands"]["build"]
            duplicate = json.loads(json.dumps(first))
            old_identity, record = next(iter(duplicate["executions"].items()))
            recipe = record["recipe"]
            recipe["parameters"][-1] = "data/output2.txt"
            recipe["outputs"] = {"data/output2.txt": "file"}
            record["observed"]["outputs"] = {
                "data/output2.txt": {"algorithm": "sha256", "digest": "b" * 64}
            }
            (path.parent / "data/output2.txt").write_text(
                "output2\n", encoding="utf-8"
            )
            new_identity = execution_id(
                ExecutionRecipe(
                    recipe["script"],
                    tuple(recipe["parameters"]),
                    tuple(recipe["environment"].items()),
                    tuple(recipe["inputs"]),
                    tuple(recipe["outputs"].items()),
                    tuple(recipe["parameter_roles"].items()),
                )
            )
            duplicate["executions"][new_identity] = duplicate["executions"].pop(
                old_identity
            )
            value["commands"]["duplicate"] = duplicate
            path.write_text(_canonical(value), encoding="utf-8")
            original = migration.analyze_effective_code

            with (
                mock.patch.object(
                    migration,
                    "MAX_REPORT_WARNINGS",
                    1,
                ),
                mock.patch.object(
                    migration,
                    "analyze_effective_code",
                    wraps=original,
                ) as analyze,
            ):
                report = migration.migrate_project(root, apply=False)

            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(report.executions, 2)
            self.assertEqual(report.warning_count, 2)
            self.assertEqual(len(report.warnings), 1)
            self.assertTrue(report.warnings_truncated)

    def test_analysis_and_publication_failures_preserve_source_bytes(self) -> None:
        for case in (
            "syntax",
            "internal_analysis",
            "validation",
            "write",
            "sync",
            "replacement",
        ):
            with tempfile.TemporaryDirectory() as directory, self.subTest(case=case):
                root = Path(directory)
                path, _ = _v6_registry(root)
                if case == "syntax":
                    next(path.parent.glob("scripts/*.py")).write_text(
                        "if:\n", encoding="utf-8"
                    )
                    context = self.assertRaisesRegex(
                        migration.MigrationError, "syntax_invalid"
                    )
                    patch = mock.patch.object(
                        migration,
                        "atomic_replace_text",
                        wraps=migration.atomic_replace_text,
                    )
                elif case == "internal_analysis":
                    context = self.assertRaisesRegex(
                        migration.MigrationError, "failed internally"
                    )
                    patch = mock.patch.object(
                        migration,
                        "analyze_effective_code",
                        side_effect=RuntimeError("injected analyzer failure"),
                    )
                elif case == "validation":
                    context = self.assertRaisesRegex(
                        migration.MigrationError, "injected validation failure"
                    )
                    patch = mock.patch.object(
                        migration,
                        "parse_pyrun_state_text",
                        side_effect=migration.MigrationError(
                            "injected validation failure"
                        ),
                    )
                else:
                    context = self.assertRaisesRegex(
                        migration.MigrationError, "could not publish"
                    )
                    patch = mock.patch.object(
                        migration,
                        "atomic_replace_text",
                        side_effect=OSError(f"injected {case} failure"),
                    )
                before = path.read_bytes()
                with patch, context:
                    migration.migrate_project(root, apply=True)
                self.assertEqual(path.read_bytes(), before)

    def test_read_failure_preserves_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = _v6_registry(root)
            before = path.read_bytes()

            with (
                mock.patch.object(
                    migration,
                    "_read_registry",
                    side_effect=migration.MigrationError("injected read failure"),
                ),
                self.assertRaisesRegex(migration.MigrationError, "read failure"),
            ):
                migration.migrate_project(root, apply=True)

            self.assertEqual(path.read_bytes(), before)

    def test_invalid_v6_code_map_is_rejected_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = _v6_registry(root)
            execution = next(
                iter(value["commands"]["build"]["executions"].values())
            )
            execution["observed"]["code"]["scripts/old_helper.py"]["digest"] = "x"
            path.write_text(_canonical(value), encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaisesRegex(migration.MigrationError, "fingerprint"):
                migration.migrate_project(root, apply=True)

            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
