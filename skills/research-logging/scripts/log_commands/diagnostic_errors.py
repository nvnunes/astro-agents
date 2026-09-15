"""Retain authoring diagnostics for bounded, read-only text inspection."""

from __future__ import annotations

import shlex
import sys

from research_log_result_store import ResultStoreError
from validation.command_diagnostics import publish_command_diagnostic

from .model import ActionError


def report_diagnostic(error: ActionError) -> None:
    """Expose the failed discovery snapshot without rerunning or asserting validation.

    Only the latest diagnostic per log is retained. Cache failures preserve the
    original error and never fall back to dumping its complete payload.
    """
    if error.diagnostic_log is None:
        print("Diagnostic not cached: log context unavailable.", file=sys.stderr)
        return
    try:
        identity = publish_command_diagnostic(
            error.diagnostic_log,
            error.diagnostic_log.name + ".md",
            error.code,
            error.records,
        )
    except (OSError, ResultStoreError, ValueError):
        return
    else:
        print(f"Diagnostic: {identity} (authoring failure; no validation performed)")
        print(
            "Inspect: log command show --path "
            f"{shlex.quote(str(error.diagnostic_log))} --id {shlex.quote(identity)}"
        )
