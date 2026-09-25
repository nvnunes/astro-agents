"""Canonical maintained-entry directory identity shared by log tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

ENTRY_DIRECTORY_RE = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})-"
    r"(?P<id>e[0-9]{3,})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\Z"
)


@dataclass(frozen=True)
class EntryDirectoryIdentity:
    """One canonical entry directory identity parsed from its basename."""

    date: str
    id: str
    slug: str


def parse_entry_directory_name(value: str) -> EntryDirectoryIdentity | None:
    """Return a canonical date-ID-slug identity, or None for an invalid name."""

    match = ENTRY_DIRECTORY_RE.fullmatch(value)
    if match is None or int(match.group("id")[1:]) == 0:
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
