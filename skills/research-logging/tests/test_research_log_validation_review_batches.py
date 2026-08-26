import copy
import hashlib
import importlib
import json
from unittest.mock import patch

from research_log_validation_test_support import (
    ADJUDICATION,
    CONTRACTS,
    DECISIONS,
    RUNTIME,
    Path,
    make_log,
    prepare_adjudication,
    tempfile,
    unittest,
    write,
)

REVIEW_BATCHES = importlib.import_module("validation.review_batches")


class OrphanBatchTests(unittest.TestCase):
    def test_entry_scoped_context_preserves_historical_fingerprint_bytes(
        self,
    ) -> None:
        candidate = {
            "identity": "entries/e001/data/résult.json",
            "kind": "artifact",
            "metadata": {"z": 2, "a": ["α", 1]},
        }
        scan = {
            "schema_version": 16,
            "validation_rules_version": "rules-v1",
            "entries": [
                {
                    "id": "e001",
                    "commands": [{"command": "python run.py", "line": 12}],
                    "data_index": {"input": {"sha256": "a" * 64}},
                    "validation_notes": [
                        {"sha256": "c" * 64},
                        {"sha256": "b" * 64},
                    ],
                }
            ],
        }
        payload = {
            "validation_rules_version": "rules-v1",
            "scan_schema_version": 16,
            "adjudication_schema_version": 7,
            "decision_schema_version": 6,
            "entry": "e001",
            "candidate": candidate,
            "commands": scan["entries"][0]["commands"],
            "data_index": scan["entries"][0]["data_index"],
            "validation_notes": ["b" * 64, "c" * 64],
        }
        historical = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

        context = REVIEW_BATCHES.orphan_fingerprint_context(
            scan, 7, "e001", 6
        )

        self.assertEqual(context.fingerprint(candidate), historical)
        self.assertEqual(
            REVIEW_BATCHES.orphan_candidate_fingerprint(
                scan, 7, "e001", candidate, 6
            ),
            historical,
        )

    def test_batches_are_bounded_stale_safe_atomic_and_convergent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            for index in range(205):
                write(
                    entry.parent / "data" / "orphan-batch" / f"item-{index:03d}.csv",
                    "value\n1\n",
                )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(
                scan, "2026-08-14", RUNTIME.RULES_VERSION
            )
            orphan = max(
                (
                    item
                    for item in prepared["review_queue"]
                    if item["kind"] == "orphan_candidates"
                ),
                key=lambda item: len(item["candidates"]),
            )
            candidate_count = len(orphan["candidates"])
            self.assertGreater(candidate_count, 200)

            metrics = {}
            packet, counts = ADJUDICATION.make_review_packet(
                scan,
                prepared,
                ADJUDICATION.ReviewPacketRequest(
                    entry=orphan["entry"], kind="orphan_candidates"
                ),
                metrics=metrics,
            )

            self.assertEqual(counts, {"orphan_candidates": 1})
            self.assertIn("# PARTIAL ORPHAN REVIEW", packet)
            self.assertEqual(metrics["orphan_candidates_in_packet"], 200)
            self.assertEqual(
                metrics["orphan_candidates_remaining"], candidate_count - 200
            )
            self.assertLessEqual(packet.count("- Candidate artifact:"), 200)

            total_batches = (candidate_count + 49) // 50
            for number in (1, max(1, total_batches // 2), total_batches):
                selected_packet, _ = ADJUDICATION.make_review_packet(
                    scan,
                    prepared,
                    ADJUDICATION.ReviewPacketRequest(
                        entry=orphan["entry"],
                        kind="orphan_candidates",
                        batch_size=50,
                        batch_number=number,
                    ),
                )
                self.assertIn(
                    f"- Orphan batch: {number} of {total_batches}", selected_packet
                )
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "out of range"
            ):
                ADJUDICATION.make_review_packet(
                    scan,
                    prepared,
                    ADJUDICATION.ReviewPacketRequest(
                        entry=orphan["entry"],
                        kind="orphan_candidates",
                        batch_size=50,
                        batch_number=total_batches + 1,
                    ),
                )

            first = REVIEW_BATCHES.select_orphan_batch(
                scan,
                prepared,
                orphan,
                REVIEW_BATCHES.OrphanBatchRequest(
                    50, 1, DECISIONS.DECISION_SCHEMA_VERSION
                ),
            )
            identities = [candidate["identity"] for candidate in first.candidates]
            action = {
                "match": {
                    "kind": "orphan_candidates",
                    "entry": orphan["entry"],
                },
                "decision": "orphan-batch",
                "candidate_fingerprints": dict(first.candidate_fingerprints),
                "unresolved": [],
                "connected": identities,
                "retained": [],
            }
            invalid = copy.deepcopy(action)
            invalid["connected"] = identities[:-1]
            prior = copy.deepcopy(prepared)
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "must be partitioned"
            ):
                DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [invalid],
                    },
                )
            self.assertEqual(prepared, prior)
            overlapping = copy.deepcopy(action)
            overlapping["unresolved"] = [identities[0]]
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "must be partitioned"
            ):
                DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [overlapping],
                    },
                )
            self.assertEqual(prepared, prior)

            unresolved_action = copy.deepcopy(action)
            unresolved_action["unresolved"] = unresolved_action.pop("connected")
            unresolved_action["connected"] = []
            with patch.object(
                DECISIONS, "reconcile_semantic_dependencies"
            ) as reconcile_semantic, patch.object(
                DECISIONS, "reconcile_graph_orphans"
            ) as reconcile_orphans, patch.object(
                DECISIONS,
                "orphan_fingerprint_context",
                side_effect=AssertionError("trusted fingerprints were recomputed"),
            ):
                decided, _ = DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [unresolved_action],
                    },
                    trusted_orphan_fingerprints={
                        str(orphan["entry"]): dict(first.candidate_fingerprints)
                    },
                )
            reconcile_semantic.assert_not_called()
            reconcile_orphans.assert_not_called()
            remaining = next(
                item
                for item in decided["review_queue"]
                if item["kind"] == "orphan_candidates"
                and item["entry"] == orphan["entry"]
            )
            remaining_identities = {
                candidate["identity"] for candidate in remaining["candidates"]
            }
            self.assertTrue(set(identities).isdisjoint(remaining_identities))
            self.assertLess(len(remaining_identities), candidate_count)
            repeated, repeated_counts = DECISIONS.apply_review_decisions(
                scan,
                decided,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [unresolved_action],
                },
            )
            self.assertEqual(repeated, decided)
            self.assertEqual(repeated_counts["orphan-batch-noop"], 1)
            conflicting = copy.deepcopy(unresolved_action)
            conflicting["connected"] = conflicting.pop("unresolved")
            conflicting["unresolved"] = []
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "conflicts with an existing decision"
            ):
                DECISIONS.apply_review_decisions(
                    scan,
                    decided,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [conflicting],
                    },
                )

            second = REVIEW_BATCHES.select_orphan_batch(
                scan,
                prepared,
                orphan,
                REVIEW_BATCHES.OrphanBatchRequest(
                    50, 2, DECISIONS.DECISION_SCHEMA_VERSION
                ),
            )
            second_identities = [
                candidate["identity"] for candidate in second.candidates
            ]
            second_action = {
                "match": {
                    "kind": "orphan_candidates",
                    "entry": orphan["entry"],
                },
                "decision": "orphan-batch",
                "candidate_fingerprints": dict(second.candidate_fingerprints),
                "unresolved": second_identities,
                "connected": [],
                "retained": [],
            }
            reverse, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [second_action],
                },
            )
            reverse, _ = DECISIONS.apply_review_decisions(
                scan,
                reverse,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [unresolved_action],
                },
            )
            reverse_remaining = next(
                item
                for item in reverse["review_queue"]
                if item["kind"] == "orphan_candidates"
                and item["entry"] == orphan["entry"]
            )
            self.assertTrue(
                (set(identities) | set(second_identities)).isdisjoint(
                    {
                        candidate["identity"]
                        for candidate in reverse_remaining["candidates"]
                    }
                )
            )

            complete_action = {
                "match": {
                    "kind": "orphan_candidates",
                    "entry": orphan["entry"],
                },
                "decision": "orphan",
                "unresolved": [],
                "connected": [
                    candidate["identity"] for candidate in orphan["candidates"]
                ],
                "retained": [],
            }
            complete, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [complete_action],
                },
            )
            batched = copy.deepcopy(prepared)
            while True:
                queued = next(
                    (
                        item
                        for item in batched["review_queue"]
                        if item["kind"] == "orphan_candidates"
                        and item["entry"] == orphan["entry"]
                    ),
                    None,
                )
                if queued is None:
                    break
                batch = REVIEW_BATCHES.select_orphan_batch(
                    scan,
                    batched,
                    queued,
                    REVIEW_BATCHES.OrphanBatchRequest(
                        50, 1, DECISIONS.DECISION_SCHEMA_VERSION
                    ),
                )
                batch_action = {
                    "match": {
                        "kind": "orphan_candidates",
                        "entry": orphan["entry"],
                    },
                    "decision": "orphan-batch",
                    "candidate_fingerprints": dict(
                        batch.candidate_fingerprints
                    ),
                    "unresolved": [],
                    "connected": [
                        candidate["identity"] for candidate in batch.candidates
                    ],
                    "retained": [],
                }
                batched, _ = DECISIONS.apply_review_decisions(
                    scan,
                    batched,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [batch_action],
                    },
                )

            complete_entry = next(
                item for item in complete["entries"] if item["id"] == orphan["entry"]
            )
            batched_entry = next(
                item for item in batched["entries"] if item["id"] == orphan["entry"]
            )
            self.assertEqual(
                batched_entry["orphan_items"], complete_entry["orphan_items"]
            )
            self.assertFalse(
                any(
                    row["target"] == ADJUDICATION.ORPHAN_TARGET
                    for row in batched_entry["targets"]
                )
            )

    def test_schema_four_decisions_are_retired(self) -> None:
        with self.assertRaisesRegex(
            CONTRACTS.ValidationToolError, "unsupported decision schema_version"
        ):
            DECISIONS.validated_decision_actions(
                {"schema_version": 4, "actions": []}, DECISIONS.decision_policy()
            )

    def test_final_unresolved_batch_reconciles_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "data" / "orphan.csv", "value\n1\n")
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(
                scan, "2026-08-14", RUNTIME.RULES_VERSION
            )
            orphan = next(
                item
                for item in prepared["review_queue"]
                if item["kind"] == "orphan_candidates"
                and any(
                    candidate["identity"].endswith("orphan.csv")
                    for candidate in item["candidates"]
                )
            )
            batch = REVIEW_BATCHES.select_orphan_batch(
                scan,
                prepared,
                orphan,
                REVIEW_BATCHES.OrphanBatchRequest(
                    200, 1, DECISIONS.DECISION_SCHEMA_VERSION
                ),
            )
            action = {
                "match": {
                    "kind": "orphan_candidates",
                    "entry": orphan["entry"],
                },
                "decision": "orphan-batch",
                "candidate_fingerprints": dict(batch.candidate_fingerprints),
                "unresolved": [
                    candidate["identity"] for candidate in batch.candidates
                ],
                "connected": [],
                "retained": [],
            }
            with patch.object(
                DECISIONS,
                "reconcile_semantic_dependencies",
                wraps=DECISIONS.reconcile_semantic_dependencies,
            ) as reconcile_semantic, patch.object(
                DECISIONS,
                "reconcile_graph_orphans",
                wraps=DECISIONS.reconcile_graph_orphans,
            ) as reconcile_orphans:
                decided, _ = DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [action],
                    },
                )
            reconcile_semantic.assert_called_once()
            reconcile_orphans.assert_called_once()
            self.assertFalse(
                any(
                    item["kind"] == "orphan_candidates"
                    and item["entry"] == orphan["entry"]
                    for item in decided["review_queue"]
                )
            )

    def test_empty_orphan_batch_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            CONTRACTS.ValidationToolError, "cannot select an empty queue"
        ):
            REVIEW_BATCHES.select_orphan_batch(
                {},
                {},
                {"candidates": []},
                REVIEW_BATCHES.OrphanBatchRequest(
                    200, 1, DECISIONS.DECISION_SCHEMA_VERSION
                ),
            )


if __name__ == "__main__":
    unittest.main()
