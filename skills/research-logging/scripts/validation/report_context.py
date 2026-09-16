"""Human wording and bounded research-log report context."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ENTRY_ID_RE = re.compile(r"e[0-9]{3,}[a-z]?\Z", re.I)
ENTRY_LINK_RE = re.compile(
    r"\[(?P<title>[^\]\r\n]+)\]"
    r"\((?P<target><?[^()\s\r\n]+>?)\)"
)
SPLIT_ENTRY_PARENT_RE = re.compile(r"^- `[0-9]{4}-[0-9]{2}-[0-9]{2}` (?P<title>.+):$")
ENTRY_FOLDER_ID_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}-(?P<entry>e[0-9]{3,})-.+\Z"
)
MAX_SUMMARY_BYTES = 8 * 1024 * 1024


class PresentationError(ValueError):
    """Raised when bounded presentation context cannot be assembled."""


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


CATALOG: Mapping[str, FindingPresentation] = {
    "association.artifact.content_mismatch": FindingPresentation(
        "Inline Artifact Mismatch",
        "The inline artifact presentation differs from its declared source.",
        "record",
    ),
    "association.artifact.fingerprint_mismatch": FindingPresentation(
        "Artifact Fingerprint Mismatch",
        "The linked artifact bytes differ from the baseline accepted "
        "in its evidence record.",
        "record",
    ),
    "association.artifact.fingerprint_unrecorded": FindingPresentation(
        "Unrecorded Artifact Fingerprint",
        "The linked artifact has no accepted byte baseline in its evidence record.",
        "record",
    ),
    "association.artifact.inline_source_invalid": FindingPresentation(
        "Invalid Inline Artifact Source",
        "The declared inline artifact source is not a regular UTF-8 file.",
        "path",
    ),
    "association.artifact.inline_source_unavailable": FindingPresentation(
        "Inline Artifact Source Unavailable",
        "The declared inline artifact source could not be read reliably.",
        "path",
    ),
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
        "The material differs from its recorded observation, "
        "or its selected Git commit cannot be verified.",
        "record",
    ),
    "data.fingerprint.unobserved": FindingPresentation(
        "Unobserved Generated Fingerprint",
        "The generated material lacks the required retained execution observation.",
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
    "data.output.declaration_invalid": FindingPresentation(
        "Invalid Generated Output Declaration",
        "A named command output is not a valid generated artifact declaration.",
        "command",
    ),
    "data.output.token_missing": FindingPresentation(
        "Missing Named Output Token",
        "A command writes declared generated material without its named token.",
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
    "evidence.definition.unsynchronized": FindingPresentation(
        "Unsynchronized Evidence Definition",
        "The Markdown definition differs from its maintained evidence record; "
        "run log evidence sync for that ID.",
        "record",
    ),
    "evidence.definition.invalid": FindingPresentation(
        "Invalid Markdown Evidence Definition",
        "Correct the evidence definition in its eid comment and synchronize it.",
        "record",
    ),
    "evidence.marker.unresolved": FindingPresentation(
        "Unresolved Evidence Marker",
        "The evidence ID has no unique adjacent marker.",
        "record",
    ),
    "evidence.marker.invalid": FindingPresentation(
        "Invalid Evidence Marker",
        "The eid comment is not adjacent to a supported presentation.",
        "record",
    ),
    "evidence.pointer.invalid": FindingPresentation(
        "Invalid Evidence Pointer",
        "Use a valid source-field pointer in the eid comment.",
        "record",
    ),
    "evidence.condition.invalid": FindingPresentation(
        "Invalid Evidence Filter",
        "Correct the typed row filter in the eid comment.",
        "record",
    ),
    "evidence.render.invalid": FindingPresentation(
        "Invalid Evidence Rendering",
        "Correct the bounded renderer in the eid comment.",
        "record",
    ),
    "evidence.table.unsupported": FindingPresentation(
        "Unsupported Evidence Table",
        "Record a script to produce a table-shaped artifact for "
        "joined or derived data.",
        "record",
    ),
    "evidence.table.columns": FindingPresentation(
        "Invalid Evidence Columns",
        "Bind each Markdown heading position to one source field and renderer.",
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
    "orphan.output.unmatched": FindingPresentation(
        "Unmatched Recorded Output",
        "A recorded output is not used by current evidence or provenance.",
        "path",
    ),
    "orphan.generated.residue": FindingPresentation(
        "Obsolete Validation Artifact",
        "An artifact from an unsupported validation layout remains in the log.",
        "path",
    ),
    "invocation.command.unsupported": FindingPresentation(
        "Unsupported Recorded Command",
        "The recorded command uses shell syntax outside the supported command grammar.",
        "command",
    ),
    "invocation.cid.duplicate": FindingPresentation(
        "Duplicate Command ID",
        "More than one recorded command or loop uses the same command ID "
        "in this entry. Add distinct numeric or full --cid overrides.",
        "command",
    ),
    "invocation.cid.parameter_collision": FindingPresentation(
        "Duplicate Command Parameters",
        "Two expansions under one command ID have the same parameter identity.",
        "command",
    ),
    "invocation.cid.unstable": FindingPresentation(
        "Unstable Command ID",
        "Expansions of one recorded command or loop use different command IDs. "
        "Add one full explicit --cid shared by the complete owner.",
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
    "invocation.fence.multiple_commands": FindingPresentation(
        "Multiple Commands In One Fence",
        "The command fence contains more than one independent command or loop; "
        "split it into separate fences.",
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
    "locator.text.range": FindingPresentation(
        "Text Slice Out Of Range",
        "The requested one-based inclusive line or character range exceeds the source.",
        "locator",
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
    "provenance.output.execution_unassociated": FindingPresentation(
        "Execution Association Missing",
        "The recorded execution no longer matches the current producing command.",
        "path",
    ),
    "provenance.output.signature_mismatch": FindingPresentation(
        "Output Producer Signature Mismatch",
        "The retained output's producer signature disagrees with its recorded "
        "invocation.",
        "path",
    ),
    "provenance.output.signature_unsupported": FindingPresentation(
        "Unsupported Output Signature",
        "The output record uses a signature form no longer accepted by the validator.",
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
    "reproduction.comparison.declaration_conflict": FindingPresentation(
        "Conflicting Reproduction Comparison",
        "The same artifact has inconsistent evidence-scoped comparison metadata.",
        "path",
    ),
    "reproduction.comparison.evidence_incompatible": FindingPresentation(
        "Incompatible Reproduction Evidence",
        "An evidence-scoped artifact depends on evidence that cannot be extracted "
        "for reproduction.",
        "record",
    ),
    "reproduction.comparison.evidence_missing": FindingPresentation(
        "Reproduction Evidence Missing",
        "An evidence-scoped artifact has no applicable evidence records.",
        "path",
    ),
    "reproduction.comparison.tolerance_incompatible": FindingPresentation(
        "Incompatible Reproduction Tolerance",
        "A reproduction tolerance does not apply to a numeric selected value.",
        "record",
    ),
    "pyrun.command.missing": FindingPresentation(
        "Missing Recorded Execution",
        "A current recorded command has no matching retained execution state.",
        "command",
    ),
    "pyrun.command.recipe_changed": FindingPresentation(
        "Recorded Command Recipe Changed",
        "The current recorded command recipe differs from its retained execution.",
        "command",
    ),
    "pyrun.command.stale": FindingPresentation(
        "Stale Recorded Execution",
        "Retained execution state has no exact current recorded-command match.",
        "record",
    ),
    "pyrun.output.identity_invalid": FindingPresentation(
        "Output Identity Mismatch",
        "The current output bytes or kind do not match the recorded identity.",
        "path",
    ),
    "pyrun.output.binding_invalid": FindingPresentation(
        "Invalid Output Binding",
        "A recorded output cannot be redirected through exactly one canonical "
        "parameter or runner-owned capture.",
        "path",
    ),
    "pyrun.policy.mismatch": FindingPresentation(
        "Reproduction Policy Mismatch",
        "The Markdown command and its execution record disagree about "
        "automatic reproduction.",
        "record",
    ),
    "pyrun.exclusive.mismatch": FindingPresentation(
        "Exclusive Reproduction Policy Mismatch",
        "The Markdown command and its execution record disagree about "
        "exclusive scheduling.",
        "record",
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
    "pyrun.state.schema.unsupported": FindingPresentation(
        "Unsupported Execution-State Schema",
        "The execution-state registry uses a schema that is no longer supported.",
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
            raise PresentationError("maintained summary exceeds report bound")
        text = summary.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PresentationError(
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
