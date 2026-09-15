"""Finding-owned Reproduce admission over the live validation graph."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Mapping, NoReturn, Sequence

from validation.domain import (
    AdmissionOwner,
    Finding,
    RepairKeyKind,
    RuleArea,
    ValidationSnapshot,
)
from validation.research_graph import (
    AmbiguityKind,
    AmbiguityObservation,
    EdgeKind,
    NodeKind,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)

from .model import ActionError

ExecutionKey = tuple[str, str, str]
_UNANCHORED_NONBLOCKING_CODES = frozenset(
    {"producer.missing", "summary.reference.unresolved"}
)


@dataclass(frozen=True, order=True)
class SelectedExecution:
    """One selected persisted execution and its complete canonical outputs."""

    entry: str
    cid: str
    execution_id: str
    outputs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entry or not self.cid or not self.execution_id:
            raise ValueError("selected execution identity is incomplete")
        normalized = tuple(sorted(set(self.outputs)))
        if not normalized or len(normalized) != len(self.outputs):
            raise ValueError("selected execution outputs are empty or duplicate")
        object.__setattr__(self, "outputs", normalized)

    @property
    def key(self) -> ExecutionKey:
        return self.entry, self.cid, self.execution_id

    @property
    def graph_identity(self) -> str:
        return f"{self.cid}:{self.execution_id}"


@dataclass(frozen=True, order=True)
class ExecutionAdmission:
    """One private execution-level admission decision."""

    entry: str
    cid: str
    execution_id: str
    disposition: str
    blocking_finding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition not in {"admitted", "excluded"}:
            raise ValueError("execution admission disposition is unsupported")
        normalized = tuple(sorted(set(self.blocking_finding_ids)))
        if normalized != self.blocking_finding_ids:
            raise ValueError("execution admission finding IDs are not canonical")
        if (self.disposition == "admitted") == bool(normalized):
            raise ValueError("execution admission blockers contradict disposition")

    @property
    def key(self) -> ExecutionKey:
        return self.entry, self.cid, self.execution_id

    def as_dict(self) -> dict[str, object]:
        return {
            "blocking_finding_ids": list(self.blocking_finding_ids),
            "cid": self.cid,
            "disposition": self.disposition,
            "entry": self.entry,
            "execution_id": self.execution_id,
        }


@dataclass(frozen=True)
class ReproductionAdmission:
    """Complete private admission result for one evaluated selection."""

    validation_snapshot_id: str
    executions: tuple[ExecutionAdmission, ...]
    global_blocking_finding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.validation_snapshot_id:
            raise ValueError("reproduction admission snapshot identity is empty")
        ordered = tuple(sorted(self.executions, key=lambda item: item.key))
        if ordered != self.executions or len({item.key for item in ordered}) != len(
            ordered
        ):
            raise ValueError("reproduction admission executions are not canonical")
        blockers = tuple(sorted(set(self.global_blocking_finding_ids)))
        if blockers != self.global_blocking_finding_ids:
            raise ValueError("global admission blockers are not canonical")


@dataclass(frozen=True)
class _BoundExecution:
    selected: SelectedExecution
    execution: ResearchNode
    command: ResearchNode


def evaluate_reproduction_admission(
    snapshot: ValidationSnapshot,
    graph: ResearchGraph,
    selected: Sequence[SelectedExecution],
) -> ReproductionAdmission:
    """Derive per-execution admission directly from findings and graph ownership."""

    ordered = tuple(sorted(selected, key=lambda item: item.key))
    if len({item.key for item in ordered}) != len(ordered):
        _invalid("selected executions are duplicate")
    index = _GraphIndex(graph)
    bound = tuple(index.bind(item) for item in ordered)
    scope = _AdmissionScopeIndex(index, bound)
    blockers: dict[ExecutionKey, set[str]] = {
        item.selected.key: set() for item in bound
    }
    global_blockers: set[str] = set()
    for finding in snapshot.findings:
        _assign_finding_blockers(
            finding,
            scope,
            blockers,
            global_blockers,
        )
    _propagate_dependency_blockers(index, bound, blockers)
    executions = tuple(
        ExecutionAdmission(
            item.selected.entry,
            item.selected.cid,
            item.selected.execution_id,
            "excluded" if blockers[item.selected.key] else "admitted",
            tuple(sorted(blockers[item.selected.key])),
        )
        for item in bound
    )
    return ReproductionAdmission(
        snapshot.internal_snapshot_id,
        executions,
        tuple(sorted(global_blockers)),
    )


def _assign_finding_blockers(
    finding: Finding,
    scope: _AdmissionScopeIndex,
    blockers: Mapping[ExecutionKey, set[str]],
    global_blockers: set[str],
) -> None:
    """Apply one finding's owner without giving its batch admission authority."""

    if not _blocks_reproduction(finding):
        return
    if finding.admission_owner is AdmissionOwner.LOG:
        global_blockers.add(finding.finding_id)
        return
    if finding.admission_owner is AdmissionOwner.ENTRY:
        for key in scope.keys_for_entry(finding.entry):
            blockers[key].add(finding.finding_id)
        return
    matches = scope.finding_matches(finding)
    if matches:
        for key in matches:
            blockers[key].add(finding.finding_id)
        return
    if (
        finding.admission_owner is None
        and finding.code not in _UNANCHORED_NONBLOCKING_CODES
    ):
        global_blockers.add(finding.finding_id)


def _blocks_reproduction(finding: Finding) -> bool:
    return finding.type is not RuleArea.ORPHAN


def _propagate_dependency_blockers(
    index: _GraphIndex,
    bound: Sequence[_BoundExecution],
    blockers: Mapping[ExecutionKey, set[str]],
) -> None:
    by_node = {item.execution.node_id: item.selected.key for item in bound}
    downstream_by_key: dict[ExecutionKey, tuple[ExecutionKey, ...]] = {}
    indegree = dict.fromkeys(blockers, 0)
    for source_node, targets in index.dependency_downstream.items():
        source = by_node.get(source_node)
        if source is None:
            continue
        downstream_keys = tuple(
            sorted(
                target
                for target_node in targets
                if (target := by_node.get(target_node)) is not None
            )
        )
        downstream_by_key[source] = downstream_keys
        for target in downstream_keys:
            indegree[target] += 1
    pending = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    visited = 0
    while pending:
        upstream = pending.popleft()
        visited += 1
        for downstream_key in downstream_by_key.get(upstream, ()):
            _merge_dependency_blockers(
                blockers[upstream],
                blockers[downstream_key],
            )
            indegree[downstream_key] -= 1
            if indegree[downstream_key] == 0:
                pending.append(downstream_key)
    if visited != len(blockers):
        _invalid("selected execution dependency graph contains a cycle")


def _merge_dependency_blockers(
    upstream: set[str],
    downstream: set[str],
) -> None:
    downstream.update(upstream)


class _GraphIndex:
    """Indexes used to bind and scope one admission decision."""

    def __init__(self, graph: ResearchGraph) -> None:
        self.nodes = {node.node_id: node for node in graph.nodes}
        self.adjacent: dict[str, list[ResearchEdge]] = {}
        self.ambiguities_by_candidate: dict[
            str, list[AmbiguityObservation]
        ] = defaultdict(list)
        self.members_by_collection: dict[str, set[str]] = defaultdict(set)
        self.dependency_upstream: dict[str, set[str]] = defaultdict(set)
        self.dependency_downstream: dict[str, tuple[str, ...]] = {}
        self.operational_executions_by_node: dict[str, set[str]] = defaultdict(set)
        self.command_nodes_by_repair_prefix: dict[str, str] = {}
        self.material_nodes_by_identity: dict[str, str] = {}
        for edge in graph.edges:
            self.adjacent.setdefault(edge.source, []).append(edge)
            self.adjacent.setdefault(edge.target, []).append(edge)
            if edge.kind is EdgeKind.MEMBERSHIP:
                self.members_by_collection[edge.source].add(edge.target)
            elif edge.kind is EdgeKind.EXECUTION_DEPENDENCY:
                self.dependency_upstream[edge.target].add(edge.source)
        for ambiguity in graph.ambiguities:
            for candidate in ambiguity.candidates:
                self.ambiguities_by_candidate[candidate].append(ambiguity)
        dependency_edges = tuple(
            edge
            for edge in graph.edges
            if edge.kind is EdgeKind.EXECUTION_DEPENDENCY
        )
        downstream: dict[str, set[str]] = defaultdict(set)
        for edge in dependency_edges:
            downstream[edge.source].add(edge.target)
        self.dependency_downstream = {
            source: tuple(sorted(targets))
            for source, targets in downstream.items()
        }
        self._index_operational_scope(graph.edges)

    def bind(self, selected: SelectedExecution) -> _BoundExecution:
        execution_id = ResearchNode(
            NodeKind.EXECUTION,
            selected.graph_identity,
            selected.entry,
        ).node_id
        execution = self.nodes.get(execution_id)
        if execution is None or execution.kind is not NodeKind.EXECUTION:
            _invalid(f"selected execution has no graph node: {selected.key!r}")
        bindings = {
            edge.source
            for edge in self.adjacent.get(execution_id, ())
            if edge.kind is EdgeKind.COMMAND_EXECUTION and edge.target == execution_id
        }
        if len(bindings) != 1:
            _invalid(
                f"selected execution has {len(bindings)} command bindings: "
                f"{selected.key!r}"
            )
        command = self.nodes[next(iter(bindings))]
        if command.kind is not NodeKind.COMMAND:
            _invalid(f"selected execution binding is not a command: {selected.key!r}")
        declared_outputs = self._command_output_materials(command.node_id)
        if declared_outputs != set(selected.outputs):
            _invalid(
                f"selected execution output binding is not exact: {selected.key!r}"
            )
        return _BoundExecution(
            selected,
            execution,
            command,
        )

    def _index_operational_scope(self, edges: Sequence[ResearchEdge]) -> None:
        for node in self.nodes.values():
            if node.kind is NodeKind.MATERIAL:
                self.material_nodes_by_identity[node.identity] = node.node_id
        for edge in edges:
            if edge.kind is not EdgeKind.COMMAND_EXECUTION:
                continue
            command = self.nodes[edge.source]
            execution = self.nodes[edge.target]
            self.command_nodes_by_repair_prefix[
                f"{command.entry}:{command.identity}"
            ] = command.node_id
            self.operational_executions_by_node[execution.node_id].add(
                execution.node_id
            )
            for node_id in self._command_neighbors(command.node_id):
                self.operational_executions_by_node[node_id].add(execution.node_id)

    def _command_output_materials(self, command_id: str) -> set[str]:
        outputs = {
            self.nodes[edge.target].identity
            for edge in self.adjacent.get(command_id, ())
            if edge.kind is EdgeKind.PRODUCTION
            and edge.source == command_id
            and self.nodes[edge.target].kind is NodeKind.MATERIAL
        }
        outputs.update(
            self.nodes[item.subject].identity
            for item in self.ambiguities_by_candidate.get(command_id, ())
            if item.kind is AmbiguityKind.REJECTED_COMMAND
            and self.nodes[item.subject].kind is NodeKind.MATERIAL
        )
        collection_ids = {
            edge.target
            for edge in self.adjacent.get(command_id, ())
            if edge.kind is EdgeKind.PRODUCTION
            and edge.source == command_id
            and self.nodes[edge.target].kind is NodeKind.COLLECTION
            and self.nodes[edge.target].attributes.get("mechanism") == "directory"
        }
        collection_ids.update(
            item.subject
            for item in self.ambiguities_by_candidate.get(command_id, ())
            if item.kind is AmbiguityKind.REJECTED_COMMAND
            and self.nodes[item.subject].kind is NodeKind.COLLECTION
            and self.nodes[item.subject].attributes.get("mechanism") == "directory"
        )
        collection_members = {
            member
            for collection in collection_ids
            for member in self.members_by_collection.get(collection, ())
        }
        return {
            identity
            for identity in outputs
            if ResearchNode(NodeKind.MATERIAL, identity).node_id
            not in collection_members
        }

    def _command_neighbors(self, command_id: str) -> set[str]:
        neighbors = {command_id}
        collections: set[str] = set()
        for edge in self.adjacent.get(command_id, ()):
            neighbors.update((edge.source, edge.target))
            other = edge.target if edge.source == command_id else edge.source
            if self.nodes[other].kind is NodeKind.COLLECTION:
                collections.add(other)
        for ambiguity in self.ambiguities_by_candidate.get(command_id, ()):
            neighbors.add(ambiguity.subject)
            neighbors.update(ambiguity.candidates)
            if self.nodes[ambiguity.subject].kind is NodeKind.COLLECTION:
                collections.add(ambiguity.subject)
        for collection in collections:
            for edge in self.adjacent.get(collection, ()):
                neighbors.update((edge.source, edge.target))
        return neighbors


class _AdmissionScopeIndex:
    """Resolve finding attachments without scanning the selected inventory."""

    def __init__(
        self,
        graph: _GraphIndex,
        bound: Sequence[_BoundExecution],
    ) -> None:
        self.graph = graph
        self.selected_by_execution = {
            item.execution.node_id: item.selected.key for item in bound
        }
        self.selected_by_execution_repair = {
            ":".join(item.selected.key): item.selected.key for item in bound
        }
        self.selected_by_command = {
            item.command.node_id: item.selected.key for item in bound
        }
        by_entry: dict[str, list[ExecutionKey]] = defaultdict(list)
        for item in bound:
            by_entry[item.selected.entry].append(item.selected.key)
        self.selected_by_entry = {
            entry: tuple(sorted(keys)) for entry, keys in by_entry.items()
        }
        self.node_matches: dict[str, frozenset[ExecutionKey]] = {}

    def keys_for_entry(self, entry: str | None) -> tuple[ExecutionKey, ...]:
        return self.selected_by_entry.get(entry or "", ())

    def finding_matches(self, finding: Finding) -> set[ExecutionKey]:
        matches: set[ExecutionKey] = set()
        for reference in finding.context_nodes:
            matches.update(self._keys_for_node(reference.node_id))
        for key in finding.repair_keys:
            matches.update(self._keys_for_repair(key.kind, key.identity))
        return matches

    def _keys_for_node(self, node_id: str) -> frozenset[ExecutionKey]:
        cached = self.node_matches.get(node_id)
        if cached is not None:
            return cached
        result = frozenset(
            selected
            for execution_id in self.graph.operational_executions_by_node.get(
                node_id, ()
            )
            if (selected := self.selected_by_execution.get(execution_id)) is not None
        )
        self.node_matches[node_id] = result
        return result

    def _keys_for_repair(
        self,
        kind: RepairKeyKind,
        identity: str,
    ) -> frozenset[ExecutionKey]:
        if kind is RepairKeyKind.EXECUTION:
            match = self.selected_by_execution_repair.get(identity)
            return frozenset((match,)) if match is not None else frozenset()
        if kind is RepairKeyKind.COMMAND:
            prefix = _longest_prefix_match(
                identity,
                self.graph.command_nodes_by_repair_prefix,
            )
            command = self.graph.command_nodes_by_repair_prefix.get(prefix, "")
            match = self.selected_by_command.get(command) if command else None
            return frozenset((match,)) if match is not None else frozenset()
        if kind is RepairKeyKind.MATERIAL:
            node_id = self.graph.material_nodes_by_identity.get(identity)
            return self._keys_for_node(node_id) if node_id else frozenset()
        if kind is RepairKeyKind.RECORD:
            matches: set[ExecutionKey] = set()
            for node_kind in (
                NodeKind.DATA_RECORD,
                NodeKind.EVIDENCE_RECORD,
                NodeKind.RETENTION_RECORD,
            ):
                for entry in _record_entries(identity):
                    node_id = ResearchNode(node_kind, identity, entry).node_id
                    if node_id in self.graph.nodes:
                        matches.update(self._keys_for_node(node_id))
            return frozenset(matches)
        if kind is RepairKeyKind.OWNERSHIP:
            material = _longest_prefix_match(
                identity,
                self.graph.material_nodes_by_identity,
            )
            node_id = self.graph.material_nodes_by_identity.get(material, "")
            return self._keys_for_node(node_id) if node_id else frozenset()
        return frozenset()


def _longest_prefix_match(identity: str, values: Mapping[str, str]) -> str:
    candidate = identity
    while candidate:
        if candidate in values:
            return candidate
        head, separator, _ = candidate.rpartition(":")
        if not separator:
            return ""
        candidate = head
    return ""


def _record_entries(identity: str) -> tuple[str, ...]:
    entry, separator, _ = identity.partition(":")
    return (entry,) if separator and entry else ()


def _invalid(message: str) -> NoReturn:
    raise ActionError("reproduction.validation.scope_unresolved", message)
