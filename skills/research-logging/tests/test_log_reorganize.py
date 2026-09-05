from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"
SCRIPT_ROOT = LOG.parent
sys.path.insert(0, str(SCRIPT_ROOT))

from log_commands import reorganize, storage  # noqa: E402
from log_commands.context import LogContext, resolve_entry, resolve_log  # noqa: E402
from log_commands.model import EntryUpdateArguments  # noqa: E402
from log_commands.storage import PublicationError  # noqa: E402
from validation.operation_state import (  # noqa: E402
    REORGANIZE_RESIDUE,
    begin_reorganization,
    finish_guarded_publication,
    operation_directory,
)


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, str(LOG), *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def create_log(root: Path, count: int = 2) -> tuple[Path, list[Path]]:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    logical = root / "docs" / "study"
    logical.parent.mkdir()
    initialized = run(root, "init", "--path", str(logical), "--title", "Study")
    if initialized.returncode:
        raise AssertionError(initialized.stderr)
    entries: list[Path] = []
    for number in range(1, count + 1):
        result = run(
            root,
            "add",
            "--path",
            str(logical),
            "--date",
            f"2026-09-{number:02d}",
            "--title",
            f"Trial {number}",
            "--slug",
            f"trial-{number}",
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        entries.append(
            logical / "entries" / f"2026-09-{number:02d}-e{number:03d}-trial-{number}"
        )
    return logical, entries


def result(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(process.stdout)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReorganizeHelpTests(unittest.TestCase):
    def test_help_is_progressive_and_transfer_has_no_support_retirement(self) -> None:
        top = run(Path.cwd(), "--help")
        family = run(Path.cwd(), "reorganize", "--help")
        transfer = run(Path.cwd(), "reorganize", "transfer", "--help")
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("reorganize", top.stdout)
        self.assertNotIn("--from-entry", top.stdout)
        self.assertIn("transfer", family.stdout)
        self.assertNotIn("--from-entry", family.stdout)
        self.assertIn("--from-entry", transfer.stdout)
        self.assertNotIn("retire", transfer.stdout)
        self.assertNotIn("pyrun", transfer.stdout)

    def test_help_does_not_import_reorganize_implementations(self) -> None:
        code = f"""
import json
import sys
sys.path.insert(0, {str(SCRIPT_ROOT)!r})
from log_commands.dispatcher import main
for arguments in (["reorganize", "--help"], ["reorganize", "transfer", "--help"]):
    try:
        main(arguments)
    except SystemExit as error:
        assert error.code == 0
print(json.dumps({{
    "identity": "log_commands.reorganize" in sys.modules,
    "transfer": "log_commands.reorganize_transfer" in sys.modules,
}}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout.splitlines()[-1]),
            {"identity": False, "transfer": False},
        )


class StorageTransactionTests(unittest.TestCase):
    def test_post_replace_failure_restores_the_current_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text("before\n", encoding="utf-8")
            real_open = os.open
            failed = False

            def fail_first_directory_open(
                target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
            ) -> int:
                nonlocal failed
                if not failed and Path(target) == path.parent and flags == os.O_RDONLY:
                    failed = True
                    raise OSError("injected directory sync failure")
                return real_open(target, flags, mode)

            with (
                mock.patch.object(
                    storage.os, "open", side_effect=fail_first_directory_open
                ),
                self.assertRaises(PublicationError) as caught,
            ):
                storage.atomic_write_texts({path: "after\n"})

            self.assertTrue(caught.exception.rollback_complete)
            self.assertEqual(path.read_text(encoding="utf-8"), "before\n")


class ReorganizeIdentityTests(unittest.TestCase):
    def test_update_entry_verifies_markdown_then_renames_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
            entry = entries[0]
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8")
                .replace("2026-09-01", "2026-10-01")
                .replace("trial-1", "calibrated")
                + "\n[Stale](study/entries/2026-09-01-e001-trial-1/e001.md)\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "# 2026-09-01:", "# 2026-10-01:"
                ),
                encoding="utf-8",
            )
            blocked = run(
                root,
                "reorganize",
                "update-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--date",
                "2026-10-01",
                "--slug",
                "calibrated",
                "--dry-run",
            )
            self.assertEqual(blocked.returncode, 2)
            summary.write_text(
                summary.read_text(encoding="utf-8").replace(
                    "2026-09-01-e001-trial-1/e001.md",
                    "2026-10-01-e001-calibrated/e001.md",
                ),
                encoding="utf-8",
            )
            changed = run(
                root,
                "reorganize",
                "update-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--date",
                "2026-10-01",
                "--slug",
                "calibrated",
            )
            destination = logical / "entries" / "2026-10-01-e001-calibrated"
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertTrue(destination.is_dir())
            self.assertFalse(entry.exists())

    def test_update_entry_rolls_back_an_ordinary_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
            entry = entries[0]
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("trial-1", "changed"),
                encoding="utf-8",
            )
            context = resolve_entry(resolve_log(logical), "e001")
            with mock.patch.object(
                reorganize,
                "atomic_write_texts",
                side_effect=OSError("injected registry failure"),
            ):
                with self.assertRaisesRegex(Exception, "injected registry failure"):
                    reorganize.update_entry(
                        context,
                        EntryUpdateArguments(None, "changed", None, False),
                    )
            self.assertTrue(entry.is_dir())
            self.assertFalse(entry.with_name("2026-09-01-e001-changed").exists())

    def test_update_entry_keeps_residue_after_incomplete_registry_rollback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
            entry = entries[0]
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("trial-1", "changed"),
                encoding="utf-8",
            )
            context = resolve_entry(resolve_log(logical), "e001")
            publication_error = PublicationError(
                OSError("injected registry failure"), ("restore failed",)
            )
            with mock.patch.object(
                reorganize, "atomic_write_texts", side_effect=publication_error
            ):
                with self.assertRaisesRegex(Exception, "rollback failed"):
                    reorganize.update_entry(
                        context,
                        EntryUpdateArguments(None, "changed", None, False),
                    )
            self.assertTrue(entry.is_dir())
            self.assertTrue(
                (operation_directory(logical) / REORGANIZE_RESIDUE).is_file()
            )

    def test_update_entry_preserves_cross_entry_relative_data_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            source, consumer = entries
            shared = source / "data" / "shared.txt"
            shared.parent.mkdir()
            shared.write_text("stable\n", encoding="utf-8")
            relative = os.path.relpath(shared, start=consumer).replace(os.sep, "/")
            registered = run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "shared",
                relative,
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("trial-1", "renamed"),
                encoding="utf-8",
            )
            changed = run(
                root,
                "reorganize",
                "update-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--slug",
                "renamed",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            payload = json.loads((consumer / "data.json").read_text())
            self.assertIn("e001-renamed", payload["inputs"][0]["location"])

    def test_reorder_applies_a_complete_simultaneous_permutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 3)
            summary = logical.with_suffix(".md")
            text = summary.read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if line.startswith("- `")]
            replacements = []
            for new_number, old_index in enumerate((2, 0, 1), 1):
                old = lines[old_index]
                old_id = f"e{old_index + 1:03d}"
                new_id = f"e{new_number:03d}"
                replacements.append(old.replace(old_id, new_id))
            start = text.index(lines[0])
            end = text.index(lines[-1]) + len(lines[-1])
            summary.write_text(
                text[:start] + "\n".join(replacements) + text[end:],
                encoding="utf-8",
            )
            changed = run(
                root,
                "reorganize",
                "reorder",
                "--path",
                str(logical),
                "--entries",
                "e003,e001,e002",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            names = sorted(path.name for path in (logical / "entries").iterdir())
            self.assertIn("2026-09-03-e001-trial-3", names)
            self.assertIn("2026-09-01-e002-trial-1", names)
            self.assertIn("2026-09-02-e003-trial-2", names)

    def test_reorder_updates_evidence_document_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            entry = entries[1]
            data = entry / "data" / "result.txt"
            data.parent.mkdir()
            data.write_text("complete\n", encoding="utf-8")
            run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "result",
                "data/result.txt",
            )
            document = entry / "e002.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + "<!-- eid:run-result -->\n```text\ncomplete\n```\n",
                encoding="utf-8",
            )
            recorded = run(
                root,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "--id",
                "run-result",
                "--source",
                "result",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)

            summary = logical.with_suffix(".md")
            text = summary.read_text(encoding="utf-8")
            text = text.replace(
                "## Summary\n\n",
                "## Summary\n\n"
                "Result `complete`<!-- ref entry = e002; eid = run-result -->.\n\n",
            )
            lines = [line for line in text.splitlines() if line.startswith("- `")]
            swapped = [
                lines[1].replace("e002", "e001"),
                lines[0].replace("e001", "e002"),
            ]
            start = text.index(lines[0])
            end = text.index(lines[-1]) + len(lines[-1])
            summary.write_text(
                text[:start] + "\n".join(swapped) + text[end:], encoding="utf-8"
            )
            blocked = run(
                root,
                "reorganize",
                "reorder",
                "--path",
                str(logical),
                "--entries",
                "e002,e001",
                "--dry-run",
            )
            self.assertEqual(blocked.returncode, 2)
            summary.write_text(
                summary.read_text(encoding="utf-8").replace(
                    "ref entry = e002; eid = run-result",
                    "ref entry = e001; eid = run-result",
                ),
                encoding="utf-8",
            )
            changed = run(
                root,
                "reorganize",
                "reorder",
                "--path",
                str(logical),
                "--entries",
                "e002,e001",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            moved = logical / "entries" / "2026-09-02-e001-trial-2"
            evidence = json.loads((moved / "evidence.json").read_text())
            self.assertEqual(
                evidence["records"][0]["document"],
                "entries/2026-09-02-e001-trial-2/e001.md",
            )

    def test_relocate_moves_the_complete_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = create_log(root, 1)
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("study/", "renamed/"),
                encoding="utf-8",
            )
            destination = logical.with_name("renamed")
            changed = run(
                root,
                "reorganize",
                "relocate-log",
                "--path",
                str(logical),
                "--to",
                str(destination),
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertTrue(destination.is_dir())
            self.assertTrue(destination.with_suffix(".md").is_file())
            self.assertFalse(logical.exists())
            self.assertFalse(summary.exists())

    def test_relocate_holds_every_entry_lock_during_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = create_log(root, 2)
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("study/", "renamed/"),
                encoding="utf-8",
            )
            destination = logical.with_name("renamed")
            original = reorganize._publish_relocation

            def require_entry_locks(
                log: LogContext, target_summary: Path, target_root: Path
            ) -> None:
                for entry_id in ("e001", "e002"):
                    lock = operation_directory(logical) / f"entry-{entry_id}.lock"
                    with lock.open("r+b") as handle:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(
                                handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                            )
                original(log, target_summary, target_root)

            with mock.patch.object(
                reorganize, "_publish_relocation", side_effect=require_entry_locks
            ):
                changed = reorganize.relocate_log(
                    resolve_log(logical), destination, dry_run=False
                )

            self.assertTrue(changed.changed)

    def test_relocate_preserves_an_external_relative_data_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
            external = root / "source.txt"
            external.write_text("source\n", encoding="utf-8")
            location = os.path.relpath(external, start=entries[0]).replace(os.sep, "/")
            registered = run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "source",
                location,
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text(encoding="utf-8").replace("study/", "renamed/"),
                encoding="utf-8",
            )
            destination = root / "archive" / "logs" / "renamed"
            destination.parent.mkdir(parents=True)
            moved = run(
                root,
                "reorganize",
                "relocate-log",
                "--path",
                str(logical),
                "--to",
                str(destination),
            )
            self.assertEqual(moved.returncode, 0, moved.stderr)
            new_entry = destination / "entries" / entries[0].name
            data = json.loads((new_entry / "data.json").read_text())
            resolved = (new_entry / data["inputs"][0]["location"]).resolve()
            self.assertEqual(resolved, external.resolve())

    def test_remove_empty_entry_requires_the_summary_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            target = entries[0] / "e001.md"
            location = os.path.relpath(target, start=entries[1]).replace(os.sep, "/")
            registered = run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "old-entry",
                location,
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            blocked = run(
                root,
                "reorganize",
                "remove-empty-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            self.assertEqual(blocked.returncode, 2)
            summary = logical.with_suffix(".md")
            text = summary.read_text(encoding="utf-8")
            without_first = (
                "\n".join(line for line in text.splitlines() if "e001.md" not in line)
                + "\n"
            )
            summary.write_text(
                without_first,
                encoding="utf-8",
            )
            referenced = run(
                root,
                "reorganize",
                "remove-empty-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            self.assertEqual(referenced.returncode, 2)
            summary.write_text(text, encoding="utf-8")
            unregistered = run(
                root,
                "data",
                "remove",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "old-entry",
            )
            self.assertEqual(unregistered.returncode, 0, unregistered.stderr)
            summary.write_text(without_first, encoding="utf-8")
            removed = run(
                root,
                "reorganize",
                "remove-empty-entry",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(entries[0].exists())

    def test_recognized_interruption_residue_blocks_later_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = create_log(root, 1)
            marker = begin_reorganization(logical)
            try:
                blocked = run(
                    root,
                    "add",
                    "--path",
                    str(logical),
                    "--date",
                    "2026-09-02",
                    "--title",
                    "Blocked",
                    "--slug",
                    "blocked",
                )
                self.assertEqual(blocked.returncode, 2)
                self.assertIn("requires Repair", blocked.stderr)
            finally:
                finish_guarded_publication(marker)


class ReorganizeTransferTests(unittest.TestCase):
    def test_transfer_moves_artifact_registry_state_after_agent_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            source, destination = entries
            source_image = source / "images" / "map.png"
            source_image.parent.mkdir()
            source_image.write_bytes(b"map bytes")
            registered = run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "map",
                "images/map.png",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            section = (
                "\n## Map\n\n`Background:`\n\nInspect the map.\n\n"
                "`Steps:`\n\nOpen the image.\n\n`Results:`\n\n"
                "![Map](images/map.png)<!-- eid:result-map -->\n"
            )
            source_document = source / "e001.md"
            source_document.write_text(
                source_document.read_text(encoding="utf-8") + section,
                encoding="utf-8",
            )
            recorded = run(
                root,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "result-map",
                "--source",
                "map",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)

            source_document.write_text(
                source_document.read_text(encoding="utf-8").replace(section, ""),
                encoding="utf-8",
            )
            destination_document = destination / "e002.md"
            moved_section = section.replace("images/map.png", "images/moved.png")
            destination_document.write_text(
                destination_document.read_text(encoding="utf-8") + moved_section,
                encoding="utf-8",
            )
            destination_image = destination / "images" / "moved.png"
            destination_image.parent.mkdir()
            source_image.rename(destination_image)
            old_document = source.relative_to(logical).as_posix() + "/e001.md"
            new_document = destination.relative_to(logical).as_posix() + "/e002.md"

            transferred = run(
                root,
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e002",
                "--evidence",
                "result-map",
                "--data",
                "map",
                "--document-map",
                old_document,
                new_document,
                "--path-map",
                "images/map.png",
                "images/moved.png",
            )

            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            record = json.loads((destination / "evidence.json").read_text())[
                "records"
            ][0]
            self.assertEqual(record["kind"], "artifact")
            self.assertEqual(
                record["sources"], [{"locator": None, "source": "<map>"}]
            )
            self.assertFalse((source / "data.json").exists())
            self.assertFalse((source / "evidence.json").exists())

    def test_transfer_all_moves_data_and_evidence_after_agent_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            source, destination = entries
            source_data = source / "data" / "result.txt"
            source_data.parent.mkdir()
            source_data.write_text("complete\n", encoding="utf-8")
            registered = run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "result",
                "data/result.txt",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            source_document = source / "e001.md"
            source_document.write_text(
                source_document.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + "<!-- eid:run-result -->\n```text\ncomplete\n```\n",
                encoding="utf-8",
            )
            recorded = run(
                root,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "run-result",
                "--source",
                "result",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)

            script = source / "scripts" / "make.py"
            script.parent.mkdir()
            script.write_text("print('complete')\n", encoding="utf-8")
            (source / "pyrun-outputs.json").write_text(
                json.dumps(
                    {
                        "schema": "research-log-pyrun-outputs/v1",
                        "outputs": {
                            "data/result.txt": {
                                "confirmed": True,
                                "fingerprint": {
                                    "algorithm": "sha256",
                                    "digest": sha256(source_data),
                                },
                                "inputs": {},
                                "parameters": ["--output", "data/result.txt"],
                                "script": {
                                    "path": "scripts/make.py",
                                    "fingerprint": {
                                        "algorithm": "sha256",
                                        "digest": sha256(script),
                                    },
                                },
                            }
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            marker = "<!-- eid:run-result -->\n```text\ncomplete\n```\n"
            source_document.write_text(
                source_document.read_text(encoding="utf-8").replace(marker, ""),
                encoding="utf-8",
            )
            destination_document = destination / "e002.md"
            destination_document.write_text(
                destination_document.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + marker,
                encoding="utf-8",
            )
            destination_data = destination / "data" / "result.txt"
            destination_data.parent.mkdir()
            source_data.rename(destination_data)
            source_document_field = source.relative_to(logical).as_posix() + "/e001.md"
            destination_document_field = (
                destination.relative_to(logical).as_posix() + "/e002.md"
            )
            transferred = run(
                root,
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e002",
                "--all",
                "--document-map",
                source_document_field,
                destination_document_field,
                "--path-map",
                "data/result.txt",
                "data/result.txt",
            )
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            self.assertEqual(len(result(transferred)["records"]), 1)
            support = json.loads((source / "pyrun-outputs.json").read_text())
            self.assertEqual(support["outputs"], {})
            self.assertFalse((source / "data.json").exists())
            self.assertFalse((source / "evidence.json").exists())
            evidence = json.loads((destination / "evidence.json").read_text())
            self.assertEqual(
                evidence["records"][0]["document"], destination_document_field
            )
            data = json.loads((destination / "data.json").read_text())
            self.assertEqual(data["inputs"][0]["name"], "result")

    def test_same_entry_document_transfer_changes_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
            entry = entries[0]
            data = entry / "data" / "result.txt"
            data.parent.mkdir()
            data.write_text("complete\n", encoding="utf-8")
            run(
                root,
                "data",
                "add-origin",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "result",
                "data/result.txt",
            )
            first = entry / "e001.md"
            marker = "<!-- eid:run-result -->\n```text\ncomplete\n```\n"
            first.write_text(
                first.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + marker,
                encoding="utf-8",
            )
            recorded = run(
                root,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "run-result",
                "--source",
                "result",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            first.write_text(
                first.read_text(encoding="utf-8").replace(marker, ""),
                encoding="utf-8",
            )
            second = entry / "e001a.md"
            second.write_text(
                "# 2026-09-01: Details\n\n## Trial\n\n`Results:`\n\n" + marker,
                encoding="utf-8",
            )
            summary = logical.with_suffix(".md")
            text = summary.read_text(encoding="utf-8")
            old_line = next(line for line in text.splitlines() if "e001.md" in line)
            split = (
                "- `2026-09-01` Trial 1:\n"
                "  - [Main](study/entries/2026-09-01-e001-trial-1/e001.md)\n"
                "  - [Details](study/entries/2026-09-01-e001-trial-1/e001a.md)"
            )
            summary.write_text(text.replace(old_line, split), encoding="utf-8")
            old_document = "entries/2026-09-01-e001-trial-1/e001.md"
            new_document = "entries/2026-09-01-e001-trial-1/e001a.md"
            transferred = run(
                root,
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e001",
                "--evidence",
                "run-result",
                "--document-map",
                old_document,
                new_document,
            )
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            evidence = json.loads((entry / "evidence.json").read_text())
            self.assertEqual(evidence["records"][0]["document"], new_document)

    def test_cross_entry_transfer_applies_every_mapping_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 2)
            source, destination = entries
            source_data = source / "data"
            source_data.mkdir()
            result_path = source_data / "result.txt"
            result_path.write_text("complete\n", encoding="utf-8")
            retained_path = source_data / "retained.log"
            retained_path.write_text("diagnostic\n", encoding="utf-8")
            for name, target in (
                ("result", "data/result.txt"),
                ("retained", "data/retained.log"),
            ):
                registered = run(
                    root,
                    "data",
                    "add-origin",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                    name,
                    target,
                )
                self.assertEqual(registered.returncode, 0, registered.stderr)
            retained = run(
                root,
                "retention",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "keep-run",
                "data/retained.log",
            )
            self.assertEqual(retained.returncode, 0, retained.stderr)
            source_document = source / "e001.md"
            old_marker = "<!-- eid:run-result -->\n```text\ncomplete\n```\n"
            source_document.write_text(
                source_document.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + old_marker,
                encoding="utf-8",
            )
            recorded = run(
                root,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "run-result",
                "--source",
                "result",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            source_document.write_text(
                source_document.read_text(encoding="utf-8").replace(old_marker, ""),
                encoding="utf-8",
            )
            destination_document = destination / "e002.md"
            new_marker = "<!-- eid:moved-result -->\n```text\ncomplete\n```\n"
            destination_document.write_text(
                destination_document.read_text(encoding="utf-8")
                + "\n## Trial\n\n`Results:`\n\n"
                + new_marker,
                encoding="utf-8",
            )
            moved_result = destination / "data" / "moved.txt"
            moved_result.parent.mkdir()
            result_path.rename(moved_result)
            moved_retained = destination / "logs" / "retained.log"
            moved_retained.parent.mkdir()
            retained_path.rename(moved_retained)
            old_document = source.relative_to(logical).as_posix() + "/e001.md"
            new_document = destination.relative_to(logical).as_posix() + "/e002.md"
            transferred = run(
                root,
                "reorganize",
                "transfer",
                "--path",
                str(logical),
                "--from-entry",
                "e001",
                "--to-entry",
                "e002",
                "--evidence",
                "run-result",
                "--data",
                "result,retained",
                "--retention",
                "keep-run",
                "--document-map",
                old_document,
                new_document,
                "--path-map",
                "data/result.txt",
                "data/moved.txt",
                "--path-map",
                "data/retained.log",
                "logs/retained.log",
                "--data-map",
                "result",
                "moved-data",
                "--evidence-map",
                "run-result",
                "moved-result",
                "--retention-map",
                "keep-run",
                "kept-run",
            )
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
            evidence = json.loads((destination / "evidence.json").read_text())
            self.assertEqual(evidence["records"][0]["id"], "moved-result")
            self.assertEqual(
                evidence["records"][0]["sources"][0]["source"], "<moved-data>"
            )
            data = json.loads((destination / "data.json").read_text())
            self.assertEqual(
                {item["name"] for item in data["inputs"]},
                {"moved-data", "retained"},
            )
            retention = json.loads((destination / "retention.json").read_text())
            self.assertEqual(retention["records"][0]["id"], "kept-run")
            self.assertEqual(retention["records"][0]["paths"], ["logs/retained.log"])


if __name__ == "__main__":
    unittest.main()
