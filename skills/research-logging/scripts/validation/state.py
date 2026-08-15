"""Persisted validation-state contracts and decoding.

This module owns the structural boundary for ``validation-state.json``.
Callers may perform additional consistency checks, but they must decode the
record here before reading nested state fields.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Mapping, TypedDict, cast

from .compatibility import (
    decode_component_versions,
    decode_input_dependencies,
    decode_producer_binding,
)
from .contracts import ValidationToolError


class ValidationStateContractError(ValueError):
    """Raised when persisted validation state violates its structural contract."""


class CompletedDependency(TypedDict):
    """One material identity retained for incremental outcome invalidation."""

    path: str
    role: str
    identity: dict[str, Any]


class _CompletedCheckRequired(TypedDict):
    entry: str
    target: str
    check: str
    result: str
    dependencies: list[CompletedDependency]
    compatibility_identity: str
    graph_slice: dict[str, Any]
    rule_dependencies: dict[str, int]
    input_dependencies: list[dict[str, Any]]


class CompletedCheck(_CompletedCheckRequired, total=False):
    """One persisted validation outcome and its complete dependency snapshot."""

    resolution: dict[str, Any]
    findings: list[str]
    producer_bindings: list[dict[str, Any]]


class ValidationState(TypedDict):
    """Complete canonical ``validation-state.json`` record."""

    schema_version: int
    validation_rules_version: str
    component_versions: dict[str, int]
    input_projection_versions: dict[str, int]
    graph_contract_version: int
    local_snapshot_identity: str
    input_fingerprint: str
    input_files: dict[str, dict[str, Any]]
    mechanical_checks: dict[str, Any]
    directory_memberships: dict[str, dict[str, Any]]
    files: dict[str, dict[str, Any]]
    completed_checks: list[CompletedCheck]
    orphan_dispositions: list[dict[str, Any]]
    result: dict[str, Any]
    report: dict[str, Any]
    graph_identity: str


VALIDATION_STATE_KEYS = frozenset(ValidationState.__required_keys__)
_COMPLETED_CHECK_REQUIRED = {
    "entry",
    "target",
    "check",
    "result",
    "dependencies",
    "compatibility_identity",
    "graph_slice",
    "rule_dependencies",
    "input_dependencies",
}
_COMPLETED_CHECK_ALLOWED = _COMPLETED_CHECK_REQUIRED | {
    "resolution",
    "findings",
    "producer_bindings",
}
_DEPENDENCY_KEYS = {"path", "role", "identity"}
_HEX_IDENTITY = re.compile(r"[0-9a-f]{64}")
_FILE_IDENTITY_REQUIRED = {"size", "mtime_ns", "ctime_ns", "sha256"}
_FILE_IDENTITY_ALLOWED = _FILE_IDENTITY_REQUIRED | {"members"}
_DIRECTORY_IDENTITY_KEYS = {"members", "sha256"}
_GRAPH_SLICE_KEYS = {"identity", "nodes", "edges", "roots"}
_GRAPH_NODE_KEYS = {"namespace", "kind", "identity"}
_RESULT_KEYS = {
    "date",
    "mode",
    "requested_scope",
    "scope",
    "summary_rows",
    "summary_failed",
    "entry_rows",
    "entry_failed",
    "entries",
    "failed_entries",
    "failure_rows",
    "failures",
}


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationStateContractError(f"{description} must be an object")
    return value


def _require_string(value: Any, description: str) -> str:
    if not isinstance(value, str):
        raise ValidationStateContractError(f"{description} must be a string")
    return value


def _require_sha256(value: Any, description: str) -> str:
    value = _require_string(value, description)
    if _HEX_IDENTITY.fullmatch(value) is None:
        raise ValidationStateContractError(f"{description} must be a SHA-256 identity")
    return value


def _require_string_list(value: Any, description: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationStateContractError(f"{description} must be a list of strings")
    return value


def _decode_file_identity(value: Any, description: str) -> None:
    identity = _require_mapping(value, description)
    fields = set(identity)
    if identity == {"missing": True}:
        return
    if fields == {"error"} and isinstance(identity["error"], str):
        return
    if fields == _DIRECTORY_IDENTITY_KEYS:
        members = identity["members"]
        if not isinstance(members, int) or isinstance(members, bool) or members < 0:
            raise ValidationStateContractError(
                f"{description} members must be a nonnegative integer"
            )
        _require_sha256(identity["sha256"], f"{description} sha256")
        return
    if not _FILE_IDENTITY_REQUIRED <= fields <= _FILE_IDENTITY_ALLOWED:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    if (
        not isinstance(identity["size"], int)
        or isinstance(identity["size"], bool)
        or identity["size"] < 0
        or not isinstance(identity["mtime_ns"], int)
        or isinstance(identity["mtime_ns"], bool)
        or identity["mtime_ns"] < 0
        or not isinstance(identity["ctime_ns"], int)
        or isinstance(identity["ctime_ns"], bool)
        or identity["ctime_ns"] < 0
    ):
        raise ValidationStateContractError(
            f"{description} size, mtime_ns, and ctime_ns must be nonnegative integers"
        )
    _require_sha256(identity["sha256"], f"{description} sha256")
    if "members" in identity:
        members = _require_string_list(identity["members"], f"{description} members")
        if not members:
            raise ValidationStateContractError(
                f"{description} members must not be empty"
            )


def _decode_directory_identity(value: Any, description: str) -> None:
    identity = _require_mapping(value, description)
    if set(identity) == {"error"} and isinstance(identity["error"], str):
        return
    if set(identity) != _DIRECTORY_IDENTITY_KEYS:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    if (
        not isinstance(identity["members"], int)
        or isinstance(identity["members"], bool)
        or identity["members"] < 0
    ):
        raise ValidationStateContractError(
            f"{description} members must be a nonnegative integer"
        )
    _require_sha256(identity["sha256"], f"{description} sha256")


def _decode_identity_map(
    value: Any,
    description: str,
    decoder: Callable[[Any, str], None],
) -> None:
    identities = _require_mapping(value, description)
    for key, identity in identities.items():
        _require_string(key, f"{description} key")
        decoder(identity, f"{description} item {key!r}")


def _decode_graph_node(value: Any, description: str) -> None:
    node = _require_mapping(value, description)
    if set(node) != _GRAPH_NODE_KEYS:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    for field in _GRAPH_NODE_KEYS:
        _require_string(node[field], f"{description} {field}")


def _decode_graph_root(value: Any, description: str) -> None:
    root = _require_mapping(value, description)
    if set(root) != {"node", "policy"}:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    _decode_graph_node(root["node"], f"{description} node")
    _require_string(root["policy"], f"{description} policy")


def _decode_graph_slice(value: Any, description: str) -> None:
    graph_slice = _require_mapping(value, description)
    if set(graph_slice) != _GRAPH_SLICE_KEYS:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    _require_sha256(graph_slice["identity"], f"{description} identity")
    nodes = graph_slice["nodes"]
    edges = graph_slice["edges"]
    roots = graph_slice["roots"]
    if not all(isinstance(items, list) for items in (nodes, edges, roots)):
        raise ValidationStateContractError(f"{description} collections must be lists")
    for index, node in enumerate(nodes):
        _decode_graph_node(node, f"{description} node {index}")
    for index, edge in enumerate(edges):
        _require_sha256(edge, f"{description} edge {index}")
    for index, root in enumerate(roots):
        _decode_graph_root(root, f"{description} root {index}")


def _decode_resolution(value: Any, description: str) -> None:
    resolution = _require_mapping(value, description)
    fields = set(resolution)
    summary_fields = {"entry", "section", "lines"}
    producer_fields = {"producer_invocation"}
    producer_binding_fields = {"producer_invocation", "producer_bindings"}
    if frozenset(fields) not in {
        frozenset(summary_fields),
        frozenset(producer_fields),
        frozenset(producer_binding_fields),
    }:
        raise ValidationStateContractError(
            f"{description} must contain Summary support or producer fields"
        )
    if "producer_invocation" in resolution and not isinstance(
        resolution["producer_invocation"], str
    ):
        raise ValidationStateContractError(
            f"{description} producer_invocation must be text"
        )
    if fields == summary_fields and not all(
        isinstance(resolution[field], str) and resolution[field]
        for field in summary_fields
    ):
        raise ValidationStateContractError(
            f"{description} Summary support fields must be text"
        )
    if "producer_bindings" in resolution:
        bindings = resolution["producer_bindings"]
        if not isinstance(bindings, list) or not bindings or not all(
            isinstance(binding, dict)
            and set(binding) == {"material", "invocation"}
            and all(isinstance(item, str) and item for item in binding.values())
            for binding in bindings
        ) or len({binding["material"] for binding in bindings}) != len(bindings):
            raise ValidationStateContractError(
                f"{description} producer_bindings are invalid"
            )


def _decode_dependency(value: Any, check_index: int, index: int) -> None:
    dependency = _require_mapping(
        value, f"completed check {check_index} dependency {index}"
    )
    if set(dependency) != _DEPENDENCY_KEYS:
        raise ValidationStateContractError(
            f"completed check {check_index} dependency {index} has incorrect fields"
        )
    if not isinstance(dependency["path"], str) or not isinstance(
        dependency["role"], str
    ):
        raise ValidationStateContractError(
            f"completed check {check_index} dependency {index} path and role "
            "must be strings"
        )
    _decode_file_identity(
        dependency["identity"],
        f"completed check {check_index} dependency {index} identity",
    )


def _decode_native_completed_check(
    check: Mapping[str, Any], index: int
) -> None:
    try:
        decode_component_versions(
            check["rule_dependencies"],
            f"completed check {index} rule_dependencies",
        )
        decode_input_dependencies(
            check["input_dependencies"],
            f"completed check {index} input_dependencies",
            require_supported=False,
        )
        _require_sha256(
            check["compatibility_identity"],
            f"completed check {index} compatibility_identity",
        )
        bindings = check.get("producer_bindings", [])
        if not isinstance(bindings, list):
            raise ValidationToolError("producer_bindings must be a list")
        for binding_index, binding in enumerate(bindings):
            decode_producer_binding(
                binding,
                f"completed check {index} producer binding {binding_index}",
            )
    except ValidationToolError as exc:
        raise ValidationStateContractError(str(exc)) from exc


def _decode_completed_check(value: Any, index: int) -> None:
    check = _require_mapping(value, f"completed check {index}")
    if not _COMPLETED_CHECK_REQUIRED <= set(check) <= _COMPLETED_CHECK_ALLOWED:
        raise ValidationStateContractError(
            f"completed check {index} has incorrect fields"
        )
    for field in (
        "entry",
        "target",
        "check",
        "result",
        "compatibility_identity",
    ):
        if not isinstance(check[field], str):
            raise ValidationStateContractError(
                f"completed check {index} field {field!r} must be a string"
            )
    dependencies = check["dependencies"]
    if not isinstance(dependencies, list):
        raise ValidationStateContractError(
            f"completed check {index} dependencies must be a list"
        )
    for dependency_index, dependency in enumerate(dependencies):
        _decode_dependency(dependency, index, dependency_index)
    _decode_graph_slice(check["graph_slice"], f"completed check {index} graph slice")
    if "resolution" in check:
        _decode_resolution(check["resolution"], f"completed check {index} resolution")
    _decode_native_completed_check(check, index)
    findings = check.get("findings")
    if findings is not None and (
        not isinstance(findings, list)
        or not all(isinstance(finding, str) for finding in findings)
    ):
        raise ValidationStateContractError(
            f"completed check {index} findings must be a list of strings"
        )


def _decode_orphan_item(value: Any, description: str) -> None:
    item = _require_mapping(value, description)
    if set(item) != {"identity", "decision", "basis", "fingerprint"}:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    _require_string(item["identity"], f"{description} identity")
    decision = _require_string(item["decision"], f"{description} decision")
    if decision not in {"accepted", "unresolved"}:
        raise ValidationStateContractError(f"{description} decision is invalid")
    basis = _require_string(item["basis"], f"{description} basis")
    if decision == "accepted":
        if basis not in {"graph", "semantic-connection"} and re.fullmatch(
            r"validation-note:[0-9a-f]{64}", basis
        ) is None:
            raise ValidationStateContractError(f"{description} basis is invalid")
    elif basis != "-":
        raise ValidationStateContractError(
            f"{description} unresolved item cannot have a retention basis"
        )
    _require_sha256(item["fingerprint"], f"{description} fingerprint")


def _decode_orphan_dependency(value: Any, description: str) -> None:
    dependency = _require_mapping(value, description)
    if set(dependency) != {"path", "role"}:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    _require_string(dependency["path"], f"{description} path")
    _require_string(dependency["role"], f"{description} role")


def _decode_orphan_disposition(value: Any, description: str) -> None:
    disposition = _require_mapping(value, description)
    expected = {
        "inventory_version",
        "entry",
        "items",
        "dependencies",
        "rule_dependencies",
        "input_dependencies",
    }
    if set(disposition) != expected:
        raise ValidationStateContractError(f"{description} has incorrect fields")
    version = disposition["inventory_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValidationStateContractError(
            f"{description} inventory_version must be an integer"
        )
    _require_string(disposition["entry"], f"{description} entry")
    items = disposition["items"]
    dependencies = disposition["dependencies"]
    if not isinstance(items, list) or not isinstance(dependencies, list):
        raise ValidationStateContractError(
            f"{description} items and dependencies must be lists"
        )
    for index, item in enumerate(items):
        _decode_orphan_item(item, f"{description} item {index}")
    for index, dependency in enumerate(dependencies):
        _decode_orphan_dependency(dependency, f"{description} dependency {index}")
    try:
        decode_component_versions(
            disposition["rule_dependencies"], f"{description} rule_dependencies"
        )
        decode_input_dependencies(
            disposition["input_dependencies"],
            f"{description} input_dependencies",
            require_supported=False,
        )
    except ValidationToolError as exc:
        raise ValidationStateContractError(str(exc)) from exc


def _decode_orphan_dispositions(value: Any) -> None:
    if not isinstance(value, list):
        raise ValidationStateContractError(
            "validation state orphan_dispositions must be a list"
        )
    for index, disposition in enumerate(value):
        _decode_orphan_disposition(disposition, f"orphan disposition {index}")


def _decode_result(value: Any) -> None:
    result = _require_mapping(value, "validation state result")
    if set(result) != _RESULT_KEYS:
        raise ValidationStateContractError(
            "validation state result has incorrect fields"
        )
    for field in ("date", "mode", "requested_scope"):
        _require_string(result[field], f"validation state result {field}")
    scope = _require_mapping(result["scope"], "validation state result scope")
    if set(scope) != {"summary", "entries"} or not isinstance(scope["summary"], bool):
        raise ValidationStateContractError(
            "validation state result scope must contain summary and entries"
        )
    _require_string_list(scope["entries"], "validation state result scope entries")
    for field in (
        "summary_rows",
        "summary_failed",
        "entry_rows",
        "entry_failed",
        "entries",
        "failed_entries",
        "failure_rows",
    ):
        if (
            not isinstance(result[field], int)
            or isinstance(result[field], bool)
            or result[field] < 0
        ):
            raise ValidationStateContractError(
                f"validation state result {field} must be a nonnegative integer"
            )
    failures = result["failures"]
    if not isinstance(failures, list):
        raise ValidationStateContractError(
            "validation state result failures must be a list"
        )
    for index, raw_failure in enumerate(failures):
        description = f"validation state result failure {index}"
        failure = _require_mapping(raw_failure, description)
        if set(failure) != {"scope", "target", "checks"}:
            raise ValidationStateContractError(f"{description} has incorrect fields")
        _require_string(failure["scope"], f"{description} scope")
        _require_string(failure["target"], f"{description} target")
        _require_string_list(failure["checks"], f"{description} checks")


def _decode_report(value: Any) -> None:
    report = _require_mapping(value, "validation state report")
    if set(report) != {"size", "sha256"}:
        raise ValidationStateContractError(
            "validation state report has incorrect fields"
        )
    if (
        not isinstance(report["size"], int)
        or isinstance(report["size"], bool)
        or report["size"] < 0
    ):
        raise ValidationStateContractError(
            "validation state report size must be a nonnegative integer"
        )
    _require_sha256(report["sha256"], "validation state report sha256")


def _decode_native_state_contract(state: Mapping[str, Any]) -> None:
    try:
        decode_component_versions(
            state["component_versions"], "validation state component_versions"
        )
        decode_component_versions(
            state["input_projection_versions"],
            "validation state input_projection_versions",
        )
    except ValidationToolError as exc:
        raise ValidationStateContractError(str(exc)) from exc
    version = state["graph_contract_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationStateContractError(
            "validation state graph_contract_version is invalid"
        )


def _decode_validation_state(
    value: Any,
    *,
    schema_version: int,
) -> dict[str, Any]:
    """Decode one exact native validation-state record."""

    state = _require_mapping(value, "validation state")
    if set(state) != VALIDATION_STATE_KEYS:
        raise ValidationStateContractError(
            "validation state has incorrect top-level fields"
        )
    if state["schema_version"] != schema_version:
        raise ValidationStateContractError(
            "validation state uses an unsupported schema version"
        )
    _require_string(state["validation_rules_version"], "validation state rules version")
    for field in ("input_fingerprint", "graph_identity"):
        _require_sha256(state[field], f"validation state {field}")
    _require_sha256(
        state["local_snapshot_identity"],
        "validation state local_snapshot_identity",
    )
    _decode_native_state_contract(state)
    _decode_identity_map(
        state["input_files"],
        "validation state input_files",
        _decode_file_identity,
    )
    _decode_identity_map(
        state["files"],
        "validation state files",
        _decode_file_identity,
    )
    _decode_identity_map(
        state["directory_memberships"],
        "validation state directory_memberships",
        _decode_directory_identity,
    )
    mechanical_checks = _require_mapping(
        state["mechanical_checks"], "validation state mechanical_checks"
    )
    if not all(
        isinstance(key, str) and isinstance(item, Mapping)
        for key, item in mechanical_checks.items()
    ):
        raise ValidationStateContractError(
            "validation state mechanical_checks must map strings to objects"
        )
    _decode_result(state["result"])
    _decode_report(state["report"])
    checks = state["completed_checks"]
    if not isinstance(checks, list):
        raise ValidationStateContractError(
            "validation state completed_checks must be a list"
        )
    for index, check in enumerate(checks):
        _decode_completed_check(check, index)
    _decode_orphan_dispositions(state["orphan_dispositions"])
    return dict(value)


def decode_validation_state(
    value: Any,
    *,
    schema_version: int,
) -> ValidationState:
    """Decode one exact native validation-state record.

    The decoder establishes the nested container, scalar, and identity syntax
    required for safe incremental comparison. Cross-record relationships such
    as report-count agreement remain the record linter's responsibility.
    """

    decoded = _decode_validation_state(
        value,
        schema_version=schema_version,
    )
    return cast(ValidationState, decoded)
