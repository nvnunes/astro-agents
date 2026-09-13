"""Single-path durable file publication primitives for research-log tools."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from .filesystem import FileIdentity, file_identity

DEFAULT_FILE_MODE = 0o644
COPY_CHUNK_BYTES = 1024 * 1024


def sync_directory(path: Path) -> None:
    """Durably record completed directory-entry changes in one directory."""

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def install_path(temporary: Path, destination: Path) -> None:
    """Atomically install a prepared sibling path and sync its directory."""

    if temporary.parent != destination.parent:
        raise ValueError("atomic installation requires sibling paths")
    os.replace(temporary, destination)
    sync_directory(destination.parent)


def atomic_replace_bytes(
    path: Path, payload: bytes, *, default_mode: int = DEFAULT_FILE_MODE
) -> FileIdentity:
    """Replace one file durably, preserving its existing mode and returning identity."""

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _replacement_mode(path, default_mode)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary.chmod(mode)
            install_path(temporary, path)
            return file_identity(os.fstat(handle.fileno()))
        finally:
            temporary.unlink(missing_ok=True)


def atomic_replace_text(path: Path, text: str) -> FileIdentity:
    """Replace one UTF-8 text file durably while preserving its existing mode."""

    return atomic_replace_bytes(path, text.encode("utf-8"))


def atomic_replace_from_file(path: Path, source: Path, mode: int) -> FileIdentity:
    """Replace one file durably from a disk-backed snapshot with an explicit mode."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            with source.open("rb") as snapshot:
                while chunk := snapshot.read(COPY_CHUNK_BYTES):
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
            temporary.chmod(mode)
            install_path(temporary, path)
            return file_identity(os.fstat(handle.fileno()))
        finally:
            temporary.unlink(missing_ok=True)


def atomic_create_text(path: Path, text: str) -> None:
    """Create one UTF-8 text file durably without replacing an occupied path."""

    path.parent.mkdir(parents=False, exist_ok=True)
    temporary: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(DEFAULT_FILE_MODE)
        os.link(temporary, path, follow_symlinks=False)
        published = True
        sync_directory(path.parent)
    except OSError:
        if published:
            path.unlink(missing_ok=True)
            sync_directory(path.parent)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_symlink(path: Path, target: str) -> None:
    """Create and durably publish one new symbolic link."""

    published = False
    try:
        os.symlink(target, path)
        published = True
        sync_directory(path.parent)
    except OSError:
        if published:
            path.unlink(missing_ok=True)
            sync_directory(path.parent)
        raise


def remove_file(path: Path) -> None:
    """Remove one file and durably publish the directory-entry change."""

    path.unlink()
    sync_directory(path.parent)


def _replacement_mode(path: Path, default_mode: int) -> int:
    return stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
