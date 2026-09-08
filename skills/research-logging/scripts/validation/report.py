"""Human-facing projections of mechanical validation results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .human_projection import (
    FindingGroup,
    ReportContext,
    area_results,
    project_findings,
)
from .mechanical_results import CheckScope, CheckStatus, MechanicalGeneratedRecord

AREA_NAMES = ("Structure", "Evidence", "Provenance", "Hygiene")
BATCH_AREA_NAMES = ("Structure", "Evidence", "Confirmation")
MAX_TARGETS_PER_GROUP = 10


@dataclass(frozen=True)
class ValidationBatchReportRow:
    """One discovered research log prepared for the batch report."""

    title: str
    summary: str
    human_report: str
    mechanical_report: str
    published: bool
    areas: Mapping[str, str]
    explanation: str | None = None


def compose_validation_report(
    record: MechanicalGeneratedRecord,
    *,
    context: ReportContext | None = None,
    groups: Sequence[FindingGroup] | None = None,
) -> str:
    """Render the generated human document for one completed result."""

    context = context or ReportContext.empty(Path(record.summary))
    groups = project_findings(record, context) if groups is None else tuple(groups)
    lines = [
        "# Validation",
        "",
        f"Validated: `{record.result_date}`",
        "",
        "## Mechanical Validation",
        "",
        *_area_table(area_results(record, groups)),
        "",
        "## Findings",
        "",
    ]
    lines.extend(_finding_lines(groups, context))
    return "\n".join(lines).rstrip() + "\n"


def compose_validation_command_report(
    record: MechanicalGeneratedRecord,
    *,
    context: ReportContext,
    published: bool,
    human_report: str,
    mechanical_report: str,
) -> str:
    """Render the ready-to-present one-log command report."""

    groups = project_findings(record, context)
    incomplete = any(check.status is CheckStatus.UNAVAILABLE for check in record.checks)
    opening = (
        f"Validation of {context.title} is incomplete."
        if incomplete
        else f"Validated {context.title}."
    )
    lines = [opening, "", *_area_table(area_results(record, groups)), ""]
    if incomplete:
        lines.extend(_incomplete_explanations(groups))
        lines.append("")
    lines.append(
        (
            "Reports: "
            + _markdown_link("Human", human_report)
            + " · "
            + _markdown_link("JSON", mechanical_report)
            if published
            else "Report: Not published."
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def compose_blocked_validation_report(title: str, explanation: str) -> str:
    """Render a ready-to-present result when validation could not start."""

    lines = [
        f"Validation of {title} could not start.",
        "",
        *_area_table({name: "—" for name in AREA_NAMES}),
        "",
        explanation.rstrip(".") + ".",
        "",
        "Report: Not published.",
    ]
    return "\n".join(lines) + "\n"


def compose_validation_batch_report(
    rows: Sequence[ValidationBatchReportRow],
) -> str:
    """Render one complete ready-to-present report for discovered logs."""

    lines = [
        "| Research log | Structure | Evidence | Confirmation | Report |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        report = (
            _markdown_link("Human", row.human_report)
            + " · "
            + _markdown_link("JSON", row.mechanical_report)
            if row.published
            else "Not published"
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_link(row.title, row.summary),
                    *(row.areas[name] for name in BATCH_AREA_NAMES),
                    report,
                )
            )
            + " |"
        )
    explanations = [
        f"{row.title}: {row.explanation.rstrip('.')}."
        for row in rows
        if row.explanation
    ]
    if explanations:
        lines.extend(("", *explanations))
    return "\n".join(lines).rstrip() + "\n"


def batch_area_results(
    record: MechanicalGeneratedRecord,
    projection: Mapping[str, object],
) -> Mapping[str, str]:
    """Return the chain-aware three-column cross-log summary."""

    chains = _batch_groups(projection, "chains")
    unresolved = _batch_groups(projection, "unresolved")
    structure_chains = {
        str(chain["chain_id"])
        for chain in chains
        if _group_has_structure(chain, status=CheckStatus.FAIL.value)
    }
    unassigned_structure_groups = sum(
        _group_has_structure(group, status=CheckStatus.FAIL.value)
        for group in unresolved
    )
    structure_incomplete = any(
        _group_has_structure(chain, status=CheckStatus.UNAVAILABLE.value)
        for chain in chains
    ) or any(
        _group_has_structure(group, status=CheckStatus.UNAVAILABLE.value)
        for group in unresolved
    )
    confirmation_commands = {
        producer
        for group in (*chains, *unresolved)
        for finding in _batch_findings(group)
        if (producer := _confirmation_producer(finding)) is not None
    }

    evidence_groups = [
        group
        for group in project_findings(record)
        if group.scope is CheckScope.EVIDENCE
    ]
    evidence_incomplete = any(
        group.status is CheckStatus.UNAVAILABLE for group in evidence_groups
    )
    evidence_count = sum(
        group.status is CheckStatus.FAIL for group in evidence_groups
    )
    return {
        "Structure": (
            "—"
            if structure_incomplete
            else _batch_structure_count(
                len(structure_chains), unassigned_structure_groups
            )
        ),
        "Evidence": "—" if evidence_incomplete else _batch_count(evidence_count),
        "Confirmation": _batch_count(len(confirmation_commands)),
    }


def _structure_finding(finding: Mapping[str, object]) -> bool:
    if finding.get("status") not in {
        CheckStatus.FAIL.value,
        CheckStatus.UNAVAILABLE.value,
    }:
        return False
    scope = finding.get("scope")
    if scope in {CheckScope.CONFORMANCE.value, CheckScope.ORPHAN.value}:
        return True
    return (
        scope == CheckScope.PROVENANCE.value
        and finding.get("code") != "provenance.output.unconfirmed"
    )


def _confirmation_producer(finding: Mapping[str, object]) -> str | None:
    if finding.get("code") != "provenance.output.unconfirmed":
        return None
    observed = finding.get("observed")
    if isinstance(observed, Mapping):
        producer = observed.get("producer")
        if isinstance(producer, str):
            return producer
    return None


def _group_has_structure(
    group: Mapping[str, object], *, status: str | None = None
) -> bool:
    return any(
        _structure_finding(finding)
        and (status is None or finding.get("status") == status)
        for finding in _batch_findings(group)
    )


def _batch_groups(
    projection: Mapping[str, object], family: str
) -> tuple[Mapping[str, object], ...]:
    value = projection.get(family)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _batch_findings(
    group: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    value = group.get("findings")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _batch_count(count: int) -> str:
    return "Clear" if count == 0 else str(count)


def _batch_structure_count(chain_count: int, unassigned_count: int) -> str:
    if unassigned_count == 0:
        return _batch_count(chain_count)
    if chain_count == 0:
        return f"{unassigned_count} unassigned"
    chain = "chain" if chain_count == 1 else "chains"
    return f"{chain_count} {chain} + {unassigned_count} unassigned"


def unavailable_explanation(record: MechanicalGeneratedRecord) -> str | None:
    """Return a concise aggregate explanation for an incomplete result."""

    groups = project_findings(record)
    unavailable = [group for group in groups if group.status is CheckStatus.UNAVAILABLE]
    if not unavailable:
        return None
    areas = []
    for group in unavailable:
        area = _area_name(group.scope)
        if area not in areas:
            areas.append(area)
    return "Validation was incomplete in " + ", ".join(areas)


def _finding_lines(groups: Sequence[FindingGroup], context: ReportContext) -> list[str]:
    if not groups:
        return ["No mechanical findings."]
    by_entry: dict[str | None, list[FindingGroup]] = defaultdict(list)
    for group in groups:
        by_entry[group.entry].append(group)
    lines: list[str] = []
    for entry, entry_groups in by_entry.items():
        if lines:
            lines.append("")
        lines.extend(_entry_finding_lines(entry, entry_groups, context))
    return lines


def _entry_finding_lines(
    entry: str | None,
    groups: Sequence[FindingGroup],
    context: ReportContext,
) -> list[str]:
    lines = [_entry_heading(entry, context), ""]
    by_type: dict[str, list[FindingGroup]] = defaultdict(list)
    for group in groups:
        by_type[group.presentation.name].append(group)
    for number, (name, issue_groups) in enumerate(by_type.items()):
        if number:
            lines.append("")
        lines.extend(_issue_type_lines(entry, name, issue_groups))
    return lines


def _issue_type_lines(
    entry: str | None,
    name: str,
    groups: Sequence[FindingGroup],
) -> list[str]:
    target_count = len(groups)
    lines = [
        f"#### {name} — {target_count} {_plural(target_count, 'target')}",
        "",
        *(_target_line(group) for group in groups[:MAX_TARGETS_PER_GROUP]),
    ]
    omitted = target_count - MAX_TARGETS_PER_GROUP
    if omitted > 0:
        command = "log findings list --path <log>"
        if entry is not None:
            command += f" --entry {entry}"
        lines.extend(
            (
                "",
                f"{omitted} more {_plural(omitted, 'target')} omitted. "
                f"Start bounded diagnosis with `{command}`.",
            )
        )
    return lines


def _target_line(group: FindingGroup) -> str:
    detail = f"{group.represented_checks} {_plural(group.represented_checks, 'check')}"
    if group.impacted_checks:
        detail += (
            f"; prevents {group.impacted_checks} dependent "
            f"{_plural(group.impacted_checks, 'check')}"
        )
    return f"- {_inline_code(group.subject)} — {group.presentation.sentence} {detail}."


def _entry_heading(entry: str | None, context: ReportContext) -> str:
    if entry is None:
        return "### Research log"
    presentation = context.entries.get(entry)
    if presentation is None:
        return f"### {_markdown_text(entry)}"
    label = f"{entry} — {presentation.title}"
    return f"### {_markdown_link(label, presentation.document)}"


def _incomplete_explanations(groups: Sequence[FindingGroup]) -> list[str]:
    lines = []
    for group in groups:
        if group.status is not CheckStatus.UNAVAILABLE:
            continue
        sentence = group.presentation.sentence.rstrip(".")
        lines.append(
            f"{_area_name(group.scope)}: {sentence} "
            f"Target: {_inline_code(group.subject)}."
        )
    return lines


def _area_table(areas: Mapping[str, str]) -> list[str]:
    lines = ["| Area | Result |", "| --- | --- |"]
    lines.extend(f"| {name} | {areas[name]} |" for name in AREA_NAMES)
    return lines


def _area_name(scope: CheckScope) -> str:
    return {
        CheckScope.CONFORMANCE: "Structure",
        CheckScope.EVIDENCE: "Evidence",
        CheckScope.PROVENANCE: "Provenance",
        CheckScope.ORPHAN: "Hygiene",
    }[scope]


def _markdown_link(label: str, target: str) -> str:
    return f"[{_markdown_text(label)}](<{target.replace('>', '%3E')}>)"


def _markdown_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _inline_code(value: str) -> str:
    delimiter = "`"
    while delimiter in value:
        delimiter += "`"
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"
