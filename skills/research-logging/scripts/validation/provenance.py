"""Evidence-rooted producer and declared-input lineage evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from .commands import Invocation, MaterialCollection, MaterialRelationship
from .errors import MechanicalContractError
from .json_codec import canonical_json

MAX_LINEAGE_DEPTH = 64


class ProvenanceV2Error(MechanicalContractError):
    """One completed mechanical provenance failure."""


@dataclass(frozen=True)
class ProvenanceResult:
    """Successful evidence-rooted provenance projection."""

    material: str
    producers: tuple[str, ...]
    lineage: tuple[tuple[str, str], ...]
    dependency_projection: str


@dataclass
class _WalkState:
    outputs: Mapping[str, tuple[Invocation, ...]]
    invocations: Sequence[Invocation]
    producers: list[str]
    producer_seen: set[str]
    lineage: list[tuple[str, str]]
    lineage_seen: set[tuple[str, str]]
    visiting: set[str]


def evaluate_provenance(
    material: Path | str,
    invocations: Sequence[Invocation],
) -> ProvenanceResult:
    """Require one producer and trace only its mechanically proved inputs."""

    canonical = Path(material).resolve().as_posix()
    if not Path(canonical).is_file() and not Path(canonical).is_dir():
        _fail("material.unresolved", canonical, {"exists": False})
    outputs = _output_index(invocations)
    state = _WalkState(outputs, invocations, [], set(), [], set(), set())
    _walk_material(canonical, None, state, starting=True, depth=0)
    by_identity = {invocation.identity: invocation for invocation in invocations}
    payload = {
        "lineage": [list(edge) for edge in state.lineage],
        "material": canonical,
        "producers": state.producers,
        "producer_state": [
            _invocation_dependency(by_identity[identity])
            for identity in state.producers
        ],
        "version": "input-registry-1",
    }
    dependency = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return ProvenanceResult(
        canonical,
        tuple(state.producers),
        tuple(state.lineage),
        dependency,
    )


def evaluate_many(
    materials: Sequence[Path | str], invocations: Sequence[Invocation]
) -> tuple[ProvenanceResult, ...]:
    """Evaluate independent evidence starting points without shared conclusions."""

    return tuple(evaluate_provenance(material, invocations) for material in materials)


def _walk_material(
    material: str,
    consumer: Invocation | None,
    state: _WalkState,
    *,
    starting: bool,
    depth: int,
) -> None:
    if depth > MAX_LINEAGE_DEPTH:
        _fail(
            "provenance.resource.too_large",
            material,
            {"depth": depth, "limit": MAX_LINEAGE_DEPTH},
        )
    candidates = [
        invocation
        for invocation in state.outputs.get(material, ())
        if consumer is None or invocation.sequence < consumer.sequence
    ]
    if not candidates:
        _fail(
            "producer.missing" if starting else "lineage.missing",
            material,
            {"consumer": consumer.identity if consumer else None},
        )
    _fail_shared_output_directory(material, candidates)
    if len(candidates) != 1:
        _fail(
            "producer.ambiguous" if starting else "lineage.ambiguous",
            material,
            {"producers": [item.identity for item in candidates]},
        )
    producer = candidates[0]
    if producer.identity in state.visiting:
        _fail("lineage.cycle", material, {"invocation": producer.identity})
    if _requires_local_script(producer) and producer.script_identity is None:
        _fail(
            "invocation.executable.unresolved",
            producer.identity,
            {"script": producer.script},
        )
    _validate_output_directories(producer, state.invocations)
    if producer.identity not in state.producer_seen:
        state.producers.append(producer.identity)
        state.producer_seen.add(producer.identity)
    if consumer is not None:
        edge = (producer.identity, consumer.identity)
        if edge not in state.lineage_seen:
            state.lineage.append(edge)
            state.lineage_seen.add(edge)
    state.visiting.add(producer.identity)
    _walk_invocation(producer, state, depth)
    state.visiting.remove(producer.identity)


def _walk_invocation(invocation: Invocation, state: _WalkState, depth: int) -> None:
    if not invocation.inputs:
        return
    for relationship in invocation.inputs:
        if (
            relationship.input_resource is not None
            and relationship.input_resource.kind == "directory"
        ):
            _walk_directory_input(relationship, invocation, state, depth)
            continue
        prior_producers = tuple(
            producer
            for producer in state.outputs.get(relationship.path, ())
            if producer.sequence < invocation.sequence
        )
        if relationship.external and not prior_producers:
            continue
        if relationship.external and len(prior_producers) == 1:
            _fail(
                "data.external.invalid",
                relationship.named_input or relationship.path,
                {"producer": prior_producers[0].identity},
            )
        _walk_material(
            relationship.path,
            invocation,
            state,
            starting=False,
            depth=depth + 1,
        )


def _walk_directory_input(
    relationship: MaterialRelationship,
    consumer: Invocation,
    state: _WalkState,
    depth: int,
) -> None:
    resource = relationship.input_resource
    assert resource is not None
    root = Path(resource.canonical_target)
    earlier = tuple(
        invocation
        for invocation in state.invocations
        if invocation.sequence < consumer.sequence
    )
    exact = tuple(
        invocation
        for invocation in earlier
        if any(
            collection.direction == "output"
            and collection.mechanism == "directory"
            and collection.root is not None
            and Path(collection.root).resolve() == root.resolve()
            for collection in invocation.collections
        )
    )
    producers_within = {
        invocation.identity
        for invocation in earlier
        if any(_within(Path(output.path), root) for output in invocation.outputs)
    }
    exact_ids = {invocation.identity for invocation in exact}
    overlapping = {
        invocation.identity
        for invocation in earlier
        for collection in invocation.collections
        if collection.direction == "output"
        and collection.mechanism == "directory"
        and collection.root is not None
        and Path(collection.root).resolve() != root.resolve()
        and (
            _within(Path(collection.root), root) or _within(root, Path(collection.root))
        )
    }
    conflicts = (producers_within - exact_ids) | overlapping
    if relationship.external:
        if exact or producers_within or overlapping:
            _fail(
                "directory.external.conflict",
                resource.name,
                {"producers": sorted(exact_ids | producers_within | overlapping)},
            )
        return
    if len(exact) != 1 or conflicts:
        _fail(
            "directory.producer.conflict",
            resource.name,
            {
                "exact_producers": [item.identity for item in exact],
                "conflicts": sorted(conflicts),
            },
        )
    owner = exact[0]
    if owner not in state.outputs.get(relationship.path, ()):
        _fail(
            "directory.producer.conflict",
            resource.name,
            {"missing_member": relationship.path, "producer": owner.identity},
        )
    _walk_material(
        relationship.path,
        consumer,
        state,
        starting=False,
        depth=depth + 1,
    )


def _output_index(
    invocations: Sequence[Invocation],
) -> dict[str, tuple[Invocation, ...]]:
    result: dict[str, list[Invocation]] = {}
    for invocation in invocations:
        for output in invocation.outputs:
            result.setdefault(output.path, []).append(invocation)
    return {path: tuple(values) for path, values in result.items()}


def _invocation_dependency(invocation: Invocation) -> Mapping[str, object]:
    return {
        "identity": invocation.identity,
        "inputs": [_relationship_dependency(value) for value in invocation.inputs],
        "outputs": [_relationship_dependency(value) for value in invocation.outputs],
        "script_identity": invocation.script_identity,
    }


def _relationship_dependency(
    relationship: MaterialRelationship,
) -> Mapping[str, object]:
    resource = relationship.input_resource
    return {
        "direction": relationship.direction,
        "external": relationship.external,
        "input": resource.as_dict() if resource is not None else None,
        "input_identity": resource.content_identity if resource is not None else None,
        "named_input": relationship.named_input,
        "path": relationship.path,
        "proof": relationship.proof,
        "target": relationship.target,
    }


def _requires_local_script(invocation: Invocation) -> bool:
    executable = Path(invocation.executable).name
    return (
        executable == "pyrun"
        or executable.startswith("python")
        or invocation.executable.startswith("./")
        or invocation.executable.startswith("../")
    )


def _fail_shared_output_directory(
    material: str, candidates: Sequence[Invocation]
) -> None:
    if len(candidates) < 2:
        return
    for invocation in candidates:
        for collection in invocation.collections:
            if (
                collection.direction == "output"
                and collection.mechanism == "directory"
                and _within(Path(material), _collection_root(invocation, collection))
            ):
                _fail(
                    "collection.output_directory.shared",
                    _collection_root(invocation, collection).as_posix(),
                    {"owners": [item.identity for item in candidates]},
                )


def _validate_output_directories(
    producer: Invocation, invocations: Sequence[Invocation]
) -> None:
    directories = [
        collection
        for collection in producer.collections
        if collection.direction == "output" and collection.mechanism == "directory"
    ]
    for collection in directories:
        root = _collection_root(producer, collection)
        conflicts = [
            other.identity
            for other in invocations
            if other.identity != producer.identity
            and any(_within(Path(output.path), root) for output in other.outputs)
        ]
        if conflicts:
            _fail(
                "collection.output_directory.shared",
                root.as_posix(),
                {"owners": [producer.identity, *conflicts]},
            )


def _collection_root(invocation: Invocation, collection: MaterialCollection) -> Path:
    if collection.root is not None:
        return Path(collection.root)
    relationships = [
        relationship
        for relationship in invocation.outputs
        if relationship.target == collection.target
        and relationship.proof == "directory"
    ]
    if not relationships:
        _fail(
            "collection.membership.unresolved",
            invocation.identity,
            {"target": collection.target},
        )
    common = Path(relationships[0].path)
    for relationship in relationships[1:]:
        while not _within(Path(relationship.path), common):
            common = common.parent
    return common


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise ProvenanceV2Error(
        code,
        subject,
        observed,
        "Recorded-Command Provenance And Material Graph",
    )
