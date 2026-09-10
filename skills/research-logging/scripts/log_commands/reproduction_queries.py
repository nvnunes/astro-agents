"""Bounded read-only projections of published reproduction state."""

from __future__ import annotations

import shlex
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
    project_reproduction_command_details,
    project_reproduction_command_inventory,
    project_reproduction_state,
)
from .reproduction_results import (
    ArtifactCurrentness,
    ReproductionResultError,
    ReproductionResults,
    RunResult,
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
COMMAND_LIST_SCHEMA = "research-log-reproduction-command-list/1"
COMMAND_SHOW_SCHEMA = "research-log-reproduction-command/1"
SUMMARY_SCHEMA = "research-log-reproduction-summary/4"
ROOT_SUMMARY_SCHEMA = "research-log-reproduction-root-summary/4"
COMMAND_BUCKETS = (
    "reproduction-not-retried",
    "skipped-by-policy",
    "succeeded",
    "failed",
    "blocked",
)
COMMAND_REASONS = (
    "reproduction_not_needed",
    "unchanged_failed",
    "unchanged_blocked",
    "not_automatic",
    "succeeded",
    "failed",
    "blocked",
)


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
            dict(artifact_summary_counts(results.artifacts, results.commands))
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
        "reproduction_not_needed": selection.reproduction_not_needed,
        "unchanged_failed": selection.unchanged_failed,
        "unchanged_blocked": selection.unchanged_blocked,
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
                    "reproduction_not_retried",
                    "not_automatic",
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
        "| Log | Reproduction not retried | Skipped by policy | Succeeded | "
        "Failed | Blocked | Total |",
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
                "reproduction_not_retried",
                "not_automatic",
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
        "reproduction_not_retried": cast(
            int, summary["reproduction_not_retried"]
        ),
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


def list_reproduction_commands(
    log: LogContext,
    *,
    bucket: str | None,
    entry: str | None,
    reason: str | None,
    run_id: str | None,
) -> dict[str, object]:
    """Return at most 50 commands from one completed run's accounting."""

    if bucket is not None and bucket not in COMMAND_BUCKETS:
        raise ActionError(
            "reproduction.command.bucket.invalid",
            f"unsupported command bucket: {bucket}",
        )
    records, results, selected_run = _reproduction_command_records(log, run_id=run_id)
    selected = [
        record
        for record in records
        if (bucket is None or record["bucket"] == bucket)
        and (entry is None or record["entry"] == entry)
        and (reason is None or record["reason"] == reason)
    ]
    returned = selected[:50]
    return {
        "filters": {"bucket": bucket, "entry": entry, "reason": reason},
        "matched": len(selected),
        "omitted": len(selected) - len(returned),
        "records": [_command_list_record(record) for record in returned],
        "returned": len(returned),
        "run_id": selected_run.run_id,
        "schema": COMMAND_LIST_SCHEMA,
        "summary": results.summary,
    }


def show_reproduction_command(
    log: LogContext,
    *,
    entry: str,
    execution_id: str,
    run_id: str | None,
) -> dict[str, object]:
    """Return one complete command record from completed-run accounting."""

    records, results, selected_run = _reproduction_command_records(log, run_id=run_id)
    selected = [
        record
        for record in records
        if record["entry"] == entry and record["execution_id"] == execution_id
    ]
    if not selected:
        raise ActionError(
            "reproduction.command.unknown",
            f"reproduction run contains no {entry}:{execution_id}",
        )
    if len(selected) != 1:
        raise ActionError(
            "reproduction.command.ambiguous",
            f"reproduction run contains ambiguous {entry}:{execution_id}",
        )
    return {
        "command": dict(selected[0]),
        "run_id": selected_run.run_id,
        "schema": COMMAND_SHOW_SCHEMA,
        "summary": results.summary,
    }


def compose_reproduction_command_list(value: Mapping[str, object]) -> str:
    """Render one bounded command list in a concise human form."""

    filters = cast(Mapping[str, object], value["filters"])
    records = cast(Sequence[Mapping[str, object]], value["records"])
    bucket = filters.get("bucket") or "all buckets"
    lines = [
        f"Commands for {value['run_id']} — {bucket}",
        f"Matched {value['matched']}; returned {value['returned']}; "
        f"omitted {value['omitted']}.",
    ]
    for record in records:
        lines.extend(
            (
                "",
                f"{record['entry']} {record['execution_id']}",
                f"  {record['bucket']}: {record['reason']}",
                f"  {record['cwd']}$ {record['command']}",
            )
        )
    return "\n".join(lines) + "\n"


def compose_reproduction_command(value: Mapping[str, object]) -> str:
    """Render one complete command record in a human-readable form."""

    record = cast(Mapping[str, object], value["command"])
    recipe = cast(Mapping[str, object], record["recipe"])
    lines = [
        f"Command {record['entry']} {record['execution_id']}",
        f"Run: {value['run_id']}",
        f"Accounting: {record['bucket']} ({record['reason']})",
        f"Working directory: {record['cwd']}",
        f"Command: {record['command']}",
        f"Automatic: {'yes' if record['auto_reproduce'] else 'no'}",
        "Requires reproduction at query time: "
        + ("yes" if record["requires_reproduction"] else "no"),
        f"Exclusive: {'yes' if record['exclusive'] else 'no'}",
        "Inputs: " + ", ".join(cast(Sequence[str], recipe["inputs"])),
        "Outputs: "
        + ", ".join(sorted(cast(Mapping[str, str], recipe["outputs"]))),
    ]
    details = cast(Sequence[str], record["details"])
    if details:
        lines.append("Details: " + "; ".join(details))
    return "\n".join(lines) + "\n"


def _reproduction_command_records(
    log: LogContext, *, run_id: str | None
) -> tuple[list[dict[str, object]], ReproductionResults, RunResult]:
    """Reconstruct exact rows and verify them against published run counts."""

    results, _currentness = _current(log)
    runs = [
        run
        for run in results.runs
        if run.status == "complete" and (run_id is None or run.run_id == run_id)
    ]
    if not runs:
        subject = "the latest completed run" if run_id is None else run_id
        raise ActionError(
            "reproduction.command.run_unavailable",
            f"published reproduction contains no command accounting for {subject}",
        )
    selected_run = runs[0]
    if selected_run.command_outcomes is None:
        raise ActionError(
            "reproduction.command.details_unavailable",
            f"command accounting predates bounded queries: {selected_run.run_id}",
        )

    from .reproduction_accounting import command_snapshot_index
    from .reproduction_jobs import _find_run, _load_run, _plan_from_record

    run_root = _find_run(log, selected_run.run_id)
    plan = _plan_from_record(_load_run(run_root / "run.json"))
    try:
        snapshots = command_snapshot_index(plan)
    except CommandAccountingError as error:
        raise ActionError(
            "reproduction.command.details_unavailable", str(error)
        ) from error
    if not snapshots:
        raise ActionError(
            "reproduction.command.details_unavailable",
            f"accepted run has no per-command snapshot: {selected_run.run_id}",
        )

    current = project_reproduction_command_details(log, plan.target)
    details = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in current
    }
    if len(details) != len(current) or not set(snapshots) <= set(details):
        raise ActionError(
            "reproduction.command.details_unavailable",
            "current command metadata no longer matches the accepted run",
        )
    terminal = {
        (item.entry, item.execution_id): item
        for item in results.commands
        if item.run_id == selected_run.run_id
    }
    records: list[dict[str, object]] = []
    for key, detail in sorted(details.items()):
        snapshot = snapshots.get(key)
        leaf, reason, extra = _command_accounting_identity(
            detail,
            snapshot=snapshot,
            terminal=terminal,
            include_all=selected_run.include_all,
            plan=plan,
        )
        records.append(
            {
                **dict(detail),
                "bucket": _command_bucket(leaf),
                "command": _recorded_command(detail),
                "details": extra,
                "reason": reason,
                "run_selection": (
                    snapshot.get("selection") if snapshot is not None else None
                ),
            }
        )
    _verify_command_record_counts(records, selected_run.command_outcomes)
    return records, results, selected_run


def _command_accounting_identity(
    detail: Mapping[str, object],
    *,
    snapshot: Mapping[str, object] | None,
    terminal: Mapping[tuple[str, str], object],
    include_all: bool,
    plan: ReproductionPlan,
) -> tuple[str, str, list[str]]:
    key = (cast(str, detail["entry"]), cast(str, detail["execution_id"]))
    if snapshot is None:
        if (
            not include_all
            and detail["requires_reproduction"] is True
            and detail["auto_reproduce"] is False
        ):
            return "not_automatic", "not_automatic", []
        return "reproduction_not_needed", "reproduction_not_needed", []
    selection = snapshot["selection"]
    if selection == "not_needed":
        return "reproduction_not_needed", "reproduction_not_needed", []
    if selection == "unchanged":
        leaf = f"unchanged_{snapshot['prior_disposition']}"
        return leaf, leaf, []
    result = terminal.get(key)
    if result is None:
        raise ActionError(
            "reproduction.command.details_unavailable",
            f"terminal command record is unavailable: {key[0]}:{key[1]}",
        )
    disposition = cast(str, getattr(result, "disposition"))
    reasons = sorted(
        {
            cast(str, case["reason"])
            for case in plan.cases
            if case.get("entry") == key[0]
            and case.get("execution_id") == key[1]
            and isinstance(case.get("reason"), str)
        }
    )
    return disposition, reasons[0] if len(reasons) == 1 else disposition, reasons


def _command_bucket(leaf: str) -> str:
    if leaf in {
        "reproduction_not_needed",
        "unchanged_failed",
        "unchanged_blocked",
    }:
        return "reproduction-not-retried"
    if leaf == "not_automatic":
        return "skipped-by-policy"
    return leaf


def _recorded_command(detail: Mapping[str, object]) -> str:
    recipe = cast(Mapping[str, object], detail["recipe"])
    arguments = [
        "<project>/.conda/bin/python",
        cast(str, recipe["script"]),
        *cast(Sequence[str], recipe["parameters"]),
    ]
    return shlex.join(arguments)


def _command_list_record(record: Mapping[str, object]) -> dict[str, object]:
    return {
        name: record[name]
        for name in (
            "auto_reproduce",
            "bucket",
            "command",
            "cwd",
            "entry",
            "execution_id",
            "reason",
        )
    }


def _verify_command_record_counts(
    records: Sequence[Mapping[str, object]], expected: Mapping[str, int]
) -> None:
    counts = {name: 0 for name in COMMAND_REASONS}
    for record in records:
        reason = cast(str, record["reason"])
        leaf = (
            cast(str, record["bucket"])
            if record["bucket"] in {"succeeded", "failed", "blocked"}
            else "not_automatic"
            if record["bucket"] == "skipped-by-policy"
            else reason
        )
        if leaf not in counts:
            raise ActionError(
                "reproduction.command.details_unavailable",
                f"command accounting reason is unsupported: {leaf}",
            )
        counts[leaf] += 1
    actual = {**counts, "total": len(records)}
    if actual != dict(expected):
        raise ActionError(
            "reproduction.command.details_unavailable",
            "current command metadata no longer reconciles with published run counts",
        )


def _current(
    log: LogContext,
) -> tuple[ReproductionResults, Mapping[tuple[str, str], ArtifactCurrentness]]:
    path = log.root / REPRODUCTION_RESULTS
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.results.missing",
            "no cached reproduction result; run reproduction with --recheck to "
            f"rebuild it: {path}",
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
