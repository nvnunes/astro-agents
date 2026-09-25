"""Strict shared contracts for entry-local ``data.json`` input registries."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from fnmatch import fnmatchcase
from glob import has_magic
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, cast

from validation.entry_materials import (
    EntryMaterialPathError,
    is_entry_material_root,
    validate_local_path_symlinks,
)
from validation.errors import MechanicalContractError
from validation.filesystem import (
    BoundedFileReadError,
    BoundedTraversalError,
    bounded_descendants,
    bounded_file_bytes,
)
from validation.json_codec import V2JsonError, canonical_json, decode_json

DATA_SCHEMA = "research-log-data/v6"
EVIDENCE_COMPARISON_CONTRACT = "research-log-evidence-scoped-comparison/1"
_MISSING = object()
DIRECTORY_FINGERPRINT_SCHEMA = "research-log-directory-fingerprint/1"
DIRECTORY_OBSERVATION_SCHEMA = "research-log-directory-observation/1"
IDENTITY_FILES_FINGERPRINT_SCHEMA = "research-log-identity-files-fingerprint/1"
IDENTITY_PATTERNS_FINGERPRINT_SCHEMA = "research-log-identity-patterns-fingerprint/1"
MAX_DATA_FILE_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 10_000
MAX_NAME_BYTES = 96
MAX_LOCATION_BYTES = 2_048
MAX_DIRECTORY_ENTRIES = 100_000
MAX_DIRECTORY_PATH_BYTES = 512
MAX_DIRECTORY_CONTENT_BYTES = 1024**4
MAX_IDENTITY_FILES = 64
MAX_IDENTITY_PATTERNS = 64
MAX_IDENTITY_PATTERN_CANDIDATES = 100_000
HASH_CHUNK_BYTES = 1024 * 1024

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
ENTRY_REFERENCE_NAME_RE = re.compile(r"e[0-9]+\Z", re.IGNORECASE)
ENTRY_REFERENCE_DIRECTORY_RE = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})-"
    r"(?P<id>e[0-9]{3,})-[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
INPUT_TOKEN_RE = re.compile(
    r"<(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)"
    r"(?::(?P<projection>commit))?>(?:/(?P<member>.+))?\Z"
)
INPUT_TOKEN_CANDIDATE_RE = re.compile(r"<[A-Za-z0-9][A-Za-z0-9_-]*(?::commit)?>")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
RESERVED_NAMES = frozenset({"log", "project", "theme"})
GIT_COMMIT_ALGORITHM = "git-commit-sha1-v1"


class DataContractError(MechanicalContractError):
    """One precise data-registry contract failure."""


@dataclass(frozen=True)
class ReproductionComparison:
    """One explicit artifact-level reproduction comparison policy."""

    contract: str
    profile: str

    def as_dict(self) -> dict[str, str]:
        """Return the exact authored comparison declaration."""

        return {"contract": self.contract, "profile": self.profile}


@dataclass(frozen=True)
class Fingerprint:
    """One local-resource material fingerprint."""

    algorithm: str
    digest: str | None = None
    files: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return the canonical fingerprint object."""

        value: dict[str, object] = {"algorithm": self.algorithm}
        if self.digest is not None:
            value["digest"] = self.digest
        if self.files:
            value["files"] = list(self.files)
        if self.patterns:
            value["patterns"] = list(self.patterns)
        return value

    @property
    def content_identity(self) -> str:
        """Return the stable canonical fingerprint identity."""

        return _identity(self.as_dict())


@dataclass(frozen=True)
class ResourceIdentity:
    """The authored rule for observing one resource, never accepted bytes."""

    algorithm: str
    commit: str | None = None
    files: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return the closed declaration representation."""

        value: dict[str, object] = {"algorithm": self.algorithm}
        if self.commit is not None:
            value["commit"] = self.commit
        if self.files:
            value["files"] = list(self.files)
        if self.patterns:
            value["patterns"] = list(self.patterns)
        return value


def parse_fingerprint(
    value: object, subject: str, *, kind: object | None = None
) -> Fingerprint:
    """Decode a retained execution observation, which always includes bytes."""

    if not isinstance(value, Mapping):
        _invalid(subject, {"fingerprint": value})
    value = cast(Mapping[str, Any], value)
    algorithm = value.get("algorithm")
    if kind is None:
        kind = (
            "file"
            if algorithm == "sha256"
            else "git-repository"
            if algorithm == GIT_COMMIT_ALGORITHM
            else "directory"
        )
    digest = value.get("digest")
    if not isinstance(digest, str) or (
        GIT_COMMIT_RE.fullmatch(digest) is None
        if algorithm == GIT_COMMIT_ALGORITHM
        else DIGEST_RE.fullmatch(digest) is None
    ):
        _invalid(subject, {"fingerprint": dict(value)})
    if (
        algorithm == "sha256"
        and set(value) == {"algorithm", "digest"}
        and kind == "file"
    ):
        return Fingerprint(algorithm, digest=digest)
    if (
        algorithm == "directory-sha256-v1"
        and set(value) == {"algorithm", "digest"}
        and kind == "directory"
    ):
        return Fingerprint(algorithm, digest=digest)
    if (
        algorithm == GIT_COMMIT_ALGORITHM
        and set(value) == {"algorithm", "digest"}
        and kind == "git-repository"
    ):
        return Fingerprint(algorithm, digest=digest)
    if (
        algorithm == "identity-files-sha256-v1"
        and kind == "directory"
        and set(value) == {"algorithm", "digest", "files"}
    ):
        return Fingerprint(
            algorithm,
            digest=digest,
            files=_identity_files(value.get("files"), subject),
        )
    if (
        algorithm == "identity-patterns-sha256-v1"
        and kind == "directory"
        and set(value) == {"algorithm", "digest", "patterns"}
    ):
        return Fingerprint(
            algorithm,
            digest=digest,
            patterns=_identity_patterns(value.get("patterns"), subject),
        )
    _invalid(subject, {"fingerprint": dict(value), "kind": kind})


@dataclass(frozen=True)
class DirectoryFingerprintEntry:
    """One canonical directory fingerprint member."""

    path: str
    type: str
    sha256: str | None = None

    def as_dict(self) -> dict[str, str]:
        """Return the canonical member object."""

        value = {"path": self.path, "type": self.type}
        if self.sha256 is not None:
            value["sha256"] = self.sha256
        return value


@dataclass(frozen=True)
class FingerprintObservation:
    """One observed fingerprint and optional directory membership."""

    fingerprint: Fingerprint
    entries: tuple[DirectoryFingerprintEntry, ...] = ()
    cache_identity: Mapping[str, object] | None = field(default=None, compare=False)
    identity_reused: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class InputResource:
    """One parsed entry-owned material input declaration."""

    name: str
    kind: str
    location: str
    identity: ResourceIdentity
    origin: bool
    canonical_target: str
    comparison: ReproductionComparison | None = None
    reference_entry: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return authored canonical fields without resolved observations."""

        if self.reference_entry is not None:
            return {"from_entry": self.reference_entry, "name": self.name}
        value: dict[str, object] = {
            "identity": self.identity.as_dict(),
            "kind": self.kind,
            "location": self.location,
            "name": self.name,
            "origin": self.origin,
        }
        if self.comparison is not None:
            value["reproduction_comparison"] = self.comparison.as_dict()
        return value

    @property
    def content_identity(self) -> str:
        """Return the stable declaration identity."""

        return _identity(self.as_dict())

    @property
    def material_identity(self) -> str:
        """Return the location-independent graph identity of this material."""

        if self.kind == "git-repository":
            return f"{GIT_COMMIT_ALGORITHM}:{self.identity.commit}"
        return self.canonical_target

    @property
    def observation_identity(self) -> str:
        """Return the locator and expected-identity key for one observation."""

        if self.kind == "git-repository":
            return f"{self.canonical_target}#{self.material_identity}"
        return self.canonical_target


@dataclass(frozen=True)
class ResolvedInputToken:
    """One exact input token resolved to a resource or directory member."""

    resource: InputResource
    path: str
    value: str
    projection: str | None = None
    member: str | None = None


@dataclass(frozen=True)
class DataFile:
    """One validated entry-root input registry."""

    path: Path
    entry_root: Path
    inputs: tuple[InputResource, ...]

    @property
    def by_name(self) -> dict[str, InputResource]:
        """Return the exact entry-scoped name mapping."""

        return {item.name: item for item in self.inputs}

    @property
    def identity(self) -> str:
        """Return canonical content identity independent of array order."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        """Return canonical JSON with inputs ordered by name."""

        return canonical_json(
            {
                "inputs": [
                    item.as_dict()
                    for item in sorted(self.inputs, key=lambda value: value.name)
                ],
                "schema": DATA_SCHEMA,
            }
        )


@dataclass(frozen=True)
class DataDeclarationConflict:
    """One canonical target with incompatible declarations in a maintained log."""

    canonical_target: str
    data_files: tuple[Path, ...]

    @property
    def error(self) -> DataContractError:
        """Return the contract error representing this complete conflict group."""

        return DataContractError(
            "data.declaration.conflict",
            self.canonical_target,
            {"files": [str(path) for path in self.data_files]},
            DATA_SCHEMA,
        )


def load_data_file(path: Path, *, entry_root: Path) -> DataFile:
    """Read one strict entry-root ``data.json`` declaration."""

    return _load_data_file(path, entry_root=entry_root, loading=frozenset())


def load_data_file_with_location_repairs(
    path: Path, *, entry_root: Path, locations: Mapping[str, str]
) -> DataFile:
    """Decode a candidate with named symlink aliases replaced by the same targets.

    Only selected declarations whose current location fails the symlink rule
    may change. Every replacement must identify the identical canonical
    material, and the complete resulting registry must pass normal decoding.
    """

    if not locations:
        _invalid(path, {"reason": "repair_empty"})
    return _load_data_file(
        path,
        entry_root=entry_root,
        loading=frozenset(),
        location_repairs=locations,
    )


def _load_data_file(
    path: Path,
    *,
    entry_root: Path,
    loading: frozenset[Path],
    location_repairs: Mapping[str, str] | None = None,
) -> DataFile:
    """Resolve one data file and its bounded cross-entry references."""

    entry_root_symlink = entry_root.is_symlink()
    entry_root = entry_root.resolve()
    expected = entry_root / "data.json"
    if path.resolve() != expected.resolve() or path.is_symlink() or entry_root_symlink:
        _fail(
            "data.file.location_invalid",
            str(path),
            {"expected": str(expected)},
            "Ownership And Completeness",
        )
    value = _read_json(path)
    if not isinstance(value, Mapping) or set(value) != {"schema", "inputs"}:
        _invalid(path, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    raw_inputs = value.get("inputs")
    schema = value.get("schema")
    if schema != DATA_SCHEMA or not isinstance(raw_inputs, list):
        _invalid(path, {"schema": value.get("schema")})
    if not raw_inputs or len(raw_inputs) > MAX_INPUTS:
        _invalid(path, {"inputs": len(raw_inputs)})
    canonical = expected.resolve()
    if canonical in loading:
        _invalid(path, {"reason": "reference_cycle"})
    nested = loading | {canonical}
    if location_repairs is not None:
        present = {
            name
            for raw in raw_inputs
            if isinstance(raw, Mapping)
            and isinstance(name := raw.get("name"), str)
        }
        missing = sorted(set(location_repairs) - present)
        if missing:
            _invalid(path, {"reason": "repair_name_missing", "names": missing})
    inputs_list: list[InputResource] = []
    for index, raw in enumerate(raw_inputs):
        subject = f"{path}:inputs[{index}]"
        name = raw.get("name") if isinstance(raw, Mapping) else None
        if (
            location_repairs is not None
            and isinstance(name, str)
            and name in location_repairs
        ):
            inputs_list.append(
                _repair_input_location(
                    raw,
                    subject,
                    entry_root,
                    location_repairs[name],
                    loading=nested,
                )
            )
        else:
            inputs_list.append(
                _decode_input(
                    raw,
                    subject,
                    entry_root,
                    loading=nested,
                )
            )
    inputs = tuple(inputs_list)
    _require_unique_inputs(inputs, path)
    return DataFile(path=expected, entry_root=entry_root, inputs=inputs)


def _repair_input_location(
    raw: Mapping[str, Any],
    subject: str,
    entry_root: Path,
    location: str,
    *,
    loading: frozenset[Path],
) -> InputResource:
    """Admit only a symlink-path correction to the same existing material."""

    if set(raw) == {"from_entry", "name"}:
        _invalid(subject, {"reason": "repair_reference", "name": raw["name"]})
    try:
        _decode_input(raw, subject, entry_root, loading=loading)
    except DataContractError as error:
        if (
            error.code != "data.declaration.invalid"
            or not isinstance(error.observed, Mapping)
            or error.observed.get("reason") != "symlink"
        ):
            raise
    else:
        _invalid(subject, {"reason": "repair_not_needed", "name": raw["name"]})
    original_location = raw["location"]
    assert isinstance(original_location, str)
    try:
        original_target = (entry_root / original_location).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        _invalid(
            subject,
            {
                "reason": "repair_target_unavailable",
                "location": original_location,
                "error": str(error),
            },
        )
    candidate = _decode_input(
        {**raw, "location": location}, subject, entry_root, loading=loading
    )
    if candidate.canonical_target != original_target.as_posix():
        _invalid(
            subject,
            {
                "reason": "repair_target_changed",
                "current": original_target.as_posix(),
                "replacement": candidate.canonical_target,
            },
        )
    return candidate


def validate_log_consistency(data_files: tuple[DataFile, ...]) -> None:
    """Reject incompatible declarations of one target within a maintained log."""

    conflicts = find_log_consistency_conflicts(data_files)
    if conflicts:
        raise conflicts[0].error


def find_log_consistency_conflicts(
    data_files: tuple[DataFile, ...],
) -> tuple[DataDeclarationConflict, ...]:
    """Return every target-level declaration conflict in deterministic order."""

    declarations: dict[str, dict[str, set[Path]]] = {}
    for data_file in data_files:
        for item in data_file.inputs:
            projections = declarations.setdefault(item.material_identity, {})
            projections.setdefault(_consistency_projection(item), set()).add(
                data_file.path
            )
    return tuple(
        DataDeclarationConflict(
            target,
            tuple(
                sorted(
                    {path for owners in projections.values() for path in owners},
                    key=lambda path: path.as_posix(),
                )
            ),
        )
        for target, projections in sorted(declarations.items())
        if len(projections) > 1
    )


def build_local_input(
    name: str,
    kind: str,
    location: str,
    *,
    entry_root: Path,
    origin: bool = False,
) -> InputResource:
    """Build one local declaration with its observation rule."""

    algorithm = "sha256" if kind == "file" else "directory-sha256-v1"
    provisional = _decode_input(
        {
            "name": name,
            "kind": kind,
            "location": location,
            "origin": origin,
            "identity": {"algorithm": algorithm},
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    return provisional


def build_declared_generated(
    name: str,
    kind: str,
    location: str,
    *,
    entry_root: Path,
    identity: tuple[str, ...] | None = None,
) -> InputResource:
    """Build one generated artifact declaration before production."""

    if kind not in {"file", "directory"}:
        _invalid(name, {"kind": kind})
    if identity and kind != "directory":
        _invalid(name, {"identity": list(identity), "kind": kind})
    if identity:
        pattern_selected = any(has_magic(selector) for selector in identity)
        declaration_identity = ResourceIdentity(
            (
                "identity-patterns-sha256-v1"
                if pattern_selected
                else "identity-files-sha256-v1"
            ),
            files=() if pattern_selected else identity,
            patterns=identity if pattern_selected else (),
        )
    else:
        declaration_identity = ResourceIdentity(
            "sha256" if kind == "file" else "directory-sha256-v1"
        )
    return _decode_input(
        {
            "name": name,
            "kind": kind,
            "location": location,
            "origin": False,
            "identity": declaration_identity.as_dict(),
        },
        f"input:{name}",
        entry_root.resolve(),
    )


def build_git_repository_input(
    name: str,
    location: str,
    commit: str,
    *,
    entry_root: Path,
) -> InputResource:
    """Build one origin repository declaration pinned to an exact commit."""

    provisional = _decode_input(
        {
            "name": name,
            "kind": "git-repository",
            "location": location,
            "origin": True,
            "identity": {
                "algorithm": GIT_COMMIT_ALGORITHM,
                "commit": commit,
            },
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    return provisional


def normalize_input_location(value: str, *, entry_root: Path) -> str:
    """Return one canonical authored location for an existing local target.

    Relative inputs remain relative to the entry. Absolute paths beneath the
    entry, including its first-class ``data`` and ``images`` links, become
    entry-relative. Other absolute inputs use their safely resolved path.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_LOCATION_BYTES
        or "\\" in value
        or "://" in value
        or "<" in value
        or ">" in value
    ):
        _invalid("input location", {"location": value})
    normalized = posixpath.normpath(value)
    if normalized in {"", "."}:
        _invalid("input location", {"location": value})
    root = entry_root.resolve()
    if not normalized.startswith("/"):
        _location(normalized, "input location", root)
        return normalized
    lexical = Path(normalized)
    relative = _entry_relative_location(lexical.absolute(), root)
    if relative is not None:
        return relative
    canonical = lexical.resolve()
    relative = _entry_relative_location(canonical, root)
    if relative is not None:
        return relative
    linked = _linked_material_location(canonical, root)
    if linked is not None:
        return linked
    result = canonical.as_posix()
    _location(result, "input location", root)
    return result


def _entry_relative_location(path: Path, root: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    result = PurePosixPath(*relative.parts).as_posix()
    _location(result, "input location", root)
    return result


def _linked_material_location(canonical: Path, root: Path) -> str | None:
    for name in ("data", "images"):
        material_root = root / name
        if not material_root.is_symlink():
            continue
        try:
            member = canonical.relative_to(material_root.resolve())
        except ValueError:
            continue
        result = (PurePosixPath(name) / PurePosixPath(*member.parts)).as_posix()
        _location(result, "input location", root)
        return result
    return None


def build_identity_directory(
    name: str,
    location: str,
    identity_files: tuple[str, ...],
    *,
    entry_root: Path,
    origin: bool = False,
) -> InputResource:
    """Build one managed local directory from exact authoritative files."""

    provisional = _decode_input(
        {
            "name": name,
            "kind": "directory",
            "location": location,
            "origin": origin,
            "identity": {
                "algorithm": "identity-files-sha256-v1",
                "files": list(identity_files),
            },
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    return provisional


def build_identity_pattern_directory(
    name: str,
    location: str,
    identity_patterns: tuple[str, ...],
    *,
    entry_root: Path,
    origin: bool = False,
) -> InputResource:
    """Build one managed local directory from bounded file selectors."""

    provisional = _decode_input(
        {
            "name": name,
            "kind": "directory",
            "location": location,
            "origin": origin,
            "identity": {
                "algorithm": "identity-patterns-sha256-v1",
                "patterns": list(identity_patterns),
            },
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    return provisional


def data_file_from_inputs(
    path: Path,
    *,
    entry_root: Path,
    inputs: tuple[InputResource, ...],
) -> DataFile:
    """Build one deterministic data file after checking declaration uniqueness."""

    if not inputs:
        _invalid(path, {"inputs": 0})
    entry_root = entry_root.resolve()
    decoded = tuple(
        _decode_input(item.as_dict(), f"{path}:inputs[{index}]", entry_root)
        for index, item in enumerate(inputs)
    )
    _require_unique_inputs(decoded, path)
    return DataFile(path=path, entry_root=entry_root, inputs=decoded)


def data_file_from_fields(
    path: Path, *, entry_root: Path, fields: Mapping[str, object]
) -> DataFile:
    """Decode accepted resolved data/v6 declarations without filesystem reads.

    Accepted fields use direct resources only; a cross-entry source is retained
    as normal resource fields plus its resolved ``canonical_target``.
    """

    if set(fields) != {"schema", "inputs"} or fields.get("schema") != DATA_SCHEMA:
        _invalid(path, {"fields": sorted(fields)})
    raw_inputs = fields.get("inputs")
    if (
        not isinstance(raw_inputs, list)
        or not raw_inputs
        or len(raw_inputs) > MAX_INPUTS
    ):
        _invalid(path, {"inputs": raw_inputs})
    root = entry_root.absolute()
    inputs: list[InputResource] = []
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, Mapping):
            _invalid(path, {"input": index})
        value = cast(Mapping[str, Any], raw)
        required = {
            "name",
            "kind",
            "location",
            "identity",
            "origin",
            "canonical_target",
            "reference_entry",
        }
        allowed = required | {"reproduction_comparison"}
        if not required <= set(value) <= allowed:
            _invalid(path, {"input": index, "fields": sorted(value)})
        target = value["canonical_target"]
        if not isinstance(target, str) or not Path(target).is_absolute():
            _invalid(path, {"input": index, "target": target})
        direct = {
            key: value[key]
            for key in {
                "name",
                "kind",
                "location",
                "identity",
                "origin",
                "reproduction_comparison",
            }
            if key in value
        }
        decoded = _decode_input(
            direct, f"{path}:inputs[{index}]", root, accepted_target=target
        )
        reference = value["reference_entry"]
        if reference is not None and (
            not isinstance(reference, str)
            or ENTRY_REFERENCE_NAME_RE.fullmatch(reference) is None
        ):
            _invalid(path, {"input": index, "reference_entry": reference})
        inputs.append(replace(decoded, reference_entry=reference))
    resolved_inputs = tuple(inputs)
    _require_unique_inputs(resolved_inputs, path)
    return DataFile(path, root, resolved_inputs)


def resolve_input_token(value: str, data_file: DataFile | None) -> ResolvedInputToken:
    """Resolve one complete locator, commit, or directory-member input token."""

    parts = input_token_parts(value)
    if parts is None:
        _fail(
            "data.input.undeclared",
            value,
            {"reason": "invalid_token"},
            "Command Tokens And Roles",
        )
    name, projection, member = parts
    resource = data_file.by_name.get(name) if data_file is not None else None
    if resource is None:
        _fail(
            "data.input.undeclared",
            value,
            {"name": name},
            "Command Tokens And Roles",
        )
    if projection == "commit":
        if resource.kind != "git-repository" or member is not None:
            _invalid(
                value,
                {"projection": projection, "resource": resource.name},
            )
        assert resource.identity.commit is not None
        return ResolvedInputToken(
            resource,
            resource.material_identity,
            resource.identity.commit,
            projection="commit",
        )
    if member is None:
        return ResolvedInputToken(
            resource,
            resource.material_identity,
            resource.canonical_target,
        )
    path = _resolve_member(resource, member, value)
    return ResolvedInputToken(resource, path, path, member=member)


def input_token_parts(value: str) -> tuple[str, str | None, str | None] | None:
    """Return the name, projection, and member of one complete input token."""

    match = INPUT_TOKEN_RE.fullmatch(value)
    name = match.group("name") if match is not None else None
    projection = match.group("projection") if match is not None else None
    member = match.group("member") if match is not None else None
    if (
        match is None
        or name in RESERVED_NAMES
        or not isinstance(name, str)
        or len(name.encode("ascii")) > MAX_NAME_BYTES
        or ENTRY_REFERENCE_NAME_RE.fullmatch(name) is not None
        or projection is not None
        and member is not None
        or member is not None
        and not _valid_input_member(member)
    ):
        return None
    return name, projection, member


def require_git_repository_token_pairs(
    values: tuple[str, ...], data_file: DataFile | None
) -> None:
    """Require each consumed repository to expose locator and commit projections."""

    projections: dict[str, set[str]] = {}
    for value in values:
        parts = input_token_parts(value)
        if parts is None:
            continue
        name, projection, _ = parts
        resource = data_file.by_name.get(name) if data_file is not None else None
        if resource is None or resource.kind != "git-repository":
            continue
        projections.setdefault(name, set()).add(projection or "locator")
    for name, observed in sorted(projections.items()):
        required = {"commit", "locator"}
        if observed != required:
            _fail(
                "data.git.projection_missing",
                name,
                {"missing": sorted(required - observed)},
                "Command Tokens And Roles",
            )


def _valid_input_member(member: str) -> bool:
    pure = PurePosixPath(member)
    return not (
        not member
        or len(member.encode("utf-8")) > MAX_DIRECTORY_PATH_BYTES
        or pure.is_absolute()
        or "\\" in member
        or "://" in member
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != member
    )


def input_token_candidate(value: str) -> bool:
    """Return whether a command argument contains data-token syntax."""

    return INPUT_TOKEN_CANDIDATE_RE.search(value) is not None


def observe_fingerprint(resource: InputResource) -> FingerprintObservation:
    """Observe one accessible local input without changing its declaration."""
    path = Path(resource.canonical_target)
    if path.is_symlink():
        _fail(
            "data.declaration.invalid",
            resource.name,
            {"location": resource.location, "reason": "symlink"},
            DATA_SCHEMA,
        )
    if resource.kind == "git-repository":
        return _observe_git_repository(resource, path)
    if resource.kind == "file":
        if not path.is_file():
            _target_missing(resource, "not_regular_file")
        digest, identity = _hash_file_observation(path)
        return FingerprintObservation(
            Fingerprint("sha256", digest=digest), cache_identity=identity
        )
    if not path.is_dir():
        _target_missing(resource, "not_directory")
    if resource.identity.algorithm == "identity-files-sha256-v1":
        paths = identity_file_paths(resource)
        identity_entries: list[DirectoryFingerprintEntry] = []
        identities: list[Mapping[str, object]] = []
        for relative, identity_path in paths.items():
            digest, identity = _hash_file_observation(identity_path)
            identity_entries.append(DirectoryFingerprintEntry(relative, "file", digest))
            identities.append({"path": relative, **identity})
        fingerprint = compose_identity_files_fingerprint(tuple(identity_entries))
        _require_unchanged_identity_files(paths, identities, resource.name)
        return FingerprintObservation(
            fingerprint,
            tuple(identity_entries),
            {"files": identities, "kind": "identity-files"},
        )
    if resource.identity.algorithm == "identity-patterns-sha256-v1":
        paths = identity_pattern_paths(resource)
        identity_entries = []
        identities = []
        for relative, identity_path in paths.items():
            digest, identity = _hash_file_observation(identity_path)
            identity_entries.append(DirectoryFingerprintEntry(relative, "file", digest))
            identities.append({"path": relative, **identity})
        fingerprint = compose_identity_patterns_fingerprint(
            tuple(identity_entries),
            resource.identity.patterns,
        )
        _require_unchanged_identity_patterns(resource, paths)
        _require_unchanged_identity_files(paths, identities, resource.name)
        return FingerprintObservation(
            fingerprint,
            tuple(identity_entries),
            {"files": identities, "kind": "identity-patterns"},
        )
    entries, digest, identity = _hash_directory(path)
    return FingerprintObservation(
        Fingerprint("directory-sha256-v1", digest=digest), entries, identity
    )


def observe_file_content(path: Path) -> tuple[str, Mapping[str, object]]:
    """Hash one stable regular file and return its content and metadata identity."""

    return _hash_file_observation(path)


def observe_directory_tree(
    root: Path,
) -> tuple[
    Mapping[str, object],
    tuple[DirectoryFingerprintEntry, ...],
    Mapping[str, Path],
]:
    """Return one bounded stable directory metadata and membership observation."""

    return _observe_directory_tree(root)


def compose_directory_fingerprint(
    entries: tuple[DirectoryFingerprintEntry, ...],
) -> Fingerprint:
    """Compose the normative directory fingerprint from ordered member identities."""

    payload = {
        "entries": [entry.as_dict() for entry in entries],
        "schema": DIRECTORY_FINGERPRINT_SCHEMA,
    }
    return Fingerprint(
        "directory-sha256-v1",
        digest=hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
    )


def compose_identity_files_fingerprint(
    entries: tuple[DirectoryFingerprintEntry, ...],
) -> Fingerprint:
    """Compose one managed-directory identity from exact declared files."""

    ordered = tuple(sorted(entries, key=lambda entry: entry.path.encode("utf-8")))
    files = tuple(entry.path for entry in ordered)
    if (
        not files
        or len(files) > MAX_IDENTITY_FILES
        or len(files) != len(set(files))
        or any(entry.type != "file" or entry.sha256 is None for entry in ordered)
    ):
        _invalid("identity-files", {"files": list(files)})
    payload = {
        "files": [{"path": entry.path, "sha256": entry.sha256} for entry in ordered],
        "schema": IDENTITY_FILES_FINGERPRINT_SCHEMA,
    }
    return Fingerprint(
        "identity-files-sha256-v1",
        digest=hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
        files=files,
    )


def compose_identity_patterns_fingerprint(
    entries: tuple[DirectoryFingerprintEntry, ...],
    patterns: tuple[str, ...],
) -> Fingerprint:
    """Compose one managed-directory identity from bounded file selectors."""

    ordered = tuple(sorted(entries, key=lambda entry: entry.path.encode("utf-8")))
    files = tuple(entry.path for entry in ordered)
    if (
        not files
        or len(files) > MAX_IDENTITY_FILES
        or len(files) != len(set(files))
        or any(entry.type != "file" or entry.sha256 is None for entry in ordered)
    ):
        _invalid("identity-patterns", {"files": list(files)})
    payload = {
        "files": [{"path": entry.path, "sha256": entry.sha256} for entry in ordered],
        "patterns": list(patterns),
        "schema": IDENTITY_PATTERNS_FINGERPRINT_SCHEMA,
    }
    return Fingerprint(
        "identity-patterns-sha256-v1",
        digest=hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
        patterns=patterns,
    )


def identity_file_paths(resource: InputResource) -> Mapping[str, Path]:
    """Resolve the exact identity files of one managed local directory."""

    if (
        resource.kind != "directory"
        or resource.identity.algorithm != "identity-files-sha256-v1"
    ):
        _invalid(resource.name, {"reason": "identity_files_not_applicable"})
    root = Path(resource.canonical_target)
    if root.is_symlink() or not root.is_dir():
        _target_missing(resource, "not_directory")
    result: dict[str, Path] = {}
    for relative in resource.identity.files:
        pure = PurePosixPath(relative)
        target = root.joinpath(*pure.parts)
        try:
            canonical = validate_local_path_symlinks(target, root)
        except EntryMaterialPathError as error:
            _invalid(resource.name, {"file": relative, "reason": error.reason})
        try:
            canonical.relative_to(root.resolve())
        except ValueError:
            _invalid(resource.name, {"file": relative, "reason": "escape"})
        if target.is_symlink() or not canonical.is_file():
            _fail(
                "data.target.missing",
                resource.name,
                {"file": relative, "reason": "not_regular_file"},
                "Identity Files",
            )
        result[relative] = canonical
    return result


def identity_pattern_paths(resource: InputResource) -> Mapping[str, Path]:
    """Resolve bounded exact and wildcard selectors to managed files."""

    if (
        resource.kind != "directory"
        or resource.identity.algorithm != "identity-patterns-sha256-v1"
    ):
        _invalid(resource.name, {"reason": "identity_patterns_not_applicable"})
    root = Path(resource.canonical_target)
    if root.is_symlink() or not root.is_dir():
        _target_missing(resource, "not_directory")
    resolved_root = root.resolve()
    result: dict[str, Path] = {}
    owners: dict[str, str] = {}
    wildcard_candidates: dict[Path, tuple[Path, ...]] = {}
    for pattern in resource.identity.patterns:
        for relative, canonical in _identity_pattern_matches(
            root,
            resolved_root,
            pattern,
            resource.name,
            wildcard_candidates,
        ):
            if relative in owners:
                _invalid(
                    resource.name,
                    {
                        "file": relative,
                        "patterns": [owners[relative], pattern],
                        "reason": "overlap",
                    },
                )
            owners[relative] = pattern
            result[relative] = canonical
            if len(result) > MAX_IDENTITY_FILES:
                _invalid(
                    resource.name,
                    {"files": len(result), "reason": "too_many_matches"},
                )
    return dict(sorted(result.items(), key=lambda item: item[0].encode("utf-8")))


def _identity_pattern_matches(
    root: Path,
    resolved_root: Path,
    pattern: str,
    subject: str,
    wildcard_candidates: dict[Path, tuple[Path, ...]],
) -> tuple[tuple[str, Path], ...]:
    pure = PurePosixPath(pattern)
    parent_relative = pure.parent
    parent = (
        root
        if parent_relative == PurePosixPath(".")
        else root.joinpath(*parent_relative.parts)
    )
    try:
        canonical_parent = validate_local_path_symlinks(parent, root)
        canonical_parent.relative_to(resolved_root)
    except (EntryMaterialPathError, ValueError) as error:
        _invalid(subject, {"pattern": pattern, "reason": _path_error_reason(error)})
    if parent.is_symlink() or not canonical_parent.is_dir():
        _fail(
            "data.target.missing",
            subject,
            {"pattern": pattern, "reason": "parent_not_directory"},
            "Identity Patterns",
        )
    if has_magic(pure.name):
        candidates = wildcard_candidates.get(canonical_parent)
        if candidates is None:
            candidates = _bounded_identity_pattern_candidates(canonical_parent, subject)
            wildcard_candidates[canonical_parent] = candidates
    else:
        candidates = (canonical_parent / pure.name,)
    matches = tuple(
        candidate for candidate in candidates if fnmatchcase(candidate.name, pure.name)
    )
    if not matches and not has_magic(pure.name):
        _fail(
            "data.target.missing",
            subject,
            {"pattern": pattern, "reason": "no_matches"},
            "Identity Patterns",
        )
    return tuple(
        _identity_pattern_match(root, resolved_root, pattern, target, subject)
        for target in matches
    )


def _bounded_identity_pattern_candidates(
    parent: Path, subject: str
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    try:
        with os.scandir(parent) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > MAX_IDENTITY_PATTERN_CANDIDATES:
                    _fail(
                        "directory.membership.invalid",
                        subject,
                        {
                            "entries": count,
                            "limit": MAX_IDENTITY_PATTERN_CANDIDATES,
                            "parent": str(parent),
                        },
                        "Identity Patterns",
                    )
                candidates.append(Path(entry.path))
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            subject,
            {"error": str(error), "parent": str(parent)},
            "Identity Patterns",
        )
    return tuple(candidates)


def _identity_pattern_match(
    root: Path,
    resolved_root: Path,
    pattern: str,
    target: Path,
    subject: str,
) -> tuple[str, Path]:
    try:
        canonical = validate_local_path_symlinks(target, root)
        relative = canonical.relative_to(resolved_root).as_posix()
    except (EntryMaterialPathError, ValueError) as error:
        _invalid(subject, {"pattern": pattern, "reason": _path_error_reason(error)})
    if target.is_symlink() or not canonical.is_file():
        _fail(
            "data.target.missing",
            subject,
            {
                "file": relative,
                "pattern": pattern,
                "reason": "not_regular_file",
            },
            "Identity Patterns",
        )
    return relative, canonical


def _path_error_reason(error: EntryMaterialPathError | ValueError) -> str:
    return error.reason if isinstance(error, EntryMaterialPathError) else "escape"


def require_matching_observation(
    expected: Fingerprint, actual: FingerprintObservation
) -> FingerprintObservation:
    """Require a caller-supplied historical observation to match current bytes."""

    if actual.fingerprint != expected:
        _fail(
            "data.fingerprint.mismatch",
            "resource observation",
            {
                "expected": expected.as_dict(),
                "observed": actual.fingerprint.as_dict(),
            },
            "Fingerprints",
        )
    return actual


def fingerprint_observation_key(resource: InputResource) -> str:
    """Return the opaque cache key for one canonical input target."""

    return hashlib.sha256(resource.canonical_target.encode("utf-8")).hexdigest()


def fingerprint_observation_record(
    resource: InputResource, observation: FingerprintObservation
) -> Mapping[str, object]:
    """Return one strict cache record for a verified local observation."""

    if observation.cache_identity is None:
        _invalid(resource.name, {"reason": "uncacheable_observation"})
    return {
        "entries": (
            [entry.as_dict() for entry in observation.entries]
            if resource.identity.algorithm
            in {"identity-files-sha256-v1", "identity-patterns-sha256-v1"}
            else []
        ),
        "fingerprint": observation.fingerprint.as_dict(),
        "selection": resource.identity.as_dict(),
        "identity": dict(observation.cache_identity),
        "kind": resource.kind,
        "target": resource.canonical_target,
    }


def _decode_input(
    value: object,
    subject: str,
    entry_root: Path,
    *,
    loading: frozenset[Path] = frozenset(),
    accepted_target: str | None = None,
) -> InputResource:
    if not isinstance(value, Mapping):
        _invalid(subject, {"type": type(value).__name__})
    value = cast(Mapping[str, Any], value)
    if set(value) == {"from_entry", "name"}:
        return _decode_reference(value, subject, entry_root, loading)
    required = {"name", "kind", "location", "identity", "origin"}
    if not required <= set(value) <= required | {"reproduction_comparison"}:
        _invalid(subject, {"fields": sorted(value)})
    name = _name(value.get("name"), subject)
    kind = value.get("kind")
    if kind not in {"file", "directory", "git-repository"}:
        _invalid(subject, {"kind": kind})
    location, target = _location(
        value.get("location"), subject, entry_root, accepted_target=accepted_target
    )
    origin = value.get("origin")
    if not isinstance(origin, bool):
        _invalid(subject, {"origin": origin})
    if kind == "git-repository" and not origin:
        _invalid(subject, {"kind": kind, "origin": origin})
    identity = parse_resource_identity(
        value.get("identity"),
        subject,
        kind=kind,
    )
    comparison = _decode_reproduction_comparison(
        value["reproduction_comparison"]
        if "reproduction_comparison" in value
        else _MISSING,
        subject,
    )
    if comparison is not None and (kind != "file" or origin):
        _invalid(
            subject,
            {
                "reproduction_comparison": comparison.as_dict(),
                "kind": kind,
                "origin": origin,
            },
        )
    return InputResource(
        name=name,
        kind=kind,
        location=location,
        identity=identity,
        origin=origin,
        canonical_target=target,
        comparison=comparison,
    )


def _decode_reference(
    value: Mapping[str, Any],
    subject: str,
    entry_root: Path,
    loading: frozenset[Path],
) -> InputResource:
    name = _name(value.get("name"), subject)
    from_entry = value.get("from_entry")
    if (
        not isinstance(from_entry, str)
        or ENTRY_REFERENCE_NAME_RE.fullmatch(from_entry) is None
    ):
        _invalid(subject, {"from_entry": from_entry})
    entries_root = entry_root.resolve().parent
    candidates = []
    try:
        for candidate in entries_root.iterdir():
            if (
                candidate.is_dir()
                and not candidate.is_symlink()
                and _entry_directory_matches(candidate.name, from_entry)
            ):
                candidates.append(candidate)
    except OSError as error:
        _invalid(subject, {"error": str(error), "from_entry": from_entry})
    if len(candidates) != 1:
        _invalid(
            subject,
            {"from_entry": from_entry, "matches": len(candidates)},
        )
    source_path = candidates[0] / "data.json"
    if source_path.is_symlink() or not source_path.is_file():
        _invalid(subject, {"from_entry": from_entry, "reason": "source_missing"})
    source = _load_data_file(
        source_path, entry_root=candidates[0], loading=loading
    ).by_name.get(name)
    if source is None or source.origin or source.reference_entry is not None:
        _invalid(
            subject,
            {
                "from_entry": from_entry,
                "name": name,
                "reason": "producer_declaration_missing",
            },
        )
    return replace(source, reference_entry=from_entry)


def _entry_directory_matches(name: str, entry_id: str) -> bool:
    """Match one canonical entry directory by its stable ID, not document names."""

    identity = ENTRY_REFERENCE_DIRECTORY_RE.fullmatch(name)
    if identity is None or identity.group("id") != entry_id:
        return False
    entry_date = identity.group("date")
    try:
        return date.fromisoformat(entry_date).isoformat() == entry_date
    except ValueError:
        return False


def _decode_reproduction_comparison(
    value: object, subject: str
) -> ReproductionComparison | None:
    if value is _MISSING:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"contract", "profile"}
        or value.get("contract") != EVIDENCE_COMPARISON_CONTRACT
        or value.get("profile") != "evidence"
    ):
        _invalid(subject, {"reproduction_comparison": value})
    return ReproductionComparison(EVIDENCE_COMPARISON_CONTRACT, "evidence")


def _name(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or value in RESERVED_NAMES
        or ENTRY_REFERENCE_NAME_RE.fullmatch(value) is not None
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or len(value.encode("ascii")) > MAX_NAME_BYTES
        or NAME_RE.fullmatch(value) is None
    ):
        _invalid(subject, {"name": value})
    return value


def _location(
    value: object, subject: str, entry_root: Path, *, accepted_target: str | None = None
) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_LOCATION_BYTES
    ):
        _invalid(subject, {"location": value})
    if "://" in value:
        _invalid(subject, {"location": value, "reason": "remote"})
    _validate_posix_location(value, subject)
    if accepted_target is not None:
        _validate_posix_location(accepted_target, subject)
        if not Path(accepted_target).is_absolute():
            _invalid(subject, {"target": accepted_target})
        return value, accepted_target
    lexical = Path(value) if Path(value).is_absolute() else entry_root / value
    if is_entry_material_root(lexical, entry_root):
        _invalid(subject, {"location": value, "reason": "artifact_root"})
    _validate_local_symlink_surface(lexical, entry_root, subject)
    return value, lexical.resolve().as_posix()


def _validate_posix_location(value: str, subject: str) -> None:
    if "\\" in value or "<" in value or ">" in value:
        _invalid(subject, {"location": value})
    absolute = value.startswith("/")
    body = value[1:] if absolute else value
    parts = body.split("/")
    if not body or any(part in {"", "."} for part in parts):
        _invalid(subject, {"location": value})
    if absolute and ".." in parts:
        _invalid(subject, {"location": value})
    if PurePosixPath(value).as_posix() != value:
        _invalid(subject, {"location": value})


def _validate_local_symlink_surface(path: Path, entry_root: Path, subject: str) -> None:
    try:
        validate_local_path_symlinks(path, entry_root)
    except EntryMaterialPathError as error:
        _invalid(subject, {"location": str(path), "reason": error.reason})


def _resolve_member(resource: InputResource, member: str, subject: str) -> str:
    pure = PurePosixPath(member)
    if resource.kind != "directory" or not _valid_input_member(member):
        _invalid(subject, {"member": member, "resource": resource.name})
    root = Path(resource.canonical_target)
    target = root.joinpath(*pure.parts)
    if target.is_symlink() or not target.is_file():
        _fail(
            "data.target.missing",
            subject,
            {"member": member, "resource": resource.name},
            "Directory Resources",
        )
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        _invalid(subject, {"member": member, "reason": "escape"})
    return target.resolve().as_posix()


def parse_resource_identity(
    value: object,
    subject: str,
    *,
    kind: object | None = None,
) -> ResourceIdentity:
    """Parse one closed declaration-owned resource identity rule."""

    if kind is None:
        if not isinstance(value, Mapping):
            _invalid(subject, {"identity": value})
        algorithm = value.get("algorithm")
        kind = (
            "file"
            if algorithm == "sha256"
            else "git-repository"
            if algorithm == GIT_COMMIT_ALGORITHM
            else "directory"
        )
    return _resource_identity(value, subject, kind)


def _resource_identity(value: object, subject: str, kind: object) -> ResourceIdentity:
    if not isinstance(value, Mapping):
        _invalid(subject, {"identity": value})
    value = cast(Mapping[str, Any], value)
    algorithm = value.get("algorithm")
    if algorithm == "identity-files-sha256-v1":
        return _identity_files_identity(value, subject, kind)
    if algorithm == "identity-patterns-sha256-v1":
        return _identity_pattern_identity(value, subject, kind)
    if algorithm == GIT_COMMIT_ALGORITHM:
        commit = value.get("commit")
        if (
            set(value) != {"algorithm", "commit"}
            or kind != "git-repository"
            or not isinstance(commit, str)
            or GIT_COMMIT_RE.fullmatch(commit) is None
        ):
            _invalid(subject, {"identity": dict(value), "kind": kind})
        return ResourceIdentity(GIT_COMMIT_ALGORITHM, commit=commit)
    if algorithm in {"sha256", "directory-sha256-v1"}:
        if set(value) != {"algorithm"}:
            _invalid(subject, {"identity": dict(value)})
        if algorithm == "directory-sha256-v1" and kind != "directory":
            _invalid(subject, {"identity": dict(value), "kind": kind})
        if algorithm == "sha256" and kind != "file":
            _invalid(subject, {"identity": dict(value), "kind": kind})
        return ResourceIdentity(algorithm)
    _invalid(subject, {"identity": dict(value)})


def _identity_files_identity(
    value: Mapping[str, Any],
    subject: str,
    kind: object,
) -> ResourceIdentity:
    files = _identity_files(value.get("files"), subject)
    if set(value) != {"algorithm", "files"} or kind != "directory":
        _invalid(subject, {"identity": dict(value), "kind": kind})
    return ResourceIdentity("identity-files-sha256-v1", files=files)


def _identity_pattern_identity(
    value: Mapping[str, Any],
    subject: str,
    kind: object,
) -> ResourceIdentity:
    patterns = _identity_patterns(value.get("patterns"), subject)
    if set(value) != {"algorithm", "patterns"} or kind != "directory":
        _invalid(subject, {"identity": dict(value), "kind": kind})
    return ResourceIdentity("identity-patterns-sha256-v1", patterns=patterns)


def _identity_files(value: object, subject: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_IDENTITY_FILES:
        _invalid(subject, {"identity_files": value})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _invalid(subject, {"identity_file": item})
        pure = PurePosixPath(item)
        if (
            not item
            or len(item.encode("utf-8")) > MAX_DIRECTORY_PATH_BYTES
            or pure.is_absolute()
            or "\\" in item
            or "://" in item
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != item
        ):
            _invalid(subject, {"identity_file": item})
        result.append(item)
    if len(result) != len(set(result)):
        _invalid(subject, {"identity_files": result, "reason": "duplicate"})
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


def _identity_patterns(value: object, subject: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_IDENTITY_PATTERNS:
        _invalid(subject, {"identity_patterns": value})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _invalid(subject, {"identity_pattern": item})
        pure = PurePosixPath(item)
        if (
            not item
            or len(item.encode("utf-8")) > MAX_DIRECTORY_PATH_BYTES
            or pure.is_absolute()
            or "\\" in item
            or "://" in item
            or "**" in item
            or any(part in {"", ".", ".."} for part in pure.parts)
            or any(has_magic(part) for part in pure.parts[:-1])
            or pure.as_posix() != item
        ):
            _invalid(subject, {"identity_pattern": item})
        result.append(item)
    if len(result) != len(set(result)):
        _invalid(subject, {"identity_patterns": result, "reason": "duplicate"})
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


def _require_unchanged_identity_files(
    paths: Mapping[str, Path],
    identities: list[Mapping[str, object]],
    subject: str,
) -> None:
    for identity in identities:
        relative = identity.get("path")
        assert isinstance(relative, str)
        try:
            current = _file_cache_identity(paths[relative].stat())
        except OSError as error:
            _fail(
                "provenance.observation.unavailable",
                subject,
                {"error": str(error), "file": relative},
                "Identity Files",
            )
        expected = {key: value for key, value in identity.items() if key != "path"}
        if current != expected:
            _fail(
                "provenance.observation.unavailable",
                subject,
                {"file": relative, "reason": "changed_during_hash"},
                "Identity Files",
            )


def _require_unchanged_identity_patterns(
    resource: InputResource,
    paths: Mapping[str, Path],
) -> None:
    current = identity_pattern_paths(resource)
    if tuple(current) != tuple(paths) or any(
        current[relative] != path for relative, path in paths.items()
    ):
        _fail(
            "provenance.observation.unavailable",
            resource.name,
            {"reason": "pattern_matches_changed"},
            "Identity Patterns",
        )


def _require_unique_inputs(inputs: tuple[InputResource, ...], path: Path) -> None:
    names = [item.name for item in inputs]
    if len(names) != len(set(names)):
        _fail(
            "data.name.duplicate",
            str(path),
            {"names": names},
            DATA_SCHEMA,
        )
    targets = [item.material_identity for item in inputs]
    if len(targets) != len(set(targets)):
        _fail(
            "data.target.duplicate",
            str(path),
            {"targets": targets},
            DATA_SCHEMA,
        )


def _hash_directory(
    root: Path,
) -> tuple[
    tuple[DirectoryFingerprintEntry, ...],
    str,
    Mapping[str, object],
]:
    before_identity, members, member_paths = _observe_directory_tree(root)
    entries: list[DirectoryFingerprintEntry] = []
    for member in members:
        if member.type == "directory":
            entries.append(member)
            continue
        path = member_paths[member.path]
        digest, _ = _hash_file_observation(path)
        entries.append(DirectoryFingerprintEntry(member.path, "file", digest))
    fingerprint = compose_directory_fingerprint(tuple(entries))
    assert fingerprint.digest is not None
    after_identity, after_members, _ = _observe_directory_tree(root)
    if before_identity != after_identity or members != after_members:
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"reason": "changed_during_hash"},
            "Fingerprints",
        )
    return tuple(entries), fingerprint.digest, after_identity


def _hash_file_observation(path: Path) -> tuple[str, Mapping[str, object]]:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
        after = path.stat()
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"error": str(error)},
            "Fingerprints",
        )
    projection_before = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    projection_after = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if projection_before != projection_after:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "changed_during_hash"},
            "Fingerprints",
        )
    return digest.hexdigest(), _file_cache_identity(after)


def _file_cache_identity(observation: os.stat_result) -> Mapping[str, object]:
    return {
        "ctime_ns": observation.st_ctime_ns,
        "kind": "file",
        "mtime_ns": observation.st_mtime_ns,
        "size": observation.st_size,
    }


def _directory_cache_observation(
    root: Path,
) -> tuple[Mapping[str, object], tuple[DirectoryFingerprintEntry, ...]]:
    before_identity, before_members, _ = _observe_directory_tree(root)
    after_identity, after_members, _ = _observe_directory_tree(root)
    if before_identity != after_identity or before_members != after_members:
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"reason": "changed_during_observation"},
            "Fingerprints",
        )
    return after_identity, after_members


def _observe_directory_tree(
    root: Path,
) -> tuple[
    Mapping[str, object],
    tuple[DirectoryFingerprintEntry, ...],
    Mapping[str, Path],
]:
    root_before, raw = _directory_observation_start(root)
    metadata, members, member_paths = _directory_member_metadata(root, raw)
    root_after = _stable_directory_root(root, root_before)
    payload = {
        "entries": metadata,
        "root": {
            "ctime_ns": root_after.st_ctime_ns,
            "mtime_ns": root_after.st_mtime_ns,
        },
        "schema": DIRECTORY_OBSERVATION_SCHEMA,
    }
    identity = {
        "kind": "directory",
        "metadata_sha256": hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest(),
    }
    return identity, members, member_paths


def _directory_observation_start(
    root: Path,
) -> tuple[os.stat_result, tuple[Path, ...]]:
    try:
        root_before = root.stat()
        raw = bounded_descendants(root, maximum_entries=MAX_DIRECTORY_ENTRIES)
    except BoundedTraversalError as error:
        if error.reason == "entry_limit":
            _fail(
                "directory.membership.invalid",
                str(root),
                {"entries": error.observed, "limit": error.limit},
                "Fingerprints",
            )
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"error": error.detail, "reason": error.reason},
            "Fingerprints",
        )
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"error": str(error)},
            "Fingerprints",
        )
    return root_before, raw


def _directory_member_metadata(
    root: Path, raw: tuple[Path, ...]
) -> tuple[
    list[dict[str, object]],
    tuple[DirectoryFingerprintEntry, ...],
    Mapping[str, Path],
]:
    metadata: list[dict[str, object]] = []
    members: list[DirectoryFingerprintEntry] = []
    member_paths: dict[str, Path] = {}
    normalized_paths: set[str] = set()
    total_bytes = 0
    for path in raw:
        relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        try:
            observation = path.lstat()
        except OSError as error:
            _fail(
                "provenance.observation.unavailable",
                str(path),
                {"error": str(error)},
                "Fingerprints",
            )
        mode = observation.st_mode
        if len(relative.encode("utf-8")) > MAX_DIRECTORY_PATH_BYTES or (
            relative in normalized_paths or stat.S_ISLNK(mode)
        ):
            _directory_invalid(root, relative, "unsafe_or_aliased")
        normalized_paths.add(relative)
        member_paths[relative] = path
        entry_type = "directory" if stat.S_ISDIR(mode) else "file"
        item: dict[str, object] = {
            "ctime_ns": observation.st_ctime_ns,
            "mtime_ns": observation.st_mtime_ns,
            "path": relative,
            "type": entry_type,
        }
        if stat.S_ISREG(mode):
            total_bytes += observation.st_size
            if total_bytes > MAX_DIRECTORY_CONTENT_BYTES:
                _directory_invalid(root, relative, "content_bound")
            item["size"] = observation.st_size
            members.append(DirectoryFingerprintEntry(relative, "file"))
        elif not stat.S_ISDIR(mode):
            _directory_invalid(root, relative, "special_file")
        else:
            members.append(DirectoryFingerprintEntry(relative, "directory"))
        metadata.append(item)
    metadata.sort(key=lambda item: str(item["path"]).encode("utf-8"))
    members.sort(key=lambda item: item.path.encode("utf-8"))
    return metadata, tuple(members), member_paths


def _stable_directory_root(root: Path, root_before: os.stat_result) -> os.stat_result:
    try:
        root_after = root.stat()
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"error": str(error)},
            "Fingerprints",
        )
    root_projection_before = (root_before.st_mtime_ns, root_before.st_ctime_ns)
    root_projection_after = (root_after.st_mtime_ns, root_after.st_ctime_ns)
    if root_projection_before != root_projection_after:
        _fail(
            "provenance.observation.unavailable",
            str(root),
            {"reason": "changed_during_observation"},
            "Fingerprints",
        )
    return root_after


def _read_json(path: Path) -> object:
    try:
        raw = bounded_file_bytes(path, maximum_bytes=MAX_DATA_FILE_BYTES)
        text = raw.decode("utf-8")
    except BoundedFileReadError as error:
        observed: dict[str, object] = {
            "limit": error.limit,
            "reason": error.reason,
        }
        if error.observed is not None:
            observed["bytes"] = error.observed
        if error.detail is not None:
            observed["error"] = error.detail
        _invalid(path, observed)
    except UnicodeError as error:
        _invalid(path, {"error": str(error)})
    try:
        return decode_json(text, maximum_bytes=MAX_DATA_FILE_BYTES, subject="data.json")
    except V2JsonError as error:
        _invalid(path, {"error": str(error)})


def _consistency_projection(item: InputResource) -> str:
    return canonical_json(
        {
            "origin": item.origin,
            "identity": item.identity.as_dict(),
            "kind": item.kind,
            "comparison": (
                item.comparison.as_dict() if item.comparison is not None else None
            ),
        }
    )


def _observe_git_repository(
    resource: InputResource, path: Path
) -> FingerprintObservation:
    """Verify one exact commit in a repository without observing live state."""

    if not path.is_dir():
        _target_missing(resource, "not_git_repository")
    root = _git_output(path, "rev-parse", "--show-toplevel")
    if root is None:
        bare = _git_output(path, "rev-parse", "--is-bare-repository")
        git_dir = _git_output(path, "rev-parse", "--absolute-git-dir")
        if (
            bare != "true"
            or git_dir is None
            or Path(git_dir).resolve() != path.resolve()
        ):
            _target_missing(resource, "not_git_repository")
    elif Path(root).resolve() != path.resolve():
        _target_missing(resource, "not_repository_root")
    assert resource.identity.commit is not None
    object_kind = _git_output(path, "cat-file", "-t", resource.identity.commit)
    if object_kind != "commit":
        _fail(
            "data.fingerprint.mismatch",
            resource.name,
            {
                "commit": resource.identity.commit,
                "object_kind": object_kind,
                "reason": "commit_unavailable" if object_kind is None else "not_commit",
            },
            "Fingerprints",
        )
    return FingerprintObservation(
        Fingerprint(GIT_COMMIT_ALGORITHM, digest=resource.identity.commit),
        cache_identity={"kind": "git-repository"},
    )


def _git_output(path: Path, *arguments: str) -> str | None:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_text(value: object, maximum_bytes: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum_bytes
    )


def _fields(value: object) -> object:
    return sorted(value) if isinstance(value, Mapping) else None


def _target_missing(resource: InputResource, reason: str) -> NoReturn:
    _fail(
        "data.target.missing",
        resource.name,
        {"location": resource.location, "reason": reason},
        "Fingerprints",
    )


def _directory_invalid(root: Path, path: str, reason: str) -> NoReturn:
    _fail(
        "directory.membership.invalid",
        str(root),
        {"path": path, "reason": reason},
        "Fingerprints",
    )


def _invalid(subject: object, observed: object) -> NoReturn:
    _fail(
        "data.declaration.invalid",
        str(subject),
        observed,
        DATA_SCHEMA,
    )


def _fail(code: str, subject: str, observed: object, rule: str) -> NoReturn:
    raise DataContractError(code, subject, observed, rule)
