"""Project-level incremental cache for local content observations.

The cache is generated acceleration state. Authored fingerprints remain the
identity contract, and validation always compares them with a current
filesystem observation.
"""

from __future__ import annotations

import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast
from urllib.parse import quote

from research_log_data import (
    DIGEST_RE,
    DataContractError,
    DirectoryFingerprintEntry,
    Fingerprint,
    FingerprintObservation,
    InputResource,
    compose_directory_fingerprint,
    compose_identity_files_fingerprint,
    compose_identity_patterns_fingerprint,
    identity_file_paths,
    identity_pattern_paths,
    observe_directory_tree,
    observe_file_content,
    validate_fingerprint_observation,
    verify_fingerprint,
)

from .filesystem import FileIdentity, file_identity
from .sqlite_support import is_sqlite_corruption

CACHE_FILENAME = "research-log-fingerprints.sqlite3"
CACHE_SCHEMA_VERSION = 1
PROJECT_MARKER = ".git"
BUSY_TIMEOUT_MILLISECONDS = 60_000
LOCK_RETRY_SECONDS = 0.01
SQLITE_BUSY_CODE = 5
SQLITE_LOCKED_CODE = 6
SQLITE_CONTENTION_CODES = frozenset({SQLITE_BUSY_CODE, SQLITE_LOCKED_CODE})
CACHE_COMPANION_SUFFIXES = ("", "-journal", "-shm", "-wal")
CACHE_TABLE_COLUMNS = {
    "file_observations": (
        ("path", "TEXT", 0, None, 1),
        ("size", "INTEGER", 1, None, 0),
        ("mtime_ns", "INTEGER", 1, None, 0),
        ("ctime_ns", "INTEGER", 1, None, 0),
        ("algorithm", "TEXT", 1, None, 0),
        ("digest", "TEXT", 1, None, 0),
    ),
    "directory_observations": (
        ("path", "TEXT", 0, None, 1),
        ("metadata_sha256", "TEXT", 1, None, 0),
        ("algorithm", "TEXT", 1, None, 0),
        ("digest", "TEXT", 1, None, 0),
        ("hydrated", "INTEGER", 1, None, 0),
    ),
    "directory_members": (
        ("directory_path", "TEXT", 1, None, 1),
        ("member_path", "TEXT", 1, None, 2),
        ("member_kind", "TEXT", 1, None, 0),
    ),
}
DIRECTORY_MEMBER_FOREIGN_KEYS = (
    (
        "directory_observations",
        "directory_path",
        "path",
        "NO ACTION",
        "CASCADE",
        "NONE",
    ),
)


class FingerprintCacheError(RuntimeError):
    """Raised when the generated fingerprint cache cannot be used safely."""


@dataclass(frozen=True)
class FingerprintCacheMetrics:
    """Diagnostic counters for one cache session."""

    file_hashes: int
    file_reuses: int
    directory_reuses: int
    directories_hydrated: int

    def as_dict(self) -> dict[str, int]:
        """Return stable metric names for the validation result envelope."""

        return {
            "fingerprint_cache_directory_reuses": self.directory_reuses,
            "fingerprint_cache_directories_hydrated": self.directories_hydrated,
            "fingerprint_cache_file_hashes": self.file_hashes,
            "fingerprint_cache_file_reuses": self.file_reuses,
        }


class FingerprintCache:
    """One project-owned SQLite cache of observed local content identities.

    The database lives at ``<project>/.cache/research-log-fingerprints.sqlite3``.
    Read-only sessions may reuse observations but never create or update cache
    state. Writable sessions persist each completed file observation in its own
    transaction so interruption does not discard completed hashing work.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        writable: bool,
        reuse: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.path = self.project_root / ".cache" / CACHE_FILENAME
        self.writable = writable
        self.reuse = reuse
        self._connection: sqlite3.Connection | None = None
        self._file_hashes = 0
        self._file_reuses = 0
        self._directory_reuses = 0
        self._directories_hydrated = 0

    def __enter__(self) -> FingerprintCache:
        """Open the cache with the requested read/write lifecycle."""

        self._connection = self._open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the SQLite connection without suppressing caller failures."""

        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def metrics(self) -> FingerprintCacheMetrics:
        """Return counters accumulated by this cache session."""

        return FingerprintCacheMetrics(
            file_hashes=self._file_hashes,
            file_reuses=self._file_reuses,
            directory_reuses=self._directory_reuses,
            directories_hydrated=self._directories_hydrated,
        )

    def verify(self, resource: InputResource) -> FingerprintObservation | None:
        """Observe one local resource and compare it with its authored identity."""

        if resource.remote:
            return None
        path = Path(resource.canonical_target)
        if (
            path.is_symlink()
            or (resource.kind == "file" and not path.is_file())
            or (resource.kind == "directory" and not path.is_dir())
        ):
            return self._verify_without_cache(resource)
        if resource.kind == "file":
            digest, identity, reused = self._observe_file(path)
            observation = FingerprintObservation(
                Fingerprint("sha256", digest=digest),
                cache_identity=identity,
                identity_reused=reused,
            )
        else:
            if resource.fingerprint.algorithm == "identity-files-sha256-v1":
                observation = self._observe_identity_files(resource)
            elif resource.fingerprint.algorithm == "identity-patterns-sha256-v1":
                observation = self._observe_identity_patterns(resource)
            else:
                observation = self._observe_directory(path)
        return validate_fingerprint_observation(resource, observation)

    def observe_regular_file(self, path: Path) -> FingerprintObservation:
        """Return one current strong identity without an authored expectation.

        The path must identify a regular non-symlink file. Reuse and stable
        observation follow the same Phase 10 rules as declared file inputs.
        """

        if path.is_symlink() or not path.is_file():
            raise FingerprintCacheError(
                f"strong identity requires a regular non-symlink file: {path}"
            )
        path = path.resolve()
        digest, identity, reused = self._observe_file(path)
        return FingerprintObservation(
            Fingerprint("sha256", digest=digest),
            cache_identity=identity,
            identity_reused=reused,
        )

    def remember_regular_file(
        self,
        path: Path,
        *,
        digest: str,
        expected_size: int,
        expected_identity: FileIdentity,
    ) -> bool:
        """Record bytes just published by the caller without rereading them.

        The caller must supply the exact identity returned by publication.
        Any replacement before or during this transaction makes the observation
        ineligible.
        """

        connection = self._connection
        if (
            not self.writable
            or connection is None
            or DIGEST_RE.fullmatch(digest) is None
            or expected_size < 0
        ):
            return False
        try:
            path = path.parent.resolve() / path.name
            current = path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or file_identity(current) != expected_identity
            ):
                return False
            before = _current_file_identity(path)
            if before["size"] != expected_size:
                return False
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO file_observations
                    (path, size, mtime_ns, ctime_ns, algorithm, digest)
                VALUES (?, ?, ?, ?, 'sha256', ?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    ctime_ns=excluded.ctime_ns,
                    algorithm=excluded.algorithm,
                    digest=excluded.digest
                """,
                (
                    path.as_posix(),
                    before["size"],
                    before["mtime_ns"],
                    before["ctime_ns"],
                    digest,
                ),
            )
            after = _current_file_identity(path)
            final = path.lstat()
            if (
                after != before
                or not stat.S_ISREG(final.st_mode)
                or file_identity(final) != expected_identity
            ):
                connection.rollback()
                return False
            connection.commit()
            return True
        except (DataContractError, OSError, sqlite3.DatabaseError):
            if connection.in_transaction:
                connection.rollback()
            return False

    def _verify_without_cache(
        self, resource: InputResource
    ) -> FingerprintObservation | None:
        return verify_fingerprint(resource)

    def _open(self) -> sqlite3.Connection | None:
        if not self.writable and not self.reuse:
            return None
        self._validate_path()
        if not self.writable and not self.path.is_file():
            return None
        try:
            return self._open_once()
        except FingerprintCacheError:
            return None
        except sqlite3.DatabaseError as error:
            if not self.writable:
                return None
            if not is_sqlite_corruption(error):
                raise FingerprintCacheError(
                    f"invalid fingerprint cache {self.path}: {error}"
                ) from error
            self._discard_corrupt_cache()
            try:
                return self._open_once()
            except sqlite3.DatabaseError as retry_error:
                raise FingerprintCacheError(
                    f"could not rebuild invalid fingerprint cache {self.path}: "
                    f"{retry_error}"
                ) from retry_error

    def _open_once(self) -> sqlite3.Connection:
        if self.writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                timeout=BUSY_TIMEOUT_MILLISECONDS / 1000,
            )
        else:
            uri = f"file:{quote(self.path.as_posix())}?mode=ro"
            connection = sqlite3.connect(
                uri,
                isolation_level=None,
                timeout=BUSY_TIMEOUT_MILLISECONDS / 1000,
                uri=True,
            )
        try:
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
            connection.execute("PRAGMA foreign_keys=ON")
            self._prepare_schema(connection)
            if self.writable:
                row = self._execute_with_contention_retry(
                    connection, "PRAGMA journal_mode=WAL"
                ).fetchone()
                if row is None or str(row[0]).lower() != "wal":
                    raise sqlite3.DatabaseError(
                        f"could not enable WAL for fingerprint cache {self.path}"
                    )
        except Exception:
            connection.close()
            raise
        return connection

    def _discard_corrupt_cache(self) -> None:
        for suffix in CACHE_COMPANION_SUFFIXES:
            path = Path(f"{self.path}{suffix}")
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise FingerprintCacheError(
                    f"could not discard invalid fingerprint cache {path}: {error}"
                ) from error

    def _validate_path(self) -> None:
        if not self.project_root.is_dir() or self.project_root.is_symlink():
            raise FingerprintCacheError(
                f"project root must be a regular directory: {self.project_root}"
            )
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise FingerprintCacheError(
                f"fingerprint cache path must not contain a symlink: {self.path}"
            )
        if self.path.exists() and not self.path.is_file():
            raise FingerprintCacheError(
                f"fingerprint cache must be a regular file: {self.path}"
            )

    def _prepare_schema(self, connection: sqlite3.Connection) -> None:
        if not self.writable:
            self._validate_or_create_schema(connection, allow_create=False)
            return
        self._execute_with_contention_retry(connection, "BEGIN IMMEDIATE")
        try:
            self._validate_or_create_schema(connection, allow_create=True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _validate_or_create_schema(
        self, connection: sqlite3.Connection, *, allow_create: bool
    ) -> None:
        row = connection.execute("PRAGMA user_version").fetchone()
        version = cast(int, row[0])
        if version == 0:
            if not allow_create:
                raise FingerprintCacheError(
                    f"fingerprint cache has no supported schema: {self.path}"
                )
            self._create_schema(connection)
            return
        if version != CACHE_SCHEMA_VERSION:
            raise FingerprintCacheError(
                "unsupported fingerprint cache schema "
                f"{version} at {self.path}; expected {CACHE_SCHEMA_VERSION}"
            )
        self._validate_schema(connection)

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        observed_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if not str(row[0]).startswith("sqlite_")
        }
        if observed_tables != set(CACHE_TABLE_COLUMNS):
            self._invalid_schema()
        for table, expected in CACHE_TABLE_COLUMNS.items():
            observed = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if observed != expected:
                self._invalid_schema()
        foreign_keys = tuple(
            (
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in connection.execute("PRAGMA foreign_key_list(directory_members)")
        )
        if foreign_keys != DIRECTORY_MEMBER_FOREIGN_KEYS:
            self._invalid_schema()

    def _invalid_schema(self) -> None:
        raise sqlite3.DatabaseError(
            f"fingerprint cache schema is incomplete: {self.path}"
        )

    @staticmethod
    def _execute_with_contention_retry(
        connection: sqlite3.Connection, statement: str
    ) -> sqlite3.Cursor:
        deadline = time.monotonic() + BUSY_TIMEOUT_MILLISECONDS / 1000
        while True:
            try:
                return connection.execute(statement)
            except sqlite3.OperationalError as error:
                if not _is_lock_contention(error) or time.monotonic() >= deadline:
                    raise
                time.sleep(LOCK_RETRY_SECONDS)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE file_observations (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                ctime_ns INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                digest TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE directory_observations (
                path TEXT PRIMARY KEY,
                metadata_sha256 TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                digest TEXT NOT NULL,
                hydrated INTEGER NOT NULL CHECK (hydrated IN (0, 1))
            )"""
        )
        connection.execute(
            """CREATE TABLE directory_members (
                directory_path TEXT NOT NULL,
                member_path TEXT NOT NULL,
                member_kind TEXT NOT NULL CHECK (member_kind IN ('directory', 'file')),
                PRIMARY KEY (directory_path, member_path),
                FOREIGN KEY (directory_path)
                    REFERENCES directory_observations(path) ON DELETE CASCADE
            )"""
        )
        connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")

    def _observe_file(self, path: Path) -> tuple[str, Mapping[str, object], bool]:
        identity = _current_file_identity(path)
        if self.reuse:
            cached = self._matching_file(path, identity)
            if cached is not None:
                self._file_reuses += 1
                return cached, identity, True
        if not self.writable or self._connection is None:
            digest, stable = observe_file_content(path)
            self._file_hashes += 1
            return digest, stable, False
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            identity = _current_file_identity(path)
            if self.reuse:
                cached = self._matching_file(path, identity)
                if cached is not None:
                    connection.commit()
                    self._file_reuses += 1
                    return cached, identity, True
            digest, stable = observe_file_content(path)
            connection.execute(
                """
                INSERT INTO file_observations
                    (path, size, mtime_ns, ctime_ns, algorithm, digest)
                VALUES (?, ?, ?, ?, 'sha256', ?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    ctime_ns=excluded.ctime_ns,
                    algorithm=excluded.algorithm,
                    digest=excluded.digest
                """,
                (
                    path.resolve().as_posix(),
                    stable["size"],
                    stable["mtime_ns"],
                    stable["ctime_ns"],
                    digest,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        self._file_hashes += 1
        return digest, stable, False

    def _matching_file(self, path: Path, identity: Mapping[str, object]) -> str | None:
        if self._connection is None:
            return None
        row = self._connection.execute(
            """
            SELECT size, mtime_ns, ctime_ns, algorithm, digest
            FROM file_observations WHERE path=?
            """,
            (path.resolve().as_posix(),),
        ).fetchone()
        if row is None:
            return None
        if (
            row[0] != identity["size"]
            or row[1] != identity["mtime_ns"]
            or row[2] != identity["ctime_ns"]
            or row[3] != "sha256"
            or not isinstance(row[4], str)
            or DIGEST_RE.fullmatch(row[4]) is None
        ):
            return None
        return str(row[4])

    def _observe_directory(self, root: Path) -> FingerprintObservation:
        before_identity, members, member_paths = observe_directory_tree(root)
        cached = self._matching_directory(root, before_identity)
        if cached is not None and cached[1]:
            self._directory_reuses += 1
            return FingerprintObservation(
                Fingerprint("directory-sha256-v1", digest=cached[0]),
                members,
                before_identity,
                identity_reused=True,
            )
        entries: list[DirectoryFingerprintEntry] = []
        for member in members:
            if member.type == "directory":
                entries.append(member)
                continue
            digest, _, _ = self._observe_file(member_paths[member.path])
            entries.append(DirectoryFingerprintEntry(member.path, "file", digest))
        fingerprint = compose_directory_fingerprint(tuple(entries))
        after_identity, after_members, _ = observe_directory_tree(root)
        if before_identity != after_identity or members != after_members:
            raise DataContractError(
                "provenance.observation.unavailable",
                str(root),
                {"reason": "changed_during_hash"},
                "Fingerprints",
            )
        if self.writable and self._connection is not None:
            self._store_directory(root, after_identity, fingerprint, entries)
        self._directories_hydrated += 1
        return FingerprintObservation(fingerprint, tuple(entries), after_identity)

    def _observe_identity_files(
        self, resource: InputResource
    ) -> FingerprintObservation:
        paths = identity_file_paths(resource)
        entries: list[DirectoryFingerprintEntry] = []
        identities: list[Mapping[str, object]] = []
        all_reused = True
        for relative, path in paths.items():
            digest, identity, reused = self._observe_file(path)
            entries.append(DirectoryFingerprintEntry(relative, "file", digest))
            identities.append({"path": relative, **identity})
            all_reused = all_reused and reused
        for identity in identities:
            raw_relative = identity["path"]
            assert isinstance(raw_relative, str)
            relative = raw_relative
            try:
                observed = _current_file_identity(paths[relative])
            except DataContractError as error:
                raise DataContractError(
                    "provenance.observation.unavailable",
                    resource.name,
                    {"error": str(error), "file": relative},
                    "Identity Files",
                ) from error
            expected = {key: value for key, value in identity.items() if key != "path"}
            if observed != expected:
                raise DataContractError(
                    "provenance.observation.unavailable",
                    resource.name,
                    {"file": relative, "reason": "changed_during_hash"},
                    "Identity Files",
                )
        fingerprint = compose_identity_files_fingerprint(tuple(entries))
        return FingerprintObservation(
            fingerprint,
            tuple(entries),
            {"files": identities, "kind": "identity-files"},
            identity_reused=all_reused,
        )

    def _observe_identity_patterns(
        self, resource: InputResource
    ) -> FingerprintObservation:
        paths = identity_pattern_paths(resource)
        entries: list[DirectoryFingerprintEntry] = []
        identities: list[Mapping[str, object]] = []
        all_reused = True
        for relative, path in paths.items():
            digest, identity, reused = self._observe_file(path)
            entries.append(DirectoryFingerprintEntry(relative, "file", digest))
            identities.append({"path": relative, **identity})
            all_reused = all_reused and reused
        current_paths = identity_pattern_paths(resource)
        if current_paths != paths:
            raise DataContractError(
                "provenance.observation.unavailable",
                resource.name,
                {"reason": "pattern_matches_changed"},
                "Identity Patterns",
            )
        for identity in identities:
            raw_relative = identity["path"]
            assert isinstance(raw_relative, str)
            relative = raw_relative
            try:
                observed = _current_file_identity(paths[relative])
            except DataContractError as error:
                raise DataContractError(
                    "provenance.observation.unavailable",
                    resource.name,
                    {"error": str(error), "file": relative},
                    "Identity Patterns",
                ) from error
            expected = {key: value for key, value in identity.items() if key != "path"}
            if observed != expected:
                raise DataContractError(
                    "provenance.observation.unavailable",
                    resource.name,
                    {"file": relative, "reason": "changed_during_hash"},
                    "Identity Patterns",
                )
        fingerprint = compose_identity_patterns_fingerprint(
            tuple(entries),
            resource.fingerprint.patterns,
        )
        return FingerprintObservation(
            fingerprint,
            tuple(entries),
            {"files": identities, "kind": "identity-patterns"},
            identity_reused=all_reused,
        )

    def _matching_directory(
        self, root: Path, identity: Mapping[str, object]
    ) -> tuple[str, bool] | None:
        if not self.reuse or self._connection is None:
            return None
        row = self._connection.execute(
            """
            SELECT metadata_sha256, algorithm, digest, hydrated
            FROM directory_observations WHERE path=?
            """,
            (root.resolve().as_posix(),),
        ).fetchone()
        if (
            row is None
            or row[0] != identity.get("metadata_sha256")
            or row[1] != "directory-sha256-v1"
            or not isinstance(row[2], str)
            or DIGEST_RE.fullmatch(row[2]) is None
        ):
            return None
        return str(row[2]), bool(row[3])

    def _store_directory(
        self,
        root: Path,
        identity: Mapping[str, object],
        fingerprint: Fingerprint,
        entries: list[DirectoryFingerprintEntry],
    ) -> None:
        assert self._connection is not None and fingerprint.digest is not None
        path = root.resolve().as_posix()
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO directory_observations
                    (path, metadata_sha256, algorithm, digest, hydrated)
                VALUES (?, ?, 'directory-sha256-v1', ?, 1)
                ON CONFLICT(path) DO UPDATE SET
                    metadata_sha256=excluded.metadata_sha256,
                    algorithm=excluded.algorithm,
                    digest=excluded.digest,
                    hydrated=1
                """,
                (path, identity["metadata_sha256"], fingerprint.digest),
            )
            connection.execute(
                "DELETE FROM directory_members WHERE directory_path=?", (path,)
            )
            connection.executemany(
                """
                INSERT INTO directory_members
                    (directory_path, member_path, member_kind)
                VALUES (?, ?, ?)
                """,
                ((path, entry.path, entry.type) for entry in entries),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

def project_root(summary: Path) -> Path:
    """Return the nearest Git worktree owning a maintained-log summary."""

    summary = summary.resolve()
    for candidate in summary.parents:
        marker = candidate / PROJECT_MARKER
        if not marker.is_symlink() and (marker.is_file() or marker.is_dir()):
            return candidate
    raise FingerprintCacheError(
        f"could not resolve project root from Git metadata: {summary}"
    )


def _current_file_identity(path: Path) -> Mapping[str, object]:
    try:
        observation = path.lstat()
    except OSError as error:
        raise DataContractError(
            "provenance.observation.unavailable",
            str(path),
            {"error": str(error)},
            "Fingerprints",
        ) from error
    if not stat.S_ISREG(observation.st_mode) or path.is_symlink():
        raise DataContractError(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "not_regular_non_symlink_file"},
            "Fingerprints",
        )
    return {
        "ctime_ns": observation.st_ctime_ns,
        "kind": "file",
        "mtime_ns": observation.st_mtime_ns,
        "size": observation.st_size,
    }


def _is_lock_contention(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in SQLITE_CONTENTION_CODES
    message = str(error).lower()
    return "database is locked" in message or "database table is locked" in message
