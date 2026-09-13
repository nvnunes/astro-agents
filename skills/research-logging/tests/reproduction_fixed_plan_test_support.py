"""Small strict fixtures shared by fixed-plan reproduction surface tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal

from log_commands.context import LogContext
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_jobs import (
    _CurrentSupervisorCallbacks,
    _CurrentSupervisorContext,
)
from log_commands.reproduction_paths import canonical_run_path


def current_supervisor_callbacks(
    calls: list[str],
    *,
    execute: Callable[[_CurrentSupervisorContext], Literal["completed", "stopped"]],
    compare: Callable[[_CurrentSupervisorContext], None],
    publish: Callable[[_CurrentSupervisorContext], None],
) -> _CurrentSupervisorCallbacks:
    """Wrap synthetic current-store transitions with observable stage order."""

    def recorded_execute(
        context: _CurrentSupervisorContext,
    ) -> Literal["completed", "stopped"]:
        calls.append("execute")
        return execute(context)

    def recorded_compare(context: _CurrentSupervisorContext) -> None:
        calls.append("compare")
        compare(context)

    def recorded_publish(context: _CurrentSupervisorContext) -> None:
        calls.append("publish")
        publish(context)

    return _CurrentSupervisorCallbacks(
        recorded_execute, recorded_compare, recorded_publish
    )


def accepted_plan(
    *, executions: tuple[dict[str, object], ...] = ()
) -> ReproductionPlan:
    """Return the smallest closed plan/10 fixture without legacy state fields."""

    return ReproductionPlan(
        "summary.md",
        {"kind": "log", "entry": None},
        False,
        {
            "evaluated_at": "2030-01-01",
            "rules_version": "research-log-mechanical/evidence-baseline-7",
            "validation_id": "fixture-validation-id",
            "validation_result_id": "fixture-validation-result-id",
            "batch_admission": {
                "admitted": [],
                "excluded": [],
                "schema": "research-log-reproduction-batch-admission/2",
            },
        },
        (),
        {
            "schema": "research-log-reproduction-comparison-context/1",
            "comparisons": [],
            "evidence_only": [],
            "materials": [],
            "result_schema": "research-log-reproduction-result/11",
        },
        (),
        executions,
        (),
        (),
    )


def publication_run(root: Path) -> tuple[LogContext, Path]:
    """Create one canonical run directory for result-publisher unit tests."""

    summary = root / "docs" / "study.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Study\n", encoding="utf-8")
    log_root = summary.with_suffix("")
    log_root.mkdir()
    log = LogContext(summary.resolve(), log_root.resolve())
    run_id = "reproduce-20300101t000000z-fixture"
    logical = canonical_run_path("2030-01-01T00:00:00Z", f"reproduce-study-{run_id}")
    run_root = root / logical
    run_root.mkdir(parents=True)
    return log, run_root


def historical_run(root: Path) -> tuple[LogContext, Path]:
    """Persist one exact canonical historical JSON directory for refusal tests."""

    log, run_root = publication_run(root)
    run_root.joinpath("run.json").write_text(
        json.dumps(
            {
                "run_id": "reproduce-20300101t000000z-fixture",
                "schema": "research-log-reproduction-run/7",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return log, run_root
