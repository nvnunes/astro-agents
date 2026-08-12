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

    def test_rules_change_invalidates_every_prior_outcome_before_identity_work(
        self,
    ) -> None:
        result = INCREMENTAL.compare_prior_state(
            {"validation_rules_version": "current"},
            {
                "validation_rules_version": "prior",
                "completed_checks": [{}, {}, {}],
            },
            POLICY,
            OPERATIONS,
        )

        self.assertEqual(
            result,
            {"status": "rules-changed", "reusable_checks": 0, "rerun_checks": 3},
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
