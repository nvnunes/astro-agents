"""Bounded, lock-free access to published command-chain findings."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from validation.result_storage import (
    list_finding_groups,
    load_direct_finding,
    load_finding_group,
)

from .context import LogContext
from .model import ActionError

LIST_SCHEMA = "research-log-findings-list/2"
BATCH_SCHEMA = "research-log-findings-batch/1"
SHOW_SCHEMA = "research-log-finding/1"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class FindingFilters:
    entries: Sequence[str] = ()
    areas: Sequence[str] = ()
    codes: Sequence[str] = ()
    families: Sequence[str] = ()
    subjects: Sequence[str] = ()
    commands: Sequence[str] = ()


def _filters(filters: FindingFilters) -> dict[str, list[str]]:
    return {
        "area": sorted(set(filters.areas)),
        "code": sorted(set(filters.codes)),
        "command": sorted(set(filters.commands)),
        "entry": sorted(set(filters.entries)),
        "family": sorted(set(filters.families)),
        "subject": sorted(set(filters.subjects)),
    }


def list_findings(
    log: LogContext, *, filters: FindingFilters = FindingFilters()
) -> dict[str, object]:
    selected = _filters(filters)
    try:
        stored, meta, groups = list_finding_groups(log.root, filters=selected)
    except Exception as error:
        raise ActionError(
            "findings.result.missing",
            f"no cached mechanical result; run full validation for {log.summary}",
        ) from error
    chains = [_summary(group, selected) for group in groups]
    result = {
        "chains": chains,
        "filters": selected,
        "matched_chains": len(groups),
        "matched_entries": len({str(group["entry"]) for group in groups}),
        "matched_findings": sum(_matching_count(value) for value in chains),
        "validation_id": stored.validation_id,
        "result_date": meta["result_date"],
        "schema": LIST_SCHEMA,
        "summary": meta["summary"],
    }
    _bound(result)
    return result


def batch_findings(
    log: LogContext, *, validation_id: str, entry: str, chain_id: str
) -> dict[str, object]:
    try:
        stored, meta, group = load_finding_group(
            log.root, entry=entry, group_id=chain_id
        )
    except Exception as error:
        raise ActionError(
            "findings.result.missing",
            f"no cached mechanical result; run full validation for {log.summary}",
        ) from error
    if validation_id != stored.validation_id:
        raise ActionError(
            "findings.validation_superseded",
            f"requested validation {validation_id!r}; current published "
            f"validation is {stored.validation_id!r}",
        )
    if group is None:
        raise ActionError("findings.chain.unknown", f"unknown batch {entry}:{chain_id}")
    result = {
        "batch": group,
        "validation_id": stored.validation_id,
        "result_date": meta["result_date"],
        "schema": BATCH_SCHEMA,
        "summary": meta["summary"],
    }
    _bound(result)
    return result


def show_finding(log: LogContext, *, check_id: str) -> dict[str, object]:
    try:
        check, finding, meta = load_direct_finding(log.root, check_id)
    except Exception as error:
        raise ActionError(
            "findings.result.missing", "no cached mechanical result"
        ) from error
    if check is None:
        raise ActionError(
            "findings.id.unknown", f"published finding does not contain {check_id!r}"
        )
    if finding is None:
        raise ActionError(
            "findings.id.not_finding", f"{check_id!r} is not a direct finding"
        )
    return {
        "finding": {
            "code": finding["code"],
            "dependencies": finding["dependencies"],
            "entry": finding["display_entry"],
            "identity": check_id,
            "observed": json.loads(str(finding["observed_json"])),
            "rule": finding["rule"],
            "scope": finding["scope"],
            "status": finding["status"],
            "subject": finding["display_subject"],
        },
        "result_date": meta["result_date"],
        "schema": SHOW_SCHEMA,
        "summary": meta["summary"],
    }


def _matches(finding: Mapping[str, object], filters: Mapping[str, list[str]]) -> bool:
    code = str(finding.get("code", ""))
    return (
        (not filters["area"] or finding.get("scope") in filters["area"])
        and (not filters["code"] or code in filters["code"])
        and (
            not filters["family"]
            or any(code == x or code.startswith(x + ".") for x in filters["family"])
        )
        and (not filters["subject"] or finding.get("subject") in filters["subject"])
    )


def _summary(
    group: Mapping[str, object], filters: Mapping[str, list[str]]
) -> dict[str, object]:
    findings = group["findings"]
    assert isinstance(findings, list)
    selected = [
        item
        for item in findings
        if isinstance(item, Mapping) and _matches(item, filters)
    ]
    if not any(filters[key] for key in ("area", "code", "family", "subject")):
        selected = findings
    commands = group.get("commands", [])
    result = {
        "chain_id": group["chain_id"],
        "code_distribution": dict(
            sorted(Counter(str(item["code"]) for item in findings).items())
        ),
        "command_count": len(commands) if isinstance(commands, list) else 0,
        "entry": group["entry"],
        "finding_count": len(findings),
        "matching_findings": len(selected),
        "represented_checks": len(findings),
        "subjects": sorted({str(item["subject"]) for item in findings}),
    }
    if group.get("signals"):
        result["signals"] = group["signals"]
    if group.get("reason"):
        result["unresolved"] = group["reason"]
    return result


def _matching_count(value: Mapping[str, object]) -> int:
    count = value.get("matching_findings")
    return count if isinstance(count, int) else 0


def _bound(result: Mapping[str, object]) -> None:
    if (
        len(
            json.dumps(
                result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        )
        > MAX_RESPONSE_BYTES
    ):
        raise ActionError(
            "findings.response.too_large",
            "matching validation exceeds the response bound",
        )
