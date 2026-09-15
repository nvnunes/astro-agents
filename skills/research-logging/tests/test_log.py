from __future__ import annotations

import fcntl
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log, run_log_process

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"
PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log(cwd, *arguments)


def authoring_result(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def fixture(root: Path) -> tuple[Path, Path]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    summary = root / "docs" / "study.md"
    entry = root / "docs" / "study" / "entries" / "2026-09-03-e001-study"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    summary.write_text(
        "# Study\n\n"
        "Validation: [latest completed report](study/validation.md)\n\n"
        "Reproduction: [latest report](study/reproduction.md)\n\n"
        "## Summary\n\n"
        "## Entries\n\n"
        "- `2026-09-03` [Study](study/entries/2026-09-03-e001-study/e001.md)\n",
        encoding="utf-8",
    )
    document = entry / "e001.md"
    document.write_text(
        "# Study\n\n## Trial\n\n`Results:`\n\n"
        "The rate was `67.6%`<!-- eid:success-rate -->.\n\n"
        "<!-- eid:comparison -->\n"
        "Case | Value\n--- | ---\ncandidate | exact\n\n"
        "<!-- eid:run-output -->\n```text\ncompleted\n```\n",
        encoding="utf-8",
    )
    results = entry / "data" / "results.csv"
    results.write_text("case,rate,value\ncandidate,0.676,exact\n", encoding="utf-8")
    run_log = entry / "data" / "run.log"
    run_log.write_text("completed\n", encoding="utf-8")
    (entry / "data.json").write_text(
        json.dumps(
            {
                "schema": "research-log-data/v6",
                "inputs": [
                    {
                        "identity": {
                            "algorithm": "sha256",
                        },
                        "kind": "file",
                        "location": "data/results.csv",
                        "name": "results",
                        "origin": True,
                    },
                    {
                        "identity": {
                            "algorithm": "sha256",
                        },
                        "kind": "file",
                        "location": "data/run.log",
                        "name": "run-log",
                        "origin": True,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary.with_suffix(""), entry


class LogHelpAndContextTests(unittest.TestCase):
    def test_progressive_help_lists_only_current_depth(self) -> None:
        top = run(Path.cwd(), "--help")
        self.assertEqual(top.returncode, 0, top.stderr)
        for family_name in (
            "add",
            "data",
            "discover",
            "evidence",
            "init",
            "reproduce",
            "reorganize",
            "retention",
            "validate",
        ):
            self.assertIn(family_name, top.stdout)
        self.assertNotIn("--source", top.stdout)

        family = run(Path.cwd(), "evidence", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertIn("sync", family.stdout)
        self.assertNotIn("--source", family.stdout)

        action = run(Path.cwd(), "evidence", "sync", "--help")
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("--source", action.stdout)
        self.assertNotIn("--definition", action.stdout)
        self.assertIn("--id", action.stdout)
        self.assertIn("logical log base", action.stdout)

        retention = run(Path.cwd(), "retention", "add", "--help")
        self.assertEqual(retention.returncode, 0, retention.stderr)
        self.assertIn("disconnected-retention decision", retention.stdout)
        self.assertIn("--target", retention.stdout)

    def test_invalid_authoring_arguments_emit_a_structured_failure(self) -> None:
        failed = run(Path.cwd(), "evidence", "sync")

        self.assertEqual(failed.returncode, 2)
        payload = authoring_result(failed)
        self.assertEqual(payload["task"], "evidence.sync")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["code"], "cli.arguments.invalid")
        self.assertIn("cli.arguments.invalid", failed.stderr)

    def test_help_and_selected_family_imports_are_lazy(self) -> None:
        script_root = LOG.parent
        code = f"""
import json
import sys
sys.path.insert(0, {str(script_root)!r})
from log_commands.dispatcher import main
for arguments in (
    [\"evidence\", \"--help\"],
    [\"evidence\", \"sync\", \"--help\"],
    [\"data\", \"--help\"],
    [\"data\", \"update\", \"--help\"],
    [\"reorganize\", \"--help\"],
    [\"reorganize\", \"transfer\", \"--help\"],
    [\"init\", \"--help\"],
    [\"add\", \"--help\"],
):
    try:
        main(arguments)
    except SystemExit as error:
        assert error.code == 0
print(json.dumps({{
    \"evidence\": \"log_commands.evidence\" in sys.modules,
    \"retention\": \"log_commands.retention\" in sys.modules,
    \"data\": \"log_commands.data\" in sys.modules,
    \"materials\": \"log_commands.materials\" in sys.modules,
    \"reorganize\": \"log_commands.reorganize\" in sys.modules,
    \"transfer\": \"log_commands.reorganize_transfer\" in sys.modules,
    \"scaffold\": \"log_commands.scaffold\" in sys.modules,
    \"validation\": \"validation.controller\" in sys.modules,
    \"validation_engine\": \"validation.engine\" in sys.modules,
    \"numpy\": \"numpy\" in sys.modules,
}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {
                "evidence": False,
                "retention": False,
                "data": False,
                "materials": False,
                "reorganize": False,
                "transfer": False,
                "scaffold": False,
                "validation": False,
                "validation_engine": False,
                "numpy": False,
            },
        )

    def test_path_rejects_summary_and_entries_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            for path in (logical.with_suffix(".md"), logical / "entries"):
                result = run(
                    entry,
                    "retention",
                    "list",
                    "--path",
                    str(path),
                    "--entry",
                    "e001",
                )
                self.assertNotEqual(result.returncode, 0)

    def test_entry_resolution_uses_only_the_canonical_identity_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            malformed = entry.with_name("2026-09-03-e001-e002-study")
            entry.rename(malformed)

            canonical = run(
                malformed,
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            alias = run(
                malformed,
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e002",
            )
            self.assertEqual(canonical.returncode, 0, canonical.stderr)
            self.assertEqual(alias.returncode, 2)
            self.assertEqual(
                authoring_result(alias)["code"], "entry.identity.unresolved"
            )

    def test_context_inference_requires_exactly_one_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            inferred = run(entry, "retention", "list", "--entry", "e001")
            self.assertEqual(inferred.returncode, 0, inferred.stderr)
            self.assertEqual(authoring_result(inferred)["records"], [])

            nested = entry / "nested"
            nested_entry = nested / "entries" / "2026-09-04-e002-nested"
            nested_entry.mkdir(parents=True)
            (entry / "nested.md").write_text(
                "# Nested\n\n"
                "Validation: [latest completed report](nested/validation.md)\n\n"
                "Reproduction: [latest report](nested/reproduction.md)\n",
                encoding="utf-8",
            )
            ambiguous = run(nested_entry, "retention", "list", "--entry", "e002")
            self.assertEqual(ambiguous.returncode, 2)
            self.assertEqual(
                authoring_result(ambiguous)["code"], "log.context.ambiguous"
            )
            self.assertTrue(logical.is_dir())

    def test_explicit_log_selects_its_project_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            logical = target / "docs" / "study"
            logical.mkdir(parents=True)
            executable = target / ".conda" / "bin" / "python"
            executable.parent.mkdir(parents=True)
            executable.write_text("placeholder\n", encoding="utf-8")
            arguments = [
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e001",
            ]

            with (
                mock.patch.object(sys, "argv", [str(LOG), *arguments]),
                mock.patch.dict(os.environ, {"RESEARCH_LOG_PYTHON_REEXEC": "0"}),
                mock.patch("subprocess.check_output", return_value=f"{target}\n"),
                mock.patch(
                    "os.execve", side_effect=RuntimeError("selected interpreter")
                ) as execute,
            ):
                with self.assertRaisesRegex(RuntimeError, "selected interpreter"):
                    runpy.run_path(str(LOG), run_name="log_launcher_test")

            selected, argv, environment = execute.call_args.args
            expected = executable.resolve()
            self.assertEqual(selected, expected)
            self.assertEqual(argv, [str(expected), str(LOG), *arguments])
            self.assertEqual(environment["RESEARCH_LOG_PYTHON_REEXEC"], "1")


class LogValidationRouteTests(unittest.TestCase):
    def test_public_routes_preserve_one_log_discovery_and_root_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = fixture(root)
            common = ("--dry-run", "--recompute")
            current = run(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--path",
                str(logical),
                *common,
            )
            self.assertEqual(current.returncode, 0, current.stderr)
            current_payload = json.loads(current.stdout)
            self.assertEqual(current_payload["outcome"], "findings")
            self.assertFalse(current_payload["saved"])
            self.assertEqual(current_payload["schema"], "research-log-validation-run/1")

            new_discovery = run(root, "discover", "--root", str(root))
            self.assertEqual(new_discovery.returncode, 0, new_discovery.stderr)
            self.assertEqual(
                json.loads(new_discovery.stdout)["summaries"],
                [logical.with_suffix(".md").resolve().as_posix()],
            )

            root_run = run(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--root",
                str(root),
                *common,
            )
            self.assertEqual(root_run.returncode, current.returncode, root_run.stderr)
            root_payload = json.loads(root_run.stdout)
            self.assertEqual(root_payload["rows"], [current_payload])
            self.assertEqual(
                root_payload["schema"], "research-log-validation-root-run/1"
            )
            self.assertEqual(root_payload["findings_count"], 1)
            self.assertEqual(root_payload["error_count"], 0)

    def test_validation_does_not_load_mutation_families(self) -> None:
        script_root = LOG.parent
        code = f"""
import json
import sys
sys.path.insert(0, {str(script_root)!r})
from log_commands.dispatcher import main
try:
    main([\"validate\", \"run\", \"--path\", \"missing\", \"--dry-run\"])
except Exception:
    pass
print(json.dumps({{
    \"evidence\": \"log_commands.evidence\" in sys.modules,
    \"retention\": \"log_commands.retention\" in sys.modules,
}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {"evidence": False, "retention": False},
        )


class LogLockTests(unittest.TestCase):
    def test_exclusive_log_lock_rejects_entry_tools_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry = fixture(root)
            source = entry / "data" / "new.txt"
            source.write_text("new\n", encoding="utf-8")
            script = entry / "scripts" / "noop.py"
            script.write_text("pass\n", encoding="utf-8")
            operation_dir = logical / ".cache" / "research-log-operations"
            operation_dir.mkdir(parents=True)
            lock = operation_dir / "log.lock"
            tracked = (
                logical.with_suffix(".md"),
                entry / "e001.md",
                entry / "data.json",
            )
            before = {path: path.read_bytes() for path in tracked}

            with lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                authoring = run_log_process(
                    entry,
                    "data",
                    "update",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                    "results",
                    "--target",
                    "data/new.txt",
                )
                publishing = run_log_process(
                    root,
                    "validate",
                    "run",
                    "--format",
                    "json",
                    "--path",
                    str(logical),
                )
                dry_run = run_log_process(
                    root,
                    "validate",
                    "run",
                    "--format",
                    "json",
                    "--path",
                    str(logical),
                    "--dry-run",
                )
                runner = subprocess.run(
                    [sys.executable, str(PYRUN), "scripts/noop.py"],
                    cwd=entry,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(authoring.returncode, 2, authoring.stderr)
            self.assertIn("operation", authoring.stderr)
            for result in (publishing, dry_run):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(
                    "operation", json.loads(result.stdout)["error"]["message"]
                )
                self.assertEqual(result.stderr, "")
            self.assertEqual(runner.returncode, 1, runner.stderr)
            self.assertIn("operation conflict", runner.stderr)
            self.assertEqual(
                {path: path.read_bytes() for path in tracked},
                before,
            )
            self.assertFalse((logical / "validation.md").exists())

    def test_process_termination_releases_operation_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, _ = fixture(Path(directory))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(LOG.parent)
            code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), 'log.lock'):
    print('ready', flush=True)
    sys.stdin.readline()
"""
            holder = subprocess.Popen(
                [sys.executable, "-u", "-c", code, str(logical)],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            holder.terminate()
            holder.communicate(timeout=2)
            acquired = subprocess.run(
                [sys.executable, "-u", "-c", code, str(logical)],
                input="\n",
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertEqual(acquired.stdout.strip(), "ready")

    def test_entry_lock_excludes_same_entry_but_not_distinct_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, _ = fixture(Path(directory))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(LOG.parent)
            holder_code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), 'entry-e001.lock'):
    print('ready', flush=True)
    sys.stdin.readline()
"""
            acquire_code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), sys.argv[2]):
    print('acquired', flush=True)
"""
            holder = subprocess.Popen(
                [sys.executable, "-u", "-c", holder_code, str(logical)],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            distinct = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    acquire_code,
                    str(logical),
                    "entry-e002.lock",
                ],
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertEqual(distinct.stdout.strip(), "acquired", distinct.stderr)
            same = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    acquire_code,
                    str(logical),
                    "entry-e001.lock",
                ],
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertNotEqual(same.returncode, 0)
            self.assertIn("research-log operation is active", same.stderr)
            self.assertIsNotNone(holder.stdin)
            holder.stdin.write("\n")
            holder.stdin.flush()
            holder.communicate(timeout=2)


class PyrunQuarantineTests(unittest.TestCase):
    def test_malformed_execution_state_is_preserved_and_execution_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry = fixture(Path(directory))
            script = entry / "scripts" / "run.py"
            script.write_text(
                "from pathlib import Path\nPath('data/executed').write_text('yes')\n",
                encoding="utf-8",
            )
            malformed = b'{"broken":'
            (entry / "pyrun.json").write_bytes(malformed)

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pyrun.state.quarantined", result.stderr)
            self.assertEqual((entry / "pyrun.json.bak").read_bytes(), malformed)
            self.assertFalse((entry / "pyrun.json").exists())
            self.assertFalse((entry / "data" / "executed").exists())

    def test_quarantine_uses_first_unused_numbered_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry = fixture(Path(directory))
            script = entry / "scripts" / "run.py"
            script.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
            malformed = b"not-json\n"
            (entry / "pyrun.json").write_bytes(malformed)
            (entry / "pyrun.json.bak").write_bytes(b"first")
            (entry / "pyrun.json.2.bak").write_bytes(b"second")

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual((entry / "pyrun.json.3.bak").read_bytes(), malformed)
            self.assertEqual((entry / "pyrun.json.bak").read_bytes(), b"first")
            self.assertEqual((entry / "pyrun.json.2.bak").read_bytes(), b"second")

    def test_unsafe_execution_state_path_is_not_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry = fixture(root)
            script = entry / "scripts" / "run.py"
            script.write_text(
                "from pathlib import Path\nPath('data/executed').write_text('yes')\n",
                encoding="utf-8",
            )
            external = root / "external.json"
            external.write_bytes(b"not-json")
            current = entry / "pyrun.json"
            current.symlink_to(external)

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("pyrun.state.invalid", result.stderr)
            self.assertTrue(current.is_symlink())
            self.assertFalse((entry / "pyrun.json.bak").exists())
            self.assertFalse((entry / "data/executed").exists())


if __name__ == "__main__":
    unittest.main()
