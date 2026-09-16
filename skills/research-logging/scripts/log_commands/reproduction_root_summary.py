"""Root discovery and compact tables over independently saved native targets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from validation.discovery import SummaryDiscoveryError, discover_summaries
from validation.summary import human_saved_at

from .context import LogContext
from .model import ActionError
from .reproduction_inspection import (
    bounded_response,
    inspection_summary,
    load_inspection,
)


def root_summary(root: Path) -> dict[str, object]:
    """Read available saved targets; missing/error coverage never becomes zeroes.

    Discovery establishes log pairs only. Each outcome remains owned by its
    immutable run; entry-target coverage is not promoted to whole-log coverage.
    """

    try:
        discovery = discover_summaries(root)
    except SummaryDiscoveryError as error:
        raise ActionError("reproduction.selector.invalid", str(error)) from error
    rows: list[dict[str, Any]] = []
    unavailable = []
    for value in cast(list[str], discovery["summaries"]):
        summary = Path(value)
        log = LogContext(summary, summary.with_suffix(""))
        try:
            rows.append(inspection_summary(load_inspection(log)))
        except ActionError as error:
            unavailable.append(
                {"log": str(log.root), "code": error.code, "message": str(error)}
            )
    return bounded_response(
        {
            "root": discovery["root"],
            "logs": rows,
            "unavailable": unavailable,
            "coverage": {
                "discovered": len(rows) + len(unavailable),
                "available": len(rows),
                "unavailable": len(unavailable),
                "entry_targets": sum(row["target"]["kind"] == "entry" for row in rows),
            },
        }
    )


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _row(values: list[object]) -> str:
    cells = [_cell(value) for value in values]
    return "|" + "".join(f" {cell} |" if cell else " |" for cell in cells)


def _command_values(row: Mapping[str, Any]) -> list[int]:
    counts = row["commands"]
    selected = counts["selected"]
    return [
        counts["total"],
        counts["not_run"]["total"],
        counts["skipped_by_policy"],
        selected["succeeded"],
        selected["failed"]["total"],
        selected["blocked"]["total"],
    ]


def _artifact_values(row: Mapping[str, Any]) -> list[int]:
    counts = row["artifacts"]
    return [
        counts["total"],
        counts["matched"],
        counts["not_matched"],
        counts["not_compared"]["total"],
    ]


def render_root_summary(value: Mapping[str, Any]) -> str:
    """Render separate command/artifact tables, aggregating available coverage only."""

    rows = value["logs"]
    unavailable = value["unavailable"]
    lines = [
        "| Log | Saved | Target | Total | Not run | Policy | "
        "Succeeded | Failed | Blocked |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    command_totals = [0] * 6
    artifact_totals = [0] * 4
    for row in rows:
        target = "Log" if row["target"]["kind"] == "log" else row["target"]["entry"]
        counts = _command_values(row)
        lines.append(
            _row(
                [
                    Path(row["log"]).name,
                    human_saved_at(row["saved_at"]),
                    target,
                    *counts,
                ]
            )
        )
        command_totals = [total + count for total, count in zip(command_totals, counts)]
    for row in unavailable:
        lines.append(_row([Path(row["log"]).name, *(["—"] * 8)]))
    lines.append(_row(["Total", "", "", *command_totals]))
    lines.extend(
        [
            "",
            "| Log | Total | Matched | Not matched | Not compared |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        counts = _artifact_values(row)
        lines.append(_row([Path(row["log"]).name, *counts]))
        artifact_totals = [
            total + count for total, count in zip(artifact_totals, counts)
        ]
    for row in unavailable:
        lines.append(_row([Path(row["log"]).name, *(["—"] * 4)]))
    lines.append(_row(["Total", *artifact_totals]))
    coverage = value["coverage"]
    if coverage["unavailable"] or coverage["entry_targets"]:
        lines.extend(
            [
                "",
                "Totals cover only available recorded targets, "
                "including entry-targeted runs.",
            ]
        )
    for row in unavailable:
        lines.append(f"{Path(row['log']).name}: {row['code']}: {row['message']}")
    return "\n".join(lines) + "\n"
