from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log
from research_log_validation_test_support import mechanical_log


class FindingsCliTests(unittest.TestCase):
    def test_list_and_show_read_one_published_finding_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root, output_option="results")
            completed = run_log(
                root,
                "validate",
                "run",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result_path = summary.with_suffix("") / ".cache/results.sqlite"
            before = result_path.read_bytes()

            listed = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
            )

            self.assertEqual(listed.returncode, 0, listed.stderr)
            payload = json.loads(listed.stdout)
            self.assertEqual(
                payload["schema"], "research-log-validation-finding-list/1"
            )
            self.assertGreater(payload["total"], 0)
            selected = payload["items"][0]

            shown = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--format",
                "json",
                "--path",
                str(summary.with_suffix("")),
                "--id",
                selected["finding_id"],
            )

            self.assertEqual(shown.returncode, 0, shown.stderr)
            finding = json.loads(shown.stdout)
            self.assertEqual(
                finding["schema"], "research-log-validation-finding-detail/1"
            )
            self.assertEqual(
                finding["finding"]["finding_id"], selected["finding_id"]
            )
            self.assertEqual(result_path.read_bytes(), before)

    def test_list_returns_every_finding_without_a_fifty_item_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "study"
            root.mkdir()
            (root / "entries").mkdir()
            root.with_suffix(".md").write_text("# Study\n", encoding="utf-8")
            from test_validation_read_model import _publish, _synthetic_snapshot

            _publish(root, _synthetic_snapshot(root, 51))

            completed = run_log(
                Path(directory),
                "validate",
                "list",
                "findings",
                "--format",
                "json",
                "--path",
                str(root),
                "--limit",
                "100",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["total"], 51)
            self.assertEqual(len(payload["items"]), 51)
            self.assertEqual(payload["items"][0]["subject"], "subject-000")

    def test_expected_query_failures_use_precise_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))

            missing = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--format",
                "json",
                "--path",
                log_path,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(
                json.loads(missing.stdout)["error"]["code"],
                "validation.store.missing",
            )

            cold = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--format",
                "json",
                "--path",
                log_path,
                "--id",
                "missing",
            )
            self.assertEqual(cold.returncode, 2)
            self.assertEqual(
                json.loads(cold.stdout)["error"]["code"],
                "validation.store.missing",
            )

    def test_generic_result_export_is_removed_from_the_public_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))
            completed = run_log(
                root, "validate", "run", "--format", "json", "--path", log_path
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            exported = run_log(root, "results", "export")
            self.assertEqual(exported.returncode, 2)
            listed = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--format",
                "json",
                "--path",
                log_path,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            value = json.loads(listed.stdout)
            self.assertNotIn("result_id", value)
            self.assertNotIn("record", value)

    def test_detail_distinguishes_unknown_ids_without_exposing_passing_checks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = mechanical_log(root)
            log_path = str(summary.with_suffix(""))
            completed = run_log(
                root, "validate", "run", "--format", "json", "--path", log_path
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            unknown = run_log(
                root,
                "validate",
                "detail",
                "finding",
                "--format",
                "json",
                "--path",
                log_path,
                "--id",
                "absent",
            )
            self.assertEqual(unknown.returncode, 2)
            self.assertEqual(
                json.loads(unknown.stdout)["error"]["code"],
                "validation.finding.missing",
            )
            self.assertEqual(unknown.stderr, "")
            listed = run_log(
                root,
                "validate",
                "list",
                "findings",
                "--format",
                "json",
                "--path",
                log_path,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout)["total"], 0)


if __name__ == "__main__":
    unittest.main()
