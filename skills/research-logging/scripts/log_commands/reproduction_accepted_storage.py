"""Normalized immutable work rows for replacement run-local job acceptance.

The run store owns transactions, lifecycle rows and its accepted run header.
These private helpers store only plan/12 components. Recipe/evidence grammars
and reference validation remain owned by the typed plan; no older plan or job
is read or translated here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Mapping, cast

from .reproduction_domain import ReproductionDomainError, ReproductionProblem
from .reproduction_saved_run import MAX_WORK_RECORDS
from .reproduction_work_plan import MAX_PLAN_BYTES, PLAN_SCHEMA, ReproductionPlan

# Every canonical dependency/problem reference needs more than 64 serialized
# bytes. This conservative read bound cannot exclude any valid 64 MiB plan.
MAX_RELATION_ROWS = MAX_PLAN_BYTES // 64
_RELATIONS = (
    "accepted_work_dependencies",
    "accepted_work_command_problems",
    "accepted_work_artifact_problems",
)

_INFRASTRUCTURE = {
    "materials": "accepted_plan_materials",
    "evidence_only": "accepted_plan_evidence_only",
    "reusable_artifact_results": "accepted_plan_reuse",
}

ACCEPTED_WORK_DDL = """
CREATE TABLE accepted_work_manifest (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    plan_digest TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE accepted_work_commands (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    command_pk INTEGER NOT NULL CHECK(command_pk >= 1),
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    work_json TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk),
    UNIQUE(run_id, entry, cid, execution_id)
) WITHOUT ROWID;
CREATE TABLE accepted_work_artifacts (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    artifact_pk INTEGER NOT NULL CHECK(artifact_pk >= 1),
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    producer_pk INTEGER,
    work_json TEXT NOT NULL,
    PRIMARY KEY(run_id, artifact_pk),
    UNIQUE(run_id, entry, artifact),
    FOREIGN KEY(run_id, producer_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE reproduction_problems (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    problem_id TEXT NOT NULL,
    problem_json TEXT NOT NULL,
    PRIMARY KEY(run_id, problem_id)
) WITHOUT ROWID;
CREATE TABLE accepted_work_problems (
    run_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    PRIMARY KEY(run_id, problem_id),
    FOREIGN KEY(run_id, problem_id)
        REFERENCES reproduction_problems(run_id, problem_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_work_dependencies (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    dependency_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, command_pk, position),
    UNIQUE(run_id, command_pk, dependency_pk),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, dependency_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_work_command_problems (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    problem_id TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, position),
    UNIQUE(run_id, command_pk, problem_id),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, problem_id)
        REFERENCES reproduction_problems(run_id, problem_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_work_artifact_problems (
    run_id TEXT NOT NULL,
    artifact_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    problem_id TEXT NOT NULL,
    PRIMARY KEY(run_id, artifact_pk, position),
    UNIQUE(run_id, artifact_pk, problem_id),
    FOREIGN KEY(run_id, artifact_pk)
        REFERENCES accepted_work_artifacts(run_id, artifact_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, problem_id)
        REFERENCES reproduction_problems(run_id, problem_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_work_scheduling (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 1),
    run_path TEXT NOT NULL,
    claims_json TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk),
    UNIQUE(run_id, plan_order),
    FOREIGN KEY(run_id, command_pk)
        REFERENCES accepted_work_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
""" + "\n".join(
    f"""CREATE TABLE {table} (
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK(position >= 0),
        record_json TEXT NOT NULL,
        PRIMARY KEY(run_id, position)
    ) WITHOUT ROWID;"""
    for table in _INFRASTRUCTURE.values()
)


def _json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise ReproductionDomainError("accepted work is not finite JSON") from error


def _identity(value: Mapping[str, object]) -> tuple[object, object, object]:
    return value["entry"], value["cid"], value["execution_id"]


def _write_accepted_work(
    db: sqlite3.Connection, run_id: str, plan: ReproductionPlan
) -> None:
    """Insert validated new-model facts inside the caller's job transaction.

    Identities and references have one relational owner; JSON payloads omit
    those fields rather than keeping independently authoritative copies.
    """

    if not isinstance(plan, ReproductionPlan):
        raise ReproductionDomainError("acceptance requires a replacement typed plan")
    serialized = plan.serialized().encode()
    fields = ReproductionPlan.from_json(serialized).as_dict()
    db.execute(
        "INSERT INTO accepted_work_manifest VALUES (?, ?)",
        (run_id, hashlib.sha256(serialized).hexdigest()),
    )
    commands = fields["commands"]
    assert isinstance(commands, list)
    command_keys = {}
    for command_pk, command in enumerate(commands, 1):
        identity = command["identity"]
        command_keys[_identity(identity)] = command_pk
        payload = {
            key: value
            for key, value in command.items()
            if key not in {"identity", "dependencies", "problem_ids"}
        }
        db.execute(
            "INSERT INTO accepted_work_commands VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, command_pk, *_identity(identity), _json(payload)),
        )
    for problem in plan.problems:
        db.execute(
            "INSERT INTO reproduction_problems VALUES (?, ?, ?)",
            (run_id, problem.problem_id, _json(problem.as_dict())),
        )
        db.execute(
            "INSERT INTO accepted_work_problems VALUES (?, ?)",
            (run_id, problem.problem_id),
        )
    for command_pk, command in enumerate(commands, 1):
        db.executemany(
            "INSERT INTO accepted_work_dependencies VALUES (?, ?, ?, ?)",
            (
                (run_id, command_pk, position, command_keys[_identity(dependency)])
                for position, dependency in enumerate(command["dependencies"])
            ),
        )
        db.executemany(
            "INSERT INTO accepted_work_command_problems VALUES (?, ?, ?, ?)",
            (
                (run_id, command_pk, position, problem_id)
                for position, problem_id in enumerate(command["problem_ids"])
            ),
        )
    artifacts = fields["artifacts"]
    assert isinstance(artifacts, list)
    for artifact_pk, artifact in enumerate(artifacts, 1):
        identity = artifact["identity"]
        producer = artifact["producer"]
        payload = {
            key: value
            for key, value in artifact.items()
            if key not in {"identity", "producer", "problem_ids"}
        }
        db.execute(
            "INSERT INTO accepted_work_artifacts VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                artifact_pk,
                identity["entry"],
                identity["artifact"],
                command_keys[_identity(producer)] if producer is not None else None,
                _json(payload),
            ),
        )
        db.executemany(
            "INSERT INTO accepted_work_artifact_problems VALUES (?, ?, ?, ?)",
            (
                (run_id, artifact_pk, position, problem_id)
                for position, problem_id in enumerate(artifact["problem_ids"])
            ),
        )
    for field, table in _INFRASTRUCTURE.items():
        records = fields[field]
        assert isinstance(records, list)
        db.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?)",
            (
                (run_id, position, _json(record))
                for position, record in enumerate(records)
            ),
        )
    for claims in plan.scheduling:
        payload = {
            key: value
            for key, value in claims.items()
            if key not in {"identity", "order", "run_path"}
        }
        db.execute(
            "INSERT INTO accepted_work_scheduling VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                command_keys[_identity(cast(Mapping[str, object], claims["identity"]))],
                claims["order"],
                claims["run_path"],
                _json(payload),
            ),
        )


def _rows(
    db: sqlite3.Connection, table: str, run_id: str, order: str
) -> list[sqlite3.Row]:
    """Bound reconstruction before materializing forged excess row inventories."""

    rows = db.execute(
        f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order} LIMIT ?",
        (run_id, MAX_WORK_RECORDS + 1),
    ).fetchall()
    if len(rows) > MAX_WORK_RECORDS:
        raise ReproductionDomainError("accepted work inventory exceeds its row bound")
    return rows


def _payload(raw: str, removed: set[str]) -> dict[str, object]:
    if (
        not isinstance(raw, str)
        or len(raw) > MAX_PLAN_BYTES
        or len(raw.encode()) > MAX_PLAN_BYTES
    ):
        raise ReproductionDomainError("accepted/result payload exceeds its byte bound")
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ReproductionDomainError("accepted work payload is not JSON") from error
    if not isinstance(payload, dict) or removed & set(payload):
        raise ReproductionDomainError(
            "accepted work payload duplicates relational fields"
        )
    return payload


def _related_rows(
    db: sqlite3.Connection, table: str, run_id: str, owner_column: str, owner: int
) -> list[sqlite3.Row]:
    rows = db.execute(
        f"SELECT * FROM {table} WHERE run_id=? AND {owner_column}=? "
        "ORDER BY position LIMIT ?",
        (run_id, owner, MAX_WORK_RECORDS + 1),
    ).fetchall()
    if len(rows) > MAX_WORK_RECORDS:
        raise ReproductionDomainError("accepted work relations exceed their row bound")
    return rows


def _require_relation_bounds(
    db: sqlite3.Connection, run_id: str, tables: tuple[str, ...] = _RELATIONS
) -> None:
    for table in tables:
        count = db.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} WHERE run_id=? LIMIT ?)",
            (run_id, MAX_RELATION_ROWS + 1),
        ).fetchone()[0]
        if count > MAX_RELATION_ROWS:
            raise ReproductionDomainError(
                "accepted run relations exceed its byte-derived row bound"
            )


def _load_accepted_work(
    db: sqlite3.Connection, run_id: str, header: Mapping[str, object]
) -> ReproductionPlan:
    """Reconstruct only plan/12 from the run header and normalized work facts.

    The domain validates the complete closed plan and all owner/reference and
    scheduling claims. No current registry, graph or retained file is read.
    """

    if set(header) != {"summary", "target", "settings", "admission"}:
        raise ReproductionDomainError("accepted run header has invalid fields")
    _require_relation_bounds(db, run_id)
    fields = {"schema": PLAN_SCHEMA, **header}
    rows = _rows(db, "accepted_work_commands", run_id, "command_pk")
    identities = {
        row["command_pk"]: {key: row[key] for key in ("entry", "cid", "execution_id")}
        for row in rows
    }
    commands = []
    for row in rows:
        command_pk = row["command_pk"]
        payload = _payload(
            row["work_json"], {"identity", "dependencies", "problem_ids"}
        )
        dependencies = _related_rows(
            db, "accepted_work_dependencies", run_id, "command_pk", command_pk
        )
        problems = _related_rows(
            db, "accepted_work_command_problems", run_id, "command_pk", command_pk
        )
        commands.append(
            {
                **payload,
                "identity": identities[command_pk],
                "dependencies": [
                    _known_execution(identities, item["dependency_pk"])
                    for item in dependencies
                ],
                "problem_ids": [item["problem_id"] for item in problems],
            }
        )
    fields["commands"] = commands
    artifacts = []
    for row in _rows(db, "accepted_work_artifacts", run_id, "artifact_pk"):
        payload = _payload(row["work_json"], {"identity", "producer", "problem_ids"})
        problems = _related_rows(
            db,
            "accepted_work_artifact_problems",
            run_id,
            "artifact_pk",
            row["artifact_pk"],
        )
        artifacts.append(
            {
                **payload,
                "identity": {"entry": row["entry"], "artifact": row["artifact"]},
                "producer": _known_execution(identities, row["producer_pk"])
                if row["producer_pk"] is not None
                else None,
                "problem_ids": [item["problem_id"] for item in problems],
            }
        )
    fields["artifacts"] = artifacts
    problem_records = []
    for membership in _rows(db, "accepted_work_problems", run_id, "problem_id"):
        row = db.execute(
            "SELECT * FROM reproduction_problems WHERE run_id=? AND problem_id=?",
            (run_id, membership["problem_id"]),
        ).fetchone()
        if row is None:
            raise ReproductionDomainError("accepted work references an unknown problem")
        problem = ReproductionProblem.from_dict(_payload(row["problem_json"], set()))
        if problem.problem_id != row["problem_id"]:
            raise ReproductionDomainError(
                "accepted problem identity disagrees with its observation"
            )
        problem_records.append(problem.as_dict())
    fields["problems"] = problem_records
    for field, table in _INFRASTRUCTURE.items():
        fields[field] = [
            _payload(row["record_json"], set())
            for row in _rows(db, table, run_id, "position")
        ]
    fields["scheduling"] = [
        {
            **_payload(row["claims_json"], {"identity", "order", "run_path"}),
            "identity": _known_execution(identities, row["command_pk"]),
            "order": row["plan_order"],
            "run_path": row["run_path"],
        }
        for row in _rows(db, "accepted_work_scheduling", run_id, "plan_order")
    ]
    plan = ReproductionPlan.from_json(_json(fields).encode())
    manifest = db.execute(
        "SELECT plan_digest FROM accepted_work_manifest WHERE run_id=?", (run_id,)
    ).fetchone()
    if (
        manifest is None
        or manifest["plan_digest"]
        != hashlib.sha256(plan.serialized().encode()).hexdigest()
    ):
        raise ReproductionDomainError(
            "accepted plan disagrees with its immutable digest"
        )
    return plan


def _known_execution(
    identities: Mapping[int, dict[str, object]], key: int
) -> dict[str, object]:
    identity = identities.get(key)
    if identity is None:
        raise ReproductionDomainError("accepted work references an unknown execution")
    return identity
