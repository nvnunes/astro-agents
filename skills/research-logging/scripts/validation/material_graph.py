"""Mechanical-only v2 material graph, hygiene, and currentness projection."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from .commands import Invocation
from .entry_materials import (
    ENTRY_MATERIAL_DIRECTORY_NAMES,
    EntryMaterialPathError,
    entry_material_roots,
)
from .evidence import EvidenceFile, RetentionRecord
from .json_codec import canonical_json

MAX_GRAPH_NODES = 1_000_000
MAX_GRAPH_EDGES = 4_000_000
VALIDATION_OWNED_PARTS = frozenset({".cache", "validation"})
IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "data.csv",
        "evidence.csv",
        "evidence.json",
        "pyrun",
    }
)


class MaterialGraphV2Error(ValueError):
    """One precise material-graph or retention conformance failure."""

    def __init__(self, code: str, subject: str, observed: object, rule: str):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule


@dataclass(frozen=True, order=True)
class GraphNode:
    """One canonical node on the mechanically established graph."""

    kind: str
    identity: str


@dataclass(frozen=True, order=True)
class GraphEdge:
    """One successful mechanical relationship between canonical nodes."""

    kind: str
    source: GraphNode
    target: GraphNode


@dataclass(frozen=True)
class EvidenceConnection:
    """One successfully associated and compared evidence presentation."""

    entry: str
    record: str
    presentation: str
    materials: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    external_materials: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DirectArtifactConnection:
    """One directly presented local artifact."""

    entry: str
    presentation: str
    material: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataIndexSurface:
    """One entry-local set of declared data-index names."""

    entry: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class HygieneResult:
    """Independent connected, retained, and residual material classes."""

    inventory: tuple[str, ...]
    connected: tuple[str, ...]
    declared_retained: tuple[str, ...]
    orphaned: tuple[str, ...]
    unused_data_names: tuple[str, ...]
    dependency_projection: str


@dataclass(frozen=True)
class MaterialGraphResult:
    """Complete successful graph plus independent hygiene classification."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    hygiene: HygieneResult
    dependency_projection: str
    metrics: Mapping[str, float | int]


@dataclass(frozen=True)
class CacheEntry:
    """One dependency-keyed reusable generated result."""

    identity: str
    dependency_projection: str
    result: Mapping[str, object]


@dataclass(frozen=True)
class CacheReuse:
    """Exact reusable and reopened outcomes for one evaluation."""

    reused: Mapping[str, Mapping[str, object]]
    reopened: tuple[str, ...]


@dataclass(frozen=True)
class MaterialGraphRequest:
    """Complete bounded inputs for one material-graph composition."""

    entry_roots: Mapping[str, Path]
    evidence: Sequence[EvidenceConnection]
    direct_artifacts: Sequence[DirectArtifactConnection]
    invocations: Sequence[Invocation]
    evidence_files: Sequence[EvidenceFile]
    data_indexes: Sequence[DataIndexSurface] = ()


@dataclass
class _GraphState:
    roots: Mapping[str, tuple[Path, ...]]
    nodes: set[GraphNode]
    edges: set[GraphEdge]
    connected: set[str]
    dependencies: list[object]


def compose_material_graph(request: MaterialGraphRequest) -> MaterialGraphResult:
    """Compose only proved edges, then classify entry-owned hygiene material."""

    roots = {entry: root.resolve() for entry, root in request.entry_roots.items()}
    connection_roots = _connection_roots(roots)
    _validate_material_classification(request.invocations)
    state = _GraphState(connection_roots, set(), set(), set(), [])
    graph_started = time.perf_counter()
    _add_evidence(request.evidence, state)
    _add_direct_artifacts(request.direct_artifacts, state)
    _add_invocations(request.invocations, state)
    _bound_graph(state.nodes, state.edges)
    graph_seconds = time.perf_counter() - graph_started
    hygiene_started = time.perf_counter()
    inventory = _inventory(roots)
    retained = _retained_material(
        request.evidence_files, roots, inventory, state.connected
    )
    unused_names = _unused_data_names(request.data_indexes, request.invocations)
    hygiene = _hygiene_result(inventory, state.connected, retained, unused_names)
    hygiene_seconds = time.perf_counter() - hygiene_started
    currentness_started = time.perf_counter()
    graph_projection = {
        "dependencies": state.dependencies,
        "edges": [_edge_projection(edge) for edge in sorted(state.edges)],
        "hygiene": hygiene.dependency_projection,
        "nodes": [_node_projection(node) for node in sorted(state.nodes)],
        "version": "v2-initial",
    }
    dependency_projection = _digest(graph_projection)
    currentness_seconds = time.perf_counter() - currentness_started
    return MaterialGraphResult(
        tuple(sorted(state.nodes)),
        tuple(sorted(state.edges)),
        hygiene,
        dependency_projection,
        {
            "currentness_seconds": currentness_seconds,
            "graph_seconds": graph_seconds,
            "hygiene_seconds": hygiene_seconds,
            "inventory_files": len(inventory),
        },
    )


def _add_evidence(
    connections: Sequence[EvidenceConnection], state: _GraphState
) -> None:
    for evidence_connection in connections:
        record = _node(
            state.nodes,
            "evidence",
            f"{evidence_connection.entry}:{evidence_connection.record}",
        )
        presentation = _node(
            state.nodes, "presentation", evidence_connection.presentation
        )
        state.edges.add(GraphEdge("presentation", record, presentation))
        for raw in evidence_connection.materials:
            material = _material_node(
                state.nodes,
                raw,
                external=raw in evidence_connection.external_materials,
            )
            state.edges.add(GraphEdge("evidence-source", record, material))
            _connect_local(state.connected, material.identity, state.roots)
        state.dependencies.append(_evidence_projection(evidence_connection))


def _add_direct_artifacts(
    connections: Sequence[DirectArtifactConnection], state: _GraphState
) -> None:
    for artifact_connection in connections:
        presentation = _node(
            state.nodes, "presentation", artifact_connection.presentation
        )
        material = _material_node(state.nodes, artifact_connection.material)
        state.edges.add(GraphEdge("direct-artifact", presentation, material))
        _connect_local(state.connected, material.identity, state.roots)
        state.dependencies.append(_artifact_projection(artifact_connection))


def _add_invocations(invocations: Sequence[Invocation], state: _GraphState) -> None:
    for invocation in invocations:
        command = _node(state.nodes, "invocation", invocation.identity)
        if invocation.script is not None and invocation.script_identity is not None:
            script = _material_node(state.nodes, invocation.script)
            state.edges.add(GraphEdge("script", command, script))
            _connect_local(state.connected, script.identity, state.roots)
        for relationship in invocation.inputs:
            material = _material_node(
                state.nodes, relationship.path, external=relationship.external
            )
            state.edges.add(GraphEdge("input", material, command))
            _connect_local(state.connected, material.identity, state.roots)
            if relationship.named_input is not None:
                name = _node(
                    state.nodes,
                    "data-name",
                    f"{invocation.entry}:{relationship.named_input}",
                )
                state.edges.add(GraphEdge("named-input", name, material))
        for relationship in invocation.outputs:
            material = _material_node(state.nodes, relationship.path)
            state.edges.add(GraphEdge("output", command, material))
            _connect_local(state.connected, material.identity, state.roots)
        for collection in invocation.collections:
            identity = hashlib.sha256(
                canonical_json(
                    {
                        "direction": collection.direction,
                        "invocation": invocation.identity,
                        "mechanism": collection.mechanism,
                        "members": list(collection.members),
                        "root": collection.root,
                        "target": collection.target,
                    }
                ).encode()
            ).hexdigest()
            group = _node(state.nodes, "collection", identity)
            state.edges.add(
                GraphEdge(
                    collection.direction,
                    group if collection.direction == "input" else command,
                    command if collection.direction == "input" else group,
                )
            )
            for member in collection.members:
                material = _material_node(state.nodes, member)
                state.edges.add(GraphEdge("member", group, material))
                _connect_local(state.connected, material.identity, state.roots)
        state.dependencies.append(_invocation_projection(invocation))


def _hygiene_result(
    inventory: set[str],
    connected: set[str],
    retained: set[str],
    unused_names: set[str],
) -> HygieneResult:
    orphaned = inventory - connected - retained
    hygiene_projection = {
        "connected": sorted(inventory & connected),
        "declared_retained": sorted(retained),
        "inventory": sorted(inventory),
        "unused_data_names": sorted(unused_names),
        "version": "v2-initial",
    }
    return HygieneResult(
        tuple(sorted(inventory)),
        tuple(sorted(inventory & connected)),
        tuple(sorted(retained)),
        tuple(sorted(orphaned)),
        tuple(sorted(unused_names)),
        _digest(hygiene_projection),
    )


def reuse_by_dependency(
    current: Mapping[str, str], prior: Sequence[CacheEntry]
) -> CacheReuse:
    """Reuse only exact identity-and-dependency matches."""

    prior_by_identity = {entry.identity: entry for entry in prior}
    reused: dict[str, Mapping[str, object]] = {}
    reopened: list[str] = []
    for identity, dependency in sorted(current.items()):
        candidate = prior_by_identity.get(identity)
        if candidate is not None and candidate.dependency_projection == dependency:
            reused[identity] = candidate.result
        else:
            reopened.append(identity)
    return CacheReuse(reused, tuple(reopened))


def _node(nodes: set[GraphNode], kind: str, identity: str) -> GraphNode:
    node = GraphNode(kind, identity)
    nodes.add(node)
    return node


def _material_node(
    nodes: set[GraphNode], value: str, *, external: bool = False
) -> GraphNode:
    if external or "://" in value:
        return _node(nodes, "external-material", value)
    return _node(nodes, "material", Path(value).resolve().as_posix())


def _connect_local(
    connected: set[str], material: str, roots: Mapping[str, tuple[Path, ...]]
) -> None:
    path = Path(material)
    if any(
        _within(path, root)
        for owned_roots in roots.values()
        for root in owned_roots
    ):
        connected.add(path.resolve().as_posix())


def _connection_roots(
    roots: Mapping[str, Path],
) -> dict[str, tuple[Path, ...]]:
    result: dict[str, tuple[Path, ...]] = {}
    for entry, root in roots.items():
        try:
            result[entry] = entry_material_roots(root)
        except EntryMaterialPathError as error:
            _fail(
                "provenance.observation.unavailable",
                str(error.path),
                {"reason": error.reason},
            )
    return result


def _inventory(roots: Mapping[str, Path]) -> set[str]:
    inventory: set[str] = set()
    for root in roots.values():
        if root.is_symlink() or not root.is_dir():
            _fail("provenance.observation.unavailable", str(root), {"directory": False})
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if _excluded(relative):
                continue
            if path.is_symlink():
                if (
                    len(relative.parts) == 1
                    and relative.name in ENTRY_MATERIAL_DIRECTORY_NAMES
                ):
                    _inventory_material_root(path, relative, inventory)
                continue
            if path.is_file():
                inventory.add(path.resolve().as_posix())
                if len(inventory) > MAX_GRAPH_NODES:
                    _fail(
                        "provenance.resource.too_large",
                        "hygiene inventory",
                        {"nodes": len(inventory), "limit": MAX_GRAPH_NODES},
                    )
    return inventory


def _inventory_material_root(
    root: Path, logical_root: Path, inventory: set[str]
) -> None:
    if not root.is_dir():
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"reason": "unavailable_material_root"},
        )
    canonical_root = root.resolve()
    for path in canonical_root.rglob("*"):
        relative = logical_root / path.relative_to(canonical_root)
        if path.is_symlink():
            _fail(
                "provenance.observation.unavailable",
                str(path),
                {"reason": "nested_symlink"},
            )
        if _excluded(relative):
            continue
        if path.is_file():
            inventory.add(path.resolve().as_posix())
            if len(inventory) > MAX_GRAPH_NODES:
                _fail(
                    "provenance.resource.too_large",
                    "hygiene inventory",
                    {"nodes": len(inventory), "limit": MAX_GRAPH_NODES},
                )


def _excluded(relative: Path) -> bool:
    return (
        relative.suffix.lower() == ".md"
        or relative.name in IGNORED_NAMES
        or any(part in VALIDATION_OWNED_PARTS for part in relative.parts)
    )


def _retained_material(
    files: Sequence[EvidenceFile],
    roots: Mapping[str, Path],
    inventory: set[str],
    connected: set[str],
) -> set[str]:
    retained: set[str] = set()
    for evidence_file in files:
        if evidence_file.entry_root.resolve() not in roots.values():
            _fail(
                "retention.declaration.invalid",
                str(evidence_file.path),
                {"entry_root": str(evidence_file.entry_root)},
            )
        for record in evidence_file.records:
            if not isinstance(record, RetentionRecord):
                continue
            covered = _retention_coverage(record, evidence_file.entry_root.resolve())
            invalid = covered - inventory
            overlap = covered & retained
            redundant = covered & connected
            if invalid or overlap or redundant:
                _fail(
                    "retention.declaration.invalid",
                    f"{evidence_file.path}:{record.id}",
                    {
                        "connected": sorted(redundant),
                        "ineligible": sorted(invalid),
                        "overlap": sorted(overlap),
                    },
                )
            retained.update(covered)
    return retained


def _retention_coverage(record: RetentionRecord, root: Path) -> set[str]:
    if record.paths:
        return {(root / path).resolve().as_posix() for path in record.paths}
    assert record.directory is not None
    directory = (root / record.directory).resolve()
    return {
        path.resolve().as_posix()
        for path in directory.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and not _excluded(Path(record.directory) / path.relative_to(directory))
    }


def _unused_data_names(
    surfaces: Sequence[DataIndexSurface], invocations: Sequence[Invocation]
) -> set[str]:
    used = {
        f"{invocation.entry}:{relationship.named_input}"
        for invocation in invocations
        for relationship in invocation.inputs
        if relationship.named_input is not None
    }
    declared = {
        f"{surface.entry}:{name}" for surface in surfaces for name in surface.names
    }
    return declared - used


def _validate_material_classification(invocations: Sequence[Invocation]) -> None:
    external = {
        relationship.path
        for invocation in invocations
        for relationship in invocation.inputs
        if relationship.external
    }
    generated = {
        relationship.path
        for invocation in invocations
        for relationship in invocation.outputs
    }
    conflicts = external & generated
    if conflicts:
        _fail(
            "material.direction.conflict",
            sorted(conflicts)[0],
            {"classifications": ["external", "locally-generated"]},
        )


def _evidence_projection(connection: EvidenceConnection) -> object:
    return {
        "dependencies": list(connection.dependencies),
        "entry": connection.entry,
        "materials": sorted(connection.materials),
        "external_materials": sorted(connection.external_materials),
        "presentation": connection.presentation,
        "record": connection.record,
    }


def _artifact_projection(connection: DirectArtifactConnection) -> object:
    return {
        "dependencies": list(connection.dependencies),
        "entry": connection.entry,
        "material": connection.material,
        "presentation": connection.presentation,
    }


def _invocation_projection(invocation: Invocation) -> object:
    return {
        "collections": [
            {
                "direction": item.direction,
                "mechanism": item.mechanism,
                "members": list(item.members),
                "root": item.root,
                "target": item.target,
            }
            for item in invocation.collections
        ],
        "command_type": invocation.command_type,
        "identity": invocation.identity,
        "inputs": [item.__dict__ for item in invocation.inputs],
        "outputs": [item.__dict__ for item in invocation.outputs],
        "script_identity": invocation.script_identity,
    }


def _node_projection(node: GraphNode) -> object:
    return {"identity": node.identity, "kind": node.kind}


def _edge_projection(edge: GraphEdge) -> object:
    return {
        "kind": edge.kind,
        "source": _node_projection(edge.source),
        "target": _node_projection(edge.target),
    }


def _bound_graph(nodes: set[GraphNode], edges: set[GraphEdge]) -> None:
    if len(nodes) > MAX_GRAPH_NODES or len(edges) > MAX_GRAPH_EDGES:
        _fail(
            "provenance.resource.too_large",
            "material graph",
            {
                "edges": len(edges),
                "edge_limit": MAX_GRAPH_EDGES,
                "nodes": len(nodes),
                "node_limit": MAX_GRAPH_NODES,
            },
        )


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    rule = (
        "Unused-Material Hygiene"
        if code.startswith("retention") or code.startswith("hygiene")
        else "Command-Provenance Resource And Safety Bounds"
    )
    raise MaterialGraphV2Error(code, subject, observed, rule)
