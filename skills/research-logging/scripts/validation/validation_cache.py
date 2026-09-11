"""Per-log SQLite acceleration for mechanical validation.

The cache owns disposable successful-selection state. It does not own
filesystem identity, authored research metadata, or authoritative validation
results.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

from .mechanical_values import SelectionResult
from .selection_codec import SelectionCodecError, decode_selection, encode_selection
from .sqlite_support import is_sqlite_corruption

CACHE_FILENAME = "research-log-validation.sqlite3"
CACHE_SCHEMA_VERSION = 2
EVIDENCE_SELECTION_VERSION = 1
MAX_SELECTION_ROWS = 100_000
MAX_SELECTION_BYTES = 256 * 1024
MAX_SELECTION_CACHE_BYTES = 16 * 1024 * 1024
CACHE_COMPANION_SUFFIXES = ("", "-journal", "-shm", "-wal")
CACHE_TABLE_COLUMNS = {
    "cache_components": ("component", "version"),
    "cache_state": ("key", "value"),
    "evidence_selections": (
        "source_identity",
        "source_profile",
        "locator_identity",
        "evaluator_version",
        "selection_json",
        "serialized_bytes",
        "retention_generation",
    ),
}


class ValidationCacheError(RuntimeError):
    """Raised when generated validation-cache state cannot be used safely."""


class CorruptValidationCacheError(ValidationCacheError):
    """Raised when generated validation-cache state is demonstrably corrupt."""


@dataclass
class ValidationCacheMetrics:
    """Non-normative diagnostics for one per-log cache session."""

    selection_hits: int = 0
    selection_misses: int = 0
    selection_writes: int = 0
    selection_oversized: int = 0
    sqlite_reads: int = 0
    sqlite_writes: int = 0
    serialized_cache_bytes: int = 0

    def as_dict(self) -> Mapping[str, int]:
        """Return stable public telemetry names."""

        return {
            "selection_cache_hits": self.selection_hits,
            "selection_cache_misses": self.selection_misses,
            "selection_cache_writes": self.selection_writes,
            "selection_cache_oversized": self.selection_oversized,
            "validation_cache_sqlite_reads": self.sqlite_reads,
            "validation_cache_sqlite_writes": self.sqlite_writes,
            "validation_cache_serialized_bytes": self.serialized_cache_bytes,
        }


class ValidationCache:
    """One bounded disposable validation cache for a maintained log.

    The caller owns the per-log writer lock. Writable sessions persist each
    completed selection independently, then promote comparison state and clean
    obsolete selections only after authoritative publication succeeds.
    """

    def __init__(self, log_root: Path, *, writable: bool, reuse: bool = True) -> None:
        self.log_root = log_root.resolve()
        self.path = self.log_root / ".cache" / CACHE_FILENAME
        self.writable = writable
        self.reuse = reuse
        self.metrics = ValidationCacheMetrics()
        self._connection: sqlite3.Connection | None = None
        self._generation: int | None = None
        self._selections_enabled = False
        self._used_selection_keys: set[tuple[str, str, str, str]] = set()
        self._selection_cache_rows: int | None = None
        self._selection_cache_bytes: int | None = None

    def __enter__(self) -> ValidationCache:
        """Open generated state, conservatively bypassing unusable caches."""

        self._connection = self._open()
        if self._connection is not None:
            try:
                self._configure_components()
                if self.writable and self._selections_enabled:
                    self._generation = self._allocate_generation()
            except (ValidationCacheError, sqlite3.DatabaseError, ValueError):
                self._connection.close()
                self._connection = None
                self._selections_enabled = False
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the database without suppressing evaluation failures."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def lookup_selection(
        self,
        *,
        source_identity: str,
        source_profile: str,
        locator_identity: str,
        evaluator_version: str,
    ) -> SelectionResult | None:
        """Return one exact current selection or a conservative cache miss."""

        connection = self._connection
        if not self.reuse or not self._selections_enabled or connection is None:
            self.metrics.selection_misses += 1
            return None
        key = (source_identity, source_profile, locator_identity, evaluator_version)
        try:
            row = connection.execute(
                """
                SELECT selection_json, serialized_bytes
                FROM evidence_selections
                WHERE source_identity = ? AND source_profile = ?
                  AND locator_identity = ? AND evaluator_version = ?
                """,
                key,
            ).fetchone()
            self.metrics.sqlite_reads += 1
            if row is None:
                self.metrics.selection_misses += 1
                return None
            if not isinstance(row[0], bytes):
                raise ValidationCacheError("cached selection payload is not bytes")
            payload = row[0]
            size = _bounded_size(row[1])
            if size != len(payload) or size > MAX_SELECTION_BYTES:
                raise ValidationCacheError("cached selection size is invalid")
            selection = decode_selection(payload)
            if (
                selection.source_identity != source_identity
                or selection.source_profile != source_profile
                or selection.locator_identity != locator_identity
            ):
                raise ValidationCacheError(
                    "cached selection key does not match payload"
                )
            if self.writable and self._generation is not None:
                self._used_selection_keys.add(key)
            self.metrics.selection_hits += 1
            self.metrics.serialized_cache_bytes += size
            return selection
        except (SelectionCodecError, ValidationCacheError, sqlite3.DatabaseError):
            self.metrics.selection_misses += 1
            self._reject_selection(key)
            return None

    def store_selection(
        self, selection: SelectionResult, *, evaluator_version: str
    ) -> None:
        """Persist one complete bounded selection in its own transaction."""

        connection = self._connection
        if (
            not self.writable
            or not self._selections_enabled
            or connection is None
            or self._generation is None
        ):
            return
        try:
            payload = encode_selection(selection)
        except SelectionCodecError:
            self._disable_selections()
            return
        size = len(payload)
        if size > MAX_SELECTION_BYTES:
            self.metrics.selection_oversized += 1
            return
        key = (
            selection.source_identity,
            selection.source_profile,
            selection.locator_identity,
            evaluator_version,
        )
        try:
            existing = connection.execute(
                """
                SELECT serialized_bytes FROM evidence_selections
                WHERE source_identity = ? AND source_profile = ?
                  AND locator_identity = ? AND evaluator_version = ?
                """,
                key,
            ).fetchone()
            self.metrics.sqlite_reads += 1
            if (
                self._selection_cache_rows is None
                or self._selection_cache_bytes is None
            ):
                self._disable_selections()
                return
            replaced = (
                _bounded_size(existing[0]) if existing is not None else 0
            )
            rows = self._selection_cache_rows + (1 if existing is None else 0)
            total = self._selection_cache_bytes - replaced + size
            if rows > MAX_SELECTION_ROWS or total > MAX_SELECTION_CACHE_BYTES:
                self.metrics.selection_oversized += 1
                return
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO evidence_selections (
                    source_identity, source_profile, locator_identity,
                    evaluator_version, selection_json, serialized_bytes,
                    retention_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    source_identity, source_profile, locator_identity,
                    evaluator_version
                ) DO UPDATE SET
                    selection_json = excluded.selection_json,
                    serialized_bytes = excluded.serialized_bytes,
                    retention_generation = excluded.retention_generation
                """,
                (*key, payload, size, self._generation),
            )
            connection.commit()
            self.metrics.selection_writes += 1
            self.metrics.sqlite_writes += 1
            self.metrics.serialized_cache_bytes += size
            self._selection_cache_rows = rows
            self._selection_cache_bytes = total
        except (
            OSError,
            SelectionCodecError,
            ValidationCacheError,
            sqlite3.DatabaseError,
        ):
            if connection.in_transaction:
                connection.rollback()
            self._disable_selections()

    def finish_published_run(self) -> None:
        """Remove selections not used by a successfully published evaluation."""

        if self.writable:
            self._finish_selection_generation()

    def _finish_selection_generation(self) -> None:
        connection = self._connection
        if (
            not self._selections_enabled
            or connection is None
            or self._generation is None
        ):
            return
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """
                UPDATE evidence_selections SET retention_generation = ?
                WHERE source_identity = ? AND source_profile = ?
                  AND locator_identity = ? AND evaluator_version = ?
                """,
                ((self._generation, *key) for key in self._used_selection_keys),
            )
            connection.execute(
                "DELETE FROM evidence_selections WHERE retention_generation != ?",
                (self._generation,),
            )
            connection.execute(
                "UPDATE cache_state SET value = ? WHERE key = 'completed_generation'",
                (str(self._generation),),
            )
            connection.commit()
            self.metrics.sqlite_writes += 1
            self._refresh_selection_storage()
        except sqlite3.DatabaseError:
            if connection.in_transaction:
                connection.rollback()
            self._disable_selections()

    def _open(self) -> sqlite3.Connection | None:
        if not self.reuse and not self.writable:
            return None
        self._validate_target()
        if not self.writable and not self.path.is_file():
            return None
        return self._open_with_recovery()

    def _open_with_recovery(self) -> sqlite3.Connection | None:
        try:
            return self._open_once()
        except (ValidationCacheError, sqlite3.DatabaseError, ValueError) as error:
            if not self.writable or not _rebuildable_cache_error(error):
                return None
        self._discard_corrupt_cache()
        try:
            return self._open_once()
        except (ValidationCacheError, sqlite3.DatabaseError, ValueError):
            return None

    def _open_once(self) -> sqlite3.Connection | None:
        if self.writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5.0)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if version > CACHE_SCHEMA_VERSION:
                    connection.close()
                    return None
                if version == 0:
                    if _has_user_tables(connection):
                        raise CorruptValidationCacheError(
                            "unversioned validation cache is not empty"
                        )
                    _create_schema(connection)
                    connection.commit()
                elif version != CACHE_SCHEMA_VERSION:
                    raise CorruptValidationCacheError(
                        "validation cache schema is obsolete"
                    )
                _validate_schema(connection)
                _require_integrity(connection)
                _validate_cache_state(connection)
                journal = connection.execute(
                    "PRAGMA journal_mode=DELETE"
                ).fetchone()
                if journal is None or str(journal[0]).lower() != "delete":
                    raise ValidationCacheError(
                        "could not enable validation-cache rollback journal"
                    )
                return connection
            except Exception:
                connection.close()
                raise
        uri = f"file:{quote(self.path.as_posix())}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != CACHE_SCHEMA_VERSION:
                connection.close()
                return None
            _validate_schema(connection)
            _require_integrity(connection)
            _validate_cache_state(connection)
            return connection
        except Exception:
            connection.close()
            raise

    def _configure_components(self) -> None:
        assert self._connection is not None
        try:
            rows = dict(
                self._connection.execute(
                    """
                    SELECT component, version FROM cache_components
                    WHERE component = 'evidence_selections'
                    """
                ).fetchall()
            )
        except sqlite3.DatabaseError:
            self._connection.close()
            self._connection = None
            return
        self._selections_enabled = self._configure_component(
            rows.get("evidence_selections"),
            name="evidence_selections",
            expected=EVIDENCE_SELECTION_VERSION,
        )
        if self._selections_enabled and not self._selection_storage_valid():
            self._selections_enabled = self._reset_component(
                "evidence_selections", EVIDENCE_SELECTION_VERSION
            )

    def _configure_component(
        self, observed: object, *, name: str, expected: int
    ) -> bool:
        if observed == expected:
            return True
        if not self.writable or isinstance(observed, int) and observed > expected:
            return False
        return self._reset_component(name, expected)

    def _reset_component(self, name: str, expected: int) -> bool:
        if not self.writable:
            return False
        connection = self._connection
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DELETE FROM {name}")
            connection.execute(
                """
                INSERT INTO cache_components (component, version) VALUES (?, ?)
                ON CONFLICT(component) DO UPDATE SET version=excluded.version
                """,
                (name, expected),
            )
            connection.commit()
            self.metrics.sqlite_writes += 1
            if name == "evidence_selections":
                self._selection_cache_rows = 0
                self._selection_cache_bytes = 0
            return True
        except sqlite3.DatabaseError:
            if connection.in_transaction:
                connection.rollback()
            return False

    def _selection_storage_valid(self) -> bool:
        connection = self._connection
        assert connection is not None
        try:
            count, total, maximum, payload_total = connection.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(serialized_bytes), 0),
                       COALESCE(MAX(serialized_bytes), 0),
                       COALESCE(SUM(length(selection_json)), 0)
                FROM evidence_selections
                """
            ).fetchone()
            self.metrics.sqlite_reads += 1
            valid = (
                _bounded_size(count) <= MAX_SELECTION_ROWS
                and _bounded_size(total) <= MAX_SELECTION_CACHE_BYTES
                and _bounded_size(maximum) <= MAX_SELECTION_BYTES
                and _bounded_size(payload_total) == total
            )
            if valid:
                self._selection_cache_rows = _bounded_size(count)
                self._selection_cache_bytes = _bounded_size(total)
            return valid
        except (ValidationCacheError, sqlite3.DatabaseError):
            return False

    def _allocate_generation(self) -> int:
        assert self._connection is not None
        row = self._connection.execute(
            "SELECT value FROM cache_state WHERE key = 'next_generation'"
        ).fetchone()
        generation = int(row[0]) if row is not None else 1
        self._connection.execute(
            "UPDATE cache_state SET value = ? WHERE key = 'next_generation'",
            (str(generation + 1),),
        )
        self._connection.commit()
        self.metrics.sqlite_writes += 1
        return generation

    def _validate_target(self) -> None:
        cache_root = self.log_root / ".cache"
        if cache_root.is_symlink() or self.path.is_symlink():
            raise ValidationCacheError(
                f"validation cache path must not contain a symlink: {self.path}"
            )
        if cache_root.exists() and not cache_root.is_dir():
            raise ValidationCacheError(
                f"validation cache parent must be a directory: {cache_root}"
            )
        if self.path.exists() and not self.path.is_file():
            raise ValidationCacheError(
                f"validation cache must be a regular file: {self.path}"
            )
        for suffix in ("-journal", "-shm", "-wal"):
            companion = Path(f"{self.path}{suffix}")
            if companion.is_symlink():
                raise ValidationCacheError(
                    "validation cache companion must not be a symlink: "
                    f"{companion}"
                )

    def _discard_corrupt_cache(self) -> None:
        for suffix in CACHE_COMPANION_SUFFIXES:
            path = Path(f"{self.path}{suffix}")
            try:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            except OSError:
                return

    def _reject_selection(self, key: tuple[str, str, str, str]) -> None:
        connection = self._connection
        if not self.writable or connection is None:
            return
        try:
            connection.execute(
                """
                DELETE FROM evidence_selections
                WHERE source_identity = ? AND source_profile = ?
                  AND locator_identity = ? AND evaluator_version = ?
                """,
                key,
            )
            connection.commit()
            self.metrics.sqlite_writes += 1
            self._refresh_selection_storage()
        except sqlite3.DatabaseError:
            if connection.in_transaction:
                connection.rollback()
            self._disable_selections()

    def _disable_selections(self) -> None:
        self._selections_enabled = False

    def _refresh_selection_storage(self) -> None:
        if not self._selection_storage_valid():
            self._selection_cache_rows = None
            self._selection_cache_bytes = None
            self._disable_selections()


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        is not None
    )


def _rebuildable_cache_error(error: Exception) -> bool:
    """Return whether disposable cache replacement is safe and warranted."""

    if isinstance(error, CorruptValidationCacheError):
        return True
    return isinstance(error, sqlite3.DatabaseError) and is_sqlite_corruption(error)


def _validate_schema(connection: sqlite3.Connection) -> None:
    for table, expected in CACHE_TABLE_COLUMNS.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        observed = tuple(str(row[1]) for row in rows)
        if observed != expected:
            raise CorruptValidationCacheError(
                f"validation cache table {table} has an incompatible shape"
            )


def _require_integrity(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA quick_check(1)").fetchone()
    if row != ("ok",):
        raise CorruptValidationCacheError(
            "validation cache integrity check failed"
        )


def _validate_cache_state(connection: sqlite3.Connection) -> None:
    count = connection.execute("SELECT COUNT(*) FROM cache_state").fetchone()[0]
    if count != 2:
        raise CorruptValidationCacheError(
            "validation cache has invalid generation state"
        )
    rows = connection.execute(
        """
        SELECT key, value FROM cache_state
        WHERE key IN ('completed_generation', 'next_generation')
        """
    ).fetchall()
    state = dict(rows)
    if len(rows) != 2 or set(state) != {"completed_generation", "next_generation"}:
        raise CorruptValidationCacheError(
            "validation cache has invalid generation keys"
        )
    try:
        completed = int(state["completed_generation"])
        next_generation = int(state["next_generation"])
    except (TypeError, ValueError) as error:
        raise CorruptValidationCacheError(
            "validation cache has invalid generation values"
        ) from error
    if (
        str(completed) != state["completed_generation"]
        or str(next_generation) != state["next_generation"]
        or completed < 0
        or next_generation <= completed
    ):
        raise CorruptValidationCacheError(
            "validation cache generation state is inconsistent"
        )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE cache_components "
        "(component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO cache_components (component, version) VALUES (?, ?)",
        ("evidence_selections", EVIDENCE_SELECTION_VERSION),
    )
    connection.execute(
        "CREATE TABLE cache_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO cache_state (key, value) VALUES (?, ?)",
        (("completed_generation", "0"), ("next_generation", "1")),
    )
    connection.execute(
        """
        CREATE TABLE evidence_selections (
            source_identity TEXT NOT NULL,
            source_profile TEXT NOT NULL,
            locator_identity TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            selection_json BLOB NOT NULL,
            serialized_bytes INTEGER NOT NULL,
            retention_generation INTEGER NOT NULL,
            PRIMARY KEY (
                source_identity, source_profile, locator_identity,
                evaluator_version
            )
        )
        """
    )
    connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")


def _bounded_size(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationCacheError("cached serialized size is invalid")
    return value


__all__ = [
    "CACHE_FILENAME",
    "CACHE_SCHEMA_VERSION",
    "EVIDENCE_SELECTION_VERSION",
    "MAX_SELECTION_BYTES",
    "MAX_SELECTION_CACHE_BYTES",
    "ValidationCache",
    "ValidationCacheError",
]
