"""Shared-graph material reachability, orphan detection, and currentness."""

from __future__ import annotations

import hashlib
import time
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from .domain import (
    AdmissionOwner,
    GraphReference,
    IssueContext,
    RepairKey,
    RepairKeyKind,
    SourceLocation,
)
from .entry_materials import (
    ENTRY_MATERIAL_DIRECTORY_NAMES,
    EntryMaterialPathError,
    entry_material_roots,
)
from .errors import MechanicalContractError
from .filesystem import BoundedTraversalError, bounded_descendants
from .json_codec import canonical_json
from .pyrun_outputs import PYRUN_OUTPUTS_BACKUP_RE
from .pyrun_state import PYRUN_BACKUP_RE
from .research_graph import (
    AmbiguityKind,
    EdgeKind,
    NodeKind,
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBounds,
    ResearchNode,
    extend_graph_materials,
)

MAX_GRAPH_NODES = 1_000_000
MAX_GRAPH_EDGES = 4_000_000
MAX_GRAPH_DEPTH = 64
RUNTIME_CACHE_DIRECTORY_NAMES = frozenset(
    {".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)
EXCLUDED_ENTRY_DIRECTORY_NAMES = frozenset({"adhoc"})
IGNORED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "data.csv",
        "data.json",
        "evidence.json",
        "pyrun",
        "pyrun.json",
        "pyrun-outputs.json",
        "retention.json",
    }
)


class MaterialClassificationError(MechanicalContractError):
    """One precise material-classification or retention conformance failure."""

    issue_context: IssueContext | None = None


@dataclass(frozen=True, order=True)
class _ReachNode:
    """One node retained only in the currentness reachability trace."""

    kind: str
    identity: str


@dataclass(frozen=True, order=True)
class _ReachEdge:
    """One edge retained only in the currentness reachability trace."""

    kind: str
    source: _ReachNode
    target: _ReachNode


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
    owner: str | None = None


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
class ReachabilityTrace:
    """Private evidence-closure trace used only for deterministic currentness."""

    nodes: tuple[_ReachNode, ...]
    edges: tuple[_ReachEdge, ...]


@dataclass(frozen=True)
class MaterialClassification:
    """Material conclusions computed from the canonical shared research graph."""

    graph: ResearchGraph
    trace: ReachabilityTrace
    orphan: OrphanResult
    dependency_projection: str
    metrics: Mapping[str, float | int]


@dataclass(frozen=True)
class MaterialClassificationRequest:
    """Complete bounded inputs for shared-graph material classification."""

    graph: ResearchGraph
    entry_roots: Mapping[str, Path]
    bounds: ResearchGraphBounds = ResearchGraphBounds()


def classify_research_graph_materials(
    request: MaterialClassificationRequest,
) -> MaterialClassification:
    """Classify evidence reachability and orphan state from the shared graph."""

    roots = {entry: root.resolve() for entry, root in request.entry_roots.items()}
    connection_roots = _connection_roots(roots)
    bundles = _graph_atomic_output_bundles(request.graph)
    graph_started = time.perf_counter()
    trace, connected = _trace_authoritative_graph(
        request.graph,
        connection_roots,
        bundles,
    )
    graph_seconds = time.perf_counter() - graph_started

    orphan_started = time.perf_counter()
    inventory = _inventory(roots)
    graph = extend_graph_materials(
        request.graph,
        tuple(inventory),
        bounds=request.bounds,
    )
    retained = _graph_retained_material(
        graph,
        roots,
        inventory,
        connected,
    )
    unused_names = _graph_unused_input_names(graph)
    orphan = _orphan_result(
        inventory, connected, retained, unused_names, bundles
    )
    orphan_seconds = time.perf_counter() - orphan_started

    currentness_started = time.perf_counter()
    graph_projection = {
        "edges": [_edge_projection(edge) for edge in trace.edges],
        "nodes": [_node_projection(node) for node in trace.nodes],
        "orphan": orphan.dependency_projection,
        "version": "input-registry-2",
    }
    dependency_projection = _digest(graph_projection)
    currentness_seconds = time.perf_counter() - currentness_started
    return MaterialClassification(
        graph,
        trace,
        orphan,
        dependency_projection,
        {
            "currentness_seconds": currentness_seconds,
            "graph_seconds": graph_seconds,
            "graph_bundle_expansions": len(bundles),
            "graph_directory_producer_lookups": 0,
            "graph_local_material_classifications": len(connected),
            "graph_material_canonicalizations": sum(
                node.kind is NodeKind.MATERIAL for node in graph.nodes
            ),
            "orphan_seconds": orphan_seconds,
            "inventory_files": len(inventory),
            "orphan_artifacts": len(orphan.orphaned),
        },
    )


@dataclass
class _TraceState:
    graph: ResearchGraph
    roots: Mapping[str, tuple[Path, ...]]
    bundles_by_material: Mapping[str, _AtomicOutputBundle]
    nodes: dict[str, ResearchNode]
    incoming: Mapping[str, tuple[ResearchEdge, ...]]
    outgoing: Mapping[str, tuple[ResearchEdge, ...]]
    trace_nodes: set[_ReachNode]
    trace_edges: set[_ReachEdge]
    connected: set[str]
    expanded_commands: set[str]
    visiting_materials: set[str]


def _trace_authoritative_graph(
    graph: ResearchGraph,
    roots: Mapping[str, tuple[Path, ...]],
    bundles: Sequence[_AtomicOutputBundle],
) -> tuple[ReachabilityTrace, set[str]]:
    """Trace evidence closure using only established ResearchGraph edges."""

    incoming: dict[str, list[ResearchEdge]] = {}
    outgoing: dict[str, list[ResearchEdge]] = {}
    for edge in graph.edges:
        incoming.setdefault(edge.target, []).append(edge)
        outgoing.setdefault(edge.source, []).append(edge)
    state = _TraceState(
        graph,
        roots,
        _bundle_material_index(bundles),
        {node.node_id: node for node in graph.nodes},
        {key: tuple(value) for key, value in incoming.items()},
        {key: tuple(value) for key, value in outgoing.items()},
        set(),
        set(),
        set(),
        set(),
        set(),
    )
    for record in graph.nodes:
        if record.kind is not NodeKind.EVIDENCE_RECORD:
            continue
        for edge in state.outgoing.get(record.node_id, ()):
            target = state.nodes[edge.target]
            if target.kind is not NodeKind.MATERIAL or edge.kind not in {
                EdgeKind.DECLARATION,
                EdgeKind.ORIGIN,
            }:
                continue
            _trace_edge("evidence-source", edge, state)
            _connect_graph_material(target, state)
            if edge.kind is EdgeKind.DECLARATION:
                _trace_graph_material(target, None, state, depth=0)
    _bound_graph(state.trace_nodes, state.trace_edges)
    return (
        ReachabilityTrace(
            tuple(sorted(state.trace_nodes)),
            tuple(sorted(state.trace_edges)),
        ),
        state.connected,
    )


def _trace_graph_material(
    material: ResearchNode,
    consumer_sequence: int | None,
    state: _TraceState,
    *,
    depth: int,
) -> None:
    if depth > MAX_GRAPH_DEPTH:
        _fail(
            "provenance.resource.too_large",
            material.identity,
            {"depth": depth, "limit": MAX_GRAPH_DEPTH},
        )
    if material.node_id in state.visiting_materials:
        return
    candidates = _graph_producer_candidates(material, consumer_sequence, state)
    if len(candidates) != 1:
        return
    command, path_edges = candidates[0]
    for edge in path_edges:
        _trace_edge(
            "membership" if edge.kind is EdgeKind.MEMBERSHIP else "output",
            edge,
            state,
        )
    _connect_graph_material(material, state)
    if command.node_id in state.expanded_commands:
        return
    state.expanded_commands.add(command.node_id)
    state.visiting_materials.add(material.node_id)
    for edge in state.incoming.get(command.node_id, ()):
        source = state.nodes[edge.source]
        if edge.kind in {EdgeKind.SCRIPT_USE, EdgeKind.CODE_USE}:
            _trace_edge(
                "script" if edge.kind is EdgeKind.SCRIPT_USE else "code",
                edge,
                state,
            )
            _connect_graph_identity(source.identity, state)
            continue
        if source.kind is not NodeKind.MATERIAL or edge.kind not in {
            EdgeKind.CONSUMPTION,
            EdgeKind.ORIGIN,
        }:
            continue
        _trace_edge("input", edge, state)
        _trace_data_declaration(source, state)
        _connect_graph_material(source, state)
        if edge.kind is EdgeKind.CONSUMPTION:
            _trace_graph_material(
                source,
                _node_sequence(command),
                state,
                depth=depth + 1,
            )
    state.visiting_materials.remove(material.node_id)


def _graph_producer_candidates(
    material: ResearchNode,
    consumer_sequence: int | None,
    state: _TraceState,
) -> tuple[tuple[ResearchNode, tuple[ResearchEdge, ...]], ...]:
    candidates: dict[str, tuple[ResearchNode, tuple[ResearchEdge, ...]]] = {}
    for edge in state.incoming.get(material.node_id, ()):
        source = state.nodes[edge.source]
        paths: list[tuple[ResearchNode, tuple[ResearchEdge, ...]]] = []
        if edge.kind is EdgeKind.PRODUCTION and source.kind is NodeKind.COMMAND:
            paths.append((source, (edge,)))
        elif edge.kind is EdgeKind.MEMBERSHIP and source.kind is NodeKind.COLLECTION:
            for producer_edge in state.incoming.get(source.node_id, ()):
                command = state.nodes[producer_edge.source]
                if (
                    producer_edge.kind is EdgeKind.PRODUCTION
                    and command.kind is NodeKind.COMMAND
                ):
                    paths.append((command, (producer_edge, edge)))
        for command, path_edges in paths:
            if (
                consumer_sequence is None
                or _node_sequence(command) < consumer_sequence
            ):
                candidates[command.node_id] = (command, path_edges)
    return tuple(candidates[key] for key in sorted(candidates))


def _trace_data_declaration(material: ResearchNode, state: _TraceState) -> None:
    for edge in state.incoming.get(material.node_id, ()):
        source = state.nodes[edge.source]
        if edge.kind is EdgeKind.DECLARATION and source.kind is NodeKind.DATA_RECORD:
            _trace_edge("declared-input", edge, state)


def _trace_edge(label: str, edge: ResearchEdge, state: _TraceState) -> None:
    source = state.nodes[edge.source]
    target = state.nodes[edge.target]
    source_trace = _ReachNode(source.kind.value, source.identity)
    target_trace = _ReachNode(target.kind.value, target.identity)
    state.trace_nodes.update((source_trace, target_trace))
    state.trace_edges.add(_ReachEdge(label, source_trace, target_trace))


def _connect_graph_material(material: ResearchNode, state: _TraceState) -> None:
    identities = {material.identity}
    bundle = state.bundles_by_material.get(material.identity)
    if bundle is not None:
        identities.update((bundle.root, *bundle.members))
        root = _ReachNode(NodeKind.MATERIAL.value, bundle.root)
        state.trace_nodes.add(root)
        for member in bundle.members:
            if member == bundle.root:
                continue
            member_node = _ReachNode(NodeKind.MATERIAL.value, member)
            state.trace_nodes.add(member_node)
            state.trace_edges.add(_ReachEdge("membership", member_node, root))
    for identity in identities:
        _connect_graph_identity(identity, state)


def _connect_graph_identity(identity: str, state: _TraceState) -> None:
    path = Path(identity)
    if any(
        _within(path, root)
        for owned_roots in state.roots.values()
        for root in owned_roots
    ):
        state.connected.add(path.resolve().as_posix())


def _node_sequence(node: ResearchNode) -> int:
    value = node.attributes.get("sequence")
    if not isinstance(value, int) or isinstance(value, bool):
        raise MaterialClassificationError(
            "provenance.observation.unavailable",
            node.identity,
            {"reason": "missing_command_sequence"},
            "Shared Research Graph",
            outcome="unavailable",
        )
    return value


def _graph_atomic_output_bundles(
    graph: ResearchGraph,
) -> tuple[_AtomicOutputBundle, ...]:
    bundles: list[_AtomicOutputBundle] = []
    roots: dict[str, int] = {}
    nodes = {node.node_id: node for node in graph.nodes}
    incoming: dict[str, list[ResearchEdge]] = {}
    outgoing: dict[str, list[ResearchEdge]] = {}
    for edge in graph.edges:
        incoming.setdefault(edge.target, []).append(edge)
        outgoing.setdefault(edge.source, []).append(edge)
    conflicts = {
        observation.subject
        for observation in graph.ambiguities
        if observation.kind is AmbiguityKind.MULTIPLE_PRODUCERS
    }
    for collection in graph.nodes:
        if (
            collection.kind is not NodeKind.COLLECTION
            or collection.attributes.get("mechanism") != "directory"
            or collection.attributes.get("supported") is not True
        ):
            continue
        root = collection.attributes.get("root")
        if not isinstance(root, str):
            continue
        producers = {
            edge.source
            for edge in incoming.get(collection.node_id, ())
            if edge.kind is EdgeKind.PRODUCTION
            and nodes[edge.source].kind is NodeKind.COMMAND
        }
        members = tuple(
            sorted(
                nodes[edge.target].identity
                for edge in outgoing.get(collection.node_id, ())
                if edge.kind is EdgeKind.MEMBERSHIP
                and nodes[edge.target].kind is NodeKind.MATERIAL
            )
        )
        member_ids = {
            edge.target
            for edge in outgoing.get(collection.node_id, ())
            if edge.kind is EdgeKind.MEMBERSHIP
        }
        if (
            len(producers) == 1
            and collection.node_id not in conflicts
            and not member_ids & conflicts
        ):
            bundles.append(_AtomicOutputBundle(root, members))
            roots[root] = roots.get(root, 0) + 1
    return tuple(bundle for bundle in bundles if roots[bundle.root] == 1)


def _graph_retained_material(
    graph: ResearchGraph,
    roots: Mapping[str, Path],
    inventory: set[str],
    connected: set[str],
) -> set[str]:
    nodes = {node.node_id: node for node in graph.nodes}
    outgoing: dict[str, list[ResearchEdge]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge)
    inventory_index = tuple(sorted(inventory))
    retained: set[str] = set()
    for record in graph.nodes:
        if record.kind is not NodeKind.RETENTION_RECORD:
            continue
        covered, targets = _retention_coverage_from_graph(
            record,
            nodes,
            outgoing,
            inventory,
            inventory_index,
        )
        _require_valid_retention_coverage(
            record,
            roots,
            inventory,
            connected,
            retained,
            covered,
            targets,
        )
        retained.update(covered)
    return retained


def _retention_coverage_from_graph(
    record: ResearchNode,
    nodes: Mapping[str, ResearchNode],
    outgoing: Mapping[str, Sequence[ResearchEdge]],
    inventory: set[str],
    inventory_index: Sequence[str],
) -> tuple[set[str], set[str]]:
    targets = {
        nodes[edge.target].identity
        for edge in outgoing.get(record.node_id, ())
        if edge.kind is EdgeKind.RETENTION
    }
    if record.attributes.get("directory") is not True:
        return targets & inventory, targets
    covered: set[str] = set()
    for target in targets:
        prefix = target.rstrip("/") + "/"
        start = bisect_left(inventory_index, prefix)
        stop = bisect_left(inventory_index, prefix + "\uffff")
        covered.update(inventory_index[start:stop])
    return covered, targets


def _require_valid_retention_coverage(  # noqa: PLR0913 -- explicit set contract
    record: ResearchNode,
    roots: Mapping[str, Path],
    inventory: set[str],
    connected: set[str],
    retained: set[str],
    covered: set[str],
    targets: set[str],
) -> None:
    if record.entry not in roots:
        _fail(
            "retention.declaration.invalid",
            record.identity,
            {"reason": "unknown_entry"},
        )
    invalid = (
        set()
        if record.attributes.get("directory") is True
        else targets - inventory
    )
    if not covered:
        invalid.update(targets)
    overlap = covered & retained
    redundant = covered & connected
    if not (invalid or overlap or redundant):
        return
    source = str(record.attributes.get("source", record.identity))
    record_id = record.identity.rsplit(":", 1)[-1]
    _fail(
        "retention.declaration.invalid",
        f"{source}:{record_id}",
        {
            "connected": sorted(redundant),
            "ineligible": sorted(invalid),
            "overlap": sorted(overlap),
        },
        issue_context=_retention_issue_context(
            record,
            source,
            record_id,
            targets,
        ),
    )


def _retention_issue_context(
    record: ResearchNode,
    source: str,
    record_id: str,
    targets: set[str],
) -> IssueContext:
    return IssueContext(
        entry=record.entry,
        source_locations=(SourceLocation(source),),
        repair_keys=(
            RepairKey(
                RepairKeyKind.RECORD,
                f"{record.entry}:retention:{record_id}",
                record.entry,
            ),
        ),
        context_nodes=(
            record.reference,
            *(
                GraphReference(NodeKind.MATERIAL.value, target)
                for target in sorted(targets)
            ),
        ),
        admission_owner=AdmissionOwner.ENTRY,
    )


def _graph_unused_input_names(graph: ResearchGraph) -> set[str]:
    nodes = {node.node_id: node for node in graph.nodes}
    used_records: set[str] = set()
    for edge in graph.edges:
        source = nodes[edge.source]
        target = nodes[edge.target]
        if (
            source.kind is NodeKind.DATA_RECORD
            and target.kind in {NodeKind.COMMAND, NodeKind.EVIDENCE_RECORD}
            and edge.kind is EdgeKind.DECLARATION
        ):
            used_records.add(source.node_id)
    unused: set[str] = set()
    for record in graph.nodes:
        if record.kind is not NodeKind.DATA_RECORD:
            continue
        if record.node_id in used_records:
            continue
        owner = str(record.attributes["owner"])
        name = str(record.attributes["name"])
        unused.add(f"{owner}:{name}")
    return unused


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
        for path in _bounded_inventory_descendants(
            root,
            excluded_top_level_directories=EXCLUDED_ENTRY_DIRECTORY_NAMES,
        ):
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


def _bounded_inventory_descendants(
    root: Path,
    *,
    excluded_top_level_directories: frozenset[str] = frozenset(),
) -> tuple[Path, ...]:
    try:
        return bounded_descendants(
            root,
            maximum_entries=MAX_GRAPH_NODES,
            excluded_top_level_directories=excluded_top_level_directories,
        )
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
            and (
                PYRUN_OUTPUTS_BACKUP_RE.fullmatch(relative.name) is not None
                or PYRUN_BACKUP_RE.fullmatch(relative.name) is not None
            )
        )
        or (bool(relative.parts) and relative.parts[0] == "tmp")
        or any(part in RUNTIME_CACHE_DIRECTORY_NAMES for part in relative.parts)
    )


def _node_projection(node: _ReachNode) -> object:
    return {"identity": node.identity, "kind": node.kind}


def _edge_projection(edge: _ReachEdge) -> object:
    return {
        "kind": edge.kind,
        "source": _node_projection(edge.source),
        "target": _node_projection(edge.target),
    }


def _bound_graph(nodes: set[_ReachNode], edges: set[_ReachEdge]) -> None:
    if len(nodes) > MAX_GRAPH_NODES or len(edges) > MAX_GRAPH_EDGES:
        _fail(
            "provenance.resource.too_large",
            "material reachability trace",
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


def _fail(
    code: str,
    subject: str,
    observed: object,
    *,
    issue_context: IssueContext | None = None,
) -> NoReturn:
    rule = (
        "Evidence-rooted Orphans"
        if code.startswith("retention") or code.startswith("orphan")
        else "Producer And Lineage Semantics"
    )
    error = MaterialClassificationError(code, subject, observed, rule)
    error.issue_context = issue_context
    raise error
