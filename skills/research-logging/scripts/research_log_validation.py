#!/usr/bin/env python3
"""Mechanical-first support for agent-led research-log validation.

The tool discovers presented evidence, checks deterministic contracts, reuses
unchanged completed outcomes, and prepares only unresolved cases for bounded
agent review. Standard validation never imports or executes research code.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import csv
import datetime
import hashlib
import json
import math
import os
import re
import shlex
import struct
import sys
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCAN_SCHEMA_VERSION = 10
ADJUDICATION_SCHEMA_VERSION = 4
STATE_SCHEMA_VERSION = 4
DECISION_SCHEMA_VERSION = 1
REPOSITORY_INDEX_SCHEMA_VERSION = 1
RULES_VERSION = "research-log-validation-v12"
ORPHAN_INVENTORY_VERSION = 5
REPOSITORY_INDEX_FILENAME = ".research-log-validation-index.json"
STATE_KEYS = {
    "schema_version",
    "validation_rules_version",
    "input_fingerprint",
    "input_files",
    "mechanical_checks",
    "directory_memberships",
    "files",
    "completed_checks",
    "orphan_dispositions",
    "result",
    "report",
}
CHUNK_SIZE = 1024 * 1024
LOCATOR_VALUE_LIMIT = 10_000
LOCATOR_CONTEXT_LIMIT = 8 * 1024

ENTRY_EVIDENCE_HEADER = (
    "entry",
    "section",
    "kind",
    "evidence",
    "sources",
    "transformation",
)
SUMMARY_EVIDENCE_HEADER = ("statistic", "entry", "section", "transformation")
EVIDENCE_KINDS = {"statistic", "table", "output"}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?P<image>!)?\[(?P<label>[^\]]*)\]\((?P<target>[^)]+)\)")
FENCE_RE = re.compile(r"^\s*(```+|~~~+)\s*([^\s`]*)")
ENTRY_ID_RE = re.compile(r"^e\d+[a-z]?$", re.IGNORECASE)
CITATION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_:.+/-]+)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)" r"(?:[eE][-+]?\d+)?%?"
)
TOKEN_RE = re.compile(r"<([A-Za-z0-9_.-]+)>")
PATH_SUFFIXES = {
    ".csv",
    ".ecsv",
    ".fits",
    ".fit",
    ".h5",
    ".hdf5",
    ".ipynb",
    ".jl",
    ".json",
    ".jpeg",
    ".jpg",
    ".log",
    ".m",
    ".md",
    ".npy",
    ".npz",
    ".parquet",
    ".pdf",
    ".pkl",
    ".png",
    ".py",
    ".r",
    ".sh",
    ".svg",
    ".txt",
    ".yaml",
    ".yml",
}
SCRIPT_SUFFIXES = {".ipynb", ".jl", ".m", ".py", ".r", ".sh"}
IGNORED_SCRIPT_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
EXTERNAL_SCHEMES = {"http", "https", "s3", "gs", "doi", "ftp"}
SUCCESS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RESULT_VALUES = {"FAIL", "-", "N/A"}
ORPHAN_TARGET = "Orphaned artifacts, scripts, and references"
DECISION_DEPENDENCY_KEYS = {
    "members",
    "add_dependencies",
    "remove_dependencies",
    "copy_dependencies_from",
}
REPRODUCTION_DECISIONS = {
    "reproduced",
    "reproduction-fail",
    "not-run",
    "not-applicable",
}
DECISION_FIELDS_BY_OUTCOME = {
    "support": {"match", "decision", "candidate"},
    "pass": {"match", "decision", "notes", *DECISION_DEPENDENCY_KEYS},
    "fail": {
        "match",
        "decision",
        "findings",
        "failure_basis",
        "notes",
        *DECISION_DEPENDENCY_KEYS,
    },
    "keep": {"match", "decision", "notes", *DECISION_DEPENDENCY_KEYS},
    "scope": {"match", "decision", "notes", *DECISION_DEPENDENCY_KEYS},
    "drop": {"match", "decision"},
    "orphan": {"match", "decision", "unresolved"},
    "reproduced": {"match", "decision", "notes", *DECISION_DEPENDENCY_KEYS},
    "reproduction-fail": {
        "match",
        "decision",
        "findings",
        "notes",
        *DECISION_DEPENDENCY_KEYS,
    },
    "not-run": {"match", "decision", "notes"},
    "not-applicable": {"match", "decision", "notes"},
}
BLOCK_LABEL_RE = re.compile(r"^\s*`([A-Za-z][A-Za-z -]*):`\s*$")
EXPERIMENTAL_LABELS = {
    "Background",
    "Steps",
    "Results",
    "Observations",
    "Decisions",
    "Uncertainty",
    "Validation",
    "Follow-up",
}
SYNTHESIS_LABELS = {
    "Background",
    "Findings",
    "Decisions",
    "Uncertainty",
    "Follow-up",
}


class ValidationToolError(RuntimeError):
    """Raised when a validation-tool input violates its public contract."""


class FileChangedError(ValidationToolError):
    """Raised when a file changes while its identity is being computed."""


# File access, path identity, and incremental fingerprints


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(str(temporary), str(path))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(str(temporary), str(path))


def find_project_root(path: Path) -> Path:
    """Return the nearest ancestor containing ``.git``.

    The lookup is read-only and does not inspect repository status.
    """

    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ValidationToolError(f"could not locate project root from {path}")


def display_path(path: Path, project_root: Path) -> str:
    """Return a project-relative path when possible, otherwise an absolute path."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sha256_file(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
    return digest.hexdigest(), count


def file_identity(path: Path) -> Dict[str, Any]:
    """Return a stable size, modification-time, and SHA-256 identity.

    Files use a content hash. Symlinks cover both the link and its resolved
    target. Collections require an explicit member scope and are handled by
    :func:`collection_identity`. Concurrent changes are rejected.
    """

    path = Path(os.path.abspath(str(path.expanduser())))
    before = path.lstat()
    if path.is_symlink():
        target = os.readlink(str(path))
        resolved = path.resolve(strict=True)
        target_identity = file_identity(resolved)
        digest = hashlib.sha256(
            (
                f"{target}\0{target_identity['size']}\0{target_identity['mtime_ns']}\0"
                f"{target_identity['sha256']}"
            ).encode("utf-8")
        ).hexdigest()
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise FileChangedError(f"symlink changed during identity check: {path}")
        return {
            "size": target_identity["size"],
            "mtime_ns": max(before.st_mtime_ns, target_identity["mtime_ns"]),
            "sha256": digest,
        }

    if path.is_file():
        digest, size = _sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise FileChangedError(f"file changed during identity check: {path}")
        return {"size": size, "mtime_ns": after.st_mtime_ns, "sha256": digest}

    raise ValidationToolError(f"file identity requires a file or symlink: {path}")


def _filtered_text_identity(path: Path, text: str) -> Dict[str, Any]:
    """Identify validation-relevant text while still detecting concurrent edits."""

    before = path.stat()
    payload = text.encode("utf-8")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FileChangedError(f"file changed during identity check: {path}")
    return {
        "size": len(payload),
        "mtime_ns": 0,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def summary_validation_identity(path: Path) -> Dict[str, Any]:
    """Identify summary content excluding generated validation and AI-use sections."""

    path = path.resolve()
    lines = _read_text(path).splitlines()
    retained = []
    excluded = False
    for line in lines:
        if line.startswith("## "):
            excluded = line in {"## Validation", "## AI Use"}
        if not excluded and line != "- [Validation](#validation)":
            retained.append(line)
    return _filtered_text_identity(path, "\n".join(retained) + "\n")


def entry_validation_identity(path: Path) -> Dict[str, Any]:
    """Identify only experimental and structurally invalid entry sections."""

    path = path.resolve()
    lines = _read_text(path).splitlines()
    sections = _section_ranges(lines)
    retained_sections = []
    for definition in _section_definitions(lines, sections):
        if definition["type"] not in {"experimental", "invalid"}:
            continue
        content = lines[definition["line"] - 1 : definition["end_line"]]
        while content and not content[-1].strip():
            content.pop()
        retained_sections.append("\n".join(content))
    return _filtered_text_identity(path, "\n\n".join(retained_sections) + "\n")


def _validation_file_identity(
    scan: Dict[str, Any], identity: str, path: Path
) -> Dict[str, Any]:
    """Apply the scope-aware identity contract for one validation dependency."""

    if identity == scan.get("summary"):
        return summary_validation_identity(path)
    entry_paths = {
        entry.get("path") for entry in scan.get("entries", []) if "error" not in entry
    }
    if identity in entry_paths:
        return entry_validation_identity(path)
    return file_identity(path)


def collection_identity(path: Path, members: Sequence[str]) -> Dict[str, Any]:
    """Identify only the explicitly adjudicated regular files in a directory."""

    path = Path(os.path.abspath(str(path.expanduser())))
    if not path.is_dir():
        raise ValidationToolError(f"collection dependency is not a directory: {path}")
    normalized = sorted(set(members))
    if not normalized:
        raise ValidationToolError(
            f"collection dependency has no selected members: {path}"
        )

    digest = hashlib.sha256()
    total_size = 0
    latest_mtime = 0
    for raw in normalized:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationToolError(f"collection member escapes its directory: {raw}")
        member = path / relative
        if not member.is_file():
            raise ValidationToolError(
                f"collection member is not a regular file: {member}"
            )
        identity = file_identity(member)
        total_size += identity["size"]
        latest_mtime = max(latest_mtime, identity["mtime_ns"])
        digest.update(
            (
                f"{relative.as_posix()}\0{identity['size']}\0{identity['mtime_ns']}\0"
                f"{identity['sha256']}\n"
            ).encode("utf-8")
        )
    return {
        "size": total_size,
        "mtime_ns": latest_mtime,
        "sha256": digest.hexdigest(),
        "members": [Path(item).as_posix() for item in normalized],
    }


def directory_membership_identity(
    path: Path, ignored_paths: Iterable[Path] = ()
) -> Dict[str, Any]:
    """Return a direct path-and-type membership fingerprint for one directory.

    Membership identities detect additions, removals, and renames without
    recursively indexing unrelated descendants or hashing unselected file
    contents. Selected collection members remain normal file dependencies and
    receive content identities separately.
    """

    path = Path(os.path.abspath(str(path.expanduser())))
    if not path.is_dir():
        raise ValidationToolError(f"directory membership requires a directory: {path}")
    ignored = {item.resolve() for item in ignored_paths}
    members = []
    for member in sorted(path.iterdir()):
        if member.resolve() in ignored:
            continue
        if member.is_symlink():
            kind = "symlink"
        elif member.is_dir():
            kind = "directory"
        else:
            kind = "file"
        members.append(f"{kind}\0{member.name}")
    payload = "\n".join(sorted(members)).encode("utf-8")
    return {
        "members": len(members),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _content_identity(path: Path) -> Dict[str, Any]:
    digest, size = _sha256_file(path)
    return {"size": size, "sha256": digest}


def _text_content_identity(text: str) -> Dict[str, Any]:
    encoded = text.encode("utf-8")
    return {"size": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


# Markdown, evidence-record, and command discovery


def _strip_link_target(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")]
    if " " in raw:
        return raw.split(" ", 1)[0]
    return raw


def resolve_reference(raw: str, source: Path) -> Dict[str, Any]:
    """Resolve one Markdown or inline path reference without accessing a network."""

    target = urllib.parse.unquote(_strip_link_target(raw))
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme.lower() in EXTERNAL_SCHEMES:
        return {"target": target, "kind": "external", "exists": None}
    if target.startswith("#"):
        return {"target": target, "kind": "anchor", "exists": True}
    if TOKEN_RE.search(target):
        return {"target": target, "kind": "token", "exists": None}

    path_text = target.split("#", 1)[0]
    if not path_text:
        return {"target": target, "kind": "anchor", "exists": True}
    path = Path(path_text)
    if not path.is_absolute():
        path = source.parent / path
    path = path.resolve()
    if path.is_file():
        kind = "file"
    elif path.is_dir():
        kind = "directory"
    else:
        kind = "missing"
    return {
        "target": target,
        "path": path.as_posix(),
        "kind": kind,
        "exists": path.exists(),
    }


def _title(lines: Sequence[str], fallback: str) -> str:
    for line in lines:
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return fallback


def _section_ranges(lines: Sequence[str]) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    title = _title(lines, "Document")
    definitions: List[Dict[str, Any]] = [
        {"line": 1, "section": title, "labels": [], "label_lines": []}
    ]
    current = 0
    active_label: Optional[str] = None
    in_fence = False
    fence_marker: Optional[str] = None
    for number, line in enumerate(lines, 1):
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = None

        heading = None if in_fence else HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            definitions[current]["end_line"] = number - 1
            definitions.append(
                {
                    "line": number,
                    "section": heading.group(2).strip(),
                    "labels": [],
                    "label_lines": [],
                }
            )
            current += 1
            active_label = None
        if not in_fence:
            label = BLOCK_LABEL_RE.match(line)
            if label:
                definitions[current]["labels"].append(label.group(1))
                definitions[current]["label_lines"].append(number)
                active_label = label.group(1)
        sections.append(
            {
                "line": number,
                "section": definitions[current]["section"],
                "section_index": current,
                "block_label": active_label,
            }
        )
    definitions[current]["end_line"] = len(lines)

    for definition in definitions:
        labels = definition["labels"]
        unique = set(labels)
        errors: List[str] = []
        duplicates = sorted(label for label in unique if labels.count(label) > 1)
        if duplicates:
            errors.append(f"duplicate labels: {', '.join(duplicates)}")
        if "Findings" in unique:
            unsupported = sorted(unique - SYNTHESIS_LABELS)
            if unsupported:
                errors.append(
                    "synthesis section has incompatible labels: "
                    + ", ".join(unsupported)
                )
            section_type = "synthesis" if not errors else "invalid"
        elif unique & {"Steps", "Results"}:
            missing = sorted({"Steps", "Results"} - unique)
            if missing:
                errors.append("experimental section is missing: " + ", ".join(missing))
            unsupported = sorted(unique - EXPERIMENTAL_LABELS)
            if unsupported:
                errors.append(
                    "experimental section has incompatible labels: "
                    + ", ".join(unsupported)
                )
            section_type = "experimental" if not errors else "invalid"
        elif not unique:
            section_type = "prose"
        else:
            section_type = "invalid"
            errors.append(
                "labeled section is neither experimental nor synthesis: "
                + ", ".join(sorted(unique))
            )
        definition["type"] = section_type
        definition["errors"] = errors

    for item in sections:
        definition = definitions[item["section_index"]]
        item["section_type"] = definition["type"]
        item["section_errors"] = definition["errors"]
    return sections


def _section_definitions(
    lines: Sequence[str], sections: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return one classified record for each section occurrence."""

    definitions: List[Dict[str, Any]] = []
    seen = set()
    for item in sections:
        index = item["section_index"]
        if index in seen:
            continue
        seen.add(index)
        definitions.append(
            {
                "index": index,
                "line": item["line"],
                "section": item["section"],
                "type": item["section_type"],
                "errors": list(item["section_errors"]),
            }
        )
    for position, definition in enumerate(definitions):
        definition["end_line"] = (
            definitions[position + 1]["line"] - 1
            if position + 1 < len(definitions)
            else len(lines)
        )
    return definitions


def _table_delimiter(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _presented_numeric_expression(value: str) -> bool:
    """Return whether one inline-code span is a marked numerical statistic."""

    value = value.strip()
    if not NUMBER_RE.search(value):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    if value.startswith("--"):
        return False
    if re.match(r"^\d+\s*PH(?:\b|$)", value, re.IGNORECASE):
        return False
    if re.search(r"[A-Za-z_][A-Za-z0-9_.-]*\s*=", value):
        return False
    if re.fullmatch(r"[0-9a-fA-F]{12,}", value):
        return False
    suffix = Path(value.split("#", 1)[0]).suffix.lower()
    if not any(character.isspace() for character in value) and (
        suffix in PATH_SUFFIXES or "/" in value or "\\" in value
    ):
        return False
    return bool(re.match(r"^[\s~≈<>≤≥([{]*[-+]?(?:\d|\.\d)", value))


def _assign_presented_selectors(items: List[Dict[str, Any]], *, summary: bool) -> None:
    """Assign stable selectors, adding occurrence suffixes only for collisions."""

    keys = [
        (
            item["base_selector"]
            if summary
            else (item["section"], item["kind"], item["base_selector"])
        )
        for item in items
    ]
    counts = {key: keys.count(key) for key in set(keys)}
    seen: Dict[Any, int] = {}
    for item, key in zip(items, keys):
        seen[key] = seen.get(key, 0) + 1
        selector = item["base_selector"]
        if counts[key] > 1:
            selector = f"{selector} [occurrence {seen[key]}]"
        item["selector"] = selector


def _table_selector(markdown: str) -> str:
    header = markdown.splitlines()[0]
    return ",".join(cell.strip() for cell in header.strip().strip("|").split("|"))


def _table_blocks(
    lines: Sequence[str],
    sections: Sequence[Dict[str, Any]],
    excluded_lines: Iterable[int],
) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    excluded = set(excluded_lines)
    index = 0
    while index + 1 < len(lines):
        if (
            index + 1 in excluded
            or index + 2 in excluded
            or "|" not in lines[index]
            or not _table_delimiter(lines[index + 1])
        ):
            index += 1
            continue
        start = index
        block: List[str] = [lines[index], lines[index + 1]]
        index += 2
        while index < len(lines) and index + 1 not in excluded and "|" in lines[index]:
            block.append(lines[index])
            index += 1
        tables.append(
            {
                "identity": f"table:L{start + 1}-L{index}",
                "line": start + 1,
                "section": sections[start]["section"],
                "section_type": sections[start]["section_type"],
                "block_label": sections[start]["block_label"],
                "rows": max(0, len(block) - 2),
                "markdown": "\n".join(block),
            }
        )
    return tables


def _fenced_blocks(
    lines: Sequence[str], sections: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = FENCE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        marker = match.group(1)[0]
        language = match.group(2).lower()
        index += 1
        content: List[str] = []
        while index < len(lines) and not lines[index].lstrip().startswith(marker * 3):
            content.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        command_like = language in {"bash", "console", "sh", "shell", "zsh"}
        blocks.append(
            {
                "identity": f"fence:L{start + 1}-L{index}",
                "line": start + 1,
                "section": sections[start]["section"],
                "section_type": sections[start]["section_type"],
                "block_label": sections[start]["block_label"],
                "language": language,
                "kind": "command" if command_like else "output_or_code",
                "text": "\n".join(content),
            }
        )
    return blocks


def _summary_items(lines: Sequence[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    in_summary = False
    heading_path: Dict[int, str] = {}
    current: Optional[Dict[str, Any]] = None
    for number, line in enumerate(lines, 1):
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            if level == 2:
                if text == "Summary":
                    in_summary = True
                    heading_path = {2: text}
                    continue
                if in_summary:
                    break
            if in_summary:
                heading_path[level] = text
                for key in list(heading_path):
                    if key > level:
                        del heading_path[key]
            continue
        if not in_summary:
            continue
        bullet = re.match(r"^\s*-\s+(.+)$", line)
        if bullet:
            if current:
                current["text"] = " ".join(current.pop("parts"))
                items.append(current)
            current = {
                "identity": f"summary:L{number}",
                "line": number,
                "summary_section": " > ".join(
                    heading_path[key] for key in sorted(heading_path) if key >= 3
                ),
                "parts": [bullet.group(1).strip()],
            }
        elif current and line.strip():
            current["parts"].append(line.strip())
        elif current:
            current["text"] = " ".join(current.pop("parts"))
            items.append(current)
            current = None
    if current:
        current["text"] = " ".join(current.pop("parts"))
        items.append(current)
    for item in items:
        item["entry_links"] = sorted(
            {
                Path(_strip_link_target(match.group("target"))).stem
                for match in LINK_RE.finditer(item["text"])
                if ENTRY_ID_RE.match(
                    Path(_strip_link_target(match.group("target"))).stem
                )
            }
        )
    return items


def _validation_notes(
    lines: Sequence[str], sections: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return bounded text from entry ``Validation:`` blocks."""

    notes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for number, (line, section) in enumerate(zip(lines, sections), 1):
        if section.get("block_label") != "Validation":
            if current is not None:
                current["text"] = " ".join(current.pop("parts"))
                current["sha256"] = hashlib.sha256(
                    current["text"].encode("utf-8")
                ).hexdigest()
                notes.append(current)
                current = None
            continue
        if BLOCK_LABEL_RE.match(line):
            continue
        text = line.strip()
        if not text:
            continue
        if current is None:
            current = {
                "section": section["section"],
                "line": number,
                "parts": [],
            }
        current["parts"].append(text)
    if current is not None:
        current["text"] = " ".join(current.pop("parts"))
        current["sha256"] = hashlib.sha256(
            current["text"].encode("utf-8")
        ).hexdigest()
        notes.append(current)
    return notes


def parse_markdown(path: Path) -> Dict[str, Any]:
    """Extract deterministic locations and validation candidates from Markdown."""

    text = _read_text(path)
    lines = text.splitlines()
    sections = _section_ranges(lines)
    fenced = _fenced_blocks(lines, sections)
    fenced_lines = set()
    for block in fenced:
        match = re.match(r"fence:L(\d+)-L(\d+)", block["identity"])
        if match:
            fenced_lines.update(range(int(match.group(1)), int(match.group(2)) + 1))
    tables = _table_blocks(lines, sections, fenced_lines)
    table_lines = set()
    for table in tables:
        match = re.match(r"table:L(\d+)-L(\d+)", table["identity"])
        if match:
            table_lines.update(range(int(match.group(1)), int(match.group(2)) + 1))

    links: List[Dict[str, Any]] = []
    inline_paths: List[Dict[str, Any]] = []
    numeric: List[Dict[str, Any]] = []
    presented_statistics: List[Dict[str, Any]] = []
    summary_statistics: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    current_h2 = ""
    for number, line in enumerate(lines, 1):
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            current_h2 = heading.group(2).strip()
        if number in fenced_lines:
            continue
        section = sections[number - 1]["section"]
        for match in LINK_RE.finditer(line):
            resolved = resolve_reference(match.group("target"), path)
            links.append(
                {
                    "line": number,
                    "section": section,
                    "section_type": sections[number - 1]["section_type"],
                    "block_label": sections[number - 1]["block_label"],
                    "label": match.group("label"),
                    "image": bool(match.group("image")),
                    **resolved,
                }
            )
        for match in INLINE_CODE_RE.finditer(line):
            value = match.group(1).strip()
            candidate = value.split("=", 1)[-1]
            suffix = Path(candidate.split("#", 1)[0]).suffix.lower()
            if suffix not in PATH_SUFFIXES and "/" not in candidate:
                continue
            if candidate.startswith("--") or any(char.isspace() for char in candidate):
                continue
            inline_paths.append(
                {
                    "line": number,
                    "section": section,
                    "section_type": sections[number - 1]["section_type"],
                    "block_label": sections[number - 1]["block_label"],
                    **resolve_reference(candidate, path),
                }
            )
        for match in CITATION_RE.finditer(line):
            citations.append(
                {
                    "line": number,
                    "section": section,
                    "section_type": sections[number - 1]["section_type"],
                    "key": match.group(1).rstrip(".,;:)"),
                }
            )
        values = [
            number
            for match in INLINE_CODE_RE.finditer(line)
            if _presented_numeric_expression(match.group(1))
            for number in NUMBER_RE.findall(match.group(1))
        ]
        if values and not HEADING_RE.match(line) and number not in table_lines:
            numeric.append(
                {
                    "line": number,
                    "section": section,
                    "section_type": sections[number - 1]["section_type"],
                    "text": line.strip(),
                    "values": values,
                }
            )
        if not HEADING_RE.match(line) and number not in table_lines:
            for match in INLINE_CODE_RE.finditer(line):
                value = match.group(1).strip()
                if not _presented_numeric_expression(value):
                    continue
                item = {
                    "kind": "statistic",
                    "section": section,
                    "base_selector": value,
                    "line": number,
                    "context": line.strip(),
                }
                if sections[number - 1]["section_type"] == "experimental":
                    presented_statistics.append(dict(item))
                if current_h2 not in {"Entries", "Validation", "AI Use"}:
                    summary_statistics.append(dict(item))

    presented_items = [
        *presented_statistics,
        *(
            {
                "kind": "table",
                "section": table["section"],
                "base_selector": _table_selector(table["markdown"]),
                "line": table["line"],
                "end_line": table["line"] + len(table["markdown"].splitlines()) - 1,
                "context": table["markdown"],
            }
            for table in tables
            if table["section_type"] == "experimental"
            and table["block_label"] == "Results"
        ),
        *(
            {
                "kind": "output",
                "section": block["section"],
                "base_selector": next(
                    (
                        line.strip()
                        for line in block["text"].splitlines()
                        if line.strip()
                    ),
                    "",
                ),
                "line": block["line"],
                "end_line": block["line"] + len(block["text"].splitlines()) + 1,
                "context": block["text"],
            }
            for block in fenced
            if block["section_type"] == "experimental"
            and block["block_label"] == "Results"
            and block["language"] == "text"
        ),
    ]
    presented_items = [item for item in presented_items if item["base_selector"]]
    presented_items.sort(key=lambda item: (item["line"], item["kind"]))
    _assign_presented_selectors(presented_items, summary=False)
    summary_statistics.sort(key=lambda item: item["line"])
    _assign_presented_selectors(summary_statistics, summary=True)
    for collection in (presented_items, summary_statistics):
        per_line: Dict[Tuple[str, int], int] = {}
        for item in collection:
            key = (item["kind"], item["line"])
            per_line[key] = per_line.get(key, 0) + 1
            item["identity"] = f"{item['kind']}:L{item['line']}:{per_line[key]}"
            item.setdefault("end_line", item["line"])
            item.setdefault("text", item["context"])

    return {
        "title": _title(lines, path.stem),
        "path": path.as_posix(),
        "headings": [
            {
                "line": number,
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
            }
            for number, line in enumerate(lines, 1)
            for match in [HEADING_RE.match(line)]
            if match
        ],
        "sections": _section_definitions(lines, sections),
        "links": links,
        "inline_paths": inline_paths,
        "tables": tables,
        "fenced_blocks": fenced,
        "numeric_evidence": numeric,
        "presented_items": presented_items,
        "summary_statistics": summary_statistics,
        "citations": citations,
        "summary_items": _summary_items(lines),
        "validation_notes": _validation_notes(lines, sections),
    }


def _bibtex_keys(path: Path) -> List[str]:
    if not path.is_file():
        return []
    return sorted(set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", _read_text(path))))


def _data_index(entry_path: Path) -> Dict[str, Any]:
    candidates = [entry_path.parent / "data.csv", entry_path.parent.parent / "data.csv"]
    index = next((candidate for candidate in candidates if candidate.is_file()), None)
    if index is None:
        return {"path": None, "rows": [], "errors": [], "duplicates": []}
    errors: List[str] = []
    rows: List[Dict[str, str]] = []
    names: List[str] = []
    try:
        with index.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"name", "type", "location"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                errors.append(f"missing columns: {', '.join(sorted(missing))}")
            else:
                for number, row in enumerate(reader, 2):
                    if any(not (row.get(key) or "").strip() for key in required):
                        errors.append(f"malformed row {number}")
                    rows.append(
                        {key: (row.get(key) or "") for key in reader.fieldnames or []}
                    )
                    names.append((row.get("name") or "").strip())
    except (OSError, csv.Error, UnicodeError) as exc:
        errors.append(str(exc))
    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    return {
        "path": index.resolve().as_posix(),
        "rows": rows,
        "errors": errors,
        "duplicates": duplicates,
    }


def _read_evidence_csv(path: Path, header: Sequence[str]) -> Dict[str, Any]:
    """Read one evidence-association record and enforce its CSV shape."""

    expected = path.resolve().as_posix()
    if not path.is_file():
        return {"path": None, "expected_path": expected, "rows": [], "errors": []}

    rows: List[Dict[str, str]] = []
    errors: List[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != tuple(header):
                errors.append("header must be exactly " + ",".join(header))
                return {
                    "path": expected,
                    "expected_path": expected,
                    "rows": [],
                    "errors": errors,
                }
            for row in reader:
                line = reader.line_num
                if None in row:
                    errors.append(f"line {line}: unexpected extra CSV fields")
                    continue
                normalized = {key: (row.get(key) or "").strip() for key in header}
                if not any(normalized.values()):
                    errors.append(f"line {line}: empty row")
                    continue
                normalized["line"] = str(line)
                rows.append(normalized)
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"could not read evidence.csv: {exc}")

    if not rows and not errors:
        errors.append("evidence.csv must not be header-only")
    return {
        "path": expected,
        "expected_path": expected,
        "rows": rows,
        "errors": errors,
    }


def _parse_evidence_sources(
    raw: str, line: str
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse entry evidence sources without resolving or validating their content."""

    errors: List[str] = []
    parsed: List[Dict[str, str]] = []
    for specification in (part.strip() for part in raw.split(" | ")):
        if not specification:
            errors.append(f"line {line}: empty source specification")
            continue
        parts = specification.split(" :: ", 1)
        source = parts[0].strip()
        locator = parts[1].strip() if len(parts) == 2 else ""
        if not source:
            errors.append(f"line {line}: source is empty")
            continue
        if len(parts) == 2 and not locator:
            errors.append(f"line {line}: source locator is empty")
        token = TOKEN_RE.fullmatch(source)
        parsed_url = urllib.parse.urlparse(source)
        source_path = Path(source)
        if source.startswith("<log>/"):
            pass
        elif token:
            if token.group(1) in {"log", "project"}:
                errors.append(
                    f"line {line}: reserved token <{token.group(1)}> requires a path"
                )
        elif source_path.is_absolute() or parsed_url.scheme:
            errors.append(
                f"line {line}: source must use an entry-relative path, "
                "<log>/ path, or <name> token"
            )
        elif TOKEN_RE.search(source):
            errors.append(f"line {line}: source token must be exact")
        elif ".." in source_path.parts:
            errors.append(f"line {line}: relative source must not traverse parents")
        parsed.append({"source": source, "locator": locator})
    return parsed, errors


def _entry_evidence_record(path: Path) -> Dict[str, Any]:
    """Read and structurally validate one entry-folder evidence record."""

    record = _read_evidence_csv(path, ENTRY_EVIDENCE_HEADER)
    seen = set()
    for row in record["rows"]:
        line = row["line"]
        for field in ("entry", "section", "kind", "evidence", "sources"):
            if not row[field]:
                record["errors"].append(f"line {line}: {field} is required")
        if row["entry"] and not ENTRY_ID_RE.fullmatch(row["entry"]):
            record["errors"].append(f"line {line}: invalid entry ID {row['entry']!r}")
        if row["kind"] and row["kind"] not in EVIDENCE_KINDS:
            record["errors"].append(f"line {line}: invalid kind {row['kind']!r}")
        source_specs, source_errors = _parse_evidence_sources(row["sources"], line)
        row["source_specs"] = source_specs
        record["errors"].extend(source_errors)
        if row["kind"] in {"statistic", "output"} and len(source_specs) != 1:
            record["errors"].append(
                f"line {line}: {row['kind']} requires exactly one source"
            )
        if row["kind"] == "table" and not source_specs:
            record["errors"].append(f"line {line}: table requires at least one source")
        identity = (row["entry"], row["section"], row["kind"], row["evidence"])
        if identity in seen:
            record["errors"].append(f"line {line}: duplicate evidence identity")
        seen.add(identity)
    return record


def _summary_evidence_record(path: Path) -> Dict[str, Any]:
    """Read and structurally validate one summary evidence record."""

    record = _read_evidence_csv(path, SUMMARY_EVIDENCE_HEADER)
    seen = set()
    for row in record["rows"]:
        line = row["line"]
        for field in ("statistic", "entry", "section"):
            if not row[field]:
                record["errors"].append(f"line {line}: {field} is required")
        if row["entry"] and not ENTRY_ID_RE.fullmatch(row["entry"]):
            record["errors"].append(f"line {line}: invalid entry ID {row['entry']!r}")
        if row["statistic"] in seen:
            record["errors"].append(f"line {line}: duplicate statistic identity")
        seen.add(row["statistic"])
    return record


def _resolve_evidence_source(
    specification: Dict[str, str],
    entry_path: Path,
    project_root: Path,
    data_index: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve one valid evidence source into a stable validation identity."""

    raw = specification["source"]
    path = _expand_local_tokens(raw, entry_path, project_root, data_index)
    if path is None:
        return {
            **specification,
            "status": "unresolved",
            "identity": raw,
            "path": None,
        }
    return {
        **specification,
        "status": "resolved" if path.exists() else "missing",
        "identity": display_path(path, project_root),
        "path": path.as_posix(),
    }


def _argparse_flags(path: Path) -> Dict[str, Any]:
    try:
        tree = ast.parse(_read_text(path), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return {
            "parse": "fail",
            "error": str(exc),
            "flags": [],
            "positionals": [],
            "argument_roles": {},
        }
    flags = set()
    positionals = []
    destinations = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        declared = []
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                declared.append(argument.value)
                if argument.value.startswith("-"):
                    flags.add(argument.value)
                else:
                    positionals.append((node.lineno, argument.value))
                    break
        explicit_dest = next(
            (
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "dest"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ),
            None,
        )
        if explicit_dest:
            destinations.add(explicit_dest)
        elif declared:
            destinations.add(
                next(
                    (value for value in declared if not value.startswith("-")),
                    declared[-1].lstrip("-").replace("-", "_"),
                )
            )
    return {
        "parse": "ok",
        "error": None,
        "flags": sorted(flags),
        "positionals": [name for _, name in sorted(positionals)],
        "argument_roles": _argument_roles_from_ast(tree, destinations),
    }


def _call_leaf_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id.lower()
    if isinstance(function, ast.Attribute):
        return function.attr.lower()
    return ""


def _open_call_role(call: ast.Call) -> Optional[str]:
    mode = None
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    if not isinstance(mode, str):
        return "input"
    return "output" if any(flag in mode for flag in "wax+") else "input"


def _call_path_role(call: ast.Call) -> Optional[str]:
    leaf = _call_leaf_name(call)
    if leaf == "open":
        return _open_call_role(call)
    if leaf in {
        "mkdir",
        "touch",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
        "savefig",
        "savetxt",
        "savez",
        "savez_compressed",
        "to_csv",
        "to_hdf",
        "to_json",
        "to_parquet",
        "to_pickle",
    } or re.search(r"(?:^|_)(?:write|save|dump|export|emit)(?:_|$)", leaf):
        return "output"
    if leaf in {
        "exists",
        "is_dir",
        "is_file",
        "read_bytes",
        "read_text",
    } or re.search(r"(?:^|_)(?:read|load|parse|inspect|scan)(?:_|$)", leaf):
        return "input"
    return None


def _argument_roles_from_ast(
    tree: ast.AST, destinations: Iterable[str]
) -> Dict[str, str]:
    """Infer path roles from actual parsed-argument use in the entrypoint."""

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    roles: Dict[str, set[str]] = {destination: set() for destination in destinations}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"args", "parsed"}
            and node.attr in roles
        ):
            continue
        current: Optional[ast.AST] = node
        for _ in range(8):
            current = parents.get(current)
            if current is None:
                break
            if isinstance(current, ast.keyword) and current.arg in {
                "cwd",
                "working_dir",
                "working_directory",
            }:
                roles[node.attr].add("workspace")
            if isinstance(
                current,
                (ast.JoinedStr, ast.ListComp, ast.SetComp, ast.GeneratorExp),
            ) and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and "addpath(" in value.value.lower()
                for value in ast.walk(current)
            ):
                roles[node.attr].add("dependency-container")
            if isinstance(current, ast.Call):
                role = _call_path_role(current)
                if role:
                    roles[node.attr].add(role)
    return {
        destination: (
            next(iter(found))
            if len(found) == 1
            else "workspace"
            if found == {"workspace", "input"}
            else "dependency-container"
            if found == {"dependency-container", "input"}
            else "unknown"
        )
        for destination, found in roles.items()
    }


def _command_lines(block: Dict[str, Any]) -> List[str]:
    if block["kind"] != "command":
        return []
    logical: List[str] = []
    buffer = ""
    for raw in block["text"].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$"):
            line = line[1:].lstrip()
        buffer += (" " if buffer else "") + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            logical.append(buffer)
            buffer = ""
    if buffer:
        logical.append(buffer)
    return logical


def _expand_local_tokens(
    value: str,
    entry_path: Path,
    project_root: Path,
    data_index: Dict[str, Any],
) -> Optional[Path]:
    rows = {row.get("name", ""): row for row in data_index["rows"]}
    duplicates = set(data_index["duplicates"])
    expanded = value
    for name in TOKEN_RE.findall(value):
        if name == "project":
            replacement = project_root.as_posix()
        elif name == "log":
            replacement = entry_path.parents[2].as_posix()
        elif name in rows and name not in duplicates:
            reference = resolve_reference(
                rows[name].get("location", ""), data_index_path(data_index, entry_path)
            )
            replacement = reference.get("path")
            if replacement is None:
                return None
        else:
            return None
        expanded = expanded.replace(f"<{name}>", replacement)
    if TOKEN_RE.search(expanded):
        return None
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = entry_path.parent / candidate
    return candidate.resolve()


def _path_arguments(
    tokens: Sequence[str],
    script_token: Optional[str],
    interface: Optional[Dict[str, Any]],
    entry_path: Path,
    project_root: Path,
    data_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    results = []
    workspace_roots = {
        project_root.resolve(),
        entry_path.parents[2].resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    system_temp_root = Path("/tmp")
    if system_temp_root.exists():
        workspace_roots.add(system_temp_root.resolve())
    skip_next = False
    positional_index = 0
    positionals = interface.get("positionals", []) if interface else []
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if index == 0 or token == script_token:
            continue
        option = None
        value = token
        if token.startswith("--") and "=" in token:
            option, value = token.split("=", 1)
        elif token.startswith("--"):
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                continue
            option = token
            value = tokens[index + 1]
            skip_next = True
        else:
            if positional_index < len(positionals):
                option = positionals[positional_index]
            positional_index += 1
        path_value = value.split("=", 1)[1] if "=" in value else value
        suffix = Path(path_value.split("#", 1)[0]).suffix.lower()
        path = _expand_local_tokens(path_value, entry_path, project_root, data_index)
        if path is None:
            continue
        previous = tokens[index - 1] if index else ""
        indexed_names = {
            name for name in TOKEN_RE.findall(path_value) if name not in {"project", "log"}
        }
        path_like = (
            bool(TOKEN_RE.search(path_value))
            or suffix in PATH_SUFFIXES
            or "/" in path_value
            or path.exists()
            or Path(previous).name == "tee"
            or previous in {">", ">>"}
        )
        if not path_like or any(character.isspace() for character in path_value):
            continue
        if path in workspace_roots:
            role_hint = "workspace"
        elif Path(previous).name == "tee" or previous in {">", ">>"}:
            role_hint = "output"
        elif indexed_names:
            role_hint = "input"
        else:
            parameter = (option or "").lstrip("-").replace("-", "_")
            role_hint = (
                interface.get("argument_roles", {}).get(parameter, "unknown")
                if interface
                else "unknown"
            )
        result = {
            "option": option,
            "raw": value,
            "path": path.as_posix(),
            "exists": path.exists(),
            "role_hint": role_hint,
        }
        if role_hint == "dependency-container":
            dependency_paths = [path.resolve()]
            raw_path = Path(path_value)
            if not raw_path.is_absolute():
                log_relative = (entry_path.parents[2] / raw_path).resolve()
                if log_relative not in dependency_paths:
                    dependency_paths.append(log_relative)
            result["dependency_paths"] = [
                item.as_posix()
                for item in dependency_paths
                if item.is_dir()
            ]
        results.append(result)
    return results


def _invocation_tokens(tokens: Sequence[str]) -> List[str]:
    """Remove leading shell environment assignments from one invocation."""

    index = 0
    while index < len(tokens) and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]
    ):
        index += 1
    return list(tokens[index:])


def _option_values(
    tokens: Sequence[str], script_token: Optional[str]
) -> List[Dict[str, Optional[str]]]:
    """Return explicit option and positional values from one invocation."""

    values = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if index == 0 or token == script_token:
            continue
        if token.startswith("--") and "=" in token:
            option, value = token.split("=", 1)
        elif token.startswith("--"):
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                continue
            option = token
            value = tokens[index + 1]
            skip_next = True
        else:
            option = None
            value = token
        values.append({"option": option, "value": value})
    return values


def _commands(
    parsed: Dict[str, Any],
    entry_path: Path,
    project_root: Path,
    script_inventory: Optional[set[Path]] = None,
) -> List[Dict[str, Any]]:
    commands: List[Dict[str, Any]] = []
    data_index = _data_index(entry_path)
    data_rows = {row.get("name", ""): row for row in data_index["rows"]}
    for block in parsed["fenced_blocks"]:
        if block["section_type"] != "experimental":
            continue
        for command in _command_lines(block):
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                commands.append(
                    {
                        "line": block["line"],
                        "section": block["section"],
                        "command": command,
                        "error": str(exc),
                    }
                )
                continue
            if not tokens:
                continue
            invocation = _invocation_tokens(tokens)
            if not invocation:
                continue
            script_token: Optional[str] = None
            if Path(invocation[0]).name == "pyrun" and len(invocation) > 1:
                script_token = invocation[1]
            elif (
                Path(invocation[0]).name.startswith("python")
                and len(invocation) > 1
            ):
                script_token = invocation[1]
            script_path: Optional[Path] = None
            interface: Optional[Dict[str, Any]] = None
            if script_token:
                script_path = _expand_local_tokens(
                    script_token, entry_path, project_root, data_index
                )
                if (
                    script_path
                    and script_path.suffix == ".py"
                    and script_path.is_file()
                ):
                    interface = _argparse_flags(script_path)
            options = sorted(
                {token.split("=", 1)[0] for token in tokens if token.startswith("--")}
            )
            unknown = []
            if interface and interface["parse"] == "ok":
                unknown = sorted(set(options) - set(interface["flags"]))
            token_results = []
            for name in TOKEN_RE.findall(command):
                if name in {"project", "log"}:
                    resolved = (
                        project_root if name == "project" else entry_path.parents[2]
                    )
                    token_results.append(
                        {
                            "name": name,
                            "status": "resolved",
                            "path": resolved.as_posix(),
                        }
                    )
                elif name in data_rows and name not in data_index["duplicates"]:
                    location = data_rows[name].get("location", "")
                    resolved_ref = resolve_reference(
                        location, data_index_path(data_index, entry_path)
                    )
                    token_results.append(
                        {"name": name, "status": "resolved", **resolved_ref}
                    )
                elif name in data_index["duplicates"]:
                    token_results.append({"name": name, "status": "ambiguous"})
                else:
                    token_results.append({"name": name, "status": "unresolved"})
            record = {
                "line": block["line"],
                "section": block["section"],
                "command": command,
                "script": script_path.as_posix() if script_path else script_token,
                "script_token": script_token,
                "script_interface": interface,
                "options": options,
                "option_values": _option_values(invocation, script_token),
                "unknown_options": unknown,
                "data_tokens": token_results,
                "path_arguments": _path_arguments(
                    invocation,
                    script_token,
                    interface,
                    entry_path,
                    project_root,
                    data_index,
                ),
            }
            if script_inventory:
                _extend_matlab_command_dependencies(
                    record, entry_path, project_root, script_inventory
                )
            commands.append(record)
    return commands


def data_index_path(data_index: Dict[str, Any], entry_path: Path) -> Path:
    raw = data_index.get("path")
    return Path(raw) if raw else entry_path.parent / "data.csv"


def _inspect_structure(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        if path.is_dir():
            with os.scandir(str(path)) as entries:
                members = sum(1 for _ in entries)
            return {
                "status": "ok",
                "type": "directory",
                "immediate_members": members,
                "identity": "deferred-until-adjudication",
            }
        if suffix == ".py":
            result = _argparse_flags(path)
            return {
                "status": result["parse"],
                "type": "python",
                "detail": result.get("error"),
            }
        if suffix == ".json":
            json.loads(_read_text(path))
            return {"status": "ok", "type": "json"}
        if suffix == ".ecsv":
            lines = _read_text(path).splitlines()
            if not lines or not lines[0].startswith("# %ECSV "):
                return {
                    "status": "fail",
                    "type": "ecsv",
                    "detail": "missing ECSV signature",
                }
            records = [
                shlex.split(line)
                for line in lines
                if line.strip() and not line.startswith("#")
            ]
            if not records:
                return {
                    "status": "fail",
                    "type": "ecsv",
                    "detail": "missing ECSV header",
                }
            widths = sorted({len(row) for row in records})
            return {
                "status": "ok" if len(widths) == 1 else "fail",
                "type": "ecsv",
                "rows": max(0, len(records) - 1),
                "columns": records[0],
            }
        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle, delimiter=delimiter))
            widths = sorted({len(row) for row in rows if row})
            return {
                "status": "ok" if len(widths) <= 1 else "fail",
                "type": "table",
                "rows": max(0, len(rows) - 1),
                "columns": widths,
            }
        if suffix == ".png":
            with path.open("rb") as handle:
                header = handle.read(24)
            if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
                return {
                    "status": "fail",
                    "type": "png",
                    "detail": "invalid PNG signature",
                }
            width, height = struct.unpack(">II", header[16:24])
            return {"status": "ok", "type": "png", "width": width, "height": height}
        if suffix in {".jpg", ".jpeg"}:
            with path.open("rb") as handle:
                start = handle.read(2)
                handle.seek(-2, os.SEEK_END)
                end = handle.read(2)
            return {
                "status": (
                    "ok" if start == b"\xff\xd8" and end == b"\xff\xd9" else "fail"
                ),
                "type": "jpeg",
            }
        if suffix == ".npz":
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                names = sorted(archive.namelist())
            return {
                "status": "ok" if bad is None else "fail",
                "type": "npz",
                "members": names,
                "bad": bad,
            }
        if suffix == ".npy":
            with path.open("rb") as handle:
                magic = handle.read(6)
            return {"status": "ok" if magic == b"\x93NUMPY" else "fail", "type": "npy"}
        if suffix in {".h5", ".hdf5"}:
            with path.open("rb") as handle:
                magic = handle.read(8)
            return {
                "status": "ok" if magic == b"\x89HDF\r\n\x1a\n" else "fail",
                "type": "hdf5",
            }
        if suffix in {".fit", ".fits"}:
            with path.open("rb") as handle:
                magic = handle.read(30)
            return {
                "status": "ok" if magic.startswith(b"SIMPLE") else "fail",
                "type": "fits",
            }
        with path.open("rb") as handle:
            handle.read(1)
        return {"status": "ok", "type": suffix.lstrip(".") or "file"}
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        csv.Error,
        zipfile.BadZipFile,
    ) as exc:
        return {
            "status": "fail",
            "type": suffix.lstrip(".") or "file",
            "detail": str(exc),
        }


def _discover_entries(summary_path: Path, log_root: Path) -> Dict[str, Any]:
    parsed = parse_markdown(summary_path)
    listed: List[Dict[str, Any]] = []
    seen = set()
    lines = _read_text(summary_path).splitlines()
    in_entries = False
    for number, line in enumerate(lines, 1):
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            if heading.group(2).strip() == "Entries":
                in_entries = True
                continue
            if in_entries:
                break
        if not in_entries:
            continue
        for match in LINK_RE.finditer(line):
            resolved = resolve_reference(match.group("target"), summary_path)
            raw_path = resolved.get("path")
            if (
                not raw_path
                or not raw_path.endswith(".md")
                or "/entries/" not in raw_path
            ):
                continue
            path = Path(raw_path)
            entry_id = path.stem
            if not ENTRY_ID_RE.match(entry_id) or raw_path in seen:
                continue
            seen.add(raw_path)
            listed.append(
                {
                    "id": entry_id,
                    "title": match.group("label"),
                    "path": raw_path,
                    "line": number,
                    "exists": path.is_file(),
                }
            )
    discovered = sorted(
        path.resolve().as_posix() for path in (log_root / "entries").glob("**/*.md")
    )
    listed_paths = [entry["path"] for entry in listed]
    return {
        "listed": listed,
        "discovered": discovered,
        "unlisted": sorted(set(discovered) - set(listed_paths)),
        "missing": [entry["path"] for entry in listed if not entry["exists"]],
        "summary": parsed,
    }


def _candidate_references(
    parsed: Dict[str, Any], source: Path, project_root: Path
) -> List[Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    for reference in parsed["links"]:
        if reference["section_type"] != "experimental":
            continue
        if reference["kind"] in {"anchor", "external", "token"}:
            continue
        if reference.get("block_label") != "Results":
            continue
        identity = reference.get("path") or reference["target"]
        item = candidates.setdefault(
            identity,
            {
                "identity": (
                    display_path(Path(identity), project_root)
                    if reference.get("path")
                    else identity
                ),
                "resolved_path": reference.get("path"),
                "kind": "figure" if reference.get("image") else reference["kind"],
                "presented": True,
                "sections": [],
                "occurrences": [],
            },
        )
        if reference["section"] not in item["sections"]:
            item["sections"].append(reference["section"])
        item["occurrences"].append(
            {"line": reference["line"], "label": reference.get("label", "")}
        )
    return list(candidates.values())


def _merge_command_candidates(
    candidates: List[Dict[str, Any]],
    commands: Sequence[Dict[str, Any]],
    project_root: Path,
) -> List[Dict[str, Any]]:
    by_identity = {candidate["identity"]: candidate for candidate in candidates}
    for command in commands:
        for argument in command.get("path_arguments", []):
            if argument["role_hint"] in {"workspace", "dependency-container"}:
                continue
            path = Path(argument["path"])
            identity = display_path(path, project_root)
            candidate = by_identity.setdefault(
                identity,
                {
                    "identity": identity,
                    "resolved_path": path.as_posix(),
                    "kind": "command-path",
                    "sections": [],
                    "occurrences": [],
                    "role_hints": [],
                    "presented": False,
                },
            )
            if command["section"] not in candidate["sections"]:
                candidate["sections"].append(command["section"])
            occurrence = {
                "line": command["line"],
                "label": argument.get("option") or argument["raw"],
                "role_hint": argument["role_hint"],
            }
            candidate["occurrences"].append(occurrence)
            hints = candidate.setdefault("role_hints", [])
            if argument["role_hint"] not in hints:
                hints.append(argument["role_hint"])
    return list(by_identity.values())


def _script_inventory(root: Path) -> List[Path]:
    """Return research scripts below one designated script root."""

    if not root.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SCRIPT_SUFFIXES
        and not any(part in IGNORED_SCRIPT_PARTS for part in path.parts)
    )


def _log_owned_roots(log_root: Path) -> List[Path]:
    """Return the log tree and targets reached through log-owned symlinks."""

    roots = {log_root.resolve()}
    for current, directories, files in os.walk(log_root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            candidate = current_path / name
            if not candidate.is_symlink():
                continue
            try:
                roots.add(candidate.resolve(strict=True))
            except OSError:
                continue
    return sorted(roots)


def _path_is_log_owned(path: Path, owned_roots: Sequence[Path]) -> bool:
    """Return whether a resolved path lies on the log's logical file surface."""

    resolved = path.resolve()
    for root in owned_roots:
        if resolved == root:
            return True
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _python_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve statically identifiable local dependencies for one Python script."""

    try:
        tree = ast.parse(_read_text(path), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []
    candidates: set[Path] = set()

    import_roots = [path.parent]

    def add_module(module: str, level: int) -> None:
        base = path.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        parts = module.split(".")
        roots = [base] if level else import_roots
        direct_match = None
        for root in roots:
            # Python resolves one sys.path root at a time. A package in the
            # first matching root wins over a same-named module or package in
            # every later root.
            for candidate in (
                root.joinpath(*parts, "__init__.py").resolve(),
                root.joinpath(*parts).with_suffix(".py").resolve(),
            ):
                if candidate in inventory:
                    direct_match = candidate
                    break
            if direct_match is not None:
                break
        if direct_match is not None:
            candidates.add(direct_match)
        if level == 0 and direct_match is None:
            direct_suffix = Path(*parts).with_suffix(".py").parts
            package_suffix = (*parts, "__init__.py")
            matches = [
                candidate
                for candidate in inventory
                if candidate.suffix.lower() == ".py"
                and (
                    candidate.parts[-len(direct_suffix) :] == direct_suffix
                    or candidate.parts[-len(package_suffix) :] == package_suffix
                )
            ]
            if len(matches) == 1:
                candidates.add(matches[0])

    bindings: Dict[str, Tuple[Path, bool]] = {"__file__": (path, True)}

    def static_path(node: ast.AST) -> Optional[Tuple[Path, bool]]:
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return Path(node.value), False
        if isinstance(node, ast.Call):
            name = _python_call_name(node.func)
            if name in {"Path", "pathlib.Path"} and node.args:
                return static_path(node.args[0])
            if name in {"str", "os.fspath"} and node.args:
                return static_path(node.args[0])
            if isinstance(node.func, ast.Attribute):
                base = static_path(node.func.value)
                if base is None:
                    return None
                value, anchored = base
                if node.func.attr in {"resolve", "absolute", "expanduser"}:
                    return value.resolve(), anchored
                if node.func.attr == "joinpath":
                    result = value
                    for argument in node.args:
                        part = static_path(argument)
                        if part is None:
                            return None
                        result /= part[0]
                        anchored = anchored or part[1]
                    return result, anchored
                if node.func.attr == "with_name" and len(node.args) == 1:
                    name_value = static_path(node.args[0])
                    if name_value is not None:
                        return value.with_name(str(name_value[0])), anchored
            return None
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = static_path(node.value)
            return (base[0].parent, base[1]) if base is not None else None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr != "parents":
                return None
            base = static_path(node.value.value)
            index = node.slice
            if (
                base is not None
                and isinstance(index, ast.Constant)
                and isinstance(index.value, int)
            ):
                try:
                    return base[0].parents[index.value], base[1]
                except IndexError:
                    return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = static_path(node.left)
            right = static_path(node.right)
            if left is not None and right is not None:
                return left[0] / right[0], left[1] or right[1]
        return None

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(4):
        changed = False
        for node in assignments:
            value_node = node.value
            if value_node is None:
                continue
            value = static_path(value_node)
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and bindings.get(target.id) != value:
                    bindings[target.id] = value
                    changed = True
        if not changed:
            break

    path_calls: List[Tuple[str, Path]] = []

    def add_path_call(node: ast.Call) -> None:
        name = _python_call_name(node.func)
        value_node: Optional[ast.AST] = None
        if name == "sys.path.insert" and len(node.args) >= 2:
            index = node.args[0]
            if not (
                isinstance(index, ast.Constant)
                and isinstance(index.value, int)
                and index.value == 0
            ):
                return
            value_node = node.args[1]
        elif name == "sys.path.append" and node.args:
            value_node = node.args[0]
        if value_node is None:
            return
        resolved = static_path(value_node)
        if resolved is None or not resolved[1]:
            return
        root = resolved[0].resolve()
        path_calls.append((name, root))

    def collect_path_calls(statements: Sequence[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if (
                isinstance(statement, (ast.For, ast.AsyncFor))
                and isinstance(statement.target, ast.Name)
                and isinstance(statement.iter, (ast.Tuple, ast.List))
            ):
                values = [static_path(item) for item in statement.iter.elts]
                if all(value is not None for value in values):
                    name = statement.target.id
                    sentinel = object()
                    prior = bindings.get(name, sentinel)
                    for value in values:
                        bindings[name] = value
                        collect_path_calls(statement.body)
                    if prior is sentinel:
                        bindings.pop(name, None)
                    else:
                        bindings[name] = prior
                    collect_path_calls(statement.orelse)
                    continue
            if isinstance(statement, ast.If):
                collect_path_calls(statement.body)
                collect_path_calls(statement.orelse)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                collect_path_calls(statement.body)
                continue
            if isinstance(statement, ast.Try):
                collect_path_calls(statement.body)
                for handler in statement.handlers:
                    collect_path_calls(handler.body)
                collect_path_calls(statement.orelse)
                collect_path_calls(statement.finalbody)
                continue
            calls = sorted(
                (node for node in ast.walk(statement) if isinstance(node, ast.Call)),
                key=lambda node: (node.lineno, node.col_offset),
            )
            for call in calls:
                add_path_call(call)

    collect_path_calls(tree.body)

    # Apply mutations in source order. Consecutive insert(0, ...) calls
    # reverse the apparent source order, exactly as they do at runtime.
    for name, root in path_calls:
        if name == "sys.path.insert":
            import_roots.insert(0, root)
        else:
            import_roots.append(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_module(alias.name, 0)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                add_module(node.module, node.level)
            for alias in node.names:
                if alias.name == "*":
                    continue
                child = ".".join(part for part in (node.module, alias.name) if part)
                add_module(child, node.level)

    for node in ast.walk(tree):
        resolved = static_path(node)
        if resolved is None or not resolved[1]:
            continue
        candidate = resolved[0].resolve()
        if candidate in inventory and candidate != path.resolve():
            candidates.add(candidate)

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def expanded_call_name(node: ast.AST) -> str:
        name = _python_call_name(node)
        first, separator, rest = name.partition(".")
        replacement = aliases.get(first)
        if replacement is None:
            return name
        return replacement + (separator + rest if separator else "")

    execution_calls = {
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.system",
        "runpy.run_path",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.run",
    }

    def add_execution_value(node: ast.AST) -> None:
        resolved = static_path(node)
        if resolved is not None:
            candidate = resolved[0]
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate in inventory and candidate != path.resolve():
                candidates.add(candidate)
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return
        try:
            tokens = shlex.split(node.value)
        except ValueError:
            tokens = [node.value]
        for token in tokens:
            if Path(token).suffix.lower() not in SCRIPT_SUFFIXES:
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate in inventory and candidate != path.resolve():
                candidates.add(candidate)

    for call in calls:
        if expanded_call_name(call.func) not in execution_calls:
            continue
        for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
            for node in ast.walk(argument):
                add_execution_value(node)

    has_matlab_launcher = any(
        "matlab" in expanded_call_name(node.func).lower() for node in calls
    )
    if has_matlab_launcher:
        matlab_roots = {path.parent}
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(
                value.value
                for value in node.values
                if isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            )
            if "addpath(" not in literal:
                continue
            for value in node.values:
                if not isinstance(value, ast.FormattedValue):
                    continue
                expression = value.value
                if isinstance(expression, ast.Call) and expression.args:
                    expression = expression.args[0]
                resolved = static_path(expression)
                if resolved is not None and resolved[1]:
                    matlab_roots.add(resolved[0].resolve())
        local_matlab: Dict[str, List[Path]] = {}
        for candidate in inventory:
            if (
                candidate.parent not in matlab_roots
                or candidate.suffix.lower() != ".m"
            ):
                continue
            local_matlab.setdefault(candidate.stem, []).append(candidate)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for name in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z]\w*)\s*\(", node.value):
                matches = local_matlab.get(name, [])
                if len(matches) == 1:
                    candidates.add(matches[0])
    return sorted(candidates)


def _python_call_name(node: ast.AST) -> str:
    """Return one dotted Python call target without evaluating source code."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _python_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _shell_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve literal source and interpreter dependencies for one shell script."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return []
    candidates = set()
    interpreters = {"bash", "dash", "julia", "python", "python3", "rscript", "sh"}
    for line in text.splitlines():
        match = re.match(r"^\s*(?:source|\.)\s+([^\s;&|]+)", line)
        if match and not any(char in match.group(1) for char in "$`*?[]{}"):
            raw = match.group(1).strip("'\"")
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate in inventory:
                candidates.add(candidate)
        try:
            tokens = _invocation_tokens(shlex.split(line))
        except ValueError:
            continue
        if not tokens:
            continue
        script_index = 1 if Path(tokens[0]).name.lower() in interpreters else 0
        if script_index >= len(tokens):
            continue
        raw = tokens[script_index]
        if any(char in raw for char in "$`*?[]{}"):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve()
        if candidate in inventory:
            candidates.add(candidate)
    return sorted(candidates)


def _literal_source_dependencies(
    path: Path, inventory: set[Path], patterns: Sequence[str]
) -> List[Path]:
    """Resolve quoted local source/include paths for a research script."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return []
    candidates = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = Path(match.group(1))
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate in inventory:
                candidates.add(candidate)
    return sorted(candidates)


def _matlab_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve explicit local MATLAB file and same-folder function calls."""

    dependencies = set(
        _literal_source_dependencies(
            path,
            inventory,
            (r"\brun\s*\(\s*['\"]([^'\"]+\.m)['\"]",),
        )
    )
    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return sorted(dependencies)
    local_functions = {
        candidate.stem: candidate
        for candidate in inventory
        if candidate.parent == path.parent and candidate.suffix.lower() == ".m"
    }
    code = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    for name, candidate in local_functions.items():
        if candidate != path and re.search(rf"\b{re.escape(name)}\s*\(", code):
            dependencies.add(candidate)
    return sorted(dependencies)


def _split_matlab_arguments(value: str) -> List[str]:
    """Split one static MATLAB call argument list without evaluating it."""

    arguments = []
    buffer = []
    quote: Optional[str] = None
    depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote:
            buffer.append(character)
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    buffer.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            buffer.append(character)
        elif character in "([{" :
            depth += 1
            buffer.append(character)
        elif character in ")]}" and depth:
            depth -= 1
            buffer.append(character)
        elif character == "," and depth == 0:
            arguments.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
        index += 1
    if buffer or value.strip():
        arguments.append("".join(buffer).strip())
    return arguments


def _matlab_function_argument_roles(
    path: Path,
) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """Return one MATLAB function name and statically evident path roles."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return None, []
    match = re.search(
        r"^\s*function\s+(?:\[[^\]]*\]\s*=\s*|\w+\s*=\s*)?"
        r"([A-Za-z]\w*)\s*\(([^)]*)\)",
        text,
        flags=re.MULTILINE,
    )
    if not match:
        return None, []
    code = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    parameters = [item.strip() for item in match.group(2).split(",") if item.strip()]
    roles = []
    for parameter in parameters:
        escaped = re.escape(parameter)
        output = bool(
            re.search(
                rf"\b(?:resolve_output_path|writetable|writematrix|writecell|"
                rf"save)\s*\([^;\n]*\b{escaped}\b",
                code,
                flags=re.IGNORECASE,
            )
        )
        input_ = bool(
            re.search(
                rf"\b(?:resolve_existing_path|readtable|readmatrix|readcell|"
                rf"load)\s*\(\s*\b{escaped}\b",
                code,
                flags=re.IGNORECASE,
            )
        )
        roles.append((parameter, "output" if output else "input" if input_ else "unknown"))
    return match.group(1), roles


def _static_matlab_path_argument(
    value: str, entry_path: Path, project_root: Path
) -> Optional[Path]:
    """Resolve one quoted static MATLAB path argument from a recorded command."""

    value = value.strip()
    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None
    raw = value[1:-1].replace(value[0] * 2, value[0])
    raw = raw.replace("<project>", project_root.as_posix())
    raw = raw.replace("<log>", entry_path.parents[2].as_posix())
    if TOKEN_RE.search(raw):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = entry_path.parent / path
    return path.resolve()


def _extend_matlab_command_dependencies(
    command: Dict[str, Any],
    entry_path: Path,
    project_root: Path,
    script_inventory: set[Path],
) -> None:
    """Add static MATLAB producer and path dependencies from a wrapper command."""

    container_roots = []
    for argument in command.get("path_arguments", []):
        if argument.get("role_hint") != "dependency-container":
            continue
        for raw in argument.get("dependency_paths", [argument["path"]]):
            root = Path(raw).resolve()
            if root not in container_roots:
                container_roots.append(root)
    if not container_roots:
        return

    matlab_scripts = []
    added_arguments = []
    for option_value in command.get("option_values", []):
        value = option_value.get("value")
        if not isinstance(value, str):
            continue
        match = re.fullmatch(
            r"\s*([A-Za-z]\w*)\s*\((.*)\)\s*;?\s*", value, flags=re.DOTALL
        )
        if not match:
            continue
        name = match.group(1)
        script = next(
            (
                candidate
                for root in container_roots
                for candidate in [root / f"{name}.m"]
                if candidate.resolve() in script_inventory
            ),
            None,
        )
        if script is None:
            continue
        script = script.resolve()
        function_name, parameters = _matlab_function_argument_roles(script)
        if function_name != name:
            continue
        if script not in matlab_scripts:
            matlab_scripts.append(script)
        values = _split_matlab_arguments(match.group(2))
        for (_, role), argument_value in zip(parameters, values):
            if role not in {"input", "output"}:
                continue
            path = _static_matlab_path_argument(
                argument_value, entry_path, project_root
            )
            if path is None:
                continue
            added_arguments.append(
                {
                    "option": option_value.get("option"),
                    "raw": argument_value,
                    "path": path.as_posix(),
                    "exists": path.exists(),
                    "role_hint": role,
                    "source": "matlab-command",
                }
            )

    command["matlab_scripts"] = [path.as_posix() for path in matlab_scripts]
    existing = {
        (argument.get("path"), argument.get("role_hint"))
        for argument in command.get("path_arguments", [])
    }
    command["path_arguments"].extend(
        argument
        for argument in added_arguments
        if (argument["path"], argument["role_hint"]) not in existing
    )


def _script_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve mechanically supported local dependency forms by script type."""

    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_local_dependencies(path, inventory)
    if suffix == ".sh":
        return _shell_local_dependencies(path, inventory)
    if suffix == ".m":
        return _matlab_local_dependencies(path, inventory)
    if suffix == ".r":
        return _literal_source_dependencies(
            path,
            inventory,
            (r"\b(?:source|sys\.source)\s*\(\s*['\"]([^'\"]+)['\"]",),
        )
    if suffix == ".jl":
        return _literal_source_dependencies(
            path, inventory, (r"\binclude\s*\(\s*['\"]([^'\"]+)['\"]",)
        )
    return []


def _reachable_script_dependencies(
    seeds: Iterable[Path],
    inventory: set[Path],
    graph: Optional[Dict[Path, List[Path]]] = None,
) -> Tuple[set[Path], Dict[Path, List[Path]]]:
    """Follow mechanically resolvable local script dependencies once per file."""

    if graph is None:
        graph = {}
    reachable: set[Path] = set()
    pending = [path.resolve() for path in seeds if path.resolve() in inventory]
    while pending:
        path = pending.pop()
        if path in reachable:
            continue
        reachable.add(path)
        dependencies = graph.get(path)
        if dependencies is None:
            dependencies = _script_local_dependencies(path, inventory)
            graph[path] = dependencies
        pending.extend(item for item in dependencies if item not in reachable)
    return reachable, graph


def _workflow_dependency_closure(
    entries: Sequence[Dict[str, Any]],
    resolved_paths: Dict[str, str],
    project_root: Path,
    seed_identities: Iterable[str],
    script_inventory: set[Path],
    script_graph: Optional[Dict[Path, List[Path]]] = None,
    ambiguous_output_identities: Iterable[str] = (),
) -> Tuple[
    set[str],
    set[str],
    set[Path],
    set[Tuple[str, str]],
    Dict[Path, List[Path]],
]:
    """Follow retained producers, their inputs, and local script dependencies."""

    command_records: Dict[Tuple[str, int], Dict[str, Any]] = {}
    output_commands: Dict[str, List[Tuple[str, int]]] = {}
    named_commands: Dict[str, List[Tuple[str, int]]] = {}
    identity_context = {
        "resolved_paths": resolved_paths,
        "project_root": project_root.as_posix(),
    }
    ambiguous_outputs = set(ambiguous_output_identities)
    for entry in entries:
        for index, command in enumerate(entry.get("commands", [])):
            key = (entry["id"], index)
            command_records[key] = command
            for argument in command.get("path_arguments", []):
                if argument.get("role_hint") in {
                    "workspace",
                    "dependency-container",
                }:
                    continue
                identity = _identity_for_path(identity_context, argument["path"])
                named_commands.setdefault(identity, []).append(key)
                if argument.get("role_hint") != "output":
                    continue
                output_commands.setdefault(identity, []).append(key)

    def producing_commands(identity: str) -> List[Tuple[str, int]]:
        # A command that names an output directory does not establish that it
        # produced every retained artifact below that directory. Several
        # commands commonly share directories such as ``images`` or ``data``.
        # Follow only exact retained-output identities here; artifact-to-command
        # associations that are not explicit remain a semantic decision.
        confirmed = output_commands.get(identity, [])
        if confirmed or identity not in ambiguous_outputs:
            return list(confirmed)
        return list(named_commands.get(identity, []))

    reachable_identities = set(seed_identities)
    used_identities = set(reachable_identities)
    pending = list(reachable_identities)
    reachable_commands: set[Tuple[str, int]] = set()
    connected_tokens: set[Tuple[str, str]] = set()
    script_seeds: set[Path] = set()
    for identity in reachable_identities:
        raw = resolved_paths.get(identity)
        if raw and Path(raw).resolve() in script_inventory:
            script_seeds.add(Path(raw).resolve())

    while pending:
        identity = pending.pop()
        for key in producing_commands(identity):
            if key in reachable_commands:
                continue
            reachable_commands.add(key)
            command = command_records[key]
            for raw_script in [
                command.get("script"),
                *command.get("matlab_scripts", []),
            ]:
                if raw_script and Path(raw_script).is_file():
                    script_seeds.add(Path(raw_script).resolve())
            for token in command.get("data_tokens", []):
                if token["name"] in {"project", "log"}:
                    continue
                connected_tokens.add((key[0], token["name"]))
                raw = token.get("path")
                if raw:
                    dependency = _identity_for_path(identity_context, raw)
                    if dependency not in reachable_identities:
                        reachable_identities.add(dependency)
                        pending.append(dependency)
            for argument in command.get("path_arguments", []):
                role = argument.get("role_hint")
                if role in {"workspace", "dependency-container"}:
                    continue
                dependency = _identity_for_path(identity_context, argument["path"])
                used_identities.add(dependency)
                if role not in {"input", "output"}:
                    continue
                # Once one exact output establishes the command as part of a
                # used workflow, its other declared outputs and shell captures
                # are retained workflow siblings rather than orphans.
                dependency_path = Path(argument["path"]).resolve()
                if role == "input" and dependency_path in script_inventory:
                    script_seeds.add(dependency_path)
                if dependency not in reachable_identities:
                    reachable_identities.add(dependency)
                    pending.append(dependency)

    reachable_scripts, script_graph = _reachable_script_dependencies(
        script_seeds, script_inventory, script_graph
    )
    for entry in entries:
        for command in entry.get("commands", []):
            for token in command.get("data_tokens", []):
                raw = token.get("path")
                if not raw or token.get("name") in {"project", "log"}:
                    continue
                dependency = _identity_for_path(identity_context, raw)
                if dependency in used_identities:
                    connected_tokens.add((entry["id"], token["name"]))
    return (
        reachable_identities,
        used_identities,
        reachable_scripts,
        connected_tokens,
        script_graph,
    )


def _orphan_identity_is_used(
    identity: str,
    used_identities: set[str],
    resolved_paths: Dict[str, str],
) -> bool:
    """Return whether an orphan identity is used directly or as a container."""

    if identity in used_identities:
        return True
    raw = resolved_paths.get(identity)
    if not raw:
        return False
    container = Path(raw).resolve()
    if not container.is_dir():
        return False
    for used_identity in used_identities:
        used_raw = resolved_paths.get(used_identity)
        if not used_raw:
            continue
        used_path = Path(used_raw).resolve()
        if used_path == container:
            return True
        try:
            used_path.relative_to(container)
        except ValueError:
            continue
        return True
    return False


def _orphan_identity_is_accepted(
    identity: str,
    token_identities: set[str],
    used_identities: set[str],
    resolved_paths: Dict[str, str],
) -> bool:
    """Return whether one orphan identity is connected to reviewed use."""

    return identity in token_identities or _orphan_identity_is_used(
        identity, used_identities, resolved_paths
    )


def _discover_repository_logs(project_root: Path) -> List[Dict[str, Any]]:
    """Discover maintained research logs and their owned script surfaces."""

    search_root = project_root / "docs"
    if not search_root.is_dir():
        search_root = project_root
    records = []
    for summary in sorted(search_root.rglob("*.md")):
        log_root = summary.with_suffix("")
        entries_root = log_root / "entries"
        if not entries_root.is_dir():
            continue
        entry_paths = sorted(
            path.resolve()
            for path in entries_root.rglob("*.md")
            if ENTRY_ID_RE.fullmatch(path.stem)
        )
        script_roots = {log_root / "scripts"}
        script_roots.update(path.parent / "scripts" for path in entry_paths)
        scripts = sorted(
            {
                script
                for root in script_roots
                for script in _script_inventory(root)
            }
        )
        records.append(
            {
                "summary": display_path(summary.resolve(), project_root),
                "summary_path": summary.resolve(),
                "root": display_path(log_root.resolve(), project_root),
                "root_path": log_root.resolve(),
                "owned_roots": _log_owned_roots(log_root),
                "entries": entry_paths,
                "scripts": scripts,
            }
        )
    return records


def _repository_log_input_paths(log: Dict[str, Any]) -> List[Path]:
    """Return one log's files that can change repository dependency edges."""

    paths = set()
    paths.add(log["summary_path"])
    state = log["root_path"] / "validation-state.json"
    if state.is_file():
        paths.add(state.resolve())
    evidence = log["root_path"] / "evidence.csv"
    if evidence.is_file():
        paths.add(evidence.resolve())
    for entry in log["entries"]:
        paths.add(entry)
        for candidate in (
            entry.parent / "data.csv",
            entry.parent / "evidence.csv",
            entry.parent.parent / "data.csv",
            entry.parent.parent / "evidence.csv",
        ):
            if candidate.is_file():
                paths.add(candidate.resolve())
    paths.update(log["scripts"])
    return sorted(paths)


def _repository_index_input_paths(
    project_root: Path, logs: Sequence[Dict[str, Any]]
) -> List[Path]:
    """Return all files whose content can change repository dependency edges."""

    del project_root
    return sorted(
        {path for log in logs for path in _repository_log_input_paths(log)}
    )


def _repository_input_stats(
    project_root: Path, paths: Sequence[Path]
) -> Dict[str, Dict[str, int]]:
    """Return inexpensive membership, size, and modification identities."""

    result = {}
    for path in paths:
        stat = path.stat()
        result[display_path(path, project_root)] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def _assert_repository_inputs_stable(
    project_root: Path,
    paths: Sequence[Path],
    expected: Dict[str, Dict[str, int]],
) -> None:
    """Reject a repository index assembled across concurrent input changes."""

    if _repository_input_stats(project_root, paths) != expected:
        raise FileChangedError("repository-index inputs changed during scan")


def _repository_owner(
    path: Path, logs: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return the unique research log owning one resolved path."""

    resolved = Path(os.path.abspath(path))
    resolved_parts = resolved.parts
    matches = []
    for log in logs:
        depths = []
        for root in log["owned_roots"]:
            root_parts = root.parts
            if resolved_parts[: len(root_parts)] != root_parts:
                continue
            depths.append(len(root_parts))
        if depths:
            matches.append((max(depths), log))
    if not matches:
        return None
    depth = max(item[0] for item in matches)
    owners = [log for candidate_depth, log in matches if candidate_depth == depth]
    return owners[0] if len(owners) == 1 else None


def _repository_identity_path(identity: str, project_root: Path) -> Path:
    """Resolve one persisted validation identity against the project root."""

    path = Path(identity)
    candidate = path if path.is_absolute() else project_root / path
    return Path(os.path.abspath(candidate))


def _repository_display_path(path: Path, project_root: Path) -> str:
    """Return a lexical index identity for an already resolved dependency."""

    normalized = Path(os.path.abspath(path))
    root = Path(os.path.abspath(project_root))
    root_parts = root.parts
    if normalized.parts[: len(root_parts)] == root_parts:
        return Path(*normalized.parts[len(root_parts) :]).as_posix()
    return normalized.as_posix()


def build_repository_dependency_index(
    project_root: Path,
    prior_index: Optional[Dict[str, Any]] = None,
    rules_version: str = RULES_VERSION,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build or reuse the ownership-aware dependency graph for research logs.

    The index records only cross-log edges reached from recorded commands,
    reviewed validation dependencies, and explicit entry paths. Dormant scripts
    do not protect files in another log from orphan review.
    """

    started = time.monotonic()
    project_root = project_root.resolve()
    logs = _discover_repository_logs(project_root)
    input_paths = _repository_index_input_paths(project_root, logs)
    input_stats = _repository_input_stats(project_root, input_paths)
    log_inputs = {
        log["summary"]: [
            display_path(path, project_root)
            for path in _repository_log_input_paths(log)
        ]
        for log in logs
    }
    log_descriptors = [
        {
            "summary": log["summary"],
            "root": log["root"],
            "owned_roots": [
                display_path(root, project_root) for root in log["owned_roots"]
            ],
        }
        for log in logs
    ]
    expected_scripts = {
        display_path(script, project_root) for log in logs for script in log["scripts"]
    }
    if (
        isinstance(prior_index, dict)
        and prior_index.get("schema_version") == REPOSITORY_INDEX_SCHEMA_VERSION
        and prior_index.get("validation_rules_version") == rules_version
        and prior_index.get("input_stats") == input_stats
        and set(prior_index.get("script_dependencies", {})) == expected_scripts
        and prior_index.get("log_inputs") == log_inputs
        and prior_index.get("logs") == log_descriptors
        and isinstance(prior_index.get("direct_edges"), list)
        and set(prior_index.get("active_script_seeds", {})) == set(log_inputs)
    ):
        _assert_repository_inputs_stable(project_root, input_paths, input_stats)
        return prior_index, {
            "status": "unchanged",
            "logs": len(logs),
            "inputs": len(input_stats),
            "edges": len(prior_index.get("edges", [])),
            "scripts_parsed": 0,
            "logs_rebuilt": 0,
            "files_hashed": 0,
            "bytes_hashed": 0,
            "elapsed_seconds": time.monotonic() - started,
        }

    script_inventory = {
        script.resolve() for log in logs for script in log["scripts"]
    }
    script_identities = {
        display_path(script, project_root): script for script in script_inventory
    }
    prior_stats = (
        prior_index.get("input_stats", {}) if isinstance(prior_index, dict) else {}
    )
    prior_graph = (
        prior_index.get("script_dependencies", {})
        if isinstance(prior_index, dict)
        else {}
    )
    inventory_unchanged = set(prior_graph) == set(script_identities)
    graph = {}
    pending_scripts = []
    for identity, script in sorted(script_identities.items()):
        if (
            inventory_unchanged
            and input_stats.get(identity) == prior_stats.get(identity)
            and isinstance(prior_graph.get(identity), list)
        ):
            graph[script] = [
                _repository_identity_path(dependency, project_root)
                for dependency in prior_graph[identity]
            ]
            continue
        pending_scripts.append(script)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(32, (os.cpu_count() or 1) + 4)
    ) as executor:
        dependencies = executor.map(
            lambda script: _script_local_dependencies(script, script_inventory),
            pending_scripts,
        )
        graph.update(zip(pending_scripts, dependencies))
    scripts_parsed = len(pending_scripts)
    seeds: Dict[str, set[Path]] = {log["summary"]: set() for log in logs}
    edges: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
    direct_edges: Dict[Tuple[str, str, str, str, str], Dict[str, str]] = {}
    prior_log_inputs = (
        prior_index.get("log_inputs", {}) if isinstance(prior_index, dict) else {}
    )
    prior_direct_edges = (
        prior_index.get("direct_edges", []) if isinstance(prior_index, dict) else []
    )
    prior_seeds = (
        prior_index.get("active_script_seeds", {})
        if isinstance(prior_index, dict)
        else {}
    )
    topology_unchanged = (
        isinstance(prior_index, dict)
        and prior_index.get("logs") == log_descriptors
    )
    logs_rebuilt = 0

    def add_edge(
        consumer: Dict[str, Any],
        raw_path: Path,
        source: Path,
        kind: str,
        *,
        direct: bool = True,
    ) -> bool:
        owner = _repository_owner(raw_path, logs)
        if owner is None or owner["summary"] == consumer["summary"]:
            return False
        edge = {
            "owner": owner["summary"],
            "path": _repository_display_path(raw_path, project_root),
            "consumer": consumer["summary"],
            "source": display_path(source.resolve(), project_root),
            "kind": kind,
        }
        key = tuple(
            edge[field]
            for field in ("owner", "path", "consumer", "source", "kind")
        )
        edges[key] = edge
        if direct:
            direct_edges[key] = edge
        return True

    for log in logs:
        current_inputs = log_inputs[log["summary"]]
        log_unchanged = (
            topology_unchanged
            and prior_log_inputs.get(log["summary"]) == current_inputs
            and all(
                input_stats.get(identity) == prior_stats.get(identity)
                for identity in current_inputs
            )
            and isinstance(prior_seeds.get(log["summary"]), list)
        )
        if log_unchanged:
            for edge in prior_direct_edges:
                if edge.get("consumer") != log["summary"]:
                    continue
                key = tuple(
                    edge[field]
                    for field in ("owner", "path", "consumer", "source", "kind")
                )
                edges[key] = edge
                direct_edges[key] = edge
            seeds[log["summary"]].update(
                _repository_identity_path(identity, project_root)
                for identity in prior_seeds[log["summary"]]
            )
            continue
        logs_rebuilt += 1
        for entry in log["entries"]:
            parsed = parse_markdown(entry)
            commands = _commands(
                parsed, entry, project_root, set(log.get("scripts", []))
            )
            for command in commands:
                for raw_script in [
                    command.get("script"),
                    *command.get("matlab_scripts", []),
                ]:
                    if not raw_script:
                        continue
                    script = Path(raw_script).resolve()
                    if script in script_inventory:
                        seeds[log["summary"]].add(script)
                        add_edge(log, script, entry, "recorded-command")
                for argument in command.get("path_arguments", []):
                    add_edge(log, Path(argument["path"]), entry, "command-path")
                for token in command.get("data_tokens", []):
                    if token.get("path"):
                        add_edge(log, Path(token["path"]), entry, "data-token")
            for link in parsed.get("links", []):
                if link.get("path"):
                    add_edge(log, Path(link["path"]), entry, "entry-link")

            data_index = _data_index(entry)
            evidence_path = entry.parent / "evidence.csv"
            evidence = _entry_evidence_record(evidence_path)
            for row in evidence.get("rows", []):
                for specification in row.get("source_specs", []):
                    resolved = _resolve_evidence_source(
                        specification, entry, project_root, data_index
                    )
                    if resolved.get("path"):
                        add_edge(
                            log,
                            Path(resolved["path"]),
                            evidence_path,
                            "evidence-source",
                        )

        state_path = log["root_path"] / "validation-state.json"
        if not state_path.is_file():
            continue
        try:
            state = _load_json(state_path)
        except (OSError, ValidationToolError):
            continue
        for check in state.get("completed_checks", []):
            for dependency in check.get("dependencies", []):
                identity = dependency.get("path")
                if not isinstance(identity, str):
                    continue
                path = _repository_identity_path(identity, project_root)
                crosses_log = add_edge(
                    log, path, state_path, "validation-dependency"
                )
                retained_identity = state.get("files", {}).get(identity, {})
                if crosses_log:
                    for member in retained_identity.get("members", []):
                        add_edge(
                            log,
                            path / member,
                            state_path,
                            "validation-collection-member",
                        )
                if path in script_inventory:
                    seeds[log["summary"]].add(path)

    for log in logs:
        pending = list(seeds[log["summary"]])
        reached = set()
        while pending:
            script = pending.pop()
            if script in reached:
                continue
            reached.add(script)
            add_edge(log, script, script, "script-dependency", direct=False)
            for dependency in graph.get(script, []):
                add_edge(
                    log,
                    dependency,
                    script,
                    "script-dependency",
                    direct=False,
                )
                if dependency not in reached:
                    pending.append(dependency)

    prior_files = (
        prior_index.get("input_files", {}) if isinstance(prior_index, dict) else {}
    )
    input_files = {}
    files_hashed = 0
    bytes_hashed = 0
    for path in input_paths:
        identity = display_path(path, project_root)
        if (
            input_stats.get(identity) == prior_stats.get(identity)
            and isinstance(prior_files.get(identity), dict)
        ):
            input_files[identity] = prior_files[identity]
            continue
        current = file_identity(path)
        if {
            "size": current["size"],
            "mtime_ns": current["mtime_ns"],
        } != input_stats[identity]:
            raise FileChangedError(
                f"repository-index input changed during scan: {path}"
            )
        input_files[identity] = current
        files_hashed += 1
        bytes_hashed += current["size"]
    _assert_repository_inputs_stable(project_root, input_paths, input_stats)
    index = {
        "schema_version": REPOSITORY_INDEX_SCHEMA_VERSION,
        "validation_rules_version": rules_version,
        "input_fingerprint": _json_fingerprint(input_files),
        "input_stats": input_stats,
        "input_files": input_files,
        "logs": log_descriptors,
        "log_inputs": log_inputs,
        "active_script_seeds": {
            summary: [display_path(path, project_root) for path in sorted(paths)]
            for summary, paths in sorted(seeds.items())
        },
        "script_dependencies": {
            display_path(script, project_root): [
                display_path(dependency, project_root)
                for dependency in dependencies
            ]
            for script, dependencies in sorted(
                graph.items(), key=lambda item: item[0].as_posix()
            )
        },
        "direct_edges": sorted(
            direct_edges.values(),
            key=lambda item: tuple(
                item[field]
                for field in ("owner", "path", "consumer", "source", "kind")
            ),
        ),
        "edges": sorted(
            edges.values(),
            key=lambda item: tuple(
                item[field]
                for field in ("owner", "path", "consumer", "source", "kind")
            ),
        ),
    }
    return index, {
        "status": "rebuilt",
        "logs": len(logs),
        "inputs": len(input_stats),
        "scripts": len(script_inventory),
        "scripts_parsed": scripts_parsed,
        "logs_rebuilt": logs_rebuilt,
        "edges": len(index["edges"]),
        "files_hashed": files_hashed,
        "bytes_hashed": bytes_hashed,
        "elapsed_seconds": time.monotonic() - started,
    }


def _repository_dependencies(
    index: Dict[str, Any], summary_identity: str
) -> List[Dict[str, str]]:
    """Return the stable inbound dependency slice for one owning log."""

    return [
        edge for edge in index.get("edges", []) if edge.get("owner") == summary_identity
    ]


def _scan_input_fingerprint(scan: Dict[str, Any]) -> str:
    """Fingerprint the complete validation-relevant scan surface."""

    entries = []
    for entry in scan.get("entries", []):
        if "error" in entry:
            entries.append({"id": entry.get("id"), "error": entry["error"]})
            continue
        entries.append(
            {
                "id": entry["id"],
                "path": entry["path"],
                "section_errors": entry.get("section_errors", []),
                "presented_items": [
                    {
                        "kind": item["kind"],
                        "section": item["section"],
                        "selector": item["selector"],
                    }
                    for item in entry.get("presented_items", [])
                ],
                "candidate_targets": [
                    {
                        "identity": item["identity"],
                        "presented": item.get("presented", False),
                        "sections": item.get("sections", []),
                        "role_hints": item.get("role_hints", []),
                        "status": item.get("mechanical", {}).get("status"),
                    }
                    for item in entry.get("candidate_targets", [])
                ],
                "orphan_candidates": entry.get("orphan_candidates", []),
                "scope_kind": entry.get("scope_kind", "entry"),
                "validation_notes": entry.get("validation_notes", []),
                "evidence_errors": entry.get("evidence_record", {}).get(
                    "errors", []
                ),
            }
        )
    payload = {
        "summary": scan["summary"],
        "entry_order": scan["entry_order"],
        "reconciliation": scan["reconciliation"],
        "summary_items": [
            {
                "selector": item["selector"],
                "section": item["section"],
            }
            for item in scan.get("summary_items", [])
        ],
        "entries": entries,
        "evidence_record_errors": {
            "summary": scan.get("evidence_records", {})
            .get("summary", {})
            .get("errors", []),
            "entries": [
                {
                    "identity": item.get("identity") or item.get("expected_path"),
                    "errors": item.get("errors", []),
                }
                for item in scan.get("evidence_records", {}).get(
                    "entry_folders", []
                )
            ],
        },
        "files": scan.get("files", {}),
        "directory_memberships": scan.get("directory_memberships", {}),
        "script_inventory": scan.get("script_inventory", []),
        "script_dependency_graph": scan.get("script_dependency_graph", {}),
        "repository_dependencies": scan.get("repository_dependencies", []),
    }
    return _json_fingerprint(payload)


def _current_check_dependency_contract(
    scan: Dict[str, Any], check: Dict[str, Any]
) -> str:
    """Fingerprint the currently discovered dependency surface for one outcome."""

    entry_id = check.get("entry")
    target = check.get("target")
    check_name = check.get("check")
    payload: Dict[str, Any] = {
        "entry": entry_id,
        "target": target,
        "check": check_name,
        "dependencies": [],
    }
    if entry_id == "Summary":
        item = next(
            (
                item
                for item in scan.get("summary_items", [])
                if item.get("selector") == target
            ),
            None,
        )
        row = next(
            (
                row
                for row in scan.get("evidence_records", {})
                .get("summary", {})
                .get("rows", [])
                if row.get("statistic") == target
            ),
            None,
        )
        dependencies = [{"path": scan["summary"], "role": "summary"}]
        association = scan.get("evidence_records", {}).get("summary", {}).get(
            "identity"
        )
        if association:
            dependencies.append(
                {"path": association, "role": "evidence-association"}
            )
        if row:
            supporting = next(
                (
                    entry
                    for entry in scan.get("entries", [])
                    if entry.get("id") == row.get("entry") and "error" not in entry
                ),
                None,
            )
            if supporting:
                dependencies.append(
                    {"path": supporting["path"], "role": "supporting-entry"}
                )
        payload.update(
            {
                "item": (
                    {
                        "selector": item.get("selector"),
                        "section": item.get("section"),
                    }
                    if item
                    else None
                ),
                "association": row,
                "dependencies": dependencies,
            }
        )
        return _json_fingerprint(payload)

    entry = next(
        (
            entry
            for entry in scan.get("entries", [])
            if entry.get("id") == entry_id and "error" not in entry
        ),
        None,
    )
    if entry is None:
        payload["entry_missing"] = True
        return _json_fingerprint(payload)
    if target == ORPHAN_TARGET:
        payload["dependencies"] = [
            {"path": path, "role": "entry"}
            for path in entry.get("scope_paths", [entry["path"]])
        ]
        payload["orphan_candidates"] = entry.get("orphan_candidates", [])
        return _json_fingerprint(payload)

    dependencies = [{"path": entry["path"], "role": "entry"}]
    associations = []
    for row in entry.get("evidence_record", {}).get("rows", []):
        matched_sources = [
            {
                "identity": source.get("identity"),
                "locator": source.get("locator", ""),
                "status": source.get("status"),
            }
            for source in row.get("resolved_sources", [])
            if source.get("identity") == target
        ]
        if not matched_sources:
            continue
        associations.append(
            {
                "section": row.get("section"),
                "kind": row.get("kind"),
                "evidence": row.get("evidence"),
                "sources": matched_sources,
                "transformation": row.get("transformation", ""),
                "presented_item": row.get("presented_item"),
            }
        )
    target_present = bool(associations) or any(
        candidate.get("identity") == target
        for candidate in entry.get("candidate_targets", [])
    )
    if target_present:
        dependencies.append({"path": target, "role": "target"})
    association_identity = entry.get("evidence_record", {}).get("identity")
    if (
        check_name in {"Provenance", "Reproducibility"}
        and associations
        and association_identity
    ):
        dependencies.append(
            {"path": association_identity, "role": "evidence-association"}
        )
    workflow = None
    if check_name in {"Provenance", "Reproducibility"}:
        workflow, workflow_dependencies = _workflow_check(entry, target, scan)
        dependencies.extend(workflow_dependencies)
    payload.update(
        {
            "associations": (
                associations
                if check_name in {"Provenance", "Reproducibility"}
                else []
            ),
            "dependencies": sorted(
                (
                    {"path": item["path"], "role": item["role"]}
                    for item in {
                        (dependency["path"], dependency["role"]): dependency
                        for dependency in dependencies
                    }.values()
                ),
                key=lambda item: (item["path"], item["role"]),
            ),
            "target_present": target_present,
            "target_directory_membership": scan.get(
                "directory_memberships", {}
            ).get(target),
            "workflow": workflow,
        }
    )
    return _json_fingerprint(payload)


def _dependency_identity_snapshot(
    scan: Dict[str, Any], dependency: Dict[str, Any]
) -> Dict[str, Any]:
    """Identify one check dependency at its exact persisted member scope."""

    identity = dependency["path"]
    raw_path = scan.get("resolved_paths", {}).get(identity)
    if raw_path is None:
        candidate = Path(identity)
        raw_path = (
            candidate
            if candidate.is_absolute()
            else Path(scan["project_root"]) / candidate
        ).as_posix()
    path = Path(raw_path)
    if not path.exists():
        return {"missing": True}
    members = dependency.get("members")
    if members is None:
        members = dependency.get("identity", {}).get("members")
    if path.is_dir():
        if isinstance(members, list):
            return collection_identity(path, members)
        membership = scan.get("directory_memberships", {}).get(identity)
        if isinstance(membership, dict):
            return membership
        return directory_membership_identity(path)
    return scan.get("files", {}).get(identity) or _validation_file_identity(
        scan, identity, path
    )


def _compare_prior_state(
    scan: Dict[str, Any], prior_state: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare a completed validation state with the current scan."""

    if prior_state.get("validation_rules_version") != scan["validation_rules_version"]:
        prior_checks = prior_state.get("completed_checks")
        if not isinstance(prior_checks, list):
            prior_checks = prior_state.get("successful_checks", [])
        return {
            "status": "rules-changed",
            "reusable_checks": 0,
            "rerun_checks": len(prior_checks),
        }
    if prior_state.get("schema_version") != STATE_SCHEMA_VERSION:
        return {"status": "invalid", "detail": "unsupported state schema version"}
    if set(prior_state) != STATE_KEYS:
        return {"status": "invalid", "detail": "state keys do not match schema"}
    prior_files = prior_state.get("files")
    prior_checks = prior_state.get("completed_checks")
    prior_directories = prior_state.get("directory_memberships")
    prior_result = prior_state.get("result")
    if (
        not isinstance(prior_files, dict)
        or not isinstance(prior_checks, list)
        or not isinstance(prior_directories, dict)
        or not isinstance(prior_result, dict)
    ):
        return {
            "status": "invalid",
            "detail": "state inputs, completed checks, or result are malformed",
        }

    project_root = Path(scan["project_root"])
    comparisons: Dict[str, Dict[str, Any]] = {}
    for identity, previous in sorted(prior_files.items()):
        if not isinstance(previous, dict):
            comparisons[identity] = {
                "status": "requires-refresh",
                "detail": "invalid identity",
            }
            continue
        raw_path = scan["resolved_paths"].get(identity)
        if raw_path is None:
            candidate = Path(identity)
            raw_path = (
                candidate if candidate.is_absolute() else project_root / candidate
            ).as_posix()
            scan["resolved_paths"][identity] = raw_path
        path = Path(raw_path)
        try:
            if previous == {"missing": True}:
                current = {"missing": True} if not path.exists() else None
                comparisons[identity] = {
                    "status": "unchanged" if current == previous else "changed",
                    "current_identity": current,
                }
                continue
            if path.is_dir():
                members = previous.get("members")
                if not isinstance(members, list) or not members:
                    comparisons[identity] = {
                        "status": "requires-refresh",
                        "detail": "prior collection identity lacks selected members",
                    }
                    continue
                current = collection_identity(path, members)
            elif path.exists():
                current = scan["files"].get(identity) or _validation_file_identity(
                    scan, identity, path
                )
            else:
                comparisons[identity] = {"status": "missing"}
                continue
        except (OSError, ValidationToolError) as exc:
            comparisons[identity] = {"status": "error", "detail": str(exc)}
            continue
        comparisons[identity] = {
            "status": "unchanged" if current == previous else "changed",
            "current_identity": current,
        }

    current_directories = scan.get("directory_memberships", {})
    directory_comparisons = {}
    for identity in sorted(set(prior_directories) | set(current_directories)):
        previous = prior_directories.get(identity)
        current = current_directories.get(identity)
        if previous is None:
            status = "added"
        elif current is None:
            status = "removed"
        else:
            status = "unchanged" if current == previous else "changed"
        directory_comparisons[identity] = {
            "status": status,
            "current_identity": current,
        }

    checks = []
    reusable = 0
    input_unchanged = prior_state.get("input_fingerprint") == scan.get(
        "input_fingerprint"
    )

    def dependency_unchanged(identity: str) -> bool:
        directory_status = directory_comparisons.get(identity, {}).get("status")
        if identity in comparisons:
            return comparisons[identity]["status"] == "unchanged" and (
                directory_status in {None, "unchanged"}
            )
        return directory_status == "unchanged"

    snapshot_cache: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
    for check in prior_checks:
        dependencies = check.get("dependencies", []) if isinstance(check, dict) else []
        blockers = []
        stored_dependencies = []
        for dependency in dependencies:
            if not isinstance(dependency, dict) or not {
                "path",
                "role",
                "identity",
            } <= set(dependency):
                blockers.append("malformed-dependency-snapshot")
                continue
            path = dependency["path"]
            previous_identity = dependency["identity"]
            members = previous_identity.get("members", [])
            cache_key = (path, tuple(members) if isinstance(members, list) else ())
            try:
                current_identity = snapshot_cache.get(cache_key)
                if current_identity is None:
                    current_identity = _dependency_identity_snapshot(scan, dependency)
                    snapshot_cache[cache_key] = current_identity
            except (OSError, ValidationToolError):
                current_identity = None
            if current_identity != previous_identity:
                blockers.append(path)
            stored = {"path": path, "role": dependency["role"]}
            if isinstance(previous_identity.get("members"), list):
                stored["members"] = previous_identity["members"]
            stored_dependencies.append(stored)
        prior_contract = check.get("dependency_signature")
        current_contract = (
            _current_check_dependency_contract(scan, check)
            if isinstance(check, dict)
            else None
        )
        if prior_contract != current_contract:
            blockers.append("dependency-contract")
        blockers = list(dict.fromkeys(blockers))
        status = (
            "reusable"
            if not blockers and (dependencies or input_unchanged)
            else "rerun"
        )
        reusable += status == "reusable"
        checks.append(
            {
                "entry": check.get("entry"),
                "target": check.get("target"),
                "check": check.get("check"),
                "result": check.get("result"),
                "status": status,
                "changed_dependencies": blockers,
                "dependency_signature": current_contract,
                "resolution": check.get("resolution"),
                "findings": check.get("findings", []),
                "dependencies": stored_dependencies,
            }
        )
    report_path = project_root / scan["log_root"] / "validation.md"
    report_identity = prior_state.get("report")
    report_unchanged = False
    if isinstance(report_identity, dict) and report_path.is_file():
        try:
            report_unchanged = _content_identity(report_path) == report_identity
        except OSError:
            report_unchanged = False
    mode_compatible = (
        scan.get("requested_mode") == "standard"
        and prior_result.get("mode") == "standard"
    )
    outcomes_unchanged = (
        input_unchanged
        and mode_compatible
        and reusable == len(prior_checks)
        and all(
            item["status"] == "unchanged" for item in directory_comparisons.values()
        )
    )
    complete_unchanged = outcomes_unchanged and report_unchanged
    current_orphans = {
        entry["id"]: {
            "identities": sorted(
                item["identity"] for item in entry.get("orphan_candidates", [])
            ),
            "fingerprints": _orphan_item_fingerprints(entry, scan),
        }
        for entry in scan.get("entries", [])
        if "error" not in entry
    }
    orphan_dispositions = []
    for disposition in prior_state.get("orphan_dispositions", []):
        if (
            not isinstance(disposition, dict)
            or disposition.get("inventory_version") != ORPHAN_INVENTORY_VERSION
        ):
            continue
        entry_id = disposition.get("entry")
        current_scope = current_orphans.get(
            entry_id, {"identities": [], "fingerprints": {}}
        )
        current = set(current_scope["identities"])
        dependency_paths = [
            item.get("path") for item in disposition.get("dependencies", [])
        ]
        blockers = [
            path
            for path in dependency_paths
            if path not in comparisons or comparisons[path]["status"] != "unchanged"
        ]
        reusable_items = [
            {"identity": item["identity"], "decision": item["decision"]}
            for item in disposition.get("items", [])
            if isinstance(item, dict)
            and item.get("identity") in current
            and item.get("decision") in {"accepted", "unresolved"}
            and not blockers
            and item.get("fingerprint")
            == current_scope["fingerprints"].get(item.get("identity"))
        ]
        reusable_identities = {item["identity"] for item in reusable_items}
        orphan_dispositions.append(
            {
                "entry": entry_id,
                "inventory_version": ORPHAN_INVENTORY_VERSION,
                "items": reusable_items,
                "pending_candidates": sorted(current - reusable_identities),
                "status": (
                    "reusable"
                    if current and current == reusable_identities
                    else "partial"
                ),
                "changed_dependencies": blockers,
            }
        )
    return {
        "status": "unchanged" if complete_unchanged else "loaded",
        "files": comparisons,
        "directories": directory_comparisons,
        "checks": checks,
        "reusable_checks": reusable,
        "rerun_checks": len(checks) - reusable,
        "input_unchanged": input_unchanged,
        "report_unchanged": report_unchanged,
        "semantic_review_required": not outcomes_unchanged,
        "cached_result": prior_result if complete_unchanged else None,
        "orphan_dispositions": orphan_dispositions,
    }


def _orphan_item_fingerprints(
    entry: Dict[str, Any], scan: Dict[str, Any]
) -> Dict[str, str]:
    """Fingerprint the minimum material supporting each orphan disposition."""

    files = scan.get("files", {})
    directories = scan.get("directory_memberships", {})
    mechanics = scan.get("mechanical_checks", {})
    command_scripts = {}
    token_material: Dict[str, List[Any]] = {}
    for command in entry.get("commands", []):
        raw_script = command.get("script")
        if raw_script:
            identity = _identity_for_path(scan, raw_script)
            command_scripts[identity] = files.get(identity)
        for token in command.get("data_tokens", []):
            raw = token.get("path")
            if not raw:
                continue
            identity = _identity_for_path(scan, raw)
            token_material.setdefault(token["name"], []).append(
                {
                    "identity": identity,
                    "material": files.get(identity)
                    or directories.get(identity)
                    or mechanics.get(identity),
                }
            )
    data_rows = {
        row.get("name"): row for row in entry.get("data_index", {}).get("rows", [])
    }
    result = {}
    for candidate in entry.get("orphan_candidates", []):
        identity = candidate["identity"]
        token_name = (
            identity[1:-1]
            if identity.startswith("<") and identity.endswith(">")
            else None
        )
        result[identity] = _json_fingerprint(
            {
                "candidate": candidate,
                "material": files.get(identity)
                or directories.get(identity)
                or mechanics.get(identity),
                "data_row": data_rows.get(token_name),
                "token_material": token_material.get(token_name, []),
                "command_scripts": command_scripts,
                "validation_notes": entry.get("validation_notes", []),
            }
        )
    return result


def scan_log(
    summary_path: Path,
    jobs: int = 8,
    prior_state: Optional[Dict[str, Any]] = None,
    repository_index: Optional[Dict[str, Any]] = None,
    rules_version: str = RULES_VERSION,
    mode: str = "standard",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Scan a maintained research-log summary and its entries.

    Returns deterministic scan data plus non-persisted performance metrics. The
    scan identifies facts and candidates but never assigns semantic validation
    results.
    """

    if mode not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    started = time.monotonic()
    summary_path = summary_path.resolve()
    if not summary_path.is_file():
        raise ValidationToolError(f"summary does not exist: {summary_path}")
    project_root = find_project_root(summary_path)
    repository_index, repository_metrics = build_repository_dependency_index(
        project_root, repository_index, rules_version
    )
    summary_identity = display_path(summary_path, project_root)
    repository_dependencies = _repository_dependencies(
        repository_index, summary_identity
    )
    repository_dependency_paths = {
        _repository_identity_path(edge["path"], project_root)
        for edge in repository_dependencies
    }
    log_root = summary_path.with_suffix("")
    discovery = _discover_entries(summary_path, log_root)
    refs_path = log_root / "refs.bib"
    bib_keys = _bibtex_keys(refs_path)
    summary_evidence = _summary_evidence_record(log_root / "evidence.csv")
    summary_evidence["identity"] = (
        display_path(Path(summary_evidence["path"]), project_root)
        if summary_evidence["path"]
        else None
    )

    entries = []
    entry_sections: Dict[str, set[str]] = {}
    entry_section_types: Dict[str, Dict[str, List[str]]] = {}
    entry_evidence_records: Dict[Path, Dict[str, Any]] = {}
    folder_entry_ids: Dict[Path, set[str]] = {}
    for listed in discovery["listed"]:
        folder_entry_ids.setdefault(Path(listed["path"]).parent, set()).add(
            listed["id"]
        )
    files: Dict[str, Dict[str, Any]] = {}
    mechanics: Dict[str, Dict[str, Any]] = {}
    identity_paths = {summary_path}
    resolved_paths = {display_path(summary_path, project_root): summary_path.as_posix()}
    log_command_scripts = set(_script_inventory(log_root / "scripts"))
    if refs_path.is_file():
        identity_paths.add(refs_path)
        resolved_paths[display_path(refs_path, project_root)] = (
            refs_path.resolve().as_posix()
        )
    if summary_evidence["path"]:
        evidence_path = Path(summary_evidence["path"])
        identity_paths.add(evidence_path)
        resolved_paths[display_path(evidence_path, project_root)] = (
            evidence_path.resolve().as_posix()
        )
    for listed in discovery["listed"]:
        entry_path = Path(listed["path"])
        if not entry_path.is_file():
            entries.append({**listed, "error": "missing entry"})
            continue
        parsed = parse_markdown(entry_path)
        data_index = _data_index(entry_path)
        if entry_path.parent not in entry_evidence_records:
            entry_evidence_records[entry_path.parent] = _entry_evidence_record(
                entry_path.parent / "evidence.csv"
            )
            record = entry_evidence_records[entry_path.parent]
            record["identity"] = (
                display_path(Path(record["path"]), project_root)
                if record["path"]
                else None
            )
        evidence_record = entry_evidence_records[entry_path.parent]
        commands = _commands(
            parsed,
            entry_path,
            project_root,
            log_command_scripts
            | set(_script_inventory(entry_path.parent / "scripts")),
        )
        candidates = _merge_command_candidates(
            _candidate_references(parsed, entry_path, project_root),
            commands,
            project_root,
        )
        used_tokens = sorted(
            {
                result["name"]
                for command in commands
                for result in command.get("data_tokens", [])
                if result["name"] not in {"project", "log"}
            }
        )
        indexed_names = sorted(
            row.get("name", "") for row in data_index["rows"] if row.get("name")
        )
        data_index["used_tokens"] = used_tokens
        data_index["unused_names"] = sorted(set(indexed_names) - set(used_tokens))

        def experimental(item: Dict[str, Any]) -> bool:
            return item.get("section_type") == "experimental"

        section_errors = [
            section for section in parsed["sections"] if section["type"] == "invalid"
        ]
        evidence_rows = []
        presented_by_key = {
            (item["section"], item["kind"], item["selector"]): item
            for item in parsed["presented_items"]
        }
        for stored_row in evidence_record["rows"]:
            if stored_row["entry"] != listed["id"]:
                continue
            row = dict(stored_row)
            row["resolved_sources"] = [
                _resolve_evidence_source(source, entry_path, project_root, data_index)
                for source in row["source_specs"]
            ]
            row["presented_item"] = presented_by_key.get(
                (row["section"], row["kind"], row["evidence"])
            )
            evidence_rows.append(row)

        expected_evidence = {
            (item["section"], item["kind"], item["selector"])
            for item in parsed["presented_items"]
        }
        actual_evidence = {
            (row["section"], row["kind"], row["evidence"]) for row in evidence_rows
        }
        for section, kind, selector in sorted(expected_evidence - actual_evidence):
            evidence_record["errors"].append(
                f"{listed['id']}: missing {kind} association in {section!r}: "
                f"{selector!r}"
            )
        for section, kind, selector in sorted(actual_evidence - expected_evidence):
            evidence_record["errors"].append(
                f"{listed['id']}: extra {kind} association in {section!r}: "
                f"{selector!r}"
            )
        entry = {
            "id": listed["id"],
            "title": parsed["title"],
            "path": display_path(entry_path, project_root),
            "headings": parsed["headings"],
            "sections": parsed["sections"],
            "section_errors": section_errors,
            "links": [item for item in parsed["links"] if experimental(item)],
            "tables": [item for item in parsed["tables"] if experimental(item)],
            "fenced_blocks": [
                item for item in parsed["fenced_blocks"] if experimental(item)
            ],
            "numeric_evidence": [
                item for item in parsed["numeric_evidence"] if experimental(item)
            ],
            "presented_items": parsed["presented_items"],
            "validation_notes": parsed["validation_notes"],
            "citations": [item for item in parsed["citations"] if experimental(item)],
            "commands": commands,
            "data_index": data_index,
            "evidence_record": {
                "path": evidence_record["path"],
                "identity": evidence_record["identity"],
                "expected_path": evidence_record["expected_path"],
                "rows": evidence_rows,
                "errors": evidence_record["errors"],
            },
            "candidate_targets": candidates,
        }
        entries.append(entry)
        entry_sections[listed["id"]] = {
            heading["text"] for heading in parsed["headings"]
        }
        type_map: Dict[str, List[str]] = {}
        for section in parsed["sections"]:
            type_map.setdefault(section["section"], []).append(section["type"])
        entry_section_types[listed["id"]] = type_map
        identity_paths.add(entry_path)
        resolved_paths[entry["path"]] = entry_path.resolve().as_posix()
        if data_index.get("path"):
            index_path = Path(data_index["path"])
            identity_paths.add(index_path)
            resolved_paths[display_path(index_path, project_root)] = (
                index_path.resolve().as_posix()
            )
        if evidence_record["path"]:
            evidence_path = Path(evidence_record["path"])
            identity_paths.add(evidence_path)
            resolved_paths[display_path(evidence_path, project_root)] = (
                evidence_path.resolve().as_posix()
            )
        for row in evidence_rows:
            for source in row["resolved_sources"]:
                raw = source.get("path")
                if not raw or not Path(raw).exists():
                    continue
                source_path = Path(raw)
                resolved_paths[source["identity"]] = source_path.resolve().as_posix()
                if source_path.is_file():
                    identity_paths.add(source_path)
                elif source_path.is_dir():
                    mechanics[source["identity"]] = _inspect_structure(source_path)
        for candidate in candidates:
            raw = candidate.get("resolved_path")
            if raw and Path(raw).exists():
                candidate_path = Path(raw)
                resolved_paths[candidate["identity"]] = (
                    candidate_path.resolve().as_posix()
                )
                if candidate_path.is_file():
                    identity_paths.add(candidate_path)
                elif candidate_path.is_dir():
                    mechanics[candidate["identity"]] = _inspect_structure(
                        candidate_path
                    )
        for command in commands:
            raw_script = command.get("script")
            if raw_script and Path(raw_script).is_file():
                script_path = Path(raw_script)
                identity_paths.add(script_path)
                resolved_paths[display_path(script_path, project_root)] = (
                    script_path.resolve().as_posix()
                )
            for argument in command.get("path_arguments", []):
                if argument["role_hint"] in {"workspace", "dependency-container"}:
                    continue
                argument_path = Path(argument["path"])
                identity = display_path(argument_path, project_root)
                resolved_paths[identity] = argument_path.resolve().as_posix()
                if argument_path.is_file():
                    identity_paths.add(argument_path)
                elif argument_path.is_dir():
                    mechanics[identity] = _inspect_structure(argument_path)
            for token in command.get("data_tokens", []):
                raw = token.get("path")
                if raw and Path(raw).exists():
                    token_path = Path(raw)
                    resolved_paths[display_path(token_path, project_root)] = (
                        token_path.resolve().as_posix()
                    )
                    if token_path.is_file():
                        identity_paths.add(token_path)
                    elif token_path.is_dir():
                        mechanics[display_path(token_path, project_root)] = (
                            _inspect_structure(token_path)
                        )

    for folder, record in entry_evidence_records.items():
        valid_ids = folder_entry_ids.get(folder, set())
        for row in record["rows"]:
            line = row["line"]
            if row["entry"] not in valid_ids:
                record["errors"].append(
                    f"line {line}: entry {row['entry']!r} is not in this entry folder"
                )
                continue
            if row["section"] and row["section"] not in entry_sections.get(
                row["entry"], set()
            ):
                record["errors"].append(
                    f"line {line}: section {row['section']!r} does not exist in "
                    f"{row['entry']}"
                )
            elif entry_section_types.get(row["entry"], {}).get(row["section"]) != [
                "experimental"
            ]:
                record["errors"].append(
                    f"line {line}: section {row['section']!r} is not a unique "
                    "experimental section"
                )

    known_entries = set(entry_sections)
    for row in summary_evidence["rows"]:
        line = row["line"]
        if row["entry"] not in known_entries:
            summary_evidence["errors"].append(
                f"line {line}: unknown supporting entry {row['entry']!r}"
            )
            continue
        if row["section"] and row["section"] not in entry_sections[row["entry"]]:
            summary_evidence["errors"].append(
                f"line {line}: section {row['section']!r} does not exist in "
                f"{row['entry']}"
            )
        elif entry_section_types[row["entry"]].get(row["section"]) != ["experimental"]:
            summary_evidence["errors"].append(
                f"line {line}: supporting section {row['section']!r} is not a "
                "unique experimental section"
            )

    expected_summary = {
        item["selector"] for item in discovery["summary"]["summary_statistics"]
    }
    actual_summary = {row["statistic"] for row in summary_evidence["rows"]}
    for selector in sorted(expected_summary - actual_summary):
        summary_evidence["errors"].append(
            f"missing summary statistic association: {selector!r}"
        )
    for selector in sorted(actual_summary - expected_summary):
        summary_evidence["errors"].append(
            f"extra summary statistic association: {selector!r}"
        )

    bytes_hashed = 0
    files_hashed = 0
    cache_compatible = (
        isinstance(prior_state, dict)
        and prior_state.get("schema_version") == STATE_SCHEMA_VERSION
        and prior_state.get("validation_rules_version") == rules_version
        and set(prior_state) == STATE_KEYS
    )
    prior_input_files = (
        prior_state.get("input_files", {}) if cache_compatible else {}
    )
    prior_mechanical_checks = (
        prior_state.get("mechanical_checks", {}) if cache_compatible else {}
    )

    entry_paths = {
        Path(entry["path"]).resolve()
        for entry in discovery["listed"]
        if Path(entry["path"]).is_file()
    }

    def inspect(
        path: Path,
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any], Optional[int]]:
        key = display_path(path, project_root)
        if path.resolve() == summary_path:
            identity = summary_validation_identity(path)
            hashed = identity["size"]
        elif path.resolve() in entry_paths:
            identity = entry_validation_identity(path)
            hashed = identity["size"]
        else:
            previous = prior_input_files.get(key)
            before = path.stat() if path.is_file() and not path.is_symlink() else None
            if (
                before is not None
                and isinstance(previous, dict)
                and previous.get("size") == before.st_size
                and previous.get("mtime_ns") == before.st_mtime_ns
                and key in prior_mechanical_checks
            ):
                identity = previous
                structure = prior_mechanical_checks[key]
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise FileChangedError(
                        f"file changed during cached identity check: {path}"
                    )
                return key, identity, structure, None
            identity = file_identity(path)
            hashed = identity["size"]
        structure = _inspect_structure(path)
        return key, identity, structure, hashed

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = {
            executor.submit(inspect, path): path for path in sorted(identity_paths)
        }
        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                key, identity, structure, hashed = future.result()
            except (OSError, ValidationToolError) as exc:
                key = display_path(path, project_root)
                mechanics[key] = {"status": "fail", "detail": str(exc)}
                continue
            files[key] = identity
            mechanics[key] = structure
            if path.is_file() and hashed is not None:
                bytes_hashed += hashed
                files_hashed += 1

    for entry in entries:
        if "error" in entry:
            continue
        unresolved_citations = sorted(
            {
                citation["key"]
                for citation in entry["citations"]
                if citation["key"] not in bib_keys
            }
        )
        entry["unresolved_citations"] = unresolved_citations
        for candidate in entry["candidate_targets"]:
            identity = candidate["identity"]
            candidate["mechanical"] = mechanics.get(
                identity,
                {
                    "status": (
                        "unavailable" if candidate["kind"] == "external" else "missing"
                    )
                },
            )
        entry["orphan_candidates"] = []

    real_entries = [entry for entry in entries if "error" not in entry]
    entries_by_folder: Dict[Path, List[Dict[str, Any]]] = {}
    for entry in real_entries:
        entries_by_folder.setdefault(
            Path(resolved_paths[entry["path"]]).parent, []
        ).append(entry)

    scope_scripts: Dict[str, List[Path]] = {}
    scope_metadata: Dict[str, Dict[str, Any]] = {}
    script_inventory: set[Path] = set()
    for folder, folder_entries in entries_by_folder.items():
        scripts = _script_inventory(folder / "scripts")
        if not scripts:
            continue
        if len(folder_entries) == 1:
            scope_id = folder_entries[0]["id"]
        else:
            relative = display_path(folder, project_root)
            scope_id = f"Entry global — {relative}"
            scope_metadata[scope_id] = {
                "id": scope_id,
                "title": "Shared entry-folder research material",
                "path": relative,
                "scope_kind": "entry-global",
                "scope_paths": [entry["path"] for entry in folder_entries],
                "validation_notes": [
                    {**note, "entry": entry["id"]}
                    for entry in folder_entries
                    for note in entry.get("validation_notes", [])
                ],
            }
        scope_scripts.setdefault(scope_id, []).extend(scripts)
        script_inventory.update(scripts)

    log_scripts = _script_inventory(log_root / "scripts")
    if log_scripts:
        scope_id = "Log level"
        scope_scripts[scope_id] = log_scripts
        script_inventory.update(log_scripts)
        scope_metadata[scope_id] = {
            "id": scope_id,
            "title": "Log-level research material",
            "path": display_path(log_root, project_root),
            "scope_kind": "log-level",
            "scope_paths": [scan_path["path"] for scan_path in real_entries],
            "validation_notes": [
                {**note, "entry": entry["id"]}
                for entry in real_entries
                for note in entry.get("validation_notes", [])
            ],
        }

    for path in sorted(script_inventory):
        identity = display_path(path, project_root)
        resolved_paths[identity] = path.as_posix()
        try:
            identity, current, structure, hashed = inspect(path)
        except (OSError, ValidationToolError) as exc:
            mechanics[identity] = {"status": "fail", "detail": str(exc)}
            continue
        files[identity] = current
        mechanics[identity] = structure
        if hashed is not None:
            bytes_hashed += hashed
            files_hashed += 1

    reachable_identities = {
        source["identity"]
        for entry in real_entries
        for row in entry["evidence_record"]["rows"]
        for source in row["resolved_sources"]
    }
    reachable_identities.update(
        candidate["identity"]
        for entry in real_entries
        for candidate in entry["candidate_targets"]
        if candidate.get("presented")
    )
    (
        reachable_identities,
        used_identities,
        reachable_scripts,
        connected_tokens,
        script_dependency_graph,
    ) = _workflow_dependency_closure(
        real_entries,
        resolved_paths,
        project_root,
        reachable_identities,
        script_inventory,
    )
    recorded_script_seeds = {
        Path(raw_script).resolve()
        for entry in real_entries
        for command in entry.get("commands", [])
        for raw_script in [
            command.get("script"),
            *command.get("matlab_scripts", []),
        ]
        if raw_script and Path(raw_script).resolve() in script_inventory
    }
    recorded_scripts, script_dependency_graph = _reachable_script_dependencies(
        recorded_script_seeds,
        script_inventory,
        script_dependency_graph,
    )
    reachable_scripts.update(recorded_scripts)
    reachable_scripts.update(repository_dependency_paths & script_inventory)
    reachable_identities.update(
        display_path(path, project_root) for path in repository_dependency_paths
    )

    owned_roots = _log_owned_roots(log_root)

    def research_owned_artifact(candidate: Dict[str, Any]) -> bool:
        raw = candidate.get("resolved_path")
        if not raw:
            return False
        path = Path(raw).resolve()
        if path.name in {
            "data.csv",
            "evidence.csv",
            "validation.md",
            "validation-state.json",
            "validation-failures.md",
        }:
            return False
        return _path_is_log_owned(path, owned_roots)

    def used_across_logs(candidate: Dict[str, Any]) -> bool:
        """Return whether an inbound repository edge uses this file or subtree."""

        raw = candidate.get("resolved_path")
        if not raw:
            raw = resolved_paths.get(candidate.get("identity", ""))
        if not raw:
            return False
        candidate_path = Path(raw).resolve()
        for dependency in repository_dependency_paths:
            if dependency == candidate_path:
                return True
            if not candidate_path.is_dir():
                continue
            try:
                dependency.relative_to(candidate_path)
            except ValueError:
                continue
            return True
        return False

    for entry in real_entries:
        orphan_outputs = sorted(
            candidate["identity"]
            for candidate in entry["candidate_targets"]
            if candidate.get("kind") == "command-path"
            and "input" not in candidate.get("role_hints", [])
            and research_owned_artifact(candidate)
            and not _orphan_identity_is_used(
                candidate["identity"], used_identities, resolved_paths
            )
            and not used_across_logs(candidate)
            and candidate.get("mechanical", {}).get("status") != "missing"
        )
        orphan_references = sorted(
            f"<{name}>"
            for name in entry["data_index"].get("used_tokens", [])
            if (entry["id"], name) not in connected_tokens
        )
        entry["orphan_candidates"].extend(
            [
                *(
                    {"kind": "artifact", "identity": identity}
                    for identity in orphan_outputs
                ),
                *(
                    {"kind": "reference", "identity": identity}
                    for identity in orphan_references
                ),
            ]
        )

    orphan_scope_entries = []
    real_entries_by_id = {entry["id"]: entry for entry in real_entries}
    for scope_id, scripts in scope_scripts.items():
        unused_scripts = sorted(set(scripts) - reachable_scripts)
        candidates = [
            {"kind": "script", "identity": display_path(path, project_root)}
            for path in unused_scripts
        ]
        if not candidates:
            continue
        if scope_id in real_entries_by_id:
            real_entries_by_id[scope_id]["orphan_candidates"].extend(candidates)
            continue
        metadata = scope_metadata[scope_id]
        orphan_scope_entries.append(
            {
                **metadata,
                "headings": [],
                "sections": [],
                "section_errors": [],
                "links": [],
                "tables": [],
                "fenced_blocks": [],
                "numeric_evidence": [],
                "presented_items": [],
                "citations": [],
                "commands": [],
                "data_index": {"path": None, "rows": [], "used_tokens": []},
                "evidence_record": {
                    "path": None,
                    "identity": None,
                    "expected_path": None,
                    "rows": [],
                    "errors": [],
                },
                "candidate_targets": [],
                "orphan_candidates": candidates,
            }
        )
    entries.extend(orphan_scope_entries)

    directory_memberships = {}
    generated_records = {
        (log_root / name).resolve()
        for name in (
            "validation.md",
            "validation-state.json",
            "validation-failures.md",
        )
    }
    for identity, raw_path in sorted(resolved_paths.items()):
        path = Path(raw_path)
        if not path.is_dir() or path.resolve() == project_root:
            continue
        try:
            directory_memberships[identity] = directory_membership_identity(
                path, generated_records
            )
        except (OSError, ValidationToolError) as exc:
            directory_memberships[identity] = {
                "error": str(exc),
            }

    scan = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "validation_rules_version": rules_version,
        "requested_mode": mode,
        "summary": display_path(summary_path, project_root),
        "log_root": display_path(log_root, project_root),
        "project_root": project_root.as_posix(),
        "entry_order": [
            *[entry["id"] for entry in discovery["listed"]],
            *[entry["id"] for entry in orphan_scope_entries],
        ],
        "reconciliation": {
            "missing_entries": discovery["missing"],
            "unlisted_entries": [
                display_path(Path(path), project_root) for path in discovery["unlisted"]
            ],
        },
        "summary_items": discovery["summary"]["summary_statistics"],
        "entries": entries,
        "evidence_records": {
            "summary": summary_evidence,
            "entry_folders": [
                entry_evidence_records[path] for path in sorted(entry_evidence_records)
            ],
        },
        "bibtex": {
            "path": (
                display_path(refs_path, project_root) if refs_path.exists() else None
            ),
            "keys": bib_keys,
        },
        "files": dict(sorted(files.items())),
        "directory_memberships": dict(sorted(directory_memberships.items())),
        "resolved_paths": dict(sorted(resolved_paths.items())),
        "mechanical_checks": dict(sorted(mechanics.items())),
        "script_inventory": [
            display_path(path, project_root) for path in sorted(script_inventory)
        ],
        "script_dependency_graph": {
            display_path(path, project_root): [
                display_path(dependency, project_root)
                for dependency in dependencies
            ]
            for path, dependencies in sorted(
                script_dependency_graph.items(), key=lambda item: item[0].as_posix()
            )
            if dependencies
        },
        "repository_dependencies": repository_dependencies,
    }
    scan["input_fingerprint"] = _scan_input_fingerprint(scan)
    if prior_state is not None:
        scan["incremental"] = _compare_prior_state(scan, prior_state)
        scan["resolved_paths"] = dict(sorted(scan["resolved_paths"].items()))
    metrics = {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "entries": len(real_entries),
        "orphan_scopes": len(orphan_scope_entries),
        "summary_items": len(scan["summary_items"]),
        "candidate_targets": sum(
            len(entry.get("candidate_targets", [])) for entry in entries
        ),
        "tables": sum(len(entry.get("tables", [])) for entry in entries),
        "fenced_blocks": sum(len(entry.get("fenced_blocks", [])) for entry in entries),
        "numeric_evidence": sum(
            len(entry.get("numeric_evidence", [])) for entry in entries
        ),
        "evidence_rows": len(summary_evidence["rows"])
        + sum(len(record["rows"]) for record in entry_evidence_records.values()),
        "evidence_errors": len(summary_evidence["errors"])
        + sum(len(record["errors"]) for record in entry_evidence_records.values()),
        "section_errors": sum(
            len(entry.get("section_errors", [])) for entry in entries
        ),
        "experimental_sections": sum(
            sum(
                section["type"] == "experimental"
                for section in entry.get("sections", [])
            )
            for entry in entries
        ),
        "synthesis_sections": sum(
            sum(section["type"] == "synthesis" for section in entry.get("sections", []))
            for entry in entries
        ),
        "prose_sections": sum(
            sum(section["type"] == "prose" for section in entry.get("sections", []))
            for entry in entries
        ),
        "files_identified": len(files),
        "files_hashed": files_hashed,
        "bytes_hashed": bytes_hashed,
        "repository_index_status": repository_metrics["status"],
        "repository_index_edges": repository_metrics["edges"],
        "repository_dependencies": len(repository_dependencies),
    }
    if prior_state is not None:
        metrics["reusable_checks"] = scan["incremental"].get("reusable_checks", 0)
        metrics["rerun_checks"] = scan["incremental"].get("rerun_checks", 0)
        metrics["incremental_status"] = scan["incremental"].get("status")
        metrics["semantic_review_required"] = scan["incremental"].get(
            "semantic_review_required", True
        )
        if scan["incremental"].get("status") == "unchanged":
            metrics["cached_result"] = scan["incremental"].get("cached_result")
    return scan, metrics


# Artifact locator extraction and logical equivalence


def _displayed_number_specs(value: str) -> List[Tuple[float, int]]:
    """Return displayed numeric values and their least-significant place."""

    value = re.sub(r"(?<=[A-Za-zµμ°])\s*\^\s*[-+]?\d+", "", value)
    specs = []
    for token in NUMBER_RE.findall(value):
        cleaned = token.rstrip("%").replace(",", "")
        try:
            number = float(cleaned)
        except ValueError:
            continue
        parts = re.split(r"[eE]", cleaned, maxsplit=1)
        mantissa = parts[0]
        exponent = int(parts[1]) if len(parts) == 2 else 0
        decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
        specs.append((number, exponent - decimals))
    return specs


def _numeric_equivalent(
    presented: str, values: Sequence[Any], transformation: str = ""
) -> bool:
    """Compare displayed numbers with locator-selected retained values."""

    expected = _displayed_number_specs(presented)
    available = [
        number for value in values for number, _ in _displayed_number_specs(str(value))
    ]
    if not expected or not available:
        return False
    percent = "%" in presented
    kilo = bool(re.search(r"\d(?:\.\d+)?k(?:\b|$)", presented, re.IGNORECASE))
    lowered_transformation = transformation.lower()
    significant_figures = _significant_figures(lowered_transformation)
    binary_units = {
        "byte": 1.0,
        "bytes": 1.0,
        "kib": 1024.0,
        "mib": 1024.0**2,
        "gib": 1024.0**3,
        "tib": 1024.0**4,
    }
    binary_conversion = re.search(
        r"\b(bytes?|kib|mib|gib|tib)\s+to\s+(bytes?|kib|mib|gib|tib)\b",
        lowered_transformation,
    )
    for target, least_significant_place in expected:
        tolerance = 0.5 * 10**least_significant_place
        if significant_figures is not None and target:
            significant_place = (
                math.floor(math.log10(abs(target))) - significant_figures + 1
            )
            tolerance = 0.5 * 10**significant_place
        matched = False
        for source in available:
            candidates = [source]
            if percent:
                candidates.extend((source * 100.0, source / 100.0))
            if kilo:
                candidates.extend((source / 1000.0, source * 1000.0))
            if "hour" in lowered_transformation:
                candidates.extend((source / 3600.0, source * 3600.0))
            if "minute" in lowered_transformation:
                candidates.extend((source / 60.0, source * 60.0))
            if binary_conversion:
                source_unit, target_unit = binary_conversion.groups()
                candidates.append(
                    source
                    * binary_units[source_unit]
                    / binary_units[target_unit]
                )
            if any(
                abs(candidate - target) <= tolerance + 1e-12 for candidate in candidates
            ):
                matched = True
                break
        if not matched:
            return False
    return True


def _significant_figures(transformation: str) -> Optional[int]:
    """Return an explicitly stated significant-figure count, if present."""

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
    }
    match = re.search(
        r"\b(\d+|one|two|three|four|five|six)\s+significant\s+"
        r"(?:figure|figures|digit|digits)\b",
        transformation,
    )
    if not match:
        return None
    token = match.group(1)
    count = int(token) if token.isdigit() else words[token]
    return count if count > 0 else None


def _recursive_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _recursive_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _recursive_dicts(child)


def _json_path(value: Any, locator: str) -> Tuple[bool, Any]:
    """Resolve a JSON-style key path, including an explicit ``$`` root."""

    current = value
    expression = locator.strip()
    if expression.startswith("$"):
        expression = expression[1:]
        if expression.startswith("."):
            expression = expression[1:]
    if not expression:
        return True, current

    position = 0
    while position < len(expression):
        if expression[position] == ".":
            position += 1
            continue
        if expression[position] != "[":
            end = position
            while end < len(expression) and expression[end] not in ".[":
                end += 1
            key = expression[position:end]
            if not key or not isinstance(current, dict) or key not in current:
                return False, None
            current = current[key]
            position = end
        while position < len(expression) and expression[position] == "[":
            end = expression.find("]", position + 1)
            if end < 0 or not isinstance(current, list):
                return False, None
            selector = expression[position + 1 : end]
            if not isinstance(current, list):
                return False, None
            if ":" in selector:
                parts = selector.split(":")
                if len(parts) != 2:
                    return False, None
                try:
                    start = int(parts[0]) if parts[0] else None
                    stop = int(parts[1]) if parts[1] else None
                except ValueError:
                    return False, None
                current = current[slice(start, stop)]
            else:
                try:
                    index = int(selector)
                except ValueError:
                    return False, None
                if index >= len(current) or index < -len(current):
                    return False, None
                current = current[index]
            position = end + 1
    return True, current


def _locator_fields(assignments: Dict[str, str]) -> List[str]:
    value = assignments.get("field") or assignments.get("fields") or ""
    return [part.strip() for part in value.split("|") if part.strip()]


def _locator_filters(assignments: Dict[str, str]) -> Dict[str, set[str]]:
    reserved = {"field", "fields", "path", "property", "text"}
    filters: Dict[str, set[str]] = {}
    for key, value in assignments.items():
        if key.startswith("where."):
            filter_key = key.removeprefix("where.")
        elif key not in reserved:
            filter_key = key
        else:
            continue
        if not filter_key:
            continue
        filters[filter_key] = {
            part.strip() for part in value.split("|") if part.strip()
        }
    return filters


def _selected_property(value: Any, expression: str) -> Tuple[bool, Any, str]:
    """Return one closed-vocabulary structural property without evaluation."""

    if expression == "shape":
        shape = getattr(value, "shape", None)
        if shape is None:
            return False, None, "the selected object has no shape"
        return True, tuple(int(item) for item in shape), ""
    match = re.fullmatch(r"shape\[(\d+)\]", expression)
    if match:
        shape = getattr(value, "shape", None)
        index = int(match.group(1))
        if shape is None or index >= len(shape):
            return False, None, f"the selected object has no shape[{index}]"
        return True, int(shape[index]), ""
    if expression == "size":
        size = getattr(value, "size", None)
        if size is None:
            return False, None, "the selected object has no size"
        return True, int(size), ""
    return False, None, f"unsupported structured property {expression!r}"


def _plain_values(value: Any) -> Tuple[bool, List[Any], str]:
    """Convert one selected structured value into a bounded scalar list."""

    if isinstance(value, bytes):
        return True, [value.decode("utf-8", errors="replace")], ""
    if isinstance(value, dict):
        return False, [], "the locator selects a mapping rather than values"
    if hasattr(value, "shape") and hasattr(value, "size"):
        size = int(value.size)
        if size > LOCATOR_VALUE_LIMIT:
            return (
                False,
                [],
                f"the locator selects {size} values, above the bounded limit "
                f"of {LOCATOR_VALUE_LIMIT}",
            )
        try:
            value = value[()]
        except (IndexError, TypeError, ValueError):
            pass
        if hasattr(value, "tolist"):
            value = value.tolist()

    flattened: List[Any] = []

    def append(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            for child in item:
                append(child)
            return
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")
        if hasattr(item, "item"):
            try:
                item = item.item()
            except (TypeError, ValueError):
                pass
        flattened.append(item)

    append(value)
    if len(flattened) > LOCATOR_VALUE_LIMIT:
        return (
            False,
            [],
            f"the locator selects {len(flattened)} values, above the bounded "
            f"limit of {LOCATOR_VALUE_LIMIT}",
        )
    return True, flattened, ""


def _json_selected_values(
    selected: Any,
    fields: Sequence[str],
    filters: Dict[str, set[str]],
) -> Tuple[str, List[Any], str]:
    """Extract fields from a JSON record or record collection."""

    records = selected if isinstance(selected, list) else [selected]
    if not all(isinstance(record, dict) for record in records):
        return (
            "unresolved",
            [],
            "the structured path does not select records for field extraction",
        )

    kept = []
    for record in records:
        matches = True
        for key, allowed in filters.items():
            found, value = _json_path(record, key)
            if not found or str(value) not in allowed:
                matches = False
                break
        if matches:
            kept.append(record)
    if not kept:
        available = {}
        for key in filters:
            values = []
            for record in records:
                found, value = _json_path(record, key)
                if found:
                    values.append(str(value))
            available[key] = sorted(set(values))
        return (
            "fail",
            [],
            "the structured locator selects no retained records; available "
            f"filter values: {_locator_context_preview(available)}",
        )

    values: List[Any] = []
    missing = set()
    for record in kept:
        for field in fields:
            found, value = _json_path(record, field)
            if not found:
                missing.add(field)
                continue
            ok, extracted, reason = _plain_values(value)
            if not ok:
                return "unresolved", [], f"field {field!r}: {reason}"
            values.extend(extracted)
    if missing:
        return "unresolved", [], "missing locator fields: " + ", ".join(sorted(missing))
    return (
        "ok",
        values,
        f"selected {len(kept)} record(s), fields {list(fields)}; values: "
        + _bounded_preview(values),
    )


def _numpy_member(container: Any, expression: str) -> Tuple[bool, Any]:
    """Resolve one flat NPZ member with optional indexes or slices."""

    match = re.fullmatch(r"([^\[]+)((?:\[[^\]]+\])*)", expression)
    if not match or match.group(1) not in container:
        return False, None
    current = container[match.group(1)]
    for selector in re.findall(r"\[([^\]]+)\]", match.group(2)):
        try:
            if ":" in selector:
                parts = selector.split(":")
                if len(parts) != 2:
                    return False, None
                start = int(parts[0]) if parts[0] else None
                stop = int(parts[1]) if parts[1] else None
                current = current[slice(start, stop)]
            else:
                current = current[int(selector)]
        except (IndexError, TypeError, ValueError):
            return False, None
    return True, current


def _npz_locator_values(
    path: Path, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    try:
        import numpy as np
    except ImportError:
        return "unresolved", [], "NumPy is unavailable for NPZ locator extraction"

    path_locator = assignments.get("path")
    if not path_locator:
        return "unresolved", [], "the NPZ locator requires path="
    fields = _locator_fields(assignments)
    filters = _locator_filters(assignments)
    property_name = assignments.get("property", "")
    try:
        with np.load(path, allow_pickle=False) as artifact:
            if path_locator == "$":
                selected = artifact
            else:
                found, selected = _numpy_member(artifact, path_locator)
                if not found:
                    return "fail", [], "the structured path does not resolve"

            if not fields:
                if filters:
                    return (
                        "unresolved",
                        [],
                        "structured filters require field= or fields=",
                    )
                if property_name:
                    ok, selected, reason = _selected_property(selected, property_name)
                    if not ok:
                        return "unresolved", [], reason
                ok, values, reason = _plain_values(selected)
                if not ok:
                    return "unresolved", [], reason
                return "ok", values, f"{path_locator}={_bounded_preview(values)}"

            arrays = {}
            for name in {*fields, *filters}:
                if path_locator != "$":
                    return (
                        "unresolved",
                        [],
                        "NPZ field extraction currently requires path=$",
                    )
                found, value = _numpy_member(artifact, name)
                if not found:
                    return "unresolved", [], f"missing locator field: {name}"
                arrays[name] = value

            indexes = None
            for name, allowed in filters.items():
                array = arrays[name]
                if getattr(array, "ndim", 0) != 1:
                    return (
                        "unresolved",
                        [],
                        f"filter field {name!r} is not a one-dimensional array",
                    )
                matched = {
                    index for index, value in enumerate(array) if str(value) in allowed
                }
                indexes = matched if indexes is None else indexes & matched
            if indexes is not None and not indexes:
                return "fail", [], "the structured locator selects no aligned values"

            values = []
            for field in fields:
                value = arrays[field]
                if indexes is not None:
                    if getattr(value, "ndim", 0) == 0 or value.shape[0] != len(
                        arrays[next(iter(filters))]
                    ):
                        return (
                            "unresolved",
                            [],
                            f"field {field!r} is not aligned with the filter array",
                        )
                    value = value[sorted(indexes)]
                if property_name:
                    ok, value, reason = _selected_property(value, property_name)
                    if not ok:
                        return "unresolved", [], f"field {field!r}: {reason}"
                ok, extracted, reason = _plain_values(value)
                if not ok:
                    return "unresolved", [], f"field {field!r}: {reason}"
                values.extend(extracted)
            return (
                "ok",
                values,
                f"selected fields {fields}; values: {_bounded_preview(values)}",
            )
    except (OSError, ValueError, TypeError) as exc:
        return "fail", [], str(exc)


def _hdf5_locator_values(
    path: Path, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    try:
        import h5py
    except ImportError:
        return "unresolved", [], "h5py is unavailable for HDF5 locator extraction"

    path_locator = assignments.get("path")
    if not path_locator:
        return "unresolved", [], "the HDF5 locator requires path="
    fields = _locator_fields(assignments)
    filters = _locator_filters(assignments)
    property_name = assignments.get("property", "")
    if filters:
        return "unresolved", [], "HDF5 exact-match filters are not supported"
    try:
        with h5py.File(path, "r") as artifact:
            if path_locator == "$":
                selected = artifact
            elif path_locator in artifact:
                selected = artifact[path_locator]
            else:
                return "fail", [], "the structured path does not resolve"

            selected_items = []
            if fields:
                if not isinstance(selected, h5py.Group):
                    return (
                        "unresolved",
                        [],
                        "HDF5 fields require path= to select a group",
                    )
                for field in fields:
                    if field not in selected:
                        return "unresolved", [], f"missing locator field: {field}"
                    selected_items.append((field, selected[field]))
            else:
                selected_items.append((path_locator, selected))

            values = []
            details = []
            for name, value in selected_items:
                if property_name:
                    ok, value, reason = _selected_property(value, property_name)
                    if not ok:
                        return "unresolved", [], f"field {name!r}: {reason}"
                ok, extracted, reason = _plain_values(value)
                if not ok:
                    return "unresolved", [], f"field {name!r}: {reason}"
                values.extend(extracted)
                details.append(f"{name}={_bounded_preview(extracted)}")
            return "ok", values, "; ".join(details)
    except (OSError, ValueError, TypeError) as exc:
        return "fail", [], str(exc)


def _locator_values(path: Path, locator: str) -> Tuple[str, List[Any], str]:
    """Extract bounded values named by a durable evidence locator."""

    clauses = [part.strip() for part in locator.split(";") if part.strip()]
    assignments = {
        key.strip(): value.strip()
        for clause in clauses
        if "=" in clause
        for key, value in [clause.split("=", 1)]
    }
    bare = [clause for clause in clauses if "=" not in clause]
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter=delimiter))
            if not rows:
                return "fail", [], "the retained table has no data rows"
            columns = set(rows[0])
            if not locator:
                return (
                    "unresolved",
                    [],
                    "the whole retained table is declared; bounded context: "
                    + _locator_context_preview(
                        {"columns": list(rows[0]), "rows": rows}
                    ),
                )
            field_value = assignments.get("field") or assignments.get("fields")
            fields = (
                [part.strip() for part in field_value.split("|") if part.strip()]
                if field_value
                else []
            )
            if not fields and bare and all(field in columns for field in bare):
                fields = bare
            filters = _locator_filters(assignments)
            for key in filters:
                if key not in columns:
                    return "unresolved", [], f"locator field {key!r} is not a column"
            selected = [
                row
                for row in rows
                if all(
                    str(row.get(key, "")) in allowed for key, allowed in filters.items()
                )
            ]
            if not selected:
                available = {
                    key: sorted({str(row.get(key, "")) for row in rows})
                    for key in filters
                }
                return (
                    "fail",
                    [],
                    "the locator selects no retained table rows; available "
                    f"filter values: {_locator_context_preview(available)}",
                )
            if not fields:
                preview = _locator_context_preview(selected)
                return (
                    "unresolved",
                    [],
                    "the table locator does not name result fields; "
                    f"selected-row context: {preview}",
                )
            missing = [field for field in fields if field not in columns]
            if missing:
                return "unresolved", [], "missing locator fields: " + ", ".join(missing)
            values = [row[field] for row in selected for field in fields]
            return (
                "ok",
                values,
                f"selected {len(selected)} row(s), fields {fields}; "
                f"values: {_bounded_preview(values)}",
            )

        if suffix == ".json":
            content = json.loads(_read_text(path))
            if not locator:
                return (
                    "unresolved",
                    [],
                    "the whole retained JSON artifact is declared; bounded context: "
                    + _locator_context_preview(content),
                )
            path_locator = assignments.get("path")
            if path_locator:
                found, selected_value = _json_path(content, path_locator)
                if not found:
                    return "fail", [], "the structured path does not resolve"
                fields = _locator_fields(assignments)
                filters = _locator_filters(assignments)
                property_name = assignments.get("property", "")
                if not fields:
                    if filters:
                        return (
                            "unresolved",
                            [],
                            "structured filters require field= or fields=",
                        )
                    if property_name:
                        ok, selected_value, reason = _selected_property(
                            selected_value, property_name
                        )
                        if not ok:
                            return "unresolved", [], reason
                    if isinstance(selected_value, (dict, list)):
                        return (
                            "unresolved",
                            [],
                            "the structured path names a compound value; context: "
                            + _locator_context_preview(selected_value),
                        )
                    return (
                        "ok",
                        [selected_value],
                        f"{path_locator}={_bounded_preview(selected_value)}",
                    )
                return _json_selected_values(
                    selected_value,
                    fields,
                    filters,
                )
            if bare and not assignments:
                for locator_path in bare:
                    found, value = _json_path(content, locator_path)
                    if found:
                        if isinstance(value, (dict, list)):
                            return (
                                "unresolved",
                                [],
                                "the JSON locator names a compound value; "
                                f"context: {_locator_context_preview(value)}",
                            )
                        return (
                            "ok",
                            [value],
                            f"{locator_path}={_bounded_preview(value)}",
                        )
                return "fail", [], "the JSON locator does not resolve"
            fields = _locator_fields(assignments)
            filters = _locator_filters(assignments)
            selected = [
                record
                for record in _recursive_dicts(content)
                if all(
                    str(record.get(key, "")) in allowed
                    for key, allowed in filters.items()
                )
                and all(field in record for field in fields)
            ]
            if not fields:
                return (
                    "unresolved",
                    [],
                    "the JSON locator does not name result fields; "
                    f"record preview: {_bounded_preview(selected[:2])}",
                )
            if not selected:
                return "fail", [], "the JSON locator selects no retained records"
            selected_values = [record[field] for record in selected for field in fields]
            return (
                "ok",
                selected_values,
                f"selected {len(selected)} record(s), fields {fields}; values: "
                f"{_bounded_preview(selected_values)}",
            )

        if suffix == ".npz":
            if not locator:
                return (
                    "unresolved",
                    [],
                    "the whole retained NPZ artifact is declared; structure: "
                    + _locator_context_preview(_inspect_structure(path)),
                )
            return _npz_locator_values(path, assignments)

        if suffix in {".h5", ".hdf5"}:
            if not locator:
                return (
                    "unresolved",
                    [],
                    "the whole retained HDF5 artifact is declared; structure: "
                    + _locator_context_preview(_inspect_structure(path)),
                )
            return _hdf5_locator_values(path, assignments)

        if suffix == ".pkl":
            return (
                "unresolved",
                [],
                "pickle deserialization is prohibited; retain a CSV or JSON "
                "summary produced by an explicit command",
            )

        if suffix in {".txt", ".log", ".md"}:
            text = _read_text(path)
            fragment = assignments.get("text", locator)
            if not fragment:
                return (
                    "unresolved",
                    [],
                    "the whole retained text artifact is declared; bounded context: "
                    + _locator_context_preview(text.splitlines()),
                )
            matches = [line for line in text.splitlines() if fragment in line]
            if matches:
                return (
                    "ok",
                    matches,
                    f"matched {len(matches)} text line(s); values: "
                    + _bounded_preview(matches),
                )
            return "fail", [], "the text locator was not found"
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
        return "fail", [], str(exc)
    structure = _inspect_structure(path)
    declaration = "whole artifact" if not locator else f"locator {locator!r}"
    return (
        "unresolved",
        [],
        f"no deterministic locator reader for {suffix or 'file'}; {declaration}; "
        f"structure: {_bounded_preview(structure)}",
    )


def _bounded_preview(value: Any, limit: int = 320) -> str:
    """Return compact extracted context without turning scan JSON into a copy."""

    if isinstance(value, dict):
        value = dict(list(value.items())[:12])
    elif isinstance(value, list):
        value = value[:12]
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) > limit:
        return rendered[: limit - 3] + "..."
    return rendered


def _locator_context_preview(value: Any) -> str:
    """Return complete small-artifact context with a strict character bound."""

    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) > LOCATOR_CONTEXT_LIMIT:
        return rendered[: LOCATOR_CONTEXT_LIMIT - 3] + "..."
    return rendered


def _mechanical_evidence_support(
    row: Dict[str, Any], source: Dict[str, Any]
) -> Dict[str, str]:
    """Check one presented-item association when its locator is deterministic."""

    if source["status"] != "resolved" or not source.get("path"):
        return {
            "status": "fail",
            "detail": f"supporting source is {source['status']}: {source['source']}",
        }
    path = Path(source["path"])
    if row["kind"] == "table":
        if not row.get("presented_item"):
            return {
                "status": "unresolved",
                "detail": "unmatched table requires semantic review",
            }
        status, values, detail = _locator_values(path, source.get("locator", ""))
        if len(row.get("source_specs", [])) != 1:
            return {
                "status": "unresolved" if status == "ok" else status,
                "detail": f"multi-source table requires semantic review; {detail}",
            }
        if status != "ok":
            return {"status": status, "detail": detail}
        lines = row["presented_item"]["context"].splitlines()[2:]
        numeric_cells = [
            cell.strip()
            for line in lines
            for cell in line.strip().strip("|").split("|")
            if NUMBER_RE.search(cell)
        ]
        if not numeric_cells:
            return {
                "status": "unresolved",
                "detail": "the table has no mechanically comparable numeric cells",
            }
        if all(
            _numeric_equivalent(cell, values, row.get("transformation", ""))
            for cell in numeric_cells
        ):
            return {
                "status": "pass",
                "detail": f"all {len(numeric_cells)} numeric cells match ({detail})",
            }
        return {
            "status": "unresolved",
            "detail": "some table cells require semantic transformation review",
        }
    if row["kind"] == "output":
        try:
            retained = " ".join(_read_text(path).split())
        except (OSError, UnicodeError) as exc:
            return {"status": "fail", "detail": str(exc)}
        selector = re.sub(r" \[occurrence \d+\]$", "", row["evidence"])
        if " ".join(selector.split()) in retained:
            return {"status": "pass", "detail": "output selector occurs in source"}
        return {"status": "fail", "detail": "output selector was not found in source"}

    status, values, detail = _locator_values(path, source.get("locator", ""))
    if status != "ok":
        return {"status": status, "detail": detail}
    selector = re.sub(r" \[occurrence \d+\]$", "", row["evidence"])
    if _numeric_equivalent(selector, values, row.get("transformation", "")):
        return {"status": "pass", "detail": detail}
    return {
        "status": "fail",
        "detail": (
            "presented value is not equivalent to locator-selected values "
            f"({detail})"
        ),
    }


def _identity_for_path(scan: Dict[str, Any], raw: str) -> str:
    resolved = Path(raw).resolve().as_posix()
    cache = scan.get("_path_identity_cache")
    if cache is None:
        cache = {
            Path(path).resolve().as_posix(): identity
            for identity, path in scan["resolved_paths"].items()
        }
        scan["_path_identity_cache"] = cache
    if resolved in cache:
        return cache[resolved]
    return display_path(Path(raw), Path(scan["project_root"]))


def _workflow_check(
    entry: Dict[str, Any], target: str, scan: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Find commands that name one target as an exact resolved path value."""

    matches = []
    for command in entry.get("commands", []):
        for argument in command.get("path_arguments", []):
            matched = _identity_for_path(scan, argument["path"]) == target
            if matched:
                matches.append((command, argument))
                break
    if not matches:
        return (
            {
                "status": "unresolved",
                "detail": "no explicit producing command matched",
                "matched_commands": 0,
            },
            [],
        )

    confirmed = [
        pair for pair in matches if pair[1].get("role_hint") == "output"
    ]
    selected = confirmed or matches
    failures = []
    uncertainties = (
        []
        if confirmed
        else ["command/path direction requires semantic producer confirmation"]
    )
    dependencies = []
    for command, _matched_argument in selected:
        script = command.get("script")
        if script and Path(script).is_file():
            identity = _identity_for_path(scan, script)
            dependencies.append({"path": identity, "role": "producer"})
            structure = scan["mechanical_checks"].get(identity, {})
            if structure.get("status") != "ok":
                failures.append(
                    f"producer structure is {structure.get('status', 'unknown')}"
                )
        else:
            uncertainties.append("producer script is unresolved")
        if command.get("unknown_options"):
            uncertainties.append(
                "recorded command uses unknown options: "
                + ", ".join(command["unknown_options"])
            )
        for token in command.get("data_tokens", []):
            if token["name"] in {"project", "log"}:
                continue
            if token.get("status") != "resolved" or not token.get("path"):
                failures.append(f"input token <{token['name']}> is {token['status']}")
                continue
            identity = _identity_for_path(scan, token["path"])
            dependencies.append({"path": identity, "role": "input"})
            if not Path(token["path"]).exists():
                failures.append(f"input is missing: {identity}")
        for argument in command.get("path_arguments", []):
            if argument["role_hint"] != "input":
                continue
            identity = _identity_for_path(scan, argument["path"])
            dependencies.append({"path": identity, "role": "input"})
            if not Path(argument["path"]).exists():
                failures.append(f"input is missing: {identity}")
    unique_dependencies = [
        dict(item)
        for item in {
            (dependency["path"], dependency["role"]): dependency
            for dependency in dependencies
        }.values()
    ]
    if failures:
        return {
            "status": "fail",
            "detail": "; ".join(sorted(set(failures))),
            "matched_commands": len(selected),
        }, unique_dependencies
    if uncertainties:
        return (
            {
                "status": "unresolved",
                "detail": "; ".join(sorted(set(uncertainties))),
                "matched_commands": len(selected),
            },
            unique_dependencies,
        )
    return {
        "status": "pass",
        "detail": f"matched {len(selected)} recorded command(s)",
        "matched_commands": len(selected),
    }, unique_dependencies


def _reusable_checks(
    scan: Dict[str, Any],
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    result = {}
    for check in scan.get("incremental", {}).get("checks", []):
        if check.get("status") == "reusable":
            result[(check.get("entry"), check.get("target"), check.get("check"))] = {
                "result": check["result"],
                "resolution": check.get("resolution"),
                "findings": check.get("findings", []),
                "dependencies": check.get("dependencies", []),
            }
    return result


def _merge_reused_dependencies(
    dependencies: List[Dict[str, Any]], prior: Optional[Dict[str, Any]]
) -> None:
    """Restore the reviewed dependency closure of one reusable check."""

    if not prior:
        return
    for stored in prior.get("dependencies", []):
        matches = [
            dependency
            for dependency in dependencies
            if dependency.get("path") == stored.get("path")
            and dependency.get("role") == stored.get("role")
        ]
        if not matches:
            dependencies.append(copy.deepcopy(stored))
        elif isinstance(stored.get("members"), list):
            matches[0]["members"] = list(stored["members"])


def make_adjudication_template(
    scan: Dict[str, Any], date: str, rules_version: str, mode: str = "standard"
) -> Dict[str, Any]:
    """Prepare mechanical results and a bounded semantic-review queue."""

    if mode not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    if mode != scan.get("requested_mode", "standard"):
        raise ValidationToolError("prepare mode does not match the scanned mode")
    if scan.get("incremental", {}).get("status") == "unchanged":
        raise ValidationToolError(
            "unchanged standard validation is complete from cached state"
        )

    reusable = _reusable_checks(scan)
    reusable_orphans = {
        item.get("entry"): item
        for item in scan.get("incremental", {}).get("orphan_dispositions", [])
    }
    entries_by_id = {entry["id"]: entry for entry in scan["entries"]}
    summary_record = scan.get("evidence_records", {}).get("summary", {})
    summary_by_statistic = {
        row["statistic"]: row for row in summary_record.get("rows", [])
    }
    summary_rows = []
    review_queue = []
    for item in scan["summary_items"]:
        selector = item["selector"]
        row = summary_by_statistic.get(selector)
        dependencies = [{"path": scan["summary"], "role": "summary"}]
        if summary_record.get("identity"):
            dependencies.append(
                {
                    "path": summary_record["identity"],
                    "role": "evidence-association",
                }
            )
        entries = [row["entry"]] if row else []
        sections = [row["section"]] if row else []
        support_candidates = []
        if row and row["entry"] in entries_by_id:
            supporting_entry = entries_by_id[row["entry"]]
            dependencies.append(
                {"path": supporting_entry["path"], "role": "supporting-entry"}
            )
            section_candidates = [
                candidate
                for candidate in supporting_entry.get("presented_items", [])
                if candidate["section"] == row["section"]
            ]
            exact_candidates = [
                candidate
                for candidate in section_candidates
                if candidate["selector"] == selector
            ]
            transformation_tokens = [
                token
                for token in NUMBER_RE.findall(row.get("transformation", ""))
                if not _numeric_equivalent(selector, [token])
            ]
            transformation_candidates = [
                candidate
                for candidate in section_candidates
                if candidate.get("base_selector") in transformation_tokens
            ]
            support_candidates = exact_candidates or transformation_candidates or [
                candidate
                for candidate in section_candidates
                if selector in candidate["context"]
                or _numeric_equivalent(selector, [candidate["context"]])
            ]
            if not support_candidates:
                support_candidates = section_candidates
        target_key = selector
        prior = reusable.get(("Summary", target_key, "Provenance"))
        provenance = prior["result"] if prior else None
        findings = []
        support_evidence = []
        support_reviewed = False
        if prior and provenance == "FAIL":
            entries = []
            sections = []
            findings = [
                {"check": "Provenance", "finding": finding}
                for finding in prior.get("findings", [])
            ]
            support_reviewed = True
        elif prior and prior.get("resolution") and row:
            resolution = prior["resolution"]
            if (
                resolution.get("entry") == row["entry"]
                and resolution.get("section") == row["section"]
                and isinstance(resolution.get("lines"), str)
            ):
                match = re.fullmatch(r"(\d+)(?:-(\d+))?", resolution["lines"])
                supporting_entry = entries_by_id.get(row["entry"])
                if match and supporting_entry:
                    entry_path = Path(scan["resolved_paths"][supporting_entry["path"]])
                    entry_lines = _read_text(entry_path).splitlines()
                    start = int(match.group(1))
                    end = int(match.group(2) or start)
                    candidate = next(
                        (
                            item
                            for item in support_candidates
                            if item["line"] == start and item["end_line"] == end
                        ),
                        None,
                    )
                    if candidate is None and len(support_candidates) == 1:
                        candidate = support_candidates[0]
                        start = candidate["line"]
                        end = candidate["end_line"]
                    if candidate is not None and 1 <= start <= end <= len(entry_lines):
                        support_evidence = [
                            {
                                "entry": row["entry"],
                                "section": row["section"],
                                "lines": (
                                    str(start) if start == end else f"{start}-{end}"
                                ),
                                "text": " ".join(entry_lines[start - 1 : end]),
                            }
                        ]
                        support_reviewed = True
        if provenance and provenance != "FAIL" and not support_reviewed:
            provenance = None
        if row is None:
            provenance = "FAIL"
            findings.append(
                {
                    "check": "Provenance",
                    "finding": (
                        "No matching log-level evidence association was recorded."
                    ),
                }
            )
        elif provenance is None:
            review_queue.append(
                {
                    "entry": "Summary",
                    "kind": "semantic_provenance",
                    "identity": selector,
                    "section": item["section"],
                    "line": item["line"],
                    "reason": "confirm summary-to-entry logical equivalence",
                    "candidates": support_candidates,
                }
            )
        summary_rows.append(
            {
                "source_item": item["identity"],
                "item": selector,
                "entries": entries,
                "sections": sections,
                "provenance": provenance,
                "support_reviewed": support_reviewed,
                "support_evidence": support_evidence,
                "dependencies": dependencies,
                "findings": findings,
            }
        )

    entry_rows = []
    for entry in scan["entries"]:
        if "error" in entry:
            continue
        targets = []
        for issue in entry.get("section_errors", []):
            detail = "; ".join(issue["errors"])
            target = f"Invalid section structure (line {issue['line']})"
            targets.append(
                {
                    "target": target,
                    "sections": [issue["section"]],
                    "integrity": "FAIL",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": "-",
                    "dependencies": [{"path": entry["path"], "role": "entry"}],
                    "findings": [
                        {
                            "check": "Integrity",
                            "finding": f"Section classification failed: {detail}.",
                        },
                        {
                            "check": "Provenance",
                            "finding": (
                                "Validation skipped the structurally invalid section."
                            ),
                        },
                    ],
                }
            )
            review_queue.append(
                {
                    "entry": entry["id"],
                    "kind": "mechanical_failure",
                    "identity": target,
                    "reason": detail,
                }
            )

        rows_by_target: Dict[str, Dict[str, Any]] = {}
        for row in entry["evidence_record"]["rows"]:
            if not row.get("presented_item"):
                continue
            for source in row["resolved_sources"]:
                target = source["identity"]
                grouped = rows_by_target.setdefault(
                    target,
                    {"source": source, "associations": [], "sections": []},
                )
                grouped["associations"].append({"row": row, "source": source})
                if row["section"] not in grouped["sections"]:
                    grouped["sections"].append(row["section"])
        for candidate in entry["candidate_targets"]:
            if not candidate.get("presented"):
                continue
            grouped = rows_by_target.setdefault(
                candidate["identity"],
                {
                    "source": {
                        "identity": candidate["identity"],
                        "path": candidate.get("resolved_path"),
                        "status": (
                            "resolved"
                            if candidate.get("resolved_path")
                            and Path(candidate["resolved_path"]).exists()
                            else "missing"
                        ),
                        "source": candidate["identity"],
                        "locator": "",
                    },
                    "associations": [],
                    "sections": [],
                },
            )
            for section in candidate["sections"]:
                if section not in grouped["sections"]:
                    grouped["sections"].append(section)

        recorded_keys = {
            (row["section"], row["kind"], row["evidence"])
            for row in entry["evidence_record"]["rows"]
        }
        for item in entry.get("presented_items", []):
            key = (item["section"], item["kind"], item["selector"])
            if key in recorded_keys:
                continue
            target = f"Unprovenanced: {item['selector']}"
            findings = [
                {
                    "check": "Integrity",
                    "finding": "No retained supporting artifact was identified.",
                },
                {
                    "check": "Provenance",
                    "finding": "No matching evidence association was recorded.",
                },
            ]
            targets.append(
                {
                    "target": target,
                    "sections": [item["section"]],
                    "integrity": "FAIL",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": "-",
                    "dependencies": [{"path": entry["path"], "role": "entry"}],
                    "findings": findings,
                }
            )
            review_queue.append(
                {
                    "entry": entry["id"],
                    "kind": "mechanical_failure",
                    "identity": target,
                    "reason": findings[1]["finding"],
                }
            )

        for target, grouped in rows_by_target.items():
            source = grouped["source"]
            dependencies = [{"path": entry["path"], "role": "entry"}]
            if source.get("path"):
                dependencies.append({"path": target, "role": "target"})
            if entry["evidence_record"].get("identity") and grouped["associations"]:
                dependencies.append(
                    {
                        "path": entry["evidence_record"]["identity"],
                        "role": "evidence-association",
                    }
                )
            workflow, workflow_dependencies = _workflow_check(entry, target, scan)
            prior_provenance = reusable.get((entry["id"], target, "Provenance"))
            if prior_provenance is None:
                dependencies.extend(workflow_dependencies)
            dependencies = [
                dict(value)
                for value in {
                    (item["path"], item["role"]): item for item in dependencies
                }.values()
            ]

            prior_integrity = reusable.get((entry["id"], target, "Integrity"))
            integrity = prior_integrity["result"] if prior_integrity else None
            if integrity is None:
                structure = scan["mechanical_checks"].get(target, {})
                if source["status"] != "resolved":
                    integrity = "FAIL"
                    integrity_detail = f"supporting artifact is {source['status']}"
                elif (
                    structure.get("status") == "ok"
                    and structure.get("type") != "directory"
                ):
                    integrity = date
                    integrity_detail = "type-appropriate structural check passed"
                elif structure.get("status") == "fail":
                    integrity = "FAIL"
                    integrity_detail = (
                        structure.get("detail") or "structural check failed"
                    )
                else:
                    integrity_detail = "custom or collection structure requires review"
            else:
                integrity_detail = "reused from unchanged validation state"

            provenance = prior_provenance["result"] if prior_provenance else None
            support_results = [
                _mechanical_evidence_support(item["row"], item["source"])
                for item in grouped["associations"]
            ]
            if provenance is None:
                statuses = {result["status"] for result in support_results}
                if workflow["status"] == "fail" or "fail" in statuses:
                    provenance = "FAIL"
                elif workflow["status"] == "pass" and statuses <= {"pass"}:
                    provenance = date
            findings = []
            if integrity == "FAIL":
                details = (
                    prior_integrity.get("findings", [])
                    if prior_integrity
                    else [integrity_detail]
                )
                findings.extend(
                    {"check": "Integrity", "finding": finding}
                    for finding in details
                )
            if provenance == "FAIL":
                if prior_provenance:
                    details = prior_provenance.get("findings", [])
                else:
                    details = [workflow["detail"]]
                    details.extend(
                        result["detail"]
                        for result in support_results
                        if result["status"] == "fail"
                    )
                    details = ["; ".join(dict.fromkeys(details))]
                findings.extend(
                    {"check": "Provenance", "finding": finding}
                    for finding in details
                )
            needs_review = (
                integrity is None
                or provenance is None
                or (integrity == "FAIL" and prior_integrity is None)
                or (provenance == "FAIL" and prior_provenance is None)
            )
            if needs_review:
                review_queue.append(
                    {
                        "entry": entry["id"],
                        "kind": (
                            "mechanical_failure"
                            if "FAIL" in {integrity, provenance}
                            else "semantic_fallback"
                        ),
                        "identity": target,
                        "sections": grouped["sections"],
                        "integrity": integrity_detail,
                        "integrity_status": (
                            "pass"
                            if _is_success_date(integrity)
                            else "fail"
                            if integrity == "FAIL"
                            else "unresolved"
                        ),
                        "workflow": workflow,
                        "evidence": [
                            {
                                "kind": row["kind"],
                                "selector": row["evidence"],
                                "context": (
                                    (row.get("presented_item") or {}).get(
                                        "context", ""
                                    )
                                ),
                                "locator": association_source.get("locator", ""),
                                "result": result,
                                "transformation": row["transformation"],
                            }
                            for item, result in zip(
                                grouped["associations"], support_results
                            )
                            for row, association_source in [
                                (item["row"], item["source"])
                            ]
                        ],
                    }
                )
            prior_reproduction = reusable.get(
                (entry["id"], target, "Reproducibility")
            )
            if prior_reproduction and mode == "standard":
                reproducibility = prior_reproduction["result"]
            elif mode == "reproduction" and workflow.get("matched_commands", 0):
                reproducibility = None
                review_queue.append(
                    {
                        "entry": entry["id"],
                        "kind": "reproduction",
                        "identity": target,
                        "sections": grouped["sections"],
                        "reason": (
                            "run the recorded invocation into temporary outputs and "
                            "compare this target with retained evidence"
                        ),
                    }
                )
            elif mode == "reproduction":
                reproducibility = "N/A"
            else:
                reproducibility = "-"
            _merge_reused_dependencies(dependencies, prior_integrity)
            _merge_reused_dependencies(dependencies, prior_provenance)
            _merge_reused_dependencies(dependencies, prior_reproduction)
            targets.append(
                {
                    "target": target,
                    "sections": grouped["sections"],
                    "integrity": integrity,
                    "provenance": provenance,
                    "reproducibility": reproducibility,
                    "notes": "-",
                    "dependencies": dependencies,
                    "findings": findings,
                }
            )

        orphan_candidates = entry.get("orphan_candidates", [])
        prior_orphan = reusable_orphans.get(entry["id"], {})
        prior_items = {
            item["identity"]: dict(item)
            for item in prior_orphan.get("items", [])
            if isinstance(item, dict) and item.get("identity")
        }
        current_identities = [item["identity"] for item in orphan_candidates]
        orphan_items = [
            prior_items.get(
                identity,
                {"identity": identity, "decision": "pending"},
            )
            for identity in current_identities
        ]
        unresolved = [
            item["identity"]
            for item in orphan_items
            if item["decision"] == "unresolved"
        ]
        pending_orphans = [
            candidate
            for candidate, item in zip(orphan_candidates, orphan_items)
            if item["decision"] == "pending"
        ]
        reportable = [*unresolved, *[item["identity"] for item in pending_orphans]]
        if reportable:
            count = len(reportable)
            dependencies = [
                {"path": path, "role": "entry"}
                for path in entry.get("scope_paths", [entry["path"]])
            ]
            targets.append(
                {
                    "target": ORPHAN_TARGET,
                    "sections": ["-"],
                    "integrity": "N/A",
                    "provenance": "FAIL",
                    "reproducibility": "N/A",
                    "notes": (
                        f"{count} unresolved "
                        f"{'item' if count == 1 else 'items'}"
                    ),
                    "dependencies": dependencies,
                    "findings": [
                        {
                            "check": "Provenance",
                            "finding": f"Unresolved orphan candidate: {identity}",
                        }
                        for identity in reportable
                    ],
                    "orphan_items": orphan_items,
                }
            )
        if pending_orphans:
            review_queue.append(
                {
                    "entry": entry["id"],
                    "kind": "orphan_candidates",
                    "identity": ORPHAN_TARGET,
                    "candidates": pending_orphans,
                    "validation_notes": entry.get("validation_notes", []),
                    "reason": (
                        "classify each candidate as unresolved or accepted because "
                        "it is connected to presented evidence or covered by a "
                        "pre-existing Validation note"
                    ),
                }
            )

        for target_row in targets:
            reused_for_row = set()
            for check, field in (
                ("Integrity", "integrity"),
                ("Provenance", "provenance"),
                ("Reproducibility", "reproducibility"),
            ):
                prior = reusable.get((entry["id"], target_row["target"], check))
                if target_row["target"] == ORPHAN_TARGET:
                    prior = None
                if prior is None or (
                    mode == "reproduction" and check == "Reproducibility"
                ):
                    continue
                target_row[field] = prior["result"]
                target_row["findings"] = [
                    finding
                    for finding in target_row.get("findings", [])
                    if finding.get("check") != check
                ]
                target_row["findings"].extend(
                    {"check": check, "finding": finding}
                    for finding in prior.get("findings", [])
                )
                _merge_reused_dependencies(target_row["dependencies"], prior)
                reused_for_row.add(check)
            if reused_for_row:
                review_queue = [
                    item
                    for item in review_queue
                    if not (
                        item.get("entry") == entry["id"]
                        and item.get("identity") == target_row["target"]
                        and item.get("kind") == "mechanical_failure"
                        and all(
                            target_row[field] not in {None, "FAIL"}
                            or check in reused_for_row
                            for check, field in (
                                ("Integrity", "integrity"),
                                ("Provenance", "provenance"),
                            )
                        )
                    )
                ]

        entry_rows.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "path": entry["path"],
                "scope_reconciled": True,
                "targets": targets,
                "scope_kind": entry.get("scope_kind", "entry"),
                "scope_paths": entry.get("scope_paths", [entry["path"]]),
                "orphan_items": orphan_items,
            }
        )

    checks = scan.get("mechanical_checks", {})
    for entry in entry_rows:
        for row in entry["targets"]:
            collections = [
                dependency["path"]
                for dependency in row.get("dependencies", [])
                if checks.get(dependency["path"], {}).get("type") == "directory"
                and not dependency.get("members")
            ]
            if not collections or not any(
                _is_success_date(row.get(check))
                for check in ("integrity", "provenance")
            ):
                continue
            queued = next(
                (
                    item
                    for item in review_queue
                    if item.get("entry") == entry["id"]
                    and item.get("identity") == row["target"]
                    and item.get("kind")
                    not in {
                        "orphan_candidates",
                        "evidence_record_error",
                        "reproduction",
                    }
                ),
                None,
            )
            if queued is not None:
                queued["collections"] = collections
                continue
            review_queue.append(
                {
                    "entry": entry["id"],
                    "kind": "collection_scope",
                    "identity": row["target"],
                    "sections": row["sections"],
                    "collections": collections,
                    "reason": (
                        "select the material relative members for each directory "
                        "dependency before retaining a dated result"
                    ),
                }
            )

    return {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "validation_rules_version": rules_version,
        "log": scan["summary"],
        "requested_scope": "complete standard scope",
        "scope": {
            "summary": True,
            "entries": list(scan["entry_order"]),
        },
        "date": date,
        "mode": mode,
        "summary": summary_rows,
        "entries": entry_rows,
        "review_queue": review_queue,
    }


# Report, state, failure-record, and summary projection generation


def _result(value: Any, field: str) -> str:
    if value in RESULT_VALUES or _is_success_date(value):
        return value
    raise ValidationToolError(f"{field} must be a date, FAIL, -, or N/A; got {value!r}")


def _is_success_date(value: Any) -> bool:
    if not isinstance(value, str) or not SUCCESS_DATE_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _checked_result(value: Any, field: str) -> str:
    result = _result(value, field)
    if result in {"-", "N/A"}:
        raise ValidationToolError(f"{field} must be a successful date or FAIL")
    return result


def _code_result(value: str) -> str:
    return f"`{value}`" if value in RESULT_VALUES else value


def _cell(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    return str(value).replace("\n", " ").replace("|", "\\|")


def _dependencies(row: Dict[str, Any], check: str) -> List[Dict[str, Any]]:
    by_check = row.get("dependencies_by_check") or {}
    dependencies = by_check.get(check, row.get("dependencies", []))
    if check == "Integrity" and check not in by_check:
        dependencies = [
            item
            for item in dependencies
            if item.get("role") in {"entry", "target", "collection-member"}
        ]
    result = []
    for item in dependencies:
        dependency = {"path": item["path"], "role": item["role"]}
        if "members" in item:
            members = item["members"]
            if not isinstance(members, list) or not all(
                isinstance(member, str) for member in members
            ):
                raise ValidationToolError(
                    "dependency members must be a list of relative paths"
                )
            dependency["members"] = members
        result.append(dependency)
    return result


def _materialize_identities(
    scan: Dict[str, Any], completed_checks: Sequence[Dict[str, Any]]
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Materialize identities for dependencies supporting completed outcomes."""

    dependency_specs: Dict[str, Dict[str, Any]] = {}
    for check in completed_checks:
        for dependency in check["dependencies"]:
            path = dependency["path"]
            spec = dependency_specs.setdefault(
                path,
                {
                    "members": set(),
                    "member_scope_given": False,
                    "successful": False,
                },
            )
            spec["successful"] = spec["successful"] or _is_success_date(
                check["result"]
            )
            if "members" in dependency:
                spec["member_scope_given"] = True
                spec["members"].update(dependency["members"])

    scan_files = scan.get("files", {})
    resolved_paths = scan.get("resolved_paths", {})
    identities: Dict[str, Dict[str, Any]] = {}
    for path, spec in sorted(dependency_specs.items()):
        raw_path = resolved_paths.get(path)
        if raw_path is None:
            candidate = Path(path)
            raw_path = (
                candidate
                if candidate.is_absolute()
                else Path(scan["project_root"]) / candidate
            ).as_posix()
        resolved = Path(raw_path)
        if not resolved.exists():
            current = {"missing": True}
        elif resolved.is_dir():
            if not spec["member_scope_given"]:
                if spec["successful"]:
                    raise ValidationToolError(
                        "successful collection dependency requires explicit members: "
                        f"{path}"
                    )
                continue
            current = collection_identity(resolved, sorted(spec["members"]))
        else:
            current = _validation_file_identity(scan, path, resolved)
        baseline = scan_files.get(path)
        same_scope = (
            not resolved.is_dir()
            or baseline is None
            or (baseline.get("members") == current.get("members"))
        )
        if baseline is not None and same_scope and current != baseline:
            raise FileChangedError(f"dependency changed after scan: {path}")
        identities[path] = current
    snapshot_cache: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
    stored_checks = []
    for check in completed_checks:
        stored_dependencies = []
        for dependency in check["dependencies"]:
            members = dependency.get("members", [])
            cache_key = (
                dependency["path"],
                tuple(members) if isinstance(members, list) else (),
            )
            current = snapshot_cache.get(cache_key)
            if current is None:
                current = _dependency_identity_snapshot(scan, dependency)
                snapshot_cache[cache_key] = current
            stored_dependencies.append(
                {
                    "path": dependency["path"],
                    "role": dependency["role"],
                    "identity": current,
                }
            )
        stored_checks.append(
            {
                **check,
                "dependencies": stored_dependencies,
                "dependency_signature": _current_check_dependency_contract(
                    scan, check
                ),
            }
        )
    return identities, stored_checks


def _finding_map(row: Dict[str, Any]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for finding in row.get("findings", []):
        result.setdefault(finding["check"], []).append(finding["finding"])
    return result


def _validate_failed_findings(
    row: Dict[str, Any], checks: Iterable[str], identity: str
) -> None:
    findings = _finding_map(row)
    for check in checks:
        if row[check.lower()] == "FAIL" and not findings.get(check):
            raise ValidationToolError(f"missing {check} finding for {identity}")


def _validate_summary_support(
    row: Dict[str, Any], scan_entries: Dict[str, Dict[str, Any]], scan: Dict[str, Any]
) -> None:
    support = row.get("support_evidence")
    if not isinstance(support, list) or not support:
        raise ValidationToolError(
            f"successful Summary row lacks exact support evidence: {row['item']}"
        )
    for evidence in support:
        if set(evidence) != {"entry", "section", "lines", "text"}:
            raise ValidationToolError("Summary support evidence has incorrect keys")
        if evidence["entry"] not in row.get("entries", []):
            raise ValidationToolError(
                "Summary support evidence names an unlisted entry"
            )
        entry = scan_entries.get(evidence["entry"])
        if entry is None or evidence["section"] not in row.get("sections", []):
            raise ValidationToolError(
                "Summary support evidence names an unlisted section"
            )
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", str(evidence["lines"]))
        if not match:
            raise ValidationToolError(
                "Summary support evidence has an invalid line range"
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        path = Path(scan["resolved_paths"][entry["path"]])
        lines = _read_text(path).splitlines()
        if start < 1 or end < start or end > len(lines):
            raise ValidationToolError(
                "Summary support evidence line range is outside the entry"
            )
        sections = _section_ranges(lines)
        if sections[start - 1]["section"] != evidence["section"]:
            raise ValidationToolError(
                "Summary support evidence section does not match its line"
            )
        if sections[start - 1]["section_type"] != "experimental":
            raise ValidationToolError(
                "Summary support evidence must come from an experimental section"
            )
        excerpt = " ".join(line.strip() for line in lines[start - 1 : end])
        normalized_excerpt = " ".join(excerpt.split())
        normalized_evidence = " ".join(str(evidence["text"]).split())
        if not normalized_evidence or normalized_evidence not in normalized_excerpt:
            raise ValidationToolError(
                "Summary support evidence text does not match its entry lines"
            )


def _mechanically_locked_outcomes(
    prepared: Dict[str, Any],
) -> set[Tuple[str, str, str]]:
    """Return successful outcomes that the fresh scan did not queue for review."""

    queued = {
        (item.get("entry"), item.get("identity"))
        for item in prepared.get("review_queue", [])
    }
    locked = set()
    for entry in prepared.get("entries", []):
        for row in entry.get("targets", []):
            if (entry["id"], row["target"]) in queued:
                continue
            for check in ("Integrity", "Provenance"):
                if _is_success_date(row.get(check.lower())):
                    locked.add((entry["id"], row["target"], check))
    return locked


def _semantic_failure_bases(item: Dict[str, Any]) -> set[str]:
    """Return unresolved components that may support a semantic FAIL."""

    bases = set()
    workflow = item.get("workflow", {})
    if workflow.get("status") in {"fail", "unresolved"}:
        bases.add("workflow")
    evidence = item.get("evidence", [])
    if any(
        evidence_item.get("result", {}).get("status") in {"fail", "unresolved"}
        for evidence_item in evidence
    ):
        bases.add("evidence")
    if item.get("integrity_status") in {"fail", "unresolved"}:
        bases.add("integrity")
    return bases


def _reject_mechanical_success_overrides(
    adjudication: Dict[str, Any], scan: Dict[str, Any]
) -> None:
    """Reject stale semantic failures applied to resolved mechanical passes."""

    prepared = make_adjudication_template(
        scan,
        adjudication["date"],
        scan["validation_rules_version"],
        adjudication["mode"],
    )
    locked = _mechanically_locked_outcomes(prepared)
    mixed_review = {
        (item.get("entry"), item.get("identity")): _semantic_failure_bases(item)
        for item in prepared.get("review_queue", [])
        if item.get("kind") == "semantic_fallback"
        and item.get("evidence")
        and all(
            evidence_item.get("result", {}).get("status") == "pass"
            for evidence_item in item["evidence"]
        )
    }
    conflicts = []
    for entry in adjudication.get("entries", []):
        for row in entry.get("targets", []):
            for check in ("Integrity", "Provenance"):
                if (
                    (entry["id"], row["target"], check) in locked
                    and row.get(check.lower()) == "FAIL"
                ):
                    conflicts.append(f"{entry['id']}: {row['target']}: {check}")
            allowed_bases = mixed_review.get((entry["id"], row["target"]))
            if (
                row.get("provenance") == "FAIL"
                and allowed_bases is not None
                and row.get("_failure_basis") not in allowed_bases
            ):
                conflicts.append(
                    f"{entry['id']}: {row['target']}: unsupported semantic basis"
                )
    if conflicts:
        raise ValidationToolError(
            "adjudication overrides a mechanically resolved PASS: "
            + "; ".join(conflicts)
        )


def render_records(
    adjudication: Dict[str, Any], scan: Dict[str, Any], output_dir: Path
) -> Dict[str, Any]:
    """Render authoritative validation records from explicit adjudications."""

    allowed_top = {
        "schema_version",
        "validation_rules_version",
        "log",
        "requested_scope",
        "scope",
        "date",
        "mode",
        "summary",
        "entries",
        "review_queue",
    }
    extra = set(adjudication) - allowed_top
    if extra:
        raise ValidationToolError(
            f"unknown adjudication keys: {', '.join(sorted(extra))}"
        )
    if adjudication.get("schema_version") != ADJUDICATION_SCHEMA_VERSION:
        raise ValidationToolError("unsupported adjudication schema_version")
    date = adjudication["date"]
    if not _is_success_date(date):
        raise ValidationToolError(f"invalid validation date: {date!r}")
    if adjudication.get("mode") not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    if scan.get("schema_version") != SCAN_SCHEMA_VERSION:
        raise ValidationToolError("unsupported scan schema_version")
    if adjudication.get("validation_rules_version") != scan.get(
        "validation_rules_version"
    ):
        raise ValidationToolError(
            "adjudication and scan validation-rules versions differ"
        )
    if adjudication.get("log") != scan.get("summary"):
        raise ValidationToolError("adjudication and scan logs differ")
    review_queue = adjudication.get("review_queue")
    if not isinstance(review_queue, list):
        raise ValidationToolError("review_queue must be a list")
    if review_queue:
        raise ValidationToolError(
            f"adjudication has {len(review_queue)} unresolved review-queue item(s)"
        )
    _reject_mechanical_success_overrides(adjudication, scan)
    orphan_conflicts = _orphan_dependency_conflicts(adjudication)
    if orphan_conflicts:
        raise ValidationToolError(
            "unresolved orphan is a dependency of a successful check: "
            + "; ".join(orphan_conflicts)
        )

    expected_order = scan["entry_order"]
    scope = adjudication.get("scope")
    if not isinstance(scope, dict) or set(scope) != {"summary", "entries"}:
        raise ValidationToolError("scope must contain exactly summary and entries")
    if not isinstance(scope["summary"], bool):
        raise ValidationToolError("scope summary must be a boolean")
    scoped_entries = scope["entries"]
    if (
        not isinstance(scoped_entries, list)
        or not all(isinstance(entry_id, str) for entry_id in scoped_entries)
        or len(scoped_entries) != len(set(scoped_entries))
    ):
        raise ValidationToolError("scope entries must be unique entry IDs")
    scoped_entry_set = set(scoped_entries)
    expected_scoped_order = [
        entry_id for entry_id in expected_order if entry_id in scoped_entry_set
    ]
    if scoped_entries != expected_scoped_order:
        raise ValidationToolError(
            "scope entries are unknown or do not follow maintained-summary order"
        )
    partial_scope = not scope["summary"] or scoped_entries != expected_order
    record_names = (
        "validation.md",
        "validation-failures.md",
        "validation-state.json",
    )
    if partial_scope and any((output_dir / name).exists() for name in record_names):
        raise ValidationToolError(
            "partial-scope rendering cannot overwrite existing validation records"
        )

    summary_rows = adjudication["summary"]
    entry_rows = adjudication["entries"]
    actual_order = [entry["id"] for entry in entry_rows]
    if actual_order != scoped_entries:
        raise ValidationToolError(
            f"entry order mismatch: expected {scoped_entries}, got {actual_order}"
        )
    scan_entries = {
        entry["id"]: entry for entry in scan["entries"] if "error" not in entry
    }
    for entry in entry_rows:
        scanned = scan_entries.get(entry["id"])
        if scanned is None:
            raise ValidationToolError(
                f"adjudication contains unknown entry: {entry['id']}"
            )
        if (
            entry.get("path") != scanned["path"]
            or entry.get("title") != scanned["title"]
        ):
            raise ValidationToolError(
                f"adjudication entry metadata drifted: {entry['id']}"
            )
        if entry.get("scope_reconciled") is not True:
            raise ValidationToolError(
                f"entry scope was not explicitly reconciled: {entry['id']}"
            )

    expected_summary_items = {item["identity"] for item in scan["summary_items"]}
    covered_summary_items = {row.get("source_item") for row in summary_rows}
    if scope["summary"] and covered_summary_items != expected_summary_items:
        raise ValidationToolError(
            "Summary adjudication does not cover the scanned item inventory"
        )
    if not scope["summary"] and summary_rows:
        raise ValidationToolError("Summary rows are present outside requested scope")

    failures: List[Tuple[str, str, Dict[str, Any]]] = []
    completed_checks: List[Dict[str, Any]] = []
    report: List[str] = [
        "# Research-Log Validation",
        "",
        f"- Log: `{adjudication['log']}`",
        f"- Requested scope: {adjudication['requested_scope']}",
        f"- Report-update date: `{date}`",
        f"- Validation mode: {adjudication['mode']}",
        f"- Validation-rules version: `{adjudication['validation_rules_version']}`",
    ]

    summary_failed = 0
    rendered_summary = []
    summary_groups = set()
    for row in summary_rows:
        if row.get("support_reviewed") is not True:
            raise ValidationToolError(
                f"Summary support was not explicitly reviewed: {row['item']}"
            )
        provenance = _checked_result(row.get("provenance"), "Summary provenance")
        entries = row.get("entries", [])
        sections = row.get("sections", [])
        group = (
            row.get("source_item"),
            tuple(sorted(entries)),
            provenance,
        )
        if group in summary_groups:
            raise ValidationToolError(
                "Summary source item is unnecessarily split across rows with "
                "the same supporting entries and outcome"
            )
        summary_groups.add(group)
        if provenance == "FAIL":
            summary_failed += 1
            failures.append(("Summary", row["item"], row))
            _validate_failed_findings(row, ("Provenance",), row["item"])
            completed_checks.append(
                {
                    "entry": "Summary",
                    "target": row["item"],
                    "check": "Provenance",
                    "result": "FAIL",
                    "findings": _finding_map(row)["Provenance"],
                    "dependencies": _dependencies(row, "Provenance"),
                }
            )
            if entries or sections:
                raise ValidationToolError(
                    "failed unsupported Summary rows must use empty entries "
                    "and sections"
                )
        elif len(entries) != 1 or len(sections) != 1:
            raise ValidationToolError(
                "successful Summary row requires exactly one entry and section: "
                f"{row['item']}"
            )
        if _is_success_date(provenance):
            _validate_summary_support(row, scan_entries, scan)
            dependencies = _dependencies(row, "Provenance")
            if not dependencies:
                raise ValidationToolError(
                    f"successful Summary row lacks dependencies: {row['item']}"
                )
            completed_checks.append(
                {
                    "entry": "Summary",
                    "target": row["item"],
                    "check": "Provenance",
                    "result": provenance,
                    "dependencies": dependencies,
                    "resolution": {
                        "entry": row["support_evidence"][0]["entry"],
                        "section": row["support_evidence"][0]["section"],
                        "lines": row["support_evidence"][0]["lines"],
                    },
                }
            )
        rendered_summary.append(
            f"| {_cell(row['item'])} | {_cell(entries) if entries else '`-`'} | "
            f"{_cell(sections) if sections else '`-`'} | {_code_result(provenance)} |"
        )

    entry_total = 0
    entry_failed = 0
    failed_entries = 0
    reported_orphan_scopes = 0
    rendered_entries: List[str] = []
    for entry in entry_rows:
        targets = entry["targets"]
        if entry.get("scope_kind", "entry") != "entry" and not targets:
            continue
        if entry.get("scope_kind", "entry") != "entry":
            reported_orphan_scopes += 1
        failed_here = 0
        rendered_entries.extend(
            [
                "",
                f"### {entry['id']}: {_cell(entry['title'])}",
                "",
                f"Entry: `{entry['path']}`",
                "",
            ]
        )
        rendered_entries.extend(
            [
                "| Target | Section | Integrity | Provenance | "
                "Reproducibility | Notes |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in targets:
            is_orphan = row["target"] == ORPHAN_TARGET
            values = {
                "Integrity": (
                    _result(row.get("integrity"), "Integrity")
                    if is_orphan
                    else _checked_result(row.get("integrity"), "Integrity")
                ),
                "Provenance": _checked_result(row.get("provenance"), "Provenance"),
                "Reproducibility": _result(
                    row.get("reproducibility"), "Reproducibility"
                ),
            }
            if is_orphan and (
                values != {
                    "Integrity": "N/A",
                    "Provenance": "FAIL",
                    "Reproducibility": "N/A",
                }
                or not re.fullmatch(r"\d+ unresolved items?", row.get("notes", ""))
            ):
                raise ValidationToolError(
                    "orphan catch-all must use N/A, FAIL, N/A and an item count"
                )
            if (
                row["target"].startswith("Unprovenanced:")
                and values["Reproducibility"] != "N/A"
            ):
                raise ValidationToolError(
                    "unprovenanced evidence must use N/A reproducibility: "
                    f"{row['target']}"
                )
            failed_checks = [
                check for check, value in values.items() if value == "FAIL"
            ]
            if failed_checks:
                failed_here += 1
                if not is_orphan and row.get("notes", "-") != "-":
                    raise ValidationToolError(
                        f"failed row Notes must be '-': {row['target']}"
                    )
                _validate_failed_findings(row, failed_checks, row["target"])
                failures.append((entry["id"], row["target"], row))
            for check, value in values.items():
                if _is_success_date(value) or value == "FAIL":
                    dependencies = _dependencies(row, check)
                    if _is_success_date(value) and not dependencies:
                        raise ValidationToolError(
                            f"successful {check} lacks dependencies: {row['target']}"
                        )
                    completed = {
                        "entry": entry["id"],
                        "target": row["target"],
                        "check": check,
                        "result": value,
                        "dependencies": dependencies,
                    }
                    if value == "FAIL":
                        completed["findings"] = _finding_map(row)[check]
                    completed_checks.append(completed)
            notes = row.get("notes", "-")
            notes_cell = _code_result(notes) if notes == "-" else _cell(notes)
            sections = row.get("sections", [])
            section_cell = (
                _code_result("-") if sections == ["-"] else _cell(sections)
            )
            rendered_entries.append(
                f"| {_cell(row['target'])} | {section_cell} | "
                f"{_code_result(values['Integrity'])} | "
                f"{_code_result(values['Provenance'])} | "
                f"{_code_result(values['Reproducibility'])} | "
                f"{notes_cell} |"
            )
        entry_total += len(targets)
        entry_failed += failed_here
        if failed_here:
            failed_entries += 1

    if failures:
        report.extend(["- Failures: [validation-failures.md](validation-failures.md)"])
    report.extend(
        [
            "",
            "## Counts",
            "",
            "| Scope | Total rows | Failed rows |",
            "| --- | ---: | ---: |",
            f"| Summary | {len(summary_rows)} | {summary_failed} |",
            f"| Entry targets | {entry_total} | {entry_failed} |",
            "",
            f"Entries: {sum(entry.get('scope_kind', 'entry') == 'entry' for entry in entry_rows)} "
            f"total; {failed_entries - reported_orphan_scopes} containing a "
            "failed target row.",
            *(
                [
                    f"Orphan scopes: {reported_orphan_scopes} with an unresolved "
                    "failure."
                ]
                if reported_orphan_scopes
                else []
            ),
            "",
            "## Summary",
            "",
            "| Statistic | Entry | Section | Provenance |",
            "| --- | --- | --- | --- |",
            *rendered_summary,
            "",
            "## Entries",
            *rendered_entries,
            "",
        ]
    )

    report_text = "\n".join(report)
    failure_text = None
    if failures:
        failure_lines = ["# Validation Failures"]
        scopes = ["Summary", *actual_order]
        for scope_name in scopes:
            scoped = [
                (identity, row)
                for item_scope, identity, row in failures
                if item_scope == scope_name
            ]
            if not scoped:
                continue
            failure_lines.extend(["", f"## {scope_name}"])
            for identity, row in scoped:
                failure_lines.extend(["", f"### {_cell(identity)}", ""])
                findings = _finding_map(row)
                for check in ("Integrity", "Provenance", "Reproducibility"):
                    if row.get(check.lower()) != "FAIL":
                        continue
                    for finding in findings.get(check, []):
                        failure_lines.extend(
                            [f"- Check: {check}", f"- Finding: {finding}", ""]
                        )
            while failure_lines and failure_lines[-1] == "":
                failure_lines.pop()
        failure_text = "\n".join(failure_lines) + "\n"

    identities, stored_checks = _materialize_identities(scan, completed_checks)
    orphan_dispositions = []
    for entry_id in scoped_entries:
        scanned = scan_entries[entry_id]
        candidates = {
            item["identity"] for item in scanned.get("orphan_candidates", [])
        }
        if not candidates:
            continue
        adjudicated_entry = next(
            entry for entry in entry_rows if entry["id"] == entry_id
        )
        items = adjudicated_entry.get("orphan_items", [])
        if (
            {item.get("identity") for item in items} != candidates
            or any(
                item.get("decision") not in {"accepted", "unresolved"}
                or set(item) != {"identity", "decision"}
                for item in items
            )
        ):
            raise ValidationToolError(
                f"orphan disposition is incomplete for scope: {entry_id}"
            )
        unresolved = [
            item["identity"]
            for item in items
            if item["decision"] == "unresolved"
        ]
        row = next(
            (
                row
                for row in adjudicated_entry.get("targets", [])
                if row.get("target") == ORPHAN_TARGET
            ),
            None,
        )
        if bool(unresolved) != (row is not None):
            raise ValidationToolError(
                f"orphan row and item dispositions disagree: {entry_id}"
            )
        orphan_fingerprints = _orphan_item_fingerprints(scanned, scan)
        orphan_dispositions.append(
            {
                "inventory_version": ORPHAN_INVENTORY_VERSION,
                "entry": entry_id,
                "items": sorted(
                    (
                        {
                            **item,
                            "fingerprint": orphan_fingerprints[item["identity"]],
                        }
                        for item in items
                    ),
                    key=lambda item: item["identity"],
                ),
                "dependencies": [
                    {"path": path, "role": "entry"}
                    for path in adjudicated_entry.get(
                        "scope_paths", [adjudicated_entry["path"]]
                    )
                ],
            }
        )

    compact_failures = [
        {
            "scope": scope_name,
            "target": identity,
            "checks": [
                check
                for check in ("Integrity", "Provenance", "Reproducibility")
                if row.get(check.lower()) == "FAIL"
            ],
        }
        for scope_name, identity, row in failures
    ]
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "validation_rules_version": adjudication["validation_rules_version"],
        "input_fingerprint": scan["input_fingerprint"],
        "input_files": scan.get("files", {}),
        "mechanical_checks": scan.get("mechanical_checks", {}),
        "directory_memberships": scan.get("directory_memberships", {}),
        "files": identities,
        "completed_checks": stored_checks,
        "orphan_dispositions": orphan_dispositions,
        "result": {
            "date": date,
            "mode": adjudication["mode"],
            "requested_scope": adjudication["requested_scope"],
            "scope": scope,
            "summary_rows": len(summary_rows),
            "summary_failed": summary_failed,
            "entry_rows": entry_total,
            "entry_failed": entry_failed,
            "entries": sum(
                entry.get("scope_kind", "entry") == "entry" for entry in entry_rows
            ),
            "failed_entries": failed_entries - reported_orphan_scopes,
            "failure_rows": len(failures),
            "failures": compact_failures,
        },
        "report": _text_content_identity(report_text),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "validation.md"
    failure_path = output_dir / "validation-failures.md"
    _write_text(report_path, report_text)
    if failure_text is not None:
        _write_text(failure_path, failure_text)
    elif failure_path.exists():
        failure_path.unlink()
    _write_json(output_dir / "validation-state.json", state)
    successful_checks = sum(
        _is_success_date(check["result"]) for check in completed_checks
    )
    return {
        "summary_rows": len(summary_rows),
        "summary_failed": summary_failed,
        "entry_rows": entry_total,
        "entry_failed": entry_failed,
        "entries": sum(
            entry.get("scope_kind", "entry") == "entry" for entry in entry_rows
        ),
        "failed_entries": failed_entries - reported_orphan_scopes,
        "successful_checks": successful_checks,
        "completed_checks": len(completed_checks),
        "file_identities": len(state["files"]),
        "failure_rows": len(failures),
    }


def _table_cells(line: str) -> List[str]:
    return [
        cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line[1:-1])
    ]


def _markdown_rows(path: Path) -> Dict[str, Any]:
    lines = _read_text(path).splitlines()
    mode: Optional[str] = None
    current_entry: Optional[str] = None
    summary_rows = []
    entry_rows = []
    entry_order: List[str] = []
    entry_groups: Dict[str, List[List[str]]] = {}
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
        if (
            line.startswith("| Statistic")
            or line.startswith("| Target")
            or line.startswith("| Scope")
        ):
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


def _report_update_date(report_text: str) -> str:
    match = re.search(
        r"^- Report-update date: `(\d{4}-\d{2}-\d{2})`$",
        report_text,
        re.MULTILINE,
    )
    if not match or not _is_success_date(match.group(1)):
        raise ValidationToolError("validation report lacks a valid update date")
    return match.group(1)


def _counted(count: int, singular: str, plural: Optional[str] = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _validation_projection(report_path: Path, summary_path: Path) -> str:
    """Build the maintained-summary projection from a generated report."""

    if not report_path.is_file():
        raise ValidationToolError(f"validation report does not exist: {report_path}")
    report_text = _read_text(report_path)
    parsed = _markdown_rows(report_path)
    date = _report_update_date(report_text)
    summary_rows = parsed["summary"]
    summary_failures = sum(
        len(row) == 4 and row[3] == "`FAIL`" for row in summary_rows
    )
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
        reproduced = sum(_is_success_date(value) for value in reproduction)
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
            f"| {entry_id} | {date} | {standard_status} | "
            f"{reproduction_status} |"
        )
    return "\n".join(lines) + "\n"


def update_summary_validation(summary_path: Path, output_dir: Path) -> Dict[str, Any]:
    """Replace the summary Validation section from canonical report rows."""

    summary_path = summary_path.resolve()
    if not summary_path.is_file():
        raise ValidationToolError(f"summary does not exist: {summary_path}")
    report_path = (output_dir / "validation.md").resolve()
    expected_report = (summary_path.with_suffix("") / "validation.md").resolve()
    if report_path != expected_report:
        raise ValidationToolError(
            "summary projection requires the canonical validation report directory"
        )
    projection = _validation_projection(report_path, summary_path)
    lines = _read_text(summary_path).splitlines()

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

    _write_text(summary_path, "\n".join(lines).rstrip() + "\n")
    return {
        "summary": summary_path.as_posix(),
        "report": report_path.as_posix(),
        "entries": len(_markdown_rows(report_path)["entry_order"]),
    }


def lint_records(
    output_dir: Path, expected_entry_order: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    """Lint validation report, failure, and state contracts."""

    issues: List[str] = []
    report_path = output_dir / "validation.md"
    state_path = output_dir / "validation-state.json"
    failure_path = output_dir / "validation-failures.md"
    if not report_path.is_file():
        issues.append("missing validation.md")
    if not state_path.is_file():
        issues.append("missing validation-state.json")
    if issues:
        return {"ok": False, "issues": issues}

    report_text = _read_text(report_path)
    if re.search(r"\bPASS\b", report_text):
        issues.append("validation.md contains PASS")
    if "| - |" in report_text:
        issues.append("validation.md contains a plain hyphen table cell")
    parsed = _markdown_rows(report_path)
    summary_rows = parsed["summary"]
    entry_rows = parsed["entries"]
    entry_order = parsed["entry_order"]
    summary_total = len(summary_rows)
    entry_total = len(entry_rows)
    summary_failed = sum(len(row) == 4 and row[3] == "`FAIL`" for row in summary_rows)
    entry_failed = sum(len(row) == 6 and "`FAIL`" in row[2:5] for row in entry_rows)
    bad_notes = sum(
        len(row) == 6
        and "`FAIL`" in row[2:5]
        and row[5] != "`-`"
        and not (
            row[0] == ORPHAN_TARGET
            and re.fullmatch(r"\d+ unresolved items?", row[5])
        )
        for row in entry_rows
    )
    if bad_notes:
        issues.append(f"{bad_notes} failed entry rows have non-placeholder Notes")
    if expected_entry_order is not None:
        expected = list(expected_entry_order)
        scoped_expected = [entry_id for entry_id in expected if entry_id in entry_order]
        if scoped_expected != entry_order or len(entry_order) != len(set(entry_order)):
            issues.append("entry order does not match the maintained summary")

    for row in summary_rows:
        if len(row) != 4 or not (_is_success_date(row[3]) or row[3] == "`FAIL`"):
            issues.append("validation.md contains an invalid Summary result row")
    for row in entry_rows:
        is_orphan = len(row) == 6 and row[0] == ORPHAN_TARGET
        if is_orphan:
            valid_checked = (
                row[1:5] == ["`-`", "`N/A`", "`FAIL`", "`N/A`"]
                and re.fullmatch(r"\d+ unresolved items?", row[5]) is not None
            )
            valid_reproduction = valid_checked
        else:
            valid_checked = len(row) == 6 and all(
                _is_success_date(value) or value == "`FAIL`" for value in row[2:4]
            )
            valid_reproduction = len(row) == 6 and (
                _is_success_date(row[4]) or row[4] in {"`FAIL`", "`-`", "`N/A`"}
            )
        if not valid_checked or not valid_reproduction:
            issues.append("validation.md contains an invalid entry result row")

    reported = {}
    for scope in ("Summary", "Entry targets"):
        match = re.search(
            rf"^\| {re.escape(scope)} \| (\d+) \| (\d+) \|$", report_text, re.MULTILINE
        )
        if match:
            reported[scope] = (int(match.group(1)), int(match.group(2)))
    if reported.get("Summary") != (summary_total, summary_failed):
        issues.append("reported Summary counts do not match table rows")
    if reported.get("Entry targets") != (entry_total, entry_failed):
        issues.append("reported entry-target counts do not match table rows")

    try:
        state = json.loads(_read_text(state_path))
    except json.JSONDecodeError as exc:
        issues.append(f"validation-state.json is invalid JSON: {exc}")
        state = {}
    if set(state) != STATE_KEYS:
        issues.append("validation-state.json has incorrect top-level keys")
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        issues.append("validation-state.json has an unsupported schema version")
    report_rules = re.search(
        r"^- Validation-rules version: `([^`]+)`$", report_text, re.MULTILINE
    )
    if not report_rules or report_rules.group(1) != state.get(
        "validation_rules_version"
    ):
        issues.append("report and state validation-rules versions differ")

    checks = state.get("completed_checks", [])
    check_keys = []
    dependencies = set()
    successful_dependencies = set()
    successful = 0
    failed_checks = 0
    for check in checks if isinstance(checks, list) else []:
        allowed_check_keys = {
            "entry",
            "target",
            "check",
            "result",
            "dependencies",
            "dependency_signature",
            "resolution",
            "findings",
        }
        if (
            not {"entry", "target", "check", "result", "dependencies"}
            <= set(check)
            <= allowed_check_keys
        ):
            issues.append("state contains a malformed completed-check record")
            continue
        if not isinstance(check.get("dependency_signature"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", check["dependency_signature"]
        ):
            issues.append("state completed check lacks a dependency signature")
        resolution = check.get("resolution")
        if resolution is not None and (
            not isinstance(resolution, dict)
            or set(resolution) != {"entry", "section", "lines"}
            or not all(isinstance(value, str) for value in resolution.values())
        ):
            issues.append("state contains a malformed fast resolution")
        result = check["result"]
        if check["check"] not in {
            "Integrity",
            "Provenance",
            "Reproducibility",
        } or not (_is_success_date(result) or result == "FAIL"):
            issues.append("state contains an invalid completed-check result")
        if result == "FAIL":
            failed_checks += 1
            findings = check.get("findings")
            if not isinstance(findings, list) or not findings or not all(
                isinstance(finding, str) and finding for finding in findings
            ):
                issues.append("failed state result lacks focused findings")
        else:
            successful += 1
            if check.get("findings"):
                issues.append("successful state result has failure findings")
        check_keys.append((check["entry"], check["target"], check["check"]))
        for dependency in check.get("dependencies", []):
            if isinstance(dependency, dict) and isinstance(
                dependency.get("path"), str
            ):
                dependencies.add(dependency["path"])
                if _is_success_date(result):
                    successful_dependencies.add(dependency["path"])
            if not isinstance(dependency, dict) or set(dependency) != {
                "path",
                "role",
                "identity",
            }:
                issues.append("state contains a malformed dependency")
                continue
            identity = dependency["identity"]
            allowed_identity = {"size", "mtime_ns", "sha256", "members"}
            required_identity = {"size", "mtime_ns", "sha256"}
            valid_identity = isinstance(identity, dict) and (
                required_identity <= set(identity) <= allowed_identity
                or set(identity) == {"members", "sha256"}
                or set(identity) == {"error"}
                or identity == {"missing": True}
            )
            if not valid_identity:
                issues.append("state contains a malformed dependency identity")
    if len(check_keys) != len(set(check_keys)):
        issues.append("state contains duplicate completed-check records")

    files = set(state.get("files", {}))
    for identity in state.get("files", {}).values():
        allowed = {"size", "mtime_ns", "sha256", "members"}
        required = {"size", "mtime_ns", "sha256"}
        if not isinstance(identity, dict) or not (
            required <= set(identity) <= allowed or identity == {"missing": True}
        ):
            issues.append("state contains a malformed file identity")
    directory_dependencies = set(state.get("directory_memberships", {}))
    if files - dependencies or dependencies - files - directory_dependencies:
        issues.append(
            "state file identities do not exactly match completed-check dependencies"
        )

    input_fingerprint = state.get("input_fingerprint")
    if not isinstance(input_fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", input_fingerprint
    ):
        issues.append("state contains an invalid input fingerprint")
    input_files = state.get("input_files")
    if not isinstance(input_files, dict):
        issues.append("state input file inventory must be an object")
    else:
        for identity in input_files.values():
            allowed = {"size", "mtime_ns", "sha256", "members"}
            required = {"size", "mtime_ns", "sha256"}
            if not isinstance(identity, dict) or not (
                required <= set(identity) <= allowed
                or identity == {"missing": True}
            ):
                issues.append("state contains a malformed input file identity")
    if not isinstance(state.get("mechanical_checks"), dict):
        issues.append("state mechanical checks must be an object")
    for identity in state.get("directory_memberships", {}).values():
        if not isinstance(identity, dict) or (
            set(identity) != {"members", "sha256"}
            and set(identity) != {"error"}
        ):
            issues.append("state contains a malformed directory membership")
    for dependency in list(successful_dependencies):
        membership = state.get("files", {}).get(dependency, {})
        successful_dependencies.update(
            (Path(dependency) / member).as_posix()
            for member in membership.get("members", [])
            if isinstance(member, str)
        )
    report_identity = state.get("report")
    if (
        not isinstance(report_identity, dict)
        or set(report_identity) != {"size", "sha256"}
        or report_identity != _content_identity(report_path)
    ):
        issues.append("state report identity does not match validation.md")

    result = state.get("result")
    required_result_keys = {
        "date",
        "mode",
        "requested_scope",
        "scope",
        "summary_rows",
        "summary_failed",
        "entry_rows",
        "entry_failed",
        "entries",
        "failed_entries",
        "failure_rows",
        "failures",
    }
    if not isinstance(result, dict) or set(result) != required_result_keys:
        issues.append("state contains a malformed cached result")
    else:
        if result["date"] != _report_update_date(report_text):
            issues.append("cached result date differs from validation.md")
        expected_counts = {
            "summary_rows": summary_total,
            "summary_failed": summary_failed,
            "entry_rows": entry_total,
            "entry_failed": entry_failed,
            "failure_rows": summary_failed + entry_failed,
        }
        for key, expected in expected_counts.items():
            if result[key] != expected:
                issues.append(f"cached result {key} differs from validation.md")
        if len(result.get("failures", [])) != summary_failed + entry_failed:
            issues.append("cached failure inventory differs from validation.md")

    dispositions = state.get("orphan_dispositions")
    if not isinstance(dispositions, list):
        issues.append("state orphan dispositions must be a list")
    else:
        seen_dispositions = set()
        unresolved_orphans = set()
        for disposition in dispositions:
            if not isinstance(disposition, dict):
                issues.append("state contains a malformed orphan disposition")
                continue
            required = {
                "inventory_version",
                "entry",
                "items",
                "dependencies",
            }
            if set(disposition) != required or disposition.get(
                "inventory_version"
            ) != ORPHAN_INVENTORY_VERSION:
                issues.append("state contains a malformed orphan disposition")
                continue
            items = disposition.get("items")
            if (
                not isinstance(items, list)
                or not all(
                    isinstance(item, dict)
                    and set(item) == {"identity", "decision", "fingerprint"}
                    and isinstance(item["identity"], str)
                    and item["decision"] in {"accepted", "unresolved"}
                    and bool(re.fullmatch(r"[0-9a-f]{64}", item["fingerprint"]))
                    for item in items
                )
                or len({item["identity"] for item in items}) != len(items)
            ):
                issues.append("state contains malformed orphan item dispositions")
            else:
                unresolved_orphans.update(
                    item["identity"]
                    for item in items
                    if item["decision"] == "unresolved"
                )
            entry_id = disposition["entry"]
            if entry_id in seen_dispositions:
                issues.append("state contains duplicate orphan dispositions")
            seen_dispositions.add(entry_id)
        conflicts = sorted(successful_dependencies & unresolved_orphans)
        if conflicts:
            issues.append(
                "unresolved orphan is a dependency of a successful check: "
                + "; ".join(conflicts)
            )

    failed_rows = summary_failed + entry_failed
    headings = 0
    if failure_path.exists():
        headings = sum(
            1
            for line in _read_text(failure_path).splitlines()
            if line.startswith("### ")
        )
    if failed_rows and not failure_path.exists():
        issues.append("failed report rows lack validation-failures.md")
    if not failed_rows and failure_path.exists():
        issues.append("validation-failures.md exists without failed report rows")
    if headings != failed_rows:
        issues.append(
            f"failure heading count {headings} does not match failed rows {failed_rows}"
        )

    dates = sum(_is_success_date(row[3]) for row in summary_rows if len(row) == 4)
    dates += sum(
        _is_success_date(value)
        for row in entry_rows
        if len(row) == 6
        for value in row[2:5]
    )
    if dates != successful:
        issues.append(
            f"successful report cells {dates} do not match state records {successful}"
        )

    return {
        "ok": not issues,
        "issues": issues,
        "counts": {
            "summary_rows": summary_total,
            "summary_failed": summary_failed,
            "entry_rows": entry_total,
            "entry_failed": entry_failed,
            "failure_headings": headings,
            "successful_checks": successful,
            "failed_checks": failed_checks,
            "completed_checks": successful + failed_checks,
            "file_identities": len(files),
        },
        "entry_order": entry_order,
    }


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(_read_text(path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationToolError(f"expected JSON object: {path}")
    return value


# Bounded review packets and declarative adjudication


def _packet_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _queue_commands(
    scan: Dict[str, Any], entry_id: str, identity: str, sections: Sequence[str]
) -> List[Dict[str, Any]]:
    """Return a small candidate-command set without deciding producer meaning."""

    entry = next(
        (item for item in scan.get("entries", []) if item.get("id") == entry_id), None
    )
    if entry is None:
        return []
    target_name = Path(identity).name
    exact = []
    local = []
    for command in entry.get("commands", []):
        output_paths = [
            argument.get("path", "")
            for argument in command.get("path_arguments", [])
            if argument.get("role_hint") == "output"
        ]
        if target_name in command.get("command", "") or any(
            Path(path).name == target_name for path in output_paths
        ):
            exact.append(command)
        elif command.get("section") in sections:
            local.append(command)
    return (exact or local)[:5]


def _command_source_context(
    scan: Dict[str, Any], command: Dict[str, Any], identity: str
) -> List[str]:
    """Return bounded source lines that use the argument carrying one path value."""

    raw_script = command.get("script")
    if not raw_script or not Path(raw_script).is_file():
        return []
    target_name = Path(identity).name
    parameters = []
    for argument in command.get("path_arguments", []):
        raw_path = argument.get("path")
        if not raw_path:
            continue
        argument_identity = _identity_for_path(scan, raw_path)
        if argument_identity != identity and Path(raw_path).name != target_name:
            continue
        parameter = argument.get("option")
        if parameter:
            parameters.append(parameter.lstrip("-").replace("-", "_"))
    if not parameters:
        return []
    try:
        source_lines = _read_text(Path(raw_script)).splitlines()
    except (OSError, UnicodeError):
        return []
    matches = []
    for number, line in enumerate(source_lines, 1):
        if any(
            re.search(rf"\b(?:args|parsed)\.{re.escape(parameter)}\b", line)
            for parameter in parameters
        ):
            matches.append(f"{number}: {line.strip()}")
        if len(matches) == 4:
            break
    return matches


def make_review_packet(
    scan: Dict[str, Any],
    adjudication: Dict[str, Any],
    *,
    entry: Optional[str] = None,
    kind: Optional[str] = None,
) -> Tuple[str, Dict[str, int]]:
    """Render compact context for bounded semantic decisions.

    The packet deliberately presents facts and candidates. It never assigns a
    semantic result or changes the adjudication.
    """

    queue = adjudication.get("review_queue", [])
    if not isinstance(queue, list):
        raise ValidationToolError("review_queue must be a list")
    if entry is not None:
        queue = [item for item in queue if item.get("entry") == entry]
    if kind is not None:
        queue = [item for item in queue if item.get("kind") == kind]
    lines = [
        "# Validation Review Packet",
        "",
        f"- Log: `{scan.get('summary', '-')}`",
        f"- Queue items: {len(queue)}",
        "- Purpose: bounded context for semantic decisions; this packet does "
        "not decide checks.",
        "- Directory dependencies: select the material relative members on "
        "the existing directory dependency from the bounded candidate inventory.",
    ]
    counts: Dict[str, int] = {}
    for number, item in enumerate(queue, 1):
        kind = item.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        entry_id = item.get("entry", "-")
        identity = item.get("identity", "-")
        lines.extend(
            [
                "",
                f"## Q{number:03d} — {entry_id}: {_packet_text(identity, 180)}",
                "",
                f"- Kind: `{kind}`",
            ]
        )
        if item.get("reason"):
            lines.append(f"- Question: {_packet_text(item['reason'])}")
        if item.get("section"):
            lines.append(f"- Section: {_packet_text(item['section'])}")
        if item.get("sections"):
            lines.append(f"- Sections: {_packet_text('; '.join(item['sections']))}")
        for collection in item.get("collections", []):
            lines.extend(_collection_packet_lines(scan, collection))
        if kind == "semantic_provenance":
            row = next(
                (
                    row
                    for row in adjudication.get("summary", [])
                    if row.get("item") == identity
                ),
                None,
            )
            if row:
                lines.append(
                    "- Declared support: "
                    + _packet_text(
                        "; ".join(
                            f"{entry} / {section}"
                            for entry, section in zip(
                                row.get("entries", []), row.get("sections", [])
                            )
                        )
                    )
                )
            for candidate in item.get("candidates", [])[:4]:
                line_range = str(candidate.get("line", "?"))
                if candidate.get("end_line") != candidate.get("line"):
                    line_range += f"-{candidate.get('end_line')}"
                lines.append(
                    f"- Candidate `{candidate.get('section', '-')}` lines "
                    f"{line_range}: {_packet_text(candidate.get('text'))}"
                )
            continue
        if kind == "orphan_candidates":
            commands = []
            command_identities: Dict[Tuple[Any, Any], List[str]] = {}
            for note in item.get("validation_notes", []):
                prefix = (
                    f"{note.get('entry')} / " if note.get("entry") else ""
                )
                lines.append(
                    f"- Existing Validation note, {prefix}"
                    f"`{note.get('section', '-')}` line {note.get('line', '?')}: "
                    f"{_packet_text(note.get('text'))}"
                )
            for candidate in item.get("candidates", []):
                candidate_identity = candidate.get("identity", "")
                lines.append(
                    f"- Candidate {candidate.get('kind', 'item')}: "
                    f"`{candidate_identity or '-'}`"
                )
                candidate_commands = _queue_commands(
                    scan, entry_id, candidate_identity, []
                )
                commands.extend(candidate_commands)
                for command in candidate_commands:
                    key = (command.get("line"), command.get("command"))
                    command_identities.setdefault(key, []).append(candidate_identity)
            commands = list(
                {
                    (command.get("line"), command.get("command")): command
                    for command in commands
                }.values()
            )[:5]
        else:
            if item.get("integrity"):
                lines.append(f"- Integrity context: {_packet_text(item['integrity'])}")
            workflow = item.get("workflow") or {}
            if workflow:
                lines.append(
                    f"- Workflow: `{workflow.get('status', '-')}` — "
                    f"{_packet_text(workflow.get('detail'))}"
                )
            for evidence in item.get("evidence", []):
                result = evidence.get("result", {})
                lines.extend(
                    [
                        f"- Presented `{evidence.get('kind', '-')}`: "
                        f"`{_packet_text(evidence.get('selector'), 180)}`",
                        f"  - Context: {_packet_text(evidence.get('context'))}",
                        f"  - Locator: `{_packet_text(evidence.get('locator'), 220)}`",
                        "  - Transformation: "
                        f"{_packet_text(evidence.get('transformation'))}",
                        f"  - Mechanical result: `{result.get('status', '-')}` — "
                        f"{_packet_text(result.get('detail'))}",
                    ]
                )
            commands = _queue_commands(
                scan, entry_id, identity, item.get("sections", [])
            )
        for command in commands:
            command_key = (command.get("line"), command.get("command"))
            lines.append(
                f"- Candidate command, `{command.get('section', '-')}` line "
                f"{command.get('line', '?')}: "
                f"`{_packet_text(command.get('command'), 520)}`"
            )
            context_identities = (
                command_identities.get(command_key, [])
                if kind == "orphan_candidates"
                else [identity]
            )
            for context_identity in context_identities:
                for source_line in _command_source_context(
                    scan, command, context_identity
                ):
                    lines.append(
                        f"  - Producer code: `{_packet_text(source_line, 420)}`"
                    )
    return "\n".join(lines) + "\n", counts


def _decision_target(
    adjudication: Dict[str, Any], entry_id: str, identity: str
) -> Tuple[str, Dict[str, Any]]:
    """Return the unique adjudication row named by a review-queue item."""

    if entry_id == "Summary":
        matches = [
            row
            for row in adjudication.get("summary", [])
            if row.get("item") == identity
        ]
        kind = "summary"
    else:
        entries = [
            entry
            for entry in adjudication.get("entries", [])
            if entry.get("id") == entry_id
        ]
        matches = [
            row
            for entry in entries
            for row in entry.get("targets", [])
            if row.get("target") == identity
        ]
        kind = "entry"
    if len(matches) != 1:
        raise ValidationToolError(
            f"review decision target is not unique: {entry_id}: {identity}"
        )
    return kind, matches[0]


def _remove_decision_target(
    adjudication: Dict[str, Any], entry_id: str, identity: str
) -> None:
    """Remove one entry target after an explicit semantic orphan decision."""

    if entry_id == "Summary":
        raise ValidationToolError("Summary rows cannot be dropped")
    entries = [
        entry
        for entry in adjudication.get("entries", [])
        if entry.get("id") == entry_id
    ]
    if len(entries) != 1:
        raise ValidationToolError(f"unknown adjudication entry: {entry_id}")
    before = len(entries[0].get("targets", []))
    entries[0]["targets"] = [
        row for row in entries[0].get("targets", []) if row.get("target") != identity
    ]
    if len(entries[0]["targets"]) != before - 1:
        raise ValidationToolError(
            f"review decision target is not unique: {entry_id}: {identity}"
        )


def _validated_member_paths(
    scan: Dict[str, Any], identity: str, members: Any
) -> List[str]:
    """Validate a compact decision's explicit collection-member scope."""

    raw = scan.get("resolved_paths", {}).get(identity)
    if raw is None or not Path(raw).is_dir():
        raise ValidationToolError(f"collection dependency is not resolved: {identity}")
    root = Path(raw)
    if isinstance(members, dict):
        if set(members) != {"glob"} or not isinstance(members["glob"], str):
            raise ValidationToolError(
                "collection member selector must contain exactly one glob string"
            )
        pattern = members["glob"]
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ValidationToolError(
                f"collection member glob must be relative: {pattern}"
            )
        members = [
            child.relative_to(root).as_posix()
            for child in root.glob(pattern)
            if child.is_file()
        ]
    if not isinstance(members, list) or not members or not all(
        isinstance(member, str) and member for member in members
    ):
        raise ValidationToolError(
            f"collection members for {identity} must be a nonempty string list "
            "or a glob selector"
        )
    normalized = []
    for member in members:
        relative = Path(member)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationToolError(
                f"collection member must be a relative child path: {member}"
            )
        child = root / relative
        if not child.is_file():
            raise ValidationToolError(
                f"collection member does not exist as a file: {identity}: {member}"
            )
        normalized.append(relative.as_posix())
    return sorted(set(normalized))


def _apply_decision_dependencies(
    scan: Dict[str, Any],
    adjudication: Dict[str, Any],
    row: Dict[str, Any],
    entry_id: str,
    action: Dict[str, Any],
) -> None:
    """Apply bounded dependency edits declared by a reviewed decision."""

    dependencies = row.setdefault("dependencies", [])
    copy_from = action.get("copy_dependencies_from")
    if copy_from is not None:
        if isinstance(copy_from, str):
            source_entry, source_identity = entry_id, copy_from
        elif isinstance(copy_from, dict) and set(copy_from) == {"entry", "identity"}:
            source_entry = copy_from["entry"]
            source_identity = copy_from["identity"]
        else:
            raise ValidationToolError(
                "copy_dependencies_from must be a target identity or an "
                "entry/identity object"
            )
        source_kind, source = _decision_target(
            adjudication, source_entry, source_identity
        )
        if source_kind != "entry":
            raise ValidationToolError("Summary dependencies cannot be copied")
        for dependency in source.get("dependencies", []):
            if dependency.get("role") == "target":
                continue
            if not any(
                item.get("path") == dependency.get("path")
                and item.get("role") == dependency.get("role")
                for item in dependencies
            ):
                dependencies.append(copy.deepcopy(dependency))

    additions = action.get("add_dependencies", [])
    if not isinstance(additions, list):
        raise ValidationToolError("add_dependencies must be a list")
    for addition in additions:
        if not isinstance(addition, dict) or not {"path", "role"} <= set(addition):
            raise ValidationToolError(
                "each added dependency must contain path and role"
            )
        if set(addition) - {"path", "role", "members"}:
            raise ValidationToolError("added dependency has unknown keys")
        identity = addition["path"]
        if identity not in scan.get("resolved_paths", {}):
            raise ValidationToolError(
                f"added dependency was not resolved by the scan: {identity}"
            )
        matches = [
            item
            for item in dependencies
            if item.get("path") == identity and item.get("role") == addition["role"]
        ]
        dependency = matches[0] if matches else {
            "path": identity,
            "role": addition["role"],
        }
        if not matches:
            dependencies.append(dependency)
        if "members" in addition:
            dependency["members"] = _validated_member_paths(
                scan, identity, addition["members"]
            )

    removals = action.get("remove_dependencies", [])
    if not isinstance(removals, list) or not all(
        isinstance(identity, str) for identity in removals
    ):
        raise ValidationToolError("remove_dependencies must be a string list")
    dependencies[:] = [
        dependency
        for dependency in dependencies
        if dependency.get("path") not in set(removals)
    ]

    member_scopes = action.get("members", {})
    if not isinstance(member_scopes, dict):
        raise ValidationToolError("members must map collection identities to lists")
    for identity, members in member_scopes.items():
        matches = [
            dependency
            for dependency in dependencies
            if dependency.get("path") == identity
        ]
        if len(matches) != 1:
            raise ValidationToolError(
                f"collection dependency is not unique on target: {identity}"
            )
        matches[0]["members"] = _validated_member_paths(scan, identity, members)


def _apply_summary_support(
    row: Dict[str, Any], item: Dict[str, Any], action: Dict[str, Any], date: str
) -> None:
    """Record one explicitly selected summary-to-entry support candidate."""

    candidate_number = action.get("candidate")
    if not isinstance(candidate_number, int) or candidate_number < 1:
        raise ValidationToolError("a support decision requires a candidate number")
    candidates = item.get("candidates", [])
    if candidate_number > len(candidates):
        raise ValidationToolError(
            f"support candidate {candidate_number} is unavailable for "
            f"{item['identity']}"
        )
    candidate = candidates[candidate_number - 1]
    entry = row.get("entries", [])
    sections = row.get("sections", [])
    if len(entry) != 1 or sections != [candidate.get("section")]:
        raise ValidationToolError(
            "support candidate does not match the declared Summary association: "
            f"{item['identity']}"
        )
    start = candidate.get("line")
    end = candidate.get("end_line", start)
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValidationToolError("support candidate lacks an exact line range")
    row["provenance"] = date
    row["support_reviewed"] = True
    row["support_evidence"] = [
        {
            "entry": entry[0],
            "section": sections[0],
            "lines": str(start) if start == end else f"{start}-{end}",
            "text": candidate.get("text", ""),
        }
    ]
    row["findings"] = []


def _replace_decision_findings(
    row: Dict[str, Any], findings: Dict[str, Any]
) -> None:
    """Replace findings only for checks explicitly decided by the agent."""

    if not isinstance(findings, dict) or not findings:
        raise ValidationToolError("a fail decision requires findings")
    invalid = set(findings) - {"Integrity", "Provenance", "Reproducibility"}
    if invalid or not all(
        isinstance(value, str) and value for value in findings.values()
    ):
        raise ValidationToolError(
            "findings must map a validation check to nonempty text"
        )
    row["findings"] = [
        finding
        for finding in row.get("findings", [])
        if finding.get("check") not in findings
    ]
    for check, finding in findings.items():
        row[check.lower()] = "FAIL"
        row["findings"].append({"check": check, "finding": finding})


def _successful_dependency_paths(adjudication: Dict[str, Any]) -> set[str]:
    """Return dependencies supporting every successful adjudicated check."""

    dependencies = set()

    def add(items: Sequence[Dict[str, Any]]) -> None:
        for item in items:
            identity = item["path"]
            dependencies.add(identity)
            dependencies.update(
                (Path(identity) / member).as_posix()
                for member in item.get("members", [])
            )

    for row in adjudication.get("summary", []):
        if _is_success_date(row.get("provenance")):
            add(_dependencies(row, "Provenance"))
    for entry in adjudication.get("entries", []):
        for row in entry.get("targets", []):
            for check in ("Integrity", "Provenance", "Reproducibility"):
                if _is_success_date(row.get(check.lower())):
                    add(_dependencies(row, check))
    return dependencies


def _orphan_dependency_conflicts(adjudication: Dict[str, Any]) -> List[str]:
    """Return unresolved orphans also used by successful checks."""

    dependencies = _successful_dependency_paths(adjudication)
    unresolved = {
        item["identity"]
        for entry in adjudication.get("entries", [])
        for item in entry.get("orphan_items", [])
        if item.get("decision") == "unresolved"
    }
    return sorted(dependencies & unresolved)


def _sync_orphan_entry(
    adjudication: Dict[str, Any], entry: Dict[str, Any]
) -> None:
    """Synchronize one catch-all row and queue item with item decisions."""

    queue = adjudication.get("review_queue", [])
    pending = {
        candidate["identity"]
        for item in queue
        if item.get("kind") == "orphan_candidates"
        and item.get("entry") == entry["id"]
        for candidate in item.get("candidates", [])
    }
    reportable = [
        item["identity"]
        for item in entry.get("orphan_items", [])
        if item.get("decision") == "unresolved" or item["identity"] in pending
    ]
    rows = [
        row
        for row in entry.get("targets", [])
        if row.get("target") == ORPHAN_TARGET
    ]
    if not reportable:
        entry["targets"] = [
            row
            for row in entry.get("targets", [])
            if row.get("target") != ORPHAN_TARGET
        ]
        return
    if len(rows) != 1:
        raise ValidationToolError(
            f"orphan review row is not unique: {entry['id']}: {ORPHAN_TARGET}"
        )
    row = rows[0]
    count = len(reportable)
    row["notes"] = f"{count} unresolved {'item' if count == 1 else 'items'}"
    row["findings"] = [
        {
            "check": "Provenance",
            "finding": f"Unresolved orphan candidate: {identity}",
        }
        for identity in reportable
    ]
    row["orphan_items"] = entry["orphan_items"]


def _reconcile_semantic_dependencies(
    scan: Dict[str, Any], adjudication: Dict[str, Any]
) -> None:
    """Expand successful semantic producer decisions and remove false orphans."""

    resolved_paths = scan.get("resolved_paths", {})
    project_root = Path(scan["project_root"])
    script_inventory = {
        Path(resolved_paths[identity]).resolve()
        for identity in scan.get("script_inventory", [])
        if identity in resolved_paths
    }
    scan_entries = [
        entry for entry in scan.get("entries", []) if "error" not in entry
    ]
    connected = _successful_dependency_paths(adjudication)
    orphan_connected = set(connected)
    connected_tokens: set[Tuple[str, str]] = set()
    script_graph: Dict[Path, List[Path]] = {}
    queue = adjudication.get("review_queue", [])

    for entry in adjudication.get("entries", []):
        for row in entry.get("targets", []):
            if row.get("target") == ORPHAN_TARGET or not _is_success_date(
                row.get("provenance")
            ):
                continue
            dependencies = row.setdefault("dependencies", [])
            seeds = {item["path"] for item in dependencies}
            (
                reachable,
                used,
                reachable_scripts,
                row_tokens,
                script_graph,
            ) = _workflow_dependency_closure(
                scan_entries,
                resolved_paths,
                project_root,
                seeds,
                script_inventory,
                script_graph,
                ambiguous_output_identities={row["target"]},
            )
            connected.update(reachable)
            orphan_connected.update(used)
            connected_tokens.update(row_tokens)
            script_identities = {
                _identity_for_path(scan, path.as_posix())
                for path in reachable_scripts
            }
            connected.update(script_identities)
            orphan_connected.update(script_identities)
            existing = {item["path"] for item in dependencies}
            for identity in sorted(reachable | script_identities):
                if identity in existing or identity not in resolved_paths:
                    continue
                raw = Path(resolved_paths[identity]).resolve()
                dependencies.append(
                    {
                        "path": identity,
                        "role": "producer" if raw in script_inventory else "input",
                    }
                )
                existing.add(identity)

            collections = [
                item["path"]
                for item in dependencies
                if scan.get("mechanical_checks", {})
                .get(item["path"], {})
                .get("type")
                == "directory"
                and not item.get("members")
            ]
            if collections:
                queued = next(
                    (
                        item
                        for item in queue
                        if item.get("entry") == entry["id"]
                        and item.get("identity") == row["target"]
                        and item.get("kind") not in {
                            "orphan_candidates",
                            "evidence_record_error",
                            "reproduction",
                        }
                    ),
                    None,
                )
                if queued is None:
                    queue.append(
                        {
                            "entry": entry["id"],
                            "kind": "collection_scope",
                            "identity": row["target"],
                            "sections": row["sections"],
                            "collections": sorted(set(collections)),
                            "reason": (
                                "select material members for collection dependencies "
                                "discovered from the semantic producer closure"
                            ),
                        }
                    )
                else:
                    queued["collections"] = sorted(
                        set(queued.get("collections", [])) | set(collections)
                    )

    for entry in adjudication.get("entries", []):
        token_identities = {
            f"<{name}>" for entry_id, name in connected_tokens if entry_id == entry["id"]
        }

        for item in entry.get("orphan_items", []):
            if _orphan_identity_is_accepted(
                item.get("identity", ""),
                token_identities,
                orphan_connected,
                resolved_paths,
            ):
                item["decision"] = "accepted"
        for review_item in list(queue):
            if review_item.get("kind") != "orphan_candidates" or review_item.get(
                "entry"
            ) != entry.get("id"):
                continue
            review_item["candidates"] = [
                candidate
                for candidate in review_item.get("candidates", [])
                if not _orphan_identity_is_accepted(
                    candidate["identity"],
                    token_identities,
                    orphan_connected,
                    resolved_paths,
                )
            ]
            if not review_item["candidates"]:
                queue.remove(review_item)
        _sync_orphan_entry(adjudication, entry)


def _apply_orphan_decision(
    adjudication: Dict[str, Any],
    entry_id: str,
    item: Dict[str, Any],
    unresolved: Any,
) -> None:
    """Persist item-level outcomes for one orphan-candidate review."""

    candidates = [candidate["identity"] for candidate in item.get("candidates", [])]
    if (
        not isinstance(unresolved, list)
        or not all(isinstance(identity, str) for identity in unresolved)
        or len(unresolved) != len(set(unresolved))
        or not set(unresolved) <= set(candidates)
    ):
        raise ValidationToolError(
            "an orphan decision requires a unique unresolved subset of its candidates"
        )
    entries = [
        entry
        for entry in adjudication.get("entries", [])
        if entry.get("id") == entry_id
    ]
    if len(entries) != 1:
        raise ValidationToolError(f"unknown orphan scope: {entry_id}")
    entry = entries[0]
    decisions = {
        candidate: ("unresolved" if candidate in unresolved else "accepted")
        for candidate in candidates
    }
    for orphan_item in entry.get("orphan_items", []):
        if orphan_item.get("identity") in decisions:
            orphan_item["decision"] = decisions[orphan_item["identity"]]
    unresolved_all = [
        orphan_item["identity"]
        for orphan_item in entry.get("orphan_items", [])
        if orphan_item.get("decision") == "unresolved"
    ]
    rows = [
        row
        for row in entry.get("targets", [])
        if row.get("target") == ORPHAN_TARGET
    ]
    if len(rows) != 1:
        raise ValidationToolError(
            f"orphan review row is not unique: {entry_id}: {ORPHAN_TARGET}"
        )
    if not unresolved_all:
        _remove_decision_target(adjudication, entry_id, ORPHAN_TARGET)
        return
    row = rows[0]
    count = len(unresolved_all)
    row["notes"] = f"{count} unresolved {'item' if count == 1 else 'items'}"
    row["findings"] = [
        {
            "check": "Provenance",
            "finding": f"Unresolved orphan candidate: {identity}",
        }
        for identity in unresolved_all
    ]
    row["orphan_items"] = entry["orphan_items"]


def apply_review_decisions(
    scan: Dict[str, Any],
    adjudication: Dict[str, Any],
    decisions: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Apply compact, explicit agent decisions to a prepared adjudication."""

    if set(decisions) != {"schema_version", "actions"}:
        raise ValidationToolError(
            "decisions must contain exactly schema_version and actions"
        )
    if decisions["schema_version"] != DECISION_SCHEMA_VERSION:
        raise ValidationToolError("unsupported decision schema_version")
    actions = decisions["actions"]
    if not isinstance(actions, list):
        raise ValidationToolError("decision actions must be a list")
    result = copy.deepcopy(adjudication)
    queue = result.get("review_queue")
    if not isinstance(queue, list):
        raise ValidationToolError("review_queue must be a list")
    date = result.get("date")
    if not _is_success_date(date):
        raise ValidationToolError("adjudication has an invalid validation date")

    counts: Dict[str, int] = {}
    allowed_action_keys = set().union(*DECISION_FIELDS_BY_OUTCOME.values())
    for action_number, action in enumerate(actions, 1):
        if not isinstance(action, dict) or set(action) - allowed_action_keys:
            raise ValidationToolError(
                f"decision action {action_number} has unknown keys"
            )
        matcher = action.get("match")
        if not isinstance(matcher, dict) or not matcher or set(matcher) - {
            "kind",
            "entry",
            "identity",
            "targets",
        }:
            raise ValidationToolError(
                f"decision action {action_number} has an invalid match"
            )
        if "targets" in matcher:
            targets = matcher["targets"]
            if len(matcher) != 1 or not isinstance(targets, list) or not targets:
                raise ValidationToolError(
                    "a targets match must be the only match field and be nonempty"
                )
            if not all(
                isinstance(target, dict)
                and set(target) == {"entry", "identity"}
                and all(isinstance(value, str) for value in target.values())
                for target in targets
            ):
                raise ValidationToolError(
                    "targets must contain exact entry/identity objects"
                )
            target_pairs = {
                (target["entry"], target["identity"]) for target in targets
            }
            if len(target_pairs) != len(targets):
                raise ValidationToolError("targets match contains duplicates")
            matches = [
                item
                for item in queue
                if (item.get("entry"), item.get("identity")) in target_pairs
            ]
            if len(matches) != len(targets):
                raise ValidationToolError(
                    f"decision action {action_number} does not match every target"
                )
        else:
            matches = [
                item
                for item in queue
                if all(item.get(key) == value for key, value in matcher.items())
            ]
        if not matches:
            raise ValidationToolError(
                f"decision action {action_number} matches no unresolved queue items"
            )
        decision = action.get("decision")
        if decision not in DECISION_FIELDS_BY_OUTCOME:
            raise ValidationToolError(
                f"decision action {action_number} has an invalid decision"
            )
        unused = set(action) - DECISION_FIELDS_BY_OUTCOME[decision]
        if unused:
            raise ValidationToolError(
                f"decision action {action_number} has keys not used by {decision}: "
                f"{', '.join(sorted(unused))}"
            )
        for item in matches:
            entry_id = item.get("entry")
            identity = item.get("identity")
            if (item.get("kind") == "reproduction") != (
                decision in REPRODUCTION_DECISIONS
            ):
                raise ValidationToolError(
                    "reproduction queue items require a reproduction decision, "
                    "and reproduction decisions apply only to those items"
                )
            if item.get("kind") == "orphan_candidates" and decision not in {
                "drop",
                "orphan",
            }:
                raise ValidationToolError(
                    "orphan candidates require an item-level orphan decision"
                )
            kind, row = _decision_target(result, entry_id, identity)
            if decision == "fail":
                bases = _semantic_failure_bases(item)
                failure_basis = action.get("failure_basis")
                requires_basis = (
                    item.get("kind") == "semantic_fallback"
                    and item.get("evidence")
                    and all(
                        evidence_item.get("result", {}).get("status") == "pass"
                        for evidence_item in item["evidence"]
                    )
                )
                if requires_basis and failure_basis not in bases:
                    raise ValidationToolError(
                        "a semantic FAIL after mechanical evidence PASS requires "
                        "an unresolved failure_basis"
                    )
                if failure_basis is not None:
                    if not isinstance(failure_basis, str) or failure_basis not in bases:
                        raise ValidationToolError(
                            "failure_basis does not name an unresolved component"
                        )
                    row["_failure_basis"] = failure_basis
            if decision == "orphan":
                if item.get("kind") != "orphan_candidates" or kind != "entry":
                    raise ValidationToolError(
                        "orphan decisions apply only to orphan-candidate rows"
                    )
                _apply_orphan_decision(
                    result, entry_id, item, action.get("unresolved")
                )
                queue.remove(item)
                counts[decision] = counts.get(decision, 0) + 1
                continue
            if decision == "drop":
                if item.get("kind") != "orphan_candidates":
                    raise ValidationToolError(
                        "only an orphan-candidates catch-all row can be dropped"
                    )
                _apply_orphan_decision(result, entry_id, item, [])
            else:
                _apply_decision_dependencies(scan, result, row, entry_id, action)
                if "notes" in action:
                    if kind != "entry" or not isinstance(action["notes"], str):
                        raise ValidationToolError(
                            "notes are supported only as text on entry rows"
                        )
                    row["notes"] = action["notes"]
                if decision == "support":
                    if kind != "summary":
                        raise ValidationToolError(
                            "support decisions apply only to Summary queue items"
                        )
                    _apply_summary_support(row, item, action, date)
                elif decision == "pass":
                    if kind != "entry":
                        raise ValidationToolError(
                            "Summary success requires a support decision"
                        )
                    for check in ("integrity", "provenance"):
                        if row.get(check) != "N/A":
                            row[check] = date
                    row["findings"] = [
                        finding
                        for finding in row.get("findings", [])
                        if finding.get("check") not in {"Integrity", "Provenance"}
                    ]
                elif decision == "fail":
                    if kind == "summary":
                        row["support_reviewed"] = True
                        row["support_evidence"] = []
                        row["entries"] = []
                        row["sections"] = []
                    else:
                        for check in ("integrity", "provenance"):
                            if row.get(check) is None:
                                row[check] = date
                    _replace_decision_findings(row, action.get("findings"))
                elif decision == "reproduced":
                    row["reproducibility"] = date
                    row["findings"] = [
                        finding
                        for finding in row.get("findings", [])
                        if finding.get("check") != "Reproducibility"
                    ]
                elif decision == "reproduction-fail":
                    findings = action.get("findings")
                    if not isinstance(findings, dict) or set(findings) != {
                        "Reproducibility"
                    }:
                        raise ValidationToolError(
                            "reproduction-fail requires one Reproducibility finding"
                        )
                    _replace_decision_findings(row, findings)
                elif decision == "not-run":
                    row["reproducibility"] = "-"
                elif decision == "not-applicable":
                    row["reproducibility"] = "N/A"
                elif decision in {"keep", "scope"}:
                    pass
            queue.remove(item)
            counts[decision] = counts.get(decision, 0) + 1
    _reconcile_semantic_dependencies(scan, result)
    counts["remaining"] = len(queue)
    return result, counts


def _collection_packet_lines(scan: Dict[str, Any], identity: str) -> List[str]:
    """Return bounded member candidates for one unresolved directory scope."""

    raw = scan.get("resolved_paths", {}).get(identity)
    if not raw:
        return [f"- Collection: `{identity}` (unresolved path)"]
    root = Path(raw)
    resolved_candidates = set()
    for child_identity, child_raw in scan.get("resolved_paths", {}).items():
        child = Path(child_raw)
        try:
            relative = child.relative_to(root)
        except ValueError:
            continue
        if relative.parts and child.is_file():
            resolved_candidates.add(relative.as_posix())
    source = "resolved child dependencies"
    root_candidates = set()
    nested_candidates = set()
    if root.is_dir():
        had_resolved = bool(resolved_candidates)
        for current, directories, files in os.walk(root):
            relative_root = Path(current).relative_to(root)
            if len(relative_root.parts) >= 2:
                directories[:] = []
            for name in files:
                relative = (relative_root / name).as_posix()
                if relative_root == Path("."):
                    root_candidates.add(relative)
                else:
                    nested_candidates.add(relative)
        source = (
            "resolved child dependencies and shallow filesystem inventory"
            if had_resolved
            else "shallow filesystem inventory"
        )
    candidates = list(
        dict.fromkeys(
            [
                *sorted(resolved_candidates),
                *sorted(root_candidates),
                *sorted(nested_candidates),
            ]
        )
    )
    preview = candidates[:80]
    suffix = f"; {len(candidates) - len(preview)} more" if len(candidates) > 80 else ""
    return [
        f"- Collection: `{identity}`",
        f"  - Candidate members from {source}: "
        f"{_packet_text('; '.join(preview) or '(none found)', 4000)}{suffix}",
    ]


# Command-line interface


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser(
        "index", help="build or refresh the repository research-log dependency index"
    )
    index.add_argument("--project-root", required=True, type=Path)
    index.add_argument("--output", type=Path)
    index.add_argument("--metrics", type=Path)
    index.add_argument("--rules-version", default=RULES_VERSION)

    scan = subparsers.add_parser(
        "scan", help="scan a research log without semantic adjudication"
    )
    scan.add_argument("--summary", required=True, type=Path)
    scan.add_argument("--output", required=True, type=Path)
    scan.add_argument("--metrics", type=Path)
    scan.add_argument("--state", type=Path, help="prior validation-state.json")
    scan.add_argument(
        "--repository-index",
        type=Path,
        help=(
            "repository dependency index; defaults to "
            f"<project>/{REPOSITORY_INDEX_FILENAME}"
        ),
    )
    scan.add_argument("--rules-version", default=RULES_VERSION)
    scan.add_argument(
        "--mode", choices=("standard", "reproduction"), default="standard"
    )
    scan.add_argument("--jobs", type=int, default=min(32, (os.cpu_count() or 1) + 4))

    template = subparsers.add_parser(
        "prepare",
        aliases=["template"],
        help="prepare mechanical results and a bounded semantic-review queue",
    )
    template.add_argument("--scan", required=True, type=Path)
    template.add_argument("--output", required=True, type=Path)
    template.add_argument("--date", required=True)
    template.add_argument(
        "--mode", choices=("standard", "reproduction"), default="standard"
    )
    template.add_argument("--rules-version", default=RULES_VERSION)

    review = subparsers.add_parser(
        "review", help="write compact context for the bounded semantic-review queue"
    )
    review.add_argument("--scan", required=True, type=Path)
    review.add_argument("--adjudication", required=True, type=Path)
    review.add_argument("--output", required=True, type=Path)
    review.add_argument(
        "--entry", help="include one queue scope, such as e003 or Summary"
    )
    review.add_argument("--kind", help="include one review-queue kind")

    decide = subparsers.add_parser(
        "decide", help="apply compact reviewed decisions to an adjudication"
    )
    decide.add_argument("--scan", required=True, type=Path)
    decide.add_argument("--adjudication", required=True, type=Path)
    decide.add_argument("--decisions", required=True, type=Path)
    decide.add_argument("--output", required=True, type=Path)

    render = subparsers.add_parser(
        "render", help="render validation records from adjudications"
    )
    render.add_argument("--scan", required=True, type=Path)
    render.add_argument("--adjudication", required=True, type=Path)
    render.add_argument("--output-dir", required=True, type=Path)

    update_summary = subparsers.add_parser(
        "update-summary",
        help="project canonical validation results into the maintained summary",
    )
    update_summary.add_argument("--summary", required=True, type=Path)
    update_summary.add_argument("--output-dir", required=True, type=Path)

    lint = subparsers.add_parser("lint", help="lint rendered validation records")
    lint.add_argument("--output-dir", required=True, type=Path)
    lint.add_argument("--scan", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the validation mechanics CLI and return a process exit status."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "index":
            project_root = args.project_root.resolve()
            output = args.output or project_root / REPOSITORY_INDEX_FILENAME
            prior_index = _load_json(output) if output.is_file() else None
            index, metrics = build_repository_dependency_index(
                project_root, prior_index, args.rules_version
            )
            if metrics["status"] != "unchanged":
                _write_json(output, index)
            if args.metrics:
                _write_json(args.metrics, metrics)
            print(json.dumps(metrics, sort_keys=True))
            return 0
        if args.command == "scan":
            prior_state = _load_json(args.state) if args.state else None
            project_root = find_project_root(args.summary.resolve())
            repository_index_path = (
                args.repository_index
                if args.repository_index is not None
                else project_root / REPOSITORY_INDEX_FILENAME
            )
            prior_index = (
                _load_json(repository_index_path)
                if repository_index_path.is_file()
                else None
            )
            repository_index, index_metrics = build_repository_dependency_index(
                project_root, prior_index, args.rules_version
            )
            if index_metrics["status"] != "unchanged":
                _write_json(repository_index_path, repository_index)
            scan, metrics = scan_log(
                args.summary,
                jobs=args.jobs,
                prior_state=prior_state,
                repository_index=repository_index,
                rules_version=args.rules_version,
                mode=args.mode,
            )
            metrics["repository_index_status"] = index_metrics["status"]
            metrics["repository_index_elapsed_seconds"] = round(
                index_metrics["elapsed_seconds"], 6
            )
            _write_json(args.output, scan)
            if args.metrics:
                _write_json(args.metrics, metrics)
            print(json.dumps(metrics, sort_keys=True))
            return 0
        if args.command in {"prepare", "template"}:
            scan = _load_json(args.scan)
            template = make_adjudication_template(
                scan, args.date, args.rules_version, mode=args.mode
            )
            _write_json(args.output, template)
            rows = [row for entry in template["entries"] for row in entry["targets"]]
            print(
                json.dumps(
                    {
                        "entry_rows": len(rows),
                        "summary_rows": len(template["summary"]),
                        "mechanical_integrity_results": sum(
                            row["integrity"] is not None for row in rows
                        ),
                        "mechanical_provenance_results": sum(
                            row["provenance"] is not None for row in rows
                        ),
                        "review_queue": len(template["review_queue"]),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "review":
            scan = _load_json(args.scan)
            adjudication = _load_json(args.adjudication)
            packet, counts = make_review_packet(
                scan, adjudication, entry=args.entry, kind=args.kind
            )
            _write_text(args.output, packet)
            print(
                json.dumps(
                    {
                        "review_queue": sum(counts.values()),
                        "kinds": counts,
                        "packet_bytes": len(packet.encode("utf-8")),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "decide":
            scan = _load_json(args.scan)
            adjudication = _load_json(args.adjudication)
            decisions = _load_json(args.decisions)
            updated, counts = apply_review_decisions(scan, adjudication, decisions)
            _write_json(args.output, updated)
            print(json.dumps(counts, sort_keys=True))
            return 0
        if args.command == "render":
            scan = _load_json(args.scan)
            adjudication = _load_json(args.adjudication)
            counts = render_records(adjudication, scan, args.output_dir)
            print(json.dumps(counts, sort_keys=True))
            return 0
        if args.command == "update-summary":
            result = update_summary_validation(args.summary, args.output_dir)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "lint":
            expected = None
            if args.scan:
                expected = _load_json(args.scan).get("entry_order")
            result = lint_records(args.output_dir, expected_entry_order=expected)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
    except (OSError, ValidationToolError) as exc:
        print(f"research_log_validation: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
