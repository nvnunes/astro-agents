from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from research_log_data import (  # noqa: E402
    build_local_input,
    data_file_from_inputs,
)
from research_log_validation_test_support import mock, unittest, write

COMMAND = importlib.import_module("validation.commands")
PROVENANCE = importlib.import_module("validation.provenance")


def _context(root: Path, inputs: tuple[object, ...] = ()) -> object:
    entry_root = root / "docs" / "log" / "entries" / "entry"
    entry_root.mkdir(parents=True, exist_ok=True)
    write(entry_root / "scripts/run.py", "# fixture\n")
    write(entry_root / "scripts/build.py", "# fixture\n")
    write(entry_root / "scripts/final.py", "# fixture\n")
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


def _invocation(
    identity: str,
    sequence: int,
    *,
    outputs: tuple[str, ...] = (),
    directories: tuple[str, ...] = (),
) -> object:
    return COMMAND.Invocation(
        identity=identity,
        document="entry.md",
        entry="e001",
        fence=1,
        ordinal=sequence + 1,
        sequence=sequence,
        tokens=("./pyrun", "scripts/run.py"),
        executable="./pyrun",
        script_argument="scripts/run.py",
        parameters=(),
        script="scripts/run.py",
        script_identity="fixture",
        inputs=(),
        outputs=tuple(
            COMMAND.MaterialRelationship(path, "output", "option")
            for path in outputs
        ),
        collections=tuple(
            COMMAND.MaterialCollection("output", "directory", "output", (), path)
            for path in directories
        ),
        candidates=(),
        material_owner="entry",
    )


class ProvenanceLineageTests(unittest.TestCase):
    def test_producer_index_preserves_outputs_overlap_and_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin_root = root / "origin"
            origin_root.mkdir()
            member = (origin_root / "member.csv").as_posix()
            nested = (origin_root / "nested").as_posix()
            parent = root.as_posix()
            unrelated = (root / "unrelated").as_posix()
            invocations = (
                _invocation("member", 0, outputs=(member,)),
                _invocation("exact", 1, directories=(origin_root.as_posix(),)),
                _invocation("nested", 2, directories=(nested,)),
                _invocation("parent", 3, directories=(parent,)),
                _invocation("unrelated", 4, directories=(unrelated,)),
            )
            index = PROVENANCE.build_producer_index(invocations)

            self.assertEqual(index.outputs[member], (invocations[0],))
            self.assertEqual(index.by_identity["exact"], invocations[1])

            matches = index.lookup(origin_root.as_posix())
            self.assertEqual(
                [match.producer.identity for match in matches],
                ["member", "exact", "nested", "parent"],
            )
            by_identity = {match.producer.identity: match for match in matches}
            self.assertTrue(by_identity["member"].member_output)
            self.assertTrue(by_identity["exact"].exact_directory)
            self.assertTrue(by_identity["nested"].overlapping_directory)
            self.assertTrue(by_identity["parent"].overlapping_directory)
            self.assertEqual(
                by_identity["member"].confirmation_targets,
                (member,),
            )
            self.assertEqual(
                by_identity["exact"].confirmation_targets,
                (origin_root.as_posix(),),
            )
            self.assertEqual(by_identity["nested"].confirmation_targets, (nested,))
            self.assertEqual(by_identity["parent"].confirmation_targets, (parent,))
            self.assertEqual(
                [
                    match.producer.identity
                    for match in index.lookup(
                        origin_root.as_posix(), before_sequence=2
                    )
                ],
                ["member", "exact"],
            )

    def test_reached_output_directory_rejects_nested_competing_producer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            target = bundle / "model.pt"
            nested = bundle / "metrics"
            nested_member = nested / "result.csv"
            write(target, "model\n")
            write(nested_member, "value\n1\n")
            invocations = (
                _invocation(
                    "bundle",
                    0,
                    outputs=(
                        target.resolve().as_posix(),
                        nested_member.resolve().as_posix(),
                    ),
                    directories=(bundle.resolve().as_posix(),),
                ),
                _invocation(
                    "nested",
                    1,
                    outputs=(nested_member.resolve().as_posix(),),
                    directories=(nested.resolve().as_posix(),),
                ),
            )

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error,
                "collection.output_directory.shared",
            ):
                PROVENANCE.evaluate_provenance(target, invocations)

    def test_one_invocation_cannot_claim_nested_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            target = bundle / "model.pt"
            nested = bundle / "metrics"
            nested_member = nested / "result.csv"
            write(target, "model\n")
            write(nested_member, "value\n1\n")
            invocation = _invocation(
                "bundle",
                0,
                outputs=(
                    target.resolve().as_posix(),
                    nested_member.resolve().as_posix(),
                ),
                directories=(
                    bundle.resolve().as_posix(),
                    nested.resolve().as_posix(),
                ),
            )

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error,
                "collection.output_directory.shared",
            ):
                PROVENANCE.evaluate_provenance(target, (invocation,))

    def test_repeated_indexed_origin_queries_do_not_resolve_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "entry"
            origin_root = entry_root / "data" / "origin"
            write(origin_root / "member.csv", "value\n1\n")
            resource = build_local_input(
                "origin",
                "directory",
                "data/origin",
                entry_root=entry_root,
                origin=True,
            )
            invocation = _invocation(
                "member",
                0,
                outputs=(
                    (Path(resource.canonical_target) / "member.csv").as_posix(),
                ),
            )
            second = _invocation(
                "second",
                1,
                outputs=(
                    (Path(resource.canonical_target) / "second.csv").as_posix(),
                ),
            )
            invocations = (invocation, second)
            index = PROVENANCE.build_producer_index(invocations)

            with mock.patch.object(
                PROVENANCE.Path,
                "resolve",
                side_effect=AssertionError("indexed query resolved a path"),
            ):
                for _ in range(8):
                    PROVENANCE.require_origin_boundary(
                        origin_root,
                        resource,
                        invocations,
                        confirmed_record=lambda *_: False,
                        producer_index=index,
                    )
                    with self.assertRaisesRegex(
                        PROVENANCE.ProvenanceV2Error,
                        "directory.origin.conflict",
                    ) as caught:
                        PROVENANCE.require_origin_boundary(
                            origin_root,
                            resource,
                            invocations,
                            confirmed_record=lambda *_: True,
                            producer_index=index,
                        )
                    self.assertEqual(
                        caught.exception.observed["producers"],
                        ["member", "second"],
                    )

    def test_inputless_producer_is_a_successful_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _context(Path(directory))
            target = context.entry_root / "data" / "final.csv"
            write(target, "value\n1\n")
            commands = COMMAND.discover_commands(
                "```bash\n"
                "./pyrun scripts/run.py --output-data data/final.csv\n"
                "```\n",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 1)
            self.assertFalse(result.lineage)
            self.assertTrue(result.dependency_projection)

    def test_origin_input_is_a_terminal_boundary(self) -> None:
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
                origin=True,
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py --input-data '<source>' --output-data data/final.csv
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
./pyrun scripts/run.py --output-data data/intermediate.csv
./pyrun scripts/run.py --input-data '<generated>' --output-data data/final.csv
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
                "./pyrun scripts/run.py --input-data '<source>' "
                "--output-data data/final.csv\n"
                "```\n",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "lineage.missing"
            ):
                PROVENANCE.evaluate_provenance(target, commands)

    def test_origin_boundary_conflicts_with_confirmed_pyrun_producer(self) -> None:
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
                origin=True,
            )
            context = _context(root, (source,))
            write(entry_root / "scripts/build.py", "# fixture\n")
            write(entry_root / "scripts/final.py", "# fixture\n")
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/build.py --output-data data/source.csv
./pyrun scripts/final.py --input-data '<source>' --output-data data/final.csv
```
""",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "data.origin.invalid"
            ):
                PROVENANCE.evaluate_provenance(
                    target, commands, confirmed_record=lambda *_: True
                )

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
./pyrun scripts/run.py --output-data data/final.csv
./pyrun scripts/run.py --output-data data/final.csv
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
./pyrun scripts/run.py --output-directory data/bundle
./pyrun scripts/run.py --input-directory '<bundle>' --output-data data/final.csv
```
<!-- command-1 output-directory = output-directory -->
<!-- command-2 input-directory = input-directory -->
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(len(result.producers), 2)
            self.assertEqual(len(result.lineage), 1)

    def test_origin_directory_rejects_confirmed_member_producer(self) -> None:
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
                origin=True,
            )
            context = _context(root, (bundle,))
            write(entry_root / "scripts/build.py", "# fixture\n")
            write(entry_root / "scripts/final.py", "# fixture\n")
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/build.py --output-data data/bundle/a.csv
./pyrun scripts/final.py --input-directory '<bundle>' --output-data data/final.csv
```
<!-- command-2 input-directory = input-directory -->
""",
                context,
            ).invocations

            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "directory.origin.conflict"
            ):
                PROVENANCE.evaluate_provenance(
                    target, commands, confirmed_record=lambda *_: True
                )
            with self.assertRaisesRegex(
                PROVENANCE.ProvenanceV2Error, "directory.origin.conflict"
            ):
                PROVENANCE.require_origin_boundary(
                    entry_root / "data/bundle/a.csv",
                    bundle,
                    commands,
                    confirmed_record=lambda *_: True,
                )


if __name__ == "__main__":
    unittest.main()
