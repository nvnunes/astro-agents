"""Neutral bounded research graph shared by Validation and Reproduce."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .domain import GraphReference, ValidationDomainError


class NodeKind(str, Enum):
    """Closed research-graph node kinds."""

    ENTRY = "entry"
    DOCUMENT = "document"
    EVIDENCE_RECORD = "evidence_record"
    PRESENTATION = "presentation"
    DATA_RECORD = "data_record"
    RETENTION_RECORD = "retention_record"
    COMMAND = "command"
    EXECUTION = "execution"
    MATERIAL = "material"
    COLLECTION = "collection"
    SCRIPT = "script"
    CODE = "code"


class EdgeKind(str, Enum):
    """Closed mechanically established relationship kinds."""

    DECLARATION = "declaration"
    PRESENTATION = "presentation"
    COMMAND_EXECUTION = "command_execution"
    PRODUCTION = "production"
    CONSUMPTION = "consumption"
    ORIGIN = "origin"
    RETENTION = "retention"
    MEMBERSHIP = "membership"
    SCRIPT_USE = "script_use"
    CODE_USE = "code_use"
    EXECUTION_DEPENDENCY = "execution_dependency"


class AmbiguityKind(str, Enum):
    """Closed observations that are not established graph edges."""

    NO_PRODUCER = "no_producer"
    MULTIPLE_PRODUCERS = "multiple_producers"
    REJECTED_COMMAND = "rejected_command"
    UNRESOLVED_BINDING = "unresolved_binding"


@dataclass(frozen=True, order=True)
class ResearchNode:
    """One typed node with composite identity and bounded attributes."""

    kind: NodeKind
    identity: str
    entry: str | None = None
    attributes: Mapping[str, object] = field(
        default_factory=dict,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValidationDomainError("research graph node identity is empty")
        if self.entry is not None and not self.entry.strip():
            raise ValidationDomainError("research graph node entry is empty")
        object.__setattr__(
            self,
            "attributes",
            _frozen_attributes(self.attributes),
        )

    @property
    def node_id(self) -> str:
        owner = f":{self.entry}" if self.entry is not None else ""
        return f"{self.kind.value}{owner}:{self.identity}"

    @property
    def reference(self) -> GraphReference:
        return GraphReference(self.kind.value, self.identity, self.entry)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "attributes": _plain(self.attributes),
            "identity": self.identity,
            "kind": self.kind.value,
            "node_id": self.node_id,
        }
        if self.entry is not None:
            value["entry"] = self.entry
        return value


@dataclass(frozen=True, order=True)
class ResearchEdge:
    """One mechanically established directed relationship."""

    kind: EdgeKind
    source: str
    target: str

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.target.strip():
            raise ValidationDomainError("research graph edge endpoints are empty")

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "target": self.target,
        }


@dataclass(frozen=True, order=True)
class AmbiguityObservation:
    """A bounded candidate observation deliberately not promoted to an edge."""

    kind: AmbiguityKind
    subject: str
    candidates: tuple[str, ...]
    observed: Mapping[str, object] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValidationDomainError("graph ambiguity subject is empty")
        candidates = tuple(sorted(self.candidates))
        if len(candidates) != len(set(candidates)):
            raise ValidationDomainError("graph ambiguity candidates must be unique")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "observed", _frozen_attributes(self.observed))

    def as_dict(self) -> dict[str, object]:
        return {
            "candidates": list(self.candidates),
            "kind": self.kind.value,
            "observed": _plain(self.observed),
            "subject": self.subject,
        }


@dataclass(frozen=True)
class GraphLimitObservation:
    """The first deterministic bound reached while building a partial graph."""

    dimension: str
    observed: int
    limit: int

    def __post_init__(self) -> None:
        if self.dimension not in {"nodes", "edges", "ambiguities"}:
            raise ValidationDomainError("graph limit dimension is unsupported")
        if self.observed <= self.limit or self.limit < 1:
            raise ValidationDomainError("graph limit observation is inconsistent")

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "limit": self.limit,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class ResearchGraph:
    """One canonical bounded graph and any non-edge ambiguity observations."""

    nodes: tuple[ResearchNode, ...]
    edges: tuple[ResearchEdge, ...]
    ambiguities: tuple[AmbiguityObservation, ...]
    limit_observation: GraphLimitObservation | None = None

    def __post_init__(self) -> None:
        nodes = tuple(sorted(self.nodes, key=lambda item: item.node_id))
        edges = tuple(sorted(self.edges))
        ambiguities = tuple(sorted(self.ambiguities))
        node_ids = [node.node_id for node in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValidationDomainError("research graph node identities must be unique")
        if len(edges) != len(set(edges)):
            raise ValidationDomainError("research graph edges must be unique")
        if len(ambiguities) != len(set(ambiguities)):
            raise ValidationDomainError("research graph ambiguities must be unique")
        known = set(node_ids)
        if any(edge.source not in known or edge.target not in known for edge in edges):
            raise ValidationDomainError(
                "research graph edge references an unknown node"
            )
        if any(
            candidate not in known
            for ambiguity in ambiguities
            for candidate in ambiguity.candidates
        ):
            raise ValidationDomainError(
                "research graph ambiguity references an unknown candidate"
            )
        if any(ambiguity.subject not in known for ambiguity in ambiguities):
            raise ValidationDomainError(
                "research graph ambiguity references an unknown subject"
            )
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "ambiguities", ambiguities)

    @property
    def complete(self) -> bool:
        return self.limit_observation is None

    def node(self, node_id: str) -> ResearchNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "ambiguities": [item.as_dict() for item in self.ambiguities],
            "complete": self.complete,
            "edges": [item.as_dict() for item in self.edges],
            "limit_observation": (
                None
                if self.limit_observation is None
                else self.limit_observation.as_dict()
            ),
            "nodes": [item.as_dict() for item in self.nodes],
        }


class ResearchGraphBuilder:
    """Deterministic graph builder that retains a useful bounded prefix."""

    def __init__(
        self,
        *,
        max_nodes: int = 1_000_000,
        max_edges: int = 4_000_000,
        max_ambiguities: int = 1_000_000,
    ) -> None:
        if min(max_nodes, max_edges, max_ambiguities) < 1:
            raise ValidationDomainError("research graph bounds must be positive")
        self._max_nodes = max_nodes
        self._max_edges = max_edges
        self._max_ambiguities = max_ambiguities
        self._nodes: dict[str, ResearchNode] = {}
        self._edges: set[ResearchEdge] = set()
        self._ambiguities: set[AmbiguityObservation] = set()
        self._limit: GraphLimitObservation | None = None

    def add_node(self, node: ResearchNode) -> bool:
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            if existing != node or existing.attributes != node.attributes:
                raise ValidationDomainError(
                    f"research graph node conflicts with {node.node_id}"
                )
            return True
        if len(self._nodes) >= self._max_nodes:
            self._record_limit("nodes", len(self._nodes) + 1, self._max_nodes)
            return False
        self._nodes[node.node_id] = node
        return True

    def add_edge(self, edge: ResearchEdge) -> bool:
        if edge in self._edges:
            return True
        if edge.source not in self._nodes or edge.target not in self._nodes:
            if self._limit is not None:
                return False
            raise ValidationDomainError("edge endpoints must be added before the edge")
        if len(self._edges) >= self._max_edges:
            self._record_limit("edges", len(self._edges) + 1, self._max_edges)
            return False
        self._edges.add(edge)
        return True

    def has_node(self, node_id: str) -> bool:
        """Return whether an exact node has already been admitted."""

        return node_id in self._nodes

    def add_ambiguity(self, ambiguity: AmbiguityObservation) -> bool:
        if ambiguity in self._ambiguities:
            return True
        if ambiguity.subject not in self._nodes or any(
            candidate not in self._nodes for candidate in ambiguity.candidates
        ):
            if self._limit is not None:
                return False
            raise ValidationDomainError(
                "ambiguity subject and candidates must be added before the observation"
            )
        if len(self._ambiguities) >= self._max_ambiguities:
            self._record_limit(
                "ambiguities",
                len(self._ambiguities) + 1,
                self._max_ambiguities,
            )
            return False
        self._ambiguities.add(ambiguity)
        return True

    def build(self) -> ResearchGraph:
        return ResearchGraph(
            tuple(self._nodes.values()),
            tuple(self._edges),
            tuple(self._ambiguities),
            self._limit,
        )

    def _record_limit(self, dimension: str, observed: int, limit: int) -> None:
        if self._limit is None:
            self._limit = GraphLimitObservation(dimension, observed, limit)


@dataclass(frozen=True)
class ResearchGraphBounds:
    """Deterministic construction bounds for one graph."""

    nodes: int = 1_000_000
    edges: int = 4_000_000
    ambiguities: int = 1_000_000


@dataclass(frozen=True)
class EvaluationGraphInputs:
    """Already loaded observations required to assemble one research graph."""

    entries: tuple[object, ...]
    invocations: tuple[object, ...]
    evidence_connections: tuple[object, ...] = ()
    code_inputs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    execution_bindings: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    rejected_commands: tuple[Mapping[str, object], ...] = ()
    supported_output_directories: frozenset[str] = frozenset()
    rejected_invocations: tuple[object, ...] = ()


@dataclass
class _EvaluationGraphState:
    """Mutable indexes used only while assembling one research graph."""

    builder: ResearchGraphBuilder
    supported_output_directories: frozenset[str] = frozenset()
    entry_nodes: dict[str, ResearchNode] = field(default_factory=dict)
    data_records: dict[tuple[str, str], list[ResearchNode]] = field(
        default_factory=dict
    )
    command_nodes: dict[str, ResearchNode] = field(default_factory=dict)
    material_nodes: dict[str, ResearchNode] = field(default_factory=dict)
    outputs: dict[str, list[ResearchNode]] = field(default_factory=dict)
    output_collections: list[tuple[ResearchNode, ResearchNode, str]] = field(
        default_factory=list
    )
    consumers: dict[str, list[ResearchNode]] = field(default_factory=dict)
    execution_nodes: dict[tuple[str, str], list[ResearchNode]] = field(
        default_factory=dict
    )
    execution_by_command: dict[str, str] = field(default_factory=dict)

    def material(self, identity: str) -> ResearchNode:
        node = self.material_nodes.get(identity)
        if node is None:
            node = ResearchNode(NodeKind.MATERIAL, identity)
            self.builder.add_node(node)
            self.material_nodes[identity] = node
        return node


def build_evaluation_graph(
    inputs: EvaluationGraphInputs,
    *,
    bounds: ResearchGraphBounds = ResearchGraphBounds(),
) -> ResearchGraph:
    """Build one graph directly from already loaded evaluation observations."""

    builder = ResearchGraphBuilder(
        max_nodes=bounds.nodes,
        max_edges=bounds.edges,
        max_ambiguities=bounds.ambiguities,
    )
    state = _EvaluationGraphState(builder, inputs.supported_output_directories)
    _add_entries(inputs.entries, state)
    _add_rejected_invocations(inputs.rejected_invocations, state)
    _add_invocations(inputs.invocations, state)
    _add_evidence_connections(inputs.evidence_connections, state)
    _add_code_inputs(inputs.code_inputs, state)
    _add_rejected_commands(inputs.rejected_commands, state)
    _add_execution_bindings(inputs.execution_bindings, state)
    _add_producer_ambiguities(state)
    _add_execution_dependencies(state)
    return builder.build()


def extend_graph_materials(
    graph: ResearchGraph,
    identities: Sequence[str],
    *,
    bounds: ResearchGraphBounds = ResearchGraphBounds(),
) -> ResearchGraph:
    """Add observed material inventory without reconstructing graph topology."""

    if graph.limit_observation is not None:
        return graph
    builder = ResearchGraphBuilder(
        max_nodes=bounds.nodes,
        max_edges=bounds.edges,
        max_ambiguities=bounds.ambiguities,
    )
    for node in graph.nodes:
        builder.add_node(node)
    for edge in graph.edges:
        builder.add_edge(edge)
    for ambiguity in graph.ambiguities:
        builder.add_ambiguity(ambiguity)
    for identity in sorted(set(identities)):
        builder.add_node(ResearchNode(NodeKind.MATERIAL, identity))
    return builder.build()


def _add_entries(entries: Sequence[object], state: _EvaluationGraphState) -> None:
    for entry in entries:
        entry_id = str(getattr(entry, "entry_id"))
        entry_node = ResearchNode(NodeKind.ENTRY, entry_id)
        state.builder.add_node(entry_node)
        state.entry_nodes[entry_id] = entry_node
        document = Path(getattr(entry, "document")).as_posix()
        document_node = ResearchNode(NodeKind.DOCUMENT, document, entry_id)
        state.builder.add_node(document_node)
        state.builder.add_edge(
            ResearchEdge(
                EdgeKind.DECLARATION,
                entry_node.node_id,
                document_node.node_id,
            )
        )
        _add_data_records(entry, entry_node, state)
        _add_evidence_records(entry, entry_node, state)
        _add_retention_records(entry, entry_node, state)
        _add_execution_records(entry, entry_node, state)


def _add_data_records(
    entry: object,
    entry_node: ResearchNode,
    state: _EvaluationGraphState,
) -> None:
    data = getattr(entry, "data")
    if data is None:
        return
    for resource in getattr(data, "inputs"):
        name = str(getattr(resource, "name"))
        identity = f"{entry_node.identity}:{getattr(resource, 'name')}"
        owner = str(getattr(entry, "material_owner", entry_node.identity))
        record = ResearchNode(
            NodeKind.DATA_RECORD,
            identity,
            entry_node.identity,
            {
                "kind": str(getattr(resource, "kind")),
                "name": name,
                "owner": owner,
            },
        )
        state.builder.add_node(record)
        state.data_records.setdefault((owner, name), []).append(record)
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, entry_node.node_id, record.node_id)
        )
        target = state.material(str(getattr(resource, "canonical_target")))
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, record.node_id, target.node_id)
        )


def _add_evidence_records(
    entry: object,
    entry_node: ResearchNode,
    state: _EvaluationGraphState,
) -> None:
    evidence = getattr(entry, "evidence", None)
    if evidence is None:
        return
    for value in getattr(evidence, "records"):
        record = ResearchNode(
            NodeKind.EVIDENCE_RECORD,
            f"{entry_node.identity}:{getattr(value, 'id')}",
            entry_node.identity,
        )
        state.builder.add_node(record)
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, entry_node.node_id, record.node_id)
        )
        document = str(getattr(value, "document"))
        presentation = ResearchNode(
            NodeKind.PRESENTATION,
            f"{document}:{getattr(value, 'id')}",
        )
        state.builder.add_node(presentation)
        state.builder.add_edge(
            ResearchEdge(
                EdgeKind.PRESENTATION,
                record.node_id,
                presentation.node_id,
            )
        )


def _add_retention_records(
    entry: object,
    entry_node: ResearchNode,
    state: _EvaluationGraphState,
) -> None:
    retention = getattr(entry, "retention")
    if retention is None:
        return
    entry_root = Path(
        getattr(entry, "entry_root", Path(getattr(entry, "document")).parent)
    ).resolve()
    for value in getattr(retention, "records"):
        directory = getattr(value, "directory")
        record = ResearchNode(
            NodeKind.RETENTION_RECORD,
            f"{entry_node.identity}:{getattr(value, 'id')}",
            entry_node.identity,
            {
                "directory": directory is not None,
                "source": Path(
                    getattr(retention, "path", entry_root / "retention.json")
                ).as_posix(),
            },
        )
        state.builder.add_node(record)
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, entry_node.node_id, record.node_id)
        )
        paths = tuple(str(item) for item in getattr(value, "paths"))
        if directory is not None:
            paths = (*paths, str(directory))
        for path in paths:
            material = state.material((entry_root / path).resolve().as_posix())
            state.builder.add_edge(
                ResearchEdge(EdgeKind.RETENTION, record.node_id, material.node_id)
            )


def _add_execution_records(
    entry: object,
    entry_node: ResearchNode,
    state: _EvaluationGraphState,
) -> None:
    pyrun = getattr(entry, "pyrun")
    if pyrun is None:
        return
    for cid, identity, execution in getattr(pyrun, "execution_items")():
        node = ResearchNode(
            NodeKind.EXECUTION,
            f"{cid}:{identity}",
            entry_node.identity,
            {
                "auto_reproduce": bool(getattr(execution, "auto_reproduce")),
                "requires_reproduction": bool(
                    getattr(execution, "requires_reproduction")
                ),
            },
        )
        state.builder.add_node(node)
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, entry_node.node_id, node.node_id)
        )
        state.execution_nodes.setdefault(
            (entry_node.identity, str(cid)), []
        ).append(node)


def _add_invocations(
    invocations: Sequence[object], state: _EvaluationGraphState
) -> None:
    for invocation in invocations:
        _add_invocation(invocation, state)


def _add_rejected_invocations(
    invocations: Sequence[object],
    state: _EvaluationGraphState,
) -> None:
    """Represent observed but inadmissible commands without producer edges."""

    for invocation in invocations:
        _add_invocation(invocation, state, rejected=True)


def _add_invocation(
    invocation: object,
    state: _EvaluationGraphState,
    *,
    rejected: bool = False,
) -> None:
    entry_id = str(getattr(invocation, "entry"))
    attributes = {
        "cid": str(getattr(invocation, "cid")),
        "document": str(getattr(invocation, "document")),
        "fence": int(getattr(invocation, "fence")),
        "ordinal": int(getattr(invocation, "ordinal")),
        "sequence": int(getattr(invocation, "sequence", 0)),
    }
    if rejected:
        attributes["rejected"] = True
    command = ResearchNode(
        NodeKind.COMMAND,
        str(getattr(invocation, "identity")),
        entry_id,
        attributes,
    )
    state.builder.add_node(command)
    state.command_nodes[command.identity] = command
    declared_entry = state.entry_nodes.get(entry_id)
    if declared_entry is not None:
        state.builder.add_edge(
            ResearchEdge(EdgeKind.DECLARATION, declared_entry.node_id, command.node_id)
        )
    _add_invocation_materials(
        invocation,
        command,
        state,
        outputs_established=not rejected,
    )
    _add_invocation_collections(
        invocation,
        command,
        state,
        outputs_established=not rejected,
    )
    script = getattr(invocation, "script")
    if script is not None:
        script_node = ResearchNode(
            NodeKind.SCRIPT,
            Path(str(script)).resolve().as_posix(),
            entry_id,
        )
        state.builder.add_node(script_node)
        state.builder.add_edge(
            ResearchEdge(EdgeKind.SCRIPT_USE, script_node.node_id, command.node_id)
        )


def _add_invocation_collections(
    invocation: object,
    command: ResearchNode,
    state: _EvaluationGraphState,
    *,
    outputs_established: bool,
) -> None:
    for index, value in enumerate(getattr(invocation, "collections")):
        direction = str(getattr(value, "direction"))
        root = getattr(value, "root", None)
        collection = ResearchNode(
            NodeKind.COLLECTION,
            f"{command.identity}:{direction}:{index}:{getattr(value, 'target')}",
            command.entry,
            {
                "mechanism": str(getattr(value, "mechanism")),
                "root": None if root is None else str(root),
                "supported": root in state.supported_output_directories,
            },
        )
        state.builder.add_node(collection)
        relationship = (
            EdgeKind.PRODUCTION if direction == "output" else EdgeKind.CONSUMPTION
        )
        if direction == "output":
            if outputs_established:
                state.builder.add_edge(
                    ResearchEdge(relationship, command.node_id, collection.node_id)
                )
            else:
                state.builder.add_ambiguity(
                    AmbiguityObservation(
                        AmbiguityKind.REJECTED_COMMAND,
                        collection.node_id,
                        (command.node_id,),
                        {"reason": "failed_prerequisite"},
                    )
                )
            if root is not None:
                root_material = state.material(str(root))
                if outputs_established:
                    state.builder.add_edge(
                        ResearchEdge(
                            EdgeKind.PRODUCTION,
                            command.node_id,
                            root_material.node_id,
                        )
                    )
                    state.outputs.setdefault(root_material.identity, []).append(command)
                    state.output_collections.append(
                        (collection, command, root_material.identity)
                    )
                else:
                    state.builder.add_ambiguity(
                        AmbiguityObservation(
                            AmbiguityKind.REJECTED_COMMAND,
                            root_material.node_id,
                            (command.node_id,),
                            {"reason": "failed_prerequisite"},
                        )
                    )
        else:
            state.builder.add_edge(
                ResearchEdge(relationship, collection.node_id, command.node_id)
            )
        for member in getattr(value, "members"):
            material = state.material(str(member))
            state.builder.add_edge(
                ResearchEdge(EdgeKind.MEMBERSHIP, collection.node_id, material.node_id)
            )


def _add_invocation_materials(
    invocation: object,
    command: ResearchNode,
    state: _EvaluationGraphState,
    *,
    outputs_established: bool,
) -> None:
    relationships = (
        *getattr(invocation, "inputs"),
        *getattr(invocation, "outputs"),
    )
    for relationship in relationships:
        resource = getattr(relationship, "input_resource", None)
        if resource is None:
            continue
        owner = str(getattr(invocation, "material_owner", command.entry))
        name = str(getattr(resource, "name"))
        for record in state.data_records.get((owner, name), ()):
            state.builder.add_edge(
                ResearchEdge(EdgeKind.DECLARATION, record.node_id, command.node_id)
            )
    for relationship in getattr(invocation, "inputs"):
        material = state.material(str(getattr(relationship, "path")))
        kind = (
            EdgeKind.ORIGIN
            if bool(getattr(relationship, "origin"))
            else EdgeKind.CONSUMPTION
        )
        state.builder.add_edge(ResearchEdge(kind, material.node_id, command.node_id))
        if kind is EdgeKind.CONSUMPTION:
            state.consumers.setdefault(material.identity, []).append(command)
    for relationship in getattr(invocation, "outputs"):
        material = state.material(str(getattr(relationship, "path")))
        if outputs_established:
            state.builder.add_edge(
                ResearchEdge(EdgeKind.PRODUCTION, command.node_id, material.node_id)
            )
            state.outputs.setdefault(material.identity, []).append(command)
        else:
            state.builder.add_ambiguity(
                AmbiguityObservation(
                    AmbiguityKind.REJECTED_COMMAND,
                    material.node_id,
                    (command.node_id,),
                    {"reason": "failed_prerequisite"},
                )
            )


def _add_evidence_connections(
    connections: Sequence[object],
    state: _EvaluationGraphState,
) -> None:
    for value in connections:
        entry = str(getattr(value, "entry"))
        record = ResearchNode(
            NodeKind.EVIDENCE_RECORD,
            f"{entry}:{getattr(value, 'record')}",
            entry,
        )
        presentation = ResearchNode(
            NodeKind.PRESENTATION,
            str(getattr(value, "presentation")),
        )
        state.builder.add_node(record)
        state.builder.add_node(presentation)
        entry_node = state.entry_nodes.get(entry)
        if entry_node is not None:
            state.builder.add_edge(
                ResearchEdge(EdgeKind.DECLARATION, entry_node.node_id, record.node_id)
            )
        state.builder.add_edge(
            ResearchEdge(EdgeKind.PRESENTATION, record.node_id, presentation.node_id)
        )
        input_names = tuple(
            str(name) for name in getattr(value, "input_names", ())
        )
        explicit_owner = getattr(value, "owner", None)
        owner = str(
            explicit_owner
            or (input_names[0].rsplit(":", 1)[0] if input_names else entry)
        )
        for input_name in input_names:
            prefix = owner + ":"
            if input_name.startswith(prefix):
                input_name = input_name.removeprefix(prefix)
            for declaration in state.data_records.get((owner, input_name), ()):
                state.builder.add_edge(
                    ResearchEdge(
                        EdgeKind.DECLARATION,
                        declaration.node_id,
                        record.node_id,
                    )
                )
        origins = set(getattr(value, "origin_materials"))
        for path in getattr(value, "materials"):
            material = state.material(str(path))
            relationship = (
                EdgeKind.ORIGIN if path in origins else EdgeKind.DECLARATION
            )
            state.builder.add_edge(
                ResearchEdge(relationship, record.node_id, material.node_id)
            )


def _add_code_inputs(
    code_inputs: Mapping[str, tuple[str, ...]],
    state: _EvaluationGraphState,
) -> None:
    for command_identity, paths in sorted(code_inputs.items()):
        command = state.command_nodes.get(command_identity)
        if command is None:
            continue
        for path in paths:
            code = ResearchNode(
                NodeKind.CODE,
                Path(path).resolve().as_posix(),
                command.entry,
            )
            state.builder.add_node(code)
            state.builder.add_edge(
                ResearchEdge(EdgeKind.CODE_USE, code.node_id, command.node_id)
            )


def _add_execution_bindings(
    bindings: Mapping[str, tuple[str, str]],
    state: _EvaluationGraphState,
) -> None:
    for command_identity, (entry, execution_identity) in sorted(bindings.items()):
        command = state.command_nodes.get(command_identity)
        if command is None:
            continue
        execution_id = ResearchNode(
            NodeKind.EXECUTION,
            execution_identity,
            entry,
        ).node_id
        if state.builder.has_node(execution_id):
            state.builder.add_edge(
                ResearchEdge(
                    EdgeKind.COMMAND_EXECUTION,
                    command.node_id,
                    execution_id,
                )
            )
            state.execution_by_command[command.node_id] = execution_id
    _add_unresolved_bindings(state)


def _add_rejected_commands(
    commands: Sequence[Mapping[str, object]],
    state: _EvaluationGraphState,
) -> None:
    """Retain rejected producer candidates as observations, never graph edges."""

    for value in commands:
        entry = str(value["entry"])
        fence = _integer(value["fence"], "rejected command fence")
        ordinal = _integer(value["ordinal"], "rejected command ordinal")
        command = ResearchNode(
            NodeKind.COMMAND,
            str(value["identity"]),
            entry,
            {
                "code": str(value["code"]),
                "document": str(value["document"]),
                "fence": fence,
                "ordinal": ordinal,
                "rejected": True,
            },
        )
        state.builder.add_node(command)
        outputs = value.get("declared_outputs", ())
        if not isinstance(outputs, (tuple, list)):
            raise ValidationDomainError(
                "rejected command outputs must be a sequence"
            )
        for output in outputs:
            assert isinstance(output, Mapping)
            material = state.material(str(output["path"]))
            state.builder.add_ambiguity(
                AmbiguityObservation(
                    AmbiguityKind.REJECTED_COMMAND,
                    material.node_id,
                    (command.node_id,),
                    {"code": str(value["code"])},
                )
            )


def _add_unresolved_bindings(state: _EvaluationGraphState) -> None:
    """Record command/execution candidates that did not establish a binding."""

    commands_by_entry_cid: dict[tuple[str, str], list[ResearchNode]] = {}
    for command in state.command_nodes.values():
        if command.entry is None:
            continue
        commands_by_entry_cid.setdefault(
            (command.entry, str(command.attributes["cid"])), []
        ).append(command)
        if command.node_id not in state.execution_by_command:
            candidates = state.execution_nodes.get(
                (command.entry, str(command.attributes["cid"])), []
            )
            if candidates:
                state.builder.add_ambiguity(
                    AmbiguityObservation(
                        AmbiguityKind.UNRESOLVED_BINDING,
                        command.node_id,
                        tuple(item.node_id for item in candidates),
                        {"candidate_count": len(candidates)},
                    )
                )
    bound_executions = set(state.execution_by_command.values())
    for key, executions in state.execution_nodes.items():
        candidates = commands_by_entry_cid.get(key, [])
        for execution in executions:
            if execution.node_id in bound_executions:
                continue
            state.builder.add_ambiguity(
                AmbiguityObservation(
                    AmbiguityKind.UNRESOLVED_BINDING,
                    execution.node_id,
                    tuple(item.node_id for item in candidates),
                    {"candidate_count": len(candidates)},
                )
            )


def _add_producer_ambiguities(state: _EvaluationGraphState) -> None:
    for material_identity, recorded in sorted(state.outputs.items()):
        producers = _unique_nodes(recorded)
        if len(producers) > 1:
            state.builder.add_ambiguity(
                AmbiguityObservation(
                    AmbiguityKind.MULTIPLE_PRODUCERS,
                    state.material(material_identity).node_id,
                    tuple(item.node_id for item in producers),
                    {"candidate_count": len(producers)},
                )
            )
    _add_collection_ownership_ambiguities(state)
    for material_identity, recorded in sorted(state.consumers.items()):
        consumers = _unique_nodes(recorded)
        if material_identity in state.outputs:
            continue
        material = state.material(material_identity)
        state.builder.add_ambiguity(
            AmbiguityObservation(
                AmbiguityKind.NO_PRODUCER,
                material.node_id,
                tuple(item.node_id for item in consumers),
                {"consumer_count": len(consumers)},
            )
        )


def _add_collection_ownership_ambiguities(state: _EvaluationGraphState) -> None:
    ordered_outputs = tuple(sorted(state.outputs))
    for collection, owner, root in state.output_collections:
        candidates = _overlapping_producer_ids(root, ordered_outputs, state.outputs)
        candidates.add(owner.node_id)
        if len(candidates) < 2:
            continue
        state.builder.add_ambiguity(
            AmbiguityObservation(
                AmbiguityKind.MULTIPLE_PRODUCERS,
                collection.node_id,
                tuple(sorted(candidates)),
                {"candidate_count": len(candidates), "root": root},
            )
        )


def _overlapping_producer_ids(
    root: str,
    ordered_outputs: Sequence[str],
    producers: Mapping[str, Sequence[ResearchNode]],
) -> set[str]:
    """Return producers on the root, an ancestor, or a descendant path."""

    overlapping = {
        node.node_id
        for ancestor in (Path(root), *Path(root).parents)
        for node in producers.get(ancestor.as_posix(), ())
    }
    prefix = root.rstrip("/") + "/"
    index = bisect_left(ordered_outputs, prefix)
    while index < len(ordered_outputs):
        output = ordered_outputs[index]
        if not output.startswith(prefix):
            break
        overlapping.update(node.node_id for node in producers[output])
        index += 1
    return overlapping


def _add_execution_dependencies(state: _EvaluationGraphState) -> None:
    """Promote only unique, ordered command dependencies to execution edges."""

    for material_identity, recorded in sorted(state.consumers.items()):
        consumers = _unique_nodes(recorded)
        producers = _unique_nodes(state.outputs.get(material_identity, []))
        if len(producers) != 1:
            continue
        producer = producers[0]
        producer_execution = state.execution_by_command.get(producer.node_id)
        if producer_execution is None:
            continue
        producer_sequence = _integer(
            producer.attributes["sequence"], "producer sequence"
        )
        for consumer in consumers:
            consumer_execution = state.execution_by_command.get(consumer.node_id)
            if (
                consumer_execution is None
                or producer_sequence
                >= _integer(consumer.attributes["sequence"], "consumer sequence")
            ):
                continue
            state.builder.add_edge(
                ResearchEdge(
                    EdgeKind.EXECUTION_DEPENDENCY,
                    producer_execution,
                    consumer_execution,
                )
            )


def _unique_nodes(nodes: Sequence[ResearchNode]) -> tuple[ResearchNode, ...]:
    by_id = {node.node_id: node for node in nodes}
    return tuple(by_id[node_id] for node_id in sorted(by_id))


def _frozen_attributes(value: Mapping[str, object]) -> Mapping[str, object]:
    if not all(isinstance(key, str) for key in value):
        raise ValidationDomainError("research graph attributes require string keys")
    return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationDomainError(f"{field} must be an integer")
    return value


def _freeze(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return _frozen_attributes(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    raise ValidationDomainError("research graph attributes must contain JSON values")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value
