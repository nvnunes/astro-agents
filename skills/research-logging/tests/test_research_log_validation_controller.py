from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import CLI, make_log, write

CONTROLLER = importlib.import_module("validation.controller")
TARGET = importlib.import_module("validation.target_records")


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
            result = CONTROLLER.validate(
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
            first = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            second = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")

    def test_operational_failure_retains_prior_completed_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = make_no_semantic_log(Path(directory))
            completed = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(completed["status"], "complete")
            report = summary.with_suffix("") / "validation.md"
            before = report.read_bytes()

            with mock.patch.object(
                CONTROLLER, "scan_log", side_effect=RuntimeError("interrupted")
            ):
                failed = CONTROLLER.validate(
                    summary, result_date="2026-08-16", jobs=1
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

    def test_deterministic_failures_are_durable_result_data(self) -> None:
        bundle = mock.Mock()
        bundle.state = {
            "validation_rules_version": "rules-v1",
            "component_versions": {"integrity": 1},
            "input_projection_versions": {"exact-material": 1},
            "completed_checks": [],
            "result": {
                "date": "2026-08-15",
                "failures": [
                    {"scope": "e001", "target": "missing.csv", "checks": ["Integrity"]}
                ],
            },
        }
        bundle.decisions = {"judgments": []}
        record = CONTROLLER._target_record(
            "docs/mini.md",
            bundle,
            TARGET.empty_record("docs/mini.md", "rules-v1"),
        )
        self.assertEqual(record["failures"], bundle.state["result"]["failures"])

    def test_semantic_exchange_accepts_only_decisions_and_rationales(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            first = CONTROLLER.validate(
                summary, result_date="2026-08-15", jobs=1
            )
            self.assertEqual(first["status"], "review_required")
            decision_path = Path(first["decision_file"])
            template = json.loads(decision_path.read_text(encoding="utf-8"))
            self.assertEqual(first["item_count"], len(template["items"]))
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

            continued = CONTROLLER.validate(
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

    def test_stale_or_modified_semantic_template_cannot_mutate_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            first = CONTROLLER.validate(
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

            rejected = CONTROLLER.validate(
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
            write(other.with_suffix("") / "validation-state.json", "{broken")

            with (
                mock.patch(
                    "validation.graph_store.discover_repository_summaries",
                    side_effect=AssertionError("population scan"),
                ),
                mock.patch(
                    "validation.inventory.find_project_root",
                    side_effect=AssertionError("Git-root lookup"),
                ),
            ):
                result = CONTROLLER.validate(
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

            result = CONTROLLER.validate(
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
