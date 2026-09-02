from __future__ import annotations

import importlib
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock

import h5py
import numpy as np
from research_log_validation_test_support import unittest, write

CACHE = importlib.import_module("validation.validation_cache")
RESULTS = importlib.import_module("validation.mechanical_results")
VALUES = importlib.import_module("validation.mechanical_values")
FINGERPRINTS = importlib.import_module("validation.fingerprint_cache")
LOCATOR = importlib.import_module("validation.locator")

RULES = "research-log-mechanical-rules/test"
REPORT = "b" * 64


def _selection(
    *,
    source_identity: str | None = None,
    source_profile: str = "csv",
    locator_identity: str = 'v2:{"select":[["value"]]}',
    value: int = 1,
) -> object:
    source_identity = source_identity or "sha256:" + "a" * 64
    items = (
        VALUES.SelectionItem(
            coordinate=(0, "value"),
            value=VALUES.integer_value(value),
            record=0,
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


def _check(identity: str = "evidence:e001:value") -> object:
    return RESULTS.MechanicalCheck(
        identity,
        RESULTS.CheckScope.EVIDENCE,
        RESULTS.CheckStatus.PASS,
        "entry/value",
        ({"selection": "abc"},),
    )


def _lookup(cache: object, selection: object, *, evaluator: str = "evaluator/1"):
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
    def test_all_maintained_source_profiles_reuse_exact_selections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = (
                (root / "results.csv", "value\n1\n", {"select": [["value"]]}),
                (root / "results.tsv", "value\n1\n", {"select": [["value"]]}),
                (root / "results.json", '{"value":1}', {"path": ["value"]}),
                (
                    root / "results.txt",
                    "retained value\n",
                    {"text": {"contains": "value"}},
                ),
            )
            for path, content, _ in sources:
                write(path, content)
            npz = root / "results.npz"
            np.savez(npz, values=np.array([1], dtype=np.int64))
            hdf5 = root / "results.h5"
            with h5py.File(hdf5, "w") as handle:
                handle.create_dataset("values", data=np.array([1], dtype=np.int64))
            binary_sources = (
                (npz, {"path": ["values", 0]}),
                (hdf5, {"path": ["values", 0]}),
            )
            observed = []
            with (
                FINGERPRINTS.FingerprintCache(root, writable=True) as fingerprints,
                CACHE.ValidationCache(root / "log", writable=True) as cache,
            ):
                for path, _, locator in sources:
                    identity = LOCATOR.observe_source_identity(
                        path, fingerprint_cache=fingerprints
                    )
                    selection = LOCATOR.evaluate_observed_locator(
                        LOCATOR.load_source(identity), locator
                    )
                    cache.store_selection(selection, evaluator_version="evaluator/1")
                    observed.append((identity, selection))
                for path, locator in binary_sources:
                    identity = LOCATOR.observe_source_identity(
                        path, fingerprint_cache=fingerprints
                    )
                    selection = LOCATOR.evaluate_observed_locator(
                        LOCATOR.load_source(identity), locator
                    )
                    cache.store_selection(selection, evaluator_version="evaluator/1")
                    observed.append((identity, selection))
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root / "log", writable=False) as cache:
                for identity, selection in observed:
                    with self.subTest(profile=identity.profile):
                        LOCATOR.require_source_reader(identity)
                        self.assertEqual(_lookup(cache, selection), selection)

    def test_successful_selection_and_check_baseline_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            check = _check()
            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, selection))
                cache.store_selection(selection, evaluator_version="evaluator/1")
                self.assertTrue(
                    cache.finish_published_run(
                        (check,), rules_version=RULES, report_sha256=REPORT
                    )
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)
                comparison = cache.load_check_comparison(
                    rules_version=RULES, report_sha256=REPORT
                )

            self.assertIsNotNone(comparison)
            assert comparison is not None
            self.assertEqual(comparison[check.identity].check, check)
            self.assertEqual(
                comparison[check.identity].dependency_projection,
                CACHE.check_dependency(check, RULES),
            )

    def test_each_selection_key_component_invalidates_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                misses = (
                    _selection(source_identity="sha256:" + "c" * 64),
                    _selection(source_profile="tsv"),
                    _selection(locator_identity='v2:{"path":["value"]}'),
                )
                for changed in misses:
                    with self.subTest(changed=changed):
                        self.assertIsNone(_lookup(cache, changed))
                self.assertIsNone(_lookup(cache, selection, evaluator="evaluator/2"))
                self.assertEqual(_lookup(cache, selection), selection)

    def test_read_only_absence_does_not_create_cache_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(_lookup(cache, selection))

            self.assertFalse((root / ".cache").exists())

    def test_recompute_bypasses_reads_but_repopulates_writable_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root, writable=True, reuse=False) as cache:
                self.assertIsNone(_lookup(cache, selection))
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

    def test_incomplete_run_retains_prior_and_new_completed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            first = _selection(value=1)
            second = _selection(locator_identity='v2:{"select":[["other"]]}', value=2)
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(first, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(second, evaluator_version="evaluator/1")

            path = root / ".cache" / CACHE.CACHE_FILENAME
            self.assertEqual(_row_count(path, "evidence_selections"), 2)

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertEqual(_lookup(cache, first), first)
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            self.assertEqual(_row_count(path, "evidence_selections"), 1)

    def test_per_result_and_per_log_bounds_omit_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            first = _selection(value=1)
            second = _selection(locator_identity='v2:{"select":[["other"]]}', value=2)
            with (
                mock.patch.object(CACHE, "MAX_SELECTION_BYTES", 1),
                CACHE.ValidationCache(root, writable=True) as cache,
            ):
                cache.store_selection(first, evaluator_version="evaluator/1")
                self.assertEqual(cache.metrics.selection_oversized, 1)

            limit = len(CACHE.encode_selection(first)) + 1
            with (
                mock.patch.object(CACHE, "MAX_SELECTION_CACHE_BYTES", limit),
                CACHE.ValidationCache(root, writable=True) as cache,
            ):
                cache.store_selection(first, evaluator_version="evaluator/1")
                cache.store_selection(second, evaluator_version="evaluator/1")
                self.assertEqual(cache.metrics.selection_oversized, 1)

    def test_future_component_is_preserved_without_disabling_other_component(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            check = _check()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (check,), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE cache_components SET version = ? WHERE component = ?",
                    (CACHE.CHECK_COMPARISON_VERSION + 1, "check_comparison"),
                )
                connection.commit()

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(
                    cache.load_check_comparison(
                        rules_version=RULES, report_sha256=REPORT
                    )
                )
                self.assertEqual(_lookup(cache, selection), selection)
                self.assertFalse(
                    cache.finish_published_run(
                        (check,), rules_version=RULES, report_sha256=REPORT
                    )
                )

            with closing(sqlite3.connect(path)) as connection:
                version = connection.execute(
                    "SELECT version FROM cache_components WHERE component = ?",
                    ("check_comparison",),
                ).fetchone()[0]
            self.assertEqual(version, CACHE.CHECK_COMPARISON_VERSION + 1)

    def test_stale_component_is_rebuilt_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            check = _check()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (check,), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE cache_components SET version = 0 WHERE component = ?",
                    ("check_comparison",),
                )
                connection.commit()

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertEqual(_lookup(cache, selection), selection)
                self.assertEqual(
                    cache.load_check_comparison(
                        rules_version=RULES, report_sha256=REPORT
                    ),
                    {},
                )

    def test_malformed_selection_row_is_a_miss_and_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE evidence_selections SET selection_json = ?",
                    (b"{",),
                )
                connection.commit()

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, selection))

            self.assertEqual(_row_count(path, "evidence_selections"), 0)

    def test_text_selection_payload_is_a_miss_and_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            payload = CACHE.encode_selection(selection)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE evidence_selections SET selection_json = ?",
                    (payload.decode("utf-8"),),
                )
                connection.commit()

            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, selection))

            self.assertEqual(_row_count(path, "evidence_selections"), 0)

    def test_selection_stores_do_not_rescan_retained_payload_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            statements: list[str] = []
            first = _selection(value=1)
            second = _selection(locator_identity='v2:{"select":[["other"]]}', value=2)

            with CACHE.ValidationCache(root, writable=True) as cache:
                assert cache._connection is not None
                cache._connection.set_trace_callback(statements.append)
                cache.store_selection(first, evaluator_version="evaluator/1")
                cache.store_selection(second, evaluator_version="evaluator/1")

            normalized = "\n".join(statements).upper()
            self.assertNotIn("SUM(SERIALIZED_BYTES)", normalized)

    def test_corrupt_database_is_bypassed_read_only_and_rebuilt_writable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            cache_root = root / ".cache"
            cache_root.mkdir(parents=True)
            path = cache_root / CACHE.CACHE_FILENAME
            path.write_bytes(b"not a sqlite database")
            selection = _selection()

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(_lookup(cache, selection))
            self.assertEqual(path.read_bytes(), b"not a sqlite database")

            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

    def test_invalid_generation_state_is_rebuilt_writable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            selection = _selection()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "DELETE FROM cache_state WHERE key = 'next_generation'"
                )
                connection.commit()

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(_lookup(cache, selection))
            with CACHE.ValidationCache(root, writable=True) as cache:
                self.assertIsNone(_lookup(cache, selection))
                cache.store_selection(selection, evaluator_version="evaluator/1")
                cache.finish_published_run(
                    (), rules_version=RULES, report_sha256=REPORT
                )
            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(_lookup(cache, selection), selection)

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

    def test_oversized_check_baseline_is_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            check = _check()
            with (
                mock.patch.object(CACHE, "MAX_CHECK_CACHE_BYTES", 1),
                CACHE.ValidationCache(root, writable=True) as cache,
            ):
                self.assertFalse(
                    cache.finish_published_run(
                        (check,), rules_version=RULES, report_sha256=REPORT
                    )
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertEqual(
                    cache.load_check_comparison(
                        rules_version=RULES, report_sha256=REPORT
                    ),
                    {},
                )

    def test_check_preflight_bounds_all_retained_columns_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            check = _check()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.finish_published_run(
                    (check,), rules_version=RULES, report_sha256=REPORT
                )
            path = root / ".cache" / CACHE.CACHE_FILENAME
            with closing(sqlite3.connect(path)) as connection:
                payload_size = int(
                    connection.execute(
                        "SELECT length(check_json) FROM check_comparison"
                    ).fetchone()[0]
                )
                connection.execute(
                    "UPDATE check_comparison SET identity = ?",
                    ("x" * (payload_size + 100),),
                )
                connection.commit()

            with (
                mock.patch.object(CACHE, "MAX_CHECK_CACHE_BYTES", payload_size + 1),
                mock.patch.object(
                    CACHE,
                    "_decode_check_row",
                    side_effect=AssertionError("oversized rows must not be decoded"),
                ),
                CACHE.ValidationCache(root, writable=False) as cache,
            ):
                self.assertIsNone(
                    cache.load_check_comparison(
                        rules_version=RULES, report_sha256=REPORT
                    )
                )

    def test_check_baseline_requires_exact_report_and_rules_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "log"
            check = _check()
            with CACHE.ValidationCache(root, writable=True) as cache:
                cache.finish_published_run(
                    (check,), rules_version=RULES, report_sha256=REPORT
                )

            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(
                    cache.load_check_comparison(
                        rules_version="changed", report_sha256=REPORT
                    )
                )
            with CACHE.ValidationCache(root, writable=False) as cache:
                self.assertIsNone(
                    cache.load_check_comparison(
                        rules_version=RULES, report_sha256="c" * 64
                    )
                )


if __name__ == "__main__":
    unittest.main()
