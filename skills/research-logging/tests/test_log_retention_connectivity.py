"""Retention authoring agrees with evidence-rooted material classification."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from log_commands import retention_graph
from log_commands.context import resolve_entry, resolve_log
from research_log_cli_test_support import SCRIPTS, run_log, run_pyrun_process
from test_log_command_sync import fixture, sync
from test_log_evidence_sync import evidence, retained_files, set_results
from test_log_graph_lifecycle import action, checked
from validation.research_graph import (
    AmbiguityKind,
    AmbiguityObservation,
    EdgeKind,
    NodeKind,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)


class RetentionConnectivityTests(unittest.TestCase):
    def test_repeated_script_invocations_share_one_analysis_per_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py --value first"
            )
            document.write_text(
                document.read_text()
                + "\n## Second\n\n`Steps:`\n\n```bash\n"
                + "./pyrun --cid other -- scripts/build.py --value second\n"
                + "```\n\n`Results:`\n\nPending.\n"
            )
            checked(sync(logical, "--cid", "other"))
            target = entry / "scripts/unused.py"
            target.write_text("# Disconnected test\n")
            context = resolve_entry(resolve_log(logical), "e001")
            with mock.patch.object(
                retention_graph,
                "_reached_code_paths",
                wraps=retention_graph._reached_code_paths,
            ) as reached:
                self.assertEqual(
                    retention_graph.retention_connections(context, [target]), ()
                )
                self.assertEqual(reached.call_count, 1)
                self.assertEqual(
                    retention_graph.retention_connections(context, [target]), ()
                )
                self.assertEqual(reached.call_count, 2)

    def conflicting_graph(
        self,
        entry: Path,
        *,
        evidence_rooted: bool = False,
        directory: bool = False,
    ) -> ResearchGraph:
        entry = entry.resolve()
        source = ResearchNode(NodeKind.MATERIAL, str(entry / "data/source.csv"))
        output = ResearchNode(NodeKind.MATERIAL, str(entry / "data/output.csv"))
        producers = tuple(
            ResearchNode(
                NodeKind.COMMAND,
                str(index),
                "e001",
                {"cid": f"producer-{index}", "sequence": index},
            )
            for index in (1, 2)
        )
        nodes = [source, output, *producers]
        edges = [
            ResearchEdge(EdgeKind.ORIGIN, source.node_id, producer.node_id)
            for producer in producers
        ]
        edges.extend(
            ResearchEdge(EdgeKind.PRODUCTION, producer.node_id, output.node_id)
            for producer in producers
        )
        subject = output
        if directory:
            subject = ResearchNode(
                NodeKind.COLLECTION,
                "bundle",
                "e001",
                {"root": str(entry / "data"), "mechanism": "directory"},
            )
            nodes.append(subject)
        if evidence_rooted:
            evidence_record = ResearchNode(NodeKind.EVIDENCE_RECORD, "value", "e001")
            nodes.append(evidence_record)
            edges.append(
                ResearchEdge(
                    EdgeKind.DECLARATION,
                    evidence_record.node_id,
                    output.node_id,
                )
            )
        ambiguity = AmbiguityObservation(
            AmbiguityKind.MULTIPLE_PRODUCERS,
            subject.node_id,
            tuple(producer.node_id for producer in producers),
        )
        return ResearchGraph(tuple(nodes), tuple(edges), (ambiguity,))

    def test_unrelated_conflicting_producers_do_not_block_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(Path(directory), "./pyrun scripts/build.py")
            checked(sync(logical))
            (entry / "data/unused.txt").write_text("retained")
            with mock.patch.object(
                retention_graph,
                "build_evaluation_graph",
                return_value=self.conflicting_graph(entry),
            ):
                checked(
                    action(
                        logical,
                        "retention",
                        "add",
                        "--id",
                        "kept",
                        "--target",
                        "data/unused.txt",
                    )
                )

    def test_relevant_producer_conflicts_are_rejected_without_writes(self):
        cases = (
            ("data/output.csv", False, False),
            ("data/source.csv", True, False),
            ("data/member.txt", False, True),
        )
        for target, evidence_rooted, directory_conflict in cases:
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as directory,
            ):
                logical, entry, _ = fixture(Path(directory), "./pyrun scripts/build.py")
                checked(sync(logical))
                (entry / target).write_text("retained")
                before = retained_files(logical)
                graph = self.conflicting_graph(
                    entry,
                    evidence_rooted=evidence_rooted,
                    directory=directory_conflict,
                )
                for dry_run in (False, True):
                    with mock.patch.object(
                        retention_graph,
                        "build_evaluation_graph",
                        return_value=graph,
                    ):
                        rejected = action(
                            logical,
                            "retention",
                            "add",
                            "--id",
                            "kept",
                            "--target",
                            target,
                            *(("--dry-run",) if dry_run else ()),
                        )
                    self.assertIn("retention.graph.unavailable", rejected.stderr)
                    self.assertIn("multiple_producers", rejected.stderr)
                    self.assertEqual(retained_files(logical), before)

    def missing_producer_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        logical, entry, document = fixture(
            root, './pyrun scripts/build.py --input "<missing-producer>"'
        )
        (entry / "data/unproduced.csv").write_text("value\n7\n")
        checked(sync(logical, "--add-origin", "missing-producer=data/unproduced.csv"))
        # Model an existing generated-input declaration whose producer is absent.
        data_path = entry / "data.json"
        data = json.loads(data_path.read_text())
        data["inputs"][0]["origin"] = False
        data_path.write_text(json.dumps(data))
        return logical, entry, document

    def test_unrelated_missing_producer_does_not_block_retention_update(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = self.missing_producer_fixture(Path(directory))
            for name in ("old.py", "new.py"):
                (entry / "scripts" / name).write_text("# Unused verification script\n")
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "scripts/old.py",
                )
            )
            before = retained_files(logical)
            arguments = (
                "--id",
                "kept",
                "--remove-target",
                "scripts/old.py",
                "--add-target",
                "scripts/new.py",
            )
            checked(action(logical, "retention", "update", *arguments, "--dry-run"))
            self.assertEqual(retained_files(logical), before)
            checked(action(logical, "retention", "update", *arguments))
            records = checked(action(logical, "retention", "list"))["records"]
            self.assertEqual(records[0]["targets"], ["scripts/new.py"])
            for path, content in before.items():
                if path.name != "retention.json":
                    self.assertEqual(path.read_bytes(), content)

    def test_connected_script_is_rejected_despite_unrelated_missing_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, _, _ = self.missing_producer_fixture(Path(directory))
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "kept",
                "--target",
                "scripts/build.py",
            )
            self.assertIn("retention.target.connected", rejected.stderr)

    def test_missing_producer_for_selected_target_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, _, _ = self.missing_producer_fixture(Path(directory))
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "kept",
                "--target",
                "data/unproduced.csv",
            )
            self.assertIn("retention.graph.unavailable", rejected.stderr)
            self.assertIn("no_producer", rejected.stderr)
            self.assertIn("unproduced.csv", rejected.stderr)

    def bundle_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        logical, entry, document = fixture(
            root,
            "./pyrun --exclusive --cid build -- scripts/build.py "
            '--output-directory "<bundle>"',
        )
        (entry / "scripts/build.py").write_text(
            "import argparse\nfrom pathlib import Path\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--output-directory')\na = p.parse_args()\n"
            "root = Path(a.output_directory)\nroot.mkdir(exist_ok=True)\n"
            "(root / 'value.csv').write_text('value\\n7\\n')\n"
            "(root / 'side.txt').write_text('debug')\n"
        )
        checked(sync(logical, "--add-generated-directory", "bundle=data/bundle"))
        (entry / "pyrun").symlink_to(SCRIPTS / "pyrun")
        ran = run_pyrun_process(
            entry,
            "--exclusive",
            "--cid",
            "build",
            "--",
            "scripts/build.py",
            "--output-directory",
            "<bundle>",
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return logical, entry, document

    def assert_not_orphan(self, logical: Path, target: Path) -> None:
        checked(
            run_log(
                logical.parent,
                "validate",
                "run",
                "--path",
                str(logical),
                "--format",
                "json",
            )
        )
        findings = checked(
            run_log(
                logical.parent,
                "validate",
                "list",
                "findings",
                "--path",
                str(logical),
                "--format",
                "json",
            )
        )["items"]
        self.assertFalse(
            any(item["subject"] == target.resolve().as_posix() for item in findings),
            findings,
        )
        self.assertFalse(
            any(item["code"] == "retention.declaration.invalid" for item in findings),
            findings,
        )

    def test_unused_recorded_output_can_be_retained_without_removing_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory), './pyrun scripts/build.py --output "<generated>"'
            )
            target = entry / "data/output.csv"
            target.write_text("value\n7\n")
            checked(sync(logical, "--add-generated", "generated=data/output.csv"))
            before = retained_files(logical)
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/output.csv",
                    "--dry-run",
                )
            )
            self.assertEqual(retained_files(logical), before)
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/output.csv",
                )
            )
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
            self.assert_not_orphan(logical, target)

    def test_unreached_command_input_can_be_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = fixture(
                Path(directory), './pyrun scripts/build.py --input "<source>"'
            )
            target = entry / "data/source.csv"
            target.write_text("value\n7\n")
            checked(sync(logical, "--add-origin", "source=data/source.csv"))
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/source.csv",
                )
            )
            self.assert_not_orphan(logical, target)

    def test_unsynced_authored_evidence_still_blocks_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), './pyrun scripts/build.py --output "<generated>"'
            )
            (entry / "data/output.csv").write_text("value\n7\n")
            checked(sync(logical, "--add-generated", "generated=data/output.csv"))
            set_results(
                document, "`7`<!-- eid:value source=generated select=/value -->"
            )
            before = retained_files(logical)
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "kept",
                "--target",
                "data/output.csv",
            )
            self.assertIn("retention.target.connected", rejected.stderr)
            self.assertEqual(retained_files(logical), before)

    def test_evidence_chain_blocks_inputs_but_not_unconsumed_output_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory),
                './pyrun scripts/build.py --input "<source>" '
                '--output "<generated>" --output-side "<side>"',
            )
            (entry / "data/source.csv").write_text("value\n7\n")
            (entry / "scripts/build.py").write_text(
                "import argparse\nfrom pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--input')\np.add_argument('--output')\n"
                "p.add_argument('--output-side')\na = p.parse_args()\n"
                "Path(a.output).write_text(Path(a.input).read_text())\n"
                "Path(a.output_side).write_text('debug')\n"
            )
            checked(
                sync(
                    logical,
                    "--add-origin",
                    "source=data/source.csv",
                    "--add-generated",
                    "generated=data/output.csv",
                    "--add-generated",
                    "side=data/side.txt",
                )
            )
            (entry / "pyrun").symlink_to(SCRIPTS / "pyrun")
            ran = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--",
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "<generated>",
                "--output-side",
                "<side>",
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            set_results(
                document, "`7`<!-- eid:value source=generated select=/value -->"
            )
            checked(evidence(logical, "sync", "--id", "value"))
            for target in ("data/source.csv", "data/output.csv"):
                rejected = action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    target,
                )
                self.assertIn("retention.target.connected", rejected.stderr)
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/side.txt",
                )
            )
            self.assert_not_orphan(logical, entry / "data/side.txt")

    def test_unconsumed_origin_directory_sibling_can_be_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            bundle = entry / "data/bundle"
            bundle.mkdir()
            (bundle / "value.csv").write_text("value\n7\n")
            side = bundle / "side.txt"
            side.write_text("debug")
            set_results(
                document, "`7`<!-- eid:value source=bundle/value.csv select=/value -->"
            )
            checked(
                evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin-directory",
                    "bundle=data/bundle",
                )
            )
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/bundle/side.txt",
                )
            )
            self.assert_not_orphan(logical, side)

    def test_unreached_atomic_bundle_can_be_retained_with_its_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, _ = self.bundle_fixture(Path(directory))
            checked(
                action(
                    logical,
                    "retention",
                    "add",
                    "--id",
                    "kept",
                    "--target",
                    "data/bundle",
                )
            )
            self.assert_not_orphan(logical, entry / "data/bundle")

    def test_reached_atomic_bundle_blocks_sibling_even_with_changed_code(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = self.bundle_fixture(Path(directory))
            set_results(
                document,
                "`7`<!-- eid:value source=bundle/value.csv select=/value -->",
            )
            checked(evidence(logical, "sync", "--id", "value"))
            script = entry / "scripts/build.py"
            script.write_text(script.read_text() + "\n# Changed source\n")
            before = retained_files(logical)
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "kept",
                "--target",
                "data/bundle/side.txt",
            )
            self.assertIn("retention.target.connected", rejected.stderr)
            self.assertEqual(retained_files(logical), before)

    def test_normalized_evidence_blocks_retention_until_its_owner_is_synced(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry, document = fixture(
                Path(directory), "./pyrun scripts/build.py"
            )
            (entry / "data/value.csv").write_text("value\n7\n")
            text = set_results(
                document, "`7`<!-- eid:value source=values select=/value -->"
            )
            checked(
                evidence(
                    logical,
                    "sync",
                    "--id",
                    "value",
                    "--add-origin",
                    "values=data/value.csv",
                )
            )
            document.write_text(
                text.replace(
                    "`7`<!-- eid:value source=values select=/value -->", "Removed."
                )
            )
            rejected = action(
                logical,
                "retention",
                "add",
                "--id",
                "kept",
                "--target",
                "data/value.csv",
            )
            self.assertIn("retention.target.connected", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
