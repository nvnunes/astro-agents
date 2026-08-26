"""Compact semantic-decision contract and queue matching."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from .adjudication import (
    ORPHAN_TARGET,
    is_success_date,
)
from .collection_scopes import (
    COLLECTION_DIRECTORY_SELECTION_KEY,
    COLLECTION_DIRECTORY_SELECTIONS_KEY,
    DIRECTORY_SELECTOR_KEYS,
    validated_directory_selector,
)
from .contracts import (
    AdjudicationRecord,
    ScanRecord,
    ValidationToolError,
)
from .graph import DependencyGraph, RootPolicy
from .graph_adapter import build_dependency_graph
from .graph_queries import (
    ambiguous_producer_nodes,
    assert_unresolved_orphans_unreachable,
    display_identity,
    orphan_location,
    orphan_locations,
    orphanable_nodes,
    provenance_nodes,
    target_provenance_seeds,
)
from .orphan_rules import below, effective_basis, material_scope, subtree_basis
from .producer_bindings import (
    ProducerBindingOptions,
    check_workflow_command,
    identity_for_path,
    producer_bindings_for_check,
    resolved_identity_cache,
    verify_producer_binding,
)
from .review_batches import (
    orphan_fingerprint_context,
)
from .review_index import ReviewContextIndex, ReviewQuerySession

DECISION_SCHEMA_VERSION = 7
DECISION_DEPENDENCY_KEYS = {
    "members",
    COLLECTION_DIRECTORY_SELECTIONS_KEY,
    "add_dependencies",
    "remove_dependencies",
    "copy_dependencies_from",
}
PRODUCER_SELECTION_KEY = "producer"
REPRODUCTION_DECISIONS = {
    "reproduced",
    "reproduction-fail",
    "not-run",
    "not-applicable",
}
DECISION_FIELDS_BY_OUTCOME = {
    "support": {"match", "decision", "candidate"},
    "pass": {
        "match",
        "decision",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "fail": {
        "match",
        "decision",
        "findings",
        "failure_basis",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "keep": {
        "match",
        "decision",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "scope": {
        "match",
        "decision",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "orphan": {"match", "decision", "unresolved", "connected", "retained"},
    "orphan-batch": {
        "match",
        "decision",
        "candidate_fingerprints",
        "rationales",
        "unresolved",
        "connected",
        "retained",
        "rule_roots",
        "subtree_splits",
    },
    "orphan-refine": {
        "match",
        "decision",
        "subtree_splits",
    },
    "reproduced": {
        "match",
        "decision",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "reproduction-fail": {
        "match",
        "decision",
        "findings",
        "notes",
        PRODUCER_SELECTION_KEY,
        *DECISION_DEPENDENCY_KEYS,
    },
    "not-run": {"match", "decision", "notes"},
    "not-applicable": {"match", "decision", "notes"},
    "bind": {"match", "decision", "producer_bindings"},
}


class DecisionPolicy(NamedTuple):
    """Versioned fields controlling compact decision decoding."""

    schema_version: int
    fields_by_outcome: Mapping[str, set[str]]
    reproduction_decisions: set[str]


def decision_policy() -> DecisionPolicy:
    """Return the current compact decision contract."""

    return DecisionPolicy(
        DECISION_SCHEMA_VERSION,
        DECISION_FIELDS_BY_OUTCOME,
        REPRODUCTION_DECISIONS,
    )


def validated_decision_actions(
    decisions: Mapping[str, Any], policy: DecisionPolicy
) -> list[Dict[str, Any]]:
    """Return decision actions after validating their outer contract."""

    if set(decisions) != {"schema_version", "actions"}:
        raise ValidationToolError(
            "decisions must contain exactly schema_version and actions"
        )
    if decisions["schema_version"] != policy.schema_version:
        raise ValidationToolError("unsupported decision schema_version")
    actions = decisions["actions"]
    if not isinstance(actions, list) or not all(
        isinstance(action, dict) for action in actions
    ):
        raise ValidationToolError("decision actions must be a list")
    return cast(list[Dict[str, Any]], actions)


def decision_matches(
    queue: Sequence[Dict[str, Any]],
    action: Dict[str, Any],
    action_number: int,
    policy: DecisionPolicy,
) -> list[Dict[str, Any]]:
    """Resolve one exact decision matcher against the current review queue."""

    allowed_action_keys = set().union(*policy.fields_by_outcome.values())
    if set(action) - allowed_action_keys:
        raise ValidationToolError(f"decision action {action_number} has unknown keys")
    matcher = action.get("match")
    if (
        not isinstance(matcher, dict)
        or not matcher
        or set(matcher) - {"kind", "entry", "identity", "targets"}
    ):
        raise ValidationToolError(
            f"decision action {action_number} has an invalid match"
        )
    if "targets" not in matcher:
        matches = [
            item
            for item in queue
            if all(item.get(key) == value for key, value in matcher.items())
        ]
    else:
        matches = _target_matches(queue, matcher, action_number)
    if not matches:
        raise ValidationToolError(
            f"decision action {action_number} matches no unresolved queue items"
        )
    return matches


def _target_matches(
    queue: Sequence[Dict[str, Any]],
    matcher: Mapping[str, Any],
    action_number: int,
) -> list[Dict[str, Any]]:
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
        raise ValidationToolError("targets must contain exact entry/identity objects")
    target_pairs = {(target["entry"], target["identity"]) for target in targets}
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
    return matches


def validated_decision_outcome(
    action: Dict[str, Any], action_number: int, policy: DecisionPolicy
) -> str:
    """Return one outcome after rejecting unused or unsupported fields."""

    decision = action.get("decision")
    if not isinstance(decision, str) or decision not in policy.fields_by_outcome:
        raise ValidationToolError(
            f"decision action {action_number} has an invalid decision"
        )
    unused = set(action) - policy.fields_by_outcome[decision]
    if unused:
        raise ValidationToolError(
            f"decision action {action_number} has keys not used by {decision}: "
            f"{', '.join(sorted(unused))}"
        )
    return decision


def validate_queue_decision_kind(
    item: Dict[str, Any], decision: str, policy: DecisionPolicy
) -> None:
    """Reject decisions that do not apply to a queue item's kind."""

    is_reproduction = item.get("kind") == "reproduction"
    if is_reproduction != (decision in policy.reproduction_decisions):
        raise ValidationToolError(
            "reproduction queue items require a reproduction decision, "
            "and reproduction decisions apply only to those items"
        )
    if item.get("kind") == "orphan_candidates" and decision not in {
        "orphan",
        "orphan-batch",
        "orphan-refine",
    }:
        raise ValidationToolError(
            "orphan candidates require an item-level orphan decision"
        )
    if decision in {"orphan", "orphan-batch", "orphan-refine"} and item.get("kind") != (
        "orphan_candidates"
    ):
        raise ValidationToolError(
            "orphan decisions apply only to orphan-candidate rows"
        )
    if item.get("kind") == "upstream_producer" and decision not in {
        "bind",
        "fail",
    }:
        raise ValidationToolError(
            "upstream producer choices require a bind or fail decision"
        )
    if decision == "bind" and item.get("kind") != "upstream_producer":
        raise ValidationToolError(
            "bind decisions apply only to upstream producer choices"
        )


def semantic_failure_bases(item: Mapping[str, Any]) -> set[str]:
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


def _decision_target(
    adjudication: Mapping[str, Any],
    entry_id: str,
    identity: str,
    sections: Any = None,
    *,
    allow_equivalent: bool = False,
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
    if len(matches) > 1 and isinstance(sections, list):
        matches = [row for row in matches if row.get("sections") == sections]
    if len(matches) > 1 and allow_equivalent:
        normalized = [
            {key: value for key, value in row.items() if key != "sections"}
            for row in matches
        ]
        if all(row == normalized[0] for row in normalized[1:]):
            matches = matches[:1]
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


def _expanded_selected_member(root: Path, identity: str, member: str) -> list[str]:
    """Resolve one selected file or directory to exact collection members."""

    relative = Path(member)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationToolError(
            f"collection member must be a relative child path: {member}"
        )
    child = root / relative
    if child.is_file():
        return [relative.as_posix()]
    if not child.is_dir():
        raise ValidationToolError(
            "collection member does not exist as a file or directory: "
            f"{identity}: {member}"
        )
    descendants = [
        path.relative_to(root).as_posix()
        for path in child.rglob("*")
        if path.is_file()
    ]
    if not descendants:
        raise ValidationToolError(
            f"collection member directory has no files: {identity}: {member}"
        )
    return descendants


def _validated_member_selection(
    scan: Mapping[str, Any], identity: str, members: Any
) -> tuple[List[str], dict[str, str] | None]:
    """Validate and expand one collection selection and its optional basis."""

    raw = scan.get("resolved_paths", {}).get(identity)
    if raw is None or not Path(raw).is_dir():
        raise ValidationToolError(f"collection dependency is not resolved: {identity}")
    root = Path(raw)
    if isinstance(members, dict):
        if set(members) == DIRECTORY_SELECTOR_KEYS:
            selection = validated_directory_selector(root, members)
            return selection.members, {
                "directory": selection.directory,
                "membership_identity": selection.membership_identity,
            }
        if set(members) != {"glob"} or not isinstance(members["glob"], str):
            raise ValidationToolError(
                "collection member selector must be a glob or an exact directory "
                "selector"
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
    if (
        not isinstance(members, list)
        or not members
        or not all(isinstance(member, str) and member for member in members)
    ):
        raise ValidationToolError(
            f"collection members for {identity} must be a nonempty string list "
            "or a glob selector"
        )
    return (
        sorted(
            {
                selected
                for member in members
                for selected in _expanded_selected_member(root, identity, member)
            }
        ),
        None,
    )


def _validated_member_paths(
    scan: Mapping[str, Any], identity: str, members: Any
) -> List[str]:
    """Validate and expand a compact decision's collection-member scope."""

    return _validated_member_selection(scan, identity, members)[0]


def canonical_review_decisions(
    scan: Mapping[str, Any], decisions: Mapping[str, Any]
) -> dict[str, Any]:
    """Expand collection-directory selections into exact durable file scopes."""

    canonical = copy.deepcopy(dict(decisions))
    for row in canonical.get("items", []):
        decision = row.get("decision")
        if row.get("kind") != "collection_scope" or not isinstance(
            decision, dict
        ):
            continue
        members = decision.get("members")
        if not isinstance(members, dict):
            continue
        expanded = {}
        selections = copy.deepcopy(
            row.get(COLLECTION_DIRECTORY_SELECTIONS_KEY, {})
        )
        for identity, selected in members.items():
            expanded_members, selection = _validated_member_selection(
                scan, identity, selected
            )
            expanded[identity] = expanded_members
            if selection is not None:
                selections[identity] = selection
        decision["members"] = expanded
        if selections:
            row[COLLECTION_DIRECTORY_SELECTIONS_KEY] = selections
    return canonical


def _copy_decision_dependencies(
    adjudication: Mapping[str, Any],
    dependencies: List[Dict[str, Any]],
    entry_id: str,
    copy_from: Any,
) -> Optional[str]:
    """Copy one target's reviewed producer contract and non-target dependencies."""

    if copy_from is None:
        return None
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
    source_kind, source = _decision_target(adjudication, source_entry, source_identity)
    if source_kind != "entry":
        raise ValidationToolError("Summary dependencies cannot be copied")
    existing = {(item.get("path"), item.get("role")) for item in dependencies}
    dependencies.extend(
        copy.deepcopy(dependency)
        for dependency in source.get("dependencies", [])
        if dependency.get("role") != "target"
        and (dependency.get("path"), dependency.get("role")) not in existing
    )
    producer_invocation = source.get("producer_invocation")
    return producer_invocation if isinstance(producer_invocation, str) else None


def _add_decision_dependencies(
    scan: Mapping[str, Any], dependencies: List[Dict[str, Any]], additions: Any
) -> None:
    """Add scan-resolved dependencies and optional exact collection members."""

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
        dependency = (
            matches[0] if matches else {"path": identity, "role": addition["role"]}
        )
        if not matches:
            dependencies.append(dependency)
        if "members" in addition:
            dependency["members"] = _validated_member_paths(
                scan, identity, addition["members"]
            )


def _remove_decision_dependencies(
    dependencies: List[Dict[str, Any]], removals: Any
) -> None:
    """Remove dependencies by exact path identity."""

    if not isinstance(removals, list) or not all(
        isinstance(identity, str) for identity in removals
    ):
        raise ValidationToolError("remove_dependencies must be a string list")
    removed = set(removals)
    dependencies[:] = [
        dependency
        for dependency in dependencies
        if dependency.get("path") not in removed
    ]


def _deduplicate_dependencies(dependencies: List[Dict[str, Any]]) -> None:
    """Collapse repeated path/role dependencies without losing member scope."""

    unique: List[Dict[str, Any]] = []
    by_key: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for dependency in dependencies:
        key = (dependency.get("path"), dependency.get("role"))
        retained = by_key.get(key)
        if retained is None:
            retained = dict(dependency)
            by_key[key] = retained
            unique.append(retained)
            continue
        retained_members = retained.get("members")
        candidate_members = dependency.get("members")
        if retained_members is None and candidate_members is not None:
            retained["members"] = candidate_members
        elif (
            retained_members is not None
            and candidate_members is not None
            and retained_members != candidate_members
        ):
            raise ValidationToolError(
                "duplicate dependency has conflicting member scopes: "
                f"{dependency.get('path')}"
            )
        retained_selection = retained.get(COLLECTION_DIRECTORY_SELECTION_KEY)
        candidate_selection = dependency.get(COLLECTION_DIRECTORY_SELECTION_KEY)
        if retained_selection is None and candidate_selection is not None:
            retained[COLLECTION_DIRECTORY_SELECTION_KEY] = candidate_selection
        elif (
            retained_selection is not None
            and candidate_selection is not None
            and retained_selection != candidate_selection
        ):
            raise ValidationToolError(
                "duplicate dependency has conflicting directory selections: "
                f"{dependency.get('path')}"
            )
    dependencies[:] = unique


def _scope_decision_collections(
    scan: Mapping[str, Any],
    dependencies: List[Dict[str, Any]],
    member_scopes: Any,
    directory_selections: Any = None,
) -> None:
    """Apply exact reviewed member scopes to existing directory dependencies."""

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
    if directory_selections is None:
        return
    if not isinstance(directory_selections, dict) or not set(
        directory_selections
    ) <= set(member_scopes):
        raise ValidationToolError(
            "collection directory selections must match reviewed member scopes"
        )
    for identity, selection in directory_selections.items():
        if not isinstance(selection, dict) or set(selection) != DIRECTORY_SELECTOR_KEYS:
            raise ValidationToolError(
                "collection directory selection has an invalid shape"
            )
        matches = [
            dependency
            for dependency in dependencies
            if dependency.get("path") == identity
        ]
        if len(matches) != 1:
            raise ValidationToolError(
                f"collection dependency is not unique on target: {identity}"
            )
        current = validated_directory_selector(
            Path(str(scan["resolved_paths"][identity])), selection
        )
        if matches[0].get("members") != current.members:
            raise ValidationToolError(
                "collection directory selection differs from its explicit members: "
                f"{identity}"
            )
        matches[0][COLLECTION_DIRECTORY_SELECTION_KEY] = dict(selection)


def _select_decision_producer(
    context: _ReviewDecisionContext,
    selection: Any,
) -> None:
    """Bind a semantic producer choice to one exact recorded invocation."""

    if selection is None:
        return
    if not isinstance(selection, str) or not selection:
        raise ValidationToolError("producer must be an exact invocation identity")
    if context.review_session is None:
        raise ValidationToolError("producer selection requires review index context")
    invocations = context.review_session.eligible_candidate_invocations(
        context.entry_id,
        context.row.get("target", context.item.get("identity", "")),
        context.item.get("sections", []),
    )
    matches = [invocation for invocation in invocations if invocation.key == selection]
    if len(matches) != 1:
        raise ValidationToolError(
            f"producer candidate {selection} is unavailable for "
            f"{context.item.get('identity', '-')}"
        )
    invocation = matches[0]
    command = invocation.command
    target_identity = context.row.get(
        "target", context.item.get("identity", "")
    )
    if any(
        argument.get("role_hint") == "input"
        and identity_for_path(
            context.scan, argument["path"], context.identity_cache
        )
        == target_identity
        for argument in command.get("path_arguments", [])
    ):
        raise ValidationToolError(
            "selected producer mechanically consumes the reviewed target"
        )
    checked = check_workflow_command(
        command, context.scan, context.identity_cache
    )
    if checked.failures:
        raise ValidationToolError(
            "selected producer has deterministic failures: "
            + "; ".join(sorted(set(checked.failures)))
        )
    verify_producer_binding(
        context.scan,
        str(target_identity),
        invocation.key,
        context.row.get("dependencies", []),
    )
    context.row["producer_invocation"] = invocation.key
    existing = {
        (dependency.get("path"), dependency.get("role"))
        for dependency in context.row.setdefault("dependencies", [])
    }
    context.row["dependencies"].extend(
        dependency
        for dependency in checked.dependencies
        if (dependency.get("path"), dependency.get("role")) not in existing
    )


class _ReviewDecisionContext(NamedTuple):
    scan: ScanRecord
    adjudication: Dict[str, Any]
    item: Dict[str, Any]
    action: Dict[str, Any]
    decision: str
    date: str
    entry_id: str
    kind: str
    row: Dict[str, Any]
    identity_cache: Mapping[str, str]
    review_session: Optional[ReviewQuerySession] = None
    trusted_orphan_fingerprints: Mapping[str, str] | None = None


def _apply_decision_dependencies(context: _ReviewDecisionContext) -> None:
    """Apply bounded dependency edits declared by a reviewed decision."""

    dependencies = context.row.setdefault("dependencies", [])
    copied_producer = _copy_decision_dependencies(
        context.adjudication,
        dependencies,
        context.entry_id,
        context.action.get("copy_dependencies_from"),
    )
    _remove_decision_dependencies(
        dependencies, context.action.get("remove_dependencies", [])
    )
    _add_decision_dependencies(
        context.scan, dependencies, context.action.get("add_dependencies", [])
    )
    _deduplicate_dependencies(dependencies)
    _scope_decision_collections(
        context.scan,
        dependencies,
        context.action.get("members", {}),
        context.action.get(COLLECTION_DIRECTORY_SELECTIONS_KEY),
    )
    _select_decision_producer(
        context, context.action.get(PRODUCER_SELECTION_KEY)
    )
    if copied_producer and not context.row.get("producer_invocation"):
        verify_producer_binding(
            context.scan,
            str(context.row.get("target", context.item.get("identity", ""))),
            copied_producer,
            dependencies,
        )
        context.row["producer_invocation"] = copied_producer


def _apply_summary_support(
    row: Dict[str, Any], item: Mapping[str, Any], action: Mapping[str, Any], date: str
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
    entries = row.get("entries", [])
    sections = row.get("sections", [])
    if len(entries) != 1 or sections != [candidate.get("section")]:
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
            "entry": entries[0],
            "section": sections[0],
            "lines": str(start) if start == end else f"{start}-{end}",
            "text": candidate.get("text", ""),
        }
    ]
    row["findings"] = []


def _replace_decision_findings(row: Dict[str, Any], findings: Any) -> None:
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


def _sync_orphan_entry(adjudication: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Synchronize one catch-all row and queue item with item decisions."""

    queue = adjudication.get("review_queue", [])
    pending = {
        candidate["identity"]
        for item in queue
        if item.get("kind") == "orphan_candidates" and item.get("entry") == entry["id"]
        for candidate in item.get("candidates", [])
    }
    reportable = [
        item["identity"]
        for item in entry.get("orphan_items", [])
        if item.get("decision") == "unresolved" or item["identity"] in pending
    ]
    rows = [
        row for row in entry.get("targets", []) if row.get("target") == ORPHAN_TARGET
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


def _scope_resolved_collection_members(
    scan: Mapping[str, Any], dependencies: List[Dict[str, Any]]
) -> None:
    """Scope collections to exact child input files already in one row."""

    resolved_paths = scan.get("resolved_paths", {})
    for dependency in dependencies:
        identity = dependency.get("path")
        if (
            not isinstance(identity, str)
            or dependency.get("members")
            or scan.get("mechanical_checks", {}).get(identity, {}).get("type")
            != "directory"
        ):
            continue
        root = Path(resolved_paths[identity]).resolve()
        members = []
        for candidate in dependencies:
            candidate_identity = candidate.get("path")
            candidate_raw = resolved_paths.get(candidate_identity)
            if (
                candidate.get("role") != "input"
                or not isinstance(candidate_raw, str)
                or candidate_identity == identity
            ):
                continue
            path = Path(candidate_raw).resolve()
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                members.append(relative.as_posix())
        if members:
            dependency["members"] = sorted(set(members))


def _queued_collection_scope(
    queue: Sequence[Dict[str, Any]], entry_id: str, target: str
) -> Optional[Dict[str, Any]]:
    """Return the existing collection-scope item for one target, if any."""

    return next(
        (
            item
            for item in queue
            if item.get("entry") == entry_id
            and item.get("identity") == target
            and item.get("kind") == "collection_scope"
        ),
        None,
    )


def _reconcile_collection_queue(
    queue: List[Dict[str, Any]],
    entry: Mapping[str, Any],
    row: Mapping[str, Any],
    collections: Sequence[str],
) -> None:
    """Synchronize one target's queue item with its unscoped collections."""

    queued = _queued_collection_scope(queue, entry["id"], row["target"])
    if not collections:
        if queued is not None:
            queue.remove(queued)
        return
    queued = next(
        (
            item
            for item in queue
            if item.get("entry") == entry["id"]
            and item.get("identity") == row["target"]
            and item.get("kind")
            not in {"orphan_candidates", "evidence_record_error", "reproduction"}
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
        queued["collections"] = sorted(set(collections))


def reconcile_semantic_dependencies(
    scan: ScanRecord, adjudication: Dict[str, Any]
) -> None:
    """Project successful dependency closures from the canonical graph."""

    resolved_paths = scan.get("resolved_paths", {})
    script_inventory = {
        Path(resolved_paths[identity]).resolve()
        for identity in scan.get("script_inventory", [])
        if identity in resolved_paths
    }
    queue = adjudication.get("review_queue", [])
    graph = build_dependency_graph(scan, adjudication)
    for entry in adjudication.get("entries", []):
        for row in entry.get("targets", []):
            if row.get("target") == ORPHAN_TARGET or not is_success_date(
                row.get("provenance")
            ):
                continue
            dependencies = row.setdefault("dependencies", [])
            seeds = target_provenance_seeds(
                graph,
                entry["id"],
                row["target"],
                dependencies,
                row.get("producer_invocation"),
            )
            closure = provenance_nodes(
                graph,
                ((seed, RootPolicy.PRESENTED) for seed in seeds),
            )
            existing = {item["path"] for item in dependencies}
            closure_identities = {display_identity(graph, key) for key in closure}
            for identity in sorted(closure_identities):
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

            ambiguity = ambiguous_producer_nodes(graph, closure)
            queued = next(
                (
                    item
                    for item in queue
                    if item.get("kind") == "upstream_producer"
                    and item.get("entry") == entry["id"]
                    and item.get("identity") == row["target"]
                ),
                None,
            )
            candidates = [
                {
                    "material": display_identity(graph, material),
                    "invocation": producer.identity,
                    "entry": graph.node(producer).attribute("entry", ""),
                    "line": graph.node(producer).attribute("line", 0),
                    "command": graph.node(producer).attribute("command", ""),
                }
                for material, producers in sorted(ambiguity.items())
                for producer in producers
            ]
            if candidates:
                payload = {
                    "entry": entry["id"],
                    "kind": "upstream_producer",
                    "identity": row["target"],
                    "sections": row["sections"],
                    "producer_candidates": candidates,
                    "reason": (
                        "select one exact producer for each ambiguous "
                        "generated input"
                    ),
                }
                if queued is None:
                    queue.append(payload)
                else:
                    queued.update(payload)
            elif queued is not None:
                queue.remove(queued)

            _scope_resolved_collection_members(scan, dependencies)
            collections = [
                item["path"]
                for item in dependencies
                if scan.get("mechanical_checks", {}).get(item["path"], {}).get("type")
                == "directory"
                and not item.get("members")
            ]
            _reconcile_collection_queue(queue, entry, row, collections)


def _ensure_orphan_row(entry: Dict[str, Any]) -> None:
    """Create the catch-all row when local reachability reopens review."""

    if any(row.get("target") == ORPHAN_TARGET for row in entry.get("targets", [])):
        return
    items = entry.get("orphan_items", [])
    reportable = [
        item["identity"]
        for item in items
        if item.get("decision") in {"pending", "unresolved"}
    ]
    if not reportable:
        return
    entry.setdefault("targets", []).append(
        {
            "target": ORPHAN_TARGET,
            "sections": ["-"],
            "integrity": "N/A",
            "provenance": "FAIL",
            "reproducibility": "N/A",
            "notes": f"{len(reportable)} unresolved items",
            "dependencies": [
                {"path": path, "role": "entry"}
                for path in entry.get("scope_paths", [entry["path"]])
            ],
            "findings": [],
            "orphan_items": items,
        }
    )


def reconcile_graph_orphans(
    scan: ScanRecord,
    adjudication: Dict[str, Any],
    preserve_pending: Optional[set[str]] = None,
) -> DependencyGraph:
    """Make graph reachability authoritative for orphan classification."""

    graph = build_dependency_graph(scan, adjudication)
    namespace = Path(scan["summary"]).with_suffix("").as_posix()
    graph_orphans = orphan_locations(graph, namespace)
    queue = adjudication.get("review_queue", [])
    queue[:] = [item for item in queue if item.get("kind") != "orphan_candidates"]
    scan_entries = {
        entry["id"]: entry for entry in scan.get("entries", []) if "error" not in entry
    }

    for entry in adjudication.get("entries", []):
        scanned = scan_entries.get(entry["id"], {})
        candidates = {
            item["identity"]: item for item in scanned.get("orphan_inventory", [])
        }
        items = entry.get("orphan_items", [])
        for item in items:
            identity = item["identity"]
            if preserve_pending is not None and identity in preserve_pending:
                item["decision"] = "pending"
                item["basis"] = "-"
            elif (entry["id"], identity) not in graph_orphans:
                item["decision"] = "accepted"
                basis = effective_basis(item.get("basis"))
                if basis != "semantic-connection" and not basis.startswith(
                    "validation-note:"
                ):
                    item["basis"] = "graph"
            elif item.get("decision") != "unresolved":
                item["decision"] = "pending"
                item["basis"] = "-"
        pending = [
            candidates[item["identity"]]
            for item in items
            if item.get("decision") == "pending" and item["identity"] in candidates
        ]
        if pending:
            queue.append(
                {
                    "entry": entry["id"],
                    "kind": "orphan_candidates",
                    "identity": ORPHAN_TARGET,
                    "candidates": pending,
                    "validation_notes": scanned.get("validation_notes", []),
                    "reason": (
                        "classify graph-unreachable candidates as unresolved, "
                        "semantically connected to presented work, or retained through "
                        "one exact pre-existing Validation note"
                    ),
                }
            )
        _ensure_orphan_row(entry)
        _sync_orphan_entry(adjudication, entry)

    unresolved_locations = {
        (entry["id"], item["identity"])
        for entry in adjudication.get("entries", [])
        for item in entry.get("orphan_items", [])
        if item.get("decision") == "unresolved"
    }
    unresolved_keys = {
        key
        for key in orphanable_nodes(graph, namespace)
        if orphan_location(graph, key) in unresolved_locations
    }
    assert_unresolved_orphans_unreachable(graph, unresolved_keys)
    return graph


def _validated_orphan_partition(
    item: Mapping[str, Any], unresolved: Any, connected: Any, retained: Any
) -> Tuple[set[str], set[str], Dict[str, str]]:
    """Validate and return one complete residual-orphan partition."""

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
    if (
        not isinstance(connected, list)
        or not all(isinstance(identity, str) for identity in connected)
        or len(connected) != len(set(connected))
        or not set(connected) <= set(candidates)
    ):
        raise ValidationToolError(
            "an orphan decision requires a unique connected subset of its candidates"
        )
    if not isinstance(retained, list) or not all(
        isinstance(value, dict)
        and set(value) == {"identity", "validation_note"}
        and all(isinstance(field, str) for field in value.values())
        for value in retained
    ):
        raise ValidationToolError(
            "retained orphan items require identity/validation_note objects"
        )
    retained_by_identity = {
        value["identity"]: value["validation_note"] for value in retained
    }
    if len(retained_by_identity) != len(retained):
        raise ValidationToolError("retained orphan identities must be unique")
    partitions = (set(unresolved), set(connected), set(retained_by_identity))
    if set().union(*partitions) != set(candidates) or any(
        left & right
        for index, left in enumerate(partitions)
        for right in partitions[index + 1 :]
    ):
        raise ValidationToolError(
            "orphan candidates must be partitioned into unresolved, connected, "
            "and retained"
        )
    available_notes = {note.get("sha256") for note in item.get("validation_notes", [])}
    if not set(retained_by_identity.values()) <= available_notes:
        raise ValidationToolError(
            "retained orphan basis must name an existing Validation note SHA-256"
        )
    return set(unresolved), set(connected), retained_by_identity


def _validated_orphan_batch_item(
    scan: ScanRecord,
    adjudication: Mapping[str, Any],
    item: Mapping[str, Any],
    action: Mapping[str, Any],
    trusted_fingerprints: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Return exact submitted candidates after candidate-scoped stale checks."""

    fingerprints = action.get("candidate_fingerprints")
    rationales = action.get("rationales")
    if (
        not isinstance(fingerprints, dict)
        or not fingerprints
        or not all(
            isinstance(identity, str)
            and identity
            and isinstance(fingerprint, str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            for identity, fingerprint in fingerprints.items()
        )
    ):
        raise ValidationToolError("orphan batch candidate fingerprints are invalid")
    if rationales is not None and (
        not isinstance(rationales, dict)
        or set(rationales) != set(fingerprints)
        or not all(
            isinstance(identity, str)
            and isinstance(rationale, str)
            and rationale.strip()
            for identity, rationale in rationales.items()
        )
    ):
        raise ValidationToolError(
            "orphan batch rationales must cover every submitted candidate"
        )
    entry_id = item.get("entry")
    if not isinstance(entry_id, str):
        raise ValidationToolError("orphan batch lacks an entry identity")
    current = {
        candidate.get("identity"): candidate
        for candidate in item.get("candidates", [])
        if isinstance(candidate, dict) and isinstance(candidate.get("identity"), str)
    }
    if not set(fingerprints) <= set(current):
        raise ValidationToolError(
            "orphan batch contains candidates no longer pending in this adjudication"
        )
    if not _fingerprints_are_trusted(fingerprints, trusted_fingerprints):
        fingerprint_context = orphan_fingerprint_context(
            scan,
            adjudication.get("schema_version"),
            entry_id,
            DECISION_SCHEMA_VERSION,
        )
        for identity, fingerprint in fingerprints.items():
            expected = fingerprint_context.fingerprint(current[identity])
            if fingerprint != expected:
                raise ValidationToolError(
                    f"orphan candidate fingerprint is stale or invalid: {identity}"
                )
    selected = dict(item)
    selected["candidates"] = [current[identity] for identity in sorted(fingerprints)]
    return selected


def _validated_subtree_splits(
    scan: ScanRecord,
    adjudication: Mapping[str, Any],
    item: Mapping[str, Any],
    action: Mapping[str, Any],
) -> list[str]:
    """Return exact split roots after candidate and material validation."""

    raw = action.get("subtree_splits", [])
    if not isinstance(raw, list):
        raise ValidationToolError("orphan subtree splits must be a list")
    if not raw:
        return []
    current = [
        candidate
        for candidate in item.get("candidates", [])
        if isinstance(candidate, Mapping)
    ]
    roots = []
    for split in raw:
        if not (
            isinstance(split, Mapping)
            and set(split)
            == {"root", "material", "rationale", "candidate_fingerprints"}
            and all(
                isinstance(split.get(key), str) and split.get(key)
                for key in ("root", "material", "rationale")
            )
            and isinstance(split.get("candidate_fingerprints"), Mapping)
        ):
            raise ValidationToolError("orphan subtree split is invalid")
        root = str(split["root"])
        scope = material_scope(root)
        covered = {
            str(candidate.get("identity"))
            for candidate in current
            if below(root, str(candidate.get("identity", "")))
        }
        if scope is None or scope[0] != split["material"] or not covered:
            raise ValidationToolError("orphan subtree split names an invalid root")
        split_fingerprints = split["candidate_fingerprints"]
        if set(split_fingerprints) != covered:
            raise ValidationToolError(
                "orphan subtree split lacks exact candidate stale protection"
            )
        _validate_reapplied_fingerprints(
            scan,
            adjudication,
            str(item["entry"]),
            split_fingerprints,
        )
        roots.append(root)
    if len(roots) != len(set(roots)):
        raise ValidationToolError("orphan subtree splits contain duplicates")
    return roots


def _apply_orphan_decision(
    adjudication: Dict[str, Any],
    entry_id: str,
    item: Mapping[str, Any],
    action: Mapping[str, Any],
) -> None:
    """Persist item-level outcomes for one orphan-candidate review."""

    unresolved_identities, connected_identities, retained_by_identity = (
        _validated_orphan_partition(
            item,
            action.get("unresolved"),
            action.get("connected"),
            action.get("retained"),
        )
    )
    rule_roots = action.get("rule_roots", {})
    if (
        not isinstance(rule_roots, Mapping)
        or not set(rule_roots)
        <= {
            *unresolved_identities,
            *connected_identities,
            *retained_by_identity,
        }
        or not all(
            isinstance(identity, str)
            and isinstance(root, str)
            and below(root, identity)
            for identity, root in rule_roots.items()
        )
    ):
        raise ValidationToolError("orphan subtree rule roots are invalid")
    entries = [
        entry
        for entry in adjudication.get("entries", [])
        if entry.get("id") == entry_id
    ]
    if len(entries) != 1:
        raise ValidationToolError(f"unknown orphan scope: {entry_id}")
    entry = entries[0]
    for orphan_item in entry.get("orphan_items", []):
        _update_orphan_item(
            orphan_item,
            unresolved_identities,
            connected_identities,
            retained_by_identity,
            rule_roots,
        )
    _update_orphan_report(adjudication, entry_id, entry)


def _update_orphan_item(
    orphan_item: Dict[str, Any],
    unresolved: set[str],
    connected: set[str],
    retained: Mapping[str, str],
    rule_roots: Mapping[str, Any],
) -> None:
    identity = orphan_item.get("identity")
    if not isinstance(identity, str):
        return
    if identity in unresolved:
        decision, basis = "unresolved", "-"
    elif identity in connected:
        decision, basis = "accepted", "semantic-connection"
    elif identity in retained:
        decision, basis = "accepted", f"validation-note:{retained[identity]}"
    else:
        return
    if identity in rule_roots:
        inherited = "unresolved" if decision == "unresolved" else basis
        basis = subtree_basis(str(rule_roots[identity]), inherited)
    orphan_item.update({"decision": decision, "basis": basis})


def _update_orphan_report(
    adjudication: Dict[str, Any], entry_id: str, entry: Mapping[str, Any]
) -> None:
    unresolved_all = [
        orphan_item["identity"]
        for orphan_item in entry.get("orphan_items", [])
        if orphan_item.get("decision") == "unresolved"
    ]
    pending_all = [
        orphan_item["identity"]
        for orphan_item in entry.get("orphan_items", [])
        if orphan_item.get("decision") == "pending"
    ]
    reportable = [*unresolved_all, *pending_all]
    rows = [
        row for row in entry.get("targets", []) if row.get("target") == ORPHAN_TARGET
    ]
    if len(rows) != 1:
        raise ValidationToolError(
            f"orphan review row is not unique: {entry_id}: {ORPHAN_TARGET}"
        )
    if not reportable:
        _remove_decision_target(adjudication, entry_id, ORPHAN_TARGET)
        return
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


def _apply_failure_basis(
    row: Dict[str, Any], item: Mapping[str, Any], action: Mapping[str, Any]
) -> None:
    bases = semantic_failure_bases(item)
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
    if failure_basis is None:
        return
    if not isinstance(failure_basis, str) or failure_basis not in bases:
        raise ValidationToolError("failure_basis does not name an unresolved component")
    row["_failure_basis"] = failure_basis


def _apply_support_decision(context: _ReviewDecisionContext) -> None:
    if context.kind != "summary":
        raise ValidationToolError("support decisions apply only to Summary queue items")
    _apply_summary_support(context.row, context.item, context.action, context.date)


def _apply_pass_decision(context: _ReviewDecisionContext) -> None:
    if context.kind != "entry":
        raise ValidationToolError("Summary success requires a support decision")
    hard_failures = context.item.get("hard_failures", [])
    if hard_failures:
        raise ValidationToolError(
            "semantic pass cannot override deterministic failure: "
            + ", ".join(hard_failures)
        )
    if context.item.get("workflow", {}).get(
        "status"
    ) == "unresolved" and not context.row.get("producer_invocation"):
        raise ValidationToolError(
            "semantic provenance pass requires a concrete producer candidate"
        )
    if context.row.get("producer_invocation"):
        verification_check = {
            "entry": context.entry_id,
            "target": context.row.get("target"),
            "check": "Provenance",
            "dependencies": context.row.get("dependencies", []),
            "resolution": {
                "producer_invocation": context.row["producer_invocation"],
                **(
                    {"producer_bindings": context.row["producer_bindings"]}
                    if context.row.get("producer_bindings")
                    else {}
                ),
            },
        }
        producer_bindings_for_check(context.scan, verification_check)
    for check in ("integrity", "provenance"):
        if context.row.get(check) != "N/A":
            context.row[check] = context.date
    context.row["findings"] = [
        finding
        for finding in context.row.get("findings", [])
        if finding.get("check") not in {"Integrity", "Provenance"}
    ]


def _apply_fail_decision(context: _ReviewDecisionContext) -> None:
    if context.kind == "summary":
        context.row["support_reviewed"] = True
        context.row["support_evidence"] = []
        context.row["entries"] = []
        context.row["sections"] = []
    else:
        for check in ("integrity", "provenance"):
            if context.row.get(check) is None:
                context.row[check] = context.date
    _replace_decision_findings(context.row, context.action.get("findings"))


def _apply_reproduced_decision(context: _ReviewDecisionContext) -> None:
    context.row["reproducibility"] = context.date
    context.row["findings"] = [
        finding
        for finding in context.row.get("findings", [])
        if finding.get("check") != "Reproducibility"
    ]


def _apply_reproduction_fail_decision(context: _ReviewDecisionContext) -> None:
    findings = context.action.get("findings")
    if not isinstance(findings, dict) or set(findings) != {"Reproducibility"}:
        raise ValidationToolError(
            "reproduction-fail requires one Reproducibility finding"
        )
    _replace_decision_findings(context.row, findings)


def _apply_not_run_decision(context: _ReviewDecisionContext) -> None:
    context.row["reproducibility"] = "-"


def _apply_not_applicable_decision(context: _ReviewDecisionContext) -> None:
    context.row["reproducibility"] = "N/A"


_ROW_DECISION_HANDLERS: Dict[str, Callable[[_ReviewDecisionContext], None]] = {
    "support": _apply_support_decision,
    "pass": _apply_pass_decision,
    "fail": _apply_fail_decision,
    "reproduced": _apply_reproduced_decision,
    "reproduction-fail": _apply_reproduction_fail_decision,
    "not-run": _apply_not_run_decision,
    "not-applicable": _apply_not_applicable_decision,
}


def _apply_row_decision(context: _ReviewDecisionContext) -> None:
    _apply_decision_dependencies(context)
    if "notes" in context.action:
        if context.kind != "entry" or not isinstance(context.action["notes"], str):
            raise ValidationToolError("notes are supported only as text on entry rows")
        context.row["notes"] = context.action["notes"]
    handler = _ROW_DECISION_HANDLERS.get(context.decision)
    if handler is not None:
        handler(context)


def _apply_review_item(context: _ReviewDecisionContext) -> None:
    validate_queue_decision_kind(context.item, context.decision, decision_policy())
    if context.decision == "fail":
        _apply_failure_basis(context.row, context.item, context.action)
    if context.decision == "bind":
        bindings = context.action.get("producer_bindings")
        candidates = {
            (candidate["material"], candidate["invocation"])
            for candidate in context.item.get("producer_candidates", [])
        }
        if not isinstance(bindings, list) or not bindings or not all(
            isinstance(binding, dict)
            and set(binding) == {"material", "invocation"}
            and (binding["material"], binding["invocation"]) in candidates
            for binding in bindings
        ) or len({binding["material"] for binding in bindings}) != len(bindings):
            raise ValidationToolError("bind decision selects unavailable producers")
        existing = {
            binding["material"]: binding
            for binding in context.row.get("producer_bindings", [])
        }
        existing.update({binding["material"]: binding for binding in bindings})
        for binding in existing.values():
            verify_producer_binding(
                context.scan,
                binding["material"],
                binding["invocation"],
                context.row.get("dependencies", []),
                ProducerBindingOptions("upstream-reviewed"),
            )
        context.row["producer_bindings"] = [
            existing[key] for key in sorted(existing)
        ]
    elif context.decision in {"orphan", "orphan-batch", "orphan-refine"}:
        _apply_orphan_review_item(context)
    else:
        _apply_row_decision(context)


def _apply_orphan_review_item(context: _ReviewDecisionContext) -> None:
    if context.item.get("kind") != "orphan_candidates" or context.kind != "entry":
        raise ValidationToolError(
            "orphan decisions apply only to orphan-candidate rows"
        )
    item = context.item
    if context.decision in {"orphan-batch", "orphan-refine"}:
        _validated_subtree_splits(
            context.scan,
            context.adjudication,
            context.item,
            context.action,
        )
    if context.decision == "orphan-refine":
        return
    if context.decision == "orphan-batch":
        item = _validated_orphan_batch_item(
            context.scan,
            context.adjudication,
            context.item,
            context.action,
            context.trusted_orphan_fingerprints,
        )
    _apply_orphan_decision(
        context.adjudication,
        context.entry_id,
        item,
        context.action,
    )


def _is_incremental_unresolved_orphan_batch(
    actions: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether actions only finalize already-reconciled pending orphans.

    Unresolved-only batches cannot add graph reachability. Their candidates came
    from the current reconciled queue, so applying them only changes item-level
    dispositions and the residual queue membership.
    """

    return bool(actions) and all(
        action.get("decision") == "orphan-batch"
        and action.get("connected") == []
        and action.get("retained") == []
        for action in actions
    )


def _orphan_batch_reapplication_status(
    scan: ScanRecord,
    adjudication: Mapping[str, Any],
    action: Mapping[str, Any],
    trusted_fingerprints: Mapping[str, str] | None = None,
) -> str:
    """Return `pending` or `applied`, rejecting stale or conflicting repeats."""

    matcher = action.get("match")
    fingerprints = action.get("candidate_fingerprints")
    if (
        not isinstance(matcher, dict)
        or not isinstance(matcher.get("entry"), str)
        or not isinstance(fingerprints, dict)
        or not fingerprints
    ):
        return "pending"
    entry_id = matcher["entry"]
    _validate_reapplied_fingerprints(
        scan,
        adjudication,
        entry_id,
        fingerprints,
        trusted_fingerprints,
    )
    desired = _desired_orphan_decisions(action)
    if set(desired) != set(fingerprints):
        return "pending"
    return _orphan_reapplication_state(adjudication, entry_id, desired)


def _validate_reapplied_fingerprints(
    scan: ScanRecord,
    adjudication: Mapping[str, Any],
    entry_id: str,
    fingerprints: Mapping[str, Any],
    trusted_fingerprints: Mapping[str, str] | None = None,
) -> None:
    if _fingerprints_are_trusted(fingerprints, trusted_fingerprints):
        return
    scan_entry = next(
        (
            entry
            for entry in scan.get("entries", [])
            if entry.get("id") == entry_id and "error" not in entry
        ),
        None,
    )
    if scan_entry is None:
        raise ValidationToolError(f"unknown orphan scope: {entry_id}")
    candidates = {
        candidate.get("identity"): candidate
        for candidate in scan_entry.get("orphan_inventory", [])
        if isinstance(candidate, dict) and isinstance(candidate.get("identity"), str)
    }
    fingerprint_context = orphan_fingerprint_context(
        scan,
        adjudication.get("schema_version"),
        entry_id,
        DECISION_SCHEMA_VERSION,
    )
    for identity, fingerprint in fingerprints.items():
        candidate = candidates.get(identity)
        if candidate is None or fingerprint != fingerprint_context.fingerprint(
            candidate
        ):
            raise ValidationToolError(
                f"orphan candidate fingerprint is stale or invalid: {identity}"
            )


def _fingerprints_are_trusted(
    fingerprints: Mapping[str, Any],
    trusted: Mapping[str, str] | None,
) -> bool:
    """Return whether every submitted digest matches a same-session index."""

    return trusted is not None and all(
        trusted.get(identity) == fingerprint
        for identity, fingerprint in fingerprints.items()
    )


def _desired_orphan_decisions(
    action: Mapping[str, Any],
) -> dict[Any, tuple[str, str]]:
    unresolved = action.get("unresolved", [])
    connected = action.get("connected", [])
    retained = action.get("retained", [])
    if not all(
        isinstance(values, list) for values in (unresolved, connected, retained)
    ):
        return {}
    desired = {identity: ("unresolved", "-") for identity in unresolved}
    desired.update(
        {identity: ("accepted", "semantic-connection") for identity in connected}
    )
    for value in retained:
        if isinstance(value, dict):
            desired[value.get("identity")] = (
                "accepted",
                f"validation-note:{value.get('validation_note')}",
            )
    roots = action.get("rule_roots", {})
    if isinstance(roots, Mapping):
        for identity, root in roots.items():
            current = desired.get(identity)
            if current is not None and isinstance(root, str):
                inherited = "unresolved" if current[0] == "unresolved" else current[1]
                desired[identity] = (current[0], subtree_basis(root, inherited))
    return desired


def _orphan_reapplication_state(
    adjudication: Mapping[str, Any],
    entry_id: str,
    desired: Mapping[Any, tuple[str, str]],
) -> str:
    entry = next(
        (
            value
            for value in adjudication.get("entries", [])
            if value.get("id") == entry_id
        ),
        None,
    )
    if entry is None:
        raise ValidationToolError(f"unknown orphan scope: {entry_id}")
    current = {
        item.get("identity"): (item.get("decision"), item.get("basis"))
        for item in entry.get("orphan_items", [])
    }
    statuses = []
    for identity, expected in desired.items():
        actual = current.get(identity)
        if actual == ("pending", "-"):
            statuses.append("pending")
        elif actual == expected:
            statuses.append("applied")
        else:
            raise ValidationToolError(
                f"orphan batch conflicts with an existing decision: {identity}"
            )
    if len(set(statuses)) > 1:
        raise ValidationToolError("orphan batch is only partially applied")
    return statuses[0]


def _update_batch_queue_after_decision(
    item: Dict[str, Any],
    action: Mapping[str, Any],
    incremental_unresolved_batch: bool,
    queue: list[Dict[str, Any]],
    preserve_pending: Optional[set[str]],
) -> bool:
    decision = action.get("decision")
    splits = [
        str(split["root"])
        for split in action.get("subtree_splits", [])
        if isinstance(split, Mapping) and isinstance(split.get("root"), str)
    ]
    if splits:
        item["subtree_splits"] = sorted({*item.get("subtree_splits", []), *splits})
    if decision == "orphan-refine":
        return False
    if preserve_pending is not None and decision in {"orphan", "orphan-batch"}:
        preserve_pending.difference_update(
            identity
            for field in ("unresolved", "connected")
            for identity in action.get(field, [])
        )
        preserve_pending.difference_update(
            retained.get("identity")
            for retained in action.get("retained", [])
            if isinstance(retained, dict)
        )
    if incremental_unresolved_batch and decision == "orphan-batch":
        selected = set(action.get("unresolved", []))
        remaining = [
            candidate
            for candidate in item.get("candidates", [])
            if candidate.get("identity") not in selected
        ]
        if remaining:
            item["candidates"] = remaining
            return False
    queue.remove(item)
    return True


def _restore_subtree_splits(
    queue: Sequence[Dict[str, Any]], splits: Mapping[str, set[str]]
) -> None:
    """Restore active-session refinement after graph queue reconciliation."""

    for item in queue:
        entry = item.get("entry")
        if item.get("kind") != "orphan_candidates" or entry not in splits:
            continue
        item["subtree_splits"] = sorted(splits[str(entry)])


def _active_subtree_splits(
    queue: Sequence[Mapping[str, Any]], actions: Sequence[Mapping[str, Any]]
) -> dict[str, set[str]]:
    active: dict[str, set[str]] = {}
    for item in queue:
        if item.get("kind") == "orphan_candidates":
            active.setdefault(str(item.get("entry")), set()).update(
                str(root) for root in item.get("subtree_splits", [])
            )
    for action in actions:
        matcher = action.get("match", {})
        if isinstance(matcher, Mapping) and isinstance(matcher.get("entry"), str):
            active.setdefault(str(matcher["entry"]), set()).update(
                str(split["root"])
                for split in action.get("subtree_splits", [])
                if isinstance(split, Mapping) and isinstance(split.get("root"), str)
            )
    return active


def apply_review_decisions(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    decisions: Mapping[str, Any],
    *,
    trusted_orphan_fingerprints: Mapping[
        str, Mapping[str, str]
    ] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Apply compact, explicit decisions and reconcile their graph effects."""

    policy = decision_policy()
    actions = validated_decision_actions(decisions, policy)
    result = copy.deepcopy(cast(Dict[str, Any], adjudication))
    queue = result.get("review_queue")
    if not isinstance(queue, list):
        raise ValidationToolError("review_queue must be a list")
    date = result.get("date")
    if not is_success_date(date):
        raise ValidationToolError("adjudication has an invalid validation date")

    counts: Dict[str, int] = {}
    review_session = (
        ReviewQuerySession(ReviewContextIndex.build(scan))
        if any(PRODUCER_SELECTION_KEY in action for action in actions)
        else None
    )
    identity_cache = (
        review_session.index.identities
        if review_session is not None
        else resolved_identity_cache(scan)
    )
    has_orphan_batch = any(
        action.get("decision") == "orphan-batch" for action in actions
    )
    active_splits = _active_subtree_splits(queue, actions)
    incremental_unresolved_batch = _is_incremental_unresolved_orphan_batch(actions)
    reconcile_after_actions = not incremental_unresolved_batch
    preserve_pending = (
        {
            candidate["identity"]
            for item in queue
            if item.get("kind") == "orphan_candidates"
            for candidate in item.get("candidates", [])
        }
        if has_orphan_batch and not incremental_unresolved_batch
        else None
    )
    for action_number, action in enumerate(actions, 1):
        matcher = action.get("match")
        trusted_entry_fingerprints = (
            trusted_orphan_fingerprints.get(str(matcher.get("entry")))
            if trusted_orphan_fingerprints is not None
            and isinstance(matcher, Mapping)
            else None
        )
        if action.get("decision") == "orphan-batch" and (
            _orphan_batch_reapplication_status(
                scan,
                result,
                action,
                trusted_entry_fingerprints,
            )
            == "applied"
        ):
            counts["orphan-batch-noop"] = counts.get("orphan-batch-noop", 0) + 1
            continue
        matches = decision_matches(queue, action, action_number, policy)
        decision = validated_decision_outcome(action, action_number, policy)
        for item in matches:
            entry_id = item.get("entry")
            identity = item.get("identity")
            if not isinstance(entry_id, str) or not isinstance(identity, str):
                raise ValidationToolError(
                    "review queue items require string entry and identity fields"
                )
            kind, row = _decision_target(
                result,
                entry_id,
                identity,
                item.get("sections"),
                allow_equivalent=decision == "keep",
            )
            _apply_review_item(
                _ReviewDecisionContext(
                    scan=scan,
                    adjudication=result,
                    item=item,
                    action=action,
                    decision=decision,
                    date=cast(str, date),
                    entry_id=entry_id,
                    kind=kind,
                    row=row,
                    identity_cache=identity_cache,
                    review_session=review_session,
                    trusted_orphan_fingerprints=trusted_entry_fingerprints,
                )
            )
            removed = _update_batch_queue_after_decision(
                item,
                action,
                incremental_unresolved_batch,
                queue,
                preserve_pending,
            )
            reconcile_after_actions = reconcile_after_actions or removed
            counts[decision] = counts.get(decision, 0) + 1
    if reconcile_after_actions:
        reconcile_semantic_dependencies(scan, result)
        reconcile_graph_orphans(scan, result, preserve_pending)
        _restore_subtree_splits(queue, active_splits)
    counts["remaining"] = len(queue)
    return result, counts
