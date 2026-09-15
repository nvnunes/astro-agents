from __future__ import annotations

import importlib
from typing import Any

import research_log_validation_test_support  # noqa: F401
from research_log_validation_test_support import mock, unittest

DOMAIN = importlib.import_module("validation.domain")


def finding(
    identity: str,
    area: object,
    *,
    code: str | None = None,
    key: object | None = None,
    entry: str | None = "e001",
    caused_by: tuple[str, ...] = (),
    context_entry: str | None = None,
) -> object:
    return DOMAIN.Finding(
        identity,
        area,
        code or f"{area.value}.{identity}",
        entry,
        identity,
        "Typed Repair Relationships And Batching",
        {"identity": identity},
        caused_by,
        () if key is None else (key,),
        (
            ()
            if context_entry is None
            else (DOMAIN.GraphReference("material", identity, context_entry),)
        ),
    )


class ValidationBatchTests(unittest.TestCase):
    def test_shared_strong_key_joins_cross_type_findings(self) -> None:
        key = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.COMMAND,
            "e001:build",
            "e001",
        )

        batches = DOMAIN.build_batches(
            (
                finding("command:build", DOMAIN.RuleArea.CONFORMANCE, key=key),
                finding("producer:build", DOMAIN.RuleArea.PROVENANCE, key=key),
            )
        )

        self.assertEqual(len(batches), 1)
        self.assertEqual(
            batches[0].finding_ids,
            ("command:build", "producer:build"),
        )
        self.assertEqual(batches[0].repair_entries, ("e001",))
        self.assertEqual(
            batches[0].rationale,
            ("repair-key:command:e001:build",),
        )
        self.assertEqual(batches[0].focus_finding_id, "command:build")

    def test_same_type_and_entry_do_not_join_without_proven_relationship(self) -> None:
        batches = DOMAIN.build_batches(
            (
                finding("evidence:a", DOMAIN.RuleArea.EVIDENCE),
                finding("evidence:b", DOMAIN.RuleArea.EVIDENCE),
            )
        )

        self.assertEqual(len(batches), 2)
        self.assertEqual(
            {batch.finding_ids for batch in batches},
            {("evidence:a",), ("evidence:b",)},
        )

    def test_residual_orphan_singletons_join_by_entry_and_exact_code(self) -> None:
        batches = DOMAIN.build_batches(
            (
                finding(
                    "orphan:a",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                ),
                finding(
                    "orphan:b",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                ),
            )
        )

        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].finding_ids, ("orphan:a", "orphan:b"))
        self.assertEqual(
            batches[0].rationale,
            ("orphan-singletons:e001:orphan.material.unused",),
        )

    def test_orphan_fallback_keeps_entries_and_codes_separate(self) -> None:
        batches = DOMAIN.build_batches(
            (
                finding(
                    "material:e001",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                ),
                finding(
                    "input:e001",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.input.unused",
                ),
                finding(
                    "material:e002",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                    entry="e002",
                ),
            )
        )

        self.assertEqual(len(batches), 3)

    def test_entryless_orphan_fallback_uses_log_scope_without_a_repair_key(
        self,
    ) -> None:
        batches = DOMAIN.build_batches(
            (
                finding(
                    "residue:a",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.generated.residue",
                    entry=None,
                ),
                finding(
                    "residue:b",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.generated.residue",
                    entry=None,
                ),
            )
        )

        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].repair_keys, ())
        self.assertEqual(batches[0].repair_entries, ())
        self.assertEqual(
            batches[0].rationale,
            ("orphan-singletons:log:orphan.generated.residue",),
        )

    def test_orphan_fallback_does_not_absorb_an_existing_batch(self) -> None:
        directory = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.MATERIAL,
            "entries/e001/artifacts/config",
            "e001",
        )
        batches = DOMAIN.build_batches(
            (
                finding(
                    "grouped:a",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                    key=directory,
                ),
                finding(
                    "grouped:b",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                    key=directory,
                ),
                finding(
                    "residual",
                    DOMAIN.RuleArea.ORPHAN,
                    code="orphan.material.unused",
                ),
            )
        )

        self.assertEqual(len(batches), 2)
        self.assertEqual(
            {batch.finding_ids for batch in batches},
            {("grouped:a", "grouped:b"), ("residual",)},
        )

    def test_explicit_failed_prerequisite_joins_and_selects_root_focus(self) -> None:
        batches = DOMAIN.build_batches(
            (
                finding("root", DOMAIN.RuleArea.CONFORMANCE),
                finding(
                    "dependent",
                    DOMAIN.RuleArea.EVIDENCE,
                    caused_by=("root",),
                ),
            )
        )

        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].focus_finding_id, "root")
        self.assertEqual(batches[0].rationale, ("caused-by:dependent:root",))

    def test_order_and_incidental_context_do_not_change_batch_identity(self) -> None:
        key = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.MATERIAL,
            "data/result.csv",
            "e001",
        )
        first = finding(
            "producer:result",
            DOMAIN.RuleArea.PROVENANCE,
            key=key,
            context_entry="e002",
        )
        second = finding(
            "evidence:result",
            DOMAIN.RuleArea.EVIDENCE,
            key=key,
            context_entry="e003",
        )

        forward = DOMAIN.build_batches((first, second))[0]
        reverse = DOMAIN.build_batches((second, first))[0]

        self.assertEqual(forward.batch_id, reverse.batch_id)
        self.assertEqual(forward.context_entries, ("e002", "e003"))

    def test_shared_identity_retains_every_mechanically_known_repair_entry(
        self,
    ) -> None:
        first_key = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.MATERIAL,
            "data/shared.csv",
            "e001",
        )
        second_key = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.MATERIAL,
            "data/shared.csv",
            "e002",
        )

        batch = DOMAIN.build_batches(
            (
                finding("producer:first", DOMAIN.RuleArea.PROVENANCE, key=first_key),
                finding("producer:second", DOMAIN.RuleArea.PROVENANCE, key=second_key),
            )
        )[0]

        self.assertEqual(batch.repair_entries, ("e001", "e002"))
        self.assertEqual(len(batch.repair_keys), 1)
        self.assertIsNone(batch.repair_keys[0].repair_entry)
        self.assertEqual(
            batch.rationale,
            ("repair-key:material:data/shared.csv",),
        )

    def test_large_shared_batch_reads_repair_keys_linearly(self) -> None:
        shared = DOMAIN.RepairKey(
            DOMAIN.RepairKeyKind.COMMAND,
            "e001:build",
            "e001",
        )
        findings = tuple(
            DOMAIN.Finding(
                f"finding:{index:04d}",
                DOMAIN.RuleArea.PROVENANCE,
                "provenance.output.reproduction_required",
                "e001",
                f"data/result-{index:04d}.csv",
                "Pyrun Output Support Records",
                {"index": index},
                repair_keys=(
                    shared,
                    DOMAIN.RepairKey(
                        DOMAIN.RepairKeyKind.MATERIAL,
                        f"data/result-{index:04d}.csv",
                        "e001",
                    ),
                ),
            )
            for index in range(500)
        )
        reads = 0

        def counted_value(key: Any) -> str:
            nonlocal reads
            reads += 1
            return f"{key.kind.value}:{key.identity}"

        with mock.patch.object(
            DOMAIN.RepairKey,
            "value",
            property(counted_value),
        ):
            batches = DOMAIN.build_batches(findings)

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0].finding_ids), len(findings))
        self.assertLessEqual(reads, 12 * len(findings))

    def test_large_orphan_fallback_reads_repair_keys_linearly(self) -> None:
        findings = tuple(
            finding(
                f"orphan:{index:04d}",
                DOMAIN.RuleArea.ORPHAN,
                code="orphan.material.unused",
                key=DOMAIN.RepairKey(
                    DOMAIN.RepairKeyKind.MATERIAL,
                    f"entries/e001/artifacts/result-{index:04d}.csv",
                    "e001",
                ),
            )
            for index in range(500)
        )
        reads = 0

        def counted_value(key: Any) -> str:
            nonlocal reads
            reads += 1
            return f"{key.kind.value}:{key.identity}"

        with mock.patch.object(
            DOMAIN.RepairKey,
            "value",
            property(counted_value),
        ):
            batches = DOMAIN.build_batches(findings)

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0].finding_ids), len(findings))
        self.assertLessEqual(reads, 12 * len(findings))

    def test_batch_rejects_duplicate_or_incomplete_membership(self) -> None:
        one = finding("one", DOMAIN.RuleArea.ORPHAN)
        attempt = DOMAIN.ValidationAttempt.build(
            target=DOMAIN.ValidationTarget(DOMAIN.TargetKind.LOG, "docs/log"),
            source_identity="source-1",
            rules_version="rules-1",
            started_at="start",
            finished_at="finish",
            checks=(
                DOMAIN.RuleCheck(
                    "one",
                    DOMAIN.RuleArea.ORPHAN,
                    DOMAIN.CheckOutcome.FINDING,
                    "one",
                    diagnostic=DOMAIN.CheckDiagnostic(
                        "orphan.one", "one", "rule", {"state": "unused"}
                    ),
                ),
            ),
        )
        canonical = DOMAIN.ValidationSnapshot.from_attempt(attempt)
        with self.assertRaises(DOMAIN.ValidationDomainError):
            DOMAIN.ValidationSnapshot(
                canonical.internal_snapshot_id,
                canonical.target,
                canonical.source_identity,
                canonical.rules_version,
                canonical.started_at,
                canonical.finished_at,
                None,
                DOMAIN.SnapshotOutcome.FINDINGS,
                canonical.passed_check_count,
                (one,),
                (),
                (),
                (),
                {},
                {},
            )


if __name__ == "__main__":
    unittest.main()
