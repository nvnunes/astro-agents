"""Typed SQLite persistence and projections for validation observations."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from research_log_data import parse_resource_identity
from research_log_result_store import (
    ResultStoreError,
    result_snapshot,
    result_transaction,
    results_lock,
)

from .mechanical_results import (
    CheckScope,
    CheckStatus,
    CompletionState,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)

_ENTRY_ID_RE = re.compile(r"e[0-9]+[a-z]?\Z", re.IGNORECASE)
_CHECK_SCOPES = frozenset(scope.value for scope in CheckScope)
_CHECK_STATUSES = frozenset(status.value for status in CheckStatus)
_FAILING_CHECK_STATUSES = frozenset(
    (CheckStatus.FAIL.value, CheckStatus.UNAVAILABLE.value)
)
_NONFAILING_CHECK_STATUSES = frozenset(
    (CheckStatus.PASS.value, CheckStatus.NOT_APPLICABLE.value)
)


@dataclass(frozen=True)
class StoredValidationResult:
    result_id: str
    validation_id: str
    generation: int


@dataclass(frozen=True)
class ValidationPublicationRequest:
    """The complete, closed input required to retain one validation result."""

    log_root: Path
    mechanical_record: MechanicalGeneratedRecord
    batch_projection: Mapping[str, object] | None = None
    report_context: object | None = None
    result_metadata: Mapping[str, object] | None = None
    kind: str = "full"
    entry: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    source_identity: str | None = None


@dataclass(frozen=True)
class _ProjectionInsertContext:
    db: Any
    result_id: str
    checks: Mapping[str, MechanicalCheck]
    display_values: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class _BatchInsertContext:
    db: Any
    result_id: str
    projected_findings: Mapping[str, Mapping[str, object]]
    command_index: Mapping[str, Any]


@dataclass(frozen=True)
class _StoredBatchLinkContext:
    db: Any
    result_id: str
    commands: Mapping[str, Any]
    anchor_index: Mapping[str, set[str]]
    material_index: Mapping[str, set[str]]
    paths: Mapping[str, set[str]]


@dataclass(frozen=True)
class ValidationAdmission:
    """The indexed, durable admission facts for one published validation."""

    validation_id: str
    result_id: str
    groups: tuple["ValidationAdmissionGroup", ...]
    commands: tuple["ValidationAdmissionCommand", ...]
    findings: tuple["ValidationAdmissionFinding", ...]


@dataclass(frozen=True)
class ValidationAdmissionGroup:
    """One stored command-chain or unresolved admission group."""

    identity: str
    kind: str
    entry: str


@dataclass(frozen=True)
class ValidationAdmissionCommand:
    """One command identity and its stored output membership."""

    identity: str
    group_id: str
    entry: str
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class ValidationAdmissionFinding:
    """One stored finding's complete admission decision."""

    identity: str
    group_id: str
    status: str
    admission_effect: str
    affected_chains: tuple[str, ...]
    affected_entries: tuple[str, ...]


def provisional_validation_admission(
    mechanical_record: MechanicalGeneratedRecord,
    projection: Mapping[str, object],
) -> ValidationAdmission:
    """Decode one validated in-memory projection for a read-only dry run."""

    _validate_projection(projection, mechanical_record)
    groups: list[ValidationAdmissionGroup] = []
    commands: list[ValidationAdmissionCommand] = []
    findings: list[ValidationAdmissionFinding] = []
    for kind, family in (("chain", "chains"), ("unresolved", "unresolved")):
        for group in _sequence(projection[family], f"{family} groups"):
            assert isinstance(group, Mapping)
            group_id = _string(group["chain_id"], "admission group identity")
            entry = _string(group["entry"], "admission group entry")
            groups.append(ValidationAdmissionGroup(group_id, kind, entry))
            for command in _sequence(group["commands"], "admission commands"):
                assert isinstance(command, Mapping)
                outputs = [
                    _string(item["path"], "admission output")
                    for item in _sequence(command["outputs"], "admission outputs")
                    if isinstance(item, Mapping)
                ]
                for collection in _sequence(
                    command["collections"], "admission collections"
                ):
                    if (
                        not isinstance(collection, Mapping)
                        or collection.get("direction") != "output"
                    ):
                        continue
                    root = collection.get("root")
                    if isinstance(root, str):
                        outputs.append(root)
                    outputs.extend(
                        _string(member, "admission output collection member")
                        for member in _sequence(
                            collection["members"], "admission output collection"
                        )
                    )
                commands.append(
                    ValidationAdmissionCommand(
                        _string(command["identity"], "admission command identity"),
                        group_id,
                        _string(command["entry"], "admission command entry"),
                        tuple(outputs),
                    )
                )
            for finding in _sequence(group["findings"], "admission findings"):
                assert isinstance(finding, Mapping)
                findings.append(
                    ValidationAdmissionFinding(
                        _string(finding["identity"], "admission finding identity"),
                        group_id,
                        _string(finding["status"], "admission finding status"),
                        _string(
                            finding["admission_effect"], "admission finding effect"
                        ),
                        tuple(
                            _strings(
                                finding["affected_chains"],
                                "admission finding chains",
                            )
                        ),
                        tuple(
                            _strings(
                                finding["affected_entries"],
                                "admission finding entries",
                            )
                        ),
                    )
                )
    return ValidationAdmission(
        _string(projection["validation_id"], "admission validation identity"),
        "provisional",
        tuple(groups),
        tuple(commands),
        tuple(findings),
    )


@dataclass(frozen=True)
class ValidationReportProjection:
    """One stored validation record with its captured display projection."""

    stored: StoredValidationResult
    record: MechanicalGeneratedRecord
    context: object
    groups: tuple[object, ...]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


_PROJECTION_FIELDS = frozenset(
    {
        "schema",
        "validation_id",
        "record_identity",
        "summary",
        "result_date",
        "rules_version",
        "source_identity",
        "chains",
        "unresolved",
        "repair_batches",
    }
)
_FINDING_FIELDS = frozenset(
    {
        "identity",
        "scope",
        "status",
        "code",
        "subject",
        "rule",
        "observed",
        "admission_effect",
        "dependencies",
        "affected_chains",
        "affected_entries",
    }
)
_COMMAND_FIELDS = frozenset(
    {
        "identity",
        "entry",
        "document",
        "fence",
        "ordinal",
        "script",
        "tokens",
        "inputs",
        "outputs",
        "collections",
    }
)
_RELATIONSHIP_REQUIRED_FIELDS = frozenset({"direction", "path", "proof"})
_RELATIONSHIP_OPTIONAL_FIELDS = frozenset({"target", "artifact", "origin"})
_COLLECTION_FIELDS = frozenset({"direction", "mechanism", "root", "target", "members"})
_REGISTRY_FIELDS = frozenset(
    {"entry", "name", "kind", "location", "path", "origin", "identity"}
)
_REFERENCE_REGISTRY_FIELDS = _REGISTRY_FIELDS | frozenset({"from_entry", "read_only"})
_BATCH_FIELDS = frozenset(
    {
        "batch_id",
        "batch_type",
        "grouping_reason",
        "scope",
        "entries",
        "anchors",
        "primary_finding_ids",
        "related_chain_ids",
        "related_batch_ids",
    }
)
_METADATA_FIELDS = frozenset(
    {
        "source_identity",
        "reason",
        "requested_entries",
        "evaluated_entries",
        "dependency_entries",
        "whole_log_limitations",
    }
)
MAX_VALIDATION_RESULT_ROWS = 100_000
MAX_VALIDATION_RESULT_BYTES = 64 * 1024 * 1024


def _closed_mapping(
    value: object, fields: frozenset[str], name: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{name} has incorrect fields")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _strings(value: object, name: str) -> list[str]:
    values = _sequence(value, name)
    if not all(isinstance(item, str) and item for item in values):
        raise ValueError(f"{name} must contain nonempty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate members")
    return [cast(str, item) for item in values]


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _validate_command(command: Mapping[str, object], group_entry: str) -> None:
    _closed_mapping(command, _COMMAND_FIELDS, "projected command")
    for field in ("identity", "entry", "document", "script"):
        _string(command[field], f"command {field}")
    if command["entry"] != group_entry:
        raise ValueError("command entry does not match its group")
    for field in ("fence", "ordinal"):
        _nonnegative_integer(command[field], f"command {field}")
    _validate_command_tokens(command)
    _validate_command_relationships(command)
    _validate_command_collections(command)


def _validate_command_tokens(command: Mapping[str, object]) -> None:
    tokens = _sequence(command["tokens"], "command tokens")
    if not all(isinstance(token, str) and token for token in tokens):
        raise ValueError("command tokens must contain nonempty strings")


def _validate_command_relationships(command: Mapping[str, object]) -> None:
    for direction, field in (("input", "inputs"), ("output", "outputs")):
        for relationship in _sequence(command[field], f"command {field}"):
            if not isinstance(relationship, Mapping) or not (
                _RELATIONSHIP_REQUIRED_FIELDS
                <= set(relationship)
                <= _RELATIONSHIP_REQUIRED_FIELDS | _RELATIONSHIP_OPTIONAL_FIELDS
            ):
                raise ValueError("command relationship has incorrect fields")
            value = relationship
            if value["direction"] != direction:
                raise ValueError("command relationship direction is inconsistent")
            for name in ("path", "proof"):
                _string(value[name], f"command relationship {name}")
            for name in ("target", "artifact"):
                if name in value:
                    _string(value[name], f"command relationship {name}")
            if "origin" in value and not isinstance(value["origin"], bool):
                raise ValueError("command relationship origin must be boolean")


def _validate_command_collections(command: Mapping[str, object]) -> None:
    for collection in _sequence(command["collections"], "command collections"):
        value = _closed_mapping(collection, _COLLECTION_FIELDS, "command collection")
        if value["direction"] not in {"input", "output"}:
            raise ValueError("command collection has an invalid direction")
        for name in ("mechanism", "target"):
            _string(value[name], f"command collection {name}")
        if value["root"] is not None:
            _string(value["root"], "command collection root")
        _strings(value["members"], "command collection members")


def _validate_registry(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("registry record must be an object")
    fields = frozenset(value)
    if fields not in {_REGISTRY_FIELDS, _REFERENCE_REGISTRY_FIELDS}:
        raise ValueError("registry record has incorrect fields")
    for field in ("entry", "name", "kind", "location", "path"):
        _string(value[field], f"registry {field}")
    if not isinstance(value["origin"], bool):
        raise ValueError("registry origin must be boolean")
    if "from_entry" in value:
        _string(value["from_entry"], "registry from_entry")
        if value["read_only"] is not True:
            raise ValueError("reference registry record must be read-only")
    try:
        parse_resource_identity(
            value["identity"], "registry identity", kind=value["kind"]
        )
    except ValueError as error:
        raise ValueError("registry identity is invalid") from error


def _metadata_entries(
    metadata: Mapping[str, object], kind: str, entry: str | None
) -> tuple[dict[str, list[str]], list[str], str | None]:
    if set(metadata) - _METADATA_FIELDS:
        raise ValueError("validation result metadata has incorrect fields")
    reason = metadata.get("reason")
    if reason is not None:
        _string(reason, "validation result reason")
    relations = {
        relation: _strings(
            metadata.get(f"{relation}_entries", []), f"{relation} entries"
        )
        for relation in ("requested", "evaluated", "dependency")
    }
    if kind == "entry":
        assert entry is not None
        if not relations["requested"]:
            relations["requested"] = [entry]
        if not relations["evaluated"]:
            relations["evaluated"] = [entry]
    limitations = _strings(
        metadata.get("whole_log_limitations", []), "whole-log limitations"
    )
    return (
        relations,
        limitations,
        _string(reason, "validation result reason") if reason is not None else None,
    )


def result_metadata_from_evaluation(
    context: object, *, source_identity: str
) -> dict[str, object]:
    """Capture evaluator-owned coverage without re-reading source documents."""

    target = getattr(context, "target")
    requested = (
        [str(getattr(target, "entry_id"))]
        if hasattr(target, "entry_id")
        else list(getattr(context, "selected_documents"))
    )
    return {
        "source_identity": source_identity,
        "requested_entries": requested,
        "evaluated_entries": list(getattr(context, "selected_documents")),
        "dependency_entries": list(getattr(context, "dependency_entries")),
        "whole_log_limitations": list(getattr(context, "whole_log_conclusions")),
    }


def _validate_storage_bounds(
    record: MechanicalGeneratedRecord,
    projection: Mapping[str, object],
    metadata: Mapping[str, object],
    report_context: str | None,
) -> None:
    """Bound the full normalized candidate before it can replace a slot."""

    candidate = {
        "record": record.as_dict(),
        "projection": projection,
        "metadata": metadata,
        "report_context": report_context,
    }
    if len(_json(candidate).encode("utf-8")) > MAX_VALIDATION_RESULT_BYTES:
        raise ValueError("validation result exceeds its byte bound")
    rows = (
        1 + len(record.checks) + sum(len(check.dependencies) for check in record.checks)
    )
    rows += sum(
        len(_sequence(metadata.get(f"{relation}_entries", []), f"{relation} entries"))
        for relation in ("requested", "evaluated", "dependency")
    )
    rows += len(_sequence(metadata.get("whole_log_limitations", []), "limitations"))
    rows += _projection_group_row_count(projection)
    rows += _batch_row_count(projection)
    if rows > MAX_VALIDATION_RESULT_ROWS:
        raise ValueError("validation result exceeds its row bound")


def _projection_group_row_count(
    projection: Mapping[str, object],
) -> int:
    rows = 0
    for family in ("chains", "unresolved"):
        for group in _sequence(projection.get(family, []), f"{family} groups"):
            assert isinstance(group, Mapping)
            findings = _sequence(group["findings"], "group findings")
            rows += 1
            for finding in findings:
                assert isinstance(finding, Mapping)
                rows += 1 + len(
                    _sequence(finding["dependencies"], "finding dependencies")
                )
                rows += len(_sequence(finding["affected_chains"], "affected chains"))
                rows += len(_sequence(finding["affected_entries"], "affected entries"))
            for command in _sequence(group.get("commands", []), "group commands"):
                assert isinstance(command, Mapping)
                rows += 1 + len(_sequence(command["tokens"], "command tokens"))
                rows += len(_sequence(command["inputs"], "command inputs"))
                rows += len(_sequence(command["outputs"], "command outputs"))
                for collection in _sequence(
                    command["collections"], "command collections"
                ):
                    assert isinstance(collection, Mapping)
                    rows += 1 + len(
                        _sequence(collection["members"], "collection members")
                    )
            artifact_count = len(
                _sequence(group.get("artifacts", []), "group artifacts")
            )
            rows += artifact_count * 2
            rows += len(_sequence(group.get("edges", []), "group edges"))
            rows += len(_sequence(group.get("registry", []), "group registry"))
            rows += len(_sequence(group.get("signals", []), "group signals"))
    return rows


def _batch_row_count(projection: Mapping[str, object]) -> int:
    """Count batch rows with the exact deduplication used by the writer."""

    rows = 0
    projected = _projected_findings(projection)
    index = _batch_command_index(projection)
    for batch in _sequence(projection.get("repair_batches", []), "repair batches"):
        assert isinstance(batch, Mapping)
        rows += 1
        for field in (
            "entries",
            "anchors",
            "primary_finding_ids",
            "related_chain_ids",
            "related_batch_ids",
        ):
            rows += len(_sequence(batch[field], f"repair batch {field}"))
        findings = list(_sequence(batch["primary_finding_ids"], "batch findings"))
        codes = [
            _string(projected[_string(finding_id, "batch finding")]["code"], "finding code")
            for finding_id in findings
        ]
        commands = _batch_link_commands(batch, findings, codes, projected, index)
        rows += sum(len(command_codes) for command_codes in commands.values())
    return rows


def _projected_findings(
    projection: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    """Index the already-validated projected findings for batch link counting."""

    findings: dict[str, Mapping[str, object]] = {}
    for family in ("chains", "unresolved"):
        for group in _sequence(projection[family], f"{family} groups"):
            assert isinstance(group, Mapping)
            for finding in _sequence(group["findings"], "group findings"):
                assert isinstance(finding, Mapping)
                identity = _string(finding["identity"], "finding identity")
                findings[identity] = finding
    return findings


def _captured_report_context(context: object | None) -> str | None:
    """Persist the closed display context used to create this observation."""
    if context is None:
        return None
    entries = getattr(context, "entries", None)
    if not isinstance(entries, Mapping):
        raise ValueError("report context has no entry mapping")
    return _json(
        {
            "title": str(getattr(context, "title")),
            "summary": str(getattr(context, "summary")),
            "entries": [
                {
                    "id": str(key),
                    "title": str(value.title),
                    "document": str(value.document),
                }
                for key, value in sorted(entries.items())
            ],
        }
    )


def _display_values(
    record: MechanicalGeneratedRecord, context: object | None
) -> dict[str, tuple[str, str]]:
    if context is None:
        return {}
    from .human_projection import project_findings

    values: dict[str, tuple[str, str]] = {}
    for group in project_findings(record, cast(Any, context)):
        entry = group.entry or ""
        for identity in group.check_ids:
            values[identity] = (entry, group.subject)
    return values


def _validate_projection(
    projection: Mapping[str, object], record: MechanicalGeneratedRecord
) -> None:
    _closed_mapping(projection, _PROJECTION_FIELDS, "validation projection")
    if projection["schema"] != "research-log-published-validation/2":
        raise ValueError("unsupported validation projection schema")
    record_identity = hashlib.sha256(record.canonical_json().encode()).hexdigest()
    if projection["record_identity"] != record_identity:
        raise ValueError("validation projection does not match mechanical record")
    for field in (
        "validation_id",
        "summary",
        "result_date",
        "rules_version",
        "source_identity",
    ):
        if not isinstance(projection[field], str) or not projection[field]:
            raise ValueError(f"validation projection {field} must be nonempty")
    if (
        projection["summary"] != record.summary
        or projection["result_date"] != record.result_date
        or projection["rules_version"] != record.rules_version
    ):
        raise ValueError(
            "validation projection metadata does not match mechanical record"
        )
    identity_body = dict(projection)
    validation_id = identity_body.pop("validation_id")
    if validation_id != hashlib.sha256(_json(identity_body).encode()).hexdigest():
        raise ValueError("validation projection identity is invalid")
    direct = {check.identity: check for check in record.checks if check.failure}
    groups, group_entries = _validate_projection_groups(projection, direct)
    _validate_finding_admission_links(projection, groups, group_entries)
    _validate_projection_batches(projection, set(direct), groups)


def _validate_projection_groups(
    projection: Mapping[str, object], direct: Mapping[str, MechanicalCheck]
) -> tuple[set[str], dict[str, str]]:
    findings: set[str] = set()
    groups: set[str] = set()
    commands: set[str] = set()
    group_entries: dict[str, str] = {}
    for family, kind in (("chains", "chain"), ("unresolved", "unresolved")):
        for group in _sequence(projection[family], family):
            item = _validated_group_mapping(group, family, kind, groups)
            group_id = _string(item["chain_id"], "projection group identity")
            group_entries[group_id] = _string(item["entry"], "projection group entry")
            _validate_group_findings(item, direct, findings)
            if kind == "chain":
                _validate_chain_group_members(item, commands)
    if findings != set(direct):
        raise ValueError("projection must contain every direct finding exactly once")
    return groups, group_entries


def _validated_group_mapping(
    group: object, family: str, kind: str, groups: set[str]
) -> Mapping[str, Any]:
    fields = (
        {"chain_id", "entry", "findings", "reason"}
        if kind == "unresolved"
        else {
            "chain_id",
            "entry",
            "findings",
            "commands",
            "artifacts",
            "edges",
            "signals",
            "registry",
        }
    )
    item = _closed_mapping(group, frozenset(fields), f"{family} group")
    group_id = _string(item["chain_id"], "projection group identity")
    _string(item["entry"], "projection group entry")
    if group_id in groups:
        raise ValueError("projection group identities must be unique")
    if kind == "unresolved" and item["reason"] != "finding_scope_unresolved":
        raise ValueError("unresolved group has an invalid reason")
    groups.add(group_id)
    return item


def _validate_group_findings(
    group: Mapping[str, Any], direct: Mapping[str, MechanicalCheck], findings: set[str]
) -> None:
    for finding in _sequence(group["findings"], "group findings"):
        value = _closed_mapping(finding, _FINDING_FIELDS, "projected finding")
        identity = _string(value["identity"], "projected finding identity")
        if identity not in direct or identity in findings:
            raise ValueError("projected findings must uniquely name direct checks")
        _validate_finding_check(value, direct[identity])
        _validate_finding_effect(value)
        findings.add(identity)


def _validate_finding_check(value: Mapping[str, Any], check: MechanicalCheck) -> None:
    assert check.failure is not None
    if (
        value["scope"] != check.scope.value
        or value["status"] != check.status.value
        or value["code"] != check.failure.code
        or value["subject"] != check.failure.subject
        or value["rule"] != check.failure.rule
        or value["observed"] != dict(check.failure.observed)
        or value["dependencies"] != [dict(item) for item in check.dependencies]
    ):
        raise ValueError("projected finding does not match its direct check")


def _validate_finding_effect(value: Mapping[str, Any]) -> None:
    effect = value["admission_effect"]
    chains = _strings(value["affected_chains"], "affected chains")
    entries = _strings(value["affected_entries"], "affected entries")
    if effect in {"none", "log"} and (chains or entries):
        raise ValueError("non-admitting finding has affected members")
    if effect == "entry" and (chains or len(entries) != 1):
        raise ValueError("entry finding must affect exactly one entry")
    if effect == "chain" and (len(chains) != 1 or len(entries) != 1):
        raise ValueError("chain finding must affect one chain and entry")
    if effect not in {"none", "log", "entry", "chain"}:
        raise ValueError("unknown admission effect")


def _validate_chain_group_members(group: Mapping[str, Any], commands: set[str]) -> None:
    command_ids = _validate_group_commands(group, commands)
    artifacts = set(_strings(group["artifacts"], "group artifacts"))
    _validate_group_signals_registry(group)
    _validate_group_edges(group, command_ids, artifacts)


def _validate_group_commands(group: Mapping[str, Any], commands: set[str]) -> set[str]:
    command_ids: set[str] = set()
    entry = _string(group["entry"], "projection group entry")
    for raw in _sequence(group["commands"], "group commands"):
        command = _closed_mapping(raw, _COMMAND_FIELDS, "projected command")
        identity = _string(command["identity"], "projected command identity")
        if identity in command_ids or identity in commands:
            raise ValueError("projected command identities must be unique")
        command_ids.add(identity)
        commands.add(identity)
        _validate_command(command, entry)
    return command_ids


def _validate_group_signals_registry(group: Mapping[str, Any]) -> None:
    allowed = {"directory", "fan_in", "fan_out", "multi_command"}
    if any(
        signal not in allowed for signal in _strings(group["signals"], "group signals")
    ):
        raise ValueError("group signal is unsupported")
    for registry in _sequence(group["registry"], "group registry"):
        _validate_registry(registry)


def _validate_group_edges(
    group: Mapping[str, Any], command_ids: set[str], artifacts: set[str]
) -> None:
    seen: set[tuple[str, str, str]] = set()
    for edge in _sequence(group["edges"], "group edges"):
        value = _closed_mapping(
            edge, frozenset({"source", "target", "artifact"}), "group edge"
        )
        item = (
            _string(value["source"], "group edge source"),
            _string(value["target"], "group edge target"),
            _string(value["artifact"], "group edge artifact"),
        )
        if (
            item[0] not in command_ids
            or item[1] not in command_ids
            or item[2] not in artifacts
        ):
            raise ValueError("group edge has a dangling member")
        if item in seen:
            raise ValueError("group edges must be unique")
        seen.add(item)


def _validate_finding_admission_links(
    projection: Mapping[str, object],
    groups: set[str],
    group_entries: Mapping[str, str],
) -> None:
    for family in ("chains", "unresolved"):
        for group in _sequence(projection[family], f"{family} groups"):
            assert isinstance(group, Mapping)
            for finding in _sequence(group["findings"], "group findings"):
                assert isinstance(finding, Mapping)
                effect = finding["admission_effect"]
                if effect == "chain" and finding["affected_chains"][0] not in groups:
                    raise ValueError("finding names an unknown affected chain")
                if (
                    effect == "chain"
                    and finding["affected_entries"][0]
                    != group_entries[finding["affected_chains"][0]]
                ):
                    raise ValueError("chain finding affects the wrong entry")


def _validate_projection_batches(
    projection: Mapping[str, object], direct: set[str], groups: set[str]
) -> None:
    seen_primary: set[str] = set()
    batch_ids: set[str] = set()
    deferred_batches: list[Mapping[str, object]] = []
    for batch in _sequence(projection["repair_batches"], "repair batches"):
        item = _closed_mapping(batch, _BATCH_FIELDS, "repair batch")
        batch_id = _string(item["batch_id"], "repair batch id")
        if batch_id in batch_ids:
            raise ValueError("repair batch identities must be unique")
        batch_ids.add(batch_id)
        for field in ("batch_type", "grouping_reason", "scope"):
            _string(item[field], f"repair batch {field}")
        _strings(item["entries"], "repair batch entries")
        if not isinstance(item["anchors"], list) or not all(
            isinstance(anchor, Mapping) for anchor in item["anchors"]
        ):
            raise ValueError("repair batch anchors must be object records")
        primary = _strings(item["primary_finding_ids"], "primary finding ids")
        if not primary or any(
            value not in direct or value in seen_primary for value in primary
        ):
            raise ValueError("every direct finding requires one primary repair batch")
        seen_primary.update(primary)
        for field in ("related_chain_ids", "related_batch_ids"):
            _strings(item[field], f"repair batch {field}")
        if any(
            chain not in groups
            for chain in _strings(item["related_chain_ids"], "related chains")
        ):
            raise ValueError("repair batch names an unknown group")
        deferred_batches.append(item)
    if seen_primary != direct:
        raise ValueError("every direct finding requires one primary repair batch")
    if any(
        related not in batch_ids
        for batch in deferred_batches
        for related in _strings(batch["related_batch_ids"], "related batches")
    ):
        raise ValueError("repair batch names an unknown related batch")


def publish_validation_result(
    request: ValidationPublicationRequest,
) -> StoredValidationResult:
    """Atomically replace one completed validation slot with normalized rows."""
    log_root = request.log_root
    mechanical_record = request.mechanical_record
    batch_projection = request.batch_projection
    report_context = request.report_context
    result_metadata = request.result_metadata
    kind = request.kind
    entry = request.entry
    started_at = request.started_at
    finished_at = request.finished_at
    source_identity = request.source_identity
    if kind not in {"full", "entry", "diagnostic"} or (kind != "full" and not entry):
        raise ValueError("invalid validation result slot")
    if mechanical_record.completion is CompletionState.INCOMPLETE:
        raise ValueError("incomplete validation records cannot replace completed slots")
    metadata, projection = dict(result_metadata or {}), dict(batch_projection or {})
    relations, limitations, reason = _metadata_entries(metadata, kind, entry)
    record_identity = hashlib.sha256(
        mechanical_record.canonical_json().encode()
    ).hexdigest()
    if kind in {"full", "entry"} and not projection:
        raise ValueError("completed validation requires a complete projection")
    if projection:
        _validate_projection(projection, mechanical_record)
    stored_source_identity = (
        source_identity
        or metadata.get("source_identity")
        or projection.get("source_identity")
    )
    if projection and stored_source_identity != projection["source_identity"]:
        raise ValueError("result source identity does not match projection")
    result_id, finished = str(uuid.uuid4()), finished_at or _now()
    slot = "full" if kind == "full" else f"{kind}:{entry}"
    validation_id = str(projection.get("validation_id") or record_identity)
    display_values = (
        _display_values(mechanical_record, report_context) if kind == "full" else {}
    )
    report_snapshot = (
        _captured_report_context(report_context) if kind == "full" else None
    )
    bounded_metadata = dict(metadata)
    bounded_metadata.update(
        {f"{relation}_entries": values for relation, values in relations.items()}
    )
    bounded_metadata["whole_log_limitations"] = limitations
    _validate_storage_bounds(
        mechanical_record, projection, bounded_metadata, report_snapshot
    )
    with result_transaction(log_root) as db:
        prior = db.execute(
            "SELECT generation FROM store_state WHERE domain='validation'"
        ).fetchone()
        generation = 1 if prior is None else int(prior[0]) + 1
        db.execute(
            "DELETE FROM validation_results"
            if kind == "full"
            else "DELETE FROM validation_results WHERE slot=?",
            () if kind == "full" else (slot,),
        )
        db.execute(
            "INSERT INTO validation_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result_id,
                generation,
                slot,
                kind,
                entry,
                mechanical_record.summary,
                mechanical_record.completion.value,
                reason,
                len(mechanical_record.checks),
                sum(check.failure is not None for check in mechanical_record.checks),
                started_at or finished,
                finished,
                finished,
                mechanical_record.result_date,
                mechanical_record.rules_version,
                stored_source_identity,
                validation_id,
                record_identity,
                projection.get("schema"),
                report_snapshot,
            ),
        )
        for relation, entries in relations.items():
            db.executemany(
                "INSERT INTO validation_result_entries VALUES (?, ?, ?, ?)",
                [
                    (result_id, relation, value, position)
                    for position, value in enumerate(entries)
                ],
            )
        db.executemany(
            "INSERT INTO validation_result_limitations VALUES (?, ?, ?)",
            [
                (result_id, value, position)
                for position, value in enumerate(limitations)
            ],
        )
        for check in mechanical_record.checks:
            failure = check.failure
            db.execute(
                "INSERT INTO validation_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    check.identity,
                    check.scope.value,
                    check.status.value,
                    check.subject,
                    None if failure is None else failure.code,
                    None if failure is None else failure.rule,
                    None if failure is None else _json(failure.observed),
                    None if failure is None else failure.dependency,
                ),
            )
            db.executemany(
                "INSERT INTO validation_check_dependencies VALUES (?, ?, ?, ?)",
                [
                    (result_id, check.identity, p, _json(v))
                    for p, v in enumerate(check.dependencies)
                ],
            )
        if kind in {"full", "entry"} and projection:
            _insert_projection(
                db, result_id, projection, mechanical_record, display_values
            )
        db.execute(
            "INSERT INTO store_state VALUES ('validation', ?, ?) ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation, summary=excluded.summary",
            (generation, mechanical_record.summary),
        )
        db.execute("DELETE FROM report_materializations WHERE kind='validation'")
    return StoredValidationResult(result_id, validation_id, generation)


def _insert_projection(
    db: Any,
    result_id: str,
    projection: Mapping[str, object],
    record: MechanicalGeneratedRecord,
    display_values: Mapping[str, tuple[str, str]],
) -> None:
    checks = {check.identity: check for check in record.checks}
    projected_findings = {
        str(finding["identity"]): finding
        for family in ("chains", "unresolved")
        for group in _sequence(projection[family], f"{family} groups")
        if isinstance(group, Mapping)
        for finding in _sequence(group["findings"], "group findings")
        if isinstance(finding, Mapping)
    }
    command_index = _batch_command_index(projection)
    _insert_projection_groups(db, result_id, projection, checks, display_values)
    context = _BatchInsertContext(db, result_id, projected_findings, command_index)
    _insert_projection_batches(context, projection)


def _insert_projection_groups(
    db: Any,
    result_id: str,
    projection: Mapping[str, object],
    checks: Mapping[str, MechanicalCheck],
    display_values: Mapping[str, tuple[str, str]],
) -> None:
    context = _ProjectionInsertContext(db, result_id, checks, display_values)
    for family, group_kind in (("chains", "chain"), ("unresolved", "unresolved")):
        groups = projection.get(family, [])
        if not isinstance(groups, list):
            raise ValueError("projection groups must be lists")
        for position, group in enumerate(groups):
            if (
                not isinstance(group, Mapping)
                or not isinstance(group.get("chain_id"), str)
                or not isinstance(group.get("entry"), str)
            ):
                raise ValueError("invalid projection group")
            group_id = str(group["chain_id"])
            db.execute(
                "INSERT INTO validation_groups VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    group_id,
                    group_kind,
                    group["entry"],
                    group.get("reason"),
                    position,
                ),
            )
            _insert_group_detail(db, result_id, group_id, group)
            _insert_group_findings(context, group_id, group)


def _insert_group_findings(
    context: _ProjectionInsertContext,
    group_id: str,
    group: Mapping[str, object],
) -> None:
    findings = group.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("invalid projected findings")
    for finding_position, finding in enumerate(findings):
        if not isinstance(finding, Mapping) or not isinstance(
            finding.get("identity"), str
        ):
            raise ValueError("invalid projected finding")
        check = context.checks.get(str(finding["identity"]))
        if check is None or check.failure is None:
            raise ValueError("projected finding has no matching failed check")
        entry, subject = context.display_values.get(
            str(finding["identity"]),
            (str(group["entry"]), str(finding["subject"])),
        )
        context.db.execute(
            "INSERT INTO validation_findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                context.result_id,
                finding["identity"],
                group_id,
                finding_position,
                finding.get("scope"),
                finding.get("status"),
                finding.get("code"),
                finding.get("subject"),
                finding.get("rule"),
                _json(finding.get("observed", {})),
                finding.get("admission_effect"),
                entry,
                subject,
            ),
        )
        context.db.executemany(
            "INSERT INTO validation_finding_dependencies VALUES (?, ?, ?, ?)",
            [
                (context.result_id, finding["identity"], p, _json(v))
                for p, v in enumerate(finding.get("dependencies", []))
            ],
        )
        context.db.executemany(
            "INSERT INTO validation_finding_affected_chains VALUES (?, ?, ?, ?)",
            [
                (context.result_id, finding["identity"], p, v)
                for p, v in enumerate(finding.get("affected_chains", []))
            ],
        )
        context.db.executemany(
            "INSERT INTO validation_finding_affected_entries VALUES (?, ?, ?, ?)",
            [
                (context.result_id, finding["identity"], p, v)
                for p, v in enumerate(finding.get("affected_entries", []))
            ],
        )


def _insert_projection_batches(
    context: _BatchInsertContext,
    projection: Mapping[str, object],
) -> None:
    batches = projection.get("repair_batches", [])
    if not isinstance(batches, list):
        raise ValueError("repair batches must be a list")
    for position, batch in enumerate(batches):
        if not isinstance(batch, Mapping) or not isinstance(batch.get("batch_id"), str):
            raise ValueError("invalid repair batch")
        findings = batch.get("primary_finding_ids", [])
        if not isinstance(findings, list) or not findings:
            raise ValueError("repair batch has no primary findings")
        starting_finding_id = next(
            (
                finding_id
                for finding_id in findings
                if _has_rejected_command(context.projected_findings[str(finding_id)])
            ),
            findings[0],
        )
        context.db.execute(
            "INSERT INTO validation_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                context.result_id,
                batch["batch_id"],
                batch.get("batch_type", ""),
                batch.get("grouping_reason", ""),
                batch.get("scope", ""),
                len(findings),
                starting_finding_id,
                position,
            ),
        )
        _insert_batch_members(context.db, context.result_id, batch)
        _insert_batch_command_links(context, batch, findings)


def _insert_batch_members(db: Any, result_id: str, batch: Mapping[str, Any]) -> None:
    for field, table, column in (
        ("entries", "validation_batch_entries", "entry"),
        ("anchors", "validation_batch_anchors", "anchor_json"),
        ("primary_finding_ids", "validation_batch_findings", "finding_id"),
        ("related_chain_ids", "validation_batch_groups", "group_id"),
        ("related_batch_ids", "validation_batch_related_batches", "related_batch_id"),
    ):
        values = _sequence(batch[field], f"repair batch {field}")
        db.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
            [
                (
                    result_id,
                    batch["batch_id"],
                    p,
                    _json(v) if column == "anchor_json" else v,
                )
                for p, v in enumerate(values)
            ],
        )


def _insert_batch_command_links(
    context: _BatchInsertContext, batch: Mapping[str, Any], findings: list[Any]
) -> None:
    batch_codes = _batch_finding_codes(context.db, context.result_id, findings)
    commands = _batch_link_commands(
        batch, findings, batch_codes, context.projected_findings, context.command_index
    )
    for command_id, codes in commands.items():
        context.db.executemany(
            "INSERT OR IGNORE INTO validation_batch_command_links VALUES (?, ?, ?, ?)",
            [
                (context.result_id, batch["batch_id"], command_id, code)
                for code in codes
            ],
        )


def _batch_finding_codes(db: Any, result_id: str, findings: list[Any]) -> list[str]:
    codes: list[str] = []
    for finding_id in findings:
        finding = db.execute(
            "SELECT code FROM validation_findings WHERE result_id=? AND finding_id=?",
            (result_id, finding_id),
        ).fetchone()
        if finding is None:
            raise ValueError("repair batch names an unknown finding")
        code = str(finding["code"])
        codes.append(code)
    return codes


def _batch_link_commands(
    batch: Mapping[str, Any],
    findings: list[Any],
    codes: list[str],
    projected: Mapping[str, Mapping[str, object]],
    index: Mapping[str, Any],
) -> dict[str, set[str]]:
    linked: dict[str, set[str]] = {}
    for finding_id, code in zip(findings, codes, strict=True):
        for command_id in _rejected_command_ids(projected[str(finding_id)]):
            if command_id in index["commands"]:
                linked.setdefault(command_id, set()).add(code)
    for command_id in _anchor_matched_commands(batch, index):
        linked.setdefault(command_id, set()).update(codes)
    return linked


def _insert_group_detail(
    db: Any, result_id: str, group_id: str, group: Mapping[str, Any]
) -> None:
    _insert_group_artifacts_signals_registry(db, result_id, group_id, group)
    _insert_group_commands(db, result_id, group_id, group)
    _insert_group_edges(db, result_id, group_id, group)


def _insert_group_artifacts_signals_registry(
    db: Any, result_id: str, group_id: str, group: Mapping[str, Any]
) -> None:
    for position, artifact in enumerate(group.get("artifacts", [])):
        db.execute(
            "INSERT OR IGNORE INTO validation_artifacts VALUES (?, ?)",
            (result_id, artifact),
        )
        db.execute(
            "INSERT INTO validation_group_artifacts VALUES (?, ?, ?, ?)",
            (result_id, group_id, position, artifact),
        )
    for position, signal in enumerate(group.get("signals", [])):
        db.execute(
            "INSERT INTO validation_group_signals VALUES (?, ?, ?, ?)",
            (result_id, group_id, position, signal),
        )
    for position, registry in enumerate(group.get("registry", [])):
        assert isinstance(registry, Mapping)
        db.execute(
            "INSERT INTO validation_group_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result_id,
                group_id,
                position,
                registry["entry"],
                registry["name"],
                registry["kind"],
                registry["location"],
                registry["path"],
                int(registry["origin"]),
                registry.get("from_entry"),
                None if "read_only" not in registry else int(registry["read_only"]),
                _json(registry["identity"]),
            ),
        )


def _insert_group_commands(
    db: Any, result_id: str, group_id: str, group: Mapping[str, Any]
) -> None:
    for position, command in enumerate(group.get("commands", [])):
        if not isinstance(command, Mapping):
            continue
        command_id = command.get("identity")
        db.execute(
            "INSERT INTO validation_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result_id,
                command_id,
                command.get("entry"),
                command.get("document"),
                command.get("fence"),
                command.get("ordinal"),
                command.get("script"),
                group_id,
                position,
            ),
        )
        db.executemany(
            "INSERT INTO validation_command_tokens VALUES (?, ?, ?, ?)",
            [
                (result_id, command_id, p, value)
                for p, value in enumerate(command.get("tokens", []))
            ],
        )
        for direction, key in (("input", "inputs"), ("output", "outputs")):
            for p, value in enumerate(command.get(key, [])):
                if isinstance(value, Mapping):
                    db.execute(
                        "INSERT INTO validation_command_relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            result_id,
                            command_id,
                            direction,
                            p,
                            value.get("path"),
                            value.get("proof"),
                            value.get("target"),
                            value.get("artifact"),
                            int(bool(value.get("origin"))),
                        ),
                    )
        for p, value in enumerate(command.get("collections", [])):
            if isinstance(value, Mapping):
                db.execute(
                    "INSERT INTO validation_command_collections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        result_id,
                        command_id,
                        p,
                        value.get("direction"),
                        value.get("mechanism"),
                        value.get("root"),
                        value.get("target"),
                    ),
                )
                db.executemany(
                    "INSERT INTO validation_collection_members VALUES (?, ?, ?, ?, ?)",
                    [
                        (result_id, command_id, p, q, member)
                        for q, member in enumerate(value.get("members", []))
                    ],
                )


def _insert_group_edges(
    db: Any, result_id: str, group_id: str, group: Mapping[str, Any]
) -> None:
    # Edge rows reference both commands and artifacts, so insert them only
    # after their complete group memberships exist.
    for position, edge in enumerate(group.get("edges", [])):
        if isinstance(edge, Mapping):
            db.execute(
                "INSERT INTO validation_group_edges VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    group_id,
                    position,
                    edge.get("source"),
                    edge.get("target"),
                    edge.get("artifact"),
                ),
            )


def _batch_command_index(projection: Mapping[str, object]) -> dict[str, Any]:
    commands: dict[str, Mapping[str, object]] = {}
    anchors: dict[str, set[str]] = {}
    materials: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    for group in _sequence(projection["chains"], "chains"):
        assert isinstance(group, Mapping)
        for command in _sequence(group["commands"], "commands"):
            assert isinstance(command, Mapping)
            command_id = _string(command["identity"], "command identity")
            commands[command_id] = command
            anchor = {"kind": "command"}
            anchor.update(
                {key: command[key] for key in ("entry", "document", "fence", "ordinal")}
            )
            anchors.setdefault(_json(anchor), set()).add(command_id)
            command_paths = {
                _string(relationship["path"], "command path")
                for field in ("inputs", "outputs")
                for relationship in _sequence(command[field], "command relationships")
                if isinstance(relationship, Mapping)
            }
            paths[command_id] = command_paths
            for path in command_paths:
                materials.setdefault(
                    _json({"kind": "material", "path": path}), set()
                ).add(command_id)
    return {
        "commands": commands,
        "anchors": anchors,
        "materials": materials,
        "paths": paths,
    }


def _has_rejected_command(finding: Mapping[str, object]) -> bool:
    observed = finding.get("observed")
    return isinstance(observed, Mapping) and (
        isinstance(observed.get("rejected_command"), Mapping)
        or bool(_sequence(observed.get("rejected_commands", []), "rejected commands"))
    )


def _rejected_command_ids(finding: Mapping[str, object]) -> set[str]:
    observed = finding.get("observed")
    if not isinstance(observed, Mapping):
        return set()
    values = list(_sequence(observed.get("rejected_commands", []), "rejected commands"))
    if isinstance(observed.get("rejected_command"), Mapping):
        values.append(observed["rejected_command"])
    return {
        _string(command["identity"], "rejected command identity")
        for command in values
        if isinstance(command, Mapping)
    }


def _anchor_matched_commands(
    batch: Mapping[str, Any], index: Mapping[str, Any]
) -> set[str]:
    matched: set[str] = set()
    for raw_anchor in _sequence(batch["anchors"], "batch anchors"):
        assert isinstance(raw_anchor, Mapping)
        anchor = dict(raw_anchor)
        if anchor.get("kind") == "output_argument":
            anchor = {"kind": "command"}
            anchor.update(
                {
                    key: raw_anchor[key]
                    for key in ("entry", "document", "fence", "ordinal")
                }
            )
        elif anchor.get("kind") == "registration":
            anchor = {"kind": "material", "path": raw_anchor["path"]}
        anchor.pop("defect", None)
        key = _json(anchor)
        matched.update(index["anchors"].get(key, set()))
        matched.update(index["materials"].get(key, set()))
    return matched


def latest_full_validation(log_root: Path) -> StoredValidationResult:
    with result_snapshot(log_root) as db:
        row = db.execute(
            "SELECT result_id, validation_id, generation FROM validation_results WHERE slot='full'"
        ).fetchone()
        if row is None:
            raise ResultStoreError(
                "results.store.missing", "no completed full validation result"
            )
        _validate_stored_result(db, row[0])
        return StoredValidationResult(row[0], row[1], row[2])


def _validate_stored_result(db: Any, result_id: str) -> None:
    """Reject corruption that SQLite foreign keys cannot express polymorphically."""

    metadata = db.execute(
        "SELECT * FROM validation_results WHERE result_id=?",
        (result_id,),
    ).fetchone()
    if metadata is None:
        raise ResultStoreError("results.store.malformed", "validation result is absent")
    _validate_stored_rows(db, result_id, metadata)
    counts = db.execute(
        "SELECT (SELECT count(*) FROM validation_checks WHERE result_id=?), "
        "(SELECT count(*) FROM validation_findings WHERE result_id=?)",
        (result_id, result_id),
    ).fetchone()
    if (metadata["evaluated_checks"], metadata["finding_count"]) != tuple(counts):
        raise ResultStoreError(
            "results.store.malformed", "validation metadata counts disagree"
        )
    links = {
        (link["batch_id"], link["command_id"], link["code"])
        for link in db.execute(
            "SELECT batch_id, command_id, code FROM validation_batch_command_links "
            "WHERE result_id=?",
            (result_id,),
        )
    }
    expected = _required_command_batch_links(db, result_id)
    if links != expected:
        raise ResultStoreError(
            "results.store.malformed", "repair batch command links are not exact"
        )


def _malformed(message: str) -> None:
    raise ResultStoreError("results.store.malformed", message)


def _stored_string(value: object, name: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value:
        _malformed(f"invalid stored {name}")


def _stored_entry_id(value: object, name: str) -> None:
    """Require a stored stable entry identifier to use the canonical domain."""

    _stored_string(value, name)
    assert isinstance(value, str)
    if _ENTRY_ID_RE.fullmatch(value) is None:
        _malformed(f"invalid stored {name}")


def _stored_position_rows(
    db: Any, sql: str, args: tuple[object, ...], name: str
) -> None:
    """Require each persisted ordered child family to have dense positions."""

    positions = [row[0] for row in db.execute(sql, args)]
    if positions != list(range(len(positions))):
        _malformed(f"invalid stored {name} positions")


def _stored_json(value: object, name: str) -> object:
    if not isinstance(value, str):
        _malformed(f"invalid stored {name}")
    assert isinstance(value, str)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ResultStoreError(
            "results.store.malformed", f"invalid stored {name} JSON"
        ) from error


def _validate_stored_rows(db: Any, result_id: str, result: Any) -> None:
    """Decode every scalar and JSON row before any public projection is built."""

    _validate_stored_result_header(result)
    _validate_stored_children(db, result_id)
    _validate_stored_checks(db, result_id)
    _validate_stored_findings(db, result_id)
    _validate_stored_finding_effects(db, result_id)
    _validate_stored_batches(db, result_id)
    _validate_stored_scalars(db, result_id, result["kind"])


def _validate_stored_result_header(result: Any) -> None:
    kind = result["kind"]
    _validate_stored_result_required_fields(result, kind)
    _validate_stored_result_slot(result, kind)
    _validate_stored_result_optional_fields(result)
    _validate_required_projection_identity(result)
    _validate_stored_result_counts(result)


def _validate_stored_result_required_fields(result: Any, kind: object) -> None:
    required_fields: tuple[str, ...] = (
        "result_id",
        "slot",
        "kind",
        "summary",
        "status",
        "started_at",
        "finished_at",
        "stored_at",
    )
    if kind != "diagnostic":
        required_fields += ("result_date", "rules_version")
    for field in required_fields:
        _stored_string(result[field], f"validation result {field}")


def _validate_stored_result_slot(result: Any, kind: object) -> None:
    if kind not in {"full", "entry", "diagnostic"}:
        _malformed("invalid stored validation result kind")
    if kind == "full" and result["slot"] != "full":
        _malformed("invalid stored full result slot")
    if kind in {"full", "diagnostic"} and result["entry"] is not None:
        _malformed("invalid stored validation result entry")
    if kind == "entry":
        _stored_entry_id(result["entry"], "validation result entry")
        if result["slot"] != f"entry:{result['entry']}":
            _malformed("invalid stored entry result slot")
    if kind == "diagnostic" and result["slot"] != "diagnostic":
        _malformed("invalid stored diagnostic result slot")


def _validate_stored_result_optional_fields(result: Any) -> None:
    for field in (
        "reason",
        "source_identity",
        "validation_id",
        "record_identity",
        "projection_schema",
        "report_context_json",
    ):
        _stored_string(result[field], f"validation result {field}", nullable=True)


def _validate_required_projection_identity(result: Any) -> None:
    if result["kind"] in {"full", "entry"}:
        for field in ("source_identity", "validation_id", "record_identity", "projection_schema"):
            _stored_string(result[field], f"validation result {field}")


def _validate_stored_result_counts(result: Any) -> None:
    if not isinstance(result["generation"], int) or result["generation"] < 1:
        _malformed("invalid stored validation generation")
    for field in ("evaluated_checks", "finding_count"):
        if not isinstance(result[field], int) or result[field] < 0:
            _malformed(f"invalid stored {field}")
    if result["report_context_json"] is not None:
        _stored_json(result["report_context_json"], "report context")


def _validate_stored_children(db: Any, result_id: str) -> None:
    for table, column, name in (
        ("validation_result_entries", "entry", "result entry"),
        ("validation_result_limitations", "code", "result limitation"),
        ("validation_check_dependencies", "dependency_json", "check dependency"),
        ("validation_finding_dependencies", "dependency_json", "finding dependency"),
        ("validation_finding_affected_chains", "group_id", "finding chain"),
        ("validation_finding_affected_entries", "entry", "finding entry"),
        ("validation_command_tokens", "token", "command token"),
        ("validation_command_relationships", "path", "command relationship"),
        ("validation_command_collections", "mechanism", "command collection"),
        ("validation_collection_members", "path", "collection member"),
        ("validation_group_artifacts", "artifact", "group artifact"),
        ("validation_group_edges", "artifact", "group edge"),
        ("validation_group_signals", "signal", "group signal"),
        ("validation_group_registry", "identity_json", "group registry"),
        ("validation_batch_entries", "entry", "batch entry"),
        ("validation_batch_anchors", "anchor_json", "batch anchor"),
        ("validation_batch_findings", "finding_id", "batch finding"),
        ("validation_batch_groups", "group_id", "batch group"),
        ("validation_batch_related_batches", "related_batch_id", "related batch"),
    ):
        for row in db.execute(
            f"SELECT {column} FROM {table} WHERE result_id=?", (result_id,)
        ):
            value = row[0]
            if column.endswith("_json"):
                _stored_json(value, name)
            else:
                _stored_string(value, name)
    _validate_stored_positions(db, result_id)


def _validate_stored_positions(db: Any, result_id: str) -> None:
    for table, where in (
        ("validation_result_entries", "result_id=? AND relation"),
        ("validation_check_dependencies", "result_id=? AND check_id"),
        ("validation_finding_dependencies", "result_id=? AND finding_id"),
        ("validation_finding_affected_chains", "result_id=? AND finding_id"),
        ("validation_finding_affected_entries", "result_id=? AND finding_id"),
        ("validation_command_tokens", "result_id=? AND command_id"),
        (
            "validation_command_relationships",
            "result_id=? AND command_id AND direction",
        ),
        ("validation_command_collections", "result_id=? AND command_id"),
        (
            "validation_collection_members",
            "result_id=? AND command_id AND collection_position",
        ),
        ("validation_group_artifacts", "result_id=? AND group_id"),
        ("validation_group_edges", "result_id=? AND group_id"),
        ("validation_group_signals", "result_id=? AND group_id"),
        ("validation_group_registry", "result_id=? AND group_id"),
        ("validation_batch_entries", "result_id=? AND batch_id"),
        ("validation_batch_anchors", "result_id=? AND batch_id"),
        ("validation_batch_findings", "result_id=? AND batch_id"),
        ("validation_batch_groups", "result_id=? AND batch_id"),
        ("validation_batch_related_batches", "result_id=? AND batch_id"),
    ):
        _validate_stored_positions_for_parent(db, result_id, table, where)


def _validate_stored_positions_for_parent(
    db: Any, result_id: str, table: str, where: str
) -> None:
    parent = where.split(" AND ")[1:]
    columns = [item.split("=")[0] for item in parent]
    select = ", ".join(columns)
    parents = db.execute(
        f"SELECT DISTINCT {select} FROM {table} WHERE result_id=?", (result_id,)
    )
    for parent_row in parents:
        predicates = " AND ".join(f"{column}=?" for column in columns)
        _stored_position_rows(
            db,
            f"SELECT position FROM {table} WHERE result_id=? AND {predicates} ORDER BY position",
            (result_id, *tuple(parent_row)),
            table,
        )


def _validate_stored_checks(db: Any, result_id: str) -> None:
    for row in db.execute(
        "SELECT * FROM validation_checks WHERE result_id=?", (result_id,)
    ):
        for field in ("check_id", "scope", "status", "subject"):
            _stored_string(row[field], f"check {field}")
        if row["scope"] not in _CHECK_SCOPES:
            _malformed("invalid stored check scope")
        if row["status"] not in _CHECK_STATUSES:
            _malformed("invalid stored check status")
        failure = row["failure_code"]
        if failure is None:
            if row["status"] not in _NONFAILING_CHECK_STATUSES:
                _malformed("stored failing check has no failure payload")
            if any(
                row[field] is not None
                for field in ("rule", "observed_json", "failure_dependency")
            ):
                _malformed("stored successful check has failure payload")
        else:
            if row["status"] not in _FAILING_CHECK_STATUSES:
                _malformed("stored successful check has failure payload")
            _stored_string(failure, "check failure code")
            _stored_string(row["rule"], "check failure rule")
            _stored_json(row["observed_json"], "check observed")
            _stored_string(
                row["failure_dependency"], "check failure dependency", nullable=True
            )


def _validate_stored_findings(db: Any, result_id: str) -> None:
    for row in db.execute(
        "SELECT * FROM validation_findings WHERE result_id=?", (result_id,)
    ):
        for field in (
            "finding_id",
            "group_id",
            "scope",
            "status",
            "code",
            "projection_subject",
            "rule",
            "observed_json",
            "admission_effect",
        ):
            _stored_string(row[field], f"finding {field}")
        if row["scope"] not in _CHECK_SCOPES:
            _malformed("invalid stored finding scope")
        if row["status"] not in _FAILING_CHECK_STATUSES:
            _malformed("invalid stored finding status")
        for field in ("display_entry", "display_subject"):
            if not isinstance(row[field], str):
                _malformed(f"invalid stored finding {field}")
        _stored_json(row["observed_json"], "finding observed")
        _validate_finding_matches_check(db, result_id, row)


def _validate_finding_matches_check(db: Any, result_id: str, finding: Any) -> None:
    check = db.execute(
        "SELECT scope,status,subject,failure_code,rule,observed_json FROM validation_checks "
        "WHERE result_id=? AND check_id=?",
        (result_id, finding["finding_id"]),
    ).fetchone()
    if check is None or check["failure_code"] is None:
        _malformed("stored finding has no failed check")
    if (
        finding["scope"] != check["scope"]
        or finding["status"] != check["status"]
        or finding["projection_subject"] != check["subject"]
        or finding["code"] != check["failure_code"]
        or finding["rule"] != check["rule"]
        or _stored_json(finding["observed_json"], "finding observed")
        != _stored_json(check["observed_json"], "check observed")
    ):
        _malformed("stored finding does not match its failed check")
    dependencies = _values(
        db,
        "SELECT dependency_json FROM validation_finding_dependencies "
        "WHERE result_id=? AND finding_id=? ORDER BY position",
        (result_id, finding["finding_id"]),
        "dependency_json",
    )
    check_dependencies = _values(
        db,
        "SELECT dependency_json FROM validation_check_dependencies "
        "WHERE result_id=? AND check_id=? ORDER BY position",
        (result_id, finding["finding_id"]),
        "dependency_json",
    )
    if dependencies != check_dependencies:
        _malformed("stored finding dependencies do not match its failed check")


def _validate_stored_finding_effects(db: Any, result_id: str) -> None:
    """Require persisted affected members to retain the admission cardinality."""

    for finding in db.execute(
        "SELECT finding_id,group_id,admission_effect FROM validation_findings "
        "WHERE result_id=?",
        (result_id,),
    ):
        finding_id = finding["finding_id"]
        chains = _values(
            db,
            "SELECT group_id FROM validation_finding_affected_chains "
            "WHERE result_id=? AND finding_id=? ORDER BY position",
            (result_id, finding_id),
            "group_id",
        )
        entries = _values(
            db,
            "SELECT entry FROM validation_finding_affected_entries "
            "WHERE result_id=? AND finding_id=? ORDER BY position",
            (result_id, finding_id),
            "entry",
        )
        effect = finding["admission_effect"]
        if effect in {"none", "log"} and (chains or entries):
            _malformed("stored non-admitting finding has affected members")
        if effect == "entry" and (chains or len(entries) != 1):
            _malformed("stored entry finding has invalid affected members")
        if effect == "chain" and (len(chains) != 1 or len(entries) != 1):
            _malformed("stored chain finding has invalid affected members")
        if effect not in {"none", "log", "entry", "chain"}:
            _malformed("stored finding has invalid admission effect")
        if effect == "chain":
            group = db.execute(
                "SELECT entry FROM validation_groups WHERE result_id=? AND group_id=?",
                (result_id, chains[0]),
            ).fetchone()
            if group is None or group["entry"] != entries[0]:
                _malformed("stored chain finding affects the wrong entry")


def _validate_stored_batches(db: Any, result_id: str) -> None:
    """Require every stored repair batch to retain its projection cardinalities."""

    for batch in db.execute(
        "SELECT batch_id,primary_finding_count,starting_finding_id "
        "FROM validation_batches WHERE result_id=?",
        (result_id,),
    ):
        rows = list(
            db.execute(
                "SELECT f.finding_id,f.observed_json FROM validation_batch_findings bf "
                "JOIN validation_findings f ON f.result_id=bf.result_id "
                "AND f.finding_id=bf.finding_id WHERE bf.result_id=? "
                "AND bf.batch_id=? ORDER BY bf.position",
                (result_id, batch["batch_id"]),
            )
        )
        finding_ids = [row["finding_id"] for row in rows]
        if (
            not finding_ids
            or len(finding_ids) != len(set(finding_ids))
            or batch["primary_finding_count"] != len(finding_ids)
            or batch["starting_finding_id"] not in finding_ids
        ):
            _malformed("stored repair batch has inconsistent primary findings")
        expected_start = next(
            (
                row["finding_id"]
                for row in rows
                if _stored_finding_has_rejected_command(row["observed_json"])
            ),
            finding_ids[0],
        )
        if batch["starting_finding_id"] != expected_start:
            _malformed("stored repair batch has inconsistent starting finding")
        related = _values(
            db,
            "SELECT related_batch_id FROM validation_batch_related_batches "
            "WHERE result_id=? AND batch_id=? ORDER BY position",
            (result_id, batch["batch_id"]),
            "related_batch_id",
        )
        if batch["batch_id"] in related or len(related) != len(set(related)):
            _malformed("stored repair batch has invalid related batches")


def _stored_finding_has_rejected_command(raw: object) -> bool:
    observed = _stored_json(raw, "finding observed")
    return isinstance(observed, Mapping) and (
        isinstance(observed.get("rejected_command"), Mapping)
        or bool(_sequence(observed.get("rejected_commands", []), "rejected commands"))
    )


def _validate_stored_scalars(db: Any, result_id: str, kind: object) -> None:
    scalar_fields = {
        "validation_groups": ("group_id", "group_kind", "entry"),
        "validation_commands": (
            "command_id",
            "entry",
            "document",
            "script",
            "group_id",
        ),
        "validation_batches": (
            "batch_id",
            "batch_type",
            "grouping_reason",
            "scope",
            "starting_finding_id",
        ),
        "validation_command_relationships": (
            "command_id",
            "direction",
            "path",
            "proof",
        ),
        "validation_command_collections": (
            "command_id",
            "direction",
            "mechanism",
            "target",
        ),
    }
    if kind != "diagnostic":
        for table, fields in scalar_fields.items():
            for row in db.execute(
                f"SELECT * FROM {table} WHERE result_id=?", (result_id,)
            ):
                for field in fields:
                    _stored_string(row[field], f"{table} {field}")
                for field in ("position", "fence", "ordinal", "primary_finding_count"):
                    if field in row.keys() and (
                        not isinstance(row[field], int) or row[field] < 0
                    ):
                        _malformed(f"invalid stored {table} {field}")
                for field in ("reason", "root", "target", "artifact"):
                    if field in row.keys():
                        _stored_string(row[field], f"{table} {field}", nullable=True)
        _validate_stored_group_reasons(db, result_id)


def _validate_stored_group_reasons(db: Any, result_id: str) -> None:
    for row in db.execute(
        "SELECT group_kind,reason FROM validation_groups WHERE result_id=?", (result_id,)
    ):
        if row["group_kind"] == "chain" and row["reason"] is not None:
            _malformed("stored chain group has a reason")
        if (
            row["group_kind"] == "unresolved"
            and row["reason"] != "finding_scope_unresolved"
        ):
            _malformed("stored unresolved group has invalid reason")


def _stored_batch_link_index(
    db: Any, result_id: str
) -> tuple[
    dict[str, Any], dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]
]:
    """Index persisted command identities and their linkable material paths."""

    commands = {
        row["command_id"]: row
        for row in db.execute(
            "SELECT command_id,entry,document,fence,ordinal FROM validation_commands WHERE result_id=?",
            (result_id,),
        )
    }
    anchor_index: dict[str, set[str]] = {}
    material_index: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {command_id: set() for command_id in commands}
    for command_id, row in commands.items():
        anchor = _json(
            {
                "kind": "command",
                "entry": row["entry"],
                "document": row["document"],
                "fence": row["fence"],
                "ordinal": row["ordinal"],
            }
        )
        anchor_index.setdefault(anchor, set()).add(command_id)
        for relationship in db.execute(
            "SELECT path FROM validation_command_relationships WHERE result_id=? AND command_id=?",
            (result_id, command_id),
        ):
            paths[command_id].add(relationship["path"])
        for path in paths[command_id]:
            material_index.setdefault(
                _json({"kind": "material", "path": path}), set()
            ).add(command_id)
    return commands, anchor_index, material_index, paths


def _required_command_batch_links(
    db: Any, result_id: str
) -> set[tuple[str, str, str]]:
    """Derive every direct batch-command link permitted by stored rows."""

    commands, anchor_index, material_index, paths = _stored_batch_link_index(
        db, result_id
    )
    context = _StoredBatchLinkContext(
        db, result_id, commands, anchor_index, material_index, paths
    )
    required: set[tuple[str, str, str]] = set()
    for batch in db.execute(
        "SELECT batch_id FROM validation_batches WHERE result_id=?", (result_id,)
    ):
        batch_id = batch["batch_id"]
        codes = _stored_batch_codes(db, result_id, batch_id)
        command_codes = _rejected_command_codes(context, batch_id)
        _add_anchor_command_codes(context, batch_id, codes, command_codes)
        for command_id, command_codes_for_id in command_codes.items():
            for code in command_codes_for_id:
                required.add((batch_id, command_id, code))
    return required


def _stored_batch_codes(db: Any, result_id: str, batch_id: str) -> list[str]:
    return [
        row["code"]
        for row in db.execute(
            "SELECT f.code FROM validation_batch_findings bf JOIN validation_findings f "
            "ON f.result_id=bf.result_id AND f.finding_id=bf.finding_id "
            "WHERE bf.result_id=? AND bf.batch_id=? ORDER BY bf.position",
            (result_id, batch_id),
        )
    ]


def _rejected_command_codes(
    context: _StoredBatchLinkContext, batch_id: str
) -> dict[str, set[str]]:
    command_codes: dict[str, set[str]] = {}
    for finding in context.db.execute(
        "SELECT f.code,f.observed_json FROM validation_batch_findings bf "
        "JOIN validation_findings f ON f.result_id=bf.result_id "
        "AND f.finding_id=bf.finding_id WHERE bf.result_id=? AND bf.batch_id=?",
        (context.result_id, batch_id),
    ):
        observed = _stored_json(finding["observed_json"], "finding observed")
        if not isinstance(observed, Mapping):
            continue
        rejected = list(observed.get("rejected_commands", []))
        singular = observed.get("rejected_command")
        if isinstance(singular, Mapping):
            rejected.append(singular)
        for value in rejected:
            if (
                isinstance(value, Mapping)
                and isinstance(value.get("identity"), str)
                and value["identity"] in context.commands
            ):
                command_codes.setdefault(value["identity"], set()).add(finding["code"])
    return command_codes


def _add_anchor_command_codes(
    context: _StoredBatchLinkContext,
    batch_id: str,
    codes: Sequence[str],
    command_codes: dict[str, set[str]],
) -> None:
    for anchor_row in context.db.execute(
        "SELECT anchor_json FROM validation_batch_anchors "
        "WHERE result_id=? AND batch_id=? ORDER BY position",
        (context.result_id, batch_id),
    ):
        key = _stored_batch_anchor_key(anchor_row["anchor_json"])
        for command_id in context.anchor_index.get(
            key, set()
        ) | context.material_index.get(key, set()):
            command_codes.setdefault(command_id, set()).update(codes)


def _stored_batch_anchor_key(raw: object) -> str:
    anchor = _stored_json(raw, "batch anchor")
    if not isinstance(anchor, Mapping):
        _malformed("stored batch anchor is not an object")
    assert isinstance(anchor, Mapping)
    value = dict(cast(Mapping[str, object], anchor))
    if value.get("kind") == "output_argument":
        value = {
            "kind": "command",
            **{
                key: value.get(key) for key in ("entry", "document", "fence", "ordinal")
            },
        }
    elif value.get("kind") == "registration":
        value = {"kind": "material", "path": value.get("path")}
    value.pop("defect", None)
    return _json(value)


def validate_validation_result(log_root: Path, result_id: str) -> None:
    """Validate normalized rows before exposing any public projection."""
    with result_snapshot(log_root) as db:
        _validate_stored_result(db, result_id)


def load_validation_admission(log_root: Path, result_id: str) -> ValidationAdmission:
    """Load only stored admission facts; no report or aggregate projection read."""
    with result_snapshot(log_root) as db:
        result = db.execute(
            "SELECT validation_id FROM validation_results WHERE result_id=? AND slot='full'",
            (result_id,),
        ).fetchone()
        if result is None:
            raise ResultStoreError(
                "results.store.missing", "full validation result is absent"
            )
        _validate_stored_result(db, result_id)
        groups = tuple(
            ValidationAdmissionGroup(row["group_id"], row["group_kind"], row["entry"])
            for row in db.execute(
                "SELECT group_id, group_kind, entry FROM validation_groups "
                "WHERE result_id=? ORDER BY group_kind, position",
                (result_id,),
            )
        )
        findings = []
        for row in db.execute(
            "SELECT finding_id, group_id, status, admission_effect FROM validation_findings "
            "WHERE result_id=? ORDER BY finding_id",
            (result_id,),
        ):
            finding_id = row["finding_id"]
            findings.append(
                ValidationAdmissionFinding(
                    finding_id,
                    row["group_id"],
                    row["status"],
                    row["admission_effect"],
                    tuple(
                        _values(
                            db,
                            "SELECT group_id FROM validation_finding_affected_chains WHERE result_id=? AND finding_id=? ORDER BY position",
                            (result_id, finding_id),
                            "group_id",
                        )
                    ),
                    tuple(
                        _values(
                            db,
                            "SELECT entry FROM validation_finding_affected_entries WHERE result_id=? AND finding_id=? ORDER BY position",
                            (result_id, finding_id),
                            "entry",
                        )
                    ),
                )
            )
        commands = tuple(
            ValidationAdmissionCommand(
                row["command_id"],
                row["group_id"],
                row["entry"],
                tuple(
                    _values(
                        db,
                        "SELECT path FROM validation_command_relationships "
                        "WHERE result_id=? AND command_id=? AND direction='output' "
                        "ORDER BY position",
                        (result_id, row["command_id"]),
                        "path",
                    )
                )
                + tuple(
                    _values(
                        db,
                        "SELECT root FROM validation_command_collections "
                        "WHERE result_id=? AND command_id=? AND direction='output' "
                        "AND root IS NOT NULL ORDER BY position",
                        (result_id, row["command_id"]),
                        "root",
                    )
                )
                + tuple(
                    _values(
                        db,
                        "SELECT member.path FROM validation_collection_members AS member "
                        "JOIN validation_command_collections AS collection "
                        "ON collection.result_id=member.result_id "
                        "AND collection.command_id=member.command_id "
                        "AND collection.position=member.collection_position "
                        "WHERE member.result_id=? AND member.command_id=? "
                        "AND collection.direction='output' ORDER BY collection.position, member.position",
                        (result_id, row["command_id"]),
                        "path",
                    )
                ),
            )
            for row in db.execute(
                "SELECT command_id, group_id, entry FROM validation_commands "
                "WHERE result_id=? ORDER BY command_id",
                (result_id,),
            )
        )
        return ValidationAdmission(
            result["validation_id"], result_id, groups, commands, tuple(findings)
        )


def load_finding_groups(
    log_root: Path,
) -> tuple[StoredValidationResult, dict[str, object], list[dict[str, object]]]:
    """Load the current full finding groups from normalized rows only."""
    stored = latest_full_validation(log_root)
    with result_snapshot(log_root) as db:
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_id=?",
            (stored.result_id,),
        ).fetchone()
        if meta is None:
            raise ResultStoreError(
                "results.store.malformed", "current validation disappeared"
            )
        groups = [
            _group(db, stored.result_id, row)
            for row in db.execute(
                "SELECT * FROM validation_groups WHERE result_id=? ORDER BY group_kind,position",
                (stored.result_id,),
            )
        ]
    return stored, dict(meta), groups


def list_finding_groups(
    log_root: Path, *, filters: Mapping[str, Sequence[str]]
) -> tuple[StoredValidationResult, dict[str, object], list[dict[str, object]]]:
    """Hydrate only groups selected by exact normalized SQL predicates."""
    stored = latest_full_validation(log_root)
    clauses: list[str] = ["g.result_id=?"]
    args: list[object] = [stored.result_id]
    entries = tuple(filters.get("entry", ()))
    if entries:
        clauses.append("g.entry IN (" + ",".join("?" for _ in entries) + ")")
        args.extend(entries)
    commands = tuple(filters.get("command", ()))
    if commands:
        clauses.append(
            "EXISTS (SELECT 1 FROM validation_commands c WHERE c.result_id=g.result_id "
            "AND c.group_id=g.group_id AND c.command_id IN ("
            + ",".join("?" for _ in commands)
            + "))"
        )
        args.extend(commands)
    finding_clauses: list[str] = []
    finding_args: list[object] = []
    for field, column in (
        ("area", "scope"),
        ("code", "code"),
        ("subject", "projection_subject"),
    ):
        values = tuple(filters.get(field, ()))
        if values:
            finding_clauses.append(
                column + " IN (" + ",".join("?" for _ in values) + ")"
            )
            finding_args.extend(values)
    families = tuple(filters.get("family", ()))
    if families:
        finding_clauses.append(
            "(" + " OR ".join("code=? OR code LIKE ?" for _ in families) + ")"
        )
        for family in families:
            finding_args.extend((family, family + ".%"))
    if finding_clauses:
        clauses.append(
            "EXISTS (SELECT 1 FROM validation_findings f WHERE f.result_id=g.result_id "
            "AND f.group_id=g.group_id AND " + " AND ".join(finding_clauses) + ")"
        )
        args.extend(finding_args)
    with result_snapshot(log_root) as db:
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_id=?",
            (stored.result_id,),
        ).fetchone()
        rows = list(
            db.execute(
                "SELECT * FROM validation_groups g WHERE "
                + " AND ".join(clauses)
                + " ORDER BY g.group_kind,g.position LIMIT 101",
                args,
            )
        )
        if len(rows) > 100:
            raise ResultStoreError(
                "findings.response.too_large", "matching finding groups exceed 100"
            )
        groups = [_group(db, stored.result_id, row) for row in rows]
    if meta is None:
        raise ResultStoreError(
            "results.store.malformed", "current validation disappeared"
        )
    return stored, dict(meta), groups


def load_finding_group(
    log_root: Path, *, entry: str, group_id: str
) -> tuple[StoredValidationResult, dict[str, object], dict[str, object] | None]:
    """Load one exact current finding group without scanning sibling groups."""
    stored = latest_full_validation(log_root)
    with result_snapshot(log_root) as db:
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_id=?",
            (stored.result_id,),
        ).fetchone()
        row = db.execute(
            "SELECT * FROM validation_groups WHERE result_id=? AND entry=? AND group_id=?",
            (stored.result_id, entry, group_id),
        ).fetchone()
        group = _group(db, stored.result_id, row) if row is not None else None
    if meta is None:
        raise ResultStoreError(
            "results.store.malformed", "current validation disappeared"
        )
    return stored, dict(meta), group


def load_direct_finding(
    log_root: Path, check_id: str
) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, object]]:
    """Return current check, direct finding, and metadata without source reads."""
    stored = latest_full_validation(log_root)
    with result_snapshot(log_root) as db:
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_id=?",
            (stored.result_id,),
        ).fetchone()
        check = db.execute(
            "SELECT * FROM validation_checks WHERE result_id=? AND check_id=?",
            (stored.result_id, check_id),
        ).fetchone()
        if check is None:
            return None, None, dict(meta)
        finding = db.execute(
            "SELECT * FROM validation_findings WHERE result_id=? AND finding_id=?",
            (stored.result_id, check_id),
        ).fetchone()
        if finding is None:
            return dict(check), None, dict(meta)
        value = dict(finding)
        value["dependencies"] = [
            json.loads(x[0])
            for x in db.execute(
                "SELECT dependency_json FROM validation_finding_dependencies WHERE result_id=? AND finding_id=? ORDER BY position",
                (stored.result_id, check_id),
            )
        ]
        return dict(check), value, dict(meta)


def load_mechanical_record(log_root: Path, result_id: str) -> MechanicalGeneratedRecord:
    with result_snapshot(log_root) as db:
        _validate_stored_result(db, result_id)
        row = db.execute(
            "SELECT summary, result_date, rules_version FROM validation_results WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if row is None:
            raise ResultStoreError(
                "results.store.missing", "validation result is absent"
            )
        checks = []
        for check in db.execute(
            "SELECT * FROM validation_checks WHERE result_id=? ORDER BY check_id",
            (result_id,),
        ):
            value: dict[str, object] = {
                "identity": check["check_id"],
                "scope": check["scope"],
                "status": check["status"],
                "subject": check["subject"],
                "dependencies": [
                    json.loads(x[0])
                    for x in db.execute(
                        "SELECT dependency_json FROM validation_check_dependencies WHERE result_id=? AND check_id=? ORDER BY position",
                        (result_id, check["check_id"]),
                    )
                ],
            }
            if check["failure_code"] is not None:
                failure: dict[str, object] = {
                    "code": check["failure_code"],
                    "rule": check["rule"],
                    "subject": check["subject"],
                    "observed": json.loads(check["observed_json"]),
                }
                if check["failure_dependency"] is not None:
                    failure["dependency"] = check["failure_dependency"]
                value["failure"] = failure
            checks.append(value)
    return MechanicalGeneratedRecord.build(
        row["summary"],
        row["rules_version"],
        row["result_date"],
        tuple(MechanicalCheck.from_dict(value) for value in checks),
    )


def load_validation_report_projection(log_root: Path) -> ValidationReportProjection:
    """Load a full report projection without consulting current research files."""

    from .human_projection import EntryPresentation, ReportContext, project_findings

    stored = latest_full_validation(log_root)
    record = load_mechanical_record(log_root, stored.result_id)
    with result_snapshot(log_root) as db:
        row = db.execute(
            "SELECT report_context_json FROM validation_results WHERE result_id=?",
            (stored.result_id,),
        ).fetchone()
        displays = {
            value["finding_id"]: (value["display_entry"], value["display_subject"])
            for value in db.execute(
                "SELECT finding_id,display_entry,display_subject FROM validation_findings WHERE result_id=?",
                (stored.result_id,),
            )
        }
    if row is None or row["report_context_json"] is None:
        raise ResultStoreError(
            "results.store.malformed", "validation report context is absent"
        )
    try:
        captured = json.loads(row["report_context_json"])
        title = captured["title"]
        summary = captured["summary"]
        entries = captured["entries"]
        if not isinstance(title, str) or not isinstance(summary, str):
            raise ValueError("invalid title or summary")
        if not isinstance(entries, list):
            raise ValueError("invalid entry projection")
        summary_path = Path(summary).absolute()
        log_root_path = summary_path.with_suffix("")
        context_entries = {}
        for item in entries:
            if not isinstance(item, Mapping):
                raise ValueError("invalid entry projection")
            entry_id, entry_title, document = (
                item.get("id"),
                item.get("title"),
                item.get("document"),
            )
            if not all(
                isinstance(value, str) and value
                for value in (entry_id, entry_title, document)
            ):
                raise ValueError("invalid entry projection")
            entry_id = _string(entry_id, "entry projection id")
            entry_title = _string(entry_title, "entry projection title")
            document = _string(document, "entry projection document")
            document_path = Path(document)
            context_entries[entry_id] = EntryPresentation(
                entry_id,
                entry_title,
                document,
                log_root_path.joinpath(*document_path.parts[:-1]),
            )
        context = ReportContext(title, summary_path, log_root_path, context_entries)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ResultStoreError(
            "results.store.malformed", f"invalid validation report context: {error}"
        ) from error
    groups = []
    for group in project_findings(record, context):
        values = {displays.get(identity) for identity in group.check_ids}
        if None in values or len(values) != 1:
            raise ResultStoreError(
                "results.store.malformed",
                "validation finding display projection is absent",
            )
        entry, subject = cast(tuple[str, str], values.pop())
        groups.append(replace(group, entry=entry or None, subject=subject))
    return ValidationReportProjection(stored, record, context, tuple(groups))


def _load_projection_for_export(
    log_root: Path, result_id: str | None = None
) -> dict[str, object]:
    selected = result_id or latest_full_validation(log_root).result_id
    with result_snapshot(log_root) as db:
        _validate_stored_result(db, selected)
        row = db.execute(
            "SELECT validation_id, record_identity, projection_schema, summary, result_date, rules_version, source_identity FROM validation_results WHERE result_id=?",
            (selected,),
        ).fetchone()
        if row is None:
            raise ResultStoreError(
                "results.store.missing", "validation result is absent"
            )
        groups = list(
            db.execute(
                "SELECT * FROM validation_groups WHERE result_id=? ORDER BY group_kind, position",
                (selected,),
            )
        )
        batches = [
            _batch(db, selected, x)
            for x in db.execute(
                "SELECT * FROM validation_batches WHERE result_id=? ORDER BY position",
                (selected,),
            )
        ]
        values = [(x["group_kind"], _group(db, selected, x)) for x in groups]
    return {
        "schema": row["projection_schema"],
        "validation_id": row["validation_id"],
        "record_identity": row["record_identity"],
        "summary": row["summary"],
        "result_date": row["result_date"],
        "rules_version": row["rules_version"],
        "source_identity": row["source_identity"],
        "chains": [x for kind, x in values if kind == "chain"],
        "unresolved": [x for kind, x in values if kind == "unresolved"],
        "repair_batches": batches,
    }


def _values(db: Any, sql: str, args: tuple[object, ...], column: str) -> list[Any]:
    return [row[column] for row in db.execute(sql, args)]


def _group(db: Any, result_id: str, row: Any) -> dict[str, Any]:
    group_id = row["group_id"]
    commands = []
    for command in db.execute(
        "SELECT * FROM validation_commands WHERE result_id=? AND group_id=? ORDER BY position",
        (result_id, group_id),
    ):
        command_id = command["command_id"]
        value = {
            key: command[key]
            for key in ("document", "entry", "fence", "identity", "ordinal", "script")
            if key != "identity"
        }
        value["identity"] = command_id
        value["tokens"] = _values(
            db,
            "SELECT token FROM validation_command_tokens WHERE result_id=? AND command_id=? ORDER BY position",
            (result_id, command_id),
            "token",
        )
        for direction, key in (("input", "inputs"), ("output", "outputs")):
            value[key] = [
                {
                    k: relationship[k]
                    for k in ("direction", "path", "proof", "target", "artifact")
                    if relationship[k] is not None
                }
                | ({"origin": True} if relationship["origin"] else {})
                for relationship in db.execute(
                    "SELECT * FROM validation_command_relationships WHERE result_id=? AND command_id=? AND direction=? ORDER BY position",
                    (result_id, command_id, direction),
                )
            ]
        collections = []
        for collection in db.execute(
            "SELECT * FROM validation_command_collections WHERE result_id=? AND command_id=? ORDER BY position",
            (result_id, command_id),
        ):
            collections.append(
                {
                    "direction": collection["direction"],
                    "mechanism": collection["mechanism"],
                    "root": collection["root"],
                    "target": collection["target"],
                    "members": _values(
                        db,
                        "SELECT path FROM validation_collection_members WHERE result_id=? AND command_id=? AND collection_position=? ORDER BY position",
                        (result_id, command_id, collection["position"]),
                        "path",
                    ),
                }
            )
        value["collections"] = collections
        commands.append(value)
    findings = []
    for finding in db.execute(
        "SELECT * FROM validation_findings WHERE result_id=? AND group_id=? ORDER BY position",
        (result_id, group_id),
    ):
        finding_id = finding["finding_id"]
        findings.append(
            {
                "identity": finding_id,
                "scope": finding["scope"],
                "status": finding["status"],
                "code": finding["code"],
                "subject": finding["projection_subject"],
                "rule": finding["rule"],
                "observed": json.loads(finding["observed_json"]),
                "admission_effect": finding["admission_effect"],
                "dependencies": [
                    json.loads(v)
                    for v in _values(
                        db,
                        "SELECT dependency_json FROM validation_finding_dependencies WHERE result_id=? AND finding_id=? ORDER BY position",
                        (result_id, finding_id),
                        "dependency_json",
                    )
                ],
                "affected_chains": _values(
                    db,
                    "SELECT group_id FROM validation_finding_affected_chains WHERE result_id=? AND finding_id=? ORDER BY position",
                    (result_id, finding_id),
                    "group_id",
                ),
                "affected_entries": _values(
                    db,
                    "SELECT entry FROM validation_finding_affected_entries WHERE result_id=? AND finding_id=? ORDER BY position",
                    (result_id, finding_id),
                    "entry",
                ),
            }
        )
    result = {"chain_id": group_id, "entry": row["entry"], "findings": findings}
    if row["group_kind"] == "unresolved":
        result["reason"] = row["reason"]
    else:
        result.update(
            {
                "commands": commands,
                "artifacts": _values(
                    db,
                    "SELECT artifact FROM validation_group_artifacts WHERE result_id=? AND group_id=? ORDER BY position",
                    (result_id, group_id),
                    "artifact",
                ),
                "edges": [
                    dict(x)
                    for x in db.execute(
                        "SELECT source_command_id AS source, target_command_id AS target, artifact FROM validation_group_edges WHERE result_id=? AND group_id=? ORDER BY position",
                        (result_id, group_id),
                    )
                ],
                "signals": _values(
                    db,
                    "SELECT signal FROM validation_group_signals WHERE result_id=? AND group_id=? ORDER BY position",
                    (result_id, group_id),
                    "signal",
                ),
                "registry": [
                    {
                        "entry": registry["owner_entry"],
                        "name": registry["name"],
                        "kind": registry["kind"],
                        "location": registry["location"],
                        "path": registry["path"],
                        "origin": bool(registry["origin"]),
                        "identity": json.loads(registry["identity_json"]),
                    }
                    | (
                        {
                            "from_entry": registry["from_entry"],
                            "read_only": True,
                        }
                        if registry["from_entry"] is not None
                        else {}
                    )
                    for registry in db.execute(
                        "SELECT * FROM validation_group_registry WHERE result_id=? AND group_id=? ORDER BY position",
                        (result_id, group_id),
                    )
                ],
            }
        )
    return result


def _batch(db: Any, result_id: str, row: Any) -> dict[str, Any]:
    batch_id = row["batch_id"]
    result = {
        "batch_id": batch_id,
        "batch_type": row["batch_type"],
        "grouping_reason": row["grouping_reason"],
        "scope": row["scope"],
        "primary_finding_count": row["primary_finding_count"],
        "starting_finding_id": row["starting_finding_id"],
    }
    for field, table, column in (
        ("entries", "validation_batch_entries", "entry"),
        ("anchors", "validation_batch_anchors", "anchor_json"),
        ("primary_finding_ids", "validation_batch_findings", "finding_id"),
        ("related_chain_ids", "validation_batch_groups", "group_id"),
        ("related_batch_ids", "validation_batch_related_batches", "related_batch_id"),
    ):
        values = _values(
            db,
            f"SELECT {column} FROM {table} WHERE result_id=? AND batch_id=? ORDER BY position",
            (result_id, batch_id),
            column,
        )
        result[field] = (
            [json.loads(v) for v in values] if field == "anchors" else values
        )
    return result


def export_validation_result(log_root: Path, result_id: str) -> dict[str, object]:
    """Emit the explicit retained-result/2 projection from normalized rows."""
    with result_snapshot(log_root) as db:
        _validate_stored_result(db, result_id)
        row = db.execute(
            "SELECT result_id,generation,slot,kind,entry,summary,status,reason,"
            "evaluated_checks,finding_count,started_at,finished_at,stored_at,"
            "result_date,rules_version,source_identity,validation_id,record_identity,"
            "projection_schema,report_context_json "
            "FROM validation_results WHERE result_id=?",
            (result_id,),
        ).fetchone()
        assert row is not None
        metadata = dict(row)
        metadata["entries"] = {
            relation: _values(
                db,
                "SELECT entry FROM validation_result_entries WHERE result_id=? "
                "AND relation=? ORDER BY position",
                (result_id, relation),
                "entry",
            )
            for relation in ("requested", "evaluated", "dependency")
        }
        metadata["whole_log_limitations"] = _values(
            db,
            "SELECT code FROM validation_result_limitations WHERE result_id=? ORDER BY position",
            (result_id,),
            "code",
        )
        groups = [
            _group(db, result_id, group)
            for group in db.execute(
                "SELECT * FROM validation_groups WHERE result_id=? "
                "ORDER BY group_kind,position",
                (result_id,),
            )
        ]
        batches = {
            row["batch_id"]: _batch(db, result_id, row)
            for row in db.execute(
                "SELECT * FROM validation_batches WHERE result_id=? ORDER BY position",
                (result_id,),
            )
        }
        checks = {
            row["check_id"]: {
                "identity": row["check_id"],
                "scope": row["scope"],
                "status": row["status"],
                "subject": row["subject"],
                "failure": (
                    None
                    if row["failure_code"] is None
                    else {
                        "code": row["failure_code"],
                        "rule": row["rule"],
                        "observed": json.loads(row["observed_json"]),
                        "dependency": row["failure_dependency"],
                    }
                ),
                "dependencies": [
                    json.loads(value)
                    for value in _values(
                        db,
                        "SELECT dependency_json FROM validation_check_dependencies "
                        "WHERE result_id=? AND check_id=? ORDER BY position",
                        (result_id, row["check_id"]),
                        "dependency_json",
                    )
                ],
            }
            for row in db.execute(
                "SELECT * FROM validation_checks WHERE result_id=? ORDER BY check_id",
                (result_id,),
            )
        }
    findings = {
        finding["identity"]: finding
        for group in groups
        for finding in group["findings"]
    }
    commands = {
        command["identity"]: command
        for group in groups
        for command in group.get("commands", [])
    }
    artifacts = {
        artifact: {"path": artifact}
        for group in groups
        for artifact in group.get("artifacts", [])
    }
    codes: dict[str, list[str]] = {}
    for finding_id, finding in findings.items():
        codes.setdefault(str(finding["code"]), []).append(finding_id)
    exported: dict[str, object] = {
        "schema": "research-log-retained-result/2",
        "result_id": result_id,
        "metadata": metadata,
        "checks": checks,
        "findings": findings,
        "chains": {group["chain_id"]: group for group in groups},
        "commands": commands,
        "artifacts": artifacts,
        "batches": batches,
        "codes": codes,
    }
    if len(_json(exported).encode("utf-8")) > MAX_VALIDATION_RESULT_BYTES:
        raise ResultStoreError(
            "results.export.too_large",
            "validation result export exceeds 64 MiB",
        )
    return exported


def publish_diagnostic_commands(
    log_root: Path, summary: str, reason: str, commands: object
) -> str:
    """Replace the one authoring-diagnostic slot with typed rejected commands."""
    if not isinstance(commands, (list, tuple)):
        raise ValueError("diagnostic commands must be a sequence")
    result_id, now = str(uuid.uuid4()), _now()
    with results_lock(log_root):
        with result_transaction(log_root) as db:
            prior = db.execute(
                "SELECT generation FROM store_state WHERE domain='validation'"
            ).fetchone()
            generation = 1 if prior is None else int(prior[0]) + 1
            db.execute("DELETE FROM validation_results WHERE slot='diagnostic'")
            db.execute(
                "INSERT INTO validation_results "
                "(result_id, generation, slot, kind, entry, summary, status, reason, "
                "evaluated_checks, finding_count, started_at, finished_at, stored_at, "
                "result_date, rules_version, source_identity, validation_id, "
                "record_identity, projection_schema, report_context_json) "
                "VALUES (?, ?, 'diagnostic', 'diagnostic', NULL, ?, 'failed', ?, "
                "0, 0, ?, ?, ?, '', '', NULL, NULL, NULL, NULL, NULL)",
                (result_id, generation, summary, reason, now, now, now),
            )
            # Diagnostic commands are still normalized command rows.  Their
            # synthetic group is owned by this replaceable result and cascades
            # with it, so a subsequent diagnostic publication cannot strand rows.
            db.execute(
                "INSERT INTO validation_groups VALUES (?, 'diagnostic', 'unresolved', '', ?, 0)",
                (result_id, reason),
            )
            for position, raw in enumerate(commands):
                if not isinstance(raw, Mapping):
                    continue
                identity = str(raw.get("identity", f"diagnostic:{position}"))
                entry = str(raw.get("entry", ""))
                db.execute(
                    "INSERT INTO validation_commands VALUES (?, ?, ?, ?, ?, ?, ?, 'diagnostic', ?)",
                    (
                        result_id,
                        identity,
                        entry,
                        str(raw.get("document", "")),
                        int(raw.get("fence", 0)),
                        int(raw.get("ordinal", position)),
                        str(raw.get("script", "")),
                        position,
                    ),
                )
            db.execute(
                "INSERT INTO store_state VALUES ('validation', ?, ?) ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation, summary=excluded.summary",
                (generation, summary),
            )
            db.execute("DELETE FROM report_materializations WHERE kind='validation'")
    return result_id
