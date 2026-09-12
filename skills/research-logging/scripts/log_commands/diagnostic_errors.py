"""Retain authoring diagnostics for bounded, read-only text inspection."""

from __future__ import annotations

import shlex
import sys

from validation.result_storage import publish_diagnostic_commands

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
        identity = publish_diagnostic_commands(
            error.diagnostic_log,
            error.diagnostic_log.name + ".md",
            error.code,
            error.records,
        )
    except (OSError, ValueError):
        return
    else:
        path = shlex.quote(str(error.diagnostic_log))
        print(f"Diagnostic: {identity} (authoring failure; no validation performed)")
        print(
            f"Inspect: log results show --path {path} --id {identity} "
            "--view commands --limit 5"
        )
