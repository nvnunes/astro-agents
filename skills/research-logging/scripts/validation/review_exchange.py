"""Bounded semantic question-and-decision exchange for target validation."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import DECISION_SCHEMA_VERSION
from .review_batches import OrphanBatchRequest, select_orphan_batch

EXCHANGE_SCHEMA_VERSION = 1
INTERNAL_FILENAME = ".continuation.json"
MAX_PACKET_ITEMS = 200


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


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


def _render_packet(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    items: list[dict[str, Any]],
    continuation: str,
) -> str:
    lines = [
        "# Validation Review Packet",
        "",
        f"- Log: `{scan['summary']}`",
        f"- Continuation: `{continuation}`",
        f"- Questions: {len(items)}",
        "- Edit only the paired template's decision and rationale fields.",
    ]
    for number, item in enumerate(items, 1):
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
                    _packet_context(adjudication, item),
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


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


def create_exchange(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    controller_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one paired packet/template and private continuation state."""

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
        rationale = row.get("rationale")
        if decision not in row.get("allowed_decisions", []):
            raise ValidationToolError(
                f"review decision {number} is incomplete or not allowed"
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValidationToolError(
                f"review decision {number} requires a concise rationale"
            )
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
    if row["kind"] == "semantic_provenance" and decision == "pass":
        return {"match": match, "decision": "support", "candidate": 1}
    if row["kind"] == "upstream_producer" and isinstance(decision, Mapping):
        return {
            "match": match,
            "decision": "bind",
            "producer_bindings": copy.deepcopy(decision["bindings"]),
        }
    if decision == "keep":
        return {"match": match, "decision": "keep"}
    if decision == "fail":
        return {
            "match": match,
            "decision": "fail",
            "findings": {"Provenance": row["rationale"]},
        }
    return {"match": match, "decision": "pass"}


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
        if action is not None:
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
                    else "bind"
                ),
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
