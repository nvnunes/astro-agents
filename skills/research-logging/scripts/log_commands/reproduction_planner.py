"""Evidence-rooted, command-bounded research-log reproduction planning."""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast

from research_log_data import (
    DataFile,
    Fingerprint,
    InputResource,
    load_data_file,
    observe_fingerprint,
    parse_fingerprint,
    resolve_input_token,
)
from validation.batch_projection import PROJECTION_SCHEMA
from validation.controller import evaluate_current_record
from validation.engine import RULES_VERSION
from validation.evidence import EvidenceFile, load_evidence_file
from validation.evidence_comparison import evidence_comparison_identity
from validation.mechanical_results import CompletionState, MechanicalGeneratedRecord
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
    parse_entry_document_name,
    resolve_entry,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_contract import (
    LEGACY_SOURCE_SNAPSHOT_SCHEMA,
    PRELOCAL_SOURCE_SNAPSHOT_SCHEMA,
    SOURCE_SNAPSHOT_SCHEMA,
    ReproductionPlan,
    canonical_execution_source_digest,
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
SelectionPolicy = Literal["incremental", "recheck"]
INCREMENTAL_SELECTION: SelectionPolicy = "incremental"
RECHECK_SELECTION: SelectionPolicy = "recheck"


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


@dataclass(frozen=True)
class _BoundaryRequest:
    kind: str
    entry: _EntryState
    resource: InputResource
    artifact: str
    consumer: _Owner | None


@dataclass
class _PlanningState:
    log: LogContext
    project_root: Path
    selected_entries: tuple[str, ...]
    entry_target: bool
    include_all: bool
    jobs: int
    selection_policy: SelectionPolicy
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
    material_owners: dict[tuple[str, str], set[ExecutionKey | None]] = field(
        default_factory=lambda: defaultdict(set)
    )
    authority_paths: set[Path] = field(default_factory=set)
    admitted_batches: set[tuple[str, str]] = field(default_factory=set)
    excluded_batches: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class ReproductionStateProjection:
    """Current evidence reachability and execution timing without validation."""

    reachable: frozenset[tuple[str, str]]
    output_executions: Mapping[tuple[str, str], str]
    last_runs: Mapping[tuple[str, str], str | None]
    comparison_definitions: Mapping[tuple[str, str], str | None] = field(
        default_factory=dict
    )


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
    comparison_definitions: dict[tuple[str, str], str | None] = field(
        default_factory=dict
    )
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
            self.comparison_definitions[artifact_key] = _comparison_identity(
                owner, output, self.project_root
            )
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
        candidates = _resource_owners(self.owners, resource.canonical_target)
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
            self.last_runs[(evidence_entry.context.id, candidates[0].execution_id)] = (
                candidates[0].execution.last_run_at
            )
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
            frozenset(self.reachable),
            self.output_executions,
            self.last_runs,
            self.comparison_definitions,
        )


def plan_reproduction(
    log: LogContext,
    *,
    entry: EntryContext | None,
    include_all: bool,
    jobs: int = 1,
    selection_policy: SelectionPolicy = INCREMENTAL_SELECTION,
) -> ReproductionPlan:
    """Build one deterministic plan under the requested work-selection policy."""

    _require_selection_policy(selection_policy)
    if isinstance(jobs, bool) or not isinstance(jobs, int) or jobs <= 0:
        raise ActionError("reproduction.jobs.invalid", "--jobs must be positive")
    _require_existing_locks_available(log, entry)
    admitted = _admit_validation(log)
    validation_snapshot, validation_state = admitted[:2]
    batch_projection = admitted[2] if len(admitted) == 3 else None
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
        entry is not None,
        include_all,
        jobs,
        selection_policy,
        entries,
        _owner_index(entries, project_root),
    )
    _trace_selected_evidence(selected_ids, entries, state)
    if batch_projection is not None:
        _apply_validation_admission(state, batch_projection)
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


def _trace_selected_evidence(
    selected_ids: Sequence[str],
    entries: Mapping[str, _EntryState],
    state: _PlanningState,
) -> None:
    """Load the selected evidence roots into one planning state."""

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


def _require_selection_policy(selection_policy: SelectionPolicy) -> None:
    if selection_policy not in {INCREMENTAL_SELECTION, RECHECK_SELECTION}:
        raise ActionError(
            "reproduction.selection.invalid",
            f"unsupported reproduction selection policy: {selection_policy}",
        )


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


def _resource_owners(
    owners: Mapping[str, tuple[_Owner, ...]], target: str
) -> tuple[_Owner, ...]:
    """Return exact and containing-directory owners for one material path."""

    found = list(owners.get(target, ()))
    material = Path(target)
    for root, candidates in owners.items():
        if root == target:
            continue
        try:
            relative = material.relative_to(Path(root))
        except ValueError:
            continue
        if not relative.parts:
            continue
        found.extend(owner for owner in candidates if owner.kind == "directory")
    return tuple(
        sorted(
            found,
            key=lambda owner: (
                owner.entry.context.id,
                owner.execution_id,
                owner.output,
            ),
        )
    )


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
        _verified_boundary(
            state,
            _BoundaryRequest("origin", owner_entry, resource, artifact, consumer),
        )
        return
    candidates = _resource_owners(state.owners, resource.canonical_target)
    in_scope = tuple(
        value
        for value in candidates
        if value.entry.context.id in state.selected_entries
    )
    if not in_scope:
        if state.entry_target:
            _verified_boundary(
                state,
                _BoundaryRequest(
                    "cross_entry", owner_entry, resource, artifact, consumer
                ),
            )
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
        _record_failure(
            state,
            _Failure(
                owner_entry.context.id,
                artifact,
                None,
                "cross_log_generated_input",
            ),
        )
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
    if not producer.execution.auto_reproduce and not state.include_all:
        _verified_boundary(
            state,
            _BoundaryRequest(
                "non_automatic", owner_entry, resource, artifact, consumer
            ),
        )
        if consumer is None:
            state.cases[(owner_entry.context.id, artifact)] = _case(
                owner_entry.context.id,
                artifact,
                producer.execution_id,
                "skipped",
                "non_automatic",
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
        _retain_material(
            state,
            ("input", resource.canonical_target),
            _material(
                resource.canonical_target,
                "input",
                resource.kind,
                resource.fingerprint,
            ),
            owner=key,
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
    failures: list[tuple[str, str]] = []
    script_identity = script.resolve().as_posix()
    failure = _material_failure(
        script, "file", execution.observed.script, "script"
    )
    if failure is None:
        _retain_material(
            state,
            ("script", script_identity),
            _material(script_identity, "script", "file", execution.observed.script),
            owner=owner.key,
        )
    else:
        failures.append(failure)
    for name, fingerprint in execution.observed.code:
        path = code_target_path(name, entry_root=owner.entry.context.root)
        identity = path.resolve().as_posix()
        failure = _material_failure(path, "file", fingerprint, "participating_code")
        if failure is None:
            _retain_material(
                state,
                ("code", identity),
                _material(identity, "code", "file", fingerprint),
                owner=owner.key,
            )
        else:
            failures.append(failure)
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
        failure = _material_failure(
            Path(target), kind, fingerprint, "comparison_baseline"
        )
        if failure is None:
            _retain_material(
                state,
                ("baseline", target),
                _material(target, "comparison_baseline", kind, fingerprint),
                owner=owner.key,
            )
        else:
            failures.append(failure)
    if failures:
        state.blocked.add(owner.key)
        reason = sorted(failures)[0][0]
        details = tuple(sorted(detail for _reason, detail in failures))
        for output, _kind in execution.recipe.outputs:
            _record_failure(
                state,
                _Failure(
                    owner.entry.context.id,
                    output,
                    owner.execution_id,
                    reason,
                    details,
                ),
            )


def _comparison_identity(owner: _Owner, output: str, project_root: Path) -> str | None:
    """Return one output's evidence-comparison definition identity, if any."""

    data = owner.entry.data
    if data is None:
        return None
    target = (
        output_target_path(
            output,
            entry_root=owner.entry.context.root,
            project_root=project_root,
        )
        .resolve()
        .as_posix()
    )
    resource = next(
        (item for item in data.inputs if item.canonical_target == target), None
    )
    if resource is None:
        return None
    return evidence_comparison_identity(
        resource,
        data=data,
        evidence=owner.entry.evidence,
    )


def _verified_boundary(
    state: _PlanningState,
    request: _BoundaryRequest,
) -> None:
    try:
        observed = observe_fingerprint(request.resource).fingerprint
    except (OSError, ValueError) as error:
        _record_boundary_failure(
            state,
            request,
            (
                "direct_input_unavailable"
                if request.consumer is not None
                else "boundary_unavailable"
            ),
            (request.resource.canonical_target, str(error)),
        )
        return
    if observed.as_dict() != request.resource.fingerprint.as_dict():
        _record_boundary_failure(
            state,
            request,
            (
                "direct_input_changed"
                if request.consumer is not None
                else "boundary_changed"
            ),
            (request.resource.canonical_target,),
        )
        return
    _boundary(state, request)


def _record_boundary_failure(
    state: _PlanningState,
    request: _BoundaryRequest,
    reason: str,
    details: tuple[str, ...],
) -> None:
    consumer = request.consumer
    if consumer is None:
        _record_failure(
            state,
            _Failure(
                request.entry.context.id,
                request.artifact,
                None,
                reason,
                details,
            ),
        )
        return
    state.blocked.add(consumer.key)
    for output, _kind in consumer.execution.recipe.outputs:
        _record_failure(
            state,
            _Failure(
                consumer.entry.context.id,
                output,
                consumer.execution_id,
                reason,
                details,
            ),
        )


def _boundary(
    state: _PlanningState,
    request: _BoundaryRequest,
) -> None:
    entry = request.entry
    resource = request.resource
    artifact = request.artifact
    value: dict[str, object] = {
        "artifact": artifact,
        "entry": entry.context.id,
        "fingerprint": resource.fingerprint.as_dict(),
        "kind": request.kind,
        "name": resource.name,
    }
    state.boundaries[(request.kind, entry.context.id, artifact)] = value
    _retain_material(
        state,
        ("boundary", resource.canonical_target),
        _material(
            resource.canonical_target, "boundary", resource.kind, resource.fingerprint
        ),
        owner=request.consumer.key if request.consumer is not None else None,
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


def _apply_validation_admission(
    state: _PlanningState, projection: Mapping[str, object]
) -> None:
    """Exclude only executions owned by validation-blocked command batches."""

    _validate_projected_admission(projection)
    blocked = _blocked_validation_batches(projection)
    entry_blockers = _entry_validation_blockers(projection)
    _require_resolved_validation_blockers(projection)
    chains = _mapping_items(projection.get("chains"))
    for key, owner in sorted(state.selected.items()):
        entry_groups = entry_blockers.get(owner.entry.context.id)
        if entry_groups:
            _exclude_entry_execution(state, key, owner, entry_groups)
            continue
        _apply_execution_admission(state, key, owner, chains, blocked)


def _validate_projected_admission(projection: Mapping[str, object]) -> None:
    for group in _mapping_items(projection.get("chains")):
        entry = _projected_physical_entry(group.get("entry"))
        chain_id = str(group.get("chain_id"))
        for finding in _mapping_items(group.get("findings")):
            effect = _finding_effect(finding)
            if effect not in {"none", "chain"} or (
                effect == "chain"
                and not _finding_affects_chain(finding, entry, chain_id)
            ):
                raise ActionError(
                    "reproduction.validation.scope_unresolved",
                    "validation chain has inconsistent admission ownership",
                )
    for group in _mapping_items(projection.get("unresolved")):
        for finding in _mapping_items(group.get("findings")):
            effect = _finding_effect(finding)
            entries = _string_items(finding.get("affected_entries"))
            chains = _string_items(finding.get("affected_chains"))
            if effect == "chain" or chains or (
                effect == "entry" and len(entries) != 1
            ) or (effect in {"none", "log"} and entries):
                raise ActionError(
                    "reproduction.validation.scope_unresolved",
                    "validation finding has inconsistent admission ownership",
                )


def _blocked_validation_batches(
    projection: Mapping[str, object],
) -> dict[tuple[str, str], tuple[str, ...]]:
    blocked: dict[tuple[str, str], tuple[str, ...]] = {}
    for group in _mapping_items(projection.get("chains")):
        entry = _projected_physical_entry(group.get("entry"))
        finding_ids = tuple(
            sorted(
                str(finding["identity"])
                for finding in _mapping_items(group.get("findings"))
                if _finding_effect(finding) == "chain"
                and _finding_affects_chain(finding, entry, str(group["chain_id"]))
            )
        )
        if finding_ids:
            blocked[(entry, str(group["chain_id"]))] = finding_ids
    return blocked


def _require_resolved_validation_blockers(
    projection: Mapping[str, object],
) -> None:
    for group in _mapping_items(projection.get("unresolved")):
        if any(
            _finding_effect(finding) == "log"
            for finding in _mapping_items(group.get("findings"))
        ):
            raise ActionError(
                "reproduction.validation.scope_unresolved",
                "a blocking validation finding has no safe batch scope",
            )


def _entry_validation_blockers(
    projection: Mapping[str, object],
) -> dict[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    result: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for group in _mapping_items(projection.get("unresolved")):
        by_entry: dict[str, list[str]] = {}
        for finding in _mapping_items(group.get("findings")):
            if _finding_effect(finding) != "entry":
                continue
            for entry in _string_items(finding.get("affected_entries")):
                by_entry.setdefault(_projected_physical_entry(entry), []).append(
                    str(finding["identity"])
                )
        for entry, identities in sorted(by_entry.items()):
            result.setdefault(entry, []).append(
                (str(group["chain_id"]), tuple(sorted(set(identities))))
            )
    return {entry: tuple(groups) for entry, groups in sorted(result.items())}


def _exclude_entry_execution(
    state: _PlanningState,
    key: ExecutionKey,
    owner: _Owner,
    blockers: Sequence[tuple[str, tuple[str, ...]]],
) -> None:
    finding_ids = tuple(
        sorted({finding for _chain, findings in blockers for finding in findings})
    )
    state.blocked.add(key)
    for chain_id, findings in blockers:
        state.excluded_batches[(owner.entry.context.id, chain_id)] = findings
    for output, _kind in owner.execution.recipe.outputs:
        _record_failure(
            state,
            _Failure(
                owner.entry.context.id,
                output,
                owner.execution_id,
                "validation_blocked",
                finding_ids,
            ),
        )


def _apply_execution_admission(
    state: _PlanningState,
    key: ExecutionKey,
    owner: _Owner,
    chains: Sequence[Mapping[str, object]],
    blocked: Mapping[tuple[str, str], tuple[str, ...]],
) -> None:
    targets = {
        output_target_path(
            output,
            entry_root=owner.entry.context.root,
            project_root=state.project_root,
        )
        .resolve()
        .as_posix()
        for output, _kind in owner.execution.recipe.outputs
    }
    matches = [
        group
        for group in chains
        if _projected_physical_entry(group.get("entry")) == owner.entry.context.id
        and any(
            targets <= _projected_command_outputs(command)
            for command in _mapping_items(group.get("commands"))
        )
    ]
    if len(matches) != 1:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            f"execution has {len(matches)} projected batch matches: {key[1]}",
        )
    group = matches[0]
    batch_key = (owner.entry.context.id, str(group["chain_id"]))
    blockers = blocked.get(batch_key)
    if blockers is None:
        state.admitted_batches.add(batch_key)
        return
    state.blocked.add(key)
    state.excluded_batches[batch_key] = blockers
    for output, _kind in owner.execution.recipe.outputs:
        _record_failure(
            state,
            _Failure(
                owner.entry.context.id,
                output,
                owner.execution_id,
                "validation_blocked",
                blockers,
            ),
        )


def _projected_physical_entry(value: object) -> str:
    """Resolve one projected entry-document ID to its physical entry owner."""

    if not isinstance(value, str):
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            "projected batch has no valid entry scope",
        )
    identity = parse_entry_document_name(f"{value}.md")
    if identity is None:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            f"projected batch has invalid entry scope: {value}",
        )
    return identity.id


def _projected_command_outputs(command: Mapping[str, object]) -> set[str]:
    """Return only material directly produced by one projected command."""

    outputs = {
        str(relationship["path"])
        for relationship in _mapping_items(command.get("outputs"))
        if isinstance(relationship.get("path"), str)
    }
    for collection in _mapping_items(command.get("collections")):
        if collection.get("direction") != "output":
            continue
        root = collection.get("root")
        if isinstance(root, str):
            outputs.add(root)
        outputs.update(
            str(member)
            for member in _sequence_items(collection.get("members"))
            if isinstance(member, str)
        )
    return outputs


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, Mapping))


def _finding_effect(finding: Mapping[str, object]) -> str:
    effect = finding.get("admission_effect")
    if effect not in {"none", "chain", "entry", "log"}:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            "validation finding has no supported admission effect",
        )
    return str(effect)


def _finding_affects_chain(
    finding: Mapping[str, object], entry: str, chain_id: str
) -> bool:
    entries = tuple(_string_items(finding.get("affected_entries")))
    chains = tuple(_string_items(finding.get("affected_chains")))
    return entries == (entry,) and chains == (chain_id,)


def _string_items(value: object) -> tuple[str, ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, str))


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
    """Select runnable executions under the active work-selection policy."""

    if state.selection_policy == RECHECK_SELECTION:
        return set(runnable)

    needs_run: set[ExecutionKey] = set()
    for key in runnable:
        owner = state.selected[key]
        if not owner.execution.confirmed:
            needs_run.add(key)
            continue
        for output, _ in owner.execution.recipe.outputs:
            result = prior.get((owner.entry.context.id, output))
            if not _result_current(result, owner, output, state.project_root):
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
    result: Mapping[str, object] | None,
    owner: _Owner,
    output: str,
    project_root: Path,
) -> bool:
    if result is None or result.get("outcome") not in {"matched", "changed"}:
        return False
    recorded = result.get("recorded_at")
    if not isinstance(recorded, str):
        return False
    if owner.execution.last_run_at is not None and _timestamp(recorded) < _timestamp(
        owner.execution.last_run_at
    ):
        return False
    comparison = result.get("comparison")
    recorded_definition = (
        comparison.get("evidence_definition")
        if isinstance(comparison, Mapping)
        else None
    )
    return recorded_definition == _comparison_identity(owner, output, project_root)


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
                "auto_reproduce": owner.execution.auto_reproduce,
                "exclusive": owner.execution.exclusive,
                **_execution_claims(state, owner),
            }
        )
    authority_files = [
        {"path": _canonical_path(path, state.project_root), "sha256": _digest(path)}
        for path in sorted(state.authority_paths, key=lambda item: item.as_posix())
        if path.name != "pyrun.json"
    ]
    runnable = set(state.selected) - state.blocked
    execution_snapshot = [
        {
            "digest": canonical_execution_source_digest(
                owner.execution.as_dict()
            ),
            "entry": owner.entry.context.id,
            "execution_id": identity,
        }
        for (_, identity), owner in sorted(state.selected.items())
        if owner.key in runnable
    ]
    materials = sorted(
        (
            value
            for key, value in state.materials.items()
            if None in state.material_owners[key]
            or bool(state.material_owners[key] & runnable)
        ),
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
    accepted_validation = dict(validation_snapshot)
    accepted_validation["batch_admission"] = {
        "admitted": [
            {"chain_id": chain, "entry": entry}
            for entry, chain in sorted(state.admitted_batches)
        ],
        "excluded": [
            {
                "blocking_findings": list(state.excluded_batches[(entry, chain)]),
                "chain_id": chain,
                "entry": entry,
            }
            for entry, chain in sorted(state.excluded_batches)
        ],
        "schema": "research-log-reproduction-batch-admission/2",
    }
    return ReproductionPlan(
        _canonical_path(state.log.summary, state.project_root),
        {
            "entry": entry.id if entry is not None else None,
            "kind": "entry" if entry is not None else "log",
        },
        state.include_all,
        accepted_validation,
        snapshot,
        cases,
        tuple(executions),
        boundaries,
        failures,
        state.jobs,
    )


def _execution_claims(state: _PlanningState, owner: _Owner) -> dict[str, object]:
    """Project immutable portable scheduling claims for one execution."""

    entry_root = owner.entry.context.root
    identity_tail = owner.execution_id.rsplit(":", 1)[-1]
    run_path = f"<run>/executions/{owner.entry.context.id}/{identity_tail}"
    read_paths: set[str] = set()
    if owner.entry.data is not None:
        for name in owner.execution.recipe.inputs:
            resource = owner.entry.data.by_name.get(name)
            if resource is not None:
                read_paths.add(
                    _claim_source_path(
                        Path(resource.canonical_target), state.project_root
                    )
                )
    write_paths = {
        _claim_run_path(
            output_target_path(
                output,
                entry_root=entry_root,
                project_root=state.project_root,
                authored=False,
            ),
            state.project_root,
        )
        for output, _ in owner.execution.recipe.outputs
    }
    return {
        "read_paths": sorted(read_paths),
        "write_paths": sorted(write_paths),
        "run_path": run_path,
        "writable_paths": sorted(
            {
                *write_paths,
                f"<run>/runtime/{owner.entry.context.id}/{identity_tail}",
                f"<run>/diagnostics/{owner.entry.context.id}/{identity_tail}",
            }
        ),
    }


def _claim_source_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return f"<project>/{resolved.relative_to(project_root).as_posix()}"
    except ValueError:
        return resolved.as_posix()


def _claim_run_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project_root).as_posix()
    except ValueError as error:
        raise ActionError(
            "reproduction.claim.external_write",
            f"generated output escapes the project: {resolved}",
        ) from error
    return f"<run>/workspace/{relative}"


def _admit_validation(
    log: LogContext,
) -> tuple[dict[str, object], MechanicalGeneratedRecord, Mapping[str, object]]:
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
    try:
        current = evaluate_current_record(log.summary, result_date=record.result_date)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.validation.stale", str(error)) from error
    if current.canonical_json() != record.canonical_json():
        raise ActionError(
            "reproduction.validation.stale",
            "published validation result does not describe current research source",
        )
    from .findings import load_batch_projection

    projection = load_batch_projection(log, record=record)
    if projection.get("schema") != PROJECTION_SCHEMA:
        raise ActionError(
            "reproduction.validation.publication_invalid",
            "published validation is unsupported",
        )
    projection_path = log.root / "validation" / "batches.json"
    source_digest, _ = research_source_projection(log.summary)
    return (
        {
            "projection_digest": _digest(projection_path),
            "validation_id": projection["validation_id"],
            "projection_path": projection_path.relative_to(log.root).as_posix(),
            "result_date": record.result_date,
            "result_digest": hashlib.sha256(raw).hexdigest(),
            "result_path": path.relative_to(log.root).as_posix(),
            "rules_version": record.rules_version,
            "source_projection_digest": source_digest,
        },
        record,
        projection,
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


def verify_reproduction_runtime_snapshot(
    log: LogContext, plan: ReproductionPlan
) -> None:
    """Verify immutable run sources while allowing owned confirmation writes."""

    if plan.source_snapshot.get("schema") in {
        LEGACY_SOURCE_SNAPSHOT_SCHEMA,
        PRELOCAL_SOURCE_SNAPSHOT_SCHEMA,
    }:
        verify_reproduction_snapshot(log, plan)
        return
    if plan.source_snapshot.get("schema") != SOURCE_SNAPSHOT_SCHEMA:
        raise ActionError("reproduction.source.invalid", "unknown source snapshot")
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
    projection_path = plan.validation_snapshot.get("projection_path")
    projection_digest = plan.validation_snapshot.get("projection_digest")
    if projection_path is None and projection_digest is None:
        return
    if not isinstance(projection_path, str) or not isinstance(projection_digest, str):
        raise ActionError("reproduction.source.invalid", "invalid published validation")
    if _digest(log.root / projection_path) != projection_digest:
        raise ActionError("reproduction.source.changed", "published validation changed")


def _recheck_executions(
    plan: ReproductionPlan, log: LogContext, project_root: Path
) -> None:
    loaded: dict[str, PyrunFile] = {}
    values = cast(Sequence[Mapping[str, object]], plan.source_snapshot["executions"])
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
        encoded = (
            execution.as_dict()
            if execution is not None
            else None
        )
        digest = (
            canonical_execution_source_digest(encoded)
            if encoded is not None
            and plan.source_snapshot.get("schema")
            in {PRELOCAL_SOURCE_SNAPSHOT_SCHEMA, SOURCE_SNAPSHOT_SCHEMA}
            else canonical_record_digest(encoded)
            if encoded is not None
            else None
        )
        if execution is None or digest != expected:
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
        return resource.location
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


def _retain_material(
    state: _PlanningState,
    key: tuple[str, str],
    value: dict[str, object],
    *,
    owner: ExecutionKey | None,
) -> None:
    state.materials[key] = value
    state.material_owners[key].add(owner)


def _material_failure(
    path: Path,
    kind: str,
    expected: Fingerprint,
    role: str,
) -> tuple[str, str] | None:
    identity = path.resolve().as_posix()
    resource = InputResource(
        "planning-material",
        kind,
        identity,
        expected,
        True,
        identity,
    )
    try:
        observed = observe_fingerprint(resource).fingerprint
    except (OSError, ValueError) as error:
        reason = {
            "script": "script_unavailable",
            "participating_code": "participating_code_unavailable",
            "comparison_baseline": "baseline_unavailable",
        }[role]
        return reason, f"{role}:{identity}:{error}"
    if observed.as_dict() == expected.as_dict():
        return None
    reason = {
        "script": "script_changed",
        "participating_code": "participating_code_changed",
        "comparison_baseline": "baseline_changed",
    }[role]
    return (
        reason,
        f"{role}:{identity}:expected={expected.content_identity}:"
        f"observed={observed.content_identity}",
    )


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
