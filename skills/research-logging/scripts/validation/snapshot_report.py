"""Human report rendering from one saved canonical validation snapshot."""

from __future__ import annotations

import shlex
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence, cast

from .domain import Finding, ValidationSnapshot
from .report_context import CATALOG, load_report_context

_TYPE_LABELS = (
    ("conformance", "Conformance"),
    ("evidence", "Evidence"),
    ("provenance", "Provenance"),
    ("orphan", "Orphans"),
)


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
    """Render the durable report from the same findings and batches as queries."""

    context = _report_context(snapshot)
    counts = Counter(finding.type.value for finding in snapshot.findings)
    lines = [
        "# Validation",
        "",
        f"Saved: {snapshot.stored_at or snapshot.finished_at}",
        f"Outcome: {snapshot.outcome.value.title()}",
        f"Log: {context['title']}",
        "",
        "| Type | Findings |",
        "| --- | ---: |",
        *(f"| {label} | {counts[key]} |" for key, label in _TYPE_LABELS),
        f"| Batches | {len(snapshot.batches)} |",
        f"| Blocked | {len(snapshot.blocked_checks)} |",
        f"| Failed | {len(snapshot.failed_checks)} |",
        "",
    ]
    log_root = Path(snapshot.target.log).with_suffix("")
    if snapshot.blocked_checks:
        lines.extend(
            (
                "Inspect blocked checks with `log validate list blocked --path "
                f"{shlex.quote(str(log_root))}`.",
                "",
            )
        )
    if snapshot.failed_checks:
        lines.extend(
            (
                "Inspect failed checks with `log validate list failed --path "
                f"{shlex.quote(str(log_root))}`.",
                "",
            )
        )
    lines.extend(("## Findings", ""))
    if not snapshot.findings:
        lines.append("No mechanical findings.")
    presentations = cast(
        Mapping[str, Mapping[str, str]], context["presentations"]
    )
    for finding in snapshot.findings:
        presentation = presentations[finding.code]
        lines.extend(
            (
                f"### {presentation['name']}",
                "",
                presentation["sentence"],
                "",
                f"- Finding: `{finding.finding_id}`",
                f"- Type: {finding.type.value}",
                f"- Entry: {_entry_label(context, finding.entry)}",
                f"- Subject: {finding.subject}",
                "",
            )
        )
    lines.extend(("## Batches", ""))
    if not snapshot.batches:
        lines.append("No batches.")
    for batch in snapshot.batches:
        lines.extend(
            (
                f"### {batch.batch_id}",
                "",
                f"- Focus finding: `{batch.focus_finding_id}`",
                f"- Findings: {', '.join(f'`{item}`' for item in batch.finding_ids)}",
                f"- Repair entries: {', '.join(batch.repair_entries) or '—'}",
                f"- Context entries: {', '.join(batch.context_entries) or '—'}",
                f"- Rationale: {'; '.join(batch.rationale) or 'singleton finding'}",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


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


def _entry_label(context: Mapping[str, object], entry: str | None) -> str:
    if entry is None:
        return "—"
    entries = cast(Mapping[str, Mapping[str, str]], context["entries"])
    presentation = entries.get(entry)
    return entry if presentation is None else f"{entry} — {presentation['title']}"


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
