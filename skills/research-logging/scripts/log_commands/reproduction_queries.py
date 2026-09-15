"""Bounded read-only projections of published reproduction state."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, cast

from research_log_paths import RESULTS_STORE
from validation.discovery import discover_summaries
from validation.report_context import load_report_context

from .context import LogContext, resolve_log, resolve_project_root
from .model import ActionError
from .reproduction_accounting import CommandAccountingError, project_command_selection
from .reproduction_contract import ReproductionPlan
from .reproduction_planner import (
    ReproductionCommandInventory,
    ReproductionStateProjection,
    project_reproduction_command_inventory,
    project_reproduction_state,
)
from .reproduction_result_storage import (
    load_reproduction_report_projection,
    reproduction_summary_projection,
)
from .reproduction_results import (
    ArtifactCurrentness,
    ArtifactResult,
    ReproductionResultError,
    ReproductionResults,
    ReproductionResultSchemaError,
    RunResult,
    command_summary_counts,
    compose_reproduction_report,
    compose_reproduction_summary_projection,
    project_current_results,
)

ARTIFACT_LIST_SCHEMA = "research-log-reproduction-artifact-list/1"
ARTIFACT_SHOW_SCHEMA = "research-log-reproduction-artifact/1"
COMMAND_LIST_SCHEMA = "research-log-reproduction-command-list/3"
COMMAND_SHOW_SCHEMA = "research-log-reproduction-command/3"
SUMMARY_SCHEMA = "research-log-reproduction-summary/5"
ROOT_SUMMARY_SCHEMA = "research-log-reproduction-root-summary/5"
MAX_COMMAND_DIAGNOSTIC_BYTES = 16 * 1024
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


@dataclass(frozen=True)
class CommandListFilters:
    """Optional filters for one bounded completed-run command listing."""

    bucket: str | None = None
    entry: str | None = None
    cid: str | None = None
    reason: str | None = None
    run_id: str | None = None


def reproduction_report(log: LogContext, *, entry: str | None) -> str:
    """Return the centralized current human report projection."""

    results, currentness = _report_projection(log)
    return compose_reproduction_report(
        results,
        context=load_report_context(log.summary),
        currentness=currentness,
        entry=entry,
        folder_links_from=Path.cwd(),
    )


def reproduction_summary(log: LogContext) -> dict[str, object]:
    """Return the canonical compact summary for one maintained log."""
    try:
        projection = reproduction_summary_projection(log.root / RESULTS_STORE)
    except ReproductionResultSchemaError as error:
        raise ActionError(
            "reproduction.results.schema_unsupported", str(error)
        ) from error
    except Exception as error:
        raise _result_store_error(error) from error
    command = cast(Mapping[str, int], projection.get("command") or {})
    artifact = cast(Mapping[str, int], projection.get("artifact") or {})
    complete = projection.get("run") is not None
    return {
        "artifacts": None
        if not complete
        else {
            "matched": artifact["matched"],
            "not_matched": artifact["not_matched"],
            "not_compared": {
                name: artifact[name]
                for name in (
                    "command_failed",
                    "command_blocked",
                    "command_skipped",
                    "comparison_failed",
                )
            }
            | {
                "total": artifact["total"]
                - artifact["matched"]
                - artifact["not_matched"]
            },
            "total": artifact["total"],
        },
        "commands": None if not complete else dict(command_summary_counts(command)),
        "generated_at": projection["updated_at"],
        "run_id": projection.get("run_id"),
        "resolved": None
        if not complete
        else all(
            command[name] == 0
            for name in ("blocked", "failed", "unchanged_blocked", "unchanged_failed")
        ),
        "schema": SUMMARY_SCHEMA,
        "status": "complete" if complete else "not_run",
        "summary": projection["summary"],
    }


def reproduction_summary_text(log: LogContext) -> str:
    """Return the canonical compact human summary for one maintained log."""

    try:
        projection = reproduction_summary_projection(log.root / RESULTS_STORE)
    except Exception as error:
        raise _result_store_error(error) from error
    return _summary_text_from_projection(projection)


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
    try:
        projection = reproduction_summary_projection(log.root / RESULTS_STORE)
    except Exception as error:
        # An absent reproduction domain is cold state, not an invitation to
        # construct an aggregate placeholder in memory.
        if "reproduction result is absent" not in str(error):
            raise _result_store_error(error) from error
        projection = {"artifact": _empty_artifact_counts(), "run": None}
    outcomes = _no_work_command_outcomes(
        plan,
        project_reproduction_command_inventory(log, plan.target),
    )
    return compose_reproduction_summary_projection(
        cast(Mapping[str, object], projection["artifact"]),
        outcomes,
        latest_run_id=cast(str | None, projection.get("run_id")),
        reconciliation=True,
    )


def _summary_text_from_projection(projection: Mapping[str, object]) -> str:
    """Format a scalar store projection without hydrating result history."""

    if projection.get("run") is None:
        return compose_reproduction_summary_projection(
            _empty_artifact_counts(), None, latest_run_id=None
        )
    return compose_reproduction_summary_projection(
        cast(Mapping[str, object], projection["artifact"]),
        cast(Mapping[str, int], projection["command"]),
        latest_run_id=cast(str | None, projection.get("run_id")),
    )


def _empty_artifact_counts() -> dict[str, object]:
    return {
        "matched": 0,
        "not_matched": 0,
        "not_compared": {
            "command_failed": 0,
            "command_blocked": 0,
            "command_skipped": 0,
            "comparison_failed": 0,
            "total": 0,
        },
        "total": 0,
    }


def _result_store_error(error: Exception) -> ActionError:
    """Map normalized-store failures without turning corrupt state into cold state."""
    message = str(error)
    if "unsupported" in message:
        return ActionError("reproduction.results.schema_unsupported", message)
    if "locked" in message or "busy" in message:
        return ActionError("reproduction.results.busy", message)
    if message == "reproduction result is absent":
        return ActionError("reproduction.results.missing", message)
    return ActionError("reproduction.results.invalid", message)


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


def _artifact_currentness(
    artifacts: Sequence[ArtifactResult], state: ReproductionStateProjection
) -> dict[tuple[str, str], str]:
    """Derive currentness for only the artifact rows selected by the SQL view."""
    result: dict[tuple[str, str], str] = {}
    for artifact in artifacts:
        entry = artifact.entry
        path = artifact.artifact
        execution_id = artifact.execution_id
        if execution_id is None:
            result[(entry, path)] = "current"
            continue
        assert artifact.cid is not None
        execution_key = (entry, artifact.cid, execution_id)
        if state.output_executions.get((entry, path)) not in {None, execution_key}:
            result[(entry, path)] = "execution_changed"
        elif execution_key not in state.last_runs:
            result[(entry, path)] = "execution_unavailable"
        elif (
            artifact.comparison is None
            and state.comparison_definitions.get((entry, path)) is not None
        ) or (
            artifact.comparison is not None
            and artifact.comparison.evidence_definition
            != state.comparison_definitions.get((entry, path))
        ):
            result[(entry, path)] = "comparison_changed"
        elif (
            last_run := state.last_runs.get(execution_key)
        ) is not None and last_run > artifact.recorded_at:
            result[(entry, path)] = "execution_reran"
        else:
            result[(entry, path)] = "current"
    return result


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
                        "resolved": None,
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
        "resolved": sum(row.get("resolved") is True for row in rows),
        "total": len(rows),
        "unavailable": sum(row["status"] == "unavailable" for row in rows),
        "unresolved": sum(row.get("resolved") is False for row in rows),
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
        f"{coverage['resolved']} resolved, {coverage['unresolved']} unresolved, "
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
        "resolved": None,
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
        "reproduction_not_retried": cast(int, summary["reproduction_not_retried"]),
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

    if outcome is not None and outcome not in {
        "matched",
        "changed",
        "failed",
        "comparison_failed",
        "skipped",
    }:
        raise ActionError(
            "reproduction.artifact.outcome.invalid", f"unsupported outcome: {outcome}"
        )
    from .reproduction_result_storage import (
        ReproductionStorageError,
        reproduction_artifact_projection,
    )

    try:
        summary, matched, records = reproduction_artifact_projection(
            log.root / RESULTS_STORE, entry=entry, outcome=outcome, artifact=artifact
        )
    except ReproductionStorageError as error:
        raise ActionError("reproduction.results.missing", str(error)) from error
    state = project_reproduction_state(log)
    currentness = _artifact_currentness(records, state)
    projected = [
        {**item.as_dict(), "currentness": currentness[(item.entry, item.artifact)]}
        for item in records
    ]
    return {
        "matched": matched,
        "omitted": matched - len(projected),
        "records": projected,
        "returned": len(projected),
        "filters": {"artifact": artifact, "entry": entry, "outcome": outcome},
        "schema": ARTIFACT_LIST_SCHEMA,
        "summary": summary,
    }


def show_reproduction_artifact(
    log: LogContext, *, entry: str, artifact: str
) -> dict[str, object]:
    """Return one complete exact current artifact record."""

    value = list_reproduction_artifacts(
        log, entry=entry, outcome=None, artifact=artifact
    )
    if value["matched"] == 0:
        raise ActionError(
            "reproduction.artifact.unknown",
            f"published reproduction contains no {entry}:{artifact}",
        )
    if value["matched"] != 1:
        raise ActionError(
            "reproduction.artifact.ambiguous",
            f"published reproduction contains ambiguous {entry}:{artifact}",
        )
    return {
        "artifact": dict(cast(Sequence[Mapping[str, object]], value["records"])[0]),
        "schema": ARTIFACT_SHOW_SCHEMA,
        "summary": value["summary"],
    }


def list_reproduction_commands(
    log: LogContext,
    filters: CommandListFilters,
) -> dict[str, object]:
    """Return at most 50 commands from one completed run's accounting."""

    if filters.bucket is not None and filters.bucket not in COMMAND_BUCKETS:
        raise ActionError(
            "reproduction.command.bucket.invalid",
            f"unsupported command bucket: {filters.bucket}",
        )
    summary, selected_run, matched, returned = _command_projection(
        log,
        _CommandProjectionRequest(
            filters.run_id,
            filters.bucket,
            filters.entry,
            filters.reason,
            50,
            filters.cid,
        ),
    )
    load_run = lru_cache(maxsize=1)(lambda: _retained_command_run(log, selected_run))
    rows = []
    for record in returned:
        error = None
        if record["bucket"] == "failed" or record["reason"] == "unchanged_failed":
            diagnostics = _command_diagnostics(
                log,
                selected_run,
                record,
                load_run=load_run,
            )
            error = _failure_summary(diagnostics)
        rows.append({**_command_list_record(record), "error": error})
    return {
        "filters": {
            "bucket": filters.bucket,
            "cid": filters.cid,
            "entry": filters.entry,
            "reason": filters.reason,
        },
        "matched": matched,
        "omitted": matched - len(returned),
        "records": rows,
        "returned": len(returned),
        "run_id": selected_run.run_id,
        "schema": COMMAND_LIST_SCHEMA,
        "summary": summary,
    }


def show_reproduction_command(
    log: LogContext,
    *,
    entry: str,
    cid: str,
    execution_id: str,
    run_id: str | None,
) -> dict[str, object]:
    """Return one complete command record from completed-run accounting."""

    summary, selected_run, matched, selected = _command_projection(
        log,
        _CommandProjectionRequest(run_id, None, entry, None, 2, cid, execution_id),
    )
    if matched == 0:
        raise ActionError(
            "reproduction.command.unknown",
            f"reproduction run contains no {entry}:{cid}:{execution_id}",
        )
    if matched != 1:
        raise ActionError(
            "reproduction.command.ambiguous",
            f"reproduction run contains ambiguous {entry}:{cid}:{execution_id}",
        )
    return {
        "command": dict(selected[0]),
        "diagnostics": _command_diagnostics(
            log,
            selected_run,
            selected[0],
        ),
        "run_id": selected_run.run_id,
        "schema": COMMAND_SHOW_SCHEMA,
        "summary": summary,
    }


def compose_reproduction_command_list(
    value: Mapping[str, object],
    *,
    path: Path | str | None = None,
    program: Path | str = "log",
) -> str:
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
        error = record.get("error")
        error_lines = (
            (f"  Error: {error['type']}: {error['message']}",)
            if isinstance(error, Mapping)
            else ()
        )
        lines.extend(
            (
                "",
                f"{record['entry']} {record['execution_id']}",
                f"  {record['bucket']}: {record['reason']}",
                *error_lines,
                f"  {record['cwd']}$ {record['command']}",
                "  Inspect: "
                + _command_show_invocation(
                    value,
                    record,
                    path=path,
                    program=program,
                ),
            )
        )
    return "\n".join(lines) + "\n"


def compose_reproduction_command(value: Mapping[str, object]) -> str:
    """Render one complete command record in a human-readable form."""

    record = cast(Mapping[str, object], value["command"])
    recipe = record.get("recipe")
    lines = [
        f"Command {record['entry']} {record['execution_id']}",
        f"Run: {value['run_id']}",
        f"Accounting: {record['bucket']} ({record['reason']})",
        f"Working directory: {record.get('cwd') or 'unavailable'}",
        f"Command: {record.get('command') or 'unavailable'}",
        f"Automatic: {'yes' if record['auto_reproduce'] else 'no'}",
        "Requires reproduction at acceptance: "
        + (
            "unavailable"
            if record.get("requires_reproduction") is None
            else "yes"
            if record["requires_reproduction"]
            else "no"
        ),
        "Exclusive: "
        + (
            "unavailable"
            if record.get("exclusive") is None
            else "yes"
            if record["exclusive"]
            else "no"
        ),
    ]
    if isinstance(recipe, Mapping):
        lines.extend(
            (
                "Inputs: " + ", ".join(cast(Sequence[str], recipe["inputs"])),
                "Outputs: "
                + ", ".join(sorted(cast(Mapping[str, str], recipe["outputs"]))),
            )
        )
    details = cast(Sequence[str], record["details"])
    if details:
        lines.append("Planning details: " + "; ".join(details))
    diagnostics = cast(Mapping[str, object], value["diagnostics"])
    availability = cast(str, diagnostics["availability"])
    if availability == "available":
        attempt = diagnostics["attempt"]
        lines.append(f"Diagnostics: retained from attempt {attempt}")
        checkpoint = cast(Mapping[str, object], diagnostics["checkpoint"])
        failure = checkpoint.get("failure")
        if isinstance(failure, Mapping):
            lines.append(f"Failure: {failure['code']}: {failure['message']}")
        lines.append(
            "Timing: "
            f"{checkpoint.get('started_at') or 'unavailable'} to "
            f"{checkpoint.get('finished_at') or 'unavailable'}; "
            + (
                f"{checkpoint['elapsed_seconds']} seconds"
                if checkpoint.get("elapsed_seconds") is not None
                else "elapsed time unavailable"
            )
        )
        outputs = cast(Sequence[Mapping[str, object]], checkpoint["outputs"])
        if outputs:
            lines.append(
                "Observed outputs: "
                + ", ".join(cast(str, item["artifact"]) for item in outputs)
            )
        for stream in ("stderr", "stdout"):
            _append_diagnostic_stream(
                lines,
                stream,
                cast(Mapping[str, object], diagnostics[stream]),
            )
    elif availability == "not_applicable":
        lines.append("Diagnostics: not applicable; command was not launched")
    else:
        lines.append(
            "Diagnostics: unavailable"
            + (
                f" ({diagnostics['reason']})"
                if diagnostics.get("reason") is not None
                else ""
            )
        )
    return "\n".join(lines) + "\n"


def _command_show_invocation(
    value: Mapping[str, object],
    record: Mapping[str, object],
    *,
    path: Path | str | None,
    program: Path | str,
) -> str:
    """Return one shell-safe drill-down command for a listed command."""

    target = str(path) if path is not None else cast(str, value["summary"])
    return shlex.join(
        (
            str(program),
            "reproduce",
            "commands",
            "show",
            "--path",
            target,
            "--entry",
            cast(str, record["entry"]),
            "--execution-id",
            cast(str, record["execution_id"]),
            "--run-id",
            cast(str, value["run_id"]),
            "--format",
            "text",
        )
    )


def _command_diagnostics(
    log: LogContext,
    run: RunResult,
    command: Mapping[str, object],
    *,
    load_run: Callable[[], tuple[Path, Mapping[str, object]] | str] | None = None,
) -> dict[str, object]:
    """Project bounded retained diagnostics for one exact launched command."""

    entry = cast(str, command["entry"])
    cid = cast(str, command["cid"])
    execution_id = cast(str, command["execution_id"])
    unavailable = (
        "command_not_launched"
        if command.get("terminal_disposition") not in {"failed", "succeeded"}
        and command.get("run_selection") != "run"
        else "run_directory_unavailable"
        if run.folder.availability != "available"
        else None
    )
    if unavailable is not None:
        return _unavailable_command_diagnostics(unavailable)
    retained = load_run() if load_run is not None else _retained_command_run(log, run)
    if isinstance(retained, str):
        return _unavailable_command_diagnostics(retained)
    run_root, record = retained
    located = _locate_command_checkpoint(run_root, entry, cid, execution_id)
    if located is None:
        return _unavailable_command_diagnostics("checkpoint_unavailable")
    checkpoint = {
        key: value
        for key, value in located.items()
        if key not in {"_stderr_path", "_stdout_path"}
    }
    stderr_path = located.get("_stderr_path")
    stdout_path = located.get("_stdout_path")
    return {
        "attempt": None,
        "availability": "available",
        "checkpoint": dict(checkpoint),
        "reason": None,
        "stderr": _diagnostic_stream(
            run_root,
            Path(stderr_path),
            published_root=run.folder.path,
        )
        if isinstance(stderr_path, str)
        else None,
        "stdout": _diagnostic_stream(
            run_root,
            Path(stdout_path),
            published_root=run.folder.path,
        )
        if isinstance(stdout_path, str)
        else None,
    }


def _retained_command_run(
    log: LogContext, run: RunResult
) -> tuple[Path, Mapping[str, object]] | str:
    """Load shared diagnostic authority once per query, including unavailable state."""

    from .reproduction_job_storage import JobStoreError, load_run_status
    from .reproduction_jobs import _find_run

    try:
        root = _find_run(log, run.run_id)
        if root != (resolve_project_root(log.root) / run.folder.path).resolve():
            return "reproduction.run.directory_changed"
        status = load_run_status(root)
        return root, {"run_id": status.run_id}
    except (ActionError, JobStoreError, OSError) as error:
        return (
            error.code
            if isinstance(error, (ActionError, JobStoreError))
            else "run_directory_unavailable"
        )


def _failure_summary(diagnostics: Mapping[str, object]) -> dict[str, object]:
    """Extract a diagnostic signature without claiming an inferred root cause."""

    for name in ("stderr", "stdout"):
        stream = diagnostics.get(name)
        if not isinstance(stream, Mapping) or not stream.get("available"):
            continue
        excerpt = re.sub(
            r"(?:\x1b|�)\[[0-?]*[ -/]*[@-~]",
            "",
            str(stream.get("excerpt") or ""),
        )
        lines = excerpt.splitlines()
        for line in reversed(lines):
            match = re.search(
                r"(?:^|\s)([\w.]*(?:Error|Exception)|StopIteration|SystemExit):?\s*(.*)$",
                line,
            )
            argument = re.search(r": error:\s*(.+)$", line)
            if match or argument:
                kind = match[1] if match else "ArgumentError"
                message = match[2] if match else cast(re.Match[str], argument)[1]
                return _compact_error(kind, message, name)
    checkpoint = diagnostics.get("checkpoint")
    failure = checkpoint.get("failure") if isinstance(checkpoint, Mapping) else None
    if isinstance(failure, Mapping):
        return _compact_error(
            str(failure["code"]), str(failure["message"]), "checkpoint"
        )
    return _compact_error(
        "diagnostics_unavailable",
        str(diagnostics.get("reason") or "No failure detail retained"),
        "unavailable",
    )


def _compact_error(kind: str, message: str, source: str) -> dict[str, object]:
    message = " ".join(_safe_diagnostic_text(message).split())
    return {
        "type": kind,
        "message": message[:512],
        "source": source,
        "truncated": len(message) > 512,
    }


def _locate_command_checkpoint(
    run_root: Path, entry: str, cid: str, execution_id: str
) -> Mapping[str, object] | None:
    """Find the one fixed-plan checkpoint for a compound command identity."""

    from .reproduction_job_storage import (
        ExecutionIdentity,
        JobStoreError,
        load_execution_checkpoint,
    )

    try:
        checkpoint = load_execution_checkpoint(
            run_root, ExecutionIdentity(entry, cid, execution_id)
        )
    except JobStoreError:
        return None
    if checkpoint is None or checkpoint.state == "active":
        return None
    return {
        "_stderr_path": checkpoint.stderr_path,
        "_stdout_path": checkpoint.stdout_path,
        "completed_at": (
            checkpoint.finished_at if checkpoint.state == "succeeded" else None
        ),
        "elapsed_seconds": checkpoint.elapsed_seconds,
        "entry": checkpoint.entry,
        "cid": checkpoint.cid,
        "execution_id": checkpoint.execution_id,
        "failure": (
            None
            if checkpoint.failure_code is None
            else {
                "code": checkpoint.failure_code,
                "message": checkpoint.failure_message,
                "recorded_at": checkpoint.failure_recorded_at,
            }
        ),
        "finished_at": checkpoint.finished_at,
        "outputs": [
            {"artifact": item.artifact, "fingerprint": dict(item.fingerprint)}
            for item in checkpoint.outputs
        ],
        "path": "state.sqlite",
        "started_at": checkpoint.started_at,
        "state": checkpoint.state,
    }


def _diagnostic_stream(
    run_root: Path, path: Path, *, published_root: str
) -> dict[str, object]:
    """Read one safe bounded tail from a retained diagnostic stream."""

    relative = path.as_posix()
    retained_path = PurePosixPath(relative)
    reported = (PurePosixPath(published_root) / retained_path).as_posix()
    target = run_root.joinpath(*retained_path.parts)
    if retained_path.is_absolute() or ".." in retained_path.parts:
        return _unavailable_diagnostic_stream(reported, "path_invalid")
    current = run_root
    for part in retained_path.parts:
        current /= part
        if current.is_symlink():
            return _unavailable_diagnostic_stream(reported, "path_invalid")
    if not target.is_file():
        return _unavailable_diagnostic_stream(reported, "missing")
    try:
        size = target.stat().st_size
        with target.open("rb") as stream:
            if size > MAX_COMMAND_DIAGNOSTIC_BYTES:
                stream.seek(size - MAX_COMMAND_DIAGNOSTIC_BYTES)
            raw = stream.read(MAX_COMMAND_DIAGNOSTIC_BYTES + 1)
    except OSError:
        return _unavailable_diagnostic_stream(reported, "unreadable")
    if len(raw) > MAX_COMMAND_DIAGNOSTIC_BYTES:
        raw = raw[-MAX_COMMAND_DIAGNOSTIC_BYTES:]
    return {
        "available": True,
        "bytes": size,
        "excerpt": _safe_diagnostic_text(raw.decode("utf-8", errors="replace")),
        "path": reported,
        "reason": None,
        "truncated": size > len(raw),
    }


def _safe_diagnostic_text(value: str) -> str:
    """Remove terminal control characters from one untrusted diagnostic excerpt."""

    return "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32 and ord(character) != 127
        else "�"
        for character in value
    )


def _unavailable_diagnostic_stream(path: str, reason: str) -> dict[str, object]:
    return {
        "available": False,
        "bytes": None,
        "excerpt": None,
        "path": path,
        "reason": reason,
        "truncated": False,
    }


def _unavailable_command_diagnostics(reason: str) -> dict[str, object]:
    return {
        "attempt": None,
        "availability": (
            "not_applicable" if reason == "command_not_launched" else "unavailable"
        ),
        "checkpoint": None,
        "reason": reason,
        "stderr": None,
        "stdout": None,
    }


def _append_diagnostic_stream(
    lines: list[str], name: str, stream: Mapping[str, object]
) -> None:
    """Append one retained stream path and bounded excerpt to command detail."""

    title = name.capitalize()
    if stream["available"] is not True:
        lines.append(f"{title}: unavailable ({stream['reason']})")
        return
    size = cast(int, stream["bytes"])
    suffix = "; tail shown" if stream["truncated"] is True else ""
    lines.append(f"{title}: {stream['path']} ({size} bytes{suffix})")
    excerpt = cast(str, stream["excerpt"])
    if excerpt:
        lines.extend((f"--- {name} ---", excerpt.rstrip("\n"), f"--- end {name} ---"))


@dataclass(frozen=True)
class _CommandProjectionRequest:
    run_id: str | None
    bucket: str | None
    entry: str | None
    reason: str | None
    limit: int
    cid: str | None = None
    execution_id: str | None = None


def _command_projection(
    log: LogContext, request: _CommandProjectionRequest
) -> tuple[str, RunResult, int, tuple[dict[str, object], ...]]:
    """Read one exact command view without hydrating unrelated run history."""
    from .reproduction_result_storage import (
        CommandProjectionRequest,
        ReproductionStorageError,
        reproduction_command_projection,
    )

    try:
        summary, run, matched, records = reproduction_command_projection(
            log.root / RESULTS_STORE,
            CommandProjectionRequest(
                request.run_id,
                request.bucket,
                request.entry,
                request.reason,
                request.cid,
                request.execution_id,
                request.limit,
            ),
            project_root=resolve_project_root(log.root),
        )
    except ReproductionStorageError as error:
        raise ActionError("reproduction.command.run_unavailable", str(error)) from error
    return (
        summary,
        run,
        matched,
        tuple(
            {**dict(record), "command": _recorded_command(record)} for record in records
        ),
    )


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
                "reproduction.command.invalid",
                f"command accounting reason is unsupported: {leaf}",
            )
        counts[leaf] += 1
    actual = {**counts, "total": len(records)}
    if actual != dict(expected):
        raise ActionError(
            "reproduction.command.invalid",
            "immutable command records do not reconcile with published run counts",
        )


def _report_projection(
    log: LogContext,
) -> tuple[ReproductionResults, Mapping[tuple[str, str], ArtifactCurrentness]]:
    path = log.root / RESULTS_STORE
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.results.missing",
            "no cached reproduction result; run reproduction with --recheck to "
            f"rebuild it: {path}",
        )
    try:
        results = load_reproduction_report_projection(
            path, project_root=resolve_project_root(log.root)
        )
        expected = resolve_project_root(log.root) / results.summary
        if expected.resolve() != log.summary.resolve():
            raise ReproductionResultError("result summary identity changed")
        state = project_reproduction_state(log)
        return project_current_results(results, state)
    except ReproductionResultSchemaError as error:
        raise ActionError(
            "reproduction.results.schema_unsupported", str(error)
        ) from error
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
