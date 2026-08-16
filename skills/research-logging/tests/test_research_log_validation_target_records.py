from __future__ import annotations

import copy
import importlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import SCRIPT

SCRIPTS = SCRIPT.parent
TARGET = importlib.import_module("validation.target_records")
FIXTURE = Path(__file__).parent / "fixtures" / "v45-hybrid"


class TargetRecordTests(unittest.TestCase):
    def _import_fixture(self, root: Path) -> tuple[dict, dict]:
        output = root / "hybrid"
        shutil.copytree(FIXTURE, output)
        return TARGET.import_v45(output, "docs/hybrid.md")

    def test_target_round_trip_preserves_durable_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, cache = self._import_fixture(root)
            TARGET.write_record_and_cache(root / "target", record, cache)
            loaded = TARGET.load_record(root / "target" / TARGET.RECORD_FILENAME)

            self.assertEqual(loaded, record)
            self.assertEqual(loaded["result"]["date"], "2026-08-15")
            self.assertEqual(loaded["outcomes"][0]["result"], "2026-08-14")
            self.assertEqual(loaded["failures"], record["failures"])
            self.assertEqual(
                loaded["rule_dependencies"], record["rule_dependencies"]
            )
            self.assertEqual(
                loaded["judgments"][0]["rationale_provenance"],
                "unavailable-in-v43",
            )
            self.assertNotIn("rationale", loaded["judgments"][0])
            self.assertNotIn("graph_slice", loaded["outcomes"][0])

    def test_malformed_durable_judgment_fails_actionably(self) -> None:
        record = TARGET.empty_record("docs/mini.md", "rules-v1")
        record["judgments"] = [
            {
                "identity": "decision-1",
                "kind": "semantic",
                "result": "accepted",
                "decision_date": "2026-08-15",
                "subject": {},
                "rule_dependencies": {"semantic": 1},
                "input_dependencies": [],
            }
        ]
        with self.assertRaisesRegex(TARGET.TargetRecordError, "rationale"):
            TARGET.decode_record(record)

    def test_missing_or_malformed_cache_is_a_recomputation_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / TARGET.CACHE_FILENAME
            missing, status = TARGET.load_cache(path)
            self.assertEqual(status, "missing")
            self.assertEqual(missing, TARGET.empty_cache())
            path.write_text("{broken", encoding="utf-8")
            malformed, status = TARGET.load_cache(path)
            self.assertEqual(status, "malformed")
            self.assertEqual(malformed, TARGET.empty_cache())

    def test_cache_rebuild_does_not_change_durable_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, cache = self._import_fixture(root)
            target = root / "target"
            TARGET.write_record_and_cache(target, record, cache)
            before = TARGET.load_record(target / TARGET.RECORD_FILENAME)
            (target / TARGET.CACHE_FILENAME).write_text("invalid", encoding="utf-8")
            rebuilt, status = TARGET.load_cache(target / TARGET.CACHE_FILENAME)
            self.assertEqual(status, "malformed")
            TARGET.write_record_and_cache(target, before, rebuilt)
            self.assertEqual(
                TARGET.load_record(target / TARGET.RECORD_FILENAME), before
            )

    def test_v45_import_reads_only_validation_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "hybrid"
            shutil.copytree(FIXTURE, output)
            artifact = output / "data.csv"
            artifact.write_text("research evidence\n", encoding="utf-8")
            original = Path.read_text
            opened: list[Path] = []

            def tracked(path: Path, *args, **kwargs):
                opened.append(path)
                return original(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", tracked):
                TARGET.import_v45(output, "docs/hybrid.md")
            self.assertEqual(
                {path.name for path in opened},
                {
                    "validation-decisions.json",
                    "validation-state.json",
                    "validation-index.json",
                },
            )
            self.assertNotIn(artifact, opened)

    def test_v45_import_is_idempotent_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_record, first_cache = self._import_fixture(root)
            output = root / "hybrid"
            decisions_path = output / "validation-decisions.json"
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
            decisions["judgments"].append(copy.deepcopy(decisions["judgments"][0]))
            decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["completed_checks"].append(
                copy.deepcopy(state["completed_checks"][0])
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")

            second_record, second_cache = TARGET.import_v45(
                output, "docs/hybrid.md"
            )
            self.assertEqual(second_record, first_record)
            self.assertEqual(second_cache, first_cache)

    def test_pre_v45_format_is_rejected_actionably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "hybrid"
            shutil.copytree(FIXTURE, output)
            path = output / "validation-state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["validation_rules_version"] = "research-log-validation-v44"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(
                TARGET.TargetRecordError, "last v45 tool"
            ):
                TARGET.import_v45(output, "docs/hybrid.md")

    def test_failed_record_write_leaves_prior_record_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, cache = self._import_fixture(root)
            target = root / "target"
            TARGET.write_record_and_cache(target, record, cache)
            before = (target / TARGET.RECORD_FILENAME).read_bytes()
            changed = copy.deepcopy(record)
            changed["result"]["date"] = "2026-08-16"
            original = TARGET._atomic_write_bytes

            def fail_record(path: Path, payload: bytes) -> None:
                if path.name == TARGET.RECORD_FILENAME:
                    raise OSError("simulated record failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_record
            ):
                with self.assertRaisesRegex(
                    TARGET.RecordPublicationError, "could not be written"
                ):
                    TARGET.write_record_and_cache(target, changed, cache)
            self.assertEqual((target / TARGET.RECORD_FILENAME).read_bytes(), before)

    def test_failed_report_write_preserves_prior_completed_report_and_progress(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, cache = self._import_fixture(root)
            target = root / "target"
            TARGET.publish_target_bundle(target, "prior report\n", record, cache)
            before = (target / "validation.md").read_bytes()
            changed = copy.deepcopy(record)
            changed["result"]["date"] = "2026-08-16"
            original = TARGET._atomic_write_bytes

            def fail_report(path: Path, payload: bytes) -> None:
                if path.name == "validation.md":
                    raise OSError("simulated report failure")
                original(path, payload)

            with mock.patch.object(
                TARGET, "_atomic_write_bytes", side_effect=fail_report
            ):
                with self.assertRaisesRegex(
                    TARGET.RecordPublicationError, "bundle could not be written"
                ):
                    TARGET.publish_target_bundle(
                        target, "new report\n", changed, cache
                    )
            self.assertEqual((target / "validation.md").read_bytes(), before)
            self.assertEqual(
                TARGET.load_record(target / TARGET.RECORD_FILENAME), changed
            )

    def test_target_publication_rejects_output_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record, cache = self._import_fixture(root)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                TARGET.RecordPublicationError, "must not be a symlink"
            ):
                TARGET.publish_target_bundle(alias, "report\n", record, cache)


if __name__ == "__main__":
    unittest.main()
