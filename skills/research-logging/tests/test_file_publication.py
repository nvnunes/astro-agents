from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validation import file_publication  # noqa: E402


class FilePublicationTests(unittest.TestCase):
    def test_replace_preserves_mode_and_cleans_its_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_bytes(b"before")
            path.chmod(0o640)

            identity = file_publication.atomic_replace_bytes(path, b"after")

            self.assertEqual(path.read_bytes(), b"after")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(identity, file_publication.file_identity(path.stat()))
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_replace_write_sync_failure_preserves_target_and_cleans_temporary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_bytes(b"before")

            with (
                patch.object(
                    file_publication.os, "fsync", side_effect=OSError("write sync")
                ),
                self.assertRaisesRegex(OSError, "write sync"),
            ):
                file_publication.atomic_replace_bytes(path, b"after")

            self.assertEqual(path.read_bytes(), b"before")
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_replace_install_failure_preserves_target_and_cleans_temporary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_bytes(b"before")

            with (
                patch.object(
                    file_publication.os, "replace", side_effect=OSError("install")
                ),
                self.assertRaisesRegex(OSError, "install"),
            ):
                file_publication.atomic_replace_text(path, "after")

            self.assertEqual(path.read_bytes(), b"before")
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_replace_directory_sync_failure_leaves_installed_target_and_no_temporary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_bytes(b"before")

            with (
                patch.object(
                    file_publication.os,
                    "fsync",
                    side_effect=[None, OSError("directory sync")],
                ),
                self.assertRaisesRegex(OSError, "directory sync"),
            ):
                file_publication.atomic_replace_bytes(path, b"after")

            self.assertEqual(path.read_bytes(), b"after")
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_create_collision_preserves_target_and_cleans_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_bytes(b"before")

            with self.assertRaises(FileExistsError):
                file_publication.atomic_create_text(path, "after")

            self.assertEqual(path.read_bytes(), b"before")
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_create_write_sync_failure_cleans_unpublished_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"

            with (
                patch.object(
                    file_publication.os, "fsync", side_effect=OSError("write sync")
                ),
                self.assertRaisesRegex(OSError, "write sync"),
            ):
                file_publication.atomic_create_text(path, "value")

            self.assertFalse(path.exists())
            self.assertEqual(tuple(root.iterdir()), ())

    def test_create_sync_failure_removes_publication_and_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"

            with (
                patch.object(
                    file_publication.os,
                    "fsync",
                    side_effect=[None, OSError("directory sync"), None],
                ),
                self.assertRaisesRegex(OSError, "directory sync"),
            ):
                file_publication.atomic_create_text(path, "value")

            self.assertFalse(path.exists())
            self.assertEqual(tuple(root.iterdir()), ())

    def test_snapshot_copy_uses_explicit_mode_and_cleans_its_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "snapshot"
            path = root / "state.json"
            source.write_bytes(b"prior")

            file_publication.atomic_replace_from_file(path, source, 0o600)

            self.assertEqual(path.read_bytes(), b"prior")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(set(root.iterdir()), {source, path})


if __name__ == "__main__":
    unittest.main()
