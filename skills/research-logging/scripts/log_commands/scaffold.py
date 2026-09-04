"""Transactional maintained-log and entry scaffolding actions."""

from __future__ import annotations

import datetime as dt
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .context import (
    EntryContext,
    LogContext,
    LogCreationContext,
    entry_number,
    parse_entry_directory_name,
    parse_entry_document_name,
)
from .model import ActionError, ActionResult, AddArguments, InitArguments
from .storage import (
    atomic_create_text,
    atomic_write_text,
    create_symlink,
    entry_lock,
    log_creation_lock,
    log_lock,
    sync_directory,
)

MAX_SUMMARY_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_TITLE_BYTES = 512
MAX_SLUG_BYTES = 96
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LINK_TARGET = r"(?P<target><[^<>\r\n]+>|[^()\s\r\n]+)"
SINGLE_ENTRY_RE = re.compile(
    rf"^- `(?P<date>[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})` "
    rf"\[(?P<title>[^\]\r\n]+)\]\({LINK_TARGET}\)$"
)
SPLIT_PARENT_RE = re.compile(
    r"^- `(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})` (?P<title>.+):$"
)
SPLIT_CHILD_RE = re.compile(rf"^  - \[(?P<title>[^\]\r\n]+)\]\({LINK_TARGET}\)$")

AI_DISCLOSURE = (
    "The researcher has led and reviewed the scientific work throughout, chosen the\n"
    "methods and next steps, and made or approved the observations and decisions\n"
    "recorded in this log. Under the researcher's direction, agents have mainly\n"
    "helped implement and run code, document the work, check calculations and\n"
    "outputs, and draft observations for review. They have also helped find and\n"
    "summarize relevant research, explore solutions, and challenge the researcher's\n"
    "reasoning. The researcher has checked all claims and conclusions against\n"
    "original sources, simulations, or saved data. Generative AI has not been used as\n"
    "scientific evidence. Reported computational results have come from code run on\n"
    "saved source data with documented settings. The source data, settings, and\n"
    "outputs have been kept so the results can be checked and reproduced."
)


@dataclass(frozen=True)
class EntryObservation:
    """One existing stable entry and its complete summary-linked documents."""

    id: str
    date: str
    slug: str
    root: Path
    documents: tuple[Path, ...]


@dataclass(frozen=True)
class SummaryEntryObservation:
    """One summary-declared entry identity without filesystem resolution."""

    id: str
    date: str
    title: str
    slug: str
    documents: tuple[Path, ...]


@dataclass(frozen=True)
class SummarySection:
    """The exact byte-preserving insertion boundary for ``## Entries``."""

    text: str
    body_start: int
    body_end: int


@dataclass(frozen=True)
class EntryScaffold:
    """One completely preflighted entry scaffold ready for publication."""

    root: Path
    document: Path
    document_text: str
    runner: Path
    runner_source: Path
    summary: str


def initialize(log: LogCreationContext, arguments: InitArguments) -> ActionResult:
    """Create one empty maintained summary and matching entries directory."""

    title = _title(arguments.title, "log.title.invalid")
    summary = _initial_summary(log, title)
    paths = tuple(
        path.as_posix() for path in (log.summary, log.root, log.root / "entries")
    )
    with log_creation_lock(log):
        _require_new_log_target(log)
        if arguments.dry_run:
            return _result("init", "dry-run", True, paths)
        created: list[Path] = []
        try:
            _make_directory(log.root, created)
            entries = log.root / "entries"
            _make_directory(entries, created)
            atomic_create_text(log.summary, summary)
            created.append(log.summary)
        except OSError as error:
            _raise_publication_failure("init", error, created)
    return _result("init", "changed", True, paths)


def add_entry(log: LogContext, arguments: AddArguments) -> ActionResult:
    """Allocate and create one minimal entry, committing its summary link last."""

    date = _date(arguments.date)
    title = _title(arguments.title, "entry.title.invalid")
    slug = _slug(arguments.slug)
    with log_lock(log):
        section, entries = _inventory(log)
        if any(item.date == date and item.slug == slug for item in entries):
            raise ActionError(
                "entry.scaffold.conflict",
                f"logical entry already exists: {date}-{slug}",
            )
        entry_id = _next_entry_id(entries)
        entry_root = log.root / "entries" / f"{date}-{entry_id}-{slug}"
        document = entry_root / f"{entry_id}.md"
        runner = entry_root / "pyrun"
        _require_new_entry_targets(entry_root, document, runner)
        source = _runner_source()
        planned = EntryScaffold(
            root=entry_root,
            document=document,
            document_text=f"# {date}: {title}\n",
            runner=runner,
            runner_source=source,
            summary=_insert_entry(section, log, date, title, document),
        )
        paths = tuple(
            path.as_posix() for path in (entry_root, document, runner, log.summary)
        )
        with entry_lock(EntryContext(log, entry_id, entry_root)):
            if arguments.dry_run:
                return _result("add", "dry-run", True, paths)
            _publish_entry(log, planned)
    return _result("add", "changed", True, paths)


def observe_entries(log: LogContext) -> tuple[EntryObservation, ...]:
    """Return the summary-consistent stable entry inventory."""

    return _inventory(log)[1]


def observe_physical_entries(log: LogContext) -> tuple[EntryObservation, ...]:
    """Return canonical physical entries while the summary is pre-edited."""

    observed = _entry_directories(log)
    return tuple(
        EntryObservation(
            item.id,
            item.date,
            item.slug,
            item.root,
            tuple(sorted(_entry_documents(log, item.root, item.id))),
        )
        for item in sorted(observed.values(), key=lambda value: _entry_number(value.id))
    )


def observe_summary_entries(log: LogContext) -> tuple[SummaryEntryObservation, ...]:
    """Return the agent-authored summary projection before identity publication."""

    text = _read_summary(log.summary)
    section = _summary_section(log, text)
    return _listed_summary_entries(log, section)


def validate_entry_date(value: str) -> str:
    """Return one canonical entry date or raise the public contract error."""

    return _date(value)


def validate_entry_slug(value: str) -> str:
    """Return one canonical entry slug or raise the public contract error."""

    return _slug(value)


def validate_entry_title(value: str) -> str:
    """Return one bounded entry title or raise the public contract error."""

    return _title(value, "entry.title.invalid")


def _require_new_log_target(log: LogCreationContext) -> None:
    summary_exists = os.path.lexists(log.summary)
    root_exists = os.path.lexists(log.root)
    if summary_exists != root_exists:
        raise ActionError(
            "log.scaffold.residue",
            "partial log scaffold requires explicit Repair",
        )
    if summary_exists:
        raise ActionError("log.scaffold.conflict", "logical log already exists")


def _require_new_entry_targets(*paths: Path) -> None:
    existing = [path for path in paths if os.path.lexists(path)]
    if existing:
        raise ActionError(
            "entry.scaffold.conflict",
            f"entry scaffold target already exists: {existing[0]}",
        )


def _publish_entry(log: LogContext, planned: EntryScaffold) -> None:
    before = log.summary.read_bytes()
    created: list[Path] = []
    try:
        _make_directory(planned.root, created)
        atomic_create_text(planned.document, planned.document_text)
        created.append(planned.document)
        target = os.path.relpath(planned.runner_source, start=planned.root)
        create_symlink(planned.runner, target)
        created.append(planned.runner)
        atomic_write_text(log.summary, planned.summary)
    except OSError as error:
        rollback_errors = _restore_summary(log.summary, before, planned.summary)
        rollback_errors.extend(_rollback_created(created))
        _raise_action_failure("add", error, rollback_errors)


def _inventory(log: LogContext) -> tuple[SummarySection, tuple[EntryObservation, ...]]:
    text = _read_summary(log.summary)
    section = _summary_section(log, text)
    listed = _listed_documents(log, section)
    observed = _entry_directories(log)
    if set(listed) != set(observed):
        raise ActionError(
            "entry.scaffold.residue",
            "summary and entry directories disagree; explicit Repair is required",
        )
    entries: list[EntryObservation] = []
    for entry_id, item in observed.items():
        documents = tuple(sorted(_entry_documents(log, item.root, entry_id)))
        if set(documents) != set(listed[entry_id][1]):
            raise ActionError(
                "entry.scaffold.residue",
                f"summary and entry documents disagree for {entry_id}",
            )
        if item.date != listed[entry_id][0]:
            raise ActionError(
                "entry.identity.inconsistent", f"entry date disagrees for {entry_id}"
            )
        entries.append(
            EntryObservation(item.id, item.date, item.slug, item.root, documents)
        )
    ordered = tuple(sorted(entries, key=lambda item: _entry_number(item.id)))
    if tuple(listed) != tuple(item.id for item in ordered):
        raise ActionError(
            "entry.identity.inconsistent", "summary entries are not in numeric order"
        )
    return section, ordered


def _summary_section(log: LogContext, text: str) -> SummarySection:
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("# "):
        raise ActionError("summary.scaffold.invalid", "summary has no level-one title")
    validation = f"Validation: [latest completed report]({log.root.name}/validation.md)"
    accepted = {validation, validation.replace("(", "(<", 1).replace(")", ">)", 1)}
    if not any(line.rstrip("\r\n") in accepted for line in lines[1:3]):
        raise ActionError(
            "summary.scaffold.invalid", "summary validation link is not canonical"
        )
    headings = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == "## Entries"
    ]
    if len(headings) != 1:
        raise ActionError(
            "summary.scaffold.invalid",
            "summary requires exactly one ## Entries section",
        )
    heading = headings[0]
    following = next(
        (
            index
            for index in range(heading + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return SummarySection(text, offsets[heading + 1], offsets[following])


def _listed_documents(
    log: LogContext, section: SummarySection
) -> dict[str, tuple[str, tuple[Path, ...]]]:
    body = section.text[section.body_start : section.body_end]
    result: dict[str, tuple[str, list[Path]]] = {}
    parent_date: str | None = None
    parent_has_child = False
    for raw in body.splitlines():
        if not raw:
            continue
        single = SINGLE_ENTRY_RE.fullmatch(raw)
        parent = SPLIT_PARENT_RE.fullmatch(raw)
        child = SPLIT_CHILD_RE.fullmatch(raw)
        if single is not None:
            if parent_date is not None and not parent_has_child:
                raise ActionError(
                    "entry.identity.invalid", "split entry has no documents"
                )
            parent_date = None
            parent_has_child = False
            _add_listed_document(
                log, result, single.group("date"), single.group("target")
            )
        elif parent is not None:
            if parent_date is not None and not parent_has_child:
                raise ActionError(
                    "entry.identity.invalid", "split entry has no documents"
                )
            parent_date = _date(parent.group("date"))
            parent_has_child = False
        elif child is not None and parent_date is not None:
            _add_listed_document(log, result, parent_date, child.group("target"))
            parent_has_child = True
        else:
            raise ActionError("entry.identity.invalid", f"invalid Entries item: {raw}")
        if len(result) > MAX_ENTRIES:
            raise ActionError("entry.identity.invalid", "entry count exceeds its bound")
    if parent_date is not None and not parent_has_child:
        raise ActionError("entry.identity.invalid", "split entry has no documents")
    return {key: (value[0], tuple(value[1])) for key, value in result.items()}


def _listed_summary_entries(
    log: LogContext, section: SummarySection
) -> tuple[SummaryEntryObservation, ...]:
    body = section.text[section.body_start : section.body_end]
    rows: dict[str, tuple[str, str, str, list[Path]]] = {}
    parent_date: str | None = None
    parent_title: str | None = None
    parent_has_child = False
    for raw in body.splitlines():
        if not raw:
            continue
        single = SINGLE_ENTRY_RE.fullmatch(raw)
        parent = SPLIT_PARENT_RE.fullmatch(raw)
        child = SPLIT_CHILD_RE.fullmatch(raw)
        if single is not None:
            _require_completed_parent(parent_date, parent_has_child)
            parent_date = parent_title = None
            parent_has_child = False
            _add_summary_row(
                log,
                rows,
                single.group("date"),
                single.group("title"),
                single.group("target"),
            )
        elif parent is not None:
            _require_completed_parent(parent_date, parent_has_child)
            parent_date = _date(parent.group("date"))
            parent_title = parent.group("title")
            parent_has_child = False
        elif child is not None and parent_date is not None and parent_title is not None:
            _add_summary_row(
                log, rows, parent_date, parent_title, child.group("target")
            )
            parent_has_child = True
        else:
            raise ActionError("entry.identity.invalid", f"invalid Entries item: {raw}")
    _require_completed_parent(parent_date, parent_has_child)
    values = tuple(
        SummaryEntryObservation(key, date, title, slug, tuple(documents))
        for key, (date, title, slug, documents) in rows.items()
    )
    if tuple(item.id for item in values) != tuple(
        sorted((item.id for item in values), key=_entry_number)
    ):
        raise ActionError(
            "entry.identity.inconsistent", "summary entries are not in numeric order"
        )
    return values


def _require_completed_parent(date: str | None, has_child: bool) -> None:
    if date is not None and not has_child:
        raise ActionError("entry.identity.invalid", "split entry has no documents")


def _add_summary_row(
    log: LogContext,
    rows: dict[str, tuple[str, str, str, list[Path]]],
    date: str,
    title: str,
    raw_target: str,
) -> None:
    listed: dict[str, tuple[str, list[Path]]] = {}
    _add_listed_document(log, listed, date, raw_target)
    entry_id, (normalized_date, documents) = next(iter(listed.items()))
    folder = parse_entry_directory_name(documents[0].parent.name)
    assert folder is not None
    current = rows.setdefault(
        entry_id, (normalized_date, title, folder.slug, [])
    )
    if current[:3] != (normalized_date, title, folder.slug):
        raise ActionError(
            "entry.identity.inconsistent", f"summary identity disagrees for {entry_id}"
        )
    if documents[0] in current[3]:
        raise ActionError(
            "entry.identity.inconsistent", f"duplicate entry target: {raw_target}"
        )
    current[3].append(documents[0])


def _add_listed_document(
    log: LogContext,
    result: dict[str, tuple[str, list[Path]]],
    date: str,
    raw_target: str,
) -> None:
    date = _date(date)
    target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
    target = urllib.parse.unquote(target)
    pure = PurePosixPath(target)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 4:
        raise ActionError("entry.identity.invalid", f"invalid entry target: {target}")
    if pure.parts[:2] != (log.root.name, "entries"):
        raise ActionError("entry.identity.invalid", f"invalid entry target: {target}")
    folder = parse_entry_directory_name(pure.parts[2])
    document = parse_entry_document_name(pure.parts[3])
    if folder is None or document is None or folder.id != document.id:
        raise ActionError("entry.identity.invalid", f"invalid entry target: {target}")
    entry_id = folder.id
    if folder.date != date:
        raise ActionError(
            "entry.identity.inconsistent", f"entry date disagrees for {entry_id}"
        )
    path = log.summary.parent.joinpath(*pure.parts)
    current = result.setdefault(entry_id, (date, []))
    if current[0] != date or path in current[1]:
        raise ActionError(
            "entry.identity.inconsistent", f"duplicate entry target: {target}"
        )
    current[1].append(path)


def _entry_directories(log: LogContext) -> dict[str, EntryObservation]:
    entries_root = log.root / "entries"
    if entries_root.is_symlink() or not entries_root.is_dir():
        raise ActionError("entry.scaffold.residue", "log has no regular entries root")
    result: dict[str, EntryObservation] = {}
    for path in sorted(entries_root.iterdir()):
        if not path.is_symlink() and not path.is_dir():
            continue
        identity = parse_entry_directory_name(path.name)
        if identity is None or path.is_symlink() or not path.is_dir():
            raise ActionError(
                "entry.identity.invalid", f"invalid entry directory: {path}"
            )
        entry_date = _date(identity.date)
        entry_id = identity.id
        if entry_id in result:
            raise ActionError(
                "entry.identity.inconsistent", f"duplicate entry ID: {entry_id}"
            )
        result[entry_id] = EntryObservation(
            entry_id, entry_date, identity.slug, path, ()
        )
    return result


def _entry_documents(log: LogContext, root: Path, entry_id: str) -> list[Path]:
    documents: list[Path] = []
    for path in sorted(root.glob("*.md")):
        identity = parse_entry_document_name(path.name)
        if (
            identity is None
            or identity.id != entry_id
            or path.is_symlink()
            or not path.is_file()
        ):
            raise ActionError(
                "entry.identity.invalid", f"invalid entry document: {path}"
            )
        documents.append(path)
    if not documents:
        raise ActionError(
            "entry.scaffold.residue", f"entry has no document: {entry_id}"
        )
    return documents


def _insert_entry(
    section: SummarySection,
    log: LogContext,
    date: str,
    title: str,
    document: Path,
) -> str:
    body = section.text[section.body_start : section.body_end]
    content = body.rstrip("\r\n")
    suffix = body[len(content) :]
    if not suffix:
        raise ActionError(
            "summary.scaffold.invalid", "Entries section lacks a separator"
        )
    relative = document.relative_to(log.summary.parent).as_posix()
    target = _markdown_target(relative)
    item = f"- `{date}` [{title}]({target})"
    inserted = f"{content}\n{item}" if content else f"\n{item}\n"
    return (
        section.text[: section.body_start]
        + inserted
        + suffix
        + section.text[section.body_end :]
    )


def _initial_summary(log: LogCreationContext, title: str) -> str:
    validation_target = _markdown_target(f"{log.root.name}/validation.md")
    return (
        f"# {title}\n\n"
        f"Validation: [latest completed report]({validation_target})\n\n"
        "## Contents\n\n"
        "- [Entries](#entries)\n"
        "- [Summary](#summary)\n"
        "- [AI Use](#ai-use)\n\n"
        "## Entries\n\n"
        "## Summary\n\n"
        "## AI Use\n\n"
        f"{AI_DISCLOSURE}\n"
    )


def _read_summary(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ActionError("summary.scaffold.invalid", f"summary is unavailable: {path}")
    payload = path.read_bytes()
    if len(payload) > MAX_SUMMARY_BYTES:
        raise ActionError("summary.scaffold.invalid", "summary exceeds its size bound")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ActionError("summary.scaffold.invalid", "summary is not UTF-8") from error


def _runner_source() -> Path:
    source = Path(__file__).resolve().parents[1] / "pyrun"
    source = source.resolve()
    if not source.is_file() or not os.access(source, os.X_OK):
        raise ActionError("entry.runner.unavailable", f"pyrun is unavailable: {source}")
    return source


def _markdown_target(value: str) -> str:
    quoted = any(character.isspace() or character in "()" for character in value)
    return f"<{value}>" if quoted else value


def _next_entry_id(entries: tuple[EntryObservation, ...]) -> str:
    number = max((_entry_number(item.id) for item in entries), default=0) + 1
    return f"e{number:03d}"


def _entry_number(entry_id: str) -> int:
    number = entry_number(entry_id)
    if number is None:
        raise ActionError("entry.identity.invalid", f"invalid entry ID: {entry_id}")
    return number


def _date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as error:
        raise ActionError(
            "entry.date.invalid", f"invalid entry date: {value}"
        ) from error
    if parsed.isoformat() != value:
        raise ActionError("entry.date.invalid", f"invalid entry date: {value}")
    return value


def _title(value: str, code: str) -> str:
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > MAX_TITLE_BYTES
        or any(character in value for character in "\r\n[]")
        or any(ord(character) < 32 for character in value)
    ):
        raise ActionError(code, "title must be one bounded Markdown-safe line")
    return value


def _slug(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_SLUG_BYTES or SLUG_RE.fullmatch(value) is None:
        raise ActionError("entry.slug.invalid", f"invalid entry slug: {value}")
    return value


def _make_directory(path: Path, created: list[Path]) -> None:
    path.mkdir()
    created.append(path)
    sync_directory(path.parent)


def _restore_summary(path: Path, before: bytes, candidate: str) -> list[str]:
    try:
        if path.read_bytes() != candidate.encode("utf-8"):
            return []
        atomic_write_text(path, before.decode("utf-8"))
    except (OSError, UnicodeError) as error:
        return [f"restore {path}: {error}"]
    return []


def _rollback_created(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in reversed(paths):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                path.rmdir()
            sync_directory(path.parent)
        except OSError as error:
            errors.append(f"remove {path}: {error}")
    return errors


def _raise_publication_failure(
    action: str, error: OSError, created: list[Path]
) -> None:
    _raise_action_failure(action, error, _rollback_created(created))


def _raise_action_failure(
    action: str, error: OSError, rollback_errors: list[str]
) -> None:
    if rollback_errors:
        rollback = "; ".join(rollback_errors)
        raise ActionError(
            f"{action}.rollback_incomplete",
            f"publication failed: {error}; rollback failed: {rollback}",
        ) from error
    raise ActionError(
        f"{action}.failed", f"publication failed and was rolled back: {error}"
    ) from error


def _result(
    task: str, status: str, changed: bool, paths: tuple[str, ...]
) -> ActionResult:
    return ActionResult(task, status, f"{task}.{status}", changed, paths=paths)
