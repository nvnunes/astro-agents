"""Shared lock identity and publication guards for research-log operations."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MAX_SNAPSHOT_FILES = 1_000_000
REORGANIZE_RESIDUE = "reorganize-residue"


def operation_directory(log_root: Path) -> Path:
    """Return the generated operation-state directory for one logical log."""

    return log_root.resolve() / ".cache" / "research-log-operations"


def _prepare_operation_directory(log_root: Path) -> Path:
    cache = log_root.resolve() / ".cache"
    if cache.is_symlink() or cache.exists() and not cache.is_dir():
        raise OSError(f"operation cache must be a regular directory: {cache}")
    cache.mkdir(exist_ok=True)
    directory = cache / "research-log-operations"
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise OSError(f"operation state must be a regular directory: {directory}")
    directory.mkdir(exist_ok=True)
    return directory


def _open_lock(path: Path, *, create: bool) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    return os.open(path, flags, 0o644)


@contextmanager
def operation_lock(log_root: Path, name: str) -> Iterator[None]:
    """Hold one stable generated operation lock."""

    if Path(name).name != name or not name.endswith(".lock"):
        raise ValueError(f"invalid operation lock name: {name}")
    directory = _prepare_operation_directory(log_root)
    with os.fdopen(_open_lock(directory / name, create=True), "r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def require_mutation_ready(log_root: Path) -> None:
    """Refuse mutation while recognized hard-crash residue remains."""

    path = operation_directory(log_root) / REORGANIZE_RESIDUE
    if path.exists() or path.is_symlink():
        raise OSError(f"research-log reorganization requires Repair: {path}")


def begin_reorganization(log_root: Path) -> Path:
    """Publish the one recognized hard-crash marker for Reorganize."""

    directory = _prepare_operation_directory(log_root)
    path = directory / REORGANIZE_RESIDUE
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"explicit Repair required after interrupted Reorganize\n")
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(directory)
    return path


def finish_reorganization(path: Path) -> None:
    """Remove a completed or fully rolled-back Reorganize marker."""

    path.unlink()
    _sync_directory(path.parent)


def mutation_active(log_root: Path) -> bool:
    """Return whether any existing operation lock is currently held."""

    directory = operation_directory(log_root)
    if directory.is_symlink():
        raise OSError(f"operation state must not be a symlink: {directory}")
    if not directory.exists():
        return False
    if not directory.is_dir():
        raise OSError(f"operation state must be a regular directory: {directory}")
    residue = directory / REORGANIZE_RESIDUE
    if residue.exists() or residue.is_symlink():
        return True
    for path in sorted(directory.glob("*.lock")):
        if path.is_symlink() or not path.is_file():
            raise OSError(f"operation lock must be a regular file: {path}")
        with os.fdopen(_open_lock(path, create=False), "r+b") as handle:
            acquired = False
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                return True
            finally:
                if acquired:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def research_snapshot(summary: Path) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Return a bounded metadata identity for research-owned filesystem state."""

    summary = summary.resolve()
    log_root = summary.with_suffix("")
    paths = [summary]
    for directory, names, files in os.walk(log_root, topdown=True, followlinks=False):
        root = Path(directory)
        generated_directories = {".cache"}
        if root == log_root:
            generated_directories.add("validation")
        paths.extend(
            root / name
            for name in names
            if name not in generated_directories and (root / name).is_symlink()
        )
        names[:] = [
            name
            for name in sorted(names)
            if name not in generated_directories and not (root / name).is_symlink()
        ]
        paths.extend(
            root / name
            for name in sorted(files)
            if not (root == log_root and name == "validation.md")
        )
        if len(paths) > MAX_SNAPSHOT_FILES:
            raise OSError("research snapshot crossed its file bound")
    result = []
    for path in paths:
        stat_result = path.stat(follow_symlinks=False)
        result.append(
            (
                path.relative_to(summary.parent).as_posix(),
                (
                    stat_result.st_dev,
                    stat_result.st_ino,
                    stat_result.st_mode,
                    stat_result.st_size,
                    stat_result.st_mtime_ns,
                    stat_result.st_ctime_ns,
                ),
            )
        )
    return tuple(sorted(result))
