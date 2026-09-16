"""Normal publication assembles accepted work and durable native observations.

No source inventory, graph, currentness scan or execution belongs here. Missing
attempt/comparison facts for runnable work fail closed; nonattempted artifacts
retain their precise producer or accepted boundary reason. Reused comparisons
are exactly those frozen at acceptance, including their original observation
run/time. Operational failures and stopped jobs never call this endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from .reproduction_domain import (
    ArtifactOutcome,
    ArtifactRef,
    CommandOutcome,
    ExecutionRef,
    NotComparedReason,
    ReproductionDomainError,
    ReproductionProblem,
    WorkSelection,
)
from .reproduction_run import ArtifactResult, CommandResult
from .reproduction_saved_run import SavedRun
from .reproduction_work import ArtifactWork
from .reproduction_work_plan import ReproductionPlan


@dataclass(frozen=True)
class RunCompletion:
    """Normally completed native facts supplied by the durable run owner.

    Commands/artifacts are actual terminal observations, not old-format result
    projections. Problems supplement accepted causes without changing them.
    """

    run_id: str
    accepted_at: str
    finished_at: str
    commands: tuple[CommandResult, ...]
    artifacts: tuple[ArtifactResult, ...]
    problems: tuple[ReproductionProblem, ...] = ()


def complete_saved_run(plan: ReproductionPlan, completion: RunCompletion) -> SavedRun:
    """Freeze one accepted target without replanning or manufacturing a match."""

    commands, artifacts = _completion_observations(plan, completion)
    work = {item.identity: item for item in plan.commands}
    reuse = {result.identity: result for result in plan.reusable_artifact_results}
    causes = {problem.problem_id: problem for problem in plan.problems}
    for problem in completion.problems:
        if problem.problem_id in causes and causes[problem.problem_id] != problem:
            raise ReproductionDomainError("completion changes an accepted diagnosis")
        causes[problem.problem_id] = problem
    for artifact in plan.artifacts:
        if artifact.identity in artifacts:
            continue
        if artifact.identity in reuse:
            artifacts[artifact.identity] = reuse[artifact.identity]
            continue
        producer = (
            work.get(artifact.producer) if artifact.producer is not None else None
        )
        result = (
            commands.get(artifact.producer) if artifact.producer is not None else None
        )
        reason = _not_compared_reason(
            artifact,
            producer.selection if producer else None,
            result.outcome if result else None,
        )
        artifacts[artifact.identity] = ArtifactResult(
            artifact.identity,
            ArtifactOutcome.NOT_COMPARED,
            completion.finished_at,
            completion.run_id,
            None,
            None,
            artifact.baseline,
            None,
            reason,
            problem_ids=artifact.problem_ids
            if reason is NotComparedReason.COMPARISON_FAILED
            else (),
        )
    return SavedRun(
        plan.summary,
        completion.run_id,
        plan.target,
        plan.settings,
        completion.accepted_at,
        completion.finished_at,
        plan.commands,
        plan.artifacts,
        tuple(commands.values()),
        tuple(artifacts.values()),
        tuple(causes.values()),
    )


def _completion_observations(
    plan: ReproductionPlan, completion: RunCompletion
) -> tuple[dict[ExecutionRef, CommandResult], dict[ArtifactRef, ArtifactResult]]:
    """Validate current observations separately from accepted historical reuse."""

    commands = {result.identity: result for result in completion.commands}
    artifacts = {result.identity: result for result in completion.artifacts}
    if len(commands) != len(completion.commands) or len(artifacts) != len(
        completion.artifacts
    ):
        raise ReproductionDomainError("completion has duplicate observation identities")
    if set(commands) - {work.identity for work in plan.commands} or set(artifacts) - {
        work.identity for work in plan.artifacts
    }:
        raise ReproductionDomainError("completion observation is outside accepted work")
    if any(
        result.origin_run_id != completion.run_id for result in completion.artifacts
    ):
        raise ReproductionDomainError(
            "current artifact observation has a different run origin"
        )
    if set(artifacts) & {result.identity for result in plan.reusable_artifact_results}:
        raise ReproductionDomainError(
            "current artifact observation overrides accepted reuse"
        )
    return commands, artifacts


def _not_compared_reason(
    work: ArtifactWork, selection: WorkSelection | None, outcome: CommandOutcome | None
) -> NotComparedReason:
    if selection is WorkSelection.BLOCKED or outcome is CommandOutcome.BLOCKED:
        return NotComparedReason.COMMAND_BLOCKED
    if outcome is CommandOutcome.FAILED:
        return NotComparedReason.COMMAND_FAILED
    if selection is WorkSelection.SKIPPED_BY_POLICY:
        return NotComparedReason.SKIPPED_BY_POLICY
    if selection in {
        WorkSelection.NOT_NEEDED,
        WorkSelection.PREVIOUS_FAILURE,
        WorkSelection.PREVIOUS_BLOCK,
    }:
        return NotComparedReason.COMMAND_NOT_RUN
    if work.producer is None:
        if work.problem_ids:
            return NotComparedReason.COMPARISON_FAILED
        kind = work.boundary.get("kind") if work.boundary is not None else None
        reason = {
            "non_automatic": NotComparedReason.SKIPPED_BY_POLICY,
            "cross_entry": NotComparedReason.COMMAND_NOT_RUN,
            "outside_queue": NotComparedReason.COMMAND_NOT_RUN,
        }.get(kind if isinstance(kind, str) else "")
        if reason is not None:
            return reason
    raise ReproductionDomainError("completed run lacks a runnable artifact observation")
