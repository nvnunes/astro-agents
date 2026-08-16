from __future__ import annotations

import copy
import importlib
import unittest

import research_log_validation_test_support  # noqa: F401
from validation.compatibility import (
    input_dependencies_for_check,
    orphan_input_dependencies,
    orphan_rule_dependencies,
    rule_dependencies_for_check,
)

REUSE = importlib.import_module("validation.migration_review_reuse")
EXCHANGE = importlib.import_module("validation.review_exchange")


def target_fixture(kind: str = "semantic_fallback") -> tuple[dict, dict, dict, dict]:
    target = "docs/mini/entries/e001/data/result.csv"
    producer = "docs/mini/entries/e001/scripts/produce.py"
    scan = {
        "summary": "docs/mini.md",
        "files": {
            target: {"size": 4, "sha256": "a" * 64},
            producer: {"size": 8, "sha256": "b" * 64},
        },
        "directory_memberships": {},
        "entries": [{"id": "e001", "sections": [], "orphan_inventory": []}],
    }
    queue = {
        "kind": kind,
        "entry": "e001",
        "identity": target,
        "producer_candidates": [
            {
                "material": producer,
                "invocation": "invocation-1",
                "coverage_identity": target,
                "coverage_kind": "exact-target",
            }
        ],
    }
    row = {
        "target": target,
        "dependencies": [
            {"path": target, "role": "target"},
            {"path": producer, "role": "producer"},
        ],
    }
    adjudication = {
        "summary": [],
        "entries": [{"id": "e001", "targets": [row]}],
        "review_queue": [queue],
    }
    template = {
        "id": "current-layout",
        "kind": kind,
        "entry": "e001",
        "identity": target,
        "allowed_decisions": ["invocation-1", "fail:workflow", "needs_context"],
    }
    return scan, adjudication, queue, template


def completed_judgment(scan: dict, adjudication: dict, **basis: object) -> dict:
    row = adjudication["entries"][0]["targets"][0]
    check = {
        **row,
        "entry": "e001",
        "target": row["target"],
        "check": "Provenance",
    }
    return {
        "kind": "completed-check",
        "result": "2026-08-15",
        "subject": {
            "check": "Provenance",
            "entry": "e001",
            "target": row["target"],
        },
        "basis": basis,
        "rule_dependencies": rule_dependencies_for_check(check),
        "input_dependencies": input_dependencies_for_check(scan, check),
    }


class MigrationReviewReuseTests(unittest.TestCase):
    def test_reuses_exact_summary_provenance_and_rejects_changed_passage(self) -> None:
        scan = {
            "summary": "docs/mini.md",
            "summary_items": [{"selector": "4.2%", "text": "summary claim"}],
            "files": {"docs/mini/evidence.csv": {"sha256": "a" * 64}},
            "directory_memberships": {},
            "entries": [
                {
                    "id": "e001",
                    "path": "docs/mini/entries/e001/e001.md",
                    "sections": [
                        {
                            "section": "Results",
                            "semantic_identity": "results",
                            "content_identity": "passage-v1",
                            "line": 20,
                            "end_line": 24,
                        }
                    ],
                    "orphan_inventory": [],
                }
            ],
        }
        queue = {
            "kind": "semantic_provenance",
            "entry": "Summary",
            "identity": "4.2%",
            "candidates": [{"section": "Results", "line": 21, "text": "4.2%"}],
        }
        row = {
            "item": "4.2%",
            "entries": ["e001"],
            "sections": ["Results"],
            "dependencies": [
                {"path": "docs/mini/evidence.csv", "role": "evidence-association"}
            ],
        }
        adjudication = {"summary": [row], "entries": [], "review_queue": [queue]}
        basis = {"entry": "e001", "section": "Results", "lines": "21"}
        check = {
            **row,
            "entry": "Summary",
            "target": "4.2%",
            "check": "Provenance",
            "resolution": {"entry": "e001", "section": "Results"},
        }
        judgment = {
            "kind": "completed-check",
            "result": "2026-08-15",
            "subject": {
                "check": "Provenance",
                "entry": "Summary",
                "target": "4.2%",
            },
            "basis": basis,
            "rule_dependencies": rule_dependencies_for_check(check),
            "input_dependencies": input_dependencies_for_check(scan, check),
        }
        template = {
            "id": "new-packet-layout",
            "kind": "semantic_provenance",
            "entry": "Summary",
            "identity": "4.2%",
            "allowed_decisions": ["pass", "fail", "needs_context"],
        }

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], "pass")
        actions = EXCHANGE.reusable_review_actions(
            scan, adjudication, [judgment]
        )
        self.assertEqual(actions["actions"][0]["decision"], "support")

        changed = copy.deepcopy(scan)
        changed["entries"][0]["sections"][0]["content_identity"] = "passage-v2"
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                changed, adjudication, queue, template, [judgment]
            )
        )

    def test_reuses_only_an_offered_exact_producer_invocation(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        judgment = completed_judgment(
            scan, adjudication, producer_invocation="invocation-1"
        )

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], "invocation-1")

        template["allowed_decisions"] = ["invocation-2", "fail:workflow"]
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [judgment]
            )
        )

        template["allowed_decisions"] = ["invocation-1", "fail:workflow"]
        judgment["rule_dependencies"]["reviewed_producer"] += 1
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [judgment]
            )
        )

    def test_projects_one_exact_upstream_binding_per_material(self) -> None:
        scan, adjudication, queue, template = target_fixture("upstream_producer")
        material = queue["producer_candidates"][0]["material"]
        template["material"] = material
        template["allowed_decisions"] = ["invocation-1", "unresolved"]
        judgment = completed_judgment(
            scan,
            adjudication,
            producer_bindings=[
                {"material": material, "invocation": "invocation-1"}
            ],
        )

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], "invocation-1")

    def test_projects_only_exact_valid_collection_members(self) -> None:
        scan, adjudication, queue, template = target_fixture("collection_scope")
        collection = "output/collection"
        queue["collections"] = [collection]
        adjudication["entries"][0]["targets"][0]["dependencies"].append(
            {"path": collection, "role": "input"}
        )
        scan["directory_memberships"][collection] = {
            "members": 2,
            "sha256": "c" * 64,
        }
        scan["files"][f"{collection}/a.csv"] = {
            "size": 3,
            "sha256": "d" * 64,
            "mtime_ns": 1,
            "ctime_ns": 2,
        }
        scan["files"][f"{collection}/b.csv"] = {
            "size": 5,
            "sha256": "e" * 64,
            "mtime_ns": 3,
            "ctime_ns": 4,
        }
        decision = {"members": {collection: ["a.csv", "b.csv"]}}
        template["allowed_decisions"] = [
            {"members": {collection: ["<relative/member>"]}},
            "fail",
        ]
        inputs = REUSE.review_judgment_inputs(
            scan, adjudication, queue, template, decision
        )
        judgment = completed_judgment(scan, adjudication)
        judgment["input_dependencies"] = inputs

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], decision)

        incremental = copy.deepcopy(scan)
        del incremental["files"][f"{collection}/a.csv"]
        del incremental["files"][f"{collection}/b.csv"]
        incremental["incremental"] = {
            "checks": [
                {
                    "check": "Provenance",
                    "entry": "e001",
                    "target": queue["identity"],
                    "input_dependencies": [
                        item
                        for item in inputs
                        if item["kind"]
                        in {"collection-member", "collection-membership"}
                    ],
                }
            ]
        }
        reused = REUSE.migration_reusable_answer(
            incremental, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], decision)

        incompatible = copy.deepcopy(incremental)
        incompatible["incremental"]["checks"][0]["input_dependencies"][0][
            "content_identity"
        ] = "changed"
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                incompatible, adjudication, queue, template, [judgment]
            )
        )

        broad = copy.deepcopy(judgment)
        broad["input_dependencies"] = [
            item for item in inputs if item["kind"] != "collection-member"
        ]
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [broad]
            )
        )

        changed = copy.deepcopy(scan)
        changed["files"][f"{collection}/a.csv"]["sha256"] = "f" * 64
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                changed, adjudication, queue, template, [judgment]
            )
        )

    def test_projects_compatible_orphan_dispositions_and_exact_note(self) -> None:
        candidate = {"identity": "docs/mini/data/orphan.csv", "kind": "file"}
        note = {"sha256": "1" * 64, "section": "Validation", "text": "retain"}
        scan = {
            "summary": "docs/mini.md",
            "entries": [
                {
                    "id": "e001",
                    "path": "docs/mini/entries/e001/e001.md",
                    "orphan_inventory": [candidate],
                    "validation_notes": [note],
                }
            ],
        }
        queue = {
            "kind": "orphan_candidates",
            "entry": "e001",
            "identity": "Orphaned artifacts, scripts, and references",
            "candidates": [candidate],
            "validation_notes": [note],
        }
        adjudication = {"summary": [], "entries": [], "review_queue": [queue]}
        template = {
            "id": "layout-v2",
            "kind": "orphan_candidate",
            "entry": "e001",
            "identity": candidate["identity"],
            "allowed_decisions": [
                "unresolved",
                "connected",
                f"retain:{note['sha256']}",
            ],
        }
        inputs = orphan_input_dependencies(scan, scan["entries"][0], [candidate])
        retained = {
            "kind": "orphan-disposition",
            "result": "accepted",
            "subject": {"entry": "e001", "identity": candidate["identity"]},
            "basis": f"validation-note:{note['sha256']}",
            "rule_dependencies": orphan_rule_dependencies(),
            "input_dependencies": inputs,
        }

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [retained]
        )
        self.assertEqual(reused[0], f"retain:{note['sha256']}")

        connected = {**retained, "basis": "semantic-connection"}
        self.assertEqual(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [connected]
            )[0],
            "connected",
        )
        unresolved = {**retained, "result": "unresolved", "basis": None}
        self.assertEqual(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [unresolved]
            )[0],
            "unresolved",
        )

        stale = copy.deepcopy(scan)
        stale["entries"][0]["orphan_inventory"][0]["kind"] = "directory"
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                stale, adjudication, queue, template, [retained]
            )
        )

    def test_reuses_subject_review_decision_across_layout_only(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        inputs = REUSE.review_judgment_inputs(
            scan, adjudication, queue, template, "invocation-1"
        )
        judgment = {
            "identity": "old-packet-layout",
            "kind": "review-decision",
            "subject": {
                "kind": "semantic_fallback",
                "entry": "e001",
                "identity": template["identity"],
            },
            "decision": "invocation-1",
            "rule_dependencies": REUSE.SEMANTIC_REVIEW_RULES,
            "input_dependencies": inputs,
        }

        reused = REUSE.migration_reusable_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], "invocation-1")

        empty_dependencies = {**judgment, "input_dependencies": []}
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [empty_dependencies]
            )
        )

        conflict = {**judgment, "decision": "fail:workflow"}
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                scan, adjudication, queue, template, [judgment, conflict]
            )
        )

        changed = copy.deepcopy(scan)
        changed["files"][template["identity"]]["sha256"] = "9" * 64
        self.assertIsNone(
            REUSE.migration_reusable_answer(
                changed, adjudication, queue, template, [judgment]
            )
        )


if __name__ == "__main__":
    unittest.main()
