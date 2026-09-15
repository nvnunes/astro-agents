"""Public parsing and presentation for validation operations."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Callable, Mapping, NoReturn, Sequence, cast

from research_log_result_store import ResultStoreError
from validation.discovery import discover_summaries
from validation.operation_state import operation_lock
from validation.read_model import (
    batch_detail,
    finding_detail,
    list_batches,
    list_blocked,
    list_failed,
    list_findings,
    show_validation,
    show_validation_root,
)
from validation.summary import human_saved_at as _human_saved_at
from validation.summary import render_saved_summary

from .context import LogContext, resolve_entry, resolve_log
from .model import ActionError
from .validation_run import ValidationOptions, run_validation

FORMATS = ("text", "json")
FINDING_TYPES = ("conformance", "evidence", "provenance", "orphan")
FINDING_SECTIONS = ("repair_keys", "nodes", "relationships", "ambiguities")
BATCH_SECTIONS = (
    "findings",
    "repair_keys",
    "nodes",
    "relationships",
    "ambiguities",
)
MAX_ERROR_MESSAGE_BYTES = 2_048


class _ValidationParser(argparse.ArgumentParser):
    """Preserve validation-specific JSON errors for argument failures."""

    def error(self, message: str) -> NoReturn:
        raise ActionError("validation.arguments.invalid", message)


def run_validate_cli(arguments: Sequence[str]) -> int:
    """Dispatch validation and retain validation-specific JSON errors."""

    try:
        return _run_validate_cli(arguments)
    except (ActionError, ResultStoreError) as error:
        if not _json_requested(arguments):
            raise
        print(
            json.dumps(
                {
                    "error": {
                        "code": str(getattr(error, "code", "validation.failed")),
                        "message": _bounded_error_message(error),
                    },
                    "schema": _error_schema(arguments),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


def _run_validate_cli(arguments: Sequence[str]) -> int:
    """Dispatch the closed ``log validate`` grammar."""

    parser = _ValidationParser(prog="log validate")
    actions = parser.add_subparsers(dest="action", required=True)
    _run_parser(actions)
    _show_parser(actions)
    _list_parser(actions)
    _detail_parser(actions)
    render = actions.add_parser(
        "render", help="Regenerate validation.md from the saved snapshot"
    )
    render.add_argument("--path", required=True, type=Path)
    args = parser.parse_args(arguments)

    if args.action == "run":
        return _dispatch_run(parser, args)
    if args.action == "show":
        return _dispatch_show(args)
    if args.action == "render":
        render_validation(resolve_log(args.path))
        return 0
    return _dispatch_saved_query(args)


def _dispatch_run(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> int:
    """Run one parsed validation evaluation command."""

    _require_run_selection(parser, args)
    value, status = run_validation(
        path=args.path,
        root=args.root,
        entry=args.entry,
        options=ValidationOptions(
            dry_run=args.dry_run,
            recompute_validation=(args.recompute or args.recompute_validation),
            recompute_fingerprints=(args.recompute or args.recompute_fingerprints),
        ),
    )
    _print(value, args.format, _render_run)
    return status


def _dispatch_show(args: argparse.Namespace) -> int:
    """Render one parsed saved-summary command."""

    value = _show(args)
    renderer = _render_root_show if args.root is not None else _render_show
    _print(value, args.format, renderer)
    rows = cast(Sequence[Mapping[str, object]], value["rows"])
    return 2 if any(row.get("outcome") == "Error" for row in rows) else 0


def _dispatch_saved_query(args: argparse.Namespace) -> int:
    """Run one parsed list or detail command."""

    log = resolve_log(args.path)
    if args.entry is not None:
        resolve_entry(log, args.entry)
    if args.action == "list":
        if args.entity == "findings":
            value = list_findings(
                log.root,
                entry=args.entry,
                finding_type=getattr(args, "finding_type", None),
                limit=args.limit,
                cursor=args.cursor,
            )
        elif args.entity == "batches":
            value = list_batches(
                log.root,
                entry=args.entry,
                limit=args.limit,
                cursor=args.cursor,
            )
        elif args.entity == "blocked":
            value = list_blocked(
                log.root,
                entry=args.entry,
                limit=args.limit,
                cursor=args.cursor,
            )
        else:
            value = list_failed(
                log.root,
                entry=args.entry,
                limit=args.limit,
                cursor=args.cursor,
            )
        _print(value, args.format, _render_list)
        if args.format == "text" and value.get("next_cursor"):
            print("Next: " + _next_page_command(args, str(value["next_cursor"])))
        return 0
    value = (
        finding_detail(
            log.root,
            args.id,
            entry=args.entry,
            section=args.section,
            limit=args.limit,
            cursor=args.cursor,
        )
        if args.entity == "finding"
        else batch_detail(
            log.root,
            args.id,
            entry=args.entry,
            section=args.section,
            limit=args.limit,
            cursor=args.cursor,
        )
    )
    _print(value, args.format, _render_detail)
    if args.format == "text" and value.get("next_cursor"):
        print("Next: " + _next_page_command(args, str(value["next_cursor"])))
    return 0


def _run_parser(actions: argparse._SubParsersAction[_ValidationParser]) -> None:
    run = actions.add_parser("run", help="Evaluate and optionally save validation")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path", type=Path)
    selection.add_argument("--root", type=Path)
    run.add_argument("--entry")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--recompute", action="store_true")
    run.add_argument("--recompute-validation", action="store_true")
    run.add_argument("--recompute-fingerprints", action="store_true")
    run.add_argument("--format", choices=FORMATS, default="text")


def _show_parser(actions: argparse._SubParsersAction[_ValidationParser]) -> None:
    show = actions.add_parser("show", help="Show saved full-log validation summaries")
    selection = show.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path", type=Path)
    selection.add_argument("--root", type=Path)
    show.add_argument("--format", choices=FORMATS, default="text")


def _list_parser(actions: argparse._SubParsersAction[_ValidationParser]) -> None:
    listing = actions.add_parser("list", help="List saved validation conclusions")
    entities = listing.add_subparsers(dest="entity", required=True)
    findings = entities.add_parser("findings", help="List atomic findings")
    _page_arguments(findings)
    findings.add_argument("--type", dest="finding_type", choices=FINDING_TYPES)
    batches = entities.add_parser("batches", help="List repair batches")
    _page_arguments(batches)
    blocked = entities.add_parser(
        "blocked", help="List checks blocked by a finding or failed check"
    )
    _page_arguments(blocked)
    failed = entities.add_parser(
        "failed", help="List checks the validator could not complete"
    )
    _page_arguments(failed)


def _detail_parser(
    actions: argparse._SubParsersAction[_ValidationParser],
) -> None:
    detail = actions.add_parser("detail", help="Inspect one finding or batch")
    entities = detail.add_subparsers(dest="entity", required=True)
    finding = entities.add_parser("finding", help="Inspect one atomic finding")
    _detail_arguments(finding, FINDING_SECTIONS)
    batch = entities.add_parser("batch", help="Inspect one repair batch")
    _detail_arguments(batch, BATCH_SECTIONS)


def _page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--entry")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--cursor")
    parser.add_argument("--format", choices=FORMATS, default="text")


def _detail_arguments(parser: argparse.ArgumentParser, sections: Sequence[str]) -> None:
    _page_arguments(parser)
    parser.add_argument("--id", required=True)
    parser.add_argument("--section", choices=sections)


def _require_run_selection(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.root is not None and args.entry is not None:
        parser.error("--entry requires --path")


def _show(args: argparse.Namespace) -> dict[str, object]:
    if args.path is not None:
        return show_validation(resolve_log(args.path).root)
    try:
        discovered = discover_summaries(args.root)
    except ValueError as error:
        raise ActionError("discovery.failed", str(error)) from error
    roots = [
        Path(value).with_suffix("")
        for value in cast(Sequence[str], discovered["summaries"])
    ]
    return show_validation_root(roots)


def render_validation(log: LogContext) -> None:
    """Regenerate ``validation.md`` without observing research-owned inputs."""

    from research_log_paths import VALIDATION_REPORT
    from research_log_result_store import (
        record_report_materialization,
        result_generation,
        results_lock,
    )
    from validation.records import publish_validation_outputs_locked
    from validation.snapshot_report import compose_snapshot_report
    from validation.snapshot_storage import load_validation_snapshot

    with operation_lock(log.root, "log.lock", mode="exclusive"):
        with results_lock(log.root):
            snapshot = load_validation_snapshot(log.root)
            generation = result_generation(log.root, "validation")
            identity = f"validation generation {generation}"
            try:
                report = compose_snapshot_report(snapshot)
            except Exception as error:
                raise ActionError(
                    "validation.report.render_failed", f"{identity}: {error}"
                ) from error
            try:
                publish_validation_outputs_locked(
                    log.root, {VALIDATION_REPORT: report.encode()}
                )
                record_report_materialization(
                    log.root,
                    "validation",
                    report.encode(),
                    expected_generation=generation,
                )
            except Exception as error:
                raise ActionError(
                    "validation.report.write_failed", f"{identity}: {error}"
                ) from error


def _print(
    value: dict[str, object],
    output_format: str,
    renderer: Callable[[Mapping[str, object]], str],
) -> None:
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(renderer(value))


def _render_run(value: Mapping[str, object]) -> str:
    if value["schema"] == "research-log-validation-root-run/1":
        rows = cast(Sequence[Mapping[str, object]], value["rows"])
        lines = [_root_show_table(value["rows"])]
        lines.append(
            "Totals: "
            f"{value['clear_count']} clear; {value['findings_count']} findings; "
            f"{value['failed_count']} failed; {value['error_count']} errors"
        )
        blocked, failed, contributing = _root_run_check_totals(rows)
        suffix = (
            f" (partial; {contributing} of {len(rows)} logs)"
            if contributing != len(rows)
            else ""
        )
        lines.extend((f"Blocked: {blocked}{suffix}", f"Failed: {failed}{suffix}"))
        next_rows = [
            row
            for row in rows
            if row.get("outcome") in {"findings", "failed"}
            and row.get("next_command")
        ]
        if next_rows:
            lines.append("Next:")
            lines.extend(
                f"{Path(str(row['log'])).name}: {row['next_command']}"
                for row in next_rows
            )
        return "\n".join(lines)
    counts = value["finding_counts_by_type"]
    assert isinstance(counts, Mapping)
    target = value["target"]
    assert isinstance(target, Mapping)
    target_label = (
        f"entry {target['entry']}" if "entry" in target else "full log"
    )
    lines = [
        f"Log: {value['log']}",
        f"Target: {target_label}",
        f"Outcome: {str(value['outcome']).title()}",
        f"Saved: {value.get('saved_at', 'No') if value['saved'] else 'No'}",
        "Findings: "
        + ", ".join(f"{name}={counts[name]}" for name in FINDING_TYPES),
    ]
    if "batch_count" in value:
        lines.append(f"Batches: {value['batch_count']}")
    lines.append(f"Blocked: {value['blocked_check_count']}")
    lines.append(f"Failed: {value['failed_check_count']}")
    if value.get("next_command"):
        lines.append(f"Next: {value['next_command']}")
    return "\n".join(lines)


def _render_show(value: Mapping[str, object]) -> str:
    rows = cast(Sequence[Mapping[str, object]], value["rows"])
    lines = [render_saved_summary(rows[0])]
    if rows[0].get("replacement_required"):
        lines.append(f"Next: {rows[0]['next_command']}")
    if isinstance(rows[0].get("error"), Mapping):
        error = cast(Mapping[str, object], rows[0]["error"])
        lines.append(f"Error: {error['code']}: {error['message']}")
    return "\n".join(lines)


def _render_root_show(value: Mapping[str, object]) -> str:
    lines = [_root_show_table(value["rows"])]
    blocked = value["blocked_check_total"]
    failed = value["failed_check_total"]
    if value["totals_partial"]:
        rows = cast(Sequence[object], value["rows"])
        suffix = (
            f" (partial; {value['contributing_log_count']} of {len(rows)} logs)"
        )
    else:
        suffix = ""
    lines.append(f"Blocked: {blocked}{suffix}")
    lines.append(f"Failed: {failed}{suffix}")
    return "\n".join(lines)


def _root_show_table(rows_value: object) -> str:
    assert isinstance(rows_value, list)
    headers = [
        "Log",
        "Saved",
        "Outcome",
        "Conformance",
        "Evidence",
        "Provenance",
        "Orphans",
        "Batches",
    ]
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    for row in rows_value:
        assert isinstance(row, Mapping)
        counts = row.get("finding_counts_by_type")
        log = str(row["log"])
        cells = [
            Path(log).name,
            _human_saved_at(row.get("saved_at")),
            str(row["outcome"]).title(),
        ]
        cells.extend(
            str(counts[name]) if isinstance(counts, Mapping) else "—"
            for name in FINDING_TYPES
        )
        cells.append(str(row.get("batch_count", "—")))
        lines.append(" | ".join(cells))
        if row.get("replacement_required"):
            lines.append(f"Next: {row['next_command']}")
        if isinstance(row.get("error"), Mapping):
            error = row["error"]
            lines.append(f"Error: {error['code']}: {error['message']}")
    return "\n".join(lines)


def _render_list(value: Mapping[str, object]) -> str:
    items = value["items"]
    assert isinstance(items, list)
    lines = [
        f"Log: {value['log']}",
        f"Saved: {value['saved_at']}",
        f"Total: {value['total']}",
    ]
    if not items:
        return "\n".join((*lines, "No items."))
    for item in items:
        assert isinstance(item, Mapping)
        if "finding_id" in item:
            lines.append(
                f"{item['finding_id']} | {item['type']} | {item['code']} | "
                f"{item.get('entry', '—')} | {item['subject']} | {item['batch_id']}"
            )
        elif "batch_id" in item:
            lines.append(
                f"{item['batch_id']} | findings={item['finding_count']} | "
                f"types={','.join(item['represented_types'])} | "
                f"repair={','.join(item['repair_entries']) or '—'} | "
                f"context={','.join(item['context_entries']) or '—'} | "
                f"rationale={','.join(item['rationale'])}"
            )
        elif "blocked_by" in item:
            lines.append(
                f"{item['check_id']} | {item['area']} | "
                f"{item.get('entry', '—')} | {item['subject']} | "
                f"{item['rule']} | "
                + json.dumps(item["blocked_by"], ensure_ascii=False, sort_keys=True)
            )
        else:
            lines.append(
                f"{item['check_id']} | {item['area']} | {item['code']} | "
                f"{item.get('entry', '—')} | {item['subject']} | "
                f"{item['rule']} | {item['operation']} | "
                + json.dumps(item["reason"], ensure_ascii=False, sort_keys=True)
            )
    return "\n".join(lines)


def _root_run_check_totals(
    rows: Sequence[Mapping[str, object]],
) -> tuple[int, int, int]:
    """Return check totals from completed root-run rows."""

    completed = [row for row in rows if row.get("outcome") != "error"]
    return (
        sum(cast(int, row["blocked_check_count"]) for row in completed),
        sum(cast(int, row["failed_check_count"]) for row in completed),
        len(completed),
    )


def _render_detail(value: Mapping[str, object]) -> str:
    if "finding" in value:
        return _render_finding_detail(value)
    owner = value["batch"]
    lines = [
        f"Log: {value['log']}",
        f"Saved: {value['saved_at']}",
        "Batch: " + json.dumps(owner, ensure_ascii=False, sort_keys=True),
        f"Section: {value['section']} ({value['section_total']})",
    ]
    if "membership_counts" in value:
        lines.append(
            "Membership: "
            + json.dumps(
                value["membership_counts"], ensure_ascii=False, sort_keys=True
            )
        )
    items = cast(Sequence[object], value["items"])
    lines.extend(
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in items
    )
    return "\n".join(lines)


def _render_finding_detail(value: Mapping[str, object]) -> str:
    """Render one saved finding as an immediately actionable diagnosis."""

    finding = cast(Mapping[str, object], value["finding"])
    diagnosis = cast(Mapping[str, object], finding["diagnosis"])
    batch = cast(Mapping[str, object], value["batch"])
    lines = [
        f"Log: {value['log']}",
        f"Saved: {value['saved_at']}",
        f"Finding: {finding['finding_id']}",
        f"Issue: {diagnosis['title']}",
        f"Explanation: {diagnosis['explanation']}",
        f"Type: {finding['type']}",
        f"Code: {finding['code']}",
        f"Rule: {finding['rule']}",
        f"Entry: {finding.get('entry', '—')}",
        f"Subject: {finding['subject']}",
        "Source: " + _render_source_locations(finding["source_locations"]),
        "Caused by: " + _render_identifiers(finding["caused_by"]),
        f"Batch: {batch['batch_id']} ({batch['finding_count']} findings)",
    ]
    observed = cast(Mapping[str, object], finding["observed"])
    if observed:
        lines.append("Diagnostic:")
        for name, item in observed.items():
            label = name.replace("_", " ").title()
            if name == "reason" and isinstance(item, str):
                item = item.replace("_", " ")
            lines.append(f"  {label}: {_render_detail_value(item)}")
    else:
        lines.append("Diagnostic: —")
    lines.append(f"Section: {value['section']} ({value['section_total']})")
    lines.extend(
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in cast(Sequence[object], value["items"])
    )
    return "\n".join(lines)


def _render_source_locations(value: object) -> str:
    locations = cast(Sequence[Mapping[str, object]], value)
    if not locations:
        return "—"
    return ", ".join(
        str(location["path"])
        + ("" if "line" not in location else f":{location['line']}")
        for location in locations
    )


def _render_identifiers(value: object) -> str:
    identities = cast(Sequence[object], value)
    return ", ".join(str(item) for item in identities) if identities else "—"


def _render_detail_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _next_page_command(args: argparse.Namespace, cursor: str) -> str:
    command = ["log", "validate", args.action, args.entity]
    for name in ("path", "entry", "finding_type", "id", "section", "limit"):
        value = getattr(args, name, None)
        if value is None:
            continue
        option = "type" if name == "finding_type" else name.replace("_", "-")
        command.extend((f"--{option}", str(value)))
    command.extend(("--cursor", cursor))
    return shlex.join(command)


def _json_requested(arguments: Sequence[str]) -> bool:
    return any(
        argument == "--format=json"
        or (
            argument == "--format"
            and index + 1 < len(arguments)
            and arguments[index + 1] == "json"
        )
        for index, argument in enumerate(arguments)
    )


def _error_schema(arguments: Sequence[str]) -> str:
    if not arguments:
        return "research-log-validation-run/1"
    action = arguments[0]
    entity = arguments[1] if len(arguments) > 1 else ""
    if action == "run":
        return (
            "research-log-validation-root-run/1"
            if any(
                argument == "--root" or argument.startswith("--root=")
                for argument in arguments
            )
            else "research-log-validation-run/1"
        )
    schemas = {
        ("detail", "batch"): "research-log-validation-batch-detail/1",
        ("detail", "finding"): "research-log-validation-finding-detail/1",
        ("list", "batches"): "research-log-validation-batch-list/1",
        ("list", "blocked"): "research-log-validation-blocked-list/1",
        ("list", "failed"): "research-log-validation-failed-list/1",
        ("list", "findings"): "research-log-validation-finding-list/1",
        ("show", ""): "research-log-validation-show/1",
    }
    return schemas.get((action, entity), "research-log-validation-run/1")


def _bounded_error_message(error: Exception) -> str:
    message = str(error)
    encoded = message.encode("utf-8")
    if len(encoded) <= MAX_ERROR_MESSAGE_BYTES:
        return message
    return encoded[: MAX_ERROR_MESSAGE_BYTES - 3].decode(
        "utf-8", errors="ignore"
    ) + "..."
