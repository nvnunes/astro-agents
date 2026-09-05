from __future__ import annotations

import hashlib
import importlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import research_log_data as DATA
from research_log_validation_test_support import mock, unittest, write

ENGINE = importlib.import_module("validation.engine")
MECHANICAL = importlib.import_module("validation.mechanical")
RESULTS = importlib.import_module("validation.mechanical_results")
LOCATOR = importlib.import_module("validation.locator")
CACHE = importlib.import_module("validation.validation_cache")


def _log(root: Path, *, output_option: str = "output-data") -> tuple[Path, Path]:
    (root / ".git").mkdir(exist_ok=True)
    summary = root / "docs" / "study.md"
    log_root = root / "docs" / "study"
    entry_root = log_root / "entries" / "2026-08-29-e001-study"
    entry = entry_root / "e001.md"
    write(
        summary,
        "# Study\n\n"
        "## Summary\n\n"
        "- Success rate: `67.6%`"
        "<!-- ref entry = e001; eid = success-rate -->.\n\n"
        "## Entries\n\n"
        "- [Study trial](study/entries/2026-08-29-e001-study/e001.md)\n",
    )
    write(entry_root / "scripts" / "model.py", "# retained model\n")
    write(entry_root / "data" / "catalog.csv", "id\n1\n")
    write(entry_root / "data" / "results.csv", "success_rate\n0.676\n")
    catalog_digest = hashlib.sha256(
        (entry_root / "data" / "catalog.csv").read_bytes()
    ).hexdigest()
    results_digest = hashlib.sha256(
        (entry_root / "data" / "results.csv").read_bytes()
    ).hexdigest()
    write(
        entry_root / "data.json",
        json.dumps(
            {
                "schema": "research-log-data/v3",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": catalog_digest,
                        },
                        "origin": True,
                    },
                    {
                        "name": "results",
                        "kind": "file",
                        "location": "data/results.csv",
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": results_digest,
                        },
                        "origin": False,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry_root / "evidence.json",
        """{
  "schema": "research-log-evidence/v3",
  "records": [
    {
      "id": "success-rate",
      "document": "entries/2026-08-29-e001-study/e001.md",
      "kind": "statistic",
      "sources": [
        {
          "source": "<results>",
          "locator": {"select": [["success_rate"]]}
        }
      ],
      "transformation": {
        "form": "percentage",
        "source": {"input": 0, "item": 0}
      }
    }
  ]
}
""",
    )
    write(
        entry_root / "pyrun-outputs.json",
        json.dumps(
            {
                "schema": "research-log-pyrun-outputs/v1",
                "outputs": {
                    "data/results.csv": {
                        "confirmed": True,
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": results_digest,
                        },
                        "inputs": {
                            "catalog": {
                                "algorithm": "sha256",
                                "digest": catalog_digest,
                            }
                        },
                        "parameters": [
                            "--catalog",
                            "<catalog>",
                            f"--{output_option}",
                            "data/results.csv",
                        ],
                        "script": {
                            "path": "scripts/model.py",
                            "fingerprint": {
                                "algorithm": "sha256",
                                "digest": hashlib.sha256(
                                    (entry_root / "scripts/model.py").read_bytes()
                                ).hexdigest(),
                            },
                        },
                    }
                },
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry,
        "# Entry e001\n\n"
        "## Trial\n\n"
        "`Background:`\n\nWhat is the success rate?\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/model.py --catalog '<catalog>' "
        f"--{output_option} data/results.csv\n"
        "```\n\n"
        "`Results:`\n\n"
        "The success rate was `67.6%`<!-- eid:success-rate -->.\n",
    )
    return summary, entry


def _comparison(evaluation: Any) -> dict[str, Any]:
    return {
        check.identity: CACHE.CheckComparisonEntry(
            check,
            CACHE.check_dependency(check, ENGINE.RULES_VERSION),
        )
        for check in evaluation.result.checks
        if check.status is RESULTS.CheckStatus.PASS and check.dependencies
    }


def _evaluate(summary: Path, *, check_comparison: dict[str, Any] | None = None) -> Any:
    return MECHANICAL.evaluate_mechanical(
        MECHANICAL.MechanicalEvaluationRequest(
            summary, "2026-08-29", check_comparison=check_comparison
        ),
        ENGINE.mechanical_policy(),
    )


def _origin_data_json(entry_root: Path) -> str:
    source = entry_root / "data/catalog.csv"
    write(source, "id\n1\n")
    return (
        json.dumps(
            {
                "schema": "research-log-data/v3",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": hashlib.sha256(source.read_bytes()).hexdigest(),
                        },
                        "origin": True,
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )


class EngineV2EndToEndTests(unittest.TestCase):
    def test_project_output_supports_generated_input_without_entering_hygiene(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            entry_root = entry.parent
            project_output = root / "artifacts/results.csv"
            project_output.parent.mkdir()
            (entry_root / "data/results.csv").replace(project_output)
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text())
            results = next(
                item for item in data["inputs"] if item["name"] == "results"
            )
            results["location"] = os.path.relpath(project_output, entry_root)
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text().replace(
                    "data/results.csv", "'<project>/artifacts/results.csv'"
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = support["outputs"].pop("data/results.csv")
            record["parameters"][-1] = "<project>/artifacts/results.csv"
            support["outputs"]["<project>/artifacts/results.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            complete = _evaluate(summary).result

            self.assertEqual(
                complete.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )
            self.assertFalse(
                any(
                    check.subject == project_output.as_posix()
                    for check in complete.checks
                )
            )

            support["outputs"]["<project>/artifacts/stale.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")
            self.assertEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )

            record["parameters"].append("changed")
            write(support_path, json.dumps(support, indent=2) + "\n")
            drift = _evaluate(summary).result
            provenance = next(
                check
                for check in drift.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.signature_mismatch",
            )

            record["parameters"].pop()
            write(support_path, json.dumps(support, indent=2) + "\n")
            project_output.unlink()
            missing = _evaluate(summary).result
            provenance = next(
                check
                for check in missing.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.failure.code, "provenance.output.missing")

    def test_validation_builds_one_directory_producer_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            builder = ENGINE.build_directory_producer_index

            with mock.patch.object(
                ENGINE,
                "build_directory_producer_index",
                wraps=builder,
            ) as indexed:
                _evaluate(summary)

            self.assertEqual(indexed.call_count, 1)

    def test_output_support_parameter_change_breaks_provenance_until_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "--catalog '<catalog>' ", "--catalog '<catalog>' --mode revised "
                ),
            )

            changed = _evaluate(summary).result

            provenance = next(
                check
                for check in changed.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)
            assert provenance.failure is not None
            self.assertEqual(
                provenance.failure.code, "provenance.output.signature_mismatch"
            )
            self.assertEqual(provenance.failure.observed["fields"], ["parameters"])

            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/results.csv"]["parameters"] = [
                "--catalog",
                "<catalog>",
                "--mode",
                "revised",
                "--output-data",
                "data/results.csv",
            ]
            write(output_path, json.dumps(support, indent=2) + "\n")
            self.assertEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )

    def test_recursive_chain_requires_each_link_and_uses_byte_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            intermediate = entry_root / "data" / "intermediate.csv"
            write(intermediate, "id\n1\n")
            write(entry_root / "scripts" / "preprocess.py", "# preprocess\n")
            data_path = entry_root / "data.json"
            data = json.loads(data_path.read_text())
            intermediate_digest = hashlib.sha256(intermediate.read_bytes()).hexdigest()
            data["inputs"].append(
                {
                    "name": "intermediate",
                    "kind": "file",
                    "location": "data/intermediate.csv",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": intermediate_digest,
                    },
                    "origin": False,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text().replace(
                    "./pyrun scripts/model.py --catalog '<catalog>' ",
                    "./pyrun scripts/preprocess.py --input-data '<catalog>' "
                    "--output-data data/intermediate.csv\n"
                    "./pyrun scripts/model.py --input-data '<intermediate>' ",
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            result_record = support["outputs"]["data/results.csv"]
            result_record["inputs"] = {
                "intermediate": {
                    "algorithm": "sha256",
                    "digest": intermediate_digest,
                }
            }
            result_record["parameters"] = [
                "--input-data",
                "<intermediate>",
                "--output-data",
                "data/results.csv",
            ]
            catalog = entry_root / "data" / "catalog.csv"
            support["outputs"]["data/intermediate.csv"] = {
                "confirmed": True,
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": intermediate_digest,
                },
                "inputs": {
                    "catalog": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                    }
                },
                "parameters": [
                    "--input-data",
                    "<catalog>",
                    "--output-data",
                    "data/intermediate.csv",
                ],
                "script": {
                    "path": "scripts/preprocess.py",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            (entry_root / "scripts" / "preprocess.py").read_bytes()
                        ).hexdigest(),
                    },
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            self.assertEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )
            original = catalog.read_bytes()
            write(catalog, "id\n2\n")
            self.assertNotEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )
            catalog.write_bytes(original)
            self.assertEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )
            write(entry_root / "scripts" / "preprocess.py", "# changed\n")
            self.assertNotEqual(
                _evaluate(summary).result.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )

    def test_unconfirmed_and_missing_output_records_fail_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/results.csv"]["confirmed"] = False
            write(output_path, json.dumps(support) + "\n")

            unconfirmed = _evaluate(summary).result
            check = next(
                item
                for item in unconfirmed.checks
                if item.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(check.failure.code, "provenance.output.unconfirmed")

            output_path.unlink()
            unrecorded = _evaluate(summary).result
            check = next(
                item
                for item in unrecorded.checks
                if item.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(check.failure.code, "provenance.output.unrecorded")

    def test_missing_output_takes_precedence_with_or_without_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            output = entry.parent / "data/results.csv"
            output.unlink()

            recorded = _evaluate(summary).result
            check = next(
                item
                for item in recorded.checks
                if item.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(check.failure.code, "provenance.output.missing")

            (entry.parent / "pyrun-outputs.json").unlink()
            unrecorded = _evaluate(summary).result
            check = next(
                item
                for item in unrecorded.checks
                if item.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(check.failure.code, "provenance.output.missing")

    def test_missing_graph_output_outside_evidence_closure_fails_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            text = entry.read_text(encoding="utf-8")
            write(
                entry,
                text.replace(
                    "```\n\n`Results:`",
                    "./pyrun scripts/model.py --catalog '<catalog>' "
                    "--output-data data/missing.csv\n"
                    "```\n\n`Results:`",
                ),
            )

            result = _evaluate(summary).result

            missing = [
                check
                for check in result.checks
                if check.failure is not None
                and check.failure.code == "provenance.output.missing"
                and check.subject.endswith("/data/missing.csv")
            ]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0].scope, RESULTS.CheckScope.PROVENANCE)

    def test_unmatched_record_is_one_hygiene_finding_not_an_orphan_duplicate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            stale = entry.parent / "data/stale.csv"
            write(stale, "stale\n")
            output_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(output_path.read_text())
            support["outputs"]["data/stale.csv"] = {
                **support["outputs"]["data/results.csv"],
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(stale.read_bytes()).hexdigest(),
                },
            }
            write(output_path, json.dumps(support) + "\n")

            result = _evaluate(summary).result
            stale_findings = [
                check
                for check in result.checks
                if check.subject == stale.resolve().as_posix()
                and check.failure is not None
            ]
            self.assertEqual(len(stale_findings), 1)
            self.assertEqual(
                stale_findings[0].failure.code, "hygiene.output.unmatched"
            )

    def test_input_verification_dependencies_include_the_entry_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "source.csv", "value\n1\n")
            resource = DATA.build_local_input(
                "source", "file", str(root / "source.csv"), entry_root=root
            )

            self.assertNotEqual(
                ENGINE._input_declaration_key("entries/first", resource),
                ENGINE._input_declaration_key("entries/second", resource),
            )

    def test_material_input_prerequisites_use_precomputed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            exact = (root / "exact.csv").as_posix()
            managed = (root / "managed").as_posix()
            exact_check = ENGINE._pass_check(
                "entry:e001:input:exact-declaration", RESULTS.CheckScope.PROVENANCE
            )
            directory_check = ENGINE._pass_check(
                "entry:e001:input:managed-declaration", RESULTS.CheckScope.PROVENANCE
            )
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.input_prerequisite_files[exact] = [exact_check]
            state.input_prerequisite_directories[managed] = [directory_check]
            state.logical_material_roots = (
                (root, "entries/2026-08-29-e001-study", ""),
            )
            state.command_blocker_candidates = (
                (
                    root / "managed",
                    True,
                    ("entry:e001:command:1:1",),
                ),
            )
            state.owner_surface_prerequisite_checks[
                "entries/2026-08-29-e001-study"
            ] = (exact_check,)

            with mock.patch.object(
                Path,
                "resolve",
                side_effect=AssertionError("lookup must not access the filesystem"),
            ):
                self.assertEqual(
                    ENGINE._material_input_prerequisites(exact, state), (exact_check,)
                )
                self.assertEqual(
                    ENGINE._material_input_prerequisites(
                        f"{managed}/nested/result.csv", state
                    ),
                    (directory_check,),
                )
                self.assertEqual(
                    ENGINE._material_input_prerequisites(
                        (root / "unrelated.csv").as_posix(), state
                    ),
                    (),
                )
                self.assertEqual(
                    ENGINE._logical_entry_material(exact, state),
                    ("entries/2026-08-29-e001-study", "exact.csv"),
                )
                self.assertEqual(
                    ENGINE._command_blockers(f"{managed}/nested/result.csv", state),
                    ("entry:e001:command:1:1",),
                )
                self.assertEqual(
                    ENGINE._owner_surface_prerequisites(
                        "entries/2026-08-29-e001-study", state
                    ),
                    (exact_check,),
                )

    def test_shared_input_observation_checks_every_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            write(source, "value\n1\n")
            correct = DATA.build_local_input(
                "first", "file", str(source), entry_root=root
            )
            wrong = replace(
                correct,
                name="second",
                fingerprint=DATA.Fingerprint("sha256", digest="0" * 64),
            )
            state = ENGINE._ScanState(root / "study.md", root, root)

            ENGINE._verify_input(correct, state)

            with self.assertRaisesRegex(
                DATA.DataContractError, "data.fingerprint.mismatch"
            ):
                ENGINE._verify_input(wrong, state)

    def test_cross_log_summary_link_is_not_an_owned_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            external_entry = (
                root / "docs/other/entries/2026-08-29-e005-other-study/e005.md"
            )
            write(external_entry, "# External entry\n")
            write(external_entry.parent / "evidence.json", "{}\n")
            write(
                summary,
                summary.read_text().replace(
                    "## Entries",
                    "See [external evidence]"
                    "(other/entries/2026-08-29-e005-other-study/e005.md).\n\n"
                    "## Entries",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertFalse(
                any("e005" in check.identity for check in evaluation.result.checks)
            )

    def test_symlinked_entry_root_is_rejected_lexically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            lexical_root = entry.parent
            physical_root = lexical_root.with_name(f"{lexical_root.name}-physical")
            lexical_root.rename(physical_root)
            lexical_root.symlink_to(physical_root.name, target_is_directory=True)

            evaluation = _evaluate(summary)

            failure = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:declaration"
            )
            self.assertEqual(failure.status, RESULTS.CheckStatus.FAIL)
            assert failure.failure is not None
            self.assertEqual(failure.failure.code, "evidence.declaration.invalid")

    def test_invalid_entry_evidence_does_not_block_valid_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            bad_root = root / "docs/study/entries/2026-08-30-e002-invalid"
            bad_entry = bad_root / "e002.md"
            write(bad_entry, "# Invalid entry\n")
            write(bad_root / "evidence.json", "{\n")
            write(
                summary,
                summary.read_text().replace(
                    "- [Study trial]",
                    "- [Invalid entry]"
                    "(study/entries/2026-08-30-e002-invalid/e002.md)\n"
                    "- [Study trial]",
                ),
            )

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            invalid = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e002:evidence-declaration"
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(invalid.scope, RESULTS.CheckScope.CONFORMANCE)
            self.assertEqual(invalid.failure.code, "evidence.json.schema_invalid")
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "association.declaration_missing"
                    and "e002" in check.identity
                    for check in evaluation.result.checks
                )
            )

    def test_invalid_retention_preserves_commands_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "retention.json", "{\n")

            evaluation = _evaluate(summary)

            invalid = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:retention-declaration"
            )
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(invalid.failure.code, "retention.declaration.invalid")
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 1)

    def test_invalid_input_preserves_unrelated_inputs_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            bad_source = entry.parent / "data" / "bad.csv"
            write(bad_source, "value\n1\n")
            data_path = entry.parent / "data.json"
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            payload["inputs"].append(
                {
                    "name": "unused-bad",
                    "kind": "file",
                    "location": "data/bad.csv",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": "0" * 64,
                    },
                    "origin": True,
                }
            )
            write(data_path, json.dumps(payload, indent=2) + "\n")

            evaluation = _evaluate(summary)

            invalid = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:input:unused-bad-declaration"
            )
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(invalid.failure.code, "data.fingerprint.mismatch")
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 1)

    def test_invalid_command_input_blocks_its_provenance_without_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            catalog_path = entry.parent / "inputs/catalog.csv"
            write(catalog_path, "value\n1\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog.update(
                {
                    "location": "inputs/catalog.csv",
                    "fingerprint": {"algorithm": "sha256", "digest": "0" * 64},
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            declaration = checks["entry:e001:input:catalog-declaration"]
            command = checks["entry:e001:command:1:1"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(command.status, RESULTS.CheckStatus.NOT_APPLICABLE)
            self.assertIn({"dependency": declaration.identity}, command.dependencies)
            self.assertEqual(
                checks["evidence:e001:success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.NOT_APPLICABLE)
            self.assertIn({"dependency": command.identity}, provenance.dependencies)
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("producer.missing", failure_codes)
            self.assertIn("hygiene.output.unmatched", failure_codes)

    def test_invalid_data_file_blocks_dependent_checks_without_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data.json", "{\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            declaration = checks["entry:e001:data-declaration"]
            for identity in (
                "entry:e001:command:1:1",
                "evidence:e001:success-rate",
                "provenance:e001:success-rate",
            ):
                self.assertEqual(
                    checks[identity].status, RESULTS.CheckStatus.NOT_APPLICABLE
                )
                self.assertIn(
                    {"dependency": declaration.identity}, checks[identity].dependencies
                )
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.material.unused", failure_codes)

    def test_invalid_evidence_file_blocks_owner_orphan_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "data/results.csv"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            declaration = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:evidence-declaration"
            )
            orphan_checks = [
                check
                for check in evaluation.result.checks
                if check.scope is RESULTS.CheckScope.ORPHAN
            ]
            self.assertTrue(orphan_checks)
            for check in orphan_checks:
                self.assertEqual(check.status, RESULTS.CheckStatus.NOT_APPLICABLE)
                self.assertIn(
                    {"dependency": declaration.identity}, check.dependencies
                )
            self.assertFalse(
                any(check.failure is not None for check in orphan_checks)
            )

    def test_conflicted_input_blocks_consumers_without_undeclared_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            shared = root / "inputs/catalog.csv"
            write(shared, "success_rate\n0.676\n")
            digest = hashlib.sha256(shared.read_bytes()).hexdigest()
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog.update(
                {
                    "location": shared.as_posix(),
                    "fingerprint": {"algorithm": "sha256", "digest": digest},
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            second_root = root / "docs/study/entries/2026-08-30-e002-conflict"
            write(second_root / "e002.md", "# Entry e002\n")
            write(
                second_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v3",
                        "inputs": [
                            {
                                "name": "shared-catalog",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "fingerprint": {
                                    "algorithm": "sha256",
                                    "digest": digest,
                                },
                                "origin": False,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "\n- [Conflict]"
                "(study/entries/2026-08-30-e002-conflict/e002.md)\n",
            )

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            conflict = next(
                check
                for check in evaluation.result.checks
                if check.identity.startswith("conformance:data-conflict:")
            )
            for identity in (
                "entry:e001:command:1:1",
                "evidence:e001:success-rate",
                "provenance:e001:success-rate",
            ):
                self.assertEqual(
                    checks[identity].status, RESULTS.CheckStatus.NOT_APPLICABLE
                )
                self.assertIn(
                    {"dependency": conflict.identity}, checks[identity].dependencies
                )
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.input.unused", failure_codes)
            self.assertIn("hygiene.output.unmatched", failure_codes)

    def test_cross_entry_data_conflict_does_not_block_unrelated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            conflict_path = root / "shared-conflict.csv"
            write(conflict_path, "conflict\n")
            conflict_digest = hashlib.sha256(conflict_path.read_bytes()).hexdigest()
            links: list[str] = []
            for entry_id, date, identity in (
                ("e002", "2026-08-30", "conflict/v1"),
                ("e003", "2026-08-31", "conflict/v2"),
            ):
                entry_root = root / f"docs/study/entries/{date}-{entry_id}-conflict"
                write(entry_root / "scripts/model.py", "# fixture\n")
                safe_path = entry_root / f"data/{entry_id}.csv"
                write(safe_path, f"safe/{entry_id}")
                write(
                    entry_root / f"{entry_id}.md",
                    f"# Entry {entry_id}\n\n## Trial\n\n`Steps:`\n\n"
                    "```bash\n"
                    f"./pyrun scripts/model.py --input-data '<safe-{entry_id}>' "
                    "--output-data data/result.csv\n"
                    "```\n\n`Results:`\n\nDone.\n",
                )
                write(
                    entry_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v3",
                            "inputs": [
                                {
                                    "name": "conflict",
                                    "kind": "file",
                                    "location": conflict_path.as_posix(),
                                    "fingerprint": {
                                        "algorithm": "sha256",
                                        "digest": conflict_digest,
                                    },
                                    "origin": entry_id == "e002",
                                },
                                {
                                    "name": f"safe-{entry_id}",
                                    "kind": "file",
                                    "location": (
                                        f"data/{entry_id}.csv"
                                    ),
                                    "fingerprint": {
                                        "algorithm": "sha256",
                                        "digest": hashlib.sha256(
                                            safe_path.read_bytes()
                                        ).hexdigest(),
                                    },
                                    "origin": True,
                                },
                            ],
                        }
                    )
                    + "\n",
                )
                links.append(
                    f"- [{entry_id}]"
                    f"(study/entries/{date}-{entry_id}-conflict/{entry_id}.md)"
                )
            write(summary, summary.read_text() + "\n" + "\n".join(links) + "\n")

            evaluation = _evaluate(summary)

            conflict = next(
                check
                for check in evaluation.result.checks
                if check.identity.startswith("conformance:data-conflict:")
            )
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(conflict.status, RESULTS.CheckStatus.FAIL)
            assert conflict.failure is not None
            self.assertEqual(conflict.failure.code, "data.declaration.conflict")
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 3)

    def test_invalid_entry_command_does_not_block_valid_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            bad_root = root / "docs/study/entries/2026-08-30-e002-invalid"
            bad_entry = bad_root / "e002.md"
            write(
                bad_entry,
                "# Invalid entry\n\n## Trial\n\n`Steps:`\n\n"
                "```bash\ntool --output-data data/result.csv\n```\n"
                "<!-- command type=model -->\n\n`Results:`\n\nDone.\n",
            )
            write(
                summary,
                summary.read_text().replace(
                    "- [Study trial]",
                    "- [Invalid entry]"
                    "(study/entries/2026-08-30-e002-invalid/e002.md)\n"
                    "- [Study trial]",
                ),
            )

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            invalid = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e002:command"
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(invalid.scope, RESULTS.CheckScope.CONFORMANCE)
            self.assertEqual(invalid.failure.code, "invocation.annotation.invalid")

    def test_split_entry_loads_shared_root_surfaces_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            summary = project_root / "docs" / "study.md"
            log_root = project_root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            write(first, "# First\n")
            write(second, "# Second\n")
            write(entry_root / "evidence.json", "{}\n")
            write(entry_root / "data.json", _origin_data_json(entry_root))
            state = ENGINE._ScanState(summary, log_root, project_root)
            evidence = object()

            with (
                mock.patch.object(
                    ENGINE, "load_evidence_file", return_value=evidence
                ) as evidence_loader,
                mock.patch.object(
                    ENGINE, "load_data_file", wraps=ENGINE.load_data_file
                ) as data_loader,
            ):
                entries = ENGINE._entries(summary.read_text(), state)

            evidence_loader.assert_called_once()
            data_loader.assert_called_once()
            self.assertIs(entries[0].evidence_file, entries[1].evidence_file)
            self.assertIs(entries[0].data_file, entries[1].data_file)

    def test_split_entry_commands_are_discovered_once_per_listed_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            log_root = project_root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            command = (
                "## Trial\n\n"
                "`Steps:`\n\n"
                "```bash\ntool --output-data data/result.csv\n```\n\n"
                "`Results:`\n\nDone.\n"
            )
            write(first, command)
            write(second, command)
            entries = [
                ENGINE._Entry("e001a", first, entry_root, None, None, None),
                ENGINE._Entry("e001b", second, entry_root, None, None, None),
            ]
            state = ENGINE._ScanState(
                project_root / "docs" / "study.md", log_root, project_root
            )
            state.entries = entries

            invocations = ENGINE._discover_invocations(state)

            self.assertEqual(len(invocations), 2)
            self.assertEqual([item.entry for item in invocations], ["e001a", "e001b"])
            self.assertEqual(
                [item.document for item in invocations],
                [
                    "entries/2026-08-29-e001-study/e001a.md",
                    "entries/2026-08-29-e001-study/e001b.md",
                ],
            )
            self.assertEqual(
                {item.material_owner for item in invocations},
                {"entries/2026-08-29-e001-study"},
            )

    def test_split_entry_shares_data_index_usage_without_false_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "study.md"
            log_root = root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            first = entry_root / "e001a.md"
            second = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            write(entry_root / "data.json", _origin_data_json(entry_root))
            write(
                first,
                "## Trial\n\n`Steps:`\n\n```bash\ntool --label baseline\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            write(
                second,
                "## Trial\n\n`Steps:`\n\n"
                "```bash\ntool --catalog '<catalog>'\n```\n\n"
                "`Results:`\n\nDone.\n",
            )

            evaluation = _evaluate(summary)

            failures = [
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            ]
            self.assertNotIn("orphan.input.unused", failures)
            self.assertEqual(evaluation.metrics["invocations"], 2)

    def test_unlisted_split_document_evidence_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "study.md"
            log_root = root / "docs" / "study"
            entry_root = log_root / "entries" / "2026-08-29-e001-study"
            listed = entry_root / "e001a.md"
            unlisted = entry_root / "e001b.md"
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Listed](study/entries/2026-08-29-e001-study/e001a.md)\n",
            )
            write(
                listed,
                "## Trial\n\n`Steps:`\n\n```bash\ntool --label baseline\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            write(
                unlisted,
                "## Trial\n\n`Steps:`\n\nNo command.\n\n`Results:`\n\n"
                "Value: `1`<!-- eid:unlisted-value -->.\n",
            )
            write(entry_root / "data" / "result.csv", "value\n1\n")
            write(
                entry_root / "evidence.json",
                """{
  "schema": "research-log-evidence/v3",
  "records": [
    {
      "id": "unlisted-value",
      "document": "entries/2026-08-29-e001-study/e001b.md",
      "kind": "statistic",
      "sources": [
        {"source": "<result>", "locator": {"select": [["value"]]}}
      ],
      "transformation": null
    }
  ]
}
""",
            )

            evaluation = _evaluate(summary)

            failures = [
                check.failure
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "association.presentation_missing"
            ]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].observed["ids"], ["unlisted-value"])

    def test_complete_log_is_mechanically_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            scopes = {item.scope: item.status for item in evaluation.result.scopes}
            self.assertEqual(
                scopes[RESULTS.CheckScope.EVIDENCE], RESULTS.CheckStatus.PASS
            )
            self.assertEqual(
                scopes[RESULTS.CheckScope.PROVENANCE], RESULTS.CheckStatus.PASS
            )
            self.assertEqual(
                scopes[RESULTS.CheckScope.ORPHAN], RESULTS.CheckStatus.PASS
            )
            self.assertEqual(evaluation.metrics["source_evaluations"], 1)
            self.assertEqual(evaluation.metrics["source_reads"], 1)
            self.assertEqual(evaluation.metrics["script_hashes"], 1)
            self.assertEqual(evaluation.metrics["markdown_reads"], 2)
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertIn(
                {
                    "context": {
                        "classification": "experimental",
                        "classifier_version": "entry-section-labels/1",
                        "under_results": True,
                    }
                },
                evidence.dependencies,
            )

    def test_generated_evidence_input_rejects_an_origin_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["origin"] = True
            write(data_path, json.dumps(data, indent=2) + "\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)
            assert provenance.failure is not None
            self.assertEqual(provenance.failure.code, "data.origin.invalid")

    def test_origin_evidence_is_valid_but_unrelated_output_is_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog_path = entry.parent / "data/catalog.csv"
            write(catalog_path, "success_rate\n0.676\n")
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog["fingerprint"]["digest"] = hashlib.sha256(
                catalog_path.read_bytes()
            ).hexdigest()
            data["inputs"] = [
                item for item in data["inputs"] if item["name"] == "catalog"
            ]
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence_data = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence_data["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence_data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    " --catalog '<catalog>'", ""
                ),
            )

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            self.assertEqual(
                evaluation.result.completion,
                RESULTS.CompletionState.COMPLETE_FINDINGS,
            )
            self.assertEqual(
                checks["evidence:e001:success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            self.assertEqual(
                checks["provenance:e001:success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_changed_generated_evidence_fails_provenance_and_blocks_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data/results.csv", "success_rate\n0.675\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            declaration = checks["entry:e001:input:results-declaration"]
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            assert declaration.failure is not None
            self.assertEqual(declaration.failure.code, "data.fingerprint.mismatch")
            self.assertEqual(evidence.status, RESULTS.CheckStatus.NOT_APPLICABLE)
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)
            assert provenance.failure is not None
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.signature_mismatch",
            )
            self.assertIn(
                {"dependency": declaration.identity}, evidence.dependencies
            )
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("data.input.undeclared", failure_codes)
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_decimal_locator_is_retained_exactly_in_record_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["locator"]["where"] = [
                {
                    "op": "eq",
                    "parse": "decimal",
                    "path": ["threshold"],
                    "value": 0.676,
                }
            ]
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry.parent / "data" / "results.csv",
                "threshold,success_rate\n0.676,0.676\n",
            )
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["fingerprint"]["digest"] = hashlib.sha256(
                (entry.parent / "data" / "results.csv").read_bytes()
            ).hexdigest()
            write(data_path, json.dumps(data, indent=2) + "\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/results.csv"]["fingerprint"]["digest"] = (
                results["fingerprint"]["digest"]
            )
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            evidence_check = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            record = evidence_check.dependencies[0]["record"]
            self.assertIsInstance(record, str)
            self.assertIn('"value":0.676', record)

    def test_invalid_section_is_reported_while_valid_section_evaluates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8")
                + "\n## Incomplete appendix\n\n`Steps:`\n\nNo result was retained.\n",
            )

            evaluation = _evaluate(summary)

            invalid = [
                check
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "association.context_invalid"
            ]
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid[0].scope, RESULTS.CheckScope.CONFORMANCE)
            self.assertEqual(
                invalid[0].failure.observed["heading"], "Incomplete appendix"
            )
            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)

    def test_association_syntax_and_context_failures_are_conformance(self) -> None:
        for code in (
            "association.context_invalid",
            "association.presentation.syntax_invalid",
        ):
            with self.subTest(code=code):
                error = ENGINE.EngineV2Error(code, "entry", {}, "rule")
                self.assertEqual(
                    ENGINE._error_scope(error, RESULTS.CheckScope.EVIDENCE),
                    RESULTS.CheckScope.CONFORMANCE,
                )

    def test_symlinked_entry_material_root_is_mechanically_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            retained = root / "output" / "logs" / "study" / "e001" / "data"
            retained.parent.mkdir(parents=True)
            (entry.parent / "data").rename(retained)
            relative_target = os.path.relpath(retained, entry.parent)
            (entry.parent / "data").symlink_to(
                relative_target, target_is_directory=True
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            scopes = {item.scope: item.status for item in evaluation.result.scopes}
            self.assertEqual(
                scopes[RESULTS.CheckScope.ORPHAN],
                RESULTS.CheckStatus.PASS,
            )

    def test_cross_entry_source_uses_the_consuming_entry_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            split_entry = entry.with_name("e001a.md")
            entry.rename(split_entry)
            entry = split_entry
            evidence_path = entry.parent / "evidence.json"
            evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence_payload["records"][0]["document"] = (
                "entries/2026-08-29-e001-study/e001a.md"
            )
            write(evidence_path, json.dumps(evidence_payload, indent=2) + "\n")
            write(
                summary,
                summary.read_text(encoding="utf-8")
                .replace("e001.md", "e001a.md")
                .replace("ref entry = e001;", "ref entry = e001a;"),
            )
            retained = root / "output" / "logs" / "study" / "e001" / "data"
            retained.parent.mkdir(parents=True)
            (entry.parent / "data").rename(retained)
            relative_target = os.path.relpath(retained, entry.parent)
            (entry.parent / "data").symlink_to(
                relative_target, target_is_directory=True
            )

            second_root = root / "docs/study/entries/2026-08-29-e002-cross-entry-study"
            second_entry = second_root / "e002.md"
            write(
                second_entry,
                "## Trial\n\n`Steps:`\n\nNo command.\n\n`Results:`\n\n"
                "The prior success rate was `67.6%`"
                "<!-- eid:prior-success-rate -->.\n",
            )
            write(
                second_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v3",
                        "inputs": [
                            {
                                "name": "prior-results",
                                "kind": "file",
                                "location": os.path.relpath(
                                    retained / "results.csv", second_root
                                ),
                                "fingerprint": {
                                    "algorithm": "sha256",
                                    "digest": hashlib.sha256(
                                        (retained / "results.csv").read_bytes()
                                    ).hexdigest(),
                                },
                                "origin": False,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                second_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v3",
                        "records": [
                            {
                                "id": "prior-success-rate",
                                "document": (
                                    "entries/2026-08-29-e002-cross-entry-study/e002.md"
                                ),
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<prior-results>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [Cross-entry study](study/entries/"
                "2026-08-29-e002-cross-entry-study/e002.md)\n",
            )

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            self.assertIn(
                "evidence:e002:prior-success-rate",
                checks,
                [
                    (check.identity, check.failure)
                    for check in evaluation.result.checks
                    if "e002" in check.identity
                ],
            )
            evidence = checks["evidence:e002:prior-success-rate"]
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            provenance = checks["provenance:e002:prior-success-rate"]
            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)

            second_evidence_path = second_root / "evidence.json"
            second_payload = json.loads(
                second_evidence_path.read_text(encoding="utf-8")
            )
            second_payload["records"][0]["sources"][0]["source"] = (
                "<log>/entries/2026-08-29-e001-study/data/results.csv"
            )
            write(
                second_evidence_path,
                json.dumps(second_payload, indent=2) + "\n",
            )

            log_relative_evaluation = _evaluate(summary)
            declaration = next(
                check
                for check in log_relative_evaluation.result.checks
                if check.identity == "entry:e002:evidence-declaration"
            )
            self.assertEqual(declaration.scope, RESULTS.CheckScope.CONFORMANCE)
            assert declaration.failure is not None
            self.assertEqual(declaration.failure.code, "evidence.declaration.invalid")

    def test_evidence_directory_requires_one_exact_regular_file_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            evidence_directory = entry_root / "data/evidence"
            evidence_directory.mkdir()
            (entry_root / "data/results.csv").replace(
                evidence_directory / "results.csv"
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "data/results.csv", "data/evidence/results.csv"
                ),
            )
            data_path = entry_root / "data.json"
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            payload["inputs"] = [
                DATA.build_local_input(
                    "results-dir",
                    "directory",
                    "data/evidence",
                    entry_root=entry_root,
                ).as_dict()
            ]
            write(data_path, json.dumps(payload, indent=2) + "\n")
            evidence_path = entry_root / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["source"] = "<results-dir>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            bare = _evaluate(summary)

            declaration = next(
                check
                for check in bare.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(declaration.status, RESULTS.CheckStatus.FAIL)
            assert declaration.failure is not None
            self.assertEqual(declaration.failure.code, "evidence.declaration.invalid")

            evidence["records"][0]["sources"][0]["source"] = "<results-dir>/results.csv"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            member = _evaluate(summary)

            checks = {check.identity: check for check in member.result.checks}
            self.assertEqual(
                checks["evidence:e001:success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            failure_codes = {
                check.failure.code
                for check in member.result.checks
                if check.failure is not None
            }
            self.assertNotIn("orphan.input.unused", failure_codes)

    def test_distinct_locators_share_one_stable_source_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            second = dict(payload["records"][0])
            second["id"] = "success-rate-checked"
            second["sources"] = [dict(second["sources"][0])]
            second["sources"][0]["locator"] = {
                "expect": {"items": 1},
                "select": [["success_rate"]],
            }
            payload["records"].append(second)
            write(evidence_path, json.dumps(payload, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was `67.6%`<!-- eid:success-rate -->.",
                    "The success rate was `67.6%`<!-- eid:success-rate -->.\n\n"
                    "The checked rate was `67.6%`<!-- eid:success-rate-checked -->.",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            self.assertEqual(evaluation.metrics["source_evaluations"], 2)
            self.assertEqual(evaluation.metrics["source_reads"], 1)

    def test_generated_artifact_uses_evidence_and_enters_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "report.txt", "retained report\n")
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace(
                    "--output-data data/results.csv",
                    "--output-data data/results.csv --output-report data/report.txt",
                )
                .replace(
                    "The success rate was",
                    "[Retained report](data/report.txt)"
                    "<!-- eid:retained-report -->\n\nThe success rate was",
                ),
            )
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            data["inputs"].append(
                {
                    "name": "report",
                    "kind": "file",
                    "location": "data/report.txt",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            (entry.parent / "data/report.txt").read_bytes()
                        ).hexdigest(),
                    },
                    "origin": False,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "retained-report",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<report>", "locator": None}],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            parameters = [
                "--catalog",
                "<catalog>",
                "--output-data",
                "data/results.csv",
                "--output-report",
                "data/report.txt",
            ]
            support["outputs"]["data/results.csv"]["parameters"] = parameters
            support["outputs"]["data/report.txt"] = {
                **support["outputs"]["data/results.csv"],
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(
                        (entry.parent / "data/report.txt").read_bytes()
                    ).hexdigest(),
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            checks = {check.identity: check for check in evaluation.result.checks}
            self.assertEqual(
                checks["evidence:e001:retained-report"].status,
                RESULTS.CheckStatus.PASS,
            )
            self.assertEqual(
                checks["provenance:e001:retained-report"].status,
                RESULTS.CheckStatus.PASS,
            )

    def test_unmarked_artifact_requires_an_evidence_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "report.txt", "retained report\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "[Retained report](data/report.txt)\n\nThe success rate was",
                ),
            )

            evaluation = _evaluate(summary)

            failures = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertIn("association.declaration_missing", failures)

    def test_artifact_evidence_may_use_an_explicit_origin_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            report = entry.parent / "data" / "historical-report.txt"
            write(report, "retained historical report\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            data["inputs"].append(
                {
                    "name": "historical_report",
                    "kind": "file",
                    "location": "data/historical-report.txt",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(report.read_bytes()).hexdigest(),
                    },
                    "origin": True,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "[Historical report](data/historical-report.txt)"
                    "<!-- eid:historical-report -->\n\nThe success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "historical-report",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [
                        {"source": "<historical_report>", "locator": None}
                    ],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            failures = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("producer.missing", failures)
            self.assertNotIn("orphan.input.unused", failures)

    def test_artifact_association_uses_path_not_equal_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = entry.parent / "data" / "first.png"
            second = entry.parent / "data" / "second.png"
            write(first, "same bytes\n")
            write(second, "same bytes\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            for name, path in (("first", first), ("second", second)):
                data["inputs"].append(
                    {
                        "name": name,
                        "kind": "file",
                        "location": f"data/{path.name}",
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
                        },
                        "origin": True,
                    }
                )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "![First](data/first.png)<!-- eid:first-image -->\n\n"
                    "The success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "first-image",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<second>", "locator": None}],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            check = next(
                item
                for item in evaluation.result.checks
                if item.identity == "evidence:e001:first-image"
            )
            self.assertEqual(check.status, RESULTS.CheckStatus.FAIL)
            assert check.failure is not None
            self.assertEqual(
                check.failure.code, "association.artifact.source_mismatch"
            )

    def test_unavailable_and_resource_limit_completion_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            unavailable = LOCATOR.LocatorV2Error(
                "locator.reader.unavailable",
                "data/results.csv",
                {"error": "temporarily unavailable"},
                "V2: Expanded Mechanical Locator Language",
                outcome="unavailable",
            )
            with mock.patch.object(
                ENGINE, "observe_source_identity", side_effect=unavailable
            ):
                evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.INCOMPLETE
            )
            self.assertIn(
                "locator.reader.unavailable",
                [
                    check.failure.code
                    for check in evaluation.result.checks
                    if check.failure is not None
                ],
            )

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            with mock.patch.object(LOCATOR, "MAX_TEXT_OR_JSON_BYTES", 4):
                evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion,
                RESULTS.CompletionState.COMPLETE_FINDINGS,
            )
            self.assertIn(
                "locator.source.too_large",
                [
                    check.failure.code
                    for check in evaluation.result.checks
                    if check.failure is not None
                ],
            )

    def test_log_level_record_marker_and_summary_bounds_are_composed(self) -> None:
        limits = (
            ("MAX_RECORDS_PER_LOG", RESULTS.CheckScope.CONFORMANCE),
            ("MAX_PRESENTATIONS_PER_LOG", RESULTS.CheckScope.CONFORMANCE),
            ("MAX_SUMMARY_REFERENCES_PER_LOG", RESULTS.CheckScope.CONFORMANCE),
        )
        for constant, scope in limits:
            with self.subTest(constant=constant):
                with tempfile.TemporaryDirectory() as directory:
                    summary, _ = _log(Path(directory))
                    with mock.patch.object(ENGINE, constant, 0):
                        evaluation = _evaluate(summary)
                failures = [
                    check
                    for check in evaluation.result.checks
                    if check.failure is not None
                    and check.failure.code == "association.resource.too_large"
                ]
                self.assertTrue(failures)
                self.assertEqual(failures[0].scope, scope)

    def test_evidence_passes_while_missing_producer_fails_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "./pyrun scripts/model.py --catalog '<catalog>' "
                    "--output-data data/results.csv",
                    "true",
                ),
            )

            evaluation = _evaluate(summary)

            scopes = {item.scope: item.status for item in evaluation.result.scopes}
            self.assertEqual(
                scopes[RESULTS.CheckScope.EVIDENCE], RESULTS.CheckStatus.PASS
            )
            self.assertEqual(
                scopes[RESULTS.CheckScope.PROVENANCE], RESULTS.CheckStatus.FAIL
            )
            failures = [
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            ]
            self.assertIn("producer.missing", failures)

    def test_failed_command_candidates_block_dependent_graph_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            scratch = entry.parent / "data/scratch.csv"
            write(scratch, "value\n1\n")
            write(
                entry,
                entry.read_text().replace(
                    "--results data/results.csv",
                    "--results data/results.csv --scratch data/scratch.csv",
                ),
            )

            evaluation = _evaluate(summary)

            command = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:command:1:1"
            )
            provenance = next(
                check
                for check in evaluation.result.checks
                if check.identity == "provenance:e001:success-rate"
            )
            scratch_orphan = next(
                check
                for check in evaluation.result.checks
                if check.identity.endswith("data/scratch.csv")
                and check.scope is RESULTS.CheckScope.ORPHAN
            )
            unused_input = next(
                check
                for check in evaluation.result.checks
                if check.identity.endswith(":catalog")
            )
            self.assertEqual(command.status, RESULTS.CheckStatus.FAIL)
            self.assertEqual(command.failure.code, "material.candidate.unresolved")
            for dependent in (provenance, scratch_orphan, unused_input):
                self.assertEqual(dependent.status, RESULTS.CheckStatus.NOT_APPLICABLE)
                self.assertIn({"dependency": command.identity}, dependent.dependencies)

    def test_failed_command_does_not_hide_later_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```bash\n./pyrun",
                    "```bash\ntool --output-data 'data/${missing}.csv'\n./pyrun",
                ),
            )

            evaluation = _evaluate(summary)

            command_failure = next(
                check
                for check in evaluation.result.checks
                if check.identity == "entry:e001:command:1:1"
            )
            provenance = next(
                check
                for check in evaluation.result.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(command_failure.status, RESULTS.CheckStatus.FAIL)
            self.assertEqual(command_failure.failure.code, "material.unresolved")
            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)

    def test_adjacent_annotation_is_a_complete_mechanical_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```\n\n`Results:`",
                    "```\n<!-- command results = output -->\n\n`Results:`",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

    def test_pyrun_other_roles_are_shared_with_static_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py --catalog '<catalog>' "
                    "--results data/results.csv",
                    "./pyrun --other-inputs catalog --other-outputs results -- "
                    "scripts/model.py --catalog '<catalog>' "
                    "--results data/results.csv",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

    def test_missing_summary_reference_is_precise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            write(
                summary,
                summary.read_text(encoding="utf-8").replace(
                    "<!-- ref entry = e001; eid = success-rate -->", ""
                ),
            )

            missing = _evaluate(summary)

            failures = [
                (check.scope, check.failure.code)
                for check in missing.result.checks
                if check.failure is not None
            ]
            self.assertIn(
                (RESULTS.CheckScope.EVIDENCE, "summary.reference.missing"), failures
            )

    def test_changed_script_bytes_break_execution_linked_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = _evaluate(summary)
            write(entry.parent / "scripts" / "model.py", "raise RuntimeError\n")
            second = _evaluate(summary)

            first_provenance = [
                check.status
                for check in first.result.checks
                if check.scope is RESULTS.CheckScope.PROVENANCE
            ]
            second_provenance = [
                check.status
                for check in second.result.checks
                if check.scope is RESULTS.CheckScope.PROVENANCE
            ]
            self.assertTrue(
                all(
                    status is RESULTS.CheckStatus.PASS
                    for status in first_provenance
                )
            )
            self.assertTrue(
                any(
                    status is RESULTS.CheckStatus.FAIL
                    for status in second_provenance
                )
            )

    def test_input_changed_during_validation_is_unavailable_without_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            catalog = entry.parent / "data" / "catalog.csv"
            original_compose = ENGINE._compose_graph

            def change_after_graph(state: Any) -> None:
                original_compose(state)
                write(catalog, "id\n2\n")

            with mock.patch.object(
                ENGINE, "_compose_graph", side_effect=change_after_graph
            ):
                evaluation = _evaluate(summary)

            failures = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertIn("provenance.observation.unavailable", failures)

    def test_automatic_simulation_and_untyped_terminal_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            script = entry.parent / "scripts" / "model.py"
            simulation = entry.parent / "scripts" / "simulate_trials.py"
            script.rename(simulation)
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace("scripts/model.py", "scripts/simulate_trials.py")
                .replace("\n<!-- command type = model -->", ""),
            )
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = support["outputs"]["data/results.csv"]
            record["script"]["path"] = "scripts/simulate_trials.py"
            record["script"]["fingerprint"]["digest"] = hashlib.sha256(
                simulation.read_bytes()
            ).hexdigest()
            write(support_path, json.dumps(support, indent=2) + "\n")

            recognized = _evaluate(summary)

            self.assertEqual(
                recognized.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace(" --catalog '<catalog>'", "")
                .replace("\n<!-- command type = model -->", ""),
            )

            untyped = _evaluate(summary)

            codes = [
                check.failure.code
                for check in untyped.result.checks
                if check.failure is not None
            ]
            self.assertNotIn("provenance.root.missing", codes)
            self.assertIn("orphan.input.unused", codes)

    def test_typed_command_does_not_hide_unrooted_visible_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "data" / "unrooted.csv", "value\n1\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--catalog '<catalog>' ",
                    "--catalog '<catalog>' --input-data data/unrooted.csv ",
                ),
            )
            evaluation = _evaluate(summary)

            codes = [
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            ]
            self.assertIn("data.input.undeclared", codes)

    def test_cross_log_evidence_reads_only_the_external_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            external_root = root / "docs" / "other"
            external_source = external_root / "entries/e001/data/results.csv"
            external_state = external_root / "validation/manifest.json"
            write(external_source, "success_rate\n0.676\n")
            write(external_state, "not valid validation state\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"].append(
                {
                    "name": "external-results",
                    "kind": "file",
                    "location": os.path.relpath(external_source, entry.parent),
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            external_source.read_bytes()
                        ).hexdigest(),
                    },
                    "origin": True,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"][0]["sources"][0]["source"] = "<external-results>"
            write(evidence_path, json.dumps(evidence) + "\n")
            original_read_bytes = Path.read_bytes
            original_read_text = Path.read_text

            def guarded_read_bytes(path: Path) -> bytes:
                if path.resolve() == external_state.resolve():
                    raise AssertionError("external validation state was read")
                return original_read_bytes(path)

            def guarded_read_text(
                path: Path, encoding: str | None = None, errors: str | None = None
            ) -> str:
                if path.resolve() == external_state.resolve():
                    raise AssertionError("external validation state was read")
                return original_read_text(path, encoding=encoding, errors=errors)

            with mock.patch.object(Path, "read_bytes", guarded_read_bytes):
                with mock.patch.object(Path, "read_text", guarded_read_text):
                    evaluation = _evaluate(summary)

            evidence_check = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            provenance_check = next(
                check
                for check in evaluation.result.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(evidence_check.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(provenance_check.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(evaluation.metrics["source_reads"], 1)

    def test_malformed_annotation_has_complete_precise_failure_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```\n\n`Results:`",
                    "```\n<!-- command type=model -->\n\n`Results:`",
                ),
            )

            evaluation = _evaluate(summary)

            failure = next(
                check.failure
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "invocation.annotation.invalid"
            )
            self.assertTrue(failure.subject)
            self.assertTrue(failure.observed)
            self.assertEqual(
                failure.rule, "Recorded-Command Provenance And Material Graph"
            )

    def test_engine_has_no_semantic_review_or_reproduction_import(self) -> None:
        source = Path(ENGINE.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "adjudication",
            "decisions",
            "review_exchange",
            "review_reuse",
            "reproduction",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"validation.{forbidden}", source)
                self.assertNotIn(f"from .{forbidden}", source)

        self.assertNotIn("from .discovery", source)

    def test_unchanged_dependency_results_match_and_changed_script_reopens(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = _evaluate(summary)
            comparison = _comparison(first)

            unchanged = _evaluate(summary, check_comparison=comparison)
            write(entry.parent / "scripts" / "model.py", "# changed identity\n")
            changed = _evaluate(summary, check_comparison=comparison)

            self.assertGreater(unchanged.metrics["checks_unchanged"], 0)
            self.assertLess(
                changed.metrics["checks_unchanged"],
                unchanged.metrics["checks_unchanged"],
            )
            self.assertEqual(
                changed.result.completion, RESULTS.CompletionState.COMPLETE_FINDINGS
            )

    def test_unchanged_comparison_requires_exact_check_and_cache_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            first = _evaluate(summary)
            comparison = _comparison(first)
            identity, cached = next(iter(comparison.items()))
            corrupted = dict(comparison)
            corrupted[identity] = CACHE.CheckComparisonEntry(
                replace(cached.check, subject="wrong subject"),
                cached.dependency_projection,
            )

            result = _evaluate(summary, check_comparison=corrupted)

            self.assertLess(
                result.metrics["checks_unchanged"],
                len(comparison),
            )
            self.assertEqual(
                result.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )


if __name__ == "__main__":
    unittest.main()
