"""Grammar and normalized projections for authored Validation notes."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

_RETENTION_NOTE_RE = re.compile(
    r"^\s*-\s+Retain\s+`(?P<scope>[^`\n]+)`(?P<reason>[^`]*)$"
)
_BLANKET_ARTIFACT_ROOTS = frozenset({"data", "images"})


def retention_scope(text: str) -> str | None:
    """Return the authored scope of one explicit orphan-retention note."""

    match = _RETENTION_NOTE_RE.fullmatch(text)
    if match is None:
        return None
    scope = match.group("scope").strip()
    return scope or None


def normalized_retention_scope(scope: str, entry_path: str | None = None) -> str:
    """Return one lexical entry-relative POSIX retention scope."""

    if scope.startswith("<") and scope.endswith(">"):
        return scope
    normalized = posixpath.normpath(scope.replace("\\", "/"))
    normalized = "" if normalized == "." else normalized.removeprefix("./")
    if entry_path is not None:
        entry_directory = PurePosixPath(entry_path).parent.as_posix()
        if normalized == entry_directory:
            return ""
        prefix = f"{entry_directory}/"
        if normalized.startswith(prefix):
            return normalized.removeprefix(prefix)
    return normalized


def blanket_retention_error(scope: str) -> str | None:
    """Explain a structurally invalid blanket artifact-root scope."""

    normalized = normalized_retention_scope(scope)
    if normalized in _BLANKET_ARTIFACT_ROOTS:
        return (
            f"blanket artifact root `{normalized}` cannot authorize orphan "
            "retention"
        )
    return None


def orphan_retention_notes(
    notes: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return only notes that carry a validated orphan-retention scope."""

    return [
        note
        for note in notes
        if isinstance(note.get("retention_scope"), str)
        and bool(note["retention_scope"])
    ]
