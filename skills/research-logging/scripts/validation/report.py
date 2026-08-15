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

    mode: Optional[str] = None
    current_entry: Optional[str] = None
    summary_rows: list[list[str]] = []
    entry_rows: list[list[str]] = []
    entry_order: list[str] = []
    entry_groups: dict[str, list[list[str]]] = {}
    for line in text.splitlines():
        if line == "## Summary":
            mode = "summary"
            continue
        if line == "## Entries":
            mode = "entry"
            continue
        if mode == "entry" and line.startswith("### "):
            current_entry = line[4:].split(":", 1)[0]
            entry_order.append(current_entry)
            entry_groups[current_entry] = []
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        if line.startswith(("| Statistic", "| Target", "| Scope")):
            continue
        cells = _table_cells(line)
        if mode == "summary":
            summary_rows.append(cells)
        elif mode == "entry":
            entry_rows.append(cells)
            if current_entry is not None:
                entry_groups[current_entry].append(cells)
    return {
        "summary": summary_rows,
        "entries": entry_rows,
        "entry_order": entry_order,
        "entry_groups": entry_groups,
    }


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
        lines.append(
            "- Remediation queue: [validation-failures.md](validation-failures.md)"
        )
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
