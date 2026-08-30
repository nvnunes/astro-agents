"""Human-facing projection of generated validation operation records."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence

from .mechanical_results import (
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
        "| Scope | Status | Pass | Fail | Unavailable | Not applicable | Total |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scope in record.scopes:
        lines.append(
            "| "
            + " | ".join(
                (
                    scope.scope.value,
                    f"`{scope.status.value}`",
                    str(scope.counts[CheckStatus.PASS.value]),
                    str(scope.counts[CheckStatus.FAIL.value]),
                    str(scope.counts[CheckStatus.UNAVAILABLE.value]),
                    str(scope.counts[CheckStatus.NOT_APPLICABLE.value]),
                    str(scope.checks),
                )
            )
            + " |"
        )
    lines.extend(("", "### Non-passing checks", ""))
    findings = [
        check for check in record.checks if check.status is not CheckStatus.PASS
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
