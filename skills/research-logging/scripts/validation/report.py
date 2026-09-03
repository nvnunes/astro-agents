"""Human-facing projection of generated validation operation records."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

from .mechanical_results import (
    CheckScope,
    CheckStatus,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)

ENTRY_RE = re.compile(r"(?<![A-Za-z0-9])(e[0-9]+[a-z]?)(?![A-Za-z0-9])", re.I)


def compose_validation_report(
    record: MechanicalGeneratedRecord,
    *,
    reproduction_section: str | None = None,
) -> str:
    """Render the shared report from authoritative operation records.

    Pass 8 supplies no reproduction record, so an absent reproduction section
    renders the explicit not-yet-run state. Phase 3 may supply that section
    through this compositor without changing mechanical record ownership.
    """

    lines = [
        "# Validation",
        "",
        "## Mechanical Validation",
        "",
        f"Completion: `{record.completion.value}`",
        f"Date: `{record.result_date}`",
        "",
        "### Counts",
        "",
        "| Scope | Unit | Status | Pass | Fail | Unavailable | "
        "Not applicable | Total |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scope in record.scopes:
        counts = scope.counts
        total = scope.checks
        unit = "checks"
        label = scope.scope.value
        if scope.scope is CheckScope.CONFORMANCE:
            label = "structure"
        elif scope.scope is CheckScope.PROVENANCE:
            counts = provenance_artifact_counts(record)
            total = sum(counts.values())
            unit = "artifacts"
        elif scope.scope is CheckScope.ORPHAN:
            counts = hygiene_finding_counts(record)
            total = sum(counts.values())
            unit = "findings"
            label = "hygiene"
        if total:
            status = (
                _provenance_status_from_counts(counts)
                if scope.scope is CheckScope.PROVENANCE
                else _status_from_counts(counts)
            )
            displayed_status = f"`{status.value}`"
        else:
            displayed_status = ""
        lines.append(
            "| "
            + " | ".join(
                (
                    label,
                    unit,
                    displayed_status,
                    str(counts[CheckStatus.PASS.value]),
                    str(counts[CheckStatus.FAIL.value]),
                    str(counts[CheckStatus.UNAVAILABLE.value]),
                    str(counts[CheckStatus.NOT_APPLICABLE.value]),
                    str(total),
                )
            )
            + " |"
        )
    lines.extend(("", "### Non-passing checks", ""))
    findings = [
        check
        for check in record.checks
        if check.status is not CheckStatus.PASS
        and check.scope is not CheckScope.ORPHAN
    ]
    if not findings:
        lines.append("None.")
    else:
        grouped: dict[str, list[MechanicalCheck]] = defaultdict(list)
        for check in findings:
            grouped[_entry_group(check)].append(check)
        for entry in sorted(grouped, key=lambda value: (value == "Log", value)):
            lines.extend((f"#### {entry}", ""))
            lines.extend(_check_lines(grouped[entry]))
    lines.extend(("", "## Reproduction", ""))
    if reproduction_section is None:
        lines.extend(
            (
                "Status: `not_yet_run`",
                "",
                "No reproduction audit has been run.",
            )
        )
    else:
        lines.append(reproduction_section.strip())
    return "\n".join(lines).rstrip() + "\n"


def provenance_artifact_counts(
    record: MechanicalGeneratedRecord,
) -> dict[str, int]:
    """Count unique provenance starting artifacts by their worst check status."""

    failure_affected_checks = _provenance_failure_affected_checks(record.checks)
    artifacts: dict[str, set[CheckStatus]] = defaultdict(set)
    for check in record.checks:
        if check.scope is not CheckScope.PROVENANCE:
            continue
        for artifact in _check_artifacts(check):
            artifacts[artifact].add(
                _provenance_artifact_status(check, failure_affected_checks)
            )
    counts = {status.value: 0 for status in CheckStatus}
    for statuses in artifacts.values():
        counts[_aggregate_provenance_artifact_status(statuses).value] += 1
    return counts


def hygiene_finding_counts(record: MechanicalGeneratedRecord) -> dict[str, int]:
    """Count distinct machine-readable hygiene findings by status."""

    counts = {status.value: 0 for status in CheckStatus}
    for check in record.checks:
        if check.scope is CheckScope.ORPHAN and check.failure is not None:
            counts[check.status.value] += 1
    return counts


def _status_from_counts(counts: Mapping[str, int]) -> CheckStatus:
    statuses = {status for status in CheckStatus if counts.get(status.value, 0) > 0}
    return _aggregate_status(statuses)


def _provenance_status_from_counts(counts: Mapping[str, int]) -> CheckStatus:
    statuses = {status for status in CheckStatus if counts.get(status.value, 0) > 0}
    return _aggregate_provenance_artifact_status(statuses)


def _check_artifacts(check: MechanicalCheck) -> set[str]:
    artifacts: set[str] = set()
    for dependency in check.dependencies:
        values = dependency.get("artifacts")
        if isinstance(values, list):
            artifacts.update(
                value for value in values if isinstance(value, str) and value
            )
    return artifacts


def _check_dependencies(check: MechanicalCheck) -> set[str]:
    dependencies: set[str] = set()
    for dependency in check.dependencies:
        value = dependency.get("dependency")
        if isinstance(value, str) and value:
            dependencies.add(value)
    return dependencies


def _provenance_failure_affected_checks(
    checks: Sequence[MechanicalCheck],
) -> set[str]:
    """Return provenance checks that fail directly or through prerequisites."""

    affected = {
        check.identity
        for check in checks
        if check.scope is CheckScope.PROVENANCE
        and check.status is CheckStatus.FAIL
        and (
            check.failure is None
            or check.failure.code != "provenance.output.unconfirmed"
        )
    }
    pending = [
        check
        for check in checks
        if check.scope is CheckScope.PROVENANCE
        and check.status is CheckStatus.NOT_APPLICABLE
    ]
    while pending:
        next_pending: list[MechanicalCheck] = []
        changed = False
        for check in pending:
            if _check_dependencies(check) & affected:
                affected.add(check.identity)
                changed = True
            else:
                next_pending.append(check)
        if not changed:
            break
        pending = next_pending
    return affected


def _provenance_artifact_status(
    check: MechanicalCheck,
    failure_affected_checks: set[str],
) -> CheckStatus:
    if (
        check.failure is not None
        and check.failure.code == "provenance.output.unconfirmed"
    ):
        return CheckStatus.UNAVAILABLE
    if (
        check.status is CheckStatus.NOT_APPLICABLE
        and check.identity in failure_affected_checks
    ):
        return CheckStatus.FAIL
    return check.status


def _aggregate_provenance_artifact_status(
    statuses: set[CheckStatus],
) -> CheckStatus:
    for status in (
        CheckStatus.FAIL,
        CheckStatus.UNAVAILABLE,
        CheckStatus.PASS,
    ):
        if status in statuses:
            return status
    return CheckStatus.NOT_APPLICABLE


def _aggregate_status(statuses: set[CheckStatus]) -> CheckStatus:
    for status in (
        CheckStatus.UNAVAILABLE,
        CheckStatus.FAIL,
        CheckStatus.PASS,
    ):
        if status in statuses:
            return status
    return CheckStatus.NOT_APPLICABLE


def _entry_group(check: MechanicalCheck) -> str:
    for value in (check.identity, check.subject):
        match = ENTRY_RE.search(value)
        if match is not None:
            return match.group(1).lower()
    return "Log"


def _check_lines(checks: Sequence[MechanicalCheck]) -> list[str]:
    lines: list[str] = []
    for check in checks:
        failure = check.failure
        title = failure.code if failure is not None else check.identity
        lines.extend(
            (
                f"- `{title}`",
                f"  - Status: `{check.status.value}`",
                f"  - Check: `{check.identity}`",
                f"  - Subject: `{check.subject}`",
            )
        )
        if check.dependencies:
            dependencies = json.dumps(
                [dict(item) for item in check.dependencies],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            lines.append(f"  - Dependencies: `{dependencies}`")
        if failure is not None:
            observed = json.dumps(
                dict(failure.observed),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            lines.extend(
                (
                    f"  - Observed: `{observed}`",
                    f"  - Violated rule: {failure.rule}",
                )
            )
            if failure.dependency is not None:
                lines.append(f"  - Dependency: `{failure.dependency}`")
    return lines
