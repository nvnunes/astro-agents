"""Shared command-selection accounting for reproduction plans."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .reproduction_contract import ReproductionPlan
from .reproduction_planner import ReproductionCommandInventory


class CommandAccountingError(ValueError):
    """One invalid or irreconcilable command-selection projection."""


@dataclass(frozen=True)
class CommandSelectionAccounting:
    """Exhaustive target counts fixed before command execution."""

    run_keys: frozenset[tuple[str, str]]
    not_automatic: int
    reused: int
    blocked: int
    total: int


def project_command_selection(
    plan: ReproductionPlan,
    inventory: ReproductionCommandInventory,
) -> CommandSelectionAccounting:
    """Reconcile one plan's run, reuse, block, and policy selections."""

    planned: dict[tuple[str, str], bool] = {}
    for item in plan.executions:
        entry = item.get("entry")
        execution_id = item.get("execution_id")
        automatic = item.get("auto_reproduce")
        key = (entry, execution_id)
        if (
            not isinstance(entry, str)
            or not isinstance(execution_id, str)
            or not isinstance(automatic, bool)
            or key in planned
        ):
            raise CommandAccountingError("planned command accounting is invalid")
        planned[(entry, execution_id)] = automatic

    snapshots = command_snapshot_index(plan)
    if snapshots:
        run_keys = frozenset(
            key for key, value in snapshots.items() if value["selection"] == "run"
        )
        if run_keys != set(planned):
            raise CommandAccountingError(
                "command selection does not match the accepted plan"
            )
        blocked = sum(value["selection"] == "blocked" for value in snapshots.values())
        included_not_automatic = sum(
            value["auto_reproduce"] is False for value in snapshots.values()
        )
    else:
        run_keys = frozenset(planned)
        blocked = 0
        included_not_automatic = sum(not automatic for automatic in planned.values())

    not_automatic = inventory.not_automatic - included_not_automatic
    reused = inventory.total - not_automatic - len(run_keys) - blocked
    if not_automatic < 0 or reused < 0:
        raise CommandAccountingError(
            "command inventory does not reconcile with the accepted plan"
        )
    return CommandSelectionAccounting(
        run_keys,
        not_automatic,
        reused,
        blocked,
        inventory.total,
    )


def command_snapshot_index(
    plan: ReproductionPlan,
) -> dict[tuple[str, str], Mapping[str, object]]:
    """Decode the immutable per-command source closures in one plan."""

    raw = plan.source_snapshot.get("commands")
    if raw is None:
        return {}
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise CommandAccountingError("command snapshots are invalid")
    snapshots: dict[tuple[str, str], Mapping[str, object]] = {}
    fields = {
        "auto_reproduce",
        "entry",
        "execution_id",
        "selection",
        "source_digest",
    }
    for value in raw:
        if not isinstance(value, Mapping) or set(value) != fields:
            raise CommandAccountingError("command snapshot is invalid")
        entry = value.get("entry")
        execution_id = value.get("execution_id")
        source_digest = value.get("source_digest")
        if (
            not isinstance(entry, str)
            or not isinstance(execution_id, str)
            or not isinstance(value.get("auto_reproduce"), bool)
            or value.get("selection") not in {"blocked", "reuse", "run"}
            or not isinstance(source_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
        ):
            raise CommandAccountingError("command snapshot is invalid")
        key = (entry, execution_id)
        if key in snapshots:
            raise CommandAccountingError("command snapshot is duplicated")
        snapshots[key] = value
    return snapshots
