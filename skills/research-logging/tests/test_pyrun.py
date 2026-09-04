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
lock = Path.cwd().parents[1] / '.cache/research-log-operations/entry-e001.lock'
with lock.open('a+b') as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        state = 'locked'
    else:
        state = 'unlocked'
Path(a.results).write_text(state, encoding='utf-8')
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
                "locked",
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
            self.assertEqual(
                record["fingerprint"]["algorithm"], "directory-sha256-v1"
            )

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
            self.assertEqual(
                record["fingerprint"]["digest"],
                digest(entry / "data/output.csv"),
            )

            before = (entry / "pyrun-outputs.json").read_bytes()
            failed = run([*command, "--fail"], cwd=entry)
            self.assertEqual(failed.returncode, 3)
            self.assertEqual((entry / "pyrun-outputs.json").read_bytes(), before)

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
            self.assertEqual(
                record["fingerprint"]["algorithm"], "directory-sha256-v1"
            )
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
            self.assertEqual(
                (entry / "data/combined.log").read_text(), combined.stdout
            )

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
