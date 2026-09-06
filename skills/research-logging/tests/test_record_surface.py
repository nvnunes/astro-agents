from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
REVIEW_LENSES = REFERENCES / "review-lenses"
PROJECT = SKILL.parents[1]
CASES = SKILL / "tests" / "presented-evidence-cases.md"
SEMANTIC_REVIEW_CASES = SKILL / "tests" / "semantic-review-cases.md"
REFERENCE_PATTERN = re.compile(r"references/([A-Za-z0-9_./-]+\.md)")


def reference(name: str) -> str:
    return (REFERENCES / name).read_text(encoding="utf-8")


class RecordSurfaceTests(unittest.TestCase):
    def test_skill_routes_operations_without_loading_record_material(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        expected_operations = {
            "operation-record.md",
            "operation-reference.md",
            "operation-reorganize.md",
            "operation-reproduce.md",
            "operation-repair.md",
            "operation-replace.md",
            "operation-review.md",
            "operation-update-summary.md",
            "operation-validate.md",
        }
        references = set(REFERENCE_PATTERN.findall(skill))
        self.assertEqual(
            references,
            expected_operations
            | {"file-reproduction-records.md", "file-validation-records.md"},
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

    def test_review_routes_through_catalog_to_focused_lens_prompts(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        review = reference("operation-review.md")
        catalog = (REVIEW_LENSES / "catalog.md").read_text(encoding="utf-8")

        self.assertIn("A bounded question that merely overlaps a lens", skill)
        self.assertIn("Semantic Review is costly", review)
        self.assertIn("directly requests the act of\nsemantic review", review)
        self.assertIn("A bounded question that merely overlaps a lens", review)
        self.assertIn("references/review-lenses/catalog.md", review)
        self.assertIn("four-option group-first menu", review)
        self.assertIn("complete numbered lens catalog verbatim", review)
        self.assertIn("After reporting, stop Review", review)

        mapped = set(
            re.findall(r"`(references/review-lenses/[a-z-]+\.md)`", catalog)
        )
        expected = {
            f"references/review-lenses/{path.name}"
            for path in REVIEW_LENSES.glob("*.md")
            if path.name != "catalog.md"
        }
        self.assertEqual(len(expected), 19)
        self.assertEqual(mapped, expected)
        for raw in mapped:
            self.assertNotIn(raw, review)

        writing = (REVIEW_LENSES / "research-log-writing.md").read_text(
            encoding="utf-8"
        )
        normalized_writing = " ".join(writing.split())
        self.assertIn("$science-writing", normalized_writing)
        self.assertIn("do not silently add", normalized_writing)

    def test_semantic_review_cases_cover_routing_and_authority(self) -> None:
        cases = SEMANTIC_REVIEW_CASES.read_text(encoding="utf-8")
        normalized_cases = " ".join(cases.split())

        for expected in (
            "does not start semantic Review",
            "four-option group-first menu",
            "nineteen-lens catalog",
            "one material-first traversal",
            "all nineteen lenses",
            "does not execute a research command",
            "the agent stops for researcher direction",
            "does not automatically invoke Validate",
            "assigns no aggregate group verdict",
            "check this research log",
            "orphan artifacts have future analytical value",
            "semantic overlap with a catalog concern",
            "sibling `<log>/` tree",
            "verifies feasible arithmetic",
            "apparently evidential prose",
            "does not repeat recognizable missing-marker",
            "Validate's read-only diagnosis path",
            "`$science-writing`",
        ):
            self.assertIn(expected, normalized_cases)

    def test_validate_owns_read_only_mechanical_finding_diagnosis(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        validate = reference("operation-validate.md")
        review = reference("operation-review.md")
        cases = CASES.read_text(encoding="utf-8")

        self.assertIn("named mechanical-validation findings", skill)
        self.assertIn("## Diagnose Named Findings", validate)
        self.assertIn("log findings list", validate)
        self.assertIn("Do not apply a correction", validate)
        self.assertIn("Do not select review lenses", review)
        self.assertIn("does not rerun validation", cases)

    def test_creation_routes_do_not_load_naming_or_summary_contracts(self) -> None:
        for route in ("operation-record-start.md", "operation-record-new.md"):
            text = reference(route)
            self.assertNotIn("references/file-entry-naming.md", text, route)
            self.assertNotIn("references/file-summary", text, route)
        self.assertNotIn(
            "references/file-summary", reference("operation-record-content.md")
        )
        cases = CASES.read_text(encoding="utf-8")
        self.assertIn("lets `log add` own naming", cases)
        self.assertNotIn("loads naming and entry-structure guidance", cases)

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

    def test_atomic_bundle_guidance_stays_in_focused_references(self) -> None:
        commands = reference("file-entry-commands.md")
        data = reference("file-data-index.md")
        evidence = reference("file-presented-evidence.md")

        self.assertIn("owns the complete directory", commands)
        self.assertIn("register that generated\ndirectory once", data)
        self.assertIn("Do not register the member separately", evidence)
        for internal in (
            "MaterialCollection",
            "DirectoryProducerIndex",
            "directory-sha256-v1",
        ):
            self.assertNotIn(internal, commands)
            self.assertNotIn(internal, data)
            self.assertNotIn(internal, evidence)

    def test_log_local_code_guidance_stays_in_script_reference(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        record = reference("operation-record.md")
        script = reference("file-script.md")

        self.assertIn("ordinary imports or ordinary Python child invocations", script)
        self.assertIn("automatically records the log-local source files", script)
        self.assertNotIn("dependency observation", skill)
        self.assertNotIn("dependency observation", record)

    def test_every_reference_has_an_operation_route(self) -> None:
        pending = list(REFERENCE_PATTERN.findall((SKILL / "SKILL.md").read_text()))
        reached: set[str] = set()
        while pending:
            relative = pending.pop()
            if relative in reached:
                continue
            path = REFERENCES / relative
            self.assertTrue(path.is_file(), relative)
            reached.add(relative)
            pending.extend(REFERENCE_PATTERN.findall(path.read_text(encoding="utf-8")))

        all_references = {
            path.relative_to(REFERENCES).as_posix()
            for path in REFERENCES.rglob("*.md")
        }
        self.assertEqual(reached, all_references)

    def test_advanced_evidence_cases_route_to_one_focused_definition(self) -> None:
        presented = reference("file-presented-evidence.md")
        sources = reference("record-evidence-definition-sources.md")
        numeric = reference("record-evidence-definition-numeric.md")
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
        self.assertIn("use its transformation and source count", presented)
        self.assertIn("one-source statistic", sources)
        self.assertIn("consume several sources", sources)
        self.assertNotIn("record-evidence-definition-numeric", sources)
        self.assertIn("Use 1–8 ordered source objects", numeric)
        for locator_key in (
            "`path`",
            "`select`",
            "`where`",
            "`identity`",
            "`property`",
            "`text`",
            "`expect`",
        ):
            self.assertIn(locator_key, numeric)
        self.assertNotIn("references/operation-repair.md", presented)
        self.assertNotIn("references/operation-reorganize.md", presented)

    def test_behavior_cases_match_current_artifact_evidence_contract(self) -> None:
        cases = CASES.read_text(encoding="utf-8")
        self.assertIn("follows the common\nwhole-artifact workflow", cases)
        self.assertIn("one stable marker and one evidence record", cases)
        self.assertNotIn("receives no evidence record or\nmarker", cases)
        self.assertIn("loads exactly one definition", cases)
        self.assertNotIn("loads locator guidance", cases)

    def test_failed_record_stops_without_exceptional_operation_routing(self) -> None:
        content = reference("operation-record-content.md")
        data = reference("file-data-index.md")
        retention = reference("file-retention.md")
        for text in (content, data, retention):
            self.assertRegex(text.lower(), r"fail(?:ed|s|ure)")
            self.assertNotIn("references/operation-repair.md", text)
            self.assertNotIn("references/operation-reorganize.md", text)

    def test_pending_generated_registration_is_repair_only(self) -> None:
        repair = reference("operation-repair.md")
        ordinary = "\n".join(
            (
                (SKILL / "SKILL.md").read_text(encoding="utf-8"),
                reference("operation-record.md"),
                reference("operation-record-content.md"),
                reference("file-data-index.md"),
            )
        )
        self.assertIn("--pending-confirmation", repair)
        self.assertNotIn("--pending-confirmation", ordinary)

    def test_record_sequences_separately_requested_validation(self) -> None:
        content = reference("operation-record-content.md")
        cases = CASES.read_text(encoding="utf-8")
        self.assertIn("Do not run Validate within Record", content)
        self.assertIn("returning to the core operation selector", content)
        self.assertIn("record an investigation and then validate it", cases)

    def test_existing_record_resolves_a_split_entry_document(self) -> None:
        existing = reference("operation-record-existing.md")
        cases = CASES.read_text(encoding="utf-8")
        self.assertIn("Resolve the target document or section", existing)
        self.assertIn("split-entry documents plausibly match", existing)
        self.assertIn("split across several documents", cases)

    def test_multilog_reporting_is_owned_by_the_validation_tool(self) -> None:
        validate = reference("operation-validate.md")
        records = reference("file-validation-records.md")
        self.assertIn(
            "Its `report` field is the complete finished Markdown", validate
        )
        self.assertIn("Present it unchanged", validate)
        self.assertIn("every discovered log", validate)
        self.assertIn("every exceptional explanation", validate)
        self.assertIn("returns no\nstructured result", validate)
        self.assertIn(
            "includes every discovered log in the finished Markdown comparison",
            CASES.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "A nonzero batch may have empty standard error.",
            CASES.read_text(encoding="utf-8"),
        )
        self.assertFalse(
            (REFERENCES / "operation-validate-multilog-report.md").exists()
        )
        for implementation_detail in (
            "check_comparison",
            "SelectionResult",
            "256 KiB",
            "100,000-candidate",
        ):
            self.assertNotIn(implementation_detail, records)

    def test_human_operation_model_matches_the_skill(self) -> None:
        guide = (PROJECT / "docs" / "research-logging.md").read_text(
            encoding="utf-8"
        )
        naming = reference("file-entry-naming.md")
        self.assertIn("eight core operations", guide)
        self.assertIn("**Reproduce**", guide)
        self.assertIn("**Repair**", guide)
        self.assertIn("**Reorganize**", guide)
        self.assertIn("### Repair", guide)
        self.assertIn("### Reorganize", guide)
        self.assertIn("## Reproducing a research log", guide)
        self.assertNotIn("Reorganizing the log is part of Record", guide)
        self.assertIn("simultaneous Reorganize\nreorder", naming)

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
