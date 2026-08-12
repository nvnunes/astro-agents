"""Canonical parsing contract for entry-local ``data.csv`` files."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

DATA_INDEX_FIELDS = ("name", "type", "location")
DATA_NAME_PATTERN = r"[A-Za-z0-9_.-]+"
DATA_NAME_RE = re.compile(rf"{DATA_NAME_PATTERN}\Z")
TOKEN_RE = re.compile(rf"<({DATA_NAME_PATTERN})>")
RESERVED_DATA_NAMES = frozenset({"log", "project", "theme"})


@dataclass(frozen=True)
class DataIndex:
    """Validated rows and token locations from one data index."""

    rows: list[dict[str, str]]
    locations: dict[str, str]


@dataclass(frozen=True)
class DataIndexReport:
    """Rows plus all reviewable contract errors discovered in one index."""

    rows: list[dict[str, str]]
    errors: list[str]
    duplicates: list[str]


class DataIndexError(ValueError):
    """Raised when a data index violates the shared contract."""


def validate_data_name(name: str) -> None:
    """Reject reserved or syntactically invalid token names."""

    if name in RESERVED_DATA_NAMES:
        raise DataIndexError(f"reserved data name {name!r}")
    if DATA_NAME_RE.fullmatch(name) is None:
        raise DataIndexError(
            f"invalid data name {name!r}; expected letters, digits, '.', '_', or '-'"
        )


def _normalized_row(
    raw: dict[str | None, str | list[str] | None], line: int
) -> dict[str, str] | None:
    if None in raw:
        raise DataIndexError(f"malformed data index row {line}")
    row = {field: str(raw.get(field) or "").strip() for field in DATA_INDEX_FIELDS}
    if not any(row.values()):
        return None
    if not all(row.values()):
        raise DataIndexError(f"malformed data index row {line}")
    try:
        validate_data_name(row["name"])
    except DataIndexError as exc:
        raise DataIndexError(f"{exc} on line {line}") from exc
    return row


def _read_rows(reader: csv.DictReader) -> DataIndexReport:
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    first_lines: dict[str, int] = {}
    duplicates: set[str] = set()
    for line_number, raw in enumerate(reader, start=2):
        try:
            row = _normalized_row(raw, line_number)
        except DataIndexError as exc:
            errors.append(str(exc))
            continue
        if row is None:
            continue
        rows.append(row)
        if row["name"] in first_lines:
            duplicates.add(row["name"])
            errors.append(
                f"duplicate data name {row['name']!r} on line {line_number}"
            )
        else:
            first_lines[row["name"]] = line_number
    return DataIndexReport(rows, errors, sorted(duplicates))


def inspect_data_index(path: Path) -> DataIndexReport:
    """Read one index and report contract errors without guessing repairs."""

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return DataIndexReport([], ["empty data index"], [])
            if tuple(reader.fieldnames) != DATA_INDEX_FIELDS:
                return DataIndexReport(
                    [],
                    [
                        "data index header must be exactly "
                        + ",".join(DATA_INDEX_FIELDS)
                    ],
                    [],
                )
            return _read_rows(reader)
    except UnicodeError as exc:
        raise DataIndexError("data index is not valid UTF-8") from exc
    except OSError as exc:
        raise DataIndexError(f"could not read data index: {exc}") from exc
    except csv.Error as exc:
        raise DataIndexError(f"malformed CSV data index: {exc}") from exc


def read_data_index(path: Path) -> DataIndex:
    """Read one valid data index or raise the first contract error."""

    report = inspect_data_index(path)
    if report.errors:
        raise DataIndexError(report.errors[0])
    return DataIndex(
        report.rows,
        {row["name"]: row["location"] for row in report.rows},
    )
