"""Metadata-gated evidence observation for normal validation.

One :class:`ObservationSession` owns file identity work for a validation
attempt.  Consumers share its result, so unchanged files are not opened and a
file whose metadata changed is hashed at most once.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .inventory import CHUNK_SIZE, content_identity, directory_membership_identity

METADATA_UNCHANGED = "metadata_unchanged"
CONTENT_UNCHANGED = "content_unchanged"
CONTENT_CHANGED = "content_changed"
NEW = "new"
MISSING = "missing"
INACCESSIBLE = "inaccessible"
CHANGED_DURING_OBSERVATION = "changed_during_observation"
AMBIGUOUS = "ambiguous"


@dataclass
class ObservationDiagnostics:
    """Deterministic identity-work counters for one invocation."""

    metadata_checked: int = 0
    hashes_reused: int = 0
    files_hashed: int = 0
    bytes_hashed: int = 0
    content_changed: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the stable public diagnostic contract."""

        return {
            "metadata_checked": self.metadata_checked,
            "hashes_reused": self.hashes_reused,
            "files_hashed": self.files_hashed,
            "bytes_hashed": self.bytes_hashed,
            "content_changed": self.content_changed,
        }


@dataclass(frozen=True)
class FileObservation:
    """One explicit evidence observation and its trustworthy identity, if any."""

    path: str
    status: str
    identity: Mapping[str, Any] | None
    prior_identity: Mapping[str, Any] | None
    detail: str | None = None

    @property
    def resolved(self) -> bool:
        """Whether this observation may support a completed outcome."""

        return self.status in {
            METADATA_UNCHANGED,
            CONTENT_UNCHANGED,
            CONTENT_CHANGED,
            NEW,
        }


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(str(path.expanduser())))


def _metadata(stat_result: os.stat_result) -> tuple[int, int, int]:
    return (
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _cached_metadata(identity: Mapping[str, Any] | None) -> tuple[Any, Any, Any]:
    if identity is None:
        return (None, None, None)
    return (
        identity.get("size"),
        identity.get("mtime_ns"),
        identity.get("ctime_ns"),
    )


class ObservationSession:
    """Share metadata-gated identities and inspections within one invocation."""

    def __init__(self) -> None:
        self.diagnostics = ObservationDiagnostics()
        self._observations: dict[str, FileObservation] = {}
        self._inspections: dict[str, Any] = {}

    def _stat(self, path: Path) -> os.stat_result:
        self.diagnostics.metadata_checked += 1
        return path.stat()

    def _unresolved(
        self,
        path: Path,
        status: str,
        cached: Mapping[str, Any] | None,
        detail: str,
    ) -> FileObservation:
        return FileObservation(path.as_posix(), status, None, cached, detail)

    def _hash(
        self, path: Path, before: os.stat_result, cached: Mapping[str, Any] | None
    ) -> FileObservation:
        digest = hashlib.sha256()
        count = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    digest.update(chunk)
                    count += len(chunk)
            after = self._stat(path)
        except FileNotFoundError as exc:
            return self._unresolved(path, MISSING, cached, str(exc))
        except (OSError, PermissionError) as exc:
            return self._unresolved(path, INACCESSIBLE, cached, str(exc))
        self.diagnostics.files_hashed += 1
        self.diagnostics.bytes_hashed += count
        if _metadata(before) != _metadata(after) or count != after.st_size:
            return self._unresolved(
                path,
                CHANGED_DURING_OBSERVATION,
                cached,
                "file metadata changed during content observation",
            )
        identity = {
            "size": count,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
            "sha256": digest.hexdigest(),
        }
        if cached is None:
            status = NEW
        elif content_identity(identity) == content_identity(cached):
            status = CONTENT_UNCHANGED
        else:
            status = CONTENT_CHANGED
            self.diagnostics.content_changed += 1
        return FileObservation(path.as_posix(), status, identity, cached)

    def observe(
        self, path: Path, cached: Mapping[str, Any] | None = None
    ) -> FileObservation:
        """Observe one file, opening content only when metadata requires it."""

        absolute = _absolute(path)
        key = absolute.as_posix()
        prior = self._observations.get(key)
        if prior is not None:
            return prior
        try:
            before = self._stat(absolute)
        except FileNotFoundError as exc:
            result = self._unresolved(absolute, MISSING, cached, str(exc))
        except (OSError, PermissionError) as exc:
            result = self._unresolved(absolute, INACCESSIBLE, cached, str(exc))
        else:
            if not absolute.is_file():
                result = self._unresolved(
                    absolute,
                    AMBIGUOUS,
                    cached,
                    "evidence identity requires one regular file",
                )
            elif cached is not None and _metadata(before) == _cached_metadata(cached):
                self.diagnostics.hashes_reused += 1
                result = FileObservation(
                    key, METADATA_UNCHANGED, copy.deepcopy(cached), cached
                )
            else:
                result = self._hash(absolute, before, cached)
        self._observations[key] = result
        return result

    def observe_collection(
        self,
        path: Path,
        cached: Mapping[str, Any],
        member_cache: Mapping[str, Mapping[str, Any]],
        logical_path: str,
    ) -> FileObservation:
        """Observe one exact selected-member directory dependency."""

        absolute = _absolute(path)
        try:
            normalized = _normalized_collection_members(cached)
        except ValueError as exc:
            return self._unresolved(
                absolute, AMBIGUOUS, cached, str(exc)
            )
        key = f"{absolute.as_posix()}\0{json.dumps(normalized)}"
        prior = self._observations.get(key)
        if prior is not None:
            return prior
        try:
            aggregate_metadata = _collection_metadata(self, absolute, normalized)
        except CollectionObservationError as exc:
            result = self._unresolved(absolute, exc.status, cached, str(exc))
            self._observations[key] = result
            return result
        if aggregate_metadata == _cached_metadata(cached):
            self.diagnostics.hashes_reused += len(normalized)
            result = FileObservation(
                absolute.as_posix(),
                METADATA_UNCHANGED,
                copy.deepcopy(cached),
                cached,
            )
            self._observations[key] = result
            return result
        try:
            identities = _collection_member_identities(
                self, absolute, normalized, member_cache, logical_path
            )
        except CollectionObservationError as exc:
            result = self._unresolved(absolute, exc.status, cached, str(exc))
            self._observations[key] = result
            return result
        identity = _collection_identity(identities, normalized)
        status = (
            CONTENT_UNCHANGED
            if content_identity(identity) == content_identity(cached)
            else CONTENT_CHANGED
        )
        if status == CONTENT_CHANGED:
            self.diagnostics.content_changed += 1
        result = FileObservation(absolute.as_posix(), status, identity, cached)
        self._observations[key] = result
        return result

    def observe_directory_membership(
        self, path: Path, cached: Mapping[str, Any]
    ) -> FileObservation:
        """Observe one direct directory-membership identity."""

        absolute = _absolute(path)
        key = f"{absolute.as_posix()}\0directory-membership"
        prior = self._observations.get(key)
        if prior is not None:
            return prior
        self.diagnostics.metadata_checked += 1
        try:
            if not absolute.exists():
                result = self._unresolved(
                    absolute, MISSING, cached, "directory no longer exists"
                )
            elif not absolute.is_dir():
                result = self._unresolved(
                    absolute,
                    AMBIGUOUS,
                    cached,
                    "directory membership requires one directory",
                )
            else:
                identity = directory_membership_identity(absolute)
                status = (
                    CONTENT_UNCHANGED if identity == cached else CONTENT_CHANGED
                )
                if status == CONTENT_CHANGED:
                    self.diagnostics.content_changed += 1
                result = FileObservation(
                    absolute.as_posix(), status, identity, cached
                )
        except PermissionError as exc:
            result = self._unresolved(
                absolute, INACCESSIBLE, cached, str(exc)
            )
        self._observations[key] = result
        return result

    def inspect(
        self,
        path: Path,
        inspector: Callable[[Path], Any],
        cached_identity: Mapping[str, Any] | None = None,
        cached_inspection: Any = None,
    ) -> tuple[FileObservation, Any | None, bool]:
        """Inspect one observation without repeating identity or inspection work.

        The boolean result reports whether the inspection was reused.  A file
        that changes while inspection reads it is returned as unresolved.
        """

        observation = self.observe(path, cached_identity)
        if not observation.resolved:
            return observation, None, False
        key = observation.path
        if key in self._inspections:
            return observation, self._inspections[key], True
        if observation.status == METADATA_UNCHANGED and cached_inspection is not None:
            self._inspections[key] = copy.deepcopy(cached_inspection)
            return observation, self._inspections[key], True
        try:
            inspection = inspector(Path(key))
            after = self._stat(Path(key))
        except (OSError, PermissionError) as exc:
            status = MISSING if isinstance(exc, FileNotFoundError) else INACCESSIBLE
            changed = self._unresolved(
                Path(key), status, cached_identity, str(exc)
            )
            self._observations[key] = changed
            result = (changed, None, False)
        else:
            identity = observation.identity
            assert identity is not None
            if _metadata(after) != _cached_metadata(identity):
                changed = self._unresolved(
                    Path(key),
                    CHANGED_DURING_OBSERVATION,
                    cached_identity,
                    "file metadata changed during inspection",
                )
                self._observations[key] = changed
                result = (changed, None, False)
            else:
                self._inspections[key] = inspection
                result = (observation, inspection, False)
        return result


class CollectionObservationError(RuntimeError):
    """One explicit unresolved status while observing a collection."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def _normalized_collection_members(cached: Mapping[str, Any]) -> list[str]:
    members = cached.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("collection has no exact member scope")
    return sorted(set(str(member) for member in members))


def _collection_metadata(
    session: ObservationSession, absolute: Path, members: list[str]
) -> tuple[int, int, int]:
    if not absolute.is_dir():
        raise CollectionObservationError(
            MISSING, f"collection directory is missing: {absolute}"
        )
    total_size = 0
    latest_mtime = 0
    latest_ctime = 0
    for raw in members:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise CollectionObservationError(
                AMBIGUOUS, f"collection member escapes its directory: {raw}"
            )
        member = absolute / relative
        try:
            member_stat = session._stat(member)
        except FileNotFoundError as exc:
            raise CollectionObservationError(MISSING, str(exc)) from exc
        except (OSError, PermissionError) as exc:
            raise CollectionObservationError(INACCESSIBLE, str(exc)) from exc
        if not member.is_file():
            raise CollectionObservationError(
                MISSING, f"collection member is not a regular file: {member}"
            )
        total_size += member_stat.st_size
        latest_mtime = max(latest_mtime, member_stat.st_mtime_ns)
        latest_ctime = max(latest_ctime, member_stat.st_ctime_ns)
    return total_size, latest_mtime, latest_ctime


def _collection_member_identities(
    session: ObservationSession,
    absolute: Path,
    members: list[str],
    member_cache: Mapping[str, Mapping[str, Any]],
    logical_path: str,
) -> list[tuple[Path, Mapping[str, Any]]]:
    identities = []
    for raw in members:
        relative = Path(raw)
        member_logical = (Path(logical_path) / relative).as_posix()
        observation = session.observe(
            absolute / relative, member_cache.get(member_logical)
        )
        if not observation.resolved or observation.identity is None:
            raise CollectionObservationError(
                observation.status,
                observation.detail or f"could not observe collection member {raw}",
            )
        identities.append((relative, observation.identity))
    return identities


def _collection_identity(
    identities: list[tuple[Path, Mapping[str, Any]]], members: list[str]
) -> dict[str, Any]:
    digest = hashlib.sha256()
    for relative, identity in identities:
        digest.update(
            (
                f"{relative.as_posix()}\0{identity['size']}\0"
                f"{identity['sha256']}\n"
            ).encode("utf-8")
        )
    return {
        "size": sum(int(identity["size"]) for _, identity in identities),
        "mtime_ns": max(int(identity["mtime_ns"]) for _, identity in identities),
        "ctime_ns": max(int(identity["ctime_ns"]) for _, identity in identities),
        "sha256": digest.hexdigest(),
        "members": members,
    }


def retain_compatible_outcomes(
    outcomes: list[Mapping[str, Any]],
    observations: Mapping[str, FileObservation],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retain outcomes whose exact dependency content is still observed.

    Metadata-only changes update the dependency identity without reopening the
    outcome. A dependency already published as missing remains compatible
    while it is still missing. Other missing, inaccessible, ambiguous,
    changing, or content-changed dependencies reopen only dependent outcomes.
    """

    retained: list[dict[str, Any]] = []
    reopened: list[dict[str, Any]] = []
    for source in outcomes:
        outcome = copy.deepcopy(dict(source))
        compatible = True
        for dependency in outcome.get("dependencies", []):
            observation = observations.get(_dependency_observation_key(dependency))
            if observation is None:
                continue
            if not _dependency_observation_is_compatible(
                dependency, observation
            ):
                compatible = False
                break
            if observation.identity is not None:
                dependency["identity"] = copy.deepcopy(observation.identity)
        (retained if compatible else reopened).append(outcome)
    return retained, reopened


def outcomes_are_compatible(
    outcomes: list[Mapping[str, Any]],
    observations: Mapping[str, FileObservation],
) -> bool:
    """Return whether every outcome dependency remains reusable."""

    return all(
        observation is None
        or _dependency_observation_is_compatible(dependency, observation)
        for outcome in outcomes
        for dependency in outcome.get("dependencies", [])
        for observation in [
            observations.get(_dependency_observation_key(dependency))
        ]
    )


def _dependency_observation_is_compatible(
    dependency: Mapping[str, Any], observation: FileObservation
) -> bool:
    expected = dependency.get("identity")
    if expected == {"missing": True}:
        return observation.status == MISSING
    return observation.resolved and observation.status != CONTENT_CHANGED


def observe_outcome_dependencies(
    session: ObservationSession,
    outcomes: list[Mapping[str, Any]],
    cached_files: Mapping[str, Mapping[str, Any]],
    project_root: Path,
) -> dict[str, FileObservation]:
    """Observe each durable outcome dependency once through the target path."""

    observations: dict[str, FileObservation] = {}
    for outcome in outcomes:
        for dependency in outcome.get("dependencies", []):
            identity = dependency["path"]
            observation_key = _dependency_observation_key(dependency)
            if observation_key in observations:
                continue
            candidate = Path(identity)
            path = candidate if candidate.is_absolute() else project_root / candidate
            dependency_identity = dependency.get("identity")
            if isinstance(dependency_identity, Mapping) and (
                set(dependency_identity) == {"members", "sha256"}
                and isinstance(dependency_identity.get("members"), int)
            ):
                observations[observation_key] = (
                    session.observe_directory_membership(path, dependency_identity)
                )
                continue
            elif isinstance(dependency_identity, Mapping) and isinstance(
                dependency_identity.get("members"), list
            ):
                cached = dependency_identity
            else:
                cached = cached_files.get(identity) or dependency_identity
            if isinstance(cached, Mapping) and isinstance(
                cached.get("members"), list
            ):
                observations[observation_key] = session.observe_collection(
                    path, cached, cached_files, identity
                )
            else:
                observations[observation_key] = session.observe(path, cached)
    return observations


def _dependency_observation_key(dependency: Mapping[str, Any]) -> str:
    path = str(dependency["path"])
    identity = dependency.get("identity")
    members = identity.get("members") if isinstance(identity, Mapping) else None
    if not isinstance(members, list):
        return path
    return f"{path}\0{json.dumps(sorted(members), ensure_ascii=False)}"
