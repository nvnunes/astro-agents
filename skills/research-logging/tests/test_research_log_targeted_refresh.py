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
from validation.targeted_refresh import (
    refresh_confirmed_provenance,
    refresh_promoted_provenance,
)


class TargetedProvenanceRefreshTests(unittest.TestCase):
    maxDiff = None

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
                        (name, data.by_name[name].fingerprint) for name in recipe.inputs
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

            results_path = entry_root / "data" / "results.csv"
            results_path.write_text(
                "success_rate,note\n0.676,promoted\n", encoding="utf-8"
            )
            promoted_fingerprint = Fingerprint(
                "sha256",
                digest=hashlib.sha256(results_path.read_bytes()).hexdigest(),
            )
            data_payload = json.loads(
                (entry_root / "data.json").read_text(encoding="utf-8")
            )
            next(item for item in data_payload["inputs"] if item["name"] == "results")[
                "fingerprint"
            ] = promoted_fingerprint.as_dict()
            (entry_root / "data.json").write_text(
                json.dumps(data_payload, indent=2) + "\n", encoding="utf-8"
            )
            promoted_execution = PyrunExecution(
                True,
                execution.slow,
                execution.last_run_at,
                execution.runner,
                execution.environment_profile,
                execution.execution_contract,
                execution.recipe,
                ObservedExecution(
                    execution.observed.script,
                    execution.observed.inputs,
                    execution.observed.code,
                    (("data/results.csv", promoted_fingerprint),),
                ),
            )
            promoted_state = PyrunFile(
                state.path, state.entry_root, {identity: promoted_execution}
            )
            state.path.write_text(
                validated_pyrun_serialization(promoted_state, project_root=root),
                encoding="utf-8",
            )

            promoted = refresh_promoted_provenance(
                summary,
                complete,
                [results_path],
                result_date="2026-08-30",
            )
            promoted_complete = evaluate_current_record(
                summary, result_date="2026-08-30"
            )

            self.assertEqual(
                promoted.canonical_json(), promoted_complete.canonical_json()
            )

            results_path.write_text("success_rate\n0.700\n", encoding="utf-8")
            changed_fingerprint = Fingerprint(
                "sha256",
                digest=hashlib.sha256(results_path.read_bytes()).hexdigest(),
            )
            data_payload["inputs"][1]["fingerprint"] = changed_fingerprint.as_dict()
            (entry_root / "data.json").write_text(
                json.dumps(data_payload, indent=2) + "\n", encoding="utf-8"
            )
            changed_execution = PyrunExecution(
                True,
                promoted_execution.slow,
                promoted_execution.last_run_at,
                promoted_execution.runner,
                promoted_execution.environment_profile,
                promoted_execution.execution_contract,
                promoted_execution.recipe,
                ObservedExecution(
                    promoted_execution.observed.script,
                    promoted_execution.observed.inputs,
                    promoted_execution.observed.code,
                    (("data/results.csv", changed_fingerprint),),
                ),
            )
            state.path.write_text(
                validated_pyrun_serialization(
                    PyrunFile(
                        state.path,
                        state.entry_root,
                        {identity: changed_execution},
                    ),
                    project_root=root,
                ),
                encoding="utf-8",
            )

            changed = refresh_promoted_provenance(
                summary,
                promoted_complete,
                [results_path],
                result_date="2026-08-30",
            )
            changed_complete = evaluate_current_record(
                summary, result_date="2026-08-30"
            )

            self.assertEqual(
                changed.canonical_json(), changed_complete.canonical_json()
            )


if __name__ == "__main__":
    unittest.main()
