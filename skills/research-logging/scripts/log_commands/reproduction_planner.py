"""Evidence-rooted, command-bounded research-log reproduction planning."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping, Sequence, cast

from research_log_data import (
    DataFile,
    Fingerprint,
    InputResource,
    ResourceIdentity,
    load_data_file,
    observe_fingerprint,
    resolve_input_token,
)
from research_log_paths import RESULTS_STORE
from validation.engine import RULES_VERSION, EvaluationEntryMaterial, EvaluationResult
from validation.evidence import EvidenceFile, load_evidence_file
from validation.evidence_comparison import evidence_comparison_identity
from validation.mechanical_results import CompletionState
from validation.pyrun_outputs import code_target_path, output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    PyrunFile,
    empty_pyrun_state,
    load_pyrun_state,
    script_target_path,
)

from .context import (
    EntryContext,
    LogContext,
    parse_entry_directory_name,
    parse_entry_document_name,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_contract import (
    MAX_EXECUTION_TIMEOUT_SECONDS,
    ReproductionPlan,
    ReproductionRuntime,
    canonical_execution_source_digest,
    canonical_record_digest,
)

if TYPE_CHECKING:
    from validation.result_storage import ValidationAdmission

MAX_REACHABLE_EXECUTIONS = 2_048
MAX_ARTIFACT_CASES = 10_000
MAX_GRAPH_NODES = 16_384
MAX_GRAPH_EDGES = 32_768
MAX_GRAPH_DEPTH = 64
MAX_BOUNDARIES = 10_000
MAX_FAILURES = 10_000
RESULT_MAX_BYTES = 64 * 1024 * 1024
ExecutionKey = tuple[str, str, str]
SelectionPolicy = Literal["incremental", "recheck"]
INCREMENTAL_SELECTION: SelectionPolicy = "incremental"
RECHECK_SELECTION: SelectionPolicy = "recheck"


@dataclass(frozen=True)
class ReproductionSelection:
    """Selection policy for a fresh plan."""

    policy: SelectionPolicy = INCREMENTAL_SELECTION


@dataclass(frozen=True)
class ReproductionCommandInventory:
    """All command execution units and remaining policy exclusions in one target."""

    total: int
    policy_skipped: int


@dataclass(frozen=True)
class PreparedReproductionContext:
    """One full evaluation and its accepted, stored admission decision."""

    evaluation: EvaluationResult
    admission: "ValidationAdmission"


def prepare_reproduction_context(
    evaluation: EvaluationResult,
    admission: "ValidationAdmission",
) -> PreparedReproductionContext:
    """Bind planning to the exact full validation result just published."""

    if evaluation.record.completion is CompletionState.INCOMPLETE:
        raise ActionError(
            "reproduction.validation.incomplete", "prepared evaluation is incomplete"
        )
    if evaluation.record.rules_version != RULES_VERSION:
        raise ActionError("reproduction.validation.invalid", "prepared rules are stale")
    if admission.validation_id == "" or admission.result_id == "":
        raise ActionError(
            "reproduction.validation.invalid", "stored validation is invalid"
        )
    return PreparedReproductionContext(evaluation, admission)


@dataclass(frozen=True)
class _EntryState:
    context: EntryContext
    data: DataFile | None
    evidence: EvidenceFile | None
    pyrun: PyrunFile


@dataclass(frozen=True)
class _Owner:
    entry: _EntryState
    cid: str
    execution_id: str
    execution: PyrunExecution
    output: str
    target: str
    kind: str

    @property
    def key(self) -> ExecutionKey:
        """Return the entry-qualified identity of this physical execution."""

        return self.entry.context.id, self.cid, self.execution_id


@dataclass(frozen=True)
class _Failure:
    entry: str
    artifact: str
    execution_id: str | None
    reason: str
    dependencies: tuple[str, ...] = ()
    cid: str | None = None


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
    execution_timeout_seconds: int
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
    evidence_selections: dict[tuple[str, str, str], dict[str, object]] = field(
        default_factory=dict
    )
    authority_paths: set[Path] = field(default_factory=set)
    admitted_batches: set[tuple[str, str]] = field(default_factory=set)
    excluded_batches: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict
    )
    command_digests: dict[ExecutionKey, str] = field(default_factory=dict)
    command_selections: dict[ExecutionKey, str] = field(default_factory=dict)
    prior_command_dispositions: dict[ExecutionKey, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReproductionStateProjection:
    """Current evidence reachability and execution timing without validation."""

    reachable: frozenset[tuple[str, str]]
    output_executions: Mapping[tuple[str, str], ExecutionKey]
    last_runs: Mapping[ExecutionKey, str | None]
    comparison_definitions: Mapping[tuple[str, str], str | None] = field(
        default_factory=dict
    )
    reachable_commands: frozenset[ExecutionKey] = frozenset()


@dataclass
class _ReachabilityProjector:
    """Bounded topology-only projection over current JSON authority."""

    log: LogContext
    project_root: Path
    entries: Mapping[str, _EntryState]
    owners: Mapping[str, tuple[_Owner, ...]]
    reachable: set[tuple[str, str]] = field(default_factory=set)
    output_executions: dict[tuple[str, str], ExecutionKey] = field(default_factory=dict)
    last_runs: dict[ExecutionKey, str | None] = field(default_factory=dict)
    comparison_definitions: dict[tuple[str, str], str | None] = field(
        default_factory=dict
    )
    visited: set[ExecutionKey] = field(default_factory=set)

    def execution(self, owner: _Owner, *, trace_inputs: bool = True) -> None:
        key = owner.key
        if key in self.visited:
            return
        self.visited.add(key)
        for output, _ in owner.execution.recipe.outputs:
            artifact_key = (owner.entry.context.id, output)
            self.reachable.add(artifact_key)
            self.output_executions[artifact_key] = owner.key
            self.comparison_definitions[artifact_key] = _comparison_identity(
                owner, output, self.project_root
            )
        self.last_runs[key] = owner.execution.last_run_at
        if owner.entry.data is None or not trace_inputs:
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
            self.output_executions[evidence_key] = candidates[0].key
            self.last_runs[candidates[0].key] = candidates[0].execution.last_run_at
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
            frozenset(self.visited),
        )


def plan_reproduction(  # noqa: PLR0913
    log: LogContext,
    prepared: PreparedReproductionContext,
    *,
    entry: EntryContext | None,
    include_all: bool,
    runtime: ReproductionRuntime = ReproductionRuntime(),
    selection: ReproductionSelection = ReproductionSelection(),
) -> ReproductionPlan:
    """Build one deterministic plan under the requested work-selection policy."""

    _require_selection_policy(selection.policy)
    if (
        isinstance(runtime.jobs, bool)
        or not isinstance(runtime.jobs, int)
        or runtime.jobs <= 0
    ):
        raise ActionError("reproduction.jobs.invalid", "--jobs must be positive")
    if (
        isinstance(runtime.execution_timeout_seconds, bool)
        or not isinstance(runtime.execution_timeout_seconds, int)
        or not 1 <= runtime.execution_timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS
    ):
        raise ActionError(
            "reproduction.execution_timeout.invalid",
            "--execution-timeout-seconds must be between 1 and "
            f"{MAX_EXECUTION_TIMEOUT_SECONDS}",
        )
    if prepared.evaluation.record.completion is CompletionState.INCOMPLETE:
        raise ActionError(
            "reproduction.validation.incomplete", "prepared evaluation is incomplete"
        )
    project_root = resolve_project_root(log.root)
    entries = _prepared_entries(
        log, project_root, prepared.evaluation.context.materials
    )
    selected_ids = (
        (entry.id,) if entry is not None and entry.id in entries else tuple(entries)
    )
    state = _PlanningState(
        log,
        project_root,
        selected_ids,
        entry is not None,
        include_all,
        runtime.jobs,
        runtime.execution_timeout_seconds,
        selection.policy,
        entries,
        _owner_index(entries, project_root),
    )
    _trace_selected_evidence(selected_ids, entries, state)
    _trace_queued_commands(state)
    _apply_validation_admission(state, prepared.admission)
    _apply_cycle_and_dependency_failures(state)
    retained_commands = dict(
        _load_prior_results(log, replace_outdated=selection.policy == RECHECK_SELECTION)
    )
    ordered = _select_and_order(state, retained_commands)
    plan = _project_plan(
        state,
        ordered,
        prepared,
        entry=entry,
    )
    plan.serialized()
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
                _retain_evidence_selection(
                    state, current.context.id, record.id, resolved.resource
                )
                _trace_resource(
                    resolved.resource, current, state, consumer=None, depth=0
                )


def _trace_queued_commands(state: _PlanningState) -> None:
    """Trace every authorized command, including commands outside evidence roots."""

    owners: dict[ExecutionKey, _Owner] = {}
    for candidates in state.owners.values():
        for owner in candidates:
            owners.setdefault(owner.key, owner)
    for key, owner in sorted(owners.items()):
        if key[0] not in state.selected_entries:
            continue
        if not owner.execution.auto_reproduce and not state.include_all:
            continue
        _trace_execution(
            owner,
            state,
            depth=0,
            trace_inputs=True,
        )


def _require_selection_policy(selection_policy: SelectionPolicy) -> None:
    if selection_policy not in {
        INCREMENTAL_SELECTION,
        RECHECK_SELECTION,
    }:
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
    """Project current evidence reachability without writing."""

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


def project_reproduction_command_inventory(
    log: LogContext, target: Mapping[str, object]
) -> ReproductionCommandInventory:
    """Count current commands in an exact log or entry target."""

    project_root = resolve_project_root(log.root)
    contexts = _entry_contexts(log)
    kind = target.get("kind")
    entry = target.get("entry")
    if kind == "entry" and isinstance(entry, str):
        contexts = tuple(context for context in contexts if context.id == entry)
        if not contexts:
            raise ActionError(
                "reproduction.entry.unknown", f"unknown reproduction entry: {entry}"
            )
    elif target != {"entry": None, "kind": "log"}:
        raise ActionError(
            "reproduction.target.invalid", "reproduction target is invalid"
        )

    total = 0
    policy_skipped = 0
    for context in contexts:
        path = context.root / "pyrun.json"
        try:
            state = (
                load_pyrun_state(
                    path,
                    entry_root=context.root,
                    project_root=project_root,
                )
                if path.is_file() or path.is_symlink()
                else empty_pyrun_state(context.root)
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ActionError(
                str(getattr(error, "code", "reproduction.metadata.invalid")),
                str(error),
            ) from error
        executions = [item[2] for item in state.execution_items()]
        total += len(executions)
        policy_skipped += sum(
            execution.requires_reproduction and not execution.auto_reproduce
            for execution in executions
        )
    return ReproductionCommandInventory(total, policy_skipped)


def project_reproduction_command_details(
    log: LogContext, target: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    """Project current recipes in an exact log or entry target."""

    project_root = resolve_project_root(log.root)
    contexts = _entry_contexts(log)
    kind = target.get("kind")
    entry = target.get("entry")
    if kind == "entry" and isinstance(entry, str):
        contexts = tuple(context for context in contexts if context.id == entry)
        if not contexts:
            raise ActionError(
                "reproduction.entry.unknown", f"unknown reproduction entry: {entry}"
            )
    elif target != {"entry": None, "kind": "log"}:
        raise ActionError(
            "reproduction.target.invalid", "reproduction target is invalid"
        )

    details: list[Mapping[str, object]] = []
    for context in contexts:
        path = context.root / "pyrun.json"
        try:
            state = (
                load_pyrun_state(
                    path,
                    entry_root=context.root,
                    project_root=project_root,
                )
                if path.is_file() or path.is_symlink()
                else empty_pyrun_state(context.root)
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ActionError(
                str(getattr(error, "code", "reproduction.metadata.invalid")),
                str(error),
            ) from error
        cwd = context.root.resolve().relative_to(project_root).as_posix()
        for cid, execution_id, execution in state.execution_items():
            details.append(
                {
                    "auto_reproduce": execution.auto_reproduce,
                    "cwd": cwd,
                    "entry": context.id,
                    "cid": cid,
                    "execution_id": execution_id,
                    "exclusive": execution.exclusive,
                    "recipe": execution.recipe.as_dict(),
                    "requires_reproduction": execution.requires_reproduction,
                }
            )
    return tuple(details)


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


def _prepared_entries(
    log: LogContext,
    project_root: Path,
    materials: Sequence[EvaluationEntryMaterial],
) -> dict[str, _EntryState]:
    """Convert evaluator-owned loaded entry material without a second load."""

    entries: dict[str, _EntryState] = {}
    for material in materials:
        if material.errors:
            error = material.errors[0]
            raise ActionError(
                str(getattr(error, "code", "reproduction.metadata.invalid")), str(error)
            )
        if material.pyrun is None and material.errors:
            raise ActionError(
                "reproduction.metadata.invalid",
                f"missing execution state for {material.entry_id}",
            )
        context = EntryContext(log, material.entry_id, material.entry_root)
        if material.entry_id in entries:
            raise ActionError(
                "reproduction.entry.duplicate", "duplicate entry material"
            )
        entries[material.entry_id] = _EntryState(
            context,
            material.data,
            material.evidence,
            material.pyrun or empty_pyrun_state(material.entry_root),
        )
    return entries


def _owner_index(
    entries: Mapping[str, _EntryState], project_root: Path
) -> dict[str, tuple[_Owner, ...]]:
    found: dict[str, list[_Owner]] = defaultdict(list)
    for state in entries.values():
        for cid, identity, execution in state.pyrun.execution_items():
            for output, kind in execution.recipe.outputs:
                target = (
                    output_target_path(
                        output, entry_root=state.context.root, project_root=project_root
                    )
                    .resolve()
                    .as_posix()
                )
                found[target].append(
                    _Owner(state, cid, identity, execution, output, target, kind)
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
    request = _BoundaryRequest("cross_entry", owner_entry, resource, artifact, consumer)
    if not in_scope:
        _trace_out_of_scope_resource(candidates, request, state)
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
    _trace_resource_producer(in_scope[0], request, state, depth)


def _trace_out_of_scope_resource(
    candidates: Sequence[_Owner],
    request: _BoundaryRequest,
    state: _PlanningState,
) -> None:
    if not state.entry_target:
        _record_failure(
            state,
            _Failure(
                request.entry.context.id,
                request.artifact,
                None,
                "cross_log_generated_input",
            ),
        )
        return
    _verified_boundary(state, request)
    if request.consumer is None:
        execution_id = candidates[0].execution_id if len(candidates) == 1 else None
        state.cases[(request.entry.context.id, request.artifact)] = _case(
            request.entry.context.id,
            request.artifact,
            (candidates[0].cid if len(candidates) == 1 else None, execution_id),
            "skipped",
            "outside_entry",
        )


def _trace_resource_producer(
    producer: _Owner,
    request: _BoundaryRequest,
    state: _PlanningState,
    depth: int,
) -> None:
    if _stop_at_nonautomatic_policy(
        producer,
        _BoundaryRequest(
            "non_automatic",
            request.entry,
            request.resource,
            request.artifact,
            request.consumer,
        ),
        state,
        depth=depth,
    ):
        return
    if request.consumer is not None:
        state.dependencies[request.consumer.key].add(producer.key)
    _trace_execution(producer, state, depth=depth)


def _stop_at_nonautomatic_policy(
    producer: _Owner,
    boundary: _BoundaryRequest,
    state: _PlanningState,
    *,
    depth: int,
) -> bool:
    """Bound traversal at one current or policy-skipped nonautomatic command."""

    if producer.execution.auto_reproduce or state.include_all:
        return False
    if not producer.execution.requires_reproduction:
        _trace_execution(producer, state, depth=depth, trace_inputs=False)
        return True
    _verified_boundary(state, boundary)
    if boundary.consumer is None:
        state.cases[(boundary.entry.context.id, boundary.artifact)] = _case(
            boundary.entry.context.id,
            boundary.artifact,
            (producer.cid, producer.execution_id),
            "skipped",
            "non_automatic",
        )
    return True


def _trace_execution(
    owner: _Owner,
    state: _PlanningState,
    *,
    depth: int,
    trace_inputs: bool = True,
) -> None:
    key = owner.key
    identity = owner.execution_id
    state.selected.setdefault(key, owner)
    _record_execution_materials(owner, state)
    for output, _ in owner.execution.recipe.outputs:
        state.cases.setdefault(
            (owner.entry.context.id, output),
            _case(owner.entry.context.id, output, (owner.cid, identity), "run", None),
        )
    if not trace_inputs:
        state.visited.add(key)
        _check_graph_bounds(state)
        return
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
                    owner.cid,
                ),
            )
            state.blocked.add(key)
            continue
        material = _material(
            resource.canonical_target,
            "input",
            resource.kind,
            dict(owner.execution.observed.inputs)[name],
        )
        material["selection"] = resource.identity.as_dict()
        _retain_material(
            state,
            ("input", _resource_selection_key(resource)),
            material,
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
    failure = (
        ("script", "missing_observation")
        if execution.observed.script is None
        else _record_source_material(
            state, owner, script, "script", execution.observed.script
        )
    )
    if failure is not None:
        failures.append(failure)
    for name, fingerprint in execution.observed.code:
        path = code_target_path(name, entry_root=owner.entry.context.root)
        failure = _record_source_material(state, owner, path, "code", fingerprint)
        if failure is not None:
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
                    owner.cid,
                ),
            )


def _observation_resource(
    name: str, kind: str, location: str, fingerprint: Fingerprint
) -> InputResource:
    """Adapt an observed fingerprint for read-only current-byte comparison."""

    identity = ResourceIdentity(
        fingerprint.algorithm,
        commit=(
            fingerprint.digest
            if fingerprint.algorithm == "git-commit-sha1-v1"
            else None
        ),
        files=fingerprint.files,
        patterns=fingerprint.patterns,
    )
    return InputResource(name, kind, location, identity, True, location)


def _record_source_material(
    state: _PlanningState,
    owner: _Owner,
    path: Path,
    role: str,
    recorded: Fingerprint,
) -> tuple[str, str] | None:
    """Record the current source fingerprint required by this plan."""

    identity = path.resolve().as_posix()
    reason_role = "participating_code" if role == "code" else "script"
    resource = _observation_resource("planning-source", "file", identity, recorded)
    try:
        accepted = observe_fingerprint(resource).fingerprint
    except (OSError, ValueError) as error:
        return f"{reason_role}_unavailable", f"{reason_role}:{identity}:{error}"
    if accepted != recorded:
        return f"{reason_role}_changed", (
            f"{reason_role}:{identity}:expected={recorded.content_identity}:"
            f"observed={accepted.content_identity}"
        )
    _retain_material(
        state,
        (role, identity),
        _material(identity, role, "file", accepted),
        owner=owner.key,
    )
    return None


def _comparison_identity(owner: _Owner, output: str, project_root: Path) -> str | None:
    """Return one output's evidence-comparison definition identity, if any."""

    data = owner.entry.data
    if data is None:
        return None
    target = _output_target(owner, output, project_root)
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


def _output_target(owner: _Owner, output: str, project_root: Path) -> str:
    """Return the canonical output target used by frozen comparison binding."""

    return (
        output_target_path(
            output,
            entry_root=owner.entry.context.root,
            project_root=project_root,
        )
        .resolve()
        .as_posix()
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
    expected = (
        dict(request.consumer.execution.observed.inputs).get(request.resource.name)
        if request.consumer is not None
        else None
    )
    if expected is not None and observed.as_dict() != expected.as_dict():
        _record_boundary_failure(
            state,
            request,
            (
                "direct_input_changed"
                if request.consumer is not None
                else "boundary_changed"
            ),
            (
                request.resource.canonical_target,
                f"expected={expected.content_identity}",
                f"observed={observed.content_identity}",
            ),
        )
        return
    _boundary(state, request, observed)


def _record_boundary_failure(
    state: _PlanningState,
    request: _BoundaryRequest,
    reason: str,
    details: tuple[str, ...],
) -> None:
    producers = _resource_owners(state.owners, request.resource.canonical_target)
    details += tuple(f"prerequisite={_reference(owner.key)}" for owner in producers)
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
                consumer.cid,
            ),
        )


def _boundary(
    state: _PlanningState,
    request: _BoundaryRequest,
    observed: Fingerprint,
) -> None:
    entry = request.entry
    resource = request.resource
    artifact = request.artifact
    value: dict[str, object] = {
        "artifact": artifact,
        "entry": entry.context.id,
        "fingerprint": observed.as_dict(),
        "kind": request.kind,
        "name": resource.name,
    }
    state.boundaries[(request.kind, entry.context.id, artifact)] = value
    _retain_material(
        state,
        ("boundary", resource.canonical_target),
        _material(resource.canonical_target, "boundary", resource.kind, observed),
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
        (failure.cid, failure.execution_id),
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
                    owner.cid,
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
                        (owner.cid, owner.execution_id),
                        "skipped",
                        "dependency_failed",
                    )
                changed = True


def _apply_validation_admission(
    state: _PlanningState, admission: "ValidationAdmission"
) -> None:
    """Exclude only executions owned by validation-blocked command batches."""

    _validate_stored_admission(admission)
    blocked = _blocked_validation_batches(admission)
    entry_blockers = _entry_validation_blockers(admission)
    _require_resolved_validation_blockers(admission)
    for key, owner in sorted(state.selected.items()):
        entry_groups = entry_blockers.get(owner.entry.context.id)
        if entry_groups:
            _exclude_entry_execution(state, key, owner, entry_groups)
            continue
        _apply_execution_admission(state, key, owner, admission, blocked)


def _validate_stored_admission(admission: "ValidationAdmission") -> None:
    groups = {group.identity: group for group in admission.groups}
    if len(groups) != len(admission.groups):
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            "validation groups are duplicate",
        )
    for command in admission.commands:
        group = groups.get(command.group_id)
        if group is None or group.kind != "chain" or command.entry != group.entry:
            raise ActionError(
                "reproduction.validation.scope_unresolved",
                "validation command group is invalid",
            )
    for finding in admission.findings:
        group = groups.get(finding.group_id)
        if group is None or finding.admission_effect not in {
            "none",
            "chain",
            "entry",
            "log",
        }:
            raise ActionError(
                "reproduction.validation.scope_unresolved",
                "validation finding has no supported admission effect",
            )
        if group.kind == "chain":
            if finding.admission_effect not in {"none", "chain"} or (
                finding.admission_effect == "chain"
                and (
                    finding.affected_chains != (group.identity,)
                    or finding.affected_entries != (_physical_entry(group.entry),)
                )
            ):
                raise ActionError(
                    "reproduction.validation.scope_unresolved",
                    "validation chain has inconsistent admission ownership",
                )
        elif (
            finding.admission_effect == "chain"
            or finding.affected_chains
            or (
                finding.admission_effect == "entry"
                and len(finding.affected_entries) != 1
            )
            or (
                finding.admission_effect in {"none", "log"} and finding.affected_entries
            )
        ):
            raise ActionError(
                "reproduction.validation.scope_unresolved",
                "validation finding has inconsistent admission ownership",
            )


def _blocked_validation_batches(
    admission: "ValidationAdmission",
) -> dict[tuple[str, str], tuple[str, ...]]:
    blocked: dict[tuple[str, str], tuple[str, ...]] = {}
    for group in admission.groups:
        if group.kind != "chain":
            continue
        finding_ids = tuple(
            sorted(
                finding.identity
                for finding in admission.findings
                if finding.group_id == group.identity
                and finding.admission_effect == "chain"
            )
        )
        if finding_ids:
            blocked[(_physical_entry(group.entry), group.identity)] = finding_ids
    return blocked


def _require_resolved_validation_blockers(admission: "ValidationAdmission") -> None:
    if any(finding.admission_effect == "log" for finding in admission.findings):
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            "a blocking validation finding has no safe batch scope",
        )


def _entry_validation_blockers(
    admission: "ValidationAdmission",
) -> dict[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    result: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    groups = {group.identity: group for group in admission.groups}
    for finding in admission.findings:
        if finding.admission_effect != "entry":
            continue
        for entry in finding.affected_entries:
            result.setdefault(entry, []).append(
                (groups[finding.group_id].identity, (finding.identity,))
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
                owner.cid,
            ),
        )


def _apply_execution_admission(
    state: _PlanningState,
    key: ExecutionKey,
    owner: _Owner,
    admission: "ValidationAdmission",
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
    groups = {group.identity: group for group in admission.groups}
    matches = {
        command.group_id
        for command in admission.commands
        if _physical_entry(groups[command.group_id].entry) == owner.entry.context.id
        and targets <= set(command.outputs)
    }
    if len(matches) != 1:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            f"execution has {len(matches)} projected batch matches: {key[2]}",
        )
    group_id = next(iter(matches))
    batch_key = (owner.entry.context.id, group_id)
    blockers = blocked.get(batch_key)
    if not blockers:
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
                owner.cid,
            ),
        )


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _physical_entry(value: str) -> str:
    """Resolve the stored entry-document identity to its physical entry owner."""

    identity = parse_entry_document_name(f"{value}.md")
    if identity is None:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            f"stored validation group has invalid entry scope: {value}",
        )
    return identity.id


def _string_items(value: object) -> tuple[str, ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, str))


def _select_and_order(
    state: _PlanningState,
    prior: Mapping[ExecutionKey, Mapping[str, object]],
) -> tuple[ExecutionKey, ...]:
    policy_skipped = {
        key
        for key, owner in state.selected.items()
        if (state.selection_policy == RECHECK_SELECTION)
        and not state.include_all
        and not owner.execution.auto_reproduce
    }
    state.command_digests = {
        key: _command_source_digest(state, key)
        for key in sorted(set(state.selected) - policy_skipped)
    }
    runnable = set(state.selected) - state.blocked - policy_skipped
    needs_run = _initial_work(state, prior, runnable)
    _propagate_required_work(state, runnable, needs_run)
    not_needed = {
        key
        for key in state.selected
        if key not in policy_skipped
        and state.selection_policy != RECHECK_SELECTION
        and key not in needs_run
        and key not in state.blocked
        and (not state.selected[key].execution.requires_reproduction)
    }
    unchanged = {
        key
        for key in state.selected
        if key not in policy_skipped
        and key not in needs_run
        and key not in not_needed
        and key not in state.blocked
        and _command_result_current(prior.get(key), state.command_digests[key])
        and state.selection_policy == INCREMENTAL_SELECTION
    }
    state.prior_command_dispositions = {
        key: cast(str, prior[key]["disposition"]) for key in unchanged
    }
    for key in state.selected:
        if key in policy_skipped:
            selection = "policy"
        elif key in needs_run:
            selection = "run"
        elif key in not_needed:
            selection = "not_needed"
        elif key in unchanged:
            selection = "unchanged"
        else:
            selection = "blocked"
        state.command_selections[key] = selection
    _project_current_cases(state, not_needed | unchanged | policy_skipped)
    return _topological_order(state, needs_run)


def _initial_work(
    state: _PlanningState,
    prior: Mapping[ExecutionKey, Mapping[str, object]],
    runnable: set[ExecutionKey],
) -> set[ExecutionKey]:
    """Select runnable executions under the active work-selection policy."""

    if state.selection_policy == RECHECK_SELECTION:
        return set(runnable)

    return {
        key
        for key in runnable
        if state.selected[key].execution.requires_reproduction
        and not _command_result_current(prior.get(key), state.command_digests[key])
    }


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
                (owner.cid, owner.execution_id),
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


def _command_result_current(
    result: Mapping[str, object] | None,
    source_digest: str,
    dispositions: Collection[str] = ("failed", "blocked"),
) -> bool:
    """Return whether one prior terminal command result has the same closure."""

    return (
        result is not None
        and result.get("disposition") in dispositions
        and result.get("source_digest") == source_digest
    )


def _command_source_digest(state: _PlanningState, key: ExecutionKey) -> str:
    """Hash every current source component owned by one command."""

    owner = state.selected[key]
    outputs = []
    for output, _kind in owner.execution.recipe.outputs:
        case = state.cases[(owner.entry.context.id, output)]
        reason = case.get("reason")
        failure = (
            state.failures.get((owner.entry.context.id, output, str(reason)))
            if isinstance(reason, str)
            else None
        )
        outputs.append(
            {
                "artifact": output,
                "comparison_definition": _comparison_identity(
                    owner, output, state.project_root
                ),
                "planning_disposition": case["disposition"],
                "planning_failure_dependencies": (
                    list(_string_items(failure.get("dependencies")))
                    if failure is not None
                    else []
                ),
                "planning_reason": reason,
            }
        )
    materials = sorted(
        (
            dict(value)
            for material_key, value in state.materials.items()
            if key in state.material_owners[material_key]
        ),
        key=lambda value: (str(value["role"]), str(value["identity"])),
    )
    return canonical_record_digest(
        {
            "contract": "research-log-reproduction-command-source/1",
            "dependencies": [
                _reference(value)
                for value in sorted(state.dependencies.get(key, set()))
            ],
            "entry": owner.entry.context.id,
            "cid": owner.cid,
            "execution": canonical_execution_source_digest(owner.execution.as_dict()),
            "execution_id": owner.execution_id,
            "materials": materials,
            "outputs": outputs,
        }
    )


def _project_plan(
    state: _PlanningState,
    ordered: tuple[ExecutionKey, ...],
    prepared: PreparedReproductionContext,
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
                "cid": owner.cid,
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
    runnable = set(state.selected) - state.blocked
    materials = sorted(
        (
            value
            for key, value in state.materials.items()
            if None in state.material_owners[key]
            or bool(state.material_owners[key] & runnable)
        ),
        key=lambda value: (str(value["role"]), str(value["identity"])),
    )
    command_details = {
        (
            cast(str, item["entry"]),
            cast(str, item["cid"]),
            cast(str, item["execution_id"]),
        ): item
        for item in _project_command_details(state)
    }
    commands = tuple(
        {
            **dict(command_details[key]),
            "prior_disposition": state.prior_command_dispositions.get(key),
            "selection": state.command_selections.get(
                key,
                ("policy" if command_details[key]["queued"] is False else "not_needed"),
            ),
            "source_digest": state.command_digests.get(key),
        }
        for key in sorted(
            command_details, key=lambda item: (item not in state.selected, item)
        )
    )
    cases = tuple(
        state.cases[key]
        for key in sorted(
            state.cases, key=lambda value: (_entry_order(value[0]), value[1])
        )
    )
    boundaries = tuple(state.boundaries[key] for key in sorted(state.boundaries))
    failures = tuple(state.failures[key] for key in sorted(state.failures))
    comparisons = _project_comparisons(state, runnable)
    admission = {
        "evaluated_at": prepared.evaluation.record.result_date,
        "rules_version": prepared.evaluation.record.rules_version,
        "validation_id": prepared.admission.validation_id,
        "validation_result_id": prepared.admission.result_id,
        "batch_admission": {
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
        },
    }
    return ReproductionPlan(
        _canonical_path(state.log.summary, state.project_root),
        {
            "entry": entry.id if entry is not None else None,
            "kind": "entry" if entry is not None else "log",
        },
        state.include_all,
        admission,
        commands,
        {
            "materials": materials,
            "evidence_only": _project_evidence_only_context(state, comparisons),
            "comparisons": comparisons,
            "result_schema": "research-log-reproduction-result/11",
            "schema": "research-log-reproduction-comparison-context/1",
        },
        cases,
        tuple(executions),
        boundaries,
        failures,
        state.jobs,
        state.execution_timeout_seconds,
    )


def _project_command_details(
    state: _PlanningState,
) -> tuple[Mapping[str, object], ...]:
    """Project immutable accepted metadata for every command in the target."""

    details: list[Mapping[str, object]] = []
    for entry_id in state.selected_entries:
        current = state.entries[entry_id]
        cwd = current.context.root.resolve().relative_to(state.project_root).as_posix()
        for cid, execution_id, execution in current.pyrun.execution_items():
            reasons = sorted(
                {
                    cast(str, case["reason"])
                    for case in state.cases.values()
                    if case.get("entry") == entry_id
                    and case.get("cid") == cid
                    and case.get("execution_id") == execution_id
                    and isinstance(case.get("reason"), str)
                }
            )
            details.append(
                {
                    "auto_reproduce": execution.auto_reproduce,
                    "cwd": cwd,
                    "details": reasons,
                    "entry_root": current.context.root.as_posix(),
                    "project_root": state.project_root.as_posix(),
                    "entry": entry_id,
                    "cid": cid,
                    "execution_id": execution_id,
                    "exclusive": execution.exclusive,
                    "queued": execution.auto_reproduce or state.include_all,
                    "execution_state": execution.as_dict(),
                    "data_declaration": (
                        {
                            "inputs": [
                                _resolved_input_declaration(item)
                                for item in current.data.inputs
                            ],
                            "schema": "research-log-data/v5",
                        }
                        if current.data is not None
                        else None
                    ),
                    "requires_reproduction": execution.requires_reproduction,
                }
            )
    return tuple(details)


def _project_comparisons(
    state: _PlanningState, runnable: Collection[ExecutionKey]
) -> list[dict[str, object]]:
    """Project exactly the runnable frozen comparison inventory."""

    return [
        {
            "entry": owner.entry.context.id,
            "cid": owner.cid,
            "execution_id": owner.execution_id,
            "output": output,
            "evidence_records": (
                [record.as_dict() for record in owner.entry.evidence.records]
                if owner.entry.evidence is not None
                else []
            ),
            "definition_identity": definition,
        }
        for key, owner in sorted(state.selected.items())
        for output, _kind in owner.execution.recipe.outputs
        for definition in (_comparison_identity(owner, output, state.project_root),)
        if key in runnable and definition is not None
    ]


def _project_evidence_only_context(
    state: _PlanningState, comparisons: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Freeze selected presentation-only inputs and consuming definitions."""

    rows: list[dict[str, object]] = []
    for entry_id in state.selected_entries:
        entry = state.entries[entry_id]
        if entry.data is None or entry.evidence is None:
            continue
        for record in entry.evidence.records:
            record_targets = {
                resolve_input_token(source.source, entry.data).resource.canonical_target
                for source in record.sources
            }
            definitions = _record_comparison_definitions(
                state, entry_id, record_targets, comparisons
            )
            if not definitions:
                continue
            for source in record.sources:
                resource = resolve_input_token(source.source, entry.data).resource
                material = state.evidence_selections.get(
                    (entry_id, record.id, _resource_selection_key(resource))
                )
                if material is None:
                    continue
                rows.append(
                    {
                        "comparisons": definitions,
                        "entry": entry_id,
                        "fingerprint": material["fingerprint"],
                        "record_id": record.id,
                        "resource": resource.canonical_target,
                        "kind": resource.kind,
                        "selection": resource.identity.as_dict(),
                    }
                )
    return rows


def _record_comparison_definitions(
    state: _PlanningState,
    entry_id: str,
    record_targets: set[str],
    comparisons: Sequence[Mapping[str, object]],
) -> list[str]:
    """Return frozen output definitions whose retained record names that output."""

    return sorted(
        {
            cast(str, comparison["definition_identity"])
            for comparison in comparisons
            if comparison.get("entry") == entry_id
            and isinstance(comparison.get("output"), str)
            and isinstance(comparison.get("definition_identity"), str)
            and _comparison_output_target(
                state, entry_id, cast(str, comparison["output"])
            )
            in record_targets
        }
    )


def _comparison_output_target(state: _PlanningState, entry_id: str, output: str) -> str:
    """Resolve a persisted comparison output under its selected entry root."""

    return (
        output_target_path(
            output,
            entry_root=state.entries[entry_id].context.root,
            project_root=state.project_root,
        )
        .resolve()
        .as_posix()
    )


def _retain_evidence_selection(
    state: _PlanningState, entry: str, record_id: str, resource: InputResource
) -> None:
    """Observe a retained evidence source using its declared selection semantics."""

    try:
        fingerprint = observe_fingerprint(resource).fingerprint
    except (OSError, ValueError) as error:
        raise ActionError(
            "reproduction.evidence.unavailable",
            f"cannot observe evidence source {resource.name}: {error}",
        ) from error
    key = (entry, record_id, _resource_selection_key(resource))
    state.evidence_selections[key] = {
        "fingerprint": fingerprint.as_dict(),
        "kind": resource.kind,
        "selection": resource.identity.as_dict(),
    }


def _resource_selection_key(resource: InputResource) -> str:
    """Return the distinct retained identity of one selected input observation."""

    return canonical_record_digest(
        {
            "kind": resource.kind,
            "resource": resource.canonical_target,
            "selection": resource.identity.as_dict(),
        }
    )


def _resolved_input_declaration(resource: InputResource) -> dict[str, object]:
    """Persist a cross-entry declaration with its resolved target intact."""

    value: dict[str, object] = {
        "canonical_target": resource.canonical_target,
        "identity": resource.identity.as_dict(),
        "kind": resource.kind,
        "location": resource.canonical_target,
        "name": resource.name,
        "origin": resource.origin,
        "reference_entry": resource.reference_entry,
    }
    if resource.comparison is not None:
        value["comparison"] = resource.comparison.as_dict()
    return value


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


def _load_prior_results(
    log: LogContext,
    *,
    replace_outdated: bool,
) -> dict[ExecutionKey, Mapping[str, object]]:
    from .reproduction_result_storage import (
        ReproductionStorageError,
        load_current_execution_results,
    )

    path = log.root / RESULTS_STORE
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        return cast(
            dict[ExecutionKey, Mapping[str, object]],
            load_current_execution_results(path),
        )
    except ReproductionStorageError as error:
        if replace_outdated:
            return {}
        raise ActionError(
            "reproduction.results.schema_unsupported",
            f"{error}; run whole-log reproduction with --recheck to rebuild it",
        ) from error


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
    identity: tuple[str | None, str | None],
    disposition: str,
    reason: str | None,
) -> dict[str, object]:
    cid, execution_id = identity
    return {
        "artifact": artifact,
        "disposition": disposition,
        "entry": entry,
        "cid": cid,
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
    resource = _observation_resource("planning-material", kind, identity, expected)
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


def _entry_order(value: str) -> int:
    return int(value[1:]) if value.startswith("e") and value[1:].isdigit() else 2**31


def _reference(key: ExecutionKey) -> str:
    """Return an unambiguous run-local dependency reference."""

    return f"{key[0]}:{key[1]}:{key[2]}"
