"""Public recovery, selective reads, and disposable cache lifecycle tests."""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from log_commands.inspection_queries import Query, inspect_result
from log_commands.inspection_views import render_view
from research_log_cli_test_support import LOG, run_log, run_log_process
from research_log_validation_test_support import mechanical_log
from validation.inspection import save_result
from validation.inspection_store import (
    STORE_NAME,
    ContentWriter,
    InspectionError,
    connection,
)


def sample(root: Path, *, members: int = 2, chain: str = "A") -> tuple:
    summary, _ = mechanical_log(root)
    finding = {
        "identity": "missing-output",
        "code": "provenance.output.missing",
        "subject": "missing.csv",
        "observed": {"path": "missing.csv"},
        "rule": "declared output must exist",
        "status": "fail",
    }
    command = {
        "identity": "cmd-" + chain,
        "document": "entries/e001.md",
        "entry": "e001",
        "fence": 1,
        "ordinal": 1,
        "tokens": ["python", "run.py"],
        "outputs": [],
        "collections": [
            {
                "direction": "output",
                "mechanism": "directory",
                "members": [f"data/item-{i:06d}.csv" for i in range(members)],
            }
        ],
    }
    projection = {
        "schema": "research-log-batch-projection/1",
        "projection_id": "new-projection",
        "source_identity": "source",
        "unresolved": [],
        "chains": [
            {
                "chain_id": chain,
                "entry": "e001",
                "commands": [command],
                "artifacts": ["missing.csv"],
                "findings": [finding],
            }
        ],
    }
    record = {
        "schema": "research-log-mechanical/1",
        "checks": [],
        "result_date": "2026-09-08",
        "rules_version": "fixture",
    }
    outcome = {"status": "complete_findings", "published": False, "findings": [finding]}
    request = {
        "kind": "batch",
        "projection": "published",
        "entry": "e001",
        "chain": chain,
        "started_at": "2026-09-08T12:00:00Z",
    }
    return summary, outcome, record, projection, request


class InspectionTests(unittest.TestCase):
    def test_lost_stdout_recovers_from_public_commands_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log = summary.with_suffix("")
            completed = subprocess.run(
                [sys.executable, str(LOG), "validate", "--path", str(log)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            listing = run_log_process(
                root, "results", "list", "--path", str(log), "--format", "json"
            )
            self.assertEqual(listing.returncode, 0, listing.stderr)
            result_id = json.loads(listing.stdout)["items"][0]["result_id"]
            # Inspection works even when source documents can no longer be decoded.
            summary.write_bytes(b"\xff")
            shown = run_log_process(
                root, "results", "show", "--path", str(log), "--id", result_id
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn(result_id, shown.stdout)
            self.assertIn("complete_clear", shown.stdout)

    def test_default_text_and_explicit_json_batch_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            path = str(summary.with_suffix(""))
            full = run_log(root, "validate", "--path", path)
            self.assertIn("Result:", full.stdout)
            self.assertFalse(full.stdout.startswith("{"))
            chains = run_log(
                root, "findings", "list", "--path", path, "--format", "json"
            )
            payload = json.loads(chains.stdout)
            # Cached views retain clear chains omitted by findings list.
            full_id = inspect_result(summary.with_suffix(""), Query(action="list"))[
                "items"
            ][0]["result_id"]
            saved = inspect_result(
                summary.with_suffix(""), Query(result_id=full_id, view="chains")
            )
            selected = saved["items"][0]
            checked = run_log(
                root,
                "validate-batch",
                "--path",
                path,
                "--projection",
                payload["projection_id"],
                "--entry",
                selected["entry"],
                "--chain",
                selected["chain_id"],
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("Result:", checked.stdout)
            self.assertNotIn("result not cached", checked.stderr)
            cached = inspect_result(
                summary.with_suffix(""), Query(action="list", kind="batch")
            )
            self.assertEqual(cached["total"], 1)
            self.assertIn(cached["items"][0]["result_id"], checked.stdout)
            explicit = run_log(root, "validate", "--path", path, "--format", "json")
            self.assertEqual(
                json.loads(explicit.stdout)["schema"],
                "research-log-validation-cli-result/1",
            )
            self.assertEqual(
                inspect_result(summary.with_suffix(""), Query(action="list"))["total"],
                1,
            )

    def test_scope_replacement_rollback_and_full_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            log = args[0].with_suffix("")
            a = save_result(*args)
            b_args = (*args[:4], {**args[4], "chain": "B"})
            b = save_result(*b_args)
            new_a = save_result(*args)
            self.assertEqual(inspect_result(log, Query(result_id=b))["result_id"], b)
            with self.assertRaisesRegex(InspectionError, "superseded or cleared"):
                inspect_result(log, Query(result_id=a))
            with mock.patch.object(
                ContentWriter, "entity", side_effect=ValueError("interrupted")
            ):
                with self.assertRaisesRegex(ValueError, "interrupted"):
                    save_result(*args)
            self.assertEqual(
                inspect_result(log, Query(result_id=new_a))["result_id"], new_a
            )
            full = save_result(
                args[0],
                {**args[1], "published": True},
                args[2],
                args[3],
                {"kind": "full", "started_at": args[4]["started_at"]},
            )
            listed = inspect_result(log, Query(action="list"))
            self.assertEqual([item["result_id"] for item in listed["items"]], [full])

    def test_large_collection_has_bounded_selective_views_and_exact_export(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory), members=100_000)
            log = args[0].with_suffix("")
            start = time.perf_counter()
            identity = save_result(*args)
            ingestion = time.perf_counter() - start
            from log_commands import inspection_queries

            original_decode = inspection_queries.decode_node
            with mock.patch.object(
                inspection_queries, "decode_node", wraps=original_decode
            ) as decoded:
                summary = inspect_result(log, Query(result_id=identity))
                finding = inspect_result(
                    log,
                    Query(
                        action="finding", result_id=identity, entity="missing-output"
                    ),
                )
                command = inspect_result(
                    log, Query(action="command", result_id=identity, entity="cmd-A")
                )
                self.assertLessEqual(decoded.call_count, 5)
            self.assertIn("provenance.output.missing", render_view(finding))
            self.assertNotIn("item-000000", render_view(command))
            self.assertLess(len(render_view(command).encode()), 16384)
            members_ref = command["items"][0]["collections"][0]["members"]["ref"]
            page = inspect_result(
                log, Query(action="collection", result_id=identity, entity=members_ref)
            )
            self.assertEqual((page["total"], page["returned"]), (100_000, 20))
            self.assertIsNotNone(page["next_cursor"])
            following = inspect_result(
                log,
                Query(
                    action="collection",
                    result_id=identity,
                    entity=members_ref,
                    cursor=page["next_cursor"],
                ),
            )
            self.assertEqual(following["items"][0], "data/item-000020.csv")
            exported = inspect_result(log, Query(action="export", result_id=identity))
            self.assertEqual(
                exported["entities"]["commands"]["cmd-A"],
                args[3]["chains"][0]["commands"][0],
            )
            self.assertEqual(summary["metadata"]["finding_count"], 1)
            print(
                f"inspection diagnostic: 100000 members, ingest {ingestion:.3f}s, "
                f"command view {len(render_view(command).encode())} bytes"
            )

    def test_collection_page_database_work_is_bounded_at_large_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory), members=100_000)
            log = args[0].with_suffix("")
            identity = save_result(*args)
            command = inspect_result(
                log, Query(action="command", result_id=identity, entity="cmd-A")
            )
            ref = command["items"][0]["collections"][0]["members"]["ref"]
            steps = [0]

            def count():
                steps[0] += 100
                return 0

            @contextmanager
            def measured(*args, **kwargs):
                with connection(*args, **kwargs) as db:
                    db.set_progress_handler(count, 100)
                    yield db

            query = dict(action="collection", result_id=identity, entity=ref)
            with mock.patch("log_commands.inspection_queries.connection", measured):
                first = inspect_result(log, Query(**query))
                self.assertLess(steps[0], 5000)
                # Advance the opaque cursor to exercise a deep indexed seek without
                # spending this regression test walking 4,500 intervening pages.
                cursor = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
                cursor.update(offset=90_000, after=89_999)
                token = base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
                steps[0] = 0
                page = inspect_result(log, Query(**query, cursor=token))
                self.assertLess(steps[0], 5000)
            self.assertEqual(page["items"][0], "data/item-090000.csv")
            self.assertEqual((page["total"], page["returned"]), (100_000, 20))

    def test_entity_and_listing_pages_preserve_order_and_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory), members=0)
            log = args[0].with_suffix("")
            paths = [f"artifact-{i:03d}" for i in range(41)]
            args[3]["chains"][0]["artifacts"] = paths
            identities = [
                save_result(*args[:4], {**args[4], "chain": name})
                for name in ("A", "B", "C")
            ]
            for query, expected, field in (
                (dict(action="list", limit=1), identities[::-1], "result_id"),
                (
                    dict(result_id=identities[-1], view="artifacts", limit=20),
                    paths,
                    "path",
                ),
                (
                    dict(
                        result_id=identities[-1],
                        view="artifacts",
                        entry="e001",
                        limit=20,
                    ),
                    paths,
                    "path",
                ),
            ):
                with self.subTest(query=query):
                    found = []
                    cursor = None
                    while True:
                        page = inspect_result(log, Query(**query, cursor=cursor))
                        self.assertEqual(page["total"], len(expected))
                        found.extend(item[field] for item in page["items"])
                        cursor = page["next_cursor"]
                        if cursor is None:
                            break
                    self.assertEqual(found, expected)
            exported = inspect_result(
                log, Query(action="export", result_id=identities[-1])
            )
            self.assertEqual(
                exported["entities"]["commands"]["cmd-A"]["collections"][0]["members"],
                [],
            )

    def test_latest_and_id_apply_the_same_content_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = sample(root)
            log = args[0].with_suffix("")
            for kind in ("full", "batch"):
                with self.subTest(kind=kind):
                    # A batch's requested chain can differ from its reconciled chain.
                    request = {"kind": kind, "started_at": args[4]["started_at"]}
                    if kind == "batch":
                        request.update(
                            entry="e001", chain="old-A", projection="published"
                        )
                    identity = save_result(*args[:4], request)
                    arguments = (
                        "results",
                        "show",
                        "--path",
                        str(log),
                        "--kind",
                        kind,
                        "--view",
                        "chains",
                        "--entry",
                        "e001",
                        "--chain",
                        "A",
                        "--format",
                        "json",
                    )
                    explicit = run_log(root, *arguments, "--id", identity)
                    latest = run_log(root, *arguments, "--latest")
                    self.assertEqual(latest.returncode, 0, latest.stderr)
                    self.assertEqual(
                        json.loads(explicit.stdout), json.loads(latest.stdout)
                    )
                    self.assertEqual(json.loads(latest.stdout)["returned"], 1)

    def test_invalid_content_nodes_return_public_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = sample(root)
            log = args[0].with_suffix("")
            identity = save_result(*args)
            for node in (
                {"type": "object", "fields": []},
                {"type": "array"},
                {"type": "collection", "ref": 42, "count": 1},
            ):
                with self.subTest(node=node):
                    with sqlite3.connect(log / ".cache" / STORE_NAME) as db:
                        db.execute(
                            "UPDATE entities SET payload=? WHERE kind='commands'",
                            (json.dumps(node),),
                        )
                    for action in (("command", "--command", "cmd-A"), ("export",)):
                        result = run_log(
                            root,
                            "results",
                            action[0],
                            "--path",
                            str(log),
                            "--id",
                            identity,
                            *action[1:],
                        )
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn("results.store.malformed", result.stderr)
                        self.assertEqual(result.stdout, "")

    def test_malformed_listing_metadata_returns_a_public_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = sample(root)
            log = args[0].with_suffix("")
            identity = save_result(*args)
            with sqlite3.connect(log / ".cache" / STORE_NAME) as db:
                db.execute(
                    "UPDATE results SET metadata=?",
                    (json.dumps({"schema": "research-log-retained-result/1"}),),
                )
            for action in (("list",), ("show", "--id", identity)):
                result = run_log(root, "results", *action, "--path", str(log))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("results.store.malformed", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_missing_collection_pieces_fail_queries_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = sample(root, members=100)
            log = args[0].with_suffix("")
            for position in (0, 50, 99):
                with self.subTest(position=position):
                    identity = save_result(*args)
                    command = inspect_result(
                        log, Query(action="command", result_id=identity, entity="cmd-A")
                    )
                    ref = command["items"][0]["collections"][0]["members"]["ref"]
                    with sqlite3.connect(log / ".cache" / STORE_NAME) as db:
                        db.execute(
                            "DELETE FROM pieces WHERE result=? AND ref=? "
                            "AND position=?",
                            (identity, ref, position),
                        )
                    for action in (
                        ("collection", "--collection", ref, "--limit", "100"),
                        ("export",),
                    ):
                        result = run_log(
                            root,
                            "results",
                            action[0],
                            "--path",
                            str(log),
                            "--id",
                            identity,
                            *action[1:],
                        )
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn("results.store.malformed", result.stderr)
                        self.assertEqual(result.stdout, "")

    def test_cursor_binds_selection_and_listing_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            log = args[0].with_suffix("")
            identity = save_result(*args)
            save_result(*args[:4], {**args[4], "chain": "B"})
            page = inspect_result(log, Query(action="list", limit=1))
            save_result(*args)
            with self.assertRaisesRegex(InspectionError, "restart"):
                inspect_result(
                    log, Query(action="list", limit=1, cursor=page["next_cursor"])
                )
            with self.assertRaises(InspectionError):
                inspect_result(log, Query(result_id=identity))

    def test_dry_run_and_cache_failure_preserve_validation_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            path = summary.with_suffix("")
            dry = run_log(root, "validate", "--path", str(path), "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse((path / ".cache" / STORE_NAME).exists())
            with mock.patch(
                "validation.inspection.save_result", side_effect=OSError("disk full")
            ):
                completed = run_log(root, "validate", "--path", str(path))
            self.assertEqual(completed.returncode, 0)
            self.assertIn("results.store.write_failed", completed.stderr)
            self.assertIn("Result not cached", completed.stdout)
            self.assertTrue((path / "validation/results.json").exists())

    def test_missing_unsupported_malformed_and_busy_store_fail_precisely(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            log = args[0].with_suffix("")
            with self.assertRaises(InspectionError) as missing:
                inspect_result(log, Query(action="list"))
            self.assertEqual(missing.exception.code, "results.store.missing")
            identity = save_result(*args)
            with connection(log, writable=True):
                with self.assertRaises(InspectionError) as busy:
                    save_result(*args)
                self.assertEqual(busy.exception.code, "results.store.busy")
            database = log / ".cache" / STORE_NAME
            with sqlite3.connect(database) as db:
                db.execute("UPDATE results SET metadata='{' WHERE id=?", (identity,))
            with self.assertRaises(InspectionError) as malformed:
                inspect_result(log, Query(result_id=identity))
            self.assertEqual(malformed.exception.code, "results.store.malformed")
            with sqlite3.connect(database) as db:
                db.execute("PRAGMA user_version=99")
            with self.assertRaises(InspectionError) as unsupported:
                inspect_result(log, Query(action="list"))
            self.assertEqual(unsupported.exception.code, "results.schema.unsupported")

    def test_shared_artifact_keeps_both_chain_associations(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            projection = args[3]
            shared = {
                **projection["chains"][0],
                "chain_id": "B",
                "commands": [],
                "findings": [],
            }
            projection["chains"].append(shared)
            identity = save_result(*args)
            log = args[0].with_suffix("")
            for chain in ("A", "B"):
                result = inspect_result(
                    log, Query(result_id=identity, view="artifacts", chain=chain)
                )
                self.assertEqual(result["total"], 1)

    def test_incomplete_full_does_not_clear_batches_and_inspection_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            log = args[0].with_suffix("")
            batch = save_result(*args)
            incomplete = save_result(
                args[0],
                {**args[1], "status": "incomplete"},
                args[2],
                args[3],
                {"kind": "full", "started_at": args[4]["started_at"]},
            )
            database = log / ".cache" / STORE_NAME
            before = (database.read_bytes(), database.stat().st_mtime_ns)
            with mock.patch(
                "validation.controller.evaluate_entry_record",
                side_effect=AssertionError("must not evaluate"),
            ):
                result = inspect_result(log, Query(result_id=incomplete))
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(
                    inspect_result(log, Query(result_id=batch))["result_id"], batch
                )
            self.assertEqual(
                before, (database.read_bytes(), database.stat().st_mtime_ns)
            )

    def test_long_text_is_retrievable_and_symlinked_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            args = sample(Path(directory))
            command = args[3]["chains"][0]["commands"][0]
            command["script_context"] = "é" * 20_000
            identity = save_result(*args)
            log = args[0].with_suffix("")
            result = inspect_result(
                log, Query(action="command", result_id=identity, entity="cmd-A")
            )
            reference = result["items"][0]["script_context"]["ref"]
            value = inspect_result(
                log,
                Query(action="value", result_id=identity, entity=reference, limit=1),
            )
            self.assertLessEqual(len(value["items"][0].encode()), 4096)
            self.assertIsNotNone(value["next_cursor"])
            chunks = list(value["items"])
            while value["next_cursor"]:
                value = inspect_result(
                    log,
                    Query(
                        action="value",
                        result_id=identity,
                        entity=reference,
                        limit=3,
                        cursor=value["next_cursor"],
                    ),
                )
                chunks.extend(value["items"])
            self.assertEqual("".join(chunks), command["script_context"])
            exported = inspect_result(log, Query(action="export", result_id=identity))
            self.assertEqual(exported["entities"]["commands"]["cmd-A"], command)
            database = log / ".cache" / STORE_NAME
            saved = database.with_suffix(".saved")
            database.rename(saved)
            database.symlink_to(saved)
            with self.assertRaises(InspectionError) as error:
                inspect_result(log, Query(result_id=identity))
            self.assertEqual(error.exception.code, "results.store.malformed")
