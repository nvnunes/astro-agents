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
        "# Empty Log\n\n## Summary\n\nNo quantitative claims.\n\n## Entries\n",
    )
    return summary


class ValidationControllerTests(unittest.TestCase):
    def test_changed_review_inputs_restart_without_deleting_active_session(
        self,
    ) -> None:
        summary = Path("/tmp/project/docs/mini.md")
        session = Path("/tmp/project/docs/mini/validation/.cache/work/session")
        context = CONTROLLER.LoadedValidation(
            CONTROLLER.ValidationRequest(summary),
            summary,
            summary.with_suffix(""),
            Path("/tmp/project"),
            "docs/mini.md",
            {
                "continuation": {
                    "kind": "paged",
                    "session": "session",
                    "session_identity": "identity",
                }
            },
            {},
            "native-v2:loaded",
        )
        with (
            mock.patch.object(
                CONTROLLER,
                "review_session_refresh_context",
                return_value={"scan": {}, "session_dir": session.as_posix()},
            ),
            mock.patch.object(
                CONTROLLER, "scan_input_metadata_matches", return_value=False
            ),
            mock.patch.object(CONTROLLER, "resume_review_session") as resume,
            mock.patch.object(CONTROLLER, "finish_review_session") as finish,
        ):
            result = CONTROLLER._resume_active_review(context)

        self.assertIsNone(result)
        self.assertIsNone(context.record["continuation"])
        self.assertEqual(context.retired_review_session, session)
        resume.assert_not_called()
        finish.assert_not_called()

    def test_final_acceptance_loads_canonical_accepted_judgment_shards(
        self,
    ) -> None:
        identities = ["b" * 64, "a" * 64]
        loaded = [
            {"identity": "a" * 64, "subject": {"kind": "first"}},
            {"identity": "b" * 64, "subject": {"kind": "second"}},
        ]
        decisions = {
            "items": [
                {
                    "kind": "orphan_candidate",
                    "entry": "e001",
                    "identity": "data/result.csv",
                }
            ]
        }
        with mock.patch.object(
            CONTROLLER,
            "load_judgments_for_subjects",
            return_value=loaded,
        ) as load:
            result = CONTROLLER._accepted_review_judgments(
                Path("/tmp/log"),
                {"_sharded_manifest": {}},
                decisions,
                identities,
            )

        assert result is not None
        self.assertEqual([judgment["identity"] for judgment in result], identities)
        self.assertEqual(
            load.call_args.args[2],
            [
                {
                    "kind": "orphan_candidate",
                    "entry": "e001",
                    "identity": "data/result.csv",
                }
            ],
        )

    def test_no_semantic_log_completes_and_publishes_only_target_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            before = summary.read_bytes()
            with mock.patch.object(
                CONTROLLER,
                "hydrate_record_shell",
                side_effect=AssertionError("new validation must not fully hydrate"),
            ):
                result = run_validate(summary, result_date="2026-08-15", jobs=1)

            self.assertEqual(result["status"], "complete")
            self.assertNotIn("review_diagnostics", result)
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
                    "validation",
                },
            )
            stored = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            self.assertEqual(stored["failures"], [])

    def test_new_dry_run_writes_no_durable_validation_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            output = summary.with_suffix("")

            result = run_validate(
                summary,
                result_date="2026-08-15",
                jobs=1,
                publish=False,
            )

            self.assertEqual(result["status"], "complete")
            self.assertFalse(result["published"])
            self.assertFalse((output / TARGET.RECORD_FILENAME).exists())
            self.assertFalse((output / TARGET.CACHE_FILENAME).exists())
            self.assertFalse((output / "validation.md").exists())

    def test_repeat_no_semantic_validation_needs_no_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            record_path = summary.with_suffix("") / TARGET.RECORD_FILENAME
            before = record_path.read_bytes()
            with (
                mock.patch.object(
                    CONTROLLER,
                    "scan_log",
                    side_effect=AssertionError("cached result must not rebuild"),
                ),
                mock.patch.object(
                    CONTROLLER,
                    "create_exchange",
                    side_effect=AssertionError("cached result must not create review"),
                ),
            ):
                second = run_validate(
                    summary,
                    result_date="2026-08-15",
                    jobs=1,
                    review_diagnostics=True,
                )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")
            self.assertTrue(second["cached"])
            self.assertEqual(record_path.read_bytes(), before)

    def test_cached_completion_does_not_depend_on_subject_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "complete")
            index_path = (
                summary.with_suffix("") / "validation" / ".cache" / STORE.INDEX_FILENAME
            )
            index_path.unlink()

            with mock.patch.object(
                CONTROLLER,
                "scan_log",
                side_effect=AssertionError("cached completion must not scan"),
            ):
                second = run_validate(
                    summary,
                    result_date="2026-08-15",
                    jobs=1,
                    review_diagnostics=True,
                )

            self.assertEqual(second["status"], "complete")
            self.assertTrue(second["cached"])
            self.assertFalse(index_path.exists())
            self.assertEqual(second["diagnostics"]["files_hashed"], 0)
            self.assertEqual(second["diagnostics"]["bytes_hashed"], 0)
            self.assertEqual(second["review_diagnostics"]["pages"], [])
            self.assertEqual(
                second["review_diagnostics"]["lifecycle"],
                [
                    {
                        "stage": "terminal_completion",
                        "item_count": 0,
                        "items_by_kind": {},
                    }
                ],
            )

    def test_cached_completion_prunes_incompatible_judgments_without_scan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "complete")
            output = summary.with_suffix("")
            report = output / "validation.md"
            before_report = report.read_bytes()
            shell = TARGET.load_record_header_with_source(
                output / TARGET.RECORD_FILENAME
            )[0]
            TARGET.append_judgment_batch(
                output,
                shell,
                [
                    {
                        "identity": "legacy-incompatible",
                        "kind": "orphan-disposition",
                        "result": "unresolved",
                        "decision_date": "2026-08-15",
                        "subject": {
                            "entry": "e001",
                            "identity": "docs/empty/legacy.csv",
                        },
                        "rule_dependencies": {"orphan_graph": 1},
                        "input_dependencies": [],
                        "rationale": "Historical incompatible decision.",
                        "rationale_provenance": "recorded",
                        "provenance": "native-reviewed",
                    }
                ],
            )

            opened: list[str] = []
            original_read = STORE._read_owned_bytes

            def read_judgment_only(validation_dir: Path, ref: dict) -> bytes:
                opened.append(ref["kind"])
                if ref["kind"] != "judgments":
                    raise AssertionError("cached cleanup hydrated unrelated rows")
                return original_read(validation_dir, ref)

            with (
                mock.patch.object(
                    CONTROLLER,
                    "scan_log",
                    side_effect=AssertionError("cached cleanup must not scan"),
                ),
                mock.patch.object(
                    STORE, "_read_owned_bytes", side_effect=read_judgment_only
                ),
            ):
                second = run_validate(summary, result_date="2026-08-15", jobs=1)

            self.assertEqual(second["status"], "complete")
            self.assertTrue(second["cached"])
            self.assertEqual(second["cleanup"]["incompatible_rows_removed"], 1)
            self.assertEqual(set(opened), {"judgments"})
            self.assertEqual(report.read_bytes(), before_report)
            stored = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            self.assertEqual(stored["judgments"], [])

            with (
                mock.patch.object(
                    CONTROLLER,
                    "scan_log",
                    side_effect=AssertionError("cached completion must not scan"),
                ),
                mock.patch.object(
                    STORE,
                    "_read_owned_bytes",
                    side_effect=AssertionError(
                        "completed cleanup must not reopen judgment shards"
                    ),
                ),
            ):
                third = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(third["status"], "complete")
            self.assertTrue(third["cached"])

    def test_removed_semantic_evidence_invalidates_only_its_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = make_log(Path(directory))
            result = run_validate(summary, result_date="2026-08-16", jobs=1)
            while result["status"] == "review_required":
                decision_path = Path(result["decision_file"])
                template = json.loads(decision_path.read_text(encoding="utf-8"))
                for item in template["items"]:
                    allowed = item["allowed_decisions"]
                    decision: object
                    if item["kind"] == "semantic_provenance":
                        decision = "pass"
                    elif item["kind"] == "semantic_fallback":
                        decision = next(
                            value
                            for value in allowed
                            if value not in {"fail:workflow", "needs_context"}
                        )
                    elif item["kind"] == "mechanical_failure":
                        decision = "keep"
                    elif item["kind"] == "orphan_candidate":
                        decision = next(
                            value
                            for value in allowed
                            if str(value).startswith("retain:")
                        )
                    elif item["kind"] == "orphan_subtree":
                        decision = next(
                            value
                            for value in allowed
                            if isinstance(value, dict)
                            and value.get("disposition") == "retained"
                        )
                    elif (
                        item["kind"] == "collection_scope"
                        and item["context_level"] == 1
                    ):
                        collections = next(
                            value["members"]
                            for value in allowed
                            if isinstance(value, dict)
                        )
                        decision = {
                            "members": {path: ["a.txt"] for path in collections}
                        }
                    else:
                        decision = "needs_context"
                    item["decision"] = decision
                    item["rationale"] = "Focused fixture decision."
                decision_path.write_text(
                    json.dumps(template, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = run_validate(summary, decision_file=decision_path, jobs=1)

            self.assertEqual(result["status"], "complete")
            output = summary.with_suffix("")
            before = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            affected_judgments = [
                judgment
                for judgment in before["judgments"]
                if judgment.get("subject", {})
                .get("identity", "")
                .endswith("data/output.csv")
            ]
            self.assertTrue(affected_judgments)
            unaffected_outcomes = {
                outcome["compatibility_identity"]
                for outcome in before["outcomes"]
                if all(
                    not dependency["path"].endswith("data/output.csv")
                    for dependency in outcome["dependencies"]
                )
            }
            self.assertTrue(unaffected_outcomes)

            (entry.parent / "data" / "output.csv").unlink()
            with (
                mock.patch.object(
                    CONTROLLER, "scan_log", wraps=CONTROLLER.scan_log
                ) as scanned,
                mock.patch.object(
                    CONTROLLER,
                    "hydrate_record_shell",
                    side_effect=AssertionError(
                        "semantic reuse must not fully hydrate history"
                    ),
                ),
            ):
                rerun = run_validate(summary, result_date="2026-08-16", jobs=1)

            self.assertEqual(rerun["status"], "review_required")
            self.assertNotEqual(rerun.get("cached"), True)
            scanned.assert_called_once()
            decisions = json.loads(
                Path(rerun["decision_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(len(decisions["items"]), 1)
            self.assertEqual(
                (
                    decisions["items"][0]["kind"],
                    decisions["items"][0]["identity"],
                ),
                (
                    "mechanical_failure",
                    (
                        "docs/mini/entries/2026-08-07-e001-validation-fixture/"
                        "data/output.csv"
                    ),
                ),
            )

            current = TARGET.load_record(output / TARGET.RECORD_FILENAME)
            current_judgments = {
                judgment["identity"] for judgment in current["judgments"]
            }
            self.assertTrue(
                all(
                    judgment["identity"] in current_judgments
                    for judgment in affected_judgments
                )
            )
            current_outcomes = {
                outcome["compatibility_identity"] for outcome in current["outcomes"]
            }
            self.assertTrue(unaffected_outcomes <= current_outcomes)
            self.assertTrue(
                any(
                    failure["target"].endswith("data/output.csv")
                    for failure in current["failures"]
                )
            )

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
                second = run_validate(summary, result_date="2026-08-16", jobs=1)

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
                repaired = run_validate(summary, result_date="2026-08-15", jobs=1)

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
                failed = run_validate(summary, result_date="2026-08-16", jobs=1)

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
            completed = run_validate(first_summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(completed["status"], "complete")
            second_summary = root / "docs" / "other.md"
            (second_summary.with_suffix("") / "entries").mkdir(parents=True)
            write(second_summary, "# Other\n\n## Entries\n")
            (second_summary.with_suffix("") / TARGET.RECORD_FILENAME).parent.mkdir(
                parents=True
            )
            shutil.copy2(
                first_summary.with_suffix("") / TARGET.RECORD_FILENAME,
                second_summary.with_suffix("") / TARGET.RECORD_FILENAME,
            )

            rejected = run_validate(second_summary, result_date="2026-08-15", jobs=1)

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

            result = run_validate(relocated, result_date="2026-08-15", jobs=1)

            self.assertEqual(result["status"], "complete")
            stored = TARGET.load_record(
                relocated.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(stored["summary"], "docs/empty.md")

    def test_operational_failure_retains_prior_completed_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            completed = run_validate(summary, result_date="2026-08-15", jobs=1)
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
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
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
            TARGET.empty_record_shell("docs/mini.md", "rules-v1"),
        )
        self.assertEqual(record["failures"], assembly.failures)

    def test_semantic_exchange_accepts_only_decisions_and_rationales(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            with mock.patch.object(
                CONTROLLER,
                "hydrate_record_shell",
                side_effect=AssertionError("new review must not fully hydrate"),
            ):
                first = run_validate(summary, result_date="2026-08-15", jobs=1)
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
            self.assertEqual(partial["continuation"]["kind"], "paged")
            self.assertEqual(
                partial["continuation"]["session_identity"],
                first["session_identity"],
            )
            resumed = run_validate(summary, jobs=1)
            self.assertEqual(resumed["status"], "review_required")
            self.assertEqual(resumed["continuation"], first["continuation"])
            self.assertEqual(resumed["decision_file"], first["decision_file"])
            for item in template["items"]:
                item["decision"] = (
                    "pass"
                    if item["kind"] == "semantic_provenance"
                    else (
                        {
                            "action": "classify-subtree",
                            "disposition": "unresolved",
                        }
                        if item["kind"] == "orphan_subtree"
                        else (
                            "unresolved"
                            if item["kind"] == "orphan_candidate"
                            else "needs_context"
                        )
                    )
                )
                item["rationale"] = "Focused fixture decision."
            decision_path.write_text(
                json.dumps(template, indent=2) + "\n", encoding="utf-8"
            )

            continued = run_validate(summary, decision_file=decision_path, jobs=1)
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
                    != first_contexts[(item["kind"], item["entry"], item["identity"])]
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

    def test_changed_inputs_restart_a_decision_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = make_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "review_required")
            old_session = Path(first["decision_file"]).parent.parent
            decision_path = Path(first["decision_file"])
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            for item in template["items"]:
                item["decision"] = (
                    {
                        "action": "classify-subtree",
                        "disposition": "unresolved",
                    }
                    if item["kind"] == "orphan_subtree"
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
            entry.write_text(
                entry.read_text(encoding="utf-8") + "\n<!-- changed -->\n",
                encoding="utf-8",
            )

            restarted = run_validate(
                summary,
                decision_file=decision_path,
                jobs=1,
            )

            self.assertEqual(restarted["status"], "review_required")
            self.assertNotEqual(
                restarted["session_identity"], first["session_identity"]
            )
            self.assertFalse(old_session.exists())

    def test_pre_pass_3_ordinary_packet_continues_without_rebuilding_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(first["status"], "review_required")
            current_decision = Path(first["decision_file"])
            current_session = current_decision.parent.parent
            output_dir = summary.with_suffix("")
            base = json.loads(
                (current_session / EXCHANGE.SESSION_BASE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            index = json.loads(
                (current_session / EXCHANGE.SESSION_INDEX_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            template = json.loads(current_decision.read_text(encoding="utf-8"))
            identity = str(template["continuation"])
            locator = EXCHANGE._session_locator(identity)
            legacy_session = EXCHANGE._session_path(output_dir, locator)
            legacy_session.mkdir(parents=True)
            legacy_internal = {
                "schema_version": EXCHANGE.EXCHANGE_SCHEMA_VERSION,
                "continuation": identity,
                "template": copy.deepcopy(template),
                "scan": base["scan"],
                "adjudication": base["adjudication"],
                "orphan_fingerprints": index.get("orphan_fingerprints", {}),
                "controller": base["controller"],
                "ordinary_session": {
                    "output_dir": output_dir.as_posix(),
                    "session": locator,
                },
            }
            legacy_decision = legacy_session / "review-decisions.json"
            shutil.copyfile(current_decision, legacy_decision)
            shutil.copyfile(
                Path(first["review_packet"]), legacy_session / "review-packet.md"
            )
            write(
                legacy_session / EXCHANGE.INTERNAL_FILENAME,
                json.dumps(legacy_internal) + "\n",
            )

            record_path = output_dir / TARGET.RECORD_FILENAME
            shell, _ = TARGET.load_record_header_with_source(record_path)
            shell = TARGET.hydrate_record_rows(
                shell, output_dir, ("outcomes", "failures")
            )
            cache, _ = TARGET.load_cache(output_dir / TARGET.CACHE_FILENAME)
            shard_closure = copy.deepcopy(shell["_sharded_manifest"]["shards"])
            shell["continuation"] = {
                "kind": "ordinary",
                "identity": identity,
                "item_count": len(template["items"]),
            }
            TARGET.write_record_and_cache(output_dir, shell, cache)
            rewritten, _ = TARGET.load_record_header_with_source(record_path)
            self.assertEqual(rewritten["_sharded_manifest"]["shards"], shard_closure)
            EXCHANGE.finish_review_session(current_session)

            for item in template["items"]:
                item["decision"] = (
                    "pass"
                    if item["kind"] == "semantic_provenance"
                    else (
                        {
                            "action": "classify-subtree",
                            "disposition": "unresolved",
                        }
                        if item["kind"] == "orphan_subtree"
                        else (
                            "unresolved"
                            if item["kind"] == "orphan_candidate"
                            else "needs_context"
                        )
                    )
                )
                item["rationale"] = "Compatible legacy packet decision."
            write(legacy_decision, json.dumps(template, indent=2) + "\n")

            continued = run_validate(summary, decision_file=legacy_decision, jobs=1)

            self.assertEqual(continued["status"], "review_required")
            self.assertFalse(legacy_session.exists())
            stored = TARGET.load_record(record_path)
            self.assertEqual(stored["continuation"]["kind"], "paged")

    def test_session_publish_retry_reuses_the_created_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            original = CONTROLLER.write_record_and_cache

            def interrupt_continuation(output_dir, record, cache):
                if record.get("continuation") is not None:
                    raise OSError("interrupted continuation publication")
                original(output_dir, record, cache)

            with mock.patch.object(
                CONTROLLER,
                "write_record_and_cache",
                side_effect=interrupt_continuation,
            ):
                interrupted = run_validate(summary, result_date="2026-08-15", jobs=1)

            self.assertEqual(interrupted["status"], "error")
            work_root = (
                summary.with_suffix("")
                / "validation"
                / ".cache"
                / EXCHANGE.VALIDATION_WORK_ROOT
            )
            sessions = [path for path in work_root.iterdir() if path.is_dir()]
            self.assertEqual(len(sessions), 1)
            session_dir = sessions[0]
            before = {
                path.relative_to(session_dir).as_posix(): path.read_bytes()
                for path in session_dir.rglob("*")
                if path.is_file()
            }

            recovered = run_validate(summary, result_date="2026-08-15", jobs=1)

            self.assertEqual(recovered["status"], "review_required")
            self.assertEqual(recovered["session_identity"], session_dir.name)
            self.assertEqual(
                {
                    path.relative_to(session_dir).as_posix(): path.read_bytes()
                    for path in session_dir.rglob("*")
                    if path.is_file()
                },
                before,
            )
            stored = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(stored["continuation"]["kind"], "paged")

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
                "members": {"docs/mini/models": ["run-1/training-history.csv"]}
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
                        "members": {"docs/mini/models": ["run-1/training-history.csv"]},
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
                write(entry.parent / f"item-{number:04d}.csv", "value\n")

            first = run_validate(summary, result_date="2026-08-16", jobs=1)
            self.assertEqual(first["status"], "review_required")
            self.assertIn("session_identity", first)
            session_dir = Path(first["decision_file"]).parent.parent
            self.assertTrue(
                session_dir.is_relative_to(
                    (
                        summary.with_suffix("")
                        / "validation"
                        / ".cache"
                        / EXCHANGE.VALIDATION_WORK_ROOT
                    ).resolve()
                )
            )
            self.addCleanup(
                lambda: (
                    EXCHANGE.finish_review_session(session_dir)
                    if session_dir.exists()
                    else None
                )
            )
            record_path = summary.with_suffix("") / TARGET.RECORD_FILENAME
            logical = TARGET.load_record(record_path)
            continuation_before = copy.deepcopy(logical["continuation"])
            session_before = {
                path.relative_to(session_dir).as_posix(): path.read_bytes()
                for path in session_dir.rglob("*")
                if path.is_file()
            }
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
            self.assertEqual(
                [call.args[0].name for call in session_reader.call_args_list].count(
                    EXCHANGE.SESSION_BASE_FILENAME
                ),
                1,
            )
            second_path = Path(second["decision_file"])
            second_template = json.loads(second_path.read_text(encoding="utf-8"))
            for item in second_template["items"]:
                item["decision"] = "unresolved"
                item["rationale"] = "No local evidence connection is recorded."
            write(second_path, json.dumps(second_template, indent=2) + "\n")

            with mock.patch.object(
                CONTROLLER,
                "_finish_review_acceptance",
                side_effect=RuntimeError("interrupted before final combine"),
            ):
                interrupted = run_validate(summary, decision_file=second_path, jobs=1)
            self.assertEqual(interrupted["status"], "error")
            self.assertTrue(session_dir.exists())

            with mock.patch.object(
                CONTROLLER,
                "hydrate_record_shell",
                side_effect=AssertionError(
                    "review completion must not fully hydrate history"
                ),
            ):
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
                write(entry.parent / f"item-{number:04d}.csv", "value\n")
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
            first = run_validate(summary, result_date="2026-08-15", jobs=1)
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

            rejected = run_validate(summary, decision_file=decision_path, jobs=1)
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

            result = run_validate(summary, result_date="2026-08-15", jobs=1)
            self.assertEqual(result["status"], "complete")
            record = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            self.assertEqual(record["summary"], "docs/empty.md")

    def test_other_log_use_does_not_exempt_a_local_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            orphan = entry.parent / "data" / "direct.csv"
            other = root / "docs" / "other.md"
            other_entry = (
                other.with_suffix("") / "entries" / "2026-08-15-e001-other" / "e001.md"
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

            result = run_validate(summary, result_date="2026-08-15", jobs=1)
            template = json.loads(
                Path(result["decision_file"]).read_text(encoding="utf-8")
            )
            orphan_identities = {
                item["identity"]
                for item in template["items"]
                if item["kind"] in {"orphan_candidate", "orphan_subtree"}
            }
            self.assertIn(
                "docs/mini/entries/2026-08-07-e001-validation-fixture/data/direct.csv",
                orphan_identities,
            )
            self.assertIn(
                "data/direct.csv",
                Path(result["review_packet"]).read_text(encoding="utf-8"),
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
                    summary.with_suffix("")
                    / "validation"
                    / ".cache"
                    / "work"
                    / "session"
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
                    return_value=({"outcomes": []}, assembly, {}),
                ),
                mock.patch.object(CONTROLLER, "finish_review_session"),
            ):
                CONTROLLER._finish_review_acceptance(summary, accepted, progress)

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
                    summary.with_suffix("")
                    / "validation"
                    / ".cache"
                    / "work"
                    / "session"
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
                mock.patch.object(CONTROLLER, "finish_review_session"),
            ):
                result = CONTROLLER._finish_review_acceptance(
                    summary, accepted, progress
                )

            self.assertIs(result, next_result)
            self.assertEqual(
                review.call_args.args[3],
                json.loads(
                    json.dumps({EXCHANGE.context_request_key(decisions["items"][0]): 1})
                ),
            )

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
                    "finish_review_session",
                    side_effect=lambda *args: events.append("cleanup"),
                ),
            ):
                result = CONTROLLER._refresh_empty_context_session(context, recovery)

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
                mock.patch.object(CONTROLLER, "finish_review_session") as cleanup,
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    CONTROLLER._refresh_empty_context_session(context, recovery)

            cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
