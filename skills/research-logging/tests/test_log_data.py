from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log(cwd, *arguments)


def run_pyrun(entry: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    return subprocess.run(
        [str(entry / "pyrun"), *arguments],
        cwd=entry,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


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
    def test_help_is_progressive(self) -> None:
        family = run(Path.cwd(), "data", "--help")
        action = run(Path.cwd(), "data", "add-origin", "--help")
        generated = run(Path.cwd(), "data", "add-generated", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertIn("add-origin", family.stdout)
        self.assertNotIn("--identity", family.stdout)
        self.assertIn("--identity", action.stdout)
        self.assertIn("--commit", action.stdout)
        self.assertIn("producerless material input", action.stdout)
        self.assertIn("logical log base", action.stdout)
        self.assertNotIn("--pending-confirmation", action.stdout)
        self.assertIn("--pending-confirmation", generated.stdout)

    def test_add_origin_infers_kind_normalizes_and_lists_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            collection = entry / "data" / "collection"
            collection.mkdir()
            (collection / "member.txt").write_text("member\n", encoding="utf-8")

            first = run(
                entry,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "source",
                str(source),
            )
            second = run(
                entry,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "collection",
                "data/unused/../collection",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            inputs = data_inputs(entry)
            self.assertEqual(
                [(item["name"], item["kind"], item["location"]) for item in inputs],
                [
                    ("collection", "directory", "data/collection"),
                    ("source", "file", "data/source.csv"),
                ],
            )

            listed = run(
                entry,
                "data",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(
                result(listed)["records"],
                [
                    {
                        "classification": "origin",
                        "kind": "directory",
                        "name": "collection",
                        "target": "data/collection",
                    },
                    {
                        "classification": "origin",
                        "kind": "file",
                        "name": "source",
                        "target": "data/source.csv",
                    },
                ],
            )

    def test_git_repository_actions_keep_locator_and_commit_coupled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry = scaffold(root)
            repository, commit, blob = source_repository(root)
            common = ("--path", str(logical), "--entry", "e001")

            abbreviated = run(
                entry,
                "data",
                "add-origin",
                *common,
                "source-repository",
                str(repository),
                "--commit",
                commit[:12],
            )
            self.assertEqual(abbreviated.returncode, 2)
            self.assertFalse((entry / "data.json").exists())

            added = run(
                entry,
                "data",
                "add-origin",
                *common,
                "source-repository",
                str(repository),
                "--commit",
                commit,
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            item = data_inputs(entry)[0]
            self.assertEqual(item["kind"], "git-repository")
            self.assertTrue(item["origin"])
            self.assertEqual(item["fingerprint"]["digest"], commit)

            listed = run(entry, "data", "list", *common)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(
                result(listed)["records"],
                [
                    {
                        "classification": "origin",
                        "commit": commit,
                        "kind": "git-repository",
                        "name": "source-repository",
                        "target": repository.resolve().as_posix(),
                    }
                ],
            )

            before = (entry / "data.json").read_bytes()
            invalid = run(
                entry,
                "data",
                "update",
                *common,
                "source-repository",
                "--commit",
                blob,
            )
            generated = run(
                entry,
                "data",
                "update",
                *common,
                "source-repository",
                "--generated",
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(generated.returncode, 2)
            self.assertEqual((entry / "data.json").read_bytes(), before)

            worktree = root / "source-worktree"
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree), commit],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            moved = run(
                entry,
                "data",
                "update",
                *common,
                "source-repository",
                "--target",
                str(worktree),
            )
            self.assertEqual(moved.returncode, 0, moved.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["location"], worktree.resolve().as_posix()
            )

            (repository / "source.txt").write_text("updated\n", encoding="utf-8")
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
                    "updated fixture",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            updated_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            updated = run(
                entry,
                "data",
                "update",
                *common,
                "source-repository",
                "--commit",
                updated_commit,
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["fingerprint"]["digest"], updated_commit
            )

            refreshed = run(
                entry, "data", "refresh", *common, "source-repository"
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertFalse(result(refreshed)["changed"])

            renamed = run(
                entry,
                "data",
                "rename",
                *common,
                "source-repository",
                "renamed-repository",
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)
            self.assertEqual(data_inputs(entry)[0]["name"], "renamed-repository")
            removed = run(
                entry, "data", "remove", *common, "renamed-repository"
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "data.json").exists())

            legacy = run(
                entry,
                "data",
                "add-origin",
                *common,
                "legacy-repository",
                str(repository),
                "--identity",
                ".git/config",
            )
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            upgraded = run(
                entry,
                "data",
                "update",
                *common,
                "legacy-repository",
                "--commit",
                updated_commit,
            )
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            self.assertEqual(data_inputs(entry)[0]["kind"], "git-repository")

    def test_add_conflict_and_dry_run_leave_registry_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            first = entry / "data" / "first.txt"
            second = entry / "data" / "second.txt"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            arguments = (
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            dry = run(
                entry,
                "data",
                "add-origin",
                *arguments,
                "first",
                "data/first.txt",
                "--dry-run",
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse((entry / "data.json").exists())
            added = run(
                entry,
                "data",
                "add-origin",
                *arguments,
                "first",
                "data/first.txt",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            before = (entry / "data.json").read_bytes()
            conflict = run(
                entry,
                "data",
                "add-origin",
                *arguments,
                "first",
                "data/second.txt",
            )
            duplicate_target = run(
                entry,
                "data",
                "add-origin",
                *arguments,
                "second",
                "data/first.txt",
            )
            self.assertEqual(conflict.returncode, 2)
            self.assertEqual(duplicate_target.returncode, 2)
            self.assertEqual((entry / "data.json").read_bytes(), before)

            remote = run(
                entry,
                "data",
                "add-origin",
                *arguments,
                "remote",
                "s3://archive/source.csv",
            )
            self.assertEqual(remote.returncode, 2)
            self.assertEqual((entry / "data.json").read_bytes(), before)

    def test_malformed_registry_fails_without_repairing_or_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            target = entry / "data" / "source.txt"
            target.write_text("source\n", encoding="utf-8")
            registry = entry / "data.json"
            malformed = b'{"schema":"research-log-data/v3","inputs":['
            registry.write_bytes(malformed)

            attempted = run(
                entry,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "source",
                "data/source.txt",
            )
            self.assertEqual(attempted.returncode, 2)
            self.assertEqual(registry.read_bytes(), malformed)

    def test_identity_update_refresh_and_byte_complete_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            collection = entry / "data" / "collection"
            collection.mkdir()
            identity = collection / "manifest.json"
            identity.write_text('{"version":1}\n', encoding="utf-8")
            (collection / "part-01.bin").write_bytes(b"one")
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "data",
                "add-origin",
                *common,
                "collection",
                "data/collection",
                "--identity",
                "manifest.json",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["fingerprint"]["algorithm"],
                "identity-files-sha256-v1",
            )
            (collection / "part-02.bin").write_bytes(b"two")
            unchanged = run(
                entry, "data", "refresh", *common, "collection"
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
            self.assertFalse(result(unchanged)["changed"])
            patterned = run(
                entry,
                "data",
                "update",
                *common,
                "collection",
                "--identity",
                "*.json",
            )
            self.assertEqual(patterned.returncode, 0, patterned.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["fingerprint"]["algorithm"],
                "identity-patterns-sha256-v1",
            )
            replacement = entry / "data" / "replacement"
            replacement.mkdir()
            (replacement / "manifest.json").write_text(
                '{"version":2}\n', encoding="utf-8"
            )
            moved = run(
                entry,
                "data",
                "update",
                *common,
                "collection",
                "--target",
                "data/replacement",
            )
            self.assertEqual(moved.returncode, 0, moved.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["fingerprint"]["algorithm"],
                "identity-patterns-sha256-v1",
            )
            complete = run(
                entry,
                "data",
                "update",
                *common,
                "collection",
                "--byte-complete",
            )
            self.assertEqual(complete.returncode, 0, complete.stderr)
            self.assertEqual(
                data_inputs(entry)[0]["fingerprint"]["algorithm"],
                "directory-sha256-v1",
            )

    def test_generated_requires_current_confirmed_unique_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "data",
                "add-origin",
                *common,
                "source",
                "data/source.csv",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            generated = entry / "data" / "generated.csv"
            generated.write_text("value\n1\n", encoding="utf-8")
            missing = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(result(missing)["code"], "producer.missing")
            missing_pending = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(missing_pending.returncode, 2)
            self.assertEqual(
                result(missing_pending)["code"], "producer.missing"
            )

            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output', required=True)\n"
                "a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                "# Trial\n\n## Build\n\n`Steps:`\n\n"
                "```bash\n"
                "./pyrun scripts/build.py --input \"<source>\" "
                "--output data/generated.csv\n"
                "```\n\n`Results:`\n\nGenerated output.\n",
                encoding="utf-8",
            )
            unconfirmed = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(unconfirmed.returncode, 2)
            self.assertEqual(
                result(unconfirmed)["code"], "provenance.output.unrecorded"
            )
            executed = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/generated.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            accepted = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            generated_item = next(
                item for item in data_inputs(entry) if item["name"] == "generated"
            )
            self.assertFalse(generated_item["origin"])

            replacement = entry / "data" / "replacement.csv"
            replacement.write_text("value\n2\n", encoding="utf-8")
            before = (entry / "data.json").read_bytes()
            unsupported_target = run(
                entry,
                "data",
                "update",
                *common,
                "generated",
                "--target",
                "data/replacement.csv",
            )
            self.assertEqual(unsupported_target.returncode, 2)
            self.assertEqual(result(unsupported_target)["code"], "producer.missing")
            invalid_identity = run(
                entry,
                "data",
                "update",
                *common,
                "generated",
                "--identity",
                "manifest.json",
            )
            self.assertEqual(invalid_identity.returncode, 2)
            self.assertEqual(result(invalid_identity)["code"], "data.identity.invalid")
            self.assertEqual((entry / "data.json").read_bytes(), before)

    def test_pending_generated_bootstraps_reproduction_without_claiming_support(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            generated = entry / "data" / "generated.csv"
            generated.write_text("value\n1\n", encoding="utf-8")
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output', required=True)\n"
                "a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            (entry / "e001.md").write_text(
                "# Trial\n\n## Build\n\n`Steps:`\n\n"
                "```bash\n"
                './pyrun scripts/build.py --input "<source>" '
                "--output data/generated.csv\n"
                "```\n\n`Results:`\n\nGenerated output.\n",
                encoding="utf-8",
            )

            strict = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(
                result(strict)["code"], "provenance.output.unrecorded"
            )
            before = (entry / "data.json").read_bytes()
            checked = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "--dry-run",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual((entry / "data.json").read_bytes(), before)
            self.assertEqual(
                result(checked)["records"],
                [
                    {
                        "confirmation": "pending",
                        "document": "entries/2026-09-04-e001-trial/e001.md",
                        "fence": 1,
                        "ordinal": 1,
                    }
                ],
            )

            added = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            item = next(
                value for value in data_inputs(entry) if value["name"] == "generated"
            )
            self.assertFalse(item["origin"])
            self.assertNotIn("pending", item)
            self.assertFalse((entry / "pyrun-outputs.json").exists())

            executed = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/generated.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            confirmed = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            self.assertEqual(result(confirmed)["status"], "unchanged")

    def test_pending_generated_defers_unconfirmed_recursive_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            intermediate = entry / "data" / "intermediate.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            intermediate.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); "
                "p.add_argument('--output'); a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            (entry / "e001.md").write_text(
                "# Trial\n\n## Intermediate\n\n`Steps:`\n\n```bash\n"
                './pyrun scripts/build.py --input "<source>" '
                "--output data/intermediate.csv\n"
                "```\n\n`Results:`\n\nPending.\n\n"
                "## Final\n\n`Steps:`\n\n```bash\n"
                './pyrun scripts/build.py --input "<intermediate>" '
                "--output data/final.csv\n"
                "```\n\n`Results:`\n\nProduced.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-generated",
                    *common,
                    "--pending-confirmation",
                    "intermediate",
                    "data/intermediate.csv",
                ).returncode,
                0,
            )
            executed = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<intermediate>",
                "--output",
                "data/final.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            strict = run(
                entry,
                "data",
                "add-generated",
                *common,
                "final",
                "data/final.csv",
            )
            self.assertEqual(
                result(strict)["code"], "provenance.output.unrecorded"
            )
            pending = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "--dry-run",
                "final",
                "data/final.csv",
            )
            self.assertEqual(pending.returncode, 0, pending.stderr)

    def test_other_log_production_is_an_origin_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer_log, producer = scaffold(root)
            source = producer / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            self.assertEqual(
                run(
                    producer,
                    "data",
                    "add-origin",
                    "--path",
                    str(producer_log),
                    "--entry",
                    "e001",
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            shared = producer / "data" / "shared.csv"
            script = producer / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); "
                "p.add_argument('--output'); a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            (producer / "e001.md").write_text(
                "# Trial\n\n## Build\n\n`Steps:`\n\n```bash\n"
                './pyrun scripts/build.py --input "<source>" '
                '--output data/shared.csv\n'
                "```\n\n`Results:`\n\nGenerated.\n",
                encoding="utf-8",
            )
            executed = run_pyrun(
                producer,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/shared.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)

            consumer_log = root / "docs" / "consumer"
            self.assertEqual(
                run(
                    root,
                    "init",
                    "--path",
                    str(consumer_log),
                    "--title",
                    "Consumer",
                ).returncode,
                0,
            )
            self.assertEqual(
                run(
                    root,
                    "add",
                    "--path",
                    str(consumer_log),
                    "--date",
                    "2026-09-05",
                    "--title",
                    "Consume",
                    "--slug",
                    "consume",
                ).returncode,
                0,
            )
            consumer = next((consumer_log / "entries").iterdir())
            generated = run(
                consumer,
                "data",
                "add-generated",
                "--path",
                str(consumer_log),
                "--entry",
                "e001",
                "shared",
                str(shared),
            )
            self.assertEqual(generated.returncode, 2)
            self.assertEqual(result(generated)["code"], "producer.missing")
            origin = run(
                consumer,
                "data",
                "add-origin",
                "--path",
                str(consumer_log),
                "--entry",
                "e001",
                "shared",
                str(shared),
            )
            self.assertEqual(origin.returncode, 0, origin.stderr)
            self.assertTrue(data_inputs(consumer)[0]["origin"])

    def test_generated_directory_registers_atomic_bundle_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            script = entry / "scripts" / "bundle.py"
            script.write_text(
                "import argparse\n"
                "from pathlib import Path\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output-dir', required=True)\n"
                "a=p.parse_args()\n"
                "root=Path(a.output_dir); root.mkdir(exist_ok=True)\n"
                "root.joinpath('one.csv').write_text('value\\n1\\n')\n"
                "root.joinpath('two.csv').write_text('value\\n2\\n')\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                "# Trial\n\n## Bundle\n\n`Steps:`\n\n"
                "```bash\n"
                "./pyrun scripts/bundle.py --input \"<source>\" "
                "--output-dir data/bundle\n"
                "```\n\n`Results:`\n\nGenerated bundle.\n",
                encoding="utf-8",
            )
            executed = run_pyrun(
                entry,
                "scripts/bundle.py",
                "--input",
                "<source>",
                "--output-dir",
                "data/bundle",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            added = run(
                entry,
                "data",
                "add-generated",
                *common,
                "bundle",
                "data/bundle",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(
                [(item["name"], item["location"]) for item in data_inputs(entry)],
                [("bundle", "data/bundle"), ("source", "data/source.csv")],
            )
            conflict = run(
                entry,
                "data",
                "add-origin",
                *common,
                "two",
                "data/bundle/two.csv",
            )
            self.assertEqual(conflict.returncode, 2)
            self.assertEqual(result(conflict)["code"], "data.origin.invalid")

    def test_generated_rejects_ambiguous_unconfirmed_and_stale_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); "
                "p.add_argument('--output'); a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            command = (
                "./pyrun scripts/build.py --input \"<source>\" "
                "--output data/generated.csv"
            )
            document = entry / "e001.md"
            document.write_text(
                f"# Trial\n\n## Build\n\n`Steps:`\n\n```bash\n{command}\n"
                "```\n\n`Results:`\n\nGenerated.\n",
                encoding="utf-8",
            )
            executed = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/generated.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            support_path = entry / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            support["outputs"]["data/generated.csv"]["confirmed"] = False
            support_path.write_text(json.dumps(support) + "\n", encoding="utf-8")
            unconfirmed = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(
                result(unconfirmed)["code"], "provenance.output.unconfirmed"
            )
            pending = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "--dry-run",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(pending.returncode, 0, pending.stderr)
            self.assertEqual(len(data_inputs(entry)), 1)
            support["outputs"]["data/generated.csv"]["confirmed"] = True
            support_path.write_text(json.dumps(support) + "\n", encoding="utf-8")
            (entry / "data" / "generated.csv").write_text(
                "value\nchanged\n", encoding="utf-8"
            )
            stale = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(
                result(stale)["code"], "provenance.output.signature_mismatch"
            )
            stale_pending = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(
                result(stale_pending)["code"],
                "provenance.output.signature_mismatch",
            )
            self.assertEqual(len(data_inputs(entry)), 1)

            rerun = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/generated.csv",
            )
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    f"{command}\n", f"{command}\n{command}\n"
                ),
                encoding="utf-8",
            )
            ambiguous = run(
                entry,
                "data",
                "add-generated",
                *common,
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(result(ambiguous)["code"], "producer.ambiguous")
            ambiguous_pending = run(
                entry,
                "data",
                "add-generated",
                *common,
                "--pending-confirmation",
                "generated",
                "data/generated.csv",
            )
            self.assertEqual(
                result(ambiguous_pending)["code"], "producer.ambiguous"
            )

    def test_update_requires_explicit_change_and_rechecks_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            generated = entry / "data" / "generated.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            generated.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            for name, target in (
                ("source", "data/source.csv"),
                ("generated", "data/generated.csv"),
            ):
                self.assertEqual(
                    run(
                        entry,
                        "data",
                        "add-origin",
                        *common,
                        name,
                        target,
                    ).returncode,
                    0,
                )
            empty = run(entry, "data", "update", *common, "generated")
            self.assertEqual(result(empty)["code"], "data.update.empty")
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); "
                "p.add_argument('--output'); a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                "# Trial\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/build.py --input \"<source>\" "
                "--output data/generated.csv\n"
                "```\n\n`Results:`\n\nGenerated.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                run_pyrun(
                    entry,
                    "scripts/build.py",
                    "--input",
                    "<source>",
                    "--output",
                    "data/generated.csv",
                ).returncode,
                0,
            )
            to_generated = run(
                entry,
                "data",
                "update",
                *common,
                "generated",
                "--generated",
            )
            self.assertEqual(to_generated.returncode, 0, to_generated.stderr)
            before = (entry / "data.json").read_bytes()
            hidden = run(
                entry,
                "data",
                "update",
                *common,
                "generated",
                "--origin",
            )
            self.assertEqual(result(hidden)["code"], "data.origin.invalid")
            self.assertEqual((entry / "data.json").read_bytes(), before)
            document.write_text("# Trial\n", encoding="utf-8")
            to_origin = run(
                entry,
                "data",
                "update",
                *common,
                "generated",
                "--origin",
            )
            self.assertEqual(to_origin.returncode, 0, to_origin.stderr)
            item = next(
                value for value in data_inputs(entry) if value["name"] == "generated"
            )
            self.assertTrue(item["origin"])

    def test_rename_updates_evidence_and_reports_required_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "data",
                "add-origin",
                *common,
                "source",
                "data/source.csv",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output', required=True)\n"
                "a=p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                "# Trial\n\n## Build\n\n`Steps:`\n\n"
                "```bash\n"
                "./pyrun scripts/build.py --input \"<source>\" "
                "--output data/generated.csv\n"
                "```\n\n`Results:`\n\nGenerated output.\n",
                encoding="utf-8",
            )
            executed = run_pyrun(
                entry,
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "data/generated.csv",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            evidence = {
                "schema": "research-log-evidence/v3",
                "records": [
                    {
                        "document": document.relative_to(logical).as_posix(),
                        "id": "value",
                        "kind": "statistic",
                        "sources": [
                            {
                                "source": "<source>",
                                "locator": {"select": [["value"]]},
                            }
                        ],
                        "transformation": None,
                    }
                ],
            }
            (entry / "evidence.json").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8"
            )

            incomplete = run(
                entry, "data", "rename", *common, "source", "renamed"
            )
            self.assertEqual(incomplete.returncode, 2)
            before = (entry / "data.json").read_bytes()
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "<source>", "<renamed>"
                ),
                encoding="utf-8",
            )
            renamed = run(
                entry, "data", "rename", *common, "source", "renamed"
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)
            payload = result(renamed)
            self.assertEqual(len(payload["records"]), 1)
            self.assertNotEqual((entry / "data.json").read_bytes(), before)
            self.assertEqual(data_inputs(entry)[0]["name"], "renamed")
            evidence_after = json.loads(
                (entry / "evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                evidence_after["records"][0]["sources"][0]["source"],
                "<renamed>",
            )

    def test_rename_rolls_back_both_registries_on_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )
            evidence = {
                "schema": "research-log-evidence/v3",
                "records": [
                    {
                        "document": (entry / "e001.md")
                        .relative_to(logical)
                        .as_posix(),
                        "id": "value",
                        "kind": "statistic",
                        "sources": [
                            {
                                "source": "<source>",
                                "locator": {"select": [["value"]]},
                            }
                        ],
                        "transformation": None,
                    }
                ],
            }
            evidence_path = entry / "evidence.json"
            evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
            before_data = (entry / "data.json").read_bytes()
            before_evidence = evidence_path.read_bytes()

            script_root = str(LOG.parent)
            sys.path.insert(0, script_root)
            try:
                from log_commands import data as data_actions
                from log_commands import storage
                from log_commands.context import resolve_entry, resolve_log

                context = resolve_entry(resolve_log(logical), "e001")
                publish_count = 0
                atomic_write_text = storage.atomic_write_text

                def fail_second_publication(path: Path, text: str) -> None:
                    nonlocal publish_count
                    publish_count += 1
                    if publish_count == 2:
                        raise OSError("injected publication failure")
                    atomic_write_text(path, text)

                with (
                    mock.patch.object(
                        storage,
                        "atomic_write_text",
                        side_effect=fail_second_publication,
                    ),
                    self.assertRaisesRegex(OSError, "injected publication failure"),
                ):
                    data_actions.rename(
                        context,
                        "source",
                        "renamed",
                        dry_run=False,
                    )
            finally:
                sys.path.remove(script_root)
            self.assertEqual((entry / "data.json").read_bytes(), before_data)
            self.assertEqual(evidence_path.read_bytes(), before_evidence)

    def test_rename_keeps_repair_residue_after_incomplete_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            self.assertEqual(
                run(
                    entry,
                    "data",
                    "add-origin",
                    *common,
                    "source",
                    "data/source.csv",
                ).returncode,
                0,
            )

            script_root = str(LOG.parent)
            sys.path.insert(0, script_root)
            try:
                from log_commands import data as data_actions
                from log_commands.context import resolve_entry, resolve_log
                from log_commands.storage import PublicationError
                from validation.operation_state import (
                    REGISTRY_RESIDUE_PREFIX,
                    operation_directory,
                )

                context = resolve_entry(resolve_log(logical), "e001")
                publication_error = PublicationError(
                    OSError("injected publication failure"), ("restore failed",)
                )
                with (
                    mock.patch.object(
                        data_actions,
                        "atomic_write_texts",
                        side_effect=publication_error,
                    ),
                    self.assertRaisesRegex(Exception, "rollback failed"),
                ):
                    data_actions.rename(
                        context,
                        "source",
                        "renamed",
                        dry_run=False,
                    )
                self.assertTrue(
                    (
                        operation_directory(logical)
                        / f"{REGISTRY_RESIDUE_PREFIX}e001"
                    ).is_file()
                )
                blocked = run(entry, "data", "refresh", *common, "source")
                self.assertEqual(blocked.returncode, 2)
                self.assertIn("requires Repair", blocked.stderr)
            finally:
                sys.path.remove(script_root)

    def test_remove_requires_no_command_or_evidence_use_and_removes_empty_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "data",
                "add-origin",
                *common,
                "source",
                "data/source.csv",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            document = entry / "e001.md"
            document.write_text(
                "# Trial\n\n## Use\n\n`Steps:`\n\n"
                "```bash\n./pyrun scripts/use.py --input \"<source>\"\n```\n"
                "\n`Results:`\n\nUsed input.\n",
                encoding="utf-8",
            )
            used = run(entry, "data", "remove", *common, "source")
            self.assertEqual(used.returncode, 2)
            document.write_text("# Trial\n", encoding="utf-8")
            removed = run(entry, "data", "remove", *common, "source")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "data.json").exists())

    def test_entry_locks_serialize_locally_without_cross_entry_rewrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, first = scaffold(Path(directory))
            second = add_entry(logical, date="2026-09-05", slug="second")
            entries = ((first, "e001"), (second, "e002"))
            processes: list[tuple[subprocess.Popen[str], Path, str, str]] = []
            environment = os.environ.copy()
            environment.pop("PYTHONHOME", None)
            for entry, entry_id in entries:
                for suffix in ("one", "two"):
                    target = entry / "data" / f"{suffix}.txt"
                    target.write_text(f"{entry_id}-{suffix}\n", encoding="utf-8")
                    processes.append(
                        (
                            subprocess.Popen(
                                [
                                    sys.executable,
                                    str(LOG),
                                    "data",
                                    "add-origin",
                                    "--path",
                                    str(logical),
                                    "--entry",
                                    entry_id,
                                    suffix,
                                    f"data/{suffix}.txt",
                                ],
                                cwd=entry,
                                text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env=environment,
                            ),
                            entry,
                            entry_id,
                            suffix,
                        )
                    )
            outputs = [
                process.communicate(timeout=20)
                for process, _, _, _ in processes
            ]
            for entry_id in ("e001", "e002"):
                returncodes = sorted(
                    process.returncode
                    for process, _, observed_id, _ in processes
                    if observed_id == entry_id
                )
                self.assertIn(returncodes, ([0, 0], [0, 2]), outputs)
            for process, entry, entry_id, suffix in processes:
                if process.returncode == 0:
                    continue
                retried = run(
                    entry,
                    "data",
                    "add-origin",
                    "--path",
                    str(logical),
                    "--entry",
                    entry_id,
                    suffix,
                    f"data/{suffix}.txt",
                )
                self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertEqual(
                [[item["name"] for item in data_inputs(entry)] for entry, _ in entries],
                [["one", "two"], ["one", "two"]],
            )

    def test_cross_entry_disagreement_is_left_for_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, first = scaffold(root)
            second = add_entry(logical, date="2026-09-05", slug="second")
            shared = root / "shared.csv"
            shared.write_text("value\n1\n", encoding="utf-8")
            first_add = run(
                first,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "shared",
                str(shared),
            )
            self.assertEqual(first_add.returncode, 0, first_add.stderr)
            shared.write_text("value\n2\n", encoding="utf-8")
            second_add = run(
                second,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "shared",
                str(shared),
            )
            self.assertEqual(second_add.returncode, 0, second_add.stderr)
            validated = run(
                root,
                "validate",
                "--path",
                str(logical),
                "--dry-run",
            )
            self.assertIn("data.declaration.conflict", validated.stdout)


if __name__ == "__main__":
    unittest.main()
