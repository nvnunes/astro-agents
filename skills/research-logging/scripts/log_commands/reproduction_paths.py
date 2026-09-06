"""Canonical project temporary-root resolution for reproduction."""

from __future__ import annotations

from pathlib import Path


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
    """Return the logical project-relative identity of one direct tmp child."""

    temporary = resolve_project_tmp(project_root)
    resolved = path.resolve(strict=True)
    if resolved.parent != temporary or path.is_symlink():
        raise OSError(f"path is not a project tmp child: {path}")
    return (Path("tmp") / resolved.name).as_posix()
