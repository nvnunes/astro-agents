"""Lock-free bounded access to published command-chain findings."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_log_paths import VALIDATION_BATCHES, VALIDATION_RESULTS
from validation.batch_projection import PROJECTION_SCHEMA
from validation.filesystem import BoundedFileReadError, bounded_file_bytes
from validation.human_projection import project_findings
from validation.mechanical_results import (
    GENERATED_RECORD_SCHEMA,
    CheckStatus,
    MechanicalGeneratedRecord,
    MechanicalResultContractError,
)
from validation.repair_batch_contract import valid_repair_projection

from .context import LogContext
from .model import ActionError

LIST_SCHEMA = "research-log-findings-list/2"
BATCH_SCHEMA = "research-log-findings-batch/1"
SHOW_SCHEMA = "research-log-finding/1"
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_PROJECTION_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class _DuplicateJsonKey(ValueError):
    """Signal one duplicate key during strict JSON decoding."""


@dataclass(frozen=True)
class FindingFilters:
    """Repeatable exact selectors for one published finding query."""

    entries: Sequence[str] = ()
    areas: Sequence[str] = ()
    codes: Sequence[str] = ()
    families: Sequence[str] = ()
    subjects: Sequence[str] = ()
    commands: Sequence[str] = ()


def list_findings(
    log: LogContext,
    *,
    filters: FindingFilters = FindingFilters(),
) -> dict[str, object]:
    """Return one complete summary per selected command chain."""

    record = _load_record(log)
    projection = load_batch_projection(log, record=record)
    selected = {
        "area": sorted(set(filters.areas)),
        "code": sorted(set(filters.codes)),
        "command": sorted(set(filters.commands)),
        "entry": sorted(set(filters.entries)),
        "family": sorted(set(filters.families)),
        "subject": sorted(set(filters.subjects)),
    }
    chains = [value for value in _all_groups(projection) if _matches(value, selected)]
    summaries = [_summary(value, selected) for value in chains]
    result: dict[str, object] = {
        "chains": summaries,
        "filters": selected,
        "matched_chains": len(chains),
        "matched_entries": len({str(value["entry"]) for value in chains}),
        "matched_findings": sum(_matching_count(value) for value in summaries),
        "validation_id": projection["validation_id"],
        "result_date": record.result_date,
        "schema": LIST_SCHEMA,
        "summary": record.summary,
    }
    _require_response_bound(result, chains)
    return result


def batch_findings(
    log: LogContext, *, validation_id: str, entry: str, chain_id: str
) -> dict[str, object]:
    """Return one complete recorded chain and every attached direct finding."""

    record = _load_record(log)
    projection = load_batch_projection(log, record=record)
    current_id = projection["validation_id"]
    if validation_id != current_id:
        raise ActionError(
            "findings.validation_superseded",
            f"requested validation {validation_id!r}; "
            f"current published validation is {current_id!r}",
        )
    matches = [
        value
        for value in _all_groups(projection)
        if value.get("entry") == entry and value.get("chain_id") == chain_id
    ]
    if not matches:
        raise ActionError("findings.chain.unknown", f"unknown batch {entry}:{chain_id}")
    if len(matches) != 1:
        raise ActionError("findings.validation.malformed", "duplicate batch identity")
    result = {
        "batch": dict(matches[0]),
        "validation_id": current_id,
        "result_date": record.result_date,
        "schema": BATCH_SCHEMA,
        "summary": record.summary,
    }
    _require_response_bound(result, matches)
    return result


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


def load_batch_projection(
    log: LogContext, *, record: MechanicalGeneratedRecord | None = None
) -> dict[str, object]:
    """Load the one current strict projection without reconstructing it."""

    if record is None:
        record = _load_record(log)
    path = log.root / VALIDATION_BATCHES
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "findings.validation_unavailable",
            "cached validation batches are missing; full validation is required",
        )
    value = _read_json(
        path, maximum_bytes=MAX_PROJECTION_BYTES, label="published validation"
    )
    required = {
        "chains",
        "validation_id",
        "repair_batches",
        "record_identity",
        "result_date",
        "rules_version",
        "schema",
        "source_identity",
        "summary",
        "unresolved",
    }
    if (
        set(value) != required
        or value.get("schema") != PROJECTION_SCHEMA
        or not isinstance(value.get("chains"), list)
        or not isinstance(value.get("unresolved"), list)
        or not isinstance(value.get("source_identity"), str)
        or value.get("summary") != record.summary
        or value.get("result_date") != record.result_date
        or value.get("rules_version") != record.rules_version
    ):
        raise ActionError(
            "findings.validation.malformed",
            "published validation is outdated or malformed; run full validation",
        )
    expected_record = hashlib.sha256(
        record.canonical_json().encode("utf-8")
    ).hexdigest()
    projected_id = value.get("validation_id")
    body = {key: item for key, item in value.items() if key != "validation_id"}
    expected_id = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    if value.get("record_identity") != expected_record or projected_id != expected_id:
        raise ActionError(
            "findings.validation.malformed",
            "current published validation identity does not match validation",
        )
    if not _valid_projection_groups(value):
        raise ActionError(
            "findings.validation.malformed", "current batch group is malformed"
        )
    if not valid_repair_projection(value, record):
        raise ActionError(
            "findings.validation.malformed", "invalid primary repair membership"
        )
    return value


def _load_record(log: LogContext) -> MechanicalGeneratedRecord:
    path = log.root / VALIDATION_RESULTS
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "findings.result.missing",
            f"no cached mechanical result; run full validation for {log.summary}",
        )
    value = _read_json(path, maximum_bytes=MAX_RESULT_BYTES, label="result")
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


def _read_json(path: Path, *, maximum_bytes: int, label: str) -> dict[str, Any]:
    try:
        raw = bounded_file_bytes(path, maximum_bytes=maximum_bytes)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (
        BoundedFileReadError,
        UnicodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
    ) as error:
        raise ActionError(
            f"findings.{label}.malformed", f"published {label} is malformed"
        ) from error
    if not isinstance(value, dict):
        raise ActionError(
            f"findings.{label}.malformed", f"published {label} must be an object"
        )
    return value


def _reject_duplicate_identities(value: Mapping[str, Any]) -> None:
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


def _valid_projection_groups(projection: Mapping[str, object]) -> bool:
    chains = projection.get("chains")
    unresolved = projection.get("unresolved")
    assert isinstance(chains, list) and isinstance(unresolved, list)
    return all(
        isinstance(value, dict) and _valid_chain(value) for value in chains
    ) and all(
        isinstance(value, dict) and _valid_unresolved(value) for value in unresolved
    )


def _all_groups(projection: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        value
        for family in ("chains", "unresolved")
        for value in _sequence_items(projection.get(family))
        if isinstance(value, dict)
    ]


def _valid_findings(value: object) -> bool:
    if not isinstance(value, list):
        return False
    required = {
        "admission_effect",
        "affected_chains",
        "affected_entries",
        "code",
        "dependencies",
        "identity",
        "observed",
        "rule",
        "scope",
        "status",
        "subject",
    }
    return all(
        isinstance(item, dict)
        and set(item) == required
        and all(
            isinstance(item.get(key), str)
            for key in (
                "admission_effect",
                "code",
                "identity",
                "rule",
                "scope",
                "status",
                "subject",
            )
        )
        and item.get("admission_effect") in {"none", "chain", "entry", "log"}
        and _string_list(item.get("affected_chains"))
        and _string_list(item.get("affected_entries"))
        and _valid_admission_effect(item)
        and isinstance(item.get("dependencies"), list)
        and all(isinstance(child, dict) for child in item["dependencies"])
        and isinstance(item.get("observed"), dict)
        for item in value
    )


def _valid_admission_effect(item: Mapping[str, object]) -> bool:
    effect = item.get("admission_effect")
    chains = item.get("affected_chains")
    entries = item.get("affected_entries")
    assert isinstance(chains, list) and isinstance(entries, list)
    if effect in {"none", "log"}:
        return not chains and not entries
    if effect == "entry":
        return not chains and len(entries) == 1
    return effect == "chain" and len(chains) == 1 and len(entries) == 1


def _valid_chain(value: Mapping[str, object]) -> bool:
    required = {
        "artifacts",
        "chain_id",
        "commands",
        "edges",
        "entry",
        "findings",
        "registry",
        "signals",
    }
    if set(value) != required or not _valid_group_base(value):
        return False
    artifacts = value.get("artifacts")
    commands = value.get("commands")
    edges = value.get("edges")
    registry = value.get("registry")
    signals = value.get("signals")
    return (
        _string_list(artifacts)
        and isinstance(commands, list)
        and all(isinstance(item, dict) and _valid_command(item) for item in commands)
        and isinstance(edges, list)
        and all(isinstance(item, dict) and _valid_edge(item) for item in edges)
        and isinstance(registry, list)
        and all(isinstance(item, dict) and _valid_registry(item) for item in registry)
        and _string_list(signals)
    )


def _valid_unresolved(value: Mapping[str, object]) -> bool:
    return (
        set(value) == {"chain_id", "entry", "findings", "reason"}
        and _valid_group_base(value)
        and isinstance(value.get("reason"), str)
    )


def _valid_group_base(value: Mapping[str, object]) -> bool:
    findings = value.get("findings")
    return (
        isinstance(value.get("chain_id"), str)
        and isinstance(value.get("entry"), str)
        and _valid_findings(findings)
    )


def _valid_command(value: Mapping[str, object]) -> bool:
    required = {
        "collections",
        "document",
        "entry",
        "fence",
        "identity",
        "inputs",
        "ordinal",
        "outputs",
        "tokens",
    }
    if "script" in value:
        required.add("script")
    collections = value.get("collections")
    return (
        set(value) == required
        and ("script" not in value or isinstance(value["script"], str))
        and all(
            isinstance(value.get(key), str) for key in ("document", "entry", "identity")
        )
        and all(isinstance(value.get(key), int) for key in ("fence", "ordinal"))
        and _string_list(value.get("tokens"))
        and _valid_relationships(value.get("inputs"))
        and _valid_relationships(value.get("outputs"))
        and isinstance(collections, list)
        and all(
            isinstance(item, dict) and _valid_collection(item) for item in collections
        )
    )


def _valid_relationships(value: object) -> bool:
    required = {"direction", "path", "proof"}
    optional = {"artifact", "origin", "target"}
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and required <= set(item) <= required | optional
        and all(isinstance(item.get(key), str) for key in required)
        and all(
            key not in item or isinstance(item.get(key), str)
            for key in ("artifact", "target")
        )
        and ("origin" not in item or item.get("origin") is True)
        for item in value
    )


def _valid_collection(value: Mapping[str, object]) -> bool:
    return (
        set(value) == {"direction", "mechanism", "members", "root", "target"}
        and all(
            isinstance(value.get(key), str)
            for key in ("direction", "mechanism", "target")
        )
        and (value.get("root") is None or isinstance(value.get("root"), str))
        and _string_list(value.get("members"))
    )


def _valid_edge(value: Mapping[str, object]) -> bool:
    return set(value) == {"artifact", "source", "target"} and all(
        isinstance(value.get(key), str) for key in value
    )


def _valid_registry(value: Mapping[str, object]) -> bool:
    required = {"entry", "kind", "location", "name", "origin", "path"}
    optional = {"fingerprint", "from_entry", "read_only"}
    if not (required <= set(value) <= required | optional):
        return False
    return (
        all(
            isinstance(value.get(key), str)
            for key in ("entry", "kind", "location", "name", "path")
        )
        and isinstance(value.get("origin"), bool)
        and ("fingerprint" not in value or isinstance(value.get("fingerprint"), dict))
        and (
            ("from_entry" not in value and "read_only" not in value)
            or (
                isinstance(value.get("from_entry"), str)
                and value.get("read_only") is True
            )
        )
    )


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _matches(value: Mapping[str, object], filters: Mapping[str, list[str]]) -> bool:
    findings = value.get("findings")
    assert isinstance(findings, list)
    if not findings:
        return False
    if filters["entry"] and value.get("entry") not in filters["entry"]:
        return False
    if filters["command"]:
        command_ids = {
            str(item.get("identity"))
            for item in _mapping_items(value.get("commands"))
            if isinstance(item, Mapping)
        }
        if command_ids.isdisjoint(filters["command"]):
            return False
    if not any(filters[name] for name in ("area", "code", "family", "subject")):
        return True
    return any(
        _finding_matches(item, filters)
        for item in findings
        if isinstance(item, Mapping)
    )


def _finding_matches(
    finding: Mapping[str, object], filters: Mapping[str, list[str]]
) -> bool:
    code = str(finding.get("code", ""))
    return (
        (not filters["area"] or finding.get("scope") in filters["area"])
        and (not filters["code"] or code in filters["code"])
        and (
            not filters["family"]
            or any(
                code == family or code.startswith(f"{family}.")
                for family in filters["family"]
            )
        )
        and (not filters["subject"] or finding.get("subject") in filters["subject"])
    )


def _summary(
    value: Mapping[str, object], filters: Mapping[str, list[str]]
) -> dict[str, object]:
    findings = value.get("findings")
    assert isinstance(findings, list)
    selected = [
        item
        for item in findings
        if isinstance(item, Mapping) and _finding_matches(item, filters)
    ]
    if not any(filters[name] for name in ("area", "code", "family", "subject")):
        selected = [item for item in findings if isinstance(item, Mapping)]
    result: dict[str, object] = {
        "chain_id": value["chain_id"],
        "code_distribution": dict(
            sorted(Counter(str(item["code"]) for item in findings).items())
        ),
        "command_count": len(_sequence_items(value.get("commands"))),
        "entry": value["entry"],
        "finding_count": len(findings),
        "matching_findings": len(selected),
        "represented_checks": len(findings),
        "subjects": sorted({str(item["subject"]) for item in findings}),
    }
    if value.get("signals"):
        result["signals"] = value["signals"]
    if value.get("reason"):
        result["unresolved"] = value["reason"]
    return result


def _require_response_bound(
    result: Mapping[str, object], groups: Sequence[Mapping[str, object]]
) -> None:
    raw = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(raw) <= MAX_RESPONSE_BYTES:
        return
    raise ActionError(
        "findings.response.too_large",
        "matching validation exceeds the response bound: "
        f"entries={len({str(value['entry']) for value in groups})} "
        f"chains={len(groups)} findings="
        f"{sum(len(_sequence_items(value.get('findings'))) for value in groups)}",
    )


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _matching_count(value: Mapping[str, object]) -> int:
    count = value.get("matching_findings")
    return count if isinstance(count, int) else 0


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, Mapping))
