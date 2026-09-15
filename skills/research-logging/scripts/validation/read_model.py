"""Bounded validation-specific queries over saved canonical snapshots."""

from __future__ import annotations

import base64
import json
import shlex
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, cast

from research_log_result_store import (
    REPLACEABLE_STORE_VERSIONS,
    STORE_VERSION,
    ResultStoreError,
    result_snapshot,
)

from .domain import ValidationSnapshot, plain_json
from .snapshot_report import SnapshotReportError, finding_presentation
from .snapshot_storage import (
    MAX_VALIDATION_RESPONSE_BYTES,
    batch_repair_node_ids,
    load_validation_snapshot,
)

MAX_RESPONSE_BYTES = MAX_VALIDATION_RESPONSE_BYTES
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
_TYPES = ("conformance", "evidence", "provenance", "orphan")
_DETAIL_SECTIONS = {
    "finding": ("repair_keys", "nodes", "relationships", "ambiguities"),
    "batch": ("findings", "repair_keys", "nodes", "relationships", "ambiguities"),
}


class ValidationQueryError(ResultStoreError):
    """One stable validation-specific query failure."""


@contextmanager
def _validation_snapshot(log_root: Path) -> Iterator[Any]:
    try:
        with result_snapshot(log_root) as db:
            yield db
    except ValidationQueryError:
        raise
    except ResultStoreError as error:
        raise ValidationQueryError(
            _validation_store_code(error.code), str(error)
        ) from error
    except sqlite3.DatabaseError as error:
        raise ValidationQueryError(
            "validation.store.malformed", "saved validation store is malformed"
        ) from error


def _validation_store_code(code: str) -> str:
    return {
        "results.store.busy": "validation.store.busy",
        "results.store.malformed": "validation.store.malformed",
        "results.store.missing": "validation.store.missing",
        "results.schema.unsupported": "validation.schema.unsupported",
    }.get(code, "validation.store.unavailable")


def show_validation(log_root: Path) -> dict[str, object]:
    """Return one read-only saved full-log summary or Not validated row."""

    try:
        with _validation_snapshot(log_root) as db:
            version = _version(db)
            if version in REPLACEABLE_STORE_VERSIONS:
                return _show_not_validated(log_root, replacement_required=True)
            _require_current(version)
            row = db.execute(
                "SELECT * FROM validation_snapshots WHERE slot='full'"
            ).fetchone()
            if row is None:
                return _show_not_validated(log_root, replacement_required=False)
            counts = _finding_counts(db, int(row["snapshot_pk"]))
            marker = db.execute(
                "SELECT source_generation FROM report_materializations "
                "WHERE kind='validation'"
            ).fetchone()
            item: dict[str, object] = {
                "batch_count": int(row["batch_count"]),
                "blocked_check_count": int(row["blocked_count"]),
                "failed_check_count": int(row["failed_count"]),
                "finding_counts_by_type": counts,
                "log": str(log_root),
                "outcome": str(row["outcome"]).title(),
                "report_materialization": (
                    "current"
                    if marker is not None
                    and int(marker[0]) == int(row["generation"])
                    else "required"
                ),
                "saved_at": str(row["stored_at"]),
            }
            return _bound(
                {"schema": "research-log-validation-show/1", "rows": [item]}
            )
    except ResultStoreError as error:
        if error.code == "validation.store.missing":
            return _show_not_validated(log_root, replacement_required=False)
        raise


def show_validation_root(log_roots: Sequence[Path]) -> dict[str, object]:
    """Summarize discovered logs while isolating per-log store failures."""

    rows: list[object] = []
    blocked = 0
    failed = 0
    contributors = 0
    for log_root in log_roots:
        try:
            current = show_validation(log_root)["rows"]
            if not isinstance(current, list):
                raise ValidationQueryError(
                    "validation.store.malformed", "show rows are invalid"
                )
            for row in current:
                blocked_count = row.get("blocked_check_count")
                failed_count = row.get("failed_check_count")
                if isinstance(blocked_count, int) and isinstance(failed_count, int):
                    blocked += blocked_count
                    failed += failed_count
                    contributors += 1
                rows.append(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"blocked_check_count", "failed_check_count"}
                    }
                )
        except ResultStoreError as error:
            rows.append(
                {
                    "error": {"code": error.code, "message": str(error)},
                    "log": str(log_root),
                    "outcome": "Error",
                }
            )
    return _bound(
        {
            "blocked_check_total": blocked,
            "failed_check_total": failed,
            "contributing_log_count": contributors,
            "rows": rows,
            "schema": "research-log-validation-show/1",
            "totals_partial": contributors != len(log_roots),
        }
    )


def list_findings(
    log_root: Path,
    *,
    entry: str | None = None,
    finding_type: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """List atomic findings from one selected completed snapshot."""

    _page_size(limit)
    if finding_type is not None and finding_type not in _TYPES:
        raise ValidationQueryError(
            "validation.type.invalid", "finding type is unsupported"
        )
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        where, args = _finding_filter(selected, entry, finding_type)
        total = _count(db, "validation_findings AS f", where, args)
        binding = _binding(selected, "finding-list", entry, finding_type)
        last = _cursor_last(cursor, binding)
        if last is not None:
            where.append("f.position>?")
            args.append(last)
        rows = db.execute(
            "SELECT f.finding_id,f.type,f.code,f.entry,f.subject,f.position,"
            "b.batch_id FROM validation_findings AS f "
            "JOIN validation_batch_findings AS m "
            "USING (snapshot_pk,finding_pk) "
            "JOIN validation_batches AS b USING (snapshot_pk,batch_pk) "
            f"WHERE {' AND '.join(where)} ORDER BY f.position LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
        items = [
            {
                "batch_id": str(row["batch_id"]),
                "code": str(row["code"]),
                **({} if row["entry"] is None else {"entry": str(row["entry"])}),
                "finding_id": str(row["finding_id"]),
                "subject": str(row["subject"]),
                "type": str(row["type"]),
            }
            for row in rows[:limit]
        ]
        return _bound(
            _page(
                "research-log-validation-finding-list/1",
                log_root,
                selected,
                total,
                items,
                rows,
                limit,
                binding,
            )
        )


def list_batches(
    log_root: Path,
    *,
    entry: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """List complete repair batches without assigning a batch type."""

    _page_size(limit)
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        where = ["b.snapshot_pk=?"]
        args: list[object] = [selected["snapshot_pk"]]
        if entry is not None and selected["slot"] == "full":
            where.append(
                "EXISTS (SELECT 1 FROM validation_batch_entries AS e "
                "WHERE e.snapshot_pk=b.snapshot_pk AND e.batch_pk=b.batch_pk "
                "AND e.entry=?)"
            )
            args.append(entry)
        total = _count(db, "validation_batches AS b", where, args)
        binding = _binding(selected, "batch-list", entry, None)
        last = _cursor_last(cursor, binding)
        if last is not None:
            where.append("b.position>?")
            args.append(last)
        rows = db.execute(
            "SELECT b.*,f.finding_id AS focus_finding_id "
            "FROM validation_batches AS b JOIN validation_findings AS f "
            "ON f.snapshot_pk=b.snapshot_pk AND f.finding_pk=b.focus_finding_pk "
            f"WHERE {' AND '.join(where)} ORDER BY b.position LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
        items = [_batch_list_item(db, selected, row) for row in rows[:limit]]
        return _bound(
            _page(
                "research-log-validation-batch-list/1",
                log_root,
                selected,
                total,
                items,
                rows,
                limit,
                binding,
            )
        )


def list_blocked(
    log_root: Path,
    *,
    entry: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """List canonical blocked validation checks from one saved snapshot."""

    _page_size(limit)
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        where = ["c.snapshot_pk=?"]
        args: list[object] = [selected["snapshot_pk"]]
        if entry is not None and selected["slot"] == "full":
            where.append("c.entry=?")
            args.append(entry)
        total = _count(db, "validation_blocked_checks AS c", where, args)
        binding = _binding(selected, "blocked-list", entry, None)
        last = _cursor_last(cursor, binding)
        if last is not None:
            where.append("c.position>?")
            args.append(last)
        rows = db.execute(
            "SELECT * FROM validation_blocked_checks AS c "
            f"WHERE {' AND '.join(where)} ORDER BY c.position LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
        items = [
            {
                "area": str(row["area"]),
                "blocked_by": _json_string_array(row["blocked_by_json"]),
                "check_id": str(row["check_id"]),
                **({} if row["entry"] is None else {"entry": str(row["entry"])}),
                "rule": str(row["rule"]),
                "subject": str(row["subject"]),
            }
            for row in rows[:limit]
        ]
        return _bound(
            _page(
                "research-log-validation-blocked-list/1",
                log_root,
                selected,
                total,
                items,
                rows,
                limit,
                binding,
            )
        )


def list_failed(
    log_root: Path,
    *,
    entry: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """List canonical failed validation checks from one saved snapshot."""

    _page_size(limit)
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        where = ["c.snapshot_pk=?"]
        args: list[object] = [selected["snapshot_pk"]]
        if entry is not None and selected["slot"] == "full":
            where.append("c.entry=?")
            args.append(entry)
        total = _count(db, "validation_failed_checks AS c", where, args)
        binding = _binding(selected, "failed-list", entry, None)
        last = _cursor_last(cursor, binding)
        if last is not None:
            where.append("c.position>?")
            args.append(last)
        rows = db.execute(
            "SELECT * FROM validation_failed_checks AS c "
            f"WHERE {' AND '.join(where)} ORDER BY c.position LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
        items = [
            {
                "area": str(row["area"]),
                "check_id": str(row["check_id"]),
                "code": str(row["code"]),
                **({} if row["entry"] is None else {"entry": str(row["entry"])}),
                "operation": str(row["operation"]),
                "reason": _json_mapping(row["reason_json"]),
                "rule": str(row["rule"]),
                "subject": str(row["subject"]),
            }
            for row in rows[:limit]
        ]
        return _bound(
            _page(
                "research-log-validation-failed-list/1",
                log_root,
                selected,
                total,
                items,
                rows,
                limit,
                binding,
            )
        )


def finding_detail(  # noqa: PLR0913 -- public query contract
    log_root: Path,
    finding_id: str,
    *,
    entry: str | None = None,
    section: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return one finding header and one bounded diagnostic section."""

    _page_size(limit)
    requested_section = _section("finding", section) if section is not None else None
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        finding = db.execute(
            "SELECT * FROM validation_findings WHERE snapshot_pk=? AND finding_id=?",
            (selected["snapshot_pk"], finding_id),
        ).fetchone()
        if finding is None or not _finding_selected(db, selected, finding, entry):
            raise ValidationQueryError(
                "validation.finding.missing", "finding is not in the selected snapshot"
            )
        batch = db.execute(
            "SELECT b.* FROM validation_batch_findings AS m "
            "JOIN validation_batches AS b USING (snapshot_pk,batch_pk) "
            "WHERE m.snapshot_pk=? AND m.finding_pk=?",
            (selected["snapshot_pk"], finding["finding_pk"]),
        ).fetchone()
        assert batch is not None
        section = requested_section or _first_applicable_finding_section(
            db, int(selected["snapshot_pk"]), int(finding["finding_pk"])
        )
        items, total, rows, binding = _finding_section(
            db, selected, finding, section, entry, limit, cursor
        )
        value = {
            "batch": _batch_summary(db, selected, batch),
            "finding": _finding_value(db, selected, finding),
            "items": items,
            "log": str(log_root),
            "schema": "research-log-validation-finding-detail/1",
            "section": section,
            "section_total": total,
            "selected_target": _selected_target(selected),
            "saved_at": str(selected["stored_at"]),
        }
        return _detail_page(value, items, rows, binding)


def batch_detail(  # noqa: PLR0913 -- public query contract
    log_root: Path,
    batch_id: str,
    *,
    entry: str | None = None,
    section: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return one batch header and one bounded pre-investigation repair section."""

    _page_size(limit)
    requested_section = _section("batch", section) if section is not None else None
    with _validation_snapshot(log_root) as db:
        selected = _select_snapshot(db, entry, log_root)
        batch = db.execute(
            "SELECT * FROM validation_batches WHERE snapshot_pk=? AND batch_id=?",
            (selected["snapshot_pk"], batch_id),
        ).fetchone()
        if batch is None or not _batch_selected(db, selected, batch, entry):
            raise ValidationQueryError(
                "validation.batch.missing", "batch is not in the selected snapshot"
            )
        snapshot = load_validation_snapshot(log_root, slot=str(selected["slot"]))
        if snapshot.internal_snapshot_id != str(selected["snapshot_id"]):
            raise ValidationQueryError(
                "validation.store.busy", "saved validation changed during the query"
            )
        graph_sections = _batch_graph_sections(snapshot, batch_id)
        section = requested_section or _first_applicable_batch_section(
            db,
            int(selected["snapshot_pk"]),
            int(batch["batch_pk"]),
            graph_sections,
        )
        items, total, rows, binding = _batch_section(
            db,
            selected,
            batch,
            graph_sections,
            section,
            entry,
            limit,
            cursor,
        )
        value = {
            "batch": _batch_summary(db, selected, batch),
            "items": items,
            "log": str(log_root),
            "membership_counts": _batch_membership_counts(
                db, selected, batch, graph_sections
            ),
            "schema": "research-log-validation-batch-detail/1",
            "section": section,
            "section_total": total,
            "selected_target": _selected_target(selected),
            "saved_at": str(selected["stored_at"]),
        }
        return _detail_page(value, items, rows, binding)


def _select_snapshot(db: Any, entry: str | None, log_root: Path) -> Any:
    version = _version(db)
    if version in REPLACEABLE_STORE_VERSIONS:
        raise ValidationQueryError(
            "validation.replacement_required",
            "saved validation requires replacement; run " + _run_command(log_root),
        )
    _require_current(version)
    if entry is None:
        row = db.execute(
            "SELECT * FROM validation_snapshots WHERE slot='full'"
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM validation_snapshots WHERE slot IN ('full',?) "
            "ORDER BY generation DESC LIMIT 1",
            (f"entry:{entry}",),
        ).fetchone()
    if row is None:
        raise ValidationQueryError(
            "validation.not_validated", "no completed validation is selectable"
        )
    if entry is not None and row["slot"] == "full":
        represented = db.execute(
            "SELECT 1 FROM validation_snapshot_entries "
            "WHERE snapshot_pk=? AND entry=? LIMIT 1",
            (row["snapshot_pk"], entry),
        ).fetchone()
        if represented is None:
            raise ValidationQueryError(
                "validation.entry.missing",
                "entry is not represented by the selected full-log validation",
            )
    return {**dict(row), "_query_log": str(log_root.resolve())}


def _finding_filter(
    selected: Any,
    entry: str | None,
    finding_type: str | None,
) -> tuple[list[str], list[object]]:
    where = ["f.snapshot_pk=?"]
    args: list[object] = [selected["snapshot_pk"]]
    if finding_type is not None:
        where.append("f.type=?")
        args.append(finding_type)
    if entry is not None and selected["slot"] == "full":
        where.append(
            "(f.entry=? OR EXISTS (SELECT 1 FROM validation_batch_findings AS bm "
            "JOIN validation_batch_entries AS be USING (snapshot_pk,batch_pk) "
            "WHERE bm.snapshot_pk=f.snapshot_pk AND bm.finding_pk=f.finding_pk "
            "AND be.entry=?))"
        )
        args.extend((entry, entry))
    return where, args


def _finding_selected(db: Any, selected: Any, finding: Any, entry: str | None) -> bool:
    if entry is None or selected["slot"] != "full":
        return True
    return bool(
        finding["entry"] == entry
        or db.execute(
            "SELECT 1 FROM validation_batch_findings AS m "
            "JOIN validation_batch_entries AS e USING (snapshot_pk,batch_pk) "
            "WHERE m.snapshot_pk=? AND m.finding_pk=? AND e.entry=? LIMIT 1",
            (selected["snapshot_pk"], finding["finding_pk"], entry),
        ).fetchone()
    )


def _batch_selected(db: Any, selected: Any, batch: Any, entry: str | None) -> bool:
    if entry is None or selected["slot"] != "full":
        return True
    return bool(
        db.execute(
            "SELECT 1 FROM validation_batch_entries "
            "WHERE snapshot_pk=? AND batch_pk=? AND entry=? LIMIT 1",
            (selected["snapshot_pk"], batch["batch_pk"], entry),
        ).fetchone()
    )


def _finding_counts(db: Any, snapshot_pk: int) -> dict[str, int]:
    counts = dict.fromkeys(_TYPES, 0)
    counts.update(
        {
            str(row["type"]): int(row["count"])
            for row in db.execute(
                "SELECT type,count(*) AS count FROM validation_findings "
                "WHERE snapshot_pk=? GROUP BY type",
                (snapshot_pk,),
            )
        }
    )
    return counts


def _batch_list_item(db: Any, selected: Any, row: Any) -> dict[str, object]:
    entries = _batch_entries(db, selected, int(row["batch_pk"]))
    types = [
        str(item[0])
        for item in db.execute(
            "SELECT DISTINCT f.type FROM validation_batch_findings AS m "
            "JOIN validation_findings AS f USING (snapshot_pk,finding_pk) "
            "WHERE m.snapshot_pk=? AND m.batch_pk=? ORDER BY f.type",
            (selected["snapshot_pk"], row["batch_pk"]),
        )
    ]
    rationale = [
        str(item[0])
        for item in db.execute(
            "SELECT rationale FROM validation_batch_rationale "
            "WHERE snapshot_pk=? AND batch_pk=? ORDER BY position",
            (selected["snapshot_pk"], row["batch_pk"]),
        )
    ]
    return {
        "batch_id": str(row["batch_id"]),
        "context_entries": entries["context"],
        "finding_count": int(row["finding_count"]),
        "focus_finding_id": str(row["focus_finding_id"]),
        "rationale": rationale,
        "repair_entries": entries["repair"],
        "represented_types": types,
    }


def _finding_value(db: Any, selected: Any, row: Any) -> dict[str, object]:
    finding_pk = int(row["finding_pk"])
    causes = [
        str(item[0])
        for item in db.execute(
            "SELECT c.finding_id FROM validation_finding_causes AS x "
            "JOIN validation_findings AS c ON c.snapshot_pk=x.snapshot_pk "
            "AND c.finding_pk=x.cause_finding_pk "
            "WHERE x.snapshot_pk=? AND x.finding_pk=? ORDER BY x.position",
            (selected["snapshot_pk"], finding_pk),
        )
    ]
    locations = [
        {
            "path": str(item["path"]),
            **(
                {}
                if item["line"] is None
                else {"line": int(item["line"])}
            ),
        }
        for item in db.execute(
            "SELECT path,line FROM validation_finding_source_locations "
            "WHERE snapshot_pk=? AND finding_pk=? ORDER BY position",
            (selected["snapshot_pk"], finding_pk),
        )
    ]
    return {
        "caused_by": causes,
        "code": str(row["code"]),
        "diagnosis": _finding_diagnosis(selected, row),
        **({} if row["entry"] is None else {"entry": str(row["entry"])}),
        "finding_id": str(row["finding_id"]),
        "observed": _json_mapping(row["observed_json"]),
        "rule": str(row["rule"]),
        "source_locations": locations,
        "subject": str(row["subject"]),
        "type": str(row["type"]),
    }


def _finding_diagnosis(selected: Any, row: Any) -> dict[str, str]:
    """Return the human explanation saved with one finding's snapshot."""

    context = _json_mapping(selected["report_context_json"])
    try:
        presentation = finding_presentation(context, str(row["code"]))
    except (KeyError, SnapshotReportError) as error:
        raise ValidationQueryError(
            "validation.store.malformed", "saved finding explanation is invalid"
        ) from error
    return {
        "explanation": presentation["sentence"],
        "title": presentation["name"],
    }


def _batch_summary(db: Any, selected: Any, row: Any) -> dict[str, object]:
    item = _batch_list_item(
        db,
        selected,
        {
            **dict(row),
            "focus_finding_id": db.execute(
                "SELECT finding_id FROM validation_findings "
                "WHERE snapshot_pk=? AND finding_pk=?",
                (selected["snapshot_pk"], row["focus_finding_pk"]),
            ).fetchone()[0],
        },
    )
    return item


def _batch_entries(db: Any, selected: Any, batch_pk: int) -> dict[str, list[str]]:
    return {
        role: [
            str(item[0])
            for item in db.execute(
                "SELECT entry FROM validation_batch_entries "
                "WHERE snapshot_pk=? AND batch_pk=? AND role=? ORDER BY position",
                (selected["snapshot_pk"], batch_pk, role),
            )
        ]
        for role in ("repair", "context")
    }


def _finding_section(  # noqa: PLR0913 -- bounded query coordinates
    db: Any,
    selected: Any,
    finding: Any,
    section: str,
    entry: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[object], int, Sequence[Any], Mapping[str, object]]:
    binding = _binding(
        selected,
        f"finding-detail:{finding['finding_id']}:{section}",
        entry,
        None,
    )
    last = _cursor_last(cursor, binding)
    snapshot_pk, finding_pk = selected["snapshot_pk"], finding["finding_pk"]
    items: list[object]
    if section == "repair_keys":
        query = (
            "SELECT l.position,k.kind,k.identity,l.repair_entry "
            "FROM validation_finding_repair_keys AS l "
            "JOIN validation_repair_keys AS k USING (snapshot_pk,repair_key_pk) "
            "WHERE l.snapshot_pk=? AND l.finding_pk=?"
        )
        rows = _section_rows(db, query, (snapshot_pk, finding_pk), last, limit)
        items = [
            {
                "kind": str(row["kind"]),
                "identity": str(row["identity"]),
                **(
                    {}
                    if row["repair_entry"] is None
                    else {"repair_entry": str(row["repair_entry"])}
                ),
            }
            for row in rows[:limit]
        ]
    elif section == "nodes":
        query = (
            "SELECT l.position,n.* FROM validation_finding_nodes AS l "
            "JOIN validation_repair_nodes AS n USING (snapshot_pk,node_pk) "
            "WHERE l.snapshot_pk=? AND l.finding_pk=?"
        )
        rows = _section_rows(db, query, (snapshot_pk, finding_pk), last, limit)
        items = [_node_value(row) for row in rows[:limit]]
    else:
        rows, items = _relationship_section(
            db, selected, "finding", finding_pk, section, last, limit
        )
    total = _finding_section_total(db, snapshot_pk, finding_pk, section)
    return items, total, rows, binding


def _batch_section(  # noqa: PLR0913 -- bounded query coordinates
    db: Any,
    selected: Any,
    batch: Any,
    graph_sections: Mapping[str, Sequence[Mapping[str, object]]],
    section: str,
    entry: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[object], int, Sequence[Any], Mapping[str, object]]:
    binding = _binding(
        selected,
        f"batch-detail:{batch['batch_id']}:{section}",
        entry,
        None,
    )
    last = _cursor_last(cursor, binding)
    snapshot_pk, batch_pk = selected["snapshot_pk"], batch["batch_pk"]
    items: list[object]
    if section == "findings":
        query = (
            "SELECT l.position,f.* FROM validation_batch_findings AS l "
            "JOIN validation_findings AS f USING (snapshot_pk,finding_pk) "
            "WHERE l.snapshot_pk=? AND l.batch_pk=?"
        )
        rows = _section_rows(db, query, (snapshot_pk, batch_pk), last, limit)
        items = [_finding_value(db, selected, row) for row in rows[:limit]]
    elif section == "repair_keys":
        query = (
            "SELECT l.position,k.kind,k.identity "
            "FROM validation_batch_repair_keys AS l "
            "JOIN validation_repair_keys AS k USING (snapshot_pk,repair_key_pk) "
            "WHERE l.snapshot_pk=? AND l.batch_pk=?"
        )
        rows = _section_rows(db, query, (snapshot_pk, batch_pk), last, limit)
        items = [
            {"kind": str(row["kind"]), "identity": str(row["identity"])}
            for row in rows[:limit]
        ]
    elif section == "nodes":
        rows = _derived_section_rows(graph_sections[section], last, limit)
        items = [plain_json(row["value"]) for row in rows[:limit]]
    else:
        rows = _derived_section_rows(graph_sections[section], last, limit)
        items = [plain_json(row["value"]) for row in rows[:limit]]
    total = _batch_section_total(
        db, snapshot_pk, batch_pk, graph_sections, section
    )
    return items, total, rows, binding


def _relationship_section(  # noqa: PLR0913 -- shared owner query coordinates
    db: Any,
    selected: Any,
    owner_kind: str,
    owner_pk: int,
    section: str,
    last: int | None,
    limit: int,
) -> tuple[Sequence[Any], list[object]]:
    assert owner_kind == "finding"
    owner_table = "validation_finding_nodes"
    owner_column = "finding_pk"
    relation_kind = "established" if section == "relationships" else "ambiguous"
    rows = db.execute(
        "SELECT DISTINCT e.* FROM validation_repair_edges AS e "
        f"JOIN {owner_table} AS n ON n.snapshot_pk=e.snapshot_pk "
        "AND (n.node_pk=e.subject_node_pk OR EXISTS ("
        "SELECT 1 FROM validation_repair_edge_targets AS t "
        "WHERE t.snapshot_pk=e.snapshot_pk AND t.edge_pk=e.edge_pk "
        "AND t.target_node_pk=n.node_pk)) "
        f"WHERE n.snapshot_pk=? AND n.{owner_column}=? "
        "AND e.relation_kind=? AND e.position>? ORDER BY e.position LIMIT ?",
        (
            selected["snapshot_pk"],
            owner_pk,
            relation_kind,
            -1 if last is None else last,
            limit + 1,
        ),
    ).fetchall()
    items: list[object] = [
        _edge_value(db, selected, row) for row in rows[:limit]
    ]
    return rows, items


def _edge_value(db: Any, selected: Any, row: Any) -> dict[str, object]:
    subject = db.execute(
        "SELECT node_id FROM validation_repair_nodes "
        "WHERE snapshot_pk=? AND node_pk=?",
        (selected["snapshot_pk"], row["subject_node_pk"]),
    ).fetchone()[0]
    targets = [
        str(item[0])
        for item in db.execute(
            "SELECT n.node_id FROM validation_repair_edge_targets AS t "
            "JOIN validation_repair_nodes AS n ON n.snapshot_pk=t.snapshot_pk "
            "AND n.node_pk=t.target_node_pk "
            "WHERE t.snapshot_pk=? AND t.edge_pk=? ORDER BY t.position",
            (selected["snapshot_pk"], row["edge_pk"]),
        )
    ]
    if row["relation_kind"] == "established":
        return {"kind": str(row["kind"]), "source": str(subject), "target": targets[0]}
    return {
        "candidates": targets,
        "kind": str(row["kind"]),
        "observed": _json_mapping(row["observed_json"]),
        "subject": str(subject),
    }


def _node_value(row: Any) -> dict[str, object]:
    return {
        "attributes": _json_mapping(row["attributes_json"]),
        **({} if row["entry"] is None else {"entry": str(row["entry"])}),
        "identity": str(row["identity"]),
        "kind": str(row["kind"]),
        "node_id": str(row["node_id"]),
    }


def _finding_section_total(
    db: Any,
    snapshot_pk: int,
    finding_pk: int,
    section: str,
) -> int:
    if section == "repair_keys":
        table, condition = "validation_finding_repair_keys", "finding_pk=?"
    elif section == "nodes":
        table, condition = "validation_finding_nodes", "finding_pk=?"
    else:
        return _relationship_total(db, snapshot_pk, "finding", finding_pk, section)
    return _count(db, table, ["snapshot_pk=?", condition], [snapshot_pk, finding_pk])


def _batch_section_total(
    db: Any,
    snapshot_pk: int,
    batch_pk: int,
    graph_sections: Mapping[str, Sequence[Mapping[str, object]]],
    section: str,
) -> int:
    tables = {
        "findings": "validation_batch_findings",
        "repair_keys": "validation_batch_repair_keys",
    }
    if section in tables:
        return _count(
            db,
            tables[section],
            ["snapshot_pk=?", "batch_pk=?"],
            [snapshot_pk, batch_pk],
        )
    return len(graph_sections[section])


def _relationship_total(
    db: Any, snapshot_pk: int, owner_kind: str, owner_pk: int, section: str
) -> int:
    assert owner_kind == "finding"
    table = "validation_finding_nodes"
    column = "finding_pk"
    kind = "established" if section == "relationships" else "ambiguous"
    row = db.execute(
        "SELECT count(DISTINCT e.edge_pk) FROM validation_repair_edges AS e "
        f"JOIN {table} AS n ON n.snapshot_pk=e.snapshot_pk "
        "AND (n.node_pk=e.subject_node_pk OR EXISTS (SELECT 1 "
        "FROM validation_repair_edge_targets AS t WHERE t.snapshot_pk=e.snapshot_pk "
        "AND t.edge_pk=e.edge_pk AND t.target_node_pk=n.node_pk)) "
        f"WHERE n.snapshot_pk=? AND n.{column}=? AND e.relation_kind=?",
        (snapshot_pk, owner_pk, kind),
    ).fetchone()
    return int(row[0])


def _batch_membership_counts(
    db: Any,
    selected: Any,
    batch: Any,
    graph_sections: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, int]:
    snapshot_pk, batch_pk = selected["snapshot_pk"], batch["batch_pk"]
    return {
        "findings": int(batch["finding_count"]),
        "repair_keys": _count(
            db,
            "validation_batch_repair_keys",
            ["snapshot_pk=?", "batch_pk=?"],
            [snapshot_pk, batch_pk],
        ),
        "nodes": len(graph_sections["nodes"]),
        "relationships": len(graph_sections["relationships"]),
        "ambiguities": len(graph_sections["ambiguities"]),
    }


def _batch_graph_sections(
    snapshot: ValidationSnapshot,
    batch_id: str,
) -> Mapping[str, tuple[Mapping[str, object], ...]]:
    """Derive one selected batch's bounded view from the shared saved graph."""

    node_ids = set(batch_repair_node_ids(snapshot, batch_id))
    repair = snapshot.repair_context
    nodes = _repair_context_items(repair, "nodes")
    relationships = _repair_context_items(repair, "relationships")
    ambiguities = _repair_context_items(repair, "ambiguities")
    return {
        "nodes": tuple(
            value for value in nodes if cast(str, value["node_id"]) in node_ids
        ),
        "relationships": tuple(
            value
            for value in relationships
            if cast(str, value["source"]) in node_ids
            and cast(str, value["target"]) in node_ids
        ),
        "ambiguities": tuple(
            value
            for value in ambiguities
            if cast(str, value["subject"]) in node_ids
            and set(cast(Sequence[str], value["candidates"])) <= node_ids
        ),
    }


def _repair_context_items(
    repair: Mapping[str, object], name: str
) -> tuple[Mapping[str, object], ...]:
    values = repair.get(name)
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(value, Mapping) for value in values
    ):
        raise ValidationQueryError(
            "validation.store.malformed", "saved repair context is malformed"
        )
    return tuple(cast(Mapping[str, object], value) for value in values)


def _derived_section_rows(
    values: Sequence[Mapping[str, object]],
    last: int | None,
    limit: int,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {"position": position, "value": value}
        for position, value in enumerate(values)
        if last is None or position > last
    )[: limit + 1]


def _section_rows(
    db: Any,
    query: str,
    args: Sequence[object],
    last: int | None,
    limit: int,
) -> Sequence[Any]:
    return db.execute(
        query + " AND l.position>? ORDER BY l.position LIMIT ?",
        (*args, -1 if last is None else last, limit + 1),
    ).fetchall()


def _section(kind: str, section: str | None) -> str:
    value = section or _DETAIL_SECTIONS[kind][0]
    if value not in _DETAIL_SECTIONS[kind]:
        raise ValidationQueryError(
            "validation.section.invalid", "detail section is unsupported"
        )
    return value


def _first_applicable_finding_section(
    db: Any,
    snapshot_pk: int,
    finding_pk: int,
) -> str:
    return next(
        (
            section
            for section in _DETAIL_SECTIONS["finding"]
            if _finding_section_total(db, snapshot_pk, finding_pk, section)
        ),
        _DETAIL_SECTIONS["finding"][0],
    )


def _first_applicable_batch_section(
    db: Any,
    snapshot_pk: int,
    batch_pk: int,
    graph_sections: Mapping[str, Sequence[Mapping[str, object]]],
) -> str:
    return next(
        (
            section
            for section in _DETAIL_SECTIONS["batch"]
            if _batch_section_total(
                db, snapshot_pk, batch_pk, graph_sections, section
            )
        ),
        _DETAIL_SECTIONS["batch"][0],
    )


def _show_not_validated(
    log_root: Path, *, replacement_required: bool
) -> dict[str, object]:
    row: dict[str, object] = {"log": str(log_root), "outcome": "Not validated"}
    if replacement_required:
        row.update(
            {
                "next_command": _run_command(log_root),
                "replacement_required": True,
            }
        )
    return _bound({"schema": "research-log-validation-show/1", "rows": [row]})


def _run_command(log_root: Path) -> str:
    return f"log validate run --path {shlex.quote(str(log_root))}"


def _selected_target(selected: Any) -> dict[str, object]:
    return {
        "kind": str(selected["target_kind"]),
        "log": str(selected["target_log"]),
        **(
            {}
            if selected["target_entry"] is None
            else {"entry": str(selected["target_entry"])}
        ),
    }


def _binding(
    selected: Any, action: str, entry: str | None, finding_type: str | None
) -> dict[str, object]:
    return {
        "action": action,
        "entry": entry,
        "finding_type": finding_type,
        "generation": int(selected["generation"]),
        "log": str(selected["_query_log"]),
        "snapshot_id": str(selected["snapshot_id"]),
    }


def _page(  # noqa: PLR0913 -- common validation page envelope
    schema: str,
    log_root: Path,
    selected: Any,
    total: int,
    items: Sequence[object],
    rows: Sequence[Any],
    limit: int,
    binding: Mapping[str, object],
) -> dict[str, object]:
    retained = list(items[:limit])
    if not retained and not rows:
        return {
            "items": [],
            "log": str(log_root),
            "saved_at": str(selected["stored_at"]),
            "schema": schema,
            "selected_target": _selected_target(selected),
            "total": total,
        }
    while retained:
        value: dict[str, object] = {
            "items": retained,
            "log": str(log_root),
            "saved_at": str(selected["stored_at"]),
            "schema": schema,
            "selected_target": _selected_target(selected),
            "total": total,
        }
        if len(rows) > len(retained):
            value["next_cursor"] = _encode_cursor(
                binding, int(rows[len(retained) - 1]["position"])
            )
        if _response_size(value) <= MAX_RESPONSE_BYTES:
            return value
        retained.pop()
    raise ValidationQueryError(
        "validation.response.too_large",
        "one list item exceeds the validation response budget",
    )


def _detail_page(
    header: dict[str, object],
    items: Sequence[object],
    rows: Sequence[Any],
    binding: Mapping[str, object],
) -> dict[str, object]:
    retained = list(items)
    while retained:
        value = {**header, "items": retained}
        if len(rows) > len(retained):
            value["next_cursor"] = _encode_cursor(
                binding, int(rows[len(retained) - 1]["position"])
            )
        if _response_size(value) <= MAX_RESPONSE_BYTES:
            return value
        retained.pop()
    value = {**header, "items": []}
    if not rows and _response_size(value) <= MAX_RESPONSE_BYTES:
        return value
    raise ValidationQueryError(
        "validation.response.too_large",
        "one detail item exceeds the validation response budget",
    )


def _cursor_last(cursor: str | None, binding: Mapping[str, object]) -> int | None:
    if cursor is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception as error:
        raise ValidationQueryError(
            "validation.cursor.invalid", "restart the query without its cursor"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"binding", "last"}
        or value.get("binding") != binding
        or not isinstance(value.get("last"), int)
        or isinstance(value.get("last"), bool)
        or int(value["last"]) < 0
    ):
        raise ValidationQueryError(
            "validation.cursor.invalid", "cursor does not match the selected snapshot"
        )
    return int(value["last"])


def _encode_cursor(binding: Mapping[str, object], last: int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(
            {"binding": binding, "last": last},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).decode()


def _page_size(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValidationQueryError(
            "validation.limit.invalid", "limit must be between 1 and 100"
        )


def _count(
    db: Any, table: str, where: Sequence[str], args: Sequence[object]
) -> int:
    row = db.execute(
        f"SELECT count(*) FROM {table} WHERE {' AND '.join(where)}", args
    ).fetchone()
    return int(row[0])


def _version(db: Any) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def _require_current(version: int) -> None:
    if version != STORE_VERSION:
        raise ValidationQueryError(
            "validation.schema.unsupported", f"store version {version} is unsupported"
        )


def _json_value(value: object) -> object:
    if not isinstance(value, str):
        raise ValidationQueryError(
            "validation.store.malformed", "stored JSON value is invalid"
        )
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ValidationQueryError(
            "validation.store.malformed", "stored JSON value is invalid"
        ) from error


def _json_mapping(value: object) -> Mapping[str, object]:
    parsed = _json_value(value)
    if not isinstance(parsed, dict):
        raise ValidationQueryError(
            "validation.store.malformed", "stored JSON object is invalid"
        )
    return cast(Mapping[str, object], parsed)


def _json_array(value: object) -> list[object]:
    parsed = _json_value(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, dict) for item in parsed
    ):
        raise ValidationQueryError(
            "validation.store.malformed", "stored JSON array is invalid"
        )
    return parsed


def _json_string_array(value: object) -> list[str]:
    parsed = _json_value(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item for item in parsed
    ):
        raise ValidationQueryError(
            "validation.store.malformed", "stored JSON string array is invalid"
        )
    return cast(list[str], parsed)


def _bound(value: dict[str, object]) -> dict[str, object]:
    if _response_size(value) > MAX_RESPONSE_BYTES:
        raise ValidationQueryError(
            "validation.response.too_large",
            "select a smaller page or a narrower detail section",
        )
    return value


def _response_size(value: Mapping[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())
