from __future__ import annotations

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

from log_commands import reorganize  # noqa: E402
from log_commands.context import resolve_entry, resolve_log  # noqa: E402
from log_commands.model import EntryUpdateArguments  # noqa: E402
from validation.operation_state import (  # noqa: E402
    begin_reorganization,
    finish_reorganization,
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
                .replace("trial-1", "calibrated"),
                encoding="utf-8",
            )
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "# 2026-09-01:", "# 2026-10-01:"
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

    def test_remove_empty_entry_requires_the_summary_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entries = create_log(root, 1)
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
            summary.write_text(
                "\n".join(line for line in text.splitlines() if "e001.md" not in line)
                + "\n",
                encoding="utf-8",
            )
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
                finish_reorganization(marker)


class ReorganizeTransferTests(unittest.TestCase):
    def test_transfer_moves_selected_data_and_evidence_after_agent_edits(self) -> None:
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
                "--evidence",
                "run-result",
                "--data",
                "result",
                "--document-map",
                source_document_field,
                destination_document_field,
                "--path-map",
                "data/result.txt",
                "data/result.txt",
            )
            self.assertEqual(transferred.returncode, 0, transferred.stderr)
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


if __name__ == "__main__":
    unittest.main()
