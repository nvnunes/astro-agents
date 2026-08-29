"""Evidence-rooted v2 producer, lineage, and accepted-root evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from .command_v2 import Invocation, MaterialCollection
from .v2_json import canonical_json

MAX_LINEAGE_DEPTH = 64


class ProvenanceV2Error(ValueError):
    """One completed mechanical provenance failure."""

    def __init__(self, code: str, subject: str, observed: object, rule: str):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule


@dataclass(frozen=True)
class ProvenanceRoot:
    """One accepted external, model, or simulation root."""

    kind: str
    identity: str


@dataclass(frozen=True)
class ProvenanceResult:
    """Successful evidence-rooted provenance projection."""

    material: str
    producers: tuple[str, ...]
    lineage: tuple[tuple[str, str], ...]
    roots: tuple[ProvenanceRoot, ...]
    dependency_projection: str


@dataclass
class _WalkState:
    outputs: Mapping[str, tuple[Invocation, ...]]
    invocations: Sequence[Invocation]
    producers: list[str]
    lineage: list[tuple[str, str]]
    roots: set[ProvenanceRoot]
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
    state = _WalkState(outputs, invocations, [], [], set(), set())
    _walk_material(canonical, None, state, starting=True, depth=0)
    roots = tuple(sorted(state.roots, key=lambda root: (root.kind, root.identity)))
    by_identity = {invocation.identity: invocation for invocation in invocations}
    payload = {
        "lineage": [list(edge) for edge in state.lineage],
        "material": canonical,
        "producers": state.producers,
        "producer_state": [
            _invocation_dependency(by_identity[identity])
            for identity in state.producers
        ],
        "roots": [root.__dict__ for root in roots],
        "version": "v2",
    }
    dependency = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return ProvenanceResult(
        canonical,
        tuple(state.producers),
        tuple(state.lineage),
        roots,
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
    state.producers.append(producer.identity)
    if consumer is not None:
        state.lineage.append((producer.identity, consumer.identity))
    state.visiting.add(producer.identity)
    _walk_invocation(producer, state, depth)
    state.visiting.remove(producer.identity)


def _walk_invocation(invocation: Invocation, state: _WalkState, depth: int) -> None:
    if invocation.command_type in {"model", "simulation"}:
        state.roots.add(ProvenanceRoot(invocation.command_type, invocation.identity))
    if not invocation.inputs:
        if invocation.command_type not in {"model", "simulation"}:
            _fail(
                "provenance.root.missing",
                invocation.identity,
                {"inputs": 0, "type": invocation.command_type},
            )
        return
    for relationship in invocation.inputs:
        if relationship.external:
            state.roots.add(
                ProvenanceRoot(
                    "external",
                    relationship.named_input or relationship.path,
                )
            )
            continue
        _walk_material(
            relationship.path,
            invocation,
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
        "command_type": invocation.command_type,
        "identity": invocation.identity,
        "inputs": [relationship.__dict__ for relationship in invocation.inputs],
        "outputs": [relationship.__dict__ for relationship in invocation.outputs],
        "script_identity": invocation.script_identity,
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
