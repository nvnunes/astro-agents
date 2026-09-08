"""Retain evaluated validation snapshots and observed authoring diagnostics."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .inspection_store import ContentWriter, InspectionError, connection, encode

RESULT_SCHEMA = "research-log-retained-result/1"


def timestamp() -> str:
    """Return the actual UTC observation time, independent of report dates."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _objects(value: Any) -> list[dict[str, Any]]:
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, (list, tuple))
        else []
    )


def _finding(check: dict[str, Any]) -> dict[str, Any]:
    failure = check.get("failure", {})
    return {**{key: item for key, item in check.items() if key != "failure"}, **failure}


def _chain(writer: ContentWriter, chain: dict[str, Any]) -> None:
    entry, identity = str(chain["entry"]), str(chain["chain_id"])
    commands = _objects(chain.get("commands"))
    findings = _objects(chain.get("findings"))
    for command in commands:
        writer.entity(
            "commands", str(command["identity"]), command, (entry, identity, "")
        )
    for finding in findings:
        _rejected_commands(writer, finding)
        writer.entity(
            "findings",
            str(finding["identity"]),
            finding,
            (entry, identity, str(finding.get("code", ""))),
        )
    for path in chain.get("artifacts", []):
        writer.entity(
            "artifacts", str(path), {"path": str(path)}, (entry, identity, "")
        )
    compact = {
        **chain,
        "commands": [str(item["identity"]) for item in commands],
        "findings": [str(item["identity"]) for item in findings],
    }
    writer.entity("chains", identity, compact, (entry, identity, ""))
    for code in {str(finding.get("code", "")) for finding in findings}:
        writer.db.execute(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?, ?)",
            (writer.result_id, "chains", identity, entry, identity, code),
        )


def _rejected_commands(writer: ContentWriter, finding: dict[str, Any]) -> None:
    """Expose rejected commands as inspection entities, never graph members."""
    observed = finding.get("observed", {})
    if not isinstance(observed, dict):
        return
    command = observed.get("rejected_command")
    commands = _objects(observed.get("rejected_commands"))
    if isinstance(command, dict):
        commands.append(command)
    for command in commands:
        writer.entity(
            "commands", str(command["identity"]), command,
            (str(command["entry"]), "", str(command["code"])),
        )


def _content(
    writer: ContentWriter,
    record: dict[str, Any],
    projection: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, int]:
    for command in _objects(outcome.get("diagnostics")):
        writer.entity(
            "commands", str(command["identity"]), command,
            (str(command["entry"]), "", str(command["code"])),
        )
    chains = _objects(projection.get("chains")) + _objects(projection.get("unresolved"))
    for chain in chains:
        _chain(writer, chain)
    for check in _objects(record.get("checks")):
        _rejected_commands(writer, _finding(check))
        writer.entity("checks", str(check["identity"]), check)
    # Batch reconciliation may return a related finding outside current chains.
    for finding in _objects(outcome.get("findings")):
        exists = writer.db.execute(
            "SELECT 1 FROM entities WHERE result=? AND kind='findings' AND id=?",
            (writer.result_id, str(finding["identity"])),
        ).fetchone()
        if not exists:
            _rejected_commands(writer, finding)
            writer.entity(
                "findings",
                str(finding["identity"]),
                finding,
                ("", "", str(finding.get("code", ""))),
            )
    for index, overlap in enumerate(_objects(outcome.get("pending_batch_overlaps"))):
        writer.entity("overlaps", str(index), overlap)
    counts = dict(
        writer.db.execute(
            "SELECT kind, count(*) FROM entities WHERE result=? GROUP BY kind",
            (writer.result_id,),
        ).fetchall()
    )
    for code, count in writer.db.execute(
        "SELECT code, count(*) FROM entities "
        "WHERE result=? AND kind='findings' GROUP BY code",
        (writer.result_id,),
    ).fetchall():
        writer.entity("codes", code, {"code": code, "findings": count})
    return counts


def _metadata(
    summary: Path,
    outcome: dict[str, Any],
    record: dict[str, Any],
    projection: dict[str, Any],
    request: dict[str, str],
) -> dict[str, Any]:
    kind = request.get("kind", "full")
    metadata = {
        "schema": RESULT_SCHEMA,
        "result_id": str(uuid.uuid4()),
        "summary": str(summary),
        "kind": kind,
        "started_at": request["started_at"],
        "finished_at": timestamp(),
        "stored_at": timestamp(),
        "status": outcome["status"],
        "reason": outcome.get("reason"),
        "result_date": record.get("result_date"),
        "rules_version": record.get("rules_version"),
        "source_schemas": [record.get("schema"), projection.get("schema")],
        "source_identity": (
            projection.get("source_identity")
            if outcome["status"] != "incomplete"
            else None
        ),
        "unavailable_reason": "evaluation_incomplete"
        if outcome["status"] == "incomplete"
        else None,
        "projection_id": projection.get("projection_id"),
        "origin_projection_id": request.get("projection"),
        "requested_entry": request.get("entry"),
        "requested_chain": request.get("chain"),
        "evaluated_scope": request.get("entry", "full log"),
        "evaluated_checks": len(record.get("checks", [])),
        "returned_scope": "reconciled chains" if kind == "batch" else "full log",
    }
    if kind == "diagnostic":
        metadata.update(
            evaluated_scope=None, evaluated_checks=None,
            returned_scope="authoring diagnostics; no validation performed",
        )
    return metadata


def save_result(
    summary: Path,
    outcome: dict[str, Any],
    record: dict[str, Any],
    projection: dict[str, Any],
    request: dict[str, str],
) -> str:
    """Atomically replace a scope's cached snapshot; propagate storage failures.

    Full producers call under their existing operation lock. Batch producers
    supply the accepted published-file stat identity, checked inside the write
    transaction to prevent a late batch repopulating a superseded cycle.
    """
    metadata = _metadata(summary, outcome, record, projection, request)
    kind, identity = metadata["kind"], metadata["result_id"]
    slot = encode(
        [kind, request.get("projection"), request.get("entry"), request.get("chain")]
    )
    with connection(summary.with_suffix(""), writable=True) as db:
        _require_projection(summary, request)
        generation = db.execute("SELECT generation FROM state").fetchone()[0] + 1
        db.execute("UPDATE state SET generation=?", (generation,))
        if kind == "full" and outcome.get("published"):
            db.execute("DELETE FROM results")
        else:
            db.execute("DELETE FROM results WHERE slot=?", (slot,))
        metadata["sequence"] = generation
        db.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                generation,
                slot,
                kind,
                request.get("entry", ""),
                request.get("chain", ""),
                request.get("projection", ""),
                "{}",
            ),
        )
        writer = ContentWriter(db, identity)
        metadata["counts"] = _content(writer, record, projection, outcome)
        metadata["finding_count"] = metadata["counts"].get("findings", 0)
        metadata["codes"] = dict(
            db.execute(
                "SELECT code, count(*) FROM entities "
                "WHERE result=? AND kind='findings' "
                "GROUP BY code ORDER BY code LIMIT 20",
                (identity,),
            ).fetchall()
        )
        metadata["code_groups"] = db.execute(
            "SELECT count(DISTINCT code) FROM entities "
            "WHERE result=? AND kind='findings'",
            (identity,),
        ).fetchone()[0]
        db.execute(
            "UPDATE results SET metadata=? WHERE id=?", (encode(metadata), identity)
        )
        _require_projection(summary, request)
    return identity


def _require_projection(summary: Path, request: dict[str, str]) -> None:
    if "published_stat" not in request:
        return
    path = summary.with_suffix("") / "validation/batches.json"
    stat = path.stat()
    actual = encode(
        [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    )
    if path.is_symlink() or actual != request["published_stat"]:
        raise InspectionError(
            "findings.projection_superseded",
            "published projection changed during batch validation",
        )


def retain_result(
    summary: Path,
    outcome: dict[str, Any],
    record: dict[str, Any],
    projection: dict[str, Any],
    request: dict[str, str],
) -> str | None:
    """Best-effort cache persistence; warnings never discard evaluation outcomes."""
    try:
        return save_result(summary, outcome, record, projection, request)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            f"log: results.store.write_failed: {str(error)[:1024]}; result not cached",
            file=sys.stderr,
        )
        return None
