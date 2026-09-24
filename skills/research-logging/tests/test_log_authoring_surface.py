"""Current authoring surfaces; historical rejection fixtures are not consumers."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import SCRIPTS, run_log
from test_log_command_sync import fixture
from test_log_evidence_sync import retained_files


class AuthoringSurfaceTests(unittest.TestCase):
    def test_help_distinguishes_evidence_and_lifecycle_actions(self):
        compare = run_log(SCRIPTS, "evidence", "compare", "--help")
        evidence_sync = run_log(SCRIPTS, "evidence", "sync", "--help")
        data_delete = run_log(SCRIPTS, "data", "delete", "--help")
        retention_add = run_log(SCRIPTS, "retention", "add", "--help")
        retention_update = run_log(SCRIPTS, "retention", "update", "--help")
        for result in (
            compare,
            evidence_sync,
            data_delete,
            retention_add,
            retention_update,
        ):
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("read-only", compare.stdout)
        self.assertIn("before/after", compare.stdout)
        self.assertIn("in its owning entry", compare.stdout)
        self.assertIn("refresh fingerprints", evidence_sync.stdout)
        self.assertIn("related evidence", evidence_sync.stdout)
        self.assertIn("in its owning entry", evidence_sync.stdout)
        self.assertIn("--producer", compare.stdout)
        self.assertIn("--producer", evidence_sync.stdout)
        self.assertIn("new markers", evidence_sync.stdout)
        self.assertIn("--dry-run is optional", evidence_sync.stdout)
        self.assertIn("declaration", data_delete.stdout)
        self.assertIn("retained bytes", data_delete.stdout)
        self.assertIn("sync", data_delete.stdout)
        self.assertIn("new", retention_add.stdout)
        self.assertIn("existing", retention_update.stdout)

    def test_help_contains_current_actions_without_removed_parameters(self):
        expected = {
            "command": {
                "sync",
                "release",
                "list",
                "verify",
                "show",
            },
            "evidence": {"compare", "sync", "list"},
            "data": {"update", "rename", "delete", "list"},
            "retention": {"add", "update", "rename", "delete", "list"},
        }
        for family, names in expected.items():
            result = run_log(SCRIPTS, family, "--help")
            self.assertEqual(result.returncode, 0, result.stderr)
            choice = re.search(r"\{([a-z,]+)\}", result.stdout)
            self.assertIsNotNone(choice, result.stdout)
            self.assertEqual(set(choice[1].split(",")), names, family)
            for action in names:
                result = run_log(SCRIPTS, family, action, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                for removed in (
                    "--definition",
                    "--data-kind",
                    "--fingerprint",
                    "--comparison-id",
                ):
                    self.assertNotIn(removed, result.stdout)
        sync_help = run_log(SCRIPTS, "evidence", "sync", "--help")
        for selector in ("--id", "--rename", "--delete"):
            self.assertIn(selector, sync_help.stdout)
        command_help = run_log(SCRIPTS, "command", "sync", "--help")
        for selector in ("--cid", "--rename", "--delete", "--delete-stale-executions"):
            self.assertIn(selector, command_help.stdout)
        self.assertNotIn("--delete-execution ", command_help.stdout)

    def test_retired_evidence_actions_fail_at_parser_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, _, _ = fixture(Path(directory), "./pyrun scripts/build.py")
            before = retained_files(logical)
            for action, extra in (
                ("rename", ("old", "new")),
                ("delete", ("--id", "old")),
            ):
                with self.subTest(action=action):
                    result = run_log(
                        logical.parent,
                        "evidence",
                        action,
                        "--path",
                        str(logical),
                        "--entry",
                        "e001",
                        *extra,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cli.arguments.invalid", result.stderr)
                    self.assertEqual(retained_files(logical), before)

    def test_runtime_and_active_guidance_have_no_removed_authoring_path(self):
        package = Path(__file__).resolve().parents[1]
        self.assertEqual(package / "scripts", SCRIPTS)
        project = package.parents[1]
        self.assertFalse(
            (package / "scripts/log_commands/evidence_definition.py").exists()
        )
        paths = list((package / "scripts").rglob("*.py"))
        paths += list((package / "references").rglob("*.md"))
        paths += [package / "SKILL.md"]
        paths += [
            project / "docs" / name
            for name in (
                "research-logging.md",
                "research-log-mechanical-validator-spec.md",
                "research-log-reproduction-spec.md",
            )
        ]
        forbidden = (
            r"def (?:add_origin|add_generated|apply_candidate_locked)\(",
            r"--definition(?:\s|=)",
            r"(?:scripts/log|\$LOG_TOOL\")\s+(?:data\s+(?:add-origin|add-generated|use)|evidence\s+(?:add|update))\b",
            r"mode\s*==\s*[\"'](?:structured|summary)[\"']",
            r"record-evidence-definition-(?:structured|summary)-tables\.md",
            r"schema\s*==\s*[\"']research-log-evidence/v4[\"']",
        )
        for path in paths:
            text = path.read_text()
            for pattern in forbidden:
                self.assertIsNone(re.search(pattern, text), f"{path}: {pattern}")

    def test_maintained_workflow_builders_use_current_surface(self):
        root = Path(__file__).resolve().parent
        command_guide = root.parent / "references/file-entry-commands.md"
        for removed in ("--remove", "--retire", "--delete-execution "):
            self.assertNotIn(removed, command_guide.read_text())
        # These are active workflow builders, not unsupported-route tests.
        for name in (
            "test_research_log_integrated_workflow.py",
            "test_log_record_workflow.py",
            "test_log_reorganize.py",
        ):
            text = (root / name).read_text()
            for removed in (
                '"add-origin"',
                '"add-generated"',
                '"use"',
                '"--definition"',
                '"research-log-data/v5"',
                '"research-log-evidence/v4"',
            ):
                self.assertNotIn(removed, text, name)
