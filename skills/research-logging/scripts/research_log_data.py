"""Strict shared contracts for entry-local ``data.json`` input registries."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
import urllib.parse
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from glob import has_magic
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NoReturn, cast

from validation.entry_materials import (
    EntryMaterialPathError,
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

DATA_SCHEMA = "research-log-data/v1"
DIRECTORY_FINGERPRINT_SCHEMA = "research-log-directory-fingerprint/1"
DIRECTORY_OBSERVATION_SCHEMA = "research-log-directory-observation/1"
IDENTITY_FILES_FINGERPRINT_SCHEMA = "research-log-identity-files-fingerprint/1"
IDENTITY_PATTERNS_FINGERPRINT_SCHEMA = (
    "research-log-identity-patterns-fingerprint/1"
)
MAX_DATA_FILE_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 10_000
MAX_NAME_BYTES = 96
MAX_LOCATION_BYTES = 2_048
MAX_EXTERNAL_FIELD_BYTES = 1_024
MAX_DIRECTORY_ENTRIES = 100_000
MAX_DIRECTORY_PATH_BYTES = 512
MAX_DIRECTORY_CONTENT_BYTES = 1024**4
MAX_IDENTITY_FILES = 64
MAX_IDENTITY_PATTERNS = 64
MAX_IDENTITY_PATTERN_CANDIDATES = 100_000
HASH_CHUNK_BYTES = 1024 * 1024

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
INPUT_TOKEN_RE = re.compile(
    r"<(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)>(?:/(?P<member>.+))?\Z"
)
INPUT_TOKEN_CANDIDATE_RE = re.compile(r"<[A-Za-z0-9][A-Za-z0-9_-]*>")
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
RESERVED_NAMES = frozenset({"log", "project", "theme"})


class DataContractError(MechanicalContractError):
    """One precise data-registry contract failure."""


@dataclass(frozen=True)
class ExternalBoundary:
    """One explicit producerless prior-provenance boundary."""

    source: str
    identity: str

    def as_dict(self) -> dict[str, str]:
        """Return the canonical external-boundary object."""

        return {"identity": self.identity, "source": self.source}

    @property
    def content_identity(self) -> str:
        """Return the stable canonical boundary identity."""

        return _identity(self.as_dict())


@dataclass(frozen=True)
class Fingerprint:
    """One local-resource or immutable-source fingerprint."""

    algorithm: str
    digest: str | None = None
    value: str | None = None
    files: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return the canonical fingerprint object."""

        if self.digest is not None:
            value: dict[str, object] = {
                "algorithm": self.algorithm,
                "digest": self.digest,
            }
            if self.files:
                value["files"] = list(self.files)
            if self.patterns:
                value["patterns"] = list(self.patterns)
            return value
        assert self.value is not None
        return {"algorithm": self.algorithm, "value": self.value}

    @property
    def content_identity(self) -> str:
        """Return the stable canonical fingerprint identity."""

        return _identity(self.as_dict())


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
    fingerprint: Fingerprint
    external: ExternalBoundary | None
    canonical_target: str
    remote: bool

    def as_dict(self) -> dict[str, object]:
        """Return authored canonical fields without resolved observations."""

        value: dict[str, object] = {
            "fingerprint": self.fingerprint.as_dict(),
            "kind": self.kind,
            "location": self.location,
            "name": self.name,
        }
        if self.external is not None:
            value["external"] = self.external.as_dict()
        return value

    @property
    def content_identity(self) -> str:
        """Return the stable declaration identity."""

        return _identity(self.as_dict())


@dataclass(frozen=True)
class ResolvedInputToken:
    """One exact input token resolved to a resource or directory member."""

    resource: InputResource
    path: str
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
            "research-log-data/v1",
        )


def load_data_file(path: Path, *, entry_root: Path) -> DataFile:
    """Read one strict entry-root ``data.json`` declaration."""

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
    if value.get("schema") != DATA_SCHEMA or not isinstance(raw_inputs, list):
        _invalid(path, {"schema": value.get("schema")})
    if not raw_inputs or len(raw_inputs) > MAX_INPUTS:
        _invalid(path, {"inputs": len(raw_inputs)})
    inputs = tuple(
        _decode_input(raw, f"{path}:inputs[{index}]", entry_root)
        for index, raw in enumerate(raw_inputs)
    )
    _require_unique_inputs(inputs, path)
    return DataFile(path=expected, entry_root=entry_root, inputs=inputs)


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
            projections = declarations.setdefault(item.canonical_target, {})
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
    external: ExternalBoundary | None = None,
) -> InputResource:
    """Build one local declaration with a freshly observed strong fingerprint."""

    algorithm = "sha256" if kind == "file" else "directory-sha256-v1"
    provisional = _decode_input(
        {
            "name": name,
            "kind": kind,
            "location": location,
            "fingerprint": {"algorithm": algorithm, "digest": "0" * 64},
            **({"external": external.as_dict()} if external is not None else {}),
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    if provisional.remote:
        _invalid(f"input:{name}", {"location": location, "reason": "remote"})
    observation = observe_fingerprint(provisional)
    return replace(provisional, fingerprint=observation.fingerprint)


def build_identity_directory(
    name: str,
    location: str,
    identity_files: tuple[str, ...],
    *,
    entry_root: Path,
    external: ExternalBoundary | None = None,
) -> InputResource:
    """Build one managed local directory from exact authoritative files."""

    provisional = _decode_input(
        {
            "name": name,
            "kind": "directory",
            "location": location,
            "fingerprint": {
                "algorithm": "identity-files-sha256-v1",
                "digest": "0" * 64,
                "files": list(identity_files),
            },
            **({"external": external.as_dict()} if external is not None else {}),
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    if provisional.remote:
        _invalid(f"input:{name}", {"location": location, "reason": "remote"})
    observation = observe_fingerprint(provisional)
    return replace(provisional, fingerprint=observation.fingerprint)


def build_identity_pattern_directory(
    name: str,
    location: str,
    identity_patterns: tuple[str, ...],
    *,
    entry_root: Path,
    external: ExternalBoundary | None = None,
) -> InputResource:
    """Build one managed local directory from bounded file selectors."""

    provisional = _decode_input(
        {
            "name": name,
            "kind": "directory",
            "location": location,
            "fingerprint": {
                "algorithm": "identity-patterns-sha256-v1",
                "digest": "0" * 64,
                "patterns": list(identity_patterns),
            },
            **({"external": external.as_dict()} if external is not None else {}),
        },
        f"input:{name}",
        entry_root.resolve(),
    )
    if provisional.remote:
        _invalid(f"input:{name}", {"location": location, "reason": "remote"})
    observation = observe_fingerprint(provisional)
    return replace(provisional, fingerprint=observation.fingerprint)


def build_remote_input(
    name: str,
    location: str,
    *,
    external: ExternalBoundary,
    fingerprint: Fingerprint,
    entry_root: Path,
) -> InputResource:
    """Build one exact inaccessible remote-file declaration."""

    return _decode_input(
        {
            "name": name,
            "kind": "file",
            "location": location,
            "fingerprint": fingerprint.as_dict(),
            "external": external.as_dict(),
        },
        f"input:{name}",
        entry_root.resolve(),
    )


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


def resolve_input_token(value: str, data_file: DataFile | None) -> ResolvedInputToken:
    """Resolve one complete ``<name>`` or ``<directory>/member`` input token."""

    match = INPUT_TOKEN_RE.fullmatch(value)
    if match is None:
        _fail(
            "data.input.undeclared",
            value,
            {"reason": "invalid_token"},
            "Command Tokens And Roles",
        )
    name = match.group("name")
    resource = data_file.by_name.get(name) if data_file is not None else None
    if resource is None:
        _fail(
            "data.input.undeclared",
            value,
            {"name": name},
            "Command Tokens And Roles",
        )
    member = match.group("member")
    if member is None:
        return ResolvedInputToken(resource, resource.canonical_target)
    path = _resolve_member(resource, member, value)
    return ResolvedInputToken(resource, path, member)


def input_token_candidate(value: str) -> bool:
    """Return whether a command argument contains data-token syntax."""

    return INPUT_TOKEN_CANDIDATE_RE.search(value) is not None


def observe_fingerprint(resource: InputResource) -> FingerprintObservation:
    """Observe one accessible local input without changing its declaration."""

    if resource.remote:
        _fail(
            "data.remote.identity_invalid",
            resource.name,
            {"reason": "remote_observation_prohibited"},
            "Fingerprints",
        )
    path = Path(resource.canonical_target)
    if path.is_symlink():
        _fail(
            "data.declaration.invalid",
            resource.name,
            {"location": resource.location, "reason": "symlink"},
            "research-log-data/v1",
        )
    if resource.kind == "file":
        if not path.is_file():
            _target_missing(resource, "not_regular_file")
        digest, identity = _hash_file_observation(path)
        return FingerprintObservation(
            Fingerprint("sha256", digest=digest), cache_identity=identity
        )
    if not path.is_dir():
        _target_missing(resource, "not_directory")
    if resource.fingerprint.algorithm == "identity-files-sha256-v1":
        paths = identity_file_paths(resource)
        identity_entries: list[DirectoryFingerprintEntry] = []
        identities: list[Mapping[str, object]] = []
        for relative, identity_path in paths.items():
            digest, identity = _hash_file_observation(identity_path)
            identity_entries.append(
                DirectoryFingerprintEntry(relative, "file", digest)
            )
            identities.append({"path": relative, **identity})
        fingerprint = compose_identity_files_fingerprint(tuple(identity_entries))
        _require_unchanged_identity_files(paths, identities, resource.name)
        return FingerprintObservation(
            fingerprint,
            tuple(identity_entries),
            {"files": identities, "kind": "identity-files"},
        )
    if resource.fingerprint.algorithm == "identity-patterns-sha256-v1":
        paths = identity_pattern_paths(resource)
        identity_entries = []
        identities = []
        for relative, identity_path in paths.items():
            digest, identity = _hash_file_observation(identity_path)
            identity_entries.append(
                DirectoryFingerprintEntry(relative, "file", digest)
            )
            identities.append({"path": relative, **identity})
        fingerprint = compose_identity_patterns_fingerprint(
            tuple(identity_entries),
            resource.fingerprint.patterns,
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
        "files": [
            {"path": entry.path, "sha256": entry.sha256} for entry in ordered
        ],
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
        "files": [
            {"path": entry.path, "sha256": entry.sha256} for entry in ordered
        ],
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
        resource.remote
        or resource.kind != "directory"
        or resource.fingerprint.algorithm != "identity-files-sha256-v1"
    ):
        _invalid(resource.name, {"reason": "identity_files_not_applicable"})
    root = Path(resource.canonical_target)
    if root.is_symlink() or not root.is_dir():
        _target_missing(resource, "not_directory")
    result: dict[str, Path] = {}
    for relative in resource.fingerprint.files:
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
        resource.remote
        or resource.kind != "directory"
        or resource.fingerprint.algorithm != "identity-patterns-sha256-v1"
    ):
        _invalid(resource.name, {"reason": "identity_patterns_not_applicable"})
    root = Path(resource.canonical_target)
    if root.is_symlink() or not root.is_dir():
        _target_missing(resource, "not_directory")
    resolved_root = root.resolve()
    result: dict[str, Path] = {}
    owners: dict[str, str] = {}
    wildcard_candidates: dict[Path, tuple[Path, ...]] = {}
    for pattern in resource.fingerprint.patterns:
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
            candidates = _bounded_identity_pattern_candidates(
                canonical_parent, subject
            )
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


def verify_fingerprint(
    resource: InputResource,
    *,
    cached: Mapping[str, object] | None = None,
) -> FingerprintObservation | None:
    """Verify one local fingerprint; remote immutable identities are declarative."""

    if resource.remote:
        return None
    observation = _reuse_fingerprint_observation(resource, cached)
    if observation is None:
        observation = observe_fingerprint(resource)
    return validate_fingerprint_observation(resource, observation)


def validate_fingerprint_observation(
    resource: InputResource, observation: FingerprintObservation
) -> FingerprintObservation:
    """Require one shared observation to match a specific declaration."""

    if observation.fingerprint != resource.fingerprint:
        _fail(
            "data.fingerprint.mismatch",
            resource.name,
            {
                "expected": resource.fingerprint.as_dict(),
                "observed": observation.fingerprint.as_dict(),
            },
            "Fingerprints",
        )
    return observation


def fingerprint_observation_key(resource: InputResource) -> str:
    """Return the opaque cache key for one canonical input target."""

    return hashlib.sha256(resource.canonical_target.encode("utf-8")).hexdigest()


def fingerprint_observation_record(
    resource: InputResource, observation: FingerprintObservation
) -> Mapping[str, object]:
    """Return one strict cache record for a verified local observation."""

    if resource.remote or observation.cache_identity is None:
        _invalid(resource.name, {"reason": "uncacheable_observation"})
    return {
        "entries": (
            [entry.as_dict() for entry in observation.entries]
            if resource.fingerprint.algorithm
            in {"identity-files-sha256-v1", "identity-patterns-sha256-v1"}
            else []
        ),
        "fingerprint": observation.fingerprint.as_dict(),
        "identity": dict(observation.cache_identity),
        "kind": resource.kind,
        "target": resource.canonical_target,
    }


def _reuse_fingerprint_observation(
    resource: InputResource, cached: Mapping[str, object] | None
) -> FingerprintObservation | None:
    parts = _cached_observation_parts(resource, cached)
    if parts is None:
        return None
    identity, raw_entries, path = parts
    if resource.kind == "file":
        return _reuse_file_observation(resource, identity, raw_entries, path)
    if resource.fingerprint.algorithm == "identity-files-sha256-v1":
        return _reuse_identity_files_observation(
            resource, identity, raw_entries, path
        )
    if resource.fingerprint.algorithm == "identity-patterns-sha256-v1":
        return _reuse_identity_patterns_observation(
            resource, identity, raw_entries, path
        )
    return _reuse_directory_observation(resource, identity, raw_entries, path)


def _reuse_identity_files_observation(
    resource: InputResource,
    identity: Mapping[str, object],
    raw_entries: list[object],
    path: Path,
) -> FingerprintObservation | None:
    if not path.is_dir() or set(identity) != {"files", "kind"}:
        return None
    raw_identities = identity.get("files")
    if identity.get("kind") != "identity-files" or not isinstance(
        raw_identities, list
    ):
        return None
    entries = _cached_identity_entries(raw_entries, resource.fingerprint.files)
    if entries is None or len(raw_identities) != len(entries):
        return None
    paths = identity_file_paths(resource)
    for raw_identity, relative in zip(raw_identities, resource.fingerprint.files):
        if not isinstance(raw_identity, Mapping):
            return None
        file_identity = {
            key: value for key, value in raw_identity.items() if key != "path"
        }
        if (
            raw_identity.get("path") != relative
            or not _valid_file_cache_identity(file_identity)
            or _file_cache_identity(paths[relative].stat()) != file_identity
        ):
            return None
    return FingerprintObservation(
        resource.fingerprint,
        entries,
        dict(identity),
        identity_reused=True,
    )


def _reuse_identity_patterns_observation(
    resource: InputResource,
    identity: Mapping[str, object],
    raw_entries: list[object],
    path: Path,
) -> FingerprintObservation | None:
    if not path.is_dir() or set(identity) != {"files", "kind"}:
        return None
    raw_identities = identity.get("files")
    if identity.get("kind") != "identity-patterns" or not isinstance(
        raw_identities, list
    ):
        return None
    paths = identity_pattern_paths(resource)
    expected_files = tuple(paths)
    entries = _cached_identity_entries(raw_entries, expected_files)
    if entries is None or len(raw_identities) != len(entries):
        return None
    for raw_identity, relative in zip(raw_identities, expected_files):
        if not isinstance(raw_identity, Mapping):
            return None
        file_identity = {
            key: value for key, value in raw_identity.items() if key != "path"
        }
        if (
            raw_identity.get("path") != relative
            or not _valid_file_cache_identity(file_identity)
            or _file_cache_identity(paths[relative].stat()) != file_identity
        ):
            return None
    return FingerprintObservation(
        resource.fingerprint,
        entries,
        dict(identity),
        identity_reused=True,
    )


def _cached_identity_entries(
    values: list[object], expected_files: tuple[str, ...]
) -> tuple[DirectoryFingerprintEntry, ...] | None:
    entries: list[DirectoryFingerprintEntry] = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "type"}:
            return None
        path = value.get("path")
        digest = value.get("sha256")
        if (
            not isinstance(path, str)
            or value.get("type") != "file"
            or not isinstance(digest, str)
            or DIGEST_RE.fullmatch(digest) is None
        ):
            return None
        entries.append(DirectoryFingerprintEntry(path, "file", digest))
    result = tuple(entries)
    return result if tuple(entry.path for entry in result) == expected_files else None


def _cached_observation_parts(
    resource: InputResource, cached: Mapping[str, object] | None
) -> tuple[Mapping[str, object], list[object], Path] | None:
    if (
        not isinstance(cached, Mapping)
        or set(cached) != {"entries", "fingerprint", "identity", "kind", "target"}
        or cached.get("fingerprint") != resource.fingerprint.as_dict()
        or cached.get("kind") != resource.kind
        or cached.get("target") != resource.canonical_target
    ):
        return None
    identity = cached.get("identity")
    raw_entries = cached.get("entries")
    if not isinstance(identity, Mapping) or not isinstance(raw_entries, list):
        return None
    path = Path(resource.canonical_target)
    if path.is_symlink():
        return None
    return identity, raw_entries, path


def _reuse_file_observation(
    resource: InputResource,
    identity: Mapping[str, object],
    raw_entries: list[object],
    path: Path,
) -> FingerprintObservation | None:
    if (
        raw_entries
        or not path.is_file()
        or not _valid_file_cache_identity(identity)
        or _file_cache_identity(path.stat()) != identity
    ):
        return None
    return FingerprintObservation(
        resource.fingerprint,
        cache_identity=dict(identity),
        identity_reused=True,
    )


def _reuse_directory_observation(
    resource: InputResource,
    identity: Mapping[str, object],
    raw_entries: list[object],
    path: Path,
) -> FingerprintObservation | None:
    if not path.is_dir() or set(identity) != {"kind", "metadata_sha256"}:
        return None
    metadata_digest = identity.get("metadata_sha256")
    if (
        identity.get("kind") != "directory"
        or not isinstance(metadata_digest, str)
        or DIGEST_RE.fullmatch(metadata_digest) is None
    ):
        return None
    if raw_entries:
        return None
    observed_identity, entries = _directory_cache_observation(path)
    if observed_identity != identity:
        return None
    return FingerprintObservation(
        resource.fingerprint,
        entries,
        dict(identity),
        identity_reused=True,
    )


def _valid_file_cache_identity(value: Mapping[str, object]) -> bool:
    if set(value) != {"ctime_ns", "kind", "mtime_ns", "size"}:
        return False
    return value.get("kind") == "file" and all(
        isinstance(value.get(field), int)
        and not isinstance(value.get(field), bool)
        and cast(int, value[field]) >= 0
        for field in ("ctime_ns", "mtime_ns", "size")
    )


def _decode_input(value: object, subject: str, entry_root: Path) -> InputResource:
    if not isinstance(value, Mapping):
        _invalid(subject, {"type": type(value).__name__})
    value = cast(Mapping[str, Any], value)
    allowed = {"name", "kind", "location", "fingerprint", "external"}
    required = allowed - {"external"}
    if not required <= set(value) <= allowed:
        _invalid(subject, {"fields": sorted(value)})
    name = _name(value.get("name"), subject)
    kind = value.get("kind")
    if kind not in {"file", "directory"}:
        _invalid(subject, {"kind": kind})
    location, remote, target = _location(value.get("location"), subject, entry_root)
    fingerprint = _fingerprint(value.get("fingerprint"), subject, remote, kind)
    external = _external(value["external"], subject) if "external" in value else None
    if remote and (kind != "file" or external is None):
        _fail(
            "data.remote.identity_invalid",
            subject,
            {"kind": kind, "external": external is not None},
            "External Boundaries",
        )
    return InputResource(
        name=name,
        kind=kind,
        location=location,
        fingerprint=fingerprint,
        external=external,
        canonical_target=target,
        remote=remote,
    )


def _name(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or value in RESERVED_NAMES
        or len(value.encode("ascii", errors="ignore")) != len(value)
        or len(value.encode("ascii")) > MAX_NAME_BYTES
        or NAME_RE.fullmatch(value) is None
    ):
        _invalid(subject, {"name": value})
    return value


def _location(value: object, subject: str, entry_root: Path) -> tuple[str, bool, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_LOCATION_BYTES
    ):
        _invalid(subject, {"location": value})
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if (
            SCHEME_RE.fullmatch(parsed.scheme) is None
            or not value.startswith(parsed.scheme + "://")
            or not parsed.netloc
            or "\\" in value
        ):
            _invalid(subject, {"location": value})
        return value, True, value
    _validate_posix_location(value, subject)
    lexical = Path(value) if Path(value).is_absolute() else entry_root / value
    _validate_local_symlink_surface(lexical, entry_root, subject)
    return value, False, lexical.resolve().as_posix()


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
    if (
        resource.kind != "directory"
        or resource.remote
        or not member
        or len(member.encode("utf-8")) > MAX_DIRECTORY_PATH_BYTES
        or pure.is_absolute()
        or "\\" in member
        or "://" in member
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != member
    ):
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


def _fingerprint(
    value: object, subject: str, remote: bool, kind: object
) -> Fingerprint:
    if not isinstance(value, Mapping):
        _invalid(subject, {"fingerprint": value})
    value = cast(Mapping[str, Any], value)
    algorithm = value.get("algorithm")
    if algorithm == "identity-files-sha256-v1":
        return _identity_files_fingerprint(value, subject, remote, kind)
    if algorithm == "identity-patterns-sha256-v1":
        return _identity_pattern_fingerprint(value, subject, remote, kind)
    if algorithm in {"sha256", "directory-sha256-v1"}:
        digest = value.get("digest")
        if (
            set(value) != {"algorithm", "digest"}
            or not isinstance(digest, str)
            or DIGEST_RE.fullmatch(digest) is None
        ):
            _invalid(subject, {"fingerprint": dict(value)})
        if algorithm == "directory-sha256-v1" and (remote or kind != "directory"):
            _invalid(subject, {"fingerprint": dict(value), "kind": kind})
        if algorithm == "sha256" and not remote and kind != "file":
            _invalid(subject, {"fingerprint": dict(value), "kind": kind})
        return Fingerprint(algorithm, digest=digest)
    if algorithm == "immutable-source":
        source_value = value.get("value")
        if (
            set(value) != {"algorithm", "value"}
            or not remote
            or not isinstance(source_value, str)
            or not source_value
            or len(source_value.encode("utf-8")) > MAX_EXTERNAL_FIELD_BYTES
        ):
            _invalid(subject, {"fingerprint": dict(value)})
        return Fingerprint(algorithm, value=source_value)
    _invalid(subject, {"fingerprint": dict(value)})


def _identity_files_fingerprint(
    value: Mapping[str, Any],
    subject: str,
    remote: bool,
    kind: object,
) -> Fingerprint:
    digest = value.get("digest")
    files = _identity_files(value.get("files"), subject)
    if (
        set(value) != {"algorithm", "digest", "files"}
        or remote
        or kind != "directory"
        or not isinstance(digest, str)
        or DIGEST_RE.fullmatch(digest) is None
    ):
        _invalid(subject, {"fingerprint": dict(value), "kind": kind})
    return Fingerprint("identity-files-sha256-v1", digest=digest, files=files)


def _identity_pattern_fingerprint(
    value: Mapping[str, Any],
    subject: str,
    remote: bool,
    kind: object,
) -> Fingerprint:
    digest = value.get("digest")
    patterns = _identity_patterns(value.get("patterns"), subject)
    if (
        set(value) != {"algorithm", "digest", "patterns"}
        or remote
        or kind != "directory"
        or not isinstance(digest, str)
        or DIGEST_RE.fullmatch(digest) is None
    ):
        _invalid(subject, {"fingerprint": dict(value), "kind": kind})
    return Fingerprint(
        "identity-patterns-sha256-v1",
        digest=digest,
        patterns=patterns,
    )


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
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_IDENTITY_PATTERNS
    ):
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


def _external(value: object, subject: str) -> ExternalBoundary:
    if not isinstance(value, Mapping) or set(value) != {"source", "identity"}:
        _invalid(subject, {"external": _fields(value)})
    source = value.get("source")
    identity = value.get("identity")
    if not _bounded_text(source, MAX_EXTERNAL_FIELD_BYTES) or not _bounded_text(
        identity, MAX_EXTERNAL_FIELD_BYTES
    ):
        _invalid(subject, {"external": dict(value)})
    assert isinstance(source, str) and isinstance(identity, str)
    return ExternalBoundary(source, identity)


def _require_unique_inputs(inputs: tuple[InputResource, ...], path: Path) -> None:
    names = [item.name for item in inputs]
    if len(names) != len(set(names)):
        _fail(
            "data.name.duplicate",
            str(path),
            {"names": names},
            "research-log-data/v1",
        )
    targets = [item.canonical_target for item in inputs]
    if len(targets) != len(set(targets)):
        _fail(
            "data.target.duplicate",
            str(path),
            {"targets": targets},
            "research-log-data/v1",
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
            "external": item.external.as_dict() if item.external else None,
            "fingerprint": item.fingerprint.as_dict(),
            "kind": item.kind,
        }
    )


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
        "research-log-data/v1",
    )


def _fail(code: str, subject: str, observed: object, rule: str) -> NoReturn:
    raise DataContractError(code, subject, observed, rule)
