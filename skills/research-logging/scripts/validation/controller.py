"""CLI-owned progressive validation controller for one maintained summary."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, cast

from .contracts import AdjudicationRecord, ScanRecord, ValidationToolError
from .decisions import apply_review_decisions
from .graph_store import (
    replacement_repository_view,
)
from .observations import (
    ObservationSession,
    observe_outcome_dependencies,
    retain_compatible_outcomes,
)
from .render import assemble_records
from .review_exchange import (
    create_exchange,
    decisions_to_actions,
    durable_review_judgments,
    load_decisions,
)
from .runtime import (
    MATERIAL_INVENTORY_POLICY,
    RULES_VERSION,
    prepare_adjudication_record,
    render_policy,
    scan_policy,
)
from .scan import ScanRequest, scan_log
from .target_records import (
    CACHE_FILENAME,
    RECORD_FILENAME,
    TargetRecordError,
    empty_cache,
    empty_record,
    import_v45,
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


def _project_root(summary_path: Path) -> Path:
    """Infer the on-disk project root without consulting source control."""

    for parent in summary_path.parents:
        if parent.name == "docs":
            return parent.parent
    return summary_path.parent


def _load_target_state(
    output_dir: Path, summary: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    record_path = output_dir / RECORD_FILENAME
    cache_path = output_dir / CACHE_FILENAME
    if record_path.is_file():
        record = load_record(record_path)
        cache, cache_status = load_cache(cache_path)
        return record, cache, f"native:{cache_status}"
    v45_names = (
        "validation-decisions.json",
        "validation-state.json",
        "validation-index.json",
    )
    if any((output_dir / name).exists() for name in v45_names):
        if not all((output_dir / name).is_file() for name in v45_names):
            raise TargetRecordError(
                "v45 migration requires validation-decisions.json, "
                "validation-state.json, and validation-index.json together"
            )
        record, cache = import_v45(output_dir, summary)
        return record, cache, "v45-imported"
    return empty_record(summary, RULES_VERSION), empty_cache(), "new"


def _cached_completion(
    summary_path: Path,
    output_dir: Path,
    record: Mapping[str, Any],
    cache: Mapping[str, Any],
) -> dict[str, Any] | None:
    outcomes = list(record.get("outcomes", []))
    if not outcomes or record.get("result") is None:
        return None
    if record.get("continuation") is not None or not (
        output_dir / "validation.md"
    ).is_file():
        return None
    session = ObservationSession()
    observed = observe_outcome_dependencies(
        session,
        outcomes,
        cache.get("files", {}),
        _project_root(summary_path),
    )
    retained, reopened = retain_compatible_outcomes(outcomes, observed)
    if reopened or len(retained) != len(outcomes):
        return None
    return {
        "status": "complete",
        "summary": summary_path.as_posix(),
        "record": (output_dir / RECORD_FILENAME).as_posix(),
        "cache": (output_dir / CACHE_FILENAME).as_posix(),
        "report": (output_dir / "validation.md").as_posix(),
        "progress_retained": True,
        "cached": True,
        "diagnostics": session.diagnostics.as_dict(),
    }


def _target_record(
    summary: str, bundle: Any, prior: Mapping[str, Any]
) -> dict[str, Any]:
    state = bundle.state
    record = empty_record(summary, state["validation_rules_version"])
    record["rule_dependencies"] = {
        "components": copy.deepcopy(state["component_versions"]),
        "input_projections": copy.deepcopy(state["input_projection_versions"]),
    }
    by_identity = {
        judgment["identity"]: copy.deepcopy(judgment)
        for judgment in [
            *prior.get("judgments", []),
            *bundle.decisions.get("judgments", []),
        ]
    }
    record["judgments"] = list(by_identity.values())
    record["outcomes"] = []
    for stored in state["completed_checks"]:
        outcome = copy.deepcopy(stored)
        outcome.pop("graph_slice", None)
        record["outcomes"].append(outcome)
    record["result"] = copy.deepcopy(state["result"])
    record["failures"] = copy.deepcopy(state["result"].get("failures", []))
    return record


def _target_cache(bundle: Any, scan: ScanRecord) -> dict[str, Any]:
    state = bundle.state
    files = copy.deepcopy(state.get("input_files", {}))
    for path, identity in state.get("files", {}).items():
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
    cache["directories"] = copy.deepcopy(state.get("directory_memberships", {}))
    cache["inspections"] = copy.deepcopy(state.get("mechanical_checks", {}))
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
        "identity": result["continuation"],
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
    bundle = assembly.bundle()
    target_record = _target_record(request.summary, bundle, request.prior_record)
    _merge_review_judgments(target_record, request.review_judgments)
    target_record["continuation"] = None
    target_cache = _target_cache(bundle, request.scan)
    if request.publish:
        publish_target_bundle(
            request.output_dir, bundle.report_text, target_record, target_cache
        )
    return target_record, assembly


def _continue_review(
    summary_path: Path, decision_file: Path, publish: bool
) -> dict[str, Any]:
    decisions, internal = load_decisions(decision_file)
    scan = cast(ScanRecord, internal["scan"])
    adjudication = cast(AdjudicationRecord, internal["adjudication"])
    scanned_summary = Path(scan["project_root"]) / scan["summary"]
    if scanned_summary.resolve() != summary_path:
        raise ValidationToolError("review decisions belong to another summary")
    output_dir = summary_path.with_suffix("")
    record, cache, state_status = _load_target_state(
        output_dir, summary_path.as_posix()
    )
    continuation = record.get("continuation") or {}
    if continuation.get("identity") != decisions.get("continuation"):
        raise ValidationToolError("review decisions are stale for the durable record")
    actions = decisions_to_actions(decisions, internal)
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


def validate(
    summary_path: Path,
    decision_file: Path | None = None,
    result_date: str | None = None,
    jobs: int = 8,
    publish: bool = True,
) -> dict[str, Any]:
    """Validate one maintained summary through the public target operation."""

    summary_path = summary_path.resolve()
    output_dir = summary_path.with_suffix("")
    summary = summary_path.as_posix()
    report_path = output_dir / "validation.md"
    prior_report = report_path.read_bytes() if report_path.is_file() else None
    try:
        record, cache, state_status = _load_target_state(output_dir, summary)
        if decision_file is not None:
            return _continue_review(summary_path, decision_file.resolve(), publish)
        cached = _cached_completion(summary_path, output_dir, record, cache)
        if cached is not None:
            cached["state_status"] = state_status
            return cached
        project_root = _project_root(summary_path)
        repository = replacement_repository_view(
            project_root,
            summary_path,
            RULES_VERSION,
            MATERIAL_INVENTORY_POLICY,
            summaries=[summary_path],
        )
        prior_state_path = output_dir / "validation-state.json"
        prior_state = (
            json.loads(prior_state_path.read_text(encoding="utf-8"))
            if prior_state_path.is_file()
            else None
        )
        decisions_path = output_dir / "validation-decisions.json"
        prior_decisions = (
            json.loads(decisions_path.read_text(encoding="utf-8"))
            if decisions_path.is_file()
            else None
        )
        scan, metrics = scan_log(
            ScanRequest(
                summary_path,
                jobs,
                prior_state,
                repository,
                RULES_VERSION,
                "standard",
                scan_policy(),
                prior_decisions,
                project_root,
            )
        )
        adjudication = prepare_adjudication_record(
            scan, result_date or date.today().isoformat()
        )
        if adjudication["review_queue"]:
            result = _review_required(
                scan,
                adjudication,
                ValidationProgress(record, cache, state_status, publish),
            )
            result.update({"summary": summary, "state_status": state_status})
            return result
        target_record, assembly = _complete_adjudication(
            CompletionRequest(
                summary, output_dir, scan, adjudication, record, [], publish
            )
        )
        return {
            "status": "complete",
            "summary": summary,
            "record": (output_dir / RECORD_FILENAME).as_posix(),
            "cache": (output_dir / CACHE_FILENAME).as_posix(),
            "report": (output_dir / "validation.md").as_posix(),
            "progress_retained": bool(target_record["outcomes"]),
            "published": publish,
            "state_status": state_status,
            "diagnostics": {
                "metadata_checked": metrics.get("files_identified", 0),
                "hashes_reused": metrics.get("files_reused", 0),
                "files_hashed": metrics.get("files_hashed", 0),
                "bytes_hashed": metrics.get("bytes_hashed", 0),
                "content_changed": 0,
            },
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
