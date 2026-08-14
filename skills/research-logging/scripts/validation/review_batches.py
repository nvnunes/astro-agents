"""Deterministic orphan-review batching and stale-packet identities."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import ValidationToolError

DEFAULT_ORPHAN_BATCH_SIZE = 200


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


def orphan_queue_fingerprint(
    scan: Mapping[str, Any],
    adjudication: Mapping[str, Any],
    item: Mapping[str, Any],
    decision_schema_version: int,
) -> str:
    """Return the complete stale-protection identity for one orphan queue item."""

    notes = sorted(
        str(note.get("sha256"))
        for note in item.get("validation_notes", [])
        if isinstance(note.get("sha256"), str)
    )
    payload = {
        "scan_input_fingerprint": scan.get("input_fingerprint", ""),
        "validation_rules_version": scan.get("validation_rules_version", ""),
        "scan_schema_version": scan.get("schema_version"),
        "adjudication_schema_version": adjudication.get("schema_version"),
        "decision_schema_version": decision_schema_version,
        "entry": item.get("entry"),
        "queue_kind": item.get("kind"),
        "queue_identity": item.get("identity"),
        "candidates": ordered_orphan_candidates(item),
        "validation_notes": notes,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    return OrphanBatch(
        item,
        selected,
        request.number,
        total,
        request.size,
        len(candidates),
        orphan_queue_fingerprint(
            scan, adjudication, item, request.decision_schema_version
        ),
    )
