from __future__ import annotations

import copy
import importlib
import tempfile
import unittest
from pathlib import Path

import research_log_validation_test_support  # noqa: F401

REUSE = importlib.import_module("validation.review_reuse")
COLLECTION_SCOPES = importlib.import_module("validation.collection_scopes")
COMPATIBILITY = importlib.import_module("validation.compatibility")


def target_fixture(kind: str = "semantic_fallback") -> tuple[dict, ...]:
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
                "entry": "e001",
                "line": 12,
                "command": f"python {producer} --output {target}",
                "normalized_command": f"python {producer} --output {target}",
                "path_arguments": [
                    {
                        "path": target,
                        "role_hint": "output",
                        "exists": True,
                    }
                ],
                "coverage_identity": target,
                "coverage_kind": "exact-target",
                "target_member": None,
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


def legacy_review_inputs(
    scan: dict, adjudication: dict, queue: dict
) -> list[dict]:
    row = adjudication["entries"][0]["targets"][0]
    return COMPATIBILITY.input_dependencies_for_check(
        scan,
        {
            **row,
            "entry": queue["entry"],
            "target": queue["identity"],
            "check": "Provenance",
        },
    )


def review_decision(
    scan: dict,
    adjudication: dict,
    queue: dict,
    template: dict,
    decision: object,
) -> dict:
    return {
        "identity": "old-packet-layout",
        "kind": "review-decision",
        "subject": {
            "kind": template["kind"],
            "entry": template["entry"],
            "identity": template["identity"],
            **({"material": template["material"]} if "material" in template else {}),
        },
        "decision": decision,
        "rule_dependencies": REUSE.SEMANTIC_REVIEW_RULES,
        "input_dependencies": REUSE.review_judgment_inputs(
            scan, adjudication, queue, template, decision
        ),
    }


class ReviewReuseTests(unittest.TestCase):
    def test_indexed_reuse_does_not_rescan_unrelated_judgments(self) -> None:
        class TrackingJudgment(dict):
            get_calls = 0

            def get(self, key: object, default: object = None) -> object:
                self.get_calls += 1
                return super().get(key, default)

        scan, adjudication, queue, template = target_fixture()
        judgment = review_decision(scan, adjudication, queue, template, "invocation-1")
        unrelated_value = copy.deepcopy(judgment)
        unrelated_value["subject"]["identity"] = "docs/mini/unrelated.csv"
        unrelated = TrackingJudgment(unrelated_value)
        indexed = REUSE.index_review_judgments([judgment, unrelated])
        unrelated.get_calls = 0

        reused = REUSE.reusable_review_answer(
            scan, adjudication, queue, template, indexed
        )

        self.assertEqual(reused[0], "invocation-1")
        self.assertEqual(unrelated.get_calls, 0)

    def test_reuses_subject_decision_across_layout_only(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        judgment = review_decision(scan, adjudication, queue, template, "invocation-1")

        reused = REUSE.reusable_review_answer(
            scan, adjudication, queue, template, [judgment]
        )
        self.assertEqual(reused[0], "invocation-1")

        empty_dependencies = {**judgment, "input_dependencies": []}
        self.assertIsNone(
            REUSE.reusable_review_answer(
                scan, adjudication, queue, template, [empty_dependencies]
            )
        )

        conflict = {**judgment, "decision": "fail:workflow"}
        self.assertIsNone(
            REUSE.reusable_review_answer(
                scan, adjudication, queue, template, [judgment, conflict]
            )
        )

        changed = copy.deepcopy(scan)
        changed["files"][template["identity"]]["sha256"] = "9" * 64
        self.assertIsNone(
            REUSE.reusable_review_answer(
                changed, adjudication, queue, template, [judgment]
            )
        )

    def test_producer_selection_reuses_legacy_broad_judgment(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        target = template["identity"]
        scan["entries"][0].update(
            {
                "path": "docs/mini/entries/e001/e001.md",
                "sections": [
                    {
                        "section": "Result",
                        "semantic_identity": "c" * 64,
                        "content_identity": "d" * 64,
                        "line": 10,
                        "end_line": 20,
                    }
                ],
                "candidate_targets": [
                    {
                        "identity": target,
                        "kind": "file",
                        "presented": True,
                        "sections": ["Result"],
                        "occurrences": [{"line": 15}],
                    }
                ],
            }
        )
        judgment = review_decision(
            scan, adjudication, queue, template, "invocation-1"
        )
        judgment["input_dependencies"] = legacy_review_inputs(
            scan, adjudication, queue
        )
        self.assertIn(
            "experimental-section",
            {item["kind"] for item in judgment["input_dependencies"]},
        )
        retained = copy.deepcopy(judgment)

        changed_section = copy.deepcopy(scan)
        changed_section["entries"][0]["sections"][0]["content_identity"] = "e" * 64
        reused = REUSE.reusable_review_answer(
            changed_section, adjudication, queue, template, [judgment]
        )

        self.assertEqual(reused[0], "invocation-1")
        self.assertEqual(judgment, retained)
        narrow = REUSE.review_judgment_inputs(
            changed_section,
            adjudication,
            queue,
            template,
            "invocation-1",
        )
        self.assertEqual(
            {item["relationship"] for item in narrow},
            {"producer", "producer-selection", "target"},
        )
        self.assertNotIn(
            "experimental-section", {item["kind"] for item in narrow}
        )
        self.assertNotIn("presented-item", {item["kind"] for item in narrow})

    def test_producer_selection_reopens_for_relevant_changes(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        judgment = review_decision(
            scan, adjudication, queue, template, "invocation-1"
        )

        changed_producer = copy.deepcopy(scan)
        producer = queue["producer_candidates"][0]["material"]
        changed_producer["files"][producer]["sha256"] = "9" * 64
        self.assertIsNone(
            REUSE.reusable_review_answer(
                changed_producer, adjudication, queue, template, [judgment]
            )
        )

        changed_candidate = copy.deepcopy(queue)
        changed_candidate["producer_candidates"][0]["invocation"] = "invocation-2"
        changed_template = copy.deepcopy(template)
        changed_template["allowed_decisions"] = [
            "invocation-2",
            "fail:workflow",
            "needs_context",
        ]
        self.assertIsNone(
            REUSE.reusable_review_answer(
                scan,
                adjudication,
                changed_candidate,
                changed_template,
                [judgment],
            )
        )

    def test_reuses_only_exact_current_collection_members(self) -> None:
        scan, adjudication, queue, template = target_fixture("collection_scope")
        collection = "output/collection"
        queue["collections"] = [collection]
        adjudication["entries"][0]["targets"][0]["dependencies"].append(
            {"path": collection, "role": "input"}
        )
        scan["directory_memberships"][collection] = {
            "members": 1,
            "sha256": "c" * 64,
        }
        scan["files"][f"{collection}/a.csv"] = {
            "size": 3,
            "sha256": "d" * 64,
            "mtime_ns": 1,
            "ctime_ns": 2,
        }
        decision = {"members": {collection: ["a.csv"]}}
        template["allowed_decisions"] = [
            {"members": {collection: ["<relative/member>"]}},
            "fail",
        ]
        judgment = review_decision(scan, adjudication, queue, template, decision)

        self.assertEqual(
            REUSE.reusable_review_answer(
                scan, adjudication, queue, template, [judgment]
            )[0],
            decision,
        )

        changed = copy.deepcopy(scan)
        changed["files"][f"{collection}/a.csv"]["sha256"] = "e" * 64
        self.assertIsNone(
            REUSE.reusable_review_answer(
                changed, adjudication, queue, template, [judgment]
            )
        )

    def test_compact_directory_judgment_reopens_when_membership_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection_root = Path(directory) / "collection"
            retained = collection_root / "retained-run"
            retained.mkdir(parents=True)
            (retained / "a.csv").write_text("a\n", encoding="utf-8")
            collection = "output/collection"
            member = f"{collection}/retained-run/a.csv"
            scan, adjudication, queue, template = target_fixture("collection_scope")
            scan["resolved_paths"] = {collection: collection_root.as_posix()}
            scan["files"][member] = {
                "size": 2,
                "sha256": "d" * 64,
                "mtime_ns": 1,
                "ctime_ns": 2,
            }
            queue["collections"] = [collection]
            adjudication["entries"][0]["targets"][0]["dependencies"].append(
                {"path": collection, "role": "input"}
            )
            decision = {"members": {collection: ["retained-run/a.csv"]}}
            choice = COLLECTION_SCOPES.compact_directory_choices(collection_root)[0]
            template[COLLECTION_SCOPES.COLLECTION_DIRECTORY_SELECTIONS_KEY] = {
                collection: choice["selector"]
            }
            template["allowed_decisions"] = [decision, "fail"]
            judgment = review_decision(
                scan, adjudication, queue, template, decision
            )

            current_template = copy.deepcopy(template)
            current_template.pop(
                COLLECTION_SCOPES.COLLECTION_DIRECTORY_SELECTIONS_KEY
            )
            self.assertEqual(
                REUSE.reusable_review_answer(
                    scan, adjudication, queue, current_template, [judgment]
                )[0],
                decision,
            )

            (retained / "b.csv").write_text("b\n", encoding="utf-8")
            self.assertIsNone(
                REUSE.reusable_review_answer(
                    scan, adjudication, queue, current_template, [judgment]
                )
            )

    def test_legacy_collection_judgment_keeps_explicit_member_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection_root = Path(directory) / "collection"
            retained = collection_root / "retained-run"
            retained.mkdir(parents=True)
            (retained / "a.csv").write_text("a\n", encoding="utf-8")
            collection = "output/collection"
            member = f"{collection}/retained-run/a.csv"
            scan, adjudication, queue, template = target_fixture("collection_scope")
            scan["resolved_paths"] = {collection: collection_root.as_posix()}
            scan["files"][member] = {
                "size": 2,
                "sha256": "d" * 64,
                "mtime_ns": 1,
                "ctime_ns": 2,
            }
            queue["collections"] = [collection]
            adjudication["entries"][0]["targets"][0]["dependencies"].append(
                {"path": collection, "role": "input"}
            )
            decision = {"members": {collection: ["retained-run/a.csv"]}}
            template["allowed_decisions"] = [decision, "fail"]
            judgment = review_decision(
                scan, adjudication, queue, template, decision
            )

            (retained / "b.csv").write_text("b\n", encoding="utf-8")
            self.assertEqual(
                REUSE.reusable_review_answer(
                    scan, adjudication, queue, template, [judgment]
                )[0],
                decision,
            )

    def test_reuse_misses_have_stable_diagnostic_reasons(self) -> None:
        scan, adjudication, queue, template = target_fixture()
        judgment = review_decision(scan, adjudication, queue, template, "invocation-1")

        cases = []
        cases.append(([], "subject_not_found"))
        changed_rules = {
            **judgment,
            "rule_dependencies": {"semantic-review": "old"},
        }
        cases.append(([changed_rules], "rule_dependency_changed"))
        incomplete = {**judgment, "input_dependencies": []}
        cases.append(([incomplete], "incomplete_legacy_input_dependencies"))
        disallowed = {**judgment, "decision": "removed-choice"}
        cases.append(([disallowed], "candidate_or_allowed_answer_changed"))
        changed_content = copy.deepcopy(judgment)
        changed_content["input_dependencies"][0]["content_identity"] = "9" * 64
        cases.append(([changed_content], "relevant_input_content_changed"))
        moved_source = copy.deepcopy(judgment)
        for dependency in moved_source["input_dependencies"]:
            dependency["semantic_identity"] += ":old-locator"
        cases.append(([moved_source], "source_locator_changed"))

        for judgments, expected in cases:
            with self.subTest(reason=expected):
                diagnostics = {}
                reused = REUSE.reusable_review_answer_diagnostics(
                    REUSE.ReuseAnswerRequest(
                        scan,
                        adjudication,
                        queue,
                        template,
                        judgments,
                    ),
                    diagnostics,
                )
                self.assertIsNone(reused)
                self.assertEqual(diagnostics["misses_by_reason"], {expected: 1})

        conflict = {**judgment, "decision": "fail:workflow"}
        diagnostics = {}
        self.assertIsNone(
            REUSE.reusable_review_answer_diagnostics(
                REUSE.ReuseAnswerRequest(
                    scan,
                    adjudication,
                    queue,
                    template,
                    [judgment, conflict],
                ),
                diagnostics,
            )
        )
        self.assertEqual(
            diagnostics["misses_by_reason"],
            {"conflicting_compatible_answers": 1},
        )


if __name__ == "__main__":
    unittest.main()
