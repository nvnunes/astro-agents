"""Summary parsing and validation projection for maintained research logs."""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from validation.contracts import FileChangedError, ValidationToolError
from validation.identities import summary_validation_identity
from validation.inventory import display_path, find_project_root
from validation.state import (
    ValidationStateContractError,
    decode_validation_state,
)


class SummaryPublicationError(RuntimeError):
    """Raised when maintained-summary input or publication is invalid."""


@dataclass(frozen=True)
class ValidationProjectionPolicy:
    """Current persisted-state contract for summary validation projection."""

    state_schema_version: int
    rules_version: str


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_text(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _table_cells(line: str) -> list[str]:
    return [
        cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line[1:-1])
    ]


def parse_markdown_rows(text: str) -> dict[str, Any]:
    """Parse generated validation-table text used by summary projection."""

    lines = text.splitlines()
    mode: Optional[str] = None
    current_entry: Optional[str] = None
    summary_rows: list[list[str]] = []
    entry_rows: list[list[str]] = []
    entry_order: list[str] = []
    entry_groups: dict[str, list[list[str]]] = {}
    for line in lines:
        if line == "## Summary":
            mode = "summary"
            continue
        if line == "## Entries":
            mode = "entry"
            continue
        if mode == "entry" and line.startswith("### "):
            current_entry = line[4:].split(":", 1)[0]
            entry_order.append(current_entry)
            entry_groups[current_entry] = []
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        if line.startswith(("| Statistic", "| Target", "| Scope")):
            continue
        cells = _table_cells(line)
        if mode == "summary":
            summary_rows.append(cells)
        elif mode == "entry":
            entry_rows.append(cells)
            if current_entry is not None:
                entry_groups[current_entry].append(cells)
    return {
        "summary": summary_rows,
        "entries": entry_rows,
        "entry_order": entry_order,
        "entry_groups": entry_groups,
    }


def markdown_rows(path: Path) -> dict[str, Any]:
    """Read and parse generated validation tables used by summary projection."""

    return parse_markdown_rows(path.read_text(encoding="utf-8"))


def _is_validation_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None


def report_update_date(report_text: str) -> str:
    """Return the validated update date declared by a report."""

    match = re.search(
        r"^- Report-update date: `(\d{4}-\d{2}-\d{2})`$",
        report_text,
        re.MULTILINE,
    )
    if not match or not _is_validation_date(match.group(1)):
        raise SummaryPublicationError("validation report lacks a valid update date")
    return match.group(1)


def _counted(count: int, singular: str, plural: Optional[str] = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def validation_projection(report_path: Path, summary_path: Path) -> str:
    """Build the maintained-summary projection from a generated report."""

    if not report_path.is_file():
        raise SummaryPublicationError(
            f"validation report does not exist: {report_path}"
        )
    report_text = report_path.read_text(encoding="utf-8")
    parsed = markdown_rows(report_path)
    date = report_update_date(report_text)
    summary_rows = parsed["summary"]
    summary_failures = sum(len(row) == 4 and row[3] == "`FAIL`" for row in summary_rows)
    if not summary_rows:
        summary_status = "`N/A`"
    elif summary_failures:
        summary_status = (
            f"`FAIL` - {summary_failures} of "
            f"{_counted(len(summary_rows), 'statistic')} failed"
        )
    else:
        summary_status = f"{date} — {len(summary_rows)} checked; 0 failures"

    relative_report = Path(os.path.relpath(report_path, summary_path.parent)).as_posix()
    lines = [
        "## Validation",
        "",
        f"[Detailed validation report]({relative_report})",
        "",
        f"Last validated on: {date}",
        "",
        f"Summary statistics: {summary_status}",
        "",
        "| Scope | Last checked | Integrity & Provenance | Reproducibility |",
        "| --- | --- | --- | --- |",
    ]
    for entry_id in parsed["entry_order"]:
        rows = parsed["entry_groups"][entry_id]
        failures = sum(len(row) == 6 and "`FAIL`" in row[2:4] for row in rows)
        if not rows:
            standard_status = "`N/A`"
        elif failures:
            standard_status = (
                f"`FAIL` - {failures} of {_counted(len(rows), 'target')} failed"
            )
        else:
            standard_status = f"{_counted(len(rows), 'target')} checked; 0 failures"

        reproduction = [row[4] for row in rows if len(row) == 6 and row[4] != "`N/A`"]
        reproduction_failures = reproduction.count("`FAIL`")
        reproduced = sum(_is_validation_date(value) for value in reproduction)
        if not reproduction:
            reproduction_status = "`N/A`"
        elif reproduction_failures:
            reproduction_status = (
                f"`FAIL` - {reproduction_failures} of "
                f"{_counted(len(reproduction), 'eligible target')} failed"
            )
        elif not reproduced:
            reproduction_status = "`-`"
        else:
            reproduction_status = (
                f"{reproduced} of "
                f"{_counted(len(reproduction), 'eligible target')} reproduced"
            )
        lines.append(
            f"| {entry_id} | {date} | {standard_status} | {reproduction_status} |"
        )
    return "\n".join(lines) + "\n"


def _validated_summary_source(
    summary_path: Path, expected_identity: Mapping[str, Any]
) -> tuple[bytes, list[str]]:
    if not summary_path.is_file():
        raise ValidationToolError(f"summary does not exist: {summary_path}")
    if summary_validation_identity(summary_path) != expected_identity:
        raise FileChangedError("maintained summary changed after canonical validation")
    original = summary_path.read_bytes()
    try:
        return original, original.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationToolError(
            f"maintained summary is not valid UTF-8: {summary_path}"
        ) from exc


def _publish_validation_projection(
    summary_path: Path,
    output_dir: Path,
    expected_summary_identity: Mapping[str, Any],
) -> dict[str, Any]:
    summary_path = summary_path.resolve()
    report_path = (output_dir / "validation.md").resolve()
    expected_report = (summary_path.with_suffix("") / "validation.md").resolve()
    if report_path != expected_report:
        raise ValidationToolError(
            "summary projection requires the canonical validation report directory"
        )
    _original, lines = _validated_summary_source(
        summary_path, expected_summary_identity
    )
    projection = validation_projection(report_path, summary_path)

    validation_sections = [
        index for index, line in enumerate(lines) if line == "## Validation"
    ]
    if len(validation_sections) > 1:
        raise ValidationToolError("summary contains duplicate Validation sections")
    section_start = validation_sections[0] if validation_sections else None
    if section_start is not None:
        section_end = next(
            (
                index
                for index in range(section_start + 1, len(lines))
                if lines[index].startswith("## ")
            ),
            len(lines),
        )
        del lines[section_start:section_end]
        while section_start < len(lines) and lines[section_start] == "":
            del lines[section_start]

    insertion = next(
        (index for index, line in enumerate(lines) if line == "## AI Use"), len(lines)
    )
    projection_lines = projection.rstrip().splitlines()
    if insertion and lines[insertion - 1] != "":
        projection_lines.insert(0, "")
    projection_lines.append("")
    lines[insertion:insertion] = projection_lines

    contents_sections = [
        index for index, line in enumerate(lines) if line == "## Contents"
    ]
    if len(contents_sections) != 1:
        raise ValidationToolError(
            "maintained summary must contain exactly one Contents section"
        )
    contents = contents_sections[0]
    contents_link = "- [Validation](#validation)"
    contents_end = next(
        (
            index
            for index in range(contents + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    if contents_link not in lines[contents + 1 : contents_end]:
        link_insertion = next(
            (
                index
                for index in range(contents + 1, len(lines))
                if lines[index].strip().endswith("(#ai-use)")
            ),
            contents_end,
        )
        while link_insertion > contents and lines[link_insertion - 1] == "":
            link_insertion -= 1
        lines.insert(link_insertion, contents_link)

    _atomic_write_text(summary_path, "\n".join(lines).rstrip() + "\n")
    return {
        "summary": summary_path.as_posix(),
        "report": report_path.as_posix(),
        "entries": len(markdown_rows(report_path)["entry_order"]),
    }


def update_validation_projection(
    summary_path: Path,
    output_dir: Path,
    policy: ValidationProjectionPolicy,
) -> dict[str, Any]:
    """Project current canonical validation results into a maintained summary."""

    output_dir = output_dir.resolve()
    state_path = output_dir / "validation-state.json"
    if not state_path.is_file():
        raise ValidationToolError(f"validation state does not exist: {state_path}")
    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        state = decode_validation_state(
            raw_state,
            schema_version=policy.state_schema_version,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"could not read JSON {state_path}: {exc}") from exc
    except ValidationStateContractError as exc:
        raise ValidationToolError(
            f"validation state violates its contract: {exc}"
        ) from exc
    if state.get("validation_rules_version") != policy.rules_version:
        raise ValidationToolError(
            "summary projection requires current validation rules"
        )
    project_root = find_project_root(summary_path.resolve())
    summary_identity = display_path(summary_path.resolve(), project_root)
    expected = state["input_files"].get(summary_identity)
    if not isinstance(expected, Mapping):
        raise ValidationToolError(
            "validation state does not identify the maintained summary"
        )
    return _publish_validation_projection(summary_path, output_dir, expected)
