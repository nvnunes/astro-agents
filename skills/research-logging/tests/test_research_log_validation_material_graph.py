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
        direct_artifacts=(),
        invocations=invocations,
        retention_files=retention_files,
        input_registries=(GRAPH.InputRegistrySurface("entries/entry", data_file),),
    )


class MaterialGraphTests(unittest.TestCase):
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
