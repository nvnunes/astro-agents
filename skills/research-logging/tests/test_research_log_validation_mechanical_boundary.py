from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from research_log_validation_test_support import make_log, write

MECHANICAL = importlib.import_module("validation.mechanical")
RUNTIME = importlib.import_module("validation.runtime")

CASES = Path(__file__).with_name("validation-mechanical-boundary-cases.json")


class MechanicalBoundaryTests(unittest.TestCase):
    def test_internal_entry_point_composes_scan_and_evaluation(self) -> None:
        calls: list[tuple[str, object]] = []
        request = MECHANICAL.MechanicalEvaluationRequest(
            Path("docs/log.md"), "2026-08-28", jobs=3
        )

        def scan(actual: Any) -> tuple[dict[str, object], dict[str, object]]:
            calls.append(("scan", actual))
            return {"summary": "docs/log.md"}, {"files_hashed": 2}

        def evaluate(actual: Any, date: str) -> dict[str, object]:
            calls.append(("evaluate", (actual, date)))
            return {"status": "complete"}

        result = MECHANICAL.evaluate_mechanical(
            request,
            MECHANICAL.MechanicalEvaluationPolicy(scan, evaluate),
        )

        self.assertEqual(result.result, {"status": "complete"})
        self.assertEqual(result.scan, {"summary": "docs/log.md"})
        self.assertEqual(result.metrics, {"files_hashed": 2})
        self.assertEqual([name for name, _ in calls], ["scan", "evaluate"])

    def test_characterization_fixture_names_every_boundary_case(self) -> None:
        fixture = json.loads(CASES.read_text(encoding="utf-8"))

        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(
            {case["id"] for case in fixture["cases"]},
            {
                "collection-membership",
                "direct-artifact",
                "exact-producer",
                "generated-validation-state",
                "named-input",
                "recorded-commands",
                "script-internal-role-inference",
                "unchanged-cache-reuse",
                "v1-entry-evidence-record",
                "v1-summary-evidence-record",
                "validation-note-retention",
            },
        )

    def test_current_scan_characterizes_preserved_and_rejected_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry_path = make_log(root)
            validation_dir = summary.with_suffix("") / "validation"
            write(validation_dir / "generated.txt", "generated\n")
            scan, metrics = RUNTIME.scan_log(summary, jobs=1)

            entry = scan["entries"][0]
            self.assertEqual(len(entry["commands"]), 3)
            self.assertEqual(
                len(scan["evidence_records"]["summary"]["rows"]), 1
            )
            self.assertEqual(
                len(scan["evidence_records"]["entry_folders"][0]["rows"]), 3
            )

            output = next(
                candidate
                for candidate in entry["candidate_targets"]
                if candidate["identity"].endswith("data/output.csv")
            )
            self.assertTrue(output["presented"])
            self.assertEqual(output["mechanical"]["status"], "ok")

            collection_identity = next(
                identity
                for identity in scan["directory_memberships"]
                if identity.endswith("data/collection")
            )
            self.assertEqual(
                scan["directory_memberships"][collection_identity]["members"], 2
            )
            self.assertTrue(entry["validation_notes"])
            self.assertFalse(
                any("/validation/" in identity for identity in scan["files"])
            )
            self.assertGreater(metrics["files_hashed"], 0)

            prior_cache = {
                "files": scan["files"],
                "inspections": scan["mechanical_checks"],
            }
            repeated, repeated_metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_cache=prior_cache
            )
            self.assertEqual(repeated["input_fingerprint"], scan["input_fingerprint"])
            self.assertGreater(repeated_metrics["files_reused"], 0)
            self.assertGreater(repeated_metrics["inspections_reused"], 0)

            script = entry_path.parent / "scripts" / "roles.py"
            write(
                script,
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--source', type=str)\n"
                "args = parser.parse_args()\n"
                "open(args.source).read()\n",
            )
            roles = importlib.import_module("validation.commands").argparse_flags(
                script
            )["argument_roles"]
            self.assertEqual(roles, {"source": "input"})


if __name__ == "__main__":
    unittest.main()
