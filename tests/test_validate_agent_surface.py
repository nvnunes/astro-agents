"""Tests for the repository-local agent-surface validation harness."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts import validate_agent_surface as validator


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = validator.Skill(
            name="example",
            path=validator.SKILLS / "example" / "SKILL.md",
            description="Example skill",
        )
        self.link = Path("/users/example/.agents/skills/astro-agents")

    def test_accepts_source_repository_locator(self) -> None:
        prompt_input = f"- example: description (file: {self.skill.path})"
        self.assertTrue(
            validator.skill_is_discovered(prompt_input, self.skill, self.link)
        )

    def test_accepts_verified_symlink_locator(self) -> None:
        locator = self.link / "example" / "SKILL.md"
        prompt_input = f"- example: description (file: {locator})"
        self.assertTrue(
            validator.skill_is_discovered(prompt_input, self.skill, self.link)
        )

    def test_rejects_wrong_locator(self) -> None:
        prompt_input = "- example: description (file: /unexpected/example/SKILL.md)"
        self.assertFalse(
            validator.skill_is_discovered(prompt_input, self.skill, self.link)
        )


if __name__ == "__main__":
    unittest.main()
