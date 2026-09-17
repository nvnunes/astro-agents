"""Public saved inspection stays immutable, bounded and agent-actionable."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import research_log_result_store as shared
from log_commands import dispatcher
from log_commands.context import LogContext
from log_commands.model import ActionError
from log_commands.reproduction_domain import ArtifactOutcome, ArtifactRef, WorkSelection
from log_commands.reproduction_inspection import (
    ListSelection,
    SavedInspection,
    artifact_detail,
    command_detail,
    list_saved,
    load_inspection,
    render_saved_summary,
)
from log_commands.reproduction_paths import saved_run_path
from log_commands.reproduction_root_summary import render_root_summary, root_summary
from log_commands.reproduction_run import ArtifactResult
from log_commands.reproduction_saved_run import RunTarget
from log_commands.reproduction_saved_storage import (
    replace_reproduction_domain,
    write_saved_run,
)
from log_commands.reproduction_work import ArtifactWork
from test_reproduction_canonical_records import (
    FINGERPRINT,
    WHEN,
    attempted,
    command,
    mixed_run,
)

MIXED_SUMMARY = """Log: study
Run: reproduce-mixed
Saved: Sep 15
Target: Log
Status: Complete

Commands — 7
  Not run — 3
    Not needed — 1
    Previous failure — 1
    Previous block — 1
  Skipped by policy — 1
  Selected — 3
    Succeeded — 1
    Failed — 1
      Execution failed — 1
    Blocked — 1
      Execution failed — 1

Artifacts — 4
  Matched — 1
  Not matched — 1
  Not compared — 2
    Command blocked — 1
    Command failed — 1
"""


class SavedInspectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / "study"
        (self.root / "entries").mkdir(parents=True)
        self.summary = self.root.with_suffix(".md")
        self.summary.write_text("# Study\n", encoding="utf-8")
        self.log = LogContext(self.summary, self.root)
        mixed = mixed_run()
        self.run = replace(
            mixed,
            summary=str(self.summary),
            commands=tuple(
                replace(work, project_root=str(self.root.parent))
                for work in mixed.commands
            ),
        )
        self.inspection = SavedInspection(self.log, self.run, 7)
        path = shared.result_store_path(self.root)
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text()
            )
            db.execute("BEGIN")
            replace_reproduction_domain(db)
            write_saved_run(db, self.run)
            db.execute("INSERT INTO store_state VALUES ('reproduction',7,'native')")

    def dispatch(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = dispatcher.main(
                ["reproduce", *arguments, "--path", str(self.root)]
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def test_public_mixed_summary_matches_independent_golden_without_source_reads(self):
        before = shared.result_store_path(self.root).read_bytes()
        with (
            mock.patch(
                "pathlib.Path.read_text", side_effect=AssertionError("source read")
            ),
            mock.patch(
                "pathlib.Path.read_bytes", side_effect=AssertionError("source read")
            ),
        ):
            self.assertEqual(self.dispatch("show"), (0, MIXED_SUMMARY, ""))
            loaded = load_inspection(self.log)
        self.assertEqual(loaded.run, self.run)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)
        self.assertEqual(render_saved_summary(self.inspection), MIXED_SUMMARY)

    def test_public_historical_show_filtered_artifacts_and_detail_selectors(self):
        later = replace(self.run, run_id="reproduce-later")
        with shared.result_transaction(self.root) as db:
            write_saved_run(db, later)
        before = shared.result_store_path(self.root).read_bytes()
        status, output, error = self.dispatch(
            "show", "--run-id", self.run.run_id, "--format", "json"
        )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output)["run_id"], self.run.run_id)
        status, output, error = self.dispatch(
            "list",
            "artifacts",
            "--entry",
            "e001",
            "--cid",
            "producer",
            "--status",
            "not-compared",
            "--reason",
            "command-failed",
            "--run-id",
            self.run.run_id,
            "--format",
            "json",
        )
        self.assertEqual((status, error), (0, ""))
        rows = json.loads(output)["items"]
        self.assertEqual(len(rows), 1)
        artifact = rows[0]["identity"]
        status, output, error = self.dispatch(
            "detail",
            "artifact",
            "--entry",
            "e001",
            "--artifact",
            artifact["artifact"],
            "--run-id",
            self.run.run_id,
            "--section",
            "diagnoses",
            "--format",
            "json",
        )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output)["sections"]["diagnoses"]["returned"], 1)
        for action in (("show",), ("list", "commands")):
            status, _, error = self.dispatch(*action, "--run-id", "reproduce-unknown")
            self.assertEqual(status, 2)
            self.assertIn("reproduction.run.unknown", error)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)

    def test_public_failed_list_and_detail_pinpoint_actual_producer_failure(self):
        status, output, error = self.dispatch(
            "list", "commands", "--status", "failed", "--format", "json"
        )
        self.assertEqual((status, error), (0, ""))
        listing = json.loads(output)
        self.assertEqual(
            (listing["matched"], listing["returned"], listing["remaining"]), (1, 1, 0)
        )
        row = listing["items"][0]
        self.assertEqual(row["explanation"], "Producer exited with status 2.")
        self.assertIn("--run-id reproduce-mixed", row["detail_command"])
        identity = row["identity"]
        status, output, error = self.dispatch(
            "detail",
            "command",
            "--entry",
            identity["entry"],
            "--cid",
            identity["cid"],
            "--execution-id",
            identity["execution_id"],
            "--format",
            "json",
        )
        self.assertEqual((status, error), (0, ""))
        detail = json.loads(output)
        self.assertEqual(detail["diagnoses"][0]["observed"], {"returncode": 2})
        self.assertEqual(detail["result"]["argv"], ["python", "scripts/producer.py"])
        self.assertEqual(
            detail["retained_source"],
            {
                "script": FINGERPRINT.as_dict(),
                "effective_code": {
                    "algorithm": "python-effective-code-sha256-v1",
                    "digest": "b" * 64,
                },
            },
        )
        self.assertEqual(detail["accepted_source"], detail["retained_source"])
        self.assertFalse(detail["stdout"]["available"])
        self.assertNotIn("cases", detail)
        status, text, error = self.dispatch(
            "detail",
            "command",
            "--entry",
            identity["entry"],
            "--cid",
            identity["cid"],
            "--execution-id",
            identity["execution_id"],
        )
        self.assertEqual((status, error), (0, ""))
        self.assertIn("Retained source:", text)
        self.assertIn("Accepted source:", text)

    def test_blocked_consumer_retains_producer_cause_without_fabricating_attempt(self):
        consumer = next(
            work for work in self.run.commands if work.identity.cid == "consumer"
        )
        detail = command_detail(self.inspection, consumer.identity)
        self.assertEqual(detail["status"], "blocked")
        self.assertEqual(detail["result"]["argv"], [])
        self.assertEqual(detail["diagnoses"][0]["observed"], {"returncode": 2})
        self.assertEqual(detail["diagnoses"][0]["subject"]["cid"], "producer")
        self.assertEqual(consumer.problem_ids, ())

    def test_artifact_detail_retains_exact_line_values_without_reading_outputs(self):
        with mock.patch("pathlib.Path.open", side_effect=AssertionError("output read")):
            detail = artifact_detail(
                self.inspection, ArtifactRef("e001", "data/unequal.txt")
            )
        self.assertEqual(detail["status"], "not-matched")
        self.assertEqual(
            detail["diagnoses"][0]["observed"],
            {"line": 3, "expected": "2", "actual": "3"},
        )
        self.assertEqual(detail["result"]["profile"], "text")
        self.assertEqual(detail["producer"]["status"], "succeeded")

    def test_detail_pages_cover_large_output_collections_and_reject_stale_cursors(self):
        outputs = tuple((f"data/output-{n:03d}.txt", "file") for n in range(103))
        run_id = "reproduce-large-output"
        work = replace(
            command("large", outputs=outputs), project_root=str(self.root.parent)
        )
        artifacts = tuple(
            ArtifactWork(
                ArtifactRef("e001", path), work.identity, path, FINGERPRINT, output=path
            )
            for path, _ in outputs
        )
        results = tuple(
            ArtifactResult(
                item.identity,
                ArtifactOutcome.MATCHED,
                WHEN,
                run_id,
                f"tmp/run/{item.identity.artifact}",
                "text",
                FINGERPRINT,
                FINGERPRINT,
            )
            for item in artifacts
        )
        run = replace(
            self.run,
            run_id=run_id,
            commands=(work,),
            command_results=(attempted(work),),
            artifacts=artifacts,
            artifact_results=results,
            problems=(),
        )
        inspection = replace(self.inspection, run=run)
        first = command_detail(inspection, work.identity, format="json")
        cursor = first["sections"]["artifacts"]["cursor"]
        second = command_detail(
            inspection, work.identity, section="artifacts", cursor=cursor, format="json"
        )
        third = command_detail(
            inspection,
            work.identity,
            section="artifacts",
            cursor=second["sections"]["artifacts"]["cursor"],
            format="json",
        )
        self.assertEqual(
            [len(page["artifacts"]) for page in (first, second, third)], [50, 50, 3]
        )
        self.assertEqual(
            len(
                {
                    row["identity"]["artifact"]
                    for page in (first, second, third)
                    for row in page["artifacts"]
                }
            ),
            103,
        )
        self.assertEqual(len(first["recipe"]["outputs"]), 50)
        self.assertEqual(first["sections"]["recipe.outputs"]["matched"], 103)
        self.assertIsNone(third["sections"]["artifacts"]["cursor"])
        self.assertIn(
            "--section artifacts", first["sections"]["artifacts"]["next_command"]
        )
        for changed, section, format in (
            (replace(inspection, generation=8), "artifacts", "json"),
            (inspection, "recipe.outputs", "json"),
            (inspection, "artifacts", "text"),
        ):
            with self.assertRaises(ActionError) as caught:
                command_detail(
                    changed,
                    work.identity,
                    section=section,
                    cursor=cursor,
                    format=format,
                )
            self.assertEqual(caught.exception.code, "reproduction.cursor.stale")
        self.assertEqual(len(run.artifacts), 103)
        with shared.result_transaction(self.root) as db:
            write_saved_run(db, run)
        before = shared.result_store_path(self.root).read_bytes()
        for section in ("artifacts", "recipe.outputs"):
            pages = []
            next_cursor = None
            for _ in range(3):
                arguments = [
                    "detail",
                    "command",
                    "--entry",
                    "e001",
                    "--cid",
                    "large",
                    "--execution-id",
                    work.identity.execution_id,
                    "--run-id",
                    run_id,
                    "--section",
                    section,
                    "--format",
                    "json",
                ]
                if next_cursor:
                    arguments.extend(("--cursor", next_cursor))
                status, output, error = self.dispatch(*arguments)
                self.assertEqual((status, error), (0, ""))
                page = json.loads(output)
                pages.append(page)
                next_cursor = page["sections"][section]["cursor"]
            self.assertEqual(
                [page["sections"][section]["returned"] for page in pages], [50, 50, 3]
            )
            self.assertIsNone(next_cursor)
        arguments = [
            "list",
            "artifacts",
            "--entry",
            "e001",
            "--cid",
            "large",
            "--status",
            "matched",
            "--run-id",
            run_id,
            "--format",
            "json",
        ]
        rows, sizes, next_cursor = [], [], None
        for _ in range(3):
            status, output, error = self.dispatch(
                *arguments,
                *(("--cursor", next_cursor) if next_cursor else ()),
            )
            self.assertEqual((status, error), (0, ""))
            page = json.loads(output)
            rows.extend(page["items"])
            sizes.append(page["returned"])
            next_cursor = page["cursor"]
        self.assertEqual(sizes, [50, 50, 3])
        self.assertEqual(
            {row["identity"]["artifact"] for row in rows}, {path for path, _ in outputs}
        )
        self.assertIsNone(next_cursor)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)

    def test_pages_cover_all_items_and_bind_every_filter_format_and_generation(self):
        works = tuple(
            replace(
                command(f"item-{index:03}", WorkSelection.NOT_NEEDED),
                project_root=str(self.root.parent),
            )
            for index in range(103)
        )
        run = replace(
            self.run,
            run_id="reproduce-large-commands",
            commands=works,
            command_results=(),
            artifacts=(),
            artifact_results=(),
            problems=(),
        )
        inspection = replace(self.inspection, run=run)
        selection = ListSelection("commands", format="json")
        first = list_saved(inspection, selection)
        second = list_saved(inspection, selection, first["cursor"])
        third = list_saved(inspection, selection, second["cursor"])
        self.assertEqual(
            [page["returned"] for page in (first, second, third)], [50, 50, 3]
        )
        self.assertEqual(third["remaining"], 0)
        self.assertIsNone(third["cursor"])
        rows = [row for page in (first, second, third) for row in page["items"]]
        self.assertEqual(len({row["identity"]["cid"] for row in rows}), 103)
        for changed, filters in (
            (replace(inspection, generation=8), selection),
            (inspection, replace(selection, format="text")),
            (inspection, replace(selection, reason="not-needed")),
        ):
            with self.assertRaises(ActionError) as caught:
                list_saved(changed, filters, first["cursor"])
            self.assertEqual(caught.exception.code, "reproduction.cursor.stale")
        with shared.result_transaction(self.root) as db:
            write_saved_run(db, run)
        before = shared.result_store_path(self.root).read_bytes()
        arguments = [
            "list",
            "commands",
            "--entry",
            "e001",
            "--status",
            "not-run",
            "--reason",
            "not-needed",
            "--run-id",
            run.run_id,
            "--format",
            "json",
        ]
        rows, sizes, next_cursor = [], [], None
        for _ in range(3):
            status, output, error = self.dispatch(
                *arguments,
                *(("--cursor", next_cursor) if next_cursor else ()),
            )
            self.assertEqual((status, error), (0, ""))
            page = json.loads(output)
            rows.extend(page["items"])
            sizes.append(page["returned"])
            next_cursor = page["cursor"]
        self.assertEqual(sizes, [50, 50, 3])
        self.assertEqual(
            {row["identity"]["cid"] for row in rows},
            {work.identity.cid for work in works},
        )
        self.assertIsNone(next_cursor)
        self.assertEqual(shared.result_store_path(self.root).read_bytes(), before)

    def test_invalid_selectors_and_cursors_are_actionable(self):
        for selection in (
            {"kind": "cases"},
            {"kind": "commands", "status": "unknown"},
            {"kind": "artifacts", "reason": "execution_failed"},
            {"kind": "commands", "cid": "producer"},
        ):
            with self.assertRaises(ActionError) as caught:
                ListSelection(**selection)
            self.assertEqual(caught.exception.code, "reproduction.selector.invalid")
        for cursor in ("!", "x" * 2049, "bnVsbA", "W10"):
            with self.assertRaises(ActionError) as caught:
                list_saved(self.inspection, ListSelection("commands"), cursor)
            self.assertEqual(caught.exception.code, "reproduction.cursor.invalid")

    def test_conflicting_status_reason_filters_are_rejected(self):
        for selection in (
            ListSelection("commands", status="failed", reason="execution_failed"),
            ListSelection("artifacts", status="not-compared", reason="command-failed"),
        ):
            self.assertEqual(list_saved(self.inspection, selection)["matched"], 1)
        for fields in (
            {"kind": "commands", "status": "succeeded", "reason": "execution_failed"},
            {"kind": "artifacts", "status": "matched", "reason": "command-blocked"},
        ):
            with self.assertRaises(ActionError) as caught:
                ListSelection(**fields)
            self.assertEqual(caught.exception.code, "reproduction.selector.invalid")

    def test_artifact_not_compared_surfaces_its_producer_cause(self):
        identity = next(
            item.identity
            for item in self.run.artifacts
            if item.producer is not None and item.producer.cid == "producer"
        )
        detail = artifact_detail(self.inspection, identity)
        self.assertEqual(detail["status"], "not-compared")
        self.assertEqual(detail["reason"], "command-failed")
        self.assertEqual(detail["explanation"], "Producer exited with status 2.")
        self.assertEqual(detail["diagnoses"][0]["observed"], {"returncode": 2})

    def test_public_text_detail_leads_with_failure_and_keeps_observed_return_code(self):
        producer = next(
            work for work in self.run.commands if work.identity.cid == "producer"
        )
        status, output, error = self.dispatch(
            "detail",
            "command",
            "--entry",
            "e001",
            "--cid",
            "producer",
            "--execution-id",
            producer.identity.execution_id,
        )
        self.assertEqual((status, error), (0, ""))
        self.assertTrue(output.startswith("Failed — Producer exited with status 2.\n"))
        self.assertIn("Execute / execution_failed", output)
        self.assertIn('"returncode": 2', output)
        self.assertIn("scripts/producer.py", output)
        self.assertIn("Unavailable", output)

    def test_root_tables_match_independent_golden_for_mixed_and_empty_targets(self):
        root = self.root.parent
        empty_root = root / "empty"
        (empty_root / "entries").mkdir(parents=True)
        empty_summary = empty_root.with_suffix(".md")
        empty_summary.write_text("# Empty\n")
        empty_run = replace(
            self.run,
            summary=str(empty_summary),
            run_id="reproduce-empty",
            commands=(),
            command_results=(),
            artifacts=(),
            artifact_results=(),
            problems=(),
        )
        path = shared.result_store_path(empty_root)
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript(
                (
                    Path(__file__).parent
                    / "fixtures/result-store-execution-baseline-v19.sql"
                ).read_text()
            )
            db.execute("BEGIN")
            replace_reproduction_domain(db)
            write_saved_run(db, empty_run)
        expected = """\
| Log | Saved | Target | Total | Not run | Policy | Succeeded | Failed | Blocked |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| empty | Sep 15 | Log | 0 | 0 | 0 | 0 | 0 | 0 |
| study | Sep 15 | Log | 7 | 3 | 1 | 1 | 1 | 1 |
| Total | | | 7 | 3 | 1 | 1 | 1 | 1 |

| Log | Total | Matched | Not matched | Not compared |
| --- | ---: | ---: | ---: | ---: |
| empty | 0 | 0 | 0 | 0 |
| study | 4 | 1 | 1 | 2 |
| Total | 4 | 1 | 1 | 2 |
"""
        self.assertEqual(render_root_summary(root_summary(root)), expected)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = dispatcher.main(["reproduce", "show", "--root", str(root)])
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), expected)

    def test_root_unavailable_and_entry_coverage_are_explicit_not_zeroes(self):
        missing = self.root.parent / "missing"
        (missing / "entries").mkdir(parents=True)
        missing.with_suffix(".md").write_text("# Missing\n")
        entry_run = replace(
            self.run,
            run_id="reproduce-entry",
            target=RunTarget("entry", "e001"),
            finished_at="2026-09-15T13:00:00Z",
        )
        with sqlite3.connect(shared.result_store_path(self.root)) as db:
            write_saved_run(db, entry_run)
        value = root_summary(self.root.parent)
        self.assertEqual(
            value["coverage"],
            {"discovered": 2, "available": 1, "unavailable": 1, "entry_targets": 1},
        )
        output = render_root_summary(value)
        self.assertIn("| study | Sep 15 | e001 | 7 |", output)
        self.assertIn("| missing | — | — | — | — | — | — | — | — |", output)
        self.assertIn("Totals cover only available recorded targets", output)
        self.assertIn("reproduction.results.missing", output)
        self.assertFalse(shared.result_store_path(missing).exists())
        with self.assertRaises(ActionError) as caught:
            list_saved(
                replace(self.inspection, run=entry_run),
                ListSelection("commands", entry="e002"),
            )
        self.assertEqual(caught.exception.code, "reproduction.selector.invalid")

    def test_public_missing_show_has_no_invented_run_or_counts(self):
        missing = self.root.parent / "missing"
        (missing / "entries").mkdir(parents=True)
        missing.with_suffix(".md").write_text("# Missing\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = dispatcher.main(["reproduce", "show", "--path", str(missing)])
        self.assertEqual(status, 3)
        self.assertEqual(
            output.getvalue(),
            "Log: missing\nSaved results: Unavailable\n"
            "Reproduce this log to create saved results.\n",
        )
        self.assertFalse(shared.result_store_path(missing).exists())

    def test_render_is_exact_show_body_and_only_changes_report_materialization(self):
        with (
            mock.patch(
                "log_commands.reproduction_jobs.launch_reproduction",
                side_effect=AssertionError("execution"),
            ),
            mock.patch(
                "log_commands.reproduction_planner.plan_reproduction_work",
                side_effect=AssertionError("replanning"),
            ),
        ):
            status, output, error = self.dispatch("render")
        expected = "# Reproduction\n\n" + MIXED_SUMMARY
        self.assertEqual((status, output, error), (0, expected, ""))
        self.assertEqual((self.root / "reproduction.md").read_text(), expected)
        self.assertEqual(load_inspection(self.log).run, self.run)
        self.assertEqual(shared.result_generation(self.root, "reproduction"), 7)
        with shared.result_snapshot(self.root) as db:
            marker = db.execute(
                "SELECT source_generation FROM report_materializations "
                "WHERE kind='reproduction'"
            ).fetchone()
            self.assertEqual(marker[0], 7)

    def test_report_marker_failure_leaves_results_queryable_and_retry_never_runs(self):
        with mock.patch(
            "log_commands.reproduction_saved_report.record_report_materialization",
            return_value=False,
        ):
            status, output, error = self.dispatch("render")
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertIn("results.report.write_failed", error)
        self.assertEqual(load_inspection(self.log).run, self.run)
        with mock.patch(
            "log_commands.reproduction_jobs.launch_reproduction",
            side_effect=AssertionError("execution"),
        ):
            self.assertEqual(
                self.dispatch("render"), (0, "# Reproduction\n\n" + MIXED_SUMMARY, "")
            )
        self.assertEqual(shared.result_generation(self.root, "reproduction"), 7)

    def test_explicit_unknown_run_and_mismatched_log_are_nonmutating_errors(self):
        with self.assertRaises(ActionError) as caught:
            load_inspection(self.log, "reproduce-unknown")
        self.assertEqual(caught.exception.code, "reproduction.run.unknown")
        with self.assertRaises(ActionError) as caught:
            load_inspection(
                replace(self.log, summary=self.summary.with_name("other.md"))
            )
        self.assertEqual(caught.exception.code, "reproduction.results.invalid")

    def test_latest_default_uses_acceptance_time_before_lexical_run_id_on_ties(self):
        earlier = replace(
            self.run,
            run_id="reproduce-z-earlier",
            accepted_at="2026-09-15T10:00:00Z",
            finished_at="2026-09-15T13:00:00Z",
        )
        later = replace(
            earlier, run_id="reproduce-a-later", accepted_at="2026-09-15T11:00:00Z"
        )
        with sqlite3.connect(shared.result_store_path(self.root)) as db:
            write_saved_run(db, earlier)
            write_saved_run(db, later)
        self.assertEqual(load_inspection(self.log).run, later)
        self.assertEqual(load_inspection(self.log, earlier.run_id).run, earlier)

    def test_retained_stream_tails_are_bounded_and_symlinks_rejected(self):
        producer = next(
            work for work in self.run.commands if work.identity.cid == "producer"
        )
        path = (
            self.root.parent.joinpath(*saved_run_path(self.run).parts)
            / "tmp/run/stdout.log"
        )
        path.parent.mkdir(parents=True)
        path.write_bytes(b"a" * 20000 + b"\x00end")
        detail = command_detail(self.inspection, producer.identity)
        self.assertTrue(detail["stdout"]["available"])
        self.assertTrue(detail["stdout"]["truncated"])
        self.assertEqual(len(detail["stdout"]["excerpt"]), 16384)
        self.assertTrue(detail["stdout"]["excerpt"].endswith("�end"))
        path.unlink()
        path.symlink_to(self.summary)
        self.assertEqual(
            command_detail(self.inspection, producer.identity)["stdout"]["reason"],
            "path-invalid",
        )

    def test_stream_lookup_accepts_project_tmp_symlink_and_confines_absolute_paths(
        self,
    ):
        producer = next(
            work for work in self.run.commands if work.identity.cid == "producer"
        )
        scratch = self.root.parent / "scratch"
        scratch.mkdir()
        (self.root.parent / "tmp").symlink_to(scratch, target_is_directory=True)
        logical = self.root.parent.joinpath(*saved_run_path(self.run).parts)
        physical = scratch.joinpath(*saved_run_path(self.run).parts[1:])
        stream = physical / "tmp/run/stdout.log"
        stream.parent.mkdir(parents=True)
        stream.write_text("original diagnostic")
        self.assertEqual(
            command_detail(self.inspection, producer.identity)["stdout"]["excerpt"],
            "original diagnostic",
        )
        original = self.run.command_result(producer.identity)
        for recorded in (stream, logical / "tmp/run/stdout.log", self.summary):
            run = replace(
                self.run,
                command_results=tuple(
                    replace(result, stdout_path=str(recorded))
                    if result is original
                    else result
                    for result in self.run.command_results
                ),
            )
            tail = command_detail(replace(self.inspection, run=run), producer.identity)[
                "stdout"
            ]
            self.assertEqual(tail["available"], recorded != self.summary)
            self.assertEqual(tail["path"], str(recorded))

    def test_retained_stream_special_files_are_unavailable_without_opening(self):
        producer = next(
            work for work in self.run.commands if work.identity.cid == "producer"
        )
        path = (
            self.root.parent.joinpath(*saved_run_path(self.run).parts)
            / "tmp/run/stdout.log"
        )
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
        with mock.patch(
            "pathlib.Path.open", side_effect=AssertionError("special-file read")
        ):
            tail = command_detail(self.inspection, producer.identity)["stdout"]
        self.assertFalse(tail["available"])
        self.assertEqual(tail["reason"], "unavailable")
