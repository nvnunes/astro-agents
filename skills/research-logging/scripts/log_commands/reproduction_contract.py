"""Exact deterministic projection contracts for reproduction planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from research_log_data import (
    DataFile,
    data_file_from_fields,
    parse_fingerprint,
    parse_resource_identity,
    resolve_input_token,
)
from validation.evidence import EvidenceRecord
from validation.pyrun_state import PyrunExecution

PLAN_SCHEMA = "research-log-reproduction-plan/9"
PREEXECUTION_RESULT_SCHEMA = "research-log-reproduction-result/9"
REPRODUCTION_RESULT_SCHEMA = "research-log-reproduction-result/10"
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_PLAN_SUMMARY_ENTRIES = 20
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 5 * 60
MAX_EXECUTION_TIMEOUT_SECONDS = 7 * 24 * 60 * 60


def valid_reproduction_target(value: object) -> bool:
    """Validate the only current durable targets: a complete log or entry."""

    from .context import ENTRY_ID_RE

    if not isinstance(value, Mapping):
        return False
    if value == {"kind": "log", "entry": None}:
        return True
    entry = value.get("entry")
    if not isinstance(entry, str) or ENTRY_ID_RE.fullmatch(entry) is None:
        return False
    if value.get("kind") == "entry":
        return set(value) == {"kind", "entry"}
    return False


def valid_historical_reproduction_target(value: object) -> bool:
    """Validate a read-only result target, including legacy execution rows."""

    if valid_reproduction_target(value):
        return True
    from validation.pyrun_state import PYRUN_EXECUTION_RE

    from .context import ENTRY_ID_RE

    if not isinstance(value, Mapping):
        return False
    entry = value.get("entry")
    identity = value.get("execution_id")
    return (
        set(value) == {"kind", "entry", "execution_id"}
        and value.get("kind") == "execution"
        and isinstance(entry, str)
        and ENTRY_ID_RE.fullmatch(entry) is not None
        and isinstance(identity, str)
        and PYRUN_EXECUTION_RE.fullmatch(identity) is not None
    )


def successful_checkpoint_state(state: object) -> bool:
    """Return whether one current checkpoint completed successfully."""

    return state == "succeeded"


@dataclass(frozen=True)
class ReproductionRuntime:
    """Immutable concurrency and per-command runtime controls for one run."""

    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class _EvidenceOnlySelection:
    """One closed auxiliary observation retained with its consuming definitions."""

    entry: str
    record_id: str
    resource: str
    kind: str
    selection: Mapping[str, object]
    comparisons: tuple[str, ...]


@dataclass(frozen=True)
class ReproductionPlan:
    """One complete write-free reproduction plan projection."""

    summary: str
    target: Mapping[str, object]
    include_all: bool
    admission: Mapping[str, object]
    commands: tuple[Mapping[str, object], ...]
    comparison_context: Mapping[str, object]
    cases: tuple[Mapping[str, object], ...]
    executions: tuple[Mapping[str, object], ...]
    boundaries: tuple[Mapping[str, object], ...]
    failures: tuple[Mapping[str, object], ...]
    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS

    def as_dict(self) -> dict[str, object]:
        """Return the closed accepted-plan/9 field set."""

        return {
            "boundaries": [dict(value) for value in self.boundaries],
            "cases": [dict(value) for value in self.cases],
            "commands": [dict(value) for value in self.commands],
            "comparison_context": dict(self.comparison_context),
            "executions": [dict(value) for value in self.executions],
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "failures": [dict(value) for value in self.failures],
            "include_all": self.include_all,
            "jobs": self.jobs,
            "schema": PLAN_SCHEMA,
            "admission": dict(self.admission),
            "summary": self.summary,
            "target": dict(self.target),
        }

    def serialized(self) -> str:
        """Serialize canonically and enforce the fixed dry-run byte bound."""

        text = json.dumps(
            self.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("reproduction dry-run plan crossed its byte bound")
        return text

    @classmethod
    def from_json(cls, raw: bytes) -> "ReproductionPlan":
        """Load one bounded, closed accepted-plan/9 JSON document."""

        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("accepted reproduction plan crossed its byte bound")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("accepted reproduction plan is not JSON") from error
        if not isinstance(value, dict) or set(value) != _PLAN_FIELDS:
            raise ValueError("accepted reproduction plan has invalid fields")
        if value.get("schema") != PLAN_SCHEMA:
            raise ValueError("accepted reproduction plan has unsupported schema")
        summary = _string(value["summary"], "summary")
        target = _mapping(value["target"], "target")
        if not valid_reproduction_target(target):
            raise ValueError("accepted reproduction plan has invalid target")
        include_all = _bool(value["include_all"], "include_all")
        jobs = _positive_int(value["jobs"], "jobs")
        timeout = _positive_int(value["execution_timeout_seconds"], "timeout")
        if timeout > MAX_EXECUTION_TIMEOUT_SECONDS:
            raise ValueError("accepted reproduction plan timeout is out of bounds")
        admission = _mapping(value["admission"], "admission")
        commands = _mappings(value["commands"], "commands")
        context = _mapping(value["comparison_context"], "comparison_context")
        cases = _mappings(value["cases"], "cases")
        executions = _mappings(value["executions"], "executions")
        boundaries = _mappings(value["boundaries"], "boundaries")
        failures = _mappings(value["failures"], "failures")
        _validate_plan_members(commands, executions, context)
        plan = cls(
            summary,
            target,
            include_all,
            admission,
            commands,
            context,
            cases,
            executions,
            boundaries,
            failures,
            jobs,
            timeout,
        )
        _validate_nested_plan(plan)
        if plan.serialized().encode("utf-8") != raw:
            raise ValueError("accepted reproduction plan is not canonical")
        return plan


@dataclass(frozen=True)
class AcceptedInvocation:
    """One decoded accepted command and its frozen planning declarations."""

    entry: str
    execution_id: str
    execution: "PyrunExecution"
    data: DataFile | None
    command: Mapping[str, object]


@dataclass(frozen=True)
class AcceptedComparison:
    """One typed frozen comparison definition and its evidence selections."""

    entry: str
    execution_id: str
    output: str
    records: tuple[EvidenceRecord, ...]
    definition: Mapping[str, object]


_PLAN_FIELDS = frozenset(
    {
        "schema",
        "summary",
        "target",
        "include_all",
        "jobs",
        "execution_timeout_seconds",
        "admission",
        "commands",
        "comparison_context",
        "cases",
        "executions",
        "boundaries",
        "failures",
    }
)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"accepted reproduction plan {name} is invalid")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"accepted reproduction plan {name} is invalid")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"accepted reproduction plan {name} is invalid")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"accepted reproduction plan {name} is invalid")
    return value


def _mappings(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError(f"accepted reproduction plan {name} is invalid")
    return tuple(_mapping(item, name) for item in value)


def _validate_plan_members(
    commands: Sequence[Mapping[str, object]],
    executions: Sequence[Mapping[str, object]],
    context: Mapping[str, object],
) -> None:
    """Require bounded identity and reference closure before lifecycle use."""

    keys: set[tuple[str, str]] = set()
    for command in commands:
        if set(command) != {
            "entry",
            "execution_id",
            "execution_state",
            "entry_root",
            "project_root",
            "data_declaration",
            "selection",
            "auto_reproduce",
            "cwd",
            "details",
            "exclusive",
            "queued",
            "requires_reproduction",
            "prior_disposition",
            "source_digest",
        }:
            raise ValueError("accepted reproduction plan command is incomplete")
        key = (
            _string(command["entry"], "command entry"),
            _string(command["execution_id"], "command execution"),
        )
        if key in keys or not isinstance(command["execution_state"], dict):
            raise ValueError("accepted reproduction plan command is invalid")
        keys.add(key)
    _validate_execution_members(executions, keys)
    if (
        context.get("schema") != "research-log-reproduction-comparison-context/1"
        or context.get("result_schema") != REPRODUCTION_RESULT_SCHEMA
    ):
        raise ValueError("accepted reproduction comparison context is invalid")


def _validate_execution_members(
    executions: Sequence[Mapping[str, object]], keys: set[tuple[str, str]]
) -> None:
    """Validate ordered scheduler records and their accepted command closure."""

    execution_keys: set[tuple[str, str]] = set()
    orders: list[int] = []
    runnable_order: dict[str, int] = {}
    for execution in executions:
        if set(execution) != {
            "depends_on",
            "entry",
            "execution_id",
            "order",
            "outputs",
            "auto_reproduce",
            "exclusive",
            "read_paths",
            "write_paths",
            "run_path",
            "writable_paths",
        }:
            raise ValueError("accepted reproduction plan execution fields are invalid")
        key = (
            _string(execution.get("entry"), "execution entry"),
            _string(execution.get("execution_id"), "execution id"),
        )
        if key not in keys or key in execution_keys:
            raise ValueError("accepted reproduction plan execution is unbound")
        execution_keys.add(key)
        order = _positive_int(execution.get("order"), "execution order")
        orders.append(order)
        runnable_order[f"{key[0]}:{key[1]}"] = order
        dependencies = execution.get("depends_on")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise ValueError("accepted reproduction plan dependencies are invalid")
        if (
            len(dependencies) != len(set(dependencies))
            or not set(dependencies) <= set(runnable_order)
            or any(runnable_order[item] >= order for item in dependencies)
        ):
            raise ValueError("accepted reproduction plan dependency is unresolved")
    if orders != list(range(1, len(orders) + 1)):
        raise ValueError("accepted reproduction plan execution order is invalid")


def _validate_nested_plan(plan: ReproductionPlan) -> None:
    """Decode every retained command and evidence definition before lifecycle use."""

    if set(plan.admission) != {"evaluated_at", "rules_version", "batch_admission"}:
        raise ValueError("accepted reproduction plan admission is invalid")
    if not isinstance(plan.admission["evaluated_at"], str) or not isinstance(
        plan.admission["rules_version"], str
    ):
        raise ValueError("accepted reproduction plan admission is invalid")
    command_keys = {
        (str(command["entry"]), str(command["execution_id"]))
        for command in plan.commands
    }
    for entry, execution_id in sorted(command_keys):
        accepted_invocation(plan, entry, execution_id)
    _validate_plan_collections(plan)
    _validate_comparison_context(plan, command_keys)


def _validate_plan_collections(plan: ReproductionPlan) -> None:
    """Validate planner-emitted artifact, boundary, failure, and admission rows."""

    batch = plan.admission["batch_admission"]
    if not isinstance(batch, Mapping) or set(batch) != {
        "admitted",
        "excluded",
        "schema",
    }:
        raise ValueError("accepted reproduction batch admission is invalid")
    if batch.get("schema") != "research-log-reproduction-batch-admission/2":
        raise ValueError("accepted reproduction batch admission is invalid")
    _validate_cases(plan.cases)
    _validate_boundaries(plan.boundaries)
    _validate_failures(plan.failures)


def _validate_cases(cases: Sequence[Mapping[str, object]]) -> None:
    for case in cases:
        if set(case) != {"artifact", "disposition", "entry", "execution_id", "reason"}:
            raise ValueError("accepted reproduction case is invalid")
        if not all(
            isinstance(case.get(name), str)
            for name in ("artifact", "disposition", "entry")
        ):
            raise ValueError("accepted reproduction case is invalid")


def _validate_boundaries(boundaries: Sequence[Mapping[str, object]]) -> None:
    for boundary in boundaries:
        if not {"artifact", "entry", "fingerprint", "kind", "name"} <= set(boundary):
            raise ValueError("accepted reproduction boundary is invalid")
        _validate_fingerprint_row(boundary, "boundary", kind=None)


def _validate_failures(failures: Sequence[Mapping[str, object]]) -> None:
    for failure in failures:
        if set(failure) != {"artifact", "dependencies", "entry", "outcome", "reason"}:
            raise ValueError("accepted reproduction failure is invalid")
        if failure.get("outcome") != "failed" or not isinstance(
            failure.get("dependencies"), list
        ):
            raise ValueError("accepted reproduction failure is invalid")


def _validate_comparison_context(
    plan: ReproductionPlan, command_keys: set[tuple[str, str]]
) -> None:
    """Validate every retained comparison definition and auxiliary selection."""

    comparisons = plan.comparison_context.get("comparisons")
    materials = plan.comparison_context.get("materials")
    evidence_only = plan.comparison_context.get("evidence_only")
    if (
        not isinstance(comparisons, list)
        or not isinstance(materials, list)
        or not isinstance(evidence_only, list)
    ):
        raise ValueError("accepted reproduction comparison context is invalid")
    seen: set[tuple[str, str, str]] = set()
    for value in comparisons:
        if not isinstance(value, dict) or set(value) != {
            "entry",
            "execution_id",
            "output",
            "evidence_records",
            "definition_identity",
        }:
            raise ValueError("accepted reproduction comparison definition is invalid")
        comparison_entry = value.get("entry")
        comparison_execution_id = value.get("execution_id")
        comparison_output = value.get("output")
        values = (comparison_entry, comparison_execution_id, comparison_output)
        if not all(isinstance(item, str) for item in values):
            raise ValueError("accepted reproduction comparison definition is invalid")
        assert isinstance(comparison_entry, str)
        assert isinstance(comparison_execution_id, str)
        assert isinstance(comparison_output, str)
        key = (comparison_entry, comparison_execution_id, comparison_output)
        if key in seen:
            raise ValueError("accepted reproduction comparison definition is invalid")
        if (key[0], key[1]) not in command_keys:
            raise ValueError("accepted reproduction comparison is unbound")
        seen.add(key)
        accepted_typed_comparison(plan, key[0], key[1], key[2])
    _validate_materials(materials)
    _validate_evidence_only(plan, evidence_only, comparisons, command_keys)


def _validate_materials(materials: Sequence[object]) -> None:
    for material in materials:
        if not isinstance(material, dict):
            raise ValueError("accepted reproduction material is invalid")
        expected = {"fingerprint", "identity", "kind", "role"}
        if material.get("role") == "input":
            expected.add("selection")
        if set(material) != expected:
            raise ValueError("accepted reproduction material is invalid")
        _validate_fingerprint_row(material, "material")
        if material.get("role") == "input":
            identity = material.get("identity")
            kind = material.get("kind")
            if not isinstance(identity, str) or not isinstance(kind, str):
                raise ValueError("accepted reproduction material is invalid")
            parse_resource_identity(material.get("selection"), identity, kind=kind)


def _validate_fingerprint_row(
    value: Mapping[str, object], label: str, *, kind: str | None = "required"
) -> None:
    identity = value.get("identity", value.get("name"))
    observed_kind = value.get("kind") if kind is not None else None
    if not isinstance(identity, str) or (
        kind is not None and not isinstance(observed_kind, str)
    ):
        raise ValueError(f"accepted reproduction {label} is invalid")
    parse_fingerprint(value.get("fingerprint"), identity, kind=observed_kind)


def _validate_evidence_only(
    plan: ReproductionPlan,
    evidence_only: Sequence[object],
    comparisons: Sequence[object],
    command_keys: set[tuple[str, str]],
) -> None:
    """Require every auxiliary observation to close over its frozen evidence."""

    definitions = _comparison_definitions(comparisons)
    seen: set[tuple[str, str, str, str]] = set()
    for value in evidence_only:
        row = _parse_evidence_only_selection(value)
        key = (
            row.entry,
            row.record_id,
            row.resource,
            canonical_record_digest(row.selection),
        )
        if key in seen:
            raise ValueError(
                "accepted reproduction evidence-only selection is duplicated"
            )
        seen.add(key)
        if not any(command_entry == row.entry for command_entry, _ in command_keys):
            raise ValueError("accepted reproduction evidence-only entry is unbound")
        for identity in row.comparisons:
            comparison = definitions.get(identity)
            if comparison is None or comparison.get("entry") != row.entry:
                raise ValueError(
                    "accepted reproduction evidence-only association is unbound"
                )
            if not _comparison_consumes_resource(plan, comparison, row):
                raise ValueError(
                    "accepted reproduction evidence-only association is invalid"
                )


def _parse_evidence_only_selection(value: object) -> _EvidenceOnlySelection:
    """Parse one closed evidence-only row before resolving its associations."""

    expected = {
        "comparisons",
        "entry",
        "fingerprint",
        "kind",
        "record_id",
        "resource",
        "selection",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("accepted reproduction evidence-only context is invalid")
    entry = value.get("entry")
    record_id = value.get("record_id")
    resource = value.get("resource")
    kind = value.get("kind")
    identities = value.get("comparisons")
    if not all(isinstance(item, str) for item in (entry, record_id, resource, kind)):
        raise ValueError("accepted reproduction evidence-only context is invalid")
    assert isinstance(entry, str)
    assert isinstance(record_id, str)
    assert isinstance(resource, str)
    assert isinstance(kind, str)
    if not isinstance(identities, list) or not all(
        isinstance(item, str) for item in identities
    ):
        raise ValueError("accepted reproduction evidence-only context is invalid")
    if not Path(resource).is_absolute():
        raise ValueError("accepted reproduction evidence-only resource is invalid")
    selection = _mapping(value.get("selection"), "evidence-only selection")
    parse_resource_identity(selection, resource, kind=kind)
    parse_fingerprint(value.get("fingerprint"), resource, kind=kind)
    if not identities or len(identities) != len(set(identities)):
        raise ValueError("accepted reproduction evidence-only association is invalid")
    return _EvidenceOnlySelection(
        entry, record_id, resource, kind, selection, tuple(identities)
    )


def _comparison_definitions(
    comparisons: Sequence[object],
) -> Mapping[str, Mapping[str, object]]:
    """Index definitions by their immutable identity without accepting collisions."""

    indexed: dict[str, Mapping[str, object]] = {}
    for value in comparisons:
        if not isinstance(value, Mapping):
            raise ValueError("accepted reproduction comparison definition is invalid")
        identity = value.get("definition_identity")
        if not isinstance(identity, str) or not identity or identity in indexed:
            raise ValueError("accepted reproduction comparison definition is invalid")
        indexed[identity] = value
    return indexed


def _comparison_consumes_resource(
    plan: ReproductionPlan,
    comparison: Mapping[str, object],
    selection: _EvidenceOnlySelection,
) -> bool:
    """Return whether a retained record actually selects the auxiliary input."""

    entry = comparison.get("entry")
    execution_id = comparison.get("execution_id")
    output = comparison.get("output")
    if not all(isinstance(item, str) for item in (entry, execution_id, output)):
        return False
    typed = accepted_typed_comparison(
        plan, cast(str, entry), cast(str, execution_id), cast(str, output)
    )
    invocation = accepted_invocation(plan, cast(str, entry), cast(str, execution_id))
    if typed is None or invocation.data is None:
        return False
    for record in typed.records:
        if record.id != selection.record_id:
            continue
        for source in record.sources:
            selected = resolve_input_token(source.source, invocation.data).resource
            if (
                selected.canonical_target == selection.resource
                and selected.kind == selection.kind
                and selected.identity.as_dict() == dict(selection.selection)
            ):
                return True
    return False


def accepted_command(
    plan: ReproductionPlan, entry: str, execution_id: str
) -> Mapping[str, object]:
    """Return one uniquely accepted command record by compound identity."""

    matches = [
        command
        for command in plan.commands
        if command.get("entry") == entry and command.get("execution_id") == execution_id
    ]
    if len(matches) != 1:
        raise ValueError("accepted reproduction plan command is missing or ambiguous")
    return matches[0]


def accepted_invocation(
    plan: ReproductionPlan, entry: str, execution_id: str
) -> AcceptedInvocation:
    """Return one typed immutable invocation without consulting current files."""

    command = accepted_command(plan, entry, execution_id)
    from validation.pyrun_state import parse_pyrun_execution

    execution_raw = _mapping(command.get("execution_state"), "execution state")
    entry_root = Path(_string(command.get("entry_root"), "entry root"))
    project_root = Path(_string(command.get("project_root"), "project root"))
    if not entry_root.is_absolute() or not project_root.is_absolute():
        raise ValueError("accepted reproduction plan root is invalid")
    execution: PyrunExecution = parse_pyrun_execution(
        execution_raw,
        subject=execution_id,
        entry_root=entry_root,
        project_root=project_root,
    )
    declaration = command.get("data_declaration")
    data = (
        data_file_from_fields(
            entry_root / "data.json",
            entry_root=entry_root,
            fields=_mapping(declaration, "command data declaration"),
        )
        if declaration is not None
        else None
    )
    return AcceptedInvocation(entry, execution_id, execution, data, command)


def accepted_comparison(
    plan: ReproductionPlan, entry: str, execution_id: str, output: str
) -> Mapping[str, object] | None:
    """Return one frozen output comparison definition, if the plan has one."""

    raw = plan.comparison_context.get("comparisons", ())
    if not isinstance(raw, list):
        raise ValueError("accepted reproduction plan comparisons are invalid")
    matches = [
        item
        for item in raw
        if isinstance(item, dict)
        and item.get("entry") == entry
        and item.get("execution_id") == execution_id
        and item.get("output") == output
    ]
    if len(matches) > 1:
        raise ValueError("accepted reproduction plan comparison is ambiguous")
    if not matches:
        return None
    return matches[0]


def accepted_typed_comparison(
    plan: ReproductionPlan, entry: str, execution_id: str, output: str
) -> AcceptedComparison | None:
    """Decode frozen evidence records without reopening current evidence files."""

    definition = accepted_comparison(plan, entry, execution_id, output)
    if definition is None:
        return None
    from validation.evidence import evidence_record_from_fields

    raw_records = definition.get("evidence_records")
    if not isinstance(raw_records, list):
        raise ValueError("accepted reproduction comparison records are invalid")
    invocation = accepted_invocation(plan, entry, execution_id)
    root = (
        invocation.data.entry_root
        if invocation.data is not None
        else Path(_string(invocation.command.get("entry_root"), "entry root"))
    )
    log_root = root.parent.parent
    records = tuple(
        evidence_record_from_fields(
            subject=f"accepted:{entry}:{execution_id}:{output}:{index}",
            log_root=log_root,
            entry_root=root,
            fields=_mapping(record, "comparison evidence record"),
        )
        for index, record in enumerate(raw_records)
    )
    return AcceptedComparison(entry, execution_id, output, records, definition)


def format_reproduction_plan_summary(plan: ReproductionPlan, *, recheck: bool) -> str:
    """Return the bounded human projection of one valid dry-run plan."""

    entry_counts: dict[str, tuple[int, int]] = {}
    exclusive_count = 0
    required_claims = {"read_paths", "write_paths", "run_path", "writable_paths"}
    claims_complete = True
    for execution in plan.executions:
        entry = execution.get("entry")
        if not isinstance(entry, str):
            raise ValueError("reproduction plan execution has an invalid entry")
        count, entry_exclusive = entry_counts.get(entry, (0, 0))
        is_exclusive = execution.get("exclusive") is True
        entry_counts[entry] = (count + 1, entry_exclusive + int(is_exclusive))
        exclusive_count += int(is_exclusive)
        claims_complete = claims_complete and required_claims <= set(execution)

    target_kind = plan.target.get("kind")
    target_entry = plan.target.get("entry")
    target = str(target_kind)
    if target_entry is not None:
        target = f"{target} {target_entry}"
    selection = "Recheck" if recheck else "Incremental"
    eligibility = "all eligible executions" if plan.include_all else "automatic only"
    failure_count = len(plan.failures)
    admission = "Ready with localized failures" if failure_count else "Ready"
    execution_count = len(plan.executions)
    lines = [
        f"Reproduction preview for `{plan.summary}`",
        "",
        f"- Target: {target}",
        f"- Admission: {admission}",
        f"- Selection: {selection}; {eligibility}",
        f"- Concurrency cap: {plan.jobs}",
        f"- Per-command runtime limit: {plan.execution_timeout_seconds} seconds",
        f"- Artifact cases: {len(plan.cases)}",
        (
            f"- Runnable executions: {execution_count} "
            f"({execution_count - exclusive_count} ordinary, "
            f"{exclusive_count} exclusive)"
        ),
        f"- Local planning failures: {failure_count} artifacts",
        f"- Boundaries: {len(plan.boundaries)}",
        (
            "- Scheduling path claims: "
            + ("Complete" if claims_complete else "Incomplete")
        ),
    ]
    selected_entries = sorted(entry_counts.items())[:MAX_PLAN_SUMMARY_ENTRIES]
    if selected_entries:
        lines.extend(
            [
                "",
                "| Entry | Runnable | Exclusive |",
                "| --- | ---: | ---: |",
                *(
                    f"| `{entry}` | {counts[0]} | {counts[1]} |"
                    for entry, counts in selected_entries
                ),
            ]
        )
    omitted = len(entry_counts) - len(selected_entries)
    if omitted:
        noun = "entry" if omitted == 1 else "entries"
        lines.extend(["", f"{omitted} additional {noun} omitted."])
    return "\n".join(lines) + "\n"


def canonical_record_digest(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of one canonical JSON object."""

    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_execution_source_digest(value: Mapping[str, Any]) -> str:
    """Hash execution source without its mutable reproduction requirement."""

    selected = dict(value)
    if set(selected) <= {"requires_reproduction"}:
        raise ValueError("execution source record is incomplete")
    selected.pop("requires_reproduction", None)
    return canonical_record_digest(selected)
