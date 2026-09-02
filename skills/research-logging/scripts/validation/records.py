"""Atomic publication primitives for generated validation records."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

LOCK_FILENAME = "validation/.cache/lock"
PUBLISHABLE_PATHS = frozenset(
    {
        "validation.md",
        "validation/mechanical.json",
        "validation/reproduction.json",
        "validation/.cache/mechanical.json",
    }
)


class RecordPublicationError(RuntimeError):
    """Raised when generated validation state cannot be published safely."""


@contextmanager
def validation_lock(log_root: Path) -> Iterator[None]:
    """Hold one log's nonblocking generated-state publication lock."""

    log_root = log_root.resolve()
    lock_path = log_root / LOCK_FILENAME
    current = log_root
    for part in PurePosixPath(LOCK_FILENAME).parts:
        current /= part
        if current.is_symlink():
            raise RecordPublicationError(
                f"validation publication path must not contain a symlink: {current}"
            )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecordPublicationError(
                f"another validation writer owns {lock_path}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy_file(path: Path, source: Path, mode: int) -> None:
    """Atomically replace ``path`` from a disk-backed snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        with source.open("rb") as snapshot:
            while chunk := snapshot.read(1024 * 1024):
                handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_file(path: Path, snapshot: Path) -> int:
    """Copy one stable prior destination to disk and return its file mode."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RecordPublicationError(
                f"validation publication destination is not a file: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            with snapshot.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        after = os.fstat(descriptor)
        current = path.stat()
    finally:
        os.close(descriptor)
    before_identity = _file_identity(before)
    if before_identity != _file_identity(after) or before_identity != _file_identity(
        current
    ):
        raise RecordPublicationError(
            f"validation publication destination changed during snapshot: {path}"
        )
    return stat.S_IMODE(before.st_mode)


def _file_identity(observation: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        observation.st_dev,
        observation.st_ino,
        observation.st_size,
        observation.st_mtime_ns,
        observation.st_ctime_ns,
    )


def _snapshot_publication(
    resolved: tuple[tuple[str, Path, bytes], ...], snapshot_root: Path
) -> dict[str, tuple[Path, int] | None]:
    prior: dict[str, tuple[Path, int] | None] = {}
    for index, (relative, path, _) in enumerate(resolved):
        if path.is_file():
            snapshot = snapshot_root / str(index)
            prior[relative] = (snapshot, _snapshot_file(path, snapshot))
        else:
            prior[relative] = None
    return prior


def _restore_publication(
    attempted: list[tuple[str, Path]],
    prior: Mapping[str, tuple[Path, int] | None],
) -> list[str]:
    errors: list[str] = []
    for relative, path in reversed(attempted):
        try:
            prior_file = prior[relative]
            if prior_file is None:
                path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
            else:
                snapshot, mode = prior_file
                _atomic_copy_file(path, snapshot, mode)
        except Exception as error:  # pragma: no cover - exercised by fault injection
            errors.append(f"{relative}: {error}")
    return errors


def _publication_failure_message(error: Exception, rollback_errors: list[str]) -> str:
    if rollback_errors:
        return (
            "validation publication failed and rollback was incomplete: "
            f"{error}; rollback failed for {rollback_errors}"
        )
    return (
        "validation publication failed and the prior publication bundle was "
        f"restored: {error}"
    )


def _publication_paths(
    log_root: Path, outputs: Mapping[str, bytes]
) -> tuple[tuple[str, Path, bytes], ...]:
    if not outputs or not set(outputs) <= PUBLISHABLE_PATHS:
        raise RecordPublicationError(
            "validation publication contains unsupported paths"
        )
    resolved: list[tuple[str, Path, bytes]] = []
    for relative, payload in sorted(outputs.items()):
        if not isinstance(payload, bytes):
            raise RecordPublicationError(
                f"publication payload is not bytes: {relative}"
            )
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise RecordPublicationError(f"invalid publication path: {relative}")
        path = log_root.joinpath(*pure.parts)
        current = log_root
        for part in pure.parts:
            current /= part
            if current.is_symlink():
                raise RecordPublicationError(
                    f"validation publication path must not contain a symlink: {current}"
                )
        if path.exists() and not path.is_file():
            raise RecordPublicationError(
                f"validation publication destination is not a file: {path}"
            )
        resolved.append((relative, path, payload))
    return tuple(resolved)


def publish_validation_outputs(
    log_root: Path,
    outputs: Mapping[str, bytes],
    *,
    validate_current: Callable[[], None] | None = None,
) -> None:
    """Publish one bundle and restore the complete prior bundle after an error.

    Each destination replacement is atomic. Process termination outside Python
    remains subject to the normal per-file atomicity boundary.
    """

    log_root = log_root.resolve()
    with validation_lock(log_root):
        resolved = _publication_paths(log_root, outputs)
        if validate_current is not None:
            validate_current()
        with tempfile.TemporaryDirectory(
            prefix="research-log-validation-publication-"
        ) as snapshot_directory:
            prior = _snapshot_publication(resolved, Path(snapshot_directory))
            attempted: list[tuple[str, Path]] = []
            try:
                for relative, path, payload in resolved:
                    attempted.append((relative, path))
                    _atomic_write_bytes(path, payload)
                if validate_current is not None:
                    validate_current()
            except Exception as exc:
                rollback_errors = _restore_publication(attempted, prior)
                raise RecordPublicationError(
                    _publication_failure_message(exc, rollback_errors)
                ) from exc
