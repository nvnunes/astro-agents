from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research_log_validation.py"
SPEC = importlib.util.spec_from_file_location("research_log_validation", SCRIPT)
assert SPEC and SPEC.loader
VALIDATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATION)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_log(root: Path) -> tuple[Path, Path]:
    (root / ".git").mkdir()
    summary = root / "docs" / "mini.md"
    entry = (
        root
        / "docs"
        / "mini"
        / "entries"
        / "2026-08-07-e001-validation-fixture"
        / "e001.md"
    )
    relative_entry = "mini/entries/2026-08-07-e001-validation-fixture/e001.md"
    write(
        summary,
        "# Mini Log\n\n"
        "## Summary\n\n"
        f"- The retained value is `1.0` ([e001]({relative_entry})).\n\n"
        "## Entries\n\n"
        f"- [e001]({relative_entry})\n",
    )
    write(
        entry,
        "# 2026-08-07: Validation Fixture\n\n"
        "## Results\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py --input <input_csv> "
        "--direct-input data/direct.csv "
        "--working-parent data/workspace "
        "--output data/command-only.csv\n"
        "python <log>/scripts/shared.py --flag\n"
        "```\n\n"
        "`Results:`\n\n"
        "The retained value is `1.0` in [output](data/output.csv).\n\n"
        "Build `v12`, `seed=2026`, `10PH`, and `2026-08-07` are not statistics.\n\n"
        "![invalid plot](data/invalid.png)\n\n"
        "A broken [artifact](data/missing.csv) is also recorded.\n\n"
        "The retained [collection](data/collection) is available for inspection.\n\n"
        "name | value\n"
        "--- | ---:\n"
        "result | `1.0`\n\n"
        "```text\n"
        "not | a table\n"
        "--- | ---\n"
        "metric | 1.0\n"
        "```\n\n"
        "External context uses @missing-source.\n",
    )
    write(entry.parent / "data" / "output.csv", "name,value\nresult,1.0\n")
    write(
        entry.parent / "evidence.csv",
        "entry,section,kind,evidence,sources,transformation\n"
        "e001,Results,statistic,1.0,data/output.csv :: value,\n"
        'e001,Results,table,"name,value",data/output.csv,\n'
        "e001,Results,output,not | a table,data/output.csv,\n",
    )
    write(
        root / "docs" / "mini" / "evidence.csv",
        "statistic,entry,section,transformation\n" "1.0,e001,Results,\n",
    )
    write(entry.parent / "data" / "invalid.png", "not a png\n")
    write(entry.parent / "data" / "command-only.csv", "name,value\nresult,1.0\n")
    write(entry.parent / "data" / "direct.csv", "name,value\ninput,2.0\n")
    write(entry.parent / "data" / "workspace" / "unrelated.txt", "temporary\n")
    write(entry.parent / "data" / "collection" / "a.txt", "a\n")
    write(entry.parent / "data" / "collection" / "b.txt", "b\n")
    write(
        entry.parent / "scripts" / "no_execute.py",
        "from pathlib import Path\n"
        "Path('EXECUTED').write_text('executed', encoding='utf-8')\n",
    )
    write(root / "docs" / "mini" / "scripts" / "shared.py", "value = 1\n")
    write(
        entry.parent / "data.csv",
        "name,type,location\n"
        "input_csv,CSV,data/output.csv\n"
        "input_csv,CSV,data/other.csv\n",
    )
    return summary, entry


def identity_ending(scan: dict, suffix: str) -> str:
    matches = [path for path in scan["resolved_paths"] if path.endswith(suffix)]
    if len(matches) != 1:
        raise AssertionError(f"expected one identity ending {suffix!r}, got {matches}")
    return matches[0]


def adjudication_for(scan: dict, entry: Path) -> dict:
    summary_item = scan["summary_items"][0]["selector"]
    output = identity_ending(scan, "data/output.csv")
    collection = identity_ending(scan, "data/collection")
    entry_identity = identity_ending(scan, "/e001.md")
    date = "2026-08-07"
    support_line, support_text = next(
        (number, line)
        for number, line in enumerate(entry.read_text(encoding="utf-8").splitlines(), 1)
        if "retained value is `1.0`" in line
    )
    entry_orphans = next(
        item for item in scan["entries"] if item["id"] == "e001"
    ).get("orphan_candidates", [])
    adjudication = {
        "schema_version": VALIDATION.ADJUDICATION_SCHEMA_VERSION,
        "validation_rules_version": VALIDATION.RULES_VERSION,
        "log": scan["summary"],
        "requested_scope": "complete standard scope",
        "scope": {"summary": True, "entries": list(scan["entry_order"])},
        "date": date,
        "mode": "standard",
        "summary": [
            {
                "source_item": scan["summary_items"][0]["identity"],
                "item": summary_item,
                "entries": ["e001"],
                "sections": ["Results"],
                "provenance": date,
                "support_reviewed": True,
                "support_evidence": [
                    {
                        "entry": "e001",
                        "section": "Results",
                        "lines": str(support_line),
                        "text": support_text,
                    }
                ],
                "dependencies": [
                    {"path": scan["summary"], "role": "summary"},
                    {"path": entry_identity, "role": "supporting-entry"},
                ],
                "findings": [],
            }
        ],
        "entries": [
            {
                "id": "e001",
                "title": "2026-08-07: Validation Fixture",
                "path": entry_identity,
                "scope_reconciled": True,
                "scope_kind": "entry",
                "scope_paths": [entry_identity],
                "orphan_items": [
                    {"identity": item["identity"], "decision": "accepted"}
                    for item in entry_orphans
                ],
                "targets": [
                    {
                        "target": output,
                        "sections": ["Results"],
                        "integrity": date,
                        "provenance": date,
                        "reproducibility": "-",
                        "notes": "-",
                        "dependencies": [
                            {"path": entry_identity, "role": "entry"},
                            {"path": output, "role": "target"},
                        ],
                        "findings": [],
                    },
                    {
                        "target": collection,
                        "sections": ["Results"],
                        "integrity": date,
                        "provenance": date,
                        "reproducibility": "-",
                        "notes": "selected member a.txt",
                        "dependencies": [
                            {"path": entry_identity, "role": "entry"},
                            {
                                "path": collection,
                                "role": "target",
                                "members": ["a.txt"],
                            },
                        ],
                        "findings": [],
                    },
                    {
                        "target": "Unprovenanced: displayed results table",
                        "sections": ["Results"],
                        "integrity": "FAIL",
                        "provenance": "FAIL",
                        "reproducibility": "N/A",
                        "notes": "-",
                        "dependencies": [
                            {"path": entry_identity, "role": "entry"}
                        ],
                        "findings": [
                            {
                                "check": "Integrity",
                                "finding": (
                                    "Expected a retained artifact; none was identified."
                                ),
                            },
                            {
                                "check": "Provenance",
                                "finding": (
                                    "Expected a generating workflow; none was recorded."
                                ),
                            },
                        ],
                    },
                ],
            }
        ],
        "review_queue": [],
    }
    by_id = {item["id"]: item for item in scan["entries"]}
    for scope_id in scan["entry_order"]:
        if scope_id == "e001":
            continue
        scanned = by_id[scope_id]
        adjudication["entries"].append(
            {
                "id": scope_id,
                "title": scanned["title"],
                "path": scanned["path"],
                "scope_reconciled": True,
                "scope_kind": scanned.get("scope_kind", "entry"),
                "scope_paths": scanned.get("scope_paths", [scanned["path"]]),
                "orphan_items": [
                    {"identity": item["identity"], "decision": "accepted"}
                    for item in scanned.get("orphan_candidates", [])
                ],
                "targets": [],
            }
        )
    return adjudication


def complete_adjudication(scan: dict) -> dict:
    prepared = VALIDATION.make_adjudication_template(
        scan, "2026-08-07", VALIDATION.RULES_VERSION
    )
    output = identity_ending(scan, "data/output.csv")
    invalid = identity_ending(scan, "data/invalid.png")
    collection = identity_ending(scan, "data/collection")
    missing = next(
        item["identity"]
        for item in prepared["review_queue"]
        if item["identity"].endswith("data/missing.csv")
    )
    decisions = {
        "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
        "actions": [
            {
                "match": {"kind": "semantic_provenance"},
                "decision": "support",
                "candidate": 1,
            },
            {
                "match": {"entry": "e001", "identity": output},
                "decision": "pass",
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
                        "The invalid or missing artifact cannot be traced to "
                        "retained evidence."
                    )
                },
            },
            {
                "match": {"entry": "e001", "identity": collection},
                "decision": "pass",
                "members": {collection: {"glob": "a.txt"}},
            },
            {"match": {"kind": "orphan_candidates"}, "decision": "drop"},
        ],
    }
    decided, counts = VALIDATION.apply_review_decisions(scan, prepared, decisions)
    if counts["remaining"]:
        raise AssertionError(f"fixture decisions left {counts['remaining']} items")
    return decided


class ScanTests(unittest.TestCase):
    def test_rules_version_is_shared_package_owned(self) -> None:
        self.assertEqual(VALIDATION.RULES_VERSION, "research-log-validation-v12")

    def test_scan_extracts_mechanics_without_executing_research_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)

            scan, metrics = VALIDATION.scan_log(summary, jobs=2)

            self.assertFalse((root / "EXECUTED").exists())
            self.assertEqual(scan["entry_order"], ["e001"])
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
                scanned_entry["commands"][0]["script"].endswith(
                    "scripts/no_execute.py"
                )
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

            scan, _ = VALIDATION.scan_log(summary, jobs=2)

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

            scan, _ = VALIDATION.scan_log(summary, jobs=2)

            orphan_identities = {
                candidate["identity"]
                for candidate in scan["entries"][0]["orphan_candidates"]
            }
            self.assertIn("external/linked/orphan.csv", orphan_identities)
            self.assertNotIn("external/direct.csv", orphan_identities)

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            orphans = {
                item["identity"]
                for item in scan["entries"][0]["orphan_candidates"]
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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

            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-10", VALIDATION.RULES_VERSION
            )
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            VALIDATION._reconcile_semantic_dependencies(scan, prepared)
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
                entry.read_text(encoding="utf-8").replace(
                    "python <log>/scripts/shared.py --flag",
                    "python scripts/make_maps.py --output-dir images/maps",
                ).replace(
                    "![invalid plot](data/invalid.png)",
                    "![invalid plot](data/invalid.png)\n\n"
                    "![retained map](images/maps/map.png)",
                ),
            )

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            orphans = {
                item["identity"]
                for item in scan["entries"][0]["orphan_candidates"]
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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

            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-10", VALIDATION.RULES_VERSION
            )
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            VALIDATION._reconcile_semantic_dependencies(scan, prepared)
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            command = next(
                item
                for item in scan["entries"][0]["commands"]
                if (item.get("script") or "").endswith("positional_output.py")
            )
            arguments = {item["option"]: item for item in command["path_arguments"]}

            self.assertEqual(arguments["first"]["role_hint"], "input")
            self.assertEqual(arguments["second"]["role_hint"], "output")
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
            packet, _ = VALIDATION.make_review_packet(
                scan, prepared, kind="orphan_candidates"
            )
            self.assertIn("Path(args.second).mkdir", packet)

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

            self.assertFalse(any(path.endswith("helper.py") for path in orphans))
            self.assertFalse(
                any(path.endswith("shared_helper.py") for path in orphans)
            )
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            entry_orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }
            log_scan = next(item for item in scan["entries"] if item["id"] == "Log level")
            log_orphans = {item["identity"] for item in log_scan["orphan_candidates"]}

            self.assertFalse(
                any(path.endswith("scripts/shared/helper.py") for path in entry_orphans)
            )
            self.assertTrue(any(path.endswith("scripts/helper.py") for path in log_orphans))

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

            self.assertTrue(any(path.endswith("local/helper.py") for path in orphans))
            self.assertFalse(
                any(path.endswith("shared/helper.py") for path in orphans)
            )

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            entry_orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }
            log_scan = next(item for item in scan["entries"] if item["id"] == "Log level")
            log_orphans = {item["identity"] for item in log_scan["orphan_candidates"]}

            self.assertFalse(any(path.endswith("entries/2026-08-07-e001-validation-fixture/scripts/batch.py") for path in entry_orphans))
            self.assertTrue(any(path.endswith("docs/mini/scripts/batch.py") for path in log_orphans))

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

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
                "function matlab_entrypoint(value)\n"
                "matlab_helper(value);\n"
                "end\n",
            )
            write(
                entry.parent / "scripts" / "matlab_helper.m",
                "function matlab_helper(value)\n"
                "disp(value);\n"
                "end\n",
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "--output data/command-only.csv",
                    "--output data/command-only.csv | tee data/output.csv",
                ),
            )

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            entry_scan = next(item for item in scan["entries"] if item["id"] == "e001")
            orphans = {
                item["identity"] for item in entry_scan["orphan_candidates"]
            }

            self.assertFalse(any(path.endswith("matlab_entrypoint.m") for path in orphans))
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            orphans = {
                item["identity"]
                for scanned in scan["entries"]
                for item in scanned["orphan_candidates"]
            }

            self.assertFalse(any(path.endswith("matlab_entrypoint.m") for path in orphans))
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
                "parser.add_argument('--addpath', action='append', type=Path, default=[])\n"
                "parser.add_argument('--command')\n"
                "args = parser.parse_args()\n"
                "addpath_commands = [f'addpath({path})' for path in args.addpath]\n"
                "def run_matlab_batch(command, *, cwd):\n"
                "    return command, cwd\n"
                "run_matlab_batch('; '.join([*addpath_commands, args.command]), cwd=args.cwd)\n",
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
                    '"record_generated_input(\'data/upstream.csv\', '
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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
                any(path.endswith("record_generated_input.m") for path in matlab_command["matlab_scripts"])
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
            self.assertNotIn(VALIDATION.display_path(entry.parent, root), orphans)
            self.assertNotIn(
                VALIDATION.display_path(entry.parent / "scripts", root), orphans
            )

            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-10", VALIDATION.RULES_VERSION
            )
            output = identity_ending(scan, "data/output.csv")
            output_row = next(
                row
                for entry_result in prepared["entries"]
                for row in entry_result["targets"]
                if row["target"] == output
            )
            output_row["provenance"] = "2026-08-10"
            VALIDATION._reconcile_semantic_dependencies(scan, prepared)
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

    def test_repository_index_protects_active_cross_log_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner_summary, owner_entry = make_log(root)
            owner_scripts = root / "docs" / "mini" / "scripts"
            active = owner_scripts / "cross_log_workflow.py"
            helper = owner_scripts / "cross_log_helper.py"
            dormant = owner_scripts / "dormant_only.py"
            write(active, "from cross_log_helper import VALUE\n")
            write(helper, "VALUE = 1\n")
            write(dormant, "VALUE = 2\n")

            consumer_summary = root / "docs" / "consumer.md"
            consumer_entry = (
                root
                / "docs"
                / "consumer"
                / "entries"
                / "2026-08-08-e001-consumer"
                / "e001.md"
            )
            relative_entry = "consumer/entries/2026-08-08-e001-consumer/e001.md"
            write(
                consumer_summary,
                "# Consumer Log\n\n## Entries\n\n"
                f"- [e001]({relative_entry})\n",
            )
            write(
                consumer_entry,
                "# 2026-08-08: Consumer\n\n"
                "## Cross-log workflow\n\n"
                "`Steps:`\n\n"
                "```bash\n"
                f"python {active.as_posix()}\n"
                "python scripts/consume.py "
                "--input "
                f"{(owner_entry.parent / 'data' / 'command-only.csv').as_posix()} "
                "--output data/result.csv\n"
                "```\n\n"
                "`Results:`\n\n"
                "The command completed.\n",
            )
            write(
                consumer_entry.parent / "scripts" / "consume.py",
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--input')\n"
                "parser.add_argument('--output')\n"
                "parser.parse_args()\n",
            )
            write(
                consumer_entry.parent / "scripts" / "unused.py",
                "import subprocess\nimport sys\n"
                f"subprocess.run([sys.executable, {str(dormant)!r}], check=True)\n",
            )

            index, metrics = VALIDATION.build_repository_dependency_index(root)
            self.assertEqual(metrics["status"], "rebuilt")
            owner_edges = VALIDATION._repository_dependencies(
                index, VALIDATION.display_path(owner_summary, root)
            )
            protected = {edge["path"] for edge in owner_edges}
            self.assertTrue(any(path.endswith(active.name) for path in protected))
            self.assertTrue(any(path.endswith(helper.name) for path in protected))
            self.assertFalse(any(path.endswith(dormant.name) for path in protected))

            scan, _ = VALIDATION.scan_log(
                owner_summary, jobs=1, repository_index=index
            )
            orphans = {
                item["identity"]
                for scanned in scan["entries"]
                for item in scanned["orphan_candidates"]
            }
            self.assertFalse(any(path.endswith(active.name) for path in orphans))
            self.assertFalse(any(path.endswith(helper.name) for path in orphans))
            self.assertFalse(
                any(path.endswith("data/command-only.csv") for path in orphans)
            )
            self.assertTrue(any(path.endswith(dormant.name) for path in orphans))

            write(
                consumer_summary,
                consumer_summary.read_text(encoding="utf-8") + "\n",
            )
            same_edges, metrics = VALIDATION.build_repository_dependency_index(
                root, index
            )
            self.assertEqual(metrics["logs_rebuilt"], 1)
            same_scan, _ = VALIDATION.scan_log(
                owner_summary, jobs=1, repository_index=same_edges
            )
            self.assertEqual(
                scan["input_fingerprint"], same_scan["input_fingerprint"]
            )

            write(
                consumer_entry,
                consumer_entry.read_text(encoding="utf-8").replace(
                    f"python {active.as_posix()}\n", ""
                ),
            )
            removed_edge_index, metrics = (
                VALIDATION.build_repository_dependency_index(root, same_edges)
            )
            self.assertEqual(metrics["logs_rebuilt"], 1)
            changed_scan, _ = VALIDATION.scan_log(
                owner_summary, jobs=1, repository_index=removed_edge_index
            )
            self.assertNotEqual(
                same_scan["input_fingerprint"], changed_scan["input_fingerprint"]
            )
            changed_orphans = {
                item["identity"]
                for scanned in changed_scan["entries"]
                for item in scanned["orphan_candidates"]
            }
            self.assertTrue(any(path.endswith(active.name) for path in changed_orphans))

    def test_repository_index_reuses_unchanged_inputs_and_rebuilds_on_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_log(root)

            first, first_metrics = VALIDATION.build_repository_dependency_index(root)
            second, second_metrics = VALIDATION.build_repository_dependency_index(
                root, first
            )
            self.assertEqual(first_metrics["status"], "rebuilt")
            self.assertEqual(second_metrics["status"], "unchanged")
            self.assertEqual(second_metrics["files_hashed"], 0)
            self.assertEqual(first, second)

            summary = root / "docs" / "mini.md"
            write(summary, summary.read_text(encoding="utf-8") + "\n")
            _, changed_metrics = VALIDATION.build_repository_dependency_index(
                root, second
            )
            self.assertEqual(changed_metrics["status"], "rebuilt")
            self.assertEqual(changed_metrics["files_hashed"], 1)
            self.assertEqual(changed_metrics["scripts_parsed"], 0)
            self.assertEqual(changed_metrics["logs_rebuilt"], 1)

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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

    def test_scoped_collection_members_are_connected_during_orphan_reconciliation(
        self,
    ) -> None:
        collection = "docs/mini/entries/e001/data/run"
        member = f"{collection}/summary.csv"
        scan = {
            "project_root": "/project",
            "resolved_paths": {collection: f"/project/{collection}"},
            "script_inventory": [],
            "entries": [],
            "mechanical_checks": {},
        }
        adjudication = {
            "summary": [],
            "entries": [
                {
                    "id": "e001",
                    "orphan_items": [
                        {
                            "identity": member,
                            "decision": "unresolved",
                            "fingerprint": "0" * 64,
                        }
                    ],
                    "targets": [
                        {
                            "target": "docs/mini/result.csv",
                            "sections": ["Results"],
                            "integrity": "2026-08-10",
                            "provenance": "2026-08-10",
                            "reproducibility": "-",
                            "dependencies": [
                                {
                                    "path": collection,
                                    "role": "input",
                                    "members": ["summary.csv"],
                                }
                            ],
                            "findings": [],
                        },
                        {
                            "target": VALIDATION.ORPHAN_TARGET,
                            "sections": [],
                            "integrity": "N/A",
                            "provenance": "FAIL",
                            "reproducibility": "N/A",
                            "findings": [],
                        },
                    ],
                }
            ],
            "review_queue": [
                {
                    "entry": "e001",
                    "kind": "orphan_candidates",
                    "identity": VALIDATION.ORPHAN_TARGET,
                    "candidates": [{"kind": "artifact", "identity": member}],
                }
            ],
        }

        VALIDATION._reconcile_semantic_dependencies(scan, adjudication)

        self.assertEqual(
            adjudication["entries"][0]["orphan_items"][0]["decision"],
            "accepted",
        )
        self.assertEqual(adjudication["review_queue"], [])
        self.assertFalse(
            any(
                row["target"] == VALIDATION.ORPHAN_TARGET
                for row in adjudication["entries"][0]["targets"]
            )
        )

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
                    "python scripts/unrelated.py --input data/unrelated.csv "
                    "--output-dir data",
                ),
            )

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
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
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": output},
                        "decision": "pass",
                        "add_dependencies": [
                            {"path": downstream, "role": "producer"},
                            {"path": intermediate, "role": "input"},
                        ],
                    },
                    {
                        "match": {
                            "entry": "e001",
                            "identity": VALIDATION.ORPHAN_TARGET,
                        },
                        "decision": "orphan",
                        "unresolved": orphan_identities,
                    },
                ],
            }

            decided, _ = VALIDATION.apply_review_decisions(
                scan, prepared, decisions
            )

            entry_result = next(item for item in decided["entries"] if item["id"] == "e001")
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
                self.assertTrue(any(path.endswith(suffix) for path in dependency_paths))
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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            scope = next(
                item
                for item in scan["entries"]
                if item.get("scope_kind") == "entry-global"
            )
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )

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
                "# Mini Log\n\n## Entries\n\n" f"- [e001]({relative_entry})\n",
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

            scan, metrics = VALIDATION.scan_log(summary, jobs=1)
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

            adjudication = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
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
                "# Mini Log\n\n## Entries\n\n" f"- [e001]({relative_entry})\n",
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

            scan, metrics = VALIDATION.scan_log(summary, jobs=1)

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
                "statistic,entry,section,transformation\n" "1.0,e999,Results,\n",
            )

            scan, metrics = VALIDATION.scan_log(summary, jobs=1)
            adjudication = VALIDATION.make_adjudication_template(
                scan, "2026-08-10", VALIDATION.RULES_VERSION
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
                ["e001"],
            )
            index_path = root / VALIDATION.REPOSITORY_INDEX_FILENAME
            self.assertTrue(index_path.is_file())
            metrics = json.loads(result.stdout)
            self.assertEqual(metrics["repository_index_status"], "rebuilt")

            index_result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "index",
                    "--project-root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(index_result.returncode, 0, index_result.stderr)
            self.assertEqual(json.loads(index_result.stdout)["status"], "unchanged")

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
                    self.assertEqual(
                        VALIDATION._inspect_structure(path)["status"], "fail"
                    )

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
            structure = VALIDATION._inspect_structure(valid_ecsv)
            self.assertEqual(structure["status"], "ok")
            self.assertEqual(structure["rows"], 2)
            self.assertEqual(structure["columns"], ["outer_pix"])

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

            parsed = VALIDATION.parse_markdown(entry)

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

            parsed = VALIDATION.parse_markdown(entry)
            candidates = VALIDATION._candidate_references(
                parsed, entry, Path(directory)
            )

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

            parsed = VALIDATION.parse_markdown(entry)

            self.assertEqual(
                VALIDATION._candidate_references(parsed, entry, Path(directory)),
                [],
            )

    def test_prepare_prefills_mechanics_and_bounds_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)

            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
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
            VALIDATION._numeric_equivalent(
                "68%", [0.676], "Converted to percent and rounded"
            )
        )
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "11 h", [39600], "Converted seconds to hours and rounded"
            )
        )
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "300", [297.216], "Rounded to one significant figure"
            )
        )
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "650", [652.457], "Rounded to two significant figures"
            )
        )
        self.assertFalse(VALIDATION._numeric_equivalent("0.7", [0.618], "Rounded"))
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "6.1 arcmin^2",
                [6.0856985504813625],
                "Rounded to one decimal and added unit",
            )
        )
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "3.5 GiB",
                [3540.4],
                "Converted MiB to GiB and rounded to one decimal",
            )
        )
        self.assertFalse(
            VALIDATION._numeric_equivalent(
                "3.5 GiB",
                [3648.0],
                "Converted MiB to GiB and rounded to one decimal",
            )
        )
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "67%",
                [67.4931],
                "Rounded to a whole percent and added percent suffix",
            )
        )
        self.assertFalse(
            VALIDATION._numeric_equivalent(
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

            status, values, _ = VALIDATION._locator_values(
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

            status, values, _ = VALIDATION._locator_values(
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

            scalar_status, scalar_values, _ = VALIDATION._locator_values(
                structured, "path=simulation[0].throughput"
            )
            records_status, records_values, _ = VALIDATION._locator_values(
                structured, "path=simulation; fields=policy|throughput"
            )
            text_status, text_values, _ = VALIDATION._locator_values(
                log, "text=completed 49152 outer pixels"
            )
            whole_status, _, whole_detail = VALIDATION._locator_values(table, "")

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

            table_result = VALIDATION._locator_values(table, "")
            structured_result = VALIDATION._locator_values(structured, "path=records")

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

            status, values, detail = VALIDATION._locator_values(
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

            lines = VALIDATION._collection_packet_lines(scan, "collection")

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

            filtered = VALIDATION._locator_values(
                source, "path=$; level=6; field=median"
            )
            relative = VALIDATION._locator_values(
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

            filtered = VALIDATION._locator_values(
                source, "path=$; labels=base; field=values"
            )
            fields = VALIDATION._locator_values(source, "path=$; fields=labels|cases")
            indexed = VALIDATION._locator_values(source, "path=values[1]")

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

            rows = VALIDATION._locator_values(
                source, "path=status/state; property=shape[0]"
            )
            shapes = VALIDATION._locator_values(
                source,
                "path=$; fields=status/state|stats/sr; property=shape",
            )
            invalid = VALIDATION._locator_values(
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

            status, values, detail = VALIDATION._locator_values(source, "path=value")

            self.assertEqual((status, values), ("unresolved", []))
            self.assertIn("deserialization is prohibited", detail)

    def test_numeric_equivalence_respects_scientific_notation_precision(self) -> None:
        self.assertTrue(
            VALIDATION._numeric_equivalent(
                "3.604586e+11",
                [360458629208.8278],
                "Formatted in scientific notation and rounded",
            )
        )
        self.assertFalse(
            VALIDATION._numeric_equivalent(
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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

    def test_review_packet_extracts_context_without_deciding_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )

            packet, counts = VALIDATION.make_review_packet(scan, prepared)

            self.assertEqual(sum(counts.values()), len(prepared["review_queue"]))
            self.assertIn("# Validation Review Packet", packet)
            self.assertIn("The retained value is `1.0`", packet)
            self.assertIn('values: ["1.0"]', packet)
            self.assertIn("python scripts/no_execute.py", packet)
            self.assertTrue(prepared["review_queue"])

            summary_packet, summary_counts = VALIDATION.make_review_packet(
                scan, prepared, entry="Summary"
            )
            self.assertEqual(summary_counts, {"semantic_provenance": 1})
            self.assertIn("Summary: 1.0", summary_packet)
            self.assertNotIn("Orphaned artifacts", summary_packet)

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

            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
            summary_review = next(
                item
                for item in prepared["review_queue"]
                if item["entry"] == "Summary"
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
            output = identity_ending(scan, "data/output.csv")
            invalid = identity_ending(scan, "data/invalid.png")
            collection = identity_ending(scan, "data/collection")
            missing = next(
                item["identity"]
                for item in prepared["review_queue"]
                if item["identity"].endswith("data/missing.csv")
            )
            decisions = {
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"kind": "semantic_provenance"},
                        "decision": "support",
                        "candidate": 1,
                    },
                    {
                        "match": {"entry": "e001", "identity": output},
                        "decision": "pass",
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
                        "members": {collection: {"glob": "a.txt"}},
                    },
                    {
                        "match": {"kind": "orphan_candidates"},
                        "decision": "drop",
                    },
                ],
            }

            adjudication, counts = VALIDATION.apply_review_decisions(
                scan, prepared, decisions
            )

            self.assertEqual(counts["remaining"], 0)
            self.assertEqual(adjudication["review_queue"], [])
            rendered = root / "records"
            VALIDATION.render_records(adjudication, scan, rendered)
            self.assertTrue((rendered / "validation.md").exists())

    def test_compact_decisions_validate_matches_and_collection_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
            collection = identity_ending(scan, "data/collection")
            unknown = {
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e999"},
                        "decision": "pass",
                    }
                ],
            }
            bad_member = {
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": "pass",
                        "members": {collection: ["missing.txt"]},
                    }
                ],
            }

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "matches no unresolved"
            ):
                VALIDATION.apply_review_decisions(scan, prepared, unknown)
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "does not exist as a file"
            ):
                VALIDATION.apply_review_decisions(scan, prepared, bad_member)
            ignored_finding = {
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": collection},
                        "decision": "pass",
                        "findings": {"Provenance": "Would otherwise be ignored."},
                    }
                ],
            }
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "keys not used by pass"
            ):
                VALIDATION.apply_review_decisions(scan, prepared, ignored_finding)

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
            scan, _ = VALIDATION.scan_log(summary, jobs=1, mode="reproduction")
            prepared = VALIDATION.make_adjudication_template(
                scan,
                "2026-08-07",
                VALIDATION.RULES_VERSION,
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
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
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

            decided, counts = VALIDATION.apply_review_decisions(
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0]["reproducibility"] = (
                "2026-08-07"
            )
            output = root / "records"
            VALIDATION.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            reused_scan, _ = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )

            prepared = VALIDATION.make_adjudication_template(
                reused_scan, "2026-08-08", VALIDATION.RULES_VERSION
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

            failed_scan, _ = VALIDATION.scan_log(summary, jobs=1)
            failed = VALIDATION.make_adjudication_template(
                failed_scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
            failed_target = failed["entries"][0]["targets"][0]
            self.assertEqual(failed_target["provenance"], "FAIL")

            write(result, "name,value\nresult,1.0\n")
            repaired_scan, _ = VALIDATION.scan_log(summary, jobs=1)
            repaired = VALIDATION.make_adjudication_template(
                repaired_scan, "2026-08-08", VALIDATION.RULES_VERSION
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


class RenderTests(unittest.TestCase):
    def test_projection_count_labels_are_grammatical(self) -> None:
        self.assertEqual(VALIDATION._counted(1, "target"), "1 target")
        self.assertEqual(VALIDATION._counted(2, "target"), "2 targets")
        self.assertEqual(
            VALIDATION._counted(1, "eligible target"), "1 eligible target"
        )

    def test_render_accepts_documented_orphan_failure_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            orphan_items = adjudication["entries"][0]["orphan_items"]
            orphan_items[0]["decision"] = "unresolved"
            adjudication["entries"][0]["targets"].append(
                {
                    "target": VALIDATION.ORPHAN_TARGET,
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

            VALIDATION.render_records(adjudication, scan, output)
            lint = VALIDATION.lint_records(output)

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
            self.assertFalse(VALIDATION.lint_records(output)["ok"])

    def test_render_and_lint_reject_success_dependency_orphan_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            entry_result = adjudication["entries"][0]
            orphan_item = entry_result["orphan_items"][0]
            orphan_item["decision"] = "unresolved"
            entry_result["targets"].append(
                {
                    "target": VALIDATION.ORPHAN_TARGET,
                    "sections": ["-"],
                    "integrity": "N/A",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": "1 unresolved item",
                    "dependencies": [
                        {"path": entry_result["path"], "role": "entry"}
                    ],
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
            successful["dependencies"].append(
                {"path": orphan_item["identity"], "role": "producer"}
            )

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError,
                "unresolved orphan is a dependency of a successful check",
            ):
                VALIDATION.render_records(adjudication, scan, root / "rejected")

            successful["dependencies"].pop()
            output = root / "records"
            VALIDATION.render_records(adjudication, scan, output)
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            check = next(
                item
                for item in state["completed_checks"]
                if item["result"] == "2026-08-07" and item["entry"] == "e001"
            )
            check["dependencies"].append(
                {"path": orphan_item["identity"], "role": "producer"}
            )
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            lint = VALIDATION.lint_records(output)

            self.assertTrue(
                any(
                    "unresolved orphan is a dependency of a successful check"
                    in issue
                    for issue in lint["issues"]
                )
            )

    def test_update_summary_rejects_noncanonical_report_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "canonical validation report"
            ):
                VALIDATION.update_summary_validation(summary, output)

    def test_update_summary_projects_report_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            original = summary.read_text(encoding="utf-8")
            write(
                summary,
                original.replace(
                    "# Mini Log\n\n",
                    "# Mini Log\n\n## Contents\n\n"
                    "- [Entries](#entries)\n"
                    "- [Summary](#summary)\n"
                    "- [AI Use](#ai-use)\n\n",
                )
                + "\n## AI Use\n\nResearcher-led fixture.\n",
            )

            VALIDATION.update_summary_validation(summary, output)
            VALIDATION.update_summary_validation(summary, output)

            text = summary.read_text(encoding="utf-8")
            self.assertEqual(text.count("## Validation"), 1)
            self.assertEqual(text.count("- [Validation](#validation)"), 1)
            self.assertLess(text.index("## Validation"), text.index("## AI Use"))
            self.assertIn("[Detailed validation report](mini/validation.md)", text)
            self.assertIn("Summary statistics: 2026-08-07 — 1 checked", text)
            self.assertIn("`FAIL` - 1 of 3 targets failed", text)
            self.assertIn("| e001 | 2026-08-07 |", text)

    def test_generated_summary_projection_does_not_invalidate_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(
                summary,
                summary.read_text(encoding="utf-8").replace(
                    "# Mini Log\n\n",
                    "# Mini Log\n\n## Contents\n\n"
                    "- [Entries](#entries)\n"
                    "- [Summary](#summary)\n"
                    "- [AI Use](#ai-use)\n\n",
                )
                + "\n## AI Use\n\nResearcher-led fixture.\n",
            )
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            VALIDATION.update_summary_validation(summary, output)
            _, metrics = VALIDATION.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["reusable_checks"], 7)
            self.assertEqual(metrics["rerun_checks"], 0)
            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertFalse(metrics["semantic_review_required"])

    def test_synthesis_only_entry_change_does_not_invalidate_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").rstrip()
                + "\n\n## Historical context\n\n"
                + "`Findings:`\n\nResearcher-validated narrative only.\n",
            )

            _, metrics = VALIDATION.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["reusable_checks"], 7)
            self.assertEqual(metrics["rerun_checks"], 0)

    def test_shifted_summary_support_locator_is_rediscovered_mechanically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
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

            changed, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            prepared = VALIDATION.make_adjudication_template(
                changed, "2026-08-08", VALIDATION.RULES_VERSION
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["requested_scope"] = "Summary claims only"
            adjudication["scope"]["entries"] = []
            adjudication["entries"] = []
            output = root / "records"

            counts = VALIDATION.render_records(adjudication, scan, output)
            lint = VALIDATION.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )

            self.assertTrue(lint["ok"], lint["issues"])
            self.assertEqual(counts["summary_rows"], 1)
            self.assertEqual(counts["entry_rows"], 0)
            self.assertEqual(counts["entries"], 0)

    def test_partial_scope_does_not_overwrite_existing_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            before = (output / "validation.md").read_bytes()
            adjudication = adjudication_for(scan, entry)
            adjudication["scope"]["entries"] = []
            adjudication["entries"] = []

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "cannot overwrite"
            ):
                VALIDATION.render_records(adjudication, scan, output)
            self.assertEqual((output / "validation.md").read_bytes(), before)

    def test_render_rejects_rows_omitted_from_declared_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"] = []

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "entry order mismatch"
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

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
                "e001,Results,statistic,1.0,data/output.csv :: value,\n",
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output_identity = identity_ending(scan, "data/output.csv")
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
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
                "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
                "actions": [
                    {
                        "match": {"entry": "e001", "identity": output_identity},
                        "decision": "fail",
                        "findings": {"Provenance": "Stale value mismatch."},
                    }
                ],
            }
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError,
                "matches no unresolved queue items",
            ):
                VALIDATION.apply_review_decisions(scan, prepared, stale_decision)

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
                VALIDATION.ValidationToolError,
                "overrides a mechanically resolved PASS",
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

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
                "e001,Results,statistic,1.0,data/output.csv :: value,\n",
            )
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output_identity = identity_ending(scan, "data/output.csv")
            prepared = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
            )
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
            self.assertEqual(
                VALIDATION._semantic_failure_bases(item), {"workflow"}
            )

            def fail_action(**extra: str) -> dict:
                return {
                    "schema_version": VALIDATION.DECISION_SCHEMA_VERSION,
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
                VALIDATION.ValidationToolError,
                "requires an unresolved failure_basis",
            ):
                VALIDATION.apply_review_decisions(
                    scan, prepared, fail_action()
                )
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError,
                "requires an unresolved failure_basis",
            ):
                VALIDATION.apply_review_decisions(
                    scan, prepared, fail_action(failure_basis="evidence")
                )

            decided, _ = VALIDATION.apply_review_decisions(
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
                VALIDATION.ValidationToolError,
                "unsupported semantic basis",
            ):
                VALIDATION.render_records(
                    stale_adjudication, scan, root / "records"
                )

    def test_render_rejects_unnecessary_summary_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            duplicate = json.loads(json.dumps(adjudication["summary"][0]))
            duplicate["item"] = "The same supported source item was split again."
            adjudication["summary"].append(duplicate)

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "unnecessarily split"
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

    def test_render_round_trip_and_collection_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=2)
            adjudication = adjudication_for(scan, entry)
            output = root / "records"

            counts = VALIDATION.render_records(adjudication, scan, output)
            lint = VALIDATION.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )

            self.assertTrue(lint["ok"], lint["issues"])
            self.assertEqual(counts["summary_rows"], 1)
            self.assertEqual(counts["entry_rows"], 3)
            self.assertEqual(counts["failure_rows"], 1)
            self.assertEqual(counts["successful_checks"], 5)
            self.assertEqual(counts["completed_checks"], 7)
            failures = (output / "validation-failures.md").read_text(encoding="utf-8")
            self.assertEqual(failures.count("### "), 1)
            self.assertEqual(failures.count("- Check:"), 2)

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

    def test_prior_state_reuses_only_unchanged_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            _, unchanged_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(unchanged_metrics["reusable_checks"], 7)
            self.assertEqual(unchanged_metrics["rerun_checks"], 0)
            self.assertEqual(unchanged_metrics["incremental_status"], "unchanged")
            self.assertEqual(
                unchanged_metrics["cached_result"]["failure_rows"], 1
            )

            reused_scan, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "complete from cached state"
            ):
                VALIDATION.make_adjudication_template(
                    reused_scan, "2026-08-08", VALIDATION.RULES_VERSION
                )

            write(entry.parent / "data" / "output.csv", "name,value\nresult,2.0\n")
            changed_scan, changed_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(changed_metrics["reusable_checks"], 5)
            self.assertEqual(changed_metrics["rerun_checks"], 2)
            changed = identity_ending(changed_scan, "data/output.csv")
            self.assertEqual(
                changed_scan["incremental"]["files"][changed]["status"], "changed"
            )

    def test_orphan_disposition_rechecks_when_candidate_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            write(entry.parent / "scripts" / "unused.py", "value = 1\n")
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
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
            changed_scan, _ = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            prepared = VALIDATION.make_adjudication_template(
                changed_scan, "2026-08-08", VALIDATION.RULES_VERSION
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"].pop()
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            unchanged, metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )

            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertFalse(metrics["semantic_review_required"])
            self.assertEqual(metrics["cached_result"]["failure_rows"], 0)
            self.assertEqual(metrics["reusable_checks"], 5)
            self.assertEqual(metrics["rerun_checks"], 0)
            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "complete from cached state"
            ):
                VALIDATION.make_adjudication_template(
                    unchanged, "2026-08-08", VALIDATION.RULES_VERSION
                )

    def test_damaged_report_requires_render_but_not_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            (output / "validation.md").write_text("damaged\n", encoding="utf-8")

            changed, metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )

            self.assertEqual(metrics["incremental_status"], "loaded")
            self.assertFalse(metrics["semantic_review_required"])
            prepared = VALIDATION.make_adjudication_template(
                changed, "2026-08-08", VALIDATION.RULES_VERSION
            )
            self.assertEqual(prepared["review_queue"], [])

    def test_partial_change_reuses_unaffected_failures_and_orphan_decisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(entry.parent / "data" / "output.csv", "name,value\nresult,2.0\n")

            changed, metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            prepared = VALIDATION.make_adjudication_template(
                changed, "2026-08-08", VALIDATION.RULES_VERSION
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            _, metrics = VALIDATION.scan_log(
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                entry,
                entry.read_text(encoding="utf-8").replace(
                    "External context uses", "Updated context uses"
                ),
            )

            _, entry_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertGreater(entry_metrics["rerun_checks"], 0)

            write(
                summary,
                summary.read_text(encoding="utf-8").replace("`1.0`", "`2.0`"),
            )
            write(
                root / "docs" / "mini" / "evidence.csv",
                "statistic,entry,section,transformation\n"
                "2.0,e001,Results,\n",
            )
            changed, summary_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            prepared = VALIDATION.make_adjudication_template(
                changed, "2026-08-08", VALIDATION.RULES_VERSION
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
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

            changed, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            entry_identity = identity_ending(changed, "/e001.md")
            state["files"][entry_identity] = changed["files"][entry_identity]
            state["input_files"][entry_identity] = changed["files"][entry_identity]
            state["input_fingerprint"] = changed["input_fingerprint"]

            rescanned, metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
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

            changed, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            association = changed["entries"][0]["evidence_record"]["identity"]
            state["files"][association] = changed["files"][association]
            state["input_files"][association] = changed["files"][association]
            state["input_fingerprint"] = changed["input_fingerprint"]

            rescanned, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
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

            changed, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            entry_identity = identity_ending(changed, "/e001.md")
            current_entry_identity = changed["files"][entry_identity]
            state["files"][entry_identity] = current_entry_identity
            state["input_files"][entry_identity] = current_entry_identity
            state["input_fingerprint"] = changed["input_fingerprint"]
            for check in state["completed_checks"]:
                for dependency in check["dependencies"]:
                    if dependency["path"] == entry_identity:
                        dependency["identity"] = current_entry_identity

            rescanned, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            output_identity = identity_ending(rescanned, "data/output.csv")
            provenance = next(
                check
                for check in rescanned["incremental"]["checks"]
                if check["entry"] == "e001"
                and check["target"] == output_identity
                and check["check"] == "Provenance"
            )

            self.assertEqual(provenance["status"], "rerun")
            self.assertIn(
                "dependency-contract", provenance["changed_dependencies"]
            )

    def test_orphan_inventory_addition_is_reviewed_and_removal_restores_reuse(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            unused = entry.parent / "scripts" / "new_unused.py"
            write(unused, "value = 1\n")

            changed, _ = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
            prepared = VALIDATION.make_adjudication_template(
                changed, "2026-08-08", VALIDATION.RULES_VERSION
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
            self.assertEqual(reviewed, [VALIDATION.display_path(unused, root)])

            unused.unlink()
            _, restored = VALIDATION.scan_log(summary, jobs=1, prior_state=state)
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
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = summary.with_suffix("")
            VALIDATION.render_records(complete_adjudication(scan), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )
            write(
                summary,
                summary.read_text(encoding="utf-8").replace(
                    "Initial disclosure.", "Revised disclosure."
                ),
            )

            _, metrics = VALIDATION.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["incremental_status"], "unchanged")
            self.assertFalse(metrics["semantic_review_required"])

    def test_collection_reuse_tracks_only_selected_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            write(
                entry.parent / "data" / "collection" / "b.txt",
                "changed but unselected\n",
            )
            _, unselected_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(unselected_metrics["reusable_checks"], 7)
            self.assertEqual(unselected_metrics["rerun_checks"], 0)

            write(
                entry.parent / "data" / "collection" / "new.txt",
                "new member\n",
            )
            _, added_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertGreater(added_metrics["rerun_checks"], 0)
            (entry.parent / "data" / "collection" / "new.txt").unlink()

            write(
                entry.parent / "data" / "collection" / "a.txt", "changed and selected\n"
            )
            _, selected_metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state
            )
            self.assertEqual(selected_metrics["reusable_checks"], 5)
            self.assertEqual(selected_metrics["rerun_checks"], 2)

    def test_changed_evidence_association_invalidates_provenance_not_integrity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            association = scan["entries"][0]["evidence_record"]["identity"]
            output_row = adjudication["entries"][0]["targets"][0]
            output_row["dependencies"].append(
                {"path": association, "role": "evidence-association"}
            )
            output = root / "records"
            VALIDATION.render_records(adjudication, scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            evidence_path = entry.parent / "evidence.csv"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            _, metrics = VALIDATION.scan_log(summary, jobs=1, prior_state=state)

            self.assertEqual(metrics["reusable_checks"], 6)
            self.assertEqual(metrics["rerun_checks"], 1)

    def test_prior_state_is_not_reused_across_rule_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)
            state = json.loads(
                (output / "validation-state.json").read_text(encoding="utf-8")
            )

            changed_scan, metrics = VALIDATION.scan_log(
                summary, jobs=1, prior_state=state, rules_version="new-rules"
            )

            self.assertEqual(changed_scan["incremental"]["status"], "rules-changed")
            self.assertEqual(metrics["reusable_checks"], 0)
            self.assertEqual(metrics["rerun_checks"], 7)

    def test_renderer_rejects_dependency_changed_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            write(entry.parent / "data" / "output.csv", "name,value\nresult,3.0\n")
            output = root / "records"

            with self.assertRaisesRegex(
                VALIDATION.FileChangedError, "changed after scan"
            ):
                VALIDATION.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_renderer_requires_checked_integrity_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["entries"][0]["targets"][0]["integrity"] = "-"

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "successful date or FAIL"
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

    def test_renderer_verifies_summary_support_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["summary"][0]["support_evidence"][0]["text"] = "invented text"

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "does not match its entry lines"
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

    def test_renderer_requires_one_summary_entry_and_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = adjudication_for(scan, entry)
            adjudication["summary"][0]["entries"] = ["e001", "e001"]

            with self.assertRaisesRegex(
                VALIDATION.ValidationToolError, "exactly one entry and section"
            ):
                VALIDATION.render_records(adjudication, scan, root / "records")

    def test_renderer_rejects_unresolved_results_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            adjudication = VALIDATION.make_adjudication_template(
                scan, "2026-08-07", VALIDATION.RULES_VERSION
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
                VALIDATION.ValidationToolError, "unresolved review-queue"
            ):
                VALIDATION.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_renderer_requires_explicit_collection_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
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
                VALIDATION.ValidationToolError, "requires explicit members"
            ):
                VALIDATION.render_records(adjudication, scan, output)
            self.assertFalse(output.exists())

    def test_linter_detects_state_and_markdown_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, entry = make_log(root)
            scan, _ = VALIDATION.scan_log(summary, jobs=1)
            output = root / "records"
            VALIDATION.render_records(adjudication_for(scan, entry), scan, output)

            report = output / "validation.md"
            report.write_text(
                report.read_text(encoding="utf-8") + "| - |\n", encoding="utf-8"
            )
            state_path = output / "validation-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["files"].pop(next(iter(state["files"])))
            state_path.write_text(json.dumps(state), encoding="utf-8")

            lint = VALIDATION.lint_records(
                output, expected_entry_order=scan["entry_order"]
            )
            self.assertFalse(lint["ok"])
            self.assertIn(
                "validation.md contains a plain hyphen table cell", lint["issues"]
            )
            self.assertIn(
                "state file identities do not exactly match completed-check "
                "dependencies",
                lint["issues"],
            )


if __name__ == "__main__":
    unittest.main()
