"""Adapters preserving validation and discovery ownership under ``log``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, cast

from validation.cli import (
    COMPLETED_STATUSES,
    evaluate_validation,
)
from validation.cli import run_discover as run_discovery_cli
from validation.controller import ValidationControllerError
from validation.discovery import discover_summaries

from .context import resolve_log
from .model import ActionError


def run_discover(root: Path) -> int:
    """Print the existing bounded discovery result."""

    try:
        return run_discovery_cli(root)
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error


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
        raise ActionError("validation.failed", str(error)) from error
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
