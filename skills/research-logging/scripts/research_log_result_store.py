"""Shared lifecycle for disposable queryable research-log result storage.

The database at ``<log>/.cache/results.sqlite`` is intentionally separate
from durable run state.  This module owns only database safety, schema
initialization, and transaction boundaries; validation and reproduction own
their domain tables and row decoders.
"""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from research_log_paths import RESULTS_STORE
from validation.operation_state import operation_lock

STORE_VERSION = 19
REPLACEABLE_STORE_VERSIONS = frozenset({17, 18})
UNCHANGED_DOMAIN_STORE_VERSIONS = frozenset({19, 20, 21, 22})
_COMPANIONS = ("-journal", "-wal", "-shm")

_SHARED_DDL = """
CREATE TABLE store_state (
    domain TEXT PRIMARY KEY CHECK(domain IN
        ('validation', 'reproduction', 'command')),
    generation INTEGER NOT NULL CHECK(generation >= 1),
    summary TEXT NOT NULL
);
CREATE TABLE report_materializations (
    kind TEXT PRIMARY KEY CHECK(kind IN ('validation', 'reproduction')),
    source_generation INTEGER NOT NULL CHECK(source_generation >= 1),
    content_sha256 TEXT NOT NULL,
    rendered_at TEXT NOT NULL
);
"""

_VALIDATION_V19_DDL = """
CREATE TABLE validation_snapshots (
    snapshot_pk INTEGER PRIMARY KEY CHECK(snapshot_pk >= 1),
    snapshot_id TEXT UNIQUE NOT NULL,
    generation INTEGER UNIQUE NOT NULL CHECK(generation >= 1),
    slot TEXT UNIQUE NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('log', 'entry')),
    target_log TEXT NOT NULL,
    target_entry TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('clear', 'findings', 'failed')),
    passed_check_count INTEGER NOT NULL CHECK(passed_check_count >= 0),
    finding_count INTEGER NOT NULL CHECK(finding_count >= 0),
    batch_count INTEGER NOT NULL CHECK(batch_count >= 0),
    blocked_count INTEGER NOT NULL CHECK(blocked_count >= 0),
    failed_count INTEGER NOT NULL CHECK(failed_count >= 0),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    report_context_json TEXT NOT NULL,
    CHECK((target_kind = 'log' AND target_entry IS NULL) OR
          (target_kind = 'entry' AND target_entry IS NOT NULL)),
    CHECK((outcome = 'clear' AND failed_count = 0 AND finding_count = 0
                              AND batch_count = 0) OR
          (outcome = 'findings' AND failed_count = 0 AND finding_count >= 1
                                 AND batch_count >= 1) OR
          (outcome = 'failed' AND failed_count >= 1))
);
CREATE TABLE validation_snapshot_entries (
    snapshot_pk INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN
        ('requested', 'evaluated', 'dependency', 'repair', 'context')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, relation, position),
    UNIQUE(snapshot_pk, relation, entry),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_findings (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL CHECK(finding_pk >= 1),
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    type TEXT NOT NULL CHECK(type IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    code TEXT NOT NULL,
    entry TEXT,
    subject TEXT NOT NULL,
    rule TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    admission_owner TEXT CHECK(admission_owner IS NULL OR admission_owner IN
        ('execution', 'command', 'material', 'entry', 'log')),
    PRIMARY KEY(snapshot_pk, finding_pk),
    UNIQUE(snapshot_pk, finding_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_blocked_checks (
    snapshot_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL CHECK(check_pk >= 1),
    check_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    area TEXT NOT NULL CHECK(area IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    entry TEXT,
    subject TEXT NOT NULL,
    rule TEXT NOT NULL,
    blocked_by_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, check_pk),
    UNIQUE(snapshot_pk, check_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_failed_checks (
    snapshot_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL CHECK(check_pk >= 1),
    check_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    area TEXT NOT NULL CHECK(area IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    entry TEXT,
    subject TEXT NOT NULL,
    code TEXT NOT NULL,
    rule TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN
        ('read', 'observe', 'cache', 'graph', 'other')),
    reason_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, check_pk),
    UNIQUE(snapshot_pk, check_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_causes (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    cause_finding_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, cause_finding_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, cause_finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_source_locations (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    line INTEGER CHECK(line IS NULL OR line >= 1),
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    repair_key_pk INTEGER NOT NULL CHECK(repair_key_pk >= 1),
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, repair_key_pk),
    UNIQUE(snapshot_pk, kind, identity),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    repair_key_pk INTEGER NOT NULL,
    repair_entry TEXT,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, repair_key_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, repair_key_pk)
        REFERENCES validation_repair_keys(snapshot_pk, repair_key_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_nodes (
    snapshot_pk INTEGER NOT NULL,
    node_pk INTEGER NOT NULL CHECK(node_pk >= 1),
    node_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    entry TEXT,
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, node_pk),
    UNIQUE(snapshot_pk, node_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_edges (
    snapshot_pk INTEGER NOT NULL,
    edge_pk INTEGER NOT NULL CHECK(edge_pk >= 1),
    position INTEGER NOT NULL CHECK(position >= 0),
    relation_kind TEXT NOT NULL CHECK(relation_kind IN
        ('established', 'ambiguous')),
    kind TEXT NOT NULL,
    subject_node_pk INTEGER NOT NULL,
    observed_json TEXT,
    PRIMARY KEY(snapshot_pk, edge_pk),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk, subject_node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE,
    CHECK((relation_kind = 'established' AND observed_json IS NULL) OR
          (relation_kind = 'ambiguous' AND observed_json IS NOT NULL))
) WITHOUT ROWID;
CREATE TABLE validation_repair_edge_targets (
    snapshot_pk INTEGER NOT NULL,
    edge_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    target_node_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, edge_pk, position),
    UNIQUE(snapshot_pk, edge_pk, target_node_pk),
    FOREIGN KEY(snapshot_pk, edge_pk)
        REFERENCES validation_repair_edges(snapshot_pk, edge_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, target_node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_nodes (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    role TEXT NOT NULL CHECK(role IN ('context', 'repair')),
    node_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, node_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batches (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL CHECK(batch_pk >= 1),
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    focus_finding_pk INTEGER NOT NULL,
    finding_count INTEGER NOT NULL CHECK(finding_count >= 1),
    PRIMARY KEY(snapshot_pk, batch_pk),
    UNIQUE(snapshot_pk, batch_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, focus_finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk)
) WITHOUT ROWID;
CREATE TABLE validation_batch_rationale (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    rationale TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_findings (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    finding_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    UNIQUE(snapshot_pk, finding_pk),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    repair_key_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    UNIQUE(snapshot_pk, batch_pk, repair_key_pk),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, repair_key_pk)
        REFERENCES validation_repair_keys(snapshot_pk, repair_key_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_entries (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('repair', 'context')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, role, position),
    UNIQUE(snapshot_pk, batch_pk, role, entry),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS command_diagnostics (
    diagnostic_pk INTEGER PRIMARY KEY CHECK(diagnostic_pk >= 1),
    diagnostic_id TEXT UNIQUE NOT NULL,
    generation INTEGER UNIQUE NOT NULL CHECK(generation >= 1),
    operation TEXT NOT NULL,
    code TEXT NOT NULL,
    entry TEXT,
    summary TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_diagnostic_records (
    diagnostic_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    kind TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(diagnostic_pk, position),
    FOREIGN KEY(diagnostic_pk) REFERENCES command_diagnostics(diagnostic_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX validation_snapshots_slot_generation
    ON validation_snapshots(slot, generation DESC);
CREATE INDEX validation_snapshot_entries_lookup
    ON validation_snapshot_entries(snapshot_pk, entry, relation);
CREATE INDEX validation_findings_type_order
    ON validation_findings(snapshot_pk, type, position);
CREATE INDEX validation_findings_entry_order
    ON validation_findings(snapshot_pk, entry, position);
CREATE INDEX validation_findings_id
    ON validation_findings(snapshot_pk, finding_id);
CREATE INDEX validation_blocked_checks_order
    ON validation_blocked_checks(snapshot_pk, position);
CREATE INDEX validation_failed_checks_order
    ON validation_failed_checks(snapshot_pk, position);
CREATE INDEX validation_batches_order
    ON validation_batches(snapshot_pk, position);
CREATE INDEX validation_batches_id
    ON validation_batches(snapshot_pk, batch_id);
CREATE INDEX validation_batch_entries_lookup
    ON validation_batch_entries(snapshot_pk, entry, batch_pk, role);
CREATE INDEX validation_batch_findings_finding
    ON validation_batch_findings(snapshot_pk, finding_pk, batch_pk);
CREATE INDEX validation_finding_nodes_node
    ON validation_finding_nodes(snapshot_pk, node_pk, finding_pk);
CREATE INDEX validation_repair_edges_subject
    ON validation_repair_edges(snapshot_pk, subject_node_pk, edge_pk);
CREATE INDEX validation_repair_edge_targets_target
    ON validation_repair_edge_targets(snapshot_pk, target_node_pk, edge_pk);
CREATE INDEX IF NOT EXISTS command_diagnostics_generation
    ON command_diagnostics(generation DESC, diagnostic_pk);
"""

_REPLACEABLE_VALIDATION_TABLES = (
    "validation_batch_entries",
    "validation_batch_repair_keys",
    "validation_batch_findings",
    "validation_batch_nodes",
    "validation_batch_rationale",
    "validation_batches",
    "validation_finding_nodes",
    "validation_repair_edge_targets",
    "validation_repair_edges",
    "validation_repair_nodes",
    "validation_finding_repair_keys",
    "validation_repair_keys",
    "validation_finding_source_locations",
    "validation_finding_causes",
    "validation_failed_checks",
    "validation_blocked_checks",
    "validation_not_evaluated",
    "validation_findings",
    "validation_snapshot_entries",
    "validation_snapshots",
)


class ResultStoreError(RuntimeError):
    """A bounded result-store failure that never authorizes source evaluation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def result_store_path(log_root: Path) -> Path:
    """Return the owned consolidated result-store path for a regular log root."""

    return log_root / RESULTS_STORE


@contextmanager
def results_lock(log_root: Path) -> Iterator[None]:
    """Serialize short result-store publication, rendering, and deletion work.

    Callers acquire their operation lock first.  Evaluation, command execution,
    comparison, and other scientific I/O must complete before this lock.
    """

    with operation_lock(log_root, "results.lock", mode="exclusive"):
        yield


def _checked_path(log_root: Path, *, writable: bool) -> Path:
    """Reject symlinked database paths and create only the owned cache directory."""

    path = result_store_path(log_root)
    for candidate in (
        log_root,
        path.parent,
        path,
        *(Path(str(path) + suffix) for suffix in _COMPANIONS),
    ):
        if candidate.is_symlink():
            raise ResultStoreError("results.store.malformed", f"symlink: {candidate}")
    if not log_root.is_dir():
        raise ResultStoreError(
            "results.store.malformed", f"log root is unavailable: {log_root}"
        )
    if writable:
        path.parent.mkdir(mode=0o755, exist_ok=True)
    elif not path.is_file():
        raise ResultStoreError("results.store.missing", "result store is absent")
    return path


def _open(path: Path, *, writable: bool) -> sqlite3.Connection:
    """Open one database after checking its exact supported schema version."""

    try:
        return _open_configured(path, writable=writable)
    except ResultStoreError:
        raise
    except sqlite3.OperationalError as error:
        raise ResultStoreError(_sqlite_error_code(error), str(error)[:1024]) from error
    except sqlite3.Error as error:
        raise ResultStoreError("results.store.malformed", str(error)[:1024]) from error


def _open_configured(path: Path, *, writable: bool) -> sqlite3.Connection:
    """Configure a SQLite connection and reject unsupported store versions."""

    uri = path.as_uri() + ("?mode=rwc" if writable else "?mode=ro")
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if writable:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and writable:
            return db
        if version not in {
            *REPLACEABLE_STORE_VERSIONS,
            *UNCHANGED_DOMAIN_STORE_VERSIONS,
        }:
            raise ResultStoreError(
                "results.schema.unsupported", f"store version {version} is unsupported"
            )
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def _sqlite_error_code(error: sqlite3.OperationalError) -> str:
    """Classify SQLite contention without recasting corrupt stores as busy."""

    text = str(error).lower()
    return (
        "results.store.busy"
        if "locked" in text or "busy" in text
        else "results.store.malformed"
    )


def _initialize_schema(db: sqlite3.Connection) -> None:
    """Create the first store schema inside the caller's open transaction."""

    _execute_ddl(db, _SHARED_DDL)
    _execute_ddl(db, _VALIDATION_V19_DDL)
    db.execute(f"PRAGMA user_version={STORE_VERSION}")


def replace_validation_schema(db: sqlite3.Connection) -> None:
    """Replace only prior validation-owned tables in the active transaction."""

    db.execute("DELETE FROM report_materializations WHERE kind='validation'")
    for table in _REPLACEABLE_VALIDATION_TABLES:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    _execute_ddl(db, _VALIDATION_V19_DDL)


def _execute_ddl(db: sqlite3.Connection, ddl: str) -> None:
    statement = ""
    for line in ddl.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ResultStoreError("results.store.malformed", "incomplete store schema")


@contextmanager
def result_transaction(log_root: Path) -> Iterator[sqlite3.Connection]:
    """Yield one exclusive durable transaction for a domain storage owner.

    Callers must finish all evaluation and report composition outside this
    context.  On failure every row change rolls back, preserving the prior
    completed domain generation.
    """

    db: sqlite3.Connection | None = None
    try:
        db = _open(_checked_path(log_root, writable=True), writable=True)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            _initialize_schema(db)
        yield db
        db.commit()
    except BaseException as error:
        _rollback(db)
        _raise_transaction_error(error)
    finally:
        if db is not None:
            db.close()


def _rollback(db: sqlite3.Connection | None) -> None:
    if db is not None:
        db.rollback()


def _raise_transaction_error(error: BaseException) -> None:
    """Raise the public result-store error for one failed transaction."""

    if isinstance(error, ResultStoreError):
        raise error
    if isinstance(error, sqlite3.OperationalError):
        raise ResultStoreError(_sqlite_error_code(error), str(error)[:1024]) from error
    if isinstance(error, sqlite3.Error):
        raise ResultStoreError("results.store.malformed", str(error)[:1024]) from error
    raise error


@contextmanager
def result_snapshot(log_root: Path) -> Iterator[sqlite3.Connection]:
    """Yield one read-only SQLite snapshot without acquiring an operation lock."""

    db: sqlite3.Connection | None = None
    try:
        db = _open(_checked_path(log_root, writable=False), writable=False)
        db.execute("BEGIN")
        yield db
        db.commit()
    except BaseException as error:
        _rollback(db)
        _raise_transaction_error(error)
    finally:
        if db is not None:
            db.close()


def record_report_materialization(
    log_root: Path, kind: str, content: bytes, *, expected_generation: int | None = None
) -> bool:
    """Record a completed derived Markdown write for its unchanged domain generation."""

    if kind not in {"validation", "reproduction"}:
        raise ResultStoreError("results.report.invalid", f"unknown report kind: {kind}")
    with result_transaction(log_root) as db:
        row = db.execute(
            "SELECT generation FROM store_state WHERE domain=?", (kind,)
        ).fetchone()
        if row is None:
            raise ResultStoreError("results.store.missing", f"no {kind} result")
        generation = int(row[0])
        if expected_generation is not None and generation != expected_generation:
            return False
        db.execute(
            "INSERT INTO report_materializations VALUES (?, ?, ?, ?) "
            "ON CONFLICT(kind) DO UPDATE SET source_generation=excluded.source_generation, "
            "content_sha256=excluded.content_sha256, rendered_at=excluded.rendered_at",
            (
                kind,
                generation,
                hashlib.sha256(content).hexdigest(),
                datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            ),
        )
    return True


def invalidate_report_materialization(log_root: Path, kind: str) -> None:
    """Mark one derived report stale without changing its committed result."""

    if kind not in {"validation", "reproduction"}:
        raise ResultStoreError("results.report.invalid", f"unknown report kind: {kind}")
    with result_transaction(log_root) as db:
        db.execute("DELETE FROM report_materializations WHERE kind=?", (kind,))


def result_generation(log_root: Path, kind: str) -> int:
    """Return the committed generation for one result domain."""

    if kind not in {"validation", "reproduction"}:
        raise ResultStoreError("results.report.invalid", f"unknown report kind: {kind}")
    with result_snapshot(log_root) as db:
        row = db.execute(
            "SELECT generation FROM store_state WHERE domain=?", (kind,)
        ).fetchone()
    if row is None:
        raise ResultStoreError("results.store.missing", f"no {kind} result")
    return int(row[0])


def clear_validation_snapshots(log_root: Path) -> None:
    """Clear only disposable validation rows and its derived report marker."""

    with results_lock(log_root):
        with result_transaction(log_root) as db:
            db.execute("DELETE FROM validation_snapshots")
            db.execute("DELETE FROM store_state WHERE domain='validation'")
            db.execute("DELETE FROM report_materializations WHERE kind='validation'")


def clear_reproduction_results(log_root: Path) -> None:
    """Clear only disposable reproduction rows and its derived report marker."""

    from log_commands.reproduction_saved_storage import clear_saved_reproduction

    with results_lock(log_root):
        with result_transaction(log_root) as db:
            clear_saved_reproduction(db)


def clear_result_store(log_root: Path) -> None:
    """Remove the disposable consolidated database without touching reports/jobs."""

    with results_lock(log_root):
        path = result_store_path(log_root)
        for candidate in (path, *(Path(str(path) + suffix) for suffix in _COMPANIONS)):
            if candidate.exists() or candidate.is_symlink():
                if candidate.is_symlink() or not candidate.is_file():
                    raise ResultStoreError(
                        "results.store.malformed", f"unsafe store path: {candidate}"
                    )
                candidate.unlink()
