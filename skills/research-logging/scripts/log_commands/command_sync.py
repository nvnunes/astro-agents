"""Transactional synchronization of one Markdown-owned command bucket."""

from __future__ import annotations

from dataclasses import replace
from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from research_log_data import (
    DataContractError,
    DataFile,
    InputResource,
    build_declared_generated,
    build_local_input,
    data_file_from_inputs,
    input_token_parts,
    load_data_file,
    normalize_input_location,
)
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

from .context import EntryContext, parse_entry_document_name, resolve_project_root
from .model import ActionError, ActionResult, CommandSyncArguments
from .storage import PublicationError, atomic_write_texts, entry_lock


def sync_command(
    entry: EntryContext,
    arguments: CommandSyncArguments,
) -> ActionResult:
    """Synchronize one selected CID and its supplemental data declarations."""

    project = resolve_project_root(entry.root)
    try:
        if arguments.dry_run:
            return _sync_locked(entry, project, arguments)
        with entry_lock(entry):
            return _sync_locked(entry, project, arguments)
    except ActionError:
        raise
    except (DataContractError, MechanicalContractError, OSError, UnicodeError) as error:
        raise ActionError("command.sync.unavailable", str(error)) from error


def _sync_locked(
    entry: EntryContext,
    project: Path,
    arguments: CommandSyncArguments,
) -> ActionResult:
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
    if not selected_invocations:
        raise ActionError("command.sync.cid.missing", arguments.cid)
    try:
        validate_command_structure(invocations)
    except MechanicalContractError as error:
        raise ActionError("command.sync.structure.invalid", str(error)) from error
    relevant_failures = tuple(
        failure
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
                _failure_record(document, failure) for document, failure in failures
            ),
            diagnostic_log=entry.log.root,
        )
    _require_safe_removals(entry, candidate_data, arguments.removals, invocations)
    _require_safe_renames(
        entry, current_data, arguments.renames, arguments.cid, invocations
    )
    _require_output_safety(indexed, invocations, selected_invocations)
    _require_origin_boundaries(
        candidate_data,
        invocations,
        tuple(_mapping(value, "--add-origin")[0] for value in arguments.add_origins),
    )

    pyrun_text = _candidate_pyrun_text(
        entry,
        project,
        arguments.cid,
        selected_invocations,
        arguments.retirements,
    )
    data_text = candidate_data.canonical_json() if candidate_data is not None else None
    return _publish_candidates(
        entry,
        data_text,
        pyrun_text,
        failures,
        dry_run=arguments.dry_run,
    )


def _candidate_pyrun_text(
    entry: EntryContext,
    project: Path,
    cid: str,
    invocations: tuple[Invocation, ...],
    retirements: tuple[str, ...],
) -> str:
    path = entry.root / PYRUN_FILENAME
    state = (
        load_pyrun_state(path, entry_root=entry.root, project_root=project)
        if path.exists() or path.is_symlink()
        else empty_pyrun_state(entry.root)
    )
    comparison = compare_command(state, cid, invocations, project_root=project)
    stale_ids = tuple(item.identity for item in comparison.stale)
    if set(retirements) != set(stale_ids) or len(retirements) != len(set(retirements)):
        raise ActionError(
            "command.sync.retirement.required",
            "acknowledge all and only stale execution IDs",
            records=tuple(
                {
                    "cid": item.cid,
                    "execution_id": item.identity,
                    "parameters": list(
                        recipe_script_parameters(item.execution.recipe.parameters)
                    ),
                    "retry_flag": f"--retire {item.identity}",
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
    _apply_renames(entry, items, arguments.cid, arguments.renames, indexed)
    _apply_removals(entry, items, arguments.removals, indexed)
    _apply_origins(entry, items, arguments.add_origins)
    _apply_generated(entry, items, selected, arguments.add_generated)
    if not items:
        return None
    return data_file_from_inputs(
        entry.root / "data.json",
        entry_root=entry.root,
        inputs=tuple(items[name] for name in sorted(items)),
    )


def _apply_renames(
    entry: EntryContext,
    items: dict[str, InputResource],
    cid: str,
    values: tuple[str, ...],
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
) -> None:
    for raw in values:
        old, new = _mapping(raw, "--rename")
        if old not in items:
            raise ActionError("data.input.missing", old)
        if items[old].reference_entry is not None:
            raise ActionError("data.reference.read_only", old)
        if new in items:
            raise ActionError("data.name.conflict", new)
        other_cids = (
            _declaration_cids_for_name(indexed, old)
            | _declaration_cids_for_name(indexed, new)
        ) - {cid}
        if (
            other_cids
            or _cross_entry_references(entry, old)
            or _evidence_uses(entry, old)
        ):
            raise ActionError("command.sync.rename.unsafe", "use log data rename")
        items[new] = replace(items.pop(old), name=new)


def _apply_removals(
    entry: EntryContext,
    items: dict[str, InputResource],
    names: tuple[str, ...],
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
) -> None:
    for name in names:
        if (
            _declaration_cids_for_name(indexed, name)
            or _cross_entry_references(entry, name)
            or _evidence_uses(entry, name)
        ):
            raise ActionError("command.sync.remove.unsafe", "use log data remove")
        if name in items:
            del items[name]


def _apply_origins(
    entry: EntryContext,
    items: dict[str, InputResource],
    values: tuple[str, ...],
) -> None:
    for raw in values:
        name, target = _mapping(raw, "--add-origin")
        if name in items:
            raise ActionError("data.name.conflict", name)
        location = normalize_input_location(target, entry_root=entry.root)
        path = Path(location) if Path(location).is_absolute() else entry.root / location
        kind = "file" if path.is_file() else "directory" if path.is_dir() else None
        if kind is None or path.is_symlink():
            raise ActionError("data.target.missing", target)
        items[name] = build_local_input(
            name, kind, location, entry_root=entry.root, origin=True
        )


def _apply_generated(
    entry: EntryContext,
    items: dict[str, InputResource],
    selected: tuple[CommandDeclaration, ...],
    values: tuple[str, ...],
) -> None:
    output_kinds = _selected_output_kinds(selected)
    for raw in values:
        name, target = _mapping(raw, "--add-generated")
        if name in items:
            raise ActionError("data.name.conflict", name)
        location = normalize_input_location(target, entry_root=entry.root)
        path = Path(location) if Path(location).is_absolute() else entry.root / location
        kind = "directory" if path.is_dir() else "file" if path.is_file() else None
        if kind is None:
            canonical = path.absolute().as_posix()
            kind = output_kinds.get(canonical, "file")
        items[name] = build_declared_generated(
            name, kind, location, entry_root=entry.root
        )


def _selected_output_kinds(
    selected: Iterable[CommandDeclaration],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for declaration in selected:
        for path, kind in declaration.outputs:
            if kind != "unknown":
                result[path] = kind
    return result


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
            "required_flag": f"{flag} {name}=PATH",
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
        canonical = Path(resource.canonical_target).absolute().as_posix()
        owners = {item.identity for item in index.outputs.get(canonical, ())}
        owners.update(item.producer.identity for item in index.lookup(canonical))
        if owners:
            raise ActionError(
                "command.sync.origin.produced",
                f"origin {name} has a current recorded producer",
            )


def _paths_overlap(left: str, right: str) -> bool:
    first = Path(left).absolute()
    second = Path(right).absolute()
    return first == second or first in second.parents or second in first.parents


def _mapping(value: str, flag: str) -> tuple[str, str]:
    if "=" not in value:
        raise ActionError(
            "command.sync.arguments.invalid", f"{flag} requires NAME=PATH"
        )
    left, right = value.split("=", 1)
    if not left or not right:
        raise ActionError(
            "command.sync.arguments.invalid", f"{flag} requires NAME=PATH"
        )
    return left, right


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


def _require_safe_renames(
    entry: EntryContext,
    current: DataFile | None,
    values: tuple[str, ...],
    cid: str,
    invocations: tuple[Invocation, ...],
) -> None:
    for raw in values:
        old, new = _mapping(raw, "--rename")
        if current is None or old not in current.by_name:
            raise ActionError("data.input.missing", old)
        if _cross_entry_references(entry, old):
            raise ActionError("command.sync.rename.unsafe", "use log data rename")
        if _evidence_uses(entry, old):
            raise ActionError("command.sync.rename.unsafe", "use log data rename")
        for invocation in invocations:
            names = _invocation_input_names(invocation)
            if invocation.cid != cid and (old in names or new in names):
                raise ActionError(
                    "command.sync.rename.unsafe", "sync affected CIDs first"
                )


def _require_safe_removals(
    entry: EntryContext,
    candidate: DataFile | None,
    names: tuple[str, ...],
    invocations: tuple[Invocation, ...],
) -> None:
    del candidate
    used = {
        name
        for invocation in invocations
        for name in _invocation_input_names(invocation)
    }
    for name in names:
        if (
            name in used
            or _cross_entry_references(entry, name)
            or _evidence_uses(entry, name)
        ):
            raise ActionError("command.sync.remove.unsafe", "use log data remove")


def _invocation_input_names(invocation: Invocation) -> set[str]:
    return {
        item.input_resource.name
        for item in invocation.inputs
        if item.input_resource is not None
    }


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


def _evidence_uses(entry: EntryContext, name: str) -> bool:
    path = entry.root / "evidence.json"
    if not path.exists() and not path.is_symlink():
        return False
    evidence = load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root)
    return any(
        (parts := input_token_parts(source.source)) is not None and parts[0] == name
        for record in evidence.records
        for source in record.sources
    )


def _load_data(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    return (
        load_data_file(path, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


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
