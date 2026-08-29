"""Public lifecycle owner for one mechanical research-log validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .engine import CACHE_SCHEMA, mechanical_policy
from .json_codec import V2JsonError, decode_json
from .mechanical import MechanicalEvaluationRequest, evaluate_mechanical
from .mechanical_results import CompletionState, MechanicalGeneratedRecord
from .records import RecordPublicationError, publish_validation_outputs
from .report import compose_validation_report

RESULT_SCHEMA = "research-log-validation-result/1"
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_UPGRADE_PATHS = 10_000
UPGRADE_TRANSACTION_DIRECTORY = "validation/.cache/upgrade-transactions"
LEGACY_PATHS = (
    "validation/manifest.json",
    "validation/outcomes",
    "validation/judgments",
    "validation/failures",
    "validation/.cache/cache.json",
    "validation/.cache/subject-index.json",
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
LEGACY_REPORT_MARKERS = (
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
        jobs: Positive worker bound passed to the mechanical engine.
        publish: Whether to publish completed generated state.
        recompute: Whether to bypass all prior mechanical-cache reuse.
    """

    summary: Path
    result_date: str | None = None
    jobs: int = 8
    publish: bool = True
    recompute: bool = False


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Evaluate one log and optionally publish its generated mechanical bundle."""

    if request.summary.is_symlink():
        raise ValidationControllerError(
            f"summary must not be a symlink: {request.summary}"
        )
    summary = request.summary.resolve()
    _validate_request(summary, request.jobs)
    cutover = _cutover_state(summary)
    if cutover is not None:
        return cutover
    result_date = _result_date(request.result_date)
    log_root = summary.with_suffix("")
    prior_cache = (
        None
        if request.recompute
        else _load_cache(log_root / "validation" / ".cache" / "mechanical.json")
    )
    evaluation = evaluate_mechanical(
        MechanicalEvaluationRequest(
            summary,
            result_date,
            jobs=request.jobs,
            prior_cache=prior_cache,
        ),
        mechanical_policy(),
    )
    record = evaluation.result
    if not isinstance(record, MechanicalGeneratedRecord):
        raise ValidationControllerError("mechanical engine returned an invalid record")
    result = _completed_result(record, evaluation.metrics, published=False)
    if not request.publish or record.completion is CompletionState.INCOMPLETE:
        return result
    cache = evaluation.scan.get("cache")
    if not isinstance(cache, Mapping) or cache.get("schema") != CACHE_SCHEMA:
        raise ValidationControllerError("mechanical engine returned an invalid cache")
    outputs = {
        "validation/mechanical.json": (record.canonical_json() + "\n").encode(),
        "validation/.cache/mechanical.json": _json_bytes(cache),
        "validation.md": compose_validation_report(record).encode(),
    }
    try:
        publish_validation_outputs(
            log_root,
            outputs,
            validate_current=lambda: _require_cutover_clear(summary),
        )
    except (OSError, RecordPublicationError) as exc:
        raise ValidationControllerError(str(exc)) from exc
    return _completed_result(record, evaluation.metrics, published=True)


def _validate_request(summary: Path, jobs: int) -> None:
    if summary.is_symlink() or not summary.is_file():
        raise ValidationControllerError(
            f"summary must be a regular non-symlink file: {summary}"
        )
    if jobs < 1:
        raise ValidationControllerError("jobs must be a positive integer")
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


def _cutover_state(summary: Path) -> dict[str, Any] | None:
    log_root = summary.with_suffix("")
    _require_no_pending_upgrade(log_root)
    evidence_csv = _bounded_legacy_evidence(log_root)
    legacy_state = [
        relative for relative in LEGACY_PATHS if (log_root / relative).exists()
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
        if any(marker in prefix for marker in LEGACY_REPORT_MARKERS):
            legacy_state.append("validation.md")
    legacy_state = sorted(set(legacy_state))
    if not evidence_csv and not legacy_state:
        return None
    return {
        "code": "validation.upgrade_required",
        "observed": {
            "evidence_csv": evidence_csv,
            "legacy_generated_state": legacy_state,
        },
        "published": False,
        "schema": RESULT_SCHEMA,
        "status": "upgrade_required",
        "summary": summary.as_posix(),
    }


def _require_no_pending_upgrade(log_root: Path) -> None:
    pending = log_root / UPGRADE_TRANSACTION_DIRECTORY
    try:
        blocked = pending.is_symlink() or (
            pending.exists()
            and (
                not pending.is_dir()
                or next(pending.iterdir(), None) is not None
            )
        )
    except OSError as exc:
        raise ValidationControllerError(
            f"upgrade.recovery.required: could not inspect {pending}: {exc}"
        ) from exc
    if blocked:
        raise ValidationControllerError(
            f"upgrade.recovery.required: recover the interrupted transaction "
            f"under {pending} before validation"
        )


def _bounded_legacy_evidence(log_root: Path) -> list[str]:
    paths: list[str] = []
    for path in log_root.rglob("evidence.csv"):
        if path.is_file():
            paths.append(path.relative_to(log_root).as_posix())
            if len(paths) > MAX_UPGRADE_PATHS:
                raise ValidationControllerError(
                    f"legacy evidence inventory exceeds {MAX_UPGRADE_PATHS} paths"
                )
    return sorted(paths)


def _require_cutover_clear(summary: Path) -> None:
    state = _cutover_state(summary)
    if state is not None:
        raise ValidationControllerError(
            "research log became upgrade-required during validation: "
            + json.dumps(state["observed"], sort_keys=True)
        )


def _load_cache(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_CACHE_BYTES:
            return None
        value = decode_json(
            raw.decode("utf-8"),
            maximum_bytes=MAX_CACHE_BYTES,
            subject="mechanical cache",
        )
    except (OSError, UnicodeError, V2JsonError):
        return None
    if not isinstance(value, Mapping) or value.get("schema") != CACHE_SCHEMA:
        return None
    return value


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
