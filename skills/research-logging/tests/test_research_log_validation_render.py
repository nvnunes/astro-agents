from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

RENDER = importlib.import_module("validation.render")
COMPATIBILITY = importlib.import_module("validation.compatibility")


class RenderAssemblyTests(unittest.TestCase):
    def test_stored_check_binding_is_equivalent_with_lifecycle_caches(self) -> None:
        command = {
            "section": "Results",
            "command": "python run.py --output data/result.csv",
            "line": 12,
            "path_arguments": [
                {"path": "/project/data/result.csv", "role_hint": "output"}
            ],
        }
        invocation = COMPATIBILITY.invocation_identities("e001", [command])[0]
        scan = {
            "project_root": "/project",
            "entries": [{"id": "e001", "commands": [command]}],
            "resolved_paths": {
                "data/result.csv": "/project/data/result.csv"
            },
        }
        check = {
            "entry": "e001",
            "target": "data/result.csv",
            "check": "Integrity",
            "result": "2026-08-26",
            "dependencies": [],
            "resolution": {"producer_invocation": invocation},
        }

        expected = RENDER.producer_bindings_for_check(scan, check)
        stored = RENDER._stored_checks(scan, [check])

        self.assertEqual(stored[0]["producer_bindings"], expected)
        self.assertEqual(
            stored[0]["compatibility_identity"],
            RENDER.outcome_compatibility_identity(
                stored[0]["rule_dependencies"],
                stored[0]["input_dependencies"],
                expected,
            ),
        )

    def test_stored_checks_reuse_binding_verification_inputs(self) -> None:
        scan = {"resolved_paths": {}, "entries": []}
        checks = [
            {
                "entry": "e001",
                "target": f"result-{index}.csv",
                "check": "Integrity",
                "result": "2026-08-26",
                "dependencies": [],
            }
            for index in range(2)
        ]
        identity_cache = {"/project/result.csv": "result.csv"}
        invocation_cache = {"invocation": object()}
        expected_bindings = [
            {
                "invocation_identity": "invocation",
                "target_identity": "result.csv",
            }
        ]

        with (
            mock.patch.object(
                RENDER,
                "resolved_identity_cache",
                return_value=identity_cache,
            ) as build_identities,
            mock.patch.object(
                RENDER,
                "producer_binding_invocation_cache",
                return_value=invocation_cache,
            ) as build_invocations,
            mock.patch.object(
                RENDER,
                "producer_bindings_for_check",
                return_value=expected_bindings,
            ) as project_bindings,
        ):
            stored = RENDER._stored_checks(scan, checks)

        build_identities.assert_called_once_with(scan)
        build_invocations.assert_called_once_with(scan)
        self.assertEqual(project_bindings.call_count, len(checks))
        for call in project_bindings.call_args_list:
            self.assertIs(call.args[2], identity_cache)
            self.assertIs(call.args[3], invocation_cache)
        self.assertEqual(
            [item["producer_bindings"] for item in stored],
            [expected_bindings, expected_bindings],
        )
        self.assertTrue(
            all(item["compatibility_identity"] for item in stored)
        )

    def test_assembly_uses_one_measurement_set_for_result_and_counts(self) -> None:
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
            outcome_inputs=RENDER.RenderOutcomeInputs(
                rules_version="test-rules",
                component_versions={"material_identity": 1},
                input_projection_versions={"entry": 1},
                input_files={"entry.md": {"sha256": "source"}},
                mechanical_checks={"target": {"integrity": "PASS"}},
                directory_memberships={},
                file_identities={"artifact.csv": {"sha256": "artifact"}},
                completed_checks=[{"check": "Integrity"}],
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
        )

        result = assembly.result()

        self.assertEqual(result["entry_rows"], 2)
        self.assertEqual(result["failure_rows"], 1)
        self.assertEqual(
            assembly.outcome_inputs.file_identities,
            {"artifact.csv": {"sha256": "artifact"}},
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
