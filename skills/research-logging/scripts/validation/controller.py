"""Public lifecycle owner for one mechanical research-log validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .engine import RULES_VERSION, mechanical_policy
from .fingerprint_cache import FingerprintCache, FingerprintCacheError, project_root
from .mechanical import MechanicalEvaluationRequest, evaluate_mechanical
from .mechanical_results import CompletionState, MechanicalGeneratedRecord
from .operation_state import operation_lock, require_mutation_ready, research_snapshot
from .records import (
    RecordPublicationError,
    publish_validation_outputs_locked,
)
from .report import compose_validation_report
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
    """

    summary: Path
    result_date: str | None = None
    publish: bool = True
    recompute: bool = False


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Evaluate one log and optionally publish its generated mechanical bundle."""

    if request.summary.is_symlink():
        raise ValidationControllerError(
            f"summary must not be a symlink: {request.summary}"
        )
    requested_summary = request.summary.absolute()
    requested_log_root = requested_summary.with_suffix("")
    try:
        with operation_lock(
            requested_log_root, "log.lock", mode="exclusive"
        ):
            require_mutation_ready(requested_log_root)
            summary = requested_summary.resolve()
            _validate_request(summary)
            unsupported = _unsupported_metadata_state(summary)
            if unsupported is not None:
                return unsupported
            result_date = _result_date(request.result_date)
            log_root = summary.with_suffix("")
            if log_root != requested_log_root.resolve():
                raise ValidationControllerError(
                    "research-log root changed while acquiring its operation lock"
                )
            return _run_validation(
                request,
                summary,
                log_root,
                result_date,
            )
    except (
        FingerprintCacheError,
        OSError,
        RecordPublicationError,
        ValidationCacheError,
    ) as error:
        raise ValidationControllerError(
            str(error), code=str(getattr(error, "code", "validation.failed"))
        ) from error


def _run_validation(
    request: ValidationRequest,
    summary: Path,
    log_root: Path,
    result_date: str,
) -> dict[str, Any]:
    """Evaluate under the caller-owned publication lifecycle."""

    starting_snapshot = research_snapshot(summary) if request.publish else None
    with FingerprintCache(
        project_root(summary),
        writable=request.publish,
        reuse=not request.recompute,
    ) as fingerprint_cache:
        with ValidationCache(
            log_root,
            writable=request.publish,
            reuse=not request.recompute,
        ) as validation_cache:
            report_identity = (
                None
                if request.recompute
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
            result = _completed_result(record, evaluation.metrics, published=False)
            if not request.publish or record.completion is CompletionState.INCOMPLETE:
                return result
            mechanical = (record.canonical_json() + "\n").encode()
            mechanical_digest = hashlib.sha256(mechanical).hexdigest()
            outputs = {
                "validation.md": compose_validation_report(record).encode(),
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
                    expected_identity=published_identities[
                        "validation/results.json"
                    ],
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
            return _completed_result(record, metrics, published=True)


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
) -> dict[str, Any]:
    return {
        "metrics": dict(metrics),
        "published": published,
        "record": record.as_dict(),
        "schema": RESULT_SCHEMA,
        "status": record.completion.value,
        "summary": record.summary,
    }
