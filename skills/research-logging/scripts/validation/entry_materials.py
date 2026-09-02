"""Path policy for entry-owned material directories and their symlinks."""

from __future__ import annotations

import os
from pathlib import Path

ENTRY_MATERIAL_DIRECTORY_NAMES = frozenset({"data", "images"})


class EntryMaterialPathError(ValueError):
    """An entry path crosses an unsupported or unavailable symlink."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"{reason}: {path}")
        self.path = path
        self.reason = reason


def validate_entry_path_symlinks(path: Path, entry_root: Path) -> Path:
    """Validate symlinks in one lexical entry-relative path and return its target.

    The exact entry-local ``data`` and ``images`` directory components may be
    directory symlinks. They remain first-class entry roots. No later or other
    symlink component is permitted.
    """

    root = entry_root.absolute()
    target = path.absolute()
    try:
        parts = target.relative_to(root).parts
    except ValueError as error:
        raise EntryMaterialPathError(path, "outside_entry") from error
    if any(part in {"", ".", ".."} for part in parts):
        raise EntryMaterialPathError(path, "aliased")
    current = root
    for index, part in enumerate(parts):
        current /= part
        if not current.is_symlink():
            continue
        if index == 0 and part in ENTRY_MATERIAL_DIRECTORY_NAMES and current.is_dir():
            continue
        reason = "unavailable_material_root" if index == 0 else "symlink"
        raise EntryMaterialPathError(path, reason)
    return target.resolve()


def validate_local_path_symlinks(path: Path, entry_root: Path) -> Path:
    """Resolve one local path after rejecting unsupported lexical symlinks.

    Entry-root ``data`` and ``images`` directory symlinks remain allowed. A
    shared ancestor alias is allowed only when its canonical target contains
    both the entry root and the requested target; this preserves platform path
    aliases without accepting an external material alias.
    """

    root = entry_root.resolve()
    target = path.absolute()
    canonical_target = target.resolve()
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current /= part
        if part in {"", ".", ".."} or not current.is_symlink():
            continue
        try:
            relative = current.absolute().relative_to(root.absolute())
        except ValueError:
            relative = None
        if (
            relative is not None
            and len(relative.parts) == 1
            and relative.name in ENTRY_MATERIAL_DIRECTORY_NAMES
            and current.is_dir()
        ):
            continue
        canonical_component = current.resolve()
        if _within(root, canonical_component) and _within(
            canonical_target, canonical_component
        ):
            continue
        raise EntryMaterialPathError(path, "symlink")
    return canonical_target


def is_entry_material_path(path: Path, entry_root: Path) -> bool:
    """Return whether a lexical path belongs to entry-local data or images."""

    root = entry_root.absolute()
    target = path.absolute()
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return False
    if not parts or parts[0] not in ENTRY_MATERIAL_DIRECTORY_NAMES:
        return False
    validate_entry_path_symlinks(target, root)
    return True


def is_entry_material_root(path: Path, entry_root: Path) -> bool:
    """Return whether a path identifies the exact entry data or images root."""

    root = Path(os.path.abspath(entry_root))
    target = Path(os.path.abspath(path))
    material_roots = tuple(root / name for name in ENTRY_MATERIAL_DIRECTORY_NAMES)
    if target in material_roots:
        return True
    try:
        canonical_target = target.resolve()
        return any(
            canonical_target == material_root.resolve()
            for material_root in material_roots
        )
    except OSError:
        return False


def entry_material_roots(entry_root: Path) -> tuple[Path, ...]:
    """Return canonical roots whose files are owned by one entry."""

    root = entry_root.resolve()
    roots = [root]
    for name in sorted(ENTRY_MATERIAL_DIRECTORY_NAMES):
        lexical = root / name
        if not lexical.is_symlink():
            continue
        canonical = validate_entry_path_symlinks(lexical, root)
        if not canonical.is_dir():
            raise EntryMaterialPathError(lexical, "unavailable_material_root")
        roots.append(canonical)
    return tuple(roots)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
