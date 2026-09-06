"""Bounded read-only access to published mechanical findings."""

from __future__ import annotations

import json
from typing import Any

from validation.filesystem import BoundedFileReadError, bounded_file_bytes
from validation.human_projection import FindingGroup, project_findings
from validation.mechanical_results import (
    GENERATED_RECORD_SCHEMA,
    CheckStatus,
    MechanicalGeneratedRecord,
    MechanicalResultContractError,
)

from .context import LogContext
from .model import ActionError

LIST_SCHEMA = "research-log-findings-list/1"
SHOW_SCHEMA = "research-log-finding/1"
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_RETURNED_GROUPS = 50


class _DuplicateJsonKey(ValueError):
    """Signal one duplicate key during strict JSON decoding."""


def list_findings(
    log: LogContext,
    *,
    entry: str | None,
    subject: str | None,
) -> dict[str, object]:
    """Return a bounded direct-finding inventory from the published record."""

    record = _load_record(log)
    groups = sorted(project_findings(record), key=_list_sort_key)
    matches = [
        group
        for group in groups
        if (entry is None or group.entry == entry)
        and (subject is None or group.subject == subject)
    ]
    returned = matches[:MAX_RETURNED_GROUPS]
    return {
        "filters": {"entry": entry, "subject": subject},
        "findings": [_list_item(group) for group in returned],
        "matched_groups": len(matches),
        "omitted_groups": len(matches) - len(returned),
        "result_date": record.result_date,
        "returned_groups": len(returned),
        "schema": LIST_SCHEMA,
        "summary": record.summary,
    }


def show_finding(log: LogContext, *, check_id: str) -> dict[str, object]:
    """Return one complete direct finding selected by stable check identity."""

    record = _load_record(log)
    matches = [check for check in record.checks if check.identity == check_id]
    if not matches:
        raise ActionError(
            "findings.id.unknown", f"published finding does not contain {check_id!r}"
        )
    if len(matches) > 1:
        raise ActionError(
            "findings.id.duplicate",
            f"published result contains duplicate identity {check_id!r}",
        )
    check = matches[0]
    if (
        check.status not in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}
        or check.failure is None
    ):
        raise ActionError(
            "findings.id.not_finding", f"{check_id!r} is not a direct finding"
        )
    group = next(
        group for group in project_findings(record) if check.identity in group.check_ids
    )
    return {
        "finding": {
            "code": check.failure.code,
            "dependencies": [dict(value) for value in check.dependencies],
            "entry": group.entry,
            "identity": check.identity,
            "observed": dict(check.failure.observed),
            "rule": check.failure.rule,
            "scope": check.scope.value,
            "status": check.status.value,
            "subject": group.subject,
        },
        "result_date": record.result_date,
        "schema": SHOW_SCHEMA,
        "summary": record.summary,
    }


def _load_record(log: LogContext) -> MechanicalGeneratedRecord:
    path = log.root / "validation" / "results.json"
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "findings.result.missing",
            f"no published mechanical result for {log.summary}",
        )
    try:
        raw = bounded_file_bytes(path, maximum_bytes=MAX_RESULT_BYTES)
    except BoundedFileReadError as error:
        raise ActionError(
            "findings.result.malformed",
            "published mechanical result cannot be read within its bound",
        ) from error
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise ActionError(
            "findings.result.malformed", "published mechanical result is malformed"
        ) from error
    if not isinstance(value, dict):
        raise ActionError(
            "findings.result.malformed", "published mechanical result must be an object"
        )
    schema = value.get("schema")
    if isinstance(schema, str) and schema != GENERATED_RECORD_SCHEMA:
        raise ActionError(
            "findings.result.schema_unsupported",
            f"unsupported published result schema: {schema!r}",
        )
    _reject_duplicate_identities(value)
    try:
        return MechanicalGeneratedRecord.from_dict(value)
    except MechanicalResultContractError as error:
        raise ActionError(
            "findings.result.malformed", "published mechanical result is malformed"
        ) from error


def _reject_duplicate_identities(value: dict[str, Any]) -> None:
    checks = value.get("checks")
    if not isinstance(checks, list):
        return
    identities = [
        check.get("identity")
        for check in checks
        if isinstance(check, dict) and isinstance(check.get("identity"), str)
    ]
    if len(identities) != len(set(identities)):
        raise ActionError(
            "findings.id.duplicate",
            "published result contains a duplicate check identity",
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _list_item(group: FindingGroup) -> dict[str, object]:
    return {
        "check_id": group.check_ids[0],
        "code": group.code,
        "entry": group.entry,
        "represented_checks": group.represented_checks,
        "subject": group.subject,
    }


def _list_sort_key(group: FindingGroup) -> tuple[object, ...]:
    return (
        group.entry is None,
        group.entry or "",
        group.code,
        group.subject,
        group.status.value,
        json.dumps(
            dict(group.observed),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        group.check_ids[0],
    )
