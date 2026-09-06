"""Strict cumulative reproduction results and their human projection."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence, cast

from research_log_data import DataContractError, Fingerprint, parse_fingerprint
from validation.human_projection import ReportContext
from validation.pyrun_state import PYRUN_EXECUTION_RE

from .context import ENTRY_ID_RE
from .model import ActionError
from .reproduction_paths import resolve_project_tmp
from .reproduction_planner import ReproductionStateProjection

RESULT_SCHEMA = "research-log-reproduction-result/1"
COMPARISON_CONTRACT = "research-log-reproduction-comparison/1"
MAX_RESULT_BYTES = 64 << 20
MAX_ARTIFACT_RESULTS = 10_000
MAX_RUN_RESULTS = 10_000
MAX_QUERY_RESULTS = 50
TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
OUTCOMES = ("matched", "changed", "failed", "comparison_failed", "skipped")
RUN_STATUSES = ("complete", "failed", "stopped")
PROFILES = (
    "directory",
    "image",
    "json",
    "named_array",
    "opaque_file",
    "table",
    "text",
)
REASONS = {
    "baseline_unavailable",
    "boundary_changed",
    "boundary_unavailable",
    "comparator_error",
    "content_changed",
    "cross_log_generated_input",
    "dependency_cycle",
    "dependency_failed",
    "execution_failed",
    "generation_failed",
    "graph_limit",
    "missing_input",
    "missing_producer",
    "multiple_producers",
    "output_missing",
    "outside_entry",
    "resource_limit",
    "safety_failure",
    "slow",
    "stop_requested",
    "unsupported_format",
    "worker_cleanup_incomplete",
    "worker_survived",
}


class ReproductionResultError(ValueError):
    """One exact cumulative-result contract failure."""


@dataclass(frozen=True)
class ComparisonRecord:
    """One exact decoded comparison identity."""

    profile: str
    expected: Fingerprint | None
    regenerated: Fingerprint | None

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

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": COMPARISON_CONTRACT,
            "expected": self.expected.as_dict() if self.expected is not None else None,
            "profile": self.profile,
            "regenerated": (
                self.regenerated.as_dict() if self.regenerated is not None else None
            ),
        }


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
class RunFolder:
    """One retained or availability-unknown run directory."""

    path: str
    availability: str

    def __post_init__(self) -> None:
        _choice(self.availability, ("available", "unknown"), "folder.availability")
        path = _portable_path(self.path, "folder.path")
        if not path.startswith("tmp/reproduce-"):
            raise ReproductionResultError("run folder path is not canonical")

    def as_dict(self) -> dict[str, str]:
        return {"availability": self.availability, "path": self.path}


@dataclass(frozen=True)
class RunResult:
    """One published terminal lifecycle event."""

    run_id: str
    target: Mapping[str, object]
    include_slow: bool
    status: str
    accepted_at: str
    finished_at: str | None
    artifact_outcomes: Mapping[str, int]
    folder: RunFolder

    def __post_init__(self) -> None:
        _run_id(self.run_id)
        _target(self.target)
        if not isinstance(self.include_slow, bool):
            raise ReproductionResultError("run include_slow must be boolean")
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

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_at": self.accepted_at,
            "artifact_outcomes": dict(self.artifact_outcomes),
            "finished_at": self.finished_at,
            "folder": self.folder.as_dict(),
            "include_slow": self.include_slow,
            "run_id": self.run_id,
            "status": self.status,
            "target": dict(self.target),
        }


@dataclass(frozen=True)
class ReproductionResults:
    """The complete current artifact map and retained run history."""

    summary: str
    updated_at: str
    artifacts: tuple[ArtifactResult, ...]
    runs: tuple[RunResult, ...]

    def __post_init__(self) -> None:
        _validate_results(self)

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": [item.as_dict() for item in self.artifacts],
            "runs": [item.as_dict() for item in self.runs],
            "schema": RESULT_SCHEMA,
            "summary": self.summary,
            "updated_at": self.updated_at,
        }

    def serialized(self) -> str:
        text = json.dumps(
            self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
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
        if set(item) != {"artifacts", "runs", "schema", "summary", "updated_at"}:
            raise ReproductionResultError("result has incorrect fields")
        if item["schema"] != RESULT_SCHEMA:
            raise ReproductionResultError("result schema is unsupported")
        artifacts = tuple(
            _decode_artifact(value, index)
            for index, value in enumerate(_sequence(item["artifacts"], "artifacts"))
        )
        runs = tuple(
            _decode_run(value, index)
            for index, value in enumerate(_sequence(item["runs"], "runs"))
        )
        result = cls(
            _string(item["summary"], "summary"),
            _timestamp(item["updated_at"], "updated_at"),
            artifacts,
            runs,
        )
        if text != result.serialized():
            raise ReproductionResultError("result serialization is not canonical")
        return result


@dataclass(frozen=True)
class ArtifactCurrentness:
    """One derived, non-persisted currentness projection."""

    current: bool
    reason: str | None = None


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
    updated_at: str,
    reachable: set[tuple[str, str]] | None = None,
) -> ReproductionResults:
    """Replace only published cases and append one unique run record."""

    if any(item.run_id == run.run_id for item in current.runs):
        raise ReproductionResultError(f"duplicate run ID: {run.run_id}")
    replacements = {(item.entry, item.artifact): item for item in artifacts}
    if len(replacements) != len(artifacts):
        raise ReproductionResultError("published artifacts are duplicated")
    merged = {
        (item.entry, item.artifact): item
        for item in current.artifacts
        if (item.entry, item.artifact) not in replacements
    }
    merged.update(replacements)
    if reachable is not None:
        merged = {key: value for key, value in merged.items() if key in reachable}
    return ReproductionResults(
        current.summary,
        _timestamp(updated_at, "updated_at"),
        tuple(sorted(merged.values(), key=_artifact_key)),
        tuple(sorted((*current.runs, run), key=_run_key)),
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
        )
    retained: list[RunResult] = []
    for run in results.runs:
        parts = PurePosixPath(run.folder.path).parts
        target = temporary_root.joinpath(*parts[1:])
        try:
            parent_available = target.parent.is_dir() and not target.parent.is_symlink()
            exists = target.exists() or target.is_symlink()
        except OSError:
            retained.append(_run_with_folder(run, "unknown"))
            continue
        if not exists and parent_available:
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
        last_run = state.last_runs.get(execution_key)
        if last_run is not None and last_run > item.recorded_at:
            currentness[key] = ArtifactCurrentness(False, "execution_reran")
        else:
            currentness[key] = ArtifactCurrentness(True)
    projected = ReproductionResults(
        results.summary, results.updated_at, artifacts, results.runs
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
    counts = {outcome: 0 for outcome in OUTCOMES}
    stale = 0
    for item in artifacts:
        counts[item.outcome] += 1
        if not currentness.get(
            (item.entry, item.artifact), ArtifactCurrentness(True)
        ).current:
            stale += 1
    latest = next((run for run in results.runs if run.status == "complete"), None)
    lines = [
        "# Reproduction",
        "",
        f"Generated: `{results.updated_at}`",
        "",
        "Latest completed run: "
        + (f"`{latest.run_id}`" if latest is not None else "none"),
        "",
        "Current artifacts: "
        + ", ".join(f"{counts[name]} {name}" for name in OUTCOMES)
        + f", {stale} stale.",
    ]
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
            f"entry {run.target['entry']}"
            if run.target["kind"] == "entry"
            else "log"
        )
        folder = _folder_label(run.folder, folder_links_from)
        lines.append(
            f"| `{run.run_id}` | {target} | {run.status} | "
            f"`{run.finished_at or run.accepted_at}` | {folder} |"
        )
    if not results.runs:
        lines.append("| — | — | not yet reproduced | — | — |")
    return "\n".join(lines).rstrip() + "\n"


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
                (currentness or {}).get(
                    (item.entry, item.artifact), ArtifactCurrentness(True)
                ).reason
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


def _decode_comparison(value: object, subject: str) -> ComparisonRecord:
    item = _mapping(value, subject)
    if set(item) != {"contract", "expected", "profile", "regenerated"}:
        raise ReproductionResultError(f"{subject} has incorrect fields")
    if item["contract"] != COMPARISON_CONTRACT:
        raise ReproductionResultError(f"{subject} contract is unsupported")
    profile = _choice(item["profile"], PROFILES, f"{subject}.profile")
    return ComparisonRecord(
        profile,
        _fingerprint_or_none(item["expected"], f"{subject}.expected"),
        _fingerprint_or_none(item["regenerated"], f"{subject}.regenerated"),
    )


def _decode_run(value: object, index: int) -> RunResult:
    item = _mapping(value, f"runs[{index}]")
    fields = {
        "accepted_at",
        "artifact_outcomes",
        "finished_at",
        "folder",
        "include_slow",
        "run_id",
        "status",
        "target",
    }
    if set(item) != fields:
        raise ReproductionResultError(f"runs[{index}] has incorrect fields")
    target = _target(item["target"])
    counts = _counts(item["artifact_outcomes"])
    folder = _folder(item["folder"])
    include_slow = item["include_slow"]
    if not isinstance(include_slow, bool):
        raise ReproductionResultError("run include_slow must be boolean")
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
        include_slow,
        status,
        _timestamp(item["accepted_at"], f"runs[{index}].accepted_at"),
        cast(str | None, finished),
        counts,
        folder,
    )


def _validate_results(results: ReproductionResults) -> None:
    _summary_path(results.summary)
    _timestamp(results.updated_at, "updated_at")
    if len(results.artifacts) > MAX_ARTIFACT_RESULTS:
        raise ReproductionResultError("too many artifact results")
    if len(results.runs) > MAX_RUN_RESULTS:
        raise ReproductionResultError("too many run results")
    keys = [(item.entry, item.artifact) for item in results.artifacts]
    if keys != sorted(keys, key=lambda key: (_entry_key(key[0]), key[1])):
        raise ReproductionResultError("artifact results are not canonically ordered")
    if len(keys) != len(set(keys)):
        raise ReproductionResultError("artifact result identities are duplicated")
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


def _folder(value: object) -> RunFolder:
    item = _mapping(value, "folder")
    if set(item) != {"availability", "path"}:
        raise ReproductionResultError("run folder has incorrect fields")
    availability = _choice(
        item["availability"], ("available", "unknown"), "folder.availability"
    )
    path = _portable_path(item["path"], "folder.path")
    if not path.startswith("tmp/reproduce-"):
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
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
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


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReproductionResultError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _artifact_key(value: ArtifactResult) -> tuple[tuple[int, str], str]:
    return _entry_key(value.entry), value.artifact


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
    while project_root.parent != project_root and not (
        project_root / ".git"
    ).exists():
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
        run.include_slow,
        run.status,
        run.accepted_at,
        run.finished_at,
        run.artifact_outcomes,
        RunFolder(run.folder.path, availability),
    )


def _escape_code(value: str) -> str:
    return value.replace("`", "\\`").replace("|", "\\|")


def load_results_or_empty(
    path: Path, *, summary: str, updated_at: str
) -> ReproductionResults:
    """Load existing results or create an in-memory empty authority."""

    if not path.exists() and not path.is_symlink():
        return empty_reproduction_results(summary, updated_at=updated_at)
    try:
        result = load_reproduction_results(path)
    except ReproductionResultError as error:
        raise ActionError("reproduction.results.invalid", str(error)) from error
    if result.summary != summary:
        raise ActionError(
            "reproduction.results.invalid", "result summary identity changed"
        )
    return result
