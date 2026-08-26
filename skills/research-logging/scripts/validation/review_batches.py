"""Deterministic orphan-review batching and stale-packet identities."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import ValidationToolError

DEFAULT_ORPHAN_BATCH_SIZE = 200


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class OrphanFingerprintContext:
    """Canonical entry-scoped state shared by orphan candidate fingerprints.

    The encoded prefix and suffix preserve the historical canonical JSON bytes;
    only the candidate-local object is serialized for each fingerprint.
    """

    prefix: bytes
    suffix: bytes

    def fingerprint(self, candidate: Mapping[str, Any]) -> str:
        """Return the compatible digest for one candidate-local payload."""

        digest = hashlib.sha256()
        digest.update(self.prefix)
        digest.update(_canonical_json(dict(candidate)))
        digest.update(self.suffix)
        return digest.hexdigest()


def orphan_fingerprint_context(
    scan: Mapping[str, Any],
    adjudication_schema_version: Any,
    entry_id: str,
    decision_schema_version: int,
) -> OrphanFingerprintContext:
    """Prepare invariant canonical bytes for one entry's orphan candidates."""

    entry: Mapping[str, Any] = next(
        (
            value
            for value in scan.get("entries", [])
            if value.get("id") == entry_id and "error" not in value
        ),
        {},
    )
    notes = sorted(
        str(note.get("sha256"))
        for note in entry.get("validation_notes", [])
        if isinstance(note.get("sha256"), str)
    )
    prefix = (
        b'{"adjudication_schema_version":'
        + _canonical_json(adjudication_schema_version)
        + b',"candidate":'
    )
    suffix_values = (
        ("commands", entry.get("commands", [])),
        ("data_index", entry.get("data_index", {})),
        ("decision_schema_version", decision_schema_version),
        ("entry", entry_id),
        ("scan_schema_version", scan.get("schema_version")),
        ("validation_notes", notes),
        ("validation_rules_version", scan.get("validation_rules_version", "")),
    )
    suffix = b"".join(
        b',"' + key.encode("utf-8") + b'":' + _canonical_json(value)
        for key, value in suffix_values
    ) + b"}"
    return OrphanFingerprintContext(prefix, suffix)


def ordered_orphan_candidates(
    item: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return one queue item's candidates in stable normalized identity order."""

    return sorted(
        (dict(candidate) for candidate in item.get("candidates", [])),
        key=lambda candidate: (
            str(candidate.get("identity", "")).casefold(),
            str(candidate.get("identity", "")),
        ),
    )


def orphan_candidate_fingerprint(
    scan: Mapping[str, Any],
    adjudication_schema_version: Any,
    entry_id: str,
    candidate: Mapping[str, Any],
    decision_schema_version: int,
) -> str:
    """Return conservative stale protection for one orphan candidate."""

    return orphan_fingerprint_context(
        scan,
        adjudication_schema_version,
        entry_id,
        decision_schema_version,
    ).fingerprint(candidate)


@dataclass(frozen=True)
class OrphanBatch:
    """One deterministic batch selected from a complete orphan queue item."""

    item: Mapping[str, Any]
    candidates: Sequence[dict[str, Any]]
    number: int
    total: int
    size: int
    complete_count: int
    fingerprint: str
    candidate_fingerprints: Mapping[str, str]

    @property
    def remaining(self) -> int:
        """Return candidates outside this batch in the current queue snapshot."""

        return self.complete_count - len(self.candidates)

    @property
    def partial(self) -> bool:
        """Return whether the selected packet covers only part of the queue item."""

        return self.complete_count > len(self.candidates)


@dataclass(frozen=True)
class OrphanBatchRequest:
    """Selection and schema inputs needed to identify one orphan batch."""

    size: int
    number: int
    decision_schema_version: int


def select_orphan_batch(
    scan: Mapping[str, Any],
    adjudication: Mapping[str, Any],
    item: Mapping[str, Any],
    request: OrphanBatchRequest,
) -> OrphanBatch:
    """Select and identify one nonempty deterministic orphan batch."""

    if request.size < 1:
        raise ValidationToolError("orphan review batch size must be positive")
    if request.number < 1:
        raise ValidationToolError("orphan review batch number must be positive")
    candidates = ordered_orphan_candidates(item)
    if not candidates:
        raise ValidationToolError("orphan review batch cannot select an empty queue")
    total = math.ceil(len(candidates) / request.size)
    if request.number > total:
        raise ValidationToolError(
            f"orphan review batch {request.number} is out of range; expected 1-{total}"
        )
    start = (request.number - 1) * request.size
    selected = candidates[start : start + request.size]
    entry_id = item.get("entry")
    if not isinstance(entry_id, str):
        raise ValidationToolError("orphan review item lacks an entry identity")
    fingerprint_context = orphan_fingerprint_context(
        scan,
        adjudication.get("schema_version"),
        entry_id,
        request.decision_schema_version,
    )
    candidate_fingerprints = {
        candidate["identity"]: fingerprint_context.fingerprint(candidate)
        for candidate in selected
    }
    return OrphanBatch(
        item,
        selected,
        request.number,
        total,
        request.size,
        len(candidates),
        hashlib.sha256(
            json.dumps(
                candidate_fingerprints,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        candidate_fingerprints,
    )
