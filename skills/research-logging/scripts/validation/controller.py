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
from .observations import (
    ObservationSession,
    observe_outcome_dependencies,
    outcomes_are_compatible,
)
from .render import assemble_records
from .review_exchange import (
    accept_deferred_orphan_page,
    context_request_key,
    create_exchange,
    decisions_to_actions,
    durable_review_judgments,
    empty_deferred_recovery_context,
    finish_deferred_orphan_session,
    finish_review_session,
    load_decisions,
    resume_deferred_orphan_session,
    resume_ordinary_exchange,
    reusable_review_actions,
    reusable_review_subjects,
)
from .runtime import (
    RULES_VERSION,
    prepare_adjudication_record,
    render_policy,
    scan_policy,
)
from .scan import ScanRequest, scan_log
from .sharded_state import prepare_state
from .target_records import (
    CACHE_FILENAME,
    RECORD_FILENAME,
    append_judgment_batch,
    assert_no_retired_artifacts,
    empty_cache,
    empty_record,
    hydrate_record_rows,
    hydrate_record_shell,
    is_sharded_shell,
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
    migrate_storage: bool = False


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

    def ensure_rows(self) -> None:
        """Hydrate sharded histories only after continuation-first handling."""

        if is_sharded_shell(self.record):
            self.record = hydrate_record_shell(self.record, self.output_dir)


def _record_has_progress(record: Mapping[str, Any]) -> bool:
    return bool(
        record_row_count(record, "outcomes")
        or record_row_count(record, "judgments")
    )


def _project_root(summary_path: Path) -> Path:
    """Infer the on-disk project root without consulting source control."""

    for parent in summary_path.parents:
        if parent.name == "docs":
            return parent.parent
    return summary_path.parent


def _relative_summary(summary_path: Path, project_root: Path) -> str:
    try:
        return summary_path.relative_to(project_root).as_posix()
    except ValueError as exc:
        raise ValidationToolError(
            "maintained summary is outside its inferred project root"
        ) from exc


def _load_target_state(
    output_dir: Path, summary: str, project_root: Path
) -> tuple[dict[str, Any], dict[str, Any], str]:
    assert_no_retired_artifacts(output_dir)
    record_path = output_dir / RECORD_FILENAME
    cache_path = output_dir / CACHE_FILENAME
    if record_path.is_file():
        record, source_version = load_record_header_with_source(
            record_path,
            expected_summary=summary,
            project_root=project_root,
        )
        cache, cache_status = load_cache(cache_path)
        return record, cache, f"native-v{source_version}:{cache_status}"
    return empty_record(summary, RULES_VERSION), empty_cache(), "new"


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
    project_root = _project_root(summary_path)
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
    summary_path: Path,
    output_dir: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
    cache_status: str,
) -> dict[str, Any] | None:
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
    if is_sharded_shell(prior):
        record["_sharded_manifest"] = copy.deepcopy(
            prior["_sharded_manifest"]
        )
        record["_state_loaded"] = False
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
    if "session_identity" in result:
        progress.record["continuation"] = {
            "kind": "paged",
            "session": result["session"],
            "session_identity": result["session_identity"],
            "review_kind": result["review_kind"],
        }
    else:
        progress.record["continuation"] = {
            "kind": "ordinary",
            "identity": result["continuation"],
            "item_count": result["item_count"],
        }
    if progress.publish:
        output_dir = Path(scan["project_root"]) / scan["log_root"]
        write_record_and_cache(output_dir, progress.record, progress.cache)
    result["progress_retained"] = bool(
        _record_has_progress(progress.record)
    )
    return result


def _complete_adjudication(
    request: CompletionRequest,
) -> tuple[dict[str, Any], Any]:
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
    if request.publish:
        publish_target_bundle(
            request.output_dir,
            assembly.report_text,
            target_record,
            target_cache,
        )
    return target_record, assembly


def _loaded_review_context(
    summary_path: Path, internal: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, ScanRecord | None, AdjudicationRecord | None]:
    deferred = internal.get("deferred_orphan")
    if isinstance(deferred, Mapping):
        if Path(str(deferred.get("summary", ""))).resolve() != summary_path:
            raise ValidationToolError("review decisions belong to another summary")
        return deferred, None, None
    scan = cast(ScanRecord, internal["scan"])
    adjudication = cast(AdjudicationRecord, internal["adjudication"])
    scanned_summary = Path(scan["project_root"]) / scan["summary"]
    if scanned_summary.resolve() != summary_path:
        raise ValidationToolError("review decisions belong to another summary")
    return None, scan, adjudication


def _finish_deferred_acceptance(
    summary_path: Path,
    accepted: Mapping[str, Any],
    progress: ValidationProgress,
) -> dict[str, Any]:
    """Apply one complete paged session and publish its canonical result."""

    scan = cast(ScanRecord, accepted["scan"])
    adjudication = _normalize_migration_session_dependencies(
        scan, cast(AdjudicationRecord, accepted["adjudication"])
    )
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
    try:
        decided, _ = apply_review_decisions(scan, adjudication, actions)
    except ValidationToolError:
        recovered = _migration_recovery_decisions(adjudication, decisions)
        if recovered is None:
            raise
        recovery_actions = decisions_to_actions(recovered, action_internal)
        decided, _ = apply_review_decisions(
            scan, adjudication, recovery_actions
        )
        decisions = recovered
    review_judgments = durable_review_judgments(
        decisions, adjudication["date"], scan, adjudication
    )
    _merge_review_judgments(progress.record, review_judgments)
    if decided["review_queue"]:
        decided = cast(
            dict[str, Any],
            _apply_reusable_judgments(
                scan,
                _normalize_migration_review_kinds(
                    cast(AdjudicationRecord, decided)
                ),
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
        finish_deferred_orphan_session(Path(str(accepted["session_dir"])))
        result.update(
            {
                "summary": summary_path.as_posix(),
                "state_status": progress.state_status,
            }
        )
        return result
    project_root = _project_root(summary_path)
    summary = _relative_summary(summary_path, project_root)
    target_record, assembly = _complete_adjudication(
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
    finish_deferred_orphan_session(Path(str(accepted["session_dir"])))
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
    }


def _normalize_migration_session_dependencies(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> AdjudicationRecord:
    """Project redundant native-v1 dependency identities into schema v8.

    Some Phase 8 sessions retained an adjudication snapshot after dependency
    identity ownership moved into the scan record.  Keep the referenced session
    immutable and normalize its snapshot in memory only when every old identity
    is still represented exactly by that same snapshot.  Normal publication
    currentness checks continue to verify the scanned paths before publication.
    """

    normalized = copy.deepcopy(adjudication)
    files = scan.get("files", {})
    directories = scan.get("directory_memberships", {})
    rows = [
        *normalized.get("summary", []),
        *(
            target
            for entry in normalized.get("entries", [])
            for target in entry.get("targets", [])
        ),
    ]
    projected = False
    for row in rows:
        for dependency in row.get("dependencies", []):
            identity = dependency.get("identity")
            if identity is None:
                continue
            path = dependency.get("path")
            file_compatible = (
                isinstance(path, str) and files.get(path) == identity
            )
            members = dependency.get("members")
            collection_compatible = (
                isinstance(path, str)
                and path in directories
                and isinstance(identity, Mapping)
                and isinstance(identity.get("members"), list)
                and members == identity["members"]
            )
            if not (file_compatible or collection_compatible):
                raise ValidationToolError(
                    "legacy review session dependency identity is incompatible "
                    f"with its scan snapshot: {path}"
                )
            del dependency["identity"]
            projected = True
    if not projected:
        return adjudication
    return normalized


def _migration_recovery_decisions(
    adjudication: AdjudicationRecord, decisions: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Omit pre-adapter bare passes that the current producer contract rejects.

    This controlled restart exists only while the eleven Phase 8 records are
    migrating.  It preserves every other accepted row and lets the normal
    durable-continuation path reissue the unprojectable questions.
    """

    resolved = {
        (entry.get("id"), row.get("target"))
        for entry in adjudication.get("entries", [])
        for row in entry.get("targets", [])
        if isinstance(row.get("producer_invocation"), str)
    }
    rejected = {
        (
            item.get("kind"),
            item.get("entry"),
            item.get("identity"),
        )
        for item in adjudication.get("review_queue", [])
        if item.get("workflow", {}).get("status") == "unresolved"
        and (item.get("entry"), item.get("identity")) not in resolved
    }
    rows = decisions.get("items")
    if not isinstance(rows, list):
        return None
    retained = [
        row
        for row in rows
        if not (
            isinstance(row, Mapping)
            and row.get("decision") == "pass"
            and (
                row.get("kind"),
                row.get("entry"),
                row.get("identity"),
            )
            in rejected
        )
    ]
    if len(retained) == len(rows):
        return None
    return {"schema_version": decisions.get("schema_version"), "items": retained}


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


def _continue_review(
    summary_path: Path, decision_file: Path, publish: bool
) -> dict[str, Any]:
    decisions, internal = load_decisions(decision_file)
    deferred, scan, adjudication = _loaded_review_context(summary_path, internal)
    output_dir = summary_path.with_suffix("")
    project_root = _project_root(summary_path)
    summary = _relative_summary(summary_path, project_root)
    record, cache, state_status = _load_target_state(
        output_dir, summary, project_root
    )
    continuation = record.get("continuation") or {}
    expected_continuation = (
        deferred.get("session_identity")
        if isinstance(deferred, Mapping)
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
    if isinstance(deferred, Mapping):
        def publish_batch(
            accepted_decisions: Mapping[str, Any],
            base: Mapping[str, Any],
        ) -> None:
            nonlocal record
            if not is_sharded_shell(record):
                return
            adjudication_date = str(base["adjudication"]["date"])
            batch = durable_review_judgments(
                accepted_decisions,
                adjudication_date,
                cast(ScanRecord, base["scan"]),
                cast(AdjudicationRecord, base["adjudication"]),
            )
            record = append_judgment_batch(output_dir, record, batch)

        accepted = accept_deferred_orphan_page(
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
        if is_sharded_shell(record):
            record = hydrate_record_shell(record, output_dir)
        return _finish_deferred_acceptance(
            summary_path,
            accepted,
            ValidationProgress(record, cache, state_status, publish),
        )
    if is_sharded_shell(record):
        record = hydrate_record_shell(record, output_dir)
    assert scan is not None
    assert adjudication is not None
    actions = decisions_to_actions(decisions, action_internal)
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    review_judgments = durable_review_judgments(
        decisions, adjudication["date"], scan, adjudication
    )
    _merge_review_judgments(record, review_judgments)
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
        finish_review_session(internal)
        return result
    target_record, assembly = _complete_adjudication(
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
    finish_review_session(internal)
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
    }


def _resume_active_review(context: LoadedValidation) -> dict[str, Any] | None:
    continuation = context.record.get("continuation")
    if not isinstance(continuation, Mapping):
        return None
    if continuation.get("kind") == "ordinary":
        resumed = resume_ordinary_exchange(
            context.project_root, context.record_summary, continuation
        )
    elif continuation.get("kind") == "paged":
        resumed = resume_deferred_orphan_session(
            context.project_root, continuation
        )
        if resumed["status"] == "ready":
            return _finish_deferred_acceptance(
                context.summary_path, resumed, context.progress()
            )
        if not is_sharded_shell(context.record):
            recovery = empty_deferred_recovery_context(
                context.project_root, continuation
            )
            restarted = _restart_empty_migration_session(context, recovery)
            if restarted is not None:
                return restarted
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


def _normalize_migration_review_kinds(
    adjudication: AdjudicationRecord,
) -> AdjudicationRecord:
    normalized = copy.deepcopy(adjudication)
    for item in normalized.get("review_queue", []):
        if (
            item.get("kind") == "mechanical_failure"
            and not item.get("hard_failures")
            and item.get("producer_candidates")
        ):
            item["kind"] = "semantic_fallback"
    return normalized


def _restart_empty_migration_session(
    context: LoadedValidation, recovery: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Replace an untouched pre-adapter session through the durable boundary."""

    if recovery is None or not context.request.publish:
        return None
    scan = cast(ScanRecord, recovery["scan"])
    original = cast(AdjudicationRecord, recovery["adjudication"])
    normalized = _normalize_migration_review_kinds(original)
    decided = _apply_reusable_judgments(
        scan, normalized, context.record["judgments"]
    )
    if decided == original:
        return None
    old_session = Path(str(recovery["session_dir"]))
    if decided["review_queue"]:
        result = _review_required(
            scan, decided, context.progress()
        )
        finish_deferred_orphan_session(old_session)
        result.update(
            {
                "summary": context.summary,
                "state_status": context.state_status,
            }
        )
        return result
    target_record, assembly = _complete_adjudication(
        CompletionRequest(
            context.record_summary,
            context.output_dir,
            scan,
            decided,
            context.record,
            [],
            True,
        )
    )
    finish_deferred_orphan_session(old_session)
    return {
        "status": "complete",
        "summary": context.summary,
        "record": (context.output_dir / RECORD_FILENAME).as_posix(),
        "cache": (context.output_dir / CACHE_FILENAME).as_posix(),
        "report": (context.output_dir / "validation.md").as_posix(),
        "progress_retained": _record_has_progress(target_record),
        "published": True,
        "state_status": context.state_status,
        "counts": assembly.counts(),
    }


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


def _migrate_storage(context: LoadedValidation) -> dict[str, Any]:
    """Project exact compatible state into shards without semantic work."""

    if is_sharded_shell(context.record):
        manifest = context.record["_sharded_manifest"]
        return {
            "status": "already_sharded",
            "summary": context.summary,
            "published": False,
            "state_status": context.state_status,
            "row_counts": copy.deepcopy(manifest["row_counts"]),
            "shard_counts": {
                kind: len(refs)
                for kind, refs in manifest["shards"].items()
            },
            "continuation_preserved": context.record.get("continuation")
            is not None,
        }
    prepared = prepare_state(context.record)
    result = {
        "status": "migrated" if context.request.publish else "migration_dry_run",
        "summary": context.summary,
        "published": context.request.publish,
        "state_status": context.state_status,
        "row_counts": copy.deepcopy(prepared.manifest["row_counts"]),
        "shard_counts": {
            kind: len(refs)
            for kind, refs in prepared.manifest["shards"].items()
        },
        "subject_count": prepared.manifest["subject_index"]["subject_count"],
        "continuation_preserved": context.record.get("continuation") is not None,
    }
    if context.request.publish:
        write_record_and_cache(context.output_dir, context.record, context.cache)
    return result


def _run_loaded_validation(context: LoadedValidation) -> dict[str, Any]:
    request = context.request
    if request.migrate_storage:
        return _migrate_storage(context)
    if request.decision_file is not None:
        return _continue_review(
            context.summary_path,
            request.decision_file.resolve(),
            request.publish,
        )
    resumed = _resume_active_review(context)
    if resumed is not None:
        return resumed
    sharded = is_sharded_shell(context.record)
    if sharded:
        context.record = hydrate_record_rows(
            context.record, context.output_dir, ("outcomes", "failures")
        )
    else:
        context.ensure_rows()
    cached = (
        _cached_completion(
            context.summary_path,
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
    if sharded:
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
    target_record, assembly = _complete_adjudication(
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
    }


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Validate one maintained summary through the public target operation."""

    summary_path = request.summary_path.resolve()
    output_dir = summary_path.with_suffix("")
    project_root = _project_root(summary_path)
    record_summary = _relative_summary(summary_path, project_root)
    report_path = output_dir / "validation.md"
    prior_report = report_path.read_bytes() if report_path.is_file() else None
    try:
        record, cache, state_status = _load_target_state(
            output_dir, record_summary, project_root
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
