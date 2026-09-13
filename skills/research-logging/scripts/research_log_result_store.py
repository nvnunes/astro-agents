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

STORE_VERSION = 15
_COMPANIONS = ("-journal", "-wal", "-shm")

# The v14 layout keeps public identifiers at the API boundary while compact
# per-result integer identities own all internal validation relationships.
_DDL = """
CREATE TABLE store_state (
    domain TEXT PRIMARY KEY CHECK(domain IN ('validation', 'reproduction')),
    generation INTEGER NOT NULL CHECK(generation >= 1),
    summary TEXT NOT NULL
);
CREATE TABLE report_materializations (
    kind TEXT PRIMARY KEY CHECK(kind IN ('validation', 'reproduction')),
    source_generation INTEGER NOT NULL CHECK(source_generation >= 1),
    content_sha256 TEXT NOT NULL,
    rendered_at TEXT NOT NULL
);
CREATE TABLE validation_results (
    result_pk INTEGER PRIMARY KEY CHECK(result_pk >= 1),
    result_id TEXT UNIQUE NOT NULL,
    generation INTEGER UNIQUE NOT NULL CHECK(generation >= 1),
    slot TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('full', 'entry', 'diagnostic')),
    entry TEXT,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    evaluated_checks INTEGER,
    finding_count INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    result_date TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    source_identity TEXT,
    validation_id TEXT,
    record_identity TEXT,
    projection_schema TEXT,
    report_context_json TEXT
);
CREATE TABLE validation_result_entries (
    result_pk INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN ('requested', 'evaluated', 'dependency')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(result_pk, relation, position),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_result_limitations (
    result_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    code TEXT NOT NULL,
    PRIMARY KEY(result_pk, position),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_codes (
    result_pk INTEGER NOT NULL,
    code_pk INTEGER NOT NULL CHECK(code_pk >= 1),
    code TEXT NOT NULL,
    PRIMARY KEY(result_pk, code_pk),
    UNIQUE(result_pk, code),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_checks (
    result_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL CHECK(check_pk >= 1),
    check_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    subject TEXT NOT NULL,
    code_pk INTEGER,
    rule TEXT,
    observed_json TEXT,
    failure_dependency TEXT,
    PRIMARY KEY(result_pk, check_pk),
    UNIQUE(result_pk, check_id),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, code_pk) REFERENCES validation_codes(result_pk, code_pk),
    CHECK(
        (status IN ('fail', 'unavailable') AND code_pk IS NOT NULL AND rule IS NOT NULL AND observed_json IS NOT NULL)
        OR
        (status NOT IN ('fail', 'unavailable') AND code_pk IS NULL AND rule IS NULL AND observed_json IS NULL AND failure_dependency IS NULL)
    )
) WITHOUT ROWID;
CREATE TABLE validation_check_dependencies (
    result_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    dependency_json TEXT NOT NULL,
    PRIMARY KEY(result_pk, check_pk, position),
    FOREIGN KEY(result_pk, check_pk) REFERENCES validation_checks(result_pk, check_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_groups (
    result_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL CHECK(group_pk >= 1),
    group_id TEXT NOT NULL,
    group_kind TEXT NOT NULL CHECK(group_kind IN ('chain', 'unresolved')),
    entry TEXT NOT NULL,
    reason TEXT,
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(result_pk, group_pk),
    UNIQUE(result_pk, group_id),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_findings (
    result_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    admission_effect TEXT NOT NULL,
    display_entry TEXT NOT NULL,
    display_subject TEXT NOT NULL,
    PRIMARY KEY(result_pk, check_pk),
    FOREIGN KEY(result_pk, check_pk) REFERENCES validation_checks(result_pk, check_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_affected_chains (
    result_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    group_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, check_pk, position),
    FOREIGN KEY(result_pk, check_pk) REFERENCES validation_findings(result_pk, check_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;
CREATE TABLE validation_finding_affected_entries (
    result_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(result_pk, check_pk, position),
    FOREIGN KEY(result_pk, check_pk) REFERENCES validation_findings(result_pk, check_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_commands (
    result_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL CHECK(command_pk >= 1),
    command_id TEXT NOT NULL,
    entry TEXT NOT NULL,
    document TEXT NOT NULL,
    fence INTEGER NOT NULL CHECK(fence >= 0),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    script TEXT NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(result_pk, command_pk),
    UNIQUE(result_pk, command_id),
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_command_tokens (
    result_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    token TEXT NOT NULL,
    PRIMARY KEY(result_pk, command_pk, position),
    FOREIGN KEY(result_pk, command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_artifacts (
    result_pk INTEGER NOT NULL,
    artifact_pk INTEGER NOT NULL CHECK(artifact_pk >= 1),
    artifact_id TEXT NOT NULL,
    PRIMARY KEY(result_pk, artifact_pk),
    UNIQUE(result_pk, artifact_id),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_command_relationships (
    result_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
    position INTEGER NOT NULL CHECK(position >= 0),
    path_artifact_pk INTEGER,
    path_text TEXT,
    proof TEXT NOT NULL,
    target TEXT,
    artifact TEXT,
    origin INTEGER NOT NULL CHECK(origin IN (0, 1)),
    PRIMARY KEY(result_pk, command_pk, direction, position),
    FOREIGN KEY(result_pk, command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, path_artifact_pk) REFERENCES validation_artifacts(result_pk, artifact_pk) ON DELETE CASCADE,
    CHECK((path_artifact_pk IS NULL) != (path_text IS NULL))
) WITHOUT ROWID;
CREATE TABLE validation_command_collections (
    result_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
    mechanism TEXT NOT NULL,
    root TEXT,
    target TEXT NOT NULL,
    PRIMARY KEY(result_pk, command_pk, position),
    FOREIGN KEY(result_pk, command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_collection_members (
    result_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL,
    collection_position INTEGER NOT NULL CHECK(collection_position >= 0),
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    PRIMARY KEY(result_pk, command_pk, collection_position, position),
    FOREIGN KEY(result_pk, command_pk, collection_position)
        REFERENCES validation_command_collections(result_pk, command_pk, position) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_group_artifacts (
    result_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    artifact_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, group_pk, position),
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, artifact_pk) REFERENCES validation_artifacts(result_pk, artifact_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_group_edges (
    result_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_command_pk INTEGER NOT NULL,
    target_command_pk INTEGER NOT NULL,
    artifact_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, group_pk, position),
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, source_command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, target_command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, artifact_pk) REFERENCES validation_artifacts(result_pk, artifact_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_group_signals (
    result_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    signal TEXT NOT NULL,
    PRIMARY KEY(result_pk, group_pk, position),
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_registry_records (
    result_pk INTEGER NOT NULL,
    registry_pk INTEGER NOT NULL CHECK(registry_pk >= 1),
    payload_sha256 TEXT NOT NULL,
    owner_entry TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    location TEXT NOT NULL,
    path TEXT NOT NULL,
    origin INTEGER NOT NULL CHECK(origin IN (0, 1)),
    from_entry TEXT,
    read_only INTEGER CHECK(read_only IN (0, 1)),
    identity_algorithm TEXT NOT NULL,
    identity_commit TEXT,
    PRIMARY KEY(result_pk, registry_pk),
    UNIQUE(result_pk, payload_sha256),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE,
    CHECK((from_entry IS NULL AND read_only IS NULL) OR (from_entry IS NOT NULL AND read_only = 1))
) WITHOUT ROWID;
CREATE TABLE validation_registry_identity_members (
    result_pk INTEGER NOT NULL,
    registry_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    member TEXT NOT NULL,
    PRIMARY KEY(result_pk, registry_pk, position),
    FOREIGN KEY(result_pk, registry_pk) REFERENCES validation_registry_records(result_pk, registry_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_group_registry (
    result_pk INTEGER NOT NULL,
    group_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    registry_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, group_pk, position),
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, registry_pk) REFERENCES validation_registry_records(result_pk, registry_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batches (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL CHECK(batch_pk >= 1),
    batch_id TEXT NOT NULL,
    batch_type TEXT NOT NULL,
    grouping_reason TEXT NOT NULL,
    scope TEXT NOT NULL,
    primary_finding_count INTEGER NOT NULL CHECK(primary_finding_count >= 1),
    starting_check_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(result_pk, batch_pk),
    UNIQUE(result_pk, batch_id),
    FOREIGN KEY(result_pk) REFERENCES validation_results(result_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, starting_check_pk) REFERENCES validation_findings(result_pk, check_pk)
) WITHOUT ROWID;
CREATE TABLE validation_batch_entries (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, position),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_anchors (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    anchor_json TEXT NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, position),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_findings (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    check_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, position),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, check_pk) REFERENCES validation_findings(result_pk, check_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_groups (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    group_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, position),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, group_pk) REFERENCES validation_groups(result_pk, group_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_related_batches (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    related_batch_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, position),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, related_batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;
CREATE TABLE validation_batch_command_links (
    result_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    command_pk INTEGER NOT NULL,
    code_pk INTEGER NOT NULL,
    PRIMARY KEY(result_pk, batch_pk, command_pk, code_pk),
    FOREIGN KEY(result_pk, batch_pk) REFERENCES validation_batches(result_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, command_pk) REFERENCES validation_commands(result_pk, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(result_pk, code_pk) REFERENCES validation_codes(result_pk, code_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE reproduction_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE reproduction_runs (
    run_pk INTEGER PRIMARY KEY CHECK(run_pk >= 1),
    run_id TEXT UNIQUE NOT NULL,
    target_kind TEXT NOT NULL,
    target_entry TEXT,
    target_cid TEXT,
    target_execution_id TEXT,
    include_all INTEGER NOT NULL CHECK(include_all IN (0, 1)),
    status TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    finished_at TEXT,
    folder_path TEXT NOT NULL,
    artifact_matched INTEGER NOT NULL,
    artifact_changed INTEGER NOT NULL,
    artifact_failed INTEGER NOT NULL,
    artifact_comparison_failed INTEGER NOT NULL,
    artifact_skipped INTEGER NOT NULL,
    command_not_automatic INTEGER,
    command_reproduction_not_needed INTEGER,
    command_unchanged_failed INTEGER,
    command_unchanged_blocked INTEGER,
    command_succeeded INTEGER,
    command_failed INTEGER,
    command_blocked INTEGER,
    command_total INTEGER
);
CREATE TABLE reproduction_run_commands (
    run_pk INTEGER NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 0),
    bucket TEXT NOT NULL,
    reason TEXT NOT NULL,
    terminal_disposition TEXT,
    source_digest TEXT,
    auto_reproduce INTEGER CHECK(auto_reproduce IN (0, 1)),
    cwd TEXT,
    exclusive INTEGER CHECK(exclusive IN (0, 1)),
    prior_disposition TEXT,
    queued INTEGER CHECK(queued IN (0, 1)),
    requires_reproduction INTEGER CHECK(requires_reproduction IN (0, 1)),
    run_selection TEXT,
    details_json TEXT NOT NULL,
    recipe_json TEXT,
    PRIMARY KEY(run_pk, entry, cid, execution_id),
    FOREIGN KEY(run_pk) REFERENCES reproduction_runs(run_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE reproduction_run_executions (
    run_pk INTEGER NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    started_at TEXT,
    finished_at TEXT,
    elapsed_seconds REAL,
    PRIMARY KEY(run_pk, entry, cid, execution_id),
    FOREIGN KEY(run_pk) REFERENCES reproduction_runs(run_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE reproduction_execution_results (
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    disposition TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    producing_run_id TEXT NOT NULL,
    PRIMARY KEY(entry, cid, execution_id)
) WITHOUT ROWID;
CREATE TABLE reproduction_artifact_results (
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    cid TEXT,
    execution_id TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    recorded_at TEXT NOT NULL,
    producing_run_id TEXT NOT NULL,
    comparison_contract TEXT,
    comparison_profile TEXT,
    expected_json TEXT,
    regenerated_json TEXT,
    evidence_contract TEXT,
    evidence_definition TEXT,
    PRIMARY KEY(entry, artifact)
) WITHOUT ROWID;
CREATE TABLE reproduction_comparison_evidence (
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    record_id TEXT,
    retained_json TEXT NOT NULL,
    regenerated_json TEXT NOT NULL,
    tolerance_json TEXT NOT NULL,
    matched INTEGER NOT NULL CHECK(matched IN (0, 1)),
    PRIMARY KEY(entry, artifact, position),
    FOREIGN KEY(entry, artifact) REFERENCES reproduction_artifact_results(entry, artifact) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX validation_results_kind_generation
    ON validation_results(kind, generation DESC, result_pk);
CREATE INDEX validation_checks_code
    ON validation_checks(result_pk, code_pk, check_pk);
CREATE INDEX validation_checks_subject
    ON validation_checks(result_pk, subject, check_pk);
CREATE INDEX validation_groups_entry
    ON validation_groups(result_pk, entry, group_id, group_pk);
CREATE INDEX validation_groups_projection_order
    ON validation_groups(result_pk, group_kind, position, group_pk);
CREATE INDEX validation_commands_group_order
    ON validation_commands(result_pk, group_pk, position, command_pk);
CREATE INDEX validation_commands_entry
    ON validation_commands(result_pk, entry, command_id, command_pk);
CREATE INDEX validation_relationship_artifact
    ON validation_command_relationships(result_pk, path_artifact_pk, command_pk)
    WHERE path_artifact_pk IS NOT NULL;
CREATE INDEX validation_group_artifacts_artifact
    ON validation_group_artifacts(result_pk, artifact_pk, group_pk);
CREATE INDEX validation_batch_entries_entry
    ON validation_batch_entries(result_pk, entry, batch_pk);
CREATE INDEX validation_batch_findings_check
    ON validation_batch_findings(result_pk, check_pk, batch_pk);
CREATE INDEX validation_batch_groups_group
    ON validation_batch_groups(result_pk, group_pk, batch_pk);
CREATE INDEX validation_batch_commands_code
    ON validation_batch_command_links(result_pk, code_pk, command_pk, batch_pk);
CREATE INDEX reproduction_runs_order
    ON reproduction_runs(finished_at DESC, accepted_at DESC, run_id DESC);
CREATE INDEX reproduction_run_commands_order
    ON reproduction_run_commands(run_pk, plan_order);
CREATE INDEX reproduction_run_executions_order
    ON reproduction_run_executions(run_pk, position);
CREATE INDEX reproduction_artifact_outcome
    ON reproduction_artifact_results(outcome, entry, artifact);
"""


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
        if version != STORE_VERSION:
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

    statement = ""
    for line in _DDL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ResultStoreError("results.store.malformed", "incomplete store schema")
    db.execute(f"PRAGMA user_version={STORE_VERSION}")


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


def clear_validation_results(log_root: Path) -> None:
    """Clear only disposable validation rows and its derived report marker."""

    with results_lock(log_root):
        with result_transaction(log_root) as db:
            db.execute("DELETE FROM validation_results")
            db.execute("DELETE FROM store_state WHERE domain='validation'")
            db.execute("DELETE FROM report_materializations WHERE kind='validation'")


def clear_reproduction_results(log_root: Path) -> None:
    """Clear only disposable reproduction rows and its derived report marker."""

    with results_lock(log_root):
        with result_transaction(log_root) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in (
                "reproduction_comparison_evidence",
                "reproduction_artifact_results",
                "reproduction_execution_results",
                "reproduction_run_executions",
                "reproduction_run_commands",
                "reproduction_runs",
                "reproduction_metadata",
            ):
                if table in tables:
                    db.execute(f"DELETE FROM {table}")
            db.execute("DELETE FROM store_state WHERE domain='reproduction'")
            db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")


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
