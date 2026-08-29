from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

from research_log_validation_test_support import mock, unittest, write

ENGINE = importlib.import_module("validation.engine")
MECHANICAL = importlib.import_module("validation.mechanical")
RESULTS = importlib.import_module("validation.mechanical_results")
LOCATOR = importlib.import_module("validation.locator")


def _log(root: Path, *, output_option: str = "output-data") -> tuple[Path, Path]:
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
        entry_root / "data.csv",
        "name,type,location\ncatalog,csv,https://example.test/catalog.csv\n",
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
        "`Question:`\n\nWhat is the success rate?\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/model.py --catalog '<catalog>' "
        f"--{output_option} data/results.csv\n"
        "```\n"
        "<!-- command type = model -->\n\n"
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


class EngineV2EndToEndTests(unittest.TestCase):
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
                scopes[RESULTS.CheckScope.HYGIENE], RESULTS.CheckStatus.PASS
            )
            self.assertEqual(evaluation.metrics["source_evaluations"], 1)
            self.assertEqual(evaluation.metrics["source_reads"], 1)
            self.assertEqual(evaluation.metrics["script_hashes"], 1)
            self.assertEqual(evaluation.metrics["markdown_reads"], 2)

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
                "V2 Expanded Mechanical Locator Language",
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
            summary, _ = _log(Path(directory), output_option="results")

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

    def test_adjacent_annotation_is_a_complete_mechanical_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory), output_option="results")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "<!-- command type = model -->",
                    "<!-- command type = model; results = output -->",
                ),
            )

            evaluation = _evaluate(summary)

            self.assertEqual(
                evaluation.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

    def test_upgrade_boundary_and_missing_summary_reference_are_precise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            write(entry.parent / "evidence.csv", "entry\n")

            legacy = _evaluate(summary)

            legacy_failures = [
                check.failure.code
                for check in legacy.result.checks
                if check.failure is not None
            ]
            self.assertEqual(legacy_failures, ["validation.upgrade_required"])

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
            self.assertIn("provenance.root.missing", codes)

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
            self.assertIn("lineage.missing", codes)

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
                entry.read_text(encoding="utf-8").replace("type = model", "type=model"),
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

    def test_unchanged_dependency_results_reuse_and_changed_script_reopens(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, entry = _log(Path(directory))
            first = _evaluate(summary)

            unchanged = _evaluate(summary, prior_cache=first.scan["cache"])
            write(entry.parent / "scripts" / "model.py", "# changed identity\n")
            changed = _evaluate(summary, prior_cache=first.scan["cache"])

            self.assertGreater(unchanged.metrics["checks_reused"], 0)
            self.assertLess(
                changed.metrics["checks_reused"], unchanged.metrics["checks_reused"]
            )
            self.assertEqual(
                changed.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )

    def test_cache_reuse_requires_exact_current_check_and_cache_contract(self) -> None:
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

            self.assertEqual(corrupted.metrics["checks_reused"], 0)
            self.assertEqual(extra_field.metrics["checks_reused"], 0)
            self.assertEqual(
                corrupted.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )
            self.assertEqual(
                extra_field.result.completion, RESULTS.CompletionState.COMPLETE_CLEAR
            )


if __name__ == "__main__":
    unittest.main()
