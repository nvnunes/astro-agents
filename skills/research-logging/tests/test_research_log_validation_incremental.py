from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

INCREMENTAL = importlib.import_module("validation.incremental")
COMPATIBILITY = importlib.import_module("validation.compatibility")


class IncrementalComparisonTests(unittest.TestCase):
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
                {"section": "Results", "command": "python unrelated.py", "line": 4},
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
                {"path": "/project/data/result.csv", "role_hint": "output"}
            ],
        }

        def binding(commands: list[dict[str, Any]]) -> dict[str, Any]:
            invocation = COMPATIBILITY.invocation_identities("e001", commands)[0]
            scan = {
                "entries": [{"id": "e001", "commands": commands}],
                "resolved_paths": {"data/result.csv": "/project/data/result.csv"},
            }
            check = {
                "entry": "e001",
                "target": "data/result.csv",
                "dependencies": [],
                "resolution": {"producer_invocation": invocation},
            }
            return COMPATIBILITY.producer_bindings_for_check(scan, check)[0]

        self.assertEqual(binding([command])["duplicate_count"], 1)
        self.assertEqual(
            binding([command, {**command, "line": 30}])["duplicate_count"], 2
        )

    def test_orphan_fingerprints_reuse_supplied_path_cache(self) -> None:
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
                    "data_tokens": [{"name": "output", "path": artifact.as_posix()}],
                }
            ],
            "data_index": {"rows": []},
            "orphan_inventory": [
                {"identity": "log/data/output.csv", "kind": "artifact"}
            ],
            "validation_notes": [],
        }
        with mock.patch.object(
            INCREMENTAL,
            "_resolved_identity_cache",
            side_effect=AssertionError("cache must not be rebuilt"),
        ):
            fingerprints = INCREMENTAL.orphan_item_fingerprints(entry, scan, {})
        self.assertEqual(set(fingerprints), {"log/data/output.csv"})

    def test_rule_compatibility_is_scoped_to_declared_components(self) -> None:
        self.assertEqual(
            COMPATIBILITY.components_compatible(
                {"entry_provenance": 1},
                {"integrity": 2, "entry_provenance": 1},
            ),
            (True, []),
        )
        self.assertEqual(
            COMPATIBILITY.components_compatible(
                {"integrity": 1}, {"integrity": 2, "entry_provenance": 1}
            ),
            (False, ["integrity"]),
        )

    def test_native_outcome_reuse_is_dependency_scoped(self) -> None:
        identity = {
            "size": 2,
            "mtime_ns": 1,
            "ctime_ns": 1,
            "sha256": "a" * 64,
        }
        scan = {
            "component_versions": {"integrity": 1},
            "entries": [],
            "resolved_paths": {"data/result.csv": "/project/data/result.csv"},
            "files": {"data/result.csv": identity},
            "directory_memberships": {},
        }
        base = {
            "entry": "e001",
            "target": "data/result.csv",
            "check": "Integrity",
            "result": "2026-08-15",
            "dependencies": [
                {"path": "data/result.csv", "role": "target", "identity": identity}
            ],
            "rule_dependencies": {"integrity": 1},
        }
        base["input_dependencies"] = COMPATIBILITY.input_dependencies_for_check(
            scan, base
        )
        base["compatibility_identity"] = "b" * 64
        record = {"outcomes": [base]}

        with mock.patch.object(
            INCREMENTAL, "producer_bindings_for_check", return_value=[]
        ):
            unchanged = INCREMENTAL.compare_prior_record(
                scan,
                record,
                INCREMENTAL.IncrementalOperations(
                    dependency_snapshot=lambda *_args: identity,
                    orphan_fingerprints=INCREMENTAL.orphan_item_fingerprints,
                ),
            )
            changed = INCREMENTAL.compare_prior_record(
                scan,
                record,
                INCREMENTAL.IncrementalOperations(
                    dependency_snapshot=lambda *_args: {**identity, "sha256": "c" * 64},
                    orphan_fingerprints=INCREMENTAL.orphan_item_fingerprints,
                ),
            )
        self.assertEqual(unchanged["reusable_checks"], 1)
        self.assertEqual(changed["rerun_checks"], 1)


if __name__ == "__main__":
    unittest.main()
