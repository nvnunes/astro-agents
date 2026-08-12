"""Typed incremental-comparison lifecycle for research-log validation.

This module owns reuse classification for completed outcomes. Discovery-specific
identity and graph-slice mechanics remain explicit operations supplied by the
scan facade; persisted state decoding and result assembly live here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, cast

from .adjudication import ORPHAN_TARGET, workflow_check
from .contracts import ScanRecord, ValidationToolError
from .graph import DependencyGraph
from .graph_adapter import build_dependency_graph
from .identities import validation_file_identity
from .inventory import (
    collection_identity,
    content_identity,
    directory_membership_identity,
    display_path,
)
from .state import (
    ValidationState,
    ValidationStateContractError,
    decode_validation_state,
)

SUCCESS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

IdentityCache = Mapping[str, str]
DependencyContract = Callable[
    [Mapping[str, Any], Mapping[str, Any], IdentityCache], str
]
DependencySnapshot = Callable[[Mapping[str, Any], Mapping[str, Any]], Dict[str, Any]]
GraphSlice = Callable[[DependencyGraph, Dict[str, Any]], Dict[str, Any]]
OrphanFingerprints = Callable[
    [Mapping[str, Any], Mapping[str, Any], IdentityCache], Dict[str, str]
]
ReportIdentity = Callable[[Path], Dict[str, Any]]


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _summary_check_dependency_contract(
    scan: Mapping[str, Any], target: str, check_name: str
) -> str:
    payload: Dict[str, Any] = {
        "entry": "Summary",
        "target": target,
        "check": check_name,
        "dependencies": [],
    }
    item = next(
        (
            item
            for item in scan.get("summary_items", [])
            if item.get("selector") == target
        ),
        None,
    )
    row = next(
        (
            row
            for row in scan.get("evidence_records", {})
            .get("summary", {})
            .get("rows", [])
            if row.get("statistic") == target
        ),
        None,
    )
    dependencies = [{"path": scan["summary"], "role": "summary"}]
    association = scan.get("evidence_records", {}).get("summary", {}).get("identity")
    if association:
        dependencies.append({"path": association, "role": "evidence-association"})
    if row:
        supporting = next(
            (
                entry
                for entry in scan.get("entries", [])
                if entry.get("id") == row.get("entry") and "error" not in entry
            ),
            None,
        )
        if supporting:
            dependencies.append(
                {"path": supporting["path"], "role": "supporting-entry"}
            )
    payload.update(
        {
            "item": (
                {"selector": item.get("selector"), "section": item.get("section")}
                if item
                else None
            ),
            "association": row,
            "dependencies": dependencies,
        }
    )
    return _json_fingerprint(payload)


def _entry_target_associations(
    entry: Mapping[str, Any], target: str
) -> List[Dict[str, Any]]:
    associations = []
    for row in entry.get("evidence_record", {}).get("rows", []):
        matched_sources = [
            {
                "identity": source.get("identity"),
                "locator": source.get("locator", ""),
                "status": source.get("status"),
            }
            for source in row.get("resolved_sources", [])
            if source.get("identity") == target
        ]
        if matched_sources:
            associations.append(
                {
                    "section": row.get("section"),
                    "kind": row.get("kind"),
                    "evidence": row.get("evidence"),
                    "sources": matched_sources,
                    "transformation": row.get("transformation", ""),
                    "presented_item": row.get("presented_item"),
                }
            )
    return associations


def _entry_check_dependency_contract(
    scan: Mapping[str, Any],
    entry_id: str,
    target: str,
    check_name: str,
    identity_cache: IdentityCache,
) -> str:
    payload: Dict[str, Any] = {
        "entry": entry_id,
        "target": target,
        "check": check_name,
        "dependencies": [],
    }
    entry = next(
        (
            entry
            for entry in scan.get("entries", [])
            if entry.get("id") == entry_id and "error" not in entry
        ),
        None,
    )
    if entry is None:
        payload["entry_missing"] = True
        return _json_fingerprint(payload)
    if target == ORPHAN_TARGET:
        payload["dependencies"] = [
            {"path": path, "role": "entry"}
            for path in entry.get("scope_paths", [entry["path"]])
        ]
        payload["orphan_inventory"] = entry.get("orphan_inventory", [])
        return _json_fingerprint(payload)

    dependencies = [{"path": entry["path"], "role": "entry"}]
    associations = _entry_target_associations(entry, target)
    target_present = bool(associations) or any(
        candidate.get("identity") == target
        for candidate in entry.get("candidate_targets", [])
    )
    if target_present:
        dependencies.append({"path": target, "role": "target"})
    association_identity = entry.get("evidence_record", {}).get("identity")
    if (
        check_name in {"Provenance", "Reproducibility"}
        and associations
        and association_identity
    ):
        dependencies.append(
            {"path": association_identity, "role": "evidence-association"}
        )
    workflow = None
    if check_name in {"Provenance", "Reproducibility"}:
        workflow, workflow_dependencies = workflow_check(
            entry, target, cast(ScanRecord, scan), identity_cache
        )
        dependencies.extend(workflow_dependencies)
    payload.update(
        {
            "associations": (
                associations if check_name in {"Provenance", "Reproducibility"} else []
            ),
            "dependencies": sorted(
                (
                    {"path": item["path"], "role": item["role"]}
                    for item in {
                        (dependency["path"], dependency["role"]): dependency
                        for dependency in dependencies
                    }.values()
                ),
                key=lambda item: (item["path"], item["role"]),
            ),
            "target_present": target_present,
            "target_directory_membership": scan.get("directory_memberships", {}).get(
                target
            ),
            "workflow": workflow,
        }
    )
    return _json_fingerprint(payload)


def current_check_dependency_contract(
    scan: Mapping[str, Any],
    check: Mapping[str, Any],
    identity_cache: Optional[IdentityCache] = None,
) -> str:
    """Fingerprint the currently discovered dependency surface for one outcome."""

    entry_id = cast(str, check.get("entry"))
    target = cast(str, check.get("target"))
    check_name = cast(str, check.get("check"))
    if entry_id == "Summary":
        return _summary_check_dependency_contract(scan, target, check_name)
    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    return _entry_check_dependency_contract(
        scan,
        entry_id,
        target,
        check_name,
        identities,
    )


def dependency_identity_snapshot(
    scan: Mapping[str, Any], dependency: Mapping[str, Any]
) -> Dict[str, Any]:
    """Identify one check dependency at its exact persisted member scope."""

    identity = dependency["path"]
    raw_path = scan.get("resolved_paths", {}).get(identity)
    if raw_path is None:
        candidate = Path(identity)
        raw_path = (
            candidate
            if candidate.is_absolute()
            else Path(scan["project_root"]) / candidate
        ).as_posix()
    path = Path(raw_path)
    if not path.exists():
        return {"missing": True}
    members = dependency.get("members")
    if members is None:
        members = dependency.get("identity", {}).get("members")
    if path.is_dir():
        if isinstance(members, list):
            return collection_identity(path, members)
        membership = scan.get("directory_memberships", {}).get(identity)
        if isinstance(membership, dict):
            return membership
        return directory_membership_identity(path)
    return scan.get("files", {}).get(identity) or validation_file_identity(
        scan, identity, path
    )


def _resolved_identity_cache(scan: Mapping[str, Any]) -> Dict[str, str]:
    """Build one resolved-path lookup for a complete incremental comparison."""

    return {
        Path(path).resolve().as_posix(): identity
        for identity, path in scan["resolved_paths"].items()
    }


def _identity_for_path(
    scan: Mapping[str, Any], raw: str, cache: IdentityCache
) -> str:
    resolved = Path(raw).resolve().as_posix()
    if resolved in cache:
        return cache[resolved]
    return display_path(Path(raw), Path(scan["project_root"]))


def orphan_item_fingerprints(
    entry: Mapping[str, Any],
    scan: Mapping[str, Any],
    identity_cache: Optional[IdentityCache] = None,
) -> Dict[str, str]:
    """Fingerprint the minimum material supporting each orphan disposition."""

    files = scan.get("files", {})
    directories = scan.get("directory_memberships", {})
    mechanics = scan.get("mechanical_checks", {})
    identities = (
        identity_cache
        if identity_cache is not None
        else _resolved_identity_cache(scan)
    )
    command_scripts = {}
    token_material: Dict[str, List[Any]] = {}
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
    result = {}
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
class IncrementalPolicy:
    """Version contract governing one incremental comparison.

    Attributes:
        state_schema_version: Exact persisted validation-state schema accepted.
        orphan_inventory_version: Exact item-fingerprint algorithm version reused.
    """

    state_schema_version: int
    orphan_inventory_version: int


@dataclass(frozen=True)
class IncrementalOperations:
    """Discovery-specific mechanics required by incremental comparison.

    Each operation identifies a concrete validator contract rather than a
    generic extension point. The incremental lifecycle never imports the scan
    facade or reaches into its private implementation.

    Attributes:
        dependency_contract: Fingerprint current discovery for one cached check.
        dependency_snapshot: Identify one persisted dependency at current scope.
        graph_slice: Project the dependency graph for one successful check.
        orphan_fingerprints: Fingerprint current item-level orphan evidence.
        report_identity: Identify the canonical human-readable validation report.
    """

    dependency_contract: DependencyContract
    dependency_snapshot: DependencySnapshot
    graph_slice: GraphSlice
    orphan_fingerprints: OrphanFingerprints
    report_identity: ReportIdentity


class _IncrementalCheckContext:
    """Current identities and lazy graph needed to validate cached outcomes."""

    def __init__(
        self,
        scan: Dict[str, Any],
        prior_state: ValidationState,
        input_unchanged: bool,
        operations: IncrementalOperations,
    ) -> None:
        self.scan = scan
        self.prior_checks = prior_state["completed_checks"]
        self.orphan_dispositions = prior_state["orphan_dispositions"]
        self.input_unchanged = input_unchanged
        self.operations = operations
        self.snapshot_cache: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        self.identity_cache = _resolved_identity_cache(scan)
        self.current_graph: Optional[DependencyGraph] = None

    def graph(self) -> DependencyGraph:
        """Build the reusable-outcome graph only when one check needs it."""

        if self.current_graph is None:
            graph_scan = copy.deepcopy(self.scan)
            graph_scan["incremental"] = {
                "checks": [
                    {
                        "entry": check.get("entry"),
                        "target": check.get("target"),
                        "check": check.get("check"),
                        "result": check.get("result"),
                        "status": "reusable",
                        "resolution": check.get("resolution"),
                        "dependencies": [
                            {
                                "path": dependency.get("path"),
                                "role": dependency.get("role"),
                                **(
                                    {"members": dependency["identity"]["members"]}
                                    if isinstance(dependency.get("identity"), dict)
                                    and isinstance(
                                        dependency["identity"].get("members"), list
                                    )
                                    else {}
                                ),
                            }
                            for dependency in check.get("dependencies", [])
                            if isinstance(dependency, dict)
                        ],
                    }
                    for check in self.prior_checks
                ],
                "orphan_dispositions": self.orphan_dispositions,
            }
            self.current_graph = build_dependency_graph(graph_scan)
        return self.current_graph

    def dependency_snapshot(
        self, dependency: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Return one current dependency identity through a member-aware cache."""

        previous_identity = dependency["identity"]
        members = previous_identity.get("members", [])
        cache_key = (
            dependency["path"],
            tuple(members) if isinstance(members, list) else (),
        )
        try:
            current = self.snapshot_cache.get(cache_key)
            if current is None:
                current = self.operations.dependency_snapshot(self.scan, dependency)
                self.snapshot_cache[cache_key] = current
            return current
        except (OSError, ValidationToolError):
            return None


def _stored_dependency(
    dependency: Mapping[str, Any], previous_identity: Mapping[str, Any]
) -> Dict[str, Any]:
    """Project one persisted dependency into adjudication dependency shape."""

    stored = {"path": dependency["path"], "role": dependency["role"]}
    if isinstance(previous_identity.get("members"), list):
        stored["members"] = previous_identity["members"]
    return stored


def _compare_completed_check(
    check: Mapping[str, Any], context: _IncrementalCheckContext
) -> Dict[str, Any]:
    """Classify one cached outcome from exact dependency and graph identities."""

    dependencies = check["dependencies"]
    blockers = []
    stored_dependencies = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not {
            "path",
            "role",
            "identity",
        } <= set(dependency):
            blockers.append("malformed-dependency-snapshot")
            continue
        previous_identity = dependency["identity"]
        current_identity = context.dependency_snapshot(dependency)
        if current_identity is None or content_identity(
            current_identity
        ) != content_identity(previous_identity):
            blockers.append(dependency["path"])
        stored_dependencies.append(_stored_dependency(dependency, previous_identity))
    current_contract = context.operations.dependency_contract(
        context.scan, check, context.identity_cache
    )
    if check["dependency_signature"] != current_contract:
        blockers.append("dependency-contract")
    prior_graph_slice = check.get("graph_slice")
    if (
        not blockers
        and not context.input_unchanged
        and isinstance(check.get("result"), str)
        and SUCCESS_DATE_RE.fullmatch(check["result"])
    ):
        if not isinstance(prior_graph_slice, dict):
            blockers.append("graph-slice")
        else:
            current_graph_slice = context.operations.graph_slice(
                context.graph(), {**check, "dependencies": stored_dependencies}
            )
            if current_graph_slice["identity"] != prior_graph_slice.get("identity"):
                blockers.append("graph-slice")
    blockers = list(dict.fromkeys(blockers))
    status = (
        "reusable"
        if not blockers and (dependencies or context.input_unchanged)
        else "rerun"
    )
    return {
        "entry": check.get("entry"),
        "target": check.get("target"),
        "check": check.get("check"),
        "result": check.get("result"),
        "status": status,
        "changed_dependencies": blockers,
        "dependency_signature": current_contract,
        "resolution": check.get("resolution"),
        "findings": check.get("findings", []),
        "dependencies": stored_dependencies,
    }


def _compare_cached_file_identities(
    scan: Dict[str, Any],
    prior_files: Mapping[str, Dict[str, Any]],
    operations: IncrementalOperations,
) -> Dict[str, Dict[str, Any]]:
    """Compare current material identities with one decoded state snapshot."""

    project_root = Path(scan["project_root"])
    comparisons: Dict[str, Dict[str, Any]] = {}
    for identity, previous in sorted(prior_files.items()):
        raw_path = scan["resolved_paths"].get(identity)
        if raw_path is None:
            candidate = Path(identity)
            raw_path = (
                candidate if candidate.is_absolute() else project_root / candidate
            ).as_posix()
            scan["resolved_paths"][identity] = raw_path
        path = Path(raw_path)
        try:
            if previous == {"missing": True}:
                current = {"missing": True} if not path.exists() else None
            elif path.is_dir():
                members = previous.get("members")
                if not isinstance(members, list) or not members:
                    comparisons[identity] = {
                        "status": "requires-refresh",
                        "detail": "prior collection identity lacks selected members",
                    }
                    continue
                current = operations.dependency_snapshot(
                    scan,
                    {"path": identity, "role": "cached", "identity": previous},
                )
            elif path.exists():
                current = operations.dependency_snapshot(
                    scan,
                    {"path": identity, "role": "cached", "identity": previous},
                )
            else:
                comparisons[identity] = {"status": "missing"}
                continue
        except (OSError, ValidationToolError) as exc:
            comparisons[identity] = {"status": "error", "detail": str(exc)}
            continue
        unchanged = (
            content_identity(current) == content_identity(previous)
            if isinstance(current, Mapping)
            else current == previous
        )
        comparisons[identity] = {
            "status": "unchanged" if unchanged else "changed",
            "current_identity": current,
        }
    return comparisons


def _compare_cached_directories(
    prior_directories: Mapping[str, Dict[str, Any]],
    current_directories: Mapping[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Compare complete deterministic directory-membership inventories."""

    comparisons = {}
    for identity in sorted(set(prior_directories) | set(current_directories)):
        previous = prior_directories.get(identity)
        current = current_directories.get(identity)
        if previous is None:
            status = "added"
        elif current is None:
            status = "removed"
        else:
            status = "unchanged" if current == previous else "changed"
        comparisons[identity] = {"status": status, "current_identity": current}
    return comparisons


def _validation_report_unchanged(
    scan: Mapping[str, Any],
    decoded: ValidationState,
    operations: IncrementalOperations,
) -> bool:
    """Return whether the canonical human-readable report is unchanged."""

    report_path = Path(scan["project_root"]) / scan["log_root"] / "validation.md"
    report_identity = decoded["report"]
    if not isinstance(report_identity, dict) or not report_path.is_file():
        return False
    try:
        return operations.report_identity(report_path) == report_identity
    except OSError:
        return False


def _current_orphan_scopes(
    scan: Mapping[str, Any],
    operations: IncrementalOperations,
    identity_cache: IdentityCache,
) -> Dict[str, Dict[str, Any]]:
    """Return current orphan identities and their decision fingerprints by entry."""

    return {
        entry["id"]: {
            "identities": sorted(
                item["identity"] for item in entry.get("orphan_inventory", [])
            ),
            "fingerprints": operations.orphan_fingerprints(
                entry, scan, identity_cache
            ),
        }
        for entry in scan.get("entries", [])
        if "error" not in entry
    }


def _reusable_orphan_dispositions(
    scan: Mapping[str, Any],
    decoded: ValidationState,
    comparisons: Mapping[str, Mapping[str, Any]],
    policy: IncrementalPolicy,
    operations: IncrementalOperations,
) -> List[Dict[str, Any]]:
    """Retain only unchanged item-level orphan decisions."""

    current_orphans = _current_orphan_scopes(
        scan, operations, _resolved_identity_cache(scan)
    )
    result = []
    for disposition in decoded["orphan_dispositions"]:
        if disposition.get("inventory_version") != policy.orphan_inventory_version:
            continue
        entry_id = cast(str, disposition.get("entry"))
        current_scope = current_orphans.get(
            entry_id, {"identities": [], "fingerprints": {}}
        )
        current_identities = set(cast(List[str], current_scope["identities"]))
        current_fingerprints = cast(Dict[str, str], current_scope["fingerprints"])
        dependency_paths = [
            item.get("path") for item in disposition.get("dependencies", [])
        ]
        blockers = [
            path
            for path in dependency_paths
            if path not in comparisons or comparisons[path]["status"] != "unchanged"
        ]
        reusable_items = [
            {
                "identity": item["identity"],
                "decision": item["decision"],
                "basis": item["basis"],
            }
            for item in disposition.get("items", [])
            if isinstance(item, dict)
            and item.get("identity") in current_identities
            and item.get("decision") in {"accepted", "unresolved"}
            and not blockers
            and item.get("fingerprint")
            == current_fingerprints.get(cast(str, item.get("identity")))
        ]
        reusable_identities = {item["identity"] for item in reusable_items}
        result.append(
            {
                "entry": entry_id,
                "inventory_version": policy.orphan_inventory_version,
                "items": reusable_items,
                "pending_candidates": sorted(current_identities - reusable_identities),
                "status": (
                    "reusable"
                    if current_identities and current_identities == reusable_identities
                    else "partial"
                ),
                "changed_dependencies": blockers,
            }
        )
    return result


def compare_prior_state(
    scan: Dict[str, Any],
    prior_state: Dict[str, Any],
    policy: IncrementalPolicy,
    operations: IncrementalOperations,
) -> Dict[str, Any]:
    """Compare a completed validation state with the current scan."""

    if prior_state.get("validation_rules_version") != scan["validation_rules_version"]:
        prior_checks = prior_state.get("completed_checks")
        if not isinstance(prior_checks, list):
            prior_checks = prior_state.get("successful_checks", [])
        if not isinstance(prior_checks, list):
            prior_checks = []
        return {
            "status": "rules-changed",
            "reusable_checks": 0,
            "rerun_checks": len(prior_checks),
        }
    try:
        decoded = decode_validation_state(
            prior_state,
            schema_version=policy.state_schema_version,
        )
    except ValidationStateContractError as exc:
        return {"status": "invalid", "detail": str(exc)}
    prior_files = decoded["files"]
    prior_checks = decoded["completed_checks"]
    prior_directories = decoded["directory_memberships"]
    prior_result = decoded["result"]

    comparisons = _compare_cached_file_identities(scan, prior_files, operations)
    current_directories = scan.get("directory_memberships", {})
    directory_comparisons = _compare_cached_directories(
        prior_directories,
        current_directories,
    )

    input_unchanged = decoded["input_fingerprint"] == scan.get("input_fingerprint")
    check_context = _IncrementalCheckContext(
        scan,
        decoded,
        input_unchanged,
        operations,
    )
    checks = [_compare_completed_check(check, check_context) for check in prior_checks]
    reusable = sum(check["status"] == "reusable" for check in checks)
    report_unchanged = _validation_report_unchanged(scan, decoded, operations)
    mode_compatible = (
        scan.get("requested_mode") == "standard"
        and prior_result.get("mode") == "standard"
    )
    outcomes_unchanged = (
        input_unchanged
        and mode_compatible
        and reusable == len(prior_checks)
        and all(
            item["status"] == "unchanged" for item in directory_comparisons.values()
        )
    )
    complete_unchanged = outcomes_unchanged and report_unchanged
    orphan_dispositions = _reusable_orphan_dispositions(
        scan,
        decoded,
        comparisons,
        policy,
        operations,
    )
    return {
        "status": "unchanged" if complete_unchanged else "loaded",
        "files": comparisons,
        "directories": directory_comparisons,
        "checks": checks,
        "reusable_checks": reusable,
        "rerun_checks": len(checks) - reusable,
        "input_unchanged": input_unchanged,
        "report_unchanged": report_unchanged,
        "semantic_review_required": not outcomes_unchanged,
        "cached_result": prior_result if complete_unchanged else None,
        "orphan_dispositions": orphan_dispositions,
    }
