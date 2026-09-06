"""Evidence-rooted, command-bounded research-log reproduction planning."""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence, cast

from research_log_data import (
    DataFile,
    Fingerprint,
    InputResource,
    load_data_file,
    observe_file_content,
    observe_fingerprint,
    parse_fingerprint,
    resolve_input_token,
)
from validation.controller import evaluate_current_record
from validation.engine import RULES_VERSION
from validation.evidence import EvidenceFile, load_evidence_file
from validation.human_projection import provenance_artifact_counts
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    CompletionState,
    MechanicalGeneratedRecord,
)
from validation.operation_state import operation_directory
from validation.pyrun_outputs import code_target_path, output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    PyrunFile,
    empty_pyrun_state,
    load_pyrun_state,
    script_target_path,
)
from validation.source_projection import research_source_projection

from .context import (
    EntryContext,
    LogContext,
    parse_entry_directory_name,
    resolve_entry,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_contract import (
    ReproductionPlan,
    canonical_record_digest,
    source_snapshot,
)

MAX_REACHABLE_EXECUTIONS = 2_048
MAX_ARTIFACT_CASES = 10_000
MAX_GRAPH_NODES = 16_384
MAX_GRAPH_EDGES = 32_768
MAX_GRAPH_DEPTH = 64
MAX_BOUNDARIES = 10_000
MAX_FAILURES = 10_000
RESULT_MAX_BYTES = 64 * 1024 * 1024
ExecutionKey = tuple[str, str]


@dataclass(frozen=True)
class _EntryState:
    context: EntryContext
    data: DataFile | None
    evidence: EvidenceFile | None
    pyrun: PyrunFile


@dataclass(frozen=True)
class _Owner:
    entry: _EntryState
    execution_id: str
    execution: PyrunExecution
    output: str
    target: str
    kind: str

    @property
    def key(self) -> ExecutionKey:
        """Return the entry-qualified identity of this physical execution."""

        return self.entry.context.id, self.execution_id


@dataclass(frozen=True)
class _Failure:
    entry: str
    artifact: str
    execution_id: str | None
    reason: str
    dependencies: tuple[str, ...] = ()


@dataclass
class _PlanningState:
    log: LogContext
    project_root: Path
    selected_entries: tuple[str, ...]
    include_slow: bool
    entries: Mapping[str, _EntryState]
    owners: Mapping[str, tuple[_Owner, ...]]
    selected: dict[ExecutionKey, _Owner] = field(default_factory=dict)
    dependencies: dict[ExecutionKey, set[ExecutionKey]] = field(
        default_factory=lambda: defaultdict(set)
    )
    cases: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    boundaries: dict[tuple[str, str, str], dict[str, object]] = field(
        default_factory=dict
    )
    failures: dict[tuple[str, str, str], dict[str, object]] = field(
        default_factory=dict
    )
    visiting: list[ExecutionKey] = field(default_factory=list)
    visited: set[ExecutionKey] = field(default_factory=set)
    cycle_members: set[ExecutionKey] = field(default_factory=set)
    blocked: set[ExecutionKey] = field(default_factory=set)
    materials: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    authority_paths: set[Path] = field(default_factory=set)


@dataclass(frozen=True)
class ReproductionStateProjection:
    """Current evidence reachability and execution timing without validation."""

    reachable: frozenset[tuple[str, str]]
    output_executions: Mapping[tuple[str, str], str]
    last_runs: Mapping[tuple[str, str], str | None]


@dataclass
class _ReachabilityProjector:
    """Bounded topology-only projection over current JSON authority."""

    log: LogContext
    project_root: Path
    entries: Mapping[str, _EntryState]
    owners: Mapping[str, tuple[_Owner, ...]]
    reachable: set[tuple[str, str]] = field(default_factory=set)
    output_executions: dict[tuple[str, str], str] = field(default_factory=dict)
    last_runs: dict[tuple[str, str], str | None] = field(default_factory=dict)
    visited: set[ExecutionKey] = field(default_factory=set)

    def execution(self, owner: _Owner) -> None:
        key = owner.key
        if key in self.visited:
            return
        self.visited.add(key)
        for output, _ in owner.execution.recipe.outputs:
            artifact_key = (owner.entry.context.id, output)
            self.reachable.add(artifact_key)
            self.output_executions[artifact_key] = owner.execution_id
        self.last_runs[key] = owner.execution.last_run_at
        if owner.entry.data is None:
            return
        for name in owner.execution.recipe.inputs:
            resource = owner.entry.data.by_name.get(name)
            if resource is not None:
                self.resource(resource, owner.entry)

    def resource(self, resource: InputResource, evidence_entry: _EntryState) -> None:
        if resource.origin:
            return
        candidates = self.owners.get(resource.canonical_target, ())
        same_entry = tuple(
            value
            for value in candidates
            if value.entry.context.id == evidence_entry.context.id
        )
        evidence_artifact = (
            same_entry[0].output
            if same_entry
            else _portable_resource_artifact(resource, self.project_root)
        )
        self.reachable.add((evidence_entry.context.id, evidence_artifact))
        if len(candidates) == 1:
            evidence_key = (evidence_entry.context.id, evidence_artifact)
            self.output_executions[evidence_key] = candidates[0].execution_id
            self.last_runs[
                (evidence_entry.context.id, candidates[0].execution_id)
            ] = candidates[0].execution.last_run_at
            self.execution(candidates[0])

    def result(self) -> ReproductionStateProjection:
        if (
            len(self.reachable) > MAX_ARTIFACT_CASES
            or len(self.visited) > MAX_REACHABLE_EXECUTIONS
        ):
            raise ActionError(
                "reproduction.results.resource_limit",
                "current reproduction projection crossed a fixed bound",
            )
        return ReproductionStateProjection(
            frozenset(self.reachable), self.output_executions, self.last_runs
        )


def plan_reproduction(
    log: LogContext, *, entry: EntryContext | None, include_slow: bool
) -> ReproductionPlan:
    """Build and recheck one deterministic write-free reproduction plan."""

    _require_existing_locks_available(log, entry)
    validation_snapshot, validation_state = _admit_validation(log)
    before_digest, before_projection = research_source_projection(log.summary)
    if before_digest != validation_snapshot["source_projection_digest"]:
        raise ActionError(
            "reproduction.validation.concurrent_change",
            "research state changed while validation admission was evaluated",
        )
    project_root = resolve_project_root(log.root)
    contexts = _entry_contexts(log)
    entries = _load_entries(log, project_root, contexts)
    selected_ids = (entry.id,) if entry is not None else tuple(entries)
    state = _PlanningState(
        log,
        project_root,
        selected_ids,
        include_slow,
        entries,
        _owner_index(entries, project_root),
    )
    for entry_id in selected_ids:
        current = entries[entry_id]
        if current.data is not None:
            state.authority_paths.add(current.data.path)
        if current.pyrun.path.is_file():
            state.authority_paths.add(current.pyrun.path)
        if current.evidence is None:
            continue
        if current.data is None:
            raise ActionError(
                "reproduction.data.missing",
                f"evidence entry has no data.json: {current.context.id}",
            )
        state.authority_paths.add(current.evidence.path)
        for record in current.evidence.records:
            for source in record.sources:
                resolved = resolve_input_token(source.source, current.data)
                _trace_resource(
                    resolved.resource, current, state, consumer=None, depth=0
                )
    _apply_cycle_and_dependency_failures(state)
    prior = _load_prior_results(log)
    ordered = _select_and_order(state, prior)
    plan = _project_plan(
        state,
        ordered,
        validation_snapshot,
        entry=entry,
    )
    _recheck_plan_sources(plan, state)
    after_digest, after_projection = research_source_projection(log.summary)
    if before_projection != after_projection or before_digest != after_digest:
        raise ActionError(
            "reproduction.source.changed",
            "research source changed while the dry-run plan was being built",
        )
    plan.serialized()
    del validation_state
    return plan


def _entry_contexts(log: LogContext) -> tuple[EntryContext, ...]:
    entries_root = log.root / "entries"
    found: list[tuple[str, str, EntryContext]] = []
    for path in entries_root.iterdir():
        identity = parse_entry_directory_name(path.name)
        if identity is None or path.is_symlink() or not path.is_dir():
            continue
        found.append(
            (identity.date, identity.id, EntryContext(log, identity.id, path.resolve()))
        )
    found.sort(key=lambda value: (value[0], int(value[1][1:])))
    if len({item[1] for item in found}) != len(found):
        raise ActionError(
            "reproduction.entry.duplicate", "duplicate stable entry identity"
        )
    return tuple(item[2] for item in found)


def project_reproduction_state(log: LogContext) -> ReproductionStateProjection:
    """Project current evidence reachability without validating or writing."""

    root = resolve_project_root(log.root)
    entries = _load_entries(log, root, _entry_contexts(log))
    owners = _owner_index(entries, root)
    projector = _ReachabilityProjector(log, root, entries, owners)

    for entry in entries.values():
        if entry.evidence is None or entry.data is None:
            continue
        for record in entry.evidence.records:
            for source in record.sources:
                resolved = resolve_input_token(source.source, entry.data)
                projector.resource(resolved.resource, entry)
    return projector.result()


def _load_entries(
    log: LogContext,
    project_root: Path,
    contexts: Sequence[EntryContext],
) -> dict[str, _EntryState]:
    result: dict[str, _EntryState] = {}
    for context in contexts:
        data_path = context.root / "data.json"
        pyrun_path = context.root / "pyrun.json"
        evidence_path = context.root / "evidence.json"
        try:
            data = (
                load_data_file(data_path, entry_root=context.root)
                if data_path.is_file() and not data_path.is_symlink()
                else None
            )
            pyrun = (
                load_pyrun_state(
                    pyrun_path,
                    entry_root=context.root,
                    project_root=project_root,
                )
                if pyrun_path.is_file() or pyrun_path.is_symlink()
                else empty_pyrun_state(context.root)
            )
            evidence = (
                load_evidence_file(
                    evidence_path, log_root=log.root, entry_root=context.root
                )
                if evidence_path.is_file() and not evidence_path.is_symlink()
                else None
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ActionError(
                str(getattr(error, "code", "reproduction.metadata.invalid")),
                str(error),
            ) from error
        result[context.id] = _EntryState(context, data, evidence, pyrun)
    return result


def _owner_index(
    entries: Mapping[str, _EntryState], project_root: Path
) -> dict[str, tuple[_Owner, ...]]:
    found: dict[str, list[_Owner]] = defaultdict(list)
    for state in entries.values():
        for identity, execution in state.pyrun.executions.items():
            for output, kind in execution.recipe.outputs:
                target = (
                    output_target_path(
                        output, entry_root=state.context.root, project_root=project_root
                    )
                    .resolve()
                    .as_posix()
                )
                found[target].append(
                    _Owner(state, identity, execution, output, target, kind)
                )
    return {
        target: tuple(
            sorted(owners, key=lambda item: (item.entry.context.id, item.execution_id))
        )
        for target, owners in found.items()
    }


def _trace_resource(
    resource: InputResource,
    owner_entry: _EntryState,
    state: _PlanningState,
    *,
    consumer: _Owner | None,
    depth: int,
) -> None:
    if depth > MAX_GRAPH_DEPTH:
        _record_failure(
            state,
            _Failure(
                owner_entry.context.id,
                _artifact(resource, owner_entry, state),
                None,
                "resource_limit",
            ),
        )
        return
    artifact = _artifact(resource, owner_entry, state)
    if resource.origin:
        _boundary(state, "origin", owner_entry, resource, artifact)
        return
    candidates = state.owners.get(resource.canonical_target, ())
    in_scope = tuple(
        value
        for value in candidates
        if value.entry.context.id in state.selected_entries
    )
    if not in_scope:
        if len(state.selected_entries) == 1:
            _verified_boundary(state, "cross_entry", owner_entry, resource, artifact)
            if consumer is None:
                execution_id = (
                    candidates[0].execution_id if len(candidates) == 1 else None
                )
                state.cases[(owner_entry.context.id, artifact)] = _case(
                    owner_entry.context.id,
                    artifact,
                    execution_id,
                    "skipped",
                    "outside_entry",
                )
            return
        reason = (
            "cross_log_generated_input"
            if len(state.selected_entries) > 1
            else "missing_producer"
        )
        _record_failure(state, _Failure(owner_entry.context.id, artifact, None, reason))
        return
    if len(in_scope) != 1:
        _record_failure(
            state,
            _Failure(
                owner_entry.context.id,
                artifact,
                None,
                "multiple_producers",
                tuple(value.execution_id for value in in_scope),
            ),
        )
        return
    producer = in_scope[0]
    if producer.execution.slow and not state.include_slow:
        _verified_boundary(state, "slow", owner_entry, resource, artifact)
        if consumer is None:
            state.cases[(owner_entry.context.id, artifact)] = _case(
                owner_entry.context.id,
                artifact,
                producer.execution_id,
                "skipped",
                "slow",
            )
        return
    if consumer is not None:
        state.dependencies[consumer.key].add(producer.key)
    _trace_execution(producer, state, depth=depth)


def _trace_execution(owner: _Owner, state: _PlanningState, *, depth: int) -> None:
    key = owner.key
    identity = owner.execution_id
    state.selected.setdefault(key, owner)
    _record_execution_materials(owner, state)
    for output, _ in owner.execution.recipe.outputs:
        state.cases.setdefault(
            (owner.entry.context.id, output),
            _case(owner.entry.context.id, output, identity, "run", None),
        )
    if key in state.visiting:
        index = state.visiting.index(key)
        state.cycle_members.update(state.visiting[index:])
        return
    if key in state.visited:
        return
    state.visiting.append(key)
    for name in owner.execution.recipe.inputs:
        resource = (
            owner.entry.data.by_name.get(name) if owner.entry.data is not None else None
        )
        if resource is None:
            _record_failure(
                state,
                _Failure(
                    owner.entry.context.id,
                    owner.output,
                    identity,
                    "missing_input",
                    (name,),
                ),
            )
            state.blocked.add(key)
            continue
        state.materials[("input", resource.canonical_target)] = _material(
            resource.canonical_target,
            "input",
            resource.kind,
            resource.fingerprint,
        )
        _trace_resource(resource, owner.entry, state, consumer=owner, depth=depth + 1)
    state.visiting.pop()
    state.visited.add(key)
    _check_graph_bounds(state)


def _record_execution_materials(owner: _Owner, state: _PlanningState) -> None:
    execution = owner.execution
    script = script_target_path(
        execution.recipe.script,
        entry_root=owner.entry.context.root,
        project_root=state.project_root,
    )
    _verify_regular(script, execution.observed.script, "script")
    state.materials[("script", script.resolve().as_posix())] = _material(
        script.resolve().as_posix(), "script", "file", execution.observed.script
    )
    for name, fingerprint in execution.observed.code:
        path = code_target_path(name, entry_root=owner.entry.context.root)
        _verify_regular(path, fingerprint, "participating code")
        state.materials[("code", path.resolve().as_posix())] = _material(
            path.resolve().as_posix(), "code", "file", fingerprint
        )
    for output, kind in execution.recipe.outputs:
        fingerprint = dict(execution.observed.outputs)[output]
        target = (
            output_target_path(
                output,
                entry_root=owner.entry.context.root,
                project_root=state.project_root,
            )
            .resolve()
            .as_posix()
        )
        state.materials[("baseline", target)] = _material(
            target, "comparison_baseline", kind, fingerprint
        )


def _verified_boundary(
    state: _PlanningState,
    kind: str,
    entry: _EntryState,
    resource: InputResource,
    artifact: str,
) -> None:
    try:
        observed = observe_fingerprint(resource).fingerprint
    except (OSError, ValueError) as error:
        _record_failure(
            state,
            _Failure(
                entry.context.id,
                artifact,
                None,
                "boundary_unavailable",
                (str(error),),
            ),
        )
        return
    if observed.as_dict() != resource.fingerprint.as_dict():
        _record_failure(
            state,
            _Failure(entry.context.id, artifact, None, "boundary_changed"),
        )
        return
    _boundary(state, kind, entry, resource, artifact)


def _boundary(
    state: _PlanningState,
    kind: str,
    entry: _EntryState,
    resource: InputResource,
    artifact: str,
) -> None:
    value: dict[str, object] = {
        "artifact": artifact,
        "entry": entry.context.id,
        "fingerprint": resource.fingerprint.as_dict(),
        "kind": kind,
        "name": resource.name,
    }
    state.boundaries[(kind, entry.context.id, artifact)] = value
    state.materials[("boundary", resource.canonical_target)] = _material(
        resource.canonical_target, "boundary", resource.kind, resource.fingerprint
    )
    if len(state.boundaries) > MAX_BOUNDARIES:
        raise ActionError("reproduction.plan.resource_limit", "boundary limit exceeded")


def _record_failure(state: _PlanningState, failure: _Failure) -> None:
    state.failures[(failure.entry, failure.artifact, failure.reason)] = {
        "artifact": failure.artifact,
        "dependencies": sorted(set(failure.dependencies)),
        "entry": failure.entry,
        "outcome": "failed",
        "reason": failure.reason,
    }
    state.cases[(failure.entry, failure.artifact)] = _case(
        failure.entry,
        failure.artifact,
        failure.execution_id,
        "failed",
        failure.reason,
    )
    if len(state.failures) > MAX_FAILURES:
        raise ActionError("reproduction.plan.resource_limit", "failure limit exceeded")


def _apply_cycle_and_dependency_failures(state: _PlanningState) -> None:
    for key in sorted(state.cycle_members):
        owner = state.selected[key]
        state.blocked.add(key)
        for output, _ in owner.execution.recipe.outputs:
            _record_failure(
                state,
                _Failure(
                    owner.entry.context.id,
                    output,
                    owner.execution_id,
                    "dependency_cycle",
                    tuple(_reference(value) for value in sorted(state.cycle_members)),
                ),
            )
    changed = True
    while changed:
        changed = False
        for key, dependencies in state.dependencies.items():
            if key not in state.blocked and dependencies & state.blocked:
                state.blocked.add(key)
                owner = state.selected[key]
                for output, _ in owner.execution.recipe.outputs:
                    state.failures[
                        (owner.entry.context.id, output, "dependency_failed")
                    ] = {
                        "artifact": output,
                        "dependencies": [
                            _reference(value)
                            for value in sorted(dependencies & state.blocked)
                        ],
                        "entry": owner.entry.context.id,
                        "outcome": "skipped",
                        "reason": "dependency_failed",
                    }
                    state.cases[(owner.entry.context.id, output)] = _case(
                        owner.entry.context.id,
                        output,
                        owner.execution_id,
                        "skipped",
                        "dependency_failed",
                    )
                changed = True


def _select_and_order(
    state: _PlanningState,
    prior: Mapping[tuple[str, str], Mapping[str, object]],
) -> tuple[ExecutionKey, ...]:
    runnable = set(state.selected) - state.blocked
    needs_run = _initial_work(state, prior, runnable)
    _propagate_required_work(state, runnable, needs_run)
    _project_current_cases(state, runnable - needs_run)
    return _topological_order(state, needs_run)


def _initial_work(
    state: _PlanningState,
    prior: Mapping[tuple[str, str], Mapping[str, object]],
    runnable: set[ExecutionKey],
) -> set[ExecutionKey]:
    """Select unconfirmed, new, failed, and timestamp-stale executions."""

    needs_run: set[ExecutionKey] = set()
    for key in runnable:
        owner = state.selected[key]
        if not owner.execution.confirmed:
            needs_run.add(key)
            continue
        for output, _ in owner.execution.recipe.outputs:
            result = prior.get((owner.entry.context.id, output))
            if not _result_current(result, owner.execution):
                needs_run.add(key)
                break
    return needs_run


def _propagate_required_work(
    state: _PlanningState,
    runnable: set[ExecutionKey],
    needs_run: set[ExecutionKey],
) -> None:
    """Select every current downstream execution affected by required work."""

    changed = True
    while changed:
        changed = False
        for key in runnable - needs_run:
            if state.dependencies.get(key, set()) & needs_run:
                needs_run.add(key)
                changed = True


def _project_current_cases(state: _PlanningState, current: set[ExecutionKey]) -> None:
    """Project reachable executions that need no new work."""

    for key in current:
        owner = state.selected[key]
        for output, _ in owner.execution.recipe.outputs:
            state.cases[(owner.entry.context.id, output)] = _case(
                owner.entry.context.id,
                output,
                owner.execution_id,
                "current",
                None,
            )


def _topological_order(
    state: _PlanningState, needs_run: set[ExecutionKey]
) -> tuple[ExecutionKey, ...]:
    """Return one stable dependency order for the selected executions."""

    indegree = {
        identity: len(state.dependencies.get(identity, set()) & needs_run)
        for identity in needs_run
    }
    ready = sorted(identity for identity, degree in indegree.items() if degree == 0)
    order: list[ExecutionKey] = []
    while ready:
        key = ready.pop(0)
        order.append(key)
        for dependent in sorted(needs_run):
            if key not in state.dependencies.get(dependent, set()):
                continue
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    if len(order) != len(needs_run):
        raise ActionError(
            "reproduction.plan.internal_cycle", "cycle escaped graph classification"
        )
    return tuple(order)


def _result_current(
    result: Mapping[str, object] | None, execution: PyrunExecution
) -> bool:
    if result is None or result.get("outcome") not in {"matched", "changed"}:
        return False
    recorded = result.get("recorded_at")
    if not isinstance(recorded, str):
        return False
    if execution.last_run_at is None:
        return True
    return _timestamp(recorded) >= _timestamp(execution.last_run_at)


def _project_plan(
    state: _PlanningState,
    ordered: tuple[ExecutionKey, ...],
    validation_snapshot: Mapping[str, object],
    *,
    entry: EntryContext | None,
) -> ReproductionPlan:
    order_index = {key: number for number, key in enumerate(ordered, 1)}
    executions = []
    ordered_set = set(ordered)
    for key in ordered:
        owner = state.selected[key]
        executions.append(
            {
                "depends_on": sorted(
                    _reference(value)
                    for value in state.dependencies.get(key, set()) & ordered_set
                ),
                "entry": owner.entry.context.id,
                "execution_id": owner.execution_id,
                "order": order_index[key],
                "outputs": sorted(
                    output for output, _ in owner.execution.recipe.outputs
                ),
                "slow": owner.execution.slow,
            }
        )
    authority_files = [
        {"path": _canonical_path(path, state.project_root), "sha256": _digest(path)}
        for path in sorted(state.authority_paths, key=lambda item: item.as_posix())
    ]
    execution_snapshot = [
        {
            "digest": canonical_record_digest(owner.execution.as_dict()),
            "entry": owner.entry.context.id,
            "execution_id": identity,
        }
        for (_, identity), owner in sorted(state.selected.items())
    ]
    materials = sorted(
        state.materials.values(),
        key=lambda value: (str(value["role"]), str(value["identity"])),
    )
    snapshot = source_snapshot(
        authority_files=authority_files,
        executions=execution_snapshot,
        materials=materials,
    )
    cases = tuple(
        state.cases[key]
        for key in sorted(
            state.cases, key=lambda value: (_entry_order(value[0]), value[1])
        )
    )
    boundaries = tuple(state.boundaries[key] for key in sorted(state.boundaries))
    failures = tuple(state.failures[key] for key in sorted(state.failures))
    return ReproductionPlan(
        _canonical_path(state.log.summary, state.project_root),
        {
            "entry": entry.id if entry is not None else None,
            "kind": "entry" if entry is not None else "log",
        },
        state.include_slow,
        validation_snapshot,
        snapshot,
        cases,
        tuple(executions),
        boundaries,
        failures,
    )


def _admit_validation(
    log: LogContext,
) -> tuple[dict[str, object], MechanicalGeneratedRecord]:
    path = log.root / "validation" / "results.json"
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.validation.missing", f"missing validation result: {path}"
        )
    try:
        raw = path.read_bytes()
        if len(raw) > RESULT_MAX_BYTES:
            raise ValueError("validation result crossed its byte bound")
        record = MechanicalGeneratedRecord.from_json(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.validation.invalid", str(error)) from error
    if record.completion is CompletionState.INCOMPLETE:
        raise ActionError(
            "reproduction.validation.incomplete", "validation result is incomplete"
        )
    if (
        Path(record.summary).resolve() != log.summary.resolve()
        or record.rules_version != RULES_VERSION
    ):
        raise ActionError(
            "reproduction.validation.stale",
            "validation identity or rules version is stale",
        )
    if any(
        check.status in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}
        for check in record.checks
        if check.scope in {CheckScope.CONFORMANCE, CheckScope.EVIDENCE}
    ):
        raise ActionError(
            "reproduction.validation.blocked", "Structure or Evidence validation failed"
        )
    if provenance_artifact_counts(record)[CheckStatus.FAIL.value]:
        raise ActionError(
            "reproduction.validation.blocked", "Provenance validation failed"
        )
    try:
        current = evaluate_current_record(log.summary, result_date=record.result_date)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.validation.stale", str(error)) from error
    if current.canonical_json() != record.canonical_json():
        raise ActionError(
            "reproduction.validation.stale",
            "published validation result does not describe current research source",
        )
    source_digest, _ = research_source_projection(log.summary)
    return (
        {
            "result_date": record.result_date,
            "result_digest": hashlib.sha256(raw).hexdigest(),
            "result_path": path.relative_to(log.root).as_posix(),
            "rules_version": record.rules_version,
            "source_projection_digest": source_digest,
        },
        record,
    )


def _load_prior_results(
    log: LogContext,
) -> dict[tuple[str, str], Mapping[str, object]]:
    from .reproduction_results import (
        ReproductionResultError,
        load_reproduction_results,
    )

    path = log.root / "reproduction" / "results.json"
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        value = load_reproduction_results(path)
    except ReproductionResultError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
    result: dict[tuple[str, str], Mapping[str, object]] = {}
    for item in value.artifacts:
        result[(item.entry, item.artifact)] = item.as_dict()
    return result


def _require_existing_locks_available(
    log: LogContext, entry: EntryContext | None
) -> None:
    directory = operation_directory(log.root)
    checks = [
        (directory / "log.lock", fcntl.LOCK_SH if entry is not None else fcntl.LOCK_EX)
    ]
    if entry is not None:
        checks.append((directory / f"entry-{entry.id}.lock", fcntl.LOCK_EX))
    handles: list[int] = []
    try:
        for path, mode in checks:
            if not path.exists():
                continue
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            handles.append(descriptor)
            try:
                fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ActionError(
                    "reproduction.operation.active",
                    f"active operation overlaps target: {path.name}",
                ) from error
    finally:
        for descriptor in reversed(handles):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _recheck_plan_sources(plan: ReproductionPlan, state: _PlanningState) -> None:
    """Recheck every accepted byte and material immediately before return."""

    verify_reproduction_snapshot(state.log, plan)


def verify_reproduction_snapshot(log: LogContext, plan: ReproductionPlan) -> None:
    """Require exact agreement with every source accepted by one plan."""

    project_root = resolve_project_root(log.root)
    _recheck_authority_files(plan, project_root)
    _recheck_validation_result(plan, log)
    _recheck_executions(plan, log, project_root)
    _recheck_materials(plan)
    digest, _ = research_source_projection(log.summary)
    expected = plan.validation_snapshot.get("source_projection_digest")
    if not isinstance(expected, str) or digest != expected:
        raise ActionError(
            "reproduction.source.changed", "validated research source changed"
        )


def verify_reproduction_publication_snapshot(
    log: LogContext, plan: ReproductionPlan
) -> None:
    """Recheck publication inputs while allowing unrelated entry publication.

    A log run owns the whole log and therefore retains the exact admitted
    validation and source projections. Distinct entry runs may publish in
    either order; their own authority, execution, and material snapshots remain
    exact while shared generated validation state is rebased under the brief
    publication mutex.
    """

    if plan.target.get("kind") != "entry":
        verify_reproduction_snapshot(log, plan)
        return
    project_root = resolve_project_root(log.root)
    _recheck_authority_files(plan, project_root)
    _recheck_executions(plan, log, project_root)
    _recheck_materials(plan)


def _recheck_authority_files(plan: ReproductionPlan, project_root: Path) -> None:
    """Require every loaded authority file to retain its exact bytes."""

    for item in cast(
        Sequence[Mapping[str, object]], plan.source_snapshot["authority_files"]
    ):
        raw_path = item["path"]
        expected = item["sha256"]
        if not isinstance(raw_path, str) or not isinstance(expected, str):
            raise ActionError(
                "reproduction.source.invalid", "invalid authority snapshot"
            )
        path = Path(raw_path)
        if not path.is_absolute():
            path = project_root / path
        if _digest(path) != expected:
            raise ActionError(
                "reproduction.source.changed", f"authority file changed: {raw_path}"
            )


def _recheck_validation_result(plan: ReproductionPlan, log: LogContext) -> None:
    """Require the admitted validation result to retain its exact bytes."""

    result_path = plan.validation_snapshot["result_path"]
    result_digest = plan.validation_snapshot["result_digest"]
    if not isinstance(result_path, str) or not isinstance(result_digest, str):
        raise ActionError("reproduction.source.invalid", "invalid validation snapshot")
    if _digest(log.root / result_path) != result_digest:
        raise ActionError("reproduction.source.changed", "validation result changed")


def _recheck_executions(
    plan: ReproductionPlan, log: LogContext, project_root: Path
) -> None:
    loaded: dict[str, PyrunFile] = {}
    values = cast(
        Sequence[Mapping[str, object]], plan.source_snapshot["executions"]
    )
    for value in values:
        entry_id = value.get("entry")
        identity = value.get("execution_id")
        expected = value.get("digest")
        if not all(isinstance(item, str) for item in (entry_id, identity, expected)):
            raise ActionError(
                "reproduction.source.invalid", "invalid execution snapshot"
            )
        assert isinstance(entry_id, str)
        assert isinstance(identity, str)
        assert isinstance(expected, str)
        if entry_id not in loaded:
            entry = resolve_entry(log, entry_id)
            loaded[entry_id] = load_pyrun_state(
                entry.root / "pyrun.json",
                entry_root=entry.root,
                project_root=project_root,
            )
        execution = loaded[entry_id].executions.get(identity)
        if (
            execution is None
            or canonical_record_digest(execution.as_dict()) != expected
        ):
            raise ActionError(
                "reproduction.source.changed",
                f"execution recipe changed: {entry_id}:{identity}",
            )


def _recheck_materials(plan: ReproductionPlan) -> None:
    """Require every snapshotted material to retain its closed fingerprint."""

    observed: set[tuple[str, str]] = set()
    for item in cast(Sequence[Mapping[str, object]], plan.source_snapshot["materials"]):
        identity = item.get("identity")
        kind = item.get("kind")
        raw_fingerprint = item.get("fingerprint")
        if not isinstance(identity, str) or not isinstance(kind, str):
            raise ActionError(
                "reproduction.source.invalid", "invalid material snapshot"
            )
        fingerprint = parse_fingerprint(
            raw_fingerprint,
            identity,
            kind=kind,
        )
        key = (identity, fingerprint.content_identity)
        if key in observed:
            continue
        observed.add(key)
        resource = InputResource(
            "snapshot-material",
            kind,
            identity,
            fingerprint,
            True,
            identity,
        )
        try:
            current = observe_fingerprint(resource).fingerprint
        except (OSError, ValueError) as error:
            raise ActionError(
                "reproduction.source.changed",
                f"material became unavailable: {identity}: {error}",
            ) from error
        if current.as_dict() != fingerprint.as_dict():
            raise ActionError(
                "reproduction.source.changed", f"material changed: {identity}"
            )


def _artifact(
    resource: InputResource, entry: _EntryState, state: _PlanningState
) -> str:
    candidates = state.owners.get(resource.canonical_target, ())
    same = [value for value in candidates if value.entry.context.id == entry.context.id]
    if same:
        return same[0].output
    return _portable_resource_artifact(resource, state.project_root)


def _portable_resource_artifact(resource: InputResource, project_root: Path) -> str:
    target = Path(resource.canonical_target).resolve()
    try:
        relative = target.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        raise ActionError(
            "reproduction.artifact.outside_project",
            f"generated artifact is outside the project: {target}",
        ) from None
    return f"<project>/{relative}"


def _case(
    entry: str,
    artifact: str,
    execution_id: str | None,
    disposition: str,
    reason: str | None,
) -> dict[str, object]:
    return {
        "artifact": artifact,
        "disposition": disposition,
        "entry": entry,
        "execution_id": execution_id,
        "reason": reason,
    }


def _material(
    identity: str, role: str, kind: str, fingerprint: Fingerprint
) -> dict[str, object]:
    return {
        "fingerprint": fingerprint.as_dict(),
        "identity": identity,
        "kind": kind,
        "role": role,
    }


def _verify_regular(path: Path, expected: Fingerprint, label: str) -> None:
    try:
        digest, _ = observe_file_content(path)
    except (OSError, ValueError) as error:
        raise ActionError(
            "reproduction.material.unavailable", f"{label} unavailable: {path}: {error}"
        ) from error
    if expected.algorithm != "sha256" or expected.digest != digest:
        raise ActionError("reproduction.material.changed", f"{label} changed: {path}")


def _check_graph_bounds(state: _PlanningState) -> None:
    edges = sum(len(value) for value in state.dependencies.values())
    nodes = len(state.selected) + len(state.cases) + len(state.boundaries)
    if (
        len(state.selected) > MAX_REACHABLE_EXECUTIONS
        or len(state.cases) > MAX_ARTIFACT_CASES
        or nodes > MAX_GRAPH_NODES
        or edges > MAX_GRAPH_EDGES
    ):
        raise ActionError(
            "reproduction.plan.resource_limit",
            "reproduction graph crossed a fixed bound",
        )


def _canonical_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _entry_order(value: str) -> int:
    return int(value[1:]) if value.startswith("e") and value[1:].isdigit() else 2**31


def _reference(key: ExecutionKey) -> str:
    """Return an unambiguous run-local dependency reference."""

    return f"{key[0]}:{key[1]}"
