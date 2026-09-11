"""Exact deterministic projection contracts for reproduction planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

LEGACY_PLAN_SCHEMA = "research-log-reproduction-plan/2"
PRECONTINUATION_PLAN_SCHEMA = "research-log-reproduction-plan/3"
PRETIMEOUT_PLAN_SCHEMA = "research-log-reproduction-plan/4"
PREEXECUTION_PLAN_SCHEMA = "research-log-reproduction-plan/5"
PLAN_SCHEMA = "research-log-reproduction-plan/6"
LEGACY_SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/1"
PRELOCAL_SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/3"
PRECOMMAND_SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/4"
PREQUERY_SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/6"
SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/8"
REPAIR_SOURCE_SNAPSHOT_SCHEMA = "research-log-reproduction-source-snapshot/9"
PREEXECUTION_RESULT_SCHEMA = "research-log-reproduction-result/9"
REPRODUCTION_RESULT_SCHEMA = "research-log-reproduction-result/10"
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_PLAN_SUMMARY_ENTRIES = 20
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 5 * 60
MAX_EXECUTION_TIMEOUT_SECONDS = 7 * 24 * 60 * 60


def valid_reproduction_target(value: object) -> bool:
    """Validate the exact log, entry, or full execution target shape."""

    from validation.pyrun_state import PYRUN_EXECUTION_RE

    from .context import ENTRY_ID_RE

    if not isinstance(value, Mapping):
        return False
    if value == {"kind": "log", "entry": None}:
        return True
    entry = value.get("entry")
    if not isinstance(entry, str) or ENTRY_ID_RE.fullmatch(entry) is None:
        return False
    if value.get("kind") == "entry":
        return set(value) == {"kind", "entry"}
    identity = value.get("execution_id")
    return (
        set(value) == {"kind", "entry", "execution_id"}
        and value.get("kind") == "execution"
        and isinstance(identity, str)
        and PYRUN_EXECUTION_RE.fullmatch(identity) is not None
    )


def successful_checkpoint_state(state: object) -> bool:
    """Return whether a checkpoint is successful in either supported schema."""

    return state in {"complete", "succeeded"}


@dataclass(frozen=True)
class ReproductionRuntime:
    """Immutable concurrency and per-command runtime controls for one run."""

    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ReproductionPlan:
    """One complete write-free reproduction plan projection."""

    summary: str
    target: Mapping[str, object]
    include_all: bool
    validation_snapshot: Mapping[str, object]
    source_snapshot: Mapping[str, object]
    cases: tuple[Mapping[str, object], ...]
    executions: tuple[Mapping[str, object], ...]
    boundaries: tuple[Mapping[str, object], ...]
    failures: tuple[Mapping[str, object], ...]
    jobs: int = 1
    execution_timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS

    def as_dict(self) -> dict[str, object]:
        """Return the exact public v1 field set."""

        return {
            "boundaries": [dict(value) for value in self.boundaries],
            "cases": [dict(value) for value in self.cases],
            "executions": [dict(value) for value in self.executions],
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "failures": [dict(value) for value in self.failures],
            "include_all": self.include_all,
            "jobs": self.jobs,
            "schema": PLAN_SCHEMA,
            "source_snapshot": dict(self.source_snapshot),
            "summary": self.summary,
            "target": dict(self.target),
            "validation_snapshot": dict(self.validation_snapshot),
        }

    def serialized(self) -> str:
        """Serialize canonically and enforce the fixed dry-run byte bound."""

        text = json.dumps(
            self.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("reproduction dry-run plan crossed its byte bound")
        return text


def format_reproduction_plan_summary(plan: ReproductionPlan, *, recheck: bool) -> str:
    """Return the bounded human projection of one valid dry-run plan."""

    entry_counts: dict[str, tuple[int, int]] = {}
    exclusive_count = 0
    required_claims = {"read_paths", "write_paths", "run_path", "writable_paths"}
    claims_complete = True
    for execution in plan.executions:
        entry = execution.get("entry")
        if not isinstance(entry, str):
            raise ValueError("reproduction plan execution has an invalid entry")
        count, entry_exclusive = entry_counts.get(entry, (0, 0))
        is_exclusive = execution.get("exclusive") is True
        entry_counts[entry] = (count + 1, entry_exclusive + int(is_exclusive))
        exclusive_count += int(is_exclusive)
        claims_complete = claims_complete and required_claims <= set(execution)

    target_kind = plan.target.get("kind")
    target_entry = plan.target.get("entry")
    target = str(target_kind)
    if target_entry is not None:
        target = f"{target} {target_entry}"
    selection = "Recheck" if recheck else "Incremental"
    eligibility = "all eligible executions" if plan.include_all else "automatic only"
    failure_count = len(plan.failures)
    admission = "Ready with localized failures" if failure_count else "Ready"
    execution_count = len(plan.executions)
    lines = [
        f"Reproduction preview for `{plan.summary}`",
        "",
        f"- Target: {target}",
        f"- Admission: {admission}",
        f"- Selection: {selection}; {eligibility}",
        f"- Concurrency cap: {plan.jobs}",
        f"- Per-command runtime limit: {plan.execution_timeout_seconds} seconds",
        f"- Artifact cases: {len(plan.cases)}",
        (
            f"- Runnable executions: {execution_count} "
            f"({execution_count - exclusive_count} ordinary, "
            f"{exclusive_count} exclusive)"
        ),
        f"- Local planning failures: {failure_count} artifacts",
        f"- Boundaries: {len(plan.boundaries)}",
        (
            "- Scheduling path claims: "
            + ("Complete" if claims_complete else "Incomplete")
        ),
    ]
    selected_entries = sorted(entry_counts.items())[:MAX_PLAN_SUMMARY_ENTRIES]
    if selected_entries:
        lines.extend(
            [
                "",
                "| Entry | Runnable | Exclusive |",
                "| --- | ---: | ---: |",
                *(
                    f"| `{entry}` | {counts[0]} | {counts[1]} |"
                    for entry, counts in selected_entries
                ),
            ]
        )
    omitted = len(entry_counts) - len(selected_entries)
    if omitted:
        noun = "entry" if omitted == 1 else "entries"
        lines.extend(["", f"{omitted} additional {noun} omitted."])
    if target_kind == "execution":
        lines.extend(_execution_plan_summary(plan))
    if is_repair_verification(plan):
        lines.extend(_repair_plan_summary(plan))
    return "\n".join(lines) + "\n"


def _execution_plan_summary(plan: ReproductionPlan) -> list[str]:
    """Explain the complete single-command scope, including zero-work plans."""

    commands = cast(
        Sequence[Mapping[str, Any]], plan.source_snapshot.get("commands", ())
    )
    lines = ["", f"- Execution ID: `{plan.target['execution_id']}`"]
    for command in commands:
        recipe = command["recipe"]
        lines.extend(
            [
                f"- Script: `{recipe['script']}`",
                f"- Selection reason: {command['selection']}",
                "- Complete outputs:",
            ]
        )
        lines.extend(f"  - `{output}`" for output in recipe["outputs"])
        if command["selection"] == "policy":
            lines.append("- Policy excludes this command; --include-all is required.")
    lines.append("- Retained prerequisites:")
    if not plan.boundaries:
        lines.append("  - None verified.")
    for boundary in plan.boundaries:
        producers = (
            ", ".join(cast(Sequence[str], boundary.get("producers", ())))
            or "external/origin"
        )
        lines.append(f"  - `{boundary['name']}` ({boundary['kind']}): {producers}")
    lines.append("- Blockers:")
    if not plan.failures:
        lines.append("  - None.")
    for failure in plan.failures:
        lines.append(f"  - `{failure['artifact']}`: {failure['reason']}")
        lines.extend(
            f"    - {detail}" for detail in cast(Sequence[str], failure["dependencies"])
        )
    if not plan.executions:
        lines.append(
            "- Zero executions: the selection reason and blockers above "
            "explain why no command will launch."
        )
    return lines


def _repair_plan_summary(plan: ReproductionPlan) -> list[str]:
    lines = [
        "",
        "- Mode: repaired-source verification; "
        "recorded recipe and observations preserved.",
        "- Accepted source fingerprints:",
    ]
    for item in cast(Sequence[Mapping[str, Any]], plan.source_snapshot["materials"]):
        if "recorded_fingerprint" in item:
            lines.append(
                f"  - `{item['identity']}`: "
                f"recorded={item['recorded_fingerprint']}; "
                f"accepted={item['fingerprint']}"
            )
    lines.append(
        "- This run does not clear the recorded reproduction requirement "
        "or authorize promotion."
    )
    return lines


def source_snapshot(
    *,
    authority_files: Sequence[Mapping[str, object]],
    commands: Sequence[Mapping[str, object]] = (),
    executions: Sequence[Mapping[str, object]],
    materials: Sequence[Mapping[str, object]],
    verify_repair: bool = False,
) -> dict[str, object]:
    """Build the exact top-level source-snapshot field set."""

    return {
        "authority_files": authority_files,
        "commands": commands,
        "executions": executions,
        "materials": materials,
        "result_schema": REPRODUCTION_RESULT_SCHEMA,
        "schema": REPAIR_SOURCE_SNAPSHOT_SCHEMA
        if verify_repair
        else SOURCE_SNAPSHOT_SCHEMA,
        **({"repair_verification": True} if verify_repair else {}),
    }


def is_repair_verification(plan: ReproductionPlan) -> bool:
    """Decode the explicit repaired-source admission marker without defaults."""

    from .model import ActionError

    snapshot = plan.source_snapshot
    if snapshot.get("schema") != REPAIR_SOURCE_SNAPSHOT_SCHEMA:
        if "repair_verification" in snapshot:
            raise ActionError(
                "reproduction.source.invalid",
                "repair marker requires source snapshot/9",
            )
        return False
    if (
        set(snapshot)
        != {
            "schema",
            "authority_files",
            "commands",
            "executions",
            "materials",
            "result_schema",
            "repair_verification",
        }
        or snapshot.get("result_schema") != REPRODUCTION_RESULT_SCHEMA
        or snapshot.get("repair_verification") is not True
        or not valid_reproduction_target(plan.target)
        or plan.target.get("kind") != "execution"
    ):
        raise ActionError(
            "reproduction.source.invalid", "invalid repair verification snapshot"
        )
    _validate_repair_material_structure(snapshot["materials"])
    return True


def _validate_repair_material_structure(materials: object) -> None:
    """Validate the additional source-history fields before any source recheck."""

    from .model import ActionError

    if not isinstance(materials, Sequence) or isinstance(materials, (str, bytes)):
        raise ActionError("reproduction.source.invalid", "invalid repair materials")
    fields = {"identity", "role", "kind", "fingerprint"}
    for item in materials:
        if not isinstance(item, Mapping):
            raise ActionError("reproduction.source.invalid", "invalid repair material")
        expected = fields | (
            {"recorded_fingerprint"}
            if item.get("role") in {"script", "code"}
            else set()
        )
        if set(item) != expected or not isinstance(item.get("identity"), str):
            raise ActionError(
                "reproduction.source.invalid", "invalid repair material fields"
            )


def canonical_record_digest(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of one canonical JSON object."""

    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_execution_source_digest(value: Mapping[str, Any]) -> str:
    """Hash execution source without its mutable reproduction requirement."""

    selected = dict(value)
    if set(selected) <= {"requires_reproduction"}:
        raise ValueError("execution source record is incomplete")
    selected.pop("requires_reproduction", None)
    return canonical_record_digest(selected)
