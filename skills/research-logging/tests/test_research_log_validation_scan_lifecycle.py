from research_log_validation_test_support import (
    ADJUDICATION,
    CANONICAL_SCAN_LOG,
    CLI,
    CONTRACTS,
    DECISIONS,
    DISCOVERY,
    EVIDENCE,
    GRAPH,
    GRAPH_ADAPTER,
    GRAPH_QUERIES,
    GRAPH_STORE,
    INVENTORY,
    RUNTIME,
    SCAN,
    SCRIPT,
    Path,
    adjudication_for,
    identity_ending,
    importlib,
    json,
    make_log,
    mock,
    prepare_adjudication,
    subprocess,
    sys,
    tempfile,
    unittest,
    write,
)


class ScanTests(unittest.TestCase):
    def test_scan_rejects_nonpositive_worker_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))

            for jobs in (0, -1, False):
                with self.subTest(jobs=jobs):
                    with self.assertRaisesRegex(
                        CONTRACTS.ValidationToolError,
                        "jobs must be a positive integer",
                    ):
                        RUNTIME.scan_log(summary, jobs=jobs)

    def test_scan_requires_explicit_repository_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "requires an explicit canonical repository view",
            ):
                CANONICAL_SCAN_LOG(summary, jobs=1)

    def test_scan_rejects_owned_material_added_after_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            original = SCAN.owned_inventory

            def inventory_then_add(*args, **kwargs):
                inventory = original(*args, **kwargs)
                write(entry.parent / "late-arrival.csv", "value\n1\n")
                return inventory

            with mock.patch.object(
                SCAN, "owned_inventory", side_effect=inventory_then_add
            ), self.assertRaisesRegex(
                CONTRACTS.FileChangedError,
                "owned directory changed after inventory",
            ):
                RUNTIME.scan_log(summary, jobs=1)

    def test_scan_rejects_incomplete_canonical_repository_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _ = make_log(Path(directory))
            incomplete = {
                "schema_version": "canonical-graph-aggregate",
                "validation_rules_version": RUNTIME.RULES_VERSION,
            }

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "incorrect fields",
            ):
                CANONICAL_SCAN_LOG(
                    summary,
                    jobs=1,
                    repository_index=incomplete,
                )

    def test_rules_version_is_shared_package_owned(self) -> None:
        self.assertEqual(RUNTIME.RULES_VERSION, "research-log-validation-v43")

    def test_scan_extracts_mechanics_without_executing_research_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)

            scan, metrics = RUNTIME.scan_log(summary, jobs=2)

            self.assertFalse((root / "EXECUTED").exists())
            self.assertEqual(scan["entry_order"], ["e001", "Log level"])
            self.assertEqual(scan["reconciliation"]["missing_entries"], [])
            self.assertEqual(scan["reconciliation"]["unlisted_entries"], [])
            self.assertEqual(metrics["entries"], 1)
            self.assertEqual(metrics["tables"], 1)
            self.assertGreaterEqual(metrics["fenced_blocks"], 2)
            self.assertEqual(metrics["numeric_evidence"], 1)
            self.assertEqual(metrics["evidence_rows"], 4)
            self.assertEqual(metrics["evidence_errors"], 0)

            scanned_entry = scan["entries"][0]
            self.assertTrue(
                scanned_entry["commands"][0]["script"].endswith("scripts/no_execute.py")
            )
            self.assertEqual(
                scanned_entry["numeric_evidence"][0]["text"],
                "The retained value is `1.0` in [output](data/output.csv).",
            )
            self.assertEqual(
                scanned_entry["evidence_record"]["rows"][1]["kind"], "table"
            )
            self.assertEqual(
                scan["evidence_records"]["summary"]["rows"][0]["entry"], "e001"
            )
            self.assertEqual(scanned_entry["data_index"]["duplicates"], ["input_csv"])
            self.assertEqual(scanned_entry["unresolved_citations"], ["missing-source"])
            self.assertEqual(
                scanned_entry["commands"][0]["data_tokens"][0]["status"], "ambiguous"
            )
            self.assertTrue(
                scanned_entry["commands"][1]["script"].endswith(
                    "docs/mini/scripts/shared.py"
                )
            )
            self.assertIn(
                identity_ending(scan, "data/direct.csv"), scan["resolved_paths"]
            )
            workspace_argument = next(
                argument
                for argument in scanned_entry["commands"][0]["path_arguments"]
                if argument["option"] == "--working-parent"
            )
            self.assertEqual(workspace_argument["role_hint"], "unknown")
            self.assertTrue(
                any(
                    identity.endswith("data/workspace")
                    for identity in scan["resolved_paths"]
                )
            )
            command_output = next(
                target
                for target in scanned_entry["candidate_targets"]
                if target["identity"].endswith("data/command-only.csv")
            )
            self.assertEqual(command_output["role_hints"], ["unknown"])
            output = identity_ending(scan, "data/output.csv")
            invalid_png = identity_ending(scan, "data/invalid.png")
            collection = identity_ending(scan, "data/collection")
            missing = next(
                target
                for target in scanned_entry["candidate_targets"]
                if target["identity"].endswith("data/missing.csv")
            )
            self.assertEqual(scan["mechanical_checks"][output]["status"], "ok")
            self.assertEqual(scan["mechanical_checks"][invalid_png]["status"], "fail")
            self.assertEqual(
                scan["mechanical_checks"][collection]["identity"],
                "deferred-until-adjudication",
            )
            self.assertNotIn(collection, scan["files"])
            self.assertEqual(missing["mechanical"]["status"], "missing")
            self.assertIn(entry.resolve().as_posix(), scan["resolved_paths"].values())

    def test_scan_treats_the_system_temp_root_as_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            system_temp_root = Path("/tmp").resolve()
            content = entry.read_text(encoding="utf-8").replace(
                "--working-parent data/workspace ",
                f"--working-parent {system_temp_root.as_posix()} ",
            )
            write(entry, content)

            scan, _ = RUNTIME.scan_log(summary, jobs=2)

            command = scan["entries"][0]["commands"][0]
            workspace_argument = next(
                argument
                for argument in command["path_arguments"]
                if argument["option"] == "--working-parent"
            )
            self.assertEqual(workspace_argument["role_hint"], "workspace")
            self.assertNotIn(system_temp_root.as_posix(), scan["resolved_paths"])

    def test_orphan_artifacts_are_limited_to_the_log_and_its_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            direct = root / "external" / "direct.csv"
            linked = root / "external" / "linked" / "orphan.csv"
            write(direct, "value\n1\n")
            write(linked, "value\n2\n")
            (entry.parent / "linked").symlink_to(
                linked.parent, target_is_directory=True
            )
            content = entry.read_text(encoding="utf-8").replace(
                "--output data/command-only.csv",
                "--output data/command-only.csv "
                "--external-direct <project>/external/direct.csv "
                "--linked-output linked/orphan.csv",
            )
            write(entry, content)

            scan, _ = RUNTIME.scan_log(summary, jobs=2)

            orphan_identities = {
                candidate["identity"]
                for candidate in scan["entries"][0]["orphan_candidates"]
            }
            self.assertIn(
                "docs/mini/entries/2026-08-07-e001-validation-fixture/linked/orphan.csv",
                orphan_identities,
            )
            self.assertNotIn("external/direct.csv", orphan_identities)

    def test_orphan_inventory_starts_from_all_log_owned_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            retained = entry.parent / "data" / "never-mentioned.csv"
            write(retained, "value\n1\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            identity = INVENTORY.display_path(retained, root)

            self.assertIn(
                {"kind": "artifact", "identity": identity},
                entry_scan["orphan_inventory"],
            )
            self.assertIn(
                {"kind": "artifact", "identity": identity},
                entry_scan["orphan_candidates"],
            )

    def test_orphan_inventory_includes_entry_folder_without_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            retained = (
                summary.with_suffix("") / "entries" / "abandoned" / "data.csv.out"
            )
            write(retained, "value\n1\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            identity = INVENTORY.display_path(retained, root)
            scope = next(
                item
                for item in scan["entries"]
                if item["id"].startswith("Entry global —")
                and item["path"].endswith("/abandoned")
            )

            self.assertIn(
                {"kind": "artifact", "identity": identity},
                scope["orphan_inventory"],
            )
            self.assertIn(
                {"kind": "artifact", "identity": identity},
                scope["orphan_candidates"],
            )

    def test_orphan_inventory_includes_unused_data_index_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "data.csv",
                "name,type,location\n"
                "input_csv,CSV,data/output.csv\n"
                "unused_external,CSV,/external/reference.csv\n",
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")

            self.assertIn(
                {"kind": "reference", "identity": "<unused_external>"},
                entry_scan["orphan_inventory"],
            )
            self.assertIn(
                {"kind": "reference", "identity": "<unused_external>"},
                entry_scan["orphan_candidates"],
            )

    def test_initial_orphan_candidates_are_projected_from_the_typed_graph(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            graph = GRAPH_ADAPTER.build_dependency_graph(scan)
            namespace = summary.relative_to(root).with_suffix("").as_posix()
            graph_orphans = set()
            for key in GRAPH_QUERIES.orphan_nodes(graph, namespace):
                node = graph.node(key)
                entry_id = node.attribute("entry")
                if entry_id is None:
                    owners = {
                        edge.target.identity
                        for edge in graph.outgoing(key, {GRAPH.EdgeKind.OWNED_BY})
                        if edge.target.kind is GRAPH.NodeKind.ENTRY
                    }
                    self.assertEqual(len(owners), 1)
                    entry_id = next(iter(owners))
                graph_orphans.add(
                    (
                        entry_id,
                        node.attribute("display_identity", key.identity),
                    )
                )

            scan_orphans = {
                (entry["id"], item["identity"])
                for entry in scan["entries"]
                for item in entry["orphan_candidates"]
            }
            inventory = {
                (entry["id"], item["identity"])
                for entry in scan["entries"]
                for item in entry["orphan_inventory"]
            }
            self.assertEqual(scan_orphans, graph_orphans & inventory)

    def test_same_data_index_name_is_classified_per_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            second = (
                summary.with_suffix("")
                / "entries"
                / "2026-08-08-e002-second"
                / "e002.md"
            )
            write(second, "# Second Entry\n\nNo experimental sections.\n")
            write(
                second.parent / "data.csv",
                "name,type,location\nshared,CSV,data/unused.csv\n",
            )
            write(second.parent / "data" / "unused.csv", "value\n2\n")
            summary_text = summary.read_text(encoding="utf-8")
            write(
                summary,
                summary_text
                + "- [e002](mini/entries/2026-08-08-e002-second/e002.md)\n",
            )
            write(
                entry.parent / "scripts" / "produce.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.output).write_text('value\\n1\\n')\n",
            )
            write(
                entry.parent / "data.csv",
                "name,type,location\nshared,CSV,data/direct.csv\n",
            )
            entry_text = entry.read_text(encoding="utf-8").replace(
                "python <log>/scripts/shared.py --flag",
                "python <log>/scripts/shared.py --flag\n"
                "python scripts/produce.py --input <shared> "
                "--output data/output.csv",
            )
            write(entry, entry_text)

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            by_id = {item["id"]: item for item in scan["entries"]}

            self.assertNotIn(
                {"kind": "reference", "identity": "<shared>"},
                by_id["e001"]["orphan_candidates"],
            )
            self.assertIn(
                {"kind": "reference", "identity": "<shared>"},
                by_id["e002"]["orphan_candidates"],
            )

    def test_shared_data_index_resources_are_owned_by_entry_global_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            second = entry.parent / "e002.md"
            write(second, "# Second Entry\n\nNo experimental sections.\n")
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + "- [e002](mini/entries/2026-08-07-e001-validation-fixture/e002.md)\n",
            )
            write(
                entry.parent / "data.csv",
                "name,type,location\n"
                "input_csv,CSV,data/output.csv\n"
                "unused,CSV,data/unused.csv\n",
            )
            write(entry.parent / "data" / "unused.csv", "value\n2\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            by_id = {item["id"]: item for item in scan["entries"]}
            shared_scope = next(
                item
                for item in scan["entries"]
                if item.get("scope_kind") == "entry-global"
            )

            for entry_id in ("e001", "e002"):
                references = {
                    item["identity"]
                    for item in by_id[entry_id]["orphan_inventory"]
                    if item["kind"] == "reference"
                }
                self.assertEqual(references, set())
            self.assertIn(
                {"kind": "reference", "identity": "<input_csv>"},
                shared_scope["orphan_inventory"],
            )
            self.assertNotIn(
                {"kind": "reference", "identity": "<input_csv>"},
                shared_scope["orphan_candidates"],
            )
            self.assertIn(
                {"kind": "reference", "identity": "<unused>"},
                shared_scope["orphan_candidates"],
            )

    def test_output_support_checks_the_complete_presented_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "data" / "output.csv", "not | a table\nwrong\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            row = next(
                item
                for item in entry_scan["evidence_record"]["rows"]
                if item["kind"] == "output"
            )
            result = EVIDENCE.mechanical_evidence_support(
                row, row["resolved_sources"][0]
            )

            self.assertEqual(result["status"], "fail")
            self.assertIn("complete normalized output excerpt", result["detail"])

    def test_orphan_token_is_used_when_its_resolved_path_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "data.csv",
                "name,type,location\nretained_source,CSV,data/output.csv\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--input <input_csv>", "--input <retained_source>"
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            orphans = {
                item["identity"] for item in scan["entries"][0]["orphan_candidates"]
            }

            self.assertNotIn("<retained_source>", orphans)

    def test_orphan_literal_argument_is_used_by_a_used_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "consume_literal.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--artifact-glob')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "def select_artifacts(pattern):\n    return pattern\n"
                "select_artifacts(args.artifact_glob)\n"
                "def make_result(path):\n"
                "    Path(path).write_text('name,value\\nresult,1.0\\n')\n"
                "make_result(args.output)\n",
            )
            write(entry.parent / "data" / "literal.mat", "retained\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/consume_literal.py "
                    "--artifact-glob data/literal.mat --output data/output.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scanned = scan["entries"][0]
            command = next(
                item
                for item in scanned["commands"]
                if (item.get("script") or "").endswith("consume_literal.py")
            )
            literal = next(
                item
                for item in command["path_arguments"]
                if item.get("option") == "--artifact-glob"
            )
            orphans = {item["identity"] for item in scanned["orphan_candidates"]}

            self.assertEqual(literal["role_hint"], "unknown")
            self.assertTrue(any(path.endswith("data/literal.mat") for path in orphans))

            prepared = prepare_adjudication(scan, "2026-08-10", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            DECISIONS.reconcile_semantic_dependencies(scan, prepared)
            DECISIONS.reconcile_graph_orphans(scan, prepared)
            orphan_items = next(
                item
                for entry_result in prepared["entries"]
                for item in entry_result.get("orphan_items", [])
                if item["identity"].endswith("data/literal.mat")
            )
            self.assertEqual(orphan_items["decision"], "accepted")

    def test_orphan_directory_container_is_used_when_a_child_is_presented(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "make_maps.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output-dir')\n"
                "args = parser.parse_args()\n"
                "Path(args.output_dir).mkdir(parents=True, exist_ok=True)\n",
            )
            write(entry.parent / "images" / "maps" / "map.png", "retained\n")
            write(
                entry,
                entry.read_text(encoding="utf-8")
                .replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/make_maps.py --output-dir images/maps",
                )
                .replace(
                    "![invalid plot](data/invalid.png)",
                    "![invalid plot](data/invalid.png)\n\n"
                    "![retained map](images/maps/map.png)",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            orphans = {
                item["identity"] for item in scan["entries"][0]["orphan_candidates"]
            }

            self.assertFalse(any(path.endswith("images/maps") for path in orphans))

    def test_orphan_sibling_output_is_used_with_its_used_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "produce_siblings.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output-data')\n"
                "parser.add_argument('--output-image')\n"
                "args = parser.parse_args()\n"
                "def make_figure(path):\n    return path\n"
                "def make_result(path):\n"
                "    Path(path).write_text('name,value\\nresult,1.0\\n')\n"
                "make_result(args.output_data)\n"
                "make_figure(args.output_image)\n",
            )
            write(entry.parent / "images" / "sibling.png", "retained\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/produce_siblings.py --output-data data/output.csv "
                    "--output-image images/sibling.png",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scanned = scan["entries"][0]
            command = next(
                item
                for item in scanned["commands"]
                if (item.get("script") or "").endswith("produce_siblings.py")
            )
            sibling = next(
                item
                for item in command["path_arguments"]
                if item.get("option") == "--output-image"
            )
            orphans = {item["identity"] for item in scanned["orphan_candidates"]}

            self.assertEqual(sibling["role_hint"], "unknown")
            self.assertTrue(
                any(path.endswith("images/sibling.png") for path in orphans)
            )

            prepared = prepare_adjudication(scan, "2026-08-10", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            DECISIONS.reconcile_semantic_dependencies(scan, prepared)
            DECISIONS.reconcile_graph_orphans(scan, prepared)
            orphan_items = next(
                item
                for entry_result in prepared["entries"]
                for item in entry_result.get("orphan_items", [])
                if item["identity"].endswith("images/sibling.png")
            )
            self.assertEqual(orphan_items["decision"], "accepted")

    def test_argparse_positional_paths_use_source_behavior_not_parameter_names(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "positional_output.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('first')\n"
                "parser.add_argument('second')\n"
                "args = parser.parse_args()\n"
                "Path(args.first).read_text()\n"
                "Path(args.second).mkdir(exist_ok=True)\n",
            )
            write(entry.parent / "input", "input\n")
            write(entry.parent / "images" / "plot.png", "not inspected here\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python <log>/scripts/shared.py --flag\n"
                    "python scripts/positional_output.py input images",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("positional_output.py")
            )
            arguments = {item["option"]: item for item in command["path_arguments"]}

            self.assertEqual(arguments["first"]["role_hint"], "input")
            self.assertEqual(arguments["second"]["role_hint"], "output")
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            packet, _ = ADJUDICATION.make_review_packet(
                scan,
                prepared,
                ADJUDICATION.ReviewPacketRequest(kind="orphan_candidates"),
            )
            self.assertIn("Path(args.second).mkdir", packet)

    def test_local_helper_parameter_flow_distinguishes_input_from_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "derive_config.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--base-config')\n"
                "parser.add_argument('--output-config')\n"
                "args = parser.parse_args()\n"
                "def write_candidate(base_path, output_path):\n"
                "    text = Path(base_path).read_text()\n"
                "    Path(output_path).write_text(text)\n"
                "write_candidate(args.base_config, args.output_config)\n",
            )
            write(entry.parent / "data" / "base.ini", "[base]\n")
            write(entry.parent / "data" / "derived.ini", "[base]\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/derive_config.py --base-config data/base.ini "
                    "--output-config data/derived.ini",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("derive_config.py")
            )
            roles = {
                argument["option"]: argument["role_hint"]
                for argument in command["path_arguments"]
            }

            self.assertEqual(roles["--base-config"], "input")
            self.assertEqual(roles["--output-config"], "output")

    def test_argparse_path_role_follows_a_direct_local_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "aliased_output.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output-csv')\n"
                "args = parser.parse_args()\n"
                "output_path = args.output_csv or 'default.csv'\n"
                "Path(output_path).write_text('name,value\\nresult,1.0\\n')\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/aliased_output.py "
                    "--output-csv <output_dir>/output.csv",
                ),
            )
            write(entry.parent / "data" / "generated" / "output.csv", "value\n1\n")
            with (entry.parent / "data.csv").open("a", encoding="utf-8") as stream:
                stream.write("output_dir,Directory,data/generated\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("aliased_output.py")
            )
            output = next(
                item
                for item in command["path_arguments"]
                if item.get("option") == "--output-csv"
            )

            self.assertEqual(output["role_hint"], "output")

    def test_argparse_path_role_distinguishes_bound_and_builtin_open_modes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "open_paths.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--bound-output')\n"
                "parser.add_argument('--builtin-output')\n"
                "parser.add_argument('--input')\n"
                "args = parser.parse_args()\n"
                "with Path(args.bound_output).open('w', encoding='utf-8') as stream:\n"
                "    stream.write('bound\\n')\n"
                "with open(args.builtin_output, 'w', encoding='utf-8') as stream:\n"
                "    stream.write('builtin\\n')\n"
                "with Path(args.input).open(encoding='utf-8') as stream:\n"
                "    stream.read()\n",
            )
            write(entry.parent / "data" / "input.txt", "input\n")
            write(entry.parent / "data" / "bound.txt", "bound\n")
            write(entry.parent / "data" / "builtin.txt", "builtin\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/open_paths.py "
                    "--bound-output data/bound.txt "
                    "--builtin-output data/builtin.txt "
                    "--input data/input.txt",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("open_paths.py")
            )
            roles = {
                item["option"]: item["role_hint"]
                for item in command["path_arguments"]
            }

            self.assertEqual(roles["--bound-output"], "output")
            self.assertEqual(roles["--builtin-output"], "output")
            self.assertEqual(roles["--input"], "input")

    def test_argparse_path_role_does_not_alias_function_inputs_to_its_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "summarize.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('source')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "def summarize(path):\n    return Path(path).read_text()\n"
                "summary = summarize(args.source)\n"
                "Path(args.output).write_text(summary)\n",
            )
            write(entry.parent / "data" / "source.csv", "value\n1\n")
            write(entry.parent / "data" / "summary.csv", "value\n1\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/summarize.py data/source.csv "
                    "--output data/summary.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("summarize.py")
            )
            arguments = {item["option"]: item for item in command["path_arguments"]}

            self.assertEqual(arguments["source"]["role_hint"], "input")
            self.assertEqual(arguments["--output"]["role_hint"], "output")

    def test_semantic_reconciliation_scopes_exact_collection_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            collection = entry.parent / "data" / "source"
            write(collection / "used.csv", "value\n1\n")
            write(collection / "unused.csv", "value\n2\n")
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-11", RUNTIME.RULES_VERSION)
            row = next(
                target
                for item in prepared["entries"]
                for target in item["targets"]
                if target["target"].endswith("data/output.csv")
            )
            collection_identity = INVENTORY.display_path(collection, root)
            used_identity = INVENTORY.display_path(collection / "used.csv", root)
            scan["resolved_paths"][collection_identity] = collection.as_posix()
            scan["resolved_paths"][used_identity] = (collection / "used.csv").as_posix()
            scan["mechanical_checks"][collection_identity] = {
                "status": "deferred",
                "type": "directory",
            }
            row["dependencies"].extend(
                [
                    {"path": collection_identity, "role": "input"},
                    {"path": used_identity, "role": "input"},
                ]
            )
            row["provenance"] = "2026-08-11"
            prepared["review_queue"].append(
                {
                    "entry": "e001",
                    "identity": row["target"],
                    "kind": "collection_scope",
                    "collections": [collection_identity],
                }
            )

            DECISIONS.reconcile_semantic_dependencies(scan, prepared)

            dependency = next(
                item
                for item in row["dependencies"]
                if item["path"] == collection_identity
            )
            self.assertEqual(dependency["members"], ["used.csv"])
            self.assertFalse(
                any(
                    item.get("kind") == "collection_scope"
                    and item.get("identity") == row["target"]
                    for item in prepared["review_queue"]
                )
            )

    def test_orphan_inventory_is_recursive_and_follows_local_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "from helper import VALUE\n"
                "from shared_helper import SHARED\n"
                "VALUE = VALUE + SHARED\n",
            )
            write(entry.parent / "scripts" / "helper.py", "VALUE = 1\n")
            write(
                root / "docs" / "mini" / "scripts" / "shared_helper.py",
                "SHARED = 1\n",
            )
            write(entry.parent / "scripts" / "nested" / "unused.py", "VALUE = 2\n")
            write(entry.parent / "scripts" / "nested" / "unused.m", "value = 3;\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/command-only.csv | tee data/output.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            self.assertFalse(any(path.endswith("helper.py") for path in orphans))
            self.assertFalse(any(path.endswith("shared_helper.py") for path in orphans))
            self.assertTrue(any(path.endswith("nested/unused.py") for path in orphans))
            self.assertTrue(any(path.endswith("nested/unused.m") for path in orphans))

    def test_orphan_inventory_follows_static_sys_path_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import sys\n"
                "from pathlib import Path\n"
                "SHARED = Path(__file__).resolve().parent / 'shared'\n"
                "sys.path.insert(0, str(SHARED))\n"
                "from helper import VALUE\n",
            )
            write(entry.parent / "scripts" / "shared" / "helper.py", "VALUE = 1\n")
            write(root / "docs" / "mini" / "scripts" / "helper.py", "VALUE = 2\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            entry_orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }
            log_scan = next(
                item for item in scan["entries"] if item["id"] == "Log level"
            )
            log_orphans = {item["identity"] for item in log_scan["orphan_candidates"]}

            self.assertFalse(
                any(path.endswith("scripts/shared/helper.py") for path in entry_orphans)
            )
            self.assertTrue(
                any(path.endswith("scripts/helper.py") for path in log_orphans)
            )

    def test_orphan_inventory_honors_consecutive_sys_path_insert_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import sys\n"
                "from pathlib import Path\n"
                "LOCAL = Path(__file__).resolve().parent / 'local'\n"
                "SHARED = Path(__file__).resolve().parent / 'shared'\n"
                "sys.path.insert(0, str(LOCAL))\n"
                "sys.path.insert(0, str(SHARED))\n"
                "from helper import VALUE\n",
            )
            local_helper = entry.parent / "scripts" / "local" / "helper.py"
            shared_helper = entry.parent / "scripts" / "shared" / "helper.py"
            write(local_helper, "VALUE = 'local'\n")
            write(shared_helper, "VALUE = 'shared'\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            self.assertTrue(any(path.endswith("local/helper.py") for path in orphans))
            self.assertFalse(any(path.endswith("shared/helper.py") for path in orphans))

            write(
                entry.parent / "scripts" / "no_execute.py",
                "import sys\n"
                "from pathlib import Path\n"
                "LOCAL = Path(__file__).resolve().parent / 'local'\n"
                "SHARED = Path(__file__).resolve().parent / 'shared'\n"
                "sys.path.insert(0, str(SHARED))\n"
                "sys.path.insert(0, str(LOCAL))\n"
                "from helper import VALUE\n",
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            self.assertFalse(any(path.endswith("local/helper.py") for path in orphans))
            self.assertTrue(any(path.endswith("shared/helper.py") for path in orphans))

    def test_orphan_inventory_honors_looped_sys_path_insert_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import sys\n"
                "from pathlib import Path\n"
                "SCRIPT_DIR = Path(__file__).resolve().parent\n"
                "SHARED = SCRIPT_DIR / 'shared'\n"
                "for path in (SCRIPT_DIR, SHARED):\n"
                "    if str(path) not in sys.path:\n"
                "        sys.path.insert(0, str(path))\n"
                "from helper import VALUE\n",
            )
            local_helper = entry.parent / "scripts" / "helper.py"
            shared_helper = entry.parent / "scripts" / "shared" / "helper.py"
            write(local_helper, "VALUE = 'local'\n")
            write(shared_helper, "VALUE = 'shared'\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            self.assertTrue(any(path.endswith("scripts/helper.py") for path in orphans))
            self.assertFalse(
                any(path.endswith("scripts/shared/helper.py") for path in orphans)
            )

    def test_recorded_command_uses_location_specific_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "scripts" / "batch.py", "VALUE = 'entry'\n")
            write(root / "docs" / "mini" / "scripts" / "batch.py", "VALUE = 'log'\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python <log>/scripts/shared.py --flag\npython scripts/batch.py",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            entry_orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }
            log_scan = next(
                item for item in scan["entries"] if item["id"] == "Log level"
            )
            log_orphans = {item["identity"] for item in log_scan["orphan_candidates"]}

            self.assertFalse(
                any(
                    path.endswith(
                        "entries/2026-08-07-e001-validation-fixture/scripts/batch.py"
                    )
                    for path in entry_orphans
                )
            )
            self.assertTrue(
                any(path.endswith("docs/mini/scripts/batch.py") for path in log_orphans)
            )

    def test_orphan_inventory_follows_file_anchored_script_launches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import subprocess\n"
                "import sys\n"
                "from pathlib import Path\n"
                "worker = Path(__file__).resolve().parent / 'worker.py'\n"
                "shell = Path(__file__).resolve().parent / 'driver.sh'\n"
                "subprocess.run([sys.executable, 'direct_worker.py'], check=True)\n",
            )
            write(
                entry.parent / "scripts" / "worker.py",
                "from worker_helper import VALUE\n",
            )
            write(entry.parent / "scripts" / "worker_helper.py", "VALUE = 1\n")
            write(entry.parent / "scripts" / "driver.sh", "python shell_worker.py\n")
            write(entry.parent / "scripts" / "shell_worker.py", "VALUE = 1\n")
            write(entry.parent / "scripts" / "direct_worker.py", "VALUE = 1\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/command-only.csv | tee data/output.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            for name in (
                "worker.py",
                "worker_helper.py",
                "driver.sh",
                "shell_worker.py",
                "direct_worker.py",
            ):
                self.assertFalse(any(path.endswith(name) for path in orphans))

    def test_orphan_inventory_follows_python_to_matlab_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "def run_matlab_batch(command):\n"
                "    return command\n\n"
                "command = 'matlab_entrypoint(1);'\n"
                "run_matlab_batch(command)\n",
            )
            write(
                entry.parent / "scripts" / "matlab_entrypoint.m",
                "function matlab_entrypoint(value)\nmatlab_helper(value);\nend\n",
            )
            write(
                entry.parent / "scripts" / "matlab_helper.m",
                "function matlab_helper(value)\ndisp(value);\nend\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/command-only.csv | tee data/output.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {item["identity"] for item in entry_scan["orphan_candidates"]}

            self.assertFalse(
                any(path.endswith("matlab_entrypoint.m") for path in orphans)
            )
            self.assertFalse(any(path.endswith("matlab_helper.m") for path in orphans))

    def test_orphan_inventory_follows_python_matlab_addpath(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "no_execute.py",
                "from pathlib import Path\n"
                "SHARED = Path(__file__).resolve().parents[3] / 'scripts'\n"
                "def matlab_string(value):\n"
                "    return repr(str(value))\n"
                "def run_matlab_batch(command):\n"
                "    return command\n"
                "command = (\n"
                "    f'addpath({matlab_string(SHARED)}); '\n"
                "    'matlab_entrypoint(1);'\n"
                ")\n"
                "run_matlab_batch(command)\n",
            )
            write(
                root / "docs" / "mini" / "scripts" / "matlab_entrypoint.m",
                "function matlab_entrypoint(value)\nmatlab_helper(value);\nend\n",
            )
            write(
                root / "docs" / "mini" / "scripts" / "matlab_helper.m",
                "function matlab_helper(value)\ndisp(value);\nend\n",
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            orphans = {
                item["identity"]
                for scanned in scan["entries"]
                for item in scanned["orphan_candidates"]
            }

            self.assertFalse(
                any(path.endswith("matlab_entrypoint.m") for path in orphans)
            )
            self.assertFalse(any(path.endswith("matlab_helper.m") for path in orphans))

    def test_recorded_matlab_wrapper_connects_workspace_container_and_upstream(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            log_scripts = root / "docs" / "mini" / "scripts"
            write(
                log_scripts / "matlab_runner.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--cwd', type=Path)\n"
                "parser.add_argument('--addpath', action='append', type=Path, "
                "default=[])\n"
                "parser.add_argument('--command')\n"
                "args = parser.parse_args()\n"
                "addpath_commands = [f'addpath({path})' for path in args.addpath]\n"
                "def run_matlab_batch(command, *, cwd):\n"
                "    return command, cwd\n"
                "run_matlab_batch('; '.join([*addpath_commands, args.command]), "
                "cwd=args.cwd)\n",
            )
            write(
                log_scripts / "record_generated_input.m",
                "function record_generated_input(inputCsv, outputCsv)\n"
                "inputCsv = resolve_existing_path(inputCsv);\n"
                "outputCsv = resolve_output_path(outputCsv);\n"
                "values = readtable(inputCsv);\n"
                "values = matlab_helper(values);\n"
                "writetable(values, outputCsv);\n"
                "end\n",
            )
            write(
                log_scripts / "matlab_helper.m",
                "function values = matlab_helper(values)\nend\n",
            )
            write(
                entry.parent / "scripts" / "upstream.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n",
            )
            write(
                entry.parent / "scripts" / "summarize.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                    "--input <input_csv> --direct-input data/direct.csv "
                    "--working-parent data/workspace "
                    "--output data/command-only.csv",
                    "python scripts/upstream.py --output data/upstream.csv\n"
                    "python <log>/scripts/matlab_runner.py --cwd . "
                    "--addpath scripts --command "
                    "\"record_generated_input('data/upstream.csv', "
                    "'data/intermediate.csv')\"\n"
                    "python scripts/summarize.py --input data/intermediate.csv "
                    "--output data/output.csv",
                ),
            )
            write(entry.parent / "data" / "upstream.csv", "name,value\nresult,1.0\n")
            write(
                entry.parent / "data" / "intermediate.csv",
                "name,value\nresult,1.0\n",
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scanned = next(item for item in scan["entries"] if item["id"] == "e001")
            matlab_command = next(
                command
                for command in scanned["commands"]
                if command.get("script", "").endswith("matlab_runner.py")
            )
            roles = {
                argument.get("option"): argument["role_hint"]
                for argument in matlab_command["path_arguments"]
                if argument.get("option") in {"--cwd", "--addpath"}
            }
            self.assertEqual(
                roles, {"--cwd": "workspace", "--addpath": "dependency-container"}
            )
            self.assertTrue(
                any(
                    path.endswith("record_generated_input.m")
                    for path in matlab_command["matlab_scripts"]
                )
            )
            self.assertIn(
                ("output", "data/intermediate.csv"),
                {
                    (argument["role_hint"], argument["raw"].strip("'\""))
                    for argument in matlab_command["path_arguments"]
                    if argument.get("source") == "matlab-command"
                },
                matlab_command["path_arguments"],
            )

            orphans = {
                item["identity"]
                for entry_scan in scan["entries"]
                for item in entry_scan["orphan_candidates"]
            }
            for suffix in (
                "scripts/matlab_runner.py",
                "scripts/record_generated_input.m",
                "scripts/matlab_helper.m",
                "scripts/upstream.py",
                "scripts/summarize.py",
                "data/upstream.csv",
                "data/intermediate.csv",
            ):
                self.assertFalse(any(path.endswith(suffix) for path in orphans), suffix)
            self.assertNotIn(INVENTORY.display_path(entry.parent, root), orphans)
            self.assertNotIn(
                INVENTORY.display_path(entry.parent / "scripts", root), orphans
            )

            prepared = prepare_adjudication(scan, "2026-08-10", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            DECISIONS.reconcile_semantic_dependencies(scan, prepared)
            DECISIONS.reconcile_graph_orphans(scan, prepared)
            dependencies = {item["path"] for item in output_row["dependencies"]}
            for suffix in (
                "scripts/matlab_runner.py",
                "scripts/record_generated_input.m",
                "scripts/matlab_helper.m",
                "scripts/upstream.py",
                "data/upstream.csv",
                "data/intermediate.csv",
            ):
                self.assertTrue(
                    any(path.endswith(suffix) for path in dependencies),
                    (suffix, sorted(dependencies)),
                )

    def test_canonical_graph_edge_protects_cross_log_dependency_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner_summary, _ = make_log(root)
            shared = root / "docs" / "mini" / "scripts" / "shared_cross_log.py"
            write(shared, "VALUE = 1\n")
            shared_identity = INVENTORY.display_path(shared, root)
            source = GRAPH.NodeKey(
                "docs/consumer",
                GRAPH.NodeKind.PRESENTED,
                f"repository:script-dependency:consumer.py:{shared_identity}",
            )
            target = GRAPH.NodeKey(
                "docs/mini",
                GRAPH.NodeKind.SCRIPT,
                shared_identity,
            )
            origin = GRAPH.FactOrigin(
                kind=GRAPH.OriginKind.MECHANICAL,
                resolver="test-repository-edge",
                inputs=(GRAPH.OriginInput("fixture", "abc123"),),
                rules_version=RUNTIME.RULES_VERSION,
            )
            builder = GRAPH.GraphBuilder(RUNTIME.RULES_VERSION)
            builder.add_node(source, origin)
            builder.add_node(
                target,
                origin,
                {"orphanable": True},
            )
            builder.add_edge(
                GRAPH.EdgeKind.CROSS_LOG_USE,
                source,
                target,
                "docs/consumer",
                origin,
            )
            projection = GRAPH_STORE.repository_slice_projection(
                [
                    GRAPH_STORE.slice_record(
                        builder.build(),
                        "docs/consumer.md",
                        {"fixture": {"size": 1, "sha256": "a" * 64}},
                    )
                ]
            )
            repository_index = GRAPH_STORE.repository_view(
                RUNTIME.RULES_VERSION,
                {
                    shared_identity: {
                        "namespace": "docs/mini",
                        "kind": "script",
                    }
                },
                projection["graph_edges"],
            )

            scan, _ = RUNTIME.scan_log(
                owner_summary,
                jobs=1,
                repository_index=repository_index,
            )

            self.assertTrue(scan["repository_graph_edges"])
            self.assertFalse(
                any(
                    item["identity"] == shared_identity
                    for entry in scan["entries"]
                    for item in entry["orphan_candidates"]
                )
            )
            graph = GRAPH_ADAPTER.build_dependency_graph(scan)
            self.assertIn(
                target,
                {
                    root.node
                    for root in graph.roots
                    if root.policy is GRAPH.RootPolicy.INCOMING_CROSS_LOG
                },
            )

    def test_used_workflow_connects_sibling_outputs_logs_and_upstream_outputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "scripts" / "produce.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output')\n"
                "parser.add_argument('--sidecar')\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text('name,value\\ninput,2.0\\n')\n"
                "Path(args.sidecar).write_bytes(b'npz')\n",
            )
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import argparse\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output')\n"
                "parser.add_argument('--metadata')\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n"
                "Path(args.metadata).write_text('retained sibling\\n')\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                    "--input <input_csv> --direct-input data/direct.csv "
                    "--working-parent data/workspace "
                    "--output data/command-only.csv",
                    "python scripts/produce.py --output data/upstream.csv "
                    "--sidecar data/upstream.npz 2>&1 | tee data/upstream.log\n"
                    "python scripts/no_execute.py --input <input_csv> "
                    "--output data/output.csv --metadata data/sibling.csv "
                    "2>&1 | tee data/evidence.log",
                ),
            )
            write(
                entry.parent / "data.csv",
                "name,type,location\ninput_csv,CSV,data/upstream.csv\n",
            )
            write(entry.parent / "data" / "upstream.csv", "name,value\ninput,2.0\n")
            write(entry.parent / "data" / "upstream.npz", "npz\n")
            write(entry.parent / "data" / "upstream.log", "complete\n")
            write(entry.parent / "data" / "sibling.csv", "retained sibling\n")
            write(entry.parent / "data" / "evidence.log", "complete\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            orphans = {
                item["identity"]
                for scanned in scan["entries"]
                for item in scanned["orphan_candidates"]
            }
            for suffix in (
                "data/upstream.csv",
                "data/upstream.npz",
                "data/upstream.log",
                "data/sibling.csv",
                "data/evidence.log",
            ):
                self.assertFalse(any(path.endswith(suffix) for path in orphans))
            self.assertNotIn("<input_csv>", orphans)

    def test_semantic_producer_decision_reconciles_upstream_and_script_closure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "data" / "intermediate.csv", "value\n1\n")
            write(
                entry.parent / "scripts" / "upstream.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.output).write_text('value\\n1\\n')\n",
            )
            write(
                entry.parent / "scripts" / "downstream.py",
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                "SCRIPT_DIR = Path(__file__).resolve().parent\n"
                "subprocess.run([sys.executable, str(SCRIPT_DIR / 'plot.py')])\n",
            )
            write(entry.parent / "scripts" / "plot.py", "from helper import VALUE\n")
            write(entry.parent / "scripts" / "helper.py", "VALUE = 1\n")
            write(
                entry.parent / "scripts" / "unrelated.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output-dir')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.output_dir).mkdir(exist_ok=True)\n",
            )
            write(entry.parent / "data" / "unrelated.csv", "value\n2\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python <log>/scripts/shared.py --flag\n"
                    "python scripts/upstream.py --output data/intermediate.csv\n"
                    "python scripts/downstream.py\n"
                    "python scripts/unrelated.py --input data/unrelated.csv "
                    "--output-dir data",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            intermediate = identity_ending(scan, "data/intermediate.csv")
            downstream = identity_ending(scan, "scripts/downstream.py")
            orphan_queue = next(
                item
                for item in prepared["review_queue"]
                if item["kind"] == "orphan_candidates" and item["entry"] == "e001"
            )
            orphan_identities = [
                item["identity"] for item in orphan_queue["candidates"]
            ]
            decisions = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": output},
                        "decision": "pass",
                        "producer": 4,
                        "add_dependencies": [
                            {"path": downstream, "role": "producer"},
                            {"path": intermediate, "role": "input"},
                        ],
                    },
                    {
                        "match": {
                            "entry": "e001",
                            "identity": ADJUDICATION.ORPHAN_TARGET,
                        },
                        "decision": "orphan",
                        "unresolved": orphan_identities,
                        "connected": [],
                        "retained": [],
                    },
                ],
            }

            decided, _ = DECISIONS.apply_review_decisions(scan, prepared, decisions)

            entry_result = next(
                item for item in decided["entries"] if item["id"] == "e001"
            )
            output_row = next(
                row for row in entry_result["targets"] if row["target"] == output
            )
            dependency_paths = {item["path"] for item in output_row["dependencies"]}
            for suffix in (
                "scripts/downstream.py",
                "scripts/plot.py",
                "scripts/helper.py",
                "scripts/upstream.py",
            ):
                self.assertTrue(
                    any(path.endswith(suffix) for path in dependency_paths),
                    sorted(dependency_paths),
                )
            self.assertFalse(
                any(path.endswith("scripts/unrelated.py") for path in dependency_paths),
                sorted(dependency_paths),
            )
            self.assertFalse(
                any(path.endswith("data/unrelated.csv") for path in dependency_paths)
            )

    def test_entry_global_orphans_are_reportable_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            second = entry.parent / "e002.md"
            write(second, "# Second Entry\n\nNo experimental sections.\n")
            write(
                summary,
                summary.read_text(encoding="utf-8")
                + f"\n- [e002]({second.relative_to(summary.parent).as_posix()})\n",
            )
            write(entry.parent / "scripts" / "unused.m", "value = 1;\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scope = next(
                item
                for item in scan["entries"]
                if item.get("scope_kind") == "entry-global"
            )
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)

            self.assertIn(scope["id"], scan["entry_order"])
            queued = next(
                item
                for item in prepared["review_queue"]
                if item.get("entry") == scope["id"]
                and item.get("kind") == "orphan_candidates"
            )
            self.assertTrue(
                any(
                    candidate["identity"].endswith("scripts/unused.m")
                    for candidate in queued["candidates"]
                )
            )

    def test_scan_classifies_sections_and_skips_non_experimental_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-07-e001-section-types"
                / "e001.md"
            )
            relative_entry = "mini/entries/2026-08-07-e001-section-types/e001.md"
            write(
                summary,
                f"# Mini Log\n\n## Entries\n\n- [e001]({relative_entry})\n",
            )
            write(
                entry,
                "# 2026-08-07: Section Types\n\n"
                "## Experiment\n\n"
                "`Steps:`\n\n"
                "```bash\n./pyrun scripts/run.py --output data/result.csv\n```\n\n"
                "`Results:`\n\n"
                "Measured `1.0`.\n\n"
                "name | value\n--- | ---:\nresult | 1.0\n\n"
                "## Synthesis\n\n"
                "`Findings:`\n\n"
                "Historical value `2.0` and [source](data/synthesis.csv).\n\n"
                "name | value\n--- | ---:\nhistorical | 2.0\n\n"
                "## Prose\n\n"
                "Planned contextual value `3.0`.\n\n"
                "## Invalid\n\n"
                "`Findings:`\n\n"
                "Mixed lifecycle.\n\n"
                "`Results:`\n\n"
                "Invalid value `4.0`.\n",
            )
            write(entry.parent / "data" / "result.csv", "name,value\nresult,1.0\n")
            write(
                entry.parent / "data" / "synthesis.csv",
                "name,value\nhistorical,2.0\n",
            )

            scan, metrics = RUNTIME.scan_log(summary, jobs=1)
            scanned = scan["entries"][0]
            types = {
                section["section"]: section["type"] for section in scanned["sections"]
            }

            self.assertEqual(types["Experiment"], "experimental")
            self.assertEqual(types["Synthesis"], "synthesis")
            self.assertEqual(types["Prose"], "prose")
            self.assertEqual(types["Invalid"], "invalid")
            self.assertEqual(len(scanned["section_errors"]), 1)
            self.assertEqual(metrics["section_errors"], 1)
            self.assertEqual(metrics["tables"], 1)
            self.assertEqual(metrics["numeric_evidence"], 1)
            self.assertEqual(scanned["numeric_evidence"][0]["values"], ["1.0"])
            self.assertFalse(
                any(
                    candidate["identity"].endswith("data/synthesis.csv")
                    for candidate in scanned["candidate_targets"]
                )
            )

            adjudication = prepare_adjudication(
                scan, "2026-08-07", RUNTIME.RULES_VERSION
            )
            invalid = next(
                target
                for target in adjudication["entries"][0]["targets"]
                if target["target"].startswith("Invalid section structure")
            )
            self.assertEqual(invalid["sections"], ["Invalid"])
            self.assertEqual(invalid["integrity"], "FAIL")
            self.assertEqual(invalid["provenance"], "FAIL")
            self.assertEqual(invalid["reproducibility"], "N/A")
            self.assertTrue(
                any(
                    item["kind"] == "mechanical_failure"
                    for item in adjudication["review_queue"]
                )
            )

    def test_scan_rejects_evidence_rows_for_synthesis_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            entry = (
                root
                / "docs"
                / "mini"
                / "entries"
                / "2026-08-07-e001-synthesis-evidence"
                / "e001.md"
            )
            relative_entry = "mini/entries/2026-08-07-e001-synthesis-evidence/e001.md"
            write(
                summary,
                f"# Mini Log\n\n## Entries\n\n- [e001]({relative_entry})\n",
            )
            write(
                entry,
                "# 2026-08-07: Synthesis Evidence\n\n"
                "## Synthesis\n\n`Findings:`\n\nHistorical value 2.0.\n",
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e001,Synthesis,statistic,2.0,data/source.csv,\n",
            )

            scan, metrics = RUNTIME.scan_log(summary, jobs=1)

            self.assertEqual(metrics["evidence_errors"], 2)
            self.assertTrue(
                any(
                    "not a unique experimental section" in error
                    for error in scan["entries"][0]["evidence_record"]["errors"]
                )
            )

    def test_scan_reports_structural_evidence_record_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e001,Missing section,output,run complete,"
                "data/a.log | data/b.log,\n",
            )
            write(
                summary.with_suffix("") / "evidence.csv",
                "statistic,entry,section,transformation\n1.0,e999,Results,\n",
            )

            scan, metrics = RUNTIME.scan_log(summary, jobs=1)
            adjudication = prepare_adjudication(
                scan, "2026-08-10", RUNTIME.RULES_VERSION
            )

            entry_errors = scan["entries"][0]["evidence_record"]["errors"]
            summary_errors = scan["evidence_records"]["summary"]["errors"]
            self.assertTrue(
                any(
                    "output requires exactly one source" in error
                    for error in entry_errors
                )
            )
            self.assertTrue(
                any("section 'Missing section'" in error for error in entry_errors)
            )
            self.assertTrue(
                any(
                    "unknown supporting entry 'e999'" in error
                    for error in summary_errors
                )
            )
            self.assertEqual(metrics["evidence_errors"], 7)
            self.assertFalse(
                any(
                    item["kind"] == "evidence_record_error"
                    for item in adjudication["review_queue"]
                )
            )
            self.assertFalse(
                any(
                    row["target"].endswith(("data/a.log", "data/b.log"))
                    for entry_row in adjudication["entries"]
                    for row in entry_row["targets"]
                )
            )

    def test_cli_scan_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            output = root / "scan.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "scan",
                    "--summary",
                    str(summary),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["entry_order"],
                ["e001", "Log level"],
            )
            self.assertFalse((root / ".research-log-validation-index").exists())
            metrics = json.loads(result.stdout)
            self.assertEqual(metrics["repository_index_status"], "replacement")

    def test_cli_scan_rejects_invalid_explicit_repository_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            explicit = root / "invalid-index.json"
            explicit.write_text("{}\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "scan",
                    "--summary",
                    str(summary),
                    "--output",
                    str(root / "scan.json"),
                    "--repository-index",
                    str(explicit),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("repository index is invalid", result.stderr)

    def test_cli_rejects_retired_repository_index_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            explicit = root / "retired-index.json"
            explicit.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "validation_rules_version": RUNTIME.RULES_VERSION,
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "scan.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "scan",
                    "--summary",
                    str(summary),
                    "--output",
                    str(output),
                    "--repository-index",
                    str(explicit),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("repository index is invalid", result.stderr)
            self.assertFalse(output.exists())

    def test_json_writer_does_not_replace_unchanged_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "state.json"
            CLI.write_json(output, {"status": "unchanged", "alpha": 1})
            before = output.stat()
            lines = output.read_text(encoding="utf-8").splitlines()

            CLI.write_json(output, {"status": "unchanged", "alpha": 1})
            after = output.stat()

            self.assertGreater(len(lines), 1)
            self.assertEqual(lines[1], '  "alpha": 1,')
            self.assertEqual(before.st_ino, after.st_ino)
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_malformed_structured_files_fail_mechanical_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = {
                "broken.py": "def incomplete(\n",
                "broken.json": "{not-json}\n",
                "broken.npz": "not-a-zip\n",
                "broken.csv": "a,b\n1\n",
                "broken.ecsv": "value\n1\n",
            }
            for name, content in malformed.items():
                path = root / name
                write(path, content)
                with self.subTest(name=name):
                    self.assertEqual(EVIDENCE.inspect_structure(path)["status"], "fail")

            valid_ecsv = root / "valid.ecsv"
            write(
                valid_ecsv,
                "# %ECSV 1.0\n"
                "# ---\n"
                "# datatype:\n"
                "# - {name: outer_pix, datatype: int64}\n"
                "outer_pix\n"
                "1\n"
                "2\n",
            )
            structure = EVIDENCE.inspect_structure(valid_ecsv)
            self.assertEqual(structure["status"], "ok")
            self.assertEqual(structure["rows"], 2)
            self.assertEqual(structure["columns"], ["outer_pix"])

    def test_signature_valid_truncated_artifacts_cannot_pass_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = {
                "truncated.png": b"\x89PNG\r\n\x1a\n" + b"0" * 16,
                "garbage.jpg": b"\xff\xd8garbage\xff\xd9",
                "header.npy": b"\x93NUMPY",
                "header.h5": b"\x89HDF\r\n\x1a\n",
                "header.fits": b"SIMPLE  =                    T",
            }
            for name, content in malformed.items():
                path = root / name
                path.write_bytes(content)
                structure = EVIDENCE.inspect_structure(path)
                with self.subTest(name=name):
                    self.assertEqual(structure["status"], "fail")
                    result, _detail = ADJUDICATION.target_integrity(
                        {"mechanical_checks": {name: structure}},
                        {"status": "resolved"},
                        name,
                        None,
                        "2026-08-12",
                    )
                    self.assertEqual(result, "FAIL")

    def test_format_readers_accept_decodable_structured_artifacts(self) -> None:
        import h5py
        import numpy as np
        import pyarrow as arrow
        import pyarrow.parquet as parquet
        from astropy.io import fits
        from PIL import Image
        from pypdf import PdfWriter
        from scipy.io import savemat

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (2, 2)).save(root / "image.png")
            Image.new("RGB", (2, 2)).save(root / "image.jpg")
            np.save(root / "values.npy", np.arange(3))
            with h5py.File(root / "values.h5", "w") as artifact:
                artifact.create_dataset("values", data=[1, 2, 3])
            fits.PrimaryHDU(np.arange(3)).writeto(root / "values.fits")
            table = fits.BinTableHDU.from_columns(
                [fits.Column(name="value", format="D", array=np.arange(3.0))]
            )
            fits.HDUList([fits.PrimaryHDU(), table]).writeto(root / "table.fits")
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with (root / "document.pdf").open("wb") as handle:
                writer.write(handle)
            write(root / "figure.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
            write(root / "config.yaml", "value: 1\n")
            write(
                root / "notebook.ipynb",
                json.dumps(
                    {
                        "nbformat": 4,
                        "nbformat_minor": 5,
                        "metadata": {},
                        "cells": [
                            {
                                "cell_type": "markdown",
                                "metadata": {},
                                "source": ["# Valid\n"],
                            }
                        ],
                    }
                ),
            )
            parquet.write_table(
                arrow.table({"value": [1, 2]}), root / "values.parquet"
            )
            savemat(root / "values.mat", {"value": np.arange(3)})

            for name in (
                "image.png",
                "image.jpg",
                "values.npy",
                "values.h5",
                "values.fits",
                "table.fits",
                "document.pdf",
                "figure.svg",
                "config.yaml",
                "notebook.ipynb",
                "values.parquet",
                "values.mat",
            ):
                with self.subTest(name=name):
                    self.assertEqual(
                        EVIDENCE.inspect_structure(root / name)["status"], "ok"
                    )

    def test_npz_and_mat_require_their_semantic_container_formats(self) -> None:
        import zipfile

        import h5py

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "text-only.npz", "w") as archive:
                archive.writestr("readme.txt", "not an array")
            with zipfile.ZipFile(root / "empty.npz", "w"):
                pass
            with h5py.File(root / "not-mat.mat", "w") as artifact:
                artifact.create_dataset("values", data=[1, 2, 3])

            for name in ("text-only.npz", "empty.npz", "not-mat.mat"):
                with self.subTest(name=name):
                    structure = EVIDENCE.inspect_structure(root / name)
                    self.assertEqual(structure["status"], "fail")
                    result, _detail = ADJUDICATION.target_integrity(
                        {"mechanical_checks": {name: structure}},
                        {"status": "resolved"},
                        name,
                        None,
                        "2026-08-12",
                    )
                    self.assertEqual(result, "FAIL")

    def test_known_malformed_formats_cannot_pass_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = {
                "broken.pdf": b"%PDF-broken",
                "broken.svg": b"<svg>",
                "broken.yaml": b"value: [unterminated",
                "broken.parquet": b"PAR1",
                "broken.ipynb": b'{"nbformat": 4, "cells": "wrong"}',
                "broken.mat": b"MATLAB broken",
            }
            for name, content in malformed.items():
                path = root / name
                path.write_bytes(content)
                structure = EVIDENCE.inspect_structure(path)
                with self.subTest(name=name):
                    self.assertEqual(structure["status"], "fail")
                    result, _detail = ADJUDICATION.target_integrity(
                        {"mechanical_checks": {name: structure}},
                        {"status": "resolved"},
                        name,
                        None,
                        "2026-08-12",
                    )
                    self.assertEqual(result, "FAIL")

    def test_pickle_and_unknown_formats_are_never_readability_successes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unsafe.pkl").write_bytes(b"not a pickle")
            (root / "unknown.xyz").write_bytes(b"readable")

            pickle = EVIDENCE.inspect_structure(root / "unsafe.pkl")
            unknown = EVIDENCE.inspect_structure(root / "unknown.xyz")

            self.assertEqual(pickle["status"], "unresolved")
            self.assertIn("prohibited", pickle["detail"])
            self.assertEqual(unknown["status"], "unresolved")
            self.assertIn("no type-appropriate", unknown["detail"])

    def test_presented_items_use_exact_collision_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            write(
                entry,
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Run.\n\n"
                "`Results:`\n\nValues were `0.2` and `0.2`.\n\n"
                "| name | value |\n| --- | ---: |\n| a | 0.2 |\n\n"
                "```text\nfinished successfully\n```\n",
            )

            parsed = DISCOVERY.parse_markdown(entry)

            self.assertEqual(
                [item["selector"] for item in parsed["presented_items"]],
                [
                    "0.2 [occurrence 1]",
                    "0.2 [occurrence 2]",
                    "name,value",
                    "finished successfully",
                ],
            )

    def test_inline_slashes_are_not_presented_artifact_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            write(
                entry,
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Inspect retained data.\n\n"
                "`Results:`\n\nThe range was `+/-35%` for `status/state`.\n\n"
                "| field | value |\n| --- | --- |\n| stats/sr | `n/a` |\n",
            )

            parsed = DISCOVERY.parse_markdown(entry)
            candidates = SCAN.candidate_references(parsed, entry, Path(directory))

            self.assertEqual(candidates, [])
            self.assertEqual(
                [item["selector"] for item in parsed["presented_items"]],
                ["field,value"],
            )

    def test_external_links_are_not_presented_artifact_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "e001.md"
            write(
                entry,
                "# Fixture\n\n## Trial\n\n`Steps:`\n\n- Compare.\n\n"
                "`Results:`\n\n[External reference](https://example.org/result).\n",
            )

            parsed = DISCOVERY.parse_markdown(entry)

            self.assertEqual(
                SCAN.candidate_references(parsed, entry, Path(directory)),
                [],
            )

    def test_prepare_prefills_mechanics_and_bounds_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)

            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            row = next(
                row
                for entry in prepared["entries"]
                for row in entry["targets"]
                if row["target"] == output
            )

            self.assertEqual(row["integrity"], "2026-08-07")
            self.assertTrue(prepared["review_queue"])
            self.assertTrue(
                all(
                    item["kind"]
                    in {
                        "mechanical_failure",
                        "orphan_candidates",
                        "semantic_fallback",
                        "semantic_provenance",
                        "collection_scope",
                    }
                    for item in prepared["review_queue"]
                )
            )

    def test_numeric_equivalence_handles_recorded_display_transformations(self) -> None:
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "68%", [0.676], "Converted to percent and rounded"
            )
        )
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "11 h", [39600], "Converted seconds to hours and rounded"
            )
        )
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "300", [297.216], "Rounded to one significant figure"
            )
        )
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "650", [652.457], "Rounded to two significant figures"
            )
        )
        self.assertFalse(EVIDENCE.numeric_value_equivalent("0.7", [0.618], "Rounded"))
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "6.1 arcmin^2",
                [6.0856985504813625],
                "Rounded to one decimal and added arcmin^2 unit",
            )
        )
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "3.5 GiB",
                [3540.4],
                "Converted MiB to GiB and rounded to one decimal",
            )
        )
        self.assertFalse(
            EVIDENCE.numeric_value_equivalent(
                "3.5 GiB",
                [3648.0],
                "Converted MiB to GiB and rounded to one decimal",
            )
        )
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "67%",
                [67.4931],
                "Rounded to a whole percent and added percent suffix",
            )
        )
        self.assertFalse(
            EVIDENCE.numeric_value_equivalent(
                "67%",
                [67.5676],
                "Rounded to a whole percent and added percent suffix",
            )
        )

    def test_tabular_locator_contract_preserves_commas_and_selects_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.csv"
            write(
                source,
                "case,case_id,value\n"
                '"1 NGS center, R=17.0",8,10.432\n'
                '"1 NGS center, R=17.0",15,9.683\n',
            )

            status, values, _ = EVIDENCE.locator_values(
                source, "case=1 NGS center, R=17.0; case_id=8|15; field=value"
            )

            self.assertEqual(status, "ok")
            self.assertEqual(values, ["10.432", "9.683"])

    def test_where_prefix_disambiguates_reserved_filter_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "comparison.csv"
            write(
                source,
                "field,candidate,absolute_difference\n"
                "validation_error_percent,2x256,-0.038\n"
                "parameter_count,2x256,67520\n",
            )

            status, values, _ = EVIDENCE.locator_values(
                source,
                "where.field=validation_error_percent; "
                "candidate=2x256; field=absolute_difference",
            )

            self.assertEqual(status, "ok")
            self.assertEqual(values, ["-0.038"])

    def test_structured_and_text_locator_contract_extracts_bounded_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            structured = root / "results.json"
            log = root / "run.log"
            table = root / "whole.csv"
            write(
                structured,
                json.dumps(
                    {
                        "simulation": [
                            {"policy": "static", "throughput": 0.81},
                            {"policy": "dynamic", "throughput": 1.12},
                        ]
                    }
                ),
            )
            write(log, "started\ncompleted 49152 outer pixels\n")
            write(table, "name,value\nresult,1.0\n")

            scalar_status, scalar_values, _ = EVIDENCE.locator_values(
                structured, "path=simulation[0].throughput"
            )
            records_status, records_values, _ = EVIDENCE.locator_values(
                structured, "path=simulation; fields=policy|throughput"
            )
            text_status, text_values, _ = EVIDENCE.locator_values(
                log, "text=completed 49152 outer pixels"
            )
            whole_status, _, whole_detail = EVIDENCE.locator_values(table, "")

            self.assertEqual((scalar_status, scalar_values), ("ok", [0.81]))
            self.assertEqual(
                (records_status, records_values),
                ("ok", ["static", 0.81, "dynamic", 1.12]),
            )
            self.assertEqual(
                (text_status, text_values),
                ("ok", ["completed 49152 outer pixels"]),
            )
            self.assertEqual(whole_status, "unresolved")
            self.assertIn("bounded context", whole_detail)

    def test_whole_and_compound_locators_include_complete_small_source_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "whole.csv"
            structured = root / "results.json"
            write(
                table,
                "name,value\n"
                + "".join(f"row-{index},{index}\n" for index in range(20)),
            )
            write(
                structured,
                json.dumps(
                    {"records": [{"name": f"row-{index}"} for index in range(20)]}
                ),
            )

            table_result = EVIDENCE.locator_values(table, "")
            structured_result = EVIDENCE.locator_values(structured, "path=records")

            self.assertEqual(table_result[0], "unresolved")
            self.assertIn("row-19", table_result[2])
            self.assertEqual(structured_result[0], "unresolved")
            self.assertIn("row-19", structured_result[2])

    def test_failed_table_filter_reports_available_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "comparison.csv"
            write(
                source,
                "field,candidate,value\n"
                "validation_error_percent,1x128,0.03\n"
                "validation_error_percent,1x256,0.04\n",
            )

            status, values, detail = EVIDENCE.locator_values(
                source,
                "where.field=validation_error_percent; candidate=1x128+1x256; "
                "field=value",
            )

            self.assertEqual((status, values), ("fail", []))
            self.assertIn("1x128", detail)
            self.assertIn("1x256", detail)

    def test_collection_packet_extracts_bounded_member_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "collection"
            root.mkdir()
            write(root / "build.yaml", "mode: test\n")
            write(root / "build.log", "complete\n")
            for index in range(90):
                write(root / "configs" / f"config-{index:03d}.ini", "[test]\n")
            scan = {
                "resolved_paths": {
                    "collection": str(root),
                    "collection/build.log": str(root / "build.log"),
                }
            }

            lines = ADJUDICATION.collection_packet_lines(scan, "collection")

            self.assertIn("resolved child dependencies", "\n".join(lines))
            self.assertIn("build.log", "\n".join(lines))
            self.assertIn("build.yaml", "\n".join(lines))
            self.assertLess(
                "\n".join(lines).index("build.yaml"),
                "\n".join(lines).index("config-000.ini"),
            )

    def test_structured_root_filters_and_relative_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.json"
            write(
                source,
                json.dumps(
                    [
                        {"level": 6, "median": -0.0004},
                        {"level": 9, "median": -0.0005},
                    ]
                ),
            )
            mapping = Path(directory) / "mapping.json"
            write(
                mapping,
                json.dumps(
                    {
                        "outer_pixels": 288,
                        "comparison": {"elapsed_seconds": 45.5},
                    }
                ),
            )

            filtered = EVIDENCE.locator_values(source, "path=$; level=6; field=median")
            relative = EVIDENCE.locator_values(
                mapping,
                "path=$; fields=outer_pixels|comparison.elapsed_seconds",
            )

            self.assertEqual(filtered[:2], ("ok", [-0.0004]))
            self.assertEqual(relative[:2], ("ok", [288, 45.5]))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy unavailable")
    def test_npz_root_fields_filters_and_direct_indexes(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.npz"
            np.savez(
                source,
                labels=np.array(["base", "windflip"]),
                values=np.array([1.6, 5.6]),
                cases=np.array([6, 6]),
            )

            filtered = EVIDENCE.locator_values(
                source, "path=$; labels=base; field=values"
            )
            fields = EVIDENCE.locator_values(source, "path=$; fields=labels|cases")
            indexed = EVIDENCE.locator_values(source, "path=values[1]")

            self.assertEqual(filtered[:2], ("ok", [1.6]))
            self.assertEqual(fields[:2], ("ok", ["base", "windflip", 6, 6]))
            self.assertEqual(indexed[:2], ("ok", [5.6]))

    @unittest.skipUnless(importlib.util.find_spec("h5py"), "h5py unavailable")
    def test_hdf5_dataset_properties_are_closed_and_extractable(self) -> None:
        import h5py

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "results.h5"
            with h5py.File(source, "w") as artifact:
                artifact.create_dataset("status/state", shape=(30_000,), dtype="u1")
                artifact.create_dataset("stats/sr", shape=(30_000, 109), dtype="f4")

            rows = EVIDENCE.locator_values(
                source, "path=status/state; property=shape[0]"
            )
            shapes = EVIDENCE.locator_values(
                source,
                "path=$; fields=status/state|stats/sr; property=shape",
            )
            invalid = EVIDENCE.locator_values(
                source, "path=status/state; property=dtype"
            )

            self.assertEqual(rows[:2], ("ok", [30_000]))
            self.assertEqual(shapes[:2], ("ok", [30_000, 30_000, 109]))
            self.assertEqual(invalid[0], "unresolved")
            self.assertIn("unsupported structured property", invalid[2])

    def test_pickle_locator_never_deserializes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "artifact.pkl"
            write(source, "not a safe pickle\n")

            status, values, detail = EVIDENCE.locator_values(source, "path=value")

            self.assertEqual((status, values), ("unresolved", []))
            self.assertIn("deserialization is prohibited", detail)

    def test_numeric_equivalence_respects_scientific_notation_precision(self) -> None:
        self.assertTrue(
            EVIDENCE.numeric_value_equivalent(
                "3.604586e+11",
                [360458629208.8278],
                "Formatted in scientific notation and rounded",
            )
        )
        self.assertFalse(
            EVIDENCE.numeric_value_equivalent(
                "3.604586e+11",
                [360458500000.0],
                "Formatted in scientific notation and rounded",
            )
        )

    def test_cli_prepare_reports_mechanical_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan_path = root / "scan.json"
            prepared_path = root / "prepared.json"
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            write(scan_path, json.dumps(scan))

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "prepare",
                    "--scan",
                    str(scan_path),
                    "--output",
                    str(prepared_path),
                    "--date",
                    "2026-08-07",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            metrics = json.loads(result.stdout)
            self.assertGreater(metrics["mechanical_integrity_results"], 0)
            self.assertGreater(metrics["review_queue"], 0)

    def test_scan_loader_rejects_malformed_nested_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scan["entries"] = {"e001": "not a list"}
            path = root / "scan.json"
            write(path, json.dumps(scan))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "scan field 'entries' must be a list of objects",
            ):
                CLI.load_scan_record(path)

    def test_scan_loader_rejects_malformed_entry_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scan["entries"][0] = {}
            path = root / "scan.json"
            write(path, json.dumps(scan))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "scan entry 0 has incorrect fields",
            ):
                CLI.load_scan_record(path)

    def test_prepare_cli_reports_malformed_scan_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scan["entries"][0] = {}
            scan_path = root / "scan.json"
            output_path = root / "prepared.json"
            write(scan_path, json.dumps(scan))

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "prepare",
                    "--scan",
                    str(scan_path),
                    "--output",
                    str(output_path),
                    "--date",
                    "2026-08-07",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("scan entry 0 has incorrect fields", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output_path.exists())

    def test_cli_rejects_retired_template_alias(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "template"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'template'", result.stderr)

    def test_scan_cli_reports_invalid_utf8_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            summary.parent.mkdir()
            summary.write_bytes(b"# Invalid\n\xff\n")
            repository_index = root / "repository-index.json"
            repository_index.write_text(
                json.dumps(GRAPH_STORE.empty_repository_view(RUNTIME.RULES_VERSION)),
                encoding="utf-8",
            )
            output = root / "scan.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "scan",
                    "--summary",
                    str(summary),
                    "--repository-index",
                    str(repository_index),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("file is not valid UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output.exists())

    def test_prepare_cli_rejects_malformed_candidate_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            scan["entries"][0]["candidate_targets"] = [{"presented": True}]
            scan_path = root / "scan.json"
            output_path = root / "prepared.json"
            write(scan_path, json.dumps(scan))

            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "prepare",
                    "--scan",
                    str(scan_path),
                    "--output",
                    str(output_path),
                    "--date",
                    "2026-08-07",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "candidate_targets item 0 has incorrect fields", result.stderr
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(output_path.exists())

    def test_adjudication_loader_rejects_unknown_contract_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["legacy_field"] = True
            path = root / "adjudication.json"
            write(path, json.dumps(adjudication))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "adjudication record has incorrect top-level fields",
            ):
                CLI.load_adjudication_record(path)

    def test_adjudication_loader_rejects_malformed_target_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0].pop("target")
            path = root / "adjudication.json"
            write(path, json.dumps(adjudication))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "adjudication entry 0 target 0 has incorrect fields",
            ):
                CLI.load_adjudication_record(path)

    def test_adjudication_loader_rejects_non_scalar_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0]["integrity"] = []
            path = root / "adjudication.json"
            write(path, json.dumps(adjudication))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "integrity must be null, a validation date",
            ):
                CLI.load_adjudication_record(path)

    def test_adjudication_loader_rejects_unknown_review_item_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = prepare_adjudication(
                scan, "2026-08-07", RUNTIME.RULES_VERSION
            )
            self.assertTrue(adjudication["review_queue"])
            adjudication["review_queue"][0]["legacy_detail"] = "unexpected"
            path = root / "adjudication.json"
            write(path, json.dumps(adjudication))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "adjudication review item 0 has incorrect fields",
            ):
                CLI.load_adjudication_record(path)

    def test_adjudication_loader_rejects_non_string_review_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = prepare_adjudication(
                scan, "2026-08-07", RUNTIME.RULES_VERSION
            )
            self.assertTrue(adjudication["review_queue"])
            adjudication["review_queue"][0]["reason"] = {"not": "text"}
            path = root / "adjudication.json"
            write(path, json.dumps(adjudication))

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "field 'reason' must be a string",
            ):
                CLI.load_adjudication_record(path)

    def test_review_packet_extracts_context_without_deciding_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)

            packet, counts = ADJUDICATION.make_review_packet(scan, prepared)

            self.assertEqual(sum(counts.values()), len(prepared["review_queue"]))
            self.assertIn("# Validation Review Packet", packet)
            self.assertIn("The retained value is `1.0`", packet)
            self.assertIn('values: ["1.0"]', packet)
            self.assertIn("python scripts/no_execute.py", packet)
            self.assertTrue(prepared["review_queue"])

            summary_packet, summary_counts = ADJUDICATION.make_review_packet(
                scan,
                prepared,
                ADJUDICATION.ReviewPacketRequest(entry="Summary"),
            )
            self.assertEqual(summary_counts, {"semantic_provenance": 1})
            self.assertIn("Summary: 1.0", summary_packet)
            self.assertNotIn("Orphaned artifacts", summary_packet)

            target = prepared["review_queue"][0]["identity"]
            target_packet, target_counts = ADJUDICATION.make_review_packet(
                scan,
                prepared,
                ADJUDICATION.ReviewPacketRequest(target=target),
            )
            self.assertEqual(sum(target_counts.values()), 1)
            self.assertIn(target, target_packet)

    def test_summary_candidates_include_source_named_by_transformation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                summary,
                summary.read_text(encoding="utf-8").replace("`1.0`", "`10%+`"),
            )
            write(
                entry,
                entry.read_text(encoding="utf-8")
                + "\nThe retained increase is `11.2%`.\n",
            )
            write(
                root / "docs" / "mini" / "evidence.csv",
                "statistic,entry,section,transformation\n"
                "10%+,e001,Results,Coarsened the supported 11.2% increase\n",
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            summary_review = next(
                item for item in prepared["review_queue"] if item["entry"] == "Summary"
            )

            self.assertEqual(
                [
                    candidate["base_selector"]
                    for candidate in summary_review["candidates"]
                ],
                ["11.2%"],
            )

    def test_compact_decisions_resolve_queue_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            invalid = identity_ending(scan, "data/invalid.png")
            collection = identity_ending(scan, "data/collection")
            missing = next(
                item["identity"]
                for item in prepared["review_queue"]
                if item["identity"].endswith("data/missing.csv")
            )
            orphan_actions = []
            for item in prepared["review_queue"]:
                if item["kind"] != "orphan_candidates":
                    continue
                note_sha = item["validation_notes"][0]["sha256"]
                orphan_actions.append(
                    {
                        "match": {
                            "kind": "orphan_candidates",
                            "entry": item["entry"],
                        },
                        "decision": "orphan",
                        "unresolved": [],
                        "connected": [],
                        "retained": [
                            {
                                "identity": candidate["identity"],
                                "validation_note": note_sha,
                            }
                            for candidate in item["candidates"]
                        ],
                    }
                )
            decisions = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"kind": "semantic_provenance"},
                        "decision": "support",
                        "candidate": 1,
                    },
                    {
                        "match": {"entry": "e001", "identity": output},
                        "decision": "pass",
                        "producer": 2,
                    },
                    {
                        "match": {
                            "targets": [
                                {"entry": "e001", "identity": invalid},
                                {"entry": "e001", "identity": missing},
                            ]
                        },
                        "decision": "fail",
                        "findings": {
                            "Provenance": (
                                "The invalid or missing artifact cannot be traced "
                                "to retained evidence."
                            )
                        },
                    },
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": "pass",
                        "producer": 2,
                        "members": {collection: {"glob": "a.txt"}},
                    },
                    *orphan_actions,
                ],
            }

            adjudication, counts = DECISIONS.apply_review_decisions(
                scan, prepared, decisions
            )

            self.assertEqual(counts["remaining"], 0)
            self.assertEqual(adjudication["review_queue"], [])
            rendered = root / "records"
            RUNTIME.render_records(adjudication, scan, rendered)
            self.assertTrue((rendered / "validation.md").exists())

    def test_orphan_decision_can_record_a_reviewed_semantic_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(
                scan, "2026-08-12", RUNTIME.RULES_VERSION
            )
            orphan = next(
                item
                for item in prepared["review_queue"]
                if item["kind"] == "orphan_candidates"
            )
            connected = [candidate["identity"] for candidate in orphan["candidates"]]
            decisions = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {
                            "kind": "orphan_candidates",
                            "entry": orphan["entry"],
                        },
                        "decision": "orphan",
                        "unresolved": [],
                        "connected": connected,
                        "retained": [],
                    }
                ],
            }

            decided, _ = DECISIONS.apply_review_decisions(
                scan, prepared, decisions
            )

            entry = next(
                item for item in decided["entries"] if item["id"] == orphan["entry"]
            )
            self.assertFalse(
                any(
                    row["target"] == ADJUDICATION.ORPHAN_TARGET
                    for row in entry["targets"]
                )
            )
            bases = {item["basis"] for item in entry["orphan_items"]}
            self.assertLessEqual(bases, {"graph", "semantic-connection"})
            self.assertIn("semantic-connection", bases)
            CONTRACTS.decode_adjudication_record(
                decided,
                schema_version=RUNTIME.ADJUDICATION_SCHEMA_VERSION,
            )
            graph = GRAPH_ADAPTER.build_dependency_graph(scan, decided)
            remaining = {
                GRAPH_QUERIES.display_identity(graph, key)
                for key in GRAPH_QUERIES.orphan_nodes(
                    graph, summary.relative_to(root).with_suffix("").as_posix()
                )
            }
            self.assertTrue(set(connected).isdisjoint(remaining))

    def test_compact_decisions_validate_matches_and_collection_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            collection = identity_ending(scan, "data/collection")
            unknown = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e999"},
                        "decision": "pass",
                    }
                ],
            }
            bad_member = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": "pass",
                        "members": {collection: ["missing.txt"]},
                    }
                ],
            }

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "matches no unresolved"
            ):
                DECISIONS.apply_review_decisions(scan, prepared, unknown)
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "does not exist as a file"
            ):
                DECISIONS.apply_review_decisions(scan, prepared, bad_member)
            ignored_finding = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": "pass",
                        "findings": {"Provenance": "Would otherwise be ignored."},
                    }
                ],
            }
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "keys not used by pass"
            ):
                DECISIONS.apply_review_decisions(scan, prepared, ignored_finding)
            unhashable_decision = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": [],
                    }
                ],
            }
            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError, "invalid decision"
            ):
                DECISIONS.apply_review_decisions(scan, prepared, unhashable_decision)

    def test_semantic_pass_cannot_override_immutable_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "\n`Validation:`",
                    "\nAn unprovenanced presented statistic is `2.0`.\n\n`Validation:`",
                ),
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            immutable = [
                item for item in prepared["review_queue"] if item.get("hard_failures")
            ]

            self.assertTrue(
                any(item["identity"].endswith("data/missing.csv") for item in immutable)
            )
            self.assertTrue(
                any(item["identity"].startswith("Unprovenanced:") for item in immutable)
            )
            for item in immutable:
                decisions = {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {
                                "entry": item["entry"],
                                "identity": item["identity"],
                            },
                            "decision": "pass",
                        }
                    ],
                }
                with self.assertRaisesRegex(
                    CONTRACTS.ValidationToolError,
                    "cannot override deterministic failure",
                ):
                    DECISIONS.apply_review_decisions(scan, prepared, decisions)

    def test_workflow_failures_are_immutable_provenance_failures(self) -> None:
        cases = {
            "ambiguous-token": None,
            "missing-input": "missing",
            "invalid-producer": "invalid",
        }
        for name, mutation in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                summary, entry = make_log(root)
                text = entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/output.csv",
                )
                if mutation == "missing":
                    text = text.replace(
                        "--direct-input data/direct.csv",
                        "--direct-input data/missing-input.csv",
                    )
                    write(
                        entry.parent / "data.csv",
                        "name,type,location\ninput_csv,CSV,data/other.csv\n",
                    )
                    write(entry.parent / "data" / "other.csv", "value\n2\n")
                    write(
                        entry.parent / "scripts" / "no_execute.py",
                        "import argparse\n"
                        "from pathlib import Path\n"
                        "parser = argparse.ArgumentParser()\n"
                        "parser.add_argument('--input')\n"
                        "parser.add_argument('--direct-input')\n"
                        "parser.add_argument('--working-parent')\n"
                        "parser.add_argument('--output')\n"
                        "args = parser.parse_args()\n"
                        "Path(args.input).read_text()\n"
                        "Path(args.direct_input).read_text()\n"
                        "Path(args.output).write_text('value\\n1\\n')\n",
                    )
                elif mutation == "invalid":
                    write(
                        entry.parent / "data.csv",
                        "name,type,location\ninput_csv,CSV,data/other.csv\n",
                    )
                    write(entry.parent / "data" / "other.csv", "value\n2\n")
                    write(entry.parent / "scripts" / "no_execute.py", "if :\n")
                    text = text.replace(
                        "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                        "--input <input_csv> --direct-input data/direct.csv "
                        "--working-parent data/workspace --output data/output.csv",
                        "python scripts/no_execute.py > data/output.csv",
                    )
                write(entry, text)

                scan, _ = RUNTIME.scan_log(summary, jobs=1)
                prepared = prepare_adjudication(
                    scan, "2026-08-07", RUNTIME.RULES_VERSION
                )
                output = identity_ending(scan, "data/output.csv")
                item = next(
                    candidate
                    for candidate in prepared["review_queue"]
                    if candidate["identity"] == output
                )
                self.assertEqual(item["workflow"]["status"], "fail")
                self.assertIn("Provenance", item["hard_failures"])
                with self.assertRaisesRegex(
                    CONTRACTS.ValidationToolError,
                    "cannot override deterministic failure",
                ):
                    DECISIONS.apply_review_decisions(
                        scan,
                        prepared,
                        {
                            "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                            "actions": [
                                {
                                    "match": {
                                        "entry": "e001",
                                        "identity": output,
                                    },
                                    "decision": "pass",
                                }
                            ],
                        },
                    )

    def test_render_rejects_immutable_workflow_failure_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/output.csv",
                ),
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "immutable Provenance failure",
            ):
                RUNTIME.render_records(adjudication, scan, summary.with_suffix(""))

    def test_semantic_producer_pass_selects_recorded_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "data" / "semantic.csv", "name,value\nresult,3.0\n")
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "\n`Validation:`",
                    "\nThe semantic result is `3.0`.\n\n`Validation:`",
                ),
            )
            evidence = entry.parent / "evidence.csv"
            write(
                evidence,
                evidence.read_text(encoding="utf-8")
                + "e001,Results,statistic,3.0,data/semantic.csv :: value,\n",
            )
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            item = next(
                candidate
                for candidate in prepared["review_queue"]
                if candidate["identity"].endswith("data/semantic.csv")
            )
            match = {"entry": item["entry"], "identity": item["identity"]}

            with self.assertRaisesRegex(
                CONTRACTS.ValidationToolError,
                "requires a concrete producer candidate",
            ):
                DECISIONS.apply_review_decisions(
                    scan,
                    prepared,
                    {
                        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                        "actions": [{"match": match, "decision": "pass"}],
                    },
                )

            decided, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [{"match": match, "decision": "pass", "producer": 2}],
                },
            )
            row = next(
                row
                for entry_row in decided["entries"]
                for row in entry_row["targets"]
                if row["target"] == item["identity"]
            )
            self.assertTrue(row["producer_invocation"].startswith("e001:L"))
            graph = GRAPH_ADAPTER.build_dependency_graph(scan, decided)
            self.assertFalse(
                any("reviewed-workflow" in node.key.identity for node in graph.nodes)
            )

    def test_unique_mechanical_producer_records_exact_invocation_for_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "data.csv",
                "name,type,location\ninput_csv,CSV,data/direct.csv\n",
            )
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--direct-input')\n"
                "parser.add_argument('--working-parent')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.direct_input).read_text()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n",
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e001,Results,statistic,1.0,data/output.csv :: value,\n"
                'e001,Results,table,"name,value",data/output.csv,\n',
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/output.csv",
                ),
            )

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            row = next(
                target
                for prepared_entry in prepared["entries"]
                for target in prepared_entry["targets"]
                if target["target"] == output
            )

            self.assertIsNone(row["provenance"])
            self.assertTrue(row["producer_invocation"].startswith("e001:L"))
            self.assertTrue(
                any(
                    item["identity"] == output
                    and item["kind"] == "semantic_fallback"
                    for item in prepared["review_queue"]
                )
            )

    def test_multiple_mechanical_producers_require_exact_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                entry.parent / "data.csv",
                "name,type,location\ninput_csv,CSV,data/direct.csv\n",
            )
            write(
                entry.parent / "scripts" / "no_execute.py",
                "import argparse\n"
                "from pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--direct-input')\n"
                "parser.add_argument('--working-parent')\n"
                "parser.add_argument('--output')\n"
                "args = parser.parse_args()\n"
                "Path(args.input).read_text()\n"
                "Path(args.direct_input).read_text()\n"
                "Path(args.output).write_text('name,value\\nresult,1.0\\n')\n",
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e001,Results,statistic,1.0,data/output.csv :: value,\n"
                'e001,Results,table,"name,value",data/output.csv,\n',
            )
            command = (
                "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                "--input <input_csv> --direct-input data/direct.csv "
                "--working-parent data/workspace --output data/output.csv"
            )
            text = entry.read_text(encoding="utf-8").replace(
                "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                "--input <input_csv> --direct-input data/direct.csv "
                "--working-parent data/workspace --output data/command-only.csv",
                f"{command}\n{command}",
            )
            write(entry, text)

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            item = next(
                candidate
                for candidate in prepared["review_queue"]
                if candidate["identity"] == output
            )
            row = next(
                target
                for prepared_entry in prepared["entries"]
                for target in prepared_entry["targets"]
                if target["target"] == output
            )

            self.assertEqual(item["workflow"]["status"], "unresolved")
            self.assertEqual(item["workflow"]["matched_commands"], 2)
            self.assertNotIn("producer_invocation", row)
            decided, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [
                        {
                            "match": {"entry": "e001", "identity": output},
                            "decision": "pass",
                            "producer": 2,
                        }
                    ],
                },
            )
            decided_row = next(
                target
                for prepared_entry in decided["entries"]
                for target in prepared_entry["targets"]
                if target["target"] == output
            )
            self.assertTrue(decided_row["producer_invocation"].startswith("e001:L"))

    def test_generated_input_with_alternative_producers_requires_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            original = (
                "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py "
                "--input <input_csv> --direct-input data/direct.csv "
                "--working-parent data/workspace "
                "--output data/command-only.csv"
            )
            commands = "\n".join(
                (
                    "python scripts/upstream.py --input data/girmos.csv "
                    "--output data/generated.csv",
                    "python scripts/upstream.py --input data/tiptop.csv "
                    "--output data/generated.csv",
                    "python scripts/consumer.py --input data/generated.csv "
                    "--output data/output.csv",
                )
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(original, commands),
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
            write(entry.parent / "scripts" / "upstream.py", script)
            write(entry.parent / "scripts" / "consumer.py", script)
            write(entry.parent / "data" / "girmos.csv", "name,value\ngirmos,1\n")
            write(entry.parent / "data" / "tiptop.csv", "name,value\ntiptop,1\n")
            write(entry.parent / "data" / "generated.csv", "name,value\nresult,1\n")

            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
            output = identity_ending(scan, "data/output.csv")
            target_item = next(
                item
                for item in prepared["review_queue"]
                if item["identity"] == output
            )
            target_action = {
                "match": {"entry": target_item["entry"], "identity": output},
                "decision": "pass",
            }
            if target_item["workflow"]["matched_commands"] != 1:
                self.fail("fixture output must have one recorded producer")
            decided, _ = DECISIONS.apply_review_decisions(
                scan,
                prepared,
                {
                    "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                    "actions": [target_action],
                },
            )
            upstream = next(
                item
                for item in decided["review_queue"]
                if item["kind"] == "upstream_producer"
                and item["identity"] == output
            )
            self.assertEqual(len(upstream["producer_candidates"]), 2)
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
                                "entry": "e001",
                                "identity": output,
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
            row = next(
                row
                for prepared_entry in bound["entries"]
                for row in prepared_entry["targets"]
                if row["target"] == output
            )
            self.assertEqual(
                row["producer_bindings"],
                [
                    {
                        "material": chosen["material"],
                        "invocation": chosen["invocation"],
                    }
                ],
            )
            self.assertFalse(
                any(
                    item["kind"] == "upstream_producer"
                    and item["identity"] == output
                    for item in bound["review_queue"]
                )
            )
            dependencies = {item["path"] for item in row["dependencies"]}
            self.assertTrue(
                any(path.endswith("data/girmos.csv") for path in dependencies)
            )
            self.assertFalse(
                any(path.endswith("data/tiptop.csv") for path in dependencies)
            )

    def test_reproduction_mode_queues_and_records_explicit_comparisons(self) -> None:
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
            scan, _ = RUNTIME.scan_log(summary, jobs=1, mode="reproduction")
            prepared = prepare_adjudication(
                scan,
                "2026-08-07",
                RUNTIME.RULES_VERSION,
                mode="reproduction",
            )
            output = identity_ending(scan, "data/output.csv")
            reproduction = [
                item
                for item in prepared["review_queue"]
                if item["kind"] == "reproduction" and item["identity"] == output
            ]
            self.assertEqual(len(reproduction), 1)
            decisions = {
                "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {
                            "kind": "reproduction",
                            "entry": "e001",
                            "identity": output,
                        },
                        "decision": "reproduced",
                    }
                ],
            }

            decided, counts = DECISIONS.apply_review_decisions(
                scan, prepared, decisions
            )

            row = next(
                row
                for row in decided["entries"][0]["targets"]
                if row["target"] == output
            )
            self.assertEqual(row["reproducibility"], "2026-08-07")
            self.assertEqual(counts["reproduced"], 1)

    def test_standard_prepare_reuses_unchanged_reproduction_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = RUNTIME.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0]["reproducibility"] = "2026-08-07"
            output = root / "records"
            RUNTIME.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            reused_scan, _ = RUNTIME.scan_log(summary, jobs=1, prior_state=state)

            prepared = prepare_adjudication(
                reused_scan, "2026-08-08", RUNTIME.RULES_VERSION
            )

            retained = identity_ending(reused_scan, "data/output.csv")
            row = next(
                row
                for row in prepared["entries"][0]["targets"]
                if row["target"] == retained
            )
            self.assertEqual(row["reproducibility"], "2026-08-07")

    def test_prepare_rechecks_a_reported_failure_after_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            summary = root / "docs" / "mini.md"
            entry = root / "docs/mini/entries/2026-08-07-e001-check/e001.md"
            relative = "mini/entries/2026-08-07-e001-check/e001.md"
            write(summary, f"# Mini\n\n## Entries\n\n- [e001]({relative})\n")
            write(
                entry,
                "# Check\n\n## Trial\n\n`Steps:`\n\n"
                "```bash\npython scripts/run.py --output data/result.csv\n```\n\n"
                "`Results:`\n\nValue was `1.0`.\n",
            )
            write(
                entry.parent / "scripts/run.py",
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--output')\n",
            )
            write(
                entry.parent / "evidence.csv",
                "entry,section,kind,evidence,sources,transformation\n"
                "e001,Trial,statistic,1.0,data/result.csv :: field=value,\n",
            )
            result = entry.parent / "data/result.csv"
            write(result, "name,value\nresult,2.0\n")

            failed_scan, _ = RUNTIME.scan_log(summary, jobs=1)
            failed = prepare_adjudication(
                failed_scan, "2026-08-07", RUNTIME.RULES_VERSION
            )
            failed_target = failed["entries"][0]["targets"][0]
            self.assertEqual(failed_target["provenance"], "FAIL")

            write(result, "name,value\nresult,1.0\n")
            repaired_scan, _ = RUNTIME.scan_log(summary, jobs=1)
            repaired = prepare_adjudication(
                repaired_scan, "2026-08-08", RUNTIME.RULES_VERSION
            )
            repaired_target = repaired["entries"][0]["targets"][0]
            self.assertIsNone(repaired_target["provenance"])
            self.assertTrue(
                any(
                    item["kind"] == "semantic_fallback"
                    and item["identity"] == repaired_target["target"]
                    for item in repaired["review_queue"]
                )
            )
