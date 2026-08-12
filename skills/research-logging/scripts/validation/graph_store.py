"""Persistence for independently owned research-log dependency-graph slices."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from .commands import log_owned_roots, script_inventory
from .contracts import (
    CanonicalRepositoryView,
    ValidationToolError,
)
from .discovery import ENTRY_ID_RE
from .graph import (
    DependencyGraph,
    EdgeKind,
    GraphContractError,
    GraphEdge,
)
from .inventory import (
    MaterialInventoryPolicy,
    display_path,
    hash_file,
    logical_display_path,
    owned_entry_folders,
    owned_inventory,
)

SLICE_SCHEMA_VERSION = 6
AGGREGATE_SCHEMA_VERSION = 2
SLICE_FILENAME = "validation-index.json"
REPOSITORY_DISCOVERY_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".conda",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "tmp",
        "venv",
    }
)
REPOSITORY_DISCOVERY_EXCLUDED_PREFIXES = (
    ".validation-",
    ".research-log-validation",
)
AGGREGATE_DIRECTORY = ".research-log-validation-index"
REPOSITORY_VIEW_SCHEMA = "canonical-graph-aggregate-v4"
REPOSITORY_VIEW_KINDS = frozenset(
    {
        "complete",
        "replacement",
        "diagnostic",
    }
)


@dataclass(frozen=True)
class RepositoryContributions:
    """Per-log slices and consuming-source snapshots contributing to a view."""

    cross_log_sources: Mapping[str, Mapping[str, Mapping[str, Any]]] = dataclass_field(
        default_factory=dict
    )
    slices: Mapping[str, Mapping[str, Any]] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class RepositoryViewScope:
    """Purpose and maintained-log coverage of one repository view."""

    kind: str = "complete"
    expected_summaries: Sequence[str] = ()
    refresh_summary: str | None = None
    cross_log_complete: bool = True


def _json_identity(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _log_namespace(summary: str) -> str:
    return Path(summary).with_suffix("").as_posix()


def inbound_dependencies(
    view: Mapping[str, Any], summary_identity: str
) -> list[dict[str, str]]:
    """Return the stable inbound dependency slice for one owning log."""

    result = []
    for value in view["graph_edges"]:
        edge = GraphEdge.from_dict(value)
        owner = f"{edge.target.namespace}.md"
        if owner != summary_identity:
            continue
        result.append(
            {
                "owner": owner,
                "path": edge.target.identity,
                "consumer": f"{edge.owner_log}.md",
                "source": edge.source.identity,
                "kind": edge.kind.value,
            }
        )
    return sorted(
        result,
        key=lambda item: tuple(
            item[key] for key in ("owner", "path", "consumer", "source", "kind")
        ),
    )


def _normalized_graph(graph: DependencyGraph) -> dict[str, Any]:
    """Serialize a graph with deduplicated origins and stable line-level records."""

    raw = graph.as_dict()
    origins: dict[str, dict[str, Any]] = {}
    for collection in (raw["nodes"], raw["edges"], raw["roots"]):
        for item in collection:
            identifiers = []
            for origin in item.pop("origins"):
                identity = _json_identity(origin)
                origins[identity] = origin
                identifiers.append(identity)
            item["origin_ids"] = identifiers
    return {
        "rules_version": raw["rules_version"],
        "origins": origins,
        "nodes": raw["nodes"],
        "edges": raw["edges"],
        "roots": raw["roots"],
    }


def _validated_origins(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise GraphContractError("normalized graph origins must be a mapping")
    for identity, origin in value.items():
        if not isinstance(identity, str) or not isinstance(origin, dict):
            raise GraphContractError("normalized graph origin is invalid")
        if _json_identity(origin) != identity:
            raise GraphContractError("normalized graph origin identity is invalid")
    return value


def _expanded_facts(
    collection: Any,
    origins: Mapping[str, dict[str, Any]],
    used_origins: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(collection, list):
        raise GraphContractError("normalized graph collection must be a list")
    result = []
    for item in collection:
        if not isinstance(item, dict) or "origin_ids" not in item:
            raise GraphContractError("normalized graph fact is invalid")
        identifiers = item["origin_ids"]
        if not isinstance(identifiers, list) or any(
            not isinstance(identity, str) or identity not in origins
            for identity in identifiers
        ):
            raise GraphContractError("normalized graph fact has invalid origins")
        used_origins.update(identifiers)
        expanded = {key: child for key, child in item.items() if key != "origin_ids"}
        expanded["origins"] = [origins[identity] for identity in identifiers]
        result.append(expanded)
    return result


def _expanded_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expand one normalized persisted graph into the canonical graph contract."""

    if not isinstance(value, Mapping):
        raise GraphContractError("normalized dependency graph must be an object")
    required = {"rules_version", "origins", "nodes", "edges", "roots"}
    if set(value) != required:
        raise GraphContractError("normalized dependency graph has incorrect fields")
    origins = _validated_origins(value["origins"])
    used_origins: set[str] = set()

    expanded = {
        "rules_version": value["rules_version"],
        "nodes": _expanded_facts(value["nodes"], origins, used_origins),
        "edges": _expanded_facts(value["edges"], origins, used_origins),
        "roots": _expanded_facts(value["roots"], origins, used_origins),
    }
    if used_origins != set(origins):
        raise GraphContractError("normalized graph contains an unused origin")
    return expanded


def empty_repository_view(rules_version: str) -> Dict[str, Any]:
    """Return an explicit empty cross-log view for isolated diagnostics."""

    if not rules_version.strip():
        raise GraphContractError("repository view rules version must not be empty")
    payload = {
        "schema_version": REPOSITORY_VIEW_SCHEMA,
        "validation_rules_version": rules_version,
        "scope": {
            "kind": "diagnostic",
            "expected_summaries": [],
            "refresh_summary": None,
            "cross_log_complete": True,
        },
        "material_owners": {},
        "cross_log_sources": {},
        "slices": {},
        "graph_edges": [],
    }
    payload["identity"] = _json_identity(payload)
    return payload


def repository_view(
    rules_version: str,
    material_owners: Mapping[str, Mapping[str, str]],
    graph_edges: Sequence[Mapping[str, Any]],
    contributions: RepositoryContributions | None = None,
    scope: RepositoryViewScope | None = None,
) -> Dict[str, Any]:
    """Build one canonical repository view with an explicit content identity."""

    contributions = contributions or RepositoryContributions()
    normalized_slices = contributions.slices
    scope = scope or RepositoryViewScope(
        expected_summaries=tuple(normalized_slices)
    )
    expected = sorted(scope.expected_summaries)
    payload = {
        "schema_version": REPOSITORY_VIEW_SCHEMA,
        "validation_rules_version": rules_version,
        "scope": {
            "kind": scope.kind,
            "expected_summaries": expected,
            "refresh_summary": scope.refresh_summary,
            "cross_log_complete": scope.cross_log_complete,
        },
        "material_owners": {
            path: dict(owner) for path, owner in sorted(material_owners.items())
        },
        "cross_log_sources": {
            summary: {path: dict(snapshot) for path, snapshot in sorted(inputs.items())}
            for summary, inputs in sorted(contributions.cross_log_sources.items())
        },
        "slices": {
            summary: {
                "path": snapshot["path"],
                "graph_identity": snapshot["graph_identity"],
                "source_identity": snapshot["source_identity"],
                "content_identity": dict(snapshot["content_identity"]),
            }
            for summary, snapshot in sorted(normalized_slices.items())
        },
        "graph_edges": sorted(
            (dict(edge) for edge in graph_edges), key=lambda edge: edge["identity"]
        ),
    }
    payload["identity"] = _json_identity(payload)
    validate_repository_view(payload, rules_version)
    return payload


def validate_repository_view(
    value: Mapping[str, Any], rules_version: str
) -> list[GraphEdge]:
    """Validate and decode one canonical repository dependency view.

    The view is fail-closed: missing fields, extra fields, duplicate edges,
    non-cross-log edges, or a rules mismatch are rejected rather than treated
    as an empty repository.
    """

    required = {
        "schema_version",
        "validation_rules_version",
        "scope",
        "identity",
        "material_owners",
        "cross_log_sources",
        "slices",
        "graph_edges",
    }
    if set(value) != required:
        raise GraphContractError("canonical repository view has incorrect fields")
    if value["schema_version"] != REPOSITORY_VIEW_SCHEMA:
        raise GraphContractError("unsupported canonical repository view schema")
    if value["validation_rules_version"] != rules_version:
        raise GraphContractError("canonical repository view uses different rules")
    _validate_repository_scope(value["scope"], value["slices"])
    identity = value["identity"]
    if not _is_sha256(identity):
        raise GraphContractError("canonical repository view identity is invalid")
    identity_payload = dict(value)
    identity_payload.pop("identity")
    if _json_identity(identity_payload) != identity:
        raise GraphContractError("canonical repository view identity does not match")
    _validate_repository_owners(value["material_owners"])
    _validate_repository_sources(value["cross_log_sources"])
    _validate_repository_slices(value["slices"])
    return _validated_repository_edges(value["graph_edges"])


def _validate_repository_scope(value: Any, slices: Any) -> None:
    """Validate whether a repository view is complete enough for its purpose."""

    if (
        not isinstance(value, Mapping)
        or set(value)
        != {
            "kind",
            "expected_summaries",
            "refresh_summary",
            "cross_log_complete",
        }
        or value.get("kind") not in REPOSITORY_VIEW_KINDS
        or not isinstance(value.get("expected_summaries"), list)
        or value["expected_summaries"] != sorted(set(value["expected_summaries"]))
        or any(
            not isinstance(summary, str) or not summary
            for summary in value["expected_summaries"]
        )
        or not isinstance(slices, Mapping)
        or not isinstance(value.get("cross_log_complete"), bool)
    ):
        raise GraphContractError("canonical repository scope is invalid")
    kind = value["kind"]
    refresh_summary = value["refresh_summary"]
    cross_log_complete = value["cross_log_complete"]
    if refresh_summary is not None and (
        not isinstance(refresh_summary, str)
        or refresh_summary not in value["expected_summaries"]
    ):
        raise GraphContractError("canonical repository refresh summary is invalid")
    expected = set(value["expected_summaries"])
    actual = set(slices)
    if kind == "complete" and (
        refresh_summary is not None or actual != expected or not cross_log_complete
    ):
        raise GraphContractError("complete repository scope lacks exact slices")
    if kind == "replacement" and (
        refresh_summary is None
        or not actual <= expected - {refresh_summary}
        or cross_log_complete != (actual == expected - {refresh_summary})
    ):
        raise GraphContractError("repository replacement view lacks exact other slices")
    if kind == "diagnostic" and (
        refresh_summary is not None
        or expected
        or actual
        or not cross_log_complete
    ):
        raise GraphContractError("diagnostic repository view must be empty")


def _validate_repository_owners(value: Any) -> None:
    if not isinstance(value, Mapping) or any(
        not isinstance(path, str)
        or not path
        or not isinstance(owner, Mapping)
        or set(owner) != {"namespace", "kind"}
        or not isinstance(owner["namespace"], str)
        or not owner["namespace"]
        or owner["kind"] not in {"artifact", "collection", "script"}
        for path, owner in value.items()
    ):
        raise GraphContractError("canonical repository material owners are invalid")


def validated_material_owners(value: Any) -> Dict[str, Dict[str, str]]:
    """Validate and normalize one repository material-ownership snapshot."""

    _validate_repository_owners(value)
    return {
        identity: dict(owner) for identity, owner in sorted(value.items())
    }


def _validate_repository_sources(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise GraphContractError("canonical repository cross-log sources are invalid")
    for summary, inputs in value.items():
        if (
            not isinstance(summary, str)
            or not summary
            or not isinstance(inputs, Mapping)
        ):
            raise GraphContractError(
                "canonical repository cross-log sources are invalid"
            )
        for path, snapshot in inputs.items():
            if not isinstance(path, str) or not path:
                raise GraphContractError(
                    "canonical repository cross-log source path is invalid"
                )
            _source_snapshot(snapshot)


def _validate_repository_slices(value: Any) -> None:
    """Validate the exact per-log slices contributing to a repository view."""

    if not isinstance(value, Mapping):
        raise GraphContractError("canonical repository slices are invalid")
    paths: set[str] = set()
    for summary, snapshot in value.items():
        if (
            not isinstance(summary, str)
            or not summary
            or not isinstance(snapshot, Mapping)
            or set(snapshot)
            != {
                "path",
                "graph_identity",
                "source_identity",
                "content_identity",
            }
            or not isinstance(snapshot["path"], str)
            or not snapshot["path"]
            or snapshot["path"] in paths
            or not _is_sha256(snapshot["graph_identity"])
            or not _is_sha256(snapshot["source_identity"])
        ):
            raise GraphContractError("canonical repository slice is invalid")
        paths.add(snapshot["path"])
        _source_snapshot(snapshot["content_identity"])


def _validated_repository_edges(value: Any) -> list[GraphEdge]:
    if not isinstance(value, list):
        raise GraphContractError("canonical repository graph_edges must be a list")
    edges = [GraphEdge.from_dict(item) for item in value]
    if any(edge.kind is not EdgeKind.CROSS_LOG_USE for edge in edges):
        raise GraphContractError(
            "canonical repository view contains a non-cross-log edge"
        )
    identities = [edge.identity for edge in edges]
    if len(set(identities)) != len(identities):
        raise GraphContractError("canonical repository view contains duplicate edges")
    return edges


def graph_slice(graph: DependencyGraph, summary: str) -> DependencyGraph:
    """Return repository-level cross-log facts owned by one maintained log."""

    namespace = _log_namespace(summary)
    edges = tuple(
        edge
        for edge in graph.edges
        if edge.owner_log == namespace and edge.kind is EdgeKind.CROSS_LOG_USE
    )
    required = {key for edge in edges for key in (edge.source, edge.target)}
    nodes = tuple(node for node in graph.nodes if node.key in required)
    return DependencyGraph(graph.rules_version, nodes, edges, ())


def _source_snapshot(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("size"), int)
        or value["size"] < 0
        or not _is_sha256(value.get("sha256"))
    ):
        raise GraphContractError("cross-log source input lacks a content identity")
    return {"size": value["size"], "sha256": value["sha256"]}


def _cross_log_source_contract(
    graph: DependencyGraph,
    input_files: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    source_inputs: dict[str, dict[str, Any]] = {}
    edge_sources: dict[str, list[str]] = {}
    nodes = {node.key: node for node in graph.nodes}
    for edge in graph.edges:
        if edge.kind is not EdgeKind.CROSS_LOG_USE:
            continue
        identities = {
            item.identity
            for origin in (*edge.origins, *nodes[edge.source].origins)
            for item in origin.inputs
            if item.identity in input_files
        }
        if not identities:
            raise GraphContractError(
                "cross-log edge lacks an identifiable consuming source input: "
                f"{edge.identity}"
            )
        edge_sources[edge.identity] = sorted(identities)
        for identity in identities:
            source_inputs[identity] = _source_snapshot(input_files[identity])
    return dict(sorted(source_inputs.items())), dict(sorted(edge_sources.items()))


def slice_record(
    graph: DependencyGraph,
    summary: str,
    input_files: Mapping[str, Any],
    material_owners: Mapping[str, Mapping[str, str]] | None = None,
) -> Dict[str, Any]:
    """Return the exact independently stageable per-log record.

    Production slices pass the complete repository ownership map. Synthetic
    graphs may omit it when they intentionally contain no owned inventory.
    """

    sliced = graph_slice(graph, summary)
    namespace = _log_namespace(summary)
    material_owners = material_owners or {}
    graph_value = _normalized_graph(sliced)
    source_inputs, edge_sources = _cross_log_source_contract(sliced, input_files)
    source_contract = {
        "source_inputs": source_inputs,
        "edge_sources": edge_sources,
    }
    return {
        "schema_version": SLICE_SCHEMA_VERSION,
        "validation_rules_version": sliced.rules_version,
        "summary": summary,
        "namespace": namespace,
        "graph_identity": sliced.identity,
        "material_owners": {
            identity: dict(owner)
            for identity, owner in sorted(material_owners.items())
            if owner.get("namespace") == namespace
        },
        **source_contract,
        "source_identity": _json_identity(source_contract),
        "graph": graph_value,
    }


def _slice_summary(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise GraphContractError("validation index slice must be an object")
    if set(value) != {
        "schema_version",
        "validation_rules_version",
        "summary",
        "namespace",
        "graph_identity",
        "material_owners",
        "source_inputs",
        "edge_sources",
        "source_identity",
        "graph",
    }:
        raise GraphContractError("validation index slice has incorrect fields")
    if value["schema_version"] != SLICE_SCHEMA_VERSION:
        raise GraphContractError("unsupported validation index slice schema")
    summary = value["summary"]
    if not isinstance(summary, str):
        raise GraphContractError("validation index summary must be a string")
    for field in (
        "validation_rules_version",
        "namespace",
        "graph_identity",
        "source_identity",
    ):
        if not isinstance(value[field], str):
            raise GraphContractError(
                f"validation index {field.replace('_', ' ')} must be a string"
            )
    if value["namespace"] != _log_namespace(summary):
        raise GraphContractError("validation index namespace does not match summary")
    _validate_repository_owners(value["material_owners"])
    if any(
        owner["namespace"] != value["namespace"]
        for owner in value["material_owners"].values()
    ):
        raise GraphContractError("validation index contains a foreign material owner")
    return summary


def _slice_graph(value: Mapping[str, Any]) -> DependencyGraph:
    graph_value = _expanded_graph(value["graph"])
    graph = DependencyGraph.from_dict(graph_value)
    if graph.rules_version != value["validation_rules_version"]:
        raise GraphContractError("slice and graph rules versions differ")
    if graph.identity != value["graph_identity"]:
        raise GraphContractError("slice graph identity does not match graph")
    return graph


def _normalized_slice_inputs(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise GraphContractError("slice source inputs must be a mapping")
    normalized = {
        identity: _source_snapshot(snapshot)
        for identity, snapshot in value.items()
        if isinstance(identity, str) and identity
    }
    if len(normalized) != len(value):
        raise GraphContractError("slice source input identity is invalid")
    return normalized


def _normalized_slice_edges(
    value: Any,
    graph: DependencyGraph,
    source_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise GraphContractError("slice edge sources must be a mapping")
    cross_edges = {
        edge.identity for edge in graph.edges if edge.kind is EdgeKind.CROSS_LOG_USE
    }
    normalized: dict[str, list[str]] = {}
    for edge_identity, identities in value.items():
        if (
            not isinstance(edge_identity, str)
            or edge_identity not in cross_edges
            or not isinstance(identities, list)
            or not identities
            or identities != sorted(set(identities))
            or any(identity not in source_inputs for identity in identities)
        ):
            raise GraphContractError("slice cross-log edge source contract is invalid")
        normalized[edge_identity] = identities
    if set(normalized) != cross_edges:
        raise GraphContractError("slice source contract does not cover cross-log edges")
    return normalized


def _validate_slice_sources(value: Mapping[str, Any], graph: DependencyGraph) -> None:
    source_inputs = value["source_inputs"]
    edge_sources = value["edge_sources"]
    normalized_inputs = _normalized_slice_inputs(source_inputs)
    normalized_edges = _normalized_slice_edges(edge_sources, graph, normalized_inputs)
    source_contract = {
        "source_inputs": normalized_inputs,
        "edge_sources": normalized_edges,
    }
    if (
        not _is_sha256(value["source_identity"])
        or _json_identity(source_contract) != value["source_identity"]
    ):
        raise GraphContractError("slice source identity does not match source contract")


def load_slice(value: Mapping[str, Any]) -> Tuple[str, DependencyGraph]:
    """Load and validate one exact per-log graph slice."""

    summary = _slice_summary(value)
    graph = _slice_graph(value)
    _validate_slice_sources(value, graph)
    return summary, graph


def aggregate_records(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Build the small disposable aggregate from independent log slices."""

    logs = []
    incoming: Dict[str, list[Dict[str, Any]]] = {}
    rules_versions = set()
    summaries: set[str] = set()
    namespaces: set[str] = set()
    for value in records:
        summary, graph = load_slice(value)
        namespace = _log_namespace(summary)
        if summary in summaries or namespace in namespaces:
            raise GraphContractError(
                f"duplicate validation index slice for log namespace: {namespace}"
            )
        summaries.add(summary)
        namespaces.add(namespace)
        rules_versions.add(graph.rules_version)
        logs.append(
            {
                "summary": summary,
                "namespace": namespace,
                "graph_identity": graph.identity,
                "source_identity": value["source_identity"],
            }
        )
        for edge in graph.edges:
            if edge.kind is not EdgeKind.CROSS_LOG_USE:
                continue
            owner_summary = f"{edge.target.namespace}.md"
            incoming.setdefault(owner_summary, []).append(edge.as_dict())
    if len(rules_versions) > 1:
        raise GraphContractError("aggregate slices use different rules versions")
    logs.sort(key=lambda item: item["summary"])
    normalized_incoming = {
        owner: sorted(edges, key=lambda edge: edge["identity"])
        for owner, edges in sorted(incoming.items())
    }
    payload = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "validation_rules_version": next(iter(rules_versions), ""),
        "logs": logs,
        "incoming": normalized_incoming,
        "sources": {
            value["summary"]: value["source_inputs"]
            for value in sorted(records, key=lambda item: item["summary"])
            if value["source_inputs"]
        },
    }
    payload["identity"] = _json_identity(payload)
    return payload


def aggregate_material_owners(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """Join the independently owned material maps from canonical log slices."""

    owners: Dict[str, Dict[str, str]] = {}
    for value in records:
        load_slice(value)
        for identity, owner in value["material_owners"].items():
            existing = owners.get(identity)
            if existing is not None and existing != owner:
                raise ValidationToolError(
                    "research-log material has multiple owners: "
                    f"{identity} ({existing}, {owner})"
                )
            owners[identity] = dict(owner)
    return dict(sorted(owners.items()))


def aggregate_graph_edges(aggregate: Mapping[str, Any]) -> list[Dict[str, Any]]:
    """Return canonical typed cross-log edges without a lossy projection."""

    return sorted(
        (edge for edges in aggregate.get("incoming", {}).values() for edge in edges),
        key=lambda edge: edge["identity"],
    )


def aggregate_files(
    aggregate: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split one aggregate value into its small manifest and incoming map."""

    incoming = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "validation_rules_version": aggregate["validation_rules_version"],
        "incoming": aggregate["incoming"],
        "sources": aggregate["sources"],
    }
    incoming["identity"] = _json_identity(incoming)
    manifest = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "validation_rules_version": aggregate["validation_rules_version"],
        "logs": aggregate["logs"],
        "incoming_identity": incoming["identity"],
    }
    manifest["identity"] = _json_identity(manifest)
    return manifest, incoming


def _validate_aggregate_logs(value: Any) -> None:
    if not isinstance(value, list):
        raise GraphContractError("aggregate manifest logs must be a list")
    summaries: set[str] = set()
    namespaces: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "summary",
            "namespace",
            "graph_identity",
            "source_identity",
        }:
            raise GraphContractError(f"aggregate log {index} has incorrect fields")
        if not all(isinstance(item[field], str) for field in item):
            raise GraphContractError(f"aggregate log {index} fields must be strings")
        if item["namespace"] != _log_namespace(item["summary"]):
            raise GraphContractError(f"aggregate log {index} namespace is invalid")
        if not _is_sha256(item["graph_identity"]):
            raise GraphContractError(f"aggregate log {index} identity is invalid")
        if not _is_sha256(item["source_identity"]):
            raise GraphContractError(
                f"aggregate log {index} source identity is invalid"
            )
        if item["summary"] in summaries or item["namespace"] in namespaces:
            raise GraphContractError("aggregate manifest contains duplicate logs")
        summaries.add(item["summary"])
        namespaces.add(item["namespace"])


def _validate_aggregate_incoming(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise GraphContractError("aggregate incoming map must be an object")
    identities: set[str] = set()
    for owner, raw_edges in value.items():
        if not isinstance(owner, str) or not isinstance(raw_edges, list):
            raise GraphContractError("aggregate incoming entries are invalid")
        for raw_edge in raw_edges:
            edge = GraphEdge.from_dict(raw_edge)
            if edge.kind is not EdgeKind.CROSS_LOG_USE:
                raise GraphContractError("aggregate contains a non-cross-log edge")
            if owner != f"{edge.target.namespace}.md":
                raise GraphContractError("aggregate incoming edge owner is invalid")
            if edge.identity in identities:
                raise GraphContractError("aggregate contains duplicate incoming edges")
            identities.add(edge.identity)


def _validate_aggregate_file_fields(
    manifest: Mapping[str, Any], incoming: Mapping[str, Any]
) -> None:
    if set(manifest) != {
        "schema_version",
        "validation_rules_version",
        "logs",
        "incoming_identity",
        "identity",
    }:
        raise GraphContractError("aggregate manifest has incorrect fields")
    if set(incoming) != {
        "schema_version",
        "validation_rules_version",
        "incoming",
        "sources",
        "identity",
    }:
        raise GraphContractError("aggregate incoming record has incorrect fields")
    for record, description in ((manifest, "manifest"), (incoming, "incoming")):
        if not isinstance(record["validation_rules_version"], str):
            raise GraphContractError(
                f"aggregate {description} rules version must be a string"
            )
        if not isinstance(record["identity"], str):
            raise GraphContractError(
                f"aggregate {description} identity must be a string"
            )
    if not isinstance(manifest["incoming_identity"], str):
        raise GraphContractError("aggregate incoming identity must be a string")


def _record_identity(value: Mapping[str, Any], description: str) -> str:
    payload = dict(value)
    identity = payload.pop("identity", None)
    if identity != _json_identity(payload):
        raise GraphContractError(f"aggregate {description} identity is invalid")
    return identity


def _validate_aggregate_pair(
    manifest: Mapping[str, Any], incoming: Mapping[str, Any], incoming_identity: str
) -> None:
    if manifest.get("incoming_identity") != incoming_identity:
        raise GraphContractError("aggregate files do not identify each other")
    if any(
        record.get("schema_version") != AGGREGATE_SCHEMA_VERSION
        for record in (manifest, incoming)
    ):
        raise GraphContractError("unsupported aggregate schema")
    if manifest.get("validation_rules_version") != incoming.get(
        "validation_rules_version"
    ):
        raise GraphContractError("aggregate files use different rules versions")


def _validate_aggregate_sources(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise GraphContractError("aggregate sources must be an object")
    for summary, inputs in value.items():
        if not isinstance(summary, str) or not isinstance(inputs, Mapping):
            raise GraphContractError("aggregate source entry is invalid")
        for identity, snapshot in inputs.items():
            if not isinstance(identity, str) or not identity:
                raise GraphContractError("aggregate source path is invalid")
            _source_snapshot(snapshot)


def load_aggregate_files(
    manifest: Mapping[str, Any], incoming: Mapping[str, Any]
) -> Dict[str, Any]:
    """Validate and join the disposable aggregate files."""

    if not isinstance(manifest, Mapping) or not isinstance(incoming, Mapping):
        raise GraphContractError("aggregate files must be objects")
    _validate_aggregate_file_fields(manifest, incoming)
    manifest_identity = _record_identity(manifest, "manifest")
    incoming_identity = _record_identity(incoming, "incoming")
    _validate_aggregate_pair(manifest, incoming, incoming_identity)
    _validate_aggregate_logs(manifest["logs"])
    _validate_aggregate_incoming(incoming["incoming"])
    _validate_aggregate_sources(incoming["sources"])
    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "validation_rules_version": manifest["validation_rules_version"],
        "logs": manifest["logs"],
        "incoming": incoming["incoming"],
        "sources": incoming["sources"],
        "identity": manifest_identity,
    }


def slice_paths(
    project_root: Path, summaries: Optional[Sequence[Path]] = None
) -> Iterable[Path]:
    """Yield maintained per-log slice files in deterministic order."""

    discovered = (
        summaries
        if summaries is not None
        else discover_repository_summaries(project_root)
    )
    for summary in discovered:
        path = summary.with_suffix("") / SLICE_FILENAME
        if path.is_file():
            yield path


def discover_repository_summaries(project_root: Path) -> list[Path]:
    """Discover maintained summaries across the project, excluding generated trees."""

    project_root = project_root.resolve()
    summaries = []
    for raw_root, directory_names, file_names in os.walk(project_root):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in REPOSITORY_DISCOVERY_EXCLUDED_DIRECTORIES
            and not any(
                name.startswith(prefix)
                for prefix in REPOSITORY_DISCOVERY_EXCLUDED_PREFIXES
            )
        )
        root = Path(raw_root)
        for name in sorted(file_names):
            if not name.endswith(".md"):
                continue
            summary = root / name
            if (summary.with_suffix("") / "entries").is_dir():
                summaries.append(summary.resolve())
    return sorted(summaries)


def _repository_log_record(project_root: Path, summary: Path) -> Dict[str, Any]:
    summary = summary.resolve()
    log_root = summary.with_suffix("")
    entries_root = log_root / "entries"
    entry_paths = sorted(
        path.resolve()
        for path in entries_root.rglob("*.md")
        if ENTRY_ID_RE.fullmatch(path.stem)
    )
    script_roots = {log_root / "scripts"}
    script_roots.update(path.parent / "scripts" for path in entry_paths)
    scripts = sorted(
        {script for root in script_roots for script in script_inventory(root)}
    )
    return {
        "summary": display_path(summary, project_root),
        "summary_path": summary,
        "root": display_path(log_root, project_root),
        "root_path": log_root,
        "owned_roots": log_owned_roots(log_root),
        "entries": entry_paths,
        "scripts": scripts,
    }


def discover_repository_logs(project_root: Path) -> List[Dict[str, Any]]:
    """Discover maintained research logs and their owned script surfaces."""

    return [
        _repository_log_record(project_root, summary)
        for summary in discover_repository_summaries(project_root)
    ]


def _material_owners_for_records(
    project_root: Path,
    policy: MaterialInventoryPolicy,
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, str]]:
    owners: Dict[str, Dict[str, str]] = {}
    for record in records:
        namespace = Path(record["summary"]).with_suffix("").as_posix()
        log_root = cast(Path, record["root_path"])
        folder_entry_ids: Dict[Path, set[str]] = {}
        for entry_path in cast(List[Path], record["entries"]):
            folder_entry_ids.setdefault(entry_path.parent, set()).add(entry_path.stem)
        entry_folders = owned_entry_folders(log_root, folder_entry_ids)
        inventory = owned_inventory(
            log_root,
            entry_folders,
            project_root,
            policy,
        )
        for item in [
            *(
                item
                for materials in inventory.by_folder.values()
                for item in materials
            ),
            *inventory.log_material,
        ]:
            identity = logical_display_path(item.logical_path, project_root)
            kind = "collection" if item.directory else item.kind
            owner = {"namespace": namespace, "kind": kind}
            existing = owners.get(identity)
            if existing is not None and existing != owner:
                raise ValidationToolError(
                    "research-log material has multiple owners: "
                    f"{identity} ({existing}, {owner})"
                )
            owners[identity] = owner
    return dict(sorted(owners.items()))


def repository_material_owners(
    project_root: Path, policy: MaterialInventoryPolicy
) -> Dict[str, Dict[str, str]]:
    """Return the mechanically discovered owner of all log-relative material."""

    return _material_owners_for_records(
        project_root, policy, discover_repository_logs(project_root)
    )


def log_material_owners(
    project_root: Path,
    summary: Path,
    policy: MaterialInventoryPolicy,
    *,
    summaries: Optional[Sequence[Path]] = None,
) -> Dict[str, Dict[str, str]]:
    """Return the current material ownership map for one maintained log."""

    summary_identity = display_path(summary.resolve(), project_root)
    discovered = (
        summaries
        if summaries is not None
        else discover_repository_summaries(project_root)
    )
    summaries = [
        candidate
        for candidate in discovered
        if display_path(candidate, project_root) == summary_identity
    ]
    if len(summaries) != 1:
        raise ValidationToolError(
            f"replacement summary is not a maintained research log: {summary_identity}"
        )
    return _material_owners_for_records(
        project_root,
        policy,
        [_repository_log_record(project_root, summaries[0])],
    )


def repository_identity_path(identity: str, project_root: Path) -> Path:
    """Resolve one persisted validation identity against the project root."""

    path = Path(identity)
    candidate = path if path.is_absolute() else project_root / path
    return Path(os.path.abspath(candidate))


def _content_identity(path: Path) -> Dict[str, Any]:
    digest, size = hash_file(path)
    return {"size": size, "sha256": digest}


def validate_slice_source_inputs(project_root: Path, value: Mapping[str, Any]) -> bool:
    """Return whether one canonical slice still has exact source inputs."""

    load_slice(value)
    for identity, expected in value["source_inputs"].items():
        path = repository_identity_path(identity, project_root)
        if not path.is_file() or _content_identity(path) != expected:
            return False
    return True


class RepositorySliceSet(NamedTuple):
    """Validated slice records and exact files supplying them."""

    records: List[Dict[str, Any]]
    snapshots: Dict[str, Dict[str, Any]]


def _load_repository_slice(
    path: Path, project_root: Path, rules_version: str
) -> tuple[dict[str, Any], str, DependencyGraph]:
    """Load one structurally current repository slice."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationToolError(f"expected JSON object: {path}")
    try:
        summary, graph = load_slice(value)
    except GraphContractError as exc:
        raise ValidationToolError(
            f"canonical graph slice is invalid: {path}: {exc}"
        ) from exc
    if graph.rules_version != rules_version:
        raise ValidationToolError(
            f"canonical graph slice uses different validation rules: {path}"
        )
    if not validate_slice_source_inputs(project_root, value):
        raise ValidationToolError(
            f"canonical graph slice has changed cross-log source inputs: {path}"
        )
    return value, summary, graph


def _usable_repository_slice(
    path: Path,
    project_root: Path,
    rules_version: str,
    *,
    allow_unusable: bool,
) -> tuple[dict[str, Any], str, DependencyGraph] | None:
    try:
        return _load_repository_slice(path, project_root, rules_version)
    except ValidationToolError:
        if allow_unusable:
            return None
        raise


def repository_slice_set(
    project_root: Path,
    rules_version: str,
    *,
    replacing_summary: Optional[str] = None,
    summaries: Optional[Sequence[Path]] = None,
    allow_unusable: bool = False,
) -> RepositorySliceSet:
    """Load exact slices, excluding only the log explicitly being replaced."""

    records: List[Dict[str, Any]] = []
    snapshots: Dict[str, Dict[str, Any]] = {}
    for path in slice_paths(project_root, summaries):
        path_summary = display_path(path.parent.with_suffix(".md"), project_root)
        if path_summary == replacing_summary:
            continue
        loaded = _usable_repository_slice(
            path,
            project_root,
            rules_version,
            allow_unusable=allow_unusable,
        )
        if loaded is None:
            continue
        value, summary, graph = loaded
        records.append(value)
        snapshots[summary] = {
            "path": display_path(path, project_root),
            "graph_identity": graph.identity,
            "source_identity": value["source_identity"],
            "content_identity": _content_identity(path),
        }
    if len(snapshots) != len(records):
        raise ValidationToolError("canonical graph slices contain duplicate logs")
    return RepositorySliceSet(records, dict(sorted(snapshots.items())))


def build_repository_aggregate(
    project_root: Path, rules_version: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the disposable repository view from independently owned slices."""

    started = time.monotonic()
    summaries = discover_repository_summaries(project_root)
    paths = list(slice_paths(project_root, summaries))
    records = repository_slice_set(
        project_root, rules_version, summaries=summaries
    ).records
    aggregate = aggregate_records(records)
    if not records:
        aggregate["validation_rules_version"] = rules_version
    expected_logs = {
        display_path(summary, project_root)
        for summary in summaries
    }
    actual_logs = {item["summary"] for item in aggregate["logs"]}
    if actual_logs != expected_logs:
        raise ValidationToolError(
            "canonical graph aggregate requires one current slice per maintained log: "
            f"missing={sorted(expected_logs - actual_logs)!r}; "
            f"extra={sorted(actual_logs - expected_logs)!r}"
        )
    if aggregate["validation_rules_version"] != rules_version:
        raise ValidationToolError(
            "canonical graph slices do not use the requested validation rules"
        )
    return aggregate, {
        "status": "rebuilt",
        "logs": len(actual_logs),
        "inputs": len(paths),
        "edges": sum(len(value) for value in aggregate["incoming"].values()),
        "scripts_parsed": 0,
        "logs_rebuilt": len(actual_logs),
        "files_hashed": len(paths),
        "bytes_hashed": sum(path.stat().st_size for path in paths),
        "elapsed_seconds": time.monotonic() - started,
    }


def replacement_repository_view(
    project_root: Path,
    summary: Path,
    rules_version: str,
    material_policy: MaterialInventoryPolicy,
    *,
    summaries: Optional[Sequence[Path]] = None,
) -> CanonicalRepositoryView:
    """Build the complete-other-slices view for one ordinary replacement."""

    summary_identity = display_path(summary.resolve(), project_root)
    summaries = (
        list(summaries)
        if summaries is not None
        else discover_repository_summaries(project_root)
    )
    expected_logs = {
        display_path(candidate, project_root)
        for candidate in summaries
    }
    if summary_identity not in expected_logs:
        raise ValidationToolError(
            f"replacement summary is not a maintained research log: {summary_identity}"
        )
    slices = repository_slice_set(
        project_root,
        rules_version,
        replacing_summary=summary_identity,
        summaries=summaries,
        allow_unusable=True,
    )
    aggregate = aggregate_records(slices.records)
    actual_logs = {item["summary"] for item in aggregate["logs"]}
    required_others = expected_logs - {summary_identity}
    if not actual_logs <= required_others:
        raise ValidationToolError(
            "repository replacement contains unexpected log slices: "
            f"{sorted(actual_logs - required_others)!r}"
        )
    complete = actual_logs == required_others
    material_owners = (
        aggregate_material_owners(slices.records)
        if complete
        else _material_owners_for_records(
            project_root,
            material_policy,
            [
                _repository_log_record(project_root, candidate)
                for candidate in summaries
            ],
        )
    )
    for identity, owner in log_material_owners(
        project_root, summary, material_policy, summaries=summaries
    ).items():
        existing = material_owners.get(identity)
        if existing is not None and existing != owner:
            raise ValidationToolError(
                "research-log material has multiple owners: "
                f"{identity} ({existing}, {owner})"
            )
        material_owners[identity] = owner
    return cast(
        CanonicalRepositoryView,
        repository_view(
            rules_version,
            material_owners,
            aggregate_graph_edges(aggregate),
            contributions=RepositoryContributions(
                cross_log_sources=aggregate["sources"],
                slices=slices.snapshots,
            ),
            scope=RepositoryViewScope(
                kind="replacement",
                expected_summaries=sorted(expected_logs),
                refresh_summary=summary_identity,
                cross_log_complete=complete,
            ),
        ),
    )
