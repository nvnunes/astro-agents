from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import importlib, write

DATA = importlib.import_module("research_log_data")
CACHE = importlib.import_module("validation.fingerprint_cache")
FILESYSTEM = importlib.import_module("validation.filesystem")


def file_resource(path: Path, *, digest: str | None = None) -> object:
    observed = digest or hashlib.sha256(path.read_bytes()).hexdigest()
    return DATA.InputResource(
        "source",
        "file",
        path.as_posix(),
        DATA.Fingerprint("sha256", digest=observed),
        False,
        path.resolve().as_posix(),
    )


def directory_resource(path: Path) -> object:
    provisional = DATA.InputResource(
        "collection",
        "directory",
        path.as_posix(),
        DATA.Fingerprint("directory-sha256-v1", digest="0" * 64),
        False,
        path.resolve().as_posix(),
    )
    observed = DATA.observe_fingerprint(provisional)
    return DATA.InputResource(
        "collection",
        "directory",
        path.as_posix(),
        observed.fingerprint,
        False,
        path.resolve().as_posix(),
    )


def identity_files_resource(path: Path) -> object:
    provisional = DATA.InputResource(
        "build",
        "directory",
        path.as_posix(),
        DATA.Fingerprint(
            "identity-files-sha256-v1",
            digest="0" * 64,
            files=("build.h5", "build.yaml"),
        ),
        False,
        path.resolve().as_posix(),
    )
    observed = DATA.observe_fingerprint(provisional)
    return DATA.InputResource(
        "build",
        "directory",
        path.as_posix(),
        observed.fingerprint,
        False,
        path.resolve().as_posix(),
    )


def identity_patterns_resource(path: Path) -> object:
    provisional = DATA.InputResource(
        "build",
        "directory",
        path.as_posix(),
        DATA.Fingerprint(
            "identity-patterns-sha256-v1",
            digest="0" * 64,
            patterns=("build.h5", "maps-*.h5"),
        ),
        False,
        path.resolve().as_posix(),
    )
    observed = DATA.observe_fingerprint(provisional)
    return DATA.InputResource(
        "build",
        "directory",
        path.as_posix(),
        observed.fingerprint,
        False,
        path.resolve().as_posix(),
    )


class FingerprintCacheTests(unittest.TestCase):
    def test_project_cache_reuses_one_file_across_log_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "inputs" / "source.csv"
            write(source, "value\n1\n")
            resource = file_resource(source)

            with CACHE.FingerprintCache(root, writable=True) as cache:
                first = cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)
            with (
                mock.patch.object(
                    CACHE,
                    "observe_file_content",
                    side_effect=AssertionError("content must not be reread"),
                ),
                CACHE.FingerprintCache(root, writable=True) as cache,
            ):
                second = cache.verify(resource)

            assert first is not None and second is not None
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertTrue(second.identity_reused)
            self.assertEqual(cache.metrics.file_reuses, 1)
            self.assertEqual(
                cache.path,
                root.resolve() / ".cache" / "research-log-fingerprints.sqlite3",
            )

    def test_expected_fingerprint_change_does_not_force_a_content_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(file_resource(source))

            changed_expectation = file_resource(source, digest="f" * 64)
            with (
                mock.patch.object(
                    CACHE,
                    "observe_file_content",
                    side_effect=AssertionError("content must not be reread"),
                ),
                CACHE.FingerprintCache(root, writable=True) as cache,
            ):
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.fingerprint.mismatch"
                ):
                    cache.verify(changed_expectation)
                self.assertEqual(cache.metrics.file_reuses, 1)

    def test_changed_directory_member_hashes_only_that_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "collection"
            write(collection / "a.txt", "a")
            write(collection / "b.txt", "b")
            resource = directory_resource(collection)
            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(resource)

            write(collection / "b.txt", "changed")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.fingerprint.mismatch"
                ):
                    cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)
                self.assertEqual(cache.metrics.file_reuses, 1)

    def test_identity_files_avoid_traversal_and_rehash_only_changed_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            write(build / "build.h5", "state")
            write(build / "build.yaml", "mode: test\n")
            write(build / "products" / "outer.h5", "product")
            resource = identity_files_resource(build)

            with (
                mock.patch.object(
                    CACHE,
                    "observe_directory_tree",
                    side_effect=AssertionError("managed directory must not be walked"),
                ),
                CACHE.FingerprintCache(root, writable=True) as cache,
            ):
                cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 2)

            write(build / "products" / "outer.h5", "changed product")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                unchanged = cache.verify(resource)
                assert unchanged is not None
                self.assertTrue(unchanged.identity_reused)
                self.assertEqual(cache.metrics.file_hashes, 0)
                self.assertEqual(cache.metrics.file_reuses, 2)

            write(build / "build.h5", "changed state")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.fingerprint.mismatch"
                ):
                    cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)
                self.assertEqual(cache.metrics.file_reuses, 1)

    def test_identity_patterns_reuse_matches_and_hash_only_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            write(build / "build.h5", "state")
            write(build / "maps-hpx6.h5", "map 6")
            write(build / "products" / "outer.h5", "product")
            resource = identity_patterns_resource(build)

            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 2)

            write(build / "products" / "outer.h5", "changed product")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                unchanged = cache.verify(resource)
                assert unchanged is not None
                self.assertTrue(unchanged.identity_reused)
                self.assertEqual(cache.metrics.file_hashes, 0)
                self.assertEqual(cache.metrics.file_reuses, 2)

            write(build / "maps-hpx9.h5", "map 9")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.fingerprint.mismatch"
                ):
                    cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)
                self.assertEqual(cache.metrics.file_reuses, 2)

    def test_identity_patterns_hash_the_first_wildcard_match_incrementally(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            write(build / "build.h5", "state")
            resource = identity_patterns_resource(build)

            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)

            write(build / "maps-hpx6.h5", "map 6")
            with CACHE.FingerprintCache(root, writable=True) as cache:
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.fingerprint.mismatch"
                ):
                    cache.verify(resource)
                self.assertEqual(cache.metrics.file_hashes, 1)
                self.assertEqual(cache.metrics.file_reuses, 1)

    def test_interrupted_directory_hydration_preserves_completed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "collection"
            write(collection / "a.txt", "a")
            write(collection / "b.txt", "b")
            resource = directory_resource(collection)
            original = CACHE.observe_file_content
            calls = 0

            def interrupt_second(path: Path) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("interrupted")
                return original(path)

            with (
                mock.patch.object(
                    CACHE, "observe_file_content", side_effect=interrupt_second
                ),
                CACHE.FingerprintCache(root, writable=True) as cache,
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    cache.verify(resource)

            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(resource)
                self.assertEqual(cache.metrics.file_reuses, 1)
                self.assertEqual(cache.metrics.file_hashes, 1)

    def test_concurrent_sessions_hash_the_same_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            resource = file_resource(source)
            original = CACHE.observe_file_content
            hashes = 0
            hashes_lock = threading.Lock()

            def slow_hash(path: Path) -> object:
                nonlocal hashes
                with hashes_lock:
                    hashes += 1
                time.sleep(0.05)
                return original(path)

            failures: list[BaseException] = []
            start = threading.Barrier(8)

            def verify() -> None:
                try:
                    start.wait()
                    with CACHE.FingerprintCache(root, writable=True) as cache:
                        cache.verify(resource)
                except BaseException as error:  # pragma: no cover - assertion aid
                    failures.append(error)

            with mock.patch.object(
                CACHE, "observe_file_content", side_effect=slow_hash
            ):
                threads = [threading.Thread(target=verify) for _ in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(failures, [])
            self.assertEqual(hashes, 1)

    def test_read_only_session_observes_committed_wal_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            write(first, "value\n1\n")
            write(second, "value\n2\n")

            with CACHE.FingerprintCache(root, writable=True) as writer:
                writer.verify(file_resource(first))
                assert writer._connection is not None
                writer._connection.execute("PRAGMA wal_autocheckpoint=0")
                writer._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                resource = file_resource(second)
                writer.verify(resource)

                with (
                    mock.patch.object(
                        CACHE,
                        "observe_file_content",
                        side_effect=AssertionError("WAL observation must be reused"),
                    ),
                    CACHE.FingerprintCache(root, writable=False) as reader,
                ):
                    observed = reader.verify(resource)

            assert observed is not None
            self.assertTrue(observed.identity_reused)
            self.assertEqual(reader.metrics.file_reuses, 1)

    def test_input_disappearing_during_observation_is_mechanical_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            resource = file_resource(source)
            with CACHE.FingerprintCache(root, writable=True) as cache:
                source.unlink()
                with (
                    mock.patch.object(Path, "is_file", return_value=True),
                    self.assertRaises(DATA.DataContractError) as captured,
                ):
                    cache.verify(resource)

            self.assertEqual(
                captured.exception.code, "provenance.observation.unavailable"
            )

    def test_read_only_session_does_not_create_a_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            with CACHE.FingerprintCache(root, writable=False) as cache:
                cache.verify(file_resource(source))
            self.assertFalse((root / ".cache").exists())

    def test_read_only_session_ignores_a_corrupt_generated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            cache_path = root / ".cache/research-log-fingerprints.sqlite3"
            cache_path.parent.mkdir()
            cache_path.write_bytes(b"not a sqlite database")

            with CACHE.FingerprintCache(root, writable=False) as cache:
                observed = cache.verify(file_resource(source))

            assert observed is not None
            self.assertFalse(observed.identity_reused)
            self.assertEqual(cache.metrics.file_hashes, 1)
            self.assertEqual(cache_path.read_bytes(), b"not a sqlite database")

    def test_writable_session_rebuilds_a_corrupt_generated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            cache_path = root / ".cache/research-log-fingerprints.sqlite3"
            cache_path.parent.mkdir()
            cache_path.write_bytes(b"not a sqlite database")
            journal_path = Path(f"{cache_path}-journal")
            journal_path.write_bytes(b"stale generated journal")

            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(file_resource(source))

            connection = sqlite3.connect(cache_path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0], 1
                )
            finally:
                connection.close()
            self.assertFalse(journal_path.exists())

    def test_writable_session_rebuilds_an_incomplete_version_1_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            cache_path = root / ".cache/research-log-fingerprints.sqlite3"
            cache_path.parent.mkdir()
            connection = sqlite3.connect(cache_path)
            try:
                connection.execute("CREATE TABLE file_observations (path TEXT)")
                connection.execute("CREATE TABLE directory_observations (path TEXT)")
                connection.execute(
                    "CREATE TABLE directory_members (directory_path TEXT)"
                )
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            finally:
                connection.close()

            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(file_resource(source))

            connection = sqlite3.connect(cache_path)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(file_observations)"
                    )
                }
            finally:
                connection.close()
            self.assertEqual(
                columns,
                {"algorithm", "ctime_ns", "digest", "mtime_ns", "path", "size"},
            )

    def test_unknown_future_schema_is_ignored_and_not_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            write(source, "value\n1\n")
            cache_path = root / ".cache/research-log-fingerprints.sqlite3"
            cache_path.parent.mkdir()
            connection = sqlite3.connect(cache_path)
            try:
                connection.execute("CREATE TABLE future_state (value TEXT)")
                connection.execute("PRAGMA user_version=2")
                connection.commit()
            finally:
                connection.close()

            with CACHE.FingerprintCache(root, writable=True) as cache:
                observed = cache.verify(file_resource(source))

            assert observed is not None
            self.assertFalse(observed.identity_reused)
            self.assertEqual(cache.metrics.file_hashes, 1)
            connection = sqlite3.connect(cache_path)
            try:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            finally:
                connection.close()
            self.assertIn(("future_state",), tables)

    def test_project_root_uses_nearest_git_worktree_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            project = outer / "docs"
            (project / ".git").mkdir(parents=True)
            summary = project / "research" / "study.md"

            self.assertEqual(CACHE.project_root(summary), project.resolve())

    def test_project_root_requires_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "research" / "study.md"

            with self.assertRaisesRegex(CACHE.FingerprintCacheError, "Git metadata"):
                CACHE.project_root(summary)

    def test_sqlite_schema_retains_directory_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "collection"
            write(collection / "a.txt", "a")
            write(collection / "empty" / ".keep", "")
            resource = directory_resource(collection)
            with CACHE.FingerprintCache(root, writable=True) as cache:
                cache.verify(resource)
                path = cache.path
            connection = sqlite3.connect(path)
            try:
                members = connection.execute(
                    "SELECT member_path, member_kind FROM directory_members "
                    "ORDER BY member_path"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                members,
                [("a.txt", "file"), ("empty", "directory"), ("empty/.keep", "file")],
            )

    def test_just_published_regular_file_can_be_remembered_without_reread(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "validation" / "results.json"
            payload = b'{"schema":"fixture"}\n'
            write(report, payload.decode())
            digest = hashlib.sha256(payload).hexdigest()

            with CACHE.FingerprintCache(root, writable=True) as cache:
                self.assertTrue(
                    cache.remember_regular_file(
                        report,
                        digest=digest,
                        expected_size=len(payload),
                        expected_identity=FILESYSTEM.file_identity(report.lstat()),
                    )
                )

            with (
                mock.patch.object(
                    CACHE,
                    "observe_file_content",
                    side_effect=AssertionError("remembered bytes must not be reread"),
                ),
                CACHE.FingerprintCache(root, writable=False) as cache,
            ):
                observation = cache.observe_regular_file(report)

            self.assertEqual(observation.fingerprint.digest, digest)
            self.assertTrue(observation.identity_reused)

    def test_remember_regular_file_rejects_wrong_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "remembered.json"
            write(report, "{}\n")

            with CACHE.FingerprintCache(root, writable=True) as cache:
                self.assertFalse(
                    cache.remember_regular_file(
                        report,
                        digest=hashlib.sha256(report.read_bytes()).hexdigest(),
                        expected_size=999,
                        expected_identity=FILESYSTEM.file_identity(report.lstat()),
                    )
                )

    def test_remember_regular_file_rejects_a_same_size_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "remembered.json"
            write(report, "old\n")
            published_identity = FILESYSTEM.file_identity(report.lstat())
            replacement = root / "replacement.json"
            write(replacement, "new\n")
            replacement.replace(report)

            with CACHE.FingerprintCache(root, writable=True) as cache:
                remembered = cache.remember_regular_file(
                    report,
                    digest=hashlib.sha256(b"old\n").hexdigest(),
                    expected_size=4,
                    expected_identity=published_identity,
                )

            self.assertFalse(remembered)


if __name__ == "__main__":
    unittest.main()
