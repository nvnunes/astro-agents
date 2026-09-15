"""Intent-aware entry-scoped input-registry authoring actions."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from glob import has_magic
from pathlib import Path
from typing import Any

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
    resolve_input_token,
)
from validation.evidence import (
    EvidenceFile,
    EvidenceRecord,
    EvidenceSource,
    evidence_file_from_records,
    index_entry_presentations,
    load_evidence_file,
    require_markdown_definition,
)
from validation.evidence_markdown import read_markdown_evidence
from validation.provenance import require_origin_boundary
from validation.pyrun_state import (
    PyrunCommand,
    PyrunFile,
    compare_command,
    execution_id,
    load_pyrun_state,
    parse_pyrun_state_text,
    validated_pyrun_serialization,
)

from .context import EntryContext, resolve_project_root
from .graph_state import (
    authored_uses,
    declaration_uses,
    describe_uses,
    material_consumers,
    normalized_uses,
    publish_updates,
    related_entries,
)
from .materials import LogMaterials, inspect_log_materials
from .model import (
    ActionError,
    ActionResult,
    DataUpdateArguments,
)
from .retention import require_unretained_paths
from .storage import (
    entry_lock_under_log,
    log_lock,
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
    identity_patterns: bool = False


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
                    {"commit": item.identity.commit}
                    if item.kind == "git-repository"
                    else {}
                ),
                "boundary": "origin" if item.origin else "generated",
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
                "identity": item.identity.as_dict(),
            }
            for item in sorted(inputs, key=lambda value: value.name)
        ),
    )


def update(entry: EntryContext, arguments: DataUpdateArguments) -> ActionResult:
    """Apply explicit target, boundary, or directory-identity changes."""

    if (
        arguments.target is None
        and arguments.boundary is None
        and arguments.identity is None
        and arguments.reproduction_comparison is None
        and arguments.kind is None
    ):
        raise ActionError("data.update.empty", "update requires an explicit change")
    with (
        nullcontext() if arguments.dry_run else log_lock(entry.log),
        nullcontext() if arguments.dry_run else entry_lock_under_log(entry),
    ):
        current = _required(entry)
        existing = _named(current, arguments.name)
        if existing.reference_entry is not None:
            raise ActionError(
                "data.reference.read_only", "change the producer declaration"
            )
        consumers = declaration_uses(entry, existing.name)
        origin = (
            existing.origin
            if arguments.boundary is None
            else arguments.boundary == "origin"
        )
        identity = _updated_identity(existing, arguments)
        target, commit = _update_target(existing, arguments)
        candidate = _build_item(
            entry,
            _InputDefinition(
                existing.name,
                target,
                origin,
                arguments.kind
                or (existing.kind if existing.kind != "git-repository" else None),
                identity,
                commit,
                any(value.startswith("pattern:") for value in arguments.identity or ())
                if arguments.identity is not None
                else existing.identity.algorithm == "identity-patterns-sha256-v1",
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
        if candidate == existing:
            return _result("update", "unchanged", False)
        if (
            len(
                {
                    (
                        use["entry"],
                        use.get("command"),
                        use.get("evidence"),
                        use.get("from_entry"),
                    )
                    for use in consumers
                }
            )
            > 1
            and not arguments.acknowledge_shared
        ):
            raise ActionError(
                "data.update.shared",
                "this declaration has several consumers; review them and use "
                "--acknowledge-shared" + describe_uses(consumers),
                records=consumers,
            )
        built = _build(entry, _replace(current, existing.name, candidate))
        if consumers:
            require_unretained_paths(entry, (candidate.canonical_target,))
        _require_consumer_tokens(entry, candidate, built)
        _require_boundary(entry, built, candidate)
        if not arguments.dry_run:
            publish_updates((entry,), {built.path: built.canonical_json()})
        return _result("update", "dry-run" if arguments.dry_run else "changed", True)


def remove(entry: EntryContext, name: str, *, dry_run: bool) -> ActionResult:
    """Remove one declaration only after command and evidence use is absent."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = _load(entry)
        if current is None or name not in current.by_name:
            return _result("delete", "absent", False)
        existing = current.by_name[name]
        uses = declaration_uses(entry, name)
        if uses:
            raise ActionError(
                "data.delete.in_use",
                "remove every use and sync its owner before deleting this declaration"
                + describe_uses(uses),
                records=uses,
            )
        remaining = tuple(item for item in current.inputs if item.name != name)
        text = _build(entry, remaining).canonical_json() if remaining else None
        disconnected = (
            existing.reference_entry is None
            and Path(existing.canonical_target).exists()
            and not material_consumers(entry, Path(existing.canonical_target))
        )
        if not dry_run:
            publish_updates((entry,), {current.path: text})
        return ActionResult(
            "data.delete",
            "dry-run" if dry_run else "changed",
            "data.dry-run" if dry_run else "data.changed",
            True,
            records=({"disconnected": existing.canonical_target},)
            if disconnected
            else (),
        )


def rename(
    entry: EntryContext, old_name: str, new_name: str, *, dry_run: bool
) -> ActionResult:
    """Rename one input and dependent evidence after command-token edits."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        current = _required(entry)
        old = _named(current, old_name)
        if old.reference_entry is not None:
            raise ActionError(
                "data.reference.read_only", "rename the producer declaration"
            )
        if new_name in current.by_name:
            raise ActionError("data.name.conflict", new_name)
        related = related_entries(entry, old_name)
        _require_renamed_uses(related, old_name, new_name)
        candidate_item = replace(old, name=new_name)
        candidate = _build(entry, _replace(current, old_name, candidate_item))
        updates = {candidate.path: candidate.canonical_json()}
        overrides = {entry.root: candidate}
        for affected, affected_data in related:
            if affected.id != entry.id and affected_data is not None:
                renamed = replace(affected_data.by_name[old_name], name=new_name)
                referenced = DataFile(
                    affected_data.path,
                    affected.root,
                    _replace(affected_data, old_name, renamed),
                )
                updates[referenced.path] = referenced.canonical_json()
                overrides[affected.root] = referenced
            evidence = _renamed_evidence(affected, old_name, new_name)
            if evidence is not None:
                _require_evidence_definitions(affected, evidence, new_name)
                updates[evidence.path] = evidence.canonical_json()
            pyrun = _renamed_pyrun(affected, old_name, new_name)
            if pyrun is not None:
                updates[affected.root / "pyrun.json"] = pyrun
        materials = inspect_log_materials(entry.log, data_overrides=overrides)
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
        _require_renamed_commands(related, updates, materials, old_name)
        if not dry_run:
            publish_updates(tuple(affected for affected, _ in related), updates)
        return ActionResult(
            "data.rename",
            "dry-run" if dry_run else "changed",
            "data.dry-run" if dry_run else "data.changed",
            True,
            paths=tuple(
                path.as_posix()
                for path in sorted(
                    updates,
                    key=lambda value: value.as_posix(),
                )
            ),
            records=reruns,
        )


def _require_renamed_uses(
    related: tuple[tuple[EntryContext, DataFile | None], ...], old: str, new: str
) -> None:
    problems: list[dict[str, Any]] = []
    for affected, _ in related:
        problems.extend(authored_uses(affected, old))
        replacements = authored_uses(affected, new)
        for use in normalized_uses(affected, old, include_targets=False):
            kind = "command" if "command" in use else "evidence"
            if not any(
                replacement.get(kind) == use[kind] for replacement in replacements
            ):
                problems.append(
                    {**use, "reason": "replacement_missing", "expected_name": new}
                )
    if problems:
        raise ActionError(
            "data.rename.markdown_incomplete",
            "replace every old-name Markdown use with the new name before renaming"
            + describe_uses(tuple(problems)),
            records=tuple(problems),
        )


def _require_evidence_definitions(
    entry: EntryContext, evidence: EvidenceFile, renamed_name: str
) -> None:
    for record in evidence.records:
        if not any(
            _token_name(source.source) == renamed_name for source in record.sources
        ):
            continue
        text = (entry.log.root / record.document).read_text(encoding="utf-8")
        item = next(
            (
                item
                for item in index_entry_presentations(
                    text, document=record.document, record_id=record.id
                )
                if item.id == record.id
            ),
            None,
        )
        if item is None:
            raise ActionError(
                "data.rename.markdown_incomplete",
                f"{record.id}: renamed presentation missing",
            )
        require_markdown_definition(record, item)


def _require_renamed_commands(
    related: tuple[tuple[EntryContext, DataFile | None], ...],
    updates: dict[Path, str],
    materials: LogMaterials,
    old_name: str,
) -> None:
    for affected, _ in related:
        path = affected.root / "pyrun.json"
        if path not in updates:
            continue
        project = resolve_project_root(affected.root)
        before = load_pyrun_state(path, entry_root=affected.root, project_root=project)
        candidate = parse_pyrun_state_text(
            updates[path], subject=path, entry_root=affected.root, project_root=project
        )
        for cid, command in before.commands.items():
            if not any(
                old_name in execution.recipe.inputs
                or any(
                    _token_name(value) == old_name
                    for value in execution.recipe.parameters
                )
                for execution in command.executions.values()
            ):
                continue
            selected = tuple(
                invocation
                for invocation in materials.invocations
                if invocation.cid == cid
                and materials.roots[invocation.material_owner] == affected.root
            )
            comparison = compare_command(candidate, cid, selected, project_root=project)
            if (
                comparison.missing
                or comparison.stale
                or comparison.recipe_changed
                or comparison.policy_changed
            ):
                raise ActionError(
                    "data.rename.command_incomplete",
                    f"{affected.id}/{cid}: replace every old-name parameter "
                    "with the new name without changing other recipe or policy fields",
                )


def _renamed_pyrun(entry: EntryContext, old: str, new: str) -> str | None:
    path = entry.root / "pyrun.json"
    if not path.exists() and not path.is_symlink():
        return None
    project = resolve_project_root(entry.root)
    state = load_pyrun_state(path, entry_root=entry.root, project_root=project)
    commands = {}
    for cid, command in state.commands.items():
        executions = {}
        for previous in command.executions.values():
            recipe = replace(
                previous.recipe,
                parameters=tuple(
                    _rename_token(value, old, new)
                    for value in previous.recipe.parameters
                ),
                inputs=tuple(
                    new if value == old else value for value in previous.recipe.inputs
                ),
            )
            identity = execution_id(recipe)
            if identity in executions:
                raise ActionError(
                    "data.rename.execution_conflict",
                    f"{cid}: renamed execution identities collide",
                )
            observed = replace(
                previous.observed,
                inputs=tuple(
                    (new if name == old else name, fingerprint)
                    for name, fingerprint in previous.observed.inputs
                ),
            )
            executions[identity] = (
                replace(
                    previous,
                    recipe=recipe,
                    observed=observed,
                    requires_reproduction=True,
                )
                if recipe != previous.recipe
                else previous
            )
        commands[cid] = PyrunCommand(executions)
    return validated_pyrun_serialization(
        PyrunFile(path, entry.root, commands), project_root=project
    )


def _build_item(
    entry: EntryContext,
    definition: _InputDefinition,
) -> InputResource:
    location = normalize_input_location(definition.target, entry_root=entry.root)
    path = Path(location) if Path(location).is_absolute() else entry.root / location
    observed_kind = "file" if path.is_file() else "directory" if path.is_dir() else None
    if definition.origin and observed_kind is None:
        raise ActionError("data.target.missing", definition.target)
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
        if definition.identity_patterns
        or any(has_magic(selector) for selector in definition.identity)
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
    if arguments.identity is not None:
        return _identity_selectors(arguments.identity)
    if existing.identity.algorithm == "identity-files-sha256-v1":
        return existing.identity.files
    if existing.identity.algorithm == "identity-patterns-sha256-v1":
        return existing.identity.patterns
    return None


def _identity_selectors(values: tuple[str, ...]) -> tuple[str, ...] | None:
    if values == ("byte-complete",):
        return None
    kinds = {value.partition(":")[0] for value in values}
    if kinds not in ({"file"}, {"pattern"}):
        raise ActionError(
            "data.identity.invalid",
            "use byte-complete alone, or file:PATH or pattern:GLOB selectors "
            "of one kind",
        )
    selectors = tuple(dict.fromkeys(value.partition(":")[2] for value in values))
    if not all(selectors):
        raise ActionError(
            "data.identity.invalid",
            "identity selectors require a nonempty path or pattern",
        )
    return selectors


def _updated_commit(
    existing: InputResource, arguments: DataUpdateArguments
) -> str | None:
    if existing.kind == "git-repository":
        if arguments.boundary == "generated":
            raise ActionError(
                "data.git.invalid", "a Git repository input must be an origin"
            )
        if arguments.identity is not None:
            raise ActionError(
                "data.git.invalid",
                "a Git repository input cannot use directory identity options",
            )
        return existing.identity.commit
    return None


def _update_target(
    existing: InputResource, arguments: DataUpdateArguments
) -> tuple[str, str | None]:
    """Resolve the one plain or Git COMMIT:PATH target form."""

    if arguments.target is not None:
        revision, separator, path = arguments.target.partition(":")
        if (
            separator
            and len(revision) == 40
            and all(character in "0123456789abcdef" for character in revision)
        ):
            if not path:
                raise ActionError(
                    "data.git.invalid", "COMMIT:PATH requires a repository path"
                )
            return path, revision
    return arguments.target or existing.location, _updated_commit(existing, arguments)


def _require_boundary(
    entry: EntryContext,
    data: DataFile,
    candidate: InputResource,
) -> dict[str, object] | None:
    if candidate.kind == "git-repository":
        observe_fingerprint(candidate)
        return None
    materials = inspect_log_materials(entry.log, data_overrides={entry.root: data})
    if candidate.origin:
        require_origin_boundary(
            candidate.canonical_target,
            candidate,
            materials.invocations,
            confirmed_record=materials.confirmed,
        )
        return None
    producer = materials.require_pending_generated(candidate)
    return {
        "reproduction": "not_yet_produced",
        "document": producer.document,
        "fence": producer.fence,
        "ordinal": producer.ordinal,
    }


def _require_consumer_tokens(
    entry: EntryContext, candidate: InputResource, built: DataFile
) -> None:
    """Reject kind/member changes that would invalidate affected evidence sources."""

    for affected, current in related_entries(entry, candidate.name):
        if current is None:
            continue
        data = (
            built
            if affected.id == entry.id
            else DataFile(
                current.path,
                affected.root,
                _replace(
                    current,
                    candidate.name,
                    replace(candidate, reference_entry=entry.id),
                ),
            )
        )
        evidence = _load_evidence(affected)
        if evidence is None:
            continue
        for record in evidence.records:
            if not any(
                _token_name(source.source) == candidate.name
                for source in record.sources
            ):
                continue
            marker = read_markdown_evidence(
                (entry.log.root / record.document).read_text(encoding="utf-8"),
                record.id,
            )
            for source in marker.sources:
                if _token_name(source["source"]) != candidate.name:
                    continue
                resolved = resolve_input_token(source["source"], data)
                if resolved.member is None and resolved.resource.kind != "file":
                    raise ActionError(
                        "data.update.consumer_invalid",
                        f"{affected.id}/{record.id}: evidence requires a file source; "
                        "edit its comment to select an explicit directory member, "
                        "then update data and sync the evidence",
                    )


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
        records.append(replace(record, sources=sources))
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
    return f"<{new_name}>{suffix}" + (f"/{member}" if member is not None else "")


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
