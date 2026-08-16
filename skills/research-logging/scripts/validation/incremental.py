"""Native outcome reuse for one independently validated research log."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .compatibility import (
    COMPONENT_VERSIONS,
    components_compatible,
    input_dependencies_for_check,
    outcome_compatibility_identity,
    producer_bindings_for_check,
    semantic_projection,
)
from .contracts import ValidationToolError
from .identities import validation_file_identity
from .inventory import (
    collection_identity,
    content_identity,
    directory_membership_identity,
    display_path,
)

IdentityCache = Mapping[str, str]
DependencySnapshot = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]
OrphanFingerprints = Callable[
    [Mapping[str, Any], Mapping[str, Any], Optional[IdentityCache]], dict[str, str]
]


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dependency_identity_snapshot(
    scan: Mapping[str, Any], dependency: Mapping[str, Any]
) -> dict[str, Any]:
    """Identify one outcome dependency at its exact persisted member scope."""

    identity = str(dependency["path"])
    raw_path = scan.get("resolved_paths", {}).get(identity)
    if raw_path is None:
        candidate = Path(identity)
        raw_path = (
            candidate
            if candidate.is_absolute()
            else Path(str(scan["project_root"])) / candidate
        ).as_posix()
    path = Path(raw_path)
    if not path.exists():
        return {"missing": True}
    return _existing_dependency_identity(scan, dependency, identity, path)


def _existing_dependency_identity(
    scan: Mapping[str, Any],
    dependency: Mapping[str, Any],
    identity: str,
    path: Path,
) -> dict[str, Any]:
    """Identify one dependency already known to exist."""

    members = dependency.get("members")
    prior_identity = dependency.get("identity")
    if members is None and isinstance(prior_identity, Mapping):
        members = prior_identity.get("members")
    if path.is_dir():
        if isinstance(members, list):
            try:
                return collection_identity(path, members)
            except (OSError, ValidationToolError) as exc:
                return {"error": str(exc)}
        membership = scan.get("directory_memberships", {}).get(identity)
        if isinstance(membership, dict):
            return membership
        return directory_membership_identity(path)
    cached = scan.get("files", {}).get(identity)
    if isinstance(cached, dict):
        return cached
    return validation_file_identity(scan, identity, path)


def _resolved_identity_cache(scan: Mapping[str, Any]) -> dict[str, str]:
    return {
        Path(path).resolve().as_posix(): identity
        for identity, path in scan.get("resolved_paths", {}).items()
    }


def _identity_for_path(
    scan: Mapping[str, Any], raw: str, cache: IdentityCache
) -> str:
    resolved = Path(raw).resolve().as_posix()
    if resolved in cache:
        return cache[resolved]
    return display_path(Path(raw), Path(str(scan["project_root"])))


def orphan_item_fingerprints(
    entry: Mapping[str, Any],
    scan: Mapping[str, Any],
    identity_cache: Optional[IdentityCache] = None,
) -> dict[str, str]:
    """Fingerprint the minimum local material supporting each orphan decision."""

    files = scan.get("files", {})
    directories = scan.get("directory_memberships", {})
    mechanics = scan.get("mechanical_checks", {})
    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    command_scripts: dict[str, Any] = {}
    token_material: dict[str, list[Any]] = {}
    for command in entry.get("commands", []):
        raw_script = command.get("script")
        if raw_script:
            identity = _identity_for_path(scan, raw_script, identities)
            command_scripts[identity] = files.get(identity)
        for token in command.get("data_tokens", []):
            raw = token.get("path")
            if not raw:
                continue
            identity = _identity_for_path(scan, raw, identities)
            token_material.setdefault(token["name"], []).append(
                {
                    "identity": identity,
                    "material": files.get(identity)
                    or directories.get(identity)
                    or mechanics.get(identity),
                }
            )
    data_rows = {
        row.get("name"): row for row in entry.get("data_index", {}).get("rows", [])
    }
    result: dict[str, str] = {}
    for candidate in entry.get("orphan_inventory", []):
        identity = candidate["identity"]
        token_name = (
            identity[1:-1]
            if identity.startswith("<") and identity.endswith(">")
            else None
        )
        result[identity] = _json_fingerprint(
            {
                "candidate": candidate,
                "material": files.get(identity)
                or directories.get(identity)
                or mechanics.get(identity),
                "data_row": data_rows.get(token_name),
                "token_material": (
                    token_material.get(token_name, []) if token_name is not None else []
                ),
                "command_scripts": command_scripts,
                "validation_notes": entry.get("validation_notes", []),
            }
        )
    return result


@dataclass(frozen=True)
class IncrementalOperations:
    """Concrete local identity operations used by native outcome reuse."""

    dependency_snapshot: DependencySnapshot
    orphan_fingerprints: OrphanFingerprints


def _scope_content_map(
    items: list[dict[str, Any]],
) -> dict[tuple[Any, Any, Any, Any], Any]:
    return {
        (
            item.get("kind"),
            item.get("semantic_identity"),
            item.get("projection_version"),
            item.get("relationship"),
        ): item.get("content_identity")
        for item in items
    }


def _stored_dependency(
    dependency: Mapping[str, Any], previous_identity: Mapping[str, Any]
) -> dict[str, Any]:
    stored: dict[str, Any] = {
        "path": dependency["path"],
        "role": dependency["role"],
    }
    members = previous_identity.get("members")
    if isinstance(members, list):
        stored["members"] = members
    return stored


def _compare_dependencies(
    scan: dict[str, Any],
    outcome: Mapping[str, Any],
    operations: IncrementalOperations,
    snapshot_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    dependencies: list[dict[str, Any]] = []
    for dependency in outcome.get("dependencies", []):
        if not isinstance(dependency, Mapping):
            blockers.append("malformed-dependency")
            continue
        previous = dependency.get("identity")
        if not isinstance(previous, Mapping):
            blockers.append("malformed-dependency")
            continue
        members = previous.get("members", [])
        key = (
            str(dependency.get("path")),
            tuple(members) if isinstance(members, list) else (),
        )
        current = snapshot_cache.get(key)
        if current is None:
            try:
                current = operations.dependency_snapshot(scan, dependency)
            except (OSError, ValidationToolError) as exc:
                current = {"error": str(exc)}
            snapshot_cache[key] = current
        dependencies.append(
            {
                **_stored_dependency(dependency, previous),
                "identity": current,
            }
        )
        if content_identity(current) != content_identity(previous):
            blockers.append(f"dependency:{dependency.get('path')}")
    return dependencies, blockers


def _compare_rules(
    scan: Mapping[str, Any], outcome: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    rules = outcome.get("rule_dependencies")
    if not isinstance(rules, Mapping):
        blockers.append("malformed-rule-dependencies")
        rules = {}
    _, changed_rules = components_compatible(
        rules, scan.get("component_versions", COMPONENT_VERSIONS)
    )
    blockers.extend(f"rule:{name}" for name in changed_rules)
    return dict(rules), blockers


def _compare_projections(
    scan: dict[str, Any],
    outcome: Mapping[str, Any],
    dependencies: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    current_shape = {**outcome, "dependencies": dependencies}
    current_inputs = input_dependencies_for_check(scan, current_shape)
    prior_inputs = outcome.get("input_dependencies")
    if not isinstance(prior_inputs, list):
        blockers.append("malformed-input-dependencies")
        prior_inputs = []
    elif _scope_content_map(prior_inputs) != _scope_content_map(current_inputs):
        blockers.append("input-projection")

    prior_bindings = outcome.get("producer_bindings", [])
    if not isinstance(prior_bindings, list):
        blockers.append("malformed-producer-bindings")
        prior_bindings = []
    try:
        current_bindings = producer_bindings_for_check(scan, current_shape)
    except ValidationToolError:
        current_bindings = []
        blockers.append("producer-binding")
    if semantic_projection(prior_bindings) != semantic_projection(current_bindings):
        blockers.append("producer-binding")
    return current_inputs, current_bindings, blockers


def _compare_outcome(
    scan: dict[str, Any],
    outcome: Mapping[str, Any],
    operations: IncrementalOperations,
    snapshot_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
) -> dict[str, Any]:
    dependencies, blockers = _compare_dependencies(
        scan, outcome, operations, snapshot_cache
    )
    rules, rule_blockers = _compare_rules(scan, outcome)
    current_inputs, current_bindings, projection_blockers = _compare_projections(
        scan, outcome, dependencies
    )
    blockers.extend(rule_blockers)
    blockers.extend(projection_blockers)

    blockers = list(dict.fromkeys(blockers))
    return {
        **copy.deepcopy(dict(outcome)),
        "status": "reusable" if not blockers else "rerun",
        "changed_dependencies": blockers,
        "dependencies": dependencies,
        "rule_dependencies": rules,
        "input_dependencies": current_inputs,
        "compatibility_identity": outcome_compatibility_identity(
            rules, current_inputs, current_bindings
        ),
        **({"producer_bindings": current_bindings} if current_bindings else {}),
    }


def compare_prior_record(
    scan: dict[str, Any],
    record: Mapping[str, Any],
    operations: IncrementalOperations,
) -> dict[str, Any]:
    """Classify authoritative native outcomes against the current local scan."""

    outcomes = record.get("outcomes", [])
    if not isinstance(outcomes, list):
        return {"status": "invalid", "checks": []}
    snapshot_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    checks = [
        _compare_outcome(scan, outcome, operations, snapshot_cache)
        for outcome in outcomes
        if isinstance(outcome, Mapping)
    ]
    reusable = sum(check["status"] == "reusable" for check in checks)
    return {
        "status": "loaded",
        "checks": checks,
        "reusable_checks": reusable,
        "rerun_checks": len(checks) - reusable,
        "semantic_review_required": reusable != len(checks),
    }
