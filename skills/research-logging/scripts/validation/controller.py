"""Public lifecycle owner for one mechanical research-log validation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .batch_projection import build_batch_projection
from .engine import RULES_VERSION, mechanical_policy
from .fingerprint_cache import FingerprintCache, FingerprintCacheError, project_root
from .human_projection import (
    ReportContext,
    load_report_context,
    project_findings,
)
from .mechanical import (
    MechanicalEvaluation,
    MechanicalEvaluationRequest,
    evaluate_mechanical,
)
from .mechanical_results import CompletionState, MechanicalGeneratedRecord
from .operation_state import (
    LOCK_OWNER_SCHEMA,
    operation_lock,
    require_mutation_ready,
    research_snapshot,
)
from .records import (
    RecordPublicationError,
    publish_validation_outputs_locked,
)
from .report import (
    compose_blocked_validation_report,
    compose_validation_command_report,
    compose_validation_report,
)
from .validation_cache import ValidationCache, ValidationCacheError

RESULT_SCHEMA = "research-log-validation-result/1"
UNSUPPORTED_GENERATED_PATHS = (
    "validation/manifest.json",
    "validation/outcomes",
    "validation/judgments",
    "validation/failures",
    "validation/.cache/cache.json",
    "validation/.cache/subject-index.json",
    "validation/.cache/upgrade-transactions",
    "validation/.cache/index-deltas",
    "validation/.cache/work",
    "validation/.cache/validation.log",
    "validation-decisions.json",
    "validation-state.json",
    "validation-index.json",
    "validation-record.json",
    "validation-cache.json",
    "validation-state",
    ".research-log-validation.lock",
)
UNSUPPORTED_REPORT_MARKERS = (
    "| Entry | Date | Checked | Reproducibility |",
    "## Status Summary",
)


class ValidationControllerError(RuntimeError):
    """Raised when the validation operation cannot complete."""

    def __init__(self, message: str, *, code: str = "validation.failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationRequest:
    """Inputs for one public mechanical validation operation.

    Attributes:
        summary: Maintained research-log summary to validate.
        result_date: Optional ISO calendar date for completed findings.
        publish: Whether to publish completed generated state.
        recompute: Whether to bypass all prior mechanical-cache reuse.
        recompute_validation: Whether to bypass per-log validation-cache reuse.
        recompute_fingerprints: Whether to bypass project fingerprint-cache reuse.
    """

    summary: Path
    result_date: str | None = None
    publish: bool = True
    recompute: bool = False
    recompute_validation: bool = False
    recompute_fingerprints: bool = False


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Evaluate one log and optionally publish its generated mechanical bundle."""

    if request.summary.is_symlink():
        raise ValidationControllerError(
            f"summary must not be a symlink: {request.summary}"
        )
    from .inspection import retain_result, timestamp

    started_at = timestamp()
    requested_summary = request.summary.absolute()
    requested_log_root = requested_summary.with_suffix("")
    starting_snapshot: tuple[tuple[str, tuple[int, ...]], ...] | None = None

    def validation_owner() -> Mapping[str, object]:
        nonlocal starting_snapshot
        starting_snapshot = research_snapshot(requested_summary)
        request_projection = {
            "publish": request.publish,
            "recompute": request.recompute,
            "recompute_fingerprints": request.recompute_fingerprints,
            "recompute_validation": request.recompute_validation,
            "result_date": request.result_date,
            "summary": requested_summary.as_posix(),
        }
        return {
            "log": requested_log_root.resolve().as_posix(),
            "operation": "validation",
            "pid": os.getpid(),
            "publication": request.publish,
            "request_fingerprint": _value_digest(request_projection),
            "schema": LOCK_OWNER_SCHEMA,
            "source_fingerprint": _value_digest(starting_snapshot),
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    try:
        with operation_lock(
            requested_log_root,
            "log.lock",
            mode="exclusive",
            owner_factory=validation_owner,
        ):
            require_mutation_ready(requested_log_root)
            summary = requested_summary.resolve()
            _validate_request(summary)
            unsupported = _unsupported_metadata_state(summary)
            if unsupported is not None:
                context = load_report_context(summary)
                unsupported["report"] = compose_blocked_validation_report(
                    context.title,
                    _unsupported_report_explanation(unsupported),
                )
                return unsupported
            result_date = _result_date(request.result_date)
            log_root = summary.with_suffix("")
            if log_root != requested_log_root.resolve():
                raise ValidationControllerError(
                    "research-log root changed while acquiring its operation lock"
                )
            result = _run_validation(
                request,
                summary,
                log_root,
                result_date,
                starting_snapshot=starting_snapshot,
            )
            if request.publish and "record" in result:
                result["_inspection_id"] = retain_result(
                    summary, result, result["record"], result["_batch_projection"],
                    {"kind": "full", "started_at": started_at},
                )
            return result
    except (
        FingerprintCacheError,
        OSError,
        RecordPublicationError,
        ValidationCacheError,
    ) as error:
        raise ValidationControllerError(
            str(error), code=str(getattr(error, "code", "validation.failed"))
        ) from error


def evaluate_current_record(
    summary: Path, *, result_date: str
) -> MechanicalGeneratedRecord:
    """Evaluate one current record without locking, publication, or cache writes.

    This narrow service lets a read-only consumer prove that a published result
    still describes the exact current research source.  The caller must guard
    the complete source snapshot against concurrent change.
    """

    summary = summary.resolve()
    _validate_request(summary)
    unsupported = _unsupported_metadata_state(summary)
    if unsupported is not None:
        raise ValidationControllerError(
            "generated metadata requires Repair before currentness evaluation"
        )
    result = _run_validation(
        ValidationRequest(summary, result_date=result_date, publish=False),
        summary,
        summary.with_suffix(""),
        _result_date(result_date),
    )
    raw = result.get("record")
    if not isinstance(raw, Mapping):
        raise ValidationControllerError("mechanical validation did not complete")
    return MechanicalGeneratedRecord.from_dict(raw)


def evaluate_entries_record(
    summary: Path, *, result_date: str, entry_ids: frozenset[str]
) -> MechanicalEvaluation[MechanicalGeneratedRecord]:
    """Evaluate a nonempty entry set once with shared read-only observations.

    The caller owns coverage and source reconciliation. This function does not
    publish, acquire an operation lock, or write evaluation caches.
    """
    if not entry_ids:
        raise ValidationControllerError("a bounded entry set is required")

    summary = summary.resolve()
    _validate_request(summary)
    unsupported = _unsupported_metadata_state(summary)
    if unsupported is not None:
        raise ValidationControllerError(
            "generated metadata requires Repair before batch evaluation"
        )
    with FingerprintCache(
        project_root(summary), writable=False, reuse=True
    ) as fingerprints:
        with ValidationCache(
            summary.with_suffix(""), writable=False, reuse=True
        ) as checks:
            evaluation = evaluate_mechanical(
                MechanicalEvaluationRequest(
                    summary,
                    _result_date(result_date),
                    fingerprint_cache=fingerprints,
                    validation_cache=checks,
                    entry_ids=entry_ids,
                ),
                mechanical_policy(),
            )
    if not isinstance(evaluation.result, MechanicalGeneratedRecord):
        raise ValidationControllerError("mechanical batch evaluation did not complete")
    return evaluation


def _run_validation(
    request: ValidationRequest,
    summary: Path,
    log_root: Path,
    result_date: str,
    *,
    starting_snapshot: tuple[tuple[str, tuple[int, ...]], ...] | None = None,
) -> dict[str, Any]:
    """Evaluate under the caller-owned publication lifecycle."""

    if starting_snapshot is None:
        starting_snapshot = research_snapshot(summary)
    report_context = load_report_context(summary)
    recompute_validation = request.recompute or request.recompute_validation
    recompute_fingerprints = request.recompute or request.recompute_fingerprints
    with FingerprintCache(
        project_root(summary),
        writable=request.publish,
        reuse=not recompute_fingerprints,
    ) as fingerprint_cache:
        with ValidationCache(
            log_root,
            writable=request.publish,
            reuse=not recompute_validation,
        ) as validation_cache:
            report_identity = (
                None
                if recompute_validation
                else _current_report_identity(log_root, fingerprint_cache)
            )
            prior_checks = validation_cache.load_check_comparison(
                rules_version=RULES_VERSION,
                report_sha256=report_identity,
            )
            evaluation = evaluate_mechanical(
                MechanicalEvaluationRequest(
                    summary,
                    result_date,
                    fingerprint_cache=fingerprint_cache,
                    validation_cache=validation_cache,
                    check_comparison=prior_checks,
                ),
                mechanical_policy(),
            )
            record = evaluation.result
            if not isinstance(record, MechanicalGeneratedRecord):
                raise ValidationControllerError(
                    "mechanical engine returned an invalid record"
                )
            projection = build_batch_projection(
                record,
                invocations=evaluation.scan["invocations"],
                registries=evaluation.scan["registries"],
                source_identity=_value_digest(starting_snapshot),
            )
            if not request.publish or record.completion is CompletionState.INCOMPLETE:
                return _completed_result(
                    record,
                    evaluation.metrics,
                    published=False,
                    report_context=report_context,
                    batch_projection=projection,
                )
            finding_groups = project_findings(record, report_context)
            mechanical = (record.canonical_json() + "\n").encode()
            mechanical_digest = hashlib.sha256(mechanical).hexdigest()
            projection_bytes = (
                json.dumps(
                    projection,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            outputs = {
                "validation/batches.json": projection_bytes,
                "validation.md": compose_validation_report(
                    record, context=report_context, groups=finding_groups
                ).encode(),
            }
            mechanical_changed = report_identity != mechanical_digest
            if mechanical_changed:
                outputs["validation/results.json"] = mechanical
            published_identities = publish_validation_outputs_locked(
                log_root,
                outputs,
                validate_current=lambda: _require_publication_state(
                    summary,
                    fingerprint_cache,
                    starting_snapshot=starting_snapshot,
                    unchanged_report_sha256=(
                        None if mechanical_changed else mechanical_digest
                    ),
                ),
            )
            if mechanical_changed:
                fingerprint_cache.remember_regular_file(
                    log_root / "validation" / "results.json",
                    digest=mechanical_digest,
                    expected_size=len(mechanical),
                    expected_identity=published_identities["validation/results.json"],
                )
            validation_cache.finish_published_run(
                record.checks,
                rules_version=RULES_VERSION,
                report_sha256=mechanical_digest,
            )
            metrics = {
                **evaluation.metrics,
                **fingerprint_cache.metrics.as_dict(),
                **validation_cache.metrics.as_dict(),
            }
            return _completed_result(
                record,
                metrics,
                published=True,
                report_context=report_context,
                batch_projection=projection,
            )


def _current_report_identity(
    log_root: Path, fingerprint_cache: FingerprintCache
) -> str | None:
    path = log_root / "validation" / "results.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        observation = fingerprint_cache.observe_regular_file(path)
    except FingerprintCacheError:
        return None
    return observation.fingerprint.digest


def _validate_request(summary: Path) -> None:
    if summary.is_symlink() or not summary.is_file():
        raise ValidationControllerError(
            f"summary must be a regular non-symlink file: {summary}"
        )
    log_root = summary.with_suffix("")
    if log_root.is_symlink() or not log_root.is_dir():
        raise ValidationControllerError(
            f"research-log root must be a regular directory: {log_root}"
        )


def _result_date(value: str | None) -> str:
    if value is None:
        return date.today().isoformat()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationControllerError(
            f"validation date must use YYYY-MM-DD: {value!r}"
        ) from exc
    if parsed.isoformat() != value:
        raise ValidationControllerError(
            f"validation date must use YYYY-MM-DD: {value!r}"
        )
    return value


def _unsupported_metadata_state(summary: Path) -> dict[str, Any] | None:
    log_root = summary.with_suffix("")
    unsupported_state = [
        relative
        for relative in UNSUPPORTED_GENERATED_PATHS
        if (log_root / relative).is_symlink() or (log_root / relative).exists()
    ]
    report = log_root / "validation.md"
    if report.is_file() and not (log_root / "validation/results.json").is_file():
        try:
            with report.open("rb") as handle:
                raw_prefix = handle.read(1024 * 1024 + 1)
            prefix = raw_prefix.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationControllerError(
                f"could not inspect existing validation report: {exc}"
            ) from exc
        if any(marker in prefix for marker in UNSUPPORTED_REPORT_MARKERS):
            unsupported_state.append("validation.md")
    unsupported_state = sorted(set(unsupported_state))
    if not unsupported_state:
        return None
    return {
        "code": "validation.unsupported_metadata",
        "observed": {"paths": unsupported_state},
        "published": False,
        "schema": RESULT_SCHEMA,
        "status": "unsupported_metadata",
        "summary": summary.as_posix(),
    }


def _unsupported_report_explanation(state: Mapping[str, Any]) -> str:
    """Name the bounded generated paths that require Repair."""

    observed = state.get("observed")
    paths = observed.get("paths") if isinstance(observed, Mapping) else None
    if not isinstance(paths, list):
        return "Generated metadata requires Repair before validation can run"
    displayed = ", ".join(f"`{path}`" for path in paths if isinstance(path, str))
    if not displayed:
        return "Generated metadata requires Repair before validation can run"
    return "Generated metadata requires Repair before validation can run: " + displayed


def _require_unsupported_metadata_clear(summary: Path) -> None:
    state = _unsupported_metadata_state(summary)
    if state is not None:
        raise ValidationControllerError(
            "research log acquired unsupported metadata during validation: "
            + json.dumps(state["observed"], sort_keys=True)
        )


def _require_publication_state(
    summary: Path,
    fingerprint_cache: FingerprintCache,
    *,
    starting_snapshot: tuple[tuple[str, tuple[int, ...]], ...] | None,
    unchanged_report_sha256: str | None,
) -> None:
    _require_unsupported_metadata_clear(summary)
    if (
        starting_snapshot is not None
        and research_snapshot(summary) != starting_snapshot
    ):
        raise ValidationControllerError(
            "research-owned state changed during validation"
        )
    if unchanged_report_sha256 is None:
        return
    observed = _current_report_identity(summary.with_suffix(""), fingerprint_cache)
    if observed != unchanged_report_sha256:
        raise ValidationControllerError(
            "authoritative mechanical report changed during validation"
        )


def _completed_result(
    record: MechanicalGeneratedRecord,
    metrics: Mapping[str, Any],
    *,
    published: bool,
    report_context: ReportContext,
    batch_projection: Mapping[str, object],
) -> dict[str, Any]:
    log_root = Path(record.summary).with_suffix("")
    return {
        "metrics": dict(metrics),
        "_batch_projection": dict(batch_projection),
        "published": published,
        "record": record.as_dict(),
        "report": compose_validation_command_report(
            record,
            context=report_context,
            published=published,
            human_report=(log_root / "validation.md").as_posix(),
            mechanical_report=(log_root / "validation" / "results.json").as_posix(),
        ),
        "schema": RESULT_SCHEMA,
        "status": record.completion.value,
        "summary": record.summary,
    }


def _value_digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
