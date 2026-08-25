"""Stable-subject reuse for current semantic review decisions."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .compatibility import (
    decode_input_dependencies,
    input_dependencies_for_check,
    orphan_input_dependencies,
    projection,
)
from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .judgment_rules import SEMANTIC_REVIEW_RULES
from .orphan_rules import SUBTREE_REVIEW_KIND, SUBTREE_RULE_DEPENDENCIES

ReviewSubjectKey = tuple[Any, ...]
ReviewJudgmentIndex = Mapping[
    ReviewSubjectKey, Sequence[Mapping[str, Any]]
]
ReviewJudgmentSource = Sequence[Mapping[str, Any]] | ReviewJudgmentIndex


def _subject_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("kind"),
        value.get("entry"),
        value.get("identity"),
        value.get("material"),
    )


def _template_subject(template: Mapping[str, Any]) -> tuple[Any, ...]:
    return _subject_key(template)


def index_review_judgments(
    judgments: Sequence[Mapping[str, Any]],
) -> dict[ReviewSubjectKey, list[Mapping[str, Any]]]:
    """Index durable judgments by the stable subject used for reuse lookup."""

    indexed: dict[ReviewSubjectKey, list[Mapping[str, Any]]] = {}
    for judgment in judgments:
        subject = judgment.get("subject")
        if not isinstance(subject, Mapping):
            continue
        indexed.setdefault(_subject_key(subject), []).append(judgment)
    return indexed


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
            "review judgment inputs",
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


def _current_collection_inputs(
    scan: ScanRecord,
    members: Mapping[str, list[str]],
    relationships: Mapping[str, str],
) -> list[dict[str, Any]] | None:
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


def _scan_entry(scan: ScanRecord, entry: str) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in scan.get("entries", [])
            if item.get("id") == entry and "error" not in item
        ),
        None,
    )


def _review_decision_answers(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: ReviewJudgmentSource,
) -> list[Any]:
    answers = []
    subject = _template_subject(template)
    expected_rules = (
        SUBTREE_RULE_DEPENDENCIES
        if template.get("kind") == SUBTREE_REVIEW_KIND
        else SEMANTIC_REVIEW_RULES
    )
    candidates = (
        judgments.get(subject, ())
        if isinstance(judgments, Mapping)
        else judgments
    )
    for judgment in candidates:
        stored_subject = judgment.get("subject")
        if (
            judgment.get("kind") != "review-decision"
            or not isinstance(stored_subject, Mapping)
            or _subject_key(stored_subject) != subject
            or judgment.get("rule_dependencies") != expected_rules
        ):
            continue
        decision = judgment.get("decision")
        current = review_judgment_inputs(
            scan, adjudication, queue_item, template, decision
        )
        compatible = _contains_current_inputs(judgment, current)
        if template.get("kind") == SUBTREE_REVIEW_KIND:
            stored = _decoded_inputs(judgment)
            compatible = stored is not None and _scope_map(stored) == _scope_map(
                current
            )
        if compatible:
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
    if template.get("kind") == SUBTREE_REVIEW_KIND:
        return _subtree_review_inputs(scan, template, decision)
    return _ordinary_review_inputs(scan, adjudication, queue_item, template, decision)


def _ordinary_review_inputs(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    decision: Any,
) -> list[dict[str, Any]]:
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


def _subtree_review_inputs(
    scan: ScanRecord, template: Mapping[str, Any], decision: Any
) -> list[dict[str, Any]]:
    entry = _scan_entry(scan, str(template.get("entry", "")))
    if entry is None:
        return []
    choice = decision if isinstance(decision, Mapping) else {}
    note_identity = (
        f"validation-note:{entry.get('id', '')}:{choice.get('validation_note')}"
    )
    return [
        dependency
        for dependency in orphan_input_dependencies(scan, entry, [])
        if dependency.get("semantic_identity") == note_identity
    ]


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


def reusable_review_answer(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    template: Mapping[str, Any],
    judgments: ReviewJudgmentSource,
) -> tuple[Any, str] | None:
    """Return one unambiguous compatible prior review decision."""

    candidates = _review_decision_answers(
        scan, adjudication, queue_item, template, judgments
    )
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
        "Reused from an exact compatible stable-subject review decision.",
    )
