from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from research_log_data import (  # noqa: E402
    build_local_input,
    data_file_from_inputs,
)
from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.commands")
GRAPH = importlib.import_module("validation.material_graph")
RETENTION = importlib.import_module("validation.retention")


def _surface(root: Path) -> tuple[Path, Path, object, object]:
    log_root = root / "docs" / "log"
    entry_root = log_root / "entries" / "entry"
    write(entry_root / "e001.md", "# Entry\n")
    write(entry_root / "scripts" / "build.py", "# fixture\n")
    write(entry_root / "data" / "source.csv", "value\n1\n")
    write(entry_root / "data" / "reached.csv", "value\n1\n")
    write(entry_root / "data" / "sibling.csv", "value\n2\n")
    source = build_local_input(
        "source",
        "file",
        "data/source.csv",
        entry_root=entry_root,
        origin=True,
    )
    data_file = data_file_from_inputs(
        entry_root / "data.json", entry_root=entry_root, inputs=(source,)
    )
    context = COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/e001.md",
        entry_root=entry_root,
        log_root=log_root,
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )
    invocations = COMMAND.discover_commands(
        """```bash
./pyrun scripts/build.py --input-data '<source>' \
  --output-data data/reached.csv --output-data data/sibling.csv
```
""",
        context,
    ).invocations
    return log_root, entry_root, data_file, invocations


def _bundle_surface(root: Path) -> tuple[Path, Path, object, object]:
    log_root = root / "docs" / "log"
    entry_root = log_root / "entries" / "entry"
    write(entry_root / "e001.md", "# Entry\n")
    write(entry_root / "scripts" / "build.py", "# fixture\n")
    write(entry_root / "data" / "source.csv", "value\n1\n")
    write(entry_root / "data" / "bundle" / "model.pt", "model\n")
    write(entry_root / "data" / "bundle" / "metrics.csv", "value\n2\n")
    source = build_local_input(
        "source",
        "file",
        "data/source.csv",
        entry_root=entry_root,
        origin=True,
    )
    bundle = build_local_input(
        "bundle",
        "directory",
        "data/bundle",
        entry_root=entry_root,
        origin=False,
    )
    data_file = data_file_from_inputs(
        entry_root / "data.json",
        entry_root=entry_root,
        inputs=(source, bundle),
    )
    context = COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/e001.md",
        entry_root=entry_root,
        log_root=log_root,
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )
    invocations = COMMAND.discover_commands(
        """```bash
./pyrun scripts/build.py --input-data '<source>' --output-dir data/bundle
```
""",
        context,
    ).invocations
    return log_root, entry_root, data_file, invocations


def _bundle_consumer_surface(
    root: Path, input_token: str
) -> tuple[Path, object, object, str]:
    log_root, entry_root, data_file, _ = _bundle_surface(root)
    write(entry_root / "scripts/use.py", "# fixture\n")
    final = entry_root / "data/final.csv"
    write(final, "value\n3\n")
    context = COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/e001.md",
        entry_root=entry_root,
        log_root=log_root,
        project_root=root,
        data_file=data_file,
        require_experimental_context=False,
    )
    input_option = (
        "--input-directory" if input_token == "<bundle>" else "--input-data"
    )
    invocations = COMMAND.discover_commands(
        f"""```bash
./pyrun scripts/build.py --input-data '<source>' --output-dir data/bundle
./pyrun scripts/use.py {input_option} '{input_token}' --output-data data/final.csv
```
""",
        context,
    ).invocations
    return entry_root, data_file, invocations, final.resolve().as_posix()


def _request(
    entry_root: Path,
    data_file: object,
    invocations: object,
    *,
    evidence: tuple[object, ...] = (),
    retention_files: tuple[object, ...] = (),
) -> object:
    return GRAPH.MaterialGraphRequest(
        entry_roots={"e001": entry_root},
        evidence=evidence,
        invocations=invocations,
        retention_files=retention_files,
        input_registries=(GRAPH.InputRegistrySurface("entries/entry", data_file),),
    )


class MaterialGraphTests(unittest.TestCase):
    def test_whole_bundle_consumer_connects_members_for_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry_root, data_file, invocations, final = _bundle_consumer_surface(
                Path(directory), "<bundle>"
            )
            bundle = (entry_root / "data/bundle").resolve().as_posix()
            members = {
                (entry_root / "data/bundle/model.pt").resolve().as_posix(),
                (entry_root / "data/bundle/metrics.csv").resolve().as_posix(),
            }

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001", "final", "e001.md:eid:final", (final,)
                        ),
                    ),
                )
            )

            self.assertTrue(members.issubset(result.orphan.connected))
            input_materials = {
                edge.source.identity
                for edge in result.edges
                if edge.kind == "input"
            }
            self.assertTrue(members.issubset(input_materials))
            self.assertNotIn(bundle, input_materials)

    def test_exact_bundle_member_consumer_keeps_input_edge_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry_root, data_file, invocations, final = _bundle_consumer_surface(
                Path(directory), "<bundle>/model.pt"
            )
            model = (entry_root / "data/bundle/model.pt").resolve().as_posix()
            metrics = (entry_root / "data/bundle/metrics.csv").resolve().as_posix()

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001", "final", "e001.md:eid:final", (final,)
                        ),
                    ),
                )
            )

            input_materials = {
                edge.source.identity
                for edge in result.edges
                if edge.kind == "input"
            }
            self.assertIn(model, input_materials)
            self.assertNotIn(metrics, input_materials)
            self.assertIn(metrics, result.orphan.connected)

    def test_selected_bundle_member_connects_atomic_ownership_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _bundle_surface(Path(directory))
            model = (entry_root / "data/bundle/model.pt").resolve().as_posix()
            metrics = (entry_root / "data/bundle/metrics.csv").resolve().as_posix()
            bundle = (entry_root / "data/bundle").resolve().as_posix()

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001",
                            "model",
                            "e001.md:eid:model",
                            (model,),
                            input_names=("entries/entry:bundle",),
                        ),
                    ),
                )
            )

            self.assertIn(model, result.orphan.connected)
            self.assertIn(metrics, result.orphan.connected)
            evidence_sources = {
                edge.target.identity
                for edge in result.edges
                if edge.kind == "evidence-source"
            }
            self.assertEqual(evidence_sources, {model})
            self.assertIn(
                ("membership", model, bundle),
                {
                    (edge.kind, edge.source.identity, edge.target.identity)
                    for edge in result.edges
                },
            )

    def test_unreached_bundle_is_one_root_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _bundle_surface(Path(directory))
            bundle = (entry_root / "data/bundle").resolve().as_posix()
            members = {
                (entry_root / "data/bundle/model.pt").resolve().as_posix(),
                (entry_root / "data/bundle/metrics.csv").resolve().as_posix(),
            }

            result = GRAPH.compose_material_graph(
                _request(entry_root, data_file, invocations)
            )

            self.assertIn(bundle, result.orphan.orphaned)
            self.assertTrue(members.isdisjoint(result.orphan.orphaned))

    def test_reached_bundle_makes_member_retention_redundant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _bundle_surface(Path(directory))
            model = (entry_root / "data/bundle/model.pt").resolve().as_posix()
            write(
                entry_root / "retention.json",
                json.dumps(
                    {
                        "schema": "research-log-retention/v1",
                        "records": [
                            {
                                "id": "metrics",
                                "paths": ["data/bundle/metrics.csv"],
                            }
                        ],
                    }
                ),
            )
            retained = RETENTION.load_retention_file(
                entry_root / "retention.json", entry_root=entry_root
            )

            with self.assertRaisesRegex(
                GRAPH.MaterialGraphV2Error, "retention.declaration.invalid"
            ):
                GRAPH.compose_material_graph(
                    _request(
                        entry_root,
                        data_file,
                        invocations,
                        evidence=(
                            GRAPH.EvidenceConnection(
                                "e001",
                                "model",
                                "e001.md:eid:model",
                                (model,),
                                input_names=("entries/entry:bundle",),
                            ),
                        ),
                        retention_files=(retained,),
                    )
                )

    def test_retaining_unreached_bundle_suppresses_root_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _bundle_surface(Path(directory))
            bundle = (entry_root / "data/bundle").resolve().as_posix()
            write(
                entry_root / "retention.json",
                json.dumps(
                    {
                        "schema": "research-log-retention/v1",
                        "records": [
                            {
                                "directory": "data/bundle",
                                "id": "bundle",
                                "membership": "all-descendants",
                            }
                        ],
                    }
                ),
            )
            retained = RETENTION.load_retention_file(
                entry_root / "retention.json", entry_root=entry_root
            )

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    retention_files=(retained,),
                )
            )

            self.assertNotIn(bundle, result.orphan.orphaned)
            self.assertTrue(
                all(
                    not path.startswith(bundle + "/")
                    for path in result.orphan.orphaned
                )
            )

    def test_repeated_bundle_members_share_one_root_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _bundle_surface(Path(directory))
            model = (entry_root / "data/bundle/model.pt").resolve().as_posix()
            metrics = (entry_root / "data/bundle/metrics.csv").resolve().as_posix()
            bundle = (entry_root / "data/bundle").resolve().as_posix()

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001", "model", "e001.md:eid:model", (model,)
                        ),
                        GRAPH.EvidenceConnection(
                            "e001", "metrics", "e001.md:eid:metrics", (metrics,)
                        ),
                    ),
                )
            )

            self.assertEqual(
                sum(
                    node.kind == "material" and node.identity == bundle
                    for node in result.nodes
                ),
                1,
            )
            self.assertEqual(
                {
                    edge.source.identity
                    for edge in result.edges
                    if edge.kind == "membership" and edge.target.identity == bundle
                },
                {model, metrics},
            )

    def test_evidence_closure_connects_exact_output_not_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            reached = (entry_root / "data" / "reached.csv").resolve().as_posix()
            sibling = (entry_root / "data" / "sibling.csv").resolve().as_posix()
            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001", "result", "e001.md:eid:result", (reached,)
                        ),
                    ),
                )
            )

            self.assertIn(reached, result.orphan.connected)
            self.assertIn(
                (entry_root / "data" / "source.csv").resolve().as_posix(),
                result.orphan.connected,
            )
            self.assertIn(sibling, result.orphan.orphaned)
            self.assertNotIn(sibling, result.orphan.connected)
            self.assertFalse(result.orphan.unused_input_names)

    def test_depth_overflow_fails_instead_of_truncating_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            reached = (entry_root / "data" / "reached.csv").resolve().as_posix()

            with (
                mock.patch.object(GRAPH, "MAX_GRAPH_DEPTH", -1),
                self.assertRaisesRegex(
                    GRAPH.MaterialGraphV2Error, "provenance.resource.too_large"
                ),
            ):
                GRAPH.compose_material_graph(
                    _request(
                        entry_root,
                        data_file,
                        invocations,
                        evidence=(
                            GRAPH.EvidenceConnection(
                                "e001", "result", "e001.md:eid:result", (reached,)
                            ),
                        ),
                    )
                )

    def test_unreached_command_connects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            result = GRAPH.compose_material_graph(
                _request(entry_root, data_file, invocations)
            )

            for relative in (
                "scripts/build.py",
                "data/source.csv",
                "data/reached.csv",
                "data/sibling.csv",
            ):
                self.assertIn(
                    (entry_root / relative).resolve().as_posix(),
                    result.orphan.orphaned,
                )
            self.assertFalse(result.orphan.connected)

    def test_unused_input_is_separate_from_artifact_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, _ = _surface(Path(directory))
            result = GRAPH.compose_material_graph(_request(entry_root, data_file, ()))

            self.assertEqual(
                result.orphan.unused_input_names, ("entries/entry:source",)
            )
            self.assertGreater(len(result.orphan.orphaned), 0)

    def test_evidence_only_input_is_not_reported_as_unused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, _ = _surface(Path(directory))
            source = (entry_root / "data/source.csv").resolve().as_posix()

            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    (),
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001",
                            "result",
                            "e001.md:eid:result",
                            (source,),
                            input_names=("entries/entry:source",),
                        ),
                    ),
                )
            )

            self.assertFalse(result.orphan.unused_input_names)

    def test_retention_is_separate_and_cannot_cover_connected_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            write(
                entry_root / "retention.json",
                json.dumps(
                    {
                        "schema": "research-log-retention/v1",
                        "records": [{"id": "sibling", "paths": ["data/sibling.csv"]}],
                    }
                ),
            )
            retained = RETENTION.load_retention_file(
                entry_root / "retention.json", entry_root=entry_root
            )
            reached = (entry_root / "data" / "reached.csv").resolve().as_posix()
            result = GRAPH.compose_material_graph(
                _request(
                    entry_root,
                    data_file,
                    invocations,
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001", "result", "e001.md:eid:result", (reached,)
                        ),
                    ),
                    retention_files=(retained,),
                )
            )
            self.assertEqual(
                result.orphan.declared_retained,
                ((entry_root / "data" / "sibling.csv").resolve().as_posix(),),
            )

            write(
                entry_root / "retention.json",
                json.dumps(
                    {
                        "schema": "research-log-retention/v1",
                        "records": [{"id": "reached", "paths": ["data/reached.csv"]}],
                    }
                ),
            )
            redundant = RETENTION.load_retention_file(
                entry_root / "retention.json", entry_root=entry_root
            )
            with self.assertRaisesRegex(
                GRAPH.MaterialGraphV2Error, "retention.declaration.invalid"
            ):
                GRAPH.compose_material_graph(
                    _request(
                        entry_root,
                        data_file,
                        invocations,
                        evidence=(
                            GRAPH.EvidenceConnection(
                                "e001", "result", "e001.md:eid:result", (reached,)
                            ),
                        ),
                        retention_files=(redundant,),
                    )
                )

    def test_runtime_cache_and_temporary_descendants_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            ignored = (
                entry_root / ".mypy_cache" / "state.json",
                entry_root / ".pytest_cache" / "nodeids",
                entry_root / ".ruff_cache" / "cache-entry",
                entry_root / "scripts" / "__pycache__" / "build.pyc",
                entry_root / "tmp" / "scratch.csv",
            )
            for path in ignored:
                write(path, "cache\n")
            result = GRAPH.compose_material_graph(
                _request(entry_root, data_file, invocations)
            )
            for path in ignored:
                self.assertNotIn(path.resolve().as_posix(), result.orphan.inventory)

    def test_recognized_pyrun_output_backups_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            ignored = (
                entry_root / "pyrun-outputs.json.bak",
                entry_root / "pyrun-outputs.json.2.bak",
                entry_root / "pyrun-outputs.json.25.bak",
            )
            for path in ignored:
                write(path, "malformed recovery bytes\n")
            ordinary = entry_root / "pyrun-outputs.json.1.bak"
            write(ordinary, "ordinary material\n")
            nested = entry_root / "data/pyrun-outputs.json.bak"
            write(nested, "ordinary nested material\n")

            result = GRAPH.compose_material_graph(
                _request(entry_root, data_file, invocations)
            )

            for path in ignored:
                self.assertNotIn(path.resolve().as_posix(), result.orphan.inventory)
            self.assertIn(ordinary.resolve().as_posix(), result.orphan.inventory)
            self.assertIn(nested.resolve().as_posix(), result.orphan.inventory)

    def test_nested_research_material_is_not_excluded_by_name_or_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, data_file, invocations = _surface(Path(directory))
            eligible = (
                entry_root / "data" / "report.md",
                entry_root / "data" / "validation" / "results.csv",
                entry_root / "data" / "tmp" / "results.csv",
            )
            for path in eligible:
                write(path, "research material\n")
            result = GRAPH.compose_material_graph(
                _request(entry_root, data_file, invocations)
            )
            for path in eligible:
                identity = path.resolve().as_posix()
                self.assertIn(identity, result.orphan.inventory)
                self.assertIn(identity, result.orphan.orphaned)


if __name__ == "__main__":
    unittest.main()
