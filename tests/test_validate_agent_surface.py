"""Tests for the repository-local agent-surface validation harness."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

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

    def test_accepts_declared_skill_root_alias_locator(self) -> None:
        prompt_input = (
            f"- `r0` = `{self.link.parent}`\n"
            "- example: description (file: r0/astro-agents/example/SKILL.md)"
        )
        self.assertTrue(
            validator.skill_is_discovered(prompt_input, self.skill, self.link)
        )

    def test_rejects_wrong_locator(self) -> None:
        prompt_input = "- example: description (file: /unexpected/example/SKILL.md)"
        self.assertFalse(
            validator.skill_is_discovered(prompt_input, self.skill, self.link)
        )


class SkillSelectionTests(unittest.TestCase):
    def test_default_fixture_uses_selection_name(self) -> None:
        self.assertEqual(
            validator.SKILL_SELECTION_CASES.name,
            "skill_selection_cases.csv",
        )
        self.assertTrue(validator.SKILL_SELECTION_CASES.is_file())

    def test_prompt_uses_selection_contract(self) -> None:
        prompt = validator.build_skill_selection_eval_prompt(
            {
                "expected_skill": "example",
                "kind": "implicit",
                "should_select": "true",
                "prompt": "Review this example",
            }
        )

        self.assertIn('"selected": boolean', prompt)
        self.assertNotIn("activat", prompt.lower())

    def test_cli_exposes_only_selection_flags(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(validator.ROOT / "scripts" / "validate_agent_surface.py"),
                "--help",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )

        self.assertIn("--skill-selection-eval", completed.stdout)
        self.assertIn("--skill-selection-cases", completed.stdout)
        self.assertNotIn("--activation", completed.stdout)

    @mock.patch("scripts.validate_agent_surface.subprocess.run")
    def test_eval_reads_selection_response(self, run: mock.Mock) -> None:
        def write_response(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(
                '{"selected_skill":"example","selected":true,"reason":"matches"}',
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        run.side_effect = write_response
        result = validator.run_skill_selection_case(
            {
                "id": "implicit-example",
                "expected_skill": "example",
                "expected_selected_skill": "example",
                "should_select": "true",
                "kind": "implicit",
                "prompt": "Review this example",
            }
        )

        self.assertTrue(result["selected"])
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
