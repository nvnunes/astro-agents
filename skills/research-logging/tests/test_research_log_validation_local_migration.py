from __future__ import annotations

from research_log_validation_test_support import (
    GRAPH_STORE,
    LOCAL_MIGRATION,
    RUNTIME,
    Path,
    complete_adjudication,
    json,
    make_log,
    tempfile,
    unittest,
)


class LocalPublicationMigrationTests(unittest.TestCase):
    def test_report_migration_preserves_rows_and_moves_failure_detail(self) -> None:
        report = (
            "# Research-Log Validation\n\n"
            "- Log: `docs/mini.md`\n"
            "- Requested scope: complete standard scope\n"
            "- Report-update date: `2026-08-14`\n"
            "- Validation mode: standard\n"
            "- Validation-rules version: `research-log-validation-v43`\n"
            "- Failures: [validation-failures.md](validation-failures.md)\n\n"
            "## Status Summary\n\n"
            "- Report updated: `2026-08-14`\n"
            "- Summary statistics: `N/A`\n"
            "- Remediation queue: [validation-failures.md](validation-failures.md)\n\n"
            "## Counts\n\n"
            "| Scope | Total rows | Failed rows |\n"
            "| --- | ---: | ---: |\n"
            "| Summary | 0 | 0 |\n"
            "| Entry targets | 1 | 1 |\n\n"
            "Entries: 1 total; 1 containing a failed target row.\n\n"
            "## Summary\n\n"
            "| Statistic | Entry | Section | Provenance |\n"
            "| --- | --- | --- | --- |\n\n"
            "## Entries\n\n"
            "### e001: Fixture\n\n"
            "Entry: `docs/mini/e001.md`\n\n"
            "| Target | Section | Integrity | Provenance | Reproducibility | Notes |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| item.csv | Results | `FAIL` | `FAIL` | `N/A` | `-` |\n"
        )
        failures = (
            "# Validation Failures\n\n"
            "## e001\n\n"
            "### item.csv\n\n"
            "- Check: Integrity\n"
            "- Finding: Missing material.\n"
        )
        migrated = LOCAL_MIGRATION.migrate_report_text(report, failures, "a" * 64)
        self.assertIn("- Local snapshot identity: `" + "a" * 64 + "`", migrated)
        self.assertIn("- Remediation queue: [Remediation](#remediation)", migrated)
        self.assertIn("## Remediation\n\n### e001\n\n#### item.csv", migrated)
        self.assertNotIn("validation-failures.md", migrated)

    def test_schema9_and_schema6_migrate_without_cache_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            summary, _ = make_log(Path(raw))
            scan, _ = RUNTIME.scan_log(summary)
            adjudication = complete_adjudication(scan)
            RUNTIME.render_records(adjudication, scan, summary.with_suffix(""))
            output_dir = summary.with_suffix("")
            state_path = output_dir / "validation-state.json"
            index_path = output_dir / GRAPH_STORE.SLICE_FILENAME
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 9
            del state["local_snapshot_identity"]
            state_path.write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["schema_version"] = 6
            del index["local_snapshot_identity"]
            index_path.write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (output_dir / "validation-decisions.json").unlink()
            staging = output_dir.parent / f".{output_dir.name}-validation-staging-old"
            staging.mkdir()
            (staging / "validation.md").write_text("obsolete\n", encoding="utf-8")
            state_before = state_path.read_bytes()
            index_before = index_path.read_bytes()

            dry_run = LOCAL_MIGRATION.migrate_log(summary)
            self.assertFalse(dry_run["applied"])
            self.assertEqual(dry_run["artifacts_rehashed"], 0)
            self.assertFalse(dry_run["semantic_review_performed"])
            result = LOCAL_MIGRATION.migrate_log(summary, apply=True)

            self.assertTrue(result["applied"])
            self.assertTrue(result["lint"]["durable_ok"])
            self.assertFalse(result["lint"]["cache_usable"])
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertEqual(index_path.read_bytes(), index_before)
            self.assertTrue((output_dir / "validation-decisions.json").is_file())
            self.assertFalse(staging.exists())

            aggregate = Path(raw) / ".research-log-validation-index"
            aggregate.mkdir()
            (aggregate / "manifest.json").write_text("{}\n", encoding="utf-8")
            (aggregate / "incoming.json").write_text("{}\n", encoding="utf-8")
            repository_lock = Path(raw) / ".research-log-validation.lock"
            repository_lock.write_text("", encoding="utf-8")
            cleanup = LOCAL_MIGRATION.cleanup_repository_artifacts(
                Path(raw), apply=True
            )
            self.assertTrue(cleanup["applied"])
            self.assertFalse(aggregate.exists())
            self.assertFalse(repository_lock.exists())


if __name__ == "__main__":
    unittest.main()
