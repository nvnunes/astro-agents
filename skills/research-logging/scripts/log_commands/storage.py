"""Atomic research-owned file publication and stable operation locks."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from validation.operation_state import operation_lock

from .context import EntryContext, LogContext


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


def remove_or_write(path: Path, text: str | None) -> None:
    """Publish canonical content or durably remove an empty registry."""

    if text is not None:
        atomic_write_text(path, text)
        return
    if path.exists():
        path.unlink()
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
