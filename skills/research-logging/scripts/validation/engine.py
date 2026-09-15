"""Integrated mechanical-validation engine for active evidence records."""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, NoReturn, Sequence, cast

from research_log_data import (
    DataContractError,
    DataDeclarationConflict,
    DataFile,
    Fingerprint,
    FingerprintObservation,
    InputResource,
    find_log_consistency_conflicts,
    input_token_parts,
    load_data_file,
    observe_fingerprint,
    resolve_input_token,
)

from .command_diagnostics import RejectedProducerIndex
from .commands import (
    MAX_INVOCATIONS_PER_LOG,
    CommandContext,
    CommandDeclaration,
    CommandDeclarationContext,
    CommandDeclarationResult,
    CommandDiscoveryFailure,
    DiscoveryResult,
    Invocation,
    ScriptObservation,
    discover_commands,
    index_commands,
    observe_commands,
    order_invocations,
    output_arguments,
    validate_command_structure,
)
from .domain import (
    AdmissionOwner,
    CheckDiagnostic,
    CheckOutcome,
    FailureOperation,
    Finding,
    GraphReference,
    IssueContext,
    RepairKey,
    RepairKeyKind,
    RuleArea,
    RuleCheck,
    SourceLocation,
    TargetKind,
    ValidationAttempt,
    ValidationSnapshot,
    ValidationTarget,
)
from .entry_materials import (
    EntryMaterialPathError,
    validate_entry_path_symlinks,
    validate_local_path_symlinks,
)
from .errors import MechanicalContractError
from .evidence import (
    MAX_PRESENTATIONS_PER_LOG,
    MAX_RECORDS_PER_LOG,
    MAX_SUMMARY_REFERENCES_PER_LOG,
    SECTION_CLASSIFIER_VERSION,
    CanonicalPresentation,
    EvidenceFile,
    EvidenceSource,
    PresentationRecord,
    PresentedItem,
    SummaryReference,
    associate_presentations,
    index_entry_documents,
    index_entry_presentation_candidates,
    index_entry_presentations,
    index_entry_section_issues,
    index_summary_references,
    index_summary_statistic_candidates,
    load_evidence_file,
    require_markdown_definition,
    resolve_summary_references,
)
from .evidence_comparison import (
    EvidenceComparisonError,
    evidence_comparison_definition,
    validate_reproduction_tolerances,
    validate_tolerant_selection,
)
from .filesystem import BoundedTraversalError, bounded_descendants
from .fingerprint_cache import FingerprintCache, FingerprintCacheError, project_root
from .json_codec import canonical_json
from .locator import (
    LOCATOR_EVALUATOR_VERSION,
    SourceIdentityObservation,
    evaluate_observed_locator,
    load_source,
    observe_source_identity,
    parse_locator,
    require_source_reader,
    require_source_unchanged,
)
from .material_graph import (
    EvidenceConnection,
    MaterialClassification,
    MaterialClassificationError,
    MaterialClassificationRequest,
    classify_research_graph_materials,
)
from .mechanical_values import SelectionResult
from .output_bindings import OutputBinding, OutputBindingError, project_output_bindings
from .output_support import (
    ResolvedCodeSupport,
    declared_output_resource,
    execution_output_support_dict,
    output_producer_mismatches,
    output_support_matches_invocation,
    require_current_execution_output,
    require_current_output_support,
    resolve_code_support,
    resolve_execution_code,
    resolve_output_support,
)
from .presentation import (
    artifact_evidence_dependencies,
    require_artifact_baseline_form,
    require_artifact_fingerprint,
    require_artifact_source_association,
)
from .provenance import (
    CompleteProvenanceContext,
    ProducerCurrentness,
    ProducerIndex,
    ProvenanceAnchor,
    ProvenanceFinding,
    ProvenanceResult,
    build_producer_index,
    evaluate_complete_provenance,
    producer_output_subject,
    require_origin_boundary,
)
from .pyrun_outputs import (
    PROJECT_OUTPUT_PREFIX,
    PyrunOutputsFile,
    empty_pyrun_outputs,
    load_pyrun_outputs,
    output_target_path,
    portable_output_path,
)
from .pyrun_state import (
    PYRUN_FILENAME,
    CommandComparison,
    ExecutionChange,
    OutputOwnerIndex,
    PyrunExecution,
    PyrunFile,
    associate_exact_execution,
    associate_execution,
    compare_command,
    execution_output_owners,
    load_pyrun_state,
    resolve_execution_output,
)
from .research_graph import (
    EdgeKind,
    EvaluationGraphInputs,
    NodeKind,
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBounds,
    ResearchNode,
    build_evaluation_graph,
)
from .retention import RetentionFile, load_retention_file
from .selection_codec import encode_selection
from .transformation import (
    TransformationResult,
    compare_presentation,
    evaluate_transformation,
)
from .validation_cache import ValidationCache

RULES_VERSION = "research-log-mechanical/markdown-evidence-11"
ENTRY_ID_RE = re.compile(r"e[0-9]+[a-z]?\Z", re.IGNORECASE)
MAX_ENTRY_SURFACE_PATHS = 1_000_000


# Evaluation contracts and mutable scan state.


class EngineV2Error(MechanicalContractError):
    """One precise integration-level mechanical failure."""


@dataclass(frozen=True)
class _Entry:
    id: str
    document: Path
    root: Path
    evidence_file: EvidenceFile | None
    data_file: DataFile | None
    retention_file: RetentionFile | None
    evidence_failure: RuleCheck | None = None
    data_failure: RuleCheck | None = None


@dataclass(frozen=True)
class _EntrySurface:
    evidence_file: EvidenceFile | None
    evidence_failure: RuleCheck | None
    data_file: DataFile | None
    data_failure: RuleCheck | None
    retention_file: RetentionFile | None


@dataclass(frozen=True)
class _EntryDeclaration:
    """One index-only physical entry surface used by scoped evaluation."""

    stable_id: str
    root: Path
    documents: tuple[Path, ...]
    data: DataFile | None
    commands: tuple[CommandDeclarationResult, ...]


CommandFrontier = tuple[int, int, int]


@dataclass(frozen=True)
class _DeclarationCandidate:
    """One output owner at its command-level source position."""

    entry_id: str
    path: str
    kind: str
    frontier: CommandFrontier


@dataclass(frozen=True)
class _LogDeclarationIndex:
    """Complete declaration namespace; never a projection of observations."""

    log_root: Path
    entries: tuple[_EntryDeclaration, ...]
    document_order: tuple[Path, ...]
    producer_candidates: Mapping[str, tuple[_DeclarationCandidate, ...]]
    rejected_candidates: Mapping[str, tuple[_DeclarationCandidate, ...]]
    data_conflicts: tuple[DataDeclarationConflict, ...]


@dataclass(frozen=True)
class _ResolvedSource:
    path: Path
    origin: bool
    input_name: str
    resource: InputResource


@dataclass(frozen=True)
class _FailureSpec:
    code: str
    subject: str
    observed: Mapping[str, object]
    rule: str
    dependency: str | None = None
    status: CheckOutcome = CheckOutcome.FINDING
    issue_context: IssueContext | None = None
    failure_operation: FailureOperation | None = None


@dataclass(frozen=True)
class _PreparedProvenanceFinding:
    """One finding with scan-local ordering and blocker work already resolved."""

    finding: ProvenanceFinding
    canonical: str
    blockers: tuple[str, ...]


_REPRODUCE_CURRENTNESS_CODES = frozenset(
    {
        "provenance.output.reproduction_required",
        "provenance.output.signature_mismatch",
    }
)


@dataclass(frozen=True)
class _OutputSupportConclusion:
    """One reusable output-support result and Reproduce-owned currentness."""

    support: Mapping[str, object] | None = None
    failure: ProvenanceFinding | None = None
    currentness: ProducerCurrentness | None = None


@dataclass
class _RecordOutcome:
    entry: str
    record: PresentationRecord
    item: PresentedItem
    materials: tuple[_ResolvedSource, ...]
    evidence_check: RuleCheck
    provenance_check: RuleCheck
    canonical: CanonicalPresentation | None
    dependencies: tuple[str, ...]


@dataclass
class _ScanState:
    summary: Path
    log_root: Path
    project_root: Path
    fingerprint_cache: FingerprintCache | None = None
    validation_cache: ValidationCache | None = None
    checks: list[RuleCheck] = field(default_factory=list)
    entries: list[_Entry] = field(default_factory=list)
    declared_entries: tuple[str, ...] = ()
    verified_inputs: list[dict[str, str]] = field(default_factory=list)
    invocations: tuple[Invocation, ...] = ()
    producer_index: ProducerIndex | None = None
    rejected_producers: RejectedProducerIndex = field(
        default_factory=RejectedProducerIndex
    )
    rejected_invocations: list[Invocation] = field(default_factory=list)
    complete_provenance_context: CompleteProvenanceContext | None = None
    command_candidate_dependencies: dict[str, set[str]] = field(default_factory=dict)
    command_blocker_candidates: (
        tuple[tuple[Path, bool, tuple[str, ...]], ...] | None
    ) = None
    command_blockers_by_subject: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    command_failure_owners: dict[str, set[str]] = field(default_factory=dict)
    records: list[_RecordOutcome] = field(default_factory=list)
    graph: ResearchGraph | None = None
    graph_analysis: MaterialClassification | None = None
    graph_nodes_by_id: dict[str, ResearchNode] = field(default_factory=dict)
    graph_output_producers: dict[str, tuple[ResearchNode, ...]] = field(
        default_factory=dict
    )
    output_files: dict[str, PyrunOutputsFile] = field(default_factory=dict)
    execution_states: dict[str, PyrunFile] = field(default_factory=dict)
    execution_output_owners: dict[str, OutputOwnerIndex] = field(default_factory=dict)
    output_record_errors: dict[str, MechanicalContractError] = field(
        default_factory=dict
    )
    output_record_error_checks: dict[str, RuleCheck] = field(default_factory=dict)
    missing_output_paths: set[str] = field(default_factory=set)
    provenance_observations: dict[str, tuple[str, Fingerprint]] = field(
        default_factory=dict
    )
    provenance_results: dict[tuple[str, int | None], ProvenanceResult] = field(
        default_factory=dict
    )
    restricted_provenance_contexts: dict[int, CompleteProvenanceContext] = field(
        default_factory=dict
    )
    provenance_checks: dict[str, RuleCheck] = field(default_factory=dict)
    output_support_conclusions: dict[tuple[str, str], _OutputSupportConclusion] = field(
        default_factory=dict
    )
    output_file_observations: dict[str, str] = field(default_factory=dict)
    selection_cache: dict[tuple[str, str], SelectionResult] = field(
        default_factory=dict
    )
    source_cache: dict[str, SourceIdentityObservation] = field(default_factory=dict)
    script_cache: dict[str, ScriptObservation] = field(default_factory=dict)
    input_observations: dict[str, FingerprintObservation] = field(default_factory=dict)
    input_resources: dict[str, InputResource] = field(default_factory=dict)
    input_prerequisite_checks: dict[str, list[RuleCheck]] = field(default_factory=dict)
    input_prerequisite_files: dict[str, list[RuleCheck]] = field(default_factory=dict)
    input_prerequisite_directories: dict[str, list[RuleCheck]] = field(
        default_factory=dict
    )
    graph_failure_owners: dict[str, set[str]] = field(default_factory=dict)
    logical_material_roots: tuple[tuple[Path, str, str], ...] | None = None
    owner_surface_prerequisite_checks: dict[str, tuple[RuleCheck, ...]] = field(
        default_factory=dict
    )
    markdown_reads: int = 0
    presentation_count: int = 0
    source_evaluations: int = 0
    source_hashes_reused: int = 0
    source_opens: int = 0
    source_payload_reads: int = 0
    input_fingerprints_reused: int = 0
    provenance_traversals: int = 0
    provenance_traversals_reused: int = 0
    selection_serialized_bytes: int = 0
    selection_serialized_max_bytes: int = 0
    selection_serialized_by_profile: dict[str, dict[str, int]] = field(
        default_factory=dict
    )
    timings: dict[str, float] = field(default_factory=dict)
    text_cache: dict[Path, str] = field(default_factory=dict)
    document_failure_checks: dict[Path, RuleCheck] = field(default_factory=dict)
    entry_surface_failure_checks: dict[Path, RuleCheck] = field(default_factory=dict)


@dataclass
class _OrphanGroupingState:
    log_identity: str
    owner: str
    inventory: set[str]
    orphan_paths: Mapping[str, str]
    grouped: set[str]
    result: dict[str, Mapping[str, object]]


@dataclass(frozen=True)
class _UnmatchedOutputs:
    """Output-support paths absent from the current command graph."""

    paths: frozenset[str]
    directory_roots: frozenset[str]


# Top-level evaluation lifecycle.


@dataclass(frozen=True)
class FullEvaluationTarget:
    """Request the complete maintained-log conclusion."""


@dataclass(frozen=True)
class EntryEvaluationTarget:
    """Request one resolved stable physical entry."""

    entry_id: str
    entry_root: Path


EvaluationTarget = FullEvaluationTarget | EntryEvaluationTarget


@dataclass(frozen=True)
class EvaluationRequest:
    """Inputs for one direct non-publishing mechanical evaluation."""

    summary_path: Path
    target: EvaluationTarget = FullEvaluationTarget()
    fingerprint_cache: FingerprintCache | None = None
    validation_cache: ValidationCache | None = None
    generated_residue: tuple[str, ...] = ()
    graph_max_nodes: int = 1_000_000
    graph_max_edges: int = 4_000_000
    graph_max_ambiguities: int = 1_000_000


@dataclass(frozen=True)
class EvaluationContext:
    """The scanned context that explains an evaluation's coverage."""

    target: EvaluationTarget
    selected_documents: tuple[str, ...]
    dependency_entries: tuple[str, ...]
    invocations: tuple[Invocation, ...]
    registries: tuple[tuple[str, DataFile], ...]
    whole_log_conclusions: tuple[str, ...]
    materials: tuple["EvaluationEntryMaterial", ...]
    currentness: tuple[ProducerCurrentness, ...]
    graph: ResearchGraph


@dataclass(frozen=True)
class EvaluationEntryMaterial:
    """Already loaded entry material available to a prepared consumer.

    The evaluator, rather than a later planner, owns these observations.  A
    missing or malformed execution-state file is represented by ``pyrun``
    being ``None`` and its concrete loading failure, never by an invented empty
    state.
    """

    entry_id: str
    material_owner: str
    entry_root: Path
    document: Path
    data: DataFile | None
    evidence: EvidenceFile | None
    retention: RetentionFile | None
    pyrun: PyrunFile | None
    errors: tuple[MechanicalContractError, ...]


@dataclass(frozen=True)
class EvaluationResult:
    """One canonical evaluation attempt, its context, and completed snapshot."""

    context: EvaluationContext
    metrics: Mapping[str, object]
    attempt: ValidationAttempt
    snapshot: ValidationSnapshot


def _physical_entry_materials(entries: Sequence[_Entry]) -> tuple[_Entry, ...]:
    """Retain one material envelope for each physical entry root."""

    by_root: dict[Path, _Entry] = {}
    for entry in entries:
        by_root.setdefault(entry.root, entry)
    return tuple(by_root[root] for root in sorted(by_root))


def _evaluation_materials(
    state: _ScanState,
) -> tuple[EvaluationEntryMaterial, ...]:
    """Project already loaded entry surfaces for graph and consumer context."""

    return tuple(
        EvaluationEntryMaterial(
            entry_id=_stable_entry_id(entry.document),
            material_owner=_material_owner(entry, state),
            entry_root=entry.root,
            document=entry.document,
            data=entry.data_file,
            evidence=entry.evidence_file,
            retention=entry.retention_file,
            pyrun=state.execution_states.get(_material_owner(entry, state)),
            errors=tuple(
                error
                for owner, error in state.output_record_errors.items()
                if owner == _material_owner(entry, state)
            ),
        )
        for entry in _physical_entry_materials(state.entries)
    )


def _evaluation_currentness(state: _ScanState) -> tuple[ProducerCurrentness, ...]:
    """Expose each reached Reproduce-owned currentness conclusion once."""

    conclusions = {
        canonical_json(conclusion.currentness.as_dict()): conclusion.currentness
        for conclusion in state.output_support_conclusions.values()
        if conclusion.currentness is not None
    }
    return tuple(conclusions[key] for key in sorted(conclusions))


def _build_research_graph(
    state: _ScanState,
    request: EvaluationRequest,
) -> ResearchGraph:
    """Build the one graph used by validation conclusions and later consumers."""

    return build_evaluation_graph(
        EvaluationGraphInputs(
            entries=_evaluation_materials(state),
            invocations=state.invocations,
            evidence_connections=_graph_evidence_connections(state),
            code_inputs=_graph_code_inputs(state),
            execution_bindings=_graph_execution_bindings(state),
            rejected_commands=tuple(state.rejected_producers.commands.values()),
            supported_output_directories=_supported_output_directories(state),
            rejected_invocations=tuple(state.rejected_invocations),
        ),
        bounds=ResearchGraphBounds(
            request.graph_max_nodes,
            request.graph_max_edges,
            request.graph_max_ambiguities,
        ),
    )


def _ensure_research_graph(state: _ScanState, request: EvaluationRequest) -> None:
    """Complete graph ownership even when an earlier scan stage failed."""

    if state.graph is None:
        state.graph = _build_research_graph(state, request)


ENTRY_LIMITATIONS = (
    "summary_evidence",
    "summary_provenance",
    "unselected_entry_conformance",
    "complete_graph_output_reconciliation",
    "whole_log_orphan_detection",
    "whole_log_clearance",
)


def evaluate_mechanical(request: EvaluationRequest) -> EvaluationResult:
    """Evaluate directly; publication and locks remain controller-owned."""

    started_at = _utc_timestamp()
    scan, metrics = _scan(request)
    checks = cast(tuple[RuleCheck, ...], scan["checks"])
    target = request.target
    selected = tuple(scan["selected_documents"])
    scan_state = cast(_ScanState, scan["state"])
    # Current provenance traversal can introduce upstream entries while resolving
    # output support. Keep the selected physical documents explicit; all scanned
    # entries remain available as dependency context.
    dependencies = tuple(scan["dependency_entries"])
    materials = _evaluation_materials(scan_state)
    graph = scan_state.graph
    assert graph is not None
    context = EvaluationContext(
        target=target,
        selected_documents=selected,
        dependency_entries=dependencies,
        invocations=tuple(scan["invocations"]),
        registries=tuple(scan["registries"]),
        whole_log_conclusions=(
            () if isinstance(target, FullEvaluationTarget) else ENTRY_LIMITATIONS
        ),
        materials=materials,
        currentness=_evaluation_currentness(scan_state),
        graph=graph,
    )
    attempt = _attempt_from_checks(
        checks,
        str(scan["summary"]),
        target,
        source_identity=_evaluation_source_identity(scan_state),
        started_at=started_at,
        finished_at=_utc_timestamp(),
        metrics=metrics,
    )
    if graph.limit_observation is not None:
        raise ValueError(
            "validation research-graph capacity was exceeded: "
            + canonical_json(graph.limit_observation.as_dict())
        )
    _require_resolved_finding_context(attempt, graph)
    snapshot = ValidationSnapshot.from_attempt(
        attempt,
        repair_context=_repair_context(graph, attempt),
        report_context=_snapshot_report_context(
            Path(str(scan["summary"])), attempt.findings
        ),
    )
    return EvaluationResult(context, metrics, attempt, snapshot)


def _snapshot_report_context(
    summary: Path,
    findings: Sequence[Finding],
) -> Mapping[str, object]:
    from .snapshot_report import build_snapshot_report_context

    return build_snapshot_report_context(summary, findings)


def _require_resolved_finding_context(
    attempt: ValidationAttempt,
    graph: ResearchGraph,
) -> None:
    """Reject implementation defects that would persist dangling graph context."""

    known = {node.node_id for node in graph.nodes}
    missing = {
        reference.node_id
        for finding in attempt.findings
        for reference in finding.context_nodes
        if reference.node_id not in known
    }
    if missing:
        raise AssertionError(
            "finding context references unknown graph nodes: "
            + ", ".join(sorted(missing))
        )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _evaluation_source_identity(state: _ScanState) -> str:
    """Identify the exact already-observed source without another read."""

    projection = {
        "documents": [
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            for path, text in sorted(
                state.text_cache.items(),
                key=lambda item: item[0].as_posix(),
            )
        ],
        "inputs": [
            {
                "algorithm": observation.fingerprint.algorithm,
                "digest": observation.fingerprint.digest,
                "path": path,
            }
            for path, observation in sorted(state.input_observations.items())
        ],
        "provenance": [
            {"identity": identity, "path": value[0], "digest": value[1].digest}
            for identity, value in sorted(state.provenance_observations.items())
        ],
    }
    return hashlib.sha256(canonical_json(projection).encode()).hexdigest()


def _attempt_from_checks(  # noqa: PLR0913 -- explicit attempt identity is contract
    checks: Sequence[RuleCheck],
    summary: str,
    target: EvaluationTarget,
    *,
    source_identity: str,
    started_at: str,
    finished_at: str,
    metrics: Mapping[str, object],
) -> ValidationAttempt:
    """Build the canonical attempt directly from evaluator-owned checks."""

    canonical_target = (
        ValidationTarget(TargetKind.LOG, summary)
        if isinstance(target, FullEvaluationTarget)
        else ValidationTarget(TargetKind.ENTRY, summary, target.entry_id)
    )
    return ValidationAttempt.build(
        target=canonical_target,
        source_identity=source_identity,
        rules_version=RULES_VERSION,
        started_at=started_at,
        finished_at=finished_at,
        checks=_canonical_check_dependencies(checks),
        metrics=metrics,
    )


def _canonical_check_dependencies(
    checks: Sequence[RuleCheck],
) -> tuple[RuleCheck, ...]:
    """Keep only causal dependencies represented by checks in this attempt."""

    known = {check.check_id for check in checks}
    return tuple(
        RuleCheck(
            check.check_id,
            check.area,
            check.outcome,
            check.subject,
            tuple(item for item in check.dependencies if item in known),
            check.diagnostic,
            check.issue_context,
            check.dependency_evidence,
            check.rule,
            check.failure_operation,
        )
        for check in checks
    )


@dataclass
class _RepairContextCollector:
    """Collect one typed repair neighborhood without graph-wide chaining."""

    graph: ResearchGraph
    primary: set[str]
    nodes_by_id: dict[str, ResearchNode] = field(init=False)
    adjacent_edges: dict[str, list[ResearchEdge]] = field(init=False)
    ambiguities_by_node: dict[str, list[int]] = field(init=False)
    included: set[str] = field(init=False)
    relationships: set[ResearchEdge] = field(init=False)
    selected_ambiguities: set[int] = field(init=False)
    operational_frontier: list[str] = field(init=False)
    ambiguity_frontier: list[str] = field(init=False)
    expanded: set[str] = field(default_factory=set)
    ambiguity_seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.nodes_by_id = {node.node_id: node for node in self.graph.nodes}
        self.adjacent_edges = defaultdict(list)
        for edge in self.graph.edges:
            self.adjacent_edges[edge.source].append(edge)
            self.adjacent_edges[edge.target].append(edge)
        self.ambiguities_by_node = defaultdict(list)
        for index, item in enumerate(self.graph.ambiguities):
            self.ambiguities_by_node[item.subject].append(index)
            for candidate in item.candidates:
                self.ambiguities_by_node[candidate].append(index)
        self.included = set(self.primary)
        self.relationships = set()
        self.selected_ambiguities = set()
        self.operational_frontier = [
            node_id for node_id in sorted(self.primary) if self._is_operational(node_id)
        ]
        self.ambiguity_frontier = list(sorted(self.primary))

    def collect(self) -> tuple[set[str], set[ResearchEdge], set[int]]:
        """Return included nodes, established edges, and ambiguity indexes."""

        for node_id in sorted(self.primary):
            for edge in self.adjacent_edges[node_id]:
                self._include_edge(edge)
        while self.operational_frontier or self.ambiguity_frontier:
            self._drain_ambiguities()
            self._drain_operational_nodes()
        return self.included, self.relationships, self.selected_ambiguities

    def _is_operational(self, node_id: str) -> bool:
        return self.nodes_by_id[node_id].kind in {
            NodeKind.COMMAND,
            NodeKind.EXECUTION,
            NodeKind.COLLECTION,
        }

    def _admit(self, node_id: str) -> None:
        if node_id in self.included:
            return
        self.included.add(node_id)
        self.ambiguity_frontier.append(node_id)
        if self._is_operational(node_id):
            self.operational_frontier.append(node_id)

    def _include_edge(self, edge: ResearchEdge) -> None:
        self.relationships.add(edge)
        self._admit(edge.source)
        self._admit(edge.target)

    def _drain_ambiguities(self) -> None:
        while self.ambiguity_frontier:
            node_id = self.ambiguity_frontier.pop()
            if node_id in self.ambiguity_seen:
                continue
            self.ambiguity_seen.add(node_id)
            for index in self.ambiguities_by_node[node_id]:
                self._include_ambiguity(index)

    def _include_ambiguity(self, index: int) -> None:
        if index in self.selected_ambiguities:
            return
        self.selected_ambiguities.add(index)
        item = self.graph.ambiguities[index]
        self._admit(item.subject)
        for candidate in item.candidates:
            self._admit(candidate)

    def _drain_operational_nodes(self) -> None:
        while self.operational_frontier:
            node_id = self.operational_frontier.pop()
            if node_id in self.expanded:
                continue
            self.expanded.add(node_id)
            for edge in self.adjacent_edges[node_id]:
                self._include_edge(edge)


def _repair_context(
    graph: ResearchGraph,
    attempt: ValidationAttempt,
) -> Mapping[str, object]:
    """Project the bounded typed neighborhood needed to start finding repair."""

    primary = {
        reference.node_id
        for finding in attempt.findings
        for reference in finding.context_nodes
    }
    repair_identities = {
        key.identity for finding in attempt.findings for key in finding.repair_keys
    }
    primary.update(
        node.node_id for node in graph.nodes if node.identity in repair_identities
    )
    included, relationships, selected_ambiguities = _RepairContextCollector(
        graph,
        primary,
    ).collect()

    ambiguities = [
        graph.ambiguities[index].as_dict() for index in sorted(selected_ambiguities)
    ]
    nodes = [node.as_dict() for node in graph.nodes if node.node_id in included]
    value: dict[str, object] = {
        "ambiguities": ambiguities,
        "nodes": nodes,
        "relationships": [edge.as_dict() for edge in sorted(relationships)],
    }
    return value


def _scan(
    request: EvaluationRequest,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    started = time.perf_counter()
    summary = request.summary_path.resolve()
    state = _ScanState(
        summary,
        summary.with_suffix(""),
        project_root(summary),
        request.fingerprint_cache,
        request.validation_cache,
    )
    try:
        summary_text = _read_text(summary, state)
        phase = time.perf_counter()
        target = request.target
        document_roots = None
        declaration_index: _LogDeclarationIndex | None = None
        missing_entry_declaration = False
        if isinstance(target, EntryEvaluationTarget):
            document_roots = frozenset({target.entry_root.resolve()})
            # Validate that the producer namespace is structurally indexable
            # before any selected-entry current observation begins.
            declaration_index = _index_log(summary_text, state)
            missing_entry_declaration = not any(
                document.parent.resolve() == target.entry_root.resolve()
                for entry in declaration_index.entries
                for document in entry.documents
            )
            if missing_entry_declaration:
                state.checks.append(
                    _error_check(
                        f"entry:{target.entry_id}:declaration",
                        RuleArea.PROVENANCE,
                        EngineV2Error(
                            "association.declaration_missing",
                            target.entry_root.as_posix(),
                            {"entry": target.entry_id},
                            "Evidence File And Presentation Association",
                        ),
                        issue_context=IssueContext(
                            entry=target.entry_id,
                            source_locations=(
                                SourceLocation(target.entry_root.as_posix()),
                            ),
                            admission_owner=AdmissionOwner.ENTRY,
                        ),
                    )
                )
        state.entries = (
            []
            if missing_entry_declaration
            else _entries(summary_text, state, document_roots=document_roots)
        )
        record_count = sum(
            len(evidence_file.records)
            for evidence_file in _unique_evidence_files(state.entries)
        )
        if record_count > MAX_RECORDS_PER_LOG:
            _fail(
                "association.resource.too_large",
                str(state.log_root),
                {"records": record_count, "limit": MAX_RECORDS_PER_LOG},
            )
        state.timings["evidence_file_parsing_seconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        _prepare_command_context(state, summary_text, target, declaration_index)
        state.timings["command_inspection_seconds"] = time.perf_counter() - phase
    except MechanicalContractError as error:
        _record_log_scan_error(error, state)
    else:
        _evaluate_entries(
            state,
            selected_roots=(
                {request.target.entry_root.resolve()}
                if isinstance(request.target, EntryEvaluationTarget)
                else None
            ),
        )
        if isinstance(request.target, FullEvaluationTarget):
            try:
                _evaluate_summary(summary_text, state)
            except MechanicalContractError as error:
                state.checks.append(
                    _error_check(
                        "evidence:summary",
                        _error_scope(error, RuleArea.EVIDENCE),
                        error,
                        issue_context=_log_issue_context(state),
                    )
                )
        state.graph = _build_research_graph(state, request)
        # Graph reconciliation is a deliberately whole-log conclusion.  The
        # declaration inventory used above is not an observed graph and must
        # never make a scoped entry request perform whole-log orphan detection.
        if isinstance(request.target, FullEvaluationTarget):
            _compose_graph(state, request)
            _record_generated_residue(request.generated_residue, state)
        _verify_source_stability(state)
        _verify_provenance_stability(state)
    _ensure_research_graph(state, request)
    if not any(check.area is RuleArea.CONFORMANCE for check in state.checks):
        state.checks.append(_pass_check("conformance:log", RuleArea.CONFORMANCE))
    metrics = {
        "elapsed_seconds": time.perf_counter() - started,
        "graph_edges": len(state.graph.edges) if state.graph else 0,
        "graph_nodes": len(state.graph.nodes) if state.graph else 0,
        "invocations": len(state.invocations),
        "markdown_reads": state.markdown_reads,
        "script_hashes": len(state.script_cache),
        "directory_observations": sum(
            observation.fingerprint.algorithm == "directory-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "identity_file_observations": sum(
            observation.fingerprint.algorithm == "identity-files-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "identity_pattern_observations": sum(
            observation.fingerprint.algorithm == "identity-patterns-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "source_evaluations": state.source_evaluations,
        "source_reads": len(state.source_cache),
        "source_opens": state.source_opens,
        "source_payload_reads": state.source_payload_reads,
        "source_hashes_reused": state.source_hashes_reused,
        "selection_serialized_bytes": state.selection_serialized_bytes,
        "selection_serialized_max_bytes": state.selection_serialized_max_bytes,
        "selection_serialized_by_profile": state.selection_serialized_by_profile,
        "input_observations": len(state.input_observations),
        "input_fingerprints_reused": state.input_fingerprints_reused,
        "provenance_traversals": state.provenance_traversals,
        "provenance_traversals_reused": state.provenance_traversals_reused,
        **(
            state.fingerprint_cache.metrics.as_dict()
            if state.fingerprint_cache is not None
            else {}
        ),
        **(
            state.validation_cache.metrics.as_dict()
            if state.validation_cache is not None
            else {}
        ),
        **state.timings,
        **(state.graph_analysis.metrics if state.graph_analysis else {}),
    }
    return {
        "checks": tuple(state.checks),
        "entries": tuple(entry.id for entry in state.entries),
        "declared_entries": state.declared_entries,
        "verified_inputs": tuple(state.verified_inputs),
        "graph": state.graph,
        "invocations": state.invocations,
        "registries": tuple(
            (entry.id, entry.data_file)
            for entry in state.entries
            if entry.data_file is not None
        ),
        "entry_materials": tuple(state.entries),
        "state": state,
        "execution_states": dict(state.execution_states),
        "execution_state_errors": tuple(
            sorted(state.output_record_errors.items(), key=lambda item: item[0])
        ),
        "summary": summary.as_posix(),
        "selected_documents": _selected_documents(state, request.target),
        "dependency_entries": _dependency_entries(state, request.target),
    }, metrics


def _record_generated_residue(paths: Sequence[str], state: _ScanState) -> None:
    """Treat unsupported prior validator artifacts as ordinary orphan findings."""

    for relative in sorted(set(paths)):
        subject = (state.log_root / relative).as_posix()
        state.checks.append(
            _failure_check(
                f"orphan:generated-residue:{relative}",
                RuleArea.ORPHAN,
                _FailureSpec(
                    "orphan.generated.residue",
                    subject,
                    {"path": relative},
                    "Generated Validation Ownership",
                    issue_context=IssueContext(
                        source_locations=(SourceLocation(subject),),
                        repair_keys=(RepairKey(RepairKeyKind.SOURCE, subject),),
                        admission_owner=AdmissionOwner.LOG,
                    ),
                ),
            )
        )


def _closure_wanted(
    index: _LogDeclarationIndex,
    invocations: Sequence[Invocation],
    roots: Sequence[tuple[str, str | None]],
) -> list[tuple[str, CommandFrontier | None]]:
    """Combine ordered command consumers with orderless evidence/origin roots."""

    wanted: list[tuple[str, CommandFrontier | None]] = [
        (relationship.path, _invocation_frontier(index, invocation))
        for invocation in invocations
        for relationship in invocation.inputs
    ]
    wanted.extend((path, None) for path, _ in roots)
    return wanted


def _candidate_dependency_ids(
    index: _LogDeclarationIndex,
    invocations: Sequence[Invocation],
    materialized: set[CommandFrontier],
    roots: Sequence[tuple[str, str | None]] = (),
) -> frozenset[str]:
    """Return earlier unobserved owners whose declarations can reach a frontier.

    This is deliberately the declaration equivalent of ``ProducerIndex.lookup``:
    an output can only satisfy a command that occurs later in complete summary
    source order.  ``unknown`` is conservatively both a scalar and directory
    declaration until materialization determines its actual collection shape.
    """

    wanted = _closure_wanted(index, invocations, roots)
    indexed_owners = _indexed_candidate_owners(index, wanted)
    owners: set[str] = set()
    for entry in index.entries:
        if entry.stable_id not in indexed_owners:
            continue
        for source_document, document in zip(entry.documents, entry.commands):
            for declaration in document.declarations:
                frontier = _declaration_frontier(index, source_document, declaration)
                if frontier in materialized:
                    continue
                if _reached_declaration_admitted(
                    index,
                    entry,
                    source_document,
                    declaration,
                    wanted,
                ):
                    owners.add(entry.stable_id)
            if any(
                _rejected_failure_admitted(
                    failure, wanted, _rejected_frontier(index, source_document, failure)
                )
                and _rejected_frontier(index, source_document, failure)
                not in materialized
                for failure in document.failures
            ) or (
                _outputs_admitted(
                    document.rejected_outputs,
                    wanted,
                    _rejected_frontier(index, source_document, None),
                )
                and _rejected_frontier(index, source_document, None) not in materialized
            ):
                owners.add(entry.stable_id)
    return frozenset(owners)


def _indexed_candidate_owners(
    index: _LogDeclarationIndex,
    wanted: Sequence[tuple[str, CommandFrontier | None]],
) -> frozenset[str]:
    """Resolve exact and both nested-directory directions without observation."""

    owners: set[str] = set()
    for candidates in (index.producer_candidates, index.rejected_candidates):
        for output_owners in candidates.values():
            for candidate in output_owners:
                if _outputs_admitted(
                    ((candidate.path, candidate.kind),), wanted, candidate.frontier
                ):
                    owners.add(candidate.entry_id)
    return frozenset(owners)


def _prepare_command_context(
    state: _ScanState,
    summary_text: str,
    target: EvaluationTarget,
    declaration_index: _LogDeclarationIndex | None,
) -> None:
    indexed = _indexed_documents(declaration_index)
    if declaration_index is not None:
        _record_indexed_data_conflicts(declaration_index, state)
    state.invocations = _discover_invocations(state, indexed_documents=indexed)
    if declaration_index is not None:
        assert isinstance(target, EntryEvaluationTarget)
        state.invocations = _observe_producer_closure(
            state, summary_text, target, declaration_index, indexed
        )
        _record_indexed_data_conflicts(declaration_index, state)
    state.producer_index = build_producer_index(state.invocations)
    state.complete_provenance_context = CompleteProvenanceContext(
        state.producer_index,
        producer_validator=lambda invocation, output: _validate_output_support(
            invocation, output, state
        ),
        confirmed_record=lambda invocation, output: _has_structural_output_record(
            invocation, output, state
        ),
    )
    _load_output_support(state)


def _indexed_documents(
    index: _LogDeclarationIndex | None,
) -> Mapping[Path, CommandDeclarationResult] | None:
    if index is None:
        return None
    return {
        document: commands
        for entry in index.entries
        for document, commands in zip(entry.documents, entry.commands)
    }


def _observe_producer_closure(
    state: _ScanState,
    summary_text: str,
    target: EntryEvaluationTarget,
    index: _LogDeclarationIndex,
    indexed: Mapping[Path, CommandDeclarationResult] | None,
) -> tuple[Invocation, ...]:
    selected_documents = {entry.document.resolve() for entry in state.entries}
    materialized = {
        _declaration_frontier(index, document, declaration)
        for entry in index.entries
        for document, declarations in zip(entry.documents, entry.commands)
        if document.resolve() in selected_documents
        for declaration in declarations.declarations
    }
    materialized.update(
        _rejected_frontier(index, document, failure)
        for entry in index.entries
        for document, declarations in zip(entry.documents, entry.commands)
        if document.resolve() in selected_documents
        for failure in declarations.failures
    )
    materialized.update(
        _rejected_frontier(index, document, None)
        for entry in index.entries
        for document, declarations in zip(entry.documents, entry.commands)
        if document.resolve() in selected_documents
        if declarations.rejected_outputs
    )
    invocations = list(state.invocations)
    roots = _selected_material_roots(state, target)
    while dependencies := _candidate_dependency_ids(
        index, invocations, materialized, roots
    ):
        dependency_documents = _candidate_dependency_documents(
            index, invocations, roots, materialized
        )
        entries = _entries(
            summary_text,
            state,
            document_roots=frozenset(
                entry.root for entry in index.entries if entry.stable_id in dependencies
            ),
            document_paths=dependency_documents,
            declaration_only_surfaces=True,
        )
        state.entries.extend(entries)
        _record_indexed_data_conflicts(index, state)
        reached = _reached_command_declarations(
            index, dependency_documents, invocations, roots, materialized
        )
        invocations.extend(
            _discover_invocations(state, entries, indexed_documents=reached)
        )
        materialized.update(
            _declaration_frontier(index, document, declaration)
            for document, declarations in reached.items()
            for declaration in declarations.declarations
        )
        materialized.update(
            _rejected_frontier(index, document, failure)
            for document, declarations in reached.items()
            for failure in declarations.failures
        )
        materialized.update(
            _rejected_frontier(index, document, None)
            for document, declarations in reached.items()
            if declarations.rejected_outputs
        )
    return _order_closure_invocations(index, invocations)


def _candidate_dependency_documents(
    index: _LogDeclarationIndex,
    invocations: Sequence[Invocation],
    roots: Sequence[tuple[str, str | None]],
    materialized: set[CommandFrontier],
) -> frozenset[Path]:
    """Select only dependency documents declaring a reached candidate material."""

    wanted = _closure_wanted(index, invocations, roots)
    result: set[Path] = set()
    for entry in index.entries:
        for document, declarations in zip(entry.documents, entry.commands):
            if (
                _outputs_admitted(
                    tuple(
                        output
                        for declaration in declarations.declarations
                        if _declaration_frontier(index, document, declaration)
                        not in materialized
                        for output in declaration.outputs
                    )
                    + declarations.rejected_outputs,
                    wanted,
                    _document_frontier(index, document),
                )
                or any(
                    _rejected_failure_admitted(
                        failure, wanted, _rejected_frontier(index, document, failure)
                    )
                    and _rejected_frontier(index, document, failure) not in materialized
                    for failure in declarations.failures
                )
                or (
                    _outputs_admitted(
                        declarations.rejected_outputs,
                        wanted,
                        _rejected_frontier(index, document, None),
                    )
                    and _rejected_frontier(index, document, None) not in materialized
                )
            ):
                result.add(document.resolve())
    return frozenset(result)


def _reached_command_declarations(
    index: _LogDeclarationIndex,
    documents: frozenset[Path],
    invocations: Sequence[Invocation],
    roots: Sequence[tuple[str, str | None]],
    materialized: set[CommandFrontier],
) -> Mapping[Path, CommandDeclarationResult]:
    """Retain only commands that can explain the current closure frontier."""

    wanted = _closure_wanted(index, invocations, roots)
    result: dict[Path, CommandDeclarationResult] = {}
    for entry in index.entries:
        for document, declarations in zip(entry.documents, entry.commands):
            if document.resolve() not in documents:
                continue
            retained = tuple(
                declaration
                for declaration in declarations.declarations
                if _declaration_frontier(index, document, declaration)
                not in materialized
                if _reached_declaration_admitted(
                    index,
                    entry,
                    document,
                    declaration,
                    wanted,
                )
            )
            failures = tuple(
                failure
                for failure in declarations.failures
                if _rejected_frontier(index, document, failure) not in materialized
                if _rejected_failure_admitted(
                    failure,
                    wanted,
                    _rejected_frontier(index, document, failure),
                )
            )
            result[document.resolve()] = CommandDeclarationResult(
                retained,
                failures,
                tuple(
                    output
                    for output in declarations.rejected_outputs
                    if _outputs_admitted(
                        (output,),
                        wanted,
                        _rejected_frontier(index, document, None),
                    )
                ),
            )
    return result


def _graph_execution_bindings(
    state: _ScanState,
) -> Mapping[str, tuple[str, str]]:
    """Return exact current command-to-execution identities for graph edges."""

    bindings: dict[str, tuple[str, str]] = {}
    for invocation in (*state.invocations, *state.rejected_invocations):
        execution_state = state.execution_states.get(invocation.material_owner)
        if execution_state is None:
            continue
        association = associate_exact_execution(
            execution_state,
            invocation,
            project_root=state.project_root,
        )
        if association is not None:
            bindings[invocation.identity] = (
                invocation.entry,
                f"{association.cid}:{association.identity}",
            )
    return bindings


def _reached_declaration_admitted(
    index: _LogDeclarationIndex,
    entry: _EntryDeclaration,
    document: Path,
    declaration: CommandDeclaration,
    wanted: Sequence[tuple[str, CommandFrontier | None]],
) -> bool:
    """Admit a declaration by command order or its entry's first root owner."""

    frontier = _declaration_frontier(index, document, declaration)
    ordered_wanted = tuple(item for item in wanted if item[1] is not None)
    root_wanted = tuple(item for item in wanted if item[1] is None)
    if _outputs_admitted(declaration.outputs, ordered_wanted, frontier):
        return True
    if not _outputs_admitted(declaration.outputs, root_wanted, frontier):
        return False
    first = min(
        (
            _declaration_frontier(index, candidate_document, candidate)
            for candidate_document, declarations in zip(entry.documents, entry.commands)
            for candidate in declarations.declarations
            if _outputs_admitted(
                candidate.outputs,
                root_wanted,
                _declaration_frontier(index, candidate_document, candidate),
            )
        ),
        default=None,
    )
    return frontier == first


def _document_frontier(index: _LogDeclarationIndex, document: Path) -> CommandFrontier:
    """Return the first command position for one summary-listed document."""

    resolved = document.resolve()
    return next(
        (position, 0, 0)
        for position, listed in enumerate(index.document_order)
        if listed.resolve() == resolved
    )


def _declaration_frontier(
    index: _LogDeclarationIndex, document: Path, declaration: CommandDeclaration
) -> CommandFrontier:
    """Return the global source order of one successfully indexed command."""

    position, _, _ = _document_frontier(index, document)
    return position, declaration.fence, declaration.ordinal


def _rejected_frontier(
    index: _LogDeclarationIndex, document: Path, failure: object | None
) -> CommandFrontier:
    """Return a stable command position for one rejected-command sentinel."""

    position, _, _ = _document_frontier(index, document)
    return position, getattr(failure, "fence", 0), getattr(failure, "ordinal", 0)


def _invocation_frontier(
    index: _LogDeclarationIndex, invocation: Invocation
) -> CommandFrontier:
    """Map an observed command back to its declaration-level source order."""

    position, _, _ = _document_frontier(index, index.log_root / invocation.document)
    return position, invocation.fence, invocation.ordinal


def _declaration_reaches(outputs: Sequence[tuple[str, str]], wanted: set[str]) -> bool:
    return any(
        output == path
        or output.startswith(path.rstrip("/") + "/")
        or path.startswith(output.rstrip("/") + "/")
        for output, _ in outputs
        for path in wanted
    )


def _rejected_failure_admitted(
    failure: object,
    wanted: Sequence[tuple[str, CommandFrontier | None]],
    producer_frontier: CommandFrontier,
) -> bool:
    """Match one rejected candidate to the specific eligible frontier root."""

    error = getattr(failure, "error", None)
    observed = getattr(error, "observed", None)
    command = (
        observed.get("rejected_command") if isinstance(observed, Mapping) else None
    )
    if not isinstance(command, Mapping):
        return False
    outputs = command.get("declared_outputs", ())
    return any(
        isinstance(output, Mapping)
        and isinstance(output.get("path"), str)
        and _outputs_admitted(((output["path"], "unknown"),), wanted, producer_frontier)
        for output in outputs
    )


def _outputs_admitted(
    outputs: Sequence[tuple[str, str]],
    wanted: Sequence[tuple[str, CommandFrontier | None]],
    producer_frontier: CommandFrontier,
) -> bool:
    """Apply material matching and before-consumer order as one predicate."""

    return any(
        (consumer_frontier is None or producer_frontier < consumer_frontier)
        and _declaration_reaches(((output, kind),), {path})
        for output, kind in outputs
        for path, consumer_frontier in wanted
    )


def _selected_material_roots(
    state: _ScanState, target: EntryEvaluationTarget
) -> tuple[tuple[str, str | None], ...]:
    """Collect selected evidence, origin, and comparison roots without reading them.

    Evidence parsing is already target-owned surface work.  Resolving its input
    tokens here seeds producer materialization before lazy locator observation,
    so a locally matching consumer cannot hide an upstream stale producer.
    """

    roots: set[tuple[str, str | None]] = set()
    for entry in state.entries:
        if entry.root != target.entry_root.resolve() or entry.data_file is None:
            continue
        for resource in entry.data_file.inputs:
            if resource.origin or resource.comparison is not None:
                roots.add((resource.material_identity, None))
        roots.update(_entry_evidence_material_roots(entry, state))
    return tuple(sorted(roots))


def _entry_evidence_material_roots(
    entry: _Entry, state: _ScanState
) -> frozenset[tuple[str, None]]:
    """Resolve only the selected document's declared evidence inputs."""

    if entry.evidence_file is None:
        return frozenset()
    document = entry.document.relative_to(state.log_root).as_posix()
    roots: set[tuple[str, None]] = set()
    for record in entry.evidence_file.records:
        if not isinstance(record, PresentationRecord) or record.document != document:
            continue
        for source in record.sources:
            try:
                resolved = _resolve_source(source, entry, state)
            except MechanicalContractError:
                continue
            roots.add((resolved.path.as_posix(), None))
    return frozenset(roots)


def _selected_documents(state: _ScanState, target: EvaluationTarget) -> tuple[str, ...]:
    if isinstance(target, FullEvaluationTarget):
        return tuple(entry.document.stem for entry in state.entries)
    root = target.entry_root.resolve()
    return tuple(entry.document.stem for entry in state.entries if entry.root == root)


def _dependency_entries(state: _ScanState, target: EvaluationTarget) -> tuple[str, ...]:
    if isinstance(target, FullEvaluationTarget):
        return ()
    root = target.entry_root.resolve()
    return tuple(
        sorted(
            {
                _stable_entry_id(entry.document)
                for entry in state.entries
                if entry.root != root
            }
        )
    )


def _order_closure_invocations(
    index: _LogDeclarationIndex, invocations: Sequence[Invocation]
) -> tuple[Invocation, ...]:
    """Order a lazily materialized closure as if its documents were scanned.

    The declaration inventory carries the summary order without projecting any
    observations.  Reusing it here preserves the existing before-consumer
    producer rule for a target whose upstream producer occurs earlier.
    """

    document_order = {
        document.relative_to(index.log_root).as_posix(): position
        for position, document in enumerate(
            document for document in index.document_order
        )
    }
    ordered = sorted(
        invocations,
        key=lambda invocation: (
            document_order.get(invocation.document, len(document_order)),
            invocation.fence,
            invocation.ordinal,
            invocation.identity,
        ),
    )
    if len(ordered) > MAX_INVOCATIONS_PER_LOG:
        _fail(
            "provenance.resource.too_large",
            "command log",
            {"invocations": len(ordered), "limit": MAX_INVOCATIONS_PER_LOG},
        )
    return tuple(
        replace(invocation, sequence=sequence)
        for sequence, invocation in enumerate(ordered)
    )


# Entry surfaces and command discovery.


def _entries(
    summary_text: str,
    state: _ScanState,
    *,
    document_roots: frozenset[Path] | None = None,
    document_paths: frozenset[Path] | None = None,
    declaration_only_surfaces: bool = False,
) -> list[_Entry]:
    listed = _entry_documents(summary_text, state, document_roots, document_paths)
    if not listed:
        _fail("association.declaration_missing", str(state.summary), {"entries": 0})
    _validate_surface_placement(listed, state, scoped=document_roots is not None)
    entries = _observe_entries(listed, state, declaration_only_surfaces)
    _record_data_conflicts(entries, state)
    return entries


def _entry_documents(
    summary_text: str,
    state: _ScanState,
    document_roots: frozenset[Path] | None,
    document_paths: frozenset[Path] | None = None,
) -> tuple[Path, ...]:
    listed = _listed_entry_documents(summary_text, state)
    state.declared_entries = tuple(document.stem for document in listed)
    if document_roots is None:
        return listed
    selected = tuple(
        document for document in listed if document.parent.resolve() in document_roots
    )
    if document_paths is None:
        return selected
    return tuple(
        document for document in selected if document.resolve() in document_paths
    )


def _stable_entry_id(document: Path) -> str:
    match = re.fullmatch(r"(e[0-9]+)[a-z]?", document.stem, re.IGNORECASE)
    return match.group(1).lower() if match is not None else document.stem


def _observe_entries(
    listed: Sequence[Path],
    state: _ScanState,
    declaration_only_surfaces: bool,
) -> list[_Entry]:
    entries: list[_Entry] = []
    surfaces: dict[Path, _EntrySurface] = {}
    surface_errors: dict[Path, MechanicalContractError] = {}
    if not declaration_only_surfaces:
        for document in listed:
            if not document.is_file():
                continue
            try:
                _read_text(document, state)
            except MechanicalContractError:
                pass
    for document in listed:
        surface: _EntrySurface | None
        if declaration_only_surfaces:
            root = document.parent.resolve()
            if root not in surfaces:
                data_file, data_failure = _read_entry_declaration_data(
                    document.stem, root, state
                )
                surfaces[root] = _EntrySurface(
                    None, None, data_file, data_failure, None
                )
            surface = surfaces[root]
        else:
            surface = _load_entry_surface(
                document, state, surfaces=surfaces, errors=surface_errors
            )
        if surface is None:
            continue
        root = document.parent.resolve()
        entries.append(
            _Entry(
                id=_stable_entry_id(document),
                document=document.resolve(),
                root=root,
                evidence_file=surface.evidence_file,
                data_file=surface.data_file,
                retention_file=surface.retention_file,
                evidence_failure=surface.evidence_failure,
                data_failure=surface.data_failure,
            )
        )
    return entries


def _record_data_conflicts(entries: Sequence[_Entry], state: _ScanState) -> None:
    data_files = tuple(
        {
            entry.data_file.path: entry.data_file
            for entry in entries
            if entry.data_file
        }.values()
    )
    conflicts = find_log_consistency_conflicts(data_files)
    for conflict in conflicts:
        identity = hashlib.sha256(conflict.canonical_target.encode("utf-8")).hexdigest()
        check = _error_check(
            f"conformance:data-conflict:{identity}",
            RuleArea.CONFORMANCE,
            conflict.error,
            issue_context=IssueContext(
                source_locations=tuple(
                    SourceLocation(path.as_posix()) for path in conflict.data_files
                ),
                repair_keys=(
                    RepairKey(
                        RepairKeyKind.MATERIAL,
                        conflict.canonical_target,
                    ),
                ),
                context_nodes=(GraphReference("material", conflict.canonical_target),),
                admission_owner=AdmissionOwner.MATERIAL,
            ),
        )
        state.checks.append(check)
        for entry in entries:
            if entry.data_file is None:
                continue
            for resource in entry.data_file.inputs:
                if resource.material_identity == conflict.canonical_target:
                    _add_input_prerequisite(entry, resource, check, state)


def _record_indexed_data_conflicts(
    index: _LogDeclarationIndex, state: _ScanState
) -> None:
    """Project indexed conflicts only when they affect the observed closure."""

    relevant = {
        resource.material_identity
        for entry in state.entries
        if entry.data_file is not None
        for resource in entry.data_file.inputs
    }
    recorded = {check.check_id: check for check in state.checks}
    for conflict in index.data_conflicts:
        if conflict.canonical_target not in relevant:
            continue
        identity = hashlib.sha256(conflict.canonical_target.encode("utf-8")).hexdigest()
        check_id = f"conformance:data-conflict:{identity}"
        check = recorded.get(check_id)
        if check is None:
            check = _error_check(
                check_id,
                RuleArea.CONFORMANCE,
                conflict.error,
                issue_context=IssueContext(
                    source_locations=tuple(
                        SourceLocation(path.as_posix()) for path in conflict.data_files
                    ),
                    repair_keys=(
                        RepairKey(
                            RepairKeyKind.MATERIAL,
                            conflict.canonical_target,
                        ),
                    ),
                    context_nodes=(
                        GraphReference("material", conflict.canonical_target),
                    ),
                    admission_owner=AdmissionOwner.MATERIAL,
                ),
            )
            state.checks.append(check)
            recorded[check_id] = check
        for entry in state.entries:
            if entry.data_file is None:
                continue
            for resource in entry.data_file.inputs:
                if resource.material_identity == conflict.canonical_target:
                    _add_input_prerequisite(entry, resource, check, state)


def _index_log(summary_text: str, state: _ScanState) -> _LogDeclarationIndex:
    """Build the complete command namespace without observing current material.

    The scoped path uses this inventory only to choose producer owners.  It is
    intentionally separate from `_entries`, whose surface loader includes
    evidence and retention observations needed only by materialized entries.
    """

    grouped: dict[Path, list[Path]] = {}
    for document in _listed_entry_documents(summary_text, state):
        grouped.setdefault(document.parent.resolve(), []).append(document)
    indexed: list[_EntryDeclaration] = []
    for root, documents in grouped.items():
        ordered = tuple(sorted(documents))
        stable = re.fullmatch(r"(e[0-9]+)[a-z]?", ordered[0].stem, re.I)
        stable_id = stable.group(1).lower() if stable is not None else ordered[0].stem
        data, _data_failure = _read_entry_declaration_data(stable_id, root, state)
        command_results: list[CommandDeclarationResult] = []
        for document in ordered:
            try:
                text = _read_text(document, state)
            except MechanicalContractError:
                command_results.append(CommandDeclarationResult((), ()))
                continue
            command_results.append(
                index_commands(
                    text,
                    CommandDeclarationContext(
                        state.log_root.as_posix(),
                        stable_id,
                        document.relative_to(state.log_root).as_posix(),
                        root,
                        state.log_root,
                        state.project_root,
                        data,
                    ),
                )
            )
        commands = tuple(command_results)
        indexed.append(_EntryDeclaration(stable_id, root, ordered, data, commands))
    document_order = tuple(_listed_entry_documents(summary_text, state))
    producers, rejected = _declaration_candidate_indexes(indexed, document_order)
    conflicts = find_log_consistency_conflicts(
        tuple(entry.data for entry in indexed if entry.data is not None)
    )
    return _LogDeclarationIndex(
        state.log_root,
        tuple(indexed),
        tuple(document.resolve() for document in document_order),
        producers,
        rejected,
        tuple(conflicts),
    )


def _declaration_candidate_indexes(
    entries: Sequence[_EntryDeclaration],
    document_order: Sequence[Path],
) -> tuple[
    Mapping[str, tuple[_DeclarationCandidate, ...]],
    Mapping[str, tuple[_DeclarationCandidate, ...]],
]:
    """Build private valid and rejected producer ownership lookups."""

    positions = {
        document.resolve(): position for position, document in enumerate(document_order)
    }
    producers: dict[str, list[_DeclarationCandidate]] = defaultdict(list)
    rejected: dict[str, list[_DeclarationCandidate]] = defaultdict(list)
    for entry in entries:
        for document, declaration_result in zip(entry.documents, entry.commands):
            for declaration in declaration_result.declarations:
                frontier = (
                    positions[document.resolve()],
                    declaration.fence,
                    declaration.ordinal,
                )
                for output, kind in declaration.outputs:
                    producers[output].append(
                        _DeclarationCandidate(entry.stable_id, output, kind, frontier)
                    )
            _add_rejected_declaration_candidates(
                declaration_result,
                entry.stable_id,
                positions[document.resolve()],
                rejected,
            )
    return (
        {
            path: tuple(sorted(candidates, key=_candidate_sort_key))
            for path, candidates in producers.items()
        },
        {
            path: tuple(sorted(candidates, key=_candidate_sort_key))
            for path, candidates in rejected.items()
        },
    )


def _add_rejected_declaration_candidates(
    declarations: CommandDeclarationResult,
    owner: str,
    document_position: int,
    rejected: dict[str, list[_DeclarationCandidate]],
) -> None:
    """Retain parseable rejected output candidates for relevant diagnostics."""

    for failure in declarations.failures:
        observed = failure.error.observed
        if not isinstance(observed, Mapping):
            continue
        command = observed.get("rejected_command")
        if not isinstance(command, Mapping):
            continue
        for output in command.get("declared_outputs", ()):
            if isinstance(output, Mapping) and isinstance(output.get("path"), str):
                rejected[output["path"]].append(
                    _DeclarationCandidate(
                        owner,
                        output["path"],
                        str(output.get("kind", "unknown")),
                        (document_position, failure.fence, failure.ordinal),
                    )
                )


def _candidate_sort_key(candidate: _DeclarationCandidate) -> tuple[object, ...]:
    """Keep indexed candidate traversal deterministic and source ordered."""

    return candidate.frontier, candidate.entry_id, candidate.path, candidate.kind


def _validate_surface_placement(
    documents: Sequence[Path], state: _ScanState, *, scoped: bool = False
) -> None:
    allowed_roots = {document.parent.resolve() for document in documents}
    entries_root = (state.log_root / "entries").resolve()
    surface_names = ("data.csv", "data.json", "retention.json")
    for name in surface_names:
        for invalid in (state.log_root / name, entries_root / name):
            if invalid.exists():
                _fail(
                    "data.file.location_invalid"
                    if name.startswith("data")
                    else "retention.file.location_invalid",
                    str(invalid),
                    {"reason": "parent_or_log_level_surface"},
                )
    if scoped or not entries_root.is_dir():
        return
    try:
        descendants = bounded_descendants(
            entries_root, maximum_entries=MAX_ENTRY_SURFACE_PATHS
        )
    except BoundedTraversalError as error:
        _fail(
            "association.resource.too_large"
            if error.reason == "entry_limit"
            else "association.document_unavailable",
            str(entries_root),
            {
                "error": error.detail,
                "limit": error.limit,
                "observed": error.observed,
                "reason": error.reason,
            },
        )
    for candidate in descendants:
        if (
            candidate.name in surface_names
            and candidate.parent.resolve() not in allowed_roots
        ):
            _fail(
                "data.file.location_invalid"
                if candidate.name.startswith("data")
                else "retention.file.location_invalid",
                str(candidate),
                {"reason": "unowned_entry_surface"},
            )


def _load_entry_surface(
    document: Path,
    state: _ScanState,
    *,
    surfaces: dict[Path, _EntrySurface],
    errors: dict[Path, MechanicalContractError],
) -> _EntrySurface | None:
    """Load one owned entry surface while preserving sibling evaluation."""

    lexical_root = document.parent.absolute()
    try:
        _validate_owned_entry(document, lexical_root, state)
        root = lexical_root.resolve()
        if root in errors:
            raise errors[root]
        if root not in surfaces:
            surfaces[root] = _read_entry_surface(document.stem, root, state)
    except MechanicalContractError as error:
        errors[lexical_root] = error
        state.checks.append(
            _error_check(
                f"entry:{document.stem}:declaration",
                _error_scope(error, RuleArea.PROVENANCE),
                error,
                issue_context=IssueContext(
                    entry=document.stem,
                    source_locations=(SourceLocation(document.as_posix()),),
                    admission_owner=AdmissionOwner.ENTRY,
                ),
            )
        )
        return None
    return surfaces[lexical_root.resolve()]


def _validate_owned_entry(document: Path, root: Path, state: _ScanState) -> None:
    if not document.is_file() or ENTRY_ID_RE.fullmatch(document.stem) is None:
        _fail(
            "association.declaration_missing",
            str(document),
            {"entry": document.stem, "exists": document.is_file()},
        )
    try:
        root.relative_to((state.log_root / "entries").absolute())
        validate_entry_path_symlinks(document, state.log_root)
    except (ValueError, EntryMaterialPathError) as error:
        raise EngineV2Error(
            "evidence.declaration.invalid",
            "entry root",
            {
                "path": str(root),
                "reason": getattr(error, "reason", "outside_entries"),
                "root": str(state.log_root),
            },
            "Evidence V3 JSON File Schema",
        ) from error


def _read_entry_surface(entry_id: str, root: Path, state: _ScanState) -> _EntrySurface:
    evidence_path = root / "evidence.json"
    evidence_file: EvidenceFile | None = None
    evidence_failure = next(
        (
            failure
            for path, failure in state.document_failure_checks.items()
            if path.parent == root
        ),
        None,
    )
    if evidence_path.is_file() and evidence_failure is None:
        try:
            evidence_file = load_evidence_file(
                evidence_path, log_root=state.log_root, entry_root=root
            )
        except MechanicalContractError as error:
            evidence_failure = _record_entry_surface_error(
                entry_id, "evidence", evidence_path, error, state
            )

    data_file, data_failure = _read_entry_data(entry_id, root, state)

    retention_path = root / "retention.json"
    retention_file: RetentionFile | None = None
    if retention_path.is_file():
        try:
            retention_file = load_retention_file(retention_path, entry_root=root)
        except MechanicalContractError as error:
            _record_entry_surface_error(
                entry_id, "retention", retention_path, error, state
            )
    return _EntrySurface(
        evidence_file,
        evidence_failure,
        data_file,
        data_failure,
        retention_file,
    )


def _read_entry_data(
    entry_id: str, root: Path, state: _ScanState
) -> tuple[DataFile | None, RuleCheck | None]:
    data_path = root / "data.json"
    legacy_path = root / "data.csv"
    try:
        if data_path.exists() and legacy_path.exists():
            _fail(
                "data.file.location_invalid",
                str(root),
                {"files": [str(data_path), str(legacy_path)]},
            )
        if legacy_path.exists():
            _fail(
                "data.file.location_invalid",
                str(legacy_path),
                {"reason": "legacy_data_csv"},
            )
        data_file = (
            load_data_file(data_path, entry_root=root) if data_path.is_file() else None
        )
    except MechanicalContractError as error:
        check = _record_entry_surface_error(entry_id, "data", data_path, error, state)
        return None, check
    if data_file is None:
        return None, None
    for resource in data_file.inputs:
        registration = {
            "entry": entry_id,
            "name": resource.name,
            "path": resource.canonical_target,
        }
        try:
            _verify_input(resource, state)
        except MechanicalContractError as error:
            observed = (
                dict(error.observed)
                if isinstance(error.observed, Mapping)
                else {"value": error.observed}
            )
            error = MechanicalContractError(
                error.code,
                error.subject,
                {**observed, "registration": registration},
                error.rule,
                outcome=error.outcome,
            )
            check = _record_entry_surface_error(
                entry_id,
                f"input:{resource.name}",
                data_path,
                error,
                state,
            )
            _add_input_prerequisite_for_root(root, resource, check, state)
        else:
            state.verified_inputs.append(registration)
    return data_file, None


def _read_entry_declaration_data(
    entry_id: str, root: Path, state: _ScanState
) -> tuple[DataFile | None, RuleCheck | None]:
    """Load dependency declaration syntax without observing every input byte."""

    try:
        data_path = root / "data.json"
        data_file = (
            load_data_file(data_path, entry_root=root) if data_path.is_file() else None
        )
    except MechanicalContractError as error:
        return None, _record_entry_surface_error(
            entry_id, "data", data_path, error, state
        )
    return data_file, None


def _record_entry_surface_error(
    entry_id: str,
    component: str,
    source_path: Path,
    error: MechanicalContractError,
    state: _ScanState,
) -> RuleCheck:
    source_path = source_path.resolve()
    prior = state.entry_surface_failure_checks.get(source_path)
    if prior is not None:
        return prior
    input_name = (
        component.removeprefix("input:") if component.startswith("input:") else None
    )
    repair_keys = (
        (
            RepairKey(
                RepairKeyKind.RECORD,
                f"{entry_id}:data:{input_name}",
                entry_id,
            ),
        )
        if input_name is not None
        else (
            RepairKey(
                RepairKeyKind.SOURCE,
                source_path.as_posix(),
                entry_id,
            ),
        )
    )
    context_nodes = (
        (GraphReference("data_record", f"{entry_id}:{input_name}", entry_id),)
        if input_name is not None
        else ()
    )
    check = _error_check(
        f"entry:{entry_id}:{component}-declaration",
        _error_scope(error, RuleArea.PROVENANCE),
        error,
        issue_context=IssueContext(
            entry=entry_id,
            source_locations=(SourceLocation(source_path.as_posix()),),
            repair_keys=repair_keys,
            context_nodes=context_nodes,
            admission_owner=AdmissionOwner.ENTRY,
        ),
    )
    state.checks.append(check)
    state.entry_surface_failure_checks[source_path] = check
    return check


def _verify_input(
    resource: InputResource, state: _ScanState
) -> FingerprintObservation | None:
    key = resource.observation_identity
    observation = state.input_observations.get(key)
    if observation is not None:
        return observation
    observation = (
        state.fingerprint_cache.observe_resource(resource)
        if state.fingerprint_cache is not None
        else observe_fingerprint(resource)
    )
    if observation is None:
        return None
    if observation.identity_reused:
        state.input_fingerprints_reused += 1
    state.input_observations[key] = observation
    state.input_resources[key] = resource
    return observation


def _input_declaration_key(owner: str, resource: InputResource) -> str:
    """Identify one entry-owned input declaration for dependency tracking."""

    return f"{owner}:{resource.name}"


def _add_input_prerequisite(
    entry: _Entry,
    resource: InputResource,
    check: RuleCheck,
    state: _ScanState,
) -> None:
    _add_input_prerequisite_for_root(entry.root, resource, check, state)


def _add_input_prerequisite_for_root(
    root: Path,
    resource: InputResource,
    check: RuleCheck,
    state: _ScanState,
) -> None:
    owner = root.relative_to(state.log_root).as_posix()
    key = _input_declaration_key(owner, resource)
    prerequisites = state.input_prerequisite_checks.setdefault(key, [])
    if check not in prerequisites:
        prerequisites.append(check)
    targets = (
        state.input_prerequisite_directories
        if resource.kind == "directory"
        else state.input_prerequisite_files
    )
    material_prerequisites = targets.setdefault(resource.material_identity, [])
    if check not in material_prerequisites:
        material_prerequisites.append(check)


def _observe_script_identity(path: Path, state: _ScanState) -> ScriptObservation:
    """Observe one script through the project-level strong-identity store."""

    assert state.fingerprint_cache is not None
    try:
        observation = state.fingerprint_cache.observe_regular_file(path)
    except FingerprintCacheError as error:
        _fail("provenance.observation.unavailable", str(path), {"error": str(error)})
    identity = observation.cache_identity
    digest = observation.fingerprint.digest
    if not isinstance(identity, Mapping) or not isinstance(digest, str):
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "invalid_strong_identity"},
        )
    values = tuple(identity.get(name) for name in ("size", "mtime_ns", "ctime_ns"))
    if not all(
        isinstance(value, int) and not isinstance(value, bool) for value in values
    ):
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "invalid_filesystem_identity"},
        )
    size, mtime_ns, ctime_ns = cast(tuple[int, int, int], values)
    return ScriptObservation(digest, size, mtime_ns, ctime_ns)


def _listed_entry_documents(text: str, state: _ScanState) -> tuple[Path, ...]:
    result: list[Path] = []
    for target in index_entry_documents(text):
        path = (state.summary.parent / target).absolute()
        if path.suffix == ".md" and ENTRY_ID_RE.fullmatch(path.stem):
            result.append(path)
    return tuple(dict.fromkeys(result))


def _discover_invocations(
    state: _ScanState,
    entries: Sequence[_Entry] | None = None,
    *,
    indexed_documents: Mapping[Path, CommandDeclarationResult] | None = None,
) -> tuple[Invocation, ...]:
    documents: list[tuple[Invocation, ...]] = []
    for entry in entries if entries is not None else state.entries:
        try:
            documents.append(
                _discover_entry_invocations(state, entry, indexed_documents)
            )
        except MechanicalContractError as error:
            root_failure = _document_failure_check(entry.document, state)
            identity = f"entry:{entry.id}:command"
            check = (
                _dependent_check(
                    identity,
                    RuleArea.PROVENANCE,
                    root_failure.check_id,
                    rule="Recorded-Command Provenance And Material Graph",
                )
                if root_failure is not None
                else _error_check(
                    identity,
                    _error_scope(error, RuleArea.PROVENANCE),
                    error,
                    issue_context=IssueContext(
                        entry=entry.id,
                        source_locations=(SourceLocation(entry.document.as_posix()),),
                        context_nodes=(
                            GraphReference(
                                "document",
                                entry.document.as_posix(),
                                entry.id,
                            ),
                        ),
                        admission_owner=AdmissionOwner.ENTRY,
                    ),
                )
            )
            state.checks.append(check)
            if root_failure is not None:
                owner = _material_owner(entry, state)
                state.command_failure_owners.setdefault(owner, set()).add(identity)
                state.graph_failure_owners.setdefault(owner, set()).add(identity)
    result = order_invocations(documents)
    try:
        validate_command_structure(result)
    except MechanicalContractError as error:
        state.checks.append(
            _error_check(
                "commands:structure",
                RuleArea.CONFORMANCE,
                error,
                issue_context=_command_structure_issue_context(error, result, state),
            )
        )
    return result


def _discover_entry_invocations(
    state: _ScanState,
    entry: _Entry,
    indexed_documents: Mapping[Path, CommandDeclarationResult] | None,
) -> tuple[Invocation, ...]:
    document = entry.document
    text = _read_text(document, state)
    relative = document.relative_to(state.log_root).as_posix()
    context = _command_context(state, entry, relative)
    declaration = (
        indexed_documents.get(document.resolve())
        if indexed_documents is not None
        else None
    )
    discovery = (
        observe_commands(declaration, text, context)
        if declaration is not None
        else discover_commands(text, context)
    )
    valid = _eligible_discovered_invocations(discovery, entry, state)
    for failure in discovery.failures:
        _record_command_failure(entry, relative, failure, state)
    return valid


def _command_context(state: _ScanState, entry: _Entry, relative: str) -> CommandContext:
    return CommandContext(
        log_id=state.log_root.as_posix(),
        entry=entry.id,
        document=relative,
        entry_root=entry.root,
        log_root=state.log_root,
        project_root=state.project_root,
        data_file=entry.data_file,
        input_fingerprint_verifier=lambda resource: _verify_input(resource, state),
        script_identity_cache=state.script_cache,
        script_identity_observer=(
            (lambda path: _observe_script_identity(path, state))
            if state.fingerprint_cache is not None
            else None
        ),
    )


def _command_issue_context(
    entry_id: str,
    document: str,
    identity: str,
    *,
    fence: int | None = None,
    ordinal: int | None = None,
) -> IssueContext:
    attributes = identity
    if fence is not None and ordinal is not None:
        attributes = f"{identity}:{fence}:{ordinal}"
    return IssueContext(
        entry=entry_id,
        source_locations=(SourceLocation(document),),
        repair_keys=(
            RepairKey(
                RepairKeyKind.COMMAND,
                f"{entry_id}:{attributes}",
                entry_id,
            ),
        ),
        context_nodes=(GraphReference("command", identity, entry_id),),
        admission_owner=AdmissionOwner.COMMAND,
    )


def _log_issue_context(state: _ScanState) -> IssueContext:
    """Identify a whole-log source defect without manufacturing repair grouping."""

    return IssueContext(
        source_locations=(SourceLocation(state.summary.as_posix()),),
        admission_owner=AdmissionOwner.LOG,
    )


def _record_log_scan_error(error: MechanicalContractError, state: _ScanState) -> None:
    """Record one operation-local root failure unless source reading did already."""

    if _document_failure_check(Path(error.subject), state) is not None:
        return
    state.checks.append(
        _error_check(
            "conformance:log",
            RuleArea.CONFORMANCE,
            error,
            issue_context=_log_issue_context(state),
        )
    )


def _command_structure_issue_context(
    error: MechanicalContractError,
    invocations: Sequence[Invocation],
    state: _ScanState,
) -> IssueContext:
    """Attach a structural command failure to an observed command declaration."""

    command = next(
        (item for item in invocations if item.document == error.subject),
        None,
    )
    if command is None:
        return _log_issue_context(state)
    return _command_issue_context(
        command.entry,
        command.document,
        command.identity,
        fence=command.fence,
        ordinal=command.ordinal,
    )


def _pyrun_issue_context(
    owner: str,
    root: Path,
    state: _ScanState,
) -> IssueContext:
    entry_id = _entry_id_for_owner(owner, state)
    return IssueContext(
        entry=entry_id,
        source_locations=(SourceLocation(root.as_posix()),),
        context_nodes=(GraphReference("entry", entry_id),),
        admission_owner=AdmissionOwner.ENTRY,
    )


def _comparison_issue_context(
    entry: _Entry,
    subject: str,
    *,
    material: str | None = None,
) -> IssueContext:
    nodes: tuple[GraphReference, ...]
    if material is None:
        key = RepairKey(
            RepairKeyKind.RECORD,
            f"{entry.id}:evidence:{subject}",
            entry.id,
        )
        nodes = (GraphReference("evidence_record", f"{entry.id}:{subject}", entry.id),)
    else:
        key = RepairKey(
            RepairKeyKind.RECORD,
            f"{entry.id}:data:{subject}",
            entry.id,
        )
        nodes = (
            GraphReference("data_record", f"{entry.id}:{subject}", entry.id),
            GraphReference("material", material),
        )
    locations = tuple(
        SourceLocation(path.as_posix())
        for path in (
            entry.data_file.path if entry.data_file is not None else None,
            entry.evidence_file.path if entry.evidence_file is not None else None,
        )
        if path is not None
    )
    return IssueContext(
        entry=entry.id,
        source_locations=locations,
        repair_keys=(key,),
        context_nodes=nodes,
        admission_owner=AdmissionOwner.ENTRY,
    )


def _execution_issue_context(
    entry_id: str,
    cid: str,
    execution_id: str,
    record_path: str,
) -> IssueContext:
    return IssueContext(
        entry=entry_id,
        source_locations=(SourceLocation(record_path),),
        repair_keys=(
            RepairKey(
                RepairKeyKind.EXECUTION,
                f"{entry_id}:{cid}:{execution_id}",
                entry_id,
            ),
        ),
        context_nodes=(GraphReference("execution", f"{cid}:{execution_id}", entry_id),),
        admission_owner=AdmissionOwner.EXECUTION,
    )


def _producer_repair_context(
    invocation: Invocation,
    state: _ScanState,
) -> IssueContext:
    """Return exact command and current-execution repair ownership."""

    command = _command_issue_context(
        invocation.entry,
        invocation.document,
        invocation.identity,
        fence=invocation.fence,
        ordinal=invocation.ordinal,
    )
    execution_state = state.execution_states.get(invocation.material_owner)
    association = (
        associate_exact_execution(
            execution_state,
            invocation,
            project_root=state.project_root,
        )
        if execution_state is not None
        else None
    )
    execution = (
        _execution_issue_context(
            invocation.entry,
            association.cid,
            association.identity,
            execution_state.path.as_posix(),
        )
        if execution_state is not None and association is not None
        else None
    )
    return IssueContext(
        entry=invocation.entry,
        source_locations=(
            *command.source_locations,
            *(execution.source_locations if execution is not None else ()),
        ),
        repair_keys=(
            *command.repair_keys,
            *(execution.repair_keys if execution is not None else ()),
        ),
        context_nodes=(
            *command.context_nodes,
            *(execution.context_nodes if execution is not None else ()),
        ),
        admission_owner=AdmissionOwner.COMMAND,
    )


def _eligible_discovered_invocations(
    discovery: DiscoveryResult, entry: _Entry, state: _ScanState
) -> tuple[Invocation, ...]:
    valid: list[Invocation] = []
    for invocation in discovery.invocations:
        _record_raw_output_findings(invocation, state)
        prerequisites = _invocation_input_prerequisites(invocation, state)
        if not prerequisites:
            valid.append(invocation)
            continue
        identity = _command_check_identity(
            entry.document.stem, invocation.fence, invocation.ordinal
        )
        state.checks.append(
            _checks_depending_on(
                identity,
                RuleArea.PROVENANCE,
                prerequisites,
                rule="Recorded-Command Provenance And Material Graph",
            )
        )
        state.rejected_invocations.append(invocation)
        _register_invocation_blockers(invocation, identity, state)
    return tuple(valid)


def _record_command_failure(
    entry: _Entry,
    relative: str,
    failure: CommandDiscoveryFailure,
    state: _ScanState,
) -> None:
    state.rejected_producers.add(failure.error.observed)
    identity = _command_check_identity(
        entry.document.stem, failure.fence, failure.ordinal
    )
    prerequisites = _command_failure_prerequisites(entry, failure.error, state)
    if prerequisites:
        state.checks.append(
            _checks_depending_on(
                identity,
                _error_scope(failure.error, RuleArea.PROVENANCE),
                prerequisites,
                rule=failure.error.rule,
            )
        )
        owner = _material_owner(entry, state)
        state.graph_failure_owners.setdefault(owner, set()).add(identity)
        state.command_failure_owners.setdefault(owner, set()).add(identity)
        return
    if failure.error.code == "material.candidate.unresolved":
        _record_command_candidate_dependencies(
            entry, relative, failure, identity, state
        )
    state.checks.append(
        _error_check(
            identity,
            _error_scope(failure.error, RuleArea.PROVENANCE),
            failure.error,
            issue_context=_command_failure_issue_context(
                entry,
                relative,
                failure,
                identity,
            ),
        )
    )


def _command_failure_issue_context(
    entry: _Entry,
    relative: str,
    failure: CommandDiscoveryFailure,
    identity: str,
) -> IssueContext:
    context = _command_issue_context(
        entry.id,
        relative,
        identity,
        fence=failure.fence,
        ordinal=failure.ordinal,
    )
    observed = failure.error.observed
    if isinstance(observed, Mapping) and isinstance(
        observed.get("rejected_command"), Mapping
    ):
        return context
    return IssueContext(
        entry=context.entry,
        source_locations=context.source_locations,
        repair_keys=context.repair_keys,
        admission_owner=context.admission_owner,
    )


def _record_command_candidate_dependencies(
    entry: _Entry,
    relative: str,
    failure: CommandDiscoveryFailure,
    identity: str,
    state: _ScanState,
) -> None:
    observed = failure.error.observed
    candidates = observed.get("candidates", ()) if isinstance(observed, Mapping) else ()
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            dependency = _command_candidate_dependency(candidate, entry)
            state.command_candidate_dependencies.setdefault(dependency, set()).add(
                identity
            )
    state.command_failure_owners.setdefault(relative.rsplit("/", 1)[0], set()).add(
        identity
    )


def _command_candidate_dependency(candidate: str, entry: _Entry) -> str:
    """Resolve a named failed-command candidate to its material identity."""

    if input_token_parts(candidate) is None or entry.data_file is None:
        return candidate
    try:
        return resolve_input_token(candidate, entry.data_file).path
    except DataContractError:
        return candidate


def _record_raw_output_findings(invocation: Invocation, state: _ScanState) -> None:
    """Check each output argument once, independently of directory member count."""

    for number, relationship in enumerate(output_arguments(invocation), 1):
        argument = {
            "kind": "output_argument",
            "entry": invocation.entry,
            "document": invocation.document,
            "fence": invocation.fence,
            "ordinal": invocation.ordinal,
            "target": relationship.target,
            "path": relationship.path,
        }
        identity = (
            _command_check_identity(
                Path(invocation.document).stem, invocation.fence, invocation.ordinal
            )
            + f":output:{number}"
        )
        dependencies = ({"output_argument": argument},)
        if relationship.named_input is not None:
            state.checks.append(
                _pass_check(identity, RuleArea.CONFORMANCE, dependencies=dependencies)
            )
            continue
        error = EngineV2Error(
            "data.output.token_missing",
            invocation.document,
            {
                "command": invocation.identity,
                "output": relationship.path,
                "target": relationship.target,
                "output_argument": argument,
            },
            "Command Tokens And Roles",
        )
        state.checks.append(
            _error_check(
                identity,
                RuleArea.CONFORMANCE,
                error,
                dependencies=dependencies,
                issue_context=_command_issue_context(
                    invocation.entry,
                    invocation.document,
                    invocation.identity,
                    fence=invocation.fence,
                    ordinal=invocation.ordinal,
                ),
            )
        )


def _command_check_identity(entry: str, fence: int, ordinal: int) -> str:
    return f"entry:{entry}:command:{fence}:{ordinal}"


def _invocation_input_prerequisites(
    invocation: Invocation, state: _ScanState
) -> tuple[RuleCheck, ...]:
    checks = [
        check
        for relationship in invocation.inputs
        if relationship.input_resource is not None
        for check in state.input_prerequisite_checks.get(
            _input_declaration_key(
                invocation.material_owner, relationship.input_resource
            ),
            (),
        )
    ]
    return _unique_checks(checks)


def _current_invocation_inputs(
    invocation: Invocation, state: _ScanState
) -> Mapping[str, Fingerprint]:
    """Project already-observed current recipe inputs for output validation."""

    observed: dict[str, Fingerprint] = {}
    for relationship in invocation.inputs:
        resource = relationship.input_resource
        if resource is None:
            continue
        observation = _verify_input(resource, state)
        if observation is None:
            continue
        prior = observed.setdefault(resource.name, observation.fingerprint)
        if prior != observation.fingerprint:
            _fail(
                "provenance.output.signature_unsupported",
                invocation.document,
                {"input": resource.name, "reason": "conflicting_observation"},
            )
    return observed


def _command_failure_prerequisites(
    entry: _Entry, error: MechanicalContractError, state: _ScanState
) -> tuple[RuleCheck, ...]:
    if entry.data_failure is not None and error.code == "data.input.undeclared":
        return (entry.data_failure,)
    matching = [
        check
        for check in _entry_input_prerequisites(entry, state)
        if check.diagnostic is not None and check.diagnostic.code == error.code
    ]
    return _unique_checks(matching)


def _entry_input_prerequisites(
    entry: _Entry, state: _ScanState
) -> tuple[RuleCheck, ...]:
    if entry.data_file is None:
        return ()
    owner = _material_owner(entry, state)
    return _unique_checks(
        [
            check
            for resource in entry.data_file.inputs
            for check in state.input_prerequisite_checks.get(
                _input_declaration_key(owner, resource), ()
            )
        ]
    )


def _register_invocation_blockers(
    invocation: Invocation, identity: str, state: _ScanState
) -> None:
    paths = {relationship.path for relationship in invocation.outputs}
    paths.update(
        collection.root
        for collection in invocation.collections
        if collection.direction == "output" and collection.root is not None
    )
    if invocation.script is not None and Path(invocation.script).is_absolute():
        paths.add(invocation.script)
    for path in paths:
        state.command_candidate_dependencies.setdefault(path, set()).add(identity)
    state.command_failure_owners.setdefault(invocation.material_owner, set()).add(
        identity
    )


def _load_output_support(state: _ScanState) -> None:
    """Load each shared entry-root output map once through the fingerprint cache."""

    owners: dict[str, Path] = {}
    for entry in state.entries:
        owners.setdefault(_material_owner(entry, state), entry.root)
    for owner, root in sorted(owners.items()):
        path = root / "pyrun-outputs.json"
        current = root / PYRUN_FILENAME
        if (path.exists() or path.is_symlink()) and (
            current.exists() or current.is_symlink()
        ):
            error = EngineV2Error(
                "pyrun.state.conflict",
                str(root),
                {"reason": "multiple_execution_state_formats"},
                "Pyrun Execution State",
            )
            state.output_record_errors[owner] = error
            check = _error_check(
                f"entry:{owner}:pyrun",
                RuleArea.PROVENANCE,
                error,
                issue_context=_pyrun_issue_context(owner, root, state),
            )
            state.output_record_error_checks[owner] = check
            state.checks.append(check)
            continue
        selected = current if current.exists() or current.is_symlink() else path
        if not selected.exists() and not selected.is_symlink():
            state.output_files[owner] = empty_pyrun_outputs(root)
            continue
        try:
            if state.fingerprint_cache is not None:
                observation = state.fingerprint_cache.observe_regular_file(selected)
            else:
                with FingerprintCache(
                    state.project_root, writable=False, reuse=False
                ) as direct_cache:
                    observation = direct_cache.observe_regular_file(selected)
            digest = observation.fingerprint.digest
            if digest is None:
                _fail(
                    "pyrun.state.unavailable",
                    str(selected),
                    {"reason": "missing_digest"},
                )
            state.output_file_observations[selected.resolve().as_posix()] = digest
            if selected == current:
                execution_state = load_pyrun_state(
                    current,
                    entry_root=root,
                    project_root=state.project_root,
                )
                _validate_execution_bindings(owner, execution_state, state)
                state.execution_states[owner] = execution_state
                state.execution_output_owners[owner] = execution_output_owners(
                    execution_state
                )
            else:
                state.output_files[owner] = load_pyrun_outputs(
                    path,
                    entry_root=root,
                    project_root=state.project_root,
                )
        except MechanicalContractError as error:
            state.output_record_errors[owner] = error
            check = _error_check(
                f"entry:{owner}:pyrun",
                RuleArea.PROVENANCE,
                error,
                issue_context=_pyrun_issue_context(owner, root, state),
            )
            state.output_record_error_checks[owner] = check
            state.checks.append(check)


def _validate_execution_bindings(
    owner: str, execution_state: PyrunFile, state: _ScanState
) -> None:
    """Report each invalid persisted binding without rejecting sibling state."""

    entry_id = next(
        (entry.id for entry in state.entries if _material_owner(entry, state) == owner),
        owner,
    )
    record_path = execution_state.path.resolve().as_posix()
    dependency = {
        "pyrun_state": {
            "path": record_path,
            "sha256": state.output_file_observations.get(record_path),
        }
    }
    current_by_cid: dict[str, list[Invocation]] = {}
    for invocation in state.invocations:
        if invocation.material_owner == owner:
            current_by_cid.setdefault(invocation.cid, []).append(invocation)
    context = _ExecutionBindingContext(entry_id, execution_state, state, dependency)
    for cid in sorted(set(current_by_cid) | set(execution_state.commands)):
        comparison = compare_command(
            execution_state,
            cid,
            tuple(current_by_cid.get(cid, ())),
            project_root=state.project_root,
        )
        _record_command_comparison(context, cid, comparison)
    for cid, identity, execution in execution_state.execution_items():
        _record_execution_binding(context, cid, identity, execution)


@dataclass(frozen=True)
class _ExecutionBindingContext:
    entry_id: str
    execution_state: PyrunFile
    state: _ScanState
    dependency: Mapping[str, object]


def _record_command_comparison(
    context: _ExecutionBindingContext,
    cid: str,
    comparison: CommandComparison,
) -> None:
    for missing_member in comparison.missing:
        invocation = missing_member.invocation
        _record_command_state_mismatch(
            context,
            cid,
            missing_member.identity,
            "missing",
            _command_issue_context(
                invocation.entry,
                invocation.document,
                invocation.identity,
                fence=invocation.fence,
                ordinal=invocation.ordinal,
            ),
        )
    for stale_member in comparison.stale:
        _record_command_state_mismatch(
            context,
            cid,
            stale_member.identity,
            "stale",
            _execution_issue_context(
                context.entry_id,
                cid,
                stale_member.identity,
                context.execution_state.path.as_posix(),
            ),
        )
    for change in comparison.recipe_changed:
        _record_command_state_mismatch(
            context,
            cid,
            change.current.identity,
            "recipe_changed",
            _execution_issue_context(
                context.entry_id,
                cid,
                change.current.identity,
                context.execution_state.path.as_posix(),
            ),
        )
    for change in comparison.policy_changed:
        _record_policy_mismatches(context, cid, change)


def _record_command_state_mismatch(
    context: _ExecutionBindingContext,
    cid: str,
    identity: str,
    label: str,
    issue_context: IssueContext,
) -> None:
    context.state.checks.append(
        _error_check(
            f"conformance:{context.entry_id}:pyrun:{cid}:{identity}",
            RuleArea.CONFORMANCE,
            EngineV2Error(
                f"pyrun.command.{label}",
                str(context.execution_state.path),
                {"cid": cid, "entry": context.entry_id, "execution_id": identity},
                "Pyrun Command State",
            ),
            dependencies=(context.dependency,),
            issue_context=issue_context,
        )
    )


def _record_policy_mismatches(
    context: _ExecutionBindingContext, cid: str, change: ExecutionChange
) -> None:
    identity = change.current.identity
    execution = change.stored
    invocation = change.current.invocation
    subject = (
        f"{context.execution_state.path}:commands[{cid!r}]:executions[{identity!r}]"
    )
    if invocation.auto_reproduce != execution.auto_reproduce:
        _record_policy_mismatch(
            context,
            identity,
            "pyrun.policy.mismatch",
            subject,
            {
                "entry": context.entry_id,
                "cid": cid,
                "markdown_auto_reproduce": invocation.auto_reproduce,
                "recorded_auto_reproduce": execution.auto_reproduce,
            },
        )
    if invocation.exclusive != execution.exclusive:
        _record_policy_mismatch(
            context,
            identity,
            "pyrun.exclusive.mismatch",
            subject,
            {
                "entry": context.entry_id,
                "cid": cid,
                "markdown_exclusive": invocation.exclusive,
                "recorded_exclusive": execution.exclusive,
            },
        )


def _record_policy_mismatch(
    context: _ExecutionBindingContext,
    identity: str,
    code: str,
    subject: str,
    observed: Mapping[str, object],
) -> None:
    check_kind = (
        "pyrun-exclusive" if code == "pyrun.exclusive.mismatch" else "pyrun-policy"
    )
    context.state.checks.append(
        _error_check(
            f"conformance:{context.entry_id}:{check_kind}:{identity}",
            RuleArea.CONFORMANCE,
            EngineV2Error(code, subject, observed, "Pyrun Execution Policy"),
            dependencies=(context.dependency,),
            issue_context=_execution_issue_context(
                context.entry_id,
                str(observed["cid"]),
                identity,
                context.execution_state.path.as_posix(),
            ),
        )
    )


def _record_execution_binding(
    context: _ExecutionBindingContext,
    cid: str,
    identity: str,
    execution: PyrunExecution,
) -> None:
    subject = (
        f"{context.execution_state.path}:commands[{cid!r}]:executions[{identity!r}]"
    )
    try:
        projection = project_output_bindings(
            execution.recipe.parameters,
            execution.recipe.outputs,
            entry_root=context.execution_state.entry_root,
            project_root=context.state.project_root,
            subject=subject,
        )
        if projection.aliases:
            raise _alias_binding_error(subject, context.entry_id, projection.aliases)
    except OutputBindingError as error:
        observed = dict(cast(Mapping[str, object], error.observed))
        observed.setdefault("entry", context.entry_id)
        context.state.checks.append(
            _error_check(
                f"conformance:{context.entry_id}:pyrun-binding:{identity}",
                RuleArea.CONFORMANCE,
                OutputBindingError(subject, observed),
                dependencies=(context.dependency,),
                issue_context=_execution_issue_context(
                    context.entry_id,
                    cid,
                    identity,
                    context.execution_state.path.as_posix(),
                ),
            )
        )


def _alias_binding_error(
    subject: str, entry_id: str, aliases: Sequence[OutputBinding]
) -> OutputBindingError:
    first = aliases[0]
    return OutputBindingError(
        subject,
        {
            "aliases": [
                {
                    "authored": binding.authored,
                    "mechanism": binding.mechanism,
                    "output": binding.output,
                }
                for binding in aliases
            ],
            "authored": first.authored,
            "entry": entry_id,
            "output": first.output,
            "reason": "noncanonical",
        },
    )


def _entry_root_for_owner(owner: str, state: _ScanState) -> Path:
    for entry in state.entries:
        if _material_owner(entry, state) == owner:
            return entry.root
    _fail("pyrun.outputs.invalid", owner, {"reason": "unknown_owner"})


def _entry_root_for_entry_id(entry_id: str, state: _ScanState) -> Path:
    for entry in state.entries:
        if entry.id == entry_id:
            return entry.root
    _fail("pyrun.outputs.invalid", entry_id, {"reason": "unknown_entry"})


def _entry_id_for_owner(owner: str, state: _ScanState) -> str:
    for entry in state.entries:
        if _material_owner(entry, state) == owner:
            return entry.id
    _fail("pyrun.outputs.invalid", owner, {"reason": "unknown_owner"})


def _output_record(
    invocation: Invocation, material: str, state: _ScanState
) -> tuple[str, object | None]:
    root = _entry_root_for_owner(invocation.material_owner, state)
    try:
        key = portable_output_path(
            material,
            entry_root=root,
            project_root=state.project_root,
        )
    except MechanicalContractError:
        _fail(
            "pyrun.output.identity_invalid",
            material,
            {"owner": invocation.material_owner},
        )
    execution_state = state.execution_states.get(invocation.material_owner)
    if execution_state is not None:
        association = associate_execution(
            execution_state, invocation, project_root=state.project_root
        )
        owners = state.execution_output_owners[invocation.material_owner]
        execution_output = resolve_execution_output(
            invocation,
            material,
            project_root=state.project_root,
            association=association,
            owners=owners,
        )
        return (
            key,
            execution_output.association.execution
            if execution_output.association
            else None,
        )
    file = state.output_files.get(invocation.material_owner)
    return key, file.outputs.get(key) if file is not None else None


def _has_structural_output_record(
    invocation: Invocation, material: str, state: _ScanState
) -> bool:
    try:
        execution_state = state.execution_states.get(invocation.material_owner)
        if execution_state is not None:
            association = associate_execution(
                execution_state, invocation, project_root=state.project_root
            )
            resolved = resolve_execution_output(
                invocation,
                material,
                project_root=state.project_root,
                association=association,
                owners=state.execution_output_owners[invocation.material_owner],
            )
            return resolved.association is not None
        support = state.output_files.get(invocation.material_owner)
        if support is None:
            return False
        return (
            resolve_output_support(
                invocation,
                material,
                entry_root=_entry_root_for_owner(invocation.material_owner, state),
                project_root=state.project_root,
                support=support,
            ).record
            is not None
        )
    except MechanicalContractError:
        return False


def _validate_output_support(
    invocation: Invocation, material: str, state: _ScanState
) -> Mapping[str, object]:
    """Require one confirmed observation per declared output binding."""

    subject = producer_output_subject(invocation, material)
    cache_key = (invocation.identity, subject)
    cached = state.output_support_conclusions.get(cache_key)
    if cached is not None:
        if cached.failure is not None:
            failure = cached.failure
            raise MechanicalContractError(
                failure.code,
                failure.subject,
                failure.observed,
                failure.rule,
                outcome=failure.outcome,
            )
        assert cached.support is not None
        return cached.support

    try:
        support, currentness = _evaluate_output_support(invocation, subject, state)
    except MechanicalContractError as error:
        state.output_support_conclusions[cache_key] = _OutputSupportConclusion(
            failure=_provenance_finding(error, ProvenanceAnchor("material", subject))
        )
        raise
    state.output_support_conclusions[cache_key] = _OutputSupportConclusion(
        support=support, currentness=currentness
    )
    return support


def _producer_currentness(
    error: MechanicalContractError,
    invocation: Invocation,
    subject: str,
) -> ProducerCurrentness:
    reason = (
        "required"
        if error.code == "provenance.output.reproduction_required"
        else "signature_mismatch"
    )
    observed = (
        dict(error.observed)
        if isinstance(error.observed, Mapping)
        else {"value": error.observed}
    )
    return ProducerCurrentness(
        reason,
        subject,
        observed,
        ProvenanceAnchor("material", subject, invocation.identity),
    )


def _evaluate_output_support(
    invocation: Invocation, material: str, state: _ScanState
) -> tuple[Mapping[str, object], ProducerCurrentness | None]:
    """Evaluate support while retaining currentness only for Reproduce."""

    key, _ = _output_record(invocation, material, state)
    path = Path(material)
    if not path.is_file() and not path.is_dir():
        state.missing_output_paths.add(path.resolve().as_posix())
        _fail(
            "provenance.output.missing",
            material,
            {"output": key, "producer": invocation.identity},
        )
    record_error = state.output_record_errors.get(invocation.material_owner)
    if record_error is not None:
        return ({"dependency": f"entry:{invocation.material_owner}:pyrun"}, None)
    root = _entry_root_for_owner(invocation.material_owner, state)
    execution_state = state.execution_states.get(invocation.material_owner)
    if execution_state is not None:
        association = associate_execution(
            execution_state, invocation, project_root=state.project_root
        )
        execution_output = resolve_execution_output(
            invocation,
            material,
            project_root=state.project_root,
            association=association,
            owners=state.execution_output_owners[invocation.material_owner],
        )
        current_output = _observe_output_path(invocation, execution_output.path, state)
        execution = (
            execution_output.association.execution
            if execution_output.association
            else None
        )
        resolved_code = (
            resolve_execution_code(
                execution, entry_root=root, subject=execution_output.subject
            )
            if execution is not None
            else ()
        )
        current_code = (
            _observe_output_code(resolved_code, state)
            if execution is not None and not execution.requires_reproduction
            else None
        )
        currentness = None
        try:
            execution = require_current_execution_output(
                invocation,
                execution_output,
                current_output=current_output,
                current_inputs=_current_invocation_inputs(invocation, state),
                current_code=current_code,
            )
        except MechanicalContractError as failure:
            if failure.code not in _REPRODUCE_CURRENTNESS_CODES:
                raise
            currentness = _producer_currentness(failure, invocation, material)
            assert execution is not None
        return (
            {
                "output": execution_output.key,
                "record": execution_output_support_dict(
                    execution, invocation, execution_output.key
                ),
                "record_file": execution_state.path.as_posix(),
                "record_file_sha256": state.output_file_observations.get(
                    execution_state.path.resolve().as_posix()
                ),
            },
            currentness,
        )
    support = state.output_files[invocation.material_owner]
    resolved = resolve_output_support(
        invocation,
        material,
        entry_root=root,
        project_root=state.project_root,
        support=support,
    )
    current_output = _observe_output_path(invocation, resolved.path, state)
    candidate = resolved.record
    resolved_code = (
        resolve_code_support(
            candidate,
            entry_root=root,
            subject=resolved.subject,
        )
        if candidate is not None and candidate.code is not None
        else ()
    )
    current_code = (
        _observe_output_code(resolved_code, state)
        if candidate is not None and candidate.confirmed and candidate.code is not None
        else None
    )
    currentness = None
    try:
        record = require_current_output_support(
            invocation,
            resolved,
            current_output=current_output,
            current_inputs=_current_invocation_inputs(invocation, state),
            current_code=current_code,
        )
    except MechanicalContractError as error:
        if error.code not in _REPRODUCE_CURRENTNESS_CODES:
            raise
        currentness = _producer_currentness(error, invocation, material)
        if candidate is None:
            raise AssertionError(
                "currentness requires retained output support"
            ) from error
        record = candidate
    support_file = support
    return (
        {
            "output": resolved.key,
            "record": record.as_dict(),
            "record_file": support_file.path.as_posix(),
            "record_file_sha256": state.output_file_observations.get(
                support_file.path.resolve().as_posix()
            ),
        },
        currentness,
    )


def _observe_output_path(
    invocation: Invocation, path: Path, state: _ScanState
) -> Fingerprint:
    """Reuse the declaration-shaped observation for one named output."""

    resource = declared_output_resource(invocation, path)
    if resource is not None:
        observation = state.input_observations.get(resource.observation_identity)
        if observation is None:
            observation = (
                state.fingerprint_cache.observe_resource(resource)
                if state.fingerprint_cache is not None
                else observe_fingerprint(resource)
            )
        return observation.fingerprint
    return _observe_provenance_path(path, state)


def _observe_output_code(
    code: Sequence[ResolvedCodeSupport],
    state: _ScanState,
) -> Mapping[str, Fingerprint]:
    """Observe each unique resolved code file once for output currentness."""

    return {item.key: _observe_provenance_path(item.resolved, state) for item in code}


def _observe_provenance_path(path: Path, state: _ScanState) -> Fingerprint:
    canonical = path.resolve().as_posix()
    cached = state.provenance_observations.get(canonical)
    if cached is not None:
        return cached[1]
    if state.fingerprint_cache is None:
        try:
            with FingerprintCache(
                state.project_root, writable=False, reuse=False
            ) as temporary_cache:
                observation = (
                    temporary_cache.observe_directory(path)
                    if path.is_dir()
                    else temporary_cache.observe_regular_file(path)
                )
        except FingerprintCacheError as error:
            _fail(
                "provenance.observation.unavailable",
                canonical,
                {"error": str(error)},
            )
        kind = "directory" if path.is_dir() else "file"
        state.provenance_observations[canonical] = (kind, observation.fingerprint)
        return observation.fingerprint
    try:
        if path.is_dir():
            observation = state.fingerprint_cache.observe_directory(path)
            kind = "directory"
        else:
            observation = state.fingerprint_cache.observe_regular_file(path)
            kind = "file"
    except FingerprintCacheError as error:
        _fail("provenance.observation.unavailable", canonical, {"error": str(error)})
    state.provenance_observations[canonical] = (kind, observation.fingerprint)
    return observation.fingerprint


# Evidence, presentations, and provenance evaluation.


def _evaluate_entries(
    state: _ScanState, *, selected_roots: set[Path] | None = None
) -> None:
    if selected_roots is None:
        _record_unowned_evidence(state)
    compared_roots: set[Path] = set()
    for entry in state.entries:
        if selected_roots is not None and entry.root not in selected_roots:
            continue
        _evaluate_entry_reproduction_once(entry, compared_roots, state)
        try:
            presentations = _entry_presentations(entry, state)
            state.presentation_count += len(presentations)
            if state.presentation_count > MAX_PRESENTATIONS_PER_LOG:
                _fail(
                    "association.resource.too_large",
                    str(state.log_root),
                    {
                        "presentations": state.presentation_count,
                        "limit": MAX_PRESENTATIONS_PER_LOG,
                    },
                )
            evidence_file = _document_evidence_file(entry, state)
            if evidence_file is None:
                if presentations and entry.evidence_failure is None:
                    _fail(
                        "association.declaration_missing",
                        str(entry.root / "evidence.json"),
                        {"markers": len(presentations)},
                    )
                associated: Mapping[str, PresentedItem] = {}
            else:
                associated = associate_presentations(evidence_file, presentations)
            records = (
                [
                    record
                    for record in evidence_file.records
                    if isinstance(record, PresentationRecord)
                ]
                if evidence_file is not None
                else []
            )
            for record in records:
                if record.id not in associated:
                    continue
                outcome = _evaluate_record(entry, record, associated[record.id], state)
                state.records.append(outcome)
                state.checks.extend((outcome.evidence_check, outcome.provenance_check))
        except MechanicalContractError as error:
            identity = f"entry:{entry.id}:association"
            root_failure = _document_failure_check(entry.document, state)
            state.checks.append(
                _dependent_check(
                    identity,
                    RuleArea.EVIDENCE,
                    root_failure.check_id,
                    rule="Evidence File And Presentation Association",
                )
                if root_failure is not None
                else _error_check(
                    identity,
                    _error_scope(error, RuleArea.EVIDENCE),
                    error,
                    issue_context=IssueContext(
                        entry=entry.id,
                        source_locations=(SourceLocation(entry.document.as_posix()),),
                        context_nodes=(
                            GraphReference(
                                "document",
                                entry.document.as_posix(),
                                entry.id,
                            ),
                        ),
                        admission_owner=AdmissionOwner.ENTRY,
                    ),
                )
            )


def _evaluate_entry_reproduction_once(
    entry: _Entry, compared_roots: set[Path], state: _ScanState
) -> None:
    """Evaluate one shared entry surface once across its split documents."""

    if entry.root in compared_roots:
        return
    _evaluate_reproduction_comparisons(entry, state)
    compared_roots.add(entry.root)


def _evaluate_reproduction_comparisons(entry: _Entry, state: _ScanState) -> None:
    """Validate authored evidence-scoped comparison declarations once."""

    if entry.data_file is None or entry.data_failure is not None:
        return
    if entry.evidence_failure is not None:
        return
    try:
        validate_reproduction_tolerances(entry.data_file, entry.evidence_file)
    except MechanicalContractError as error:
        state.checks.append(
            _error_check(
                f"conformance:reproduction-tolerance:{entry.id}:{error.subject}",
                RuleArea.CONFORMANCE,
                error,
                issue_context=_comparison_issue_context(entry, error.subject),
            )
        )
    for resource in entry.data_file.inputs:
        if resource.comparison is None:
            continue
        try:
            evidence_comparison_definition(
                resource,
                data=entry.data_file,
                evidence=entry.evidence_file,
            )
        except MechanicalContractError as error:
            state.checks.append(
                _error_check(
                    f"conformance:reproduction-comparison:{entry.id}:{resource.name}",
                    RuleArea.CONFORMANCE,
                    error,
                    issue_context=_comparison_issue_context(
                        entry,
                        resource.name,
                        material=resource.canonical_target,
                    ),
                )
            )


def _entry_presentations(entry: _Entry, state: _ScanState) -> tuple[PresentedItem, ...]:
    presented: list[PresentedItem] = []
    document = entry.document
    text = _read_text(document, state)
    relative = document.relative_to(state.log_root).as_posix()
    for issue in index_entry_section_issues(text):
        state.checks.append(
            _failure_check(
                f"entry:{entry.id}:section:{issue.line}",
                RuleArea.CONFORMANCE,
                _FailureSpec(
                    "association.context_invalid",
                    f"{relative}:{issue.line}",
                    {
                        "classifier_version": SECTION_CLASSIFIER_VERSION,
                        "heading": issue.heading,
                        "labels": list(issue.labels),
                        "reason": issue.reason,
                    },
                    "Eligible Presentation Context",
                    issue_context=IssueContext(
                        entry=entry.id,
                        source_locations=(SourceLocation(relative, issue.line),),
                        context_nodes=(
                            GraphReference(
                                "document",
                                document.as_posix(),
                                entry.id,
                            ),
                        ),
                        admission_owner=AdmissionOwner.ENTRY,
                    ),
                ),
            )
        )
    indexed = index_entry_presentations(text, document=relative)
    _require_complete_markers(document, indexed, text, state)
    presented.extend(indexed)
    return tuple(presented)


def _document_evidence_file(entry: _Entry, state: _ScanState) -> EvidenceFile | None:
    evidence_file = entry.evidence_file
    if evidence_file is None:
        return None
    document = entry.document.relative_to(state.log_root).as_posix()
    records = tuple(
        record
        for record in evidence_file.records
        if not isinstance(record, PresentationRecord) or record.document == document
    )
    return EvidenceFile(evidence_file.path, evidence_file.entry_root, records)


def _unique_evidence_files(entries: Sequence[_Entry]) -> tuple[EvidenceFile, ...]:
    files: dict[Path, EvidenceFile] = {}
    for entry in entries:
        if entry.evidence_file is not None:
            files.setdefault(entry.evidence_file.path, entry.evidence_file)
    return tuple(files.values())


def _record_unowned_evidence(state: _ScanState) -> None:
    listed_by_file: dict[Path, set[str]] = {}
    for entry in state.entries:
        if entry.evidence_file is None:
            continue
        listed_by_file.setdefault(entry.evidence_file.path, set()).add(
            entry.document.relative_to(state.log_root).as_posix()
        )
    for evidence_file in _unique_evidence_files(state.entries):
        listed = listed_by_file[evidence_file.path]
        missing = [
            record
            for record in evidence_file.records
            if isinstance(record, PresentationRecord) and record.document not in listed
        ]
        if not missing:
            continue
        entry_id = next(
            entry.id
            for entry in state.entries
            if entry.evidence_file is not None
            and entry.evidence_file.path == evidence_file.path
        )
        state.checks.append(
            _failure_check(
                f"evidence:{evidence_file.path}:document-ownership",
                RuleArea.EVIDENCE,
                _FailureSpec(
                    "association.presentation_missing",
                    str(evidence_file.path),
                    {
                        "documents": sorted({record.document for record in missing}),
                        "ids": sorted(record.id for record in missing),
                    },
                    "Association Completeness And Conflict Rules",
                    issue_context=IssueContext(
                        entry=entry_id,
                        source_locations=(SourceLocation(str(evidence_file.path)),),
                        repair_keys=(
                            RepairKey(
                                RepairKeyKind.SOURCE,
                                str(evidence_file.path),
                                entry_id,
                            ),
                        ),
                        context_nodes=tuple(
                            GraphReference(
                                "evidence_record",
                                f"{entry_id}:{record.id}",
                                entry_id,
                            )
                            for record in missing
                        ),
                        admission_owner=AdmissionOwner.ENTRY,
                    ),
                ),
            )
        )


def _material_owner(entry: _Entry, state: _ScanState) -> str:
    return entry.root.relative_to(state.log_root).as_posix()


def _material_owner_for_entry(entry_id: str, state: _ScanState) -> str:
    for entry in state.entries:
        if entry.id == entry_id:
            return _material_owner(entry, state)
    raise AssertionError(f"unknown evaluation entry {entry_id!r}")


def _require_complete_markers(
    document: Path,
    indexed: Sequence[PresentedItem],
    text: str,
    state: _ScanState,
) -> None:
    expected = Counter(
        (item.kind, item.line) for item in index_entry_presentation_candidates(text)
    )
    observed = Counter((item.kind, item.line) for item in indexed)
    missing = expected - observed
    if missing:
        kind, line = sorted(missing.elements())[0]
        _fail(
            "association.declaration_missing",
            f"{document}:{line}",
            {"kind": kind, "missing": missing[(kind, line)]},
        )


def _observe_artifact_evidence(
    record: PresentationRecord,
    item: PresentedItem,
    source_path: Path,
    log_root: Path,
) -> Fingerprint | None:
    """Check artifact association and compare only path-based byte baselines."""

    require_artifact_source_association(
        item, source_path=source_path, log_root=log_root
    )
    require_artifact_baseline_form(record, item)
    if item.presentation_form in {"image", "link"}:
        return require_artifact_fingerprint(record, source_path=source_path)
    return None


def _evidence_issue_context(
    entry: _Entry,
    record: PresentationRecord,
    item: PresentedItem,
    materials: Sequence[_ResolvedSource] = (),
) -> IssueContext:
    """Capture typed repair ownership while record objects are in hand."""

    keys = [
        RepairKey(
            RepairKeyKind.RECORD,
            f"{entry.id}:evidence:{record.id}",
            entry.id,
        )
    ]
    keys.extend(
        RepairKey(RepairKeyKind.MATERIAL, path, entry.id)
        for path in sorted({material.path.as_posix() for material in materials})
    )
    nodes = [
        GraphReference(
            "evidence_record",
            f"{entry.id}:{record.id}",
            entry.id,
        ),
        GraphReference(
            "presentation",
            f"{item.document}:{item.id}",
        ),
    ]
    nodes.extend(_material_context_references(materials))
    return IssueContext(
        entry=entry.id,
        source_locations=(SourceLocation(item.document, item.line),),
        repair_keys=tuple(keys),
        context_nodes=tuple(nodes),
        admission_owner=AdmissionOwner.ENTRY,
    )


def _evaluate_record(
    entry: _Entry,
    record: PresentationRecord,
    item: PresentedItem,
    state: _ScanState,
) -> _RecordOutcome:
    identity = f"evidence:{entry.id}:{record.id}"
    issue_context = _evidence_issue_context(entry, record, item)
    if entry.data_failure is not None:
        evidence = _check_depending_on(
            identity,
            RuleArea.EVIDENCE,
            entry.data_failure,
            rule="Evidence File And Presentation Association",
        )
        provenance = _check_depending_on(
            f"provenance:{entry.id}:{record.id}",
            RuleArea.PROVENANCE,
            entry.data_failure,
            rule="Recorded-Command Provenance And Material Graph",
        )
        return _RecordOutcome(
            entry.id, record, item, (), evidence, provenance, None, ()
        )
    try:
        require_markdown_definition(record, item)
        materials = tuple(
            _resolve_source(source, entry, state) for source in record.sources
        )
    except MechanicalContractError as error:
        evidence = _error_check(
            identity,
            _error_scope(error, RuleArea.EVIDENCE),
            error,
            issue_context=issue_context,
        )
        provenance = _check_depending_on(
            f"provenance:{entry.id}:{record.id}",
            RuleArea.PROVENANCE,
            evidence,
            rule="Recorded-Command Provenance And Material Graph",
        )
        return _RecordOutcome(
            entry.id, record, item, (), evidence, provenance, None, ()
        )
    artifact_observation: Fingerprint | None = None
    if record.kind == "artifact":
        try:
            artifact_observation = _observe_artifact_evidence(
                record, item, materials[0].path, state.log_root
            )
        except MechanicalContractError as error:
            evidence = _error_check(
                identity,
                _error_scope(error, RuleArea.EVIDENCE),
                error,
                issue_context=_evidence_issue_context(
                    entry,
                    record,
                    item,
                    materials,
                ),
            )
            return _RecordOutcome(
                entry.id,
                record,
                item,
                materials,
                evidence,
                _record_provenance(entry, record, materials, state),
                None,
                (),
            )
    verification_checks = _unique_checks(
        check
        for material in materials
        for check in state.input_prerequisite_checks.get(material.input_name, ())
    )
    if verification_checks:
        evidence = _checks_depending_on(
            identity,
            RuleArea.EVIDENCE,
            verification_checks,
            rule="Strict Presentation Parsing And Comparison",
        )
        provenance = _record_provenance(entry, record, materials, state)
        if provenance.outcome is CheckOutcome.PASS:
            provenance = _checks_depending_on(
                f"provenance:{entry.id}:{record.id}",
                RuleArea.PROVENANCE,
                verification_checks,
                rule="Recorded-Command Provenance And Material Graph",
            )
        return _RecordOutcome(
            entry.id, record, item, materials, evidence, provenance, None, ()
        )
    provenance = _record_provenance(entry, record, materials, state)
    if record.kind == "artifact":
        evidence = _pass_check(
            identity,
            RuleArea.EVIDENCE,
            dependencies=artifact_evidence_dependencies(
                record,
                item,
                (
                    {
                        "declaration": materials[0].resource.content_identity,
                        "name": materials[0].input_name,
                        "path": materials[0].path.as_posix(),
                    },
                ),
                artifact_observation=artifact_observation,
            ),
        )
        return _RecordOutcome(
            entry.id,
            record,
            item,
            materials,
            evidence,
            provenance,
            None,
            (),
        )
    try:
        selections = [
            _selection(source, resolved, state)
            for source, resolved in zip(record.sources, materials)
        ]
        validate_tolerant_selection(
            record,
            tuple(material.resource for material in materials),
            selections,
        )
        transformed = _transform_and_compare(record, selections, item, state)
        evidence = _pass_check(
            identity,
            RuleArea.EVIDENCE,
            dependencies=_record_dependencies(
                record, materials, selections, transformed, item
            ),
        )
        canonical = _canonical_presentation(transformed, item)
        dependencies = tuple(
            [selection.dependency_projection for selection in selections]
            + [transformed.dependency_projection]
        )
    except MechanicalContractError as error:
        if any(material.resource.comparison is not None for material in materials):
            comparison_error = (
                error
                if isinstance(error, EvidenceComparisonError)
                else EvidenceComparisonError(
                    "reproduction.comparison.evidence_incompatible",
                    record.id,
                    {"code": error.code, "subject": error.subject},
                    "Evidence-Scoped Reproduction Comparison",
                )
            )
            state.checks.append(
                _error_check(
                    f"conformance:reproduction-comparison:{entry.id}:{record.id}",
                    RuleArea.CONFORMANCE,
                    comparison_error,
                    issue_context=_evidence_issue_context(
                        entry,
                        record,
                        item,
                        materials,
                    ),
                )
            )
        scope = _error_scope(error, RuleArea.EVIDENCE)
        evidence = _error_check(
            identity,
            scope,
            error,
            issue_context=_evidence_issue_context(entry, record, item, materials),
        )
        canonical = None
        dependencies = ()
    return _RecordOutcome(
        entry.id,
        record,
        item,
        materials,
        evidence,
        provenance,
        canonical,
        dependencies,
    )


def _transform_and_compare(
    record: PresentationRecord,
    selections: Sequence[Any],
    item: PresentedItem,
    state: _ScanState,
) -> TransformationResult:
    started = time.perf_counter()
    try:
        transformed = evaluate_transformation(
            record.transformation, selections, presentation_kind=record.kind
        )
        compare_presentation(
            transformed, presented_kind=item.kind, presented=item.value
        )
        return transformed
    finally:
        state.timings["presentation_comparison_seconds"] = state.timings.get(
            "presentation_comparison_seconds", 0.0
        ) + (time.perf_counter() - started)


def _selection(
    source: EvidenceSource, resolved: _ResolvedSource, state: _ScanState
) -> SelectionResult:
    assert source.locator is not None
    path = resolved.path
    source_key = path.resolve().as_posix()
    parsed_locator = parse_locator(source.locator)
    key = (source_key, parsed_locator.identity)
    if key not in state.selection_cache:
        evaluation_started = time.perf_counter()
        identity = state.source_cache.get(source_key)
        if identity is None:
            started = time.perf_counter()
            trusted_identity = _trusted_input_identity(resolved, state)
            identity = observe_source_identity(
                path,
                trusted_identity=trusted_identity,
                fingerprint_cache=state.fingerprint_cache,
            )
            state.timings["source_identity_seconds"] = state.timings.get(
                "source_identity_seconds", 0.0
            ) + (time.perf_counter() - started)
            state.source_opens += 1
            state.source_cache[source_key] = identity
            if identity.identity_reused and trusted_identity is None:
                state.source_hashes_reused += 1
        started = time.perf_counter()
        selection = (
            state.validation_cache.lookup_selection(
                source_identity=identity.source_identity,
                source_profile=identity.profile,
                locator_identity=parsed_locator.identity,
                evaluator_version=LOCATOR_EVALUATOR_VERSION,
            )
            if state.validation_cache is not None
            else None
        )
        state.timings["selection_cache_lookup_seconds"] = state.timings.get(
            "selection_cache_lookup_seconds", 0.0
        ) + (time.perf_counter() - started)
        if selection is not None:
            require_source_reader(identity)
        else:
            started = time.perf_counter()
            observation = load_source(identity)
            state.source_payload_reads += 1
            state.source_opens += 1
            state.timings["source_payload_read_seconds"] = state.timings.get(
                "source_payload_read_seconds", 0.0
            ) + (time.perf_counter() - started)
            started = time.perf_counter()
            selection = evaluate_observed_locator(observation, parsed_locator.value)
            state.source_evaluations += 1
            state.timings["source_parsing_and_locator_evaluation_seconds"] = (
                state.timings.get("source_parsing_and_locator_evaluation_seconds", 0.0)
                + (time.perf_counter() - started)
            )
            if state.validation_cache is not None:
                state.validation_cache.store_selection(
                    selection,
                    evaluator_version=LOCATOR_EVALUATOR_VERSION,
                )
        state.selection_cache[key] = selection
        serialized_bytes = len(encode_selection(selection))
        state.selection_serialized_bytes += serialized_bytes
        state.selection_serialized_max_bytes = max(
            state.selection_serialized_max_bytes, serialized_bytes
        )
        profile = state.selection_serialized_by_profile.setdefault(
            selection.source_profile,
            {"count": 0, "maximum_bytes": 0, "total_bytes": 0},
        )
        profile["count"] += 1
        profile["maximum_bytes"] = max(profile["maximum_bytes"], serialized_bytes)
        profile["total_bytes"] += serialized_bytes
        state.timings["source_evaluation_seconds"] = state.timings.get(
            "source_evaluation_seconds", 0.0
        ) + (time.perf_counter() - evaluation_started)
    return state.selection_cache[key]


def _record_provenance(
    entry: _Entry,
    record: PresentationRecord,
    materials: Sequence[_ResolvedSource],
    state: _ScanState,
) -> RuleCheck:
    identity = f"provenance:{entry.id}:{record.id}"
    artifact_dependency = {
        "artifacts": sorted(material.path.as_posix() for material in materials),
        "inputs": [
            {
                "declaration": material.resource.content_identity,
                "name": material.input_name,
                "path": material.path.as_posix(),
            }
            for material in sorted(materials, key=lambda item: item.input_name)
        ],
    }
    findings: list[ProvenanceFinding] = []
    output_record_prerequisites: list[RuleCheck] = []
    try:
        dependencies: list[Mapping[str, object]] = [artifact_dependency]
        for material in materials:
            if material.origin:
                try:
                    require_origin_boundary(
                        material.path,
                        material.resource,
                        state.invocations,
                        confirmed_record=lambda invocation, output: (
                            _has_structural_output_record(invocation, output, state)
                        ),
                        producer_index=state.producer_index,
                    )
                except MechanicalContractError as error:
                    findings.append(
                        _provenance_finding(
                            error,
                            ProvenanceAnchor(
                                "material", material.path.resolve().as_posix()
                            ),
                        )
                    )
                dependencies.append(
                    {"kind": "origin", "material": material.path.as_posix()}
                )
                continue
            invocations, context, cache_key = _material_provenance_context(
                entry, material, state
            )
            result = state.provenance_results.get(cache_key)
            if result is None:
                state.provenance_traversals += 1
                result = evaluate_complete_provenance(
                    material.path,
                    invocations,
                    context=context,
                )
                state.provenance_results[cache_key] = result
            else:
                state.provenance_traversals_reused += 1
            findings.extend(result.findings)
            output_record_prerequisites.extend(
                _output_record_error_checks(result.producers, state)
            )
            dependencies.append(
                {
                    "dependency_projection": result.dependency_projection,
                    "material": result.material,
                    "evaluated_materials": list(result.evaluated_materials),
                }
            )
        provenance_checks = _unique_checks(
            (
                *_register_provenance_checks(
                    _ordered_provenance_findings(findings, state),
                    state,
                ),
                *output_record_prerequisites,
            )
        )
        if provenance_checks:
            return _checks_depending_on(
                identity,
                RuleArea.PROVENANCE,
                provenance_checks,
                rule="Recorded-Command Provenance And Material Graph",
            )
        return _pass_check(identity, RuleArea.PROVENANCE, dependencies=dependencies)
    except MechanicalContractError as error:
        fallback_material = materials[0].path.resolve().as_posix()
        findings.append(
            _provenance_finding(error, ProvenanceAnchor("material", fallback_material))
        )
        provenance_checks = _register_provenance_checks(
            _ordered_provenance_findings(findings, state),
            state,
        )
        return _checks_depending_on(
            identity,
            RuleArea.PROVENANCE,
            provenance_checks,
            rule="Recorded-Command Provenance And Material Graph",
        )


def _output_record_error_checks(
    producers: Sequence[str], state: _ScanState
) -> tuple[RuleCheck, ...]:
    """Return source-record failures that made producer support unavailable."""

    assert state.producer_index is not None
    checks = {
        check.check_id: check
        for producer in producers
        if (
            check := state.output_record_error_checks.get(
                state.producer_index.by_identity[producer].material_owner
            )
        )
        is not None
    }
    return tuple(checks[identity] for identity in sorted(checks))


def _provenance_issue_context(
    finding: ProvenanceFinding,
    state: _ScanState,
) -> IssueContext:
    if finding.anchor.kind == "command":
        invocation = next(
            (
                item
                for item in state.invocations
                if item.identity == finding.anchor.identity
            ),
            None,
        )
        if invocation is None:
            raise ValueError(
                f"unknown provenance command anchor: {finding.anchor.identity}"
            )
        return _command_issue_context(
            invocation.entry,
            invocation.document,
            invocation.identity,
            fence=invocation.fence,
            ordinal=invocation.ordinal,
        )
    material = finding.anchor.identity
    logical = _logical_entry_material(material, state)
    entry_id = None
    if logical is not None:
        entry_id = _entry_id_for_owner(logical[0], state)
    known_materials = {
        relationship.path
        for invocation in state.invocations
        for relationship in (*invocation.inputs, *invocation.outputs)
    }
    producer_context = None
    if finding.anchor.producer_identity is not None:
        assert state.producer_index is not None
        producer = state.producer_index.by_identity.get(
            finding.anchor.producer_identity
        )
        if producer is not None:
            producer_context = _producer_repair_context(producer, state)
    return IssueContext(
        entry=entry_id,
        source_locations=(
            producer_context.source_locations if producer_context is not None else ()
        ),
        repair_keys=(
            RepairKey(RepairKeyKind.MATERIAL, material, entry_id),
            *(producer_context.repair_keys if producer_context is not None else ()),
        ),
        context_nodes=(
            *(
                (GraphReference("material", material),)
                if material in known_materials
                else ()
            ),
            *(producer_context.context_nodes if producer_context is not None else ()),
        ),
        admission_owner=AdmissionOwner.MATERIAL,
    )


def _material_context_references(
    materials: Sequence[_ResolvedSource],
) -> tuple[GraphReference, ...]:
    """Return one repair-context reference per distinct resolved material."""

    return tuple(
        GraphReference("material", path)
        for path in sorted({material.path.as_posix() for material in materials})
    )


def _material_provenance_context(
    entry: _Entry, material: _ResolvedSource, state: _ScanState
) -> tuple[
    tuple[Invocation, ...],
    CompleteProvenanceContext,
    tuple[str, int | None],
]:
    """Restrict evidence material to commands preceding its local consumer."""

    consumers = [
        invocation
        for invocation in state.invocations
        if invocation.entry == entry.id
        if any(
            relationship.input_resource == material.resource
            for relationship in invocation.inputs
        )
    ]
    if not consumers:
        assert state.complete_provenance_context is not None
        return (
            state.invocations,
            state.complete_provenance_context,
            (material.path.as_posix(), None),
        )
    boundary = min(invocation.sequence for invocation in consumers)
    invocations = tuple(
        invocation for invocation in state.invocations if invocation.sequence < boundary
    )
    context = state.restricted_provenance_contexts.get(boundary)
    if context is None:
        context = CompleteProvenanceContext(
            build_producer_index(invocations),
            producer_validator=lambda invocation, output: _validate_output_support(
                invocation, output, state
            ),
            confirmed_record=lambda invocation, output: _has_structural_output_record(
                invocation, output, state
            ),
        )
        state.restricted_provenance_contexts[boundary] = context
    return invocations, context, (material.path.as_posix(), boundary)


def _ordered_provenance_findings(
    findings: Sequence[ProvenanceFinding], state: _ScanState
) -> list[_PreparedProvenanceFinding]:
    """Deduplicate findings and prefer actual failures over confirmation state."""

    unique: dict[str, ProvenanceFinding] = {}
    for finding in findings:
        if finding.code in {"producer.missing", "lineage.missing"}:
            related = state.rejected_producers.related(finding.subject)
            if related:
                finding = replace(
                    finding,
                    observed={**finding.observed, "rejected_commands": list(related)},
                )
        key = canonical_json(finding.identity_dict())
        unique.setdefault(key, finding)
    prepared = [
        _PreparedProvenanceFinding(
            finding,
            canonical,
            (
                _command_blockers(finding.subject, state)
                if finding.code in {"producer.missing", "lineage.missing"}
                else ()
            ),
        )
        for canonical, finding in unique.items()
    ]
    return sorted(
        prepared,
        key=lambda item: (
            _provenance_finding_priority(item.finding, item.blockers),
            item.canonical,
        ),
    )


def _provenance_finding_priority(
    finding: ProvenanceFinding, blockers: Sequence[str]
) -> int:
    if finding.code in {"producer.missing", "lineage.missing"} and blockers:
        return 2
    return 0


def _register_provenance_checks(
    findings: Sequence[_PreparedProvenanceFinding],
    state: _ScanState,
) -> tuple[RuleCheck, ...]:
    """Register each distinct provenance condition once per validation attempt."""

    registered: list[RuleCheck] = []
    for finding in findings:
        check = state.provenance_checks.get(finding.canonical)
        if check is None:
            digest = hashlib.sha256(finding.canonical.encode("utf-8")).hexdigest()[:16]
            check = _provenance_finding_check(
                f"provenance:{finding.finding.code}:{digest}",
                finding,
                dependencies=(),
                issue_context=_provenance_issue_context(
                    finding.finding,
                    state,
                ),
            )
            state.provenance_checks[finding.canonical] = check
            state.checks.append(check)
        registered.append(check)
    return tuple(registered)


def _provenance_finding_check(
    identity: str,
    prepared: _PreparedProvenanceFinding,
    *,
    dependencies: Sequence[Mapping[str, object]],
    issue_context: IssueContext | None = None,
) -> RuleCheck:
    """Project one collected traversal finding into validation state."""

    finding = prepared.finding
    blockers = prepared.blockers
    if finding.code in {"producer.missing", "lineage.missing"} and blockers:
        return _blocked_check(
            identity,
            RuleArea.PROVENANCE,
            finding.subject,
            blockers,
            rule=finding.rule,
        )

    return _failure_check(
        identity,
        RuleArea.PROVENANCE,
        _FailureSpec(
            finding.code,
            finding.subject,
            finding.observed,
            finding.rule,
            status=(
                CheckOutcome.FAILED
                if finding.outcome == "unavailable"
                or finding.code == "provenance.observation.unavailable"
                else CheckOutcome.FINDING
            ),
            failure_operation=(
                _failure_operation_for_code(finding.code)
                if finding.outcome == "unavailable"
                or finding.code == "provenance.observation.unavailable"
                else None
            ),
        ),
        dependencies=dependencies,
        issue_context=issue_context,
    )


def _provenance_finding(
    error: MechanicalContractError, anchor: ProvenanceAnchor
) -> ProvenanceFinding:
    observed = (
        dict(error.observed)
        if isinstance(error.observed, Mapping)
        else {"value": error.observed}
    )
    return ProvenanceFinding(
        error.code, error.subject, observed, error.rule, anchor, error.outcome
    )


def _evaluate_summary(text: str, state: _ScanState) -> None:
    references = index_summary_references(text)
    if len(references) > MAX_SUMMARY_REFERENCES_PER_LOG:
        _fail(
            "association.resource.too_large",
            str(state.summary),
            {
                "summary_references": len(references),
                "limit": MAX_SUMMARY_REFERENCES_PER_LOG,
            },
        )
    _require_complete_summary_references(state.summary, text, references, state)
    outcomes = {
        (PurePosixPath(item.record.document).stem, item.record.id): item
        for item in state.records
    }
    targets = {
        identity: outcome.canonical
        for identity, outcome in outcomes.items()
        if outcome.canonical is not None
    }
    for reference in references:
        identity = f"summary:{reference.line}"
        target_identity = (reference.entry, reference.evidence_id)
        outcome = outcomes.get(target_identity)
        issue_context = _summary_issue_context(reference, outcome, state)
        if outcome is None or outcome.canonical is None:
            state.checks.append(
                _summary_target_failure(
                    identity,
                    target_identity,
                    outcome,
                    issue_context,
                    state,
                )
            )
        else:
            try:
                resolve_summary_references((reference,), targets)
                state.checks.append(
                    _pass_check(
                        f"evidence:{identity}",
                        RuleArea.EVIDENCE,
                        dependencies=(
                            {"target": f"{reference.entry}:{reference.evidence_id}"},
                        ),
                    )
                )
            except MechanicalContractError as error:
                state.checks.append(
                    _error_check(
                        f"evidence:{identity}",
                        RuleArea.EVIDENCE,
                        error,
                        issue_context=issue_context,
                    )
                )
        state.checks.append(
            _summary_provenance(
                identity,
                target_identity,
                outcome,
                issue_context,
            )
        )


def _require_complete_summary_references(
    summary: Path,
    text: str,
    references: Sequence[SummaryReference],
    state: _ScanState,
) -> None:
    expected = Counter(index_summary_statistic_candidates(text))
    observed = Counter(reference.line for reference in references)
    missing = expected - observed
    if missing:
        line = min(missing)
        _fail(
            "summary.reference.missing",
            f"{summary}:{line}",
            {"missing": missing[line]},
        )


# Material graph composition and artifact-level orphan grouping.


def _compose_graph(state: _ScanState, evaluation: EvaluationRequest) -> None:
    assert state.graph is not None
    request = MaterialClassificationRequest(
        graph=state.graph,
        entry_roots={entry.id: entry.root for entry in state.entries},
        bounds=ResearchGraphBounds(
            evaluation.graph_max_nodes,
            evaluation.graph_max_edges,
            evaluation.graph_max_ambiguities,
        ),
    )
    try:
        state.graph_analysis = classify_research_graph_materials(request)
    except MechanicalContractError as error:
        issue_context = (
            error.issue_context
            if isinstance(error, MaterialClassificationError)
            and error.issue_context is not None
            else _log_issue_context(state)
        )
        state.checks.append(
            _error_check(
                "graph:log",
                _error_scope(error, RuleArea.PROVENANCE),
                error,
                issue_context=issue_context,
            )
        )
        return
    state.graph = state.graph_analysis.graph
    _index_research_graph(state)
    orphan = state.graph_analysis.orphan
    _record_missing_outputs(state)
    unmatched = _record_unmatched_outputs(state)
    orphaned = tuple(
        path
        for path in orphan.orphaned
        if not _covered_by_unmatched_output(path, unmatched)
    )
    _record_orphan_artifacts(state, orphan.inventory, orphaned)
    _record_unused_inputs(state, orphan.unused_input_names)
    if not orphaned and not orphan.unused_input_names and not unmatched.paths:
        state.checks.append(
            _pass_check(
                "orphan:log",
                RuleArea.ORPHAN,
                dependencies=({"dependency_projection": orphan.dependency_projection},),
            )
        )


def _index_research_graph(state: _ScanState) -> None:
    """Cache node and established material-producer indexes once per graph."""

    assert state.graph is not None
    nodes = {node.node_id: node for node in state.graph.nodes}
    producers: dict[str, dict[str, ResearchNode]] = {}
    for edge in state.graph.edges:
        if edge.kind is not EdgeKind.PRODUCTION:
            continue
        source = nodes[edge.source]
        target = nodes[edge.target]
        if source.kind is not NodeKind.COMMAND or target.kind is not NodeKind.MATERIAL:
            continue
        producers.setdefault(target.identity, {})[source.node_id] = source
    state.graph_nodes_by_id = nodes
    state.graph_output_producers = {
        material: tuple(by_id[node_id] for node_id in sorted(by_id))
        for material, by_id in producers.items()
    }


def _record_missing_outputs(state: _ScanState) -> None:
    """Report graph outputs absent outside evidence-rooted traversal."""

    for canonical, declarations in sorted(state.graph_output_producers.items()):
        if canonical in state.missing_output_paths:
            continue
        path = Path(canonical)
        if path.is_file() or path.is_dir():
            continue
        entry_id = declarations[0].entry
        assert entry_id is not None
        root = _entry_root_for_entry_id(entry_id, state)
        owner = root.relative_to(state.log_root).as_posix()
        key = portable_output_path(
            canonical,
            entry_root=root,
            project_root=state.project_root,
        )
        producer_ids = tuple(item.identity for item in declarations)
        assert state.producer_index is not None
        repair_keys = [
            RepairKey(
                RepairKeyKind.MATERIAL,
                canonical,
                entry_id,
            )
        ]
        producer_contexts = tuple(
            _producer_repair_context(invocation, state)
            for declaration in declarations
            if (
                invocation := state.producer_index.by_identity.get(declaration.identity)
            )
            is not None
        )
        repair_keys.extend(
            repair_key
            for context in producer_contexts
            for repair_key in context.repair_keys
        )
        if len(producer_ids) > 1:
            repair_keys.append(
                RepairKey(
                    RepairKeyKind.OWNERSHIP,
                    f"{canonical}:{hashlib.sha256(canonical_json(producer_ids).encode()).hexdigest()}",
                    entry_id,
                )
            )
        state.missing_output_paths.add(canonical)
        state.checks.append(
            _failure_check(
                f"provenance:missing-output:{owner}:{key}",
                RuleArea.PROVENANCE,
                _FailureSpec(
                    "provenance.output.missing",
                    canonical,
                    {
                        "output": key,
                        "producers": list(producer_ids),
                    },
                    "Output Reconciliation",
                    issue_context=IssueContext(
                        entry=entry_id,
                        source_locations=tuple(
                            {
                                location
                                for context in producer_contexts
                                for location in context.source_locations
                            }
                        ),
                        repair_keys=tuple(set(repair_keys)),
                        context_nodes=tuple(
                            {
                                ResearchNode(
                                    NodeKind.MATERIAL,
                                    canonical,
                                ).reference,
                                *(item.reference for item in declarations),
                                *(
                                    node
                                    for context in producer_contexts
                                    for node in context.context_nodes
                                ),
                            }
                        ),
                        admission_owner=AdmissionOwner.MATERIAL,
                    ),
                ),
            )
        )


def _graph_evidence_connections(state: _ScanState) -> tuple[EvidenceConnection, ...]:
    connections: list[EvidenceConnection] = []
    for outcome in state.records:
        input_names = _evidence_input_names(outcome.entry, outcome.record, state)
        if not outcome.materials and not input_names:
            continue
        connections.append(
            EvidenceConnection(
                entry=outcome.entry,
                record=outcome.record.id,
                presentation=f"{outcome.item.document}:{outcome.item.id}",
                materials=tuple(
                    material.path.as_posix() for material in outcome.materials
                ),
                owner=_material_owner_for_entry(outcome.entry, state),
                dependencies=outcome.dependencies,
                origin_materials=frozenset(
                    material.path.as_posix()
                    for material in outcome.materials
                    if material.origin
                ),
                input_names=input_names,
            )
        )
    return tuple(connections)


def _graph_code_inputs(state: _ScanState) -> Mapping[str, tuple[str, ...]]:
    """Return conservative code edges from support associated with commands."""

    result: dict[str, tuple[str, ...]] = {}
    for invocation in state.invocations:
        execution_state = state.execution_states.get(invocation.material_owner)
        if execution_state is not None:
            paths = _execution_code_inputs(invocation, execution_state, state)
        else:
            paths = _legacy_code_inputs(invocation, state)
        if paths is not None:
            result[invocation.identity] = paths
    return result


def _execution_code_inputs(
    invocation: Invocation, execution_state: PyrunFile, state: _ScanState
) -> tuple[str, ...] | None:
    """Return code edges for one invocation associated with current state."""

    association = associate_execution(
        execution_state, invocation, project_root=state.project_root
    )
    if association is None:
        return None
    owners = state.execution_output_owners[invocation.material_owner]
    associated = next(
        (
            output
            for material in _invocation_output_materials(invocation)
            if (
                output := resolve_execution_output(
                    invocation,
                    material,
                    project_root=state.project_root,
                    association=association,
                    owners=owners,
                )
            ).association
            is not None
        ),
        None,
    )
    if associated is None:
        return None
    try:
        code = resolve_execution_code(
            association.execution,
            entry_root=owners.entry_root,
            subject=associated.subject,
        )
    except MechanicalContractError:
        return None
    return tuple(item.path.absolute().as_posix() for item in code)


def _legacy_code_inputs(
    invocation: Invocation, state: _ScanState
) -> tuple[str, ...] | None:
    """Return code edges for one invocation using read-only legacy support."""

    support = state.output_files.get(invocation.material_owner)
    if support is None:
        return None
    root = _entry_root_for_owner(invocation.material_owner, state)
    mappings: list[tuple[tuple[str, Fingerprint], ...]] = []
    paths: tuple[str, ...] | None = None
    for material in _invocation_output_materials(invocation):
        key = portable_output_path(
            material,
            entry_root=root,
            project_root=state.project_root,
        )
        record = support.outputs.get(key)
        if record is None or not output_support_matches_invocation(
            invocation,
            record,
            current_inputs=_current_invocation_inputs(invocation, state),
            material=material,
        ):
            continue
        try:
            resolved_code = resolve_code_support(
                record,
                entry_root=root,
                subject=material,
            )
        except MechanicalContractError:
            return None
        mappings.append(record.code)
        if paths is None:
            paths = tuple(item.path.absolute().as_posix() for item in resolved_code)
    if not mappings or len(set(mappings)) != 1:
        return None
    return paths or ()


def _invocation_output_materials(invocation: Invocation) -> tuple[str, ...]:
    """Return canonical output identities that can own output support."""

    materials = {relationship.path for relationship in invocation.outputs}
    materials.update(
        collection.root
        for collection in invocation.collections
        if collection.direction == "output" and collection.root is not None
    )
    return tuple(sorted(materials))


def _record_unmatched_outputs(state: _ScanState) -> _UnmatchedOutputs:
    graph_outputs = set(state.graph_output_producers)
    unmatched: set[str] = set()
    directory_roots: set[str] = set()
    for owner, output_file in sorted(state.output_files.items()):
        root = _entry_root_for_owner(owner, state)
        entry_id = _entry_id_for_owner(owner, state)
        for key in sorted(output_file.outputs):
            if key.startswith(PROJECT_OUTPUT_PREFIX):
                continue
            record = output_file.outputs[key]
            canonical = (
                output_target_path(
                    key,
                    entry_root=root,
                    project_root=state.project_root,
                    authored=True,
                )
                .resolve()
                .as_posix()
            )
            if canonical in graph_outputs:
                continue
            unmatched.add(canonical)
            if record.fingerprint.algorithm == "directory-sha256-v1":
                directory_roots.add(canonical)
            blockers = _output_reconciliation_blockers(owner, canonical, state)
            if blockers:
                state.checks.append(
                    _checks_depending_on(
                        f"orphan:unmatched-output:{owner}:{key}",
                        RuleArea.ORPHAN,
                        _checks_by_identity(blockers, state),
                        rule="Output Reconciliation",
                        subject=canonical,
                    )
                )
                continue
            state.checks.append(
                _failure_check(
                    f"orphan:unmatched-output:{owner}:{key}",
                    RuleArea.ORPHAN,
                    _FailureSpec(
                        "orphan.output.unmatched",
                        canonical,
                        {
                            "classification": "unmatched_output",
                            "output": key,
                            "record": output_file.path.as_posix(),
                        },
                        "Output Reconciliation",
                        issue_context=IssueContext(
                            entry=entry_id,
                            repair_keys=(
                                RepairKey(
                                    RepairKeyKind.MATERIAL,
                                    canonical,
                                    entry_id,
                                ),
                            ),
                            context_nodes=_known_graph_references(
                                state,
                                GraphReference("material", canonical),
                            ),
                            admission_owner=AdmissionOwner.MATERIAL,
                        ),
                    ),
                )
            )
    return _UnmatchedOutputs(frozenset(unmatched), frozenset(directory_roots))


def _known_graph_references(
    state: _ScanState,
    *references: GraphReference,
) -> tuple[GraphReference, ...]:
    return tuple(
        reference
        for reference in references
        if reference.node_id in state.graph_nodes_by_id
    )


def _covered_by_unmatched_output(material: str, unmatched: _UnmatchedOutputs) -> bool:
    if material in unmatched.paths:
        return True
    path = Path(material)
    return any(_within(path, Path(root)) for root in unmatched.directory_roots)


def _record_orphan_artifacts(
    state: _ScanState, inventory: Sequence[str], orphaned: Sequence[str]
) -> None:
    orphan_groups = _orphan_group_metadata(state, inventory, orphaned)
    for path in orphaned:
        metadata = orphan_groups[path]
        owner = str(metadata["owner"])
        entry_id = _entry_id_for_owner(owner, state)
        material_blockers = _material_graph_blockers(path, state)
        if material_blockers:
            state.checks.append(
                _checks_depending_on(
                    f"orphan:material:{path}",
                    RuleArea.ORPHAN,
                    _checks_by_identity(material_blockers, state),
                    rule="Orphan Detection",
                    subject=path,
                )
            )
            continue
        directory = metadata["directory"]
        group_root = (
            (_entry_root_for_owner(owner, state) / str(directory)).resolve().as_posix()
            if directory is not None
            else None
        )
        state.checks.append(
            _failure_check(
                f"orphan:material:{path}",
                RuleArea.ORPHAN,
                _FailureSpec(
                    "orphan.material.unused",
                    path,
                    {
                        "classification": "orphaned",
                        **metadata,
                    },
                    "Orphan Detection",
                    issue_context=IssueContext(
                        entry=entry_id,
                        repair_keys=(
                            RepairKey(
                                RepairKeyKind.MATERIAL,
                                path,
                                entry_id,
                            ),
                            *(
                                (
                                    RepairKey(
                                        RepairKeyKind.MATERIAL,
                                        group_root,
                                        entry_id,
                                    ),
                                )
                                if group_root is not None
                                else ()
                            ),
                        ),
                        context_nodes=_known_graph_references(
                            state,
                            GraphReference("material", path),
                            *(
                                (GraphReference("material", group_root),)
                                if group_root is not None
                                else ()
                            ),
                        ),
                        admission_owner=AdmissionOwner.MATERIAL,
                    ),
                ),
            )
        )


def _record_unused_inputs(state: _ScanState, names: Sequence[str]) -> None:
    for name in names:
        owner, input_name = name.rsplit(":", 1)
        entry_id = _entry_id_for_owner(owner, state)
        input_blockers = _input_graph_blockers(name, owner, state)
        if input_blockers:
            state.checks.append(
                _checks_depending_on(
                    f"orphan:data-name:{name}",
                    RuleArea.ORPHAN,
                    _checks_by_identity(input_blockers, state),
                    rule="Orphan Detection",
                    subject=name,
                )
            )
            continue
        state.checks.append(
            _failure_check(
                f"orphan:data-name:{name}",
                RuleArea.ORPHAN,
                _FailureSpec(
                    "orphan.input.unused",
                    name,
                    {"classification": "unused"},
                    "Orphan Detection",
                    issue_context=IssueContext(
                        entry=entry_id,
                        repair_keys=(
                            RepairKey(
                                RepairKeyKind.RECORD,
                                f"{entry_id}:data:{input_name}",
                                entry_id,
                            ),
                        ),
                        context_nodes=(
                            _data_record_reference(
                                owner,
                                input_name,
                                entry_id,
                                state,
                            ),
                        ),
                        admission_owner=AdmissionOwner.ENTRY,
                    ),
                ),
            )
        )


def _data_record_reference(
    owner: str,
    name: str,
    entry_id: str,
    state: _ScanState,
) -> GraphReference:
    assert state.graph is not None
    for node in state.graph.nodes:
        if (
            node.kind is NodeKind.DATA_RECORD
            and node.entry == entry_id
            and node.attributes.get("owner") == owner
            and node.attributes.get("name") == name
        ):
            return node.reference
    raise AssertionError(f"missing data-record node for {entry_id!r} input {name!r}")


def _orphan_group_metadata(
    state: _ScanState,
    inventory: Sequence[str],
    orphaned: Sequence[str],
) -> dict[str, Mapping[str, object]]:
    inventory_by_owner: dict[str, dict[str, str]] = defaultdict(dict)
    orphan_by_owner: dict[str, dict[str, str]] = defaultdict(dict)
    for path in inventory:
        logical = _logical_entry_material(path, state)
        if logical is not None:
            owner, relative = logical
            inventory_by_owner[owner][relative] = path
    for path in orphaned:
        logical = _logical_entry_material(path, state)
        if logical is not None:
            owner, relative = logical
            orphan_by_owner[owner][relative] = path
    result: dict[str, Mapping[str, object]] = {}
    for owner, orphan_paths in orphan_by_owner.items():
        result.update(
            _owner_orphan_groups(
                state.log_root.as_posix(),
                owner,
                set(inventory_by_owner[owner]),
                orphan_paths,
            )
        )
    return result


def _owner_orphan_groups(
    log_identity: str,
    owner: str,
    inventory: set[str],
    orphan_paths: Mapping[str, str],
) -> dict[str, Mapping[str, object]]:
    state = _OrphanGroupingState(
        log_identity, owner, inventory, orphan_paths, set(), {}
    )
    top_directories = sorted(
        {PurePosixPath(path).parts[0] for path in inventory if "/" in path}
    )
    for directory in top_directories:
        _collapse_orphan_directory(directory, state)
    for relative, path in orphan_paths.items():
        if relative not in state.grouped:
            state.result[path] = {
                "artifact_count": 1,
                "directory": None,
                "group_identity": None,
                "owner": owner,
                "relative": relative,
            }
    return state.result


def _collapse_orphan_directory(
    directory: str,
    state: _OrphanGroupingState,
) -> None:
    prefix = directory + "/"
    eligible = {path for path in state.inventory if path.startswith(prefix)}
    if eligible and eligible <= set(state.orphan_paths):
        identity = (
            "orphan-group:"
            + hashlib.sha256(
                canonical_json([state.log_identity, state.owner, directory]).encode()
            ).hexdigest()
        )
        for relative in eligible:
            path = state.orphan_paths[relative]
            state.result[path] = {
                "artifact_count": len(eligible),
                "directory": directory,
                "group_identity": identity,
                "owner": state.owner,
                "relative": relative,
            }
            state.grouped.add(relative)
        return
    depth = len(PurePosixPath(directory).parts)
    children = sorted(
        {
            PurePosixPath(path).parts[depth]
            for path in eligible
            if len(PurePosixPath(path).parts) > depth + 1
        }
    )
    for child in children:
        _collapse_orphan_directory(f"{directory}/{child}", state)


def _logical_entry_material(material: str, state: _ScanState) -> tuple[str, str] | None:
    path = Path(material)
    for root, owner, prefix in _logical_material_roots(state):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        return owner, f"{prefix}{relative}"
    return None


def _logical_material_roots(state: _ScanState) -> tuple[tuple[Path, str, str], ...]:
    """Build canonical entry and symlink roots once for graph-material lookup."""

    if state.logical_material_roots is not None:
        return state.logical_material_roots
    roots: list[tuple[Path, str, str]] = []
    seen: set[Path] = set()
    for entry in state.entries:
        if entry.root in seen:
            continue
        seen.add(entry.root)
        owner = _material_owner(entry, state)
        roots.append((entry.root, owner, ""))
        for name in ("data", "images"):
            logical_root = entry.root / name
            if not logical_root.is_symlink():
                continue
            roots.append((logical_root.resolve(), owner, f"{name}/"))
    state.logical_material_roots = tuple(roots)
    return state.logical_material_roots


def _material_graph_blockers(material: str, state: _ScanState) -> tuple[str, ...]:
    blockers = set(_command_blockers(material, state))
    logical = _logical_entry_material(material, state)
    if logical is None:
        return tuple(sorted(blockers))
    owner, _relative = logical
    blockers.update(state.graph_failure_owners.get(owner, ()))
    blockers.update(
        check.check_id for check in _owner_surface_prerequisites(owner, state)
    )
    blockers.update(
        check.check_id for check in _material_input_prerequisites(material, state)
    )
    return tuple(sorted(blockers))


def _output_reconciliation_blockers(
    owner: str, material: str, state: _ScanState
) -> tuple[str, ...]:
    """Return only prerequisites needed to establish command production."""

    blockers = set(_command_blockers(material, state))
    blockers.update(state.graph_failure_owners.get(owner, ()))
    return tuple(sorted(blockers))


def _input_graph_blockers(name: str, owner: str, state: _ScanState) -> tuple[str, ...]:
    blockers = set(state.command_failure_owners.get(owner, ()))
    blockers.update(state.graph_failure_owners.get(owner, ()))
    blockers.update(
        check.check_id for check in state.input_prerequisite_checks.get(name, ())
    )
    blockers.update(
        check.check_id for check in _owner_surface_prerequisites(owner, state)
    )
    return tuple(sorted(blockers))


def _owner_surface_prerequisites(
    owner: str, state: _ScanState
) -> tuple[RuleCheck, ...]:
    cached = state.owner_surface_prerequisite_checks.get(owner)
    if cached is not None:
        return cached
    prerequisites = _unique_checks(
        check
        for entry in state.entries
        if _material_owner(entry, state) == owner
        for check in (entry.data_failure, entry.evidence_failure)
        if check is not None
    )
    state.owner_surface_prerequisite_checks[owner] = prerequisites
    return prerequisites


def _material_input_prerequisites(
    material: str, state: _ScanState
) -> tuple[RuleCheck, ...]:
    """Return failed input declarations covering one canonical graph material."""

    path = Path(material)
    checks = list(state.input_prerequisite_files.get(material, ()))
    for target, prerequisites in state.input_prerequisite_directories.items():
        try:
            path.relative_to(Path(target))
        except ValueError:
            continue
        checks.extend(prerequisites)
    return _unique_checks(checks)


def _supported_output_directories(state: _ScanState) -> frozenset[str]:
    """Return exact directory outputs backed by matching producer support."""

    supported: set[str] = set()
    for invocation in state.invocations:
        output_file = state.output_files.get(invocation.material_owner)
        if output_file is None:
            continue
        entry_root = _entry_root_for_owner(invocation.material_owner, state)
        for collection in invocation.collections:
            if (
                collection.direction != "output"
                or collection.mechanism != "directory"
                or collection.root is None
            ):
                continue
            key = portable_output_path(
                collection.root,
                entry_root=entry_root,
                project_root=state.project_root,
            )
            record = output_file.outputs.get(key)
            if (
                record is None
                or record.fingerprint.algorithm != "directory-sha256-v1"
                or output_producer_mismatches(
                    invocation,
                    record,
                    current_inputs=_current_invocation_inputs(invocation, state),
                    material=collection.root,
                )
            ):
                continue
            supported.add(collection.root)
    return frozenset(supported)


def _evidence_input_names(
    entry_id: str, record: PresentationRecord, state: _ScanState
) -> tuple[str, ...]:
    """Return declared registry names consumed by one evidence record."""

    entry = next((item for item in state.entries if item.id == entry_id), None)
    if entry is None or entry.data_file is None:
        return ()
    owner = _material_owner(entry, state)
    names: list[str] = []
    for source in record.sources:
        parts = input_token_parts(source.source)
        if parts is None or parts[0] not in entry.data_file.by_name:
            continue
        names.append(f"{owner}:{parts[0]}")
    return tuple(names)


def _unique_retention_files(entries: Sequence[_Entry]) -> tuple[RetentionFile, ...]:
    files: dict[Path, RetentionFile] = {}
    for entry in entries:
        if entry.retention_file is not None:
            files.setdefault(entry.retention_file.path, entry.retention_file)
    return tuple(files.values())


def _resolve_source(
    source: EvidenceSource, entry: _Entry, state: _ScanState
) -> _ResolvedSource:
    value = source.source
    try:
        resolved = resolve_input_token(value, entry.data_file)
    except DataContractError as error:
        _fail(error.code, value, error.observed)
    resource = resolved.resource
    if resolved.member is None and resource.kind != "file":
        _fail(
            "evidence.declaration.invalid",
            value,
            {"reason": "file_source_required"},
        )
    path = Path(resolved.path)
    _validate_entry_source_path(path, entry, value)
    return _ResolvedSource(
        path.resolve(),
        resource.origin,
        f"{_material_owner(entry, state)}:{resource.name}",
        resource,
    )


def _trusted_input_identity(
    source: _ResolvedSource, state: _ScanState
) -> Mapping[str, object] | None:
    """Project a verified file input into the locator identity contract."""

    resource = source.resource
    if resource.kind != "file":
        return None
    observation = state.input_observations.get(resource.observation_identity)
    if observation is None or observation.fingerprint.digest is None:
        return None
    identity = observation.cache_identity
    if not isinstance(identity, Mapping):
        return None
    values = {name: identity.get(name) for name in ("size", "mtime_ns", "ctime_ns")}
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in values.values()
    ):
        return None
    return {**values, "sha256": observation.fingerprint.digest}


def _validate_entry_source_path(path: Path, entry: _Entry, source: str) -> None:
    try:
        validate_local_path_symlinks(path, entry.root)
    except EntryMaterialPathError as error:
        _fail(
            "locator.path.unresolved",
            source,
            {"path": path.as_posix(), "reason": error.reason},
        )


def _canonical_presentation(
    transformed: TransformationResult, item: PresentedItem
) -> CanonicalPresentation:
    if transformed.kind == "statistic":
        return CanonicalPresentation(kind="statistic", statistic=item.value)
    if transformed.kind == "table":
        return CanonicalPresentation(
            kind="table",
            table=transformed.rows,
            numerical_cells=transformed.numerical_cells,
        )
    return CanonicalPresentation(kind="output")


def _record_dependencies(
    record: PresentationRecord,
    materials: Sequence[_ResolvedSource],
    selections: Sequence[Any],
    transformed: TransformationResult,
    item: PresentedItem,
) -> tuple[Mapping[str, object], ...]:
    return (
        {"record": canonical_json(record.as_dict())},
        {
            "presentation": {
                "document": item.document,
                "id": item.id,
                "value": item.value,
            }
        },
        {
            "context": {
                "classification": item.section_classification,
                "classifier_version": SECTION_CLASSIFIER_VERSION,
                "under_results": item.under_results,
            }
        },
        {
            "inputs": [
                {
                    "declaration": material.resource.content_identity,
                    "name": material.input_name,
                    "path": material.path.as_posix(),
                }
                for material in materials
            ]
        },
        {"selections": [selection.dependency_projection for selection in selections]},
        {"transformation": transformed.dependency_projection},
    )


def _summary_target_failure(
    identity: str,
    target: tuple[str, str],
    outcome: _RecordOutcome | None,
    issue_context: IssueContext,
    state: _ScanState,
) -> RuleCheck:
    if outcome is not None:
        return _dependent_check(
            f"evidence:{identity}",
            RuleArea.EVIDENCE,
            outcome.evidence_check.check_id,
            rule="Summary Association",
        )
    evidence_failure = next(
        (
            entry.evidence_failure
            for entry in state.entries
            if entry.id == target[0] and entry.evidence_failure is not None
        ),
        None,
    )
    if evidence_failure is not None:
        return _check_depending_on(
            f"evidence:{identity}",
            RuleArea.EVIDENCE,
            evidence_failure,
            rule="Summary Association",
        )
    return _failure_check(
        f"evidence:{identity}",
        RuleArea.EVIDENCE,
        _FailureSpec(
            "summary.reference.target_invalid",
            identity,
            {"entry": target[0], "eid": target[1]},
            "Summary Association",
            None,
            CheckOutcome.FINDING,
            issue_context,
        ),
    )


def _summary_provenance(
    identity: str,
    target: tuple[str, str],
    outcome: _RecordOutcome | None,
    issue_context: IssueContext,
) -> RuleCheck:
    check_identity = f"provenance:{identity}"
    if outcome is None:
        return _dependent_check(
            check_identity,
            RuleArea.PROVENANCE,
            f"evidence:{identity}",
            rule="Summary Association",
        )
    target_check = outcome.provenance_check
    if target_check.outcome is CheckOutcome.PASS:
        return _pass_check(
            check_identity,
            RuleArea.PROVENANCE,
            dependencies=({"target": target_check.check_id},),
        )
    return _dependent_check(
        check_identity,
        RuleArea.PROVENANCE,
        target_check.check_id,
        rule="Summary Association",
        blocker_evidence=target_check.dependency_evidence,
    )


def _summary_issue_context(
    reference: SummaryReference,
    outcome: _RecordOutcome | None,
    state: _ScanState,
) -> IssueContext:
    """Bind summary conclusions to their exact line and referenced record."""

    summary_key = RepairKey(
        RepairKeyKind.RECORD,
        f"summary:{reference.line}",
    )
    target_context = (
        outcome.evidence_check.issue_context if outcome is not None else None
    )
    return IssueContext(
        entry=outcome.entry if outcome is not None else None,
        source_locations=(SourceLocation(state.summary.as_posix(), reference.line),),
        repair_keys=(
            summary_key,
            *(target_context.repair_keys if target_context is not None else ()),
        ),
        context_nodes=(
            target_context.context_nodes if target_context is not None else ()
        ),
        admission_owner=(
            AdmissionOwner.ENTRY if outcome is not None else AdmissionOwner.LOG
        ),
    )


def _pass_check(
    identity: str,
    area: RuleArea,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
) -> RuleCheck:
    dependency_ids = _dependency_ids(dependencies)
    return RuleCheck(
        identity,
        area,
        CheckOutcome.PASS,
        identity,
        dependency_ids,
        dependency_evidence=tuple(dependencies),
    )


def _dependent_check(
    identity: str,
    area: RuleArea,
    dependency: str,
    *,
    rule: str,
    blocker_evidence: Sequence[Mapping[str, object]] = (),
) -> RuleCheck:
    evidence = (
        tuple(blocker_evidence) if blocker_evidence else ({"dependency": dependency},)
    )
    return RuleCheck(
        identity,
        area,
        CheckOutcome.BLOCKED,
        identity,
        (dependency,),
        dependency_evidence=evidence,
        rule=rule,
    )


def _check_depending_on(
    identity: str, scope: RuleArea, dependency: RuleCheck, *, rule: str
) -> RuleCheck:
    """Project one failed prerequisite into its dependent check."""

    return _dependent_check(
        identity,
        scope,
        dependency.check_id,
        rule=rule,
        blocker_evidence=({"dependency": dependency.check_id},),
    )


def _checks_depending_on(
    identity: str,
    scope: RuleArea,
    dependencies: Sequence[RuleCheck],
    *,
    rule: str,
    subject: str | None = None,
) -> RuleCheck:
    """Project several input-verification prerequisites into one check."""

    unique = {check.check_id: check for check in dependencies}
    subject = identity if subject is None else subject
    evidence: list[Mapping[str, object]] = []
    for dependency in unique.values():
        evidence.append({"dependency": dependency.check_id})
        if dependency.outcome is CheckOutcome.BLOCKED:
            evidence.extend(dependency.dependency_evidence)
    return RuleCheck(
        identity,
        scope,
        CheckOutcome.BLOCKED,
        subject,
        tuple(unique),
        dependency_evidence=tuple(evidence),
        rule=rule,
    )


def _unique_checks(checks: Iterable[RuleCheck]) -> tuple[RuleCheck, ...]:
    return tuple({check.check_id: check for check in checks}.values())


def _checks_by_identity(
    identities: Sequence[str], state: _ScanState
) -> tuple[RuleCheck, ...]:
    selected = set(identities)
    return tuple(check for check in state.checks if check.check_id in selected)


def _blocked_check(
    identity: str,
    area: RuleArea,
    subject: str,
    blockers: Sequence[str],
    *,
    rule: str,
) -> RuleCheck:
    evidence = tuple({"dependency": dependency} for dependency in sorted(blockers))
    return RuleCheck(
        identity,
        area,
        CheckOutcome.BLOCKED,
        subject,
        _dependency_ids(evidence),
        dependency_evidence=evidence,
        rule=rule,
    )


def _command_blockers(subject: str, state: _ScanState) -> tuple[str, ...]:
    cached = state.command_blockers_by_subject.get(subject)
    if cached is not None:
        return cached
    if "://" in subject:
        state.command_blockers_by_subject[subject] = ()
        return ()
    path = Path(subject)
    if not path.is_absolute():
        state.command_blockers_by_subject[subject] = ()
        return ()
    blockers: set[str] = set()
    for candidate, directory, identities in _indexed_command_blockers(state):
        if path == candidate or (directory and _lexically_within(path, candidate)):
            blockers.update(identities)
    result = tuple(sorted(blockers))
    state.command_blockers_by_subject[subject] = result
    return result


def _indexed_command_blockers(
    state: _ScanState,
) -> tuple[tuple[Path, bool, tuple[str, ...]], ...]:
    """Resolve and classify failed-command material candidates once per scan."""

    if state.command_blocker_candidates is not None:
        return state.command_blocker_candidates
    candidates: list[tuple[Path, bool, tuple[str, ...]]] = []
    for candidate, identities in state.command_candidate_dependencies.items():
        path = Path(candidate)
        if path.is_absolute():
            path = path.resolve()
            candidates.append((path, path.is_dir(), tuple(sorted(identities))))
    state.command_blocker_candidates = tuple(candidates)
    return state.command_blocker_candidates


def _lexically_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _error_check(
    identity: str,
    scope: RuleArea,
    error: MechanicalContractError,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
    issue_context: IssueContext | None = None,
) -> RuleCheck:
    code = error.code
    subject = error.subject
    observed = error.observed
    rule = error.rule
    status = (
        CheckOutcome.FAILED
        if error.outcome == "unavailable"
        or code == "provenance.observation.unavailable"
        else CheckOutcome.FINDING
    )
    return _failure_check(
        identity,
        scope,
        _FailureSpec(
            code,
            subject,
            observed if isinstance(observed, Mapping) else {"value": observed},
            rule,
            status=status,
            issue_context=issue_context,
            failure_operation=(
                _failure_operation_for_code(code)
                if status is CheckOutcome.FAILED
                else None
            ),
        ),
        dependencies=dependencies,
    )


def _failure_check(
    identity: str,
    area: RuleArea,
    failure: _FailureSpec,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
    issue_context: IssueContext | None = None,
) -> RuleCheck:
    dependency_ids = set(_dependency_ids(dependencies))
    if failure.dependency is not None:
        dependency_ids.add(failure.dependency)
    context = issue_context or failure.issue_context
    if context is None and failure.status is CheckOutcome.FINDING:
        raise AssertionError(
            f"failed check {identity!r} requires explicit typed issue context"
        )
    if failure.status is CheckOutcome.FAILED and failure.failure_operation is None:
        raise AssertionError(
            f"failed check {identity!r} requires a typed failure operation"
        )
    return RuleCheck(
        identity,
        area,
        failure.status,
        failure.subject,
        tuple(sorted(dependency_ids)),
        diagnostic=CheckDiagnostic(
            failure.code,
            failure.subject,
            failure.rule,
            failure.observed,
            failure.dependency,
        ),
        issue_context=context,
        dependency_evidence=tuple(dependencies),
        failure_operation=failure.failure_operation,
    )


def _failure_operation_for_code(code: str) -> FailureOperation:
    """Classify one localized validator failure at check construction."""

    if code in {
        "association.artifact.inline_source_unavailable",
        "association.document_unavailable",
        "locator.reader.unavailable",
    }:
        return FailureOperation.READ
    if code in {"locator.source.changed", "provenance.observation.unavailable"}:
        return FailureOperation.OBSERVE
    if code.startswith("validation.cache."):
        return FailureOperation.CACHE
    if code.startswith("validation.graph."):
        return FailureOperation.GRAPH
    return FailureOperation.OTHER


def _dependency_ids(
    dependencies: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                identity
                for item in dependencies
                if isinstance((identity := item.get("dependency")), str)
            }
        )
    )


def _error_scope(error: MechanicalContractError, default: RuleArea) -> RuleArea:
    code = error.code
    conformance_prefixes = (
        "data.declaration.",
        "data.file.",
        "data.name.",
        "data.target.duplicate",
        "evidence.file.",
        "evidence.json.",
        "evidence.record.",
        "invocation.command.",
        "invocation.path_value.",
        "locator.condition.",
        "locator.literal.",
        "locator.property.",
        "locator.resource.",
        "locator.syntax.",
        "locator.version.",
        "presentation.marker.",
        "reproduction.comparison.",
        "transformation.input.",
        "transformation.nonfinite_",
        "transformation.output.",
        "transformation.render.",
        "transformation.scale.",
        "transformation.syntax.",
    )
    conformance_codes = {
        "association.context_invalid",
        "association.presentation.syntax_invalid",
        "association.resource.too_large",
        "data.input.token_missing",
        "evidence.declaration.invalid",
        "material.candidate.unresolved",
        "provenance.resource.too_large",
        "retention.declaration.invalid",
        "retention.file.location_invalid",
        "retention.target.missing",
        "summary.reference.invalid",
    }
    return (
        RuleArea.CONFORMANCE
        if (
            code in conformance_codes
            or any(code.startswith(prefix) for prefix in conformance_prefixes)
        )
        else default
    )


def _read_text(path: Path, state: _ScanState) -> str:
    path = path.resolve()
    cached = state.text_cache.get(path)
    if cached is not None:
        return cached
    prior = state.document_failure_checks.get(path)
    if prior is not None:
        assert prior.diagnostic is not None
        raise EngineV2Error(
            prior.diagnostic.code,
            prior.diagnostic.subject,
            prior.diagnostic.observed,
            prior.diagnostic.rule,
            outcome="unavailable",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        failure = EngineV2Error(
            "association.document_unavailable",
            str(path),
            {"error": str(error)},
            "Readable Validation Source",
            outcome="unavailable",
        )
        relative = None
        try:
            relative = path.relative_to(state.log_root)
        except ValueError:
            pass
        entry_id = (
            _stable_entry_id(path)
            if relative is not None and relative.parts[:1] == ("entries",)
            else None
        )
        digest = hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()[:16]
        check = _error_check(
            f"conformance:document-read:{digest}",
            RuleArea.CONFORMANCE,
            failure,
            issue_context=IssueContext(
                entry=entry_id,
                source_locations=(SourceLocation(path.as_posix()),),
                context_nodes=(GraphReference("document", path.as_posix(), entry_id),),
                admission_owner=(
                    AdmissionOwner.ENTRY if entry_id is not None else AdmissionOwner.LOG
                ),
            ),
        )
        state.document_failure_checks[path] = check
        state.checks.append(check)
        raise failure from error
    state.markdown_reads += 1
    state.text_cache[path] = text
    return text


def _document_failure_check(path: Path, state: _ScanState) -> RuleCheck | None:
    """Return the one localized failed check for an unreadable Markdown source."""

    return state.document_failure_checks.get(path.resolve())


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _verify_source_stability(state: _ScanState) -> None:
    for source, observation in sorted(state.source_cache.items()):
        try:
            require_source_unchanged(observation)
        except MechanicalContractError as error:
            identity = hashlib.sha256(source.encode("utf-8")).hexdigest()
            state.checks.append(
                _error_check(
                    f"evidence:source-stability:{identity}",
                    RuleArea.EVIDENCE,
                    error,
                )
            )


def _verify_provenance_stability(state: _ScanState) -> None:
    """Require every execution-linked byte observation to remain current."""

    cache = state.fingerprint_cache
    if cache is None:
        with FingerprintCache(
            state.project_root, writable=False, reuse=False
        ) as direct_cache:
            _verify_provenance_stability_with_cache(state, direct_cache)
        return
    _verify_provenance_stability_with_cache(state, cache)


def _verify_provenance_stability_with_cache(
    state: _ScanState, cache: FingerprintCache
) -> None:
    """Re-observe execution support through one shared fingerprint service."""

    expected_files = dict(state.output_file_observations)
    expected_files.update(
        {path: observation.digest for path, observation in state.script_cache.items()}
    )
    for path, expected_digest in sorted(expected_files.items()):
        try:
            observed_digest = cache.observe_regular_file(Path(path)).fingerprint.digest
        except FingerprintCacheError as error:
            _record_provenance_stability_error(path, {"error": str(error)}, state)
            continue
        if observed_digest != expected_digest:
            _record_provenance_stability_error(
                path,
                {
                    "expected": expected_digest,
                    "observed": observed_digest,
                    "reason": "changed",
                },
                state,
            )
    for path, (kind, expected_fingerprint) in sorted(
        state.provenance_observations.items()
    ):
        try:
            observation = (
                cache.observe_directory(Path(path))
                if kind == "directory"
                else cache.observe_regular_file(Path(path))
            )
        except FingerprintCacheError as error:
            _record_provenance_stability_error(path, {"error": str(error)}, state)
            continue
        if observation.fingerprint != expected_fingerprint:
            _record_provenance_stability_error(
                path,
                {
                    "expected": expected_fingerprint.as_dict(),
                    "observed": observation.fingerprint.as_dict(),
                    "reason": "changed",
                },
                state,
            )
    for path, expected_input in sorted(state.input_observations.items()):
        resource = state.input_resources[path]
        try:
            observed_input = cache.observe_resource(resource)
        except (FingerprintCacheError, MechanicalContractError) as error:
            _record_provenance_stability_error(path, {"error": str(error)}, state)
            continue
        if (
            observed_input is None
            or observed_input.fingerprint != expected_input.fingerprint
        ):
            _record_provenance_stability_error(
                path,
                {
                    "expected": expected_input.fingerprint.as_dict(),
                    "observed": (
                        observed_input.fingerprint.as_dict()
                        if observed_input is not None
                        else None
                    ),
                    "reason": "changed",
                },
                state,
            )


def _record_provenance_stability_error(
    path: str, observed: Mapping[str, object], state: _ScanState
) -> None:
    identity = hashlib.sha256(path.encode()).hexdigest()
    state.checks.append(
        _failure_check(
            f"provenance:stability:{identity}",
            RuleArea.PROVENANCE,
            _FailureSpec(
                "provenance.observation.unavailable",
                path,
                observed,
                "Stable Byte Observation",
                status=CheckOutcome.FAILED,
                failure_operation=FailureOperation.OBSERVE,
            ),
        )
    )


def _fail(
    code: str,
    subject: str,
    observed: object,
    *,
    outcome: str = "fail",
) -> NoReturn:
    raise EngineV2Error(
        code,
        subject,
        observed,
        "Mechanical Validation Evaluation And Outcomes",
        outcome=outcome,
    )
