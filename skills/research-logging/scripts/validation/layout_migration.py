"""Temporary storage-only migration from the Phase 8 validation layout."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import sharded_state, target_records
from .records import _atomic_write_bytes

OLD_MANIFEST = "validation-record.json"
OLD_STATE = "validation-state"
OLD_CACHE = "validation-cache.json"
OLD_LOCK = ".research-log-validation.lock"


class LayoutMigrationError(ValueError):
    """Raised when a storage-only migration cannot be proved safe."""


@dataclass(frozen=True)
class MigrationProjection:
    """Validated old authority and its exact final-layout projection."""

    old_manifest: dict[str, Any]
    new_manifest: dict[str, Any]
    logical_record: dict[str, Any]
    files: dict[str, bytes]
    cache: dict[str, Any]
    index: dict[str, Any]


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _read_json(path: Path, description: str) -> dict[str, Any]:
    if path.is_symlink():
        raise LayoutMigrationError(f"{description} must not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LayoutMigrationError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayoutMigrationError(f"{description} must be an object")
    return value


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _old_owned_file(output_dir: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.as_posix() != relative
        or ".." in pure.parts
        or not relative.startswith(f"{OLD_STATE}/")
    ):
        raise LayoutMigrationError("old validation state path is not owned")
    path = output_dir / relative
    current = output_dir
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise LayoutMigrationError("old validation state contains a symlink")
    try:
        path.resolve().relative_to(output_dir.resolve())
    except ValueError as exc:
        raise LayoutMigrationError("old validation state escapes its log") from exc
    return path


def _project_ref(
    output_dir: Path, kind: str, value: Any
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, Mapping):
        raise LayoutMigrationError(f"old {kind} shard reference must be an object")
    ref = dict(value)
    identity = ref.get("sha256")
    expected_old = f"{OLD_STATE}/{kind}/{identity}.jsonl"
    if ref.get("kind") != kind or ref.get("path") != expected_old:
        raise LayoutMigrationError("old shard path does not match its identity")
    path = _old_owned_file(output_dir, expected_old)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise LayoutMigrationError(f"cannot read old shard {path}: {exc}") from exc
    if len(payload) != ref.get("byte_count") or _digest(payload) != identity:
        raise LayoutMigrationError("old shard bytes disagree with its reference")
    try:
        rows = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LayoutMigrationError("old shard is not valid UTF-8 JSONL") from exc
    if len(rows) != ref.get("row_count") or not all(
        isinstance(row, dict) for row in rows
    ):
        raise LayoutMigrationError("old shard row count or shape disagrees")
    projected = copy.deepcopy(ref)
    projected["path"] = f"{kind}/{identity}.jsonl"
    return projected, payload


def _normalize_old_index(
    output_dir: Path,
    descriptor: Any,
    new_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(descriptor, Mapping) or set(descriptor) != {
        "path",
        "sha256",
        "byte_count",
        "subject_count",
    }:
        raise LayoutMigrationError("old subject-index descriptor is malformed")
    identity = descriptor.get("sha256")
    relative = f"{OLD_STATE}/index/{identity}.json"
    if descriptor.get("path") != relative:
        raise LayoutMigrationError("old subject-index path disagrees with identity")
    path = _old_owned_file(output_dir, relative)
    payload = path.read_bytes()
    if len(payload) != descriptor.get("byte_count") or _digest(payload) != identity:
        raise LayoutMigrationError("old subject-index bytes disagree with descriptor")
    old_index = _read_json(path, "old subject index")
    if (
        set(old_index) != {"schema_version", "subjects"}
        or old_index.get("schema_version") != 1
    ):
        raise LayoutMigrationError("old subject index has an unsupported schema")
    subjects = _project_index_subjects(old_index.get("subjects"))
    normalized = {
        "schema_version": sharded_state.INDEX_SCHEMA_VERSION,
        "summary": new_manifest["summary"],
        "closure_identity": sharded_state.manifest_closure_identity(new_manifest),
        "sequence": 0,
        "indexed_shards": sorted(
            str(ref["path"])
            for kind in sharded_state.SUBJECT_KINDS
            for ref in new_manifest["shards"][kind]
        ),
        "subjects": subjects,
    }
    valid = sharded_state._validate_index(normalized, new_manifest)
    subject_count = sum(len(entries) for entries in valid["subjects"].values())
    if subject_count != descriptor.get("subject_count"):
        raise LayoutMigrationError("old subject-index count disagrees with mappings")
    return valid


def _project_index_subjects(value: Any) -> dict[str, Any]:
    subjects = copy.deepcopy(value)
    if not isinstance(subjects, dict):
        raise LayoutMigrationError("old subject index mappings must be an object")
    for entries in subjects.values():
        if not isinstance(entries, list):
            raise LayoutMigrationError("old subject index bucket must be an array")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("shards"), dict):
                raise LayoutMigrationError("old subject index entry is malformed")
            for kind, paths in entry["shards"].items():
                prefix = f"{OLD_STATE}/{kind}/"
                if not isinstance(paths, list) or any(
                    not isinstance(item, str) or not item.startswith(prefix)
                    for item in paths
                ):
                    raise LayoutMigrationError("old subject index names invalid shards")
                entry["shards"][kind] = [
                    item.removeprefix(f"{OLD_STATE}/") for item in paths
                ]
    return subjects


def _cache_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    cache = target_records.empty_cache()
    for field in ("completion_dependencies",):
        dependencies = record.get(field, [])
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, Mapping):
                continue
            path = dependency.get("path")
            identity = dependency.get("identity")
            if not isinstance(path, str) or not isinstance(identity, Mapping):
                continue
            previous = cache["files"].get(path)
            if previous is not None and previous != identity:
                raise LayoutMigrationError("completion cache identities conflict")
            cache["files"][path] = copy.deepcopy(dict(identity))
    return target_records.decode_cache(cache)


def _validate_old_header(old: Mapping[str, Any], expected_summary: str) -> None:
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
        "subject_index",
    }
    if set(old) != expected_fields or old.get("summary") != expected_summary:
        raise LayoutMigrationError("old manifest fields or summary ownership disagree")
    if old.get("continuation") is not None:
        raise LayoutMigrationError(
            "active Phase 8 continuation requires a controlled session relocation"
        )


def _project_shards(
    output_dir: Path, old_shards: Any
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, bytes],
]:
    if not isinstance(old_shards, Mapping) or set(old_shards) != set(
        sharded_state.ROW_KINDS
    ):
        raise LayoutMigrationError("old manifest shard collections are malformed")
    files: dict[str, bytes] = {}
    refs: dict[str, list[dict[str, Any]]] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for kind in sharded_state.ROW_KINDS:
        raw_refs = old_shards[kind]
        if not isinstance(raw_refs, list):
            raise LayoutMigrationError("old manifest shard collection must be an array")
        refs[kind] = []
        rows[kind] = []
        for raw_ref in raw_refs:
            ref, payload = _project_ref(output_dir, kind, raw_ref)
            refs[kind].append(ref)
            files[str(ref["path"])] = payload
            rows[kind].extend(
                json.loads(line) for line in payload.decode("utf-8").splitlines()
            )
    return refs, rows, files


def _logical_record(
    manifest: Mapping[str, Any], rows: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    logical = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key not in {"storage_layout", "shards", "row_counts"}
    }
    logical.update(rows)
    return target_records.decode_record(logical)


def _expected_subjects(
    refs: Mapping[str, list[dict[str, Any]]],
    rows: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for kind in sharded_state.SUBJECT_KINDS:
        offset = 0
        for ref in refs[kind]:
            count = int(ref["row_count"])
            sharded_state._add_index_rows(
                expected, kind, ref, rows[kind][offset : offset + count]
            )
            offset += count
    return expected


def project_old_layout(output_dir: Path, expected_summary: str) -> MigrationProjection:
    """Validate and project one Phase 8 bundle without writing any file."""

    old = _read_json(output_dir / OLD_MANIFEST, "old validation manifest")
    _validate_old_header(old, expected_summary)
    refs, rows, files = _project_shards(output_dir, old.get("shards"))
    new = {
        key: copy.deepcopy(value)
        for key, value in old.items()
        if key != "subject_index"
    }
    new["shards"] = refs
    new = target_records.decode_sharded_manifest(new)
    logical = _logical_record(new, rows)
    old_index = _normalize_old_index(output_dir, old["subject_index"], new)
    expected_subjects = _expected_subjects(refs, rows)
    if old_index["subjects"] != expected_subjects:
        raise LayoutMigrationError(
            "old stable-subject mapping disagrees with authoritative rows"
        )
    cache_path = output_dir / OLD_CACHE
    try:
        cache = target_records.decode_cache(_read_json(cache_path, "old cache"))
    except (FileNotFoundError, LayoutMigrationError, target_records.TargetRecordError):
        cache = _cache_from_record(logical)
    return MigrationProjection(old, new, logical, files, cache, old_index)


def _logical_new(output_dir: Path, expected_summary: str) -> dict[str, Any]:
    return target_records.load_record(
        target_records.manifest_path(output_dir), expected_summary=expected_summary
    )


def _equivalent_new(output_dir: Path, projection: MigrationProjection) -> None:
    manifest_path = target_records.manifest_path(output_dir)
    new = _read_json(manifest_path, "new validation manifest")
    if target_records.decode_sharded_manifest(new) != projection.new_manifest:
        raise LayoutMigrationError("old and new manifests are not exact projections")
    if (
        _logical_new(output_dir, projection.new_manifest["summary"])
        != projection.logical_record
    ):
        raise LayoutMigrationError("old and new logical validation states differ")


def _cleanup_old(output_dir: Path) -> None:
    for name in (OLD_MANIFEST, OLD_CACHE, OLD_LOCK):
        path = output_dir / name
        if path.is_symlink():
            raise LayoutMigrationError("refusing to remove a symlinked old artifact")
        path.unlink(missing_ok=True)
    state = output_dir / OLD_STATE
    if state.exists():
        if state.is_symlink() or not state.is_dir():
            raise LayoutMigrationError("old validation state directory is unsafe")
        shutil.rmtree(state)


def migrate_layout(output_dir: Path, expected_summary: str) -> dict[str, Any]:
    """Migrate one validated bundle, with the new manifest as commit point."""

    projection = project_old_layout(output_dir, expected_summary)
    validation_dir = target_records.validation_directory(output_dir)
    manifest_path = target_records.manifest_path(output_dir)
    if manifest_path.exists():
        _equivalent_new(output_dir, projection)
    else:
        sharded_state.publish_immutable_files(
            validation_dir, projection.files, _atomic_write_bytes
        )
        _atomic_write_bytes(manifest_path, _json_bytes(projection.new_manifest))
        _equivalent_new(output_dir, projection)
    _atomic_write_bytes(
        target_records.cache_path(output_dir), _json_bytes(projection.cache)
    )
    sharded_state.compact_subject_index(
        validation_dir, projection.new_manifest, _atomic_write_bytes
    )
    index = sharded_state.ensure_subject_index(validation_dir, projection.new_manifest)
    if index["subjects"] != projection.index["subjects"]:
        raise LayoutMigrationError("rebuilt local subject index is not equivalent")
    _cleanup_old(output_dir)
    return inventory(output_dir, expected_summary)


def _tree_inventory(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "bytes": path.stat().st_size,
                    }
                )
    return {
        "files": files,
        "file_count": len(files),
        "bytes": sum(item["bytes"] for item in files),
    }


def _git_status(project_root: Path, output_dir: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--", output_dir.as_posix()],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return completed.stdout.splitlines()


def inventory(output_dir: Path, expected_summary: str) -> dict[str, Any]:
    """Return bounded migration evidence for one log's validation storage."""

    project_root = output_dir
    while project_root != project_root.parent and not (project_root / ".git").exists():
        project_root = project_root.parent
    old_path = output_dir / OLD_MANIFEST
    new_path = target_records.manifest_path(output_dir)
    manifest_path = new_path if new_path.is_file() else old_path
    manifest = _read_json(manifest_path, "validation manifest")
    if manifest.get("summary") != expected_summary:
        raise LayoutMigrationError("inventory summary ownership disagrees")
    index_dir = output_dir / OLD_STATE / "index"
    continuation = manifest.get("continuation")
    return {
        "summary": expected_summary,
        "layout": "final" if new_path.is_file() else "phase8",
        "manifest": manifest_path.relative_to(output_dir).as_posix(),
        "manifest_sha256": _digest(manifest_path.read_bytes()),
        "row_counts": copy.deepcopy(manifest.get("row_counts")),
        "result": copy.deepcopy(manifest.get("result")),
        "projection": copy.deepcopy(manifest.get("projection")),
        "continuation": copy.deepcopy(continuation),
        "accepted_fragments": 0 if continuation is None else "active-session",
        "old_state": _tree_inventory(output_dir / OLD_STATE),
        "final_state": _tree_inventory(output_dir / sharded_state.STATE_DIRECTORY),
        "old_index": _tree_inventory(index_dir),
        "old_cache_bytes": (output_dir / OLD_CACHE).stat().st_size
        if (output_dir / OLD_CACHE).is_file()
        else 0,
        "old_lock": (output_dir / OLD_LOCK).exists(),
        "git_status": _git_status(project_root, output_dir),
    }
