"""Shared human projection for mechanical validation findings."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .mechanical_results import (
    CheckScope,
    CheckStatus,
    FailurePayload,
    MechanicalCheck,
    MechanicalGeneratedRecord,
)

ENTRY_ID_RE = re.compile(r"e[0-9]{3,}[a-z]?\Z", re.I)
ENTRY_TOKEN_RE = re.compile(r"(?:^|:)(e[0-9]{3,}[a-z]?)(?=:|$)", re.I)
ENTRY_DIRECTORY_RE = re.compile(
    r"(?:^|/)[0-9]{4}-[0-9]{2}-[0-9]{2}-(e[0-9]{3,})-", re.I
)
ENTRY_LINK_RE = re.compile(
    r"\[(?P<title>[^\]\r\n]+)\]"
    r"\((?P<target><?[^()\s\r\n]+>?)\)"
)
SPLIT_ENTRY_PARENT_RE = re.compile(
    r"^- `[0-9]{4}-[0-9]{2}-[0-9]{2}` (?P<title>.+):$"
)
ENTRY_FOLDER_ID_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}-(?P<entry>e[0-9]{3,})-.+\Z"
)
SUMMARY_LINE_RE = re.compile(r"summary:(?P<line>[1-9][0-9]*)\Z")
MAX_SUMMARY_BYTES = 8 * 1024 * 1024


class HumanProjectionError(ValueError):
    """Raised when a machine finding has no complete human projection."""


@dataclass(frozen=True)
class FindingPresentation:
    """Human wording and target ownership for one machine finding code."""

    name: str
    sentence: str
    target_kind: str


@dataclass(frozen=True)
class EntryPresentation:
    """Human title and report-relative document for one maintained entry."""

    id: str
    title: str
    document: str
    root: Path


@dataclass(frozen=True)
class ReportContext:
    """Nonserialized human context captured from one maintained summary."""

    title: str
    summary: Path
    log_root: Path
    entries: Mapping[str, EntryPresentation]

    @classmethod
    def empty(cls, summary: Path) -> ReportContext:
        """Build context without reading research-owned material."""

        summary = summary.absolute()
        return cls(summary.stem, summary, summary.with_suffix(""), {})


@dataclass(frozen=True)
class FindingGroup:
    """One deterministic human issue signature over machine checks."""

    status: CheckStatus
    scope: CheckScope
    code: str
    entry: str | None
    subject: str
    rule: str
    observed: Mapping[str, Any]
    presentation: FindingPresentation
    check_ids: tuple[str, ...]
    impacted_checks: int

    @property
    def represented_checks(self) -> int:
        """Return the number of exact checks represented by this group."""

        return len(self.check_ids)


CATALOG: Mapping[str, FindingPresentation] = {
    "association.artifact.source_mismatch": FindingPresentation(
        "Evidence Source Mismatch",
        "The presented artifact does not match its declared evidence source.",
        "record",
    ),
    "association.context_invalid": FindingPresentation(
        "Invalid Evidence Context",
        "The evidence presentation appears outside a valid entry context.",
        "entry",
    ),
    "association.declaration_missing": FindingPresentation(
        "Missing Evidence Declaration",
        "The entry presents evidence without the required declaration.",
        "entry",
    ),
    "association.document_mismatch": FindingPresentation(
        "Evidence Document Mismatch",
        "The evidence record names a different document from the presentation.",
        "record",
    ),
    "association.document_unavailable": FindingPresentation(
        "Evidence Document Unavailable",
        "The declared evidence document could not be inspected.",
        "path",
    ),
    "association.kind_mismatch": FindingPresentation(
        "Evidence Kind Mismatch",
        "The presentation kind does not match the declared evidence kind.",
        "record",
    ),
    "association.presentation.syntax_invalid": FindingPresentation(
        "Invalid Evidence Presentation",
        "The evidence presentation syntax is invalid.",
        "record",
    ),
    "association.presentation_missing": FindingPresentation(
        "Missing Evidence Presentation",
        "The evidence declaration has no matching presentation.",
        "record",
    ),
    "association.resource.too_large": FindingPresentation(
        "Evidence Association Limit Exceeded",
        "The evidence association inventory exceeds its validation bound.",
        "log",
    ),
    "collection.membership.invalid": FindingPresentation(
        "Invalid Collection Membership",
        "The declared collection membership is invalid.",
        "path",
    ),
    "collection.membership.unresolved": FindingPresentation(
        "Collection Membership Unresolved",
        "The complete collection membership could not be resolved.",
        "path",
    ),
    "collection.output_directory.shared": FindingPresentation(
        "Shared Output Directory",
        "More than one invocation claims the same output directory.",
        "path",
    ),
    "data.declaration.conflict": FindingPresentation(
        "Conflicting Data Declaration",
        "Data declarations disagree about the same input or target.",
        "record",
    ),
    "data.declaration.invalid": FindingPresentation(
        "Invalid Data Declaration",
        "The data declaration does not satisfy the registry contract.",
        "record",
    ),
    "data.file.location_invalid": FindingPresentation(
        "Invalid Data Registry Location",
        "The data registry is not at its required entry-owned location.",
        "path",
    ),
    "data.fingerprint.mismatch": FindingPresentation(
        "Input Fingerprint Mismatch",
        "The material no longer matches its declared fingerprint.",
        "record",
    ),
    "data.git.projection_missing": FindingPresentation(
        "Missing Git Source Projection",
        "The command does not pass both the repository locator and pinned commit.",
        "command",
    ),
    "data.input.token_missing": FindingPresentation(
        "Missing Named Input Token",
        "A command uses declared input material without its named token.",
        "command",
    ),
    "data.input.undeclared": FindingPresentation(
        "Undeclared Command Input",
        "A command reads material that is not declared as an input.",
        "command",
    ),
    "data.name.duplicate": FindingPresentation(
        "Duplicate Data Name",
        "More than one data declaration uses the same name.",
        "record",
    ),
    "data.origin.invalid": FindingPresentation(
        "Invalid Data Origin",
        "The data origin flag conflicts with the material boundary.",
        "record",
    ),
    "data.target.duplicate": FindingPresentation(
        "Duplicate Data Target",
        "More than one data declaration claims the same target.",
        "path",
    ),
    "data.target.missing": FindingPresentation(
        "Data Target Missing",
        "The declared input target does not exist or cannot be resolved.",
        "path",
    ),
    "directory.membership.invalid": FindingPresentation(
        "Invalid Directory Membership",
        "The directory declaration does not describe a valid complete membership.",
        "path",
    ),
    "directory.origin.conflict": FindingPresentation(
        "Directory Origin Conflict",
        "Directory members disagree about their material origin boundary.",
        "path",
    ),
    "directory.producer.conflict": FindingPresentation(
        "Directory Producer Conflict",
        "Directory members do not share one valid producer boundary.",
        "path",
    ),
    "evidence.declaration.invalid": FindingPresentation(
        "Invalid Evidence Declaration",
        "The evidence record does not satisfy the evidence contract.",
        "record",
    ),
    "evidence.file.empty": FindingPresentation(
        "Empty Evidence Registry", "The evidence registry is empty.", "path"
    ),
    "evidence.file.encoding_invalid": FindingPresentation(
        "Invalid Evidence Registry Encoding",
        "The evidence registry is not valid UTF-8.",
        "path",
    ),
    "evidence.file.location_invalid": FindingPresentation(
        "Invalid Evidence Registry Location",
        "The evidence registry is not at its required entry-owned location.",
        "path",
    ),
    "evidence.json.schema_invalid": FindingPresentation(
        "Invalid Evidence Registry Schema",
        "The evidence registry JSON does not match its schema.",
        "path",
    ),
    "evidence.presentation.too_large": FindingPresentation(
        "Evidence Presentation Too Large",
        "The presentation document exceeds its validation bound.",
        "path",
    ),
    "evidence.presentation.unresolved": FindingPresentation(
        "Evidence Presentation Unresolved",
        "The declared presentation cannot be located unambiguously.",
        "record",
    ),
    "evidence.record.id_duplicate": FindingPresentation(
        "Duplicate Evidence ID",
        "More than one evidence record uses the same ID.",
        "record",
    ),
    "hygiene.output.unmatched": FindingPresentation(
        "Unmatched Recorded Output",
        "A recorded output is not used by current evidence or provenance.",
        "path",
    ),
    "invocation.command.unsupported": FindingPresentation(
        "Unsupported Recorded Command",
        "The recorded command uses shell syntax outside the supported command grammar.",
        "command",
    ),
    "invocation.executable.unresolved": FindingPresentation(
        "Command Executable Unresolved",
        "The recorded command executable cannot be resolved safely.",
        "command",
    ),
    "invocation.path_value.embedded": FindingPresentation(
        "Embedded Material Path",
        "A material path is embedded in an argument instead of passed as one "
        "complete value.",
        "command",
    ),
    "lineage.ambiguous": FindingPresentation(
        "Ambiguous Material Lineage",
        "More than one upstream path can produce this material.",
        "path",
    ),
    "lineage.cycle": FindingPresentation(
        "Material Lineage Cycle", "The material lineage contains a cycle.", "path"
    ),
    "lineage.missing": FindingPresentation(
        "Missing Material Lineage",
        "An intermediate material has no recorded producer.",
        "path",
    ),
    "orphan.input.unused": FindingPresentation(
        "Unused Input Declaration",
        "A declared input is not used by current evidence or recorded commands.",
        "record",
    ),
    "orphan.material.unused": FindingPresentation(
        "Unused Retained Material",
        "Retained material is not used by current evidence, provenance, or "
        "retention declarations.",
        "path",
    ),
    "locator.alignment.invalid": FindingPresentation(
        "Locator Alignment Mismatch",
        "Related locator selections do not have compatible alignment.",
        "locator",
    ),
    "locator.encoding.too_large": FindingPresentation(
        "Locator Value Too Large",
        "A selected value exceeds the bounded canonical encoding size.",
        "locator",
    ),
    "locator.expectation.mismatch": FindingPresentation(
        "Locator Expectation Mismatch",
        "The selected value does not satisfy its declared expectation.",
        "locator",
    ),
    "locator.field.missing": FindingPresentation(
        "Locator Field Missing",
        "A requested field is absent from the selected source.",
        "locator",
    ),
    "locator.identity.duplicate": FindingPresentation(
        "Duplicate Locator Identity",
        "More than one selected item has the same declared identity.",
        "locator",
    ),
    "locator.identity.expectation_mismatch": FindingPresentation(
        "Locator Identity Mismatch",
        "Selected identities do not match the declared identity expectation.",
        "locator",
    ),
    "locator.literal.invalid": FindingPresentation(
        "Invalid Locator Literal",
        "A locator predicate or expectation contains an invalid literal.",
        "locator",
    ),
    "locator.path.unresolved": FindingPresentation(
        "Locator Path Unresolved",
        "The requested locator path cannot be resolved.",
        "locator",
    ),
    "locator.predicate.parse_failed": FindingPresentation(
        "Invalid Locator Predicate",
        "The locator predicate cannot be parsed.",
        "locator",
    ),
    "locator.property.unsupported": FindingPresentation(
        "Unsupported Locator Property",
        "The requested property is unavailable for this source format.",
        "locator",
    ),
    "locator.reader.unavailable": FindingPresentation(
        "Locator Reader Unavailable",
        "The required bounded source reader is unavailable.",
        "locator",
    ),
    "locator.selection.ambiguous": FindingPresentation(
        "Ambiguous Locator Selection",
        "The locator selects more than one item where one is required.",
        "locator",
    ),
    "locator.selection.empty": FindingPresentation(
        "Empty Locator Selection", "The locator selects no item.", "locator"
    ),
    "locator.selection.too_large": FindingPresentation(
        "Locator Selection Too Large",
        "The locator selection exceeds its validation bound.",
        "locator",
    ),
    "locator.source.changed": FindingPresentation(
        "Source Changed During Validation",
        "The source changed while it was being validated.",
        "path",
    ),
    "locator.source.format_mismatch": FindingPresentation(
        "Source Format Mismatch",
        "The source content does not match the declared or detected format.",
        "path",
    ),
    "locator.source.too_large": FindingPresentation(
        "Source Too Large", "The source exceeds the bounded reader limit.", "path"
    ),
    "locator.source.unsafe": FindingPresentation(
        "Unsafe Source Encoding",
        "The source requires an unsafe or prohibited decoding mode.",
        "path",
    ),
    "locator.source.unsupported": FindingPresentation(
        "Unsupported Source Format",
        "The source format is not supported for mechanical selection.",
        "path",
    ),
    "locator.syntax.invalid": FindingPresentation(
        "Invalid Locator Syntax",
        "The locator does not satisfy the locator syntax contract.",
        "locator",
    ),
    "locator.text.decode": FindingPresentation(
        "Text Source Decode Failure",
        "The text source cannot be decoded as required.",
        "path",
    ),
    "locator.type.mismatch": FindingPresentation(
        "Locator Type Mismatch",
        "The selected value has a different type from the required type.",
        "locator",
    ),
    "material.candidate.unresolved": FindingPresentation(
        "Material Role Unresolved",
        "A path-like command value has no mechanically established input or "
        "output role.",
        "command",
    ),
    "material.direction.conflict": FindingPresentation(
        "Material Direction Conflict",
        "The same material is classified as both input and output in one invocation.",
        "path",
    ),
    "material.root.invalid": FindingPresentation(
        "Entry Material Root Used Directly",
        "The command uses an entry material root where a specific target is required.",
        "command",
    ),
    "material.unresolved": FindingPresentation(
        "Material Path Unresolved",
        "A declared command material cannot be resolved to a bounded path.",
        "command",
    ),
    "presentation.marker.duplicate": FindingPresentation(
        "Duplicate Evidence Marker",
        "More than one presentation marker uses the same evidence ID.",
        "record",
    ),
    "presentation.marker.invalid": FindingPresentation(
        "Invalid Evidence Marker",
        "The evidence marker is malformed or placed on an invalid value.",
        "record",
    ),
    "producer.ambiguous": FindingPresentation(
        "Ambiguous Starting Producer",
        "More than one invocation claims to produce this starting material.",
        "path",
    ),
    "producer.missing": FindingPresentation(
        "Missing Starting Producer",
        "Entry-local starting material has no recorded producer.",
        "path",
    ),
    "provenance.observation.unavailable": FindingPresentation(
        "Provenance Observation Unavailable",
        "Required provenance material could not be observed.",
        "path",
    ),
    "provenance.output.code_invalid": FindingPresentation(
        "Invalid Output Code Map",
        "The recorded output code dependency map is invalid.",
        "path",
    ),
    "provenance.output.missing": FindingPresentation(
        "Recorded Output Missing", "A recorded output target is missing.", "path"
    ),
    "provenance.output.signature_mismatch": FindingPresentation(
        "Output Signature Mismatch",
        "The producing invocation no longer matches the recorded output signature.",
        "path",
    ),
    "provenance.output.signature_unsupported": FindingPresentation(
        "Unsupported Output Signature",
        "The output record uses a signature form no longer accepted by the validator.",
        "path",
    ),
    "provenance.output.unconfirmed": FindingPresentation(
        "Output Awaits Confirmation",
        "The recorded output has not yet been confirmed by reproduction.",
        "path",
    ),
    "provenance.output.unrecorded": FindingPresentation(
        "Output Support Missing",
        "A claimed produced output has no output-support record.",
        "path",
    ),
    "provenance.resource.too_large": FindingPresentation(
        "Provenance Graph Limit Exceeded",
        "The provenance graph exceeds its validation bound.",
        "log",
    ),
    "pyrun.output.identity_invalid": FindingPresentation(
        "Output Identity Mismatch",
        "The current output bytes or kind do not match the recorded identity.",
        "path",
    ),
    "pyrun.outputs.invalid": FindingPresentation(
        "Invalid Output-Support Registry",
        "The output-support registry does not satisfy its schema or ownership "
        "contract.",
        "path",
    ),
    "pyrun.outputs.quarantine_failed": FindingPresentation(
        "Output-Support Quarantine Failed",
        "An invalid output-support registry could not be moved aside safely.",
        "path",
    ),
    "pyrun.outputs.quarantined": FindingPresentation(
        "Output-Support Registry Quarantined",
        "An invalid output-support registry was moved aside and requires Repair "
        "review.",
        "path",
    ),
    "pyrun.outputs.unavailable": FindingPresentation(
        "Output-Support Registry Unavailable",
        "The output-support registry could not be read.",
        "path",
    ),
    "pyrun.state.conflict": FindingPresentation(
        "Conflicting Execution State",
        "More than one execution-state format exists for the entry.",
        "path",
    ),
    "pyrun.state.invalid": FindingPresentation(
        "Invalid Execution State",
        "The execution-state registry does not satisfy its schema or ownership "
        "contract.",
        "path",
    ),
    "pyrun.state.quarantine_failed": FindingPresentation(
        "Execution-State Quarantine Failed",
        "Invalid execution state could not be moved aside safely.",
        "path",
    ),
    "pyrun.state.quarantined": FindingPresentation(
        "Execution State Quarantined",
        "Invalid execution state was moved aside and requires Repair review.",
        "path",
    ),
    "pyrun.state.unavailable": FindingPresentation(
        "Execution State Unavailable",
        "The execution-state registry could not be read.",
        "path",
    ),
    "retention.declaration.invalid": FindingPresentation(
        "Invalid Retention Declaration",
        "The retention record does not satisfy the retention contract.",
        "record",
    ),
    "retention.file.location_invalid": FindingPresentation(
        "Invalid Retention Registry Location",
        "The retention registry is not at its required entry-owned location.",
        "path",
    ),
    "retention.target.missing": FindingPresentation(
        "Retained Target Missing", "A declared retained target is missing.", "path"
    ),
    "summary.reference.coordinate_invalid": FindingPresentation(
        "Invalid Summary Table Coordinate",
        "The summary reference uses an invalid table coordinate.",
        "reference",
    ),
    "summary.reference.invalid": FindingPresentation(
        "Invalid Summary Reference",
        "The summary reference syntax or placement is invalid.",
        "reference",
    ),
    "summary.reference.mismatch": FindingPresentation(
        "Summary Value Mismatch",
        "The summary value does not match the referenced evidence.",
        "reference",
    ),
    "summary.reference.missing": FindingPresentation(
        "Missing Summary Reference",
        "A mechanical value in the summary has no evidence reference.",
        "reference",
    ),
    "summary.reference.target_invalid": FindingPresentation(
        "Invalid Summary Reference Target",
        "The summary reference points to an invalid entry or evidence target.",
        "reference",
    ),
    "summary.reference.unresolved": FindingPresentation(
        "Summary Reference Unresolved",
        "The summary reference cannot be resolved unambiguously.",
        "reference",
    ),
    "transformation.boolean.invalid": FindingPresentation(
        "Invalid Boolean Transformation",
        "The transformation does not define valid boolean presentation.",
        "record",
    ),
    "transformation.input.reference_invalid": FindingPresentation(
        "Invalid Transformation Input Reference",
        "The transformation refers to an input that is absent or invalid.",
        "record",
    ),
    "transformation.input.reused": FindingPresentation(
        "Transformation Input Reused",
        "The transformation consumes an input more than once where reuse is "
        "prohibited.",
        "record",
    ),
    "transformation.input.unused": FindingPresentation(
        "Transformation Input Unused",
        "A declared transformation input is not used.",
        "record",
    ),
    "transformation.nonfinite_unsupported": FindingPresentation(
        "Unsupported Nonfinite Value",
        "The transformation encounters a nonfinite value it cannot present.",
        "record",
    ),
    "transformation.output.shape": FindingPresentation(
        "Transformation Output Shape Mismatch",
        "The transformation output has the wrong scalar, row, or table shape.",
        "record",
    ),
    "transformation.output.too_large": FindingPresentation(
        "Transformation Output Too Large",
        "The transformation output exceeds its evaluation bound.",
        "record",
    ),
    "transformation.parse_failed": FindingPresentation(
        "Transformation Parse Failure",
        "A transformation expression cannot be parsed.",
        "record",
    ),
    "transformation.presentation.mismatch": FindingPresentation(
        "Transformed Presentation Mismatch",
        "The presented value does not match the transformed source value.",
        "record",
    ),
    "transformation.render.invalid": FindingPresentation(
        "Invalid Transformation Rendering",
        "The transformation requests an invalid rendering form.",
        "record",
    ),
    "transformation.scale.invalid": FindingPresentation(
        "Invalid Transformation Scale", "The transformation scale is invalid.", "record"
    ),
    "transformation.syntax.invalid": FindingPresentation(
        "Invalid Transformation Syntax",
        "The transformation does not satisfy its syntax contract.",
        "record",
    ),
    "transformation.table.direct_mismatch": FindingPresentation(
        "Direct Table Presentation Mismatch",
        "The presented table does not match the selected source table.",
        "record",
    ),
    "transformation.table.input_not_records": FindingPresentation(
        "Table Input Is Not Records",
        "A table transformation requires record-shaped input.",
        "record",
    ),
    "transformation.table.label_invalid": FindingPresentation(
        "Invalid Table Label",
        "A table transformation contains an invalid label.",
        "record",
    ),
    "transformation.table.order_mismatch": FindingPresentation(
        "Table Order Mismatch",
        "Presented rows or columns do not match the declared order.",
        "record",
    ),
    "transformation.type.mismatch": FindingPresentation(
        "Transformation Type Mismatch",
        "A transformation input or output has the wrong type.",
        "record",
    ),
    "transformation.version.unsupported": FindingPresentation(
        "Unsupported Transformation Version",
        "The transformation uses an unsupported contract version.",
        "record",
    ),
}


def load_report_context(summary: Path) -> ReportContext:
    """Read bounded entry titles and links from the validated summary."""

    summary = summary.absolute()
    try:
        if summary.stat().st_size > MAX_SUMMARY_BYTES:
            raise HumanProjectionError("maintained summary exceeds report bound")
        text = summary.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise HumanProjectionError(
            f"could not read maintained summary for reporting: {error}"
        ) from error
    first = text.splitlines()[0] if text else ""
    title = first[2:].strip() if first.startswith("# ") else summary.stem
    log_root = summary.with_suffix("")
    entries = _direct_entry_presentations(text, log_root)
    for entry_id, presentation in _split_entry_presentations(text, log_root).items():
        entries.setdefault(entry_id, presentation)
    return ReportContext(title or summary.stem, summary, log_root, entries)


def _direct_entry_presentations(
    text: str, log_root: Path
) -> dict[str, EntryPresentation]:
    entries: dict[str, EntryPresentation] = {}
    for match in ENTRY_LINK_RE.finditer(text):
        raw_target = match.group("target").strip("<>")
        target = _entry_target(raw_target, log_root)
        if target is None:
            continue
        entry_id = Path(target.name).stem.lower()
        if ENTRY_ID_RE.fullmatch(entry_id) is None:
            continue
        document = target.as_posix()
        root = log_root.joinpath(*target.parts[:-1])
        entries.setdefault(
            entry_id,
            EntryPresentation(
                entry_id,
                match.group("title").strip(),
                document,
                root.absolute(),
            ),
        )
    return entries


def _split_entry_presentations(
    text: str, log_root: Path
) -> dict[str, EntryPresentation]:
    entries: dict[str, EntryPresentation] = {}
    parent_title: str | None = None
    for line in text.splitlines():
        parent = SPLIT_ENTRY_PARENT_RE.fullmatch(line)
        if parent is not None:
            parent_title = parent.group("title").strip()
            continue
        if parent_title is None or not line.startswith("  - "):
            if line.strip():
                parent_title = None
            continue
        split_match = ENTRY_LINK_RE.search(line)
        if split_match is None:
            continue
        raw_target = split_match.group("target").strip("<>")
        target = _entry_target(raw_target, log_root)
        if target is None:
            continue
        folder = next(
            (
                value
                for part in target.parts
                if (value := ENTRY_FOLDER_ID_RE.fullmatch(part)) is not None
            ),
            None,
        )
        if folder is None:
            continue
        entry_id = folder.group("entry").lower()
        document = target.as_posix()
        root = log_root.joinpath(*target.parts[:-1])
        entries.setdefault(
            entry_id,
            EntryPresentation(
                entry_id,
                parent_title,
                document,
                root.absolute(),
            ),
        )
    return entries


def _entry_target(raw_target: str, log_root: Path) -> PurePosixPath | None:
    target = PurePosixPath(raw_target)
    parts = target.parts
    if parts and parts[0] == log_root.name:
        target = PurePosixPath(*parts[1:])
    if not target.parts or target.parts[0] != "entries":
        return None
    return target


def project_findings(
    record: MechanicalGeneratedRecord,
    context: ReportContext | None = None,
) -> tuple[FindingGroup, ...]:
    """Project direct non-passing checks into deterministic issue groups."""

    context = context or ReportContext.empty(Path(record.summary))
    impacted = _dependent_impacts(record.checks)
    grouped: dict[
        tuple[str, ...],
        tuple[MechanicalCheck, FindingPresentation, str | None, str],
    ] = {}
    identities: dict[tuple[str, ...], list[str]] = defaultdict(list)
    impacts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for check in record.checks:
        if check.status not in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}:
            continue
        if check.failure is None:
            raise HumanProjectionError(
                f"direct non-passing check has no failure: {check.identity}"
            )
        presentation = CATALOG.get(check.failure.code)
        if presentation is None:
            raise HumanProjectionError(
                f"missing human presentation for {check.failure.code}"
            )
        entry = _entry_id(check, context)
        subject = logical_subject(check, context, entry)
        observed = json.dumps(
            dict(check.failure.observed),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        signature: tuple[str, ...] = (
            check.status.value,
            check.failure.code,
            entry or "",
            subject,
            check.failure.rule,
            observed,
        )
        grouped.setdefault(signature, (check, presentation, entry, subject))
        identities[signature].append(check.identity)
        impacts[signature].update(impacted.get(check.identity, ()))
    result = []
    for signature, (check, presentation, entry, subject) in grouped.items():
        assert check.failure is not None
        result.append(
            FindingGroup(
                check.status,
                check.scope,
                check.failure.code,
                entry,
                subject,
                check.failure.rule,
                dict(check.failure.observed),
                presentation,
                tuple(sorted(identities[signature])),
                len(impacts[signature]),
            )
        )
    return tuple(sorted(result, key=finding_sort_key))


def finding_sort_key(group: FindingGroup) -> tuple[object, ...]:
    """Return the stable human and machine inventory order."""

    entry = _entry_sort_key(group.entry)
    return (
        *entry,
        group.presentation.name.casefold(),
        group.code,
        group.subject.casefold(),
        group.status.value,
        json.dumps(
            dict(group.observed),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        group.check_ids[0],
    )


def logical_subject(
    check: MechanicalCheck,
    context: ReportContext,
    entry: str | None = None,
) -> str:
    """Return one bounded human logical subject without reading source files."""

    failure = check.failure
    subject = check.subject
    summary_match = SUMMARY_LINE_RE.fullmatch(subject)
    if summary_match is not None:
        return f"Summary line {summary_match.group('line')}"
    observed = _observed_subject(failure)
    if observed is not None:
        return observed
    entry_relative = _entry_relative_subject(subject, context, entry)
    if entry_relative is not None:
        return entry_relative
    relative = _relative_path(subject, context.log_root)
    if relative is not None:
        return _short_log_path(relative)
    return _fallback_subject(subject)


def _observed_subject(failure: object) -> str | None:
    if not isinstance(failure, FailurePayload):
        return None
    for key in ("relative", "output"):
        value = failure.observed.get(key)
        if isinstance(value, str) and value:
            return _portable(value)
    return None


def _entry_relative_subject(
    subject: str,
    context: ReportContext,
    entry: str | None,
) -> str | None:
    if entry is None or entry not in context.entries:
        return None
    return _relative_path(subject, context.entries[entry].root)


def _short_log_path(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if len(parts) >= 4 and parts[0] == "entries":
        return PurePosixPath(*parts[2:]).as_posix()
    return relative


def _fallback_subject(subject: str) -> str:
    if subject.startswith("entries/") and ":" in subject:
        return subject.rsplit(":", 1)[-1]
    if _looks_absolute(subject):
        return f"<external>/{Path(subject).name}"
    return _portable(subject)


def area_results(
    record: MechanicalGeneratedRecord,
    groups: Sequence[FindingGroup],
) -> Mapping[str, str]:
    """Return the shared four-area human result vocabulary."""

    by_scope: dict[CheckScope, list[FindingGroup]] = defaultdict(list)
    for group in groups:
        by_scope[group.scope].append(group)
    return {
        "Structure": _ordinary_area(
            record, CheckScope.CONFORMANCE, by_scope[CheckScope.CONFORMANCE]
        ),
        "Evidence": _ordinary_area(
            record, CheckScope.EVIDENCE, by_scope[CheckScope.EVIDENCE]
        ),
        "Provenance": _provenance_area(record, by_scope[CheckScope.PROVENANCE]),
        "Hygiene": _ordinary_area(
            record, CheckScope.ORPHAN, by_scope[CheckScope.ORPHAN]
        ),
    }


def provenance_artifact_counts(
    record: MechanicalGeneratedRecord,
) -> Mapping[str, int]:
    """Count unique provenance artifacts by their worst human status."""

    failure_affected = _failure_affected_provenance_checks(record.checks)
    artifacts: dict[str, set[CheckStatus]] = defaultdict(set)
    for check in record.checks:
        if check.scope is not CheckScope.PROVENANCE:
            continue
        for artifact in _check_artifacts(check):
            status = check.status
            if (
                check.failure is not None
                and check.failure.code == "provenance.output.unconfirmed"
            ):
                status = CheckStatus.UNAVAILABLE
            elif (
                status is CheckStatus.NOT_APPLICABLE
                and check.identity in failure_affected
            ):
                status = CheckStatus.FAIL
            artifacts[artifact].add(status)
    counts = {status.value: 0 for status in CheckStatus}
    for statuses in artifacts.values():
        for status in (
            CheckStatus.FAIL,
            CheckStatus.UNAVAILABLE,
            CheckStatus.PASS,
            CheckStatus.NOT_APPLICABLE,
        ):
            if status in statuses:
                counts[status.value] += 1
                break
    return counts


def _ordinary_area(
    record: MechanicalGeneratedRecord,
    scope: CheckScope,
    groups: Sequence[FindingGroup],
) -> str:
    checks = [check for check in record.checks if check.scope is scope]
    if any(group.status is CheckStatus.UNAVAILABLE for group in groups):
        return "Incomplete"
    if groups:
        return f"{len(groups)} {_plural(len(groups), 'issue')}"
    if (
        checks
        and all(check.status is CheckStatus.NOT_APPLICABLE for check in checks)
        and any(check.dependencies for check in checks)
    ):
        return "—"
    return "Clear"


def _provenance_area(
    record: MechanicalGeneratedRecord,
    groups: Sequence[FindingGroup],
) -> str:
    if any(group.status is CheckStatus.UNAVAILABLE for group in groups):
        return "Incomplete"
    counts = provenance_artifact_counts(record)
    values = []
    failed = counts[CheckStatus.FAIL.value]
    unconfirmed = counts[CheckStatus.UNAVAILABLE.value]
    if failed:
        values.append(f"{failed} artifact {_plural(failed, 'issue')}")
    if unconfirmed:
        values.append(f"{unconfirmed} await confirmation")
    if values:
        return " · ".join(values)
    checks = [check for check in record.checks if check.scope is CheckScope.PROVENANCE]
    if (
        checks
        and all(check.status is CheckStatus.NOT_APPLICABLE for check in checks)
        and any(check.dependencies for check in checks)
    ):
        return "—"
    return "Clear"


def _entry_id(
    check: MechanicalCheck,
    context: ReportContext,
) -> str | None:
    match = ENTRY_TOKEN_RE.search(check.identity)
    if match is not None:
        return match.group(1).lower()
    if check.failure is not None:
        for key in ("entry", "owner", "document"):
            value = check.failure.observed.get(key)
            if not isinstance(value, str):
                continue
            match = ENTRY_DIRECTORY_RE.search(value.replace("\\", "/"))
            if match is not None:
                return match.group(1).lower()
            if ENTRY_ID_RE.fullmatch(value):
                return value.lower()
    for entry_id, entry in context.entries.items():
        if _relative_path(check.subject, entry.root) is not None:
            return entry_id
    return None


def _dependent_impacts(
    checks: Sequence[MechanicalCheck],
) -> Mapping[str, frozenset[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    not_applicable = {
        check.identity for check in checks if check.status is CheckStatus.NOT_APPLICABLE
    }
    for check in checks:
        if check.identity not in not_applicable:
            continue
        for dependency in check.dependencies:
            identity = dependency.get("dependency")
            if isinstance(identity, str) and identity:
                reverse[identity].add(check.identity)
    result: dict[str, frozenset[str]] = {}
    for check in checks:
        if check.status not in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}:
            continue
        found: set[str] = set()
        queue = deque(reverse.get(check.identity, ()))
        while queue:
            identity = queue.popleft()
            if identity in found:
                continue
            found.add(identity)
            queue.extend(reverse.get(identity, ()))
        result[check.identity] = frozenset(found)
    return result


def _failure_affected_provenance_checks(
    checks: Sequence[MechanicalCheck],
) -> set[str]:
    affected = {
        check.identity
        for check in checks
        if check.scope is CheckScope.PROVENANCE
        and check.status is CheckStatus.FAIL
        and (
            check.failure is None
            or check.failure.code != "provenance.output.unconfirmed"
        )
    }
    pending = [
        check
        for check in checks
        if check.scope is CheckScope.PROVENANCE
        and check.status is CheckStatus.NOT_APPLICABLE
    ]
    while pending:
        remaining = []
        changed = False
        for check in pending:
            if _check_dependencies(check) & affected:
                affected.add(check.identity)
                changed = True
            else:
                remaining.append(check)
        if not changed:
            break
        pending = remaining
    return affected


def _check_artifacts(check: MechanicalCheck) -> set[str]:
    artifacts: set[str] = set()
    for dependency in check.dependencies:
        values = dependency.get("artifacts")
        if isinstance(values, list):
            artifacts.update(
                value for value in values if isinstance(value, str) and value
            )
    return artifacts


def _check_dependencies(check: MechanicalCheck) -> set[str]:
    dependencies: set[str] = set()
    for dependency in check.dependencies:
        value = dependency.get("dependency")
        if isinstance(value, str) and value:
            dependencies.add(value)
    return dependencies


def _entry_sort_key(entry: str | None) -> tuple[int, int, str]:
    if entry is None:
        return (1, 0, "")
    match = re.fullmatch(r"e(?P<number>[0-9]+)(?P<suffix>[a-z]?)", entry, re.I)
    if match is None:
        return (0, 0, entry.casefold())
    return (0, int(match.group("number")), match.group("suffix").casefold())


def _relative_path(value: str, root: Path) -> str | None:
    if not _looks_absolute(value):
        return None
    try:
        return Path(value).absolute().relative_to(root.absolute()).as_posix()
    except ValueError:
        return None


def _looks_absolute(value: str) -> bool:
    return value.startswith("/") or bool(re.match(r"[A-Za-z]:[/\\]", value))


def _portable(value: str) -> str:
    return value.replace("\\", "/")


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"
