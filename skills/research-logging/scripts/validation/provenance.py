"""Evidence-rooted producer and declared-input lineage evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, NoReturn, Sequence

from research_log_data import InputResource

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


@dataclass(frozen=True)
class DirectoryProducerMatch:
    """One producer's complete relationship to a canonical directory root."""

    producer: Invocation
    confirmation_targets: tuple[str, ...]
    exact_directory: bool
    member_output: bool
    overlapping_directory: bool


@dataclass(frozen=True)
class _IndexedOutput:
    producer: Invocation
    path: str


@dataclass
class _DirectoryMatchBuilder:
    producer: Invocation
    confirmation_targets: set[str]
    exact_directory: bool = False
    member_output: bool = False
    overlapping_directory: bool = False


@dataclass(frozen=True)
class ProducerIndex:
    """Validation-scoped lookup over canonical invocation producer state."""

    invocations: tuple[Invocation, ...]
    outputs: Mapping[str, tuple[Invocation, ...]]
    by_identity: Mapping[str, Invocation]
    order_by_identity: Mapping[str, int]
    scalar_by_ancestor: Mapping[str, tuple[_IndexedOutput, ...]]
    directory_by_ancestor: Mapping[str, tuple[_IndexedOutput, ...]]
    directory_by_root: Mapping[str, tuple[_IndexedOutput, ...]]

    def lookup(
        self, root: str, *, before_sequence: int | None = None
    ) -> tuple[DirectoryProducerMatch, ...]:
        """Return producers touching ``root`` without filesystem access."""

        builders: dict[str, _DirectoryMatchBuilder] = {}
        _collect_scalar_matches(
            self.scalar_by_ancestor.get(root, ()), builders, before_sequence
        )
        _collect_contained_directory_matches(
            root,
            self.directory_by_ancestor.get(root, ()),
            builders,
            before_sequence,
        )
        _collect_containing_directory_matches(
            root,
            self.directory_by_root,
            builders,
            before_sequence,
        )

        return tuple(
            DirectoryProducerMatch(
                matched.producer,
                tuple(sorted(matched.confirmation_targets)),
                matched.exact_directory,
                matched.member_output,
                matched.overlapping_directory,
            )
            for matched in sorted(
                builders.values(),
                key=lambda value: self.order_by_identity[value.producer.identity],
            )
        )


def _collect_scalar_matches(
    outputs: Sequence[_IndexedOutput],
    builders: dict[str, _DirectoryMatchBuilder],
    before_sequence: int | None,
) -> None:
    for output in outputs:
        matched = _match_builder(output, builders, before_sequence)
        if matched is None:
            continue
        matched.confirmation_targets.add(output.path)
        matched.member_output = True


def _collect_contained_directory_matches(
    root: str,
    outputs: Sequence[_IndexedOutput],
    builders: dict[str, _DirectoryMatchBuilder],
    before_sequence: int | None,
) -> None:
    for output in outputs:
        matched = _match_builder(output, builders, before_sequence)
        if matched is None:
            continue
        matched.confirmation_targets.add(output.path)
        if output.path == root:
            matched.exact_directory = True
        else:
            matched.overlapping_directory = True


def _collect_containing_directory_matches(
    root: str,
    outputs_by_root: Mapping[str, tuple[_IndexedOutput, ...]],
    builders: dict[str, _DirectoryMatchBuilder],
    before_sequence: int | None,
) -> None:
    for ancestor in _path_and_parents(root):
        for output in outputs_by_root.get(ancestor, ()):
            if output.path == root:
                continue
            matched = _match_builder(output, builders, before_sequence)
            if matched is None:
                continue
            matched.confirmation_targets.add(output.path)
            matched.overlapping_directory = True


def _match_builder(
    output: _IndexedOutput,
    builders: dict[str, _DirectoryMatchBuilder],
    before_sequence: int | None,
) -> _DirectoryMatchBuilder | None:
    if before_sequence is not None and output.producer.sequence >= before_sequence:
        return None
    return builders.setdefault(
        output.producer.identity,
        _DirectoryMatchBuilder(output.producer, set()),
    )


@dataclass
class _WalkState:
    producer_index: ProducerIndex
    producers: list[str]
    producer_seen: set[str]
    lineage: list[tuple[str, str]]
    lineage_seen: set[tuple[str, str]]
    visiting: set[str]
    support: list[Mapping[str, object]]
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None
    confirmed_record: Callable[[Invocation, str], bool] | None


def evaluate_provenance(
    material: Path | str,
    invocations: Sequence[Invocation],
    *,
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None = None,
    confirmed_record: Callable[[Invocation, str], bool] | None = None,
    producer_index: ProducerIndex | None = None,
) -> ProvenanceResult:
    """Require one producer and trace only its mechanically proved inputs."""

    canonical = Path(material).resolve().as_posix()
    producer_index = producer_index or build_producer_index(invocations)
    state = _WalkState(
        producer_index,
        [],
        set(),
        [],
        set(),
        set(),
        [],
        producer_validator,
        confirmed_record,
    )
    _walk_material(canonical, None, state, starting=True, depth=0)
    payload = {
        "lineage": [list(edge) for edge in state.lineage],
        "material": canonical,
        "producers": state.producers,
        "producer_state": [
            _invocation_dependency(producer_index.by_identity[identity])
            for identity in state.producers
        ],
        "support": state.support,
        "version": "end-to-end-provenance-1",
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

    producer_index = build_producer_index(invocations)
    return tuple(
        evaluate_provenance(
            material,
            invocations,
            producer_index=producer_index,
        )
        for material in materials
    )


def require_declared_producer(
    material: Path | str,
    invocations: Sequence[Invocation],
    *,
    producer_index: ProducerIndex | None = None,
) -> Invocation:
    """Require one structurally valid producer without asserting execution."""

    canonical = Path(material).resolve().as_posix()
    index = producer_index or build_producer_index(invocations)
    producer = _starting_producer(canonical, index)
    _require_declared_producer_ready(canonical, producer, index)
    return producer


def build_producer_index(
    invocations: Sequence[Invocation],
) -> ProducerIndex:
    """Index canonical invocation producers once for validation-scoped reuse."""

    ordered = tuple(invocations)
    outputs: dict[str, list[Invocation]] = {}
    scalar_by_ancestor: dict[str, list[_IndexedOutput]] = {}
    directory_by_ancestor: dict[str, list[_IndexedOutput]] = {}
    directory_by_root: dict[str, list[_IndexedOutput]] = {}
    for invocation in ordered:
        for output in invocation.outputs:
            outputs.setdefault(output.path, []).append(invocation)
            indexed = _IndexedOutput(invocation, output.path)
            for parent in PurePosixPath(output.path).parents:
                scalar_by_ancestor.setdefault(parent.as_posix(), []).append(indexed)
        for collection in invocation.collections:
            if (
                collection.direction != "output"
                or collection.mechanism != "directory"
            ):
                continue
            root = _collection_root(invocation, collection).as_posix()
            indexed = _IndexedOutput(invocation, root)
            directory_by_root.setdefault(root, []).append(indexed)
            for directory_ancestor in _path_and_parents(root):
                directory_by_ancestor.setdefault(directory_ancestor, []).append(indexed)
    return ProducerIndex(
        ordered,
        {path: tuple(values) for path, values in outputs.items()},
        {invocation.identity: invocation for invocation in ordered},
        {invocation.identity: order for order, invocation in enumerate(ordered)},
        _frozen_output_index(scalar_by_ancestor),
        _frozen_output_index(directory_by_ancestor),
        _frozen_output_index(directory_by_root),
    )


def require_origin_boundary(
    material: Path | str,
    resource: InputResource,
    invocations: Sequence[Invocation],
    *,
    confirmed_record: Callable[[Invocation, str], bool] | None = None,
    producer_index: ProducerIndex | None = None,
) -> None:
    """Reject an origin only when it hides confirmed ``pyrun`` production."""

    if not resource.origin:
        raise ValueError("origin-boundary validation requires origin: true")
    if resource.kind == "git-repository":
        return
    index = producer_index or build_producer_index(invocations)
    if resource.kind == "directory":
        matches = index.lookup(resource.canonical_target)
        confirmed = [
            match.producer
            for match in matches
            if confirmed_record is not None
            and any(
                confirmed_record(match.producer, output)
                for output in match.confirmation_targets
            )
        ]
        if confirmed:
            _fail(
                "directory.origin.conflict",
                resource.name,
                {"producers": sorted(item.identity for item in confirmed)},
            )
        return
    canonical = Path(material).resolve().as_posix()
    file_producers = index.outputs.get(canonical, ())
    confirmed = [
        producer
        for producer in file_producers
        if confirmed_record is not None
        and confirmed_record(producer, canonical)
    ]
    if len(confirmed) == 1:
        _fail(
            "data.origin.invalid",
            resource.name,
            {"producer": confirmed[0].identity},
        )
    if len(confirmed) > 1:
        _fail(
            "lineage.ambiguous",
            canonical,
            {"producers": [producer.identity for producer in confirmed]},
        )


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
    producer = _unique_producer(material, consumer, state, starting=starting)
    _require_producer_ready(material, producer, state)
    _record_producer_lineage(producer, consumer, state)
    state.visiting.add(producer.identity)
    _walk_invocation(producer, state, depth)
    state.visiting.remove(producer.identity)


def _unique_producer(
    material: str,
    consumer: Invocation | None,
    state: _WalkState,
    *,
    starting: bool,
) -> Invocation:
    if starting:
        return _starting_producer(material, state.producer_index)
    candidates = [
        invocation
        for invocation in state.producer_index.outputs.get(material, ())
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
    return candidates[0]


def _starting_producer(
    material: str,
    producer_index: ProducerIndex,
) -> Invocation:
    if Path(material).is_dir():
        return _starting_directory_producer(material, producer_index)
    candidates = producer_index.outputs.get(material, ())
    if not candidates:
        _fail("producer.missing", material, {"consumer": None})
    _fail_shared_output_directory(material, candidates)
    if len(candidates) != 1:
        _fail(
            "producer.ambiguous",
            material,
            {"producers": [item.identity for item in candidates]},
        )
    return candidates[0]


def _starting_directory_producer(
    material: str, producer_index: ProducerIndex
) -> Invocation:
    """Require one exact output-directory producer for a starting root."""

    matches = producer_index.lookup(material)
    exact = tuple(match.producer for match in matches if match.exact_directory)
    if not exact:
        _fail("producer.missing", material, {"consumer": None})
    exact_ids = {invocation.identity for invocation in exact}
    producers_within = {
        match.producer.identity for match in matches if match.member_output
    }
    overlapping = {
        match.producer.identity for match in matches if match.overlapping_directory
    }
    conflicts = (producers_within - exact_ids) | overlapping
    if len(exact) != 1 or conflicts:
        _fail(
            "producer.ambiguous",
            material,
            {
                "producers": [item.identity for item in exact],
                "conflicts": sorted(conflicts),
            },
        )
    return exact[0]


def _require_producer_ready(
    material: str, producer: Invocation, state: _WalkState
) -> None:
    _require_declared_producer_ready(
        material, producer, state.producer_index
    )
    if producer.identity in state.visiting:
        _fail("lineage.cycle", material, {"invocation": producer.identity})
    if state.producer_validator is not None:
        state.support.append(state.producer_validator(producer, material))


def _require_declared_producer_ready(
    material: str,
    producer: Invocation,
    producer_index: ProducerIndex,
) -> None:
    path = Path(material)
    if not path.is_file() and not path.is_dir():
        _fail(
            "provenance.output.missing",
            material,
            {"producer": producer.identity},
        )
    if _requires_local_script(producer) and producer.script_identity is None:
        _fail(
            "invocation.executable.unresolved",
            producer.identity,
            {"script": producer.script},
        )
    _validate_output_directories(producer, producer_index)


def _record_producer_lineage(
    producer: Invocation, consumer: Invocation | None, state: _WalkState
) -> None:
    if producer.identity not in state.producer_seen:
        state.producers.append(producer.identity)
        state.producer_seen.add(producer.identity)
    if consumer is not None:
        edge = (producer.identity, consumer.identity)
        if edge not in state.lineage_seen:
            state.lineage.append(edge)
            state.lineage_seen.add(edge)


def _walk_invocation(invocation: Invocation, state: _WalkState, depth: int) -> None:
    if not invocation.inputs:
        return
    for relationship in invocation.inputs:
        if relationship.origin and relationship.input_resource is not None:
            require_origin_boundary(
                relationship.path,
                relationship.input_resource,
                state.producer_index.invocations,
                confirmed_record=state.confirmed_record,
                producer_index=state.producer_index,
            )
            continue
        if (
            relationship.input_resource is not None
            and relationship.input_resource.kind == "directory"
        ):
            _walk_directory_input(relationship, invocation, state, depth)
            continue
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
    if relationship.origin:
        require_origin_boundary(
            relationship.path,
            resource,
            state.producer_index.invocations,
            confirmed_record=state.confirmed_record,
            producer_index=state.producer_index,
        )
        return
    matches = state.producer_index.lookup(
        resource.canonical_target,
        before_sequence=consumer.sequence,
    )
    exact = tuple(match.producer for match in matches if match.exact_directory)
    producers_within = {
        match.producer.identity for match in matches if match.member_output
    }
    exact_ids = {invocation.identity for invocation in exact}
    overlapping = {
        match.producer.identity for match in matches if match.overlapping_directory
    }
    conflicts = (producers_within - exact_ids) | overlapping
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
    if owner not in state.producer_index.outputs.get(relationship.path, ()):
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


def _invocation_dependency(invocation: Invocation) -> Mapping[str, object]:
    return {
        "identity": invocation.identity,
        "inputs": [_relationship_dependency(value) for value in invocation.inputs],
        "outputs": [_relationship_dependency(value) for value in invocation.outputs],
        "parameters": list(invocation.parameters),
        "script_argument": invocation.script_argument,
        "script_identity": invocation.script_identity,
    }


def _relationship_dependency(
    relationship: MaterialRelationship,
) -> Mapping[str, object]:
    resource = relationship.input_resource
    return {
        "direction": relationship.direction,
        "origin": relationship.origin,
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
    producer: Invocation, producer_index: ProducerIndex
) -> None:
    directories = [
        collection
        for collection in producer.collections
        if collection.direction == "output" and collection.mechanism == "directory"
    ]
    for collection in directories:
        root = _collection_root(producer, collection)
        conflicts = {
            match.producer.identity
            for match in producer_index.lookup(root.as_posix())
            if (
                match.producer.identity == producer.identity
                and match.overlapping_directory
            )
            or (
                match.producer.identity != producer.identity
                and (
                    match.exact_directory
                    or match.member_output
                    or match.overlapping_directory
                )
            )
        }
        if conflicts:
            _fail(
                "collection.output_directory.shared",
                root.as_posix(),
                {"owners": sorted({producer.identity, *conflicts})},
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


def _path_and_parents(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    return (path.as_posix(), *(parent.as_posix() for parent in path.parents))


def _frozen_output_index(
    values: Mapping[str, list[_IndexedOutput]],
) -> Mapping[str, tuple[_IndexedOutput, ...]]:
    return {key: tuple(items) for key, items in values.items()}


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise ProvenanceV2Error(
        code,
        subject,
        observed,
        "Recorded-Command Provenance And Material Graph",
    )
