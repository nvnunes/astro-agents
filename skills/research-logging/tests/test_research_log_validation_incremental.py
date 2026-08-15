from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

INCREMENTAL = importlib.import_module("validation.incremental")
COMPATIBILITY = importlib.import_module("validation.compatibility")


def unused(*_args: Any) -> Any:
    raise AssertionError("rules and schema gates must run before identity mechanics")


OPERATIONS = INCREMENTAL.IncrementalOperations(
    dependency_contract=unused,
    dependency_snapshot=unused,
    graph_slice=unused,
    orphan_fingerprints=unused,
    report_identity=unused,
)
POLICY = INCREMENTAL.IncrementalPolicy(
    state_schema_version=6,
    orphan_inventory_version=7,
)


class IncrementalComparisonTests(unittest.TestCase):
    def test_native_orphan_judgment_ignores_graph_resolver_churn(self) -> None:
        entry = {
            "id": "e001",
            "path": "docs/example/e001.md",
            "orphan_inventory": [
                {"identity": "docs/example/orphan.csv", "kind": "artifact"}
            ],
            "validation_notes": [],
        }
        scan = {
            "entries": [entry],
            "repository_dependencies": [
                {"path": "docs/example/orphan.csv", "owner": "docs/other.md"}
            ],
        }
        candidate = next(
            item
            for item in COMPATIBILITY.orphan_input_dependencies(
                scan, entry, [{"identity": "docs/example/orphan.csv"}]
            )
            if item["kind"] == "orphan-candidate"
        )
        judgment = {
            "kind": "orphan-disposition",
            "subject": {
                "entry": "e001",
                "identity": "docs/example/orphan.csv",
            },
            "rule_dependencies": COMPATIBILITY.orphan_rule_dependencies(),
            "input_dependencies": [
                candidate,
                {
                    "kind": "graph-resolver",
                    "semantic_identity": "graph-resolver:e001",
                    "projection_version": 1,
                    "content_identity": "stale",
                    "relationship": "orphan-graph-resolution",
                },
            ],
        }

        self.assertTrue(
            INCREMENTAL._native_judgment_compatible(
                scan,
                judgment,
            )
        )

    def test_invocation_identity_ignores_lines_and_unrelated_commands(self) -> None:
        command = {
            "section": "Results",
            "command": "python run.py --output data/result.csv",
            "line": 12,
        }
        original = COMPATIBILITY.invocation_identities("e001", [command])[0]
        moved = COMPATIBILITY.invocation_identities(
            "e001",
            [
                {
                    "section": "Results",
                    "command": "python unrelated.py",
                    "line": 4,
                },
                {**command, "line": 40},
            ],
        )[1]

        self.assertEqual(moved, original)

    def test_duplicate_command_count_is_part_of_producer_binding(self) -> None:
        command = {
            "section": "Results",
            "command": "python run.py --output data/result.csv",
            "line": 12,
            "path_arguments": [
                {
                    "path": "/project/data/result.csv",
                    "role_hint": "output",
                }
            ],
        }

        def binding(commands: list[dict[str, Any]]) -> dict[str, Any]:
            invocation = COMPATIBILITY.invocation_identities("e001", commands)[0]
            scan = {
                "entries": [{"id": "e001", "commands": commands}],
                "resolved_paths": {
                    "data/result.csv": "/project/data/result.csv"
                },
            }
            check = {
                "entry": "e001",
                "target": "data/result.csv",
                "dependencies": [],
                "resolution": {"producer_invocation": invocation},
            }
            return COMPATIBILITY.producer_bindings_for_check(scan, check)[0]

        self.assertEqual(binding([command])["duplicate_count"], 1)
        duplicate = binding([command, {**command, "line": 30}])
        self.assertEqual(duplicate["duplicate_count"], 2)

    def test_orphan_fingerprints_reuse_supplied_resolved_path_cache(self) -> None:
        root = Path("/project")
        script = root / "log/scripts/run.py"
        artifact = root / "log/data/output.csv"
        scan = {
            "project_root": root.as_posix(),
            "resolved_paths": {
                "log/scripts/run.py": script.as_posix(),
                "log/data/output.csv": artifact.as_posix(),
            },
            "files": {"log/scripts/run.py": {"sha256": "script"}},
            "directory_memberships": {},
            "mechanical_checks": {},
        }
        entry = {
            "commands": [
                {
                    "script": script.as_posix(),
                    "data_tokens": [
                        {
                            "name": "output",
                            "path": artifact.as_posix(),
                        }
                    ],
                }
            ],
            "data_index": {"rows": []},
            "orphan_inventory": [
                {"identity": "log/data/output.csv", "kind": "artifact"}
            ],
            "validation_notes": [],
        }
        cache: dict[str, str] = {}

        with mock.patch.object(
            INCREMENTAL,
            "_resolved_identity_cache",
            side_effect=AssertionError("cache must not be rebuilt"),
        ):
            fingerprints = INCREMENTAL.orphan_item_fingerprints(entry, scan, cache)

        self.assertEqual(set(fingerprints), {"log/data/output.csv"})

    def test_native_durable_judgment_reconstructs_scoped_producer_bindings(
        self,
    ) -> None:
        judgment = {
            "kind": "completed-check",
            "subject": {
                "entry": "e001",
                "target": "data/result.csv",
                "check": "Provenance",
            },
            "result": "2026-08-15",
            "basis": {"producer_invocation": "e001:producer"},
            "producer_bindings": [
                {
                    "coverage_identity": "data/result.csv",
                    "invocation_identity": "e001:producer",
                }
            ],
            "input_dependencies": [
                {
                    "kind": "collection-member",
                    "semantic_identity": "collection-member:data/models:a.bin",
                    "projection_version": 1,
                    "content_identity": "a" * 64,
                    "relationship": "input",
                    "source_locator": {
                        "path": "data/models",
                        "member": "a.bin",
                    },
                },
                {
                    "kind": "collection-member",
                    "semantic_identity": "collection-member:data/models:b.bin",
                    "projection_version": 1,
                    "content_identity": "b" * 64,
                    "relationship": "input",
                    "source_locator": {
                        "path": "data/models",
                        "member": "b.bin",
                    },
                },
            ],
        }

        check = INCREMENTAL._decision_check({"entries": []}, judgment)

        self.assertNotIn("producer_bindings", check["resolution"])
        self.assertEqual(
            check["dependencies"],
            [{"path": "data/models", "role": "input", "members": ["a.bin", "b.bin"]}],
        )

    def test_dependency_contract_reuses_supplied_resolved_path_cache(self) -> None:
        cache: dict[str, str] = {}
        scan = {
            "project_root": "/project",
            "resolved_paths": {"entry.md": "/project/entry.md"},
            "directory_memberships": {},
            "entries": [
                {
                    "id": "e001",
                    "path": "entry.md",
                    "candidate_targets": [{"identity": "target.csv"}],
                    "evidence_record": {"rows": []},
                }
            ],
        }
        check = {"entry": "e001", "target": "target.csv", "check": "Provenance"}

        with (
            mock.patch.object(
                INCREMENTAL,
                "_resolved_identity_cache",
                side_effect=AssertionError("cache must not be rebuilt"),
            ),
            mock.patch.object(
                INCREMENTAL,
                "workflow_check",
                return_value=({"status": "ok"}, []),
            ) as workflow,
        ):
            result = INCREMENTAL.current_check_dependency_contract(
                scan, check, cache
            )

        self.assertEqual(len(result), 64)
        self.assertIs(workflow.call_args.args[3], cache)

    def test_rule_compatibility_is_scoped_to_declared_components(self) -> None:
        compatible, blockers = COMPATIBILITY.components_compatible(
            {"integrity": 1}, {"integrity": 2, "entry_provenance": 1}
        )

        self.assertFalse(compatible)
        self.assertEqual(blockers, ["integrity"])
        self.assertEqual(
            COMPATIBILITY.components_compatible(
                {"entry_provenance": 1},
                {"integrity": 2, "entry_provenance": 1},
            ),
            (True, []),
        )

    def test_same_rules_malformed_state_fails_closed_before_identity_work(self) -> None:
        result = INCREMENTAL.compare_prior_state(
            {"validation_rules_version": "current"},
            {"validation_rules_version": "current"},
            POLICY,
            OPERATIONS,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("incorrect", result["detail"])


if __name__ == "__main__":
    unittest.main()
