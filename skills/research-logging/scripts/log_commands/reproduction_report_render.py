"""Recovery rendering for the derived reproduction report."""

from __future__ import annotations

from pathlib import Path

from research_log_result_store import (
    record_report_materialization,
    result_generation,
    results_lock,
)
from validation.operation_state import operation_lock

from .context import LogContext
from .model import ActionError
from .storage import atomic_write_texts


def render_reproduction_report(log: LogContext) -> None:
    """Regenerate ``reproduction.md`` without executing recorded commands."""

    with operation_lock(log.root, "log.lock", mode="exclusive"):
        expected_generation = result_generation(log.root, "reproduction")
        try:
            report = compose_reproduction_render_input(log)
        except Exception as error:
            raise ActionError(
                "reproduction.report.render_failed",
                f"reproduction generation {expected_generation}: {error}",
            ) from error
        with results_lock(log.root):
            identity = f"reproduction generation {expected_generation}"
            if result_generation(log.root, "reproduction") != expected_generation:
                raise ActionError(
                    "reproduction.report.write_failed",
                    f"{identity} changed before report replacement",
                )
            try:
                atomic_write_texts({log.root / "reproduction.md": report})
                record_report_materialization(
                    log.root,
                    "reproduction",
                    report.encode(),
                    expected_generation=expected_generation,
                )
            except Exception as error:
                raise ActionError(
                    "reproduction.report.write_failed", f"{identity}: {error}"
                ) from error


def compose_reproduction_render_input(log: LogContext) -> str:
    """Compose reproduction Markdown before acquiring the result publication lock."""

    from validation.report_context import load_report_context

    from .reproduction_planner import project_reproduction_state
    from .reproduction_result_storage import load_reproduction_report_projection
    from .reproduction_results import (
        compose_reproduction_report,
        project_current_results,
    )

    results = load_reproduction_report_projection(log.root / ".cache/results.sqlite")
    projected, currentness = project_current_results(
        results, project_reproduction_state(log)
    )
    return compose_reproduction_report(
        projected,
        context=load_report_context(log.summary),
        currentness=currentness,
        folder_links_from=Path.cwd(),
    )
