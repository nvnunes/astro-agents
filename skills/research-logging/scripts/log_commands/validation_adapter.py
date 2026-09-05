"""Public validation and discovery adapters for ``scripts/log``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence, cast

from validation.controller import (
    ValidationControllerError,
    ValidationRequest,
    validate,
)
from validation.discovery import discover_summaries

from .context import resolve_log
from .model import ActionError

COMPLETED_STATUSES = frozenset(
    {"complete_clear", "complete_findings", "unsupported_metadata"}
)
CLI_RESULT_SCHEMA = "research-log-validation-cli-result/1"


def evaluate_validation(
    summary: Path,
    *,
    result_date: str | None = None,
    dry_run: bool = False,
    recompute: bool = False,
) -> dict[str, object]:
    """Return the bounded public result for one validation request."""

    result = validate(
        ValidationRequest(
            summary,
            result_date=result_date,
            publish=not dry_run,
            recompute=recompute,
        )
    )
    return _public_result(result)


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
    result_date: str | None,
    dry_run: bool,
    recompute: bool,
) -> int:
    """Validate one resolved log or every log beneath an explicit root."""

    try:
        summaries: Sequence[Path]
        if root is not None:
            discovered = discover_summaries(root)
            summaries = tuple(
                Path(value) for value in cast(Sequence[str], discovered["summaries"])
            )
        else:
            summaries = (resolve_log(path).summary,)
        results = [
            evaluate_validation(
                summary,
                result_date=result_date,
                dry_run=dry_run,
                recompute=recompute,
            )
            for summary in summaries
        ]
    except (ValidationControllerError, ValueError) as error:
        raise ActionError(
            str(getattr(error, "code", "validation.failed")), str(error)
        ) from error
    if root is None:
        print(json.dumps(results[0], ensure_ascii=False, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "results": results,
                    "root": root.resolve().as_posix(),
                    "schema": "research-log-validation-batch-result/1",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    statuses = {str(result.get("status")) for result in results}
    return 0 if statuses <= COMPLETED_STATUSES else 3
