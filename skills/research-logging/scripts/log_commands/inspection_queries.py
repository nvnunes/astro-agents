"""Bounded SQL views over normalized validation result rows."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from research_log_result_store import ResultStoreError, result_snapshot
from validation.result_storage import (
    export_validation_result,
    validate_validation_result,
)

VIEW_SCHEMA = "research-log-result-view/1"
MAX_VIEW_BYTES = 16 * 1024
VIEWS = ("summary", "codes", "findings", "batches", "chains", "commands", "artifacts")


class InspectionError(ResultStoreError):
    pass


@dataclass(frozen=True)
class Query:
    action: str = "show"
    result_id: str | None = None
    latest: bool = False
    kind: str | None = None
    view: str = "summary"
    entry: str | None = None
    chain: str | None = None
    batch: str | None = None
    code: str | None = None
    entity: str | None = None
    limit: int = 20
    cursor: str | None = None


def _enc(v: object) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(v, sort_keys=True, separators=(",", ":")).encode()
    ).decode()


def _last(cursor: str | None, binding: object) -> str | None:
    if not cursor:
        return None
    try:
        v = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception as e:
        raise InspectionError(
            "results.cursor.invalid", "restart this query without its cursor"
        ) from e
    if (
        not isinstance(v, dict)
        or v.get("binding") != binding
        or not isinstance(v.get("last"), str)
    ):
        raise InspectionError(
            "results.cursor.invalid", "cursor does not match this current result view"
        )
    return v["last"]


def _bound(v: dict[str, object]) -> dict[str, object]:
    if len(json.dumps(v, ensure_ascii=False).encode()) > MAX_VIEW_BYTES:
        raise InspectionError(
            "results.view.too_large", "select a narrower entity or use explicit export"
        )
    return v


def _meta(db: Any, q: Query) -> dict[str, object]:
    row = (
        db.execute(
            "SELECT * FROM validation_results WHERE kind=? "
            "ORDER BY generation DESC LIMIT 1",
            (q.kind,),
        ).fetchone()
        if q.latest and q.kind
        else db.execute(
            "SELECT * FROM validation_results WHERE result_id=?", (q.result_id,)
        ).fetchone()
    )
    if row is None:
        raise InspectionError(
            "results.id.missing",
            "result may have been superseded or cleared; list cached results",
        )
    return dict(row)


def _total(db: Any, table: str, where: list[str], args: Sequence[object]) -> int:
    """Return the cardinality for the exact bounded query, not its page."""
    row = db.execute(
        f"SELECT COUNT(*) AS count FROM {table}"
        + (" WHERE " + " AND ".join(where) if where else ""),
        args,
    ).fetchone()
    assert row is not None
    return int(row["count"])


def _generation(db: Any) -> int:
    row = db.execute(
        "SELECT generation FROM store_state WHERE domain='validation'"
    ).fetchone()
    if row is None:
        raise InspectionError(
            "results.store.missing", "validation result state is absent"
        )
    return int(row["generation"])


def _batch_row(db: Any, row: dict[str, object]) -> dict[str, object]:
    """Add the bounded public anchors for one already-selected repair batch."""
    batch_id = str(row["batch_id"])
    try:
        anchors = [
            json.loads(anchor["anchor_json"])
            for anchor in db.execute(
                "SELECT anchor_json FROM validation_batch_anchors "
                "WHERE result_id=? AND batch_id=? ORDER BY position",
                (row["result_id"], batch_id),
            )
        ]
    except (TypeError, json.JSONDecodeError) as error:
        raise InspectionError(
            "results.store.malformed", f"invalid repair-batch anchor: {error}"
        ) from error
    return {**row, "anchors": anchors}


def _list_results(root: Path, db: Any, q: Query) -> dict[str, object]:
    where: list[str] = []
    args: list[object] = []
    if q.kind:
        where.append("kind=?")
        args.append(q.kind)
    total = _total(db, "validation_results", where, args)
    binding = {
        "action": "list",
        "generation": _generation(db),
        "view": "list",
        "filters": {"kind": q.kind},
        "total": total,
    }
    last = _last(q.cursor, binding)
    if last:
        where.append("result_id<?")
        args.append(last)
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT result_id,kind,entry,finished_at,status FROM validation_results"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY result_id DESC LIMIT ?",
            [*args, q.limit + 1],
        )
    ]
    items = rows[: q.limit]
    return _bound(
        {
            "schema": VIEW_SCHEMA,
            "log": str(root),
            "total": total,
            "returned": len(items),
            "items": items,
            "next_cursor": _enc({"binding": binding, "last": items[-1]["result_id"]})
            if len(rows) > q.limit
            else None,
        }
    )


def _base_view(root: Path, metadata: dict[str, object]) -> dict[str, object]:
    return {
        "schema": VIEW_SCHEMA,
        "log": str(root),
        "result_id": str(metadata["result_id"]),
        "status": metadata["status"],
        "historical": True,
    }


def _summary_view(root: Path, metadata: dict[str, object]) -> dict[str, object]:
    fields = (
        "kind",
        "entry",
        "finished_at",
        "status",
        "summary",
        "result_date",
        "rules_version",
        "validation_id",
        "record_identity",
    )
    return _bound(
        {
            **_base_view(root, metadata),
            "metadata": {field: metadata[field] for field in fields},
        }
    )


def _code_filters(q: Query, result_id: str) -> tuple[list[str], list[object]]:
    where = ["result_id=?"]
    args: list[object] = [result_id]
    if q.entry:
        where.append(
            "group_id IN (SELECT group_id FROM validation_groups "
            "WHERE result_id=? AND entry=?)"
        )
        args.extend((result_id, q.entry))
    if q.chain:
        where.append("group_id=?")
        args.append(q.chain)
    if q.code:
        where.append("code=?")
        args.append(q.code)
    if q.batch:
        where.append(
            "EXISTS (SELECT 1 FROM validation_batch_findings bf "
            "WHERE bf.result_id=validation_findings.result_id AND bf.batch_id=? "
            "AND bf.finding_id=validation_findings.finding_id)"
        )
        args.append(q.batch)
    return where, args


def _codes_view(
    root: Path, db: Any, q: Query, metadata: dict[str, object]
) -> dict[str, object]:
    result_id = str(metadata["result_id"])
    where, args = _code_filters(q, result_id)
    row = db.execute(
        "SELECT COUNT(*) AS count FROM (SELECT code FROM validation_findings WHERE "
        + " AND ".join(where)
        + " GROUP BY code)",
        args,
    ).fetchone()
    assert row is not None
    total = int(row["count"])
    binding = {
        "result_id": result_id,
        "generation": _generation(db),
        "view": "codes",
        "filters": {
            "entry": q.entry,
            "chain": q.chain,
            "batch": q.batch,
            "code": q.code,
        },
        "total": total,
    }
    last = _last(q.cursor, binding)
    if last:
        where.append("code>?")
        args.append(last)
    rows = [
        dict(item)
        for item in db.execute(
            "SELECT code,count(*) findings FROM validation_findings WHERE "
            + " AND ".join(where)
            + " GROUP BY code ORDER BY code LIMIT ?",
            [*args, q.limit + 1],
        )
    ]
    items = rows[: q.limit]
    return _bound(
        {
            **_base_view(root, metadata),
            "total": total,
            "returned": len(items),
            "items": items,
            "next_cursor": _enc({"binding": binding, "last": items[-1]["code"]})
            if len(rows) > q.limit
            else None,
        }
    )


def _entry_filter(
    q: Query, view: str, result_id: str
) -> tuple[list[str], list[object]]:
    if not q.entry:
        return [], []
    if view == "commands":
        return ["entry=?"], [q.entry]
    if view in {"findings", "chains"}:
        return [
            "group_id IN (SELECT group_id FROM validation_groups "
            "WHERE result_id=? AND entry=?)"
        ], [result_id, q.entry]
    if view == "artifacts":
        return [
            "EXISTS (SELECT 1 FROM validation_group_artifacts ga "
            "JOIN validation_groups g ON g.result_id=ga.result_id "
            "AND g.group_id=ga.group_id WHERE ga.result_id="
            "validation_artifacts.result_id AND ga.artifact="
            "validation_artifacts.artifact_id AND g.entry=?)"
        ], [q.entry]
    if view == "batches":
        return [
            "EXISTS (SELECT 1 FROM validation_batch_entries e "
            "WHERE e.result_id=validation_batches.result_id "
            "AND e.batch_id=validation_batches.batch_id AND e.entry=?)"
        ], [q.entry]
    return [], []


def _chain_filter(q: Query, view: str) -> tuple[list[str], list[object]]:
    if not q.chain:
        return [], []
    if view in {"findings", "chains", "commands"}:
        return ["group_id=?"], [q.chain]
    if view == "artifacts":
        return [
            "EXISTS (SELECT 1 FROM validation_group_artifacts ga "
            "WHERE ga.result_id=validation_artifacts.result_id "
            "AND ga.artifact=validation_artifacts.artifact_id AND ga.group_id=?)"
        ], [q.chain]
    if view == "batches":
        return [
            "EXISTS (SELECT 1 FROM validation_batch_groups g "
            "WHERE g.result_id=validation_batches.result_id "
            "AND g.batch_id=validation_batches.batch_id AND g.group_id=?)"
        ], [q.chain]
    return [], []


def _entity_code_filter(
    q: Query, view: str, table: str, key: str
) -> tuple[list[str], list[object]]:
    if not q.code:
        return [], []
    clause = {
        "findings": "code=?",
        "batches": (
            "EXISTS (SELECT 1 FROM validation_batch_findings bf "
            "JOIN validation_findings f ON f.result_id=bf.result_id "
            "AND f.finding_id=bf.finding_id WHERE bf.result_id="
            "validation_batches.result_id AND bf.batch_id="
            "validation_batches.batch_id AND f.code=?)"
        ),
        "chains": (
            "EXISTS (SELECT 1 FROM validation_batch_groups bg "
            "JOIN validation_batch_findings bf ON bf.result_id=bg.result_id "
            "AND bf.batch_id=bg.batch_id JOIN validation_findings f "
            "ON f.result_id=bf.result_id AND f.finding_id=bf.finding_id "
            f"WHERE bg.result_id={table}.result_id AND bg.group_id={table}.{key} "
            "AND f.code=?)"
        ),
        "commands": (
            "EXISTS (SELECT 1 FROM validation_batch_command_links l "
            f"WHERE l.result_id={table}.result_id "
            f"AND l.command_id={table}.{key} AND l.code=?)"
        ),
        "artifacts": (
            "EXISTS (SELECT 1 FROM validation_batch_command_links l "
            "JOIN validation_command_relationships r ON r.result_id=l.result_id "
            "AND r.command_id=l.command_id "
            f"WHERE l.result_id={table}.result_id AND r.path={table}.{key} "
            "AND l.code=?)"
        ),
    }.get(view)
    return ([clause], [q.code]) if clause else ([], [])


def _batch_filter(
    q: Query, view: str, table: str, key: str
) -> tuple[list[str], list[object]]:
    if not q.batch:
        return [], []
    clause = {
        "batches": "batch_id=?",
        "findings": (
            "EXISTS (SELECT 1 FROM validation_batch_findings bf "
            f"WHERE bf.result_id={table}.result_id AND bf.batch_id=? "
            f"AND bf.finding_id={table}.{key})"
        ),
        "chains": (
            "EXISTS (SELECT 1 FROM validation_batch_groups bg "
            f"WHERE bg.result_id={table}.result_id AND bg.batch_id=? "
            f"AND bg.group_id={table}.{key})"
        ),
        "commands": (
            "EXISTS (SELECT 1 FROM validation_batch_command_links l "
            f"WHERE l.result_id={table}.result_id AND l.batch_id=? "
            f"AND l.command_id={table}.{key})"
        ),
        "artifacts": (
            "EXISTS (SELECT 1 FROM validation_batch_command_links l "
            "JOIN validation_command_relationships r ON r.result_id=l.result_id "
            "AND r.command_id=l.command_id "
            f"WHERE l.result_id={table}.result_id AND l.batch_id=? "
            f"AND r.path={table}.{key})"
        ),
    }.get(view)
    return ([clause], [q.batch]) if clause else ([], [])


def _entity_filters(
    q: Query, view: str, table: str, key: str, result_id: str
) -> tuple[list[str], list[object]]:
    where = ["result_id=?"]
    args: list[object] = [result_id]
    if q.action in {"finding", "batch", "command", "artifact"}:
        where.append(key + "=?")
        args.append(q.entity or "")
    for clauses, values in (
        _entry_filter(q, view, result_id),
        _chain_filter(q, view),
        _entity_code_filter(q, view, table, key),
        _batch_filter(q, view, table, key),
    ):
        where.extend(clauses)
        args.extend(values)
    return where, args


def _entity_rows(
    db: Any, view: str, items: list[dict[str, object]]
) -> list[dict[str, object]]:
    if view == "batches":
        return [_batch_row(db, item) for item in items]
    if view == "artifacts":
        return [{**item, "path": item["artifact_id"]} for item in items]
    return items


def _entity_view(
    root: Path, db: Any, q: Query, metadata: dict[str, object]
) -> dict[str, object]:
    view = (
        q.action if q.action in {"finding", "batch", "command", "artifact"} else q.view
    )
    table, key = {
        "findings": ("validation_findings", "finding_id"),
        "batches": ("validation_batches", "batch_id"),
        "chains": ("validation_groups", "group_id"),
        "commands": ("validation_commands", "command_id"),
        "artifacts": ("validation_artifacts", "artifact_id"),
    }[view]
    result_id = str(metadata["result_id"])
    where, args = _entity_filters(q, view, table, key, result_id)
    filters = {"entry": q.entry, "chain": q.chain, "batch": q.batch, "code": q.code}
    total = _total(db, table, where, args)
    binding = {
        "result_id": result_id,
        "generation": _generation(db),
        "view": view,
        "filters": filters,
        "total": total,
    }
    last = _last(q.cursor, binding)
    if last:
        where.append(key + ">?")
        args.append(last)
    rows = [
        dict(row)
        for row in db.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY {key} LIMIT ?",
            [*args, q.limit + 1],
        )
    ]
    items = _entity_rows(db, view, rows[: q.limit])
    if q.action in {"finding", "batch", "command", "artifact"} and not items:
        raise InspectionError(
            "results.entity.missing", f"unknown {q.action}: {q.entity}"
        )
    return _bound(
        {
            **_base_view(root, metadata),
            "total": total,
            "returned": len(items),
            "items": items,
            "next_cursor": _enc({"binding": binding, "last": items[-1][key]})
            if len(rows) > q.limit
            else None,
        }
    )


def inspect_result(root: Path, q: Query) -> dict[str, object]:
    if (
        q.action
        not in {
            "list",
            "show",
            "export",
            "finding",
            "batch",
            "command",
            "artifact",
        }
        or q.view not in VIEWS
    ):
        raise InspectionError("results.selection.invalid", "unsupported result view")
    if not 1 <= q.limit <= 100:
        raise InspectionError(
            "results.limit.invalid", "limit must be between 1 and 100"
        )
    try:
        with result_snapshot(root) as db:
            if q.action == "list":
                return _list_results(root, db, q)
            metadata = _meta(db, q)
            result_id = str(metadata["result_id"])
            validate_validation_result(root, result_id)
            if q.action == "export":
                return export_validation_result(root, result_id)
            if q.action == "show" and q.view == "summary":
                return _summary_view(root, metadata)
            if q.view == "codes":
                return _codes_view(root, db, q, metadata)
            return _entity_view(root, db, q, metadata)
    except ResultStoreError as error:
        raise InspectionError(error.code, str(error)) from error
