"""Markdown-first lifecycle of command-owned execution state."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

from research_log_data import DataFile, data_file_from_inputs, load_data_file
from validation.pyrun_outputs import output_target_path
from validation.pyrun_state import (
    PyrunFile,
    compare_command,
    load_pyrun_state,
    validated_pyrun_serialization,
)

from .command_sync import _index_entry, _materialize
from .context import EntryContext, resolve_project_root
from .graph_state import (
    declaration_uses,
    describe_uses,
    material_consumers,
    publish_updates,
)
from .model import ActionError, ActionResult
from .storage import entry_lock_under_log, log_lock


def _state(entry: EntryContext) -> PyrunFile | None:
    path = entry.root / "pyrun.json"
    return (
        load_pyrun_state(
            path, entry_root=entry.root, project_root=resolve_project_root(entry.root)
        )
        if path.exists() or path.is_symlink()
        else None
    )


def _data(entry: EntryContext) -> DataFile | None:
    path = entry.root / "data.json"
    return (
        load_data_file(path, entry_root=entry.root)
        if path.exists() or path.is_symlink()
        else None
    )


def list_commands(entry: EntryContext) -> ActionResult:
    """Expose the recipe and execution policy needed for Record and Repair."""

    state = _state(entry)
    records = (
        tuple(
            {
                "cid": cid,
                "execution_id": identity,
                **execution.recipe.as_dict(),
                "auto_reproduce": execution.auto_reproduce,
                "exclusive": execution.exclusive,
                "requires_reproduction": execution.requires_reproduction,
            }
            for cid, identity, execution in state.execution_items()
        )
        if state
        else ()
    )
    return ActionResult(
        "command.list", "unchanged", "command.listed", False, records=records
    )


def rename(entry: EntryContext, old: str, new: str, *, dry_run: bool) -> ActionResult:
    """Move a CID bucket only after its matching Markdown-only rename."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        state = _state(entry)
        if state is None or old not in state.commands:
            raise ActionError("command.record.missing", old)
        if new in state.commands:
            raise ActionError("command.record.conflict", new)
        project = resolve_project_root(entry.root)
        indexed = _index_entry(entry, project, _data(entry))
        declarations = [
            declaration
            for _, _, result in indexed
            for declaration in result.declarations
        ]
        if any(
            declaration.parsed.cid == old for declaration in declarations
        ) or not any(declaration.parsed.cid == new for declaration in declarations):
            raise ActionError(
                "command.rename.markdown_incomplete",
                "rename every old CID in Markdown before the registry",
            )
        commands = dict(state.commands)
        commands[new] = commands.pop(old)
        candidate = PyrunFile(state.path, entry.root, commands)
        invocations, failures = _materialize(indexed, _data(entry))
        selected = tuple(
            invocation for invocation in invocations if invocation.cid == new
        )
        comparison = compare_command(candidate, new, selected, project_root=project)
        if (
            comparison.missing
            or comparison.stale
            or comparison.recipe_changed
            or comparison.policy_changed
        ):
            raise ActionError(
                "command.rename.recipe_changed",
                "a rename must preserve recipes and policies; "
                "use command sync for other edits",
            )
        if failures and not selected:
            raise ActionError(
                "command.rename.unresolved", "the renamed command cannot be resolved"
            )
        text = validated_pyrun_serialization(candidate, project_root=project)
        if not dry_run:
            publish_updates((entry,), {state.path: text})
        return ActionResult(
            "command.rename",
            "dry-run" if dry_run else "changed",
            "command.renamed",
            True,
        )


def delete(entry: EntryContext, cid: str, *, dry_run: bool) -> ActionResult:
    """Remove an absent command and only its exclusively owned declarations."""

    with (
        nullcontext() if dry_run else log_lock(entry.log),
        nullcontext() if dry_run else entry_lock_under_log(entry),
    ):
        state = _state(entry)
        if state is None or cid not in state.commands:
            return ActionResult("command.delete", "absent", "command.absent", False)
        data = _data(entry)
        indexed = _index_entry(entry, resolve_project_root(entry.root), data)
        if any(
            declaration.parsed.cid == cid
            for _, _, result in indexed
            for declaration in result.declarations
        ):
            raise ActionError(
                "command.delete.markdown_present",
                "remove every command block with this CID first",
            )
        outputs = _output_paths(entry, state, cid)
        owned = (
            tuple(
                item
                for item in data.inputs
                if not item.origin
                and item.reference_entry is None
                and item.canonical_target in outputs
            )
            if data
            else ()
        )
        blockers: list[dict[str, Any]] = []
        for output in outputs:
            blockers.extend(
                material_consumers(entry, Path(output), excluded_command=cid)
            )
        for item in owned:
            blockers.extend(
                use
                for use in declaration_uses(entry, item.name)
                if not (use.get("entry") == entry.id and use.get("command") == cid)
            )
        if blockers:
            raise ActionError(
                "command.delete.outputs_in_use",
                "downstream consumers still use this command's outputs"
                + describe_uses(tuple(blockers)),
                records=tuple(blockers),
            )
        commands = {
            name: command for name, command in state.commands.items() if name != cid
        }
        text = (
            validated_pyrun_serialization(
                PyrunFile(state.path, entry.root, commands),
                project_root=resolve_project_root(entry.root),
            )
            if commands
            else None
        )
        updates = {state.path: text}
        if data and owned:
            remaining = tuple(item for item in data.inputs if item not in owned)
            updates[data.path] = (
                data_file_from_inputs(
                    data.path, entry_root=entry.root, inputs=remaining
                ).canonical_json()
                if remaining
                else None
            )
        if not dry_run:
            publish_updates((entry,), updates)
        return ActionResult(
            "command.delete",
            "dry-run" if dry_run else "changed",
            "command.deleted",
            True,
            records=tuple(
                {"disconnected": path}
                for path in sorted(outputs)
                if Path(path).exists()
            ),
        )


def _output_paths(entry: EntryContext, state: PyrunFile, cid: str) -> set[str]:
    return {
        output_target_path(
            path, entry_root=entry.root, project_root=resolve_project_root(entry.root)
        )
        .resolve()
        .as_posix()
        for execution in state.commands[cid].executions.values()
        for path, _ in execution.recipe.outputs
    }
