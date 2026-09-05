from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"
sys.path.insert(0, str(PYRUN.parent))
DATA = importlib.import_module("research_log_data")
LOADER = importlib.machinery.SourceFileLoader("research_logging_pyrun", str(PYRUN))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
PYRUN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = PYRUN_MODULE
LOADER.exec_module(PYRUN_MODULE)


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def make_repo(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path.resolve()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit_source(repository: Path) -> str:
    source = repository / "tracked-source.txt"
    source.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", source.name], cwd=repository, check=True)
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
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()


def make_entry(root: Path, *, with_data: bool = True) -> Path:
    (root / "log.md").write_text("# Log\n", encoding="utf-8")
    entry = root / "log" / "entries" / "2026-05-01-e001-test-entry"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    source = entry / "data" / "input.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    (entry / "scripts" / "print_args.py").write_text(
        "import sys\nprint('\\n'.join(sys.argv[1:]))\n", encoding="utf-8"
    )
    (entry / "scripts" / "print_executable.py").write_text(
        "import sys\nprint(sys.executable)\n", encoding="utf-8"
    )
    if with_data:
        (entry / "data.json").write_text(
            json.dumps(
                {
                    "schema": "research-log-data/v3",
                    "inputs": [
                        {
                            "name": "input_csv",
                            "kind": "file",
                            "location": "data/input.csv",
                            "fingerprint": {
                                "algorithm": "sha256",
                                "digest": digest(source),
                            },
                            "origin": True,
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return entry


def add_directory_input(entry: Path, name: str, directory: Path) -> None:
    resource = DATA.build_local_input(
        name,
        "directory",
        directory.relative_to(entry).as_posix(),
        entry_root=entry,
        origin=True,
    )
    payload = json.loads((entry / "data.json").read_text(encoding="utf-8"))
    payload["inputs"].append(resource.as_dict())
    (entry / "data.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


class PyrunResolutionTests(unittest.TestCase):
    def test_environment_options_are_normalized_into_the_signature(self) -> None:
        first = PYRUN_MODULE.parse_pyrun_arguments(
            [
                "--env",
                "OMP_NUM_THREADS=2",
                "--env",
                "CUDA_VISIBLE_DEVICES=0",
                "--",
                "scripts/model.py",
                "--mode",
                "exact",
            ]
        )
        second = PYRUN_MODULE.parse_pyrun_arguments(
            [
                "--env",
                "CUDA_VISIBLE_DEVICES=0",
                "--env",
                "OMP_NUM_THREADS=2",
                "--",
                "scripts/model.py",
                "--mode",
                "exact",
            ]
        )

        self.assertEqual(first.environment, second.environment)
        self.assertEqual(first.parameters, second.parameters)
        self.assertEqual(
            first.parameters,
            (
                "--env",
                "CUDA_VISIBLE_DEVICES=0",
                "--env",
                "OMP_NUM_THREADS=2",
                "--",
                "--mode",
                "exact",
            ),
        )

    def test_environment_options_reject_invalid_and_runner_managed_names(self) -> None:
        cases = (
            ["--env", "missing", "--", "scripts/model.py"],
            ["--env", "MODE=one", "--env", "MODE=two", "--", "scripts/model.py"],
            ["--env", "MPLCONFIGDIR=/tmp/custom", "--", "scripts/model.py"],
            [
                "--env",
                "PYRUN_CODE_TRACE_DIRECTORY=/tmp/custom",
                "--",
                "scripts/model.py",
            ],
            ["--env", "XDG_CACHE_HOME=/tmp/custom", "--", "scripts/model.py"],
            ["--env", "MODE=one", "scripts/model.py"],
        )
        for arguments in cases:
            with (
                self.subTest(arguments=arguments),
                self.assertRaises(PYRUN_MODULE.PyrunContractError),
            ):
                PYRUN_MODULE.parse_pyrun_arguments(arguments)

    def test_other_role_layout_is_normalized_and_not_persisted(self) -> None:
        first = PYRUN_MODULE.parse_pyrun_arguments(
            [
                "--other-inputs",
                "weights,catalog",
                "--other-outputs",
                "results",
                "--",
                "scripts/model.py",
                "--catalog",
                "<input_csv>",
                "--weights",
                "<input_csv>",
                "--results",
                "data/results.csv",
            ]
        )
        second = PYRUN_MODULE.parse_pyrun_arguments(
            [
                "--other-outputs",
                "results",
                "--other-inputs",
                "catalog,weights",
                "--",
                "scripts/model.py",
                "--catalog",
                "<input_csv>",
                "--weights",
                "<input_csv>",
                "--results",
                "data/results.csv",
            ]
        )

        self.assertEqual(first.roles, second.roles)
        self.assertEqual(first.parameters, second.parameters)
        self.assertEqual(
            first.parameters,
            (
                "--catalog",
                "<input_csv>",
                "--weights",
                "<input_csv>",
                "--results",
                "data/results.csv",
            ),
        )

    def test_other_roles_and_capture_have_distinct_signature_ownership(self) -> None:
        layout = PYRUN_MODULE.parse_pyrun_arguments(
            [
                "--other-outputs",
                "results",
                "--capture-stdout",
                "data/run.log",
                "--",
                "scripts/model.py",
                "--results",
                "data/results.csv",
            ]
        )

        self.assertEqual(
            layout.parameters,
            (
                "--capture-stdout",
                "data/run.log",
                "--",
                "--results",
                "data/results.csv",
            ),
        )

    def test_resolves_project_log_file_directory_and_member_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            collection = entry / "data" / "collection"
            collection.mkdir()
            (collection / "member.npz").write_bytes(b"member")
            add_directory_input(entry, "collection", collection)

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/print_args.py",
                    "<project>",
                    "<log>",
                    "<input_csv>",
                    "<collection>",
                    "<collection>/member.npz",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(root),
                    str(root / "log"),
                    str((entry / "data" / "input.csv").resolve()),
                    str(collection.resolve()),
                    str((collection / "member.npz").resolve()),
                ],
            )

    def test_git_repository_requires_and_records_both_projections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            commit = commit_source(root)
            entry = make_entry(root)
            resource = DATA.build_git_repository_input(
                "source-repository",
                root.as_posix(),
                commit,
                entry_root=entry,
            )
            payload = json.loads((entry / "data.json").read_text(encoding="utf-8"))
            payload["inputs"].append(resource.as_dict())
            (entry / "data.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            script = entry / "scripts" / "record_repository.py"
            script.write_text(
                "import argparse, json\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--input-repository')\n"
                "p.add_argument('--input-commit')\n"
                "p.add_argument('--output-json'); a=p.parse_args()\n"
                "open(a.output_json, 'w').write(json.dumps({"
                "'repository': a.input_repository, 'commit': a.input_commit}))\n",
                encoding="utf-8",
            )

            for token in ("<source-repository>", "<source-repository:commit>"):
                incomplete = run(
                    [
                        sys.executable,
                        str(PYRUN),
                        "scripts/record_repository.py",
                        "--input-repository",
                        token,
                        "--output-json",
                        "data/repository.json",
                    ],
                    cwd=entry,
                )
                self.assertNotEqual(incomplete.returncode, 0)
                self.assertIn("data.git.projection_missing", incomplete.stderr)
                self.assertFalse((entry / "data" / "repository.json").exists())

            completed = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/record_repository.py",
                    "--input-repository",
                    "<source-repository>",
                    "--input-commit",
                    "<source-repository:commit>",
                    "--output-json",
                    "data/repository.json",
                ],
                cwd=entry,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            generated = json.loads(
                (entry / "data" / "repository.json").read_text(encoding="utf-8")
            )
            self.assertEqual(generated, {"repository": str(root), "commit": commit})
            support = json.loads(
                (entry / "pyrun-outputs.json").read_text(encoding="utf-8")
            )["outputs"]["data/repository.json"]
            self.assertEqual(
                support["inputs"],
                {"source-repository": resource.fingerprint.as_dict()},
            )

    def test_rejects_embedded_missing_and_unsafe_member_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            collection = entry / "data" / "collection"
            collection.mkdir()
            (collection / "member.npz").write_bytes(b"member")
            add_directory_input(entry, "collection", collection)
            commands = (
                "label=<input_csv>",
                "<missing>",
                "<collection>/../input.csv",
                "<collection>/missing.npz",
            )
            for argument in commands:
                result = run(
                    [
                        sys.executable,
                        str(PYRUN),
                        "scripts/print_args.py",
                        argument,
                    ],
                    cwd=entry,
                )
                self.assertNotEqual(result.returncode, 0, argument)
                self.assertNotIn("Traceback", result.stderr)

    def test_fingerprint_drift_blocks_execution_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            path = entry / "data.json"
            before = path.read_bytes()
            (entry / "data" / "input.csv").write_text("value\n2\n", encoding="utf-8")

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<input_csv>"],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("data.fingerprint.mismatch", result.stderr)
            self.assertEqual(path.read_bytes(), before)

    def test_rejects_legacy_mixed_parent_and_log_level_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            for path in (
                entry / "data.csv",
                entry.parent / "data.json",
                entry.parent.parent / "data.json",
            ):
                path.write_text("legacy\n", encoding="utf-8")
                result = run(
                    [sys.executable, str(PYRUN), "scripts/print_args.py", "plain"],
                    cwd=entry,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                path.unlink()


class PyrunOutputSupportTests(unittest.TestCase):
    def test_execution_receives_explicit_and_isolated_managed_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/environment.py").write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[1]).write_text(json.dumps({name: os.environ[name] "
                "for name in ('MODE', 'MPLCONFIGDIR', 'XDG_CACHE_HOME')}))\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--env",
                    "MODE=exact",
                    "--other-outputs",
                    "@1",
                    "--",
                    "scripts/environment.py",
                    "data/environment.json",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(
                (entry / "data/environment.json").read_text(encoding="utf-8")
            )
            self.assertEqual(observed["MODE"], "exact")
            self.assertNotEqual(
                observed["MPLCONFIGDIR"], os.environ.get("MPLCONFIGDIR")
            )
            self.assertNotEqual(
                observed["XDG_CACHE_HOME"], os.environ.get("XDG_CACHE_HOME")
            )
            self.assertFalse(Path(observed["MPLCONFIGDIR"]).exists())
            self.assertFalse(Path(observed["XDG_CACHE_HOME"]).exists())

    def test_execution_and_output_publication_hold_the_stable_entry_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/check_lock.py").write_text(
                """import argparse
import fcntl
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--results')
a = p.parse_args()
operations = Path.cwd().parents[1] / '.cache/research-log-operations'
states = []
for name in ('log.lock', 'entry-e001.lock'):
    lock = operations / name
    handle = lock.open('a+b')
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            states.append('locked')
        else:
            states.append('unlocked')
    finally:
        handle.close()
Path(a.results).write_text(','.join(states), encoding='utf-8')
""",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--other-outputs",
                    "results",
                    "--",
                    "scripts/check_lock.py",
                    "--results",
                    "data/lock-state.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (entry / "data/lock-state.txt").read_text(encoding="utf-8"),
                "locked,locked",
            )

    def test_other_roles_publish_support_without_entering_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/build_other.py").write_text(
                """import argparse
p = argparse.ArgumentParser()
p.add_argument('--catalog')
p.add_argument('--results')
a = p.parse_args()
open(a.results, 'wb').write(open(a.catalog, 'rb').read())
""",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "--other-inputs",
                "catalog",
                "--other-outputs",
                "results",
                "--",
                "scripts/build_other.py",
                "--catalog",
                "<input_csv>",
                "--results",
                "data/results.csv",
            ]

            result = run(command, cwd=entry)

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/results.csv"
            ]
            self.assertEqual(
                record["parameters"],
                ["--catalog", "<input_csv>", "--results", "data/results.csv"],
            )
            self.assertEqual(set(record["inputs"]), {"input_csv"})

    def test_other_role_overrides_a_misleading_automatic_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/override.py").write_text(
                """import argparse
p = argparse.ArgumentParser()
p.add_argument('--output-source')
p.add_argument('--result')
a = p.parse_args()
open(a.result, 'wb').write(open(a.output_source, 'rb').read())
""",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--other-inputs",
                    "output-source",
                    "--other-outputs",
                    "result",
                    "--",
                    "scripts/override.py",
                    "--output-source",
                    "<input_csv>",
                    "--result",
                    "data/result.csv",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"]
            self.assertEqual(set(outputs), {"data/result.csv"})

    def test_other_output_can_select_a_positional_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/positional_directory.py").write_text(
                """from pathlib import Path
import sys
target = Path(sys.argv[1])
target.mkdir()
(target / 'result.csv').write_text('value\\n1\\n', encoding='utf-8')
""",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--other-outputs",
                    "@1",
                    "--",
                    "scripts/positional_directory.py",
                    "data/results",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/results"
            ]
            self.assertEqual(record["fingerprint"]["algorithm"], "directory-sha256-v1")

    def test_absent_other_output_publishes_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--other-outputs",
                    "result",
                    "--",
                    "scripts/print_args.py",
                    "--result",
                    "data/absent.csv",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_other_role_contract_rejects_invalid_forms_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            forms = (
                ["--other-inputs", "catalog", "scripts/print_args.py"],
                ["--other-inputs", "", "--", "scripts/print_args.py"],
                ["--other-inputs", "a, a", "--", "scripts/print_args.py"],
                ["--other-inputs", "a,a", "--", "scripts/print_args.py"],
                [
                    "--other-inputs",
                    "value",
                    "--other-inputs",
                    "other",
                    "--",
                    "scripts/print_args.py",
                    "--value",
                    "<input_csv>",
                    "--other",
                    "<input_csv>",
                ],
                [
                    "--other-inputs",
                    "value",
                    "--other-outputs",
                    "value",
                    "--",
                    "scripts/print_args.py",
                    "--value",
                    "<input_csv>",
                ],
                ["--other-outputs", "missing", "--", "scripts/print_args.py"],
            )
            for arguments in forms:
                with self.subTest(arguments=arguments):
                    result = run([sys.executable, str(PYRUN), *arguments], cwd=entry)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_success_records_exact_output_support_and_failed_run_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/build.py").write_text(
                """import argparse
p = argparse.ArgumentParser()
p.add_argument('--input-data')
p.add_argument('--output-data')
p.add_argument('--fail', action='store_true')
a = p.parse_args()
if a.fail:
    raise SystemExit(3)
open(a.output_data, 'wb').write(open(a.input_data, 'rb').read())
""",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "scripts/build.py",
                "--input-data",
                "<input_csv>",
                "--output-data",
                "data/output.csv",
            ]

            result = run(command, cwd=entry)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((entry / "pyrun-outputs.json").read_text())
            self.assertEqual(payload["schema"], "research-log-pyrun-outputs/v1")
            record = payload["outputs"]["data/output.csv"]
            self.assertIs(record["confirmed"], True)
            self.assertEqual(record["script"]["path"], "scripts/build.py")
            self.assertEqual(record["parameters"], command[3:])
            self.assertEqual(set(record["inputs"]), {"input_csv"})
            self.assertEqual(record["code"], {})
            self.assertEqual(
                record["fingerprint"]["digest"],
                digest(entry / "data/output.csv"),
            )

            before = (entry / "pyrun-outputs.json").read_bytes()
            failed = run([*command, "--fail"], cwd=entry)
            self.assertEqual(failed.returncode, 3)
            self.assertEqual((entry / "pyrun-outputs.json").read_bytes(), before)

    def test_records_only_loaded_direct_transitive_and_dynamic_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/transitive_helper.py").write_text(
                "VALUE = 'transitive'\n", encoding="utf-8"
            )
            (entry / "scripts/direct_helper.py").write_text(
                "from transitive_helper import VALUE\n", encoding="utf-8"
            )
            (entry / "scripts/dynamic_helper.py").write_text(
                "VALUE = 'dynamic'\n", encoding="utf-8"
            )
            (entry / "scripts/not_loaded.py").write_text(
                "VALUE = 'absent'\n", encoding="utf-8"
            )
            (entry / "scripts/build_code.py").write_text(
                "import importlib\n"
                "from pathlib import Path\n"
                "import direct_helper as first\n"
                "import direct_helper as second\n"
                "dynamic = importlib.import_module('dynamic_helper')\n"
                "Path('data/code.txt').write_text(first.VALUE + dynamic.VALUE)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_code.py",
                    "--output-data",
                    "data/code.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/code.txt"
            ]
            self.assertEqual(
                record["code"],
                {
                    name: {"algorithm": "sha256", "digest": digest(entry / name)}
                    for name in (
                        "scripts/direct_helper.py",
                        "scripts/dynamic_helper.py",
                        "scripts/transitive_helper.py",
                    )
                },
            )

    def test_records_log_sibling_and_logical_symlink_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            log = entry.parents[1]
            sibling = log / "entries/2026-05-02-e002-sibling"
            sibling.mkdir()
            (log / "shared_helper.py").write_text("VALUE = 'shared'\n")
            (sibling / "sibling_helper.py").write_text("VALUE = 'sibling'\n")
            storage = root / "relocated"
            storage.mkdir()
            (storage / "linked_helper.py").write_text("VALUE = 'linked'\n")
            (log / "linked").symlink_to(storage, target_is_directory=True)
            (entry / "scripts/build_scoped.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "log = Path.cwd().parents[1]\n"
                "sys.path.insert(0, str(log))\n"
                "import shared_helper\n"
                f"sys.path.insert(0, str(log / 'entries/{sibling.name}'))\n"
                "import sibling_helper\n"
                "sys.path.insert(0, str(log / 'linked'))\n"
                "import linked_helper\n"
                "Path('data/scoped.txt').write_text("
                "shared_helper.VALUE + sibling_helper.VALUE + linked_helper.VALUE)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_scoped.py",
                    "--output-data",
                    "data/scoped.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            code = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/scoped.txt"
            ]["code"]
            self.assertEqual(
                set(code),
                {
                    "<log>/shared_helper.py",
                    f"<log>/entries/{sibling.name}/sibling_helper.py",
                    "<log>/linked/linked_helper.py",
                },
            )
            self.assertEqual(
                code["<log>/linked/linked_helper.py"]["digest"],
                digest(storage / "linked_helper.py"),
            )

    def test_records_python_child_entry_points_and_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/child_helper.py").write_text(
                "VALUE = 'child'\n", encoding="utf-8"
            )
            (entry / "scripts/child.py").write_text(
                "import child_helper\n", encoding="utf-8"
            )
            (entry / "scripts/build_children.py").write_text(
                "import os, subprocess, sys\n"
                "from pathlib import Path\n"
                "child = str(Path('scripts/child.py').resolve())\n"
                "subprocess.run([sys.executable, child], check=True)\n"
                "environment = dict(os.environ, PYTHON=sys.executable, CHILD=child)\n"
                "subprocess.run(['sh', '-c', '\"$PYTHON\" \"$CHILD\"'], "
                "env=environment, check=True)\n"
                "Path('data/children.txt').write_text('done')\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_children.py",
                    "--output-data",
                    "data/children.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            code = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/children.txt"
            ]["code"]
            self.assertEqual(set(code), {"scripts/child.py", "scripts/child_helper.py"})

    def test_records_spawn_and_fork_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/process_helper.py").write_text(
                "VALUE = 'process'\n", encoding="utf-8"
            )
            (entry / "scripts/process_worker.py").write_text(
                "def load():\n    import process_helper\n",
                encoding="utf-8",
            )
            (entry / "scripts/build_process.py").write_text(
                "import multiprocessing\n"
                "import sys\n"
                "from pathlib import Path\n"
                "from process_worker import load\n"
                "if __name__ == '__main__':\n"
                "    process = multiprocessing.get_context(sys.argv[1]).Process("
                "target=load)\n"
                "    process.start(); process.join()\n"
                "    if process.exitcode:\n        raise SystemExit(process.exitcode)\n"
                "    Path(sys.argv[2]).write_text('done')\n",
                encoding="utf-8",
            )
            methods = set(__import__("multiprocessing").get_all_start_methods())
            for method in ("spawn", "fork"):
                if method not in methods:
                    continue
                with self.subTest(method=method):
                    output = f"data/{method}.txt"
                    result = run(
                        [
                            sys.executable,
                            str(PYRUN),
                            "--other-outputs",
                            "@2",
                            "--",
                            "scripts/build_process.py",
                            method,
                            output,
                        ],
                        cwd=entry,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    code = json.loads((entry / "pyrun-outputs.json").read_text())[
                        "outputs"
                    ][output]["code"]
                    self.assertEqual(
                        set(code),
                        {"scripts/process_helper.py", "scripts/process_worker.py"},
                    )

    def test_changed_loaded_helper_publishes_no_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            helper = entry / "scripts/changing_helper.py"
            helper.write_text("VALUE = 'before'\n", encoding="utf-8")
            (entry / "scripts/change_code.py").write_text(
                "from pathlib import Path\n"
                "import changing_helper\n"
                "Path('data/changed.txt').write_text('done')\n"
                "Path('scripts/changing_helper.py').write_text("
                "\"VALUE = 'after'\\n\")\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/change_code.py",
                    "--output-data",
                    "data/changed.txt",
                ],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("loaded code changed during execution", result.stderr)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_missing_root_trace_publishes_no_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/skip_shutdown.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "Path('data/skip.txt').write_text('done')\n"
                "os._exit(0)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/skip_shutdown.py",
                    "--output-data",
                    "data/skip.txt",
                ],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root Python process left no complete trace", result.stderr)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_excessive_loaded_code_publishes_no_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            for index in range(PYRUN_MODULE.MAX_CODE_PATHS + 1):
                (entry / f"scripts/helper_{index}.py").write_text(
                    f"VALUE = {index}\n", encoding="utf-8"
                )
            (entry / "scripts/build_excessive.py").write_text(
                "import importlib\n"
                "from pathlib import Path\n"
                f"for index in range({PYRUN_MODULE.MAX_CODE_PATHS + 1}):\n"
                "    importlib.import_module(f'helper_{index}')\n"
                "Path('data/excessive.txt').write_text('done')\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_excessive.py",
                    "--output-data",
                    "data/excessive.txt",
                ],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("code_path_limit", result.stderr)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_excludes_project_code_outside_the_current_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (root / "project_helper.py").write_text("VALUE = 'project'\n")
            (entry / "scripts/build_external.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path.cwd().parents[2]))\n"
                "import project_helper\n"
                "Path('data/external.txt').write_text(project_helper.VALUE)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_external.py",
                    "--output-data",
                    "data/external.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/external.txt"
            ]
            self.assertEqual(record["code"], {})

    def test_preserves_an_existing_sitecustomize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            startup = root / "startup"
            startup.mkdir()
            marker = root / "sitecustomize-loaded.txt"
            (startup / "sitecustomize.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['SITE_MARKER']).write_text('loaded')\n",
                encoding="utf-8",
            )
            (entry / "scripts/check_sitecustomize.py").write_text(
                "import sitecustomize\n"
                "from pathlib import Path\n"
                "Path('data/site.txt').write_text(sitecustomize.__file__)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--env",
                    f"PYTHONPATH={startup}",
                    "--env",
                    f"SITE_MARKER={marker}",
                    "--",
                    "scripts/check_sitecustomize.py",
                    "--output-data",
                    "data/site.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(), "loaded")
            self.assertEqual(
                Path((entry / "data/site.txt").read_text()).resolve(),
                (startup / "sitecustomize.py").resolve(),
            )

    def test_many_short_children_share_one_deduplicated_code_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/shared_child_helper.py").write_text(
                "VALUE = 'child'\n", encoding="utf-8"
            )
            (entry / "scripts/short_child.py").write_text(
                "import shared_child_helper\n", encoding="utf-8"
            )
            (entry / "scripts/build_many_children.py").write_text(
                "import subprocess, sys\n"
                "from pathlib import Path\n"
                "child = str(Path('scripts/short_child.py').resolve())\n"
                "for _ in range(12):\n"
                "    subprocess.run([sys.executable, child], check=True)\n"
                "Path('data/first.txt').write_text('first')\n"
                "Path('data/second.txt').write_text('second')\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_many_children.py",
                    "--output-first",
                    "data/first.txt",
                    "--output-second",
                    "data/second.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"]
            expected = {"scripts/shared_child_helper.py", "scripts/short_child.py"}
            self.assertEqual(set(outputs["data/first.txt"]["code"]), expected)
            self.assertEqual(
                outputs["data/first.txt"]["code"],
                outputs["data/second.txt"]["code"],
            )

    def test_isolated_and_detached_children_add_no_dependency_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/hidden_helper.py").write_text(
                "VALUE = 'hidden'\n", encoding="utf-8"
            )
            (entry / "scripts/hidden_child.py").write_text(
                "VALUE = 'isolated'\n", encoding="utf-8"
            )
            (entry / "scripts/detached_child.py").write_text(
                "import time\ntime.sleep(0.2)\nimport hidden_helper\n",
                encoding="utf-8",
            )
            (entry / "scripts/build_unobserved.py").write_text(
                "import subprocess, sys\n"
                "from pathlib import Path\n"
                "subprocess.run([sys.executable, '-I', "
                "'scripts/hidden_child.py'], check=True)\n"
                "subprocess.Popen([sys.executable, 'scripts/detached_child.py'], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL, start_new_session=True)\n"
                "Path('data/unobserved.txt').write_text('done')\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build_unobserved.py",
                    "--output-data",
                    "data/unobserved.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"][
                "data/unobserved.txt"
            ]
            self.assertEqual(record["code"], {})

    def test_success_records_directory_output_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/build_directory.py").write_text(
                """import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--output-dir')
a = p.parse_args()
target = Path(a.output_dir)
target.mkdir()
(target / 'result.csv').write_text('value\\n1\\n', encoding='utf-8')
""",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "scripts/build_directory.py",
                "--output-dir",
                "data/trials",
            ]

            result = run(command, cwd=entry)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((entry / "pyrun-outputs.json").read_text())
            record = payload["outputs"]["data/trials"]
            self.assertIs(record["confirmed"], True)
            self.assertEqual(record["fingerprint"]["algorithm"], "directory-sha256-v1")
            self.assertEqual(record["parameters"], command[3:])

    def test_success_records_portable_project_file_and_directory_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/build_project.py").write_text(
                """import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--output-file')
p.add_argument('--output-dir')
a = p.parse_args()
file = Path(a.output_file)
file.parent.mkdir(parents=True, exist_ok=True)
file.write_text('value\\n1\\n', encoding='utf-8')
target = Path(a.output_dir)
target.mkdir(parents=True)
(target / 'member.csv').write_text('value\\n2\\n', encoding='utf-8')
""",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "scripts/build_project.py",
                "--output-file",
                "<project>/artifacts/result.csv",
                "--output-dir",
                "<project>/artifacts/trials",
            ]

            result = run(command, cwd=entry)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((entry / "pyrun-outputs.json").read_text())
            file_record = payload["outputs"]["<project>/artifacts/result.csv"]
            directory_record = payload["outputs"]["<project>/artifacts/trials"]
            self.assertEqual(file_record["fingerprint"]["algorithm"], "sha256")
            self.assertEqual(
                directory_record["fingerprint"]["algorithm"],
                "directory-sha256-v1",
            )
            self.assertEqual(file_record["parameters"], command[3:])

    def test_project_outputs_reject_nonportable_and_escaping_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            root = make_repo(root)
            entry = make_entry(root)
            outside = Path(directory) / "outside"
            outside.mkdir()
            (root / "escaped").symlink_to(outside, target_is_directory=True)
            (root / "aliased").symlink_to(root / "artifacts", target_is_directory=True)
            invalid = (
                str(root / "absolute.csv"),
                str(outside / "outside.csv"),
                "../../outside.csv",
                "<project>",
                "<project>/../outside.csv",
                "<project>//outside.csv",
                "<project>/escaped/outside.csv",
                "<project>/aliased/outside.csv",
            )

            for target in invalid:
                with self.subTest(target=target):
                    result = run(
                        [
                            sys.executable,
                            str(PYRUN),
                            "scripts/print_args.py",
                            "--output-data",
                            target,
                        ],
                        cwd=entry,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_duplicate_entry_and_project_spellings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            project_spelling = (
                "<project>/" + (entry / "data/output.csv").relative_to(root).as_posix()
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/print_args.py",
                    "--output-data",
                    "data/output.csv",
                    "--output-data",
                    project_spelling,
                ],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate output target", result.stderr)

    def test_input_change_during_execution_publishes_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/mutate_input.py").write_text(
                """import argparse
p = argparse.ArgumentParser()
p.add_argument('--input-data')
p.add_argument('--output-data')
a = p.parse_args()
content = open(a.input_data, 'rb').read()
open(a.output_data, 'wb').write(content)
open(a.input_data, 'wb').write(b'value\\n2\\n')
""",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/mutate_input.py",
                    "--input-data",
                    "<input_csv>",
                    "--output-data",
                    "data/output.csv",
                ],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("data.fingerprint.mismatch", result.stderr)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_capture_options_mirror_and_record_stream_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/streams.py").write_text(
                "import sys\nprint('out')\nprint('err', file=sys.stderr)\n",
                encoding="utf-8",
            )

            separate = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--capture-stdout",
                    "data/out.log",
                    "--capture-stderr",
                    "data/err.log",
                    "--",
                    "scripts/streams.py",
                ],
                cwd=entry,
            )

            self.assertEqual(separate.returncode, 0, separate.stderr)
            self.assertEqual(separate.stdout, "out\n")
            self.assertEqual(separate.stderr, "err\n")
            self.assertEqual((entry / "data/out.log").read_text(), "out\n")
            self.assertEqual((entry / "data/err.log").read_text(), "err\n")
            outputs = json.loads((entry / "pyrun-outputs.json").read_text())["outputs"]
            self.assertEqual(set(outputs), {"data/err.log", "data/out.log"})

            combined = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "--capture-stdout-stderr",
                    "data/combined.log",
                    "--",
                    "scripts/streams.py",
                ],
                cwd=entry,
            )
            self.assertEqual(combined.returncode, 0, combined.stderr)
            self.assertIn("out\n", combined.stdout)
            self.assertIn("err\n", combined.stdout)
            self.assertEqual((entry / "data/combined.log").read_text(), combined.stdout)

    def test_capture_contract_rejects_ambiguous_forms_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            forms = (
                ["--capture-stdout", "data/a.log", "scripts/print_args.py"],
                [
                    "--capture-stdout",
                    "data/a.log",
                    "--capture-stdout",
                    "data/b.log",
                    "--",
                    "scripts/print_args.py",
                ],
                [
                    "--capture-stdout",
                    "data/a.log",
                    "--capture-stderr",
                    "data/a.log",
                    "--",
                    "scripts/print_args.py",
                ],
                [
                    "--capture-stdout-stderr",
                    "data/a.log",
                    "--capture-stderr",
                    "data/b.log",
                    "--",
                    "scripts/print_args.py",
                ],
            )
            for arguments in forms:
                with self.subTest(arguments=arguments):
                    result = run([sys.executable, str(PYRUN), *arguments], cwd=entry)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_closed_mirror_does_not_stop_capture_or_deadlock_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            byte_count = 8 * 1024 * 1024
            (entry / "scripts/large_stdout.py").write_text(
                f"import sys\nsys.stdout.buffer.write(b'x' * {byte_count})\n",
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(PYRUN),
                    "--capture-stdout",
                    "data/run.log",
                    "--",
                    "scripts/large_stdout.py",
                ],
                cwd=entry,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdout.read(1)
            process.stdout.close()
            try:
                returncode = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                self.fail("pyrun deadlocked after its mirror pipe closed")
            stderr = process.stderr.read().decode()
            process.stderr.close()

            self.assertNotEqual(returncode, 0)
            self.assertIn("stream mirror failed", stderr)
            self.assertEqual((entry / "data/run.log").stat().st_size, byte_count)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

    def test_capture_write_failure_still_drains_the_source(self) -> None:
        class FailingCapture(io.BytesIO):
            def write(self, value: bytes) -> int:
                raise OSError("capture unavailable")

        content = b"x" * (2 * 1024 * 1024)
        source = io.BytesIO(content)
        capture_failed = PYRUN_MODULE.threading.Event()
        errors: list[BaseException] = []

        PYRUN_MODULE._pump_captured_stream(
            source,
            FailingCapture(),
            io.BytesIO(),
            capture_failed,
            errors,
            [],
            PYRUN_MODULE.threading.Lock(),
        )

        self.assertTrue(capture_failed.is_set())
        self.assertEqual(source.tell(), len(content))
        self.assertEqual(str(errors[0]), "capture unavailable")

    def test_malformed_json_blocks_execution_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            path = entry / "data.json"
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            before = path.read_bytes()
            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "plain"],
                cwd=entry,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), before)


class PyrunRuntimeTests(unittest.TestCase):
    def test_uses_project_conda_python_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            conda_python = root / ".conda" / "bin" / "python"
            conda_python.parent.mkdir(parents=True)
            conda_python.symlink_to(Path(sys.executable))
            result = run(
                [sys.executable, str(PYRUN), "scripts/print_executable.py"], cwd=entry
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(conda_python))

    def test_uses_runner_python_without_project_conda(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            result = run(
                [sys.executable, str(PYRUN), "scripts/print_executable.py"], cwd=entry
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                Path(result.stdout.strip()).resolve(), Path(sys.executable).resolve()
            )

    def test_rejects_theme_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<theme>"],
                cwd=entry,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("<theme> is no longer supported", result.stderr)


if __name__ == "__main__":
    unittest.main()
