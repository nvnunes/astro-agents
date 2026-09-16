"""Agent-oriented text presentation of already bounded native inspection facts."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .model import ActionError
from .reproduction_domain import MAX_INSPECTION_BYTES


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").capitalize()


def _identity(value: Mapping[str, str]) -> str:
    if "cid" in value:
        return f"{value['entry']} / {value['cid']} / {value['execution_id']}"
    return f"{value['entry']} / {value['artifact']}"


def _lead(value: Mapping[str, Any]) -> str:
    label = _label(value["status"])
    explanation = value.get("explanation")
    reason = value.get("reason")
    if explanation:
        return f"{label} — {explanation}"
    return f"{label} — {_label(reason)}" if reason else label


def _section(label: str, value: object) -> list[str]:
    return [
        "",
        f"{label}:",
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
    ]


def _stream(label: str, value: Mapping[str, Any]) -> list[str]:
    lines = ["", f"{label}: {value['path'] or 'Not recorded'}"]
    if not value["available"]:
        lines.append(f"Unavailable ({value['reason']}).")
    else:
        lines.append(
            "Retained tail (truncated)."
            if value["truncated"]
            else "Complete retained stream."
        )
        lines.append(value["excerpt"])
    return lines


def _list_lines(value: Mapping[str, Any]) -> list[str]:
    lines = [
        f"Run: {value['run_id'] or '— (empty confirmed)'}",
        f"{_label(value['kind'])}: {value['matched']} matched; "
        f"{value['returned']} returned; {value['remaining']} remaining.",
    ]
    for row in value["items"]:
        lines.extend(
            ["", _identity(row["identity"]), _lead(row), row["detail_command"]]
        )
    if value["cursor"] is not None:
        lines.extend(["", f"Continuation cursor: {value['cursor']}"])
    return lines


def _detail_lines(value: Mapping[str, Any]) -> list[str]:
    lines = [
        _lead(value),
        f"Identity: {_identity(value['identity'])}",
        f"Run: {value['run_id']}",
    ]
    for problem in value["diagnoses"]:
        lines.extend(
            [
                "",
                f"{_label(problem['stage'])} / {problem['code']}: "
                f"{problem['explanation']}",
            ]
        )
        lines.extend(_section("Observed", problem["observed"]))
        lines.extend(_section("Diagnosis owner", problem["subject"]))
        if problem["locations"]:
            lines.extend(_section("Locations", problem["locations"]))
    for field in (
        "recipe",
        "entry_root",
        "project_root",
        "dependencies",
        "producer",
        "retained_path",
        "output",
        "result",
        "artifacts",
    ):
        if field in value:
            lines.extend(_section(_label(field), value[field]))
    for field in ("stdout", "stderr"):
        if field in value:
            lines.extend(_stream(_label(field), value[field]))
    for name, page in value.get("sections", {}).items():
        lines.extend(
            [
                "",
                f"{name}: {page['matched']} matched; {page['returned']} returned; "
                f"{page['remaining']} remaining.",
            ]
        )
        if page["next_command"]:
            lines.append("Next: " + page["next_command"])
    return lines


def render_inspection(value: Mapping[str, Any]) -> str:
    """Lead with the actionable outcome; preserve every supplied diagnostic scalar."""

    lines = _list_lines(value) if "items" in value else _detail_lines(value)
    text = "\n".join(lines) + "\n"
    if len(text.encode()) > MAX_INSPECTION_BYTES:
        raise ActionError(
            "reproduction.limit.exceeded",
            "Inspection text exceeds its fixed byte bound",
        )
    return text
