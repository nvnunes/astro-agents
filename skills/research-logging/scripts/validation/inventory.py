"""Complete research-log-owned material inventory.

This module inventories the logical file surface beneath designated entry and
log roots. Paths reached through a log-owned symlink keep their logical path
identity while retaining the resolved target used for inspection.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from .contracts import FileChangedError, ValidationToolError

IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "pyrun",
    }
)
CODE_CONTAINER_NAMES = frozenset({"scripts"})
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, order=True)
class OwnedMaterial:
    """One path on a research log's logical owned surface."""

    logical_path: Path
    resolved_path: Path
    kind: str
    directory: bool


@dataclass(frozen=True)
class OwnedMaterialTree:
    """Material and every traversed directory below one ownership root."""

    material: tuple[OwnedMaterial, ...]
    directories: frozenset[Path]
    directory_memberships: Mapping[Path, Mapping[str, object]]


@dataclass(frozen=True)
class OwnedInventory:
    """Complete owned material, aliases, and membership boundaries for one log."""

    by_folder: Mapping[Path, list[OwnedMaterial]]
    log_material: tuple[OwnedMaterial, ...]
    paths: Mapping[str, Path]
    aliases: Mapping[Path, str]
    directory_boundaries: frozenset[Path]
    directory_memberships: Mapping[Path, Mapping[str, object]]

    def resolved_directory_boundaries(self, project_root: Path) -> Dict[str, str]:
        """Return logical identities and paths for every membership boundary."""

        return {
            logical_display_path(path, project_root): path.as_posix()
            for path in sorted(self.directory_boundaries)
        }


@dataclass(frozen=True)
class MaterialInventoryPolicy:
    """File classes and exclusions for complete log-owned inventory."""

    script_suffixes: frozenset[str]
    excluded_names: frozenset[str]


def hash_file(path: Path) -> tuple[str, int]:
    """Return a streaming SHA-256 digest and byte count for one file."""

    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            count += len(chunk)
    return digest.hexdigest(), count


def file_identity(path: Path) -> dict[str, Any]:
    """Return a concurrent-change-safe identity for a file or symlink."""

    path = Path(os.path.abspath(str(path.expanduser())))
    before = path.lstat()
    if path.is_symlink():
        target = os.readlink(str(path))
        resolved = path.resolve(strict=True)
        target_identity = file_identity(resolved)
        digest = hashlib.sha256(
            (
                f"{target}\0{target_identity['size']}\0"
                f"{target_identity['sha256']}"
            ).encode("utf-8")
        ).hexdigest()
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise FileChangedError(f"symlink changed during identity check: {path}")
        return {
            "size": target_identity["size"],
            "mtime_ns": max(before.st_mtime_ns, int(target_identity["mtime_ns"])),
            "ctime_ns": max(before.st_ctime_ns, int(target_identity["ctime_ns"])),
            "sha256": digest,
        }

    if path.is_file():
        digest, size = hash_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise FileChangedError(f"file changed during identity check: {path}")
        return {
            "size": size,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
            "sha256": digest,
        }

    raise ValidationToolError(f"file identity requires a file or symlink: {path}")


def collection_identity(path: Path, members: Sequence[str]) -> dict[str, Any]:
    """Identify explicitly selected regular files in one directory."""

    path = Path(os.path.abspath(str(path.expanduser())))
    if not path.is_dir():
        raise ValidationToolError(f"collection dependency is not a directory: {path}")
    normalized = sorted(set(members))
    if not normalized:
        raise ValidationToolError(
            f"collection dependency has no selected members: {path}"
        )

    digest = hashlib.sha256()
    total_size = 0
    latest_mtime = 0
    latest_ctime = 0
    for raw in normalized:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationToolError(f"collection member escapes its directory: {raw}")
        member = path / relative
        if not member.is_file():
            raise ValidationToolError(
                f"collection member is not a regular file: {member}"
            )
        identity = file_identity(member)
        size = int(identity["size"])
        mtime = int(identity["mtime_ns"])
        ctime = int(identity["ctime_ns"])
        total_size += size
        latest_mtime = max(latest_mtime, mtime)
        latest_ctime = max(latest_ctime, ctime)
        digest.update(
            (
                f"{relative.as_posix()}\0{size}\0{identity['sha256']}\n"
            ).encode("utf-8")
        )
    return {
        "size": total_size,
        "mtime_ns": latest_mtime,
        "ctime_ns": latest_ctime,
        "sha256": digest.hexdigest(),
        "members": [Path(item).as_posix() for item in normalized],
    }


def content_identity(identity: Mapping[str, Any]) -> Dict[str, Any]:
    """Return content equality fields, excluding metadata used only to skip hashes."""

    return {
        key: value
        for key, value in identity.items()
        if key not in {"mtime_ns", "ctime_ns"}
    }


def logical_display_path(path: Path, project_root: Path) -> str:
    """Return a project-relative identity without resolving owned symlinks."""

    absolute = Path(os.path.abspath(str(path.expanduser())))
    project = project_root.resolve()
    try:
        return absolute.relative_to(project).as_posix()
    except ValueError:
        return absolute.as_posix()


def display_path(path: Path, project_root: Path) -> str:
    """Return a resolved project-relative identity when possible."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def infer_project_root(summary_path: Path) -> Path:
    """Return the source-control-independent root for a maintained summary.

    A summary beneath ``docs`` belongs to the directory containing its nearest
    ``docs`` ancestor. Other summaries belong to their containing directory.
    """

    summary_path = summary_path.resolve()
    for parent in summary_path.parents:
        if parent.name == "docs":
            return parent.parent
    return summary_path.parent


def directory_membership_identity(
    path: Path, ignored_paths: Iterable[Path] = ()
) -> dict[str, object]:
    """Return a direct path-and-type membership fingerprint for one directory."""

    path = Path(os.path.abspath(str(path.expanduser())))
    if not path.is_dir():
        raise ValidationToolError(f"directory membership requires a directory: {path}")
    ignored = {item.resolve() for item in ignored_paths}
    members = []
    for member in sorted(path.iterdir()):
        if member.resolve() in ignored:
            continue
        if member.is_symlink():
            kind = "symlink"
        elif member.is_dir():
            kind = "directory"
        else:
            kind = "file"
        members.append(f"{kind}\0{member.name}")
    payload = "\n".join(sorted(members)).encode("utf-8")
    return {
        "members": len(members),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _ignored(path: Path, excluded_names: frozenset[str]) -> bool:
    return (
        path.name in IGNORED_NAMES
        or path.name in excluded_names
        or path.suffix.lower() == ".md"
    )


def _inventory_owned_tree(
    root: Path,
    *,
    script_suffixes: Iterable[str],
    excluded_names: Iterable[str],
    excluded_top_level: Sequence[str] = (),
    membership_ignored_paths: Iterable[Path] = (),
) -> OwnedMaterialTree:
    """Enumerate material and membership boundaries below one designated root.

    The walk follows directory symlinks because their targets are explicitly
    owned through the log path. An active-directory identity set prevents
    symlink cycles while allowing the same target to be exposed deliberately
    through more than one logical path.
    """

    root = Path(os.path.abspath(str(root.expanduser())))
    if not root.is_dir():
        return OwnedMaterialTree((), frozenset(), {})
    suffixes = frozenset(suffix.lower() for suffix in script_suffixes)
    excluded = frozenset(excluded_names)
    excluded_top = frozenset(excluded_top_level)
    material: list[OwnedMaterial] = []
    directories: set[Path] = set()
    memberships: Dict[Path, Mapping[str, object]] = {}
    ignored_members = tuple(membership_ignored_paths)

    def visit(directory: Path, active: frozenset[tuple[int, int]]) -> None:
        directories.add(directory)
        before_membership = directory_membership_identity(
            directory, ignored_members
        )
        status = directory.stat()
        identity = (status.st_dev, status.st_ino)
        if identity in active:
            memberships[directory] = before_membership
            return
        active = active | {identity}
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if directory == root and child.name in excluded_top:
                continue
            if _ignored(child, excluded):
                continue
            is_directory = child.is_dir()
            if is_directory:
                if child.name not in CODE_CONTAINER_NAMES:
                    material.append(
                        OwnedMaterial(
                            logical_path=child,
                            resolved_path=child.resolve(),
                            kind="artifact",
                            directory=True,
                        )
                    )
                visit(child, active)
                continue
            kind = "script" if child.suffix.lower() in suffixes else "artifact"
            material.append(
                OwnedMaterial(
                    logical_path=child,
                    resolved_path=child.resolve(strict=False),
                    kind=kind,
                    directory=False,
                )
            )

        after_membership = directory_membership_identity(directory, ignored_members)
        if before_membership != after_membership:
            raise FileChangedError(
                f"owned directory changed during inventory: {directory}"
            )
        memberships[directory] = after_membership

    visit(root, frozenset())
    return OwnedMaterialTree(
        tuple(sorted(material)), frozenset(directories), memberships
    )


def inventory_owned_material(
    root: Path,
    *,
    script_suffixes: Iterable[str],
    excluded_names: Iterable[str],
    excluded_top_level: Sequence[str] = (),
) -> list[OwnedMaterial]:
    """Enumerate files and artifact containers below one designated root."""

    return list(
        _inventory_owned_tree(
            root,
            script_suffixes=script_suffixes,
            excluded_names=excluded_names,
            excluded_top_level=excluded_top_level,
        ).material
    )


def owned_inventory(
    log_root: Path,
    entry_folders: Iterable[Path],
    project_root: Path,
    policy: MaterialInventoryPolicy,
    *,
    membership_ignored_paths: Iterable[Path] = (),
) -> OwnedInventory:
    """Return complete entry/log material plus unambiguous owned aliases."""

    membership_ignored = tuple(membership_ignored_paths)
    entries_root = log_root / "entries"
    entries_before = (
        directory_membership_identity(entries_root, membership_ignored)
        if entries_root.is_dir()
        else None
    )
    folder_trees = {
        folder: _inventory_owned_tree(
            folder,
            script_suffixes=policy.script_suffixes,
            excluded_names=policy.excluded_names,
            membership_ignored_paths=membership_ignored,
        )
        for folder in sorted(set(entry_folders))
    }
    by_folder = {
        folder: list(tree.material) for folder, tree in folder_trees.items()
    }
    log_tree = _inventory_owned_tree(
        log_root,
        script_suffixes=policy.script_suffixes,
        excluded_names=policy.excluded_names,
        excluded_top_level=("entries",),
        membership_ignored_paths=membership_ignored,
    )
    log_material = list(log_tree.material)
    paths: Dict[str, Path] = {}
    aliases: Dict[Path, list[str]] = {}
    for item in [
        *(item for values in by_folder.values() for item in values),
        *log_material,
    ]:
        identity = logical_display_path(item.logical_path, project_root)
        if identity in paths:
            raise ValidationToolError(f"duplicate owned material identity: {identity}")
        paths[identity] = item.logical_path
        aliases.setdefault(item.resolved_path, []).append(identity)
    unique_aliases = {
        resolved: identities[0]
        for resolved, identities in aliases.items()
        if len(identities) == 1
    }
    boundaries = {
        *log_tree.directories,
        *(path for tree in folder_trees.values() for path in tree.directories),
    }
    if entries_root.is_dir():
        boundaries.add(entries_root)
        entries_after = directory_membership_identity(
            entries_root, membership_ignored
        )
        if entries_before != entries_after:
            raise FileChangedError(
                f"owned directory changed during inventory: {entries_root}"
            )
    memberships = {
        **log_tree.directory_memberships,
        **{
            path: identity
            for tree in folder_trees.values()
            for path, identity in tree.directory_memberships.items()
        },
    }
    if entries_root.is_dir() and entries_root not in memberships:
        memberships[entries_root] = directory_membership_identity(
            entries_root, membership_ignored
        )
    return OwnedInventory(
        by_folder,
        tuple(log_material),
        paths,
        unique_aliases,
        frozenset(boundaries),
        memberships,
    )


def owned_entry_folders(
    log_root: Path, folder_entry_ids: Mapping[Path, set[str]]
) -> set[Path]:
    """Return entry material roots, including artifact-only entry folders."""

    owned = set(folder_entry_ids)
    entries_root = log_root / "entries"
    if not entries_root.is_dir():
        return owned
    for child in entries_root.iterdir():
        if not child.is_dir():
            continue
        if any(
            folder == child or folder.is_relative_to(child)
            for folder in folder_entry_ids
        ):
            continue
        owned.add(child)
    return owned
