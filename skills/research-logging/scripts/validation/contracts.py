"""Typed lifecycle records shared by research-log validation tools.

The required bases describe the fields every stage must produce. Optional
extensions are limited to genuinely conditional scan and metrics data; the
contracts must not turn misspelled or omitted lifecycle fields into valid
records merely because the implementation uses dictionaries internally.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypedDict, cast

from .graph import GraphContractError, GraphEdge


class LifecycleRecordContractError(ValueError):
    """Raised when a scan or adjudication record violates its exact contract."""


class ValidationToolError(RuntimeError):
    """Raised when a validation-tool input violates its public contract."""


class FileChangedError(ValidationToolError):
    """Raised when a validation input changes during a guarded operation."""


class CanonicalRepositoryView(TypedDict):
    """Complete repository dependency view required by a canonical scan."""

    schema_version: str
    validation_rules_version: str
    scope: dict[str, Any]
    identity: str
    material_owners: dict[str, dict[str, str]]
    cross_log_sources: dict[str, dict[str, dict[str, Any]]]
    slices: dict[str, dict[str, Any]]
    graph_edges: list[dict[str, Any]]


class _ScanRecordRequired(TypedDict):
    schema_version: int
    validation_rules_version: str
    requested_mode: str
    summary: str
    log_root: str
    project_root: str
    entry_order: list[str]
    reconciliation: dict[str, Any]
    summary_items: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    evidence_records: dict[str, Any]
    bibtex: dict[str, Any]
    files: dict[str, Any]
    directory_memberships: dict[str, Any]
    resolved_paths: dict[str, str]
    mechanical_checks: dict[str, Any]
    script_inventory: list[str]
    script_dependency_graph: dict[str, list[str]]
    repository_dependencies: list[dict[str, Any]]
    repository_material_owners: dict[str, dict[str, str]]
    repository_cross_log_sources: dict[str, dict[str, dict[str, Any]]]
    repository_slices: dict[str, dict[str, Any]]
    repository_scope: dict[str, Any]
    repository_view_identity: str
    repository_graph_edges: list[dict[str, Any]]
    durable_record_identity: str
    input_fingerprint: str


class ScanRecord(_ScanRecordRequired, total=False):
    """Deterministic discovery output before semantic adjudication."""

    incremental: dict[str, Any]


class AdjudicationRecord(TypedDict):
    """Explicit mechanical and semantic decisions for one scan."""

    schema_version: int
    validation_rules_version: str
    log: str
    requested_scope: str
    scope: dict[str, Any]
    date: str
    mode: str
    summary: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    review_queue: list[dict[str, Any]]


class ValidationMetrics(TypedDict, total=False):
    """Noncanonical timing and count metrics returned by tool stages."""

    status: str
    elapsed_seconds: float
    entries: int
    inputs: int
    edges: int
    scripts: int
    scripts_parsed: int
    logs: int
    logs_rebuilt: int
    files_hashed: int
    bytes_hashed: int
    orphan_scopes: int
    summary_items: int
    candidate_targets: int
    tables: int
    fenced_blocks: int
    numeric_evidence: int
    evidence_rows: int
    evidence_errors: int
    section_errors: int
    experimental_sections: int
    synthesis_sections: int
    prose_sections: int
    files_identified: int
    repository_index_status: str
    repository_index_edges: int
    repository_dependencies: int
    repository_index_elapsed_seconds: float
    reusable_checks: int
    rerun_checks: int
    incremental_status: str
    semantic_review_required: bool
    cached_result: dict[str, Any]


class RenderCounts(TypedDict):
    """Counts returned after successful canonical publication."""

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


SCAN_RECORD_REQUIRED_KEYS = frozenset(_ScanRecordRequired.__required_keys__)
SCAN_RECORD_ALLOWED_KEYS = SCAN_RECORD_REQUIRED_KEYS | {"incremental"}
ADJUDICATION_RECORD_KEYS = frozenset(AdjudicationRecord.__required_keys__)
_HEX_IDENTITY = re.compile(r"[0-9a-f]{64}")
_VALIDATION_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REVIEW_KINDS = frozenset(
    {
        "collection_scope",
        "mechanical_failure",
        "orphan_candidates",
        "reproduction",
        "semantic_fallback",
        "semantic_provenance",
        "upstream_producer",
    }
)
_SCAN_SUMMARY_ITEM_KEYS = frozenset(
    {
        "base_selector",
        "context",
        "end_line",
        "identity",
        "kind",
        "line",
        "section",
        "selector",
        "text",
    }
)
_SCAN_ENTRY_REQUIRED_KEYS = frozenset(
    {
        "candidate_targets",
        "citations",
        "commands",
        "data_index",
        "evidence_record",
        "fenced_blocks",
        "headings",
        "id",
        "links",
        "numeric_evidence",
        "orphan_candidates",
        "orphan_inventory",
        "path",
        "presented_items",
        "section_errors",
        "sections",
        "tables",
        "title",
        "validation_notes",
    }
)
_SCAN_ENTRY_OPTIONAL_KEYS = frozenset(
    {"scope_kind", "scope_paths", "unresolved_citations"}
)
_HEADING_KEYS = frozenset({"level", "line", "text"})
_SECTION_KEYS = frozenset({"end_line", "errors", "index", "line", "section", "type"})
_LINK_REQUIRED_KEYS = frozenset(
    {
        "block_label",
        "image",
        "kind",
        "label",
        "line",
        "section",
        "section_type",
        "target",
    }
)
_LINK_OPTIONAL_KEYS = frozenset({"exists", "path"})
_TABLE_KEYS = frozenset(
    {"block_label", "identity", "line", "markdown", "rows", "section", "section_type"}
)
_FENCE_KEYS = frozenset(
    {
        "block_label",
        "identity",
        "kind",
        "language",
        "line",
        "section",
        "section_type",
        "text",
    }
)
_NUMERIC_KEYS = frozenset({"line", "section", "section_type", "text", "values"})
_CITATION_KEYS = frozenset({"key", "line", "section", "section_type"})
_VALIDATION_NOTE_REQUIRED_KEYS = frozenset({"line", "section", "sha256", "text"})
_VALIDATION_NOTE_OPTIONAL_KEYS = frozenset({"entry"})
_COMMAND_REQUIRED_KEYS = frozenset(
    {
        "command",
        "data_tokens",
        "line",
        "option_values",
        "options",
        "path_arguments",
        "script",
        "script_interface",
        "script_token",
        "section",
        "unknown_options",
    }
)
_COMMAND_OPTIONAL_KEYS = frozenset({"matlab_scripts"})
_CANDIDATE_REQUIRED_KEYS = frozenset(
    {
        "identity",
        "kind",
        "mechanical",
        "occurrences",
        "presented",
        "resolved_path",
        "sections",
    }
)
_CANDIDATE_OPTIONAL_KEYS = frozenset({"role_hints"})
_ORPHAN_KEYS = frozenset({"identity", "kind"})
_DATA_INDEX_REQUIRED_KEYS = frozenset({"path", "rows", "used_tokens"})
_DATA_INDEX_OPTIONAL_KEYS = frozenset({"duplicates", "errors", "unused_names"})
_EVIDENCE_RECORD_KEYS = frozenset(
    {"errors", "expected_path", "identity", "path", "rows"}
)
_EVIDENCE_ROW_REQUIRED_KEYS = frozenset(
    {
        "entry",
        "evidence",
        "kind",
        "line",
        "section",
        "source_specs",
        "sources",
        "transformation",
    }
)
_EVIDENCE_ROW_OPTIONAL_KEYS = frozenset({"presented_item", "resolved_sources"})
_ADJUDICATION_SUMMARY_KEYS = frozenset(
    {
        "dependencies",
        "entries",
        "findings",
        "item",
        "provenance",
        "sections",
        "source_item",
        "support_evidence",
        "support_reviewed",
    }
)
_ADJUDICATION_ENTRY_KEYS = frozenset(
    {
        "id",
        "orphan_items",
        "path",
        "scope_kind",
        "scope_paths",
        "scope_reconciled",
        "targets",
        "title",
    }
)
_ADJUDICATION_TARGET_REQUIRED_KEYS = frozenset(
    {
        "dependencies",
        "findings",
        "integrity",
        "notes",
        "provenance",
        "reproducibility",
        "sections",
        "target",
    }
)
_ADJUDICATION_TARGET_OPTIONAL_KEYS = frozenset(
    {
        "_failure_basis",
        "orphan_items",
        "producer_bindings",
        "producer_invocation",
    }
)
_REVIEW_ITEM_ALLOWED_KEYS = frozenset(
    {
        "candidates",
        "collections",
        "entry",
        "evidence",
        "hard_failures",
        "identity",
        "integrity",
        "integrity_status",
        "kind",
        "line",
        "producer_candidates",
        "reason",
        "section",
        "sections",
        "validation_notes",
        "workflow",
    }
)


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LifecycleRecordContractError(f"{description} must be an object")
    return value


def _mapping_list(value: Any, description: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise LifecycleRecordContractError(f"{description} must be a list of objects")
    return value


def _string_list(value: Any, description: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LifecycleRecordContractError(f"{description} must be a list of strings")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    description: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    fields = set(value)
    if not required <= fields <= required | optional:
        missing = sorted(required - fields)
        extra = sorted(fields - required - optional)
        raise LifecycleRecordContractError(
            f"{description} has incorrect fields: missing={missing!r}; extra={extra!r}"
        )


def _validate_string_fields(
    value: Mapping[str, Any], description: str, fields: tuple[str, ...]
) -> None:
    for field in fields:
        if not isinstance(value[field], str):
            raise LifecycleRecordContractError(
                f"{description} field {field!r} must be a string"
            )


def _validate_nullable_string(value: Any, description: str) -> None:
    if value is not None and not isinstance(value, str):
        raise LifecycleRecordContractError(f"{description} must be null or a string")


def _validate_boolean(value: Any, description: str) -> None:
    if not isinstance(value, bool):
        raise LifecycleRecordContractError(f"{description} must be a boolean")


def _validate_integer(value: Any, description: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise LifecycleRecordContractError(f"{description} must be an integer")


def _validate_file_identity(value: Any, description: str) -> None:
    identity = _mapping(value, description)
    fields = set(identity)
    if fields not in (
        {"size", "mtime_ns", "ctime_ns", "sha256"},
        {"size", "mtime_ns", "ctime_ns", "sha256", "members"},
    ):
        raise LifecycleRecordContractError(f"{description} has incorrect fields")
    _validate_integer(identity["size"], f"{description} size")
    _validate_integer(identity["mtime_ns"], f"{description} mtime_ns")
    _validate_integer(identity["ctime_ns"], f"{description} ctime_ns")
    if (
        identity["size"] < 0
        or identity["mtime_ns"] < 0
        or identity["ctime_ns"] < 0
        or not isinstance(identity["sha256"], str)
        or _HEX_IDENTITY.fullmatch(identity["sha256"]) is None
    ):
        raise LifecycleRecordContractError(f"{description} is invalid")
    if "members" in identity:
        _string_list(identity["members"], f"{description} members")


def _validate_file_identity_map(value: Any, description: str) -> None:
    identities = _mapping(value, description)
    for key, identity in identities.items():
        if not isinstance(key, str):
            raise LifecycleRecordContractError(f"{description} keys must be strings")
        _validate_file_identity(identity, f"{description} item {key!r}")


def _validate_directory_identity_map(value: Any, description: str) -> None:
    identities = _mapping(value, description)
    for key, raw_identity in identities.items():
        if not isinstance(key, str):
            raise LifecycleRecordContractError(f"{description} keys must be strings")
        identity = _mapping(raw_identity, f"{description} item {key!r}")
        if set(identity) == {"error"} and isinstance(identity["error"], str):
            continue
        if set(identity) != {"members", "sha256"}:
            raise LifecycleRecordContractError(
                f"{description} item {key!r} has incorrect fields"
            )
        _validate_integer(identity["members"], f"{description} item {key!r} members")
        if (
            identity["members"] < 0
            or not isinstance(identity["sha256"], str)
            or _HEX_IDENTITY.fullmatch(identity["sha256"]) is None
        ):
            raise LifecycleRecordContractError(f"{description} item {key!r} is invalid")


def _validate_dependency_rows(value: Any, description: str) -> None:
    for index, dependency in enumerate(_mapping_list(value, description)):
        _exact_fields(
            dependency,
            f"{description} item {index}",
            frozenset({"path", "role"}),
            frozenset({"members"}),
        )
        _validate_string_fields(
            dependency, f"{description} item {index}", ("path", "role")
        )
        if "members" in dependency:
            _string_list(dependency["members"], f"{description} item {index} members")


def _validate_finding_rows(value: Any, description: str) -> None:
    for index, finding in enumerate(_mapping_list(value, description)):
        _exact_fields(
            finding,
            f"{description} item {index}",
            frozenset({"check", "finding"}),
        )
        _validate_string_fields(
            finding, f"{description} item {index}", ("check", "finding")
        )


def _validate_orphan_rows(value: Any, description: str) -> None:
    for index, item in enumerate(_mapping_list(value, description)):
        _exact_fields(
            item,
            f"{description} item {index}",
            frozenset({"basis", "decision", "identity"}),
        )
        _validate_string_fields(
            item,
            f"{description} item {index}",
            ("basis", "decision", "identity"),
        )
        if item["decision"] not in {
            "accepted",
            "deferred",
            "pending",
            "unresolved",
        }:
            raise LifecycleRecordContractError(
                f"{description} item {index} has an invalid decision"
            )
        basis = item["basis"]
        if item["decision"] == "accepted":
            if (
                basis not in {"graph", "semantic-connection"}
                and re.fullmatch(r"validation-note:[0-9a-f]{64}", basis) is None
            ):
                raise LifecycleRecordContractError(
                    f"{description} item {index} has an invalid acceptance basis"
                )
        elif item["decision"] == "deferred":
            if basis != "cross-log-incomplete":
                raise LifecycleRecordContractError(
                    f"{description} item {index} has an invalid deferral basis"
                )
        elif basis != "-":
            raise LifecycleRecordContractError(
                f"{description} item {index} has a basis before acceptance"
            )


def _validate_result(value: Any, description: str, specials: frozenset[str]) -> None:
    if value is None:
        return
    if isinstance(value, str) and (
        value in specials or _VALIDATION_DATE.fullmatch(value) is not None
    ):
        return
    raise LifecycleRecordContractError(
        f"{description} must be null, a validation date, or one of {sorted(specials)!r}"
    )


def _validate_repository_slice_snapshots(value: Any) -> None:
    """Validate the exact slice identities bound into one scan record."""

    slices = _mapping(value, "scan repository_slices")
    for summary, raw_snapshot in slices.items():
        snapshot = _mapping(raw_snapshot, f"scan repository slice {summary!r}")
        if (
            not isinstance(summary, str)
            or set(snapshot)
            != {
                "path",
                "graph_identity",
                "source_identity",
                "local_snapshot_identity",
                "content_identity",
            }
            or not isinstance(snapshot["path"], str)
            or _HEX_IDENTITY.fullmatch(snapshot["graph_identity"]) is None
            or _HEX_IDENTITY.fullmatch(snapshot["source_identity"]) is None
            or _HEX_IDENTITY.fullmatch(snapshot["local_snapshot_identity"])
            is None
        ):
            raise LifecycleRecordContractError("scan repository_slices is invalid")
        identity = _mapping(
            snapshot["content_identity"],
            f"scan repository slice content identity {summary!r}",
        )
        if (
            set(identity) != {"size", "sha256"}
            or not isinstance(identity["size"], int)
            or isinstance(identity["size"], bool)
            or identity["size"] < 0
            or not isinstance(identity["sha256"], str)
            or _HEX_IDENTITY.fullmatch(identity["sha256"]) is None
        ):
            raise LifecycleRecordContractError("scan repository_slices is invalid")


def _validate_scan_scalars(record: Mapping[str, Any]) -> None:
    for field in (
        "validation_rules_version",
        "requested_mode",
        "summary",
        "log_root",
        "project_root",
        "durable_record_identity",
        "input_fingerprint",
        "repository_view_identity",
    ):
        if not isinstance(record[field], str):
            raise LifecycleRecordContractError(
                f"scan record field {field!r} must be a string"
            )
    for field in (
        "durable_record_identity",
        "input_fingerprint",
        "repository_view_identity",
    ):
        if _HEX_IDENTITY.fullmatch(record[field]) is None:
            raise LifecycleRecordContractError(
                f"scan record field {field!r} must be a SHA-256 identity"
            )


def _validate_scan_collections(record: Mapping[str, Any]) -> None:
    entry_order = _string_list(record["entry_order"], "scan entry_order")
    if len(entry_order) != len(set(entry_order)):
        raise LifecycleRecordContractError("scan entry_order contains duplicates")
    for field in (
        "summary_items",
        "entries",
        "repository_dependencies",
        "repository_graph_edges",
    ):
        _mapping_list(record[field], f"scan field {field!r}")
    for field in (
        "reconciliation",
        "evidence_records",
        "bibtex",
        "files",
        "directory_memberships",
        "resolved_paths",
        "mechanical_checks",
        "script_dependency_graph",
        "repository_material_owners",
        "repository_cross_log_sources",
        "repository_slices",
        "repository_scope",
    ):
        _mapping(record[field], f"scan field {field!r}")
    owners = _mapping(
        record["repository_material_owners"], "scan repository_material_owners"
    )
    if not all(
        isinstance(path, str)
        and path
        and isinstance(owner, Mapping)
        and set(owner) == {"namespace", "kind"}
        and isinstance(owner["namespace"], str)
        and owner["namespace"]
        and owner["kind"] in {"artifact", "collection", "script"}
        for path, owner in owners.items()
    ):
        raise LifecycleRecordContractError(
            "scan repository_material_owners must map paths to owner records"
        )
    sources = _mapping(
        record["repository_cross_log_sources"],
        "scan repository_cross_log_sources",
    )
    for summary, inputs in sources.items():
        if not isinstance(summary, str) or not isinstance(inputs, Mapping):
            raise LifecycleRecordContractError(
                "scan repository_cross_log_sources is invalid"
            )
        for path, raw_identity in inputs.items():
            identity = _mapping(
                raw_identity,
                f"scan repository cross-log source {summary!r} {path!r}",
            )
            if (
                not isinstance(path, str)
                or set(identity) != {"size", "sha256"}
                or not isinstance(identity["size"], int)
                or isinstance(identity["size"], bool)
                or identity["size"] < 0
                or not isinstance(identity["sha256"], str)
                or _HEX_IDENTITY.fullmatch(identity["sha256"]) is None
            ):
                raise LifecycleRecordContractError(
                    "scan repository_cross_log_sources is invalid"
                )
    _validate_repository_slice_snapshots(record["repository_slices"])
    scope = _mapping(record["repository_scope"], "scan repository_scope")
    if (
        set(scope)
        != {
            "kind",
            "expected_summaries",
            "refresh_summary",
            "cross_log_complete",
            "excluded_slices",
        }
        or scope.get("kind")
        not in {
            "complete",
            "replacement",
            "diagnostic",
        }
        or not isinstance(scope.get("expected_summaries"), list)
        or any(
            not isinstance(summary, str) for summary in scope["expected_summaries"]
        )
        or scope.get("refresh_summary") is not None
        and not isinstance(scope["refresh_summary"], str)
        or not isinstance(scope.get("cross_log_complete"), bool)
        or not isinstance(scope.get("excluded_slices"), Mapping)
        or any(
            not isinstance(summary, str)
            or not summary
            or not isinstance(reason, str)
            or not reason
            for summary, reason in scope["excluded_slices"].items()
        )
    ):
        raise LifecycleRecordContractError("scan repository_scope is invalid")
    _string_list(record["script_inventory"], "scan script_inventory")


def _validate_scan_summary_items(record: Mapping[str, Any]) -> None:
    for index, item in enumerate(record["summary_items"]):
        description = f"scan summary item {index}"
        _exact_fields(item, description, _SCAN_SUMMARY_ITEM_KEYS)
        _validate_string_fields(
            item,
            description,
            (
                "base_selector",
                "context",
                "identity",
                "kind",
                "section",
                "selector",
                "text",
            ),
        )
        if not isinstance(item["line"], int) or not isinstance(item["end_line"], int):
            raise LifecycleRecordContractError(
                f"{description} line range must contain integers"
            )


def _validate_heading_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _HEADING_KEYS)
        _validate_integer(row["level"], f"{row_description} level")
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(row, row_description, ("text",))


def _validate_section_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _SECTION_KEYS)
        for field in ("index", "line", "end_line"):
            _validate_integer(row[field], f"{row_description} {field}")
        _validate_string_fields(row, row_description, ("section", "type"))
        _string_list(row["errors"], f"{row_description} errors")


def _validate_link_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _LINK_REQUIRED_KEYS, _LINK_OPTIONAL_KEYS)
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(
            row,
            row_description,
            ("kind", "label", "section", "section_type", "target"),
        )
        _validate_nullable_string(row["block_label"], f"{row_description} block_label")
        _validate_boolean(row["image"], f"{row_description} image")
        if "path" in row:
            _validate_nullable_string(row["path"], f"{row_description} path")
        if "exists" in row and row["exists"] is not None:
            _validate_boolean(row["exists"], f"{row_description} exists")


def _validate_table_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _TABLE_KEYS)
        _validate_integer(row["line"], f"{row_description} line")
        _validate_integer(row["rows"], f"{row_description} rows")
        _validate_string_fields(
            row,
            row_description,
            ("identity", "markdown", "section", "section_type"),
        )
        _validate_nullable_string(row["block_label"], f"{row_description} block_label")


def _validate_fence_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _FENCE_KEYS)
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(
            row,
            row_description,
            ("identity", "kind", "language", "section", "section_type", "text"),
        )
        _validate_nullable_string(row["block_label"], f"{row_description} block_label")


def _validate_numeric_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _NUMERIC_KEYS)
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(
            row, row_description, ("section", "section_type", "text")
        )
        _string_list(row["values"], f"{row_description} values")


def _validate_citation_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _CITATION_KEYS)
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(
            row, row_description, ("key", "section", "section_type")
        )


def _validate_validation_notes(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(
            row,
            row_description,
            _VALIDATION_NOTE_REQUIRED_KEYS,
            _VALIDATION_NOTE_OPTIONAL_KEYS,
        )
        _validate_integer(row["line"], f"{row_description} line")
        _validate_string_fields(row, row_description, ("section", "sha256", "text"))
        if _HEX_IDENTITY.fullmatch(row["sha256"]) is None:
            raise LifecycleRecordContractError(
                f"{row_description} sha256 must be a SHA-256 identity"
            )
        if "entry" in row and not isinstance(row["entry"], str):
            raise LifecycleRecordContractError(
                f"{row_description} entry must be a string"
            )


def _validate_presented_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _SCAN_SUMMARY_ITEM_KEYS)
        _validate_string_fields(
            row,
            row_description,
            (
                "base_selector",
                "context",
                "identity",
                "kind",
                "section",
                "selector",
                "text",
            ),
        )
        _validate_integer(row["line"], f"{row_description} line")
        _validate_integer(row["end_line"], f"{row_description} end_line")


def _validate_orphan_inventory(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(row, row_description, _ORPHAN_KEYS)
        _validate_string_fields(row, row_description, ("identity", "kind"))


def _validate_option_values(value: Any, description: str) -> None:
    for index, item in enumerate(_mapping_list(value, description)):
        item_description = f"{description} item {index}"
        _exact_fields(item, item_description, frozenset({"option", "value"}))
        _validate_nullable_string(item["option"], f"{item_description} option")
        _validate_string_fields(item, item_description, ("value",))


def _validate_data_tokens(value: Any, description: str) -> None:
    for index, item in enumerate(_mapping_list(value, description)):
        item_description = f"{description} item {index}"
        _exact_fields(
            item,
            item_description,
            frozenset({"name", "status"}),
            frozenset({"exists", "kind", "path", "target"}),
        )
        _validate_string_fields(item, item_description, ("name", "status"))
        for field in ("kind", "path", "target"):
            if field in item:
                _validate_string_fields(item, item_description, (field,))
        if "exists" in item:
            _validate_boolean(item["exists"], f"{item_description} exists")


def _validate_path_arguments(value: Any, description: str) -> None:
    for index, item in enumerate(_mapping_list(value, description)):
        item_description = f"{description} item {index}"
        _exact_fields(
            item,
            item_description,
            frozenset({"exists", "option", "path", "raw", "role_hint"}),
            frozenset({"dependency_paths", "source"}),
        )
        _validate_boolean(item["exists"], f"{item_description} exists")
        _validate_nullable_string(item["option"], f"{item_description} option")
        _validate_string_fields(item, item_description, ("path", "raw", "role_hint"))
        if "dependency_paths" in item:
            _string_list(item["dependency_paths"], f"{item_description} dependencies")
        if "source" in item:
            _validate_string_fields(item, item_description, ("source",))


def _validate_command_row(row: Mapping[str, Any], description: str) -> None:
    if "error" in row:
        _exact_fields(
            row,
            description,
            frozenset({"command", "error", "line", "section"}),
        )
        _validate_integer(row["line"], f"{description} line")
        _validate_string_fields(row, description, ("command", "error", "section"))
        return
    _exact_fields(row, description, _COMMAND_REQUIRED_KEYS, _COMMAND_OPTIONAL_KEYS)
    _validate_integer(row["line"], f"{description} line")
    _validate_string_fields(row, description, ("command", "section"))
    for field in ("script", "script_token"):
        _validate_nullable_string(row[field], f"{description} {field}")
    if row["script_interface"] is not None:
        _mapping(row["script_interface"], f"{description} script_interface")
    for field in ("options", "unknown_options"):
        _string_list(row[field], f"{description} {field}")
    if "matlab_scripts" in row:
        _string_list(row["matlab_scripts"], f"{description} matlab_scripts")
    _validate_option_values(row["option_values"], f"{description} option_values")
    _validate_data_tokens(row["data_tokens"], f"{description} data_tokens")
    _validate_path_arguments(row["path_arguments"], f"{description} path_arguments")


def _validate_command_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        _validate_command_row(row, f"{description} item {index}")


def _validate_candidate_rows(value: Any, description: str) -> None:
    for index, row in enumerate(_mapping_list(value, description)):
        row_description = f"{description} item {index}"
        _exact_fields(
            row,
            row_description,
            _CANDIDATE_REQUIRED_KEYS,
            _CANDIDATE_OPTIONAL_KEYS,
        )
        _validate_string_fields(row, row_description, ("identity", "kind"))
        _validate_nullable_string(
            row["resolved_path"], f"{row_description} resolved_path"
        )
        _validate_boolean(row["presented"], f"{row_description} presented")
        _mapping(row["mechanical"], f"{row_description} mechanical")
        _string_list(row["sections"], f"{row_description} sections")
        if "role_hints" in row:
            _string_list(row["role_hints"], f"{row_description} role_hints")
        for occurrence_index, occurrence in enumerate(
            _mapping_list(row["occurrences"], f"{row_description} occurrences")
        ):
            occurrence_description = f"{row_description} occurrence {occurrence_index}"
            _exact_fields(
                occurrence,
                occurrence_description,
                frozenset({"label", "line"}),
                frozenset({"role_hint"}),
            )
            _validate_string_fields(occurrence, occurrence_description, ("label",))
            _validate_integer(occurrence["line"], f"{occurrence_description} line")
            if "role_hint" in occurrence and not isinstance(
                occurrence["role_hint"], str
            ):
                raise LifecycleRecordContractError(
                    f"{occurrence_description} role_hint must be a string"
                )


def _validate_data_index(value: Any, description: str) -> None:
    index = _mapping(value, description)
    _exact_fields(
        index,
        description,
        _DATA_INDEX_REQUIRED_KEYS,
        _DATA_INDEX_OPTIONAL_KEYS,
    )
    _validate_nullable_string(index["path"], f"{description} path")
    for field in ("used_tokens", "duplicates", "errors", "unused_names"):
        if field in index:
            _string_list(index[field], f"{description} {field}")
    for row_index, row in enumerate(
        _mapping_list(index["rows"], f"{description} rows")
    ):
        row_description = f"{description} row {row_index}"
        if not {"name", "type", "location"} <= set(row) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in row.items()
        ):
            raise LifecycleRecordContractError(
                f"{row_description} must contain string name, type, and location fields"
            )


def _validate_evidence_row(row: Mapping[str, Any], description: str) -> None:
    _exact_fields(
        row,
        description,
        _EVIDENCE_ROW_REQUIRED_KEYS,
        _EVIDENCE_ROW_OPTIONAL_KEYS,
    )
    _validate_string_fields(
        row,
        description,
        ("entry", "evidence", "kind", "line", "section", "sources", "transformation"),
    )
    for source_index, source in enumerate(
        _mapping_list(row["source_specs"], f"{description} source_specs")
    ):
        source_description = f"{description} source spec {source_index}"
        _exact_fields(
            source,
            source_description,
            frozenset({"locator", "source"}),
        )
        _validate_string_fields(source, source_description, ("locator", "source"))
    if "presented_item" in row:
        if row["presented_item"] is not None:
            _validate_presented_rows(
                [row["presented_item"]], f"{description} presented_item"
            )
    if "resolved_sources" in row:
        for source_index, source in enumerate(
            _mapping_list(row["resolved_sources"], f"{description} resolved_sources")
        ):
            source_description = f"{description} resolved source {source_index}"
            _exact_fields(
                source,
                source_description,
                frozenset({"identity", "locator", "path", "source", "status"}),
            )
            _validate_string_fields(
                source,
                source_description,
                ("identity", "locator", "source", "status"),
            )
            _validate_nullable_string(source["path"], f"{source_description} path")


def _validate_evidence_record(
    value: Any, description: str, *, summary: bool = False
) -> None:
    record = _mapping(value, description)
    _exact_fields(record, description, _EVIDENCE_RECORD_KEYS)
    for field in ("path", "identity", "expected_path"):
        _validate_nullable_string(record[field], f"{description} {field}")
    _string_list(record["errors"], f"{description} errors")
    for row_index, row in enumerate(
        _mapping_list(record["rows"], f"{description} rows")
    ):
        row_description = f"{description} row {row_index}"
        if summary:
            _exact_fields(
                row,
                row_description,
                frozenset({"entry", "line", "section", "statistic", "transformation"}),
            )
            _validate_string_fields(
                row,
                row_description,
                ("entry", "line", "section", "statistic", "transformation"),
            )
        else:
            _validate_evidence_row(row, row_description)


def _validate_scan_evidence_records(record: Mapping[str, Any]) -> None:
    evidence_records = _mapping(record["evidence_records"], "scan evidence_records")
    if set(evidence_records) != {"summary", "entry_folders"}:
        raise LifecycleRecordContractError("scan evidence_records has incorrect fields")
    _validate_evidence_record(
        evidence_records["summary"], "scan summary evidence record", summary=True
    )
    for index, entry_record in enumerate(
        _mapping_list(
            evidence_records["entry_folders"],
            "scan entry evidence records",
        )
    ):
        _validate_evidence_record(entry_record, f"scan entry evidence record {index}")


def _validate_scan_metadata(record: Mapping[str, Any]) -> None:
    reconciliation = _mapping(record["reconciliation"], "scan reconciliation")
    if set(reconciliation) != {"missing_entries", "unlisted_entries"}:
        raise LifecycleRecordContractError("scan reconciliation has incorrect fields")
    _string_list(reconciliation["missing_entries"], "scan missing_entries")
    _string_list(reconciliation["unlisted_entries"], "scan unlisted_entries")
    bibtex = _mapping(record["bibtex"], "scan bibtex")
    if set(bibtex) != {"keys", "path"}:
        raise LifecycleRecordContractError("scan bibtex has incorrect fields")
    _string_list(bibtex["keys"], "scan bibtex keys")
    _validate_nullable_string(bibtex["path"], "scan bibtex path")
    _validate_file_identity_map(record["files"], "scan files")
    _validate_directory_identity_map(
        record["directory_memberships"], "scan directory_memberships"
    )
    mechanics = _mapping(record["mechanical_checks"], "scan mechanical_checks")
    if not all(
        isinstance(key, str) and isinstance(item, Mapping)
        for key, item in mechanics.items()
    ):
        raise LifecycleRecordContractError(
            "scan mechanical_checks must map strings to objects"
        )
    for index, dependency in enumerate(record["repository_dependencies"]):
        description = f"scan repository dependency {index}"
        _exact_fields(
            dependency,
            description,
            frozenset({"consumer", "kind", "owner", "path", "source"}),
        )
        _validate_string_fields(
            dependency,
            description,
            ("consumer", "kind", "owner", "path", "source"),
        )
    for index, edge in enumerate(record["repository_graph_edges"]):
        try:
            GraphEdge.from_dict(edge)
        except GraphContractError as exc:
            raise LifecycleRecordContractError(
                f"scan repository graph edge {index} is invalid: {exc}"
            ) from exc


def _validate_scan_entries(record: Mapping[str, Any]) -> None:
    for index, entry in enumerate(record["entries"]):
        description = f"scan entry {index}"
        if "error" in entry:
            _exact_fields(
                entry,
                description,
                frozenset({"error", "exists", "id", "line", "path", "title"}),
            )
            _validate_string_fields(
                entry, description, ("error", "id", "path", "title")
            )
            _validate_boolean(entry["exists"], f"{description} exists")
            _validate_integer(entry["line"], f"{description} line")
            continue
        _exact_fields(
            entry,
            description,
            _SCAN_ENTRY_REQUIRED_KEYS,
            _SCAN_ENTRY_OPTIONAL_KEYS,
        )
        _validate_string_fields(entry, description, ("id", "path", "title"))
        _validate_heading_rows(entry["headings"], f"{description} headings")
        _validate_section_rows(entry["sections"], f"{description} sections")
        _validate_section_rows(entry["section_errors"], f"{description} section_errors")
        _validate_link_rows(entry["links"], f"{description} links")
        _validate_table_rows(entry["tables"], f"{description} tables")
        _validate_fence_rows(entry["fenced_blocks"], f"{description} fenced_blocks")
        _validate_numeric_rows(
            entry["numeric_evidence"], f"{description} numeric_evidence"
        )
        _validate_presented_rows(
            entry["presented_items"], f"{description} presented_items"
        )
        _validate_validation_notes(
            entry["validation_notes"], f"{description} validation_notes"
        )
        _validate_citation_rows(entry["citations"], f"{description} citations")
        _validate_command_rows(entry["commands"], f"{description} commands")
        _validate_data_index(entry["data_index"], f"{description} data_index")
        _validate_evidence_record(
            entry["evidence_record"], f"{description} evidence_record"
        )
        _validate_candidate_rows(
            entry["candidate_targets"], f"{description} candidate_targets"
        )
        _validate_orphan_inventory(
            entry["orphan_candidates"], f"{description} orphan_candidates"
        )
        _validate_orphan_inventory(
            entry["orphan_inventory"], f"{description} orphan_inventory"
        )
        if "unresolved_citations" in entry:
            _string_list(
                entry["unresolved_citations"],
                f"{description} field 'unresolved_citations'",
            )
        if "scope_kind" in entry and not isinstance(entry["scope_kind"], str):
            raise LifecycleRecordContractError(
                f"{description} field 'scope_kind' must be a string"
            )
        if "scope_paths" in entry:
            _string_list(entry["scope_paths"], f"{description} field 'scope_paths'")


def _validate_scan_path_maps(record: Mapping[str, Any]) -> None:
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in record["resolved_paths"].items()
    ):
        raise LifecycleRecordContractError(
            "scan resolved_paths must map strings to strings"
        )
    if not all(
        isinstance(key, str)
        and isinstance(items, list)
        and all(isinstance(item, str) for item in items)
        for key, items in record["script_dependency_graph"].items()
    ):
        raise LifecycleRecordContractError(
            "scan script_dependency_graph must map strings to string lists"
        )


def decode_scan_record(value: Any, *, schema_version: int) -> ScanRecord:
    """Decode one exact scan-stage lifecycle record."""

    record = _mapping(value, "scan record")
    if not SCAN_RECORD_REQUIRED_KEYS <= set(record) <= SCAN_RECORD_ALLOWED_KEYS:
        raise LifecycleRecordContractError("scan record has incorrect top-level fields")
    if record["schema_version"] != schema_version:
        raise LifecycleRecordContractError("scan record uses an unsupported schema")
    _validate_scan_scalars(record)
    _validate_scan_collections(record)
    _validate_scan_summary_items(record)
    _validate_scan_entries(record)
    _validate_scan_evidence_records(record)
    _validate_scan_metadata(record)
    _validate_scan_path_maps(record)
    if "incremental" in record:
        _mapping(record["incremental"], "scan incremental field")
    return cast(ScanRecord, value)


def _validate_adjudication_summary_rows(record: Mapping[str, Any]) -> None:
    summary_rows = _mapping_list(record["summary"], "adjudication field 'summary'")
    for index, row in enumerate(summary_rows):
        description = f"adjudication summary row {index}"
        _exact_fields(
            row,
            description,
            _ADJUDICATION_SUMMARY_KEYS,
            frozenset({"_failure_basis"}),
        )
        _validate_string_fields(row, description, ("item", "source_item"))
        _validate_result(
            row["provenance"], f"{description} provenance", frozenset({"FAIL"})
        )
        _string_list(row["entries"], f"{description} entries")
        _string_list(row["sections"], f"{description} sections")
        if not isinstance(row["support_reviewed"], bool):
            raise LifecycleRecordContractError(
                f"{description} support_reviewed must be a boolean"
            )
        _validate_dependency_rows(row["dependencies"], f"{description} dependencies")
        _validate_finding_rows(row["findings"], f"{description} findings")
        for evidence_index, evidence in enumerate(
            _mapping_list(row["support_evidence"], f"{description} support_evidence")
        ):
            evidence_description = (
                f"{description} support_evidence item {evidence_index}"
            )
            _exact_fields(
                evidence,
                evidence_description,
                frozenset({"entry", "lines", "section", "text"}),
            )
            _validate_string_fields(
                evidence,
                evidence_description,
                ("entry", "lines", "section", "text"),
            )
        if "_failure_basis" in row and not isinstance(row["_failure_basis"], str):
            raise LifecycleRecordContractError(
                f"{description} _failure_basis must be a string"
            )


def _validate_adjudication_target_rows(targets: Any, entry_description: str) -> None:
    for target_index, target in enumerate(
        _mapping_list(targets, f"{entry_description} targets")
    ):
        description = f"{entry_description} target {target_index}"
        _exact_fields(
            target,
            description,
            _ADJUDICATION_TARGET_REQUIRED_KEYS,
            _ADJUDICATION_TARGET_OPTIONAL_KEYS,
        )
        _validate_string_fields(target, description, ("notes", "target"))
        _validate_result(
            target["integrity"],
            f"{description} integrity",
            frozenset({"FAIL", "N/A"}),
        )
        _validate_result(
            target["provenance"],
            f"{description} provenance",
            frozenset({"FAIL", "N/A"}),
        )
        _validate_result(
            target["reproducibility"],
            f"{description} reproducibility",
            frozenset({"-", "FAIL", "N/A"}),
        )
        _string_list(target["sections"], f"{description} sections")
        _validate_dependency_rows(target["dependencies"], f"{description} dependencies")
        _validate_finding_rows(target["findings"], f"{description} findings")
        if "orphan_items" in target:
            _validate_orphan_rows(target["orphan_items"], f"{description} orphan_items")
        if "producer_invocation" in target and not isinstance(
            target["producer_invocation"], str
        ):
            raise LifecycleRecordContractError(
                f"{description} producer_invocation must be a string"
            )
        if "producer_bindings" in target:
            if "producer_invocation" not in target:
                raise LifecycleRecordContractError(
                    f"{description} producer_bindings require producer_invocation"
                )
            bindings = target["producer_bindings"]
            if not isinstance(bindings, list) or not bindings or not all(
                isinstance(binding, dict)
                and set(binding) == {"material", "invocation"}
                and all(
                    isinstance(value, str) and value
                    for value in binding.values()
                )
                for binding in bindings
            ) or len({binding["material"] for binding in bindings}) != len(bindings):
                raise LifecycleRecordContractError(
                    f"{description} producer_bindings must contain exact bindings"
                )
        if "_failure_basis" in target and not isinstance(target["_failure_basis"], str):
            raise LifecycleRecordContractError(
                f"{description} _failure_basis must be a string"
            )


def _validate_adjudication_entry_rows(record: Mapping[str, Any]) -> None:
    entry_rows = _mapping_list(record["entries"], "adjudication field 'entries'")
    for index, entry in enumerate(entry_rows):
        description = f"adjudication entry {index}"
        _exact_fields(entry, description, _ADJUDICATION_ENTRY_KEYS)
        _validate_string_fields(
            entry, description, ("id", "path", "scope_kind", "title")
        )
        if not isinstance(entry["scope_reconciled"], bool):
            raise LifecycleRecordContractError(
                f"{description} scope_reconciled must be a boolean"
            )
        _string_list(entry["scope_paths"], f"{description} scope_paths")
        _validate_orphan_rows(entry["orphan_items"], f"{description} orphan_items")
        _validate_adjudication_target_rows(entry["targets"], description)


def _validate_producer_candidates(value: Any, description: str) -> None:
    for index, candidate in enumerate(
        _mapping_list(value, f"{description} field 'producer_candidates'")
    ):
        candidate_description = f"{description} producer candidate {index}"
        _exact_fields(
            candidate,
            candidate_description,
            frozenset({"material", "invocation", "entry", "line", "command"}),
        )
        _validate_string_fields(
            candidate,
            candidate_description,
            ("material", "invocation", "entry", "command"),
        )
        _validate_integer(candidate["line"], f"{candidate_description} line")


def _validate_optional_review_fields(row: Mapping[str, Any], description: str) -> None:
    for field in ("integrity", "integrity_status", "reason", "section"):
        if field in row and not isinstance(row[field], str):
            raise LifecycleRecordContractError(
                f"{description} field {field!r} must be a string"
            )
    if "line" in row and not isinstance(row["line"], int):
        raise LifecycleRecordContractError(
            f"{description} field 'line' must be an integer"
        )
    for field in ("collections", "hard_failures", "sections"):
        if field in row:
            _string_list(row[field], f"{description} field {field!r}")
    hard_failures = row.get("hard_failures", [])
    if not set(hard_failures) <= {"Integrity", "Provenance"}:
        raise LifecycleRecordContractError(
            f"{description} hard_failures contains an unsupported check"
        )
    for field in ("candidates", "evidence", "validation_notes"):
        if field in row:
            _mapping_list(row[field], f"{description} field {field!r}")
    _validate_producer_candidates(row.get("producer_candidates", []), description)
    if "workflow" in row:
        _mapping(row["workflow"], f"{description} field 'workflow'")


def _validate_adjudication_review_rows(record: Mapping[str, Any]) -> None:
    review_rows = _mapping_list(
        record["review_queue"], "adjudication field 'review_queue'"
    )
    for index, row in enumerate(review_rows):
        description = f"adjudication review item {index}"
        _exact_fields(
            row,
            description,
            frozenset({"entry", "identity", "kind"}),
            _REVIEW_ITEM_ALLOWED_KEYS - {"entry", "identity", "kind"},
        )
        _validate_string_fields(row, description, ("entry", "identity", "kind"))
        if row["kind"] not in _REVIEW_KINDS:
            raise LifecycleRecordContractError(
                f"{description} has an unsupported review kind"
            )
        _validate_optional_review_fields(row, description)


def decode_adjudication_record(
    value: Any, *, schema_version: int
) -> AdjudicationRecord:
    """Decode one exact adjudication-stage lifecycle record."""

    record = _mapping(value, "adjudication record")
    if set(record) != ADJUDICATION_RECORD_KEYS:
        raise LifecycleRecordContractError(
            "adjudication record has incorrect top-level fields"
        )
    if record["schema_version"] != schema_version:
        raise LifecycleRecordContractError(
            "adjudication record uses an unsupported schema"
        )
    for field in (
        "validation_rules_version",
        "log",
        "requested_scope",
        "date",
        "mode",
    ):
        if not isinstance(record[field], str):
            raise LifecycleRecordContractError(
                f"adjudication field {field!r} must be a string"
            )
    scope = _mapping(record["scope"], "adjudication scope")
    if set(scope) != {"summary", "entries"} or not isinstance(scope["summary"], bool):
        raise LifecycleRecordContractError(
            "adjudication scope must contain summary and entries"
        )
    _string_list(scope["entries"], "adjudication scope entries")
    _validate_adjudication_summary_rows(record)
    _validate_adjudication_entry_rows(record)
    _validate_adjudication_review_rows(record)
    return cast(AdjudicationRecord, value)
