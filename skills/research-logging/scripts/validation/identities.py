"""Scope-aware content identities for research-log validation."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from .contracts import FileChangedError
from .discovery import section_definitions, section_ranges
from .inventory import file_identity


def _filtered_text_identity(path: Path, text: str) -> dict[str, Any]:
    """Identify filtered text while rejecting concurrent source edits."""

    before = path.stat()
    payload = text.encode("utf-8")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FileChangedError(f"file changed during identity check: {path}")
    return {
        "size": len(payload),
        "mtime_ns": 0,
        "ctime_ns": 0,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def summary_validation_identity(path: Path) -> dict[str, Any]:
    """Identify research content while excluding validation navigation scaffolding."""

    path = path.resolve()
    filtered = _summary_validation_text(path, path.read_text(encoding="utf-8"))
    return _filtered_text_identity(path, filtered)


def summary_validation_text_identity(path: Path, text: str) -> dict[str, Any]:
    """Identify supplied summary text under one path's projection contract."""

    payload = _summary_validation_text(path, text).encode("utf-8")
    return {
        "size": len(payload),
        "mtime_ns": 0,
        "ctime_ns": 0,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _summary_validation_text(path: Path, text: str) -> str:
    """Return validation-relevant summary text under one path's link contract."""

    lines = text.splitlines()
    retained = []
    excluded = False
    fixed_link = summary_validation_link(path)
    for line in lines:
        if line.startswith("## "):
            excluded = line in {"## Validation", "## AI Use"}
        if not excluded and line not in {"- [Validation](#validation)", fixed_link}:
            retained.append(line)
    return "\n".join(retained) + "\n"


def summary_validation_link(path: Path) -> str:
    """Return the fixed validation-report navigation line for one summary."""

    return f"Validation: [latest completed report]({path.stem}/validation.md)"


def entry_validation_identity(path: Path) -> dict[str, Any]:
    """Identify only experimental and structurally invalid entry sections."""

    path = path.resolve()
    lines = path.read_text(encoding="utf-8").splitlines()
    sections = section_ranges(lines)
    retained_sections = []
    for definition in section_definitions(lines, sections):
        if definition["type"] not in {"experimental", "invalid"}:
            continue
        content = lines[definition["line"] - 1 : definition["end_line"]]
        while content and not content[-1].strip():
            content.pop()
        retained_sections.append("\n".join(content))
    return _filtered_text_identity(path, "\n\n".join(retained_sections) + "\n")


def validation_file_identity(
    scan: Mapping[str, Any], identity: str, path: Path
) -> dict[str, Any]:
    """Apply the scope-aware identity contract for one validation dependency."""

    if identity == scan.get("summary"):
        return summary_validation_identity(path)
    entry_paths = {
        entry.get("path") for entry in scan.get("entries", []) if "error" not in entry
    }
    if identity in entry_paths:
        return entry_validation_identity(path)
    return file_identity(path)


def repository_validation_identity(
    project_root: Path,
    identity: str,
    summary_paths: Sequence[Path],
) -> dict[str, Any]:
    """Apply persisted validation identity semantics to a repository source."""

    candidate = Path(identity)
    path = (
        candidate if candidate.is_absolute() else project_root / candidate
    ).resolve()
    summaries = [summary.resolve() for summary in summary_paths]
    if path in summaries:
        return summary_validation_identity(path)
    if path.suffix.lower() == ".md" and any(
        path.is_relative_to(summary.with_suffix("") / "entries")
        for summary in summaries
    ):
        return entry_validation_identity(path)
    return file_identity(path)


def text_content_identity(text: str) -> dict[str, Any]:
    """Return a stable SHA-256 identity for generated UTF-8 text."""

    encoded = text.encode("utf-8")
    return {"size": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
