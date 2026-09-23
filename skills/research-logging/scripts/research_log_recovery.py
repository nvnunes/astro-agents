"""Bounded generated candidate state for agent-confirmed ordinary completion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from research_log_reservations import MAX_CANDIDATE_BYTES, recovery_candidate_path
from validation.file_publication import atomic_create_text
from validation.filesystem import BoundedFileReadError, bounded_file_bytes
from validation.json_codec import V2JsonError, decode_json

SCHEMA = "research-log-pyrun-completion-candidate/1"


class RecoveryCandidateError(ValueError):
    """One unavailable or malformed exact ordinary-completion candidate."""


def publish_candidate(root: Path, identity: str, value: Mapping[str, object]) -> Path:
    """Create one bounded generated candidate without replacing an earlier one."""

    path = recovery_candidate_path(root, identity)
    candidate = {**value, "schema": SCHEMA}
    payload = json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise RecoveryCandidateError("completion candidate exceeds its byte limit")
    try:
        atomic_create_text(path, payload)
    except OSError as error:
        raise RecoveryCandidateError(
            f"could not retain completion candidate {path}: {error}"
        ) from error
    return path


def load_candidate(root: Path, identity: str) -> dict[str, Any]:
    """Read one exact candidate; never infer one from retained output bytes."""

    path = recovery_candidate_path(root, identity)
    try:
        payload = bounded_file_bytes(path, maximum_bytes=MAX_CANDIDATE_BYTES)
        value = decode_json(
            payload.decode("utf-8"),
            maximum_bytes=MAX_CANDIDATE_BYTES,
            subject=str(path),
        )
    except (BoundedFileReadError, OSError, UnicodeError, V2JsonError) as error:
        raise RecoveryCandidateError(
            f"completion candidate is unavailable or invalid: {path}: {error}"
        ) from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise RecoveryCandidateError(f"completion candidate has invalid schema: {path}")
    return value
