"""Versioned command-chain projection for finding intake and reproduction admission."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Mapping, Sequence

from research_log_data import DataFile, InputResource

from .commands import Invocation, MaterialRelationship
from .json_codec import canonical_json
from .mechanical_results import CheckStatus, MechanicalCheck, MechanicalGeneratedRecord
from .repair_batches import build_repair_batches

PROJECTION_SCHEMA = "research-log-published-validation/2"
MAX_PROJECTED_CHAINS = 10_000
MAX_PROJECTED_COMMANDS = 10_000
_ENTRY_RE = re.compile(r"(?:^|:)(e[0-9]+[a-z]?)(?::|$)", re.IGNORECASE)
_COMMAND_CHECK_RE = re.compile(
    r"^entry:(?P<entry>e[0-9]+[a-z]?):command:(?P<fence>[0-9]+):(?P<ordinal>[0-9]+)$",
    re.IGNORECASE,
)


class BatchProjectionError(ValueError):
    """Raised when a projected batch contract cannot be constructed."""


def build_batch_projection(
    record: MechanicalGeneratedRecord,
    *,
    invocations: Sequence[Invocation],
    registries: Sequence[tuple[str, DataFile]],
    source_identity: str,
) -> dict[str, object]:
    """Build the complete deterministic chain and finding projection once."""

    if len(invocations) > MAX_PROJECTED_COMMANDS:
        raise BatchProjectionError("published validation crossed its command bound")
    commands = {_command_key(value): _command(value) for value in invocations}
    edges = _command_edges(invocations)
    components = _components(invocations, edges)
    if len(components) > MAX_PROJECTED_CHAINS:
        raise BatchProjectionError("published validation crossed its chain bound")
    registry_index = _registry_index(registries)
    chains = [
        _chain(component, commands, edges, registry_index) for component in components
    ]
    _attach_findings(record.checks, chains)
    unresolved = _unresolved_groups(record.checks, chains)
    body: dict[str, object] = {
        "chains": sorted(chains, key=_chain_sort_key),
        "record_identity": hashlib.sha256(
            record.canonical_json().encode("utf-8")
        ).hexdigest(),
        "result_date": record.result_date,
        "rules_version": record.rules_version,
        "schema": PROJECTION_SCHEMA,
        "source_identity": source_identity,
        "summary": record.summary,
        "unresolved": unresolved,
        "repair_batches": build_repair_batches(record, chains),
    }
    body["validation_id"] = hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()
    return body


def _command_key(invocation: Invocation) -> tuple[str, str]:
    return invocation.entry, invocation.identity


def _relationship(value: MaterialRelationship) -> dict[str, object]:
    result: dict[str, object] = {
        "direction": value.direction,
        "path": value.path,
        "proof": value.proof,
    }
    if value.target is not None:
        result["target"] = value.target
    if value.named_input is not None:
        result["artifact"] = value.named_input
    if value.origin:
        result["origin"] = True
    return result


def _collection(value: object) -> dict[str, object]:
    return {
        "direction": str(getattr(value, "direction")),
        "mechanism": str(getattr(value, "mechanism")),
        "members": list(getattr(value, "members")),
        "root": getattr(value, "root"),
        "target": str(getattr(value, "target")),
    }


def _command(invocation: Invocation) -> dict[str, object]:
    return {
        "collections": [_collection(value) for value in invocation.collections],
        "document": invocation.document,
        "entry": invocation.entry,
        "fence": invocation.fence,
        "identity": invocation.identity,
        "inputs": [_relationship(value) for value in invocation.inputs],
        "ordinal": invocation.ordinal,
        "outputs": [_relationship(value) for value in invocation.outputs],
        "script": invocation.script,
        "tokens": list(invocation.tokens),
    }


def _command_edges(
    invocations: Sequence[Invocation],
) -> tuple[tuple[tuple[str, str], tuple[str, str], str], ...]:
    producers: dict[str, list[Invocation]] = defaultdict(list)
    for invocation in invocations:
        for path in _invocation_materials(invocation, "output"):
            producers[path].append(invocation)
    edges: set[tuple[tuple[str, str], tuple[str, str], str]] = set()
    for consumer in invocations:
        for path in _invocation_materials(consumer, "input"):
            candidates = producers.get(path, ())
            if len(candidates) != 1:
                continue
            producer = candidates[0]
            if (
                producer.entry != consumer.entry
                or producer.identity == consumer.identity
            ):
                continue
            edges.add((_command_key(producer), _command_key(consumer), path))
    return tuple(sorted(edges))


def _invocation_materials(invocation: Invocation, direction: str) -> set[str]:
    result = {
        value.path
        for value in (invocation.inputs if direction == "input" else invocation.outputs)
    }
    for collection in invocation.collections:
        if collection.direction != direction:
            continue
        result.update(collection.members)
        if collection.root is not None:
            result.add(collection.root)
    return result


def _components(
    invocations: Sequence[Invocation],
    edges: Sequence[tuple[tuple[str, str], tuple[str, str], str]],
) -> tuple[tuple[tuple[str, str], ...], ...]:
    neighbors: dict[tuple[str, str], set[tuple[str, str]]] = {
        _command_key(value): set() for value in invocations
    }
    for source, target, _ in edges:
        neighbors[source].add(target)
        neighbors[target].add(source)
    pending = set(neighbors)
    result: list[tuple[tuple[str, str], ...]] = []
    while pending:
        seed = min(pending)
        stack = [seed]
        component: set[tuple[str, str]] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(neighbors[current] - component, reverse=True))
        pending.difference_update(component)
        result.append(tuple(sorted(component)))
    return tuple(sorted(result))


def _registry_index(
    registries: Sequence[tuple[str, DataFile]],
) -> tuple[
    dict[str, list[tuple[str, InputResource]]],
    dict[tuple[str, str], list[tuple[str, InputResource]]],
]:
    by_path: dict[str, list[tuple[str, InputResource]]] = defaultdict(list)
    by_name: dict[tuple[str, str], list[tuple[str, InputResource]]] = defaultdict(list)
    for entry, data_file in registries:
        for resource in data_file.inputs:
            by_path[resource.canonical_target].append((entry, resource))
            by_name[(entry, resource.name)].append((entry, resource))
    return by_path, by_name


def _chain(
    component: Sequence[tuple[str, str]],
    commands: Mapping[tuple[str, str], Mapping[str, object]],
    edges: Sequence[tuple[tuple[str, str], tuple[str, str], str]],
    registry_index: tuple[
        Mapping[str, Sequence[tuple[str, InputResource]]],
        Mapping[tuple[str, str], Sequence[tuple[str, InputResource]]],
    ],
) -> dict[str, object]:
    entry = component[0][0]
    members = [commands[key] for key in component]
    member_keys = set(component)
    selected_edges = [
        {"artifact": artifact, "source": source[1], "target": target[1]}
        for source, target, artifact in edges
        if source in member_keys and target in member_keys
    ]
    artifacts = sorted(_projected_artifacts(members))
    by_path, by_name = registry_index
    registry_resources: dict[tuple[str, str, str], tuple[str, InputResource]] = {}
    for artifact in artifacts:
        for owner, resource in by_path.get(artifact, ()):
            registry_resources[(owner, resource.name, resource.canonical_target)] = (
                owner,
                resource,
            )
    for command in members:
        for family in ("inputs", "outputs"):
            for relationship in _mapping_items(command.get(family)):
                name = relationship.get("artifact")
                if not isinstance(name, str):
                    continue
                for owner, resource in by_name.get((entry, name), ()):
                    registry_resources[
                        (owner, resource.name, resource.canonical_target)
                    ] = (owner, resource)
    registry = [_registry_record(*value) for value in registry_resources.values()]
    command_ids = sorted(key[1] for key in component)
    chain_id = hashlib.sha256(
        canonical_json({"commands": command_ids, "entry": entry}).encode("utf-8")
    ).hexdigest()
    signals = [
        signal
        for signal, present in (
            ("directory", any(_directory_collections(command) for command in members)),
            (
                "fan_in",
                any(
                    _logical_material_count(command, "input") > 1 for command in members
                ),
            ),
            (
                "fan_out",
                any(
                    _logical_material_count(command, "output") > 1
                    for command in members
                ),
            ),
            ("multi_command", len(component) > 1),
        )
        if present
    ]
    return {
        "artifacts": artifacts,
        "chain_id": chain_id,
        "commands": members,
        "edges": selected_edges,
        "entry": entry,
        "findings": [],
        "registry": sorted(
            registry,
            key=lambda value: (str(value["entry"]), str(value["name"])),
        ),
        "signals": signals,
    }


def _projected_artifacts(commands: Sequence[Mapping[str, object]]) -> set[str]:
    result = {
        str(relationship["path"])
        for command in commands
        for family in ("inputs", "outputs")
        for relationship in _mapping_items(command.get(family))
    }
    for command in commands:
        for collection in _mapping_items(command.get("collections")):
            root = collection.get("root")
            if isinstance(root, str):
                result.add(root)
            result.update(
                str(value) for value in _sequence_items(collection.get("members"))
            )
    return result


def _registry_record(owner: str, resource: InputResource) -> dict[str, object]:
    value: dict[str, object] = {
        "entry": owner,
        "kind": resource.kind,
        "location": resource.location,
        "name": resource.name,
        "origin": resource.origin,
        "path": resource.canonical_target,
    }
    if resource.fingerprint.digest is not None:
        value["fingerprint"] = resource.fingerprint.as_dict()
    if resource.reference_entry is not None:
        value["from_entry"] = resource.reference_entry
        value["read_only"] = True
    return value


def _directory_collections(command: Mapping[str, object]) -> bool:
    return any(
        value.get("mechanism") in {"directory", "identity-files", "identity-patterns"}
        for value in _mapping_items(command.get("collections"))
    )


def _logical_material_count(command: Mapping[str, object], direction: str) -> int:
    family = f"{direction}s"
    relationships = _mapping_items(command.get(family))
    collections = [
        value
        for value in _mapping_items(command.get("collections"))
        if value.get("direction") == direction
    ]
    collection_members = {
        str(member)
        for collection in collections
        for member in _sequence_items(collection.get("members"))
    }
    scalar_paths = {
        str(value["path"])
        for value in relationships
        if "path" in value and str(value["path"]) not in collection_members
    }
    return len(scalar_paths) + len(collections)


def _attach_findings(
    checks: Sequence[MechanicalCheck], chains: list[dict[str, object]]
) -> None:
    by_entry: dict[str, list[dict[str, object]]] = defaultdict(list)
    command_aliases: dict[str, dict[str, object]] = {}
    for chain in chains:
        by_entry[str(chain["entry"])].append(chain)
        for command in _mapping_items(chain.get("commands")):
            alias = (
                f"entry:{command['entry']}:command:"
                f"{command['fence']}:{command['ordinal']}"
            )
            command_aliases[alias] = chain
    for check in checks:
        if check.status not in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}:
            continue
        candidates: dict[str, dict[str, object]] = {}
        direct = command_aliases.get(check.identity)
        if direct is not None:
            candidates[str(direct["chain_id"])] = direct
        strings = _finding_strings(check)
        entry = _finding_entry(check)
        for chain in by_entry.get(entry or "", ()):
            chain_strings = _chain_strings(chain)
            if strings & chain_strings:
                candidates[str(chain["chain_id"])] = chain
        if len(candidates) == 1:
            selected = next(iter(candidates.values()))
            findings = selected["findings"]
            assert isinstance(findings, list)
            blocks = _blocks_reproduction(check)
            findings.append(
                _finding(
                    check,
                    effect="chain" if blocks else "none",
                    affected_chains=(str(selected["chain_id"]),) if blocks else (),
                    affected_entries=(
                        (_physical_entry(str(selected["entry"])),) if blocks else ()
                    ),
                )
            )
    for chain in chains:
        chain["findings"] = sorted(
            _mapping_items(chain.get("findings")),
            key=lambda value: str(value["identity"]),
        )


def _unresolved_groups(
    checks: Sequence[MechanicalCheck], chains: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    assigned = {
        str(finding["identity"])
        for chain in chains
        for finding in _mapping_items(chain.get("findings"))
    }
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for check in checks:
        if (
            check.status in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}
            and check.identity not in assigned
        ):
            entry = _finding_entry(check)
            effect = _unresolved_admission_effect(check, entry)
            physical_entry = _physical_entry(entry) if entry is not None else None
            grouped[(entry or "log", effect)].append(
                _finding(
                    check,
                    effect=effect,
                    affected_entries=(physical_entry,)
                    if effect == "entry" and physical_entry is not None
                    else (),
                )
            )
    result: list[dict[str, object]] = []
    for (entry, effect), findings in sorted(grouped.items()):
        findings.sort(key=lambda value: str(value["identity"]))
        identity = hashlib.sha256(
            canonical_json(
                {
                    "effect": effect,
                    "entry": entry,
                    "findings": [value["identity"] for value in findings],
                }
            ).encode("utf-8")
        ).hexdigest()
        result.append(
            {
                "chain_id": f"unresolved-{identity}",
                "entry": entry,
                "findings": findings,
                "reason": "finding_scope_unresolved",
            }
        )
    return result


def _finding(
    check: MechanicalCheck,
    *,
    effect: str,
    affected_chains: Sequence[str] = (),
    affected_entries: Sequence[str] = (),
) -> dict[str, object]:
    assert check.failure is not None
    return {
        "admission_effect": effect,
        "affected_chains": list(affected_chains),
        "affected_entries": list(affected_entries),
        "code": check.failure.code,
        "dependencies": [dict(value) for value in check.dependencies],
        "identity": check.identity,
        "observed": dict(check.failure.observed),
        "rule": check.failure.rule,
        "scope": check.scope.value,
        "status": check.status.value,
        "subject": check.subject,
    }


def _unresolved_admission_effect(
    check: MechanicalCheck, entry: str | None
) -> str:
    """Classify one unassigned finding without making reproduction reinterpret it."""

    if not _blocks_reproduction(check) or check.failure is None:
        return "none"
    if check.failure.code == "summary.reference.unresolved":
        return "none"
    return "entry" if entry is not None else "log"


def _blocks_reproduction(check: MechanicalCheck) -> bool:
    if check.status is not CheckStatus.FAIL:
        return False
    if check.scope.value in {"conformance", "evidence"}:
        return True
    return (
        check.scope.value == "provenance"
        and check.failure is not None
        and check.failure.code != "provenance.output.reproduction_required"
    )


def _physical_entry(entry: str) -> str:
    match = re.fullmatch(r"(e[0-9]+)[a-z]?", entry, re.IGNORECASE)
    if match is None:
        raise BatchProjectionError(f"invalid projected entry identity: {entry}")
    return match.group(1).lower()


def _finding_entry(check: MechanicalCheck) -> str | None:
    match = _ENTRY_RE.search(check.identity)
    if match is not None:
        return match.group(1).lower()
    if check.failure is not None:
        owner = check.failure.observed.get("owner")
        if isinstance(owner, str):
            match = _ENTRY_RE.search(owner)
            if match is not None:
                return match.group(1).lower()
    return None


def _finding_strings(check: MechanicalCheck) -> set[str]:
    result = {check.identity, check.subject}
    if check.failure is not None:
        result.update(_strings(check.failure.observed))
    for dependency in check.dependencies:
        result.update(_strings(dependency))
    return result


def _chain_strings(chain: Mapping[str, object]) -> set[str]:
    artifacts = chain.get("artifacts")
    result = {str(value) for value in _sequence_items(artifacts)}
    for resource in _mapping_items(chain.get("registry")):
        result.update(
            str(resource[key])
            for key in ("location", "name", "path")
            if key in resource
        )
    for command in _mapping_items(chain.get("commands")):
        result.update(
            {
                str(command["document"]),
                str(command["identity"]),
                f"entry:{command['entry']}:command:{command['fence']}:{command['ordinal']}",
            }
        )
    return result


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _sequence_items(value) if isinstance(item, Mapping))


def _sequence_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {item for child in value.values() for item in _strings(child)}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return {item for child in value for item in _strings(child)}
    return set()


def _chain_sort_key(value: Mapping[str, object]) -> tuple[str, str]:
    return str(value["entry"]), str(value["chain_id"])
