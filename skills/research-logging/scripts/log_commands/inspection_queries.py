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
    resolve_validation_result,
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
    resolve_validation_result(db, str(row["result_id"]))
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


def _codes_view(
    root: Path, db: Any, q: Query, metadata: dict[str, object]
) -> dict[str, object]:
    result_pk = _stored_integer(metadata["result_pk"], "result key")
    result_id = str(metadata["result_id"])
    where = ["f.result_pk=?"]
    args: list[object] = [result_pk]
    if q.entry:
        where.append("g.entry=?")
        args.append(q.entry)
    if q.chain:
        where.append("g.group_id=?")
        args.append(q.chain)
    if q.code:
        where.append("k.code=?")
        args.append(q.code)
    if q.batch:
        where.append(
            "EXISTS (SELECT 1 FROM validation_batches b "
            "JOIN validation_batch_findings bf USING (result_pk,batch_pk) "
            "WHERE b.result_pk=f.result_pk AND b.batch_id=? "
            "AND bf.check_pk=f.check_pk)"
        )
        args.append(q.batch)
    source = (
        "validation_findings f "
        "JOIN validation_checks c USING (result_pk,check_pk) "
        "JOIN validation_codes k USING (result_pk,code_pk) "
        "JOIN validation_groups g ON g.result_pk=f.result_pk "
        "AND g.group_pk=f.group_pk"
    )
    predicate = " AND ".join(where)
    total = int(
        db.execute(
            "SELECT COUNT(*) FROM (SELECT k.code FROM "
            + source
            + " WHERE "
            + predicate
            + " GROUP BY k.code)",
            args,
        ).fetchone()[0]
    )
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
        where.append("k.code>?")
        args.append(last)
    rows = [
        dict(item)
        for item in db.execute(
            "SELECT k.code,count(*) findings FROM "
            + source
            + " WHERE "
            + " AND ".join(where)
            + " GROUP BY k.code ORDER BY k.code LIMIT ?",
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


def _entity_source(view: str) -> tuple[str, str, str]:
    return {
        "findings": (
            "e.result_pk AS _result_pk,e.check_pk AS _check_pk,"
            "r.result_id,c.check_id AS finding_id,g.group_id,e.position,c.scope,"
            "c.status,k.code,c.subject AS projection_subject,c.rule,c.observed_json,"
            "e.admission_effect,e.display_entry,e.display_subject",
            "validation_findings e "
            "JOIN validation_results r USING (result_pk) "
            "JOIN validation_checks c USING (result_pk,check_pk) "
            "LEFT JOIN validation_codes k USING (result_pk,code_pk) "
            "JOIN validation_groups g ON g.result_pk=e.result_pk "
            "AND g.group_pk=e.group_pk",
            "finding_id",
        ),
        "batches": (
            "e.result_pk AS _result_pk,e.batch_pk AS _batch_pk,r.result_id,"
            "e.batch_id,e.batch_type,e.grouping_reason,e.scope,"
            "e.primary_finding_count,c.check_id AS starting_finding_id,e.position",
            "validation_batches e "
            "JOIN validation_results r USING (result_pk) "
            "JOIN validation_checks c ON c.result_pk=e.result_pk "
            "AND c.check_pk=e.starting_check_pk",
            "batch_id",
        ),
        "chains": (
            "e.result_pk AS _result_pk,e.group_pk AS _group_pk,r.result_id,"
            "e.group_id,e.group_kind,e.entry,e.reason,e.position",
            "validation_groups e JOIN validation_results r USING (result_pk)",
            "group_id",
        ),
        "commands": (
            "e.result_pk AS _result_pk,e.command_pk AS _command_pk,r.result_id,"
            "e.command_id,e.entry,e.document,e.fence,e.ordinal,e.script,"
            "g.group_id,e.position",
            "validation_commands e "
            "JOIN validation_results r USING (result_pk) "
            "JOIN validation_groups g ON g.result_pk=e.result_pk "
            "AND g.group_pk=e.group_pk",
            "command_id",
        ),
        "artifacts": (
            "e.result_pk AS _result_pk,e.artifact_pk AS _artifact_pk,"
            "r.result_id,e.artifact_id",
            "validation_artifacts e JOIN validation_results r USING (result_pk)",
            "artifact_id",
        ),
    }[view]


def _entity_filters(
    q: Query, view: str, result_pk: int, key_column: str
) -> tuple[list[str], list[object]]:
    where = ["e.result_pk=?"]
    args: list[object] = [result_pk]
    if q.action in {"finding", "batch", "command", "artifact"}:
        where.append(key_column + "=?")
        args.append(q.entity or "")
    if q.entry:
        clause = {
            "findings": "g.entry=?",
            "chains": "e.entry=?",
            "commands": "e.entry=?",
            "artifacts": (
                "EXISTS (SELECT 1 FROM validation_group_artifacts ga "
                "JOIN validation_groups eg USING (result_pk,group_pk) "
                "WHERE ga.result_pk=e.result_pk "
                "AND ga.artifact_pk=e.artifact_pk AND eg.entry=?)"
            ),
            "batches": (
                "EXISTS (SELECT 1 FROM validation_batch_entries be "
                "WHERE be.result_pk=e.result_pk AND be.batch_pk=e.batch_pk "
                "AND be.entry=?)"
            ),
        }[view]
        where.append(clause)
        args.append(q.entry)
    if q.chain:
        clause = {
            "findings": "g.group_id=?",
            "chains": "e.group_id=?",
            "commands": "g.group_id=?",
            "artifacts": (
                "EXISTS (SELECT 1 FROM validation_group_artifacts ga "
                "JOIN validation_groups cg USING (result_pk,group_pk) "
                "WHERE ga.result_pk=e.result_pk "
                "AND ga.artifact_pk=e.artifact_pk AND cg.group_id=?)"
            ),
            "batches": (
                "EXISTS (SELECT 1 FROM validation_batch_groups bg "
                "JOIN validation_groups cg USING (result_pk,group_pk) "
                "WHERE bg.result_pk=e.result_pk AND bg.batch_pk=e.batch_pk "
                "AND cg.group_id=?)"
            ),
        }[view]
        where.append(clause)
        args.append(q.chain)
    if q.code:
        clause = {
            "findings": "k.code=?",
            "batches": (
                "EXISTS (SELECT 1 FROM validation_batch_findings bf "
                "JOIN validation_checks bc USING (result_pk,check_pk) "
                "JOIN validation_codes bk USING (result_pk,code_pk) "
                "WHERE bf.result_pk=e.result_pk AND bf.batch_pk=e.batch_pk "
                "AND bk.code=?)"
            ),
            "chains": (
                "EXISTS (SELECT 1 FROM validation_batch_groups bg "
                "JOIN validation_batch_findings bf USING (result_pk,batch_pk) "
                "JOIN validation_checks bc USING (result_pk,check_pk) "
                "JOIN validation_codes bk USING (result_pk,code_pk) "
                "WHERE bg.result_pk=e.result_pk AND bg.group_pk=e.group_pk "
                "AND bk.code=?)"
            ),
            "commands": (
                "EXISTS (SELECT 1 FROM validation_batch_command_links l "
                "JOIN validation_codes lk USING (result_pk,code_pk) "
                "WHERE l.result_pk=e.result_pk AND l.command_pk=e.command_pk "
                "AND lk.code=?)"
            ),
            "artifacts": (
                "EXISTS (SELECT 1 FROM validation_batch_command_links l "
                "JOIN validation_codes lk USING (result_pk,code_pk) "
                "JOIN validation_command_relationships cr "
                "ON cr.result_pk=l.result_pk AND cr.command_pk=l.command_pk "
                "WHERE l.result_pk=e.result_pk "
                "AND cr.path_artifact_pk=e.artifact_pk AND lk.code=?)"
            ),
        }[view]
        where.append(clause)
        args.append(q.code)
    if q.batch:
        clause = {
            "batches": "e.batch_id=?",
            "findings": (
                "EXISTS (SELECT 1 FROM validation_batches b "
                "JOIN validation_batch_findings bf USING (result_pk,batch_pk) "
                "WHERE b.result_pk=e.result_pk AND b.batch_id=? "
                "AND bf.check_pk=e.check_pk)"
            ),
            "chains": (
                "EXISTS (SELECT 1 FROM validation_batches b "
                "JOIN validation_batch_groups bg USING (result_pk,batch_pk) "
                "WHERE b.result_pk=e.result_pk AND b.batch_id=? "
                "AND bg.group_pk=e.group_pk)"
            ),
            "commands": (
                "EXISTS (SELECT 1 FROM validation_batches b "
                "JOIN validation_batch_command_links l USING (result_pk,batch_pk) "
                "WHERE b.result_pk=e.result_pk AND b.batch_id=? "
                "AND l.command_pk=e.command_pk)"
            ),
            "artifacts": (
                "EXISTS (SELECT 1 FROM validation_batches b "
                "JOIN validation_batch_command_links l USING (result_pk,batch_pk) "
                "JOIN validation_command_relationships cr "
                "ON cr.result_pk=l.result_pk AND cr.command_pk=l.command_pk "
                "WHERE b.result_pk=e.result_pk AND b.batch_id=? "
                "AND cr.path_artifact_pk=e.artifact_pk)"
            ),
        }[view]
        where.append(clause)
        args.append(q.batch)
    return where, args


def _stored_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InspectionError("results.store.malformed", f"invalid stored {name}")
    return value


def _batch_member_rows(
    db: Any, result_pk: int, batch_pk: int
) -> dict[str, list[Any]]:
    rows = {
        name: list(
            db.execute(
                f"SELECT position,{column} AS member FROM {table} "
                "WHERE result_pk=? AND batch_pk=? ORDER BY position",
                (result_pk, batch_pk),
            )
        )
        for name, table, column in (
            ("entries", "validation_batch_entries", "entry"),
            ("findings", "validation_batch_findings", "check_pk"),
            ("groups", "validation_batch_groups", "group_pk"),
            (
                "related batches",
                "validation_batch_related_batches",
                "related_batch_pk",
            ),
        )
    }
    for name, members in rows.items():
        _require_dense_positions(members, f"repair-batch {name}")
    return rows


def _validated_batch_members(
    member_rows: dict[str, list[Any]], anchors: list[object]
) -> dict[str, list[object]]:
    semantic = {
        name: [item["member"] for item in members]
        for name, members in member_rows.items()
    }
    for name, members in semantic.items():
        if len(members) != len(set(members)):
            raise InspectionError(
                "results.store.malformed",
                f"selected repair batch has duplicate {name}",
            )
    for entry in semantic["entries"]:
        _require_string(entry, "repair-batch entry")
    for name in ("findings", "groups", "related batches"):
        for member in semantic[name]:
            _stored_integer(member, f"repair-batch {name} member")
    anchor_ids = [
        json.dumps(anchor, sort_keys=True, separators=(",", ":"))
        for anchor in anchors
    ]
    if len(anchor_ids) != len(set(anchor_ids)):
        raise InspectionError(
            "results.store.malformed", "selected repair batch has duplicate anchors"
        )
    return semantic


def _validate_local_batch_members(
    db: Any,
    result_pk: int,
    batch_pk: int,
    member_rows: dict[str, list[Any]],
) -> None:
    queries = {
        "findings": (
            "SELECT count(*) FROM validation_batch_findings AS m "
            "JOIN validation_findings AS f USING (result_pk,check_pk) "
            "WHERE m.result_pk=? AND m.batch_pk=?"
        ),
        "groups": (
            "SELECT count(*) FROM validation_batch_groups AS m "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE m.result_pk=? AND m.batch_pk=?"
        ),
        "related batches": (
            "SELECT count(*) FROM validation_batch_related_batches AS m "
            "JOIN validation_batches AS b ON b.result_pk=m.result_pk "
            "AND b.batch_pk=m.related_batch_pk "
            "WHERE m.result_pk=? AND m.batch_pk=?"
        ),
    }
    for name, query in queries.items():
        count = db.execute(query, (result_pk, batch_pk)).fetchone()[0]
        if count != len(member_rows[name]):
            raise InspectionError(
                "results.store.malformed",
                f"selected repair batch {name} member is absent",
            )


def _batch_row(db: Any, row: dict[str, object]) -> dict[str, object]:
    batch_pk = _stored_integer(row.pop("_batch_pk"), "batch key")
    result_pk = _stored_integer(row.pop("_result_pk"), "result key")
    try:
        anchor_rows = list(
            db.execute(
                "SELECT position,anchor_json FROM validation_batch_anchors "
                "WHERE result_pk=? AND batch_pk=? ORDER BY position",
                (result_pk, batch_pk),
            )
        )
        _require_dense_positions(anchor_rows, "repair-batch anchors")
        anchors = [json.loads(anchor["anchor_json"]) for anchor in anchor_rows]
        member_rows = _batch_member_rows(db, result_pk, batch_pk)
    except (TypeError, json.JSONDecodeError) as error:
        raise InspectionError(
            "results.store.malformed", f"invalid repair-batch anchor: {error}"
        ) from error
    semantic_members = _validated_batch_members(member_rows, anchors)

    findings = member_rows["findings"]
    if len(findings) != row["primary_finding_count"]:
        raise InspectionError(
            "results.store.malformed", "selected repair batch finding count disagrees"
        )
    _validate_local_batch_members(db, result_pk, batch_pk, member_rows)
    starting = db.execute(
        "SELECT c.check_pk FROM validation_checks AS c "
        "JOIN validation_findings AS f USING (result_pk,check_pk) "
        "WHERE c.result_pk=? AND c.check_id=?",
        (result_pk, row["starting_finding_id"]),
    ).fetchone()
    if starting is None or starting[0] not in {item["member"] for item in findings}:
        raise InspectionError(
            "results.store.malformed", "selected repair batch start is not a member"
        )
    related = semantic_members["related batches"]
    if batch_pk in related:
        raise InspectionError(
            "results.store.malformed", "selected repair batch relation is invalid"
        )
    return {**row, "anchors": anchors}


def _require_dense_positions(rows: list[Any], name: str) -> None:
    if [row["position"] for row in rows] != list(range(len(rows))):
        raise InspectionError(
            "results.store.malformed", f"invalid selected {name} positions"
        )


def _require_string(value: object, name: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value:
        raise InspectionError("results.store.malformed", f"invalid selected {name}")


def _validate_selected_finding(db: Any, item: dict[str, object]) -> None:
    result_pk = _stored_integer(item["_result_pk"], "result key")
    check_pk = _stored_integer(item["_check_pk"], "check key")
    for name in (
        "finding_id",
        "group_id",
        "scope",
        "status",
        "projection_subject",
        "admission_effect",
        "display_entry",
        "display_subject",
    ):
        _require_string(item[name], f"finding {name}")
    if item["status"] not in {"fail", "unavailable"}:
        raise InspectionError(
            "results.store.malformed", "selected finding has no failed check"
        )
    for name in ("code", "rule"):
        _require_string(item[name], f"finding {name}")
    if item["scope"] not in {"conformance", "evidence", "provenance", "orphan"}:
        raise InspectionError(
            "results.store.malformed", "selected finding has invalid scope"
        )
    try:
        json.loads(str(item["observed_json"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise InspectionError(
            "results.store.malformed", f"invalid selected finding JSON: {error}"
        ) from error
    chains = list(
        db.execute(
            "SELECT a.position,g.entry "
            "FROM validation_finding_affected_chains AS a "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE a.result_pk=? AND a.check_pk=? ORDER BY a.position",
            (result_pk, check_pk),
        )
    )
    entries = list(
        db.execute(
            "SELECT position,entry FROM validation_finding_affected_entries "
            "WHERE result_pk=? AND check_pk=? ORDER BY position",
            (result_pk, check_pk),
        )
    )
    _require_dense_positions(chains, "finding affected-chain")
    _require_dense_positions(entries, "finding affected-entry")
    effect = item["admission_effect"]
    if effect in {"none", "log"} and (chains or entries):
        raise InspectionError(
            "results.store.malformed", "selected finding has affected members"
        )
    if effect == "entry" and (chains or len(entries) != 1):
        raise InspectionError(
            "results.store.malformed", "selected entry finding has invalid members"
        )
    if effect == "chain" and (
        len(chains) != 1
        or len(entries) != 1
        or chains[0]["entry"] != entries[0]["entry"]
    ):
        raise InspectionError(
            "results.store.malformed", "selected chain finding has invalid members"
        )
    if effect not in {"none", "log", "entry", "chain"}:
        raise InspectionError(
            "results.store.malformed", "selected finding has invalid effect"
        )


def _validate_selected_scalars(view: str, item: dict[str, object]) -> None:
    string_fields = {
        "batches": ("batch_id", "batch_type", "grouping_reason", "scope"),
        "chains": ("group_id", "group_kind", "entry"),
        "commands": ("command_id", "entry", "document", "script", "group_id"),
        "artifacts": ("artifact_id",),
    }.get(view, ())
    for name in string_fields:
        _require_string(item[name], f"{view} {name}")
    if view == "chains":
        kind, reason = item["group_kind"], item["reason"]
        if kind not in {"chain", "unresolved"}:
            raise InspectionError(
                "results.store.malformed", "invalid selected group kind"
            )
        if (kind == "chain" and reason is not None) or (
            kind == "unresolved" and reason != "finding_scope_unresolved"
        ):
            raise InspectionError(
                "results.store.malformed", "invalid selected group reason"
            )
    for name in {"batches": ("primary_finding_count",)}.get(view, ()):
        _stored_integer(item[name], f"{view} {name}")
    for name in {"commands": ("fence", "ordinal")}.get(view, ()):
        value = item[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InspectionError(
                "results.store.malformed", f"invalid selected {view} {name}"
            )
    position = item.get("position")
    if position is not None and (
        not isinstance(position, int) or isinstance(position, bool) or position < 0
    ):
        raise InspectionError(
            "results.store.malformed", f"invalid selected {view} position"
        )


def _entity_rows(
    db: Any, view: str, items: list[dict[str, object]]
) -> list[dict[str, object]]:
    if view == "batches":
        for item in items:
            _validate_selected_scalars(view, item)
        return [_batch_row(db, item) for item in items]
    if view == "findings":
        for item in items:
            _validate_selected_finding(db, item)
    else:
        for item in items:
            _validate_selected_scalars(view, item)
    for item in items:
        for key in tuple(item):
            if key.startswith("_"):
                item.pop(key)
    if view == "artifacts":
        return [{**item, "path": item["artifact_id"]} for item in items]
    return items


def _entity_view(
    root: Path, db: Any, q: Query, metadata: dict[str, object]
) -> dict[str, object]:
    view = (
        {
            "finding": "findings",
            "batch": "batches",
            "command": "commands",
            "artifact": "artifacts",
        }[q.action]
        if q.action in {"finding", "batch", "command", "artifact"}
        else q.view
    )
    select, source, key = _entity_source(view)
    key_column = {
        "findings": "c.check_id",
        "batches": "e.batch_id",
        "chains": "e.group_id",
        "commands": "e.command_id",
        "artifacts": "e.artifact_id",
    }[view]
    result_pk = _stored_integer(metadata["result_pk"], "result key")
    result_id = str(metadata["result_id"])
    where, args = _entity_filters(q, view, result_pk, key_column)
    filters = {"entry": q.entry, "chain": q.chain, "batch": q.batch, "code": q.code}
    predicate = " AND ".join(where)
    total = int(
        db.execute(
            "SELECT COUNT(*) FROM " + source + " WHERE " + predicate, args
        ).fetchone()[0]
    )
    binding = {
        "result_id": result_id,
        "generation": _generation(db),
        "view": view,
        "filters": filters,
        "total": total,
    }
    last = _last(q.cursor, binding)
    if last:
        where.append(key_column + ">?")
        args.append(last)
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT "
            + select
            + " FROM "
            + source
            + " WHERE "
            + " AND ".join(where)
            + " ORDER BY "
            + key
            + " LIMIT ?",
            [*args, q.limit + 1],
        )
    ]
    page = rows[: q.limit]
    last_value = None if not page else page[-1][key]
    items = _entity_rows(db, view, page)
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
            "next_cursor": _enc({"binding": binding, "last": last_value})
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
            if q.action == "export":
                return export_validation_result(root, result_id, connection=db)
            if q.action == "show" and q.view == "summary":
                return _summary_view(root, metadata)
            if q.view == "codes":
                return _codes_view(root, db, q, metadata)
            return _entity_view(root, db, q, metadata)
    except ResultStoreError as error:
        raise InspectionError(error.code, str(error)) from error
