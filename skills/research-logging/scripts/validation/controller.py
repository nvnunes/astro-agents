"""Public lifecycle owner for one mechanical research-log validation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from research_log_paths import RESULTS_STORE, VALIDATION_REPORT

from .domain import (
    ValidationAttempt,
    ValidationSnapshot,
)
from .engine import (
    EntryEvaluationTarget,
    EvaluationRequest,
    EvaluationResult,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from .fingerprint_cache import FingerprintCache, FingerprintCacheError, project_root
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
from .validation_cache import ValidationCache, ValidationCacheError

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
        publish: Whether to publish completed generated state.
        recompute: Whether to bypass all prior mechanical-cache reuse.
        recompute_validation: Whether to bypass per-log validation-cache reuse.
        recompute_fingerprints: Whether to bypass project fingerprint-cache reuse.
    """

    summary: Path
    publish: bool = True
    recompute: bool = False
    recompute_validation: bool = False
    recompute_fingerprints: bool = False


@dataclass(frozen=True)
class ValidationControllerOutcome:
    """Canonical evaluation plus controller-owned publication facts."""

    attempt: ValidationAttempt
    snapshot: ValidationSnapshot
    published: bool
    metrics: Mapping[str, object]


def validate(request: ValidationRequest) -> ValidationControllerOutcome:
    """Evaluate one log and optionally publish its completed snapshot."""

    if request.summary.is_symlink():
        raise ValidationControllerError(
            f"summary must not be a symlink: {request.summary}"
        )
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
            log_root = summary.with_suffix("")
            if log_root != requested_log_root.resolve():
                raise ValidationControllerError(
                    "research-log root changed while acquiring its operation lock"
                )
            result = _run_validation(
                request,
                summary,
                log_root,
                starting_snapshot=starting_snapshot,
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


@dataclass(frozen=True)
class EntryValidationRequest:
    """One non-authoritative stable-entry inspection evaluation."""

    summary: Path
    entry_id: str
    entry_root: Path
    dry_run: bool = False
    recompute_validation: bool = False
    recompute_fingerprints: bool = False


@dataclass(frozen=True)
class EntryValidationOutcome:
    """One scoped evaluation and its retained snapshot identity when saved."""

    evaluation: EvaluationResult
    stored_snapshot: ValidationSnapshot | None = None

    @property
    def snapshot_id(self) -> str | None:
        """Return the retained snapshot identity when this result was saved."""

        return (
            None
            if self.stored_snapshot is None
            else self.stored_snapshot.internal_snapshot_id
        )

    @property
    def context(self):
        return self.evaluation.context

    @property
    def metrics(self):
        return self.evaluation.metrics


def validate_entry(request: EntryValidationRequest) -> EntryValidationOutcome:
    """Evaluate exactly one entry under the normal log lock without publishing."""

    summary = request.summary.resolve()
    _validate_request(summary)
    from research_log_result_store import results_lock

    from .snapshot_storage import (
        SnapshotPublicationRequest,
        entry_relations_from_evaluation,
        load_validation_snapshot,
        publish_validation_snapshot,
    )

    starting_snapshot = research_snapshot(summary)
    with operation_lock(summary.with_suffix(""), "log.lock", mode="exclusive"):
        if research_snapshot(summary) != starting_snapshot:
            raise ValidationControllerError(
                "research sources changed before entry validation completed",
                code="validation.source_changed",
            )
        with FingerprintCache(
            project_root(summary),
            writable=False,
            reuse=not request.recompute_fingerprints,
        ) as fingerprints:
            with ValidationCache(
                summary.with_suffix(""),
                writable=False,
                reuse=not request.recompute_validation,
            ) as checks:
                result = evaluate_mechanical(
                    EvaluationRequest(
                        summary,
                        EntryEvaluationTarget(request.entry_id, request.entry_root),
                        fingerprints,
                        checks,
                    )
                )
        if research_snapshot(summary) != starting_snapshot:
            raise ValidationControllerError(
                "research sources changed during entry validation",
                code="validation.source_changed",
            )
        stored_snapshot = None
        if not request.dry_run:
            # The controller owns scoped retention because it owns the lock and
            # the source-stability decision.  A cache write is best effort and
            # never changes the evaluation outcome.
            with results_lock(summary.with_suffix("")):
                publish_validation_snapshot(
                    SnapshotPublicationRequest(
                        summary.with_suffix(""),
                        result.snapshot,
                        entry_relations_from_evaluation(result.context),
                    )
                )
                stored_snapshot = load_validation_snapshot(
                    summary.with_suffix(""), slot=f"entry:{request.entry_id}"
                )
    return EntryValidationOutcome(result, stored_snapshot)


def _run_validation(
    request: ValidationRequest,
    summary: Path,
    log_root: Path,
    *,
    starting_snapshot: tuple[tuple[str, tuple[int, ...]], ...] | None = None,
) -> ValidationControllerOutcome:
    """Evaluate under the caller-owned publication lifecycle."""

    if starting_snapshot is None:
        starting_snapshot = research_snapshot(summary)
    generated_residue = _unsupported_metadata_paths(summary)
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
            evaluation = evaluate_mechanical(
                EvaluationRequest(
                    summary,
                    FullEvaluationTarget(),
                    fingerprint_cache,
                    validation_cache,
                    generated_residue,
                )
            )
            if not request.publish:
                return ValidationControllerOutcome(
                    attempt=evaluation.attempt,
                    snapshot=evaluation.snapshot,
                    published=False,
                    metrics=dict(evaluation.metrics),
                )
            _require_publication_state(
                summary,
                starting_snapshot=starting_snapshot,
            )
            from research_log_result_store import (
                record_report_materialization,
                results_lock,
            )

            from .snapshot_report import compose_snapshot_report
            from .snapshot_storage import (
                SnapshotPublicationRequest,
                entry_relations_from_evaluation,
                load_validation_snapshot,
                publish_validation_snapshot,
            )

            with results_lock(log_root):
                retained = publish_validation_snapshot(
                    SnapshotPublicationRequest(
                        log_root,
                        evaluation.snapshot,
                        entry_relations_from_evaluation(evaluation.context),
                    )
                )
                try:
                    stored_snapshot = load_validation_snapshot(log_root)
                    report_bytes = compose_snapshot_report(stored_snapshot).encode()
                except Exception as error:
                    raise _committed_report_error(
                        "render_failed", retained, error
                    ) from error
                try:
                    publish_validation_outputs_locked(
                        log_root,
                        {VALIDATION_REPORT: report_bytes},
                        validate_current=lambda: (
                            _require_unsupported_metadata_unchanged(
                                summary,
                                generated_residue,
                            )
                        ),
                    )
                except Exception as error:
                    raise _committed_report_error(
                        "write_failed", retained, error
                    ) from error
                try:
                    record_report_materialization(
                        log_root,
                        "validation",
                        report_bytes,
                        expected_generation=retained.generation,
                    )
                except Exception as error:
                    raise _committed_report_error(
                        "write_failed", retained, error
                    ) from error
            validation_cache.finish_published_run()
            metrics = {
                **evaluation.metrics,
                **fingerprint_cache.metrics.as_dict(),
                **validation_cache.metrics.as_dict(),
            }
            return ValidationControllerOutcome(
                attempt=evaluation.attempt,
                snapshot=stored_snapshot,
                published=True,
                metrics=metrics,
            )


def _committed_report_error(
    kind: str, retained: object, error: Exception
) -> ValidationControllerError:
    """Name a report-only failure without recasting its snapshot as failed."""

    snapshot_id = getattr(retained, "snapshot_id", "unknown")
    generation = getattr(retained, "generation", "unknown")
    return ValidationControllerError(
        "validation snapshot committed: "
        f"id={snapshot_id}; generation={generation}; {error}",
        code=f"validation.report.{kind}",
    )


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


def _unsupported_metadata_paths(summary: Path) -> tuple[str, ...]:
    log_root = summary.with_suffix("")
    unsupported_state = [
        relative
        for relative in UNSUPPORTED_GENERATED_PATHS
        if (log_root / relative).is_symlink() or (log_root / relative).exists()
    ]
    report = log_root / VALIDATION_REPORT
    if report.is_file() and not (log_root / RESULTS_STORE).is_file():
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
    return tuple(unsupported_state)


def _require_unsupported_metadata_unchanged(
    summary: Path,
    expected: tuple[str, ...],
) -> None:
    observed = _unsupported_metadata_paths(summary)
    expected_structural = tuple(path for path in expected if path != "validation.md")
    observed_structural = tuple(path for path in observed if path != "validation.md")
    if observed_structural != expected_structural:
        raise ValidationControllerError(
            "generated validation residue changed during validation: "
            + json.dumps(
                {
                    "expected": expected_structural,
                    "observed": observed_structural,
                }
            )
        )


def _require_publication_state(
    summary: Path,
    *,
    starting_snapshot: tuple[tuple[str, tuple[int, ...]], ...] | None,
) -> None:
    if (
        starting_snapshot is not None
        and research_snapshot(summary) != starting_snapshot
    ):
        raise ValidationControllerError(
            "research-owned state changed during validation"
        )


def _value_digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
