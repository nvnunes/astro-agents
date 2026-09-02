from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"
sys.path.insert(0, str(PYRUN.parent))
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


def run_data(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, str(PYRUN), "data", *arguments], cwd=cwd)


def make_repo(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path.resolve()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
                    "schema": "research-log-data/v2",
                    "inputs": [
                        {
                            "name": "input_csv",
                            "kind": "file",
                            "location": "data/input.csv",
                            "fingerprint": {
                                "algorithm": "sha256",
                                "digest": digest(source),
                            },
                            "external": {
                                "source": "Fixture",
                                "identity": "input/v1",
                            },
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return entry


def inputs(entry: Path) -> list[dict[str, object]]:
    return json.loads((entry / "data.json").read_text(encoding="utf-8"))["inputs"]


class PyrunResolutionTests(unittest.TestCase):
    def test_resolves_project_log_file_directory_and_member_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            collection = entry / "data" / "collection"
            collection.mkdir()
            (collection / "member.npz").write_bytes(b"member")
            added = run_data(entry, "add", "collection", "directory", "data/collection")
            self.assertEqual(added.returncode, 0, added.stderr)

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

    def test_rejects_embedded_missing_and_unsafe_member_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            collection = entry / "data" / "collection"
            collection.mkdir()
            (collection / "member.npz").write_bytes(b"member")
            self.assertEqual(
                run_data(
                    entry, "add", "collection", "directory", "data/collection"
                ).returncode,
                0,
            )
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

    def test_remote_tokens_preserve_exact_uri(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            uri = "s3://archive/catalog.csv?versionId=v2"
            added = run_data(
                entry,
                "add-remote",
                "catalog",
                uri,
                "Archive",
                "catalog/v2",
                "versionId=v2",
            )
            self.assertEqual(added.returncode, 0, added.stderr)

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<catalog>"],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), uri)

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


class PyrunAuthoringTests(unittest.TestCase):
    def test_add_writes_fingerprint_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            result = run_data(entry, "add", "input_csv", "file", "data/input.csv")
            self.assertEqual(result.returncode, 0, result.stderr)
            item = inputs(entry)[0]
            self.assertEqual(item["name"], "input_csv")
            self.assertEqual(
                item["fingerprint"]["digest"], digest(entry / "data/input.csv")
            )
            self.assertEqual(stat.S_IMODE((entry / "data.json").stat().st_mode), 0o644)

            (entry / "data.json").chmod(0o640)
            (entry / "data" / "second.csv").write_text("value\n2\n", encoding="utf-8")
            result = run_data(entry, "add", "second", "file", "data/second.csv")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE((entry / "data.json").stat().st_mode), 0o640)
            self.assertEqual(
                [item["name"] for item in inputs(entry)], ["input_csv", "second"]
            )

    def test_duplicate_name_target_reserved_name_and_bad_kind_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            before = (entry / "data.json").read_bytes()
            commands = (
                ("add", "input_csv", "file", "data/input.csv"),
                ("add", "alias", "file", "data/input.csv"),
                ("add", "project", "file", "data/input.csv"),
                ("add", "bad.name", "file", "data/input.csv"),
                ("add", "bad_kind", "CSV", "data/input.csv"),
            )
            for command in commands:
                result = run_data(entry, *command)
                self.assertNotEqual(result.returncode, 0, command)
                self.assertEqual((entry / "data.json").read_bytes(), before)
                self.assertNotIn("Traceback", result.stderr)

    def test_update_external_refresh_and_remove_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            source = entry / "data" / "input.csv"
            source.write_text("value\n2\n", encoding="utf-8")
            refreshed = run_data(entry, "fingerprint", "input_csv")
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(inputs(entry)[0]["fingerprint"]["digest"], digest(source))

            external = run_data(
                entry, "external", "input_csv", "Updated fixture", "input/v2"
            )
            self.assertEqual(external.returncode, 0, external.stderr)
            self.assertEqual(inputs(entry)[0]["external"]["identity"], "input/v2")

            removed_boundary = run_data(entry, "external-remove", "input_csv")
            self.assertEqual(removed_boundary.returncode, 0, removed_boundary.stderr)
            self.assertNotIn("external", inputs(entry)[0])

            (entry / "data" / "replacement.csv").write_text(
                "value\n3\n", encoding="utf-8"
            )
            updated = run_data(
                entry, "update", "input_csv", "file", "data/replacement.csv"
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(inputs(entry)[0]["location"], "data/replacement.csv")

            removed = run_data(entry, "remove", "input_csv")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "data.json").exists())

    def test_add_and_update_identity_directory_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            build = entry / "build"
            build.mkdir()
            (build / "build.h5").write_text("state", encoding="utf-8")
            (build / "build.yaml").write_text("mode: test\n", encoding="utf-8")
            (build / "validation-run.json").write_text("{}\n", encoding="utf-8")

            added = run_data(
                entry,
                "add-identity-directory",
                "build",
                "build",
                "build.h5",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            fingerprint = inputs(entry)[0]["fingerprint"]
            self.assertEqual(fingerprint["algorithm"], "identity-files-sha256-v1")
            self.assertEqual(fingerprint["files"], ["build.h5"])

            updated = run_data(
                entry,
                "update-identity-directory",
                "build",
                "build",
                "validation-run.json",
                "build.h5",
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(
                inputs(entry)[0]["fingerprint"]["files"],
                ["build.h5", "validation-run.json"],
            )

    def test_add_and_update_identity_pattern_directory_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            build = entry / "build"
            build.mkdir()
            (build / "build.h5").write_text("state", encoding="utf-8")
            (build / "build.log").write_text("completed\n", encoding="utf-8")
            (build / "build.yaml").write_text("mode: test\n", encoding="utf-8")
            (build / "maps-hpx6.h5").write_text("map 6", encoding="utf-8")

            added = run_data(
                entry,
                "add-identity-pattern-directory",
                "build",
                "build",
                "build.h5",
                "build.log",
                "build.yaml",
                "maps-*.h5",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            fingerprint = inputs(entry)[0]["fingerprint"]
            self.assertEqual(fingerprint["algorithm"], "identity-patterns-sha256-v1")
            self.assertEqual(
                fingerprint["patterns"],
                ["build.h5", "build.log", "build.yaml", "maps-*.h5"],
            )

            (build / "maps-hpx6.h5").unlink()
            updated_without_maps = run_data(
                entry,
                "update-identity-pattern-directory",
                "build",
                "build",
                "build.h5",
                "build.log",
                "build.yaml",
                "maps-*.h5",
            )
            self.assertEqual(
                updated_without_maps.returncode, 0, updated_without_maps.stderr
            )

            (build / "maps-hpx9.h5").write_text("map 9", encoding="utf-8")
            updated = run_data(
                entry,
                "update-identity-pattern-directory",
                "build",
                "build",
                "build.h5",
                "build.log",
                "build.yaml",
                "maps-*.h5",
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertNotEqual(
                inputs(entry)[0]["fingerprint"]["digest"],
                fingerprint["digest"],
            )

    def test_remote_sha256_and_remote_boundary_removal_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            result = run_data(
                entry,
                "add-remote",
                "archive",
                "https://example.test/archive.csv",
                "Archive",
                "archive/v1",
                "sha256:" + "a" * 64,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(inputs(entry)[0]["fingerprint"]["algorithm"], "sha256")
            rejected = run_data(entry, "external-remove", "archive")
            self.assertNotEqual(rejected.returncode, 0)

    def test_malformed_json_and_wrong_working_directory_do_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            path = entry / "data.json"
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            before = path.read_bytes()
            result = run_data(entry, "add", "new", "file", "data/input.csv")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), before)

            result = run_data(entry / "data", "remove", "input_csv")
            self.assertNotEqual(result.returncode, 0)

    def test_concurrent_add_preserves_both_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root, with_data=False)
            for name in ("one", "two"):
                (entry / "data" / f"{name}.csv").write_text(
                    f"value\n{name}\n", encoding="utf-8"
                )
            environment = os.environ.copy()
            commands = [
                [
                    sys.executable,
                    str(PYRUN),
                    "data",
                    "add",
                    name,
                    "file",
                    f"data/{name}.csv",
                ]
                for name in ("one", "two")
            ]
            processes = [
                subprocess.Popen(
                    command,
                    cwd=entry,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                )
                for command in commands
            ]
            results = [process.communicate(timeout=10) for process in processes]
            self.assertEqual(
                [process.returncode for process in processes], [0, 0], results
            )
            self.assertEqual([item["name"] for item in inputs(entry)], ["one", "two"])


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
