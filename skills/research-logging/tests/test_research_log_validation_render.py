from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

RENDER = importlib.import_module("validation.render")


class RenderAssemblyTests(unittest.TestCase):
    def test_assembly_uses_one_measurement_set_for_state_and_counts(self) -> None:
        measurements = RENDER.RenderMeasurements(
            summary_rows=1,
            summary_failed=0,
            entry_rows=2,
            entry_failed=1,
            entries=1,
            failed_entries=1,
            successful_checks=3,
            completed_checks=4,
            file_identities=2,
            failure_rows=1,
        )
        assembly = RENDER.RenderAssembly(
            report_text="# Validation\n",
            failure_text="# Failure\n",
            graph_record={"graph_identity": "graph"},
            state_inputs=RENDER.RenderStateInputs(
                schema_version=4,
                rules_version="test-rules",
                local_snapshot_identity="a" * 64,
                input_fingerprint="input",
                input_files={"entry.md": {"sha256": "source"}},
                mechanical_checks={"target": {"integrity": "PASS"}},
                directory_memberships={},
                file_identities={"artifact.csv": {"sha256": "artifact"}},
                completed_checks=[{"check": "Integrity"}],
                orphan_dispositions=[],
                report_identity={"sha256": "report"},
                graph_identity="graph",
            ),
            measurements=measurements,
            date="2026-08-11",
            mode="standard",
            requested_scope="complete standard scope",
            scope={"summary": True, "entries": ["e001"]},
            failures=[
                {
                    "scope": "e001",
                    "target": "artifact.csv",
                    "checks": ["Provenance"],
                }
            ],
            local_snapshot_identity="a" * 64,
        )

        bundle = assembly.bundle()

        self.assertEqual(bundle.state["result"]["entry_rows"], 2)
        self.assertEqual(bundle.state["result"]["failure_rows"], 1)
        self.assertEqual(
            bundle.state["files"], {"artifact.csv": {"sha256": "artifact"}}
        )
        self.assertEqual(assembly.counts()["entry_rows"], 2)
        self.assertEqual(assembly.counts()["failure_rows"], 1)

    def test_owner_renders_entry_rows_and_completed_checks(self) -> None:
        rendered = RENDER.render_entry_rows(
            [
                {
                    "id": "e001",
                    "title": "Example",
                    "path": "docs/example/e001.md",
                    "targets": [
                        {
                            "target": "result.csv",
                            "sections": ["Results"],
                            "integrity": "2026-08-11",
                            "provenance": "2026-08-11",
                            "reproducibility": "-",
                            "notes": "-",
                            "findings": [],
                            "dependencies": [
                                {"path": "result.csv", "role": "target"}
                            ],
                        }
                    ],
                }
            ]
        )

        self.assertEqual(rendered.total, 1)
        self.assertEqual(rendered.failed, 0)
        self.assertEqual(len(rendered.completed_checks), 2)
        self.assertIn("| result.csv | Results |", rendered.lines[-1])


if __name__ == "__main__":
    unittest.main()
