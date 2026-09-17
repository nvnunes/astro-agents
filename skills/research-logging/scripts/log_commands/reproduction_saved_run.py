"""One coherent immutable target view, with derived inspection classifications."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .context import ENTRY_ID_RE
from .reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    Classification,
    ExecutionRef,
    NotComparedReason,
    ReproductionDomainError,
    ReproductionProblem,
    WorkSelection,
    _canonical_json,
    _canonical_path,
    _fields,
    _matches,
    _text,
    classify_artifact,
    classify_command,
)
from .reproduction_invocation import MAX_EXECUTION_TIMEOUT_SECONDS
from .reproduction_run import ArtifactResult, CommandResult, run_identity, timestamp
from .reproduction_work import (
    ArtifactWork,
    CommandWork,
    _list,
    validate_problem_links,
    validate_producer,
)

RESULT_SCHEMA = "research-log-reproduction-result/13"
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_WORK_RECORDS = 100_000


@dataclass(frozen=True)
class RunTarget:
    """Recorded coverage, never an execution selector or a cumulative view."""

    kind: str = "log"
    entry: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "log" and self.entry is None:
            return
        if self.kind != "entry":
            raise ReproductionDomainError("unsupported reproduction target")
        _matches(self.entry, ENTRY_ID_RE, "target entry")

    def as_dict(self) -> dict[str, object]:
        """Serialize exact accepted coverage."""

        return {"kind": self.kind, "entry": self.entry}

    @classmethod
    def from_dict(cls, value: object) -> RunTarget:
        """Reject historical execution targets and unknown coverage fields."""

        item = _fields(value, {"kind", "entry"}, "run target")
        return cls(
            _text(item["kind"]), None if item["entry"] is None else _text(item["entry"])
        )


@dataclass(frozen=True)
class RunSettings:
    """The immutable target policy and runtime controls actually accepted."""

    include_all: bool = False
    recheck: bool = False
    jobs: int = 1
    execution_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if type(self.include_all) is not bool or type(self.recheck) is not bool:
            raise ReproductionDomainError("run policy must be boolean")
        if type(self.jobs) is not int or self.jobs < 1:
            raise ReproductionDomainError("jobs must be a positive integer")
        if (
            type(self.execution_timeout_seconds) is not int
            or not 1 <= self.execution_timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS
        ):
            raise ReproductionDomainError(
                "execution timeout is outside its fixed range"
            )

    def as_dict(self) -> dict[str, object]:
        """Serialize policies separately from recorded recipe eligibility."""

        return {
            "include_all": self.include_all,
            "recheck": self.recheck,
            "jobs": self.jobs,
            "execution_timeout_seconds": self.execution_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunSettings:
        """Validate the complete settings object without normalizing bad values."""

        item = _fields(
            value,
            {"include_all", "recheck", "jobs", "execution_timeout_seconds"},
            "run settings",
        )
        return cls(**item)  # type: ignore[arg-type]


@dataclass(frozen=True)
class SavedRun:
    """Normally published facts and minimal lookup indexes derived from them.

    Every accepted command/artifact is represented once. Attempt results occur
    only for selected work; artifact results cover all counted artifacts and
    explicitly identify comparisons reused from an earlier run. Operationally
    failed or stopped jobs do not become this saved inspection record.
    """

    summary: str
    run_id: str
    target: RunTarget
    settings: RunSettings
    accepted_at: str
    finished_at: str
    commands: tuple[CommandWork, ...]
    artifacts: tuple[ArtifactWork, ...]
    command_results: tuple[CommandResult, ...]
    artifact_results: tuple[ArtifactResult, ...]
    problems: tuple[ReproductionProblem, ...]
    _commands: Mapping[ExecutionRef, CommandWork] = field(
        init=False, repr=False, compare=False
    )
    _artifacts: Mapping[ArtifactRef, ArtifactWork] = field(
        init=False, repr=False, compare=False
    )
    _command_results: Mapping[ExecutionRef, CommandResult] = field(
        init=False, repr=False, compare=False
    )
    _artifact_results: Mapping[ArtifactRef, ArtifactResult] = field(
        init=False, repr=False, compare=False
    )
    _problems: Mapping[str, ReproductionProblem] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _canonical_path(self.summary)
        run_identity(self.run_id)
        if not isinstance(self.target, RunTarget) or not isinstance(
            self.settings, RunSettings
        ):
            raise ReproductionDomainError("saved run has invalid target/settings")
        timestamp(self.accepted_at)
        timestamp(self.finished_at)
        if self.accepted_at > self.finished_at:
            raise ReproductionDomainError("run finishes before acceptance")
        self._index_records()
        self._validate_work_links()
        self._validate_result_links()
        records: tuple[
            CommandWork | ArtifactWork | CommandResult | ArtifactResult,
            ...,
        ] = (
            *self.commands,
            *self.artifacts,
            *self.command_results,
            *self.artifact_results,
        )
        validate_problem_links(
            self._commands,
            self._artifacts,
            self._problems,
            tuple((record.identity, record.problem_ids) for record in records),
            summary=self.summary,
        )

    def _index_records(self) -> None:
        collections = (
            ("commands", CommandWork, "_commands", "identity"),
            ("artifacts", ArtifactWork, "_artifacts", "identity"),
            ("command_results", CommandResult, "_command_results", "identity"),
            ("artifact_results", ArtifactResult, "_artifact_results", "identity"),
            ("problems", ReproductionProblem, "_problems", "problem_id"),
        )
        for name, record_type, index_name, identity_name in collections:
            records = getattr(self, name)
            if not isinstance(records, tuple) or len(records) > MAX_WORK_RECORDS:
                raise ReproductionDomainError(f"{name} exceeds its fixed record bound")
            if any(not isinstance(record, record_type) for record in records):
                raise ReproductionDomainError(f"invalid {name} record")
            indexed = {getattr(record, identity_name): record for record in records}
            if len(indexed) != len(records):
                raise ReproductionDomainError(f"duplicate {name} identity")
            object.__setattr__(self, index_name, MappingProxyType(indexed))
            object.__setattr__(
                self, name, tuple(indexed[key] for key in sorted(indexed))
            )

    def _validate_work_links(self) -> None:
        for work in self.commands:
            self._coverage(work.identity.entry)
            if any(identity not in self._commands for identity in work.dependencies):
                raise ReproductionDomainError(
                    "dependency is outside the accepted inventory"
                )
            self._problem_links(work.problem_ids)
        for artifact in self.artifacts:
            self._coverage(artifact.identity.entry)
            validate_producer(artifact, self._commands)
            self._problem_links(artifact.problem_ids)
        for problem in self.problems:
            self._problem_owner(problem)

    def _validate_result_links(self) -> None:
        if set(self._artifact_results) != set(self._artifacts):
            raise ReproductionDomainError(
                "artifact results do not cover the accepted inventory"
            )
        selected = {
            work.identity
            for work in self.commands
            if work.selection is WorkSelection.RUN
        }
        locally_blocked = {
            work.identity
            for work in self.commands
            if work.selection is WorkSelection.BLOCKED
        }
        if (
            not selected <= set(self._command_results)
            or not set(self._command_results) <= selected | locally_blocked
        ):
            raise ReproductionDomainError(
                "command results do not match accepted selection"
            )
        for result in self.command_results:
            self._problem_links(result.problem_ids)
            if any(identity not in self._commands for identity in result.blocked_by):
                raise ReproductionDomainError(
                    "blocked prerequisite is outside accepted inventory"
                )
        for artifact_result in self.artifact_results:
            self._problem_links(artifact_result.problem_ids)
            if (
                artifact_result.outcome is not ArtifactOutcome.NOT_COMPARED
                and self._artifacts[artifact_result.identity].producer is None
            ):
                raise ReproductionDomainError(
                    "compared artifact has no recorded producer"
                )
        for identity in selected | locally_blocked:
            self.command_classification(identity)
        for artifact_result in self.artifact_results:
            self._validate_artifact_effect(artifact_result)

    def _validate_artifact_effect(self, result: ArtifactResult) -> None:
        if result.not_compared_reason is None:
            return
        expected_status = {
            NotComparedReason.COMMAND_FAILED: "failed",
            NotComparedReason.COMMAND_BLOCKED: "blocked",
            NotComparedReason.COMMAND_NOT_RUN: "not-run",
            NotComparedReason.SKIPPED_BY_POLICY: "skipped-by-policy",
        }.get(result.not_compared_reason)
        if expected_status is None:
            return
        work = self._artifacts[result.identity]
        producer = work.producer
        if producer is None:
            kind = work.boundary.get("kind") if work.boundary is not None else None
            boundary_reason = {
                "cross_entry": NotComparedReason.COMMAND_NOT_RUN,
                "outside_queue": NotComparedReason.COMMAND_NOT_RUN,
                "non_automatic": NotComparedReason.SKIPPED_BY_POLICY,
            }.get(kind if isinstance(kind, str) else "")
            if result.not_compared_reason is boundary_reason:
                return
            raise ReproductionDomainError(
                "command-derived artifact reason has no producer"
            )
        if self.command_classification(producer).status != expected_status:
            raise ReproductionDomainError(
                "artifact reason contradicts its recorded producer"
            )

    def _coverage(self, entry: str) -> None:
        if self.target.kind == "entry" and entry != self.target.entry:
            raise ReproductionDomainError("record is outside accepted entry coverage")

    def _problem_links(self, references: tuple[str, ...]) -> None:
        if any(identity not in self._problems for identity in references):
            raise ReproductionDomainError("unknown problem reference")

    def _problem_owner(self, problem: ReproductionProblem) -> None:
        if (
            isinstance(problem.subject, ExecutionRef)
            and problem.subject not in self._commands
        ):
            raise ReproductionDomainError(
                "problem command owner is outside accepted inventory"
            )
        if (
            isinstance(problem.subject, ArtifactRef)
            and problem.subject not in self._artifacts
        ):
            raise ReproductionDomainError(
                "problem artifact owner is outside accepted inventory"
            )

    def command_classification(self, identity: ExecutionRef) -> Classification:
        """Use the single classifier and existing prerequisite references."""

        work = self._commands[identity]
        result = self._command_results.get(identity)
        return classify_command(
            work.selection,
            result.outcome if result is not None else None,
            self.primary_problem(identity),
        )

    def command(self, identity: ExecutionRef) -> CommandWork:
        """Return the exact accepted command; unknown identities raise KeyError."""

        return self._commands[identity]

    def command_result(self, identity: ExecutionRef) -> CommandResult | None:
        """Return an actual attempt/block result, never invent one for unrun work."""

        return self._command_results.get(identity)

    def artifact(self, identity: ArtifactRef) -> ArtifactWork:
        """Return the exact counted artifact; unknown identities raise KeyError."""

        return self._artifacts[identity]

    def artifact_result(self, identity: ArtifactRef) -> ArtifactResult:
        """Return the recorded comparison/non-comparison through its identity."""

        return self._artifact_results[identity]

    def problem(self, problem_id: str) -> ReproductionProblem:
        """Return the owned diagnosis without copying or re-reading source facts."""

        return self._problems[problem_id]

    def primary_problem(self, identity: ExecutionRef) -> ReproductionProblem | None:
        """Resolve retained deterministic cause precedence, without copying causes."""

        pending, seen = [identity], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            work = self._commands[current]
            result = self._command_results.get(current)
            references = (
                result.problem_ids
                if result is not None and result.problem_ids
                else work.problem_ids
            )
            if references:
                return self._problems[references[0]]
            prerequisites = (
                result.blocked_by if result is not None else work.dependencies
            )
            pending.extend(reversed(prerequisites))
        return None

    def artifact_classification(self, identity: ArtifactRef) -> Classification:
        """Classify immutable observations without currentness or filesystem reads."""

        result = self._artifact_results[identity]
        return classify_artifact(result.outcome, result.not_compared_reason)

    def as_dict(self) -> dict[str, object]:
        """Serialize one normalized target view; no totals or accounting copies."""

        return {
            "schema": RESULT_SCHEMA,
            "summary": self.summary,
            "run_id": self.run_id,
            "target": self.target.as_dict(),
            "settings": self.settings.as_dict(),
            "accepted_at": self.accepted_at,
            "finished_at": self.finished_at,
            "status": "complete",
            "commands": [item.as_dict() for item in self.commands],
            "artifacts": [item.as_dict() for item in self.artifacts],
            "command_results": [item.as_dict() for item in self.command_results],
            "artifact_results": [item.as_dict() for item in self.artifact_results],
            "problems": [item.as_dict() for item in self.problems],
        }

    def serialized(self) -> str:
        """Produce deterministic bounded JSON without diagnostic truncation."""

        text = _canonical_json(self.as_dict())
        if len(text.encode()) > MAX_RESULT_BYTES:
            raise ReproductionDomainError("saved run exceeds its fixed byte bound")
        return text

    @classmethod
    def from_json(cls, raw: bytes) -> SavedRun:
        """Read only result/13; older results require rerunning reproduction."""

        if len(raw) > MAX_RESULT_BYTES:
            raise ReproductionDomainError("saved run exceeds its fixed byte bound")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise ReproductionDomainError("saved run is not valid JSON") from error
        item = _fields(
            value,
            {
                "schema",
                "summary",
                "run_id",
                "target",
                "settings",
                "accepted_at",
                "finished_at",
                "status",
                "commands",
                "artifacts",
                "command_results",
                "artifact_results",
                "problems",
            },
            "saved run",
        )
        if item["schema"] != RESULT_SCHEMA:
            raise ReproductionDomainError(
                "unsupported reproduction results; rerun with --recheck"
            )
        if item["status"] != "complete":
            raise ReproductionDomainError(
                "unpublished job cannot replace saved inspection"
            )
        run = cls(
            _text(item["summary"]),
            _text(item["run_id"]),
            RunTarget.from_dict(item["target"]),
            RunSettings.from_dict(item["settings"]),
            _text(item["accepted_at"]),
            _text(item["finished_at"]),
            tuple(CommandWork.from_dict(raw) for raw in _list(item["commands"])),
            tuple(ArtifactWork.from_dict(raw) for raw in _list(item["artifacts"])),
            tuple(
                CommandResult.from_dict(raw) for raw in _list(item["command_results"])
            ),
            tuple(
                ArtifactResult.from_dict(raw) for raw in _list(item["artifact_results"])
            ),
            tuple(
                ReproductionProblem.from_dict(raw) for raw in _list(item["problems"])
            ),
        )
        if run.serialized().encode() != raw:
            raise ReproductionDomainError("saved run is not canonical")
        return run
