"""Intent-aware entry-scoped input-registry authoring actions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from glob import has_magic
from pathlib import Path

from research_log_data import (
    EVIDENCE_COMPARISON_CONTRACT,
    DataFile,
    InputResource,
    ReproductionComparison,
    build_declared_generated,
    build_git_repository_input,
    build_identity_directory,
    build_identity_pattern_directory,
    build_local_input,
    data_file_from_inputs,
    input_token_parts,
    load_data_file,
    normalize_input_location,
    observe_fingerprint,
)
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    EvidenceSource,
    evidence_file_from_records,
    load_evidence_file,
)
from validation.operation_state import (
    begin_registry_transaction,
    finish_guarded_publication,
)
from validation.provenance import require_origin_boundary

from .context import EntryContext
from .materials import inspect_log_materials
from .model import (
    ActionError,
    ActionResult,
    DataAddArguments,
    DataUpdateArguments,
)
from .storage import (
    PublicationError,
    atomic_write_texts,
    entry_lock,
    entry_locks,
    remove_or_write,
)


@dataclass(frozen=True)
class _InputDefinition:
    """Complete semantic definition used to build one registry input."""

    name: str
    target: str
    origin: bool
    kind: str | None
    identity: tuple[str, ...] | None
    commit: str | None


def list_inputs(entry: EntryContext) -> ActionResult:
    """Return bounded semantic input declarations without registry internals."""

    current = _load(entry)
    inputs = () if current is None else current.inputs
    return ActionResult(
        "data.list",
        "unchanged",
        "data.listed",
        False,
        records=tuple(
            {
                **(
                    {"commit": item.fingerprint.digest}
                    if item.kind == "git-repository"
                    else {}
                ),
                "classification": "origin" if item.origin else "generated",
                "kind": item.kind,
                "name": item.name,
                **(
                    {"from_entry": item.reference_entry}
                    if item.reference_entry is not None
                    else {}
                ),
                **(
                    {"reproduction_comparison": item.comparison.profile}
                    if item.comparison is not None
                    else {}
                ),
                "target": item.location,
            }
            for item in sorted(inputs, key=lambda value: value.name)
        ),
    )


def use(
    entry: EntryContext,
    *,
    source: EntryContext,
    name: str,
    dry_run: bool,
) -> ActionResult:
    """Reference one generated declaration owned by another log entry."""

    if source.id == entry.id:
        raise ActionError("data.reference.invalid", "source and destination match")
    with entry_locks(entry.log, (entry, source)):
        current = _load(entry)
        if current is not None and name in current.by_name:
            raise ActionError("data.name.conflict", name)
        source_data = _required(source)
        source_item = _named(source_data, name)
        if source_item.origin or source_item.reference_entry is not None:
            raise ActionError(
                "data.reference.source_invalid",
                "source must directly declare a generated artifact",
            )
        reference = replace(source_item, reference_entry=source.id)
        built = _build(entry, (*_items(current), reference))
        if not dry_run:
            remove_or_write(built.path, built.canonical_json())
        return ActionResult(
            "data.use",
            "dry-run" if dry_run else "changed",
            "data.dry-run" if dry_run else "data.changed",
            True,
            records=({"from_entry": source.id, "name": name},),
        )


def add(
    entry: EntryContext, *, generated: bool, arguments: DataAddArguments
) -> ActionResult:
    """Add one inferred local input after checking its asserted boundary."""

    if arguments.pending_confirmation and not generated:
        raise ActionError(
            "data.pending.invalid",
            "pending confirmation applies only to generated inputs",
        )
    with entry_lock(entry):
        current = _load(entry)
        candidate = _build_item(
            entry,
            _InputDefinition(
                arguments.name,
                arguments.target,
                not generated,
                arguments.kind,
                arguments.identity,
                arguments.commit,
            ),
        )
        existing = current.by_name.get(arguments.name) if current else None
        if existing is not None:
            if existing == candidate:
                assert current is not None
                producer = _require_boundary(
                    entry,
                    current,
                    candidate,
                    pending_confirmation=arguments.pending_confirmation,
                )
                return _result(
                    "add-generated" if generated else "add-origin",
                    "unchanged",
                    False,
                    producer,
                )
            raise ActionError("data.input.conflict", arguments.name)
        built = _build(entry, (*_items(current), candidate))
        producer = _require_boundary(
            entry,
            built,
            candidate,
            pending_confirmation=arguments.pending_confirmation,
        )
        if not arguments.dry_run:
            remove_or_write(built.path, built.canonical_json())
        return _result(
            "add-generated" if generated else "add-origin",
            "dry-run" if arguments.dry_run else "changed",
            True,
            producer,
        )


def update(entry: EntryContext, arguments: DataUpdateArguments) -> ActionResult:
    """Apply explicit target, classification, or directory-identity changes."""

    if (
        arguments.target is None
        and arguments.classification is None
        and arguments.identity is None
        and not arguments.byte_complete
        and arguments.commit is None
        and arguments.reproduction_comparison is None
    ):
        raise ActionError("data.update.empty", "update requires an explicit change")
    with entry_lock(entry):
        current = _required(entry)
        existing = _named(current, arguments.name)
        if existing.reference_entry is not None:
            raise ActionError(
                "data.reference.read_only", "change the producer declaration"
            )
        origin = (
            existing.origin
            if arguments.classification is None
            else arguments.classification == "origin"
        )
        identity = _updated_identity(existing, arguments)
        commit = _updated_commit(existing, arguments)
        candidate = _build_item(
            entry,
            _InputDefinition(
                existing.name,
                arguments.target or existing.location,
                origin,
                existing.kind if existing.kind != "git-repository" else None,
                identity,
                commit,
            ),
        )
        comparison = existing.comparison
        if arguments.reproduction_comparison == "evidence":
            comparison = ReproductionComparison(
                EVIDENCE_COMPARISON_CONTRACT, "evidence"
            )
        elif arguments.reproduction_comparison == "exact":
            comparison = None
        candidate = replace(candidate, comparison=comparison)
        built = _build(entry, _replace(current, existing.name, candidate))
        _require_boundary(entry, built, candidate)
        if candidate == existing:
            return _result("update", "unchanged", False)
        if not arguments.dry_run:
            remove_or_write(built.path, built.canonical_json())
        return _result("update", "dry-run" if arguments.dry_run else "changed", True)


def refresh(entry: EntryContext, name: str, *, dry_run: bool) -> ActionResult:
    """Refresh one intentional byte identity without changing its semantics."""

    with entry_lock(entry):
        current = _required(entry)
        existing = _named(current, name)
        if existing.reference_entry is not None:
            raise ActionError(
                "data.reference.read_only", "refresh the producer declaration"
            )
        observed = observe_fingerprint(existing)
        candidate = replace(existing, fingerprint=observed.fingerprint)
        built = _build(entry, _replace(current, name, candidate))
        _require_boundary(entry, built, candidate)
        if candidate == existing:
            return _result("refresh", "unchanged", False)
        if not dry_run:
            remove_or_write(built.path, built.canonical_json())
        return _result("refresh", "dry-run" if dry_run else "changed", True)


def remove(entry: EntryContext, name: str, *, dry_run: bool) -> ActionResult:
    """Remove one declaration only after command and evidence use is absent."""

    with entry_lock(entry):
        current = _load(entry)
        if current is None or name not in current.by_name:
            return _result("remove", "absent", False)
        existing = current.by_name[name]
        if existing.reference_entry is None and _references_to(entry, name):
            raise ActionError(
                "data.remove.in_use", "remove cross-entry references first"
            )
        materials = inspect_log_materials(entry.log)
        if name in materials.input_names.get(entry.root, frozenset()):
            raise ActionError(
                "data.remove.in_use", "remove the recorded command use first"
            )
        evidence = _load_evidence(entry)
        if evidence is not None and any(
            _token_name(source.source) == name
            for record in evidence.records
            for source in record.sources
        ):
            raise ActionError("data.remove.in_use", "remove the evidence use first")
        remaining = tuple(item for item in current.inputs if item.name != name)
        text = _build(entry, remaining).canonical_json() if remaining else None
        if not dry_run:
            remove_or_write(current.path, text)
        return _result("remove", "dry-run" if dry_run else "changed", True)


def rename(
    entry: EntryContext, old_name: str, new_name: str, *, dry_run: bool
) -> ActionResult:
    """Rename one input and dependent evidence after command-token edits."""

    with entry_lock(entry):
        current = _required(entry)
        old = _named(current, old_name)
        if old.reference_entry is not None:
            raise ActionError(
                "data.reference.read_only", "rename the producer declaration"
            )
        if _references_to(entry, old_name):
            raise ActionError(
                "data.rename.in_use", "rename cross-entry references first"
            )
        if new_name in current.by_name:
            raise ActionError("data.name.conflict", new_name)
        candidate_item = replace(old, name=new_name)
        candidate = _build(entry, _replace(current, old_name, candidate_item))
        evidence = _renamed_evidence(entry, old_name, new_name)
        materials = inspect_log_materials(
            entry.log, data_overrides={entry.root: candidate}
        )
        if old_name in materials.input_names.get(entry.root, frozenset()):
            raise ActionError(
                "data.rename.command_incomplete",
                "rename every recorded-command token before the registry",
            )
        undeclared = [
            failure
            for failure in materials.failures.get(entry.root, ())
            if failure.error.code == "data.input.undeclared"
        ]
        if undeclared:
            raise ActionError(
                "data.rename.command_incomplete",
                "edited commands do not resolve against the renamed registry",
            )
        reruns = materials.rerun_commands(
            entry.root, old_name=old_name, new_name=new_name
        )
        if not dry_run:
            updates = {candidate.path: candidate.canonical_json()}
            if evidence is not None:
                updates[evidence.path] = evidence.canonical_json()
            residue = begin_registry_transaction(entry.log.root, entry.id)
            try:
                atomic_write_texts(updates)
            except PublicationError as error:
                if error.rollback_complete:
                    finish_guarded_publication(residue)
                    raise
                raise ActionError("data.rename.failed", str(error)) from error
            finish_guarded_publication(residue)
        return ActionResult(
            "data.rename",
            "dry-run" if dry_run else "changed",
            "data.dry-run" if dry_run else "data.changed",
            True,
            paths=tuple(
                path.as_posix()
                for path in sorted(
                    [
                        candidate.path,
                        *([evidence.path] if evidence is not None else []),
                    ],
                    key=lambda value: value.as_posix(),
                )
            ),
            records=reruns,
        )


def _build_item(
    entry: EntryContext,
    definition: _InputDefinition,
) -> InputResource:
    location = normalize_input_location(definition.target, entry_root=entry.root)
    path = Path(location) if Path(location).is_absolute() else entry.root / location
    observed_kind = (
        "file" if path.is_file() else "directory" if path.is_dir() else None
    )
    kind = definition.kind or observed_kind
    if definition.kind is not None and observed_kind not in {None, definition.kind}:
        raise ActionError("data.kind.invalid", definition.target)
    if kind is None:
        if definition.origin:
            raise ActionError("data.target.missing", definition.target)
        raise ActionError(
            "data.kind.required",
            "--kind is required before a generated target exists",
        )
    if definition.commit is not None:
        return _build_commit_item(entry, definition, location, kind)
    if definition.identity:
        return _build_identity_item(
            entry, definition, location, kind, observed_kind=observed_kind
        )
    if observed_kind is None:
        return build_declared_generated(
            definition.name,
            kind,
            location,
            entry_root=entry.root,
        )
    return build_local_input(
        definition.name,
        kind,
        location,
        entry_root=entry.root,
        origin=definition.origin,
    )


def _build_commit_item(
    entry: EntryContext,
    definition: _InputDefinition,
    location: str,
    kind: str,
) -> InputResource:
    if not definition.origin or kind != "directory" or definition.identity:
        raise ActionError(
            "data.git.invalid",
            "--commit requires an origin Git repository without --identity",
        )
    assert definition.commit is not None
    return build_git_repository_input(
        definition.name,
        location,
        definition.commit,
        entry_root=entry.root,
    )


def _build_identity_item(
    entry: EntryContext,
    definition: _InputDefinition,
    location: str,
    kind: str,
    *,
    observed_kind: str | None,
) -> InputResource:
    if kind != "directory":
        raise ActionError("data.identity.invalid", "--identity requires a directory")
    assert definition.identity is not None
    if observed_kind is None:
        return build_declared_generated(
            definition.name,
            kind,
            location,
            entry_root=entry.root,
            identity=definition.identity,
        )
    builder = (
        build_identity_pattern_directory
        if any(has_magic(selector) for selector in definition.identity)
        else build_identity_directory
    )
    return builder(
        definition.name,
        location,
        definition.identity,
        entry_root=entry.root,
        origin=definition.origin,
    )


def _updated_identity(
    existing: InputResource, arguments: DataUpdateArguments
) -> tuple[str, ...] | None:
    if arguments.commit is not None:
        return None
    if arguments.identity is not None:
        return arguments.identity
    if arguments.byte_complete:
        return None
    if existing.fingerprint.algorithm == "identity-files-sha256-v1":
        return existing.fingerprint.files
    if existing.fingerprint.algorithm == "identity-patterns-sha256-v1":
        return existing.fingerprint.patterns
    return None


def _updated_commit(
    existing: InputResource, arguments: DataUpdateArguments
) -> str | None:
    if arguments.commit is not None:
        return arguments.commit
    if existing.kind == "git-repository":
        if arguments.classification == "generated":
            raise ActionError(
                "data.git.invalid", "a Git repository input must be an origin"
            )
        if arguments.identity is not None or arguments.byte_complete:
            raise ActionError(
                "data.git.invalid",
                "a Git repository input cannot use directory identity options",
            )
        return existing.fingerprint.digest
    return None


def _require_boundary(
    entry: EntryContext,
    data: DataFile,
    candidate: InputResource,
    *,
    pending_confirmation: bool = False,
) -> dict[str, object] | None:
    if candidate.kind == "git-repository":
        return None
    materials = inspect_log_materials(
        entry.log, data_overrides={entry.root: data}
    )
    if candidate.origin:
        require_origin_boundary(
            candidate.canonical_target,
            candidate,
            materials.invocations,
            confirmed_record=materials.confirmed,
        )
        return None
    if candidate.fingerprint.digest is None:
        producer = materials.require_pending_generated(candidate)
        return {
            "confirmation": "not_yet_produced",
            "document": producer.document,
            "fence": producer.fence,
            "ordinal": producer.ordinal,
        }
    if pending_confirmation:
        producer = materials.require_pending_generated(candidate)
        return {
            "confirmation": "pending",
            "document": producer.document,
            "fence": producer.fence,
            "ordinal": producer.ordinal,
        }
    materials.require_generated(candidate)
    return None


def _renamed_evidence(
    entry: EntryContext, old_name: str, new_name: str
) -> EvidenceFile | None:
    current = _load_evidence(entry)
    if current is None:
        return None
    records: list[EvidenceRecord] = []
    for record in current.records:
        sources = tuple(
            EvidenceSource(
                _rename_token(source.source, old_name, new_name), source.locator
            )
            for source in record.sources
        )
        records.append(
            EvidenceRecord(
                record.id,
                record.document,
                record.kind,
                sources,
                record.transformation,
                record.reproduction_tolerance,
            )
        )
    return evidence_file_from_records(
        current.path,
        log_root=entry.log.root,
        entry_root=entry.root,
        records=tuple(records),
    )


def _rename_token(value: str, old_name: str, new_name: str) -> str:
    name, projection, member = input_token_parts(value) or (None, None, None)
    if name != old_name:
        return value
    suffix = f":{projection}" if projection is not None else ""
    return f"<{new_name}>{suffix}" + (
        f"/{member}" if member is not None else ""
    )


def _token_name(value: str) -> str | None:
    parts = input_token_parts(value)
    return parts[0] if parts is not None else None


def _load(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    legacy = entry.root / "data.csv"
    unsupported = [
        candidate
        for root in (entry.root.parent, entry.log.root)
        for candidate in (root / "data.json", root / "data.csv")
        if candidate.exists() or candidate.is_symlink()
    ]
    if unsupported:
        raise ActionError(
            "data.file.location_invalid",
            "parent or log-level data files are unsupported",
        )
    if path.exists() and legacy.exists():
        raise ActionError(
            "data.file.location_invalid", "conflicting data.json and data.csv"
        )
    if legacy.exists() or legacy.is_symlink():
        raise ActionError("data.file.location_invalid", "legacy data.csv")
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


def _references_to(entry: EntryContext, name: str) -> tuple[str, ...]:
    """Return entries explicitly referencing one direct declaration."""

    references: list[str] = []
    entries_root = entry.root.parent
    for candidate in sorted(entries_root.iterdir(), key=lambda path: path.name):
        if candidate == entry.root or candidate.is_symlink() or not candidate.is_dir():
            continue
        path = candidate / "data.json"
        if path.is_symlink() or not path.is_file():
            continue
        data = load_data_file(path, entry_root=candidate)
        item = data.by_name.get(name)
        if item is not None and item.reference_entry == entry.id:
            references.append(candidate.name)
    return tuple(references)


def _required(entry: EntryContext) -> DataFile:
    current = _load(entry)
    if current is None:
        raise ActionError("data.input.missing", "data registry is absent")
    return current


def _named(data: DataFile, name: str) -> InputResource:
    item = data.by_name.get(name)
    if item is None:
        raise ActionError("data.input.missing", name)
    return item


def _items(data: DataFile | None) -> tuple[InputResource, ...]:
    return data.inputs if data is not None else ()


def _replace(
    data: DataFile, name: str, replacement: InputResource
) -> tuple[InputResource, ...]:
    return tuple(replacement if item.name == name else item for item in data.inputs)


def _build(entry: EntryContext, inputs: tuple[InputResource, ...]) -> DataFile:
    return data_file_from_inputs(
        entry.root / "data.json", entry_root=entry.root, inputs=inputs
    )


def _result(
    action: str,
    status: str,
    changed: bool,
    producer: dict[str, object] | None = None,
) -> ActionResult:
    return ActionResult(
        f"data.{action}",
        status,
        f"data.{status}",
        changed,
        records=(producer,) if producer is not None else None,
    )
