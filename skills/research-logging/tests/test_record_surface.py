from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
REFERENCE_PATTERN = re.compile(r"references/([A-Za-z0-9_.-]+\.md)")


def reference(name: str) -> str:
    return (REFERENCES / name).read_text(encoding="utf-8")


class RecordSurfaceTests(unittest.TestCase):
    def test_skill_routes_operations_without_loading_record_material(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        expected_operations = {
            "operation-record.md",
            "operation-reference.md",
            "operation-reorganize.md",
            "operation-repair.md",
            "operation-replace.md",
            "operation-review.md",
            "operation-update-summary.md",
            "operation-validate.md",
        }
        references = set(REFERENCE_PATTERN.findall(skill))
        self.assertEqual(
            references,
            expected_operations | {"file-validation-records.md"},
        )

        record = reference("operation-record.md")
        for routed in (
            "file-data-index.md",
            "file-entry-commands.md",
            "file-entry-labels.md",
            "file-presented-evidence.md",
            "file-references.md",
            "file-retention.md",
            "file-script.md",
            "research-log-writing.md",
        ):
            self.assertIn(f"references/{routed}", record)
        self.assertLess(
            record.index("## Select The Record Path"),
            record.index("## Material Routing"),
        )
        self.assertIn("read exactly one path", record)

    def test_record_subroutes_do_not_cross_operation_boundaries(self) -> None:
        record_routes = (
            "operation-record.md",
            "operation-record-start.md",
            "operation-record-new.md",
            "operation-record-existing.md",
            "operation-record-content.md",
        )
        forbidden = (
            "operation-reorganize.md",
            "operation-repair.md",
            "operation-replace.md",
            "operation-review.md",
            "operation-update-summary.md",
            "operation-validate.md",
        )
        for route in record_routes:
            text = reference(route)
            for name in forbidden:
                self.assertNotIn(f"references/{name}", text, route)

        for route in REFERENCES.glob("operation-*.md"):
            if route.name.startswith("operation-record"):
                continue
            text = route.read_text(encoding="utf-8")
            self.assertNotIn("references/operation-record", text, route.name)

    def test_creation_routes_do_not_load_naming_or_summary_contracts(self) -> None:
        for route in ("operation-record-start.md", "operation-record-new.md"):
            text = reference(route)
            self.assertNotIn("references/file-entry-naming.md", text, route)
            self.assertNotIn("references/file-summary", text, route)
        self.assertNotIn(
            "references/file-summary", reference("operation-record-content.md")
        )

    def test_ordinary_record_does_not_route_to_registry_grammars(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("## Record Contract", skill)
        self.assertIn("## Contract", reference("operation-record.md"))

        ordinary = "\n".join(
            (
                reference("operation-record-content.md"),
                reference("file-entry-commands.md"),
                reference("file-script.md"),
                reference("file-presented-evidence.md"),
                reference("file-data-index.md"),
                reference("file-retention.md"),
            )
        )
        for registry_grammar in (
            "research-log-data/v3",
            "research-log-evidence/v3",
            "research-log-retention/v1",
            '"fingerprint"',
            '"records"',
        ):
            self.assertNotIn(registry_grammar, ordinary)
        self.assertIn("Delegate deterministic", ordinary)
        for registry_name in ("data.json", "evidence.json", "retention.json"):
            self.assertNotIn(registry_name, ordinary)
        self.assertIn("Never create, inspect, or edit its registry", ordinary)
        self.assertIn("Never create, inspect, or edit the registry", ordinary)
        self.assertIn("Never create,\ninspect, or edit that registry", ordinary)

    def test_every_reference_has_an_operation_route(self) -> None:
        pending = list(REFERENCE_PATTERN.findall((SKILL / "SKILL.md").read_text()))
        reached: set[str] = set()
        while pending:
            name = pending.pop()
            if name in reached:
                continue
            path = REFERENCES / name
            self.assertTrue(path.is_file(), name)
            reached.add(name)
            pending.extend(REFERENCE_PATTERN.findall(path.read_text(encoding="utf-8")))

        candidate_reorganize = {
            "operation-reorganize-documents.md",
            "operation-reorganize-identity.md",
            "operation-reorganize-transfer.md",
        }
        all_references = {path.name for path in REFERENCES.glob("*.md")}
        self.assertEqual(reached | candidate_reorganize, all_references)
        for name in candidate_reorganize:
            self.assertNotIn(name, reached)

    def test_advanced_evidence_cases_route_to_one_focused_definition(self) -> None:
        presented = reference("file-presented-evidence.md")
        routed = (
            "record-evidence-definition-sources.md",
            "record-evidence-definition-numeric.md",
            "record-evidence-definition-direct-tables.md",
            "record-evidence-definition-structured-tables.md",
            "record-evidence-definition-summary-tables.md",
            "record-evidence-definition-outputs.md",
        )
        self.assertIn("evidence.common.unsupported", presented)
        for name in routed:
            self.assertEqual(presented.count(f"references/{name}"), 1)
        self.assertNotIn("references/operation-repair.md", presented)
        self.assertNotIn("references/operation-reorganize.md", presented)

    def test_failed_record_stops_without_exceptional_operation_routing(self) -> None:
        content = reference("operation-record-content.md")
        data = reference("file-data-index.md")
        retention = reference("file-retention.md")
        for text in (content, data, retention):
            self.assertRegex(text.lower(), r"fail(?:ed|s|ure)")
            self.assertNotIn("references/operation-repair.md", text)
            self.assertNotIn("references/operation-reorganize.md", text)

    def test_replace_removes_registries_before_old_artifacts(self) -> None:
        replace = reference("operation-replace.md")
        markdown = replace.index("remove the superseded Markdown")
        evidence = replace.index("log evidence remove")
        data = replace.index("log data remove")
        retention = replace.index("log retention remove")
        artifacts = replace.index("delete the explicitly\n   authorized old source")
        self.assertLess(markdown, evidence)
        self.assertLess(evidence, artifacts)
        self.assertLess(data, artifacts)
        self.assertLess(retention, artifacts)
        self.assertIn("stop on\n   the first failure", replace)
        self.assertIn("Never repair the failure by editing a registry", replace)


if __name__ == "__main__":
    unittest.main()
