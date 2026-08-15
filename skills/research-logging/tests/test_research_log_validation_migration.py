from research_log_validation_test_support import (
    IDENTITIES,
    MIGRATION,
    REPORT,
    RUNTIME,
    Path,
    adjudication_for,
    json,
    make_log,
    tempfile,
    unittest,
    write,
)


def legacy_summary(summary: Path) -> str:
    original = summary.read_text(encoding="utf-8").rstrip()
    return (
        original.replace(
            "# Mini Log\n\n",
            "# Mini Log\n\n## Contents\n\n"
            "- [Summary](#summary)\n"
            "- [Validation](#validation)\n"
            "- [Entries](#entries)\n"
            "- [AI Use](#ai-use)\n\n",
        )
        + "\n\n## Validation Method\n\nResearch-authored guidance.\n\n"
        "## Validation\n\n"
        "[Detailed validation report](mini/validation.md)\n\n"
        "Last validated on: 2026-08-07\n\n"
        "Summary statistics: `STALE`\n\n"
        "| Scope | Last checked | Integrity & Provenance | Reproducibility |\n"
        "| --- | --- | --- | --- |\n"
        "| e001 | `STALE` | `STALE` | `STALE` |\n\n"
        "## AI Use\n\nResearcher-led fixture.\n"
    )


class PublicationMigrationTests(unittest.TestCase):
    def test_research_operation_guidance_protects_generated_records(self) -> None:
        references = Path(__file__).resolve().parents[1] / "references"
        names = (
            "operation-record.md",
            "operation-record-content.md",
            "operation-record-start.md",
            "operation-replace.md",
            "operation-update-summary.md",
        )
        for name in names:
            with self.subTest(name=name):
                text = (references / name).read_text(encoding="utf-8")
                self.assertIn("validation", text.lower())
                if name != "operation-record-start.md":
                    self.assertIn("generated validation", text)

    def test_summary_migration_is_identity_neutral_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _entry = make_log(Path(directory))
            before_text = legacy_summary(summary)
            before = IDENTITIES.summary_validation_text_identity(summary, before_text)

            migrated = MIGRATION.migrate_summary_text(summary, before_text)

            self.assertEqual(
                migrated.splitlines()[1],
                "Validation: [latest completed report](mini/validation.md)",
            )
            self.assertNotIn("## Validation\n", migrated)
            self.assertNotIn("- [Validation](#validation)", migrated)
            self.assertIn("## Validation Method", migrated)
            self.assertEqual(
                IDENTITIES.summary_validation_text_identity(summary, migrated), before
            )
            self.assertEqual(
                MIGRATION.migrate_summary_text(summary, migrated), migrated
            )

    def test_summary_migration_rejects_missing_duplicate_and_mixed_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _entry = make_log(Path(directory))
            cases = {
                "neither": summary.read_text(encoding="utf-8"),
                "duplicate": legacy_summary(summary)
                + "\n## Validation\n\nDuplicate.\n",
                "mixed": legacy_summary(summary).replace(
                    "# Mini Log\n",
                    "# Mini Log\n"
                    "Validation: [latest completed report](mini/validation.md)\n",
                ),
            }
            for label, text in cases.items():
                with self.subTest(label=label), self.assertRaises(
                    MIGRATION.PublicationMigrationError
                ):
                    MIGRATION.migrate_summary_text(summary, text)

    def test_summary_named_validation_preserves_authored_validation_heading(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs" / "validation.md"
            text = (
                "# Validation Research Log\n\n"
                "## Contents\n\n"
                "- [Summary](#summary)\n"
                "- [Validation](#validation)\n\n"
                "## Summary\n\nCurrent research content.\n\n"
                "## Validation Method\n\nResearch-authored method.\n\n"
                "## Validation\n\nLast validated on: NOT RUN\n\n"
                "Summary statistics: NOT RUN\n\n"
                "## AI Use\n\nResearcher-led.\n"
            )

            migrated = MIGRATION.migrate_summary_text(summary, text)

            self.assertIn(
                "Validation: [latest completed report](validation/validation.md)",
                migrated,
            )
            self.assertIn("## Validation Method", migrated)
            self.assertNotIn("\n## Validation\n", migrated)

    def test_status_summary_covers_na_and_not_yet_reproduced(self) -> None:
        report = (
            "# Research-Log Validation\n\n"
            "- Report-update date: `2026-08-14`\n\n"
            "## Counts\n\n"
            "| Scope | Total rows | Failed rows |\n"
            "| --- | ---: | ---: |\n"
            "| Summary | 0 | 0 |\n"
            "| Entry targets | 1 | 0 |\n\n"
            "## Summary\n\n"
            "| Statistic | Entry | Section | Provenance |\n"
            "| --- | --- | --- | --- |\n\n"
            "## Entries\n\n"
            "### e001: Example\n\n"
            "| Target | Sections | Integrity | Provenance | Reproducibility | Notes |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| data.csv | Results | 2026-08-14 | 2026-08-14 | `-` | `-` |\n"
        )

        migrated = REPORT.install_status_summary(report)

        self.assertIn("- Summary statistics: `N/A`", migrated)
        self.assertIn(
            "| e001 | 2026-08-14 | 1 target checked; 0 failures | `-` |",
            migrated,
        )

    def test_repository_migration_publishes_bundle_then_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(summary, legacy_summary(summary))
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state_before = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            result = MIGRATION.migrate_repository(
                root, ["docs/mini.md"], RUNTIME.lint_records
            )

            self.assertEqual(result["count"], 1)
            self.assertIn("## Status Summary", (output / "validation.md").read_text())
            self.assertIn(
                "Validation: [latest completed report](mini/validation.md)",
                summary.read_text(),
            )
            state_after = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            for key in state_before:
                if key != "report":
                    self.assertEqual(state_after[key], state_before[key])

    def test_repository_migration_requires_exact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_log(root)
            with self.assertRaisesRegex(
                MIGRATION.PublicationMigrationError, "manifest differs"
            ):
                MIGRATION.migrate_repository(
                    root, ["docs/unexpected.md"], RUNTIME.lint_records
                )


if __name__ == "__main__":
    unittest.main()
