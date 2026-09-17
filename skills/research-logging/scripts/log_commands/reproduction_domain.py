"""Canonical reproduction identities, owned diagnoses and outcome vocabulary.

Planning selection, attempted execution and artifact comparison are distinct
facts. Diagnoses have local content identities; affected work references those
diagnoses rather than copying them. This domain has no saved-report/currentness
projection and does not read files, jobs, result stores or source registries.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping, TypeAlias

from validation.domain import SourceLocation, plain_json
from validation.pyrun_state import NAME_RE, PYRUN_EXECUTION_RE

from .context import ENTRY_ID_RE

MAX_PROBLEM_BYTES = 16 * 1024
MAX_INSPECTION_BYTES = 128 * 1024
MAX_INSPECTION_ITEMS = 50
MAX_STREAM_TAIL_BYTES = 16 * 1024
PROBLEM_ID_RE = re.compile(r"problem-[0-9a-f]{64}\Z")
PROBLEM_CODES = frozenset(
    {
        "baseline_unavailable",
        "baseline_changed",
        "boundary_changed",
        "boundary_unavailable",
        "capture_failed",
        "comparator_error",
        "content_changed",
        "cross_log_generated_input",
        "dependency_cycle",
        "direct_input_changed",
        "direct_input_unavailable",
        "execution_exception",
        "execution_failed",
        "execution_timeout",
        "evidence_comparison_failed",
        "evidence_context_changed",
        "generation_failed",
        "graph_limit",
        "missing_input",
        "missing_producer",
        "multiple_producers",
        "output_materialization_failed",
        "output_missing",
        "effective_code_changed",
        "effective_code_unavailable",
        "reproduction.input.unavailable",
        "resource_limit",
        "safety_failure",
        "unsupported_format",
        "validation_blocked",
    }
)


class ReproductionDomainError(ValueError):
    """A canonical record is malformed or cannot retain its bounded diagnosis."""


class WorkSelection(str, Enum):
    """Mutually exclusive decisions made before attempting execution."""

    RUN = "run"
    BLOCKED = "blocked"
    NOT_NEEDED = "not_needed"
    PREVIOUS_FAILURE = "previous_failure"
    PREVIOUS_BLOCK = "previous_block"
    SKIPPED_BY_POLICY = "skipped_by_policy"


class CommandOutcome(str, Enum):
    """Terminal execution results; blocked means no attempt was made."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class ArtifactOutcome(str, Enum):
    """Comparison completion, independent of command execution success."""

    MATCHED = "matched"
    NOT_MATCHED = "not-matched"
    NOT_COMPARED = "not-compared"


class NotComparedReason(str, Enum):
    """Exhaustive reasons an artifact has no completed comparison."""

    COMMAND_FAILED = "command-failed"
    COMMAND_BLOCKED = "command-blocked"
    COMMAND_NOT_RUN = "command-not-run"
    SKIPPED_BY_POLICY = "skipped-by-policy"
    COMPARISON_FAILED = "comparison-failed"


class ProblemStage(str, Enum):
    """The point where a mechanical cause became known, not a second status."""

    PREPARE = "prepare"
    LAUNCH = "launch"
    CAPTURE = "capture"
    EXECUTE = "execute"
    MATERIALIZE = "materialize"
    COMPARE = "compare"


@dataclass(frozen=True, order=True)
class ExecutionRef:
    """An expanded command unit; equal digests in other entries/CIDs are distinct."""

    entry: str
    cid: str
    execution_id: str

    def __post_init__(self) -> None:
        _matches(self.entry, ENTRY_ID_RE, "execution entry")
        _matches(self.cid, NAME_RE, "execution CID")
        _matches(self.execution_id, PYRUN_EXECUTION_RE, "execution identity")

    def as_dict(self) -> dict[str, object]:
        """Return the complete compound identity with no display-path inference."""

        return {"entry": self.entry, "cid": self.cid, "execution_id": self.execution_id}

    @classmethod
    def from_dict(cls, value: object) -> ExecutionRef:
        """Reject partial identities and unknown fields rather than guessing owners."""

        item = _fields(value, {"entry", "cid", "execution_id"}, "execution reference")
        return cls(
            _text(item["entry"]), _text(item["cid"]), _text(item["execution_id"])
        )


@dataclass(frozen=True, order=True)
class ArtifactRef:
    """One counted artifact at its entry-qualified canonical retained identity.

    Canonical absolute and <project>/ identities preserve external boundaries;
    ordinary identities are entry-relative. No path is resolved or accessed.
    """

    entry: str
    artifact: str

    def __post_init__(self) -> None:
        _matches(self.entry, ENTRY_ID_RE, "artifact entry")
        _canonical_path(self.artifact)

    def as_dict(self) -> dict[str, object]:
        """Return the exact artifact owner and identity."""

        return {"entry": self.entry, "artifact": self.artifact}

    @classmethod
    def from_dict(cls, value: object) -> ArtifactRef:
        """Decode a closed artifact identity without execution-status inference."""

        item = _fields(value, {"entry", "artifact"}, "artifact reference")
        return cls(_text(item["entry"]), _text(item["artifact"]))


@dataclass(frozen=True, order=True)
class SourceRef:
    """A shared canonical source/definition location with no invented command owner."""

    path: str

    def __post_init__(self) -> None:
        _canonical_path(self.path)

    def as_dict(self) -> dict[str, object]:
        """Return the already canonical source identity."""

        return {"path": self.path}


ProblemSubject: TypeAlias = ExecutionRef | ArtifactRef | SourceRef


@dataclass(frozen=True)
class ReproductionProblem:
    """One mechanically observed cause at its actual subject.

    Attributes:
        subject: Exact command, artifact or shared source owner.
        code: Mechanical cause code, never inferred from diagnostic prose.
        stage: Where the cause was observed.
        explanation: Plain-language diagnosis retained at observation time.
        observed: Complete bounded code-specific facts, including actual/expected
            values when those distinguish the defect. Nested values are frozen.
        locations: Exact authored or retained locations useful for debugging.

    The local identity hashes subject/code/stage/observed, not wording or
    consumers. Distinct observations remain distinct and no fuzzy dedup occurs.
    """

    subject: ProblemSubject
    code: str
    stage: ProblemStage
    explanation: str
    observed: Mapping[str, object]
    locations: tuple[SourceLocation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.subject, (ExecutionRef, ArtifactRef, SourceRef)):
            raise ReproductionDomainError("problem subject has no canonical owner")
        _text(self.code)
        if self.code not in PROBLEM_CODES:
            raise ReproductionDomainError("unknown mechanical problem code")
        _text(self.explanation)
        if not isinstance(self.stage, ProblemStage):
            raise ReproductionDomainError("invalid problem stage")
        if not isinstance(self.observed, Mapping):
            raise ReproductionDomainError("problem observations must be an object")
        object.__setattr__(self, "observed", _frozen_object(self.observed))
        if any(not isinstance(item, SourceLocation) for item in self.locations):
            raise ReproductionDomainError("invalid problem source location")
        object.__setattr__(
            self,
            "locations",
            tuple(
                sorted(
                    set(self.locations), key=lambda item: (item.path, item.line or 0)
                )
            ),
        )
        if len(_canonical_json(self.as_dict()).encode()) > MAX_PROBLEM_BYTES:
            raise ReproductionDomainError(
                "complete problem diagnosis exceeds its byte bound"
            )

    @property
    def problem_id(self) -> str:
        """Return a deterministic local mechanical identity, independent of fan-out."""

        value = self._identity_facts()
        return "problem-" + hashlib.sha256(_canonical_json(value).encode()).hexdigest()

    def _identity_facts(self) -> dict[str, object]:
        return {
            "subject": _subject_value(self.subject),
            "code": self.code,
            "stage": self.stage.value,
            "observed": plain_json(self.observed),
        }

    def as_dict(self) -> dict[str, object]:
        """Serialize the closed diagnosis; do not embed affected-item inventories."""

        return {
            **self._identity_facts(),
            "explanation": self.explanation,
            "locations": [item.as_dict() for item in self.locations],
        }

    @classmethod
    def from_dict(cls, value: object) -> ReproductionProblem:
        """Validate one complete diagnosis; unknown/partial records fail closed."""

        item = _fields(
            value,
            {"subject", "code", "stage", "observed", "explanation", "locations"},
            "problem",
        )
        if not isinstance(item["locations"], list):
            raise ReproductionDomainError("problem locations must be a list")
        return cls(
            _subject_from_dict(item["subject"]),
            _text(item["code"]),
            ProblemStage(item["stage"]),
            _text(item["explanation"]),
            _fields(item["observed"], None, "observations"),
            tuple(SourceLocation.from_dict(raw) for raw in item["locations"]),
        )


@dataclass(frozen=True)
class Classification:
    """Derived status/primary reason shared by summaries, inventories and filters."""

    status: str
    reason: str | None


_NOT_RUN_REASONS = {
    WorkSelection.NOT_NEEDED: "not-needed",
    WorkSelection.PREVIOUS_FAILURE: "previous-failure",
    WorkSelection.PREVIOUS_BLOCK: "previous-block",
}


def classify_command(
    selection: WorkSelection,
    outcome: CommandOutcome | None,
    primary_problem: ReproductionProblem | None = None,
) -> Classification:
    """Classify one saved command from selection and terminal facts only.

    Normally published selected work must be terminal; pending/stopped work
    belongs to job status, not saved inspection. The caller resolves the first
    relevant problem in deterministic planning/error order. No accounting reason
    is persisted independently of those facts, and all causes remain in detail.
    """

    if not isinstance(selection, WorkSelection):
        raise ReproductionDomainError("invalid work selection")
    if outcome is not None and not isinstance(outcome, CommandOutcome):
        raise ReproductionDomainError("invalid command outcome")
    if selection in _NOT_RUN_REASONS:
        _no_attempt(outcome)
        return Classification("not-run", _NOT_RUN_REASONS[selection])
    if selection is WorkSelection.SKIPPED_BY_POLICY:
        _no_attempt(outcome)
        return Classification("skipped-by-policy", "skipped-by-policy")
    if selection is WorkSelection.BLOCKED:
        if outcome not in {None, CommandOutcome.BLOCKED}:
            raise ReproductionDomainError(
                "blocked work cannot have an attempted outcome"
            )
        outcome = CommandOutcome.BLOCKED
    if outcome is None:
        raise ReproductionDomainError(
            "normally published selected work has no terminal outcome"
        )
    if outcome is CommandOutcome.SUCCEEDED:
        return Classification("succeeded", None)
    if primary_problem is None:
        raise ReproductionDomainError(
            "failed or blocked command has no diagnosed cause"
        )
    return Classification(outcome.value, primary_problem.code)


def classify_artifact(
    outcome: ArtifactOutcome,
    reason: NotComparedReason | None,
) -> Classification:
    """Classify comparisons without treating unequal artifacts as command failures."""

    if not isinstance(outcome, ArtifactOutcome):
        raise ReproductionDomainError("invalid artifact outcome")
    if outcome is ArtifactOutcome.NOT_COMPARED:
        if not isinstance(reason, NotComparedReason):
            raise ReproductionDomainError("not-compared artifact has no precise reason")
        return Classification(outcome.value, reason.value)
    if reason is not None:
        raise ReproductionDomainError(
            "completed comparison cannot have a non-comparison reason"
        )
    return Classification(outcome.value, None)


def _no_attempt(outcome: CommandOutcome | None) -> None:
    if outcome is not None:
        raise ReproductionDomainError("excluded/not-run work cannot have a new outcome")


def _subject_value(subject: ProblemSubject) -> dict[str, object]:
    kind = (
        "command"
        if isinstance(subject, ExecutionRef)
        else "artifact"
        if isinstance(subject, ArtifactRef)
        else "source"
    )
    return {"kind": kind, **subject.as_dict()}


def _subject_from_dict(value: object) -> ProblemSubject:
    item = _fields(value, None, "problem subject")
    kind = item.get("kind")
    identity = {key: raw for key, raw in item.items() if key != "kind"}
    if kind == "command":
        return ExecutionRef.from_dict(identity)
    if kind == "artifact":
        return ArtifactRef.from_dict(identity)
    if kind == "source":
        return SourceRef(_text(_fields(identity, {"path"}, "source reference")["path"]))
    raise ReproductionDomainError("invalid problem subject kind")


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ReproductionDomainError("expected nonempty text without NUL")
    return value


def _matches(value: object, pattern: re.Pattern[str], subject: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReproductionDomainError(f"invalid {subject}")


def _canonical_path(value: object) -> None:
    path = _text(value)
    pure = PurePosixPath(path)
    if path.startswith("//") or "\\" in path or pure.as_posix() != path:
        raise ReproductionDomainError("noncanonical material path")
    if pure == PurePosixPath(".") or pure == PurePosixPath("/") or ".." in pure.parts:
        raise ReproductionDomainError("material path has no safe canonical identity")


def _fields(
    value: object, fields: set[str] | None, subject: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ReproductionDomainError(f"{subject} must be an object")
    if fields is not None and set(value) != fields:
        raise ReproductionDomainError(f"invalid {subject} fields")
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            plain_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ReproductionDomainError(
            "observations are not finite JSON values"
        ) from error


def _frozen_object(value: Mapping[str, object]) -> Mapping[str, object]:
    try:
        _json_keys(value)
    except RecursionError as error:
        raise ReproductionDomainError(
            "observations are cyclic or too deeply nested"
        ) from error
    decoded = json.loads(_canonical_json(value))
    return MappingProxyType({key: _freeze(raw) for key, raw in decoded.items()})


def _json_keys(value: object) -> None:
    """Reject key coercion before canonical JSON can erase an invalid identity."""

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ReproductionDomainError("observations require string object keys")
        for raw in value.values():
            _json_keys(raw)
    elif isinstance(value, (list, tuple)):
        for raw in value:
            _json_keys(raw)


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(raw) for key, raw in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(raw) for raw in value)
    return value
