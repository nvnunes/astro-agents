"""Command-line argument contract for the research-log validation tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Sequence, cast

from .adjudication import ReviewPacketRequest, make_review_packet
from .contracts import (
    AdjudicationRecord,
    CanonicalRepositoryView,
    LifecycleRecordContractError,
    ScanRecord,
    ValidationMetrics,
    ValidationToolError,
    decode_adjudication_record,
    decode_scan_record,
)
from .decision_store import merge_native_orphan_batch_judgments
from .decisions import apply_review_decisions
from .discovery import MarkdownDiscoveryError
from .graph import GraphContractError
from .graph_store import (
    discover_repository_summaries,
    replacement_repository_view,
    repository_identity_path,
    slice_paths,
    validate_repository_view,
)
from .inventory import find_project_root
from .records import RecordPublicationError, validation_lock
from .runtime import (
    ADJUDICATION_SCHEMA_VERSION,
    MATERIAL_INVENTORY_POLICY,
    RULES_VERSION,
    SCAN_SCHEMA_VERSION,
    lint_records,
    prepare_adjudication_record,
    render_records,
    scan_log,
)
from .scan import local_snapshot_identity


def build_parser() -> argparse.ArgumentParser:
    """Build the stable validation command and argument surface."""

    parser = argparse.ArgumentParser(
        description="Mechanical-first support for agent-led research-log validation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan", help="scan a research log without semantic adjudication"
    )
    scan.add_argument("--summary", required=True, type=Path)
    scan.add_argument("--output", required=True, type=Path)
    scan.add_argument("--metrics", type=Path)
    scan.add_argument("--state", type=Path, help="prior validation-state.json")
    scan.add_argument(
        "--repository-index",
        type=Path,
        help="explicit canonical repository graph view for isolated testing",
    )
    scan.add_argument(
        "--mode", choices=("standard", "reproduction"), default="standard"
    )
    scan.add_argument("--jobs", type=int, default=min(32, (os.cpu_count() or 1) + 4))

    prepare = subparsers.add_parser(
        "prepare",
        help="prepare mechanical results and a bounded semantic-review queue",
    )
    prepare.add_argument("--scan", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--date", required=True)
    prepare.add_argument(
        "--mode", choices=("standard", "reproduction"), default="standard"
    )

    review = subparsers.add_parser(
        "review", help="write compact context for the bounded semantic-review queue"
    )
    review.add_argument("--scan", required=True, type=Path)
    review.add_argument("--adjudication", required=True, type=Path)
    review.add_argument("--output", required=True, type=Path)
    review.add_argument("--metrics", type=Path)
    review.add_argument(
        "--entry", help="include one queue scope, such as e003 or Summary"
    )
    review.add_argument("--target", help="include one exact queued target identity")
    review.add_argument("--kind", help="include one review-queue kind")
    review.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="maximum orphan candidates in one deterministic packet batch",
    )
    review.add_argument(
        "--batch-number",
        type=int,
        help="one-based orphan packet batch across the filtered queue",
    )

    decide = subparsers.add_parser(
        "decide", help="apply compact reviewed decisions to an adjudication"
    )
    decide.add_argument("--scan", required=True, type=Path)
    decide.add_argument("--adjudication", required=True, type=Path)
    decide.add_argument("--decisions", required=True, type=Path)
    decide.add_argument("--output", required=True, type=Path)
    decide.add_argument(
        "--decision-store",
        type=Path,
        help="merge candidate-scoped orphan judgments into the canonical store",
    )

    render = subparsers.add_parser(
        "render", help="render validation records from adjudications"
    )
    render.add_argument("--scan", required=True, type=Path)
    render.add_argument("--adjudication", required=True, type=Path)
    render.add_argument("--output-dir", required=True, type=Path)

    lint = subparsers.add_parser("lint", help="lint rendered validation records")
    lint.add_argument("--output-dir", required=True, type=Path)
    lint.add_argument("--scan", type=Path)
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationToolError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationToolError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    """Atomically write deterministic JSON, preserving an identical file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == payload:
        return
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == value:
        return
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_scan_record(path: Path) -> ScanRecord:
    """Load and validate one current-schema scan record from JSON."""

    try:
        return decode_scan_record(_read_json(path), schema_version=SCAN_SCHEMA_VERSION)
    except LifecycleRecordContractError as exc:
        raise ValidationToolError(f"invalid scan record {path}: {exc}") from exc


def load_adjudication_record(path: Path) -> AdjudicationRecord:
    """Load and validate one current-schema adjudication record from JSON."""

    try:
        return decode_adjudication_record(
            _read_json(path), schema_version=ADJUDICATION_SCHEMA_VERSION
        )
    except LifecycleRecordContractError as exc:
        raise ValidationToolError(f"invalid adjudication record {path}: {exc}") from exc


def repository_view_for_scan(
    args: argparse.Namespace, project_root: Path
) -> tuple[CanonicalRepositoryView, ValidationMetrics]:
    """Return the explicit or canonical replacement view for one scan."""

    if args.repository_index is not None:
        if not args.repository_index.is_file():
            raise ValidationToolError(
                f"explicit repository index does not exist: {args.repository_index}"
            )
        raw = _read_json(args.repository_index)
        try:
            validate_repository_view(raw, RULES_VERSION)
        except GraphContractError as exc:
            raise ValidationToolError(f"repository index is invalid: {exc}") from exc
        return cast(CanonicalRepositoryView, raw), cast(
            ValidationMetrics,
            {"status": "explicit-canonical", "elapsed_seconds": 0.0},
        )
    started = time.monotonic()
    summaries = discover_repository_summaries(project_root)
    view = replacement_repository_view(
        project_root,
        args.summary,
        RULES_VERSION,
        MATERIAL_INVENTORY_POLICY,
        summaries=summaries,
    )
    status = (
        "replacement"
        if view["scope"]["cross_log_complete"]
        else "replacement-cross-log-incomplete"
    )
    return view, cast(
        ValidationMetrics,
        {
            "status": status,
            "logs": len(summaries),
            "inputs": len(list(slice_paths(project_root, summaries))),
            "edges": len(view["graph_edges"]),
            "scripts_parsed": 0,
            "logs_rebuilt": 0,
            "files_hashed": 0,
            "bytes_hashed": 0,
            "elapsed_seconds": time.monotonic() - started,
        },
    )


def _run_scan(args: argparse.Namespace) -> int:
    prior = _read_json(args.state) if args.state else None
    canonical_dir = args.summary.resolve().with_suffix("")
    decision_path = canonical_dir / "validation-decisions.json"
    prior_decisions = _read_json(decision_path) if decision_path.is_file() else None
    canonical_state = canonical_dir / "validation-state.json"
    if args.state and args.state.resolve() == canonical_state:
        canonical_lint = lint_records(canonical_dir)
        if not canonical_lint["ok"] or not canonical_lint.get(
            "cache_usable", False
        ):
            prior = None
    root = find_project_root(args.summary.resolve())
    repository, index_metrics = repository_view_for_scan(args, root)
    scan, metrics = scan_log(
        args.summary,
        jobs=args.jobs,
        prior_state=prior,
        repository_index=repository,
        mode=args.mode,
        prior_decisions=prior_decisions,
    )
    metrics["repository_index_status"] = index_metrics["status"]
    metrics["repository_index_elapsed_seconds"] = round(
        index_metrics["elapsed_seconds"], 6
    )
    write_json(args.output, scan)
    if args.metrics:
        write_json(args.metrics, metrics)
    print(json.dumps(metrics, sort_keys=True))
    return 0


def _run_prepare(args: argparse.Namespace) -> int:
    template = prepare_adjudication_record(
        load_scan_record(args.scan), args.date, args.mode
    )
    write_json(args.output, template)
    rows = [row for entry in template["entries"] for row in entry["targets"]]
    result = {
        "entry_rows": len(rows),
        "summary_rows": len(template["summary"]),
        "mechanical_integrity_results": sum(
            row["integrity"] is not None for row in rows
        ),
        "mechanical_provenance_results": sum(
            row["provenance"] is not None for row in rows
        ),
        "review_queue": len(template["review_queue"]),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def _run_review(args: argparse.Namespace) -> int:
    metrics: dict[str, Any] = {}
    packet, counts = make_review_packet(
        load_scan_record(args.scan),
        load_adjudication_record(args.adjudication),
        ReviewPacketRequest(
            entry=args.entry,
            target=args.target,
            kind=args.kind,
            batch_size=args.batch_size,
            batch_number=args.batch_number,
        ),
        metrics=metrics,
    )
    _write_text(args.output, packet)
    metrics.update({"review_queue": sum(counts.values()), "kinds": counts})
    if args.metrics:
        write_json(args.metrics, metrics)
    print(json.dumps(metrics, sort_keys=True))
    return 0


def _run_decide(args: argparse.Namespace) -> int:
    scan = load_scan_record(args.scan)
    adjudication = load_adjudication_record(args.adjudication)
    decisions = _read_json(args.decisions)
    updated, counts = apply_review_decisions(
        scan,
        adjudication,
        decisions,
    )
    if args.decision_store is None:
        write_json(args.output, updated)
    else:
        canonical_dir = repository_identity_path(
            scan["log_root"], Path(scan["project_root"])
        )
        canonical_store = canonical_dir / "validation-decisions.json"
        if args.decision_store.resolve() != canonical_store:
            raise ValidationToolError(
                "--decision-store must name the scanned log's canonical store"
            )
        with validation_lock(canonical_dir):
            prior_store = (
                _read_json(canonical_store) if canonical_store.is_file() else None
            )
            store, store_counts = merge_native_orphan_batch_judgments(
                prior_store,
                decisions.get("actions", []),
                validation_rules_version=scan["validation_rules_version"],
                local_snapshot_identity=local_snapshot_identity(scan),
                decision_date=adjudication["date"],
            )
            write_json(canonical_store, store)
            write_json(args.output, updated)
        counts.update(store_counts)
    print(json.dumps(counts, sort_keys=True))
    return 0


def _run_render(args: argparse.Namespace) -> int:
    scan = load_scan_record(args.scan)
    root = Path(scan["project_root"])
    canonical = repository_identity_path(scan["log_root"], root)
    if args.output_dir.resolve() != canonical:
        raise ValidationToolError(
            "canonical rendering requires the scanned log's validation directory"
        )
    if scan["repository_scope"]["kind"] != "replacement":
        raise ValidationToolError(
            "canonical rendering requires a repository replacement view"
        )
    counts = render_records(
        load_adjudication_record(args.adjudication), scan, args.output_dir
    )
    print(json.dumps(counts, sort_keys=True))
    return 0


def _run_lint(args: argparse.Namespace) -> int:
    scan = load_scan_record(args.scan) if args.scan else None
    expected = scan["entry_order"] if scan else None
    result = lint_records(
        args.output_dir,
        expected,
        local_snapshot_identity(scan) if scan is not None else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


COMMAND_HANDLERS = {
    "scan": _run_scan,
    "prepare": _run_prepare,
    "review": _run_review,
    "decide": _run_decide,
    "render": _run_render,
    "lint": _run_lint,
}

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run validation command orchestration and return an exit status."""

    args = build_parser().parse_args(argv)
    try:
        return COMMAND_HANDLERS[args.command](args)
    except (
        OSError,
        MarkdownDiscoveryError,
        ValidationToolError,
        GraphContractError,
        RecordPublicationError,
    ) as exc:
        print(f"research_log_validation: {exc}", file=sys.stderr)
        return 2
