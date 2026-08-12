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

from research_log_summary import SummaryPublicationError

from .adjudication import make_review_packet
from .contracts import (
    AdjudicationRecord,
    CanonicalRepositoryView,
    FileChangedError,
    LifecycleRecordContractError,
    ScanRecord,
    ValidationMetrics,
    ValidationToolError,
    decode_adjudication_record,
    decode_scan_record,
)
from .decisions import apply_review_decisions
from .discovery import MarkdownDiscoveryError
from .graph import GraphContractError
from .graph_store import (
    AGGREGATE_DIRECTORY,
    aggregate_files,
    build_repository_aggregate,
    discover_repository_summaries,
    replacement_repository_view,
    repository_identity_path,
    slice_paths,
    validate_repository_view,
)
from .inventory import find_project_root
from .records import (
    RecordPublicationError,
    publish_record_bundle,
    record_bundle_identity,
    repository_lock,
)
from .runtime import (
    ADJUDICATION_SCHEMA_VERSION,
    MATERIAL_INVENTORY_POLICY,
    RULES_VERSION,
    SCAN_SCHEMA_VERSION,
    lint_records,
    prepare_adjudication_record,
    render_records,
    scan_log,
    update_summary_validation,
)


def _add_index_command(subparsers: argparse._SubParsersAction) -> None:
    """Add the disposable repository-aggregate rebuild command."""

    index = subparsers.add_parser(
        "index", help="build or refresh the repository research-log dependency index"
    )
    index.add_argument("--project-root", required=True, type=Path)
    index.add_argument("--output", type=Path)
    index.add_argument("--metrics", type=Path)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable validation command and argument surface."""

    parser = argparse.ArgumentParser(
        description="Mechanical-first support for agent-led research-log validation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_index_command(subparsers)

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
    review.add_argument(
        "--entry", help="include one queue scope, such as e003 or Summary"
    )
    review.add_argument("--target", help="include one exact queued target identity")
    review.add_argument("--kind", help="include one review-queue kind")

    decide = subparsers.add_parser(
        "decide", help="apply compact reviewed decisions to an adjudication"
    )
    decide.add_argument("--scan", required=True, type=Path)
    decide.add_argument("--adjudication", required=True, type=Path)
    decide.add_argument("--decisions", required=True, type=Path)
    decide.add_argument("--output", required=True, type=Path)

    render = subparsers.add_parser(
        "render", help="render validation records from adjudications"
    )
    render.add_argument("--scan", required=True, type=Path)
    render.add_argument("--adjudication", required=True, type=Path)
    render.add_argument("--output-dir", required=True, type=Path)

    update_summary = subparsers.add_parser(
        "update-summary",
        help="project canonical validation results into the maintained summary",
    )
    update_summary.add_argument("--summary", required=True, type=Path)
    update_summary.add_argument("--output-dir", required=True, type=Path)

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


def run_index_command(args: argparse.Namespace) -> int:
    """Publish a current aggregate built only from canonical per-log slices."""

    project_root = args.project_root.resolve()
    output = args.output or project_root / AGGREGATE_DIRECTORY
    names = ("manifest.json", "incoming.json")
    prior_identity = record_bundle_identity(output, names)
    aggregate, metrics = build_repository_aggregate(project_root, RULES_VERSION)
    manifest, incoming = aggregate_files(aggregate)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=f".{output.name}-staging-"
    ) as directory:
        staged = Path(directory)
        write_json(staged / "manifest.json", manifest)
        write_json(staged / "incoming.json", incoming)

        def validate_slices() -> None:
            current, _ = build_repository_aggregate(project_root, RULES_VERSION)
            if current != aggregate:
                raise FileChangedError(
                    "validation index slices changed during aggregate build"
                )

        publish_record_bundle(
            staged,
            output,
            names,
            expected_identity=prior_identity,
            validate_publication=validate_slices,
        )
    if args.metrics:
        write_json(args.metrics, metrics)
    print(json.dumps(metrics, sort_keys=True))
    return 0


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
    canonical_state = canonical_dir / "validation-state.json"
    if (
        args.state
        and args.state.resolve() == canonical_state
        and not lint_records(canonical_dir)["ok"]
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
    packet, counts = make_review_packet(
        load_scan_record(args.scan),
        load_adjudication_record(args.adjudication),
        entry=args.entry,
        target=args.target,
        kind=args.kind,
    )
    _write_text(args.output, packet)
    print(
        json.dumps(
            {
                "review_queue": sum(counts.values()),
                "kinds": counts,
                "packet_bytes": len(packet.encode("utf-8")),
            },
            sort_keys=True,
        )
    )
    return 0


def _run_decide(args: argparse.Namespace) -> int:
    updated, counts = apply_review_decisions(
        load_scan_record(args.scan),
        load_adjudication_record(args.adjudication),
        _read_json(args.decisions),
    )
    write_json(args.output, updated)
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


def _run_update_summary(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            update_summary_validation(args.summary, args.output_dir), sort_keys=True
        )
    )
    return 0


def _run_lint(args: argparse.Namespace) -> int:
    scan = load_scan_record(args.scan) if args.scan else None
    expected = scan["entry_order"] if scan else None
    result = lint_records(args.output_dir, expected)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


COMMAND_HANDLERS = {
    "index": run_index_command,
    "scan": _run_scan,
    "prepare": _run_prepare,
    "review": _run_review,
    "decide": _run_decide,
    "render": _run_render,
    "update-summary": _run_update_summary,
    "lint": _run_lint,
}

LOCKED_COMMANDS = frozenset({"index", "scan", "render", "update-summary", "lint"})


def _command_project_root(args: argparse.Namespace) -> Path:
    """Return the repository owning one canonical command."""

    if args.command == "index":
        return args.project_root.resolve()
    if args.command in {"scan", "update-summary"}:
        return find_project_root(args.summary.resolve())
    if args.command == "render":
        return Path(load_scan_record(args.scan)["project_root"])
    if args.command == "lint" and args.scan:
        return Path(load_scan_record(args.scan)["project_root"])
    if args.command == "lint":
        return find_project_root(args.output_dir.resolve())
    raise ValidationToolError(f"command has no canonical repository: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run validation command orchestration and return an exit status."""

    args = build_parser().parse_args(argv)
    try:
        if args.command in LOCKED_COMMANDS:
            with repository_lock(_command_project_root(args)):
                return COMMAND_HANDLERS[args.command](args)
        return COMMAND_HANDLERS[args.command](args)
    except (
        OSError,
        MarkdownDiscoveryError,
        ValidationToolError,
        GraphContractError,
        RecordPublicationError,
        SummaryPublicationError,
    ) as exc:
        print(f"research_log_validation: {exc}", file=sys.stderr)
        return 2
