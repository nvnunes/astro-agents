"""Validation-owned current research-source projection for reproduction."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .json_codec import canonical_json
from .operation_state import research_snapshot


def research_source_projection(
    summary: Path,
) -> tuple[str, tuple[tuple[str, tuple[int, ...]], ...]]:
    """Return an opaque digest and the exact state used to derive it.

    Reproduction deliberately does not interpret this projection.  Validation
    owns which maintained files participate and the stable serialization used
    for the currentness token.
    """

    snapshot = research_snapshot(summary)
    digest = hashlib.sha256(
        canonical_json(
            {
                "files": [
                    {"path": path, "stat": list(identity)}
                    for path, identity in snapshot
                ],
                "schema": "research-log-source-projection/1",
            }
        ).encode("utf-8")
    ).hexdigest()
    return digest, snapshot
