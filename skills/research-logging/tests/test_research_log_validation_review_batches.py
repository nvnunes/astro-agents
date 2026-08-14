import copy
import importlib
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
                "queue_fingerprint": first.fingerprint,
                "batch_size": 50,
                "batch_number": 1,
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
            ) as reconcile_orphans:
                decided, _ = DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [unresolved_action],
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
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "fingerprint is stale"
            ):
                DECISIONS.apply_review_decisions(
                    scan,
                    decided,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [unresolved_action],
                    },
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
                    "queue_fingerprint": batch.fingerprint,
                    "batch_size": 50,
                    "batch_number": 1,
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
                "queue_fingerprint": batch.fingerprint,
                "batch_size": 200,
                "batch_number": 1,
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
