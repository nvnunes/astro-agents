"""Shared SQLite failure classification for disposable validation caches."""

from __future__ import annotations

import sqlite3

SQLITE_CORRUPTION_CODES = frozenset({11, 24, 26})


def is_sqlite_corruption(error: sqlite3.DatabaseError) -> bool:
    """Return whether SQLite has specifically identified corrupt storage."""

    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in SQLITE_CORRUPTION_CODES
    message = str(error).lower()
    return (
        "not a database" in message
        or "malformed" in message
        or "schema is incomplete" in message
    )


__all__ = ["is_sqlite_corruption"]
