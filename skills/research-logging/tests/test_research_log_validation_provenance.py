from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from research_log_data import (  # noqa: E402
    ExternalBoundary,
    build_local_input,
    data_file_from_inputs,
)
from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")
PROVENANCE = importlib.import_module("validation.provenance")


def _context(root: Path, inputs: tuple[object, ...] = ()) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    data_file = (
        data_file_from_inputs(
            entry_root / "data.json", entry_root=entry_root, inputs=inputs
        )
        if inputs
        else None
    )
    return COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/e001.md",
        entry_root=entry_root,
        log_root=root / "docs" / "log",
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )


class ProvenanceLineageTests(unittest.TestCase):
    def test_inputless_producer_is_a_successful_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "data" / "final.csv"
            write(target, "value\n1\n")
            commands = COMMAND.discover_commands(
                "```bash\ntool --output-data data/final.csv\n```\n", context
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 1)
            self.assertFalse(result.lineage)
            self.assertTrue(result.dependency_projection)

    def test_external_input_is_a_terminal_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_path = entry_root / "data/source.csv"
            target = entry_root / "data/final.csv"
            write(source_path, "value\n1\n")
            write(target, "value\n1\n")
            source = build_local_input(
                "source",
                "file",
                "data/source.csv",
                entry_root=entry_root,
                external=ExternalBoundary("fixture", "source/v1"),
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
tool --input-data '<source>' --output-data data/final.csv
```
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 1)
            self.assertFalse(result.lineage)

    def test_generated_input_traces_to_earlier_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            intermediate = entry_root / "data/intermediate.csv"
            target = entry_root / "data/final.csv"
            write(intermediate, "value\n1\n")
            write(target, "value\n1\n")
            generated = build_local_input(
                "generated",
                "file",
                "data/intermediate.csv",
                entry_root=entry_root,
            )
            context = _context(root, (generated,))
            commands = COMMAND.discover_commands(
                """```bash
tool --output-data data/intermediate.csv
tool --input-data '<generated>' --output-data data/final.csv
```
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 2)
            self.assertEqual(len(result.lineage), 1)

    def test_missing_boundary_fails_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_path = entry_root / "data/source.csv"
            target = entry_root / "data/final.csv"
            write(source_path, "value\n1\n")
            write(target, "value\n1\n")
            source = build_local_input(
                "source", "file", "data/source.csv", entry_root=entry_root
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                "```bash\n"
                "tool --input-data '<source>' --output-data data/final.csv\n"
                "```\n",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "lineage.missing"
            ):
                PROVENANCE.evaluate_provenance(target, commands)

    def test_external_boundary_conflicts_with_earlier_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_path = entry_root / "data/source.csv"
            target = entry_root / "data/final.csv"
            write(source_path, "value\n1\n")
            write(target, "value\n1\n")
            source = build_local_input(
                "source",
                "file",
                "data/source.csv",
                entry_root=entry_root,
                external=ExternalBoundary("fixture", "source/v1"),
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
tool --output-data data/source.csv
tool --input-data '<source>' --output-data data/final.csv
```
""",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "data.external.invalid"
            ):
                PROVENANCE.evaluate_provenance(target, commands)

    def test_missing_and_ambiguous_starting_producers_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "data/final.csv"
            write(target, "value\n1\n")
            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "producer.missing"
            ):
                PROVENANCE.evaluate_provenance(target, ())
            commands = COMMAND.discover_commands(
                """```bash
tool --output-data data/final.csv
tool --output-data data/final.csv
```
""",
                context,
            ).invocations
            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "producer.ambiguous"
            ):
                PROVENANCE.evaluate_provenance(target, commands)

    def test_generated_directory_requires_exact_directory_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            write(entry_root / "data/bundle/a.csv", "value\n1\n")
            target = entry_root / "data/final.csv"
            write(target, "value\n1\n")
            bundle = build_local_input(
                "bundle", "directory", "data/bundle", entry_root=entry_root
            )
            context = _context(root, (bundle,))
            commands = COMMAND.discover_commands(
                """```bash
tool --output-directory data/bundle
tool --input-directory '<bundle>' --output-data data/final.csv
```
<!-- command-1 output-directory = output-directory -->
<!-- command-2 input-directory = input-directory -->
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 2)
            self.assertEqual(len(result.lineage), 1)

    def test_external_directory_rejects_member_producer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            write(entry_root / "data/bundle/a.csv", "value\n1\n")
            target = entry_root / "data/final.csv"
            write(target, "value\n1\n")
            bundle = build_local_input(
                "bundle",
                "directory",
                "data/bundle",
                entry_root=entry_root,
                external=ExternalBoundary("fixture", "bundle/v1"),
            )
            context = _context(root, (bundle,))
            commands = COMMAND.discover_commands(
                """```bash
tool --output-data data/bundle/a.csv
tool --input-directory '<bundle>' --output-data data/final.csv
```
<!-- command-2 input-directory = input-directory -->
""",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "directory.external.conflict"
            ):
                PROVENANCE.evaluate_provenance(target, commands)


if __name__ == "__main__":
    unittest.main()
