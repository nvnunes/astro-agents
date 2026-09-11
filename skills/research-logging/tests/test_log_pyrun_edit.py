"""Markdown-first command edits preserve retained evidence and unrelated state."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_log_cli_test_support import run_log
from research_log_data import InputResource, data_file_from_inputs
from test_log_pyrun import _execution, _fingerprint, _fixture, _recipe, _write_state
from validation.pyrun_state import execution_id, load_pyrun_state


class LogPyrunEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base, self.entry = _fixture(
            self.root, "./pyrun scripts/build.py --output-data data/result.csv"
        )
        self.recipe = _recipe("data/result.csv")
        self.old = replace(
            _execution(self.recipe, auto_reproduce=True),
            requires_reproduction=False,
            last_run_at="2030-01-01T00:00:00Z",
        )
        _write_state(self.entry, (self.old,))
        (self.entry / "data/result.csv").write_text("retained\n")
        self.document = self.entry / "e001.md"
        self.original_markdown = self.document.read_text()
        self.state_path = self.entry / "pyrun.json"
        self.original_state = self.state_path.read_bytes()

    def command(self, value: str) -> None:
        self.document.write_text(
            self.original_markdown.replace(
                "./pyrun scripts/build.py --output-data data/result.csv", value
            )
        )

    def run_edit(self, action: str, *arguments: str):
        before = self.document.read_bytes()
        result = run_log(
            self.root,
            "pyrun",
            action,
            "--path",
            str(self.base),
            "--entry",
            "e001",
            "--execution-id",
            execution_id(self.recipe),
            *arguments,
        )
        self.assertEqual(self.document.read_bytes(), before)
        self.assertEqual((self.entry / "data/result.csv").read_text(), "retained\n")
        return result

    def current(self):
        state = load_pyrun_state(
            self.state_path, entry_root=self.entry, project_root=self.root
        )
        self.assertEqual(len(state.executions), 1)
        value = next(iter(state.executions.values()))
        self.assertTrue(value.requires_reproduction)
        self.assertIsNone(value.last_run_at)
        self.assertEqual(value.auto_reproduce, self.old.auto_reproduce)
        self.assertEqual(value.exclusive, self.old.exclusive)
        return value

    def registry(self, name: str, *, origin: bool = True) -> None:
        path = self.entry / f"data/{name}.txt"
        path.write_text(name)
        resource = InputResource(
            name,
            "file",
            f"data/{name}.txt",
            _fingerprint(name.encode()),
            origin,
            str(path),
        )
        data = data_file_from_inputs(
            self.entry / "data.json", entry_root=self.entry, inputs=(resource,)
        )
        (self.entry / "data.json").write_text(data.canonical_json())

    def test_requires_markdown_first_and_refuses_unrelated_changes(self) -> None:
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.command(
            "./pyrun scripts/build.py --output-data data/result.csv --count 2 --extra 9"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)

    def test_set_parameter_dry_run_then_apply_preserves_observations(self) -> None:
        self.command("./pyrun scripts/build.py --output-data data/result.csv --count=2")
        result = self.run_edit(
            "set-parameter", "--parameter", "count", "--value", "2", "--dry-run"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("diff", result.stdout)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.current()
        self.assertEqual(current.observed, self.old.observed)
        self.assertNotEqual(execution_id(current.recipe), execution_id(self.recipe))

    def test_add_registered_input(self) -> None:
        self.registry("config")
        registry_before = (self.entry / "data.json").read_bytes()
        self.command(
            "./pyrun --other-inputs config -- scripts/build.py "
            "--output-data data/result.csv --config '<config>'"
        )
        result = self.run_edit(
            "add-input", "--parameter", "config", "--value", "<config>"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.current()
        self.assertEqual(current.recipe.inputs, ("config",))
        self.assertEqual(
            dict(current.observed.inputs)["config"], _fingerprint(b"config")
        )
        self.assertEqual((self.entry / "data.json").read_bytes(), registry_before)

    def test_add_output_and_refuse_missing_retained_output(self) -> None:
        self.command(
            "./pyrun --other-outputs report -- scripts/build.py "
            "--output-data data/result.csv --report data/report.txt"
        )
        result = self.run_edit(
            "add-output", "--parameter", "report", "--value", "data/report.txt"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        (self.entry / "data/report.txt").write_text("report")
        result = self.run_edit(
            "add-output", "--parameter", "report", "--value", "data/report.txt"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(("data/report.txt", "file"), self.current().recipe.outputs)

    def test_set_script_does_not_execute_it(self) -> None:
        script = self.entry / "scripts/new.py"
        script.write_text("raise RuntimeError('must not execute')\n")
        self.command("./pyrun scripts/new.py --output-data data/result.csv")
        result = self.run_edit("set-script", "--script", "scripts/new.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.current()
        self.assertEqual(current.recipe.script, "scripts/new.py")
        self.assertEqual(current.observed.script, _fingerprint(script.read_bytes()))
        self.assertEqual(current.observed.code, ())

    def test_remove_parameter_drops_material_association_only(self) -> None:
        self.registry("config")
        self.recipe = replace(
            self.recipe,
            parameters=(*self.recipe.parameters, "--config", "<config>"),
            inputs=("config",),
        )
        self.old = replace(
            self.old,
            recipe=self.recipe,
            observed=replace(
                self.old.observed, inputs=(("config", _fingerprint(b"config")),)
            ),
        )
        _write_state(self.entry, (self.old,))
        result = self.run_edit("remove-parameter", "--parameter", "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.inputs, ())
        self.assertTrue((self.entry / "data/config.txt").exists())

    def test_set_role_for_existing_parameter(self) -> None:
        self.registry("config")
        self.recipe = replace(
            self.recipe, parameters=(*self.recipe.parameters, "--config", "<config>")
        )
        self.old = replace(self.old, recipe=self.recipe)
        _write_state(self.entry, (self.old,))
        self.command(
            "./pyrun --other-inputs config -- scripts/build.py "
            "--output-data data/result.csv --config '<config>'"
        )
        result = self.run_edit("set-role", "--parameter", "config", "--role", "input")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.inputs, ("config",))

    def test_ordinary_role_removes_input_without_changing_value(self) -> None:
        self.registry("config")
        self.recipe = replace(
            self.recipe,
            parameters=(*self.recipe.parameters, "--input-label", "<config>"),
            inputs=("config",),
        )
        self.old = replace(
            self.old,
            recipe=self.recipe,
            observed=replace(
                self.old.observed, inputs=(("config", _fingerprint(b"config")),)
            ),
        )
        _write_state(self.entry, (self.old,))
        self.command(
            "./pyrun --other-parameters input-label -- scripts/build.py "
            "--output-data data/result.csv --input-label '<config>'"
        )
        result = self.run_edit(
            "set-role", "--parameter", "input-label", "--role", "ordinary"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.inputs, ())

    def test_repeated_parameter_needs_occurrence(self) -> None:
        self.recipe = replace(
            self.recipe,
            parameters=(*self.recipe.parameters, "--count", "1", "--count", "2"),
        )
        self.old = replace(self.old, recipe=self.recipe)
        _write_state(self.entry, (self.old,))
        self.command(
            "./pyrun scripts/build.py --output-data data/result.csv --count 1 --count 3"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "3")
        self.assertEqual(result.returncode, 2)
        result = self.run_edit(
            "set-parameter", "--parameter", "count", "--occurrence", "2", "--value", "3"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.parameters[-2:], ("--count", "3"))

    def test_duplicate_markdown_commands_are_ambiguous(self) -> None:
        command = "./pyrun scripts/build.py --output-data data/result.csv --count 2"
        self.command(command + "\n" + command)
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)

    def test_add_named_output(self) -> None:
        self.registry("report", origin=False)
        self.command(
            "./pyrun --other-outputs report -- scripts/build.py "
            "--output-data data/result.csv --report '<report>'"
        )
        result = self.run_edit(
            "add-output", "--parameter", "report", "--value", "<report>"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(("data/report.txt", "file"), self.current().recipe.outputs)

    def test_positional_input_and_negative_parameter(self) -> None:
        self.registry("config")
        self.command(
            "./pyrun --other-inputs @1 -- scripts/build.py "
            "--output-data data/result.csv '<config>'"
        )
        result = self.run_edit("add-input", "--position", "1", "--value", "<config>")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.inputs, ("config",))
        _write_state(self.entry, (self.old,))
        self.command(
            "./pyrun scripts/build.py --output-data data/result.csv --count=-1"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value=-1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_input_fingerprint_mismatch_is_not_reconstructed(self) -> None:
        self.registry("config")
        (self.entry / "data/config.txt").write_text("changed")
        self.command(
            "./pyrun --other-inputs config -- scripts/build.py "
            "--output-data data/result.csv --config '<config>'"
        )
        result = self.run_edit(
            "add-input", "--parameter", "config", "--value", "<config>"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)

    def test_policy_changes_are_not_applied_with_recipe_changes(self) -> None:
        self.command(
            "./pyrun --exclusive -- scripts/build.py "
            "--output-data data/result.csv --count 2"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        self.old = replace(self.old, exclusive=True)
        _write_state(self.entry, (self.old,))
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.current().exclusive)

    def test_unrelated_material_role_change_is_rejected(self) -> None:
        self.recipe = replace(
            self.recipe,
            parameters=(*self.recipe.parameters, "--report", "data/report.txt"),
        )
        self.old = replace(self.old, recipe=self.recipe)
        _write_state(self.entry, (self.old,))
        before = self.state_path.read_bytes()
        (self.entry / "data/report.txt").write_text("report")
        self.command(
            "./pyrun --other-outputs report -- scripts/build.py "
            "--output-data data/result.csv --report data/report.txt --count 2"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_value_change_cannot_also_add_a_material_role(self) -> None:
        self.registry("config")
        self.recipe = replace(
            self.recipe, parameters=(*self.recipe.parameters, "--config", "old")
        )
        self.old = replace(self.old, recipe=self.recipe)
        _write_state(self.entry, (self.old,))
        before = self.state_path.read_bytes()
        self.command(
            "./pyrun --other-inputs config -- scripts/build.py "
            "--output-data data/result.csv --config '<config>'"
        )
        result = self.run_edit(
            "set-parameter", "--parameter", "config", "--value", "<config>"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_add_input_corrects_existing_parameter_value_and_role(self) -> None:
        self.registry("config")
        self.recipe = replace(
            self.recipe,
            parameters=(*self.recipe.parameters, "--config", "data/config.txt"),
        )
        self.old = replace(self.old, recipe=self.recipe)
        _write_state(self.entry, (self.old,))
        self.command(
            "./pyrun --other-inputs config -- scripts/build.py "
            "--output-data data/result.csv --config '<config>'"
        )
        result = self.run_edit(
            "add-input", "--parameter", "config", "--value", "<config>"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.current().recipe.inputs, ("config",))

    def test_outputless_command_does_not_block_an_unrelated_edit(self) -> None:
        self.command(
            "./pyrun scripts/build.py\n"
            "./pyrun scripts/build.py --output-data data/result.csv --count 2"
        )
        result = self.run_edit("set-parameter", "--parameter", "count", "--value", "2")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_write_preserves_state(self) -> None:
        self.command("./pyrun scripts/build.py --output-data data/result.csv --count 2")
        with patch(
            "log_commands.pyrun_edit.atomic_write_text", side_effect=OSError("fixture")
        ):
            result = self.run_edit(
                "set-parameter", "--parameter", "count", "--value", "2"
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), self.original_state)


if __name__ == "__main__":
    unittest.main()
