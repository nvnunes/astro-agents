"""Typed SQLite persistence and projections for validation observations."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from research_log_data import parse_resource_identity
from research_log_result_store import (
    ResultStoreError,
    result_snapshot,
    result_transaction,
    results_lock,
)
from result_export_encoder import CappedJsonEncoder, ExportTooLarge, measure_json_value

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
@dataclass(frozen=True)
class StoredValidationResult:
    result_id: str
    validation_id: str
    generation: int


@dataclass(frozen=True)
class ResolvedValidationResult:
    """One public result identity resolved inside a caller-owned snapshot."""

    result_pk: int
    result_id: str
    validation_id: str | None
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
class _V14InsertContext:
    db: Any
    result_pk: int
    checks: Mapping[str, MechanicalCheck]
    check_pks: Mapping[str, int]
    code_pks: Mapping[str, int]
    group_pks: Mapping[str, int]
    command_pks: Mapping[str, int]
    artifact_pks: Mapping[str, int]
    batch_pks: Mapping[str, int]
    display_values: Mapping[str, tuple[str, str]]
    projected_findings: Mapping[str, Mapping[str, object]]
    command_index: Mapping[str, Any]


@dataclass(frozen=True)
class _BatchAuditContext:
    db: Any
    result_pk: int
    command_pks: Mapping[str, int]
    anchor_index: Mapping[str, set[int]]
    material_index: Mapping[str, set[int]]


@dataclass(frozen=True)
class _PreparedValidationPublication:
    request: ValidationPublicationRequest
    metadata: Mapping[str, object]
    projection: Mapping[str, object]
    relations: Mapping[str, list[str]]
    limitations: list[str]
    reason: str | None
    record_identity: str
    source_identity: object
    result_id: str
    finished: str
    slot: str
    validation_id: str
    display_values: Mapping[str, tuple[str, str]]
    report_snapshot: str | None


@dataclass(frozen=True)
class _ProjectionInsertRequest:
    db: Any
    result_pk: int
    projection: Mapping[str, object]
    record: MechanicalGeneratedRecord
    display_values: Mapping[str, tuple[str, str]]
    keys: Mapping[str, Mapping[str, int]]


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
            for command in _sequence(group.get("commands", []), "admission commands"):
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
MAX_VALIDATION_RESULT_ROWS = 200_000
MAX_VALIDATION_RESULT_BYTES = 128 * 1024 * 1024


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
    try:
        measure_json_value(candidate, MAX_VALIDATION_RESULT_BYTES)
    except ExportTooLarge:
        raise ValueError("validation result exceeds its byte bound")
    rows = (
        1 + len(record.checks) + sum(len(check.dependencies) for check in record.checks)
    )
    rows += len(
        {
            check.failure.code
            for check in record.checks
            if check.failure is not None
        }
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
    artifacts: set[str] = set()
    registry_payloads: dict[str, Mapping[str, object]] = {}
    for family in ("chains", "unresolved"):
        for group in _sequence(projection.get(family, []), f"{family} groups"):
            assert isinstance(group, Mapping)
            findings = _sequence(group["findings"], "group findings")
            rows += 1
            for finding in findings:
                assert isinstance(finding, Mapping)
                rows += 1
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
            group_artifacts = _strings(
                group.get("artifacts", []), "group artifacts"
            )
            artifacts.update(group_artifacts)
            rows += len(group_artifacts)
            rows += len(_sequence(group.get("edges", []), "group edges"))
            registry = _sequence(group.get("registry", []), "group registry")
            rows += len(registry)
            for raw in registry:
                assert isinstance(raw, Mapping)
                registry_payloads.setdefault(_json(raw), raw)
            rows += len(_sequence(group.get("signals", []), "group signals"))
    rows += len(artifacts)
    rows += len(registry_payloads)
    for registry_payload in registry_payloads.values():
        identity = parse_resource_identity(
            registry_payload["identity"],
            "registry identity",
            kind=registry_payload["kind"],
        )
        rows += len(identity.files or identity.patterns)
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


def _ordered_projection_groups(
    projection: Mapping[str, object],
) -> list[tuple[str, Mapping[str, Any]]]:
    """Return groups in the canonical chain-then-unresolved insertion order."""

    ordered: list[tuple[str, Mapping[str, Any]]] = []
    for family, group_kind in (("chains", "chain"), ("unresolved", "unresolved")):
        for raw in _sequence(projection.get(family, []), f"{family} groups"):
            assert isinstance(raw, Mapping)
            ordered.append((group_kind, raw))
    return ordered


def _v14_entity_keys(
    record: MechanicalGeneratedRecord,
    projection: Mapping[str, object],
) -> dict[str, dict[str, int]]:
    """Allocate deterministic positive identities for one normalized result."""

    code_pks: dict[str, int] = {}
    check_pks: dict[str, int] = {}
    for check_pk, check in enumerate(record.checks, 1):
        check_pks[check.identity] = check_pk
        if check.failure is not None and check.failure.code not in code_pks:
            code_pks[check.failure.code] = len(code_pks) + 1

    group_pks: dict[str, int] = {}
    command_pks: dict[str, int] = {}
    artifact_pks: dict[str, int] = {}
    for _, group in _ordered_projection_groups(projection):
        group_id = _string(group["chain_id"], "group identity")
        group_pks[group_id] = len(group_pks) + 1
        for artifact in _strings(group.get("artifacts", []), "group artifacts"):
            if artifact not in artifact_pks:
                artifact_pks[artifact] = len(artifact_pks) + 1
        for command in _sequence(group.get("commands", []), "group commands"):
            assert isinstance(command, Mapping)
            command_id = _string(command["identity"], "command identity")
            command_pks[command_id] = len(command_pks) + 1

    batch_pks = {
        _string(batch["batch_id"], "batch identity"): position
        for position, batch in enumerate(
            _sequence(projection.get("repair_batches", []), "repair batches"), 1
        )
        if isinstance(batch, Mapping)
    }
    return {
        "codes": code_pks,
        "checks": check_pks,
        "groups": group_pks,
        "commands": command_pks,
        "artifacts": artifact_pks,
        "batches": batch_pks,
    }


def _prepare_validation_publication(
    request: ValidationPublicationRequest,
) -> _PreparedValidationPublication:
    mechanical_record = request.mechanical_record
    kind = request.kind
    entry = request.entry
    if kind not in {"full", "entry", "diagnostic"} or (kind != "full" and not entry):
        raise ValueError("invalid validation result slot")
    if mechanical_record.completion is CompletionState.INCOMPLETE:
        raise ValueError("incomplete validation records cannot replace completed slots")
    metadata = dict(request.result_metadata or {})
    projection = dict(request.batch_projection or {})
    relations, limitations, reason = _metadata_entries(metadata, kind, entry)
    record_identity = hashlib.sha256(
        mechanical_record.canonical_json().encode()
    ).hexdigest()
    if kind in {"full", "entry"} and not projection:
        raise ValueError("completed validation requires a complete projection")
    if projection:
        _validate_projection(projection, mechanical_record)
    stored_source_identity = (
        request.source_identity
        or metadata.get("source_identity")
        or projection.get("source_identity")
    )
    if projection and stored_source_identity != projection["source_identity"]:
        raise ValueError("result source identity does not match projection")
    result_id, finished = str(uuid.uuid4()), request.finished_at or _now()
    slot = "full" if kind == "full" else f"{kind}:{entry}"
    validation_id = str(projection.get("validation_id") or record_identity)
    display_values = (
        _display_values(mechanical_record, request.report_context)
        if kind == "full"
        else {}
    )
    report_snapshot = (
        _captured_report_context(request.report_context) if kind == "full" else None
    )
    bounded_metadata = dict(metadata)
    bounded_metadata.update(
        {f"{relation}_entries": values for relation, values in relations.items()}
    )
    bounded_metadata["whole_log_limitations"] = limitations
    _validate_storage_bounds(
        mechanical_record, projection, bounded_metadata, report_snapshot
    )
    return _PreparedValidationPublication(
        request,
        metadata,
        projection,
        relations,
        limitations,
        reason,
        record_identity,
        stored_source_identity,
        result_id,
        finished,
        slot,
        validation_id,
        display_values,
        report_snapshot,
    )


def publish_validation_result(
    request: ValidationPublicationRequest,
) -> StoredValidationResult:
    """Atomically replace one completed validation slot with normalized rows."""

    prepared = _prepare_validation_publication(request)
    kind = request.kind
    mechanical_record = request.mechanical_record
    with result_transaction(request.log_root) as db:
        prior = db.execute(
            "SELECT generation FROM store_state WHERE domain='validation'"
        ).fetchone()
        generation = 1 if prior is None else int(prior[0]) + 1
        db.execute(
            "DELETE FROM validation_results"
            if kind == "full"
            else "DELETE FROM validation_results WHERE slot=?",
            () if kind == "full" else (prepared.slot,),
        )
        result_pk = int(
            db.execute(
                "SELECT COALESCE(MAX(result_pk), 0) + 1 FROM validation_results"
            ).fetchone()[0]
        )
        db.execute(
            "INSERT INTO validation_results "
            "(result_pk,result_id,generation,slot,kind,entry,summary,status,reason,"
            "evaluated_checks,finding_count,started_at,finished_at,stored_at,"
            "result_date,rules_version,source_identity,validation_id,record_identity,"
            "projection_schema,report_context_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result_pk,
                prepared.result_id,
                generation,
                prepared.slot,
                kind,
                request.entry,
                mechanical_record.summary,
                mechanical_record.completion.value,
                prepared.reason,
                len(mechanical_record.checks),
                sum(check.failure is not None for check in mechanical_record.checks),
                request.started_at or prepared.finished,
                prepared.finished,
                prepared.finished,
                mechanical_record.result_date,
                mechanical_record.rules_version,
                prepared.source_identity,
                prepared.validation_id,
                prepared.record_identity,
                prepared.projection.get("schema"),
                prepared.report_snapshot,
            ),
        )
        for relation, entries in prepared.relations.items():
            db.executemany(
                "INSERT INTO validation_result_entries VALUES (?, ?, ?, ?)",
                [
                    (result_pk, relation, position, value)
                    for position, value in enumerate(entries)
                ],
            )
        db.executemany(
            "INSERT INTO validation_result_limitations VALUES (?, ?, ?)",
            [
                (result_pk, position, value)
                for position, value in enumerate(prepared.limitations)
            ],
        )
        keys = _v14_entity_keys(mechanical_record, prepared.projection)
        db.executemany(
            "INSERT INTO validation_codes (result_pk,code_pk,code) VALUES (?,?,?)",
            [
                (result_pk, code_pk, code)
                for code, code_pk in keys["codes"].items()
            ],
        )
        for check_pk, check in enumerate(mechanical_record.checks, 1):
            failure = check.failure
            db.execute(
                "INSERT INTO validation_checks "
                "(result_pk,check_pk,check_id,scope,status,subject,code_pk,rule,"
                "observed_json,failure_dependency) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    result_pk,
                    check_pk,
                    check.identity,
                    check.scope.value,
                    check.status.value,
                    check.subject,
                    None if failure is None else keys["codes"][failure.code],
                    None if failure is None else failure.rule,
                    None if failure is None else _json(failure.observed),
                    None if failure is None else failure.dependency,
                ),
            )
            db.executemany(
                "INSERT INTO validation_check_dependencies VALUES (?, ?, ?, ?)",
                [
                    (result_pk, check_pk, p, _json(v))
                    for p, v in enumerate(check.dependencies)
                ],
            )
        if kind in {"full", "entry"} and prepared.projection:
            _insert_projection(
                _ProjectionInsertRequest(
                    db,
                    result_pk,
                    prepared.projection,
                    mechanical_record,
                    prepared.display_values,
                    keys,
                )
            )
        _audit_validation_result(db, result_pk)
        db.execute(
            "INSERT INTO store_state VALUES ('validation', ?, ?) ON CONFLICT(domain) DO UPDATE SET generation=excluded.generation, summary=excluded.summary",
            (generation, mechanical_record.summary),
        )
        db.execute("DELETE FROM report_materializations WHERE kind='validation'")
    return StoredValidationResult(
        prepared.result_id, prepared.validation_id, generation
    )


def _insert_projection(
    request: _ProjectionInsertRequest,
) -> None:
    db = request.db
    result_pk = request.result_pk
    projection = request.projection
    record = request.record
    keys = request.keys
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
    context = _V14InsertContext(
        db,
        result_pk,
        checks,
        keys["checks"],
        keys["codes"],
        keys["groups"],
        keys["commands"],
        keys["artifacts"],
        keys["batches"],
        request.display_values,
        projected_findings,
        command_index,
    )
    _insert_projection_groups(context, projection)
    _insert_projection_batches(context, projection)


def _insert_projection_groups(
    context: _V14InsertContext,
    projection: Mapping[str, object],
) -> None:
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
            group_pk = context.group_pks[group_id]
            context.db.execute(
                "INSERT INTO validation_groups "
                "(result_pk,group_pk,group_id,group_kind,entry,reason,position) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    context.result_pk,
                    group_pk,
                    group_id,
                    group_kind,
                    group["entry"],
                    group.get("reason"),
                    position,
                ),
            )
    for _, group in _ordered_projection_groups(projection):
        group_id = _string(group["chain_id"], "group identity")
        group_pk = context.group_pks[group_id]
        _insert_group_detail(context, group_pk, group)
        _insert_group_findings(context, group_pk, group)


def _insert_group_findings(
    context: _V14InsertContext,
    group_pk: int,
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
            "INSERT INTO validation_findings "
            "(result_pk,check_pk,group_pk,position,admission_effect,display_entry,"
            "display_subject) VALUES (?,?,?,?,?,?,?)",
            (
                context.result_pk,
                context.check_pks[str(finding["identity"])],
                group_pk,
                finding_position,
                finding.get("admission_effect"),
                entry,
                subject,
            ),
        )
        context.db.executemany(
            "INSERT INTO validation_finding_affected_chains VALUES (?, ?, ?, ?)",
            [
                (
                    context.result_pk,
                    context.check_pks[str(finding["identity"])],
                    p,
                    context.group_pks[str(v)],
                )
                for p, v in enumerate(finding.get("affected_chains", []))
            ],
        )
        context.db.executemany(
            "INSERT INTO validation_finding_affected_entries VALUES (?, ?, ?, ?)",
            [
                (
                    context.result_pk,
                    context.check_pks[str(finding["identity"])],
                    p,
                    v,
                )
                for p, v in enumerate(finding.get("affected_entries", []))
            ],
        )


def _insert_projection_batches(
    context: _V14InsertContext,
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
            "INSERT INTO validation_batches "
            "(result_pk,batch_pk,batch_id,batch_type,grouping_reason,scope,"
            "primary_finding_count,starting_check_pk,position) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                context.result_pk,
                context.batch_pks[str(batch["batch_id"])],
                batch["batch_id"],
                batch.get("batch_type", ""),
                batch.get("grouping_reason", ""),
                batch.get("scope", ""),
                len(findings),
                context.check_pks[str(starting_finding_id)],
                position,
            ),
        )
        _insert_batch_members(context, batch)
        _insert_batch_command_links(context, batch, findings)


def _insert_batch_members(
    context: _V14InsertContext, batch: Mapping[str, Any]
) -> None:
    batch_pk = context.batch_pks[str(batch["batch_id"])]
    for field, table in (
        ("entries", "validation_batch_entries"),
        ("anchors", "validation_batch_anchors"),
        ("primary_finding_ids", "validation_batch_findings"),
        ("related_chain_ids", "validation_batch_groups"),
        ("related_batch_ids", "validation_batch_related_batches"),
    ):
        values = _sequence(batch[field], f"repair batch {field}")
        encoded = []
        for position, value in enumerate(values):
            stored: object
            if field == "anchors":
                stored = _json(value)
            elif field == "primary_finding_ids":
                stored = context.check_pks[str(value)]
            elif field == "related_chain_ids":
                stored = context.group_pks[str(value)]
            elif field == "related_batch_ids":
                stored = context.batch_pks[str(value)]
            else:
                stored = value
            encoded.append((context.result_pk, batch_pk, position, stored))
        context.db.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
            encoded,
        )


def _insert_batch_command_links(
    context: _V14InsertContext, batch: Mapping[str, Any], findings: list[Any]
) -> None:
    batch_codes = [
        _string(context.projected_findings[str(value)]["code"], "finding code")
        for value in findings
    ]
    commands = _batch_link_commands(
        batch, findings, batch_codes, context.projected_findings, context.command_index
    )
    for command_id, codes in commands.items():
        context.db.executemany(
            "INSERT OR IGNORE INTO validation_batch_command_links VALUES (?, ?, ?, ?)",
            [
                (
                    context.result_pk,
                    context.batch_pks[str(batch["batch_id"])],
                    context.command_pks[command_id],
                    context.code_pks[code],
                )
                for code in codes
            ],
        )


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
    context: _V14InsertContext, group_pk: int, group: Mapping[str, Any]
) -> None:
    _insert_group_artifacts_signals_registry(context, group_pk, group)
    _insert_group_commands(context, group_pk, group)
    _insert_group_edges(context, group_pk, group)


def _insert_group_artifacts_signals_registry(
    context: _V14InsertContext, group_pk: int, group: Mapping[str, Any]
) -> None:
    for position, artifact in enumerate(group.get("artifacts", [])):
        artifact_pk = context.artifact_pks[str(artifact)]
        context.db.execute(
            "INSERT OR IGNORE INTO validation_artifacts VALUES (?, ?, ?)",
            (context.result_pk, artifact_pk, artifact),
        )
        context.db.execute(
            "INSERT INTO validation_group_artifacts VALUES (?, ?, ?, ?)",
            (context.result_pk, group_pk, position, artifact_pk),
        )
    for position, signal in enumerate(group.get("signals", [])):
        context.db.execute(
            "INSERT INTO validation_group_signals VALUES (?, ?, ?, ?)",
            (context.result_pk, group_pk, position, signal),
        )
    for position, registry in enumerate(group.get("registry", [])):
        assert isinstance(registry, Mapping)
        payload = _json(registry)
        payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        stored = context.db.execute(
            "SELECT registry_pk FROM validation_registry_records "
            "WHERE result_pk=? AND payload_sha256=?",
            (context.result_pk, payload_sha256),
        ).fetchone()
        if stored is None:
            registry_pk = int(
                context.db.execute(
                    "SELECT COALESCE(MAX(registry_pk), 0) + 1 "
                    "FROM validation_registry_records WHERE result_pk=?",
                    (context.result_pk,),
                ).fetchone()[0]
            )
            identity = parse_resource_identity(
                registry["identity"],
                "registry identity",
                kind=registry["kind"],
            )
            context.db.execute(
                "INSERT INTO validation_registry_records "
                "(result_pk,registry_pk,payload_sha256,owner_entry,name,kind,"
                "location,path,origin,from_entry,read_only,identity_algorithm,"
                "identity_commit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    context.result_pk,
                    registry_pk,
                    payload_sha256,
                    registry["entry"],
                    registry["name"],
                    registry["kind"],
                    registry["location"],
                    registry["path"],
                    int(registry["origin"]),
                    registry.get("from_entry"),
                    None
                    if "read_only" not in registry
                    else int(registry["read_only"]),
                    identity.algorithm,
                    identity.commit,
                ),
            )
            context.db.executemany(
                "INSERT INTO validation_registry_identity_members "
                "VALUES (?,?,?,?)",
                [
                    (context.result_pk, registry_pk, member_position, member)
                    for member_position, member in enumerate(
                        cast(Mapping[str, Any], registry["identity"]).get(
                            "files",
                            cast(Mapping[str, Any], registry["identity"]).get(
                                "patterns", []
                            ),
                        )
                    )
                ],
            )
        else:
            registry_pk = int(stored[0])
        context.db.execute(
            "INSERT INTO validation_group_registry VALUES (?,?,?,?)",
            (context.result_pk, group_pk, position, registry_pk),
        )


def _insert_group_commands(
    context: _V14InsertContext, group_pk: int, group: Mapping[str, Any]
) -> None:
    for position, command in enumerate(group.get("commands", [])):
        if not isinstance(command, Mapping):
            continue
        command_id = command.get("identity")
        command_pk = context.command_pks[str(command_id)]
        context.db.execute(
            "INSERT INTO validation_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                context.result_pk,
                command_pk,
                command_id,
                command.get("entry"),
                command.get("document"),
                command.get("fence"),
                command.get("ordinal"),
                command.get("script"),
                group_pk,
                position,
            ),
        )
        context.db.executemany(
            "INSERT INTO validation_command_tokens VALUES (?, ?, ?, ?)",
            [
                (context.result_pk, command_pk, p, value)
                for p, value in enumerate(command.get("tokens", []))
            ],
        )
        for direction, key in (("input", "inputs"), ("output", "outputs")):
            for p, value in enumerate(command.get(key, [])):
                if isinstance(value, Mapping):
                    path = str(value.get("path"))
                    path_artifact_pk = context.artifact_pks.get(path)
                    context.db.execute(
                        "INSERT INTO validation_command_relationships "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            context.result_pk,
                            command_pk,
                            direction,
                            p,
                            path_artifact_pk,
                            None if path_artifact_pk is not None else path,
                            value.get("proof"),
                            value.get("target"),
                            value.get("artifact"),
                            int(bool(value.get("origin"))),
                        ),
                    )
        for p, value in enumerate(command.get("collections", [])):
            if isinstance(value, Mapping):
                context.db.execute(
                    "INSERT INTO validation_command_collections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        context.result_pk,
                        command_pk,
                        p,
                        value.get("direction"),
                        value.get("mechanism"),
                        value.get("root"),
                        value.get("target"),
                    ),
                )
                context.db.executemany(
                    "INSERT INTO validation_collection_members VALUES (?, ?, ?, ?, ?)",
                    [
                        (context.result_pk, command_pk, p, q, member)
                        for q, member in enumerate(value.get("members", []))
                    ],
                )


def _insert_group_edges(
    context: _V14InsertContext, group_pk: int, group: Mapping[str, Any]
) -> None:
    # Edge rows reference both commands and artifacts, so insert them only
    # after their complete group memberships exist.
    for position, edge in enumerate(group.get("edges", [])):
        if isinstance(edge, Mapping):
            context.db.execute(
                "INSERT INTO validation_group_edges VALUES (?, ?, ?, ?, ?, ?)",
                (
                    context.result_pk,
                    group_pk,
                    position,
                    context.command_pks[str(edge.get("source"))],
                    context.command_pks[str(edge.get("target"))],
                    context.artifact_pks[str(edge.get("artifact"))],
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
            "SELECT * FROM validation_results WHERE slot='full'"
        ).fetchone()
        if row is None:
            raise ResultStoreError(
                "results.store.missing", "no completed full validation result"
            )
        _validate_stored_result_header(row)
        if row["kind"] != "full":
            _malformed("full validation slot has the wrong kind")
        return StoredValidationResult(
            str(row["result_id"]),
            str(row["validation_id"]),
            int(row["generation"]),
        )


def resolve_validation_result(
    db: Any, result_id: str
) -> ResolvedValidationResult:
    """Resolve one public identity without opening a second snapshot."""

    row = db.execute(
        "SELECT * FROM validation_results WHERE result_id=?", (result_id,)
    ).fetchone()
    if row is None:
        raise ResultStoreError("results.store.missing", "validation result is absent")
    if not isinstance(row["result_pk"], int) or row["result_pk"] < 1:
        _malformed("invalid stored validation result identity")
    _validate_stored_result_header(row)
    return ResolvedValidationResult(
        int(row["result_pk"]),
        str(row["result_id"]),
        None if row["validation_id"] is None else str(row["validation_id"]),
        int(row["generation"]),
    )


def _resolve_latest_full(db: Any) -> ResolvedValidationResult:
    row = db.execute(
        "SELECT result_id FROM validation_results WHERE slot='full'"
    ).fetchone()
    if row is None:
        raise ResultStoreError(
            "results.store.missing", "no completed full validation result"
        )
    return resolve_validation_result(db, str(row["result_id"]))


def _audit_dense_positions(
    db: Any, result_pk: int, table: str, parents: tuple[str, ...]
) -> None:
    """Audit every ordered membership under each parent in one result."""

    select = ",".join(parents)
    parent_rows = (
        [()]
        if not parents
        else [
            tuple(row)
            for row in db.execute(
                f"SELECT DISTINCT {select} FROM {table} WHERE result_pk=?",
                (result_pk,),
            )
        ]
    )
    for parent in parent_rows:
        predicates = "".join(f" AND {column}=?" for column in parents)
        positions = [
            row[0]
            for row in db.execute(
                f"SELECT position FROM {table} WHERE result_pk=?{predicates} "
                "ORDER BY position",
                (result_pk, *parent),
            )
        ]
        if positions != list(range(len(positions))):
            _malformed(f"invalid stored {table} positions")


def _audit_validation_result(db: Any, result_pk: int) -> None:
    """Perform the explicit whole-result v14 integrity audit."""

    result = db.execute(
        "SELECT * FROM validation_results WHERE result_pk=?", (result_pk,)
    ).fetchone()
    if result is None:
        _malformed("validation result is absent")
    _validate_stored_result_header(result)
    if result["report_context_json"] is not None:
        _stored_json(result["report_context_json"], "report context")
    counts = db.execute(
        "SELECT (SELECT count(*) FROM validation_checks WHERE result_pk=?),"
        "(SELECT count(*) FROM validation_findings WHERE result_pk=?)",
        (result_pk, result_pk),
    ).fetchone()
    if (result["evaluated_checks"], result["finding_count"]) != tuple(counts):
        _malformed("validation metadata counts disagree")
    _audit_validation_positions(db, result_pk)
    _audit_validation_checks(db, result_pk)
    _audit_validation_findings(db, result_pk)
    _audit_registry_records(db, result_pk)
    _audit_validation_batches_and_links(db, result_pk)


def _audit_validation_positions(db: Any, result_pk: int) -> None:
    for table, parents in (
        ("validation_result_entries", ("relation",)),
        ("validation_result_limitations", ()),
        ("validation_check_dependencies", ("check_pk",)),
        ("validation_groups", ("group_kind",)),
        ("validation_findings", ("group_pk",)),
        ("validation_finding_affected_chains", ("check_pk",)),
        ("validation_finding_affected_entries", ("check_pk",)),
        ("validation_commands", ("group_pk",)),
        ("validation_command_tokens", ("command_pk",)),
        ("validation_command_relationships", ("command_pk", "direction")),
        ("validation_command_collections", ("command_pk",)),
        ("validation_collection_members", ("command_pk", "collection_position")),
        ("validation_group_artifacts", ("group_pk",)),
        ("validation_group_edges", ("group_pk",)),
        ("validation_group_signals", ("group_pk",)),
        ("validation_group_registry", ("group_pk",)),
        ("validation_registry_identity_members", ("registry_pk",)),
        ("validation_batches", ()),
        ("validation_batch_entries", ("batch_pk",)),
        ("validation_batch_anchors", ("batch_pk",)),
        ("validation_batch_findings", ("batch_pk",)),
        ("validation_batch_groups", ("batch_pk",)),
        ("validation_batch_related_batches", ("batch_pk",)),
    ):
        _audit_dense_positions(db, result_pk, table, parents)


def _audit_validation_checks(db: Any, result_pk: int) -> None:
    for row in db.execute(
        "SELECT c.*,k.code FROM validation_checks AS c "
        "LEFT JOIN validation_codes AS k USING (result_pk,code_pk) "
        "WHERE c.result_pk=?",
        (result_pk,),
    ):
        for field in ("check_id", "scope", "status", "subject"):
            _stored_string(row[field], f"check {field}")
        if row["scope"] not in _CHECK_SCOPES or row["status"] not in _CHECK_STATUSES:
            _malformed("invalid stored check scope or status")
        if row["status"] in _FAILING_CHECK_STATUSES:
            _audit_validation_check_failure(row)
        elif any(
            row[field] is not None
            for field in ("code_pk", "rule", "observed_json", "failure_dependency")
        ):
            _malformed("stored successful check has failure payload")
        for dependency in db.execute(
            "SELECT dependency_json FROM validation_check_dependencies "
            "WHERE result_pk=? AND check_pk=? ORDER BY position",
            (result_pk, row["check_pk"]),
        ):
            _stored_json(dependency[0], "check dependency")


def _audit_validation_check_failure(row: Any) -> None:
    if any(row[field] is None for field in ("code_pk", "rule", "observed_json")):
        _malformed("stored failing check has no failure payload")
    _stored_string(row["code"], "check failure code")
    _stored_string(row["rule"], "check failure rule")
    _stored_json(row["observed_json"], "check observed")
    _stored_string(row["failure_dependency"], "check failure dependency", nullable=True)


def _audit_validation_findings(db: Any, result_pk: int) -> None:
    for row in db.execute(
        "SELECT f.*,c.check_id,c.status,c.subject,k.code,g.entry AS group_entry "
        "FROM validation_findings AS f "
        "LEFT JOIN validation_checks AS c USING (result_pk,check_pk) "
        "LEFT JOIN validation_codes AS k USING (result_pk,code_pk) "
        "LEFT JOIN validation_groups AS g USING (result_pk,group_pk) "
        "WHERE f.result_pk=?",
        (result_pk,),
    ):
        if row["check_id"] is None or row["group_entry"] is None:
            _malformed("stored finding references a foreign parent")
        if row["status"] not in _FAILING_CHECK_STATUSES:
            _malformed("stored finding has no failed check")
        for field in ("check_id", "code", "admission_effect"):
            _stored_string(row[field], f"finding {field}")
        if not isinstance(row["display_entry"], str) or not isinstance(
            row["display_subject"], str
        ):
            _malformed("invalid stored finding display")
        _audit_finding_effect(db, result_pk, row)


def _audit_validation_report_rows(db: Any, result_pk: int) -> None:
    """Audit only complete mechanical/report rows consumed by report rendering."""

    result = db.execute(
        "SELECT * FROM validation_results WHERE result_pk=?", (result_pk,)
    ).fetchone()
    if result is None:
        _malformed("validation result is absent")
    _validate_stored_result_header(result)
    if result["report_context_json"] is None:
        _malformed("validation report context is absent")
    _stored_json(result["report_context_json"], "report context")
    counts = db.execute(
        "SELECT (SELECT count(*) FROM validation_checks WHERE result_pk=?),"
        "(SELECT count(*) FROM validation_findings WHERE result_pk=?)",
        (result_pk, result_pk),
    ).fetchone()
    if (result["evaluated_checks"], result["finding_count"]) != tuple(counts):
        _malformed("validation metadata counts disagree")
    _audit_dense_positions(
        db, result_pk, "validation_check_dependencies", ("check_pk",)
    )
    _audit_validation_checks(db, result_pk)
    for row in db.execute(
        "SELECT c.status,f.display_entry,f.display_subject "
        "FROM validation_findings AS f "
        "JOIN validation_checks AS c USING (result_pk,check_pk) "
        "WHERE f.result_pk=?",
        (result_pk,),
    ):
        if row["status"] not in _FAILING_CHECK_STATUSES:
            _malformed("stored finding has no failed check")
        if not isinstance(row["display_entry"], str) or not isinstance(
            row["display_subject"], str
        ):
            _malformed("invalid stored finding display")


def _audit_finding_effect(db: Any, result_pk: int, row: Any) -> None:
    chains = list(
        db.execute(
            "SELECT a.group_pk,g.entry FROM validation_finding_affected_chains AS a "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE a.result_pk=? AND a.check_pk=? ORDER BY a.position",
            (result_pk, row["check_pk"]),
        )
    )
    entries = [
        item[0]
        for item in db.execute(
            "SELECT entry FROM validation_finding_affected_entries "
            "WHERE result_pk=? AND check_pk=? ORDER BY position",
            (result_pk, row["check_pk"]),
        )
    ]
    effect = row["admission_effect"]
    if effect in {"none", "log"} and (chains or entries):
        _malformed("stored non-admitting finding has affected members")
    if effect == "entry" and (chains or len(entries) != 1):
        _malformed("stored entry finding has invalid affected members")
    if effect == "chain" and (
        len(chains) != 1
        or len(entries) != 1
        or chains[0][1] != entries[0]
    ):
        _malformed("stored chain finding has invalid affected members")
    if effect not in {"none", "log", "entry", "chain"}:
        _malformed("stored finding has invalid admission effect")

def _audit_registry_records(db: Any, result_pk: int) -> None:
    for row in db.execute(
        "SELECT * FROM validation_registry_records WHERE result_pk=?",
        (result_pk,),
    ):
        identity: dict[str, object] = {"algorithm": row["identity_algorithm"]}
        if row["identity_commit"] is not None:
            identity["commit"] = row["identity_commit"]
        members = [
            item[0]
            for item in db.execute(
                "SELECT member FROM validation_registry_identity_members "
                "WHERE result_pk=? AND registry_pk=? ORDER BY position",
                (result_pk, row["registry_pk"]),
            )
        ]
        if row["identity_algorithm"] == "identity-files-sha256-v1":
            identity["files"] = members
        elif row["identity_algorithm"] == "identity-patterns-sha256-v1":
            identity["patterns"] = members
        elif members:
            _malformed("scalar registry identity has members")
        payload: dict[str, object] = {
            "entry": row["owner_entry"],
            "name": row["name"],
            "kind": row["kind"],
            "location": row["location"],
            "path": row["path"],
            "origin": bool(row["origin"]),
            "identity": identity,
        }
        if row["from_entry"] is not None:
            payload["from_entry"] = row["from_entry"]
            payload["read_only"] = bool(row["read_only"])
        try:
            _validate_registry(payload)
        except ValueError as error:
            raise ResultStoreError(
                "results.store.malformed", "invalid stored registry payload"
            ) from error
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        if digest != row["payload_sha256"]:
            _malformed("stored registry payload hash disagrees")


def _audit_validation_batches_and_links(db: Any, result_pk: int) -> None:
    command_pks, anchor_index, material_index = _batch_command_audit_indexes(
        db, result_pk
    )
    context = _BatchAuditContext(
        db, result_pk, command_pks, anchor_index, material_index
    )
    expected: set[tuple[int, int, int]] = set()
    for batch in db.execute(
        "SELECT * FROM validation_batches WHERE result_pk=?", (result_pk,)
    ):
        expected.update(
            _expected_batch_command_links(
                context,
                batch,
            )
        )
    actual = {
        (int(row[0]), int(row[1]), int(row[2]))
        for row in db.execute(
            "SELECT batch_pk,command_pk,code_pk FROM validation_batch_command_links "
            "WHERE result_pk=?",
            (result_pk,),
        )
    }
    if actual != expected:
        _malformed("repair batch command links are not exact")


def _batch_command_audit_indexes(
    db: Any, result_pk: int
) -> tuple[dict[str, int], dict[str, set[int]], dict[str, set[int]]]:
    command_pks: dict[str, int] = {}
    anchor_index: dict[str, set[int]] = {}
    material_index: dict[str, set[int]] = {}
    for row in db.execute(
        "SELECT command_pk,command_id,entry,document,fence,ordinal "
        "FROM validation_commands WHERE result_pk=?",
        (result_pk,),
    ):
        command_pk = int(row["command_pk"])
        command_pks[str(row["command_id"])] = command_pk
        anchor = _json(
            {
                "kind": "command",
                "entry": row["entry"],
                "document": row["document"],
                "fence": row["fence"],
                "ordinal": row["ordinal"],
            }
        )
        anchor_index.setdefault(anchor, set()).add(command_pk)
        for relationship in db.execute(
            "SELECT COALESCE(a.artifact_id,r.path_text) AS path "
            "FROM validation_command_relationships AS r "
            "LEFT JOIN validation_artifacts AS a "
            "ON a.result_pk=r.result_pk AND a.artifact_pk=r.path_artifact_pk "
            "WHERE r.result_pk=? AND r.command_pk=?",
            (result_pk, command_pk),
        ):
            path = relationship["path"]
            _stored_string(path, "command relationship path")
            material_index.setdefault(
                _json({"kind": "material", "path": path}), set()
            ).add(command_pk)
    return command_pks, anchor_index, material_index


def _expected_batch_command_links(
    context: _BatchAuditContext,
    batch: Any,
) -> set[tuple[int, int, int]]:
    batch_pk = int(batch["batch_pk"])
    findings = list(
        context.db.execute(
            "SELECT bf.check_pk,c.check_id,c.observed_json,c.code_pk,k.code "
            "FROM validation_batch_findings AS bf "
            "JOIN validation_checks AS c USING (result_pk,check_pk) "
            "JOIN validation_codes AS k USING (result_pk,code_pk) "
            "WHERE bf.result_pk=? AND bf.batch_pk=? ORDER BY bf.position",
            (context.result_pk, batch_pk),
        )
    )
    _audit_batch_finding_members(batch, findings)
    _audit_batch_related_members(context.db, context.result_pk, batch_pk)
    codes = {int(row["code_pk"]) for row in findings}
    command_codes = _rejected_batch_command_codes(findings, context.command_pks)
    _add_batch_anchor_codes(
        context,
        batch_pk,
        codes,
        command_codes,
    )
    return {
        (batch_pk, command_pk, code_pk)
        for command_pk, linked_codes in command_codes.items()
        for code_pk in linked_codes
    }


def _audit_batch_finding_members(batch: Any, findings: list[Any]) -> None:
    check_pks = [int(row["check_pk"]) for row in findings]
    if (
        not findings
        or len(check_pks) != len(set(check_pks))
        or len(findings) != batch["primary_finding_count"]
        or batch["starting_check_pk"] not in check_pks
    ):
        _malformed("stored repair batch has inconsistent primary findings")
    rejected_start = next(
        (
            int(row["check_pk"])
            for row in findings
            if _stored_finding_has_rejected_command(row["observed_json"])
        ),
        check_pks[0],
    )
    if batch["starting_check_pk"] != rejected_start:
        _malformed("stored repair batch has inconsistent starting finding")


def _audit_batch_related_members(db: Any, result_pk: int, batch_pk: int) -> None:
    related = [
        int(row[0])
        for row in db.execute(
            "SELECT related_batch_pk FROM validation_batch_related_batches "
            "WHERE result_pk=? AND batch_pk=? ORDER BY position",
            (result_pk, batch_pk),
        )
    ]
    if batch_pk in related or len(related) != len(set(related)):
        _malformed("stored repair batch has invalid related batches")


def _rejected_batch_command_codes(
    findings: list[Any], command_pks: Mapping[str, int]
) -> dict[int, set[int]]:
    command_codes: dict[int, set[int]] = {}
    for finding in findings:
        observed = _stored_json(finding["observed_json"], "finding observed")
        if not isinstance(observed, Mapping):
            continue
        rejected = list(observed.get("rejected_commands", []))
        singular = observed.get("rejected_command")
        if isinstance(singular, Mapping):
            rejected.append(singular)
        for value in rejected:
            if not isinstance(value, Mapping):
                continue
            identity = value.get("identity")
            command_pk = (
                command_pks.get(identity) if isinstance(identity, str) else None
            )
            if command_pk is not None:
                command_codes.setdefault(command_pk, set()).add(
                    int(finding["code_pk"])
                )
    return command_codes


def _add_batch_anchor_codes(
    context: _BatchAuditContext,
    batch_pk: int,
    codes: set[int],
    command_codes: dict[int, set[int]],
) -> None:
    for anchor_row in context.db.execute(
        "SELECT anchor_json FROM validation_batch_anchors "
        "WHERE result_pk=? AND batch_pk=? ORDER BY position",
        (context.result_pk, batch_pk),
    ):
        key = _stored_batch_anchor_key(anchor_row["anchor_json"])
        for command_pk in context.anchor_index.get(
            key, set()
        ) | context.material_index.get(key, set()):
            command_codes.setdefault(command_pk, set()).update(codes)

def _validate_stored_result(db: Any, result_id: str) -> None:
    """Reject corruption that SQLite foreign keys cannot express polymorphically."""

    resolved = resolve_validation_result(db, result_id)
    _audit_validation_result(db, resolved.result_pk)


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


def _stored_finding_has_rejected_command(raw: object) -> bool:
    observed = _stored_json(raw, "finding observed")
    return isinstance(observed, Mapping) and (
        isinstance(observed.get("rejected_command"), Mapping)
        or bool(_sequence(observed.get("rejected_commands", []), "rejected commands"))
    )


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


def audit_validation_result(log_root: Path, result_id: str) -> None:
    """Explicitly audit every normalized row owned by one validation result."""
    with result_snapshot(log_root) as db:
        _validate_stored_result(db, result_id)


def validate_validation_result(log_root: Path, result_id: str) -> None:
    """Compatibility name for the explicit validation-result audit."""

    audit_validation_result(log_root, result_id)


def _admission_groups(db: Any, result_pk: int) -> tuple[ValidationAdmissionGroup, ...]:
    _audit_dense_positions(db, result_pk, "validation_groups", ("group_kind",))
    groups = []
    for row in db.execute(
        "SELECT group_id,group_kind,entry FROM validation_groups "
        "WHERE result_pk=? ORDER BY group_kind,position",
        (result_pk,),
    ):
        for field in ("group_id", "group_kind", "entry"):
            _stored_string(row[field], f"admission group {field}")
        if row["group_kind"] not in {"chain", "unresolved"}:
            _malformed("invalid stored admission group kind")
        groups.append(
            ValidationAdmissionGroup(row["group_id"], row["group_kind"], row["entry"])
        )
    return tuple(groups)


def _admission_finding_members(
    db: Any, result_pk: int, row: Any
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    check_pk = int(row["check_pk"])
    for table in (
        "validation_finding_affected_chains",
        "validation_finding_affected_entries",
    ):
        _validate_selected_positions(db, result_pk, table, {"check_pk": check_pk})
    chain_rows = list(
        db.execute(
            "SELECT a.group_pk,g.group_id,g.entry FROM "
            "validation_finding_affected_chains AS a "
            "LEFT JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE a.result_pk=? AND a.check_pk=? ORDER BY a.position",
            (result_pk, check_pk),
        )
    )
    if any(item["group_id"] is None for item in chain_rows):
        _malformed("stored admission finding references a foreign group")
    chains = tuple(str(item["group_id"]) for item in chain_rows)
    entries = tuple(
        _values(
            db,
            "SELECT entry FROM validation_finding_affected_entries "
            "WHERE result_pk=? AND check_pk=? ORDER BY position",
            (result_pk, check_pk),
            "entry",
        )
    )
    for value in (*chains, *entries):
        _stored_string(value, "admission affected member")
    effect = row["admission_effect"]
    if effect in {"none", "log"} and (chains or entries):
        _malformed("stored non-admitting finding has affected members")
    if effect == "entry" and (chains or len(entries) != 1):
        _malformed("stored entry finding has invalid affected members")
    if effect == "chain" and (
        len(chain_rows) != 1
        or len(entries) != 1
        or chain_rows[0]["entry"] != entries[0]
    ):
        _malformed("stored chain finding has invalid affected members")
    if effect not in {"none", "log", "entry", "chain"}:
        _malformed("stored finding has invalid admission effect")
    return chains, entries


def _admission_findings(
    db: Any, result_pk: int
) -> tuple[ValidationAdmissionFinding, ...]:
    _audit_dense_positions(db, result_pk, "validation_findings", ("group_pk",))
    rows = list(
        db.execute(
            "SELECT f.check_pk,c.check_id,g.group_id,c.status,f.admission_effect "
            "FROM validation_findings AS f "
            "JOIN validation_checks AS c USING (result_pk,check_pk) "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE f.result_pk=? ORDER BY c.check_id",
            (result_pk,),
        )
    )
    count = db.execute(
        "SELECT count(*) FROM validation_findings WHERE result_pk=?", (result_pk,)
    ).fetchone()[0]
    if len(rows) != count:
        _malformed("stored admission finding references a foreign parent")
    findings = []
    for row in rows:
        for field in ("check_id", "group_id", "status", "admission_effect"):
            _stored_string(row[field], f"admission finding {field}")
        if row["status"] not in _FAILING_CHECK_STATUSES:
            _malformed("stored admission finding has no failed check")
        chains, entries = _admission_finding_members(db, result_pk, row)
        findings.append(
            ValidationAdmissionFinding(
                row["check_id"],
                row["group_id"],
                row["status"],
                row["admission_effect"],
                chains,
                entries,
            )
        )
    return tuple(findings)


def _admission_command_outputs(
    db: Any, result_pk: int, command_pk: int
) -> tuple[str, ...]:
    _validate_selected_positions(
        db,
        result_pk,
        "validation_command_relationships",
        {"command_pk": command_pk, "direction": "output"},
    )
    relationships = tuple(
        _values(
            db,
            "SELECT COALESCE(a.artifact_id,r.path_text) AS path "
            "FROM validation_command_relationships AS r "
            "LEFT JOIN validation_artifacts AS a "
            "ON a.result_pk=r.result_pk AND a.artifact_pk=r.path_artifact_pk "
            "WHERE r.result_pk=? AND r.command_pk=? AND r.direction='output' "
            "ORDER BY r.position",
            (result_pk, command_pk),
            "path",
        )
    )
    collections = list(
        db.execute(
            "SELECT position,root FROM validation_command_collections "
            "WHERE result_pk=? AND command_pk=? AND direction='output' "
            "ORDER BY position",
            (result_pk, command_pk),
        )
    )
    roots = tuple(row["root"] for row in collections if row["root"] is not None)
    members: list[str] = []
    for collection in collections:
        position = int(collection["position"])
        _validate_selected_positions(
            db,
            result_pk,
            "validation_collection_members",
            {"command_pk": command_pk, "collection_position": position},
        )
        members.extend(
            _values(
                db,
                "SELECT path FROM validation_collection_members "
                "WHERE result_pk=? AND command_pk=? AND collection_position=? "
                "ORDER BY position",
                (result_pk, command_pk, position),
                "path",
            )
        )
    outputs = (*relationships, *roots, *members)
    for path in outputs:
        _stored_string(path, "admission command output")
    return tuple(outputs)


def _admission_commands(
    db: Any, result_pk: int
) -> tuple[ValidationAdmissionCommand, ...]:
    _audit_dense_positions(db, result_pk, "validation_commands", ("group_pk",))
    rows = list(
        db.execute(
            "SELECT c.command_pk,c.command_id,g.group_id,c.entry,g.entry AS group_entry "
            "FROM validation_commands AS c "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE c.result_pk=? ORDER BY c.command_id",
            (result_pk,),
        )
    )
    count = db.execute(
        "SELECT count(*) FROM validation_commands WHERE result_pk=?", (result_pk,)
    ).fetchone()[0]
    if len(rows) != count:
        _malformed("stored admission command references a foreign group")
    commands = []
    for row in rows:
        for field in ("command_id", "group_id", "entry", "group_entry"):
            _stored_string(row[field], f"admission command {field}")
        if row["entry"] != row["group_entry"]:
            _malformed("stored admission command entry disagrees with its group")
        command_pk = int(row["command_pk"])
        commands.append(
            ValidationAdmissionCommand(
                row["command_id"],
                row["group_id"],
                row["entry"],
                _admission_command_outputs(db, result_pk, command_pk),
            )
        )
    return tuple(commands)


def load_validation_admission(log_root: Path, result_id: str) -> ValidationAdmission:
    """Load only stored admission facts; no report or aggregate projection read."""
    with result_snapshot(log_root) as db:
        result = db.execute(
            "SELECT * FROM validation_results WHERE result_id=? AND slot='full'",
            (result_id,),
        ).fetchone()
        if result is None:
            raise ResultStoreError(
                "results.store.missing", "full validation result is absent"
        )
        _validate_stored_result_header(result)
        result_pk = int(result["result_pk"])
        groups = _admission_groups(db, result_pk)
        findings = _admission_findings(db, result_pk)
        commands = _admission_commands(db, result_pk)
        return ValidationAdmission(
            result["validation_id"], result_id, groups, commands, findings
        )


def load_finding_groups(
    log_root: Path,
) -> tuple[StoredValidationResult, dict[str, object], list[dict[str, object]]]:
    """Load the current full finding groups from normalized rows only."""
    with result_snapshot(log_root) as db:
        resolved = _resolve_latest_full(db)
        stored = StoredValidationResult(
            resolved.result_id,
            str(resolved.validation_id),
            resolved.generation,
        )
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_pk=?",
            (resolved.result_pk,),
        ).fetchone()
        groups = [
            _group(db, resolved.result_pk, row)
            for row in db.execute(
                "SELECT * FROM validation_groups WHERE result_pk=? "
                "ORDER BY group_kind,position",
                (resolved.result_pk,),
            )
        ]
    if meta is None:
        raise ResultStoreError(
            "results.store.malformed", "current validation disappeared"
        )
    return stored, dict(meta), groups


def list_finding_groups(
    log_root: Path, *, filters: Mapping[str, Sequence[str]]
) -> tuple[StoredValidationResult, dict[str, object], list[dict[str, object]]]:
    """Hydrate only groups selected by exact normalized SQL predicates."""
    with result_snapshot(log_root) as db:
        resolved = _resolve_latest_full(db)
        stored = StoredValidationResult(
            resolved.result_id,
            str(resolved.validation_id),
            resolved.generation,
        )
        clauses: list[str] = ["g.result_pk=?"]
        args: list[object] = [resolved.result_pk]
        entries = tuple(filters.get("entry", ()))
        if entries:
            clauses.append("g.entry IN (" + ",".join("?" for _ in entries) + ")")
            args.extend(entries)
        commands = tuple(filters.get("command", ()))
        if commands:
            clauses.append(
                "EXISTS (SELECT 1 FROM validation_commands c "
                "WHERE c.result_pk=g.result_pk AND c.group_pk=g.group_pk "
                "AND c.command_id IN ("
                + ",".join("?" for _ in commands)
                + "))"
            )
            args.extend(commands)
        finding_clauses: list[str] = []
        finding_args: list[object] = []
        for field, column in (
            ("area", "c.scope"),
            ("code", "k.code"),
            ("subject", "c.subject"),
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
                "("
                + " OR ".join("k.code=? OR k.code LIKE ?" for _ in families)
                + ")"
            )
            for family in families:
                finding_args.extend((family, family + ".%"))
        if finding_clauses:
            clauses.append(
                "EXISTS (SELECT 1 FROM validation_findings f "
                "JOIN validation_checks c USING (result_pk,check_pk) "
                "JOIN validation_codes k USING (result_pk,code_pk) "
                "WHERE f.result_pk=g.result_pk AND f.group_pk=g.group_pk AND "
                + " AND ".join(finding_clauses)
                + ")"
            )
            args.extend(finding_args)
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_pk=?",
            (resolved.result_pk,),
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
        groups = [_group(db, resolved.result_pk, row) for row in rows]
    if meta is None:
        raise ResultStoreError(
            "results.store.malformed", "current validation disappeared"
        )
    return stored, dict(meta), groups


def load_finding_group(
    log_root: Path, *, entry: str, group_id: str
) -> tuple[StoredValidationResult, dict[str, object], dict[str, object] | None]:
    """Load one exact current finding group without scanning sibling groups."""
    with result_snapshot(log_root) as db:
        resolved = _resolve_latest_full(db)
        stored = StoredValidationResult(
            resolved.result_id,
            str(resolved.validation_id),
            resolved.generation,
        )
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_pk=?",
            (resolved.result_pk,),
        ).fetchone()
        row = db.execute(
            "SELECT * FROM validation_groups "
            "WHERE result_pk=? AND entry=? AND group_id=?",
            (resolved.result_pk, entry, group_id),
        ).fetchone()
        group = _group(db, resolved.result_pk, row) if row is not None else None
    if meta is None:
        raise ResultStoreError(
            "results.store.malformed", "current validation disappeared"
        )
    return stored, dict(meta), group


def load_direct_finding(
    log_root: Path, check_id: str
) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, object]]:
    """Return current check, direct finding, and metadata without source reads."""
    with result_snapshot(log_root) as db:
        resolved = _resolve_latest_full(db)
        meta = db.execute(
            "SELECT summary,result_date FROM validation_results WHERE result_pk=?",
            (resolved.result_pk,),
        ).fetchone()
        assert meta is not None
        check = db.execute(
            "SELECT c.*,k.code FROM validation_checks AS c "
            "LEFT JOIN validation_codes AS k USING (result_pk,code_pk) "
            "WHERE c.result_pk=? AND c.check_id=?",
            (resolved.result_pk, check_id),
        ).fetchone()
        if check is None:
            return None, None, dict(meta)
        public_check = {
            "result_id": resolved.result_id,
            "check_id": check["check_id"],
            "scope": check["scope"],
            "status": check["status"],
            "subject": check["subject"],
            "failure_code": check["code"],
            "rule": check["rule"],
            "observed_json": check["observed_json"],
            "failure_dependency": check["failure_dependency"],
        }
        finding = db.execute(
            "SELECT f.*,g.group_id,c.scope,c.status,c.subject,c.rule,"
            "c.observed_json,k.code "
            "FROM validation_findings AS f "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "JOIN validation_checks AS c USING (result_pk,check_pk) "
            "JOIN validation_codes AS k USING (result_pk,code_pk) "
            "WHERE f.result_pk=? AND f.check_pk=?",
            (resolved.result_pk, check["check_pk"]),
        ).fetchone()
        if finding is None:
            return public_check, None, dict(meta)
        if finding["status"] not in _FAILING_CHECK_STATUSES:
            _malformed("stored finding has no failed check")
        value = {
            "result_id": resolved.result_id,
            "finding_id": check_id,
            "group_id": finding["group_id"],
            "position": finding["position"],
            "scope": finding["scope"],
            "status": finding["status"],
            "code": finding["code"],
            "projection_subject": finding["subject"],
            "rule": finding["rule"],
            "observed_json": finding["observed_json"],
            "admission_effect": finding["admission_effect"],
            "display_entry": finding["display_entry"],
            "display_subject": finding["display_subject"],
            "dependencies": [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT dependency_json FROM validation_check_dependencies "
                    "WHERE result_pk=? AND check_pk=? ORDER BY position",
                    (resolved.result_pk, check["check_pk"]),
                )
            ],
        }
        return public_check, value, dict(meta)


def _mechanical_record_from_db(
    db: Any, resolved: ResolvedValidationResult
) -> MechanicalGeneratedRecord:
    row = db.execute(
        "SELECT summary,result_date,rules_version FROM validation_results "
        "WHERE result_pk=?",
        (resolved.result_pk,),
    ).fetchone()
    if row is None:
        raise ResultStoreError("results.store.missing", "validation result is absent")
    checks = []
    for check in db.execute(
        "SELECT c.*,k.code FROM validation_checks AS c "
        "LEFT JOIN validation_codes AS k USING (result_pk,code_pk) "
        "WHERE c.result_pk=? ORDER BY c.check_id",
        (resolved.result_pk,),
    ):
        value: dict[str, object] = {
            "identity": check["check_id"],
            "scope": check["scope"],
            "status": check["status"],
            "subject": check["subject"],
            "dependencies": [
                json.loads(x[0])
                for x in db.execute(
                    "SELECT dependency_json FROM validation_check_dependencies "
                    "WHERE result_pk=? AND check_pk=? ORDER BY position",
                    (resolved.result_pk, check["check_pk"]),
                )
            ],
        }
        if check["code"] is not None:
            failure: dict[str, object] = {
                "code": check["code"],
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


def load_mechanical_record(log_root: Path, result_id: str) -> MechanicalGeneratedRecord:
    with result_snapshot(log_root) as db:
        resolved = resolve_validation_result(db, result_id)
        _audit_validation_result(db, resolved.result_pk)
        return _mechanical_record_from_db(db, resolved)


def load_validation_report_projection(log_root: Path) -> ValidationReportProjection:
    """Load a full report projection without consulting current research files."""

    from .human_projection import EntryPresentation, ReportContext, project_findings

    with result_snapshot(log_root) as db:
        resolved = _resolve_latest_full(db)
        _audit_validation_report_rows(db, resolved.result_pk)
        stored = StoredValidationResult(
            resolved.result_id,
            str(resolved.validation_id),
            resolved.generation,
        )
        record = _mechanical_record_from_db(db, resolved)
        row = db.execute(
            "SELECT report_context_json FROM validation_results WHERE result_pk=?",
            (resolved.result_pk,),
        ).fetchone()
        displays = {
            value["check_id"]: (value["display_entry"], value["display_subject"])
            for value in db.execute(
                "SELECT c.check_id,f.display_entry,f.display_subject "
                "FROM validation_findings AS f "
                "JOIN validation_checks AS c USING (result_pk,check_pk) "
                "JOIN validation_results AS r USING (result_pk) "
                "WHERE f.result_pk=?",
                (resolved.result_pk,),
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
    with result_snapshot(log_root) as db:
        if result_id is None:
            selected_row = db.execute(
                "SELECT result_id FROM validation_results WHERE slot='full'"
            ).fetchone()
            if selected_row is None:
                raise ResultStoreError(
                    "results.store.missing", "no completed full validation result"
                )
            selected = str(selected_row["result_id"])
        else:
            selected = result_id
        resolved = resolve_validation_result(db, selected)
        _audit_validation_result(db, resolved.result_pk)
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
                "SELECT * FROM validation_groups WHERE result_pk=? ORDER BY group_kind, position",
                (resolved.result_pk,),
            )
        )
        batches = [
            _batch(db, resolved.result_pk, x)
            for x in db.execute(
                "SELECT * FROM validation_batches WHERE result_pk=? ORDER BY position",
                (resolved.result_pk,),
            )
        ]
        values = [
            (x["group_kind"], _group(db, resolved.result_pk, x)) for x in groups
        ]
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


def _validate_selected_positions(
    db: Any,
    result_pk: int,
    table: str,
    predicates: Mapping[str, object],
) -> None:
    """Validate dense order for one selected parent without scanning siblings."""

    suffix = "".join(f" AND {column}=?" for column in predicates)
    positions = [
        row[0]
        for row in db.execute(
            f"SELECT position FROM {table} WHERE result_pk=?{suffix} "
            "ORDER BY position",
            (result_pk, *predicates.values()),
        )
    ]
    if positions != list(range(len(positions))):
        _malformed(f"invalid stored {table} positions")


def _registry_public_value(db: Any, result_pk: int, row: Any) -> dict[str, object]:
    _validate_selected_positions(
        db,
        result_pk,
        "validation_registry_identity_members",
        {"registry_pk": row["registry_pk"]},
    )
    identity: dict[str, object] = {"algorithm": row["identity_algorithm"]}
    if row["identity_commit"] is not None:
        identity["commit"] = row["identity_commit"]
    members = _values(
        db,
        "SELECT member FROM validation_registry_identity_members "
        "WHERE result_pk=? AND registry_pk=? ORDER BY position",
        (result_pk, row["registry_pk"]),
        "member",
    )
    if row["identity_algorithm"] == "identity-files-sha256-v1":
        identity["files"] = members
    elif row["identity_algorithm"] == "identity-patterns-sha256-v1":
        identity["patterns"] = members
    value: dict[str, object] = {
        "entry": row["owner_entry"],
        "name": row["name"],
        "kind": row["kind"],
        "location": row["location"],
        "path": row["path"],
        "origin": bool(row["origin"]),
        "identity": identity,
    }
    if row["from_entry"] is not None:
        value["from_entry"] = row["from_entry"]
        value["read_only"] = True
    if hashlib.sha256(_json(value).encode()).hexdigest() != row["payload_sha256"]:
        _malformed("stored registry payload hash disagrees")
    return value


def _command_public_value(db: Any, result_pk: int, command: Any) -> dict[str, Any]:
    command_pk = int(command["command_pk"])
    for table, predicates in (
        ("validation_command_tokens", {"command_pk": command_pk}),
        ("validation_command_collections", {"command_pk": command_pk}),
    ):
        _validate_selected_positions(db, result_pk, table, predicates)
    for direction in ("input", "output"):
        _validate_selected_positions(
            db,
            result_pk,
            "validation_command_relationships",
            {"command_pk": command_pk, "direction": direction},
        )
    value = {
        key: command[key]
        for key in ("document", "entry", "fence", "ordinal", "script")
    }
    value["identity"] = command["command_id"]
    value["tokens"] = _values(
        db,
        "SELECT token FROM validation_command_tokens "
        "WHERE result_pk=? AND command_pk=? ORDER BY position",
        (result_pk, command_pk),
        "token",
    )
    for direction, key in (("input", "inputs"), ("output", "outputs")):
        value[key] = [
            {
                name: relationship[name]
                for name in ("direction", "path", "proof", "target", "artifact")
                if relationship[name] is not None
            }
            | ({"origin": True} if relationship["origin"] else {})
            for relationship in db.execute(
                "SELECT r.direction,COALESCE(a.artifact_id,r.path_text) AS path,"
                "r.proof,r.target,r.artifact,r.origin "
                "FROM validation_command_relationships AS r "
                "LEFT JOIN validation_artifacts AS a "
                "ON a.result_pk=r.result_pk AND a.artifact_pk=r.path_artifact_pk "
                "WHERE r.result_pk=? AND r.command_pk=? AND r.direction=? "
                "ORDER BY r.position",
                (result_pk, command_pk, direction),
            )
        ]
    collections = []
    for collection in db.execute(
        "SELECT * FROM validation_command_collections "
        "WHERE result_pk=? AND command_pk=? ORDER BY position",
        (result_pk, command_pk),
    ):
        _validate_selected_positions(
            db,
            result_pk,
            "validation_collection_members",
            {
                "command_pk": command_pk,
                "collection_position": collection["position"],
            },
        )
        collections.append(
            {
                "direction": collection["direction"],
                "mechanism": collection["mechanism"],
                "root": collection["root"],
                "target": collection["target"],
                "members": _values(
                    db,
                    "SELECT path FROM validation_collection_members "
                    "WHERE result_pk=? AND command_pk=? "
                    "AND collection_position=? ORDER BY position",
                    (result_pk, command_pk, collection["position"]),
                    "path",
                ),
            }
        )
    value["collections"] = collections
    return value


def _finding_public_value(db: Any, result_pk: int, finding: Any) -> dict[str, Any]:
    check_pk = int(finding["check_pk"])
    for table in (
        "validation_check_dependencies",
        "validation_finding_affected_chains",
        "validation_finding_affected_entries",
    ):
        _validate_selected_positions(
            db, result_pk, table, {"check_pk": check_pk}
        )
    return {
        "identity": finding["check_id"],
        "scope": finding["scope"],
        "status": finding["status"],
        "code": finding["code"],
        "subject": finding["subject"],
        "rule": finding["rule"],
        "observed": json.loads(finding["observed_json"]),
        "admission_effect": finding["admission_effect"],
        "dependencies": [
            json.loads(value)
            for value in _values(
                db,
                "SELECT dependency_json FROM validation_check_dependencies "
                "WHERE result_pk=? AND check_pk=? ORDER BY position",
                (result_pk, check_pk),
                "dependency_json",
            )
        ],
        "affected_chains": _values(
            db,
            "SELECT g.group_id "
            "FROM validation_finding_affected_chains AS a "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE a.result_pk=? AND a.check_pk=? ORDER BY a.position",
            (result_pk, check_pk),
            "group_id",
        ),
        "affected_entries": _values(
            db,
            "SELECT entry FROM validation_finding_affected_entries "
            "WHERE result_pk=? AND check_pk=? ORDER BY position",
            (result_pk, check_pk),
            "entry",
        ),
    }


def _group(db: Any, result_pk: int, row: Any) -> dict[str, Any]:
    group_pk = int(row["group_pk"])
    for table in (
        "validation_commands",
        "validation_findings",
        "validation_group_artifacts",
        "validation_group_edges",
        "validation_group_signals",
        "validation_group_registry",
    ):
        _validate_selected_positions(
            db, result_pk, table, {"group_pk": group_pk}
        )
    commands = [
        _command_public_value(db, result_pk, command)
        for command in db.execute(
            "SELECT * FROM validation_commands WHERE result_pk=? AND group_pk=? "
            "ORDER BY position",
            (result_pk, group_pk),
        )
    ]

    findings = []
    for finding in db.execute(
        "SELECT f.*,c.check_id,c.scope,c.status,c.subject,c.rule,c.observed_json,"
        "c.failure_dependency,k.code "
        "FROM validation_findings AS f "
        "JOIN validation_checks AS c USING (result_pk,check_pk) "
        "JOIN validation_codes AS k USING (result_pk,code_pk) "
        "WHERE f.result_pk=? AND f.group_pk=? ORDER BY f.position",
        (result_pk, group_pk),
    ):
        findings.append(_finding_public_value(db, result_pk, finding))
    result = {"chain_id": row["group_id"], "entry": row["entry"], "findings": findings}
    if row["group_kind"] == "unresolved":
        result["reason"] = row["reason"]
        return result

    result.update(
        {
            "commands": commands,
            "artifacts": _values(
                db,
                "SELECT a.artifact_id FROM validation_group_artifacts AS m "
                "JOIN validation_artifacts AS a USING (result_pk,artifact_pk) "
                "WHERE m.result_pk=? AND m.group_pk=? ORDER BY m.position",
                (result_pk, group_pk),
                "artifact_id",
            ),
            "edges": [
                dict(edge)
                for edge in db.execute(
                    "SELECT s.command_id AS source,t.command_id AS target,"
                    "a.artifact_id AS artifact "
                    "FROM validation_group_edges AS e "
                    "JOIN validation_commands AS s "
                    "ON s.result_pk=e.result_pk AND s.command_pk=e.source_command_pk "
                    "JOIN validation_commands AS t "
                    "ON t.result_pk=e.result_pk AND t.command_pk=e.target_command_pk "
                    "JOIN validation_artifacts AS a USING (result_pk,artifact_pk) "
                    "WHERE e.result_pk=? AND e.group_pk=? ORDER BY e.position",
                    (result_pk, group_pk),
                )
            ],
            "signals": _values(
                db,
                "SELECT signal FROM validation_group_signals "
                "WHERE result_pk=? AND group_pk=? ORDER BY position",
                (result_pk, group_pk),
                "signal",
            ),
            "registry": [
                _registry_public_value(db, result_pk, registry)
                for registry in db.execute(
                    "SELECT r.* FROM validation_group_registry AS m "
                    "JOIN validation_registry_records AS r "
                    "USING (result_pk,registry_pk) "
                    "WHERE m.result_pk=? AND m.group_pk=? ORDER BY m.position",
                    (result_pk, group_pk),
                )
            ],
        }
    )
    return result


def _batch(db: Any, result_pk: int, row: Any) -> dict[str, Any]:
    batch_pk = int(row["batch_pk"])
    for table in (
        "validation_batch_entries",
        "validation_batch_anchors",
        "validation_batch_findings",
        "validation_batch_groups",
        "validation_batch_related_batches",
    ):
        _validate_selected_positions(
            db, result_pk, table, {"batch_pk": batch_pk}
        )
    starting = db.execute(
        "SELECT check_id FROM validation_checks WHERE result_pk=? AND check_pk=?",
        (result_pk, row["starting_check_pk"]),
    ).fetchone()
    if starting is None:
        _malformed("stored repair batch starting finding is absent")
    result = {
        "batch_id": row["batch_id"],
        "batch_type": row["batch_type"],
        "grouping_reason": row["grouping_reason"],
        "scope": row["scope"],
        "primary_finding_count": row["primary_finding_count"],
        "starting_finding_id": starting["check_id"],
        "entries": _values(
            db,
            "SELECT entry FROM validation_batch_entries "
            "WHERE result_pk=? AND batch_pk=? ORDER BY position",
            (result_pk, batch_pk),
            "entry",
        ),
        "anchors": [
            json.loads(value)
            for value in _values(
                db,
                "SELECT anchor_json FROM validation_batch_anchors "
                "WHERE result_pk=? AND batch_pk=? ORDER BY position",
                (result_pk, batch_pk),
                "anchor_json",
            )
        ],
        "primary_finding_ids": _values(
            db,
            "SELECT c.check_id FROM validation_batch_findings AS m "
            "JOIN validation_checks AS c USING (result_pk,check_pk) "
            "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
            (result_pk, batch_pk),
            "check_id",
        ),
        "related_chain_ids": _values(
            db,
            "SELECT g.group_id FROM validation_batch_groups AS m "
            "JOIN validation_groups AS g USING (result_pk,group_pk) "
            "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
            (result_pk, batch_pk),
            "group_id",
        ),
        "related_batch_ids": _values(
            db,
            "SELECT b.batch_id FROM validation_batch_related_batches AS m "
            "JOIN validation_batches AS b "
            "ON b.result_pk=m.result_pk AND b.batch_pk=m.related_batch_pk "
            "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
            (result_pk, batch_pk),
            "batch_id",
        ),
    }
    return result


def _write_keyed_rows(
    encoder: CappedJsonEncoder,
    rows: Any,
    key: str,
    public_value: Any,
) -> None:
    encoder.write_object(
        ((str(row[key]), row) for row in rows),
        lambda target, row: target.write_value(public_value(row)),
    )


def _write_callbacks(
    encoder: CappedJsonEncoder,
    members: Sequence[tuple[str, Any]],
) -> None:
    encoder.write_object(members, lambda target, writer: writer(target))


def _write_row_values(
    encoder: CappedJsonEncoder,
    rows: Any,
    column: str,
) -> None:
    encoder.write_array(rows, lambda target, row: target.write_value(row[column]))


def _write_json_rows(
    encoder: CappedJsonEncoder,
    rows: Any,
    column: str,
) -> None:
    encoder.write_array(rows, lambda target, row: target.append(row[column]))


def _write_command_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, command: Any
) -> None:
    command_pk = int(command["command_pk"])

    def relationships(target: CappedJsonEncoder, direction: str) -> None:
        target.write_array(
            db.execute(
                "SELECT r.direction,COALESCE(a.artifact_id,r.path_text) AS path,"
                "r.proof,r.target,r.artifact,r.origin "
                "FROM validation_command_relationships AS r "
                "LEFT JOIN validation_artifacts AS a "
                "ON a.result_pk=r.result_pk AND a.artifact_pk=r.path_artifact_pk "
                "WHERE r.result_pk=? AND r.command_pk=? AND r.direction=? "
                "ORDER BY r.position",
                (result_pk, command_pk, direction),
            ),
            lambda output, row: output.write_value(
                {
                    key: row[key]
                    for key in (
                        "direction",
                        "path",
                        "proof",
                        "target",
                        "artifact",
                    )
                    if row[key] is not None
                }
                | ({"origin": True} if row["origin"] else {})
            ),
        )

    def collections(target: CappedJsonEncoder) -> None:
        def write_collection(output: CappedJsonEncoder, row: Any) -> None:
            _write_callbacks(
                output,
                (
                    ("direction", lambda item: item.write_value(row["direction"])),
                    ("mechanism", lambda item: item.write_value(row["mechanism"])),
                    (
                        "members",
                        lambda item: _write_row_values(
                            item,
                            db.execute(
                                "SELECT path FROM validation_collection_members "
                                "WHERE result_pk=? AND command_pk=? "
                                "AND collection_position=? ORDER BY position",
                                (result_pk, command_pk, row["position"]),
                            ),
                            "path",
                        ),
                    ),
                    ("root", lambda item: item.write_value(row["root"])),
                    ("target", lambda item: item.write_value(row["target"])),
                ),
            )

        target.write_array(
            db.execute(
                "SELECT * FROM validation_command_collections "
                "WHERE result_pk=? AND command_pk=? ORDER BY position",
                (result_pk, command_pk),
            ),
            write_collection,
        )

    _write_callbacks(
        encoder,
        (
            ("collections", collections),
            ("document", lambda target: target.write_value(command["document"])),
            ("entry", lambda target: target.write_value(command["entry"])),
            ("fence", lambda target: target.write_value(command["fence"])),
            ("identity", lambda target: target.write_value(command["command_id"])),
            ("inputs", lambda target: relationships(target, "input")),
            ("ordinal", lambda target: target.write_value(command["ordinal"])),
            ("outputs", lambda target: relationships(target, "output")),
            ("script", lambda target: target.write_value(command["script"])),
            (
                "tokens",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT token FROM validation_command_tokens "
                        "WHERE result_pk=? AND command_pk=? ORDER BY position",
                        (result_pk, command_pk),
                    ),
                    "token",
                ),
            ),
        ),
    )


def _write_finding_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, finding: Any
) -> None:
    check_pk = int(finding["check_pk"])
    _write_callbacks(
        encoder,
        (
            (
                "admission_effect",
                lambda target: target.write_value(finding["admission_effect"]),
            ),
            (
                "affected_chains",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT g.group_id FROM validation_finding_affected_chains a "
                        "JOIN validation_groups g USING (result_pk,group_pk) "
                        "WHERE a.result_pk=? AND a.check_pk=? ORDER BY a.position",
                        (result_pk, check_pk),
                    ),
                    "group_id",
                ),
            ),
            (
                "affected_entries",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT entry FROM validation_finding_affected_entries "
                        "WHERE result_pk=? AND check_pk=? ORDER BY position",
                        (result_pk, check_pk),
                    ),
                    "entry",
                ),
            ),
            ("code", lambda target: target.write_value(finding["code"])),
            (
                "dependencies",
                lambda target: _write_json_rows(
                    target,
                    db.execute(
                        "SELECT dependency_json FROM validation_check_dependencies "
                        "WHERE result_pk=? AND check_pk=? ORDER BY position",
                        (result_pk, check_pk),
                    ),
                    "dependency_json",
                ),
            ),
            ("identity", lambda target: target.write_value(finding["check_id"])),
            ("observed", lambda target: target.append(finding["observed_json"])),
            ("rule", lambda target: target.write_value(finding["rule"])),
            ("scope", lambda target: target.write_value(finding["scope"])),
            ("status", lambda target: target.write_value(finding["status"])),
            ("subject", lambda target: target.write_value(finding["subject"])),
        ),
    )


def _write_registry_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, row: Any
) -> None:
    identity_members: list[tuple[str, Any]] = [
        ("algorithm", lambda target: target.write_value(row["identity_algorithm"]))
    ]
    if row["identity_commit"] is not None:
        identity_members.append(
            ("commit", lambda target: target.write_value(row["identity_commit"]))
        )
    member_name = {
        "identity-files-sha256-v1": "files",
        "identity-patterns-sha256-v1": "patterns",
    }.get(row["identity_algorithm"])
    if member_name is not None:
        identity_members.append(
            (
                member_name,
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT member FROM validation_registry_identity_members "
                        "WHERE result_pk=? AND registry_pk=? ORDER BY position",
                        (result_pk, row["registry_pk"]),
                    ),
                    "member",
                ),
            )
        )
    members: list[tuple[str, Any]] = [
        ("entry", lambda target: target.write_value(row["owner_entry"])),
    ]
    if row["from_entry"] is not None:
        members.append(
            ("from_entry", lambda target: target.write_value(row["from_entry"]))
        )
    members.extend(
        (
            ("identity", lambda target: _write_callbacks(target, identity_members)),
            ("kind", lambda target: target.write_value(row["kind"])),
            ("location", lambda target: target.write_value(row["location"])),
            ("name", lambda target: target.write_value(row["name"])),
            ("origin", lambda target: target.write_value(bool(row["origin"]))),
            ("path", lambda target: target.write_value(row["path"])),
        )
    )
    if row["from_entry"] is not None:
        members.append(("read_only", lambda target: target.write_value(True)))
    _write_callbacks(encoder, tuple(members))


def _write_group_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, row: Any
) -> None:
    group_pk = int(row["group_pk"])
    finding_select = (
        "SELECT f.*,c.check_id,c.scope,c.status,c.subject,c.rule,"
        "c.observed_json,c.failure_dependency,k.code "
        "FROM validation_findings f "
        "JOIN validation_checks c USING (result_pk,check_pk) "
        "JOIN validation_codes k USING (result_pk,code_pk) "
        "WHERE f.result_pk=? AND f.group_pk=? ORDER BY f.position"
    )

    def findings(target: CappedJsonEncoder) -> None:
        target.write_array(
            db.execute(finding_select, (result_pk, group_pk)),
            lambda output, finding: _write_finding_export(
                output, db, result_pk, finding
            ),
        )

    if row["group_kind"] == "unresolved":
        _write_callbacks(
            encoder,
            (
                ("chain_id", lambda target: target.write_value(row["group_id"])),
                ("entry", lambda target: target.write_value(row["entry"])),
                ("findings", findings),
                ("reason", lambda target: target.write_value(row["reason"])),
            ),
        )
        return

    def commands(target: CappedJsonEncoder) -> None:
        target.write_array(
            db.execute(
                "SELECT * FROM validation_commands "
                "WHERE result_pk=? AND group_pk=? ORDER BY position",
                (result_pk, group_pk),
            ),
            lambda output, command: _write_command_export(
                output, db, result_pk, command
            ),
        )

    def registry(target: CappedJsonEncoder) -> None:
        target.write_array(
            db.execute(
                "SELECT r.* FROM validation_group_registry m "
                "JOIN validation_registry_records r USING (result_pk,registry_pk) "
                "WHERE m.result_pk=? AND m.group_pk=? ORDER BY m.position",
                (result_pk, group_pk),
            ),
            lambda output, registry_row: _write_registry_export(
                output, db, result_pk, registry_row
            ),
        )

    _write_callbacks(
        encoder,
        (
            (
                "artifacts",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT a.artifact_id FROM validation_group_artifacts m "
                        "JOIN validation_artifacts a USING (result_pk,artifact_pk) "
                        "WHERE m.result_pk=? AND m.group_pk=? ORDER BY m.position",
                        (result_pk, group_pk),
                    ),
                    "artifact_id",
                ),
            ),
            ("chain_id", lambda target: target.write_value(row["group_id"])),
            ("commands", commands),
            (
                "edges",
                lambda target: target.write_array(
                    db.execute(
                        "SELECT s.command_id AS source,t.command_id AS target,"
                        "a.artifact_id AS artifact FROM validation_group_edges e "
                        "JOIN validation_commands s ON s.result_pk=e.result_pk "
                        "AND s.command_pk=e.source_command_pk "
                        "JOIN validation_commands t ON t.result_pk=e.result_pk "
                        "AND t.command_pk=e.target_command_pk "
                        "JOIN validation_artifacts a USING (result_pk,artifact_pk) "
                        "WHERE e.result_pk=? AND e.group_pk=? ORDER BY e.position",
                        (result_pk, group_pk),
                    ),
                    lambda output, edge: output.write_value(dict(edge)),
                ),
            ),
            ("entry", lambda target: target.write_value(row["entry"])),
            ("findings", findings),
            ("registry", registry),
            (
                "signals",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT signal FROM validation_group_signals "
                        "WHERE result_pk=? AND group_pk=? ORDER BY position",
                        (result_pk, group_pk),
                    ),
                    "signal",
                ),
            ),
        ),
    )


def _write_check_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, row: Any
) -> None:
    def failure(target: CappedJsonEncoder) -> None:
        if row["code"] is None:
            target.write_value(None)
            return
        _write_callbacks(
            target,
            (
                ("code", lambda output: output.write_value(row["code"])),
                (
                    "dependency",
                    lambda output: output.write_value(row["failure_dependency"]),
                ),
                ("observed", lambda output: output.append(row["observed_json"])),
                ("rule", lambda output: output.write_value(row["rule"])),
            ),
        )

    _write_callbacks(
        encoder,
        (
            (
                "dependencies",
                lambda target: _write_json_rows(
                    target,
                    db.execute(
                        "SELECT dependency_json FROM validation_check_dependencies "
                        "WHERE result_pk=? AND check_pk=? ORDER BY position",
                        (result_pk, row["check_pk"]),
                    ),
                    "dependency_json",
                ),
            ),
            ("failure", failure),
            ("identity", lambda target: target.write_value(row["check_id"])),
            ("scope", lambda target: target.write_value(row["scope"])),
            ("status", lambda target: target.write_value(row["status"])),
            ("subject", lambda target: target.write_value(row["subject"])),
        ),
    )


def _write_batch_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, row: Any
) -> None:
    batch_pk = int(row["batch_pk"])
    starting = db.execute(
        "SELECT check_id FROM validation_checks WHERE result_pk=? AND check_pk=?",
        (result_pk, row["starting_check_pk"]),
    ).fetchone()
    if starting is None:
        _malformed("stored repair batch starting finding is absent")

    def values(target: CappedJsonEncoder, table: str, column: str) -> None:
        _write_row_values(
            target,
            db.execute(
                f"SELECT {column} FROM {table} "
                "WHERE result_pk=? AND batch_pk=? ORDER BY position",
                (result_pk, batch_pk),
            ),
            column,
        )

    _write_callbacks(
        encoder,
        (
            (
                "anchors",
                lambda target: _write_json_rows(
                    target,
                    db.execute(
                        "SELECT anchor_json FROM validation_batch_anchors "
                        "WHERE result_pk=? AND batch_pk=? ORDER BY position",
                        (result_pk, batch_pk),
                    ),
                    "anchor_json",
                ),
            ),
            ("batch_id", lambda target: target.write_value(row["batch_id"])),
            ("batch_type", lambda target: target.write_value(row["batch_type"])),
            (
                "entries",
                lambda target: values(
                    target, "validation_batch_entries", "entry"
                ),
            ),
            (
                "grouping_reason",
                lambda target: target.write_value(row["grouping_reason"]),
            ),
            (
                "primary_finding_count",
                lambda target: target.write_value(row["primary_finding_count"]),
            ),
            (
                "primary_finding_ids",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT c.check_id FROM validation_batch_findings m "
                        "JOIN validation_checks c USING (result_pk,check_pk) "
                        "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
                        (result_pk, batch_pk),
                    ),
                    "check_id",
                ),
            ),
            (
                "related_batch_ids",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT b.batch_id FROM validation_batch_related_batches m "
                        "JOIN validation_batches b ON b.result_pk=m.result_pk "
                        "AND b.batch_pk=m.related_batch_pk "
                        "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
                        (result_pk, batch_pk),
                    ),
                    "batch_id",
                ),
            ),
            (
                "related_chain_ids",
                lambda target: _write_row_values(
                    target,
                    db.execute(
                        "SELECT g.group_id FROM validation_batch_groups m "
                        "JOIN validation_groups g USING (result_pk,group_pk) "
                        "WHERE m.result_pk=? AND m.batch_pk=? ORDER BY m.position",
                        (result_pk, batch_pk),
                    ),
                    "group_id",
                ),
            ),
            ("scope", lambda target: target.write_value(row["scope"])),
            (
                "starting_finding_id",
                lambda target: target.write_value(starting["check_id"]),
            ),
        ),
    )


def _write_metadata_export(
    encoder: CappedJsonEncoder, db: Any, result_pk: int, row: Any
) -> None:
    def entries(target: CappedJsonEncoder) -> None:
        target.write_object(
            (
                (relation, relation)
                for relation in ("dependency", "evaluated", "requested")
            ),
            lambda output, relation: _write_row_values(
                output,
                db.execute(
                    "SELECT entry FROM validation_result_entries "
                    "WHERE result_pk=? AND relation=? ORDER BY position",
                    (result_pk, relation),
                ),
                "entry",
            ),
        )

    scalar_names = (
        "entry",
        "evaluated_checks",
        "finding_count",
        "finished_at",
        "generation",
        "kind",
        "projection_schema",
        "reason",
        "record_identity",
        "report_context_json",
        "result_date",
        "result_id",
        "rules_version",
        "slot",
        "source_identity",
        "started_at",
        "status",
        "stored_at",
        "summary",
        "validation_id",
    )
    def scalar(name: str) -> Callable[[CappedJsonEncoder], None]:
        def write(target: CappedJsonEncoder) -> None:
            target.write_value(row[name])

        return write

    writers: dict[str, Callable[[CappedJsonEncoder], None]] = {
        name: scalar(name) for name in scalar_names
    }
    writers["entries"] = entries
    writers["whole_log_limitations"] = lambda target: _write_row_values(
        target,
        db.execute(
            "SELECT code FROM validation_result_limitations "
            "WHERE result_pk=? ORDER BY position",
            (result_pk,),
        ),
        "code",
    )
    _write_callbacks(
        encoder,
        tuple((name, writers[name]) for name in sorted(writers)),
    )


def export_validation_result(
    log_root: Path, result_id: str, *, connection: Any | None = None
) -> dict[str, object]:
    """Stream, bound, then materialize the explicit retained-result/2 export."""

    try:
        with (
            result_snapshot(log_root)
            if connection is None
            else nullcontext(connection)
        ) as db:
            resolved = resolve_validation_result(db, result_id)
            result_pk = resolved.result_pk
            _audit_validation_result(db, result_pk)
            row = db.execute(
                "SELECT result_id,generation,slot,kind,entry,summary,status,reason,"
                "evaluated_checks,finding_count,started_at,finished_at,stored_at,"
                "result_date,rules_version,source_identity,validation_id,record_identity,"
                "projection_schema,report_context_json "
                "FROM validation_results WHERE result_pk=?",
                (result_pk,),
            ).fetchone()
            assert row is not None
            encoder = CappedJsonEncoder(MAX_VALIDATION_RESULT_BYTES)

            def write_artifacts(target: CappedJsonEncoder) -> None:
                _write_keyed_rows(
                    target,
                    db.execute(
                        "SELECT artifact_id FROM validation_artifacts "
                        "WHERE result_pk=? ORDER BY artifact_id",
                        (result_pk,),
                    ),
                    "artifact_id",
                    lambda item: {"path": item["artifact_id"]},
                )

            def write_batches(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["batch_id"]), item)
                        for item in db.execute(
                            "SELECT * FROM validation_batches WHERE result_pk=? "
                            "ORDER BY batch_id",
                            (result_pk,),
                        )
                    ),
                    lambda output, item: _write_batch_export(
                        output, db, result_pk, item
                    ),
                )

            def write_chains(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["group_id"]), item)
                        for item in db.execute(
                            "SELECT * FROM validation_groups WHERE result_pk=? "
                            "ORDER BY group_id",
                            (result_pk,),
                        )
                    ),
                    lambda output, item: _write_group_export(
                        output, db, result_pk, item
                    ),
                )

            def write_checks(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["check_id"]), item)
                        for item in db.execute(
                            "SELECT c.*,k.code FROM validation_checks AS c "
                            "LEFT JOIN validation_codes AS k "
                            "USING (result_pk,code_pk) "
                            "WHERE c.result_pk=? ORDER BY c.check_id",
                            (result_pk,),
                        )
                    ),
                    lambda output, item: _write_check_export(
                        output, db, result_pk, item
                    ),
                )

            def write_codes(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["code"]), item)
                        for item in db.execute(
                            "SELECT code_pk,code FROM validation_codes "
                            "WHERE result_pk=? ORDER BY code",
                            (result_pk,),
                        )
                    ),
                    lambda output, item: _write_row_values(
                        output,
                        db.execute(
                            "SELECT c.check_id FROM validation_findings AS f "
                            "JOIN validation_checks AS c USING (result_pk,check_pk) "
                            "JOIN validation_groups AS g "
                            "ON g.result_pk=f.result_pk AND g.group_pk=f.group_pk "
                            "WHERE f.result_pk=? AND c.code_pk=? "
                            "ORDER BY g.group_kind,g.position,f.position,c.check_id",
                            (result_pk, item["code_pk"]),
                        ),
                        "check_id",
                    ),
                )

            finding_select = (
                "SELECT f.*,c.check_id,c.scope,c.status,c.subject,c.rule,"
                "c.observed_json,c.failure_dependency,k.code "
                "FROM validation_findings AS f "
                "JOIN validation_checks AS c USING (result_pk,check_pk) "
                "JOIN validation_codes AS k USING (result_pk,code_pk) "
                "WHERE f.result_pk=? ORDER BY c.check_id"
            )

            def write_findings(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["check_id"]), item)
                        for item in db.execute(finding_select, (result_pk,))
                    ),
                    lambda output, item: _write_finding_export(
                        output, db, result_pk, item
                    ),
                )

            def write_commands(target: CappedJsonEncoder) -> None:
                target.write_object(
                    (
                        (str(item["command_id"]), item)
                        for item in db.execute(
                            "SELECT * FROM validation_commands WHERE result_pk=? "
                            "ORDER BY command_id",
                            (result_pk,),
                        )
                    ),
                    lambda output, item: _write_command_export(
                        output, db, result_pk, item
                    ),
                )

            members = (
                ("artifacts", write_artifacts),
                ("batches", write_batches),
                ("chains", write_chains),
                ("checks", write_checks),
                ("codes", write_codes),
                ("commands", write_commands),
                ("findings", write_findings),
                (
                    "metadata",
                    lambda target: _write_metadata_export(
                        target, db, result_pk, row
                    ),
                ),
                ("result_id", lambda target: target.write_value(result_id)),
                (
                    "schema",
                    lambda target: target.write_value(
                        "research-log-retained-result/2"
                    ),
                ),
            )
            encoder.write_object(
                members, lambda target, writer: writer(target)
            )
            exported = encoder.materialize()
    except ExportTooLarge as error:
        raise ResultStoreError(
            "results.export.too_large",
            "validation result export exceeds 64 MiB",
        ) from error
    assert isinstance(exported, dict)
    return cast(dict[str, object], exported)


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
            result_pk = int(
                db.execute(
                    "SELECT COALESCE(MAX(result_pk), 0) + 1 FROM validation_results"
                ).fetchone()[0]
            )
            db.execute(
                "INSERT INTO validation_results "
                "(result_pk,result_id, generation, slot, kind, entry, summary, status, reason, "
                "evaluated_checks, finding_count, started_at, finished_at, stored_at, "
                "result_date, rules_version, source_identity, validation_id, "
                "record_identity, projection_schema, report_context_json) "
                "VALUES (?, ?, ?, 'diagnostic', 'diagnostic', NULL, ?, 'failed', ?, "
                "0, 0, ?, ?, ?, '', '', NULL, NULL, NULL, NULL, NULL)",
                (result_pk, result_id, generation, summary, reason, now, now, now),
            )
            # Diagnostic commands are still normalized command rows.  Their
            # synthetic group is owned by this replaceable result and cascades
            # with it, so a subsequent diagnostic publication cannot strand rows.
            db.execute(
                "INSERT INTO validation_groups VALUES "
                "(?, 1, 'diagnostic', 'unresolved', '', ?, 0)",
                (result_pk, reason),
            )
            for position, raw in enumerate(commands):
                if not isinstance(raw, Mapping):
                    continue
                identity = str(raw.get("identity", f"diagnostic:{position}"))
                entry = str(raw.get("entry", ""))
                db.execute(
                    "INSERT INTO validation_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        result_pk,
                        position + 1,
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
