from __future__ import annotations

import importlib
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")


def _context(root: Path, data_index: dict[str, str] | None = None) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    return COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="docs/log/entries/entry/e001.md",
        entry_root=entry_root,
        log_root=root / "docs" / "log",
        project_root=root,
        data_index=data_index or {},
        require_experimental_context=False,
    )


class CommandV2RoleTests(unittest.TestCase):
    def test_option_role_vocabulary_is_closed_and_unambiguous(self) -> None:
        accepted = {
            "input": "input",
            "input-data": "input",
            "data_input": "input",
            "output-summary-csv": "output",
            "summary_csv_output": "output",
        }
        for name, role in accepted.items():
            with self.subTest(name=name):
                self.assertEqual(COMMAND.automatic_option_role(name), role)
        for name in ("dataset", "results", "summary-output-csv", "input-output"):
            with self.subTest(name=name):
                self.assertIsNone(COMMAND.automatic_option_role(name))

    def test_annotations_target_only_commands_that_need_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root, {"catalog": "https://example.test/catalog.csv"})
            write(context.entry_root / "scripts" / "evaluate.py", "# fixture\n")
            write(context.entry_root / "scripts" / "simulate_trials.py", "# fixture\n")
            text = """```bash
./pyrun scripts/evaluate.py --catalog '<catalog>' --results data/model.csv
./pyrun scripts/simulate_trials.py --input-data data/model.csv \
  --output-data data/trials.npz
```
<!-- command type = model; results = output -->
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 2)
            model, simulation = result.invocations
            self.assertEqual(model.command_type, "model")
            self.assertEqual(simulation.command_type, "simulation")
            self.assertTrue(model.inputs[0].external)
            self.assertTrue(model.outputs[0].path.endswith("data/model.csv"))
            self.assertTrue(simulation.inputs[0].path.endswith("data/model.csv"))
            self.assertTrue(simulation.outputs[0].path.endswith("data/trials.npz"))

    def test_annotation_ordinal_and_positional_target_are_one_based(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --flag
tool summary.csv final.csv
```
<!-- command-2 @1 = input; @2 = output -->
"""

            result = COMMAND.discover_commands(text, context)

            second = result.invocations[1]
            self.assertEqual(len(result.invocations[0].inputs), 0)
            self.assertTrue(second.inputs[0].path.endswith("summary.csv"))
            self.assertTrue(second.outputs[0].path.endswith("final.csv"))

    def test_annotation_override_and_embedded_path_failure_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --output-data source.csv
```
<!-- command output-data = input -->
"""
            result = COMMAND.discover_commands(text, context)
            self.assertEqual(len(result.invocations[0].inputs), 1)
            self.assertFalse(result.invocations[0].outputs)

            embedded = """```bash
tool --output-data label=data/result.csv
```
"""
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "invocation.path_value.embedded"
            ):
                COMMAND.discover_commands(embedded, context)

    def test_data_index_loader_requires_one_exact_unique_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data.csv"
            write(
                path,
                "name,type,location\n"
                "catalog,csv,https://example.test/catalog.csv\n"
                "local,csv,../prior/data.csv\n",
            )

            self.assertEqual(
                COMMAND.load_data_index(path),
                {
                    "catalog": "https://example.test/catalog.csv",
                    "local": "../prior/data.csv",
                },
            )

            write(
                path,
                "name,type,location\n"
                "catalog,csv,https://example.test/one.csv\n"
                "catalog,csv,https://example.test/two.csv\n",
            )
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "data_index.connection.missing"
            ):
                COMMAND.load_data_index(path)

            write(path, "name,location\ncatalog,https://example.test/catalog.csv\n")
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "data_index.connection.missing"
            ):
                COMMAND.load_data_index(path)

    def test_simulation_filename_convention_is_exact_and_overridable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            for script in (
                "simulate.py",
                "simulation_trials.py",
                "sim_trials.py",
                "model_trials.py",
            ):
                write(context.entry_root / "scripts" / script, "# fixture\n")
            text = """```bash
./pyrun scripts/simulate.py
./pyrun scripts/simulation_trials.py
./pyrun scripts/sim_trials.py
./pyrun scripts/model_trials.py
```
<!-- command-2 type = model -->
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(
                [invocation.command_type for invocation in result.invocations],
                ["simulation", "model", None, None],
            )

    def test_script_contents_never_establish_material_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            script = context.entry_root / "scripts" / "analyze.py"
            write(script, "open('data/result.csv', 'w').write('result')\n")
            text = """```bash
./pyrun scripts/analyze.py --results data/result.csv
```
"""

            first = COMMAND.discover_commands(text, context).invocations[0]
            write(script, "raise RuntimeError('contents changed')\n")
            second = COMMAND.discover_commands(text, context).invocations[0]

            self.assertFalse(first.outputs)
            self.assertTrue(first.candidates[0].endswith("data/result.csv"))
            self.assertEqual(first.identity, second.identity)
            self.assertNotEqual(first.script_identity, second.script_identity)

    def test_explicit_project_executable_is_a_currentness_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            executable = context.entry_root / "tools" / "analyze"
            write(executable, "#!/bin/sh\n")

            invocation = COMMAND.discover_commands(
                "```bash\n./tools/analyze --output-data data/result.csv\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(invocation.script, executable.resolve().as_posix())
            self.assertTrue(invocation.script_identity)


class CommandV2ShellTests(unittest.TestCase):
    def test_only_complete_experimental_sections_are_eligible_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = replace(
                _context(Path(directory)), require_experimental_context=True
            )
            text = """## Synthesis

```bash
tool --output-data data/ignored.csv
```

## Experiment

`Steps:`

```bash
tool --output-data data/kept.csv
```

`Results:`

Done.
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 1)
            self.assertTrue(result.invocations[0].outputs[0].path.endswith("kept.csv"))

    def test_environment_continuation_redirection_and_terminal_tee(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
MODE=test tool --input-data data/source.csv \\
  --result data/result.csv < data/config.txt > data/run.log
tool --input-data data/source.csv | tee data/capture.log
```
<!-- command result = output -->
"""

            result = COMMAND.discover_commands(text, context)

            first, second = result.invocations
            self.assertEqual(len(first.inputs), 2)
            self.assertEqual(len(first.outputs), 2)
            self.assertEqual(second.outputs[0].proof, "shell")
            self.assertTrue(second.outputs[0].path.endswith("data/capture.log"))

    def test_physical_continuation_and_file_descriptor_redirection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = (
                "```bash\n"
                "tool --input-data data/source.csv \\\n"
                "  --output-data data/result.csv 2> data/stderr.log\n"
                "```\n"
            )

            invocation = COMMAND.discover_commands(text, context).invocations[0]

            self.assertEqual(len(invocation.inputs), 1)
            self.assertEqual(len(invocation.outputs), 2)
            self.assertTrue(
                any(
                    item.path.endswith("data/stderr.log") for item in invocation.outputs
                )
            )

    def test_command_text_bound_applies_to_each_invocation_not_the_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            argument = "x" * (COMMAND.MAX_COMMAND_BYTES // 2)
            text = f"```bash\ntool {argument}\ntool {argument}\n```\n"

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 2)

            oversized = "x" * COMMAND.MAX_COMMAND_BYTES
            text = f"```bash\ntool {oversized}\n```\n"
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "provenance.resource.too_large"
            ):
                COMMAND.discover_commands(text, context)

    def test_unsupported_unannotated_surface_is_not_a_standalone_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            unsupported = """```bash
tool && other
```
"""
            result = COMMAND.discover_commands(unsupported, context)
            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)

            annotated = unsupported + "<!-- command @1 = output -->\n"
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "invocation.command.unsupported"
            ):
                COMMAND.discover_commands(annotated, context)

    def test_identity_ignores_heading_and_line_but_counts_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            first = COMMAND.discover_commands(
                "# One\n\n```bash\ntool --output-data data/a.csv\n```\n", context
            )
            moved = COMMAND.discover_commands(
                "# Renamed\n\n\n\n```bash\ntool --output-data data/a.csv\n```\n",
                context,
            )
            duplicate = COMMAND.discover_commands(
                "```bash\n"
                "tool --output-data data/a.csv\n"
                "tool --output-data data/a.csv\n"
                "```\n",
                context,
            )

            self.assertEqual(
                first.invocations[0].identity, moved.invocations[0].identity
            )
            self.assertNotEqual(
                duplicate.invocations[0].identity, duplicate.invocations[1].identity
            )

    def test_caller_supplied_document_order_controls_global_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_context = _context(root)
            second_context = replace(
                first_context,
                document="docs/log/entries/entry/notes.md",
            )
            first = COMMAND.discover_commands(
                "```bash\ntool --output-data data/a.csv\n```\n", first_context
            ).invocations
            second = COMMAND.discover_commands(
                "```bash\ntool --input-data data/a.csv --output-data data/b.csv\n```\n",
                second_context,
            ).invocations

            ordered = COMMAND.order_invocations((first, second))

            self.assertEqual([item.sequence for item in ordered], [0, 1])
            self.assertEqual(
                [item.document for item in ordered],
                [
                    first_context.document,
                    second_context.document,
                ],
            )
            with mock.patch.object(COMMAND, "MAX_INVOCATIONS_PER_LOG", 1):
                with self.assertRaisesRegex(
                    COMMAND.CommandV2Error, "provenance.resource.too_large"
                ):
                    COMMAND.order_invocations((first, second))


class CommandV2CollectionTests(unittest.TestCase):
    def test_repeated_directory_and_manifest_collections_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            write(context.entry_root / "data" / "a.csv", "a\n")
            write(context.entry_root / "data" / "b.csv", "b\n")
            write(context.entry_root / "owned" / "x.txt", "x\n")
            write(context.entry_root / "owned" / "nested" / "y.txt", "y\n")
            write(context.entry_root / "manifest.csv", "path\ndata/a.csv\ndata/b.csv\n")
            text = """```bash
tool --input-file data/a.csv --input-file data/b.csv
tool --target owned
tool --files manifest.csv
```
<!-- command-2 target = output-directory -->
<!-- command-3 files = input-manifest -->
"""

            result = COMMAND.discover_commands(text, context)

            repeated, directory_result, manifest = result.invocations
            self.assertEqual(repeated.collections[0].mechanism, "repeated")
            self.assertEqual(len(directory_result.collections[0].members), 2)
            self.assertEqual(manifest.collections[0].mechanism, "manifest")
            self.assertEqual(len(manifest.inputs), 3)

    def test_manifest_escape_and_missing_member_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "manifest.csv", "path\n../outside.csv\n")
            text = """```bash
tool --files manifest.csv
```
<!-- command files = input-manifest -->
"""
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "collection.manifest.invalid"
            ):
                COMMAND.discover_commands(text, context)


if __name__ == "__main__":
    unittest.main()
