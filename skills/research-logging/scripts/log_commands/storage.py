"""Atomic research-owned file publication and stable operation locks."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from validation.file_publication import (
    atomic_create_text,
    create_symlink,
    remove_file,
    sync_directory,
)
from validation.file_publication import atomic_replace_text as atomic_write_text
from validation.operation_state import operation_lock, require_mutation_ready

from .context import EntryContext, LogContext, LogCreationContext

__all__ = [
    "PublicationError",
    "atomic_create_text",
    "atomic_write_text",
    "atomic_write_texts",
    "create_symlink",
    "entry_lock",
    "entry_lock_under_log",
    "entry_locks",
    "log_and_entry_locks",
    "log_creation_lock",
    "log_lock",
    "remove_or_write",
    "reproduction_log_reservation",
    "sync_directory",
]


class PublicationError(OSError):
    """A text transaction failed, with explicit rollback completion state."""

    def __init__(self, error: BaseException, rollback_errors: tuple[str, ...]):
        self.rollback_errors = rollback_errors
        detail = (
            f"; rollback failed: {'; '.join(rollback_errors)}"
            if rollback_errors
            else ""
        )
        super().__init__(f"transaction publication failed: {error}{detail}")

    @property
    def rollback_complete(self) -> bool:
        """Return whether every attempted destination was restored."""

        return not self.rollback_errors


@contextmanager
def entry_lock(entry: EntryContext) -> Iterator[None]:
    """Hold the shared log lock, then one stable entry lock exclusively."""

    with operation_lock(entry.log.root, "log.lock", mode="shared"):
        require_mutation_ready(entry.log.root, entry_id=entry.id)
        with entry_lock_under_log(entry):
            yield


@contextmanager
def entry_locks(log: LogContext, entries: Iterable[EntryContext]) -> Iterator[None]:
    """Hold the shared log lock and several entry locks in stable order."""

    selected = sorted(entries, key=lambda item: item.id)
    if len({entry.id for entry in selected}) != len(selected) or any(
        entry.log.root != log.root for entry in selected
    ):
        raise ValueError("operation locks require unique entries from one log")
    with ExitStack() as stack:
        stack.enter_context(operation_lock(log.root, "log.lock", mode="shared"))
        for entry in selected:
            require_mutation_ready(log.root, entry_id=entry.id)
            stack.enter_context(entry_lock_under_log(entry))
        yield


@contextmanager
def entry_lock_under_log(entry: EntryContext) -> Iterator[None]:
    """Hold one entry lock while the caller already owns the log lock."""

    with operation_lock(entry.log.root, f"entry-{entry.id}.lock"):
        yield


@contextmanager
def log_lock(log: LogContext, *, timeout_seconds: float = 0) -> Iterator[None]:
    """Hold the canonical log lock exclusively."""

    with operation_lock(
        log.root, "log.lock", mode="exclusive", timeout_seconds=timeout_seconds
    ):
        require_mutation_ready(log.root)
        yield


@contextmanager
def reproduction_log_reservation(log: LogContext) -> Iterator[None]:
    """Exclude every durable or isolated reproduction operation for one log."""

    with operation_lock(log.root, "reproduction-log.lock", mode="exclusive"):
        require_mutation_ready(log.root)
        yield


@contextmanager
def log_creation_lock(log: LogCreationContext) -> Iterator[None]:
    """Hold the project-scoped lock for one intended canonical log path."""

    identity = hashlib.sha256(log.root.as_posix().encode("utf-8")).hexdigest()
    with operation_lock(log.project_root, f"create-{identity}.lock"):
        yield


@contextmanager
def log_and_entry_locks(
    log: LogContext,
    entries: Iterable[EntryContext],
) -> Iterator[None]:
    """Hold the log lock, then unique entry locks in stable ID order."""

    selected = sorted(entries, key=lambda item: item.id)
    if len({entry.id for entry in selected}) != len(selected) or any(
        entry.log.root != log.root for entry in selected
    ):
        raise ValueError("operation locks require unique entries from one log")
    with ExitStack() as stack:
        stack.enter_context(log_lock(log))
        for entry in selected:
            stack.enter_context(entry_lock_under_log(entry))
        yield


def atomic_write_texts(updates: Mapping[Path, str | None]) -> None:
    """Publish text replacements/removals or restore every prior byte."""

    ordered = tuple(sorted(updates.items(), key=lambda item: item[0].as_posix()))
    before = {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path, _ in ordered
    }
    written: list[Path] = []
    try:
        for path, value in ordered:
            written.append(path)
            remove_or_write(path, value)
    except (OSError, UnicodeError) as error:
        rollback: list[str] = []
        for path in reversed(written):
            try:
                remove_or_write(path, before[path])
            except (OSError, UnicodeError) as restore_error:
                rollback.append(f"{path}: {restore_error}")
        raise PublicationError(error, tuple(rollback)) from error


def remove_or_write(path: Path, text: str | None) -> None:
    """Publish canonical content or durably remove an empty registry."""

    if text is not None:
        atomic_write_text(path, text)
        return
    if path.exists():
        remove_file(path)
