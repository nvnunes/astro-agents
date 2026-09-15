from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log, run_pyrun_process

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log(cwd, *arguments)


def run_pyrun(entry: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_pyrun_process(entry, "--cid", "build", "--", *arguments)


def result(value: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(value.stdout)


def scaffold(root: Path) -> tuple[Path, Path]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    logical = root / "docs" / "study"
    logical.parent.mkdir()
    initialized = run(
        root,
        "init",
        "--path",
        str(logical),
        "--title",
        "Study",
    )
    if initialized.returncode != 0:
        raise AssertionError(initialized.stderr)
    added = run(
        root,
        "add",
        "--path",
        str(logical),
        "--date",
        "2026-09-04",
        "--title",
        "Trial",
        "--slug",
        "trial",
    )
    if added.returncode != 0:
        raise AssertionError(added.stderr)
    entry = next((logical / "entries").iterdir())
    (entry / "data").mkdir()
    (entry / "scripts").mkdir()
    return logical, entry


def add_entry(logical: Path, *, date: str, slug: str) -> Path:
    added = run(
        logical.parent,
        "add",
        "--path",
        str(logical),
        "--date",
        date,
        "--title",
        slug.title(),
        "--slug",
        slug,
    )
    if added.returncode != 0:
        raise AssertionError(added.stderr)
    entry = next(path for path in (logical / "entries").iterdir() if slug in path.name)
    (entry / "data").mkdir()
    (entry / "scripts").mkdir()
    return entry


def data_inputs(entry: Path) -> list[dict[str, object]]:
    return json.loads((entry / "data.json").read_text(encoding="utf-8"))["inputs"]


def source_repository(root: Path) -> tuple[Path, str, str]:
    repository = root / "source-repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    source = repository / "source.txt"
    source.write_text("source\n", encoding="utf-8")
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
    blob = subprocess.check_output(
        ["git", "rev-parse", "HEAD:source.txt"], cwd=repository, text=True
    ).strip()
    return repository, commit, blob


class LogDataTests(unittest.TestCase):
    def test_removed_creation_routes_are_unknown_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            before = {p: p.read_bytes() for p in entry.rglob("*") if p.is_file()}
            for action in ("add-origin", "add-generated", "use", "remove"):
                rejected = run(
                    entry, "data", action, "--path", str(logical), "--entry", "e001"
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertIn("invalid choice", rejected.stderr)
            self.assertEqual(
                {p: p.read_bytes() for p in entry.rglob("*") if p.is_file()}, before
            )

    def test_help_exposes_only_graph_ownership_actions(self):
        help_result = run(Path.cwd(), "data", "--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("{update,rename,delete,list}", help_result.stdout)
        update = run(Path.cwd(), "data", "update", "--help")
        for flag in (
            "--boundary",
            "--kind",
            "--identity",
            "--reproduction-comparison",
            "--acknowledge-shared",
        ):
            self.assertIn(flag, update.stdout)
        for obsolete in ("--classification", "--byte-complete", "--commit"):
            self.assertNotIn(obsolete, update.stdout)

    def test_malformed_registry_is_preserved_and_routes_to_direct_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            registry = entry / "data.json"
            registry.write_bytes(b'{"broken":')
            for verb, extra in (
                ("list", ()),
                ("update", ("values", "--boundary", "origin")),
            ):
                rejected = run(
                    entry,
                    "data",
                    verb,
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                    *extra,
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(registry.read_bytes(), b'{"broken":')
                self.assertIn("data.json", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
