import os

from research_log_validation_test_support import (
    ADJUDICATION,
    CANONICAL_SCAN_LOG,
    CONTRACTS,
    DECISIONS,
    DISCOVERY,
    EVIDENCE,
    GRAPH,
    GRAPH_ADAPTER,
    GRAPH_STORE,
    IDENTITIES,
    INVENTORY,
    PUBLICATION,
    RECORDS,
    RENDER,
    REPORT,
    RUNTIME,
    SCAN,
    STATE,
    Path,
    adjudication_for,
    complete_adjudication,
    identity_ending,
    json,
    make_log,
    mock,
    prepare_adjudication,
    re,
    tempfile,
    unittest,
    write,
)


class RenderTests(unittest.TestCase):
    def test_state_contract_accepts_unscoped_directory_dependency_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads((output / "validation-state.json").read_text())
            dependency = state["completed_checks"][0]["dependencies"][0]
            dependency["identity"] = {"members": 2, "sha256": "a" * 64}

            decoded = STATE.decode_validation_state(
                state, schema_version=RUNTIME.STATE_SCHEMA_VERSION
            )

            self.assertEqual(
                decoded["completed_checks"][0]["dependencies"][0]["identity"],
                {"members": 2, "sha256": "a" * 64},
            )






    def test_record_bundle_identity_rejects_empty_filename_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for filenames in ((), ("",)):
                with self.subTest(filenames=filenames):
                    with self.assertRaisesRegex(
                        RECORDS.RecordPublicationError,
                        "unique basenames",
                    ):
                        RECORDS.record_bundle_identity(output, filenames)

    def test_record_publication_rejects_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            staged.mkdir()
            output = root / "output"
            output.mkdir()
            for filenames in (("../summary.md",), ("nested/validation.md",)):
                with self.subTest(filenames=filenames), self.assertRaisesRegex(
                    RECORDS.RecordPublicationError, "unique basenames"
                ):
                    RECORDS.publish_record_bundle(
                        staged,
                        output,
                        filenames,
                        RECORDS.PublicationGuard("unused"),
                    )
            linked = root / "linked-output"
            linked.symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(
                RECORDS.RecordPublicationError, "must not be a symlink"
            ):
                RECORDS.publish_record_bundle(
                    staged,
                    linked,
                    ("validation.md",),
                    RECORDS.PublicationGuard("unused"),
                )

    def test_validation_publication_requires_exact_generated_allowlist(self) -> None:
        class Bundle:
            report_text = ""
            failure_text = None
            state = {}
            graph_record = {}

        with tempfile.TemporaryDirectory() as directory:
            target = PUBLICATION.ValidationPublicationTarget(
                Path(directory) / "log",
                "unused",
                ("validation.md", "summary.md"),
                (),
                "validation-index.json",
            )
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "generated-file allowlist"
            ):
                PUBLICATION.publish_validation_bundle(
                    Bundle(), target, lambda: None, lambda *_args: {"ok": True}
                )

    def test_status_count_labels_are_grammatical(self) -> None:
        self.assertEqual(REPORT._counted(1, "target"), "1 target")
        self.assertEqual(REPORT._counted(2, "target"), "2 targets")
        self.assertEqual(REPORT._counted(1, "eligible target"), "1 eligible target")

    def test_render_accepts_documented_orphan_failure_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            orphan_items = adjudication["entries"][0]["orphan_items"]
            candidate_identity = next(
                item["identity"]
                for item in scan["entries"][0]["orphan_candidates"]
                if item["identity"].endswith("data/command-only.csv")
            )
            orphan_item = next(
                item for item in orphan_items if item["identity"] == candidate_identity
            )
            orphan_item.update({"decision": "unresolved", "basis": "-"})
            adjudication["entries"][0]["targets"].append(
                {
                    "target": ADJUDICATION.ORPHAN_TARGET,
                    "sections": ["-"],
                    "integrity": "N/A",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": "1 unresolved item",
                    "dependencies": [
                        {"path": scan["entries"][0]["path"], "role": "entry"}
                    ],
                    "findings": [
                        {
                            "check": "Provenance",
                            "finding": "One research script is not used.",
                        }
                    ],
                    "orphan_items": orphan_items,
                }
            )
            output = root / "records"

            RUNTIME.render_records(adjudication, scan, output)
            lint = RUNTIME.lint_records(output)

            self.assertTrue(lint["ok"], lint["issues"])
            self.assertIn(
                "| Orphaned artifacts, scripts, and references | `-` | `N/A` | "
                "`FAIL` | `N/A` | 1 unresolved item |",
                (output / "validation.md").read_text(encoding="utf-8"),
            )
            write(
                output / "validation.md",
                (output / "validation.md")
                .read_text(encoding="utf-8")
                .replace(
                    "`FAIL` | `N/A` | 1 unresolved item |",
                    "`FAIL` | `-` | 1 unresolved item |",
                ),
            )
            self.assertFalse(RUNTIME.lint_records(output)["ok"])

    def test_render_rejects_unreachable_orphan_with_graph_acceptance_basis(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            unused = entry.parent / "scripts" / "unused.py"
            write(unused, "VALUE = 1\n")
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            item = next(
                value
                for value in adjudication["entries"][0]["orphan_items"]
                if value["identity"].endswith("scripts/unused.py")
            )
            item["basis"] = "graph"

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "orphan dispositions disagree with canonical graph reachability",
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_render_rejects_retention_basis_without_matching_validation_note(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["orphan_items"][0]["basis"] = (
                "validation-note:" + "0" * 64
            )

            with self.assertRaisesRegex(
                GRAPH.GraphContractError,
                "retention basis does not match one Validation note",
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_render_and_lint_reject_success_dependency_orphan_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "scripts" / "unused.py", "VALUE = 1\n")
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            entry_result = adjudication["entries"][0]
            orphan_item = next(
                item
                for item in entry_result["orphan_items"]
                if item["identity"].endswith("scripts/unused.py")
            )
            orphan_item.update({"decision": "unresolved", "basis": "-"})
            entry_result["targets"].append(
                {
                    "target": ADJUDICATION.ORPHAN_TARGET,
                    "sections": ["-"],
                    "integrity": "N/A",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": "1 unresolved item",
                    "dependencies": [{"path": entry_result["path"], "role": "entry"}],
                    "findings": [
                        {
                            "check": "Provenance",
                            "finding": "One research script is not used.",
                        }
                    ],
                    "orphan_items": entry_result["orphan_items"],
                }
            )
            successful = entry_result["targets"][0]
            recorded_commands = next(
                item["commands"] for item in scan["entries"] if item["id"] == "e001"
            )
            successful["producer_invocation"] = (
                GRAPH_ADAPTER.recorded_invocation_identity(
                    "e001", 2, recorded_commands[1]
                )
            )
            successful["dependencies"].append(
                {"path": orphan_item["identity"], "role": "producer"}
            )

            rejected = root / "rejected"
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "unresolved orphan is a dependency of a successful check",
            ):
                RUNTIME.render_records(adjudication, scan, rejected)
            self.assertFalse(rejected.exists())

            successful["dependencies"].pop()
            orphan_path = Path(orphan_item["identity"])
            successful["dependencies"].append(
                {
                    "path": orphan_path.parent.as_posix(),
                    "role": "input",
                    "members": [orphan_path.name],
                }
            )
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "unresolved orphan is a dependency of a successful check",
            ):
                RUNTIME.render_records(adjudication, scan, rejected)
            self.assertFalse(rejected.exists())
            successful["dependencies"].pop()

            output = root / "records"
            RUNTIME.render_records(adjudication, scan, output)
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            check = next(
                item
                for item in state["completed_checks"]
                if item["result"] == "2026-08-07" and item["entry"] == "e001"
            )
            check["dependencies"].append(
                {
                    "path": orphan_item["identity"],
                    "role": "producer",
                    "identity": INVENTORY.file_identity(
                        Path(scan["resolved_paths"][orphan_item["identity"]])
                    ),
                }
            )
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            lint = RUNTIME.lint_records(output)

            self.assertTrue(
                any(
                    "unresolved orphan is a dependency of a successful check" in issue
                    for issue in lint["issues"]
                )
            )

    def test_render_writes_status_summary_without_changing_maintained_summary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            before = summary.read_bytes()
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)

            report = (output / "validation.md").read_text(encoding="utf-8")
            self.assertEqual(summary.read_bytes(), before)
            self.assertIn("## Status Summary", report)
            self.assertIn("- Report updated: `2026-08-07`", report)
            self.assertIn("- Summary statistics: 2026-08-07 — 1 checked", report)
            self.assertIn("`FAIL` - 1 of 3 targets failed", report)
            self.assertIn("| e001 | 2026-08-07 |", report)
            self.assertIn("[Remediation](#remediation)", report)

    def test_render_leaves_complete_research_owned_tree_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            generated = set(RUNTIME.VALIDATION_RECORD_FILENAMES) | {
                RECORDS.LOCK_FILENAME
            }
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and path.name not in generated
            }
            scan, _ = RUNTIME.scan_log(summary, jobs=1)

            RUNTIME.render_records(
                adjudication_for(scan, entry), scan, summary.with_suffix("")
            )

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and path.name not in generated
            }
            self.assertEqual(after, before)

    def test_fixed_summary_link_is_projection_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            original = summary.read_text(encoding="utf-8")
            write(
                summary,
                original.rstrip()
                + "\n\n## Validation\n\nLast validated on: NOT RUN\n\n"
                "Summary statistics: NOT RUN\n\n"
                "## AI Use\n\nResearcher-led fixture.\n",
            )
            before = IDENTITIES.summary_validation_identity(summary)
            write(
                summary,
                original.rstrip().replace(
                    "# Mini Log\n\n",
                    "# Mini Log\n"
                    "Validation: [latest completed report](mini/validation.md)\n\n",
                )
                + "\n\n## AI Use\n\nResearcher-led fixture.\n",
            )
            self.assertEqual(IDENTITIES.summary_validation_identity(summary), before)

    def test_synthesis_only_entry_change_does_not_invalidate_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").rstrip()
                + "\n\n## Historical context\n\n"
                + "`Findings:`\n\nResearcher-validated narrative only.\n",
            )

            _, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["reusable_checks"], 7)
            self.assertEqual(metrics["rerun_checks"], 0)

    def test_shifted_summary_support_locator_is_rediscovered_mechanically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "## Results",
                    "## Context\n\n`Findings:`\n\nHistorical context.\n\n## Results",
                ),
            )
            write(entry.parent / "data" / "output.csv", "name,value\nresult,2.0\n")

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            prepared = prepare_adjudication(
                changed, "2026-08-08", RUNTIME.RULES_VERSION
            )

            self.assertTrue(prepared["summary"][0]["support_reviewed"])
            self.assertFalse(
                any(
                    item["kind"] == "semantic_provenance"
                    for item in prepared["review_queue"]
                )
            )

    def test_render_summary_only_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["requested_scope"] = "Summary claims only"
            adjudication["scope"]["entries"] = []
            adjudication["entries"] = []
            output = root / "records"

            counts = RUNTIME.render_records(adjudication, scan, output)
            lint = RUNTIME.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )

            self.assertTrue(lint["ok"], lint["issues"])
            self.assertEqual(counts["summary_rows"], 1)
            self.assertEqual(counts["entry_rows"], 0)
            self.assertEqual(counts["entries"], 0)

    def test_partial_scope_cannot_initialize_canonical_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["requested_scope"] = "Summary claims only"
            adjudication["scope"]["entries"] = []
            adjudication["entries"] = []

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "canonical rendering requires complete-log scope",
            ):
                RUNTIME.render_records(adjudication, scan, summary.with_suffix(""))

            self.assertFalse(
                (summary.with_suffix("") / "validation-state.json").exists()
            )

    def test_partial_scope_does_not_overwrite_existing_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            before = (output / "validation.md").read_bytes()
            adjudication = adjudication_for(scan, entry)
            adjudication["scope"]["entries"] = []
            adjudication["entries"] = []

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "cannot overwrite"
            ):
                RUNTIME.render_records(adjudication, scan, output)
            self.assertEqual((output / "validation.md").read_bytes(), before)

    def test_render_rejects_rows_omitted_from_declared_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"] = []

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "entry order mismatch"
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_resolved_mechanical_pass_cannot_be_reapplied_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag\n",
                    (
                        "python <log>/scripts/shared.py --flag\n"
                        "python scripts/no_execute.py --output data/output.csv\n"
                    ),
                ),
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                'e001,Results,table,"name,value",data/output.csv,\n',
            )
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output', required=True)\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text('result', encoding='utf-8')\n",
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output_identity = identity_ending(scan, "data/output.csv")
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            row = next(
                row
                for entry_row in prepared["entries"]
                for row in entry_row["targets"]
                if row["target"] == output_identity
            )
            self.assertEqual(row["provenance"], "2026-08-07")
            self.assertFalse(
                any(
                    item.get("entry") == "e001"
                    and item.get("identity") == output_identity
                    for item in prepared["review_queue"]
                )
            )
            stale_decision = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": output_identity},
                        "decision": "fail",
                        "findings": {"Provenance": "Stale value mismatch."},
                    }
                ],
            }
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "matches no unresolved queue items",
            ):
                DECISIONS.apply_review_decisions(scan, prepared, stale_decision)

            adjudication = adjudication_for(scan, entry)
            output_row = next(
                row
                for row in adjudication["entries"][0]["targets"]
                if row["target"] == output_identity
            )
            output_row["provenance"] = "FAIL"
            output_row["findings"] = [
                {
                    "check": "Provenance",
                    "finding": "Stale value mismatch.",
                }
            ]
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "overrides a mechanically resolved outcome",
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_semantic_failure_after_mechanical_pass_requires_component_basis(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--input <input_csv> --direct-input data/direct.csv "
                    "--working-parent data/workspace "
                    "--output data/command-only.csv",
                    "--output data/output.csv",
                ),
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                'e001,Results,table,"name,value",data/output.csv,\n',
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output_identity = identity_ending(scan, "data/output.csv")
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            item = next(
                item
                for item in prepared["review_queue"]
                if item.get("kind") == "semantic_fallback"
                and item.get("identity") == output_identity
            )
            self.assertTrue(item["evidence"])
            self.assertTrue(
                all(
                    evidence_item["result"]["status"] == "pass"
                    for evidence_item in item["evidence"]
                )
            )
            self.assertEqual(DECISIONS.semantic_failure_bases(item), {"workflow"})

            def fail_action(**extra: str) -> dict:
                return {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {
                                "entry": "e001",
                                "identity": output_identity,
                            },
                            "decision": "fail",
                            "findings": {
                                "Provenance": "The producer direction is unresolved."
                            },
                            **extra,
                        }
                    ],
                }

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "requires an unresolved failure_basis",
            ):
                DECISIONS.apply_review_decisions(scan, prepared, fail_action())
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "requires an unresolved failure_basis",
            ):
                DECISIONS.apply_review_decisions(
                    scan, prepared, fail_action(failure_basis="evidence")
                )

            decided, _ = DECISIONS.apply_review_decisions(
                scan, prepared, fail_action(failure_basis="workflow")
            )
            decided_row = next(
                row
                for entry_row in decided["entries"]
                for row in entry_row["targets"]
                if row["target"] == output_identity
            )
            self.assertEqual(decided_row["provenance"], "FAIL")
            self.assertEqual(decided_row["_failure_basis"], "workflow")

            stale_adjudication = adjudication_for(scan, entry)
            stale_row = next(
                row
                for row in stale_adjudication["entries"][0]["targets"]
                if row["target"] == output_identity
            )
            stale_row["provenance"] = "FAIL"
            stale_row["findings"] = [
                {
                    "check": "Provenance",
                    "finding": "Stale evidence-value mismatch.",
                }
            ]
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "unsupported semantic basis",
            ):
                RUNTIME.render_records(stale_adjudication, scan, root / "records")

    def test_render_rejects_unnecessary_summary_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            duplicate = json.loads(json.dumps(adjudication["summary"][0]))
            duplicate["item"] = "The same supported source item was split again."
            adjudication["summary"].append(duplicate)

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "unnecessarily split"
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_render_round_trip_and_collection_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=2)
            adjudication = adjudication_for(scan, entry)
            output = root / "records"

            counts = RUNTIME.render_records(adjudication, scan, output)
            lint = RUNTIME.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )

            self.assertTrue(lint["ok"], lint["issues"])
            self.assertEqual(counts["summary_rows"], 1)
            self.assertEqual(counts["entry_rows"], 3)
            self.assertEqual(counts["failure_rows"], 1)
            self.assertEqual(counts["successful_checks"], 5)
            self.assertEqual(counts["completed_checks"], 7)
            report = (output / "validation.md").read_text(encoding="utf-8")
            self.assertEqual(report.count("#### "), 1)
            self.assertEqual(report.count("- Check:"), 2)
            self.assertFalse((output / "validation-failures.md").exists())
            self.assertTrue((output / "validation-decisions.json").is_file())

            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["result"]["scope"], adjudication["scope"])
            collection = identity_ending(scan, "data/collection")
            self.assertEqual(state["files"][collection]["members"], ["a.txt"])
            self.assertEqual(len(state["files"]), 4)
            self.assertTrue(
                all(
                    re.fullmatch(r"[0-9a-f]{64}", check["dependency_signature"])
                    for check in state["completed_checks"]
                )
            )
            self.assertTrue(
                all(
                    "members" not in dependency
                    and isinstance(dependency.get("identity"), dict)
                    for check in state["completed_checks"]
                    for dependency in check["dependencies"]
                )
            )
            provenance_check = next(
                check
                for check in state["completed_checks"]
                if check["check"] == "Provenance" and check["result"] != "FAIL"
            )
            provenance_check["resolution"] = {
                "producer_invocation": "e001:L10:1:producer",
                "producer_bindings": [
                    {
                        "material": "data/generated.csv",
                        "invocation": "e001:L5:1:upstream",
                    }
                ],
            }
            decoded = STATE.decode_validation_state(
                state, schema_version=RUNTIME.STATE_SCHEMA_VERSION
            )
            self.assertEqual(
                decoded["completed_checks"][
                    state["completed_checks"].index(provenance_check)
                ]["resolution"]["producer_bindings"],
                provenance_check["resolution"]["producer_bindings"],
            )

    def test_prior_state_reuses_only_unchanged_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            _, unchanged_metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertEqual(unchanged_metrics["reusable_checks"], 7)
            self.assertEqual(unchanged_metrics["rerun_checks"], 0)
            self.assertEqual(unchanged_metrics["incremental_status"], "unchanged")
            self.assertEqual(unchanged_metrics["cached_result"]["failure_rows"], 1)

            reused_scan, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "complete from cached state"
            ):
                prepare_adjudication(reused_scan, "2026-08-08", RUNTIME.RULES_VERSION)

            write(entry.parent / "data" / "output.csv", "name,value\nresult,2.0\n")
            changed_scan, changed_metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(changed_metrics["reusable_checks"], 5)
            self.assertEqual(changed_metrics["rerun_checks"], 2)
            changed = identity_ending(changed_scan, "data/output.csv")
            self.assertEqual(
                changed_scan["incremental"]["files"][changed]["status"], "changed"
            )

    def test_upstream_producer_binding_round_trips_and_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            upstream_entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-07-e001-upstream-binding"
                / "e001.md"
            )
            entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-08-e002-upstream-consumer"
                / "e002.md"
            )
            write(
                summary,
                "# Mini Log\n\n"
                "## Entries\n\n"
                "- [e001](mini/entries/2026-08-07-e001-upstream-binding/e001.md)\n"
                "- [e002](mini/entries/2026-08-08-e002-upstream-consumer/e002.md)\n",
            )
            write(
                upstream_entry,
                "# 2026-08-07: Upstream Binding\n\n"
                "## Results\n\n"
                "`Steps:`\n\n"
                "```bash\n"
                "python scripts/upstream.py --input ../../data/girmos.csv "
                "--output ../../data/generated.csv\n"
                "python scripts/upstream.py --input ../../data/tiptop.csv "
                "--output ../../data/generated.csv\n"
                "```\n\n"
                "`Results:`\n\n"
                "The generated input is retained for the consuming entry.\n",
            )
            write(
                entry,
                "# 2026-08-08: Upstream Consumer\n\n"
                "## Results\n\n"
                "`Steps:`\n\n"
                "```bash\n"
                "python scripts/consumer.py --input ../../data/generated.csv "
                "--output data/output.csv\n"
                "```\n\n"
                "`Results:`\n\n"
                "The retained result is `1.0` in [output](data/output.csv).\n",
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e002,Results,statistic,1.0,data/output.csv :: value,\n",
            )
            script = (
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n"
            )
            write(upstream_entry.parent / "scripts" / "upstream.py", script)
            write(entry.parent / "scripts" / "consumer.py", script)
            shared_data = root / "docs" / "mini" / "data"
            write(shared_data / "girmos.csv", "name,value\ngirmos,1\n")
            write(shared_data / "tiptop.csv", "name,value\ntiptop,1\n")
            write(shared_data / "generated.csv", "name,value\nresult,1\n")
            write(entry.parent / "data" / "output.csv", "name,value\nresult,1.0\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output_identity = identity_ending(scan, "data/output.csv")
            decided, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {"entry": "e002", "identity": output_identity},
                            "decision": "pass",
                        }
                    ],
                },
            )
            upstream = next(
                item
                for item in decided["review_queue"]
                if item["kind"] == "upstream_producer"
            )
            chosen = next(
                candidate
                for candidate in upstream["producer_candidates"]
                if "girmos.csv" in candidate["command"]
            )
            bound, _ = DECISIONS.apply_review_decisions(
                scan,
                decided,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {
                                "kind": "upstream_producer",
                                "entry": "e002",
                                "identity": output_identity,
                            },
                            "decision": "bind",
                            "producer_bindings": [
                                {
                                    "material": chosen["material"],
                                    "invocation": chosen["invocation"],
                                }
                            ],
                        }
                    ],
                },
            )
            orphans = [
                item
                for item in bound["review_queue"]
                if item["kind"] == "orphan_candidates"
            ]
            final, counts = DECISIONS.apply_review_decisions(
                scan,
                bound,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {
                                "kind": "orphan_candidates",
                                "entry": orphan["entry"],
                            },
                            "decision": "orphan",
                            "unresolved": [
                                candidate["identity"]
                                for candidate in orphan["candidates"]
                            ],
                            "connected": [],
                            "retained": [],
                        }
                        for orphan in orphans
                    ],
                },
            )
            self.assertEqual(counts["remaining"], 0, final["review_queue"])

            output_dir = summary.with_suffix("")
            RUNTIME.render_records(final, scan, output_dir)
            state = json.loads(
                (output_dir / "validation-state.json").read_text(encoding="utf-8")
            )
            provenance = next(
                check
                for check in state["completed_checks"]
                if check["check"] == "Provenance"
            )
            self.assertEqual(
                provenance["resolution"]["producer_bindings"],
                [
                    {
                        "material": chosen["material"],
                        "invocation": chosen["invocation"],
                    }
                ],
            )

            _unchanged, metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertEqual(metrics["rerun_checks"], 0)
            self.assertEqual(
                metrics["reusable_checks"], len(state["completed_checks"])
            )
            self.assertFalse(metrics["semantic_review_required"])

            write(
                root / "docs" / "mini" / "data" / "girmos-v2.csv",
                "name,value\ngirmos-v2,1\n",
            )
            write(
                upstream_entry,
                upstream_entry.read_text(encoding="utf-8").replace(
                    "--input ../../data/girmos.csv --output ../../data/generated.csv",
                    "--input ../../data/girmos-v2.csv "
                    "--output ../../data/generated.csv",
                ),
            )
            _changed, changed_metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertGreater(changed_metrics["rerun_checks"], 0)
            self.assertTrue(changed_metrics["semantic_review_required"])

    def test_restored_mtime_does_not_hide_changed_material_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            artifact = entry.parent / "data" / "output.csv"
            before = artifact.stat()
            artifact.write_text("name,value\nresult,9.0\n", encoding="utf-8")
            os.utime(
                artifact,
                ns=(before.st_atime_ns, before.st_mtime_ns),
            )
            after = artifact.stat()
            self.assertEqual(after.st_size, before.st_size)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)

            changed_scan, metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )

            changed = identity_ending(changed_scan, "data/output.csv")
            self.assertEqual(
                changed_scan["incremental"]["files"][changed]["status"], "changed"
            )
            self.assertEqual(metrics["rerun_checks"], 2)
            self.assertNotEqual(
                changed_scan["files"][changed]["sha256"],
                state["files"][changed]["sha256"],
            )

    def test_identical_rewrite_rehashes_without_invalidating_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            artifact = entry.parent / "data" / "output.csv"
            content = artifact.read_bytes()
            before = artifact.stat()
            artifact.write_bytes(content)
            os.utime(artifact, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertNotEqual(artifact.stat().st_ctime_ns, before.st_ctime_ns)

            unchanged_scan, metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )

            identity = identity_ending(unchanged_scan, "data/output.csv")
            self.assertEqual(
                unchanged_scan["incremental"]["files"][identity]["status"],
                "unchanged",
            )
            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertEqual(metrics["rerun_checks"], 0)
            self.assertEqual(metrics["reusable_checks"], 7)

    def test_orphan_disposition_rechecks_when_candidate_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "scripts" / "unused.py", "value = 1\n")
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            disposition = state["orphan_dispositions"][0]
            self.assertTrue(
                all(
                    re.fullmatch(r"[0-9a-f]{64}", item["fingerprint"])
                    for item in disposition["items"]
                )
            )

            script = entry.parent / "scripts" / "unused.py"
            write(script, script.read_text(encoding="utf-8") + "value = 2\n")
            changed_scan, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            prepared = prepare_adjudication(
                changed_scan, "2026-08-08", RUNTIME.RULES_VERSION
            )

            orphan = next(
                item
                for item in prepared["review_queue"]
                if item["kind"] == "orphan_candidates" and item["entry"] == "e001"
            )
            self.assertTrue(
                any(
                    item["identity"].endswith("scripts/unused.py")
                    for item in orphan["candidates"]
                )
            )

    def test_unchanged_clean_result_returns_without_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"].pop()
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in output.iterdir()
                if path.is_file()
            }

            unchanged, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in output.iterdir()
                if path.is_file()
            }

            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertFalse(metrics["semantic_review_required"])
            self.assertEqual(metrics["cached_result"]["failure_rows"], 0)
            self.assertEqual(metrics["reusable_checks"], 5)
            self.assertEqual(metrics["rerun_checks"], 0)
            self.assertEqual(after, before)
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "complete from cached state"
            ):
                prepare_adjudication(unchanged, "2026-08-08", RUNTIME.RULES_VERSION)

    def test_damaged_report_requires_render_but_not_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            (output / "validation.md").write_text("damaged\n", encoding="utf-8")

            changed, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["incremental_status"], "loaded")
            self.assertFalse(metrics["semantic_review_required"])
            prepared = prepare_adjudication(
                changed, "2026-08-08", RUNTIME.RULES_VERSION
            )
            self.assertEqual(prepared["review_queue"], [])

    def test_partial_change_reuses_unaffected_failures_and_orphan_decisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(entry.parent / "data" / "output.csv", "name,value\nresult,2.0\n")

            changed, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            prepared = prepare_adjudication(
                changed, "2026-08-08", RUNTIME.RULES_VERSION
            )

            self.assertGreater(metrics["reusable_checks"], 0)
            self.assertGreater(metrics["rerun_checks"], 0)
            self.assertFalse(
                any(
                    item["kind"] == "orphan_candidates"
                    for item in prepared["review_queue"]
                )
            )
            failed_targets = {
                row["target"]
                for row in prepared["entries"][0]["targets"]
                if "FAIL" in {row["integrity"], row["provenance"]}
            }
            self.assertTrue(
                any(target.endswith("invalid.png") for target in failed_targets)
            )
            self.assertTrue(
                any(target.endswith("missing.csv") for target in failed_targets)
            )

    def test_reproduction_request_never_takes_standard_fast_return(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            _, metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state, mode="reproduction"
            )

            self.assertNotEqual(metrics["incremental_status"], "unchanged")
            self.assertTrue(metrics["semantic_review_required"])

    def test_changed_summary_and_entry_invalidate_dependent_outcomes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "External context uses", "Updated context uses"
                ),
            )

            _, entry_metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertGreater(entry_metrics["rerun_checks"], 0)

            write(
                summary,
                summary.read_text(encoding="utf-8").replace("`1.0`", "`2.0`"),
            )
            write(
                root / "docs" / "mini" / "evidence.csv",
                "statistic,entry,section,transformation\n2.0,e001,Results,\n",
            )
            changed, summary_metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state
            )
            prepared = prepare_adjudication(
                changed, "2026-08-08", RUNTIME.RULES_VERSION
            )
            self.assertGreater(summary_metrics["rerun_checks"], 0)
            self.assertTrue(
                any(
                    item["kind"] == "semantic_provenance"
                    for item in prepared["review_queue"]
                )
            )

    def test_changed_presented_item_survives_no_global_snapshot_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The retained value is `1.0` in",
                    "The retained value is `1.00` in",
                ),
            )

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            entry_identity = identity_ending(changed, "/e001.md")
            state["files"][entry_identity] = changed["files"][entry_identity]
            state["input_files"][entry_identity] = changed["files"][entry_identity]
            state["input_fingerprint"] = changed["input_fingerprint"]

            rescanned, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            output_identity = identity_ending(rescanned, "data/output.csv")
            provenance = next(
                check
                for check in rescanned["incremental"]["checks"]
                if check["entry"] == "e001"
                and check["target"] == output_identity
                and check["check"] == "Provenance"
            )

            self.assertEqual(provenance["status"], "rerun")
            self.assertIn(entry_identity, provenance["changed_dependencies"])
            self.assertGreater(metrics["rerun_checks"], 0)

    def test_changed_evidence_association_uses_per_outcome_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            evidence_path = entry.parent / "evidence.csv"
            write(
                evidence_path,
                evidence_path.read_text(encoding="utf-8").replace(
                    "e001,Results,statistic,1.0,data/output.csv :: value,",
                    (
                        "e001,Results,statistic,1.0,data/output.csv :: value,"
                        "round to one decimal"
                    ),
                ),
            )

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            association = changed["entries"][0]["evidence_record"]["identity"]
            state["files"][association] = changed["files"][association]
            state["input_files"][association] = changed["files"][association]
            state["input_fingerprint"] = changed["input_fingerprint"]

            rescanned, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            output_identity = identity_ending(rescanned, "data/output.csv")
            checks = {
                check["check"]: check
                for check in rescanned["incremental"]["checks"]
                if check["entry"] == "e001" and check["target"] == output_identity
            }

            self.assertEqual(checks["Integrity"]["status"], "reusable")
            self.assertEqual(checks["Provenance"]["status"], "rerun")
            self.assertIn(association, checks["Provenance"]["changed_dependencies"])

    def test_new_producer_command_changes_cached_dependency_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag\n",
                    (
                        "python <log>/scripts/shared.py --flag\n"
                        "python scripts/new_producer.py --output data/output.csv\n"
                    ),
                ),
            )
            write(entry.parent / "scripts" / "new_producer.py", "value = 1\n")

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            entry_identity = identity_ending(changed, "/e001.md")
            current_entry_identity = changed["files"][entry_identity]
            state["files"][entry_identity] = current_entry_identity
            state["input_files"][entry_identity] = current_entry_identity
            state["input_fingerprint"] = changed["input_fingerprint"]
            for check in state["completed_checks"]:
                for dependency in check["dependencies"]:
                    if dependency["path"] == entry_identity:
                        dependency["identity"] = current_entry_identity

            rescanned, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            output_identity = identity_ending(rescanned, "data/output.csv")
            provenance = next(
                check
                for check in rescanned["incremental"]["checks"]
                if check["entry"] == "e001"
                and check["target"] == output_identity
                and check["check"] == "Provenance"
            )

            self.assertEqual(provenance["status"], "rerun")
            self.assertIn("dependency-contract", provenance["changed_dependencies"])

    def test_orphan_inventory_addition_is_reviewed_and_removal_restores_reuse(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            unused = entry.parent / "scripts" / "new_unused.py"
            write(unused, "value = 1\n")

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            prepared = prepare_adjudication(
                changed, "2026-08-08", RUNTIME.RULES_VERSION
            )
            self.assertEqual(
                {item["kind"] for item in prepared["review_queue"]},
                {"orphan_candidates"},
            )
            self.assertTrue(
                any(
                    item["kind"] == "orphan_candidates"
                    for item in prepared["review_queue"]
                )
            )
            reviewed = [
                candidate["identity"]
                for item in prepared["review_queue"]
                if item["kind"] == "orphan_candidates"
                for candidate in item["candidates"]
            ]
            self.assertEqual(reviewed, [INVENTORY.display_path(unused, root)])

            unused.unlink()
            _, restored = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertEqual(restored["incremental_status"], "unchanged")

    def test_ai_use_changes_do_not_invalidate_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "\n## AI Use\n\nInitial disclosure.\n",
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                summary,
                summary.read_text(encoding="utf-8").replace(
                    "Initial disclosure.", "Revised disclosure."
                ),
            )

            _, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertFalse(metrics["semantic_review_required"])

    def test_unrelated_repository_edge_preserves_unchanged_fast_return(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            source = GRAPH.NodeKey(
                "docs/unrelated-consumer",
                GRAPH.NodeKind.INVOCATION,
                "e001:command:1",
            )
            target = GRAPH.NodeKey(
                "docs/unrelated-owner",
                GRAPH.NodeKind.ARTIFACT,
                "docs/unrelated-owner/data/value.csv",
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="unrelated-repository-change",
                inputs=(GRAPH.OriginInput("fixture", "abc123"),),
                rules_version=RUNTIME.RULES_VERSION,
            )
            builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            builder.add_node(source, origin)
            builder.add_node(target, origin)
            builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                source,
                target,
                "docs/unrelated-consumer",
                origin,
            )
            summary_identity = INVENTORY.display_path(summary, root)
            changed_view = GRAPH_STORE.repository_view(
                RUNTIME.RULES_VERSION,
                scan["repository_material_owners"],
                [edge.as_dict() for edge in builder.build().edges],
                scope=GRAPH_STORE.RepositoryViewScope(
                    kind="replacement",
                    expected_summaries=[summary_identity],
                    refresh_summary=summary_identity,
                ),
            )

            _changed, metrics = CANONICAL_SCAN_LOG(
                summary,
                jobs=1,
                prior_state=state,
                repository_index=changed_view,
            )

            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertEqual(metrics["rerun_checks"], 0)
            self.assertFalse(metrics["semantic_review_required"])

    def test_applicable_incoming_edge_invalidates_unchanged_fast_return(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            output_identity = identity_ending(scan, "data/output.csv")
            source = GRAPH.NodeKey(
                "docs/consumer",
                GRAPH.NodeKind.INVOCATION,
                "e001:command:1",
            )
            target = GRAPH.NodeKey(
                "docs/mini",
                GRAPH.NodeKind.ARTIFACT,
                output_identity,
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="incoming-repository-change",
                inputs=(GRAPH.OriginInput("fixture", "abc123"),),
                rules_version=RUNTIME.RULES_VERSION,
            )
            builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            builder.add_node(source, origin)
            builder.add_node(target, origin)
            builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                source,
                target,
                "docs/consumer",
                origin,
            )
            summary_identity = INVENTORY.display_path(summary, root)
            changed_view = GRAPH_STORE.repository_view(
                RUNTIME.RULES_VERSION,
                scan["repository_material_owners"],
                [edge.as_dict() for edge in builder.build().edges],
                scope=GRAPH_STORE.RepositoryViewScope(
                    kind="replacement",
                    expected_summaries=[summary_identity],
                    refresh_summary=summary_identity,
                ),
            )

            _changed, metrics = CANONICAL_SCAN_LOG(
                summary,
                jobs=1,
                prior_state=state,
                repository_index=changed_view,
            )

            self.assertEqual(metrics["incremental_status"], "loaded")
            self.assertTrue(metrics["semantic_review_required"])

    def test_collection_reuse_tracks_only_selected_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            write(
                entry.parent / "data" / "collection" / "b.txt",
                "changed but unselected\n",
            )
            _, unselected_metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertEqual(unselected_metrics["reusable_checks"], 7)
            self.assertEqual(unselected_metrics["rerun_checks"], 0)

            write(
                entry.parent / "data" / "collection" / "new.txt",
                "new member\n",
            )
            _, added_metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertGreater(added_metrics["rerun_checks"], 0)
            (entry.parent / "data" / "collection" / "new.txt").unlink()

            write(
                entry.parent / "data" / "collection" / "a.txt", "changed and selected\n"
            )
            _, selected_metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)
            self.assertEqual(selected_metrics["reusable_checks"], 5)
            self.assertEqual(selected_metrics["rerun_checks"], 2)

    def test_changed_evidence_association_invalidates_provenance_not_integrity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            association = scan["entries"][0]["evidence_record"]["identity"]
            output_row = adjudication["entries"][0]["targets"][0]
            output_row["dependencies"].append(
                {"path": association, "role": "evidence-association"}
            )
            output = root / "records"
            RUNTIME.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            evidence_path = entry.parent / "evidence.csv"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            _, metrics = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["reusable_checks"], 6)
            self.assertEqual(metrics["rerun_checks"], 1)

    def test_prior_state_is_not_reused_across_rule_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            changed_scan, metrics = RUNTIME.scan_log(
                summary, jobs=1, prior_state=state, rules_version="new-rules"
            )

            self.assertEqual(changed_scan["incremental"]["status"], "rules-changed")
            self.assertEqual(metrics["reusable_checks"], 0)
            self.assertEqual(metrics["rerun_checks"], 7)

    def test_malformed_completed_check_invalidates_incremental_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            state["completed_checks"] = [None]

            changed, metrics = RUNTIME.scan_log(
                summary,
                jobs=1,
                prior_state=state,
            )

            self.assertEqual(changed["incremental"]["status"], "invalid")
            self.assertIn("completed check 0", changed["incremental"]["detail"])
            self.assertEqual(metrics["incremental_status"], "invalid")
            self.assertTrue(metrics["semantic_review_required"])

    def test_malformed_dependency_invalidates_incremental_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            state["completed_checks"][0]["dependencies"][0]["identity"] = None

            changed, _ = RUNTIME.scan_log(
                summary,
                jobs=1,
                prior_state=state,
            )

            self.assertEqual(changed["incremental"]["status"], "invalid")
            self.assertIn("dependency 0 identity", changed["incremental"]["detail"])

    def test_nonstring_dependency_members_invalidate_incremental_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            state["completed_checks"][0]["dependencies"][0]["identity"]["members"] = [1]

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(changed["incremental"]["status"], "invalid")
            self.assertIn(
                "members must be a list of strings", changed["incremental"]["detail"]
            )

    def test_nonstring_orphan_decision_invalidates_incremental_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            state["orphan_dispositions"][0]["items"][0]["decision"] = []

            changed, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(changed["incremental"]["status"], "invalid")
            self.assertIn(
                "decision must be a string",
                changed["incremental"]["detail"],
            )

    def test_renderer_rejects_dependency_changed_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            write(entry.parent / "data" / "output.csv", "name,value\nresult,3.0\n")
            output = root / "records"

            with self.assertRaisesRegex(
                CONTRACTS.FileChangedError, "changed after scan"
            ):
                RUNTIME.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_scan_rejects_summary_changed_during_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            original = DISCOVERY.parse_markdown

            def parse_then_change(path: Path) -> dict[str, object]:
                parsed = original(path)
                if path.resolve() == summary.resolve():
                    write(path, path.read_text(encoding="utf-8") + "\nchanged\n")
                return parsed

            with mock.patch.object(
                RUNTIME, "parse_markdown", side_effect=parse_then_change
            ):
                with self.assertRaisesRegex(
                    CONTRACTS.FileChangedError,
                    "file changed during validation read",
                ):
                    RUNTIME.scan_log(summary, jobs=1)

    def test_scan_rejects_entry_changed_during_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            original = DISCOVERY.parse_markdown

            def parse_then_change(path: Path) -> dict[str, object]:
                parsed = original(path)
                if path.resolve() == entry.resolve():
                    write(
                        path,
                        path.read_text(encoding="utf-8")
                        + "\nResults:\n\nThe changed value is `9.0`.\n",
                    )
                return parsed

            with mock.patch.object(
                RUNTIME, "parse_markdown", side_effect=parse_then_change
            ):
                with self.assertRaisesRegex(
                    CONTRACTS.FileChangedError,
                    "file changed during validation read",
                ):
                    RUNTIME.scan_log(summary, jobs=1)

    def test_scan_rejects_evidence_record_changed_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            original = DISCOVERY.entry_evidence_record

            def read_then_change(path: Path) -> dict[str, object]:
                record = original(path)
                write(path, path.read_text(encoding="utf-8") + "\n")
                return record

            with mock.patch.object(
                RUNTIME,
                "entry_evidence_record",
                side_effect=read_then_change,
            ):
                with self.assertRaisesRegex(
                    CONTRACTS.FileChangedError,
                    "file changed during validation read",
                ):
                    RUNTIME.scan_log(summary, jobs=1)

    def test_scan_rejects_file_changed_during_structure_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            artifact = entry.parent / "data" / "output.csv"
            original = EVIDENCE.inspect_structure

            def inspect_then_change(path: Path) -> dict[str, object]:
                result = original(path)
                if path.resolve() == artifact.resolve():
                    write(path, "name,value\nresult,9.0\n")
                return result

            with mock.patch.object(
                RUNTIME,
                "inspect_structure",
                side_effect=inspect_then_change,
            ):
                with self.assertRaisesRegex(
                    CONTRACTS.FileChangedError,
                    "file changed during structure inspection",
                ):
                    RUNTIME.scan_log(summary, jobs=1)

    def test_renderer_requires_checked_integrity_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0]["integrity"] = "-"

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "must be null, a validation date"
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_renderer_verifies_summary_support_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["summary"][0]["support_evidence"][0]["text"] = "invented text"

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "does not match its entry lines"
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_renderer_requires_one_summary_entry_and_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["summary"][0]["entries"] = ["e001", "e001"]

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "exactly one entry and section"
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")

    def test_renderer_rejects_unresolved_results_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = prepare_adjudication(
                scan, "2026-08-07", RUNTIME.RULES_VERSION
            )
            output = root / "records"

            self.assertTrue(adjudication["review_queue"])
            self.assertTrue(
                any(
                    dependency["role"] == "evidence-association"
                    for dependency in adjudication["summary"][0]["dependencies"]
                )
            )

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "unresolved review-queue"
            ):
                RUNTIME.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_renderer_requires_explicit_collection_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            collection = identity_ending(scan, "data/collection")
            collection_row = next(
                row
                for row in adjudication["entries"][0]["targets"]
                if row["target"] == collection
            )
            del collection_row["dependencies"][1]["members"]
            output = root / "records"

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "requires explicit members"
            ):
                RUNTIME.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_linter_detects_state_and_markdown_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)

            report = output / "validation.md"
            report.write_text(
                report.read_text(encoding="utf-8") + "| - |\n", encoding="utf-8"
            )
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["files"].pop(next(iter(state["files"])))
            state_path.write_text(json.dumps(state), encoding="utf-8")

            lint = RUNTIME.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )
            self.assertFalse(lint["ok"])
            self.assertFalse(lint["cache_usable"])
            self.assertIn(
                "validation.md contains a plain hyphen table cell", lint["issues"]
            )
            self.assertIn(
                "state file identities do not exactly match completed-check "
                "dependencies",
                lint["issues"],
            )

    def test_linter_reports_malformed_completed_check_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["completed_checks"] = [None]
            state_path.write_text(json.dumps(state), encoding="utf-8")

            lint = RUNTIME.lint_records(output)

            self.assertTrue(lint["ok"])
            self.assertFalse(lint["cache_usable"])
            self.assertTrue(
                any("completed check 0" in issue for issue in lint["issues"])
            )

    def test_linter_rejects_success_with_missing_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            successful = next(
                check
                for check in state["completed_checks"]
                if check["result"] != "FAIL" and check["dependencies"]
            )
            successful["dependencies"][0]["identity"] = {"missing": True}
            state_path.write_text(json.dumps(state), encoding="utf-8")

            lint = RUNTIME.lint_records(output)

            self.assertTrue(lint["ok"])
            self.assertFalse(lint["cache_usable"])
            self.assertIn(
                "successful state result has an unavailable dependency",
                lint["issues"],
            )

    def test_linter_reports_invalid_utf8_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            (output / "validation.md").write_bytes(b"\xff")

            lint = RUNTIME.lint_records(output)

            self.assertFalse(lint["ok"])
            self.assertTrue(
                any("not readable UTF-8" in issue for issue in lint["issues"])
            )

    def test_linter_distinguishes_a_historical_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            RUNTIME.render_records(adjudication_for(scan, entry), scan, output)
            (output / "validation-state.json").unlink()
            (output / "validation-index.json").unlink()
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "The retained value is `1.0`", "The retained value is `2.0`"
                ),
            )
            changed, _ = RUNTIME.scan_log(summary, jobs=1)

            historical = RUNTIME.lint_records(
                output,
                expected_entry_order=changed["entry_order"],
                expected_local_snapshot_identity=SCAN.local_snapshot_identity(
                    changed
                ),
            )

            self.assertFalse(historical["ok"])
            self.assertFalse(historical["cache_usable"])
            self.assertIn(
                "validation.md is historical for the current local research snapshot",
                historical["currentness_issues"],
            )

    def test_render_rejects_invalid_bundle_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = root / "records"

            with mock.patch.object(
                RENDER,
                "lint_records",
                return_value={"ok": False, "issues": ["generated defect"]},
            ):
                with self.assertRaisesRegex(
                    CONTRACTS.ValidationToolError, "generated defect"
                ):
                    RUNTIME.render_records(adjudication_for(scan, entry), scan, output)

            self.assertFalse(output.exists())

    def test_stale_renderer_cannot_replace_newer_canonical_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            adjudication = adjudication_for(scan, entry)
            RUNTIME.render_records(adjudication, scan, output)
            before = (output / "validation-state.json").read_bytes()
            stale = json.loads(json.dumps(adjudication))
            stale["date"] = "2026-08-08"

            with self.assertRaisesRegex(
                RECORDS.RecordPublicationError,
                "canonical validation bundle changed after scan",
            ):
                RUNTIME.render_records(stale, scan, output)

            self.assertEqual((output / "validation-state.json").read_bytes(), before)

    def test_renderer_allows_other_log_slice_published_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            consumer_summary = root / "docs" / "consumer.md"
            consumer_entry = (
                root
                / "docs"
                / "consumer"
                / "entries"
                / "2026-08-08-e001-consumer"
                / "e001.md"
            )
            write(
                consumer_summary,
                "# Consumer\n\n## Entries\n\n"
                "- [e001](consumer/entries/2026-08-08-e001-consumer/e001.md)\n",
            )
            write(consumer_entry, "# Consumer Entry\n\n## Results\n")
            consumer_slice = (
                consumer_summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME
            )
            empty_builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            write(
                consumer_slice,
                json.dumps(
                    GRAPH_STORE.slice_record(
                        empty_builder.build(), "docs/consumer.md", {}
                    )
                ),
            )
            mini_slice = summary.with_suffix("") / GRAPH_STORE.SLICE_FILENAME
            write(
                mini_slice,
                json.dumps(
                    GRAPH_STORE.slice_record(empty_builder.build(), "docs/mini.md", {})
                ),
            )
            repository_index = GRAPH_STORE.replacement_repository_view(
                root,
                summary,
                RUNTIME.RULES_VERSION,
                RUNTIME.MATERIAL_INVENTORY_POLICY,
            )
            scan, _ = RUNTIME.scan_log(
                summary,
                jobs=1,
                repository_index=repository_index,
            )

            source_identity = consumer_summary.relative_to(root).as_posix()
            invocation = GRAPH.NodeKey(
                "docs/consumer", GRAPH.NodeKind.INVOCATION, "e001:command:1"
            )
            artifact = GRAPH.NodeKey(
                "docs/mini",
                GRAPH.NodeKind.ARTIFACT,
                "docs/mini/entries/2026-08-07-e001-validation-fixture/data/output.csv",
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="test-concurrent-slice-publication",
                inputs=(GRAPH.OriginInput(source_identity, "fixture"),),
                rules_version=RUNTIME.RULES_VERSION,
            )
            changed_builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            changed_builder.add_node(invocation, origin)
            changed_builder.add_node(artifact, origin)
            changed_builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                invocation,
                artifact,
                "docs/consumer",
                origin,
            )
            write(
                consumer_slice,
                json.dumps(
                    GRAPH_STORE.slice_record(
                        changed_builder.build(),
                        "docs/consumer.md",
                        {source_identity: INVENTORY.file_identity(consumer_summary)},
                    )
                ),
            )

            RUNTIME.render_records(
                adjudication_for(scan, entry),
                scan,
                summary.with_suffix(""),
            )
            self.assertTrue(RUNTIME.lint_records(summary.with_suffix(""))["ok"])

    def test_render_rejects_new_inventory_member_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            write(
                entry.parent / "data" / "collection" / "unexpected.txt",
                "changed after scan\n",
            )

            with self.assertRaisesRegex(
                CONTRACTS.FileChangedError,
                "validation directory changed after scan",
            ):
                RUNTIME.render_records(
                    adjudication_for(scan, entry), scan, summary.with_suffix("")
                )

    def test_render_rejects_new_entry_root_member_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            write(entry.parent / "unexpected-direct.csv", "value\n1\n")

            with self.assertRaisesRegex(
                CONTRACTS.FileChangedError,
                "validation directory changed after scan",
            ):
                RUNTIME.render_records(
                    adjudication_for(scan, entry), scan, summary.with_suffix("")
                )

    def test_render_rejects_new_log_root_member_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            write(summary.with_suffix("") / "unexpected-root.csv", "value\n1\n")

            with self.assertRaisesRegex(
                CONTRACTS.FileChangedError,
                "validation directory changed after scan",
            ):
                RUNTIME.render_records(
                    adjudication_for(scan, entry), scan, summary.with_suffix("")
                )

    def test_render_rejects_new_entry_folder_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            write(
                summary.with_suffix("")
                / "entries"
                / "2026-08-12-e002-unreviewed"
                / "e002.md",
                "# Unreviewed entry\n",
            )

            with self.assertRaisesRegex(
                CONTRACTS.FileChangedError,
                "validation directory changed after scan",
            ):
                RUNTIME.render_records(
                    adjudication_for(scan, entry), scan, summary.with_suffix("")
                )

    def test_render_rejects_noncurrent_rules_even_when_packet_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            scan["validation_rules_version"] = "research-log-validation-v16"
            adjudication["validation_rules_version"] = scan["validation_rules_version"]

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "current validation-rules version",
            ):
                RUNTIME.render_records(adjudication, scan, root / "records")









if __name__ == "__main__":
    unittest.main()
