"""Execution adapter for the public ``log validate run`` operation."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, cast

from research_log_result_store import ResultStoreError
from validation.controller import (
    EntryValidationRequest,
    ValidationControllerError,
    ValidationRequest,
    validate,
    validate_entry,
)
from validation.discovery import discover_summaries
from validation.domain import SnapshotOutcome, ValidationAttempt, ValidationSnapshot

from .context import LogContext, resolve_entry, resolve_log
from .model import ActionError

RUN_SCHEMA = "research-log-validation-run/1"
ROOT_RUN_SCHEMA = "research-log-validation-root-run/1"
FINDING_TYPES = ("conformance", "evidence", "provenance", "orphan")
MAX_FAILURE_MESSAGE_BYTES = 2_048
MAX_RUN_RESPONSE_BYTES = 16 * 1024


@dataclass(frozen=True)
class ValidationOptions:
    """Evaluation and cache options for one validation run."""

    dry_run: bool = False
    recompute_validation: bool = False
    recompute_fingerprints: bool = False


def run_validation(
    *,
    path: Path | None,
    root: Path | None,
    options: ValidationOptions,
    entry: str | None = None,
) -> tuple[dict[str, object], int]:
    """Evaluate one explicit selection and return its bounded public projection."""

    if root is not None:
        if path is not None or entry is not None:
            raise ActionError(
                "validation.selection.invalid",
                "--root cannot be combined with --path or --entry",
            )
        return _run_root(root, options)
    log = resolve_log(path)
    return _run_log(log, options, entry)


def run_discover(root: Path) -> int:
    """Print the bounded maintained-summary inventory."""

    try:
        result = discover_summaries(root)
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _run_log(
    log: LogContext,
    options: ValidationOptions,
    entry: str | None,
) -> tuple[dict[str, object], int]:
    try:
        if entry is None:
            result = validate(
                ValidationRequest(
                    log.summary,
                    publish=not options.dry_run,
                    recompute_validation=options.recompute_validation,
                    recompute_fingerprints=options.recompute_fingerprints,
                )
            )
            attempt = result.attempt
            snapshot = result.snapshot
            saved = result.published
        else:
            selected = resolve_entry(log, entry)
            evaluated = validate_entry(
                EntryValidationRequest(
                    log.summary,
                    selected.id,
                    selected.root,
                    dry_run=options.dry_run,
                    recompute_validation=options.recompute_validation,
                    recompute_fingerprints=options.recompute_fingerprints,
                )
            )
            attempt = evaluated.evaluation.attempt
            snapshot = evaluated.stored_snapshot or evaluated.evaluation.snapshot
            saved = evaluated.snapshot_id is not None
    except (ValidationControllerError, ValueError) as error:
        raise ActionError(
            str(getattr(error, "code", "validation.failed")), str(error)
        ) from error
    value = _run_projection(log, attempt, snapshot, saved=saved, entry=entry)
    return value, 3 if snapshot.outcome is SnapshotOutcome.FAILED else 0


def _run_root(
    root: Path,
    options: ValidationOptions,
) -> tuple[dict[str, object], int]:
    try:
        discovered = discover_summaries(root)
        summaries: Sequence[Path] = tuple(
            Path(value) for value in cast(Sequence[str], discovered["summaries"])
        )
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error
    rows: list[dict[str, object]] = []
    for summary in summaries:
        log = LogContext(summary.resolve(), summary.with_suffix("").resolve())
        try:
            row, _ = _run_log(log, options, None)
        except (ActionError, OSError, ResultStoreError, UnicodeError) as error:
            rows.append(
                {
                    "error": {
                        "code": str(getattr(error, "code", "validation.failed")),
                        "message": _bounded_message(error),
                    },
                    "log": str(log.root),
                    "outcome": "error",
                    "saved": False,
                    "target": {"kind": "log", "log": str(log.summary)},
                }
            )
        else:
            rows.append(row)
    counts = {
        outcome: sum(row["outcome"] == outcome for row in rows)
        for outcome in ("clear", "findings", "failed", "error")
    }
    value: dict[str, object] = {
        "clear_count": counts["clear"],
        "error_count": counts["error"],
        "failed_count": counts["failed"],
        "findings_count": counts["findings"],
        "root": str(root.resolve()),
        "rows": rows,
        "schema": ROOT_RUN_SCHEMA,
    }
    _require_run_budget(value)
    return value, 2 if counts["error"] else 3 if counts["failed"] else 0


def _run_projection(
    log: LogContext,
    attempt: ValidationAttempt,
    snapshot: ValidationSnapshot,
    *,
    saved: bool,
    entry: str | None,
) -> dict[str, object]:
    findings = snapshot.findings
    outcome = snapshot.outcome.value
    counts = dict.fromkeys(FINDING_TYPES, 0)
    for finding in findings:
        counts[finding.type.value] += 1
    value: dict[str, object] = {
        "finding_counts_by_type": counts,
        "log": str(log.root),
        "outcome": outcome,
        "saved": saved,
        "schema": RUN_SCHEMA,
        "target": attempt.target.as_dict(),
    }
    value["batch_count"] = len(snapshot.batches)
    value["blocked_check_count"] = len(snapshot.blocked_checks)
    value["failed_check_count"] = len(snapshot.failed_checks)
    if saved and snapshot.stored_at is not None:
        value["saved_at"] = snapshot.stored_at
    value["next_command"] = _next_command(log, outcome, entry, saved)
    if entry is None and saved:
        value["report_materialization"] = "current"
    _require_run_budget(value)
    return value


def _next_command(
    log: LogContext,
    outcome: str,
    entry: str | None,
    saved: bool,
) -> str:
    path = shlex.quote(str(log.root))
    entry_option = "" if entry is None else " --entry " + shlex.quote(entry)
    if saved and outcome == "findings":
        return f"log validate list batches --path {path}{entry_option}"
    if saved and outcome == "failed":
        return f"log validate list failed --path {path}{entry_option}"
    if saved:
        return f"log validate show --path {path}"
    return f"log validate run --path {path}{entry_option}"


def _bounded_message(error: Exception) -> str:
    encoded = str(error).encode("utf-8")
    if len(encoded) <= MAX_FAILURE_MESSAGE_BYTES:
        return str(error)
    return encoded[: MAX_FAILURE_MESSAGE_BYTES - 3].decode(
        "utf-8", errors="ignore"
    ) + "..."


def _require_run_budget(value: dict[str, object]) -> None:
    if _response_size(value) > MAX_RUN_RESPONSE_BYTES:
        raise ActionError(
            "validation.response.too_large",
            "validation run response exceeds its 16 KiB UTF-8 budget",
        )


def _response_size(value: dict[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
