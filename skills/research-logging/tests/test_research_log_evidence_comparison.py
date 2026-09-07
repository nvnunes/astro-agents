from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from research_log_data import (
    EVIDENCE_COMPARISON_CONTRACT,
    ReproductionComparison,
    build_local_input,
    data_file_from_inputs,
)
from validation.errors import MechanicalContractError
from validation.evidence import (
    EvidenceRecord,
    EvidenceSource,
    ReproductionTolerance,
    evidence_file_from_records,
)
from validation.evidence_comparison import (
    compare_evidence_scoped,
    evidence_comparison_definition,
)


class EvidenceScopedComparisonTests(unittest.TestCase):
    def test_exact_selected_value_matches_despite_whole_artifact_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            definition, regenerated = self._fixture(
                Path(directory),
                retained='{"stable":7,"runtime":10.0}\n',
                regenerated='{"stable":7,"runtime":15.0}\n',
                records=(self._record("stable", "stable"),),
            )

            outcome = compare_evidence_scoped(definition, regenerated=regenerated)

            self.assertTrue(outcome.matched)
            self.assertEqual([item["id"] for item in outcome.records], ["stable"])

    def test_numeric_selected_value_uses_its_record_tolerance(self) -> None:
        record = self._record("runtime", "runtime", tolerance="0.1")
        with tempfile.TemporaryDirectory() as directory:
            definition, regenerated = self._fixture(
                Path(directory),
                retained='{"runtime":10.0}\n',
                regenerated='{"runtime":10.05}\n',
                records=(record,),
            )

            outcome = compare_evidence_scoped(definition, regenerated=regenerated)

            self.assertTrue(outcome.matched)
            self.assertEqual(outcome.records[0]["tolerance"], {"absolute": "0.1"})

    def test_every_applicable_record_must_match(self) -> None:
        records = (
            self._record("stable", "stable"),
            self._record("runtime", "runtime", tolerance="0.1"),
        )
        with tempfile.TemporaryDirectory() as directory:
            definition, regenerated = self._fixture(
                Path(directory),
                retained='{"stable":7,"runtime":10.0}\n',
                regenerated='{"stable":8,"runtime":10.05}\n',
                records=records,
            )

            outcome = compare_evidence_scoped(definition, regenerated=regenerated)

            self.assertFalse(outcome.matched)
            self.assertEqual(
                [(item["id"], item["matched"]) for item in outcome.records],
                [("stable", False), ("runtime", True)],
            )

    def test_missing_regenerated_value_is_comparison_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            definition, regenerated = self._fixture(
                Path(directory),
                retained='{"stable":7}\n',
                regenerated='{"other":7}\n',
                records=(self._record("stable", "stable"),),
            )

            with self.assertRaises(MechanicalContractError):
                compare_evidence_scoped(definition, regenerated=regenerated)

    @staticmethod
    def _record(
        record_id: str, field: str, *, tolerance: str | None = None
    ) -> EvidenceRecord:
        return EvidenceRecord(
            record_id,
            "entries/2026-09-07-e001-test/e001.md",
            "statistic",
            (EvidenceSource("<result>", {"path": [field]}),),
            None,
            (ReproductionTolerance(tolerance) if tolerance is not None else None),
        )

    @staticmethod
    def _fixture(
        root: Path,
        *,
        retained: str,
        regenerated: str,
        records: tuple[EvidenceRecord, ...],
    ):
        log_root = root / "docs" / "study"
        entry_root = log_root / "entries" / "2026-09-07-e001-test"
        data_root = entry_root / "data"
        data_root.mkdir(parents=True)
        (entry_root / "e001.md").write_text("# Test\n", encoding="utf-8")
        retained_path = data_root / "result.json"
        regenerated_path = root / "regenerated.json"
        retained_path.write_text(retained, encoding="utf-8")
        regenerated_path.write_text(regenerated, encoding="utf-8")
        resource = replace(
            build_local_input(
                "result",
                "file",
                "data/result.json",
                entry_root=entry_root,
            ),
            comparison=ReproductionComparison(EVIDENCE_COMPARISON_CONTRACT, "evidence"),
        )
        data = data_file_from_inputs(
            entry_root / "data.json", entry_root=entry_root, inputs=(resource,)
        )
        evidence = evidence_file_from_records(
            entry_root / "evidence.json",
            log_root=log_root,
            entry_root=entry_root,
            records=records,
        )
        definition = evidence_comparison_definition(
            data.inputs[0], data=data, evidence=evidence
        )
        return definition, regenerated_path


if __name__ == "__main__":
    unittest.main()
