"""One bounded authored-registry transaction for explicit Reorganize transfers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from research_log_data import (
    DataFile,
    Fingerprint,
    FingerprintObservation,
    InputResource,
    data_file_from_inputs,
    input_token_parts,
    load_data_file,
    observe_file_content,
    observe_fingerprint,
    resolve_input_token,
    validate_log_consistency,
)
from validation.commands import command_input_names
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    EvidenceSource,
    decode_evidence_file_records,
    evidence_file_from_records,
    index_summary_references,
    load_evidence_file,
    validate_evidence_record_context,
)
from validation.locator import evaluate_locator
from validation.operation_state import begin_reorganization, finish_guarded_publication
from validation.presentation import (
    find_entry_presentation,
    index_entry_presentations_all,
    require_artifact_baseline_form,
    require_artifact_fingerprint,
    require_artifact_source_association,
)
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PYRUN_FILENAME,
    PyrunCommand,
    PyrunFile,
    load_pyrun_state,
)
from validation.retention import (
    RetentionFile,
    RetentionRecord,
    decode_retention_file_records,
    load_retention_file,
    retention_file_from_records,
    validate_retention_record_context,
)
from validation.transformation import compare_presentation, evaluate_transformation

from .authoring_transactions import data_publication_transaction
from .context import EntryContext, resolve_project_root
from .model import ActionError, ActionResult, TransferArguments
from .scaffold import observe_physical_entries
from .storage import PublicationError, atomic_write_texts


@dataclass(frozen=True)
class _SourceState:
    data: DataFile | None
    evidence: tuple[EvidenceRecord, ...]
    retention: tuple[RetentionRecord, ...]


@dataclass(frozen=True)
class _TransferPlan:
    selections: Mapping[str, frozenset[str]]
    maps: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class _SupportUpdate:
    """One execution-state replacement within the registry transaction."""

    path: Path
    text: str | None


@dataclass
class _MaterialObservations:
    """Operation-local observations of only transfer-affected material."""

    resources: dict[InputResource, FingerprintObservation]
    artifacts: dict[Path, Fingerprint]

    def observe_resource(self, resource: InputResource) -> FingerprintObservation:
        observation = self.resources.get(resource)
        if observation is None:
            observation = observe_fingerprint(resource)
            self.resources[resource] = observation
        return observation

    def observe_artifact(self, path: Path) -> Fingerprint:
        target = path.resolve()
        observation = self.artifacts.get(target)
        if observation is None:
            digest, _ = observe_file_content(target)
            observation = Fingerprint("sha256", digest)
            self.artifacts[target] = observation
        return observation


def transfer_registries(
    source: EntryContext,
    destination: EntryContext,
    arguments: TransferArguments,
) -> ActionResult:
    """Validate and publish one selected source/destination registry transaction."""

    _require_selection(arguments, source == destination)
    maps = _validated_maps(arguments)
    state = _SourceState(
        _load_data(source),
        _evidence_records(source),
        _retention_records(source),
    )
    selections = _selections(arguments, state.data, state.evidence, state.retention)
    _validate_unselected_source_context(source, state, selections)
    _require_mapping_sources(
        maps, selections, state.data, state.evidence, state.retention
    )
    plan = _TransferPlan(selections, maps)
    observations = _MaterialObservations({}, {})
    candidates = _build_candidates(source, destination, state, plan, observations)
    _require_source_detached(source, candidates, plan)
    _verify_markdown(source, destination, candidates.moved_evidence, state, plan)
    _validate_log_data(
        source, destination, candidates.source_data, candidates.destination_data
    )
    support_update, reruns = _retired_support(source, destination, state, plan)
    updates = candidates.updates(source, destination)
    for name, family in (
        ("data.json", "data"),
        ("evidence.json", "evidence"),
        ("retention.json", "retention"),
    ):
        if not selections[family]:
            updates.pop(source.root / name, None)
            updates.pop(destination.root / name, None)
    if support_update is not None:
        updates[support_update.path] = support_update.text
    changed = any(
        value is None or not path.exists() or path.read_text(encoding="utf-8") != value
        for path, value in updates.items()
    )
    if not changed:
        return _result("unchanged", False, (), reruns)
    if arguments.dry_run:
        return _result("dry-run", True, tuple(updates), reruns)
    with data_publication_transaction(
        (source, destination) if source != destination else (source,), updates
    ):
        residue = begin_reorganization(source.log.root)
        try:
            atomic_write_texts(updates)
        except PublicationError as error:
            if error.rollback_complete:
                finish_guarded_publication(residue)
            raise ActionError("reorganize.transfer.failed", str(error)) from error
        finish_guarded_publication(residue)
    return _result("changed", True, tuple(updates), reruns)


@dataclass(frozen=True)
class _Candidates:
    source_data: DataFile | None
    destination_data: DataFile | None
    source_evidence: EvidenceFile | None
    destination_evidence: EvidenceFile | None
    source_retention: RetentionFile | None
    destination_retention: RetentionFile | None
    moved_evidence: tuple[EvidenceRecord, ...]

    def updates(
        self, source: EntryContext, destination: EntryContext
    ) -> dict[Path, str | None]:
        result: dict[Path, str | None] = {}
        groups = [
            (
                source,
                self.source_data,
                self.source_evidence,
                self.source_retention,
            )
        ]
        if source != destination:
            groups.append(
                (
                    destination,
                    self.destination_data,
                    self.destination_evidence,
                    self.destination_retention,
                )
            )
        for entry, data, evidence, retention in groups:
            for name, value in (
                ("data.json", data),
                ("evidence.json", evidence),
                ("retention.json", retention),
            ):
                path = entry.root / name
                text = value.canonical_json() if value is not None else None
                if path.exists() or text is not None:
                    result[path] = text
        return result


def _build_candidates(
    source: EntryContext,
    destination: EntryContext,
    state: _SourceState,
    plan: _TransferPlan,
    observations: _MaterialObservations,
) -> _Candidates:
    selections = plan.selections
    maps = plan.maps
    destination_data = _load_data(destination) if source != destination else state.data
    destination_evidence = (
        _load_evidence(destination) if source != destination else None
    )
    destination_retention = (
        _load_retention(destination) if source != destination else None
    )

    moved_data = tuple(
        _move_input(item, destination, maps, observations)
        for item in (state.data.inputs if state.data else ())
        if item.name in selections["data"]
    )
    source_data = tuple(
        item
        for item in (state.data.inputs if state.data else ())
        if item.name in selections["data"]
    )
    _require_selected_generated_observations(
        source, source_data, moved_data, observations
    )
    remaining_data = tuple(
        item
        for item in (state.data.inputs if state.data else ())
        if item.name not in selections["data"]
    )
    moved_evidence = tuple(
        _move_evidence(record, maps)
        for record in state.evidence
        if record.id in selections["evidence"]
    )
    remaining_evidence = tuple(
        record for record in state.evidence if record.id not in selections["evidence"]
    )
    moved_retention = tuple(
        _move_retention(record, maps)
        for record in state.retention
        if record.id in selections["retention"]
    )
    remaining_retention = tuple(
        record for record in state.retention if record.id not in selections["retention"]
    )

    if source == destination:
        data_file = _build_data(source, remaining_data + moved_data)
        _require_evidence_inputs(moved_evidence, data_file)
        _verify_evidence_values(source, moved_evidence, data_file, observations)
        return _Candidates(
            data_file,
            None,
            _build_evidence(source, remaining_evidence + moved_evidence),
            None,
            _build_retention(source, remaining_retention + moved_retention),
            None,
            moved_evidence,
        )
    source_data_file = _build_data(source, remaining_data)
    destination_data_file = _build_data(
        destination,
        (*(destination_data.inputs if destination_data else ()), *moved_data),
    )
    _require_evidence_inputs(moved_evidence, destination_data_file)
    _verify_evidence_values(
        destination, moved_evidence, destination_data_file, observations
    )
    return _Candidates(
        source_data_file,
        destination_data_file,
        _build_evidence(source, remaining_evidence),
        _build_evidence(
            destination,
            (
                *(destination_evidence.records if destination_evidence else ()),
                *moved_evidence,
            ),
        ),
        _build_retention(source, remaining_retention),
        _build_retention(
            destination,
            (
                *(destination_retention.records if destination_retention else ()),
                *moved_retention,
            ),
        ),
        moved_evidence,
    )


def _build_data(
    entry: EntryContext, inputs: tuple[InputResource, ...]
) -> DataFile | None:
    if not inputs:
        return None
    return data_file_from_inputs(
        entry.root / "data.json", entry_root=entry.root, inputs=inputs
    )


def _build_evidence(
    entry: EntryContext, records: tuple[EvidenceRecord, ...]
) -> EvidenceFile | None:
    if not records:
        return None
    return evidence_file_from_records(
        entry.root / "evidence.json",
        log_root=entry.log.root,
        entry_root=entry.root,
        records=records,
    )


def _build_retention(
    entry: EntryContext, records: tuple[RetentionRecord, ...]
) -> RetentionFile | None:
    if not records:
        return None
    return retention_file_from_records(
        entry.root / "retention.json", entry_root=entry.root, records=records
    )


def _move_input(
    item: InputResource,
    destination: EntryContext,
    maps: Mapping[str, Mapping[str, str]],
    observations: _MaterialObservations,
) -> InputResource:
    name = maps["data"].get(item.name, item.name)
    location = maps["path"].get(item.location, item.location)
    lexical = (
        Path(location) if Path(location).is_absolute() else destination.root / location
    )
    candidate = replace(
        item,
        name=name,
        location=location,
        canonical_target=lexical.resolve().as_posix(),
    )
    observations.observe_resource(candidate)
    return candidate


def _move_evidence(
    record: EvidenceRecord, maps: Mapping[str, Mapping[str, str]]
) -> EvidenceRecord:
    sources = tuple(
        EvidenceSource(_mapped_token(source.source, maps["data"]), source.locator)
        for source in record.sources
    )
    return replace(
        record,
        id=maps["evidence"].get(record.id, record.id),
        document=maps["document"].get(record.document, record.document),
        sources=sources,
    )


def _move_retention(
    record: RetentionRecord, maps: Mapping[str, Mapping[str, str]]
) -> RetentionRecord:
    return replace(
        record,
        id=maps["retention"].get(record.id, record.id),
        paths=tuple(maps["path"].get(path, path) for path in record.paths),
        directory=(
            maps["path"].get(record.directory, record.directory)
            if record.directory is not None
            else None
        ),
    )


def _mapped_token(value: str, names: Mapping[str, str]) -> str:
    parts = input_token_parts(value)
    assert parts is not None
    name, projection, member = parts
    mapped = names.get(name, name)
    suffix = f":{projection}" if projection is not None else ""
    return f"<{mapped}>{suffix}" + (f"/{member}" if member is not None else "")


def _verify_markdown(
    source: EntryContext,
    destination: EntryContext,
    records: tuple[EvidenceRecord, ...],
    state: _SourceState,
    plan: _TransferPlan,
) -> None:
    source_markers = {
        item.id for item in index_entry_presentations_all(source.root, source.log.root)
    }
    references = index_summary_references(
        source.log.summary.read_text(encoding="utf-8")
    )
    original_ids = {
        record.id
        for record in state.evidence
        if record.id in plan.selections["evidence"]
    }
    expected_ids = {
        plan.maps["evidence"].get(record_id, record_id) for record_id in original_ids
    }
    if source != destination and source_markers & original_ids:
        raise ActionError(
            "reorganize.transfer.markdown_incomplete",
            "selected source markers remain",
        )
    for record in records:
        presentation = find_entry_presentation(
            destination.root, destination.log.root, record.id
        )
        if presentation.document != record.document or presentation.kind != record.kind:
            raise ActionError("reorganize.transfer.markdown_incomplete", record.id)
        if any(
            item.evidence_id == record.id and item.entry != destination.id
            for item in references
        ):
            raise ActionError("reorganize.transfer.markdown_incomplete", record.id)
    if any(item.evidence_id in original_ids - expected_ids for item in references):
        raise ActionError(
            "reorganize.transfer.markdown_incomplete",
            "stale summary evidence references remain",
        )


def _require_evidence_inputs(
    records: tuple[EvidenceRecord, ...], data: DataFile | None
) -> None:
    names = set(data.by_name) if data is not None else set()
    for record in records:
        for source in record.sources:
            parts = input_token_parts(source.source)
            assert parts is not None
            if parts[0] not in names:
                raise ActionError(
                    "reorganize.transfer.dependency_missing", source.source
                )


def _require_source_detached(
    source: EntryContext, candidates: _Candidates, plan: _TransferPlan
) -> None:
    remaining = candidates.source_evidence
    if remaining is not None:
        _require_evidence_inputs(remaining.records, candidates.source_data)
    if not plan.selections["data"]:
        return
    used: set[str] = set()
    for document in source.root.glob("*.md"):
        used.update(command_input_names(document.read_text(encoding="utf-8")))
    stale = used & plan.selections["data"]
    if stale:
        raise ActionError(
            "reorganize.transfer.dependency_present",
            f"selected source inputs remain in recorded commands: {sorted(stale)}",
        )


def _verify_evidence_values(
    entry: EntryContext,
    records: tuple[EvidenceRecord, ...],
    data: DataFile | None,
    observations: _MaterialObservations,
) -> None:
    for record in records:
        presentation = find_entry_presentation(entry.root, entry.log.root, record.id)
        selections = []
        for source in record.sources:
            resolved = resolve_input_token(source.source, data)
            source_path = Path(resolved.path)
            if presentation.kind == "artifact":
                require_artifact_source_association(
                    presentation,
                    source_path=source_path,
                    log_root=entry.log.root,
                )
            resource_observation = observations.observe_resource(resolved.resource)
            if presentation.kind != "artifact":
                assert source.locator is not None
                selections.append(evaluate_locator(source_path, source.locator))
        if presentation.kind == "artifact":
            require_artifact_baseline_form(record, presentation)
            if presentation.presentation_form in {"image", "link"}:
                observed = (
                    resource_observation.fingerprint
                    if resolved.resource.kind == "file"
                    and source_path.resolve()
                    == Path(resolved.resource.canonical_target).resolve()
                    else observations.observe_artifact(source_path)
                )
                require_artifact_fingerprint(
                    record,
                    source_path=source_path,
                    observed=observed,
                )
            continue
        transformed = evaluate_transformation(
            record.transformation,
            selections,
            presentation_kind=presentation.kind,
        )
        compare_presentation(
            transformed,
            presented_kind=presentation.kind,
            presented=presentation.value,
        )


def _require_selected_generated_observations(
    source: EntryContext,
    originals: tuple[InputResource, ...],
    candidates: tuple[InputResource, ...],
    observations: _MaterialObservations,
) -> None:
    """Match selected generated material to its direct source observation."""

    selected = tuple(
        (original, candidate)
        for original, candidate in zip(originals, candidates, strict=True)
        if not original.origin
    )
    if not selected:
        return
    path = source.root / PYRUN_FILENAME
    if not path.exists() and not path.is_symlink():
        raise ActionError("data.fingerprint.unobserved", selected[0][0].name)
    project_root = resolve_project_root(source.log.root)
    state = load_pyrun_state(path, entry_root=source.root, project_root=project_root)
    for original, candidate in selected:
        target = Path(original.canonical_target).resolve()
        matches = [
            dict(execution.observed.outputs).get(output)
            for _, _, execution in state.execution_items()
            for output, _ in execution.recipe.outputs
            if output_target_path(
                output,
                entry_root=source.root,
                project_root=project_root,
                authored=True,
            ).resolve()
            == target
        ]
        if len(matches) != 1 or matches[0] is None:
            raise ActionError("data.fingerprint.unobserved", original.name)
        current = observations.observe_resource(candidate).fingerprint
        if current != matches[0]:
            raise ActionError("data.fingerprint.mismatch", original.name)


def _validate_log_data(
    source: EntryContext,
    destination: EntryContext,
    source_data: DataFile | None,
    destination_data: DataFile | None,
) -> None:
    candidates: list[DataFile] = []
    for entry in observe_physical_entries(source.log):
        if entry.root == source.root:
            candidate = source_data
        elif entry.root == destination.root:
            candidate = destination_data
        else:
            path = entry.root / "data.json"
            candidate = (
                load_data_file(path, entry_root=entry.root) if path.exists() else None
            )
        if candidate is not None:
            candidates.append(candidate)
    validate_log_consistency(tuple(candidates))


def _retired_support(
    source: EntryContext,
    destination: EntryContext,
    state: _SourceState,
    plan: _TransferPlan,
) -> tuple[_SupportUpdate | None, tuple[dict[str, object], ...]]:
    if source == destination:
        return None, ()
    current = source.root / PYRUN_FILENAME
    if not current.exists() and not current.is_symlink():
        return None, ()
    project_root = resolve_project_root(source.log.root)
    selected_paths = _selected_transfer_paths(state, plan)
    return _retired_execution_state(
        source,
        destination,
        current,
        selected_paths=selected_paths,
        project_root=project_root,
    )


def _selected_transfer_paths(state: _SourceState, plan: _TransferPlan) -> set[str]:
    """Return every selected entry-relative material target."""

    selected = set(plan.maps["path"])
    selected.update(
        item.location
        for item in (state.data.inputs if state.data is not None else ())
        if item.name in plan.selections["data"]
    )
    selected.update(
        path
        for record in state.retention
        if record.id in plan.selections["retention"]
        for path in (*record.paths, *((record.directory,) if record.directory else ()))
    )
    return selected


def _retired_execution_state(
    source: EntryContext,
    destination: EntryContext,
    path: Path,
    *,
    selected_paths: set[str],
    project_root: Path,
) -> tuple[_SupportUpdate | None, tuple[dict[str, object], ...]]:
    """Retire only complete executions whose full output set was transferred."""

    state = load_pyrun_state(path, entry_root=source.root, project_root=project_root)
    selected: dict[str, list[str]] = {}
    reruns: list[dict[str, object]] = []
    for cid, identity, execution in state.execution_items():
        outputs = set(dict(execution.recipe.outputs))
        if not outputs & selected_paths:
            continue
        if not outputs <= selected_paths:
            raise ActionError(
                "reorganize.transfer.support_ambiguous",
                f"transfer selects only part of execution {identity}",
            )
        for output in outputs:
            if output_target_path(
                output,
                entry_root=source.root,
                project_root=project_root,
                authored=True,
            ).exists():
                raise ActionError("reorganize.transfer.support_still_current", output)
        selected.setdefault(cid, []).append(identity)
        reruns.append(
            {
                "entry": destination.id,
                "cid": cid,
                "execution_id": identity,
                "parameters": list(execution.recipe.parameters),
                "script": execution.recipe.script,
            }
        )
    if not selected:
        return None, ()
    result = state
    for cid, identities in selected.items():
        executions = {
            key: value
            for key, value in result.commands[cid].executions.items()
            if key not in identities
        }
        commands = dict(result.commands)
        if executions:
            commands[cid] = PyrunCommand(executions)
        else:
            del commands[cid]
        result = PyrunFile(result.path, result.entry_root, commands)
    return (
        _SupportUpdate(result.path, result.serialized() if result.commands else None),
        tuple(reruns),
    )


def _require_selection(arguments: TransferArguments, same_entry: bool) -> None:
    lists = arguments.evidence or arguments.data or arguments.retention
    if arguments.select_all and lists:
        raise ActionError(
            "reorganize.selector.conflict", "--all excludes selector lists"
        )
    if not arguments.select_all and not lists:
        raise ActionError("reorganize.selector.missing", "select at least one record")
    if same_entry and arguments.select_all:
        raise ActionError("reorganize.selector.invalid", "--all requires two entries")
    if same_entry and (
        arguments.data
        or arguments.retention
        or arguments.data_maps
        or arguments.evidence_maps
        or arguments.retention_maps
        or arguments.path_maps
    ):
        raise ActionError(
            "reorganize.selector.invalid",
            "same-entry transfer changes evidence document associations only",
        )
    if same_entry and (not arguments.evidence or not arguments.document_maps):
        raise ActionError(
            "reorganize.mapping.missing",
            "same-entry transfer requires evidence and a document mapping",
        )


def _validated_maps(arguments: TransferArguments) -> dict[str, dict[str, str]]:
    values = {
        "document": arguments.document_maps,
        "path": arguments.path_maps,
        "data": arguments.data_maps,
        "evidence": arguments.evidence_maps,
        "retention": arguments.retention_maps,
    }
    result: dict[str, dict[str, str]] = {}
    for name, pairs in values.items():
        sources = [source for source, _ in pairs]
        destinations = [destination for _, destination in pairs]
        if len(sources) != len(set(sources)) or len(destinations) != len(
            set(destinations)
        ):
            raise ActionError("reorganize.mapping.duplicate", name)
        result[name] = dict(pairs)
    return result


def _selections(
    arguments: TransferArguments,
    data: DataFile | None,
    evidence: tuple[EvidenceRecord, ...],
    retention: tuple[RetentionRecord, ...],
) -> dict[str, frozenset[str]]:
    available = {
        "data": frozenset(data.by_name) if data is not None else frozenset(),
        "evidence": frozenset(record.id for record in evidence),
        "retention": frozenset(record.id for record in retention),
    }
    selected = (
        available
        if arguments.select_all
        else {
            "data": frozenset(arguments.data),
            "evidence": frozenset(arguments.evidence),
            "retention": frozenset(arguments.retention),
        }
    )
    for name in available:
        missing = selected[name] - available[name]
        if missing:
            raise ActionError(
                "reorganize.selector.unknown", f"{name}: {sorted(missing)}"
            )
    return selected


def _require_mapping_sources(
    maps: Mapping[str, Mapping[str, str]],
    selections: Mapping[str, frozenset[str]],
    data: DataFile | None,
    evidence: tuple[EvidenceRecord, ...],
    retention: tuple[RetentionRecord, ...],
) -> None:
    for name in ("data", "evidence", "retention"):
        extra = set(maps[name]) - selections[name]
        if extra:
            raise ActionError(
                "reorganize.mapping.unselected", f"{name}: {sorted(extra)}"
            )
    documents = {
        record.document for record in evidence if record.id in selections["evidence"]
    }
    paths = {
        path
        for record in retention
        if record.id in selections["retention"]
        for path in (*record.paths, *((record.directory,) if record.directory else ()))
    }
    paths.update(
        item.location
        for item in (data.inputs if data is not None else ())
        if item.name in selections["data"]
    )
    if set(maps["document"]) - documents:
        raise ActionError("reorganize.mapping.unselected", "document")
    if set(maps["path"]) - paths:
        raise ActionError("reorganize.mapping.unselected", "path")


def _load_data(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    return (
        load_data_file(path, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _load_evidence(entry: EntryContext) -> EvidenceFile | None:
    path = entry.root / "evidence.json"
    return (
        load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _load_retention(entry: EntryContext) -> RetentionFile | None:
    path = entry.root / "retention.json"
    return (
        load_retention_file(path, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def _evidence_records(entry: EntryContext) -> tuple[EvidenceRecord, ...]:
    path = entry.root / "evidence.json"
    if not path.exists() and not path.is_symlink():
        return ()
    return decode_evidence_file_records(
        path, log_root=entry.log.root, entry_root=entry.root
    )


def _retention_records(entry: EntryContext) -> tuple[RetentionRecord, ...]:
    path = entry.root / "retention.json"
    if not path.exists() and not path.is_symlink():
        return ()
    return decode_retention_file_records(path, entry_root=entry.root)


def _validate_unselected_source_context(
    source: EntryContext,
    state: _SourceState,
    selections: Mapping[str, frozenset[str]],
) -> None:
    """Validate source context except where the exact selected record moved."""

    evidence_path = source.root / "evidence.json"
    for index, evidence_record in enumerate(state.evidence):
        if evidence_record.id not in selections["evidence"]:
            validate_evidence_record_context(
                evidence_record,
                subject=f"{evidence_path}:records[{index}]",
                entry_root=source.root,
            )
    retention_path = source.root / "retention.json"
    for index, retention_record in enumerate(state.retention):
        if retention_record.id not in selections["retention"]:
            validate_retention_record_context(
                retention_record,
                subject=f"{retention_path}:records[{index}]",
                entry_root=source.root,
            )


def _result(
    status: str,
    changed: bool,
    paths: tuple[Path, ...],
    reruns: tuple[dict[str, object], ...],
) -> ActionResult:
    return ActionResult(
        "reorganize.transfer",
        status,
        f"reorganize.{status}",
        changed,
        paths=tuple(path.as_posix() for path in sorted(paths)),
        records=reruns,
    )
