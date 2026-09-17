"""Frozen command and artifact work, independent of planner issue projections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping

from research_log_data import (
    DataFile,
    Fingerprint,
    data_file_from_fields,
    parse_fingerprint,
)
from validation.domain import plain_json
from validation.evidence import evidence_record_from_canonical_fields
from validation.pyrun_state import PyrunExecution, parse_pyrun_execution

from .context import parse_entry_directory_name
from .reproduction_domain import (
    PROBLEM_ID_RE,
    ArtifactRef,
    ExecutionRef,
    ReproductionDomainError,
    ReproductionProblem,
    SourceRef,
    WorkSelection,
    _canonical_path,
    _fields,
    _frozen_object,
    _matches,
    _text,
)
from .reproduction_invocation import AcceptedSource

DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def validate_problem_links(
    commands: Mapping[ExecutionRef, CommandWork],
    artifacts: Mapping[ArtifactRef, ArtifactWork],
    problems: Mapping[str, ReproductionProblem],
    links: tuple[tuple[ExecutionRef | ArtifactRef, tuple[str, ...]], ...],
    *,
    summary: str,
) -> None:
    """Keep diagnoses at actual owners; prerequisites/producer links carry effects.

    Shared source causes may be referenced by their actual accepted consumers.
    No current files or independently reconstructed causal graph are consulted.
    """

    referenced: set[str] = set()
    for owner, references in links:
        for reference in references:
            if reference not in problems:
                raise ReproductionDomainError("unknown problem reference")
            problem = problems[reference]
            if isinstance(problem.subject, SourceRef):
                sources = _accepted_sources(owner, commands, artifacts, summary)
                if problem.subject.path not in sources:
                    raise ReproductionDomainError(
                        "problem source is unrelated to its consumer"
                    )
            elif problem.subject != owner and not (
                isinstance(problem.subject, ArtifactRef)
                and isinstance(owner, ExecutionRef)
                and (
                    artifacts[problem.subject].producer == owner
                    or _command_consumes_artifact(
                        commands[owner], artifacts[problem.subject], commands
                    )
                )
            ):
                raise ReproductionDomainError(
                    "problem is attached to an unrelated owner"
                )
            referenced.add(reference)
    if referenced != set(problems):
        raise ReproductionDomainError("unreferenced reproduction problem")


def _accepted_sources(
    owner: ExecutionRef | ArtifactRef,
    commands: Mapping[ExecutionRef, CommandWork],
    artifacts: Mapping[ArtifactRef, ArtifactWork],
    summary: str,
) -> set[str]:
    sources = {summary}
    if isinstance(owner, ArtifactRef):
        artifact = artifacts[owner]
        sources.add(artifact.retained_path)
        sources.update(_strings(artifact.boundary))
        for record in artifact.evidence_records:
            sources.update(_strings(record))
        if artifact.producer is None:
            return sources
        owner = artifact.producer
    work = commands[owner]
    sources.add((Path(work.project_root) / summary).as_posix())
    root = Path(work.entry_root)
    sources.update(
        str(root / name) for name in ("pyrun.json", "data.json", f"{owner.entry}.md")
    )
    for path in (work.execution.recipe.script,):
        sources.add(path)
        target = (
            Path(work.project_root) / path.removeprefix("<project>/")
            if path.startswith("<project>/")
            else root.parent.parent / path.removeprefix("<log>/")
            if path.startswith("<log>/")
            else root / path
        )
        sources.add(target.as_posix())
    if work.data_declaration is not None:
        declaration = _fields(
            plain_json(work.data_declaration), None, "data declaration"
        )
        for resource in _list(declaration["inputs"]):
            sources.add(
                _text(_fields(resource, None, "input resource")["canonical_target"])
            )
    return sources


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return set().union(*(_strings(item) for item in value.values()))
    if isinstance(value, tuple):
        return set().union(*(_strings(item) for item in value))
    return set()


def _command_consumes_artifact(
    command: CommandWork,
    artifact: ArtifactWork,
    commands: Mapping[ExecutionRef, CommandWork],
) -> bool:
    """Return whether accepted command input binding consumes this artifact.

    The relation is derived only from frozen plan work. Directory outputs own
    accepted inputs beneath their retained root; file-like outputs require an
    exact canonical target.
    """

    if artifact.producer is None or artifact.output is None or command.data is None:
        return False
    producer = commands[artifact.producer]
    kind = dict(producer.execution.recipe.outputs).get(artifact.output)
    if kind is None:
        raise ReproductionDomainError(
            "artifact output is not declared by its producer"
        )
    retained = PurePosixPath(artifact.retained_path)
    for name in command.execution.recipe.inputs:
        resource = command.data.by_name.get(name)
        if resource is None:
            raise ReproductionDomainError("command input has no accepted declaration")
        target = PurePosixPath(resource.canonical_target)
        if target == retained or (
            kind == "directory" and retained in target.parents
        ):
            return True
    return False


@dataclass(frozen=True)
class CommandWork:
    """One accepted expanded execution and its preparation decision.

    The existing execution-state grammar owns the recipe, observed inputs/code,
    need flag and automatic/exclusive policy. Do not copy those into parallel
    command flags. Dependency and problem references describe effects; problems
    themselves live once in the containing plan/run.
    """

    identity: ExecutionRef
    execution: PyrunExecution
    entry_root: str
    project_root: str
    data_declaration: Mapping[str, object] | None
    selection: WorkSelection
    source_digest: str | None
    dependencies: tuple[ExecutionRef, ...] = ()
    problem_ids: tuple[str, ...] = ()
    accepted_source: AcceptedSource | None = None
    _data: DataFile | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionRef):
            raise ReproductionDomainError("command work has no execution identity")
        if not isinstance(self.execution, PyrunExecution):
            raise ReproductionDomainError("command work has no typed execution")
        _absolute_path(self.entry_root)
        _absolute_path(self.project_root)
        self._validate_selection()
        self._validate_recipe()
        self._validate_relations()

    @property
    def data(self) -> DataFile | None:
        """Return the frozen declaration's typed decode, never current data.json.

        Construction validates/decodes it once. Runtime consumers share that
        same immutable view rather than reparsing declarations for each output.
        The canonical declaration remains the sole serialized data owner.
        """

        return self._data

    def _validate_selection(self) -> None:
        if not isinstance(self.selection, WorkSelection):
            raise ReproductionDomainError("command work has invalid selection")
        if self.source_digest is not None:
            _matches(self.source_digest, DIGEST_RE, "source closure digest")
        if self.selection not in {
            WorkSelection.NOT_NEEDED,
            WorkSelection.SKIPPED_BY_POLICY,
        }:
            if self.source_digest is None:
                raise ReproductionDomainError(
                    "selected/previous work needs a source digest"
                )
        if self.selection is WorkSelection.RUN and self.accepted_source is None:
            raise ReproductionDomainError("runnable work has no accepted source")
        if self.accepted_source is not None and not isinstance(
            self.accepted_source, AcceptedSource
        ):
            raise ReproductionDomainError("command work has invalid accepted source")

    def _validate_recipe(self) -> None:
        validated = parse_pyrun_execution(
            self.execution.as_dict(),
            subject=self.identity.execution_id,
            entry_root=Path(self.entry_root),
            project_root=Path(self.project_root),
            accepted=True,
        )
        object.__setattr__(self, "execution", validated)
        object.__setattr__(self, "_data", None)
        if self.data_declaration is not None:
            declaration = _frozen_object(self.data_declaration)
            data = data_file_from_fields(
                Path(self.entry_root) / "data.json",
                entry_root=Path(self.entry_root),
                fields=_fields(
                    plain_json(declaration), None, "resolved data declaration"
                ),
            )
            object.__setattr__(self, "data_declaration", declaration)
            object.__setattr__(self, "_data", data)

    def _validate_relations(self) -> None:
        if any(not isinstance(item, ExecutionRef) for item in self.dependencies):
            raise ReproductionDomainError("command dependency has invalid identity")
        if self.identity in self.dependencies and self.selection not in {
            WorkSelection.BLOCKED,
            WorkSelection.PREVIOUS_BLOCK,
        }:
            raise ReproductionDomainError("command cannot depend on itself")
        object.__setattr__(self, "dependencies", tuple(sorted(set(self.dependencies))))
        object.__setattr__(self, "problem_ids", problem_references(self.problem_ids))
        if (
            self.selection in {WorkSelection.BLOCKED, WorkSelection.PREVIOUS_BLOCK}
            and not self.problem_ids
            and not set(self.dependencies) - {self.identity}
        ):
            raise ReproductionDomainError("blocked work has no cause or prerequisite")
        if self.selection is WorkSelection.PREVIOUS_FAILURE and not self.problem_ids:
            raise ReproductionDomainError("previous failure has no retained diagnosis")

    def as_dict(self) -> dict[str, object]:
        """Return closed accepted metadata, without cases/failure inventories."""

        return {
            "identity": self.identity.as_dict(),
            "execution": self.execution.as_dict(),
            "entry_root": self.entry_root,
            "project_root": self.project_root,
            "data_declaration": plain_json(self.data_declaration),
            "selection": self.selection.value,
            "source_digest": self.source_digest,
            "dependencies": [item.as_dict() for item in self.dependencies],
            "problem_ids": list(self.problem_ids),
            "accepted_source": (
                self.accepted_source.as_dict()
                if self.accepted_source is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> CommandWork:
        """Decode accepted work using existing recipe/data grammars, without reads."""

        item = _fields(
            value,
            {
                "identity",
                "execution",
                "entry_root",
                "project_root",
                "data_declaration",
                "selection",
                "source_digest",
                "dependencies",
                "problem_ids",
                "accepted_source",
            },
            "command work",
        )
        identity = ExecutionRef.from_dict(item["identity"])
        entry_root, project_root = (
            _text(item["entry_root"]),
            _text(item["project_root"]),
        )
        execution = parse_pyrun_execution(
            item["execution"],
            subject=identity.execution_id,
            entry_root=Path(entry_root),
            project_root=Path(project_root),
            accepted=True,
        )
        return cls(
            identity,
            execution,
            entry_root,
            project_root,
            None
            if item["data_declaration"] is None
            else _fields(item["data_declaration"], None, "data declaration"),
            WorkSelection(item["selection"]),
            None if item["source_digest"] is None else _text(item["source_digest"]),
            tuple(ExecutionRef.from_dict(raw) for raw in _list(item["dependencies"])),
            tuple(_text(raw) for raw in _list(item["problem_ids"])),
            (
                None
                if item["accepted_source"] is None
                else AcceptedSource.from_dict(item["accepted_source"])
            ),
        )


@dataclass(frozen=True)
class ArtifactWork:
    """One existing reachable artifact case, not every execution output.

    Evidence definitions retain their accepted identity and original authored
    fields. Their existing decoder/comparator continues to own profile-specific
    interpretation; this record does not invent a parallel evidence grammar.
    Ownerless scope/boundary cases remain artifacts without invented commands.
    """

    identity: ArtifactRef
    producer: ExecutionRef | None
    retained_path: str
    baseline: Fingerprint | None
    output: str | None = None
    definition_identity: str | None = None
    evidence_records: tuple[Mapping[str, object], ...] = ()
    boundary: Mapping[str, object] | None = None
    problem_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ArtifactRef):
            raise ReproductionDomainError("artifact work has invalid identity")
        if self.producer is not None and not isinstance(self.producer, ExecutionRef):
            raise ReproductionDomainError("artifact producer has invalid identity")
        if (self.producer is None) != (self.output is None):
            raise ReproductionDomainError(
                "artifact has a partial producer/output binding"
            )
        if self.output is not None:
            _canonical_path(self.output)
        _canonical_path(self.retained_path)
        if self.baseline is not None:
            if not isinstance(self.baseline, Fingerprint):
                raise ReproductionDomainError("artifact baseline is not a fingerprint")
            parse_fingerprint(self.baseline.as_dict(), "artifact baseline")
        if self.definition_identity is not None:
            _matches(
                self.definition_identity, DIGEST_RE, "comparison definition identity"
            )
        elif self.evidence_records:
            raise ReproductionDomainError(
                "evidence records have no accepted definition"
            )
        object.__setattr__(
            self,
            "evidence_records",
            tuple(self._evidence_record(item) for item in self.evidence_records),
        )
        if self.boundary is not None:
            object.__setattr__(self, "boundary", _frozen_object(self.boundary))
        object.__setattr__(self, "problem_ids", problem_references(self.problem_ids))

    def _evidence_record(self, value: Mapping[str, object]) -> Mapping[str, object]:
        entry = (
            self.producer.entry if self.producer is not None else self.identity.entry
        )
        frozen = _frozen_object(value)
        fields = _fields(plain_json(frozen), None, "accepted evidence record")
        parent = PurePosixPath(_text(fields.get("document"))).parent
        directory = parse_entry_directory_name(parent.name)
        if (
            parent.parts[:1] != ("entries",)
            or len(parent.parts) != 2
            or directory is None
            or directory.id != entry
        ):
            raise ReproductionDomainError(
                "evidence document is outside its owning entry"
            )
        record = evidence_record_from_canonical_fields(
            subject="accepted artifact evidence",
            entry_relative=parent.as_posix(),
            fields=fields,
        )
        return _frozen_object(record.as_dict())

    def as_dict(self) -> dict[str, object]:
        """Serialize artifact-owned definition/boundary facts and cause references."""

        return {
            "identity": self.identity.as_dict(),
            "producer": self.producer.as_dict() if self.producer is not None else None,
            "retained_path": self.retained_path,
            "baseline": self.baseline.as_dict() if self.baseline is not None else None,
            "output": self.output,
            "definition_identity": self.definition_identity,
            "evidence_records": [plain_json(item) for item in self.evidence_records],
            "boundary": plain_json(self.boundary),
            "problem_ids": list(self.problem_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> ArtifactWork:
        """Decode the closed artifact envelope; profile fields retain their owner."""

        item = _fields(
            value,
            {
                "identity",
                "producer",
                "retained_path",
                "baseline",
                "output",
                "definition_identity",
                "evidence_records",
                "boundary",
                "problem_ids",
            },
            "artifact work",
        )
        return cls(
            ArtifactRef.from_dict(item["identity"]),
            None
            if item["producer"] is None
            else ExecutionRef.from_dict(item["producer"]),
            _text(item["retained_path"]),
            None
            if item["baseline"] is None
            else parse_fingerprint(item["baseline"], "artifact baseline"),
            None if item["output"] is None else _text(item["output"]),
            None
            if item["definition_identity"] is None
            else _text(item["definition_identity"]),
            tuple(
                _fields(raw, None, "evidence record")
                for raw in _list(item["evidence_records"])
            ),
            None
            if item["boundary"] is None
            else _fields(item["boundary"], None, "artifact boundary"),
            tuple(_text(raw) for raw in _list(item["problem_ids"])),
        )


def validate_producer(
    artifact: ArtifactWork, commands: Mapping[ExecutionRef, CommandWork]
) -> None:
    """Validate the frozen declared-output link without rebuilding ownership."""

    if artifact.producer is None:
        return
    producer = commands.get(artifact.producer)
    if producer is None:
        raise ReproductionDomainError("artifact producer is outside accepted inventory")
    if artifact.output not in dict(producer.execution.recipe.outputs):
        raise ReproductionDomainError("artifact output is not declared by its producer")
    if any(
        PurePosixPath(_text(record["document"])).parent.name
        != Path(producer.entry_root).name
        for record in artifact.evidence_records
    ):
        raise ReproductionDomainError(
            "evidence document disagrees with its frozen producer root"
        )


def problem_references(value: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve deterministic cause precedence while rejecting duplicate links."""

    if not isinstance(value, tuple):
        raise ReproductionDomainError("problem references must be a tuple")
    for identity in value:
        _matches(identity, PROBLEM_ID_RE, "problem reference")
    if len(set(value)) != len(value):
        raise ReproductionDomainError("duplicate problem reference")
    return value


def _absolute_path(value: object) -> None:
    _canonical_path(value)
    if not Path(_text(value)).is_absolute():
        raise ReproductionDomainError("recorded root must be absolute")


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ReproductionDomainError("recorded collection must be a list")
    return value
