"""Persistence contracts for independent research-log validation.

``validation-record.json`` is authoritative durable state.  It owns semantic
judgments, completed outcomes, failures, result dates, dependency contracts,
observed evidence identities, and continuation state.  ``validation-cache.json``
is disposable acceleration data: absence or corruption is always a cache miss.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .records import RecordPublicationError, _atomic_write_bytes, validation_lock

RECORD_FILENAME = "validation-record.json"
CACHE_FILENAME = "validation-cache.json"
RECORD_SCHEMA_VERSION = 2
NATIVE_V1_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
RETIRED_VALIDATION_FILENAMES = (
    "validation-decisions.json",
    "validation-state.json",
    "validation-index.json",
)


class TargetRecordError(ValueError):
    """Raised when authoritative target validation data violates its contract."""


def empty_record(summary: str, rules_version: str) -> dict[str, Any]:
    """Return a valid empty durable record for one maintained summary."""

    _validate_relative_path(summary, "summary")
    if not rules_version:
        raise TargetRecordError("durable record rules version must be nonempty")
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "summary": summary,
        "validation_rules_version": rules_version,
        "rule_dependencies": {
            "components": {},
            "input_projections": {},
        },
        "judgments": [],
        "outcomes": [],
        "result": None,
        "failures": [],
        "continuation": None,
        "completion_dependencies": [],
        "projection": None,
    }


def empty_cache() -> dict[str, Any]:
    """Return an empty rebuildable validation cache."""

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "files": {},
        "directories": {},
        "inspections": {},
        "local_indexes": {},
    }


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TargetRecordError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise TargetRecordError(f"{field} must be an array")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TargetRecordError(f"{field} must be a nonempty string")
    return value


def _validate_relative_path(value: Any, field: str) -> str:
    path = _nonempty_string(value, field)
    if "\\" in path:
        raise TargetRecordError(f"{field} must be a project-relative POSIX path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or path != pure.as_posix() or ".." in pure.parts:
        raise TargetRecordError(f"{field} must be a project-relative POSIX path")
    return path


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 identity of exact bytes."""

    return hashlib.sha256(value).hexdigest()


def projection_for(record: Mapping[str, Any], report_text: str) -> dict[str, str]:
    """Return the single identity and report hash for a complete projection."""

    projection_state = {
        "validation_rules_version": record["validation_rules_version"],
        "result": record["result"],
        "outcomes": record["outcomes"],
        "failures": record["failures"],
    }
    identity = sha256_bytes(
        json.dumps(
            projection_state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    return {
        "identity": identity,
        "report_sha256": sha256_bytes(report_text.encode("utf-8")),
    }


def _validate_identity(identity: Any, field: str) -> None:
    value = _mapping(identity, field)
    if value == {"missing": True}:
        return
    if set(value) == {"error"}:
        _nonempty_string(value["error"], f"{field}.error")
        return
    if set(value) == {"members", "sha256"}:
        members = value["members"]
        if isinstance(members, bool) or not isinstance(members, int) or members < 0:
            raise TargetRecordError(
                f"{field}.members must be a nonnegative integer"
            )
        _validate_sha256(value["sha256"], f"{field}.sha256")
        return
    required = {"size", "mtime_ns", "ctime_ns", "sha256"}
    if not required <= set(value) <= required | {"members"}:
        raise TargetRecordError(f"{field} has incorrect fields")
    if not isinstance(value["size"], int) or value["size"] < 0:
        raise TargetRecordError(f"{field}.size must be a nonnegative integer")
    for key in ("mtime_ns", "ctime_ns"):
        if not isinstance(value[key], int) or value[key] < 0:
            raise TargetRecordError(f"{field}.{key} must be a nonnegative integer")
    _validate_sha256(value["sha256"], f"{field}.sha256")
    if "members" in value and (
        not isinstance(value["members"], list)
        or not value["members"]
        or not all(isinstance(member, str) for member in value["members"])
    ):
        raise TargetRecordError(f"{field}.members must be a nonempty string array")


def _validate_sha256(sha256: Any, field: str) -> None:
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise TargetRecordError(f"{field} must be a lowercase SHA-256")


def _validate_dependencies(value: Any, field: str) -> None:
    for number, dependency in enumerate(_sequence(value, field)):
        item = _mapping(dependency, f"{field}[{number}]")
        _nonempty_string(item.get("path"), f"{field}[{number}].path")
        _nonempty_string(item.get("role"), f"{field}[{number}].role")
        _validate_identity(item.get("identity"), f"{field}[{number}].identity")


def _validate_input_dependencies(value: Any, field: str) -> None:
    for number, dependency in enumerate(_sequence(value, field)):
        item = _mapping(dependency, f"{field}[{number}]")
        for key in (
            "content_identity",
            "kind",
            "relationship",
            "semantic_identity",
        ):
            _nonempty_string(item.get(key), f"{field}[{number}].{key}")
        if not isinstance(item.get("projection_version"), int):
            raise TargetRecordError(
                f"{field}[{number}].projection_version must be an integer"
            )


def _validate_rule_dependencies(value: Any, field: str) -> None:
    for key, version in _mapping(value, field).items():
        _nonempty_string(key, f"{field} key")
        if not isinstance(version, int) or version < 1:
            raise TargetRecordError(f"{field}.{key} must be a positive integer")


def _validate_judgment(value: Any, number: int) -> None:
    field = f"judgments[{number}]"
    judgment = _mapping(value, field)
    for key in ("identity", "kind", "result", "decision_date"):
        _nonempty_string(judgment.get(key), f"{field}.{key}")
    _mapping(judgment.get("subject"), f"{field}.subject")
    _validate_rule_dependencies(
        judgment.get("rule_dependencies"), f"{field}.rule_dependencies"
    )
    _validate_input_dependencies(
        judgment.get("input_dependencies"), f"{field}.input_dependencies"
    )
    rationale = judgment.get("rationale")
    provenance = judgment.get("provenance")
    rationale_provenance = judgment.get("rationale_provenance")
    unavailable_legacy = (
        provenance == "legacy-attested"
        and rationale_provenance == "unavailable-in-v43"
        and rationale is None
    )
    preserved_legacy_findings = (
        provenance == "legacy-attested"
        and rationale_provenance == "recorded"
        and isinstance(rationale, list)
        and bool(rationale)
        and all(isinstance(item, str) and item.strip() for item in rationale)
    )
    if not unavailable_legacy and not preserved_legacy_findings:
        _nonempty_string(rationale, f"{field}.rationale")


def _validate_outcome(value: Any, number: int) -> None:
    field = f"outcomes[{number}]"
    outcome = _mapping(value, field)
    allowed = {
        "check",
        "compatibility_identity",
        "dependencies",
        "entry",
        "findings",
        "input_dependencies",
        "producer_bindings",
        "resolution",
        "result",
        "rule_dependencies",
        "target",
    }
    if not set(outcome) <= allowed:
        raise TargetRecordError(f"{field} contains unsupported fields")
    for key in ("compatibility_identity", "entry", "check", "target", "result"):
        _nonempty_string(outcome.get(key), f"{field}.{key}")
    _validate_dependencies(outcome.get("dependencies"), f"{field}.dependencies")
    _validate_input_dependencies(
        outcome.get("input_dependencies"), f"{field}.input_dependencies"
    )
    _validate_rule_dependencies(
        outcome.get("rule_dependencies"), f"{field}.rule_dependencies"
    )


def _validate_record_header(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise TargetRecordError(
            "unsupported validation-record schema; expected "
            f"{RECORD_SCHEMA_VERSION}, got {record.get('schema_version')!r}"
        )
    _validate_relative_path(record.get("summary"), "summary")
    _nonempty_string(
        record.get("validation_rules_version"), "validation_rules_version"
    )


def _validate_record_rule_dependencies(record: Mapping[str, Any]) -> None:
    rule_dependencies = _mapping(
        record.get("rule_dependencies"), "rule_dependencies"
    )
    if set(rule_dependencies) != {"components", "input_projections"}:
        raise TargetRecordError(
            "rule_dependencies must contain components and input_projections"
        )
    for key in ("components", "input_projections"):
        _validate_rule_dependencies(rule_dependencies[key], f"rule_dependencies.{key}")


def _validate_unique_record_rows(
    field: str, rows: Sequence[Any], identity_key: str
) -> None:
    identities = [row[identity_key] for row in rows]
    if len(identities) != len(set(identities)):
        raise TargetRecordError(f"{field} contains duplicate identities")


def _outcome_row_identity(outcome: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Identify one durable outcome without conflating compatible rows."""

    return (
        str(outcome["entry"]),
        str(outcome["target"]),
        str(outcome["check"]),
        str(outcome["compatibility_identity"]),
    )


def _validate_projection(value: Any) -> None:
    if value is None:
        return
    projection = _mapping(value, "projection")
    if set(projection) != {"identity", "report_sha256"}:
        raise TargetRecordError("projection has incorrect fields")
    _validate_sha256(projection.get("identity"), "projection.identity")
    _validate_sha256(
        projection.get("report_sha256"), "projection.report_sha256"
    )


def _validate_continuation(value: Any) -> None:
    if value is None:
        return
    continuation = _mapping(value, "continuation")
    kind = continuation.get("kind")
    if kind == "ordinary":
        if set(continuation) != {"kind", "identity", "item_count"}:
            raise TargetRecordError("ordinary continuation has incorrect fields")
        _validate_sha256(continuation.get("identity"), "continuation.identity")
        item_count = continuation.get("item_count")
        if (
            isinstance(item_count, bool)
            or not isinstance(item_count, int)
            or item_count < 1
        ):
            raise TargetRecordError(
                "continuation.item_count must be a positive integer"
            )
        return
    if kind == "paged":
        if set(continuation) != {
            "kind",
            "review_kind",
            "session",
            "session_identity",
        }:
            raise TargetRecordError("paged continuation has incorrect fields")
        _validate_relative_path(continuation.get("session"), "continuation.session")
        _validate_sha256(
            continuation.get("session_identity"),
            "continuation.session_identity",
        )
        _nonempty_string(
            continuation.get("review_kind"), "continuation.review_kind"
        )
        return
    raise TargetRecordError("continuation.kind must be ordinary or paged")


def _validate_record_rows(record: Mapping[str, Any]) -> None:
    _validate_record_rule_dependencies(record)
    judgments = _sequence(record.get("judgments"), "judgments")
    outcomes = _sequence(record.get("outcomes"), "outcomes")
    for number, judgment in enumerate(judgments):
        _validate_judgment(judgment, number)
    for number, outcome in enumerate(outcomes):
        _validate_outcome(outcome, number)
    _validate_unique_record_rows("judgments", judgments, "identity")
    outcome_identities = [_outcome_row_identity(outcome) for outcome in outcomes]
    if len(outcome_identities) != len(set(outcome_identities)):
        raise TargetRecordError("outcomes contains duplicate identities")
    failures = _sequence(record.get("failures"), "failures")
    for number, failure in enumerate(failures):
        _mapping(failure, f"failures[{number}]")
    result = record.get("result")
    if result is not None:
        _mapping(result, "result")


def decode_record(value: Any) -> dict[str, Any]:
    """Validate and copy one authoritative durable record."""

    record = _mapping(value, "durable record")
    expected_fields = {
        "schema_version",
        "summary",
        "validation_rules_version",
        "rule_dependencies",
        "judgments",
        "outcomes",
        "result",
        "failures",
        "continuation",
        "completion_dependencies",
        "projection",
    }
    if set(record) != expected_fields:
        raise TargetRecordError("durable record has incorrect fields")
    _validate_record_header(record)
    _validate_record_rows(record)
    _validate_dependencies(
        record.get("completion_dependencies"), "completion_dependencies"
    )
    _validate_projection(record.get("projection"))
    _validate_continuation(record.get("continuation"))
    return copy.deepcopy(dict(record))


def decode_cache(value: Any) -> dict[str, Any]:
    """Validate and copy rebuildable cache data."""

    cache = _mapping(value, "validation cache")
    if cache.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise TargetRecordError(
            "unsupported validation-cache schema; expected "
            f"{CACHE_SCHEMA_VERSION}, got {cache.get('schema_version')!r}"
        )
    for field in ("files", "directories", "inspections", "local_indexes"):
        _mapping(cache.get(field), field)
    for path, identity in cache["files"].items():
        _nonempty_string(path, "files key")
        _validate_identity(identity, f"files[{path!r}]")
    return copy.deepcopy(dict(cache))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TargetRecordError(f"cannot read valid JSON from {path}: {exc}") from exc


def _decode_native_v1(value: Any) -> dict[str, Any]:
    """Decode only the native schema deployed immediately before v2."""

    record = _mapping(value, "native-v1 durable record")
    expected_fields = {
        "schema_version",
        "summary",
        "validation_rules_version",
        "rule_dependencies",
        "judgments",
        "outcomes",
        "result",
        "failures",
        "continuation",
    }
    if set(record) != expected_fields:
        raise TargetRecordError("native-v1 durable record has incorrect fields")
    if record.get("schema_version") != NATIVE_V1_SCHEMA_VERSION:
        raise TargetRecordError("record is not the deployed native-v1 schema")
    _nonempty_string(record.get("summary"), "summary")
    _nonempty_string(
        record.get("validation_rules_version"), "validation_rules_version"
    )
    _validate_record_rows(record)
    continuation = record.get("continuation")
    if continuation is not None:
        _mapping(continuation, "continuation")
    return copy.deepcopy(dict(record))


def _native_v1_summary(
    raw_summary: str, expected_summary: str, project_root: Path
) -> str:
    expected = _validate_relative_path(expected_summary, "expected summary")
    raw_path = Path(raw_summary)
    expected_path = (project_root / expected).resolve()
    if raw_path.is_absolute():
        if raw_path.resolve() != expected_path:
            raise TargetRecordError(
                "durable validation record belongs to another summary"
            )
        return expected
    normalized = _validate_relative_path(raw_summary, "summary")
    if normalized != expected:
        raise TargetRecordError("durable validation record belongs to another summary")
    return normalized


def _native_v1_completion_dependencies(
    record: Mapping[str, Any], summary: str
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for outcome in record.get("outcomes", []):
        for dependency in outcome.get("dependencies", []):
            if (
                dependency.get("path") == summary
                and dependency.get("role") == "summary"
                and dependency not in dependencies
            ):
                dependencies.append(copy.deepcopy(dependency))
    return dependencies


def upgrade_native_v1(
    value: Any, expected_summary: str, project_root: Path
) -> dict[str, Any]:
    """Convert the exact deployed native-v1 record without publishing it."""

    old = _decode_native_v1(value)
    summary = _native_v1_summary(
        str(old["summary"]), expected_summary, project_root
    )
    continuation = old.get("continuation")
    if continuation is not None:
        if set(continuation) != {"identity", "item_count"}:
            raise TargetRecordError(
                "active native-v1 continuation cannot be converted safely"
            )
        continuation = {"kind": "ordinary", **continuation}
    converted = {
        **old,
        "schema_version": RECORD_SCHEMA_VERSION,
        "summary": summary,
        "continuation": continuation,
        "completion_dependencies": _native_v1_completion_dependencies(
            old, summary
        ),
        "projection": None,
    }
    return decode_record(converted)


def load_record_with_source(
    path: Path,
    *,
    expected_summary: str | None = None,
    project_root: Path | None = None,
    allow_native_v1: bool = True,
) -> tuple[dict[str, Any], int]:
    """Load one record and report the native schema found on disk."""

    try:
        value = _read_json(path)
    except FileNotFoundError as exc:
        raise TargetRecordError(
            f"durable validation record is missing: {path}"
        ) from exc
    raw = _mapping(value, "durable record")
    source_version = raw.get("schema_version")
    if source_version == RECORD_SCHEMA_VERSION:
        record = decode_record(raw)
        if expected_summary is not None and record["summary"] != expected_summary:
            raise TargetRecordError(
                "durable validation record belongs to another summary"
            )
        return record, RECORD_SCHEMA_VERSION
    if source_version == NATIVE_V1_SCHEMA_VERSION and allow_native_v1:
        if expected_summary is None or project_root is None:
            raise TargetRecordError(
                "native-v1 loading requires the requested summary and project root"
            )
        return (
            upgrade_native_v1(raw, expected_summary, project_root),
            NATIVE_V1_SCHEMA_VERSION,
        )
    raise TargetRecordError(
        "unsupported validation-record schema; expected "
        f"{RECORD_SCHEMA_VERSION}, got {source_version!r}"
    )


def load_record(
    path: Path,
    *,
    expected_summary: str | None = None,
    project_root: Path | None = None,
    allow_native_v1: bool = True,
) -> dict[str, Any]:
    """Load authoritative state, failing actionably on malformed ownership."""

    record, _ = load_record_with_source(
        path,
        expected_summary=expected_summary,
        project_root=project_root,
        allow_native_v1=allow_native_v1,
    )
    return record


def load_cache(path: Path) -> tuple[dict[str, Any], str]:
    """Load cache data or return an empty recomputation case and diagnostic."""

    try:
        return decode_cache(_read_json(path)), "loaded"
    except FileNotFoundError:
        return empty_cache(), "missing"
    except TargetRecordError:
        return empty_cache(), "malformed"


def assert_no_retired_artifacts(output_dir: Path) -> None:
    """Reject obsolete generated formats before current validation starts."""

    present = [
        name
        for name in RETIRED_VALIDATION_FILENAMES
        if (output_dir / name).exists()
    ]
    if present:
        raise TargetRecordError(
            "retired validation artifacts remain: "
            + ", ".join(present)
            + "; migrate them with a pre-transition astro-agents checkout "
            "before using the current validator"
        )


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def write_record_and_cache(
    output_dir: Path, record: Mapping[str, Any], cache: Mapping[str, Any]
) -> None:
    """Atomically write target state under the per-log validation lock.

    Cache is published first because it has no correctness authority.  The
    authoritative record is the commit point: a failure before or during that
    write leaves the prior valid durable record intact.
    """

    valid_record = decode_record(record)
    valid_cache = decode_cache(cache)
    try:
        with validation_lock(output_dir):
            _atomic_write_bytes(output_dir / CACHE_FILENAME, _json_bytes(valid_cache))
            _atomic_write_bytes(
                output_dir / RECORD_FILENAME, _json_bytes(valid_record)
            )
    except RecordPublicationError:
        raise
    except OSError as exc:
        raise RecordPublicationError(
            f"target validation state could not be written: {exc}"
        ) from exc


def publish_target_bundle(
    output_dir: Path,
    report_text: str,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
) -> None:
    """Publish target files with the report as the final commit point.

    Progressive durable state and disposable cache are written first.  Every
    individual replacement is atomic, so a failure leaves the prior completed
    report intact and retains every authoritative record write that succeeded.
    """

    valid_record = decode_record(record)
    valid_cache = decode_cache(cache)
    if not report_text.endswith("\n"):
        raise TargetRecordError("validation report must end with a newline")
    if output_dir.is_symlink():
        raise RecordPublicationError(
            "target validation output directory must not be a symlink"
        )
    for name in (CACHE_FILENAME, RECORD_FILENAME, "validation.md"):
        if (output_dir / name).is_symlink():
            raise RecordPublicationError(
                f"target validation destination must not be a symlink: {name}"
            )
    assert_no_retired_artifacts(output_dir)
    try:
        with validation_lock(output_dir):
            _atomic_write_bytes(output_dir / CACHE_FILENAME, _json_bytes(valid_cache))
            _atomic_write_bytes(
                output_dir / RECORD_FILENAME, _json_bytes(valid_record)
            )
            _atomic_write_bytes(output_dir / "validation.md", report_text.encode())
    except RecordPublicationError:
        raise
    except OSError as exc:
        raise RecordPublicationError(
            f"target validation bundle could not be written: {exc}"
        ) from exc
