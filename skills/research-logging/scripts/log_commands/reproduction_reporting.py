"""Read-only human results for one explicitly selected execution."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence, cast

from .context import LogContext, resolve_project_root
from .model import ActionError
from .reproduction_comparison import load_recorded_comparisons
from .reproduction_contract import ReproductionPlan, is_repair_verification
from .reproduction_execution import open_existing_workspace


def reproduction_execution_report(log: LogContext, run_id: str) -> str:
    """Report one retained attempt without consulting cumulative log results."""

    from .reproduction_jobs import _find_run, _load_run, _plan_from_record

    root = _find_run(log, run_id)
    record = _load_run(root / "run.json")
    plan = _plan_from_record(record)
    if plan.target.get("kind") != "execution":
        raise ActionError(
            "reproduction.report.target_invalid",
            "--run-id requires a single-execution run; use status for broader runs",
        )
    state = cast(Mapping[str, object], record["state"])
    checkpoints = cast(Sequence[Mapping[str, object]], record["checkpoints"])
    checkpoint = next(
        (
            item
            for item in checkpoints
            if item["execution_id"] == plan.target["execution_id"]
        ),
        None,
    )
    commands = cast(Sequence[Mapping[str, object]], plan.source_snapshot["commands"])
    command = commands[0]
    selection = str(command["selection"])
    outcome = (
        checkpoint["state"]
        if checkpoint
        else {"run": "pending", "policy": "skipped by policy"}.get(selection, selection)
    )
    lines = [
        f"Execution {plan.target['entry']} {plan.target['execution_id']}: {outcome}",
        f"Run {run_id}: {state.get('status') or state['phase']}",
    ]
    if is_repair_verification(plan):
        lines.append("Mode: repaired-source verification")
    if record.get("attempt") is not None:
        lines.append(f"Attempt: {record['attempt']}")
    lines.extend(_output_lines(log, root, run_id, plan, command))
    for failure in plan.failures:
        lines.append(f"Blocked {failure['artifact']}: {failure['reason']}")
        lines.extend(
            str(detail) for detail in cast(Sequence[str], failure["dependencies"])
        )
    for diagnostic in (
        state.get("operational_failure"),
        state.get("latest_execution_diagnostic"),
        checkpoint.get("failure") if checkpoint else None,
    ):
        if isinstance(diagnostic, Mapping):
            detail = f"Failure: {diagnostic['code']}: {diagnostic['message']}"
            if detail not in lines:
                lines.append(detail)
    return "\n".join(lines) + "\n"


def _output_lines(
    log: LogContext,
    root: Path,
    run_id: str,
    plan: ReproductionPlan,
    command: Mapping[str, object],
) -> list[str]:
    """List every declared output using only this attempt's comparisons."""

    lines: list[str] = []
    comparisons: dict[str, tuple[str, str | None]] = {}
    if (root / "staging.json").exists() or (root / "staging.json").is_symlink():
        workspace = open_existing_workspace(
            resolve_project_root(log.root), root, run_id
        )
        for result in load_recorded_comparisons(plan, workspace, verify_outputs=False):
            if (result.entry, result.execution_id) == (
                plan.target["entry"],
                plan.target["execution_id"],
            ):
                comparisons.update(
                    (item.artifact, (item.outcome, item.reason))
                    for item in result.artifacts
                )
    cases = {str(case["artifact"]): case for case in plan.cases}
    recipe = cast(Mapping[str, object], command["recipe"])
    for artifact in sorted(cast(Mapping[str, str], recipe["outputs"])):
        case = cases.get(artifact, {})
        comparison_outcome, reason = comparisons.get(
            artifact,
            ("not compared", cast(str | None, case.get("reason"))),
        )
        lines.append(
            f"- {artifact}: {comparison_outcome}" + (f" ({reason})" if reason else "")
        )
    return lines
