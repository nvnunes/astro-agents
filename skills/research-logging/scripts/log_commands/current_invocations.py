"""Current Markdown invocation discovery shared by policy and command verification."""

from __future__ import annotations

from pathlib import Path

from research_log_data import DataFile, load_data_file
from validation.commands import (
    CommandContext,
    Invocation,
    discover_commands,
    order_invocations,
    validate_command_structure,
)
from validation.errors import MechanicalContractError
from validation.evidence import index_entry_documents

from .context import EntryContext, parse_entry_document_name
from .model import ActionError


def entry_invocations(
    entry: EntryContext, *, project_root: Path
) -> tuple[Invocation, ...]:
    """Discover all current eligible invocations across a physical split entry."""
    if entry.root.is_symlink() or not entry.root.is_dir():
        raise ActionError("entry.identity.unresolved", entry.id)
    data = _load_data(entry.root)
    try:
        summary = entry.log.summary.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionError("association.document_unavailable", str(error)) from error
    documents_on_disk = _entry_document_paths(entry, summary)
    invocations_by_document = [
        _document_invocations(entry, document, project_root=project_root, data=data)
        for document in documents_on_disk
    ]
    result = order_invocations(invocations_by_document)
    try:
        validate_command_structure(result)
    except MechanicalContractError as error:
        raise ActionError("pyrun.command.invalid", str(error)) from error
    return result


def _entry_document_paths(entry: EntryContext, summary: str) -> tuple[Path, ...]:
    """Resolve the selected physical entry's complete document inventory."""

    document_paths: list[Path] = []
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
        document_paths.append(path.resolve())
    documents_on_disk = tuple(sorted(set(document_paths)))
    if not documents_on_disk:
        raise ActionError("entry.identity.unresolved", entry.id)
    return documents_on_disk


def _document_invocations(
    entry: EntryContext,
    document: Path,
    *,
    project_root: Path,
    data: DataFile | None,
) -> tuple[Invocation, ...]:
    """Discover one validated entry document's command invocations."""

    try:
        text = document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ActionError("association.document_unavailable", str(error)) from error
    discovered = discover_commands(
        text,
        CommandContext(
            entry.log.root.as_posix(),
            entry.id,
            document.relative_to(entry.log.root).as_posix(),
            entry.root,
            entry.log.root,
            project_root,
            data,
        ),
    )
    if discovered.failures:
        failure = discovered.failures[0]
        raise ActionError(
            "pyrun.command.invalid",
            f"{document}: fence {failure.fence}, command {failure.ordinal}: "
            f"{failure.error}",
        )
    return discovered.invocations


def _load_data(entry_root: Path) -> DataFile | None:
    path = entry_root / "data.json"
    return (
        load_data_file(path, entry_root=entry_root)
        if path.exists() or path.is_symlink()
        else None
    )
