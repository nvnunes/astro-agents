"""Typed plan/14 acceptance boundary, shared by fresh preview and real launch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, cast

from research_log_data import parse_fingerprint, parse_resource_identity
from validation.domain import plain_json

from .context import ENTRY_ID_RE
from .reproduction_admission import ExecutionAdmission
from .reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    ExecutionRef,
    ReproductionDomainError,
    ReproductionProblem,
    WorkSelection,
    _canonical_json,
    _canonical_path,
    _fields,
    _frozen_object,
    _matches,
    _text,
)
from .reproduction_run import ArtifactResult
from .reproduction_saved_run import MAX_WORK_RECORDS, RunSettings, RunTarget
from .reproduction_work import (
    DIGEST_RE,
    ArtifactWork,
    CommandWork,
    _command_consumes_artifact,
    _list,
    validate_problem_links,
    validate_producer,
)

PLAN_SCHEMA = "research-log-reproduction-plan/14"
MAX_PLAN_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ReproductionPlan:
    """Frozen target facts and genuine execution/scheduling infrastructure.

    There are no cases/failures inventories or copied diagnosis fields. Runnable
    scheduling rows point to canonical work; policy/need flags and complete
    output sets are obtained from its existing typed execution recipe.
    """

    summary: str
    target: RunTarget
    settings: RunSettings
    admission: Mapping[str, object]
    commands: tuple[CommandWork, ...]
    artifacts: tuple[ArtifactWork, ...]
    problems: tuple[ReproductionProblem, ...]
    materials: tuple[Mapping[str, object], ...]
    evidence_only: tuple[Mapping[str, object], ...]
    scheduling: tuple[Mapping[str, object], ...]
    reusable_artifact_results: tuple[ArtifactResult, ...] = ()
    _commands: Mapping[ExecutionRef, CommandWork] = field(
        init=False, repr=False, compare=False
    )
    _artifacts: Mapping[ArtifactRef, ArtifactWork] = field(
        init=False, repr=False, compare=False
    )
    _scheduling: Mapping[ExecutionRef, Mapping[str, object]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _canonical_path(self.summary)
        if not isinstance(self.target, RunTarget) or not isinstance(
            self.settings, RunSettings
        ):
            raise ReproductionDomainError("plan has invalid target/settings")
        self._validate_work()
        self._freeze_infrastructure()
        _validate_admission(self.admission, {work.identity for work in self.commands})
        self._validate_scheduling()
        self._validate_reuse()
        records: tuple[CommandWork | ArtifactWork | ArtifactResult, ...] = (
            *self.commands,
            *self.artifacts,
            *self.reusable_artifact_results,
        )
        validate_problem_links(
            {work.identity: work for work in self.commands},
            {work.identity: work for work in self.artifacts},
            {problem.problem_id: problem for problem in self.problems},
            tuple((record.identity, record.problem_ids) for record in records),
            summary=self.summary,
        )

    def command(self, identity: ExecutionRef) -> CommandWork:
        """Return the exact accepted command object, without decoding or copying."""

        try:
            return self._commands[identity]
        except KeyError as error:
            raise ReproductionDomainError("command is outside accepted work") from error

    def artifact(self, identity: ArtifactRef) -> ArtifactWork:
        """Return one counted artifact, not the execution's complete output set."""

        try:
            return self._artifacts[identity]
        except KeyError as error:
            raise ReproductionDomainError(
                "artifact is outside accepted work"
            ) from error

    def dependency_artifacts(
        self, consumer: ExecutionRef, producer: ExecutionRef
    ) -> tuple[ArtifactWork, ...]:
        """Return exact accepted artifacts carried by one dependency edge."""

        command = self.command(consumer)
        if producer not in command.dependencies:
            raise ReproductionDomainError(
                "artifact lookup is not an accepted command dependency"
            )
        related = tuple(
            artifact
            for artifact in self.artifacts
            if artifact.producer == producer
            and _command_consumes_artifact(command, artifact, self._commands)
        )
        if not related:
            raise ReproductionDomainError(
                "accepted dependency has no consumed artifact"
            )
        return related

    def schedule(self, identity: ExecutionRef) -> Mapping[str, object]:
        """Return the native claim row; nonrunnable work has no schedule."""

        try:
            return self._scheduling[identity]
        except KeyError as error:
            raise ReproductionDomainError("command has no accepted schedule") from error

    def _validate_work(self) -> None:
        for name, kind, identity in (
            ("commands", CommandWork, "identity"),
            ("artifacts", ArtifactWork, "identity"),
            ("problems", ReproductionProblem, "problem_id"),
        ):
            records = getattr(self, name)
            if (
                not isinstance(records, tuple)
                or len(records) > MAX_WORK_RECORDS
                or any(not isinstance(record, kind) for record in records)
            ):
                raise ReproductionDomainError(f"invalid or excessive plan {name}")
            indexed = {getattr(record, identity): record for record in records}
            if len(indexed) != len(records):
                raise ReproductionDomainError(f"duplicate plan {name} identity")
            object.__setattr__(
                self, name, tuple(indexed[key] for key in sorted(indexed))
            )
        _validate_work_links(self.commands, self.artifacts, self.problems, self.target)
        object.__setattr__(
            self,
            "_commands",
            MappingProxyType({work.identity: work for work in self.commands}),
        )
        object.__setattr__(
            self,
            "_artifacts",
            MappingProxyType({work.identity: work for work in self.artifacts}),
        )

    def _freeze_infrastructure(self) -> None:
        object.__setattr__(self, "admission", _frozen_object(self.admission))
        for name in ("materials", "evidence_only", "scheduling"):
            records = getattr(self, name)
            if not isinstance(records, tuple) or len(records) > MAX_WORK_RECORDS:
                raise ReproductionDomainError(f"invalid or excessive plan {name}")
            object.__setattr__(
                self, name, tuple(_frozen_object(record) for record in records)
            )
        for material in self.materials:
            _validate_material(material)
        definitions = {
            work.definition_identity: work.identity.entry
            for work in self.artifacts
            if work.definition_identity is not None
        }
        for row in self.evidence_only:
            _validate_evidence_only(row, definitions, self.target)

    def _validate_scheduling(self) -> None:
        selected = {
            work.identity
            for work in self.commands
            if work.selection is WorkSelection.RUN
        }
        observed: set[ExecutionRef] = set()
        orders: dict[ExecutionRef, int] = {}
        scheduling = {}
        for row in self.scheduling:
            _fields(
                row,
                {
                    "identity",
                    "order",
                    "read_paths",
                    "write_paths",
                    "run_path",
                    "writable_paths",
                },
                "scheduling claims",
            )
            identity = ExecutionRef.from_dict(row["identity"])
            order = row["order"]
            if (
                identity in observed
                or type(order) is not int
                or order < 1
                or order in orders.values()
            ):
                raise ReproductionDomainError(
                    "invalid/duplicate scheduling identity or order"
                )
            observed.add(identity)
            scheduling[identity] = row
            orders[identity] = order
            _canonical_path(row["run_path"])
            for name in ("read_paths", "write_paths", "writable_paths"):
                _validate_paths(row[name])
        if observed != selected:
            raise ReproductionDomainError(
                "scheduling claims do not match selected work"
            )
        self._validate_ordering(orders)
        ordered = tuple(
            sorted(self.scheduling, key=lambda row: cast(int, row["order"]))
        )
        object.__setattr__(self, "scheduling", ordered)
        object.__setattr__(self, "_scheduling", MappingProxyType(scheduling))

    def _validate_ordering(self, orders: Mapping[ExecutionRef, int]) -> None:
        if sorted(orders.values()) != list(range(1, len(orders) + 1)):
            raise ReproductionDomainError(
                "scheduling order is not the complete accepted sequence"
            )
        commands = self._commands
        for identity, order in orders.items():
            for dependency in commands[identity].dependencies:
                if commands[dependency].selection is WorkSelection.BLOCKED:
                    raise ReproductionDomainError(
                        "runnable command has a blocked prerequisite"
                    )
                if dependency in orders and orders[dependency] >= order:
                    raise ReproductionDomainError(
                        "scheduling dependency is not before its consumer"
                    )

    def _validate_reuse(self) -> None:
        artifacts = {work.identity for work in self.artifacts}
        known_problems = {problem.problem_id for problem in self.problems}
        seen = set()
        if (
            not isinstance(self.reusable_artifact_results, tuple)
            or len(self.reusable_artifact_results) > MAX_WORK_RECORDS
        ):
            raise ReproductionDomainError("invalid or excessive reusable comparisons")
        for result in self.reusable_artifact_results:
            if (
                not isinstance(result, ArtifactResult)
                or result.identity not in artifacts
                or result.identity in seen
            ):
                raise ReproductionDomainError(
                    "invalid/duplicate reusable comparison identity"
                )
            if result.outcome is ArtifactOutcome.NOT_COMPARED:
                raise ReproductionDomainError(
                    "a missing comparison cannot be reused as completed"
                )
            self._validate_reuse_binding(result)
            if any(identity not in known_problems for identity in result.problem_ids):
                raise ReproductionDomainError(
                    "reusable comparison has unknown retained problem"
                )
            seen.add(result.identity)
        object.__setattr__(
            self,
            "reusable_artifact_results",
            tuple(
                sorted(
                    self.reusable_artifact_results, key=lambda result: result.identity
                )
            ),
        )

    def _validate_reuse_binding(self, result: ArtifactResult) -> None:
        work = self._artifacts[result.identity]
        if work.producer is None:
            raise ReproductionDomainError(
                "reusable comparison has no accepted producer"
            )
        if self._commands[work.producer].selection in {
            WorkSelection.RUN,
            WorkSelection.BLOCKED,
        }:
            raise ReproductionDomainError(
                "selected work cannot reuse a prior comparison"
            )
        if result.expected != work.baseline:
            raise ReproductionDomainError(
                "reusable comparison uses a different baseline"
            )
        if result.definition_identity != work.definition_identity:
            raise ReproductionDomainError(
                "reusable comparison uses a different evidence definition"
            )

    def as_dict(self) -> dict[str, object]:
        """Return the complete closed plan/14 field set, not preview pagination."""

        return {
            "schema": PLAN_SCHEMA,
            "summary": self.summary,
            "target": self.target.as_dict(),
            "settings": self.settings.as_dict(),
            "admission": plain_json(self.admission),
            "commands": [work.as_dict() for work in self.commands],
            "artifacts": [work.as_dict() for work in self.artifacts],
            "problems": [problem.as_dict() for problem in self.problems],
            "materials": [plain_json(row) for row in self.materials],
            "evidence_only": [plain_json(row) for row in self.evidence_only],
            "scheduling": [plain_json(row) for row in self.scheduling],
            "reusable_artifact_results": [
                result.as_dict() for result in self.reusable_artifact_results
            ],
        }

    def serialized(self) -> str:
        """Canonical bounded acceptance bytes, preserving every diagnosis."""

        text = _canonical_json(self.as_dict())
        if len(text.encode()) > MAX_PLAN_BYTES:
            raise ReproductionDomainError("accepted plan exceeds its fixed byte bound")
        return text

    @classmethod
    def from_json(cls, raw: bytes) -> ReproductionPlan:
        """Read only plan/14; unsupported durable jobs are never translated."""

        if len(raw) > MAX_PLAN_BYTES:
            raise ReproductionDomainError("accepted plan exceeds its fixed byte bound")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise ReproductionDomainError("accepted plan is not valid JSON") from error
        item = _fields(
            value,
            {
                "schema",
                "summary",
                "target",
                "settings",
                "admission",
                "commands",
                "artifacts",
                "problems",
                "materials",
                "evidence_only",
                "scheduling",
                "reusable_artifact_results",
            },
            "accepted plan",
        )
        if item["schema"] != PLAN_SCHEMA:
            raise ReproductionDomainError(
                "unsupported accepted reproduction plan; no legacy resume"
            )
        plan = cls(
            _text(item["summary"]),
            RunTarget.from_dict(item["target"]),
            RunSettings.from_dict(item["settings"]),
            _fields(item["admission"], None, "admission"),
            tuple(CommandWork.from_dict(row) for row in _list(item["commands"])),
            tuple(ArtifactWork.from_dict(row) for row in _list(item["artifacts"])),
            tuple(
                ReproductionProblem.from_dict(row) for row in _list(item["problems"])
            ),
            tuple(_fields(row, None, "material") for row in _list(item["materials"])),
            tuple(
                _fields(row, None, "evidence-only context")
                for row in _list(item["evidence_only"])
            ),
            tuple(
                _fields(row, None, "scheduling claims")
                for row in _list(item["scheduling"])
            ),
            tuple(
                ArtifactResult.from_dict(row)
                for row in _list(item["reusable_artifact_results"])
            ),
        )
        if plan.serialized().encode() != raw:
            raise ReproductionDomainError("accepted plan is not canonical")
        return plan


def _validate_work_links(
    commands: tuple[CommandWork, ...],
    artifacts: tuple[ArtifactWork, ...],
    problems: tuple[ReproductionProblem, ...],
    target: RunTarget,
) -> None:
    command_ids = {work.identity for work in commands}
    command_index = {work.identity: work for work in commands}
    artifact_ids = {work.identity for work in artifacts}
    problem_ids = {problem.problem_id for problem in problems}
    work_records: tuple[CommandWork | ArtifactWork, ...] = (*commands, *artifacts)
    for work in work_records:
        if target.kind == "entry" and work.identity.entry != target.entry:
            raise ReproductionDomainError("plan work is outside target coverage")
        if any(identity not in problem_ids for identity in work.problem_ids):
            raise ReproductionDomainError("plan work has unknown problem reference")
    for command in commands:
        if any(identity not in command_ids for identity in command.dependencies):
            raise ReproductionDomainError(
                "plan dependency is outside accepted inventory"
            )
    for artifact in artifacts:
        validate_producer(artifact, command_index)
    _validate_problem_owners(problems, command_ids, artifact_ids)


def _validate_problem_owners(
    problems: tuple[ReproductionProblem, ...],
    command_ids: set[ExecutionRef],
    artifact_ids: set[ArtifactRef],
) -> None:
    for problem in problems:
        if (
            isinstance(problem.subject, ExecutionRef)
            and problem.subject not in command_ids
        ):
            raise ReproductionDomainError("plan problem command owner is unknown")
        if (
            isinstance(problem.subject, ArtifactRef)
            and problem.subject not in artifact_ids
        ):
            raise ReproductionDomainError("plan problem artifact owner is unknown")


def _validate_admission(
    admission: Mapping[str, object], command_ids: set[ExecutionRef]
) -> None:
    item = _fields(
        plain_json(admission),
        {
            "schema",
            "validation_snapshot_id",
            "rules_version",
            "evaluated_at",
            "executions",
        },
        "admission",
    )
    if item["schema"] != "research-log-reproduction-admission/1":
        raise ReproductionDomainError("unsupported admission schema")
    _text(item["validation_snapshot_id"])
    _text(item["rules_version"])
    datetime.fromisoformat(_text(item["evaluated_at"]))
    seen = set()
    for raw in _list(item["executions"]):
        row = _fields(
            raw,
            {"entry", "cid", "execution_id", "disposition", "blocking_finding_ids"},
            "execution admission",
        )
        identity = ExecutionRef.from_dict(
            {key: row[key] for key in ("entry", "cid", "execution_id")}
        )
        if identity in seen:
            raise ReproductionDomainError("duplicate admission identity")
        seen.add(identity)
        ExecutionAdmission(
            identity.entry,
            identity.cid,
            identity.execution_id,
            _text(row["disposition"]),
            tuple(_text(value) for value in _list(row["blocking_finding_ids"])),
        )
    if seen != command_ids:
        raise ReproductionDomainError("admission does not cover accepted commands")


def _validate_material(value: Mapping[str, object]) -> None:
    role = value.get("role")
    fields = {"role", "identity", "kind", "fingerprint"}
    if role == "input":
        fields.add("selection")
    item = _fields(value, fields, "retained material")
    if role not in {"input", "script", "code", "comparison_baseline", "boundary"}:
        raise ReproductionDomainError("unknown retained material role")
    identity, kind = _text(item["identity"]), _text(item["kind"])
    _canonical_path(identity)
    parse_fingerprint(item["fingerprint"], identity, kind=kind)
    if role == "input":
        parse_resource_identity(item["selection"], identity, kind=kind)


def _validate_evidence_only(
    value: Mapping[str, object], definitions: Mapping[str, str], target: RunTarget
) -> None:
    item = _fields(
        plain_json(value),
        {
            "comparisons",
            "entry",
            "fingerprint",
            "kind",
            "record_id",
            "resource",
            "selection",
        },
        "evidence-only context",
    )
    entry = _text(item["entry"])
    _matches(entry, ENTRY_ID_RE, "evidence entry")
    if target.kind == "entry" and entry != target.entry:
        raise ReproductionDomainError("evidence observation is outside target coverage")
    _text(item["record_id"])
    resource, kind = _text(item["resource"]), _text(item["kind"])
    _canonical_path(resource)
    parse_resource_identity(item["selection"], resource, kind=kind)
    parse_fingerprint(item["fingerprint"], resource, kind=kind)
    comparisons = _list(item["comparisons"])
    if not comparisons or any(
        not isinstance(identity, str)
        or DIGEST_RE.fullmatch(identity) is None
        or identity not in definitions
        or definitions[identity] != entry
        for identity in comparisons
    ):
        raise ReproductionDomainError(
            "evidence-only context has unknown comparison definition"
        )
    if len(set(comparisons)) != len(comparisons):
        raise ReproductionDomainError("duplicate evidence-only comparison definition")


def _validate_paths(value: object) -> None:
    if not isinstance(value, tuple):
        raise ReproductionDomainError(
            "scheduling paths are not an immutable collection"
        )
    for path in value:
        _canonical_path(path)
    if tuple(sorted(set(value))) != value:
        raise ReproductionDomainError("scheduling paths are not canonical/unique")
