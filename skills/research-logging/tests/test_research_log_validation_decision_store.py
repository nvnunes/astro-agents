from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

STORE = importlib.import_module("validation.decision_store")


class DecisionStoreTests(unittest.TestCase):
    def _store(self) -> dict:
        return STORE.build_decision_store(
            [
                {
                    "entry": "e001",
                    "target": "artifact.csv",
                    "check": "Provenance",
                    "result": "FAIL",
                    "dependency_signature": "b" * 64,
                    "findings": ["Producer command was not recorded."],
                },
                {
                    "entry": "e001",
                    "target": "artifact.csv",
                    "check": "Integrity",
                    "result": "2026-08-01",
                    "dependency_signature": "a" * 64,
                },
            ],
            [
                {
                    "entry": "e001",
                    "items": [
                        {
                            "identity": "orphan.csv",
                            "decision": "unresolved",
                            "basis": "-",
                            "fingerprint": "c" * 64,
                        }
                    ],
                }
            ],
            validation_rules_version="research-log-validation-v43",
            local_snapshot_identity="d" * 64,
            report_date="2026-08-02",
        )

    def test_builds_only_provable_semantic_judgments(self) -> None:
        store = self._store()

        decoded = STORE.decode_decision_store(store)

        self.assertEqual(len(decoded["judgments"]), 2)
        failure = next(
            item
            for item in decoded["judgments"]
            if item["kind"] == "completed-check"
        )
        self.assertEqual(failure["rationale_provenance"], "recorded")
        self.assertEqual(failure["date_provenance"], "report-date-fallback")
        orphan = next(
            item
            for item in decoded["judgments"]
            if item["kind"] == "orphan-disposition"
        )
        self.assertEqual(orphan["rationale_provenance"], "unavailable-in-v43")
        self.assertNotIn("rationale", orphan)

    def test_rejects_unknown_fields_and_identity_drift(self) -> None:
        unknown = self._store()
        unknown["legacy"] = True
        with self.assertRaisesRegex(
            STORE.ValidationToolError, "incorrect fields"
        ):
            STORE.decode_decision_store(unknown)

        changed = copy.deepcopy(self._store())
        changed["judgments"][0]["result"] = "accepted"
        with self.assertRaisesRegex(STORE.ValidationToolError, "identity is invalid"):
            STORE.decode_decision_store(changed)

    def test_store_is_deterministic(self) -> None:
        self.assertEqual(self._store(), self._store())

    def test_native_orphan_batches_merge_idempotently_and_conflict_explicitly(
        self,
    ) -> None:
        action = {
            "match": {"kind": "orphan_candidates", "entry": "e002"},
            "decision": "orphan-batch",
            "candidate_fingerprints": {"candidate.csv": "e" * 64},
            "rationales": {
                "candidate.csv": "No producing command or retained use was found."
            },
            "unresolved": ["candidate.csv"],
            "connected": [],
            "retained": [],
        }
        store, counts = STORE.merge_native_orphan_batch_judgments(
            None,
            [action],
            validation_rules_version="research-log-validation-v43",
            local_snapshot_identity="f" * 64,
            decision_date="2026-08-14",
        )
        self.assertEqual(counts["decision-store-merged"], 1)
        self.assertEqual(store["judgments"][0]["provenance"], "native-reviewed")
        repeated, counts = STORE.merge_native_orphan_batch_judgments(
            store,
            [action],
            validation_rules_version="research-log-validation-v43",
            local_snapshot_identity="f" * 64,
            decision_date="2026-08-14",
        )
        self.assertEqual(repeated, store)
        self.assertEqual(counts["decision-store-noop"], 1)

        conflicting = copy.deepcopy(action)
        conflicting["unresolved"] = []
        conflicting["connected"] = ["candidate.csv"]
        with self.assertRaisesRegex(
            STORE.ValidationToolError, "conflicts with existing decision"
        ):
            STORE.merge_native_orphan_batch_judgments(
                store,
                [conflicting],
                validation_rules_version="research-log-validation-v43",
                local_snapshot_identity="f" * 64,
                decision_date="2026-08-14",
            )


if __name__ == "__main__":
    unittest.main()
