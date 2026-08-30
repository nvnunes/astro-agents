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

    def test_literal_function_expands_positional_arguments_and_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
fit_variant () {
  architecture=$1
  shift
  ./pyrun scripts/train.py \\
    --output-dir "data/models/${architecture}" \\
    --hidden-units "$@"
}
fit_variant compact 32 16 &
fit_variant wide 64 32 &
wait
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 2)
            compact, wide = result.invocations
            self.assertNotIn("&", compact.tokens)
            self.assertIn("data/models/compact", compact.tokens)
            self.assertEqual(compact.tokens[-2:], ("32", "16"))
            self.assertIn("data/models/wide", wide.tokens)

    def test_background_function_call_changes_identity_not_body_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
run_one () {
  tool --label "$1"
}
run_one alpha
run_one alpha &
wait
```
"""

            result = COMMAND.discover_commands(text, context)

            foreground, background = result.invocations
            self.assertEqual(foreground.tokens, background.tokens)
            self.assertNotEqual(foreground.identity, background.identity)
            self.assertNotIn("&", background.tokens)

    def test_literal_for_loop_expands_scalar_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for fraction in 025 050 075; do
  ./pyrun scripts/train.py \\
    --input-data "data/mean-${fraction}-membership.csv" \\
    --output-dir "data/models/mean-${fraction}"
done
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 3)
            self.assertEqual(
                [item.inputs[0].path.rsplit("/", 1)[-1] for item in result.invocations],
                [
                    "mean-025-membership.csv",
                    "mean-050-membership.csv",
                    "mean-075-membership.csv",
                ],
            )
            self.assertTrue(
                all(
                    "${fraction}" not in token
                    for item in result.invocations
                    for token in item.tokens
                )
            )

    def test_literal_case_expands_architecture_specific_array(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for architecture in compact deep; do
  case "$architecture" in
    compact) hidden_units=(64 32) ;;
    deep) hidden_units=(128 64 32) ;;
  esac
  ./pyrun scripts/train.py \\
    --output-data "data/${architecture}.csv" \\
    --hidden-units "${hidden_units[@]}"
done
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 2)
            self.assertEqual(result.invocations[0].tokens[-2:], ("64", "32"))
            self.assertEqual(result.invocations[1].tokens[-3:], ("128", "64", "32"))
            self.assertTrue(result.invocations[0].outputs[0].path.endswith("compact.csv"))
            self.assertTrue(result.invocations[1].outputs[0].path.endswith("deep.csv"))

    def test_static_projection_changes_identity_without_changing_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            first = COMMAND.discover_commands(
                "```bash\nfor case in alpha; do\ntool --label fixed\ndone\n```\n",
                context,
            ).invocations[0]
            second = COMMAND.discover_commands(
                "```bash\nfor case in alpha beta; do\ntool --label fixed\ndone\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(first.tokens, second.tokens)
            self.assertNotEqual(first.identity, second.identity)

    def test_static_projection_canonicalizes_quotes_and_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            first = COMMAND.discover_commands(
                "```bash\nfor x in alpha; do\ntool --label \"$x\"\ndone\n```\n",
                context,
            ).invocations[0]
            second = COMMAND.discover_commands(
                "```bash\nfor   x   in alpha ; do\ntool   --label   ${x}\ndone\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(first.tokens, second.tokens)
            self.assertEqual(first.identity, second.identity)

            first_function = COMMAND.discover_commands(
                "```bash\nrun_one () {\ntool --label \"$1\"\n}\n"
                "run_one alpha\n```\n",
                context,
            ).invocations[0]
            second_function = COMMAND.discover_commands(
                "```bash\nrun_one()   {\ntool   --label   $1\n}\n"
                "run_one   alpha\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(first_function.tokens, second_function.tokens)
            self.assertEqual(first_function.identity, second_function.identity)

    def test_static_projection_retains_result_affecting_source_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            literal = COMMAND.discover_commands(
                "```bash\nfor x in fixed; do\ntool --label fixed\ndone\n```\n",
                context,
            ).invocations[0]
            bound = COMMAND.discover_commands(
                "```bash\nfor x in fixed; do\ntool --label \"$x\"\ndone\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(literal.tokens, bound.tokens)
            self.assertNotEqual(literal.identity, bound.identity)

    def test_standalone_wait_is_not_an_invocation_inside_supported_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
run_one () {
  tool --label "$1"
  wait
}
run_one function &
for value in loop; do
  tool --label "$value"
  wait
done
wait
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 2)
            self.assertEqual(
                [item.tokens[-1] for item in result.invocations],
                ["function", "loop"],
            )
            self.assertEqual([item.ordinal for item in result.invocations], [1, 2])

    def test_unsupported_composite_commands_fail_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            cases = {
                "function-operator": """run_one () {
  tool --output-data data/leaked.csv
  tool && other
}
run_one alpha""",
                "loop-operator": """for value in alpha; do
  tool --output-data data/leaked.csv
  tool && other
done""",
                "loop-glob": """for value in alpha; do
  tool --output-data data/leaked.csv
  tool --input-data data/*.csv
done""",
            }
            for name, body in cases.items():
                with self.subTest(name=name):
                    result = COMMAND.discover_commands(
                        f"```bash\n{body}\n```\n", context
                    )

                    self.assertFalse(result.invocations)
                    self.assertEqual(len(result.unsupported), 1)

    def test_escaped_characters_remain_literal_inside_static_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = r"""```bash
for value in result; do
  tool --label \${value} --operator \& --pattern \*.csv
done
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(
                result.invocations[0].tokens[-6:],
                ("--label", "${value}", "--operator", "&", "--pattern", "*.csv"),
            )

    def test_escaped_output_variable_cannot_create_false_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = r"""```bash
for value in result; do
  tool --output-data data/\${value}.csv
done
```
"""

            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "material.unresolved"
            ):
                COMMAND.discover_commands(text, context)

    def test_zero_argument_static_function_is_outside_the_closed_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
prepare () {
  tool --output-data data/result.csv
}
prepare
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)
            self.assertEqual(
                result.unsupported[0]["reason"],
                "static shell function requires literal arguments",
            )

    def test_dynamic_static_shell_forms_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            cases = {
                "unbound": 'tool --output-data "$HOME/result.csv"',
                "command-substitution": 'tool --value "$(other)"',
                "process-substitution": "tool --input-data <(other)",
                "arithmetic": 'tool --value "$((1 + 1))"',
                "glob": "tool --input-data data/*.csv",
                "dynamic-loop": "for value in $VALUES; do\ntool $value\ndone",
                "nested-loop": (
                    "for outer in one; do\n"
                    "for inner in two; do\n"
                    "tool --value $inner\n"
                    "done\n"
                    "done"
                ),
                "dynamic-case": (
                    "for value in one; do\n"
                    "case \"$other\" in\n"
                    "one) selected=literal ;;\n"
                    "esac\n"
                    "tool --value $selected\n"
                    "done"
                ),
            }
            for name, body in cases.items():
                with self.subTest(name=name):
                    result = COMMAND.discover_commands(
                        f"```bash\n{body}\n```\n", context
                    )
                    self.assertFalse(result.invocations)
                    self.assertEqual(len(result.unsupported), 1)

    def test_unsupported_control_block_does_not_leak_body_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
if test -f data/source.csv; then
  tool --output-data data/result.csv
fi
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)

    def test_single_quoted_expansions_and_operators_remain_literal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --label '$(other)' --operator '&' --pattern '*.csv'
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 1)
            self.assertEqual(
                result.invocations[0].tokens[-6:],
                ("--label", "$(other)", "--operator", "&", "--pattern", "*.csv"),
            )

    def test_single_quoted_loop_variable_is_not_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for value in alpha; do
  tool --label '${value}'
done
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(result.invocations[0].tokens[-1], "${value}")

    def test_single_quoted_loop_output_cannot_create_false_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for value in alpha; do
  tool --output-data 'data/${value}.csv'
done
```
"""

            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "material.unresolved"
            ):
                COMMAND.discover_commands(text, context)

    def test_invalid_function_call_is_consumed_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
fit () {
  tool --output-data "data/${1}.csv"
}
fit alpha; other --output-data data/leaked.csv
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)

    def test_invalid_and_duplicate_functions_poison_later_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            cases = (
                """fit () {
  if true; then
    tool --value "$1"
  fi
}
fit alpha""",
                """fit () {
  tool --value "$1"
}
fit () {
  tool --value "$1"
}
fit alpha""",
            )
            for body in cases:
                with self.subTest(body=body):
                    result = COMMAND.discover_commands(
                        f"```bash\n{body}\n```\n", context
                    )
                    self.assertFalse(result.invocations)
                    self.assertEqual(len(result.unsupported), 2)

    def test_malformed_function_definition_poison_later_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
fit () { unexpected
  tool --output-data data/leaked.csv
}
fit alpha
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 2)

    def test_nested_unsupported_control_does_not_leak_after_inner_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
if true; then
  if true; then
    ignored
  fi
  tool --output-data data/leaked.csv
fi
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)

    def test_tab_separated_unsupported_control_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            result = COMMAND.discover_commands(
                "```bash\nif\ttrue; then\n"
                "tool --output-data data/leaked.csv\nfi\n```\n",
                context,
            )

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.unsupported), 1)

    def test_annotation_ordinals_count_only_concrete_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
if true; then
  ignored
fi
tool data/result.csv
```
<!-- command-1 @1 = output -->
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.unsupported), 1)
            self.assertEqual(len(result.invocations), 1)
            self.assertTrue(result.invocations[0].outputs[0].path.endswith("result.csv"))
            self.assertEqual(result.invocations[0].ordinal, 1)

    def test_static_expansion_obeys_invocation_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for value in one two three; do
  tool --value "$value"
done
```
"""

            with mock.patch.object(COMMAND, "MAX_INVOCATIONS_PER_FENCE", 2):
                with self.assertRaisesRegex(
                    COMMAND.CommandV2Error, "provenance.resource.too_large"
                ):
                    COMMAND.discover_commands(text, context)

    def test_static_token_and_work_bounds_report_the_exact_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            with mock.patch.object(COMMAND, "MAX_STATIC_TOKENS_PER_FENCE", 2):
                with self.assertRaises(COMMAND.CommandV2Error) as tokens:
                    COMMAND.discover_commands(
                        "```bash\ntool one two\n```\n", context
                    )
            self.assertEqual(tokens.exception.observed, {"tokens": 3, "limit": 2})

            repeated = """```bash
for value in one two; do
  # scanned for every binding
  tool --label "$value"
done
```
"""
            with mock.patch.object(COMMAND, "MAX_STATIC_WORK_ITEMS_PER_FENCE", 6):
                with self.assertRaises(COMMAND.CommandV2Error) as work:
                    COMMAND.discover_commands(repeated, context)
            self.assertEqual(
                work.exception.observed, {"work_items": 7, "limit": 6}
            )

    def test_unsupported_surfaces_do_not_consume_invocation_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            bodies = (
                'tool "$HOME/one"\ntool "$HOME/two"\ntool "$HOME/three"',
                "tool && one\ntool && two\ntool && three",
            )
            for body in bodies:
                with self.subTest(body=body), mock.patch.object(
                    COMMAND, "MAX_INVOCATIONS_PER_FENCE", 1
                ):
                    result = COMMAND.discover_commands(
                        f"```bash\n{body}\n```\n", context
                    )

                    self.assertFalse(result.invocations)
                    self.assertEqual(len(result.unsupported), 3)

    def test_static_expansion_bounds_empty_loop_iterations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
for value in one two three; do
  # no invocation
done
```
"""

            with mock.patch.object(COMMAND, "MAX_STATIC_BINDINGS_PER_FENCE", 2):
                with self.assertRaisesRegex(
                    COMMAND.CommandV2Error, "provenance.resource.too_large"
                ) as caught:
                    COMMAND.discover_commands(text, context)

            self.assertEqual(caught.exception.observed, {"bindings": 3, "limit": 2})

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

    def test_symlinked_entry_material_root_remains_command_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            retained = root / "output" / "entry" / "data"
            write(retained / "a.csv", "a\n")
            write(retained / "b.csv", "b\n")
            (context.entry_root / "data").symlink_to(
                retained, target_is_directory=True
            )
            text = """```bash
tool --target data
```
<!-- command target = output-directory -->
"""

            invocation = COMMAND.discover_commands(text, context).invocations[0]

            self.assertEqual(len(invocation.outputs), 2)
            self.assertEqual(
                invocation.collections[0].root, retained.resolve().as_posix()
            )

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
