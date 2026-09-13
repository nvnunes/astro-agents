"""Canonical maintained-summary discovery for validation orchestration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

DISCOVERY_SCHEMA = "research-log-discovery-result/1"
MAX_MARKDOWN_FILES = 100_000
MAX_HEADER_CHARACTERS = 64 * 1024
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".conda",
        ".cache",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)


class SummaryDiscoveryError(ValueError):
    """Raised when maintained-summary discovery cannot finish safely."""


def discover_summaries(root: Path) -> dict[str, object]:
    """Return every maintained summary below one regular project root.

    Discovery uses only the regular summary, sibling log root, and entries root.
    """

    if root.is_symlink():
        raise SummaryDiscoveryError(f"discovery root must not be a symlink: {root}")
    root = root.resolve()
    if not root.is_dir():
        raise SummaryDiscoveryError(
            f"discovery root must be a regular directory: {root}"
        )
    summaries = [
        path.resolve().as_posix()
        for path in _markdown_candidates(root)
        if _is_maintained_summary(path)
    ]
    return {
        "root": root.as_posix(),
        "schema": DISCOVERY_SCHEMA,
        "summaries": sorted(summaries),
    }


def _markdown_candidates(root: Path) -> Iterator[Path]:
    markdown_files = 0
    for directory, names, files in os.walk(
        root, topdown=True, onerror=_raise_walk_error, followlinks=False
    ):
        directory_path = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name not in IGNORED_DIRECTORY_NAMES
            and not (directory_path / name).is_symlink()
        )
        for name in sorted(files):
            if Path(name).suffix.lower() != ".md":
                continue
            markdown_files += 1
            if markdown_files > MAX_MARKDOWN_FILES:
                raise SummaryDiscoveryError(
                    "maintained-summary discovery crossed its Markdown-file bound"
                )
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def _raise_walk_error(error: OSError) -> None:
    raise SummaryDiscoveryError(
        f"could not traverse maintained-summary discovery root: {error}"
    ) from error


def _is_maintained_summary(path: Path) -> bool:
    """Return whether one regular Markdown path has the complete log pair."""

    log_root = path.with_suffix("")
    entries_root = log_root / "entries"
    return (
        not log_root.is_symlink()
        and log_root.is_dir()
        and not entries_root.is_symlink()
        and entries_root.is_dir()
    )
