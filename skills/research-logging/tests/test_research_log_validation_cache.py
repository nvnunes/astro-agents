from __future__ import annotations

import importlib
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import unittest

CACHE = importlib.import_module("validation.validation_cache")
VALUES = importlib.import_module("validation.mechanical_values")


def _selection(
    *,
    source_identity: str = "sha256:" + "a" * 64,
    source_profile: str = "csv",
    locator_identity: str = 'v2:{"select":[["value"]]}',
    value: int = 1,
) -> object:
    items = (
        VALUES.SelectionItem(
            coordinate=(0, "value"), value=VALUES.integer_value(value), record=0,
            field=("value",),
        ),
    )
    return VALUES.SelectionResult(
        locator_identity=locator_identity,
        source_identity=source_identity,
        source_profile=source_profile,
        items=items,
        matches=1,
        membership=("value",),
        identities=((VALUES.integer_value(0),),),
        shape=(1,),
        dependency_projection=VALUES.selection_dependency(
            source_identity=source_identity,
            locator_identity=locator_identity,
            items=items,
        ),
    )


def _lookup(
    cache: object, selection: object, *, evaluator: str = "evaluator/1"
):
    return cache.lookup_selection(
        source_identity=selection.source_identity,
        source_profile=selection.source_profile,
        locator_identity=selection.locator_identity,
        evaluator_version=evaluator,
    )


def _row_count(path: Path, table: str) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class ValidationCacheTests(unittest.TestCase):
    def test_successful_selection_round_trip_has_no_check_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, selection))
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run()

            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertNotIn("check_comparison", tables)
            self.assertEqual(_row_count(path, "evidence_selections"), 1)
            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

    def test_each_selection_key_component_invalidates_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run()

            with CACHE.ValidationCache(root, writable=False) as cache:
                for changed in (
                    _selection(source_identity="sha256:" + "c" * 64),
                    _selection(source_profile="tsv"),
                    _selection(locator_identity='v2:{"path":["value"]}'),
                ):
                    self.assertIsNone(_lookup(cache, changed))
                self.assertIsNone(_lookup(cache, selection, evaluator="evaluator/2"))
                self.assertEqual(_lookup(cache, selection), selection)

    def test_unpublished_run_retains_rows_until_successful_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            first = _selection(value=1)
            second = _selection(locator_identity='v2:{"select":[["other"]]}', value=2)
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(first, evaluator_version="evaluator/1")
                cache.finish_published_run()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(second, evaluator_version="evaluator/1")

            path = root / ".cache" / CACHE.CACHE_FILENAME
            self.assertEqual(_row_count(path, "evidence_selections"), 2)
            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertEqual(_lookup(cache, first), first)
                cache.finish_published_run()
            self.assertEqual(_row_count(path, "evidence_selections"), 1)

    def test_recompute_bypasses_reads_but_repopulates_writable_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run()
            with CACHE.ValidationCache(root, writable=True, reuse=False) as cache:
                self.assertIsNone(_lookup(cache, selection))
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run()
            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

    def test_corrupt_database_is_bypassed_read_only_and_rebuilt_writable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            path = root / ".cache" / CACHE.CACHE_FILENAME
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not a sqlite database")
            selection = _selection()

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(_lookup(cache, selection))
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run()
            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

    def test_future_schema_is_preserved_and_bypassed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            path = root / ".cache" / CACHE.CACHE_FILENAME
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    f"PRAGMA user_version={CACHE.CACHE_SCHEMA_VERSION + 1}"
                )
            before = path.read_bytes()

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, _selection()))
            self.assertEqual(path.read_bytes(), before)

    def test_obsolete_schema_is_rebuilt_without_check_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            path = root / ".cache" / CACHE.CACHE_FILENAME
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE check_comparison (identity TEXT)")
                connection.execute("CREATE TABLE cache_components (component TEXT)")
                connection.execute("PRAGMA user_version=1")
            with CACHE.ValidationCache(root, writable=True):
                pass

            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertNotIn("check_comparison", tables)

    def test_transient_open_failure_is_bypassed_without_deleting_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = CACHE.ValidationCache(Path(directory) / "log", writable=True)
            with (
                mock.patch.object(
                    cache,
                    "_open_once",
                    side_effect=sqlite3.OperationalError("database is locked"),
                ),
                mock.patch.object(cache, "_discard_corrupt_cache") as discard,
            ):
                self.assertIsNone(cache._open_with_recovery())
            discard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
