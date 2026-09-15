"""Shared saved-validation summary facts and compact human presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

TYPE_LABELS = (
    ("conformance", "Conformance"),
    ("evidence", "Evidence"),
    ("provenance", "Provenance"),
    ("orphan", "Orphans"),
)


def human_saved_at(value: object) -> str:
    """Render an exact saved timestamp as a concise UTC calendar date."""

    if value is None:
        return "—"
    instant = datetime.fromisoformat(str(value))
    if instant.tzinfo is None:
        raise ValueError("saved validation timestamp must include a timezone")
    utc = instant.astimezone(timezone.utc)
    return f"{utc:%b} {utc.day}"


def render_saved_summary(row: Mapping[str, object]) -> str:
    """Render one saved or unavailable row as a fixed-order field/value table.

    Missing counts remain unavailable rather than becoming zero. This is the
    complete summary body shared by single-log text show and validation.md;
    diagnostic navigation and errors belong to the invoking CLI, not reports.
    """

    counts = row.get("finding_counts_by_type")
    fields: list[tuple[str, object]] = [
        ("Log", row["log"]),
        ("Saved", human_saved_at(row.get("saved_at"))),
        ("Outcome", str(row["outcome"]).title()),
    ]
    fields.extend(
        (label, counts[key] if isinstance(counts, Mapping) else "—")
        for key, label in TYPE_LABELS
    )
    fields.extend(
        (
            ("Batches", row.get("batch_count", "—")),
            ("Blocked", row.get("blocked_check_count", "—")),
            ("Failed", row.get("failed_check_count", "—")),
        )
    )
    return "\n".join(
        (
            "| Field | Value |",
            "| --- | --- |",
            *(f"| {label} | {_table_cell(value)} |" for label, value in fields),
        )
    )


def _table_cell(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")
