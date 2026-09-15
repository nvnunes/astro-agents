"""Human report rendering from one saved canonical validation snapshot."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence, cast

from .domain import Finding, ValidationSnapshot
from .report_context import CATALOG, load_report_context
from .summary import TYPE_LABELS, render_saved_summary


class SnapshotReportError(ValueError):
    """Raised when a saved snapshot lacks its closed human presentation context."""


def build_snapshot_report_context(
    summary: Path,
    findings: Sequence[Finding],
) -> Mapping[str, object]:
    """Capture the bounded human labels required to render one saved snapshot."""

    context = load_report_context(summary)
    presentations: dict[str, object] = {}
    for finding in findings:
        presentation = CATALOG.get(finding.code)
        if presentation is None:
            raise SnapshotReportError(
                f"finding code has no human presentation: {finding.code}"
            )
        presentations[finding.code] = {
            "name": presentation.name,
            "sentence": presentation.sentence,
            "target_kind": presentation.target_kind,
        }
    return {
        "entries": {
            entry_id: {
                "document": entry.document,
                "title": entry.title,
            }
            for entry_id, entry in sorted(context.entries.items())
        },
        "presentations": presentations,
        "title": context.title,
    }


def compose_snapshot_report(snapshot: ValidationSnapshot) -> str:
    """Render only the shared saved summary beneath the Validation heading."""

    _report_context(snapshot)
    counts = Counter(finding.type.value for finding in snapshot.findings)
    row = {
        "log": str(Path(snapshot.target.log).with_suffix("")),
        "saved_at": snapshot.stored_at or snapshot.finished_at,
        "outcome": snapshot.outcome.value,
        "finding_counts_by_type": {key: counts[key] for key, _ in TYPE_LABELS},
        "batch_count": len(snapshot.batches),
        "blocked_check_count": len(snapshot.blocked_checks),
        "failed_check_count": len(snapshot.failed_checks),
    }
    return "# Validation\n\n" + render_saved_summary(row) + "\n"


def validate_snapshot_report_context(snapshot: ValidationSnapshot) -> None:
    """Reject a snapshot whose saved human presentation packet is incomplete."""

    _report_context(snapshot)


def finding_presentation(
    context: Mapping[str, object], code: str
) -> Mapping[str, str]:
    """Return one presentation after validating the complete saved context."""

    normalized = _normalize_report_context(context, {code})
    presentations = cast(
        Mapping[str, Mapping[str, str]], normalized["presentations"]
    )
    return presentations[code]


def _report_context(snapshot: ValidationSnapshot) -> Mapping[str, object]:
    return _normalize_report_context(
        snapshot.report_context, {finding.code for finding in snapshot.findings}
    )


def _normalize_report_context(
    context: Mapping[str, object], required_codes: set[str]
) -> Mapping[str, object]:
    _exact_fields(context, {"entries", "presentations", "title"}, "report context")
    title = _text(context.get("title"), "report title")
    entries = _object(context.get("entries"), "report entries")
    presentations = _object(context.get("presentations"), "report presentations")
    normalized_entries: dict[str, Mapping[str, str]] = {}
    for entry_id, raw in entries.items():
        item = _object(raw, "report entry")
        _exact_fields(item, {"document", "title"}, "report entry")
        normalized_entries[entry_id] = {
            "document": _text(item.get("document"), "entry document"),
            "title": _text(item.get("title"), "entry title"),
        }
    normalized_presentations: dict[str, Mapping[str, str]] = {}
    for code, raw in presentations.items():
        item = _object(raw, "finding presentation")
        _exact_fields(
            item,
            {"name", "sentence", "target_kind"},
            "finding presentation",
        )
        normalized_presentations[code] = {
            key: _text(item.get(key), f"finding presentation {key}")
            for key in ("name", "sentence", "target_kind")
        }
    missing = required_codes - set(normalized_presentations)
    if missing:
        raise SnapshotReportError(
            f"snapshot report context is missing finding codes: {sorted(missing)}"
        )
    return {
        "entries": normalized_entries,
        "presentations": normalized_presentations,
        "title": title,
    }


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise SnapshotReportError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotReportError(f"{name} must be a nonempty string")
    return value


def _exact_fields(
    value: Mapping[str, object], required: set[str], name: str
) -> None:
    if set(value) != required:
        raise SnapshotReportError(f"{name} fields are invalid")
