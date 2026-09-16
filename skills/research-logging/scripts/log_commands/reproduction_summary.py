"""Derived reproduction counts shared by saved inspection and current preview."""

from __future__ import annotations

from collections import Counter
from typing import Mapping

from .reproduction_domain import Classification, WorkSelection
from .reproduction_saved_run import SavedRun
from .reproduction_work import CommandWork


def saved_counts(run: SavedRun) -> dict[str, object]:
    """Count complete target facts through the same classifier used by lists."""

    commands = [run.command_classification(work.identity) for work in run.commands]
    artifacts = [run.artifact_classification(work.identity) for work in run.artifacts]
    command_statuses = Counter(item.status for item in commands)
    artifact_statuses = Counter(item.status for item in artifacts)
    not_run = _reasons(commands, "not-run")
    return {
        "commands": {
            "total": len(commands),
            "not_run": {
                "total": command_statuses["not-run"],
                "not_needed": not_run.get("not-needed", 0),
                "previous_failure": not_run.get("previous-failure", 0),
                "previous_block": not_run.get("previous-block", 0),
            },
            "skipped_by_policy": command_statuses["skipped-by-policy"],
            "selected": {
                "total": sum(
                    command_statuses[key] for key in ("succeeded", "failed", "blocked")
                ),
                "succeeded": command_statuses["succeeded"],
                "failed": {
                    "total": command_statuses["failed"],
                    "reasons": _reasons(commands, "failed"),
                },
                "blocked": {
                    "total": command_statuses["blocked"],
                    "reasons": _reasons(commands, "blocked"),
                },
            },
        },
        "artifacts": {
            "total": len(artifacts),
            "matched": artifact_statuses["matched"],
            "not_matched": artifact_statuses["not-matched"],
            "not_compared": {
                "total": artifact_statuses["not-compared"],
                "reasons": _reasons(artifacts, "not-compared"),
            },
        },
    }


def plan_selection_counts(commands: tuple[CommandWork, ...]) -> Mapping[str, int]:
    """Count preparation leaves; never predict success or artifact matches."""

    counts = Counter(work.selection for work in commands)
    return {
        "total": len(commands),
        "ready_to_run": counts[WorkSelection.RUN],
        "blocked": counts[WorkSelection.BLOCKED],
        "not_needed": counts[WorkSelection.NOT_NEEDED],
        "previous_failure": counts[WorkSelection.PREVIOUS_FAILURE],
        "previous_block": counts[WorkSelection.PREVIOUS_BLOCK],
        "skipped_by_policy": counts[WorkSelection.SKIPPED_BY_POLICY],
    }


def _reasons(items: list[Classification], status: str) -> dict[str, int]:
    counts = Counter(
        item.reason
        for item in items
        if item.status == status and item.reason is not None
    )
    return {key: counts[key] for key in sorted(counts)}
