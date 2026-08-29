from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")
PROVENANCE = importlib.import_module("validation.provenance")


def _context(root: Path, data_index: dict[str, str] | None = None) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    return COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="docs/log/entries/entry/e001.md",
        entry_root=entry_root,
        log_root=root / "docs" / "log",
        project_root=root,
        data_index=data_index or {},
        require_experimental_context=False,
    )


class ProvenanceV2LineageTests(unittest.TestCase):
    def test_exact_earlier_lineage_reaches_model_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "data" / "base.csv", "base\n")
            write(context.entry_root / "data" / "final.csv", "final\n")
            text = """```bash
tool --output-data data/base.csv
tool --input-data data/base.csv --output-data data/final.csv
```
<!-- command type = model -->
"""
            commands = COMMAND.discover_commands(text, context).invocations

            result = PROVENANCE.evaluate_provenance(
                context.entry_root / "data" / "final.csv", commands
            )

            self.assertEqual([root.kind for root in result.roots], ["model"])
            self.assertEqual(len(result.producers), 2)
            self.assertEqual(len(result.lineage), 1)
            self.assertTrue(result.dependency_projection)

    def test_named_external_input_is_trusted_terminal_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(
                Path(directory), {"catalog": "https://example.test/catalog.csv"}
            )
            write(context.entry_root / "data" / "final.csv", "final\n")
            text = """```bash
tool --dataset '<catalog>' --output-data data/final.csv
```
"""
            commands = COMMAND.discover_commands(text, context).invocations

            result = PROVENANCE.evaluate_provenance(
                context.entry_root / "data" / "final.csv", commands
            )

            self.assertEqual(
                [(root.kind, root.identity) for root in result.roots],
                [("external", "catalog")],
            )

    def test_multiple_model_and_simulation_roots_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            for name in ("model.csv", "simulation.csv", "final.csv"):
                write(context.entry_root / "data" / name, "value\n1\n")
            text = """```bash
tool --output-data data/model.csv
tool --output-data data/simulation.csv
tool --input-data data/model.csv --input-data data/simulation.csv \
  --output-data data/final.csv
```
<!-- command-1 type = model -->
<!-- command-2 type = simulation -->
"""
            commands = COMMAND.discover_commands(text, context).invocations

            result = PROVENANCE.evaluate_provenance(
                context.entry_root / "data" / "final.csv", commands
            )

            self.assertEqual(
                sorted(root.kind for root in result.roots), ["model", "simulation"]
            )

    def test_typed_command_does_not_hide_unrooted_visible_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "data" / "missing.csv", "input\n")
            write(context.entry_root / "data" / "final.csv", "final\n")
            text = """```bash
tool --input-data data/missing.csv --output-data data/final.csv
```
<!-- command type = simulation -->
"""
            commands = COMMAND.discover_commands(text, context).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "lineage.missing"
            ):
                PROVENANCE.evaluate_provenance(
                    context.entry_root / "data" / "final.csv", commands
                )

    def test_untyped_terminal_producer_is_not_an_accepted_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "data" / "final.csv", "final\n")
            commands = COMMAND.discover_commands(
                "```bash\ntool --output-data data/final.csv\n```\n", context
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "provenance.root.missing"
            ):
                PROVENANCE.evaluate_provenance(
                    context.entry_root / "data" / "final.csv", commands
                )

    def test_required_explicit_local_executable_must_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            write(context.entry_root / "data" / "final.csv", "final\n")
            commands = COMMAND.discover_commands(
                "```bash\n./missing-tool --output-data data/final.csv\n```\n"
                "<!-- command type = model -->\n",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error,
                "invocation.executable.unresolved",
            ):
                PROVENANCE.evaluate_provenance(
                    context.entry_root / "data" / "final.csv", commands
                )

    def test_missing_and_ambiguous_producers_remain_black_and_white(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "data" / "final.csv"
            write(target, "final\n")
            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "producer.missing"
            ):
                PROVENANCE.evaluate_provenance(target, ())

            text = """```bash
tool --output-data data/final.csv
tool --output-data data/final.csv
```
<!-- command-1 type = model -->
<!-- command-2 type = simulation -->
"""
            commands = COMMAND.discover_commands(text, context).invocations
            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "producer.ambiguous"
            ):
                PROVENANCE.evaluate_provenance(target, commands)


class ProvenanceV2CollectionTests(unittest.TestCase):
    def test_unclassified_directory_candidate_creates_no_collection_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "owned" / "x.txt"
            write(target, "x\n")
            commands = COMMAND.discover_commands(
                "```bash\ntool --results owned\n```\n", context
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error,
                "producer.missing",
            ):
                PROVENANCE.evaluate_provenance(target, commands)

    def test_unrelated_output_directory_conflict_does_not_reopen_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "data" / "final.csv"
            write(target, "final\n")
            write(context.entry_root / "unrelated" / "x.txt", "x\n")
            text = """```bash
tool --output-data data/final.csv
tool --target unrelated
tool --output-data unrelated/x.txt
```
<!-- command-1 type = model -->
<!-- command-2 type = model; target = output-directory -->
<!-- command-3 type = model -->
"""
            commands = COMMAND.discover_commands(text, context).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual([root.kind for root in result.roots], ["model"])

    def test_output_directory_must_have_one_exclusive_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "owned" / "x.txt"
            write(target, "x\n")
            text = """```bash
tool --target owned
tool --output-data owned/x.txt
```
<!-- command-1 type = model; target = output-directory -->
<!-- command-2 type = model -->
"""
            commands = COMMAND.discover_commands(text, context).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error,
                "collection.output_directory.shared",
            ):
                PROVENANCE.evaluate_provenance(target, commands)


if __name__ == "__main__":
    unittest.main()
