"""Markdown structure, evidence-index, and data-index discovery.

This module owns deterministic parsing of maintained research-log Markdown and
its adjacent CSV records. It does not resolve evidence sources or assign
validation outcomes.
"""

from __future__ import annotations

import csv
import hashlib
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from research_log_data_index import (
    TOKEN_RE,
    DataIndexError,
    inspect_data_index,
)

from .evidence import NUMBER_RE
from .inventory import display_path


class MarkdownDiscoveryError(ValueError):
    """Raised when maintained Markdown or citation text cannot be decoded."""


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?P<image>!)?\[(?P<label>[^\]]*)\]\((?P<target>[^)]+)\)")
FENCE_RE = re.compile(r"^\s*(```+|~~~+)\s*([^\s`]*)")
ENTRY_ID_RE = re.compile(r"^e\d+[a-z]?$", re.IGNORECASE)
CITATION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_:.+/-]+)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
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
EXTERNAL_SCHEMES = {"http", "https", "s3", "gs", "doi", "ftp"}
ENTRY_EVIDENCE_HEADER = (
    "entry",
    "section",
    "kind",
    "evidence",
    "sources",
    "transformation",
)
SUMMARY_EVIDENCE_HEADER = ("statistic", "entry", "section", "transformation")
EVIDENCE_KINDS = frozenset({"statistic", "table", "output"})
BLOCK_LABEL_RE = re.compile(r"^\s*`([A-Za-z][A-Za-z -]*):`\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
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


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise MarkdownDiscoveryError(f"file is not valid UTF-8: {path}") from exc


def _is_closing_fence(line: str, opening: str) -> bool:
    """Return whether ``line`` closes the exact opening fence family."""

    stripped = line.lstrip()
    marker = re.escape(opening[0])
    return re.fullmatch(rf"{marker}{{{len(opening)},}}\s*", stripped) is not None


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


def _section_definitions_and_lines(
    lines: Sequence[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    definitions: List[Dict[str, Any]] = [
        {"line": 1, "section": _title(lines, "Document"), "labels": []}
    ]
    sections = []
    current = 0
    active_label: Optional[str] = None
    fence_marker: Optional[str] = None
    for number, line in enumerate(lines, 1):
        fence = FENCE_RE.match(line)
        if fence_marker is None and fence:
            fence_marker = fence.group(1)
        elif fence_marker and _is_closing_fence(line, fence_marker):
            fence_marker = None

        heading = None if fence_marker else HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            definitions[current]["end_line"] = number - 1
            definitions.append(
                {"line": number, "section": heading.group(2).strip(), "labels": []}
            )
            current += 1
            active_label = None
        label = None if fence_marker else BLOCK_LABEL_RE.match(line)
        if label:
            active_label = label.group(1)
            definitions[current]["labels"].append(active_label)
        sections.append(
            {
                "line": number,
                "section": definitions[current]["section"],
                "section_index": current,
                "block_label": active_label,
            }
        )
    definitions[current]["end_line"] = len(lines)
    return definitions, sections


def _classify_labels(labels: Sequence[str]) -> tuple[str, List[str]]:
    unique = set(labels)
    errors = []
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
        return ("synthesis" if not errors else "invalid"), errors
    if unique & {"Steps", "Results"}:
        missing = sorted({"Steps", "Results"} - unique)
        if missing:
            errors.append("experimental section is missing: " + ", ".join(missing))
        unsupported = sorted(unique - EXPERIMENTAL_LABELS)
        if unsupported:
            errors.append(
                "experimental section has incompatible labels: "
                + ", ".join(unsupported)
            )
        return ("experimental" if not errors else "invalid"), errors
    if not unique:
        return "prose", errors
    errors.append(
        "labeled section is neither experimental nor synthesis: "
        + ", ".join(sorted(unique))
    )
    return "invalid", errors


def section_ranges(lines: Sequence[str]) -> List[Dict[str, Any]]:
    """Partition Markdown into heading ranges with active validation labels."""

    definitions, sections = _section_definitions_and_lines(lines)
    for definition in definitions:
        definition["type"], errors = _classify_labels(definition["labels"])
        definition["errors"] = errors

    for item in sections:
        definition = definitions[item["section_index"]]
        item["section_type"] = definition["type"]
        item["section_errors"] = definition["errors"]
    return sections


def section_definitions(
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
    duplicate_counts: Dict[str, int] = {}
    for definition in definitions:
        normalized = " ".join(definition["section"].split()).casefold()
        duplicate_counts[normalized] = duplicate_counts.get(normalized, 0) + 1
        semantic_payload = (
            f"{normalized}\0{duplicate_counts[normalized]}".encode("utf-8")
        )
        start = definition["line"] - 1
        end = definition["end_line"]
        content_lines = [
            line
            for line, scope in zip(lines[start:end], sections[start:end])
            if scope.get("block_label") != "Validation"
        ]
        while content_lines and not content_lines[-1].strip():
            content_lines.pop()
        content = "\n".join(content_lines).encode("utf-8")
        definition["semantic_identity"] = hashlib.sha256(
            semantic_payload
        ).hexdigest()
        definition["content_identity"] = hashlib.sha256(content).hexdigest()
    return definitions


def _table_delimiter(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _presented_numeric_expression(value: str) -> bool:
    """Return whether one inline-code span is a marked numerical statistic."""

    value = value.strip()
    rejected = (
        not NUMBER_RE.search(value)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        or value.startswith("--")
        or re.match(r"^\d+\s*PH(?:\b|$)", value, re.IGNORECASE)
        or re.search(r"[A-Za-z_][A-Za-z0-9_.-]*\s*=", value)
        or re.fullmatch(r"[0-9a-fA-F]{12,}", value)
        or UUID_RE.fullmatch(value)
    )
    if rejected:
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
        marker = match.group(1)
        language = match.group(2).lower()
        index += 1
        content: List[str] = []
        while index < len(lines) and not _is_closing_fence(lines[index], marker):
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


def _summary_body(
    lines: Sequence[str],
) -> Iterable[tuple[int, str, Dict[int, str]]]:
    in_summary = False
    heading_path: Dict[int, str] = {}
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
        yield number, line, dict(heading_path)


def _finish_summary_item(
    items: List[Dict[str, Any]], current: Optional[Dict[str, Any]]
) -> None:
    if current is None:
        return
    current["text"] = " ".join(current.pop("parts"))
    items.append(current)


def _summary_items(lines: Sequence[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for number, line, heading_path in _summary_body(lines):
        bullet = re.match(r"^\s*-\s+(.+)$", line)
        if bullet:
            _finish_summary_item(items, current)
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
            _finish_summary_item(items, current)
            current = None
    _finish_summary_item(items, current)
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

    from .validation_notes import retention_scope

    notes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        current["text"] = " ".join(current.pop("parts"))
        current["sha256"] = hashlib.sha256(
            current["text"].encode("utf-8")
        ).hexdigest()
        scope = retention_scope(current["text"])
        if scope is not None:
            current["retention_scope"] = scope
        notes.append(current)
        current = None

    for number, (line, section) in enumerate(zip(lines, sections), 1):
        if section.get("block_label") != "Validation":
            finish()
            continue
        if BLOCK_LABEL_RE.match(line):
            continue
        text = line.strip()
        if not text:
            continue
        if re.match(r"^-\s+", text):
            finish()
        if current is None:
            current = {
                "section": section["section"],
                "line": number,
                "parts": [],
            }
        current["parts"].append(text)
    finish()
    return notes


def _identity_lines(items: Sequence[Mapping[str, Any]], prefix: str) -> set[int]:
    lines: set[int] = set()
    for item in items:
        match = re.match(rf"{prefix}:L(\d+)-L(\d+)", item["identity"])
        if match:
            lines.update(range(int(match.group(1)), int(match.group(2)) + 1))
    return lines


def _line_references(
    path: Path,
    number: int,
    line: str,
    section: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    shared = {
        "line": number,
        "section": section["section"],
        "section_type": section["section_type"],
    }
    links = [
        {
            **shared,
            "block_label": section["block_label"],
            "label": match.group("label"),
            "image": bool(match.group("image")),
            **resolve_reference(match.group("target"), path),
        }
        for match in LINK_RE.finditer(line)
    ]
    inline_paths = []
    for match in INLINE_CODE_RE.finditer(line):
        candidate = match.group(1).strip().split("=", 1)[-1]
        suffix = Path(candidate.split("#", 1)[0]).suffix.lower()
        if (
            (suffix in PATH_SUFFIXES or "/" in candidate)
            and not candidate.startswith("--")
            and not any(char.isspace() for char in candidate)
        ):
            inline_paths.append(
                {
                    **shared,
                    "block_label": section["block_label"],
                    **resolve_reference(candidate, path),
                }
            )
    citations = [
        {**shared, "key": match.group(1).rstrip(".,;:)")}
        for match in CITATION_RE.finditer(line)
    ]
    return links, inline_paths, citations


def _line_statistics(
    number: int,
    line: str,
    section: Mapping[str, Any],
    current_h2: str,
    prose_context: Mapping[str, Any] | None,
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    if HEADING_RE.match(line) or prose_context is None:
        return None, [], []
    marked = [
        match.group(1).strip()
        for match in INLINE_CODE_RE.finditer(line)
        if _presented_numeric_expression(match.group(1))
    ]
    numeric = (
        {
            "line": number,
            "section": section["section"],
            "section_type": section["section_type"],
            "text": line.strip(),
            "values": [value for item in marked for value in NUMBER_RE.findall(item)],
        }
        if marked
        else None
    )
    items = [
        {
            "kind": "statistic",
            "section": section["section"],
            "base_selector": value,
            "line": number,
            "end_line": int(prose_context["end_line"]),
            "context": str(prose_context["text"]),
        }
        for value in marked
    ]
    presented = items if section["section_type"] == "experimental" else []
    summary = [dict(item) for item in items] if current_h2 == "Summary" else []
    return numeric, presented, summary


def _prose_contexts(
    lines: Sequence[str], excluded_lines: set[int]
) -> dict[int, dict[str, Any]]:
    """Map prose lines to their complete contiguous Markdown paragraph."""

    contexts: dict[int, dict[str, Any]] = {}
    block: list[tuple[int, str]] = []

    def flush() -> None:
        if not block:
            return
        context = {
            "end_line": block[-1][0],
            "text": " ".join(line.strip() for _, line in block),
        }
        for number, _ in block:
            contexts[number] = context
        block.clear()

    for number, line in enumerate(lines, 1):
        if (
            number in excluded_lines
            or not line.strip()
            or HEADING_RE.match(line)
            or BLOCK_LABEL_RE.match(line)
        ):
            flush()
            continue
        if LIST_ITEM_RE.match(line):
            flush()
        block.append((number, line))
    flush()
    return contexts


def _scan_markdown_lines(
    path: Path,
    lines: Sequence[str],
    sections: Sequence[Mapping[str, Any]],
    fenced_lines: set[int],
    table_lines: set[int],
) -> Dict[str, List[Dict[str, Any]]]:
    found: Dict[str, List[Dict[str, Any]]] = {
        "links": [],
        "inline_paths": [],
        "citations": [],
        "numeric": [],
        "presented": [],
        "summary": [],
    }
    prose_contexts = _prose_contexts(lines, fenced_lines | table_lines)
    current_h2 = ""
    for number, line in enumerate(lines, 1):
        if number in fenced_lines:
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            current_h2 = heading.group(2).strip()
        section = sections[number - 1]
        links, inline_paths, citations = _line_references(
            path, number, line, section
        )
        found["links"].extend(links)
        found["inline_paths"].extend(inline_paths)
        found["citations"].extend(citations)
        numeric, presented, summary = _line_statistics(
            number,
            line,
            section,
            current_h2,
            prose_contexts.get(number),
        )
        if numeric is not None:
            found["numeric"].append(numeric)
        found["presented"].extend(presented)
        found["summary"].extend(summary)
    return found


def _assign_item_identities(collection: Sequence[Dict[str, Any]]) -> None:
    per_line: Dict[Tuple[str, int], int] = {}
    for item in collection:
        key = (item["kind"], item["line"])
        per_line[key] = per_line.get(key, 0) + 1
        item["identity"] = f"{item['kind']}:L{item['line']}:{per_line[key]}"
        item.setdefault("end_line", item["line"])
        item.setdefault("text", item["context"])


def parse_markdown(path: Path) -> Dict[str, Any]:
    """Extract deterministic locations and validation candidates from Markdown."""

    return parse_markdown_text(path, _read_utf8(path))


def parse_markdown_text(
    path: Path, text: str, *, include_validation_notes: bool = True
) -> Dict[str, Any]:
    """Extract Markdown facts from one caller-owned stable UTF-8 observation."""

    lines = text.splitlines()
    sections = section_ranges(lines)
    fenced = _fenced_blocks(lines, sections)
    fenced_lines = _identity_lines(fenced, "fence")
    tables = _table_blocks(lines, sections, fenced_lines)
    table_lines = _identity_lines(tables, "table")
    found = _scan_markdown_lines(path, lines, sections, fenced_lines, table_lines)

    presented_items = [
        *found["presented"],
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
    found["summary"].sort(key=lambda item: item["line"])
    _assign_presented_selectors(found["summary"], summary=True)
    for collection in (presented_items, found["summary"]):
        _assign_item_identities(collection)

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
            if match and number not in fenced_lines
        ],
        "sections": section_definitions(lines, sections),
        "links": found["links"],
        "inline_paths": found["inline_paths"],
        "tables": tables,
        "fenced_blocks": fenced,
        "numeric_evidence": found["numeric"],
        "presented_items": presented_items,
        "summary_statistics": found["summary"],
        "citations": found["citations"],
        "summary_items": _summary_items(lines),
        "validation_notes": (
            _validation_notes(lines, sections) if include_validation_notes else []
        ),
    }


def bibtex_keys(path: Path) -> List[str]:
    """Return the unique citation keys from an optional UTF-8 BibTeX file."""

    if not path.is_file():
        return []
    return sorted(set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", _read_utf8(path))))


def data_index(entry_path: Path) -> Dict[str, Any]:
    """Load the nearest entry-level data index and retain parse diagnostics."""

    candidates = [entry_path.parent / "data.csv", entry_path.parent.parent / "data.csv"]
    index = next((candidate for candidate in candidates if candidate.is_file()), None)
    if index is None:
        return {"path": None, "rows": [], "errors": [], "duplicates": []}
    errors: List[str] = []
    rows: List[Dict[str, str]] = []
    try:
        report = inspect_data_index(index)
        rows = report.rows
        errors = report.errors
        duplicates = report.duplicates
    except DataIndexError as exc:
        errors.append(str(exc))
        duplicates = []
    return {
        "path": index.resolve().as_posix(),
        "rows": rows,
        "errors": errors,
        "duplicates": duplicates,
    }


def data_index_path(data: Mapping[str, Any], entry_path: Path) -> Path:
    """Return the resolved or expected data-index path for one entry."""

    raw = data.get("path")
    return Path(raw) if raw else entry_path.parent / "data.csv"


def expand_local_tokens(
    value: str,
    entry_path: Path,
    project_root: Path,
    data: Mapping[str, Any],
) -> Optional[Path]:
    """Resolve project, log, and data-index tokens in one local path value."""

    rows = {row.get("name", ""): row for row in data["rows"]}
    duplicates = set(data["duplicates"])
    expanded = value
    for name in TOKEN_RE.findall(value):
        replacement: Optional[str]
        if name == "project":
            replacement = project_root.as_posix()
        elif name == "log":
            replacement = entry_path.parents[2].as_posix()
        elif name in rows and name not in duplicates:
            reference = resolve_reference(
                rows[name].get("location", ""), data_index_path(data, entry_path)
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


def resolve_evidence_source(
    specification: Mapping[str, str],
    entry_path: Path,
    project_root: Path,
    data: Mapping[str, Any],
) -> Dict[str, Any]:
    """Resolve one evidence source into a stable validation identity."""

    raw = specification["source"]
    path = expand_local_tokens(raw, entry_path, project_root, data)
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


def read_evidence_csv(path: Path, header: Sequence[str]) -> Dict[str, Any]:
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


def _source_path_error(source: str, line: str) -> Optional[str]:
    token = TOKEN_RE.fullmatch(source)
    if source.startswith("<log>/"):
        return None
    if token:
        return (
            f"line {line}: reserved token <{token.group(1)}> requires a path"
            if token.group(1) in {"log", "project"}
            else None
        )
    source_path = Path(source)
    if source_path.is_absolute() or urllib.parse.urlparse(source).scheme:
        return (
            f"line {line}: source must use an entry-relative path, "
            "<log>/ path, or <name> token"
        )
    if TOKEN_RE.search(source):
        return f"line {line}: source token must be exact"
    if ".." in source_path.parts:
        return f"line {line}: relative source must not traverse parents"
    return None


def _parse_evidence_source(
    specification: str, line: str
) -> tuple[Optional[Dict[str, str]], List[str]]:
    if not specification:
        return None, [f"line {line}: empty source specification"]
    parts = specification.split(" :: ", 1)
    source = parts[0].strip()
    locator = parts[1].strip() if len(parts) == 2 else ""
    if not source:
        return None, [f"line {line}: source is empty"]
    errors = []
    if len(parts) == 2 and not locator:
        errors.append(f"line {line}: source locator is empty")
    path_error = _source_path_error(source, line)
    if path_error:
        errors.append(path_error)
    return {"source": source, "locator": locator}, errors


def parse_evidence_sources(
    raw: str, line: str
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse entry evidence sources without resolving their content."""

    parsed = []
    errors = []
    for specification in (part.strip() for part in raw.split(" | ")):
        source, source_errors = _parse_evidence_source(specification, line)
        if source is not None:
            parsed.append(source)
        errors.extend(source_errors)
    return parsed, errors


def entry_evidence_record(path: Path) -> Dict[str, Any]:
    """Read and structurally validate one entry-folder evidence record."""

    record = read_evidence_csv(path, ENTRY_EVIDENCE_HEADER)
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
        source_specs, source_errors = parse_evidence_sources(row["sources"], line)
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


def summary_evidence_record(path: Path) -> Dict[str, Any]:
    """Read and structurally validate one summary evidence record."""

    record = read_evidence_csv(path, SUMMARY_EVIDENCE_HEADER)
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
