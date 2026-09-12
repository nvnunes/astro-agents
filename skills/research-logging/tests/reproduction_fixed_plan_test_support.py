"""Small strict fixtures shared by fixed-plan reproduction surface tests."""

from __future__ import annotations

from pathlib import Path

from log_commands.context import LogContext
from log_commands.reproduction_contract import ReproductionPlan
from log_commands.reproduction_jobs import _accepted_record, _canonical


def accepted_plan(
    *, executions: tuple[dict[str, object], ...] = ()
) -> ReproductionPlan:
    """Return the smallest closed plan/9 fixture without legacy state fields."""

    return ReproductionPlan(
        "summary.md",
        {"kind": "log", "entry": None},
        False,
        {
            "evaluated_at": "2030-01-01",
            "rules_version": "research-log-mechanical/evidence-baseline-7",
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
            "result_schema": "research-log-reproduction-result/10",
        },
        (),
        executions,
        (),
        (),
    )


def accepted_run(
    root: Path, *, plan: ReproductionPlan | None = None
) -> tuple[LogContext, Path]:
    """Persist the minimal canonical plan/9 and accepted run/7 pair."""

    summary = root / "docs" / "study.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Study\n", encoding="utf-8")
    log_root = summary.with_suffix("")
    log_root.mkdir()
    log = LogContext(summary.resolve(), log_root.resolve())
    run_root = root / "runs" / "reproduce-20300101t000000z-fixture"
    run_root.mkdir(parents=True)
    plan = accepted_plan() if plan is None else plan
    (run_root / "plan.json").write_text(plan.serialized(), encoding="utf-8")
    record = _accepted_record(
        log,
        plan,
        run_root.name,
        run_root,
        accepted_at="2030-01-01T00:00:00Z",
    )
    (run_root / "run.json").write_text(_canonical(record), encoding="utf-8")
    return log, run_root
