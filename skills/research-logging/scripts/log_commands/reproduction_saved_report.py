"""Recover summary-only reproduction Markdown from committed native facts."""

from __future__ import annotations

from research_log_paths import REPRODUCTION_REPORT
from research_log_result_store import (
    ResultStoreError,
    record_report_materialization,
    results_lock,
)
from validation.operation_state import operation_lock

from .context import LogContext
from .model import ActionError
from .reproduction_inspection import (
    EmptyInspection,
    SavedInspection,
    load_inspection,
    render_saved_summary,
)
from .storage import PublicationError, atomic_write_texts


def compose_saved_report(inspection: SavedInspection | EmptyInspection) -> str:
    """Add only the Reproduction heading to the exact single-log show body."""

    return "# Reproduction\n\n" + render_saved_summary(inspection)


def materialize_saved_report_locked(log: LogContext) -> str:
    """Write the current native summary and mark its exact committed generation.

    The caller owns the reproduction-publication and results locks. Neither
    rendering nor report-only retry executes, replans or scans live registries.
    A failed write/marker leaves saved facts queryable and recoverable.
    """

    inspection = load_inspection(log)
    report = compose_saved_report(inspection)
    identity = f"committed reproduction generation {inspection.generation}"
    try:
        atomic_write_texts({log.root / REPRODUCTION_REPORT: report})
        marked = record_report_materialization(
            log.root,
            "reproduction",
            report.encode(),
            expected_generation=inspection.generation,
        )
    except (OSError, PublicationError, ResultStoreError) as error:
        raise ActionError(
            "results.report.write_failed", f"{identity}: {error}"
        ) from error
    if not marked:
        raise ActionError(
            "results.report.write_failed",
            f"{identity}; generation changed while results lock was held",
        )
    return report


def render_saved_report(log: LogContext) -> str:
    """Explicit native report recovery; replace no saved outcomes or research files."""

    with operation_lock(log.root, "reproduction-publication.lock"):
        with results_lock(log.root):
            return materialize_saved_report_locked(log)
