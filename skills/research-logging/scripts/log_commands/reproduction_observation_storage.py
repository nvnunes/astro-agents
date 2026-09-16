"""Native terminal observations within the run store's existing transaction.

The job owns lifecycle and transaction boundaries. These rows own only complete
terminal research facts and their relationships to accepted work/shared causes.
Stopped attempts and operational publication errors are not command results.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TypeAlias

from .reproduction_accepted_storage import (
    _json,
    _payload,
    _related_rows,
    _require_relation_bounds,
)
from .reproduction_domain import (
    ArtifactRef,
    ExecutionRef,
    ReproductionDomainError,
    ReproductionProblem,
)
from .reproduction_run import ArtifactResult, CommandResult

Result: TypeAlias = CommandResult | ArtifactResult
Identity: TypeAlias = ExecutionRef | ArtifactRef

OBSERVATION_DDL = (
    "\n".join(
        f"""CREATE TABLE run_{kind}_results (
        run_id TEXT NOT NULL,
        {kind}_pk INTEGER NOT NULL,
        result_json TEXT NOT NULL,
        result_digest TEXT NOT NULL,
        PRIMARY KEY(run_id, {kind}_pk),
        FOREIGN KEY(run_id, {kind}_pk)
            REFERENCES accepted_work_{kind}s(run_id, {kind}_pk) ON DELETE RESTRICT
    ) WITHOUT ROWID;
    CREATE TABLE run_{kind}_problem_links (
        run_id TEXT NOT NULL,
        {kind}_pk INTEGER NOT NULL,
        position INTEGER NOT NULL CHECK(position >= 0),
        problem_id TEXT NOT NULL,
        PRIMARY KEY(run_id, {kind}_pk, position),
        UNIQUE(run_id, {kind}_pk, problem_id),
        FOREIGN KEY(run_id, {kind}_pk)
            REFERENCES run_{kind}_results(run_id, {kind}_pk) ON DELETE RESTRICT,
        FOREIGN KEY(run_id, problem_id)
            REFERENCES reproduction_problems(run_id, problem_id) ON DELETE RESTRICT
    ) WITHOUT ROWID;"""
        for kind in ("command", "artifact")
    )
    + """
CREATE TABLE run_blocked_dependencies (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    dependency_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, command_pk, position),
    UNIQUE(run_id, command_pk, dependency_pk),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES run_command_results(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, dependency_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
"""
)


def _kind(identity: Identity) -> str:
    if not isinstance(identity, (ExecutionRef, ArtifactRef)):
        raise ReproductionDomainError("terminal observation has an invalid identity")
    return "command" if isinstance(identity, ExecutionRef) else "artifact"


def _work_key(db: sqlite3.Connection, run_id: str, identity: Identity) -> int:
    kind = _kind(identity)
    fields = identity.as_dict()
    row = db.execute(
        f"SELECT {kind}_pk FROM accepted_work_{kind}s WHERE run_id=? AND "
        + " AND ".join(f"{key}=?" for key in fields),
        (run_id, *fields.values()),
    ).fetchone()
    if row is None:
        raise ReproductionDomainError("terminal observation has no accepted work")
    return row[0]


def _write_problems(
    db: sqlite3.Connection,
    run_id: str,
    result: Result,
    problems: tuple[ReproductionProblem, ...],
) -> None:
    if any(
        problem.subject != result.identity
        or problem.problem_id not in result.problem_ids
        for problem in problems
    ):
        raise ReproductionDomainError(
            "terminal diagnosis has an unrelated owner/reference"
        )
    for problem in problems:
        payload = _json(problem.as_dict())
        stored = db.execute(
            "SELECT problem_json FROM reproduction_problems "
            "WHERE run_id=? AND problem_id=?",
            (run_id, problem.problem_id),
        ).fetchone()
        if stored is not None:
            if stored[0] != payload:
                raise ReproductionDomainError(
                    "terminal diagnosis changes an existing observation"
                )
        else:
            db.execute(
                "INSERT INTO reproduction_problems VALUES (?, ?, ?)",
                (run_id, problem.problem_id, payload),
            )


def _insert_result(
    db: sqlite3.Connection, run_id: str, key: int, result: Result
) -> bool:
    kind = _kind(result.identity)
    digest = hashlib.sha256(_json(result.as_dict()).encode()).hexdigest()
    row = db.execute(
        f"SELECT result_digest FROM run_{kind}_results WHERE run_id=? AND {kind}_pk=?",
        (run_id, key),
    ).fetchone()
    if row is not None:
        if row[0] != digest:
            raise ReproductionDomainError("terminal observation is already immutable")
        if load_observation(db, run_id, result.identity) != result:
            raise ReproductionDomainError("stored terminal observation changed")
        return False
    payload = {
        key: value
        for key, value in result.as_dict().items()
        if key not in {"identity", "problem_ids", "blocked_by"}
    }
    db.execute(
        f"INSERT INTO run_{kind}_results VALUES (?, ?, ?, ?)",
        (run_id, key, _json(payload), digest),
    )
    db.executemany(
        f"INSERT INTO run_{kind}_problem_links VALUES (?, ?, ?, ?)",
        (
            (run_id, key, position, problem_id)
            for position, problem_id in enumerate(result.problem_ids)
        ),
    )
    return True


def write_command_observation(
    db: sqlite3.Connection,
    run_id: str,
    result: CommandResult,
    problems: tuple[ReproductionProblem, ...] = (),
) -> None:
    """Retain one failed/succeeded attempt or prerequisite-mediated non-attempt."""

    result = CommandResult.from_dict(result.as_dict())
    key = _work_key(db, run_id, result.identity)
    _write_problems(db, run_id, result, problems)
    if _insert_result(db, run_id, key, result):
        db.executemany(
            "INSERT INTO run_blocked_dependencies VALUES (?, ?, ?, ?)",
            (
                (run_id, key, position, _work_key(db, run_id, dependency))
                for position, dependency in enumerate(result.blocked_by)
            ),
        )


def write_artifact_observation(
    db: sqlite3.Connection,
    run_id: str,
    result: ArtifactResult,
    problems: tuple[ReproductionProblem, ...] = (),
) -> None:
    """Retain an artifact comparison or its command-mediated non-comparison."""

    result = ArtifactResult.from_dict(result.as_dict())
    key = _work_key(db, run_id, result.identity)
    _write_problems(db, run_id, result, problems)
    _insert_result(db, run_id, key, result)


def load_observation(
    db: sqlite3.Connection, run_id: str, identity: Identity
) -> Result | None:
    """Read exact supported facts without replanning or inspecting current source."""

    _require_relation_bounds(
        db,
        run_id,
        (
            "run_command_problem_links",
            "run_artifact_problem_links",
            "run_blocked_dependencies",
        ),
    )
    kind = _kind(identity)
    key = _work_key(db, run_id, identity)
    row = db.execute(
        f"SELECT * FROM run_{kind}_results WHERE run_id=? AND {kind}_pk=?",
        (run_id, key),
    ).fetchone()
    if row is None:
        return None
    fields = _payload(row["result_json"], {"identity", "problem_ids", "blocked_by"})
    fields["identity"] = identity.as_dict()
    fields["problem_ids"] = [
        item["problem_id"]
        for item in _related_rows(
            db, f"run_{kind}_problem_links", run_id, f"{kind}_pk", key
        )
    ]
    if isinstance(identity, ExecutionRef):
        blocked_by: list[dict[str, object]] = []
        for dependency in _related_rows(
            db, "run_blocked_dependencies", run_id, "command_pk", key
        ):
            owner = db.execute(
                "SELECT entry, cid, execution_id FROM accepted_work_commands "
                "WHERE run_id=? AND command_pk=?",
                (run_id, dependency["dependency_pk"]),
            ).fetchone()
            if owner is None:
                raise ReproductionDomainError(
                    "blocked observation has an unknown prerequisite"
                )
            blocked_by.append(dict(owner))
        fields["blocked_by"] = blocked_by
        result: Result = CommandResult.from_dict(fields)
    else:
        result = ArtifactResult.from_dict(fields)
    if (
        row["result_digest"]
        != hashlib.sha256(_json(result.as_dict()).encode()).hexdigest()
    ):
        raise ReproductionDomainError(
            "terminal observation disagrees with its immutable digest"
        )
    return result


def load_observation_problems(
    db: sqlite3.Connection, run_id: str, result: Result
) -> tuple[ReproductionProblem, ...]:
    """Load the bounded owned causes referenced by one exact terminal result."""

    problems = []
    for reference in result.problem_ids:
        row = db.execute(
            "SELECT problem_json FROM reproduction_problems "
            "WHERE run_id=? AND problem_id=?",
            (run_id, reference),
        ).fetchone()
        if row is None:
            raise ReproductionDomainError("terminal observation has an unknown problem")
        problem = ReproductionProblem.from_dict(_payload(row[0], set()))
        if problem.problem_id != reference:
            raise ReproductionDomainError(
                "terminal problem identity disagrees with its observation"
            )
        problems.append(problem)
    return tuple(problems)
