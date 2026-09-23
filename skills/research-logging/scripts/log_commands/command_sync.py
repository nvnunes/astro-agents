"""Transactional synchronization of selected Markdown-owned command buckets."""

from __future__ import annotations

import re
from contextlib import nullcontext
from dataclasses import dataclass, replace
from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from effective_code import (
    EffectiveCodeError,
    UnsupportedLocation,
    analyze_effective_code,
)
from python_execution import PythonExecutionContext
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
    _command_fences,
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
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PYRUN_FILENAME,
    PyrunCommand,
    PyrunFile,
    changed_execution,
    compare_command,
    empty_pyrun_state,
    load_pyrun_state,
    pending_execution,
    script_target_path,
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
from .graph_state import (
    authored_evidence_uses,
    declaration_uses,
    describe_uses,
    material_consumers,
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
    before_state: PyrunFile
    candidate: DataFile | None
    declarations: tuple[CommandDeclaration, ...]
    invocations: tuple[Invocation, ...]
    failures: tuple[tuple[str, CommandDiscoveryFailure], ...]
    selection: _Selection


@dataclass(frozen=True)
class _Selection:
    """Normalized, non-overlapping command identities in one change set."""

    selected: tuple[str, ...]
    renames: tuple[tuple[str, str], ...]
    deleted: tuple[str, ...]
    stale_acknowledged: frozenset[str]


@dataclass(frozen=True)
class _PublicationCandidate:
    """Complete generated command-sync state and diagnostics."""

    data_text: str | None
    pyrun_text: str | None
    failures: tuple[tuple[str, CommandDiscoveryFailure], ...]
    warnings: tuple[dict[str, object], ...]
    changes: tuple[dict[str, object], ...]


def sync_command(
    entry: EntryContext,
    arguments: CommandSyncArguments,
) -> ActionResult:
    """Synchronize selected CIDs and lifecycle edits in one publication."""

    project = resolve_project_root(entry.root)
    try:
        selection = _selection(arguments)
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
        prepared = _prepare_command(entry, project, arguments, selection)
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


def _selection(arguments: CommandSyncArguments) -> _Selection:
    """Reject contradictory selectors before reading or writing registry state."""

    renames = {_mapping(raw, "--rename") for raw in arguments.renames}
    sources = [old for old, _ in renames]
    targets = [new for _, new in renames]
    if (
        len(set(sources)) != len(sources)
        or len(set(targets)) != len(targets)
        or set(sources) & set(targets)
    ):
        raise ActionError(
            "command.sync.selection.conflict",
            "renames must be one-to-one without chains or cycles",
        )
    selected = set(arguments.cids) | set(targets)
    deleted = set(arguments.deletions)
    if not selected and not deleted:
        raise ActionError(
            "cli.arguments.invalid",
            "command sync requires --cid, --rename, or --delete",
        )
    if deleted & (selected | set(sources)) or set(sources) & set(arguments.cids):
        raise ActionError(
            "command.sync.selection.conflict",
            "a command ID cannot be selected for incompatible actions",
        )
    stale_acknowledged = frozenset(arguments.stale_execution_deletions)
    if stale_acknowledged - selected:
        raise ActionError(
            "command.sync.selection.conflict",
            "--delete-stale-executions must name a selected final CID",
        )
    return _Selection(
        tuple(sorted(selected)),
        tuple(sorted(renames)),
        tuple(sorted(deleted)),
        stale_acknowledged,
    )


def _prepare_command(
    entry: EntryContext,
    project: Path,
    arguments: CommandSyncArguments,
    selection: _Selection,
) -> _PreparedCommand:
    with (
        nullcontext()
        if arguments.dry_run
        else entry_locks(entry.log, (entry,), timeout_seconds=10)
    ):
        current_data = _load_data(entry)
        current_state = _load_state(entry, project)
        indexed = _index_entry(entry, project, current_data)
    _require_removed_markdown_absent(indexed, selection)
    selected = tuple(
        declaration
        for cid in selection.selected
        for declaration in _selected_declarations(indexed, cid)
    )
    candidate_data = _candidate_data(
        entry,
        current_data,
        arguments,
        indexed,
        selection,
    )
    candidate_data = _remove_deleted_declarations(
        entry, current_state, candidate_data, selection
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
        item for item in invocations if item.cid in selection.selected
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
        if selection.selected:
            raise ActionError("command.sync.cid.missing", str(selection.selected))
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
        current_data,
        current_state,
        candidate_data,
        selected,
        selected_invocations,
        failures,
        selection,
    )


def _load_state(entry: EntryContext, project: Path) -> PyrunFile:
    path = entry.root / PYRUN_FILENAME
    if not path.exists() and not path.is_symlink():
        return empty_pyrun_state(entry.root)
    try:
        return load_pyrun_state(path, entry_root=entry.root, project_root=project)
    except MechanicalContractError as error:
        raise ActionError(
            "command.sync.registry.invalid",
            "the owned pyrun registry requires direct Repair before sync",
            records=({"code": error.code, "registry": error.subject},),
            diagnostic_log=entry.log.root,
        ) from error


def _deleted_outputs(
    entry: EntryContext, state: PyrunFile, selection: _Selection
) -> set[str]:
    project = resolve_project_root(entry.root)
    return {
        output_target_path(path, entry_root=entry.root, project_root=project)
        .resolve()
        .as_posix()
        for cid in selection.deleted
        for execution in state.commands.get(cid, PyrunCommand({})).executions.values()
        for path, _ in execution.recipe.outputs
    }


def _remove_deleted_declarations(
    entry: EntryContext,
    state: PyrunFile,
    candidate: DataFile | None,
    selection: _Selection,
) -> DataFile | None:
    if not selection.deleted:
        return candidate
    outputs = _deleted_outputs(entry, state, selection)
    owned = tuple(
        item
        for item in (candidate.inputs if candidate is not None else ())
        if not item.origin
        and item.reference_entry is None
        and item.canonical_target in outputs
    )
    blocked: list[dict[str, object]] = []
    for output in sorted(outputs):
        blocked.extend(
            use
            for use in material_consumers(entry, Path(output))
            if not (
                use.get("entry") == entry.id and use.get("command") in selection.deleted
            )
        )
    for item in owned:
        blocked.extend(
            use
            for use in declaration_uses(entry, item.name)
            if not (
                use.get("entry") == entry.id and use.get("command") in selection.deleted
            )
        )
    if blocked:
        raise ActionError(
            "command.sync.outputs_in_use",
            "downstream consumers still use deleted command outputs"
            + describe_uses(tuple(blocked)),
            records=tuple(blocked),
        )
    if candidate is None:
        return None
    remaining = tuple(item for item in candidate.inputs if item not in owned)
    return (
        data_file_from_inputs(
            entry.root / "data.json", entry_root=entry.root, inputs=remaining
        )
        if remaining
        else None
    )


def _require_removed_markdown_absent(
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    selection: _Selection,
) -> None:
    removed = set(selection.deleted) | {old for old, _ in selection.renames}
    found = sorted(
        {
            declaration.parsed.cid
            for _, _, result in indexed
            for declaration in result.declarations
            if declaration.parsed.cid in removed
        }
    )
    for path, _, result in indexed:
        if not result.failures:
            continue
        bodies = tuple(
            body for body, _, _ in _command_fences(path.read_text(encoding="utf-8"))
        )
        for failure in result.failures:
            body = bodies[failure.fence - 1]
            found.extend(
                cid for cid in sorted(removed) if _failed_fence_mentions_cid(body, cid)
            )
    if found:
        raise ActionError(
            "command.sync.markdown_present",
            f"remove old command blocks from Markdown first: {sorted(set(found))}",
        )


def _failed_fence_mentions_cid(body: str, cid: str) -> bool:
    """Conservatively recognize a removed CID inside a failed command fence."""

    starts = tuple(
        match.start()
        for match in re.finditer(r"(?<![A-Za-z0-9_./-])(?:\./)?pyrun(?=\s|$)", body)
    )
    for index, start in enumerate(starts):
        segment = body[start : starts[index + 1] if index + 1 < len(starts) else None]
        explicit = re.findall(r"(?<!\S)--cid(?:\s+|=)[\"']?([A-Za-z0-9_-]+)", segment)
        stems = {
            Path(value).stem
            for value in re.findall(
                r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_./-]+\.py)", segment
            )
        }
        if explicit:
            if any(
                value == cid
                or value.isdecimal()
                and any(f"{stem}-{int(value)}" == cid for stem in stems)
                for value in explicit
            ):
                return True
        elif cid in stems:
            return True
    return False


def _finish_command(
    entry: EntryContext,
    project: Path,
    arguments: CommandSyncArguments,
    prepared: _PreparedCommand,
) -> ActionResult:
    selection = prepared.selection
    names = {
        parts[0]
        for declaration in prepared.declarations
        for value in declaration.tokens
        if (parts := input_token_parts(value)) is not None
    }
    fresh_data = _load_data(entry)
    data = merge_data(
        prepared.before, prepared.candidate, fresh_data, names=names, entry=entry
    )
    indexed = _index_entry(entry, project, data)
    _recheck_target_change_ownership(entry, fresh_data, data, arguments, indexed)
    _require_removed_markdown_absent(indexed, selection)
    selected = tuple(
        declaration
        for cid in selection.selected
        for declaration in _selected_declarations(indexed, cid)
    )
    require_unchanged(
        tuple((item.document, item.parsed) for item in prepared.declarations),
        tuple((item.document, item.parsed) for item in selected),
        f"{entry.id}/{selection.selected} Markdown command",
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
        item for item in invocations if item.cid in selection.selected
    )
    current_state = _load_state(entry, project)
    for cid in (
        set(selection.selected)
        | set(selection.deleted)
        | {old for old, _ in selection.renames}
    ):
        before_bucket = prepared.before_state.commands.get(cid)
        current_bucket = current_state.commands.get(cid)
        if before_bucket != current_bucket:
            redundant = False
            if cid in selection.selected and before_bucket is None:
                comparison = compare_command(
                    current_state,
                    cid,
                    tuple(item for item in selected_invocations if item.cid == cid),
                    project_root=project,
                )
                redundant = not (
                    comparison.missing
                    or comparison.stale
                    or comparison.recipe_changed
                    or comparison.policy_changed
                )
            if not redundant:
                require_unchanged(
                    before_bucket,
                    current_bucket,
                    f"{entry.id}/{cid} execution state",
                )
    _remove_deleted_declarations(entry, current_state, _load_data(entry), selection)
    _require_output_safety(indexed, invocations, selected_invocations)
    _require_generated_boundaries(entry, prepared.before, data, arguments, invocations)
    warnings = _effective_code_warnings(entry, project, selected_invocations)
    pyrun_text, stale_records = _candidate_pyrun_text(
        entry,
        project,
        current_state,
        selection,
        selected_invocations,
    )
    data_text = data.canonical_json() if data is not None else None
    require_artifact_access(
        project,
        writes=(
            *data_change_paths(prepared.before, data),
            *(
                Path(path)
                for path in sorted(_deleted_outputs(entry, current_state, selection))
            ),
        ),
    )
    renamed_from = {new: old for old, new in selection.renames}
    change_rows: list[dict[str, object]] = []
    for cid in selection.selected:
        row: dict[str, object] = {"cid": cid, "synchronized": True}
        if cid in renamed_from:
            row["renamed_from"] = renamed_from[cid]
        change_rows.append(row)
    change_rows.extend({"cid": cid, "deleted": True} for cid in selection.deleted)
    change_rows.extend(
        {"disconnected": path}
        for path in sorted(_deleted_outputs(entry, current_state, selection))
        if Path(path).exists()
    )
    candidate = _PublicationCandidate(
        data_text, pyrun_text, failures, warnings, tuple(change_rows) + stale_records
    )
    return _publish_candidates(entry, candidate, dry_run=arguments.dry_run)


def _effective_code_warnings(
    entry: EntryContext,
    project: Path,
    invocations: tuple[Invocation, ...],
) -> tuple[dict[str, object], ...]:
    """Return bounded warnings for scripts that cannot be fingerprinted."""

    result: list[dict[str, object]] = []
    scripts: dict[Path, tuple[tuple[str, str], ...]] = {}
    for invocation in invocations:
        if invocation.script is None:
            continue
        scripts.setdefault(
            script_target_path(
                invocation.script,
                entry_root=entry.root,
                project_root=project,
            ),
            invocation.environment,
        )
    for script, environment in sorted(
        scripts.items(), key=lambda item: item[0].as_posix()
    ):
        relative = _project_relative(script, project)
        if "PYTHONPATH" in dict(environment):
            result.append(
                _effective_code_warning(
                    relative,
                    UnsupportedLocation(
                        relative,
                        0,
                        "import_path_environment",
                        "explicit PYTHONPATH changes project-local import resolution",
                    ),
                )
            )
            continue
        try:
            python_context = PythonExecutionContext.for_research_script(
                script,
                entry_root=entry.root,
                log_root=entry.log.root,
                project_root=project,
            )
            analysis = analyze_effective_code(
                script,
                project_root=project,
                import_roots=python_context.import_roots,
            )
        except EffectiveCodeError as error:
            result.append(
                _effective_code_warning(
                    relative,
                    UnsupportedLocation(
                        error.path or relative,
                        error.line,
                        error.code,
                        error.detail,
                    ),
                    code="command.sync.effective_code.unavailable",
                    remediation=(
                        "repair the analysis failure, then run pyrun to record "
                        "the fingerprint"
                    ),
                )
            )
            continue
        for item in analysis.unsupported:
            result.append(
                _effective_code_warning(
                    relative,
                    item,
                )
            )
        if analysis.unsupported_truncated:
            result.append(
                _effective_code_warning(
                    relative,
                    UnsupportedLocation(
                        relative,
                        0,
                        "unsupported_locations_truncated",
                        "additional unsupported locations were omitted",
                    ),
                )
            )
    return tuple(result)


def _effective_code_warning(
    script: str,
    issue: UnsupportedLocation,
    *,
    code: str = "command.sync.effective_code.unsupported",
    remediation: str = "run pyrun after repairing the unsupported construct",
) -> dict[str, object]:
    return {
        "code": code,
        "consequence": (
            "effective-code currentness is unavailable; reproduction will select "
            "this command on every incremental plan until the script and environment "
            f"can be analyzed; {remediation}"
        ),
        "construct": issue.construct,
        "detail": issue.detail,
        "line": issue.line,
        "location": issue.path,
        "script": script,
        "status": "warning",
    }


def _project_relative(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


def _candidate_pyrun_text(
    entry: EntryContext,
    project: Path,
    state: PyrunFile,
    selection: _Selection,
    invocations: tuple[Invocation, ...],
) -> tuple[str | None, tuple[dict[str, object], ...]]:
    commands = dict(state.commands)
    already_renamed = _apply_command_renames(commands, selection.renames)
    stale_records: list[dict[str, object]] = []
    for cid in selection.deleted:
        commands.pop(cid, None)
    for cid in selection.selected:
        provisional = PyrunFile(state.path, state.entry_root, commands)
        members = tuple(item for item in invocations if item.cid == cid)
        comparison = compare_command(provisional, cid, members, project_root=project)
        stale = comparison.stale
        if stale and cid not in selection.stale_acknowledged:
            raise ActionError(
                "command.sync.execution.deletion_required",
                "acknowledge all stale executions for this CID",
                records=tuple(
                    {
                        "cid": cid,
                        "execution_id": item.identity,
                        "parameters": list(
                            recipe_script_parameters(item.execution.recipe.parameters)
                        ),
                        "retry_flag": f"--delete-stale-executions {cid}",
                        "script": item.execution.recipe.script,
                    }
                    for item in stale
                ),
                diagnostic_log=entry.log.root,
            )
        if cid in already_renamed and (
            comparison.missing
            or comparison.stale
            or comparison.recipe_changed
            or comparison.policy_changed
        ):
            raise ActionError(
                "command.sync.source.missing",
                f"renamed source is missing and {cid} is not already synchronized",
            )
        stale_records.extend(
            {
                "cid": cid,
                "execution_id": item.identity,
                "deleted_stale_execution": True,
                "parameters": list(
                    recipe_script_parameters(item.execution.recipe.parameters)
                ),
            }
            for item in stale
        )
        executions = dict(commands.get(cid, PyrunCommand({})).executions)
        for stale_member in stale:
            executions.pop(stale_member.identity)
        for missing_member in comparison.missing:
            executions[missing_member.identity] = pending_execution(missing_member)
        for changed_member in (
            *comparison.recipe_changed,
            *comparison.policy_changed,
        ):
            executions[changed_member.current.identity] = changed_execution(
                changed_member
            )
        commands[cid] = PyrunCommand(executions)
    return (
        (
            validated_pyrun_serialization(
                PyrunFile(state.path, state.entry_root, commands), project_root=project
            )
            if commands
            else None
        ),
        tuple(stale_records),
    )


def _apply_command_renames(
    commands: dict[str, PyrunCommand], renames: tuple[tuple[str, str], ...]
) -> set[str]:
    """Move stored buckets and identify already-applied rename requests."""

    already_renamed: set[str] = set()
    for old, new in renames:
        if old in commands:
            if new in commands:
                raise ActionError("command.sync.destination.conflict", new)
            commands[new] = commands.pop(old)
        elif new not in commands:
            raise ActionError(
                "command.sync.source.missing",
                f"{old}: missing source and no synchronized destination {new}",
            )
        else:
            already_renamed.add(new)
    return already_renamed


def _publish_candidates(
    entry: EntryContext,
    candidate: _PublicationCandidate,
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
        _diff_record(data_path, before_data, candidate.data_text),
        _diff_record(state_path, before_pyrun, candidate.pyrun_text),
    ]
    records.extend(candidate.changes)
    records.extend(candidate.warnings)
    records.extend(
        _failure_record(document, failure) for document, failure in candidate.failures
    )
    updates = {
        path: text
        for path, before, text in (
            (data_path, before_data, candidate.data_text),
            (state_path, before_pyrun, candidate.pyrun_text),
        )
        if before != text
    }
    if not dry_run and updates:
        residue = begin_registry_transaction(entry.log.root, entry.id)
        try:
            atomic_write_texts(updates)
        except PublicationError as error:
            if error.rollback_complete:
                finish_guarded_publication(residue)
            raise
        finish_guarded_publication(residue)
    return ActionResult(
        "command.sync",
        "dry-run" if dry_run else "changed" if updates else "unchanged",
        "command.sync.dry-run" if dry_run else "command.sync.complete",
        bool(updates),
        tuple(path.as_posix() for path in updates),
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
    arguments: CommandSyncArguments,
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
    selection: _Selection,
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
        entry, items, set(selection.selected), arguments.target_changes, indexed
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
    selected_cids: set[str],
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
        if not any(
            name in _declaration_names_for_cid(indexed, cid) for cid in selected_cids
        ):
            raise ActionError(
                "command.sync.target.not_owned",
                f"selected commands do not use {name}",
                records=(
                    {
                        "cids": sorted(selected_cids),
                        "name": name,
                        "owner": "log data update",
                    },
                ),
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
        consumers = sorted(_declaration_cids_for_name(indexed, name) - selected_cids)
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


def _recheck_target_change_ownership(
    entry: EntryContext,
    fresh: DataFile | None,
    candidate: DataFile | None,
    arguments: CommandSyncArguments,
    indexed: tuple[
        tuple[Path, CommandDeclarationContext, CommandDeclarationResult], ...
    ],
) -> None:
    """Reject new unselected consumers before publishing a local target edit."""

    old = fresh.by_name if fresh else {}
    new = candidate.by_name if candidate else {}
    selected = set(_selection(arguments).selected)
    for raw in arguments.target_changes:
        name, _ = _mapping(raw, "--change-target")
        if resource_authority(old.get(name)) == resource_authority(new.get(name)):
            continue
        consumers = sorted(_declaration_cids_for_name(indexed, name) - selected)
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
                {
                    "cids": sorted({item.cid for item in selected}),
                    "name": name,
                    "owner": "log data update",
                }
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
    selected_ids = {
        item.identity
        for item in invocations
        if item.cid in _selection(arguments).selected
    }
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
    normalized = (
        load_evidence_file(path, log_root=entry.log.root, entry_root=entry.root).records
        if path.exists() or path.is_symlink()
        else ()
    )
    ids = {
        record.id
        for record in normalized
        if any(
            (parts := input_token_parts(source.source)) is not None and parts[0] == name
            for source in record.sources
        )
    }
    ids.update(use["evidence"] for use in authored_evidence_uses(entry, name))
    return tuple(sorted(ids))


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
