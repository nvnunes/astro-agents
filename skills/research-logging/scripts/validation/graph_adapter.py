"""Adapters from validator discovery records to canonical graph facts.

The adapter is deliberately read-only with respect to scan and adjudication
records.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    cast,
)

from .compatibility import invocation_identities
from .contracts import ScanRecord, ValidationToolError
from .graph import (
    DependencyGraph,
    EdgeKind,
    FactOrigin,
    GraphBuilder,
    GraphContractError,
    NodeKey,
    NodeKind,
    OriginInput,
    OriginKind,
    RootPolicy,
)
from .graph_queries import orphan_nodes
from .orphan_rules import effective_basis
from .producer_bindings import (
    ProducerBindingInvocation,
    ProducerBindingOptions,
    producer_binding_invocation_cache,
    verify_producer_binding,
)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _origin(
    scan: Mapping[str, Any],
    resolver: str,
    inputs: Iterable[Tuple[str, Any]],
    *,
    semantic_scope: str = "",
) -> FactOrigin:
    material = tuple(
        sorted(
            OriginInput(identity=identity, fingerprint=_fingerprint(value))
            for identity, value in inputs
        )
    )
    return FactOrigin(
        kind=OriginKind.SEMANTIC if semantic_scope else OriginKind.MECHANICAL,
        resolver=resolver,
        inputs=material,
        rules_version=scan["validation_rules_version"],
        reviewed_scope=semantic_scope,
    )


def _log_namespace(summary: str) -> str:
    path = Path(summary)
    return path.with_suffix("").as_posix()


def _identity_lookup(scan: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(Path(raw).resolve()): identity
        for identity, raw in scan.get("resolved_paths", {}).items()
    }


def _path_identity(scan: Mapping[str, Any], lookup: Mapping[str, str], raw: str) -> str:
    resolved = str(Path(raw).resolve())
    if resolved in lookup:
        return lookup[resolved]
    path = Path(resolved)
    project_root = Path(scan["project_root"]).resolve()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _local_identities(scan: Mapping[str, Any]) -> Set[str]:
    identities = set(scan.get("script_inventory", []))
    for entry in scan.get("entries", []):
        identities.update(
            candidate["identity"]
            for candidate in entry.get("orphan_inventory", [])
            if isinstance(candidate.get("identity"), str)
            and not candidate["identity"].startswith("<")
        )
    return identities


def _material_namespace(
    identity: str,
    local_namespace: str,
    local_identities: Set[str],
) -> str:
    prefix = identity.rstrip("/") + "/"
    if (
        identity in local_identities
        or identity.startswith(local_namespace + "/")
        or any(local.startswith(prefix) for local in local_identities)
    ):
        return local_namespace
    return "external"


def _material_kind(
    scan: Mapping[str, Any],
    identity: str,
) -> NodeKind:
    if identity in set(scan.get("script_inventory", [])):
        return NodeKind.SCRIPT
    check = scan.get("mechanical_checks", {}).get(identity, {})
    membership = scan.get("directory_memberships", {}).get(identity)
    if check.get("type") == "directory" or membership is not None:
        return NodeKind.COLLECTION
    return NodeKind.ARTIFACT


def _entry_nodes(
    scan: Mapping[str, Any], builder: GraphBuilder, namespace: str, log: NodeKey
) -> Dict[str, NodeKey]:
    result = {}
    for entry in scan.get("entries", []):
        entry_id = entry["id"]
        key = NodeKey(namespace, NodeKind.ENTRY, entry_id)
        origin = _origin(
            scan,
            "entry-discovery",
            [
                (
                    entry.get("path", entry_id),
                    scan.get("files", {}).get(
                        entry.get("path"),
                        {
                            "path": entry.get("path"),
                            "scope_kind": entry.get("scope_kind", "entry"),
                        },
                    ),
                )
            ],
        )
        builder.add_node(
            key,
            origin,
            {
                "scope_kind": entry.get("scope_kind", "entry"),
                "path": entry.get("path"),
            },
        )
        builder.add_edge(EdgeKind.BELONGS_TO_LOG, key, log, namespace, origin)
        result[entry_id] = key
    return result


def _orphanable_inventory(scan: Mapping[str, Any]) -> Set[str]:
    """Return the complete mechanically discoverable orphan candidate inventory."""

    result = set(scan.get("script_inventory", []))
    for entry in scan.get("entries", []):
        result.update(
            candidate["identity"]
            for candidate in entry.get("orphan_inventory", [])
            if isinstance(candidate.get("identity"), str)
        )
        for candidate in entry.get("candidate_targets", []):
            identity = candidate.get("identity")
            if not isinstance(identity, str):
                continue
            if candidate.get("mechanical", {}).get("status") == "missing":
                continue
            roles = set(candidate.get("role_hints", []))
            if roles & {"workspace", "dependency-container"}:
                continue
            result.add(identity)
    return result


def _material_ownership_index(
    scan: Mapping[str, Any],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Index exact material owners and ordered entry-root fallbacks once."""

    exact: dict[str, str] = {}
    roots: list[tuple[str, str]] = []
    for entry in scan.get("entries", []):
        entry_id = entry.get("id")
        if not isinstance(entry_id, str):
            continue
        for candidate in (
            *entry.get("candidate_targets", []),
            *entry.get("orphan_inventory", []),
        ):
            identity = candidate.get("identity")
            if isinstance(identity, str):
                exact.setdefault(identity, entry_id)
        path = entry.get("path")
        if isinstance(path, str):
            roots.append((Path(path).parent.as_posix() + "/", entry_id))
    return exact, roots


def _material_owner_entry(
    identity: str,
    default: str,
    exact: Mapping[str, str],
    roots: Sequence[tuple[str, str]],
) -> str:
    owner = exact.get(identity)
    if owner is not None:
        return owner
    for entry_root, entry_id in roots:
        if identity.startswith(entry_root):
            return entry_id
    return default


@dataclass
class _GraphBuildState:
    scan: Mapping[str, Any]
    namespace: str
    builder: GraphBuilder
    log: NodeKey
    entries: Dict[str, NodeKey]
    default_entry: Optional[NodeKey]
    lookup: Dict[str, str]
    invocation_lookup: Mapping[str, ProducerBindingInvocation]
    local_identities: Set[str]
    orphanable: Set[str]
    material_owners: Mapping[str, str]
    entry_roots: Sequence[tuple[str, str]]
    material_keys: Dict[str, NodeKey] = field(default_factory=dict)
    token_keys: Dict[Tuple[str, str], NodeKey] = field(default_factory=dict)

    def ensure_material(self, identity: str) -> NodeKey:
        """Return the uniquely owned material node for one discovered identity."""

        current = self.material_keys.get(identity)
        if current is not None:
            return current
        owner_namespace = _material_namespace(
            identity,
            self.namespace,
            self.local_identities,
        )
        kind = _material_kind(self.scan, identity)
        if owner_namespace == "external" and kind is not NodeKind.SCRIPT:
            kind = NodeKind.EXTERNAL_SOURCE
        key = NodeKey(owner_namespace, kind, identity)
        source = (
            self.scan.get("files", {}).get(identity)
            or self.scan.get("directory_memberships", {}).get(identity)
            or self.scan.get("mechanical_checks", {}).get(identity)
            or self.scan.get("resolved_paths", {}).get(identity)
            or {"identity": identity}
        )
        origin = _origin(self.scan, "material-discovery", [(identity, source)])
        self.builder.add_node(
            key,
            origin,
            {
                "orphanable": identity in self.orphanable
                and owner_namespace == self.namespace,
                "path": identity,
            },
        )
        if owner_namespace == self.namespace and self.default_entry is not None:
            owner_id = _material_owner_entry(
                identity,
                self.default_entry.identity,
                self.material_owners,
                self.entry_roots,
            )
            owner_entry = self.entries.get(owner_id, self.default_entry)
            self.builder.add_edge(
                EdgeKind.OWNED_BY,
                key,
                owner_entry,
                self.namespace,
                origin,
            )
            self.builder.add_edge(
                EdgeKind.BELONGS_TO_LOG,
                key,
                self.log,
                self.namespace,
                origin,
            )
        self.material_keys[identity] = key
        return key


@dataclass
class _InvocationFacts:
    scripts: Dict[NodeKey, Set[NodeKey]] = field(default_factory=dict)
    paths: Dict[NodeKey, Set[NodeKey]] = field(default_factory=dict)
    inputs: Dict[NodeKey, Set[NodeKey]] = field(default_factory=dict)
    connected: Dict[NodeKey, Set[NodeKey]] = field(default_factory=dict)

    def add(self, invocation: NodeKey) -> None:
        """Initialize all relationship sets for one invocation."""

        self.scripts[invocation] = set()
        self.paths[invocation] = set()
        self.inputs[invocation] = set()
        self.connected[invocation] = set()


def _new_graph_build_state(scan: Mapping[str, Any]) -> _GraphBuildState:
    namespace = _log_namespace(scan["summary"])
    builder = GraphBuilder(scan["validation_rules_version"])
    summary_origin = _origin(
        scan,
        "log-discovery",
        [
            (
                scan["summary"],
                scan.get("files", {}).get(scan["summary"], scan["summary"]),
            )
        ],
    )
    log = NodeKey(namespace, NodeKind.LOG, namespace)
    builder.add_node(log, summary_origin)
    entries = _entry_nodes(scan, builder, namespace, log)
    material_owners, entry_roots = _material_ownership_index(scan)
    return _GraphBuildState(
        scan=scan,
        namespace=namespace,
        builder=builder,
        log=log,
        entries=entries,
        default_entry=entries.get("Log level") or next(iter(entries.values()), None),
        lookup=_identity_lookup(scan),
        invocation_lookup=producer_binding_invocation_cache(scan),
        local_identities=_local_identities(scan),
        orphanable=_orphanable_inventory(scan),
        material_owners=material_owners,
        entry_roots=entry_roots,
    )


def _add_material_inventory(state: _GraphBuildState) -> None:
    identities = set(state.scan.get("resolved_paths", {}))
    identities.update(state.scan.get("script_inventory", []))
    for entry in state.scan.get("entries", []):
        identities.update(
            candidate["identity"]
            for candidate in (
                *entry.get("candidate_targets", []),
                *entry.get("orphan_inventory", []),
            )
            if isinstance(candidate.get("identity"), str)
            and not candidate["identity"].startswith("<")
        )
    for identity in sorted(identities):
        state.ensure_material(identity)


def _add_collection_memberships(state: _GraphBuildState) -> None:
    collections = [
        key for key in state.material_keys.values() if key.kind is NodeKind.COLLECTION
    ]
    for child in state.material_keys.values():
        if child.kind not in {NodeKind.ARTIFACT, NodeKind.MEMBER}:
            continue
        for collection in collections:
            prefix = collection.identity.rstrip("/") + "/"
            if child.namespace != collection.namespace or not child.identity.startswith(
                prefix
            ):
                continue
            origin = _origin(
                state.scan,
                "collection-membership",
                [
                    (
                        collection.identity,
                        state.scan.get("directory_memberships", {}).get(
                            collection.identity, {"member": child.identity}
                        ),
                    )
                ],
            )
            state.builder.add_edge(
                EdgeKind.MEMBER_OF,
                child,
                collection,
                state.namespace,
                origin,
                {"selected": False},
            )


def _add_command_scripts(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    invocation: NodeKey,
    command: Mapping[str, Any],
    origin: FactOrigin,
) -> None:
    for raw_script in [command.get("script"), *command.get("matlab_scripts", [])]:
        if not raw_script:
            continue
        identity = _path_identity(state.scan, state.lookup, str(raw_script))
        script = state.ensure_material(identity)
        if script.kind is NodeKind.SCRIPT:
            state.builder.add_edge(
                EdgeKind.INVOKES,
                invocation,
                script,
                state.namespace,
                origin,
            )
            facts.scripts[invocation].add(script)


def _indexed_input(
    state: _GraphBuildState,
    entry: Mapping[str, Any],
    entry_id: str,
    name: str,
    token: Mapping[str, Any],
) -> NodeKey:
    data_index_path = entry.get("data_index", {}).get("path")
    owner_id = entry_id
    if isinstance(data_index_path, str):
        shared_owner = next(
            (
                candidate["id"]
                for candidate in state.scan.get("entries", [])
                if candidate.get("scope_kind") == "entry-global"
                and candidate.get("data_index", {}).get("path") == data_index_path
            ),
            None,
        )
        if shared_owner is not None:
            owner_id = shared_owner
    current = state.token_keys.get((owner_id, name))
    if current is not None:
        state.token_keys[(entry_id, name)] = current
        return current
    current = NodeKey(
        state.namespace,
        NodeKind.INDEXED_INPUT,
        f"{owner_id}:<{name}>",
    )
    origin = _origin(
        state.scan,
        "data-index-resolution",
        [(entry.get("data_index", {}).get("path") or entry_id, token)],
    )
    state.builder.add_node(
        current,
        origin,
        {
            "orphanable": f"<{name}>" in state.orphanable,
            "display_identity": f"<{name}>",
            "entry": owner_id,
        },
    )
    owner_entry = state.entries[owner_id]
    state.builder.add_edge(
        EdgeKind.OWNED_BY,
        current,
        owner_entry,
        state.namespace,
        origin,
    )
    state.builder.add_edge(
        EdgeKind.BELONGS_TO_LOG,
        current,
        state.log,
        state.namespace,
        origin,
    )
    state.token_keys[(owner_id, name)] = current
    state.token_keys[(entry_id, name)] = current
    return current


def _add_indexed_inputs(state: _GraphBuildState) -> None:
    """Represent every data.csv resource, including resources unused by commands."""

    for entry in state.scan.get("entries", []):
        entry_id = entry["id"]
        for row in entry.get("data_index", {}).get("rows", []):
            name = row.get("name")
            if not isinstance(name, str) or not name:
                continue
            _indexed_input(state, entry, entry_id, name, row)


def _add_command_tokens(
    state: _GraphBuildState,
    entry: Mapping[str, Any],
    invocation: NodeKey,
    command: Mapping[str, Any],
    origin: FactOrigin,
) -> None:
    entry_id = entry["id"]
    for token in command.get("data_tokens", []):
        name = token.get("name")
        if not isinstance(name, str) or name in {"log", "project"}:
            continue
        token_key = _indexed_input(state, entry, entry_id, name, token)
        state.builder.add_edge(
            EdgeKind.CONSUMES,
            invocation,
            token_key,
            state.namespace,
            origin,
        )
        if token.get("status") == "resolved" and token.get("path"):
            identity = _path_identity(state.scan, state.lookup, str(token["path"]))
            material = state.ensure_material(identity)
            state.builder.add_edge(
                EdgeKind.RESOLVES_TO,
                token_key,
                material,
                state.namespace,
                origin,
            )


def _add_command_paths(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    invocation: NodeKey,
    command: Mapping[str, Any],
    origin: FactOrigin,
) -> None:
    command_text = command.get("command", "")
    for argument in command.get("path_arguments", []):
        role = argument.get("role_hint")
        if role in {"workspace", "dependency-container"}:
            continue
        identity = _path_identity(state.scan, state.lookup, str(argument["path"]))
        target = state.ensure_material(identity)
        facts.paths[invocation].add(target)
        if role == "input":
            state.builder.add_edge(
                EdgeKind.CONSUMES,
                invocation,
                target,
                state.namespace,
                origin,
            )
            facts.inputs[invocation].add(target)
            facts.connected[invocation].add(target)
        elif role == "output":
            kind = (
                EdgeKind.CAPTURES
                if re.search(r"(?:^|\s)(?:tee|>|>>)\s", command_text)
                and argument.get("option") is None
                else EdgeKind.PRODUCES
            )
            state.builder.add_edge(
                kind,
                invocation,
                target,
                state.namespace,
                origin,
            )
            facts.connected[invocation].add(target)


def _add_command_facts(state: _GraphBuildState) -> _InvocationFacts:
    facts = _InvocationFacts()
    for entry in state.scan.get("entries", []):
        entry_id = entry["id"]
        entry_key = state.entries[entry_id]
        commands = entry.get("commands", [])
        identities = invocation_identities(entry_id, commands)
        for identity, command in zip(identities, commands):
            invocation = NodeKey(state.namespace, NodeKind.INVOCATION, identity)
            origin = _origin(
                state.scan,
                "command-discovery",
                [(entry.get("path", entry_id), command)],
            )
            state.builder.add_node(
                invocation,
                origin,
                {
                    "entry": entry_id,
                    "line": command.get("line"),
                    "command": command.get("command", ""),
                },
            )
            state.builder.add_edge(
                EdgeKind.OWNED_BY,
                invocation,
                entry_key,
                state.namespace,
                origin,
            )
            state.builder.add_edge(
                EdgeKind.BELONGS_TO_LOG,
                invocation,
                state.log,
                state.namespace,
                origin,
            )
            state.builder.add_root(invocation, RootPolicy.RECORDED_COMMAND, origin)
            facts.add(invocation)
            _add_command_scripts(state, facts, invocation, command, origin)
            _add_command_tokens(state, entry, invocation, command, origin)
            _add_command_paths(state, facts, invocation, command, origin)
    return facts


def _add_code_dependencies(state: _GraphBuildState) -> None:
    for source_identity, dependencies in state.scan.get(
        "script_dependency_graph", {}
    ).items():
        source = state.ensure_material(source_identity)
        if source.kind is not NodeKind.SCRIPT:
            continue
        origin = _origin(
            state.scan,
            "code-dependency-resolution",
            [
                (
                    source_identity,
                    state.scan.get("files", {}).get(source_identity, dependencies),
                )
            ],
        )
        for dependency_identity in dependencies:
            dependency = state.ensure_material(dependency_identity)
            if dependency.kind is NodeKind.SCRIPT:
                state.builder.add_edge(
                    EdgeKind.DEPENDS_ON_CODE,
                    source,
                    dependency,
                    state.namespace,
                    origin,
                )


def _add_evidence_presentations(
    state: _GraphBuildState,
    entry: Mapping[str, Any],
    entry_id: str,
    entry_path: str,
) -> None:
    for row_index, row in enumerate(
        entry.get("evidence_record", {}).get("rows", []), 1
    ):
        selector = (
            f"{entry_id}:{row.get('section')}:{row.get('kind')}:"
            f"{row.get('evidence')}:{row_index}"
        )
        presented = NodeKey(state.namespace, NodeKind.PRESENTED, selector)
        origin = _origin(state.scan, "evidence-association", [(entry_path, row)])
        state.builder.add_node(
            presented,
            origin,
            {"entry": entry_id, "section": row.get("section")},
        )
        state.builder.add_root(presented, RootPolicy.PRESENTED, origin)
        state.builder.add_edge(
            EdgeKind.BELONGS_TO_LOG,
            presented,
            state.log,
            state.namespace,
            origin,
        )
        for source in row.get("resolved_sources", []):
            identity = source.get("identity")
            if isinstance(identity, str):
                material = state.ensure_material(identity)
                state.builder.add_edge(
                    EdgeKind.SUPPORTS,
                    material,
                    presented,
                    state.namespace,
                    origin,
                )


def _add_artifact_presentations(
    state: _GraphBuildState,
    entry: Mapping[str, Any],
    entry_id: str,
    entry_path: str,
) -> None:
    for target_index, candidate in enumerate(entry.get("candidate_targets", []), 1):
        identity = candidate.get("identity")
        if not candidate.get("presented") or not isinstance(identity, str):
            continue
        presented = NodeKey(
            state.namespace,
            NodeKind.PRESENTED,
            f"{entry_id}:artifact:{identity}:{target_index}",
        )
        origin = _origin(state.scan, "artifact-presentation", [(entry_path, candidate)])
        state.builder.add_node(
            presented,
            origin,
            {"entry": entry_id, "sections": candidate.get("sections", [])},
        )
        state.builder.add_root(presented, RootPolicy.PRESENTED, origin)
        state.builder.add_edge(
            EdgeKind.BELONGS_TO_LOG,
            presented,
            state.log,
            state.namespace,
            origin,
        )
        material = state.ensure_material(identity)
        state.builder.add_edge(
            EdgeKind.SUPPORTS,
            material,
            presented,
            state.namespace,
            origin,
        )


def _add_entry_presentations(state: _GraphBuildState) -> None:
    for entry in state.scan.get("entries", []):
        entry_id = entry["id"]
        entry_path = entry.get("path", entry_id)
        _add_evidence_presentations(state, entry, entry_id, entry_path)
        _add_artifact_presentations(state, entry, entry_id, entry_path)


def _add_summary_presentations(state: _GraphBuildState) -> None:
    summary_rows = {
        row.get("statistic"): row
        for row in state.scan.get("evidence_records", {})
        .get("summary", {})
        .get("rows", [])
    }
    for index, item in enumerate(state.scan.get("summary_items", []), 1):
        selector = item.get("selector", f"summary-{index}")
        presented = NodeKey(
            state.namespace,
            NodeKind.PRESENTED,
            f"Summary:{selector}:{index}",
        )
        row = summary_rows.get(selector)
        origin = _origin(
            state.scan,
            "summary-association",
            [(str(state.scan["summary"]), {"item": item, "association": row})],
        )
        state.builder.add_node(presented, origin, {"scope": "Summary"})
        state.builder.add_root(presented, RootPolicy.PRESENTED, origin)
        state.builder.add_edge(
            EdgeKind.BELONGS_TO_LOG,
            presented,
            state.log,
            state.namespace,
            origin,
        )
        if row and row.get("entry") in state.entries:
            state.builder.add_edge(
                EdgeKind.SUPPORTS,
                state.entries[row["entry"]],
                presented,
                state.namespace,
                origin,
            )


def _add_semantic_facts(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    adjudication: Optional[Mapping[str, Any]],
) -> None:
    if adjudication is not None:
        _add_reviewed_graph_facts(state, adjudication, facts)
        _add_reviewed_retention_roots(
            state,
            adjudication,
        )
    elif state.scan.get("incremental"):
        invocations, accepted_nodes = _add_cached_semantic_facts(state, facts)
        _connect_cached_used_candidates(state, facts, invocations)
        _add_cached_acceptance_roots(
            state.scan,
            state.builder,
            state.namespace,
            accepted_nodes,
        )


def build_dependency_graph(
    scan: Mapping[str, Any], adjudication: Optional[Mapping[str, Any]] = None
) -> DependencyGraph:
    """Build the canonical graph from mechanical and reviewed scan facts."""

    state = _new_graph_build_state(scan)
    _add_material_inventory(state)
    _add_collection_memberships(state)
    _add_indexed_inputs(state)
    invocation_facts = _add_command_facts(state)
    _add_code_dependencies(state)
    _add_entry_presentations(state)
    _add_summary_presentations(state)
    _add_semantic_facts(state, invocation_facts, adjudication)
    return state.builder.build()


@dataclass(frozen=True)
class _ReviewedRow:
    target: NodeKey
    dependencies: Sequence[Mapping[str, Any]]
    origin: FactOrigin
    invocation: Optional[NodeKey]


def _add_reviewed_graph_facts(
    state: _GraphBuildState,
    adjudication: Mapping[str, Any],
    facts: _InvocationFacts,
    require_selected_producers: bool = True,
) -> Set[NodeKey]:
    """Add only uniquely resolvable semantic producer and collection facts."""

    selected_invocations: Set[NodeKey] = set()
    for entry in adjudication.get("entries", []):
        entry_id = entry["id"]
        for row in entry.get("targets", []):
            reviewed = _reviewed_row(
                state, facts, entry_id, row, require_selected_producers
            )
            if reviewed is None:
                continue
            if reviewed.invocation is not None:
                selected_invocations.add(reviewed.invocation)
                _add_reviewed_invocation_facts(state, facts, reviewed)
            _add_reviewed_collection_facts(state, reviewed)
            _add_reviewed_producer_bindings(
                state,
                facts,
                row,
                reviewed.origin,
                required=require_selected_producers,
            )
    return selected_invocations


def _add_reviewed_producer_bindings(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    row: Mapping[str, Any],
    origin: FactOrigin,
    *,
    required: bool,
) -> None:
    """Add exact reviewed upstream producer choices for generated inputs."""

    for raw_binding in row.get("producer_bindings", []):
        try:
            binding = verify_producer_binding(
                cast(ScanRecord, state.scan),
                raw_binding["material"],
                raw_binding["invocation"],
                row.get("dependencies", []),
                ProducerBindingOptions(
                    "upstream-reviewed", state.lookup, state.invocation_lookup
                ),
            )
        except ValidationToolError as exc:
            if not required:
                continue
            raise GraphContractError(str(exc)) from exc
        material = state.ensure_material(raw_binding["material"])
        invocation = NodeKey(
            state.namespace, NodeKind.INVOCATION, binding["invocation_identity"]
        )
        if invocation not in facts.paths:
            if not required:
                continue
            raise GraphContractError(
                "reviewed upstream producer is not a recorded invocation: "
                f"{binding['invocation_identity']}"
            )
        state.builder.add_edge(
            EdgeKind.PRODUCES,
            invocation,
            material,
            invocation.namespace,
            origin,
        )
        state.builder.add_edge(
            EdgeKind.SELECTED_PRODUCER,
            material,
            invocation,
            invocation.namespace,
            origin,
        )


def _reviewed_row(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    entry_id: str,
    row: Mapping[str, Any],
    required: bool,
) -> Optional[_ReviewedRow]:
    target_identity = row.get("target")
    if row.get("provenance") in {"FAIL", "-", "N/A", None} or not isinstance(
        target_identity, str
    ):
        return None
    target = state.ensure_material(target_identity)
    dependencies = row.get("dependencies", [])
    producer_scripts = {
        state.ensure_material(item["path"])
        for item in dependencies
        if item.get("role") == "producer" and isinstance(item.get("path"), str)
    }
    producer_identity = row.get("producer_invocation")
    invocation = None
    if isinstance(producer_identity, str):
        try:
            binding = verify_producer_binding(
                cast(ScanRecord, state.scan),
                target_identity,
                producer_identity,
                dependencies,
                ProducerBindingOptions(
                    identity_cache=state.lookup,
                    invocation_cache=state.invocation_lookup,
                ),
            )
        except ValidationToolError as exc:
            if not required:
                return None
            raise GraphContractError(str(exc)) from exc
        invocation = NodeKey(
            state.namespace,
            NodeKind.INVOCATION,
            binding["invocation_identity"],
        )
        if invocation not in facts.paths:
            if not required:
                return None
            raise GraphContractError(
                "validated producer is absent from command graph: "
                f"{binding['invocation_identity']}"
            )
    elif producer_scripts and required:
        raise GraphContractError(
            "successful provenance lacks a concrete recorded producer: "
            f"{entry_id}: {target_identity}"
        )
    origin = _origin(
        state.scan,
        "reviewed-producer",
        [
            (
                f"{entry_id}:{target_identity}",
                {
                    "target": target_identity,
                    "provenance": row.get("provenance"),
                    "dependencies": dependencies,
                },
            )
        ],
        semantic_scope=f"{entry_id}:{target_identity}",
    )
    return _ReviewedRow(target, dependencies, origin, invocation)


def _add_reviewed_invocation_facts(
    state: _GraphBuildState, facts: _InvocationFacts, reviewed: _ReviewedRow
) -> None:
    invocation = reviewed.invocation
    if invocation is None:
        return
    state.builder.add_edge(
        EdgeKind.PRODUCES,
        invocation,
        reviewed.target,
        invocation.namespace,
        reviewed.origin,
    )
    state.builder.add_edge(
        EdgeKind.SELECTED_PRODUCER,
        reviewed.target,
        invocation,
        invocation.namespace,
        reviewed.origin,
    )
    for dependency in sorted(
        facts.paths.get(invocation, set()) - facts.connected.get(invocation, set())
    ):
        kind = (
            EdgeKind.INVOKES
            if dependency.kind is NodeKind.SCRIPT
            else EdgeKind.CONSUMES
        )
        direction = (
            "command-path-code"
            if dependency.kind is NodeKind.SCRIPT
            else "unresolved-command-path"
        )
        state.builder.add_edge(
            kind,
            invocation,
            dependency,
            invocation.namespace,
            reviewed.origin,
            {"semantic_direction": direction},
        )
    for item in reviewed.dependencies:
        _add_reviewed_dependency(state, invocation, item, reviewed.origin)


def _add_reviewed_dependency(
    state: _GraphBuildState,
    invocation: NodeKey,
    item: Mapping[str, Any],
    origin: FactOrigin,
) -> None:
    path = item.get("path")
    if not isinstance(path, str):
        return
    dependency = state.ensure_material(path)
    role = item.get("role")
    if role == "producer" and dependency.kind is NodeKind.SCRIPT:
        kind = EdgeKind.INVOKES
    elif role == "producer" or (
        role == "input"
        and dependency.kind
        in {
            NodeKind.ARTIFACT,
            NodeKind.COLLECTION,
            NodeKind.MEMBER,
            NodeKind.INDEXED_INPUT,
            NodeKind.EXTERNAL_SOURCE,
        }
    ):
        kind = EdgeKind.CONSUMES
    else:
        return
    state.builder.add_edge(kind, invocation, dependency, invocation.namespace, origin)


def _add_reviewed_collection_facts(
    state: _GraphBuildState, reviewed: _ReviewedRow
) -> None:
    for item in reviewed.dependencies:
        members = item.get("members")
        path = item.get("path")
        if not isinstance(members, list) or not isinstance(path, str):
            continue
        collection = state.ensure_material(path)
        if collection.kind is not NodeKind.COLLECTION:
            continue
        for member_path in members:
            _add_reviewed_collection_member(state, reviewed, collection, member_path)


def _add_reviewed_collection_member(
    state: _GraphBuildState,
    reviewed: _ReviewedRow,
    collection: NodeKey,
    member_path: str,
) -> None:
    material_identity = f"{collection.identity.rstrip('/')}/{member_path}"
    member = state.ensure_material(material_identity)
    if member.kind not in {NodeKind.ARTIFACT, NodeKind.MEMBER}:
        member = NodeKey(
            collection.namespace,
            NodeKind.MEMBER,
            f"{collection.identity}::{member_path}",
        )
        state.builder.add_node(
            member,
            reviewed.origin,
            {
                "orphanable": False,
                "collection": collection.identity,
                "relative_path": member_path,
                "material_path": material_identity,
            },
        )
    namespace = (
        reviewed.invocation.namespace
        if reviewed.invocation is not None
        else collection.namespace
    )
    state.builder.add_edge(
        EdgeKind.MEMBER_OF,
        member,
        collection,
        namespace,
        reviewed.origin,
        {"selected": True},
    )


def _retention_note(
    entry_id: str,
    identity: str,
    basis: str,
    notes: Sequence[Mapping[str, Any]],
) -> tuple[bool, Optional[Dict[str, Any]]]:
    basis = effective_basis(basis)
    if basis == "semantic-connection":
        return True, None
    if not basis.startswith("validation-note:"):
        return False, None
    note_sha = basis.removeprefix("validation-note:")
    matching_notes = [
        dict(candidate) for candidate in notes if candidate.get("sha256") == note_sha
    ]
    if len(matching_notes) != 1:
        raise GraphContractError(
            "orphan retention basis does not match one Validation note: "
            f"{entry_id}: {identity}"
        )
    return True, matching_notes[0]


def _reviewed_retention_candidates(
    state: _GraphBuildState,
    adjudication: Mapping[str, Any],
) -> List[Tuple[str, str, NodeKey, Optional[Dict[str, Any]]]]:
    notes_by_entry = {
        entry["id"]: entry.get("validation_notes", [])
        for entry in state.scan.get("entries", [])
    }
    pending: List[Tuple[str, str, NodeKey, Optional[Dict[str, Any]]]] = []
    for entry in adjudication.get("entries", []):
        entry_id = entry["id"]
        notes = notes_by_entry.get(entry_id, [])
        for item in entry.get("orphan_items", []):
            identity = item.get("identity")
            basis = effective_basis(item.get("basis"))
            if item.get("decision") != "accepted" or not isinstance(identity, str):
                continue
            retained, note = _retention_note(entry_id, identity, basis, notes)
            if not retained:
                continue
            token_match = re.fullmatch(r"<([^>]+)>", identity)
            node = (
                state.token_keys.get((entry_id, token_match.group(1)))
                if token_match
                else state.ensure_material(identity)
            )
            if node is not None:
                pending.append((entry_id, identity, node, note))
    return pending


def _add_reviewed_retention_roots(
    state: _GraphBuildState,
    adjudication: Mapping[str, Any],
) -> None:
    """Persist bounded semantic acceptance for residual reviewed candidates."""

    remaining = orphan_nodes(state.builder.build(), state.namespace)
    for entry_id, identity, node, note in _reviewed_retention_candidates(
        state, adjudication
    ):
        if node not in remaining:
            continue
        inputs = [(identity, item_identity(state.scan, identity))]
        if note is not None:
            inputs.append((f"{entry_id}:Validation", note))
        origin = _origin(
            state.scan,
            (
                "reviewed-retention-note"
                if note is not None
                else "reviewed-semantic-connection"
            ),
            inputs,
            semantic_scope=f"{entry_id}:retention:{identity}",
        )
        state.builder.add_root(node, RootPolicy.RETENTION, origin)


def item_identity(scan: Mapping[str, Any], identity: str) -> Any:
    """Return the bounded discovery identity for one retained candidate."""

    for entry in scan.get("entries", []):
        for candidate in entry.get("orphan_inventory", []):
            if candidate.get("identity") == identity:
                return candidate
    return {"identity": identity, "input_fingerprint": scan.get("input_fingerprint")}


def _cached_orphan_acceptances(
    scan: Mapping[str, Any],
    ensure_material: Any,
    token_keys: Mapping[Tuple[str, str], NodeKey],
) -> List[Tuple[str, str, NodeKey, Optional[Dict[str, Any]]]]:
    """Resolve reusable semantic orphan acceptances to graph nodes."""

    notes_by_entry = {
        entry["id"]: entry.get("validation_notes", [])
        for entry in scan.get("entries", [])
    }
    accepted = []
    for disposition in scan.get("incremental", {}).get("orphan_dispositions", []):
        entry_id = disposition["entry"]
        for item in disposition.get("items", []):
            identity = item.get("identity")
            basis = item.get("basis", "")
            if item.get("decision") != "accepted" or not isinstance(identity, str):
                continue
            note: Optional[Dict[str, Any]] = None
            if basis.startswith("validation-note:"):
                note_sha = basis.removeprefix("validation-note:")
                notes = [
                    candidate
                    for candidate in notes_by_entry.get(entry_id, [])
                    if candidate.get("sha256") == note_sha
                ]
                if len(notes) != 1:
                    continue
                note = notes[0]
            elif basis != "semantic-connection":
                continue
            token_match = re.fullmatch(r"<([^>]+)>", identity)
            node = (
                token_keys.get((entry_id, token_match.group(1)))
                if token_match
                else ensure_material(identity)
            )
            if node is not None:
                accepted.append((entry_id, identity, node, note))
    return accepted


def _add_cached_semantic_facts(
    state: _GraphBuildState,
    facts: _InvocationFacts,
) -> Tuple[Set[NodeKey], List[Tuple[str, str, NodeKey, Any]]]:
    """Overlay reusable outcomes and orphan decisions for incremental queries."""

    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for check in state.scan.get("incremental", {}).get("checks", []):
        if check.get("check") != "Provenance" or check.get("status") != "reusable":
            continue
        if check.get("entry") in {None, "Summary"}:
            continue
        target = check.get("target")
        if not isinstance(target, str) or target.startswith("Orphaned "):
            continue
        dependencies = []
        for dependency in check.get("dependencies", []):
            if not isinstance(dependency, dict):
                continue
            item = {
                "path": dependency.get("path"),
                "role": dependency.get("role"),
            }
            if isinstance(dependency.get("members"), list):
                item["members"] = dependency["members"]
            if isinstance(item["path"], str) and isinstance(item["role"], str):
                dependencies.append(item)
        grouped.setdefault(str(check["entry"]), []).append(
            {
                "target": target,
                "provenance": check.get("result"),
                "dependencies": dependencies,
                "producer_invocation": (
                    check.get("resolution", {}).get("producer_invocation")
                    if isinstance(check.get("resolution"), dict)
                    else None
                ),
                "producer_bindings": (
                    check.get("resolution", {}).get("producer_bindings", [])
                    if isinstance(check.get("resolution"), dict)
                    else []
                ),
                "cached_resolution": check.get("resolution"),
                "cached_outcome": True,
            }
        )
    selected_invocations = _add_reviewed_graph_facts(
        state,
        {"entries": [{"id": key, "targets": value} for key, value in grouped.items()]},
        facts,
        require_selected_producers=False,
    )

    accepted_nodes = _cached_orphan_acceptances(
        state.scan, state.ensure_material, state.token_keys
    )
    return selected_invocations, accepted_nodes


def _add_cached_acceptance_roots(
    scan: Mapping[str, Any],
    builder: GraphBuilder,
    namespace: str,
    accepted_nodes: Sequence[Tuple[str, str, NodeKey, Any]],
) -> None:
    """Retain accepted cached items still unconnected after reconstruction."""

    remaining = orphan_nodes(builder.build(), namespace)
    for entry_id, identity, node, note in accepted_nodes:
        if node not in remaining:
            continue
        inputs = [(identity, item_identity(scan, identity))]
        if note is not None:
            inputs.append(("Validation", note))
        origin = _origin(
            scan,
            (
                "cached-retention-note"
                if note is not None
                else "cached-semantic-connection"
            ),
            inputs,
            semantic_scope=f"{entry_id}:orphan:{identity}",
        )
        builder.add_root(node, RootPolicy.RETENTION, origin)


def _connect_cached_used_candidates(
    state: _GraphBuildState,
    facts: _InvocationFacts,
    reviewed_invocations: Set[NodeKey],
) -> None:
    """Recover uniquely attributable sibling-use facts from reusable outcomes."""

    raw_orphans = {
        candidate["identity"]
        for entry in state.scan.get("entries", [])
        for candidate in entry.get("orphan_candidates", [])
        if isinstance(candidate.get("identity"), str)
    }
    input_identities = {
        dependency["path"]
        for check in state.scan.get("incremental", {}).get("checks", [])
        for dependency in check.get("dependencies", [])
        if dependency.get("role") == "input" and isinstance(dependency.get("path"), str)
    }
    for entry in state.scan.get("entries", []):
        entry_id = entry["id"]
        for candidate in entry.get("candidate_targets", []):
            resolved = _cached_candidate_material(state, candidate, raw_orphans)
            if resolved is None:
                continue
            identity, material = resolved
            matches = _candidate_invocations(
                entry_id, candidate, material, reviewed_invocations, facts.paths
            )
            if len(matches) != 1:
                continue
            invocation = matches[0]
            origin = _origin(
                state.scan,
                "cached-used-classification",
                [
                    (
                        identity,
                        {
                            "candidate": candidate,
                            "input_fingerprint": state.scan.get("input_fingerprint"),
                        },
                    )
                ],
                semantic_scope=f"{entry_id}:cached-used:{identity}",
            )
            kind = (
                EdgeKind.CONSUMES if identity in input_identities else EdgeKind.PRODUCES
            )
            state.builder.add_edge(
                kind,
                invocation,
                material,
                invocation.namespace,
                origin,
            )


def _cached_candidate_material(
    state: _GraphBuildState,
    candidate: Mapping[str, Any],
    raw_orphans: Set[str],
) -> Optional[Tuple[str, NodeKey]]:
    identity = candidate.get("identity")
    if (
        not isinstance(identity, str)
        or identity in raw_orphans
        or candidate.get("mechanical", {}).get("status") == "missing"
    ):
        return None
    material = state.ensure_material(identity)
    if material.kind not in {
        NodeKind.ARTIFACT,
        NodeKind.COLLECTION,
        NodeKind.EXTERNAL_SOURCE,
    }:
        return None
    return identity, material


def _candidate_invocations(
    entry_id: str,
    candidate: Mapping[str, Any],
    material: NodeKey,
    reviewed_invocations: Set[NodeKey],
    invocation_paths: Mapping[NodeKey, Set[NodeKey]],
) -> List[NodeKey]:
    lines = {
        occurrence.get("line")
        for occurrence in candidate.get("occurrences", [])
        if isinstance(occurrence.get("line"), int)
    }
    return [
        invocation
        for invocation in reviewed_invocations
        if _candidate_matches_invocation(
            entry_id, lines, material, invocation, invocation_paths
        )
    ]


def _candidate_matches_invocation(
    entry_id: str,
    lines: Set[int],
    material: NodeKey,
    invocation: NodeKey,
    invocation_paths: Mapping[NodeKey, Set[NodeKey]],
) -> bool:
    if not invocation.identity.startswith(entry_id + ":L"):
        return False
    try:
        line = int(invocation.identity.split(":L", 1)[1].split(":", 1)[0])
    except (IndexError, ValueError):
        return False
    if lines and line not in lines:
        return False
    return any(
        material == path
        or (
            path.kind is NodeKind.COLLECTION
            and material.identity.startswith(path.identity.rstrip("/") + "/")
        )
        for path in invocation_paths.get(invocation, set())
    )
