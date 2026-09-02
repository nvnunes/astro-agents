"""Public lifecycle owner for one mechanical research-log validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .engine import cache_envelope_supported, mechanical_policy
from .filesystem import BoundedFileReadError, bounded_file_bytes
from .fingerprint_cache import FingerprintCache, FingerprintCacheError, project_root
from .json_codec import V2JsonError, decode_json
from .mechanical import MechanicalEvaluationRequest, evaluate_mechanical
from .mechanical_results import CompletionState, MechanicalGeneratedRecord
from .records import RecordPublicationError, publish_validation_outputs
from .report import compose_validation_report

RESULT_SCHEMA = "research-log-validation-result/1"
MAX_CACHE_BYTES = 32 * 1024 * 1024
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
    summary = request.summary.resolve()
    _validate_request(summary)
    unsupported = _unsupported_metadata_state(summary)
    if unsupported is not None:
        return unsupported
    result_date = _result_date(request.result_date)
    log_root = summary.with_suffix("")
    raw_cache = (
        None
        if request.recompute
        else _load_cache(log_root / "validation" / ".cache" / "mechanical.json")
    )
    prior_cache = raw_cache if cache_envelope_supported(raw_cache) else None
    try:
        with FingerprintCache(
            project_root(summary),
            writable=request.publish,
            reuse=not request.recompute,
        ) as fingerprint_cache:
            if not request.recompute:
                fingerprint_cache.seed_mechanical_cache(raw_cache)
            evaluation = evaluate_mechanical(
                MechanicalEvaluationRequest(
                    summary,
                    result_date,
                    prior_cache=prior_cache,
                    fingerprint_cache=fingerprint_cache,
                ),
                mechanical_policy(),
            )
    except FingerprintCacheError as error:
        raise ValidationControllerError(str(error)) from error
    record = evaluation.result
    if not isinstance(record, MechanicalGeneratedRecord):
        raise ValidationControllerError("mechanical engine returned an invalid record")
    result = _completed_result(record, evaluation.metrics, published=False)
    if not request.publish or record.completion is CompletionState.INCOMPLETE:
        return result
    cache = evaluation.scan.get("cache")
    if not cache_envelope_supported(cache):
        raise ValidationControllerError("mechanical engine returned an invalid cache")
    assert isinstance(cache, Mapping)
    outputs = {
        "validation/mechanical.json": (record.canonical_json() + "\n").encode(),
        "validation/.cache/mechanical.json": _json_bytes(cache),
        "validation.md": compose_validation_report(record).encode(),
    }
    try:
        publish_validation_outputs(
            log_root,
            outputs,
            validate_current=lambda: _require_unsupported_metadata_clear(summary),
        )
    except (OSError, RecordPublicationError) as exc:
        raise ValidationControllerError(str(exc)) from exc
    return _completed_result(record, evaluation.metrics, published=True)


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
    if report.is_file() and not (log_root / "validation/mechanical.json").is_file():
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


def _load_cache(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        raw = bounded_file_bytes(path, maximum_bytes=MAX_CACHE_BYTES)
        value = decode_json(
            raw.decode("utf-8"),
            maximum_bytes=MAX_CACHE_BYTES,
            subject="mechanical cache",
        )
    except (BoundedFileReadError, UnicodeError, V2JsonError):
        return None
    return value if isinstance(value, Mapping) else None


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


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
