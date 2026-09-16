"""Accepted physical invocation and shared runtime/source-identity contracts.

Work selection, problems, and saved outcomes are owned by the reproduction
domain. This owner contains only the existing recipe execution grammar and
its deterministic identity/runtime primitives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research_log_data import DataFile
from validation.pyrun_state import PyrunExecution

from .reproduction_domain import ExecutionRef

DEFAULT_EXECUTION_TIMEOUT_SECONDS = 5 * 60
MAX_EXECUTION_TIMEOUT_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ReproductionRuntime:
    """Immutable concurrency and per-command runtime controls for one run."""

    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class AcceptedInvocation:
    """Execution authority only: identity, roots, recipe and declarations.

    Selection and diagnoses belong to command work, not this physical invocation.
    """

    identity: ExecutionRef
    entry_root: Path
    execution: "PyrunExecution"
    data: DataFile | None


def canonical_record_digest(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of one canonical JSON object."""

    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_execution_source_digest(value: Mapping[str, Any]) -> str:
    """Hash execution source without its mutable reproduction requirement."""

    selected = dict(value)
    if set(selected) <= {"requires_reproduction"}:
        raise ValueError("execution source record is incomplete")
    selected.pop("requires_reproduction", None)
    return canonical_record_digest(selected)
