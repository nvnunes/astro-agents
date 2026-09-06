from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log
from research_log_validation_test_support import mechanical_log, write

RESULTS = importlib.import_module("validation.mechanical_results")


class FindingsCliTests(unittest.TestCase):
    def test_list_and_show_read_one_published_finding_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            completed = run_log(
                root, "validate", "--path", str(summary.with_suffix(""))
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = summary.with_suffix("") / "validation/results.json"
            before = result_path.read_bytes()

            listed = run_log(
                root, "findings", "list", "--path", str(summary.with_suffix(""))
            )

            self.assertEqual(listed.returncode, 0, listed.stderr)
            payload = json.loads(listed.stdout)
            self.assertEqual(payload["schema"], "research-log-findings-list/1")
            self.assertGreater(payload["matched_groups"], 0)
            selected = payload["findings"][0]
            narrowed = run_log(
                root,
                "findings",
                "list",
                "--path",
                str(summary.with_suffix("")),
                "--entry",
                selected["entry"],
                "--subject",
                selected["subject"],
            )
            self.assertEqual(narrowed.returncode, 0, narrowed.stderr)
            narrowed_payload = json.loads(narrowed.stdout)
            self.assertTrue(narrowed_payload["findings"])
            self.assertTrue(
                all(
                    item["entry"] == selected["entry"]
                    and item["subject"] == selected["subject"]
                    for item in narrowed_payload["findings"]
                )
            )

            shown = run_log(
                root,
                "findings",
                "show",
                "--path",
                str(summary.with_suffix("")),
                "--id",
                selected["check_id"],
            )

            self.assertEqual(shown.returncode, 0, shown.stderr)
            finding = json.loads(shown.stdout)
            self.assertEqual(finding["schema"], "research-log-finding/1")
            self.assertEqual(finding["finding"]["identity"], selected["check_id"])
            self.assertEqual(finding["finding"]["code"], selected["code"])
            self.assertEqual(result_path.read_bytes(), before)

    def test_list_is_bounded_and_collapses_exact_duplicate_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            checks = []
            for number in range(51):
                subject = f"data/item-{number:03}.csv"
                checks.append(
                    RESULTS.MechanicalCheck(
                        f"orphan:e001:{number:03}",
                        RESULTS.CheckScope.ORPHAN,
                        RESULTS.CheckStatus.FAIL,
                        subject,
                        failure=RESULTS.FailurePayload(
                            "orphan.material.unused", subject, {}, "Hygiene"
                        ),
                    )
                )
            checks.append(
                RESULTS.MechanicalCheck(
                    "orphan:e001:duplicate",
                    RESULTS.CheckScope.ORPHAN,
                    RESULTS.CheckStatus.FAIL,
                    "data/item-000.csv",
                    failure=RESULTS.FailurePayload(
                        "orphan.material.unused",
                        "data/item-000.csv",
                        {},
                        "Hygiene",
                    ),
                )
            )
            record = RESULTS.MechanicalGeneratedRecord.build(
                summary.resolve().as_posix(),
                "test-rules",
                "2026-09-05",
                checks,
            )
            result_path = summary.with_suffix("") / "validation/results.json"
            write(result_path, record.canonical_json() + "\n")

            completed = run_log(
                root, "findings", "list", "--path", str(summary.with_suffix(""))
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["matched_groups"], 51)
            self.assertEqual(payload["returned_groups"], 50)
            self.assertEqual(payload["omitted_groups"], 1)
            self.assertEqual(len(payload["findings"]), 50)
            first = payload["findings"][0]
            self.assertEqual(first["subject"], "data/item-000.csv")
            self.assertEqual(first["represented_checks"], 2)

    def test_expected_query_failures_use_precise_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))

            missing = run_log(root, "findings", "list", "--path", log_path)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("findings.result.missing", missing.stderr)

            result_path = summary.with_suffix("") / "validation/results.json"
            write(result_path, '{"schema":"research-log-mechanical/2"}\n')
            unsupported = run_log(root, "findings", "list", "--path", log_path)
            self.assertEqual(unsupported.returncode, 2)
            self.assertIn("findings.result.schema_unsupported", unsupported.stderr)

            write(result_path, "{not json}\n")
            malformed = run_log(root, "findings", "list", "--path", log_path)
            self.assertEqual(malformed.returncode, 2)
            self.assertIn("findings.result.malformed", malformed.stderr)

    def test_show_distinguishes_duplicate_unknown_and_nonfinding_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))
            completed = run_log(root, "validate", "--path", log_path)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = summary.with_suffix("") / "validation/results.json"
            payload = json.loads(result_path.read_text())
            passing = next(
                check["identity"]
                for check in payload["checks"]
                if check["status"] == "pass"
            )

            unknown = run_log(
                root, "findings", "show", "--path", log_path, "--id", "absent"
            )
            self.assertEqual(unknown.returncode, 2)
            self.assertIn("findings.id.unknown", unknown.stderr)
            not_finding = run_log(
                root, "findings", "show", "--path", log_path, "--id", passing
            )
            self.assertEqual(not_finding.returncode, 2)
            self.assertIn("findings.id.not_finding", not_finding.stderr)

            payload["checks"].append(payload["checks"][0])
            write(result_path, json.dumps(payload) + "\n")
            duplicate = run_log(
                root,
                "findings",
                "show",
                "--path",
                log_path,
                "--id",
                payload["checks"][0]["identity"],
            )
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("findings.id.duplicate", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()
