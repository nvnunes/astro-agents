"""Bounded semantic question-and-decision exchange for target validation."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from .collection_scopes import (
    COLLECTION_DIRECTORY_SELECTIONS_KEY,
    DIRECTORY_SELECTOR_KEYS,
    compact_directory_choices,
)
from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import DECISION_SCHEMA_VERSION
from .orphan_rules import (
    SUBTREE_REVIEW_KIND,
    SUBTREE_RULE_DEPENDENCIES,
    ancestor_roots,
    candidates_below,
    disposition_choice,
    refined_questions,
    split_choice,
    structural_summary,
    subtree_fingerprint,
    subtree_subject,
)
from .orphan_rules import (
    allowed_decisions as subtree_allowed_decisions,
)
from .review_batches import (
    ordered_orphan_candidates,
    orphan_candidate_fingerprint,
    orphan_fingerprint_context,
)
from .review_index import ReviewContextIndex, ReviewQuerySession
from .review_reuse import (
    SEMANTIC_REVIEW_RULES,
    ReuseAnswerRequest,
    ReviewJudgmentIndex,
    index_review_judgments,
    reusable_review_answer,
    reusable_review_answer_diagnostics,
    review_judgment_inputs,
)

EXCHANGE_SCHEMA_VERSION = 1
INTERNAL_FILENAME = ".continuation.json"
MAX_PACKET_ITEMS = 200
TARGET_PACKET_BYTES = 65_536
MAX_PACKET_BYTES = 73_728
MAX_EXPANDED_CONTEXT_BYTES = TARGET_PACKET_BYTES // 2
REVIEW_SESSION_SCHEMA_VERSION = 1
CONTEXT_PROJECTION_VERSION = 5
SESSION_BASE_FILENAME = "base.json"
SESSION_INDEX_FILENAME = "index.json"
SESSION_STATE_FILENAME = "state.json"
SESSION_ITEMS_DIRECTORY = "items"
SESSION_ITEM_SHARD_SIZE = MAX_PACKET_ITEMS
VALIDATION_WORK_ROOT = "work"
VALIDATION_DIRECTORY = "validation"
LOCAL_CACHE_DIRECTORY = ".cache"
COLLECTION_SHARED_CONTEXT_KEYS = (
    "reason",
    "selection_contract",
    "collections",
    "collection_structure",
    "recorded_invocations",
)


class _ReviewExchangeProjection:
    """Exchange-owned indexes for deterministic context projection."""

    def __init__(self, scan: ScanRecord, adjudication: AdjudicationRecord) -> None:
        self.scan = scan
        self.adjudication = adjudication
        self.entries, self.targets = _review_entry_indexes(scan, adjudication)
        (
            self.queue,
            self.orphan_queues,
            self.orphan_candidates,
            self.orphan_descendants,
        ) = _review_queue_indexes(adjudication)
        self._query_session: ReviewQuerySession | None = None
        self._eligible_invocation_cache: dict[
            tuple[str, str, tuple[str, ...]], tuple[dict[str, Any], ...]
        ] = {}

    def queue_item(self, item: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Resolve one template to its first compatible queue item."""

        entry = str(item.get("entry", ""))
        if item.get("kind") in {SUBTREE_REVIEW_KIND, "orphan_candidate"}:
            return self.orphan_queues.get(entry)
        return self.queue.get(
            (entry, str(item.get("kind", "")), str(item.get("identity", "")))
        )

    def entry(self, entry_id: Any) -> Mapping[str, Any]:
        return self.entries.get(str(entry_id), {})

    def target(self, entry_id: Any, identity: Any) -> Mapping[str, Any]:
        return self.targets.get((str(entry_id), str(identity)), {})

    def candidate(self, entry_id: Any, identity: Any) -> Mapping[str, Any]:
        return self.orphan_candidates.get(str(entry_id), {}).get(str(identity), {})

    def subtree_candidates(
        self, entry_id: Any, root: Any
    ) -> Sequence[Mapping[str, Any]]:
        return self.orphan_descendants.get(str(entry_id), {}).get(str(root), ())

    def eligible_invocations(
        self, queue_item: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        cache_key = (
            str(queue_item.get("entry", "")),
            str(queue_item.get("identity", "")),
            tuple(str(section) for section in queue_item.get("sections", [])),
        )
        cached = self._eligible_invocation_cache.get(cache_key)
        if cached is not None:
            return [dict(invocation) for invocation in cached]
        if self._query_session is None:
            self._query_session = ReviewQuerySession(
                ReviewContextIndex.build(self.scan)
            )
        invocations = tuple(
            {
                "invocation": invocation.key,
                "entry": invocation.entry_id,
                "line": invocation.command.get("line"),
                "command": invocation.command.get("command", ""),
                "path_arguments": invocation.command.get("path_arguments", []),
            }
            for invocation in self._query_session.eligible_candidate_invocations(
                str(queue_item.get("entry", "")),
                str(queue_item.get("identity", "")),
                queue_item.get("sections", []),
            )
        )
        self._eligible_invocation_cache[cache_key] = invocations
        return [dict(invocation) for invocation in invocations]


def _review_entry_indexes(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[tuple[str, str], Mapping[str, Any]],
]:
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in scan.get("entries", []):
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            entries.setdefault(entry_id, entry)
    targets: dict[tuple[str, str], Mapping[str, Any]] = {}
    for entry in adjudication.get("entries", []):
        entry_id = entry.get("id")
        if not isinstance(entry_id, str):
            continue
        for target in entry.get("targets", []):
            target_id = target.get("target")
            if isinstance(target_id, str):
                targets.setdefault((entry_id, target_id), target)
    return entries, targets


def _review_queue_indexes(
    adjudication: AdjudicationRecord,
) -> tuple[
    dict[tuple[str, str, str], Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, dict[str, Mapping[str, Any]]],
    dict[str, dict[str, list[Mapping[str, Any]]]],
]:
    queue: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    orphan_queues: dict[str, Mapping[str, Any]] = {}
    orphan_candidates: dict[str, dict[str, Mapping[str, Any]]] = {}
    orphan_descendants: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for item in adjudication.get("review_queue", []):
        entry = str(item.get("entry", ""))
        kind = str(item.get("kind", ""))
        identity = str(item.get("identity", ""))
        queue.setdefault((entry, kind, identity), item)
        if kind != "orphan_candidates" or entry in orphan_queues:
            continue
        orphan_queues[entry] = item
        candidates = [
            candidate
            for candidate in item.get("candidates", [])
            if isinstance(candidate, Mapping)
        ]
        orphan_candidates[entry] = {
            str(candidate.get("identity")): candidate for candidate in candidates
        }
        descendants: dict[str, list[Mapping[str, Any]]] = {}
        for candidate in candidates:
            candidate_identity = candidate.get("identity")
            if not isinstance(candidate_identity, str):
                continue
            descendants.setdefault(candidate_identity, []).append(candidate)
            for parent in PurePosixPath(candidate_identity).parents:
                descendants.setdefault(parent.as_posix(), []).append(candidate)
        orphan_descendants[entry] = descendants
    return queue, orphan_queues, orphan_candidates, orphan_descendants


@dataclass(frozen=True)
class _PreparedReviewItems:
    """Templates and their single exchange-owned context projections."""

    items: list[dict[str, Any]]
    contexts: list[Mapping[str, Any]]
    orphan_fingerprints: dict[str, dict[str, str]]


class _JudgmentQueueIndex:
    """Queue lookup index used while projecting accepted judgment shards."""

    def __init__(self, adjudication: AdjudicationRecord) -> None:
        self.ordinary: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.orphan: dict[str, Mapping[str, Any]] = {}
        self.orphan_entries: dict[tuple[str, str], Mapping[str, Any]] = {}
        for item in adjudication.get("review_queue", []):
            entry = str(item.get("entry", ""))
            identity = str(item.get("identity", ""))
            self.ordinary.setdefault((entry, identity), item)
            if item.get("kind") != "orphan_candidates":
                continue
            self.orphan.setdefault(entry, item)
            for candidate in item.get("candidates", []):
                if isinstance(candidate, Mapping):
                    self.orphan_entries.setdefault(
                        (entry, str(candidate.get("identity", ""))), item
                    )

    def queue_item(self, row: Mapping[str, Any]) -> Mapping[str, Any] | None:
        entry = str(row.get("entry", ""))
        identity = str(row.get("identity", ""))
        ordinary = self.ordinary.get((entry, identity))
        if ordinary is not None:
            return ordinary
        if row.get("kind") == SUBTREE_REVIEW_KIND:
            return self.orphan.get(entry)
        if row.get("kind") == "orphan_candidate":
            return self.orphan_entries.get((entry, identity))
        return None


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    """Return one deterministic JSON representation for temporary state."""

    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: bytes) -> None:
    """Atomically replace one temporary review-session file."""

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_session_index(session_dir: Path, index: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a compact index with optional hash-verified item shards."""

    items = list(index.get("items", []))
    if len(items) <= SESSION_ITEM_SHARD_SIZE:
        retained = copy.deepcopy(dict(index))
        _atomic_write(session_dir / SESSION_INDEX_FILENAME, _json_bytes(retained))
        return retained
    items_dir = session_dir / SESSION_ITEMS_DIRECTORY
    items_dir.mkdir()
    shards = []
    session_identity = index["session_identity"]
    for offset in range(0, len(items), SESSION_ITEM_SHARD_SIZE):
        selected = items[offset : offset + SESSION_ITEM_SHARD_SIZE]
        payload = {
            "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
            "session_identity": session_identity,
            "offset": offset,
            "items": selected,
        }
        encoded = _json_bytes(payload)
        name = f"items-{offset:08d}.json"
        _atomic_write(items_dir / name, encoded)
        shards.append(
            {
                "file": f"{SESSION_ITEMS_DIRECTORY}/{name}",
                "offset": offset,
                "item_count": len(selected),
                "sha256": _sha256(encoded),
            }
        )
    retained = {
        key: copy.deepcopy(value) for key, value in index.items() if key != "items"
    }
    retained["item_count"] = len(items)
    retained["item_shards"] = shards
    _atomic_write(session_dir / SESSION_INDEX_FILENAME, _json_bytes(retained))
    return retained


def _validated_item_shards(
    session_dir: Path, index: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    raw_shards = index.get("item_shards")
    if not isinstance(raw_shards, list) or not raw_shards:
        raise ValidationToolError("review session item shards are invalid")
    expected_offset = 0
    shards = []
    expected_files = set()
    for shard in raw_shards:
        if not (
            isinstance(shard, Mapping)
            and set(shard) == {"file", "offset", "item_count", "sha256"}
            and isinstance(shard.get("file"), str)
            and isinstance(shard.get("offset"), int)
            and isinstance(shard.get("item_count"), int)
            and isinstance(shard.get("sha256"), str)
        ):
            raise ValidationToolError("review session item shards are invalid")
        name = f"items-{expected_offset:08d}.json"
        if (
            shard["file"] != f"{SESSION_ITEMS_DIRECTORY}/{name}"
            or shard["offset"] != expected_offset
            or shard["item_count"] < 1
            or shard["item_count"] > SESSION_ITEM_SHARD_SIZE
            or len(shard["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in shard["sha256"])
        ):
            raise ValidationToolError("review session item shards are invalid")
        expected_files.add(name)
        shards.append(shard)
        expected_offset += int(shard["item_count"])
    if expected_offset != index.get("item_count"):
        raise ValidationToolError("review session item shards are incomplete")
    items_dir = session_dir / SESSION_ITEMS_DIRECTORY
    if items_dir.is_symlink() or not items_dir.is_dir():
        raise ValidationToolError("review session item shard directory is invalid")
    contents = list(items_dir.iterdir())
    present = {path.name for path in contents}
    if present != expected_files or any(
        path.is_symlink() or not path.is_file() for path in contents
    ):
        raise ValidationToolError("review session item shard directory is invalid")
    return shards


def _session_items(
    session_dir: Path,
    index: Mapping[str, Any],
    offset: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load one bounded item range from current or legacy session storage."""

    raw_items = index.get("items")
    if isinstance(raw_items, list):
        selected = raw_items[offset : None if limit is None else offset + limit]
        return list(selected)
    end = int(index.get("item_count", 0)) if limit is None else offset + limit
    items = []
    for shard in _validated_item_shards(session_dir, index):
        shard_offset = int(shard["offset"])
        shard_end = shard_offset + int(shard["item_count"])
        if shard_end <= offset or shard_offset >= end:
            continue
        path = session_dir / str(shard["file"])
        encoded = path.read_bytes()
        if _sha256(encoded) != shard["sha256"]:
            raise ValidationToolError("review session item shard identity differs")
        try:
            payload = json.loads(encoded)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationToolError("review session item shard is invalid") from exc
        shard_items = payload.get("items") if isinstance(payload, Mapping) else None
        if not (
            isinstance(payload, Mapping)
            and payload.get("schema_version") == REVIEW_SESSION_SCHEMA_VERSION
            and payload.get("session_identity") == index.get("session_identity")
            and payload.get("offset") == shard_offset
            and isinstance(shard_items, list)
            and len(shard_items) == shard["item_count"]
        ):
            raise ValidationToolError("review session item shard is invalid")
        low = max(offset, shard_offset) - shard_offset
        high = min(end, shard_end) - shard_offset
        items.extend(shard_items[low:high])
    expected_count = max(0, min(end, int(index.get("item_count", 0))) - offset)
    if len(items) != expected_count:
        raise ValidationToolError("review session item range is incomplete")
    return items


def _summary_work_root(output_dir: Path) -> Path:
    return (
        output_dir / VALIDATION_DIRECTORY / LOCAL_CACHE_DIRECTORY / VALIDATION_WORK_ROOT
    )


def _session_locator(session_identity: str) -> str:
    return (Path(VALIDATION_WORK_ROOT) / session_identity).as_posix()


def _session_path(output_dir: Path, locator: str) -> Path:
    pure = PurePosixPath(locator)
    if (
        "\\" in locator
        or pure.is_absolute()
        or pure.as_posix() != locator
        or len(pure.parts) != 2
        or pure.parts[0] != VALIDATION_WORK_ROOT
        or ".." in pure.parts
        or len(pure.parts[1]) != 64
        or any(character not in "0123456789abcdef" for character in pure.parts[1])
    ):
        raise ValidationToolError("review session locator is invalid")
    cache_root = output_dir / VALIDATION_DIRECTORY / LOCAL_CACHE_DIRECTORY
    work_root = _summary_work_root(output_dir)
    session_dir = cache_root / locator
    owned_paths = [
        output_dir / VALIDATION_DIRECTORY,
        cache_root,
        work_root,
        session_dir,
    ]
    if output_dir.is_symlink() or any(path.is_symlink() for path in owned_paths):
        raise ValidationToolError("review session locator is invalid")
    try:
        session_dir.resolve().relative_to(work_root.resolve())
    except ValueError as exc:
        raise ValidationToolError("review session locator is invalid") from exc
    return session_dir


def _question(item: Mapping[str, Any]) -> str:
    kind = item["kind"]
    if kind == "semantic_fallback" and item.get("producer_candidates"):
        return "Which exact recorded invocation establishes provenance?"
    questions = {
        "mechanical_failure": "Retain the reported deterministic failure?",
        "semantic_fallback": "Does the supplied evidence satisfy the stated contract?",
        "collection_scope": "Which listed members materially support this target?",
        "reproduction": "What was the independent retained-evidence comparison result?",
    }
    return str(item.get("reason") or questions.get(kind, f"Decide {kind}."))


def _semantic_fallback_choices(item: Mapping[str, Any]) -> list[str]:
    candidates = [
        str(candidate["invocation"])
        for candidate in item.get("producer_candidates", [])
    ]
    if candidates:
        return [*candidates, "fail:workflow"]
    choices = ["pass"]
    if item.get("workflow", {}).get("status") in {"fail", "unresolved"}:
        choices.append("fail:workflow")
    if any(
        evidence.get("result", {}).get("status") in {"fail", "unresolved"}
        for evidence in item.get("evidence", [])
    ):
        choices.append("fail:evidence")
    if item.get("integrity_status") in {"fail", "unresolved"}:
        choices.append("fail:integrity")
    return choices


def context_request_key(item: Mapping[str, Any]) -> str:
    """Return the stable subject key for one context-expansion request."""

    return json.dumps(
        [
            item.get("kind"),
            item.get("entry"),
            item.get("identity"),
            item.get("material"),
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _ordinary_allowed_decisions(
    item: Mapping[str, Any],
    kind: str,
    material: str | None,
    invocations: list[str] | None,
) -> list[Any]:
    if kind == "upstream_producer":
        if material is None or not invocations:
            raise ValidationToolError(
                "upstream producer review requires one material and candidates"
            )
        return [*invocations, "unresolved"]
    if kind == "collection_scope":
        collections = [str(value) for value in item.get("collections", [])]
        return [
            {
                "members": {
                    collection: ["<relative/member>"] for collection in collections
                }
            },
            {
                "members": {
                    collection: {"glob": "<relative/glob>"}
                    for collection in collections
                }
            },
            {
                "members": {
                    collection: {
                        "directory": "<relative/subdirectory>",
                        "membership_identity": "<sha256>",
                    }
                    for collection in collections
                }
            },
            "fail",
        ]
    if kind == "semantic_provenance":
        return ["pass", "fail"] if item.get("candidates") else ["fail"]
    if kind == "semantic_fallback":
        return _semantic_fallback_choices(item)
    if kind == "reproduction":
        return [
            "reproduced",
            "reproduction-fail",
            "not-run",
            "not-applicable",
        ]
    return ["keep"] if item.get("hard_failures") else ["pass", "fail"]


def _producer_dependencies(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(candidate["invocation"]): [
            {
                "path": candidate["coverage_identity"],
                "role": "producer",
                "members": [candidate["target_member"]],
            }
        ]
        for candidate in item.get("producer_candidates", [])
        if candidate.get("coverage_kind") == "scoped-collection"
        and candidate.get("target_member")
    }


def _ordinary_template(
    item: Mapping[str, Any],
    context_level: int = 0,
    *,
    material: str | None = None,
    invocations: list[str] | None = None,
) -> dict[str, Any]:
    kind = str(item["kind"])
    allowed = _ordinary_allowed_decisions(item, kind, material, invocations)
    if context_level == 0:
        allowed.append("needs_context")
    identity = (
        _fingerprint(item)
        if context_level == 0 and material is None
        else _fingerprint(
            {
                "queue_item": item,
                "material": material,
                "context_level": context_level,
            }
        )
    )
    template = {
        "id": identity,
        "kind": kind,
        "entry": item.get("entry"),
        "identity": item.get("identity"),
        "question": (
            f"Which recorded invocation produces `{material}`?"
            if material is not None
            else _question(item)
        ),
        "allowed_decisions": allowed,
        "context_level": context_level,
        "context_identity": None,
        "decision": None,
        "rationale": None,
    }
    if material is not None:
        template["material"] = material
    if kind == "semantic_fallback":
        template["producer_dependencies"] = _producer_dependencies(item)
    return template


def _upstream_templates(
    item: Mapping[str, Any], context_levels: Mapping[str, int]
) -> list[dict[str, Any]]:
    by_material: dict[str, list[str]] = {}
    for candidate in item.get("producer_candidates", []):
        material = str(candidate["material"])
        invocation = str(candidate["invocation"])
        values = by_material.setdefault(material, [])
        if invocation not in values:
            values.append(invocation)
    templates: list[dict[str, Any]] = []
    for material in sorted(by_material):
        subject = {**item, "material": material}
        level = context_levels.get(context_request_key(subject), 0)
        templates.append(
            _ordinary_template(
                item,
                level,
                material=material,
                invocations=by_material[material],
            )
        )
    return templates


def _orphan_templates(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    item: Mapping[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    candidates = ordered_orphan_candidates(item)
    if not candidates:
        raise ValidationToolError("orphan review cannot select an empty queue")
    fingerprint_context = orphan_fingerprint_context(
        scan,
        adjudication.get("schema_version"),
        str(item["entry"]),
        DECISION_SCHEMA_VERSION,
    )
    fingerprints = {
        str(candidate["identity"]): fingerprint_context.fingerprint(candidate)
        for candidate in candidates
    }
    notes = [
        str(note["sha256"])
        for note in item.get("validation_notes", [])
        if isinstance(note.get("sha256"), str)
    ]
    allowed = ["unresolved", "connected", *[f"retain:{note}" for note in notes]]
    templates: list[dict[str, Any]] = []
    questions, exact = refined_questions(candidates, item.get("subtree_splits", []))
    for question in questions:
        if len(templates) >= limit:
            break
        fingerprint = subtree_fingerprint(
            str(item["entry"]),
            question,
            fingerprints,
            {
                "notes": notes,
                "rules_version": scan.get("validation_rules_version"),
                "adjudication_schema": adjudication.get("schema_version"),
                "decision_schema": DECISION_SCHEMA_VERSION,
            },
        )
        templates.append(
            {
                "id": _fingerprint(
                    {
                        "subject": subtree_subject(
                            str(item["entry"]),
                            str(question["material"]),
                            str(question["root"]),
                        ),
                        "fingerprint": fingerprint,
                    }
                ),
                "kind": SUBTREE_REVIEW_KIND,
                "entry": item["entry"],
                "identity": question["root"],
                "material": question["material"],
                "question": (
                    "Does this subtree have one orphan lifecycle, or must it be split?"
                ),
                "allowed_decisions": subtree_allowed_decisions(
                    item.get("validation_notes", [])
                ),
                "context_level": 0,
                "context_identity": None,
                "decision": None,
                "rationale": None,
            }
        )
    for candidate in exact:
        if len(templates) >= limit:
            break
        identity = str(candidate["identity"])
        templates.append(
            {
                "id": _fingerprint(
                    {
                        "queue": item,
                        "candidate": identity,
                        "fingerprint": fingerprints[identity],
                    }
                ),
                "kind": "orphan_candidate",
                "entry": item["entry"],
                "identity": identity,
                "question": "How is this locally unconnected candidate classified?",
                "allowed_decisions": allowed,
                "context_level": 0,
                "context_identity": None,
                "decision": None,
                "rationale": None,
            }
        )
    return templates, fingerprints


def _structural_initial_context_level(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection,
) -> tuple[int, Mapping[str, Any] | None]:
    """Start at expanded context only when minimum context cannot be terminal."""

    if queue_item.get("kind") != "collection_scope":
        return 0, None
    requested_sections = {
        str(section) for section in queue_item.get("sections", []) if section
    }
    if not requested_sections:
        return 0, None
    available_sections = {
        str(section.get("section"))
        for section in projection.entry(queue_item.get("entry")).get("sections", [])
        if isinstance(section, Mapping) and section.get("section")
    }
    if requested_sections.isdisjoint(available_sections):
        return 0, None
    if projection.eligible_invocations(queue_item):
        return 0, None
    template = _ordinary_template(queue_item, 1)
    minimum = _minimum_context(
        scan, adjudication, queue_item, template, projection
    )
    expanded = _expanded_context(scan, queue_item, minimum, projection)
    packet = _render_packet_with_contexts(
        str(scan.get("summary", "")), [template], "0" * 64, [expanded]
    )
    return (
        (1, expanded)
        if len(packet.encode("utf-8")) <= MAX_PACKET_BYTES
        else (0, minimum)
    )


def _initial_ordinary_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    context_levels: Mapping[str, int],
    projection: _ReviewExchangeProjection,
) -> tuple[int, Mapping[str, Any] | None]:
    """Return the initial level and any context already built to choose it."""

    requested = context_levels.get(context_request_key(queue_item), 0)
    if requested == 1:
        return 1, None
    return _structural_initial_context_level(
        scan, adjudication, queue_item, projection
    )


def _template_items(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    context_levels: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    levels = context_levels or {}
    items: list[dict[str, Any]] = []
    orphan_fingerprints: dict[str, dict[str, str]] = {}
    projection = _ReviewExchangeProjection(scan, adjudication)
    preprojected_contexts: dict[str, Mapping[str, Any]] = {}
    for queue_item in adjudication["review_queue"]:
        remaining = MAX_PACKET_ITEMS - len(items)
        if remaining < 1:
            break
        if queue_item["kind"] == "upstream_producer":
            expanded = _upstream_templates(queue_item, levels)
            if len(expanded) > remaining:
                if items:
                    break
                raise ValidationToolError(
                    "one upstream-producer question group exceeds the item bound"
                )
            items.extend(expanded)
            continue
        if queue_item["kind"] != "orphan_candidates":
            level, context = _initial_ordinary_context(
                scan, adjudication, queue_item, levels, projection
            )
            template = _ordinary_template(queue_item, level)
            items.append(template)
            if context is not None:
                preprojected_contexts[template["id"]] = context
            continue
        expanded, fingerprints = _orphan_templates(
            scan, adjudication, queue_item, remaining
        )
        items.extend(expanded)
        orphan_fingerprints[str(queue_item["entry"])] = fingerprints
    for item in items:
        context = preprojected_contexts.get(item["id"])
        if context is None:
            context = _packet_context(scan, adjudication, item, projection)
        item["context_identity"] = _fingerprint(context)
    return items, orphan_fingerprints


def _all_template_items(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    context_levels: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Return the complete linear question set for a bounded session."""

    prepared = _prepare_all_template_items(scan, adjudication, context_levels)
    return prepared.items, prepared.orphan_fingerprints


def _prepare_all_template_items(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    context_levels: Mapping[str, int] | None = None,
) -> _PreparedReviewItems:
    """Project every session template and context exactly once."""

    levels = context_levels or {}
    items: list[dict[str, Any]] = []
    orphan_fingerprints: dict[str, dict[str, str]] = {}
    projection = _ReviewExchangeProjection(scan, adjudication)
    preprojected_contexts: dict[str, Mapping[str, Any]] = {}
    for queue_item in adjudication["review_queue"]:
        if queue_item["kind"] == "upstream_producer":
            items.extend(_upstream_templates(queue_item, levels))
        elif queue_item["kind"] == "orphan_candidates":
            candidates = queue_item.get("candidates", [])
            expanded, fingerprints = _orphan_templates(
                scan, adjudication, queue_item, max(1, len(candidates))
            )
            items.extend(expanded)
            orphan_fingerprints[str(queue_item["entry"])] = fingerprints
        else:
            level, context = _initial_ordinary_context(
                scan, adjudication, queue_item, levels, projection
            )
            template = _ordinary_template(queue_item, level)
            items.append(template)
            if context is not None:
                preprojected_contexts[template["id"]] = context
    contexts = []
    for item in items:
        context = preprojected_contexts.get(item["id"])
        if context is None:
            context = _packet_context(scan, adjudication, item, projection)
        item["context_identity"] = _fingerprint(context)
        contexts.append(context)
    return _PreparedReviewItems(items, contexts, orphan_fingerprints)


def _semantic_provenance_context(
    adjudication: AdjudicationRecord, queue_item: Mapping[str, Any]
) -> dict[str, Any]:
    association = next(
        (
            row
            for row in adjudication.get("summary", [])
            if row.get("item") == queue_item.get("identity")
        ),
        {},
    )
    return {
        "summary": {
            key: queue_item.get(key)
            for key in ("identity", "section", "line", "reason")
        },
        "association": association,
        "candidates": queue_item.get("candidates", []),
    }


def _upstream_context(queue_item: Mapping[str, Any], material: Any) -> dict[str, Any]:
    return {
        "target": queue_item.get("identity"),
        "material": material,
        "candidates": [
            candidate
            for candidate in queue_item.get("producer_candidates", [])
            if candidate.get("material") == material
        ],
        "workflow": queue_item.get("workflow"),
        "evidence": queue_item.get("evidence", []),
    }


def _collection_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> dict[str, Any]:
    structures = {}
    for collection in queue_item.get("collections", []):
        raw_path = scan.get("resolved_paths", {}).get(collection)
        choices: list[dict[str, Any]] = []
        direct_files: list[str] = []
        if isinstance(raw_path, str) and Path(raw_path).is_dir():
            root = Path(raw_path)
            choices = compact_directory_choices(root)
            direct_files = sorted(
                child.name for child in root.iterdir() if child.is_file()
            )
        structures[str(collection)] = {
            "directory_choices": choices,
            "directory_choice_count": len(choices),
            "direct_sibling_files": direct_files,
            "direct_sibling_file_count": len(direct_files),
        }
    invocations = _eligible_invocations(scan, queue_item, projection)
    context = {
        "identity": queue_item.get("identity"),
        "collections": queue_item.get("collections", []),
        "reason": queue_item.get("reason"),
        "target_dependencies": _target_row(adjudication, queue_item, projection).get(
            "dependencies", []
        ),
        "recorded_invocations": invocations,
        "collection_structure": structures,
        "selection_contract": (
            "Choose an exact directory selector from collection_structure when "
            "one complete subdirectory is the meaningful retained unit. Lists "
            "and glob selectors remain valid for other exact scopes."
        ),
    }
    return context


def _entry(
    scan: ScanRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> Mapping[str, Any]:
    if projection is not None:
        return projection.entry(queue_item.get("entry"))
    return next(
        (
            entry
            for entry in scan.get("entries", [])
            if entry.get("id") == queue_item.get("entry")
        ),
        {},
    )


def _target_row(
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> Mapping[str, Any]:
    if projection is not None:
        return projection.target(queue_item.get("entry"), queue_item.get("identity"))
    entry = next(
        (
            row
            for row in adjudication.get("entries", [])
            if row.get("id") == queue_item.get("entry")
        ),
        {},
    )
    return next(
        (
            row
            for row in entry.get("targets", [])
            if row.get("target") == queue_item.get("identity")
        ),
        {},
    )


def _eligible_invocations(
    scan: ScanRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> list[dict[str, Any]]:
    if projection is not None:
        return projection.eligible_invocations(queue_item)
    session = ReviewQuerySession(ReviewContextIndex.build(scan))
    return [
        {
            "invocation": invocation.key,
            "entry": invocation.entry_id,
            "line": invocation.command.get("line"),
            "command": invocation.command.get("command", ""),
            "path_arguments": invocation.command.get("path_arguments", []),
        }
        for invocation in session.eligible_candidate_invocations(
            str(queue_item.get("entry", "")),
            str(queue_item.get("identity", "")),
            queue_item.get("sections", []),
        )
    ]


def _target_review_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> dict[str, Any]:
    kind = queue_item["kind"]
    expected = {
        "mechanical_failure": (
            "Deterministic failures remain failures; semantic review may retain "
            "the finding but cannot turn it into a pass."
        ),
        "semantic_fallback": (
            "Integrity and provenance pass only when the supplied workflow and "
            "presented-evidence associations support the retained target."
        ),
    }[kind]
    entry = _entry(scan, queue_item, projection)
    return {
        "target": queue_item.get("identity"),
        "sections": queue_item.get("sections", []),
        "failed_checks": queue_item.get("hard_failures", []),
        "observed": {
            "integrity": queue_item.get("integrity"),
            "integrity_status": queue_item.get("integrity_status"),
            "workflow": queue_item.get("workflow"),
            "evidence": queue_item.get("evidence", []),
        },
        "expected_contract": expected,
        "producer_candidates": queue_item.get("producer_candidates", []),
        "target_dependencies": _target_row(adjudication, queue_item, projection).get(
            "dependencies", []
        ),
        "authored_validation_notes": entry.get("validation_notes", []),
    }


def _reproduction_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> dict[str, Any]:
    entry = _entry(scan, queue_item, projection)
    return {
        "target": queue_item.get("identity"),
        "sections": queue_item.get("sections", []),
        "eligible_invocations": _eligible_invocations(scan, queue_item, projection),
        "retained_evidence": _target_row(adjudication, queue_item, projection).get(
            "dependencies", []
        ),
        "comparison_rule": entry.get("validation_notes", []),
        "output_constraint": (
            "Run only one listed invocation with every output redirected to a "
            "temporary location; do not change retained evidence."
        ),
    }


def _minimum_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> dict[str, Any]:
    kind = item["kind"]
    if kind == "semantic_provenance":
        return _semantic_provenance_context(adjudication, queue_item)
    if kind == "upstream_producer":
        return _upstream_context(queue_item, item.get("material"))
    if kind == "collection_scope":
        return _collection_context(scan, adjudication, queue_item, projection)
    if kind in {"mechanical_failure", "semantic_fallback"}:
        return _target_review_context(scan, adjudication, queue_item, projection)
    if kind == "reproduction":
        return _reproduction_context(scan, adjudication, queue_item, projection)
    raise ValidationToolError(f"review kind lacks a context projection: {kind}")


def _expanded_entry_passages(
    scan: ScanRecord,
    entry: Mapping[str, Any],
    queue_item: Mapping[str, Any],
) -> tuple[dict[str, str], int]:
    """Return the requested authored entry sections and their byte count."""

    used_bytes = 0
    section_passages: dict[str, str] = {}
    requested_sections = set(queue_item.get("sections", []))
    entry_path = entry.get("path")
    if requested_sections and isinstance(entry_path, str):
        raw_path = scan.get("resolved_paths", {}).get(entry_path)
        path = (
            Path(raw_path)
            if isinstance(raw_path, str)
            else Path(scan["project_root"]) / entry_path
        )
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValidationToolError(
                f"focused entry context cannot be read: {entry_path}: {exc}"
            ) from exc
        for section in entry.get("sections", []):
            name = section.get("section")
            if name not in requested_sections:
                continue
            start = int(section["line"]) - 1
            end = int(section["end_line"])
            passage = "\n".join(lines[start:end])
            used_bytes += len(passage.encode("utf-8"))
            if used_bytes > MAX_EXPANDED_CONTEXT_BYTES:
                raise ValidationToolError(
                    "focused entry passage exceeds its packet context budget"
                )
            section_passages[str(name)] = passage
    return section_passages, used_bytes


def _expanded_context(
    scan: ScanRecord,
    queue_item: Mapping[str, Any],
    minimum: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> dict[str, Any]:
    entry = _entry(scan, queue_item, projection)
    section_passages, _ = _expanded_entry_passages(scan, entry, queue_item)
    return {
        "minimum": minimum,
        "focused_expansion": {
            "entry_path": entry.get("path"),
            "sections": queue_item.get("sections", []),
            "validation_notes": entry.get("validation_notes", []),
            "decision_hint": queue_item.get("reason"),
            **(
                {"entry_section_passages": section_passages} if section_passages else {}
            ),
        },
    }


def _packet_context(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    item: Mapping[str, Any],
    projection: _ReviewExchangeProjection | None = None,
) -> Mapping[str, Any]:
    projection = projection or _ReviewExchangeProjection(scan, adjudication)
    queue_item = projection.queue_item(item)
    if queue_item is None:
        return {}
    if item["kind"] == SUBTREE_REVIEW_KIND:
        question = {
            "root": item.get("identity"),
            "material": item.get("material"),
            "candidates": candidates_below(
                projection.subtree_candidates(item.get("entry"), item.get("identity")),
                str(item.get("identity", "")),
            ),
        }
        return {
            "structural_summary": structural_summary(question),
            "reachability_reason": queue_item.get("reason"),
            "authored_validation_notes": queue_item.get("validation_notes", []),
            "decision_constraint": (
                "Classify only if every current and future compatible residual "
                "descendant shares one lifecycle; otherwise split."
            ),
        }
    if item["kind"] == "orphan_candidate":
        return {
            "candidate": projection.candidate(item.get("entry"), item.get("identity")),
            "reachability_reason": queue_item.get("reason"),
            "validation_notes": queue_item.get("validation_notes", []),
        }
    context = _minimum_context(scan, adjudication, queue_item, item, projection)
    return (
        _expanded_context(scan, queue_item, context, projection)
        if item.get("context_level") == 1
        else context
    )


def _shareable_context_projection(
    item: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Return exact reusable page context while retaining target-specific facts."""

    if item.get("kind") != "collection_scope":
        return context
    shared = {
        key: context[key] for key in COLLECTION_SHARED_CONTEXT_KEYS if key in context
    }
    return shared if shared else context


def _target_specific_context(
    item: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Return question-local facts omitted from its shareable projection."""

    if item.get("kind") != "collection_scope":
        return {}
    return {
        key: value
        for key, value in context.items()
        if key not in COLLECTION_SHARED_CONTEXT_KEYS
    }


def _render_packet_with_contexts(
    summary: str,
    items: list[dict[str, Any]],
    continuation: str,
    contexts: list[Mapping[str, Any]],
) -> str:
    shareable_contexts = [
        _shareable_context_projection(item, context)
        for item, context in zip(items, contexts, strict=True)
    ]
    target_contexts = [
        _target_specific_context(item, context)
        for item, context in zip(items, contexts, strict=True)
    ]
    rendered_context_bytes = [
        _json_bytes(context) for context in shareable_contexts
    ]
    rendered_identities = [_sha256(encoded) for encoded in rendered_context_bytes]
    identity_counts = Counter(rendered_identities)
    shared_identities = {
        identity for identity, count in identity_counts.items() if count > 1
    }
    shared_labels: dict[str, str] = {}
    shared_bytes: dict[str, bytes] = {}
    for identity, encoded in zip(
        rendered_identities, rendered_context_bytes, strict=True
    ):
        if identity not in shared_identities:
            continue
        previous = shared_bytes.setdefault(identity, encoded)
        if previous != encoded:
            raise ValidationToolError("shared review context identity collides")
        if identity not in shared_labels:
            shared_labels[identity] = f"C{len(shared_labels) + 1:03d}"
    lines = [
        "# Validation Review Packet",
        "",
        f"- Log: `{summary}`",
        f"- Continuation: `{continuation}`",
        f"- Questions: {len(items)}",
        "- Edit only the paired template's decision and rationale fields.",
    ]
    rendered_shared: set[str] = set()
    for identity, context in zip(
        rendered_identities, shareable_contexts, strict=True
    ):
        if identity not in shared_identities or identity in rendered_shared:
            continue
        rendered_shared.add(identity)
        lines.extend(
            [
                "",
                f"## Shared Context {shared_labels[identity]} — `{identity}`",
                "",
                "```json",
                json.dumps(
                    context,
                    sort_keys=True,
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )
    for number, (item, context, target_context, context_identity) in enumerate(
        zip(items, contexts, target_contexts, rendered_identities, strict=True), 1
    ):
        lines.extend(
            [
                "",
                f"## Q{number:03d} — {item.get('entry')}: {item.get('identity')}",
                "",
                f"- Kind: `{item['kind']}`",
                f"- Question: {item['question']}",
                "- Allowed decisions: "
                + ", ".join(
                    f"`{json.dumps(value, sort_keys=True)}`"
                    for value in item["allowed_decisions"]
                ),
            ]
        )
        if context_identity in shared_identities:
            lines.append(
                "- Shared context: "
                f"shared context {shared_labels[context_identity]} "
                f"(`{context_identity}`)"
            )
            if target_context:
                lines.extend(
                    [
                        "- Target-specific context:",
                        "",
                        "```json",
                        json.dumps(
                            target_context,
                            sort_keys=True,
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "```",
                    ]
                )
        else:
            lines.extend(
                [
                    "- Context:",
                    "",
                    "```json",
                    json.dumps(
                        context,
                        sort_keys=True,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                ]
            )
    return "\n".join(lines) + "\n"


def _bounded_collection_packet_context(
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Project an oversized collection context into a terminal bounded view."""

    minimum = context.get("minimum", context)
    if not isinstance(minimum, Mapping):
        minimum = {}
    dependencies = []
    for dependency in minimum.get("target_dependencies", []):
        if not isinstance(dependency, Mapping):
            continue
        members = dependency.get("members", [])
        dependencies.append(
            {
                key: copy.deepcopy(dependency[key])
                for key in ("path", "role")
                if key in dependency
            }
            | (
                {
                    "members": list(members[:20]),
                    "member_count": len(members),
                    "members_truncated": len(members) > 20,
                }
                if isinstance(members, list) and members
                else {}
            )
        )
    invocations = []
    for invocation in minimum.get("recorded_invocations", [])[:20]:
        if not isinstance(invocation, Mapping):
            continue
        invocations.append(
            {
                key: copy.deepcopy(invocation[key])
                for key in ("invocation", "entry", "line", "command")
                if key in invocation
            }
        )
    structures = _bounded_collection_structures(minimum)
    return {
        "context_projection": "bounded-terminal-collection",
        "identity": minimum.get("identity"),
        "collections": minimum.get("collections", []),
        "reason": minimum.get("reason"),
        "target_dependencies": dependencies,
        "recorded_invocations": invocations,
        "collection_structure": structures,
        "decision_constraint": (
            "Select a listed hash-bound directory only when it establishes the "
            "exact material set. The selector expands to all current regular-file "
            "descendants; otherwise use an exact list or glob, or record failure."
        ),
        "omitted_context": (
            "The complete collection context exceeds the packet byte bound; "
            "its full identity remains continuation-bound."
        ),
    }


def _bounded_collection_structures(
    minimum: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bound directory choices and loose files without expanding descendants."""

    structures: dict[str, dict[str, Any]] = {}
    source_structures = minimum.get("collection_structure", {})
    if isinstance(source_structures, Mapping):
        for collection, structure in source_structures.items():
            if not isinstance(structure, Mapping):
                continue
            choices = structure.get("directory_choices", [])
            direct_files = structure.get("direct_sibling_files", [])
            if not isinstance(choices, list):
                continue
            if not isinstance(direct_files, list):
                direct_files = []
            structures[str(collection)] = {
                "directory_choices": copy.deepcopy(choices[:20]),
                "directory_choice_count": len(choices),
                "choices_truncated": len(choices) > 20,
                "direct_sibling_files": list(direct_files[:80]),
                "direct_sibling_file_count": len(direct_files),
                "direct_sibling_files_truncated": len(direct_files) > 80,
            }
    return structures


def _render_contexts(
    summary: str,
    items: list[dict[str, Any]],
    continuation: str,
    contexts: list[Mapping[str, Any]],
) -> tuple[str, list[Mapping[str, Any]]]:
    """Render once, bounding oversized terminal collection projections."""

    packet = _render_packet_with_contexts(summary, items, continuation, contexts)
    if len(packet.encode("utf-8")) <= MAX_PACKET_BYTES or len(items) != 1:
        return packet, contexts
    if items[0].get("kind") != "collection_scope":
        return packet, contexts
    bounded_contexts: list[Mapping[str, Any]] = [
        _bounded_collection_packet_context(contexts[0])
    ]
    return (
        _render_packet_with_contexts(summary, items, continuation, bounded_contexts),
        bounded_contexts,
    )


def _deferred_orphan_index(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> dict[str, Any] | None:
    """Build one immutable orphan-review index when paging is worthwhile."""

    queue = adjudication.get("review_queue", [])
    if not queue or any(item.get("kind") != "orphan_candidates" for item in queue):
        return None
    indexed_items: list[dict[str, Any]] = []
    for item in queue:
        questions, _ = refined_questions(
            item.get("candidates", []), item.get("subtree_splits", [])
        )
        if questions:
            return None
        entry = str(item["entry"])
        notes = [
            str(note["sha256"])
            for note in item.get("validation_notes", [])
            if isinstance(note.get("sha256"), str)
        ]
        allowed = [
            "unresolved",
            "connected",
            *[f"retain:{note}" for note in notes],
        ]
        for candidate in ordered_orphan_candidates(item):
            identity = str(candidate["identity"])
            context = {
                "candidate": candidate,
                "reachability_reason": item.get("reason"),
                "validation_notes": item.get("validation_notes", []),
            }
            fingerprint = orphan_candidate_fingerprint(
                scan,
                adjudication.get("schema_version"),
                entry,
                candidate,
                DECISION_SCHEMA_VERSION,
            )
            indexed_items.append(
                {
                    "template": {
                        "id": _fingerprint(
                            {
                                "queue": item,
                                "candidate": identity,
                                "fingerprint": fingerprint,
                            }
                        ),
                        "kind": "orphan_candidate",
                        "entry": entry,
                        "identity": identity,
                        "question": (
                            "How is this locally unconnected candidate classified?"
                        ),
                        "allowed_decisions": allowed,
                        "context_level": 0,
                        "context_identity": _fingerprint(context),
                        "decision": None,
                        "rationale": None,
                    },
                    "context": context,
                    "fingerprint": fingerprint,
                }
            )
    if len(indexed_items) <= MAX_PACKET_ITEMS:
        return None
    session_identity = _fingerprint(
        {
            "context_projection_version": CONTEXT_PROJECTION_VERSION,
            "summary": scan["summary"],
            "rules": scan["validation_rules_version"],
            "scan": scan["input_fingerprint"],
            "date": adjudication["date"],
            "items": [
                {
                    "id": item["template"]["id"],
                    "fingerprint": item["fingerprint"],
                }
                for item in indexed_items
            ],
        }
    )
    return {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "context_projection_version": CONTEXT_PROJECTION_VERSION,
        "session_identity": session_identity,
        "summary": scan["summary"],
        "review_kind": "orphan_candidates",
        "items": indexed_items,
    }


def _bounded_session_index(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    items: list[dict[str, Any]],
    orphan_fingerprints: Mapping[str, Mapping[str, str]],
    contexts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    indexed_items: list[dict[str, Any]] = []
    projected = (
        list(contexts)
        if contexts is not None
        else [_packet_context(scan, adjudication, item) for item in items]
    )
    for item, context in zip(items, projected, strict=True):
        fingerprint = (
            orphan_fingerprints.get(str(item.get("entry")), {}).get(
                str(item.get("identity"))
            )
            if item["kind"] == "orphan_candidate"
            else item["id"]
        )
        indexed_items.append(
            {
                "template": copy.deepcopy(item),
                "context": context,
                "fingerprint": fingerprint,
            }
        )
    for indexed in indexed_items:
        locality = _review_locality(indexed["template"], indexed["context"])
        indexed["locality"] = locality
        indexed["locality_identity"] = _fingerprint(locality)
    indexed_items.sort(key=_indexed_locality_sort_key)
    session_identity = _fingerprint(
        {
            "context_projection_version": CONTEXT_PROJECTION_VERSION,
            "summary": scan["summary"],
            "rules": scan["validation_rules_version"],
            "scan": scan["input_fingerprint"],
            "date": adjudication["date"],
            "items": [
                {
                    "id": item["template"]["id"],
                    "fingerprint": item["fingerprint"],
                }
                for item in indexed_items
            ],
        }
    )
    return {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "context_projection_version": CONTEXT_PROJECTION_VERSION,
        "session_identity": session_identity,
        "summary": scan["summary"],
        "review_kind": "bounded_review",
        "items": indexed_items,
        "orphan_fingerprints": copy.deepcopy(dict(orphan_fingerprints)),
    }


def _locality_strings(value: Any, field: str | None = None) -> tuple[str, ...]:
    """Return stable non-empty strings from one locality-bearing value."""

    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Mapping):
        if field is not None:
            return _locality_strings(value.get(field))
        return tuple(
            sorted(
                {
                    text
                    for key in sorted(value)
                    for text in _locality_strings(value[key])
                }
            )
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(
            sorted(
                {
                    text
                    for member in value
                    for text in _locality_strings(member, field)
                }
            )
        )
    return ()


def _review_locality(
    item: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the deterministic page-locality facts for one review question."""

    minimum = context.get("minimum", context)
    if not isinstance(minimum, Mapping):
        minimum = {}
    focused = context.get("focused_expansion", {})
    if not isinstance(focused, Mapping):
        focused = {}
    summary = minimum.get("summary", {})
    if not isinstance(summary, Mapping):
        summary = {}
    sections = tuple(
        sorted(
            {
                *(_locality_strings(minimum.get("section"))),
                *(_locality_strings(minimum.get("sections"), "section")),
                *(_locality_strings(summary.get("section"))),
                *(_locality_strings(focused.get("sections"), "section")),
            }
        )
    )
    candidates = minimum.get("candidates", context.get("candidates", []))
    relationships = {
        *(_locality_strings(item.get("material"))),
        *(_locality_strings(candidates, "invocation")),
        *(_locality_strings(candidates, "material")),
    }
    if item.get("kind") == "collection_scope":
        relationships.add(
            _fingerprint(_shareable_context_projection(item, context))
        )
    collections = {
        *(_locality_strings(minimum.get("collections"))),
        *(_locality_strings(context.get("collections"))),
    }
    return {
        "entry": str(item.get("entry", "")),
        "sections": list(sections),
        "producer_relationship": sorted(relationships),
        "kind": str(item.get("kind", "")),
        "collection_roots": sorted(collections),
    }


def _indexed_locality_identity(indexed: Mapping[str, Any]) -> str:
    identity = indexed.get("locality_identity")
    if isinstance(identity, str) and identity:
        return identity
    template = indexed.get("template", {})
    context = indexed.get("context", {})
    if not isinstance(template, Mapping) or not isinstance(context, Mapping):
        return ""
    return _fingerprint(_review_locality(template, context))


def _indexed_locality_sort_key(indexed: Mapping[str, Any]) -> tuple[str, ...]:
    """Order locality facts first and use question identity as a stable tie-break."""

    locality = indexed.get("locality", {})
    if not isinstance(locality, Mapping):
        locality = {}
    template = indexed.get("template", {})
    if not isinstance(template, Mapping):
        template = {}
    return (
        str(locality.get("entry", "")),
        json.dumps(locality.get("sections", []), ensure_ascii=False),
        json.dumps(locality.get("producer_relationship", []), ensure_ascii=False),
        str(locality.get("kind", "")),
        json.dumps(locality.get("collection_roots", []), ensure_ascii=False),
        str(template.get("identity", "")),
        str(template.get("material", "")),
        str(template.get("id", "")),
    )


def _current_page_state(
    state: Mapping[str, Any],
    continuation: str,
    count: int,
    review_diagnostics: bool,
) -> dict[str, Any]:
    """Build transient state for one issued review page."""

    offset = int(state["next_offset"])
    page_number = int(state["next_page_number"])
    current = {
        "continuation": continuation,
        "count": count,
        "offset": offset,
        "page_number": page_number,
        "batch_identity": _fingerprint(
            {
                "session": state["session_identity"],
                "page": page_number,
                "continuation": continuation,
            }
        ),
    }
    if not review_diagnostics:
        return current
    previous = state.get("current")
    current["issued_at_epoch_seconds"] = (
        previous.get("issued_at_epoch_seconds")
        if isinstance(previous, Mapping)
        and previous.get("continuation") == continuation
        else time.time()
    )
    return current


def _page_diagnostics(
    selected: Sequence[Mapping[str, Any]],
    rendered_contexts: Sequence[Mapping[str, Any]],
    packet_bytes: int,
    page_number: int,
    next_item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return opt-in packet and context composition measurements."""

    context_sizes: Counter[str] = Counter()
    context_identities: Counter[str] = Counter()
    unique_context_bytes: dict[str, int] = {}
    shared_projection_identities: Counter[str] = Counter()
    unique_shared_projection_bytes: dict[str, int] = {}
    shared_projection_bytes = 0
    for indexed, context in zip(selected, rendered_contexts, strict=True):
        template_item = indexed["template"]
        family = str(template_item.get("kind", "unknown"))
        context_bytes = len(_json_bytes(context))
        context_sizes[family] += context_bytes
        identity = str(template_item.get("context_identity", ""))
        if identity:
            context_identities[identity] += 1
            unique_context_bytes.setdefault(identity, context_bytes)
        shared_projection = _shareable_context_projection(template_item, context)
        shared_projection_bytes += len(_json_bytes(shared_projection))
        shared_identity = _fingerprint(shared_projection)
        shared_projection_identities[shared_identity] += 1
        unique_shared_projection_bytes.setdefault(
            shared_identity, len(_json_bytes(shared_projection))
        )
    locality_identities = [_indexed_locality_identity(item) for item in selected]
    locality_clusters = sum(
        identity != previous
        for identity, previous in zip(
            locality_identities,
            [None, *locality_identities[:-1]],
            strict=True,
        )
    )
    split_locality_cluster = bool(
        selected
        and next_item is not None
        and _indexed_locality_identity(selected[-1])
        == _indexed_locality_identity(next_item)
    )
    return {
        "page_number": page_number,
        "item_count": len(selected),
        "items_by_kind": dict(
            sorted(
                Counter(
                    str(item["template"].get("kind", "unknown"))
                    for item in selected
                ).items()
            )
        ),
        "packet_bytes": packet_bytes,
        "normal_packet_target_bytes": TARGET_PACKET_BYTES,
        "hard_packet_ceiling_bytes": MAX_PACKET_BYTES,
        "locality_overflow_used": packet_bytes > TARGET_PACKET_BYTES,
        "locality_overflow_bytes": max(0, packet_bytes - TARGET_PACKET_BYTES),
        "locality_cluster_count": locality_clusters,
        "split_locality_cluster": split_locality_cluster,
        "context_bytes": sum(context_sizes.values()),
        "unique_context_bytes": sum(unique_context_bytes.values()),
        "context_bytes_by_projection_family": dict(sorted(context_sizes.items())),
        "shared_context_identities": len(context_identities),
        "repeated_shared_context_identities": sum(
            count - 1 for count in context_identities.values() if count > 1
        ),
        "shared_projection_identities": len(shared_projection_identities),
        "shared_projection_bytes": shared_projection_bytes,
        "repeated_shared_projection_identities": sum(
            count - 1
            for count in shared_projection_identities.values()
            if count > 1
        ),
        "unique_shared_projection_bytes": sum(
            unique_shared_projection_bytes.values()
        ),
    }


def _render_session_page_candidate(
    index: Mapping[str, Any],
    state: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    count: int,
) -> tuple[list[dict[str, Any]], str, str, list[Mapping[str, Any]]]:
    """Render one prefix with continuation identity bound to its exact order."""

    selected = candidates[:count]
    offset = int(state["next_offset"])
    page_number = int(state["next_page_number"])
    items = [copy.deepcopy(item["template"]) for item in selected]
    shared_context_identities = [
        _fingerprint(
            _shareable_context_projection(item["template"], item["context"])
        )
        for item in selected
    ]
    continuation = _fingerprint(
        {
            "session": state["session_identity"],
            "page": page_number,
            "offset": offset,
            "items": [item["id"] for item in items],
            "shared_contexts": shared_context_identities,
        }
    )
    packet, rendered_contexts = _render_contexts(
        str(index["summary"]),
        items,
        continuation,
        [item["context"] for item in selected],
    )
    return items, continuation, packet, rendered_contexts


def _bounded_session_page(
    index: Mapping[str, Any],
    state: Mapping[str, Any],
    page_candidates: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[list[dict[str, Any]], str, str, list[Mapping[str, Any]]],
    Sequence[Mapping[str, Any]],
]:
    """Select one target-bounded page with locality-preserving soft overflow."""

    candidates = page_candidates[:MAX_PACKET_ITEMS]
    low = 1
    high = len(candidates)
    bounded = None
    while low <= high:
        count = (low + high) // 2
        rendered = _render_session_page_candidate(index, state, candidates, count)
        if len(rendered[2].encode("utf-8")) <= TARGET_PACKET_BYTES:
            bounded = rendered
            low = count + 1
        else:
            high = count - 1
    if bounded is None:
        minimum = _render_session_page_candidate(index, state, candidates, 1)
        if len(minimum[2].encode("utf-8")) > MAX_PACKET_BYTES:
            raise ValidationToolError(
                "one minimum-sufficient review question exceeds the hard packet ceiling"
            )
        bounded = minimum
    target_count = len(bounded[0])
    target_cluster = _indexed_locality_identity(candidates[target_count - 1])
    cluster_end = target_count
    while (
        cluster_end < len(candidates)
        and _indexed_locality_identity(candidates[cluster_end]) == target_cluster
    ):
        cluster_end += 1
    cluster_is_complete = not (
        cluster_end == len(candidates)
        and len(page_candidates) > len(candidates)
        and _indexed_locality_identity(page_candidates[len(candidates)])
        == target_cluster
    )
    if cluster_end > target_count and cluster_is_complete:
        extended = _render_session_page_candidate(
            index, state, candidates, cluster_end
        )
        if len(extended[2].encode("utf-8")) <= MAX_PACKET_BYTES:
            bounded = extended
    return bounded, candidates


def _session_page(
    session_dir: Path,
    index: Mapping[str, Any],
    state: dict[str, Any],
    review_diagnostics: bool = False,
) -> dict[str, Any]:
    """Write the next bounded page and advance only small session state."""

    offset = int(state["next_offset"])
    page_number = int(state["next_page_number"])
    page_candidates = _session_items(
        session_dir, index, offset, MAX_PACKET_ITEMS + 1
    )
    if not page_candidates:
        raise ValidationToolError("review session has no next page")
    bounded, candidates = _bounded_session_page(index, state, page_candidates)
    items, continuation, packet, rendered_contexts = bounded
    selected = candidates[: len(items)]
    next_item = (
        page_candidates[len(items)] if len(page_candidates) > len(items) else None
    )
    page_dir = session_dir / f"page-{page_number:06d}"
    page_dir.mkdir(exist_ok=True)
    template = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "items": items,
    }
    internal = {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "continuation": continuation,
        "template": copy.deepcopy(template),
        "review_session": {
            "output_dir": state["output_dir"],
            "session": state["session"],
            "session_identity": state["session_identity"],
            "page_number": page_number,
            "offset": offset,
            "summary": state["summary_path"],
        },
    }
    packet_path = page_dir / "review-packet.md"
    decision_path = page_dir / "review-decisions.json"
    _atomic_write(packet_path, packet.encode("utf-8"))
    _atomic_write(
        decision_path,
        (
            json.dumps(template, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    _atomic_write(page_dir / INTERNAL_FILENAME, _json_bytes(internal))
    state["current"] = _current_page_state(
        state,
        continuation,
        len(items),
        review_diagnostics,
    )
    _atomic_write(session_dir / SESSION_STATE_FILENAME, _json_bytes(state))
    packet_bytes = len(packet.encode("utf-8"))
    result = {
        "status": "review_required",
        "review_packet": packet_path.as_posix(),
        "decision_file": decision_path.as_posix(),
        "continuation": continuation,
        "session_identity": state["session_identity"],
        "session": state["session"],
        "review_kind": state["review_kind"],
        "item_count": len(items),
        "byte_count": packet_bytes,
    }
    if review_diagnostics:
        result["page_diagnostics"] = _page_diagnostics(
            selected,
            rendered_contexts,
            packet_bytes,
            page_number,
            next_item,
        )
    return result


def _create_review_session(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    controller_state: Mapping[str, Any],
    index: dict[str, Any],
    review_diagnostics: bool = False,
) -> dict[str, Any]:
    """Create one project-local durable session for bounded review work."""

    project_root = Path(scan["project_root"]).resolve()
    log_root = str(scan.get("log_root") or Path(scan["summary"]).with_suffix(""))
    output_dir = (project_root / log_root).resolve()
    locator = _session_locator(index["session_identity"])
    session_dir = _session_path(output_dir, locator)
    try:
        session_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        recovered = resume_review_session(
            output_dir,
            {
                "kind": "paged",
                "session": locator,
                "session_identity": index["session_identity"],
                "review_kind": index["review_kind"],
            },
            review_diagnostics=review_diagnostics,
        )
        if recovered.get("status") != "review_required":
            raise ValidationToolError(
                "unreferenced review session contains completed work"
            )
        return recovered
    base = {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "session_identity": index["session_identity"],
        "scan": scan,
        "adjudication": adjudication,
        "controller": copy.deepcopy(dict(controller_state)),
    }
    _atomic_write(session_dir / SESSION_BASE_FILENAME, _json_bytes(base))
    retained_index = _write_session_index(session_dir, index)
    state = {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "session_identity": index["session_identity"],
        "session": locator,
        "summary": scan["summary"],
        "review_kind": index["review_kind"],
        "project_root": project_root.as_posix(),
        "output_dir": output_dir.as_posix(),
        "summary_path": (project_root / str(scan["summary"])).resolve().as_posix(),
        "total_items": len(index["items"]),
        "next_offset": 0,
        "next_page_number": 1,
        "accepted_batches": [],
        "current": None,
    }
    return _session_page(session_dir, retained_index, state, review_diagnostics)


def review_session_reference(
    internal: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return the canonical or pre-Pass-3 paged-session reference."""

    current = internal.get("review_session")
    legacy = internal.get("deferred_orphan")
    if isinstance(current, Mapping) and isinstance(legacy, Mapping):
        raise ValidationToolError("review packet has conflicting session references")
    if isinstance(current, Mapping):
        return current
    return legacy if isinstance(legacy, Mapping) else None


def _load_review_session(
    decisions: Mapping[str, Any], internal: Mapping[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], Any]:
    session = review_session_reference(internal)
    if not isinstance(session, Mapping):
        raise ValidationToolError("review packet is not a review-session page")
    output_dir = Path(str(session.get("output_dir", ""))).resolve()
    session_dir = _session_path(output_dir, str(session.get("session", "")))
    state = _read_object(
        session_dir / SESSION_STATE_FILENAME,
        "review session state",
    )
    session_identity = session.get("session_identity")
    current = state.get("current")
    if (
        state.get("schema_version") != REVIEW_SESSION_SCHEMA_VERSION
        or state.get("session_identity") != session_identity
        or state.get("session") != session.get("session")
        or not isinstance(current, dict)
        or current.get("continuation") != decisions.get("continuation")
        or current.get("page_number") != session.get("page_number")
        or current.get("offset") != session.get("offset")
    ):
        raise ValidationToolError("review-session page is stale")
    base = _read_object(
        session_dir / SESSION_BASE_FILENAME,
        "review session base",
    )
    index = _read_object(
        session_dir / SESSION_INDEX_FILENAME,
        "review session index",
    )
    if (
        base.get("session_identity") != session_identity
        or index.get("session_identity") != session_identity
    ):
        raise ValidationToolError("review session identity differs")
    return session_dir, state, base, index, session_identity


def _record_session_fragment(
    session_dir: Path,
    state: dict[str, Any],
    decisions: Mapping[str, Any],
    session_identity: Any,
    judgment_identities: Sequence[str] = (),
) -> list[dict[str, Any]]:
    current = state["current"]
    page_number = int(current["page_number"])
    fragment = {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "session_identity": session_identity,
        "page_number": page_number,
        "continuation": decisions["continuation"],
        "batch_identity": current["batch_identity"],
        "decisions": copy.deepcopy(dict(decisions)),
        **(
            {"judgment_identities": list(judgment_identities)}
            if judgment_identities
            else {}
        ),
    }
    fragment_bytes = _json_bytes(fragment)
    fragment_name = f"accepted-{current['batch_identity']}.json"
    fragment_path = session_dir / fragment_name
    if fragment_path.exists():
        if fragment_path.read_bytes() != fragment_bytes:
            raise ValidationToolError(
                "review page conflicts with its accepted fragment"
            )
    else:
        _atomic_write(fragment_path, fragment_bytes)
    fragments = list(state.get("accepted_batches", []))
    expected_fragment = {
        "file": fragment_name,
        "batch_identity": current["batch_identity"],
        "item_count": len(decisions["items"]),
    }
    if expected_fragment not in fragments:
        fragments.append(expected_fragment)
    state["accepted_batches"] = fragments
    state["next_offset"] = int(current["offset"]) + int(current["count"])
    state["next_page_number"] = page_number + 1
    state["current"] = None
    return fragments


def _merged_session_rows(
    session_dir: Path,
    fragments: list[dict[str, Any]],
    session_identity: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = []
    judgment_identities = []
    for retained in fragments:
        payload = _read_object(
            session_dir / str(retained["file"]),
            "accepted review fragment",
        )
        if payload.get("session_identity") != session_identity or payload.get(
            "batch_identity"
        ) != retained.get("batch_identity"):
            raise ValidationToolError("accepted review fragment has another session")
        fragment_rows = payload.get("decisions", {}).get("items", [])
        if not isinstance(fragment_rows, list):
            raise ValidationToolError("accepted review fragment has invalid items")
        rows.extend(copy.deepcopy(fragment_rows))
        raw_identities = payload.get("judgment_identities", [])
        if not (
            isinstance(raw_identities, list)
            and all(isinstance(identity, str) for identity in raw_identities)
        ):
            raise ValidationToolError(
                "accepted review fragment has invalid judgment identities"
            )
        judgment_identities.extend(raw_identities)
    return rows, judgment_identities


def _deferred_orphan_fingerprints(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    fingerprints: dict[str, dict[str, str]] = {}
    for item in items:
        template = item["template"]
        if template.get("kind") != "orphan_candidate":
            continue
        fingerprints.setdefault(str(template["entry"]), {})[
            str(template["identity"])
        ] = str(item["fingerprint"])
    return fingerprints


def _ready_review_session(
    session_dir: Path,
    state: Mapping[str, Any],
    base: Mapping[str, Any],
    index: Mapping[str, Any],
) -> dict[str, Any]:
    fragments = list(state.get("accepted_batches", []))
    rows, judgment_identities = _merged_session_rows(
        session_dir, fragments, state["session_identity"]
    )
    if len(rows) != int(state["total_items"]):
        raise ValidationToolError("review session is missing accepted items")
    return {
        "status": "ready",
        "session_dir": session_dir.as_posix(),
        "scan": base["scan"],
        "adjudication": base["adjudication"],
        "controller": base["controller"],
        "decisions": {
            "schema_version": EXCHANGE_SCHEMA_VERSION,
            "continuation": state["session_identity"],
            "items": rows,
        },
        "orphan_fingerprints": copy.deepcopy(
            index.get("orphan_fingerprints")
            or _deferred_orphan_fingerprints(_session_items(session_dir, index))
        ),
        **({"judgment_identities": judgment_identities} if judgment_identities else {}),
    }


def _validate_session_fragments(session_dir: Path, state: Mapping[str, Any]) -> None:
    accepted = list(state.get("accepted_batches", []))
    expected = {str(item.get("file")) for item in accepted}
    for item in accepted:
        path = session_dir / str(item.get("file"))
        if not path.is_file():
            raise ValidationToolError(
                f"review session state names a missing fragment: {path}"
            )
    current = state.get("current")
    pending = None
    if isinstance(current, Mapping):
        pending = f"accepted-{current.get('batch_identity')}.json"
    present = {path.name for path in session_dir.glob("accepted-*.json")}
    unexpected = present - expected - ({pending} if pending else set())
    if unexpected:
        raise ValidationToolError(
            "review session contains an unowned fragment: "
            + ", ".join(sorted(unexpected))
        )


def resume_review_session(
    output_dir: Path,
    continuation: Mapping[str, Any],
    review_diagnostics: bool = False,
) -> dict[str, Any]:
    """Resume the current review session from its durable record reference."""

    session_dir = _session_path(output_dir, str(continuation.get("session", "")))
    state = _read_object(
        session_dir / SESSION_STATE_FILENAME,
        "review session state",
    )
    if (
        state.get("schema_version") != REVIEW_SESSION_SCHEMA_VERSION
        or state.get("session_identity") != continuation.get("session_identity")
        or state.get("session") != continuation.get("session")
    ):
        raise ValidationToolError("review session state has another owner")
    index = _read_object(
        session_dir / SESSION_INDEX_FILENAME,
        "review session index",
    )
    if index.get("session_identity") != state["session_identity"]:
        raise ValidationToolError("review session identity differs")
    _validate_session_fragments(session_dir, state)
    current = state.get("current")
    if isinstance(current, Mapping):
        fragment_path = session_dir / (f"accepted-{current.get('batch_identity')}.json")
        if fragment_path.is_file():
            fragment = _read_object(fragment_path, "accepted review fragment")
            decisions = fragment.get("decisions")
            if not isinstance(decisions, Mapping):
                raise ValidationToolError(
                    "accepted review fragment has invalid decisions"
                )
            _record_session_fragment(
                session_dir,
                state,
                decisions,
                state["session_identity"],
                fragment.get("judgment_identities", []),
            )
    if int(state["next_offset"]) >= int(state["total_items"]):
        base = _read_object(
            session_dir / SESSION_BASE_FILENAME,
            "review session base",
        )
        if base.get("session_identity") != state["session_identity"]:
            raise ValidationToolError("review session identity differs")
        _atomic_write(session_dir / SESSION_STATE_FILENAME, _json_bytes(state))
        return _ready_review_session(session_dir, state, base, index)
    return _session_page(session_dir, index, state, review_diagnostics)


def review_session_refresh_context(
    output_dir: Path, continuation: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a validated session base for currentness and refresh checks."""

    session_dir = _session_path(output_dir, str(continuation.get("session", "")))
    state = _read_object(
        session_dir / SESSION_STATE_FILENAME,
        "review session state",
    )
    if state.get("session_identity") != continuation.get(
        "session_identity"
    ) or state.get("session") != continuation.get("session"):
        raise ValidationToolError("review session state has another owner")
    base = _read_object(
        session_dir / SESSION_BASE_FILENAME,
        "review session base",
    )
    if base.get("session_identity") != state["session_identity"]:
        raise ValidationToolError("review session identity differs")
    index = _read_object(
        session_dir / SESSION_INDEX_FILENAME,
        "review session index",
    )
    context_levels = {
        context_request_key(template): int(template.get("context_level", 0))
        for item in _session_items(session_dir, index)
        for template in [item.get("template", {})]
        if isinstance(template, Mapping)
    }
    return {
        "session_dir": session_dir.as_posix(),
        "scan": base["scan"],
        "adjudication": base["adjudication"],
        "context_levels": context_levels,
        "context_projection_version": index.get("context_projection_version", 1),
        "accepted_batches": bool(state.get("accepted_batches")),
        "next_offset": int(state.get("next_offset", -1)),
    }


def accept_review_page(
    decisions: Mapping[str, Any],
    internal: Mapping[str, Any],
    publish_batch: Callable[
        [Mapping[str, Any], Mapping[str, Any]], Sequence[str] | None
    ]
    | None = None,
    review_diagnostics: bool = False,
) -> dict[str, Any]:
    """Append one accepted page and return the next page or final state."""

    session_dir, state, base, index, session_identity = _load_review_session(
        decisions, internal
    )
    accepted_page_diagnostics: dict[str, Any] | None = None
    if review_diagnostics:
        current = state.get("current")
        issued_at = (
            current.get("issued_at_epoch_seconds")
            if isinstance(current, Mapping)
            else None
        )
        accepted_page_diagnostics = {
            "page_number": int(current.get("page_number", 0))
            if isinstance(current, Mapping)
            else 0,
            "item_count": len(decisions.get("items", [])),
            "items_by_kind": dict(
                sorted(
                    Counter(
                        str(item.get("kind", "unknown"))
                        for item in decisions.get("items", [])
                        if isinstance(item, Mapping)
                    ).items()
                )
            ),
            **(
                {
                    "review_wait_seconds": round(
                        max(0.0, time.time() - float(issued_at)), 3
                    )
                }
                if isinstance(issued_at, (int, float))
                else {}
            ),
        }
    judgment_identities = (
        publish_batch(decisions, base) if publish_batch is not None else None
    )
    _record_session_fragment(
        session_dir,
        state,
        decisions,
        session_identity,
        judgment_identities or (),
    )
    if int(state["next_offset"]) < int(state["total_items"]):
        result = _session_page(session_dir, index, state, review_diagnostics)
        if accepted_page_diagnostics is not None:
            result["accepted_page_diagnostics"] = accepted_page_diagnostics
        return result
    _atomic_write(session_dir / SESSION_STATE_FILENAME, _json_bytes(state))
    result = _ready_review_session(session_dir, state, base, index)
    if accepted_page_diagnostics is not None:
        result["accepted_page_diagnostics"] = accepted_page_diagnostics
    return result


def finish_review_session(session_dir: Path) -> None:
    """Remove one completed project-local session after canonical publication."""

    resolved = session_dir.resolve()
    if not (
        resolved.parent.name == VALIDATION_WORK_ROOT
        and resolved.parent.parent.name == LOCAL_CACHE_DIRECTORY
        and resolved.parent.parent.parent.name == VALIDATION_DIRECTORY
    ):
        raise ValidationToolError("refusing to remove an invalid review session")
    if (resolved / SESSION_STATE_FILENAME).is_file():
        shutil.rmtree(resolved)


def resume_legacy_ordinary_exchange(
    output_dir: Path,
    summary: str,
    continuation: Mapping[str, Any],
    expected_rules_version: str | None = None,
) -> dict[str, Any]:
    """Return one pre-Pass-3 ordinary packet from its stable continuation."""

    identity = str(continuation.get("identity", ""))
    locator = _session_locator(identity)
    session_dir = _session_path(output_dir, locator)
    internal = _read_object(
        session_dir / INTERNAL_FILENAME,
        "ordinary review continuation",
    )
    if (
        internal.get("continuation") != identity
        or internal.get("ordinary_session", {}).get("session") != locator
        or internal.get("scan", {}).get("summary") != summary
    ):
        raise ValidationToolError("ordinary review session has another owner")
    if (
        expected_rules_version is not None
        and internal.get("scan", {}).get("validation_rules_version")
        != expected_rules_version
    ):
        return {"status": "superseded_rules"}
    packet_path = session_dir / "review-packet.md"
    decision_path = session_dir / "review-decisions.json"
    if not packet_path.is_file() or not decision_path.is_file():
        raise ValidationToolError("ordinary review session is incomplete")
    return {
        "status": "review_required",
        "review_packet": packet_path.as_posix(),
        "decision_file": decision_path.as_posix(),
        "continuation": identity,
        "item_count": continuation["item_count"],
        "byte_count": len(packet_path.read_bytes()),
    }


def finish_legacy_ordinary_session(internal: Mapping[str, Any]) -> None:
    """Remove one accepted pre-Pass-3 ordinary review session."""

    ordinary = internal.get("ordinary_session")
    if not isinstance(ordinary, Mapping):
        return
    output_dir = Path(str(ordinary.get("output_dir", ""))).resolve()
    session_dir = _session_path(output_dir, str(ordinary.get("session", "")))
    if (session_dir / INTERNAL_FILENAME).is_file():
        shutil.rmtree(session_dir)


def create_exchange(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    controller_state: Mapping[str, Any],
    context_levels: Mapping[str, int] | None = None,
    *,
    review_diagnostics: bool = False,
) -> dict[str, Any]:
    """Create one durable review session and return its first bounded page."""

    deferred_index = _deferred_orphan_index(scan, adjudication)
    if deferred_index is not None:
        return _create_review_session(
            scan,
            adjudication,
            controller_state,
            deferred_index,
            review_diagnostics,
        )
    prepared = _prepare_all_template_items(scan, adjudication, context_levels)
    return _create_review_session(
        scan,
        adjudication,
        controller_state,
        _bounded_session_index(
            scan,
            adjudication,
            prepared.items,
            prepared.orphan_fingerprints,
            prepared.contexts,
        ),
        review_diagnostics,
    )


def _read_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationToolError(f"{description} must be a JSON object")
    return value


def _valid_collection_scope(
    row: Mapping[str, Any], decision: Any, internal: Mapping[str, Any]
) -> bool:
    if not (
        row.get("kind") == "collection_scope"
        and isinstance(decision, dict)
        and set(decision) == {"members"}
        and isinstance(decision.get("members"), dict)
    ):
        return False
    members = decision["members"]
    queue_item = next(
        (
            item
            for item in internal.get("adjudication", {}).get("review_queue", [])
            if item.get("kind") == "collection_scope"
            and item.get("entry") == row.get("entry")
            and item.get("identity") == row.get("identity")
        ),
        None,
    )
    expected_collections = (
        set(queue_item.get("collections", []))
        if isinstance(queue_item, dict)
        else next(
            (
                set(value["members"])
                for value in row.get("allowed_decisions", [])
                if isinstance(value, dict)
                and set(value) == {"members"}
                and isinstance(value.get("members"), dict)
            ),
            set(),
        )
    )
    def valid_selection(value: Any) -> bool:
        if isinstance(value, list):
            return bool(value) and all(
                isinstance(member, str) and member for member in value
            )
        if not isinstance(value, Mapping):
            return False
        if set(value) == {"glob"}:
            return isinstance(value.get("glob"), str) and bool(value["glob"])
        return set(value) == DIRECTORY_SELECTOR_KEYS and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in DIRECTORY_SELECTOR_KEYS
        )

    return set(members) == expected_collections and all(
        valid_selection(values) for values in members.values()
    )


def _validate_decision_row(
    row: Any,
    expected_row: Any,
    number: int,
    internal: Mapping[str, Any],
) -> None:
    if not isinstance(row, dict) or not isinstance(expected_row, dict):
        raise ValidationToolError(f"review decision {number} must be an object")
    invariant = {
        key: value for key, value in row.items() if key not in {"decision", "rationale"}
    }
    expected_invariant = {
        key: value
        for key, value in expected_row.items()
        if key not in {"decision", "rationale"}
    }
    if invariant != expected_invariant:
        raise ValidationToolError(f"review decision {number} modified CLI-owned fields")
    decision = row.get("decision")
    if decision not in row.get("allowed_decisions", []) and not _valid_collection_scope(
        row, decision, internal
    ):
        raise ValidationToolError(
            f"review decision {number} is incomplete or not allowed"
        )
    rationale = row.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValidationToolError(
            f"review decision {number} requires a concise rationale"
        )


def load_decisions(decision_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate paired continuation identity and agent-editable fields."""

    decisions = _read_object(decision_path, "review decisions")
    internal = _read_object(decision_path.parent / INTERNAL_FILENAME, "continuation")
    if (
        decisions.get("schema_version") != EXCHANGE_SCHEMA_VERSION
        or internal.get("schema_version") != EXCHANGE_SCHEMA_VERSION
        or decisions.get("continuation") != internal.get("continuation")
    ):
        raise ValidationToolError("review decisions have a stale continuation identity")
    expected = internal.get("template")
    if not isinstance(expected, dict):
        raise ValidationToolError("continuation lacks its decision template")
    rows = decisions.get("items")
    expected_rows = expected.get("items")
    if not isinstance(rows, list) or not isinstance(expected_rows, list):
        raise ValidationToolError("review decisions items must be an array")
    if len(rows) != len(expected_rows):
        raise ValidationToolError("review decisions are incomplete or unexpected")
    for number, (row, expected_row) in enumerate(zip(rows, expected_rows), 1):
        _validate_decision_row(row, expected_row, number, internal)
    return decisions, internal


def _action_match(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "entry": row["entry"],
        "identity": row["identity"],
    }


def _semantic_fallback_action(row: Mapping[str, Any]) -> dict[str, Any]:
    decision = row["decision"]
    match = _action_match(row)
    if str(decision).startswith("fail:"):
        return {
            "match": match,
            "decision": "fail",
            "failure_basis": str(decision).split(":", 1)[1],
            "findings": {"Provenance": row["rationale"]},
        }
    if decision != "pass":
        result: dict[str, Any] = {
            "match": match,
            "decision": "pass",
            "producer": decision,
        }
        dependencies = row.get("producer_dependencies", {}).get(str(decision), [])
        if dependencies:
            result["add_dependencies"] = copy.deepcopy(dependencies)
        return result
    return {"match": match, "decision": "pass"}


def _reproduction_action(row: Mapping[str, Any]) -> dict[str, Any]:
    decision = row["decision"]
    match = _action_match(row)
    if decision == "reproduction-fail":
        return {
            "match": match,
            "decision": "reproduction-fail",
            "findings": {"Reproducibility": row["rationale"]},
        }
    return {"match": match, "decision": decision}


def _ordinary_action(row: Mapping[str, Any]) -> dict[str, Any] | None:
    decision = row["decision"]
    if decision == "needs_context":
        return None
    match = _action_match(row)
    if row["kind"] == "semantic_provenance" and decision == "pass":
        action = {"match": match, "decision": "support", "candidate": 1}
    elif row["kind"] == "semantic_fallback":
        action = _semantic_fallback_action(row)
    elif row["kind"] == "collection_scope" and isinstance(decision, Mapping):
        action = {
            "match": match,
            "decision": "pass",
            "members": copy.deepcopy(decision["members"]),
            **(
                {
                    COLLECTION_DIRECTORY_SELECTIONS_KEY: copy.deepcopy(
                        row[COLLECTION_DIRECTORY_SELECTIONS_KEY]
                    )
                }
                if COLLECTION_DIRECTORY_SELECTIONS_KEY in row
                else {}
            ),
        }
    elif row["kind"] == "reproduction":
        action = _reproduction_action(row)
    elif decision == "keep":
        action = {"match": match, "decision": "keep"}
    elif decision == "fail":
        action = {
            "match": match,
            "decision": "fail",
            "findings": {"Provenance": row["rationale"]},
        }
    else:
        action = {"match": match, "decision": "pass"}
    return action


def _queue_sections(row: Mapping[str, Any], internal: Mapping[str, Any]) -> Any:
    return next(
        (
            item.get("sections", [])
            for item in internal.get("adjudication", {}).get("review_queue", [])
            if item.get("kind") == "semantic_fallback"
            and item.get("entry") == row["entry"]
            and item.get("identity") == row["identity"]
        ),
        [],
    )


def _restore_scoped_producer_dependencies(
    row: Mapping[str, Any],
    action: dict[str, Any] | None,
    internal: Mapping[str, Any],
) -> None:
    if (
        action is None
        or row["kind"] != "semantic_fallback"
        or "producer" not in action
        or "add_dependencies" in action
    ):
        return
    scan = internal.get("scan")
    if not isinstance(scan, dict):
        return
    session = ReviewQuerySession(ReviewContextIndex.build(scan))
    invocation = next(
        (
            candidate
            for candidate in session.eligible_candidate_invocations(
                str(row["entry"]),
                str(row["identity"]),
                _queue_sections(row, internal),
            )
            if candidate.key == action["producer"]
        ),
        None,
    )
    if invocation is None:
        return
    eligibility = session.eligibility_for(invocation, str(row["identity"]))
    if eligibility.kind == "scoped-collection" and eligibility.target_member:
        action["add_dependencies"] = [
            {
                "path": eligibility.coverage_identity,
                "role": "producer",
                "members": [eligibility.target_member],
            }
        ]


def _upstream_action(
    entry: str, identity: str, rows: list[Mapping[str, Any]]
) -> dict[str, Any] | None:
    if any(row["decision"] == "needs_context" for row in rows):
        return None
    match = {"kind": "upstream_producer", "entry": entry, "identity": identity}
    unresolved = [row for row in rows if row["decision"] == "unresolved"]
    if unresolved:
        return {
            "match": match,
            "decision": "fail",
            "findings": {
                "Provenance": "; ".join(str(row["rationale"]) for row in unresolved)
            },
        }
    return {
        "match": match,
        "decision": "bind",
        "producer_bindings": [
            {"material": row["material"], "invocation": row["decision"]} for row in rows
        ],
    }


def _append_upstream_actions(
    actions: list[dict[str, Any]],
    grouped: Mapping[tuple[str, str], list[Mapping[str, Any]]],
) -> None:
    for (entry, identity), rows in grouped.items():
        action = _upstream_action(entry, identity, rows)
        if action is not None:
            actions.append(action)


def _orphan_queue_item(
    adjudication: Mapping[str, Any], entry: str
) -> Mapping[str, Any]:
    item = next(
        (
            value
            for value in adjudication.get("review_queue", [])
            if value.get("kind") == "orphan_candidates" and value.get("entry") == entry
        ),
        None,
    )
    if not isinstance(item, Mapping):
        raise ValidationToolError(f"unknown orphan review scope: {entry}")
    return item


def _orphan_row_effect(
    row: Mapping[str, Any],
    current: Mapping[str, Mapping[str, Any]],
    fingerprints: Mapping[str, Any],
    subtree_candidates: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[str], tuple[str, str, str | None] | None, dict[str, Any] | None]:
    if row["kind"] == "orphan_candidate":
        decision = str(row["decision"])
        if decision == "unresolved":
            decoded: tuple[str, str, str | None] = ("unresolved", "-", None)
        elif decision == "connected":
            decoded = ("connected", "semantic-connection", None)
        else:
            decoded = (
                "retained",
                f"validation-note:{decision.removeprefix('retain:')}",
                None,
            )
        return [str(row["identity"])], decoded, None

    root = str(row["identity"])
    identities = (
        list(subtree_candidates.get(root, ()))
        if subtree_candidates is not None
        else [
            str(candidate["identity"])
            for candidate in candidates_below(list(current.values()), root)
        ]
    )
    if split_choice(row["decision"]):
        return (
            identities,
            None,
            {
                "root": root,
                "material": row["material"],
                "rationale": row["rationale"],
                "candidate_fingerprints": {
                    identity: fingerprints[identity] for identity in identities
                },
            },
        )
    choice = disposition_choice(row["decision"])
    if choice is None:
        raise ValidationToolError("invalid subtree classification")
    return identities, (*choice, root), None


def _orphan_disposition_payload(
    dispositions: Mapping[str, tuple[str, str, str | None]],
    rationales: Mapping[str, str],
    fingerprints: Mapping[str, Any],
    stale_identities: set[str],
) -> dict[str, Any]:
    return {
        "candidate_fingerprints": {
            identity: fingerprints[identity] for identity in sorted(stale_identities)
        },
        "rationales": {identity: rationales[identity] for identity in dispositions},
        "unresolved": [
            identity
            for identity, (decision, _, _) in dispositions.items()
            if decision == "unresolved"
        ],
        "connected": [
            identity
            for identity, (decision, _, _) in dispositions.items()
            if decision == "connected"
        ],
        "retained": [
            {
                "identity": identity,
                "validation_note": basis.removeprefix("validation-note:"),
            }
            for identity, (decision, basis, _) in dispositions.items()
            if decision == "retained"
        ],
        "rule_roots": {
            identity: root
            for identity, (_, _, root) in dispositions.items()
            if root is not None
        },
    }


def _subtree_candidate_index(
    rows: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    roots = {
        str(row["identity"]) for row in rows if row.get("kind") == SUBTREE_REVIEW_KIND
    }
    indexed: dict[str, list[str]] = {root: [] for root in roots}
    for identity in current:
        if identity in indexed:
            indexed[identity].append(identity)
        for parent in PurePosixPath(identity).parents:
            root = parent.as_posix()
            if root in indexed:
                indexed[root].append(identity)
    for identities in indexed.values():
        identities.sort(key=lambda value: (value.casefold(), value))
    return indexed


def _append_orphan_actions(
    actions: list[dict[str, Any]],
    grouped: Mapping[str, list[Mapping[str, Any]]],
    fingerprints: Mapping[str, Any],
    adjudication: Mapping[str, Any],
) -> None:
    for entry, rows in grouped.items():
        selected = [row for row in rows if row["decision"] != "needs_context"]
        if not selected:
            continue
        queue_item = _orphan_queue_item(adjudication, entry)
        current = {
            str(candidate["identity"]): dict(candidate)
            for candidate in queue_item.get("candidates", [])
        }
        subtree_candidates = _subtree_candidate_index(selected, current)
        dispositions: dict[str, tuple[str, str, str | None]] = {}
        rationales: dict[str, str] = {}
        splits = []
        stale_identities: set[str] = set()
        for row in selected:
            identities, decoded, split = _orphan_row_effect(
                row,
                current,
                fingerprints[entry],
                subtree_candidates,
            )
            if split is not None:
                splits.append(split)
                continue
            for identity in identities:
                assert decoded is not None
                if identity in dispositions:
                    raise ValidationToolError(
                        f"overlapping orphan review decisions: {identity}"
                    )
                dispositions[identity] = decoded
                rationales[identity] = str(row["rationale"])
                stale_identities.add(identity)
        action: dict[str, Any] = {
            "match": {"kind": "orphan_candidates", "entry": entry},
            "decision": "orphan-batch" if dispositions else "orphan-refine",
            "subtree_splits": splits,
        }
        if dispositions:
            action.update(
                _orphan_disposition_payload(
                    dispositions,
                    rationales,
                    fingerprints[entry],
                    stale_identities,
                )
            )
        actions.append(action)


def decisions_to_actions(
    decisions: Mapping[str, Any], internal: Mapping[str, Any]
) -> dict[str, Any]:
    """Translate narrow template decisions into the internal action contract."""

    actions: list[dict[str, Any]] = []
    orphan_rows: dict[str, list[Mapping[str, Any]]] = {}
    upstream_rows: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in decisions["items"]:
        if row["kind"] in {"orphan_candidate", SUBTREE_REVIEW_KIND}:
            orphan_rows.setdefault(str(row["entry"]), []).append(row)
        elif row["kind"] == "upstream_producer":
            upstream_rows.setdefault(
                (str(row["entry"]), str(row["identity"])), []
            ).append(row)
        else:
            action = _ordinary_action(row)
            _restore_scoped_producer_dependencies(row, action, internal)
            if action is not None and action not in actions:
                actions.append(action)
    _append_upstream_actions(actions, upstream_rows)
    _append_orphan_actions(
        actions,
        orphan_rows,
        internal.get("orphan_fingerprints", {}),
        internal.get("adjudication", {}),
    )
    return {"schema_version": DECISION_SCHEMA_VERSION, "actions": actions}


def durable_review_judgments(
    decisions: Mapping[str, Any],
    decision_date: str,
    scan: ScanRecord | None = None,
    adjudication: AdjudicationRecord | None = None,
) -> list[dict[str, Any]]:
    """Return compact rationale-owning judgments for accepted template rows."""

    judgments = []
    queue_index = (
        _JudgmentQueueIndex(adjudication)
        if scan is not None and adjudication is not None
        else None
    )
    for row in decisions["items"]:
        if row["decision"] == "needs_context" or split_choice(row["decision"]):
            continue
        inputs: list[dict[str, Any]] = []
        if scan is not None and adjudication is not None:
            queue_item = _judgment_queue_item(adjudication, row, queue_index)
            if queue_item is not None:
                inputs = review_judgment_inputs(
                    scan,
                    adjudication,
                    queue_item,
                    row,
                    row["decision"],
                )
        subject = {
            "kind": row["kind"],
            "entry": row["entry"],
            "identity": row["identity"],
            **({"material": row["material"]} if "material" in row else {}),
        }
        rule_dependencies = (
            SUBTREE_RULE_DEPENDENCIES
            if row["kind"] == SUBTREE_REVIEW_KIND
            else SEMANTIC_REVIEW_RULES
        )
        judgments.append(
            {
                "identity": _fingerprint(
                    {
                        "subject": subject,
                        "decision": row["decision"],
                        "rule_dependencies": rule_dependencies,
                        "input_dependencies": inputs,
                    }
                ),
                "kind": "review-decision",
                "result": (
                    str(row["decision"].get("disposition"))
                    if row["kind"] == SUBTREE_REVIEW_KIND
                    and isinstance(row["decision"], Mapping)
                    else (
                        "bind"
                        if row["kind"] == "upstream_producer"
                        and row["decision"] != "unresolved"
                        else row["decision"]
                    )
                    if isinstance(row["decision"], str)
                    else ("scope" if row["kind"] == "collection_scope" else "bind")
                ),
                "decision": copy.deepcopy(row["decision"]),
                "decision_date": decision_date,
                "subject": subject,
                "rule_dependencies": rule_dependencies,
                "input_dependencies": inputs,
                "rationale": row["rationale"],
                "rationale_provenance": "recorded",
                "provenance": "native-reviewed",
            }
        )
    return judgments


def _judgment_queue_item(
    adjudication: AdjudicationRecord,
    row: Mapping[str, Any],
    queue_index: _JudgmentQueueIndex | None = None,
) -> Mapping[str, Any] | None:
    if queue_index is not None:
        return queue_index.queue_item(row)
    for item in adjudication["review_queue"]:
        if item.get("entry") != row.get("entry"):
            continue
        if item.get("identity") == row.get("identity"):
            return item
        if (
            row.get("kind") == SUBTREE_REVIEW_KIND
            and item.get("kind") == "orphan_candidates"
        ):
            return item
        if row.get("kind") == "orphan_candidate" and any(
            candidate.get("identity") == row.get("identity")
            for candidate in item.get("candidates", [])
            if isinstance(candidate, Mapping)
        ):
            return item
    return None


def _exact_reusable_judgment(
    reusable: Mapping[str, list[dict[str, Any]]], identity: str
) -> dict[str, Any] | None:
    by_decision = {
        _fingerprint(judgment.get("decision")): judgment
        for judgment in reusable.get(identity, [])
    }
    return next(iter(by_decision.values())) if len(by_decision) == 1 else None


def _reuse_templates(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if queue_item["kind"] == "upstream_producer":
        return _upstream_templates(queue_item, {}), {}
    if queue_item["kind"] != "orphan_candidates":
        return [_ordinary_template(queue_item)], {}
    candidates = queue_item.get("candidates", [])
    return _orphan_templates(scan, adjudication, queue_item, max(1, len(candidates)))


def _projected_reuse_rows(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    templates: list[dict[str, Any]],
    reuse_sources: tuple[
        Mapping[str, list[dict[str, Any]]],
        ReviewJudgmentIndex,
        MutableMapping[str, Any] | None,
    ],
) -> list[dict[str, Any]]:
    reusable, judgments_by_subject, diagnostics = reuse_sources
    rows = []
    for template in templates:
        judgment = _exact_reusable_judgment(reusable, template["id"])
        answer: tuple[Any, str] | None
        if judgment is not None:
            if diagnostics is not None:
                diagnostics["questions_considered"] = (
                    int(diagnostics.get("questions_considered", 0)) + 1
                )
                diagnostics["answers_found"] = (
                    int(diagnostics.get("answers_found", 0)) + 1
                )
            answer = (
                copy.deepcopy(judgment["decision"]),
                str(judgment["rationale"]),
            )
        else:
            answer = reusable_review_answer_diagnostics(
                ReuseAnswerRequest(
                    scan,
                    adjudication,
                    queue_item,
                    template,
                    judgments_by_subject,
                ),
                diagnostics,
            )
        if answer is None:
            continue
        decision, rationale = answer
        rows.append(
            {
                **template,
                "decision": copy.deepcopy(decision),
                "rationale": rationale,
            }
        )
    return rows


def _candidate_reuse_template(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    candidate: Mapping[str, Any],
    fingerprint: str | None = None,
) -> dict[str, Any]:
    identity = str(candidate["identity"])
    notes = [
        str(note["sha256"])
        for note in queue_item.get("validation_notes", [])
        if isinstance(note.get("sha256"), str)
    ]
    return {
        "id": _fingerprint(
            {
                "candidate": identity,
                "fingerprint": fingerprint
                or orphan_candidate_fingerprint(
                    scan,
                    adjudication.get("schema_version"),
                    str(queue_item["entry"]),
                    candidate,
                    DECISION_SCHEMA_VERSION,
                ),
            }
        ),
        "kind": "orphan_candidate",
        "entry": queue_item["entry"],
        "identity": identity,
        "allowed_decisions": [
            "unresolved",
            "connected",
            *[f"retain:{note}" for note in notes],
        ],
    }


def _subtree_reuse_template(
    queue_item: Mapping[str, Any], material: str, root: str
) -> dict[str, Any]:
    return {
        "id": _fingerprint(subtree_subject(str(queue_item["entry"]), material, root)),
        **subtree_subject(str(queue_item["entry"]), material, root),
        "allowed_decisions": subtree_allowed_decisions(
            queue_item.get("validation_notes", [])
        ),
    }


def _reuse_answer(
    request: ReuseAnswerRequest,
    diagnostics: MutableMapping[str, Any] | None,
) -> tuple[Any, str] | None:
    """Use the ordinary compatibility path unless diagnostics are enabled."""

    if diagnostics is None:
        return reusable_review_answer(*request)
    return reusable_review_answer_diagnostics(request, diagnostics)


def _orphan_reuse_action(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    queue_item: Mapping[str, Any],
    judgments_by_subject: ReviewJudgmentIndex,
    diagnostics: MutableMapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected: dict[str, tuple[str, str, str | None, str]] = {}
    fingerprints: dict[str, str] = {}
    fingerprint_context = orphan_fingerprint_context(
        scan,
        adjudication.get("schema_version"),
        str(queue_item["entry"]),
        DECISION_SCHEMA_VERSION,
    )
    for candidate in ordered_orphan_candidates(queue_item):
        identity = str(candidate["identity"])
        fingerprints[identity] = fingerprint_context.fingerprint(candidate)
        template = _candidate_reuse_template(
            scan,
            adjudication,
            queue_item,
            candidate,
            fingerprints[identity],
        )
        request = ReuseAnswerRequest(
            scan,
            adjudication,
            queue_item,
            template,
            judgments_by_subject,
        )
        candidate_diagnostics: dict[str, Any] | None = (
            {} if diagnostics is not None else None
        )
        answer = _reuse_answer(request, candidate_diagnostics)
        rule_root: str | None = None
        if answer is None:
            for material, root in reversed(ancestor_roots(identity)):
                template = _subtree_reuse_template(queue_item, material, root)
                request = ReuseAnswerRequest(
                    scan,
                    adjudication,
                    queue_item,
                    template,
                    judgments_by_subject,
                )
                answer = _reuse_answer(request, candidate_diagnostics)
                if answer is not None:
                    rule_root = root
                    break
        if answer is None:
            _merge_reuse_candidate_diagnostics(
                diagnostics, candidate_diagnostics, reused=False
            )
            continue
        _merge_reuse_candidate_diagnostics(
            diagnostics, candidate_diagnostics, reused=True
        )
        decision, rationale = answer
        if isinstance(decision, str):
            decoded = (
                ("unresolved", "-")
                if decision == "unresolved"
                else (
                    ("connected", "semantic-connection")
                    if decision == "connected"
                    else (
                        "retained",
                        f"validation-note:{decision.removeprefix('retain:')}",
                    )
                )
            )
        else:
            choice = disposition_choice(decision)
            if choice is None:
                continue
            decoded = choice
        selected[identity] = (*decoded, rule_root, rationale)
    if not selected:
        return None
    return {
        "match": {
            "kind": "orphan_candidates",
            "entry": queue_item["entry"],
        },
        "decision": "orphan-batch",
        "candidate_fingerprints": {
            identity: fingerprints[identity] for identity in selected
        },
        "rationales": {
            identity: rationale for identity, (_, _, _, rationale) in selected.items()
        },
        "unresolved": [
            identity
            for identity, (decision, _, _, _) in selected.items()
            if decision == "unresolved"
        ],
        "connected": [
            identity
            for identity, (decision, _, _, _) in selected.items()
            if decision == "connected"
        ],
        "retained": [
            {
                "identity": identity,
                "validation_note": basis.removeprefix("validation-note:"),
            }
            for identity, (decision, basis, _, _) in selected.items()
            if decision == "retained"
        ],
        "rule_roots": {
            identity: root
            for identity, (_, _, root, _) in selected.items()
            if root is not None
        },
        "subtree_splits": [],
    }


def _merge_reuse_candidate_diagnostics(
    diagnostics: MutableMapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    *,
    reused: bool,
) -> None:
    """Collapse exact and ancestor lookups into one orphan question result."""

    if diagnostics is None or candidate is None:
        return
    diagnostics["questions_considered"] = (
        int(diagnostics.get("questions_considered", 0)) + 1
    )
    if reused:
        diagnostics["answers_found"] = int(diagnostics.get("answers_found", 0)) + 1
        return
    raw_misses = candidate.get("misses_by_reason", {})
    misses = diagnostics.setdefault("misses_by_reason", {})
    if not isinstance(raw_misses, Mapping) or not isinstance(misses, MutableMapping):
        return
    for reason in (
        "candidate_or_allowed_answer_changed",
        "relevant_input_content_changed",
        "source_locator_changed",
        "incomplete_legacy_input_dependencies",
        "rule_dependency_changed",
        "subject_not_found",
    ):
        if int(raw_misses.get(reason, 0)):
            misses[reason] = int(misses.get(reason, 0)) + 1
            return


def reusable_review_actions(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    judgments: list[dict[str, Any]],
    diagnostics: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return actions for exact current questions with durable native answers."""

    reusable: dict[str, list[dict[str, Any]]] = {}
    judgments_by_subject = index_review_judgments(judgments)
    for judgment in judgments:
        if (
            judgment.get("kind") == "review-decision"
            and "decision" in judgment
            and judgment.get("rule_dependencies") == SEMANTIC_REVIEW_RULES
        ):
            reusable.setdefault(str(judgment.get("identity", "")), []).append(judgment)

    rows: list[dict[str, Any]] = []
    direct_actions: list[dict[str, Any]] = []
    orphan_fingerprints: dict[str, dict[str, str]] = {}
    for queue_item in adjudication["review_queue"]:
        if queue_item["kind"] == "orphan_candidates":
            action = _orphan_reuse_action(
                scan,
                adjudication,
                queue_item,
                judgments_by_subject,
                diagnostics,
            )
            if action is not None:
                direct_actions.append(action)
            continue
        templates, fingerprints = _reuse_templates(scan, adjudication, queue_item)
        if fingerprints:
            orphan_fingerprints[str(queue_item["entry"])] = fingerprints
        projected_rows = _projected_reuse_rows(
            scan,
            adjudication,
            queue_item,
            templates,
            (reusable, judgments_by_subject, diagnostics),
        )
        if queue_item["kind"] != "upstream_producer" or len(projected_rows) == len(
            templates
        ):
            rows.extend(projected_rows)
    result = decisions_to_actions(
        {"schema_version": EXCHANGE_SCHEMA_VERSION, "items": rows},
        {"adjudication": adjudication, "orphan_fingerprints": orphan_fingerprints},
    )
    result["actions"] = [*direct_actions, *result["actions"]]
    return result


def reusable_review_subjects(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> list[dict[str, Any]]:
    """Return exact durable subjects that can answer the current queue."""

    subjects: dict[str, dict[str, Any]] = {}
    for queue_item in adjudication["review_queue"]:
        if queue_item["kind"] == "orphan_candidates":
            for candidate in ordered_orphan_candidates(queue_item):
                identity_value = str(candidate["identity"])
                exact_subject = {
                    "kind": "orphan_candidate",
                    "entry": queue_item["entry"],
                    "identity": identity_value,
                }
                candidates = [
                    exact_subject,
                ]
                candidates.extend(
                    subtree_subject(str(queue_item["entry"]), material, root)
                    for material, root in ancestor_roots(identity_value)
                )
                for subject in candidates:
                    identity = json.dumps(
                        subject, sort_keys=True, separators=(",", ":")
                    )
                    subjects[identity] = subject
            continue
        templates, _ = _reuse_templates(scan, adjudication, queue_item)
        for template in templates:
            review_subject = {
                key: copy.deepcopy(template[key])
                for key in ("kind", "entry", "identity", "material")
                if key in template
            }
            candidates = [review_subject]
            for subject in candidates:
                identity = json.dumps(subject, sort_keys=True, separators=(",", ":"))
                subjects[identity] = subject
    return list(subjects.values())
