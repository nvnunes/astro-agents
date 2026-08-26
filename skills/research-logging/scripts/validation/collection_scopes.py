"""Compact directory choices for collection-scope review."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, NamedTuple

from .contracts import ValidationToolError

DIRECTORY_SELECTOR_KEYS = frozenset({"directory", "membership_identity"})
COLLECTION_DIRECTORY_SELECTION_KEY = "collection_directory_selection"
COLLECTION_DIRECTORY_SELECTIONS_KEY = "collection_directory_selections"


class DirectorySelection(NamedTuple):
    """One exact directory selector and its expanded regular-file members."""

    directory: str
    membership_identity: str
    members: list[str]


def _relative_child(value: Any, description: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationToolError(f"{description} must be a nonempty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValidationToolError(f"{description} must be a relative child path")
    return relative


def directory_selection(root: Path, relative_directory: Any) -> DirectorySelection:
    """Identify every regular-file descendant of one collection subdirectory."""

    relative = _relative_child(
        relative_directory, "collection member directory"
    )
    directory = root / relative
    if not directory.is_dir():
        raise ValidationToolError(
            "collection member directory does not exist: "
            f"{relative.as_posix()}"
        )
    members = sorted(
        child.relative_to(root).as_posix()
        for child in directory.rglob("*")
        if child.is_file()
    )
    if not members:
        raise ValidationToolError(
            "collection member directory has no files: "
            f"{relative.as_posix()}"
        )
    # NUL cannot occur in a POSIX filename, so this remains unambiguous even
    # when a valid member name contains a newline.
    payload = b"\0".join(member.encode("utf-8") for member in members)
    return DirectorySelection(
        relative.as_posix(), hashlib.sha256(payload).hexdigest(), members
    )


def compact_directory_choices(root: Path) -> list[dict[str, Any]]:
    """Return one structural choice for each nonempty direct subdirectory."""

    choices = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        try:
            selection = directory_selection(root, child.name)
        except ValidationToolError:
            continue
        choices.append(
            {
                "relative_directory": selection.directory,
                "regular_file_descendant_count": len(selection.members),
                "membership_identity": selection.membership_identity,
                "selector": {
                    "directory": selection.directory,
                    "membership_identity": selection.membership_identity,
                },
            }
        )
    return choices


def validated_directory_selector(
    root: Path, selector: Mapping[str, Any]
) -> DirectorySelection:
    """Validate one hash-bound directory selector against current membership."""

    if set(selector) != DIRECTORY_SELECTOR_KEYS:
        raise ValidationToolError(
            "collection directory selector must contain exactly directory and "
            "membership_identity"
        )
    expected = selector.get("membership_identity")
    if not isinstance(expected, str) or len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise ValidationToolError(
            "collection directory selector membership_identity must be a "
            "lowercase SHA-256"
        )
    selection = directory_selection(root, selector.get("directory"))
    if selection.membership_identity != expected:
        raise ValidationToolError(
            "collection directory membership changed after review packet creation: "
            f"{selection.directory}"
        )
    return selection
