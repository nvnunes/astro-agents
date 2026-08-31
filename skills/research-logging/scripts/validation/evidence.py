"""Active v2 evidence-file and presentation-association contracts."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, Sequence, cast

from .entry_materials import EntryMaterialPathError, validate_entry_path_symlinks
from .json_codec import V2JsonError, canonical_json, decode_json

EVIDENCE_SCHEMA = "research-log-evidence/v2"
MAX_EVIDENCE_FILE_BYTES = 8 * 1024 * 1024
MAX_RECORDS_PER_FILE = 1000
MAX_RECORDS_PER_LOG = 10_000
MAX_PRESENTATIONS_PER_LOG = 10_000
MAX_SUMMARY_REFERENCES_PER_LOG = 10_000
MAX_RECORD_ID_BYTES = 96
MAX_DOCUMENT_BYTES = 512
MAX_RETENTION_PATHS = 10_000
MAX_RETENTION_REASON_BYTES = 2048
MAX_SOURCES = 32
MAX_SUMMARY_REFERENCE_BYTES = 512
MAX_PRESENTATION_BYTES = 1024 * 1024
SECTION_CLASSIFIER_VERSION = "entry-section-labels/1"

SECTION_LABEL_RE = re.compile(r"`(?P<label>[^`\r\n]+:)`\Z")
SECTION_LABELS = frozenset(
    {
        "Background:",
        "Steps:",
        "Results:",
        "Findings:",
        "Observations:",
        "Uncertainty:",
        "Decisions:",
        "Follow-up:",
    }
)
EXPERIMENTAL_SECTION_LABELS = SECTION_LABELS - {"Findings:"}
SYNTHESIS_SECTION_LABELS = SECTION_LABELS - {
    "Steps:",
    "Results:",
    "Observations:",
}

RECORD_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
EID_COMMENT_RE = re.compile(r"<!-- eid:(?P<id>[a-z][a-z0-9]*(?:-[a-z0-9]+)*) -->")
EID_CANDIDATE_RE = re.compile(r"<!--\s*[Ee][Ii][Dd](?::|\s|=)")
EID_LINE_RE = re.compile(
    r"(?P<code>`(?P<value>[^`\r\n]+)`)"
    r"<!-- eid:(?P<id>[a-z][a-z0-9]*(?:-[a-z0-9]+)*) -->"
)
SUMMARY_REFERENCE_RE = re.compile(
    r"<!-- ref entry = (?P<entry>[A-Za-z0-9][A-Za-z0-9_-]*); "
    r"eid = (?P<id>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)"
    r"(?:; row = (?P<row>[1-9][0-9]*); column = (?P<column>[1-9][0-9]*))? -->"
)
SUMMARY_LINE_RE = re.compile(
    r"(?P<code>`(?P<value>[^`\r\n]+)`)"
    r"(?P<reference><!-- ref [^\r\n]+ -->)"
)
SUMMARY_CANDIDATE_RE = re.compile(r"<!--\s*[Rr][Ee][Ff](?:\s|=)")
INLINE_CODE_RE = re.compile(r"`([^`\r\n]+)`")
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?%?"
)
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)\s*$")
FENCE_RE = re.compile(r"^(?P<fence>`{3,})(?P<info>[^`]*)$")
MARKDOWN_LINK_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]\r\n]*)\]"
    r"\((?P<target><[^<>\r\n]+>|[^()\s\r\n]+)"
    r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'))?\)"
)
EXTERNAL_TARGET_SCHEMES = frozenset({"doi", "ftp", "gs", "http", "https", "s3"})


class EvidenceV2Error(ValueError):
    """Raised when active v2 evidence metadata violates its exact contract."""

    def __init__(self, code: str, subject: str, observed: object, rule: str):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule


@dataclass(frozen=True)
class EvidenceSource:
    """One ordered v2 source and embedded locator declaration."""

    source: str
    locator: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical evidence-source object."""

        return {"locator": dict(self.locator), "source": self.source}


@dataclass(frozen=True)
class PresentationRecord:
    """One entry-owned v2 presentation declaration."""

    id: str
    document: str
    kind: str
    sources: tuple[EvidenceSource, ...]
    transformation: Mapping[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical record object."""

        return {
            "document": self.document,
            "id": self.id,
            "kind": self.kind,
            "sources": [source.as_dict() for source in self.sources],
            "transformation": (
                dict(self.transformation) if self.transformation is not None else None
            ),
        }


@dataclass(frozen=True)
class RetentionRecord:
    """One entry-local retention declaration affecting orphan classification."""

    id: str
    paths: tuple[str, ...] = ()
    directory: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical record object without interpreting reason."""

        value: dict[str, Any] = {"id": self.id, "kind": "retention"}
        if self.paths:
            value["paths"] = list(self.paths)
        else:
            value["directory"] = self.directory
            value["membership"] = "all-descendants"
        if self.reason is not None:
            value["reason"] = self.reason
        return value


EvidenceRecord = PresentationRecord | RetentionRecord


@dataclass(frozen=True)
class EvidenceFile:
    """Validated entry-local v2 evidence file."""

    path: Path
    entry_root: Path
    records: tuple[EvidenceRecord, ...]

    @property
    def identity(self) -> str:
        """Return the canonical content identity independent of record order."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        """Return canonical JSON with records ordered by entry-scoped ID."""

        return canonical_json(
            {
                "records": [
                    record.as_dict()
                    for record in sorted(self.records, key=lambda item: item.id)
                ],
                "schema": EVIDENCE_SCHEMA,
            }
        )


@dataclass(frozen=True)
class PresentedItem:
    """One exact entry presentation selected by an adjacent v2 marker."""

    id: str
    document: str
    kind: str
    value: str
    line: int
    section: str | None
    context_valid: bool
    section_classification: str
    under_results: bool


@dataclass(frozen=True)
class EntrySectionIssue:
    """One structurally invalid descriptive entry section."""

    heading: str
    line: int
    labels: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SummaryReference:
    """One exact inline summary reference and its presented expression."""

    value: str
    entry: str
    evidence_id: str
    line: int
    row: int | None = None
    column: int | None = None


@dataclass(frozen=True)
class DirectArtifactPresentation:
    """One local-like Markdown artifact target presented under Results."""

    document: str
    line: int
    target: str
    normalized_target: str
    label: str
    image: bool
    section: str | None


@dataclass(frozen=True)
class PresentationCandidate:
    """One natural presentation that requires authored association metadata."""

    kind: str
    line: int


@dataclass(frozen=True)
class CanonicalPresentation:
    """Minimal completed presentation projection used by summary forwarding."""

    kind: str
    statistic: str | None = None
    table: tuple[tuple[str, ...], ...] = ()
    numerical_cells: frozenset[tuple[int, int]] = frozenset()


@dataclass(frozen=True)
class SummaryAssociation:
    """One exact summary reference resolved to its entry presentation."""

    reference: SummaryReference
    target: CanonicalPresentation
    forwarded_value: str


@dataclass(frozen=True)
class _PresentationContext:
    document: str
    section: str | None
    context_valid: bool
    section_classification: str
    under_results: bool


@dataclass(frozen=True)
class _LineContext:
    """Deterministic section classification at one Markdown source line."""

    section: str | None
    classification: str
    under_results: bool


@dataclass(frozen=True)
class _SectionAnalysis:
    """One complete section-classifier projection for an entry document."""

    contexts: tuple[_LineContext, ...]
    issues: tuple[EntrySectionIssue, ...]


def load_evidence_file(
    path: Path,
    *,
    log_root: Path,
    entry_root: Path,
) -> EvidenceFile:
    """Read one exact entry-root v2 file with strict JSON and schema checks."""

    path = path.resolve()
    log_root = log_root.resolve()
    entry_root = entry_root.resolve()
    expected = entry_root / "evidence.json"
    if path != expected or path.is_symlink() or entry_root.is_symlink():
        _fail(
            "evidence.file.location_invalid",
            str(path),
            {"expected": str(expected)},
            "Evidence Files And Unsupported Metadata",
        )
    value = _read_evidence_json(path)
    if not isinstance(value, Mapping) or set(value) != {"schema", "records"}:
        _fail(
            "evidence.json.schema_invalid",
            str(path),
            {"fields": sorted(value) if isinstance(value, Mapping) else None},
            "V2 JSON File Schema",
        )
    if value["schema"] != EVIDENCE_SCHEMA or not isinstance(value["records"], list):
        _fail(
            "evidence.json.schema_invalid",
            str(path),
            {"schema": value.get("schema")},
            "V2 JSON File Schema",
        )
    raw_records = value["records"]
    if not raw_records:
        _fail(
            "evidence.file.empty",
            str(path),
            {"records": 0},
            "V2 JSON File Schema",
        )
    if len(raw_records) > MAX_RECORDS_PER_FILE:
        _fail(
            "association.resource.too_large",
            str(path),
            {"records": len(raw_records), "limit": MAX_RECORDS_PER_FILE},
            "Association Resource Bounds",
        )
    entry_relative = _relative(entry_root, log_root, "entry root")
    records = tuple(
        _decode_record(
            item,
            subject=f"{path}:records[{number}]",
            entry_relative=entry_relative,
            entry_root=entry_root,
        )
        for number, item in enumerate(raw_records)
    )
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        _fail(
            "evidence.record.id_duplicate",
            str(path),
            {"ids": ids},
            "V2 JSON File Schema",
        )
    return EvidenceFile(path=path, entry_root=entry_root, records=records)


def _read_evidence_json(path: Path) -> object:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(
            "evidence.file.encoding_invalid",
            str(path),
            {"error": str(exc)},
            "V2 JSON File Schema",
        )
    except OSError as exc:
        _fail(
            "evidence.json.schema_invalid",
            str(path),
            {"error": str(exc)},
            "V2 JSON File Schema",
        )
    if len(raw) > MAX_EVIDENCE_FILE_BYTES:
        _fail(
            "association.resource.too_large",
            str(path),
            {"bytes": len(raw), "limit": MAX_EVIDENCE_FILE_BYTES},
            "Association Resource Bounds",
        )
    try:
        return decode_json(
            text,
            maximum_bytes=MAX_EVIDENCE_FILE_BYTES,
            subject="evidence.json",
        )
    except V2JsonError as exc:
        _fail(
            "evidence.json.schema_invalid",
            str(path),
            {"error": str(exc)},
            "V2 JSON File Schema",
        )


def index_entry_presentations(
    text: str,
    *,
    document: str,
) -> tuple[PresentedItem, ...]:
    """Index exact v2 entry markers and their structurally adjacent items."""

    lines = text.splitlines()
    contexts = _line_contexts(lines)
    fenced = _fenced_lines(lines)
    items: list[PresentedItem] = []
    consumed_markers: set[tuple[int, int]] = set()
    for number, line in enumerate(lines, 1):
        if fenced[number - 1]:
            continue
        found, consumed = _presentations_on_line(
            lines, contexts, number, line, document
        )
        items.extend(found)
        consumed_markers.update(consumed)
    observed_markers = [
        (number, match.start())
        for number, line in enumerate(lines, 1)
        for match in EID_CANDIDATE_RE.finditer(line)
    ]
    if any(marker not in consumed_markers for marker in observed_markers):
        _fail(
            "presentation.marker.invalid",
            document,
            {"markers": observed_markers},
            "V2 Entry Presentation Markers",
        )
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        _fail(
            "presentation.marker.duplicate",
            document,
            {"ids": ids},
            "V2 Entry Presentation Markers",
        )
    return tuple(items)


def index_entry_section_issues(text: str) -> tuple[EntrySectionIssue, ...]:
    """Return one precise issue for each structurally invalid ``##`` section."""

    return _section_analysis(text.splitlines()).issues


def _presentations_on_line(
    lines: Sequence[str],
    contexts: Sequence[_LineContext],
    number: int,
    line: str,
    document: str,
) -> tuple[list[PresentedItem], set[tuple[int, int]]]:
    context = contexts[number - 1]
    experimental = context.classification == "experimental"
    items = []
    for match in EID_LINE_RE.finditer(line):
        value = match.group("value")
        _require_presentation_bound(value, f"{document}:{number}")
        items.append(
            PresentedItem(
                id=match.group("id"),
                document=document,
                kind="statistic",
                value=value,
                line=number,
                section=context.section,
                context_valid=experimental,
                section_classification=context.classification,
                under_results=context.under_results,
            )
        )
    consumed = {
        (number, match.start("code") + len(match.group("code")))
        for match in EID_LINE_RE.finditer(line)
    }
    marker = EID_COMMENT_RE.fullmatch(line.strip())
    if marker is None:
        return items, consumed
    consumed.add((number, line.index("<!--")))
    block = _block_presentation(
        lines,
        number,
        marker.group("id"),
        _PresentationContext(
            document,
            context.section,
            experimental and context.under_results,
            context.classification,
            context.under_results,
        ),
    )
    if block is not None:
        items.append(block)
    return items, consumed


def _block_presentation(
    lines: Sequence[str],
    number: int,
    record_id: str,
    context: _PresentationContext,
) -> PresentedItem | None:
    if number >= len(lines):
        return None
    if _looks_like_table(lines, number):
        markdown, _ = _table_block(lines, number)
        _require_presentation_bound(markdown, f"{context.document}:{number + 1}")
        return PresentedItem(
            id=record_id,
            document=context.document,
            kind="table",
            value=markdown,
            line=number + 1,
            section=context.section,
            context_valid=context.context_valid,
            section_classification=context.section_classification,
            under_results=context.under_results,
        )
    fence = FENCE_RE.fullmatch(lines[number])
    if fence is None or fence.group("info") != "text":
        return None
    payload, _ = _text_fence(lines, number, fence.group("fence"))
    _require_presentation_bound(payload, f"{context.document}:{number + 1}")
    return PresentedItem(
        id=record_id,
        document=context.document,
        kind="output",
        value=payload,
        line=number + 1,
        section=context.section,
        context_valid=context.context_valid,
        section_classification=context.section_classification,
        under_results=context.under_results,
    )


def index_direct_artifacts(
    text: str,
    *,
    document: str,
) -> tuple[DirectArtifactPresentation, ...]:
    """Index natural local artifact links and images under experimental Results."""

    lines = text.splitlines()
    contexts = _line_contexts(lines)
    fenced = _fenced_lines(lines)
    artifacts: list[DirectArtifactPresentation] = []
    document_path = PurePosixPath(_normalized_relative(document, document))
    for number, line in enumerate(lines, 1):
        if fenced[number - 1]:
            continue
        context = contexts[number - 1]
        if (
            context.classification != "experimental"
            or not context.under_results
        ):
            continue
        for match in MARKDOWN_LINK_RE.finditer(line):
            raw_target = match.group("target")
            target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
            target = urllib.parse.unquote(target)
            parsed = urllib.parse.urlparse(target)
            path_text = target.split("#", 1)[0]
            if (
                parsed.scheme.lower() in EXTERNAL_TARGET_SCHEMES
                or target.startswith("#")
                or not path_text
                or PurePosixPath(path_text).suffix.lower() == ".md"
            ):
                continue
            pure = PurePosixPath(path_text)
            normalized = (
                pure.as_posix()
                if pure.is_absolute()
                else _normalize_join(document_path.parent, pure)
            )
            artifacts.append(
                DirectArtifactPresentation(
                    document=document,
                    line=number,
                    target=target,
                    normalized_target=normalized,
                    label=match.group("label"),
                    image=bool(match.group("image")),
                    section=context.section,
                )
            )
    return tuple(artifacts)


def index_entry_documents(text: str) -> tuple[str, ...]:
    """Return unique owned-entry targets from the summary entry inventory."""

    targets: list[str] = []
    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    contexts = _line_contexts(lines)
    for number, line in enumerate(lines):
        if fenced[number]:
            continue
        if contexts[number].section != "Entries":
            continue
        for match in MARKDOWN_LINK_RE.finditer(line):
            if match.group("image"):
                continue
            raw = match.group("target")
            target = raw[1:-1] if raw.startswith("<") else raw
            target = urllib.parse.unquote(target.split("#", 1)[0])
            if "/entries/" not in target:
                continue
            pure = PurePosixPath(target)
            if pure.suffix == ".md" and re.fullmatch(
                r"e[0-9]+[a-z]?", pure.stem, re.I
            ):
                targets.append(pure.as_posix())
    return tuple(dict.fromkeys(targets))


def index_entry_presentation_candidates(
    text: str,
) -> tuple[PresentationCandidate, ...]:
    """Index natural entry presentations that require an adjacent evidence ID."""

    lines = text.splitlines()
    contexts = _line_contexts(lines)
    fenced = _fenced_lines(lines)
    candidates: list[PresentationCandidate] = []
    for index, line in enumerate(lines):
        context = contexts[index]
        experimental = context.classification == "experimental"
        if not fenced[index] and experimental:
            candidates.extend(
                PresentationCandidate("statistic", index + 1)
                for match in INLINE_CODE_RE.finditer(line)
                if _presented_numeric_expression(match.group(1))
            )
        if not experimental or not context.under_results:
            continue
        if not fenced[index] and _looks_like_table(lines, index):
            candidates.append(PresentationCandidate("table", index + 1))
        fence = FENCE_RE.fullmatch(line)
        if fence is not None and fence.group("info") == "text":
            candidates.append(PresentationCandidate("output", index + 1))
    return tuple(candidates)


def index_summary_statistic_candidates(text: str) -> tuple[int, ...]:
    """Return summary lines containing natural statistics that require a ref."""

    lines = text.splitlines()
    fenced = _fenced_lines(lines)
    in_summary = False
    candidates: list[int] = []
    for index, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading and len(heading.group("marks")) == 2:
            title = heading.group("title").strip()
            if title == "Summary":
                in_summary = True
                continue
            if in_summary:
                break
        if not in_summary or fenced[index]:
            continue
        candidates.extend(
            index + 1
            for match in INLINE_CODE_RE.finditer(line)
            if _presented_numeric_expression(match.group(1))
        )
    return tuple(candidates)


def _presented_numeric_expression(value: str) -> bool:
    value = value.strip()
    return not bool(
        not NUMBER_RE.search(value)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        or value.startswith("--")
        or re.match(r"^\d+\s*PH(?:\b|$)", value, re.I)
        or re.search(r"[A-Za-z_][A-Za-z0-9_.-]*\s*=", value)
        or re.fullmatch(r"[0-9a-fA-F]{12,}", value)
        or UUID_RE.fullmatch(value)
        or not re.match(r"^[\s~≈<>≤≥([{]*[-+]?(?:\d|\.\d)", value)
    )


def _require_presentation_bound(value: str, subject: str) -> None:
    encoded_bytes = len(value.encode("utf-8"))
    if encoded_bytes > MAX_PRESENTATION_BYTES:
        _fail(
            "association.resource.too_large",
            subject,
            {"bytes": encoded_bytes, "limit": MAX_PRESENTATION_BYTES},
            "Association Resource Bounds",
        )


def index_summary_references(text: str) -> tuple[SummaryReference, ...]:
    """Parse exact inline summary references without value search."""

    references: list[SummaryReference] = []
    consumed: set[tuple[int, int]] = set()
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        for match in SUMMARY_LINE_RE.finditer(line):
            raw = match.group("reference")
            reference = SUMMARY_REFERENCE_RE.fullmatch(raw)
            if (
                reference is None
                or len(raw.encode("utf-8")) > MAX_SUMMARY_REFERENCE_BYTES
            ):
                _fail(
                    "summary.reference.invalid",
                    f"summary:{number}",
                    {"reference": raw},
                    "V2 Summary Evidence References",
                )
            row = reference.group("row")
            column = reference.group("column")
            references.append(
                SummaryReference(
                    value=match.group("value"),
                    entry=reference.group("entry"),
                    evidence_id=reference.group("id"),
                    line=number,
                    row=int(row) if row is not None else None,
                    column=int(column) if column is not None else None,
                )
            )
            consumed.add((number, match.start("reference")))
    observed = [
        (number, match.start())
        for number, line in enumerate(lines, 1)
        for match in SUMMARY_CANDIDATE_RE.finditer(line)
    ]
    if any(marker not in consumed for marker in observed):
        _fail(
            "summary.reference.invalid",
            "summary",
            {"references": observed},
            "V2 Summary Evidence References",
        )
    return tuple(references)


def associate_presentations(
    evidence: EvidenceFile,
    presentations: Sequence[PresentedItem],
) -> dict[str, PresentedItem]:
    """Require one exact marker for every presentation record and no extras."""

    records = {
        record.id: record
        for record in evidence.records
        if isinstance(record, PresentationRecord)
    }
    skipped = {
        item.id
        for item in presentations
        if item.section_classification == "invalid"
    }
    eligible = [
        item for item in presentations if item.section_classification != "invalid"
    ]
    presented = {item.id: item for item in eligible}
    for item in eligible:
        record = records.get(item.id)
        if record is None:
            _fail(
                "association.declaration_missing",
                f"{item.document}:{item.line}",
                {"id": item.id},
                "Association Completeness And Conflict Rules",
            )
        if record.document != item.document:
            _fail(
                "association.document_mismatch",
                item.id,
                {"declared": record.document, "observed": item.document},
                "Association Completeness And Conflict Rules",
            )
        if record.kind != item.kind:
            _fail(
                "association.kind_mismatch",
                item.id,
                {"declared": record.kind, "observed": item.kind},
                "Association Completeness And Conflict Rules",
            )
        if not item.context_valid:
            _fail(
                "association.context_invalid",
                item.id,
                {"section": item.section, "kind": item.kind},
                "Eligible Presentation Context",
            )
    missing = sorted(set(records) - set(presented) - skipped)
    if missing:
        _fail(
            "association.presentation_missing",
            str(evidence.path),
            {"ids": missing},
            "Association Completeness And Conflict Rules",
        )
    return presented


def resolve_summary_references(
    references: Sequence[SummaryReference],
    targets: Mapping[tuple[str, str], CanonicalPresentation],
) -> tuple[SummaryAssociation, ...]:
    """Resolve summary references by identity and compare exact forwarded text."""

    resolved: list[SummaryAssociation] = []
    for reference in references:
        identity = (reference.entry, reference.evidence_id)
        target = targets.get(identity)
        if target is None:
            _fail(
                "summary.reference.unresolved",
                f"summary:{reference.line}",
                {"entry": reference.entry, "eid": reference.evidence_id},
                "Summary Association",
            )
        if reference.row is None:
            if target.kind != "statistic" or target.statistic is None:
                _fail(
                    "summary.reference.target_invalid",
                    f"summary:{reference.line}",
                    {"kind": target.kind, "coordinates": False},
                    "V2 Summary Evidence References",
                )
            forwarded = target.statistic
        else:
            if target.kind != "table" or target.statistic is not None:
                _fail(
                    "summary.reference.target_invalid",
                    f"summary:{reference.line}",
                    {"kind": target.kind, "coordinates": True},
                    "V2 Summary Evidence References",
                )
            assert reference.column is not None
            coordinate = (reference.row, reference.column)
            try:
                forwarded = target.table[reference.row - 1][reference.column - 1]
            except IndexError:
                _fail(
                    "summary.reference.coordinate_invalid",
                    f"summary:{reference.line}",
                    {"row": reference.row, "column": reference.column},
                    "V2 Summary Evidence References",
                )
            if coordinate not in target.numerical_cells:
                _fail(
                    "summary.reference.coordinate_invalid",
                    f"summary:{reference.line}",
                    {"row": reference.row, "column": reference.column},
                    "V2 Summary Evidence References",
                )
        if reference.value != forwarded:
            _fail(
                "summary.reference.mismatch",
                f"summary:{reference.line}",
                {"expected": forwarded, "observed": reference.value},
                "Summary Association",
            )
        resolved.append(
            SummaryAssociation(
                reference=reference,
                target=target,
                forwarded_value=forwarded,
            )
        )
    return tuple(resolved)


def _decode_record(
    value: object,
    *,
    subject: str,
    entry_relative: str,
    entry_root: Path,
) -> EvidenceRecord:
    if not isinstance(value, Mapping):
        _invalid(subject, {"type": type(value).__name__})
    value = cast(Mapping[str, Any], value)
    record_id = _record_id(value.get("id"), subject)
    kind = value.get("kind")
    if kind == "retention":
        return _decode_retention(value, subject, record_id, entry_root)
    expected = {"document", "id", "kind", "sources", "transformation"}
    if set(value) != expected or kind not in {"statistic", "table", "output"}:
        _invalid(subject, {"fields": sorted(value), "kind": kind})
    document = _document(value["document"], subject, entry_relative, entry_root)
    sources = value["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        _invalid(
            subject,
            {"sources": len(sources) if isinstance(sources, list) else None},
        )
    decoded_sources = tuple(
        _decode_source(source, f"{subject}.sources[{number}]")
        for number, source in enumerate(sources)
    )
    transformation = value["transformation"]
    if transformation is not None and (
        not isinstance(transformation, Mapping) or not transformation
    ):
        _invalid(subject, {"transformation": transformation})
    return PresentationRecord(
        id=record_id,
        document=document,
        kind=kind,
        sources=decoded_sources,
        transformation=(dict(transformation) if transformation is not None else None),
    )


def _decode_source(value: object, subject: str) -> EvidenceSource:
    if not isinstance(value, Mapping) or set(value) != {"source", "locator"}:
        _invalid(
            subject,
            {"fields": sorted(value) if isinstance(value, Mapping) else None},
        )
    source = value["source"]
    locator = value["locator"]
    if not isinstance(source, str) or not source.strip():
        _invalid(subject, {"source": source})
    if not isinstance(locator, Mapping) or not locator:
        _invalid(subject, {"locator": locator})
    return EvidenceSource(source=source, locator=dict(locator))


def _decode_retention(
    value: Mapping[str, Any], subject: str, record_id: str, entry_root: Path
) -> RetentionRecord:
    reason = value.get("reason")
    if reason is not None and (
        not isinstance(reason, str)
        or len(reason.encode("utf-8")) > MAX_RETENTION_REASON_BYTES
    ):
        _invalid(subject, {"reason_bytes": _bytes(reason)})
    if "paths" in value:
        expected = {"id", "kind", "paths"} | (
            {"reason"} if "reason" in value else set()
        )
        paths = value["paths"]
        if set(value) != expected or not isinstance(paths, list) or not paths:
            _invalid(subject, {"fields": sorted(value), "paths": paths})
        if len(paths) > MAX_RETENTION_PATHS or len(paths) != len(set(paths)):
            _invalid(subject, {"path_count": len(paths)})
        decoded = tuple(_retention_file(path, subject, entry_root) for path in paths)
        return RetentionRecord(id=record_id, paths=decoded, reason=reason)
    expected = {"directory", "id", "kind", "membership"} | (
        {"reason"} if "reason" in value else set()
    )
    if set(value) != expected or value.get("membership") != "all-descendants":
        _invalid(
            subject,
            {"fields": sorted(value), "membership": value.get("membership")},
        )
    directory = _retention_directory(value.get("directory"), subject, entry_root)
    return RetentionRecord(id=record_id, directory=directory, reason=reason)


def _record_id(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or len(value.encode("ascii")) > MAX_RECORD_ID_BYTES
        or RECORD_ID_RE.fullmatch(value) is None
    ):
        _invalid(subject, {"id": value})
    return value


def _document(
    value: object, subject: str, entry_relative: str, entry_root: Path
) -> str:
    path = _normalized_relative(value, subject)
    if len(path.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        _invalid(subject, {"document_bytes": len(path.encode("utf-8"))})
    if PurePosixPath(path).parent != PurePosixPath(entry_relative):
        _invalid(subject, {"document": path, "entry": entry_relative})
    if not path.endswith(".md"):
        _invalid(subject, {"document": path})
    target = entry_root / PurePosixPath(path).name
    _reject_symlinked_target(target, entry_root, subject, path)
    if not target.is_file():
        _invalid(subject, {"document": path, "reason": "not_regular_file"})
    try:
        target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _invalid(subject, {"document": path, "error": str(exc)})
    return path


def _retention_file(value: object, subject: str, entry_root: Path) -> str:
    path = _normalized_relative(value, subject)
    resolved = entry_root.joinpath(*PurePosixPath(path).parts)
    _reject_symlinked_target(resolved, entry_root, subject, path)
    if not resolved.is_file():
        _invalid(subject, {"path": path, "reason": "not_regular_file"})
    return path


def _retention_directory(value: object, subject: str, entry_root: Path) -> str:
    path = _normalized_relative(value, subject)
    resolved = entry_root.joinpath(*PurePosixPath(path).parts)
    _reject_symlinked_target(resolved, entry_root, subject, path)
    if not resolved.is_dir():
        _invalid(subject, {"directory": path, "reason": "not_directory"})
    descendants = list(resolved.rglob("*"))
    for child in descendants:
        _reject_symlinked_target(child, entry_root, subject, path)
    eligible = [child for child in descendants if child.is_file()]
    if not eligible:
        _invalid(subject, {"directory": path, "reason": "empty"})
    return path


def _reject_symlinked_target(target: Path, root: Path, subject: str, path: str) -> None:
    try:
        validate_entry_path_symlinks(target, root)
    except EntryMaterialPathError as error:
        _invalid(subject, {"path": path, "reason": error.reason})


def _normalized_relative(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        _invalid(subject, {"path": value})
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _invalid(subject, {"path": value})
    if pure.as_posix() != value:
        _invalid(subject, {"path": value})
    return value


def _line_contexts(lines: Sequence[str]) -> tuple[_LineContext, ...]:
    return _section_analysis(lines).contexts


def _section_analysis(lines: Sequence[str]) -> _SectionAnalysis:
    fenced = _fenced_lines(lines)
    starts = [
        index
        for index, line in enumerate(lines)
        if not fenced[index]
        and (heading_candidate := HEADING_RE.match(line)) is not None
        and len(heading_candidate.group("marks")) == 2
    ]
    contexts = [_LineContext(None, "outside", False) for _ in lines]
    issues: list[EntrySectionIssue] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        heading_match = HEADING_RE.match(lines[start])
        assert heading_match is not None
        heading = heading_match.group("title").strip()
        labels = tuple(
            match.group("label")
            for index in range(start + 1, end)
            if not fenced[index]
            and (match := SECTION_LABEL_RE.fullmatch(lines[index].strip())) is not None
        )
        classification, reason = _classify_section(labels)
        under_results = False
        for index in range(start, end):
            label = (
                None
                if fenced[index]
                else SECTION_LABEL_RE.fullmatch(lines[index].strip())
            )
            if classification == "experimental" and label is not None:
                under_results = label.group("label") == "Results:"
            contexts[index] = _LineContext(heading, classification, under_results)
        if classification == "invalid":
            issues.append(
                EntrySectionIssue(heading, start + 1, labels, reason or "invalid")
            )
    return _SectionAnalysis(tuple(contexts), tuple(issues))


def _classify_section(labels: Sequence[str]) -> tuple[str, str | None]:
    if not labels:
        return "prose", None
    if len(labels) != len(set(labels)):
        return "invalid", "duplicate_label"
    if set(labels) - SECTION_LABELS:
        return "invalid", "unknown_label"
    observed = set(labels)
    if (
        {"Steps:", "Results:"} <= observed
        and observed <= EXPERIMENTAL_SECTION_LABELS
    ):
        return "experimental", None
    if "Findings:" in observed and observed <= SYNTHESIS_SECTION_LABELS:
        return "synthesis", None
    return "invalid", "invalid_label_combination"


def _fenced_lines(lines: Sequence[str]) -> tuple[bool, ...]:
    fenced: list[bool] = []
    opening: str | None = None
    for line in lines:
        stripped = line.lstrip()
        if opening is None:
            match = re.match(r"(?P<fence>`{3,}|~{3,})", stripped)
            if match is None:
                fenced.append(False)
                continue
            opening = match.group("fence")
            fenced.append(True)
            continue
        fenced.append(True)
        if re.fullmatch(rf"{re.escape(opening[0])}{{{len(opening)},}}\s*", stripped):
            opening = None
    return tuple(fenced)


def _normalize_join(base: PurePosixPath, target: PurePosixPath) -> str:
    parts: list[str] = []
    for part in (*base.parts, *target.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    return PurePosixPath(*parts).as_posix()


def _looks_like_table(lines: Sequence[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and all(
            re.fullmatch(r":?-{3,}:?", cell.strip()) is not None
            for cell in lines[index + 1].strip().strip("|").split("|")
        )
    )


def _table_block(lines: Sequence[str], index: int) -> tuple[str, int]:
    block: list[str] = []
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        block.append(lines[index])
        index += 1
    return "\n".join(block), index


def _text_fence(lines: Sequence[str], index: int, fence: str) -> tuple[str, int]:
    payload: list[str] = []
    index += 1
    while index < len(lines) and lines[index] != fence:
        payload.append(lines[index])
        index += 1
    if index == len(lines):
        _fail(
            "association.presentation.syntax_invalid",
            "text fence",
            {"closed": False},
            "Strict Presentation Parsing And Comparison",
        )
    return "\n".join(payload), index


def _relative(path: Path, root: Path, subject: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        _invalid(subject, {"path": str(path), "root": str(root)})


def _bytes(value: object) -> int | None:
    return len(value.encode("utf-8")) if isinstance(value, str) else None


def _invalid(subject: str, observed: object) -> NoReturn:
    _fail(
        "evidence.declaration.invalid",
        subject,
        observed,
        "V2 JSON File Schema",
    )


def _fail(code: str, subject: str, observed: object, rule: str) -> NoReturn:
    raise EvidenceV2Error(code, subject, observed, rule)
