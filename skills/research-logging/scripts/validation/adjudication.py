"""Typed preparation records for research-log validation adjudication."""

from __future__ import annotations

import copy
import datetime
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from .contracts import (
    AdjudicationRecord,
    ScanRecord,
    ValidationToolError,
)
from .evidence import NUMBER_RE, numeric_value_equivalent
from .graph_adapter import recorded_invocation_identity

COMPLETE_SCOPE_DESCRIPTION = "complete standard scope"
ORPHAN_TARGET = "Orphaned artifacts, scripts, and references"
SUCCESS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class PreparedEntryItems:
    """Prepared targets and review items for one entry subproblem."""

    targets: Sequence[Dict[str, Any]]
    review_items: Sequence[Dict[str, Any]]


@dataclass(frozen=True)
class PreparedOrphans:
    """Prepared orphan rows, review items, and complete item dispositions."""

    targets: Sequence[Dict[str, Any]]
    review_items: Sequence[Dict[str, Any]]
    orphan_items: Sequence[Dict[str, Any]]


@dataclass(frozen=True)
class TargetPreparationContext:
    """Shared immutable inputs for entry-target preparation."""

    scan: Mapping[str, Any]
    reusable: Mapping[Tuple[str, str, str], Dict[str, Any]]
    date: str
    mode: str
    mechanical_support: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, str]]


@dataclass(frozen=True)
class TargetAssessment:
    """Mechanical and reusable state for one evidence target."""

    integrity: Optional[str]
    provenance: Optional[str]
    integrity_detail: str
    workflow: Mapping[str, Any]
    support_results: Sequence[Mapping[str, Any]]
    prior_integrity: Optional[Dict[str, Any]]
    prior_provenance: Optional[Dict[str, Any]]
    prior_reproduction: Optional[Dict[str, Any]]
    mode: str


@dataclass(frozen=True)
class AdjudicationPreparationPolicy:
    """Version and mechanical operation required to prepare adjudication."""

    schema_version: int
    mechanical_support: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, str]]


@dataclass(frozen=True)
class AdjudicationAssembly:
    """Typed owner of the prepared adjudication-record boundary."""

    schema_version: int
    rules_version: str
    log: str
    date: str
    mode: str
    entry_order: Sequence[str]
    summary_rows: Sequence[Dict[str, Any]]
    entry_rows: Sequence[Dict[str, Any]]
    review_queue: Sequence[Dict[str, Any]]

    def record(self) -> AdjudicationRecord:
        """Serialize prepared results into the exact adjudication contract."""

        return cast(
            AdjudicationRecord,
            {
                "schema_version": self.schema_version,
                "validation_rules_version": self.rules_version,
                "log": self.log,
                "requested_scope": COMPLETE_SCOPE_DESCRIPTION,
                "scope": {
                    "summary": True,
                    "entries": list(self.entry_order),
                },
                "date": self.date,
                "mode": self.mode,
                "summary": list(self.summary_rows),
                "entries": list(self.entry_rows),
                "review_queue": list(self.review_queue),
            },
        )


def reusable_checks(
    scan: Mapping[str, Any],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return reusable results keyed by entry, target, and check."""

    result = {}
    for check in scan.get("incremental", {}).get("checks", []):
        if check.get("status") != "reusable":
            continue
        key = (check.get("entry"), check.get("target"), check.get("check"))
        if not all(isinstance(value, str) for value in key):
            continue
        result[cast(tuple[str, str, str], key)] = {
            "result": check["result"],
            "resolution": check.get("resolution"),
            "findings": check.get("findings", []),
            "dependencies": check.get("dependencies", []),
        }
    return result


def merge_reused_dependencies(
    dependencies: list[dict[str, Any]], prior: Optional[dict[str, Any]]
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


def prepare_orphan_items(
    entry: Mapping[str, Any],
    prior_orphan: Mapping[str, Any],
    *,
    deferred: bool = False,
) -> PreparedOrphans:
    """Prepare one entry's item-level orphan dispositions and review row."""

    orphan_inventory = entry.get("orphan_inventory", [])
    candidate_identities = {
        item["identity"] for item in entry.get("orphan_candidates", [])
    }
    prior_items = {
        item["identity"]: dict(item)
        for item in prior_orphan.get("items", [])
        if isinstance(item, dict) and item.get("identity")
    }
    orphan_items = []
    for candidate in orphan_inventory:
        if deferred:
            orphan_items.append(
                {
                    "identity": candidate["identity"],
                    "decision": "deferred",
                    "basis": "cross-log-incomplete",
                }
            )
            continue
        orphan_items.append(
            (
                prior_items.get(
                candidate["identity"],
                {
                    "identity": candidate["identity"],
                    "decision": "pending",
                    "basis": "-",
                },
            )
                if candidate["identity"] in candidate_identities
                else {
                    "identity": candidate["identity"],
                    "decision": "accepted",
                    "basis": "graph",
                }
            )
        )
    unresolved = [
        item["identity"] for item in orphan_items if item["decision"] == "unresolved"
    ]
    pending = [
        candidate
        for candidate, item in zip(orphan_inventory, orphan_items)
        if candidate["identity"] in candidate_identities
        and item["decision"] == "pending"
    ]
    reportable = [*unresolved, *[item["identity"] for item in pending]]
    targets: list[dict[str, Any]] = []
    if reportable:
        count = len(reportable)
        targets.append(
            {
                "target": ORPHAN_TARGET,
                "sections": ["-"],
                "integrity": "N/A",
                "provenance": "FAIL",
                "reproducibility": "N/A",
                "notes": f"{count} unresolved {'item' if count == 1 else 'items'}",
                "dependencies": [
                    {"path": path, "role": "entry"}
                    for path in entry.get("scope_paths", [entry["path"]])
                ],
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
    review_items: list[dict[str, Any]] = []
    if pending:
        review_items.append(
            {
                "entry": entry["id"],
                "kind": "orphan_candidates",
                "identity": ORPHAN_TARGET,
                "candidates": pending,
                "validation_notes": entry.get("validation_notes", []),
                "reason": (
                    "classify each residual candidate as unresolved or retain "
                    "it through one exact pre-existing Validation note"
                ),
            }
        )
    return PreparedOrphans(targets, review_items, orphan_items)


def apply_reusable_target_results(
    entry_id: str,
    targets: Sequence[dict[str, Any]],
    review_items: Sequence[dict[str, Any]],
    reusable: Mapping[tuple[str, str, str], dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    """Apply reusable results and remove superseded mechanical review items."""

    retained_review = list(review_items)
    for target_row in targets:
        reused_for_row = set()
        for check, field in (
            ("Integrity", "integrity"),
            ("Provenance", "provenance"),
            ("Reproducibility", "reproducibility"),
        ):
            prior = reusable.get((entry_id, target_row["target"], check))
            if target_row["target"] == ORPHAN_TARGET:
                prior = None
            if prior is None or (mode == "reproduction" and check == "Reproducibility"):
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
            merge_reused_dependencies(target_row["dependencies"], prior)
            if check == "Provenance" and isinstance(prior.get("resolution"), dict):
                producer = prior["resolution"].get("producer_invocation")
                if isinstance(producer, str):
                    target_row["producer_invocation"] = producer
            reused_for_row.add(check)
        if reused_for_row:
            retained_review = [
                item
                for item in retained_review
                if not (
                    item.get("entry") == entry_id
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
    return retained_review


def validate_prepare_request(scan: Mapping[str, Any], mode: str) -> None:
    """Reject a preparation request that contradicts its scan record."""

    if mode not in {"standard", "reproduction"}:
        raise ValidationToolError("validation mode must be standard or reproduction")
    if mode != scan.get("requested_mode", "standard"):
        raise ValidationToolError("prepare mode does not match the scanned mode")
    if scan.get("incremental", {}).get("status") == "unchanged":
        raise ValidationToolError(
            "unchanged standard validation is complete from cached state"
        )


def _summary_support_candidates(
    row: Mapping[str, Any],
    selector: str,
    supporting_entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return bounded entry candidates that may support one summary statistic."""

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
        if not numeric_value_equivalent(selector, [token])
    ]
    transformation_candidates = [
        candidate
        for candidate in section_candidates
        if candidate.get("base_selector") in transformation_tokens
    ]
    contextual_candidates = [
        candidate
        for candidate in section_candidates
        if selector in candidate["context"]
        or numeric_value_equivalent(selector, [candidate["context"]])
    ]
    return (
        exact_candidates
        or transformation_candidates
        or contextual_candidates
        or section_candidates
    )


def _reused_summary_support(
    scan: Mapping[str, Any],
    row: Mapping[str, Any],
    prior: Mapping[str, Any],
    support_candidates: Sequence[Mapping[str, Any]],
    entries_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reconstruct reviewed summary support when it still resolves."""

    resolution = prior.get("resolution")
    if not isinstance(resolution, Mapping):
        return []
    if (
        resolution.get("entry") != row["entry"]
        or resolution.get("section") != row["section"]
        or not isinstance(resolution.get("lines"), str)
    ):
        return []
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", resolution["lines"])
    supporting_entry = entries_by_id.get(row["entry"])
    if not match or not supporting_entry:
        return []
    entry_path = Path(scan["resolved_paths"][supporting_entry["path"]])
    entry_lines = entry_path.read_text(encoding="utf-8").splitlines()
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
    if candidate is None or not 1 <= start <= end <= len(entry_lines):
        return []
    return [
        {
            "entry": row["entry"],
            "section": row["section"],
            "lines": str(start) if start == end else f"{start}-{end}",
            "text": " ".join(entry_lines[start - 1 : end]),
        }
    ]


def prepare_summary_rows(
    scan: Mapping[str, Any],
    reusable: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepare summary provenance rows and unresolved review items."""

    entries_by_id = {entry["id"]: entry for entry in scan["entries"]}
    summary_record = scan.get("evidence_records", {}).get("summary", {})
    summary_by_statistic = {
        row["statistic"]: row for row in summary_record.get("rows", [])
    }
    summary_rows: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
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
        support_candidates: list[dict[str, Any]] = []
        supporting_entry = entries_by_id.get(row["entry"]) if row else None
        if row and supporting_entry:
            dependencies.append(
                {"path": supporting_entry["path"], "role": "supporting-entry"}
            )
            support_candidates = _summary_support_candidates(
                row, selector, supporting_entry
            )
        prior = reusable.get(("Summary", selector, "Provenance"))
        provenance = prior["result"] if prior else None
        findings: list[dict[str, Any]] = []
        support_evidence: list[dict[str, Any]] = []
        support_reviewed = False
        if prior and provenance == "FAIL":
            entries = []
            sections = []
            findings = [
                {"check": "Provenance", "finding": finding}
                for finding in prior.get("findings", [])
            ]
            support_reviewed = True
        elif prior and row:
            support_evidence = _reused_summary_support(
                scan, row, prior, support_candidates, entries_by_id
            )
            support_reviewed = bool(support_evidence)
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
    return summary_rows, review_queue


def section_error_items(entry: Mapping[str, Any]) -> PreparedEntryItems:
    """Convert invalid section structures into deterministic failures."""

    targets: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
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
        review_items.append(
            {
                "entry": entry["id"],
                "kind": "mechanical_failure",
                "identity": target,
                "hard_failures": ["Integrity", "Provenance"],
                "reason": detail,
            }
        )
    return PreparedEntryItems(targets, review_items)


def group_entry_targets(entry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Group presented evidence associations by retained target identity."""

    rows_by_target: dict[str, dict[str, Any]] = {}
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
    return rows_by_target


def unprovenanced_items(entry: Mapping[str, Any]) -> PreparedEntryItems:
    """Create failures for presented items missing evidence associations."""

    recorded_keys = {
        (row["section"], row["kind"], row["evidence"])
        for row in entry["evidence_record"]["rows"]
    }
    targets: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
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
        review_items.append(
            {
                "entry": entry["id"],
                "kind": "mechanical_failure",
                "identity": target,
                "hard_failures": ["Integrity", "Provenance"],
                "reason": findings[1]["finding"],
            }
        )
    return PreparedEntryItems(targets, review_items)


def target_integrity(
    scan: Mapping[str, Any],
    source: Mapping[str, Any],
    target: str,
    prior: Optional[Mapping[str, Any]],
    date: str,
) -> tuple[Optional[str], str]:
    """Resolve or mechanically assess one target's integrity result."""

    if prior:
        return prior["result"], "reused from unchanged validation state"
    structure = scan["mechanical_checks"].get(target, {})
    if source["status"] != "resolved":
        return "FAIL", f"supporting artifact is {source['status']}"
    if structure.get("status") == "ok" and structure.get("type") != "directory":
        return date, "type-appropriate structural check passed"
    if structure.get("status") == "fail":
        return "FAIL", structure.get("detail") or "structural check failed"
    return None, "custom or collection structure requires review"


def _target_findings(assessment: TargetAssessment) -> list[dict[str, Any]]:
    """Build check-scoped findings for one prepared target."""

    findings: list[dict[str, Any]] = []
    if assessment.integrity == "FAIL":
        details = (
            assessment.prior_integrity.get("findings", [])
            if assessment.prior_integrity
            else [assessment.integrity_detail]
        )
        findings.extend(
            {"check": "Integrity", "finding": finding} for finding in details
        )
    if assessment.provenance == "FAIL":
        if assessment.prior_provenance:
            details = assessment.prior_provenance.get("findings", [])
        else:
            details = [assessment.workflow["detail"]]
            details.extend(
                result["detail"]
                for result in assessment.support_results
                if result["status"] == "fail"
            )
            details = ["; ".join(dict.fromkeys(details))]
        findings.extend(
            {"check": "Provenance", "finding": finding} for finding in details
        )
    return findings


def _target_review_item(
    entry: Mapping[str, Any],
    target: str,
    grouped: Mapping[str, Any],
    assessment: TargetAssessment,
    hard_failures: Sequence[str],
) -> dict[str, Any]:
    """Build the bounded semantic packet for one unresolved target."""

    return {
        "entry": entry["id"],
        "kind": (
            "mechanical_failure"
            if "FAIL" in {assessment.integrity, assessment.provenance}
            else "semantic_fallback"
        ),
        "identity": target,
        "hard_failures": list(hard_failures),
        "sections": grouped["sections"],
        "integrity": assessment.integrity_detail,
        "integrity_status": (
            "pass"
            if is_success_date(assessment.integrity)
            else "fail"
            if assessment.integrity == "FAIL"
            else "unresolved"
        ),
        "workflow": assessment.workflow,
        "evidence": [
            {
                "kind": row["kind"],
                "selector": row["evidence"],
                "context": (row.get("presented_item") or {}).get("context", ""),
                "locator": association_source.get("locator", ""),
                "result": result,
                "transformation": row["transformation"],
            }
            for item, result in zip(grouped["associations"], assessment.support_results)
            for row, association_source in [(item["row"], item["source"])]
        ],
    }


def _target_hard_failures(
    source: Mapping[str, Any],
    integrity: Optional[str],
    workflow: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return mechanically final checks that semantic review cannot pass."""

    failures: list[str] = []
    if source.get("status") != "resolved":
        failures.extend(("Integrity", "Provenance"))
    elif integrity == "FAIL":
        failures.append("Integrity")
    if workflow.get("status") == "fail":
        failures.append("Provenance")
    return tuple(dict.fromkeys(failures))


def _target_reproducibility(
    entry: Mapping[str, Any],
    target: str,
    grouped: Mapping[str, Any],
    assessment: TargetAssessment,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Resolve reproduction state and any required review instruction."""

    if assessment.prior_reproduction and assessment.mode == "standard":
        return assessment.prior_reproduction["result"], []
    if assessment.mode == "reproduction" and assessment.workflow.get(
        "matched_commands", 0
    ):
        return None, [
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
        ]
    if assessment.mode == "reproduction":
        return "N/A", []
    return "-", []


def prepare_evidence_target(
    entry: dict[str, Any],
    target: str,
    grouped: Mapping[str, Any],
    context: TargetPreparationContext,
) -> PreparedEntryItems:
    """Prepare mechanics and review context for one evidence target."""

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
    workflow, workflow_dependencies = workflow_check(
        entry, target, cast(ScanRecord, context.scan)
    )
    prior_provenance = context.reusable.get((entry["id"], target, "Provenance"))
    if prior_provenance is None:
        dependencies.extend(workflow_dependencies)
    dependencies = [
        dict(value)
        for value in {
            (item["path"], item["role"]): item for item in dependencies
        }.values()
    ]
    prior_integrity = context.reusable.get((entry["id"], target, "Integrity"))
    integrity, integrity_detail = target_integrity(
        context.scan, source, target, prior_integrity, context.date
    )
    provenance = prior_provenance["result"] if prior_provenance else None
    support_results = [
        context.mechanical_support(item["row"], item["source"])
        for item in grouped["associations"]
    ]
    if provenance is None:
        statuses = {result["status"] for result in support_results}
        if workflow["status"] == "fail" or "fail" in statuses:
            provenance = "FAIL"
        elif workflow["status"] == "pass" and statuses <= {"pass"}:
            provenance = context.date
    prior_reproduction = context.reusable.get((entry["id"], target, "Reproducibility"))
    assessment = TargetAssessment(
        integrity,
        provenance,
        integrity_detail,
        workflow,
        support_results,
        prior_integrity,
        prior_provenance,
        prior_reproduction,
        context.mode,
    )
    findings = _target_findings(assessment)
    review_items: list[dict[str, Any]] = []
    if (
        integrity is None
        or provenance is None
        or (integrity == "FAIL" and prior_integrity is None)
        or (provenance == "FAIL" and prior_provenance is None)
    ):
        review_items.append(
            _target_review_item(
                entry,
                target,
                grouped,
                assessment,
                _target_hard_failures(source, integrity, workflow),
            )
        )
    reproducibility, reproduction_review = _target_reproducibility(
        entry,
        target,
        grouped,
        assessment,
    )
    review_items.extend(reproduction_review)
    merge_reused_dependencies(dependencies, prior_integrity)
    merge_reused_dependencies(dependencies, prior_provenance)
    merge_reused_dependencies(dependencies, prior_reproduction)
    producer_resolution = (
        prior_provenance.get("resolution", {}).get("producer_invocation")
        if prior_provenance and isinstance(prior_provenance.get("resolution"), dict)
        else workflow.get("producer_invocation")
    )
    target_row = {
        "target": target,
        "sections": grouped["sections"],
        "integrity": integrity,
        "provenance": provenance,
        "reproducibility": reproducibility,
        "notes": "-",
        "dependencies": dependencies,
        "findings": findings,
    }
    if isinstance(producer_resolution, str):
        target_row["producer_invocation"] = producer_resolution
    return PreparedEntryItems([target_row], review_items)


def prepare_entry_row(
    entry: dict[str, Any],
    context: TargetPreparationContext,
    prior_orphan: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prepare every target and unresolved review item for one entry."""

    section_errors = section_error_items(entry)
    unprovenanced = unprovenanced_items(entry)
    targets = [*section_errors.targets, *unprovenanced.targets]
    review_items = [*section_errors.review_items, *unprovenanced.review_items]
    for target, grouped in group_entry_targets(entry).items():
        prepared_target = prepare_evidence_target(entry, target, grouped, context)
        targets.extend(prepared_target.targets)
        review_items.extend(prepared_target.review_items)
    orphans = prepare_orphan_items(
        entry,
        prior_orphan,
        deferred=not context.scan["repository_scope"]["cross_log_complete"],
    )
    targets.extend(orphans.targets)
    review_items.extend(orphans.review_items)
    review_items = apply_reusable_target_results(
        entry["id"], targets, review_items, context.reusable, context.mode
    )
    return (
        {
            "id": entry["id"],
            "title": entry["title"],
            "path": entry["path"],
            "scope_reconciled": True,
            "targets": targets,
            "scope_kind": entry.get("scope_kind", "entry"),
            "scope_paths": entry.get("scope_paths", [entry["path"]]),
            "orphan_items": orphans.orphan_items,
        },
        review_items,
    )


def prepare_adjudication(
    scan: ScanRecord,
    date: str,
    rules_version: str,
    policy: AdjudicationPreparationPolicy,
    mode: str = "standard",
) -> AdjudicationRecord:
    """Prepare mechanical outcomes and the bounded semantic-review queue."""

    validate_prepare_request(scan, mode)
    reusable = reusable_checks(scan)
    reusable_orphans = {
        item.get("entry"): item
        for item in scan.get("incremental", {}).get("orphan_dispositions", [])
    }
    summary_rows, review_queue = prepare_summary_rows(scan, reusable)
    context = TargetPreparationContext(
        scan,
        reusable,
        date,
        mode,
        policy.mechanical_support,
    )
    entry_rows = []
    for entry in scan["entries"]:
        if "error" in entry:
            continue
        entry_row, entry_review = prepare_entry_row(
            entry,
            context,
            reusable_orphans.get(entry["id"], {}),
        )
        entry_rows.append(entry_row)
        review_queue.extend(entry_review)

    queue_collection_scopes(scan, entry_rows, review_queue)
    return AdjudicationAssembly(
        schema_version=policy.schema_version,
        rules_version=rules_version,
        log=scan["summary"],
        date=date,
        mode=mode,
        entry_order=scan["entry_order"],
        summary_rows=summary_rows,
        entry_rows=entry_rows,
        review_queue=review_queue,
    ).record()


def is_success_date(value: Any) -> bool:
    """Return whether a result is a real ISO calendar date used for success."""

    if not isinstance(value, str) or not SUCCESS_DATE_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def queue_collection_scopes(
    scan: Mapping[str, Any],
    entry_rows: Sequence[Mapping[str, Any]],
    review_queue: list[dict[str, Any]],
) -> None:
    """Queue member selection for successful directory dependencies."""

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
                is_success_date(row.get(check))
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
            else:
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


class _ReviewCommandContext(NamedTuple):
    """Rendered context plus candidate commands and their target identities."""

    lines: list[str]
    commands: list[dict[str, Any]]
    command_identities: dict[tuple[Any, Any], list[str]]


class _WorkflowMatch(NamedTuple):
    """One recorded command whose exact path argument names a target."""

    command: dict[str, Any]
    argument: dict[str, Any]
    command_index: int


class WorkflowCommandCheck(NamedTuple):
    """Dependency facts and check details from one recorded command."""

    dependencies: list[dict[str, str]]
    failures: list[str]
    uncertainties: list[str]


def _packet_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _resolved_identity_cache(scan: ScanRecord) -> Dict[str, str]:
    """Return one resolved-path lookup for a bounded validation operation."""

    return {
        Path(path).resolve().as_posix(): identity
        for identity, path in scan["resolved_paths"].items()
    }


def identity_for_path(
    scan: ScanRecord,
    raw: str,
    cache: Optional[Mapping[str, str]] = None,
) -> str:
    """Map one resolved path to its scan identity or stable display path."""

    resolved = Path(raw).resolve().as_posix()
    identities = cache if cache is not None else _resolved_identity_cache(scan)
    if resolved in identities:
        return identities[resolved]
    path = Path(raw).resolve()
    project_root = Path(scan["project_root"]).resolve()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _matching_workflow_commands(
    entry: Mapping[str, Any],
    target: str,
    scan: ScanRecord,
    identity_cache: Mapping[str, str],
) -> tuple[list[_WorkflowMatch], bool]:
    """Return target-naming commands and whether output direction is explicit."""

    matches = []
    for index, command in enumerate(entry.get("commands", []), 1):
        for argument in command.get("path_arguments", []):
            if identity_for_path(scan, argument["path"], identity_cache) != target:
                continue
            matches.append(_WorkflowMatch(command, argument, index))
            break
    confirmed = [
        match for match in matches if match.argument.get("role_hint") == "output"
    ]
    return confirmed or matches, bool(confirmed)


def _producer_command_check(
    command: Mapping[str, Any],
    scan: ScanRecord,
    identity_cache: Mapping[str, str],
) -> WorkflowCommandCheck:
    """Resolve and structurally check one recorded command entrypoint."""

    script = command.get("script")
    if not script or not Path(script).is_file():
        return WorkflowCommandCheck([], [], ["producer script is unresolved"])
    identity = identity_for_path(scan, script, identity_cache)
    dependencies = [{"path": identity, "role": "producer"}]
    structure = scan["mechanical_checks"].get(identity, {})
    failures = (
        [f"producer structure is {structure.get('status', 'unknown')}"]
        if structure.get("status") != "ok"
        else []
    )
    return WorkflowCommandCheck(dependencies, failures, [])


def check_workflow_command(
    command: Mapping[str, Any],
    scan: ScanRecord,
    identity_cache: Optional[Mapping[str, str]] = None,
) -> WorkflowCommandCheck:
    """Inspect one recorded producer command without inferring semantics."""

    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    producer = _producer_command_check(command, scan, identities)
    dependencies = list(producer.dependencies)
    failures = list(producer.failures)
    uncertainties = list(producer.uncertainties)
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
        identity = identity_for_path(scan, token["path"], identities)
        dependencies.append({"path": identity, "role": "input"})
        if not Path(token["path"]).exists():
            failures.append(f"input is missing: {identity}")
    for argument in command.get("path_arguments", []):
        if argument["role_hint"] != "input":
            continue
        identity = identity_for_path(scan, argument["path"], identities)
        dependencies.append({"path": identity, "role": "input"})
        if not Path(argument["path"]).exists():
            failures.append(f"input is missing: {identity}")
    return WorkflowCommandCheck(dependencies, failures, uncertainties)


def workflow_check(
    entry: dict[str, Any],
    target: str,
    scan: ScanRecord,
    identity_cache: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Check the recorded producer invocation that names one exact target."""

    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    selected, direction_confirmed = _matching_workflow_commands(
        entry, target, scan, identities
    )
    if not selected:
        return (
            {
                "status": "unresolved",
                "detail": "no explicit producing command matched",
                "matched_commands": 0,
            },
            [],
        )

    if Path(target).is_absolute() and all(
        match.argument.get("role_hint") == "input" for match in selected
    ):
        return (
            {
                "status": "pass",
                "detail": "recorded workflow consumes this retained external input",
                "matched_commands": len(selected),
            },
            [],
        )

    checked_matches = [
        (match, check_workflow_command(match.command, scan, identities))
        for match in selected
    ]
    viable = [pair for pair in checked_matches if not pair[1].failures]
    if not viable:
        failures = [
            failure
            for _match, checked in checked_matches
            for failure in checked.failures
        ]
        return {
            "status": "fail",
            "detail": "; ".join(sorted(set(failures))),
            "matched_commands": len(selected),
        }, []

    if len(viable) > 1:
        return (
            {
                "status": "unresolved",
                "detail": "multiple producing commands require semantic selection",
                "matched_commands": len(viable),
            },
            [],
        )

    match, checked = viable[0]
    uncertainties = list(checked.uncertainties)
    if not direction_confirmed:
        uncertainties.append(
            "command/path direction requires semantic producer confirmation"
        )
    unique_dependencies = [
        dict(item)
        for item in {
            (dependency["path"], dependency["role"]): dependency
            for dependency in checked.dependencies
        }.values()
    ]
    if uncertainties:
        return (
            {
                "status": "unresolved",
                "detail": "; ".join(sorted(set(uncertainties))),
                "matched_commands": 1,
            },
            unique_dependencies,
        )
    return {
        "status": "pass",
        "detail": "matched one recorded command",
        "matched_commands": 1,
        "producer_invocation": recorded_invocation_identity(
            entry["id"], match.command_index, match.command
        ),
    }, unique_dependencies


def _command_candidate_groups(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identity: str,
    sections: Sequence[str],
    identities: Mapping[str, str],
) -> tuple[bool, bool, bool, bool]:
    """Classify one recorded command against an evidence target."""

    target_name = Path(identity).name
    output_paths = [
        argument.get("path", "")
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "output"
    ]
    output_identities = [
        identity_for_path(scan, path, identities) for path in output_paths
    ]
    direct = identity in output_identities
    container = any(
        scan.get("mechanical_checks", {}).get(output_identity, {}).get("type")
        == "directory"
        and identity.startswith(output_identity.rstrip("/") + "/")
        for output_identity in output_identities
    )
    if not container:
        container = _unknown_container_may_produce_target(
            scan, command, identity, identities
        )
    exact = target_name in command.get("command", "") or any(
        Path(path).name == target_name for path in output_paths
    )
    return direct, container, exact, command.get("section") in sections


def _unknown_container_may_produce_target(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identity: str,
    identities: Mapping[str, str],
) -> bool:
    """Return whether source text supports an unknown output-container candidate."""

    containers = [
        identity_for_path(scan, argument.get("path", ""), identities)
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "unknown" and argument.get("path")
    ]
    if not any(
        scan.get("mechanical_checks", {}).get(container, {}).get("type")
        == "directory"
        and identity.startswith(container.rstrip("/") + "/")
        for container in containers
    ):
        return False
    script = command.get("script")
    if not isinstance(script, str) or not Path(script).is_file():
        return False
    try:
        source = Path(script).read_text(encoding="utf-8").lower()
    except (OSError, UnicodeError):
        return False
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", Path(identity).stem.lower())
        if len(token) > 1
    }
    searchable = source + "\n" + str(command.get("command", "")).lower()
    return bool(tokens) and all(token in searchable for token in tokens)


def _ordered_candidate_commands(
    groups: Sequence[Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    ordered = []
    seen = set()
    for command in (command for group in groups for command in group):
        command_id = id(command)
        if command_id in seen:
            continue
        seen.add(command_id)
        ordered.append(command)
    return ordered[:5]


def _candidate_command_entries(
    scan: ScanRecord, entry_id: str, identity: str
) -> list[dict[str, Any]]:
    """Return the presenting entry plus the physical owner of one target."""

    entries = scan.get("entries", [])
    presenting = next(
        (item for item in entries if item.get("id") == entry_id), None
    )
    if presenting is None:
        return []
    owners = [
        item
        for item in entries
        if item is not presenting
        and isinstance(item.get("path"), str)
        and identity.startswith(Path(item["path"]).parent.as_posix().rstrip("/") + "/")
    ]
    return [presenting, *owners]


def candidate_commands(
    scan: ScanRecord,
    entry_id: str,
    identity: str,
    sections: Sequence[str],
    identity_cache: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Return a small candidate-command set without deciding producer meaning."""

    entries = _candidate_command_entries(scan, entry_id, identity)
    if not entries:
        return []
    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    direct_outputs = []
    local_container_outputs: list[dict[str, Any]] = []
    owner_container_outputs: list[dict[str, Any]] = []
    other_container_outputs: list[dict[str, Any]] = []
    exact = []
    local = []
    for entry_number, entry in enumerate(entries):
        for command in entry.get("commands", []):
            direct, container, exact_match, section_match = (
                _command_candidate_groups(
                    scan, command, identity, sections, identities
                )
            )
            if direct:
                direct_outputs.append(command)
            if container:
                if section_match:
                    local_container_outputs.append(command)
                elif entry_number:
                    owner_container_outputs.append(command)
                else:
                    other_container_outputs.append(command)
            if exact_match:
                exact.append(command)
            elif section_match:
                local.append(command)
    return _ordered_candidate_commands(
        (
            direct_outputs,
            local_container_outputs,
            owner_container_outputs,
            exact,
            local,
            other_container_outputs,
        )
    )


def _command_path_parameters(
    scan: ScanRecord, command: Mapping[str, Any], identity: str
) -> list[str]:
    """Return normalized option names whose values identify one target."""

    target_name = Path(identity).name
    parameters = []
    identity_cache = _resolved_identity_cache(scan)
    for argument in command.get("path_arguments", []):
        raw_path = argument.get("path")
        if not raw_path:
            continue
        argument_identity = identity_for_path(scan, raw_path, identity_cache)
        if argument_identity != identity and Path(raw_path).name != target_name:
            continue
        parameter = argument.get("option")
        if parameter:
            parameters.append(parameter.lstrip("-").replace("-", "_"))
    return parameters


def _command_source_context(
    scan: ScanRecord, command: Mapping[str, Any], identity: str
) -> list[str]:
    """Return bounded source lines using the option that carries one path."""

    raw_script = command.get("script")
    if not raw_script or not Path(raw_script).is_file():
        return []
    parameters = _command_path_parameters(scan, command, identity)
    if not parameters:
        return []
    try:
        source_lines = Path(raw_script).read_text(encoding="utf-8").splitlines()
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


def _summary_review_lines(
    adjudication: AdjudicationRecord, item: Mapping[str, Any]
) -> list[str]:
    """Render bounded support candidates for one summary review item."""

    identity = item.get("identity", "-")
    lines = []
    row = next(
        (row for row in adjudication.get("summary", []) if row.get("item") == identity),
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
    return lines


def _orphan_review_context(
    scan: ScanRecord,
    item: Mapping[str, Any],
    entry_id: str,
    identity_cache: Mapping[str, str],
) -> _ReviewCommandContext:
    """Render explicit notes and candidate identities for one orphan review."""

    lines = []
    commands = []
    command_identities: dict[tuple[Any, Any], list[str]] = {}
    for note in item.get("validation_notes", []):
        prefix = f"{note.get('entry')} / " if note.get("entry") else ""
        lines.append(
            f"- Existing Validation note, {prefix}"
            f"`{note.get('section', '-')}` line {note.get('line', '?')}: "
            f"{_packet_text(note.get('text'))} "
            f"[sha256 `{note.get('sha256', '-')}`]"
        )
    for candidate in item.get("candidates", []):
        identity = candidate.get("identity", "")
        lines.append(
            f"- Candidate {candidate.get('kind', 'item')}: `{identity or '-'}`"
        )
        candidates = candidate_commands(
            scan, entry_id, identity, [], identity_cache
        )
        commands.extend(candidates)
        for command in candidates:
            key = (command.get("line"), command.get("command"))
            command_identities.setdefault(key, []).append(identity)
    commands = list(
        {
            (command.get("line"), command.get("command")): command
            for command in commands
        }.values()
    )[:5]
    return _ReviewCommandContext(lines, commands, command_identities)


def _target_review_context(
    scan: ScanRecord,
    item: Mapping[str, Any],
    entry_id: str,
    identity: str,
    identity_cache: Mapping[str, str],
) -> _ReviewCommandContext:
    """Render mechanics and evidence details for one target review."""

    lines = []
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
                f"  - Transformation: {_packet_text(evidence.get('transformation'))}",
                f"  - Mechanical result: `{result.get('status', '-')}` — "
                f"{_packet_text(result.get('detail'))}",
            ]
        )
    return _ReviewCommandContext(
        lines,
        candidate_commands(
            scan,
            entry_id,
            identity,
            item.get("sections", []),
            identity_cache,
        ),
        {},
    )


def _review_command_lines(
    scan: ScanRecord,
    commands: Sequence[Mapping[str, Any]],
    identity: str,
    orphan_identities: Mapping[tuple[Any, Any], Sequence[str]],
) -> list[str]:
    """Render bounded candidate commands and matching producer-code snippets."""

    lines = []
    for candidate_number, command in enumerate(commands, 1):
        command_key = (command.get("line"), command.get("command"))
        lines.append(
            f"- Candidate command {candidate_number}, "
            f"`{command.get('section', '-')}` line "
            f"{command.get('line', '?')}: "
            f"`{_packet_text(command.get('command'), 520)}`"
        )
        context_identities = orphan_identities.get(command_key, [identity])
        for context_identity in context_identities:
            for source_line in _command_source_context(scan, command, context_identity):
                lines.append(f"  - Producer code: `{_packet_text(source_line, 420)}`")
    return lines


def collection_packet_lines(scan: ScanRecord, identity: str) -> list[str]:
    """Return bounded member candidates for one unresolved directory scope."""

    raw = scan.get("resolved_paths", {}).get(identity)
    if not raw:
        return [f"- Collection: `{identity}` (unresolved path)"]
    root = Path(raw)
    resolved_candidates: set[str] = set()
    for child_raw in scan.get("resolved_paths", {}).values():
        child = Path(child_raw)
        try:
            relative = child.relative_to(root)
        except ValueError:
            continue
        if relative.parts and child.is_file():
            resolved_candidates.add(relative.as_posix())
    source = "resolved child dependencies"
    root_candidates: set[str] = set()
    nested_candidates: set[str] = set()
    if root.is_dir():
        had_resolved = bool(resolved_candidates)
        for current, directories, files in os.walk(root):
            relative_root = Path(current).relative_to(root)
            if len(relative_root.parts) >= 2:
                directories[:] = []
            for name in files:
                relative_name = (relative_root / name).as_posix()
                if relative_root == Path("."):
                    root_candidates.add(relative_name)
                else:
                    nested_candidates.add(relative_name)
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


def _review_item_lines(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    item: Mapping[str, Any],
    number: int,
    identity_cache: Mapping[str, str],
) -> list[str]:
    """Render one bounded semantic-review queue item."""

    item_kind = item.get("kind", "unknown")
    entry_id = item.get("entry", "-")
    identity = item.get("identity", "-")
    lines = [
        "",
        f"## Q{number:03d} — {entry_id}: {_packet_text(identity, 180)}",
        "",
        f"- Kind: `{item_kind}`",
    ]
    if item.get("reason"):
        lines.append(f"- Question: {_packet_text(item['reason'])}")
    if item.get("hard_failures"):
        lines.append(
            "- Immutable failures: "
            + ", ".join(f"`{check}`" for check in item["hard_failures"])
            + "; semantic pass is not permitted."
        )
    if item.get("section"):
        lines.append(f"- Section: {_packet_text(item['section'])}")
    if item.get("sections"):
        lines.append(f"- Sections: {_packet_text('; '.join(item['sections']))}")
    for collection in item.get("collections", []):
        lines.extend(collection_packet_lines(scan, collection))
    for candidate in item.get("producer_candidates", []):
        lines.append(
            "- Upstream candidate: "
            f"`{candidate.get('material', '-')}` <- "
            f"`{candidate.get('invocation', '-')}` "
            f"({candidate.get('entry', '-')}:L{candidate.get('line', '-')})"
        )
        lines.append(f"  - Command: `{candidate.get('command', '-')}`")
    if item_kind == "semantic_provenance":
        lines.extend(_summary_review_lines(adjudication, item))
        return lines
    context = (
        _orphan_review_context(scan, item, entry_id, identity_cache)
        if item_kind == "orphan_candidates"
        else _target_review_context(
            scan, item, entry_id, identity, identity_cache
        )
    )
    lines.extend(context.lines)
    lines.extend(
        _review_command_lines(
            scan, context.commands, identity, context.command_identities
        )
    )
    return lines


def make_review_packet(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    *,
    entry: Optional[str] = None,
    target: Optional[str] = None,
    kind: Optional[str] = None,
) -> tuple[str, dict[str, int]]:
    """Render facts and candidates for bounded semantic decisions."""

    queue = list(adjudication["review_queue"])
    if entry is not None:
        queue = [item for item in queue if item.get("entry") == entry]
    if target is not None:
        queue = [item for item in queue if item.get("identity") == target]
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
    counts: dict[str, int] = {}
    identity_cache = _resolved_identity_cache(scan)
    for number, item in enumerate(queue, 1):
        item_kind = item.get("kind", "unknown")
        counts[item_kind] = counts.get(item_kind, 0) + 1
        lines.extend(
            _review_item_lines(
                scan, adjudication, item, number, identity_cache
            )
        )
    return "\n".join(lines) + "\n", counts
