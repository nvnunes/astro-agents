"""Replacement-only persistence for canonical validation snapshots."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from research_log_result_store import (
    REPLACEABLE_STORE_VERSIONS,
    STORE_VERSION,
    UNCHANGED_DOMAIN_STORE_VERSIONS,
    ResultStoreError,
    replace_validation_schema,
    result_snapshot,
    result_transaction,
)
from result_export_encoder import ExportTooLarge, measure_json_value

from .domain import (
    AdmissionOwner,
    Batch,
    BlockedCheck,
    FailedCheck,
    FailureOperation,
    Finding,
    GraphReference,
    RepairKey,
    RepairKeyKind,
    RuleArea,
    SnapshotOutcome,
    SourceLocation,
    ValidationSnapshot,
    ValidationTarget,
)

MAX_VALIDATION_SNAPSHOT_ROWS = 5_000_000
MAX_VALIDATION_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_VALIDATION_RESPONSE_BYTES = 16 * 1024
_RELATIONS = frozenset(("requested", "evaluated", "dependency"))
_Hook = Callable[[str], None]


@dataclass(frozen=True)
class SnapshotPublicationRequest:
    """Complete input for one replacement validation publication."""

    log_root: Path
    snapshot: ValidationSnapshot
    entry_relations: Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class StoredValidationSnapshot:
    """Identity of one atomically retained canonical snapshot."""

    snapshot_id: str
    generation: int
    stored_at: str


def entry_relations_from_evaluation(
    context: object,
) -> Mapping[str, tuple[str, ...]]:
    """Capture evaluator-owned target relations without observing sources again."""

    target = getattr(context, "target")
    selected = tuple(str(item) for item in getattr(context, "selected_documents"))
    requested = (
        (str(getattr(target, "entry_id")),)
        if hasattr(target, "entry_id")
        else selected
    )
    return {
        "requested": requested,
        "evaluated": selected,
        "dependency": tuple(
            str(item) for item in getattr(context, "dependency_entries")
        ),
    }


def publish_validation_snapshot(
    request: SnapshotPublicationRequest,
    *,
    _test_hook: _Hook | None = None,
) -> StoredValidationSnapshot:
    """Replace one slot without reading or translating superseded validation rows."""

    prepared = _prepare(request)
    _call_hook(_test_hook, "before_transaction")
    with result_transaction(request.log_root) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version in REPLACEABLE_STORE_VERSIONS:
            replace_validation_schema(db)
            db.execute(f"PRAGMA user_version={STORE_VERSION}")
        elif version not in UNCHANGED_DOMAIN_STORE_VERSIONS:
            raise ResultStoreError(
                "results.schema.unsupported",
                f"store version {version} is unsupported",
            )
        _call_hook(_test_hook, "after_schema_replacement")
        generation = _next_generation(db)
        snapshot = prepared.snapshot
        slot = _slot(snapshot)
        if snapshot.target.kind.value == "log":
            db.execute("DELETE FROM validation_snapshots")
        else:
            db.execute("DELETE FROM validation_snapshots WHERE slot=?", (slot,))
        snapshot_pk = int(
            db.execute(
                "SELECT COALESCE(MAX(snapshot_pk), 0) + 1 "
                "FROM validation_snapshots"
            ).fetchone()[0]
        )
        _insert_snapshot(db, snapshot_pk, generation, slot, prepared)
        _call_hook(_test_hook, "after_snapshot_insert")
        _insert_entries(db, snapshot_pk, prepared)
        finding_pks = _insert_findings(db, snapshot_pk, snapshot.findings)
        _insert_blocked_checks(db, snapshot_pk, snapshot.blocked_checks)
        _insert_failed_checks(db, snapshot_pk, snapshot.failed_checks)
        repair_key_pks = _insert_repair_keys(db, snapshot_pk, snapshot)
        node_pks = _insert_repair_context(db, snapshot_pk, snapshot)
        _insert_finding_context(
            db,
            snapshot_pk,
            snapshot.findings,
            finding_pks,
            repair_key_pks,
            node_pks,
            prepared.finding_node_ids,
        )
        _insert_batches(
            db,
            snapshot_pk,
            snapshot.batches,
            finding_pks,
            repair_key_pks,
        )
        _audit_snapshot(db, snapshot_pk, snapshot)
        db.execute(
            "INSERT INTO store_state VALUES ('validation', ?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET "
            "generation=excluded.generation, summary=excluded.summary",
            (generation, snapshot.outcome.value),
        )
        db.execute("DELETE FROM report_materializations WHERE kind='validation'")
        _call_hook(_test_hook, "before_commit")
    return StoredValidationSnapshot(
        prepared.snapshot.internal_snapshot_id,
        generation,
        cast(str, prepared.snapshot.stored_at),
    )


@dataclass(frozen=True)
class _PreparedSnapshot:
    snapshot: ValidationSnapshot
    relations: Mapping[str, tuple[str, ...]]
    nodes: tuple[Mapping[str, object], ...]
    relationships: tuple[Mapping[str, object], ...]
    ambiguities: tuple[Mapping[str, object], ...]
    finding_node_ids: Mapping[str, tuple[str, ...]]


def _prepare(request: SnapshotPublicationRequest) -> _PreparedSnapshot:
    from .snapshot_report import validate_snapshot_report_context

    snapshot = request.snapshot
    relations = _entry_relations(request.entry_relations, snapshot)
    repair = snapshot.repair_context
    nodes = _mapping_items(repair.get("nodes", ()), "repair-context nodes")
    relationships = _mapping_items(
        repair.get("relationships", ()), "repair-context relationships"
    )
    ambiguities = _mapping_items(
        repair.get("ambiguities", ()), "repair-context ambiguities"
    )
    stored_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    prepared = _PreparedSnapshot(
        replace(snapshot, stored_at=stored_at),
        relations,
        nodes,
        relationships,
        ambiguities,
        {},
    )
    _validate_references(prepared)
    validate_snapshot_report_context(prepared.snapshot)
    prepared = replace(prepared, finding_node_ids=_finding_node_memberships(prepared))
    _validate_bounds(prepared, request.log_root)
    return prepared


def _entry_relations(
    supplied: Mapping[str, Sequence[str]],
    snapshot: ValidationSnapshot,
) -> Mapping[str, tuple[str, ...]]:
    if set(supplied) - _RELATIONS:
        raise ValueError("snapshot entry relations contain an unsupported relation")
    values = {
        relation: tuple(sorted(set(supplied.get(relation, ()))))
        for relation in sorted(_RELATIONS)
    }
    values["repair"] = tuple(
        sorted({entry for batch in snapshot.batches for entry in batch.repair_entries})
    )
    values["context"] = tuple(
        sorted(
            {entry for batch in snapshot.batches for entry in batch.context_entries}
        )
    )
    if any(not entry for entries in values.values() for entry in entries):
        raise ValueError("snapshot entry relation is empty")
    return values


def _mapping_items(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{name} must contain objects")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _validate_references(prepared: _PreparedSnapshot) -> None:
    _validate_repair_context_structure(prepared)
    node_ids = _repair_node_ids(prepared.nodes)
    _validate_finding_and_batch_references(prepared.snapshot, node_ids)
    _validate_graph_references(prepared, node_ids)


def _validate_repair_context_structure(prepared: _PreparedSnapshot) -> None:
    _exact_fields(
        prepared.snapshot.repair_context,
        {"ambiguities", "nodes", "relationships"},
        set(),
        "repair context",
    )
    for node in prepared.nodes:
        _exact_fields(
            node,
            {"node_id", "kind", "identity", "attributes"},
            {"entry"},
            "repair node",
        )
        _string(node.get("node_id"), "repair node id")
        _string(node.get("kind"), "repair node kind")
        _string(node.get("identity"), "repair node identity")
        _optional_string(node.get("entry"), "repair node entry")
        _object(node.get("attributes"), "repair node attributes")
    for relationship in prepared.relationships:
        _exact_fields(
            relationship,
            {"kind", "source", "target"},
            set(),
            "repair relationship",
        )
        _string(relationship.get("kind"), "relationship kind")
        _string(relationship.get("source"), "relationship source")
        _string(relationship.get("target"), "relationship target")
    for ambiguity in prepared.ambiguities:
        _exact_fields(
            ambiguity,
            {"kind", "subject", "candidates", "observed"},
            set(),
            "repair ambiguity",
        )
        _string(ambiguity.get("kind"), "ambiguity kind")
        _string(ambiguity.get("subject"), "ambiguity subject")
        _object(ambiguity.get("observed"), "ambiguity observed")


def _repair_node_ids(nodes: Sequence[Mapping[str, object]]) -> set[str]:
    node_ids = {_string(item.get("node_id"), "repair node id") for item in nodes}
    if len(node_ids) != len(nodes):
        raise ValueError("repair-context node identities are duplicate")
    return node_ids


def _validate_finding_and_batch_references(
    snapshot: ValidationSnapshot,
    node_ids: set[str],
) -> None:
    finding_ids = {item.finding_id for item in snapshot.findings}
    memberships = [item for batch in snapshot.batches for item in batch.finding_ids]
    if sorted(memberships) != sorted(finding_ids):
        raise ValueError("every finding must belong to exactly one batch")
    referenced = {
        reference.node_id
        for finding in snapshot.findings
        for reference in finding.context_nodes
    }
    if not referenced <= node_ids:
        raise ValueError("finding references a missing repair-context node")


def _validate_graph_references(
    prepared: _PreparedSnapshot,
    node_ids: set[str],
) -> None:
    for relationship in prepared.relationships:
        endpoints = {
            _string(relationship.get("source"), "relationship source"),
            _string(relationship.get("target"), "relationship target"),
        }
        if not endpoints <= node_ids:
            raise ValueError("relationship references a missing repair-context node")
    for ambiguity in prepared.ambiguities:
        subject = _string(ambiguity.get("subject"), "ambiguity subject")
        candidates = ambiguity.get("candidates")
        if not isinstance(candidates, (list, tuple)) or any(
            not isinstance(item, str) or not item for item in candidates
        ):
            raise ValueError("ambiguity candidates are invalid")
        if len(candidates) != len(set(candidates)):
            raise ValueError("ambiguity candidates must be unique")
        if subject not in node_ids or not set(candidates) <= node_ids:
            raise ValueError("ambiguity references a missing repair-context node")


def _exact_fields(
    value: Mapping[str, object],
    required: set[str],
    optional: set[str],
    name: str,
) -> None:
    keys = set(value)
    if missing := required - keys:
        raise ValueError(f"{name} is missing fields: {sorted(missing)}")
    if extra := keys - required - optional:
        raise ValueError(f"{name} has unsupported fields: {sorted(extra)}")


def _validate_bounds(prepared: _PreparedSnapshot, log_root: Path) -> None:
    try:
        measure_json_value(prepared.snapshot.as_dict(), MAX_VALIDATION_SNAPSHOT_BYTES)
    except ExportTooLarge as error:
        raise ValueError("validation snapshot exceeds its byte bound") from error
    rows = _fixed_row_count(prepared)
    rows += sum(len(value) for value in prepared.finding_node_ids.values())
    if rows > MAX_VALIDATION_SNAPSHOT_ROWS:
        raise ValueError("validation snapshot exceeds its row bound")
    _validate_finding_detail_bounds(prepared.snapshot, log_root)


def _validate_finding_detail_bounds(
    snapshot: ValidationSnapshot, log_root: Path
) -> None:
    """Reject findings whose complete diagnostic cannot fit a detail response."""

    findings = {finding.finding_id: finding for finding in snapshot.findings}
    batches = {
        finding_id: batch
        for batch in snapshot.batches
        for finding_id in batch.finding_ids
    }
    batch_values = {
        batch.batch_id: _batch_detail_value(batch, findings)
        for batch in snapshot.batches
    }
    presentations = cast(
        Mapping[str, Mapping[str, str]], snapshot.report_context["presentations"]
    )
    for finding in snapshot.findings:
        batch = batches[finding.finding_id]
        finding_value: dict[str, object] = {
            "caused_by": list(finding.caused_by),
            "code": finding.code,
            "diagnosis": {
                "explanation": presentations[finding.code]["sentence"],
                "title": presentations[finding.code]["name"],
            },
            "finding_id": finding.finding_id,
            "observed": _plain(finding.observed),
            "rule": finding.rule,
            "source_locations": [item.as_dict() for item in finding.source_locations],
            "subject": finding.subject,
            "type": finding.type.value,
        }
        if finding.entry is not None:
            finding_value["entry"] = finding.entry
        batch_value = batch_values[batch.batch_id]
        value: dict[str, object] = {
            "batch": batch_value,
            "finding": finding_value,
            "items": [],
            "log": str(log_root),
            "saved_at": snapshot.stored_at,
            "schema": "research-log-validation-finding-detail/1",
            "section": "relationships",
            "section_total": MAX_VALIDATION_SNAPSHOT_ROWS,
            "selected_target": snapshot.target.as_dict(),
        }
        if len(json.dumps(value, ensure_ascii=False).encode()) > (
            MAX_VALIDATION_RESPONSE_BYTES
        ):
            raise ValueError("finding detail header exceeds its response byte bound")


def _batch_detail_value(
    batch: Batch, findings: Mapping[str, Finding]
) -> dict[str, object]:
    return {
        "batch_id": batch.batch_id,
        "context_entries": list(batch.context_entries),
        "finding_count": len(batch.finding_ids),
        "focus_finding_id": batch.focus_finding_id,
        "rationale": list(batch.rationale),
        "repair_entries": list(batch.repair_entries),
        "represented_types": sorted(
            {findings[item].type.value for item in batch.finding_ids}
        ),
    }


def _fixed_row_count(prepared: _PreparedSnapshot) -> int:
    snapshot = prepared.snapshot
    rows = 1 + sum(len(items) for items in prepared.relations.values())
    rows += (
        len(snapshot.findings)
        + len(snapshot.batches)
        + len(snapshot.blocked_checks)
        + len(snapshot.failed_checks)
        + len(prepared.nodes)
    )
    rows += len(
        {
            (key.kind.value, key.identity)
            for finding in snapshot.findings
            for key in finding.repair_keys
        }
    )
    rows += len(prepared.relationships) * 2
    rows += sum(
        1 + len(cast(Sequence[object], item.get("candidates", ())))
        for item in prepared.ambiguities
    )
    rows += sum(
        len(item.caused_by)
        + len(item.source_locations)
        + len(item.repair_keys)
        for item in snapshot.findings
    )
    rows += sum(
        len(item.finding_ids)
        + len(item.repair_keys)
        + len(item.rationale)
        + len(item.repair_entries)
        + len(item.context_entries)
        for item in snapshot.batches
    )
    return rows


def _next_generation(db: object) -> int:
    row = db.execute(  # type: ignore[attr-defined]
        "SELECT generation FROM store_state WHERE domain='validation'"
    ).fetchone()
    return 1 if row is None else int(row[0]) + 1


def _slot(snapshot: ValidationSnapshot) -> str:
    if snapshot.target.kind.value == "log":
        return "full"
    assert snapshot.target.entry is not None
    return f"entry:{snapshot.target.entry}"


def _insert_snapshot(
    db: object,
    snapshot_pk: int,
    generation: int,
    slot: str,
    prepared: _PreparedSnapshot,
) -> None:
    snapshot = prepared.snapshot
    db.execute(  # type: ignore[attr-defined]
        "INSERT INTO validation_snapshots VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            snapshot_pk,
            snapshot.internal_snapshot_id,
            generation,
            slot,
            snapshot.target.kind.value,
            snapshot.target.log,
            snapshot.target.entry,
            snapshot.outcome.value,
            snapshot.passed_check_count,
            len(snapshot.findings),
            len(snapshot.batches),
            len(snapshot.blocked_checks),
            len(snapshot.failed_checks),
            snapshot.started_at,
            snapshot.finished_at,
            snapshot.stored_at,
            snapshot.rules_version,
            snapshot.source_identity,
            _json(snapshot.report_context),
        ),
    )


def _insert_blocked_checks(
    db: object,
    snapshot_pk: int,
    checks: Sequence[BlockedCheck],
) -> None:
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_blocked_checks VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                snapshot_pk,
                position + 1,
                check.check_id,
                position,
                check.area.value,
                check.entry,
                check.subject,
                check.rule,
                _json(check.blocked_by),
            )
            for position, check in enumerate(checks)
        ],
    )


def _insert_failed_checks(
    db: object,
    snapshot_pk: int,
    checks: Sequence[FailedCheck],
) -> None:
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_failed_checks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                snapshot_pk,
                position + 1,
                check.check_id,
                position,
                check.area.value,
                check.entry,
                check.subject,
                check.code,
                check.rule,
                check.operation.value,
                _json(check.reason),
            )
            for position, check in enumerate(checks)
        ],
    )


def _insert_entries(db: object, snapshot_pk: int, prepared: _PreparedSnapshot) -> None:
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_snapshot_entries VALUES (?,?,?,?)",
        [
            (snapshot_pk, relation, position, entry)
            for relation, entries in prepared.relations.items()
            for position, entry in enumerate(entries)
        ],
    )


def _insert_findings(
    db: object,
    snapshot_pk: int,
    findings: Sequence[Finding],
) -> Mapping[str, int]:
    pks = {finding.finding_id: position for position, finding in enumerate(findings, 1)}
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_findings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                snapshot_pk,
                pks[finding.finding_id],
                finding.finding_id,
                position,
                finding.type.value,
                finding.code,
                finding.entry,
                finding.subject,
                finding.rule,
                _json(finding.observed),
                (
                    None
                    if finding.admission_owner is None
                    else finding.admission_owner.value
                ),
            )
            for position, finding in enumerate(findings)
        ],
    )
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_finding_causes VALUES (?,?,?,?)",
        [
            (snapshot_pk, pks[finding.finding_id], position, pks[cause])
            for finding in findings
            for position, cause in enumerate(finding.caused_by)
        ],
    )
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_finding_source_locations VALUES (?,?,?,?,?)",
        [
            (snapshot_pk, pks[finding.finding_id], position, item.path, item.line)
            for finding in findings
            for position, item in enumerate(finding.source_locations)
        ],
    )
    return pks


def _insert_repair_keys(
    db: object,
    snapshot_pk: int,
    snapshot: ValidationSnapshot,
) -> Mapping[tuple[str, str], int]:
    keys: set[tuple[str, str]] = set()
    for key in (
        *(key for finding in snapshot.findings for key in finding.repair_keys),
        *(key for batch in snapshot.batches for key in batch.repair_keys),
    ):
        keys.add((key.kind.value, key.identity))
    ordered = sorted(keys)
    pks = {identity: position for position, identity in enumerate(ordered, 1)}
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_repair_keys VALUES (?,?,?,?)",
        [
            (snapshot_pk, pks[identity], identity[0], identity[1])
            for identity in ordered
        ],
    )
    return pks


def _insert_repair_context(
    db: object,
    snapshot_pk: int,
    snapshot: ValidationSnapshot,
) -> Mapping[str, int]:
    nodes = _mapping_items(snapshot.repair_context.get("nodes", ()), "repair nodes")
    node_pks = {
        _string(node.get("node_id"), "repair node id"): position
        for position, node in enumerate(nodes, 1)
    }
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_repair_nodes VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                snapshot_pk,
                node_pks[_string(node.get("node_id"), "repair node id")],
                _string(node.get("node_id"), "repair node id"),
                position,
                _string(node.get("kind"), "repair node kind"),
                _string(node.get("identity"), "repair node identity"),
                _optional_string(node.get("entry"), "repair node entry"),
                _json(_object(node.get("attributes"), "repair node attributes")),
            )
            for position, node in enumerate(nodes)
        ],
    )
    edges = _mapping_items(
        snapshot.repair_context.get("relationships", ()), "repair relationships"
    )
    ambiguities = _mapping_items(
        snapshot.repair_context.get("ambiguities", ()), "repair ambiguities"
    )
    edge_pk = 0
    for position, edge in enumerate(edges):
        edge_pk += 1
        source = _string(edge.get("source"), "relationship source")
        target = _string(edge.get("target"), "relationship target")
        db.execute(  # type: ignore[attr-defined]
            "INSERT INTO validation_repair_edges VALUES (?,?,?,?,?,?,?)",
            (
                snapshot_pk,
                edge_pk,
                position,
                "established",
                _string(edge.get("kind"), "relationship kind"),
                node_pks[source],
                None,
            ),
        )
        db.execute(  # type: ignore[attr-defined]
            "INSERT INTO validation_repair_edge_targets VALUES (?,?,?,?)",
            (snapshot_pk, edge_pk, 0, node_pks[target]),
        )
    for offset, ambiguity in enumerate(ambiguities, len(edges)):
        edge_pk += 1
        subject = _string(ambiguity.get("subject"), "ambiguity subject")
        observed = _object(ambiguity.get("observed"), "ambiguity observed")
        db.execute(  # type: ignore[attr-defined]
            "INSERT INTO validation_repair_edges VALUES (?,?,?,?,?,?,?)",
            (
                snapshot_pk,
                edge_pk,
                offset,
                "ambiguous",
                _string(ambiguity.get("kind"), "ambiguity kind"),
                node_pks[subject],
                _json(observed),
            ),
        )
        db.executemany(  # type: ignore[attr-defined]
            "INSERT INTO validation_repair_edge_targets VALUES (?,?,?,?)",
            [
                (snapshot_pk, edge_pk, position, node_pks[str(candidate)])
                for position, candidate in enumerate(
                    cast(Sequence[str], ambiguity.get("candidates", ()))
                )
            ],
        )
    return node_pks


def _insert_finding_context(  # noqa: PLR0913 -- normalized insert indexes
    db: object,
    snapshot_pk: int,
    findings: Sequence[Finding],
    finding_pks: Mapping[str, int],
    repair_key_pks: Mapping[tuple[str, str], int],
    node_pks: Mapping[str, int],
    finding_node_ids: Mapping[str, tuple[str, ...]],
) -> None:
    context_node_ids = {
        finding.finding_id: {reference.node_id for reference in finding.context_nodes}
        for finding in findings
    }
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_finding_repair_keys VALUES (?,?,?,?,?)",
        [
            (
                snapshot_pk,
                finding_pks[finding.finding_id],
                position,
                repair_key_pks[(key.kind.value, key.identity)],
                key.repair_entry,
            )
            for finding in findings
            for position, key in enumerate(finding.repair_keys)
        ],
    )
    db.executemany(  # type: ignore[attr-defined]
        "INSERT INTO validation_finding_nodes VALUES (?,?,?,?,?)",
        [
            (
                snapshot_pk,
                finding_pks[finding.finding_id],
                position,
                (
                    "context"
                    if node_id in context_node_ids[finding.finding_id]
                    else "repair"
                ),
                node_pks[node_id],
            )
            for finding in findings
            for position, node_id in enumerate(finding_node_ids[finding.finding_id])
        ],
    )


def _insert_batches(  # noqa: PLR0913 -- normalized insert needs exact indexes
    db: object,
    snapshot_pk: int,
    batches: Sequence[Batch],
    finding_pks: Mapping[str, int],
    repair_key_pks: Mapping[tuple[str, str], int],
) -> None:
    batch_pks = {batch.batch_id: position for position, batch in enumerate(batches, 1)}
    for position, batch in enumerate(batches):
        batch_pk = batch_pks[batch.batch_id]
        db.execute(  # type: ignore[attr-defined]
            "INSERT INTO validation_batches VALUES (?,?,?,?,?,?)",
            (
                snapshot_pk,
                batch_pk,
                batch.batch_id,
                position,
                finding_pks[batch.focus_finding_id],
                len(batch.finding_ids),
            ),
        )
        db.executemany(  # type: ignore[attr-defined]
            "INSERT INTO validation_batch_rationale VALUES (?,?,?,?)",
            [
                (snapshot_pk, batch_pk, item_position, value)
                for item_position, value in enumerate(batch.rationale)
            ],
        )
        db.executemany(  # type: ignore[attr-defined]
            "INSERT INTO validation_batch_findings VALUES (?,?,?,?)",
            [
                (snapshot_pk, batch_pk, item_position, finding_pks[finding_id])
                for item_position, finding_id in enumerate(batch.finding_ids)
            ],
        )
        db.executemany(  # type: ignore[attr-defined]
            "INSERT INTO validation_batch_repair_keys VALUES (?,?,?,?)",
            [
                (
                    snapshot_pk,
                    batch_pk,
                    item_position,
                    repair_key_pks[(key.kind.value, key.identity)],
                )
                for item_position, key in enumerate(batch.repair_keys)
            ],
        )
        db.executemany(  # type: ignore[attr-defined]
            "INSERT INTO validation_batch_entries VALUES (?,?,?,?,?)",
            [
                (snapshot_pk, batch_pk, role, item_position, entry)
                for role, entries in (
                    ("repair", batch.repair_entries),
                    ("context", batch.context_entries),
                )
                for item_position, entry in enumerate(entries)
            ],
        )


@dataclass(frozen=True)
class _RepairNeighborhoodIndex:
    kinds: Mapping[str, str]
    adjacent: Mapping[str, tuple[tuple[str, str], ...]]
    ambiguity_neighbors: Mapping[str, tuple[frozenset[str], ...]]

    @classmethod
    def build(cls, prepared: _PreparedSnapshot) -> _RepairNeighborhoodIndex:
        kinds = {
            _string(node.get("node_id"), "repair node id"): _string(
                node.get("kind"), "repair node kind"
            )
            for node in prepared.nodes
        }
        adjacent: dict[str, list[tuple[str, str]]] = {}
        for relationship in prepared.relationships:
            source = _string(relationship.get("source"), "relationship source")
            target = _string(relationship.get("target"), "relationship target")
            adjacent.setdefault(source, []).append((source, target))
            adjacent.setdefault(target, []).append((source, target))
        ambiguity_neighbors: dict[str, list[frozenset[str]]] = {}
        for ambiguity in prepared.ambiguities:
            participants = frozenset(
                {
                    _string(ambiguity.get("subject"), "ambiguity subject"),
                    *cast(Sequence[str], ambiguity.get("candidates", ())),
                }
            )
            for participant in participants:
                ambiguity_neighbors.setdefault(participant, []).append(participants)
        return cls(
            kinds,
            {key: tuple(value) for key, value in adjacent.items()},
            {key: tuple(value) for key, value in ambiguity_neighbors.items()},
        )


@dataclass
class _RepairNeighborhoodCollector:
    index: _RepairNeighborhoodIndex
    included: set[str]
    limit: int
    operation_frontier: list[str] = field(init=False)
    ambiguity_frontier: list[str] = field(init=False)
    expanded: set[str] = field(default_factory=set)
    ambiguity_seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if len(self.included) > self.limit:
            raise ValueError("validation snapshot exceeds its row bound")
        self.operation_frontier = [
            item for item in self.included if self._is_operational(item)
        ]
        self.ambiguity_frontier = list(self.included)

    def collect(self) -> set[str]:
        for node_id in tuple(sorted(self.included)):
            for edge in self.index.adjacent.get(node_id, ()):
                self._include_edge(edge)
        while self.operation_frontier or self.ambiguity_frontier:
            self._drain_ambiguities()
            self._drain_operations()
        return self.included

    def _is_operational(self, node_id: str) -> bool:
        return self.index.kinds[node_id] in {"collection", "command", "execution"}

    def _admit(self, node_id: str) -> None:
        if node_id in self.included:
            return
        self.included.add(node_id)
        if len(self.included) > self.limit:
            raise ValueError("validation snapshot exceeds its row bound")
        self.ambiguity_frontier.append(node_id)
        if self._is_operational(node_id):
            self.operation_frontier.append(node_id)

    def _include_edge(self, edge: tuple[str, str]) -> None:
        self._admit(edge[0])
        self._admit(edge[1])

    def _drain_ambiguities(self) -> None:
        while self.ambiguity_frontier:
            node_id = self.ambiguity_frontier.pop()
            if node_id in self.ambiguity_seen:
                continue
            self.ambiguity_seen.add(node_id)
            for participants in self.index.ambiguity_neighbors.get(node_id, ()):
                for participant in participants:
                    self._admit(participant)

    def _drain_operations(self) -> None:
        while self.operation_frontier:
            node_id = self.operation_frontier.pop()
            if node_id in self.expanded:
                continue
            self.expanded.add(node_id)
            for edge in self.index.adjacent.get(node_id, ()):
                self._include_edge(edge)


def _finding_node_memberships(
    prepared: _PreparedSnapshot,
) -> Mapping[str, tuple[str, ...]]:
    nodes_by_identity: dict[str, set[str]] = {}
    for node in prepared.nodes:
        node_id = _string(node.get("node_id"), "repair node id")
        identity = _string(node.get("identity"), "repair node identity")
        nodes_by_identity.setdefault(identity, set()).add(node_id)
    finding_nodes = {
        finding.finding_id: tuple(
            sorted(
                {
                    *(reference.node_id for reference in finding.context_nodes),
                    *(
                        node_id
                        for key in finding.repair_keys
                        for node_id in nodes_by_identity.get(key.identity, ())
                    ),
                }
            )
        )
        for finding in prepared.snapshot.findings
    }
    return finding_nodes


def batch_repair_node_ids(
    snapshot: ValidationSnapshot,
    batch_id: str,
) -> tuple[str, ...]:
    """Derive one batch's repair neighborhood from direct saved anchors."""

    repair = snapshot.repair_context
    nodes = _mapping_items(repair.get("nodes", ()), "repair-context nodes")
    prepared = _PreparedSnapshot(
        snapshot,
        {},
        nodes,
        _mapping_items(
            repair.get("relationships", ()), "repair-context relationships"
        ),
        _mapping_items(repair.get("ambiguities", ()), "repair-context ambiguities"),
        {},
    )
    _validate_references(prepared)
    return _batch_repair_membership(prepared, batch_id)


def _batch_repair_membership(
    prepared: _PreparedSnapshot,
    batch_id: str,
) -> tuple[str, ...]:
    nodes_by_identity: dict[str, set[str]] = {}
    for node in prepared.nodes:
        node_id = _string(node.get("node_id"), "repair node id")
        identity = _string(node.get("identity"), "repair node identity")
        nodes_by_identity.setdefault(identity, set()).add(node_id)
    finding_nodes = _finding_node_memberships(prepared)
    try:
        batch = next(
            item for item in prepared.snapshot.batches if item.batch_id == batch_id
        )
    except StopIteration as error:
        raise ValueError("batch is not in the validation snapshot") from error
    index = _RepairNeighborhoodIndex.build(prepared)
    return _collect_batch_node_ids(
        batch,
        finding_nodes,
        nodes_by_identity,
        index,
        len(prepared.nodes),
    )


def _collect_batch_node_ids(
    batch: Batch,
    finding_nodes: Mapping[str, tuple[str, ...]],
    nodes_by_identity: Mapping[str, set[str]],
    index: _RepairNeighborhoodIndex,
    limit: int,
) -> tuple[str, ...]:
    seeds = {
        *(
            node_id
            for finding_id in batch.finding_ids
            for node_id in finding_nodes[finding_id]
        ),
        *(
            node_id
            for key in batch.repair_keys
            for node_id in nodes_by_identity.get(key.identity, ())
        ),
    }
    return tuple(
        sorted(_RepairNeighborhoodCollector(index, seeds, limit).collect())
    )


def _audit_snapshot(db: object, snapshot_pk: int, snapshot: ValidationSnapshot) -> None:
    row = db.execute(  # type: ignore[attr-defined]
        "SELECT finding_count,batch_count,blocked_count,failed_count "
        "FROM validation_snapshots "
        "WHERE snapshot_pk=?",
        (snapshot_pk,),
    ).fetchone()
    counts = db.execute(  # type: ignore[attr-defined]
        "SELECT (SELECT count(*) FROM validation_findings WHERE snapshot_pk=?),"
        "(SELECT count(*) FROM validation_batches WHERE snapshot_pk=?),"
        "(SELECT count(*) FROM validation_blocked_checks WHERE snapshot_pk=?),"
        "(SELECT count(*) FROM validation_failed_checks WHERE snapshot_pk=?),"
        "(SELECT count(*) FROM validation_batch_findings WHERE snapshot_pk=?)",
        (snapshot_pk, snapshot_pk, snapshot_pk, snapshot_pk, snapshot_pk),
    ).fetchone()
    expected_row = (
        len(snapshot.findings),
        len(snapshot.batches),
        len(snapshot.blocked_checks),
        len(snapshot.failed_checks),
    )
    expected_counts = (*expected_row, len(snapshot.findings))
    if row is None or tuple(row) != expected_row or tuple(counts) != expected_counts:
        raise ValueError("stored validation snapshot counts are inconsistent")
    violations = db.execute("PRAGMA foreign_key_check").fetchall()  # type: ignore[attr-defined]
    if violations:
        raise ValueError("stored validation snapshot violates foreign keys")


def load_validation_snapshot(
    log_root: Path,
    *,
    slot: str = "full",
) -> ValidationSnapshot:
    """Load one canonical snapshot without observing research-owned files."""

    with result_snapshot(log_root) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version in REPLACEABLE_STORE_VERSIONS:
            raise ResultStoreError(
                "validation.replacement_required",
                "saved validation requires replacement; run "
                f"log validate run --path "
                f"{shlex.quote(str(log_root))}",
            )
        row = db.execute(
            "SELECT * FROM validation_snapshots WHERE slot=?", (slot,)
        ).fetchone()
        if row is None:
            raise ResultStoreError("validation.not_validated", "no saved validation")
        try:
            return _load_snapshot(db, row)
        except ResultStoreError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ResultStoreError(
                "results.store.malformed",
                "stored validation snapshot violates its canonical contract",
            ) from error


def _load_snapshot(db: Any, row: Any) -> ValidationSnapshot:
    snapshot_pk = int(row["snapshot_pk"])
    finding_rows = db.execute(
        "SELECT * FROM validation_findings WHERE snapshot_pk=? ORDER BY position",
        (snapshot_pk,),
    ).fetchall()
    finding_ids = {
        int(item["finding_pk"]): str(item["finding_id"])
        for item in finding_rows
    }
    findings = tuple(
        _load_finding(db, snapshot_pk, item, finding_ids) for item in finding_rows
    )
    blocked_checks = tuple(
        BlockedCheck(
            str(item["check_id"]),
            RuleArea(str(item["area"])),
            None if item["entry"] is None else str(item["entry"]),
            str(item["subject"]),
            str(item["rule"]),
            _json_string_array(
                item["blocked_by_json"], "blocked-check blockers"
            ),
        )
        for item in db.execute(
            "SELECT * FROM validation_blocked_checks "
            "WHERE snapshot_pk=? ORDER BY position",
            (snapshot_pk,),
        )
    )
    failed_checks = tuple(
        FailedCheck(
            str(item["check_id"]),
            RuleArea(str(item["area"])),
            None if item["entry"] is None else str(item["entry"]),
            str(item["code"]),
            str(item["subject"]),
            str(item["rule"]),
            FailureOperation(str(item["operation"])),
            _json_mapping(item["reason_json"], "failed-check reason"),
        )
        for item in db.execute(
            "SELECT * FROM validation_failed_checks "
            "WHERE snapshot_pk=? ORDER BY position",
            (snapshot_pk,),
        )
    )
    batches = _load_batches(db, snapshot_pk, finding_ids)
    repair_context = _load_repair_context(db, snapshot_pk)
    snapshot = ValidationSnapshot(
        str(row["snapshot_id"]),
        ValidationTarget.from_dict(
            {
                "kind": str(row["target_kind"]),
                "log": str(row["target_log"]),
                **(
                    {}
                    if row["target_entry"] is None
                    else {"entry": str(row["target_entry"])}
                ),
            }
        ),
        str(row["source_identity"]),
        str(row["rules_version"]),
        str(row["started_at"]),
        str(row["finished_at"]),
        str(row["stored_at"]),
        SnapshotOutcome(str(row["outcome"])),
        int(row["passed_check_count"]),
        findings,
        batches,
        blocked_checks,
        failed_checks,
        repair_context,
        _json_mapping(row["report_context_json"], "report context"),
    )
    if len(findings) != int(row["finding_count"]) or len(batches) != int(
        row["batch_count"]
    ) or len(blocked_checks) != int(row["blocked_count"]) or len(
        failed_checks
    ) != int(row["failed_count"]):
        raise ResultStoreError("results.store.malformed", "snapshot counts disagree")
    return snapshot


def _load_finding(
    db: Any,
    snapshot_pk: int,
    row: Any,
    finding_ids: Mapping[int, str],
) -> Finding:
    finding_pk = int(row["finding_pk"])
    causes = tuple(
        finding_ids[int(item[0])]
        for item in db.execute(
            "SELECT cause_finding_pk FROM validation_finding_causes "
            "WHERE snapshot_pk=? AND finding_pk=? ORDER BY position",
            (snapshot_pk, finding_pk),
        )
    )
    repair_keys = tuple(
        RepairKey(
            RepairKeyKind(str(item["kind"])),
            str(item["identity"]),
            None if item["repair_entry"] is None else str(item["repair_entry"]),
        )
        for item in db.execute(
            "SELECT k.*,f.repair_entry FROM validation_finding_repair_keys AS f "
            "JOIN validation_repair_keys AS k USING (snapshot_pk,repair_key_pk) "
            "WHERE f.snapshot_pk=? AND f.finding_pk=? ORDER BY f.position",
            (snapshot_pk, finding_pk),
        )
    )
    nodes = tuple(
        GraphReference(
            str(item["kind"]),
            str(item["identity"]),
            None if item["entry"] is None else str(item["entry"]),
        )
        for item in db.execute(
            "SELECT n.* FROM validation_finding_nodes AS f "
            "JOIN validation_repair_nodes AS n USING (snapshot_pk,node_pk) "
            "WHERE f.snapshot_pk=? AND f.finding_pk=? AND f.role='context' "
            "ORDER BY f.position",
            (snapshot_pk, finding_pk),
        )
    )
    locations = tuple(
        SourceLocation(str(item["path"]), item["line"])
        for item in db.execute(
            "SELECT path,line FROM validation_finding_source_locations "
            "WHERE snapshot_pk=? AND finding_pk=? ORDER BY position",
            (snapshot_pk, finding_pk),
        )
    )
    owner = row["admission_owner"]
    return Finding(
        str(row["finding_id"]),
        RuleArea(str(row["type"])),
        str(row["code"]),
        None if row["entry"] is None else str(row["entry"]),
        str(row["subject"]),
        str(row["rule"]),
        _json_mapping(row["observed_json"], "finding observed"),
        causes,
        repair_keys,
        nodes,
        locations,
        None if owner is None else AdmissionOwner(str(owner)),
    )


def _load_batches(
    db: object,
    snapshot_pk: int,
    finding_ids: Mapping[int, str],
) -> tuple[Batch, ...]:
    values = []
    for row in db.execute(  # type: ignore[attr-defined]
        "SELECT * FROM validation_batches WHERE snapshot_pk=? ORDER BY position",
        (snapshot_pk,),
    ):
        batch_pk = int(row["batch_pk"])
        members = tuple(
            finding_ids[int(item[0])]
            for item in db.execute(  # type: ignore[attr-defined]
                "SELECT finding_pk FROM validation_batch_findings "
                "WHERE snapshot_pk=? AND batch_pk=? ORDER BY position",
                (snapshot_pk, batch_pk),
            )
        )
        entries = {
            role: tuple(
                str(item[0])
                for item in db.execute(  # type: ignore[attr-defined]
                    "SELECT entry FROM validation_batch_entries "
                    "WHERE snapshot_pk=? AND batch_pk=? AND role=? ORDER BY position",
                    (snapshot_pk, batch_pk, role),
                )
            )
            for role in ("repair", "context")
        }
        keys = tuple(
            RepairKey(RepairKeyKind(str(item["kind"])), str(item["identity"]))
            for item in db.execute(  # type: ignore[attr-defined]
                "SELECT k.* FROM validation_batch_repair_keys AS b "
                "JOIN validation_repair_keys AS k USING (snapshot_pk,repair_key_pk) "
                "WHERE b.snapshot_pk=? AND b.batch_pk=? ORDER BY b.position",
                (snapshot_pk, batch_pk),
            )
        )
        rationale = tuple(
            str(item[0])
            for item in db.execute(  # type: ignore[attr-defined]
                "SELECT rationale FROM validation_batch_rationale "
                "WHERE snapshot_pk=? AND batch_pk=? ORDER BY position",
                (snapshot_pk, batch_pk),
            )
        )
        values.append(
            Batch(
                str(row["batch_id"]),
                members,
                entries["repair"],
                entries["context"],
                keys,
                rationale,
                finding_ids[int(row["focus_finding_pk"])],
            )
        )
    return tuple(values)


def _load_repair_context(
    db: object,
    snapshot_pk: int,
) -> Mapping[str, object]:
    node_rows = db.execute(  # type: ignore[attr-defined]
        "SELECT * FROM validation_repair_nodes WHERE snapshot_pk=? ORDER BY position",
        (snapshot_pk,),
    ).fetchall()
    node_ids = {int(item["node_pk"]): str(item["node_id"]) for item in node_rows}
    nodes = [
        {
            "attributes": _json_mapping(item["attributes_json"], "node attributes"),
            "identity": str(item["identity"]),
            "kind": str(item["kind"]),
            "node_id": str(item["node_id"]),
            **({} if item["entry"] is None else {"entry": str(item["entry"])}),
        }
        for item in node_rows
    ]
    relationships: list[Mapping[str, object]] = []
    ambiguities: list[Mapping[str, object]] = []
    for edge in db.execute(  # type: ignore[attr-defined]
        "SELECT * FROM validation_repair_edges WHERE snapshot_pk=? ORDER BY position",
        (snapshot_pk,),
    ):
        targets = tuple(
            node_ids[int(item[0])]
            for item in db.execute(  # type: ignore[attr-defined]
                "SELECT target_node_pk FROM validation_repair_edge_targets "
                "WHERE snapshot_pk=? AND edge_pk=? ORDER BY position",
                (snapshot_pk, int(edge["edge_pk"])),
            )
        )
        subject = node_ids[int(edge["subject_node_pk"])]
        if edge["relation_kind"] == "established":
            if len(targets) != 1:
                raise ResultStoreError(
                    "results.store.malformed", "established edge target is invalid"
                )
            relationships.append(
                {"kind": str(edge["kind"]), "source": subject, "target": targets[0]}
            )
        else:
            ambiguities.append(
                {
                    "candidates": list(targets),
                    "kind": str(edge["kind"]),
                    "observed": _json_mapping(
                        edge["observed_json"], "ambiguity observed"
                    ),
                    "subject": subject,
                }
            )
    value: dict[str, object] = {
        "ambiguities": ambiguities,
        "nodes": nodes,
        "relationships": relationships,
    }
    return value


def _json(value: object) -> str:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _json_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, str):
        raise ResultStoreError("results.store.malformed", f"{name} is not JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ResultStoreError(
            "results.store.malformed", f"{name} is invalid"
        ) from error
    return _object(parsed, name)


def _json_array(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, str):
        raise ResultStoreError("results.store.malformed", f"{name} is not JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ResultStoreError(
            "results.store.malformed", f"{name} is invalid"
        ) from error
    if not isinstance(parsed, list) or any(
        not isinstance(item, Mapping) for item in parsed
    ):
        raise ResultStoreError("results.store.malformed", f"{name} is invalid")
    return tuple(cast(Mapping[str, object], item) for item in parsed)


def _json_string_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ResultStoreError("results.store.malformed", f"{name} is not JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ResultStoreError(
            "results.store.malformed", f"{name} is invalid"
        ) from error
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item for item in parsed
    ):
        raise ResultStoreError("results.store.malformed", f"{name} is invalid")
    return tuple(parsed)


def _integer_mapping(value: object, name: str) -> Mapping[str, int]:
    parsed = _json_mapping(value, name)
    if any(
        not isinstance(item, int) or isinstance(item, bool)
        for item in parsed.values()
    ):
        raise ResultStoreError("results.store.malformed", f"{name} is invalid")
    return cast(Mapping[str, int], parsed)


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _call_hook(hook: _Hook | None, phase: str) -> None:
    if hook is not None:
        hook(phase)
