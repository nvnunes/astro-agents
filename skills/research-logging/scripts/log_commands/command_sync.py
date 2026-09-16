"""Transactional synchronization of one Markdown-owned command bucket."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from research_log_data import (
    DataContractError,
    DataFile,
    InputResource,
    build_declared_generated,
    build_git_repository_input,
    build_local_input,
    data_file_from_inputs,
    input_token_parts,
    load_data_file,
    normalize_input_location,
    observe_fingerprint,
)
from research_log_reservations import artifact_transaction, require_artifact_access
from validation.commands import (
    CommandDeclaration,
    CommandDeclarationContext,
    CommandDeclarationResult,
    CommandDiscoveryFailure,
    Invocation,
    index_commands,
    materialize_declared_commands,
    order_invocations,
    validate_command_structure,
)
from validation.errors import MechanicalContractError
from validation.evidence import index_entry_documents, load_evidence_file
from validation.operation_state import (
    begin_registry_transaction,
    finish_guarded_publication,
)
from validation.provenance import build_producer_index
from validation.pyrun_contract import automatic_option_role, recipe_script_parameters
from validation.pyrun_state import (
    PYRUN_FILENAME,
    PyrunCommand,
    PyrunFile,
    changed_execution,
    compare_command,
    empty_pyrun_state,
    load_pyrun_state,
    pending_execution,
    validated_pyrun_serialization,
)

from .authoring_transactions import (
    data_change_paths,
    merge_data,
    require_unchanged,
    resource_authority,
)
from .context import (
    EntryContext,
    parse_entry_document_name,
    resolve_entry,
    resolve_project_root,
)
from .data_assertions import (
    assignment as _mapping,
)
from .data_assertions import (
    ensure_declaration as _ensure_declaration,
)
from .data_assertions import (
    require_local_target as _require_local_target,
)
from .model import ActionError, ActionResult, CommandSyncArguments
from .retention import require_unretained_paths
from .storage import PublicationError, atomic_write_texts, entry_locks


@dataclass(frozen=True)
class _LocalDeclarationAssertions:
    """The fields asserted by one file or directory add form."""

    values: tuple[str, ...]
    flag: str
    kind: str
    origin: bool


@dataclass(frozen=True)
class _PreparedCommand:
    """Unlocked command preparation, with only relevant state retained."""

    before: DataFile | None
    candidate: DataFile | None
    declarations: tuple[CommandDeclaration, ...]
    invocations: tuple[Invocation, ...]
    failures: tuple[tuple[str, CommandDiscoveryFailure], ...]


def sync_command(
    entry: EntryContext,
    arguments: CommandSyncArguments,
) -> ActionResult:
    """Synchronize one selected CID and its supplemental data declarations."""

    project = resolve_project_root(entry.root)
    try:
        source_ids = tuple(
            dict.fromkeys(
                _mapping(value, "--add-from-entry")[1]
                for value in arguments.add_from_entries
            )
        )
        if entry.id in source_ids:
            raise ActionError("data.reference.invalid", "source and destination match")
        referenced_entries = tuple(
            resolve_entry(entry.log, source_id) for source_id in source_ids
        )
        prepared = _prepare_command(entry, project, arguments)
        if arguments.dry_run:
            return _finish_command(entry, project, arguments, prepared)
        with (
            entry_locks(entry.log, (entry, *referenced_entries), timeout_seconds=10),
            artifact_transaction(project),
        ):
            return _finish_command(entry, project, arguments, prepared)
    except ActionError:
        raise
    except DataContractError as error:
        raise ActionError(
            "command.sync.declaration.invalid",
            "the requested data declaration is invalid",
            records=(
                {
                    "code": error.code,
                    "observed": error.observed,
                    "subject": error.subject,
                },
            ),
            diagnostic_log=entry.log.root,
        ) from error
    except (MechanicalContractError, OSError, UnicodeError) as error:
        raise ActionError("command.sync.unavailable", str(error)) from error


def _prepare_command(
    entry: EntryContext,
    project: Path,
    arguments: CommandSyncArguments,
) -> _PreparedCommand:
    with (
        nullcontext()
        if arguments.dry_run
        else entry_locks(entry.log, (entry,), timeout_seconds=10)
    ):
        current_data = _load_data(entry)
        indexed = _index_entry(entry, project, current_data)
    selected = _selected_declarations(indexed, arguments.cid)
    candidate_data = _candidate_data(
        entry,
        current_data,
        selected,
        arguments,
        indexed,
    )
    missing = _missing_declarations(selected, candidate_data)
    if missing:
        raise ActionError(
            "command.sync.declarations.required",
            "supply every missing declaration",
            records=missing,
            diagnostic_log=entry.log.root,
        )
    invocations, failures = _materialize(indexed, candidate_data)
    selected_invocations = tuple(
        item for item in invocations if item.cid == arguments.cid
    )
    relevant_failures = tuple(
        (document, failure)
        for document, failure in failures
        if any(
            declaration.document == document and declaration.fence == failure.fence
            for declaration in selected
        )
    )
    if relevant_failures:
        raise ActionError(
            "command.sync.declaration.invalid",
            "selected command has unresolved declarations",
            records=tuple(
                {**_failure_record(document, failure), "status": "selected-failure"}
                for document, failure in relevant_failures
            ),
            diagnostic_log=entry.log.root,
        )
    if not selected_invocations:
        raise ActionError("command.sync.cid.missing", arguments.cid)
    try:
        validate_command_structure(selected_invocations)
    except MechanicalContractError as error:
        raise ActionError("command.sync.structure.invalid", str(error)) from error
    _require_requested_declarations_consumed(entry, arguments, selected_invocations)
    for invocation in selected_invocations:
        paths = [
            relationship.path
            for relationship in (*invocation.inputs, *invocation.outputs)
        ]
        paths.extend(
            collection.root for collection in invocation.collections if collection.root
        )
        if invocation.script:
            paths.append(invocation.script)
        require_unretained_paths(entry, paths)
    _require_output_safety(indexed, invocations, selected_invocations)
    _require_generated_boundaries(
        entry,
        current_data,
        candidate_data,
        arguments,
        invocations,
    )
    _require_origin_boundaries(
        candidate_data,
        invocations,
        tuple(
            _mapping(value, flag)[0]
            for flag, values in (
                ("--add-origin", arguments.add_origins),
                ("--add-origin-directory", arguments.add_origin_directories),
                ("--add-origin-git", arguments.add_origin_git),
            )
            for value in values
        )
        + tuple(
            name
            for value in arguments.target_changes
            if candidate_data is not None
            and (name := _mapping(value, "--change-target")[0])
            and candidate_data.by_name[name].origin
        ),
    )

    return _PreparedCommand(
        current_data, candidate_data, selected, selected_invocations, failures
    )


def _finish_command(
    entry: EntryContext,
    project: Path,
    arguments: CommandSyncArguments,
    prepared: _PreparedCommand,
) -> ActionResult:
    names = {
        parts[0]
        for declaration in prepared.declarations
        for value in declaration.tokens
        if (parts := input_token_parts(value)) is not None
    }
    data = merge_data(
        prepared.before, prepared.candidate, _load_data(entry), names=names, entry=entry
    )
    indexed = _index_entry(entry, project, data)
    selected = _selected_declarations(indexed, arguments.cid)
    require_unchanged(
        tuple((item.document, item.parsed) for item in prepared.declarations),
        tuple((item.document, item.parsed) for item in selected),
        f"{entry.id}/{arguments.cid} Markdown command",
    )
    for raw in arguments.add_from_entries:
        name, source_id = _mapping(raw, "--add-from-entry")
        source = _load_data(resolve_entry(entry.log, source_id))
        expected = data.by_name[name] if data else None
        require_unchanged(
            resource_authority(
                replace(expected, reference_entry=None) if expected else None
            ),
            resource_authority(source.by_name.get(name) if source else None),
            f"{source_id}/{name} source declaration",
        )
    invocations, failures = _materialize(indexed, data)
    selected_invocations = tuple(
        item for item in invocations if item.cid == arguments.cid
    )
    _require_output_safety(indexed, invocations, selected_invocations)
    _require_generated_boundaries(entry, prepared.before, data, arguments, invocations)
    pyrun_text = _candidate_pyrun_text(
        entry,
        project,
        arguments.cid,
        selected_invocations,
        arguments.execution_deletions,
    )
    data_text = data.canonical_json() if data is not None else None
    require_artifact_access(project, writes=data_change_paths(prepared.before, data))
    if arguments.dry_run:
        return _publish_candidates(entry, data_text, pyrun_text, failures, dry_run=True)
    return _publish_candidates(entry, data_text, pyrun_text, failures, dry_run=False)


def _candidate_pyrun_text(
    entry: EntryContext,
    project: Path,
    cid: str,
    invocations: tuple[Invocation, ...],
    deletions: tuple[str, ...],
) -> str:
    path = entry.root / PYRUN_FILENAME
    if path.exists() or path.is_symlink():
        try:
            state = load_pyrun_state(path, entry_root=entry.root, project_root=project)
        except MechanicalContractError as error:
            raise ActionError(
                "command.sync.registry.invalid",
                "the owned pyrun registry requires direct Repair before sync",
                records=(
                    {
                        "code": error.code,
                        "observed": error.observed,
                        "registry": error.subject,
                        "required_action": "direct Repair",
                    },
                ),
                diagnostic_log=entry.log.root,
            ) from error
    else:
        state = empty_pyrun_state(entry.root)
    comparison = compare_command(state, cid, invocations, project_root=project)
    stale_ids = tuple(item.identity for item in comparison.stale)
    invalid_deletions = sorted(set(deletions) - set(stale_ids))
    if invalid_deletions:
        current_ids = set(state.commands.get(cid, PyrunCommand({})).executions)
        raise ActionError(
            "command.sync.execution.not_stale",
            "only stale executions may be deleted during command sync",
            records=tuple(
                {
                    "cid": cid,
                    "execution_id": identity,
                    "status": "current" if identity in current_ids else "unknown",
                }
                for identity in invalid_deletions
            ),
            diagnostic_log=entry.log.root,
        )
    if set(deletions) != set(stale_ids) or len(deletions) != len(set(deletions)):
        raise ActionError(
            "command.sync.execution.deletion_required",
            "delete every stale execution ID exactly once",
            records=tuple(
                {
                    "cid": item.cid,
                    "execution_id": item.identity,
                    "parameters": list(
                        recipe_script_parameters(item.execution.recipe.parameters)
                    ),
                    "retry_flag": f"--delete-execution {item.identity}",
                    "script": item.execution.recipe.script,
                }
                for item in comparison.stale
            ),
            diagnostic_log=entry.log.root,
        )
    executions = dict(state.commands.get(cid, PyrunCommand({})).executions)
    for identity in stale_ids:
        del executions[identity]
    for member in comparison.missing:
        executions[member.identity] = pending_execution(member)
    for change in (*comparison.recipe_changed, *comparison.policy_changed):
        executions[change.current.identity] = changed_execution(change)
    commands = dict(state.commands)
    commands[cid] = PyrunCommand(executions)
    candidate_state = PyrunFile(state.path, state.entry_root, commands)
    return validated_pyrun_serialization(candidate_state, project_root=project)


def _publish_candidates(
    entry: EntryContext,
    data_text: str | None,
    pyrun_text: str,
    failures: tuple[tuple[str, CommandDiscoveryFailure], ...],
    *,
    dry_run: bool,
) -> ActionResult:
    data_path = entry.root / "data.json"
    state_path = entry.root / PYRUN_FILENAME
    before_data = data_path.read_text(encoding="utf-8") if data_path.exists() else None
    before_pyrun = (
        state_path.read_text(encoding="utf-8") if state_path.exists() else None
    )
    records: list[dict[str, object]] = [
        _diff_record(data_path, before_data, data_text),
        _diff_record(state_path, before_pyrun, pyrun_text),
    ]
    records.extend(_failure_record(document, failure) for document, failure in failures)
    changed = before_data != data_text or before_pyrun != pyrun_text
    if not dry_run and changed:
        residue = begin_registry_transaction(entry.log.root, entry.id)
        try:
            atomic_write_texts({data_path: data_text, state_path: pyrun_text})
        except PublicationError as error:
            if error.rollback_complete:
                finish_guarded_publication(residue)
            raise
        finish_guarded_publication(residue)
    return ActionResult(
        "command.sync",
        "dry-run" if dry_run else "changed" if changed else "unchanged",
        "command.sync.dry-run" if dry_run else "command.sync.complete",
        changed,
        (data_path.as_posix(), state_path.as_posix()),
        tuple(records),
    )


def _index_entry(
    entry: EntryContext, project: Path, data: DataFile | None
) -> tuple[tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...]:
    summary = entry.log.summary.read_text(encoding="utf-8")
    paths: list[Path] = []
    for target in index_entry_documents(summary):
        path = entry.log.summary.parent / target
        identity = parse_entry_document_name(path.name)
        if identity is None or identity.id != entry.id:
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != entry.root
        ):
            raise ActionError("entry.identity.unresolved", str(path))
        paths.append(path.resolve())
    if not paths:
        raise ActionError("entry.identity.unresolved", entry.id)
    result = []
    for path in sorted(set(paths)):
        context = CommandDeclarationContext(
            entry.log.root.as_posix(),
            entry.id,
            path.relative_to(entry.log.root).as_posix(),
            entry.root,
            entry.log.root,
            project,
            data,
        )
        result.append(
            (path, context, index_commands(path.read_text(encoding="utf-8"), context))
        )
    return tuple(result)


def _selected_declarations(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    cid: str,
) -> tuple[CommandDeclaration, ...]:
    selected = tuple(
        declaration
        for _, _, result in indexed
        for declaration in result.declarations
        if declaration.parsed.cid == cid
    )
    if not selected:
        raise ActionError("command.sync.cid.missing", cid)
    owners = {
        (item.document, item.fence, item.parsed.authored_group) for item in selected
    }
    if len(owners) != 1:
        raise ActionError("command.sync.cid.ambiguous", cid)
    owner = next(iter(owners))
    all_owner_cids = {
        item.parsed.cid
        for _, _, result in indexed
        for item in result.declarations
        if (item.document, item.fence, item.parsed.authored_group) == owner
    }
    if all_owner_cids != {cid}:
        raise ActionError("command.sync.cid.unstable", cid)
    same_fence_owners = {
        item.parsed.authored_group
        for _, _, result in indexed
        for item in result.declarations
        if (item.document, item.fence) == owner[:2]
    }
    if len(same_fence_owners) != 1:
        raise ActionError("command.sync.fence.multiple_commands", cid)
    return selected


def _candidate_data(
    entry: EntryContext,
    current: DataFile | None,
    selected: tuple[CommandDeclaration, ...],
    arguments: CommandSyncArguments,
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
) -> DataFile | None:
    items = {item.name: item for item in current.inputs} if current is not None else {}
    _apply_local_declarations(
        entry,
        items,
        _LocalDeclarationAssertions(
            arguments.add_origins, "--add-origin", "file", True
        ),
    )
    _apply_local_declarations(
        entry,
        items,
        _LocalDeclarationAssertions(
            arguments.add_origin_directories,
            "--add-origin-directory",
            "directory",
            True,
        ),
    )
    _apply_git_origins(entry, items, arguments.add_origin_git)
    _apply_local_declarations(
        entry,
        items,
        _LocalDeclarationAssertions(
            arguments.add_generated, "--add-generated", "file", False
        ),
    )
    _apply_local_declarations(
        entry,
        items,
        _LocalDeclarationAssertions(
            arguments.add_generated_directories,
            "--add-generated-directory",
            "directory",
            False,
        ),
    )
    _apply_from_entries(entry, items, arguments.add_from_entries)
    _apply_target_changes(
        entry, items, arguments.cid, arguments.target_changes, indexed
    )
    if not items:
        return None
    return data_file_from_inputs(
        entry.root / "data.json",
        entry_root=entry.root,
        inputs=tuple(items[name] for name in sorted(items)),
    )


def _apply_local_declarations(
    entry: EntryContext,
    items: dict[str, InputResource],
    assertions: _LocalDeclarationAssertions,
) -> None:
    for raw in assertions.values:
        name, target = _mapping(raw, assertions.flag)
        location = normalize_input_location(target, entry_root=entry.root)
        _require_local_target(
            entry, name, location, kind=assertions.kind, origin=assertions.origin
        )
        candidate = (
            build_local_input(
                name, assertions.kind, location, entry_root=entry.root, origin=True
            )
            if assertions.origin
            else build_declared_generated(
                name, assertions.kind, location, entry_root=entry.root
            )
        )
        _ensure_declaration(entry, items, candidate, assertions.flag)


def _apply_git_origins(
    entry: EntryContext,
    items: dict[str, InputResource],
    values: tuple[str, ...],
) -> None:
    for raw in values:
        name, target = _mapping(raw, "--add-origin-git")
        commit, location = _git_target(target, "--add-origin-git")
        normalized = normalize_input_location(location, entry_root=entry.root)
        candidate = build_git_repository_input(
            name, normalized, commit, entry_root=entry.root
        )
        observe_fingerprint(candidate)
        _ensure_declaration(entry, items, candidate, "--add-origin-git")


def _apply_from_entries(
    entry: EntryContext,
    items: dict[str, InputResource],
    values: tuple[str, ...],
) -> None:
    for raw in values:
        name, source_id = _mapping(raw, "--add-from-entry")
        if source_id == entry.id:
            raise ActionError("data.reference.invalid", "source and destination match")
        source_entry = resolve_entry(entry.log, source_id)
        source_data = _load_data(source_entry)
        source = source_data.by_name.get(name) if source_data is not None else None
        if source is None or source.origin or source.reference_entry is not None:
            raise ActionError(
                "data.reference.source_invalid",
                f"{source_id} must directly declare generated data named {name}",
                records=({"entry": source_id, "name": name},),
                diagnostic_log=entry.log.root,
            )
        _ensure_declaration(
            entry,
            items,
            replace(source, reference_entry=source_id),
            "--add-from-entry",
        )


def _apply_target_changes(
    entry: EntryContext,
    items: dict[str, InputResource],
    cid: str,
    values: tuple[str, ...],
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
) -> None:
    for raw in values:
        name, target = _mapping(raw, "--change-target")
        existing = items.get(name)
        if existing is None:
            raise ActionError("data.input.missing", name)
        if existing.reference_entry is not None:
            raise ActionError(
                "command.sync.target.shared",
                "cross-entry sources are changed through log data update",
                records=({"name": name, "owner": "log data update"},),
                diagnostic_log=entry.log.root,
            )
        if name not in _declaration_names_for_cid(indexed, cid):
            raise ActionError(
                "command.sync.target.not_owned",
                f"{cid} does not use {name}",
                records=({"cid": cid, "name": name, "owner": "log data update"},),
                diagnostic_log=entry.log.root,
            )
        if existing.kind == "git-repository":
            commit, location = _git_target(target, "--change-target")
            candidate = build_git_repository_input(
                name,
                normalize_input_location(location, entry_root=entry.root),
                commit,
                entry_root=entry.root,
            )
            observe_fingerprint(candidate)
        else:
            location = normalize_input_location(target, entry_root=entry.root)
            _require_local_target(
                entry, name, location, kind=existing.kind, origin=existing.origin
            )
            base = build_local_input(
                name,
                existing.kind,
                location,
                entry_root=entry.root,
                origin=existing.origin,
            )
            candidate = replace(
                base,
                identity=existing.identity,
                comparison=existing.comparison,
            )
        if candidate == existing:
            continue
        consumers = sorted(_declaration_cids_for_name(indexed, name) - {cid})
        references = list(_cross_entry_references(entry, name))
        evidence = _evidence_use_ids(entry, name)
        if consumers or references or evidence:
            raise ActionError(
                "command.sync.target.shared",
                "shared declarations are changed through log data update",
                records=(
                    {
                        "command_consumers": consumers,
                        "cross_entry_consumers": references,
                        "evidence_consumers": list(evidence),
                        "name": name,
                        "owner": "log data update",
                    },
                ),
                diagnostic_log=entry.log.root,
            )
        items[name] = candidate


def _git_target(value: str, flag: str) -> tuple[str, str]:
    if ":" not in value:
        raise ActionError(
            "command.sync.arguments.invalid", f"{flag} requires NAME=COMMIT:PATH"
        )
    commit, location = value.split(":", 1)
    if not commit or not location:
        raise ActionError(
            "command.sync.arguments.invalid", f"{flag} requires NAME=COMMIT:PATH"
        )
    return commit, location


def _declaration_cids_for_name(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    name: str,
) -> set[str]:
    cids: set[str] = set()
    for _, _, result in indexed:
        for declaration in result.declarations:
            command = declaration.parsed
            values = [value for _, value in command.capture_outputs]
            values.extend(item.value for item in command.options)
            values.extend(command.positionals)
            if any(
                (parts := input_token_parts(value)) is not None and parts[0] == name
                for value in values
            ):
                cids.add(command.cid)
    return cids


def _declaration_names_for_cid(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    cid: str,
) -> set[str]:
    names: set[str] = set()
    for _, _, result in indexed:
        for declaration in result.declarations:
            if declaration.parsed.cid != cid:
                continue
            command = declaration.parsed
            values = [value for _, value in command.capture_outputs]
            values.extend(item.value for item in command.options)
            values.extend(command.positionals)
            names.update(
                parts[0]
                for value in values
                if (parts := input_token_parts(value)) is not None
            )
    return names


def _missing_declarations(
    selected: Iterable[CommandDeclaration], data: DataFile | None
) -> tuple[dict[str, object], ...]:
    existing = set(data.by_name) if data is not None else set()
    missing: dict[str, str] = {}
    for declaration in selected:
        command = declaration.parsed
        values: list[tuple[str, str | None]] = [
            (value, "output") for _, value in command.capture_outputs
        ]
        values.extend(
            (
                occurrence.value,
                command.runner_roles.get(
                    occurrence.name,
                    automatic_option_role(occurrence.name),
                ),
            )
            for occurrence in command.options
        )
        values.extend(
            (value, command.runner_roles.get(f"@{number}"))
            for number, value in enumerate(command.positionals, 1)
        )
        for value, role in values:
            parts = input_token_parts(value)
            if parts is None or parts[0] in existing:
                continue
            missing[parts[0]] = (
                "--add-generated"
                if role is not None and role.startswith("output")
                else "--add-origin"
            )
    return tuple(
        {
            "name": name,
            "required_flags": [
                f"{flag} {name}=PATH",
                f"{flag}-directory {name}=PATH",
            ],
            "role": "generated" if flag == "--add-generated" else "origin",
        }
        for name, flag in sorted(missing.items())
    )


def _require_output_safety(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    invocations: tuple[Invocation, ...],
    selected: tuple[Invocation, ...],
) -> None:
    index = build_producer_index(invocations)
    for invocation in selected:
        targets = {item.path for item in invocation.outputs}
        targets.update(
            item.root
            for item in invocation.collections
            if item.direction == "output" and item.root is not None
        )
        for target in targets:
            canonical = Path(target).absolute().as_posix()
            rejected = {
                path
                for _, _, result in indexed
                for path, _ in result.rejected_outputs
                if _paths_overlap(canonical, path)
            }
            if rejected:
                raise ActionError(
                    "command.sync.producer.unresolved",
                    f"rejected command may own selected output: {target}",
                )
            matches = index.lookup(canonical)
            owners = {item.identity for item in index.outputs.get(canonical, ())}
            owners.update(item.producer.identity for item in matches)
            if len(owners) != 1 or invocation.identity not in owners:
                raise ActionError(
                    "command.sync.producer.ambiguous",
                    f"selected output has {len(owners)} current producers: {target}",
                )


def _require_requested_declarations_consumed(
    entry: EntryContext,
    arguments: CommandSyncArguments,
    selected: tuple[Invocation, ...],
) -> None:
    requested = {
        _mapping(value, flag)[0]
        for flag, values in (
            ("--add-origin", arguments.add_origins),
            ("--add-origin-directory", arguments.add_origin_directories),
            ("--add-origin-git", arguments.add_origin_git),
            ("--add-generated", arguments.add_generated),
            ("--add-generated-directory", arguments.add_generated_directories),
            ("--add-from-entry", arguments.add_from_entries),
        )
        for value in values
    }
    consumed = {
        relationship.input_resource.name
        for invocation in selected
        for relationship in (*invocation.inputs, *invocation.outputs)
        if relationship.input_resource is not None
    }
    unused = sorted(requested - consumed)
    if unused:
        raise ActionError(
            "command.sync.declaration.unused",
            "added declarations must be consumed by the selected command",
            records=tuple(
                {"cid": arguments.cid, "name": name, "owner": "log data update"}
                for name in unused
            ),
            diagnostic_log=entry.log.root,
        )


def _require_origin_boundaries(
    data: DataFile | None,
    invocations: tuple[Invocation, ...],
    added_names: tuple[str, ...],
) -> None:
    if data is None or not added_names:
        return
    index = build_producer_index(invocations)
    for name in added_names:
        resource = data.by_name[name]
        # Pinned Git inputs identify commits, not the locator's live directory.
        if resource.kind == "git-repository":
            continue
        canonical = Path(resource.canonical_target).absolute().as_posix()
        owners = {item.identity for item in index.outputs.get(canonical, ())}
        owners.update(item.producer.identity for item in index.lookup(canonical))
        if owners:
            raise ActionError(
                "command.sync.origin.produced",
                f"origin {name} has a current recorded producer",
            )


def _require_generated_boundaries(
    entry: EntryContext,
    current: DataFile | None,
    candidate: DataFile | None,
    arguments: CommandSyncArguments,
    invocations: tuple[Invocation, ...],
) -> None:
    if candidate is None:
        return
    names = {
        _mapping(value, flag)[0]
        for flag, values in (
            ("--add-generated", arguments.add_generated),
            ("--add-generated-directory", arguments.add_generated_directories),
            ("--change-target", arguments.target_changes),
        )
        for value in values
    }
    index = build_producer_index(invocations)
    selected_ids = {item.identity for item in invocations if item.cid == arguments.cid}
    for name in sorted(names):
        resource = candidate.by_name[name]
        if resource.origin or resource.reference_entry is not None:
            continue
        target = resource.canonical_target
        owners = {item.identity for item in index.outputs.get(target, ())}
        owners.update(item.producer.identity for item in index.lookup(target))
        if not owners:
            raise ActionError(
                "producer.missing",
                f"generated data {name} has no recorded producer; "
                "author its command first",
                records=(
                    {"name": name, "target": target, "owner": "log command sync"},
                ),
                diagnostic_log=entry.log.root,
            )
        if len(owners) != 1:
            raise ActionError(
                "command.sync.producer.ambiguous",
                f"generated data {name} has several recorded producers",
                records=({"name": name, "producers": sorted(owners)},),
                diagnostic_log=entry.log.root,
            )
        if (
            current is None or name not in current.by_name
        ) and not owners <= selected_ids:
            raise ActionError(
                "command.sync.producer.not_selected",
                f"bootstrap {name} through its producer command",
                records=(
                    {
                        "name": name,
                        "producers": sorted(owners),
                        "owner": "log command sync",
                    },
                ),
                diagnostic_log=entry.log.root,
            )


def _paths_overlap(left: str, right: str) -> bool:
    first = Path(left).absolute()
    second = Path(right).absolute()
    return first == second or first in second.parents or second in first.parents


def _materialize(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    data: DataFile | None,
) -> tuple[tuple[Invocation, ...], tuple[tuple[str, CommandDiscoveryFailure], ...]]:
    by_document: list[tuple[Invocation, ...]] = []
    failures: list[tuple[str, CommandDiscoveryFailure]] = []
    for _, context, result in indexed:
        candidate_context = replace(context, data_file=data)
        discovered = materialize_declared_commands(result, candidate_context)
        by_document.append(discovered.invocations)
        failures.extend((context.document, item) for item in discovered.failures)
    return order_invocations(by_document), tuple(failures)


def _cross_entry_references(entry: EntryContext, name: str) -> tuple[str, ...]:
    found = []
    for root in sorted(entry.root.parent.iterdir()):
        if root == entry.root or root.is_symlink() or not root.is_dir():
            continue
        path = root / "data.json"
        if path.is_symlink() or not path.is_file():
            continue
        data = load_data_file(path, entry_root=root)
        resource = data.by_name.get(name)
        if resource is not None and resource.reference_entry == entry.id:
            found.append(root.name)
    return tuple(found)


def _evidence_use_ids(entry: EntryContext, name: str) -> tuple[str, ...]:
    path = entry.root / "evidence.json"
    if not path.exists() and not path.is_symlink():
        return ()
    evidence = load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
    return tuple(
        record.id
        for record in evidence.records
        if any(
            (parts := input_token_parts(source.source)) is not None and parts[0] == name
            for source in record.sources
        )
    )


def _load_data(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return load_data_file(path, entry_root=entry.root)
    except DataContractError as error:
        raise ActionError(
            "command.sync.registry.invalid",
            "the owned data registry requires direct Repair before sync",
            records=(
                {
                    "code": error.code,
                    "observed": error.observed,
                    "registry": error.subject,
                    "required_action": "direct Repair",
                },
            ),
            diagnostic_log=entry.log.root,
        ) from error


def _failure_record(
    document: str, failure: CommandDiscoveryFailure
) -> dict[str, object]:
    return {
        "code": failure.error.code,
        "document": document,
        "fence": failure.fence,
        "observed": failure.error.observed,
        "ordinal": failure.ordinal,
        "status": "unrelated-failure",
    }


def _diff_record(
    path: Path, before: str | None, after: str | None
) -> dict[str, object]:
    return {
        "diff": "".join(
            unified_diff(
                (before or "").splitlines(True),
                (after or "").splitlines(True),
                fromfile=path.as_posix(),
                tofile=path.as_posix(),
            )
        ),
        "path": path.as_posix(),
    }
