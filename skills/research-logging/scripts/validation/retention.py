"""Strict entry-local ``retention.json`` contract."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, cast

from .entry_materials import EntryMaterialPathError, validate_entry_path_symlinks
from .errors import MechanicalContractError
from .filesystem import (
    BoundedFileReadError,
    BoundedTraversalError,
    bounded_descendants,
    bounded_file_bytes,
)
from .json_codec import V2JsonError, canonical_json, decode_json

RETENTION_SCHEMA = "research-log-retention/v1"
MAX_RETENTION_FILE_BYTES = 8 * 1024 * 1024
MAX_RETENTION_RECORDS = 1_000
MAX_RETENTION_PATHS = 10_000
MAX_RETENTION_DESCENDANTS = 100_000
MAX_RETENTION_REASON_BYTES = 2_048
MAX_RETENTION_ID_BYTES = 96
RECORD_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


class RetentionContractError(MechanicalContractError):
    """One precise retention declaration failure."""


@dataclass(frozen=True)
class RetentionRecord:
    """One exact-path or all-descendants retention declaration."""

    id: str
    paths: tuple[str, ...] = ()
    directory: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the canonical record object."""

        value: dict[str, object] = {"id": self.id}
        if self.paths:
            value["paths"] = list(self.paths)
        else:
            value["directory"] = self.directory
            value["membership"] = "all-descendants"
        if self.reason is not None:
            value["reason"] = self.reason
        return value


@dataclass(frozen=True)
class RetentionFile:
    """One strict entry-owned retention file."""

    path: Path
    entry_root: Path
    records: tuple[RetentionRecord, ...]

    @property
    def identity(self) -> str:
        """Return canonical content identity independent of record order."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        """Return canonical JSON with records ordered by ID."""

        return canonical_json(
            {
                "records": [
                    record.as_dict()
                    for record in sorted(self.records, key=lambda value: value.id)
                ],
                "schema": RETENTION_SCHEMA,
            }
        )


def load_retention_file(path: Path, *, entry_root: Path) -> RetentionFile:
    """Read one strict entry-root ``retention.json`` file."""

    entry_root_symlink = entry_root.is_symlink()
    entry_root = entry_root.resolve()
    expected = entry_root / "retention.json"
    if path.resolve() != expected.resolve() or path.is_symlink() or entry_root_symlink:
        _fail(
            "retention.file.location_invalid",
            str(path),
            {"expected": str(expected)},
            "research-log-retention/v1",
        )
    value = _read_json(path)
    if not isinstance(value, Mapping) or set(value) != {"schema", "records"}:
        _invalid(path, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    raw_records = value.get("records")
    if value.get("schema") != RETENTION_SCHEMA or not isinstance(raw_records, list):
        _invalid(path, {"schema": value.get("schema")})
    if not raw_records or len(raw_records) > MAX_RETENTION_RECORDS:
        _invalid(path, {"records": len(raw_records)})
    records = tuple(
        _decode_record(raw, f"{path}:records[{index}]", entry_root)
        for index, raw in enumerate(raw_records)
    )
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        _invalid(path, {"reason": "duplicate_ids", "ids": ids})
    _validate_overlaps(records, path)
    return RetentionFile(path=expected, entry_root=entry_root, records=records)


def _decode_record(value: object, subject: str, entry_root: Path) -> RetentionRecord:
    if not isinstance(value, Mapping):
        _invalid(subject, {"type": type(value).__name__})
    value = cast(Mapping[str, Any], value)
    record_id = _record_id(value.get("id"), subject)
    reason = value.get("reason")
    if reason is not None and (
        not isinstance(reason, str)
        or len(reason.encode("utf-8")) > MAX_RETENTION_REASON_BYTES
    ):
        _invalid(subject, {"reason": reason})
    if "paths" in value:
        expected = {"id", "paths"} | ({"reason"} if "reason" in value else set())
        paths = value.get("paths")
        if set(value) != expected or not isinstance(paths, list) or not paths:
            _invalid(subject, {"fields": sorted(value), "paths": paths})
        if len(paths) > MAX_RETENTION_PATHS or len(paths) != len(set(paths)):
            _invalid(subject, {"path_count": len(paths)})
        decoded = tuple(_retention_file(item, subject, entry_root) for item in paths)
        return RetentionRecord(record_id, paths=decoded, reason=reason)
    expected = {"directory", "id", "membership"} | (
        {"reason"} if "reason" in value else set()
    )
    if set(value) != expected or value.get("membership") != "all-descendants":
        _invalid(
            subject,
            {"fields": sorted(value), "membership": value.get("membership")},
        )
    directory = _retention_directory(value.get("directory"), subject, entry_root)
    return RetentionRecord(record_id, directory=directory, reason=reason)


def _record_id(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or len(value.encode("ascii")) > MAX_RETENTION_ID_BYTES
        or RECORD_ID_RE.fullmatch(value) is None
    ):
        _invalid(subject, {"id": value})
    return value


def _retention_file(value: object, subject: str, entry_root: Path) -> str:
    path = _normalized_relative(value, subject)
    target = entry_root.joinpath(*PurePosixPath(path).parts)
    _reject_symlink(target, entry_root, subject, path)
    if not target.is_file():
        _fail(
            "retention.target.missing",
            subject,
            {"path": path},
            "research-log-retention/v1",
        )
    return path


def _retention_directory(value: object, subject: str, entry_root: Path) -> str:
    path = _normalized_relative(value, subject)
    target = entry_root.joinpath(*PurePosixPath(path).parts)
    _reject_symlink(target, entry_root, subject, path)
    if not target.is_dir():
        _fail(
            "retention.target.missing",
            subject,
            {"directory": path},
            "research-log-retention/v1",
        )
    try:
        descendants = bounded_descendants(
            target, maximum_entries=MAX_RETENTION_DESCENDANTS
        )
    except BoundedTraversalError as error:
        _invalid(
            subject,
            {
                "directory": path,
                "limit": error.limit,
                "observed": error.observed,
                "reason": error.reason,
            },
        )
    for child in descendants:
        _reject_symlink(child, entry_root, subject, path)
    if not any(child.is_file() for child in descendants):
        _invalid(subject, {"directory": path, "reason": "empty"})
    return path


def _validate_overlaps(records: tuple[RetentionRecord, ...], path: Path) -> None:
    owners: dict[str, str] = {}
    directories: list[tuple[str, str]] = []
    for record in records:
        for target in record.paths:
            if target in owners:
                _invalid(
                    path, {"target": target, "records": [owners[target], record.id]}
                )
            owners[target] = record.id
        if record.directory is not None:
            directories.append((record.directory, record.id))
    for directory, record_id in directories:
        prefix = directory + "/"
        overlaps = [
            (target, owner)
            for target, owner in owners.items()
            if target == directory or target.startswith(prefix)
        ]
        nested = [
            (other, owner)
            for other, owner in directories
            if other != directory
            and (other.startswith(prefix) or directory.startswith(other + "/"))
        ]
        if overlaps or nested:
            _invalid(
                path,
                {
                    "directory": directory,
                    "record": record_id,
                    "overlaps": overlaps + nested,
                },
            )


def _normalized_relative(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        _invalid(subject, {"path": value})
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _invalid(subject, {"path": value})
    if pure.as_posix() != value:
        _invalid(subject, {"path": value})
    return value


def _reject_symlink(target: Path, root: Path, subject: str, path: str) -> None:
    try:
        validate_entry_path_symlinks(target, root)
    except EntryMaterialPathError as error:
        _invalid(subject, {"path": path, "reason": error.reason})


def _read_json(path: Path) -> object:
    try:
        raw = bounded_file_bytes(path, maximum_bytes=MAX_RETENTION_FILE_BYTES)
        text = raw.decode("utf-8")
    except BoundedFileReadError as error:
        _invalid(
            path,
            {
                "bytes": error.observed,
                "error": error.detail,
                "limit": error.limit,
                "reason": error.reason,
            },
        )
    except UnicodeError as error:
        _invalid(path, {"error": str(error)})
    try:
        return decode_json(
            text,
            maximum_bytes=MAX_RETENTION_FILE_BYTES,
            subject="retention.json",
        )
    except V2JsonError as error:
        _invalid(path, {"error": str(error)})


def _fields(value: object) -> object:
    return sorted(value) if isinstance(value, Mapping) else None


def _invalid(subject: object, observed: object) -> NoReturn:
    _fail(
        "retention.declaration.invalid",
        str(subject),
        observed,
        "research-log-retention/v1",
    )


def _fail(code: str, subject: str, observed: object, rule: str) -> NoReturn:
    raise RetentionContractError(code, subject, observed, rule)
