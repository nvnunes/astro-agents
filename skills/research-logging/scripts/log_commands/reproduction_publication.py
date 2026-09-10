"""Independent publication of completed reproduction state."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence, cast

from research_log_data import DataContractError, parse_fingerprint
from research_log_paths import (
    REPRODUCTION_REPORT,
    REPRODUCTION_RESULTS,
    VALIDATION_RESULTS,
)
from validation.engine import RULES_VERSION
from validation.human_projection import load_report_context, provenance_artifact_counts
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    CompletionState,
    MechanicalGeneratedRecord,
)
from validation.operation_state import OperationLockError, operation_lock

from .context import LogContext, resolve_project_root
from .model import ActionError
from .reproduction_accounting import (
    CommandAccountingError,
    command_snapshot_index,
    project_command_selection,
)
from .reproduction_comparison import (
    ArtifactComparison,
    ExecutionComparison,
)
from .reproduction_contract import REPRODUCTION_RESULT_SCHEMA, ReproductionPlan
from .reproduction_paths import project_tmp_relative
from .reproduction_planner import (
    ReproductionCommandInventory,
    project_reproduction_command_inventory,
    project_reproduction_state,
    verify_reproduction_runtime_snapshot,
)
from .reproduction_results import (
    OUTCOMES,
    ArtifactResult,
    CommandResult,
    ComparisonRecord,
    ReproductionResultError,
    ReproductionResults,
    ReproductionResultSchemaError,
    RunFolder,
    RunResult,
    compose_reproduction_report,
    load_reproduction_results,
    load_results_or_empty,
    merge_reproduction_results,
    project_current_results,
    reconcile_run_folders,
)
from .storage import PublicationError, atomic_write_texts


@dataclass(frozen=True)
class PublishedReproduction:
    """The exact completed machine and human reproduction projections."""

    results: ReproductionResults
    report: str


@dataclass(frozen=True)
class CompletedPublication:
    """One complete normal-endpoint publication request."""

    plan: ReproductionPlan
    comparisons: tuple[ExecutionComparison, ...]
    run_id: str
    accepted_at: str
    finished_at: str
    run_folder: Path
    dependency_skips: tuple[Mapping[str, object], ...] = ()


def verify_publication_retry_compatibility(
    log: LogContext, plan: ReproductionPlan
) -> None:
    """Reject a partial publication retry against outdated generated results."""

    path = log.root / REPRODUCTION_RESULTS
    if not path.exists() and not path.is_symlink():
        return
    try:
        load_reproduction_results(path)
    except ReproductionResultSchemaError as error:
        if not _replaces_outdated_results(plan):
            raise ActionError(
                "reproduction.results.schema_unsupported", str(error)
            ) from error
    except ReproductionResultError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error


def publish_completed_reproduction(
    log: LogContext,
    request: CompletedPublication,
) -> PublishedReproduction:
    """Publish one normally completed target without validation or state mutation."""

    project_root = resolve_project_root(log.root)
    try:
        artifacts = _artifact_results(request)
        commands = _command_results(request)
    except ReproductionResultError as error:
        raise ActionError("reproduction.publication.failed", str(error)) from error
    try:
        with operation_lock(log.root, "reproduction-publication.lock"):
            verify_reproduction_runtime_snapshot(log, request.plan)
            run = _run_result(
                request.plan,
                artifacts,
                request,
                project_root,
                project_reproduction_command_inventory(log, request.plan.target),
            )
            result_path = log.root / REPRODUCTION_RESULTS
            summary = log.summary.resolve().relative_to(project_root).as_posix()
            current = load_results_or_empty(
                result_path,
                summary=summary,
                updated_at=request.finished_at,
                replace_outdated=_replaces_outdated_results(request.plan),
            )
            current = reconcile_run_folders(current, project_root=project_root)
            state_projection = project_reproduction_state(
                log,
                targets=(*[run.target for run in current.runs], request.plan.target),
            )
            snapshots = command_snapshot_index(request.plan)
            state_projection = replace(
                state_projection,
                reachable_commands=frozenset(
                    key for key, item in snapshots.items() if item.get("queued") is True
                ),
            )
            if request.plan.target.get("kind") == "log":
                state_projection = replace(
                    state_projection,
                    reachable=frozenset(
                        (
                            _required(case, "entry"),
                            _required(case, "artifact"),
                        )
                        for case in request.plan.cases
                    ),
                )
            merged = merge_reproduction_results(
                current,
                artifacts,
                run,
                commands=commands,
                state=(
                    None
                    if request.plan.target.get("kind") == "execution"
                    else state_projection
                ),
            )
            projected, currentness = project_current_results(merged, state_projection)
            context = load_report_context(log.summary)
            report = compose_reproduction_report(
                projected,
                context=context,
                currentness=currentness,
                folder_links_from=log.root,
            )
            updates: dict[Path, str | None] = {
                result_path: merged.serialized(),
                log.root / REPRODUCTION_REPORT: report,
            }
            verify_reproduction_runtime_snapshot(log, request.plan)
            atomic_write_texts(updates)
    except (OperationLockError, OSError, PublicationError) as error:
        raise ActionError("reproduction.publication.failed", str(error)) from error
    return PublishedReproduction(merged, report)


def _replaces_outdated_results(plan: ReproductionPlan) -> bool:
    """Return whether one whole-log plan can rebuild cumulative generated state."""

    if plan.source_snapshot.get("result_schema") != REPRODUCTION_RESULT_SCHEMA:
        return False
    if plan.target != {"entry": None, "kind": "log"}:
        return False
    commands = plan.source_snapshot.get("commands")
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
        return False
    try:
        snapshots = command_snapshot_index(plan)
    except CommandAccountingError:
        return False
    return len(snapshots) == len(commands) and all(
        item["selection"] not in {"not_needed", "unchanged"}
        for item in snapshots.values()
    )


def _artifact_results(
    request: CompletedPublication,
) -> tuple[ArtifactResult, ...]:
    compared = _comparison_index(request.comparisons)
    skipped = _dependency_skip_index(request.dependency_skips)
    results: list[ArtifactResult] = []
    consumed: set[tuple[str, str, str]] = set()
    for case in request.plan.cases:
        result, comparison_key = _artifact_result_for_case(
            case, compared, skipped, request
        )
        if result is not None:
            results.append(result)
        if comparison_key is not None:
            consumed.add(comparison_key)
    unused = set(compared) - consumed
    if unused:
        raise ActionError(
            "reproduction.publication.invalid", "comparison is outside planned cases"
        )
    return tuple(
        sorted(results, key=lambda value: (int(value.entry[1:]), value.artifact))
    )


def _comparison_index(
    comparisons: Sequence[ExecutionComparison],
) -> dict[tuple[str, str, str], ArtifactComparison]:
    compared: dict[tuple[str, str, str], ArtifactComparison] = {}
    for result in comparisons:
        for artifact in result.artifacts:
            key = (result.entry, result.execution_id, artifact.artifact)
            if key in compared:
                raise ActionError(
                    "reproduction.publication.invalid", "duplicate artifact comparison"
                )
            compared[key] = artifact
    return compared


def _dependency_skip_index(
    skips: Sequence[Mapping[str, object]],
) -> set[tuple[str, str]]:
    return {
        (cast(str, value.get("entry")), cast(str, value.get("execution_id")))
        for value in skips
        if isinstance(value.get("entry"), str)
        and isinstance(value.get("execution_id"), str)
        and value.get("reason") == "dependency_failed"
    }


def _command_results(request: CompletedPublication) -> tuple[CommandResult, ...]:
    """Project new terminal results without replacing unselected prior results."""

    try:
        snapshots = command_snapshot_index(request.plan)
    except CommandAccountingError as error:
        raise ActionError("reproduction.publication.invalid", str(error)) from error
    compared = {(item.entry, item.execution_id): item for item in request.comparisons}
    if len(compared) != len(request.comparisons):
        raise ActionError(
            "reproduction.publication.invalid", "execution comparison is duplicated"
        )
    skipped = _dependency_skip_index(request.dependency_skips)
    results: list[CommandResult] = []
    for key, snapshot in sorted(snapshots.items()):
        selection = snapshot["selection"]
        if selection in {"not_needed", "policy", "unchanged"}:
            continue
        if selection == "blocked" or key in skipped:
            disposition = "blocked"
        else:
            comparison = compared.get(key)
            if comparison is None:
                raise ActionError(
                    "reproduction.publication.incomplete",
                    f"command has no terminal result: {key[0]}:{key[1]}",
                )
            disposition = "succeeded" if comparison.complete else "failed"
        results.append(
            CommandResult(
                key[0],
                key[1],
                disposition,
                cast(str, snapshot["source_digest"]),
                request.finished_at,
                request.run_id,
            )
        )
    return tuple(results)


def _artifact_result_for_case(
    case: Mapping[str, object],
    compared: Mapping[tuple[str, str, str], ArtifactComparison],
    skipped: set[tuple[str, str]],
    request: CompletedPublication,
) -> tuple[ArtifactResult | None, tuple[str, str, str] | None]:
    disposition = case.get("disposition")
    if disposition == "current":
        return None, None
    entry = _required(case, "entry")
    artifact = _required(case, "artifact")
    execution = case.get("execution_id")
    if execution is not None and not isinstance(execution, str):
        raise ActionError(
            "reproduction.publication.invalid", "invalid case execution ID"
        )
    case_reason = case.get("reason")
    if case_reason is not None and not isinstance(case_reason, str):
        raise ActionError("reproduction.publication.invalid", "invalid artifact reason")
    comparison_key = (entry, cast(str, execution), artifact)
    comparison = compared.get(comparison_key)
    outcome, reason, details = _case_outcome(case, execution, comparison, skipped)
    return (
        ArtifactResult(
            entry,
            artifact,
            execution,
            outcome,
            reason,
            request.finished_at,
            request.run_id,
            details,
        ),
        comparison_key if comparison is not None else None,
    )


def _case_outcome(
    case: Mapping[str, object],
    execution: str | None,
    comparison: ArtifactComparison | None,
    skipped: set[tuple[str, str]],
) -> tuple[str, str | None, ComparisonRecord | None]:
    disposition = case.get("disposition")
    case_reason = cast(str | None, case.get("reason"))
    entry = _required(case, "entry")
    artifact = _required(case, "artifact")
    if comparison is not None:
        return comparison.outcome, comparison.reason, _comparison_record(comparison)
    if execution is not None and (entry, execution) in skipped:
        return "skipped", "dependency_failed", None
    if disposition in {"failed", "skipped"}:
        return cast(str, disposition), case_reason, None
    raise ActionError(
        "reproduction.publication.incomplete",
        f"artifact has no terminal result: {entry}:{artifact}",
    )


def _comparison_record(
    comparison: ArtifactComparison,
) -> ComparisonRecord | None:
    if comparison.profile is None:
        return None
    try:
        return ComparisonRecord(
            comparison.profile,
            _fingerprint(comparison.expected, "expected"),
            _fingerprint(comparison.regenerated, "regenerated"),
            comparison.evidence_definition,
            comparison.evidence,
        )
    except (DataContractError, ValueError) as error:
        raise ActionError("reproduction.publication.invalid", str(error)) from error


def _run_result(
    plan: ReproductionPlan,
    artifacts: Sequence[ArtifactResult],
    request: CompletedPublication,
    project_root: Path,
    inventory: ReproductionCommandInventory,
) -> RunResult:
    try:
        folder = project_tmp_relative(request.run_folder, project_root)
    except OSError as error:
        raise ActionError(
            "reproduction.run.path_invalid", "run folder is outside the project"
        ) from error
    counts = Counter(item.outcome for item in artifacts)
    command_outcomes = _command_outcomes(plan, request, inventory)
    return RunResult(
        request.run_id,
        plan.target,
        plan.include_all,
        "complete",
        request.accepted_at,
        request.finished_at,
        {outcome: counts[outcome] for outcome in OUTCOMES},
        RunFolder(folder, "available"),
        _execution_timings(plan, request.run_folder),
        command_outcomes,
        _command_records(plan, request),
    )


def _command_records(
    plan: ReproductionPlan,
    request: CompletedPublication,
) -> tuple[Mapping[str, object], ...] | None:
    """Freeze every command-accounting row from accepted immutable metadata."""

    try:
        snapshots = command_snapshot_index(plan)
    except CommandAccountingError as error:
        raise ActionError("reproduction.publication.invalid", str(error)) from error
    if not snapshots or any("recipe" not in item for item in snapshots.values()):
        return None
    compared = {(item.entry, item.execution_id): item for item in request.comparisons}
    skipped = _dependency_skip_index(request.dependency_skips)
    records: list[Mapping[str, object]] = []
    for key, snapshot in sorted(snapshots.items()):
        selection = cast(str, snapshot["selection"])
        prior = cast(str | None, snapshot["prior_disposition"])
        terminal: str | None = None
        details = list(cast(Sequence[str], snapshot["details"]))
        if selection == "policy":
            bucket = "skipped-by-policy"
            reason = "not_automatic"
        elif selection == "not_needed":
            bucket = "reproduction-not-retried"
            reason = "reproduction_not_needed"
        elif selection == "unchanged":
            terminal = prior
            bucket = "reproduction-not-retried"
            reason = f"unchanged_{prior}"
        elif selection == "blocked" or key in skipped:
            terminal = "blocked"
            bucket = "blocked"
            reason = details[0] if len(details) == 1 else "blocked"
        else:
            comparison = compared.get(key)
            if comparison is None:
                raise ActionError(
                    "reproduction.publication.invalid",
                    f"command has no terminal comparison: {key[0]}:{key[1]}",
                )
            terminal = "succeeded" if comparison.complete else "failed"
            bucket = terminal
            reason = details[0] if len(details) == 1 else terminal
        records.append(
            {
                "auto_reproduce": snapshot["auto_reproduce"],
                "bucket": bucket,
                "cwd": snapshot["cwd"],
                "details": details,
                "entry": snapshot["entry"],
                "execution_id": snapshot["execution_id"],
                "exclusive": snapshot["exclusive"],
                "prior_disposition": prior,
                "queued": snapshot["queued"],
                "reason": reason,
                "recipe": dict(cast(Mapping[str, object], snapshot["recipe"])),
                "requires_reproduction": snapshot["requires_reproduction"],
                "run_selection": selection,
                "source_digest": snapshot["source_digest"],
                "terminal_disposition": terminal,
            }
        )
    return tuple(records)


def _command_outcomes(
    plan: ReproductionPlan,
    request: CompletedPublication,
    inventory: ReproductionCommandInventory,
) -> Mapping[str, int]:
    """Return one exhaustive command reconciliation for a completed run."""

    try:
        selection = project_command_selection(plan, inventory)
    except CommandAccountingError as error:
        raise ActionError("reproduction.publication.invalid", str(error)) from error
    compared: dict[tuple[str, str], ExecutionComparison] = {}
    for comparison in request.comparisons:
        key = (comparison.entry, comparison.execution_id)
        if key in compared:
            raise ActionError(
                "reproduction.publication.invalid",
                "execution comparison is duplicated",
            )
        compared[key] = comparison
    dependency_skips = _dependency_skip_index(request.dependency_skips)
    if (
        set(compared) & dependency_skips
        or set(compared) | dependency_skips != selection.run_keys
    ):
        raise ActionError(
            "reproduction.publication.invalid",
            "terminal command outcomes do not match the accepted plan",
        )

    succeeded = sum(comparison.complete for comparison in compared.values())
    failed = len(compared) - succeeded
    blocked = len(dependency_skips) + selection.blocked
    return {
        "not_automatic": selection.not_automatic,
        "reproduction_not_needed": selection.reproduction_not_needed,
        "unchanged_failed": selection.unchanged_failed,
        "unchanged_blocked": selection.unchanged_blocked,
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "total": selection.total,
    }


def _execution_timings(
    plan: ReproductionPlan, run_root: Path
) -> tuple[Mapping[str, object], ...]:
    """Project explicit launched-attempt timing in accepted execution order."""

    observed: dict[tuple[str, str], Mapping[str, object]] = {}
    for path in sorted((run_root / "checkpoints").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ActionError("reproduction.publication.invalid", str(error)) from error
        if not isinstance(value, Mapping) or value.get("started_at") is None:
            continue
        entry = value.get("entry")
        identity = value.get("execution_id")
        if not isinstance(entry, str) or not isinstance(identity, str):
            raise ActionError(
                "reproduction.publication.invalid",
                "checkpoint timing identity is invalid",
            )
        observed[(entry, identity)] = {
            "elapsed_seconds": value.get("elapsed_seconds"),
            "entry": entry,
            "execution_id": identity,
            "finished_at": value.get("finished_at"),
            "started_at": value.get("started_at"),
        }
    return tuple(
        observed[key]
        for planned in sorted(
            plan.executions, key=lambda item: cast(int, item["order"])
        )
        for key in ((str(planned["entry"]), str(planned["execution_id"])),)
        if key in observed
    )


def _load_validation(log: LogContext) -> MechanicalGeneratedRecord:
    path = log.root / VALIDATION_RESULTS
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.validation.missing",
            f"missing cached validation result; run full validation: {path}",
        )
    try:
        return MechanicalGeneratedRecord.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.validation.invalid", str(error)) from error


def _require_admissible_validation(
    log: LogContext, record: MechanicalGeneratedRecord
) -> None:
    if (
        record.completion is CompletionState.INCOMPLETE
        or Path(record.summary).resolve() != log.summary.resolve()
        or record.rules_version != RULES_VERSION
    ):
        raise ActionError(
            "reproduction.validation.stale", "validation identity is not admissible"
        )
    if (
        any(
            check.status in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}
            for check in record.checks
            if check.scope in {CheckScope.CONFORMANCE, CheckScope.EVIDENCE}
        )
        or provenance_artifact_counts(record)[CheckStatus.FAIL.value]
    ):
        raise ActionError(
            "reproduction.validation.blocked", "validation contains blocking findings"
        )


def _fingerprint(value: object, subject: str):
    if value is None:
        return None
    return parse_fingerprint(value, f"comparison.{subject}")


def _required(value: Mapping[str, object], name: str) -> str:
    selected = value.get(name)
    if not isinstance(selected, str) or not selected:
        raise ActionError(
            "reproduction.publication.invalid", f"artifact case has no {name}"
        )
    return selected
