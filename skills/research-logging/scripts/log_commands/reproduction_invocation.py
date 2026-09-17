"""Accepted physical invocation and shared runtime/source-identity contracts.

Work selection, problems, and saved outcomes are owned by the reproduction
domain. This owner contains only the existing recipe execution grammar and
its deterministic identity/runtime primitives.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from effective_code import FINGERPRINT_ALGORITHM
from research_log_data import DataFile, Fingerprint, parse_fingerprint
from validation.pyrun_state import PyrunExecution

from .reproduction_domain import ExecutionRef, ReproductionDomainError

DEFAULT_EXECUTION_TIMEOUT_SECONDS = 5 * 60
MAX_EXECUTION_TIMEOUT_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ReproductionRuntime:
    """Immutable concurrency and per-command runtime controls for one run."""

    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class AcceptedSource:
    """Accepted raw script and nullable effective-code observation."""

    script: Fingerprint
    effective_code: Fingerprint | None

    def __post_init__(self) -> None:
        parse_fingerprint(self.script.as_dict(), "accepted script fingerprint")
        if self.effective_code is not None and (
            self.effective_code.algorithm != FINGERPRINT_ALGORITHM
            or self.effective_code.digest is None
            or re.fullmatch(r"[0-9a-f]{64}", self.effective_code.digest) is None
            or set(self.effective_code.as_dict()) != {"algorithm", "digest"}
        ):
            raise ReproductionDomainError(
                "accepted effective-code fingerprint is invalid"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "script": self.script.as_dict(),
            "effective_code": (
                self.effective_code.as_dict()
                if self.effective_code is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> AcceptedSource:
        if not isinstance(value, Mapping) or set(value) != {
            "script",
            "effective_code",
        }:
            raise ReproductionDomainError("accepted source has invalid fields")
        return cls(
            parse_fingerprint(value["script"], "accepted script fingerprint"),
            (
                _effective_code_fingerprint(value["effective_code"])
                if value["effective_code"] is not None
                else None
            ),
        )


def _effective_code_fingerprint(value: object) -> Fingerprint:
    if not isinstance(value, Mapping) or set(value) != {"algorithm", "digest"}:
        raise ReproductionDomainError("accepted effective-code fingerprint is invalid")
    algorithm, digest = value.get("algorithm"), value.get("digest")
    if (
        algorithm != FINGERPRINT_ALGORITHM
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise ReproductionDomainError("accepted effective-code fingerprint is invalid")
    return Fingerprint(algorithm, digest=digest)


def observe_script_source(path: Path) -> Fingerprint:
    """Fingerprint one regular top-level script without following a symlink."""

    if path.is_symlink() or not path.is_file():
        raise OSError(f"script is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return Fingerprint("sha256", digest=digest.hexdigest())


@dataclass(frozen=True)
class AcceptedInvocation:
    """Execution authority only: identity, roots, recipe and declarations.

    Selection and diagnoses belong to command work, not this physical invocation.
    """

    identity: ExecutionRef
    entry_root: Path
    execution: "PyrunExecution"
    data: DataFile | None
    accepted_source: AcceptedSource | None = None


def canonical_record_digest(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of one canonical JSON object."""

    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_execution_source_digest(value: Mapping[str, Any]) -> str:
    """Hash retained execution material without mutable source-currentness facts."""

    selected = dict(value)
    if set(selected) <= {"requires_reproduction"}:
        raise ValueError("execution source record is incomplete")
    selected.pop("requires_reproduction", None)
    observed_value = selected.get("observed")
    if not isinstance(observed_value, Mapping):
        raise ValueError("execution observations are incomplete")
    observed = dict(observed_value)
    observed.pop("script", None)
    observed.pop("effective_code", None)
    selected["observed"] = observed
    return canonical_record_digest(selected)
