"""Persistence contracts for independent research-log validation.

``validation-record.json`` is authoritative durable state.  It owns semantic
judgments, completed outcomes, failures, result dates, dependency contracts,
observed evidence identities, and continuation state.  ``validation-cache.json``
is disposable acceleration data: absence or corruption is always a cache miss.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .records import RecordPublicationError, _atomic_write_bytes, validation_lock

RECORD_FILENAME = "validation-record.json"
CACHE_FILENAME = "validation-cache.json"
RECORD_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
V45_RULES_VERSION = "research-log-validation-v45"
V45_DECISION_SCHEMA_VERSION = 2
V45_STATE_SCHEMA_VERSION = 11
V45_INDEX_SCHEMA_VERSION = 8


class TargetRecordError(ValueError):
    """Raised when authoritative target validation data violates its contract."""


def empty_record(summary: str, rules_version: str) -> dict[str, Any]:
    """Return a valid empty durable record for one maintained summary."""

    if not summary:
        raise TargetRecordError("durable record summary must be a nonempty path")
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


def _validate_identity(identity: Any, field: str) -> None:
    value = _mapping(identity, field)
    for key in ("size", "mtime_ns", "ctime_ns", "sha256"):
        if key not in value:
            raise TargetRecordError(f"{field}.{key} is required")
    if not isinstance(value["size"], int) or value["size"] < 0:
        raise TargetRecordError(f"{field}.size must be a nonnegative integer")
    for key in ("mtime_ns", "ctime_ns"):
        if not isinstance(value[key], int) or value[key] < 0:
            raise TargetRecordError(f"{field}.{key} must be a nonnegative integer")
    sha256 = value["sha256"]
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise TargetRecordError(f"{field}.sha256 must be a lowercase SHA-256")


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
    if not unavailable_legacy:
        _nonempty_string(rationale, f"{field}.rationale")


def _validate_outcome(value: Any, number: int) -> None:
    field = f"outcomes[{number}]"
    outcome = _mapping(value, field)
    for key in ("compatibility_identity", "entry", "check", "target", "result"):
        _nonempty_string(outcome.get(key), f"{field}.{key}")
    _validate_dependencies(outcome.get("dependencies"), f"{field}.dependencies")
    _validate_input_dependencies(
        outcome.get("input_dependencies"), f"{field}.input_dependencies"
    )
    _validate_rule_dependencies(
        outcome.get("rule_dependencies"), f"{field}.rule_dependencies"
    )
    if "graph_slice" in outcome:
        raise TargetRecordError(f"{field}.graph_slice has no target-format owner")


def _validate_record_header(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise TargetRecordError(
            "unsupported validation-record schema; expected "
            f"{RECORD_SCHEMA_VERSION}, got {record.get('schema_version')!r}"
        )
    _nonempty_string(record.get("summary"), "summary")
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


def decode_record(value: Any) -> dict[str, Any]:
    """Validate and copy one authoritative durable record."""

    record = _mapping(value, "durable record")
    _validate_record_header(record)
    _validate_record_rule_dependencies(record)
    judgments = _sequence(record.get("judgments"), "judgments")
    outcomes = _sequence(record.get("outcomes"), "outcomes")
    for number, judgment in enumerate(judgments):
        _validate_judgment(judgment, number)
    for number, outcome in enumerate(outcomes):
        _validate_outcome(outcome, number)
    for field, rows, identity_key in (
        ("judgments", judgments, "identity"),
        ("outcomes", outcomes, "compatibility_identity"),
    ):
        _validate_unique_record_rows(field, rows, identity_key)
    failures = _sequence(record.get("failures"), "failures")
    for number, failure in enumerate(failures):
        _mapping(failure, f"failures[{number}]")
    result = record.get("result")
    if result is not None:
        _mapping(result, "result")
    continuation = record.get("continuation")
    if continuation is not None:
        _mapping(continuation, "continuation")
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


def load_record(path: Path) -> dict[str, Any]:
    """Load authoritative state, failing actionably on any malformed field."""

    try:
        value = _read_json(path)
    except FileNotFoundError as exc:
        raise TargetRecordError(
            f"durable validation record is missing: {path}"
        ) from exc
    return decode_record(value)


def load_cache(path: Path) -> tuple[dict[str, Any], str]:
    """Load cache data or return an empty recomputation case and diagnostic."""

    try:
        return decode_cache(_read_json(path)), "loaded"
    except FileNotFoundError:
        return empty_cache(), "missing"
    except TargetRecordError:
        return empty_cache(), "malformed"


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


def _v45_document(path: Path, schema_version: int) -> dict[str, Any]:
    try:
        document = _mapping(_read_json(path), path.name)
    except FileNotFoundError as exc:
        raise TargetRecordError(f"v45 migration input is missing: {path}") from exc
    rules = document.get("validation_rules_version")
    schema = document.get("schema_version")
    if rules != V45_RULES_VERSION or schema != schema_version:
        raise TargetRecordError(
            f"unsupported pre-v45 validation format in {path}; expected "
            f"{V45_RULES_VERSION} schema {schema_version}, got rules={rules!r} "
            f"schema={schema!r}; validate with the last v45 tool before migration"
        )
    return copy.deepcopy(dict(document))


def import_v45(output_dir: Path, summary: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Import the deployed v45 triad without opening research evidence.

    The importer reads only the three validation-owned JSON records.  Its graph
    slice is accepted as migration input for local indexes and deliberately is
    not reproduced in the durable target record.
    """

    decisions = _v45_document(
        output_dir / "validation-decisions.json", V45_DECISION_SCHEMA_VERSION
    )
    state = _v45_document(
        output_dir / "validation-state.json", V45_STATE_SCHEMA_VERSION
    )
    graph_slice = _v45_document(
        output_dir / "validation-index.json", V45_INDEX_SCHEMA_VERSION
    )

    record = empty_record(summary, V45_RULES_VERSION)
    record["rule_dependencies"] = {
        "components": copy.deepcopy(state.get("component_versions", {})),
        "input_projections": copy.deepcopy(
            state.get("input_projection_versions", {})
        ),
    }
    judgments = copy.deepcopy(decisions.get("judgments", []))
    outcomes = []
    for imported in state.get("completed_checks", []):
        outcome = copy.deepcopy(imported)
        outcome.pop("graph_slice", None)
        outcomes.append(outcome)
    record["judgments"] = list(
        {judgment["identity"]: judgment for judgment in judgments}.values()
    )
    record["outcomes"] = list(
        {
            outcome["compatibility_identity"]: outcome
            for outcome in outcomes
        }.values()
    )
    record["result"] = copy.deepcopy(state.get("result"))
    record["failures"] = copy.deepcopy(
        (state.get("result") or {}).get("failures", [])
    )

    files: dict[str, Any] = {}
    for field in ("files", "input_files"):
        for path, identity in _mapping(state.get(field, {}), field).items():
            prior = files.get(path)
            if prior is not None and prior != identity:
                raise TargetRecordError(
                    f"v45 cache contains conflicting identities for {path}"
                )
            files[path] = copy.deepcopy(identity)
    cache = empty_cache()
    cache["files"] = files
    cache["directories"] = copy.deepcopy(state.get("directory_memberships", {}))
    cache["inspections"] = copy.deepcopy(state.get("mechanical_checks", {}))
    cache["local_indexes"] = {
        "material_owners": copy.deepcopy(graph_slice.get("material_owners", {}))
    }
    return decode_record(record), decode_cache(cache)
