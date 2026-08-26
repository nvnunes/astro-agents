from __future__ import annotations

import copy
import importlib
import unittest

import research_log_validation_test_support  # noqa: F401

REUSE = importlib.import_module("validation.review_reuse")


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
