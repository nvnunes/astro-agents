from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from research_log_data import Fingerprint, load_data_file, observe_fingerprint
from test_research_log_validation_engine import _evaluate, _log
from validation.commands import CommandContext, discover_commands
from validation.controller import evaluate_current_record
from validation.errors import MechanicalContractError
from validation.mechanical_results import CheckScope
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
    _evidence_error_scope,
    _observe,
    refresh_promoted_provenance,
)


class TargetedProvenanceRefreshTests(unittest.TestCase):
    maxDiff = None

    def test_artifact_baseline_structure_errors_keep_conformance_scope(self) -> None:
        structural = MechanicalContractError(
            "evidence.declaration.invalid",
            "artifact",
            {},
            "Artifact Evidence Baseline",
        )
        unrecorded = MechanicalContractError(
            "association.artifact.fingerprint_unrecorded",
            "artifact",
            {},
            "Artifact Evidence Baseline",
        )

        self.assertEqual(_evidence_error_scope(structural), CheckScope.CONFORMANCE)
        self.assertEqual(_evidence_error_scope(unrecorded), CheckScope.EVIDENCE)

    def test_directory_observation_includes_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "result.txt").write_text(
                "result\n", encoding="utf-8"
            )

            first = _observe(root)
            (root / "nested" / "result.txt").write_text(
                "changed\n", encoding="utf-8"
            )
            second = _observe(root)

            self.assertNotEqual(first, second)

    def test_artifact_refresh_observes_selected_directory_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            entry_root = entry.parent
            bundle = entry_root / "data" / "bundle"
            bundle.mkdir()
            artifact = bundle / "map.png"
            artifact.write_bytes(b"map bytes")
            selected = bundle / "selected.txt"
            selected.write_text("selected\n", encoding="utf-8")
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"].append(
                {
                    "name": "bundle",
                    "kind": "directory",
                    "location": "data/bundle",
                    "identity": {
                        "algorithm": "identity-files-sha256-v1",
                        "files": ["map.png", "selected.txt"],
                    },
                    "origin": True,
                }
            )
            data_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            entry.write_text(
                entry.read_text(encoding="utf-8")
                + "\n## Map\n\n`Background:`\n\nMap context.\n\n"
                "`Steps:`\n\nOpen the map.\n\n`Results:`\n\n"
                "![Map](data/bundle/map.png)<!-- eid:map -->\n",
                encoding="utf-8",
            )
            evidence_path = entry_root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"].append(
                {
                    "id": "map",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<bundle>/map.png", "locator": None}],
                    "transformation": None,
                    "artifact_fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                }
            )
            evidence_path.write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
            prior = _evaluate(summary).result
            initial = next(
                check for check in prior.checks if check.identity == "evidence:e001:map"
            )
            self.assertEqual(initial.status.value, "pass")

            selected.unlink()
            refreshed = refresh_promoted_provenance(
                summary, prior, [artifact], result_date="2026-08-30"
            )
            check = next(
                item
                for item in refreshed.checks
                if item.identity == "evidence:e001:map"
            )
            self.assertEqual(check.scope, CheckScope.EVIDENCE)
            assert check.failure is not None
            self.assertEqual(check.failure.code, "data.target.missing")

    def test_promotion_refresh_matches_complete_current_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            suffixed_entry = entry.with_name("e001a.md")
            entry.rename(suffixed_entry)
            entry = suffixed_entry
            summary.write_text(
                summary.read_text(encoding="utf-8")
                .replace("ref entry = e001;", "ref entry = e001a;")
                .replace("e001.md", "e001a.md")
                .replace(
                    "# Study\n\n",
                    "# Study\n\n"
                    "Validation: [latest completed report](study/validation.md)\n\n",
                ),
                encoding="utf-8",
            )
            narrative = (
                summary.with_suffix("")
                / "entries"
                / "2026-08-29-e002-notes"
                / "e002.md"
            )
            narrative.parent.mkdir(parents=True)
            narrative.write_text(
                "# Entry e002\n\n## Notes\n\nNarrative-only context.\n",
                encoding="utf-8",
            )
            sibling = entry.with_name("e001b.md")
            sibling.write_text(
                "# Entry e001b\n\n## Notes\n\nShared-entry context.\n",
                encoding="utf-8",
            )
            summary.write_text(
                summary.read_text(encoding="utf-8").replace(
                    "- [Study trial](study/entries/2026-08-29-e001-study/e001a.md)\n",
                    "- [Study trial](study/entries/2026-08-29-e001-study/e001a.md)\n"
                    "- [Shared notes](study/entries/2026-08-29-e001-study/e001b.md)\n"
                    "- [Notes](study/entries/2026-08-29-e002-notes/e002.md)\n",
                ),
                encoding="utf-8",
            )
            evidence_path = entry.parent / "evidence.json"
            evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence_payload["records"][0]["document"] = (
                "entries/2026-08-29-e001-study/e001a.md"
            )
            evidence_path.write_text(
                json.dumps(evidence_payload, indent=2) + "\n", encoding="utf-8"
            )
            legacy_path = entry.parent / "pyrun-outputs.json"
            legacy_path.unlink()
            prior = _evaluate(summary).result
            direct = next(
                check
                for check in prior.checks
                if check.identity == "provenance:e001a:success-rate"
            )
            self.assertEqual(direct.failure.code, "provenance.output.unrecorded")

            entry_root = entry.parent
            data = load_data_file(entry_root / "data.json", entry_root=entry_root)
            discovery = discover_commands(
                entry.read_text(encoding="utf-8"),
                CommandContext(
                    log_id=summary.with_suffix("").as_posix(),
                    entry="e001a",
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
                True,
                True,
                None,
                PYRUN_RUNNER,
                PYRUN_ENVIRONMENT_PROFILE,
                PYRUN_EXECUTION_CONTRACT,
                recipe,
                ObservedExecution(
                    Fingerprint("sha256", digest=invocation.script_identity),
                    tuple(
                        (name, observe_fingerprint(data.by_name[name]).fingerprint)
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
            candidate = PyrunFile(
                state.path,
                state.entry_root,
                {
                    identity: PyrunExecution(
                        False,
                        execution.auto_reproduce,
                        execution.last_run_at,
                        execution.runner,
                        execution.environment_profile,
                        execution.execution_contract,
                        execution.recipe,
                        execution.observed,
                    )
                },
            )

            candidate.path.write_text(
                validated_pyrun_serialization(candidate, project_root=root),
                encoding="utf-8",
            )
            complete = evaluate_current_record(summary, result_date="2026-08-30")

            results_path = entry_root / "data" / "results.csv"
            results_path.write_text(
                "success_rate,note\n0.676,promoted\n", encoding="utf-8"
            )
            promoted_fingerprint = Fingerprint(
                "sha256",
                digest=hashlib.sha256(results_path.read_bytes()).hexdigest(),
            )
            promoted_execution = PyrunExecution(
                False,
                execution.auto_reproduce,
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
            changed_execution = PyrunExecution(
                False,
                promoted_execution.auto_reproduce,
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
