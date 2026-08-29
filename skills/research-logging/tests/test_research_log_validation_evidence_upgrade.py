from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from research_log_validation_test_support import write

UPGRADE = importlib.import_module("validation.evidence_upgrade")
V2 = importlib.import_module("validation.evidence")


def legacy_log(root: Path) -> tuple[Path, Path, Path]:
    summary = root / "docs" / "log.md"
    log_root = root / "docs" / "log"
    entry_root = log_root / "entries" / "2026-08-28-e001-study"
    entry = entry_root / "e001.md"
    write(
        summary,
        "# Log\n\n"
        "The result was `1.0`.\n\n"
        "## Entries\n\n"
        "- [e001](log/entries/2026-08-28-e001-study/e001.md)\n",
    )
    write(
        entry,
        "# Entry\n\n## Trial\n\n`Question:`\n\nQ\n\n"
        "`Results:`\n\nThe result was `1.0`.\n",
    )
    write(
        entry_root / "evidence.csv",
        "entry,section,kind,evidence,sources,transformation\n"
        "e001,Trial,statistic,1.0,data/result.csv :: field=value,\n",
    )
    write(
        log_root / "evidence.csv",
        "statistic,entry,section,transformation\n1.0,e001,Trial,\n",
    )
    write(entry_root / "data" / "result.csv", "value\n1.0\n")
    return summary, entry_root, entry


def authored_candidate(summary: Path, entry_root: Path, entry: Path):
    relative_entry = entry.relative_to(summary.parent).as_posix()
    relative_evidence = (entry_root / "evidence.json").relative_to(
        summary.parent
    ).as_posix()
    relative_summary = summary.name
    marked_entry = entry.read_text(encoding="utf-8").replace(
        "`1.0`.", "`1.0`<!-- eid:result -->."
    )
    marked_summary = summary.read_text(encoding="utf-8").replace(
        "`1.0`.", "`1.0`<!-- ref entry = e001; eid = result -->."
    )
    evidence = b"""{
  "schema": "research-log-evidence/v2",
  "records": [{
    "id": "result",
    "document": "entries/2026-08-28-e001-study/e001.md",
    "kind": "statistic",
    "sources": [{"source": "data/result.csv", "locator": {"select": [["value"]]}}],
    "transformation": null
  }]
}
"""
    replacements = {
        relative_summary: marked_summary.encode(),
        relative_entry: marked_entry.encode(),
        relative_evidence: evidence,
    }
    removals = [
        (summary.with_suffix("") / "evidence.csv")
        .relative_to(summary.parent)
        .as_posix(),
        (entry_root / "evidence.csv").relative_to(summary.parent).as_posix(),
    ]
    return replacements, removals


def validate_candidate(summary: Path) -> None:
    log_root = summary.with_suffix("")
    entry_root = log_root / "entries" / "2026-08-28-e001-study"
    if list(log_root.rglob("evidence.csv")):
        raise AssertionError("legacy evidence remains")
    evidence = V2.load_evidence_file(
        entry_root / "evidence.json",
        log_root=log_root,
        entry_root=entry_root,
    )
    presentations = V2.index_entry_presentations(
        (entry_root / "e001.md").read_text(encoding="utf-8"),
        document="entries/2026-08-28-e001-study/e001.md",
    )
    V2.associate_presentations(evidence, presentations)
    references = V2.index_summary_references(summary.read_text(encoding="utf-8"))
    if len(references) != 1:
        raise AssertionError("summary reference missing")


class EvidenceUpgradeTests(unittest.TestCase):
    def test_preflight_partitions_every_v1_row_without_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _, _ = legacy_log(Path(directory))

            inventory = UPGRADE.inventory_upgrade(summary)

            self.assertEqual(len(inventory.rows), 2)
            self.assertEqual(
                inventory.counts,
                {"mechanical_candidate": 1, "summary_mapping_required": 1},
            )
            self.assertTrue(all(row.identity for row in inventory.rows))

    def test_stage_and_publish_replace_v1_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_root, entry = legacy_log(Path(directory))
            replacements, removals = authored_candidate(summary, entry_root, entry)

            staged = UPGRADE.stage_upgrade(
                summary,
                replacements=replacements,
                removals=removals,
                validate_candidate=validate_candidate,
            )
            self.assertTrue((entry_root / "evidence.csv").is_file())

            UPGRADE.publish_upgrade(staged, verify_published=validate_candidate)

            self.assertFalse(list(summary.with_suffix("").rglob("evidence.csv")))
            self.assertTrue((entry_root / "evidence.json").is_file())

    def test_snapshot_change_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_root, entry = legacy_log(Path(directory))
            replacements, removals = authored_candidate(summary, entry_root, entry)
            staged = UPGRADE.stage_upgrade(
                summary,
                replacements=replacements,
                removals=removals,
                validate_candidate=validate_candidate,
            )
            entry.write_text(entry.read_text(encoding="utf-8") + "changed\n")

            with self.assertRaisesRegex(
                UPGRADE.EvidenceUpgradeError, "upgrade.snapshot.changed"
            ):
                UPGRADE.publish_upgrade(staged, verify_published=validate_candidate)

            self.assertTrue((entry_root / "evidence.csv").is_file())
            self.assertFalse((entry_root / "evidence.json").exists())

    def test_failed_post_publish_verification_rolls_back_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_root, entry = legacy_log(Path(directory))
            replacements, removals = authored_candidate(summary, entry_root, entry)
            before = {
                path: path.read_bytes()
                for path in (summary, entry, entry_root / "evidence.csv")
            }
            staged = UPGRADE.stage_upgrade(
                summary,
                replacements=replacements,
                removals=removals,
                validate_candidate=validate_candidate,
            )

            with self.assertRaisesRegex(
                UPGRADE.EvidenceUpgradeError, "upgrade.publish.failed"
            ):
                UPGRADE.publish_upgrade(
                    staged,
                    verify_published=lambda _: (_ for _ in ()).throw(
                        AssertionError("forced failure")
                    ),
                )

            self.assertEqual(
                {path: path.read_bytes() for path in before},
                before,
            )
            self.assertFalse((entry_root / "evidence.json").exists())

    def test_interrupted_publication_is_recovered_from_durable_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry_root, entry = legacy_log(Path(directory))
            replacements, removals = authored_candidate(summary, entry_root, entry)
            observed = {
                path: path.read_bytes()
                for path in (
                    summary,
                    entry,
                    entry_root / "evidence.csv",
                    summary.with_suffix("") / "evidence.csv",
                )
            }
            staged = UPGRADE.stage_upgrade(
                summary,
                replacements=replacements,
                removals=removals,
                validate_candidate=validate_candidate,
            )

            with self.assertRaises(KeyboardInterrupt):
                UPGRADE.publish_upgrade(
                    staged,
                    verify_published=lambda _: (_ for _ in ()).throw(
                        KeyboardInterrupt()
                    ),
                )
            with self.assertRaisesRegex(
                UPGRADE.EvidenceUpgradeError, "upgrade.recovery.required"
            ):
                UPGRADE.inventory_upgrade(summary)

            self.assertTrue(UPGRADE.recover_upgrade(summary))
            self.assertEqual(
                {path: path.read_bytes() for path in observed},
                observed,
            )
            self.assertFalse((entry_root / "evidence.json").exists())
            self.assertFalse(UPGRADE.recover_upgrade(summary))


if __name__ == "__main__":
    unittest.main()
