"""Persistence contracts for one log's durable and local validation state.

``validation/manifest.json`` and its row shards are authoritative. Local state
under ``validation/.cache/`` is rebuildable or transient and never determines
whether a validation result is correct.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from . import sharded_state
from .judgment_rules import (
    TERMINAL_CLEANUP_CACHE_KEY,
    TERMINAL_CLEANUP_VERSION,
)
from .records import RecordPublicationError, _atomic_write_bytes, validation_lock

RECORD_FILENAME = "validation/manifest.json"
CACHE_FILENAME = "validation/.cache/cache.json"
RECORD_SCHEMA_VERSION = 2
CACHE_SCHEMA_VERSION = 1
RETIRED_VALIDATION_FILENAMES = (
    "validation-decisions.json",
    "validation-state.json",
    "validation-index.json",
    "validation-record.json",
    "validation-cache.json",
    "validation-state",
    ".research-log-validation.lock",
)


class TargetRecordError(ValueError):
    """Raised when authoritative target validation data violates its contract."""


def validation_directory(output_dir: Path) -> Path:
    """Return the durable validation directory owned by one research log."""

    return output_dir / sharded_state.STATE_DIRECTORY


def manifest_path(output_dir: Path) -> Path:
    """Return one log's authoritative manifest path."""

    return output_dir / RECORD_FILENAME


def cache_path(output_dir: Path) -> Path:
    """Return one log's ignored deterministic-cache path."""

    return output_dir / CACHE_FILENAME


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


def _coalesce_saved_judgments(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Load exact historical duplicates once without rewriting saved shards.

    Some already-published manifests reference the same immutable judgment row
    from different shards. Identical rows are one logical judgment. Conflicting
    rows with the same identity remain a durable-state error, and newly
    constructed logical records still reject duplicate identities through
    ``decode_record``.
    """

    loaded: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = copy.deepcopy(dict(row))
        identity = current.get("identity")
        if not isinstance(identity, str):
            loaded.append(current)
            continue
        prior = by_identity.get(identity)
        if prior is None:
            by_identity[identity] = current
            loaded.append(current)
        elif prior != current:
            raise TargetRecordError(
                "saved judgments contain conflicting duplicate identities"
            )
    return loaded


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
        # Read-only compatibility for a packet issued before the unified
        # review-session lifecycle. New continuations always use ``paged``.
        if set(continuation) != {"kind", "identity", "item_count"}:
            raise TargetRecordError(
                "legacy ordinary continuation has incorrect fields"
            )
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
        session = _validate_relative_path(
            continuation.get("session"), "continuation.session"
        )
        session_path = PurePosixPath(session)
        if len(session_path.parts) != 2 or session_path.parts[0] != "work":
            raise TargetRecordError(
                "continuation.session must be work/<session-id>"
            )
        _validate_sha256(
            continuation.get("session_identity"),
            "continuation.session_identity",
        )
        if session_path.parts[1] != continuation.get("session_identity"):
            raise TargetRecordError(
                "continuation.session must match continuation.session_identity"
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


def decode_sharded_manifest(value: Any) -> dict[str, Any]:
    """Validate one small authoritative manifest without opening row shards."""

    manifest = sharded_state.validate_manifest(value)
    expected_fields = {
        "schema_version",
        "storage_layout",
        "summary",
        "validation_rules_version",
        "rule_dependencies",
        "shards",
        "row_counts",
        "result",
        "continuation",
        "completion_dependencies",
        "projection",
    }
    if set(manifest) != expected_fields:
        raise TargetRecordError("sharded durable manifest has incorrect fields")
    _validate_record_header(manifest)
    _validate_record_rule_dependencies(manifest)
    result = manifest.get("result")
    if result is not None:
        _mapping(result, "result")
    _validate_dependencies(
        manifest.get("completion_dependencies"), "completion_dependencies"
    )
    _validate_projection(manifest.get("projection"))
    _validate_continuation(manifest.get("continuation"))
    return copy.deepcopy(manifest)


def _manifest_shell(manifest: Mapping[str, Any]) -> dict[str, Any]:
    shell = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in {"storage_layout", "shards", "row_counts"}
    }
    shell.update(
        {
            "judgments": [],
            "outcomes": [],
            "failures": [],
            "_sharded_manifest": copy.deepcopy(dict(manifest)),
        }
    )
    return shell


def empty_record_shell(summary: str, rules_version: str) -> dict[str, Any]:
    """Return canonical empty state without publishing files or a manifest."""

    prepared = sharded_state.prepare_state(empty_record(summary, rules_version))
    if prepared.files:
        raise TargetRecordError("empty validation state unexpectedly produced shards")
    return _manifest_shell(decode_sharded_manifest(prepared.manifest))


def is_sharded_shell(record: Mapping[str, Any]) -> bool:
    """Return whether an in-memory record is a lightweight sharded header."""

    return isinstance(record.get("_sharded_manifest"), Mapping)


def record_row_count(record: Mapping[str, Any], kind: str) -> int:
    """Return a row count without forcing a sharded record to hydrate."""

    if kind not in sharded_state.ROW_KINDS:
        raise TargetRecordError(f"unsupported record row kind: {kind}")
    manifest = record.get("_sharded_manifest")
    if isinstance(manifest, Mapping):
        return int(manifest.get("row_counts", {}).get(kind, 0))
    rows = record.get(kind, [])
    return len(rows) if isinstance(rows, list) else 0


def hydrate_record_shell(
    record: Mapping[str, Any],
    output_dir: Path,
    *,
    preserve_manifest: bool = False,
) -> dict[str, Any]:
    """Load every referenced row for diagnostics or legacy compatibility."""

    if not is_sharded_shell(record):
        return decode_record(record)
    manifest = decode_sharded_manifest(record["_sharded_manifest"])
    rows = sharded_state.hydrate_rows(validation_directory(output_dir), manifest)
    rows["judgments"] = _coalesce_saved_judgments(rows["judgments"])
    logical = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in {"storage_layout", "shards", "row_counts"}
    }
    logical.update(rows)
    decoded = decode_record(logical)
    if preserve_manifest:
        decoded["_sharded_manifest"] = copy.deepcopy(manifest)
    return decoded


def hydrate_record_rows(
    record: Mapping[str, Any], output_dir: Path, kinds: Sequence[str]
) -> dict[str, Any]:
    """Hydrate selected histories while retaining the lightweight manifest."""

    if not is_sharded_shell(record):
        return decode_record(record)
    result = copy.deepcopy(dict(record))
    rows = sharded_state.hydrate_selected_rows(
        validation_directory(output_dir), record["_sharded_manifest"], kinds
    )
    if "judgments" in rows:
        rows["judgments"] = _coalesce_saved_judgments(rows["judgments"])
    result.update(rows)
    return result


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


def _assert_manifest_source(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise TargetRecordError("durable validation manifest must not be a symlink")


def load_record_with_source(
    path: Path,
    *,
    expected_summary: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Load one record and report the native schema found on disk."""

    _assert_manifest_source(path)
    try:
        value = _read_json(path)
    except FileNotFoundError as exc:
        raise TargetRecordError(
            f"durable validation record is missing: {path}"
        ) from exc
    raw = _mapping(value, "durable record")
    source_version = raw.get("schema_version")
    if source_version == RECORD_SCHEMA_VERSION:
        if raw.get("storage_layout") != sharded_state.STORAGE_LAYOUT:
            raise TargetRecordError(
                "monolithic native-v2 validation records are retired; use a "
                "pre-transition astro-agents checkout to migrate this log"
            )
        manifest = decode_sharded_manifest(raw)
        if expected_summary is not None and manifest["summary"] != expected_summary:
            raise TargetRecordError(
                "durable validation record belongs to another summary"
            )
        record = hydrate_record_shell(_manifest_shell(manifest), path.parent.parent)
        if expected_summary is not None and record["summary"] != expected_summary:
            raise TargetRecordError(
                "durable validation record belongs to another summary"
            )
        return record, RECORD_SCHEMA_VERSION
    if source_version == 1:
        raise TargetRecordError(
            "native-v1 validation records are retired; use a pre-transition "
            "astro-agents checkout to migrate this log"
        )
    raise TargetRecordError(
        "unsupported validation-record schema; expected "
        f"{RECORD_SCHEMA_VERSION}, got {source_version!r}"
    )


def load_record_header_with_source(
    path: Path,
    *,
    expected_summary: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Load only a supported sharded manifest and validate its ownership."""

    _assert_manifest_source(path)
    try:
        value = _read_json(path)
    except FileNotFoundError as exc:
        raise TargetRecordError(
            f"durable validation record is missing: {path}"
        ) from exc
    raw = _mapping(value, "durable record")
    if (
        raw.get("schema_version") == RECORD_SCHEMA_VERSION
        and raw.get("storage_layout") == sharded_state.STORAGE_LAYOUT
    ):
        manifest = decode_sharded_manifest(raw)
        if expected_summary is not None and manifest["summary"] != expected_summary:
            raise TargetRecordError(
                "durable validation record belongs to another summary"
            )
        return _manifest_shell(manifest), RECORD_SCHEMA_VERSION
    return load_record_with_source(
        path,
        expected_summary=expected_summary,
    )


def load_record(
    path: Path,
    *,
    expected_summary: str | None = None,
) -> dict[str, Any]:
    """Load authoritative state, failing actionably on malformed ownership."""

    record, _ = load_record_with_source(
        path,
        expected_summary=expected_summary,
    )
    return record


def load_cache(path: Path) -> tuple[dict[str, Any], str]:
    """Load cache data or return an empty recomputation case and diagnostic."""

    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        return empty_cache(), "malformed"
    try:
        return decode_cache(_read_json(path)), "loaded"
    except FileNotFoundError:
        return empty_cache(), "missing"
    except TargetRecordError:
        return empty_cache(), "malformed"


def load_judgments_for_subjects(
    output_dir: Path,
    record: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Load only judgment shards relevant to exact stable subjects."""

    if not subjects:
        return []
    manifest = record.get("_sharded_manifest")
    if not isinstance(manifest, Mapping):
        judgments = record.get("judgments", [])
        return list(judgments) if isinstance(judgments, list) else []
    return _coalesce_saved_judgments(
        sharded_state.load_subject_rows(
            validation_directory(output_dir),
            manifest,
            "judgments",
            subjects,
            _atomic_write_bytes,
        )
    )


def append_judgment_batch(
    output_dir: Path,
    record: Mapping[str, Any],
    judgments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Publish one accepted judgment shard before advancing review state."""

    for number, judgment in enumerate(judgments):
        _validate_judgment(judgment, number)
    _assert_output_destinations(output_dir)
    assert_no_retired_artifacts(output_dir)
    manifest = record.get("_sharded_manifest")
    if not isinstance(manifest, Mapping):
        raise TargetRecordError("accepted-batch append requires sharded state")
    current = decode_sharded_manifest(manifest)
    state_dir = validation_directory(output_dir)
    prepared = sharded_state.prepare_judgment_append(
        state_dir, current, judgments, _atomic_write_bytes
    )
    if not prepared.files:
        return _manifest_shell(current)
    valid_manifest = decode_sharded_manifest(prepared.manifest)
    try:
        with validation_lock(output_dir):
            disk = decode_sharded_manifest(
                _read_json(manifest_path(output_dir))
            )
            if disk != current:
                raise RecordPublicationError(
                    "durable validation manifest changed during batch acceptance"
                )
            sharded_state.publish_immutable_files(
                state_dir, prepared.files, _atomic_write_bytes
            )
            _atomic_write_bytes(
                manifest_path(output_dir), _json_bytes(valid_manifest)
            )
    except (RecordPublicationError, sharded_state.ShardedStateError):
        raise
    except OSError as exc:
        raise RecordPublicationError(
            f"accepted judgment batch could not be written: {exc}"
        ) from exc
    if prepared.index_delta is not None:
        try:
            sharded_state.write_index_delta(
                state_dir,
                prepared.index_delta,
                valid_manifest,
                _atomic_write_bytes,
            )
        except (OSError, sharded_state.ShardedStateError):
            pass
    return _manifest_shell(valid_manifest)


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


def _prepare_record_state(
    output_dir: Path, record: Mapping[str, Any]
) -> sharded_state.PreparedState:
    if is_sharded_shell(record):
        logical = {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key != "_sharded_manifest"
        }
        valid_subset = decode_record(logical)
        return sharded_state.prepare_progress_state(
            validation_directory(output_dir),
            record["_sharded_manifest"],
            valid_subset,
            _atomic_write_bytes,
        )
    return sharded_state.prepare_state(decode_record(record))


def _prepare_compacted_state(
    output_dir: Path,
    record: Mapping[str, Any],
    superseded_subjects: Sequence[Mapping[str, Any]],
) -> tuple[sharded_state.PreparedState, dict[str, int]]:
    prepared = _prepare_record_state(output_dir, record)
    terminal = record.get("continuation") is None
    return sharded_state.prepare_judgment_compaction(
        validation_directory(output_dir),
        prepared,
        superseded_subjects if terminal else (),
        prune_incompatible=terminal,
    )


def cleanup_unreachable_shards(
    output_dir: Path, manifest: Mapping[str, Any], *, publish: bool
) -> dict[str, int]:
    """Report or collect shard files outside one verified manifest closure."""

    if not publish:
        return sharded_state.collect_unreachable_shards(
            validation_directory(output_dir), manifest, delete=False
        )
    try:
        with validation_lock(output_dir):
            disk = decode_sharded_manifest(_read_json(manifest_path(output_dir)))
            expected = decode_sharded_manifest(manifest)
            if disk != expected:
                raise RecordPublicationError(
                    "durable validation manifest changed before shard collection"
                )
            return sharded_state.collect_unreachable_shards(
                validation_directory(output_dir), disk, delete=True
            )
    except OSError:
        return {
            "unreachable_shards": 0,
            "unreachable_bytes": 0,
            "shards_deleted": 0,
            "cleanup_pending": 1,
        }


def inspect_target_cleanup(
    output_dir: Path,
    record: Mapping[str, Any],
    superseded_subjects: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Return the terminal compaction and collection dry-run report."""

    prepared, report = _prepare_compacted_state(
        output_dir, record, superseded_subjects
    )
    return {
        **report,
        **cleanup_unreachable_shards(
            output_dir, prepared.manifest, publish=False
        ),
    }


def compact_cached_judgments(
    output_dir: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
    *,
    publish: bool,
) -> dict[str, int]:
    """Compact one coherent terminal manifest without hydrating other rows."""

    manifest = record.get("_sharded_manifest")
    if not isinstance(manifest, Mapping):
        return {}
    current = decode_sharded_manifest(manifest)
    if current.get("continuation") is not None:
        raise TargetRecordError("cached judgment compaction requires terminal state")
    marker = cache.get("local_indexes", {}).get(TERMINAL_CLEANUP_CACHE_KEY)
    expected_marker = _terminal_cleanup_marker(current)
    if marker == expected_marker:
        return cleanup_unreachable_shards(
            output_dir, current, publish=publish
        )
    prepared, report = sharded_state.prepare_judgment_compaction(
        validation_directory(output_dir),
        sharded_state.PreparedState(current, {}),
        (),
        prune_incompatible=True,
    )
    updated = decode_sharded_manifest(prepared.manifest)
    if not publish:
        return {
            **report,
            **cleanup_unreachable_shards(output_dir, updated, publish=False),
        }
    if updated != current:
        _assert_output_destinations(output_dir)
        assert_no_retired_artifacts(output_dir)
        try:
            with validation_lock(output_dir):
                disk = decode_sharded_manifest(
                    _read_json(manifest_path(output_dir))
                )
                if disk != current:
                    raise RecordPublicationError(
                        "durable validation manifest changed during terminal cleanup"
                    )
                sharded_state.publish_immutable_files(
                    validation_directory(output_dir),
                    prepared.files,
                    _atomic_write_bytes,
                )
                _atomic_write_bytes(
                    manifest_path(output_dir), _json_bytes(updated)
                )
        except (RecordPublicationError, sharded_state.ShardedStateError):
            raise
        except OSError as exc:
            raise RecordPublicationError(
                f"terminal judgment cleanup could not be written: {exc}"
            ) from exc
    _write_local_state(output_dir, cache, updated)
    return {
        **report,
        **cleanup_unreachable_shards(output_dir, updated, publish=True),
    }


def _compact_local_subject_index(
    output_dir: Path, manifest: Mapping[str, Any]
) -> None:
    try:
        sharded_state.compact_subject_index(
            validation_directory(output_dir), manifest, _atomic_write_bytes
        )
    except (OSError, sharded_state.ShardedStateError):
        pass


def _terminal_cleanup_marker(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": TERMINAL_CLEANUP_VERSION,
        "manifest_closure": sharded_state.manifest_closure_identity(manifest),
    }


def _write_local_state(
    output_dir: Path,
    cache: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    """Best-effort refresh ignored cache and compact subject index state."""

    updated_cache = copy.deepcopy(dict(cache))
    local_indexes = updated_cache.setdefault("local_indexes", {})
    if manifest.get("result") is not None and manifest.get("continuation") is None:
        local_indexes[TERMINAL_CLEANUP_CACHE_KEY] = _terminal_cleanup_marker(
            manifest
        )
    else:
        local_indexes.pop(TERMINAL_CLEANUP_CACHE_KEY, None)
    try:
        _atomic_write_bytes(cache_path(output_dir), _json_bytes(updated_cache))
    except OSError:
        pass
    _compact_local_subject_index(output_dir, manifest)


def _assert_output_destinations(output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise RecordPublicationError(
            "target validation output directory must not be a symlink"
        )
    destinations = (
        manifest_path(output_dir),
        cache_path(output_dir),
        output_dir / "validation.md",
        validation_directory(output_dir),
    )
    for destination in destinations:
        current = output_dir
        for part in destination.relative_to(output_dir).parts:
            current /= part
            if current.is_symlink():
                raise RecordPublicationError(
                    "target validation destination must not be a symlink: "
                    + destination.relative_to(output_dir).as_posix()
                )


def write_record_and_cache(
    output_dir: Path, record: Mapping[str, Any], cache: Mapping[str, Any]
) -> dict[str, Any]:
    """Atomically write target state under the per-log validation lock.

    New row shards are published before the manifest commit point. Ignored
    local state is refreshed afterward and cannot invalidate durable work.
    The return value is a fresh lightweight shell for the published manifest.
    """

    valid_cache = decode_cache(cache)
    prepared = _prepare_record_state(output_dir, record)
    valid_manifest = decode_sharded_manifest(prepared.manifest)
    _assert_output_destinations(output_dir)
    assert_no_retired_artifacts(output_dir)
    try:
        with validation_lock(output_dir):
            sharded_state.publish_immutable_files(
                validation_directory(output_dir),
                prepared.files,
                _atomic_write_bytes,
            )
            _atomic_write_bytes(
                manifest_path(output_dir), _json_bytes(valid_manifest)
            )
    except (RecordPublicationError, sharded_state.ShardedStateError):
        raise
    except OSError as exc:
        raise RecordPublicationError(
            f"target validation state could not be written: {exc}"
        ) from exc
    _write_local_state(output_dir, valid_cache, valid_manifest)
    return _manifest_shell(valid_manifest)


def publish_target_bundle(
    output_dir: Path,
    report_text: str,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
    superseded_subjects: Sequence[Mapping[str, Any]] = (),
) -> dict[str, int]:
    """Publish target files with the report as the final commit point.

    Progressive durable state is written first and the report remains the
    final durable projection write. Local cache/index refresh happens only
    after durable publication and cannot change the bundle's authority.
    """

    valid_cache = decode_cache(cache)
    prepared, cleanup = _prepare_compacted_state(
        output_dir, record, superseded_subjects
    )
    valid_manifest = decode_sharded_manifest(prepared.manifest)
    if not report_text.endswith("\n"):
        raise TargetRecordError("validation report must end with a newline")
    _assert_output_destinations(output_dir)
    assert_no_retired_artifacts(output_dir)
    try:
        with validation_lock(output_dir):
            sharded_state.publish_immutable_files(
                validation_directory(output_dir),
                prepared.files,
                _atomic_write_bytes,
            )
            _atomic_write_bytes(
                manifest_path(output_dir), _json_bytes(valid_manifest)
            )
            _atomic_write_bytes(output_dir / "validation.md", report_text.encode())
    except (RecordPublicationError, sharded_state.ShardedStateError):
        raise
    except OSError as exc:
        raise RecordPublicationError(
            f"target validation bundle could not be written: {exc}"
        ) from exc
    _write_local_state(output_dir, valid_cache, valid_manifest)
    return {
        **cleanup,
        **cleanup_unreachable_shards(output_dir, valid_manifest, publish=True),
    }
