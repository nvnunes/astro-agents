"""Bounded read-only projections of published reproduction state."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Mapping, cast

from research_log_paths import REPRODUCTION_RESULTS
from validation.discovery import discover_summaries
from validation.human_projection import load_report_context

from .context import LogContext, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_accounting import CommandAccountingError, project_command_selection
from .reproduction_contract import ReproductionPlan
from .reproduction_planner import (
    ReproductionCommandInventory,
    project_reproduction_command_inventory,
    project_reproduction_state,
)
from .reproduction_results import (
    ArtifactCurrentness,
    ReproductionResultError,
    ReproductionResults,
    artifact_summary_counts,
    command_summary_counts,
    compose_reproduction_reconciliation_summary,
    compose_reproduction_report,
    compose_reproduction_summary,
    load_reproduction_results,
    load_results_or_empty,
    project_current_results,
    query_artifacts,
)

ARTIFACT_LIST_SCHEMA = "research-log-reproduction-artifact-list/1"
ARTIFACT_SHOW_SCHEMA = "research-log-reproduction-artifact/1"
SUMMARY_SCHEMA = "research-log-reproduction-summary/2"
ROOT_SUMMARY_SCHEMA = "research-log-reproduction-root-summary/2"


def reproduction_report(log: LogContext, *, entry: str | None) -> str:
    """Return the centralized current human report projection."""

    results, currentness = _current(log)
    return compose_reproduction_report(
        results,
        context=load_report_context(log.summary),
        currentness=currentness,
        entry=entry,
        folder_links_from=Path.cwd(),
    )


def reproduction_summary(log: LogContext) -> dict[str, object]:
    """Return the canonical compact summary for one maintained log."""

    results, _currentness = _current(log)
    latest = next((run for run in results.runs if run.status == "complete"), None)
    return {
        "artifacts": (
            dict(artifact_summary_counts(results.artifacts))
            if latest is not None
            else None
        ),
        "commands": (
            dict(command_summary_counts(latest.command_outcomes))
            if latest is not None and latest.command_outcomes is not None
            else None
        ),
        "generated_at": results.updated_at,
        "run_id": latest.run_id if latest is not None else None,
        "schema": SUMMARY_SCHEMA,
        "status": "complete" if latest is not None else "not_run",
        "summary": results.summary,
    }


def reproduction_summary_text(log: LogContext) -> str:
    """Return the canonical compact human summary for one maintained log."""

    results, _currentness = _current(log)
    return compose_reproduction_summary(results)


def reproduction_reconciliation_text(
    log: LogContext,
    plan: ReproductionPlan,
    *,
    generated_at: str,
) -> str:
    """Return the current terminal summary for a plan with no runnable work."""

    if plan.executions:
        raise ActionError(
            "reproduction.reconciliation.invalid",
            "a no-work reconciliation cannot contain runnable executions",
        )
    project = resolve_project_root(log.root)
    try:
        summary = log.summary.resolve().relative_to(project).as_posix()
    except ValueError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
    results = load_results_or_empty(
        log.root / REPRODUCTION_RESULTS,
        summary=summary,
        updated_at=generated_at,
    )
    state = project_reproduction_state(log)
    projected, _currentness = project_current_results(results, state)
    outcomes = _no_work_command_outcomes(
        plan,
        project_reproduction_command_inventory(log, plan.target),
    )
    return compose_reproduction_reconciliation_summary(
        projected,
        outcomes,
        generated_at=generated_at,
    )


def _no_work_command_outcomes(
    plan: ReproductionPlan,
    inventory: ReproductionCommandInventory,
) -> Mapping[str, int]:
    """Reconcile current plan selections when no command will be launched."""

    try:
        selection = project_command_selection(plan, inventory)
    except CommandAccountingError as error:
        raise ActionError("reproduction.reconciliation.invalid", str(error)) from error
    if selection.run_keys:
        raise ActionError(
            "reproduction.reconciliation.invalid",
            "command selection does not match the no-work plan",
        )
    return {
        "blocked": selection.blocked,
        "failed": 0,
        "not_automatic": selection.not_automatic,
        "reused": selection.reused,
        "succeeded": 0,
        "total": selection.total,
    }


def root_reproduction_summary(root: Path) -> dict[str, object]:
    """Return one compact cross-log summary below a project root."""

    try:
        discovered = discover_summaries(root)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError(
            "reproduction.summary.discovery_failed", str(error)
        ) from error
    project_root = Path(cast(str, discovered["root"]))
    rows: list[dict[str, object]] = []
    for value in cast(Sequence[str], discovered["summaries"]):
        summary = Path(value)
        name = summary.relative_to(project_root).with_suffix("").as_posix()
        try:
            row = reproduction_summary(resolve_log(summary.with_suffix("")))
            rows.append({"log": name, **row})
        except ActionError as error:
            if error.code == "reproduction.results.missing":
                rows.append(
                    {
                        "artifacts": None,
                        "commands": None,
                        "generated_at": None,
                        "log": name,
                        "run_id": None,
                        "schema": SUMMARY_SCHEMA,
                        "status": "not_run",
                        "summary": summary.relative_to(project_root).as_posix(),
                    }
                )
                continue
            rows.append(_unavailable_row(summary, project_root, name, error))
        except (OSError, UnicodeError, ValueError) as error:
            rows.append(_unavailable_row(summary, project_root, name, error))

    coverage = {
        "complete": sum(row["status"] == "complete" for row in rows),
        "not_run": sum(row["status"] == "not_run" for row in rows),
        "total": len(rows),
        "unavailable": sum(row["status"] == "unavailable" for row in rows),
    }
    command_rows = [
        _flat_commands(cast(Mapping[str, object], row["commands"]))
        for row in rows
        if row["commands"] is not None
    ]
    artifact_rows = [
        _flat_artifacts(cast(Mapping[str, object], row["artifacts"]))
        for row in rows
        if row["artifacts"] is not None
    ]
    return {
        "coverage": coverage,
        "logs": rows,
        "root": project_root.as_posix(),
        "schema": ROOT_SUMMARY_SCHEMA,
        "totals": {
            "artifacts": _sum_counts(
                artifact_rows, ("matched", "not_matched", "not_compared", "total")
            ),
            "commands": _sum_counts(
                command_rows,
                (
                    "not_automatic",
                    "reused",
                    "succeeded",
                    "failed",
                    "blocked",
                    "total",
                ),
            ),
        },
    }


def compose_root_reproduction_summary(summary: Mapping[str, object]) -> str:
    """Render a cross-log summary without merging command and artifact units."""

    coverage = cast(Mapping[str, int], summary["coverage"])
    rows = cast(Sequence[Mapping[str, object]], summary["logs"])
    totals = cast(Mapping[str, Mapping[str, int] | None], summary["totals"])
    lines = [
        "# Reproduction Summary",
        "",
        "Coverage: "
        f"{coverage['complete']} with a completed run, "
        f"{coverage['not_run']} not yet reproduced, {coverage['unavailable']} "
        f"unavailable ({coverage['total']} logs total).",
        "",
        "A command can produce more than one artifact, so the totals are not "
        "expected to match.",
        "A succeeded command ran to completion; artifact matching is shown separately.",
        "",
        "## Commands — Latest Completed Run",
        "",
        "| Log | Not automatic | Reused | Succeeded | Failed | Blocked | Total |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(_root_command_row(row))
    lines.append(_root_total_row("Accounted logs", totals["commands"], 6))
    lines.extend(
        (
            "",
            "## Artifacts — Current State",
            "",
            "| Log | Matched | Not matched | Not compared | Total |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for row in rows:
        lines.append(_root_artifact_row(row))
    lines.append(_root_total_row("Completed logs", totals["artifacts"], 4))
    return "\n".join(lines).rstrip() + "\n"


def _sum_counts(
    rows: Sequence[Mapping[str, int]], names: Sequence[str]
) -> dict[str, int] | None:
    if not rows:
        return None
    return {name: sum(row[name] for row in rows) for name in names}


def _unavailable_row(
    summary: Path, project_root: Path, name: str, error: Exception
) -> dict[str, object]:
    return {
        "artifacts": None,
        "commands": None,
        "error": str(error),
        "generated_at": None,
        "log": name,
        "run_id": None,
        "schema": SUMMARY_SCHEMA,
        "status": "unavailable",
        "summary": summary.relative_to(project_root).as_posix(),
    }


def _root_command_row(row: Mapping[str, object]) -> str:
    summary = cast(Mapping[str, object] | None, row["commands"])
    commands = None if summary is None else _flat_commands(summary)
    if commands is None:
        return f"| {_root_label(row)} | — | — | — | — | — | — |"
    return (
        f"| {_root_label(row)} | "
        + " | ".join(
            str(commands[name])
            for name in (
                "not_automatic",
                "reused",
                "succeeded",
                "failed",
                "blocked",
                "total",
            )
        )
        + " |"
    )


def _root_artifact_row(row: Mapping[str, object]) -> str:
    summary = cast(Mapping[str, object] | None, row["artifacts"])
    artifacts = None if summary is None else _flat_artifacts(summary)
    if artifacts is None:
        return f"| {_root_label(row)} | — | — | — | — |"
    return (
        f"| {_root_label(row)} | "
        + " | ".join(
            str(artifacts[name])
            for name in ("matched", "not_matched", "not_compared", "total")
        )
        + " |"
    )


def _flat_commands(summary: Mapping[str, object]) -> dict[str, int]:
    selected = cast(Mapping[str, int], summary["selected"])
    return {
        "not_automatic": cast(int, summary["skipped_by_policy"]),
        "reused": cast(int, summary["reused"]),
        "succeeded": selected["succeeded"],
        "failed": selected["failed"],
        "blocked": selected["blocked"],
        "total": cast(int, summary["total"]),
    }


def _flat_artifacts(summary: Mapping[str, object]) -> dict[str, int]:
    not_compared = cast(Mapping[str, int], summary["not_compared"])
    return {
        "matched": cast(int, summary["matched"]),
        "not_matched": cast(int, summary["not_matched"]),
        "not_compared": not_compared["total"],
        "total": cast(int, summary["total"]),
    }


def _root_total_row(label: str, counts: Mapping[str, int] | None, columns: int) -> str:
    if counts is None:
        return f"| **{label}** | " + " | ".join("—" for _ in range(columns)) + " |"
    return (
        f"| **{label}** | " + " | ".join(str(value) for value in counts.values()) + " |"
    )


def _root_label(row: Mapping[str, object]) -> str:
    value = str(row["log"]).replace("`", "\\`").replace("|", "\\|")
    suffix = {
        "complete": "",
        "not_run": " — not yet reproduced",
        "unavailable": " — unavailable",
    }[str(row["status"])]
    return f"`{value}`{suffix}"


def list_reproduction_artifacts(
    log: LogContext,
    *,
    entry: str | None,
    outcome: str | None,
    artifact: str | None,
) -> dict[str, object]:
    """Return at most 50 exact current artifact records."""

    results, currentness = _current(log)
    query = query_artifacts(
        results,
        currentness=currentness,
        entry=entry,
        outcome=outcome,
        artifact=artifact,
    )
    return {
        **query.as_dict(),
        "filters": {"artifact": artifact, "entry": entry, "outcome": outcome},
        "schema": ARTIFACT_LIST_SCHEMA,
        "summary": results.summary,
    }


def show_reproduction_artifact(
    log: LogContext, *, entry: str, artifact: str
) -> dict[str, object]:
    """Return one complete exact current artifact record."""

    results, currentness = _current(log)
    query = query_artifacts(
        results, currentness=currentness, entry=entry, artifact=artifact
    )
    if query.matched == 0:
        raise ActionError(
            "reproduction.artifact.unknown",
            f"published reproduction contains no {entry}:{artifact}",
        )
    if query.matched != 1:
        raise ActionError(
            "reproduction.artifact.ambiguous",
            f"published reproduction contains ambiguous {entry}:{artifact}",
        )
    return {
        "artifact": dict(query.records[0]),
        "schema": ARTIFACT_SHOW_SCHEMA,
        "summary": results.summary,
    }


def _current(
    log: LogContext,
) -> tuple[ReproductionResults, Mapping[tuple[str, str], ArtifactCurrentness]]:
    path = log.root / REPRODUCTION_RESULTS
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.results.missing",
            f"no cached reproduction result; rerun reproduction: {path}",
        )
    try:
        results = load_reproduction_results(path)
        expected = resolve_project_root(log.root) / results.summary
        if expected.resolve() != log.summary.resolve():
            raise ReproductionResultError("result summary identity changed")
        state = project_reproduction_state(log)
        return project_current_results(results, state)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
