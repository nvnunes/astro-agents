from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from log_commands.reproduction_planner import ReproductionStateProjection
from log_commands.reproduction_results import (
    ArtifactCurrentness,
    ArtifactResult,
    ComparisonRecord,
    ReproductionResultError,
    ReproductionResults,
    RunFolder,
    RunResult,
    compose_reproduction_report,
    merge_reproduction_results,
    project_current_results,
    query_artifacts,
    reconcile_run_folders,
)
from research_log_data import Fingerprint
from validation.human_projection import EntryPresentation, ReportContext

FIXTURES = Path(__file__).parent / "fixtures"


class ReproductionResultContractTests(unittest.TestCase):
    def test_frozen_result_fixtures_are_exact_canonical_contracts(self) -> None:
        for path in sorted(FIXTURES.glob("reproduction-result-*.json")):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                result = ReproductionResults.from_json(text)
                self.assertEqual(result.serialized(), text)

    def test_unknown_field_and_noncanonical_order_are_rejected(self) -> None:
        value = json.loads(
            (FIXTURES / "reproduction-result-complete-v1.json").read_text()
        )
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
        current = ReproductionResults.from_json(
            (FIXTURES / "reproduction-result-complete-v1.json").read_text()
        )
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

        merged = merge_reproduction_results(
            current, (changed,), run, updated_at="2030-01-02T00:05:00Z"
        )

        self.assertEqual(len(merged.artifacts), len(current.artifacts))
        self.assertEqual(merged.runs[0].run_id, run.run_id)
        self.assertEqual(
            next(
                item
                for item in merged.artifacts
                if item.artifact == "data/matched.csv"
            ).outcome,
            "changed",
        )
        self.assertEqual(
            next(
                item
                for item in merged.artifacts
                if item.artifact == "data/failed.json"
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

    def test_reconciliation_removes_only_conclusively_absent_run_folders(self) -> None:
        current = ReproductionResults.from_json(
            (FIXTURES / "reproduction-result-complete-v1.json").read_text()
        )
        with self.subTest("missing beneath accessible tmp"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "tmp").mkdir()
                reconciled = reconcile_run_folders(current, project_root=root)

                self.assertEqual(reconciled.runs, ())
                self.assertEqual(reconciled.artifacts, current.artifacts)

        unknown = RunResult(
            current.runs[0].run_id,
            current.runs[0].target,
            current.runs[0].include_slow,
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
        current = ReproductionResults.from_json(
            (FIXTURES / "reproduction-result-complete-v1.json").read_text()
        )
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


class ReproductionReportTests(unittest.TestCase):
    def test_report_lists_every_entry_bolds_nonmatches_and_hides_reason_codes(
        self,
    ) -> None:
        result = ReproductionResults.from_json(
            (FIXTURES / "reproduction-result-complete-v1.json").read_text()
        )
        context = _context()
        currentness = {
            ("e003", "data/matched.csv"): ArtifactCurrentness(
                False, "execution_reran"
            )
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
        RunFolder(f"tmp/reproduce-research-{run_id}", "available"),
    )


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
