from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validation.inventory import (  # noqa: E402
    collection_identity,
    file_identity,
    inventory_owned_material,
)

SCRIPT_SUFFIXES = {".jl", ".m", ".py", ".r", ".sh"}
EXCLUDED_NAMES = {
    "data.csv",
    "evidence.csv",
    "refs.bib",
    "validation-cache.json",
    "validation-record.json",
    "validation.md",
}


def write(path: Path, text: str = "value\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class OwnedMaterialInventoryTests(unittest.TestCase):
    def test_file_and_collection_identities_cover_selected_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "data" / "a.csv", "a\n")
            write(root / "data" / "b.csv", "bb\n")

            direct = file_identity(root / "data" / "a.csv")
            selected = collection_identity(root / "data", ["b.csv", "a.csv"])

            self.assertEqual(direct["size"], 2)
            self.assertEqual(selected["size"], 5)
            self.assertEqual(selected["members"], ["a.csv", "b.csv"])

    def test_inventory_includes_unmentioned_artifacts_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "entry"
            write(root / "e001.md", "# Entry\n")
            write(root / "data.csv")
            write(root / "evidence.csv")
            write(root / "data" / "unmentioned.csv")
            write(root / "images" / "figure.png")
            write(root / "scripts" / "run.py")

            inventory = inventory_owned_material(
                root,
                script_suffixes=SCRIPT_SUFFIXES,
                excluded_names=EXCLUDED_NAMES,
            )
            observed = {
                item.logical_path.relative_to(root).as_posix(): (
                    item.kind,
                    item.directory,
                )
                for item in inventory
            }

            self.assertEqual(
                observed,
                {
                    "data": ("artifact", True),
                    "data/unmentioned.csv": ("artifact", False),
                    "images": ("artifact", True),
                    "images/figure.png": ("artifact", False),
                    "scripts/run.py": ("script", False),
                },
            )

    def test_inventory_excludes_tool_state_and_the_pyrun_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "entry"
            write(root / ".ruff_cache" / "state")
            write(root / ".mypy_cache" / "state")
            write(root / ".pytest_cache" / "state")
            write(root / "__pycache__" / "module.pyc")
            write(root / "pyrun", "#!/bin/sh\n")
            write(root / "scripts" / "research.py")

            inventory = inventory_owned_material(
                root,
                script_suffixes=SCRIPT_SUFFIXES,
                excluded_names=EXCLUDED_NAMES,
            )

            self.assertEqual(
                [item.logical_path.relative_to(root).as_posix() for item in inventory],
                ["scripts/research.py"],
            )

    def test_directory_symlink_keeps_logical_identity_and_bounds_the_walk(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "entry"
            external = workspace / "external"
            write(external / "linked" / "value.csv")
            write(external / "outside.csv")
            root.mkdir()
            (root / "data").symlink_to(external / "linked", target_is_directory=True)

            inventory = inventory_owned_material(
                root,
                script_suffixes=SCRIPT_SUFFIXES,
                excluded_names=EXCLUDED_NAMES,
            )
            observed = {
                item.logical_path.relative_to(root).as_posix(): item
                for item in inventory
            }

            self.assertEqual(set(observed), {"data", "data/value.csv"})
            self.assertEqual(
                observed["data/value.csv"].resolved_path,
                (external / "linked" / "value.csv").resolve(),
            )

    def test_directory_symlink_cycle_terminates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "entry"
            write(root / "data" / "value.csv")
            (root / "data" / "loop").symlink_to(root / "data", target_is_directory=True)

            inventory = inventory_owned_material(
                root,
                script_suffixes=SCRIPT_SUFFIXES,
                excluded_names=EXCLUDED_NAMES,
            )

            self.assertEqual(
                [item.logical_path.relative_to(root).as_posix() for item in inventory],
                ["data", "data/loop", "data/value.csv"],
            )


if __name__ == "__main__":
    unittest.main()
