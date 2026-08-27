from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

INCREMENTAL = importlib.import_module("validation.incremental")
COMPATIBILITY = importlib.import_module("validation.compatibility")
JUDGMENT_RULES = importlib.import_module("validation.judgment_rules")
COLLECTION_SCOPES = importlib.import_module("validation.collection_scopes")
PRODUCER_BINDINGS = importlib.import_module("validation.producer_bindings")


class IncrementalComparisonTests(unittest.TestCase):
    def test_recorded_command_path_content_is_an_outcome_dependency(self) -> None:
        path = "data/model.mat"
        check = {
            "entry": "e001",
            "target": "data/result.csv",
            "check": "Provenance",
            "dependencies": [
                {"path": path, "role": "recorded-command-path"}
            ],
        }
        original = COMPATIBILITY.input_dependencies_for_check(
            {"files": {path: {"size": 4, "sha256": "a" * 64}}}, check
        )
        changed = COMPATIBILITY.input_dependencies_for_check(
            {"files": {path: {"size": 4, "sha256": "b" * 64}}}, check
        )

        self.assertEqual(len(original), 1)
        self.assertEqual(original[0]["kind"], "exact-material")
        self.assertEqual(
            original[0]["relationship"], "recorded-command-path"
        )
        self.assertNotEqual(
            original[0]["content_identity"], changed[0]["content_identity"]
        )

    def test_presented_candidate_uses_its_section_instead_of_the_whole_entry(
        self,
    ) -> None:
        target = "docs/log/entries/e001/images/result.png"
        scan = {
            "entries": [
                {
                    "id": "e001",
                    "path": "docs/log/entries/e001/e001.md",
                    "sections": [
                        {
                            "section": "Results",
                            "semantic_identity": "a" * 64,
                            "content_identity": "b" * 64,
                            "line": 10,
                            "end_line": 20,
                        }
                    ],
                    "evidence_record": {"rows": []},
                    "candidate_targets": [
                        {
                            "identity": target,
                            "kind": "figure",
                            "presented": True,
                            "sections": ["Results"],
                            "occurrences": [{"line": 15, "label": "Result"}],
                        }
                    ],
                }
            ]
        }
        check = {
            "entry": "e001",
            "target": target,
            "check": "Provenance",
            "dependencies": [
                {"path": "docs/log/entries/e001/e001.md", "role": "entry"},
                {"path": target, "role": "target"},
            ],
        }

        dependencies = COMPATIBILITY.input_dependencies_for_check(scan, check)

        self.assertNotIn("entry", {item["kind"] for item in dependencies})
        self.assertEqual(
            {item["relationship"] for item in dependencies},
            {"owning-section", "presented-target", "target"},
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

    def test_producer_binding_projection_reuses_supplied_identity_cache(
        self,
    ) -> None:
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
            "dependencies": [],
            "resolution": {"producer_invocation": invocation},
        }
        identities = {"/project/data/result.csv": "data/result.csv"}

        with mock.patch.object(
            PRODUCER_BINDINGS,
            "resolved_identity_cache",
            side_effect=AssertionError("cache must not be rebuilt"),
        ):
            bindings = COMPATIBILITY.producer_bindings_for_check(
                scan, check, identities
            )

        self.assertEqual(bindings[0]["invocation_identity"], invocation)

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

    def test_judgment_rule_compatibility_uses_its_declared_family(self) -> None:
        self.assertTrue(
            JUDGMENT_RULES.compatible(
                {
                    "kind": "review-decision",
                    "rule_dependencies": {"semantic_review": 1},
                }
            )
        )
        self.assertTrue(
            JUDGMENT_RULES.compatible(
                {
                    "kind": "review-decision",
                    "rule_dependencies": {
                        "semantic_review": 1,
                        "orphan_subtree": 1,
                    },
                }
            )
        )
        self.assertFalse(
            JUDGMENT_RULES.compatible(
                {
                    "kind": "review-decision",
                    "rule_dependencies": {"semantic_review": 2},
                }
            )
        )
        self.assertFalse(
            JUDGMENT_RULES.compatible(
                {
                    "kind": "orphan-disposition",
                    "rule_dependencies": {"orphan_graph": 1},
                }
            )
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

    def test_component_change_reopens_only_declaring_outcome(self) -> None:
        identity = {
            "size": 2,
            "mtime_ns": 1,
            "ctime_ns": 1,
            "sha256": "a" * 64,
        }
        scan = {
            "component_versions": {
                "integrity": 1,
                "mechanical_producer": 3,
            },
            "entries": [],
            "resolved_paths": {"data/result.csv": "/project/data/result.csv"},
            "files": {"data/result.csv": identity},
            "directory_memberships": {},
        }
        outcomes = []
        for check, rules in (
            ("Integrity", {"integrity": 1}),
            ("Provenance", {"mechanical_producer": 2}),
        ):
            outcome = {
                "entry": "e001",
                "target": "data/result.csv",
                "check": check,
                "result": "2026-08-15",
                "dependencies": [
                    {
                        "path": "data/result.csv",
                        "role": "target",
                        "identity": identity,
                    }
                ],
                "rule_dependencies": rules,
            }
            outcome["input_dependencies"] = (
                COMPATIBILITY.input_dependencies_for_check(scan, outcome)
            )
            outcome["compatibility_identity"] = "b" * 64
            outcomes.append(outcome)

        with mock.patch.object(
            INCREMENTAL, "producer_bindings_for_check", return_value=[]
        ):
            compared = INCREMENTAL.compare_prior_record(
                scan,
                {"outcomes": outcomes},
                INCREMENTAL.IncrementalOperations(
                    dependency_snapshot=lambda *_args: identity,
                    orphan_fingerprints=INCREMENTAL.orphan_item_fingerprints,
                ),
            )

        self.assertEqual(compared["reusable_checks"], 1)
        self.assertEqual(compared["rerun_checks"], 1)
        self.assertEqual(
            [row["check"] for row in compared["checks"] if row["status"] == "rerun"],
            ["Provenance"],
        )

    def test_compact_directory_outcome_reopens_for_membership_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "collection"
            retained = root / "retained-run"
            retained.mkdir(parents=True)
            (retained / "a.csv").write_text("a\n", encoding="utf-8")
            collection = "output/collection"
            explicit_identity = {
                "members": ["retained-run/a.csv"],
                "size": 2,
                "mtime_ns": 1,
                "ctime_ns": 1,
                "sha256": "a" * 64,
            }
            choice = COLLECTION_SCOPES.compact_directory_choices(root)[0]
            dependency = {
                "path": collection,
                "role": "input",
                "identity": explicit_identity,
                COLLECTION_SCOPES.COLLECTION_DIRECTORY_SELECTION_KEY: choice[
                    "selector"
                ],
            }
            scan = {
                "component_versions": {"entry_provenance": 2},
                "entries": [],
                "resolved_paths": {collection: root.as_posix()},
                "files": {},
                "directory_memberships": {},
            }
            outcome = {
                "entry": "e001",
                "target": "result.csv",
                "check": "Provenance",
                "result": "2026-08-26",
                "dependencies": [dependency],
                "rule_dependencies": {"entry_provenance": 2},
            }
            outcome["input_dependencies"] = (
                COMPATIBILITY.input_dependencies_for_check(scan, outcome)
            )
            outcome["compatibility_identity"] = "b" * 64
            operations = INCREMENTAL.IncrementalOperations(
                dependency_snapshot=lambda *_args: explicit_identity,
                orphan_fingerprints=INCREMENTAL.orphan_item_fingerprints,
            )
            with mock.patch.object(
                INCREMENTAL, "producer_bindings_for_check", return_value=[]
            ):
                unchanged = INCREMENTAL.compare_prior_record(
                    scan, {"outcomes": [outcome]}, operations
                )
                (retained / "b.csv").write_text("b\n", encoding="utf-8")
                changed = INCREMENTAL.compare_prior_record(
                    scan, {"outcomes": [outcome]}, operations
                )

            self.assertEqual(unchanged["reusable_checks"], 1)
            self.assertEqual(changed["rerun_checks"], 1)


if __name__ == "__main__":
    unittest.main()
