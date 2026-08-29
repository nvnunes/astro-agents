from __future__ import annotations

import importlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import write

EVIDENCE_V2 = importlib.import_module("validation.evidence")


def v2_fixture(root: Path) -> tuple[Path, Path, Path]:
    log_root = root / "docs" / "study"
    entry_root = log_root / "entries" / "2026-08-28-e001-study"
    document = entry_root / "e001.md"
    write(
        document,
        "# Entry\n\n"
        "## Trial\n\n"
        "`Question:`\n\nWhat happened?\n\n"
        "`Results:`\n\n"
        "The rate was `67.6%`<!-- eid:success-rate -->.\n\n"
        "<!-- eid:comparison-table -->\n"
        "Case | Rate\n"
        "--- | ---:\n"
        "candidate | 67.6%\n\n"
        "<!-- eid:run-output -->\n"
        "```text\n"
        "completed\n"
        "```\n",
    )
    write(
        entry_root / "evidence.json",
        """{
  "schema": "research-log-evidence/v2",
  "records": [
    {
      "id": "success-rate",
      "document": "entries/2026-08-28-e001-study/e001.md",
      "kind": "statistic",
      "sources": [{"source": "data/results.csv", "locator": {"select": [["rate"]]}}],
      "transformation": {"form": "percentage", "source": {"input": 0, "item": 0}}
    },
    {
      "id": "comparison-table",
      "document": "entries/2026-08-28-e001-study/e001.md",
      "kind": "table",
      "sources": [{
        "source": "data/results.csv",
        "locator": {"select": [["case"], ["rate"]]}
      }],
      "transformation": {
        "form": "table",
        "mode": "direct",
        "headings": ["Case", "Rate"],
        "columns": [{"form": "text"}, {"form": "percentage"}]
      }
    },
    {
      "id": "run-output",
      "document": "entries/2026-08-28-e001-study/e001.md",
      "kind": "output",
      "sources": [{
        "source": "data/run.log",
        "locator": {"text": {"contains": "completed"}}
      }],
      "transformation": null
    },
    {
      "id": "debug-traces",
      "kind": "retention",
      "paths": ["data/debug.json"],
      "reason": "Retained for later inspection."
    }
  ]
}
""",
    )
    write(entry_root / "data" / "results.csv", "case,rate\ncandidate,0.676\n")
    write(entry_root / "data" / "run.log", "completed\n")
    write(entry_root / "data" / "debug.json", "{}\n")
    return log_root, entry_root, document


class EvidenceFileTests(unittest.TestCase):
    def test_active_dispatch_is_v2_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs" / "study.md"
            log_root, entry_root, _ = v2_fixture(root)
            write(summary, "# Study\n")

            self.assertEqual(EVIDENCE_V2.active_evidence_version(summary), "v2")
            write(entry_root / "evidence.csv", "entry\n")
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "validation.upgrade_required"
            ):
                EVIDENCE_V2.active_evidence_version(summary)
            self.assertEqual(log_root, summary.with_suffix(""))

    def test_strict_file_decodes_presentations_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, _ = v2_fixture(Path(directory))

            evidence = EVIDENCE_V2.load_evidence_file(
                entry_root / "evidence.json",
                log_root=log_root,
                entry_root=entry_root,
            )

            self.assertEqual(len(evidence.records), 4)
            self.assertEqual(
                [record.id for record in evidence.records],
                ["success-rate", "comparison-table", "run-output", "debug-traces"],
            )
            self.assertEqual(
                [
                    record.id
                    for record in sorted(evidence.records, key=lambda row: row.id)
                ],
                ["comparison-table", "debug-traces", "run-output", "success-rate"],
            )
            self.assertEqual(len(evidence.identity), 64)

    def test_duplicate_keys_and_ids_fail_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, _ = v2_fixture(Path(directory))
            path = entry_root / "evidence.json"
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace('"schema":', '"schema":"x","schema":', 1))

            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "evidence.json.schema_invalid"
            ):
                EVIDENCE_V2.load_evidence_file(
                    path, log_root=log_root, entry_root=entry_root
                )

    def test_wrong_file_location_and_unsafe_retention_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, _ = v2_fixture(Path(directory))
            path = entry_root / "evidence.json"
            wrong = log_root / "evidence.json"
            write(wrong, path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "evidence.file.location_invalid"
            ):
                EVIDENCE_V2.load_evidence_file(
                    wrong, log_root=log_root, entry_root=entry_root
                )
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    '"data/debug.json"', '"../debug.json"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "evidence.declaration.invalid"
            ):
                EVIDENCE_V2.load_evidence_file(
                    path, log_root=log_root, entry_root=entry_root
                )

    def test_declared_documents_and_retained_targets_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, document = v2_fixture(Path(directory))
            path = entry_root / "evidence.json"
            document.unlink()
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "evidence.declaration.invalid"
            ):
                EVIDENCE_V2.load_evidence_file(
                    path, log_root=log_root, entry_root=entry_root
                )

    def test_file_and_individual_presentation_bounds_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, _ = v2_fixture(Path(directory))
            path = entry_root / "evidence.json"
            with mock.patch.object(EVIDENCE_V2, "MAX_EVIDENCE_FILE_BYTES", 4):
                with self.assertRaisesRegex(
                    EVIDENCE_V2.EvidenceV2Error,
                    "association.resource.too_large",
                ):
                    EVIDENCE_V2.load_evidence_file(
                        path, log_root=log_root, entry_root=entry_root
                    )

        prose = "x" * 100
        with mock.patch.object(EVIDENCE_V2, "MAX_PRESENTATION_BYTES", 8):
            indexed = EVIDENCE_V2.index_entry_presentations(
                f"{prose}\n`1`<!-- eid:value -->\n", document="entry.md"
            )
            self.assertEqual(len(indexed), 1)
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error,
                "association.resource.too_large",
            ):
                EVIDENCE_V2.index_entry_presentations(
                    f"`{'1' * 9}`<!-- eid:value -->\n", document="entry.md"
                )


class EvidenceV2AssociationTests(unittest.TestCase):
    def test_entry_markers_bind_all_three_presentation_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, document = v2_fixture(Path(directory))
            evidence = EVIDENCE_V2.load_evidence_file(
                entry_root / "evidence.json",
                log_root=log_root,
                entry_root=entry_root,
            )

            presentations = EVIDENCE_V2.index_entry_presentations(
                document.read_text(encoding="utf-8"),
                document="entries/2026-08-28-e001-study/e001.md",
            )
            associated = EVIDENCE_V2.associate_presentations(
                evidence, presentations
            )

            self.assertEqual(
                {item.kind for item in presentations},
                {"statistic", "table", "output"},
            )
            self.assertEqual(
                set(associated),
                {"success-rate", "comparison-table", "run-output"},
            )

    def test_marker_placement_kind_and_context_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, document = v2_fixture(Path(directory))
            evidence = EVIDENCE_V2.load_evidence_file(
                entry_root / "evidence.json",
                log_root=log_root,
                entry_root=entry_root,
            )
            text = document.read_text(encoding="utf-8").replace(
                "`67.6%`<!-- eid:success-rate -->",
                "`67.6%` <!-- eid:success-rate -->",
            )

            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "presentation.marker.invalid"
            ):
                EVIDENCE_V2.index_entry_presentations(
                    text,
                    document="entries/2026-08-28-e001-study/e001.md",
                )

            wrong_kind = tuple(
                item
                if item.id != "success-rate"
                else EVIDENCE_V2.PresentedItem(
                    id=item.id,
                    document=item.document,
                    kind="output",
                    value=item.value,
                    line=item.line,
                    section=item.section,
                    context_valid=True,
                )
                for item in EVIDENCE_V2.index_entry_presentations(
                    document.read_text(encoding="utf-8"),
                    document="entries/2026-08-28-e001-study/e001.md",
                )
            )
            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "association.kind_mismatch"
            ):
                EVIDENCE_V2.associate_presentations(evidence, wrong_kind)

            with self.assertRaisesRegex(
                EVIDENCE_V2.EvidenceV2Error, "presentation.marker.invalid"
            ):
                EVIDENCE_V2.index_entry_presentations(
                    document.read_text(encoding="utf-8").replace(
                        "<!-- eid:success-rate -->", "<!-- EID:success-rate -->"
                    ),
                    document="entries/2026-08-28-e001-study/e001.md",
                )

    def test_summary_references_preserve_exact_coordinates(self) -> None:
        text = (
            "# Summary\n\n"
            "The rate was `67.6%`"
            "<!-- ref entry = e001; eid = success-rate -->.\n"
            "The table rate was `67.6%`"
            "<!-- ref entry = e001; eid = comparison-table; row = 1; column = 2 -->.\n"
        )

        references = EVIDENCE_V2.index_summary_references(text)

        self.assertEqual(len(references), 2)
        self.assertEqual((references[1].row, references[1].column), (1, 2))
        with self.assertRaisesRegex(
            EVIDENCE_V2.EvidenceV2Error, "summary.reference.invalid"
        ):
            EVIDENCE_V2.index_summary_references(
                text.replace("entry = e001", "entry=e001", 1)
            )

    def test_summary_references_forward_exact_completed_presentations(self) -> None:
        references = EVIDENCE_V2.index_summary_references(
            "Rate `67.6%`<!-- ref entry = e001; eid = rate -->.\n"
            "Cell `3.39x`"
            "<!-- ref entry = e001; eid = table; row = 1; column = 2 -->.\n"
        )
        targets = {
            ("e001", "rate"): EVIDENCE_V2.CanonicalPresentation(
                kind="statistic", statistic="67.6%"
            ),
            ("e001", "table"): EVIDENCE_V2.CanonicalPresentation(
                kind="table",
                table=(("candidate", "3.39x"),),
                numerical_cells=frozenset({(1, 2)}),
            ),
        }

        resolved = EVIDENCE_V2.resolve_summary_references(references, targets)

        self.assertEqual(
            [association.forwarded_value for association in resolved],
            ["67.6%", "3.39x"],
        )
        with self.assertRaisesRegex(
            EVIDENCE_V2.EvidenceV2Error, "summary.reference.mismatch"
        ):
            EVIDENCE_V2.resolve_summary_references(
                (replace(references[0], value="67.7%"),),
                targets,
            )

    def test_direct_artifacts_preserve_normalized_markdown_targets(self) -> None:
        text = (
            "# Entry\n\n## Trial\n\n`Question:`\n\nQ\n\n`Results:`\n\n"
            "![Figure](../images/result.png) and "
            "[table](<data/result.csv> \"download\").\n"
            "[navigation](notes.md) [external](https://example.com/a.csv)\n"
            "```text\n[not an artifact](data/inside.csv)\n```\n"
        )

        artifacts = EVIDENCE_V2.index_direct_artifacts(
            text,
            document="entries/2026-08-28-e001-study/e001.md",
        )

        self.assertEqual(len(artifacts), 2)
        self.assertEqual(
            [artifact.normalized_target for artifact in artifacts],
            [
                "entries/images/result.png",
                "entries/2026-08-28-e001-study/data/result.csv",
            ],
        )
        self.assertTrue(artifacts[0].image)


if __name__ == "__main__":
    unittest.main()
