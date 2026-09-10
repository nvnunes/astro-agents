"""Strict cumulative reproduction results and their human projection."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence, cast

from research_log_data import DataContractError, Fingerprint, parse_fingerprint
from validation.evidence_comparison import EVIDENCE_COMPARISON_RESULT_CONTRACT
from validation.human_projection import ReportContext
from validation.pyrun_state import PYRUN_EXECUTION_RE

from .context import ENTRY_ID_RE
from .model import ActionError
from .reproduction_contract import REPRODUCTION_RESULT_SCHEMA
from .reproduction_paths import (
    REPRODUCTION_ROOT_NAME,
    is_canonical_run_path,
    resolve_project_tmp,
)
from .reproduction_planner import ReproductionStateProjection

RESULT_SCHEMA = REPRODUCTION_RESULT_SCHEMA
COMPARISON_CONTRACT = "research-log-reproduction-comparison/1"
MAX_RESULT_BYTES = 64 << 20
MAX_ARTIFACT_RESULTS = 10_000
MAX_COMMAND_RESULTS = 10_000
MAX_RUN_RESULTS = 10_000
MAX_QUERY_RESULTS = 50
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
OUTCOMES = ("matched", "changed", "failed", "comparison_failed", "skipped")
COMMAND_OUTCOMES = (
    "not_automatic",
    "reproduction_not_needed",
    "unchanged_failed",
    "unchanged_blocked",
    "succeeded",
    "failed",
    "blocked",
    "total",
)
COMMAND_DISPOSITIONS = ("succeeded", "failed", "blocked")
ARTIFACT_NOT_COMPARED_REASONS = (
    "comparison_failed",
    "command_failed",
    "command_blocked",
    "command_skipped",
)
RUN_STATUSES = ("complete", "failed", "stopped")
PROFILES = (
    "directory",
    "evidence",
    "image",
    "json",
    "named_array",
    "opaque_file",
    "table",
    "text",
)
REASONS = {
    "baseline_unavailable",
    "baseline_changed",
    "boundary_changed",
    "boundary_unavailable",
    "capture_failed",
    "comparator_error",
    "content_changed",
    "cross_log_generated_input",
    "dependency_cycle",
    "dependency_failed",
    "direct_input_changed",
    "direct_input_unavailable",
    "execution_exception",
    "execution_failed",
    "execution_timeout",
    "evidence_comparison_failed",
    "generation_failed",
    "graph_limit",
    "missing_input",
    "missing_producer",
    "multiple_producers",
    "output_materialization_failed",
    "output_missing",
    "outside_entry",
    "participating_code_changed",
    "participating_code_unavailable",
    "reproduction.input.unavailable",
    "reproduction.run.invalid",
    "resource_limit",
    "safety_failure",
    "script_changed",
    "script_unavailable",
    "non_automatic",
    "stop_requested",
    "unsupported_format",
    "validation_blocked",
    "worker_cleanup_incomplete",
    "worker_survived",
    "outside_queue",
}


class ReproductionResultError(ValueError):
    """One exact cumulative-result contract failure."""


class ReproductionResultSchemaError(ReproductionResultError):
    """One outdated or otherwise unsupported generated result schema."""


@dataclass(frozen=True)
class ComparisonRecord:
    """One exact decoded comparison identity."""

    profile: str
    expected: Fingerprint | None
    regenerated: Fingerprint | None
    evidence_definition: str | None = None
    evidence: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        _choice(self.profile, PROFILES, "comparison.profile")
        for name, value in (
            ("expected", self.expected),
            ("regenerated", self.regenerated),
        ):
            if value is None:
                continue
            try:
                parse_fingerprint(value.as_dict(), f"comparison.{name}")
            except (AssertionError, DataContractError) as error:
                raise ReproductionResultError(
                    f"comparison.{name} fingerprint is invalid"
                ) from error
        if self.evidence_definition is None:
            if self.evidence or self.profile == "evidence":
                raise ReproductionResultError(
                    "evidence comparison needs its definition identity"
                )
        elif re.fullmatch(r"[0-9a-f]{64}", self.evidence_definition) is None or any(
            not _valid_evidence_comparison(item) for item in self.evidence
        ):
            raise ReproductionResultError("evidence comparison details are invalid")

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "contract": COMPARISON_CONTRACT,
            "expected": self.expected.as_dict() if self.expected is not None else None,
            "profile": self.profile,
            "regenerated": (
                self.regenerated.as_dict() if self.regenerated is not None else None
            ),
        }
        if self.evidence_definition is not None:
            result.update(
                {
                    "evidence": [dict(item) for item in self.evidence],
                    "evidence_contract": EVIDENCE_COMPARISON_RESULT_CONTRACT,
                    "evidence_definition": self.evidence_definition,
                }
            )
        return result


@dataclass(frozen=True)
class ArtifactResult:
    """One current evidence-rooted artifact result."""

    entry: str
    artifact: str
    execution_id: str | None
    outcome: str
    reason: str | None
    recorded_at: str
    run_id: str
    comparison: ComparisonRecord | None

    def __post_init__(self) -> None:
        _entry(self.entry, "artifact.entry")
        _artifact_path(self.artifact, "artifact.artifact")
        outcome = _choice(self.outcome, OUTCOMES, "artifact.outcome")
        if self.execution_id is not None:
            _execution(self.execution_id)
        if outcome in {"matched", "changed"} and self.execution_id is None:
            raise ReproductionResultError(
                "matched or changed artifact needs execution ID"
            )
        if outcome == "matched" and self.reason is not None:
            raise ReproductionResultError("matched artifact reason must be null")
        if outcome != "matched" and self.reason not in REASONS:
            raise ReproductionResultError(
                f"unsupported artifact reason: {self.reason!r}"
            )
        if outcome in {"matched", "changed"} and self.comparison is None:
            raise ReproductionResultError("compared artifact needs comparison details")
        _timestamp(self.recorded_at, "artifact.recorded_at")
        _run_id(self.run_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact,
            "comparison": (
                self.comparison.as_dict() if self.comparison is not None else None
            ),
            "entry": self.entry,
            "execution_id": self.execution_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class CommandResult:
    """One reusable terminal result for an entry-qualified command."""

    entry: str
    execution_id: str
    disposition: str
    source_digest: str
    recorded_at: str
    run_id: str

    def __post_init__(self) -> None:
        _entry(self.entry, "command.entry")
        _execution(self.execution_id)
        _choice(self.disposition, COMMAND_DISPOSITIONS, "command.disposition")
        _sha256_digest(self.source_digest, "command.source_digest")
        _timestamp(self.recorded_at, "command.recorded_at")
        _run_id(self.run_id)

    def as_dict(self) -> dict[str, str]:
        return {
            "disposition": self.disposition,
            "entry": self.entry,
            "execution_id": self.execution_id,
            "recorded_at": self.recorded_at,
            "run_id": self.run_id,
            "source_digest": self.source_digest,
        }


@dataclass(frozen=True)
class RunFolder:
    """One retained or availability-unknown run directory."""

    path: str
    availability: str

    def __post_init__(self) -> None:
        _choice(self.availability, ("available", "unknown"), "folder.availability")
        path = _portable_path(self.path, "folder.path")
        if not is_canonical_run_path(path):
            raise ReproductionResultError("run folder path is not canonical")

    def as_dict(self) -> dict[str, str]:
        return {"availability": self.availability, "path": self.path}


@dataclass(frozen=True)
class RunResult:
    """One published terminal lifecycle event."""

    run_id: str
    target: Mapping[str, object]
    include_all: bool
    status: str
    accepted_at: str
    finished_at: str | None
    artifact_outcomes: Mapping[str, int]
    folder: RunFolder
    executions: tuple[Mapping[str, object], ...] = ()
    command_outcomes: Mapping[str, int] | None = None
    command_records: tuple[Mapping[str, object], ...] | None = None

    def __post_init__(self) -> None:
        _run_id(self.run_id)
        _target(self.target)
        if not isinstance(self.include_all, bool):
            raise ReproductionResultError("run include_all must be boolean")
        status = _choice(self.status, RUN_STATUSES, "run.status")
        accepted = _timestamp(self.accepted_at, "run.accepted_at")
        finished = (
            None
            if self.finished_at is None
            else _timestamp(self.finished_at, "run.finished_at")
        )
        if status in {"complete", "failed"} and finished is None:
            raise ReproductionResultError("complete or failed run needs finished_at")
        if status == "stopped" and finished is not None:
            raise ReproductionResultError("stopped run must remain resumable")
        if finished is not None and finished < accepted:
            raise ReproductionResultError("run finished_at precedes accepted_at")
        _counts(self.artifact_outcomes)
        _execution_timings(self.executions)
        if self.command_outcomes is not None:
            _command_counts(self.command_outcomes)
        if self.command_records is not None:
            _command_records(self.command_records, self.command_outcomes)

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_at": self.accepted_at,
            "artifact_outcomes": dict(self.artifact_outcomes),
            "command_outcomes": (
                dict(self.command_outcomes)
                if self.command_outcomes is not None
                else None
            ),
            "command_records": (
                [dict(value) for value in self.command_records]
                if self.command_records is not None
                else None
            ),
            "finished_at": self.finished_at,
            "folder": self.folder.as_dict(),
            "include_all": self.include_all,
            "executions": [dict(value) for value in self.executions],
            "run_id": self.run_id,
            "status": self.status,
            "target": dict(self.target),
        }


@dataclass(frozen=True)
class ReproductionResults:
    """The current artifact and command maps plus retained run history."""

    summary: str
    updated_at: str
    artifacts: tuple[ArtifactResult, ...]
    runs: tuple[RunResult, ...]
    commands: tuple[CommandResult, ...] = ()

    def __post_init__(self) -> None:
        _validate_results(self)

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": [item.as_dict() for item in self.artifacts],
            "commands": [item.as_dict() for item in self.commands],
            "runs": [item.as_dict() for item in self.runs],
            "schema": RESULT_SCHEMA,
            "summary": self.summary,
            "updated_at": self.updated_at,
        }

    def serialized(self) -> str:
        text = (
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ReproductionResultError("reproduction result exceeds 64 MiB")
        return text

    @classmethod
    def from_json(cls, text: str) -> ReproductionResults:
        if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ReproductionResultError("reproduction result exceeds 64 MiB")
        try:
            value = json.loads(text, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ReproductionResultError) as error:
            raise ReproductionResultError(
                f"invalid reproduction result: {error}"
            ) from error
        item = _mapping(value, "result")
        schema = item.get("schema")
        if schema != RESULT_SCHEMA:
            raise ReproductionResultSchemaError(
                "published reproduction result schema is unsupported; run "
                "whole-log reproduction with --recheck to rebuild it"
            )
        fields = {
            "artifacts",
            "commands",
            "runs",
            "schema",
            "summary",
            "updated_at",
        }
        if set(item) != fields:
            raise ReproductionResultError("result has incorrect fields")
        artifacts = tuple(
            _decode_artifact(value, index)
            for index, value in enumerate(_sequence(item["artifacts"], "artifacts"))
        )
        runs = tuple(
            _decode_run(value, index, current=True)
            for index, value in enumerate(_sequence(item["runs"], "runs"))
        )
        commands = tuple(
            _decode_command(value, index)
            for index, value in enumerate(_sequence(item["commands"], "commands"))
        )
        result = cls(
            _string(item["summary"], "summary"),
            _timestamp(item["updated_at"], "updated_at"),
            artifacts,
            runs,
            commands,
        )
        if text != result.serialized():
            raise ReproductionResultError("result serialization is not canonical")
        return result


@dataclass(frozen=True)
class CommandQueryMetadata:
    """The complete current metadata required by bounded command queries."""

    outcomes: Mapping[str, int]
    records: tuple[Mapping[str, object], ...]


def current_command_query_metadata(run: RunResult) -> CommandQueryMetadata | None:
    """Return metadata only when a run has the current command-query contract."""

    if run.command_outcomes is None or run.command_records is None:
        return None
    return CommandQueryMetadata(run.command_outcomes, run.command_records)


@dataclass(frozen=True)
class ArtifactCurrentness:
    """One derived, non-persisted currentness projection."""

    current: bool
    reason: str | None = None


@dataclass(frozen=True)
class _SummaryPresentation:
    heading: str
    run_context: tuple[str, ...]
    artifacts_available: bool


@dataclass(frozen=True)
class ArtifactQuery:
    """One bounded exact artifact-list response."""

    records: tuple[Mapping[str, object], ...]
    matched: int
    returned: int
    omitted: int

    def as_dict(self) -> dict[str, object]:
        return {
            "matched": self.matched,
            "omitted": self.omitted,
            "records": [dict(value) for value in self.records],
            "returned": self.returned,
        }


def empty_reproduction_results(summary: str, *, updated_at: str) -> ReproductionResults:
    """Create the canonical not-yet-reproduced state."""

    return ReproductionResults(summary, _timestamp(updated_at, "updated_at"), (), ())


def load_reproduction_results(path: Path) -> ReproductionResults:
    """Load one regular canonical result without repair or cleanup."""

    if path.is_symlink() or not path.is_file():
        raise ReproductionResultError(f"result is not a regular file: {path}")
    try:
        return ReproductionResults.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ReproductionResultError(str(error)) from error


def merge_reproduction_results(
    current: ReproductionResults,
    artifacts: Sequence[ArtifactResult],
    run: RunResult,
    *,
    commands: Sequence[CommandResult] = (),
    state: ReproductionStateProjection | None = None,
) -> ReproductionResults:
    """Replace published artifact and command cases and append one run."""

    previous_run = next(
        (item for item in current.runs if item.run_id == run.run_id), None
    )
    if previous_run is not None:
        run = _merge_logical_run(previous_run, run)
    replacements = {(item.entry, item.artifact): item for item in artifacts}
    if len(replacements) != len(artifacts):
        raise ReproductionResultError("published artifacts are duplicated")
    merged = {
        (item.entry, item.artifact): item
        for item in current.artifacts
        if (item.entry, item.artifact) not in replacements
    }
    merged.update(replacements)
    if state is not None:
        merged = {key: value for key, value in merged.items() if key in state.reachable}
    command_replacements = {(item.entry, item.execution_id): item for item in commands}
    if len(command_replacements) != len(commands):
        raise ReproductionResultError("published command results are duplicated")
    merged_commands = {
        (item.entry, item.execution_id): item
        for item in current.commands
        if (item.entry, item.execution_id) not in command_replacements
    }
    merged_commands.update(command_replacements)
    if state is not None:
        merged_commands = {
            key: value
            for key, value in merged_commands.items()
            if key in state.reachable_commands
        }
    return ReproductionResults(
        current.summary,
        _timestamp(run.finished_at, "run.finished_at"),
        tuple(sorted(merged.values(), key=_artifact_key)),
        tuple(
            sorted(
                (*(item for item in current.runs if item.run_id != run.run_id), run),
                key=_run_key,
            )
        ),
        tuple(sorted(merged_commands.values(), key=_command_key)),
    )


def _merge_logical_run(previous: RunResult, current: RunResult) -> RunResult:
    """Merge a later attempt into one persistent logical run result."""

    if previous.command_records is None or current.command_records is None:
        raise ReproductionResultError(
            f"duplicate run ID without continuation records: {current.run_id}"
        )
    old = {
        (cast(str, item["entry"]), cast(str, item["execution_id"])): item
        for item in previous.command_records
    }
    merged = dict(old)
    for item in current.command_records:
        key = (cast(str, item["entry"]), cast(str, item["execution_id"]))
        prior = old.get(key)
        if prior is not None and prior["queued"] is False:
            continue
        if prior is not None and (
            item["run_selection"] == "not_needed"
            and prior["terminal_disposition"] == "succeeded"
            or item["run_selection"] == "unchanged"
            and prior["terminal_disposition"] in {"failed", "blocked"}
        ):
            continue
        if prior is not None:
            item = {
                **dict(item),
                **{
                    name: prior[name]
                    for name in (
                        "auto_reproduce",
                        "cwd",
                        "exclusive",
                        "queued",
                        "recipe",
                        "requires_reproduction",
                    )
                },
            }
        merged[key] = item
    records = tuple(
        merged[key]
        for key in sorted(merged, key=lambda key: (_entry_key(key[0]), key[1]))
    )
    counts = {name: 0 for name in COMMAND_OUTCOMES}
    for item in records:
        bucket = item["bucket"]
        reason = cast(str, item["reason"])
        leaf = (
            cast(str, bucket)
            if bucket in {"blocked", "failed", "succeeded"}
            else "not_automatic"
            if bucket == "skipped-by-policy"
            else reason
        )
        counts[leaf] += 1
    counts["total"] = len(records)
    return replace(
        current,
        accepted_at=previous.accepted_at,
        command_outcomes=counts,
        command_records=records,
    )


def reconcile_run_folders(
    results: ReproductionResults, *, project_root: Path
) -> ReproductionResults:
    """Drop conclusively removed run folders and retain unavailable checks.

    A missing child beneath an accessible project ``tmp`` directory is
    conclusive. Any inspection error retains the run and projects its folder as
    availability unknown.
    """

    try:
        temporary_root = resolve_project_tmp(project_root)
    except OSError:
        return ReproductionResults(
            results.summary,
            results.updated_at,
            results.artifacts,
            tuple(_run_with_folder(run, "unknown") for run in results.runs),
            results.commands,
        )
    storage_root = temporary_root / REPRODUCTION_ROOT_NAME
    try:
        if storage_root.is_symlink() or (
            storage_root.exists() and not storage_root.is_dir()
        ):
            raise OSError("reproduction storage root is unavailable")
    except OSError:
        return ReproductionResults(
            results.summary,
            results.updated_at,
            results.artifacts,
            tuple(_run_with_folder(run, "unknown") for run in results.runs),
            results.commands,
        )
    retained: list[RunResult] = []
    for run in results.runs:
        parts = PurePosixPath(run.folder.path).parts
        target = temporary_root.joinpath(*parts[1:])
        try:
            exists = target.exists() or target.is_symlink()
        except OSError:
            retained.append(_run_with_folder(run, "unknown"))
            continue
        if not exists:
            continue
        availability = (
            "available"
            if exists and target.is_dir() and not target.is_symlink()
            else "unknown"
        )
        retained.append(_run_with_folder(run, availability))
    return ReproductionResults(
        results.summary,
        results.updated_at,
        results.artifacts,
        tuple(retained),
        results.commands,
    )


def project_current_results(
    results: ReproductionResults,
    state: ReproductionStateProjection,
) -> tuple[ReproductionResults, Mapping[tuple[str, str], ArtifactCurrentness]]:
    """Ignore unreachable records and derive timestamp currentness on demand."""

    artifacts = tuple(
        item
        for item in results.artifacts
        if (item.entry, item.artifact) in state.reachable
    )
    currentness: dict[tuple[str, str], ArtifactCurrentness] = {}
    for item in artifacts:
        key = (item.entry, item.artifact)
        if item.execution_id is None:
            currentness[key] = ArtifactCurrentness(True)
            continue
        current_execution = state.output_executions.get(key)
        if current_execution is not None and current_execution != item.execution_id:
            currentness[key] = ArtifactCurrentness(False, "execution_changed")
            continue
        execution_key = (item.entry, item.execution_id)
        if current_execution is None and execution_key not in state.last_runs:
            currentness[key] = ArtifactCurrentness(False, "execution_unavailable")
            continue
        recorded_definition = (
            item.comparison.evidence_definition if item.comparison is not None else None
        )
        if recorded_definition != state.comparison_definitions.get(key):
            currentness[key] = ArtifactCurrentness(False, "comparison_changed")
            continue
        last_run = state.last_runs.get(execution_key)
        if last_run is not None and last_run > item.recorded_at:
            currentness[key] = ArtifactCurrentness(False, "execution_reran")
        else:
            currentness[key] = ArtifactCurrentness(True)
    projected = ReproductionResults(
        results.summary, results.updated_at, artifacts, results.runs, results.commands
    )
    return projected, currentness


def compose_reproduction_report(
    results: ReproductionResults,
    *,
    context: ReportContext,
    currentness: Mapping[tuple[str, str], ArtifactCurrentness] | None = None,
    entry: str | None = None,
    folder_links_from: Path | None = None,
) -> str:
    """Render the sole deterministic human reproduction projection."""

    currentness = currentness or {}
    artifacts = tuple(
        item for item in results.artifacts if entry is None or item.entry == entry
    )
    latest = next((run for run in results.runs if run.status == "complete"), None)
    lines = _summary_lines(
        results.updated_at,
        artifact_summary_counts(artifacts, results.commands),
        latest.command_outcomes if latest is not None else None,
        _SummaryPresentation(
            "# Reproduction",
            _latest_run_context(latest),
            latest is not None,
        ),
    )
    stable_context_entries = {
        value for value in context.entries if re.fullmatch(r"e[0-9]+", value)
    }
    entry_ids = sorted(
        (
            {entry}
            if entry is not None
            else stable_context_entries | {item.entry for item in artifacts}
        ),
        key=_entry_key,
    )
    for entry_id in entry_ids:
        lines.extend(("", _entry_heading(entry_id, context), ""))
        lines.extend(("| Artifact | Status |", "| --- | --- |"))
        for item in (value for value in artifacts if value.entry == entry_id):
            state = currentness.get(
                (item.entry, item.artifact), ArtifactCurrentness(True)
            )
            status = item.outcome if state.current else f"{item.outcome} (stale)"
            if status != "matched":
                status = f"**{status}**"
            lines.append(f"| `{_escape_code(item.artifact)}` | {status} |")
    lines.extend(
        ("", "## Runs", "", "| Run ID | Target | Run status | Time | Folder |")
    )
    lines.append("| --- | --- | --- | --- | --- |")
    for run in results.runs:
        target = (
            f"entry {run.target['entry']}" if run.target["kind"] == "entry" else "log"
        )
        folder = _folder_label(run.folder, folder_links_from)
        lines.append(
            f"| `{run.run_id}` | {target} | {_run_status_label(run)} | "
            f"`{run.finished_at or run.accepted_at}` | {folder} |"
        )
    if not results.runs:
        lines.append("| — | — | not yet reproduced | — | — |")
    return "\n".join(lines).rstrip() + "\n"


def compose_reproduction_summary(
    results: ReproductionResults,
) -> str:
    """Render the concise balanced summary of recorded reproduction outcomes."""

    latest = next((run for run in results.runs if run.status == "complete"), None)
    lines = _summary_lines(
        results.updated_at,
        artifact_summary_counts(results.artifacts, results.commands),
        latest.command_outcomes if latest is not None else None,
        _SummaryPresentation(
            "# Reproduction Summary",
            _latest_run_context(latest),
            latest is not None,
        ),
    )
    return "\n".join(lines).rstrip() + "\n"


def compose_reproduction_reconciliation_summary(
    results: ReproductionResults,
    command_outcomes: Mapping[str, int],
    *,
    generated_at: str,
) -> str:
    """Render a terminal no-work reconciliation without inventing a run."""

    latest = next((run for run in results.runs if run.status == "complete"), None)
    lines = _summary_lines(
        generated_at,
        artifact_summary_counts(results.artifacts, results.commands),
        command_outcomes,
        _SummaryPresentation(
            "# Reproduction Summary",
            (
                "Current reconciliation: no commands executed; no run was created.",
                "Latest completed run remains: "
                + (f"`{latest.run_id}`" if latest is not None else "none"),
            ),
            latest is not None,
        ),
    )
    return "\n".join(lines).rstrip() + "\n"


def artifact_summary_counts(
    artifacts: Sequence[ArtifactResult],
    commands: Sequence[CommandResult] = (),
) -> Mapping[str, object]:
    """Project mutually exclusive recorded artifact outcomes for reporting."""

    command_dispositions = {
        (item.entry, item.execution_id): item.disposition for item in commands
    }
    matched = 0
    not_matched = 0
    total = 0
    not_compared = {name: 0 for name in ARTIFACT_NOT_COMPARED_REASONS}
    for item in artifacts:
        if item.outcome == "matched":
            matched += 1
        elif item.outcome == "changed":
            not_matched += 1
        elif item.outcome == "failed":
            disposition = (
                None
                if item.execution_id is None
                else command_dispositions.get((item.entry, item.execution_id))
            )
            reason = (
                "command_blocked"
                if item.execution_id is None or disposition == "blocked"
                else "command_failed"
            )
            not_compared[reason] += 1
        elif item.outcome == "skipped":
            reason = (
                "command_blocked"
                if item.reason == "dependency_failed"
                else "command_skipped"
            )
            not_compared[reason] += 1
        else:
            not_compared["comparison_failed"] += 1
        total += 1
    not_compared["total"] = sum(not_compared.values())
    return {
        "matched": matched,
        "not_matched": not_matched,
        "not_compared": not_compared,
        "total": total,
    }


def command_summary_counts(commands: Mapping[str, int]) -> Mapping[str, object]:
    """Nest stored command outcomes according to their selection lifecycle."""

    checked = _command_counts(commands)
    selected = checked["succeeded"] + checked["failed"] + checked["blocked"]
    not_retried = (
        checked["reproduction_not_needed"]
        + checked["unchanged_failed"]
        + checked["unchanged_blocked"]
    )
    return {
        "total": checked["total"],
        "skipped_by_policy": checked["not_automatic"],
        "reproduction_not_retried": not_retried,
        "selected": {
            "total": selected,
            "succeeded": checked["succeeded"],
            "failed": checked["failed"],
            "blocked": checked["blocked"],
        },
    }


def _summary_lines(
    updated_at: str,
    artifacts: Mapping[str, object],
    command_outcomes: Mapping[str, int] | None,
    presentation: _SummaryPresentation,
) -> list[str]:
    lines = [
        presentation.heading,
        "",
        f"Generated: `{updated_at}`",
        "",
        *presentation.run_context,
        "",
        "A command can produce more than one artifact, so the totals are not "
        "expected to match.",
        "A succeeded command ran to completion; artifact matching is shown separately.",
        "",
        "## Commands",
        "",
    ]
    if command_outcomes is None:
        lines.append(
            "Command accounting is unavailable for this older result. Run "
            "reproduction again to publish it."
        )
    else:
        commands = command_summary_counts(command_outcomes)
        selected = cast(Mapping[str, int], commands["selected"])
        lines.extend(
            (
                "```text",
                f"{commands['total']} total",
                f"├─ {commands['reproduction_not_retried']} reproduction not retried",
                f"├─ {commands['skipped_by_policy']} skipped by policy (not automatic)",
                f"└─ {selected['total']} selected for execution",
                f"   ├─ {selected['succeeded']} succeeded",
                f"   ├─ {selected['failed']} failed",
                f"   └─ {selected['blocked']} blocked",
                "```",
            )
        )
    not_compared = cast(Mapping[str, int], artifacts["not_compared"])
    lines.extend(
        (
            "",
            "## Artifacts",
            "",
        )
    )
    if not presentation.artifacts_available:
        lines.append("Artifact accounting is unavailable until reproduction completes.")
    else:
        reasons = [
            (
                not_compared["comparison_failed"],
                "comparison failed",
            ),
            (
                not_compared["command_failed"],
                "command failed",
            ),
            (
                not_compared["command_blocked"],
                "command blocked",
            ),
            (not_compared["command_skipped"], "command skipped"),
        ]
        visible = [(count, label) for count, label in reasons if count]
        tree = [
            "```text",
            f"{artifacts['total']} total",
            f"├─ {artifacts['matched']} matched",
            f"├─ {artifacts['not_matched']} not matched",
            f"└─ {not_compared['total']} not compared",
        ]
        for index, (count, label) in enumerate(visible):
            branch = "└─" if index == len(visible) - 1 else "├─"
            tree.append(f"   {branch} {count} {label}")
        tree.append("```")
        lines.extend(tree)
    return lines


def _latest_run_context(latest: RunResult | None) -> tuple[str, ...]:
    if latest is None:
        return ("Latest completed run: none",)
    state = "resolved" if run_is_resolved(latest) else "unresolved; resume this run"
    return (f"Latest completed run: `{latest.run_id}` ({state})",)


def run_is_resolved(run: RunResult) -> bool:
    """Return whether every command in a completed logical queue succeeded."""

    outcomes = run.command_outcomes
    return bool(
        run.status == "complete"
        and outcomes is not None
        and all(
            outcomes[name] == 0
            for name in (
                "blocked",
                "failed",
                "unchanged_blocked",
                "unchanged_failed",
            )
        )
    )


def _run_status_label(run: RunResult) -> str:
    if run.status != "complete":
        return run.status
    return "complete (resolved)" if run_is_resolved(run) else "complete (unresolved)"


def query_artifacts(
    results: ReproductionResults,
    *,
    currentness: Mapping[tuple[str, str], ArtifactCurrentness] | None = None,
    entry: str | None = None,
    outcome: str | None = None,
    artifact: str | None = None,
) -> ArtifactQuery:
    """Return at most 50 exact current records under combinable filters."""

    if outcome is not None and outcome not in OUTCOMES:
        raise ReproductionResultError(f"unsupported outcome: {outcome}")
    selected = [
        item
        for item in results.artifacts
        if (entry is None or item.entry == entry)
        and (outcome is None or item.outcome == outcome)
        and (artifact is None or item.artifact == artifact)
    ]
    projections = [
        {
            **item.as_dict(),
            "currentness": (
                (currentness or {})
                .get((item.entry, item.artifact), ArtifactCurrentness(True))
                .reason
                or "current"
            ),
        }
        for item in selected[:MAX_QUERY_RESULTS]
    ]
    return ArtifactQuery(
        tuple(projections),
        len(selected),
        len(projections),
        len(selected) - len(projections),
    )


def _decode_artifact(value: object, index: int) -> ArtifactResult:
    item = _mapping(value, f"artifacts[{index}]")
    fields = {
        "artifact",
        "comparison",
        "entry",
        "execution_id",
        "outcome",
        "reason",
        "recorded_at",
        "run_id",
    }
    if set(item) != fields:
        raise ReproductionResultError(f"artifacts[{index}] has incorrect fields")
    entry = _entry(item["entry"], f"artifacts[{index}].entry")
    artifact = _artifact_path(item["artifact"], f"artifacts[{index}].artifact")
    outcome = _choice(item["outcome"], OUTCOMES, f"artifacts[{index}].outcome")
    reason = item["reason"]
    if outcome == "matched":
        if reason is not None:
            raise ReproductionResultError("matched artifact reason must be null")
    elif reason not in REASONS:
        raise ReproductionResultError(f"unsupported artifact reason: {reason!r}")
    raw_execution = item["execution_id"]
    execution_id = None if raw_execution is None else _execution(raw_execution)
    if execution_id is None and outcome in {"matched", "changed"}:
        raise ReproductionResultError("matched or changed artifact needs execution ID")
    comparison = (
        None
        if item["comparison"] is None
        else _decode_comparison(item["comparison"], f"artifacts[{index}].comparison")
    )
    if outcome in {"matched", "changed"} and comparison is None:
        raise ReproductionResultError("compared artifact needs comparison details")
    return ArtifactResult(
        entry,
        artifact,
        execution_id,
        outcome,
        cast(str | None, reason),
        _timestamp(item["recorded_at"], f"artifacts[{index}].recorded_at"),
        _run_id(item["run_id"]),
        comparison,
    )


def _decode_command(value: object, index: int) -> CommandResult:
    item = _mapping(value, f"commands[{index}]")
    if set(item) != {
        "disposition",
        "entry",
        "execution_id",
        "recorded_at",
        "run_id",
        "source_digest",
    }:
        raise ReproductionResultError(f"commands[{index}] has incorrect fields")
    return CommandResult(
        _entry(item["entry"], f"commands[{index}].entry"),
        _execution(item["execution_id"]),
        _choice(
            item["disposition"],
            COMMAND_DISPOSITIONS,
            f"commands[{index}].disposition",
        ),
        _sha256_digest(item["source_digest"], f"commands[{index}].source_digest"),
        _timestamp(item["recorded_at"], f"commands[{index}].recorded_at"),
        _run_id(item["run_id"]),
    )


def _decode_command_record(value: object, index: int) -> Mapping[str, object]:
    item = _mapping(value, f"command_records[{index}]")
    fields = {
        "auto_reproduce",
        "bucket",
        "cwd",
        "details",
        "entry",
        "execution_id",
        "exclusive",
        "prior_disposition",
        "queued",
        "reason",
        "recipe",
        "requires_reproduction",
        "run_selection",
        "source_digest",
        "terminal_disposition",
    }
    if set(item) != fields:
        raise ReproductionResultError(f"command_records[{index}] has incorrect fields")
    entry = _entry(item["entry"], f"command_records[{index}].entry")
    execution_id = _execution(item["execution_id"])
    for name in (
        "auto_reproduce",
        "exclusive",
        "queued",
        "requires_reproduction",
    ):
        if not isinstance(item[name], bool):
            raise ReproductionResultError(
                f"command_records[{index}].{name} must be boolean"
            )
    cwd = _portable_path(item["cwd"], f"command_records[{index}].cwd")
    details = _sequence(item["details"], f"command_records[{index}].details")
    if any(not isinstance(detail, str) for detail in details):
        raise ReproductionResultError(f"command_records[{index}].details is invalid")
    recipe = _mapping(item["recipe"], f"command_records[{index}].recipe")
    if set(recipe) != {"environment", "inputs", "outputs", "parameters", "script"}:
        raise ReproductionResultError(
            f"command_records[{index}].recipe has incorrect fields"
        )
    if not _json_value(recipe):
        raise ReproductionResultError(f"command_records[{index}].recipe is invalid")
    bucket = _choice(
        item["bucket"],
        (
            "blocked",
            "failed",
            "reproduction-not-retried",
            "skipped-by-policy",
            "succeeded",
        ),
        f"command_records[{index}].bucket",
    )
    reason = _string(item["reason"], f"command_records[{index}].reason")
    selection = _choice(
        item["run_selection"],
        ("blocked", "not_needed", "policy", "run", "unchanged"),
        f"command_records[{index}].run_selection",
    )
    prior = item["prior_disposition"]
    terminal = item["terminal_disposition"]
    if prior is not None:
        prior = _choice(
            prior, COMMAND_DISPOSITIONS, f"command_records[{index}].prior_disposition"
        )
    if terminal is not None:
        terminal = _choice(
            terminal,
            COMMAND_DISPOSITIONS,
            f"command_records[{index}].terminal_disposition",
        )
    source_digest = item["source_digest"]
    if source_digest is not None:
        source_digest = _sha256_digest(
            source_digest, f"command_records[{index}].source_digest"
        )
    return {
        "auto_reproduce": item["auto_reproduce"],
        "bucket": bucket,
        "cwd": cwd,
        "details": list(cast(Sequence[str], details)),
        "entry": entry,
        "execution_id": execution_id,
        "exclusive": item["exclusive"],
        "prior_disposition": prior,
        "queued": item["queued"],
        "reason": reason,
        "recipe": dict(recipe),
        "requires_reproduction": item["requires_reproduction"],
        "run_selection": selection,
        "source_digest": source_digest,
        "terminal_disposition": terminal,
    }


def _json_value(value: object) -> bool:
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True


def _decode_comparison(value: object, subject: str) -> ComparisonRecord:
    item = _mapping(value, subject)
    required = {"contract", "expected", "profile", "regenerated"}
    evidence_fields = {"evidence", "evidence_contract", "evidence_definition"}
    if not required <= set(item) <= required | evidence_fields or set(
        item
    ) & evidence_fields not in (set(), evidence_fields):
        raise ReproductionResultError(f"{subject} has incorrect fields")
    if item["contract"] != COMPARISON_CONTRACT:
        raise ReproductionResultError(f"{subject} contract is unsupported")
    profile = _choice(item["profile"], PROFILES, f"{subject}.profile")
    evidence = item.get("evidence", [])
    if not isinstance(evidence, list) or not all(
        isinstance(record, Mapping) for record in evidence
    ):
        raise ReproductionResultError(f"{subject}.evidence is invalid")
    definition = item.get("evidence_definition")
    if definition is not None and (
        not isinstance(definition, str)
        or item.get("evidence_contract") != EVIDENCE_COMPARISON_RESULT_CONTRACT
    ):
        raise ReproductionResultError(f"{subject}.evidence contract is invalid")
    return ComparisonRecord(
        profile,
        _fingerprint_or_none(item["expected"], f"{subject}.expected"),
        _fingerprint_or_none(item["regenerated"], f"{subject}.regenerated"),
        definition,
        tuple(dict(record) for record in cast(list[Mapping[str, object]], evidence)),
    )


def _valid_evidence_comparison(value: Mapping[str, object]) -> bool:
    fields = {
        "definition",
        "expected",
        "id",
        "matched",
        "regenerated",
        "tolerance",
    }
    expected = value.get("expected")
    regenerated = value.get("regenerated")
    tolerance = value.get("tolerance")
    if (
        set(value) != fields
        or not isinstance(value.get("definition"), str)
        or re.fullmatch(r"[0-9a-f]{64}", cast(str, value["definition"])) is None
        or not isinstance(value.get("id"), str)
        or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", cast(str, value["id"]))
        is None
        or not isinstance(value.get("matched"), bool)
        or not isinstance(expected, list)
        or not isinstance(regenerated, list)
        or not expected
        or len(expected) != len(regenerated)
        or not all(isinstance(item, Mapping) for item in (*expected, *regenerated))
        or tolerance is not None
        and (
            not isinstance(tolerance, Mapping)
            or set(tolerance) != {"absolute"}
            or not isinstance(tolerance.get("absolute"), str)
        )
    ):
        return False
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True


def _decode_run(value: object, index: int, *, current: bool) -> RunResult:
    item = _mapping(value, f"runs[{index}]")
    fields = {
        "accepted_at",
        "artifact_outcomes",
        "finished_at",
        "folder",
        "include_all",
        "executions",
        "run_id",
        "status",
        "target",
    }
    fields.add("command_outcomes")
    if current:
        fields.add("command_records")
    if set(item) != fields:
        raise ReproductionResultError(f"runs[{index}] has incorrect fields")
    target = _target(item["target"])
    counts = _counts(item["artifact_outcomes"])
    folder = _folder(item["folder"])
    executions = _execution_timings(item["executions"])
    include_all = item["include_all"]
    if not isinstance(include_all, bool):
        raise ReproductionResultError("run include_all must be boolean")
    status = _choice(item["status"], RUN_STATUSES, f"runs[{index}].status")
    finished = item["finished_at"]
    if finished is not None:
        finished = _timestamp(finished, f"runs[{index}].finished_at")
    if status in {"complete", "failed"} and finished is None:
        raise ReproductionResultError("complete or failed run needs finished_at")
    if status == "stopped" and finished is not None:
        raise ReproductionResultError("stopped run must remain resumable")
    return RunResult(
        _run_id(item["run_id"]),
        target,
        include_all,
        status,
        _timestamp(item["accepted_at"], f"runs[{index}].accepted_at"),
        cast(str | None, finished),
        counts,
        folder,
        executions,
        (
            None
            if item["command_outcomes"] is None
            else _command_counts(item["command_outcomes"])
        ),
        (
            tuple(
                _decode_command_record(raw, record_index)
                for record_index, raw in enumerate(
                    _sequence(item["command_records"], f"runs[{index}].command_records")
                )
            )
            if current and item["command_records"] is not None
            else None
        ),
    )


def _execution_timings(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 2_048:
        raise ReproductionResultError("run executions are invalid")
    decoded: list[Mapping[str, object]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        item = _mapping(raw, f"run.executions[{index}]")
        if set(item) != {
            "elapsed_seconds",
            "entry",
            "execution_id",
            "finished_at",
            "started_at",
        }:
            raise ReproductionResultError("run execution timing fields are invalid")
        entry = _entry(item["entry"], f"run.executions[{index}].entry")
        identity = item["execution_id"]
        if (
            not isinstance(identity, str)
            or PYRUN_EXECUTION_RE.fullmatch(identity) is None
        ):
            raise ReproductionResultError("run execution ID is invalid")
        started = _timestamp(item["started_at"], f"run.executions[{index}].started_at")
        finished = item["finished_at"]
        if finished is not None:
            finished = _timestamp(finished, f"run.executions[{index}].finished_at")
            if finished < started:
                raise ReproductionResultError(
                    "run execution finished before it started"
                )
        elapsed = item["elapsed_seconds"]
        if (
            not isinstance(elapsed, (int, float))
            or isinstance(elapsed, bool)
            or elapsed < 0
        ):
            raise ReproductionResultError("run execution elapsed time is invalid")
        key = (entry, identity)
        if key in identities:
            raise ReproductionResultError("run execution timing is duplicated")
        identities.add(key)
        decoded.append(
            {
                "elapsed_seconds": float(elapsed),
                "entry": entry,
                "execution_id": identity,
                "finished_at": finished,
                "started_at": started,
            }
        )
    return tuple(decoded)


def _validate_results(results: ReproductionResults) -> None:
    _summary_path(results.summary)
    _timestamp(results.updated_at, "updated_at")
    if len(results.artifacts) > MAX_ARTIFACT_RESULTS:
        raise ReproductionResultError("too many artifact results")
    if len(results.commands) > MAX_COMMAND_RESULTS:
        raise ReproductionResultError("too many command results")
    if len(results.runs) > MAX_RUN_RESULTS:
        raise ReproductionResultError("too many run results")
    keys = [(item.entry, item.artifact) for item in results.artifacts]
    if keys != sorted(keys, key=lambda key: (_entry_key(key[0]), key[1])):
        raise ReproductionResultError("artifact results are not canonically ordered")
    if len(keys) != len(set(keys)):
        raise ReproductionResultError("artifact result identities are duplicated")
    command_keys = [(item.entry, item.execution_id) for item in results.commands]
    if command_keys != sorted(
        command_keys, key=lambda key: (_entry_key(key[0]), key[1])
    ):
        raise ReproductionResultError("command results are not canonically ordered")
    if len(command_keys) != len(set(command_keys)):
        raise ReproductionResultError("command result identities are duplicated")
    run_ids = [item.run_id for item in results.runs]
    if len(run_ids) != len(set(run_ids)):
        raise ReproductionResultError("run IDs are duplicated")
    if list(results.runs) != sorted(results.runs, key=_run_key):
        raise ReproductionResultError("run results are not canonically ordered")


def _counts(value: object) -> Mapping[str, int]:
    item = _mapping(value, "artifact_outcomes")
    if set(item) != set(OUTCOMES) or any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in item.values()
    ):
        raise ReproductionResultError("artifact outcome counts are invalid")
    return {name: cast(int, item[name]) for name in OUTCOMES}


def _command_counts(value: object) -> Mapping[str, int]:
    item = _mapping(value, "command_outcomes")
    if set(item) != set(COMMAND_OUTCOMES) or any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in item.values()
    ):
        raise ReproductionResultError("command outcome counts are invalid")
    result = {name: cast(int, item[name]) for name in COMMAND_OUTCOMES}
    if result["total"] != sum(
        result[name] for name in COMMAND_OUTCOMES if name != "total"
    ):
        raise ReproductionResultError("command outcome counts do not reconcile")
    return result


def _command_records(
    value: Sequence[Mapping[str, object]],
    expected: Mapping[str, int] | None,
) -> None:
    if len(value) > MAX_COMMAND_RESULTS:
        raise ReproductionResultError("too many run command records")
    decoded = tuple(
        _decode_command_record(item, index) for index, item in enumerate(value)
    )
    keys = [
        (cast(str, item["entry"]), cast(str, item["execution_id"])) for item in decoded
    ]
    if keys != sorted(keys, key=lambda key: (_entry_key(key[0]), key[1])):
        raise ReproductionResultError("run command records are not canonically ordered")
    if len(keys) != len(set(keys)):
        raise ReproductionResultError("run command record identities are duplicated")
    if expected is None:
        raise ReproductionResultError("run command records need command outcomes")
    counts = {name: 0 for name in COMMAND_OUTCOMES}
    for item in decoded:
        bucket = item["bucket"]
        reason = cast(str, item["reason"])
        leaf = (
            cast(str, bucket)
            if bucket in {"blocked", "failed", "succeeded"}
            else "not_automatic"
            if bucket == "skipped-by-policy"
            else reason
        )
        if leaf not in counts or leaf == "total":
            raise ReproductionResultError("run command record reason is invalid")
        counts[leaf] += 1
    counts["total"] = len(decoded)
    if counts != dict(expected):
        raise ReproductionResultError("run command records do not reconcile")


def _folder(value: object) -> RunFolder:
    item = _mapping(value, "folder")
    if set(item) != {"availability", "path"}:
        raise ReproductionResultError("run folder has incorrect fields")
    availability = _choice(
        item["availability"], ("available", "unknown"), "folder.availability"
    )
    path = _portable_path(item["path"], "folder.path")
    if not is_canonical_run_path(path):
        raise ReproductionResultError("run folder path is not canonical")
    return RunFolder(path, availability)


def _target(value: object) -> Mapping[str, object]:
    item = _mapping(value, "target")
    if set(item) != {"entry", "kind"} or item["kind"] not in {"entry", "log"}:
        raise ReproductionResultError("run target is invalid")
    entry = item["entry"]
    if item["kind"] == "entry":
        entry = _entry(entry, "target.entry")
    elif entry is not None:
        raise ReproductionResultError("log target entry must be null")
    return {"entry": entry, "kind": item["kind"]}


def _fingerprint_or_none(value: object, subject: str) -> Fingerprint | None:
    if value is None:
        return None
    try:
        return parse_fingerprint(value, subject)
    except DataContractError as error:
        raise ReproductionResultError(str(error)) from error


def _artifact_path(value: object, subject: str) -> str:
    path = _string(value, subject)
    pure = PurePosixPath(path)
    if pure.is_absolute():
        if (
            path.startswith("//")
            or "\\" in path
            or len(pure.parts) == 1
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != path
        ):
            raise ReproductionResultError(f"{subject} is not a canonical path")
        return path
    suffix = path.removeprefix("<project>/")
    portable = _portable_path(suffix, subject)
    return f"<project>/{portable}" if path.startswith("<project>/") else portable


def _summary_path(value: object) -> str:
    path = _portable_path(value, "summary")
    if not path.endswith(".md"):
        raise ReproductionResultError("summary path must end in .md")
    return path


def _portable_path(value: object, subject: str) -> str:
    text = _string(value, subject)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or "\\" in text
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != text
    ):
        raise ReproductionResultError(f"{subject} is not a portable path")
    return text


def _timestamp(value: object, subject: str) -> str:
    text = _string(value, subject)
    if TIMESTAMP_RE.fullmatch(text) is None:
        raise ReproductionResultError(f"{subject} is not a canonical timestamp")
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ReproductionResultError(f"{subject} is invalid") from error
    if parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != text:
        raise ReproductionResultError(f"{subject} is invalid")
    return text


def _entry(value: object, subject: str) -> str:
    text = _string(value, subject)
    if ENTRY_ID_RE.fullmatch(text) is None:
        raise ReproductionResultError(f"{subject} is invalid")
    return text


def _execution(value: object) -> str:
    text = _string(value, "execution_id")
    if PYRUN_EXECUTION_RE.fullmatch(text) is None:
        raise ReproductionResultError("execution ID is invalid")
    return text


def _run_id(value: object) -> str:
    text = _string(value, "run_id")
    if RUN_ID_RE.fullmatch(text) is None:
        raise ReproductionResultError("run ID is invalid")
    return text


def _choice(value: object, choices: Iterable[str], subject: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ReproductionResultError(f"{subject} is unsupported")
    return value


def _mapping(value: object, subject: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ReproductionResultError(f"{subject} must be an object")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, subject: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ReproductionResultError(f"{subject} must be an array")
    return cast(Sequence[object], value)


def _string(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReproductionResultError(f"{subject} must be a nonempty string")
    return value


def _sha256_digest(value: object, subject: str) -> str:
    text = _string(value, subject)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ReproductionResultError(f"{subject} is invalid")
    return text


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReproductionResultError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _artifact_key(value: ArtifactResult) -> tuple[tuple[int, str], str]:
    return _entry_key(value.entry), value.artifact


def _command_key(value: CommandResult) -> tuple[tuple[int, str], str]:
    return _entry_key(value.entry), value.execution_id


def _entry_key(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"e(?P<number>[0-9]+)(?P<suffix>[a-z]?)", value)
    if match is None:
        raise ReproductionResultError(f"invalid entry ID: {value!r}")
    return int(match.group("number")), value


def _run_key(value: RunResult) -> tuple[float, str]:
    timestamp = datetime.fromisoformat(
        value.accepted_at.removesuffix("Z") + "+00:00"
    ).timestamp()
    return -timestamp, value.run_id


def _entry_heading(entry: str, context: ReportContext) -> str:
    presentation = context.entries.get(entry)
    if presentation is None:
        return f"## {entry}"
    return f"## [{entry} — {presentation.title}]({presentation.document})"


def _folder_label(folder: RunFolder, report_root: Path | None) -> str:
    if folder.availability != "available" or report_root is None:
        return f"`{folder.path}` ({folder.availability})"
    project_root = report_root
    while project_root.parent != project_root and not (project_root / ".git").exists():
        project_root = project_root.parent
    target = project_root / PurePosixPath(folder.path)
    relative = os.path.relpath(target, start=report_root).replace(os.sep, "/")
    return f"[{folder.path}]({relative})"


def _run_with_folder(run: RunResult, availability: str) -> RunResult:
    if run.folder.availability == availability:
        return run
    return RunResult(
        run.run_id,
        run.target,
        run.include_all,
        run.status,
        run.accepted_at,
        run.finished_at,
        run.artifact_outcomes,
        RunFolder(run.folder.path, availability),
        run.executions,
        run.command_outcomes,
        run.command_records,
    )


def _escape_code(value: str) -> str:
    return value.replace("`", "\\`").replace("|", "\\|")


def load_results_or_empty(
    path: Path, *, summary: str, updated_at: str, replace_outdated: bool = False
) -> ReproductionResults:
    """Load current results or replace outdated generated state when authorized."""

    if not path.exists() and not path.is_symlink():
        return empty_reproduction_results(summary, updated_at=updated_at)
    try:
        result = load_reproduction_results(path)
    except ReproductionResultSchemaError as error:
        if replace_outdated:
            return empty_reproduction_results(summary, updated_at=updated_at)
        raise ActionError(
            "reproduction.results.schema_unsupported", str(error)
        ) from error
    except ReproductionResultError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
    if result.summary != summary:
        raise ActionError(
            "reproduction.results.invalid", "result summary identity changed"
        )
    return result
