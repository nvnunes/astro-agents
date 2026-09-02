"""Integrated mechanical-validation engine for active evidence records."""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, Sequence

from research_log_data import (
    DataContractError,
    DataFile,
    FingerprintObservation,
    InputResource,
    find_log_consistency_conflicts,
    load_data_file,
    resolve_input_token,
    validate_fingerprint_observation,
    verify_fingerprint,
)

from .commands import (
    CommandContext,
    Invocation,
    ScriptObservation,
    discover_commands,
    order_invocations,
)
from .entry_materials import (
    EntryMaterialPathError,
    validate_entry_path_symlinks,
    validate_local_path_symlinks,
)
from .errors import MechanicalContractError
from .evidence import (
    MAX_PRESENTATIONS_PER_LOG,
    MAX_RECORDS_PER_LOG,
    MAX_SUMMARY_REFERENCES_PER_LOG,
    SECTION_CLASSIFIER_VERSION,
    CanonicalPresentation,
    DirectArtifactPresentation,
    EvidenceFile,
    EvidenceSource,
    PresentationRecord,
    PresentedItem,
    SummaryReference,
    associate_presentations,
    index_direct_artifacts,
    index_entry_documents,
    index_entry_presentation_candidates,
    index_entry_presentations,
    index_entry_section_issues,
    index_summary_references,
    index_summary_statistic_candidates,
    load_evidence_file,
    resolve_summary_references,
)
from .filesystem import BoundedTraversalError, bounded_descendants
from .fingerprint_cache import FingerprintCache, project_root
from .json_codec import canonical_json
from .locator import (
    SourceObservation,
    evaluate_observed_locator,
    observe_source,
)
from .material_graph import (
    DirectArtifactConnection,
    EvidenceConnection,
    InputRegistrySurface,
    MaterialGraphRequest,
    MaterialGraphResult,
    compose_material_graph,
)
from .mechanical import (
    MechanicalEvaluationPolicy,
    MechanicalEvaluationRequest,
)
from .mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)
from .provenance import evaluate_provenance
from .retention import RetentionFile, load_retention_file
from .transformation import (
    TransformationResult,
    compare_presentation,
    evaluate_transformation,
)

RULES_VERSION = "research-log-mechanical/input-registry-4"
CACHE_SCHEMA = "research-log-mechanical-cache/6"
CACHE_FIELDS = frozenset(
    {
        "artifact_identities",
        "checks",
        "rules_version",
        "schema",
    }
)
ENTRY_ID_RE = re.compile(r"e[0-9]+[a-z]?\Z", re.IGNORECASE)
MAX_ENTRY_SURFACE_PATHS = 1_000_000


# Evaluation contracts and mutable scan state.


class EngineV2Error(MechanicalContractError):
    """One precise integration-level mechanical failure."""


@dataclass(frozen=True)
class _Entry:
    id: str
    document: Path
    root: Path
    evidence_file: EvidenceFile | None
    data_file: DataFile | None
    retention_file: RetentionFile | None
    evidence_failed: bool = False


@dataclass(frozen=True)
class _EntrySurface:
    evidence_file: EvidenceFile | None
    evidence_failed: bool
    data_file: DataFile | None
    retention_file: RetentionFile | None


@dataclass(frozen=True)
class _ResolvedSource:
    path: Path
    external: bool


@dataclass(frozen=True)
class _FailureSpec:
    code: str
    subject: str
    observed: Mapping[str, object]
    rule: str
    dependency: str | None = None
    status: CheckStatus = CheckStatus.FAIL


@dataclass
class _RecordOutcome:
    entry: str
    record: PresentationRecord
    item: PresentedItem
    materials: tuple[_ResolvedSource, ...]
    evidence_check: MechanicalCheck
    provenance_check: MechanicalCheck
    canonical: CanonicalPresentation | None
    dependencies: tuple[str, ...]


@dataclass
class _ScanState:
    summary: Path
    log_root: Path
    project_root: Path
    fingerprint_cache: FingerprintCache | None = None
    checks: list[MechanicalCheck] = field(default_factory=list)
    entries: list[_Entry] = field(default_factory=list)
    invocations: tuple[Invocation, ...] = ()
    command_candidate_dependencies: dict[str, set[str]] = field(default_factory=dict)
    command_failure_owners: dict[str, set[str]] = field(default_factory=dict)
    records: list[_RecordOutcome] = field(default_factory=list)
    direct: list[DirectArtifactConnection] = field(default_factory=list)
    graph: MaterialGraphResult | None = None
    selection_cache: dict[tuple[str, str], object] = field(default_factory=dict)
    source_cache: dict[str, SourceObservation] = field(default_factory=dict)
    script_cache: dict[str, ScriptObservation] = field(default_factory=dict)
    script_identity_seeds: dict[str, Mapping[str, object]] = field(default_factory=dict)
    artifact_identity_seeds: dict[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    artifact_identities: dict[str, Mapping[str, object]] = field(default_factory=dict)
    input_observations: dict[str, FingerprintObservation] = field(default_factory=dict)
    markdown_reads: int = 0
    presentation_count: int = 0
    source_evaluations: int = 0
    source_hashes_reused: int = 0
    input_fingerprints_reused: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    text_cache: dict[Path, str] = field(default_factory=dict)


@dataclass
class _OrphanGroupingState:
    log_identity: str
    owner: str
    inventory: set[str]
    orphan_paths: Mapping[str, str]
    grouped: set[str]
    result: dict[str, Mapping[str, object]]


# Top-level evaluation lifecycle.


def mechanical_policy() -> MechanicalEvaluationPolicy[MechanicalGeneratedRecord]:
    """Return the active policy for ``evaluate_mechanical``."""

    return MechanicalEvaluationPolicy(scan=_scan, evaluate=_evaluate)


def _scan(
    request: MechanicalEvaluationRequest,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    started = time.perf_counter()
    summary = request.summary_path.resolve()
    state = _ScanState(
        summary,
        summary.with_suffix(""),
        project_root(summary),
        request.fingerprint_cache,
    )
    state.artifact_identity_seeds = _accepted_artifact_identities(
        request.prior_cache, state.project_root
    )
    state.script_identity_seeds = {
        (state.project_root / relative).resolve().as_posix(): value
        for relative, value in state.artifact_identity_seeds.items()
    }
    try:
        summary_text = _read_text(summary, state)
        phase = time.perf_counter()
        state.entries = _entries(summary_text, state)
        record_count = sum(
            len(evidence_file.records)
            for evidence_file in _unique_evidence_files(state.entries)
        )
        if record_count > MAX_RECORDS_PER_LOG:
            _fail(
                "association.resource.too_large",
                str(state.log_root),
                {"records": record_count, "limit": MAX_RECORDS_PER_LOG},
            )
        state.timings["evidence_file_parsing_seconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        state.invocations = _discover_invocations(state)
        _record_script_identities(state)
        state.timings["command_inspection_seconds"] = time.perf_counter() - phase
    except MechanicalContractError as error:
        state.checks.append(
            _error_check("conformance:log", CheckScope.CONFORMANCE, error)
        )
    else:
        _evaluate_entries(state)
        try:
            _evaluate_summary(summary_text, state)
        except MechanicalContractError as error:
            state.checks.append(
                _error_check(
                    "evidence:summary",
                    _error_scope(error, CheckScope.EVIDENCE),
                    error,
                )
            )
        _compose_graph(state)
    if not any(check.scope is CheckScope.CONFORMANCE for check in state.checks):
        state.checks.append(_pass_check("conformance:log", CheckScope.CONFORMANCE))
    checks, unchanged = _compare_checks(state.checks, request.prior_cache)
    cache = _cache_projection(
        checks,
        state.artifact_identities,
    )
    metrics = {
        "checks_unchanged": unchanged,
        "elapsed_seconds": time.perf_counter() - started,
        "graph_edges": len(state.graph.edges) if state.graph else 0,
        "graph_nodes": len(state.graph.nodes) if state.graph else 0,
        "invocations": len(state.invocations),
        "markdown_reads": state.markdown_reads,
        "script_hashes": len(state.script_cache),
        "artifact_identities": len(state.artifact_identities),
        "directory_observations": sum(
            observation.fingerprint.algorithm == "directory-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "identity_file_observations": sum(
            observation.fingerprint.algorithm == "identity-files-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "identity_pattern_observations": sum(
            observation.fingerprint.algorithm == "identity-patterns-sha256-v1"
            for observation in state.input_observations.values()
        ),
        "artifact_identity_seeds": len(state.artifact_identity_seeds),
        "source_evaluations": state.source_evaluations,
        "source_reads": len(state.source_cache),
        "source_hashes_reused": state.source_hashes_reused,
        "input_observations": len(state.input_observations),
        "input_fingerprints_reused": state.input_fingerprints_reused,
        **(
            state.fingerprint_cache.metrics.as_dict()
            if state.fingerprint_cache is not None
            else {}
        ),
        **state.timings,
        **(state.graph.metrics if state.graph else {}),
    }
    return {
        "cache": cache,
        "checks": checks,
        "summary": summary.as_posix(),
    }, metrics


def _evaluate(scan: Mapping[str, Any], date: str) -> MechanicalGeneratedRecord:
    checks = scan["checks"]
    assert isinstance(checks, tuple)
    return MechanicalGeneratedRecord.build(
        str(scan["summary"]), RULES_VERSION, date, checks
    )


# Entry surfaces and command discovery.


def _entries(summary_text: str, state: _ScanState) -> list[_Entry]:
    listed = _listed_entry_documents(summary_text, state)
    if not listed:
        _fail("association.declaration_missing", str(state.summary), {"entries": 0})
    _validate_surface_placement(listed, state)
    entries: list[_Entry] = []
    surfaces: dict[Path, _EntrySurface] = {}
    surface_errors: dict[Path, MechanicalContractError] = {}
    for document in listed:
        surface = _load_entry_surface(
            document, state, surfaces=surfaces, errors=surface_errors
        )
        if surface is None:
            continue
        root = document.parent.resolve()
        entries.append(
            _Entry(
                document.stem,
                document.resolve(),
                root,
                surface.evidence_file,
                surface.data_file,
                surface.retention_file,
                surface.evidence_failed,
            )
        )
    data_files = tuple(
        {
            entry.data_file.path: entry.data_file
            for entry in entries
            if entry.data_file
        }.values()
    )
    conflicts = find_log_consistency_conflicts(data_files)
    if not conflicts:
        return entries
    conflicted_targets = {conflict.canonical_target for conflict in conflicts}
    for conflict in conflicts:
        identity = hashlib.sha256(conflict.canonical_target.encode("utf-8")).hexdigest()
        state.checks.append(
            _error_check(
                f"conformance:data-conflict:{identity}",
                CheckScope.CONFORMANCE,
                conflict.error,
            )
        )
    return [_without_conflicted_inputs(entry, conflicted_targets) for entry in entries]


def _without_conflicted_inputs(entry: _Entry, targets: set[str]) -> _Entry:
    if entry.data_file is None:
        return entry
    inputs = tuple(
        item for item in entry.data_file.inputs if item.canonical_target not in targets
    )
    data_file = replace(entry.data_file, inputs=inputs) if inputs else None
    return replace(entry, data_file=data_file)


def _validate_surface_placement(documents: Sequence[Path], state: _ScanState) -> None:
    allowed_roots = {document.parent.resolve() for document in documents}
    entries_root = (state.log_root / "entries").resolve()
    surface_names = ("data.csv", "data.json", "retention.json")
    for name in surface_names:
        for invalid in (state.log_root / name, entries_root / name):
            if invalid.exists():
                _fail(
                    "data.file.location_invalid"
                    if name.startswith("data")
                    else "retention.file.location_invalid",
                    str(invalid),
                    {"reason": "parent_or_log_level_surface"},
                )
    if not entries_root.is_dir():
        return
    try:
        descendants = bounded_descendants(
            entries_root, maximum_entries=MAX_ENTRY_SURFACE_PATHS
        )
    except BoundedTraversalError as error:
        _fail(
            "association.resource.too_large"
            if error.reason == "entry_limit"
            else "association.document_unavailable",
            str(entries_root),
            {
                "error": error.detail,
                "limit": error.limit,
                "observed": error.observed,
                "reason": error.reason,
            },
        )
    for candidate in descendants:
        if (
            candidate.name in surface_names
            and candidate.parent.resolve() not in allowed_roots
        ):
            _fail(
                "data.file.location_invalid"
                if candidate.name.startswith("data")
                else "retention.file.location_invalid",
                str(candidate),
                {"reason": "unowned_entry_surface"},
            )


def _load_entry_surface(
    document: Path,
    state: _ScanState,
    *,
    surfaces: dict[Path, _EntrySurface],
    errors: dict[Path, MechanicalContractError],
) -> _EntrySurface | None:
    """Load one owned entry surface while preserving sibling evaluation."""

    lexical_root = document.parent.absolute()
    try:
        _validate_owned_entry(document, lexical_root, state)
        root = lexical_root.resolve()
        if root in errors:
            raise errors[root]
        if root not in surfaces:
            surfaces[root] = _read_entry_surface(document.stem, root, state)
    except MechanicalContractError as error:
        errors[lexical_root] = error
        state.checks.append(
            _error_check(
                f"entry:{document.stem}:declaration",
                _error_scope(error, CheckScope.PROVENANCE),
                error,
            )
        )
        return None
    return surfaces[lexical_root.resolve()]


def _validate_owned_entry(document: Path, root: Path, state: _ScanState) -> None:
    if not document.is_file() or ENTRY_ID_RE.fullmatch(document.stem) is None:
        _fail(
            "association.declaration_missing",
            str(document),
            {"entry": document.stem, "exists": document.is_file()},
        )
    try:
        root.relative_to((state.log_root / "entries").absolute())
        validate_entry_path_symlinks(document, state.log_root)
    except (ValueError, EntryMaterialPathError) as error:
        raise EngineV2Error(
            "evidence.declaration.invalid",
            "entry root",
            {
                "path": str(root),
                "reason": getattr(error, "reason", "outside_entries"),
                "root": str(state.log_root),
            },
            "V2 JSON File Schema",
        ) from error


def _read_entry_surface(entry_id: str, root: Path, state: _ScanState) -> _EntrySurface:
    evidence_path = root / "evidence.json"
    evidence_file: EvidenceFile | None = None
    evidence_failed = False
    if evidence_path.is_file():
        try:
            evidence_file = load_evidence_file(
                evidence_path, log_root=state.log_root, entry_root=root
            )
        except MechanicalContractError as error:
            evidence_failed = True
            _record_entry_surface_error(entry_id, "evidence", error, state)

    data_file = _read_entry_data(entry_id, root, state)

    retention_path = root / "retention.json"
    retention_file: RetentionFile | None = None
    if retention_path.is_file():
        try:
            retention_file = load_retention_file(retention_path, entry_root=root)
        except MechanicalContractError as error:
            _record_entry_surface_error(entry_id, "retention", error, state)
    return _EntrySurface(evidence_file, evidence_failed, data_file, retention_file)


def _read_entry_data(entry_id: str, root: Path, state: _ScanState) -> DataFile | None:
    data_path = root / "data.json"
    legacy_path = root / "data.csv"
    try:
        if data_path.exists() and legacy_path.exists():
            _fail(
                "data.file.location_invalid",
                str(root),
                {"files": [str(data_path), str(legacy_path)]},
            )
        if legacy_path.exists():
            _fail(
                "data.file.location_invalid",
                str(legacy_path),
                {"reason": "legacy_data_csv"},
            )
        data_file = (
            load_data_file(data_path, entry_root=root) if data_path.is_file() else None
        )
    except MechanicalContractError as error:
        _record_entry_surface_error(entry_id, "data", error, state)
        return None
    if data_file is None:
        return None
    valid_inputs: list[InputResource] = []
    for resource in data_file.inputs:
        try:
            _verify_input(resource, state)
        except MechanicalContractError as error:
            _record_entry_surface_error(
                entry_id, f"input:{resource.name}", error, state
            )
        else:
            valid_inputs.append(resource)
    return replace(data_file, inputs=tuple(valid_inputs))


def _record_entry_surface_error(
    entry_id: str, component: str, error: MechanicalContractError, state: _ScanState
) -> None:
    state.checks.append(
        _error_check(
            f"entry:{entry_id}:{component}-declaration",
            _error_scope(error, CheckScope.PROVENANCE),
            error,
        )
    )


def _verify_input(
    resource: InputResource, state: _ScanState
) -> FingerprintObservation | None:
    if resource.remote:
        return None
    key = resource.canonical_target
    observation = state.input_observations.get(key)
    if observation is not None:
        return validate_fingerprint_observation(resource, observation)
    observation = (
        state.fingerprint_cache.verify(resource)
        if state.fingerprint_cache is not None
        else verify_fingerprint(resource)
    )
    if observation is None:
        return None
    if observation.identity_reused:
        state.input_fingerprints_reused += 1
    state.input_observations[key] = observation
    return observation


def _listed_entry_documents(text: str, state: _ScanState) -> tuple[Path, ...]:
    result: list[Path] = []
    for target in index_entry_documents(text):
        path = (state.summary.parent / target).absolute()
        if path.suffix == ".md" and ENTRY_ID_RE.fullmatch(path.stem):
            result.append(path)
    return tuple(dict.fromkeys(result))


def _discover_invocations(state: _ScanState) -> tuple[Invocation, ...]:
    documents: list[tuple[Invocation, ...]] = []
    for entry in state.entries:
        try:
            document = entry.document
            text = _read_text(document, state)
            relative = document.relative_to(state.log_root).as_posix()
            context = CommandContext(
                log_id=state.log_root.as_posix(),
                entry=entry.id,
                document=relative,
                entry_root=entry.root,
                log_root=state.log_root,
                project_root=state.project_root,
                data_file=entry.data_file,
                input_fingerprint_verifier=lambda resource: _verify_input(
                    resource, state
                ),
                script_identity_cache=state.script_cache,
                script_identity_seeds=state.script_identity_seeds,
            )
            discovery = discover_commands(text, context)
            documents.append(discovery.invocations)
            for failure in discovery.failures:
                identity = f"entry:{entry.id}:command:{failure.fence}:{failure.ordinal}"
                if failure.error.code == "material.candidate.unresolved":
                    observed = failure.error.observed
                    candidates = (
                        observed.get("candidates", ())
                        if isinstance(observed, Mapping)
                        else ()
                    )
                    for candidate in candidates:
                        if isinstance(candidate, str) and candidate:
                            state.command_candidate_dependencies.setdefault(
                                candidate, set()
                            ).add(identity)
                    state.command_failure_owners.setdefault(
                        relative.rsplit("/", 1)[0], set()
                    ).add(identity)
                state.checks.append(
                    _error_check(
                        identity,
                        _error_scope(failure.error, CheckScope.PROVENANCE),
                        failure.error,
                    )
                )
        except MechanicalContractError as error:
            state.checks.append(
                _error_check(
                    f"entry:{entry.id}:command",
                    _error_scope(error, CheckScope.PROVENANCE),
                    error,
                )
            )
    return order_invocations(documents)


# Evidence, presentations, and provenance evaluation.


def _evaluate_entries(state: _ScanState) -> None:
    _record_unowned_evidence(state)
    for entry in state.entries:
        try:
            presentations, direct = _entry_presentations(entry, state)
            state.presentation_count += len(presentations)
            if state.presentation_count > MAX_PRESENTATIONS_PER_LOG:
                _fail(
                    "association.resource.too_large",
                    str(state.log_root),
                    {
                        "presentations": state.presentation_count,
                        "limit": MAX_PRESENTATIONS_PER_LOG,
                    },
                )
            evidence_file = _document_evidence_file(entry, state)
            if evidence_file is None:
                if presentations and not entry.evidence_failed:
                    _fail(
                        "association.declaration_missing",
                        str(entry.root / "evidence.json"),
                        {"markers": len(presentations)},
                    )
                associated: Mapping[str, PresentedItem] = {}
            else:
                associated = associate_presentations(evidence_file, presentations)
            records = (
                [
                    record
                    for record in evidence_file.records
                    if isinstance(record, PresentationRecord)
                ]
                if evidence_file is not None
                else []
            )
            for record in records:
                if record.id not in associated:
                    continue
                outcome = _evaluate_record(entry, record, associated[record.id], state)
                state.records.append(outcome)
                state.checks.extend((outcome.evidence_check, outcome.provenance_check))
            _evaluate_direct_artifacts(entry, direct, state)
        except MechanicalContractError as error:
            identity = f"entry:{entry.id}:association"
            scope = _error_scope(error, CheckScope.EVIDENCE)
            state.checks.append(_error_check(identity, scope, error))


def _entry_presentations(
    entry: _Entry, state: _ScanState
) -> tuple[tuple[PresentedItem, ...], tuple[DirectArtifactPresentation, ...]]:
    presented: list[PresentedItem] = []
    direct: list[DirectArtifactPresentation] = []
    document = entry.document
    text = _read_text(document, state)
    relative = document.relative_to(state.log_root).as_posix()
    for issue in index_entry_section_issues(text):
        state.checks.append(
            _failure_check(
                f"entry:{entry.id}:section:{issue.line}",
                CheckScope.CONFORMANCE,
                _FailureSpec(
                    "association.context_invalid",
                    f"{relative}:{issue.line}",
                    {
                        "classifier_version": SECTION_CLASSIFIER_VERSION,
                        "heading": issue.heading,
                        "labels": list(issue.labels),
                        "reason": issue.reason,
                    },
                    "Eligible Presentation Context",
                ),
            )
        )
    indexed = index_entry_presentations(text, document=relative)
    _require_complete_markers(document, indexed, text, state)
    presented.extend(indexed)
    direct.extend(index_direct_artifacts(text, document=relative))
    return tuple(presented), tuple(direct)


def _document_evidence_file(entry: _Entry, state: _ScanState) -> EvidenceFile | None:
    evidence_file = entry.evidence_file
    if evidence_file is None:
        return None
    document = entry.document.relative_to(state.log_root).as_posix()
    records = tuple(
        record
        for record in evidence_file.records
        if not isinstance(record, PresentationRecord) or record.document == document
    )
    return EvidenceFile(evidence_file.path, evidence_file.entry_root, records)


def _unique_evidence_files(entries: Sequence[_Entry]) -> tuple[EvidenceFile, ...]:
    files: dict[Path, EvidenceFile] = {}
    for entry in entries:
        if entry.evidence_file is not None:
            files.setdefault(entry.evidence_file.path, entry.evidence_file)
    return tuple(files.values())


def _record_unowned_evidence(state: _ScanState) -> None:
    listed_by_file: dict[Path, set[str]] = {}
    for entry in state.entries:
        if entry.evidence_file is None:
            continue
        listed_by_file.setdefault(entry.evidence_file.path, set()).add(
            entry.document.relative_to(state.log_root).as_posix()
        )
    for evidence_file in _unique_evidence_files(state.entries):
        listed = listed_by_file[evidence_file.path]
        missing = [
            record
            for record in evidence_file.records
            if isinstance(record, PresentationRecord) and record.document not in listed
        ]
        if not missing:
            continue
        state.checks.append(
            _failure_check(
                f"evidence:{evidence_file.path}:document-ownership",
                CheckScope.EVIDENCE,
                _FailureSpec(
                    "association.presentation_missing",
                    str(evidence_file.path),
                    {
                        "documents": sorted({record.document for record in missing}),
                        "ids": sorted(record.id for record in missing),
                    },
                    "Association Completeness And Conflict Rules",
                ),
            )
        )


def _material_owner(entry: _Entry, state: _ScanState) -> str:
    return entry.root.relative_to(state.log_root).as_posix()


def _require_complete_markers(
    document: Path,
    indexed: Sequence[PresentedItem],
    text: str,
    state: _ScanState,
) -> None:
    expected = Counter(
        (item.kind, item.line) for item in index_entry_presentation_candidates(text)
    )
    observed = Counter((item.kind, item.line) for item in indexed)
    missing = expected - observed
    if missing:
        kind, line = sorted(missing.elements())[0]
        _fail(
            "association.declaration_missing",
            f"{document}:{line}",
            {"kind": kind, "missing": missing[(kind, line)]},
        )


def _evaluate_record(
    entry: _Entry,
    record: PresentationRecord,
    item: PresentedItem,
    state: _ScanState,
) -> _RecordOutcome:
    identity = f"evidence:{entry.id}:{record.id}"
    try:
        materials = tuple(
            _resolve_source(source, entry, state) for source in record.sources
        )
    except MechanicalContractError as error:
        evidence = _error_check(
            identity, _error_scope(error, CheckScope.EVIDENCE), error
        )
        provenance = _dependent_check(
            f"provenance:{entry.id}:{record.id}",
            CheckScope.PROVENANCE,
            identity,
        )
        return _RecordOutcome(
            entry.id, record, item, (), evidence, provenance, None, ()
        )
    provenance = _record_provenance(entry, record, materials, state)
    try:
        selections = [
            _selection(source, resolved.path, state)
            for source, resolved in zip(record.sources, materials)
        ]
        transformed = _transform_and_compare(record, selections, item, state)
        evidence = _pass_check(
            identity,
            CheckScope.EVIDENCE,
            dependencies=_record_dependencies(record, selections, transformed, item),
        )
        canonical = _canonical_presentation(transformed, item)
        dependencies = tuple(
            [selection.dependency_projection for selection in selections]
            + [transformed.dependency_projection]
        )
    except MechanicalContractError as error:
        scope = _error_scope(error, CheckScope.EVIDENCE)
        evidence = _error_check(identity, scope, error)
        canonical = None
        dependencies = ()
    return _RecordOutcome(
        entry.id,
        record,
        item,
        materials,
        evidence,
        provenance,
        canonical,
        dependencies,
    )


def _transform_and_compare(
    record: PresentationRecord,
    selections: Sequence[Any],
    item: PresentedItem,
    state: _ScanState,
) -> TransformationResult:
    started = time.perf_counter()
    try:
        transformed = evaluate_transformation(
            record.transformation, selections, presentation_kind=record.kind
        )
        compare_presentation(
            transformed, presented_kind=item.kind, presented=item.value
        )
        return transformed
    finally:
        state.timings["presentation_comparison_seconds"] = state.timings.get(
            "presentation_comparison_seconds", 0.0
        ) + (time.perf_counter() - started)


def _selection(source: EvidenceSource, path: Path, state: _ScanState) -> Any:
    source_key = path.resolve().as_posix()
    key = (source_key, canonical_json(source.locator))
    if key not in state.selection_cache:
        started = time.perf_counter()
        observation = state.source_cache.get(source_key)
        if observation is None:
            relative = _project_relative(path, state.project_root)
            trusted = state.artifact_identity_seeds.get(relative) if relative else None
            observation = observe_source(path, trusted_identity=trusted)
            state.source_cache[source_key] = observation
            if observation.identity_reused:
                state.source_hashes_reused += 1
            if relative is not None:
                state.artifact_identities[relative] = _source_artifact_identity(
                    observation
                )
        state.selection_cache[key] = evaluate_observed_locator(
            observation, source.locator
        )
        state.timings["source_evaluation_seconds"] = state.timings.get(
            "source_evaluation_seconds", 0.0
        ) + (time.perf_counter() - started)
        state.source_evaluations += 1
    return state.selection_cache[key]


def _record_provenance(
    entry: _Entry,
    record: PresentationRecord,
    materials: Sequence[_ResolvedSource],
    state: _ScanState,
) -> MechanicalCheck:
    identity = f"provenance:{entry.id}:{record.id}"
    artifact_dependency = {
        "artifacts": sorted(material.path.as_posix() for material in materials)
    }
    try:
        dependencies: list[Mapping[str, object]] = [artifact_dependency]
        for material in materials:
            if material.external:
                dependencies.append(
                    {"kind": "external", "material": material.path.as_posix()}
                )
                continue
            result = evaluate_provenance(material.path, state.invocations)
            dependencies.append(
                {
                    "dependency_projection": result.dependency_projection,
                    "material": result.material,
                }
            )
        return _pass_check(identity, CheckScope.PROVENANCE, dependencies=dependencies)
    except MechanicalContractError as error:
        blockers = _command_blockers(error.subject, state)
        if error.code in {"producer.missing", "lineage.missing"} and blockers:
            return _blocked_check(
                identity,
                CheckScope.PROVENANCE,
                error.subject,
                blockers,
                dependencies=(artifact_dependency,),
            )
        return _error_check(
            identity,
            CheckScope.PROVENANCE,
            error,
            dependencies=(artifact_dependency,),
        )


def _evaluate_direct_artifacts(
    entry: _Entry,
    artifacts: Sequence[DirectArtifactPresentation],
    state: _ScanState,
) -> None:
    for artifact in artifacts:
        identity = f"artifact:{artifact.document}:{artifact.line}"
        target = state.log_root / artifact.normalized_target
        state.direct.append(
            DirectArtifactConnection(entry.id, identity, target.resolve().as_posix())
        )
        try:
            result = evaluate_provenance(target, state.invocations)
            state.checks.append(
                _pass_check(
                    f"provenance:{identity}",
                    CheckScope.PROVENANCE,
                    dependencies=(
                        {"artifacts": [target.resolve().as_posix()]},
                        {"dependency_projection": result.dependency_projection},
                    ),
                )
            )
        except MechanicalContractError as error:
            blockers = _command_blockers(error.subject, state)
            if error.code in {"producer.missing", "lineage.missing"} and blockers:
                state.checks.append(
                    _blocked_check(
                        f"provenance:{identity}",
                        CheckScope.PROVENANCE,
                        error.subject,
                        blockers,
                        dependencies=({"artifacts": [target.resolve().as_posix()]},),
                    )
                )
                continue
            state.checks.append(
                _error_check(
                    f"provenance:{identity}",
                    CheckScope.PROVENANCE,
                    error,
                    dependencies=({"artifacts": [target.resolve().as_posix()]},),
                )
            )


def _evaluate_summary(text: str, state: _ScanState) -> None:
    references = index_summary_references(text)
    if len(references) > MAX_SUMMARY_REFERENCES_PER_LOG:
        _fail(
            "association.resource.too_large",
            str(state.summary),
            {
                "summary_references": len(references),
                "limit": MAX_SUMMARY_REFERENCES_PER_LOG,
            },
        )
    _require_complete_summary_references(state.summary, text, references, state)
    outcomes = {(item.entry, item.record.id): item for item in state.records}
    targets = {
        identity: outcome.canonical
        for identity, outcome in outcomes.items()
        if outcome.canonical is not None
    }
    for reference in references:
        identity = f"summary:{reference.line}"
        target_identity = (reference.entry, reference.evidence_id)
        outcome = outcomes.get(target_identity)
        if outcome is None or outcome.canonical is None:
            state.checks.append(
                _summary_target_failure(identity, target_identity, outcome)
            )
        else:
            try:
                resolve_summary_references((reference,), targets)
                state.checks.append(
                    _pass_check(
                        f"evidence:{identity}",
                        CheckScope.EVIDENCE,
                        dependencies=(
                            {"target": f"{reference.entry}:{reference.evidence_id}"},
                        ),
                    )
                )
            except MechanicalContractError as error:
                state.checks.append(
                    _error_check(f"evidence:{identity}", CheckScope.EVIDENCE, error)
                )
        state.checks.append(_summary_provenance(identity, target_identity, outcome))


def _require_complete_summary_references(
    summary: Path,
    text: str,
    references: Sequence[SummaryReference],
    state: _ScanState,
) -> None:
    expected = Counter(index_summary_statistic_candidates(text))
    observed = Counter(reference.line for reference in references)
    missing = expected - observed
    if missing:
        line = min(missing)
        _fail(
            "summary.reference.missing",
            f"{summary}:{line}",
            {"missing": missing[line]},
        )


# Material graph composition and artifact-level orphan grouping.


def _compose_graph(state: _ScanState) -> None:
    connections = tuple(
        EvidenceConnection(
            outcome.entry,
            outcome.record.id,
            f"{outcome.item.document}:{outcome.item.id}",
            tuple(material.path.as_posix() for material in outcome.materials),
            outcome.dependencies,
            frozenset(
                material.path.as_posix()
                for material in outcome.materials
                if material.external
            ),
        )
        for outcome in state.records
        if outcome.materials
    )
    request = MaterialGraphRequest(
        entry_roots={entry.id: entry.root for entry in state.entries},
        evidence=connections,
        direct_artifacts=tuple(state.direct),
        invocations=state.invocations,
        retention_files=_unique_retention_files(state.entries),
        input_registries=_input_registry_surfaces(state),
    )
    try:
        state.graph = compose_material_graph(request)
    except MechanicalContractError as error:
        state.checks.append(
            _error_check("graph:log", _error_scope(error, CheckScope.PROVENANCE), error)
        )
        return
    orphan = state.graph.orphan
    orphan_groups = _orphan_group_metadata(state, orphan.inventory, orphan.orphaned)
    for path in orphan.orphaned:
        material_blockers = _command_blockers(path, state)
        if material_blockers:
            state.checks.append(
                _blocked_check(
                    f"orphan:material:{path}",
                    CheckScope.ORPHAN,
                    path,
                    material_blockers,
                    dependencies=({"artifacts": [path]},),
                )
            )
            continue
        state.checks.append(
            _failure_check(
                f"orphan:material:{path}",
                CheckScope.ORPHAN,
                _FailureSpec(
                    "orphan.material.unused",
                    path,
                    {
                        "classification": "orphaned",
                        **orphan_groups[path],
                    },
                    "Orphan Detection",
                ),
            )
        )
    for name in orphan.unused_input_names:
        owner = name.rsplit(":", 1)[0]
        input_blockers = tuple(sorted(state.command_failure_owners.get(owner, set())))
        if input_blockers:
            state.checks.append(
                _blocked_check(
                    f"orphan:data-name:{name}",
                    CheckScope.ORPHAN,
                    name,
                    input_blockers,
                )
            )
            continue
        state.checks.append(
            _failure_check(
                f"orphan:data-name:{name}",
                CheckScope.ORPHAN,
                _FailureSpec(
                    "orphan.input.unused",
                    name,
                    {"classification": "unused"},
                    "Orphan Detection",
                ),
            )
        )
    if not orphan.orphaned and not orphan.unused_input_names:
        state.checks.append(
            _pass_check(
                "orphan:log",
                CheckScope.ORPHAN,
                dependencies=({"dependency_projection": orphan.dependency_projection},),
            )
        )


def _orphan_group_metadata(
    state: _ScanState,
    inventory: Sequence[str],
    orphaned: Sequence[str],
) -> dict[str, Mapping[str, object]]:
    inventory_by_owner: dict[str, dict[str, str]] = defaultdict(dict)
    orphan_by_owner: dict[str, dict[str, str]] = defaultdict(dict)
    for path in inventory:
        logical = _logical_entry_material(path, state)
        if logical is not None:
            owner, relative = logical
            inventory_by_owner[owner][relative] = path
    for path in orphaned:
        logical = _logical_entry_material(path, state)
        if logical is not None:
            owner, relative = logical
            orphan_by_owner[owner][relative] = path
    result: dict[str, Mapping[str, object]] = {}
    for owner, orphan_paths in orphan_by_owner.items():
        result.update(
            _owner_orphan_groups(
                state.log_root.as_posix(),
                owner,
                set(inventory_by_owner[owner]),
                orphan_paths,
            )
        )
    return result


def _owner_orphan_groups(
    log_identity: str,
    owner: str,
    inventory: set[str],
    orphan_paths: Mapping[str, str],
) -> dict[str, Mapping[str, object]]:
    state = _OrphanGroupingState(
        log_identity, owner, inventory, orphan_paths, set(), {}
    )
    top_directories = sorted(
        {PurePosixPath(path).parts[0] for path in inventory if "/" in path}
    )
    for directory in top_directories:
        _collapse_orphan_directory(directory, state)
    for relative, path in orphan_paths.items():
        if relative not in state.grouped:
            state.result[path] = {
                "artifact_count": 1,
                "directory": None,
                "group_identity": None,
                "owner": owner,
                "relative": relative,
            }
    return state.result


def _collapse_orphan_directory(
    directory: str,
    state: _OrphanGroupingState,
) -> None:
    prefix = directory + "/"
    eligible = {path for path in state.inventory if path.startswith(prefix)}
    if eligible and eligible <= set(state.orphan_paths):
        identity = (
            "orphan-group:"
            + hashlib.sha256(
                canonical_json([state.log_identity, state.owner, directory]).encode()
            ).hexdigest()
        )
        for relative in eligible:
            path = state.orphan_paths[relative]
            state.result[path] = {
                "artifact_count": len(eligible),
                "directory": directory,
                "group_identity": identity,
                "owner": state.owner,
                "relative": relative,
            }
            state.grouped.add(relative)
        return
    depth = len(PurePosixPath(directory).parts)
    children = sorted(
        {
            PurePosixPath(path).parts[depth]
            for path in eligible
            if len(PurePosixPath(path).parts) > depth + 1
        }
    )
    for child in children:
        _collapse_orphan_directory(f"{directory}/{child}", state)


def _logical_entry_material(material: str, state: _ScanState) -> tuple[str, str] | None:
    path = Path(material).resolve()
    seen: set[Path] = set()
    for entry in state.entries:
        if entry.root in seen:
            continue
        seen.add(entry.root)
        owner = _material_owner(entry, state)
        try:
            return owner, path.relative_to(entry.root.resolve()).as_posix()
        except ValueError:
            pass
        for name in ("data", "images"):
            logical_root = entry.root / name
            if not logical_root.is_symlink():
                continue
            try:
                relative = path.relative_to(logical_root.resolve()).as_posix()
            except ValueError:
                continue
            return owner, f"{name}/{relative}"
    return None


def _input_registry_surfaces(state: _ScanState) -> tuple[InputRegistrySurface, ...]:
    surfaces: dict[Path, InputRegistrySurface] = {}
    for entry in state.entries:
        if entry.data_file is not None:
            surfaces.setdefault(
                entry.data_file.path,
                InputRegistrySurface(_material_owner(entry, state), entry.data_file),
            )
    return tuple(surfaces.values())


def _unique_retention_files(entries: Sequence[_Entry]) -> tuple[RetentionFile, ...]:
    files: dict[Path, RetentionFile] = {}
    for entry in entries:
        if entry.retention_file is not None:
            files.setdefault(entry.retention_file.path, entry.retention_file)
    return tuple(files.values())


def _resolve_source(
    source: EvidenceSource, entry: _Entry, state: _ScanState
) -> _ResolvedSource:
    value = source.source
    exact_name = re.match(r"<([A-Za-z0-9][A-Za-z0-9_-]*)>", value)
    external = False
    if exact_name is not None and exact_name.group(1) not in {"log", "project"}:
        try:
            resolved = resolve_input_token(value, entry.data_file)
        except DataContractError as error:
            _fail(error.code, value, error.observed)
        resource = resolved.resource
        location = resolved.path
        if resource.remote:
            _fail(
                "locator.reader.unavailable",
                value,
                {"location": location, "retained_observation": False},
            )
        external = resource.external is not None
        path = Path(location)
    elif value.startswith("<log>/"):
        path = state.log_root / value.removeprefix("<log>/")
    elif value.startswith("<project>/"):
        path = state.project_root / value.removeprefix("<project>/")
        external = not _within(path, state.log_root)
    else:
        pure = PurePosixPath(value)
        if (
            not value
            or pure.is_absolute()
            or "\\" in value
            or "://" in value
            or any(part in {"", ".", ".."} for part in pure.parts)
            or "<" in value
            or ">" in value
        ):
            _fail("locator.path.unresolved", value, {"source": value})
        path = entry.root.joinpath(*pure.parts)
    _validate_entry_source_path(path, entry, value)
    if path.is_symlink() or not path.is_file():
        _fail(
            "locator.path.unresolved",
            value,
            {"path": path.resolve().as_posix(), "regular_file": False},
        )
    return _ResolvedSource(path.resolve(), external)


def _validate_entry_source_path(path: Path, entry: _Entry, source: str) -> None:
    try:
        validate_local_path_symlinks(path, entry.root)
    except EntryMaterialPathError as error:
        _fail(
            "locator.path.unresolved",
            source,
            {"path": path.as_posix(), "reason": error.reason},
        )


def _canonical_presentation(
    transformed: TransformationResult, item: PresentedItem
) -> CanonicalPresentation:
    if transformed.kind == "statistic":
        return CanonicalPresentation(kind="statistic", statistic=item.value)
    if transformed.kind == "table":
        return CanonicalPresentation(
            kind="table",
            table=transformed.rows,
            numerical_cells=transformed.numerical_cells,
        )
    return CanonicalPresentation(kind="output")


def _record_dependencies(
    record: PresentationRecord,
    selections: Sequence[Any],
    transformed: TransformationResult,
    item: PresentedItem,
) -> tuple[Mapping[str, object], ...]:
    return (
        {"record": canonical_json(record.as_dict())},
        {
            "presentation": {
                "document": item.document,
                "id": item.id,
                "value": item.value,
            }
        },
        {
            "context": {
                "classification": item.section_classification,
                "classifier_version": SECTION_CLASSIFIER_VERSION,
                "under_results": item.under_results,
            }
        },
        {"selections": [selection.dependency_projection for selection in selections]},
        {"transformation": transformed.dependency_projection},
    )


def _summary_target_failure(
    identity: str,
    target: tuple[str, str],
    outcome: _RecordOutcome | None,
) -> MechanicalCheck:
    dependency = outcome.evidence_check.identity if outcome is not None else None
    status = (
        CheckStatus.UNAVAILABLE
        if outcome is not None
        and outcome.evidence_check.status is CheckStatus.UNAVAILABLE
        else CheckStatus.FAIL
    )
    return _failure_check(
        f"evidence:{identity}",
        CheckScope.EVIDENCE,
        _FailureSpec(
            "summary.reference.target_invalid",
            identity,
            {"entry": target[0], "eid": target[1]},
            "Summary Association",
            dependency,
            status,
        ),
    )


def _summary_provenance(
    identity: str,
    target: tuple[str, str],
    outcome: _RecordOutcome | None,
) -> MechanicalCheck:
    check_identity = f"provenance:{identity}"
    if outcome is None:
        return _dependent_check(
            check_identity, CheckScope.PROVENANCE, f"{target[0]}:{target[1]}"
        )
    target_check = outcome.provenance_check
    if target_check.status is CheckStatus.PASS:
        return _pass_check(
            check_identity,
            CheckScope.PROVENANCE,
            dependencies=({"target": target_check.identity},),
        )
    if target_check.status is CheckStatus.UNAVAILABLE:
        return _failure_check(
            check_identity,
            CheckScope.PROVENANCE,
            _FailureSpec(
                "summary.reference.target_invalid",
                identity,
                {"target_status": target_check.status.value},
                "Summary Association",
                target_check.identity,
                CheckStatus.UNAVAILABLE,
            ),
        )
    if target_check.status is CheckStatus.FAIL:
        return _failure_check(
            check_identity,
            CheckScope.PROVENANCE,
            _FailureSpec(
                "summary.reference.target_invalid",
                identity,
                {"target_status": target_check.status.value},
                "Summary Association",
                target_check.identity,
            ),
        )
    return _dependent_check(
        check_identity,
        CheckScope.PROVENANCE,
        target_check.identity,
    )


def _pass_check(
    identity: str,
    scope: CheckScope,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
) -> MechanicalCheck:
    return MechanicalCheck(
        identity, scope, CheckStatus.PASS, identity, tuple(dependencies)
    )


def _dependent_check(
    identity: str, scope: CheckScope, dependency: str
) -> MechanicalCheck:
    return MechanicalCheck(
        identity,
        scope,
        CheckStatus.NOT_APPLICABLE,
        identity,
        ({"dependency": dependency},),
    )


def _blocked_check(
    identity: str,
    scope: CheckScope,
    subject: str,
    blockers: Sequence[str],
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
) -> MechanicalCheck:
    return MechanicalCheck(
        identity,
        scope,
        CheckStatus.NOT_APPLICABLE,
        subject,
        tuple(dependencies)
        + tuple({"dependency": dependency} for dependency in sorted(blockers)),
    )


def _command_blockers(subject: str, state: _ScanState) -> tuple[str, ...]:
    if "://" in subject:
        return ()
    path = Path(subject)
    if not path.is_absolute():
        return ()
    resolved = path.resolve()
    blockers: set[str] = set()
    for candidate, identities in state.command_candidate_dependencies.items():
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute():
            continue
        candidate_path = candidate_path.resolve()
        if resolved == candidate_path or (
            candidate_path.is_dir() and _within(resolved, candidate_path)
        ):
            blockers.update(identities)
    return tuple(sorted(blockers))


def _error_check(
    identity: str,
    scope: CheckScope,
    error: MechanicalContractError,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
) -> MechanicalCheck:
    code = error.code
    subject = error.subject
    observed = error.observed
    rule = error.rule
    status = (
        CheckStatus.UNAVAILABLE
        if error.outcome == "unavailable"
        or code == "provenance.observation.unavailable"
        else CheckStatus.FAIL
    )
    return _failure_check(
        identity,
        scope,
        _FailureSpec(
            code,
            subject,
            observed if isinstance(observed, Mapping) else {"value": observed},
            rule,
            status=status,
        ),
        dependencies=dependencies,
    )


def _failure_check(
    identity: str,
    scope: CheckScope,
    failure: _FailureSpec,
    *,
    dependencies: Sequence[Mapping[str, object]] = (),
) -> MechanicalCheck:
    return MechanicalCheck(
        identity,
        scope,
        failure.status,
        failure.subject,
        tuple(dependencies),
        failure=FailurePayload(
            failure.code,
            failure.subject,
            failure.observed,
            failure.rule,
            failure.dependency,
        ),
    )


def _error_scope(error: MechanicalContractError, default: CheckScope) -> CheckScope:
    code = error.code
    conformance_prefixes = (
        "data.declaration.",
        "data.file.",
        "data.name.",
        "data.target.duplicate",
        "evidence.file.",
        "evidence.json.",
        "evidence.record.",
        "invocation.annotation.",
        "invocation.command.",
        "invocation.path_value.",
        "locator.condition.",
        "locator.literal.",
        "locator.property.",
        "locator.resource.",
        "locator.syntax.",
        "locator.version.",
        "presentation.marker.",
        "transformation.input.",
        "transformation.nonfinite_",
        "transformation.output.",
        "transformation.render.",
        "transformation.scale.",
        "transformation.syntax.",
    )
    conformance_codes = {
        "association.context_invalid",
        "association.presentation.syntax_invalid",
        "association.resource.too_large",
        "data.input.token_missing",
        "data.remote.identity_invalid",
        "evidence.declaration.invalid",
        "material.candidate.unresolved",
        "provenance.resource.too_large",
        "retention.declaration.invalid",
        "retention.file.location_invalid",
        "retention.target.missing",
        "summary.reference.invalid",
    }
    return (
        CheckScope.CONFORMANCE
        if (
            code in conformance_codes
            or any(code.startswith(prefix) for prefix in conformance_prefixes)
        )
        else default
    )


def _read_text(path: Path, state: _ScanState) -> str:
    path = path.resolve()
    cached = state.text_cache.get(path)
    if cached is not None:
        return cached
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail("association.document_unavailable", str(path), {"error": str(error)})
    state.markdown_reads += 1
    state.text_cache[path] = text
    return text


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


# Cache validation, identity seeding, and exact check comparison.


def _compare_checks(
    checks: Sequence[MechanicalCheck], prior: Mapping[str, Any] | None
) -> tuple[tuple[MechanicalCheck, ...], int]:
    if (
        not isinstance(prior, Mapping)
        or not cache_envelope_supported(prior)
        or prior.get("rules_version") != RULES_VERSION
    ):
        return tuple(checks), 0
    prior_checks = prior.get("checks")
    assert isinstance(prior_checks, Mapping)
    result: list[MechanicalCheck] = []
    unchanged = 0
    for check in checks:
        cached = prior_checks.get(check.identity)
        dependency = _check_dependency(check)
        if (
            check.status is CheckStatus.PASS
            and check.dependencies
            and isinstance(cached, Mapping)
            and set(cached) == {"check", "dependency_projection"}
            and cached.get("dependency_projection") == dependency
        ):
            try:
                previous = MechanicalCheck.from_dict(cached.get("check"))
            except (TypeError, ValueError):
                pass
            else:
                if previous == check and previous.status is CheckStatus.PASS:
                    unchanged += 1
        result.append(check)
    return tuple(result), unchanged


def _cache_projection(
    checks: Sequence[MechanicalCheck],
    artifact_identities: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    return {
        "artifact_identities": dict(sorted(artifact_identities.items())),
        "checks": {
            check.identity: {
                "check": check.as_dict(),
                "dependency_projection": _check_dependency(check),
            }
            for check in checks
            if check.status is CheckStatus.PASS and check.dependencies
        },
        "rules_version": RULES_VERSION,
        "schema": CACHE_SCHEMA,
    }


def _accepted_artifact_identities(
    prior: Mapping[str, Any] | None, project_root: Path
) -> dict[str, Mapping[str, object]]:
    if not isinstance(prior, Mapping) or not cache_envelope_supported(prior):
        return {}
    candidates = prior.get("artifact_identities")
    if not isinstance(candidates, Mapping):
        return {}
    accepted: dict[str, Mapping[str, object]] = {}
    for relative, value in candidates.items():
        if not isinstance(relative, str) or not isinstance(value, Mapping):
            continue
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            continue
        target = project_root.joinpath(*path.parts)
        if (
            target.is_symlink()
            or not target.is_file()
            or not _within(target, project_root)
        ):
            continue
        digest = value.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            continue
        try:
            observed = _artifact_identity(target, digest)
        except OSError:
            continue
        if observed == value:
            accepted[relative] = dict(value)
    return accepted


def cache_envelope_supported(value: object) -> bool:
    """Return whether a decoded value has the complete cache envelope."""

    return (
        isinstance(value, Mapping)
        and set(value) == CACHE_FIELDS
        and value.get("schema") == CACHE_SCHEMA
        and isinstance(value.get("rules_version"), str)
        and isinstance(value.get("artifact_identities"), Mapping)
        and isinstance(value.get("checks"), Mapping)
    )


def _artifact_identity(path: Path, digest: object) -> Mapping[str, object]:
    stat = path.stat()
    return {
        "ctime_ns": stat.st_ctime_ns,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
        "size": stat.st_size,
    }


def _source_artifact_identity(
    observation: SourceObservation,
) -> Mapping[str, object]:
    _device, _inode, size, mtime_ns, ctime_ns = observation.file_observation
    return {
        "ctime_ns": ctime_ns,
        "mtime_ns": mtime_ns,
        "sha256": observation.source_identity.removeprefix("sha256:"),
        "size": size,
    }


def _project_relative(path: Path, project_root: Path) -> str | None:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None


def _record_script_identities(state: _ScanState) -> None:
    for absolute, observation in state.script_cache.items():
        path = Path(absolute)
        relative = _project_relative(path, state.project_root)
        if relative is not None:
            state.artifact_identities[relative] = observation.as_cache_record()


def _check_dependency(check: MechanicalCheck) -> str:
    payload = {
        "dependencies": [dict(item) for item in check.dependencies],
        "identity": check.identity,
        "rules_version": RULES_VERSION,
        "scope": check.scope.value,
        "subject": check.subject,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise EngineV2Error(
        code, subject, observed, "Mechanical Validation Evaluation And Outcomes"
    )
