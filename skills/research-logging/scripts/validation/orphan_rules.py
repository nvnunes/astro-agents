"""Prospective subtree subjects and deterministic orphan refinement."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .judgment_rules import (
    SUBTREE_RULE_DEPENDENCIES as _SUBTREE_RULE_DEPENDENCIES,
)

MATERIAL_CLASSES = ("data", "images", "scripts")
SUBTREE_REVIEW_KIND = "orphan_subtree"
SUBTREE_RULE_DEPENDENCIES = _SUBTREE_RULE_DEPENDENCIES
SUBTREE_BASIS_PREFIX = "orphan-subtree:"


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def material_scope(identity: str) -> tuple[str, str] | None:
    """Return the material class and project-relative material root."""

    if identity.startswith("<") and identity.endswith(">"):
        return None
    path = PurePosixPath(identity)
    for index, part in enumerate(path.parts):
        if part in MATERIAL_CLASSES:
            return part, PurePosixPath(*path.parts[: index + 1]).as_posix()
    return None


def below(root: str, identity: str) -> bool:
    """Return whether an identity is the root or one of its descendants."""

    return identity == root or identity.startswith(f"{root}/")


def ancestor_roots(identity: str) -> list[tuple[str, str]]:
    """Return prospective subfolder subjects from broadest to narrowest."""

    scope = material_scope(identity)
    if scope is None:
        return []
    material, material_root = scope
    identity_path = PurePosixPath(identity)
    root_path = PurePosixPath(material_root)
    if identity_path == root_path:
        return []
    relative = identity_path.relative_to(root_path)
    roots: list[str] = []
    current = root_path
    for part in relative.parts:
        current /= part
        roots.append(current.as_posix())
    return [(material, root) for root in roots]


def subtree_subject(entry: str, material: str, root: str) -> dict[str, str]:
    """Return the stable subject of one prospective subtree rule."""

    return {
        "kind": SUBTREE_REVIEW_KIND,
        "entry": entry,
        "identity": root,
        "material": material,
    }


def subtree_basis(root: str, basis: str) -> str:
    """Encode outcome provenance for a disposition inherited from a rule."""

    return f"{SUBTREE_BASIS_PREFIX}{root}:{basis}"


def inherited_basis(value: Any) -> bool:
    """Return whether an item disposition came from a prospective rule."""

    return isinstance(value, str) and value.startswith(SUBTREE_BASIS_PREFIX)


def effective_basis(value: Any) -> str:
    """Return the existing graph disposition encoded by an inherited basis."""

    if not isinstance(value, str) or not inherited_basis(value):
        return value if isinstance(value, str) else ""
    if ":validation-note:" in value:
        return "validation-note:" + value.rsplit(":validation-note:", 1)[1]
    if value.endswith(":semantic-connection"):
        return "semantic-connection"
    if value.endswith(":unresolved"):
        return "-"
    return ""


def disposition_choice(decision: Any) -> tuple[str, str] | None:
    """Decode one exact classify-subtree decision into disposition and basis."""

    if not isinstance(decision, Mapping) or decision.get("action") != (
        "classify-subtree"
    ):
        return None
    disposition = decision.get("disposition")
    if disposition == "unresolved" and set(decision) == {
        "action",
        "disposition",
    }:
        return "unresolved", "-"
    if disposition == "connected" and set(decision) == {
        "action",
        "disposition",
    }:
        return "connected", "semantic-connection"
    note = decision.get("validation_note")
    if (
        disposition == "retained"
        and isinstance(note, str)
        and note
        and set(decision) == {"action", "disposition", "validation_note"}
    ):
        return "retained", f"validation-note:{note}"
    return None


def split_choice(decision: Any) -> bool:
    """Return whether a decision is the exact split-subtree action."""

    return isinstance(decision, Mapping) and dict(decision) == {
        "action": "split-subtree"
    }


def allowed_decisions(notes: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return exact agent choices for one subtree question."""

    choices = [
        {"action": "classify-subtree", "disposition": "unresolved"},
        {"action": "classify-subtree", "disposition": "connected"},
    ]
    choices.extend(
        {
            "action": "classify-subtree",
            "disposition": "retained",
            "validation_note": str(note["sha256"]),
        }
        for note in notes
        if isinstance(note.get("sha256"), str)
    )
    choices.append({"action": "split-subtree"})
    return choices


def candidates_below(
    candidates: Sequence[Mapping[str, Any]], root: str
) -> list[dict[str, Any]]:
    """Return stable candidate copies covered by one subtree root."""

    return sorted(
        (
            dict(candidate)
            for candidate in candidates
            if below(root, str(candidate.get("identity", "")))
        ),
        key=lambda candidate: (
            str(candidate.get("identity", "")).casefold(),
            str(candidate.get("identity", "")),
        ),
    )


def _child_groups(
    candidates: Sequence[Mapping[str, Any]], root: str
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    children: dict[str, list[dict[str, Any]]] = {}
    loose: list[dict[str, Any]] = []
    prefix = f"{root}/"
    for raw in candidates:
        candidate = dict(raw)
        identity = str(candidate.get("identity", ""))
        if identity == root:
            loose.append(candidate)
            continue
        if not identity.startswith(prefix):
            continue
        relative = identity[len(prefix) :]
        head = relative.split("/", 1)[0]
        children.setdefault(f"{root}/{head}", []).append(candidate)
    groups = {}
    for child_root, values in children.items():
        if any(str(value.get("identity", "")) != child_root for value in values):
            groups[child_root] = values
        else:
            loose.extend(values)
    return groups, loose


def refined_questions(
    candidates: Sequence[Mapping[str, Any]], split_roots: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return current subtree questions and exact-path fallback candidates."""

    split = set(split_roots)
    by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
    exact: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = dict(raw)
        scope = material_scope(str(candidate.get("identity", "")))
        if scope is None:
            exact.append(candidate)
            continue
        by_scope.setdefault(scope, []).append(candidate)

    questions: list[dict[str, Any]] = []

    def visit(material: str, root: str, values: list[dict[str, Any]]) -> None:
        if root not in split:
            questions.append({"material": material, "root": root, "candidates": values})
            return
        groups, loose = _child_groups(values, root)
        exact.extend(loose)
        for child_root in sorted(groups, key=lambda value: (value.casefold(), value)):
            visit(material, child_root, groups[child_root])

    for (material, root), values in sorted(by_scope.items()):
        groups, loose = _child_groups(candidates_below(values, root), root)
        exact.extend(loose)
        for child_root in sorted(groups, key=lambda value: (value.casefold(), value)):
            visit(material, child_root, groups[child_root])
    return questions, sorted(
        exact,
        key=lambda candidate: (
            str(candidate.get("identity", "")).casefold(),
            str(candidate.get("identity", "")),
        ),
    )


def structural_summary(question: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded minimum-sufficient structure for one subtree question."""

    root = str(question["root"])
    candidates = list(question.get("candidates", []))
    identities = [str(candidate.get("identity", "")) for candidate in candidates]
    extensions = Counter(
        PurePosixPath(identity).suffix.lower() or "<none>"
        for identity in identities
        if identity != root
    )
    immediate = sorted(
        {
            identity[len(root) + 1 :].split("/", 1)[0]
            for identity in identities
            if identity.startswith(f"{root}/")
        },
        key=lambda value: (value.casefold(), value),
    )
    nested = sum(identity.count("/") > root.count("/") + 1 for identity in identities)
    anomalies = []
    if len(extensions) > 1:
        anomalies.append("mixed file extensions")
    if nested and nested < len(identities):
        anomalies.append("mixed loose and nested descendants")
    return {
        "root": root,
        "material": question["material"],
        "descendant_count": len(candidates),
        "nested_descendant_count": nested,
        "extension_counts": dict(sorted(extensions.items())[:20]),
        "extension_counts_truncated": len(extensions) > 20,
        "immediate_children": immediate[:40],
        "immediate_child_count": len(immediate),
        "immediate_children_truncated": len(immediate) > 40,
        "sample_identities": identities[:20],
        "anomalies": anomalies,
    }


def subtree_fingerprint(
    entry: str,
    question: Mapping[str, Any],
    candidate_fingerprints: Mapping[str, str],
    compatibility: Mapping[str, Any],
) -> str:
    """Return membership-bound stale protection for one subtree question."""

    identities = [
        str(candidate["identity"]) for candidate in question.get("candidates", [])
    ]
    return _fingerprint(
        {
            "entry": entry,
            "material": question["material"],
            "root": question["root"],
            "members": {
                identity: candidate_fingerprints[identity] for identity in identities
            },
            "notes": sorted(compatibility.get("notes", [])),
            "validation_rules_version": compatibility.get("rules_version"),
            "adjudication_schema_version": compatibility.get("adjudication_schema"),
            "decision_schema_version": compatibility.get("decision_schema"),
        }
    )
