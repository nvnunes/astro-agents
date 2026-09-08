"""Read-only, indexed views of tool-owned validation snapshots.

No query opens published records or research sources. Continuations pin exact
result IDs; result listings also pin the store generation. Only explicit export
expands complete collection and text content.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from validation.inspection_store import (
    InspectionError,
    collection_metadata,
    connection,
    decode_node,
    encode,
    expand,
    read_pieces,
    unpack_view,
)

VIEW_SCHEMA = "research-log-result-view/1"
MAX_VIEW_BYTES = 16 * 1024
LIST_FIELDS = (
    "result_id",
    "kind",
    "finished_at",
    "status",
    "requested_entry",
    "requested_chain",
    "origin_projection_id",
    "projection_id",
)
VIEWS = (
    "summary",
    "codes",
    "findings",
    "chains",
    "commands",
    "artifacts",
    "collections",
    "overlaps",
)


@dataclass(frozen=True)
class Query:
    """Exact selectors and bounded continuation for one public result view."""

    action: str = "show"
    result_id: str | None = None
    latest: bool = False
    kind: str | None = None
    view: str = "summary"
    entry: str | None = None
    chain: str | None = None
    code: str | None = None
    projection: str | None = None
    entity: str | None = None
    limit: int = 20
    cursor: str | None = None


@dataclass(frozen=True)
class _Selection:
    """Indexed row selection and optional ingestion-time total for pagination."""

    sql: str
    parameters: list[Any]
    key: str
    descending: bool = False
    total: int | None = None


def _where(query: Query, *, listing: bool) -> tuple[str, list[Any]]:
    fields = (
        {
            "kind": query.kind,
            "entry": query.entry,
            "chain": query.chain,
            "projection": query.projection,
        }
        if listing
        else {"entry": query.entry, "chain": query.chain, "code": query.code}
    )
    selected = [(key, value) for key, value in fields.items() if value is not None]
    return "".join(f" AND {key}=?" for key, _ in selected), [
        value for _, value in selected
    ]


def _cursor(query: Query, binding: Any) -> dict[str, Any] | None:
    if query.cursor is None:
        return None
    try:
        if len(query.cursor) > 4096:
            raise ValueError("oversized cursor")
        raw = json.loads(base64.urlsafe_b64decode(query.cursor.encode()))
        if (
            raw["binding"] != binding
            or type(raw["offset"]) is not int
            or raw["offset"] < 0
            or type(raw["total"]) is not int
            or raw["total"] < raw["offset"]
            or type(raw["after"]) not in (str, int)
        ):
            raise ValueError("mismatched cursor")
        return raw
    except (ValueError, KeyError, TypeError) as error:
        raise InspectionError(
            "results.cursor.invalid", "restart this query without its cursor"
        ) from error


def _token(binding: Any, offset: int, total: int, after: str | int) -> str:
    return base64.urlsafe_b64encode(
        encode(
            {"binding": binding, "offset": offset, "total": total, "after": after}
        ).encode()
    ).decode()


def _metadata(db: sqlite3.Connection, query: Query) -> dict[str, Any]:
    if query.latest:
        where, values = _where(
            Query(kind=query.kind, projection=query.projection), listing=True
        )
        row = db.execute(
            "SELECT metadata FROM results WHERE 1=1"
            + where
            + " ORDER BY sequence DESC LIMIT 1",
            values,
        ).fetchone()
    else:
        row = db.execute(
            "SELECT metadata FROM results WHERE id=?", (query.result_id,)
        ).fetchone()
    if row is None:
        raise InspectionError(
            "results.id.missing",
            "result may have been superseded or cleared; list cached results",
        )
    return _read_metadata(row[0])


def _read_metadata(raw: str) -> dict[str, Any]:
    value = decode_node(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != "research-log-retained-result/1"
    ):
        raise InspectionError(
            "results.schema.unsupported", "unsupported retained result"
        )
    required = {*LIST_FIELDS, "summary", "counts", "code_groups"}
    if (
        not required <= value.keys()
        or not isinstance(value["counts"], dict)
        or not all(
            type(count) is int and count >= 0 for count in value["counts"].values()
        )
        or type(value["code_groups"]) is not int
        or value["code_groups"] < 0
    ):
        raise InspectionError("results.store.malformed", "invalid retained metadata")
    return value


def _page(
    db: sqlite3.Connection,
    query: Query,
    base: dict[str, Any],
    selection: _Selection,
    binding: Any,
) -> dict[str, Any]:
    sql, parameters = selection.sql, selection.parameters
    key, descending, total = selection.key, selection.descending, selection.total
    cursor = _cursor(query, binding)
    offset = cursor["offset"] if cursor else 0
    if total is None:
        total = (
            cursor["total"]
            if cursor
            else db.execute(
                "SELECT count(*) FROM (" + sql + ")", parameters
            ).fetchone()[0]
        )
    after = cursor["after"] if cursor else None
    if cursor:
        sql += f" AND {key} {'<' if descending else '>'} ?"
        parameters = [*parameters, after]
    sql += f" ORDER BY {key}" + (" DESC" if descending else "")
    rows = db.execute(sql + " LIMIT ?", [*parameters, query.limit])
    result = {**base, "total": total, "returned": 0, "items": [], "next_cursor": None}
    for row in rows:
        item = (
            _read_metadata(row[0])
            if query.action == "list"
            else unpack_view(decode_node(row[0]))
        )
        if query.action == "list":
            item = {key: item[key] for key in LIST_FIELDS}

        candidate = {**result, "items": [*result["items"], item]}
        # Leave room for a cursor and its shell command in either format.
        if not _fits_page(candidate):
            if not result["items"]:
                raise InspectionError(
                    "results.view.too_large",
                    "select a narrower entity or use explicit export",
                )
            break
        result["items"].append(item)
        after = row[1]
    result["returned"] = len(result["items"])
    if offset + result["returned"] < total:
        if after is None or not result["items"]:
            raise InspectionError("results.store.malformed", "missing page content")
        result["next_cursor"] = _token(
            binding, offset + result["returned"], total, after
        )
    return result


def _listing(
    db: sqlite3.Connection, query: Query, base: dict[str, Any]
) -> dict[str, Any]:
    generation = db.execute("SELECT generation FROM state").fetchone()[0]
    where, values = _where(query, listing=True)
    binding = ["list", generation, where, values]
    return _page(
        db,
        query,
        base,
        _Selection(
            "SELECT metadata, sequence FROM results WHERE 1=1" + where,
            values,
            "sequence",
            descending=True,
        ),
        binding,
    )


def _entities(
    db: sqlite3.Connection, query: Query, base: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any]:
    where, values = _where(query, listing=False)
    kind = query.view if query.action == "show" else query.action + "s"
    values = [base["result_id"], kind, *values]
    sql = "SELECT payload, id FROM entities e WHERE result=? AND kind=?"
    if where:
        sql += (
            " AND EXISTS (SELECT 1 FROM links l WHERE l.result=e.result "
            "AND l.kind=e.kind AND l.id=e.id" + where + ")"
        )
    if query.entity is not None:
        sql += " AND id=?"
        values.append(query.entity)
    total = None
    if not where and query.entity is None:
        total = (
            metadata["code_groups"]
            if kind == "codes"
            else metadata["counts"].get(kind, 0)
        )
    result = _page(
        db,
        query,
        base,
        _Selection(sql, values, "id", total=total),
        [base["result_id"], sql, values],
    )
    if query.entity is not None and result["total"] == 0:
        raise InspectionError(
            "results.entity.missing", f"unknown {kind}: {query.entity}"
        )
    return result


def _pieces(
    db: sqlite3.Connection, query: Query, base: dict[str, Any]
) -> dict[str, Any]:
    if query.entity is None:
        raise InspectionError("results.selection.invalid", "a reference is required")
    manifest = collection_metadata(db, base["result_id"], query.entity)
    if manifest["kind"] != query.action:
        raise InspectionError(
            "results.selection.invalid", "reference kind does not match command"
        )
    binding = [base["result_id"], query.action, query.entity]
    cursor = _cursor(query, binding)
    start = cursor["offset"] if cursor else 0
    total = manifest["piece_count"]
    if start > total:
        raise InspectionError(
            "results.cursor.invalid", "cursor is outside this collection"
        )
    result = {**base, "total": total, "returned": 0, "items": [], "next_cursor": None}
    rows = read_pieces(db, base["result_id"], manifest, start, query.limit)
    for node in rows:
        item = unpack_view(node)
        if not _fits_page({**result, "items": [*result["items"], item]}):
            if not result["items"]:
                raise InspectionError("results.view.too_large", "use explicit export")
            break
        result["items"].append(item)
    result["returned"] = len(result["items"])
    end = start + result["returned"]
    if end < total:
        result["next_cursor"] = _token(binding, end, total, end - 1)
    return result


def inspect_result(log_root: Path, query: Query) -> dict[str, Any]:
    """Retrieve bounded cached data only, including incomplete observations."""
    if query.code and query.view not in {"findings", "chains"}:
        raise InspectionError(
            "results.selection.invalid", "--code selects findings or chains"
        )
    if query.latest and query.kind is None:
        raise InspectionError("results.selection.invalid", "--latest requires --kind")
    if not 1 <= query.limit <= 100:
        raise InspectionError(
            "results.limit.invalid", "limit must be between 1 and 100"
        )
    base: dict[str, Any] = {"schema": VIEW_SCHEMA, "log": str(log_root)}
    with connection(log_root) as db:
        if query.action == "list":
            return _listing(db, query, base)
        metadata = _metadata(db, query)
        base.update(
            result_id=metadata["result_id"], status=metadata["status"], historical=True
        )
        if query.action == "export":
            return _export(db, metadata)
        if query.action in {"collection", "value"}:
            return _pieces(db, query, base)
        if query.action == "show" and query.view == "summary":
            fields = (
                "finished_at",
                "kind",
                "reason",
                "projection_id",
                "origin_projection_id",
                "evaluated_scope",
                "evaluated_checks",
                "returned_scope",
                "finding_count",
                "counts",
                "codes",
                "code_groups",
            )
            return {**base, "metadata": {key: metadata.get(key) for key in fields}}
        return _entities(db, query, base, metadata)


def _export(db: sqlite3.Connection, metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {**metadata, "entities": {}}
    rows = db.execute(
        "SELECT kind, id, payload FROM entities WHERE result=? ORDER BY kind, id",
        (metadata["result_id"],),
    )
    for kind, identity, raw in rows:
        result["entities"].setdefault(kind, {})[identity] = expand(
            db, metadata["result_id"], decode_node(raw)
        )
    return result


def _fits_page(candidate: dict[str, Any]) -> bool:
    from .inspection_views import render_view

    if len(encode(candidate).encode()) > MAX_VIEW_BYTES - 4096:
        return False
    try:
        return len(render_view(candidate).encode()) <= MAX_VIEW_BYTES - 4096
    except InspectionError:
        return False
