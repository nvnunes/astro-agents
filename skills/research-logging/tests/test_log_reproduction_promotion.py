from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands.reproduction_comparison import STAGING_SCHEMA
from log_commands.reproduction_jobs import _accepted_record
from log_commands.reproduction_promotion import promote_execution
from research_log_data import Fingerprint
from test_log_reproduction_execution import _Fixture
from validation.pyrun_state import load_pyrun_state


class ReproductionPromotionTests(unittest.TestCase):
    def test_promotes_complete_execution_by_copy_and_retains_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            run_id, staged = _staged_run(fixture, content=b"changed\n")
            staged_before = staged.read_bytes()

            with (
                mock.patch(
                    "log_commands.reproduction_promotion.verify_reproduction_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_promotion._report_candidates",
                    return_value={},
                ),
            ):
                result = promote_execution(
                    fixture.log,
                    run_id=run_id,
                    execution_id=fixture.identity,
                )

            self.assertEqual(result.outputs, ("data/result.txt",))
            self.assertEqual(fixture.output.read_bytes(), b"changed\n")
            self.assertEqual(staged.read_bytes(), staged_before)
            state = load_pyrun_state(
                fixture.entry_root / "pyrun.json",
                entry_root=fixture.entry_root,
                project_root=fixture.project,
            )
            execution = state.executions[fixture.identity]
            self.assertTrue(execution.confirmed)
            self.assertEqual(
                dict(execution.observed.outputs)["data/result.txt"],
                _fingerprint_bytes(b"changed\n"),
            )

    def test_rejects_partial_staging_without_changing_retained_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            run_id, _staged = _staged_run(fixture, content=b"partial\n", complete=False)
            before = fixture.output.read_bytes()

            with mock.patch(
                "log_commands.reproduction_promotion.verify_reproduction_snapshot"
            ):
                with self.assertRaisesRegex(Exception, "incomplete"):
                    promote_execution(
                        fixture.log,
                        run_id=run_id,
                        execution_id=fixture.identity,
                    )

            self.assertEqual(fixture.output.read_bytes(), before)

    def test_report_failure_rolls_back_artifact_and_pyrun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            run_id, _staged = _staged_run(fixture, content=b"changed\n")
            artifact_before = fixture.output.read_bytes()
            pyrun_before = (fixture.entry_root / "pyrun.json").read_bytes()

            with (
                mock.patch(
                    "log_commands.reproduction_promotion.verify_reproduction_snapshot"
                ),
                mock.patch(
                    "log_commands.reproduction_promotion._report_candidates",
                    side_effect=RuntimeError("publication failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "publication failed"):
                    promote_execution(
                        fixture.log,
                        run_id=run_id,
                        execution_id=fixture.identity,
                    )

            self.assertEqual(fixture.output.read_bytes(), artifact_before)
            self.assertEqual(
                (fixture.entry_root / "pyrun.json").read_bytes(), pyrun_before
            )

    def test_active_reproduction_input_rejects_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            run_id, _staged = _staged_run(fixture, content=b"changed\n")
            run_root = fixture.project / "tmp" / f"reproduce-study-e001-{run_id}"
            path = run_root / "run.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            materials = [
                {
                    "fingerprint": _fingerprint_bytes(
                        fixture.output.read_bytes()
                    ).as_dict(),
                    "identity": fixture.output.resolve().as_posix(),
                    "kind": "file",
                    "role": "boundary",
                }
            ]
            record["source_snapshot"]["materials"] = materials
            record["plan"]["source_snapshot"]["materials"] = materials
            path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            before = fixture.output.read_bytes()

            with mock.patch(
                "log_commands.reproduction_promotion.verify_reproduction_snapshot"
            ):
                with self.assertRaisesRegex(Exception, "active reproduction"):
                    promote_execution(
                        fixture.log,
                        run_id=run_id,
                        execution_id=fixture.identity,
                    )

            self.assertEqual(fixture.output.read_bytes(), before)

    def test_rejects_staging_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            run_id, _staged = _staged_run(fixture, content=b"changed\n")
            run_root = fixture.project / "tmp" / f"reproduce-study-e001-{run_id}"
            path = run_root / "staging.json"
            staging = json.loads(path.read_text(encoding="utf-8"))
            staging["executions"][0]["outputs"][0]["staged"] = "../../outside"
            path.write_text(
                json.dumps(staging, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "staged path is invalid"):
                promote_execution(
                    fixture.log,
                    run_id=run_id,
                    execution_id=fixture.identity,
                )


def _staged_run(
    fixture: _Fixture, *, content: bytes, complete: bool = True
) -> tuple[str, Path]:
    run_id = "reproduce-20300101t000000z-promotion"
    run_root = fixture.project / "tmp" / f"reproduce-study-e001-{run_id}"
    run_root.mkdir(parents=True)
    record = _accepted_record(
        fixture.log,
        fixture.plan,
        run_id,
        run_root,
        fixture.project,
    )
    (run_root / "run.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bundle_path = "executions/e001-fixture"
    staged = run_root / bundle_path / "outputs" / "entry" / "data" / "result.txt"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(content)
    fingerprint = _fingerprint_bytes(content).as_dict()
    staging = {
        "executions": [
            {
                "bytes": len(content),
                "complete": complete,
                "diagnostics": [],
                "entry": "e001",
                "execution_id": fixture.identity,
                "outputs": [
                    {
                        "artifact": "data/result.txt",
                        "available": True,
                        "expected": _fingerprint_bytes(
                            fixture.output.read_bytes()
                        ).as_dict(),
                        "kind": "file",
                        "outcome": "changed",
                        "reason": "content_changed",
                        "regenerated": fingerprint,
                        "staged": "outputs/entry/data/result.txt",
                    }
                ],
                "path": bundle_path,
            }
        ],
        "run_id": run_id,
        "schema": STAGING_SCHEMA,
        "target": dict(fixture.plan.target),
    }
    (run_root / "staging.json").write_text(
        json.dumps(staging, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_id, staged


def _fingerprint_bytes(value: bytes) -> Fingerprint:
    return Fingerprint("sha256", digest=hashlib.sha256(value).hexdigest())


if __name__ == "__main__":
    unittest.main()
