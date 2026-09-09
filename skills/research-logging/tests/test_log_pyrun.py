from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log
from research_log_data import Fingerprint
from validation.pyrun_state import (
    LEGACY_PYRUN_SCHEMA,
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_RUNNER,
    PYRUN_SCHEMA,
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    execution_id,
)


def _fingerprint(value: bytes) -> Fingerprint:
    return Fingerprint("sha256", digest=hashlib.sha256(value).hexdigest())


def _fixture(root: Path, body: str) -> tuple[Path, Path]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    base = root / "docs/study"
    entry = base / "entries/2030-01-01-e001-test"
    (entry / "scripts").mkdir(parents=True)
    (entry / "data").mkdir()
    (root / "docs/study.md").write_text(
        "# Study\n\n"
        "Validation: [latest completed report](study/validation.md)\n\n"
        "Reproduction: [latest report](study/reproduction.md)\n\n"
        "## Summary\n\n"
        "## Entries\n\n"
        "- `2030-01-01` [Test](study/entries/2030-01-01-e001-test/e001.md)\n",
        encoding="utf-8",
    )
    (entry / "e001.md").write_text(
        "# Test\n\n## Execution\n\n`Steps:`\n\n```bash\n"
        + body
        + "\n```\n\n`Results:`\n\nRecorded output.\n",
        encoding="utf-8",
    )
    (entry / "scripts/build.py").write_text("print('fixture')\n", encoding="utf-8")
    return base, entry


def _execution(
    recipe: ExecutionRecipe, *, auto_reproduce: bool, exclusive: bool = False
) -> PyrunExecution:
    return PyrunExecution(
        False,
        auto_reproduce,
        None,
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        ObservedExecution(
            _fingerprint(b"script"),
            (),
            (),
            tuple((name, _fingerprint(name.encode())) for name, _ in recipe.outputs),
        ),
        exclusive,
    )


def _write_state(
    entry: Path,
    executions: tuple[PyrunExecution, ...],
    *,
    schema: str | None = None,
) -> None:
    state = PyrunFile(
        entry / "pyrun.json",
        entry,
        {execution_id(item.recipe): item for item in executions},
        schema or "research-log-pyrun/v3",
    )
    (entry / "pyrun.json").write_text(state.serialized(), encoding="utf-8")


def _recipe(output: str, *, case: str | None = None) -> ExecutionRecipe:
    parameters = (
        ("--output-data", output)
        if case is None
        else ("--case", case, "--output-data", output)
    )
    return ExecutionRecipe("scripts/build.py", parameters, (), (), ((output, "file"),))


class LogPyrunPolicyTests(unittest.TestCase):
    def test_help_exposes_only_the_narrow_update_surface(self) -> None:
        top = run_log(Path.cwd(), "--help")
        family = run_log(Path.cwd(), "pyrun", "--help")
        action = run_log(Path.cwd(), "pyrun", "update", "--help")

        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("pyrun", top.stdout)
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertIn("update", family.stdout)
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("--execution-id", action.stdout)
        self.assertIn("--auto-reproduce", action.stdout)
        self.assertIn("--exclusive", action.stdout)
        self.assertNotIn("--slow", action.stdout)

    def test_markdown_first_update_changes_only_auto_reproduce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "./pyrun --auto-reproduce=false -- scripts/build.py "
                "--output-data data/result.csv",
            )
            recipe = _recipe("data/result.csv")
            initial = _execution(recipe, auto_reproduce=True)
            _write_state(entry, (initial,))
            validation = base / "validation/results.json"
            validation.parent.mkdir()
            validation.write_text('{"sentinel":true}\n', encoding="utf-8")
            before_validation = validation.read_bytes()
            before = json.loads((entry / "pyrun.json").read_text())
            identity = execution_id(recipe)

            result = run_log(
                root,
                "pyrun",
                "update",
                "--path",
                str(base),
                "--entry",
                "e001",
                "--execution-id",
                identity,
                "--auto-reproduce",
                "false",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            after = json.loads((entry / "pyrun.json").read_text())
            self.assertFalse(after["executions"][identity]["auto_reproduce"])
            before["executions"][identity]["auto_reproduce"] = False
            self.assertEqual(after, before)
            self.assertEqual(validation.read_bytes(), before_validation)

    def test_markdown_first_update_changes_only_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "./pyrun --exclusive -- scripts/build.py --output-data data/result.csv",
            )
            recipe = _recipe("data/result.csv")
            _write_state(entry, (_execution(recipe, auto_reproduce=True),))
            identity = execution_id(recipe)

            result = run_log(
                root,
                "pyrun",
                "update",
                "--path",
                str(base),
                "--entry",
                "e001",
                "--execution-id",
                identity,
                "--exclusive",
                "true",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((entry / "pyrun.json").read_text())
            self.assertTrue(state["executions"][identity]["exclusive"])
            self.assertEqual(state["schema"], "research-log-pyrun/v3")

    def test_migration_converts_v2_from_current_markdown_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "./pyrun --exclusive -- scripts/build.py --output-data data/result.csv",
            )
            recipe = _recipe("data/result.csv")
            _write_state(
                entry,
                (_execution(recipe, auto_reproduce=True),),
                schema=LEGACY_PYRUN_SCHEMA,
            )
            before = (entry / "pyrun.json").read_bytes()

            preview = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--dry-run",
                "--format",
                "json",
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual((entry / "pyrun.json").read_bytes(), before)
            preview_record = json.loads(preview.stdout)
            self.assertEqual(preview_record["status"], "ready")
            self.assertEqual(preview_record["executions"][0]["line"], 7)

            migrated = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--format",
                "json",
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            state = json.loads((entry / "pyrun.json").read_text())
            self.assertEqual(state["schema"], "research-log-pyrun/v3")
            self.assertTrue(state["executions"][execution_id(recipe)]["exclusive"])
            self.assertFalse(
                (
                    base / ".cache/research-log-operations/pyrun-exclusivity-migration"
                ).exists()
            )

            repeated = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--format",
                "json",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            repeated_record = json.loads(repeated.stdout)
            self.assertEqual(repeated_record["status"], "complete")
            self.assertFalse(repeated_record["changed"])

    def test_migration_refusals_keep_the_migration_result_contract(self) -> None:
        scenarios = (
            ("state_missing", None, None),
            (
                "orphan",
                "./pyrun -- scripts/build.py --output-data data/other.csv",
                _recipe("data/result.csv"),
            ),
            (
                "ambiguity",
                "./pyrun -- scripts/build.py --output-data data/result.csv\n"
                "./pyrun -- scripts/build.py --output-data data/result.csv",
                _recipe("data/result.csv"),
            ),
            (
                "policy_disagreement",
                "./pyrun --auto-reproduce=false -- scripts/build.py "
                "--output-data data/result.csv",
                _recipe("data/result.csv"),
            ),
        )
        for name, markdown, recipe in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = markdown or (
                    "./pyrun -- scripts/build.py --output-data data/result.csv"
                )
                base, entry = _fixture(root, command)
                if recipe is not None:
                    _write_state(
                        entry,
                        (_execution(recipe, auto_reproduce=True),),
                        schema=LEGACY_PYRUN_SCHEMA,
                    )

                result = run_log(
                    root,
                    "pyrun",
                    "migrate-exclusivity",
                    "--path",
                    str(base),
                    "--dry-run",
                    "--format",
                    "json",
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                record = json.loads(result.stdout)
                self.assertEqual(record["status"], "refused")
                self.assertEqual(
                    set(record),
                    {
                        "changed",
                        "diagnostics",
                        "entries",
                        "executions",
                        "schema",
                        "status",
                        "summary",
                        "totals",
                    },
                )
                self.assertFalse(record["changed"])
                self.assertEqual(len(record["diagnostics"]), 1)

    def test_migration_recovers_prepared_transaction_and_blocks_other_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "./pyrun --exclusive -- scripts/build.py --output-data data/result.csv",
            )
            recipe = _recipe("data/result.csv")
            legacy_execution = _execution(recipe, auto_reproduce=True)
            _write_state(
                entry,
                (legacy_execution,),
                schema=LEGACY_PYRUN_SCHEMA,
            )
            target = entry / "pyrun.json"
            original_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            transaction_root = (
                base / ".cache/research-log-operations/pyrun-exclusivity-migration"
            )
            transaction_root.mkdir(parents=True)
            staged = transaction_root / f"000000-{entry.name}.json"
            migrated_execution = _execution(recipe, auto_reproduce=True, exclusive=True)
            staged.write_text(
                PyrunFile(
                    target,
                    entry,
                    {execution_id(recipe): migrated_execution},
                    PYRUN_SCHEMA,
                ).serialized(),
                encoding="utf-8",
            )
            journal = {
                "schema": "research-log-pyrun-exclusivity-migration-transaction/1",
                "transaction_id": "pyrun-exclusivity-0123456789abcdef01234567",
                "state": "prepared",
                "created_at": "2030-01-01T00:00:00Z",
                "entries": [
                    {
                        "entry": "e001",
                        "target": target.relative_to(root).as_posix(),
                        "original_digest": original_digest,
                        "staged": staged.name,
                        "staged_digest": hashlib.sha256(
                            staged.read_bytes()
                        ).hexdigest(),
                        "published": False,
                    }
                ],
            }
            (transaction_root / "transaction.json").write_text(
                json.dumps(journal, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            staged.write_text(
                PyrunFile(
                    target,
                    entry,
                    {
                        execution_id(recipe): _execution(
                            recipe, auto_reproduce=True, exclusive=False
                        )
                    },
                    PYRUN_SCHEMA,
                ).serialized(),
                encoding="utf-8",
            )
            journal["entries"][0]["staged_digest"] = hashlib.sha256(
                staged.read_bytes()
            ).hexdigest()
            (transaction_root / "transaction.json").write_text(
                json.dumps(journal, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            refused_policy = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--format",
                "json",
            )
            self.assertEqual(refused_policy.returncode, 2)
            self.assertIn("does not match authored policy", refused_policy.stdout)

            staged.write_text(
                PyrunFile(
                    target,
                    entry,
                    {execution_id(recipe): migrated_execution},
                    PYRUN_SCHEMA,
                ).serialized(),
                encoding="utf-8",
            )
            journal["entries"][0]["staged_digest"] = hashlib.sha256(
                staged.read_bytes()
            ).hexdigest()
            journal["entries"][0]["target"] = "unrelated.json"
            (transaction_root / "transaction.json").write_text(
                json.dumps(journal, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            refused_target = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--format",
                "json",
            )
            self.assertEqual(refused_target.returncode, 2)
            self.assertIn("paths are not canonical", refused_target.stdout)

            journal["entries"][0]["target"] = target.relative_to(root).as_posix()
            (transaction_root / "transaction.json").write_text(
                json.dumps(journal, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            blocked = run_log(
                root,
                "pyrun",
                "update",
                "--path",
                str(base),
                "--entry",
                "e001",
                "--execution-id",
                execution_id(recipe),
                "--exclusive",
                "true",
            )
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("pyrun-exclusivity-0123456789abcdef01234567", blocked.stderr)

            recovered = run_log(
                root,
                "pyrun",
                "migrate-exclusivity",
                "--path",
                str(base),
                "--format",
                "json",
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse(transaction_root.exists())
            self.assertEqual(json.loads(target.read_text())["schema"], PYRUN_SCHEMA)

    def test_static_loop_update_changes_every_distinct_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "for case in one two; do\n"
                "  ./pyrun --auto-reproduce=false -- scripts/build.py "
                '--case "$case" '
                '--output-data "data/$case.csv"\n'
                "done",
            )
            recipes = (
                _recipe("data/one.csv", case="one"),
                _recipe("data/two.csv", case="two"),
            )
            _write_state(
                entry,
                tuple(_execution(item, auto_reproduce=True) for item in recipes),
            )

            result = run_log(
                root,
                "pyrun",
                "update",
                "--path",
                str(base),
                "--entry",
                "e001",
                "--execution-id",
                execution_id(recipes[0]),
                "--auto-reproduce",
                "false",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((entry / "pyrun.json").read_text())["executions"]
            self.assertEqual(set(state), {execution_id(item) for item in recipes})
            self.assertTrue(all(not item["auto_reproduce"] for item in state.values()))

    def test_update_refuses_policy_recipe_and_argument_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root, "./pyrun scripts/build.py --output-data data/result.csv"
            )
            recipe = _recipe("data/result.csv")
            _write_state(entry, (_execution(recipe, auto_reproduce=True),))
            identity = execution_id(recipe)
            before = (entry / "pyrun.json").read_bytes()
            common = (
                "pyrun",
                "update",
                "--path",
                str(base),
                "--entry",
                "e001",
                "--execution-id",
                identity,
            )

            wrong_policy = run_log(root, *common, "--auto-reproduce", "false")
            self.assertEqual(wrong_policy.returncode, 2)
            self.assertIn("pyrun.update.markdown_disagreement", wrong_policy.stderr)

            document = entry / "e001.md"
            document.write_text(
                document.read_text().replace("data/result.csv", "data/other.csv"),
                encoding="utf-8",
            )
            wrong_recipe = run_log(root, *common, "--auto-reproduce", "true")
            self.assertEqual(wrong_recipe.returncode, 2)
            self.assertIn("pyrun.update.command_unresolved", wrong_recipe.stderr)

            invalid = run_log(root, *common)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("cli.arguments.invalid", invalid.stderr)
            self.assertEqual((entry / "pyrun.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
