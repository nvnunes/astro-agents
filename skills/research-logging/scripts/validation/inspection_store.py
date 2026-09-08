"""Disposable, transactional storage for selectively inspected validation results.

Each log owns one SQLite cache. Replacing a batch deletes only that batch's
previous result; publishing a full result clears the preceding cycle. Reads
never evaluate research sources or repair the store. Large arrays and strings
are stored separately so fetching an entity does not decode its membership.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

STORE_VERSION = 3
STORE_NAME = "research-log-inspection.sqlite3"
CHUNK_CHARACTERS = 1024  # At most 4096 UTF-8 bytes.
MAX_NODE_BYTES = 8 * 1024

DDL = """
CREATE TABLE state (generation INTEGER NOT NULL);
INSERT INTO state VALUES (0);
CREATE TABLE results (
 id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL, slot TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL, entry TEXT NOT NULL, chain TEXT NOT NULL,
 validation TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE INDEX result_selection ON results(kind, entry, chain, validation, sequence);
CREATE TABLE entities (
 result TEXT REFERENCES results(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, id TEXT NOT NULL, entry TEXT NOT NULL, chain TEXT NOT NULL,
 code TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(result, kind, id)
);
CREATE INDEX entity_selection ON entities(result, kind, entry, chain, code, id);
CREATE TABLE links (
 result TEXT REFERENCES results(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, id TEXT NOT NULL, entry TEXT NOT NULL,
 chain TEXT NOT NULL, code TEXT NOT NULL,
 PRIMARY KEY(result, kind, id, entry, chain, code)
);
CREATE INDEX link_selection ON links(result, kind, entry, chain, code, id);
CREATE TABLE pieces (
 result TEXT REFERENCES results(id) ON DELETE CASCADE,
 ref TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(result, ref, position)
);
"""


class InspectionError(ValueError):
    """A precise cache failure, never permission to reevaluate research sources."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def encode(value: Any) -> str:
    """Encode tool-owned JSON with stable keys and no non-finite values."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_path(log_root: Path, *, writable: bool) -> Path:
    directory = log_root / ".cache"
    path = directory / STORE_NAME
    for item in (
        log_root,
        directory,
        path,
        *(Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")),
    ):
        if item.is_symlink():
            raise InspectionError("results.store.malformed", f"symlink: {item}")
    if writable:
        directory.mkdir(exist_ok=True)
    elif not path.is_file():
        raise InspectionError("results.store.missing", "inspection cache is absent")
    return path


def _open(path: Path, writable: bool) -> sqlite3.Connection:
    uri = path.as_uri() + ("?mode=rwc" if writable else "?mode=ro")
    db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if writable:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2, STORE_VERSION) or (
        not writable and version != STORE_VERSION
    ):
        db.close()
        raise InspectionError(
            "results.schema.unsupported",
            f"store version {version}; run full validation to rebuild results",
        )
    if writable and version < STORE_VERSION:
        from .inspection_batches import BATCH_DDL

        try:
            db.executescript(
                "BEGIN IMMEDIATE;"
                + "".join(
                    f"DROP TABLE IF EXISTS {table};"
                    for table in (
                        "batch_links",
                        "batch_requests",
                        "pieces",
                        "links",
                        "entities",
                        "results",
                        "state",
                    )
                )
                + DDL
                + BATCH_DDL
                + f"PRAGMA user_version={STORE_VERSION};COMMIT;"
            )
        except BaseException:
            db.close()
            raise
    return db


@contextmanager
def connection(
    log_root: Path, *, writable: bool = False
) -> Iterator[sqlite3.Connection]:
    """Open the owned store; map database errors to bounded public failures."""
    db = None
    try:
        path = _safe_path(log_root, writable=writable)
        db = _open(path, writable)
        db.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
        yield db
        db.commit()
    except sqlite3.Error as error:
        code = "results.store.malformed"
        if isinstance(error, sqlite3.OperationalError):
            code = (
                "results.store.busy"
                if "locked" in str(error)
                else (
                    "results.store.write_failed"
                    if writable
                    else "results.store.malformed"
                )
            )
        raise InspectionError(code, str(error)[:1024]) from error
    finally:
        if db is not None:
            db.close()


class ContentWriter:
    """Pack entity content into bounded nodes and reusable paged pieces."""

    def __init__(self, db: sqlite3.Connection, result_id: str):
        self.db = db
        self.result_id = result_id
        self.references: set[str] = set()

    def _pieces(self, value: Any, kind: str) -> dict[str, Any]:
        raw = encode(value)
        ref = hashlib.sha256(raw.encode()).hexdigest()
        if ref not in self.references:
            self.references.add(ref)
            values = (
                list(
                    value[i : i + CHUNK_CHARACTERS]
                    for i in range(0, len(value), CHUNK_CHARACTERS)
                )
                if kind == "value"
                else value
            )
            for position, item in enumerate(values):
                payload = encode(item if kind == "value" else self.pack(item))
                self.db.execute(
                    "INSERT INTO pieces VALUES (?, ?, ?, ?)",
                    (self.result_id, ref, position, payload),
                )
        self.entity(
            "collections",
            ref,
            {"collection_id": ref, "count": len(value), "kind": kind},
        )
        return {"type": kind, "ref": ref, "count": len(value)}

    def pack(self, value: Any, *, defer: bool = False) -> Any:
        """Tag objects to distinguish authored values from inspection references."""
        if isinstance(value, list):
            if len(value) <= 8 and not defer:
                node: dict[str, Any] = {
                    "type": "array",
                    "items": [self.pack(item) for item in value],
                }
                if len(encode(node).encode()) <= 2048:
                    return node
            return self._pieces(value, "collection")
        if isinstance(value, str) and len(value.encode()) > 2048:
            return self._pieces(value, "value")
        if isinstance(value, dict):
            fields = {
                key: self.pack(item, defer=key == "members")
                for key, item in value.items()
            }
            node = {"type": "object", "fields": fields}
            if len(encode(node).encode()) > MAX_NODE_BYTES:
                return {
                    "type": "mapping",
                    **self._pieces(
                        [{"key": key, "value": item} for key, item in value.items()],
                        "collection",
                    ),
                    "mapping": True,
                }
            return node
        return value

    def entity(
        self,
        kind: str,
        identity: str,
        value: dict[str, Any],
        selectors: tuple[str, str, str] = ("", "", ""),
    ) -> None:
        """Store a typed entity once; duplicates must have identical content."""
        payload = encode(self.pack(value))
        self.db.execute(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?, ?)",
            (self.result_id, kind, identity, *selectors),
        )
        old = self.db.execute(
            "SELECT payload FROM entities WHERE result=? AND kind=? AND id=?",
            (self.result_id, kind, identity),
        ).fetchone()
        if old is not None:
            if old[0] != payload:
                raise InspectionError(
                    "results.store.malformed", f"conflicting {kind}: {identity}"
                )
            return
        self.db.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.result_id, kind, identity, *selectors, payload),
        )


def _node_kind(value: Any, depth: int) -> str | None:
    """Validate the tagged node before interpreting its fields."""
    if depth > 64 or isinstance(value, list):
        raise InspectionError("results.store.malformed", "invalid stored node nesting")
    if not isinstance(value, dict):
        return None
    kind = value.get("type")
    valid = False
    if kind == "array":
        valid = isinstance(value.get("items"), list)
    elif kind == "object":
        valid = isinstance(value.get("fields"), dict)
    elif kind in ("collection", "value"):
        valid = (
            isinstance(value.get("ref"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["ref"]) is not None
            and type(value.get("count")) is int
            and value["count"] >= 0
            and type(value.get("mapping", False)) is bool
        )
    if not valid:
        raise InspectionError("results.store.malformed", "invalid stored node fields")
    return kind


def unpack_view(value: Any, *, _depth: int = 0) -> Any:
    """Validate bounded content and expose references without expanding pieces."""
    kind = _node_kind(value, _depth)
    if kind == "array":
        return [unpack_view(item, _depth=_depth + 1) for item in value["items"]]
    if kind == "object":
        return {
            key: unpack_view(item, _depth=_depth + 1)
            for key, item in value["fields"].items()
        }
    return value


def decode_node(raw: str) -> Any:
    """Validate one stored node's byte bound and JSON before exposing it."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_NODE_BYTES * 2:
        raise InspectionError(
            "results.store.malformed", "invalid stored node size or type"
        )
    try:
        return json.loads(
            raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise InspectionError(
            "results.store.malformed", "invalid stored JSON"
        ) from error


def collection_metadata(
    db: sqlite3.Connection, result_id: str, ref: str
) -> dict[str, Any]:
    """Read one collection manifest and derive its exact stored piece count."""
    row = db.execute(
        "SELECT payload FROM entities WHERE result=? AND kind='collections' AND id=?",
        (result_id, ref),
    ).fetchone()
    if row is None:
        raise InspectionError("results.entity.missing", f"unknown reference: {ref}")
    value = unpack_view(decode_node(row[0]))
    if (
        not isinstance(value, dict)
        or value.get("collection_id") != ref
        or value.get("kind") not in ("collection", "value")
        or type(value.get("count")) is not int
        or value["count"] < 0
    ):
        raise InspectionError("results.store.malformed", "invalid collection manifest")
    count = value["count"]
    value["piece_count"] = (
        (count + CHUNK_CHARACTERS - 1) // CHUNK_CHARACTERS
        if value["kind"] == "value"
        else count
    )
    return value


def read_pieces(
    db: sqlite3.Connection,
    result_id: str,
    manifest: dict[str, Any],
    start: int,
    limit: int,
) -> list[Any]:
    """Seek a member range and reject holes or invalid text chunks in that range."""
    total = manifest["piece_count"]
    size = min(limit, total - start)
    rows = db.execute(
        "SELECT position, payload FROM pieces WHERE result=? AND ref=? "
        "AND position>=? ORDER BY position LIMIT ?",
        (result_id, manifest["collection_id"], start, size + 1),
    ).fetchall()
    expected = size + (start + size < total)
    if len(rows) != expected:
        raise InspectionError(
            "results.store.malformed", "missing or extra collection pieces"
        )
    items = []
    for offset, row in enumerate(rows):
        position = start + offset
        if row[0] != position:
            raise InspectionError(
                "results.store.malformed", "noncontiguous collection pieces"
            )
        node = decode_node(row[1])
        if manifest["kind"] == "value":
            length = min(
                CHUNK_CHARACTERS, manifest["count"] - position * CHUNK_CHARACTERS
            )
            if not isinstance(node, str) or len(node) != length:
                raise InspectionError("results.store.malformed", "invalid text chunk")
        if offset < size:
            items.append(node)
    return items


def expand(db: sqlite3.Connection, result_id: str, value: Any) -> Any:
    """Export complete validated content; reject missing pieces and reference cycles."""
    return _expand(db, result_id, value, frozenset(), 0)


def _expand(
    db: sqlite3.Connection,
    result_id: str,
    value: Any,
    active: frozenset[str],
    depth: int,
) -> Any:
    kind = _node_kind(value, depth)
    if kind is None:
        return value
    if kind == "array":
        return [
            _expand(db, result_id, item, active, depth + 1) for item in value["items"]
        ]
    if kind == "object":
        return {
            key: _expand(db, result_id, item, active, depth + 1)
            for key, item in value["fields"].items()
        }
    ref = value["ref"]
    if ref in active:
        raise InspectionError("results.store.malformed", "cyclic collection reference")
    try:
        manifest = collection_metadata(db, result_id, ref)
    except InspectionError as error:
        if error.code == "results.entity.missing":
            raise InspectionError(
                "results.store.malformed", "missing referenced collection"
            ) from error
        raise
    if manifest["kind"] != kind or manifest["count"] != value["count"]:
        raise InspectionError(
            "results.store.malformed", "inconsistent collection reference"
        )
    nodes = read_pieces(db, result_id, manifest, 0, manifest["piece_count"])
    items = [_expand(db, result_id, item, active | {ref}, depth + 1) for item in nodes]
    if kind == "value":
        return "".join(items)
    if value.get("mapping"):
        return _expand_mapping(items)
    return items


def _expand_mapping(items: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or "value" not in item
            or item["key"] in result
        ):
            raise InspectionError("results.store.malformed", "invalid mapping member")
        result[item["key"]] = item["value"]
    return result


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate stored JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")
