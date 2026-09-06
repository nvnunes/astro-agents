"""Exact deterministic projection contracts for reproduction planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

PLAN_SCHEMA = "research-log-reproduction-plan/1"
SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/1"
MAX_PLAN_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ReproductionPlan:
    """One complete write-free reproduction plan projection."""

    summary: str
    target: Mapping[str, object]
    include_slow: bool
    validation_snapshot: Mapping[str, object]
    source_snapshot: Mapping[str, object]
    cases: tuple[Mapping[str, object], ...]
    executions: tuple[Mapping[str, object], ...]
    boundaries: tuple[Mapping[str, object], ...]
    failures: tuple[Mapping[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        """Return the exact public v1 field set."""

        return {
            "boundaries": [dict(value) for value in self.boundaries],
            "cases": [dict(value) for value in self.cases],
            "executions": [dict(value) for value in self.executions],
            "failures": [dict(value) for value in self.failures],
            "include_slow": self.include_slow,
            "schema": PLAN_SCHEMA,
            "source_snapshot": dict(self.source_snapshot),
            "summary": self.summary,
            "target": dict(self.target),
            "validation_snapshot": dict(self.validation_snapshot),
        }

    def serialized(self) -> str:
        """Serialize canonically and enforce the fixed dry-run byte bound."""

        text = json.dumps(
            self.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("reproduction dry-run plan crossed its byte bound")
        return text


def source_snapshot(
    *,
    authority_files: Sequence[Mapping[str, object]],
    executions: Sequence[Mapping[str, object]],
    materials: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build the exact top-level source-snapshot field set."""

    return {
        "authority_files": authority_files,
        "executions": executions,
        "materials": materials,
        "schema": SOURCE_SNAPSHOT_SCHEMA,
    }


def canonical_record_digest(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of one canonical JSON object."""

    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
