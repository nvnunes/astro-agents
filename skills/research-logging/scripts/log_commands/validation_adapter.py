"""Public validation and discovery adapters for ``scripts/log``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

from research_log_paths import RESULTS_STORE, VALIDATION_REPORT
from validation.controller import (
    EntryValidationRequest,
    ValidationControllerError,
    ValidationRequest,
    validate,
    validate_entry,
)
from validation.discovery import MAX_HEADER_CHARACTERS, discover_summaries
from validation.engine import ENTRY_LIMITATIONS
from validation.mechanical_results import MechanicalGeneratedRecord
from validation.report import (
    BATCH_AREA_NAMES,
    ValidationBatchReportRow,
    batch_area_results,
    compose_validation_batch_report,
    unavailable_explanation,
)

from .context import resolve_entry, resolve_log
from .model import ActionError

COMPLETED_STATUSES = frozenset(
    {"complete_clear", "complete_findings", "unsupported_metadata"}
)
CLI_RESULT_SCHEMA = "research-log-validation-cli-result/1"
ENTRY_RESULT_SCHEMA = "research-log-entry-validation-cli-result/1"
BATCH_RESULT_SCHEMA = "research-log-validation-batch-result/1"
MAX_FAILURE_MESSAGE_BYTES = 2_048


@dataclass(frozen=True)
class _ValidationOutcome:
    result: dict[str, object]
    record: MechanicalGeneratedRecord | None
    projection: Mapping[str, object] | None
    inspection_id: str | None = None


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
    inspection_id = result.pop("_inspection_id", None)
    raw_record = result.get("record")
    raw_projection = result.pop("_batch_projection", None)
    record = (
        MechanicalGeneratedRecord.from_dict(raw_record)
        if isinstance(raw_record, dict)
        else None
    )
    projection = raw_projection if isinstance(raw_projection, Mapping) else None
    return _ValidationOutcome(_public_result(result), record, projection, inspection_id)


def _public_result(result: dict[str, object]) -> dict[str, object]:
    """Bound a completed published result to its generated artifacts."""

    if not result.get("published") or not isinstance(result.get("record"), dict):
        return result
    record = cast(Mapping[str, object], result["record"])
    summary = Path(str(result["summary"]))
    log_root = summary.with_suffix("")
    return {
        "generated": {
            "human": (log_root / VALIDATION_REPORT).as_posix(),
            "mechanical": (log_root / RESULTS_STORE).as_posix(),
        },
        "metrics": result.get("metrics", {}),
        "published": True,
        "report": result["report"],
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
    output_format: str = "text",
    entry: str | None = None,
) -> int:
    """Validate one resolved log or every log beneath an explicit root."""

    if entry is not None:
        return _run_entry_validation(path, root, options, output_format, entry)

    if root is None:
        return _run_one_validation(path, options, output_format)
    return _run_root_validation(root, options, output_format)


def _run_entry_validation(
    path: Path | None, root: Path | None, options: ValidationOptions,
    output_format: str, entry: str,
) -> int:
    if path is None or root is not None:
        raise ActionError("validation.entry.path_required", "--entry requires --path")
    log = resolve_log(path)
    selected = resolve_entry(log, entry)
    try:
        evaluation = validate_entry(EntryValidationRequest(
            log.summary, selected.id, selected.root, options.result_date,
            options.dry_run, options.recompute_validation,
            options.recompute_fingerprints,
        ))
    except ValidationControllerError as error:
        raise ActionError(
            str(getattr(error, "code", "validation.failed")), str(error)
        ) from error
    record = evaluation.record
    result: dict[str, object] = {
        "_record": record.as_dict(),
            "schema": ENTRY_RESULT_SCHEMA,
            "summary": log.summary.as_posix(), "entry": selected.id,
            "status": record.completion.value, "result_date": record.result_date,
            "rules_version": record.rules_version,
            "scopes": record.as_dict().get("scopes", []),
            "metrics": dict(evaluation.metrics), "published": False,
            "scope": {
                "kind": "entry",
                "selected_documents": list(evaluation.context.selected_documents),
                "dependency_entries": list(evaluation.context.dependency_entries),
                "whole_log_evaluated": False,
                "unavailable_whole_log_conclusions": list(ENTRY_LIMITATIONS),
            },
    }
    _print_entry_result(result, evaluation.inspection_id, log.root, output_format)
    return 3 if record.completion.value == "incomplete" else 0


def _run_one_validation(
    path: Path | None, options: ValidationOptions, output_format: str,
) -> int:
    try:
        summary = resolve_log(path).summary
        outcome = _evaluate_validation(summary, options)
        result = outcome.result
    except (ValidationControllerError, ValueError) as error:
        raise ActionError(
            str(getattr(error, "code", "validation.failed")), str(error)
        ) from error
    from .inspection_cli import print_producer

    print_producer(
        {**result, "_inspection_id": outcome.inspection_id},
        summary.with_suffix(""),
        output_format,
    )
    return 0 if str(result.get("status")) in COMPLETED_STATUSES else 3


def _print_entry_result(
    result: dict[str, object], inspection_id: str | None, log_root: Path,
    output_format: str,
) -> None:
    if output_format == "json":
        if inspection_id is None:
            result["record"] = result.pop("_record")
        else:
            result.pop("_record")
            result["result_id"] = inspection_id
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    result.pop("_record")
    print(f"Status: {result['status']}; entry: {result['entry']}; published: false")
    if inspection_id:
        print(
            f"Result: {inspection_id}\nInspect: log results show "
            f"--path {log_root} --id {inspection_id}"
        )
    print("Whole-log conclusions unavailable: " + ", ".join(ENTRY_LIMITATIONS))


def _run_root_validation(
    root: Path, options: ValidationOptions, output_format: str,
) -> int:

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
    receipts: list[str] = []
    for summary in summaries:
        title = summary.stem
        try:
            title = _summary_title(summary)
            outcome = _evaluate_validation(summary, options)
            results.append(outcome.result)
            receipts.append(
                f"{summary}: {outcome.inspection_id or 'result not cached'}"
            )
            report_rows.append(_batch_row(title, summary, outcome))
        except (OSError, UnicodeError, ValidationControllerError, ValueError) as error:
            failure: dict[str, object] = {
                "code": str(getattr(error, "code", "validation.failed")),
                "message": _bounded_failure_message(error),
                "summary": summary.resolve().as_posix(),
            }
            failures.append(failure)
            report_rows.append(
                _blocked_batch_row(
                    title,
                    summary,
                    "Validation could not start: " + str(failure["message"]),
                )
            )
    report = compose_validation_batch_report(report_rows)
    _print_root_result(root, results, failures, (report, receipts), output_format)
    statuses = {str(result.get("status")) for result in results}
    return 0 if not failures and statuses <= COMPLETED_STATUSES else 3


def _bounded_failure_message(error: Exception) -> str:
    """Return one UTF-8-safe operational message within the batch bound."""

    encoded = str(error).encode("utf-8")
    if len(encoded) <= MAX_FAILURE_MESSAGE_BYTES:
        return str(error)
    suffix = b"..."
    return (
        encoded[: MAX_FAILURE_MESSAGE_BYTES - len(suffix)].decode(
            "utf-8", errors="ignore"
        )
        + suffix.decode()
    )


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


def _batch_row(
    title: str,
    summary: Path,
    outcome: _ValidationOutcome,
) -> ValidationBatchReportRow:
    """Project one structured per-log result into a complete batch row."""

    if outcome.record is None:
        return _blocked_batch_row(
            title,
            summary,
            "Validation could not start because generated metadata requires Repair",
        )
    if outcome.projection is None:
        return _blocked_batch_row(
            title,
            summary,
            "Validation did not produce a batch projection",
        )
    log_root = summary.with_suffix("")
    return ValidationBatchReportRow(
        title=title,
        summary=summary.resolve().as_posix(),
        human_report=(log_root / "validation.md").resolve().as_posix(),
        mechanical_report=(log_root / RESULTS_STORE)
        .resolve()
        .as_posix(),
        published=bool(outcome.result.get("published")),
        areas=batch_area_results(outcome.record, outcome.projection),
        explanation=unavailable_explanation(outcome.record),
    )


def _blocked_batch_row(
    title: str,
    summary: Path,
    explanation: str,
) -> ValidationBatchReportRow:
    """Project one blocked or failed log without inventing area results."""

    log_root = summary.with_suffix("")
    return ValidationBatchReportRow(
        title=title,
        summary=summary.resolve().as_posix(),
        human_report=(log_root / "validation.md").resolve().as_posix(),
        mechanical_report=(log_root / RESULTS_STORE)
        .resolve()
        .as_posix(),
        published=False,
        areas={name: "—" for name in BATCH_AREA_NAMES},
        explanation=explanation,
    )


def _print_root_result(
    root: Path,
    results: list[dict[str, object]],
    failures: list[dict[str, object]],
    presentation: tuple[str, list[str]],
    output_format: str,
) -> None:
    report, receipts = presentation
    if output_format == "json":
        print(
            json.dumps(
                {
                    "failures": failures,
                    "report": report,
                    "results": results,
                    "root": root.resolve().as_posix(),
                    "schema": BATCH_RESULT_SCHEMA,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(report)
        print("\n".join(receipts))
