"""Path policy for entry-owned material directories and their symlinks."""

from __future__ import annotations

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
        if (
            index == 0
            and part in ENTRY_MATERIAL_DIRECTORY_NAMES
            and current.is_dir()
        ):
            continue
        reason = "unavailable_material_root" if index == 0 else "symlink"
        raise EntryMaterialPathError(path, reason)
    return target.resolve()


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
