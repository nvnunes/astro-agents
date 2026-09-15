"""Directory ownership and argument-sized output migration checks."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from research_log_cli_test_support import run_log

# isort: split
# The CLI test helper adds the scripts directory before importing its modules.
from research_log_data import build_local_input
from research_log_validation_test_support import mechanical_log, write
from test_research_log_validation_material_graph import _bundle_surface, _request
from test_research_log_validation_provenance import _context, _invocation
from validation.commands import discover_commands
from validation.domain import CheckOutcome
from validation.engine import _record_raw_output_findings
from validation.material_graph import classify_research_graph_materials
from validation.output_support import resolve_output_support
from validation.provenance import ProvenanceV2Error, evaluate_complete_provenance
from validation.pyrun_outputs import empty_pyrun_outputs
from validation.pyrun_state import execution_id, recipe_from_invocation
from validation.read_model import batch_detail, list_batches


class DirectoryOwnershipTests(unittest.TestCase):
    def test_child_uses_earlier_parent_membership_and_parent_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry = root / "docs/log/entries/entry"
            member = entry / "data/models/child/model.csv"
            sibling = entry / "data/models/sibling/model.csv"
            target = entry / "data/final.csv"
            for path in (member, sibling, target):
                write(path, "value\n1\n")
            resources = tuple(
                build_local_input(
                    name, "directory", location, entry_root=entry, origin=False
                )
                for name, location in (
                    ("parent", "data/models"),
                    ("child", "data/models/child"),
                )
            )
            context = _context(root, resources)
            discovery = discover_commands(
                "```bash\n"
                './pyrun --cid parent -- scripts/build.py --output-dir "<parent>"\n'
                './pyrun --cid final -- scripts/final.py --input-dir "<child>" '
                "--output data/final.csv\n```",
                context,
            )
            self.assertFalse(discovery.failures)
            owner, consumer = discovery.invocations
            result = evaluate_complete_provenance(target, (owner, consumer))
            self.assertFalse(result.findings)
            self.assertIn(member.as_posix(), result.evaluated_materials)
            self.assertNotIn(sibling.as_posix(), result.evaluated_materials)

            seen = []

            def support(invocation, material):
                resolved = resolve_output_support(
                    invocation,
                    material,
                    entry_root=entry,
                    project_root=root,
                    support=empty_pyrun_outputs(entry),
                )
                seen.append((invocation.identity, resolved.path))
                if invocation.identity == owner.identity:
                    raise ProvenanceV2Error(
                        "provenance.output.reproduction_required",
                        material,
                        {},
                        "support",
                    )
                return {}

            unconfirmed = evaluate_complete_provenance(
                target,
                (owner, consumer),
                producer_validator=support,
            )
            self.assertEqual(
                {f.code for f in unconfirmed.findings},
                {"provenance.output.reproduction_required"},
            )
            self.assertIn((owner.identity, entry / "data/models"), seen)

            incomplete = replace(
                owner,
                outputs=tuple(
                    relation
                    for relation in owner.outputs
                    if relation.path != str(member)
                ),
            )
            late = replace(owner, sequence=consumer.sequence + 1)
            competitor = _invocation("competitor", 1, outputs=(str(member),))
            for label, invocations in (
                ("missing member", (incomplete, consumer)),
                ("later producer", (late, consumer)),
                (
                    "competing member writer",
                    (owner, competitor, replace(consumer, sequence=2)),
                ),
            ):
                with self.subTest(label=label):
                    result = evaluate_complete_provenance(target, invocations)
                    self.assertIn(
                        "directory.producer.conflict", {f.code for f in result.findings}
                    )


class OutputArgumentTests(unittest.TestCase):
    def test_overlapping_output_aliases_preserve_the_execution_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry = root / "docs/log/entries/entry"
            write(entry / "data/bundle/result.csv", "value\n1\n")
            resources = tuple(
                build_local_input(name, kind, path, entry_root=entry, origin=False)
                for name, kind, path in (
                    ("bundle", "directory", "data/bundle"),
                    ("result", "file", "data/bundle/result.csv"),
                )
            )
            context = _context(root, resources)
            arguments = (
                ('--output-dir "<bundle>"', "--output-dir data/bundle"),
                (
                    '--aggregate-output "<result>"',
                    "--aggregate-output data/bundle/result.csv",
                ),
                ('--output-copy="<result>"', "--output-copy=data/bundle/result.csv"),
            )
            for ordered in (arguments, tuple(reversed(arguments))):
                with self.subTest(arguments=ordered):
                    recipes = []
                    for index in (0, 1):
                        text = (
                            "```bash\n./pyrun --cid build -- scripts/build.py "
                            + " ".join(pair[index] for pair in ordered)
                            + "\n```"
                        )
                        discovered = discover_commands(text, context)
                        self.assertFalse(discovered.failures)
                        invocation = discovered.invocations[0]
                        self.assertEqual(len(invocation.outputs), 1)
                        recipes.append(
                            recipe_from_invocation(
                                invocation, entry_root=entry, project_root=root
                            )
                        )
                    self.assertEqual(recipes[0], recipes[1])
                    self.assertEqual(execution_id(recipes[0]), execution_id(recipes[1]))
                    self.assertEqual(
                        recipes[0].outputs, (("data/bundle", "directory"),)
                    )

    def test_output_declaration_is_used_without_connecting_an_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, entry, data, _ = _bundle_surface(root)
            discovery = discover_commands(
                "```bash\n"
                './pyrun --cid build -- scripts/build.py --input-data "<source>" '
                '--output-dir "<bundle>"\n```',
                _context(root, data.inputs),
            )
            self.assertFalse(discovery.failures)
            graph = classify_research_graph_materials(
                _request(entry, data, discovery.invocations)
            )
            self.assertFalse(graph.orphan.unused_input_names)
            self.assertIn(str(entry / "data/bundle"), graph.orphan.orphaned)

    def test_named_capture_preserves_recipe_and_requires_generated_declaration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry = root / "docs/log/entries/entry"
            write(entry / "data/run.txt", "output\n")
            capture = build_local_input(
                "capture", "file", "data/run.txt", entry_root=entry, origin=False
            )
            context = _context(root, (capture,))
            template = (
                "```bash\n./pyrun --cid build --capture-stdout {} -- "
                "scripts/build.py\n```"
            )
            raw = discover_commands(template.format("data/run.txt"), context)
            named = discover_commands(template.format('"<capture>"'), context)
            self.assertFalse(named.failures)
            self.assertEqual(
                raw.invocations[0].recipe_parameters,
                named.invocations[0].recipe_parameters,
            )
            self.assertEqual(named.invocations[0].outputs[0].named_input, "capture")
            rejected = discover_commands(
                template.format('"<capture>"'),
                _context(root, (replace(capture, origin=True),)),
            )
            self.assertEqual(
                rejected.failures[0].error.code, "data.output.declaration_invalid"
            )

    def test_directory_and_repeated_outputs_keep_argument_sized_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry = root / "docs/log/entries/entry"
            for path in (
                "data/bundle/a.csv",
                "data/bundle/b.csv",
                "data/one.csv",
                "data/two.csv",
            ):
                write(entry / path, "x\n1\n")
            bundle = build_local_input(
                "bundle", "directory", "data/bundle", entry_root=entry, origin=False
            )
            context = _context(root, (bundle,))
            for value, missing in (("data/bundle", 4), ('"<bundle>"', 3)):
                discovery = discover_commands(
                    "```bash\n./pyrun --cid build --capture-stdout data/run.txt -- "
                    "scripts/build.py "
                    f"--output-dir {value} --output-csv data/one.csv "
                    "--output-csv data/two.csv\n```",
                    context,
                )
                self.assertFalse(discovery.failures)
                state = SimpleNamespace(checks=[])
                _record_raw_output_findings(discovery.invocations[0], state)
                self.assertEqual(len(state.checks), 4)
                self.assertEqual(
                    sum(
                        c.outcome is CheckOutcome.FINDING for c in state.checks
                    ),
                    missing,
                )
                roots = [
                    c.dependency_evidence[0]["output_argument"]["path"]
                    for c in state.checks
                ]
                self.assertIn(str(entry / "data/bundle"), roots)
                self.assertNotIn(str(entry / "data/bundle/a.csv"), roots)

            files = tuple(
                build_local_input(
                    name, "file", f"data/{name}.csv", entry_root=entry, origin=False
                )
                for name in ("one", "two")
            )
            named = discover_commands(
                "```bash\n./pyrun --cid build --capture-stdout data/run.txt -- "
                "scripts/build.py "
                '--output-dir "<bundle>" --output-csv "<one>" '
                '--output-csv "<two>"\n```',
                _context(root, (bundle, *files)),
            )
            self.assertFalse(named.failures)
            self.assertEqual(
                discovery.invocations[0].collections,
                named.invocations[0].collections,
            )

    def test_output_group_inspection_survives_token_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            summary, entry = mechanical_log(root)
            named = entry.read_text()
            entry.write_text(named.replace("'<results>'", "data/results.csv"))
            published = run_log(
                root,
                "validate",
                "run",
                "--path",
                str(summary.with_suffix("")),
                "--format",
                "json",
            )
            self.assertEqual(
                published.returncode, 0, published.stderr + published.stdout
            )
            logical = summary.with_suffix("")
            batches = list_batches(logical)["items"]
            self.assertEqual(len(batches), 1)
            batch = batches[0]
            self.assertEqual(batch["finding_count"], 1)
            nodes = batch_detail(logical, batch["batch_id"], section="nodes", limit=100)
            self.assertTrue(
                any(
                    item["kind"] == "command"
                    and item["attributes"].get("cid") == "model"
                    for item in nodes["items"]
                )
            )
            identities = {item["identity"] for item in nodes["items"]}
            self.assertTrue(
                any("data/results.csv" in identity for identity in identities)
            )
