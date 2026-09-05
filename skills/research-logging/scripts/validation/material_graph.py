"""Evidence-rooted material graph, artifact orphans, and currentness."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from research_log_data import DataFile

from .commands import Invocation, MaterialRelationship
from .entry_materials import (
    ENTRY_MATERIAL_DIRECTORY_NAMES,
    EntryMaterialPathError,
    entry_material_roots,
)
from .errors import MechanicalContractError
from .filesystem import BoundedTraversalError, bounded_descendants
from .json_codec import canonical_json
from .provenance import ProducerIndex, build_producer_index
from .pyrun_outputs import PYRUN_OUTPUTS_BACKUP_RE
from .retention import MAX_RETENTION_DESCENDANTS, RetentionFile, RetentionRecord

MAX_GRAPH_NODES = 1_000_000
MAX_GRAPH_EDGES = 4_000_000
MAX_GRAPH_DEPTH = 64
RUNTIME_CACHE_DIRECTORY_NAMES = frozenset(
    {".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)
IGNORED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "data.csv",
        "data.json",
        "evidence.json",
        "pyrun",
        "pyrun-outputs.json",
        "retention.json",
    }
)


class MaterialGraphV2Error(MechanicalContractError):
    """One precise material-graph or retention conformance failure."""


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
    origin_materials: frozenset[str] = frozenset()
    input_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class InputRegistrySurface:
    """One material-owner-local input registry."""

    owner: str
    data_file: DataFile


@dataclass(frozen=True)
class _AtomicOutputBundle:
    """One unambiguous generated output-directory ownership boundary."""

    root: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class OrphanResult:
    """Independent connected, retained, and residual material classes."""

    inventory: tuple[str, ...]
    connected: tuple[str, ...]
    declared_retained: tuple[str, ...]
    orphaned: tuple[str, ...]
    unused_input_names: tuple[str, ...]
    dependency_projection: str


@dataclass(frozen=True)
class MaterialGraphResult:
    """Complete successful graph plus independent orphan classification."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    orphan: OrphanResult
    dependency_projection: str
    metrics: Mapping[str, float | int]


@dataclass(frozen=True)
class MaterialGraphRequest:
    """Complete bounded inputs for one material-graph composition."""

    entry_roots: Mapping[str, Path]
    evidence: Sequence[EvidenceConnection]
    invocations: Sequence[Invocation]
    retention_files: Sequence[RetentionFile]
    input_registries: Sequence[InputRegistrySurface] = ()
    producer_index: ProducerIndex | None = None
    supported_output_directories: frozenset[str] = frozenset()


@dataclass
class _GraphState:
    roots: Mapping[str, tuple[Path, ...]]
    producer_index: ProducerIndex
    outputs: Mapping[str, tuple[Invocation, ...]]
    bundles_by_material: Mapping[str, _AtomicOutputBundle]
    nodes: set[GraphNode]
    edges: set[GraphEdge]
    connected: set[str]
    dependencies: list[object]
    canonical_materials: dict[str, str]
    material_nodes: dict[tuple[str, bool], GraphNode]
    local_materials: dict[str, bool]
    directory_producers: dict[tuple[str, int], Invocation | None]
    expanded_bundles: set[str]
    expanded_invocations: set[str]
    visiting: set[str]
    directory_producer_lookups: int = 0


def compose_material_graph(request: MaterialGraphRequest) -> MaterialGraphResult:
    """Compose the evidence closure, then classify entry-owned material."""

    roots = {entry: root.resolve() for entry, root in request.entry_roots.items()}
    producer_index = request.producer_index or build_producer_index(
        request.invocations
    )
    bundles = _atomic_output_bundles(
        request.invocations,
        producer_index,
        request.supported_output_directories,
    )
    state = _GraphState(
        roots=_connection_roots(roots),
        producer_index=producer_index,
        outputs=producer_index.outputs,
        bundles_by_material=_bundle_material_index(bundles),
        nodes=set(),
        edges=set(),
        connected=set(),
        dependencies=[],
        canonical_materials={},
        material_nodes={},
        local_materials={},
        directory_producers={},
        expanded_bundles=set(),
        expanded_invocations=set(),
        visiting=set(),
    )
    graph_started = time.perf_counter()
    _add_evidence(request.evidence, state)
    _bound_graph(state.nodes, state.edges)
    graph_seconds = time.perf_counter() - graph_started

    orphan_started = time.perf_counter()
    inventory = _inventory(roots)
    retained = _retained_material(
        request.retention_files, roots, inventory, state.connected
    )
    unused_names = _unused_input_names(
        request.input_registries,
        request.invocations,
        request.evidence,
    )
    orphan = _orphan_result(
        inventory, state.connected, retained, unused_names, bundles
    )
    orphan_seconds = time.perf_counter() - orphan_started

    currentness_started = time.perf_counter()
    graph_projection = {
        "dependencies": state.dependencies,
        "edges": [_edge_projection(edge) for edge in sorted(state.edges)],
        "nodes": [_node_projection(node) for node in sorted(state.nodes)],
        "orphan": orphan.dependency_projection,
        "version": "input-registry-2",
    }
    dependency_projection = _digest(graph_projection)
    currentness_seconds = time.perf_counter() - currentness_started
    return MaterialGraphResult(
        tuple(sorted(state.nodes)),
        tuple(sorted(state.edges)),
        orphan,
        dependency_projection,
        {
            "currentness_seconds": currentness_seconds,
            "graph_seconds": graph_seconds,
            "graph_bundle_expansions": len(state.expanded_bundles),
            "graph_directory_producer_lookups": state.directory_producer_lookups,
            "graph_local_material_classifications": len(state.local_materials),
            "graph_material_canonicalizations": len(state.canonical_materials),
            "orphan_seconds": orphan_seconds,
            "inventory_files": len(inventory),
            "orphan_artifacts": len(orphan.orphaned),
        },
    )


def _add_evidence(
    connections: Sequence[EvidenceConnection], state: _GraphState
) -> None:
    for connection in connections:
        record = _node(
            state.nodes, "evidence", f"{connection.entry}:{connection.record}"
        )
        presentation = _node(state.nodes, "presentation", connection.presentation)
        state.edges.add(GraphEdge("presentation", record, presentation))
        for raw in connection.materials:
            origin = raw in connection.origin_materials
            material = _material_node(state, raw)
            state.edges.add(GraphEdge("evidence-source", record, material))
            _connect_material(material, state)
            if not origin:
                _trace_material(material.identity, None, state, depth=0)
        state.dependencies.append(_evidence_projection(connection))


def _trace_material(
    material: str,
    consumer: Invocation | None,
    state: _GraphState,
    *,
    depth: int,
) -> None:
    """Add only the unambiguous producer portion of one reached branch."""

    if depth > MAX_GRAPH_DEPTH:
        _fail(
            "provenance.resource.too_large",
            material,
            {"depth": depth, "limit": MAX_GRAPH_DEPTH},
        )
    candidates = tuple(
        invocation
        for invocation in state.outputs.get(material, ())
        if consumer is None or invocation.sequence < consumer.sequence
    )
    if len(candidates) != 1:
        return
    producer = candidates[0]
    if producer.identity in state.visiting:
        return
    command = _node(state.nodes, "invocation", producer.identity)
    output = _material_node(state, material)
    state.edges.add(GraphEdge("output", command, output))
    _connect_material(output, state)
    if producer.identity in state.expanded_invocations:
        return
    state.expanded_invocations.add(producer.identity)
    state.visiting.add(producer.identity)
    if producer.script is not None and producer.script_identity is not None:
        script = _material_node(state, producer.script)
        state.edges.add(GraphEdge("script", command, script))
        _connect_material(script, state)
    for relationship in producer.inputs:
        _add_reached_input(relationship, producer, command, state, depth=depth)
    state.visiting.remove(producer.identity)
    state.dependencies.append(_invocation_projection(producer))


def _add_reached_input(
    relationship: MaterialRelationship,
    consumer: Invocation,
    command: GraphNode,
    state: _GraphState,
    *,
    depth: int,
) -> None:
    resource = relationship.input_resource
    material = _material_node(
        state,
        relationship.path,
        path_based=resource is None or resource.kind != "git-repository",
    )
    state.edges.add(GraphEdge("input", material, command))
    if resource is None or resource.kind != "git-repository":
        _connect_material(material, state)
    if resource is not None:
        declaration = _node(
            state.nodes,
            "input-declaration",
            f"{consumer.material_owner}:{resource.name}",
        )
        state.edges.add(GraphEdge("declared-input", declaration, material))
    prior = _reached_prior_producer(relationship, consumer, state)
    if prior is None:
        return
    _trace_material(relationship.path, consumer, state, depth=depth + 1)


def _reached_prior_producer(
    relationship: MaterialRelationship,
    consumer: Invocation,
    state: _GraphState,
) -> Invocation | None:
    if relationship.origin:
        return None
    resource = relationship.input_resource
    if resource is None or resource.kind == "file":
        earlier = tuple(
            invocation
            for invocation in state.outputs.get(relationship.path, ())
            if invocation.sequence < consumer.sequence
        )
        return earlier[0] if len(earlier) == 1 else None
    key = (resource.canonical_target, consumer.sequence)
    if key not in state.directory_producers:
        state.directory_producer_lookups += 1
        matches = state.producer_index.lookup(
            resource.canonical_target,
            before_sequence=consumer.sequence,
        )
        exact = tuple(match.producer for match in matches if match.exact_directory)
        exact_ids = {invocation.identity for invocation in exact}
        producers_within = {
            match.producer.identity for match in matches if match.member_output
        }
        overlapping = {
            match.producer.identity for match in matches if match.overlapping_directory
        }
        conflicts = (producers_within - exact_ids) | overlapping
        state.directory_producers[key] = (
            exact[0] if len(exact) == 1 and not conflicts else None
        )
    owner = state.directory_producers[key]
    if owner is None or owner not in state.outputs.get(relationship.path, ()):
        return None
    return owner


def _atomic_output_bundles(
    invocations: Sequence[Invocation],
    producer_index: ProducerIndex,
    supported_output_directories: frozenset[str],
) -> tuple[_AtomicOutputBundle, ...]:
    """Derive unambiguous atomic roots from command-owned output support."""

    bundles: list[_AtomicOutputBundle] = []
    for invocation in invocations:
        for collection in invocation.collections:
            if (
                collection.direction != "output"
                or collection.mechanism != "directory"
                or collection.root is None
                or collection.root not in supported_output_directories
            ):
                continue
            matches = producer_index.lookup(collection.root)
            exact = tuple(match for match in matches if match.exact_directory)
            if (
                len(exact) != 1
                or exact[0].producer.identity != invocation.identity
                or any(
                    match.overlapping_directory
                    or (
                        match.producer.identity != invocation.identity
                        and (match.exact_directory or match.member_output)
                    )
                    for match in matches
                )
            ):
                continue
            bundles.append(
                _AtomicOutputBundle(collection.root, tuple(collection.members))
            )
    return tuple(sorted(bundles, key=lambda bundle: bundle.root))


def _bundle_material_index(
    bundles: Sequence[_AtomicOutputBundle],
) -> Mapping[str, _AtomicOutputBundle]:
    result: dict[str, _AtomicOutputBundle] = {}
    for bundle in bundles:
        for material in (bundle.root, *bundle.members):
            result[material] = bundle
    return result


def _orphan_result(
    inventory: set[str],
    connected: set[str],
    retained: set[str],
    unused_names: set[str],
    bundles: Sequence[_AtomicOutputBundle],
) -> OrphanResult:
    orphaned = _atomic_orphans(
        inventory - connected - retained,
        inventory,
        bundles,
    )
    orphan_projection = {
        "atomic_output_bundles": [
            {"members": list(bundle.members), "root": bundle.root}
            for bundle in bundles
        ],
        "connected": sorted(inventory & connected),
        "declared_retained": sorted(retained),
        "inventory": sorted(inventory),
        "unused_input_names": sorted(unused_names),
        "version": "input-registry-2",
    }
    return OrphanResult(
        tuple(sorted(inventory)),
        tuple(sorted(inventory & connected)),
        tuple(sorted(retained)),
        tuple(sorted(orphaned)),
        tuple(sorted(unused_names)),
        _digest(orphan_projection),
    )


def _atomic_orphans(
    orphaned: set[str],
    inventory: set[str],
    bundles: Sequence[_AtomicOutputBundle],
) -> set[str]:
    result = set(orphaned)
    for bundle in bundles:
        eligible = set(bundle.members) & inventory
        if not eligible & result:
            continue
        result.difference_update(eligible)
        result.add(bundle.root)
    return result


def _node(nodes: set[GraphNode], kind: str, identity: str) -> GraphNode:
    node = GraphNode(kind, identity)
    nodes.add(node)
    return node


def _material_node(
    state: _GraphState, value: str, *, path_based: bool = True
) -> GraphNode:
    """Return one validation-scoped material node with bounded canonicalization."""

    key = (value, path_based)
    node = state.material_nodes.get(key)
    if node is not None:
        return node
    identity = _canonical_material(value, state) if path_based else value
    node = _node(state.nodes, "material", identity)
    state.material_nodes[key] = node
    return node


def _connect_material(material: GraphNode, state: _GraphState) -> None:
    """Connect one exact material and its atomic bundle ownership boundary."""

    _connect_local(material.identity, state)
    bundle = state.bundles_by_material.get(material.identity)
    if bundle is None:
        return
    if bundle.root not in state.expanded_bundles:
        state.expanded_bundles.add(bundle.root)
        for member in bundle.members:
            _connect_local(member, state)
    bundle_node = _material_node(state, bundle.root)
    if material != bundle_node:
        state.edges.add(GraphEdge("membership", material, bundle_node))


def _connect_local(material: str, state: _GraphState) -> None:
    canonical = _canonical_material(material, state)
    local = state.local_materials.get(canonical)
    if local is None:
        path = Path(canonical)
        local = any(
            _within(path, root)
            for owned_roots in state.roots.values()
            for root in owned_roots
        )
        state.local_materials[canonical] = local
    if local:
        state.connected.add(canonical)


def _canonical_material(material: str, state: _GraphState) -> str:
    canonical = state.canonical_materials.get(material)
    if canonical is None:
        canonical = Path(material).resolve().as_posix()
        state.canonical_materials[material] = canonical
    return canonical


def _connection_roots(roots: Mapping[str, Path]) -> dict[str, tuple[Path, ...]]:
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
        for path in _bounded_inventory_descendants(root):
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
                _bound_inventory(inventory)
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
    for path in _bounded_inventory_descendants(canonical_root):
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
            _bound_inventory(inventory)


def _bound_inventory(inventory: set[str]) -> None:
    if len(inventory) > MAX_GRAPH_NODES:
        _fail(
            "provenance.resource.too_large",
            "orphan inventory",
            {"nodes": len(inventory), "limit": MAX_GRAPH_NODES},
        )


def _bounded_inventory_descendants(root: Path) -> tuple[Path, ...]:
    try:
        return bounded_descendants(root, maximum_entries=MAX_GRAPH_NODES)
    except BoundedTraversalError as error:
        if error.reason == "entry_limit":
            _fail(
                "provenance.resource.too_large",
                str(root),
                {"entries": error.observed, "limit": error.limit},
            )
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"error": error.detail, "reason": error.reason},
        )


def _excluded(relative: Path) -> bool:
    return (
        (len(relative.parts) == 1 and relative.suffix.lower() == ".md")
        or relative.name in IGNORED_FILE_NAMES
        or (
            len(relative.parts) == 1
            and PYRUN_OUTPUTS_BACKUP_RE.fullmatch(relative.name) is not None
        )
        or (bool(relative.parts) and relative.parts[0] == "tmp")
        or any(part in RUNTIME_CACHE_DIRECTORY_NAMES for part in relative.parts)
    )


def _retained_material(
    files: Sequence[RetentionFile],
    roots: Mapping[str, Path],
    inventory: set[str],
    connected: set[str],
) -> set[str]:
    retained: set[str] = set()
    for retention_file in files:
        if retention_file.entry_root.resolve() not in roots.values():
            _fail(
                "retention.declaration.invalid",
                str(retention_file.path),
                {"entry_root": str(retention_file.entry_root)},
            )
        for record in retention_file.records:
            covered = _retention_coverage(record, retention_file.entry_root.resolve())
            invalid = covered - inventory
            overlap = covered & retained
            redundant = covered & connected
            if invalid or overlap or redundant:
                _fail(
                    "retention.declaration.invalid",
                    f"{retention_file.path}:{record.id}",
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
    try:
        descendants = bounded_descendants(
            directory, maximum_entries=MAX_RETENTION_DESCENDANTS
        )
    except BoundedTraversalError as error:
        _fail(
            "retention.declaration.invalid",
            str(directory),
            {
                "limit": error.limit,
                "observed": error.observed,
                "reason": error.reason,
            },
        )
    return {
        path.resolve().as_posix()
        for path in descendants
        if path.is_file()
        and not path.is_symlink()
        and not _excluded(Path(record.directory) / path.relative_to(directory))
    }


def _unused_input_names(
    surfaces: Sequence[InputRegistrySurface],
    invocations: Sequence[Invocation],
    evidence: Sequence[EvidenceConnection],
) -> set[str]:
    used = {
        f"{invocation.material_owner}:{relationship.input_resource.name}"
        for invocation in invocations
        for relationship in invocation.inputs
        if relationship.input_resource is not None
    }
    used.update(name for connection in evidence for name in connection.input_names)
    declared = {
        f"{surface.owner}:{resource.name}"
        for surface in surfaces
        for resource in surface.data_file.inputs
    }
    return declared - used


def _evidence_projection(connection: EvidenceConnection) -> object:
    return {
        "dependencies": list(connection.dependencies),
        "entry": connection.entry,
        "origin_materials": sorted(connection.origin_materials),
        "input_names": sorted(connection.input_names),
        "materials": sorted(connection.materials),
        "presentation": connection.presentation,
        "record": connection.record,
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
        "identity": invocation.identity,
        "inputs": [_relationship_projection(item) for item in invocation.inputs],
        "outputs": [_relationship_projection(item) for item in invocation.outputs],
        "parameters": list(invocation.parameters),
        "script_argument": invocation.script_argument,
        "script_identity": invocation.script_identity,
    }


def _relationship_projection(relationship: MaterialRelationship) -> object:
    resource = relationship.input_resource
    return {
        "direction": relationship.direction,
        "origin": relationship.origin,
        "input_identity": resource.content_identity if resource is not None else None,
        "named_input": relationship.named_input,
        "path": relationship.path,
        "proof": relationship.proof,
        "target": relationship.target,
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
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    rule = (
        "Evidence-rooted Orphans"
        if code.startswith("retention") or code.startswith("orphan")
        else "Producer And Lineage Semantics"
    )
    raise MaterialGraphV2Error(code, subject, observed, rule)
