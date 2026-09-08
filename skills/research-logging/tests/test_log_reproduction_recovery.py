from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from log_commands.model import ActionError
from log_commands.reproduction_comparison import (
    ArtifactComparison,
    ExecutionComparison,
)
from log_commands.reproduction_recovery import (
    _dependency_skips,
    _verify_normalized_pyrun,
)
from test_log_reproduction_execution import _Fixture
from validation.pyrun_state import load_pyrun_state


class ReproductionRecoveryTests(unittest.TestCase):
    def test_normalized_pyrun_accepts_only_the_confirmed_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            path = fixture.entry_root / "pyrun.json"
            state = load_pyrun_state(
                path,
                entry_root=fixture.entry_root,
                project_root=fixture.project,
            )
            execution = state.executions[fixture.identity]
            pre_repair = _digest(execution.as_dict())
            confirmed = _digest(replace(execution, confirmed=True).as_dict())
            file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            candidate = {
                "confirmed_record_digest": confirmed,
                "current_confirmed": False,
                "execution_id": fixture.identity,
                "pre_repair_record_digest": pre_repair,
            }

            _verify_normalized_pyrun(fixture.project, path, [candidate], file_digest)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["executions"][fixture.identity]["confirmed"] = True
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _verify_normalized_pyrun(fixture.project, path, [candidate], file_digest)

            value["executions"][fixture.identity]["auto_reproduce"] = False
            path.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ActionError):
                _verify_normalized_pyrun(
                    fixture.project, path, [candidate], file_digest
                )

    def test_dependency_skips_reuse_one_failed_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            dependent = "pyrun-exec/v1:" + "2" * 64
            reference = f"e001:{fixture.identity}"
            plan = replace(
                fixture.plan,
                executions=(
                    {
                        "depends_on": [],
                        "entry": "e001",
                        "execution_id": fixture.identity,
                        "order": 1,
                    },
                    {
                        "depends_on": [reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "order": 2,
                    },
                ),
            )
            failure = ExecutionComparison(
                "e001",
                fixture.identity,
                (
                    ArtifactComparison(
                        "data/result.txt",
                        "failed",
                        "execution_failed",
                        None,
                        None,
                        None,
                    ),
                ),
                None,
                False,
            )

            self.assertEqual(
                _dependency_skips(plan, (failure,)),
                (
                    {
                        "depends_on": [reference],
                        "entry": "e001",
                        "execution_id": dependent,
                        "reason": "dependency_failed",
                    },
                ),
            )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
