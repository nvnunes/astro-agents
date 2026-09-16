"""Immutable saved reproduction facts; classifications are derived, not stored."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from research_log_data import Fingerprint, parse_fingerprint
from validation.domain import plain_json
from validation.evidence_comparison import valid_evidence_comparison_record

from .reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    NotComparedReason,
    ReproductionDomainError,
    _canonical_path,
    _fields,
    _frozen_object,
    _matches,
    _text,
    classify_artifact,
)
from .reproduction_work import _list, problem_references

RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
PROFILES = frozenset(
    {
        "directory",
        "evidence",
        "image",
        "json",
        "kind",
        "named_array",
        "opaque_file",
        "table",
        "text",
    }
)


@dataclass(frozen=True)
class CommandResult:
    """Terminal attempt facts, or prerequisite links explaining no attempt.

    Error stage/message/type belong to referenced problems, not copied result
    diagnoses. Invocation and stream paths are what actually ran, not a recipe
    reconstructed from today's source. Output observations retain partial work.
    """

    identity: ExecutionRef
    outcome: CommandOutcome
    started_at: str | None
    finished_at: str | None
    argv: tuple[str, ...]
    cwd: str | None
    stdout_path: str | None
    stderr_path: str | None
    outputs: Mapping[str, object]
    problem_ids: tuple[str, ...] = ()
    blocked_by: tuple[ExecutionRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionRef) or not isinstance(
            self.outcome, CommandOutcome
        ):
            raise ReproductionDomainError("command result has invalid identity/outcome")
        self._validate_invocation()
        self._validate_relations()
        if self.outcome is CommandOutcome.BLOCKED:
            self._validate_blocked()
        else:
            self._validate_attempted()

    def _validate_invocation(self) -> None:
        for value in (self.started_at, self.finished_at):
            if value is not None:
                timestamp(value)
        if (self.started_at is None) != (self.finished_at is None):
            raise ReproductionDomainError("command result has incomplete timing")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.started_at > self.finished_at
        ):
            raise ReproductionDomainError("command result finishes before it starts")
        if not isinstance(self.argv, tuple) or any(
            not isinstance(value, str) or "\0" in value for value in self.argv
        ):
            raise ReproductionDomainError("command invocation has invalid arguments")
        for path in (self.cwd, self.stdout_path, self.stderr_path):
            if path is not None:
                _canonical_path(path)
        object.__setattr__(self, "outputs", _frozen_object(self.outputs))

    def _validate_relations(self) -> None:
        object.__setattr__(self, "problem_ids", problem_references(self.problem_ids))
        if (
            any(not isinstance(item, ExecutionRef) for item in self.blocked_by)
            or self.identity in self.blocked_by
        ):
            raise ReproductionDomainError("invalid blocked prerequisite reference")
        object.__setattr__(self, "blocked_by", tuple(sorted(set(self.blocked_by))))

    def _validate_blocked(self) -> None:
        if self.started_at is not None or self.argv or self.cwd is not None:
            raise ReproductionDomainError("blocked command cannot have attempt facts")
        if self.stdout_path is not None or self.stderr_path is not None or self.outputs:
            raise ReproductionDomainError(
                "blocked command cannot have captured outputs"
            )
        if not self.problem_ids and not self.blocked_by:
            raise ReproductionDomainError(
                "blocked command has no cause or prerequisite"
            )

    def _validate_attempted(self) -> None:
        if self.started_at is None or not self.argv or self.cwd is None:
            raise ReproductionDomainError("attempted command lacks invocation/timing")
        if self.blocked_by:
            raise ReproductionDomainError(
                "attempted command cannot have blocking prerequisites"
            )
        if self.outcome is CommandOutcome.FAILED and not self.problem_ids:
            raise ReproductionDomainError("failed attempt has no diagnosis")
        if self.outcome is CommandOutcome.SUCCEEDED and self.problem_ids:
            raise ReproductionDomainError(
                "successful attempt cannot have failure diagnoses"
            )

    def as_dict(self) -> dict[str, object]:
        """Serialize only terminal facts and relationships, never derived buckets."""

        return {
            "identity": self.identity.as_dict(),
            "outcome": self.outcome.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "outputs": plain_json(self.outputs),
            "problem_ids": list(self.problem_ids),
            "blocked_by": [item.as_dict() for item in self.blocked_by],
        }

    @classmethod
    def from_dict(cls, value: object) -> CommandResult:
        """Reject partial or unknown result fields without repairing saved records."""

        item = _fields(
            value,
            {
                "identity",
                "outcome",
                "started_at",
                "finished_at",
                "argv",
                "cwd",
                "stdout_path",
                "stderr_path",
                "outputs",
                "problem_ids",
                "blocked_by",
            },
            "command result",
        )
        return cls(
            ExecutionRef.from_dict(item["identity"]),
            CommandOutcome(item["outcome"]),
            optional_text(item["started_at"]),
            optional_text(item["finished_at"]),
            tuple(_argument(raw) for raw in _list(item["argv"])),
            optional_text(item["cwd"]),
            optional_text(item["stdout_path"]),
            optional_text(item["stderr_path"]),
            _fields(item["outputs"], None, "output observations"),
            tuple(_text(raw) for raw in _list(item["problem_ids"])),
            tuple(ExecutionRef.from_dict(raw) for raw in _list(item["blocked_by"])),
        )


@dataclass(frozen=True)
class ArtifactResult:
    """A recorded comparison or precise non-comparison, including reuse origin."""

    identity: ArtifactRef
    outcome: ArtifactOutcome
    recorded_at: str
    origin_run_id: str
    regenerated_path: str | None
    profile: str | None
    expected: Fingerprint | None
    regenerated: Fingerprint | None
    not_compared_reason: NotComparedReason | None = None
    evidence: tuple[Mapping[str, object], ...] = ()
    problem_ids: tuple[str, ...] = ()
    definition_identity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ArtifactRef):
            raise ReproductionDomainError("artifact result has invalid identity")
        classify_artifact(self.outcome, self.not_compared_reason)
        timestamp(self.recorded_at)
        run_identity(self.origin_run_id)
        if self.regenerated_path is not None:
            _canonical_path(self.regenerated_path)
        if self.profile is not None and self.profile not in PROFILES:
            raise ReproductionDomainError(
                "artifact result has invalid comparison profile"
            )
        for fingerprint in (self.expected, self.regenerated):
            if fingerprint is not None:
                if not isinstance(fingerprint, Fingerprint):
                    raise ReproductionDomainError(
                        "comparison observation is not a fingerprint"
                    )
                parse_fingerprint(fingerprint.as_dict(), "comparison observation")
        object.__setattr__(
            self, "evidence", tuple(_frozen_object(item) for item in self.evidence)
        )
        for record in self.evidence:
            if not valid_evidence_comparison_record(
                _fields(plain_json(record), None, "comparison record")
            ):
                raise ReproductionDomainError(
                    "invalid retained evidence comparison record"
                )
        object.__setattr__(self, "problem_ids", problem_references(self.problem_ids))
        self._validate_comparison()

    def _validate_comparison(self) -> None:
        if self.definition_identity is not None:
            _matches(
                self.definition_identity,
                re.compile(r"[0-9a-f]{64}\Z"),
                "observed comparison definition",
            )
        if self.profile == "evidence" and self.definition_identity is None:
            raise ReproductionDomainError(
                "evidence comparison lacks its exact definition"
            )
        if self.outcome is not ArtifactOutcome.NOT_COMPARED:
            if (
                self.profile is None
                or self.regenerated_path is None
                or self.expected is None
                or self.regenerated is None
            ):
                raise ReproductionDomainError(
                    "completed comparison lacks recorded observations"
                )
        if self.outcome is ArtifactOutcome.NOT_MATCHED and not self.problem_ids:
            raise ReproductionDomainError(
                "unequal artifact lacks its retained difference diagnosis"
            )
        if self.outcome is ArtifactOutcome.MATCHED and self.problem_ids:
            raise ReproductionDomainError(
                "matched artifact cannot have problem diagnoses"
            )
        if (
            self.not_compared_reason is NotComparedReason.COMPARISON_FAILED
            and not self.problem_ids
        ):
            raise ReproductionDomainError(
                "failed comparison lacks its retained error diagnosis"
            )

    def as_dict(self) -> dict[str, object]:
        """Retain comparisons and origin, without an independent command status."""

        return {
            "identity": self.identity.as_dict(),
            "outcome": self.outcome.value,
            "recorded_at": self.recorded_at,
            "origin_run_id": self.origin_run_id,
            "regenerated_path": self.regenerated_path,
            "profile": self.profile,
            "expected": self.expected.as_dict() if self.expected is not None else None,
            "regenerated": self.regenerated.as_dict()
            if self.regenerated is not None
            else None,
            "not_compared_reason": self.not_compared_reason.value
            if self.not_compared_reason is not None
            else None,
            "evidence": [plain_json(item) for item in self.evidence],
            "problem_ids": list(self.problem_ids),
            "definition_identity": self.definition_identity,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArtifactResult:
        """Decode complete immutable observations; never re-observe current files."""

        item = _fields(
            value,
            {
                "identity",
                "outcome",
                "recorded_at",
                "origin_run_id",
                "regenerated_path",
                "profile",
                "expected",
                "regenerated",
                "not_compared_reason",
                "evidence",
                "problem_ids",
                "definition_identity",
            },
            "artifact result",
        )
        return cls(
            ArtifactRef.from_dict(item["identity"]),
            ArtifactOutcome(item["outcome"]),
            _text(item["recorded_at"]),
            _text(item["origin_run_id"]),
            optional_text(item["regenerated_path"]),
            optional_text(item["profile"]),
            None
            if item["expected"] is None
            else parse_fingerprint(item["expected"], "expected observation"),
            None
            if item["regenerated"] is None
            else parse_fingerprint(item["regenerated"], "regenerated observation"),
            None
            if item["not_compared_reason"] is None
            else NotComparedReason(item["not_compared_reason"]),
            tuple(
                _fields(raw, None, "evidence comparison")
                for raw in _list(item["evidence"])
            ),
            tuple(_text(raw) for raw in _list(item["problem_ids"])),
            optional_text(item["definition_identity"]),
        )


def timestamp(value: object) -> None:
    """Validate canonical UTC observation time, including calendar validity."""

    _matches(value, TIMESTAMP_RE, "reproduction timestamp")
    datetime.strptime(_text(value), "%Y-%m-%dT%H:%M:%SZ")


def run_identity(value: object) -> None:
    """Validate the existing canonical run-ID grammar without finding a folder."""

    _matches(value, RUN_ID_RE, "reproduction run identity")


def optional_text(value: object) -> str | None:
    """Decode an explicitly unavailable scalar, not an omitted field."""

    return None if value is None else _text(value)


def _argument(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ReproductionDomainError("invalid recorded invocation argument")
    return value
