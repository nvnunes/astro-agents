from __future__ import annotations

import hashlib
import importlib
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from research_log_data import (
    FingerprintObservation,
    InputResource,
    build_git_repository_input,
    build_local_input,
    data_file_from_inputs,
    verify_fingerprint,
)
from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")


def _context(root: Path, inputs: tuple[InputResource, ...] = ()) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    write(entry_root / "scripts" / "run.py", "# fixture\n")
    data_file = (
        data_file_from_inputs(
            entry_root / "data.json", entry_root=entry_root, inputs=inputs
        )
        if inputs
        else None
    )
    return COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/e001.md",
        entry_root=entry_root,
        log_root=root / "docs" / "log",
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )


def _discover(body: str, context: object) -> object:
    return COMMAND.discover_commands(f"```bash\n{body}\n```\n", context)


class CommandRoleTests(unittest.TestCase):
    def test_natural_and_explicit_roles_form_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "docs/log/entries/entry/data/source.csv"
            write(source, "value\n1\n")
            resource = build_local_input(
                "source", "file", "data/source.csv", entry_root=source.parents[1],
                origin=True,
            )
            context = _context(root, (resource,))
            result = _discover(
                "./pyrun --other-inputs catalog --other-outputs results -- "
                "scripts/run.py --catalog '<source>' --results data/result.csv "
                "--output-image images/result.png",
                context,
            )

            self.assertFalse(result.failures)
            invocation = result.invocations[0]
            self.assertEqual({item.target for item in invocation.inputs}, {"catalog"})
            self.assertEqual(
                {item.target for item in invocation.outputs},
                {"results", "output-image"},
            )
            self.assertEqual(
                invocation.parameters,
                (
                    "--catalog", "<source>", "--results", "data/result.csv",
                    "--output-image", "images/result.png",
                ),
            )

    def test_directory_input_member_preserves_registered_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "docs/log/entries/entry"
            write(entry / "data/bundle/model.pt", "model\n")
            resource = build_local_input(
                "bundle", "directory", "data/bundle", entry_root=entry, origin=False
            )
            result = _discover(
                "./pyrun scripts/run.py --input-model '<bundle>/model.pt'",
                _context(root, (resource,)),
            )

            relationship = result.invocations[0].inputs[0]
            self.assertEqual(
                relationship.path, (entry / "data/bundle/model.pt").resolve().as_posix()
            )
            self.assertEqual(relationship.input_resource, resource)

    def test_git_repository_requires_locator_and_commit_projections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            subprocess.run(
                ["git", "init"], cwd=repository, check=True, capture_output=True
            )
            write(repository / "source.txt", "source\n")
            subprocess.run(["git", "add", "source.txt"], cwd=repository, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=Tests", "-c",
                    "user.email=tests@example.invalid", "commit", "-m", "fixture",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            entry = root / "docs/log/entries/entry"
            resource = build_git_repository_input(
                "source-repository", repository.as_posix(), commit, entry_root=entry
            )
            context = _context(root, (resource,))

            complete = _discover(
                "./pyrun scripts/run.py --input-repository '<source-repository>' "
                "--input-commit '<source-repository:commit>'",
                context,
            )
            self.assertFalse(complete.failures)
            self.assertEqual(
                complete.invocations[0].inputs[0].path, resource.material_identity
            )
            incomplete = _discover(
                "./pyrun scripts/run.py --input-repository '<source-repository>'",
                context,
            )
            self.assertEqual(
                incomplete.failures[0].error.code, "data.git.projection_missing"
            )

    def test_capture_is_a_file_output_and_signature_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "./pyrun --capture-stdout data/run.log -- scripts/run.py --mode exact",
                _context(Path(directory)),
            )

            invocation = result.invocations[0]
            self.assertEqual(invocation.outputs[0].proof, "pyrun-capture")
            self.assertEqual(
                invocation.parameters,
                ("--capture-stdout", "data/run.log", "--", "--mode", "exact"),
            )

    def test_adjacent_comments_are_inert(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            result = COMMAND.discover_commands(
                "```bash\n./pyrun scripts/run.py --target result\n```\n"
                "<!-- historical note -->\n",
                context,
            )

            self.assertFalse(result.failures)
            self.assertFalse(result.invocations[0].outputs)

    def test_material_roots_cannot_receive_explicit_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            context.entry_root.joinpath("data").mkdir(exist_ok=True)
            result = _discover(
                "./pyrun --other-outputs target -- scripts/run.py --target data",
                context,
            )

            self.assertFalse(result.invocations)
            self.assertEqual(result.failures[0].error.code, "material.root.invalid")

    def test_project_file_and_directory_outputs_are_portable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "artifacts/trials/member.csv", "value\n1\n")
            result = _discover(
                "./pyrun scripts/run.py --output-file "
                "'<project>/artifacts/result.csv' --output-dir "
                "'<project>/artifacts/trials'",
                _context(root),
            )

            self.assertFalse(result.failures)
            invocation = result.invocations[0]
            self.assertEqual(
                invocation.collections[0].root,
                (root / "artifacts/trials").resolve().as_posix(),
            )
            self.assertEqual(len(invocation.outputs), 2)

    def test_absolute_project_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = _discover(
                f"./pyrun scripts/run.py --output-file '{root / 'result.csv'}'",
                _context(root),
            )
            self.assertEqual(
                result.failures[0].error.code, "pyrun.output.identity_invalid"
            )

    def test_script_observation_is_part_of_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            invocation = _discover("./pyrun scripts/run.py", context).invocations[0]

            self.assertEqual(invocation.script_argument, "scripts/run.py")
            self.assertEqual(
                invocation.script_identity,
                hashlib.sha256(b"# fixture\n").hexdigest(),
            )

class ClosedShellGrammarTests(unittest.TestCase):
    def test_multiple_direct_pyrun_calls_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "./pyrun scripts/run.py --label first\n"
                "./pyrun scripts/run.py --label second",
                _context(Path(directory)),
            )
            self.assertFalse(result.failures)
            self.assertEqual(len(result.invocations), 2)

    def test_non_pyrun_and_mixed_fences_fail_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            for body in (
                "python scripts/run.py",
                "./pyrun scripts/run.py\npython scripts/run.py",
            ):
                with self.subTest(body=body):
                    result = _discover(body, context)
                    self.assertFalse(result.invocations)
                    self.assertEqual(
                        result.failures[0].error.code, "invocation.command.unsupported"
                    )

    def test_all_shell_fences_are_checked_but_only_results_create_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = replace(_context(root), require_experimental_context=True)
            text = (
                "## Background\n\n```bash\n"
                "./pyrun scripts/run.py --label ignored\n```\n\n"
                "## Experiment\n\n`Steps:`\n\n`Results:`\n\n"
                "```bash\n./pyrun scripts/run.py --label retained\n```\n"
            )
            result = COMMAND.discover_commands(text, context)
            self.assertFalse(result.failures)
            self.assertEqual(len(result.invocations), 1)
            self.assertIn("retained", result.invocations[0].tokens)

            invalid = COMMAND.discover_commands(
                "## Background\n\n```bash\npython scripts/run.py\n```\n", context
            )
            self.assertEqual(
                invalid.failures[0].error.code, "invocation.command.unsupported"
            )

    def test_disallowed_shell_forms_fail_closed(self) -> None:
        cases = {
            "environment-prefix": "MODE=test ./pyrun scripts/run.py",
            "pipeline": "./pyrun scripts/run.py | tee output.log",
            "redirection": "./pyrun scripts/run.py > output.log",
            "background": "./pyrun scripts/run.py &\nwait",
            "function": "run_one() {\n./pyrun scripts/run.py\n}\nrun_one",
            "conditional": "if true; then\n./pyrun scripts/run.py\nfi",
            "command-substitution": "./pyrun scripts/run.py --label '$(date)'",
            "glob": "./pyrun scripts/run.py --input-file data/*.csv",
        }
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            for name, body in cases.items():
                with self.subTest(name=name):
                    result = _discover(body, context)
                    self.assertFalse(result.invocations)
                    self.assertEqual(
                        result.failures[0].error.code, "invocation.command.unsupported"
                    )

    def test_literal_for_loop_expands_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "for value in alpha beta; do\n"
                "  ./pyrun scripts/run.py --label \"$value\"\n"
                "done",
                _context(Path(directory)),
            )
            self.assertFalse(result.failures)
            self.assertEqual(
                [item.parameters[-1] for item in result.invocations], ["alpha", "beta"]
            )

    def test_literal_array_and_arbitrary_nested_loops_expand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "outer_pixels=(28559 5219)\n"
                "for device in cpu gpu; do\n"
                "  for outer_pixel in \"${outer_pixels[@]}\"; do\n"
                "    for mode in fast exact; do\n"
                "      ./pyrun scripts/run.py --device \"$device\" "
                "--outer-pixel \"$outer_pixel\" --mode \"$mode\"\n"
                "    done\n"
                "  done\n"
                "done",
                _context(Path(directory)),
            )
            self.assertFalse(result.failures)
            self.assertEqual(len(result.invocations), 8)

    def test_literal_case_assigns_scalar_and_array_loop_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "for architecture in small large; do\n"
                "  case \"$architecture\" in\n"
                "    small) hidden=(64 32) ;;\n"
                "    large) hidden=(128 128) ;;\n"
                "  esac\n"
                "  case \"$architecture\" in\n"
                "    small) input_manifest=small-manifest ;;\n"
                "    large) input_manifest=large-manifest ;;\n"
                "  esac\n"
                "  ./pyrun scripts/run.py --architecture \"$architecture\" "
                "--hidden \"${hidden[@]}\" --manifest \"$input_manifest\"\n"
                "done",
                _context(Path(directory)),
            )
            self.assertFalse(result.failures)
            self.assertEqual(len(result.invocations), 2)
            self.assertIn("64", result.invocations[0].tokens)
            self.assertIn("32", result.invocations[0].tokens)
            self.assertIn("large-manifest", result.invocations[1].tokens)

    def test_top_level_assignment_cannot_parameterize_a_direct_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _discover(
                "label=alpha\n./pyrun scripts/run.py --label fixed",
                _context(Path(directory)),
            )
            self.assertFalse(result.invocations)
            self.assertEqual(
                result.failures[0].error.code, "invocation.command.unsupported"
            )

    def test_static_projection_distinguishes_loop_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            first = _discover(
                "for value in alpha; do\n"
                "./pyrun scripts/run.py --label fixed\n"
                "done",
                context,
            ).invocations[0]
            second = _discover(
                "for value in beta; do\n"
                "./pyrun scripts/run.py --label fixed\n"
                "done",
                context,
            ).invocations[0]
            self.assertEqual(first.tokens, second.tokens)
            self.assertNotEqual(first.identity, second.identity)

    def test_invocation_limit_applies_after_static_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            values = " ".join(str(index) for index in range(65))
            with self.assertRaisesRegex(
                COMMAND.CommandV2Error, "provenance.resource.too_large"
            ):
                _discover(
                    f"for value in {values}; do\n"
                    "./pyrun scripts/run.py --value \"$value\"\n"
                    "done",
                    _context(Path(directory)),
                )


class CommandCollectionTests(unittest.TestCase):
    def test_named_input_directory_uses_shared_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "docs/log/entries/entry"
            write(entry / "data/catalog/a.csv", "a\n")
            resource = build_local_input(
                "catalog", "directory", "data/catalog", entry_root=entry, origin=True
            )
            calls: list[str] = []

            def verify(candidate: InputResource) -> FingerprintObservation:
                calls.append(candidate.name)
                observation = verify_fingerprint(candidate)
                assert observation is not None
                return observation

            context = replace(
                _context(root, (resource,)), input_fingerprint_verifier=verify
            )
            result = _discover(
                "./pyrun scripts/run.py --input-directory '<catalog>'", context
            )

            self.assertFalse(result.failures)
            self.assertEqual(calls, ["catalog"])
            self.assertEqual(
                result.invocations[0].collections[0].root,
                resource.canonical_target,
            )

    def test_output_directory_counts_as_one_authored_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "docs/log/entries/entry/data/owned"
            for index in range(COMMAND.MAX_RELATIONSHIPS + 1):
                write(owned / f"{index}.csv", "value\n")
            result = _discover(
                "./pyrun --other-outputs target -- scripts/run.py --target data/owned",
                _context(root),
            )

            self.assertFalse(result.failures)
            invocation = result.invocations[0]
            self.assertEqual(len(invocation.outputs), COMMAND.MAX_RELATIONSHIPS + 1)
            self.assertEqual(invocation.collections[0].root, owned.resolve().as_posix())


if __name__ == "__main__":
    unittest.main()
