from __future__ import annotations

import importlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import research_log_data as DATA
from research_log_validation_test_support import mock, unittest, write

ENGINE = importlib.import_module("validation.engine")
MECHANICAL = importlib.import_module("validation.mechanical")
RESULTS = importlib.import_module("validation.mechanical_results")
LOCATOR = importlib.import_module("validation.locator")


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
    write(entry_root / "data" / "results.csv", "success_rate\n0.676\n")
    write(
        entry_root / "data.json",
        json.dumps(
            {
                "schema": "research-log-data/v1",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "https://example.test/catalog.csv",
                        "fingerprint": {
                            "algorithm": "immutable-source",
                            "value": "fixture-catalog/v1",
                        },
                        "external": {
                            "source": "test fixture",
                            "identity": "fixture-catalog/v1",
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry_root / "evidence.json",
        """{
  "schema": "research-log-evidence/v2",
  "records": [
    {
      "id": "success-rate",
      "document": "entries/2026-08-29-e001-study/e001.md",
      "kind": "statistic",
      "sources": [
        {
          "source": "data/results.csv",
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


def _evaluate(summary: Path, *, prior_cache: object = None) -> object:
    return MECHANICAL.evaluate_mechanical(
        MECHANICAL.MechanicalEvaluationRequest(
            summary, "2026-08-29", prior_cache=prior_cache
        ),
        ENGINE.mechanical_policy(),
    )


def _remote_data_json() -> str:
    return (
        json.dumps(
            {
                "schema": "research-log-data/v1",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "https://example.test/catalog.csv",
                        "fingerprint": {
                            "algorithm": "immutable-source",
                            "value": "fixture-catalog/v1",
                        },
                        "external": {
                            "source": "test fixture",
                            "identity": "fixture-catalog/v1",
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )


class EngineV2EndToEndTests(unittest.TestCase):
    def test_entry_data_reference_accepts_one_split_entry_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "entries/2026-08-29-e009-split-study"
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.entries = [
                ENGINE._Entry(
                    "e009a", entry_root / "e009a.md", entry_root, None, None, None
                ),
                ENGINE._Entry(
                    "e009b", entry_root / "e009b.md", entry_root, None, None, None
                ),
            ]

            referenced_entry, path = ENGINE._resolve_entry_data_reference(
                "<e009>/results.csv", state
            )

            self.assertEqual(referenced_entry.root, entry_root)
            self.assertEqual(path, entry_root / "data/results.csv")

    def test_entry_data_reference_rejects_invalid_member_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "entries/2026-08-29-e009-study"
            state = ENGINE._ScanState(root / "study.md", root, root)
            state.entries = [
                ENGINE._Entry(
                    "e009", entry_root / "e009.md", entry_root, None, None, None
                )
            ]

            for source in (
                "<e009>/",
                "<e009>/../results.csv",
                "<e009>/nested//results.csv",
                "<e009>/nested\\results.csv",
                "<e009>/https://example.test/results.csv",
            ):
                with (
                    self.subTest(source=source),
                    self.assertRaises(ENGINE.EngineV2Error) as raised,
                ):
                    ENGINE._resolve_entry_data_reference(source, state)
                self.assertEqual(raised.exception.code, "locator.path.unresolved")

    def test_entry_data_reference_rejects_missing_and_ambiguous_families(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ENGINE._ScanState(root / "study.md", root, root)
            with self.assertRaises(ENGINE.EngineV2Error) as missing:
                ENGINE._resolve_entry_data_reference("<e009>/results.csv", state)
            self.assertEqual(missing.exception.code, "locator.path.unresolved")
            self.assertEqual(missing.exception.observed["matches"], 0)

            first_root = root / "entries/2026-08-29-e009a-study"
            second_root = root / "entries/2026-08-30-e009b-study"
            state.entries = [
                ENGINE._Entry(
                    "e009a", first_root / "e009a.md", first_root, None, None, None
                ),
                ENGINE._Entry(
                    "e009b", second_root / "e009b.md", second_root, None, None, None
                ),
            ]
            with self.assertRaises(ENGINE.EngineV2Error) as ambiguous:
                ENGINE._resolve_entry_data_reference("<e009>/results.csv", state)
            self.assertEqual(ambiguous.exception.code, "locator.path.unresolved")
            self.assertEqual(ambiguous.exception.observed["matches"], 2)

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
                    "external": {
                        "source": "test fixture",
                        "identity": "bad/v1",
                    },
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

    def test_cross_entry_data_conflict_does_not_block_unrelated_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = _log(root)
            links: list[str] = []
            for entry_id, date, identity in (
                ("e002", "2026-08-30", "conflict/v1"),
                ("e003", "2026-08-31", "conflict/v2"),
            ):
                entry_root = root / f"docs/study/entries/{date}-{entry_id}-conflict"
                write(entry_root / "scripts/model.py", "# fixture\n")
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
                            "schema": "research-log-data/v1",
                            "inputs": [
                                {
                                    "name": "conflict",
                                    "kind": "file",
                                    "location": "https://example.test/conflict.csv",
                                    "fingerprint": {
                                        "algorithm": "immutable-source",
                                        "value": identity,
                                    },
                                    "external": {
                                        "source": "fixture",
                                        "identity": identity,
                                    },
                                },
                                {
                                    "name": f"safe-{entry_id}",
                                    "kind": "file",
                                    "location": (
                                        f"https://example.test/{entry_id}.csv"
                                    ),
                                    "fingerprint": {
                                        "algorithm": "immutable-source",
                                        "value": f"safe/{entry_id}",
                                    },
                                    "external": {
                                        "source": "fixture",
                                        "identity": f"safe/{entry_id}",
                                    },
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
            write(entry_root / "data.json", _remote_data_json())
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
            write(entry_root / "data.json", _remote_data_json())
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
  "schema": "research-log-evidence/v2",
  "records": [
    {
      "id": "unlisted-value",
      "document": "entries/2026-08-29-e001-study/e001b.md",
      "kind": "statistic",
      "sources": [
        {"source": "data/result.csv", "locator": {"select": [["value"]]}}
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

    def test_cross_entry_sources_resolve_against_referenced_entry(self) -> None:
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
                second_root / "evidence.json",
                json.dumps(
                    {
                        "schema": "research-log-evidence/v2",
                        "records": [
                            {
                                "id": "prior-success-rate",
                                "document": (
                                    "entries/2026-08-29-e002-cross-entry-study/e002.md"
                                ),
                                "kind": "statistic",
                                "sources": [
                                    {
                                        "source": "<e001>/results.csv",
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
            log_relative_checks = {
                check.identity: check for check in log_relative_evaluation.result.checks
            }
            self.assertEqual(
                log_relative_checks["evidence:e002:prior-success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )
            self.assertEqual(
                log_relative_checks["provenance:e002:prior-success-rate"].status,
                RESULTS.CheckStatus.PASS,
            )

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

    def test_direct_artifact_bypasses_evidence_file_and_enters_provenance(self) -> None:
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
                    "[Retained report](data/report.txt)\n\nThe success rate was",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            identities = {check.identity for check in evaluation.result.checks}
            self.assertTrue(
                any(
                    identity.startswith("provenance:artifact:")
                    for identity in identities
                )
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
            with mock.patch.object(ENGINE, "observe_source", side_effect=unavailable):
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

    def test_unrelated_script_body_never_changes_producer_edges(self) -> None:
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
            self.assertEqual(first_provenance, second_provenance)

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
            evidence_path = entry.parent / "evidence.json"
            evidence = json.loads(evidence_path.read_text())
            evidence["records"][0]["sources"][0]["source"] = (
                "<project>/docs/other/entries/e001/data/results.csv"
            )
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

            unchanged = _evaluate(summary, prior_cache=first.scan["cache"])
            write(entry.parent / "scripts" / "model.py", "# changed identity\n")
            changed = _evaluate(summary, prior_cache=first.scan["cache"])

            self.assertGreater(unchanged.metrics["checks_unchanged"], 0)
            self.assertLess(
                changed.metrics["checks_unchanged"],
                unchanged.metrics["checks_unchanged"],
            )
            self.assertEqual(
                changed.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

    def test_unchanged_comparison_requires_exact_check_and_cache_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = _log(Path(directory))
            first = _evaluate(summary)
            corrupted_cache = json.loads(json.dumps(first.scan["cache"]))
            for cached in corrupted_cache["checks"].values():
                cached["check"]["subject"] = "wrong subject"

            corrupted = _evaluate(summary, prior_cache=corrupted_cache)
            extra_cache = json.loads(json.dumps(first.scan["cache"]))
            extra_cache["extra"] = True
            extra_field = _evaluate(summary, prior_cache=extra_cache)

            self.assertEqual(corrupted.metrics["checks_unchanged"], 0)
            self.assertEqual(extra_field.metrics["checks_unchanged"], 0)
            self.assertEqual(extra_field.metrics["artifact_identity_seeds"], 0)
            self.assertEqual(extra_field.metrics["source_hashes_reused"], 0)
            self.assertEqual(
                corrupted.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            self.assertEqual(
                extra_field.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )


if __name__ == "__main__":
    unittest.main()
