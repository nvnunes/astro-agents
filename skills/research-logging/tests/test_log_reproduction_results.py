from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_results import (
    ArtifactCurrentness,
    ArtifactResult,
    CommandResult,
    ComparisonRecord,
    ReproductionResultError,
    ReproductionResults,
    RunFolder,
    RunResult,
    compose_reproduction_reconciliation_summary,
    compose_reproduction_report,
    compose_reproduction_summary,
    merge_reproduction_results,
    project_current_results,
    query_artifacts,
    reconcile_run_folders,
)
from research_log_data import Fingerprint
from validation.human_projection import (
    EntryPresentation,
    ReportContext,
    load_report_context,
)


class ReproductionResultContractTests(unittest.TestCase):
    def test_v3_result_is_readable_and_upgrades_without_inventing_commands(
        self,
    ) -> None:
        value = _complete_results().as_dict()
        value["schema"] = "research-log-reproduction-result/3"
        del value["commands"]

        decoded = ReproductionResults.from_json(_canonical(value))

        self.assertEqual(decoded.commands, ())
        self.assertIn(
            '"schema": "research-log-reproduction-result/4"', decoded.serialized()
        )

    def test_v2_result_is_not_retained_as_a_compatibility_format(self) -> None:
        value = _complete_results().as_dict()
        value["schema"] = "research-log-reproduction-result/2"
        del value["commands"]

        with self.assertRaisesRegex(ReproductionResultError, "schema is unsupported"):
            ReproductionResults.from_json(_canonical(value))

    def test_command_result_round_trips_and_merges_by_execution_identity(self) -> None:
        current = _complete_results()
        command = CommandResult(
            "e003",
            "pyrun-exec/v1:" + "1" * 64,
            "failed",
            "a" * 64,
            "2030-01-02T00:05:00Z",
            "reproduce-20300102t000000z-fixture",
        )
        merged = merge_reproduction_results(
            current,
            (),
            _run("reproduce-20300102t000000z-fixture", "2030-01-02T00:00:00Z"),
            commands=(command,),
        )

        decoded = ReproductionResults.from_json(merged.serialized())

        self.assertEqual(decoded.commands, (command,))

    def test_evidence_comparison_details_round_trip_durably(self) -> None:
        comparison = ComparisonRecord(
            "evidence",
            Fingerprint("sha256", digest="a" * 64),
            Fingerprint("sha256", digest="b" * 64),
            "c" * 64,
            (
                {
                    "definition": "d" * 64,
                    "expected": [{"value": {"type": "integer", "value": "1"}}],
                    "id": "score",
                    "matched": True,
                    "regenerated": [{"value": {"type": "integer", "value": "1"}}],
                    "tolerance": None,
                },
            ),
        )
        artifact = ArtifactResult(
            "e001",
            "data/result.json",
            "pyrun-exec/v1:" + "1" * 64,
            "matched",
            None,
            "2030-01-01T00:00:00Z",
            "reproduce-20300101t000000z-fixture",
            comparison,
        )
        result = ReproductionResults(
            "docs/research.md",
            "2030-01-01T00:00:00Z",
            (artifact,),
            (_run("reproduce-20300101t000000z-fixture", "2030-01-01T00:00:00Z"),),
        )

        decoded = ReproductionResults.from_json(result.serialized())

        self.assertEqual(decoded.artifacts[0].comparison, comparison)

    def test_unknown_field_and_noncanonical_order_are_rejected(self) -> None:
        value = _complete_results().as_dict()
        value["extra"] = True
        with self.assertRaises(ReproductionResultError):
            ReproductionResults.from_json(_canonical(value))
        del value["extra"]
        value["artifacts"].reverse()
        with self.assertRaises(ReproductionResultError):
            ReproductionResults.from_json(_canonical(value))

    def test_merge_replaces_only_selected_cases_and_preserves_other_entries(
        self,
    ) -> None:
        current = _complete_results()
        changed = ArtifactResult(
            "e003",
            "data/matched.csv",
            "pyrun-exec/v1:" + "4" * 64,
            "changed",
            "content_changed",
            "2030-01-02T00:05:00Z",
            "reproduce-20300102t000000z-fixture",
            ComparisonRecord(
                "table",
                Fingerprint("sha256", digest="e" * 64),
                Fingerprint("sha256", digest="f" * 64),
            ),
        )
        run = _run("reproduce-20300102t000000z-fixture", "2030-01-02T00:00:00Z")

        merged = merge_reproduction_results(current, (changed,), run)

        self.assertEqual(len(merged.artifacts), len(current.artifacts))
        self.assertEqual(merged.runs[0].run_id, run.run_id)
        self.assertEqual(
            next(
                item for item in merged.artifacts if item.artifact == "data/matched.csv"
            ).outcome,
            "changed",
        )
        self.assertEqual(
            next(
                item for item in merged.artifacts if item.artifact == "data/failed.json"
            ).run_id,
            current.runs[0].run_id,
        )

    def test_failed_pre_execution_case_may_have_no_execution_id(self) -> None:
        failed = ArtifactResult(
            "e001",
            "data/result.csv",
            None,
            "failed",
            "graph_limit",
            "2030-01-01T00:00:00Z",
            "reproduce-20300101t000000z-fixture",
            None,
        )
        result = ReproductionResults(
            "docs/research.md",
            "2030-01-01T00:00:00Z",
            (failed,),
            (_run("reproduce-20300101t000000z-fixture", "2030-01-01T00:00:00Z"),),
        )

        self.assertIsNone(
            ReproductionResults.from_json(result.serialized()).artifacts[0].execution_id
        )

    def test_failed_external_generated_input_retains_absolute_identity(self) -> None:
        artifact = ArtifactResult(
            "e001",
            "/Volumes/Data/fixture/build.log",
            None,
            "failed",
            "cross_log_generated_input",
            "2030-01-01T00:00:00Z",
            "reproduce-20300101t000000z-fixture",
            None,
        )
        result = ReproductionResults(
            "docs/research.md",
            "2030-01-01T00:00:00Z",
            (artifact,),
            (_run("reproduce-20300101t000000z-fixture", "2030-01-01T00:00:00Z"),),
        )

        decoded = ReproductionResults.from_json(result.serialized())

        self.assertEqual(decoded.artifacts[0].artifact, artifact.artifact)
        with self.assertRaisesRegex(ReproductionResultError, "not a canonical path"):
            ArtifactResult(
                "e001",
                "/Volumes/Data/fixture/../build.log",
                None,
                "failed",
                "cross_log_generated_input",
                "2030-01-01T00:00:00Z",
                "reproduce-20300101t000000z-fixture",
                None,
            )

    def test_reconciliation_removes_only_conclusively_absent_run_folders(self) -> None:
        current = _complete_results()
        with self.subTest("missing beneath accessible tmp"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "tmp").mkdir()
                reconciled = reconcile_run_folders(current, project_root=root)

                self.assertEqual(reconciled.runs, ())
                self.assertEqual(reconciled.artifacts, current.artifacts)

        with self.subTest("intentional accessible tmp symlink"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                external = root / "external"
                external.mkdir()
                (root / "tmp").symlink_to(external, target_is_directory=True)
                run_root = external.joinpath(
                    *Path(current.runs[0].folder.path).parts[1:]
                )
                run_root.mkdir(parents=True)

                available = reconcile_run_folders(current, project_root=root)
                self.assertEqual(available.runs[0].folder.availability, "available")
                run_root.rmdir()
                absent = reconcile_run_folders(current, project_root=root)
                self.assertEqual(absent.runs, ())

        unknown = RunResult(
            current.runs[0].run_id,
            current.runs[0].target,
            current.runs[0].include_all,
            current.runs[0].status,
            current.runs[0].accepted_at,
            current.runs[0].finished_at,
            current.runs[0].artifact_outcomes,
            RunFolder(current.runs[0].folder.path, "unknown"),
        )
        retained = ReproductionResults(
            current.summary, current.updated_at, current.artifacts, (unknown,)
        )
        with self.subTest("filesystem unavailable"):
            with mock.patch.object(Path, "is_dir", side_effect=OSError("offline")):
                reconciled = reconcile_run_folders(retained, project_root=Path("/x"))

            self.assertEqual(len(reconciled.runs), 1)
            self.assertEqual(reconciled.runs[0].folder.availability, "unknown")

    def test_projection_ignores_unreachable_and_derives_timestamp_staleness(
        self,
    ) -> None:
        current = _complete_results()
        matched = next(
            item for item in current.artifacts if item.artifact == "data/matched.csv"
        )
        state = ReproductionStateProjection(
            frozenset({(matched.entry, matched.artifact)}),
            {(matched.entry, matched.artifact): matched.execution_id},
            {(matched.entry, matched.execution_id): "2030-01-01T00:06:00Z"},
        )

        projected, currentness = project_current_results(current, state)

        self.assertEqual(projected.artifacts, (matched,))
        self.assertEqual(
            currentness[(matched.entry, matched.artifact)],
            ArtifactCurrentness(False, "execution_reran"),
        )

    def test_comparison_definition_change_makes_prior_result_stale(self) -> None:
        current = _complete_results()
        matched = next(
            item for item in current.artifacts if item.artifact == "data/matched.csv"
        )
        key = (matched.entry, matched.artifact)
        state = ReproductionStateProjection(
            frozenset({key}),
            {key: matched.execution_id},
            {(matched.entry, matched.execution_id): None},
            {key: "f" * 64},
        )

        _, currentness = project_current_results(current, state)

        self.assertEqual(
            currentness[key], ArtifactCurrentness(False, "comparison_changed")
        )


class ReproductionReportTests(unittest.TestCase):
    def test_no_work_summary_uses_current_command_reconciliation(self) -> None:
        summary = compose_reproduction_reconciliation_summary(
            _complete_results(),
            {
                "blocked": 0,
                "failed": 0,
                "not_automatic": 61,
                "reused": 189,
                "succeeded": 0,
                "total": 250,
            },
            generated_at="2030-01-02T00:00:00Z",
        )

        self.assertIn(
            "Current reconciliation: no commands executed; no run was created.",
            summary,
        )
        self.assertIn("Latest completed run remains:", summary)
        self.assertIn(
            "250 total\n"
            "├─ 61 skipped by policy (not automatic)\n"
            "├─ 189 reused from saved state\n"
            "└─ 0 selected for execution\n"
            "   ├─ 0 succeeded\n"
            "   ├─ 0 failed\n"
            "   └─ 0 blocked",
            summary,
        )
        self.assertIn("5 total\n├─ 1 matched\n├─ 1 not matched", summary)

    def test_unchanged_incremental_run_reports_all_commands_reused(self) -> None:
        result = _complete_results()
        run = replace(
            result.runs[0],
            command_outcomes={
                "blocked": 0,
                "failed": 0,
                "not_automatic": 2,
                "reused": 10,
                "succeeded": 0,
                "total": 12,
            },
        )

        summary = compose_reproduction_summary(replace(result, runs=(run,)))

        self.assertIn(
            "12 total\n"
            "├─ 2 skipped by policy (not automatic)\n"
            "├─ 10 reused from saved state\n"
            "└─ 0 selected for execution\n"
            "   ├─ 0 succeeded\n"
            "   ├─ 0 failed\n"
            "   └─ 0 blocked",
            summary,
        )

    def test_compact_summary_reconciles_commands_separately_from_artifacts(
        self,
    ) -> None:
        result = _complete_results()
        summary = compose_reproduction_summary(result)

        self.assertIn(
            "12 total\n"
            "├─ 2 skipped by policy (not automatic)\n"
            "├─ 3 reused from saved state\n"
            "└─ 7 selected for execution\n"
            "   ├─ 4 succeeded\n"
            "   ├─ 1 failed\n"
            "   └─ 2 blocked",
            summary,
        )
        self.assertIn(
            "5 total\n├─ 1 matched\n├─ 1 not matched\n└─ 3 not compared",
            summary,
        )
        self.assertIn("1 comparison failed", summary)
        self.assertIn("1 command failed", summary)
        self.assertIn("1 command blocked", summary)
        self.assertIn("totals are not expected to match", summary)

    def test_artifact_summary_lists_skipped_after_every_other_reason(self) -> None:
        result = _complete_results()
        skipped = ArtifactResult(
            "e003",
            "data/manual.txt",
            "pyrun-exec/v1:" + "6" * 64,
            "skipped",
            "non_automatic",
            "2030-01-01T00:05:00Z",
            "reproduce-20300101t000000z-fixture",
            None,
        )

        summary = compose_reproduction_summary(
            replace(
                result,
                artifacts=tuple(
                    sorted((*result.artifacts, skipped), key=lambda item: item.artifact)
                ),
            )
        )

        reasons = (
            "comparison failed",
            "command failed",
            "command blocked",
            "command skipped",
        )
        positions = tuple(summary.index(reason) for reason in reasons)
        self.assertEqual(positions, tuple(sorted(positions)))

    def test_split_documents_project_as_one_stable_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "docs/study"
            root.mkdir(parents=True)
            summary = root.with_suffix(".md")
            summary.write_text(
                "# Study\n\n"
                "Validation: [latest completed report](study/validation.md)\n\n"
                "Reproduction: [latest report](study/reproduction.md)\n\n"
                "## Entries\n\n"
                "- `2030-01-01` Split Study:\n"
                "  - [Part A](study/entries/2030-01-01-e002-split/e002a.md)\n"
                "  - [Part B](study/entries/2030-01-01-e002-split/e002b.md)\n",
                encoding="utf-8",
            )

            report = compose_reproduction_report(
                ReproductionResults("docs/study.md", "2030-01-01T00:00:00Z", (), ()),
                context=load_report_context(summary),
            )

            self.assertIn("## [e002 — Split Study]", report)
            self.assertNotIn("## [e002a", report)
            self.assertNotIn("## [e002b", report)

    def test_report_lists_every_entry_bolds_nonmatches_and_hides_reason_codes(
        self,
    ) -> None:
        result = _complete_results()
        context = _context()
        currentness = {
            ("e003", "data/matched.csv"): ArtifactCurrentness(False, "execution_reran")
        }

        report = compose_reproduction_report(
            result,
            context=context,
            currentness=currentness,
            folder_links_from=context.log_root,
        )

        self.assertIn("## [e002 — Earlier](entries/e002.md)", report)
        self.assertIn("## [e003 — Example](entries/e003.md)", report)
        self.assertIn("| `data/changed.bin` | **changed** |", report)
        self.assertIn("| `data/matched.csv` | **matched (stale)** |", report)
        self.assertNotIn("content_changed", report)
        self.assertNotIn("dependency_cycle", report)
        self.assertIn("| Run ID | Target | Run status | Time | Folder |", report)

    def test_bounded_query_reports_exact_matched_returned_and_omitted_counts(
        self,
    ) -> None:
        run_id = "reproduce-20300101t000000z-query"
        artifacts = tuple(
            ArtifactResult(
                "e001",
                f"data/result-{number:03d}.csv",
                "pyrun-exec/v1:" + f"{number:064x}",
                "matched",
                None,
                "2030-01-01T00:00:00Z",
                run_id,
                ComparisonRecord(
                    "table",
                    Fingerprint("sha256", digest="a" * 64),
                    Fingerprint("sha256", digest="a" * 64),
                ),
            )
            for number in range(60)
        )
        result = ReproductionResults(
            "docs/research.md",
            "2030-01-01T00:00:00Z",
            artifacts,
            (_run(run_id, "2030-01-01T00:00:00Z", count=60),),
        )

        query = query_artifacts(result, entry="e001", outcome="matched")

        self.assertEqual((query.matched, query.returned, query.omitted), (60, 50, 10))
        self.assertEqual(len(query.records), 50)


def _run(run_id: str, accepted: str, *, count: int = 1) -> RunResult:
    return RunResult(
        run_id,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        accepted,
        accepted,
        {
            "matched": count,
            "changed": 0,
            "failed": 0,
            "comparison_failed": 0,
            "skipped": 0,
        },
        RunFolder(
            f"tmp/reproduction/{accepted[:10]}/reproduce-research-{run_id}",
            "available",
        ),
    )


def _complete_results() -> ReproductionResults:
    run_id = "reproduce-20300101t000000z-fixture"
    recorded_at = "2030-01-01T00:05:00Z"
    specs = (
        (
            "data/changed.bin",
            "1",
            "changed",
            "content_changed",
            "opaque_file",
            "a",
            "b",
        ),
        (
            "data/comparison.dat",
            "2",
            "comparison_failed",
            "unsupported_format",
            "opaque_file",
            "c",
            "d",
        ),
        ("data/failed.json", "3", "failed", "dependency_cycle", None, None, None),
        ("data/matched.csv", "4", "matched", None, "table", "e", "e"),
        ("data/skipped.png", "5", "skipped", "dependency_failed", None, None, None),
    )
    artifacts = tuple(
        ArtifactResult(
            "e003",
            artifact,
            "pyrun-exec/v1:" + digit * 64,
            outcome,
            reason,
            recorded_at,
            run_id,
            (
                ComparisonRecord(
                    profile,
                    Fingerprint("sha256", digest=expected * 64),
                    Fingerprint("sha256", digest=regenerated * 64),
                )
                if profile is not None
                else None
            ),
        )
        for artifact, digit, outcome, reason, profile, expected, regenerated in specs
    )
    run = RunResult(
        run_id,
        {"entry": None, "kind": "log"},
        False,
        "complete",
        "2030-01-01T00:00:00Z",
        recorded_at,
        {
            "changed": 1,
            "comparison_failed": 1,
            "failed": 1,
            "matched": 1,
            "skipped": 1,
        },
        RunFolder(
            "tmp/reproduction/2030-01-01/"
            "reproduce-research-reproduce-20300101t000000z-fixture",
            "available",
        ),
        (),
        {
            "blocked": 2,
            "failed": 1,
            "not_automatic": 2,
            "reused": 3,
            "succeeded": 4,
            "total": 12,
        },
    )
    return ReproductionResults("docs/research.md", recorded_at, artifacts, (run,))


def _context() -> ReportContext:
    root = Path("/project/docs/research")
    return ReportContext(
        "Research",
        root.with_suffix(".md"),
        root,
        {
            "e002": EntryPresentation(
                "e002", "Earlier", "entries/e002.md", root / "entries" / "e002"
            ),
            "e003": EntryPresentation(
                "e003", "Example", "entries/e003.md", root / "entries" / "e003"
            ),
        },
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    unittest.main()
