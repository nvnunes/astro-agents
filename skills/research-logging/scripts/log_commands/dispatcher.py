"""Lazy command dispatcher for the public research-log management entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .context import resolve_entry, resolve_log
from .model import (
    ActionError,
    ActionResult,
    EvidenceCommonArguments,
    RetentionArguments,
)

FAMILIES = ("discover", "evidence", "retention", "validate")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one bounded management task and own process-level reporting."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        _top_parser().print_help()
        return 0 if arguments else 2
    family = arguments.pop(0)
    if family not in FAMILIES:
        _top_parser().error(f"unknown task family: {family}")
    selected_task = (
        f"{family}.{arguments[0]}"
        if family in {"evidence", "retention"} and arguments
        else family
    )
    try:
        if family == "discover":
            return _dispatch_discover(arguments)
        if family == "validate":
            return _dispatch_validate(arguments)
        result = (
            _dispatch_evidence(arguments)
            if family == "evidence"
            else _dispatch_retention(arguments)
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ActionError, OSError, UnicodeError) as error:
        return _report_failure(family, selected_task, error)
    except ValueError as error:
        if not hasattr(error, "code"):
            raise
        return _report_failure(family, selected_task, error)


def _report_failure(family: str, selected_task: str, error: Exception) -> int:
    """Emit one bounded expected operational or contract failure."""

    code = getattr(error, "code", f"{family}.failed")
    print(f"log: {code}: {error}", file=sys.stderr)
    if family in {"evidence", "retention"}:
        print(
            json.dumps(
                ActionResult(selected_task, "failed", str(code), False).as_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 2


def _top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log", description="Manage and validate maintained research logs."
    )
    parser.add_argument("family", nargs="?", choices=FAMILIES)
    return parser


def _entry_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", type=Path)
    parser.add_argument("--entry", required=True)


def _mutation_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")


def _dispatch_evidence(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log evidence")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add", "update"):
        action = actions.add_parser(name)
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("--id", required=True)
        source_form = action.add_mutually_exclusive_group(required=True)
        source_form.add_argument("--source", action="append")
        source_form.add_argument("--definition", type=Path)
        action.add_argument("--select", action="append", default=[])
        action.add_argument("--identity", action="append", default=[])
        action.add_argument(
            "--where",
            nargs=3,
            action="append",
            default=[],
            metavar=("POINTER", "TYPE", "VALUE"),
        )
        transform = action.add_mutually_exclusive_group()
        transform.add_argument("--as-percentage", action="store_true")
        transform.add_argument("--scale")
    rename = actions.add_parser("rename")
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    remove = actions.add_parser("remove")
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("--id", required=True)
    listed = actions.add_parser("list")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    from . import evidence

    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action in {"add", "update"}:
        if args.definition is not None:
            raise ActionError(
                "evidence.definition.unavailable",
                "full evidence definitions are added in the next implementation pass",
            )
        if len(args.source) != 1:
            raise ActionError(
                "evidence.common.unsupported",
                "common evidence accepts exactly one source",
            )
        return evidence.add_or_update_common(
            entry,
            action=args.action,
            arguments=EvidenceCommonArguments(
                record_id=args.id,
                source=args.source[0],
                select=tuple(args.select),
                identity=tuple(args.identity),
                where=tuple(tuple(value) for value in args.where),
                as_percentage=args.as_percentage,
                scale=args.scale,
                dry_run=args.dry_run,
            ),
        )
    if args.action == "rename":
        return evidence.rename(entry, args.old_id, args.new_id, dry_run=args.dry_run)
    if args.action == "remove":
        return evidence.remove(entry, args.id, dry_run=args.dry_run)
    return evidence.list_records(entry)


def _dispatch_retention(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log retention")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add", "update"):
        action = actions.add_parser(name)
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("--id", required=True)
        action.add_argument("--reason")
        action.add_argument("targets", nargs="+")
    rename = actions.add_parser("rename")
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    remove = actions.add_parser("remove")
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("--id", required=True)
    listed = actions.add_parser("list")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    from . import retention

    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action in {"add", "update"}:
        return retention.add_or_update(
            entry,
            action=args.action,
            arguments=RetentionArguments(
                record_id=args.id,
                targets=tuple(args.targets),
                reason=args.reason,
                dry_run=args.dry_run,
            ),
        )
    if args.action == "rename":
        return retention.rename(entry, args.old_id, args.new_id, dry_run=args.dry_run)
    if args.action == "remove":
        return retention.remove(entry, args.id, dry_run=args.dry_run)
    return retention.list_records(entry)


def _dispatch_discover(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log discover")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(arguments)
    from .validation_adapter import run_discover

    return run_discover(args.root)


def _dispatch_validate(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log validate")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--path", type=Path)
    selection.add_argument("--root", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args(arguments)
    from .validation_adapter import run_validate

    return run_validate(
        path=args.path,
        root=args.root,
        result_date=args.date,
        dry_run=args.dry_run,
        recompute=args.recompute,
    )
