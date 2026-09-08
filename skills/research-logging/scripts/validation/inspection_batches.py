"""Indexed primary batch membership in the existing disposable result store."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from .inspection_store import ContentWriter, InspectionError
from .json_codec import canonical_json
from .repair_batches import command_anchor, objects
from .report import format_structure_counts, structure_finding

BATCH_DDL = """
CREATE TABLE batch_links (
 result TEXT REFERENCES results(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, id TEXT NOT NULL, entry TEXT NOT NULL,
 batch TEXT NOT NULL, code TEXT NOT NULL,
 PRIMARY KEY(result, kind, id, entry, batch, code)
);
CREATE INDEX batch_selection ON batch_links(result, kind, batch, entry, code, id);
CREATE TABLE batch_requests (
 result TEXT REFERENCES results(id) ON DELETE CASCADE,
 batch TEXT NOT NULL, entry TEXT NOT NULL,
 PRIMARY KEY(result, batch, entry)
);
CREATE INDEX batch_request_selection ON batch_requests(batch, entry, result);
"""


def store_batches(
    writer: ContentWriter,
    projection: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any] | None:
    """Store bounded entities and indexed primary links without duplicating work."""
    if "repair_batches" not in projection:
        return None
    findings = {
        finding["identity"]: finding
        for chain in objects(projection.get("chains"))
        + objects(projection.get("unresolved"))
        for finding in objects(chain.get("findings"))
    }
    findings.update({f["identity"]: f for f in objects(outcome.get("findings"))})
    batches = objects(projection["repair_batches"])
    requested = outcome.get("requested_repair_batch")
    if isinstance(requested, dict):
        batches = [requested]
    commands = _command_index(projection)
    reasons: dict[str, int] = {}
    sizes = []
    fallback_findings = 0
    for batch in batches:
        identity = batch["batch_id"]
        members = batch["primary_finding_ids"]
        selected = (
            sorted(f["identity"] for f in objects(outcome.get("findings")))
            if requested
            else members
        )
        compact = {**batch, "primary_finding_count": len(selected)}
        if selected:
            compact["starting_finding"] = next(
                (
                    i
                    for i in selected
                    if "rejected_command" in findings.get(i, {}).get("observed", {})
                ),
                selected[0],
            )
        if requested:
            compact.update(
                {
                    key: outcome[key]
                    for key in ("coverage", "reconciliation", "current_membership")
                }
            )
        writer.entity("batches", identity, compact)
        reasons[batch["grouping_reason"]] = reasons.get(batch["grouping_reason"], 0) + 1
        sizes.append(len(selected))
        if batch["grouping_reason"] == "inspection_group":
            fallback_findings += len(selected)
        _batch_links(writer, batch, selected, findings, commands)
    return {
        "by_reason": reasons,
        "minimum_size": min(sizes, default=0),
        "maximum_size": max(sizes, default=0),
        "primary_findings": sum(sizes),
        "fallback_findings": fallback_findings,
    }


def _link(
    writer: ContentWriter, kind: str, identity: str, scope: tuple[str, str, str]
) -> None:
    writer.db.execute(
        "INSERT OR IGNORE INTO batch_links VALUES (?, ?, ?, ?, ?, ?)",
        (writer.result_id, kind, identity, *scope),
    )


def _command_index(projection: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chain in objects(projection.get("chains")):
        for command in objects(chain["commands"]):
            index[canonical_json(command_anchor(command))].append(command)
            paths = {
                r["path"]
                for r in objects(command["inputs"]) + objects(command["outputs"])
            }
            for path in paths:
                index[canonical_json({"kind": "material", "path": path})].append(
                    command
                )
    return index


def _finding_links(
    writer: ContentWriter, batch: dict[str, Any], finding: dict[str, Any]
) -> None:
    identity, code = batch["batch_id"], finding["code"]
    _link(writer, "batches", identity, ("", identity, code))
    _link(writer, "findings", finding["identity"], ("", identity, code))
    observed = finding.get("observed", {})
    commands = objects(observed.get("rejected_commands"))
    if isinstance(observed.get("rejected_command"), dict):
        commands.append(observed["rejected_command"])
    for command in commands:
        _link(writer, "commands", command["identity"], ("", identity, code))


def _batch_links(
    writer: ContentWriter,
    batch: dict[str, Any],
    selected: list[str],
    findings: dict[str, dict[str, Any]],
    commands: dict[str, list[dict[str, Any]]],
) -> None:
    # Store entry scope separately from membership to avoid their cross product.
    identity = batch["batch_id"]
    for entry in batch["entries"] or [""]:
        _link(writer, "batches", identity, (entry, identity, ""))
    for chain_id in batch["related_chain_ids"]:
        writer.db.execute(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?, ?)",
            (writer.result_id, "batches", identity, "", chain_id, ""),
        )
    for finding_id in selected:
        finding = findings.get(finding_id)
        if finding is not None:
            _finding_links(writer, batch, finding)
    matched: dict[str, dict[str, Any]] = {}
    for anchor in batch["anchors"]:
        if anchor["kind"] == "output_argument":
            anchor = command_anchor(anchor)
        elif anchor["kind"] == "registration":
            anchor = {"kind": "material", "path": anchor["path"]}
        key = canonical_json({k: v for k, v in anchor.items() if k != "defect"})
        matched.update((c["identity"], c) for c in commands.get(key, []))
    paths: set[str] = set()
    for command in matched.values():
        _link(writer, "commands", command["identity"], ("", identity, ""))
        paths.update(
            r["path"] for r in objects(command["inputs"]) + objects(command["outputs"])
        )
    for path in sorted(paths):
        writer.entity("artifacts", path, {"path": path})
        _link(writer, "artifacts", path, ("", identity, ""))


def _scalar(field: str, alias: str) -> str:
    """Select a known scalar in an inline object or its indexed mapping pieces."""
    return (
        f"coalesce(json_extract({alias}.payload, '$.fields.{field}'), "
        "(SELECT json_extract(p.payload, '$.fields.value') FROM pieces p "
        f"WHERE p.result={alias}.result "
        f"AND p.ref=json_extract({alias}.payload, '$.ref') "
        f"AND json_extract(p.payload, '$.fields.key')='{field}'))"
    )


def structure_summary(db: sqlite3.Connection, result_id: str) -> str:
    """Count cached primary Structure batches using indexed links, without sources.

    Read only distinct diagnostic categories and SQL aggregates; never expand
    a batch's member collection or load generated publication files.
    """
    categories = db.execute(
        f"SELECT DISTINCT code, {_scalar('scope', 'e')}, {_scalar('status', 'e')} "
        "FROM entities e WHERE result=? AND kind='findings'",
        (result_id,),
    ).fetchall()
    if any(any(value is None for value in row) for row in categories):
        raise InspectionError(
            "results.store.malformed", "missing finding classification"
        )
    selected = [
        row
        for row in categories
        if structure_finding(dict(zip(("code", "scope", "status"), row)))
    ]
    if any(status == "unavailable" for _, _, status in selected):
        return "—"
    if not selected:
        return "Clear"
    placeholders = ",".join("(?,?,?)" for _ in selected)
    rows = db.execute(
        f"SELECT {_scalar('batch_type', 'b')}, "
        f"{_scalar('grouping_reason', 'b')}, count(DISTINCT b.id) "
        "FROM entities b JOIN batch_links l ON l.result=b.result AND l.batch=b.id "
        "AND l.kind='findings' JOIN entities f ON f.result=l.result "
        "AND f.kind='findings' AND f.id=l.id "
        "WHERE b.result=? AND b.kind='batches' "
        f"AND (f.code, {_scalar('scope', 'f')}, {_scalar('status', 'f')}) IN ("
        + placeholders
        + ") "
        "GROUP BY 1, 2",
        [result_id, *(value for row in selected for value in row)],
    )
    counts: dict[str, int] = defaultdict(int)
    for batch_type, reason, count in rows:
        if batch_type not in {"chain", "structural"} or reason is None:
            raise InspectionError(
                "results.store.malformed", "missing batch classification"
            )
        counts["inspection" if reason == "inspection_group" else batch_type] += count
    return format_structure_counts(counts)
