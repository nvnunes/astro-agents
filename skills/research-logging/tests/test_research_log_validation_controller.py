from __future__ import annotations

import copy
import importlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import CLI, make_log, write

CONTROLLER = importlib.import_module("validation.controller")
EXCHANGE = importlib.import_module("validation.review_exchange")
TARGET = importlib.import_module("validation.target_records")
STORE = importlib.import_module("validation.sharded_state")


def run_validate(summary: Path, **kwargs):
    return CONTROLLER.validate(CONTROLLER.ValidationRequest(summary, **kwargs))


def make_no_semantic_log(root: Path) -> Path:
    (root / ".git").mkdir()
    summary = root / "docs" / "empty.md"
    (summary.with_suffix("") / "entries").mkdir(parents=True)
    write(
        summary,
        "# Empty Log\n\n"
        "## Summary\n\n"
        "No quantitative claims.\n\n"
        "## Entries\n",
    )
    return summary


class ValidationControllerTests(unittest.TestCase):
    def test_no_semantic_log_completes_and_publishes_only_target_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            before = summary.read_bytes()
            result = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(summary.read_bytes(), before)
            output = summary.with_suffix("")
            generated = {
                path.name
                for path in output.iterdir()
                if path.name != "entries" and not path.name.startswith(".")
            }
            self.assertEqual(
                generated,
                {
                    "validation.md",
                    TARGET.RECORD_FILENAME,
                    TARGET.CACHE_FILENAME,
                    "validation-state",
                },
            )
            stored = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            self.assertEqual(stored["failures"], [])

    def test_repeat_no_semantic_validation_needs_no_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            second = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")

    def test_storage_migration_shards_exact_state_without_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            completed = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            output = summary.with_suffix("")
            record_path = output / TARGET.RECORD_FILENAME
            logical = TARGET.load_record(record_path)
            record_path.write_bytes(TARGET._json_bytes(logical))
            monolithic = record_path.read_bytes()
            report = (output / "validation.md").read_bytes()

            dry_run = run_validate(
                summary, jobs=1, migrate_storage=True, publish=False
            )
            self.assertEqual(dry_run["status"], "migration_dry_run")
            self.assertEqual(record_path.read_bytes(), monolithic)

            with mock.patch.object(
                CONTROLLER,
                "scan_log",
                side_effect=AssertionError("storage migration must not scan"),
            ):
                migrated = run_validate(
                    summary, jobs=1, migrate_storage=True
                )
            self.assertEqual(migrated["status"], "migrated")
            self.assertEqual(TARGET.load_record(record_path), logical)
            self.assertEqual((output / "validation.md").read_bytes(), report)
            manifest = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["storage_layout"], "sharded-v1")

    def test_native_v1_storage_migration_preserves_exact_upgraded_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = make_no_semantic_log(root)
            completed = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            output = summary.with_suffix("")
            record_path = output / TARGET.RECORD_FILENAME
            current = TARGET.load_record(record_path)
            native_v1 = {
                key: copy.deepcopy(value)
                for key, value in current.items()
                if key not in {"completion_dependencies", "projection"}
            }
            native_v1["schema_version"] = 1
            native_v1["summary"] = summary.resolve().as_posix()
            record_path.write_bytes(TARGET._json_bytes(native_v1))
            expected = TARGET.upgrade_native_v1(
                native_v1,
                current["summary"],
                root,
            )

            with mock.patch.object(
                CONTROLLER,
                "scan_log",
                side_effect=AssertionError("storage migration must not scan"),
            ):
                migrated = run_validate(
                    summary, jobs=1, migrate_storage=True
                )
            self.assertEqual(migrated["status"], "migrated")
            self.assertEqual(TARGET.load_record(record_path), expected)

    def test_missing_cache_for_changed_zero_outcome_log_forces_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "complete")
            (summary.with_suffix("") / TARGET.CACHE_FILENAME).unlink()
            write(
                summary,
                summary.read_text(encoding="utf-8") + "\nUpdated context.\n",
            )

            with mock.patch.object(
                CONTROLLER, "scan_log", wraps=CONTROLLER.scan_log
            ) as scanned:
                second = run_validate(
                    summary, result_date="2026-08-16", jobs=1
                )

            self.assertEqual(second["status"], "complete")
            self.assertNotEqual(second.get("cached"), True)
            scanned.assert_called_once()

    def test_tampered_report_rejects_cached_completion_and_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "complete")
            report = summary.with_suffix("") / "validation.md"
            report.write_text("stale report\n", encoding="utf-8")

            with mock.patch.object(
                CONTROLLER, "scan_log", wraps=CONTROLLER.scan_log
            ) as scanned:
                repaired = run_validate(
                    summary, result_date="2026-08-15", jobs=1
                )

            self.assertEqual(repaired["status"], "complete")
            self.assertNotEqual(repaired.get("cached"), True)
            self.assertNotEqual(report.read_text(encoding="utf-8"), "stale report\n")
            scanned.assert_called_once()

    def test_failed_report_publication_is_repaired_on_next_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "complete")
            report = summary.with_suffix("") / "validation.md"
            prior_report = report.read_bytes()
            write(
                summary,
                summary.read_text(encoding="utf-8") + "\nUpdated context.\n",
            )
            original = TARGET._atomic_write_bytes

            def fail_report(path: Path, payload: bytes) -> None:
                if path.name == "validation.md":
                    raise OSError("simulated report failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_report
            ):
                failed = run_validate(
                    summary, result_date="2026-08-16", jobs=1
                )

            self.assertEqual(failed["status"], "error")
            self.assertEqual(report.read_bytes(), prior_report)
            repaired = run_validate(summary, result_date="2026-08-16", jobs=1)
            self.assertEqual(repaired["status"], "complete")
            self.assertNotEqual(repaired.get("cached"), True)
            stored = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(
                stored["projection"]["report_sha256"],
                TARGET.sha256_bytes(report.read_bytes()),
            )

    def test_record_from_another_summary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_summary = make_no_semantic_log(root)
            completed = run_validate(
                first_summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            second_summary = root / "docs" / "other.md"
            (second_summary.with_suffix("") / "entries").mkdir(parents=True)
            write(second_summary, "# Other\n\n## Entries\n")
            shutil.copy2(
                first_summary.with_suffix("") / TARGET.RECORD_FILENAME,
                second_summary.with_suffix("") / TARGET.RECORD_FILENAME,
            )

            rejected = run_validate(
                second_summary, result_date="2026-08-15", jobs=1
            )

            self.assertEqual(rejected["status"], "error")
            self.assertIn("belongs to", rejected["error"])

    def test_relative_record_identity_survives_checkout_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_root = root / "checkout-one"
            second_root = root / "checkout-two"
            first_root.mkdir()
            summary = make_no_semantic_log(first_root)
            completed = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(completed["status"], "complete")
            shutil.copytree(first_root, second_root, copy_function=shutil.copy2)
            relocated = second_root / "docs" / "empty.md"

            result = run_validate(
                relocated, result_date="2026-08-15", jobs=1
            )

            self.assertEqual(result["status"], "complete")
            stored = TARGET.load_record(
                relocated.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(stored["summary"], "docs/empty.md")

    def test_operational_failure_retains_prior_completed_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            completed = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            report = summary.with_suffix("") / "validation.md"
            before = report.read_bytes()

            with mock.patch.object(
                CONTROLLER, "scan_log", side_effect=RuntimeError("interrupted")
            ):
                failed = run_validate(
                    summary,
                    result_date="2026-08-16",
                    jobs=1,
                    mode="reproduction",
                )
            self.assertEqual(failed["status"], "error")
            self.assertTrue(failed["prior_report_retained"])
            self.assertTrue(failed["progress_retained"])
            self.assertEqual(report.read_bytes(), before)

    def test_cli_validate_returns_structured_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            with mock.patch("builtins.print") as printed:
                status = CLI.main(
                    [
                        "validate",
                        "--summary",
                        str(summary),
                        "--date",
                        "2026-08-15",
                        "--jobs",
                        "1",
                    ]
                )
            self.assertEqual(status, 0)
            payload = json.loads(printed.call_args.args[0])
            self.assertEqual(payload["status"], "complete")

    def test_reproduction_mode_does_not_use_standard_cached_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "complete")

            with mock.patch.object(
                CONTROLLER,
                "scan_log",
                wraps=CONTROLLER.scan_log,
            ) as scanned:
                second = run_validate(
                    summary,
                    result_date="2026-08-15",
                    jobs=1,
                    publish=False,
                    mode="reproduction",
                )

            self.assertEqual(second["status"], "complete")
            scanned.assert_called_once()

    def test_deterministic_failures_are_durable_result_data(self) -> None:
        assembly = mock.Mock()
        assembly.outcome_inputs.rules_version = "rules-v1"
        assembly.outcome_inputs.component_versions = {"integrity": 1}
        assembly.outcome_inputs.input_projection_versions = {"exact-material": 1}
        assembly.outcome_inputs.completed_checks = []
        assembly.failures = [
            {"scope": "e001", "target": "missing.csv", "checks": ["Integrity"]}
        ]
        assembly.result.return_value = {
            "date": "2026-08-15",
            "failures": assembly.failures,
        }
        record = CONTROLLER._target_record(
            "docs/mini.md",
            assembly,
            TARGET.empty_record("docs/mini.md", "rules-v1"),
        )
        self.assertEqual(record["failures"], assembly.failures)

    def test_semantic_exchange_accepts_only_decisions_and_rationales(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            first = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "review_required")
            decision_path = Path(first["decision_file"])
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            self.assertEqual(first["item_count"], len(template["items"]))
            self.assertLessEqual(first["item_count"], 200)
            self.assertGreater(first["byte_count"], 0)
            packet_text = Path(first["review_packet"]).read_text(encoding="utf-8")
            self.assertIn(
                "The retained value is `1.0` in [output](data/output.csv).",
                packet_text,
            )
            partial = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertGreater(len(partial["outcomes"]), 0)
            self.assertGreater(len(partial["failures"]), 0)
            self.assertIsNotNone(partial["continuation"])
            self.assertEqual(partial["continuation"]["kind"], "ordinary")
            resumed = run_validate(summary, jobs=1)
            self.assertEqual(resumed["status"], "review_required")
            self.assertEqual(resumed["continuation"], first["continuation"])
            self.assertEqual(resumed["decision_file"], first["decision_file"])
            for item in template["items"]:
                item["decision"] = (
                    "pass"
                    if item["kind"] == "semantic_provenance"
                    else (
                        "unresolved"
                        if item["kind"] == "orphan_candidate"
                        else "needs_context"
                    )
                )
                item["rationale"] = "Focused fixture decision."
            decision_path.write_text(
                json.dumps(template, indent=2) + "\n", encoding="utf-8"
            )

            continued = run_validate(
                summary, decision_file=decision_path, jobs=1
            )
            self.assertEqual(continued["status"], "review_required")
            self.assertLess(continued["item_count"], first["item_count"])
            expanded = json.loads(
                Path(continued["decision_file"]).read_text(encoding="utf-8")
            )
            self.assertTrue(
                all(item["context_level"] == 1 for item in expanded["items"])
            )
            self.assertTrue(
                all(
                    "needs_context" not in item["allowed_decisions"]
                    for item in expanded["items"]
                )
            )
            first_contexts = {
                (item["kind"], item["entry"], item["identity"]): item[
                    "context_identity"
                ]
                for item in template["items"]
            }
            self.assertTrue(
                all(
                    item["context_identity"]
                    != first_contexts[
                        (item["kind"], item["entry"], item["identity"])
                    ]
                    for item in expanded["items"]
                )
            )
            record = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            reviewed = [
                judgment
                for judgment in record["judgments"]
                if judgment["kind"] == "review-decision"
                and judgment["subject"]["kind"] == "semantic_provenance"
            ]
            self.assertEqual(len(reviewed), 1)
            self.assertEqual(reviewed[0]["rationale"], "Focused fixture decision.")

    def test_collection_scope_exchange_accepts_exact_member_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {
                "entry": "e001",
                "kind": "collection_scope",
                "identity": "docs/mini/result.csv",
                "collections": ["docs/mini/models"],
                "reason": "select material members",
            }
            template_item = EXCHANGE._ordinary_template(item)
            template_item["decision"] = {
                "members": {
                    "docs/mini/models": ["run-1/training-history.csv"]
                }
            }
            template_item["rationale"] = "The producer reads this retained history."
            template = {
                "schema_version": EXCHANGE.EXCHANGE_SCHEMA_VERSION,
                "continuation": "continuation",
                "items": [template_item],
            }
            internal = {
                "schema_version": EXCHANGE.EXCHANGE_SCHEMA_VERSION,
                "continuation": "continuation",
                "template": {
                    **template,
                    "items": [
                        {
                            **template_item,
                            "decision": None,
                            "rationale": None,
                        }
                    ],
                },
                "adjudication": {"review_queue": [item]},
            }
            decision_path = root / "review-decisions.json"
            decision_path.write_text(
                json.dumps(template, indent=2) + "\n", encoding="utf-8"
            )
            (root / EXCHANGE.INTERNAL_FILENAME).write_text(
                json.dumps(internal) + "\n", encoding="utf-8"
            )

            decisions, loaded_internal = EXCHANGE.load_decisions(decision_path)
            actions = EXCHANGE.decisions_to_actions(decisions, loaded_internal)

            self.assertEqual(
                actions["actions"],
                [
                    {
                        "match": {
                            "kind": "collection_scope",
                            "entry": "e001",
                            "identity": "docs/mini/result.csv",
                        },
                        "decision": "pass",
                        "members": {
                            "docs/mini/models": [
                                "run-1/training-history.csv"
                            ]
                        },
                    }
                ],
            )

    def test_large_review_appends_judgment_shards_between_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-16-e001-large-orphan-review"
                / "e001.md"
            )
            write(
                summary,
                "# Mini\n\n## Entries\n\n"
                "- [e001](mini/entries/2026-08-16-e001-large-orphan-review/"
                "e001.md)\n",
            )
            write(entry, "# Entry\n\nNo retained result claims.\n")
            for number in range(201):
                write(entry.parent / "data" / f"item-{number:04d}.csv", "value\n")

            first = run_validate(summary, result_date="2026-08-16", jobs=1)
            self.assertEqual(first["status"], "review_required")
            self.assertIn("session_identity", first)
            session_dir = Path(first["decision_file"]).parent.parent
            self.assertTrue(
                session_dir.is_relative_to(
                    (root / EXCHANGE.VALIDATION_WORK_ROOT).resolve()
                )
            )
            self.addCleanup(
                lambda: EXCHANGE.finish_deferred_orphan_session(session_dir)
                if session_dir.exists()
                else None
            )
            record_path = summary.with_suffix("") / TARGET.RECORD_FILENAME
            logical = TARGET.load_record(record_path)
            continuation_before = copy.deepcopy(logical["continuation"])
            record_path.write_bytes(TARGET._json_bytes(logical))
            session_before = {
                path.relative_to(session_dir).as_posix(): path.read_bytes()
                for path in session_dir.rglob("*")
                if path.is_file()
            }
            migrated = run_validate(summary, jobs=1, migrate_storage=True)
            self.assertEqual(migrated["status"], "migrated")
            self.assertEqual(
                TARGET.load_record(record_path)["continuation"],
                continuation_before,
            )
            self.assertEqual(
                {
                    path.relative_to(session_dir).as_posix(): path.read_bytes()
                    for path in session_dir.rglob("*")
                    if path.is_file()
                },
                session_before,
            )
            record_before = record_path.read_bytes()
            decision_path = Path(first["decision_file"])
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            for item in template["items"]:
                item["decision"] = "unresolved"
                item["rationale"] = "No local evidence connection is recorded."
            write(decision_path, json.dumps(template, indent=2) + "\n")

            second = run_validate(summary, decision_file=decision_path, jobs=1)

            self.assertEqual(second["status"], "review_required")
            record_after_first_batch = record_path.read_bytes()
            self.assertNotEqual(record_after_first_batch, record_before)
            stored = TARGET.load_record(record_path)
            self.assertEqual(len(stored["judgments"]), len(template["items"]))
            self.assertEqual(stored["continuation"]["kind"], "paged")
            self.assertNotIn("current", stored["continuation"])
            read_object = EXCHANGE._read_object
            with (
                mock.patch.object(
                    CONTROLLER,
                    "scan_log",
                    side_effect=AssertionError("active resume must not scan"),
                ),
                mock.patch.object(
                    CONTROLLER,
                    "hydrate_record_shell",
                    side_effect=AssertionError("active resume must not hydrate"),
                ),
                mock.patch.object(
                    STORE,
                    "_read_owned_bytes",
                    side_effect=AssertionError("active resume must not read shards"),
                ),
                mock.patch.object(
                    EXCHANGE, "_read_object", wraps=read_object
                ) as session_reader,
            ):
                resumed = run_validate(summary, jobs=1)
            self.assertEqual(resumed["status"], "review_required")
            self.assertEqual(resumed["continuation"], second["continuation"])
            self.assertEqual(record_path.read_bytes(), record_after_first_batch)
            self.assertNotIn(
                EXCHANGE.DEFERRED_BASE_FILENAME,
                [call.args[0].name for call in session_reader.call_args_list],
            )
            second_path = Path(second["decision_file"])
            second_template = json.loads(second_path.read_text(encoding="utf-8"))
            for item in second_template["items"]:
                item["decision"] = "unresolved"
                item["rationale"] = "No local evidence connection is recorded."
            write(second_path, json.dumps(second_template, indent=2) + "\n")

            with mock.patch.object(
                CONTROLLER,
                "_finish_deferred_acceptance",
                side_effect=RuntimeError("interrupted before final combine"),
            ):
                interrupted = run_validate(
                    summary, decision_file=second_path, jobs=1
                )
            self.assertEqual(interrupted["status"], "error")
            self.assertTrue(session_dir.exists())

            completed = run_validate(summary, jobs=1)

            self.assertEqual(completed["status"], "complete")
            self.assertFalse(session_dir.exists())

    def test_missing_active_paged_session_fails_without_restarting_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-16-e001-large-orphan-review"
                / "e001.md"
            )
            write(
                summary,
                "# Mini\n\n## Entries\n\n"
                "- [e001](mini/entries/2026-08-16-e001-large-orphan-review/"
                "e001.md)\n",
            )
            write(entry, "# Entry\n\nNo retained result claims.\n")
            for number in range(201):
                write(entry.parent / "data" / f"item-{number:04d}.csv", "value\n")
            first = run_validate(summary, result_date="2026-08-16", jobs=1)
            self.assertEqual(first["status"], "review_required")
            session_dir = Path(first["decision_file"]).parent.parent
            (session_dir / EXCHANGE.SESSION_STATE_FILENAME).unlink()

            failed = run_validate(summary, jobs=1)

            self.assertEqual(failed["status"], "error")
            self.assertIn("session state", failed["error"])
            record = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(record["continuation"]["kind"], "paged")

    def test_stale_or_modified_semantic_template_cannot_mutate_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            first = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            decision_path = Path(first["decision_file"])
            record_path = summary.with_suffix("") / TARGET.RECORD_FILENAME
            before = record_path.read_bytes()
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            template["items"][0]["question"] = "broadened question"
            template["items"][0]["decision"] = "fail"
            template["items"][0]["rationale"] = "Not supported."
            for item in template["items"][1:]:
                item["decision"] = "needs_context"
                item["rationale"] = "More focused context is required."
            decision_path.write_text(
                json.dumps(template, indent=2) + "\n", encoding="utf-8"
            )

            rejected = run_validate(
                summary, decision_file=decision_path, jobs=1
            )
            self.assertEqual(rejected["status"], "error")
            self.assertIn("modified CLI-owned fields", rejected["error"])
            self.assertEqual(record_path.read_bytes(), before)

    def test_target_validation_needs_no_git_or_maintained_log_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs" / "empty.md"
            (summary.with_suffix("") / "entries").mkdir(parents=True)
            write(
                summary,
                "# Empty Log\n\n## Summary\n\nNo claims.\n\n## Entries\n",
            )
            other = root / "docs" / "other.md"
            (other.with_suffix("") / "entries").mkdir(parents=True)
            write(other, "# Other\n\n## Entries\n")
            write(other.with_suffix("") / TARGET.RECORD_FILENAME, "{broken")

            with mock.patch(
                "validation.inventory.find_project_root",
                side_effect=AssertionError("Git-root lookup"),
            ):
                result = run_validate(
                    summary, result_date="2026-08-15", jobs=1
                )
            self.assertEqual(result["status"], "complete")

    def test_other_log_use_does_not_exempt_a_local_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            orphan = entry.parent / "data" / "direct.csv"
            other = root / "docs" / "other.md"
            other_entry = (
                other.with_suffix("")
                / "entries"
                / "2026-08-15-e001-other"
                / "e001.md"
            )
            write(
                other,
                "# Other\n\n## Entries\n\n"
                "- [e001](other/entries/2026-08-15-e001-other/e001.md)\n",
            )
            write(
                other_entry,
                "# Other Entry\n\n## Results\n\n"
                f"[Cross-log use]({orphan.as_posix()})\n",
            )

            result = run_validate(
                summary, result_date="2026-08-15", jobs=1
            )
            template = json.loads(
                Path(result["decision_file"]).read_text(encoding="utf-8")
            )
            orphan_identities = {
                item["identity"]
                for item in template["items"]
                if item["kind"] == "orphan_candidate"
            }
            self.assertIn(
                "docs/mini/entries/2026-08-07-e001-validation-fixture/"
                "data/direct.csv",
                orphan_identities,
            )

    def test_completed_paged_review_restores_scoped_producer_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = (root / "docs" / "mini.md").resolve()
            write(summary, "# Mini\n")
            scan = {
                "project_root": root.resolve().as_posix(),
                "summary": "docs/mini.md",
            }
            adjudication = {"date": "2026-08-16", "review_queue": []}
            accepted = {
                "scan": scan,
                "adjudication": adjudication,
                "decisions": {"items": []},
                "orphan_fingerprints": {},
                "session_dir": (
                    root / ".astro-agents-validation-work" / "summary" / "session"
                ).as_posix(),
            }
            progress = CONTROLLER.ValidationProgress(
                {"outcomes": [], "judgments": []}, {}, "native-v2:loaded", True
            )
            assembly = mock.Mock()
            assembly.counts.return_value = {}

            with (
                mock.patch.object(
                    CONTROLLER,
                    "decisions_to_actions",
                    return_value={"schema_version": 6, "actions": []},
                ) as convert,
                mock.patch.object(
                    CONTROLLER,
                    "apply_review_decisions",
                    return_value=(adjudication, {}),
                ),
                mock.patch.object(
                    CONTROLLER,
                    "_complete_adjudication",
                    return_value=({"outcomes": []}, assembly),
                ),
                mock.patch.object(CONTROLLER, "finish_deferred_orphan_session"),
            ):
                CONTROLLER._finish_deferred_acceptance(
                    summary, accepted, progress
                )

            internal = convert.call_args.args[1]
            self.assertIs(internal["scan"], scan)
            self.assertIs(internal["adjudication"], adjudication)

    def test_partial_progress_excludes_targets_with_pending_scope_review(self) -> None:
        target = "docs/mini/entries/e001/data/result.csv"
        adjudication = {
            "summary": [],
            "review_queue": [
                {
                    "kind": "upstream_producer",
                    "entry": "e001",
                    "identity": target,
                    "collections": ["output/collection"],
                }
            ],
            "entries": [
                {
                    "id": "e001",
                    "targets": [
                        {
                            "target": target,
                            "integrity": "2026-08-16",
                            "provenance": "2026-08-16",
                            "reproducibility": "-",
                        }
                    ],
                    "orphan_items": [],
                }
            ],
        }

        partial = CONTROLLER._partial_adjudication(adjudication)

        self.assertEqual(partial["entries"][0]["targets"], [])

    def test_completed_paged_context_request_starts_expanded_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            write(summary, "# Mini\n")
            queue_item = {
                "kind": "collection_scope",
                "entry": "e001",
                "identity": "docs/mini/result.csv",
            }
            adjudication = {
                "date": "2026-08-16",
                "review_queue": [queue_item],
            }
            decisions = {
                "items": [
                    {
                        **queue_item,
                        "context_level": 0,
                        "decision": "needs_context",
                        "rationale": "Nested members are required.",
                    }
                ]
            }
            accepted = {
                "scan": {
                    "project_root": root.as_posix(),
                    "summary": "docs/mini.md",
                },
                "adjudication": adjudication,
                "decisions": decisions,
                "orphan_fingerprints": {},
                "session_dir": (
                    root / ".astro-agents-validation-work" / "summary" / "session"
                ).as_posix(),
            }
            progress = CONTROLLER.ValidationProgress(
                {"outcomes": [], "judgments": []}, {}, "native-v2:loaded", True
            )
            next_result = {"status": "review_required"}

            with (
                mock.patch.object(
                    CONTROLLER,
                    "decisions_to_actions",
                    return_value={"schema_version": 6, "actions": []},
                ),
                mock.patch.object(
                    CONTROLLER,
                    "apply_review_decisions",
                    return_value=(adjudication, {}),
                ),
                mock.patch.object(
                    CONTROLLER,
                    "durable_review_judgments",
                    return_value=[],
                ),
                mock.patch.object(
                    CONTROLLER, "_review_required", return_value=next_result
                ) as review,
                mock.patch.object(CONTROLLER, "finish_deferred_orphan_session"),
            ):
                result = CONTROLLER._finish_deferred_acceptance(
                    summary, accepted, progress
                )

            self.assertIs(result, next_result)
            self.assertEqual(review.call_args.args[3], json.loads(json.dumps({
                EXCHANGE.context_request_key(decisions["items"][0]): 1
            })))

    def test_migration_recovery_retains_valid_rows_and_reissues_bare_pass(self) -> None:
        target = "docs/mini/data/result.csv"
        adjudication = {
            "entries": [{"id": "e001", "targets": [{"target": target}]}],
            "review_queue": [
                {
                    "kind": "mechanical_failure",
                    "entry": "e001",
                    "identity": target,
                    "workflow": {"status": "unresolved"},
                },
                {
                    "kind": "semantic_provenance",
                    "entry": "Summary",
                    "identity": "4.2%",
                },
            ],
        }
        invalid = {
            "kind": "mechanical_failure",
            "entry": "e001",
            "identity": target,
            "decision": "pass",
        }
        valid = {
            "kind": "semantic_provenance",
            "entry": "Summary",
            "identity": "4.2%",
            "decision": "pass",
        }
        decisions = {"schema_version": 1, "items": [invalid, valid]}

        recovered = CONTROLLER._migration_recovery_decisions(
            adjudication, decisions
        )

        self.assertEqual(recovered, {"schema_version": 1, "items": [valid]})

    def test_migration_normalizes_deferred_dependency_identities(self) -> None:
        file_identity = {
            "size": 12,
            "mtime_ns": 34,
            "ctime_ns": 56,
            "sha256": "a" * 64,
        }
        collection_identity = {
            **file_identity,
            "members": ["case_001.pkl", "case_002.pkl"],
        }
        scan = {
            "files": {"docs/mini/script.py": file_identity},
            "directory_memberships": {
                "docs/mini/data": {"members": 3, "sha256": "b" * 64}
            },
        }
        adjudication = {
            "summary": [],
            "entries": [
                {
                    "targets": [
                        {
                            "dependencies": [
                                {
                                    "path": "docs/mini/script.py",
                                    "role": "producer",
                                    "identity": file_identity,
                                },
                                {
                                    "path": "docs/mini/data",
                                    "role": "input",
                                    "members": collection_identity["members"],
                                    "identity": collection_identity,
                                },
                            ]
                        }
                    ]
                }
            ],
        }

        normalized = CONTROLLER._normalize_migration_session_dependencies(
            scan, adjudication
        )

        dependencies = normalized["entries"][0]["targets"][0]["dependencies"]
        self.assertNotIn("identity", dependencies[0])
        self.assertNotIn("identity", dependencies[1])
        self.assertIn(
            "identity",
            adjudication["entries"][0]["targets"][0]["dependencies"][0],
        )

    def test_migration_rejects_incompatible_deferred_dependency_identity(self) -> None:
        scan = {
            "files": {
                "docs/mini/script.py": {
                    "size": 12,
                    "mtime_ns": 34,
                    "ctime_ns": 56,
                    "sha256": "a" * 64,
                }
            },
            "directory_memberships": {},
        }
        adjudication = {
            "summary": [],
            "entries": [
                {
                    "targets": [
                        {
                            "dependencies": [
                                {
                                    "path": "docs/mini/script.py",
                                    "role": "producer",
                                    "identity": {
                                        "size": 13,
                                        "mtime_ns": 34,
                                        "ctime_ns": 56,
                                        "sha256": "a" * 64,
                                    },
                                }
                            ]
                        }
                    ]
                }
            ],
        }

        with self.assertRaisesRegex(
            CONTROLLER.ValidationToolError, "incompatible with its scan snapshot"
        ):
            CONTROLLER._normalize_migration_session_dependencies(scan, adjudication)

    def test_context_upgrade_publishes_before_old_session_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs/mini.md"
            old_session = root / "work/old-session"
            context = CONTROLLER.LoadedValidation(
                CONTROLLER.ValidationRequest(summary),
                summary,
                summary.with_suffix(""),
                root,
                "docs/mini.md",
                {"outcomes": [], "judgments": []},
                {},
                "native-v2:loaded",
            )
            recovery = {
                "scan": {},
                "adjudication": {},
                "context_levels": {},
                "session_dir": old_session.as_posix(),
            }
            events = []

            with (
                mock.patch.object(
                    CONTROLLER,
                    "_review_required",
                    return_value={"session_identity": "new-session"},
                ),
                mock.patch.object(
                    CONTROLLER,
                    "write_record_and_cache",
                    side_effect=lambda *args: events.append("manifest"),
                ),
                mock.patch.object(
                    CONTROLLER,
                    "finish_deferred_orphan_session",
                    side_effect=lambda *args: events.append("cleanup"),
                ),
            ):
                result = CONTROLLER._refresh_empty_context_session(
                    context, recovery
                )

            self.assertEqual(events, ["manifest", "cleanup"])
            self.assertEqual(result["session_identity"], "new-session")

    def test_context_upgrade_publication_failure_retains_old_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "docs/mini.md"
            old_session = root / "work/old-session"
            context = CONTROLLER.LoadedValidation(
                CONTROLLER.ValidationRequest(summary),
                summary,
                summary.with_suffix(""),
                root,
                "docs/mini.md",
                {"outcomes": [], "judgments": []},
                {},
                "native-v2:loaded",
            )
            recovery = {
                "scan": {},
                "adjudication": {},
                "context_levels": {},
                "session_dir": old_session.as_posix(),
            }

            with (
                mock.patch.object(
                    CONTROLLER,
                    "_review_required",
                    return_value={"session_identity": "new-session"},
                ),
                mock.patch.object(
                    CONTROLLER,
                    "write_record_and_cache",
                    side_effect=OSError("interrupted"),
                ),
                mock.patch.object(
                    CONTROLLER, "finish_deferred_orphan_session"
                ) as cleanup,
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    CONTROLLER._refresh_empty_context_session(context, recovery)

            cleanup.assert_not_called()

    def test_migration_normalizes_reviewable_failure_to_producer_choice(self) -> None:
        adjudication = {
            "review_queue": [
                {
                    "kind": "mechanical_failure",
                    "hard_failures": [],
                    "producer_candidates": [{"invocation": "invocation-1"}],
                },
                {
                    "kind": "mechanical_failure",
                    "hard_failures": ["Integrity"],
                    "producer_candidates": [{"invocation": "invocation-2"}],
                },
            ]
        }

        normalized = CONTROLLER._normalize_migration_review_kinds(adjudication)

        self.assertEqual(
            [item["kind"] for item in normalized["review_queue"]],
            ["semantic_fallback", "mechanical_failure"],
        )
        self.assertEqual(
            [item["kind"] for item in adjudication["review_queue"]],
            ["mechanical_failure", "mechanical_failure"],
        )


if __name__ == "__main__":
    unittest.main()
