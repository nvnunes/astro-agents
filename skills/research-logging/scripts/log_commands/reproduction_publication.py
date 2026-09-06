"""Coordinated publication of completed reproduction state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

from research_log_data import DataContractError, parse_fingerprint
from validation.engine import RULES_VERSION
from validation.human_projection import load_report_context, provenance_artifact_counts
from validation.mechanical_results import (
    CheckScope,
    CheckStatus,
    CompletionState,
    MechanicalGeneratedRecord,
)
from validation.operation_state import OperationLockError, operation_lock
from validation.report import compose_validation_report
from validation.targeted_refresh import (
    TargetedRefreshError,
    refresh_confirmed_provenance,
)

from .context import LogContext, resolve_project_root
from .model import ActionError
from .reproduction_comparison import (
    ArtifactComparison,
    ExecutionComparison,
    prepare_confirmation_updates_locked,
)
from .reproduction_contract import ReproductionPlan
from .reproduction_paths import project_tmp_relative
from .reproduction_planner import (
    project_reproduction_state,
    verify_reproduction_publication_snapshot,
)
from .reproduction_results import (
    OUTCOMES,
    ArtifactResult,
    ComparisonRecord,
    ReproductionResults,
    RunFolder,
    RunResult,
    compose_reproduction_report,
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
    validation: MechanicalGeneratedRecord


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


def publish_completed_reproduction(
    log: LogContext,
    request: CompletedPublication,
) -> PublishedReproduction:
    """Publish one normally completed target as one rollback-safe transaction."""

    project_root = resolve_project_root(log.root)
    artifacts = _artifact_results(request)
    run = _run_result(
        request.plan, artifacts, request, project_root
    )
    try:
        with operation_lock(log.root, "reproduction-publication.lock"):
            verify_reproduction_publication_snapshot(log, request.plan)
            confirmations = prepare_confirmation_updates_locked(
                log,
                request.plan,
                request.comparisons,
                project_root=project_root,
                verify_snapshot=False,
            )
            validation = _load_validation(log)
            _require_admissible_validation(log, validation)
            if confirmations.states:
                try:
                    validation = refresh_confirmed_provenance(
                        log.summary,
                        validation,
                        confirmations.states,
                        confirmations.execution_ids,
                        result_date=request.finished_at[:10],
                    )
                except TargetedRefreshError as error:
                    raise ActionError(
                        "reproduction.validation.refresh_failed", str(error)
                    ) from error
            result_path = log.root / "reproduction" / "results.json"
            summary = log.summary.resolve().relative_to(project_root).as_posix()
            current = load_results_or_empty(
                result_path, summary=summary, updated_at=request.finished_at
            )
            current = reconcile_run_folders(current, project_root=project_root)
            state_projection = project_reproduction_state(log)
            reachable = set(state_projection.reachable)
            if request.plan.target.get("kind") == "log":
                reachable = {
                    (_required(case, "entry"), _required(case, "artifact"))
                    for case in request.plan.cases
                }
            merged = merge_reproduction_results(
                current,
                artifacts,
                run,
                updated_at=request.finished_at,
                reachable=reachable,
            )
            projected, currentness = project_current_results(
                merged, state_projection
            )
            context = load_report_context(log.summary)
            report = compose_reproduction_report(
                projected,
                context=context,
                currentness=currentness,
                folder_links_from=log.root,
            )
            updates: dict[Path, str | None] = {
                **confirmations.files,
                result_path: merged.serialized(),
                log.root / "reproduction.md": report,
            }
            if confirmations.states:
                updates[log.root / "validation" / "results.json"] = (
                    validation.canonical_json() + "\n"
                )
                updates[log.root / "validation.md"] = compose_validation_report(
                    validation, context=context
                )
            verify_reproduction_publication_snapshot(log, request.plan)
            atomic_write_texts(updates)
    except (OperationLockError, OSError, PublicationError) as error:
        raise ActionError("reproduction.publication.failed", str(error)) from error
    return PublishedReproduction(merged, report, validation)


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
        raise ActionError(
            "reproduction.publication.invalid", "invalid artifact reason"
        )
    comparison_key = (entry, cast(str, execution), artifact)
    comparison = compared.get(comparison_key)
    outcome, reason, details = _case_outcome(
        case, execution, comparison, skipped
    )
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
        )
    except (DataContractError, ValueError) as error:
        raise ActionError("reproduction.publication.invalid", str(error)) from error


def _run_result(
    plan: ReproductionPlan,
    artifacts: Sequence[ArtifactResult],
    request: CompletedPublication,
    project_root: Path,
) -> RunResult:
    try:
        folder = project_tmp_relative(request.run_folder, project_root)
    except OSError as error:
        raise ActionError(
            "reproduction.run.path_invalid", "run folder is outside the project"
        ) from error
    counts = Counter(item.outcome for item in artifacts)
    return RunResult(
        request.run_id,
        plan.target,
        plan.include_slow,
        "complete",
        request.accepted_at,
        request.finished_at,
        {outcome: counts[outcome] for outcome in OUTCOMES},
        RunFolder(folder, "available"),
    )


def _load_validation(log: LogContext) -> MechanicalGeneratedRecord:
    path = log.root / "validation" / "results.json"
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.validation.missing", f"missing validation result: {path}"
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
    if any(
        check.status in {CheckStatus.FAIL, CheckStatus.UNAVAILABLE}
        for check in record.checks
        if check.scope in {CheckScope.CONFORMANCE, CheckScope.EVIDENCE}
    ) or provenance_artifact_counts(record)[CheckStatus.FAIL.value]:
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
