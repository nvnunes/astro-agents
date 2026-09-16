"""Bounded current-work preview from the same native planner used for acceptance.

No plan cache, run, validation publication or saved result is created. Continuation
reruns preparation and binds source identity, work/history, settings and format;
evaluation timestamps are not cursor authority.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from .model import ActionError
from .reproduction_domain import (
    MAX_INSPECTION_ITEMS,
    ReproductionProblem,
    WorkSelection,
)
from .reproduction_inspection import _cursor, _offset, bounded_response
from .reproduction_summary import plan_selection_counts
from .reproduction_work import CommandWork
from .reproduction_work_plan import ReproductionPlan


def _binding(plan: ReproductionPlan, source_identity: str, format: str) -> str:
    packet = plan.as_dict()
    raw_admission = packet["admission"]
    assert isinstance(raw_admission, Mapping)
    admission = dict(raw_admission)
    admission.pop("evaluated_at")
    admission.pop("validation_snapshot_id")
    packet["admission"] = admission
    packet.update(source_identity=source_identity, format=format)
    return hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()


def _next_command(plan: ReproductionPlan, cursor: str, format: str) -> str:
    argv = [
        "log",
        "reproduce",
        "plan",
        "--path",
        str(Path(plan.summary).with_suffix("")),
    ]
    if plan.target.entry is not None:
        argv += ["--entry", plan.target.entry]
    if plan.settings.include_all:
        argv.append("--include-all")
    if plan.settings.recheck:
        argv.append("--recheck")
    argv += [
        "--jobs",
        str(plan.settings.jobs),
        "--execution-timeout-seconds",
        str(plan.settings.execution_timeout_seconds),
        "--format",
        format,
        "--cursor",
        cursor,
    ]
    return shlex.join(argv)


def _row(
    work: CommandWork, problems: Mapping[str, ReproductionProblem]
) -> dict[str, object]:
    primary = next((problems[key] for key in work.problem_ids), None)
    outputs = [name for name, _kind in work.execution.recipe.outputs]
    return {
        "identity": work.identity.as_dict(),
        "selection": work.selection.value,
        "reason": primary.code if primary else None,
        "explanation": primary.explanation if primary else None,
        "dependencies": [identity.as_dict() for identity in work.dependencies],
        "outputs": outputs[:10],
        "output_count": len(outputs),
        "remaining_outputs": max(0, len(outputs) - 10),
    }


def plan_page(
    plan: ReproductionPlan,
    source_identity: str,
    *,
    cursor: str | None = None,
    format: str = "text",
) -> dict[str, object]:
    """Page run-selected and locally blocked commands; totals cover the full target.

    A changed source, accepted history or selector rejects continuation. No
    execution success or artifact match is predicted. Output previews explicitly
    report omitted names; this response is not an unbounded accepted-plan export.
    """

    if format not in {"text", "json"}:
        raise ActionError("reproduction.selector.invalid", "Use text or json")
    binding = _binding(plan, source_identity, format)
    actionable = tuple(
        work
        for work in plan.commands
        if work.selection in {WorkSelection.RUN, WorkSelection.BLOCKED}
    )
    offset = _offset(cursor, "plan", binding)
    if offset >= len(actionable) and offset != 0:
        raise ActionError(
            "reproduction.cursor.invalid", "Cursor is outside the current plan"
        )
    page = actionable[offset : offset + MAX_INSPECTION_ITEMS]
    end = offset + len(page)
    next_cursor = _cursor("plan", binding, end) if end < len(actionable) else None
    problems = {problem.problem_id: problem for problem in plan.problems}
    return bounded_response(
        {
            "schema": "research-log-reproduction-preview/1",
            "summary": plan.summary,
            "target": plan.target.as_dict(),
            "settings": plan.settings.as_dict(),
            "source_identity": source_identity,
            "commands": dict(plan_selection_counts(plan.commands)),
            "artifacts": {"total": len(plan.artifacts)},
            "matched": len(actionable),
            "returned": len(page),
            "remaining": len(actionable) - end,
            "items": [_row(work, problems) for work in page],
            "cursor": next_cursor,
            "next": _next_command(plan, next_cursor, format) if next_cursor else None,
        }
    )


def render_plan_page(value: Mapping[str, Any]) -> str:
    """Render only already-bounded preview facts, not the raw plan contract."""

    target = value["target"]
    settings = value["settings"]
    assert isinstance(target, Mapping) and isinstance(settings, Mapping)
    counts = value["commands"]
    artifacts = value["artifacts"]
    assert isinstance(counts, Mapping) and isinstance(artifacts, Mapping)
    lines = [
        f"Log: {Path(str(value['summary'])).stem}",
        f"Target: {target['entry'] or 'Log'}",
        f"Policy: {'Recheck' if settings['recheck'] else 'Incremental'}",
        "",
        f"Commands — {counts['total']}",
    ]
    lines += [
        f"  {key.replace('_', ' ').capitalize()} — {count}"
        for key, count in counts.items()
        if key != "total"
    ]
    lines += [
        f"Artifacts — {artifacts['total']}",
        "",
        f"Actionable: {value['matched']}; returned: {value['returned']}; "
        f"remaining: {value['remaining']}.",
    ]
    for row in value["items"]:
        identity = row["identity"]
        lines.append(
            f"{identity['entry']} / {identity['cid']} / {identity['execution_id']} "
            f"— {row['selection']}"
        )
        if row["explanation"]:
            lines.append(f"  {row['explanation']}")
        if row["dependencies"]:
            prerequisites = "; ".join(
                f"{item['entry']} / {item['cid']} / {item['execution_id']}"
                for item in row["dependencies"]
            )
            lines.append(f"  Prerequisites: {prerequisites}")
        remaining = (
            f" ({row['remaining_outputs']} more)" if row["remaining_outputs"] else ""
        )
        lines.append(f"  Outputs: {', '.join(row['outputs']) or 'None'}{remaining}")
    if value["next"]:
        lines += ["", f"Next: {value['next']}"]
    return "\n".join(lines) + "\n"
