"""Shared parsing and compact-status contracts for generated validation reports."""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional


class ReportContractError(RuntimeError):
    """Raised when generated validation-report text violates its contract."""


def _table_cells(line: str) -> list[str]:
    return [
        cell.strip().replace(r"\|", "|")
        for cell in re.split(r"(?<!\\)\|", line[1:-1])
    ]


def parse_markdown_rows(text: str) -> dict[str, Any]:
    """Parse detailed Summary and Entries tables from a generated report."""

    state: dict[str, Any] = {
        "mode": None,
        "current_entry": None,
        "summary": [],
        "entries": [],
        "entry_order": [],
        "entry_groups": {},
    }
    for line in text.splitlines():
        _parse_report_line(line, state)
    state.pop("mode")
    state.pop("current_entry")
    return state


def _parse_report_line(line: str, state: dict[str, Any]) -> None:
    if line.startswith("## ") and line not in {"## Summary", "## Entries"}:
        state.update({"mode": None, "current_entry": None})
    if line == "## Summary":
        state["mode"] = "summary"
        return
    if line == "## Entries":
        state["mode"] = "entry"
        return
    if state["mode"] == "entry" and line.startswith("### "):
        entry = line[4:].split(":", 1)[0]
        state["current_entry"] = entry
        state["entry_order"].append(entry)
        state["entry_groups"][entry] = []
    if not line.startswith("|") or line.startswith("| ---"):
        return
    if line.startswith(("| Statistic", "| Target", "| Scope")):
        return
    cells = _table_cells(line)
    if state["mode"] == "summary":
        state["summary"].append(cells)
    elif state["mode"] == "entry":
        state["entries"].append(cells)
        if state["current_entry"] is not None:
            state["entry_groups"][state["current_entry"]].append(cells)


def is_validation_date(value: str) -> bool:
    """Return whether a value is one canonical ISO validation date."""

    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None


def report_update_date(report_text: str) -> str:
    """Return the validated update date declared by a report."""

    match = re.search(
        r"^- Report-update date: `(\d{4}-\d{2}-\d{2})`$",
        report_text,
        re.MULTILINE,
    )
    if not match or not is_validation_date(match.group(1)):
        raise ReportContractError("validation report lacks a valid update date")
    return match.group(1)


def _counted(count: int, singular: str, plural: Optional[str] = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _summary_status(rows: Sequence[Sequence[str]], date: str) -> str:
    failed = sum(len(row) == 4 and row[3] == "`FAIL`" for row in rows)
    if not rows:
        return "`N/A`"
    if failed:
        return f"`FAIL` - {failed} of {_counted(len(rows), 'statistic')} failed"
    return f"{date} — {len(rows)} checked; 0 failures"


def _entry_status(rows: Sequence[Sequence[str]]) -> tuple[str, str]:
    failed = sum(len(row) == 6 and "`FAIL`" in row[2:4] for row in rows)
    if not rows:
        checked = "`N/A`"
    elif failed:
        checked = f"`FAIL` - {failed} of {_counted(len(rows), 'target')} failed"
    else:
        checked = f"{_counted(len(rows), 'target')} checked; 0 failures"

    reproduction = [row[4] for row in rows if len(row) == 6 and row[4] != "`N/A`"]
    reproduction_failed = reproduction.count("`FAIL`")
    reproduced = sum(is_validation_date(value) for value in reproduction)
    if not reproduction:
        reproduction_status = "`N/A`"
    elif reproduction_failed:
        reproduction_status = (
            f"`FAIL` - {reproduction_failed} of "
            f"{_counted(len(reproduction), 'eligible target')} failed"
        )
    elif not reproduced:
        reproduction_status = "`-`"
    else:
        reproduction_status = (
            f"{reproduced} of "
            f"{_counted(len(reproduction), 'eligible target')} reproduced"
        )
    return checked, reproduction_status


def status_summary(report_text: str) -> str:
    """Render the compact status projection for one complete report body."""

    parsed = parse_markdown_rows(report_text)
    date = report_update_date(report_text)
    lines = [
        "## Status Summary",
        "",
        f"- Report updated: `{date}`",
        f"- Summary statistics: {_summary_status(parsed['summary'], date)}",
    ]
    failed = any(
        len(row) in {4, 6} and "`FAIL`" in row[2:]
        for row in [*parsed["summary"], *parsed["entries"]]
    )
    if failed:
        lines.append("- Remediation queue: [Remediation](#remediation)")
    if parsed["entry_order"]:
        lines.extend(
            [
                "",
                "| Scope | Last checked | Integrity & Provenance | Reproducibility |",
                "| --- | --- | --- | --- |",
            ]
        )
        for entry_id in parsed["entry_order"]:
            checked, reproduction = _entry_status(parsed["entry_groups"][entry_id])
            lines.append(f"| {entry_id} | {date} | {checked} | {reproduction} |")
    return "\n".join(lines) + "\n"


def install_status_summary(report_text: str) -> str:
    """Insert or replace the generated status block after report provenance."""

    lines = report_text.rstrip().splitlines()
    starts = [index for index, line in enumerate(lines) if line == "## Status Summary"]
    if len(starts) > 1:
        raise ReportContractError(
            "validation report has duplicate Status Summary sections"
        )
    if starts:
        start = starts[0]
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith("## ")
            ),
            len(lines),
        )
        del lines[start:end]
        while start < len(lines) and not lines[start]:
            del lines[start]
    counts = [index for index, line in enumerate(lines) if line == "## Counts"]
    if len(counts) != 1:
        raise ReportContractError("validation report must contain one Counts section")
    body = "\n".join(lines) + "\n"
    status = status_summary(body).rstrip().splitlines()
    insertion = counts[0]
    lines[insertion:insertion] = [*status, ""]
    return "\n".join(lines).rstrip() + "\n"


def status_summary_fields(report_text: str) -> Mapping[str, Any]:
    """Return canonical compact-status values for lint and migration checks."""

    parsed = parse_markdown_rows(report_text)
    date = report_update_date(report_text)
    entries = []
    for entry_id in parsed["entry_order"]:
        checked, reproduction = _entry_status(parsed["entry_groups"][entry_id])
        entries.append((entry_id, date, checked, reproduction))
    return {
        "date": date,
        "summary": _summary_status(parsed["summary"], date),
        "entries": entries,
    }
