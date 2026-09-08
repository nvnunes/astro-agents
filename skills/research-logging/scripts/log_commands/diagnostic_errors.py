"""Retain authoring diagnostics for bounded, read-only text inspection."""

from __future__ import annotations

import shlex
import sys

from validation.inspection import retain_result, timestamp

from .model import AUTHORING_RESULT_SCHEMA, ActionError


def report_diagnostic(error: ActionError) -> None:
    """Expose the failed discovery snapshot without rerunning or asserting validation.

    Only the latest diagnostic per log is retained. Cache failures preserve the
    original error and never fall back to dumping its complete payload.
    """
    if error.diagnostic_log is None:
        print("Diagnostic not cached: log context unavailable.", file=sys.stderr)
        return
    identity = retain_result(
        error.diagnostic_log.with_name(error.diagnostic_log.name + ".md"),
        {"status": "failed", "reason": error.code, "diagnostics": error.records},
        {"schema": AUTHORING_RESULT_SCHEMA},
        {},
        {"kind": "diagnostic", "started_at": timestamp()},
    )
    if identity is not None:
        path = shlex.quote(str(error.diagnostic_log))
        print(f"Diagnostic: {identity} (authoring failure; no validation performed)")
        print(
            f"Inspect: log results show --path {path} --id {identity} "
            "--view commands --limit 5"
        )
