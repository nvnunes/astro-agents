"""Readable, bounded presentation for validation inspection views."""

from __future__ import annotations

import json
import shlex
from typing import Any

from validation.inspection_store import InspectionError

from .inspection_queries import MAX_VIEW_BYTES


def _lines(value: Any, indent: str, command: str) -> list[str]:
    if isinstance(value, dict) and value.get("type") in {"collection", "value"}:
        kind = value["type"]
        flag = "--ref" if kind == "value" else "--collection"
        unit = "characters" if kind == "value" else "items"
        return [
            f"{indent}{value['count']} {unit}; "
            f"log results {kind} {command} {flag} {value['ref']}"
        ]
    if isinstance(value, dict):
        lines = []
        if {"result_id", "kind", "finished_at"} <= value.keys():
            command = (
                command.rsplit(" --id ", 1)[0]
                + " --id " + shlex.quote(value["result_id"])
            )
        lines.extend(_followups(value, indent, command))
        for key, item in value.items():
            if item is None:
                continue
            nested = isinstance(item, (dict, list))
            lines.append(f"{indent}{key}:" + ("" if nested else f" {item}"))
            if nested:
                lines.extend(_lines(item, indent + "  ", command))
        return lines
    if isinstance(value, list):
        return [line for item in value for line in _lines(item, indent, command)]
    return [indent + str(value)]


def _followups(value: dict[str, Any], indent: str, command: str) -> list[str]:
    """Supply exact queries for stored batch and rejected-command records."""
    lines = []
    if "batch_id" in value and "primary_finding_count" in value:
        lines.append(
            f"{indent}Inspect batch: log results batch {command} "
            f"--batch {shlex.quote(value['batch_id'])}"
        )
    if "starting_finding" in value:
        lines.append(
            f"{indent}Start inspection: log results finding {command} "
            f"--finding {shlex.quote(value['starting_finding'])}"
        )
    if value.get("status") == "rejected" and "declared_outputs" in value:
        identity = shlex.quote(str(value["identity"]))
        lines.append(
            f"{indent}Inspect rejected command: "
            f"log results command {command} --command {identity}"
        )
    return lines


def render_view(view: dict[str, Any]) -> str:
    """Render exact fields and actionable references without JSON punctuation."""
    log = shlex.quote(str(view.get("log", "LOG")))
    identity = shlex.quote(str(view.get("result_id", "RESULT_ID")))
    command = f"--path {log} --id {identity}"
    values = {key: value for key, value in view.items() if key != "schema"}
    text = "\n".join(_lines(values, "", command)) + "\n"
    if len(text.encode()) > MAX_VIEW_BYTES:
        raise InspectionError(
            "results.view.too_large", "request fewer items with --limit"
        )
    return text


def print_view(view: dict[str, Any], output_format: str) -> None:
    """Emit only the requested view, never text plus its complete JSON payload."""
    if output_format == "json":
        raw = json.dumps(view, ensure_ascii=False, sort_keys=True)
        if len(raw.encode()) > MAX_VIEW_BYTES:
            raise InspectionError(
                "results.view.too_large", "request fewer items with --limit"
            )
        print(raw)
    else:
        print(render_view(view), end="")
