"""CLI-owned progressive validation controller for one maintained summary."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, cast

from .activity import (
    ValidationActivityLog,
    log_checkpoint,
    log_operation,
    log_phase,
)
from .adjudication import ORPHAN_TARGET
from .compatibility import COMPONENT_VERSIONS, INPUT_PROJECTION_VERSIONS
from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import apply_review_decisions, canonical_review_decisions
from .diagnostics import ValidationDiagnostics
from .inventory import infer_project_root
from .observations import (
    ObservationSession,
    observe_outcome_dependencies,
    outcomes_are_compatible,
)
from .orphan_rules import inherited_basis
from .render import assemble_records, scan_input_metadata_matches
from .review_exchange import (
    CONTEXT_PROJECTION_VERSION,
    accept_review_page,
    context_request_key,
    create_exchange,
    decisions_to_actions,
    durable_review_judgments,
    finish_legacy_ordinary_session,
    finish_review_session,
    load_decisions,
    resume_legacy_ordinary_exchange,
    resume_review_session,
    reusable_review_actions,
    reusable_review_subjects,
    review_session_reference,
    review_session_refresh_context,
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
    activity: ValidationActivityLog | None = None
    diagnostics: ValidationDiagnostics | None = None


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
    activity: ValidationActivityLog | None = None


@dataclass(frozen=True)
class ValidationRequest:
    """Public target-validation inputs for one maintained summary."""

    summary_path: Path
    decision_file: Path | None = None
    result_date: str | None = None
    jobs: int = 8
    publish: bool = True
    mode: str = "standard"
    activity: ValidationActivityLog | None = None
    review_diagnostics: bool = False


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
    diagnostics: ValidationDiagnostics | None = None
    retired_review_session: Path | None = None

    @property
    def summary(self) -> str:
        return self.summary_path.as_posix()

    def progress(self) -> ValidationProgress:
        return ValidationProgress(
            self.record,
            self.cache,
            self.state_status,
            self.request.publish,
            self.request.activity,
            self.diagnostics,
        )


def _record_has_progress(record: Mapping[str, Any]) -> bool:
    return bool(
        record_row_count(record, "outcomes") or record_row_count(record, "judgments")
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


def _coherent_report_projection(output_dir: Path, record: Mapping[str, Any]) -> bool:
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


def _cached_contract_is_current(record: Mapping[str, Any]) -> bool:
    """Require cached completion to use the current rule registries.

    A mismatch bypasses the cache-only return. The normal scan then applies
    per-outcome compatibility, so only outcomes that declare a changed rule or
    input projection reopen.
    """

    dependencies = record.get("rule_dependencies")
    if not isinstance(dependencies, Mapping):
        return False
    components = dependencies.get("components")
    input_projections = dependencies.get("input_projections")
    return (
        isinstance(components, Mapping)
        and dict(components) == COMPONENT_VERSIONS
        and isinstance(input_projections, Mapping)
        and dict(input_projections) == INPUT_PROJECTION_VERSIONS
    )


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
        and _cached_contract_is_current(record)
        and record.get("result") is not None
        and record.get("continuation") is None
        and _coherent_report_projection(output_dir, record)
    )
    session = (
        _compatible_cached_dependencies(summary_path, record, cache) if ready else None
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


def _merge_outcomes(record: dict[str, Any], outcomes: list[dict[str, Any]]) -> None:
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
        review_diagnostics=progress.diagnostics is not None,
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
    result["progress_retained"] = bool(_record_has_progress(progress.record))
    if progress.diagnostics is not None:
        progress.diagnostics.record_page(result)
    return result


def _record_reuse_pass(
    diagnostics: ValidationDiagnostics | None,
    metrics: Mapping[str, Any],
    items_before: int,
    adjudication: AdjudicationRecord,
    stage: str,
) -> None:
    """Record one reuse pass without expanding controller lifecycle paths."""

    if diagnostics is None:
        return
    diagnostics.record_reuse(
        metrics,
        items_before=items_before,
        items_after=len(adjudication["review_queue"]),
    )
    _record_queue(diagnostics, stage, adjudication["review_queue"])


def _record_queue(
    diagnostics: ValidationDiagnostics | None,
    stage: str,
    items: list[dict[str, Any]],
) -> None:
    """Record a queue snapshot only for an opted-in invocation."""

    if diagnostics is not None:
        diagnostics.record_queue(stage, items)


def _record_page_transition(
    diagnostics: ValidationDiagnostics | None, result: Mapping[str, Any]
) -> None:
    """Record one accepted page and any next packet returned with it."""

    if diagnostics is None:
        return
    diagnostics.record_page_acceptance(result)
    diagnostics.record_page(result)


def _complete_adjudication(
    request: CompletionRequest,
) -> tuple[dict[str, Any], Any, dict[str, int]]:
    activity = request.activity
    log_phase(activity, "complete.assemble-records")
    with log_operation(activity, "assemble-records", subject=request.summary):
        assembly = assemble_records(
            request.adjudication,
            request.scan,
            request.output_dir,
            render_policy(),
        )
    target_record = _target_record(request.summary, assembly, request.prior_record)
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
    target_record["projection"] = projection_for(target_record, assembly.report_text)
    superseded_subjects = _superseded_orphan_subjects(request.adjudication)
    if request.publish:
        log_phase(
            activity,
            "complete.publish-records",
            failures=len(target_record["failures"]),
        )
        with log_operation(
            activity,
            "publish-target-bundle",
            subject=request.output_dir.as_posix(),
        ):
            cleanup = publish_target_bundle(
                request.output_dir,
                assembly.report_text,
                target_record,
                target_cache,
                superseded_subjects,
            )
    else:
        log_phase(activity, "complete.inspect-dry-run-cleanup")
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

    activity = progress.activity
    diagnostics = progress.diagnostics
    scan = cast(ScanRecord, accepted["scan"])
    adjudication = cast(AdjudicationRecord, accepted["adjudication"])
    scanned_summary = Path(scan["project_root"]) / scan["summary"]
    if scanned_summary.resolve() != summary_path:
        raise ValidationToolError("review session belongs to another summary")
    accepted_decisions = cast(Mapping[str, Any], accepted["decisions"])
    decisions = canonical_review_decisions(scan, accepted_decisions)
    action_internal = {
        "scan": scan,
        "adjudication": adjudication,
        "orphan_fingerprints": accepted["orphan_fingerprints"],
    }
    log_phase(
        activity,
        "review.finalize.translate-actions",
        decisions=len(decisions.get("items", [])),
    )
    with log_operation(
        activity,
        "translate-review-actions",
        subject=scan["summary"],
        decisions=len(decisions.get("items", [])),
    ):
        actions = decisions_to_actions(decisions, action_internal)
    log_phase(
        activity,
        "review.finalize.apply-actions",
        actions=len(actions.get("actions", [])),
    )
    with log_operation(
        activity,
        "apply-review-actions",
        subject=scan["summary"],
        actions=len(actions.get("actions", [])),
    ):
        decided, _ = apply_review_decisions(
            scan,
            adjudication,
            actions,
            trusted_orphan_fingerprints=cast(
                Mapping[str, Mapping[str, str]],
                accepted["orphan_fingerprints"],
            ),
        )
    if diagnostics is not None:
        diagnostics.record_queue("residual_adjudication", decided["review_queue"])
    output_dir = summary_path.with_suffix("")
    retained_identities = (
        accepted.get("judgment_identities") if decisions == accepted_decisions else None
    )
    log_phase(activity, "review.finalize.load-accepted-judgments")
    with log_operation(
        activity,
        "load-accepted-judgments",
        subject=scan["summary"],
        expected=(
            len(retained_identities) if isinstance(retained_identities, list) else 0
        ),
    ):
        review_judgments = _accepted_review_judgments(
            output_dir,
            progress.record,
            decisions,
            retained_identities,
        )
        if review_judgments is None:
            review_judgments = durable_review_judgments(
                decisions, adjudication["date"], scan, adjudication
            )
    _merge_review_judgments(progress.record, review_judgments)
    if decided["review_queue"]:
        log_phase(
            activity,
            "review.finalize.resolve-residual-subjects",
            review_items=len(decided["review_queue"]),
        )
        with log_operation(
            activity,
            "resolve-residual-subjects",
            subject=scan["summary"],
        ):
            residual_subjects = reusable_review_subjects(
                scan, cast(AdjudicationRecord, decided)
            )
        log_checkpoint(
            activity,
            "residual-subjects-resolved",
            subjects=len(residual_subjects),
        )
        with log_operation(
            activity,
            "load-residual-judgments",
            subject=scan["summary"],
            subjects=len(residual_subjects),
        ):
            progress.record["judgments"] = load_judgments_for_subjects(
                output_dir,
                progress.record,
                residual_subjects,
            )
        _merge_review_judgments(progress.record, review_judgments)
        log_phase(
            activity,
            "review.finalize.apply-reusable-judgments",
            judgments=len(progress.record["judgments"]),
        )
        with log_operation(
            activity,
            "apply-residual-judgments",
            subject=scan["summary"],
            judgments=len(progress.record["judgments"]),
        ):
            decided = cast(
                dict[str, Any],
                _apply_reusable_with_diagnostics(
                    scan,
                    cast(AdjudicationRecord, decided),
                    progress.record["judgments"],
                    diagnostics,
                    "residual_judgment_reuse",
                ),
            )
    if decided["review_queue"]:
        log_phase(
            activity,
            "review.finalize.create-residual-session",
            review_items=len(decided["review_queue"]),
        )
        with log_operation(
            activity,
            "create-residual-review-session",
            subject=scan["summary"],
        ):
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
            activity,
        )
    )
    if diagnostics is not None:
        diagnostics.record_queue("terminal_completion", [])
    finish_review_session(Path(str(accepted["session_dir"])))
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (summary_path.with_suffix("") / RECORD_FILENAME).as_posix(),
        "cache": (summary_path.with_suffix("") / CACHE_FILENAME).as_posix(),
        "report": (summary_path.with_suffix("") / "validation.md").as_posix(),
        "progress_retained": _record_has_progress(target_record),
        "published": progress.publish,
        "state_status": progress.state_status,
        "counts": assembly.counts(),
        "cleanup": cleanup,
    }


def _accepted_review_judgments(
    output_dir: Path,
    record: Mapping[str, Any],
    decisions: Mapping[str, Any],
    raw_identities: Any,
) -> list[dict[str, Any]] | None:
    """Load canonical accepted shards, or defer for a legacy review session."""

    if not (
        isinstance(raw_identities, list)
        and raw_identities
        and all(isinstance(identity, str) for identity in raw_identities)
        and isinstance(record.get("_sharded_manifest"), Mapping)
    ):
        return None
    subjects = [
        {
            "kind": row.get("kind"),
            "entry": row.get("entry"),
            "identity": row.get("identity"),
            **({"material": row["material"]} if "material" in row else {}),
        }
        for row in decisions.get("items", [])
        if isinstance(row, Mapping)
    ]
    loaded = load_judgments_for_subjects(output_dir, record, subjects)
    by_identity = {str(row.get("identity")): row for row in loaded}
    missing = [identity for identity in raw_identities if identity not in by_identity]
    if missing:
        raise ValidationToolError(
            "accepted review judgment shards are incomplete for this session"
        )
    return [copy.deepcopy(by_identity[identity]) for identity in raw_identities]


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


def _current_review_session(
    context: LoadedValidation, continuation: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Return a metadata-current session or stage it for safe replacement."""

    recovery = review_session_refresh_context(context.output_dir, continuation)
    current = scan_input_metadata_matches(
        cast(ScanRecord, recovery["scan"]),
        render_policy(),
        cast(Mapping[str, Mapping[str, Any]], context.cache.get("files", {})),
    )
    if current:
        return recovery
    context.record["continuation"] = None
    context.retired_review_session = Path(str(recovery["session_dir"]))
    return None


def _continue_review(
    context: LoadedValidation,
    decision_file: Path,
    activity: ValidationActivityLog | None = None,
) -> dict[str, Any] | None:
    summary_path = context.summary_path
    output_dir = context.output_dir
    publish = context.request.publish
    decisions, internal = load_decisions(decision_file)
    session, scan, adjudication = _loaded_review_context(summary_path, internal)
    _ensure_current_review_rules(scan)
    project_root = context.project_root
    summary = _relative_summary(summary_path, project_root)
    record = context.record
    cache = context.cache
    state_status = context.state_status
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
        if _current_review_session(context, continuation) is None:
            return None

        def publish_batch(
            accepted_decisions: Mapping[str, Any],
            base: Mapping[str, Any],
        ) -> list[str]:
            nonlocal record
            adjudication_date = str(base["adjudication"]["date"])
            canonical_decisions = canonical_review_decisions(
                cast(ScanRecord, base["scan"]), accepted_decisions
            )
            batch = durable_review_judgments(
                canonical_decisions,
                adjudication_date,
                cast(ScanRecord, base["scan"]),
                cast(AdjudicationRecord, base["adjudication"]),
            )
            record = append_judgment_batch(output_dir, record, batch)
            return [str(judgment["identity"]) for judgment in batch]

        log_phase(
            activity,
            "review.accept-page",
            decisions=len(decisions.get("items", [])),
        )
        with log_operation(
            activity,
            "accept-review-page",
            subject=summary,
            decisions=len(decisions.get("items", [])),
        ):
            accepted = accept_review_page(
                decisions,
                internal,
                publish_batch,
                review_diagnostics=context.diagnostics is not None,
            )
        _record_page_transition(context.diagnostics, accepted)
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
            ValidationProgress(
                record,
                cache,
                state_status,
                publish,
                activity,
                context.diagnostics,
            ),
        )
    record = hydrate_record_shell(record, output_dir, preserve_manifest=True)
    assert scan is not None
    assert adjudication is not None
    decisions = canonical_review_decisions(scan, decisions)
    actions = decisions_to_actions(decisions, action_internal)
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    _record_queue(
        context.diagnostics,
        "residual_adjudication",
        decided["review_queue"],
    )
    review_judgments = durable_review_judgments(
        decisions, adjudication["date"], scan, adjudication
    )
    _merge_review_judgments(record, review_judgments)
    if decided["review_queue"]:
        decided = cast(
            dict[str, Any],
            _apply_reusable_with_diagnostics(
                scan,
                cast(AdjudicationRecord, decided),
                record["judgments"],
                context.diagnostics,
                "residual_judgment_reuse",
            ),
        )
    if decided["review_queue"]:
        context_levels = _requested_context_levels(decisions)
        result = _review_required(
            scan,
            cast(AdjudicationRecord, decided),
            ValidationProgress(
                record,
                cache,
                state_status,
                publish,
                activity,
                context.diagnostics,
            ),
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
    _record_queue(context.diagnostics, "terminal_completion", [])
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
        recovery = _current_review_session(context, continuation)
        if recovery is None:
            return None
        resumed = resume_review_session(
            context.output_dir,
            continuation,
            review_diagnostics=context.diagnostics is not None,
        )
        if resumed["status"] == "ready":
            return _finish_review_acceptance(
                context.summary_path, resumed, context.progress()
            )
        if (
            not recovery.get("accepted_batches")
            and int(recovery.get("next_offset", -1)) == 0
            and recovery.get("context_projection_version") != CONTEXT_PROJECTION_VERSION
        ):
            refreshed = _refresh_empty_context_session(context, recovery)
            if refreshed is not None:
                return refreshed
    else:
        raise ValidationToolError("durable continuation kind is unsupported")
    if context.diagnostics is not None:
        context.diagnostics.record_page(resumed)
    resumed.update(
        {
            "summary": context.summary,
            "state_status": context.state_status,
            "progress_retained": _record_has_progress(context.record),
        }
    )
    return resumed


def _finish_retired_review_session(context: LoadedValidation) -> None:
    """Delete a stale session only after its replacement state is durable."""

    if context.request.publish and context.retired_review_session is not None:
        finish_review_session(context.retired_review_session)
        context.retired_review_session = None


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
        context.request.activity,
        context.diagnostics,
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
    diagnostics: dict[str, Any] | None = None,
) -> AdjudicationRecord:
    if not adjudication["review_queue"]:
        return adjudication
    if not judgments:
        if diagnostics is not None:
            count = len(adjudication["review_queue"])
            diagnostics["questions_considered"] = count
            diagnostics["misses_by_reason"] = {"subject_not_found": count}
        return adjudication
    actions = reusable_review_actions(scan, adjudication, judgments, diagnostics)
    if not actions["actions"]:
        return adjudication
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    return cast(AdjudicationRecord, decided)


def _apply_reusable_with_diagnostics(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    judgments: list[dict[str, Any]],
    diagnostics: ValidationDiagnostics | None,
    stage: str,
) -> AdjudicationRecord:
    """Apply one reuse pass and retain its noncanonical measurements."""

    items_before = len(adjudication["review_queue"])
    metrics: dict[str, Any] | None = {} if diagnostics is not None else None
    decided = _apply_reusable_judgments(scan, adjudication, judgments, metrics)
    _record_reuse_pass(diagnostics, metrics or {}, items_before, decided, stage)
    return decided


def _run_loaded_validation(context: LoadedValidation) -> dict[str, Any]:
    request = context.request
    activity = request.activity
    if request.decision_file is not None:
        log_phase(
            activity,
            "review.continue",
            decision_file=request.decision_file.as_posix(),
        )
        continued = _continue_review(
            context,
            request.decision_file.resolve(),
            activity,
        )
        if continued is not None:
            return continued
        log_phase(activity, "review.stale-restart")
    else:
        log_phase(activity, "review.resume-check")
        resumed = _resume_active_review(context)
        if resumed is not None:
            log_checkpoint(
                activity,
                "review-resumed",
                status=str(resumed.get("status", "unknown")),
            )
            return resumed
    log_phase(activity, "state.hydrate-outcomes")
    context.record = hydrate_record_rows(
        context.record, context.output_dir, ("outcomes", "failures")
    )
    log_phase(activity, "state.cached-completion-check")
    cached = (
        _cached_completion(
            request,
            context.output_dir,
            context.record,
            context.cache,
            context.state_status.rsplit(":", 1)[-1],
        )
        if request.mode == "standard" and context.state_status.startswith("native-v2:")
        else None
    )
    if cached is not None:
        cached["state_status"] = context.state_status
        if context.diagnostics is not None:
            context.diagnostics.record_queue("terminal_completion", [])
        log_checkpoint(activity, "cached-completion", reused=True)
        return cached
    log_phase(activity, "scan.start", state_status=context.state_status)
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
            activity,
        )
    )
    log_phase(activity, "adjudication.prepare", entries=len(scan["entries"]))
    with log_operation(activity, "prepare-adjudication", subject=context.summary):
        adjudication = prepare_adjudication_record(
            scan,
            request.result_date or date.today().isoformat(),
            request.mode,
        )
    if context.diagnostics is not None:
        context.diagnostics.record_queue(
            "initial_adjudication", adjudication["review_queue"]
        )
    log_phase(
        activity,
        "adjudication.reuse-judgments",
        review_items=len(adjudication["review_queue"]),
    )
    review_items_before = len(adjudication["review_queue"])
    with log_operation(activity, "resolve-reusable-subjects", subject=context.summary):
        subjects = reusable_review_subjects(scan, adjudication)
    log_checkpoint(
        activity,
        "reusable-subjects-resolved",
        subjects=len(subjects),
    )
    with log_operation(activity, "load-reusable-judgments", subject=context.summary):
        context.record["judgments"] = load_judgments_for_subjects(
            context.output_dir, context.record, subjects
        )
    log_checkpoint(
        activity,
        "reusable-judgments-loaded",
        judgments=len(context.record["judgments"]),
    )
    with log_operation(activity, "apply-reusable-judgments", subject=context.summary):
        adjudication = _apply_reusable_with_diagnostics(
            scan,
            adjudication,
            context.record["judgments"],
            context.diagnostics,
            "durable_judgment_reuse",
        )
    log_checkpoint(
        activity,
        "reusable-judgments-applied",
        review_items_before=review_items_before,
        review_items_after=len(adjudication["review_queue"]),
    )
    if adjudication["review_queue"]:
        log_phase(
            activity,
            "review.create-packet",
            review_items=len(adjudication["review_queue"]),
        )
        with log_operation(activity, "create-review-packet", subject=context.summary):
            result = _review_required(scan, adjudication, context.progress())
        _finish_retired_review_session(context)
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
            activity,
        )
    )
    if context.diagnostics is not None:
        context.diagnostics.record_queue("terminal_completion", [])
    _finish_retired_review_session(context)
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

    activity = request.activity
    log_phase(activity, "validation.resolve-input")
    summary_path = request.summary_path.resolve()
    output_dir = summary_path.with_suffix("")
    project_root = infer_project_root(summary_path)
    record_summary = _relative_summary(summary_path, project_root)
    report_path = output_dir / "validation.md"
    diagnostics = ValidationDiagnostics() if request.review_diagnostics else None
    prior_report = report_path.read_bytes() if report_path.is_file() else None
    try:
        log_phase(activity, "validation.load-state", summary=record_summary)
        with log_operation(activity, "load-target-state", subject=record_summary):
            record, cache, state_status = _load_target_state(output_dir, record_summary)
        log_checkpoint(activity, "target-state-loaded", state_status=state_status)
        result = _run_loaded_validation(
            LoadedValidation(
                request,
                summary_path,
                output_dir,
                project_root,
                record_summary,
                record,
                cache,
                state_status,
                diagnostics,
            )
        )
    except Exception as exc:
        log_checkpoint(
            activity,
            "validation-error",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        report_retained = (
            prior_report is not None
            and report_path.is_file()
            and report_path.read_bytes() == prior_report
        )
        result = {
            "status": "error",
            "summary": summary_path.as_posix(),
            "error": str(exc),
            "progress_retained": (output_dir / RECORD_FILENAME).is_file(),
            "prior_report_retained": report_retained,
        }
    if diagnostics is not None:
        result["review_diagnostics"] = diagnostics.as_dict()
    return result
