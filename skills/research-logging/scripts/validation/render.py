"""Typed assembly of deterministic research-log validation render results."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .adjudication import (
    COMPLETE_SCOPE_DESCRIPTION,
    ORPHAN_TARGET,
    AdjudicationPreparationPolicy,
    is_success_date,
    prepare_adjudication,
)
from .contracts import (
    AdjudicationRecord,
    FileChangedError,
    LifecycleRecordContractError,
    RenderCounts,
    ScanRecord,
    ValidationToolError,
    decode_adjudication_record,
    decode_scan_record,
)
from .decision_store import ValidationDecisionStore, build_decision_store
from .decisions import semantic_failure_bases
from .discovery import section_ranges
from .evidence import (
    inspect_structure,
    mechanical_evidence_support,
)
from .graph import (
    DependencyGraph,
    EdgeKind,
    NodeKind,
    RootPolicy,
)
from .graph_adapter import build_dependency_graph
from .graph_queries import (
    display_identity,
    orphan_locations,
    provenance_nodes,
    target_provenance_seeds,
)
from .graph_store import (
    SLICE_FILENAME,
    repository_identity_path,
    slice_record,
)
from .identities import (
    text_content_identity,
    validation_file_identity,
)
from .incremental import (
    current_check_dependency_contract,
    dependency_identity_snapshot,
    orphan_item_fingerprints,
)
from .inventory import (
    MaterialInventoryPolicy,
    collection_identity,
    directory_membership_identity,
)
from .lint import LintPolicy, lint_validation_records
from .publication import (
    ValidationPublicationTarget,
    publish_validation_bundle,
)
from .records import LOCK_FILENAME, record_bundle_identity
from .report import install_status_summary
from .scan import local_snapshot_identity

Failure = tuple[str, str, dict[str, Any]]
RESULT_VALUES = {"FAIL", "-", "N/A"}


@dataclass(frozen=True)
class RenderLifecyclePolicy:
    """Version and inventory contracts for canonical render publication."""

    scan_schema_version: int
    adjudication_schema_version: int
    state_schema_version: int
    rules_version: str
    orphan_inventory_version: int
    record_filenames: tuple[str, ...]
    material_inventory_policy: MaterialInventoryPolicy


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_graph_slice(
    graph: DependencyGraph, check: Mapping[str, Any]
) -> Dict[str, Any]:
    """Return the minimum persisted graph contract supporting one outcome."""

    identities = {
        dependency["path"]
        for dependency in check.get("dependencies", [])
        if isinstance(dependency, dict) and isinstance(dependency.get("path"), str)
    }
    seeds = {
        node.key
        for node in graph.nodes
        if display_identity(graph, node.key) in identities
    }
    if check.get("check") in {"Provenance", "Reproducibility"}:
        target_seeds = target_provenance_seeds(
            graph,
            check.get("entry", ""),
            check.get("target", ""),
            check.get("dependencies", []),
            check.get("resolution", {}).get("producer_invocation"),
        )
        selected = provenance_nodes(
            graph,
            ((seed, RootPolicy.PRESENTED) for seed in (target_seeds or seeds)),
        )
    else:
        selected = seeds
    candidate_edges = tuple(
        edge
        for node in selected
        if node.kind in {
            NodeKind.ARTIFACT,
            NodeKind.COLLECTION,
            NodeKind.MEMBER,
            NodeKind.EXTERNAL_SOURCE,
        }
        for edge in graph.incoming(node, {EdgeKind.PRODUCES, EdgeKind.CAPTURES})
    )
    selected = selected | {edge.source for edge in candidate_edges}
    edges = tuple(
        edge
        for edge in graph.edges
        if edge.source in selected and edge.target in selected
    )
    roots = tuple(root for root in graph.roots if root.node in selected)
    payload = {
        "nodes": [graph.node(key).as_dict() for key in sorted(selected)],
        "edges": [edge.as_dict() for edge in edges],
        "roots": [root.as_dict() for root in roots],
    }
    return {
        "identity": _json_fingerprint(payload),
        "nodes": [key.as_dict() for key in sorted(selected)],
        "edges": [edge.identity for edge in edges],
        "roots": [
            {"node": root.node.as_dict(), "policy": root.policy.value} for root in roots
        ],
    }


def validation_result(value: Any, field: str) -> str:
    """Return a syntactically valid validation result."""

    if value in RESULT_VALUES or is_success_date(value):
        return value
    raise ValidationToolError(f"{field} must be a date, FAIL, -, or N/A; got {value!r}")


def checked_result(value: Any, field: str) -> str:
    """Return a result that represents an attempted validation check."""

    result = validation_result(value, field)
    if result in {"-", "N/A"}:
        raise ValidationToolError(f"{field} must be a successful date or FAIL")
    return result


def _code_result(value: str) -> str:
    return f"`{value}`" if value in RESULT_VALUES else value


def _cell(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    return str(value).replace("\n", " ").replace("|", "\\|")


def dependencies_for_check(row: Mapping[str, Any], check: str) -> List[Dict[str, Any]]:
    """Return the normalized dependency contract for one check."""

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


def finding_map(row: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Group one row's findings by validation check."""

    result: Dict[str, List[str]] = {}
    for finding in row.get("findings", []):
        result.setdefault(finding["check"], []).append(finding["finding"])
    return result


def _validate_failed_findings(
    row: Mapping[str, Any], checks: Iterable[str], identity: str
) -> None:
    findings = finding_map(row)
    for check in checks:
        if row[check.lower()] == "FAIL" and not findings.get(check):
            raise ValidationToolError(f"missing {check} finding for {identity}")


def _validate_summary_support(
    row: Mapping[str, Any],
    scan_entries: Mapping[str, Mapping[str, Any]],
    scan: Mapping[str, Any],
) -> None:
    support = row.get("support_evidence")
    if not isinstance(support, list) or not support:
        raise ValidationToolError(
            f"successful Summary row lacks exact support evidence: {row['item']}"
        )
    for evidence in support:
        _validate_summary_support_item(evidence, row, scan_entries, scan)


def _validate_summary_support_item(
    evidence: Mapping[str, Any],
    row: Mapping[str, Any],
    scan_entries: Mapping[str, Mapping[str, Any]],
    scan: Mapping[str, Any],
) -> None:
    """Validate one exact summary-to-entry support excerpt."""

    if set(evidence) != {"entry", "section", "lines", "text"}:
        raise ValidationToolError("Summary support evidence has incorrect keys")
    if evidence["entry"] not in row.get("entries", []):
        raise ValidationToolError("Summary support evidence names an unlisted entry")
    entry = scan_entries.get(evidence["entry"])
    if entry is None or evidence["section"] not in row.get("sections", []):
        raise ValidationToolError("Summary support evidence names an unlisted section")
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", str(evidence["lines"]))
    if not match:
        raise ValidationToolError("Summary support evidence has an invalid line range")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    path = Path(scan["resolved_paths"][entry["path"]])
    lines = path.read_text(encoding="utf-8").splitlines()
    if start < 1 or end < start or end > len(lines):
        raise ValidationToolError(
            "Summary support evidence line range is outside the entry"
        )
    sections = section_ranges(lines)
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


@dataclass(frozen=True)
class GeneratedValidationBundle:
    """Deterministic canonical content produced before publication."""

    report_text: str
    failure_text: Optional[str]
    decisions: ValidationDecisionStore
    state: Mapping[str, Any]
    graph_record: Mapping[str, Any]


@dataclass(frozen=True)
class RenderedSummary:
    """Rendered summary rows and the checks they establish."""

    lines: list[str]
    failures: list[Failure]
    completed_checks: list[dict[str, Any]]
    failed: int


@dataclass(frozen=True)
class RenderedTarget:
    """One rendered entry target and its completed checks."""

    line: str
    failure: Optional[Failure]
    completed_checks: list[dict[str, Any]]


@dataclass(frozen=True)
class RenderedEntries:
    """Rendered entry sections and their aggregate counts."""

    lines: list[str]
    failures: list[Failure]
    completed_checks: list[dict[str, Any]]
    total: int
    failed: int
    failed_entries: int
    reported_entries: int
    orphan_scopes: int


@dataclass(frozen=True)
class RenderedReport:
    """Complete deterministic report content before state materialization."""

    report_text: str
    failure_text: Optional[str]
    failures: Sequence[Failure]
    completed_checks: Sequence[Dict[str, Any]]
    summary_rows: int
    summary_failed: int
    entry_rows: int
    entry_failed: int
    entries: int
    failed_entries: int
    orphan_scopes: int


@dataclass(frozen=True)
class OrphanDispositionPolicy:
    """Version and fingerprint operation for persisted orphan dispositions."""

    inventory_version: int
    item_fingerprints: Callable[
        [Mapping[str, Any], Mapping[str, Any]], Mapping[str, str]
    ]


@dataclass(frozen=True)
class RenderMeasurements:
    """Canonical counts shared by the state record and command result."""

    summary_rows: int
    summary_failed: int
    entry_rows: int
    entry_failed: int
    entries: int
    failed_entries: int
    successful_checks: int
    completed_checks: int
    file_identities: int
    failure_rows: int

    def result(
        self,
        *,
        date: str,
        mode: str,
        requested_scope: str,
        scope: Mapping[str, Any],
        failures: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Return the persisted result summary."""

        return {
            "date": date,
            "mode": mode,
            "requested_scope": requested_scope,
            "scope": dict(scope),
            "summary_rows": self.summary_rows,
            "summary_failed": self.summary_failed,
            "entry_rows": self.entry_rows,
            "entry_failed": self.entry_failed,
            "entries": self.entries,
            "failed_entries": self.failed_entries,
            "failure_rows": self.failure_rows,
            "failures": list(failures),
        }

    def counts(self) -> RenderCounts:
        """Return the public render command counts."""

        return {
            "summary_rows": self.summary_rows,
            "summary_failed": self.summary_failed,
            "entry_rows": self.entry_rows,
            "entry_failed": self.entry_failed,
            "entries": self.entries,
            "failed_entries": self.failed_entries,
            "successful_checks": self.successful_checks,
            "completed_checks": self.completed_checks,
            "file_identities": self.file_identities,
            "failure_rows": self.failure_rows,
        }


@dataclass(frozen=True)
class RenderStateInputs:
    """Validated inputs needed to serialize one canonical state record."""

    schema_version: int
    rules_version: str
    local_snapshot_identity: str
    input_fingerprint: str
    input_files: Mapping[str, Any]
    mechanical_checks: Mapping[str, Any]
    directory_memberships: Mapping[str, Any]
    file_identities: Mapping[str, Any]
    completed_checks: Sequence[Mapping[str, Any]]
    orphan_dispositions: Sequence[Mapping[str, Any]]
    report_identity: Mapping[str, Any]
    graph_identity: str

    def record(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """Return the persisted validation-state record."""

        return {
            "schema_version": self.schema_version,
            "validation_rules_version": self.rules_version,
            "local_snapshot_identity": self.local_snapshot_identity,
            "input_fingerprint": self.input_fingerprint,
            "input_files": dict(self.input_files),
            "mechanical_checks": dict(self.mechanical_checks),
            "directory_memberships": dict(self.directory_memberships),
            "files": dict(self.file_identities),
            "completed_checks": list(self.completed_checks),
            "orphan_dispositions": list(self.orphan_dispositions),
            "result": dict(result),
            "report": dict(self.report_identity),
            "graph_identity": self.graph_identity,
        }


@dataclass(frozen=True)
class RenderAssembly:
    """Canonical generated bundle plus its command-facing measurements."""

    report_text: str
    failure_text: Optional[str]
    graph_record: Mapping[str, Any]
    state_inputs: RenderStateInputs
    measurements: RenderMeasurements
    date: str
    mode: str
    requested_scope: str
    scope: Mapping[str, Any]
    failures: Sequence[Mapping[str, Any]]
    local_snapshot_identity: str

    def bundle(self) -> GeneratedValidationBundle:
        """Build deterministic content without performing publication I/O."""

        result = self.measurements.result(
            date=self.date,
            mode=self.mode,
            requested_scope=self.requested_scope,
            scope=self.scope,
            failures=self.failures,
        )
        return GeneratedValidationBundle(
            self.report_text,
            self.failure_text,
            build_decision_store(
                self.state_inputs.completed_checks,
                self.state_inputs.orphan_dispositions,
                validation_rules_version=self.state_inputs.rules_version,
                local_snapshot_identity=self.local_snapshot_identity,
                report_date=self.date,
            ),
            self.state_inputs.record(result),
            self.graph_record,
        )

    def counts(self) -> RenderCounts:
        """Return the public command result for this assembly."""

        return self.measurements.counts()


def report_header(
    adjudication: AdjudicationRecord, scan: ScanRecord, date: str
) -> List[str]:
    """Return the canonical report header."""

    lines = [
        "# Research-Log Validation",
        "",
        f"- Log: `{adjudication['log']}`",
        f"- Requested scope: {adjudication['requested_scope']}",
        f"- Report-update date: `{date}`",
        f"- Validation mode: {adjudication['mode']}",
        f"- Validation-rules version: `{adjudication['validation_rules_version']}`",
        f"- Local snapshot identity: `{local_snapshot_identity(scan)}`",
    ]
    if not scan["repository_scope"]["cross_log_complete"]:
        lines.append(
            "- Cross-log orphan review: DEFERRED pending complete current-rule slices"
        )
    for summary, snapshot in sorted(scan["repository_slices"].items()):
        lines.append(
            "- Contributing cross-log slice: "
            f"`{summary}` at `{snapshot['graph_identity']}`"
        )
    for summary, reason in sorted(
        scan["repository_scope"].get("excluded_slices", {}).items()
    ):
        lines.append(f"- Excluded cross-log slice: `{summary}` — {reason}")
    return lines


def _render_summary_row(
    row: Dict[str, Any],
    scan_entries: Mapping[str, Mapping[str, Any]],
    scan: ScanRecord,
) -> Tuple[str, Optional[Failure], Dict[str, Any]]:
    if row.get("support_reviewed") is not True:
        raise ValidationToolError(
            f"Summary support was not explicitly reviewed: {row['item']}"
        )
    provenance = checked_result(row.get("provenance"), "Summary provenance")
    entries = row.get("entries", [])
    sections = row.get("sections", [])
    if provenance == "FAIL":
        _validate_failed_findings(row, ("Provenance",), row["item"])
        if entries or sections:
            raise ValidationToolError(
                "failed unsupported Summary rows must use empty entries and sections"
            )
        failure: Optional[Failure] = ("Summary", row["item"], row)
        completed = {
            "entry": "Summary",
            "target": row["item"],
            "check": "Provenance",
            "result": "FAIL",
            "findings": finding_map(row)["Provenance"],
            "dependencies": dependencies_for_check(row, "Provenance"),
        }
    else:
        if len(entries) != 1 or len(sections) != 1:
            raise ValidationToolError(
                "successful Summary row requires exactly one entry and section: "
                f"{row['item']}"
            )
        _validate_summary_support(row, scan_entries, scan)
        dependencies = dependencies_for_check(row, "Provenance")
        if not dependencies:
            raise ValidationToolError(
                f"successful Summary row lacks dependencies: {row['item']}"
            )
        failure = None
        completed = {
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
    line = (
        f"| {_cell(row['item'])} | {_cell(entries) if entries else '`-`'} | "
        f"{_cell(sections) if sections else '`-`'} | {_code_result(provenance)} |"
    )
    return line, failure, completed


def render_summary_rows(
    rows: Sequence[Dict[str, Any]],
    scan_entries: Mapping[str, Mapping[str, Any]],
    scan: ScanRecord,
) -> RenderedSummary:
    """Render all maintained-summary validation rows."""

    lines: List[str] = []
    failures: List[Failure] = []
    completed: List[Dict[str, Any]] = []
    groups = set()
    for row in rows:
        provenance = checked_result(row.get("provenance"), "Summary provenance")
        group = (
            row.get("source_item"),
            tuple(sorted(row.get("entries", []))),
            provenance,
        )
        if group in groups:
            raise ValidationToolError(
                "Summary source item is unnecessarily split across rows with "
                "the same supporting entries and outcome"
            )
        groups.add(group)
        line, failure, check = _render_summary_row(row, scan_entries, scan)
        lines.append(line)
        completed.append(check)
        if failure is not None:
            failures.append(failure)
    return RenderedSummary(lines, failures, completed, len(failures))


def _render_entry_target(entry_id: str, row: Dict[str, Any]) -> RenderedTarget:
    is_orphan = row["target"] == ORPHAN_TARGET
    values = {
        "Integrity": (
            validation_result(row.get("integrity"), "Integrity")
            if is_orphan
            else checked_result(row.get("integrity"), "Integrity")
        ),
        "Provenance": checked_result(row.get("provenance"), "Provenance"),
        "Reproducibility": validation_result(
            row.get("reproducibility"), "Reproducibility"
        ),
    }
    if is_orphan and (
        values
        != {
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
            f"unprovenanced evidence must use N/A reproducibility: {row['target']}"
        )
    failed_checks = [check for check, value in values.items() if value == "FAIL"]
    failure: Optional[Failure] = None
    if failed_checks:
        if not is_orphan and row.get("notes", "-") != "-":
            raise ValidationToolError(f"failed row Notes must be '-': {row['target']}")
        _validate_failed_findings(row, failed_checks, row["target"])
        failure = (entry_id, row["target"], row)
    completed = []
    for check, value in values.items():
        if not (is_success_date(value) or value == "FAIL"):
            continue
        dependencies = dependencies_for_check(row, check)
        if is_success_date(value) and not dependencies:
            raise ValidationToolError(
                f"successful {check} lacks dependencies: {row['target']}"
            )
        item = {
            "entry": entry_id,
            "target": row["target"],
            "check": check,
            "result": value,
            "dependencies": dependencies,
        }
        if check == "Provenance" and row.get("producer_invocation"):
            item["resolution"] = {
                "producer_invocation": row["producer_invocation"],
                **(
                    {"producer_bindings": row["producer_bindings"]}
                    if row.get("producer_bindings")
                    else {}
                ),
            }
        if value == "FAIL":
            item["findings"] = finding_map(row)[check]
        completed.append(item)
    notes = row.get("notes", "-")
    notes_cell = _code_result(notes) if notes == "-" else _cell(notes)
    sections = row.get("sections", [])
    section_cell = _code_result("-") if sections == ["-"] else _cell(sections)
    line = (
        f"| {_cell(row['target'])} | {section_cell} | "
        f"{_code_result(values['Integrity'])} | "
        f"{_code_result(values['Provenance'])} | "
        f"{_code_result(values['Reproducibility'])} | {notes_cell} |"
    )
    return RenderedTarget(line, failure, completed)


def render_entry_rows(rows: Sequence[Dict[str, Any]]) -> RenderedEntries:
    """Render all entry and global-scope validation rows."""

    lines: List[str] = []
    failures: List[Failure] = []
    completed: List[Dict[str, Any]] = []
    total = failed = failed_entries = reported_entries = orphan_scopes = 0
    for entry in rows:
        targets = entry["targets"]
        is_entry = entry.get("scope_kind", "entry") == "entry"
        if not is_entry and not targets:
            continue
        reported_entries += is_entry
        orphan_scopes += not is_entry
        entry_lines = [
            "",
            f"### {entry['id']}: {_cell(entry['title'])}",
            "",
            f"Entry: `{entry['path']}`",
            "",
            "| Target | Section | Integrity | Provenance | Reproducibility | Notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        failed_here = 0
        for row in targets:
            rendered = _render_entry_target(entry["id"], row)
            entry_lines.append(rendered.line)
            completed.extend(rendered.completed_checks)
            if rendered.failure is not None:
                failures.append(rendered.failure)
                failed_here += 1
        lines.extend(entry_lines)
        total += len(targets)
        failed += failed_here
        failed_entries += bool(failed_here)
    return RenderedEntries(
        lines,
        failures,
        completed,
        total,
        failed,
        failed_entries,
        reported_entries,
        orphan_scopes,
    )


def failure_report(
    failures: Sequence[Failure], entry_order: Sequence[str]
) -> Optional[str]:
    """Render the optional, failure-only working report."""

    if not failures:
        return None
    lines = ["# Validation Failures"]
    for scope_name in ("Summary", *entry_order):
        scoped = [
            (identity, row)
            for item_scope, identity, row in failures
            if item_scope == scope_name
        ]
        if not scoped:
            continue
        lines.extend(["", f"## {scope_name}"])
        for identity, row in scoped:
            lines.extend(["", f"### {_cell(identity)}", ""])
            findings = finding_map(row)
            for check in ("Integrity", "Provenance", "Reproducibility"):
                if row.get(check.lower()) != "FAIL":
                    continue
                for finding in findings.get(check, []):
                    lines.extend([f"- Check: {check}", f"- Finding: {finding}", ""])
        while lines and lines[-1] == "":
            lines.pop()
    return "\n".join(lines) + "\n"


def remediation_section(
    failures: Sequence[Failure], entry_order: Sequence[str]
) -> list[str]:
    """Render durable failure detail inside the canonical report."""

    if not failures:
        return []
    lines = ["## Remediation"]
    for scope_name in ("Summary", *entry_order):
        scoped = [
            (identity, row)
            for item_scope, identity, row in failures
            if item_scope == scope_name
        ]
        if not scoped:
            continue
        lines.extend(["", f"### {scope_name}"])
        for identity, row in scoped:
            lines.extend(["", f"#### {_cell(identity)}", ""])
            findings = finding_map(row)
            for check in ("Integrity", "Provenance", "Reproducibility"):
                if row.get(check.lower()) != "FAIL":
                    continue
                for finding in findings.get(check, []):
                    lines.extend([f"- Check: {check}", f"- Finding: {finding}", ""])
        while lines and lines[-1] == "":
            lines.pop()
    return lines


def validate_successful_orphan_separation(
    completed_checks: Sequence[Mapping[str, Any]],
    adjudicated_orphans: set[tuple[str, str]],
) -> None:
    """Reject unresolved orphans that support successful checks."""

    orphan_paths = {
        identity
        for _, identity in adjudicated_orphans
        if not (identity.startswith("<") and identity.endswith(">"))
    }
    successful_material: set[str] = set()
    for check in completed_checks:
        if not is_success_date(check["result"]):
            continue
        for dependency in check["dependencies"]:
            path = dependency["path"]
            successful_material.add(path)
            successful_material.update(
                (Path(path) / member).as_posix()
                for member in dependency.get("members", [])
            )
    conflicts = sorted(successful_material & orphan_paths)
    if conflicts:
        raise ValidationToolError(
            "unresolved orphan is a dependency of a successful check: "
            + "; ".join(conflicts)
        )


def render_orphan_dispositions(
    scoped_entries: Sequence[str],
    scan_entries: Mapping[str, Mapping[str, Any]],
    entry_rows: Sequence[Mapping[str, Any]],
    scan: ScanRecord,
    policy: OrphanDispositionPolicy,
) -> List[Dict[str, Any]]:
    """Serialize complete item-level orphan outcomes for incremental reuse."""

    if not scan["repository_scope"]["cross_log_complete"]:
        return []
    dispositions = []
    adjudicated = {entry["id"]: entry for entry in entry_rows}
    for entry_id in scoped_entries:
        scanned = scan_entries[entry_id]
        candidates = {item["identity"] for item in scanned.get("orphan_inventory", [])}
        if not candidates:
            continue
        entry = adjudicated[entry_id]
        items = entry.get("orphan_items", [])
        if {item["identity"] for item in items} != candidates or any(
            item["decision"] == "pending" for item in items
        ):
            raise ValidationToolError(
                f"orphan disposition is incomplete for scope: {entry_id}"
            )
        unresolved = [
            item["identity"] for item in items if item["decision"] == "unresolved"
        ]
        has_orphan_row = any(
            row.get("target") == ORPHAN_TARGET for row in entry.get("targets", [])
        )
        if bool(unresolved) != has_orphan_row:
            raise ValidationToolError(
                f"orphan row and item dispositions disagree: {entry_id}"
            )
        fingerprints = policy.item_fingerprints(scanned, scan)
        dispositions.append(
            {
                "inventory_version": policy.inventory_version,
                "entry": entry_id,
                "items": sorted(
                    (
                        {**item, "fingerprint": fingerprints[item["identity"]]}
                        for item in items
                    ),
                    key=lambda item: item["identity"],
                ),
                "dependencies": [
                    {"path": path, "role": "entry"}
                    for path in entry.get("scope_paths", [entry["path"]])
                ],
            }
        )
    return dispositions


def render_report(
    adjudication: AdjudicationRecord,
    scan_entries: Mapping[str, Mapping[str, Any]],
    scan: ScanRecord,
    date: str,
    adjudicated_orphans: set[tuple[str, str]],
) -> RenderedReport:
    """Render the complete human-facing report and completed-check inventory."""

    summary_rows = adjudication["summary"]
    entry_rows = adjudication["entries"]
    summary_rendered = render_summary_rows(summary_rows, scan_entries, scan)
    entries_rendered = render_entry_rows(entry_rows)
    failures = [*summary_rendered.failures, *entries_rendered.failures]
    completed_checks = [
        *summary_rendered.completed_checks,
        *entries_rendered.completed_checks,
    ]
    report = report_header(adjudication, scan, date)
    report.extend(
        [
            "",
            "## Counts",
            "",
            "| Scope | Total rows | Failed rows |",
            "| --- | ---: | ---: |",
            f"| Summary | {len(summary_rows)} | {summary_rendered.failed} |",
            f"| Entry targets | {entries_rendered.total} | {entries_rendered.failed} |",
            "",
            f"Entries: {entries_rendered.reported_entries} total; "
            f"{entries_rendered.failed_entries - entries_rendered.orphan_scopes} "
            "containing a failed target row.",
            *(
                [
                    f"Orphan scopes: {entries_rendered.orphan_scopes} with an "
                    "unresolved failure."
                ]
                if entries_rendered.orphan_scopes
                else []
            ),
            "",
            "## Summary",
            "",
            "| Statistic | Entry | Section | Provenance |",
            "| --- | --- | --- | --- |",
            *summary_rendered.lines,
            "",
            "## Entries",
            *entries_rendered.lines,
            "",
            *remediation_section(failures, [entry["id"] for entry in entry_rows]),
            "",
        ]
    )
    validate_successful_orphan_separation(completed_checks, adjudicated_orphans)
    actual_order = [entry["id"] for entry in entry_rows]
    report_text = install_status_summary("\n".join(report))
    return RenderedReport(
        report_text=report_text,
        failure_text=failure_report(failures, actual_order),
        failures=failures,
        completed_checks=completed_checks,
        summary_rows=len(summary_rows),
        summary_failed=summary_rendered.failed,
        entry_rows=entries_rendered.total,
        entry_failed=entries_rendered.failed,
        entries=entries_rendered.reported_entries,
        failed_entries=entries_rendered.failed_entries - entries_rendered.orphan_scopes,
        orphan_scopes=entries_rendered.orphan_scopes,
    )


def _validated_scan_record(value: Any, policy: RenderLifecyclePolicy) -> ScanRecord:
    try:
        return decode_scan_record(value, schema_version=policy.scan_schema_version)
    except LifecycleRecordContractError as exc:
        raise ValidationToolError(f"invalid scan record: {exc}") from exc


def _validated_adjudication_record(
    value: Any, policy: RenderLifecyclePolicy
) -> AdjudicationRecord:
    try:
        return decode_adjudication_record(
            value, schema_version=policy.adjudication_schema_version
        )
    except LifecycleRecordContractError as exc:
        raise ValidationToolError(f"invalid adjudication record: {exc}") from exc


def _materialize_identities(
    scan: Mapping[str, Any], completed_checks: Sequence[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    dependency_specs = _dependency_specs(completed_checks)
    identities = _dependency_identities(scan, dependency_specs)
    return identities, _stored_checks(scan, completed_checks)


def _dependency_specs(
    completed_checks: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    dependency_specs: dict[str, dict[str, Any]] = {}
    for check in completed_checks:
        for dependency in check["dependencies"]:
            path = dependency["path"]
            spec = dependency_specs.setdefault(
                path,
                {"members": set(), "member_scope_given": False, "successful": False},
            )
            spec["successful"] = spec["successful"] or is_success_date(check["result"])
            if "members" in dependency:
                spec["member_scope_given"] = True
                spec["members"].update(dependency["members"])
    return dependency_specs


def _dependency_identities(
    scan: Mapping[str, Any], dependency_specs: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    scan_files = scan.get("files", {})
    resolved_paths = scan.get("resolved_paths", {})
    identities: dict[str, dict[str, Any]] = {}
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
            current: dict[str, Any] = {"missing": True}
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
            current = validation_file_identity(scan, path, resolved)
        baseline = scan_files.get(path)
        same_scope = (
            not resolved.is_dir()
            or baseline is None
            or baseline.get("members") == current.get("members")
        )
        if baseline is not None and same_scope and current != baseline:
            raise FileChangedError(f"dependency changed after scan: {path}")
        identities[path] = current
    return identities


def _stored_checks(
    scan: Mapping[str, Any], completed_checks: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    snapshot_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    stored_checks = []
    for check in completed_checks:
        stored_dependencies = []
        for dependency in check["dependencies"]:
            members = dependency.get("members", [])
            cache_key = (
                dependency["path"],
                tuple(members) if isinstance(members, list) else (),
            )
            snapshot = snapshot_cache.get(cache_key)
            if snapshot is None:
                snapshot = dependency_identity_snapshot(scan, dependency)
                snapshot_cache[cache_key] = snapshot
            stored_dependencies.append(
                {
                    "path": dependency["path"],
                    "role": dependency["role"],
                    "identity": snapshot,
                }
            )
        stored_checks.append(
            {
                **check,
                "dependencies": stored_dependencies,
                "dependency_signature": current_check_dependency_contract(scan, check),
            }
        )
    return stored_checks


def _mechanically_locked_outcomes(
    prepared: Mapping[str, Any],
) -> set[tuple[str, str, str]]:
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
                if is_success_date(row.get(check.lower())):
                    locked.add((entry["id"], row["target"], check))
    return locked


def _mechanically_failed_outcomes(
    prepared: Mapping[str, Any],
) -> set[tuple[str, str, str]]:
    return {
        (item["entry"], item["identity"], check)
        for item in prepared.get("review_queue", [])
        for check in item.get("hard_failures", [])
    }


def _reject_mechanical_success_overrides(
    adjudication: Mapping[str, Any],
    scan: ScanRecord,
    policy: RenderLifecyclePolicy,
) -> None:
    prepared = prepare_adjudication(
        scan,
        adjudication["date"],
        scan["validation_rules_version"],
        AdjudicationPreparationPolicy(
            policy.adjudication_schema_version,
            lambda row, source: mechanical_evidence_support(
                row, source, inspect_structure
            ),
        ),
        adjudication["mode"],
    )
    locked = _mechanically_locked_outcomes(prepared)
    failed = _mechanically_failed_outcomes(prepared)
    mixed_review = {
        (item.get("entry"), item.get("identity")): semantic_failure_bases(item)
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
                if (entry["id"], row["target"], check) in locked and row.get(
                    check.lower()
                ) == "FAIL":
                    conflicts.append(f"{entry['id']}: {row['target']}: {check}")
                if (entry["id"], row["target"], check) in failed and row.get(
                    check.lower()
                ) != "FAIL":
                    conflicts.append(
                        f"{entry['id']}: {row['target']}: immutable {check} failure"
                    )
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
            "adjudication overrides a mechanically resolved outcome: "
            + "; ".join(conflicts)
        )


def _validate_render_header(
    adjudication: AdjudicationRecord,
    scan: ScanRecord,
    policy: RenderLifecyclePolicy,
) -> str:
    date = adjudication["date"]
    if not is_success_date(date):
        raise ValidationToolError(f"invalid validation date: {date!r}")
    if adjudication.get("mode") not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    if adjudication.get("validation_rules_version") != scan.get(
        "validation_rules_version"
    ):
        raise ValidationToolError(
            "adjudication and scan validation-rules versions differ"
        )
    if scan.get("validation_rules_version") != policy.rules_version:
        raise ValidationToolError(
            "canonical rendering requires the current validation-rules version"
        )
    durable_identity = scan.get("durable_record_identity")
    if (
        not isinstance(durable_identity, str)
        or re.fullmatch(r"[0-9a-f]{64}", durable_identity) is None
    ):
        raise ValidationToolError("scan lacks a valid durable-record identity")
    scope = scan.get("repository_scope", {})
    if (
        scope.get("kind") != "replacement"
        or scope.get("refresh_summary") != scan.get("summary")
        or scan.get("summary") not in scope.get("expected_summaries", [])
    ):
        raise ValidationToolError(
            "canonical rendering requires a complete repository replacement view"
        )
    if adjudication.get("log") != scan.get("summary"):
        raise ValidationToolError("adjudication and scan logs differ")
    if adjudication["review_queue"]:
        raise ValidationToolError(
            f"adjudication has {len(adjudication['review_queue'])} unresolved "
            "review-queue item(s)"
        )
    return date


def _validate_render_graph(
    adjudication: AdjudicationRecord,
    scan: ScanRecord,
    policy: RenderLifecyclePolicy,
) -> tuple[DependencyGraph, set[tuple[str, str]]]:
    _reject_mechanical_success_overrides(adjudication, scan, policy)
    graph = build_dependency_graph(scan, adjudication)
    namespace = Path(scan["summary"]).with_suffix("").as_posix()
    graph_orphans = orphan_locations(graph, namespace)
    unresolved = {
        (entry["id"], item["identity"])
        for entry in adjudication.get("entries", [])
        for item in entry.get("orphan_items", [])
        if item.get("decision") == "unresolved"
    }
    inventory = {
        (entry["id"], item["identity"])
        for entry in adjudication.get("entries", [])
        for item in entry.get("orphan_items", [])
    }
    if not scan["repository_scope"]["cross_log_complete"]:
        deferred = {
            (entry["id"], item["identity"])
            for entry in adjudication.get("entries", [])
            for item in entry.get("orphan_items", [])
            if item.get("decision") == "deferred"
            and item.get("basis") == "cross-log-incomplete"
        }
        if deferred != inventory:
            raise ValidationToolError(
                "incomplete cross-log view requires every orphan disposition "
                "to remain deferred"
            )
        return graph, set()
    expected = graph_orphans & inventory
    if unresolved - expected:
        raise ValidationToolError(
            "unresolved orphan is a dependency of a successful check or other "
            "applicable graph root: "
            + "; ".join(
                f"{entry}: {identity}"
                for entry, identity in sorted(unresolved - expected)
            )
        )
    if unresolved != expected:
        raise ValidationToolError(
            "orphan dispositions disagree with canonical graph reachability: "
            f"reported-only={sorted(unresolved - expected)!r}; "
            f"graph-only={sorted(expected - unresolved)!r}"
        )
    return graph, unresolved


def _validated_render_scope(
    adjudication: AdjudicationRecord, scan: ScanRecord, output_dir: Path
) -> list[str]:
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
    expected_order = scan["entry_order"]
    scoped_set = set(scoped_entries)
    if scoped_entries != [entry for entry in expected_order if entry in scoped_set]:
        raise ValidationToolError(
            "scope entries are unknown or do not follow maintained-summary order"
        )
    partial = not scope["summary"] or scoped_entries != expected_order
    project_root = Path(scan["project_root"])
    canonical_output = repository_identity_path(scan["log_root"], project_root)
    if output_dir.resolve() == canonical_output and (
        partial or adjudication.get("requested_scope") != COMPLETE_SCOPE_DESCRIPTION
    ):
        raise ValidationToolError("canonical rendering requires complete-log scope")
    record_names = ("validation.md", "validation-failures.md", "validation-state.json")
    if partial and any((output_dir / name).exists() for name in record_names):
        raise ValidationToolError(
            "partial-scope rendering cannot overwrite existing validation records"
        )
    return scoped_entries


def _validated_render_entries(
    adjudication: AdjudicationRecord,
    scan: ScanRecord,
    scoped_entries: Sequence[str],
) -> dict[str, dict[str, Any]]:
    actual_order = [entry["id"] for entry in adjudication["entries"]]
    if actual_order != scoped_entries:
        raise ValidationToolError(
            f"entry order mismatch: expected {scoped_entries}, got {actual_order}"
        )
    scan_entries = {
        entry["id"]: entry for entry in scan["entries"] if "error" not in entry
    }
    for entry in adjudication["entries"]:
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
    return scan_entries


def _validate_render_summary_scope(
    rows: Sequence[dict[str, Any]], include_summary: bool, scan: ScanRecord
) -> None:
    expected = {item["identity"] for item in scan["summary_items"]}
    covered = {row.get("source_item") for row in rows}
    if include_summary and covered != expected:
        raise ValidationToolError(
            "Summary adjudication does not cover the scanned item inventory"
        )
    if not include_summary and rows:
        raise ValidationToolError("Summary rows are present outside requested scope")


def _assert_scanned_files_current(scan: ScanRecord) -> None:
    for identity, expected in scan.get("files", {}).items():
        raw_path = scan.get("resolved_paths", {}).get(identity)
        if not isinstance(raw_path, str):
            raise FileChangedError(
                f"validation input no longer resolves after scan: {identity}"
            )
        try:
            current = validation_file_identity(scan, identity, Path(raw_path))
        except (OSError, ValidationToolError) as exc:
            raise FileChangedError(
                f"validation input changed after scan: {identity}: {exc}"
            ) from exc
        if current != expected:
            raise FileChangedError(f"validation input changed after scan: {identity}")


def _assert_scanned_directories_current(
    scan: ScanRecord, policy: RenderLifecyclePolicy
) -> None:
    project_root = Path(scan["project_root"])
    log_root = repository_identity_path(scan["log_root"], project_root)
    generated = {
        (log_root / name).resolve()
        for name in (*policy.record_filenames, LOCK_FILENAME)
    }
    for identity, expected in scan.get("directory_memberships", {}).items():
        raw_path = scan.get("resolved_paths", {}).get(identity)
        if not isinstance(raw_path, str):
            raise FileChangedError(
                f"validation directory no longer resolves after scan: {identity}"
            )
        try:
            current = directory_membership_identity(Path(raw_path), generated)
        except (OSError, ValidationToolError) as exc:
            current = {"error": str(exc)}
        if current != expected:
            raise FileChangedError(
                f"validation directory changed after scan: {identity}"
            )


def assert_scan_inputs_current(scan: ScanRecord, policy: RenderLifecyclePolicy) -> None:
    """Require current validation-relevant inputs owned by the scanned log."""

    _assert_scanned_files_current(scan)
    _assert_scanned_directories_current(scan, policy)


def _assert_scan_snapshot_current(
    scan: ScanRecord,
    policy: RenderLifecyclePolicy,
) -> None:
    _assert_scanned_files_current(scan)
    _assert_scanned_directories_current(scan, policy)
    if scan["repository_scope"]["kind"] != "replacement":
        raise FileChangedError("scan is not bound to a publishable repository view")


def lint_records(
    output_dir: Path,
    policy: RenderLifecyclePolicy,
    expected_entry_order: Optional[Sequence[str]] = None,
    expected_local_snapshot_identity: Optional[str] = None,
) -> dict[str, Any]:
    """Lint one generated canonical record bundle."""

    return lint_validation_records(
        output_dir,
        expected_entry_order,
        LintPolicy(
            policy.state_schema_version,
            policy.orphan_inventory_version,
            ORPHAN_TARGET,
            SLICE_FILENAME,
        ),
        expected_local_snapshot_identity,
    )


def _publication_lint(
    output_dir: Path,
    expected_entry_order: Optional[Sequence[str]],
    policy: RenderLifecyclePolicy,
) -> dict[str, Any]:
    return lint_records(output_dir, policy, expected_entry_order)


def render_records(
    adjudication: AdjudicationRecord,
    scan: ScanRecord,
    output_dir: Path,
    policy: RenderLifecyclePolicy,
) -> RenderCounts:
    """Render and atomically publish authoritative validation records."""

    scan = _validated_scan_record(scan, policy)
    adjudication = _validated_adjudication_record(adjudication, policy)
    date = _validate_render_header(adjudication, scan, policy)
    graph, adjudicated_orphans = _validate_render_graph(adjudication, scan, policy)
    scoped_entries = _validated_render_scope(adjudication, scan, output_dir)
    scope = adjudication["scope"]
    summary_rows = adjudication["summary"]
    entry_rows = adjudication["entries"]
    scan_entries = _validated_render_entries(adjudication, scan, scoped_entries)
    _validate_render_summary_scope(summary_rows, scope["summary"], scan)
    rendered = render_report(
        adjudication, scan_entries, scan, date, adjudicated_orphans
    )

    identities, stored_checks = _materialize_identities(
        scan, list(rendered.completed_checks)
    )
    for completed, stored in zip(rendered.completed_checks, stored_checks):
        stored["graph_slice"] = check_graph_slice(graph, completed)
    orphan_dispositions = render_orphan_dispositions(
        scoped_entries,
        scan_entries,
        entry_rows,
        scan,
        OrphanDispositionPolicy(
            policy.orphan_inventory_version, orphan_item_fingerprints
        ),
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
        for scope_name, identity, row in rendered.failures
    ]
    graph_record = slice_record(
        graph,
        scan["summary"],
        scan["files"],
        scan["repository_material_owners"],
        local_snapshot_identity=local_snapshot_identity(scan),
    )
    measurements = RenderMeasurements(
        summary_rows=rendered.summary_rows,
        summary_failed=rendered.summary_failed,
        entry_rows=rendered.entry_rows,
        entry_failed=rendered.entry_failed,
        entries=rendered.entries,
        failed_entries=rendered.failed_entries,
        successful_checks=sum(
            is_success_date(check["result"]) for check in rendered.completed_checks
        ),
        completed_checks=len(rendered.completed_checks),
        file_identities=len(identities),
        failure_rows=len(rendered.failures),
    )
    assembly = RenderAssembly(
        report_text=rendered.report_text,
        failure_text=rendered.failure_text,
        graph_record=graph_record,
        state_inputs=RenderStateInputs(
            schema_version=policy.state_schema_version,
            rules_version=adjudication["validation_rules_version"],
            local_snapshot_identity=local_snapshot_identity(scan),
            input_fingerprint=scan["input_fingerprint"],
            input_files=scan.get("files", {}),
            mechanical_checks=scan.get("mechanical_checks", {}),
            directory_memberships=scan.get("directory_memberships", {}),
            file_identities=identities,
            completed_checks=stored_checks,
            orphan_dispositions=orphan_dispositions,
            report_identity=text_content_identity(rendered.report_text),
            graph_identity=graph_record["graph_identity"],
        ),
        measurements=measurements,
        date=date,
        mode=adjudication["mode"],
        requested_scope=adjudication["requested_scope"],
        scope=scope,
        failures=compact_failures,
        local_snapshot_identity=local_snapshot_identity(scan),
    )

    project_root = Path(scan["project_root"])
    canonical_output = repository_identity_path(scan["log_root"], project_root)
    expected_bundle_identity = (
        scan["durable_record_identity"]
        if output_dir.resolve() == canonical_output
        else record_bundle_identity(
            output_dir, ("validation-decisions.json", "validation.md")
        )
    )
    publish_validation_bundle(
        assembly.bundle(),
        ValidationPublicationTarget(
            output_dir,
            expected_bundle_identity,
            policy.record_filenames,
            scan.get("entry_order", []),
            SLICE_FILENAME,
        ),
        lambda: _assert_scan_snapshot_current(scan, policy),
        lambda directory, expected: _publication_lint(directory, expected, policy),
    )
    return assembly.counts()
