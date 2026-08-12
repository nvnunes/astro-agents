from __future__ import annotations

import csv
import importlib.machinery
import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"
sys.path.insert(0, str(PYRUN.parent))
LOADER = importlib.machinery.SourceFileLoader("research_logging_pyrun", str(PYRUN))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
PYRUN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = PYRUN_MODULE
LOADER.exec_module(PYRUN_MODULE)


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)


def run_data_add(
    cwd: Path, name: str, data_type: str, location: str
) -> subprocess.CompletedProcess[str]:
    return run(
        [sys.executable, str(PYRUN), "data", "add", name, data_type, location],
        cwd=cwd,
    )


def data_rows(index: Path) -> list[dict[str, str]]:
    with index.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path.resolve()


def make_entry(root: Path) -> Path:
    (root / "log.md").write_text("# Log\n", encoding="utf-8")
    entry = root / "log" / "entries" / "2026-05-01-e001-test-entry"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    (entry / "data" / "input.csv").write_text("value\n1\n", encoding="utf-8")
    (entry / "data.csv").write_text(
        "name,type,location\ninput_csv,CSV,data/input.csv\n",
        encoding="utf-8",
    )
    (entry / "scripts" / "print_args.py").write_text(
        "import sys\nprint('\\n'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    (entry / "scripts" / "print_executable.py").write_text(
        "import sys\nprint(sys.executable)\n",
        encoding="utf-8",
    )
    return entry


class PyrunTests(unittest.TestCase):
    def test_resolves_project_log_and_data_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/print_args.py",
                    "<project>",
                    "<log>",
                    "<input_csv>",
                    "series=<input_csv>/file.npz",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], str(root))
            self.assertEqual(lines[1], str(root / "log"))
            self.assertEqual(lines[2], str(entry / "data" / "input.csv"))
            self.assertEqual(
                lines[3],
                f"series={entry / 'data' / 'input.csv'}/file.npz",
            )

    def test_preserves_uri_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").write_text(
                "name,type,location\n"
                "web_csv,URL,https://example.test/data.csv\n"
                "store_npz,URI,s3://bucket/data.npz\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/print_args.py",
                    "<web_csv>",
                    "<store_npz>",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["https://example.test/data.csv", "s3://bucket/data.npz"],
            )

    def test_data_add_creates_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").unlink()

            result = run_data_add(entry, "source_csv", "CSV", "data/source.csv")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                data_rows(entry / "data.csv"),
                [{"name": "source_csv", "type": "CSV", "location": "data/source.csv"}],
            )

    def test_data_add_appends_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run_data_add(entry, "output_npz", "NPZ", "data/output.npz")

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = data_rows(entry / "data.csv")
            self.assertEqual([row["name"] for row in rows], ["input_csv", "output_npz"])
            self.assertEqual((entry / "data.csv").stat().st_mode & 0o777, 0o644)

    def test_data_index_publication_syncs_file_then_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "data.csv"
            data = PYRUN_MODULE.DataIndex(
                rows=[{"name": "source", "type": "CSV", "location": "data/a.csv"}],
                locations={"source": "data/a.csv"},
            )
            events: list[str] = []
            real_fsync = os.fsync
            real_replace = os.replace

            def record_fsync(file_descriptor: int) -> None:
                events.append("fsync")
                real_fsync(file_descriptor)

            def record_replace(source: object, destination: object) -> None:
                events.append("replace")
                real_replace(source, destination)

            with (
                mock.patch.object(PYRUN_MODULE.os, "fsync", side_effect=record_fsync),
                mock.patch.object(
                    PYRUN_MODULE.os,
                    "replace",
                    side_effect=record_replace,
                ),
            ):
                PYRUN_MODULE._write_data_index(index, data)

            self.assertEqual(events, ["fsync", "replace", "fsync"])
            self.assertEqual(data_rows(index), data.rows)

    def test_concurrent_data_add_preserves_both_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").unlink()
            commands = [
                [
                    sys.executable,
                    str(PYRUN),
                    "data",
                    "add",
                    name,
                    "CSV",
                    f"data/{name}.csv",
                ]
                for name in ("first", "second")
            ]
            processes = [
                subprocess.Popen(
                    command,
                    cwd=entry,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for command in commands
            ]
            results = [process.communicate() for process in processes]

            for process, (_, stderr) in zip(processes, results):
                self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(
                {row["name"] for row in data_rows(entry / "data.csv")},
                {"first", "second"},
            )

    def test_data_add_rejects_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            index = entry / "data.csv"
            original = index.read_text(encoding="utf-8")

            result = run_data_add(entry, "input_csv", "CSV", "data/other.csv")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate data name 'input_csv'", result.stderr)
            self.assertEqual(index.read_text(encoding="utf-8"), original)

    def test_data_add_rejects_reserved_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            index = entry / "data.csv"
            original = index.read_text(encoding="utf-8")

            for name in ("project", "log", "theme"):
                with self.subTest(name=name):
                    result = run_data_add(entry, name, "CSV", "data/other.csv")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"reserved data name {name!r}", result.stderr)
                    self.assertEqual(index.read_text(encoding="utf-8"), original)

    def test_data_add_rejects_names_outside_token_grammar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            index = entry / "data.csv"
            original = index.read_text(encoding="utf-8")

            result = run_data_add(entry, "bad name", "CSV", "data/other.csv")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid data name 'bad name'", result.stderr)
            self.assertEqual(index.read_text(encoding="utf-8"), original)

    def test_rejects_noncanonical_data_index_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").write_text(
                "Name,type,location\ninput_csv,CSV,data/input.csv\n",
                encoding="utf-8",
            )

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<input_csv>"],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("header must be exactly name,type,location", result.stderr)

    def test_data_add_rejects_invalid_index_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            index = entry / "data.csv"
            original = "name,type,location\ninput_csv,,data/input.csv\n"
            index.write_text(original, encoding="utf-8")

            result = run_data_add(entry, "output_npz", "NPZ", "data/output.npz")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("malformed data index row 2", result.stderr)
            self.assertEqual(index.read_text(encoding="utf-8"), original)

    def test_data_add_requires_entry_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run_data_add(
                entry / "scripts", "output_npz", "NPZ", "data/output.npz"
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("data add must run from an entry root", result.stderr)

    def test_rejects_duplicate_data_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").write_text(
                "name,type,location\n"
                "input_csv,CSV,data/input.csv\n"
                "input_csv,CSV,data/other.csv\n",
                encoding="utf-8",
            )

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<input_csv>"],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate data name 'input_csv' on line 3", result.stderr)

    def test_rejects_non_utf8_data_index_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").write_bytes(b"name,type,location\ninput,CSV,\xff\n")

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<input>"],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("data index is not valid UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_rejects_reserved_name_in_existing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "data.csv").write_text(
                "name,type,location\nproject,CSV,data/input.csv\n",
                encoding="utf-8",
            )

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_args.py", "<project>"],
                cwd=entry,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reserved data name 'project'", result.stderr)

    def test_uses_project_conda_python_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            conda_python = root / ".conda" / "bin" / "python"
            conda_python.parent.mkdir(parents=True)
            conda_python.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' project-conda\n"
                f'exec {shlex.quote(sys.executable)} "$@"\n',
                encoding="utf-8",
            )
            conda_python.chmod(0o755)

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_executable.py"],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[0], "project-conda")

    def test_uses_runner_python_without_project_conda(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run(
                [sys.executable, str(PYRUN), "scripts/print_executable.py"],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                Path(result.stdout.strip()).resolve(),
                Path(sys.executable).resolve(),
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
