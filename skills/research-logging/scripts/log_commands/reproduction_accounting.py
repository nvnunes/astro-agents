"""Shared command-selection accounting for reproduction plans."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .reproduction_contract import ReproductionPlan
from .reproduction_planner import ReproductionCommandInventory


class CommandAccountingError(ValueError):
    """One invalid or irreconcilable command-selection projection."""


@dataclass(frozen=True)
class CommandSelectionAccounting:
    """Exhaustive target counts fixed before command execution."""

    run_keys: frozenset[tuple[str, str, str]]
    not_automatic: int
    reproduction_not_needed: int
    unchanged_failed: int
    unchanged_blocked: int
    blocked: int
    total: int


def project_command_selection(
    plan: ReproductionPlan,
    inventory: ReproductionCommandInventory | None,
) -> CommandSelectionAccounting:
    """Reconcile one plan's run, no-work, block, and policy selections."""

    planned: dict[tuple[str, str, str], bool] = {}
    for item in plan.executions:
        entry = item.get("entry")
        cid = item.get("cid")
        execution_id = item.get("execution_id")
        automatic = item.get("auto_reproduce")
        key = (entry, cid, execution_id)
        if (
            not isinstance(entry, str)
            or not isinstance(cid, str)
            or not isinstance(execution_id, str)
            or not isinstance(automatic, bool)
            or key in planned
        ):
            raise CommandAccountingError("planned command accounting is invalid")
        planned[(entry, cid, execution_id)] = automatic

    snapshots = command_snapshot_index(plan)
    del inventory
    run_keys = frozenset(
        key for key, value in snapshots.items() if value["selection"] == "run"
    )
    if run_keys != set(planned):
        raise CommandAccountingError("command selection does not match accepted plan")
    blocked = sum(value["selection"] == "blocked" for value in snapshots.values())
    unchanged_failed = sum(
        value["selection"] == "unchanged" and value["prior_disposition"] == "failed"
        for value in snapshots.values()
    )
    unchanged_blocked = sum(
        value["selection"] == "unchanged" and value["prior_disposition"] == "blocked"
        for value in snapshots.values()
    )
    not_automatic = sum(value["selection"] == "policy" for value in snapshots.values())
    total = len(snapshots)
    reproduction_not_needed = (
        total
        - not_automatic
        - len(run_keys)
        - blocked
        - unchanged_failed
        - unchanged_blocked
    )
    if not_automatic < 0 or reproduction_not_needed < 0:
        raise CommandAccountingError(
            "command inventory does not reconcile with the accepted plan"
        )
    return CommandSelectionAccounting(
        run_keys,
        not_automatic,
        reproduction_not_needed,
        unchanged_failed,
        unchanged_blocked,
        blocked,
        total,
    )


def command_snapshot_index(
    plan: ReproductionPlan,
) -> dict[tuple[str, str, str], Mapping[str, object]]:
    """Decode the immutable per-command source closures in one plan."""

    raw = plan.commands
    snapshots: dict[tuple[str, str, str], Mapping[str, object]] = {}
    for value in raw:
        if not isinstance(value, Mapping):
            raise CommandAccountingError("command snapshot is invalid")
        entry = value.get("entry")
        cid = value.get("cid")
        execution_id = value.get("execution_id")
        source_digest = value.get("source_digest")
        if (
            not isinstance(entry, str)
            or not isinstance(cid, str)
            or not isinstance(execution_id, str)
            or not isinstance(value.get("auto_reproduce"), bool)
            or value.get("selection")
            not in {"blocked", "not_needed", "policy", "run", "unchanged"}
            or (
                source_digest is not None
                and (
                    not isinstance(source_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
                )
            )
        ):
            raise CommandAccountingError("command snapshot is invalid")
        selection = value["selection"]
        prior_disposition = value.get("prior_disposition")
        if (selection == "unchanged") != (
            prior_disposition in {"failed", "blocked"}
        ) or (selection != "unchanged" and prior_disposition is not None):
            raise CommandAccountingError("command snapshot is invalid")
        if selection not in {"not_needed", "policy"} and source_digest is None:
            raise CommandAccountingError("command snapshot is invalid")
        key = (entry, cid, execution_id)
        if key in snapshots:
            raise CommandAccountingError("command snapshot is duplicated")
        snapshots[key] = value
    return snapshots
