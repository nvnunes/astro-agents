"""Canonical log and entry context resolution for management commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import ActionError

ENTRY_ID_RE = re.compile(r"e(?P<number>[0-9]{3,})\Z")
ENTRY_DOCUMENT_RE = re.compile(r"(?P<id>e[0-9]{3,})(?P<suffix>[a-z]?)\.md\Z")
ENTRY_DIRECTORY_RE = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})-"
    r"(?P<id>e[0-9]{3,})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\Z"
)


@dataclass(frozen=True)
class LogContext:
    """One canonical maintained summary and matching log directory."""

    summary: Path
    root: Path


@dataclass(frozen=True)
class LogCreationContext:
    """One canonical not-yet-created log and its owning Git project."""

    summary: Path
    root: Path
    project_root: Path


@dataclass(frozen=True)
class EntryContext:
    """One stable entry identity resolved within a maintained log."""

    log: LogContext
    id: str
    root: Path


@dataclass(frozen=True)
class EntryDirectoryIdentity:
    """One canonical entry directory identity parsed from its basename."""

    date: str
    id: str
    slug: str


@dataclass(frozen=True)
class EntryDocumentIdentity:
    """One canonical entry document identity parsed from its basename."""

    id: str
    suffix: str


def entry_number(value: str) -> int | None:
    """Return the positive numeric component of one canonical entry ID."""

    match = ENTRY_ID_RE.fullmatch(value)
    if match is None:
        return None
    number = int(match.group("number"))
    return number if number > 0 else None


def parse_entry_directory_name(value: str) -> EntryDirectoryIdentity | None:
    """Return one fully canonical date-ID-slug directory identity."""

    match = ENTRY_DIRECTORY_RE.fullmatch(value)
    if match is None or entry_number(match.group("id")) is None:
        return None
    try:
        parsed_date = date.fromisoformat(match.group("date"))
    except ValueError:
        return None
    if parsed_date.isoformat() != match.group("date"):
        return None
    return EntryDirectoryIdentity(
        match.group("date"), match.group("id"), match.group("slug")
    )


def parse_entry_document_name(value: str) -> EntryDocumentIdentity | None:
    """Return one canonical stable-ID and optional sub-entry suffix."""

    match = ENTRY_DOCUMENT_RE.fullmatch(value)
    if match is None or entry_number(match.group("id")) is None:
        return None
    return EntryDocumentIdentity(match.group("id"), match.group("suffix"))


def resolve_log_creation(
    value: Path | None, *, cwd: Path | None = None
) -> LogCreationContext:
    """Resolve an explicit logical ``<log>`` base before it exists."""

    if value is None:
        raise ActionError("log.path.required", "init requires --path")
    current = (cwd or Path.cwd()).resolve()
    lexical = value if value.is_absolute() else current / value
    if (
        lexical.suffix == ".md"
        or lexical.name == "entries"
        or any(character in lexical.name for character in "<>\r\n")
        or lexical.is_symlink()
    ):
        raise ActionError("log.path.invalid", "--path names the logical log base")
    parent = lexical.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ActionError(
            "log.path.invalid", "--path must have an existing regular parent"
        )
    parent = parent.resolve()
    root = parent / lexical.name
    project_root = _project_root(parent)
    try:
        root.relative_to(project_root)
    except ValueError as error:
        raise ActionError(
            "log.path.invalid", "--path must be inside its project"
        ) from error
    return LogCreationContext(
        summary=parent / f"{root.name}.md",
        root=root,
        project_root=project_root,
    )


def resolve_log(value: Path | None, *, cwd: Path | None = None) -> LogContext:
    """Resolve one logical ``<log>`` base without broad filesystem search."""

    current = (cwd or Path.cwd()).resolve()
    if value is not None:
        lexical = value if value.is_absolute() else current / value
        if lexical.suffix == ".md" or lexical.name == "entries":
            raise ActionError("log.path.invalid", "--path names the logical log base")
        if lexical.is_symlink():
            raise ActionError("log.path.invalid", "--path must not be a symlink")
        root = lexical.resolve()
        summary = root.parent / f"{root.name}.md"
        if not root.is_dir() or summary.is_symlink() or not summary.is_file():
            raise ActionError("log.path.invalid", f"not a maintained log: {lexical}")
        return LogContext(summary.resolve(), root)
    matches: list[LogContext] = []
    for candidate in (current, *current.parents):
        summary = candidate.parent / f"{candidate.name}.md"
        entries = candidate / "entries"
        if (
            not entries.is_symlink()
            and entries.is_dir()
            and not summary.is_symlink()
            and summary.is_file()
        ):
            matches.append(LogContext(summary.resolve(), candidate.resolve()))
    if len(matches) != 1:
        raise ActionError(
            "log.context.ambiguous",
            "working directory must resolve exactly one maintained log",
        )
    return matches[0]


def resolve_entry(log: LogContext, entry_id: str) -> EntryContext:
    """Resolve one stable entry ID to exactly one canonical entry directory."""

    if entry_number(entry_id) is None:
        raise ActionError("entry.id.invalid", f"invalid entry ID: {entry_id}")
    entries = log.root / "entries"
    if entries.is_symlink() or not entries.is_dir():
        raise ActionError(
            "entry.identity.unresolved", "log has no regular entries root"
        )
    matches = sorted(
        path
        for path in entries.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (identity := parse_entry_directory_name(path.name)) is not None
        and identity.id == entry_id
    )
    if len(matches) != 1:
        raise ActionError(
            "entry.identity.unresolved",
            f"expected one directory for {entry_id}, found {len(matches)}",
        )
    return EntryContext(log, entry_id, matches[0].resolve())


def resolve_project_root(path: Path) -> Path:
    """Return the Git project that owns one log path."""

    return _project_root(path.resolve())


def _project_root(path: Path) -> Path:
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if not marker.is_symlink() and (marker.is_file() or marker.is_dir()):
            return candidate
    raise ActionError(
        "log.project.unresolved", f"could not resolve Git project for {path}"
    )
