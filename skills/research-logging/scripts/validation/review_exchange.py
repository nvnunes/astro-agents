"""Bounded semantic question-and-decision exchange for target validation."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import DECISION_SCHEMA_VERSION
from .review_batches import (
    OrphanBatchRequest,
    ordered_orphan_candidates,
    orphan_candidate_fingerprint,
    select_orphan_batch,
)

EXCHANGE_SCHEMA_VERSION = 1
INTERNAL_FILENAME = ".continuation.json"
MAX_PACKET_ITEMS = 200
DEFERRED_SESSION_SCHEMA_VERSION = 1
DEFERRED_BASE_FILENAME = "base.json"
DEFERRED_INDEX_FILENAME = "index.json"
DEFERRED_MANIFEST_FILENAME = "manifest.json"


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    """Return one deterministic JSON representation for temporary state."""

    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: bytes) -> None:
    """Atomically replace one temporary review-session file."""

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _question(item: Mapping[str, Any]) -> str:
    return str(item.get("reason") or f"Decide the {item['kind']} question.")


def _ordinary_template(item: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(item["kind"])
    allowed: list[Any]
    if kind == "upstream_producer":
        by_material: dict[str, list[str]] = {}
        for candidate in item.get("producer_candidates", []):
            material = str(candidate["material"])
            invocation = str(candidate["invocation"])
            values = by_material.setdefault(material, [])
            if invocation not in values:
                values.append(invocation)
        allowed = [
            {
                "bindings": [
                    {"material": material, "invocation": invocation}
                    for material, invocation in zip(
                        sorted(by_material), choices, strict=True
                    )
                ]
            }
            for choices in itertools.product(
                *(by_material[material] for material in sorted(by_material))
            )
        ]
        allowed.append("needs_context")
    elif kind == "collection_scope":
        allowed = [
            {
                "members": {
                    str(collection): ["<relative/member>"]
                    for collection in item.get("collections", [])
                }
            },
            "fail",
            "needs_context",
        ]
    elif kind == "semantic_provenance":
        allowed = ["fail", "needs_context"]
        if item.get("candidates"):
            allowed.insert(0, "pass")
    elif item.get("hard_failures"):
        allowed = ["keep", "needs_context"]
    else:
        allowed = ["pass", "fail", "needs_context"]
    identity = _fingerprint(item)
    return {
        "id": identity,
        "kind": kind,
        "entry": item.get("entry"),
        "identity": item.get("identity"),
        "question": _question(item),
        "allowed_decisions": allowed,
        "decision": None,
        "rationale": None,
    }


def _orphan_templates(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    item: Mapping[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    batch = select_orphan_batch(
        scan,
        adjudication,
        item,
        OrphanBatchRequest(limit, 1, DECISION_SCHEMA_VERSION),
    )
    notes = [
        str(note["sha256"])
        for note in item.get("validation_notes", [])
        if isinstance(note.get("sha256"), str)
    ]
    allowed = ["unresolved", "connected", *[f"retain:{note}" for note in notes]]
    allowed.append("needs_context")
    templates = []
    for candidate in batch.candidates:
        identity = str(candidate["identity"])
        templates.append(
            {
                "id": _fingerprint(
                    {
                        "queue": item,
                        "candidate": identity,
                        "fingerprint": batch.candidate_fingerprints[identity],
                    }
                ),
                "kind": "orphan_candidate",
                "entry": item["entry"],
                "identity": identity,
                "question": "How is this locally unconnected candidate classified?",
                "allowed_decisions": allowed,
                "decision": None,
                "rationale": None,
            }
        )
    return templates, dict(batch.candidate_fingerprints)


def _template_items(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    items: list[dict[str, Any]] = []
    orphan_fingerprints: dict[str, dict[str, str]] = {}
    for queue_item in adjudication["review_queue"]:
        remaining = MAX_PACKET_ITEMS - len(items)
        if remaining < 1:
            break
        if queue_item["kind"] != "orphan_candidates":
            items.append(_ordinary_template(queue_item))
            continue
        expanded, fingerprints = _orphan_templates(
            scan, adjudication, queue_item, remaining
        )
        items.extend(expanded)
        orphan_fingerprints[str(queue_item["entry"])] = fingerprints
    return items, orphan_fingerprints


def _packet_context(
    adjudication: AdjudicationRecord, item: Mapping[str, Any]
) -> Mapping[str, Any]:
    for queue_item in adjudication["review_queue"]:
        if queue_item.get("entry") != item.get("entry"):
            continue
        if item["kind"] == "orphan_candidate":
            if queue_item.get("kind") != "orphan_candidates":
                continue
            candidate: Mapping[str, Any] = next(
                (
                    candidate
                    for candidate in queue_item.get("candidates", [])
                    if candidate.get("identity") == item.get("identity")
                ),
                {},
            )
            return {
                "candidate": candidate,
                "validation_notes": queue_item.get("validation_notes", []),
            }
        if (
            queue_item.get("kind") == item.get("kind")
            and queue_item.get("identity") == item.get("identity")
        ):
            return {
                key: value
                for key, value in queue_item.items()
                if key not in {"candidates"}
            }
    return {}


def _render_packet_with_contexts(
    summary: str,
    items: list[dict[str, Any]],
    continuation: str,
    contexts: list[Mapping[str, Any]],
) -> str:
    lines = [
        "# Validation Review Packet",
        "",
        f"- Log: `{summary}`",
        f"- Continuation: `{continuation}`",
        f"- Questions: {len(items)}",
        "- Edit only the paired template's decision and rationale fields.",
    ]
    for number, (item, context) in enumerate(zip(items, contexts, strict=True), 1):
        lines.extend(
            [
                "",
                f"## Q{number:03d} — {item.get('entry')}: {item.get('identity')}",
                "",
                f"- Kind: `{item['kind']}`",
                f"- Question: {item['question']}",
                "- Allowed decisions: "
                + ", ".join(
                    f"`{json.dumps(value, sort_keys=True)}`"
                    for value in item["allowed_decisions"]
                ),
                "- Context:",
                "",
                "```json",
                json.dumps(
                    context,
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_packet(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    items: list[dict[str, Any]],
    continuation: str,
) -> str:
    return _render_packet_with_contexts(
        str(scan["summary"]),
        items,
        continuation,
        [_packet_context(adjudication, item) for item in items],
    )


def _continuation_identity(
    scan: ScanRecord, adjudication: AdjudicationRecord, items: list[dict[str, Any]]
) -> str:
    return _fingerprint(
        {
            "summary": scan["summary"],
            "rules": scan["validation_rules_version"],
            "scan": scan["input_fingerprint"],
            "date": adjudication["date"],
            "items": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"decision", "rationale"}
                }
                for item in items
            ],
        }
    )


def _deferred_orphan_index(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> dict[str, Any] | None:
    """Build one immutable orphan-review index when paging is worthwhile."""

    queue = adjudication.get("review_queue", [])
    if not queue or any(item.get("kind") != "orphan_candidates" for item in queue):
        return None
    indexed_items: list[dict[str, Any]] = []
    for item in queue:
        entry = str(item["entry"])
        notes = [
            str(note["sha256"])
            for note in item.get("validation_notes", [])
            if isinstance(note.get("sha256"), str)
        ]
        allowed = [
            "unresolved",
            "connected",
            *[f"retain:{note}" for note in notes],
            "needs_context",
        ]
        for candidate in ordered_orphan_candidates(item):
            identity = str(candidate["identity"])
            fingerprint = orphan_candidate_fingerprint(
                scan,
                adjudication.get("schema_version"),
                entry,
                candidate,
                DECISION_SCHEMA_VERSION,
            )
            indexed_items.append(
                {
                    "template": {
                        "id": _fingerprint(
                            {
                                "queue": item,
                                "candidate": identity,
                                "fingerprint": fingerprint,
                            }
                        ),
                        "kind": "orphan_candidate",
                        "entry": entry,
                        "identity": identity,
                        "question": (
                            "How is this locally unconnected candidate classified?"
                        ),
                        "allowed_decisions": allowed,
                        "decision": None,
                        "rationale": None,
                    },
                    "context": {
                        "candidate": candidate,
                        "validation_notes": item.get("validation_notes", []),
                    },
                    "fingerprint": fingerprint,
                }
            )
    if len(indexed_items) <= MAX_PACKET_ITEMS:
        return None
    session_identity = _fingerprint(
        {
            "summary": scan["summary"],
            "rules": scan["validation_rules_version"],
            "scan": scan["input_fingerprint"],
            "date": adjudication["date"],
            "items": [
                {
                    "id": item["template"]["id"],
                    "fingerprint": item["fingerprint"],
                }
                for item in indexed_items
            ],
        }
    )
    return {
        "schema_version": DEFERRED_SESSION_SCHEMA_VERSION,
        "session_identity": session_identity,
        "summary": scan["summary"],
        "items": indexed_items,
    }


def _deferred_page(
    session_dir: Path,
    index: Mapping[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Write the next bounded page and advance only the small manifest."""

    offset = int(manifest["next_offset"])
    page_number = int(manifest["next_page_number"])
    selected = list(index["items"])[offset : offset + MAX_PACKET_ITEMS]
    if not selected:
        raise ValidationToolError("deferred orphan session has no next page")
    items = [copy.deepcopy(item["template"]) for item in selected]
    continuation = _fingerprint(
        {
            "session": manifest["session_identity"],
            "page": page_number,
            "offset": offset,
            "items": [item["id"] for item in items],
        }
    )
    page_dir = session_dir / f"page-{page_number:06d}"
    page_dir.mkdir()
    packet = _render_packet_with_contexts(
        str(index["summary"]),
        items,
        continuation,
        [item["context"] for item in selected],
    )
    template = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "items": items,
    }
    internal = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "template": copy.deepcopy(template),
        "deferred_orphan": {
            "session_dir": session_dir.as_posix(),
            "session_identity": manifest["session_identity"],
            "page_number": page_number,
            "offset": offset,
            "summary": manifest["summary_path"],
        },
    }
    packet_path = page_dir / "review-packet.md"
    decision_path = page_dir / "review-decisions.json"
    packet_path.write_text(packet, encoding="utf-8")
    decision_path.write_text(
        json.dumps(template, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (page_dir / INTERNAL_FILENAME).write_bytes(_json_bytes(internal))
    manifest["current"] = {
        "continuation": continuation,
        "count": len(items),
        "offset": offset,
        "page_number": page_number,
    }
    _atomic_write(
        session_dir / DEFERRED_MANIFEST_FILENAME, _json_bytes(manifest)
    )
    return {
        "status": "review_required",
        "review_packet": packet_path.as_posix(),
        "decision_file": decision_path.as_posix(),
        "continuation": continuation,
        "session_identity": manifest["session_identity"],
        "item_count": len(items),
        "byte_count": len(packet.encode("utf-8")),
    }


def _create_deferred_orphan_exchange(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    controller_state: Mapping[str, Any],
    index: dict[str, Any],
) -> dict[str, Any]:
    """Create one append-only temporary session for a large orphan review."""

    session_dir = Path(
        tempfile.mkdtemp(prefix="research-log-validation-orphan-session-")
    )
    base = {
        "schema_version": DEFERRED_SESSION_SCHEMA_VERSION,
        "session_identity": index["session_identity"],
        "scan": scan,
        "adjudication": adjudication,
        "controller": copy.deepcopy(dict(controller_state)),
    }
    base_bytes = _json_bytes(base)
    index_bytes = _json_bytes(index)
    (session_dir / DEFERRED_BASE_FILENAME).write_bytes(base_bytes)
    (session_dir / DEFERRED_INDEX_FILENAME).write_bytes(index_bytes)
    manifest = {
        "schema_version": DEFERRED_SESSION_SCHEMA_VERSION,
        "session_identity": index["session_identity"],
        "summary": scan["summary"],
        "summary_path": (
            Path(scan["project_root"]) / str(scan["summary"])
        ).resolve().as_posix(),
        "base_sha256": _sha256(base_bytes),
        "index_sha256": _sha256(index_bytes),
        "total_items": len(index["items"]),
        "next_offset": 0,
        "next_page_number": 1,
        "fragments": [],
        "current": None,
    }
    return _deferred_page(session_dir, index, manifest)


def _verified_session_object(
    session_dir: Path, filename: str, expected_sha256: str
) -> dict[str, Any]:
    path = session_dir / filename
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ValidationToolError(
            f"deferred orphan session is incomplete: {path}: {exc}"
        ) from exc
    if _sha256(value) != expected_sha256:
        raise ValidationToolError(
            f"deferred orphan session file changed unexpectedly: {path}"
        )
    try:
        decoded = json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(
            f"deferred orphan session file is malformed: {path}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValidationToolError(
            f"deferred orphan session file must be an object: {path}"
        )
    return decoded


def _load_deferred_session(
    decisions: Mapping[str, Any], internal: Mapping[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], Any]:
    deferred = internal.get("deferred_orphan")
    if not isinstance(deferred, Mapping):
        raise ValidationToolError("review packet is not a deferred orphan page")
    session_dir = Path(str(deferred.get("session_dir", ""))).resolve()
    if not session_dir.name.startswith("research-log-validation-orphan-session-"):
        raise ValidationToolError("deferred orphan session path is invalid")
    manifest = _read_object(
        session_dir / DEFERRED_MANIFEST_FILENAME,
        "deferred orphan manifest",
    )
    session_identity = deferred.get("session_identity")
    current = manifest.get("current")
    if (
        manifest.get("schema_version") != DEFERRED_SESSION_SCHEMA_VERSION
        or manifest.get("session_identity") != session_identity
        or not isinstance(current, dict)
        or current.get("continuation") != decisions.get("continuation")
        or current.get("page_number") != deferred.get("page_number")
        or current.get("offset") != deferred.get("offset")
    ):
        raise ValidationToolError("deferred orphan review page is stale")
    base = _verified_session_object(
        session_dir,
        DEFERRED_BASE_FILENAME,
        str(manifest.get("base_sha256", "")),
    )
    index = _verified_session_object(
        session_dir,
        DEFERRED_INDEX_FILENAME,
        str(manifest.get("index_sha256", "")),
    )
    if (
        base.get("session_identity") != session_identity
        or index.get("session_identity") != session_identity
    ):
        raise ValidationToolError("deferred orphan session identity differs")
    return session_dir, manifest, base, index, session_identity


def _record_deferred_fragment(
    session_dir: Path,
    manifest: dict[str, Any],
    decisions: Mapping[str, Any],
    session_identity: Any,
) -> list[dict[str, Any]]:
    current = manifest["current"]
    page_number = int(current["page_number"])
    fragment = {
        "schema_version": DEFERRED_SESSION_SCHEMA_VERSION,
        "session_identity": session_identity,
        "page_number": page_number,
        "continuation": decisions["continuation"],
        "decisions": copy.deepcopy(dict(decisions)),
    }
    fragment_bytes = _json_bytes(fragment)
    fragment_name = f"accepted-{page_number:06d}.json"
    fragment_path = session_dir / fragment_name
    if fragment_path.exists():
        if fragment_path.read_bytes() != fragment_bytes:
            raise ValidationToolError(
                "deferred orphan page conflicts with its accepted fragment"
            )
    else:
        _atomic_write(fragment_path, fragment_bytes)
    fragments = list(manifest.get("fragments", []))
    expected_fragment = {
        "file": fragment_name,
        "sha256": _sha256(fragment_bytes),
        "item_count": len(decisions["items"]),
    }
    if expected_fragment not in fragments:
        fragments.append(expected_fragment)
    manifest["fragments"] = fragments
    manifest["next_offset"] = int(current["offset"]) + int(current["count"])
    manifest["next_page_number"] = page_number + 1
    manifest["current"] = None
    return fragments


def _merged_deferred_rows(
    session_dir: Path,
    fragments: list[dict[str, Any]],
    session_identity: Any,
) -> list[dict[str, Any]]:
    rows = []
    for retained in fragments:
        payload = _verified_session_object(
            session_dir,
            str(retained["file"]),
            str(retained["sha256"]),
        )
        if payload.get("session_identity") != session_identity:
            raise ValidationToolError("accepted orphan fragment has another session")
        fragment_rows = payload.get("decisions", {}).get("items", [])
        if not isinstance(fragment_rows, list):
            raise ValidationToolError("accepted orphan fragment has invalid items")
        rows.extend(copy.deepcopy(fragment_rows))
    return rows


def _deferred_orphan_fingerprints(
    index: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    fingerprints: dict[str, dict[str, str]] = {}
    for item in index["items"]:
        template = item["template"]
        fingerprints.setdefault(str(template["entry"]), {})[
            str(template["identity"])
        ] = str(item["fingerprint"])
    return fingerprints


def accept_deferred_orphan_page(
    decisions: Mapping[str, Any], internal: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one accepted page and return the next page or final state."""

    session_dir, manifest, base, index, session_identity = _load_deferred_session(
        decisions, internal
    )
    fragments = _record_deferred_fragment(
        session_dir, manifest, decisions, session_identity
    )
    if int(manifest["next_offset"]) < int(manifest["total_items"]):
        return _deferred_page(session_dir, index, manifest)
    _atomic_write(
        session_dir / DEFERRED_MANIFEST_FILENAME, _json_bytes(manifest)
    )
    rows = _merged_deferred_rows(session_dir, fragments, session_identity)
    if len(rows) != int(manifest["total_items"]):
        raise ValidationToolError("deferred orphan session is missing accepted items")
    return {
        "status": "ready",
        "session_dir": session_dir.as_posix(),
        "scan": base["scan"],
        "adjudication": base["adjudication"],
        "controller": base["controller"],
        "decisions": {
            "schema_version": EXCHANGE_SCHEMA_VERSION,
            "continuation": session_identity,
            "items": rows,
        },
        "orphan_fingerprints": _deferred_orphan_fingerprints(index),
    }


def finish_deferred_orphan_session(session_dir: Path) -> None:
    """Remove one completed temporary session after canonical publication."""

    resolved = session_dir.resolve()
    if not resolved.name.startswith("research-log-validation-orphan-session-"):
        raise ValidationToolError("refusing to remove an invalid review session")
    if (resolved / DEFERRED_MANIFEST_FILENAME).is_file():
        shutil.rmtree(resolved)


def create_exchange(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    controller_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one paired packet/template and private continuation state."""

    deferred_index = _deferred_orphan_index(scan, adjudication)
    if deferred_index is not None:
        return _create_deferred_orphan_exchange(
            scan, adjudication, controller_state, deferred_index
        )
    work_dir = Path(tempfile.mkdtemp(prefix="research-log-validation-review-"))
    items, orphan_fingerprints = _template_items(scan, adjudication)
    continuation = _continuation_identity(scan, adjudication, items)
    packet = _render_packet(scan, adjudication, items, continuation)
    template = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "items": items,
    }
    internal = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "template": copy.deepcopy(template),
        "scan": scan,
        "adjudication": adjudication,
        "orphan_fingerprints": orphan_fingerprints,
        "controller": copy.deepcopy(dict(controller_state)),
    }
    packet_path = work_dir / "review-packet.md"
    decision_path = work_dir / "review-decisions.json"
    packet_path.write_text(packet, encoding="utf-8")
    decision_path.write_text(
        json.dumps(template, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (work_dir / INTERNAL_FILENAME).write_text(
        json.dumps(internal, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "review_required",
        "review_packet": packet_path.as_posix(),
        "decision_file": decision_path.as_posix(),
        "continuation": continuation,
        "item_count": len(items),
        "byte_count": len(packet.encode("utf-8")),
    }


def _read_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationToolError(f"{description} must be a JSON object")
    return value


def _valid_collection_scope(
    row: Mapping[str, Any], decision: Any, internal: Mapping[str, Any]
) -> bool:
    if not (
        row.get("kind") == "collection_scope"
        and isinstance(decision, dict)
        and set(decision) == {"members"}
        and isinstance(decision.get("members"), dict)
    ):
        return False
    members = decision["members"]
    queue_item = next(
        (
            item
            for item in internal.get("adjudication", {}).get("review_queue", [])
            if item.get("kind") == "collection_scope"
            and item.get("entry") == row.get("entry")
            and item.get("identity") == row.get("identity")
        ),
        None,
    )
    expected_collections = (
        set(queue_item.get("collections", []))
        if isinstance(queue_item, dict)
        else set()
    )
    return set(members) == expected_collections and all(
        isinstance(values, list)
        and values
        and all(isinstance(value, str) and value for value in values)
        for values in members.values()
    )


def _validate_decision_row(
    row: Any,
    expected_row: Any,
    number: int,
    internal: Mapping[str, Any],
) -> None:
    if not isinstance(row, dict) or not isinstance(expected_row, dict):
        raise ValidationToolError(f"review decision {number} must be an object")
    invariant = {
        key: value
        for key, value in row.items()
        if key not in {"decision", "rationale"}
    }
    expected_invariant = {
        key: value
        for key, value in expected_row.items()
        if key not in {"decision", "rationale"}
    }
    if invariant != expected_invariant:
        raise ValidationToolError(
            f"review decision {number} modified CLI-owned fields"
        )
    decision = row.get("decision")
    if decision not in row.get("allowed_decisions", []) and not _valid_collection_scope(
        row, decision, internal
    ):
        raise ValidationToolError(
            f"review decision {number} is incomplete or not allowed"
        )
    rationale = row.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValidationToolError(
            f"review decision {number} requires a concise rationale"
        )


def load_decisions(decision_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate paired continuation identity and agent-editable fields."""

    decisions = _read_object(decision_path, "review decisions")
    internal = _read_object(decision_path.parent / INTERNAL_FILENAME, "continuation")
    if (
        decisions.get("schema_version") != EXCHANGE_SCHEMA_VERSION
        or internal.get("schema_version") != EXCHANGE_SCHEMA_VERSION
        or decisions.get("continuation") != internal.get("continuation")
    ):
        raise ValidationToolError("review decisions have a stale continuation identity")
    expected = internal.get("template")
    if not isinstance(expected, dict):
        raise ValidationToolError("continuation lacks its decision template")
    rows = decisions.get("items")
    expected_rows = expected.get("items")
    if not isinstance(rows, list) or not isinstance(expected_rows, list):
        raise ValidationToolError("review decisions items must be an array")
    if len(rows) != len(expected_rows):
        raise ValidationToolError("review decisions are incomplete or unexpected")
    for number, (row, expected_row) in enumerate(zip(rows, expected_rows), 1):
        _validate_decision_row(row, expected_row, number, internal)
    return decisions, internal


def _ordinary_action(row: Mapping[str, Any]) -> dict[str, Any] | None:
    decision = row["decision"]
    if decision == "needs_context":
        return None
    match = {
        "kind": row["kind"],
        "entry": row["entry"],
        "identity": row["identity"],
    }
    result: dict[str, Any] = {"match": match, "decision": "pass"}
    if row["kind"] == "semantic_provenance" and decision == "pass":
        result = {"match": match, "decision": "support", "candidate": 1}
    elif row["kind"] == "upstream_producer" and isinstance(decision, Mapping):
        result = {
            "match": match,
            "decision": "bind",
            "producer_bindings": copy.deepcopy(decision["bindings"]),
        }
    elif row["kind"] == "collection_scope" and isinstance(decision, Mapping):
        result = {
            "match": match,
            "decision": "pass",
            "members": copy.deepcopy(decision["members"]),
        }
    elif decision == "keep":
        result = {"match": match, "decision": "keep"}
    elif decision == "fail":
        result = {
            "match": match,
            "decision": "fail",
            "findings": {"Provenance": row["rationale"]},
        }
    return result


def decisions_to_actions(
    decisions: Mapping[str, Any], internal: Mapping[str, Any]
) -> dict[str, Any]:
    """Translate narrow template decisions into the internal action contract."""

    actions = []
    orphan_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in decisions["items"]:
        if row["kind"] == "orphan_candidate":
            orphan_rows.setdefault(str(row["entry"]), []).append(row)
            continue
        action = _ordinary_action(row)
        if action is not None and action not in actions:
            actions.append(action)
    fingerprints = internal.get("orphan_fingerprints", {})
    for entry, rows in orphan_rows.items():
        selected = [row for row in rows if row["decision"] != "needs_context"]
        if not selected:
            continue
        actions.append(
            {
                "match": {"kind": "orphan_candidates", "entry": entry},
                "decision": "orphan-batch",
                "candidate_fingerprints": {
                    row["identity"]: fingerprints[entry][row["identity"]]
                    for row in selected
                },
                "rationales": {
                    row["identity"]: row["rationale"] for row in selected
                },
                "unresolved": [
                    row["identity"]
                    for row in selected
                    if row["decision"] == "unresolved"
                ],
                "connected": [
                    row["identity"]
                    for row in selected
                    if row["decision"] == "connected"
                ],
                "retained": [
                    {
                        "identity": row["identity"],
                        "validation_note": str(row["decision"]).removeprefix(
                            "retain:"
                        ),
                    }
                    for row in selected
                    if str(row["decision"]).startswith("retain:")
                ],
            }
        )
    return {"schema_version": DECISION_SCHEMA_VERSION, "actions": actions}


def durable_review_judgments(
    decisions: Mapping[str, Any], decision_date: str
) -> list[dict[str, Any]]:
    """Return compact rationale-owning judgments for accepted template rows."""

    judgments = []
    for row in decisions["items"]:
        if row["decision"] == "needs_context":
            continue
        judgments.append(
            {
                "identity": row["id"],
                "kind": "review-decision",
                "result": (
                    row["decision"]
                    if isinstance(row["decision"], str)
                    else (
                        "scope"
                        if row["kind"] == "collection_scope"
                        else "bind"
                    )
                ),
                "decision": copy.deepcopy(row["decision"]),
                "decision_date": decision_date,
                "subject": {
                    "kind": row["kind"],
                    "entry": row["entry"],
                    "identity": row["identity"],
                },
                "rule_dependencies": {"semantic_review": 1},
                "input_dependencies": [],
                "rationale": row["rationale"],
                "rationale_provenance": "recorded",
                "provenance": "native-reviewed",
            }
        )
    return judgments


def reusable_review_actions(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    judgments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return actions for exact current questions with durable native answers."""

    reusable = {
        judgment["identity"]: judgment
        for judgment in judgments
        if judgment.get("kind") == "review-decision"
        and "decision" in judgment
        and judgment.get("rule_dependencies") == {"semantic_review": 1}
    }
    if not reusable:
        return {"schema_version": DECISION_SCHEMA_VERSION, "actions": []}

    rows: list[dict[str, Any]] = []
    orphan_fingerprints: dict[str, dict[str, str]] = {}
    for queue_item in adjudication["review_queue"]:
        if queue_item["kind"] != "orphan_candidates":
            templates = [_ordinary_template(queue_item)]
        else:
            candidates = queue_item.get("candidates", [])
            templates, fingerprints = _orphan_templates(
                scan, adjudication, queue_item, max(1, len(candidates))
            )
            orphan_fingerprints[str(queue_item["entry"])] = fingerprints
        for template in templates:
            judgment = reusable.get(template["id"])
            if judgment is None:
                continue
            rows.append(
                {
                    **template,
                    "decision": copy.deepcopy(judgment["decision"]),
                    "rationale": judgment["rationale"],
                }
            )
    return decisions_to_actions(
        {"schema_version": EXCHANGE_SCHEMA_VERSION, "items": rows},
        {"adjudication": adjudication, "orphan_fingerprints": orphan_fingerprints},
    )
