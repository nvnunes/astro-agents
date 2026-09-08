"""Evidence-rooted producer and declared-input lineage evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, MutableMapping, NoReturn, Sequence

from research_log_data import InputResource

from .commands import Invocation, MaterialCollection, MaterialRelationship
from .errors import MechanicalContractError
from .json_codec import canonical_json

MAX_LINEAGE_DEPTH = 64


class ProvenanceV2Error(MechanicalContractError):
    """One completed mechanical provenance failure."""


@dataclass(frozen=True)
class ProvenanceResult:
    """Complete bounded evidence-rooted provenance projection."""

    material: str
    producers: tuple[str, ...]
    lineage: tuple[tuple[str, str], ...]
    findings: tuple[ProvenanceFinding, ...]
    dependency_projection: str


@dataclass(frozen=True)
class ProvenanceFinding:
    """One independently established finding from graph traversal."""

    code: str
    subject: str
    observed: Mapping[str, object]
    rule: str
    outcome: str = "fail"

    def as_dict(self) -> Mapping[str, object]:
        """Return the deterministic dependency projection for this finding."""

        return {
            "code": self.code,
            "observed": dict(self.observed),
            "outcome": self.outcome,
            "rule": self.rule,
            "subject": self.subject,
        }


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
    lookup_cache: dict[
        tuple[str, int | None], tuple[DirectoryProducerMatch, ...]
    ] = field(default_factory=dict, compare=False, repr=False)
    dependency_cache: dict[str, Mapping[str, object]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def lookup(
        self, root: str, *, before_sequence: int | None = None
    ) -> tuple[DirectoryProducerMatch, ...]:
        """Return producers touching ``root`` without filesystem access."""

        cache_key = (root, before_sequence)
        cached = self.lookup_cache.get(cache_key)
        if cached is not None:
            return cached
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

        result = tuple(
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
        self.lookup_cache[cache_key] = result
        return result


@dataclass(frozen=True)
class _WalkTrace:
    """One reusable root producer's ordered upstream traversal effects."""

    producers: tuple[str, ...]
    lineage: tuple[tuple[str, str], ...]
    support: tuple[Mapping[str, object], ...]
    findings: tuple[tuple[str, ProvenanceFinding], ...]


@dataclass(frozen=True)
class _ProvenanceDependency:
    """Values contributing to one provenance dependency projection."""

    material: str
    producers: Sequence[str]
    lineage: Sequence[tuple[str, str]]
    support: Sequence[Mapping[str, object]]
    findings: Sequence[ProvenanceFinding]


@dataclass
class CompleteProvenanceContext:
    """Scan-local immutable-index and reusable producer conclusions."""

    producer_index: ProducerIndex
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None = None
    confirmed_record: Callable[[Invocation, str], bool] | None = None
    output_directory_cache: MutableMapping[int, ProvenanceFinding | None] = field(
        default_factory=dict
    )
    root_invocation_cache: MutableMapping[int, _WalkTrace] = field(
        default_factory=dict
    )
    origin_boundary_cache: MutableMapping[
        tuple[str, int], ProvenanceFinding | None
    ] = field(default_factory=dict)
    canonical_mapping_cache: MutableMapping[int, str] = field(default_factory=dict)


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
    findings: list[ProvenanceFinding]
    finding_seen: set[str]
    collect_findings: bool
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None
    confirmed_record: Callable[[Invocation, str], bool] | None
    output_directory_cache: MutableMapping[int, ProvenanceFinding | None] | None
    root_invocation_cache: MutableMapping[int, _WalkTrace] | None
    origin_boundary_cache: MutableMapping[
        tuple[str, int], ProvenanceFinding | None
    ] | None


@dataclass(frozen=True)
class _EvaluationConfig:
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None
    confirmed_record: Callable[[Invocation, str], bool] | None
    producer_index: ProducerIndex | None
    collect_findings: bool
    output_directory_cache: MutableMapping[int, ProvenanceFinding | None] | None = None
    root_invocation_cache: MutableMapping[int, _WalkTrace] | None = None
    origin_boundary_cache: MutableMapping[
        tuple[str, int], ProvenanceFinding | None
    ] | None = None
    canonical_mapping_cache: MutableMapping[int, str] | None = None


def evaluate_provenance(
    material: Path | str,
    invocations: Sequence[Invocation],
    *,
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None = None,
    confirmed_record: Callable[[Invocation, str], bool] | None = None,
    producer_index: ProducerIndex | None = None,
) -> ProvenanceResult:
    """Require one producer and raise the first provenance failure."""

    return _evaluate_provenance(
        material,
        invocations,
        _EvaluationConfig(
            producer_validator, confirmed_record, producer_index, False
        ),
    )


def evaluate_complete_provenance(
    material: Path | str,
    invocations: Sequence[Invocation],
    *,
    producer_validator: Callable[[Invocation, str], Mapping[str, object]] | None = None,
    confirmed_record: Callable[[Invocation, str], bool] | None = None,
    context: CompleteProvenanceContext | None = None,
) -> ProvenanceResult:
    """Collect every independently reachable bounded provenance failure."""

    if context is not None:
        if producer_validator is not None or confirmed_record is not None:
            raise ValueError("complete provenance context owns validation callbacks")
        producer_validator = context.producer_validator
        confirmed_record = context.confirmed_record
    return _evaluate_provenance(
        material,
        invocations,
        _EvaluationConfig(
            producer_validator,
            confirmed_record,
            context.producer_index if context is not None else None,
            True,
            context.output_directory_cache if context is not None else None,
            context.root_invocation_cache if context is not None else None,
            context.origin_boundary_cache if context is not None else None,
            context.canonical_mapping_cache if context is not None else None,
        ),
    )


def _evaluate_provenance(
    material: Path | str,
    invocations: Sequence[Invocation],
    config: _EvaluationConfig,
) -> ProvenanceResult:
    """Evaluate one evidence-rooted graph under an explicit failure policy.

    With collection enabled, a failure stops only the edge that cannot be
    followed safely and every other reachable branch is evaluated. When
    disabled, the first failure preserves the strict direct-check contract.
    """

    canonical = Path(material).resolve().as_posix()
    producer_index = config.producer_index or build_producer_index(invocations)
    state = _WalkState(
        producer_index,
        [],
        set(),
        [],
        set(),
        set(),
        [],
        [],
        set(),
        config.collect_findings,
        config.producer_validator,
        config.confirmed_record,
        config.output_directory_cache,
        config.root_invocation_cache,
        config.origin_boundary_cache,
    )
    _walk_material(canonical, None, state, starting=True, depth=0)
    dependency_json = _provenance_dependency_json(
        _ProvenanceDependency(
            canonical,
            state.producers,
            state.lineage,
            state.support,
            state.findings,
        ),
        producer_index,
        config.canonical_mapping_cache,
    )
    dependency = hashlib.sha256(dependency_json.encode()).hexdigest()
    return ProvenanceResult(
        canonical,
        tuple(state.producers),
        tuple(state.lineage),
        tuple(state.findings),
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
    allow_missing: bool = False,
) -> Invocation:
    """Require one structurally valid producer without asserting execution."""

    canonical = Path(material).resolve().as_posix()
    index = producer_index or build_producer_index(invocations)
    producer = _starting_producer(canonical, index)
    _require_declared_producer_ready(
        canonical, producer, index, allow_missing=allow_missing
    )
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
        directory_roots = {
            _collection_root(invocation, collection).as_posix()
            for collection in invocation.collections
            if collection.direction == "output"
            and collection.mechanism == "directory"
        }
        for output in invocation.outputs:
            if output.path in directory_roots:
                continue
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
            outputs.setdefault(root, []).append(invocation)
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
    """Reject an origin only when it hides confirmed selected-log production.

    ``invocations`` and ``producer_index`` must contain commands from only the
    log being validated. Origin boundaries deliberately do not consult or
    import another log's execution state.
    """

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


def _require_origin_boundary_cached(
    material: str, resource: InputResource, state: _WalkState
) -> None:
    """Reuse one origin-boundary conclusion under a scan's fixed callbacks."""

    cache = state.origin_boundary_cache
    identity = (material, id(resource))
    if cache is not None and identity in cache:
        failure = cache[identity]
        if failure is not None:
            raise ProvenanceV2Error(
                failure.code,
                failure.subject,
                failure.observed,
                failure.rule,
                outcome=failure.outcome,
            )
        return
    try:
        require_origin_boundary(
            material,
            resource,
            state.producer_index.invocations,
            confirmed_record=state.confirmed_record,
            producer_index=state.producer_index,
        )
    except MechanicalContractError as error:
        if cache is not None:
            cache[identity] = _finding(error)
        raise
    if cache is not None:
        cache[identity] = None


def _walk_material(
    material: str,
    consumer: Invocation | None,
    state: _WalkState,
    *,
    starting: bool,
    depth: int,
) -> None:
    try:
        if depth > MAX_LINEAGE_DEPTH:
            _fail(
                "provenance.resource.too_large",
                material,
                {"depth": depth, "limit": MAX_LINEAGE_DEPTH},
            )
        producer = _unique_producer(material, consumer, state, starting=starting)
    except MechanicalContractError as error:
        _record_finding(state, error)
        return
    if not _check_producer_ready(material, producer, state):
        return
    _record_producer_lineage(producer, consumer, state)
    state.visiting.add(producer.identity)
    try:
        _walk_invocation(producer, state, depth)
    finally:
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
        matches = producer_index.lookup(material)
        directory_owners = {
            match.producer.identity: match.producer
            for match in matches
            if match.overlapping_directory
        }
        if len(directory_owners) == 1:
            return next(iter(directory_owners.values()))
        _fail(
            "producer.missing" if not directory_owners else "producer.ambiguous",
            material,
            {
                "consumer": None,
                "producers": sorted(directory_owners),
            },
        )
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


def _check_producer_ready(
    material: str, producer: Invocation, state: _WalkState
) -> bool:
    output_available = True
    if not state.collect_findings:
        _require_declared_producer_ready(material, producer, state.producer_index)
    else:
        path = Path(material)
        if not path.is_file() and not path.is_dir():
            output_available = False
            _record_finding(
                state,
                _provenance_error(
                    "provenance.output.missing",
                    material,
                    {"producer": producer.identity},
                ),
            )
        if _requires_local_script(producer) and producer.script_identity is None:
            _record_finding(
                state,
                _provenance_error(
                    "invocation.executable.unresolved",
                    producer.identity,
                    {"script": producer.script},
                ),
            )
        try:
            _validate_output_directories_cached(producer, state)
        except MechanicalContractError as error:
            _record_finding(state, error)
    if producer.identity in state.visiting:
        _record_finding(
            state,
            _provenance_error(
                "lineage.cycle", material, {"invocation": producer.identity}
            ),
        )
        return False
    if state.producer_validator is not None and output_available:
        try:
            state.support.append(state.producer_validator(producer, material))
        except MechanicalContractError as error:
            _record_finding(state, error)
            state.support.append({"finding": _finding(error).as_dict()})
    return True


def _validate_output_directories_cached(
    producer: Invocation, state: _WalkState
) -> None:
    """Reuse one immutable producer/index ownership conclusion per scan."""

    cache = state.output_directory_cache
    identity = id(producer)
    if cache is not None and identity in cache:
        failure = cache[identity]
        if failure is not None:
            raise ProvenanceV2Error(
                failure.code,
                failure.subject,
                failure.observed,
                failure.rule,
                outcome=failure.outcome,
            )
        return
    try:
        _validate_output_directories(producer, state.producer_index)
    except MechanicalContractError as error:
        if cache is not None:
            cache[identity] = _finding(error)
        raise
    if cache is not None:
        cache[identity] = None


def _require_declared_producer_ready(
    material: str,
    producer: Invocation,
    producer_index: ProducerIndex,
    *,
    allow_missing: bool = False,
) -> None:
    path = Path(material)
    if not allow_missing and not path.is_file() and not path.is_dir():
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
    """Walk one producer, reusing traces only from an identical root context."""

    cache = state.root_invocation_cache
    if cache is None or not state.collect_findings or len(state.visiting) != 1:
        _walk_invocation_uncached(invocation, state, depth)
        return
    identity = id(invocation)
    cached = cache.get(identity)
    if cached is not None:
        _merge_walk_trace(cached, state)
        return
    trace_state = _WalkState(
        state.producer_index,
        [],
        set(),
        [],
        set(),
        set(state.visiting),
        [],
        [],
        set(),
        True,
        state.producer_validator,
        state.confirmed_record,
        state.output_directory_cache,
        state.root_invocation_cache,
        state.origin_boundary_cache,
    )
    _walk_invocation_uncached(invocation, trace_state, depth)
    trace = _WalkTrace(
        tuple(trace_state.producers),
        tuple(trace_state.lineage),
        tuple(trace_state.support),
        tuple(
            (canonical_json(finding.as_dict()), finding)
            for finding in trace_state.findings
        ),
    )
    cache[identity] = trace
    _merge_walk_trace(trace, state)


def _merge_walk_trace(trace: _WalkTrace, state: _WalkState) -> None:
    """Replay one cached root trace with normal result-level deduplication."""

    for producer in trace.producers:
        if producer not in state.producer_seen:
            state.producers.append(producer)
            state.producer_seen.add(producer)
    for edge in trace.lineage:
        if edge not in state.lineage_seen:
            state.lineage.append(edge)
            state.lineage_seen.add(edge)
    state.support.extend(trace.support)
    for identity, finding in trace.findings:
        if identity not in state.finding_seen:
            state.findings.append(finding)
            state.finding_seen.add(identity)


def _walk_invocation_uncached(
    invocation: Invocation, state: _WalkState, depth: int
) -> None:
    """Walk one producer without consulting the root-trace cache."""

    if not invocation.inputs:
        return
    for relationship in invocation.inputs:
        if relationship.origin and relationship.input_resource is not None:
            try:
                _require_origin_boundary_cached(
                    relationship.path,
                    relationship.input_resource,
                    state,
                )
            except MechanicalContractError as error:
                _record_finding(state, error)
            continue
        if (
            relationship.input_resource is not None
            and relationship.input_resource.kind == "directory"
        ):
            try:
                _walk_directory_input(relationship, invocation, state, depth)
            except MechanicalContractError as error:
                _record_finding(state, error)
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
        _require_origin_boundary_cached(relationship.path, resource, state)
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


def _invocation_dependency_cached(
    producer_index: ProducerIndex, identity: str
) -> Mapping[str, object]:
    """Reuse one immutable invocation dependency projection per scan."""

    cached = producer_index.dependency_cache.get(identity)
    if cached is not None:
        return cached
    dependency = _invocation_dependency(producer_index.by_identity[identity])
    producer_index.dependency_cache[identity] = dependency
    return dependency


def _provenance_dependency_json(
    value: _ProvenanceDependency,
    producer_index: ProducerIndex,
    cache: MutableMapping[int, str] | None,
) -> str:
    """Serialize the unchanged dependency contract with reusable mappings."""

    producer_state = tuple(
        _invocation_dependency_cached(producer_index, identity)
        for identity in value.producers
    )
    return (
        '{"findings":'
        + canonical_json([finding.as_dict() for finding in value.findings])
        + ',"lineage":'
        + canonical_json(value.lineage)
        + ',"material":'
        + canonical_json(value.material)
        + ',"producer_state":'
        + _canonical_mapping_sequence(producer_state, cache)
        + ',"producers":'
        + canonical_json(value.producers)
        + ',"support":'
        + _canonical_mapping_sequence(value.support, cache)
        + ',"version":"end-to-end-provenance-2"}'
    )


def _canonical_mapping_sequence(
    values: Sequence[Mapping[str, object]], cache: MutableMapping[int, str] | None
) -> str:
    """Serialize mappings in order while reusing their exact canonical forms."""

    if cache is None:
        return canonical_json(values)
    serialized: list[str] = []
    for value in values:
        identity = id(value)
        item = cache.get(identity)
        if item is None:
            item = canonical_json(value)
            cache[identity] = item
        serialized.append(item)
    return "[" + ",".join(serialized) + "]"


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


def _record_finding(state: _WalkState, error: MechanicalContractError) -> None:
    """Record one finding or preserve strict first-failure evaluation."""

    if not state.collect_findings:
        raise error
    finding = _finding(error)
    identity = canonical_json(finding.as_dict())
    if identity not in state.finding_seen:
        state.findings.append(finding)
        state.finding_seen.add(identity)


def _finding(error: MechanicalContractError) -> ProvenanceFinding:
    observed = (
        dict(error.observed)
        if isinstance(error.observed, Mapping)
        else {"value": error.observed}
    )
    return ProvenanceFinding(
        error.code, error.subject, observed, error.rule, error.outcome
    )


def _provenance_error(
    code: str, subject: str, observed: object
) -> ProvenanceV2Error:
    return ProvenanceV2Error(
        code,
        subject,
        observed,
        "Recorded-Command Provenance And Material Graph",
    )


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise _provenance_error(code, subject, observed)
