"""Typed preparation records for research-log validation adjudication."""

from __future__ import annotations

import copy
import datetime
import os
import re
import time
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

from .compatibility import normalized_command
from .contracts import (
    AdjudicationRecord,
    ScanRecord,
    ValidationToolError,
)
from .evidence import NUMBER_RE, numeric_value_equivalent
from .producer_bindings import (
    resolved_identity_cache,
    workflow_check,
)
from .review_batches import (
    DEFAULT_ORPHAN_BATCH_SIZE,
    OrphanBatch,
    OrphanBatchRequest,
    select_orphan_batch,
)
from .review_index import PreparedInvocation, ReviewContextIndex, ReviewQuerySession

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
    review_session: Optional[ReviewQuerySession] = None
    identity_cache: Optional[Mapping[str, str]] = None


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
class ReviewPacketRequest:
    """Filters and deterministic orphan-batch selection for one review packet."""

    entry: Optional[str] = None
    target: Optional[str] = None
    kind: Optional[str] = None
    batch_size: int = DEFAULT_ORPHAN_BATCH_SIZE
    batch_number: Optional[int] = None


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
        normalized = {
            key: copy.deepcopy(stored[key])
            for key in ("path", "role", "members")
            if key in stored
        }
        matches = [
            dependency
            for dependency in dependencies
            if dependency.get("path") == stored.get("path")
            and dependency.get("role") == stored.get("role")
        ]
        if not matches:
            dependencies.append(normalized)
        elif isinstance(stored.get("members"), list):
            matches[0]["members"] = list(stored["members"])


def prepare_orphan_items(
    entry: Mapping[str, Any],
    prior_orphan: Mapping[str, Any],
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
    review_details: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the bounded semantic packet for one unresolved target."""

    return {
        "entry": entry["id"],
        "kind": (
            "mechanical_failure"
            if review_details["hard_failures"]
            else "semantic_fallback"
        ),
        "identity": target,
        "hard_failures": copy.deepcopy(review_details["hard_failures"]),
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
        "producer_candidates": copy.deepcopy(
            review_details["producer_candidates"]
        ),
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


def _review_producer_candidates(
    target: str,
    invocations: Sequence[PreparedInvocation],
    review_session: ReviewQuerySession,
) -> list[dict[str, Any]]:
    candidates = []
    for invocation in invocations:
        eligibility = review_session.eligibility_for(invocation, target)
        candidates.append(
            {
                "invocation": invocation.key,
                "entry": invocation.entry_id,
                "line": invocation.command.get("line"),
                "command": invocation.command.get("command", ""),
                "normalized_command": normalized_command(
                    str(invocation.command.get("command", ""))
                ),
                "path_arguments": copy.deepcopy(
                    invocation.command.get("path_arguments", [])
                ),
                "coverage_kind": eligibility.kind,
                "coverage_identity": eligibility.coverage_identity,
                "target_member": eligibility.target_member,
            }
        )
    return candidates


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
        entry,
        target,
        cast(ScanRecord, context.scan),
        context.identity_cache,
    )
    review_session = context.review_session or ReviewQuerySession(
        ReviewContextIndex.build(cast(ScanRecord, context.scan))
    )
    eligible_producers = review_session.eligible_candidate_invocations(
        entry["id"], target, grouped["sections"]
    )
    if workflow.get("status") == "unresolved" and not eligible_producers:
        workflow = {
            "status": "fail",
            "detail": "no eligible recorded producer covers this generated target",
            "matched_commands": 0,
        }
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
                {
                    "hard_failures": _target_hard_failures(
                        source, integrity, workflow
                    ),
                    "producer_candidates": _review_producer_candidates(
                        target, eligible_producers, review_session
                    ),
                },
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
        ReviewQuerySession(ReviewContextIndex.build(scan)),
        resolved_identity_cache(scan),
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
    commands: list[PreparedInvocation]
    command_identities: dict[str, list[str]]


def _packet_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def candidate_commands(
    scan: ScanRecord,
    entry_id: str,
    identity: str,
    sections: Sequence[str],
    identity_cache: Optional[Mapping[str, str]] = None,
) -> list[dict[str, Any]]:
    """Return indexed candidates while preserving the legacy public helper."""

    del identity_cache
    return ReviewQuerySession(ReviewContextIndex.build(scan)).candidate_commands(
        entry_id, identity, sections
    )


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
    item: Mapping[str, Any],
    entry_id: str,
    session: ReviewQuerySession,
) -> _ReviewCommandContext:
    """Render explicit notes and candidate identities for one orphan review."""

    lines = []
    commands: list[PreparedInvocation] = []
    command_identities: dict[str, list[str]] = {}
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
        candidates = session.candidate_invocations(entry_id, identity, [])
        commands.extend(candidates)
        for invocation in candidates:
            identities = command_identities.setdefault(invocation.key, [])
            if identity not in identities:
                identities.append(identity)
    commands = list({invocation.key: invocation for invocation in commands}.values())
    return _ReviewCommandContext(lines, commands, command_identities)


def _target_review_context(
    item: Mapping[str, Any],
    entry_id: str,
    identity: str,
    session: ReviewQuerySession,
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
        session.candidate_invocations(
            entry_id, identity, item.get("sections", [])
        ),
        {},
    )


def _review_command_lines(
    session: ReviewQuerySession,
    commands: Sequence[PreparedInvocation],
    identity: str,
    orphan_identities: Mapping[str, Sequence[str]],
) -> list[str]:
    """Render bounded candidate commands and matching producer-code snippets."""

    lines = []
    rendered_source: set[tuple[str, str]] = set()
    for candidate_number, invocation in enumerate(commands, 1):
        command = invocation.command
        eligibility = session.eligibility_for(invocation, identity)
        label = (
            f"Eligible producer `{invocation.key}`"
            if eligibility.eligible
            else f"Diagnostic command {candidate_number} (not selectable)"
        )
        lines.append(
            f"- {label}, "
            f"`{command.get('section', '-')}` line "
            f"{command.get('line', '?')}: "
            f"`{_packet_text(command.get('command'), 520)}`"
        )
        lines.append(f"  - Producer eligibility: {_packet_text(eligibility.reason)}")
        if eligibility.eligible:
            lines.append(
                "  - Proof: "
                f"`{eligibility.kind}`; scope "
                f"`{eligibility.coverage_identity}`; direction "
                f"`{eligibility.direction_evidence}`"
                + (
                    f"; target member `{eligibility.target_member}`"
                    if eligibility.target_member is not None
                    else ""
                )
            )
        context_identities = orphan_identities.get(invocation.key, [identity])
        if invocation.key in orphan_identities:
            lines.append(
                "  - Applies to candidates: "
                + _packet_text("; ".join(context_identities), 4000)
            )
        for context_identity in context_identities:
            session.counters["rendered_candidate_relationships"] = (
                session.counters.get("rendered_candidate_relationships", 0) + 1
            )
            for source_line in session.source_context(invocation, context_identity):
                source_key = (invocation.key, source_line)
                if source_key in rendered_source:
                    continue
                rendered_source.add(source_key)
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
    adjudication: AdjudicationRecord,
    item: Mapping[str, Any],
    number: int,
    session: ReviewQuerySession,
    orphan_batch: OrphanBatch | None = None,
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
    if orphan_batch is not None:
        lines.extend(
            [
                f"- Queue fingerprint: `{orphan_batch.fingerprint}`",
                f"- Orphan batch: {orphan_batch.number} of {orphan_batch.total}",
                f"- Candidates in packet: {len(orphan_batch.candidates)}",
                f"- Candidates remaining in snapshot: {orphan_batch.remaining}",
                "- Batch decision: use `orphan-batch` with the candidate "
                "fingerprints below and a nonempty rationale for every candidate; "
                "batch number and size are not semantic.",
                *(
                    "- Candidate fingerprint: "
                    f"`{identity}` = `{fingerprint}`"
                    for identity, fingerprint in sorted(
                        orphan_batch.candidate_fingerprints.items()
                    )
                ),
            ]
        )
    for collection in item.get("collections", []):
        lines.extend(collection_packet_lines(session.index.scan, collection))
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
        _orphan_review_context(item, entry_id, session)
        if item_kind == "orphan_candidates"
        else _target_review_context(item, entry_id, identity, session)
    )
    lines.extend(context.lines)
    lines.extend(
        _review_command_lines(
            session, context.commands, identity, context.command_identities
        )
    )
    return lines


def _orphan_batch_descriptors(
    orphan_items: Sequence[dict[str, Any]], batch_size: int
) -> list[tuple[dict[str, Any], int]]:
    descriptors: list[tuple[dict[str, Any], int]] = []
    for item in orphan_items:
        candidate_count = len(item.get("candidates", []))
        total = max(1, (candidate_count + batch_size - 1) // batch_size)
        descriptors.extend((item, number) for number in range(1, total + 1))
    return descriptors


def _all_packet_batches(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue: Sequence[dict[str, Any]],
    batch_size: int,
    decision_schema_version: int,
) -> tuple[list[dict[str, Any]], dict[int, OrphanBatch]]:
    selected = list(queue)
    batches = {
        id(item): select_orphan_batch(
            scan,
            adjudication,
            item,
            OrphanBatchRequest(batch_size, 1, decision_schema_version),
        )
        for item in queue
        if item.get("kind") == "orphan_candidates"
    }
    return selected, batches


def _single_packet_batch(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue: Sequence[dict[str, Any]],
    descriptor: tuple[dict[str, Any], int],
    batch_size: int,
) -> tuple[list[dict[str, Any]], dict[int, OrphanBatch]]:
    from .decisions import DECISION_SCHEMA_VERSION

    selected_item, local_number = descriptor
    batch = select_orphan_batch(
        scan,
        adjudication,
        selected_item,
        OrphanBatchRequest(batch_size, local_number, DECISION_SCHEMA_VERSION),
    )
    selected_view = dict(selected_item)
    selected_view["candidates"] = list(batch.candidates)
    selected = [
        selected_view
        if item is selected_item
        else item
        for item in queue
        if item is selected_item or item.get("kind") != "orphan_candidates"
    ]
    return selected, {id(selected_view): batch}


def _packet_orphan_batches(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue: Sequence[dict[str, Any]],
    batch_size: int,
    batch_number: Optional[int],
) -> tuple[list[dict[str, Any]], dict[int, OrphanBatch], dict[str, int]]:
    """Select bounded orphan context while leaving the adjudication untouched."""

    from .decisions import DECISION_SCHEMA_VERSION

    orphan_items = [item for item in queue if item.get("kind") == "orphan_candidates"]
    if not orphan_items:
        if batch_number is not None:
            raise ValidationToolError(
                "an orphan review batch number requires an orphan-candidate queue"
            )
        return list(queue), {}, {
            "orphan_candidates_total": 0,
            "orphan_candidates_in_packet": 0,
            "orphan_candidates_remaining": 0,
            "orphan_batch_number": 0,
            "orphan_batch_total": 0,
        }

    total_candidates = sum(len(item.get("candidates", [])) for item in orphan_items)
    descriptors = _orphan_batch_descriptors(orphan_items, batch_size)
    requested = batch_number or 1
    if requested < 1 or requested > len(descriptors):
        raise ValidationToolError(
            f"orphan review batch {requested} is out of range; "
            f"expected 1-{len(descriptors)}"
        )

    include_all = batch_number is None and total_candidates <= batch_size
    if include_all:
        selected, batches = _all_packet_batches(
            scan, adjudication, queue, batch_size, DECISION_SCHEMA_VERSION
        )
    else:
        selected, batches = _single_packet_batch(
            scan, adjudication, queue, descriptors[requested - 1], batch_size
        )
    selected_count = sum(
        len(item.get("candidates", []))
        for item in selected
        if item.get("kind") == "orphan_candidates"
    )
    return selected, batches, {
        "orphan_candidates_total": total_candidates,
        "orphan_candidates_in_packet": selected_count,
        "orphan_candidates_remaining": total_candidates - selected_count,
        "orphan_batch_number": requested if not include_all else 1,
        "orphan_batch_total": len(descriptors),
    }


def make_review_packet(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    request: ReviewPacketRequest = ReviewPacketRequest(),
    metrics: Optional[Dict[str, Any]] = None,
    query_session: Optional[ReviewQuerySession] = None,
) -> tuple[str, dict[str, int]]:
    """Render facts and candidates for bounded semantic decisions."""

    queue = list(adjudication["review_queue"])
    if request.entry is not None:
        queue = [item for item in queue if item.get("entry") == request.entry]
    if request.target is not None:
        queue = [item for item in queue if item.get("identity") == request.target]
    if request.kind is not None:
        queue = [item for item in queue if item.get("kind") == request.kind]
    if request.batch_size < 1:
        raise ValidationToolError("orphan review batch size must be positive")
    queue, orphan_batches, batch_metrics = _packet_orphan_batches(
        scan,
        adjudication,
        queue,
        request.batch_size,
        request.batch_number,
    )
    session = query_session or ReviewQuerySession(ReviewContextIndex.build(scan))
    render_started = time.monotonic()
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
    if batch_metrics["orphan_candidates_remaining"]:
        lines[1:1] = [
            "",
            "# PARTIAL ORPHAN REVIEW",
            "",
            "This packet contains one deterministic subset of the complete "
            "orphan queue. Generate a new packet after applying this batch.",
        ]
    if batch_metrics["orphan_candidates_total"]:
        lines.extend(
            [
                f"- Orphan packet batch: {batch_metrics['orphan_batch_number']} "
                f"of {batch_metrics['orphan_batch_total']}",
                "- Orphan candidates in packet: "
                f"{batch_metrics['orphan_candidates_in_packet']}",
                "- Orphan candidates remaining: "
                f"{batch_metrics['orphan_candidates_remaining']}",
            ]
        )
    counts: dict[str, int] = {}
    for number, item in enumerate(queue, 1):
        item_kind = item.get("kind", "unknown")
        counts[item_kind] = counts.get(item_kind, 0) + 1
        lines.extend(
            _review_item_lines(
                adjudication,
                item,
                number,
                session,
                orphan_batches.get(id(item)),
            )
        )
    packet = "\n".join(lines) + "\n"
    if metrics is not None:
        metrics.update(session.metrics())
        metrics.update(batch_metrics)
        metrics["render_seconds"] = round(time.monotonic() - render_started, 6)
        metrics["packet_bytes"] = len(packet.encode("utf-8"))
    return packet, counts
