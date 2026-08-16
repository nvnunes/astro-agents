"""Immutable row shards and rebuildable local indexes for validation v2."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

STORAGE_LAYOUT = "sharded-v1"
STATE_DIRECTORY = "validation"
LOCAL_CACHE_DIRECTORY = ".cache"
INDEX_FILENAME = "subject-index.json"
INDEX_DELTA_DIRECTORY = "index-deltas"
INDEX_SCHEMA_VERSION = 2
INDEX_DELTA_SCHEMA_VERSION = 1
MAX_SHARD_ROWS = 200
MAX_SHARD_BYTES = 8_388_608
MAX_ROW_BYTES = MAX_SHARD_BYTES
ROW_KINDS = ("outcomes", "judgments", "failures")
SUBJECT_KINDS = ("outcomes", "judgments")


class ShardedStateError(ValueError):
    """Raised when immutable validation state violates its storage contract."""


@dataclass(frozen=True)
class PreparedState:
    """A validated manifest, new immutable files, and optional local delta."""

    manifest: dict[str, Any]
    files: dict[str, bytes]
    index_delta: dict[str, Any] | None = None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return _canonical_json(value) + b"\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ShardedStateError(f"{field} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        raise ShardedStateError(f"{field} must be a relative POSIX path")
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ShardedStateError(f"{field} must be a lowercase SHA-256")
    return value


def _positive_count(value: Any, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        label = "nonnegative" if allow_zero else "positive"
        raise ShardedStateError(f"{field} must be a {label} integer")
    return value


def _row_subject(kind: str, row: Mapping[str, Any]) -> dict[str, Any] | None:
    if kind == "judgments":
        subject = row.get("subject")
        return copy.deepcopy(dict(subject)) if isinstance(subject, Mapping) else None
    if kind == "outcomes":
        required = ("check", "entry", "target")
        if any(not isinstance(row.get(key), str) for key in required):
            return None
        return {key: row[key] for key in required}
    return None


def subject_identity(subject: Mapping[str, Any]) -> str:
    """Return the stable collision-checked identity for one semantic subject."""

    return _sha256(_canonical_json(subject))


def _row_batches(rows: Sequence[Mapping[str, Any]]) -> list[tuple[list[Any], bytes]]:
    batches: list[tuple[list[Any], bytes]] = []
    current_rows: list[Any] = []
    current_lines: list[bytes] = []
    current_bytes = 0
    for row in rows:
        line = _canonical_json(row) + b"\n"
        if len(line) > MAX_ROW_BYTES:
            raise ShardedStateError(
                f"one validation row exceeds {MAX_ROW_BYTES} bytes"
            )
        if current_rows and (
            len(current_rows) >= MAX_SHARD_ROWS
            or current_bytes + len(line) > MAX_SHARD_BYTES
        ):
            batches.append((current_rows, b"".join(current_lines)))
            current_rows = []
            current_lines = []
            current_bytes = 0
        current_rows.append(copy.deepcopy(dict(row)))
        current_lines.append(line)
        current_bytes += len(line)
    if current_rows:
        batches.append((current_rows, b"".join(current_lines)))
    return batches


def _shard_ref(kind: str, payload: bytes, row_count: int) -> dict[str, Any]:
    identity = _sha256(payload)
    return {
        "kind": kind,
        "path": f"{kind}/{identity}.jsonl",
        "sha256": identity,
        "row_count": row_count,
        "byte_count": len(payload),
    }


def _prepared_rows(
    record: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bytes]]:
    references: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in ROW_KINDS
    }
    files: dict[str, bytes] = {}
    for kind in ROW_KINDS:
        raw_rows = record.get(kind)
        if not isinstance(raw_rows, list):
            raise ShardedStateError(f"{kind} must be an array before sharding")
        rows = [dict(row) for row in raw_rows if isinstance(row, Mapping)]
        if len(rows) != len(raw_rows):
            raise ShardedStateError(f"{kind} contains a non-object row")
        for batch, payload in _row_batches(rows):
            ref = _shard_ref(kind, payload, len(batch))
            references[kind].append(ref)
            files[str(ref["path"])] = payload
    return references, files


def prepare_state(record: Mapping[str, Any]) -> PreparedState:
    """Project one logical record into immutable shards and a small manifest."""

    references, files = _prepared_rows(record)
    manifest = {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in ROW_KINDS
    }
    manifest.update(
        {
            "storage_layout": STORAGE_LAYOUT,
            "shards": references,
            "row_counts": {
                kind: sum(int(ref["row_count"]) for ref in references[kind])
                for kind in ROW_KINDS
            },
        }
    )
    return PreparedState(manifest=manifest, files=files)


def _validate_shard_ref(value: Any, expected_kind: str, number: int) -> dict[str, Any]:
    field = f"shards.{expected_kind}[{number}]"
    if not isinstance(value, Mapping) or set(value) != {
        "kind",
        "path",
        "sha256",
        "row_count",
        "byte_count",
    }:
        raise ShardedStateError(f"{field} has incorrect fields")
    if value.get("kind") != expected_kind:
        raise ShardedStateError(f"{field}.kind does not match its collection")
    identity = _digest(value.get("sha256"), f"{field}.sha256")
    path = _relative_path(value.get("path"), f"{field}.path")
    expected_path = f"{expected_kind}/{identity}.jsonl"
    if path != expected_path:
        raise ShardedStateError(f"{field}.path does not match its identity")
    return {
        "kind": expected_kind,
        "path": path,
        "sha256": identity,
        "row_count": _positive_count(value.get("row_count"), f"{field}.row_count"),
        "byte_count": _positive_count(
            value.get("byte_count"), f"{field}.byte_count"
        ),
    }


def _validated_shards(
    shards: Mapping[str, Any], counts: Mapping[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    normalized_shards: dict[str, list[dict[str, Any]]] = {}
    normalized_counts: dict[str, int] = {}
    all_paths: set[str] = set()
    all_identities: set[str] = set()
    for kind in ROW_KINDS:
        values = shards[kind]
        if not isinstance(values, list):
            raise ShardedStateError(f"shards.{kind} must be an array")
        refs = [
            _validate_shard_ref(item, kind, number)
            for number, item in enumerate(values)
        ]
        paths = [str(ref["path"]) for ref in refs]
        identities = [str(ref["sha256"]) for ref in refs]
        if (
            len(paths) != len(set(paths))
            or all_paths.intersection(paths)
            or len(identities) != len(set(identities))
            or all_identities.intersection(identities)
        ):
            raise ShardedStateError("manifest contains duplicate shard identities")
        all_paths.update(paths)
        all_identities.update(identities)
        expected = sum(int(ref["row_count"]) for ref in refs)
        normalized_count = _positive_count(
            counts[kind], f"row_counts.{kind}", allow_zero=True
        )
        if normalized_count != expected:
            raise ShardedStateError(f"row_counts.{kind} disagrees with shards")
        normalized_shards[kind] = refs
        normalized_counts[kind] = normalized_count
    return normalized_shards, normalized_counts


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate storage-owned manifest fields without opening any shard."""

    if not isinstance(value, Mapping):
        raise ShardedStateError("sharded validation manifest must be an object")
    if value.get("storage_layout") != STORAGE_LAYOUT:
        raise ShardedStateError("unsupported validation storage layout")
    shards = value.get("shards")
    counts = value.get("row_counts")
    if not isinstance(shards, Mapping) or set(shards) != set(ROW_KINDS):
        raise ShardedStateError("manifest shards have incorrect fields")
    if not isinstance(counts, Mapping) or set(counts) != set(ROW_KINDS):
        raise ShardedStateError("manifest row_counts have incorrect fields")
    normalized_shards, normalized_counts = _validated_shards(shards, counts)
    normalized = copy.deepcopy(dict(value))
    normalized["shards"] = normalized_shards
    normalized["row_counts"] = normalized_counts
    return normalized


def manifest_closure_identity(manifest: Mapping[str, Any]) -> str:
    """Identify the exact normalized row-shard closure of one manifest."""

    valid = validate_manifest(manifest)
    closure = {
        "storage_layout": valid["storage_layout"],
        "shards": valid["shards"],
        "row_counts": valid["row_counts"],
    }
    return _sha256(_canonical_json(closure))


def _owned_path(validation_dir: Path, relative: str) -> Path:
    path = validation_dir / relative
    root = validation_dir.resolve()
    owned_paths: list[Path] = []
    candidate = validation_dir
    for part in PurePosixPath(relative).parts:
        candidate /= part
        owned_paths.append(candidate)
    if validation_dir.is_symlink() or any(item.is_symlink() for item in owned_paths):
        raise ShardedStateError("validation storage path must not be a symlink")
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ShardedStateError("validation storage path escapes its owner") from exc
    return path


def publish_immutable_files(
    validation_dir: Path,
    files: Mapping[str, bytes],
    writer: Callable[[Path, bytes], None],
) -> None:
    """Publish content-addressed files idempotently before a manifest commit."""

    for relative, payload in sorted(files.items()):
        path = _owned_path(validation_dir, _relative_path(relative, "shard file"))
        if path.exists():
            if not path.is_file() or path.read_bytes() != payload:
                raise ShardedStateError(
                    f"immutable validation shard conflicts: {relative}"
                )
            continue
        writer(path, payload)


def _read_owned_bytes(validation_dir: Path, ref: Mapping[str, Any]) -> bytes:
    path = _owned_path(validation_dir, str(ref["path"]))
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ShardedStateError(
            f"cannot read validation shard {path}: {exc}"
        ) from exc
    if len(payload) != int(ref["byte_count"]) or _sha256(payload) != ref["sha256"]:
        raise ShardedStateError(
            f"validation shard identity mismatch: {ref['path']}"
        )
    return payload


def _decode_jsonl(payload: bytes, ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        lines = payload.decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ShardedStateError(f"invalid JSONL shard {ref['path']}: {exc}") from exc
    if len(rows) != int(ref["row_count"]) or not all(
        isinstance(row, dict) for row in rows
    ):
        raise ShardedStateError(f"shard row count or shape mismatch: {ref['path']}")
    return rows


def hydrate_rows(
    validation_dir: Path, manifest: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Load and verify every row shard for full scan or rendering."""

    valid = validate_manifest(manifest)
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in ROW_KINDS:
        rows: list[dict[str, Any]] = []
        for ref in valid["shards"][kind]:
            rows.extend(_decode_jsonl(_read_owned_bytes(validation_dir, ref), ref))
        result[kind] = rows
    return result


def hydrate_selected_rows(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    kinds: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Load selected row classes without touching unrelated shard histories."""

    valid = validate_manifest(manifest)
    requested = set(kinds)
    if not requested <= set(ROW_KINDS):
        raise ShardedStateError("unsupported selected shard kind")
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in requested:
        rows: list[dict[str, Any]] = []
        for ref in valid["shards"][kind]:
            rows.extend(_decode_jsonl(_read_owned_bytes(validation_dir, ref), ref))
        result[kind] = rows
    return result


def _add_index_rows(
    subjects: dict[str, Any],
    kind: str,
    ref: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    path = str(ref["path"])
    for row in rows:
        subject = _row_subject(kind, row)
        if subject is None:
            continue
        identity = subject_identity(subject)
        entries = subjects.setdefault(identity, [])
        match = next(
            (entry for entry in entries if entry["subject"] == subject), None
        )
        if match is None:
            match = {"subject": subject, "shards": {}}
            entries.append(match)
        paths = match["shards"].setdefault(kind, [])
        if path not in paths:
            paths.append(path)


def _indexed_shards(manifest: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(ref["path"])
        for kind in SUBJECT_KINDS
        for ref in manifest["shards"][kind]
    )


def _rebuild_subject_index(
    validation_dir: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    valid = validate_manifest(manifest)
    subjects: dict[str, Any] = {}
    for kind in SUBJECT_KINDS:
        for ref in valid["shards"][kind]:
            rows = _decode_jsonl(_read_owned_bytes(validation_dir, ref), ref)
            _add_index_rows(subjects, kind, ref, rows)
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "summary": valid["summary"],
        "closure_identity": manifest_closure_identity(valid),
        "sequence": 0,
        "indexed_shards": _indexed_shards(valid),
        "subjects": subjects,
    }


def _validated_mapping_paths(
    value: Any,
    owned: Mapping[str, set[str]],
    field: str,
) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or not set(value) <= set(SUBJECT_KINDS):
        raise ShardedStateError(f"{field} has incorrect fields")
    mapped: dict[str, list[str]] = {}
    for kind, raw_paths in value.items():
        if (
            not isinstance(raw_paths, list)
            or not raw_paths
            or not all(isinstance(path, str) for path in raw_paths)
            or len(raw_paths) != len(set(raw_paths))
            or not set(raw_paths) <= owned[kind]
        ):
            raise ShardedStateError(f"{field}.{kind} names invalid shards")
        mapped[kind] = list(raw_paths)
    return mapped


def _validated_subject_entry(
    value: Any,
    identity: str,
    owned: Mapping[str, set[str]],
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"subject", "shards"}:
        raise ShardedStateError(f"{field} has incorrect fields")
    subject = value.get("subject")
    if not isinstance(subject, Mapping) or not subject:
        raise ShardedStateError(f"{field}.subject must be an object")
    subject = copy.deepcopy(dict(subject))
    if subject_identity(subject) != identity:
        raise ShardedStateError(f"{field}.subject identity disagrees")
    return {
        "subject": subject,
        "shards": _validated_mapping_paths(
            value.get("shards"), owned, f"{field}.shards"
        ),
    }


def _validate_subject_mappings(
    value: Any, manifest: Mapping[str, Any], field: str
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ShardedStateError(f"{field} must be an object")
    owned = {
        kind: {str(ref["path"]) for ref in manifest["shards"][kind]}
        for kind in SUBJECT_KINDS
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for identity, raw_entries in value.items():
        _digest(identity, f"{field} key")
        if not isinstance(raw_entries, list):
            raise ShardedStateError(f"{field}[{identity!r}] must be an array")
        entries = [
            _validated_subject_entry(
                raw_entry,
                identity,
                owned,
                f"{field}[{identity!r}][{number}]",
            )
            for number, raw_entry in enumerate(raw_entries)
        ]
        subjects = [entry["subject"] for entry in entries]
        distinct_subjects = {json.dumps(item, sort_keys=True) for item in subjects}
        if len(subjects) != len(distinct_subjects):
            raise ShardedStateError(f"{field}[{identity!r}] duplicates a subject")
        normalized[identity] = entries
    return normalized


def _validate_index(value: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "summary",
        "closure_identity",
        "sequence",
        "indexed_shards",
        "subjects",
    }:
        raise ShardedStateError("subject index has an invalid shape")
    if value.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise ShardedStateError("subject index has an unsupported schema")
    if value.get("summary") != manifest.get("summary"):
        raise ShardedStateError("subject index belongs to another summary")
    closure = _digest(value.get("closure_identity"), "subject index closure")
    sequence = _positive_count(
        value.get("sequence"), "subject index sequence", allow_zero=True
    )
    indexed = value.get("indexed_shards")
    if (
        not isinstance(indexed, list)
        or not all(isinstance(path, str) for path in indexed)
        or indexed != sorted(set(indexed))
        or not set(indexed) <= set(_indexed_shards(manifest))
    ):
        raise ShardedStateError("subject index names invalid indexed shards")
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "summary": value["summary"],
        "closure_identity": closure,
        "sequence": sequence,
        "indexed_shards": list(indexed),
        "subjects": _validate_subject_mappings(
            value.get("subjects"), manifest, "subject index subjects"
        ),
    }


def _cache_path(validation_dir: Path, relative: str) -> Path:
    relative = _relative_path(relative, "local cache path")
    return _owned_path(
        validation_dir,
        (PurePosixPath(LOCAL_CACHE_DIRECTORY) / relative).as_posix(),
    )


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ShardedStateError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShardedStateError(f"{description} must be an object")
    return value


def _validate_delta(value: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "summary",
        "sequence",
        "batch_identity",
        "prior_closure_identity",
        "closure_identity",
        "added_shards",
        "subjects",
    }:
        raise ShardedStateError("subject index delta has an invalid shape")
    if value.get("schema_version") != INDEX_DELTA_SCHEMA_VERSION:
        raise ShardedStateError("subject index delta has an unsupported schema")
    if value.get("summary") != manifest.get("summary"):
        raise ShardedStateError("subject index delta belongs to another summary")
    sequence = _positive_count(value.get("sequence"), "index delta sequence")
    batch_identity = _digest(value.get("batch_identity"), "index delta batch")
    prior = _digest(
        value.get("prior_closure_identity"), "index delta prior closure"
    )
    closure = _digest(value.get("closure_identity"), "index delta closure")
    added = value.get("added_shards")
    if not isinstance(added, list):
        raise ShardedStateError("index delta added_shards must be an array")
    refs = [
        _validate_shard_ref(ref, "judgments", number)
        for number, ref in enumerate(added)
    ]
    owned = {
        str(ref["path"]): ref for ref in manifest["shards"]["judgments"]
    }
    if (
        len({str(ref["path"]) for ref in refs}) != len(refs)
        or any(owned.get(str(ref["path"])) != ref for ref in refs)
    ):
        raise ShardedStateError("index delta names invalid added shards")
    return {
        "schema_version": INDEX_DELTA_SCHEMA_VERSION,
        "summary": value["summary"],
        "sequence": sequence,
        "batch_identity": batch_identity,
        "prior_closure_identity": prior,
        "closure_identity": closure,
        "added_shards": refs,
        "subjects": _validate_subject_mappings(
            value.get("subjects"), manifest, "index delta subjects"
        ),
    }


def _merge_subject_mappings(
    target: dict[str, Any], additions: Mapping[str, Any]
) -> None:
    for identity, entries in additions.items():
        retained = target.setdefault(identity, [])
        for entry in entries:
            match = next(
                (
                    current
                    for current in retained
                    if current["subject"] == entry["subject"]
                ),
                None,
            )
            if match is None:
                retained.append(copy.deepcopy(entry))
                continue
            for kind, paths in entry["shards"].items():
                current_paths = match["shards"].setdefault(kind, [])
                for path in paths:
                    if path not in current_paths:
                        current_paths.append(path)


def _load_index_chain(
    validation_dir: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    valid = validate_manifest(manifest)
    base = _validate_index(
        _read_json(
            _cache_path(validation_dir, INDEX_FILENAME), "subject index"
        ),
        valid,
    )
    delta_dir = _cache_path(validation_dir, INDEX_DELTA_DIRECTORY)
    if delta_dir.exists() and (not delta_dir.is_dir() or delta_dir.is_symlink()):
        raise ShardedStateError("subject index delta path is invalid")
    delta_paths = sorted(delta_dir.iterdir()) if delta_dir.is_dir() else []
    if any(path.suffix != ".json" or not path.is_file() for path in delta_paths):
        raise ShardedStateError("subject index delta directory has unexpected files")
    current_closure = str(base["closure_identity"])
    current_sequence = int(base["sequence"])
    indexed = set(base["indexed_shards"])
    subjects = copy.deepcopy(base["subjects"])
    for path in delta_paths:
        delta = _validate_delta(_read_json(path, "subject index delta"), valid)
        expected_name = (
            f"{delta['sequence']:08d}-{delta['batch_identity']}.json"
        )
        if (
            path.name != expected_name
            or delta["sequence"] != current_sequence + 1
            or delta["prior_closure_identity"] != current_closure
        ):
            raise ShardedStateError("subject index deltas are missing or reordered")
        current_sequence = int(delta["sequence"])
        current_closure = str(delta["closure_identity"])
        indexed.update(str(ref["path"]) for ref in delta["added_shards"])
        _merge_subject_mappings(subjects, delta["subjects"])
    if (
        current_closure != manifest_closure_identity(valid)
        or indexed != set(_indexed_shards(valid))
    ):
        raise ShardedStateError("subject index is stale for the manifest closure")
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "summary": valid["summary"],
        "closure_identity": current_closure,
        "sequence": current_sequence,
        "indexed_shards": sorted(indexed),
        "subjects": subjects,
    }


def _clear_index_deltas(validation_dir: Path) -> None:
    delta_dir = _cache_path(validation_dir, INDEX_DELTA_DIRECTORY)
    if not delta_dir.exists():
        return
    if not delta_dir.is_dir() or delta_dir.is_symlink():
        raise ShardedStateError("subject index delta path is invalid")
    for path in delta_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ShardedStateError("subject index delta directory is invalid")
        path.unlink()
    with suppress(OSError):
        delta_dir.rmdir()


def ensure_subject_index(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    writer: Callable[[Path, bytes], None] | None = None,
) -> dict[str, Any]:
    """Load an exact local index or rebuild it from authoritative row shards."""

    valid = validate_manifest(manifest)
    try:
        return _load_index_chain(validation_dir, valid)
    except (OSError, ShardedStateError):
        index = _rebuild_subject_index(validation_dir, valid)
    if writer is not None:
        try:
            writer(_cache_path(validation_dir, INDEX_FILENAME), _json_bytes(index))
            _clear_index_deltas(validation_dir)
        except (OSError, ShardedStateError):
            pass
    return index


def compact_subject_index(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    writer: Callable[[Path, bytes], None],
) -> None:
    """Replace ignored base-plus-deltas with one current rebuildable index."""

    index = _rebuild_subject_index(validation_dir, validate_manifest(manifest))
    writer(_cache_path(validation_dir, INDEX_FILENAME), _json_bytes(index))
    _clear_index_deltas(validation_dir)


def write_index_delta(
    validation_dir: Path,
    delta: Mapping[str, Any],
    manifest: Mapping[str, Any],
    writer: Callable[[Path, bytes], None],
) -> None:
    """Write one idempotent ignored delta after its manifest is authoritative."""

    valid = _validate_delta(delta, validate_manifest(manifest))
    name = f"{valid['sequence']:08d}-{valid['batch_identity']}.json"
    path = _cache_path(
        validation_dir, f"{INDEX_DELTA_DIRECTORY}/{name}"
    )
    payload = _json_bytes(valid)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ShardedStateError("subject index delta conflicts with its identity")
        return
    writer(path, payload)


def _subject_shard_paths(
    index: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    kind: str,
) -> set[str]:
    paths: set[str] = set()
    for subject in subjects:
        entries = index["subjects"].get(subject_identity(subject), [])
        for entry in entries:
            if isinstance(entry, Mapping) and entry.get("subject") == subject:
                shards = entry.get("shards", {})
                if isinstance(shards, Mapping):
                    paths.update(str(path) for path in shards.get(kind, []))
    return paths


def _load_mapped_rows(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    kind: str,
    subjects: Sequence[Mapping[str, Any]],
    paths: set[str],
) -> list[dict[str, Any]]:
    refs = {str(ref["path"]): ref for ref in manifest["shards"][kind]}
    if not paths <= set(refs):
        raise ShardedStateError("subject index names an unowned shard")
    rows: list[dict[str, Any]] = []
    for ref in manifest["shards"][kind]:
        if ref["path"] not in paths:
            continue
        for row in _decode_jsonl(_read_owned_bytes(validation_dir, ref), ref):
            if _row_subject(kind, row) in subjects:
                rows.append(row)
    return rows


def load_subject_rows(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    kind: str,
    subjects: Sequence[Mapping[str, Any]],
    writer: Callable[[Path, bytes], None] | None = None,
) -> list[dict[str, Any]]:
    """Load only shards mapped to exact stable semantic subjects."""

    if kind not in SUBJECT_KINDS:
        raise ShardedStateError(
            "stable-subject lookup requires outcome or judgment rows"
        )
    valid = validate_manifest(manifest)
    index = ensure_subject_index(validation_dir, valid, writer)
    requested = [copy.deepcopy(dict(subject)) for subject in subjects]
    paths = _subject_shard_paths(index, requested, kind)
    return _load_mapped_rows(validation_dir, valid, kind, requested, paths)


def _prepare_index_delta(
    index: Mapping[str, Any],
    current: Mapping[str, Any],
    updated: Mapping[str, Any],
    refs: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    subjects: dict[str, Any] = {}
    row_offset = 0
    for ref in refs:
        row_count = int(ref["row_count"])
        batch = rows[row_offset : row_offset + row_count]
        _add_index_rows(subjects, "judgments", ref, batch)
        row_offset += row_count
    batch_identity = _sha256(
        _canonical_json(
            {
                "shards": list(refs),
                "judgments": [row.get("identity") for row in rows],
            }
        )
    )
    return {
        "schema_version": INDEX_DELTA_SCHEMA_VERSION,
        "summary": updated["summary"],
        "sequence": int(index["sequence"]) + 1,
        "batch_identity": batch_identity,
        "prior_closure_identity": manifest_closure_identity(current),
        "closure_identity": manifest_closure_identity(updated),
        "added_shards": copy.deepcopy(list(refs)),
        "subjects": subjects,
    }


def prepare_judgment_append(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    writer: Callable[[Path, bytes], None] | None = None,
) -> PreparedState:
    """Prepare one idempotent accepted-judgment batch and one local delta."""

    valid = validate_manifest(manifest)
    subjects = [
        subject
        for row in rows
        if (subject := _row_subject("judgments", row)) is not None
    ]
    if len(subjects) != len(rows):
        raise ShardedStateError("accepted judgment lacks a stable subject")
    index = ensure_subject_index(validation_dir, valid, writer)
    paths = _subject_shard_paths(index, subjects, "judgments")
    existing = _load_mapped_rows(
        validation_dir, valid, "judgments", subjects, paths
    )
    by_identity = {
        str(row.get("identity")): row
        for row in existing
        if isinstance(row.get("identity"), str)
    }
    pending: list[dict[str, Any]] = []
    for row in rows:
        identity = row.get("identity")
        if not isinstance(identity, str):
            raise ShardedStateError("accepted judgment lacks an identity")
        prior = by_identity.get(identity)
        if prior is not None:
            if prior != row:
                raise ShardedStateError(
                    "accepted judgment conflicts with its durable identity"
                )
            continue
        pending.append(copy.deepcopy(dict(row)))
    if not pending:
        return PreparedState(copy.deepcopy(valid), {})
    files: dict[str, bytes] = {}
    new_refs: list[dict[str, Any]] = []
    for batch, payload in _row_batches(pending):
        ref = _shard_ref("judgments", payload, len(batch))
        new_refs.append(ref)
        files[str(ref["path"])] = payload
    updated = copy.deepcopy(valid)
    updated["shards"]["judgments"].extend(new_refs)
    updated["row_counts"]["judgments"] += len(pending)
    delta = _prepare_index_delta(index, valid, updated, new_refs, pending)
    return PreparedState(updated, files, delta)


def prepare_progress_state(
    validation_dir: Path,
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
    writer: Callable[[Path, bytes], None] | None = None,
) -> PreparedState:
    """Replace outcome/failure projections while retaining judgment shards."""

    judgment_update = prepare_judgment_append(
        validation_dir, manifest, record.get("judgments", []), writer
    )
    updated = judgment_update.manifest
    files = dict(judgment_update.files)
    references: dict[str, list[dict[str, Any]]] = {
        "outcomes": [],
        "failures": [],
    }
    for kind in ("outcomes", "failures"):
        raw_rows = record.get(kind, [])
        if not isinstance(raw_rows, list) or not all(
            isinstance(row, Mapping) for row in raw_rows
        ):
            raise ShardedStateError(f"{kind} must contain object rows")
        for batch, payload in _row_batches(raw_rows):
            ref = _shard_ref(kind, payload, len(batch))
            references[kind].append(ref)
            files[str(ref["path"])] = payload
    next_manifest = {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in {*ROW_KINDS, "_sharded_manifest", "_state_loaded"}
    }
    next_manifest.update(
        {
            "storage_layout": STORAGE_LAYOUT,
            "shards": {
                "outcomes": references["outcomes"],
                "judgments": copy.deepcopy(updated["shards"]["judgments"]),
                "failures": references["failures"],
            },
            "row_counts": {
                "outcomes": len(record.get("outcomes", [])),
                "judgments": int(updated["row_counts"]["judgments"]),
                "failures": len(record.get("failures", [])),
            },
        }
    )
    return PreparedState(next_manifest, files)
