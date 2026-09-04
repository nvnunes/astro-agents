from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_PATH = re.compile(
    r"`((?:docs|examples|skills)/[^`\n]+|(?:AGENTS|CHANGELOG|README)\.md)`"
)
PACKAGE_REFERENCE_PATH = re.compile(r"`(references/[^`\n]+)`")
BUNDLED_SCRIPT_PATH = re.compile(r"`(scripts/(?:pyrun|research_log_validation\.py))`")
REPAIR_SPEC_PATH = re.compile(
    r"`((?:\.\./)+docs/research-log-mechanical-validator-spec\.md)`"
)


class ResearchLogSkillPackageTests(unittest.TestCase):
    def test_runtime_guidance_references_only_package_resources(self) -> None:
        paths = [
            SKILL_ROOT / "SKILL.md",
            *sorted((SKILL_ROOT / "references").glob("*.md")),
        ]

        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertEqual(REPOSITORY_PATH.findall(text), [])

                for raw in PACKAGE_REFERENCE_PATH.findall(text):
                    self.assertTrue((SKILL_ROOT / raw).exists(), raw)

                for raw in BUNDLED_SCRIPT_PATH.findall(text):
                    self.assertTrue((SKILL_ROOT / raw).exists(), raw)

                repair_specs = REPAIR_SPEC_PATH.findall(text)
                self.assertEqual(
                    len(repair_specs), 1 if path.name == "operation-repair.md" else 0
                )
                for raw in repair_specs:
                    self.assertTrue((path.parent / raw).resolve().is_file(), raw)


if __name__ == "__main__":
    unittest.main()
