from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from research_log_validation_test_support import unittest, write

COMMAND = importlib.import_module("validation.command_v2")
EVIDENCE = importlib.import_module("validation.evidence_v2")
GRAPH = importlib.import_module("validation.material_graph_v2")


def _fixture(root: Path) -> tuple[Path, Path, object, object]:
    log_root = root / "docs" / "log"
    entry_root = log_root / "entries" / "entry"
    write(entry_root / "entry.md", "# Entry\n")
    write(entry_root / "scripts" / "model.py", "# fixture\n")
    write(entry_root / "data" / "source.csv", "value\n1\n")
    write(entry_root / "data" / "debug.json", "{}\n")
    write(entry_root / "data" / "orphan.txt", "unused\n")
    write(
        entry_root / "data.csv",
        "name,type,location\ncatalog,csv,https://example.test/catalog.csv\nunused,csv,https://example.test/unused.csv\n",
    )
    write(
        entry_root / "evidence.json",
        """{
  "schema": "research-log-evidence/v2",
  "records": [
    {
      "id": "value",
      "document": "entries/entry/entry.md",
      "kind": "statistic",
      "sources": [{"source": "data/source.csv", "locator": {"select": [["value"]]}}],
      "transformation": null
    },
    {
      "id": "debug",
      "kind": "retention",
      "paths": ["data/debug.json"],
      "reason": "Useful context for later semantic review."
    }
  ]
}
""",
    )
    context = COMMAND.CommandContext(
        log_id="docs/log",
        entry="e001",
        document="entries/entry/entry.md",
        entry_root=entry_root,
        log_root=log_root,
        project_root=root,
        data_index={
            "catalog": "https://example.test/catalog.csv",
            "unused": "https://example.test/unused.csv",
        },
        require_experimental_context=False,
    )
    commands = COMMAND.discover_commands(
        """```bash
./pyrun scripts/model.py --catalog '<catalog>' --output-data data/source.csv
```
<!-- command type = model -->
""",
        context,
    ).invocations
    evidence_file = EVIDENCE.load_evidence_file(
        entry_root / "evidence.json", log_root=log_root, entry_root=entry_root
    )
    return log_root, entry_root, commands, evidence_file


class MaterialGraphV2Tests(unittest.TestCase):
    def test_graph_and_hygiene_are_composed_from_successful_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, commands, evidence_file = _fixture(Path(directory))

            result = GRAPH.compose_material_graph(
                GRAPH.MaterialGraphRequest(
                    entry_roots={"e001": entry_root},
                    evidence=(
                        GRAPH.EvidenceConnection(
                            "e001",
                            "value",
                            "entry.md:eid:value",
                            ((entry_root / "data" / "source.csv").as_posix(),),
                            ("locator-dependency", "presentation-dependency"),
                        ),
                    ),
                    direct_artifacts=(),
                    invocations=commands,
                    evidence_files=(evidence_file,),
                    data_indexes=(
                        GRAPH.DataIndexSurface("e001", ("catalog", "unused")),
                    ),
                )
            )

            self.assertIn(
                (entry_root / "data" / "source.csv").resolve().as_posix(),
                result.hygiene.connected,
            )
            self.assertEqual(
                result.hygiene.declared_retained,
                ((entry_root / "data" / "debug.json").resolve().as_posix(),),
            )
            self.assertEqual(
                result.hygiene.orphaned,
                ((entry_root / "data" / "orphan.txt").resolve().as_posix(),),
            )
            self.assertEqual(result.hygiene.unused_data_names, ("e001:unused",))
            self.assertTrue(result.dependency_projection)

    def test_retention_cannot_overlap_or_hide_connected_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, commands, evidence_file = _fixture(Path(directory))
            path = entry_root / "evidence.json"
            write(
                path,
                path.read_text(encoding="utf-8").replace(
                    '"paths": ["data/debug.json"]',
                    '"paths": ["data/source.csv"]',
                ),
            )
            evidence_file = EVIDENCE.load_evidence_file(
                path, log_root=log_root, entry_root=entry_root
            )

            with self.assertRaisesRegex(
                GRAPH.MaterialGraphV2Error, "retention.declaration.invalid"
            ):
                GRAPH.compose_material_graph(
                    GRAPH.MaterialGraphRequest(
                        entry_roots={"e001": entry_root},
                        evidence=(
                            GRAPH.EvidenceConnection(
                                "e001",
                                "value",
                                "entry.md:eid:value",
                                ((entry_root / "data" / "source.csv").as_posix(),),
                            ),
                        ),
                        direct_artifacts=(),
                        invocations=commands,
                        evidence_files=(evidence_file,),
                    )
                )

    def test_retention_reason_has_no_currentness_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root, entry_root, commands, evidence_file = _fixture(Path(directory))

            first = GRAPH.compose_material_graph(
                GRAPH.MaterialGraphRequest(
                    entry_roots={"e001": entry_root},
                    evidence=(),
                    direct_artifacts=(),
                    invocations=commands,
                    evidence_files=(evidence_file,),
                )
            )
            path = entry_root / "evidence.json"
            write(
                path,
                path.read_text(encoding="utf-8").replace(
                    "Useful context for later semantic review.", "Different prose."
                ),
            )
            changed = EVIDENCE.load_evidence_file(
                path, log_root=log_root, entry_root=entry_root
            )
            second = GRAPH.compose_material_graph(
                GRAPH.MaterialGraphRequest(
                    entry_roots={"e001": entry_root},
                    evidence=(),
                    direct_artifacts=(),
                    invocations=commands,
                    evidence_files=(changed,),
                )
            )

            self.assertEqual(first.dependency_projection, second.dependency_projection)

    def test_unrelated_material_content_does_not_reopen_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry_root, commands, evidence_file = _fixture(Path(directory))
            first = GRAPH.compose_material_graph(
                GRAPH.MaterialGraphRequest(
                    entry_roots={"e001": entry_root},
                    evidence=(),
                    direct_artifacts=(),
                    invocations=commands,
                    evidence_files=(evidence_file,),
                )
            )
            write(entry_root / "data" / "orphan.txt", "changed bytes\n")
            second = GRAPH.compose_material_graph(
                GRAPH.MaterialGraphRequest(
                    entry_roots={"e001": entry_root},
                    evidence=(),
                    direct_artifacts=(),
                    invocations=commands,
                    evidence_files=(evidence_file,),
                )
            )

            self.assertEqual(first.dependency_projection, second.dependency_projection)

    def test_external_and_locally_generated_classification_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry_root, _, evidence_file = _fixture(root)
            target = (entry_root / "data" / "source.csv").resolve().as_posix()
            context = COMMAND.CommandContext(
                log_id="docs/log",
                entry="e001",
                document="entries/entry/entry.md",
                entry_root=entry_root,
                log_root=root / "docs" / "log",
                project_root=root,
                data_index={"absolute": target},
                require_experimental_context=False,
            )
            commands = COMMAND.discover_commands(
                """```bash
tool --source '<absolute>'
tool --output-data data/source.csv
```
<!-- command-2 type = model -->
""",
                context,
            ).invocations

            with self.assertRaisesRegex(
                GRAPH.MaterialGraphV2Error, "material.direction.conflict"
            ):
                GRAPH.compose_material_graph(
                    GRAPH.MaterialGraphRequest(
                        entry_roots={"e001": entry_root},
                        evidence=(),
                        direct_artifacts=(),
                        invocations=commands,
                        evidence_files=(evidence_file,),
                    )
                )

    def test_cache_reuse_requires_exact_dependency(self) -> None:
        prior = (
            GRAPH.CacheEntry("a", "dep-a", {"status": "pass"}),
            GRAPH.CacheEntry("b", "old", {"status": "pass"}),
        )

        result = GRAPH.reuse_by_dependency(
            {"a": "dep-a", "b": "new", "c": "dep-c"}, prior
        )

        self.assertEqual(result.reused, {"a": {"status": "pass"}})
        self.assertEqual(result.reopened, ("b", "c"))


if __name__ == "__main__":
    unittest.main()
