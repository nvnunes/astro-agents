"""Canonical dependency-graph queries for research-log validation."""

from __future__ import annotations

from collections import deque
from typing import Iterable, Mapping, Set, Tuple

from .graph import (
    DependencyGraph,
    EdgeKind,
    GraphContractError,
    NodeKey,
    NodeKind,
    RootPolicy,
)

_MATERIAL_KINDS = {
    NodeKind.ARTIFACT,
    NodeKind.COLLECTION,
    NodeKind.MEMBER,
    NodeKind.EXTERNAL_SOURCE,
}


def _eligible_material_producers(
    graph: DependencyGraph, node: NodeKey
) -> set[NodeKey]:
    """Return producers that do not also consume the same material."""

    producers = {
        edge.source
        for edge in graph.incoming(node, {EdgeKind.PRODUCES, EdgeKind.CAPTURES})
    }
    consumers = {
        edge.source
        for edge in graph.incoming(node, {EdgeKind.CONSUMES})
    }
    return producers - consumers


def _material_transitions(
    graph: DependencyGraph, node: NodeKey, phase: str
) -> Iterable[Tuple[NodeKey, str]]:
    """Yield producer or selected-member provenance for retained material."""

    selected_producers = [
        edge.target
        for edge in graph.outgoing(node, {EdgeKind.SELECTED_PRODUCER})
    ]
    producers = _eligible_material_producers(graph, node)
    if node.kind is NodeKind.COLLECTION:
        selected_members = [
            edge.source
            for edge in graph.incoming(node, {EdgeKind.MEMBER_OF})
            if edge.attribute("selected", False)
        ]
        if selected_members:
            yield from ((member, phase) for member in selected_members)
    chosen = selected_producers or (producers if len(producers) == 1 else [])
    yield from ((producer, phase) for producer in chosen)
    yield from (
        (edge.source, phase)
        for edge in graph.incoming(node, {EdgeKind.RESOLVES_TO})
    )


def ambiguous_producer_nodes(
    graph: DependencyGraph, reached: Iterable[NodeKey]
) -> dict[NodeKey, tuple[NodeKey, ...]]:
    """Return reached generated materials lacking one selected producer."""

    ambiguous = {}
    for node in reached:
        if node.kind not in _MATERIAL_KINDS:
            continue
        if list(graph.outgoing(node, {EdgeKind.SELECTED_PRODUCER})):
            continue
        producers = tuple(sorted(_eligible_material_producers(graph, node)))
        if len(producers) > 1:
            ambiguous[node] = producers
    return ambiguous


def _presented_transitions(
    graph: DependencyGraph,
    node: NodeKey,
    phase: str,
    *,
    retain_related_material: bool,
) -> Iterable[Tuple[NodeKey, str]]:
    """Yield direction-aware dependencies for one presented workflow.

    Outputs produced beside a supporting target are retained as sibling outputs,
    but are terminal.  This prevents a shared output directory from becoming a
    bridge into unrelated commands that also write to that directory.
    """

    if phase in {"sibling-output", "associated-material"}:
        if node.kind is NodeKind.COLLECTION:
            producers = {
                edge.source
                for edge in graph.incoming(
                    node, {EdgeKind.PRODUCES, EdgeKind.CAPTURES}
                )
                if edge.source.kind is NodeKind.INVOCATION
            }
            yield from (
                (edge.source, phase)
                for edge in graph.incoming(node, {EdgeKind.MEMBER_OF})
                if edge.attribute("selected", False) or len(producers) == 1
            )
        return

    if node.kind is NodeKind.PRESENTED:
        yield from (
            (edge.source, phase)
            for edge in graph.incoming(node, {EdgeKind.SUPPORTS})
        )
        return
    if node.kind in _MATERIAL_KINDS:
        yield from _material_transitions(graph, node, phase)
        return
    if node.kind is NodeKind.INDEXED_INPUT:
        yield from (
            (edge.target, phase)
            for edge in graph.outgoing(node, {EdgeKind.RESOLVES_TO})
        )
        return
    if node.kind is NodeKind.INVOCATION:
        yield from (
            (
                edge.target,
                (
                    "associated-material"
                    if edge.attribute("semantic_direction")
                    == "unresolved-command-path"
                    else phase
                ),
            )
            for edge in graph.outgoing(
                node,
                {
                    EdgeKind.CONSUMES,
                    EdgeKind.INVOKES,
                },
            )
            if retain_related_material
            or edge.attribute("semantic_direction")
            != "unresolved-command-path"
        )
        if retain_related_material:
            yield from (
                (edge.target, "sibling-output")
                for edge in graph.outgoing(
                    node, {EdgeKind.PRODUCES, EdgeKind.CAPTURES}
                )
            )
        return
    if node.kind is NodeKind.SCRIPT:
        yield from (
            (edge.target, phase)
            for edge in graph.outgoing(
                node, {EdgeKind.DEPENDS_ON_CODE}
            )
        )


def _recorded_command_neighbors(
    graph: DependencyGraph, node: NodeKey
) -> Iterable[NodeKey]:
    """Yield only entrypoint and transitive code dependencies for a command."""

    if node.kind is NodeKind.INVOCATION:
        yield from (
            edge.target
            for edge in graph.outgoing(
                node, {EdgeKind.INVOKES}
            )
        )
    elif node.kind is NodeKind.SCRIPT:
        yield from (
            edge.target
            for edge in graph.outgoing(
                node, {EdgeKind.DEPENDS_ON_CODE}
            )
        )


def _retention_neighbors(
    graph: DependencyGraph, node: NodeKey
) -> Iterable[NodeKey]:
    """Yield members covered by an exact retained-directory instruction."""

    if node.kind is NodeKind.COLLECTION:
        yield from (
            edge.source for edge in graph.incoming(node, {EdgeKind.MEMBER_OF})
        )


def _walk_nodes(
    graph: DependencyGraph,
    roots: Iterable[Tuple[NodeKey, RootPolicy]] | None,
    *,
    retain_related_material: bool,
) -> Set[NodeKey]:
    """Return nodes reached under the selected traversal contract."""

    selected = (
        [(root.node, root.policy) for root in graph.roots]
        if roots is None
        else list(roots)
    )
    reached: Set[Tuple[NodeKey, RootPolicy, str]] = set()
    pending = deque((node, policy, "provenance") for node, policy in selected)
    while pending:
        node, policy, phase = pending.popleft()
        state = (node, policy, phase)
        if state in reached:
            continue
        graph.node(node)
        reached.add(state)
        if policy is RootPolicy.PRESENTED:
            transitions = _presented_transitions(
                graph,
                node,
                phase,
                retain_related_material=retain_related_material,
            )
        elif policy is RootPolicy.RECORDED_COMMAND:
            transitions = (
                (neighbor, phase)
                for neighbor in _recorded_command_neighbors(graph, node)
            )
        elif policy is RootPolicy.RETENTION:
            transitions = (
                (neighbor, phase)
                for neighbor in _retention_neighbors(graph, node)
            )
        else:  # pragma: no cover - exhaustive enum guard
            raise GraphContractError(f"unsupported root policy: {policy}")
        pending.extend(
            (neighbor, policy, next_phase)
            for neighbor, next_phase in transitions
        )
    return {node for node, _policy, _phase in reached}


def reachable_nodes(
    graph: DependencyGraph,
    roots: Iterable[Tuple[NodeKey, RootPolicy]] | None = None,
) -> Set[NodeKey]:
    """Return material retained for provenance and local orphan classification."""

    return _walk_nodes(
        graph, roots, retain_related_material=True
    )


def provenance_nodes(
    graph: DependencyGraph,
    roots: Iterable[Tuple[NodeKey, RootPolicy]] | None = None,
) -> Set[NodeKey]:
    """Return the supporting provenance without sibling or ambiguous paths."""

    return _walk_nodes(
        graph, roots, retain_related_material=False
    )


def display_identity(graph: DependencyGraph, key: NodeKey) -> str:
    """Return the research-log identity represented by one graph node."""

    return str(graph.node(key).attribute("display_identity", key.identity))


def target_provenance_seeds(
    graph: DependencyGraph,
    entry_id: str,
    target: str,
    dependencies: Iterable[Mapping[str, object]],
    producer_invocation: str | None = None,
) -> Set[NodeKey]:
    """Return the graph roots representing one exact presented target."""

    if producer_invocation is not None:
        target_namespaces = {
            node.key.namespace
            for node in graph.nodes
            if display_identity(graph, node.key) == target
        }
        selected = {
            node.key
            for node in graph.nodes
            if node.key.kind is NodeKind.INVOCATION
            and node.key.identity == producer_invocation
            and (
                not target_namespaces
                or node.key.namespace in target_namespaces
            )
        }
        if len(selected) != 1:
            return set()
        return selected

    material_kinds = _MATERIAL_KINDS | {NodeKind.INDEXED_INPUT}
    target_identities = {target}
    target_identities.update(
        str(item["path"])
        for item in dependencies
        if item.get("role") == "target" and isinstance(item.get("path"), str)
    )
    materials = {
        node.key
        for node in graph.nodes
        if node.key.kind in material_kinds
        and display_identity(graph, node.key) in target_identities
    }
    presented = {
        edge.target
        for material in materials
        for edge in graph.outgoing(material, {EdgeKind.SUPPORTS})
        if edge.target.kind is NodeKind.PRESENTED
        and graph.node(edge.target).attribute("entry") == entry_id
    }
    return presented or materials


def orphanable_nodes(
    graph: DependencyGraph, namespace: str | None = None
) -> Set[NodeKey]:
    """Return graph nodes declared eligible for orphan classification."""

    return {
        node.key
        for node in graph.nodes
        if node.attribute("orphanable", False)
        and (namespace is None or node.key.namespace == namespace)
    }


def _orphan_reachable_nodes(graph: DependencyGraph) -> Set[NodeKey]:
    """Return used material plus its immediate owning collection containers."""

    reached = reachable_nodes(graph)
    containers = {
        edge.target
        for edge in graph.edges
        if edge.kind is EdgeKind.MEMBER_OF and edge.source in reached
    }
    return reached | containers


def orphan_nodes(
    graph: DependencyGraph, namespace: str | None = None
) -> Set[NodeKey]:
    """Return orphanable nodes unreachable from every applicable root."""

    return orphanable_nodes(graph, namespace) - _orphan_reachable_nodes(graph)


def orphan_location(graph: DependencyGraph, key: NodeKey) -> Tuple[str, str]:
    """Return the entry-scoped display identity for one orphanable node."""

    node = graph.node(key)
    entry_id = node.attribute("entry")
    if not isinstance(entry_id, str):
        owners = {
            edge.target.identity
            for edge in graph.outgoing(key, {EdgeKind.OWNED_BY})
            if edge.target.kind is NodeKind.ENTRY
        }
        if len(owners) != 1:
            raise GraphContractError(
                f"orphanable graph node must have one entry owner: {key.as_dict()}"
            )
        entry_id = next(iter(owners))
    identity = str(node.attribute("display_identity", key.identity))
    return entry_id, identity


def orphan_locations(
    graph: DependencyGraph, namespace: str | None = None
) -> Set[Tuple[str, str]]:
    """Return entry-scoped identities for graph-unreachable inventory."""

    return {orphan_location(graph, key) for key in orphan_nodes(graph, namespace)}


def assert_unresolved_orphans_unreachable(
    graph: DependencyGraph, unresolved: Iterable[NodeKey]
) -> None:
    """Reject an unresolved orphan that is reachable from an applicable root."""

    conflicts = set(unresolved) & _orphan_reachable_nodes(graph)
    if conflicts:
        rendered = ", ".join(
            f"{item.namespace}:{item.kind.value}:{item.identity}"
            for item in sorted(conflicts)
        )
        raise GraphContractError(
            "unresolved orphan is reachable from an applicable root: " + rendered
        )


def graph_summary(graph: DependencyGraph) -> Mapping[str, int | str]:
    """Return compact deterministic metrics for shadow comparison."""

    reached = reachable_nodes(graph)
    orphanable = orphanable_nodes(graph)
    return {
        "identity": graph.identity,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "roots": len(graph.roots),
        "reachable_nodes": len(reached),
        "orphanable_nodes": len(orphanable),
        "orphan_nodes": len(orphanable - reached),
    }
