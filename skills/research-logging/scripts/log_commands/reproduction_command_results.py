"""Canonical terminal research facts from the invocation that just completed.

Runtime supplies its actual invocation, observed outputs and completion time
after materialization. No second checkpoint outcome is accepted or reconciled.
The job transaction owns durability and permit release.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Mapping

from research_log_data import DataContractError, parse_fingerprint
from validation.domain import SourceLocation

from .reproduction_domain import (
    PROBLEM_CODES,
    CommandOutcome,
    ExecutionRef,
    ProblemStage,
    ReproductionDomainError,
    ReproductionProblem,
)
from .reproduction_run import CommandResult
from .reproduction_work import CommandWork

if TYPE_CHECKING:
    from .reproduction_execution import _CurrentProcessResult


def completed_command_observation(
    work: CommandWork,
    invocation: _CurrentProcessResult,
    finished_at: str,
    observations: tuple[Mapping[str, object], ...],
) -> tuple[CommandResult | None, tuple[ReproductionProblem, ...]]:
    """Derive the result from actual execution, never a checkpoint projection.

    Launch exceptions retain the immediately pending attempt time, not an
    invented child-launch time. Missing outputs retain exact declared members.
    Stopped attempts remain lifecycle state and produce no research result.
    """

    from .reproduction_execution import _attempt_state

    prepared = invocation.prepared
    if work.identity != ExecutionRef(
        prepared.entry, prepared.cid, prepared.execution_id
    ):
        raise ReproductionDomainError("invocation has a different command owner")
    if invocation.outcome.stopped:
        return None, ()
    outputs = _terminal_outputs(work, observations)
    state, code, message = _attempt_state(
        invocation.outcome, len(outputs), len(work.execution.recipe.outputs)
    )
    problems = (
        (_execution_problem(work, invocation, outputs, code, message),)
        if state == "failed"
        else ()
    )
    return CommandResult(
        work.identity,
        CommandOutcome(state),
        invocation.started_at,
        finished_at,
        invocation.argv,
        invocation.cwd,
        invocation.stdout,
        invocation.stderr,
        outputs,
        tuple(problem.problem_id for problem in problems),
    ), problems


def _terminal_outputs(
    work: CommandWork, observations: tuple[Mapping[str, object], ...]
) -> dict[str, object]:
    declared = dict(work.execution.recipe.outputs)
    outputs = {}
    for item in observations:
        if not isinstance(item, Mapping) or set(item) != {"artifact", "fingerprint"}:
            raise ReproductionDomainError("terminal output record is malformed")
        artifact = item.get("artifact")
        if (
            not isinstance(artifact, str)
            or artifact not in declared
            or artifact in outputs
        ):
            raise ReproductionDomainError("terminal output is undeclared or duplicated")
        try:
            parse_fingerprint(
                item.get("fingerprint"), artifact, kind=declared[artifact]
            )
        except DataContractError as error:
            raise ReproductionDomainError(
                "terminal output fingerprint is invalid"
            ) from error
        outputs[artifact] = item["fingerprint"]
    return outputs


def _execution_problem(
    work: CommandWork,
    invocation: _CurrentProcessResult,
    outputs: Mapping[str, object],
    original_code: str | None,
    message: str | None,
) -> ReproductionProblem:
    assert original_code is not None
    outcome = invocation.outcome
    stage = outcome.failure_stage or ProblemStage.EXECUTE
    code = (
        original_code
        if original_code in PROBLEM_CODES
        else "output_materialization_failed"
        if stage is ProblemStage.MATERIALIZE
        else "execution_exception"
    )
    observed: dict[str, object] = {
        "original_code": original_code,
        "returncode": outcome.returncode,
        "error_type": outcome.error_type,
    }
    if original_code == "output_missing":
        observed["missing_outputs"] = sorted(
            {name for name, _ in work.execution.recipe.outputs} - set(outputs)
        )
    if outcome.capture_failures:
        observed["capture_failures"] = [
            {
                "name": capture.name,
                "required": capture.required,
                "error_type": type(capture.error).__name__,
                "message": str(capture.error),
            }
            for capture in outcome.capture_failures
        ]
    script = PurePosixPath(work.entry_root) / work.execution.recipe.script
    return ReproductionProblem(
        work.identity,
        code,
        stage,
        message or "Execution failed.",
        observed,
        (SourceLocation(str(script)),),
    )


def blocked_command_observation(
    identity: ExecutionRef, prerequisites: tuple[ExecutionRef, ...]
) -> CommandResult:
    """Retain failed prerequisites without fabricating a dependent attempt."""

    return CommandResult(
        identity,
        CommandOutcome.BLOCKED,
        None,
        None,
        (),
        None,
        None,
        None,
        {},
        blocked_by=prerequisites,
    )
