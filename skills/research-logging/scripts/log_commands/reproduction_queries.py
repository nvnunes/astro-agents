"""Bounded read-only projections of published reproduction state."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from validation.human_projection import load_report_context

from .context import LogContext, resolve_project_root
from .model import ActionError
from .reproduction_planner import project_reproduction_state
from .reproduction_results import (
    ArtifactCurrentness,
    ReproductionResultError,
    ReproductionResults,
    compose_reproduction_report,
    load_reproduction_results,
    project_current_results,
    query_artifacts,
)

ARTIFACT_LIST_SCHEMA = "research-log-reproduction-artifact-list/1"
ARTIFACT_SHOW_SCHEMA = "research-log-reproduction-artifact/1"


def reproduction_report(log: LogContext, *, entry: str | None) -> str:
    """Return the centralized current human report projection."""

    results, currentness = _current(log)
    return compose_reproduction_report(
        results,
        context=load_report_context(log.summary),
        currentness=currentness,
        entry=entry,
        folder_links_from=Path.cwd(),
    )


def list_reproduction_artifacts(
    log: LogContext,
    *,
    entry: str | None,
    outcome: str | None,
    artifact: str | None,
) -> dict[str, object]:
    """Return at most 50 exact current artifact records."""

    results, currentness = _current(log)
    query = query_artifacts(
        results,
        currentness=currentness,
        entry=entry,
        outcome=outcome,
        artifact=artifact,
    )
    return {
        **query.as_dict(),
        "filters": {"artifact": artifact, "entry": entry, "outcome": outcome},
        "schema": ARTIFACT_LIST_SCHEMA,
        "summary": results.summary,
    }


def show_reproduction_artifact(
    log: LogContext, *, entry: str, artifact: str
) -> dict[str, object]:
    """Return one complete exact current artifact record."""

    results, currentness = _current(log)
    query = query_artifacts(
        results, currentness=currentness, entry=entry, artifact=artifact
    )
    if query.matched == 0:
        raise ActionError(
            "reproduction.artifact.unknown",
            f"published reproduction contains no {entry}:{artifact}",
        )
    if query.matched != 1:
        raise ActionError(
            "reproduction.artifact.ambiguous",
            f"published reproduction contains ambiguous {entry}:{artifact}",
        )
    return {
        "artifact": dict(query.records[0]),
        "schema": ARTIFACT_SHOW_SCHEMA,
        "summary": results.summary,
    }


def _current(
    log: LogContext,
) -> tuple[ReproductionResults, Mapping[tuple[str, str], ArtifactCurrentness]]:
    path = log.root / "reproduction" / "results.json"
    if path.is_symlink() or not path.is_file():
        raise ActionError(
            "reproduction.results.missing", f"no published reproduction result: {path}"
        )
    try:
        results = load_reproduction_results(path)
        expected = resolve_project_root(log.root) / results.summary
        if expected.resolve() != log.summary.resolve():
            raise ReproductionResultError("result summary identity changed")
        state = project_reproduction_state(log)
        return project_current_results(results, state)
    except (OSError, UnicodeError, ValueError) as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
