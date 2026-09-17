"""Replacement-only immutable saved runs in the shared result transaction.

This owner saves canonical per-run work/results/problems and minimal identity
indexes. It never reads the superseded cumulative reproduction rows. Database
locking, paths, validation/command domains and report generations remain owned
by the shared result-store/publication lifecycle.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .reproduction_accepted_storage import _json, _payload
from .reproduction_domain import (
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    ReproductionDomainError,
    ReproductionProblem,
    WorkSelection,
)
from .reproduction_run import ArtifactResult, CommandResult, timestamp
from .reproduction_saved_run import (
    MAX_RESULT_BYTES,
    MAX_WORK_RECORDS,
    RESULT_SCHEMA,
    SavedRun,
)
from .reproduction_work import ArtifactWork, CommandWork

SAVED_STORE_VERSION = 22


@dataclass(frozen=True)
class EmptyConfirmation:
    """Explicit empty-log recovery receipt, not an execution or saved run."""

    summary: str
    confirmed_at: str
    generation: int

    def __post_init__(self) -> None:
        timestamp(self.confirmed_at)
        if not Path(self.summary).is_absolute() or not self.summary.endswith(".md"):
            raise ReproductionDomainError("invalid empty confirmation summary")
        if type(self.generation) is not int or self.generation < 1:
            raise ReproductionDomainError("invalid empty confirmation generation")


@dataclass(frozen=True)
class PreparationHistory:
    """Native observations selected by minimal latest-origin indexes.

    Values reference canonical work/results, not rewritten result packets.
    Problems remain grouped at their actual accepted command owners; the
    planner decides eligibility against current work and source observations.
    """

    commands: Mapping[ExecutionRef, tuple[CommandWork, CommandResult | None]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    artifacts: Mapping[ArtifactRef, tuple[ArtifactWork, ArtifactResult]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    problems: Mapping[ExecutionRef, tuple[ReproductionProblem, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    artifact_problems: Mapping[str, ReproductionProblem] = field(
        default_factory=lambda: MappingProxyType({})
    )
    command_origins: Mapping[ExecutionRef, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


_COLLECTIONS = {
    "commands": ("reproduction_run_commands", ("entry", "cid", "execution_id")),
    "artifacts": ("reproduction_run_artifacts", ("entry", "artifact")),
    "command_results": (
        "reproduction_run_command_results",
        ("entry", "cid", "execution_id"),
    ),
    "artifact_results": ("reproduction_run_artifact_results", ("entry", "artifact")),
}
_OLD_TABLES = (
    "reproduction_latest_commands",
    "reproduction_latest_artifacts",
    "reproduction_run_command_results",
    "reproduction_run_artifact_results",
    "reproduction_run_artifacts",
    "reproduction_comparison_evidence",
    "reproduction_artifact_results",
    "reproduction_execution_results",
    "reproduction_run_executions",
    "reproduction_run_commands",
    "reproduction_run_problems",
    "reproduction_runs",
    "reproduction_empty_confirmation",
    "reproduction_metadata",
)

SAVED_RUN_DDL = (
    """
CREATE TABLE reproduction_runs (
    run_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('log', 'entry')),
    target_entry TEXT,
    settings_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    run_digest TEXT NOT NULL,
    CHECK((target_kind='log' AND target_entry IS NULL) OR
          (target_kind='entry' AND target_entry IS NOT NULL))
) WITHOUT ROWID;
CREATE TABLE reproduction_run_problems (
    run_id TEXT NOT NULL REFERENCES reproduction_runs(run_id) ON DELETE RESTRICT,
    problem_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(run_id, problem_id)
) WITHOUT ROWID;
"""
    + "\n".join(
        f"""CREATE TABLE {table} (
        run_id TEXT NOT NULL REFERENCES reproduction_runs(run_id) ON DELETE RESTRICT,
        {", ".join(column + " TEXT NOT NULL" for column in columns)},
        record_json TEXT NOT NULL,
        PRIMARY KEY(run_id, {", ".join(columns)})
    ) WITHOUT ROWID;"""
        for table, columns in _COLLECTIONS.values()
    )
    + """
CREATE TABLE reproduction_latest_commands (
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY(entry, cid, execution_id),
    FOREIGN KEY(run_id, entry, cid, execution_id)
        REFERENCES reproduction_run_commands(run_id, entry, cid, execution_id)
        ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE reproduction_latest_artifacts (
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY(entry, artifact),
    FOREIGN KEY(run_id, entry, artifact)
        REFERENCES reproduction_run_artifact_results(run_id, entry, artifact)
        ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE INDEX reproduction_saved_run_order
    ON reproduction_runs(finished_at DESC, accepted_at DESC, run_id DESC);
CREATE TABLE reproduction_empty_confirmation (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    summary TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0)
);
"""
)


def clear_saved_reproduction(db: sqlite3.Connection) -> None:
    """Clear the native disposable result domain, not jobs or retained files.

    The caller owns the shared results lock and transaction. Absent history is
    harmless; obsolete formats are unsupported, never decoded or migrated.
    """
    if not db.in_transaction:
        raise ReproductionDomainError(
            "reproduction clearing requires an active transaction"
        )
    if not _absent_domain(db):
        _require_supported(db)
        for table in (
            "reproduction_latest_commands",
            "reproduction_latest_artifacts",
            *[value[0] for value in _COLLECTIONS.values()],
            "reproduction_run_problems",
            "reproduction_runs",
            "reproduction_empty_confirmation",
        ):
            db.execute(f"DELETE FROM {table}")
    db.execute("DELETE FROM store_state WHERE domain='reproduction'")
    db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")


def replace_reproduction_domain(db: sqlite3.Connection) -> None:
    """Discard old reproduction only, inside the caller's atomic transaction."""

    from research_log_result_store import _execute_ddl

    if not db.in_transaction:
        raise ReproductionDomainError(
            "reproduction replacement requires an active transaction"
        )
    version = db.execute("PRAGMA user_version").fetchone()[0]
    native = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='reproduction_run_problems'"
    ).fetchone()
    valid_source = (version == 19 and native is None) or (
        version == 20 and native is not None
    )
    if not valid_source:
        raise ReproductionDomainError(
            "replacement requires the inspected execution-baseline store"
        )
    for table in _OLD_TABLES:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    db.execute("DELETE FROM store_state WHERE domain='reproduction'")
    db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")
    _execute_ddl(db, SAVED_RUN_DDL)
    db.execute(f"PRAGMA user_version={SAVED_STORE_VERSION}")


def _absent_domain(db: sqlite3.Connection) -> bool:
    """A shared v19 store without reproduction tables has no saved history."""

    version = db.execute("PRAGMA user_version").fetchone()[0]
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name GLOB 'reproduction_*' LIMIT 1"
    ).fetchone()
    return version == 19 and present is None


def _require_supported(db: sqlite3.Connection) -> None:
    native = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('reproduction_run_problems', 'reproduction_empty_confirmation')"
    ).fetchone()[0]
    if (
        db.execute("PRAGMA user_version").fetchone()[0] != SAVED_STORE_VERSION
        or native != 2
    ):
        raise ReproductionDomainError(
            "saved reproduction is unsupported; rerun reproduction with --recheck"
        )


def load_empty_confirmation(db: sqlite3.Connection) -> EmptyConfirmation | None:
    """Authenticate confirmed zero against the existing domain generation."""

    if _absent_domain(db):
        return None
    _require_supported(db)
    row = db.execute("SELECT * FROM reproduction_empty_confirmation").fetchone()
    if row is None:
        return None
    receipt = EmptyConfirmation(row["summary"], row["confirmed_at"], row["generation"])
    state = db.execute(
        "SELECT summary, generation FROM store_state WHERE domain='reproduction'"
    ).fetchone()
    if (
        state is None
        or (state["summary"], state["generation"])
        != (
            receipt.summary,
            receipt.generation,
        )
        or db.execute("SELECT 1 FROM reproduction_runs LIMIT 1").fetchone()
    ):
        raise ReproductionDomainError("empty confirmation conflicts with saved facts")
    return receipt


def confirm_empty_replacement(log_root: Path, confirmed_at: str) -> int | None:
    """Recover only obsolete history after explicit truly-empty whole-log recheck.

    Caller proves an empty current target and owns log/publication/results locks.
    Absent or supported history is unchanged; a receipt retry returns its original
    generation so an interrupted report write can recover without new facts.
    """

    from research_log_result_store import result_transaction

    summary = str(log_root.with_suffix(".md"))
    timestamp(confirmed_at)
    with result_transaction(log_root) as db:
        if _absent_domain(db):
            return None
        if db.execute("PRAGMA user_version").fetchone()[0] == SAVED_STORE_VERSION:
            receipt = load_empty_confirmation(db)
            if receipt is not None and receipt.summary != summary:
                raise ReproductionDomainError(
                    "empty confirmation belongs to another log"
                )
            return None if receipt is None else receipt.generation
        previous = db.execute(
            "SELECT generation FROM store_state WHERE domain='reproduction'"
        ).fetchone()
        generation = (0 if previous is None else previous[0]) + 1
        receipt = EmptyConfirmation(summary, confirmed_at, generation)
        replace_reproduction_domain(db)
        db.execute(
            "INSERT INTO reproduction_empty_confirmation VALUES (1,?,?,?)",
            (receipt.summary, receipt.confirmed_at, receipt.generation),
        )
        db.execute(
            "INSERT INTO store_state VALUES ('reproduction',?,?)",
            (receipt.generation, receipt.summary),
        )
        return receipt.generation


def write_saved_run(db: sqlite3.Connection, run: SavedRun) -> None:
    """Insert exact new-format history; a run ID can never acquire new facts."""

    _require_supported(db)
    if not isinstance(run, SavedRun):
        raise ReproductionDomainError("publication requires a replacement saved run")
    serialized = run.serialized().encode()
    run = SavedRun.from_json(serialized)
    digest = hashlib.sha256(serialized).hexdigest()
    summaries = db.execute(
        "SELECT DISTINCT summary FROM reproduction_runs LIMIT 2"
    ).fetchall()
    if any(row[0] != run.summary for row in summaries):
        raise ReproductionDomainError("saved reproduction belongs to a different log")
    prior = db.execute(
        "SELECT run_digest FROM reproduction_runs WHERE run_id=?", (run.run_id,)
    ).fetchone()
    if prior is not None:
        if prior[0] != digest or load_saved_run(db, run.run_id) != run:
            raise ReproductionDomainError(
                "saved run identity conflicts with immutable facts"
            )
        return
    db.execute(
        "INSERT INTO reproduction_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run.run_id,
            run.summary,
            run.target.kind,
            run.target.entry,
            _json(run.settings.as_dict()),
            run.accepted_at,
            run.finished_at,
            digest,
        ),
    )
    for collection, (table, columns) in _COLLECTIONS.items():
        for record in getattr(run, collection):
            values = record.as_dict()
            identity = values.pop("identity")
            placeholders = ", ".join("?" for _ in range(len(columns) + 2))
            db.execute(
                f"INSERT INTO {table} VALUES ({placeholders})",
                (run.run_id, *(identity[column] for column in columns), _json(values)),
            )
    db.executemany(
        "INSERT INTO reproduction_run_problems VALUES (?, ?, ?)",
        (
            (run.run_id, problem.problem_id, _json(problem.as_dict()))
            for problem in run.problems
        ),
    )
    _replace_latest_indexes(db, run)


def publish_saved_run(log_root: Path, run: SavedRun) -> int:
    """Commit native completion and its generation atomically under caller locks.

    The canonical log root must match recorded coverage. Replacement discards
    only unsupported reproduction rows; an exact native retry is read-only and
    returns the current generation without reapplying latest-index changes.
    Report publication follows this transaction and can recover separately.
    """

    from research_log_result_store import result_transaction

    run = SavedRun.from_json(run.serialized().encode())
    _require_publication_log(log_root, run)
    with result_transaction(log_root) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != SAVED_STORE_VERSION:
            replace_reproduction_domain(db)
        existing = load_saved_run(db, run.run_id)
        if existing is not None:
            return _exact_saved_generation(db, run, existing)
        db.execute("DELETE FROM reproduction_empty_confirmation")
        write_saved_run(db, run)
        db.execute("DELETE FROM report_materializations WHERE kind='reproduction'")
        row = db.execute(
            "SELECT generation FROM store_state WHERE domain='reproduction'"
        ).fetchone()
        generation = (row[0] if row is not None else 0) + 1
        db.execute(
            "INSERT INTO store_state VALUES ('reproduction',?,?) "
            "ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation, "
            "summary=excluded.summary",
            (generation, run.summary),
        )
    return generation


def lookup_saved_run_generation(log_root: Path, run: SavedRun) -> int | None:
    """Recognize exact immutable facts after an uncertain native result commit.

    Missing history returns no commit. Conflicting facts or unsupported stores
    fail closed; neither this read nor recognition performs replacement.
    """

    from research_log_result_store import ResultStoreError, result_snapshot

    _require_publication_log(log_root, run)
    try:
        with result_snapshot(log_root) as db:
            assert db is not None
            existing = load_saved_run(db, run.run_id)
            return (
                _exact_saved_generation(db, run, existing)
                if existing is not None
                else None
            )
    except ResultStoreError as error:
        if error.code == "results.store.missing":
            return None
        raise


def _require_publication_log(log_root: Path, run: SavedRun) -> None:
    if not log_root.is_absolute() or str(log_root.with_suffix(".md")) != run.summary:
        raise ReproductionDomainError("publication log differs from recorded coverage")


def _exact_saved_generation(
    db: sqlite3.Connection, run: SavedRun, existing: SavedRun
) -> int:
    if existing != run:
        raise ReproductionDomainError("run commit conflicts with immutable facts")
    row = db.execute(
        "SELECT generation,summary FROM store_state WHERE domain='reproduction'"
    ).fetchone()
    if row is None or row[0] < 1 or row[1] != run.summary:
        raise ReproductionDomainError("native run commit has invalid generation state")
    return row[0]


def _replace_latest_indexes(db: sqlite3.Connection, run: SavedRun) -> None:
    # Preserve the original selected-producer invalidation: stale observations
    # for its previously indexed outputs cannot survive this selected attempt.
    db.execute(
        "DELETE FROM reproduction_latest_artifacts WHERE EXISTS ("
        "SELECT 1 FROM reproduction_run_artifacts a "
        "JOIN reproduction_run_commands c ON c.run_id=? "
        "AND c.entry=json_extract(a.record_json,'$.producer.entry') "
        "AND c.cid=json_extract(a.record_json,'$.producer.cid') "
        "AND c.execution_id=json_extract(a.record_json,'$.producer.execution_id') "
        "WHERE a.run_id=reproduction_latest_artifacts.run_id "
        "AND a.entry=reproduction_latest_artifacts.entry "
        "AND a.artifact=reproduction_latest_artifacts.artifact "
        "AND json_extract(c.record_json,'$.selection') IN ('run','blocked'))",
        (run.run_id,),
    )
    for work in run.commands:
        if work.selection not in {WorkSelection.RUN, WorkSelection.BLOCKED}:
            continue
        identity = work.identity
        db.execute(
            "INSERT INTO reproduction_latest_commands VALUES (?, ?, ?, ?) "
            "ON CONFLICT(entry, cid, execution_id) "
            "DO UPDATE SET run_id=excluded.run_id",
            (identity.entry, identity.cid, identity.execution_id, run.run_id),
        )
    for artifact in run.artifact_results:
        artifact_identity = artifact.identity
        db.execute(
            "INSERT INTO reproduction_latest_artifacts VALUES (?, ?, ?) "
            "ON CONFLICT(entry, artifact) DO UPDATE SET run_id=excluded.run_id",
            (artifact_identity.entry, artifact_identity.artifact, run.run_id),
        )


def _records(
    db: sqlite3.Connection, run_id: str, table: str, columns: tuple[str, ...]
) -> list[dict[str, object]]:
    rows = db.execute(
        f"SELECT * FROM {table} WHERE run_id=? ORDER BY {', '.join(columns)} LIMIT ?",
        (run_id, MAX_WORK_RECORDS + 1),
    ).fetchall()
    if len(rows) > MAX_WORK_RECORDS:
        raise ReproductionDomainError("saved run inventory exceeds its row bound")
    return [
        {
            **_payload(row["record_json"], {"identity"}),
            "identity": {column: row[column] for column in columns},
        }
        for row in rows
    ]


def load_latest_saved_run(db: sqlite3.Connection) -> SavedRun | None:
    """Authenticate the latest native run in canonical immutable-history order."""

    if _absent_domain(db):
        return None
    _require_supported(db)
    row = db.execute(
        "SELECT run_id FROM reproduction_runs "
        "ORDER BY finished_at DESC, accepted_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else load_saved_run(db, row[0])


def load_saved_run(db: sqlite3.Connection, run_id: str) -> SavedRun | None:
    """Reconstruct exact saved facts and reject altered/corrupt native records."""

    if _absent_domain(db):
        return None
    _require_supported(db)
    _require_read_budget(db, run_id)
    row = db.execute(
        "SELECT * FROM reproduction_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    fields: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "summary": row["summary"],
        "run_id": row["run_id"],
        "target": {"kind": row["target_kind"], "entry": row["target_entry"]},
        "settings": _payload(row["settings_json"], set()),
        "accepted_at": row["accepted_at"],
        "finished_at": row["finished_at"],
        "status": "complete",
    }
    for collection, (table, columns) in _COLLECTIONS.items():
        fields[collection] = _records(db, run_id, table, columns)
    problems = db.execute(
        "SELECT problem_id, record_json FROM reproduction_run_problems "
        "WHERE run_id=? ORDER BY problem_id LIMIT ?",
        (run_id, MAX_WORK_RECORDS + 1),
    ).fetchall()
    if len(problems) > MAX_WORK_RECORDS:
        raise ReproductionDomainError("saved problem inventory exceeds its row bound")
    fields["problems"] = [
        _payload(problem["record_json"], set()) for problem in problems
    ]
    run = SavedRun.from_json(_json(fields).encode())
    if tuple(problem.problem_id for problem in run.problems) != tuple(
        problem["problem_id"] for problem in problems
    ):
        raise ReproductionDomainError(
            "saved problem identity disagrees with its observation"
        )
    if hashlib.sha256(run.serialized().encode()).hexdigest() != row["run_digest"]:
        raise ReproductionDomainError("saved run disagrees with its immutable digest")
    return run


def _require_read_budget(db: sqlite3.Connection, run_id: str) -> int:
    """Bound native text inventories in SQLite before transferring any facts.

    Stored text is a subset of the canonical serialized run, so this budget
    cannot reject a valid run within the domain's total byte limit. Typed
    reconstruction still checks serialization overhead and field grammars.
    """

    tables = {
        "reproduction_runs": (
            "run_id",
            "summary",
            "target_kind",
            "target_entry",
            "settings_json",
            "accepted_at",
            "finished_at",
            "run_digest",
        ),
        "reproduction_run_problems": ("record_json",),
        **{
            table: (*columns, "record_json") for table, columns in _COLLECTIONS.values()
        },
    }
    remaining = MAX_RESULT_BYTES + 64  # The digest is not part of serialized facts.
    # This relational key already appears inside its canonical problem JSON;
    # bound it independently without double-counting valid serialized facts.
    longest_problem_id = db.execute(
        "SELECT COALESCE(MAX(length(CAST(problem_id AS BLOB))),0) "
        "FROM reproduction_run_problems WHERE run_id=?",
        (run_id,),
    ).fetchone()[0]
    if longest_problem_id > 72:
        raise ReproductionDomainError("saved problem identity exceeds its byte bound")
    for table, columns in tables.items():
        lengths = "+".join(
            f"COALESCE(length(CAST({column} AS BLOB)),0)" for column in columns
        )
        count, size = db.execute(
            f"SELECT COUNT(*),COALESCE(SUM({lengths}),0) FROM "
            f"(SELECT {', '.join(columns)} FROM {table} WHERE run_id=? LIMIT ?)",
            (run_id, MAX_WORK_RECORDS + 1),
        ).fetchone()
        if count > MAX_WORK_RECORDS:
            raise ReproductionDomainError("saved run inventory exceeds its row bound")
        remaining -= size
        if remaining < 0:
            raise ReproductionDomainError("saved run exceeds its aggregate read budget")
    return MAX_RESULT_BYTES + 64 - remaining


def latest_command_run(db: sqlite3.Connection, identity: ExecutionRef) -> str | None:
    """Return only the latest terminal-observation origin used by preparation."""

    _require_supported(db)
    row = db.execute(
        "SELECT run_id FROM reproduction_latest_commands "
        "WHERE entry=? AND cid=? AND execution_id=?",
        (identity.entry, identity.cid, identity.execution_id),
    ).fetchone()
    return row[0] if row is not None else None


def latest_artifact_run(db: sqlite3.Connection, identity: ArtifactRef) -> str | None:
    """Return a saved observation origin, never a newly inferred comparison."""

    _require_supported(db)
    row = db.execute(
        "SELECT run_id FROM reproduction_latest_artifacts WHERE entry=? AND artifact=?",
        (identity.entry, identity.artifact),
    ).fetchone()
    return row[0] if row is not None else None


def load_preparation_history(
    db: sqlite3.Connection,
    commands: tuple[ExecutionRef, ...],
    artifacts: tuple[ArtifactRef, ...],
) -> PreparationHistory:
    """Read only indexed native origins, once per origin, without source reads.

    Full saved-run validation authenticates each origin. Only requested native
    work/results and their prerequisite diagnoses survive that read; previous
    runs are not held in a growing history cache. Nothing is reclassified from
    current files or translated from an older representation.
    """

    if _absent_domain(db):
        return PreparationHistory()
    origins = _preparation_origins(db, commands, artifacts)
    _require_history_read_budget(db, tuple(origins))
    command_facts = {}
    artifact_facts = {}
    command_origins = {}
    problem_facts: dict[ExecutionRef, dict[str, ReproductionProblem]] = {}
    unique_problems: dict[str, ReproductionProblem] = {}
    artifact_problems: dict[str, ReproductionProblem] = {}
    remaining = MAX_RESULT_BYTES
    for origin, (command_ids, artifact_ids) in sorted(origins.items()):
        run = load_saved_run(db, origin)
        if run is None:
            raise ReproductionDomainError("indexed reproduction origin is missing")
        new_commands, new_artifacts = _preparation_records(
            run, command_ids, artifact_ids
        )
        remaining = _history_charge(
            remaining,
            tuple(record for pair in new_commands.values() for record in pair),
        )
        remaining = _history_charge(
            remaining,
            tuple(record for pair in new_artifacts.values() for record in pair),
        )
        command_facts.update(new_commands)
        command_origins.update({identity: origin for identity in new_commands})
        artifact_facts.update(new_artifacts)
        for identity, records in _preparation_problems(run, command_ids).items():
            for problem in records:
                remaining = _retain_history_problem(remaining, unique_problems, problem)
                problem_facts.setdefault(identity, {}).setdefault(
                    problem.problem_id, unique_problems[problem.problem_id]
                )
        for problem in _comparison_problems(run, new_artifacts):
            remaining = _retain_history_problem(remaining, unique_problems, problem)
            artifact_problems[problem.problem_id] = unique_problems[problem.problem_id]
    return PreparationHistory(
        MappingProxyType(command_facts),
        MappingProxyType(artifact_facts),
        MappingProxyType(
            {
                identity: tuple(records.values())
                for identity, records in problem_facts.items()
            }
        ),
        MappingProxyType(artifact_problems),
        MappingProxyType(command_origins),
    )


def _require_history_read_budget(
    db: sqlite3.Connection, origins: tuple[str, ...]
) -> None:
    """Bound all distinct origin payloads before authenticating any full run."""

    remaining = MAX_RESULT_BYTES
    for origin in origins:
        remaining -= max(0, _require_read_budget(db, origin) - 64)
        if remaining < 0:
            raise ReproductionDomainError(
                "native preparation history exceeds its aggregate read budget"
            )


def _comparison_problems(
    run: SavedRun,
    artifacts: Mapping[ArtifactRef, tuple[ArtifactWork, ArtifactResult]],
) -> tuple[ReproductionProblem, ...]:
    """Keep actual requested comparison diagnostics, not unrelated run issues."""

    references = {
        reference
        for _, result in artifacts.values()
        for reference in result.problem_ids
    }
    return tuple(
        problem for problem in run.problems if problem.problem_id in references
    )


def _retain_history_problem(
    remaining: int,
    retained: dict[str, ReproductionProblem],
    problem: ReproductionProblem,
) -> int:
    """Share only identical native diagnostics across requested origins."""

    prior = retained.get(problem.problem_id)
    if prior is not None:
        if prior.as_dict() != problem.as_dict():
            raise ReproductionDomainError("conflicting native history diagnosis")
        return remaining
    remaining = _history_charge(remaining, (problem,))
    retained[problem.problem_id] = problem
    return remaining


def _preparation_origins(
    db: sqlite3.Connection,
    commands: tuple[ExecutionRef, ...],
    artifacts: tuple[ArtifactRef, ...],
) -> dict[str, tuple[list[ExecutionRef], list[ArtifactRef]]]:
    _require_supported(db)
    if len(commands) > MAX_WORK_RECORDS or len(artifacts) > MAX_WORK_RECORDS:
        raise ReproductionDomainError("preparation inventory exceeds its row bound")
    origins: dict[str, tuple[list[ExecutionRef], list[ArtifactRef]]] = {}
    for identity in dict.fromkeys(commands):
        origin = latest_command_run(db, identity)
        if origin is not None:
            origins.setdefault(origin, ([], []))[0].append(identity)
    for artifact in dict.fromkeys(artifacts):
        origin = latest_artifact_run(db, artifact)
        if origin is not None:
            origins.setdefault(origin, ([], []))[1].append(artifact)
    return origins


def _preparation_records(
    run: SavedRun,
    commands: list[ExecutionRef],
    artifacts: list[ArtifactRef],
) -> tuple[
    dict[ExecutionRef, tuple[CommandWork, CommandResult | None]],
    dict[ArtifactRef, tuple[ArtifactWork, ArtifactResult]],
]:
    """Retain native records, not their containing historical runs."""

    work = {item.identity: item for item in run.commands}
    attempted = {item.identity: item for item in run.command_results}
    output_work = {item.identity: item for item in run.artifacts}
    compared = {item.identity: item for item in run.artifact_results}
    command_facts = {}
    artifact_facts = {}
    for identity in commands:
        if identity not in work:
            raise ReproductionDomainError("indexed command is outside its origin")
        command_facts[identity] = (work[identity], attempted.get(identity))
    for artifact in artifacts:
        if artifact not in output_work or artifact not in compared:
            raise ReproductionDomainError("indexed artifact is outside its origin")
        artifact_facts[artifact] = (output_work[artifact], compared[artifact])
    return command_facts, artifact_facts


def _preparation_problems(
    run: SavedRun,
    identities: list[ExecutionRef],
) -> dict[ExecutionRef, tuple[ReproductionProblem, ...]]:
    """Follow original blocked prerequisites without copying their diagnoses."""

    work = {item.identity: item for item in run.commands}
    results = {item.identity: item for item in run.command_results}
    problems = {item.problem_id: item for item in run.problems}
    retained: dict[ExecutionRef, dict[str, ReproductionProblem]] = {}
    pending, seen = list(identities), set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        command = work[identity]
        result = results.get(identity)
        blocked = (
            result is not None and result.outcome is CommandOutcome.BLOCKED
        ) or command.selection in {WorkSelection.BLOCKED, WorkSelection.PREVIOUS_BLOCK}
        failed = (
            result is not None and result.outcome is CommandOutcome.FAILED
        ) or command.selection is WorkSelection.PREVIOUS_FAILURE
        if not blocked and not failed:
            continue
        for reference in dict.fromkeys(
            (
                *(result.problem_ids if result is not None else ()),
                *command.problem_ids,
            )
        ):
            retained.setdefault(identity, {}).setdefault(reference, problems[reference])
        if blocked:
            pending.extend(
                result.blocked_by
                if result is not None and result.blocked_by
                else command.dependencies
            )
    return {identity: tuple(records.values()) for identity, records in retained.items()}


def _history_charge(
    remaining: int,
    records: tuple[
        CommandWork
        | CommandResult
        | ArtifactWork
        | ArtifactResult
        | ReproductionProblem
        | None,
        ...,
    ],
) -> int:
    """Bound the retained native subset, not a cache of every indexed run."""

    remaining -= sum(
        len(_json(record.as_dict()).encode())
        for record in records
        if record is not None
    )
    if remaining < 0:
        raise ReproductionDomainError(
            "preparation history exceeds its aggregate read budget"
        )
    return remaining
