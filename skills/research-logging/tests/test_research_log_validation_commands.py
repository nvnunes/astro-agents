from __future__ import annotations

import hashlib
import importlib
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from research_log_data import (
    FingerprintObservation,
    InputResource,
    build_git_repository_input,
    build_identity_directory,
    build_local_input,
    data_file_from_inputs,
    verify_fingerprint,
)
from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")


def _git_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source-repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    write(repository / "source.txt", "source\n")
    subprocess.run(["git", "add", "source.txt"], cwd=repository, check=True)
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
        cwd=repository,
        check=True,
        capture_output=True,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    return repository, commit


def _context(root: Path, data_index: dict[str, str] | None = None) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    inputs = []
    for name, location in (data_index or {}).items():
        inputs.append(
            build_local_input(
                name,
                "directory" if Path(location).is_dir() else "file",
                location,
                entry_root=entry_root,
                origin=True,
            )
        )
    data_file = (
        data_file_from_inputs(
            entry_root / "data.json",
            entry_root=entry_root,
            inputs=tuple(inputs),
        )
        if inputs
        else None
    )
    return COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="docs/log/entries/entry/e001.md",
        entry_root=entry_root,
        log_root=root / "docs" / "log",
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )


class CommandV2RoleTests(unittest.TestCase):
    def test_git_repository_projections_form_one_material_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            repository, commit = _git_repository(root)
            resource = build_git_repository_input(
                "source-repository",
                repository.as_posix(),
                commit,
                entry_root=context.entry_root,
            )
            data_file = data_file_from_inputs(
                context.entry_root / "data.json",
                entry_root=context.entry_root,
                inputs=(resource,),
            )
            context = replace(context, data_file=data_file)
            write(context.entry_root / "scripts/run.py", "# fixture\n")
            complete = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py \\
  --input-repository "<source-repository>" \\
  --input-commit "<source-repository:commit>"
```
""",
                context,
            )
            self.assertEqual(len(complete.invocations), 1)
            self.assertEqual(len(complete.invocations[0].inputs), 1)
            self.assertEqual(
                complete.invocations[0].inputs[0].path,
                resource.material_identity,
            )

            for token in ("<source-repository>", "<source-repository:commit>"):
                incomplete = COMMAND.discover_commands(
                    f"""```bash
./pyrun scripts/run.py --input-repository "{token}"
```
""",
                    context,
                )
                self.assertFalse(incomplete.invocations)
                self.assertEqual(
                    incomplete.failures[0].error.code,
                    "data.git.projection_missing",
                )

    def test_pyrun_capture_is_an_exact_output_and_signature_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            write(context.entry_root / "scripts/run.py", "# fixture\n")
            invocation = COMMAND.discover_commands(
                """```bash
./pyrun --capture-stdout data/run.log -- \\
  scripts/run.py \\
  --mode exact
```
""",
                context,
            ).invocations[0]

            self.assertTrue(invocation.via_pyrun)
            self.assertEqual(invocation.script_argument, "scripts/run.py")
            self.assertEqual(
                invocation.parameters,
                ("--capture-stdout", "data/run.log", "--", "--mode", "exact"),
            )
            self.assertEqual(len(invocation.outputs), 1)
            self.assertEqual(invocation.outputs[0].proof, "pyrun-capture")
            self.assertEqual(invocation.outputs[0].target, "capture-stdout")

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
            entry_root = root / "docs/log/entries/entry"
            write(entry_root / "data/model.csv", "value\n1\n")
            write(entry_root / "data/catalog.csv", "value\n1\n")
            context = _context(
                root,
                {
                    "catalog": "data/catalog.csv",
                    "model": "data/model.csv",
                },
            )
            write(context.entry_root / "scripts" / "evaluate.py", "# fixture\n")
            write(context.entry_root / "scripts" / "simulate_trials.py", "# fixture\n")
            text = """```bash
./pyrun scripts/evaluate.py --catalog '<catalog>' --results data/model.csv
./pyrun scripts/simulate_trials.py --input-data '<model>' \
  --output-data data/trials.npz
```
<!-- command results = output -->
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 2)
            model, simulation = result.invocations
            self.assertTrue(model.inputs[0].origin)
            self.assertTrue(model.outputs[0].path.endswith("data/model.csv"))
            self.assertTrue(simulation.inputs[0].path.endswith("data/model.csv"))
            self.assertTrue(simulation.outputs[0].path.endswith("data/trials.npz"))

    def test_annotation_ordinal_and_positional_target_are_one_based(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "docs/log/entries/entry/summary.csv", "value\n1\n")
            context = _context(root, {"summary": "summary.csv"})
            text = """```bash
tool --flag
tool '<summary>' final.csv
```
<!-- command-2 @1 = input; @2 = output -->
"""

            result = COMMAND.discover_commands(text, context)

            second = result.invocations[1]
            self.assertEqual(len(result.invocations[0].inputs), 0)
            self.assertTrue(second.inputs[0].path.endswith("summary.csv"))
            self.assertTrue(second.outputs[0].path.endswith("final.csv"))

    def test_indented_annotation_after_indented_fence_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            text = """  ```bash
  tool result.csv
  ```
  <!-- command @1 = output -->
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 1)
            self.assertTrue(
                result.invocations[0].outputs[0].path.endswith("result.csv")
            )

    def test_caffeinate_flags_do_not_consume_wrapped_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            text = """```bash
caffeinate -dimsu ./ao-sky run v2 --output-summary data/summary.json
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 1)
            invocation = result.invocations[0]
            self.assertEqual(Path(invocation.executable).name, "ao-sky")
            self.assertTrue(invocation.outputs[0].path.endswith("data/summary.json"))

    def test_annotation_override_and_embedded_path_failure_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "docs/log/entries/entry/source.csv", "value\n1\n")
            context = _context(root, {"source": "source.csv"})
            text = """```bash
tool --output-data '<source>'
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
            embedded_result = COMMAND.discover_commands(embedded, context)
            self.assertFalse(embedded_result.invocations)
            self.assertEqual(len(embedded_result.failures), 1)
            self.assertEqual(
                embedded_result.failures[0].error.code,
                "invocation.path_value.embedded",
            )

    def test_path_like_scalar_values_do_not_become_material_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --case "ERIS / 1 NGS"
tool --run "9/6,2026-04-19T04:49:25+00:00,2026-04-19T18:59:50+00:00"
tool --title-prefix "Stochastic 9/6 Static Simulation vs Run #1"
tool --title-prefix "v2/v3 Dynamic"
tool --version v2/v3
tool --date 2026/09/02
tool --directory missing/directory
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 7)
            self.assertFalse(result.failures)
            self.assertTrue(all(not item.candidates for item in result.invocations))

    def test_unavailable_long_scalar_does_not_terminate_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            value = "x" * 300

            result = COMMAND.discover_commands(
                f"```bash\ntool --label {value}\n```\n", context
            )

            self.assertEqual(len(result.invocations), 1)
            self.assertFalse(result.failures)
            self.assertFalse(result.invocations[0].candidates)

    def test_positive_path_evidence_still_creates_material_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            existing = context.entry_root / "data with spaces" / "input"
            write(existing, "retained\n")
            existing_directory = context.entry_root / "catalog"
            existing_directory.mkdir()
            values = (
                existing.relative_to(context.entry_root).as_posix(),
                existing_directory.relative_to(context.entry_root).as_posix(),
                "record('<project>/data/input.csv')",
            )

            for value in values:
                with self.subTest(value=value):
                    result = COMMAND.discover_commands(
                        f'```bash\ntool --value "{value}"\n```\n', context
                    )
                    self.assertFalse(result.invocations)
                    self.assertEqual(
                        result.failures[0].error.code,
                        "material.candidate.unresolved",
                    )

    def test_exact_artifact_roots_are_not_material_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            (context.entry_root / "data").mkdir(parents=True)
            (context.entry_root / "images").mkdir()
            text = """```bash
tool --data-dir data --image-dir images --data-alias data/../data
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 1)
            self.assertFalse(result.failures)
            self.assertFalse(result.invocations[0].candidates)

    def test_exact_artifact_roots_cannot_receive_material_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            (context.entry_root / "data").mkdir(parents=True)
            (context.entry_root / "images").mkdir()
            cases = (
                (
                    "tool --input-data data",
                    "",
                ),
                (
                    "tool --output-data data",
                    "",
                ),
                (
                    "tool --target images",
                    "<!-- command target = input-directory -->\n",
                ),
                (
                    "tool --target images",
                    "<!-- command target = output-directory -->\n",
                ),
                (
                    "tool --output-data data/../data",
                    "",
                ),
            )

            for command, annotation in cases:
                with self.subTest(command=command):
                    result = COMMAND.discover_commands(
                        f"```bash\n{command}\n```\n{annotation}", context
                    )
                    self.assertFalse(result.invocations)
                    self.assertEqual(
                        result.failures[0].error.code,
                        "material.root.invalid",
                    )

    def test_command_type_annotation_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "invocation.annotation.invalid"
            ):
                COMMAND.discover_commands(
                    "```bash\ntool --output-data result.csv\n```\n"
                    "<!-- command type = model -->\n",
                    _context(Path(directory)),
                )

    def test_raw_external_rule_applies_only_to_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            external = root / "output" / "result.csv"
            text = f"""```bash
tool --input-data {external}
tool --output-data {external}
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.failures), 1)
            self.assertEqual(
                result.failures[0].error.code,
                "data.input.undeclared",
            )
            self.assertEqual(len(result.invocations), 1)
            self.assertEqual(
                result.invocations[0].outputs[0].path,
                external.resolve().as_posix(),
            )

    def test_script_filenames_have_no_provenance_classification(self) -> None:
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
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.invocations), 4)
            self.assertTrue(
                all(
                    not hasattr(invocation, "command_type")
                    for invocation in result.invocations
                )
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
<!-- command results = output -->
"""

            first = COMMAND.discover_commands(text, context).invocations[0]
            write(script, "raise RuntimeError('contents changed')\n")
            second = COMMAND.discover_commands(text, context).invocations[0]

            self.assertTrue(first.outputs)
            self.assertFalse(first.candidates)
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

    def test_script_change_during_hash_is_an_unavailable_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "analyze.py"
            write(script, "print('before')\n")
            original_stat = COMMAND.Path.stat
            calls = 0

            def racing_stat(path: Path, *args: object, **kwargs: object):
                nonlocal calls
                if path == script:
                    calls += 1
                    if calls == 2:
                        write(script, "print('after')\n")
                return original_stat(path, *args, **kwargs)

            with mock.patch.object(COMMAND.Path, "stat", new=racing_stat):
                with self.assertRaisesRegex(
                    COMMAND.CommandV2Error, "provenance.observation.unavailable"
                ):
                    COMMAND._observe_script(script)

    def test_stale_in_memory_script_observation_is_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            script = context.entry_root / "scripts" / "analyze.py"
            write(script, "print('before')\n")
            stale = COMMAND._observe_script(script)
            write(script, "print('after')\n")
            expected = hashlib.sha256(script.read_bytes()).hexdigest()
            canonical = script.resolve().as_posix()
            context = replace(
                context,
                script_identity_cache={canonical: stale},
            )

            invocation = COMMAND.discover_commands(
                "```bash\n./pyrun scripts/analyze.py\n```\n", context
            ).invocations[0]

            self.assertEqual(invocation.script_identity, expected)
            self.assertNotEqual(invocation.script_identity, stale.digest)


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
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            write(entry_root / "data/source.csv", "value\n1\n")
            write(entry_root / "data/config.txt", "config\n")
            context = _context(
                root, {"source": "data/source.csv", "config": "data/config.txt"}
            )
            text = """```bash
MODE=test tool --input-data '<source>' \\
  --result data/result.csv < '<config>' > data/run.log
tool --input-data '<source>' | tee data/capture.log
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
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry/data"
            inputs = {}
            for fraction in ("025", "050", "075"):
                path = entry_root / f"mean-{fraction}-membership.csv"
                write(path, "value\n1\n")
                inputs[f"mean-{fraction}"] = f"data/{path.name}"
            context = _context(root, inputs)
            text = """```bash
for fraction in 025 050 075; do
  ./pyrun scripts/train.py \\
    --input-data "<mean-${fraction}>" \\
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
            self.assertTrue(
                result.invocations[0].outputs[0].path.endswith("compact.csv")
            )
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
                '```bash\nfor x in alpha; do\ntool --label "$x"\ndone\n```\n',
                context,
            ).invocations[0]
            second = COMMAND.discover_commands(
                "```bash\nfor   x   in alpha ; do\ntool   --label   ${x}\ndone\n```\n",
                context,
            ).invocations[0]

            self.assertEqual(first.tokens, second.tokens)
            self.assertEqual(first.identity, second.identity)

            first_function = COMMAND.discover_commands(
                '```bash\nrun_one () {\ntool --label "$1"\n}\nrun_one alpha\n```\n',
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
                '```bash\nfor x in fixed; do\ntool --label "$x"\ndone\n```\n',
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
  tool --label \${value} --operator \& --pattern \*.pattern
done
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(
                result.invocations[0].tokens[-6:],
                ("--label", "${value}", "--operator", "&", "--pattern", "*.pattern"),
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

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0].error.code, "material.unresolved")

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
                    'case "$other" in\n'
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
tool --label '$(other)' --operator '&' --pattern '*.pattern'
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.unsupported)
            self.assertEqual(len(result.invocations), 1)
            self.assertEqual(
                result.invocations[0].tokens[-6:],
                ("--label", "$(other)", "--operator", "&", "--pattern", "*.pattern"),
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

            result = COMMAND.discover_commands(text, context)

            self.assertFalse(result.invocations)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0].error.code, "material.unresolved")

    def test_failed_command_does_not_discard_later_valid_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --output-data 'data/${missing}.csv'
tool --output-data data/result.csv
```
"""

            result = COMMAND.discover_commands(text, context)

            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0].fence, 1)
            self.assertEqual(result.failures[0].ordinal, 1)
            self.assertEqual(len(result.invocations), 1)
            self.assertTrue(
                result.invocations[0].outputs[0].path.endswith("data/result.csv")
            )

    def test_failed_commands_still_consume_the_invocation_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            text = """```bash
tool --output-data 'data/${first}.csv'
tool --output-data 'data/${second}.csv'
```
"""

            with mock.patch.object(COMMAND, "MAX_INVOCATIONS_PER_LOG", 1):
                with self.assertRaisesRegex(
                    COMMAND.CommandV2Error, "provenance.resource.too_large"
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
            self.assertTrue(
                result.invocations[0].outputs[0].path.endswith("result.csv")
            )
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
                    COMMAND.discover_commands("```bash\ntool one two\n```\n", context)
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
            self.assertEqual(work.exception.observed, {"work_items": 7, "limit": 6})

    def test_unsupported_surfaces_do_not_consume_invocation_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            bodies = (
                'tool "$HOME/one"\ntool "$HOME/two"\ntool "$HOME/three"',
                "tool && one\ntool && two\ntool && three",
            )
            for body in bodies:
                with (
                    self.subTest(body=body),
                    mock.patch.object(COMMAND, "MAX_INVOCATIONS_PER_FENCE", 1),
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
            root = Path(directory)
            write(root / "docs/log/entries/entry/data/source.csv", "value\n1\n")
            context = _context(root, {"source": "data/source.csv"})
            text = (
                "```bash\n"
                "tool --input-data '<source>' \\\n"
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
            write(root / "docs/log/entries/entry/data/a.csv", "value\n1\n")
            first_context = _context(root, {"a": "data/a.csv"})
            second_context = replace(
                first_context,
                document="docs/log/entries/entry/notes.md",
            )
            first = COMMAND.discover_commands(
                "```bash\ntool --output-data data/a.csv\n```\n", first_context
            ).invocations
            second = COMMAND.discover_commands(
                "```bash\ntool --input-data '<a>' --output-data data/b.csv\n```\n",
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
    def test_named_input_directory_uses_shared_fingerprint_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "docs/log/entries/entry/data/collection"
            write(collection / "a.csv", "a\n")
            write(collection / "nested" / "b.csv", "b\n")
            context = _context(root, {"collection": collection.as_posix()})
            assert context.data_file is not None
            resource = context.data_file.by_name["collection"]
            observation = verify_fingerprint(resource)
            assert observation is not None
            calls: list[str] = []

            def shared_verifier(value: InputResource) -> FingerprintObservation:
                calls.append(value.name)
                return observation

            context = replace(context, input_fingerprint_verifier=shared_verifier)
            with mock.patch.object(
                COMMAND,
                "verify_fingerprint",
                side_effect=AssertionError("shared verifier must own observation"),
            ):
                result = COMMAND.discover_commands(
                    "```bash\ntool --input-directory '<collection>'\n```\n"
                    "<!-- command input-directory = input-directory -->\n",
                    context,
                )

            self.assertEqual(calls, ["collection"])
            self.assertEqual(len(result.invocations[0].collections[0].members), 2)

    def test_identity_file_directory_is_one_logical_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            build = entry_root / "build"
            write(build / "build.h5", "state")
            write(build / "build.yaml", "mode: test\n")
            write(build / "products" / "outer.h5", "product")
            resource = build_identity_directory(
                "build",
                "build",
                ("build.h5", "build.yaml"),
                entry_root=entry_root,
                origin=True,
            )
            context = replace(
                _context(root),
                data_file=data_file_from_inputs(
                    entry_root / "data.json",
                    entry_root=entry_root,
                    inputs=(resource,),
                ),
            )

            result = COMMAND.discover_commands(
                "```bash\ntool --input-directory '<build>'\n```\n"
                "<!-- command input-directory = input-directory -->\n",
                context,
            )

            invocation = result.invocations[0]
            self.assertEqual(len(invocation.inputs), 1)
            self.assertEqual(invocation.inputs[0].path, build.resolve().as_posix())
            self.assertEqual(invocation.inputs[0].proof, "identity-files")
            self.assertEqual(
                invocation.collections[0].members,
                (build.resolve().as_posix(),),
            )
            self.assertEqual(invocation.collections[0].mechanism, "identity-files")

    def test_repeated_inputs_and_output_directory_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            write(entry_root / "data" / "a.csv", "a\n")
            write(entry_root / "data" / "b.csv", "b\n")
            write(entry_root / "owned" / "x.txt", "x\n")
            write(entry_root / "owned" / "nested" / "y.txt", "y\n")
            context = _context(root, {"a": "data/a.csv", "b": "data/b.csv"})
            text = """```bash
tool --input-file '<a>' --input-file '<b>'
tool --target owned
```
<!-- command-2 target = output-directory -->
"""

            result = COMMAND.discover_commands(text, context)

            repeated, directory_result = result.invocations
            self.assertEqual(len(repeated.inputs), 2)
            self.assertEqual(len(directory_result.collections[0].members), 2)

    def test_large_output_directory_counts_as_one_authored_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            for index in range(COMMAND.MAX_RELATIONSHIPS + 1):
                write(entry_root / "owned" / f"{index}.txt", "x\n")
            context = _context(root)
            text = """```bash
tool --target owned
```
<!-- command target = output-directory -->
"""

            invocation = COMMAND.discover_commands(text, context).invocations[0]

            self.assertEqual(len(invocation.outputs), COMMAND.MAX_RELATIONSHIPS + 1)
            self.assertEqual(len(invocation.collections), 1)

    def test_symlinked_entry_material_root_cannot_receive_material_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = _context(root)
            retained = root / "output" / "entry" / "data"
            write(retained / "a.csv", "a\n")
            write(retained / "b.csv", "b\n")
            (context.entry_root / "data").symlink_to(retained, target_is_directory=True)
            for value in ("data", retained.as_posix()):
                with self.subTest(value=value):
                    text = f"""```bash
tool --target {value}
```
<!-- command target = output-directory -->
"""

                    result = COMMAND.discover_commands(text, context)

                    self.assertFalse(result.invocations)
                    self.assertEqual(
                        result.failures[0].error.code,
                        "material.root.invalid",
                    )

    def test_manifest_annotation_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "manifest.csv", "path\n../outside.csv\n")
            text = """```bash
tool --files manifest.csv
```
<!-- command files = input-manifest -->
"""
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "invocation.annotation.invalid"
            ):
                COMMAND.discover_commands(text, context)


if __name__ == "__main__":
    unittest.main()
