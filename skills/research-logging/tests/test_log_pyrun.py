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
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_RUNNER,
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


def _execution(recipe: ExecutionRecipe, *, slow: bool) -> PyrunExecution:
    return PyrunExecution(
        False,
        slow,
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
    )


def _write_state(entry: Path, executions: tuple[PyrunExecution, ...]) -> None:
    state = PyrunFile(
        entry / "pyrun.json",
        entry,
        {execution_id(item.recipe): item for item in executions},
    )
    (entry / "pyrun.json").write_text(state.serialized(), encoding="utf-8")


def _recipe(output: str, *, case: str | None = None) -> ExecutionRecipe:
    parameters = (
        ("--output-data", output)
        if case is None
        else ("--case", case, "--output-data", output)
    )
    return ExecutionRecipe(
        "scripts/build.py", parameters, (), (), ((output, "file"),)
    )


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
        self.assertIn("--slow", action.stdout)
        self.assertIn("--no-slow", action.stdout)

    def test_markdown_first_update_changes_only_slow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "./pyrun --slow -- scripts/build.py --output-data data/result.csv",
            )
            recipe = _recipe("data/result.csv")
            initial = _execution(recipe, slow=False)
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
                "--slow",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            after = json.loads((entry / "pyrun.json").read_text())
            self.assertTrue(after["executions"][identity]["slow"])
            before["executions"][identity]["slow"] = True
            self.assertEqual(after, before)
            self.assertEqual(validation.read_bytes(), before_validation)

    def test_static_loop_update_changes_every_distinct_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root,
                "for case in one two; do\n"
                "  ./pyrun --slow -- scripts/build.py --case \"$case\" "
                "--output-data \"data/$case.csv\"\n"
                "done",
            )
            recipes = (
                _recipe("data/one.csv", case="one"),
                _recipe("data/two.csv", case="two"),
            )
            _write_state(entry, tuple(_execution(item, slow=False) for item in recipes))

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
                "--slow",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((entry / "pyrun.json").read_text())["executions"]
            self.assertEqual(set(state), {execution_id(item) for item in recipes})
            self.assertTrue(all(item["slow"] for item in state.values()))

    def test_update_refuses_policy_recipe_and_argument_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, entry = _fixture(
                root, "./pyrun scripts/build.py --output-data data/result.csv"
            )
            recipe = _recipe("data/result.csv")
            _write_state(entry, (_execution(recipe, slow=False),))
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

            wrong_policy = run_log(root, *common, "--slow")
            self.assertEqual(wrong_policy.returncode, 2)
            self.assertIn("pyrun.update.markdown_disagreement", wrong_policy.stderr)

            document = entry / "e001.md"
            document.write_text(
                document.read_text().replace("data/result.csv", "data/other.csv"),
                encoding="utf-8",
            )
            wrong_recipe = run_log(root, *common, "--no-slow")
            self.assertEqual(wrong_recipe.returncode, 2)
            self.assertIn("pyrun.update.command_unresolved", wrong_recipe.stderr)

            invalid = run_log(root, *common)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("cli.arguments.invalid", invalid.stderr)
            self.assertEqual((entry / "pyrun.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
