"""CLI-owned progressive validation controller for one maintained summary."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, cast

from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import apply_review_decisions
from .observations import (
    METADATA_UNCHANGED,
    ObservationSession,
    observe_outcome_dependencies,
    outcomes_are_compatible,
)
from .render import assemble_records
from .review_exchange import (
    accept_deferred_orphan_page,
    create_exchange,
    decisions_to_actions,
    durable_review_judgments,
    finish_deferred_orphan_session,
    load_decisions,
    reusable_review_actions,
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
    assert_no_retired_artifacts,
    empty_cache,
    empty_record,
    load_cache,
    load_record,
    publish_target_bundle,
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


def _project_root(summary_path: Path) -> Path:
    """Infer the on-disk project root without consulting source control."""

    for parent in summary_path.parents:
        if parent.name == "docs":
            return parent.parent
    return summary_path.parent


def _load_target_state(
    output_dir: Path, summary: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    assert_no_retired_artifacts(output_dir)
    record_path = output_dir / RECORD_FILENAME
    cache_path = output_dir / CACHE_FILENAME
    if record_path.is_file():
        record = load_record(record_path)
        cache, cache_status = load_cache(cache_path)
        return record, cache, f"native:{cache_status}"
    return empty_record(summary, RULES_VERSION), empty_cache(), "new"


def _cached_completion(
    summary_path: Path,
    output_dir: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
    publish: bool,
) -> dict[str, Any] | None:
    outcomes = list(record.get("outcomes", []))
    if record.get("validation_rules_version") != RULES_VERSION:
        return None
    if record.get("result") is None:
        return None
    if not (output_dir / "validation.md").is_file():
        return None
    session = ObservationSession()
    project_root = _project_root(summary_path)
    if outcomes:
        observed = observe_outcome_dependencies(
            session,
            outcomes,
            cache.get("files", {}),
            project_root,
        )
        if not outcomes_are_compatible(outcomes, observed):
            return None
    else:
        for logical_path, identity in cache.get("files", {}).items():
            observation = session.observe(project_root / logical_path, identity)
            if observation.status != METADATA_UNCHANGED:
                return None
    recovered_continuation = record.get("continuation") is not None
    if recovered_continuation and publish:
        completed_record = copy.deepcopy(dict(record))
        completed_record["continuation"] = None
        write_record_and_cache(output_dir, completed_record, cache)
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (output_dir / RECORD_FILENAME).as_posix(),
        "cache": (output_dir / CACHE_FILENAME).as_posix(),
        "report": (output_dir / "validation.md").as_posix(),
        "progress_retained": True,
        "cached": True,
        "recovered_continuation": recovered_continuation,
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


def _review_required(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    progress: ValidationProgress,
) -> dict[str, Any]:
    result = create_exchange(
        scan,
        adjudication,
        {
            "summary": scan["summary"],
            "record": progress.record,
            "cache": progress.cache,
            "state_status": progress.state_status,
            "publish": progress.publish,
        },
    )
    progress.record["continuation"] = {
        "identity": result.get("session_identity", result["continuation"]),
        "item_count": result["item_count"],
    }
    if progress.publish:
        output_dir = Path(scan["project_root"]) / scan["log_root"]
        write_record_and_cache(output_dir, progress.record, progress.cache)
    result["progress_retained"] = bool(
        progress.record["outcomes"] or progress.record["judgments"]
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


def _continue_review(
    summary_path: Path, decision_file: Path, publish: bool
) -> dict[str, Any]:
    decisions, internal = load_decisions(decision_file)
    deferred, scan, adjudication = _loaded_review_context(summary_path, internal)
    output_dir = summary_path.with_suffix("")
    record, cache, state_status = _load_target_state(
        output_dir, summary_path.as_posix()
    )
    continuation = record.get("continuation") or {}
    expected_continuation = (
        deferred.get("session_identity")
        if isinstance(deferred, Mapping)
        else decisions.get("continuation")
    )
    if continuation.get("identity") != expected_continuation:
        raise ValidationToolError("review decisions are stale for the durable record")
    session_dir: Path | None = None
    action_internal = internal
    if isinstance(deferred, Mapping):
        accepted = accept_deferred_orphan_page(decisions, internal)
        if accepted["status"] == "review_required":
            accepted.update(
                {
                    "summary": summary_path.as_posix(),
                    "state_status": state_status,
                    "progress_retained": bool(
                        record["outcomes"] or record["judgments"]
                    ),
                }
            )
            return accepted
        scan = cast(ScanRecord, accepted["scan"])
        adjudication = cast(AdjudicationRecord, accepted["adjudication"])
        scanned_summary = Path(scan["project_root"]) / scan["summary"]
        if scanned_summary.resolve() != summary_path:
            raise ValidationToolError("review session belongs to another summary")
        decisions = cast(dict[str, Any], accepted["decisions"])
        action_internal = {
            "orphan_fingerprints": accepted["orphan_fingerprints"]
        }
        session_dir = Path(str(accepted["session_dir"]))
    assert scan is not None
    assert adjudication is not None
    actions = decisions_to_actions(decisions, action_internal)
    decided, _ = apply_review_decisions(scan, adjudication, actions)
    review_judgments = durable_review_judgments(decisions, adjudication["date"])
    _merge_review_judgments(record, review_judgments)
    if decided["review_queue"]:
        result = _review_required(
            scan,
            cast(AdjudicationRecord, decided),
            ValidationProgress(record, cache, state_status, publish),
        )
        result.update(
            {"summary": summary_path.as_posix(), "state_status": state_status}
        )
        if session_dir is not None:
            finish_deferred_orphan_session(session_dir)
        return result
    target_record, assembly = _complete_adjudication(
        CompletionRequest(
            summary_path.as_posix(),
            output_dir,
            scan,
            cast(AdjudicationRecord, decided),
            record,
            review_judgments,
            publish,
        )
    )
    if session_dir is not None:
        finish_deferred_orphan_session(session_dir)
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (output_dir / RECORD_FILENAME).as_posix(),
        "cache": (output_dir / CACHE_FILENAME).as_posix(),
        "report": (output_dir / "validation.md").as_posix(),
        "progress_retained": bool(target_record["outcomes"]),
        "published": publish,
        "state_status": state_status,
        "counts": assembly.counts(),
    }


def validate(request: ValidationRequest) -> dict[str, Any]:
    """Validate one maintained summary through the public target operation."""

    summary_path = request.summary_path.resolve()
    output_dir = summary_path.with_suffix("")
    summary = summary_path.as_posix()
    report_path = output_dir / "validation.md"
    prior_report = report_path.read_bytes() if report_path.is_file() else None
    try:
        record, cache, state_status = _load_target_state(output_dir, summary)
        if request.decision_file is not None:
            return _continue_review(
                summary_path, request.decision_file.resolve(), request.publish
            )
        cached = (
            _cached_completion(
                summary_path, output_dir, record, cache, request.publish
            )
            if request.mode == "standard" and state_status.startswith("native:")
            else None
        )
        if cached is not None:
            cached["state_status"] = state_status
            return cached
        project_root = _project_root(summary_path)
        scan, metrics = scan_log(
            ScanRequest(
                summary_path,
                request.jobs,
                record,
                cache,
                RULES_VERSION,
                request.mode,
                scan_policy(),
                project_root,
            )
        )
        adjudication = prepare_adjudication_record(
            scan,
            request.result_date or date.today().isoformat(),
            request.mode,
        )
        if adjudication["review_queue"] and record["judgments"]:
            actions = reusable_review_actions(
                scan, adjudication, record["judgments"]
            )
            if actions["actions"]:
                decided, _ = apply_review_decisions(
                    scan, adjudication, actions
                )
                adjudication = cast(AdjudicationRecord, decided)
        if adjudication["review_queue"]:
            result = _review_required(
                scan,
                adjudication,
                ValidationProgress(
                    record, cache, state_status, request.publish
                ),
            )
            result.update({"summary": summary, "state_status": state_status})
            return result
        target_record, assembly = _complete_adjudication(
            CompletionRequest(
                summary,
                output_dir,
                scan,
                adjudication,
                record,
                [],
                request.publish,
            )
        )
        return {
            "status": "complete",
            "summary": summary,
            "record": (output_dir / RECORD_FILENAME).as_posix(),
            "cache": (output_dir / CACHE_FILENAME).as_posix(),
            "report": (output_dir / "validation.md").as_posix(),
            "progress_retained": bool(target_record["outcomes"]),
            "published": request.publish,
            "state_status": state_status,
            "diagnostics": _diagnostics(metrics),
            "counts": assembly.counts(),
        }
    except Exception as exc:
        report_retained = (
            prior_report is not None
            and (output_dir / "validation.md").is_file()
            and (output_dir / "validation.md").read_bytes() == prior_report
        )
        return {
            "status": "error",
            "summary": summary,
            "error": str(exc),
            "progress_retained": (output_dir / RECORD_FILENAME).is_file(),
            "prior_report_retained": report_retained,
        }
