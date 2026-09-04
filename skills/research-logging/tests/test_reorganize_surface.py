from __future__ import annotations

import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
CASES = SKILL / "tests" / "reorganize-candidate-cases.md"

CANDIDATES = {
    "identity": "operation-reorganize-identity.md",
    "documents": "operation-reorganize-documents.md",
    "transfer": "operation-reorganize-transfer.md",
}


class ReorganizeCandidateSurfaceTests(unittest.TestCase):
    def test_candidate_references_remain_unlinked_before_activation(self) -> None:
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL / "SKILL.md", *REFERENCES.glob("*.md"))
            if path.name not in CANDIDATES.values()
        )
        for name in CANDIDATES.values():
            self.assertNotIn(name, active)

    def test_each_candidate_owns_only_its_workflow_family(self) -> None:
        identity = (REFERENCES / CANDIDATES["identity"]).read_text(encoding="utf-8")
        documents = (REFERENCES / CANDIDATES["documents"]).read_text(
            encoding="utf-8"
        )
        transfer = (REFERENCES / CANDIDATES["transfer"]).read_text(encoding="utf-8")

        self.assertIn("log reorganize update-entry --help", identity)
        self.assertIn("log reorganize reorder --help", identity)
        self.assertIn("log reorganize relocate-log --help", identity)
        self.assertIn("log reorganize remove-empty-entry --help", identity)
        self.assertIn("log evidence rename", identity)
        self.assertNotIn("## Split One Stable Entry", identity)

        self.assertIn("## Move Within One Document", documents)
        self.assertIn("## Split One Entry Document", documents)
        self.assertIn("same-entry `transfer`", documents)
        self.assertNotIn("transfer --all", documents)

        self.assertIn("## Split One Stable Entry Into Two", transfer)
        self.assertIn("## Merge Two Stable Entries", transfer)
        self.assertIn("transfer --all", transfer)
        self.assertIn("reported rerun", transfer)

    def test_every_workflow_has_order_and_stop_conditions(self) -> None:
        combined = "\n".join(
            (REFERENCES / name).read_text(encoding="utf-8")
            for name in CANDIDATES.values()
        )
        for phrase in (
            "Markdown",
            "Dry-run",
            "Stop",
            "Do not",
            "one maintained log",
        ):
            self.assertIn(phrase.lower(), combined.lower())
        for schema in (
            "research-log-data/v3",
            "research-log-evidence/v3",
            "research-log-retention/v1",
            '"fingerprint"',
            '"records"',
        ):
            self.assertNotIn(schema, combined)

    def test_candidate_behavior_corpus_covers_distinct_compositions(self) -> None:
        cases = CASES.read_text(encoding="utf-8")
        for phrase in (
            "title-only",
            "simultaneous mapping",
            "within one document",
            "between documents",
            "stable-entry split",
            "stable-entry merge",
            "unresolved dependencies",
            "nonempty source",
        ):
            self.assertIn(phrase, cases)


if __name__ == "__main__":
    unittest.main()
