from __future__ import annotations

import importlib
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from research_log_data import (  # noqa: E402
    build_identity_pattern_directory,
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


def _unconfirmed_support(invocation: Any, material: str) -> Any:
    raise PROVENANCE.ProvenanceV2Error(
        "provenance.output.unconfirmed",
        material,
        {"producer": invocation.identity},
        "Pyrun Output Support Records",
    )


class ProvenanceLineageTests(unittest.TestCase):
    def test_cached_dependency_serialization_preserves_canonical_contract(self) -> None:
        producer = _invocation("shared", 0, outputs=("/tmp/result.csv",))
        index = PROVENANCE.build_producer_index((producer,))
        support = ({"nested": {"state": "confirmed"}, "output": "result.csv"},)
        finding = PROVENANCE.ProvenanceFinding(
            "lineage.missing",
            "/tmp/source.csv",
            {"consumer": "shared"},
            "Recorded-Command Provenance And Material Graph",
        )
        value = PROVENANCE._ProvenanceDependency(
            "/tmp/result.csv",
            ("shared",),
            (("upstream", "shared"),),
            support,
            (finding,),
        )
        expected = PROVENANCE.canonical_json(
            {
                "findings": [finding.as_dict()],
                "lineage": [["upstream", "shared"]],
                "material": "/tmp/result.csv",
                "producers": ["shared"],
                "producer_state": [PROVENANCE._invocation_dependency(producer)],
                "support": list(support),
                "version": "end-to-end-provenance-2",
            }
        )
        cache: dict[int, str] = {}

        actual = PROVENANCE._provenance_dependency_json(value, index, cache)

        self.assertEqual(actual, expected)
        self.assertEqual(len(cache), 2)
        self.assertEqual(
            PROVENANCE._provenance_dependency_json(value, index, cache), expected
        )

    def test_complete_traversals_reuse_origin_boundary_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_path = root / "external/source.csv"
            outputs = (entry_root / "data/first.csv", entry_root / "data/second.csv")
            for path in (source_path, *outputs):
                write(path, "value\n1\n")
            source = build_local_input(
                "source",
                "file",
                source_path.as_posix(),
                entry_root=entry_root,
                origin=True,
            )
            command_context = _context(root, (source,))
            invocations = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py --input-data '<source>' --output-data data/first.csv
./pyrun scripts/run.py --input-data '<source>' --output-data data/second.csv
```
""",
                command_context,
            ).invocations
            context = PROVENANCE.CompleteProvenanceContext(
                PROVENANCE.build_producer_index(invocations)
            )
            validator = PROVENANCE.require_origin_boundary

            with mock.patch.object(
                PROVENANCE, "require_origin_boundary", wraps=validator
            ) as validated:
                results = [
                    PROVENANCE.evaluate_complete_provenance(
                        output, invocations, context=context
                    )
                    for output in outputs
                ]

            self.assertEqual(validated.call_count, 1)
            self.assertTrue(all(not result.findings for result in results))

            context = PROVENANCE.CompleteProvenanceContext(
                PROVENANCE.build_producer_index(invocations)
            )
            failure = PROVENANCE.ProvenanceV2Error(
                "data.origin.invalid",
                "source",
                {"producer": "unexpected"},
                "Declared Origin Boundary",
            )
            with mock.patch.object(
                PROVENANCE, "require_origin_boundary", side_effect=failure
            ) as validated:
                results = [
                    PROVENANCE.evaluate_complete_provenance(
                        output, invocations, context=context
                    )
                    for output in outputs
                ]

            self.assertEqual(validated.call_count, 1)
            self.assertEqual(
                [[finding.code for finding in result.findings] for result in results],
                [["data.origin.invalid"], ["data.origin.invalid"]],
            )

    def test_complete_traversals_reuse_root_producer_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            outputs = (root / "first.csv", root / "second.csv")
            for path in (source, *outputs):
                write(path, "value\n1\n")
            upstream = _invocation(
                "upstream", 0, outputs=(source.resolve().as_posix(),)
            )
            producer = replace(
                _invocation(
                    "shared",
                    1,
                    outputs=tuple(path.resolve().as_posix() for path in outputs),
                ),
                inputs=(
                    COMMAND.MaterialRelationship(
                        source.resolve().as_posix(), "input", "option"
                    ),
                ),
            )
            invocations = (upstream, producer)
            context = PROVENANCE.CompleteProvenanceContext(
                PROVENANCE.build_producer_index(invocations),
                producer_validator=_unconfirmed_support,
            )
            walker = PROVENANCE._walk_invocation_uncached

            with mock.patch.object(
                PROVENANCE, "_walk_invocation_uncached", wraps=walker
            ) as walked:
                results = [
                    PROVENANCE.evaluate_complete_provenance(
                        output, invocations, context=context
                    )
                    for output in outputs
                ]

            calls_by_producer = [
                call.args[0].identity for call in walked.call_args_list
            ]
            self.assertEqual(calls_by_producer.count("shared"), 1)
            self.assertEqual(calls_by_producer.count("upstream"), 1)
            for output, result in zip(outputs, results, strict=True):
                self.assertEqual(result.material, output.resolve().as_posix())
                self.assertEqual(result.producers, ("shared", "upstream"))
                self.assertEqual(result.lineage, (("upstream", "shared"),))
                self.assertEqual(
                    [finding.subject for finding in result.findings],
                    [output.resolve().as_posix(), source.resolve().as_posix()],
                )

    def test_producer_index_reuses_exact_directory_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = (root / "bundle").resolve().as_posix()
            producer = _invocation("shared", 0, directories=(bundle,))
            index = PROVENANCE.build_producer_index((producer,))

            first = index.lookup(bundle, before_sequence=2)
            second = index.lookup(bundle, before_sequence=2)
            unbounded = index.lookup(bundle)

            self.assertIs(first, second)
            self.assertEqual(len(index.lookup_cache), 2)
            self.assertEqual(first, unbounded)
            dependency = PROVENANCE._invocation_dependency_cached(index, "shared")
            self.assertIs(
                dependency,
                PROVENANCE._invocation_dependency_cached(index, "shared"),
            )

    def test_complete_traversals_reuse_output_directory_conclusions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = (root / "first.csv", root / "second.csv")
            for output in outputs:
                write(output, "value\n1\n")
            producer = _invocation(
                "shared",
                0,
                outputs=tuple(output.resolve().as_posix() for output in outputs),
            )
            context = PROVENANCE.CompleteProvenanceContext(
                PROVENANCE.build_producer_index((producer,))
            )
            validator = PROVENANCE._validate_output_directories

            with mock.patch.object(
                PROVENANCE, "_validate_output_directories", wraps=validator
            ) as validated:
                for output in outputs:
                    result = PROVENANCE.evaluate_complete_provenance(
                        output,
                        (producer,),
                        context=context,
                    )
                    self.assertFalse(result.findings)

            self.assertEqual(validated.call_count, 1)

            context.output_directory_cache.clear()
            failure = PROVENANCE.ProvenanceV2Error(
                "collection.output_directory.shared",
                str(root),
                {"owners": ["shared"]},
                "Recorded-Command Provenance And Material Graph",
            )
            with mock.patch.object(
                PROVENANCE, "_validate_output_directories", side_effect=failure
            ) as validated:
                results = [
                    PROVENANCE.evaluate_complete_provenance(
                        output,
                        (producer,),
                        context=context,
                    )
                    for output in outputs
                ]

            self.assertEqual(validated.call_count, 1)
            self.assertEqual(
                [[finding.code for finding in result.findings] for result in results],
                [
                    ["collection.output_directory.shared"],
                    ["collection.output_directory.shared"],
                ],
            )

    def test_missing_output_does_not_hide_independent_lineage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            missing_input = root / "missing-input.csv"
            write(missing_input, "value\n1\n")
            final = replace(
                _invocation("final", 0, outputs=(target.resolve().as_posix(),)),
                inputs=(
                    COMMAND.MaterialRelationship(
                        missing_input.resolve().as_posix(), "input", "option"
                    ),
                ),
            )

            result = PROVENANCE.evaluate_complete_provenance(target, (final,))

            self.assertEqual(
                {finding.code for finding in result.findings},
                {"lineage.missing", "provenance.output.missing"},
            )

    def test_collected_findings_continue_across_independent_input_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            missing = root / "missing.csv"
            ambiguous = root / "ambiguous.csv"
            for path in (target, missing, ambiguous):
                write(path, "value\n1\n")
            first = _invocation(
                "first", 0, outputs=(ambiguous.resolve().as_posix(),)
            )
            second = _invocation(
                "second", 1, outputs=(ambiguous.resolve().as_posix(),)
            )
            final = replace(
                _invocation("final", 2, outputs=(target.resolve().as_posix(),)),
                inputs=(
                    COMMAND.MaterialRelationship(
                        missing.resolve().as_posix(), "input", "option"
                    ),
                    COMMAND.MaterialRelationship(
                        ambiguous.resolve().as_posix(), "input", "option"
                    ),
                ),
            )
            result = PROVENANCE.evaluate_complete_provenance(
                target,
                (first, second, final),
                producer_validator=_unconfirmed_support,
            )

            self.assertEqual(
                {finding.code for finding in result.findings},
                {
                    "lineage.ambiguous",
                    "lineage.missing",
                    "provenance.output.unconfirmed",
                },
            )

    def test_collected_findings_stop_a_cycle_edge_and_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.csv"
            intermediate = root / "intermediate.csv"
            independent = root / "independent.csv"
            for path in (target, intermediate, independent):
                write(path, "value\n1\n")
            earlier = replace(
                _invocation(
                    "cycle", 0, outputs=(intermediate.resolve().as_posix(),)
                ),
                inputs=(
                    COMMAND.MaterialRelationship(
                        target.resolve().as_posix(), "input", "option"
                    ),
                ),
            )
            final = replace(
                _invocation("cycle", 1, outputs=(target.resolve().as_posix(),)),
                inputs=(
                    COMMAND.MaterialRelationship(
                        intermediate.resolve().as_posix(), "input", "option"
                    ),
                    COMMAND.MaterialRelationship(
                        independent.resolve().as_posix(), "input", "option"
                    ),
                ),
            )

            result = PROVENANCE.evaluate_complete_provenance(
                target,
                (earlier, final),
                producer_validator=_unconfirmed_support,
            )

            self.assertEqual(
                {finding.code for finding in result.findings},
                {
                    "lineage.cycle",
                    "lineage.missing",
                    "provenance.output.unconfirmed",
                },
            )

    def test_collected_findings_report_directory_conflict_after_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "entry"
            target = entry_root / "data" / "target.csv"
            bundle = entry_root / "data" / "bundle"
            member = bundle / "member.csv"
            sibling = bundle / "sibling.csv"
            for path in (target, member, sibling):
                write(path, "value\n1\n")
            resource = build_local_input(
                "bundle", "directory", "data/bundle", entry_root=entry_root
            )
            owner = _invocation(
                "owner",
                0,
                outputs=(member.resolve().as_posix(),),
                directories=(bundle.resolve().as_posix(),),
            )
            conflict = _invocation(
                "conflict", 1, outputs=(sibling.resolve().as_posix(),)
            )
            final = replace(
                _invocation("final", 2, outputs=(target.resolve().as_posix(),)),
                inputs=(
                    COMMAND.MaterialRelationship(
                        member.resolve().as_posix(),
                        "input",
                        "directory",
                        target="bundle",
                        named_input="bundle",
                        input_resource=resource,
                    ),
                ),
            )

            result = PROVENANCE.evaluate_complete_provenance(
                target,
                (owner, conflict, final),
                producer_validator=_unconfirmed_support,
            )

            self.assertEqual(
                {finding.code for finding in result.findings},
                {
                    "directory.producer.conflict",
                    "provenance.output.unconfirmed",
                },
            )

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

    def test_external_origin_directory_is_a_whole_directory_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_root = root / "output/logs/other/e001/data/source"
            target = entry_root / "data/final.csv"
            write(source_root / "first.csv", "value\n1\n")
            write(source_root / "second.csv", "value\n2\n")
            write(target, "value\n1\n")
            source = build_local_input(
                "source",
                "directory",
                source_root.as_posix(),
                entry_root=entry_root,
                origin=True,
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py --input-directory '<source>' --output-data data/final.csv
```
<!-- command-1 input-directory = input-directory -->
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)
            expected_members = tuple(
                path.resolve().as_posix()
                for path in (source_root / "first.csv", source_root / "second.csv")
            )

            self.assertEqual(
                tuple(relationship.path for relationship in commands[0].inputs),
                expected_members,
            )
            self.assertTrue(
                all(
                    relationship.input_resource == source
                    and relationship.origin
                    and relationship.proof == "directory"
                    for relationship in commands[0].inputs
                )
            )
            self.assertEqual(len(commands[0].collections), 1)
            self.assertEqual(commands[0].collections[0].mechanism, "directory")
            self.assertEqual(commands[0].collections[0].members, expected_members)
            self.assertEqual(
                commands[0].collections[0].root,
                source_root.resolve().as_posix(),
            )
            self.assertEqual(len(result.producers), 1)
            self.assertFalse(result.lineage)

    def test_external_origin_directory_member_is_an_exact_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_root = root / "output/logs/other/e001/data/source"
            selected = source_root / "selected.csv"
            target = entry_root / "data/final.csv"
            write(selected, "value\n1\n")
            write(source_root / "sibling.csv", "value\n2\n")
            write(target, "value\n1\n")
            source = build_local_input(
                "source",
                "directory",
                source_root.as_posix(),
                entry_root=entry_root,
                origin=True,
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py --input-data '<source>/selected.csv' --output-data data/final.csv
```
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(
                tuple(relationship.path for relationship in commands[0].inputs),
                (selected.resolve().as_posix(),),
            )
            self.assertEqual(commands[0].inputs[0].input_resource, source)
            self.assertTrue(commands[0].inputs[0].origin)
            self.assertFalse(commands[0].collections)
            self.assertEqual(len(result.producers), 1)
            self.assertFalse(result.lineage)

    def test_managed_origin_aggregate_preserves_its_member_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry_root = root / "docs/log/entries/entry"
            source_root = root / "external/gnao-baseline/v1"
            selected = source_root / "build.log"
            target = entry_root / "data/final.csv"
            write(source_root / "build.h5", "state\n")
            write(selected, "complete\n")
            write(source_root / "maps-001.h5", "maps\n")
            write(source_root / "scratch.txt", "ignored\n")
            write(target, "value\n1\n")
            source = build_identity_pattern_directory(
                "baseline",
                source_root.as_posix(),
                ("build.h5", "build.log", "maps-*.h5"),
                entry_root=entry_root,
                origin=True,
            )
            context = _context(root, (source,))
            commands = COMMAND.discover_commands(
                """```bash
./pyrun scripts/run.py --input-data '<baseline>/build.log' --output-data data/final.csv
```
""",
                context,
            ).invocations

            result = PROVENANCE.evaluate_provenance(target, commands)

            self.assertEqual(
                tuple(relationship.path for relationship in commands[0].inputs),
                (selected.resolve().as_posix(),),
            )
            self.assertEqual(commands[0].inputs[0].input_resource, source)
            self.assertEqual(
                commands[0].inputs[0].input_resource.fingerprint.algorithm,
                "identity-patterns-sha256-v1",
            )
            self.assertTrue(commands[0].inputs[0].origin)
            self.assertFalse(commands[0].collections)
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
