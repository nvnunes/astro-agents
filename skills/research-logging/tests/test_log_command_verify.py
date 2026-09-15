from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import log_commands.command_verification as command_verification_module
from log_commands.command_verification import (
    CommandVerificationRequest,
    _result,
    verify_command,
)
from log_commands.context import EntryContext
from log_commands.current_invocations import entry_invocations
from log_commands.dispatcher import _dispatch_command, main
from log_commands.model import ActionError
from log_commands.reproduction_execution import (
    ExecutionAttempt,
    ExecutionCheckpoint,
    ReproductionControlPlaneError,
    observe_output_fingerprint,
)
from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import (
    Fingerprint,
    InputResource,
    ResourceIdentity,
    observe_fingerprint,
)
from test_log_reproduction_planning import _Fixture
from validation.output_bindings import OutputBindingError, project_output_bindings
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    execution_id,
    load_pyrun_state,
    recipe_from_invocation,
)


class _TestConfinement:
    """Test-only confinement adapter; subprocess behavior is exercised directly."""

    def preflight(self) -> None:
        return None

    def command(self, command: object, **_kwargs: object) -> object:
        return command


class _RecordingConfinement(_TestConfinement):
    """Observe the real isolated execution confinement contract."""

    def __init__(self) -> None:
        self.writable_roots: tuple[Path, ...] = ()

    def command(self, command: object, **kwargs: object) -> object:
        roots = kwargs["writable_roots"]
        assert isinstance(roots, tuple)
        self.writable_roots = roots
        return command


class CommandVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.process_table = mock.patch(
            "log_commands.reproduction_execution._process_table", return_value={}
        )
        self.confinement = mock.patch(
            "log_commands.reproduction_execution.DarwinSeatbelt", _TestConfinement
        )
        self.process_table_mock = self.process_table.start()
        self.confinement.start()

    def tearDown(self) -> None:
        self.process_table.stop()
        self.confinement.stop()

    def _fixture(self, root: Path) -> tuple[_Fixture, str]:
        fixture = _Fixture(root)
        (fixture.root / "tmp").mkdir()
        environment = fixture.root / ".conda" / "bin"
        environment.mkdir(parents=True, exist_ok=True)
        (environment / "python").symlink_to(Path(sys.executable))
        entry = fixture.entry(1)
        source = entry.root / "data" / "source.txt"
        output = entry.root / "data" / "result.txt"
        source.write_text("source\n", encoding="utf-8")
        output.write_text("source\n", encoding="utf-8")
        fixture.write_data(
            entry,
            [
                fixture.item(entry, "source", source, origin=True),
                fixture.item(entry, "result", output, origin=False),
            ],
        )
        fixture.evidence(entry, "result")
        identity, execution = fixture.execution(
            entry, "repair", {"source": source}, {"result": output}
        )
        (entry.root / "scripts" / "repair.py").write_text(
            "from pathlib import Path\nimport sys\n"
            "Path(sys.argv[-1]).write_text(Path(sys.argv[-3]).read_text())\n",
            encoding="utf-8",
        )
        execution = replace(
            execution,
            observed=replace(
                execution.observed,
                script=Fingerprint(
                    "sha256",
                    hashlib.sha256(
                        (entry.root / "scripts" / "repair.py").read_bytes()
                    ).hexdigest(),
                ),
            ),
        )
        fixture.write_pyrun(entry, [(identity, execution)])
        return fixture, identity

    @staticmethod
    def _snapshot(root: Path) -> dict[str, str]:
        """Capture every retained fixture byte, excluding owned repair scratch."""

        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and "tmp" not in path.relative_to(root).parts
            and "research-log-operations" not in path.relative_to(root).parts
        }

    @staticmethod
    def _retained_identity(root: Path) -> dict[str, tuple[int, int, int, int, str]]:
        """Capture bytes and filesystem identity for every non-scratch fixture file."""

        result = {}
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(root)
            if "tmp" in relative.parts or "research-log-operations" in relative.parts:
                continue
            observed = path.stat()
            result[relative.as_posix()] = (
                stat.S_IMODE(observed.st_mode),
                observed.st_ino,
                observed.st_mtime_ns,
                observed.st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        return result

    def _assert_workspace(self, result: object) -> Path:
        workspace = getattr(result, "workspace")
        self.assertIsNotNone(workspace)
        path = Path(workspace)
        self.assertTrue(path.is_dir())
        return path

    def _entry_document(self, fixture: _Fixture) -> Path:
        return next((fixture.log_root / "entries").glob("*/e001.md"))

    def _replace_script(self, fixture: _Fixture, text: str) -> None:
        next((fixture.log_root / "entries").glob("*/scripts/repair.py")).write_text(
            text, encoding="utf-8"
        )

    def _refresh_script_observation(self, fixture: _Fixture, identity: str) -> None:
        state_path = next((fixture.log_root / "entries").glob("*/pyrun.json"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        script = next((fixture.log_root / "entries").glob("*/scripts/*.py"))
        matches = [
            command["executions"][identity]
            for command in state["commands"].values()
            if identity in command["executions"]
        ]
        self.assertEqual(len(matches), 1)
        matches[0]["observed"]["script"]["digest"] = hashlib.sha256(
            script.read_bytes()
        ).hexdigest()
        state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _rewrite_current_command(
        self,
        fixture: _Fixture,
        identity: str,
        command: str,
        script_text: str,
    ) -> str:
        """Rewrite one fixture command and derive its recorded recipe from Markdown."""

        entry_root = next((fixture.log_root / "entries").glob("*/e001.md")).parent
        entry = EntryContext(fixture.log, "e001", entry_root.resolve())
        document = entry_root / "e001.md"
        lines = document.read_text(encoding="utf-8").splitlines()
        command_indexes = [
            index for index, line in enumerate(lines) if line.startswith("./pyrun")
        ]
        self.assertEqual(len(command_indexes), 1)
        lines[command_indexes[0]] = command
        document.write_text("\n".join(lines) + "\n", encoding="utf-8")

        invocation = entry_invocations(entry, project_root=fixture.root.resolve())[0]
        recipe = recipe_from_invocation(
            invocation, entry_root=entry.root, project_root=fixture.root.resolve()
        )
        state = load_pyrun_state(
            entry.root / "pyrun.json",
            entry_root=entry.root,
            project_root=fixture.root.resolve(),
        )
        prior = state.execution(invocation.cid, identity)
        self.assertIsNotNone(prior)
        assert prior is not None
        script = entry.root / recipe.script
        script.write_text(script_text, encoding="utf-8")
        observed = replace(
            prior.observed,
            script=Fingerprint(
                "sha256", hashlib.sha256(script.read_bytes()).hexdigest()
            ),
            outputs=tuple(
                (
                    artifact,
                    observe_output_fingerprint(entry.root / artifact, kind),
                )
                for artifact, kind in recipe.outputs
            ),
        )
        updated = replace(prior, recipe=recipe, observed=observed)
        rewritten_identity = execution_id(recipe)
        fixture.write_pyrun(entry, [(rewritten_identity, updated)])
        return rewritten_identity

    def _tolerant_evidence_fixture(
        self, root: Path
    ) -> tuple[_Fixture, str, Path, Path, Path]:
        """Create a numeric evidence-scoped output plus one context-only source."""

        fixture = _Fixture(root)
        (fixture.root / "tmp").mkdir()
        environment = fixture.root / ".conda" / "bin"
        environment.mkdir(parents=True)
        (environment / "python").symlink_to(Path(sys.executable))
        entry = fixture.entry(1)
        raw = entry.root / "data" / "raw.txt"
        output = entry.root / "data" / "output.json"
        context = entry.root / "data" / "context.json"
        raw.write_text("raw\n", encoding="utf-8")
        output.write_text('{"value": 1.0}\n', encoding="utf-8")
        context.write_text('{"context": 1.0}\n', encoding="utf-8")
        fixture.write_data(
            entry,
            [
                fixture.item(entry, "raw", raw, origin=True),
                {
                    **fixture.item(entry, "output", output, origin=False),
                    "comparison": {
                        "contract": "research-log-evidence-scoped-comparison/1",
                        "profile": "evidence",
                    },
                },
                fixture.item(entry, "context", context, origin=False),
            ],
        )
        identity, execution = fixture.execution(
            entry, "build", {"raw": raw}, {"output": output}
        )
        document = entry.root / f"{entry.id}.md"
        document.write_text(
            document.read_text(encoding="utf-8")
            + "\n<!-- eid:numeric-output -->\n```text\n1.0\n```\n",
            encoding="utf-8",
        )
        evidence = entry.root / "evidence.json"
        evidence.write_text(
            json.dumps(
                {
                    "schema": "research-log-evidence/v4",
                    "records": [
                        {
                            "document": f"entries/{entry.root.name}/{entry.id}.md",
                            "id": "numeric-output",
                            "kind": "statistic",
                            "sources": [
                                {"source": "<output>", "locator": {"path": ["value"]}},
                                {
                                    "source": "<context>",
                                    "locator": {"path": ["context"]},
                                },
                            ],
                            "transformation": {
                                "form": "tuple",
                                "values": [
                                    {
                                        "source": {"input": 0, "item": 0},
                                        "render": {
                                            "decimal_places": 1,
                                            "mode": "fixed",
                                        },
                                    },
                                    {
                                        "source": {"input": 1, "item": 0},
                                        "render": {
                                            "decimal_places": 1,
                                            "mode": "fixed",
                                        },
                                    },
                                ],
                            },
                            "reproduction_tolerance": {"absolute": "0.01"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        script = entry.root / "scripts" / "build.py"
        script.write_text(
            "from pathlib import Path\nimport sys\n"
            "Path(sys.argv[-1]).write_text('{\\\"value\\\": 1.0}\\n')\n",
            encoding="utf-8",
        )
        execution = replace(
            execution,
            observed=replace(
                execution.observed,
                script=Fingerprint(
                    "sha256", hashlib.sha256(script.read_bytes()).hexdigest()
                ),
            ),
        )
        fixture.write_pyrun(entry, [(identity, execution)])
        return fixture, identity, output, context, document

    def _pattern_directory_fixture(self, root: Path) -> tuple[_Fixture, str, Path]:
        """Build one current recipe whose direct input is a selected directory view."""

        fixture, _unused_identity = self._fixture(root)
        entry = fixture.entry(2)
        bundle = entry.root / "data" / "bundle"
        bundle.mkdir()
        (bundle / "selected-a.txt").write_text("selected\n", encoding="utf-8")
        (bundle / "ignored.bin").write_bytes(b"ignored")
        raw = entry.root / "data" / "raw.txt"
        raw.write_text("raw\n", encoding="utf-8")
        output = entry.root / "data" / "result.txt"
        output.write_text("selected\n", encoding="utf-8")
        fixture.write_data(
            entry,
            [
                {
                    "identity": {
                        "algorithm": "identity-patterns-sha256-v1",
                        "patterns": ["selected-*.txt"],
                    },
                    "kind": "directory",
                    "location": "data/bundle",
                    "name": "bundle",
                    "origin": False,
                },
                fixture.item(entry, "result", output, origin=False),
            ],
        )
        fixture.evidence(entry, "result")
        # Reuse the fixture's complete Markdown command shape, then substitute
        # the directory token so current-invocation discovery is authoritative.
        fixture.execution(entry, "pattern", {"raw": raw}, {"result": output})
        script = entry.root / "scripts" / "pattern.py"
        script.write_text(
            "from pathlib import Path\nimport sys\n"
            "source = Path(sys.argv[-3]) / 'selected-a.txt'\n"
            "Path(sys.argv[-1]).write_text(source.read_text())\n",
            encoding="utf-8",
        )
        parameters = (
            "--input-data",
            "<bundle>",
            "--output-data",
            "data/result.txt",
        )
        recipe = ExecutionRecipe(
            "scripts/pattern.py",
            parameters,
            (),
            ("bundle",),
            (("data/result.txt", "file"),),
            fixture_parameter_roles(
                parameters, ("bundle",), (("data/result.txt", "file"),)
            ),
        )
        resource = InputResource(
            "bundle",
            "directory",
            "data/bundle",
            ResourceIdentity(
                "identity-patterns-sha256-v1", patterns=("selected-*.txt",)
            ),
            False,
            bundle.resolve().as_posix(),
        )
        execution = PyrunExecution(
            True,
            True,
            None,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
            recipe,
            ObservedExecution(
                Fingerprint("sha256", hashlib.sha256(script.read_bytes()).hexdigest()),
                (("bundle", observe_fingerprint(resource).fingerprint),),
                (),
                (
                    (
                        "data/result.txt",
                        Fingerprint(
                            "sha256", hashlib.sha256(output.read_bytes()).hexdigest()
                        ),
                    ),
                ),
            ),
        )
        identity = execution_id(recipe)
        fixture.write_pyrun(entry, [(identity, execution)])
        document = entry.root / "e002.md"
        document.write_text(
            document.read_text(encoding="utf-8").replace("<raw>", "<bundle>"),
            encoding="utf-8",
        )
        return fixture, identity, bundle

    def _directory_output_fixture(
        self, root: Path, *, regenerated: str
    ) -> tuple[_Fixture, str, Path]:
        """Build one exact-comparison directory output repair fixture."""

        fixture = _Fixture(root)
        (fixture.root / "tmp").mkdir()
        environment = fixture.root / ".conda" / "bin"
        environment.mkdir(parents=True, exist_ok=True)
        (environment / "python").symlink_to(Path(sys.executable))
        entry = fixture.entry(1)
        source = entry.root / "data" / "source.txt"
        source.write_text("source\n", encoding="utf-8")
        bundle = entry.root / "data" / "bundle"
        bundle.mkdir()
        (bundle / "member.txt").write_text("source\n", encoding="utf-8")
        fixture.write_data(
            entry,
            [
                fixture.item(entry, "source", source, origin=True),
                {
                    "identity": {"algorithm": "directory-sha256-v1"},
                    "kind": "directory",
                    "location": "data/bundle",
                    "name": "bundle",
                    "origin": False,
                },
            ],
        )
        (entry.root / "evidence.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "artifact_fingerprint": {
                                "algorithm": "sha256",
                                "digest": hashlib.sha256(
                                    source.read_bytes()
                                ).hexdigest(),
                            },
                            "document": f"entries/{entry.root.name}/e001.md",
                            "id": "source-fixture",
                            "kind": "artifact",
                            "sources": [{"locator": None, "source": "<source>"}],
                            "transformation": None,
                        }
                    ],
                    "schema": "research-log-evidence/v4",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        document = entry.root / "e001.md"
        document.write_text(
            "# Directory output\n\n## Run\n\n`Background:`\n\nFixture.\n\n"
            "`Steps:`\n\n```bash\n"
            "./pyrun --cid directory -- scripts/directory.py --input-data '<source>' "
            "--output-data '<bundle>'\n```\n\n`Results:`\n\n"
            "[source](data/source.txt)<!-- eid:source-fixture -->\n",
            encoding="utf-8",
        )
        script = entry.root / "scripts" / "directory.py"
        script.write_text(
            "from pathlib import Path\nimport sys\n"
            "output = Path(sys.argv[-1])\noutput.mkdir()\n"
            f"(output / 'member.txt').write_text({regenerated!r})\n",
            encoding="utf-8",
        )
        invocation = entry_invocations(entry, project_root=fixture.root.resolve())[0]
        recipe = recipe_from_invocation(
            invocation, entry_root=entry.root, project_root=fixture.root.resolve()
        )
        identity = execution_id(recipe)
        execution = PyrunExecution(
            True,
            True,
            None,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
            recipe,
            ObservedExecution(
                Fingerprint("sha256", hashlib.sha256(script.read_bytes()).hexdigest()),
                (
                    (
                        "source",
                        Fingerprint(
                            "sha256", hashlib.sha256(source.read_bytes()).hexdigest()
                        ),
                    ),
                ),
                (),
                (("data/bundle", observe_output_fingerprint(bundle, "directory")),),
            ),
        )
        fixture.write_pyrun(entry, [(identity, execution)])
        return fixture, identity, bundle

    def test_unknown_execution_fails_before_workspace_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, _identity = self._fixture(Path(directory))
            with self.assertRaisesRegex(ActionError, "unknown execution"):
                verify_command(
                    fixture.log,
                    CommandVerificationRequest(
                        "e001", "repair", "pyrun-exec/v2:" + "0" * 64
                    ),
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_selector_rejects_alias_and_prefix_before_log_access(self) -> None:
        identity = "pyrun-exec/v2:" + "1" * 64
        for selected in (identity.removeprefix("pyrun-exec/v2:"), identity[:40]):
            with (
                self.subTest(selected=selected),
                self.assertRaisesRegex(ActionError, "full pyrun-exec/v2 ID"),
            ):
                verify_command(
                    mock.sentinel.log,
                    CommandVerificationRequest("e001", "repair", selected),
                )

    def test_timeout_range_is_checked_before_locking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            with self.assertRaisesRegex(ActionError, "execution-timeout-seconds"):
                verify_command(
                    fixture.log,
                    CommandVerificationRequest("e001", "repair", identity, 0),
                )

    def test_result_contract_is_nonpublishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            value = result.as_dict()
            self.assertEqual(
                value["schema"], "research-log-command-verification-result/1"
            )
            self.assertFalse(value["published"])
            self.assertNotIn("run_id", value)
            self.assertNotIn("plan", value)
            self._assert_workspace(result)
            self.assertFalse((fixture.root / "tmp" / "reproduction").exists())

    def test_dispatcher_maps_operational_and_cleanup_failures_to_exit_two(self) -> None:
        cases = (
            OSError("log lock I/O failure"),
            ReproductionControlPlaneError(OSError("scratch cleanup failed")),
        )
        for error in cases:
            with self.subTest(error=type(error).__name__):
                stderr = io.StringIO()
                with (
                    mock.patch(
                        "log_commands.dispatcher._run_command_verification",
                        side_effect=error,
                    ),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        main(
                            [
                                "command",
                                "verify",
                                "--path",
                                "unused",
                                "--entry",
                                "e001",
                                "--cid",
                                "build",
                                "--execution-id",
                                "pyrun-exec/v2:" + "0" * 64,
                            ]
                        ),
                        2,
                    )
                self.assertIn("log:", stderr.getvalue())

    def test_cli_json_and_text_projection_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            arguments = (
                "--path",
                str(fixture.summary.with_suffix("")),
                "--entry",
                "e001",
                "--cid",
                "repair",
                "--execution-id",
                identity,
            )
            json_output = io.StringIO()
            with redirect_stdout(json_output):
                self.assertEqual(
                    _dispatch_command(("verify", *arguments, "--format", "json")),
                    0,
                )
            projected = json.loads(json_output.getvalue())
            self.assertEqual(
                set(projected),
                {
                    "schema",
                    "summary",
                    "entry",
                    "cid",
                    "execution_id",
                    "status",
                    "exit_status",
                    "published",
                    "workspace",
                    "execution",
                    "inputs",
                    "outputs",
                    "diagnostics",
                    "limitations",
                },
            )
            self.assertEqual(projected["status"], "matched")
            self.assertFalse(projected["published"])
            self.assertIn("stdout_path", projected["diagnostics"])
            text_output = io.StringIO()
            with redirect_stdout(text_output):
                self.assertEqual(_dispatch_command(("verify", *arguments)), 0)
            text = text_output.getvalue()
            self.assertIn(f"matched: {identity}", text)
            self.assertIn("workspace: ", text)
            self.assertIn("policy: ", text)
            self.assertIn("source: script", text)

    def test_exact_selector_keeps_retained_bytes_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            before = self._snapshot(fixture.root)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "matched")
            self.assertEqual(result.exit_status, 0)
            self.assertEqual(before, self._snapshot(fixture.root))
            self.assertTrue(
                Path(result.workspace or "")
                .resolve()
                .is_relative_to((fixture.root / "tmp").resolve())
            )

    def test_absent_selector_fails_before_workspace_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            document = self._entry_document(fixture)
            document.write_text(
                document.read_text(encoding="utf-8").replace("repair.py", "other.py"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ActionError, "recipe differs"):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_ambiguous_selector_fails_before_workspace_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            document = self._entry_document(fixture)
            text = document.read_text(encoding="utf-8")
            document.write_text(
                text + text[text.index("## repair") :], encoding="utf-8"
            )
            with self.assertRaisesRegex(ActionError, "invocation.cid.duplicate"):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_invalid_current_command_fails_before_workspace_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            document = self._entry_document(fixture)
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n```bash\n./pyrun --output-data data/result.txt\n```\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ActionError, "cannot derive a command ID"):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_stale_current_recipe_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            document = self._entry_document(fixture)
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "--output-data '<result>'", "--output-data data/other.txt"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ActionError, "current command selection is absent"
            ):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_changed_direct_input_executes_and_reports_historical_difference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            source = next((fixture.log_root / "entries").glob("*/data/source.txt"))
            source.write_text("changed\n", encoding="utf-8")
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "different")
            self.assertTrue(result.inputs[0]["differs_from_recorded"])
            self._assert_workspace(result)

    def test_current_generated_input_runs_without_launching_a_producer(self) -> None:
        """Command verification consumes current input and never schedules it."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            data_path = next((fixture.log_root / "entries").glob("*/data.json"))
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"][0]["origin"] = False
            data_path.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch(
                "log_commands.reproduction_execution.execute_current_reproduction_plan",
                side_effect=AssertionError(
                    "command verification must not launch a producer"
                ),
            ) as producer:
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertEqual(result.status, "matched")
            self._assert_workspace(result)
            producer.assert_not_called()

    def test_selected_pattern_directory_input_ignores_unselected_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity, bundle = self._pattern_directory_fixture(Path(directory))
            with mock.patch(
                "log_commands.reproduction_execution.execute_current_reproduction_plan",
                side_effect=AssertionError(
                    "command verification must not launch a producer"
                ),
            ) as producer:
                baseline = verify_command(
                    fixture.log, CommandVerificationRequest("e002", "pattern", identity)
                )
                (bundle / "ignored.bin").write_bytes(b"changed ignored")
                ignored = verify_command(
                    fixture.log, CommandVerificationRequest("e002", "pattern", identity)
                )
                (bundle / "selected-a.txt").write_text("changed\n", encoding="utf-8")
                selected = verify_command(
                    fixture.log, CommandVerificationRequest("e002", "pattern", identity)
                )
            self.assertEqual(baseline.status, "matched")
            self.assertEqual(ignored.status, "matched")
            self.assertFalse(ignored.inputs[0]["differs_from_recorded"])
            self.assertEqual(selected.status, "different")
            self.assertTrue(selected.inputs[0]["differs_from_recorded"])
            self._assert_workspace(selected)
            producer.assert_not_called()

    def test_evidence_tolerance_matches_and_rejects_changed_workspace_output(
        self,
    ) -> None:
        for value, expected in (("1.005", "matched"), ("1.02", "different")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                fixture, identity, retained, _context, _document = (
                    self._tolerant_evidence_fixture(Path(directory))
                )
                script = next((fixture.log_root / "entries").glob("*/scripts/build.py"))
                script.write_text(
                    "from pathlib import Path\nimport sys\n"
                    f"Path(sys.argv[-1]).write_text('{{\\\"value\\\": {value}}}\\n')\n",
                    encoding="utf-8",
                )
                self._refresh_script_observation(fixture, identity)
                before = retained.read_bytes()
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "build", identity)
                )
                self.assertEqual(result.status, expected)
                self.assertEqual(retained.read_bytes(), before)
                self._assert_workspace(result)
                evidence = result.outputs[0]["comparison"]["evidence"]
                self.assertEqual(evidence[0]["matched"], expected == "matched")

    def test_evidence_context_source_locator_and_document_mutation_are_unavailable(
        self,
    ) -> None:
        for target in ("source", "locator", "document"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, identity, retained, context, document = (
                    self._tolerant_evidence_fixture(Path(directory))
                )
                evidence = next((fixture.log_root / "entries").glob("*/evidence.json"))
                changed = {
                    "source": context,
                    "locator": evidence,
                    "document": document,
                }[target]
                script = next((fixture.log_root / "entries").glob("*/scripts/build.py"))
                script.write_text(
                    "from pathlib import Path\nimport sys\n"
                    "Path(sys.argv[-1]).write_text('{\\\"value\\\": 1.0}\\n')\n"
                    f"Path({str(changed)!r}).write_text('changed\\n')\n",
                    encoding="utf-8",
                )
                self._refresh_script_observation(fixture, identity)
                before = retained.read_bytes()
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "build", identity)
                )
                self.assertEqual(
                    (result.status, result.exit_status), ("unavailable", 3)
                )
                self._assert_workspace(result)
                self.assertEqual(retained.read_bytes(), before)

    def test_log_lock_brackets_authority_and_final_comparison_not_the_child(
        self,
    ) -> None:
        """The reservation spans repair work, but ordinary log locking does not."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            active = 0
            entered: list[str] = []

            @contextmanager
            def tracked_lock(_log: object) -> object:
                nonlocal active
                active += 1
                entered.append("enter")
                try:
                    yield
                finally:
                    active -= 1
                    entered.append("exit")

            execute = command_verification_module.execute_isolated_invocation

            def child_without_log_lock(*args: object, **kwargs: object) -> object:
                self.assertEqual(active, 0)
                return execute(*args, **kwargs)

            with (
                mock.patch("log_commands.command_verification.log_lock", tracked_lock),
                mock.patch(
                    "log_commands.command_verification.execute_isolated_invocation",
                    side_effect=child_without_log_lock,
                ),
            ):
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertEqual(result.status, "matched")
            self.assertEqual(entered, ["enter", "exit"] * 3)

    def test_command_verification_never_calls_durable_mutation_sinks(self) -> None:
        """A passing isolated execution cannot create or publish durable state."""

        sinks = (
            "log_commands.reproduction_jobs.launch_reproduction",
            "log_commands.reproduction_job_storage.record_execution_comparison",
            "log_commands.reproduction_publication.publish_completed_reproduction",
            "log_commands.reproduction_promotion.promote_execution",
            "log_commands.reproduction_comparison.clear_current_reproduction_requirement",
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            with ExitStack() as stack:
                blocked = [
                    stack.enter_context(
                        mock.patch(sink, side_effect=AssertionError(sink))
                    )
                    for sink in sinks
                ]
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertEqual(result.status, "matched")
            for sink in blocked:
                sink.assert_not_called()
            self.assertFalse((fixture.root / ".cache" / "results.sqlite").exists())
            self.assertFalse((fixture.root / ".cache" / "reproduction").exists())
            self.assertFalse((fixture.root / "tmp" / "reproduction").exists())

    def test_missing_current_input_does_not_launch_the_child(self) -> None:
        """Current input admission is a pre-launch boundary, not child policy."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            marker = fixture.root / "child-launched"
            self._replace_script(
                fixture,
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            )
            source = next((fixture.log_root / "entries").glob("*/data/source.txt"))
            source.unlink()
            before = self._retained_identity(fixture.root)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self.assertEqual(result.workspace, None)
            self.assertFalse(marker.exists())
            self.assertEqual(before, self._retained_identity(fixture.root))

    def test_unsafe_current_input_does_not_launch_the_child(self) -> None:
        """A symlink input is rejected while authority is still read-only."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            marker = fixture.root / "child-launched"
            self._replace_script(
                fixture,
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            )
            source = next((fixture.log_root / "entries").glob("*/data/source.txt"))
            replacement = source.with_name("replacement.txt")
            replacement.write_text("source\n", encoding="utf-8")
            source.unlink()
            source.symlink_to(replacement)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self.assertEqual(result.workspace, None)
            self.assertFalse(marker.exists())

    def test_malformed_current_input_declaration_does_not_launch_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            marker = fixture.root / "child-launched"
            self._replace_script(
                fixture,
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            )
            (next((fixture.log_root / "entries").glob("*/data.json"))).write_text(
                '{"schema": "research-log-data/v5", "inputs": "invalid"}\n',
                encoding="utf-8",
            )
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self.assertEqual(result.workspace, None)
            self.assertFalse(marker.exists())

    def test_corrupt_or_missing_authority_is_unavailable_without_child(self) -> None:
        """Every current-authority decoder is a non-launching repair boundary."""

        cases = (
            ("missing-data", "data.json", None),
            ("corrupt-data", "data.json", "{not json}\n"),
            ("corrupt-evidence", "evidence.json", "{not json}\n"),
            ("corrupt-pyrun", "pyrun.json", "{not json}\n"),
        )
        for name, filename, contents in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture, identity = self._fixture(Path(directory))
                marker = fixture.root / "child-launched"
                self._replace_script(
                    fixture,
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('ran')\n",
                )
                authority = next((fixture.log_root / "entries").glob(f"*/{filename}"))
                if contents is None:
                    authority.unlink()
                else:
                    authority.write_text(contents, encoding="utf-8")
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
                self.assertEqual(
                    (result.status, result.exit_status), ("unavailable", 3)
                )
                self.assertEqual(result.workspace, None)
                self.assertEqual(
                    result.diagnostics,
                    {
                        "stdout": "",
                        "stdout_path": "",
                        "stderr": "",
                        "stderr_path": "",
                    },
                )
                self.assertIn(
                    result.execution["failure"]["code"],
                    {
                        "command.verify.authority.unavailable",
                        "command.verify.input.unavailable",
                    },
                )
                self.assertFalse(marker.exists())

    def test_actual_timeout_retains_workspace_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "import time\ntime.sleep(5)\n",
            )
            self._refresh_script_observation(fixture, identity)
            before = self._retained_identity(fixture.root)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity, 1)
            )
            self.assertEqual(
                (result.status, result.exit_status), ("execution_failed", 3)
            )
            self.assertEqual(result.execution["failure"], "execution_timeout")
            workspace = self._assert_workspace(result)
            self.assertTrue(Path(result.diagnostics["stdout_path"]).is_file())
            self.assertTrue(Path(result.diagnostics["stderr_path"]).is_file())
            self.assertTrue(
                Path(result.diagnostics["stdout_path"]).is_relative_to(workspace)
            )
            self.assertEqual(before, self._retained_identity(fixture.root))

    def test_scratch_is_confined_and_removed_after_success_and_failure(self) -> None:
        """The private TMPDIR is writable to the child but never retained."""

        for expected, script in (
            ("matched", None),
            ("execution_failed", "import sys\nsys.exit(7)\n"),
        ):
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, identity = self._fixture(Path(directory))
                if script is not None:
                    self._replace_script(fixture, script)
                    self._refresh_script_observation(fixture, identity)
                adapter = _RecordingConfinement()
                created: list[Path] = []
                real_mkdtemp = tempfile.mkdtemp

                def record_mkdtemp(*args: object, **kwargs: object) -> str:
                    path = Path(real_mkdtemp(*args, **kwargs))
                    created.append(path)
                    return str(path)

                with (
                    mock.patch(
                        "log_commands.reproduction_execution.DarwinSeatbelt",
                        return_value=adapter,
                    ),
                    mock.patch(
                        "log_commands.reproduction_execution.tempfile.mkdtemp",
                        side_effect=record_mkdtemp,
                    ),
                ):
                    result = verify_command(
                        fixture.log,
                        CommandVerificationRequest("e001", "repair", identity),
                    )
                self.assertEqual(result.status, expected)
                self._assert_workspace(result)
                self.assertEqual(len(created), 1)
                self.assertIn(created[0], adapter.writable_roots)
                self.assertFalse(created[0].exists())

    def test_confinement_construction_failure_cleans_scratch_and_profile(self) -> None:
        """A backend failure after workspace creation leaves no disposable state."""

        class FailingConfinement(_TestConfinement):
            def command(self, command: object, **kwargs: object) -> object:
                roots = kwargs["writable_roots"]
                assert isinstance(roots, tuple)
                diagnostics = roots[-1]
                assert isinstance(diagnostics, Path)
                (diagnostics / "seatbelt-fixture.sb").write_text(
                    "fixture", encoding="utf-8"
                )
                raise OSError("confinement construction failed")

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            scratches: list[Path] = []
            real_mkdtemp = tempfile.mkdtemp

            def capture_scratch(*args: object, **kwargs: object) -> str:
                path = real_mkdtemp(*args, **kwargs)
                scratches.append(Path(path))
                return path

            with (
                mock.patch(
                    "log_commands.reproduction_execution.DarwinSeatbelt",
                    side_effect=(_TestConfinement(), FailingConfinement()),
                ),
                mock.patch(
                    "log_commands.reproduction_execution.tempfile.mkdtemp",
                    side_effect=capture_scratch,
                ),
                self.assertRaisesRegex(OSError, "confinement construction failed"),
            ):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertEqual(len(scratches), 1)
            self.assertFalse(scratches[0].exists())
            workspaces = list(
                (fixture.root / "tmp" / "command-verification").glob("*/*")
            )
            self.assertEqual(len(workspaces), 1)
            self.assertEqual(list(workspaces[0].rglob("seatbelt-*.sb")), [])

    def test_scratch_rmtree_failure_is_operational_and_preserves_research_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            before = self._retained_identity(fixture.root)
            real_rmtree = __import__("shutil").rmtree
            real_mkdtemp = tempfile.mkdtemp
            scratches: list[Path] = []

            def capture_scratch(*args: object, **kwargs: object) -> str:
                scratch = Path(real_mkdtemp(*args, **kwargs))
                scratches.append(scratch)
                return str(scratch)

            def fail_only_scratch(
                path: object, *args: object, **kwargs: object
            ) -> None:
                candidate = Path(path)
                if candidate.name.startswith("reproduction-scratch-"):
                    raise OSError("fixture scratch cleanup failure")
                real_rmtree(candidate, *args, **kwargs)

            with (
                mock.patch(
                    "log_commands.reproduction_execution.shutil.rmtree",
                    side_effect=fail_only_scratch,
                ),
                mock.patch(
                    "log_commands.reproduction_execution.tempfile.mkdtemp",
                    side_effect=capture_scratch,
                ),
                self.assertRaises(ReproductionControlPlaneError) as caught,
            ):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            for scratch in scratches:
                real_rmtree(scratch, ignore_errors=True)
            self.assertTrue(caught.exception.cleanup_incomplete)
            self.assertEqual(
                getattr(caught.exception, "code"), "scratch_cleanup_incomplete"
            )
            self.assertEqual(before, self._retained_identity(fixture.root))

    @unittest.skipUnless(
        sys.platform == "darwin" and os.environ.get("REPRODUCTION_SANDBOX_TEST") == "1",
        "requires the opt-in macOS Seatbelt smoke host",
    )
    def test_production_seatbelt_denies_network_and_retained_write(self) -> None:
        """Host-only smoke: verification workers get scratch, never authority."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            retained = next((fixture.log_root / "entries").glob("*/data/source.txt"))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport socket\nimport sys\n"
                f"retained = Path({str(retained)!r})\n"
                "try:\n    retained.write_text('unsafe')\n    write = 'allowed'\n"
                "except OSError:\n    write = 'denied'\n"
                "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n"
                "    network = 'allowed'\n"
                "except OSError:\n    network = 'denied'\n"
                "print(f'write={write} network={network}')\n"
                "Path(sys.argv[-1]).write_text(Path(sys.argv[-3]).read_text())\n",
            )
            self._refresh_script_observation(fixture, identity)
            # This suite normally replaces DarwinSeatbelt to be portable.
            self.confinement.stop()
            try:
                result = verify_command(
                    fixture.log,
                    CommandVerificationRequest("e001", "repair", identity, 10),
                )
            finally:
                self.confinement.start()
            self.assertEqual(result.status, "matched")
            self.assertIn("write=denied network=denied", result.diagnostics["stdout"])
            self.assertEqual(retained.read_text(encoding="utf-8"), "source\n")

    def test_live_sigint_and_sigterm_stop_the_running_child(self) -> None:
        """The signal handler, rather than result mapping, stops workers."""

        for received, exit_status in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with (
                self.subTest(received=received),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, identity = self._fixture(Path(directory))
                self._replace_script(
                    fixture,
                    "import time\nprint('child started', flush=True)\n"
                    "while True:\n    time.sleep(0.05)\n",
                )
                self._refresh_script_observation(fixture, identity)
                before = self._retained_identity(fixture.root)
                launched: list[subprocess.Popen[bytes]] = []
                real_popen = subprocess.Popen

                def capture_popen(
                    *args: object, **kwargs: object
                ) -> subprocess.Popen[bytes]:
                    process = real_popen(*args, **kwargs)
                    launched.append(process)
                    return process

                def visible_workers(
                    _marker: str,
                ) -> dict[int, tuple[int, str, str]]:
                    return {
                        process.pid: (os.getpid(), "running", "command-verify-worker")
                        for process in launched
                        if process.poll() is None
                    }

                self.process_table_mock.side_effect = visible_workers
                timer = threading.Timer(0.35, lambda: os.kill(os.getpid(), received))
                timer.start()
                try:
                    with mock.patch(
                        "log_commands.reproduction_execution.subprocess.Popen",
                        side_effect=capture_popen,
                    ):
                        result = verify_command(
                            fixture.log,
                            CommandVerificationRequest("e001", "repair", identity, 10),
                        )
                finally:
                    timer.cancel()
                    for process in launched:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    self.process_table_mock.side_effect = None
                    self.process_table_mock.return_value = {}
                self.assertEqual(
                    (result.status, result.exit_status), ("cancelled", exit_status)
                )
                self.assertTrue(result.execution["failure"] in {"stop_requested", None})
                workspace = self._assert_workspace(result)
                self.assertIn("child started", result.diagnostics["stdout"])
                self.assertTrue(
                    Path(result.diagnostics["stderr_path"]).is_relative_to(workspace)
                )
                self.assertEqual(before, self._retained_identity(fixture.root))

    def test_live_survivor_cleanup_reports_exit_two_without_leaking_worker(
        self,
    ) -> None:
        """A stale process inventory takes precedence after the real child is killed."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "import time\nprint('survivor fixture started', flush=True)\n"
                "while True:\n    time.sleep(0.05)\n",
            )
            self._refresh_script_observation(fixture, identity)
            before = self._retained_identity(fixture.root)
            launched: list[subprocess.Popen[bytes]] = []
            real_popen = subprocess.Popen

            def capture_popen(
                *args: object, **kwargs: object
            ) -> subprocess.Popen[bytes]:
                process = real_popen(*args, **kwargs)
                launched.append(process)
                return process

            def stale_worker(_marker: str) -> dict[int, tuple[int, str, str]]:
                return {
                    process.pid: (os.getpid(), "running", "command-verify-worker")
                    for process in launched
                }

            self.process_table_mock.side_effect = stale_worker
            timer = threading.Timer(0.35, lambda: os.kill(os.getpid(), signal.SIGTERM))
            timer.start()
            try:
                with (
                    mock.patch(
                        "log_commands.reproduction_execution.subprocess.Popen",
                        side_effect=capture_popen,
                    ),
                    mock.patch(
                        "log_commands.reproduction_execution.GRACEFUL_STOP_SECONDS",
                        0.05,
                    ),
                    mock.patch(
                        "log_commands.reproduction_execution.FORCED_STOP_SECONDS",
                        0.05,
                    ),
                    mock.patch(
                        "log_commands.reproduction_execution.POLL_SECONDS", 0.01
                    ),
                ):
                    result = verify_command(
                        fixture.log,
                        CommandVerificationRequest("e001", "repair", identity, 10),
                    )
            finally:
                timer.cancel()
                for process in launched:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                self.process_table_mock.side_effect = None
                self.process_table_mock.return_value = {}
            self.assertEqual(
                (result.status, result.exit_status), ("worker_cleanup_incomplete", 2)
            )
            workspace = self._assert_workspace(result)
            self.assertIn("survivor fixture started", result.diagnostics["stdout"])
            self.assertTrue(
                Path(result.diagnostics["stderr_path"]).is_relative_to(workspace)
            )
            self.assertEqual(before, self._retained_identity(fixture.root))

    def test_retained_identity_is_unchanged_for_match_difference_and_failure(
        self,
    ) -> None:
        """Command verification never replaces, chmods, or touches research state."""

        cases = (
            ("matched", None),
            (
                "different",
                "from pathlib import Path\nimport sys\n"
                "Path(sys.argv[-1]).write_text('different\\n')\n",
            ),
            ("execution_failed", "import sys\nsys.exit(17)\n"),
        )
        for expected, replacement in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, identity = self._fixture(Path(directory))
                if replacement is not None:
                    self._replace_script(fixture, replacement)
                    self._refresh_script_observation(fixture, identity)
                before = self._retained_identity(fixture.root)
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
                self.assertEqual(result.status, expected)
                self._assert_workspace(result)
                self.assertEqual(before, self._retained_identity(fixture.root))

    def test_changed_retained_baseline_is_comparison_unavailable_without_child(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            marker = fixture.root / "child-launched"
            self._replace_script(
                fixture,
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            )
            retained = next((fixture.log_root / "entries").glob("*/data/result.txt"))
            retained.write_text("changed baseline\n", encoding="utf-8")
            before = self._retained_identity(fixture.root)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(
                (result.status, result.exit_status), ("comparison_unavailable", 3)
            )
            self.assertEqual(result.workspace, None)
            self.assertEqual(
                result.diagnostics,
                {
                    "stdout": "",
                    "stdout_path": "",
                    "stderr": "",
                    "stderr_path": "",
                },
            )
            self.assertFalse(marker.exists())
            self.assertEqual(before, self._retained_identity(fixture.root))

    def test_changed_script_with_old_observation_is_reported_without_recipe_drift(
        self,
    ) -> None:
        """Current source drift stays visible even though recipe selection holds."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\n"
                "Path(sys.argv[-1]).write_text('different\\n')\n",
            )
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "different")
            self.assertTrue(result.execution["sources"][0]["differs_from_recorded"])
            self._assert_workspace(result)

    def test_difference_is_retained_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\n"
                "Path(sys.argv[-1]).write_text('different\\n')\n",
            )
            # Keep the recorded recipe observation current: command verification
            # tests the current declaration, not a deliberately stale baseline.
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "different")
            self._assert_workspace(result)
            self.assertFalse((fixture.root / "tmp" / "reproduction").exists())

    def test_nonzero_child_exit_is_retained_as_execution_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(fixture, "import sys\nsys.exit(17)\n")
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "execution_failed")
            self.assertEqual(result.execution["returncode"], 17)
            self._assert_workspace(result)

    def test_missing_declared_output_is_retained_as_execution_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(fixture, "print('no output')\n")
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "execution_failed")
            self.assertEqual(result.execution["failure"], "output_missing")

    def test_input_mutation_during_child_is_rejected_after_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\n"
                "source = Path(sys.argv[-3])\nsource.write_text('changed\\n')\n"
                "Path(sys.argv[-1]).write_text('source\\n')\n",
            )
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self._assert_workspace(result)

    def test_authority_mutation_during_child_is_rejected_after_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            summary = fixture.summary
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\n"
                f"Path({str(summary)!r}).write_text('changed\\n')\n"
                "Path(sys.argv[-1]).write_text(Path(sys.argv[-3]).read_text())\n",
            )
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self._assert_workspace(result)

    def test_retained_baseline_mutation_during_child_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            retained = next((fixture.log_root / "entries").glob("*/data/result.txt"))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\n"
                f"Path({str(retained)!r}).write_text('replaced\\n')\n"
                "Path(sys.argv[-1]).write_text('source\\n')\n",
            )
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("unavailable", 3))
            self._assert_workspace(result)

    def test_stdout_stderr_and_workspace_output_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "from pathlib import Path\nimport sys\nprint('out')\n"
                "print('err', file=sys.stderr)\n"
                "Path(sys.argv[-1]).write_text(Path(sys.argv[-3]).read_text())\n",
            )
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "matched")
            self.assertIn("out", result.diagnostics["stdout"])
            self.assertIn("err", result.diagnostics["stderr"])
            retained = next((fixture.log_root / "entries").glob("*/data/result.txt"))
            self.assertEqual(retained.read_text(encoding="utf-8"), "source\n")
            self.assertEqual(
                Path(result.outputs[0]["path"]).read_text(encoding="utf-8"),
                "source\n",
            )

    def test_reservation_conflict_leaves_no_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            with mock.patch(
                "log_commands.command_verification.reproduction_log_reservation"
            ) as reserve:
                reserve.side_effect = ActionError("reproduction.locked", "busy")
                with self.assertRaisesRegex(ActionError, "busy"):
                    verify_command(
                        fixture.log,
                        CommandVerificationRequest("e001", "repair", identity),
                    )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_output_binding_covers_equals_and_combined_capture_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = project_output_bindings(
                (
                    "--capture-stdout-stderr",
                    "data/combined.txt",
                    "--",
                    "--output=data/result.txt",
                ),
                (("data/combined.txt", "file"), ("data/result.txt", "file")),
                entry_root=root,
                project_root=root,
            )
            self.assertEqual(projection.child_parameters, ("--output=data/result.txt",))
            self.assertEqual(
                {(item.output, item.mechanism) for item in projection.bindings},
                {("data/combined.txt", "capture"), ("data/result.txt", "parameter")},
            )

    def test_named_binding_output_is_confined_to_verification_workspace(self) -> None:
        """The recorded named binding executes, but never writes retained output."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            retained = next((fixture.log_root / "entries").glob("*/data/result.txt"))
            before = retained.read_bytes()
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual(result.status, "matched")
            output = Path(result.outputs[0]["path"])
            self.assertTrue(output.is_relative_to(Path(result.workspace or "")))
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(retained.read_bytes(), before)

    def test_actual_parameter_and_capture_bindings_use_isolated_outputs(self) -> None:
        cases = (
            (
                "equals",
                "./pyrun --cid repair -- scripts/repair.py --input-data '<source>' "
                "--output-data='<result>'",
                "from pathlib import Path\nimport sys\n"
                "source = sys.argv[-2]\n"
                "output = sys.argv[-1].split('=', 1)[1]\n"
                "Path(output).write_text(Path(source).read_text())\n",
            ),
            (
                "positional",
                "./pyrun --cid repair --other-inputs @1 --other-outputs @2 -- "
                "scripts/repair.py '<source>' '<result>'",
                "from pathlib import Path\nimport sys\n"
                "Path(sys.argv[-1]).write_text(Path(sys.argv[-2]).read_text())\n",
            ),
            (
                "stdout",
                "./pyrun --cid repair --capture-stdout '<result>' -- "
                "scripts/repair.py --input-data '<source>'",
                "from pathlib import Path\nimport sys\n"
                "print(Path(sys.argv[-1]).read_text(), end='')\n",
            ),
            (
                "stderr",
                "./pyrun --cid repair --capture-stderr '<result>' -- "
                "scripts/repair.py --input-data '<source>'",
                "from pathlib import Path\nimport sys\n"
                "print(Path(sys.argv[-1]).read_text(), end='', file=sys.stderr)\n",
            ),
            (
                "combined",
                "./pyrun --cid repair --capture-stdout-stderr '<result>' -- "
                "scripts/repair.py --input-data '<source>'",
                "from pathlib import Path\nimport sys\n"
                "print(Path(sys.argv[-1]).read_text(), end='')\n",
            ),
        )
        for name, command, script in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture, identity = self._fixture(Path(directory))
                retained = next(
                    (fixture.log_root / "entries").glob("*/data/result.txt")
                )
                before = retained.read_bytes()
                identity = self._rewrite_current_command(
                    fixture, identity, command, script
                )
                result = verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
                self.assertEqual(result.status, "matched", result.as_dict())
                output = Path(result.outputs[0]["path"])
                self.assertTrue(output.is_relative_to(Path(result.workspace or "")))
                self.assertEqual(output.read_bytes(), before)
                self.assertEqual(retained.read_bytes(), before)

    def test_actual_directory_output_isolated_match_and_difference(self) -> None:
        for regenerated, expected in (
            ("source\n", "matched"),
            ("different\n", "different"),
        ):
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture, identity, retained = self._directory_output_fixture(
                    Path(directory), regenerated=regenerated
                )
                before = self._retained_identity(fixture.root)
                result = verify_command(
                    fixture.log,
                    CommandVerificationRequest("e001", "directory", identity),
                )
                self.assertEqual(result.status, expected, result.as_dict())
                output = Path(result.outputs[0]["path"])
                self.assertTrue(output.is_relative_to(Path(result.workspace or "")))
                self.assertTrue(output.is_dir())
                self.assertEqual(
                    (output / "member.txt").read_text(encoding="utf-8"), regenerated
                )
                self.assertEqual(
                    (retained / "member.txt").read_text(encoding="utf-8"), "source\n"
                )
                self.assertEqual(before, self._retained_identity(fixture.root))

    def test_binding_preflight_rejects_overlapping_capture_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(OutputBindingError):
                project_output_bindings(
                    (
                        "--capture-stdout",
                        "data/stdout.txt",
                        "--capture-stdout-stderr",
                        "data/combined.txt",
                        "--",
                    ),
                    (("data/stdout.txt", "file"), ("data/combined.txt", "file")),
                    entry_root=root,
                    project_root=root,
                )

    def test_signal_exit_mapping_and_cleanup_precedence(self) -> None:
        invocation = SimpleNamespace(
            entry="e001",
            cid="repair",
            execution_id="pyrun-exec/v2:" + "0" * 64,
            execution=SimpleNamespace(observed=SimpleNamespace(inputs=())),
        )
        authority = SimpleNamespace(invocation=invocation, inputs={}, sources=())
        comparison = SimpleNamespace(artifacts=(), matched=True)
        workspace = Path("/private/tmp/command-verification-test")
        for received, expected_status, expected_exit in (
            ([signal.SIGINT], "cancelled", 130),
            ([signal.SIGTERM], "cancelled", 143),
        ):
            attempt = ExecutionAttempt(
                "e001",
                "repair",
                invocation.execution_id,
                None,
                True,
                None,
                None,
                ExecutionCheckpoint(
                    "e001", "repair", invocation.execution_id, "stopped", "", None, ()
                ),
                (),
                "stdout",
                "stderr",
            )
            result = _result(
                SimpleNamespace(summary=Path("docs/study.md")),
                authority,
                attempt,
                comparison,
                {},
                workspace,
                received,
            )
            self.assertEqual(
                (result.status, result.exit_status), (expected_status, expected_exit)
            )
        survivor = ExecutionAttempt(
            "e001",
            "repair",
            invocation.execution_id,
            None,
            True,
            "worker_cleanup_incomplete",
            "survivor",
            ExecutionCheckpoint(
                "e001", "repair", invocation.execution_id, "failed", "", None, ()
            ),
            (),
            "stdout",
            "stderr",
        )
        result = _result(
            SimpleNamespace(summary=Path("docs/study.md")),
            authority,
            survivor,
            comparison,
            {},
            workspace,
            [signal.SIGTERM],
        )
        self.assertEqual(
            (result.status, result.exit_status), ("worker_cleanup_incomplete", 2)
        )

    def test_reservation_oserror_remains_operational(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            with mock.patch(
                "log_commands.command_verification.reproduction_log_reservation",
                side_effect=OSError("lock I/O failed"),
            ):
                with self.assertRaisesRegex(OSError, "lock I/O failed"):
                    verify_command(
                        fixture.log,
                        CommandVerificationRequest("e001", "repair", identity),
                    )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())

    def test_signal_precedes_late_unavailable_result(self) -> None:
        workspace = Path("/private/tmp/command-verification-signal")
        for received, exit_status in (([signal.SIGINT], 130), ([signal.SIGTERM], 143)):
            result = command_verification_module._unavailable_result(
                SimpleNamespace(summary=Path("docs/study.md")),
                CommandVerificationRequest(
                    "e001", "repair", "pyrun-exec/v2:" + "0" * 64
                ),
                ActionError("command.verify.source.changed", "late"),
                workspace=workspace,
                diagnostics={"stdout": "", "stderr": ""},
                received=received,
            )
            self.assertEqual(
                (result.status, result.exit_status), ("cancelled", exit_status)
            )
            self.assertEqual(result.workspace, str(workspace))
            self.assertEqual(
                result.diagnostics,
                {
                    "stdout": "",
                    "stdout_path": "",
                    "stderr": "",
                    "stderr_path": "",
                },
            )

    def test_child_deleted_tmpdir_is_cleaned_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            self._replace_script(
                fixture,
                "import os\nfrom pathlib import Path\nimport sys\n"
                "os.rmdir(os.environ['TMPDIR'])\n"
                "Path(sys.argv[-1]).write_text(Path(sys.argv[-3]).read_text())\n",
            )
            self._refresh_script_observation(fixture, identity)
            result = verify_command(
                fixture.log, CommandVerificationRequest("e001", "repair", identity)
            )
            self.assertEqual((result.status, result.exit_status), ("matched", 0))

    def test_missing_summary_owned_split_document_rejects_before_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture, identity = self._fixture(Path(directory))
            entry = next((fixture.log_root / "entries").glob("*"))
            with fixture.summary.open("a", encoding="utf-8") as handle:
                handle.write(f"\n- [Missing](study/entries/{entry.name}/e001a.md)\n")
            with self.assertRaisesRegex(ActionError, "e001a.md"):
                verify_command(
                    fixture.log, CommandVerificationRequest("e001", "repair", identity)
                )
            self.assertFalse((fixture.root / "tmp" / "command-verification").exists())


if __name__ == "__main__":
    unittest.main()
