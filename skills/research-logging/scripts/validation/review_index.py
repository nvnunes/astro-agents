"""Ephemeral indexes for bounded validation-review candidate queries."""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence, cast

from .contracts import ScanRecord
from .producer_bindings import (
    ProducerCandidateClass,
    ProducerCandidateFacts,
    classify_candidate,
    identity_for_path,
    prepare_candidate_facts,
    resolved_identity_cache,
)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", value.lower()))


def _command_fragments(value: str, size: int | None = None) -> frozenset[str]:
    normalized = value.lower()
    sizes = (1, 2, 3) if size is None else (size,)
    return frozenset(
        normalized[index : index + width]
        for width in sizes
        for index in range(max(0, len(normalized) - width + 1))
    )


def _normalized_command(value: str) -> str:
    try:
        return " ".join(shlex.split(value))
    except ValueError:
        return " ".join(value.split())


def review_invocation_key(
    entry_id: str,
    section: str,
    command: str,
    duplicate_ordinal: int,
) -> str:
    """Return the stable ephemeral identity for one recorded invocation."""

    normalized_section = " ".join(section.split()).casefold()
    section_identity = hashlib.sha256(normalized_section.encode("utf-8")).hexdigest()
    command_identity = hashlib.sha256(
        _normalized_command(command).encode("utf-8")
    ).hexdigest()
    return (
        f"{entry_id}:{section_identity[:16]}:{command_identity[:16]}:"
        f"{duplicate_ordinal}"
    )


def _identity_ancestors(identity: str) -> tuple[str, ...]:
    path = Path(identity)
    ancestors = []
    for parent in path.parents:
        value = parent.as_posix()
        if value in {".", ""}:
            continue
        ancestors.append(value)
    return tuple(ancestors)


def _append_index(
    index: MutableMapping[str, list[str]], identity: str, invocation_key: str
) -> None:
    index.setdefault(identity, []).append(invocation_key)


@dataclass(frozen=True)
class SourceSnapshot:
    """One cached producer-source snapshot shared by all its invocations."""

    identity: str
    lines: tuple[str, ...]
    searchable: str


@dataclass(frozen=True)
class PreparedInvocation:
    """Target-independent review facts for one recorded invocation."""

    key: str
    entry_id: str
    entry_position: int
    command_position: int
    command: dict[str, Any]
    candidate_facts: ProducerCandidateFacts
    source: SourceSnapshot | None
    searchable: str


@dataclass(frozen=True)
class ReviewContextIndex:
    """Immutable scan-wide facts used by one or more review query sessions."""

    scan: ScanRecord
    identities: Mapping[str, str]
    entry_positions: Mapping[str, int]
    owner_entries: Mapping[str, tuple[str, ...]]
    invocations: Mapping[str, PreparedInvocation]
    invocation_keys_by_entry: Mapping[str, tuple[str, ...]]
    direct_outputs: Mapping[str, tuple[str, ...]]
    output_containers: Mapping[str, tuple[str, ...]]
    unknown_containers: Mapping[str, tuple[str, ...]]
    command_fragments: Mapping[str, frozenset[str]]
    source_tokens: Mapping[str, frozenset[str]]
    command_objects: Mapping[int, str]
    build_metrics: Mapping[str, int | float]

    @classmethod
    def build(cls, scan: Mapping[str, Any]) -> "ReviewContextIndex":
        """Build one immutable review index from current scan facts."""

        return _ReviewIndexBuilder(scan).build()


class _ReviewIndexBuilder:
    """Mutable single-use builder for one immutable review context index."""

    def __init__(self, scan: Mapping[str, Any]) -> None:
        self.started = time.monotonic()
        self.scan = cast(ScanRecord, scan)
        self.identities = resolved_identity_cache(self.scan)
        self.entries = list(scan.get("entries", []))
        self.entry_positions = {
            str(entry.get("id")): position
            for position, entry in enumerate(self.entries)
        }
        self.owner_entries: dict[str, list[str]] = {}
        self.source_cache: dict[str, SourceSnapshot | None] = {}
        self.invocations: dict[str, PreparedInvocation] = {}
        self.by_entry: dict[str, list[str]] = {}
        self.direct_outputs: dict[str, list[str]] = {}
        self.output_containers: dict[str, list[str]] = {}
        self.unknown_containers: dict[str, list[str]] = {}
        self.command_fragments: dict[str, set[str]] = {}
        self.source_tokens: dict[str, set[str]] = {}
        self.command_objects: dict[int, str] = {}
        self.duplicate_counts: dict[tuple[str, str, str], int] = {}
        self.source_reads = 0
        self.filesystem_probes = 0

    def _index_entry_owners(self) -> None:
        for entry in self.entries:
            entry_id = entry.get("id")
            path = entry.get("path")
            if isinstance(entry_id, str) and isinstance(path, str):
                self.owner_entries.setdefault(
                    Path(path).parent.as_posix(), []
                ).append(entry_id)

    def _source_snapshot(self, command: Mapping[str, Any]) -> SourceSnapshot | None:
        raw_script = command.get("script")
        if not isinstance(raw_script, str) or not raw_script:
            return None
        script_identity = Path(raw_script).resolve().as_posix()
        if script_identity in self.source_cache:
            return self.source_cache[script_identity]
        self.filesystem_probes += 1
        script_path = Path(raw_script)
        if not script_path.is_file():
            self.source_cache[script_identity] = None
            return None
        try:
            text = script_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.source_cache[script_identity] = None
            return None
        self.source_reads += 1
        snapshot = SourceSnapshot(
            script_identity, tuple(text.splitlines()), text.lower()
        )
        self.source_cache[script_identity] = snapshot
        return snapshot

    def _invocation_key(
        self, entry_id: str, section: str, command_text: str
    ) -> str:
        fingerprint = hashlib.sha256(
            _normalized_command(command_text).encode("utf-8")
        ).hexdigest()
        normalized_section = " ".join(section.split()).casefold()
        group = (entry_id, normalized_section, fingerprint)
        self.duplicate_counts[group] = self.duplicate_counts.get(group, 0) + 1
        return review_invocation_key(
            entry_id, section, command_text, self.duplicate_counts[group]
        )

    def _prepare_invocation(
        self,
        entry_id: str,
        entry_position: int,
        command_position: int,
        command: dict[str, Any],
    ) -> PreparedInvocation:
        command_text = str(command.get("command", ""))
        section = str(command.get("section", ""))
        key = self._invocation_key(entry_id, section, command_text)
        facts = prepare_candidate_facts(self.scan, command, self.identities)
        source = self._source_snapshot(command)
        if source is None and facts.unknown_containers:
            facts = facts._replace(unknown_containers=frozenset())
        searchable = (
            (source.searchable if source is not None else "")
            + "\n"
            + command_text.lower()
        )
        return PreparedInvocation(
            key,
            entry_id,
            entry_position,
            command_position,
            command,
            facts,
            source,
            searchable,
        )

    def _index_invocation(self, invocation: PreparedInvocation) -> None:
        key = invocation.key
        facts = invocation.candidate_facts
        self.invocations[key] = invocation
        self.by_entry.setdefault(invocation.entry_id, []).append(key)
        self.command_objects[id(invocation.command)] = key
        for identity in facts.output_identities:
            _append_index(self.direct_outputs, identity, key)
        for identity in facts.output_containers:
            _append_index(self.output_containers, identity, key)
        for identity in facts.unknown_containers:
            _append_index(self.unknown_containers, identity, key)
        for fragment in _command_fragments(facts.command_text):
            self.command_fragments.setdefault(fragment, set()).add(key)
        for token in _tokens(invocation.searchable):
            self.source_tokens.setdefault(token, set()).add(key)

    def _index_invocations(self) -> None:
        for entry_position, entry in enumerate(self.entries):
            entry_id = str(entry.get("id", ""))
            for command_position, command in enumerate(entry.get("commands", []), 1):
                self._index_invocation(
                    self._prepare_invocation(
                        entry_id, entry_position, command_position, command
                    )
                )

    def _metrics(self) -> dict[str, int | float]:
        return {
            "entry_scans": 1,
            "entries_indexed": len(self.entries),
            "static_invocation_preparations": len(self.invocations),
            "unique_scripts": len(self.source_cache),
            "direct_output_identities": len(self.direct_outputs),
            "output_container_identities": len(self.output_containers),
            "unknown_output_container_identities": len(self.unknown_containers),
            "command_fragment_terms": len(self.command_fragments),
            "source_token_terms": len(self.source_tokens),
            "producer_source_reads": self.source_reads,
            "filesystem_probes": self.filesystem_probes,
            "index_build_seconds": round(time.monotonic() - self.started, 6),
        }

    @staticmethod
    def _tuples(value: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
        return {key: tuple(items) for key, items in value.items()}

    @staticmethod
    def _sets(value: Mapping[str, set[str]]) -> dict[str, frozenset[str]]:
        return {key: frozenset(items) for key, items in value.items()}

    def build(self) -> ReviewContextIndex:
        """Consume scan facts and freeze every derived lookup."""

        self._index_entry_owners()
        self._index_invocations()
        return ReviewContextIndex(
            self.scan,
            self.identities,
            self.entry_positions,
            self._tuples(self.owner_entries),
            self.invocations,
            self._tuples(self.by_entry),
            self._tuples(self.direct_outputs),
            self._tuples(self.output_containers),
            self._tuples(self.unknown_containers),
            self._sets(self.command_fragments),
            self._sets(self.source_tokens),
            self.command_objects,
            self._metrics(),
        )


@dataclass
class ReviewQuerySession:
    """Operation-scoped memoized candidate and source-context queries."""

    index: ReviewContextIndex
    relationship_cache: Dict[
        tuple[str, str, tuple[str, ...]], ProducerCandidateClass
    ] = field(default_factory=dict)
    candidate_cache: Dict[
        tuple[str, str, tuple[str, ...]], tuple[str, ...]
    ] = field(default_factory=dict)
    source_context_cache: Dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict
    )
    counters: Dict[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.counters.update(
            {
                "candidate_queries": 0,
                "candidate_cache_hits": 0,
                "relationship_evaluations": 0,
                "relationship_cache_hits": 0,
                "path_prefix_comparisons": 0,
                "source_token_queries": 0,
                "source_context_extractions": 0,
                "source_context_cache_hits": 0,
                "candidate_query_seconds": 0.0,
                "source_context_seconds": 0.0,
            }
        )

    def _entry_ids(self, entry_id: str, identity: str) -> tuple[str, ...]:
        if entry_id not in self.index.entry_positions:
            return ()
        result = [entry_id]
        seen = {entry_id}
        ancestors = _identity_ancestors(identity)
        self.counters["path_prefix_comparisons"] += len(ancestors)
        owners = {
            owner
            for ancestor in ancestors
            for owner in self.index.owner_entries.get(ancestor, ())
        }
        for owner in sorted(
            owners, key=lambda value: self.index.entry_positions.get(value, 10**9)
        ):
            if owner not in seen:
                result.append(owner)
                seen.add(owner)
        return tuple(result)

    @staticmethod
    def _intersection(
        tokens: Iterable[str], index: Mapping[str, frozenset[str]]
    ) -> set[str]:
        groups = [index.get(token, frozenset()) for token in tokens]
        if not groups or any(not group for group in groups):
            return set()
        result = set(groups[0])
        for group in groups[1:]:
            result.intersection_update(group)
        return result

    def _container_keys(
        self, identity: str, index: Mapping[str, tuple[str, ...]]
    ) -> set[str]:
        ancestors = _identity_ancestors(identity)
        self.counters["path_prefix_comparisons"] += len(ancestors)
        return {
            invocation
            for ancestor in ancestors
            for invocation in index.get(ancestor, ())
        }

    def _plausible_keys(
        self, entry_ids: Sequence[str], identity: str, sections: Sequence[str]
    ) -> set[str]:
        allowed = {
            key
            for entry_id in entry_ids
            for key in self.index.invocation_keys_by_entry.get(entry_id, ())
        }
        plausible = set(self.index.direct_outputs.get(identity, ()))
        plausible.update(self._container_keys(identity, self.index.output_containers))
        unknown = self._container_keys(identity, self.index.unknown_containers)
        source_tokens = [
            token for token in _tokens(Path(identity).stem) if len(token) > 1
        ]
        if source_tokens:
            self.counters["source_token_queries"] += 1
            unknown.intersection_update(
                self._intersection(source_tokens, self.index.source_tokens)
            )
            plausible.update(unknown)
        target_name = Path(identity).name
        fragment_size = min(3, len(target_name))
        name_fragments = _command_fragments(target_name, fragment_size)
        if name_fragments:
            plausible.update(
                self._intersection(name_fragments, self.index.command_fragments)
            )
        section_set = set(sections)
        plausible.update(
            key
            for key in allowed
            if self.index.invocations[key].candidate_facts.section in section_set
        )
        return plausible & allowed

    def _relationship(
        self, invocation: PreparedInvocation, identity: str, sections: Sequence[str]
    ) -> ProducerCandidateClass:
        normalized_sections = tuple(sorted(set(sections)))
        key = (invocation.key, identity, normalized_sections)
        cached = self.relationship_cache.get(key)
        if cached is not None:
            self.counters["relationship_cache_hits"] += 1
            return cached
        relationship = classify_candidate(
            invocation.candidate_facts,
            identity,
            normalized_sections,
            invocation.searchable,
        )
        self.relationship_cache[key] = relationship
        self.counters["relationship_evaluations"] += 1
        return relationship

    @staticmethod
    def _deduplicated(
        groups: Sequence[Sequence[PreparedInvocation]], limit: int | None = None
    ) -> list[PreparedInvocation]:
        result = []
        seen = set()
        for invocation in (item for group in groups for item in group):
            if invocation.key in seen:
                continue
            seen.add(invocation.key)
            result.append(invocation)
            if limit is not None and len(result) == limit:
                break
        return result

    def candidate_invocations(
        self,
        entry_id: str,
        identity: str,
        sections: Sequence[str],
    ) -> list[PreparedInvocation]:
        """Return ordered current-v43 candidates for one target exactly once."""

        started = time.monotonic()
        normalized_sections = tuple(sorted(set(sections)))
        cache_key = (entry_id, identity, normalized_sections)
        cached = self.candidate_cache.get(cache_key)
        if cached is not None:
            self.counters["candidate_cache_hits"] += 1
            return [self.index.invocations[key] for key in cached]
        self.counters["candidate_queries"] += 1
        entry_ids = self._entry_ids(entry_id, identity)
        if not entry_ids:
            self.candidate_cache[cache_key] = ()
            return []
        entry_rank = {value: position for position, value in enumerate(entry_ids)}
        candidates = [
            self.index.invocations[key]
            for key in self._plausible_keys(entry_ids, identity, normalized_sections)
        ]
        candidates.sort(
            key=lambda item: (
                entry_rank.get(item.entry_id, 10**9),
                item.command_position,
            )
        )
        groups: list[list[PreparedInvocation]] = [[] for _ in range(6)]
        for invocation in candidates:
            relationship = self._relationship(
                invocation, identity, normalized_sections
            )
            if relationship.direct:
                groups[0].append(invocation)
            if relationship.container:
                if relationship.section:
                    groups[1].append(invocation)
                elif invocation.entry_id != entry_id:
                    groups[2].append(invocation)
                else:
                    groups[5].append(invocation)
            if relationship.exact:
                groups[3].append(invocation)
            elif relationship.section:
                groups[4].append(invocation)
        eligible = self._deduplicated(groups[:3])
        diagnostic_limit = max(0, 5 - len(eligible))
        eligible_keys = {item.key for item in eligible}
        diagnostics = [
            item
            for item in self._deduplicated(groups[3:])
            if item.key not in eligible_keys
        ][:diagnostic_limit]
        ordered = self._deduplicated((eligible, diagnostics))
        self.candidate_cache[cache_key] = tuple(item.key for item in ordered)
        self.counters["candidate_query_seconds"] += time.monotonic() - started
        return ordered

    def candidate_commands(
        self, entry_id: str, identity: str, sections: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Return original scan command objects in deterministic candidate order."""

        return [
            invocation.command
            for invocation in self.candidate_invocations(entry_id, identity, sections)
        ]

    def invocation_for_command(
        self, command: Mapping[str, Any]
    ) -> PreparedInvocation | None:
        """Return prepared facts for one original scan command object."""

        key = self.index.command_objects.get(id(command))
        return self.index.invocations.get(key) if key is not None else None

    def source_context(
        self, invocation: PreparedInvocation, identity: str
    ) -> tuple[str, ...]:
        """Return cached producer-code lines that reference the target option."""

        started = time.monotonic()
        cache_key = (invocation.key, identity)
        cached = self.source_context_cache.get(cache_key)
        if cached is not None:
            self.counters["source_context_cache_hits"] += 1
            return cached
        target_name = Path(identity).name
        parameters = []
        for argument in invocation.command.get("path_arguments", []):
            raw_path = argument.get("path")
            if not raw_path:
                continue
            argument_identity = identity_for_path(
                self.index.scan, raw_path, self.index.identities
            )
            if argument_identity != identity and Path(raw_path).name != target_name:
                continue
            parameter = argument.get("option")
            if parameter:
                parameters.append(parameter.lstrip("-").replace("-", "_"))
        matches = []
        if invocation.source is not None and parameters:
            self.counters["source_context_extractions"] += 1
            for number, line in enumerate(invocation.source.lines, 1):
                if any(
                    re.search(rf"\b(?:args|parsed)\.{re.escape(parameter)}\b", line)
                    for parameter in parameters
                ):
                    matches.append(f"{number}: {line.strip()}")
                if len(matches) == 4:
                    break
        result = tuple(matches)
        self.source_context_cache[cache_key] = result
        self.counters["source_context_seconds"] += time.monotonic() - started
        return result

    def metrics(self) -> dict[str, int | float]:
        """Return stable index and session counters for diagnostics."""

        result = dict(self.index.build_metrics)
        result.update(self.counters)
        result["candidate_relationships"] = len(self.relationship_cache)
        result["source_context_cache_entries"] = len(self.source_context_cache)
        return result
