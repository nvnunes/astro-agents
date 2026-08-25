"""CLI-owned progressive validation controller for one maintained summary."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, cast

from .adjudication import ORPHAN_TARGET
from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import apply_review_decisions
from .inventory import infer_project_root
from .observations import (
    ObservationSession,
    observe_outcome_dependencies,
    outcomes_are_compatible,
)
from .orphan_rules import inherited_basis
from .render import assemble_records
from .review_exchange import (
    CONTEXT_PROJECTION_VERSION,
    accept_review_page,
    context_request_key,
    create_exchange,
    decisions_to_actions,
    durable_review_judgments,
    empty_review_session_refresh_context,
    finish_legacy_ordinary_session,
    finish_review_session,
    load_decisions,
    resume_legacy_ordinary_exchange,
    resume_review_session,
    reusable_review_actions,
    reusable_review_subjects,
    review_session_reference,
)
from .runtime import (
    RULES_VERSION,
    prepare_adjudication_record,
    render_policy,
    scan_policy,
)
from .scan import ScanRequest, scan_log
from .target_records import (
    CACHE_FILENAME,
    RECORD_FILENAME,
    append_judgment_batch,
    assert_no_retired_artifacts,
    compact_cached_judgments,
    empty_cache,
    empty_record,
    empty_record_shell,
    hydrate_record_rows,
    hydrate_record_shell,
    inspect_target_cleanup,
    load_cache,
    load_judgments_for_subjects,
    load_record_header_with_source,
    projection_for,
    publish_target_bundle,
    record_row_count,
    write_record_and_cache,
)


@dataclass
class ValidationProgress:
    """Durable state and publication mode carried across semantic review."""

    record: dict[str, Any]
    cache: dict[str, Any]
    state_status: str
    publish: bool


@dataclass(frozen=True)
class CompletionRequest:
    """Inputs needed to assemble and optionally publish one completed log."""

    summary: str
    output_dir: Path
    scan: ScanRecord
    adjudication: AdjudicationRecord
    prior_record: Mapping[str, Any]
    review_judgments: list[dict[str, Any]]
    publish: bool


@dataclass(frozen=True)
class ValidationRequest:
    """Public target-validation inputs for one maintained summary."""

    summary_path: Path
    decision_file: Path | None = None
    result_date: str | None = None
    jobs: int = 8
    publish: bool = True
    mode: str = "standard"


@dataclass
class LoadedValidation:
    """Resolved request paths and loaded target state for one invocation."""

    request: ValidationRequest
    summary_path: Path
    output_dir: Path
    project_root: Path
    record_summary: str
    record: dict[str, Any]
    cache: dict[str, Any]
    state_status: str

    @property
    def summary(self) -> str:
        return self.summary_path.as_posix()

    def progress(self) -> ValidationProgress:
        return ValidationProgress(
            self.record,
            self.cache,
            self.state_status,
            self.request.publish,
        )

def _record_has_progress(record: Mapping[str, Any]) -> bool:
    return bool(
        record_row_count(record, "outcomes")
        or record_row_count(record, "judgments")
    )


def _relative_summary(summary_path: Path, project_root: Path) -> str:
    try:
        return summary_path.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise ValidationToolError(
            "maintained summary is outside its inferred project root"
        ) from exc


def _load_target_state(
    output_dir: Path, summary: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    assert_no_retired_artifacts(output_dir)
    record_path = output_dir / RECORD_FILENAME
    cache_path = output_dir / CACHE_FILENAME
    if record_path.is_file():
        record, source_version = load_record_header_with_source(
            record_path,
            expected_summary=summary,
        )
        cache, cache_status = load_cache(cache_path)
        return record, cache, f"native-v{source_version}:{cache_status}"
    return empty_record_shell(summary, RULES_VERSION), empty_cache(), "new"


def _coherent_report_projection(
    output_dir: Path, record: Mapping[str, Any]
) -> bool:
    projection = record.get("projection")
    if not isinstance(projection, Mapping):
        return False
    report_path = output_dir / "validation.md"
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return projection_for(record, report_text) == projection


def _compatible_cached_dependencies(
    summary_path: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
) -> ObservationSession | None:
    session = ObservationSession()
    project_root = infer_project_root(summary_path)
    completion_dependencies = list(record.get("completion_dependencies", []))
    if not completion_dependencies:
        return None
    completion_observations = observe_outcome_dependencies(
        session,
        [{"dependencies": completion_dependencies}],
        cache.get("files", {}),
        project_root,
    )
    if not outcomes_are_compatible(
        [{"dependencies": completion_dependencies}], completion_observations
    ):
        return None
    outcomes = list(record.get("outcomes", []))
    if outcomes:
        observed = observe_outcome_dependencies(
            session,
            outcomes,
            cache.get("files", {}),
            project_root,
        )
        if not outcomes_are_compatible(outcomes, observed):
            return None
    return session


def _cached_completion(
    request: ValidationRequest,
    output_dir: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
    cache_status: str,
) -> dict[str, Any] | None:
    summary_path = request.summary_path.resolve()
    ready = (
        cache_status == "loaded"
        and record.get("validation_rules_version") == RULES_VERSION
        and record.get("result") is not None
        and record.get("continuation") is None
        and _coherent_report_projection(output_dir, record)
    )
    session = (
        _compatible_cached_dependencies(summary_path, record, cache)
        if ready
        else None
    )
    if session is None:
        return None
    cleanup = compact_cached_judgments(
        output_dir, record, cache, publish=request.publish
    )
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (output_dir / RECORD_FILENAME).as_posix(),
        "cache": (output_dir / CACHE_FILENAME).as_posix(),
        "report": (output_dir / "validation.md").as_posix(),
        "progress_retained": True,
        "cached": True,
        "recovered_continuation": False,
        "diagnostics": session.diagnostics.as_dict(),
        "cleanup": cleanup,
    }


def _diagnostics(metrics: Mapping[str, Any]) -> dict[str, int]:
    return {
        "metadata_checked": int(metrics.get("files_identified", 0)),
        "hashes_reused": int(metrics.get("files_reused", 0)),
        "files_hashed": int(metrics.get("files_hashed", 0)),
        "bytes_hashed": int(metrics.get("bytes_hashed", 0)),
        "content_changed": 0,
    }


def _target_record(
    summary: str, assembly: Any, prior: Mapping[str, Any]
) -> dict[str, Any]:
    state = assembly.outcome_inputs
    record = empty_record(summary, state.rules_version)
    record["rule_dependencies"] = {
        "components": copy.deepcopy(state.component_versions),
        "input_projections": copy.deepcopy(state.input_projection_versions),
    }
    by_identity = {
        judgment["identity"]: copy.deepcopy(judgment)
        for judgment in prior.get("judgments", [])
    }
    record["judgments"] = list(by_identity.values())
    outcomes: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for stored in state.completed_checks:
        outcome = copy.deepcopy(stored)
        identity = (
            str(outcome["entry"]),
            str(outcome["target"]),
            str(outcome["check"]),
            str(outcome["compatibility_identity"]),
        )
        outcomes[identity] = outcome
    record["outcomes"] = list(outcomes.values())
    record["result"] = assembly.result()
    record["failures"] = copy.deepcopy(assembly.failures)
    manifest = prior.get("_sharded_manifest")
    if not isinstance(manifest, Mapping):
        raise ValidationToolError("canonical validation state lacks a manifest")
    record["_sharded_manifest"] = copy.deepcopy(manifest)
    return record


def _target_cache(assembly: Any, scan: ScanRecord) -> dict[str, Any]:
    state = assembly.outcome_inputs
    files = copy.deepcopy(state.input_files)
    for path, identity in state.file_identities.items():
        files.setdefault(path, copy.deepcopy(identity))
    for identity, raw_path in scan.get("resolved_paths", {}).items():
        if identity not in files:
            continue
        try:
            metadata = Path(raw_path).stat()
        except OSError:
            continue
        files[identity] = {
            **files[identity],
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }
    cache = empty_cache()
    cache["files"] = files
    cache["directories"] = copy.deepcopy(state.directory_memberships)
    cache["inspections"] = copy.deepcopy(state.mechanical_checks)
    return cache


def _merge_review_judgments(
    record: dict[str, Any], judgments: list[dict[str, Any]]
) -> None:
    by_identity = {
        judgment["identity"]: copy.deepcopy(judgment)
        for judgment in [*record["judgments"], *judgments]
    }
    record["judgments"] = list(by_identity.values())


def _partial_adjudication(
    adjudication: AdjudicationRecord,
) -> AdjudicationRecord:
    """Return rows whose current checks no longer require semantic work."""

    partial = copy.deepcopy(adjudication)
    pending_targets = {
        (str(item.get("entry")), str(item.get("identity")))
        for item in partial["review_queue"]
        if item.get("collections")
        and item.get("entry") is not None
        and item.get("identity") is not None
    }
    partial["review_queue"] = []
    partial["summary"] = [
        row for row in partial["summary"] if row.get("provenance") is not None
    ]
    for entry in partial["entries"]:
        entry_id = str(entry.get("id", ""))
        entry["targets"] = [
            row
            for row in entry.get("targets", [])
            if row.get("target") != ORPHAN_TARGET
            and (entry_id, str(row.get("target", ""))) not in pending_targets
            and all(
                row.get(check) is not None
                for check in ("integrity", "provenance", "reproducibility")
            )
        ]
        entry["orphan_items"] = [
            item
            for item in entry.get("orphan_items", [])
            if item.get("decision") == "accepted"
        ]
    return partial


def _merge_outcomes(
    record: dict[str, Any], outcomes: list[dict[str, Any]]
) -> None:
    by_identity = {
        (
            row["entry"],
            row["target"],
            row["check"],
            row["compatibility_identity"],
        ): copy.deepcopy(row)
        for row in [*record["outcomes"], *outcomes]
    }
    record["outcomes"] = list(by_identity.values())


def _materialize_partial_progress(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    progress: ValidationProgress,
) -> None:
    """Persist deterministic rows without assembling an incomplete report."""

    output_dir = Path(scan["project_root"]) / scan["log_root"]
    partial_adjudication = _partial_adjudication(adjudication)
    partial_scan = copy.deepcopy(scan)
    retained_summary_items = {
        row["source_item"] for row in partial_adjudication["summary"]
    }
    partial_scan["summary_items"] = [
        item
        for item in partial_scan["summary_items"]
        if item["identity"] in retained_summary_items
    ]
    assembly = assemble_records(
        partial_adjudication,
        partial_scan,
        output_dir,
        render_policy(),
    )
    partial = _target_record(scan["summary"], assembly, progress.record)
    progress.record["rule_dependencies"] = partial["rule_dependencies"]
    _merge_outcomes(progress.record, partial["outcomes"])
    progress.record["failures"] = copy.deepcopy(partial["failures"])
    progress.cache = _target_cache(assembly, scan)


def _review_required(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    progress: ValidationProgress,
    context_levels: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    _materialize_partial_progress(scan, adjudication, progress)
    result = create_exchange(
        scan,
        adjudication,
        {
            "summary": scan["summary"],
            "state_status": progress.state_status,
            "publish": progress.publish,
        },
        context_levels,
    )
    progress.record["continuation"] = {
        "kind": "paged",
        "session": result["session"],
        "session_identity": result["session_identity"],
        "review_kind": result["review_kind"],
    }
    if progress.publish:
        output_dir = Path(scan["project_root"]) / scan["log_root"]
        progress.record = write_record_and_cache(
            output_dir, progress.record, progress.cache
        )
    result["progress_retained"] = bool(
        _record_has_progress(progress.record)
    )
    return result


def _complete_adjudication(
    request: CompletionRequest,
) -> tuple[dict[str, Any], Any, dict[str, int]]:
    assembly = assemble_records(
        request.adjudication,
        request.scan,
        request.output_dir,
        render_policy(),
    )
    target_record = _target_record(
        request.summary, assembly, request.prior_record
    )
    _merge_review_judgments(target_record, request.review_judgments)
    target_record["continuation"] = None
    target_cache = _target_cache(assembly, request.scan)
    summary_identity = target_cache["files"].get(request.summary)
    if not isinstance(summary_identity, Mapping):
        raise ValidationToolError(
            "completed validation lacks the maintained-summary identity"
        )
    target_record["completion_dependencies"] = [
        {
            "path": request.summary,
            "role": "summary",
            "identity": copy.deepcopy(summary_identity),
        }
    ]
    target_record["projection"] = projection_for(
        target_record, assembly.report_text
    )
    superseded_subjects = _superseded_orphan_subjects(request.adjudication)
    if request.publish:
        cleanup = publish_target_bundle(
            request.output_dir,
            assembly.report_text,
            target_record,
            target_cache,
            superseded_subjects,
        )
    else:
        cleanup = inspect_target_cleanup(
            request.output_dir, target_record, superseded_subjects
        )
    return target_record, assembly, cleanup


def _superseded_orphan_subjects(
    adjudication: AdjudicationRecord,
) -> list[dict[str, str]]:
    subjects = []
    for entry in adjudication.get("entries", []):
        entry_id = entry.get("id")
        if not isinstance(entry_id, str):
            continue
        for item in entry.get("orphan_items", []):
            identity = item.get("identity")
            if isinstance(identity, str) and inherited_basis(item.get("basis")):
                subjects.append(
                    {
                        "kind": "orphan_candidate",
                        "entry": entry_id,
                        "identity": identity,
                    }
                )
    return subjects


def _loaded_review_context(
    summary_path: Path, internal: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, ScanRecord | None, AdjudicationRecord | None]:
    session = review_session_reference(internal)
    if isinstance(session, Mapping):
        if Path(str(session.get("summary", ""))).resolve() != summary_path:
            raise ValidationToolError("review decisions belong to another summary")
        return session, None, None
    scan = cast(ScanRecord, internal["scan"])
    adjudication = cast(AdjudicationRecord, internal["adjudication"])
    scanned_summary = Path(scan["project_root"]) / scan["summary"]
    if scanned_summary.resolve() != summary_path:
        raise ValidationToolError("review decisions belong to another summary")
    return None, scan, adjudication


def _finish_review_acceptance(
    summary_path: Path,
    accepted: Mapping[str, Any],
    progress: ValidationProgress,
) -> dict[str, Any]:
    """Apply one complete review session and publish its canonical result."""

    scan = cast(ScanRecord, accepted["scan"])
    adjudication = cast(AdjudicationRecord, accepted["adjudication"])
    scanned_summary = Path(scan["project_root"]) / scan["summary"]
    if scanned_summary.resolve() != summary_path:
        raise ValidationToolError("review session belongs to another summary")
    decisions = cast(dict[str, Any], accepted["decisions"])
    action_internal = {
        "scan": scan,
        "adjudication": adjudication,
        "orphan_fingerprints": accepted["orphan_fingerprints"],
    }
    actions = decisions_to_actions(decisions, action_internal)
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    review_judgments = durable_review_judgments(
        decisions, adjudication["date"], scan, adjudication
    )
    _merge_review_judgments(progress.record, review_judgments)
    if decided["review_queue"]:
        progress.record["judgments"] = load_judgments_for_subjects(
            summary_path.with_suffix(""),
            progress.record,
            reusable_review_subjects(
                scan, cast(AdjudicationRecord, decided)
            ),
        )
        _merge_review_judgments(progress.record, review_judgments)
        decided = cast(
            dict[str, Any],
            _apply_reusable_judgments(
                scan,
                cast(AdjudicationRecord, decided),
                progress.record["judgments"],
            ),
        )
    if decided["review_queue"]:
        result = _review_required(
            scan,
            cast(AdjudicationRecord, decided),
            progress,
            _requested_context_levels(decisions),
        )
        finish_review_session(Path(str(accepted["session_dir"])))
        result.update(
            {
                "summary": summary_path.as_posix(),
                "state_status": progress.state_status,
            }
        )
        return result
    project_root = infer_project_root(summary_path)
    summary = _relative_summary(summary_path, project_root)
    target_record, assembly, cleanup = _complete_adjudication(
        CompletionRequest(
            summary,
            summary_path.with_suffix(""),
            scan,
            cast(AdjudicationRecord, decided),
            progress.record,
            review_judgments,
            progress.publish,
        )
    )
    finish_review_session(Path(str(accepted["session_dir"])))
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (
            summary_path.with_suffix("") / RECORD_FILENAME
        ).as_posix(),
        "cache": (summary_path.with_suffix("") / CACHE_FILENAME).as_posix(),
        "report": (summary_path.with_suffix("") / "validation.md").as_posix(),
        "progress_retained": _record_has_progress(target_record),
        "published": progress.publish,
        "state_status": progress.state_status,
        "counts": assembly.counts(),
        "cleanup": cleanup,
    }


def _requested_context_levels(
    decisions: Mapping[str, Any],
) -> dict[str, int]:
    levels: dict[str, int] = {}
    for row in decisions.get("items", []):
        if row.get("decision") != "needs_context":
            continue
        current = int(row.get("context_level", 0))
        if current >= 1:
            raise ValidationToolError(
                "review context is already at its terminal bounded level"
            )
        levels[context_request_key(row)] = current + 1
    return levels


def _ensure_current_review_rules(scan: ScanRecord | None) -> None:
    if scan is not None and scan.get("validation_rules_version") != RULES_VERSION:
        raise ValidationToolError("review decisions use superseded validation rules")


def _continue_review(
    summary_path: Path, decision_file: Path, publish: bool
) -> dict[str, Any]:
    decisions, internal = load_decisions(decision_file)
    session, scan, adjudication = _loaded_review_context(summary_path, internal)
    _ensure_current_review_rules(scan)
    output_dir = summary_path.with_suffix("")
    project_root = infer_project_root(summary_path)
    summary = _relative_summary(summary_path, project_root)
    record, cache, state_status = _load_target_state(output_dir, summary)
    continuation = record.get("continuation") or {}
    expected_continuation = (
        session.get("session_identity")
        if isinstance(session, Mapping)
        else decisions.get("continuation")
    )
    durable_identity = (
        continuation.get("session_identity")
        if continuation.get("kind") == "paged"
        else continuation.get("identity")
    )
    if durable_identity != expected_continuation:
        raise ValidationToolError("review decisions are stale for the durable record")
    action_internal = internal
    if isinstance(session, Mapping):
        def publish_batch(
            accepted_decisions: Mapping[str, Any],
            base: Mapping[str, Any],
        ) -> None:
            nonlocal record
            adjudication_date = str(base["adjudication"]["date"])
            batch = durable_review_judgments(
                accepted_decisions,
                adjudication_date,
                cast(ScanRecord, base["scan"]),
                cast(AdjudicationRecord, base["adjudication"]),
            )
            record = append_judgment_batch(output_dir, record, batch)

        accepted = accept_review_page(
            decisions, internal, publish_batch
        )
        if accepted["status"] == "review_required":
            accepted.update(
                {
                    "summary": summary_path.as_posix(),
                    "state_status": state_status,
                    "progress_retained": _record_has_progress(record),
                }
            )
            return accepted
        return _finish_review_acceptance(
            summary_path,
            accepted,
            ValidationProgress(record, cache, state_status, publish),
        )
    record = hydrate_record_shell(record, output_dir, preserve_manifest=True)
    assert scan is not None
    assert adjudication is not None
    actions = decisions_to_actions(decisions, action_internal)
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    review_judgments = durable_review_judgments(
        decisions, adjudication["date"], scan, adjudication
    )
    _merge_review_judgments(record, review_judgments)
    if decided["review_queue"]:
        decided = cast(
            dict[str, Any],
            _apply_reusable_judgments(
                scan,
                cast(AdjudicationRecord, decided),
                record["judgments"],
            ),
        )
    if decided["review_queue"]:
        context_levels = _requested_context_levels(decisions)
        result = _review_required(
            scan,
            cast(AdjudicationRecord, decided),
            ValidationProgress(record, cache, state_status, publish),
            context_levels,
        )
        result.update(
            {"summary": summary_path.as_posix(), "state_status": state_status}
        )
        finish_legacy_ordinary_session(internal)
        return result
    target_record, assembly, cleanup = _complete_adjudication(
        CompletionRequest(
            summary,
            output_dir,
            scan,
            cast(AdjudicationRecord, decided),
            record,
            review_judgments,
            publish,
        )
    )
    finish_legacy_ordinary_session(internal)
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (output_dir / RECORD_FILENAME).as_posix(),
        "cache": (output_dir / CACHE_FILENAME).as_posix(),
        "report": (output_dir / "validation.md").as_posix(),
        "progress_retained": _record_has_progress(target_record),
        "published": publish,
        "state_status": state_status,
        "counts": assembly.counts(),
        "cleanup": cleanup,
    }


def _resume_active_review(context: LoadedValidation) -> dict[str, Any] | None:
    continuation = context.record.get("continuation")
    if not isinstance(continuation, Mapping):
        return None
    if continuation.get("kind") == "ordinary":
        resumed = resume_legacy_ordinary_exchange(
            context.output_dir,
            context.record_summary,
            continuation,
            RULES_VERSION,
        )
        if resumed["status"] == "superseded_rules":
            context.record["continuation"] = None
            return None
    elif continuation.get("kind") == "paged":
        resumed = resume_review_session(
            context.output_dir, continuation
        )
        if resumed["status"] == "ready":
            return _finish_review_acceptance(
                context.summary_path, resumed, context.progress()
            )
        recovery = empty_review_session_refresh_context(
            context.output_dir, continuation
        )
        if (
            recovery is not None
            and recovery.get("context_projection_version")
            != CONTEXT_PROJECTION_VERSION
        ):
            refreshed = _refresh_empty_context_session(context, recovery)
            if refreshed is not None:
                return refreshed
    else:
        raise ValidationToolError("durable continuation kind is unsupported")
    resumed.update(
        {
            "summary": context.summary,
            "state_status": context.state_status,
            "progress_retained": _record_has_progress(context.record),
        }
    )
    return resumed


def _refresh_empty_context_session(
    context: LoadedValidation, recovery: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Replace an untouched session after a context-projection upgrade."""

    if not context.request.publish:
        return None
    progress = ValidationProgress(
        copy.deepcopy(context.record),
        copy.deepcopy(context.cache),
        context.state_status,
        False,
    )
    result = _review_required(
        cast(ScanRecord, recovery["scan"]),
        cast(AdjudicationRecord, recovery["adjudication"]),
        progress,
        cast(Mapping[str, int], recovery["context_levels"]),
    )
    old_session = Path(str(recovery["session_dir"]))
    if result.get("session_identity") == old_session.name:
        return None
    write_record_and_cache(context.output_dir, progress.record, progress.cache)
    finish_review_session(old_session)
    result.update(
        {
            "summary": context.summary,
            "state_status": context.state_status,
        }
    )
    return result


def _apply_reusable_judgments(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    judgments: list[dict[str, Any]],
) -> AdjudicationRecord:
    if not adjudication["review_queue"] or not judgments:
        return adjudication
    actions = reusable_review_actions(scan, adjudication, judgments)
    if not actions["actions"]:
        return adjudication
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    return cast(AdjudicationRecord, decided)


def _run_loaded_validation(context: LoadedValidation) -> dict[str, Any]:
    request = context.request
    if request.decision_file is not None:
        return _continue_review(
            context.summary_path,
            request.decision_file.resolve(),
            request.publish,
        )
    resumed = _resume_active_review(context)
    if resumed is not None:
        return resumed
    context.record = hydrate_record_rows(
        context.record, context.output_dir, ("outcomes", "failures")
    )
    cached = (
        _cached_completion(
            request,
            context.output_dir,
            context.record,
            context.cache,
            context.state_status.rsplit(":", 1)[-1],
        )
        if request.mode == "standard"
        and context.state_status.startswith("native-v2:")
        else None
    )
    if cached is not None:
        cached["state_status"] = context.state_status
        return cached
    scan, metrics = scan_log(
        ScanRequest(
            context.summary_path,
            request.jobs,
            context.record,
            context.cache,
            RULES_VERSION,
            request.mode,
            scan_policy(),
            context.project_root,
        )
    )
    adjudication = prepare_adjudication_record(
        scan,
        request.result_date or date.today().isoformat(),
        request.mode,
    )
    subjects = reusable_review_subjects(scan, adjudication)
    context.record["judgments"] = load_judgments_for_subjects(
        context.output_dir, context.record, subjects
    )
    adjudication = _apply_reusable_judgments(
        scan, adjudication, context.record["judgments"]
    )
    if adjudication["review_queue"]:
        result = _review_required(scan, adjudication, context.progress())
        result.update(
            {"summary": context.summary, "state_status": context.state_status}
        )
        return result
    target_record, assembly, cleanup = _complete_adjudication(
        CompletionRequest(
            context.record_summary,
            context.output_dir,
            scan,
            adjudication,
            context.record,
            [],
            request.publish,
        )
    )
    return {
        "status": "complete",
        "summary": context.summary,
        "record": (context.output_dir / RECORD_FILENAME).as_posix(),
        "cache": (context.output_dir / CACHE_FILENAME).as_posix(),
        "report": (context.output_dir / "validation.md").as_posix(),
        "progress_retained": _record_has_progress(target_record),
        "published": request.publish,
        "state_status": context.state_status,
        "diagnostics": _diagnostics(metrics),
        "counts": assembly.counts(),
        "cleanup": cleanup,
    }


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Validate one maintained summary through the public target operation."""

    summary_path = request.summary_path.resolve()
    output_dir = summary_path.with_suffix("")
    project_root = infer_project_root(summary_path)
    record_summary = _relative_summary(summary_path, project_root)
    report_path = output_dir / "validation.md"
    prior_report = report_path.read_bytes() if report_path.is_file() else None
    try:
        record, cache, state_status = _load_target_state(
            output_dir, record_summary
        )
        return _run_loaded_validation(
            LoadedValidation(
                request,
                summary_path,
                output_dir,
                project_root,
                record_summary,
                record,
                cache,
                state_status,
            )
        )
    except Exception as exc:
        report_retained = (
            prior_report is not None
            and report_path.is_file()
            and report_path.read_bytes() == prior_report
        )
        return {
            "status": "error",
            "summary": summary_path.as_posix(),
            "error": str(exc),
            "progress_retained": (output_dir / RECORD_FILENAME).is_file(),
            "prior_report_retained": report_retained,
        }
