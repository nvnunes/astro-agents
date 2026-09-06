from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from research_log_data import Fingerprint, load_data_file
from test_research_log_validation_engine import _evaluate, _log
from validation.commands import CommandContext, discover_commands
from validation.controller import evaluate_current_record
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_RUNNER,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    execution_id,
    recipe_from_invocation,
    validated_pyrun_serialization,
)
from validation.targeted_refresh import refresh_confirmed_provenance


class TargetedProvenanceRefreshTests(unittest.TestCase):
    def test_narrow_refresh_matches_complete_current_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            summary.write_text(
                summary.read_text(encoding="utf-8").replace(
                    "# Study\n\n",
                    "# Study\n\n"
                    "Validation: [latest completed report](study/validation.md)\n\n",
                ),
                encoding="utf-8",
            )
            legacy_path = entry.parent / "pyrun-outputs.json"
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy["outputs"]["data/results.csv"]["confirmed"] = False
            legacy_path.write_text(
                json.dumps(legacy, indent=2) + "\n", encoding="utf-8"
            )
            prior = _evaluate(summary).result
            direct = next(
                check
                for check in prior.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(direct.failure.code, "provenance.output.unconfirmed")

            entry_root = entry.parent
            data = load_data_file(entry_root / "data.json", entry_root=entry_root)
            discovery = discover_commands(
                entry.read_text(encoding="utf-8"),
                CommandContext(
                    log_id=summary.with_suffix("").as_posix(),
                    entry="e001",
                    document=entry.relative_to(summary.with_suffix("")).as_posix(),
                    entry_root=entry_root,
                    log_root=summary.with_suffix(""),
                    project_root=root,
                    data_file=data,
                ),
            )
            self.assertFalse(discovery.failures)
            invocation = discovery.invocations[0]
            recipe = recipe_from_invocation(
                invocation, entry_root=entry_root, project_root=root
            )
            identity = execution_id(recipe)
            execution = PyrunExecution(
                False,
                False,
                None,
                PYRUN_RUNNER,
                PYRUN_ENVIRONMENT_PROFILE,
                PYRUN_EXECUTION_CONTRACT,
                recipe,
                ObservedExecution(
                    Fingerprint("sha256", digest=invocation.script_identity),
                    tuple(
                        (name, data.by_name[name].fingerprint)
                        for name in recipe.inputs
                    ),
                    (),
                    (
                        (
                            "data/results.csv",
                            Fingerprint(
                                "sha256",
                                digest=hashlib.sha256(
                                    (entry_root / "data/results.csv").read_bytes()
                                ).hexdigest(),
                            ),
                        ),
                    ),
                ),
            )
            state = PyrunFile(
                entry_root / "pyrun.json", entry_root, {identity: execution}
            )
            state.path.write_text(
                validated_pyrun_serialization(state, project_root=root),
                encoding="utf-8",
            )
            legacy_path.unlink()
            candidate = PyrunFile(
                state.path,
                state.entry_root,
                {
                    identity: PyrunExecution(
                        True,
                        execution.slow,
                        execution.last_run_at,
                        execution.runner,
                        execution.environment_profile,
                        execution.execution_contract,
                        execution.recipe,
                        execution.observed,
                    )
                },
            )

            refreshed = refresh_confirmed_provenance(
                summary,
                prior,
                {"e001": candidate},
                {"e001": frozenset({identity})},
                result_date="2026-08-30",
            )
            candidate.path.write_text(
                validated_pyrun_serialization(candidate, project_root=root),
                encoding="utf-8",
            )
            complete = evaluate_current_record(summary, result_date="2026-08-30")

            for refreshed_check, complete_check in zip(
                refreshed.checks, complete.checks, strict=True
            ):
                self.assertEqual(
                    refreshed_check.as_dict(),
                    complete_check.as_dict(),
                    refreshed_check.identity,
                )
            self.assertEqual(refreshed.canonical_json(), complete.canonical_json())


if __name__ == "__main__":
    unittest.main()
