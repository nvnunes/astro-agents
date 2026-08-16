"""Temporary native-v1 to v2 semantic-review reuse adapter.

This module exists only for the Phase 8 migration of the eleven validation
records.  Remove it, and its call site, after every record is native v2.  It
projects prior semantic judgments into current review answers; it never
accepts packets or continuations, whose full identities remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .adjudication import is_success_date
from .compatibility import (
    decode_input_dependencies,
    input_dependencies_for_check,
    orphan_input_dependencies,
    orphan_rule_dependencies,
    projection,
    rule_dependencies_for_check,
)
from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError

MIGRATION_RECORD_COUNT = 11
SEMANTIC_REVIEW_RULES = {"semantic_review": 1}


def _subject_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("kind"),
        value.get("entry"),
        value.get("identity"),
        value.get("material"),
    )


def _template_subject(template: Mapping[str, Any]) -> tuple[Any, ...]:
    return _subject_key(template)


def _scope_map(items: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, ...], Any]:
    return {
        (
            item.get("kind"),
            item.get("semantic_identity"),
            item.get("projection_version"),
            item.get("relationship"),
        ): item.get("content_identity")
        for item in items
    }


def _decoded_inputs(judgment: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    try:
        return decode_input_dependencies(
            judgment.get("input_dependencies"),
            "migration judgment inputs",
            require_supported=False,
        )
    except ValidationToolError:
        return None


def _target_row(
    adjudication: AdjudicationRecord, entry: str, identity: str
) -> Mapping[str, Any] | None:
    if entry == "Summary":
        return next(
            (
                row
                for row in adjudication.get("summary", [])
                if row.get("item") == identity
            ),
            None,
        )
    owner = next(
        (
            row
            for row in adjudication.get("entries", [])
            if row.get("id") == entry
        ),
        None,
    )
    if owner is None:
        return None
    return next(
        (
            row
            for row in owner.get("targets", [])
            if row.get("target") == identity
        ),
        None,
    )


def _summary_resolution(
    queue_item: Mapping[str, Any], row: Mapping[str, Any], basis: Any
) -> dict[str, str] | None:
    if not isinstance(basis, Mapping):
        return None
    entry = basis.get("entry")
    section = basis.get("section")
    line = str(basis.get("lines", "")).split("-", 1)[0]
    declared = set(zip(row.get("entries", []), row.get("sections", [])))
    candidates = [
        candidate
        for candidate in queue_item.get("candidates", [])
        if candidate.get("section") == section
        and str(candidate.get("line", "")) == line
    ]
    if (
        not isinstance(entry, str)
        or not isinstance(section, str)
        or (entry, section) not in declared
        or len(candidates) != 1
    ):
        return None
    return {"entry": entry, "section": section}


def _current_check_inputs(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    basis: Any = None,
) -> list[dict[str, Any]] | None:
    entry = str(queue_item.get("entry", ""))
    identity = str(queue_item.get("identity", ""))
    row = _target_row(adjudication, entry, identity)
    if row is None:
        return None
    check = {
        **row,
        "entry": entry,
        "target": identity,
        "check": "Provenance",
    }
    if entry == "Summary":
        if basis is None:
            candidates = list(queue_item.get("candidates", []))
            declared = list(zip(row.get("entries", []), row.get("sections", [])))
            if len(candidates) != 1:
                return None
            section = candidates[0].get("section")
            entries = [
                name
                for name, name_section in declared
                if name_section == section
            ]
            if len(entries) != 1:
                return None
            basis = {
                "entry": entries[0],
                "section": section,
                "lines": str(candidates[0].get("line", "")),
            }
        resolution = _summary_resolution(queue_item, row, basis)
        if resolution is None:
            return None
        check["resolution"] = resolution
    return input_dependencies_for_check(scan, check)


def _collection_paths(queue_item: Mapping[str, Any]) -> set[str]:
    return {
        str(value)
        for value in queue_item.get("collections", [])
        if isinstance(value, str) and value
    }


def _without_unscoped_collections(
    inputs: Sequence[Mapping[str, Any]], collections: set[str]
) -> list[dict[str, Any]]:
    prefixes = {
        f"exact-material:{path}" for path in collections
    } | {f"collection-membership:{path}" for path in collections}
    return [
        dict(item)
        for item in inputs
        if item.get("semantic_identity") not in prefixes
        and not any(
            str(item.get("semantic_identity", "")).startswith(
                f"collection-member:{path}:"
            )
            for path in collections
        )
    ]


def _contains_current_inputs(
    judgment: Mapping[str, Any], current: Sequence[Mapping[str, Any]]
) -> bool:
    stored = _decoded_inputs(judgment)
    if stored is None or not current:
        return False
    stored_scopes = _scope_map(stored)
    return all(
        stored_scopes.get(scope) == content
        for scope, content in _scope_map(current).items()
    )


def _native_rules_match(judgment: Mapping[str, Any], entry: str) -> bool:
    expected = rule_dependencies_for_check(
        {"check": "Provenance", "entry": entry}
    )
    return judgment.get("rule_dependencies") == expected


def _answer_allowed(
    queue_item: Mapping[str, Any], template: Mapping[str, Any], answer: Any
) -> bool:
    if answer in template.get("allowed_decisions", []):
        return True
    if template.get("kind") != "collection_scope" or not isinstance(
        answer, Mapping
    ):
        return False
    members = answer.get("members")
    return (
        isinstance(members, Mapping)
        and set(members) == _collection_paths(queue_item)
        and all(
            isinstance(values, list)
            and bool(values)
            and values == sorted(set(values))
            and all(isinstance(value, str) and value for value in values)
            for values in members.values()
        )
    )


def _matching_completed_checks(
    judgments: Sequence[Mapping[str, Any]], template: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    subject = {
        "check": "Provenance",
        "entry": template.get("entry"),
        "target": template.get("identity"),
    }
    return [
        judgment
        for judgment in judgments
        if judgment.get("kind") == "completed-check"
        and judgment.get("subject") == subject
        and is_success_date(judgment.get("result"))
    ]


def _legacy_provenance_answers(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> list[Any]:
    kind = template.get("kind")
    if kind not in {"semantic_provenance", "semantic_fallback", "upstream_producer"}:
        return []
    answers: list[Any] = []
    summary_bases: set[str] = set()
    for judgment in _matching_completed_checks(judgments, template):
        projected = _legacy_provenance_for_judgment(
            scan,
            adjudication,
            queue_item,
            template,
            judgment,
        )
        if projected is None:
            continue
        projected_answers, basis = projected
        if kind == "semantic_provenance" and projected_answers:
            summary_bases.add(
                json.dumps(basis, sort_keys=True, separators=(",", ":"))
            )
        answers.extend(projected_answers)
    return [] if len(summary_bases) > 1 else answers


def _legacy_provenance_for_judgment(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgment: Mapping[str, Any],
) -> tuple[list[Any], Any] | None:
    kind = str(template.get("kind", ""))
    entry = str(template.get("entry", ""))
    if not _native_rules_match(judgment, entry):
        return None
    basis = judgment.get("basis")
    current = _current_check_inputs(scan, adjudication, queue_item, basis)
    if current is None or not _contains_current_inputs(
        judgment,
        _without_unscoped_collections(current, _collection_paths(queue_item)),
    ):
        return None
    if kind == "semantic_provenance":
        return ["pass"], basis
    if kind == "semantic_fallback":
        invocation = (
            basis.get("producer_invocation")
            if isinstance(basis, Mapping)
            else None
        )
        allowed = template.get("allowed_decisions", [])
        return ([invocation] if invocation in allowed else []), basis
    bindings = (
        basis.get("producer_bindings") if isinstance(basis, Mapping) else None
    )
    if not isinstance(bindings, list):
        bindings = judgment.get("producer_bindings")
    available_bindings = bindings if isinstance(bindings, list) else []
    answers = [
        binding.get("invocation", binding.get("invocation_identity"))
        for binding in available_bindings
        if isinstance(binding, Mapping)
        and binding.get("material", binding.get("coverage_identity"))
        == template.get("material")
        and binding.get("invocation", binding.get("invocation_identity"))
        in template.get("allowed_decisions", [])
    ]
    return answers, basis


def _selected_collection_identity(
    scan: ScanRecord, collection: str, members: Sequence[str]
) -> dict[str, Any] | None:
    identities = []
    for member in members:
        path = (PurePosixPath(collection) / member).as_posix()
        identity = scan.get("files", {}).get(path)
        if not isinstance(identity, Mapping) or not {
            "size",
            "sha256",
        } <= set(identity):
            return None
        identities.append((member, identity))
    digest = hashlib.sha256()
    for member, identity in identities:
        digest.update(
            f"{member}\0{identity['size']}\0{identity['sha256']}\n".encode()
        )
    return {
        "size": sum(int(identity["size"]) for _, identity in identities),
        "mtime_ns": max(int(identity.get("mtime_ns", 0)) for _, identity in identities),
        "ctime_ns": max(int(identity.get("ctime_ns", 0)) for _, identity in identities),
        "sha256": digest.hexdigest(),
        "members": list(members),
    }


def _legacy_collection_answer(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> list[Any]:
    if template.get("kind") != "collection_scope":
        return []
    expected_collections = _collection_paths(queue_item)
    answers = []
    for judgment in _matching_completed_checks(judgments, template):
        if not _native_rules_match(judgment, str(template.get("entry", ""))):
            continue
        stored = _decoded_inputs(judgment)
        if stored is None:
            continue
        selected = _legacy_collection_selection(stored, expected_collections)
        if selected is None:
            continue
        normalized, relevant, relationships = selected
        current_check = _current_check_inputs(
            scan, adjudication, queue_item, judgment.get("basis")
        )
        if current_check is None or not _contains_current_inputs(
            judgment,
            _without_unscoped_collections(current_check, expected_collections),
        ):
            continue
        current = _current_collection_inputs(
            scan,
            normalized,
            relationships,
            incremental=_incremental_collection_inputs(
                scan, queue_item, normalized, relationships
            ),
        )
        if current is None:
            continue
        if _scope_map(relevant) == _scope_map(current):
            answers.append({"members": normalized})
    return answers


def _legacy_collection_selection(
    stored: Sequence[Mapping[str, Any]], expected: set[str]
) -> tuple[
    dict[str, list[str]], list[dict[str, Any]], dict[str, str]
] | None:
    members: dict[str, list[str]] = defaultdict(list)
    relevant: list[dict[str, Any]] = []
    relationships: dict[str, str] = {}
    for item in stored:
        if item.get("kind") not in {"collection-member", "collection-membership"}:
            continue
        locator = item.get("source_locator")
        if not isinstance(locator, Mapping) or not isinstance(
            locator.get("path"), str
        ):
            return None
        path = str(locator["path"])
        relevant.append(dict(item))
        if item.get("kind") == "collection-membership":
            relationships[path] = str(item.get("relationship", ""))
        member = locator.get("member")
        if item.get("kind") == "collection-member" and isinstance(member, str):
            members[path].append(member)
    normalized = {
        path: sorted(set(values)) for path, values in members.items() if values
    }
    if set(normalized) != expected or set(relationships) != expected or not relevant:
        return None
    return normalized, relevant, relationships


def _current_collection_inputs(
    scan: ScanRecord,
    members: Mapping[str, list[str]],
    relationships: Mapping[str, str],
    *,
    incremental: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    if incremental is not None:
        return [dict(item) for item in incremental]
    current = []
    for path, selected in sorted(members.items()):
        identity = _selected_collection_identity(scan, path, selected)
        if identity is None:
            return None
        relationship = relationships[path]
        current.append(
            projection(
                "collection-membership",
                f"collection-membership:{path}",
                selected,
                relationship,
                source_locator={"path": path},
            )
        )
        current.extend(
            projection(
                "collection-member",
                f"collection-member:{path}:{member}",
                {"collection_identity": identity, "member": member},
                relationship,
                source_locator={"path": path, "member": member},
            )
            for member in selected
        )
    return current


def _incremental_collection_inputs(
    scan: ScanRecord,
    queue_item: Mapping[str, Any],
    members: Mapping[str, list[str]],
    relationships: Mapping[str, str],
) -> list[dict[str, Any]] | None:
    """Return current projections recomputed for a persisted exact member set."""

    matches = [
        check
        for check in scan.get("incremental", {}).get("checks", [])
        if check.get("check") == "Provenance"
        and check.get("entry") == queue_item.get("entry")
        and check.get("target") == queue_item.get("identity")
    ]
    if len(matches) != 1:
        return None
    projected = [
        dict(item)
        for item in matches[0].get("input_dependencies", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("source_locator"), Mapping)
        and item["source_locator"].get("path") in members
        and item.get("kind") in {"collection-member", "collection-membership"}
    ]
    if not projected:
        return None
    expected_scopes = {
        (
            "collection-membership",
            f"collection-membership:{path}",
            relationship,
        )
        for path, relationship in relationships.items()
    } | {
        (
            "collection-member",
            f"collection-member:{path}:{member}",
            relationships[path],
        )
        for path, selected in members.items()
        for member in selected
    }
    actual_scopes = {
        (
            str(item.get("kind")),
            str(item.get("semantic_identity")),
            str(item.get("relationship")),
        )
        for item in projected
    }
    return projected if actual_scopes == expected_scopes else None


def _scan_entry(scan: ScanRecord, entry: str) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in scan.get("entries", [])
            if item.get("id") == entry and "error" not in item
        ),
        None,
    )


def _legacy_orphan_answers(
    scan: ScanRecord,
    template: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> list[Any]:
    if template.get("kind") != "orphan_candidate":
        return []
    entry_id = str(template.get("entry", ""))
    identity = str(template.get("identity", ""))
    entry = _scan_entry(scan, entry_id)
    if entry is None:
        return []
    candidate = next(
        (
            item
            for item in entry.get("orphan_inventory", [])
            if item.get("identity") == identity
        ),
        None,
    )
    if candidate is None:
        return []
    current = [
        item
        for item in orphan_input_dependencies(scan, entry, [candidate])
        if item.get("kind") == "orphan-candidate"
    ]
    answers = []
    subject = {"entry": entry_id, "identity": identity}
    for judgment in judgments:
        if (
            judgment.get("kind") != "orphan-disposition"
            or judgment.get("subject") != subject
            or judgment.get("rule_dependencies") != orphan_rule_dependencies()
            or not _contains_current_inputs(judgment, current)
        ):
            continue
        result = judgment.get("result")
        basis = judgment.get("basis")
        if result == "unresolved":
            answers.append("unresolved")
        elif result == "accepted" and basis in {"graph", "semantic-connection"}:
            answers.append("connected")
        elif result == "accepted" and isinstance(basis, str) and basis.startswith(
            "validation-note:"
        ):
            answers.append(f"retain:{basis.removeprefix('validation-note:')}")
    return answers


def _review_decision_answers(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> list[Any]:
    answers = []
    subject = _template_subject(template)
    for judgment in judgments:
        stored_subject = judgment.get("subject")
        if (
            judgment.get("kind") != "review-decision"
            or not isinstance(stored_subject, Mapping)
            or _subject_key(stored_subject) != subject
            or judgment.get("rule_dependencies") != SEMANTIC_REVIEW_RULES
        ):
            continue
        decision = judgment.get("decision")
        current = review_judgment_inputs(
            scan, adjudication, queue_item, template, decision
        )
        if current and _contains_current_inputs(judgment, current):
            answers.append(decision)
    return answers


def review_judgment_inputs(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    decision: Any = None,
) -> list[dict[str, Any]]:
    """Return the stable semantic dependency projection for a review answer."""

    if template.get("kind") == "orphan_candidate":
        return _orphan_review_inputs(scan, template)
    current = _current_check_inputs(scan, adjudication, queue_item)
    if current is None:
        return []
    result = _without_unscoped_collections(
        current, _collection_paths(queue_item)
    )
    if template.get("kind") != "collection_scope" or not isinstance(
        decision, Mapping
    ):
        return result
    selected = _review_collection_selection(queue_item, decision)
    if selected is None:
        return []
    relationships = {path: "input" for path in selected}
    collection_inputs = _current_collection_inputs(scan, selected, relationships)
    if collection_inputs is None:
        return []
    result.extend(collection_inputs)
    return sorted(
        result,
        key=lambda item: (
            item["kind"],
            item["semantic_identity"],
            item["projection_version"],
            item["relationship"],
        ),
    )


def _orphan_review_inputs(
    scan: ScanRecord, template: Mapping[str, Any]
) -> list[dict[str, Any]]:
    entry = _scan_entry(scan, str(template.get("entry", "")))
    if entry is None:
        return []
    candidate = next(
        (
            item
            for item in entry.get("orphan_inventory", [])
            if item.get("identity") == template.get("identity")
        ),
        None,
    )
    return (
        orphan_input_dependencies(scan, entry, [candidate])
        if candidate is not None
        else []
    )


def _review_collection_selection(
    queue_item: Mapping[str, Any], decision: Mapping[str, Any]
) -> dict[str, list[str]] | None:
    selected = decision.get("members")
    if not isinstance(selected, Mapping) or set(selected) != _collection_paths(
        queue_item
    ):
        return None
    normalized: dict[str, list[str]] = {}
    for path, members in sorted(selected.items()):
        if (
            not isinstance(path, str)
            or not isinstance(members, list)
            or not members
            or members != sorted(set(members))
            or not all(isinstance(member, str) and member for member in members)
        ):
            return None
        normalized[path] = list(members)
    return normalized


def migration_reusable_answer(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> tuple[Any, str] | None:
    """Project one unambiguous compatible prior judgment into a current answer."""

    candidates = [
        *_review_decision_answers(
            scan, adjudication, queue_item, template, judgments
        ),
        *_legacy_provenance_answers(
            scan, adjudication, queue_item, template, judgments
        ),
        *_legacy_collection_answer(
            scan, adjudication, queue_item, template, judgments
        ),
        *_legacy_orphan_answers(scan, template, judgments),
    ]
    allowed = [
        answer
        for answer in candidates
        if _answer_allowed(queue_item, template, answer)
    ]
    unique = {
        json.dumps(answer, sort_keys=True, separators=(",", ":")): answer
        for answer in allowed
    }
    if len(unique) != 1:
        return None
    return (
        next(iter(unique.values())),
        "Reused from an exact compatible native judgment during v1-to-v2 migration.",
    )
