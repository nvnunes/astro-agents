"""Atomic publication primitives for generated validation records."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

LOCK_FILENAME = ".research-log-validation.lock"


class RecordPublicationError(RuntimeError):
    """Raised when exclusive validation publication cannot complete safely."""


@dataclass(frozen=True)
class PublicationGuard:
    """Staleness identity and optional currentness hook for one publication."""

    expected_identity: str
    identity_filenames: Iterable[str] | None = None
    validate_publication: Callable[[], None] | None = None


@contextmanager
def validation_lock(output_dir: Path) -> Iterator[None]:
    """Hold one log's nonblocking canonical-publication lock.

    The lock file is stable and ignored by source control. The operating system
    releases the advisory lock automatically when the process exits.
    """

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / LOCK_FILENAME
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


def _validated_names(filenames: Iterable[str]) -> tuple[str, ...]:
    names = tuple(filenames)
    if (
        not names
        or len(set(names)) != len(names)
        or any(not name or Path(name).name != name for name in names)
    ):
        raise RecordPublicationError("publication filenames must be unique basenames")
    return names


def record_bundle_identity(output_dir: Path, filenames: Iterable[str]) -> str:
    """Identify the exact present/missing content of one generated bundle."""

    names = _validated_names(filenames)
    digest = hashlib.sha256()
    for name in sorted(names):
        path = output_dir / name
        digest.update(f"{name}\0".encode())
        if not path.is_file():
            digest.update(b"missing\n")
            continue
        payload = path.read_bytes()
        digest.update(f"present\0{len(payload)}\0".encode())
        digest.update(hashlib.sha256(payload).digest())
        digest.update(b"\n")
    return digest.hexdigest()


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


def _publish_one(staged: Path, destination: Path) -> None:
    """Atomically replace or remove one generated record."""

    if staged.is_file():
        _atomic_write_bytes(destination, staged.read_bytes())
    else:
        destination.unlink(missing_ok=True)
        _fsync_directory(destination.parent)


def _validate_publication_paths(
    staged_dir: Path, output_dir: Path, names: Iterable[str]
) -> None:
    if output_dir.is_symlink():
        raise RecordPublicationError(
            "publication output directory must not be a symlink"
        )
    if staged_dir.is_symlink() or not staged_dir.is_dir():
        raise RecordPublicationError("publication staging directory is invalid")
    for name in names:
        destination = output_dir / name
        if destination.is_symlink():
            raise RecordPublicationError(
                f"publication destination must not be a symlink: {destination}"
            )


def publish_record_bundle(
    staged_dir: Path,
    output_dir: Path,
    filenames: Iterable[str],
    guard: PublicationGuard,
) -> None:
    """Publish a staged bundle with atomic files and fail-closed interruption.

    The caller owns the per-log publication lock. ``expected_identity`` rejects a stale
    render packet. There is deliberately no rollback: a later canonical scan
    rejects an incomplete bundle and rebuilds it from research inputs.
    """

    names = _validated_names(filenames)
    _validate_publication_paths(staged_dir, output_dir, names)
    identity_names = _validated_names(guard.identity_filenames or names)
    if record_bundle_identity(output_dir, identity_names) != guard.expected_identity:
        raise RecordPublicationError("canonical validation bundle changed after scan")
    if guard.validate_publication is not None:
        guard.validate_publication()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for name in names:
            _publish_one(staged_dir / name, output_dir / name)
        if guard.validate_publication is not None:
            guard.validate_publication()
    except BaseException as exc:
        if isinstance(exc, Exception):
            raise RecordPublicationError(
                "validation-record publication was interrupted; "
                "the next validation must rebuild the incomplete bundle: "
                f"{exc}"
            ) from exc
        raise
