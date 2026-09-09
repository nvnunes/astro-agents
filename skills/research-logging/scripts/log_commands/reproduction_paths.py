"""Canonical project temporary and reproduction-run path handling."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path, PurePosixPath

REPRODUCTION_ROOT_NAME = "reproduction"
RUN_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
RUN_LEAF_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,319}\Z")
CHECKPOINT_NAME_RE = re.compile(r"e[0-9]{3}-[0-9a-f]{64}\.json\Z")
CHECKPOINT_TEMP_NAME_RE = re.compile(
    r"\.e[0-9]{3}-[0-9a-f]{64}\.json\.[1-9][0-9]*\.tmp\Z"
)


def checkpoint_temporary_path(path: Path, pid: int) -> Path:
    """Return the reserved atomic-write path for one checkpoint generation."""

    if CHECKPOINT_NAME_RE.fullmatch(path.name) is None or pid <= 0:
        raise ValueError("invalid checkpoint temporary path input")
    return path.with_name(f".{path.name}.{pid}.tmp")


def is_checkpoint_temporary_name(name: str) -> bool:
    """Return whether a directory entry has the reserved writer-temporary name."""

    return CHECKPOINT_TEMP_NAME_RE.fullmatch(name) is not None


def resolve_project_tmp(project_root: Path) -> Path:
    """Return one accessible regular project ``tmp`` directory.

    An intentional project-root symlink is accepted, while a missing, broken,
    or non-directory target remains unavailable.
    """

    path = project_root.resolve() / "tmp"
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise OSError(f"project tmp is unavailable: {path}") from error
    if resolved.is_symlink() or not resolved.is_dir():
        raise OSError(f"project tmp is not a regular directory: {path}")
    return resolved


def project_tmp_relative(path: Path, project_root: Path) -> str:
    """Return one canonical dated reproduction-run identity."""

    _, logical = _canonical_run_location(path, project_root, require_exists=True)
    return logical


def canonical_run_root(
    path: Path, project_root: Path, *, require_exists: bool
) -> Path:
    """Validate and return one canonical dated reproduction-run root."""

    resolved, _ = _canonical_run_location(
        path, project_root, require_exists=require_exists
    )
    return resolved


def _canonical_run_location(
    path: Path, project_root: Path, *, require_exists: bool
) -> tuple[Path, str]:
    temporary = resolve_project_tmp(project_root)
    resolved = path.resolve(strict=require_exists)
    try:
        relative = resolved.relative_to(temporary)
    except ValueError as error:
        raise OSError(f"path is outside the project tmp directory: {path}") from error
    logical = (PurePosixPath("tmp") / PurePosixPath(relative.as_posix())).as_posix()
    if (
        path.is_symlink()
        or not is_canonical_run_path(logical)
        or (require_exists and not resolved.is_dir())
    ):
        raise OSError(f"path is not a canonical reproduction run: {path}")
    return resolved, logical


def canonical_run_path(accepted_at: str, leaf: str) -> PurePosixPath:
    """Return the logical dated path for one accepted run leaf."""

    run_date = accepted_at[:10]
    if not _valid_run_date(run_date) or RUN_LEAF_RE.fullmatch(leaf) is None:
        raise ValueError("invalid reproduction run path component")
    return PurePosixPath("tmp", REPRODUCTION_ROOT_NAME, run_date, leaf)


def run_leaf(log_name: str, entry: str | None, run_id: str) -> str:
    """Return the filesystem-safe leaf for one log or entry run."""

    parts = ["reproduce", _safe_component(log_name)]
    if entry is not None:
        parts.append(_safe_component(entry))
    parts.append(run_id)
    leaf = "-".join(parts)
    if RUN_LEAF_RE.fullmatch(leaf) is None:
        raise ValueError("invalid reproduction run leaf")
    return leaf


def is_canonical_run_path(value: str) -> bool:
    """Return whether ``value`` is one canonical dated run path."""

    path = PurePosixPath(value)
    if path.as_posix() != value or len(path.parts) != 4:
        return False
    temporary, root, run_date, leaf = path.parts
    return (
        temporary == "tmp"
        and root == REPRODUCTION_ROOT_NAME
        and _valid_run_date(run_date)
        and RUN_LEAF_RE.fullmatch(leaf) is not None
    )


def iter_canonical_run_roots(
    project_root: Path, *, max_entries: int
) -> tuple[Path, ...]:
    """Return canonical run directories after one bounded two-level scan."""

    temporary = resolve_project_tmp(project_root)
    root = temporary / REPRODUCTION_ROOT_NAME
    if not root.exists() and not root.is_symlink():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise OSError(f"reproduction root is not a regular directory: {root}")
    runs: list[Path] = []
    inspected = 0
    for dated in sorted(root.iterdir(), key=lambda item: item.name):
        inspected += 1
        if inspected > max_entries:
            raise OSError("reproduction run scan limit exceeded")
        if dated.is_symlink() or not dated.is_dir() or not _valid_run_date(dated.name):
            continue
        for candidate in sorted(dated.iterdir(), key=lambda item: item.name):
            inspected += 1
            if inspected > max_entries:
                raise OSError("reproduction run scan limit exceeded")
            logical = PurePosixPath(
                "tmp", REPRODUCTION_ROOT_NAME, dated.name, candidate.name
            ).as_posix()
            if (
                candidate.is_symlink()
                or not candidate.is_dir()
                or not is_canonical_run_path(logical)
            ):
                continue
            runs.append(candidate.resolve())
    return tuple(runs)


def _valid_run_date(value: str) -> bool:
    if RUN_DATE_RE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _safe_component(value: str) -> str:
    selected = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not selected:
        raise ValueError("empty reproduction run path component")
    return selected[:64]
