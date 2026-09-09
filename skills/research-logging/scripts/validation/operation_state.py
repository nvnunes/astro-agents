"""Shared lock identity and publication guards for research-log operations."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping

MAX_SNAPSHOT_FILES = 1_000_000
RUNTIME_CACHE_DIRECTORIES = frozenset(
    {".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)
REORGANIZE_RESIDUE = "reorganize-residue"
REGISTRY_RESIDUE = "registry-residue"
REGISTRY_RESIDUE_PREFIX = "registry-residue-"
LOCK_OWNER_SCHEMA = "research-log-operation-owner/1"
MAX_LOCK_OWNER_BYTES = 64 * 1024
LockMode = Literal["shared", "exclusive"]


class OperationLockError(OSError):
    """One maintained operation could not acquire its canonical lock."""

    code = "operation.lock.conflict"

    def __init__(self, path: Path, owner: Mapping[str, object] | None = None):
        self.path = path
        self.owner = dict(owner) if owner is not None else None
        detail = (
            f": {json.dumps(self.owner, ensure_ascii=False, sort_keys=True)}"
            if self.owner is not None
            else ""
        )
        super().__init__(f"research-log operation is active: {path}{detail}")


def operation_directory(log_root: Path) -> Path:
    """Return the generated operation-state directory for one logical log."""

    return log_root.resolve() / ".cache" / "research-log-operations"


def _prepare_operation_directory(log_root: Path) -> Path:
    if log_root.is_symlink() or not log_root.is_dir():
        raise OSError(f"operation root must be a regular directory: {log_root}")
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
def operation_lock(
    log_root: Path,
    name: str,
    *,
    mode: LockMode = "exclusive",
    owner_factory: Callable[[], Mapping[str, object]] | None = None,
) -> Iterator[None]:
    """Hold one stable generated operation lock without waiting.

    An exclusive owner may publish bounded diagnostic metadata after acquiring
    the OS lock and before control reaches the protected operation. Contenders
    read that metadata directly; stale metadata never establishes contention.
    """

    if Path(name).name != name or not name.endswith(".lock"):
        raise ValueError(f"invalid operation lock name: {name}")
    if mode not in {"shared", "exclusive"}:
        raise ValueError(f"invalid operation lock mode: {mode}")
    if owner_factory is not None and mode != "exclusive":
        raise ValueError("operation owner metadata requires an exclusive lock")
    directory = _prepare_operation_directory(log_root)
    path = directory / name
    owner_path = directory / f"{name}.owner.json"
    with os.fdopen(_open_lock(path, create=True), "r+b") as handle:
        operation = fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX
        try:
            fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OperationLockError(path, _read_lock_owner(owner_path)) from error
        published_owner = False
        try:
            if owner_factory is not None:
                _publish_lock_owner(owner_path, owner_factory())
                published_owner = True
            yield
        finally:
            if published_owner:
                try:
                    owner_path.unlink(missing_ok=True)
                    _sync_directory(directory)
                except OSError:
                    pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _publish_lock_owner(path: Path, owner: Mapping[str, object]) -> None:
    """Atomically publish one bounded exact owner record."""

    value = dict(owner)
    if value.get("schema") != LOCK_OWNER_SCHEMA:
        raise ValueError("operation owner has an unsupported schema")
    raw = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if len(raw) > MAX_LOCK_OWNER_BYTES:
        raise ValueError("operation owner crossed its byte bound")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_lock_owner(path: Path) -> dict[str, object] | None:
    """Read current diagnostic metadata without treating it as a lock."""

    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_LOCK_OWNER_BYTES + 1)
        if len(raw) > MAX_LOCK_OWNER_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != LOCK_OWNER_SCHEMA
        or not all(isinstance(key, str) for key in value)
    ):
        return None
    return value


def require_mutation_ready(
    log_root: Path,
    *,
    entry_id: str | None = None,
) -> None:
    """Refuse mutation while recognized hard-crash residue remains."""

    directory = operation_directory(log_root)
    paths = [directory / REORGANIZE_RESIDUE, directory / REGISTRY_RESIDUE]
    if entry_id is None:
        if directory.is_dir():
            paths.extend(sorted(directory.glob(f"{REGISTRY_RESIDUE_PREFIX}*")))
    else:
        paths.append(directory / f"{REGISTRY_RESIDUE_PREFIX}{entry_id}")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise OSError(f"research-log mutation requires Repair: {path}")


def begin_reorganization(log_root: Path) -> Path:
    """Publish the one recognized hard-crash marker for Reorganize."""

    return _begin_residue(
        log_root,
        REORGANIZE_RESIDUE,
        "explicit Repair required after interrupted Reorganize\n",
    )


def begin_registry_transaction(log_root: Path, entry_id: str) -> Path:
    """Guard one multi-file authored-registry publication until completion."""

    if not re.fullmatch(r"e[0-9]{3}", entry_id):
        raise ValueError(f"invalid stable entry ID: {entry_id}")
    return _begin_residue(
        log_root,
        f"{REGISTRY_RESIDUE_PREFIX}{entry_id}",
        "explicit Repair required after interrupted registry publication\n",
    )


def _begin_residue(log_root: Path, name: str, message: str) -> Path:
    directory = _prepare_operation_directory(log_root)
    path = directory / name
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(message.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(directory)
    return path


def finish_guarded_publication(path: Path) -> None:
    """Remove a completed or fully rolled-back publication marker."""

    path.unlink()
    _sync_directory(path.parent)


def operation_lock_owner(path: Path) -> Mapping[str, object] | None:
    """Read bounded owner metadata beside one canonical lock path."""

    return _read_lock_owner(path.with_name(f"{path.name}.owner.json"))


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
        generated_directories = set(RUNTIME_CACHE_DIRECTORIES)
        if root == log_root:
            generated_directories.update({"reproduction", "validation"})
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
            if not (root == log_root and name in {"reproduction.md", "validation.md"})
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
