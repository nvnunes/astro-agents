"""Bounded filesystem traversal primitives for mechanical validation."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoundedTraversalError(RuntimeError):
    """One unavailable or resource-bounded descendant traversal."""

    root: Path
    reason: str
    limit: int
    observed: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class BoundedFileReadError(RuntimeError):
    """One unavailable, changing, or oversized bounded file observation."""

    path: Path
    reason: str
    limit: int
    observed: int | None = None
    detail: str | None = None


def bounded_file_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    """Read one regular file only after establishing and enforcing its bound."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BoundedFileReadError(
                path, "unavailable", maximum_bytes, detail="not_regular_file"
            )
        if before.st_size > maximum_bytes:
            raise BoundedFileReadError(
                path,
                "byte_limit",
                maximum_bytes,
                observed=before.st_size,
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        after = os.fstat(descriptor)
        current = path.stat()
    except BoundedFileReadError:
        raise
    except OSError as error:
        raise BoundedFileReadError(
            path, "unavailable", maximum_bytes, detail=str(error)
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > maximum_bytes:
        raise BoundedFileReadError(
            path, "byte_limit", maximum_bytes, observed=len(payload)
        )
    before_identity = _file_identity(before)
    if before_identity != _file_identity(after) or before_identity != _file_identity(
        current
    ):
        raise BoundedFileReadError(
            path,
            "changed_during_observation",
            maximum_bytes,
            observed=len(payload),
        )
    return payload


def _file_identity(observation: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        observation.st_dev,
        observation.st_ino,
        observation.st_size,
        observation.st_mtime_ns,
        observation.st_ctime_ns,
    )


def bounded_descendants(root: Path, *, maximum_entries: int) -> tuple[Path, ...]:
    """Return all descendants without following symlinks or exceeding the bound.

    Enumeration stops as soon as ``maximum_entries + 1`` descendants have been
    observed. The returned paths use deterministic entry-relative byte order.
    """

    pending = [root]
    descendants: list[Path] = []
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    descendants.append(path)
                    if len(descendants) > maximum_entries:
                        raise BoundedTraversalError(
                            root,
                            "entry_limit",
                            maximum_entries,
                            observed=len(descendants),
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except BoundedTraversalError:
            raise
        except OSError as error:
            raise BoundedTraversalError(
                root,
                "unavailable",
                maximum_entries,
                detail=str(error),
            ) from error
    return tuple(
        sorted(
            descendants,
            key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
        )
    )
