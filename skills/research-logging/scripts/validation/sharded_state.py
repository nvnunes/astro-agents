"""Immutable sharded storage for logical validation-record v2 state."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

STORAGE_LAYOUT = "sharded-v1"
STATE_DIRECTORY = "validation-state"
INDEX_SCHEMA_VERSION = 1
MAX_SHARD_ROWS = 200
MAX_SHARD_BYTES = 1_048_576
MAX_ROW_BYTES = 524_288
ROW_KINDS = ("outcomes", "judgments", "failures")


class ShardedStateError(ValueError):
    """Raised when immutable validation state violates its storage contract."""


@dataclass(frozen=True)
class PreparedState:
    """A validated manifest and its immutable content-addressed files."""

    manifest: dict[str, Any]
    files: dict[str, bytes]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


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
                f"one validation-state row exceeds {MAX_ROW_BYTES} bytes"
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
        "path": f"{STATE_DIRECTORY}/{kind}/{identity}.jsonl",
        "sha256": identity,
        "row_count": row_count,
        "byte_count": len(payload),
    }


def _index_ref(payload: bytes, subject_count: int) -> dict[str, Any]:
    identity = _sha256(payload)
    return {
        "path": f"{STATE_DIRECTORY}/index/{identity}.json",
        "sha256": identity,
        "byte_count": len(payload),
        "subject_count": subject_count,
    }


def _empty_index() -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "subjects": {},
    }


def _add_index_rows(
    index: dict[str, Any],
    kind: str,
    ref: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    path = str(ref["path"])
    for row in rows:
        subject = _row_subject(kind, row)
        if subject is not None:
            identity = subject_identity(subject)
            entries = index["subjects"].setdefault(identity, [])
            match = next(
                (entry for entry in entries if entry["subject"] == subject), None
            )
            if match is None:
                match = {"subject": subject, "shards": {}}
                entries.append(match)
            paths = match["shards"].setdefault(kind, [])
            if path not in paths:
                paths.append(path)


def _prepared_rows(
    record: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bytes], dict[str, Any]]:
    references: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in ROW_KINDS
    }
    files: dict[str, bytes] = {}
    index = _empty_index()
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
            _add_index_rows(index, kind, ref, batch)
    return references, files, index


def prepare_state(record: Mapping[str, Any]) -> PreparedState:
    """Project one validated logical record into immutable shards and a manifest."""

    references, files, index = _prepared_rows(record)
    index_payload = _canonical_json(index) + b"\n"
    index_ref = _index_ref(index_payload, len(index["subjects"]))
    files[str(index_ref["path"])] = index_payload
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
            "subject_index": index_ref,
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
    expected_path = f"{STATE_DIRECTORY}/{expected_kind}/{identity}.jsonl"
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


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate storage-owned manifest fields without opening any shard."""

    if not isinstance(value, Mapping):
        raise ShardedStateError("sharded validation manifest must be an object")
    if value.get("storage_layout") != STORAGE_LAYOUT:
        raise ShardedStateError("unsupported validation storage layout")
    shards = value.get("shards")
    counts = value.get("row_counts")
    index = value.get("subject_index")
    if not isinstance(shards, Mapping) or set(shards) != set(ROW_KINDS):
        raise ShardedStateError("manifest shards have incorrect fields")
    if not isinstance(counts, Mapping) or set(counts) != set(ROW_KINDS):
        raise ShardedStateError("manifest row_counts have incorrect fields")
    normalized_shards, normalized_counts = _validated_shards(shards, counts)
    normalized = copy.deepcopy(dict(value))
    normalized["shards"] = normalized_shards
    normalized["row_counts"] = normalized_counts
    normalized["subject_index"] = _validated_index_ref(index)
    return normalized


def _validated_shards(
    shards: Mapping[str, Any], counts: Mapping[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    normalized_shards: dict[str, list[dict[str, Any]]] = {}
    normalized_counts: dict[str, int] = {}
    all_paths: set[str] = set()
    for kind in ROW_KINDS:
        values = shards[kind]
        if not isinstance(values, list):
            raise ShardedStateError(f"shards.{kind} must be an array")
        refs = [
            _validate_shard_ref(item, kind, number)
            for number, item in enumerate(values)
        ]
        paths = [str(ref["path"]) for ref in refs]
        if len(paths) != len(set(paths)) or all_paths.intersection(paths):
            raise ShardedStateError("manifest contains duplicate shard paths")
        all_paths.update(paths)
        expected = sum(int(ref["row_count"]) for ref in refs)
        normalized_count = _positive_count(
            counts[kind], f"row_counts.{kind}", allow_zero=True
        )
        if normalized_count != expected:
            raise ShardedStateError(f"row_counts.{kind} disagrees with shards")
        normalized_shards[kind] = refs
        normalized_counts[kind] = normalized_count
    return normalized_shards, normalized_counts


def _validated_index_ref(index: Any) -> dict[str, Any]:
    if not isinstance(index, Mapping) or set(index) != {
        "path",
        "sha256",
        "byte_count",
        "subject_count",
    }:
        raise ShardedStateError("subject_index has incorrect fields")
    index_identity = _digest(index.get("sha256"), "subject_index.sha256")
    index_path = _relative_path(index.get("path"), "subject_index.path")
    if index_path != f"{STATE_DIRECTORY}/index/{index_identity}.json":
        raise ShardedStateError("subject_index.path does not match its identity")
    return {
        "path": index_path,
        "sha256": index_identity,
        "byte_count": _positive_count(
            index.get("byte_count"), "subject_index.byte_count"
        ),
        "subject_count": _positive_count(
            index.get("subject_count"),
            "subject_index.subject_count",
            allow_zero=True,
        ),
    }


def _owned_path(output_dir: Path, relative: str) -> Path:
    path = output_dir / relative
    state_directory = output_dir / STATE_DIRECTORY
    state_root = state_directory.resolve()
    owned_paths: list[Path] = []
    candidate = output_dir
    for part in PurePosixPath(relative).parts:
        candidate /= part
        owned_paths.append(candidate)
    if output_dir.is_symlink() or any(
        candidate.is_symlink() for candidate in owned_paths
    ):
        raise ShardedStateError("validation-state path must not be a symlink")
    try:
        path.resolve().relative_to(state_root)
    except ValueError as exc:
        raise ShardedStateError("validation-state path escapes its owner") from exc
    return path


def publish_immutable_files(
    output_dir: Path,
    files: Mapping[str, bytes],
    writer: Callable[[Path, bytes], None],
) -> None:
    """Publish content-addressed files idempotently before a manifest commit."""

    for relative, payload in sorted(files.items()):
        path = _owned_path(output_dir, _relative_path(relative, "state file"))
        if path.exists():
            if not path.is_file() or path.read_bytes() != payload:
                raise ShardedStateError(
                    f"immutable validation-state file conflicts: {relative}"
                )
            continue
        writer(path, payload)


def _read_owned_bytes(output_dir: Path, ref: Mapping[str, Any]) -> bytes:
    path = _owned_path(output_dir, str(ref["path"]))
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ShardedStateError(
            f"cannot read validation-state file {path}: {exc}"
        ) from exc
    if len(payload) != int(ref["byte_count"]) or _sha256(payload) != ref["sha256"]:
        raise ShardedStateError(
            f"validation-state identity mismatch: {ref['path']}"
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
    output_dir: Path, manifest: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Load and verify every row shard for full scan or rendering."""

    valid = validate_manifest(manifest)
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in ROW_KINDS:
        rows: list[dict[str, Any]] = []
        for ref in valid["shards"][kind]:
            rows.extend(_decode_jsonl(_read_owned_bytes(output_dir, ref), ref))
        result[kind] = rows
    return result


def hydrate_selected_rows(
    output_dir: Path,
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
            rows.extend(_decode_jsonl(_read_owned_bytes(output_dir, ref), ref))
        result[kind] = rows
    return result


def _decode_index(output_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    valid = validate_manifest(manifest)
    ref = valid["subject_index"]
    payload = _read_owned_bytes(output_dir, ref)
    try:
        index = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ShardedStateError(f"invalid subject index: {exc}") from exc
    if (
        not isinstance(index, dict)
        or index.get("schema_version") != INDEX_SCHEMA_VERSION
        or not isinstance(index.get("subjects"), dict)
        or set(index) != {"schema_version", "subjects"}
        or len(index["subjects"]) != int(ref["subject_count"])
    ):
        raise ShardedStateError("subject index has an invalid shape")
    return index


def load_subject_rows(
    output_dir: Path,
    manifest: Mapping[str, Any],
    kind: str,
    subjects: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Load only shards mapped to exact stable semantic subjects."""

    if kind not in {"outcomes", "judgments"}:
        raise ShardedStateError(
            "stable-subject lookup requires outcome or judgment rows"
        )
    valid = validate_manifest(manifest)
    index = _decode_index(output_dir, valid)
    requested = [copy.deepcopy(dict(subject)) for subject in subjects]
    paths = _subject_shard_paths(index, requested, kind)
    return _load_mapped_rows(output_dir, valid, kind, requested, paths)


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
    output_dir: Path,
    manifest: Mapping[str, Any],
    kind: str,
    subjects: Sequence[Mapping[str, Any]],
    paths: set[str],
) -> list[dict[str, Any]]:
    refs = {
        str(ref["path"]): ref for ref in manifest["shards"][kind]
    }
    if not paths <= set(refs):
        raise ShardedStateError("subject index names an unowned shard")
    rows: list[dict[str, Any]] = []
    for ref in manifest["shards"][kind]:
        if ref["path"] not in paths:
            continue
        for row in _decode_jsonl(_read_owned_bytes(output_dir, ref), ref):
            if _row_subject(kind, row) in subjects:
                rows.append(row)
    return rows


def prepare_judgment_append(
    output_dir: Path,
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> PreparedState:
    """Prepare one idempotent accepted-judgment batch and compact metadata."""

    valid = validate_manifest(manifest)
    subjects = [
        subject
        for row in rows
        if (subject := _row_subject("judgments", row)) is not None
    ]
    if len(subjects) != len(rows):
        raise ShardedStateError("accepted judgment lacks a stable subject")
    existing = load_subject_rows(
        output_dir, valid, "judgments", subjects
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
    index = _decode_index(output_dir, valid)
    files: dict[str, bytes] = {}
    new_refs: list[dict[str, Any]] = []
    for batch, payload in _row_batches(pending):
        ref = _shard_ref("judgments", payload, len(batch))
        new_refs.append(ref)
        files[str(ref["path"])] = payload
        _add_index_rows(index, "judgments", ref, batch)
    index_payload = _canonical_json(index) + b"\n"
    index_ref = _index_ref(index_payload, len(index["subjects"]))
    files[str(index_ref["path"])] = index_payload
    updated = copy.deepcopy(valid)
    updated["shards"]["judgments"].extend(new_refs)
    updated["row_counts"]["judgments"] += len(pending)
    updated["subject_index"] = index_ref
    return PreparedState(updated, files)


def prepare_progress_state(
    output_dir: Path,
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
) -> PreparedState:
    """Replace current outcome/failure projections while retaining judgments."""

    judgment_update = prepare_judgment_append(
        output_dir, manifest, record.get("judgments", [])
    )
    updated = judgment_update.manifest
    files = dict(judgment_update.files)
    index_path = str(updated["subject_index"]["path"])
    if index_path in files:
        index = json.loads(files.pop(index_path))
    else:
        index = _decode_index(output_dir, updated)
    for identity, entries in list(index["subjects"].items()):
        retained = []
        for entry in entries:
            shards = dict(entry.get("shards", {}))
            shards.pop("outcomes", None)
            if shards:
                retained.append({"subject": entry["subject"], "shards": shards})
        if retained:
            index["subjects"][identity] = retained
        else:
            del index["subjects"][identity]

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
            _add_index_rows(index, kind, ref, batch)

    index_payload = _canonical_json(index) + b"\n"
    index_ref = _index_ref(index_payload, len(index["subjects"]))
    files[str(index_ref["path"])] = index_payload
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
            "subject_index": index_ref,
        }
    )
    return PreparedState(next_manifest, files)
