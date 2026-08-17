from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from research_log_validation_test_support import make_log, write

CONTROLLER = importlib.import_module("validation.controller")
ORPHANS = importlib.import_module("validation.orphan_rules")
EXCHANGE = importlib.import_module("validation.review_exchange")
TARGET = importlib.import_module("validation.target_records")


def _candidate(identity: str) -> dict[str, str]:
    return {"identity": identity, "kind": "artifact"}


def _fill_decisions(path: Path) -> None:
    template = json.loads(path.read_text(encoding="utf-8"))
    for item in template["items"]:
        allowed = item["allowed_decisions"]
        if item["kind"] == "semantic_provenance":
            decision: object = "pass"
        elif item["kind"] == "semantic_fallback":
            decision = next(
                value
                for value in allowed
                if value not in {"fail:workflow", "needs_context"}
            )
        elif item["kind"] == "mechanical_failure":
            decision = "keep"
        elif item["kind"] == "orphan_subtree":
            decision = next(
                value
                for value in allowed
                if isinstance(value, dict) and value.get("disposition") == "retained"
            )
        elif item["kind"] == "orphan_candidate":
            decision = next(
                value for value in allowed if str(value).startswith("retain:")
            )
        elif item["kind"] == "collection_scope" and item["context_level"] == 1:
            collections = next(
                value["members"] for value in allowed if isinstance(value, dict)
            )
            decision = {
                "members": {collection: ["a.txt"] for collection in collections}
            }
        else:
            decision = "needs_context"
        item["decision"] = decision
        item["rationale"] = "Focused subtree fixture decision."
    write(path, json.dumps(template, indent=2) + "\n")


def _complete(summary: Path) -> dict:
    result = CONTROLLER.validate(
        CONTROLLER.ValidationRequest(
            summary, result_date="2026-08-16", jobs=1, publish=True
        )
    )
    while result["status"] == "review_required":
        decision_file = Path(result["decision_file"])
        _fill_decisions(decision_file)
        result = CONTROLLER.validate(
            CONTROLLER.ValidationRequest(
                summary, decision_file=decision_file, jobs=1, publish=True
            )
        )
    return result


class OrphanSubtreeTests(unittest.TestCase):
    def test_only_rule_inherited_items_supersede_exact_judgments(self) -> None:
        inherited = "docs/log/entries/e001/data/inherited.csv"
        exact = "docs/log/entries/e001/data/exception.csv"
        adjudication = {
            "entries": [
                {
                    "id": "e001",
                    "orphan_items": [
                        {
                            "identity": inherited,
                            "decision": "accepted",
                            "basis": ORPHANS.subtree_basis(
                                "docs/log/entries/e001/data", "semantic-connection"
                            ),
                        },
                        {
                            "identity": exact,
                            "decision": "unresolved",
                            "basis": "-",
                        },
                        {
                            "identity": "docs/log/entries/e001/data/graph.csv",
                            "decision": "accepted",
                            "basis": "graph",
                        },
                    ],
                }
            ]
        }

        subjects = CONTROLLER._superseded_orphan_subjects(adjudication)

        self.assertEqual(
            subjects,
            [{"kind": "orphan_candidate", "entry": "e001", "identity": inherited}],
        )

    def test_material_root_starts_with_subfolders_and_exact_loose_files(
        self,
    ) -> None:
        root = "docs/log/entries/e001/data"
        candidates = [
            _candidate(f"{root}/loose.csv"),
            _candidate(f"{root}/set"),
            _candidate(f"{root}/set/a.csv"),
            _candidate(f"{root}/set/b.csv"),
            _candidate("<input_csv>"),
        ]

        questions, exact = ORPHANS.refined_questions(candidates, [])
        self.assertEqual([question["root"] for question in questions], [f"{root}/set"])
        self.assertEqual(
            [candidate["identity"] for candidate in exact],
            ["<input_csv>", f"{root}/loose.csv"],
        )

        questions, exact = ORPHANS.refined_questions(candidates, [f"{root}/set"])
        self.assertEqual(questions, [])
        self.assertEqual(
            [candidate["identity"] for candidate in exact],
            [
                "<input_csv>",
                f"{root}/loose.csv",
                f"{root}/set",
                f"{root}/set/a.csv",
                f"{root}/set/b.csv",
            ],
        )

    def test_parent_rule_is_prospective_and_nested_rule_is_more_specific(self) -> None:
        entry = "e001"
        root = "docs/log/entries/e001/data/group"
        nested = f"{root}/set"
        candidates = [
            _candidate(f"{root}/loose.csv"),
            _candidate(f"{nested}/new.csv"),
            _candidate(f"{nested}/other.csv"),
        ]
        queue_item = {
            "entry": entry,
            "kind": "orphan_candidates",
            "identity": "Orphans",
            "candidates": candidates,
            "validation_notes": [],
        }
        scan = {
            "schema_version": 1,
            "validation_rules_version": "rules-v1",
            "entries": [
                {
                    "id": entry,
                    "orphan_inventory": candidates,
                    "validation_notes": [],
                }
            ],
        }
        adjudication = {
            "schema_version": 1,
            "review_queue": [queue_item],
        }

        def judgment(subject: dict[str, str], decision: dict[str, str]) -> dict:
            return {
                "identity": json.dumps(subject, sort_keys=True),
                "kind": "review-decision",
                "result": decision["disposition"],
                "decision": decision,
                "decision_date": "2026-08-16",
                "subject": subject,
                "rule_dependencies": ORPHANS.SUBTREE_RULE_DEPENDENCIES,
                "input_dependencies": [],
                "rationale": "One reviewed lifecycle.",
                "rationale_provenance": "recorded",
                "provenance": "native-reviewed",
            }

        parent = judgment(
            ORPHANS.subtree_subject(entry, "data", root),
            {"action": "classify-subtree", "disposition": "connected"},
        )
        child = judgment(
            ORPHANS.subtree_subject(entry, "data", nested),
            {"action": "classify-subtree", "disposition": "unresolved"},
        )
        exact_template = EXCHANGE._candidate_reuse_template(
            scan, adjudication, queue_item, candidates[1]
        )
        exact = {
            **judgment(
                {
                    "kind": "orphan_candidate",
                    "entry": entry,
                    "identity": f"{nested}/new.csv",
                },
                {"action": "classify-subtree", "disposition": "connected"},
            ),
            "decision": "connected",
            "rule_dependencies": EXCHANGE.SEMANTIC_REVIEW_RULES,
            "input_dependencies": EXCHANGE.review_judgment_inputs(
                scan,
                adjudication,
                queue_item,
                exact_template,
                "connected",
            ),
        }

        action = EXCHANGE._orphan_reuse_action(
            scan, adjudication, queue_item, [parent, child, exact]
        )

        assert action is not None
        self.assertEqual(
            action["connected"], [f"{root}/loose.csv", f"{nested}/new.csv"]
        )
        self.assertEqual(action["unresolved"], [f"{nested}/other.csv"])
        self.assertEqual(
            action["rule_roots"],
            {f"{root}/loose.csv": root, f"{nested}/other.csv": nested},
        )

    def test_new_descendant_reuses_rule_without_another_orphan_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = make_log(Path(directory))
            added = entry.parent / "data" / "prospective" / "first.csv"
            write(added, "value\n1\n")
            first = _complete(summary)
            self.assertEqual(first["status"], "complete", first)

            write(added.with_name("second.csv"), "value\n2\n")
            second = CONTROLLER.validate(
                CONTROLLER.ValidationRequest(
                    summary,
                    result_date="2026-08-17",
                    jobs=1,
                    publish=True,
                )
            )

            self.assertEqual(second["status"], "complete")
            record = TARGET.load_record(
                summary.with_suffix("") / TARGET.RECORD_FILENAME
            )
            subtree_judgments = [
                judgment
                for judgment in record["judgments"]
                if judgment.get("subject", {}).get("kind") == "orphan_subtree"
            ]
            self.assertTrue(subtree_judgments)
            self.assertTrue(
                all(
                    not any(
                        dependency.get("kind") == "orphan-candidate"
                        for dependency in judgment["input_dependencies"]
                    )
                    for judgment in subtree_judgments
                )
            )

    def test_changed_supporting_note_reopens_only_the_rule_that_uses_it(self) -> None:
        entry = "e001"
        data_root = "docs/log/entries/e001/data/set"
        image_root = "docs/log/entries/e001/images/set"
        candidates = [
            _candidate(f"{data_root}/a.csv"),
            _candidate(f"{image_root}/a.png"),
        ]
        old_note = {
            "line": 10,
            "section": "Results",
            "sha256": "a" * 64,
            "text": "Retain the data subtree.",
        }
        new_note = {**old_note, "sha256": "b" * 64, "text": "Changed rule."}

        def scan(note: dict[str, object]) -> dict:
            return {
                "schema_version": 1,
                "validation_rules_version": "rules-v1",
                "entries": [
                    {
                        "id": entry,
                        "path": "docs/log/entries/e001/e001.md",
                        "orphan_inventory": candidates,
                        "validation_notes": [note],
                    }
                ],
            }

        def queue(note: dict[str, object]) -> dict:
            return {
                "entry": entry,
                "kind": "orphan_candidates",
                "identity": "Orphans",
                "candidates": candidates,
                "validation_notes": [note],
            }

        old_scan = scan(old_note)
        old_queue = queue(old_note)
        old_adjudication = {"schema_version": 1, "review_queue": [old_queue]}
        retained_decision = {
            "action": "classify-subtree",
            "disposition": "retained",
            "validation_note": old_note["sha256"],
        }
        retained_template = EXCHANGE._subtree_reuse_template(
            old_queue, "data", data_root
        )
        retained = {
            "identity": "retained",
            "kind": "review-decision",
            "result": "retained",
            "decision": retained_decision,
            "decision_date": "2026-08-16",
            "subject": ORPHANS.subtree_subject(entry, "data", data_root),
            "rule_dependencies": ORPHANS.SUBTREE_RULE_DEPENDENCIES,
            "input_dependencies": EXCHANGE.review_judgment_inputs(
                old_scan,
                old_adjudication,
                old_queue,
                retained_template,
                retained_decision,
            ),
            "rationale": "The authored rule covers this subtree.",
            "rationale_provenance": "recorded",
            "provenance": "native-reviewed",
        }
        connected = {
            **retained,
            "identity": "connected",
            "result": "connected",
            "decision": {
                "action": "classify-subtree",
                "disposition": "connected",
            },
            "subject": ORPHANS.subtree_subject(entry, "images", image_root),
            "input_dependencies": [],
        }
        current_queue = queue(new_note)
        current_adjudication = {
            "schema_version": 1,
            "review_queue": [current_queue],
        }

        action = EXCHANGE._orphan_reuse_action(
            scan(new_note),
            current_adjudication,
            current_queue,
            [retained, connected],
        )

        assert action is not None
        self.assertEqual(action["connected"], [f"{image_root}/a.png"])
        self.assertNotIn(f"{data_root}/a.csv", action["candidate_fingerprints"])

    def test_training_sized_subtree_packet_is_bounded_and_constant_question_count(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = "docs/log/entries/e001/data/training"
            candidates = [
                _candidate(f"{base}/item-{index:05d}.bin") for index in range(12000)
            ]
            scan = {
                "summary": "docs/log.md",
                "log_root": "docs/log",
                "project_root": root.as_posix(),
                "validation_rules_version": "rules-v1",
                "input_fingerprint": "scan-v1",
                "schema_version": 1,
                "entries": [
                    {
                        "id": "e001",
                        "validation_notes": [],
                        "orphan_inventory": candidates,
                    }
                ],
            }
            adjudication = {
                "schema_version": 1,
                "date": "2026-08-16",
                "review_queue": [
                    {
                        "entry": "e001",
                        "kind": "orphan_candidates",
                        "identity": "Orphans",
                        "candidates": candidates,
                        "validation_notes": [],
                    }
                ],
                "entries": [],
            }

            result = EXCHANGE.create_exchange(scan, adjudication, {})

            self.assertEqual(result["item_count"], 1)
            self.assertLessEqual(result["byte_count"], EXCHANGE.MAX_PACKET_BYTES)
            template = json.loads(
                Path(result["decision_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(template["items"][0]["kind"], "orphan_subtree")


if __name__ == "__main__":
    unittest.main()
