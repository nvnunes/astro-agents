from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

CONTRACTS = importlib.import_module("validation.contracts")
GRAPH_STORE = importlib.import_module("validation.graph_store")
SCAN = importlib.import_module("validation.scan")


class ScanAssemblyTests(unittest.TestCase):
    def test_local_snapshot_identity_excludes_repository_facts(self) -> None:
        record = {
            "summary": "docs/example.md",
            "entry_order": [],
            "reconciliation": {},
            "summary_items": [],
            "entries": [],
            "evidence_records": {"summary": {"errors": []}, "entry_folders": []},
            "files": {},
            "directory_memberships": {},
            "script_inventory": [],
            "script_dependency_graph": {},
            "repository_dependencies": [],
            "resolved_paths": {},
            "repository_material_owners": {},
            "repository_cross_log_sources": {},
            "repository_slices": {},
            "repository_scope": {
                "refresh_summary": "docs/example.md",
                "cross_log_complete": True,
            },
        }
        changed = copy.deepcopy(record)
        changed["repository_scope"]["cross_log_complete"] = False

        self.assertEqual(
            SCAN.local_snapshot_identity(record),
            SCAN.local_snapshot_identity(changed),
        )
        self.assertNotEqual(
            SCAN.input_fingerprint(record), SCAN.input_fingerprint(changed)
        )

    def test_scan_owner_rejects_an_invalid_mode_at_its_public_boundary(self) -> None:
        request = SCAN.ScanRequest(
            Path("missing.md"),
            1,
            None,
            None,
            "test-rules",
            "invalid",
            mock.Mock(),
        )

        with self.assertRaisesRegex(CONTRACTS.ValidationToolError, "validation mode"):
            SCAN.scan_log(request)

    def test_scan_owner_validates_runtime_inputs(self) -> None:
        repository = GRAPH_STORE.empty_repository_view("test-rules")

        metrics = SCAN.validated_repository_view(repository, "test-rules")

        self.assertEqual(metrics, {"status": "unchanged", "edges": 0})
        self.assertEqual(SCAN.validated_jobs(3), 3)
        with self.assertRaises(CONTRACTS.ValidationToolError):
            SCAN.validated_jobs(True)

    def test_typed_assembly_serializes_the_exact_scan_contract(self) -> None:
        repository = GRAPH_STORE.empty_repository_view("test-rules")
        documents = SCAN.ScanDocumentFacts(
            entry_order=[],
            reconciliation={"missing_entries": [], "unlisted_entries": []},
            summary_items=[],
            entries=[],
            summary_evidence={
                "path": None,
                "identity": None,
                "expected_path": "/project/docs/example/evidence.csv",
                "rows": [],
                "errors": [],
            },
            entry_evidence_records=[],
            bibtex_path=None,
            bibtex_keys=[],
        )
        materials = SCAN.ScanMaterialFacts(
            files={},
            directory_memberships={},
            resolved_paths={},
            mechanical_checks={},
            script_inventory=[],
            script_dependency_graph={},
        )
        record = SCAN.ScanAssembly(
            schema_version=13,
            rules_version="test-rules",
            mode="standard",
            summary="docs/example.md",
            log_root="docs/example",
            project_root="/project",
            documents=documents,
            materials=materials,
            repository=SCAN.ScanRepositoryFacts([], repository),
            durable_record_identity="b" * 64,
        ).record()
        record["input_fingerprint"] = "a" * 64

        decoded = CONTRACTS.decode_scan_record(record, schema_version=13)

        self.assertEqual(decoded["entry_order"], [])
        self.assertEqual(decoded["repository_view_identity"], repository["identity"])
        self.assertEqual(decoded["durable_record_identity"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
