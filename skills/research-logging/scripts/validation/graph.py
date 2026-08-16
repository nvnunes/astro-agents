"""Typed dependency graph contracts for research-log validation.

The graph is the canonical owner of local material relationships used by
provenance, orphan discovery, and incremental invalidation. This module
contains no research-log discovery or report-rendering behavior.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple


class GraphContractError(ValueError):
    """Raised when dependency facts violate the graph contract."""


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphContractError(f"{description} must be an object")
    return value


def _require_string(value: Any, description: str) -> str:
    if not isinstance(value, str):
        raise GraphContractError(f"{description} must be a string")
    return value


def _require_list(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise GraphContractError(f"{description} must be a list")
    return value


class NodeKind(str, enum.Enum):
    """Kinds of material and control nodes represented in the graph."""

    PRESENTED = "presented"
    ARTIFACT = "artifact"
    COLLECTION = "collection"
    MEMBER = "member"
    INVOCATION = "invocation"
    SCRIPT = "script"
    INDEXED_INPUT = "indexed-input"
    EXTERNAL_SOURCE = "external-source"
    ENTRY = "entry"
    LOG = "log"


class EdgeKind(str, enum.Enum):
    """Kinds of directed dependency facts represented in the graph."""

    SUPPORTS = "supports"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    INVOKES = "invokes"
    DEPENDS_ON_CODE = "depends-on-code"
    CAPTURES = "captures"
    MEMBER_OF = "member-of"
    RESOLVES_TO = "resolves-to"
    SELECTED_PRODUCER = "selected-producer"
    OWNED_BY = "owned-by"
    BELONGS_TO_LOG = "belongs-to-log"


class OriginKind(str, enum.Enum):
    """Whether a fact was discovered mechanically or accepted semantically."""

    MECHANICAL = "mechanical"
    SEMANTIC = "semantic"


class RootPolicy(str, enum.Enum):
    """Purpose-specific traversal policies for dependency roots."""

    PRESENTED = "presented"
    RECORDED_COMMAND = "recorded-command"
    RETENTION = "retention"


@dataclass(frozen=True, order=True)
class NodeKey:
    """Stable identity for one graph node.

    ``namespace`` is a maintained-log key or an explicit external namespace.
    ``identity`` is kind-specific and must already be normalized.
    """

    namespace: str
    kind: NodeKind
    identity: str

    def __post_init__(self) -> None:
        if not self.namespace.strip():
            raise GraphContractError("node namespace must not be empty")
        if not self.identity.strip():
            raise GraphContractError("node identity must not be empty")

    def as_dict(self) -> Dict[str, str]:
        """Return the stable serialized form of this node key."""

        return {
            "namespace": self.namespace,
            "kind": self.kind.value,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "NodeKey":
        """Load a node key from its exact serialized contract."""

        value = _require_mapping(value, "node key")
        if set(value) != {"namespace", "kind", "identity"}:
            raise GraphContractError("node key has incorrect fields")
        try:
            return cls(
                namespace=_require_string(value["namespace"], "node namespace"),
                kind=NodeKind(value["kind"]),
                identity=_require_string(value["identity"], "node identity"),
            )
        except (TypeError, ValueError) as exc:
            raise GraphContractError(f"invalid node key: {exc}") from exc


@dataclass(frozen=True, order=True)
class OriginInput:
    """One material identity that establishes or invalidates a graph fact."""

    identity: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise GraphContractError("origin input identity must not be empty")
        if not self.fingerprint.strip():
            raise GraphContractError("origin input fingerprint must not be empty")

    def as_dict(self) -> Dict[str, str]:
        """Return the serialized material identity."""

        return {"identity": self.identity, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, value: Any) -> "OriginInput":
        """Load one exact persisted origin input."""

        value = _require_mapping(value, "origin input")
        if set(value) != {"identity", "fingerprint"}:
            raise GraphContractError("origin input has incorrect fields")
        return cls(
            _require_string(value["identity"], "origin input identity"),
            _require_string(value["fingerprint"], "origin input fingerprint"),
        )


@dataclass(frozen=True, order=True)
class FactOrigin:
    """Discovery or review origin for a graph fact.

    Semantic origins must name the reviewed scope and the complete material
    inputs that invalidate the accepted fact. Agent reasoning is deliberately
    not part of this contract.
    """

    kind: OriginKind
    resolver: str
    inputs: Tuple[OriginInput, ...]
    rules_version: str
    reviewed_scope: str = ""

    def __post_init__(self) -> None:
        if not self.resolver.strip():
            raise GraphContractError("fact origin resolver must not be empty")
        if not self.inputs:
            raise GraphContractError("fact origin must name at least one input")
        if not self.rules_version.strip():
            raise GraphContractError("fact origin rules version must not be empty")
        if self.kind is OriginKind.SEMANTIC and not self.reviewed_scope.strip():
            raise GraphContractError("semantic origin must name its reviewed scope")
        if self.kind is OriginKind.MECHANICAL and self.reviewed_scope:
            raise GraphContractError("mechanical origin cannot name a reviewed scope")

    def as_dict(self) -> Dict[str, Any]:
        """Return the exact persisted form of this origin."""

        result: Dict[str, Any] = {
            "kind": self.kind.value,
            "resolver": self.resolver,
            "inputs": [item.as_dict() for item in self.inputs],
            "rules_version": self.rules_version,
        }
        if self.reviewed_scope:
            result["reviewed_scope"] = self.reviewed_scope
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "FactOrigin":
        """Load one exact persisted fact origin."""

        value = _require_mapping(value, "fact origin")
        allowed = {"kind", "resolver", "inputs", "rules_version", "reviewed_scope"}
        if set(value) - allowed or not {
            "kind",
            "resolver",
            "inputs",
            "rules_version",
        } <= set(value):
            raise GraphContractError("fact origin has incorrect fields")
        try:
            return cls(
                kind=OriginKind(value["kind"]),
                resolver=_require_string(value["resolver"], "fact origin resolver"),
                inputs=tuple(
                    OriginInput.from_dict(item)
                    for item in _require_list(value["inputs"], "fact origin inputs")
                ),
                rules_version=_require_string(
                    value["rules_version"], "fact origin rules version"
                ),
                reviewed_scope=_require_string(
                    value.get("reviewed_scope", ""), "fact origin reviewed scope"
                ),
            )
        except (TypeError, ValueError) as exc:
            raise GraphContractError(f"invalid fact origin: {exc}") from exc


def _attribute_tuple(value: Mapping[str, Any] | None) -> Tuple[Tuple[str, str], ...]:
    """Normalize small graph attributes into an immutable JSON representation."""

    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise GraphContractError("graph attributes must be an object")
    result = []
    for key, item in sorted(value.items()):
        if not isinstance(key, str) or not key:
            raise GraphContractError("graph attribute keys must be nonempty strings")
        try:
            encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise GraphContractError(
                f"graph attribute is not JSON serializable: {key}"
            ) from exc
        result.append((key, encoded))
    return tuple(result)


def _attribute_dict(value: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    return {key: json.loads(item) for key, item in value}


@dataclass(frozen=True, order=True)
class GraphNode:
    """One immutable graph node with all known fact origins."""

    key: NodeKey
    origins: Tuple[FactOrigin, ...]
    attributes: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.origins:
            raise GraphContractError("graph node must have at least one origin")

    def as_dict(self) -> Dict[str, Any]:
        """Return the deterministic serialized node."""

        return {
            "key": self.key.as_dict(),
            "origins": [item.as_dict() for item in self.origins],
            "attributes": _attribute_dict(self.attributes),
        }

    def attribute(self, name: str, default: Any = None) -> Any:
        """Return one decoded node attribute."""

        return _attribute_dict(self.attributes).get(name, default)

    @classmethod
    def from_dict(cls, value: Any) -> "GraphNode":
        """Load one exact persisted graph node."""

        value = _require_mapping(value, "graph node")
        if set(value) != {"key", "origins", "attributes"}:
            raise GraphContractError("graph node has incorrect fields")
        return cls(
            key=NodeKey.from_dict(value["key"]),
            origins=tuple(
                FactOrigin.from_dict(item)
                for item in _require_list(value["origins"], "graph node origins")
            ),
            attributes=_attribute_tuple(value["attributes"]),
        )


@dataclass(frozen=True, order=True)
class GraphEdge:
    """One immutable directed dependency relation."""

    kind: EdgeKind
    source: NodeKey
    target: NodeKey
    owner_log: str
    origins: Tuple[FactOrigin, ...]
    attributes: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise GraphContractError("graph edge cannot connect a node to itself")
        if not self.owner_log.strip():
            raise GraphContractError("graph edge owner log must not be empty")
        if not self.origins:
            raise GraphContractError("graph edge must have at least one origin")

    @property
    def identity(self) -> str:
        """Return a stable content identity excluding discovery provenance."""

        payload = {
            "kind": self.kind.value,
            "source": self.source.as_dict(),
            "target": self.target.as_dict(),
            "owner_log": self.owner_log,
            "attributes": _attribute_dict(self.attributes),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


    def as_dict(self) -> Dict[str, Any]:
        """Return the deterministic serialized edge."""

        return {
            "identity": self.identity,
            "kind": self.kind.value,
            "source": self.source.as_dict(),
            "target": self.target.as_dict(),
            "owner_log": self.owner_log,
            "origins": [item.as_dict() for item in self.origins],
            "attributes": _attribute_dict(self.attributes),
        }

    def attribute(self, name: str, default: Any = None) -> Any:
        """Return one decoded edge attribute."""

        return _attribute_dict(self.attributes).get(name, default)

    @classmethod
    def from_dict(cls, value: Any) -> "GraphEdge":
        """Load and verify one exact persisted graph edge."""

        value = _require_mapping(value, "graph edge")
        if set(value) != {
            "identity",
            "kind",
            "source",
            "target",
            "owner_log",
            "origins",
            "attributes",
        }:
            raise GraphContractError("graph edge has incorrect fields")
        try:
            edge = cls(
                kind=EdgeKind(value["kind"]),
                source=NodeKey.from_dict(value["source"]),
                target=NodeKey.from_dict(value["target"]),
                owner_log=_require_string(value["owner_log"], "graph edge owner log"),
                origins=tuple(
                    FactOrigin.from_dict(item)
                    for item in _require_list(value["origins"], "graph edge origins")
                ),
                attributes=_attribute_tuple(value["attributes"]),
            )
        except (TypeError, ValueError) as exc:
            raise GraphContractError(f"invalid graph edge: {exc}") from exc
        _validate_edge_domain(edge.kind, edge.source, edge.target)
        identity = _require_string(value["identity"], "graph edge identity")
        if identity != edge.identity:
            raise GraphContractError("graph edge identity does not match its content")
        return edge


def _validate_edge_domain(kind: EdgeKind, source: NodeKey, target: NodeKey) -> None:
    """Reject edge directions that contradict the canonical graph model."""

    material = {
        NodeKind.ARTIFACT,
        NodeKind.COLLECTION,
        NodeKind.MEMBER,
        NodeKind.INDEXED_INPUT,
        NodeKind.EXTERNAL_SOURCE,
    }
    valid = {
        EdgeKind.SUPPORTS: source.kind in material | {NodeKind.ENTRY}
        and target.kind is NodeKind.PRESENTED,
        EdgeKind.PRODUCES: source.kind is NodeKind.INVOCATION
        and target.kind in material - {NodeKind.INDEXED_INPUT},
        EdgeKind.CONSUMES: source.kind is NodeKind.INVOCATION
        and target.kind in material,
        EdgeKind.INVOKES: source.kind is NodeKind.INVOCATION
        and target.kind is NodeKind.SCRIPT,
        EdgeKind.DEPENDS_ON_CODE: source.kind is NodeKind.SCRIPT
        and target.kind is NodeKind.SCRIPT,
        EdgeKind.CAPTURES: source.kind is NodeKind.INVOCATION
        and target.kind is NodeKind.ARTIFACT,
        EdgeKind.MEMBER_OF: source.kind in {NodeKind.ARTIFACT, NodeKind.MEMBER}
        and target.kind is NodeKind.COLLECTION,
        EdgeKind.RESOLVES_TO: source.kind is NodeKind.INDEXED_INPUT
        and target.kind in material - {NodeKind.INDEXED_INPUT},
        EdgeKind.SELECTED_PRODUCER: source.kind in material - {NodeKind.INDEXED_INPUT}
        and target.kind is NodeKind.INVOCATION,
        EdgeKind.OWNED_BY: source.kind not in {NodeKind.PRESENTED, NodeKind.LOG}
        and target.kind is NodeKind.ENTRY,
        EdgeKind.BELONGS_TO_LOG: source.kind is not NodeKind.LOG
        and target.kind is NodeKind.LOG,
    }[kind]
    if not valid:
        raise GraphContractError(
            f"invalid {kind.value} edge domain: {source.kind.value} -> "
            f"{target.kind.value}"
        )


@dataclass(frozen=True, order=True)
class GraphRoot:
    """One node selected under a purpose-specific reachability policy."""

    node: NodeKey
    policy: RootPolicy
    origins: Tuple[FactOrigin, ...]

    def __post_init__(self) -> None:
        if not self.origins:
            raise GraphContractError("graph root must have at least one origin")

    def as_dict(self) -> Dict[str, Any]:
        """Return the deterministic serialized root."""

        return {
            "node": self.node.as_dict(),
            "policy": self.policy.value,
            "origins": [origin.as_dict() for origin in self.origins],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "GraphRoot":
        """Load one exact persisted graph root."""

        value = _require_mapping(value, "graph root")
        if set(value) != {"node", "policy", "origins"}:
            raise GraphContractError("graph root has incorrect fields")
        try:
            return cls(
                node=NodeKey.from_dict(value["node"]),
                policy=RootPolicy(value["policy"]),
                origins=tuple(
                    FactOrigin.from_dict(item)
                    for item in _require_list(value["origins"], "graph root origins")
                ),
            )
        except (TypeError, ValueError) as exc:
            raise GraphContractError(f"invalid graph root: {exc}") from exc


@dataclass(frozen=True)
class DependencyGraph:
    """Immutable dependency graph used by every validation query."""

    rules_version: str
    nodes: Tuple[GraphNode, ...]
    edges: Tuple[GraphEdge, ...]
    roots: Tuple[GraphRoot, ...]
    _nodes_by_key: Mapping[NodeKey, GraphNode] = field(init=False, repr=False)
    _outgoing_by_key: Mapping[NodeKey, Tuple[GraphEdge, ...]] = field(
        init=False, repr=False
    )
    _incoming_by_key: Mapping[NodeKey, Tuple[GraphEdge, ...]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not self.rules_version.strip():
            raise GraphContractError("graph rules version must not be empty")
        nodes_by_key = {node.key: node for node in self.nodes}
        if len(nodes_by_key) != len(self.nodes):
            raise GraphContractError("graph contains duplicate node keys")
        edge_ids = {edge.identity for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise GraphContractError("graph contains duplicate edge identities")
        for edge in self.edges:
            if edge.source not in nodes_by_key or edge.target not in nodes_by_key:
                raise GraphContractError("graph edge references an unknown node")
        for root in self.roots:
            if root.node not in nodes_by_key:
                raise GraphContractError("graph root references an unknown node")
        outgoing: Dict[NodeKey, list[GraphEdge]] = {key: [] for key in nodes_by_key}
        incoming: Dict[NodeKey, list[GraphEdge]] = {key: [] for key in nodes_by_key}
        for edge in self.edges:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
        object.__setattr__(self, "_nodes_by_key", nodes_by_key)
        object.__setattr__(
            self,
            "_outgoing_by_key",
            {key: tuple(value) for key, value in outgoing.items()},
        )
        object.__setattr__(
            self,
            "_incoming_by_key",
            {key: tuple(value) for key, value in incoming.items()},
        )

    def node(self, key: NodeKey) -> GraphNode:
        """Return a node by stable key or raise ``KeyError``."""

        return self._nodes_by_key[key]

    def outgoing(
        self, key: NodeKey, kinds: Iterable[EdgeKind] | None = None
    ) -> Tuple[GraphEdge, ...]:
        """Return sorted outgoing edges, optionally restricted by kind."""

        allowed = set(kinds) if kinds is not None else None
        return tuple(
            edge
            for edge in self._outgoing_by_key.get(key, ())
            if allowed is None or edge.kind in allowed
        )

    def incoming(
        self, key: NodeKey, kinds: Iterable[EdgeKind] | None = None
    ) -> Tuple[GraphEdge, ...]:
        """Return sorted incoming edges, optionally restricted by kind."""

        allowed = set(kinds) if kinds is not None else None
        return tuple(
            edge
            for edge in self._incoming_by_key.get(key, ())
            if allowed is None or edge.kind in allowed
        )

    @property
    def identity(self) -> str:
        """Return a deterministic identity for the complete graph value."""

        payload = self.as_dict()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        """Return the deterministic serialized graph contract."""

        return {
            "rules_version": self.rules_version,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "roots": [root.as_dict() for root in self.roots],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "DependencyGraph":
        """Load and validate the exact persisted graph contract."""

        value = _require_mapping(value, "dependency graph")
        if set(value) != {"rules_version", "nodes", "edges", "roots"}:
            raise GraphContractError("dependency graph has incorrect fields")
        return cls(
            rules_version=_require_string(
                value["rules_version"], "dependency graph rules version"
            ),
            nodes=tuple(
                GraphNode.from_dict(item)
                for item in _require_list(value["nodes"], "dependency graph nodes")
            ),
            edges=tuple(
                GraphEdge.from_dict(item)
                for item in _require_list(value["edges"], "dependency graph edges")
            ),
            roots=tuple(
                GraphRoot.from_dict(item)
                for item in _require_list(value["roots"], "dependency graph roots")
            ),
        )


class GraphBuilder:
    """Validate and merge graph facts before producing an immutable graph."""

    def __init__(self, rules_version: str) -> None:
        if not rules_version.strip():
            raise GraphContractError("graph rules version must not be empty")
        self.rules_version = rules_version
        self._nodes: Dict[NodeKey, Dict[str, Any]] = {}
        self._edges: Dict[
            Tuple[EdgeKind, NodeKey, NodeKey, str, Tuple[Tuple[str, str], ...]],
            Dict[str, Any],
        ] = {}
        self._roots: Dict[Tuple[NodeKey, RootPolicy], set[FactOrigin]] = {}

    def add_node(
        self,
        key: NodeKey,
        origin: FactOrigin,
        attributes: Mapping[str, Any] | None = None,
    ) -> NodeKey:
        """Add or merge one node fact and return its key."""

        normalized = _attribute_tuple(attributes)
        current = self._nodes.get(key)
        if current is None:
            self._nodes[key] = {"attributes": normalized, "origins": {origin}}
        else:
            if current["attributes"] != normalized:
                raise GraphContractError(f"conflicting attributes for node: {key}")
            current["origins"].add(origin)
        return key

    def has_node(self, key: NodeKey) -> bool:
        """Return whether the mutable graph already contains ``key``."""

        return key in self._nodes

    def add_edge(
        self,
        kind: EdgeKind,
        source: NodeKey,
        target: NodeKey,
        owner_log: str,
        origin: FactOrigin,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Add or merge one edge fact between existing nodes."""

        if source not in self._nodes or target not in self._nodes:
            raise GraphContractError("add both edge nodes before adding the edge")
        _validate_edge_domain(kind, source, target)
        normalized = _attribute_tuple(attributes)
        key = (kind, source, target, owner_log, normalized)
        current = self._edges.get(key)
        if current is None:
            self._edges[key] = {"origins": {origin}}
        else:
            current["origins"].add(origin)

    def add_root(self, node: NodeKey, policy: RootPolicy, origin: FactOrigin) -> None:
        """Add one purpose-specific root for an existing node."""

        if node not in self._nodes:
            raise GraphContractError("add the root node before adding the root")
        key = (node, policy)
        self._roots.setdefault(key, set()).add(origin)

    def build(self) -> DependencyGraph:
        """Return a deterministic immutable graph from all accepted facts."""

        nodes = tuple(
            GraphNode(
                key=key,
                origins=tuple(sorted(value["origins"])),
                attributes=value["attributes"],
            )
            for key, value in sorted(self._nodes.items())
        )
        edges = tuple(
            sorted(
                (
                    GraphEdge(
                        kind=kind,
                        source=source,
                        target=target,
                        owner_log=owner_log,
                        origins=tuple(sorted(value["origins"])),
                        attributes=attributes,
                    )
                    for (
                        kind,
                        source,
                        target,
                        owner_log,
                        attributes,
                    ), value in self._edges.items()
                ),
                key=lambda edge: edge.identity,
            )
        )
        roots = tuple(
            GraphRoot(node=node, policy=policy, origins=tuple(sorted(origins)))
            for (node, policy), origins in sorted(self._roots.items())
        )
        return DependencyGraph(
            rules_version=self.rules_version,
            nodes=nodes,
            edges=edges,
            roots=roots,
        )
