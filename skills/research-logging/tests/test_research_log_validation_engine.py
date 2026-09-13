from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import research_log_data as DATA
from research_log_cli_test_support import fixture_parameter_roles
from research_log_validation_test_support import mock, unittest, write

ENGINE = importlib.import_module("validation.engine")
RESULTS = importlib.import_module("validation.mechanical_results")
LOCATOR = importlib.import_module("validation.locator")
PYRUN_STATE = importlib.import_module("validation.pyrun_state")
PRESENTATION = importlib.import_module("validation.presentation")
HUMAN = importlib.import_module("validation.human_projection")
PROVENANCE = importlib.import_module("validation.provenance")
REPORT = importlib.import_module("validation.report")
COMMANDS = importlib.import_module("validation.commands")

_EVALUATE_MECHANICAL = ENGINE.evaluate_mechanical


def _evaluate_current_fixture(request: Any) -> Any:
    """Add stable CIDs to legacy test prose immediately before evaluation."""

    log_root = Path(request.summary_path).with_suffix("")
    for entry_root in sorted(
        path for path in log_root.rglob("entries/*") if path.is_dir()
    ):
        counts: dict[str, int] = {}
        for document in sorted(entry_root.glob("*.md")):
            text = document.read_text(encoding="utf-8")

            def add_cid(match: re.Match[str]) -> str:
                tail = text[match.end() : text.find("\n", match.end())]
                script = re.search(r"scripts/([A-Za-z0-9_-]+)\.py", tail)
                stem = script.group(1) if script is not None else "command"
                counts[stem] = counts.get(stem, 0) + 1
                cid = stem if counts[stem] == 1 else f"{stem}-{counts[stem]}"
                separator = "" if tail.lstrip().startswith("--") else "-- "
                return f"./pyrun --cid {cid} {separator}"

            current = re.sub(r"\./pyrun (?![^\n]*--cid\b)", add_cid, text)
            if current != text:
                document.write_text(current, encoding="utf-8")
    return _EVALUATE_MECHANICAL(request)


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
                "schema": "research-log-data/v5",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    },
                    {
                        "name": "results",
                        "kind": "file",
                        "location": "data/results.csv",
                        "identity": {"algorithm": "sha256"},
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
  "schema": "research-log-evidence/v4",
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
                        "code": {},
                        "parameters": [
                            "--input-catalog",
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
        "./pyrun scripts/model.py --input-catalog '<catalog>' "
        f"--{output_option} '<results>'\n"
        "```\n\n"
        "`Results:`\n\n"
        "The success rate was `67.6%`<!-- eid:success-rate -->.\n",
    )
    return summary, entry


def _replace_with_pyrun_state(entry_document: Path, parameters: tuple[str, ...]) -> str:
    """Replace the legacy fixture registry with one current execution."""

    entry = entry_document.parent
    (entry / "pyrun-outputs.json").unlink()
    recipe = PYRUN_STATE.ExecutionRecipe(
        "scripts/model.py",
        parameters,
        (),
        ("catalog",),
        (("data/results.csv", "file"),),
        parameter_roles=fixture_parameter_roles(
            parameters, ("catalog",), (("data/results.csv", "file"),)
        ),
    )
    observed = PYRUN_STATE.ObservedExecution(
        DATA.Fingerprint(
            "sha256",
            digest=hashlib.sha256(
                (entry / "scripts/model.py").read_bytes()
            ).hexdigest(),
        ),
        (
            (
                "catalog",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/catalog.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
        (),
        (
            (
                "data/results.csv",
                DATA.Fingerprint(
                    "sha256",
                    digest=hashlib.sha256(
                        (entry / "data/results.csv").read_bytes()
                    ).hexdigest(),
                ),
            ),
        ),
    )
    execution = PYRUN_STATE.PyrunExecution(
        False,
        True,
        "2030-01-01T00:00:00Z",
        PYRUN_STATE.PYRUN_RUNNER,
        PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
        recipe,
        observed,
    )
    identity = PYRUN_STATE.execution_id(recipe)
    state = PYRUN_STATE.PyrunFile(
        entry / PYRUN_STATE.PYRUN_FILENAME,
        entry,
        {"model": PYRUN_STATE.PyrunCommand({identity: execution})},
    )
    write(entry / PYRUN_STATE.PYRUN_FILENAME, state.serialized())
    return identity


def _evaluate(summary: Path) -> Any:
    evaluation = _evaluate_current_fixture(
        ENGINE.EvaluationRequest(summary, "2026-08-29")
    )
    return SimpleNamespace(
        result=evaluation.record,
        scan={
            "invocations": evaluation.context.invocations,
            "registries": evaluation.context.registries,
        },
        metrics=evaluation.metrics,
    )


def _set_code_support(
    entry: Path,
    code: dict[str, dict[str, str]],
    *,
    output: str = "data/results.csv",
) -> dict[str, Any]:
    path = entry.parent / "pyrun-outputs.json"
    support = json.loads(path.read_text(encoding="utf-8"))
    support["outputs"][output]["code"] = code
    write(path, json.dumps(support, indent=2) + "\n")
    return support


def _origin_data_json(entry_root: Path) -> str:
    source = entry_root / "data/catalog.csv"
    write(source, "id\n1\n")
    return (
        json.dumps(
            {
                "schema": "research-log-data/v5",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "data/catalog.csv",
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )


def _convert_result_to_bundle(entry: Path) -> tuple[Path, Path, Path]:
    entry_root = entry.parent
    bundle = entry_root / "data/bundle"
    member = bundle / "results.csv"
    sibling = bundle / "model.pt"
    bundle.mkdir()
    (entry_root / "data/results.csv").replace(member)
    write(sibling, "model\n")

    data_path = entry_root / "data.json"
    data = json.loads(data_path.read_text())
    data["inputs"] = [item for item in data["inputs"] if item["name"] != "results"]
    resource = DATA.build_local_input(
        "results",
        "directory",
        "data/bundle",
        entry_root=entry_root,
        origin=False,
    )
    data["inputs"].append(resource.as_dict())
    data["inputs"].sort(key=lambda item: item["name"])
    write(data_path, json.dumps(data, indent=2) + "\n")

    evidence_path = entry_root / "evidence.json"
    write(
        evidence_path,
        evidence_path.read_text().replace(
            '"source": "<results>"',
            '"source": "<results>/results.csv"',
        ),
    )
    write(
        entry,
        entry.read_text().replace(
            "--output-data '<results>'",
            "--output-dir '<results>'",
        ),
    )
    support_path = entry_root / "pyrun-outputs.json"
    support = json.loads(support_path.read_text())
    record = support["outputs"].pop("data/results.csv")
    record["fingerprint"] = DATA.observe_fingerprint(resource).fingerprint.as_dict()
    record["parameters"] = [
        "--input-catalog",
        "<catalog>",
        "--output-dir",
        "data/bundle",
    ]
    support["outputs"]["data/bundle"] = record
    write(support_path, json.dumps(support, indent=2) + "\n")
    return bundle, member, sibling


class EngineV2EndToEndTests(unittest.TestCase):
    def test_entry_declaration_index_selects_exact_overlap_and_rejected_competitors(
        self,
    ) -> None:
        """Closure selection retains every declaration that can compete for a root."""

        index = ENGINE._LogDeclarationIndex(
            Path("/log"),
            (),
            (),
            {
                "/project/data/exact.csv": (
                    ENGINE._DeclarationCandidate(
                        "e001", "/project/data/exact.csv", "file", (0, 1, 1)
                    ),
                ),
                "/project/data/tree": (
                    ENGINE._DeclarationCandidate(
                        "e002", "/project/data/tree", "directory", (1, 1, 1)
                    ),
                ),
                "/project/data/tree/member.csv": (
                    ENGINE._DeclarationCandidate(
                        "e003", "/project/data/tree/member.csv", "file", (2, 1, 1)
                    ),
                ),
            },
            {
                "/project/data/tree/rejected.csv": (
                    ENGINE._DeclarationCandidate(
                        "e004", "/project/data/tree/rejected.csv", "unknown", (3, 1, 1)
                    ),
                ),
            },
            (),
        )
        owners = ENGINE._indexed_candidate_owners(
            index, (("/project/data/exact.csv", None), ("/project/data/tree", None))
        )
        self.assertEqual(owners, frozenset({"e001", "e002", "e003", "e004"}))

    def test_declaration_candidates_use_command_frontiers_not_document_order(self):
        """A prior fence can feed its document, but a later fence cannot."""

        candidate = ENGINE._DeclarationCandidate(
            "e001", "/project/data/shared.csv", "file", (0, 1, 1)
        )
        index = ENGINE._LogDeclarationIndex(
            Path("/log"), (), (), {candidate.path: (candidate,)}, {}, ()
        )
        self.assertEqual(
            ENGINE._indexed_candidate_owners(index, ((candidate.path, (0, 2, 1)),)),
            frozenset({"e001"}),
        )
        self.assertEqual(
            ENGINE._indexed_candidate_owners(index, ((candidate.path, (0, 1, 1)),)),
            frozenset(),
        )

    def test_selected_named_rejection_is_seeded_once_in_source_order(self):
        """Selected structural rejections do not re-enter producer closure."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py", "python scripts/model.py"
                ),
            )
            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e001", entry.parent),
                )
            )
            rejected = [
                check
                for check in result.record.checks
                if check.identity == "entry:e001:command:1:1"
            ]
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0].failure.code, "invocation.command.unsupported")

    def test_entry_unreadable_declaration_index_is_incomplete_then_recovers(
        self,
    ) -> None:
        """An unavailable declaration read is scoped incomplete and retryable."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = ENGINE.EvaluationRequest(
                summary,
                "2026-08-29",
                ENGINE.EntryEvaluationTarget("e001", entry.parent),
            )
            original = ENGINE._read_text
            with mock.patch.object(
                ENGINE,
                "_read_text",
                side_effect=lambda path, state: (
                    (_ for _ in ()).throw(OSError("denied"))
                    if path.name == "e001.md"
                    else original(path, state)
                ),
            ):
                incomplete = _evaluate_current_fixture(request)
            self.assertEqual(
                incomplete.record.completion, RESULTS.CompletionState.INCOMPLETE
            )
            self.assertEqual(
                _evaluate_current_fixture(request).record.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )

    def test_entry_over_bound_declaration_index_is_incomplete_then_recovers(
        self,
    ) -> None:
        """A bounded declaration-index failure is retryable scoped incompleteness."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            request = ENGINE.EvaluationRequest(
                summary,
                "2026-08-29",
                ENGINE.EntryEvaluationTarget("e001", entry.parent),
            )
            with mock.patch.object(
                ENGINE,
                "_index_log",
                side_effect=ENGINE.EngineV2Error(
                    "association.resource.too_large",
                    "index",
                    {"limit": 1},
                    "Recorded-Command Provenance And Material Graph",
                    outcome="unavailable",
                ),
            ):
                incomplete = _evaluate_current_fixture(request)
            self.assertEqual(
                incomplete.record.completion, RESULTS.CompletionState.INCOMPLETE
            )
            self.assertEqual(
                _evaluate_current_fixture(request).record.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )

    def test_command_declaration_index_never_loads_execution_state(self) -> None:
        """Namespace indexing is structural even when an entry has state to load."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
            with mock.patch.object(
                ENGINE,
                "load_pyrun_state",
                side_effect=AssertionError("indexing must not load execution state"),
            ):
                index = ENGINE._index_log(summary.read_text(encoding="utf-8"), state)
            self.assertEqual(
                tuple(entry.stable_id for entry in index.entries), ("e001",)
            )

    def test_pyrun_policy_mismatch_is_structure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry_document = _log(root)
            identity = _replace_with_pyrun_state(
                entry_document,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            entry = entry_document.parent
            path = entry / PYRUN_STATE.PYRUN_FILENAME
            state = PYRUN_STATE.load_pyrun_state(
                path, entry_root=entry, project_root=root
            )
            changed = dict(state.commands["model"].executions)
            changed[identity] = replace(changed[identity], auto_reproduce=False)
            write(
                path,
                PYRUN_STATE.PyrunFile(
                    path, entry, {"model": PYRUN_STATE.PyrunCommand(changed)}
                ).serialized(),
            )

            evaluation = _evaluate(summary)

            finding = next(
                check
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "pyrun.policy.mismatch"
            )
            self.assertEqual(finding.scope, RESULTS.CheckScope.CONFORMANCE)
            self.assertIn(identity, finding.identity)

    def test_current_execution_requires_exact_command_association(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            entry.write_text(
                entry.read_text(encoding="utf-8").replace(
                    "--output-data '<results>'",
                    "--output-data '<results>' --mode exact",
                ),
                encoding="utf-8",
            )

            evaluation = _evaluate(summary).result

            provenance = next(
                check
                for check in evaluation.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.execution_unassociated",
            )

    def test_current_execution_rejects_changed_script_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            _replace_with_pyrun_state(
                entry,
                ("--input-catalog", "<catalog>", "--output-data", "data/results.csv"),
            )
            (entry.parent / "scripts/model.py").write_text(
                "# changed model\n", encoding="utf-8"
            )

            evaluation = _evaluate(summary).result

            provenance = next(
                check
                for check in evaluation.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.signature_mismatch",
            )
            self.assertEqual(
                provenance.failure.observed["fields"], ["script_fingerprint"]
            )

    def test_reproduction_tolerance_requires_evidence_scoped_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["reproduction_tolerance"] = {"absolute": "0.01"}
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            evaluation = _evaluate(summary)

            failure = next(
                check
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code
                == "reproduction.comparison.tolerance_incompatible"
            )
            self.assertEqual(failure.scope, RESULTS.CheckScope.CONFORMANCE)

    def test_evidence_scoped_comparison_requires_applicable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            data["inputs"][1]["comparison"] = {
                "contract": "research-log-evidence-scoped-comparison/1",
                "profile": "evidence",
            }
            write(data_path, json.dumps(data, indent=2) + "\n")

            valid = _evaluate(summary)

            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code.startswith("reproduction.comparison.")
                    for check in valid.result.checks
                )
            )

            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["records"][0]["sources"][0]["locator"] = {"select": [["missing"]]}
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            incompatible = _evaluate(summary)
            self.assertTrue(
                any(
                    check.failure is not None
                    and check.failure.code
                    == "reproduction.comparison.evidence_incompatible"
                    and check.scope is RESULTS.CheckScope.CONFORMANCE
                    for check in incompatible.result.checks
                )
            )

            evidence["records"][0]["sources"][0]["locator"] = {
                "select": [["success_rate"]]
            }
            evidence["records"][0]["sources"][0]["source"] = "<catalog>"
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            invalid = _evaluate(summary)

            failure = next(
                check
                for check in invalid.result.checks
                if check.failure is not None
                and check.failure.code == "reproduction.comparison.evidence_missing"
            )
            self.assertEqual(failure.scope, RESULTS.CheckScope.CONFORMANCE)

    def test_pyrun_binding_failure_is_execution_scoped_structure(self) -> None:
        cases = (
            (("--input-catalog", "<catalog>", "--mode", "exact"), "missing"),
            (
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "data/results.csv",
                    "--reference",
                    "data/results.csv",
                ),
                "ambiguous",
            ),
            (
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "./data/results.csv",
                ),
                "noncanonical",
            ),
        )
        for parameters, reason in cases:
            with (
                tempfile.TemporaryDirectory() as directory,
                self.subTest(reason=reason),
            ):
                summary, entry = _log(Path(directory))
                identity = _replace_with_pyrun_state(entry, parameters)

                evaluation = _evaluate(summary)

                binding = next(
                    check
                    for check in evaluation.result.checks
                    if check.failure is not None
                    and check.failure.code == "pyrun.output.binding_invalid"
                )
                self.assertEqual(binding.scope, RESULTS.CheckScope.CONFORMANCE)
                self.assertIn(identity, binding.identity)
                self.assertEqual(binding.failure.observed["reason"], reason)
                self.assertFalse(
                    any(
                        check.failure is not None
                        and check.failure.code == "pyrun.state.invalid"
                        for check in evaluation.result.checks
                    )
                )
                self.assertTrue(
                    any(
                        check.identity == "evidence:e001:success-rate"
                        for check in evaluation.result.checks
                    )
                )

    def test_undeclared_generated_artifact_remains_an_orphan_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            unexpected = entry.parent / "data/unexpected.csv"
            write(unexpected, "value\n2\n")
            _replace_with_pyrun_state(
                entry,
                (
                    "--input-catalog",
                    "<catalog>",
                    "--output-data",
                    "data/results.csv",
                ),
            )

            evaluation = _evaluate(summary)

            failures = {
                (check.failure.code, check.failure.subject)
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertIn(
                ("orphan.material.unused", unexpected.resolve().as_posix()),
                failures,
            )
            self.assertFalse(
                any(code == "pyrun.output.binding_invalid" for code, _ in failures)
            )

    def test_current_code_support_enters_provenance_and_suppresses_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            write(helper, "VALUE = 1\n")
            _set_code_support(
                entry,
                {
                    "scripts/helper.py": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    }
                },
            )

            result = _evaluate(summary).result
            provenance = next(
                check
                for check in result.checks
                if check.identity == "provenance:e001:success-rate"
            )

            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in result.checks
                )
            )

    def test_changed_and_unconfirmed_code_support_stays_connected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            write(helper, "VALUE = 1\n")
            support = _set_code_support(
                entry,
                {
                    "scripts/helper.py": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    }
                },
            )
            support_path = entry.parent / "pyrun-outputs.json"

            write(helper, "VALUE = 2\n")
            changed = _evaluate(summary).result
            provenance = next(
                check
                for check in changed.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code, "provenance.output.signature_mismatch"
            )
            self.assertIn("code", provenance.failure.observed["fields"])
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in changed.checks
                )
            )

            support["outputs"]["data/results.csv"]["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            unconfirmed = _evaluate(summary).result
            provenance = next(
                check
                for check in unconfirmed.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code, "provenance.output.reproduction_required"
            )
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in unconfirmed.checks
                )
            )

    def test_unavailable_and_duplicate_code_targets_fail_without_graph_edges(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            alias = entry.parent / "scripts/alias.py"
            write(helper, "VALUE = 1\n")
            alias.symlink_to(helper.name)
            fingerprint = {
                "algorithm": "sha256",
                "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
            }
            _set_code_support(
                entry,
                {
                    "scripts/helper.py": fingerprint,
                    "scripts/alias.py": fingerprint,
                },
            )

            duplicate = _evaluate(summary).result
            provenance = next(
                check
                for check in duplicate.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.failure.code, "provenance.output.code_invalid")
            self.assertEqual(
                provenance.failure.observed["reason"],
                "duplicate_resolved_identity",
            )
            self.assertTrue(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject == helper.resolve().as_posix()
                    for check in duplicate.checks
                )
            )

            alias.unlink()
            helper.unlink()
            _set_code_support(entry, {"scripts/missing.py": fingerprint})
            missing = _evaluate(summary).result
            provenance = next(
                check
                for check in missing.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.failure.code, "provenance.output.code_invalid")
            self.assertEqual(provenance.failure.observed["reason"], "unavailable")

            code_directory = entry.parent / "scripts/directory.py"
            code_directory.mkdir()
            _set_code_support(entry, {"scripts/directory.py": fingerprint})
            wrong_kind = _evaluate(summary).result
            provenance = next(
                check
                for check in wrong_kind.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.failure.code, "provenance.output.code_invalid")
            self.assertEqual(provenance.failure.observed["reason"], "not_regular_file")

    def test_unmatched_code_support_does_not_connect_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            helper = entry.parent / "scripts/helper.py"
            stale = entry.parent / "data/stale.csv"
            write(helper, "VALUE = 1\n")
            write(stale, "stale\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            record = dict(support["outputs"]["data/results.csv"])
            record["fingerprint"] = {
                "algorithm": "sha256",
                "digest": hashlib.sha256(stale.read_bytes()).hexdigest(),
            }
            record["code"] = {
                "scripts/helper.py": {
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(helper.read_bytes()).hexdigest(),
                }
            }
            support["outputs"]["data/stale.csv"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).result
            failures = {
                (check.failure.code, check.subject)
                for check in result.checks
                if check.failure is not None
            }
            self.assertIn(
                ("hygiene.output.unmatched", stale.resolve().as_posix()), failures
            )
            self.assertIn(
                ("orphan.material.unused", helper.resolve().as_posix()), failures
            )

    def test_code_observation_reuses_one_resolved_file_across_logical_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            entry_root = entry.parent
            helper = entry_root.parent.parent / "shared/helper.py"
            write(helper, "VALUE = 1\n")
            linked = entry_root / "scripts/linked"
            linked.symlink_to(helper.parent, target_is_directory=True)
            fingerprint = DATA.Fingerprint(
                "sha256", digest=hashlib.sha256(helper.read_bytes()).hexdigest()
            )
            support = ENGINE.load_pyrun_outputs(
                entry_root / "pyrun-outputs.json",
                entry_root=entry_root,
                project_root=root,
            )
            base = support.outputs["data/results.csv"]
            direct = replace(base, code=(("<log>/shared/helper.py", fingerprint),))
            alias = replace(base, code=(("scripts/linked/helper.py", fingerprint),))
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root.resolve())

            with ENGINE.FingerprintCache(root, writable=False, reuse=False) as cache:
                state.fingerprint_cache = cache
                with mock.patch.object(
                    cache,
                    "observe_regular_file",
                    wraps=cache.observe_regular_file,
                ) as observe:
                    ENGINE._observe_output_code(
                        ENGINE.resolve_code_support(
                            direct,
                            entry_root=entry_root,
                            subject="data/results.csv",
                        ),
                        state,
                    )
                    ENGINE._observe_output_code(
                        ENGINE.resolve_code_support(
                            alias,
                            entry_root=entry_root,
                            subject="data/results.csv",
                        ),
                        state,
                    )

            helper_calls = [
                call
                for call in observe.call_args_list
                if call.args[0].resolve() == helper.resolve()
            ]
            self.assertEqual(len(helper_calls), 1)

    def test_support_without_code_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text(encoding="utf-8"))
            del support["outputs"]["data/results.csv"]["code"]
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).result

            self.assertEqual(
                result.completion, RESULTS.CompletionState.COMPLETE_FINDINGS
            )
            self.assertIn(
                "pyrun.outputs.invalid",
                {
                    check.failure.code
                    for check in result.checks
                    if check.failure is not None
                },
            )

    def test_bundle_member_uses_root_support_and_atomic_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            bundle, member, sibling = _convert_result_to_bundle(entry)

            complete = _evaluate(summary).result

            self.assertEqual(
                complete.completion,
                RESULTS.CompletionState.COMPLETE_CLEAR,
            )
            provenance = next(
                check
                for check in complete.checks
                if check.identity == "provenance:e001:success-rate"
            )
            artifact = provenance.dependencies[0]
            self.assertEqual(artifact["artifacts"], [member.resolve().as_posix()])
            self.assertEqual(
                provenance.dependencies[1]["material"], member.resolve().as_posix()
            )

            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/bundle"]["parameters"].append("--stale")
            write(support_path, json.dumps(support, indent=2) + "\n")
            stale = _evaluate(summary).result
            provenance = next(
                check
                for check in stale.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.signature_mismatch",
            )
            self.assertEqual(provenance.failure.subject, bundle.resolve().as_posix())

            support["outputs"]["data/bundle"]["parameters"].pop()
            support["outputs"]["data/bundle"]["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            unconfirmed = _evaluate(summary).result
            provenance = next(
                check
                for check in unconfirmed.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                provenance.failure.code, "provenance.output.reproduction_required"
            )
            self.assertEqual(provenance.failure.subject, bundle.resolve().as_posix())
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject
                    in {
                        member.resolve().as_posix(),
                        sibling.resolve().as_posix(),
                    }
                    for check in unconfirmed.checks
                )
            )

            support["outputs"]["data/bundle"]["confirmed"] = True
            write(support_path, json.dumps(support, indent=2) + "\n")
            write(sibling, "changed model\n")
            modified = _evaluate(summary).result
            modified_provenance = next(
                check
                for check in modified.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(
                modified_provenance.failure.code,
                "provenance.output.signature_mismatch",
            )
            self.assertEqual(
                modified_provenance.failure.subject,
                bundle.resolve().as_posix(),
            )
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "orphan.material.unused"
                    and check.subject.startswith(bundle.resolve().as_posix() + "/")
                    for check in modified.checks
                )
            )

            shutil.rmtree(bundle)
            deleted = _evaluate(summary).result
            missing = [
                check
                for check in deleted.checks
                if check.failure is not None
                and check.failure.code == "provenance.output.missing"
                and check.subject == bundle.resolve().as_posix()
            ]
            self.assertEqual(len(missing), 1)

    def test_unreached_output_only_bundle_is_one_root_hygiene_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            bundle = entry_root / "data/bundle"
            members = (bundle / "one.csv", bundle / "two.csv")
            for index, member in enumerate(members, 1):
                write(member, f"value\n{index}\n")
            write(entry_root / "scripts/bundle.py", "# bundle\n")
            resource = DATA.build_local_input(
                "bundle",
                "directory",
                "data/bundle",
                entry_root=entry_root,
                origin=False,
            )
            write(
                entry,
                entry.read_text().replace(
                    "```\n\n`Results:`",
                    "./pyrun scripts/bundle.py --output-dir data/bundle\n"
                    "```\n\n`Results:`",
                ),
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/bundle"] = {
                "confirmed": False,
                "fingerprint": DATA.observe_fingerprint(resource).fingerprint.as_dict(),
                "inputs": {},
                "code": {},
                "parameters": ["--output-dir", "data/bundle"],
                "script": {
                    "path": "scripts/bundle.py",
                    "fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(
                            (entry_root / "scripts/bundle.py").read_bytes()
                        ).hexdigest(),
                    },
                },
            }
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).result
            bundle_root = bundle.resolve().as_posix()
            bundle_findings = [
                check
                for check in result.checks
                if check.failure is not None
                and check.failure.code == "orphan.material.unused"
                and (
                    check.subject == bundle_root
                    or check.subject.startswith(bundle_root + "/")
                )
            ]

            self.assertEqual(len(bundle_findings), 1)
            self.assertEqual(bundle_findings[0].subject, bundle_root)

            support["outputs"]["data/bundle"]["parameters"].append("changed")
            write(support_path, json.dumps(support, indent=2) + "\n")
            mismatched = _evaluate(summary).result
            mismatched_subjects = {
                check.subject
                for check in mismatched.checks
                if check.failure is not None
                and check.failure.code == "orphan.material.unused"
            }
            self.assertNotIn(bundle_root, mismatched_subjects)
            self.assertTrue(
                {member.resolve().as_posix() for member in members}.issubset(
                    mismatched_subjects
                )
            )

    def test_unmatched_directory_support_suppresses_descendant_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            entry_root = entry.parent
            stale = entry_root / "data/stale"
            members = (stale / "one.csv", stale / "two.csv")
            for index, member in enumerate(members, 1):
                write(member, f"value\n{index}\n")
            resource = DATA.build_local_input(
                "stale",
                "directory",
                "data/stale",
                entry_root=entry_root,
                origin=False,
            )
            support_path = entry_root / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            record = dict(support["outputs"]["data/results.csv"])
            record["fingerprint"] = DATA.observe_fingerprint(
                resource
            ).fingerprint.as_dict()
            support["outputs"]["data/stale"] = record
            write(support_path, json.dumps(support, indent=2) + "\n")

            result = _evaluate(summary).result
            root = stale.resolve().as_posix()
            findings = [
                check
                for check in result.checks
                if check.failure is not None
                and (check.subject == root or check.subject.startswith(root + "/"))
            ]

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].failure.code, "hygiene.output.unmatched")
            self.assertEqual(findings[0].subject, root)

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
            results = next(item for item in data["inputs"] if item["name"] == "results")
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

    def test_validation_builds_one_shared_producer_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            builder = ENGINE.build_producer_index

            with mock.patch.object(
                ENGINE,
                "build_producer_index",
                wraps=builder,
            ) as indexed:
                _evaluate(summary)

            self.assertEqual(indexed.call_count, 1)

    def test_output_support_conclusions_are_reused_within_one_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ENGINE._ScanState(root / "study.md", root, root)
            invocation = mock.Mock(identity="entry:e001:execution:one")
            support = {"output": "data/result.csv"}

            with mock.patch.object(
                ENGINE, "_evaluate_output_support", return_value=support
            ) as evaluate:
                first = ENGINE._validate_output_support(invocation, "result", state)
                second = ENGINE._validate_output_support(invocation, "result", state)

            self.assertIs(first, support)
            self.assertIs(second, support)
            self.assertEqual(evaluate.call_count, 1)

            failure = ENGINE.EngineV2Error(
                "provenance.output.reproduction_required",
                "failed",
                {"producer": invocation.identity},
                "Pyrun Output Support Records",
            )
            with mock.patch.object(
                ENGINE, "_evaluate_output_support", side_effect=failure
            ) as evaluate:
                for _ in range(2):
                    with self.assertRaises(ENGINE.MechanicalContractError) as raised:
                        ENGINE._validate_output_support(invocation, "failed", state)
                    self.assertEqual(raised.exception.code, failure.code)
                    self.assertEqual(raised.exception.observed, failure.observed)

            self.assertEqual(evaluate.call_count, 1)

    def test_provenance_findings_prepare_ordering_and_blockers_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subject = (root / "missing.csv").as_posix()
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.command_blocker_candidates = (
                (root / "missing.csv", False, ("entry:e001:command:1:1",)),
            )
            findings = (
                PROVENANCE.ProvenanceFinding(
                    "lineage.missing", subject, {"consumer": "two"}, "Lineage"
                ),
                PROVENANCE.ProvenanceFinding(
                    "provenance.output.reproduction_required",
                    subject,
                    {"producer": "one"},
                    "Output Support",
                ),
            )
            encoder = ENGINE.canonical_json

            with (
                mock.patch.object(ENGINE, "canonical_json", wraps=encoder) as canonical,
                mock.patch.object(
                    ENGINE,
                    "_indexed_command_blockers",
                    wraps=ENGINE._indexed_command_blockers,
                ) as blocker_index,
            ):
                prepared = ENGINE._ordered_provenance_findings(findings, state)
                self.assertEqual(
                    ENGINE._command_blockers(subject, state),
                    ("entry:e001:command:1:1",),
                )

            self.assertEqual(canonical.call_count, len(findings))
            self.assertEqual(blocker_index.call_count, 1)
            self.assertEqual(
                [item.finding.code for item in prepared],
                ["provenance.output.reproduction_required", "lineage.missing"],
            )
            with mock.patch.object(
                ENGINE,
                "_command_blockers",
                side_effect=AssertionError("prepared check must reuse blockers"),
            ):
                check = ENGINE._provenance_finding_check(
                    "provenance:e001:test", prepared[-1], dependencies=()
                )
            self.assertEqual(check.status, RESULTS.CheckStatus.NOT_APPLICABLE)

    def test_output_support_parameter_change_breaks_provenance_until_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text().replace(
                    "--input-catalog '<catalog>' ",
                    "--input-catalog '<catalog>' --mode revised ",
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
                "--input-catalog",
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
                    "identity": {"algorithm": "sha256"},
                    "origin": False,
                }
            )
            write(data_path, json.dumps(data, indent=2) + "\n")
            write(
                entry,
                entry.read_text().replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' ",
                    "./pyrun --cid preprocess -- scripts/preprocess.py "
                    "--input-data '<catalog>' --output-data '<intermediate>'\n"
                    "```\n\n```bash\n"
                    "./pyrun --cid model -- scripts/model.py "
                    "--input-data '<intermediate>' ",
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
                "code": {},
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
            complete_document = entry.read_text(encoding="utf-8")
            result_record["confirmed"] = False
            write(support_path, json.dumps(support, indent=2) + "\n")
            write(
                entry,
                complete_document.replace(
                    "./pyrun --cid preprocess -- scripts/preprocess.py "
                    "--input-data '<catalog>' --output-data '<intermediate>'\n"
                    "```\n\n```bash\n",
                    "",
                ),
            )

            collected = _evaluate(summary).result
            provenance = [
                check
                for check in collected.checks
                if check.identity.startswith("provenance:e001:success-rate")
            ]
            self.assertEqual(
                {check.failure.code for check in provenance if check.failure},
                {"lineage.missing", "provenance.output.reproduction_required"},
            )
            primary = next(
                check
                for check in provenance
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(primary.failure.code, "lineage.missing")
            self.assertEqual(
                HUMAN.provenance_artifact_counts(collected)[
                    RESULTS.CheckStatus.FAIL.value
                ],
                1,
            )
            self.assertEqual(
                HUMAN.provenance_artifact_counts(collected)[
                    RESULTS.CheckStatus.UNAVAILABLE.value
                ],
                0,
            )

            result_record["confirmed"] = True
            write(support_path, json.dumps(support, indent=2) + "\n")
            confirmed = _evaluate(summary).result
            self.assertEqual(
                {
                    check.failure.code
                    for check in confirmed.checks
                    if check.identity.startswith("provenance:e001:success-rate")
                    and check.failure
                },
                {"lineage.missing"},
            )
            write(entry, complete_document)
            write(support_path, json.dumps(support, indent=2) + "\n")

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
            self.assertEqual(
                check.failure.code, "provenance.output.reproduction_required"
            )

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
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
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
            self.assertEqual(stale_findings[0].failure.code, "hygiene.output.unmatched")

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
            state.owner_surface_prerequisite_checks["entries/2026-08-29-e001-study"] = (
                exact_check,
            )

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
                origin=False,
            )
            state = ENGINE._ScanState(root / "study.md", root, root)

            ENGINE._verify_input(correct, state)

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

    def test_origin_ignores_confirmed_producer_from_another_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            source = root / "output/logs/other/e001/data/catalog.csv"
            write(source, "id\n1\n")
            (entry.parent / "data/catalog.csv").unlink()
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog["location"] = source.as_posix()
            write(data_path, json.dumps(data, indent=2) + "\n")

            other_entry = root / "docs/other/entries/2026-08-29-e001-other"
            output_key = "<project>/output/logs/other/e001/data/catalog.csv"
            script = other_entry / "scripts/build.py"
            write(root / "docs/other.md", "# Other\n\n## Entries\n")
            write(script, "# retained producer\n")
            write(
                other_entry / "e001.md",
                "# Other entry\n\n"
                "## Build catalog\n\n"
                "`Steps:`\n\n"
                "```bash\n"
                "./pyrun scripts/build.py "
                f"--output-data '{output_key}'\n"
                "```\n\n"
                "`Results:`\n\n"
                "The catalog was generated.\n",
            )
            recipe = PYRUN_STATE.ExecutionRecipe(
                "scripts/build.py",
                ("--output-data", output_key),
                (),
                (),
                ((output_key, "file"),),
                parameter_roles=fixture_parameter_roles(
                    ("--output-data", output_key), (), ((output_key, "file"),)
                ),
            )
            observed = PYRUN_STATE.ObservedExecution(
                DATA.Fingerprint(
                    "sha256", digest=hashlib.sha256(script.read_bytes()).hexdigest()
                ),
                (),
                (),
                (
                    (
                        output_key,
                        DATA.Fingerprint(
                            "sha256",
                            digest=hashlib.sha256(source.read_bytes()).hexdigest(),
                        ),
                    ),
                ),
            )
            execution = PYRUN_STATE.PyrunExecution(
                False,
                True,
                "2030-01-01T00:00:00Z",
                PYRUN_STATE.PYRUN_RUNNER,
                PYRUN_STATE.PYRUN_ENVIRONMENT_PROFILE,
                PYRUN_STATE.PYRUN_EXECUTION_CONTRACT,
                recipe,
                observed,
            )
            identity = PYRUN_STATE.execution_id(recipe)
            other_state = PYRUN_STATE.PyrunFile(
                other_entry / PYRUN_STATE.PYRUN_FILENAME,
                other_entry,
                {"build": PYRUN_STATE.PyrunCommand({identity: execution})},
            )
            write(
                other_state.path,
                PYRUN_STATE.validated_pyrun_serialization(
                    other_state, project_root=root
                ),
            )

            evaluation = _evaluate(summary)

            provenance = next(
                check
                for check in evaluation.result.checks
                if check.identity == "provenance:e001:success-rate"
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)

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
                    "identity": {"algorithm": "sha256"},
                    "origin": True,
                }
            )
            write(data_path, json.dumps(payload, indent=2) + "\n")

            evaluation = _evaluate(summary)

            evidence = next(
                check
                for check in evaluation.result.checks
                if check.identity == "evidence:e001:success-rate"
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(evaluation.metrics["invocations"], 1)

    def test_invalid_command_input_blocks_its_provenance_without_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            catalog_path = entry.parent / "data/catalog.csv"
            write(catalog_path, "value\n1\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            command = checks["entry:e001:command:1:1:output:1"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(command.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(
                checks["evidence:e001:success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)
            failure_codes = {
                check.failure.code
                for check in evaluation.result.checks
                if check.failure is not None
            }
            self.assertNotIn("producer.missing", failure_codes)

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
                self.assertIn({"dependency": declaration.identity}, check.dependencies)
            self.assertFalse(any(check.failure is not None for check in orphan_checks))

    def test_conflicted_input_blocks_consumers_without_undeclared_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            shared = root / "inputs/catalog.csv"
            write(shared, "success_rate\n0.676\n")
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog = next(item for item in data["inputs"] if item["name"] == "catalog")
            catalog.update(
                {
                    "location": shared.as_posix(),
                    "identity": {"algorithm": "sha256"},
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
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared-catalog",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
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
                summary.read_text(encoding="utf-8") + "\n- [Conflict]"
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
                            "schema": "research-log-data/v5",
                            "inputs": [
                                {
                                    "name": "conflict",
                                    "kind": "file",
                                    "location": conflict_path.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": entry_id == "e002",
                                },
                                {
                                    "name": f"safe-{entry_id}",
                                    "kind": "file",
                                    "location": (f"data/{entry_id}.csv"),
                                    "identity": {"algorithm": "sha256"},
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
                "```bash\npython scripts/run.py\n```\n\n`Results:`\n\nDone.\n",
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
                if check.identity.startswith("entry:e002:command")
            )
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(invalid.scope, RESULTS.CheckScope.CONFORMANCE)
            self.assertEqual(invalid.failure.code, "invocation.command.unsupported")

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
                "```bash\n./pyrun --cid run -- scripts/run.py "
                "--output-data data/result.csv\n```\n\n"
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
                "## Trial\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/run.py --label baseline\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            write(
                second,
                "## Trial\n\n`Steps:`\n\n"
                "```bash\n./pyrun scripts/run.py --input-catalog '<catalog>'\n```\n\n"
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
                "## Trial\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/run.py --label baseline\n```\n\n"
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
  "schema": "research-log-evidence/v4",
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

    def test_repeated_evidence_source_reuses_provenance_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            duplicate = json.loads(json.dumps(evidence["records"][0]))
            duplicate["id"] = "success-rate-copy"
            evidence["records"].append(duplicate)
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            write(
                entry,
                entry.read_text(encoding="utf-8") + "\nThe copied rate was `67.6%`"
                "<!-- eid:success-rate-copy -->.\n",
            )

            with mock.patch.object(
                ENGINE,
                "evaluate_complete_provenance",
                wraps=ENGINE.evaluate_complete_provenance,
            ) as evaluate:
                evaluation = _evaluate(summary)

            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(evaluation.metrics["provenance_traversals"], 1)
            self.assertEqual(evaluation.metrics["provenance_traversals_reused"], 1)

    def test_named_output_rejects_an_origin_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            results = next(item for item in data["inputs"] if item["name"] == "results")
            results["origin"] = True
            write(data_path, json.dumps(data, indent=2) + "\n")

            evaluation = _evaluate(summary)

            checks = {check.identity: check for check in evaluation.result.checks}
            command = checks["entry:e001:command:1:1"]
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(command.status, RESULTS.CheckStatus.FAIL)
            self.assertEqual(command.failure.code, "data.output.declaration_invalid")
            self.assertEqual(evidence.status, RESULTS.CheckStatus.PASS)
            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)

    def test_origin_evidence_is_valid_but_unrelated_output_is_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            catalog_path = entry.parent / "data/catalog.csv"
            write(catalog_path, "success_rate\n0.676\n")
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
                    " --input-catalog '<catalog>'", ""
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
            evidence = checks["evidence:e001:success-rate"]
            provenance = checks["provenance:e001:success-rate"]
            self.assertEqual(evidence.status, RESULTS.CheckStatus.FAIL)
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)
            assert provenance.failure is not None
            self.assertEqual(
                provenance.failure.code,
                "provenance.output.signature_mismatch",
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
            write(data_path, json.dumps(data, indent=2) + "\n")
            support_path = entry.parent / "pyrun-outputs.json"
            support = json.loads(support_path.read_text())
            support["outputs"]["data/results.csv"]["fingerprint"]["digest"] = (
                hashlib.sha256(
                    (entry.parent / "data" / "results.csv").read_bytes()
                ).hexdigest()
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
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "prior-results",
                                "kind": "file",
                                "location": os.path.relpath(
                                    retained / "results.csv", second_root
                                ),
                                "identity": {"algorithm": "sha256"},
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
                        "schema": "research-log-evidence/v4",
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
                    "--output-data '<results>'",
                    "--output-data '<results>' --output-report '<report>'",
                )
                .replace(
                    "The success rate was",
                    "<!-- eid:retained-report -->\n"
                    "```diff\nretained report\n```\n\nThe success rate was",
                ),
            )
            data_path = entry.parent / "data.json"
            data = json.loads(data_path.read_text())
            data["inputs"].append(
                {
                    "name": "report",
                    "kind": "file",
                    "location": "data/report.txt",
                    "identity": {"algorithm": "sha256"},
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
                "--input-catalog",
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
            evidence["records"][-1]["artifact_fingerprint"] = None
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            inline_baseline = _evaluate(summary)
            inline_check = next(
                check
                for check in inline_baseline.result.checks
                if check.identity == "evidence:e001:retained-report"
            )
            self.assertEqual(inline_check.scope, RESULTS.CheckScope.CONFORMANCE)
            assert inline_check.failure is not None
            self.assertEqual(inline_check.failure.code, "evidence.declaration.invalid")

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
                    "identity": {"algorithm": "sha256"},
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
                    "sources": [{"source": "<historical_report>", "locator": None}],
                    "transformation": None,
                    "artifact_fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(report.read_bytes()).hexdigest(),
                    },
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

            write(report, "replacement bytes\n")
            replacement = _evaluate(summary)
            artifact = next(
                check
                for check in replacement.result.checks
                if check.identity == "evidence:e001:historical-report"
            )
            self.assertEqual(artifact.status, RESULTS.CheckStatus.FAIL)
            assert artifact.failure is not None
            self.assertEqual(
                artifact.failure.code, "association.artifact.fingerprint_mismatch"
            )
            provenance = next(
                check
                for check in replacement.result.checks
                if check.identity == "provenance:e001:historical-report"
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.PASS)

            evidence["records"][-1].pop("artifact_fingerprint")
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            missing = _evaluate(summary)
            missing_check = next(
                check
                for check in missing.result.checks
                if check.identity == "evidence:e001:historical-report"
            )
            self.assertEqual(missing_check.scope, RESULTS.CheckScope.CONFORMANCE)
            assert missing_check.failure is not None
            self.assertEqual(missing_check.failure.code, "evidence.declaration.invalid")

            evidence["records"][-1]["artifact_fingerprint"] = None
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")
            unrecorded = _evaluate(summary)
            unrecorded_check = next(
                check
                for check in unrecorded.result.checks
                if check.identity == "evidence:e001:historical-report"
            )
            self.assertEqual(unrecorded_check.scope, RESULTS.CheckScope.EVIDENCE)
            assert unrecorded_check.failure is not None
            self.assertEqual(
                unrecorded_check.failure.code,
                "association.artifact.fingerprint_unrecorded",
            )

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
                        "identity": {"algorithm": "sha256"},
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
                    "artifact_fingerprint": {
                        "algorithm": "sha256",
                        "digest": hashlib.sha256(second.read_bytes()).hexdigest(),
                    },
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
            self.assertEqual(check.failure.code, "association.artifact.source_mismatch")

    def test_inline_artifact_source_obeys_the_presentation_byte_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "<!-- eid:results-diff -->\n"
                    "```diff\nsuccess_rate\n0.676\n```\n\nThe success rate was",
                ),
            )
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"].append(
                {
                    "id": "results-diff",
                    "document": "entries/2026-08-29-e001-study/e001.md",
                    "kind": "artifact",
                    "sources": [{"source": "<results>", "locator": None}],
                    "transformation": None,
                }
            )
            write(evidence_path, json.dumps(evidence, indent=2) + "\n")

            with mock.patch.object(PRESENTATION, "MAX_PRESENTATION_BYTES", 4):
                evaluation = _evaluate(summary)

            check = next(
                item
                for item in evaluation.result.checks
                if item.identity == "evidence:e001:results-diff"
            )
            self.assertEqual(check.scope, RESULTS.CheckScope.CONFORMANCE)
            assert check.failure is not None
            self.assertEqual(check.failure.code, "association.resource.too_large")

    def test_unmarked_diff_fence_requires_an_evidence_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The success rate was",
                    "```diff\n-old\n+new\n```\n\nThe success rate was",
                ),
            )

            evaluation = _evaluate(summary)

            failures = [
                check.failure
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "association.declaration_missing"
            ]
            self.assertTrue(failures)
            self.assertEqual(failures[0].observed["kind"], "artifact")

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
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--output-data '<results>'",
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
                    "--results '<results>'",
                    "--results '<results>' --scratch data/scratch.csv",
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

    def test_invalid_command_makes_the_whole_fence_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```bash\n./pyrun",
                    "```bash\npython scripts/run.py\n./pyrun",
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
            self.assertEqual(
                command_failure.failure.code, "invocation.command.unsupported"
            )
            self.assertEqual(provenance.status, RESULTS.CheckStatus.FAIL)

    def test_adjacent_comment_cannot_supply_a_material_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```\n\n`Results:`",
                    "```\n<!-- results are retained -->\n\n`Results:`",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion,
                RESULTS.CompletionState.COMPLETE_FINDINGS,
            )

    def test_pyrun_other_roles_are_shared_with_static_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "./pyrun scripts/model.py --input-catalog '<catalog>' "
                    "--results '<results>'",
                    "./pyrun --other-inputs input-catalog --other-outputs results -- "
                    "scripts/model.py --input-catalog '<catalog>' "
                    "--results '<results>'",
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
                all(status is RESULTS.CheckStatus.PASS for status in first_provenance)
            )
            self.assertTrue(
                any(status is RESULTS.CheckStatus.FAIL for status in second_provenance)
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
                .replace(" --input-catalog '<catalog>'", "")
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
                    "--input-catalog '<catalog>' ",
                    "--input-catalog '<catalog>' --input-data data/unrooted.csv ",
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
                    "identity": {"algorithm": "sha256"},
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

    def test_unsupported_command_has_complete_precise_failure_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "```bash\n./pyrun",
                    "```bash\npython scripts/run.py\n./pyrun",
                ),
            )

            evaluation = _evaluate(summary)

            failure = next(
                check.failure
                for check in evaluation.result.checks
                if check.failure is not None
                and check.failure.code == "invocation.command.unsupported"
            )
            self.assertTrue(failure.subject)
            self.assertTrue(failure.observed)
            self.assertEqual(
                failure.rule, "Recorded-Command Provenance And Material Graph"
            )

    def test_entry_evaluation_does_not_observe_unrelated_entry_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            unrelated_root = root / "docs/study/entries/2026-08-30-e002-unrelated"
            unrelated = unrelated_root / "e002.md"
            write(unrelated_root / "scripts/unrelated.py", "# must stay unopened\n")
            write(unrelated_root / "data/input.csv", "unrelated\n")
            write(
                unrelated_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "input",
                                "kind": "file",
                                "location": "data/input.csv",
                                "identity": {"algorithm": "sha256"},
                                "origin": True,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                unrelated,
                "# Unrelated\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/unrelated.py --input-data '<input>' "
                "--output-data data/unrelated.csv\n```\n",
            )
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [Unrelated](study/entries/2026-08-30-e002-unrelated/e002.md)\n",
            )

            with mock.patch.object(
                ENGINE,
                "_observe_script_identity",
                wraps=ENGINE._observe_script_identity,
            ) as observe:
                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        "2026-08-29",
                        ENGINE.EntryEvaluationTarget("e001", entry.parent),
                    )
                )

            self.assertEqual(result.context.selected_documents, ("e001",))
            self.assertEqual(result.context.dependency_entries, ())
            self.assertEqual(
                tuple(invocation.entry for invocation in result.context.invocations),
                ("e001",),
            )
            self.assertFalse(
                any(
                    "unrelated.py" in str(call.args[0])
                    for call in observe.call_args_list
                )
            )

    def test_entry_checks_match_the_full_evaluation_for_its_selected_entry(
        self,
    ) -> None:
        """A scoped evaluation preserves all applicable selected-entry checks."""

        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e001", entry.parent),
                )
            )

            full_checks = {
                check.identity: check
                for check in full.record.checks
                if check.identity
                not in {
                    "evidence:summary:5",
                    "orphan:log",
                    "provenance:summary:5",
                }
            }
            self.assertEqual(
                {check.identity: check for check in scoped.record.checks},
                full_checks,
            )

    def test_fixed_clock_full_record_metrics_projection_and_report_are_invariant(
        self,
    ) -> None:
        """Fixed time leaves the complete full-validation result reproducible."""

        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            request = ENGINE.EvaluationRequest(summary, "2026-08-29")
            with mock.patch("validation.engine.time.perf_counter", return_value=1.0):
                first = _evaluate_current_fixture(request)
                second = _evaluate_current_fixture(request)
            first_projection = HUMAN.project_findings(
                first.record, HUMAN.load_report_context(summary)
            )
            second_projection = HUMAN.project_findings(
                second.record, HUMAN.load_report_context(summary)
            )
            self.assertEqual(first.record, second.record)
            self.assertEqual(first.metrics, second.metrics)
            self.assertEqual(first_projection, second_projection)
            self.assertEqual(
                REPORT.compose_validation_report(first.record),
                REPORT.compose_validation_report(second.record),
            )

    def test_entry_closure_reaches_a_producer_listed_after_its_presentation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, target = _log(root)
            producer_root = target.parent
            shared = root / "shared.csv"
            write(shared, "value\n1\n")
            write(
                producer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": str(shared),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                target,
                "# Producer\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-data '<shared>'\n```\n\n"
                "`Results:`\n\nDone.\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            consumer = consumer_root / "e002.md"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": str(shared),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared-value",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["value"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "number",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer,
                "# Consumer\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/result.csv\n```\n\n`Results:`\n\n"
                "Value `1`<!-- eid:shared-value -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n",
            )

            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )

            self.assertEqual(result.context.selected_documents, ("e002",))
            self.assertEqual(result.context.dependency_entries, ("e001",))
            self.assertEqual(
                tuple(invocation.entry for invocation in result.context.invocations),
                ("e002", "e001"),
            )

    def test_entry_exposes_stale_producer_after_evidence_passes(
        self,
    ) -> None:
        """A matching consumer presentation cannot certify its stale producer."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = producer_root / "data/results.csv"
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n"
                "```\n\n`Results:`\n\nValue `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
            )
            # Keep the producer output and evidence byte intact, but invalidate its
            # recorded input fingerprint after its support record was written.
            write(producer_root / "data/catalog.csv", "id\n2\n")

            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            checks = {check.identity: check for check in result.record.checks}
            self.assertEqual(
                checks["evidence:e002:shared"].status, RESULTS.CheckStatus.PASS
            )
            self.assertEqual(result.context.dependency_entries, ("e001",))
            self.assertIn(
                "provenance.output.signature_mismatch",
                [check.failure.code for check in result.record.checks if check.failure],
            )

    def test_entry_data_conflict_prerequisites_match_full_when_relevant_only(
        self,
    ) -> None:
        """Scoped conflict checks include only conflicts touching the selected entry."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, selected = _log(root)
            selected_root = selected.parent
            shared = root / "shared.csv"
            unrelated = root / "unrelated.csv"
            write(shared, "shared\n")
            write(unrelated, "unrelated\n")
            selected_data = json.loads((selected_root / "data.json").read_text())
            catalog = next(
                item for item in selected_data["inputs"] if item["name"] == "catalog"
            )
            catalog["location"] = shared.as_posix()
            write(
                selected_root / "data.json", json.dumps(selected_data, indent=2) + "\n"
            )
            links = []
            for entry_id, path, comparison in (
                ("e002", shared, "comparison/a"),
                ("e003", unrelated, "comparison/b"),
                ("e004", unrelated, "comparison/c"),
            ):
                entry_root = (
                    root
                    / f"docs/study/entries/2026-08-3{int(entry_id[-1]) - 1}-{entry_id}"
                )
                write(entry_root / f"{entry_id}.md", f"# {entry_id}\n")
                write(
                    entry_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v5",
                            "inputs": [
                                {
                                    "name": "input",
                                    "kind": "file",
                                    "location": path.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": entry_id == "e003",
                                }
                            ],
                        }
                    )
                    + "\n",
                )
                links.append(
                    f"- [{entry_id}](study/entries/{entry_root.name}/{entry_id}.md)"
                )
            write(summary, summary.read_text() + "\n" + "\n".join(links) + "\n")

            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e001", selected_root),
                )
            )
            full_conflicts = {
                check.identity
                for check in full.record.checks
                if check.identity.startswith("conformance:data-conflict:")
            }
            scoped_conflicts = {
                check.identity
                for check in scoped.record.checks
                if check.identity.startswith("conformance:data-conflict:")
            }
            self.assertTrue(scoped_conflicts <= full_conflicts)
            self.assertEqual(len(scoped_conflicts), 1)
            self.assertEqual(len(full_conflicts), 2)
            full_checks = {check.identity: check for check in full.record.checks}
            scoped_checks = {check.identity: check for check in scoped.record.checks}
            conflict_id = next(iter(scoped_conflicts))
            self.assertEqual(scoped_checks[conflict_id], full_checks[conflict_id])
            selected_prerequisites = {
                check.identity
                for check in scoped.record.checks
                if check.identity.startswith("evidence:e001:")
            }
            self.assertIn("evidence:e001:success-rate", selected_prerequisites)
            for identity in selected_prerequisites:
                self.assertEqual(
                    scoped_checks[identity].dependencies,
                    full_checks[identity].dependencies,
                )

    def test_reached_conflict_prerequisite_matches_full_producer_blocking(self):
        """A later reached owner receives an already-emitted conflict prerequisite."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = root / "shared.csv"
            write(shared, "id\n1\n")
            producer_data = json.loads((producer_root / "data.json").read_text())
            catalog = next(
                item for item in producer_data["inputs"] if item["name"] == "catalog"
            )
            catalog["location"] = shared.as_posix()
            write(
                producer_root / "data.json",
                json.dumps(producer_data, indent=2) + "\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            results = producer_root / "data/results.csv"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "catalog",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                                "comparison": {
                                    "contract": DATA.EVIDENCE_COMPARISON_CONTRACT,
                                    "profile": "evidence",
                                },
                            },
                            {
                                "name": "results",
                                "kind": "file",
                                "location": results.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            },
                        ],
                    },
                    indent=2,
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<results>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<results>",
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
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
            )
            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            full_checks = {check.identity: check for check in full.record.checks}
            scoped_checks = {check.identity: check for check in scoped.record.checks}
            producer_command = "entry:e001:command:1:1"
            self.assertEqual(
                scoped_checks[producer_command], full_checks[producer_command]
            )
            self.assertEqual(
                scoped_checks[producer_command].status,
                RESULTS.CheckStatus.NOT_APPLICABLE,
            )
            self.assertEqual(
                scoped_checks["provenance:e002:shared"],
                full_checks["provenance:e002:shared"],
            )

    def test_entry_competing_producers_match_full_relevant_ambiguity(self) -> None:
        """Exact and overlapping owners stay in scoped closure and match full output."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, first = _log(root)
            shared = first.parent / "data/results.csv"
            third_root = root / "docs/study/entries/2026-08-30-e003-third"
            write(third_root / "scripts/model.py", "# producer\n")
            write(shared.parent / "catalog.csv", "id\n1\n")
            write(
                third_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            DATA.build_declared_generated(
                                "bundle",
                                "directory",
                                shared.parent.as_posix(),
                                entry_root=third_root,
                                identity=("catalog.csv",),
                            ).as_dict(),
                        ],
                    }
                )
                + "\n",
            )
            write(
                third_root / "e003.md",
                "# Second\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-dir '<bundle>'\n"
                "```\n\n`Results:`\n\nDone.\n",
            )
            consumer_root = root / "docs/study/entries/2026-08-31-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-31-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            fourth_root = root / "docs/study/entries/2026-08-30-e004-fourth"
            write(fourth_root / "scripts/model.py", "# producer\n")
            write(
                fourth_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            DATA.build_local_input(
                                "results",
                                "file",
                                shared.as_posix(),
                                entry_root=fourth_root,
                                origin=False,
                            ).as_dict(),
                        ],
                    }
                )
                + "\n",
            )
            write(
                fourth_root / "e004.md",
                "# Third\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/model.py --output-data '<results>'\n"
                "```\n\n`Results:`\n\nDone.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [First](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Third](study/entries/2026-08-30-e003-third/e003.md)\n"
                "- [Fourth](study/entries/2026-08-30-e004-fourth/e004.md)\n"
                "- [Consumer](study/entries/2026-08-31-e002-consumer/e002.md)\n",
            )
            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            self.assertEqual(
                scoped.context.dependency_entries, ("e001", "e003", "e004")
            )
            full_check = next(
                check
                for check in full.record.checks
                if check.identity == "provenance:e002:shared"
            )
            scoped_check = next(
                check
                for check in scoped.record.checks
                if check.identity == "provenance:e002:shared"
            )
            self.assertEqual(scoped_check, full_check)
            self.assertEqual(scoped_check.failure.code, "producer.ambiguous")
            producer_entries = {
                item.identity: item.entry for item in scoped.context.invocations
            }
            ambiguous_producers = scoped_check.failure.observed["producers"]
            self.assertEqual(
                [producer_entries[item] for item in ambiguous_producers],
                ["e001", "e004"],
            )
            self.assertTrue(
                any(item.entry == "e003" for item in scoped.context.invocations)
            )
            self.assertFalse(
                any(
                    check.failure is not None
                    and check.failure.code == "directory.producer.conflict"
                    for check in scoped.record.checks
                )
            )

    def test_index_log_preserves_summary_order_across_split_documents(self) -> None:
        """Declaration order follows summary order, not physical-root order."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = _log(root)
            first = entry.with_name("e001a.md")
            second = entry.with_name("e001b.md")
            write(first, "# First\n")
            write(second, "# Second\n")
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Second](study/entries/2026-08-29-e001-study/e001b.md)\n"
                "- [Main](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [First](study/entries/2026-08-29-e001-study/e001a.md)\n",
            )
            state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
            index = ENGINE._index_log(summary.read_text(encoding="utf-8"), state)
            self.assertEqual(
                tuple(path.name for path in index.document_order),
                ("e001b.md", "e001.md", "e001a.md"),
            )

    def test_interleaved_split_producer_admits_only_earlier_document(self) -> None:
        """A later split producer cannot enter a consumer's scoped frontier."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            earlier = producer.with_name("e001a.md")
            later = producer.with_name("e001b.md")
            producer.replace(earlier)
            write(later, earlier.read_text(encoding="utf-8"))
            shared = producer_root / "data/results.csv"
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Earlier](study/entries/2026-08-29-e001-study/e001a.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Later](study/entries/2026-08-29-e001-study/e001b.md)\n",
            )
            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            scoped = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(
                    summary,
                    "2026-08-29",
                    ENGINE.EntryEvaluationTarget("e002", consumer_root),
                )
            )
            self.assertEqual(scoped.context.dependency_entries, ("e001",))
            self.assertEqual(
                tuple(
                    item.document.rsplit("/", 1)[-1]
                    for item in scoped.context.invocations
                ),
                ("e001a.md", "e002.md"),
            )
            self.assertEqual(
                tuple(
                    item.document.rsplit("/", 1)[-1]
                    for item in full.context.invocations
                ),
                ("e001a.md", "e002.md", "e001b.md"),
            )
            full_check = next(
                check
                for check in full.record.checks
                if check.identity == "provenance:e002:shared"
            )
            scoped_check = next(
                check
                for check in scoped.record.checks
                if check.identity == "provenance:e002:shared"
            )
            self.assertEqual(scoped_check, full_check)

    def test_reached_dependency_document_omits_unrelated_broken_command(
        self,
    ) -> None:
        """Closure observation excludes an unrelated command in a reached document."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, producer = _log(root)
            producer_root = producer.parent
            shared = producer_root / "data/results.csv"
            write(producer_root / "scripts/unrelated.py", "# must remain unopened\n")
            producer.write_text(
                producer.read_text() + "\n```bash\n"
                "./pyrun scripts/unrelated.py --input-data data/missing.csv "
                "--output-data data/unrelated.csv\n```\n",
                encoding="utf-8",
            )
            consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
            write(consumer_root / "scripts/use.py", "# consumer\n")
            write(
                consumer_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "shared",
                                "kind": "file",
                                "location": shared.as_posix(),
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                consumer_root / "e002.md",
                "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/use.py --input-data '<shared>' "
                "--output-data data/local.csv\n```\n\n`Results:`\n\n"
                "Value `67.6%`<!-- eid:shared -->.\n",
            )
            write(
                consumer_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v4",
                        "records": [
                            {
                                "id": "shared",
                                "document": "entries/2026-08-30-e002-consumer/e002.md",
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<shared>",
                                        "locator": {"select": [["success_rate"]]},
                                    }
                                ],
                                "transformation": {
                                    "form": "percentage",
                                    "source": {"input": 0, "item": 0},
                                },
                            }
                        ],
                    }
                )
                + "\n",
            )
            unrelated_root = root / "docs/study/entries/2026-08-31-e003-unrelated"
            write(
                unrelated_root / "data.json",
                json.dumps(
                    {
                        "schema": "research-log-data/v5",
                        "inputs": [
                            {
                                "name": "missing",
                                "kind": "file",
                                "location": "data/missing.csv",
                                "identity": {"algorithm": "sha256"},
                                "origin": True,
                            }
                        ],
                    }
                )
                + "\n",
            )
            write(
                unrelated_root / "e003.md",
                "# Unrelated\n\n## Run\n\n`Steps:`\n\n```bash\n"
                "./pyrun scripts/missing.py --input-data '<missing>' "
                "--output-data data/unrelated.csv\n```\n\n`Results:`\n\n"
                "Broken `1`<!-- eid:broken -->.\n",
            )
            write(unrelated_root / "evidence.json", "{ broken evidence\n")
            write(unrelated_root / "retention.json", "{ broken retention\n")
            write(unrelated_root / "pyrun.json", "{ broken state\n")
            write(
                summary,
                "# Study\n\n## Entries\n\n"
                "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n"
                "- [Unrelated](study/entries/2026-08-31-e003-unrelated/e003.md)\n",
            )
            full = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )
            with (
                mock.patch.object(
                    ENGINE,
                    "_observe_script_identity",
                    wraps=ENGINE._observe_script_identity,
                ) as observe,
                mock.patch.object(
                    ENGINE, "load_evidence_file", wraps=ENGINE.load_evidence_file
                ) as evidence_loader,
                mock.patch.object(
                    ENGINE, "load_retention_file", wraps=ENGINE.load_retention_file
                ) as retention_loader,
                mock.patch.object(
                    ENGINE, "load_pyrun_state", wraps=ENGINE.load_pyrun_state
                ) as state_loader,
                mock.patch.object(
                    COMMANDS, "_observe_script", wraps=COMMANDS._observe_script
                ) as command_script,
                mock.patch.object(
                    ENGINE, "observe_fingerprint", wraps=ENGINE.observe_fingerprint
                ) as fingerprint,
                mock.patch.object(
                    ENGINE, "_entry_presentations", wraps=ENGINE._entry_presentations
                ) as presentations,
            ):
                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        "2026-08-29",
                        ENGINE.EntryEvaluationTarget("e002", consumer_root),
                    )
                )
            self.assertFalse(
                any(
                    "unrelated.py" in str(call.args[0])
                    for call in observe.call_args_list
                )
            )
            self.assertFalse(
                any("unrelated" in check.identity for check in result.record.checks)
            )
            self.assertEqual(result.context.selected_documents, ("e002",))
            self.assertEqual(result.context.dependency_entries, ("e001",))
            full_check = next(
                check
                for check in full.record.checks
                if check.identity == "evidence:e002:shared"
            )
            scoped_check = next(
                check
                for check in result.record.checks
                if check.identity == "evidence:e002:shared"
            )
            self.assertEqual(scoped_check, full_check)
            for name, loader in (
                ("evidence", evidence_loader),
                ("retention", retention_loader),
                ("state", state_loader),
                ("command", command_script),
                ("fingerprint", fingerprint),
                ("presentations", presentations),
            ):
                calls = loader.call_args_list
                if name == "presentations":
                    self.assertFalse(
                        any(call.args[0].root == unrelated_root for call in calls),
                        name,
                    )
                    continue
                self.assertFalse(
                    any(unrelated_root.as_posix() in str(call.args) for call in calls),
                    name,
                )
            self.assertEqual(
                tuple(item.entry for item in result.context.invocations),
                ("e001", "e002"),
            )
            self.assertEqual(
                tuple(entry for entry, _ in result.context.registries),
                ("e002", "e001"),
            )
            self.assertEqual(
                next(
                    check
                    for check in result.record.checks
                    if check.identity == "provenance:e002:shared"
                ),
                next(
                    check
                    for check in full.record.checks
                    if check.identity == "provenance:e002:shared"
                ),
            )

    def test_entry_producer_closure_covers_exact_reverse_and_rejected_declarations(
        self,
    ) -> None:
        """Entry closure admits each declaration shape without full evaluation."""

        for shape in ("exact", "reverse", "rejected"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                summary, producer = _log(root)
                producer_root = producer.parent
                if shape == "reverse":
                    member = producer_root / "data/bundle/member.csv"
                    write(member, "value\n1\n")
                    producer.write_text(
                        producer.read_text(encoding="utf-8").replace(
                            "--output-data '<results>'", "--output-dir data/bundle"
                        ),
                        encoding="utf-8",
                    )
                    material = member
                else:
                    material = producer_root / "data/results.csv"
                if shape == "rejected":
                    producer.write_text(
                        producer.read_text(encoding="utf-8").replace(
                            "./pyrun scripts/model.py", "python scripts/model.py"
                        ),
                        encoding="utf-8",
                    )
                consumer_root = root / "docs/study/entries/2026-08-30-e002-consumer"
                write(consumer_root / "scripts/use.py", "# consumer\n")
                write(
                    consumer_root / "data.json",
                    json.dumps(
                        {
                            "schema": "research-log-data/v5",
                            "inputs": [
                                {
                                    "name": "shared",
                                    "kind": "file",
                                    "location": material.as_posix(),
                                    "identity": {"algorithm": "sha256"},
                                    "origin": False,
                                }
                            ],
                        }
                    )
                    + "\n",
                )
                write(
                    consumer_root / "e002.md",
                    "# Consumer\n\n## Run\n\n`Steps:`\n\n```bash\n"
                    "./pyrun scripts/use.py --input-data '<shared>' "
                    "--output-data data/local.csv\n"
                    "```\n\n`Results:`\n\nDone.\n",
                )
                write(
                    summary,
                    "# Study\n\n## Entries\n\n"
                    "- [Producer](study/entries/2026-08-29-e001-study/e001.md)\n"
                    "- [Consumer](study/entries/2026-08-30-e002-consumer/e002.md)\n",
                )

                if shape == "rejected":
                    state = ENGINE._ScanState(summary, summary.with_suffix(""), root)
                    index = ENGINE._index_log(
                        summary.read_text(encoding="utf-8"), state
                    )
                    self.assertIn(
                        material.resolve().as_posix(), index.rejected_candidates
                    )

                result = _evaluate_current_fixture(
                    ENGINE.EvaluationRequest(
                        summary,
                        "2026-08-29",
                        ENGINE.EntryEvaluationTarget("e002", consumer_root),
                    )
                )

                self.assertEqual(result.context.dependency_entries, ("e001",))
                if shape == "rejected":
                    self.assertIn(
                        "invocation.command.unsupported",
                        [
                            check.failure.code
                            for check in result.record.checks
                            if check.failure
                        ],
                    )
                else:
                    self.assertTrue(
                        any(
                            invocation.entry == "e001"
                            for invocation in result.context.invocations
                        )
                    )

    def test_evaluation_exposes_loaded_entry_material_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))

            result = _evaluate_current_fixture(
                ENGINE.EvaluationRequest(summary, "2026-08-29")
            )

            self.assertEqual(len(result.context.materials), 1)
            material = result.context.materials[0]
            self.assertEqual(material.entry_id, "e001")
            self.assertEqual(material.entry_root, entry.parent.resolve())
            self.assertEqual(material.document, entry.resolve())
            self.assertIsNotNone(material.data)
            self.assertIsNotNone(material.evidence)

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


if __name__ == "__main__":
    unittest.main()
