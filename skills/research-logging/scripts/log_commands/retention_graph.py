"""Fresh evidence-rooted connectivity for retention authoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from research_log_data import DataFile, load_data_file, resolve_declared_input_token
from validation.evidence import authored_eid_comments, load_evidence_file
from validation.evidence_markdown import read_markdown_evidence
from validation.material_graph import EvidenceConnection, trace_research_graph_materials
from validation.research_graph import (
    AmbiguityObservation,
    EdgeKind,
    EvaluationGraphInputs,
    NodeKind,
    ResearchGraph,
    ResearchNode,
    build_evaluation_graph,
)

from .context import EntryContext
from .graph_state import _reached_code_paths
from .materials import inspect_log_materials
from .model import ActionError
from .scaffold import observe_physical_entries


def retention_connections(
    entry: EntryContext, targets: Sequence[Path]
) -> tuple[dict[str, Any], ...]:
    """Return connected targets and graph owners without evaluating evidence.

    Both current Markdown and normalized evidence remain roots until their
    owning sync completes. Producers outside that closure remain recorded but
    do not prohibit retention. This does not weaken mutation/deletion guards.
    """

    materials = inspect_log_materials(entry.log, declarations_only=True)
    failures = [failure for group in materials.failures.values() for failure in group]
    if failures:
        raise ActionError("retention.graph.unavailable", str(failures[0].error))
    code_inputs = {}
    if any(target.suffix == ".py" or target.is_dir() for target in targets):
        for invocation in materials.invocations:
            if invocation.script:
                code_inputs[invocation.identity] = tuple(
                    path.as_posix()
                    for path in _reached_code_paths(
                        Path(invocation.script),
                        materials.roots[invocation.material_owner],
                        entry.log.root,
                        materials.project_root,
                    )
                )
    graph = build_evaluation_graph(
        EvaluationGraphInputs(
            entries=(),
            invocations=materials.invocations,
            evidence_connections=_evidence_connections(entry),
            code_inputs=code_inputs,
            supported_output_directories=materials.supported_output_directories(),
        )
    )
    if not graph.complete:
        raise ActionError(
            "retention.graph.unavailable", "research graph exceeds its bounds"
        )
    trace, connected = trace_research_graph_materials(graph, materials.roots)
    matched = {
        path
        for path in connected
        for target in targets
        if Path(path) == target.resolve()
        or target.is_dir()
        and Path(path).is_relative_to(target.resolve())
    }
    if not matched:
        ambiguities = _target_ambiguities(graph, targets)
        if ambiguities:
            raise ActionError(
                "retention.graph.unavailable",
                "selected retention targets have unresolved graph connections: "
                + "; ".join(
                    f"{item.kind.value}: {item.subject}" for item in ambiguities
                ),
                records=tuple(item.as_dict() for item in ambiguities),
            )
        return ()
    traced = {(node.kind, node.identity) for node in trace.nodes}
    owners = [
        {
            "entry": node.entry,
            "command"
            if node.kind is NodeKind.COMMAND
            else "evidence": node.attributes.get("cid", node.identity),
        }
        for node in graph.nodes
        if node.kind in {NodeKind.COMMAND, NodeKind.EVIDENCE_RECORD}
        and (node.kind.value, node.identity) in traced
    ]
    nodes = {node.node_id: node for node in graph.nodes}
    owners.extend(
        {
            "entry": nodes[edge.target].entry,
            "command": nodes[edge.target].attributes["cid"],
        }
        for edge in graph.edges
        if edge.kind in {EdgeKind.SCRIPT_USE, EdgeKind.CODE_USE}
        and nodes[edge.source].identity in matched
    )
    return tuple(owners) + tuple(
        {"entry": entry.id, "material": path, "document": path}
        for path in sorted(matched)
    )


def _target_ambiguities(
    graph: ResearchGraph, targets: Sequence[Path]
) -> tuple[AmbiguityObservation, ...]:
    """Select direct target conflicts and uncertain evidence paths to targets.

    Follow all declared dependency branches, including multiple producers, so
    a target cannot be retained merely because the ordinary trace stopped at an
    ambiguity. Unrelated graph findings remain the validator's responsibility.
    """

    nodes = {node.node_id: node for node in graph.nodes}
    selected = {node.node_id for node in graph.nodes if _matches_targets(node, targets)}
    dependencies: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.kind in {
            EdgeKind.PRODUCTION,
            EdgeKind.CONSUMPTION,
            EdgeKind.ORIGIN,
            EdgeKind.SCRIPT_USE,
            EdgeKind.CODE_USE,
        }:
            source, target = edge.target, edge.source
            if nodes[edge.source].kind is NodeKind.EVIDENCE_RECORD:
                source, target = edge.source, edge.target
        elif edge.kind is EdgeKind.DECLARATION:
            if nodes[edge.source].kind is not NodeKind.EVIDENCE_RECORD:
                continue
            source, target = edge.source, edge.target
        elif edge.kind is EdgeKind.MEMBERSHIP:
            source, target = edge.target, edge.source
        else:
            continue
        dependencies.setdefault(source, set()).add(target)
        dependents.setdefault(target, set()).add(source)
    roots = {
        node.node_id for node in graph.nodes if node.kind is NodeKind.EVIDENCE_RECORD
    }
    relevant = _reachable(roots, dependencies) & _reachable(selected, dependents)
    return tuple(
        item
        for item in graph.ambiguities
        if item.subject in selected or item.subject in relevant
    )


def _matches_targets(node: ResearchNode, targets: Sequence[Path]) -> bool:
    if node.kind in {NodeKind.MATERIAL, NodeKind.SCRIPT, NodeKind.CODE}:
        path = Path(node.identity)
    elif node.kind is NodeKind.COLLECTION and isinstance(
        node.attributes.get("root"), str
    ):
        path = Path(str(node.attributes["root"]))
        if any(target.resolve().is_relative_to(path) for target in targets):
            return True
    else:
        return False
    return any(
        path == target.resolve()
        or target.is_dir()
        and path.is_relative_to(target.resolve())
        for target in targets
    )


def _reachable(roots: set[str], edges: dict[str, set[str]]) -> set[str]:
    reached = set(roots)
    pending = list(roots)
    while pending:
        for node in edges.get(pending.pop(), ()):
            if node not in reached:
                reached.add(node)
                pending.append(node)
    return reached


def _evidence_connections(entry: EntryContext) -> tuple[EvidenceConnection, ...]:
    connections = []
    for observed in observe_physical_entries(entry.log):
        data_path = observed.root / "data.json"
        data = (
            load_data_file(data_path, entry_root=observed.root)
            if data_path.exists()
            else None
        )
        evidence_path = observed.root / "evidence.json"
        if evidence_path.exists():
            evidence = load_evidence_file(
                evidence_path,
                log_root=entry.log.root,
                entry_root=observed.root,
            )
            for record in evidence.records:
                connections.append(
                    _connection(
                        observed.id,
                        "normalized-" + record.id,
                        record.document,
                        tuple(source.source for source in record.sources),
                        data,
                    )
                )
        for document in sorted(observed.root.glob("*.md")):
            text = document.read_text(encoding="utf-8")
            for marker in authored_eid_comments(text):
                authored = read_markdown_evidence(text, marker["id"])
                connections.append(
                    _connection(
                        observed.id,
                        "authored-" + marker["id"],
                        document.relative_to(entry.log.root).as_posix(),
                        tuple(str(source["source"]) for source in authored.sources),
                        data,
                    )
                )
    return tuple(connections)


def _connection(
    entry_id: str,
    record_id: str,
    document: str,
    tokens: tuple[str, ...],
    data: DataFile | None,
) -> EvidenceConnection:
    if data is None:
        raise ActionError(
            "retention.graph.unavailable", f"{document}: evidence has no data registry"
        )
    resolved = tuple(resolve_declared_input_token(token, data) for token in tokens)
    return EvidenceConnection(
        entry=entry_id,
        record=record_id,
        presentation=f"{document}:{record_id}",
        materials=tuple(item.path for item in resolved),
        origin_materials=frozenset(
            item.path for item in resolved if item.resource.origin
        ),
    )
