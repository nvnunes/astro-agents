"""Evidence-rooted, command-bounded research-log reproduction planning."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast

from effective_code import (
    EffectiveCodeAnalysis,
    EffectiveCodeError,
    UnsupportedLocation,
    analyze_effective_code,
)
from python_execution import PythonExecutionContext
from research_log_data import (
    DATA_SCHEMA,
    DataFile,
    Fingerprint,
    InputResource,
    ResourceIdentity,
    observe_fingerprint,
    resolve_input_token,
)
from validation.domain import Finding, plain_json
from validation.engine import RULES_VERSION, EvaluationEntryMaterial, EvaluationResult
from validation.evidence import EvidenceFile
from validation.evidence_comparison import (
    EvidenceComparisonDefinition,
    evidence_comparison_definition,
)
from validation.provenance import ProducerCurrentness
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunExecution,
    PyrunFile,
    associate_execution,
    empty_pyrun_state,
    script_target_path,
)
from validation.research_graph import (
    AmbiguityKind,
    EdgeKind,
    NodeKind,
    ResearchGraph,
    ResearchNode,
)

from .context import (
    EntryContext,
    LogContext,
    resolve_project_root,
)
from .model import ActionError
from .reproduction_admission import (
    ExecutionAdmission,
    SelectedExecution,
    evaluate_reproduction_admission,
)
from .reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    ExecutionRef,
    NotComparedReason,
    ProblemStage,
    ReproductionProblem,
    SourceLocation,
    SourceRef,
    WorkSelection,
)
from .reproduction_invocation import (
    MAX_EXECUTION_TIMEOUT_SECONDS,
    AcceptedSource,
    ReproductionRuntime,
    canonical_execution_source_digest,
    canonical_record_digest,
    observe_script_source,
)
from .reproduction_run import ArtifactResult
from .reproduction_saved_run import RunSettings, RunTarget
from .reproduction_saved_storage import PreparationHistory
from .reproduction_work import ArtifactWork, CommandWork
from .reproduction_work_plan import ReproductionPlan as AcceptedWorkPlan

MAX_REACHABLE_EXECUTIONS = 2_048
MAX_ARTIFACT_CASES = 10_000
MAX_GRAPH_NODES = 16_384
MAX_GRAPH_EDGES = 32_768
MAX_GRAPH_DEPTH = 64
MAX_BOUNDARIES = 10_000
ExecutionKey = tuple[str, str, str]
SelectionPolicy = Literal["incremental", "recheck"]
INCREMENTAL_SELECTION: SelectionPolicy = "incremental"
RECHECK_SELECTION: SelectionPolicy = "recheck"


@dataclass(frozen=True)
class ReproductionSelection:
    """Selection policy for a fresh plan."""

    policy: SelectionPolicy = INCREMENTAL_SELECTION


@dataclass(frozen=True)
class PreparedReproductionContext:
    """One fresh complete evaluation used directly for planning."""

    evaluation: EvaluationResult


def prepare_reproduction_context(
    evaluation: EvaluationResult,
) -> PreparedReproductionContext:
    """Bind planning to one fresh complete canonical validation snapshot."""

    if evaluation.snapshot.failed_checks:
        raise ActionError(
            "reproduction.validation.failed", "prepared validation has failed checks"
        )
    if evaluation.attempt.rules_version != RULES_VERSION:
        raise ActionError("reproduction.validation.invalid", "prepared rules are stale")
    return PreparedReproductionContext(evaluation)


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


@dataclass
class _GraphOwnerProjection:
    """Indexed shared-graph state used to retain established owner payloads."""

    payloads: Mapping[str, Mapping[str, _Owner]]
    execution_by_command: Mapping[str, set[str]]
    nodes: Mapping[str, ResearchNode]
    found: dict[str, dict[tuple[ExecutionKey, str], _Owner]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def retain(self, command_id: str, target_id: str) -> None:
        executions = self.execution_by_command.get(command_id, set())
        target = self.nodes[target_id]
        if len(executions) != 1 or target.kind not in {
            NodeKind.MATERIAL,
            NodeKind.COLLECTION,
        }:
            return
        owner = self.payloads.get(next(iter(executions)), {}).get(target.identity)
        if owner is not None:
            self.found[target.identity][(owner.key, owner.output)] = owner


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
    graph: ResearchGraph
    command_owners: Mapping[ExecutionKey, _Owner]
    owners: Mapping[str, tuple[_Owner, ...]]
    currentness: Mapping[ExecutionKey, tuple[ProducerCurrentness, ...]]
    selected: dict[ExecutionKey, _Owner] = field(default_factory=dict)
    dependencies: dict[ExecutionKey, set[ExecutionKey]] = field(
        default_factory=lambda: defaultdict(set)
    )
    requested_dependencies: set[tuple[ExecutionKey, ExecutionKey]] = field(
        default_factory=set
    )
    boundaries: dict[tuple[str, str, str], dict[str, object]] = field(
        default_factory=dict
    )
    artifacts: dict[ArtifactRef, ArtifactWork] = field(default_factory=dict)
    problems: dict[str, ReproductionProblem] = field(default_factory=dict)
    command_problem_ids: dict[ExecutionKey, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    artifact_problem_ids: dict[ArtifactRef, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    recorded_execution_materials: set[ExecutionKey] = field(default_factory=set)
    effective_code_observations: dict[
        tuple[Path, bool, tuple[Path, ...]],
        EffectiveCodeAnalysis | EffectiveCodeError,
    ] = field(default_factory=dict)
    accepted_sources: dict[ExecutionKey, AcceptedSource] = field(default_factory=dict)
    source_selected: set[ExecutionKey] = field(default_factory=set)
    unverifiable_sources: set[ExecutionKey] = field(default_factory=set)
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
    admission_decisions: dict[ExecutionKey, ExecutionAdmission] = field(
        default_factory=dict
    )
    command_digests: dict[ExecutionKey, str] = field(default_factory=dict)
    command_selections: dict[ExecutionKey, str] = field(default_factory=dict)
    prior_command_dispositions: dict[ExecutionKey, str] = field(default_factory=dict)


def plan_reproduction_work(  # noqa: PLR0913
    log: LogContext,
    prepared: PreparedReproductionContext,
    *,
    entry: EntryContext | None,
    include_all: bool,
    runtime: ReproductionRuntime = ReproductionRuntime(),
    selection: ReproductionSelection = ReproductionSelection(),
) -> AcceptedWorkPlan:
    """Prepare canonical immutable work directly, without an old-plan projection.

    Fresh preview and acceptance use the same checks and selector. Prior facts
    must be authenticated native saved history; explicit recheck may ignore an
    unsupported old format but never translate it into replacement records.
    The caller owns the existing log lock and validation-publication decision.
    """

    state = _prepare_planning_state(
        log, prepared, entry, include_all, runtime, selection
    )
    history = _load_work_history(state)
    ordered = _select_and_order(state, _history_selection_facts(history))
    plan = _canonical_plan(state, ordered, prepared, entry, history)
    plan.serialized()
    return plan


def _prepare_planning_state(  # noqa: PLR0913
    log: LogContext,
    prepared: PreparedReproductionContext,
    entry: EntryContext | None,
    include_all: bool,
    runtime: ReproductionRuntime,
    selection: ReproductionSelection,
) -> _PlanningState:
    """Run the existing preparation checks once for both typed consumers."""

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
    if prepared.evaluation.snapshot.failed_checks:
        raise ActionError(
            "reproduction.validation.failed", "prepared validation has failed checks"
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
        prepared.evaluation.context.graph,
        _command_owner_index(entries, project_root),
        _graph_owner_index(
            entries,
            project_root,
            prepared.evaluation.context.graph,
        ),
        _currentness_by_execution(prepared.evaluation, entries, project_root),
    )
    _trace_selected_evidence(selected_ids, entries, state)
    _trace_queued_commands(state)
    _apply_graph_dependencies(state)
    _apply_validation_admission(state, prepared.evaluation)
    _apply_cycle_and_dependency_failures(state)
    return state


def _load_work_history(state: _PlanningState) -> PreparationHistory:
    from research_log_result_store import ResultStoreError, result_snapshot

    from .reproduction_domain import ReproductionDomainError
    from .reproduction_saved_storage import (
        SAVED_STORE_VERSION,
        _absent_domain,
        load_preparation_history,
    )

    try:
        with result_snapshot(state.log.root) as db:
            if db is None or _absent_domain(db):
                return PreparationHistory()
            if db.execute("PRAGMA user_version").fetchone()[0] != SAVED_STORE_VERSION:
                if state.selection_policy == RECHECK_SELECTION:
                    return PreparationHistory()
                raise ActionError(
                    "reproduction.results.unsupported",
                    "Saved reproduction format is unsupported; use log reproduce run "
                    "--recheck to replace it without migration.",
                )
            return load_preparation_history(
                db,
                tuple(ExecutionRef(*key) for key in sorted(_target_owners(state))),
                tuple(work.identity for work in _artifact_work(state)),
            )
    except ResultStoreError as error:
        if error.code == "results.store.missing":
            return PreparationHistory()
        raise ActionError("reproduction.results.invalid", str(error)) from error
    except ReproductionDomainError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error


def _history_selection_facts(
    history: PreparationHistory,
) -> dict[ExecutionKey, Mapping[str, object]]:
    """Supply only closure/status facts to the existing selection predicate."""

    prior: dict[ExecutionKey, Mapping[str, object]] = {}
    for identity, (work, result) in history.commands.items():
        disposition = (
            result.outcome.value
            if result is not None
            else {
                WorkSelection.PREVIOUS_FAILURE: "failed",
                WorkSelection.PREVIOUS_BLOCK: "blocked",
                WorkSelection.BLOCKED: "blocked",
            }.get(work.selection)
        )
        if (
            result is not None
            and result.outcome.value == "succeeded"
            and _has_complete_same_run_artifacts(history, identity, work)
        ):
            disposition = "succeeded"
        if work.source_digest is not None and disposition in {
            "failed",
            "blocked",
            "succeeded",
        }:
            prior[(identity.entry, identity.cid, identity.execution_id)] = {
                "source_digest": work.source_digest,
                "disposition": disposition,
            }
    return prior


def _has_complete_same_run_artifacts(
    history: PreparationHistory, identity: ExecutionRef, work: CommandWork
) -> bool:
    """Require one command and its whole comparison set from the same saved run."""

    origin = history.command_origins.get(identity)
    if origin is None:
        return False
    for output, _kind in work.execution.recipe.outputs:
        prior = history.artifacts.get(ArtifactRef(identity.entry, output))
        if (
            prior is None
            or prior[0].producer != identity
            or prior[1].origin_run_id != origin
        ):
            return False
    return True


def _currentness_by_execution(
    evaluation: EvaluationResult,
    entries: Mapping[str, _EntryState],
    project_root: Path,
) -> Mapping[ExecutionKey, tuple[ProducerCurrentness, ...]]:
    """Resolve shared evaluator conclusions to current execution units."""

    invocations = {
        invocation.identity: invocation for invocation in evaluation.context.invocations
    }
    required: dict[ExecutionKey, dict[str, ProducerCurrentness]] = defaultdict(dict)
    for conclusion in evaluation.context.currentness:
        producer = conclusion.anchor.producer_identity
        invocation = invocations.get(producer) if producer is not None else None
        if invocation is None:
            continue
        entry = entries.get(invocation.entry)
        if entry is None:
            continue
        association = associate_execution(
            entry.pyrun,
            invocation,
            project_root=project_root,
        )
        if association is not None:
            key = (invocation.entry, association.cid, association.identity)
            required[key][canonical_record_digest(conclusion.as_dict())] = conclusion
    return {
        key: tuple(values[identity] for identity in sorted(values))
        for key, values in sorted(required.items())
    }


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

    for key, owner in _target_owners(state).items():
        if not owner.execution.auto_reproduce and not state.include_all:
            continue
        _trace_execution(
            owner,
            state,
            depth=0,
            trace_inputs=True,
        )


def _target_owners(state: _PlanningState) -> dict[ExecutionKey, _Owner]:
    """Return one representative owner for every command in the exact target."""

    return {
        key: owner
        for key, owner in state.command_owners.items()
        if key[0] in state.selected_entries
    }


def _require_selection_policy(selection_policy: SelectionPolicy) -> None:
    if selection_policy not in {
        INCREMENTAL_SELECTION,
        RECHECK_SELECTION,
    }:
        raise ActionError(
            "reproduction.selection.invalid",
            f"unsupported reproduction selection policy: {selection_policy}",
        )


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


def _command_owner_index(
    entries: Mapping[str, _EntryState], project_root: Path
) -> dict[ExecutionKey, _Owner]:
    """Index loaded execution payloads without assigning graph ownership."""

    commands: dict[ExecutionKey, _Owner] = {}
    for owners in _owner_index(entries, project_root).values():
        for owner in owners:
            commands.setdefault(owner.key, owner)
    return dict(sorted(commands.items()))


def _graph_owner_index(
    entries: Mapping[str, _EntryState],
    project_root: Path,
    graph: ResearchGraph,
) -> dict[str, tuple[_Owner, ...]]:
    """Project established material ownership from the shared research graph."""

    payloads = _graph_execution_payloads(entries, project_root)
    nodes = {node.node_id: node for node in graph.nodes}
    execution_by_command = _graph_command_bindings(graph)
    projection = _GraphOwnerProjection(
        payloads,
        execution_by_command,
        nodes,
    )
    for edge in graph.edges:
        if edge.kind is EdgeKind.PRODUCTION:
            projection.retain(edge.source, edge.target)
    for ambiguity in graph.ambiguities:
        if ambiguity.kind is AmbiguityKind.REJECTED_COMMAND:
            for command_id in ambiguity.candidates:
                projection.retain(command_id, ambiguity.subject)
    return {
        target: tuple(
            sorted(
                owners.values(),
                key=lambda item: (
                    item.entry.context.id,
                    item.execution_id,
                    item.output,
                ),
            )
        )
        for target, owners in sorted(projection.found.items())
    }


def _graph_execution_payloads(
    entries: Mapping[str, _EntryState], project_root: Path
) -> dict[str, dict[str, _Owner]]:
    payloads: dict[str, dict[str, _Owner]] = defaultdict(dict)
    for owners in _owner_index(entries, project_root).values():
        for owner in owners:
            execution_node = ResearchNode(
                NodeKind.EXECUTION,
                f"{owner.cid}:{owner.execution_id}",
                owner.entry.context.id,
            ).node_id
            payloads[execution_node][owner.target] = owner
    return payloads


def _graph_command_bindings(graph: ResearchGraph) -> dict[str, set[str]]:
    execution_by_command: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.kind is EdgeKind.COMMAND_EXECUTION:
            execution_by_command[edge.source].add(edge.target)
    return execution_by_command


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
        _record_root_failure(
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
        _record_root_failure(
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
        _record_root_failure(
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
        state.requested_dependencies.add((producer.key, request.consumer.key))
    _trace_execution(producer, state, depth=depth)


def _apply_graph_dependencies(state: _PlanningState) -> None:
    """Project selected execution dependencies from the shared graph."""

    selected_by_node = {
        ResearchNode(
            NodeKind.EXECUTION,
            f"{key[1]}:{key[2]}",
            key[0],
        ).node_id: key
        for key in state.selected
    }
    for edge in state.graph.edges:
        if edge.kind is not EdgeKind.EXECUTION_DEPENDENCY:
            continue
        producer = selected_by_node.get(edge.source)
        consumer = selected_by_node.get(edge.target)
        if (
            producer is not None
            and consumer is not None
            and (producer, consumer) in state.requested_dependencies
        ):
            state.dependencies[consumer].add(producer)


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
        _retain_output_work(state, owner, output)
    if not trace_inputs:
        state.visited.add(key)
        _check_graph_bounds(state)
        return
    if key in state.visiting:
        index = state.visiting.index(key)
        members = tuple(sorted(state.visiting[index:]))
        state.cycle_members.update(members)
        problem = ReproductionProblem(
            SourceRef(state.log.summary.as_posix()),
            "dependency_cycle",
            ProblemStage.PREPARE,
            "Execution prerequisites form a cycle: "
            + ", ".join(_reference(member) for member in members),
            {"members": [ExecutionRef(*member).as_dict() for member in members]},
        )
        for member in members:
            _retain_preparation_problem(state, member, problem)
        return
    if key in state.visited:
        return
    state.visiting.append(key)
    for name in owner.execution.recipe.inputs:
        resource = (
            owner.entry.data.by_name.get(name) if owner.entry.data is not None else None
        )
        if resource is None:
            _record_root_failure(
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


def _retain_output_work(state: _PlanningState, owner: _Owner, output: str) -> None:
    """Freeze an actual reached output once, independently of its outcome."""

    identity = ArtifactRef(owner.entry.context.id, output)
    if identity in state.artifacts:
        return
    definition = _output_comparison(owner, output, state.project_root)
    state.artifacts[identity] = ArtifactWork(
        identity,
        ExecutionRef(*owner.key),
        _output_target(owner, output, state.project_root),
        dict(owner.execution.observed.outputs)[output],
        output=output,
        definition_identity=definition.identity if definition is not None else None,
        evidence_records=(
            tuple(record.as_dict() for record in definition.records)
            if definition is not None
            else ()
        ),
    )


def _record_execution_materials(owner: _Owner, state: _PlanningState) -> None:
    if owner.key in state.recorded_execution_materials:
        return
    state.recorded_execution_materials.add(owner.key)
    execution = owner.execution
    script = script_target_path(
        execution.recipe.script,
        entry_root=owner.entry.context.root,
        project_root=state.project_root,
    )
    failures: list[tuple[str, str]] = []
    source_failure = _record_effective_code(state, owner, script)
    if source_failure is not None:
        failures.append((source_failure.code, source_failure.explanation))
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
        baseline_problem = _baseline_problem(
            Path(target), kind, fingerprint, ArtifactRef(owner.entry.context.id, output)
        )
        if baseline_problem is None:
            _retain_material(
                state,
                ("baseline", target),
                _material(target, "comparison_baseline", kind, fingerprint),
                owner=owner.key,
            )
        else:
            _retain_preparation_problem(state, owner.key, baseline_problem)
            failures.append((baseline_problem.code, baseline_problem.explanation))
    if failures:
        state.blocked.add(owner.key)


def _record_effective_code(
    state: _PlanningState, owner: _Owner, script: Path
) -> ReproductionProblem | None:
    """Compare one saved/current effective-code fingerprint with useful diagnosis."""

    identity = script.resolve().as_posix()
    recorded = owner.execution.observed.effective_code
    environment_changes_imports = "PYTHONPATH" in dict(
        owner.execution.recipe.environment
    )
    python_context = PythonExecutionContext.for_research_script(
        script,
        entry_root=owner.entry.context.root,
        log_root=owner.entry.context.log.root,
        project_root=state.project_root,
    )
    cache_key = (
        script.resolve(),
        environment_changes_imports,
        python_context.import_roots,
    )
    observed = state.effective_code_observations.get(cache_key)
    if observed is None:
        if environment_changes_imports:
            observed = EffectiveCodeAnalysis(
                None,
                (_unsupported_environment_location(script, state.project_root),),
            )
        else:
            try:
                observed = analyze_effective_code(
                    script,
                    project_root=state.project_root,
                    import_roots=python_context.import_roots,
                )
            except EffectiveCodeError as error:
                observed = error
        state.effective_code_observations[cache_key] = observed
    try:
        script_fingerprint = observe_script_source(script)
    except (OSError, ValueError) as error:
        problem = _effective_code_problem(
            identity,
            "effective_code_unavailable",
            f"Current top-level script cannot be fingerprinted: {error}",
            {
                "availability": "script-unavailable",
                "error": str(error),
                "expected": recorded.as_dict() if recorded is not None else None,
            },
            locations=(SourceLocation(identity),),
        )
        _retain_preparation_problem(state, owner.key, problem)
        return problem
    if isinstance(observed, EffectiveCodeError):
        location = (
            SourceLocation(observed.path, observed.line or None)
            if observed.path is not None
            else SourceLocation(identity)
        )
        problem = _effective_code_problem(
            identity,
            "effective_code_unavailable",
            "Effective-code analysis failed; reproduction is selected on every "
            "incremental plan because currentness cannot be established: "
            f"{observed}",
            {
                "availability": "analysis-failed",
                "error_code": observed.code,
                "error": observed.detail,
                "expected": recorded.as_dict() if recorded is not None else None,
            },
            locations=(location,),
        )
        _retain_preparation_problem(state, owner.key, problem)
        state.accepted_sources[owner.key] = AcceptedSource(script_fingerprint, None)
        state.source_selected.add(owner.key)
        state.unverifiable_sources.add(owner.key)
        return None
    if observed.fingerprint is None:
        problem = _effective_code_problem(
            identity,
            "effective_code_unavailable",
            "Current effective code cannot be fingerprinted; reproduction is "
            "selected on every incremental plan because currentness cannot be "
            "established.",
            {
                "availability": "unsupported",
                "expected": recorded.as_dict() if recorded is not None else None,
                "unsupported": [
                    {
                        "construct": item.construct,
                        "detail": item.detail,
                        "line": item.line,
                        "path": item.path,
                    }
                    for item in observed.unsupported
                ],
                "unsupported_truncated": observed.unsupported_truncated,
            },
            locations=tuple(
                SourceLocation(item.path, item.line or None)
                for item in observed.unsupported
            ),
        )
        _retain_preparation_problem(state, owner.key, problem)
        state.accepted_sources[owner.key] = AcceptedSource(script_fingerprint, None)
        state.source_selected.add(owner.key)
        state.unverifiable_sources.add(owner.key)
        return None
    accepted = Fingerprint(
        observed.fingerprint.algorithm,
        digest=observed.fingerprint.digest,
    )
    state.accepted_sources[owner.key] = AcceptedSource(script_fingerprint, accepted)
    if recorded is None:
        problem = _effective_code_problem(
            identity,
            "effective_code_unavailable",
            "No saved effective-code fingerprint is available.",
            {
                "availability": "missing-saved-fingerprint",
                "expected": None,
                "actual": accepted.as_dict(),
            },
            locations=(SourceLocation(identity),),
        )
        _retain_preparation_problem(state, owner.key, problem)
        state.source_selected.add(owner.key)
        return None
    if accepted != recorded:
        problem = _effective_code_problem(
            identity,
            "effective_code_changed",
            "The project-local effective code no longer matches the saved execution.",
            {"expected": recorded.as_dict(), "actual": accepted.as_dict()},
            locations=(SourceLocation(identity),),
        )
        _retain_preparation_problem(state, owner.key, problem)
        state.source_selected.add(owner.key)
        return None
    return None


def _unsupported_environment_location(
    script: Path, project: Path
) -> UnsupportedLocation:
    try:
        path = script.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        path = script.resolve().as_posix()
    return UnsupportedLocation(
        path,
        0,
        "import_path_environment",
        "explicit PYTHONPATH changes project-local import resolution",
    )


def _effective_code_problem(
    identity: str,
    code: str,
    explanation: str,
    observed: Mapping[str, object],
    *,
    locations: tuple[SourceLocation, ...] = (),
) -> ReproductionProblem:
    return ReproductionProblem(
        SourceRef(identity),
        code,
        ProblemStage.PREPARE,
        explanation,
        {"path": identity, **observed},
        locations,
    )


def _retain_preparation_problem(
    state: _PlanningState, key: ExecutionKey | None, problem: ReproductionProblem
) -> None:
    """Retain one known observation; only its real source consumers reference it."""

    state.problems.setdefault(problem.problem_id, problem)
    if key is not None:
        references = state.command_problem_ids[key]
        if problem.problem_id not in references:
            references.append(problem.problem_id)
    if isinstance(problem.subject, ArtifactRef):
        artifact_references = state.artifact_problem_ids[problem.subject]
        if problem.problem_id not in artifact_references:
            artifact_references.append(problem.problem_id)


def _comparison_identity(owner: _Owner, output: str, project_root: Path) -> str | None:
    """Return one output's evidence-comparison definition identity, if any."""

    definition = _output_comparison(owner, output, project_root)
    return definition.identity if definition is not None else None


def _output_comparison(
    owner: _Owner, output: str, project_root: Path
) -> EvidenceComparisonDefinition | None:
    """Use the existing selector contract to freeze only this output's records."""

    data = owner.entry.data
    if data is None:
        return None
    target = _output_target(owner, output, project_root)
    resource = next(
        (item for item in data.inputs if item.canonical_target == target), None
    )
    if resource is None or resource.comparison is None or owner.entry.evidence is None:
        return None
    return evidence_comparison_definition(
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
    expected = (
        dict(request.consumer.execution.observed.inputs).get(request.resource.name)
        if request.consumer is not None
        else None
    )
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
            {
                "expected": expected.as_dict() if expected is not None else None,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return
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
            {"expected": expected.as_dict(), "actual": observed.as_dict()},
        )
        return
    _boundary(state, request, observed)


def _record_boundary_failure(
    state: _PlanningState,
    request: _BoundaryRequest,
    reason: str,
    details: tuple[str, ...],
    observed: Mapping[str, object],
) -> None:
    producers = _resource_owners(state.owners, request.resource.canonical_target)
    details += tuple(f"prerequisite={_reference(owner.key)}" for owner in producers)
    consumer = request.consumer
    problem = ReproductionProblem(
        SourceRef(request.resource.canonical_target)
        if consumer is not None
        else ArtifactRef(request.entry.context.id, request.artifact),
        reason,
        ProblemStage.PREPARE,
        f"{reason}: " + "; ".join(details),
        {
            "path": request.resource.canonical_target,
            "kind": request.resource.kind,
            "selection": request.resource.identity.as_dict(),
            **observed,
        },
    )
    _retain_preparation_problem(
        state, consumer.key if consumer is not None else None, problem
    )
    if consumer is None:
        state.artifacts.setdefault(
            cast(ArtifactRef, problem.subject),
            ArtifactWork(
                cast(ArtifactRef, problem.subject),
                None,
                request.resource.canonical_target,
                None,
                boundary={"kind": request.kind, "name": request.resource.name},
            ),
        )
        return
    state.blocked.add(consumer.key)


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
    _retain_boundary_work(state, request, observed)
    _retain_material(
        state,
        ("boundary", resource.canonical_target),
        _material(resource.canonical_target, "boundary", resource.kind, observed),
        owner=request.consumer.key if request.consumer is not None else None,
    )
    if len(state.boundaries) > MAX_BOUNDARIES:
        raise ActionError("reproduction.plan.resource_limit", "boundary limit exceeded")


def _retain_boundary_work(
    state: _PlanningState, request: _BoundaryRequest, observed: Fingerprint
) -> None:
    """Retain counted policy/scope roots, not every verified command input."""

    if request.consumer is not None or request.kind == "origin":
        return
    entry, artifact, resource = request.entry, request.artifact, request.resource
    identity = ArtifactRef(entry.context.id, artifact)
    candidates = tuple(
        owner
        for owner in _resource_owners(state.owners, resource.canonical_target)
        if owner.entry.context.id in state.selected_entries
    )
    producer = candidates[0] if len(candidates) == 1 else None
    state.artifacts.setdefault(
        identity,
        ArtifactWork(
            identity,
            ExecutionRef(*producer.key) if producer is not None else None,
            resource.canonical_target,
            observed,
            output=producer.output if producer is not None else None,
            boundary=state.boundaries[(request.kind, entry.context.id, artifact)],
        ),
    )


def _record_root_failure(state: _PlanningState, failure: _Failure) -> None:
    """Retain a tracing failure at its owner, not at each downstream output."""

    key = (
        (failure.entry, failure.cid, failure.execution_id)
        if failure.cid is not None and failure.execution_id is not None
        else None
    )
    problem = ReproductionProblem(
        ExecutionRef(*key)
        if key is not None
        else ArtifactRef(failure.entry, failure.artifact),
        failure.reason,
        ProblemStage.PREPARE,
        f"{failure.reason}: {failure.artifact}"
        + (": " + "; ".join(failure.dependencies) if failure.dependencies else ""),
        {"dependencies": sorted(set(failure.dependencies))},
    )
    _retain_preparation_problem(state, key, problem)
    if isinstance(problem.subject, ArtifactRef):
        state.artifacts.setdefault(
            problem.subject,
            ArtifactWork(problem.subject, None, failure.artifact, None),
        )


def _apply_cycle_and_dependency_failures(state: _PlanningState) -> None:
    state.blocked.update(state.cycle_members)
    changed = True
    while changed:
        changed = False
        for key, dependencies in state.dependencies.items():
            if key not in state.blocked and dependencies & state.blocked:
                state.blocked.add(key)
                changed = True


def _apply_validation_admission(
    state: _PlanningState,
    evaluation: EvaluationResult,
) -> None:
    """Apply finding-owned per-execution admission from the live graph."""

    snapshot = evaluation.snapshot
    if snapshot is None:
        raise ActionError(
            "reproduction.validation.incomplete",
            "prepared evaluation has no completed snapshot",
        )
    selected = tuple(
        SelectedExecution(
            owner.entry.context.id,
            owner.cid,
            owner.execution_id,
            tuple(
                output_target_path(
                    output,
                    entry_root=owner.entry.context.root,
                    project_root=state.project_root,
                )
                .resolve()
                .as_posix()
                for output, _kind in owner.execution.recipe.outputs
            ),
        )
        for owner in _target_owners(state).values()
    )
    admission = evaluate_reproduction_admission(
        snapshot,
        evaluation.context.graph,
        selected,
    )
    if admission.global_blocking_finding_ids:
        raise ActionError(
            "reproduction.validation.scope_unresolved",
            "blocking validation findings have no safe execution scope: "
            + ", ".join(admission.global_blocking_finding_ids),
        )
    state.admission_decisions = {item.key: item for item in admission.executions}
    for key, decision in sorted(state.admission_decisions.items()):
        if decision.disposition == "excluded" and key in state.selected:
            _exclude_validation_execution(
                state,
                key,
                decision,
                tuple(
                    finding
                    for finding in snapshot.findings
                    if finding.finding_id in decision.blocking_finding_ids
                ),
            )


def _exclude_validation_execution(
    state: _PlanningState,
    key: ExecutionKey,
    decision: ExecutionAdmission,
    findings: tuple[Finding, ...],
) -> None:
    state.blocked.add(key)
    for finding in findings:
        problem = ReproductionProblem(
            SourceRef(state.log.summary.as_posix()),
            "validation_blocked",
            ProblemStage.PREPARE,
            f"Validation finding {finding.finding_id} excludes reproduction: "
            f"{finding.code}: {finding.subject}",
            {"finding": finding.as_dict()},
            finding.source_locations,
        )
        _retain_preparation_problem(state, key, problem)


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


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
        and not state.selected[key].execution.requires_reproduction
        and key not in state.currentness
        and key not in state.source_selected
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
        if key in state.unverifiable_sources
        or (
            (
                state.selected[key].execution.requires_reproduction
                or key in state.currentness
                or key in state.source_selected
            )
            and not _command_result_current(prior.get(key), state.command_digests[key])
        )
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
    dispositions: Collection[str] = ("failed", "blocked", "succeeded"),
) -> bool:
    """Return whether one prior terminal command result has the same closure."""

    return (
        result is not None
        and result.get("disposition") in dispositions
        and result.get("source_digest") == source_digest
    )


def _command_source_digest(state: _PlanningState, key: ExecutionKey) -> str:
    """Hash frozen source observations and every known preparation cause."""

    owner = state.selected[key]
    outputs = []
    for output, _kind in owner.execution.recipe.outputs:
        outputs.append(
            {
                "artifact": output,
                "comparison_definition": _comparison_identity(
                    owner, output, state.project_root
                ),
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
    accepted_source = state.accepted_sources.get(key)
    accepted_effective_code = (
        accepted_source.effective_code.as_dict()
        if accepted_source is not None and accepted_source.effective_code is not None
        else None
    )
    return canonical_record_digest(
        {
            "contract": "research-log-reproduction-command-source/3",
            "dependencies": [
                _reference(value)
                for value in sorted(state.dependencies.get(key, set()))
            ],
            "entry": owner.entry.context.id,
            "cid": owner.cid,
            "execution": canonical_execution_source_digest(owner.execution.as_dict()),
            "execution_id": owner.execution_id,
            "currentness": [
                conclusion.as_dict() for conclusion in state.currentness.get(key, ())
            ],
            "accepted_effective_code": accepted_effective_code,
            "materials": materials,
            "outputs": outputs,
            "preparation_problems": sorted(state.command_problem_ids.get(key, ())),
        }
    )


def _command_work(
    state: _PlanningState,
    prior_problems: Mapping[ExecutionRef, tuple[ReproductionProblem, ...]],
) -> tuple[CommandWork, ...]:
    """Build command facts from loaded recipes and the one shared selector.

    Previous diagnoses must be supported new-model observations supplied by
    preparation, never manufactured by decoding an old result projection.
    """

    work = []
    retained_owners = _prior_problem_owners(state)
    for key, owner in sorted(_target_owners(state).items()):
        identity = ExecutionRef(*key)
        selection = _work_selection(state, owner)
        references = tuple(state.command_problem_ids.get(key, ()))
        if key in retained_owners and selection in {
            WorkSelection.PREVIOUS_FAILURE,
            WorkSelection.PREVIOUS_BLOCK,
            WorkSelection.NOT_NEEDED,
        }:
            references = tuple(
                dict.fromkeys(
                    (
                        *references,
                        *(
                            problem.problem_id
                            for problem in prior_problems.get(identity, ())
                        ),
                    )
                )
            )
        work.append(
            CommandWork(
                identity,
                owner.execution,
                owner.entry.context.root.as_posix(),
                state.project_root.as_posix(),
                (
                    {
                        "inputs": [
                            _resolved_input_declaration(item)
                            for item in owner.entry.data.inputs
                        ],
                        "schema": DATA_SCHEMA,
                    }
                    if owner.entry.data is not None
                    else None
                ),
                selection,
                state.command_digests.get(key),
                tuple(
                    ExecutionRef(*dependency)
                    for dependency in sorted(state.dependencies.get(key, ()))
                ),
                references,
                state.accepted_sources.get(key),
            )
        )
    return tuple(work)


def _prior_problem_owners(state: _PlanningState) -> set[ExecutionKey]:
    """Retain previous blocks through existing dependencies, at actual owners.

    A currently unneeded prerequisite may still own a previous consumer's
    diagnosis. Unrelated previous observations are not preparation causes.
    """

    owners: set[ExecutionKey] = set()
    pending: list[ExecutionKey] = []
    for key, selection in state.command_selections.items():
        if selection == "unchanged":
            owners.add(key)
            if state.prior_command_dispositions[key] == "blocked":
                pending.extend(state.dependencies.get(key, ()))
    while pending:
        key = pending.pop()
        if key not in owners:
            owners.add(key)
            pending.extend(state.dependencies.get(key, ()))
    return owners


def _work_selection(state: _PlanningState, owner: _Owner) -> WorkSelection:
    selection = state.command_selections.get(owner.key)
    if selection is None:
        return (
            WorkSelection.SKIPPED_BY_POLICY
            if not owner.execution.auto_reproduce and not state.include_all
            else WorkSelection.NOT_NEEDED
        )
    if selection == "unchanged":
        return (
            WorkSelection.PREVIOUS_FAILURE
            if state.prior_command_dispositions[owner.key] == "failed"
            else WorkSelection.PREVIOUS_BLOCK
            if state.prior_command_dispositions[owner.key] == "blocked"
            else WorkSelection.NOT_NEEDED
        )
    return (
        WorkSelection.SKIPPED_BY_POLICY
        if selection == "policy"
        else WorkSelection(selection)
    )


def _canonical_plan(
    state: _PlanningState,
    ordered: tuple[ExecutionKey, ...],
    prepared: PreparedReproductionContext,
    entry: EntryContext | None,
    history: PreparationHistory,
) -> AcceptedWorkPlan:
    """Cross the immutable plan/14 boundary directly from prepared facts.

    This builder performs no research-file reads, prior-result translation or
    publication. The selector and graph already established work and scope.
    """

    commands = _command_work(state, history.problems)
    artifacts = _artifact_work(state)
    reused = _reusable_artifact_results(commands, artifacts, history)
    comparisons = _project_comparisons(state, set(state.selected) - state.blocked)
    snapshot = prepared.evaluation.snapshot
    assert snapshot is not None
    return AcceptedWorkPlan(
        state.log.summary.as_posix(),
        RunTarget("entry", entry.id) if entry is not None else RunTarget(),
        RunSettings(
            state.include_all,
            state.selection_policy == RECHECK_SELECTION,
            state.jobs,
            state.execution_timeout_seconds,
        ),
        _admission_packet(state, prepared),
        commands,
        artifacts,
        _accepted_problems(state, commands, history, reused),
        _retained_materials(state),
        tuple(_project_evidence_only_context(state, comparisons)),
        tuple(
            {
                "identity": ExecutionRef(*key).as_dict(),
                "order": number,
                **_execution_claims(state, state.selected[key]),
            }
            for number, key in enumerate(ordered, 1)
        ),
        reused,
    )


def _reusable_artifact_results(
    commands: tuple[CommandWork, ...],
    artifacts: tuple[ArtifactWork, ...],
    history: PreparationHistory,
) -> tuple[ArtifactResult, ...]:
    """Freeze only completed comparisons with the exact accepted owner/baseline."""

    selected = {
        work.identity
        for work in commands
        if work.selection in {WorkSelection.RUN, WorkSelection.BLOCKED}
    }
    return tuple(
        result
        for work in artifacts
        if work.producer not in selected
        and (prior := history.artifacts.get(work.identity)) is not None
        and _comparison_reusable(work, *prior)
        for result in (prior[1],)
    )


def _comparison_reusable(
    work: ArtifactWork, prior: ArtifactWork, result: ArtifactResult
) -> bool:
    return (
        (
            result.outcome in {ArtifactOutcome.MATCHED, ArtifactOutcome.NOT_MATCHED}
            or result.outcome is ArtifactOutcome.NOT_COMPARED
            and result.not_compared_reason is NotComparedReason.COMPARISON_FAILED
        )
        and prior.producer == work.producer
        and prior.output == work.output
        and prior.retained_path == work.retained_path
        and prior.baseline == work.baseline == result.expected
        and prior.definition_identity
        == work.definition_identity
        == result.definition_identity
    )


def _accepted_problems(
    state: _PlanningState,
    commands: tuple[CommandWork, ...],
    history: PreparationHistory,
    reused: tuple[ArtifactResult, ...],
) -> tuple[ReproductionProblem, ...]:
    """Add frozen prior diagnoses without changing current-source preparation."""

    problems = dict(state.problems)
    for command in commands:
        for problem in history.problems.get(command.identity, ()):
            if problem.problem_id in command.problem_ids:
                problems.setdefault(problem.problem_id, problem)
    for result in reused:
        for problem_id in result.problem_ids:
            problems.setdefault(problem_id, history.artifact_problems[problem_id])
    return tuple(problems.values())


def _retained_materials(state: _PlanningState) -> tuple[Mapping[str, object], ...]:
    runnable = set(state.selected) - state.blocked
    return tuple(
        sorted(
            (
                value
                for key, value in state.materials.items()
                if None in state.material_owners[key]
                or bool(state.material_owners[key] & runnable)
            ),
            key=lambda value: (str(value["role"]), str(value["identity"])),
        )
    )


def _admission_packet(
    state: _PlanningState, prepared: PreparedReproductionContext
) -> dict[str, object]:
    snapshot = prepared.evaluation.snapshot
    assert snapshot is not None
    return {
        "evaluated_at": snapshot.finished_at,
        "executions": [
            state.admission_decisions[key].as_dict()
            for key in sorted(state.admission_decisions)
        ],
        "rules_version": snapshot.rules_version,
        "schema": "research-log-reproduction-admission/1",
        "validation_snapshot_id": snapshot.internal_snapshot_id,
    }


def _artifact_work(state: _PlanningState) -> tuple[ArtifactWork, ...]:
    """Freeze reached artifact facts with only their owned preparation causes."""

    return tuple(
        replace(
            work,
            problem_ids=tuple(state.artifact_problem_ids.get(identity, ())),
        )
        for identity, work in sorted(state.artifacts.items())
    )


def _project_comparisons(
    state: _PlanningState, runnable: Collection[ExecutionKey]
) -> list[dict[str, object]]:
    """Project exactly the runnable frozen comparison inventory."""

    return [
        {
            "entry": work.producer.entry,
            "cid": work.producer.cid,
            "execution_id": work.producer.execution_id,
            "output": work.output,
            "evidence_records": [
                plain_json(record) for record in work.evidence_records
            ],
            "definition_identity": work.definition_identity,
        }
        for work in sorted(
            state.artifacts.values(),
            key=lambda work: (
                (work.producer.entry, work.producer.cid, work.producer.execution_id)
                if work.producer is not None
                else ("", "", ""),
                work.output or "",
            ),
        )
        if work.producer is not None
        and (work.producer.entry, work.producer.cid, work.producer.execution_id)
        in runnable
        and work.definition_identity is not None
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
        value["reproduction_comparison"] = resource.comparison.as_dict()
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


def _observation_resource(
    name: str, kind: str, location: str, fingerprint: Fingerprint
) -> InputResource:
    """Adapt an observed fingerprint for read-only material comparison."""

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


def _baseline_problem(
    path: Path,
    kind: str,
    expected: Fingerprint,
    artifact: ArtifactRef,
) -> ReproductionProblem | None:
    identity = path.resolve().as_posix()
    resource = _observation_resource("planning-material", kind, identity, expected)
    try:
        observed = observe_fingerprint(resource).fingerprint
    except (OSError, ValueError) as error:
        return ReproductionProblem(
            artifact,
            "baseline_unavailable",
            ProblemStage.PREPARE,
            f"comparison_baseline:{identity}:{error}",
            {
                "path": identity,
                "expected": expected.as_dict(),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
    if observed.as_dict() == expected.as_dict():
        return None
    return ReproductionProblem(
        artifact,
        "baseline_changed",
        ProblemStage.PREPARE,
        f"comparison_baseline:{identity}:expected={expected.content_identity}:"
        f"observed={observed.content_identity}",
        {
            "path": identity,
            "expected": expected.as_dict(),
            "actual": observed.as_dict(),
        },
    )


def _check_graph_bounds(state: _PlanningState) -> None:
    edges = sum(len(value) for value in state.dependencies.values())
    nodes = len(state.selected) + len(state.artifacts) + len(state.boundaries)
    if (
        len(state.selected) > MAX_REACHABLE_EXECUTIONS
        or len(state.artifacts) > MAX_ARTIFACT_CASES
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


def _entry_order(value: str) -> int:
    return int(value[1:]) if value.startswith("e") and value[1:].isdigit() else 2**31


def _reference(key: ExecutionKey) -> str:
    """Return an unambiguous run-local dependency reference."""

    return f"{key[0]}:{key[1]}:{key[2]}"
