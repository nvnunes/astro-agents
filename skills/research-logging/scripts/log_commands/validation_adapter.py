"""Public validation and discovery adapters for ``scripts/log``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

from validation.controller import (
    ValidationControllerError,
    ValidationRequest,
    validate,
)
from validation.discovery import MAX_HEADER_CHARACTERS, discover_summaries
from validation.mechanical_results import MechanicalGeneratedRecord
from validation.report import (
    ValidationBatchReportRow,
    compose_validation_batch_report,
)

from .context import resolve_log
from .model import ActionError

COMPLETED_STATUSES = frozenset(
    {"complete_clear", "complete_findings", "unsupported_metadata"}
)
CLI_RESULT_SCHEMA = "research-log-validation-cli-result/1"
BATCH_RESULT_SCHEMA = "research-log-validation-batch-result/1"
MAX_FAILURE_MESSAGE_BYTES = 2_048


@dataclass(frozen=True)
class _ValidationOutcome:
    result: dict[str, object]
    record: MechanicalGeneratedRecord | None


@dataclass(frozen=True)
class ValidationOptions:
    """Cache and publication options shared by one validation selection."""

    result_date: str | None = None
    dry_run: bool = False
    recompute_validation: bool = False
    recompute_fingerprints: bool = False


def evaluate_validation(
    summary: Path,
    *,
    options: ValidationOptions = ValidationOptions(),
) -> dict[str, object]:
    """Return the bounded public result for one validation request."""

    return _evaluate_validation(summary, options).result


def _evaluate_validation(
    summary: Path,
    options: ValidationOptions,
) -> _ValidationOutcome:
    """Retain the generated record long enough to compose batch reporting."""

    result = validate(
        ValidationRequest(
            summary,
            result_date=options.result_date,
            publish=not options.dry_run,
            recompute_validation=options.recompute_validation,
            recompute_fingerprints=options.recompute_fingerprints,
        )
    )
    raw_record = result.get("record")
    record = (
        MechanicalGeneratedRecord.from_dict(raw_record)
        if isinstance(raw_record, dict)
        else None
    )
    return _ValidationOutcome(_public_result(result), record)


def _public_result(result: dict[str, object]) -> dict[str, object]:
    """Bound a completed published result to its generated artifacts."""

    if not result.get("published") or not isinstance(result.get("record"), dict):
        return result
    record = cast(Mapping[str, object], result["record"])
    summary = Path(str(result["summary"]))
    log_root = summary.with_suffix("")
    return {
        "generated": {
            "human": (log_root / "validation.md").as_posix(),
            "mechanical": (log_root / "validation/results.json").as_posix(),
        },
        "metrics": result.get("metrics", {}),
        "published": True,
        "result_date": record.get("result_date"),
        "rules_version": record.get("rules_version"),
        "schema": CLI_RESULT_SCHEMA,
        "scopes": record.get("scopes", []),
        "status": result["status"],
        "summary": result["summary"],
    }


def run_discover(root: Path) -> int:
    """Print the bounded maintained-summary inventory."""

    try:
        result = discover_summaries(root)
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def run_validate(
    *,
    path: Path | None,
    root: Path | None,
    options: ValidationOptions,
) -> int:
    """Validate one resolved log or every log beneath an explicit root."""

    if root is None:
        try:
            summary = resolve_log(path).summary
            result = evaluate_validation(summary, options=options)
        except (ValidationControllerError, ValueError) as error:
            raise ActionError(
                str(getattr(error, "code", "validation.failed")), str(error)
            ) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if str(result.get("status")) in COMPLETED_STATUSES else 3

    try:
        discovered = discover_summaries(root)
        summaries: Sequence[Path] = tuple(
            Path(value) for value in cast(Sequence[str], discovered["summaries"])
        )
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error

    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    report_rows: list[ValidationBatchReportRow] = []
    for summary in summaries:
        try:
            title = _summary_title(summary)
            outcome = _evaluate_validation(summary, options)
            results.append(outcome.result)
            if outcome.record is not None and str(outcome.result.get("status")) in {
                "complete_clear",
                "complete_findings",
            }:
                log_root = summary.with_suffix("")
                report_rows.append(
                    ValidationBatchReportRow(
                        title,
                        summary.resolve().as_posix(),
                        (log_root / "validation.md").resolve().as_posix(),
                        (
                            log_root / "validation" / "results.json"
                        ).resolve().as_posix(),
                        bool(outcome.result.get("published")),
                        outcome.record,
                    )
                )
        except (OSError, UnicodeError, ValidationControllerError, ValueError) as error:
            failures.append(
                {
                    "code": str(getattr(error, "code", "validation.failed")),
                    "message": _bounded_failure_message(error),
                    "summary": summary.resolve().as_posix(),
                }
            )
    print(
        json.dumps(
            {
                "failures": failures,
                "report": compose_validation_batch_report(report_rows),
                "results": results,
                "root": root.resolve().as_posix(),
                "schema": BATCH_RESULT_SCHEMA,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    statuses = {str(result.get("status")) for result in results}
    return 0 if not failures and statuses <= COMPLETED_STATUSES else 3


def _bounded_failure_message(error: Exception) -> str:
    """Return one UTF-8-safe operational message within the batch bound."""

    encoded = str(error).encode("utf-8")
    if len(encoded) <= MAX_FAILURE_MESSAGE_BYTES:
        return str(error)
    suffix = b"..."
    return encoded[: MAX_FAILURE_MESSAGE_BYTES - len(suffix)].decode(
        "utf-8", errors="ignore"
    ) + suffix.decode()


def _summary_title(summary: Path) -> str:
    """Read the discovery-bounded maintained-summary title."""

    with summary.open(encoding="utf-8") as handle:
        line = handle.readline(MAX_HEADER_CHARACTERS + 1)
    if len(line) > MAX_HEADER_CHARACTERS or not line.startswith("# "):
        raise ValueError(f"maintained summary has no bounded title: {summary}")
    title = line[2:].strip()
    if not title:
        raise ValueError(f"maintained summary has an empty title: {summary}")
    return title
