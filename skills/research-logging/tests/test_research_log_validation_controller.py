from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import CLI, make_log, write

CONTROLLER = importlib.import_module("validation.controller")
EXCHANGE = importlib.import_module("validation.review_exchange")
TARGET = importlib.import_module("validation.target_records")


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
            for item in template["items"]:
                item["decision"] = (
                    "pass"
                    if item["kind"] == "semantic_provenance"
                    else "needs_context"
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
            record = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            reviewed = [
                judgment
                for judgment in record["judgments"]
                if judgment["kind"] == "review-decision"
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

    def test_large_orphan_review_does_not_rewrite_record_between_pages(self) -> None:
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
            self.addCleanup(
                lambda: EXCHANGE.finish_deferred_orphan_session(session_dir)
                if session_dir.exists()
                else None
            )
            record_path = summary.with_suffix("") / TARGET.RECORD_FILENAME
            record_before = record_path.read_bytes()
            decision_path = Path(first["decision_file"])
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            for item in template["items"]:
                item["decision"] = "unresolved"
                item["rationale"] = "No local evidence connection is recorded."
            write(decision_path, json.dumps(template, indent=2) + "\n")

            second = run_validate(summary, decision_file=decision_path, jobs=1)

            self.assertEqual(second["status"], "review_required")
            self.assertEqual(record_path.read_bytes(), record_before)
            second_path = Path(second["decision_file"])
            second_template = json.loads(second_path.read_text(encoding="utf-8"))
            for item in second_template["items"]:
                item["decision"] = "unresolved"
                item["rationale"] = "No local evidence connection is recorded."
            write(second_path, json.dumps(second_template, indent=2) + "\n")

            completed = run_validate(
                summary, decision_file=second_path, jobs=1
            )

            self.assertEqual(completed["status"], "complete")
            self.assertFalse(session_dir.exists())

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


if __name__ == "__main__":
    unittest.main()
