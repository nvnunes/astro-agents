"""One-time migration from maintained-summary snapshots to generated status."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .contracts import ValidationToolError
from .graph_store import SLICE_FILENAME, discover_repository_summaries
from .identities import (
    summary_validation_link,
    summary_validation_text_identity,
    text_content_identity,
)
from .records import publish_record_bundle, record_bundle_identity, repository_lock
from .report import install_status_summary, status_summary_fields

GENERATED_FILENAMES = (
    "validation.md",
    "validation-failures.md",
    "validation-state.json",
    SLICE_FILENAME,
)


class PublicationMigrationError(ValidationToolError):
    """Raised when the one-time publication migration cannot proceed safely."""


@dataclass(frozen=True)
class StagedMigration:
    """Fully staged report bundle and maintained-summary patch for one log."""

    summary: Path
    output_dir: Path
    staged_dir: Path
    summary_text: str
    expected_bundle_identity: str
    corrected_snapshot: bool


def _section_ranges(lines: Sequence[str], heading: str) -> list[tuple[int, int]]:
    starts = [index for index, line in enumerate(lines) if line == heading]
    return [
        (
            start,
            next(
                (
                    index
                    for index in range(start + 1, len(lines))
                    if lines[index].startswith("## ")
                ),
                len(lines),
            ),
        )
        for start in starts
    ]


def _validated_summary_shape(
    lines: Sequence[str], link: str
) -> tuple[int, Optional[tuple[int, int]]]:
    if not lines or not lines[0].startswith("# ") or lines[0].startswith("## "):
        raise PublicationMigrationError("maintained summary must start with one H1")
    link_count = lines.count(link)
    legacy = _section_ranges(lines, "## Validation")
    if link_count > 1:
        raise PublicationMigrationError("maintained summary has duplicate report links")
    if len(legacy) > 1:
        raise PublicationMigrationError(
            "maintained summary has duplicate Validation sections"
        )
    if link_count == 1 and legacy:
        raise PublicationMigrationError(
            "maintained summary mixes the fixed link and legacy snapshot"
        )
    if link_count == 0 and not legacy:
        raise PublicationMigrationError(
            "maintained summary has neither a legacy snapshot nor fixed report link"
        )
    return link_count, legacy[0] if legacy else None


def _remove_legacy_section(
    lines: list[str], legacy: Optional[tuple[int, int]]
) -> None:
    if legacy is not None:
        start, end = legacy
        del lines[start:end]
        while start < len(lines) and not lines[start]:
            del lines[start]


def _remove_contents_link(lines: list[str]) -> None:
    contents = _section_ranges(lines, "## Contents")
    if len(contents) > 1:
        raise PublicationMigrationError(
            "maintained summary has duplicate Contents sections"
        )
    if contents:
        start, end = contents[0]
        contents_link = "- [Validation](#validation)"
        count = lines[start + 1 : end].count(contents_link)
        if count > 1:
            raise PublicationMigrationError(
                "maintained summary has duplicate Validation contents links"
            )
        if count:
            del lines[lines.index(contents_link, start + 1, end)]


def _install_fixed_link(lines: list[str], link: str, link_count: int) -> None:
    if link_count == 0:
        if len(lines) > 1 and lines[1] == "":
            lines[1:2] = [link, ""]
        else:
            lines[1:1] = [link, ""]
    elif lines[1:3] != [link, ""]:
        raise PublicationMigrationError(
            "fixed validation report link is not immediately below the H1"
        )


def migrate_summary_text(summary_path: Path, text: str) -> str:
    """Replace one legacy snapshot with the fixed report-navigation link."""

    lines = text.rstrip().splitlines()
    link = summary_validation_link(summary_path)
    link_count, legacy = _validated_summary_shape(lines, link)
    _remove_legacy_section(lines, legacy)
    _remove_contents_link(lines)
    _install_fixed_link(lines, link, link_count)
    migrated = "\n".join(lines).rstrip() + "\n"
    before = summary_validation_text_identity(summary_path, text)
    after = summary_validation_text_identity(summary_path, migrated)
    if before != after:
        raise PublicationMigrationError(
            "summary migration changed validation-relevant research content"
        )
    return migrated


def _legacy_snapshot_fields(text: str) -> Optional[dict[str, Any]]:
    lines = text.splitlines()
    ranges = _section_ranges(lines, "## Validation")
    if len(ranges) != 1:
        return None
    start, end = ranges[0]
    block = lines[start:end]
    date = next(
        (
            match.group(1)
            for line in block
            if (match := re.fullmatch(r"Last validated on: (.+)", line))
        ),
        None,
    )
    summary = next(
        (
            match.group(1)
            for line in block
            if (match := re.fullmatch(r"Summary statistics: (.+)", line))
        ),
        None,
    )
    entries = []
    for line in block:
        if not line.startswith("|") or line.startswith(("| ---", "| Scope")):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) == 4:
            entries.append(tuple(cells))
    return {"date": date, "summary": summary, "entries": entries}


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationMigrationError(
            f"could not read validation state: {path}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("report"), dict):
        raise PublicationMigrationError(
            f"validation state lacks report identity: {path}"
        )
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _stage_one(
    summary: Path,
    staging_root: Path,
    lint_bundle: Callable[[Path, Optional[list[str]]], Mapping[str, Any]],
) -> StagedMigration:
    if summary.is_symlink() or not summary.is_file():
        raise PublicationMigrationError(f"summary is not a regular file: {summary}")
    output_dir = summary.with_suffix("")
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise PublicationMigrationError(
            f"validation output is not a regular directory: {output_dir}"
        )
    for name in ("validation.md", "validation-state.json", SLICE_FILENAME):
        if not (output_dir / name).is_file() or (output_dir / name).is_symlink():
            raise PublicationMigrationError(
                f"canonical bundle file is invalid: {output_dir / name}"
            )

    staged_dir = staging_root / output_dir.name
    staged_dir.mkdir(parents=True)
    for name in GENERATED_FILENAMES:
        source = output_dir / name
        if source.exists():
            if not source.is_file() or source.is_symlink():
                raise PublicationMigrationError(f"generated record is unsafe: {source}")
            (staged_dir / name).write_bytes(source.read_bytes())

    report_path = staged_dir / "validation.md"
    report_text = install_status_summary(report_path.read_text(encoding="utf-8"))
    report_path.write_text(report_text, encoding="utf-8")
    state_path = staged_dir / "validation-state.json"
    state = _read_state(state_path)
    state["report"] = text_content_identity(report_text)
    _write_json(state_path, state)
    lint = lint_bundle(staged_dir, None)
    if not lint.get("ok"):
        raise PublicationMigrationError(
            "staged generated bundle failed lint: " + "; ".join(lint.get("issues", []))
        )
    summary_source = summary.read_text(encoding="utf-8")
    legacy = _legacy_snapshot_fields(summary_source)
    current = status_summary_fields(report_text)
    return StagedMigration(
        summary=summary,
        output_dir=output_dir,
        staged_dir=staged_dir,
        summary_text=migrate_summary_text(summary, summary_source),
        expected_bundle_identity=record_bundle_identity(
            output_dir, GENERATED_FILENAMES
        ),
        corrected_snapshot=legacy is not None and legacy != current,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    if path.read_bytes() == payload:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_repository(
    project_root: Path,
    manifest: Sequence[str],
    lint_bundle: Callable[[Path, Optional[list[str]]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Stage, verify, and publish one exact maintained-log migration set."""

    project_root = project_root.resolve()
    expected = sorted(manifest)
    if not expected or len(expected) != len(set(expected)):
        raise PublicationMigrationError(
            "migration manifest must be nonempty and unique"
        )
    discovered = sorted(
        summary.resolve().relative_to(project_root).as_posix()
        for summary in discover_repository_summaries(project_root)
    )
    if discovered != expected:
        raise PublicationMigrationError(
            "migration manifest differs from maintained summaries: "
            f"expected={expected!r}; "
            f"discovered={discovered!r}"
        )
    summaries = [(project_root / identity).resolve() for identity in expected]
    if any(not summary.is_relative_to(project_root) for summary in summaries):
        raise PublicationMigrationError("migration summary escapes the project root")

    with tempfile.TemporaryDirectory(
        prefix="research-log-publication-migration-"
    ) as raw:
        staging_root = Path(raw)
        staged = [
            _stage_one(summary, staging_root / str(index), lint_bundle)
            for index, summary in enumerate(summaries)
        ]
        with repository_lock(project_root):
            for item in staged:
                publish_record_bundle(
                    item.staged_dir,
                    item.output_dir,
                    GENERATED_FILENAMES,
                    expected_identity=item.expected_bundle_identity,
                )
            for item in staged:
                _atomic_write_text(item.summary, item.summary_text)
    return {
        "summaries": expected,
        "count": len(expected),
        "corrected_snapshots": [
            item.summary.relative_to(project_root).as_posix()
            for item in staged
            if item.corrected_snapshot
        ],
    }
