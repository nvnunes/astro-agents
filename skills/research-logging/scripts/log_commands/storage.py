"""Atomic research-owned file publication and stable operation locks."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from validation.operation_state import operation_lock

from .context import EntryContext, LogContext, LogCreationContext


@contextmanager
def entry_lock(entry: EntryContext) -> Iterator[None]:
    """Hold the stable entry lock without first acquiring the log lock."""

    with _lock(entry.log, f"entry-{entry.id}.lock"):
        yield


@contextmanager
def log_lock(log: LogContext) -> Iterator[None]:
    """Hold the canonical log mutation lock."""

    with _lock(log, "log.lock"):
        yield


@contextmanager
def log_creation_lock(log: LogCreationContext) -> Iterator[None]:
    """Hold the project-scoped lock for one intended canonical log path."""

    identity = hashlib.sha256(log.root.as_posix().encode("utf-8")).hexdigest()
    with operation_lock(log.project_root, f"create-{identity}.lock"):
        yield


@contextmanager
def log_and_entry_locks(
    log: LogContext, entries: Iterable[EntryContext]
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
            stack.enter_context(entry_lock(entry))
        yield


@contextmanager
def _lock(log: LogContext, name: str) -> Iterator[None]:
    with operation_lock(log.root, name):
        yield


def atomic_write_text(path: Path, text: str) -> None:
    """Replace one text file atomically and durably, preserving its mode."""

    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_texts(updates: Mapping[Path, str]) -> None:
    """Publish a small text-file transaction or restore every prior byte."""

    ordered = tuple(sorted(updates.items(), key=lambda item: item[0].as_posix()))
    before = {path: path.read_text(encoding="utf-8") for path, _ in ordered}
    written: list[Path] = []
    try:
        for path, value in ordered:
            atomic_write_text(path, value)
            written.append(path)
    except (OSError, UnicodeError) as error:
        rollback: list[str] = []
        for path in reversed(written):
            try:
                atomic_write_text(path, before[path])
            except OSError as restore_error:
                rollback.append(f"{path}: {restore_error}")
        detail = f"; rollback failed: {'; '.join(rollback)}" if rollback else ""
        raise OSError(f"transaction publication failed: {error}{detail}") from error


def atomic_create_text(path: Path, text: str) -> None:
    """Create one text file atomically without replacing an existing target."""

    path.parent.mkdir(parents=False, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    published = False
    try:
        temporary.chmod(0o644)
        os.link(temporary, path, follow_symlinks=False)
        published = True
        _sync_directory(path.parent)
    except OSError:
        if published:
            path.unlink(missing_ok=True)
            _sync_directory(path.parent)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def create_symlink(path: Path, target: str) -> None:
    """Create and durably publish one new symbolic link."""

    published = False
    try:
        os.symlink(target, path)
        published = True
        _sync_directory(path.parent)
    except OSError:
        if published:
            path.unlink(missing_ok=True)
            _sync_directory(path.parent)
        raise


def remove_or_write(path: Path, text: str | None) -> None:
    """Publish canonical content or durably remove an empty registry."""

    if text is not None:
        atomic_write_text(path, text)
        return
    if path.exists():
        path.unlink()
        _sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    """Durably record directory-entry changes in one existing directory."""

    _sync_directory(path)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
