"""Lazy command dispatcher for the public research-log management entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .context import resolve_entry, resolve_log, resolve_log_creation
from .model import (
    ActionError,
    ActionResult,
    AddArguments,
    DataAddArguments,
    DataUpdateArguments,
    EvidenceCommonArguments,
    InitArguments,
    RetentionArguments,
)

FAMILIES = ("add", "data", "discover", "evidence", "init", "retention", "validate")
AUTHORING_FAMILIES = frozenset({"add", "data", "evidence", "init", "retention"})


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
        if family in {"data", "evidence", "retention"} and arguments
        else family
    )
    try:
        if family == "discover":
            return _dispatch_discover(arguments)
        if family == "validate":
            return _dispatch_validate(arguments)
        dispatch = {
            "add": _dispatch_add,
            "data": _dispatch_data,
            "evidence": _dispatch_evidence,
            "init": _dispatch_init,
            "retention": _dispatch_retention,
        }
        result = dispatch[family](arguments)
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
    if family in AUTHORING_FAMILIES:
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
    parser.add_argument(
        "--path",
        type=Path,
        help="logical log base whose summary is PATH.md (never the summary file)",
    )
    parser.add_argument("--entry", required=True, help="stable entry ID")


def _mutation_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check the complete action without writing",
    )


def _dispatch_init(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log init")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--title", required=True)
    _mutation_argument(parser)
    args = parser.parse_args(arguments)
    from . import scaffold

    return scaffold.initialize(
        resolve_log_creation(args.path),
        InitArguments(title=args.title, dry_run=args.dry_run),
    )


def _dispatch_add(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log add")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--slug", required=True)
    _mutation_argument(parser)
    args = parser.parse_args(arguments)
    from . import scaffold

    return scaffold.add_entry(
        resolve_log(args.path),
        AddArguments(
            date=args.date,
            title=args.title,
            slug=args.slug,
            dry_run=args.dry_run,
        ),
    )


def _dispatch_evidence(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log evidence")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add", "update"):
        verb = "Add" if name == "add" else "Replace"
        action = actions.add_parser(
            name,
            help=f"{verb} one evidence record after authoring its marker",
            description=(
                f"{verb} one fully checked evidence record. Author the unique "
                "presentation marker before invoking this action."
            ),
        )
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("--id", required=True, help="presentation marker ID")
        source_form = action.add_mutually_exclusive_group(required=True)
        source_form.add_argument(
            "--source",
            action="append",
            help="one local data input name or complete <name> token",
        )
        source_form.add_argument(
            "--definition",
            type=Path,
            help="advanced sources/transformation JSON beneath /private/tmp",
        )
        action.add_argument(
            "--select",
            action="append",
            default=[],
            help="JSON Pointer to one selected field or value; repeat as needed",
        )
        action.add_argument(
            "--identity",
            action="append",
            default=[],
            help="JSON Pointer asserting stable record identity; repeat as needed",
        )
        action.add_argument(
            "--where",
            nargs=3,
            action="append",
            default=[],
            metavar=("POINTER", "TYPE", "VALUE"),
            help="require a typed equality match; repeat for conjunction",
        )
        transform = action.add_mutually_exclusive_group()
        transform.add_argument(
            "--as-percentage",
            action="store_true",
            help="present one retained proportion as a percentage",
        )
        transform.add_argument(
            "--scale", help="apply one researcher-authorized numeric scale"
        )
    rename = actions.add_parser(
        "rename", help="Rename one evidence ID after every Markdown edit"
    )
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    remove = actions.add_parser(
        "remove", help="Remove one record after its Markdown references"
    )
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("--id", required=True)
    listed = actions.add_parser("list", help="List bounded evidence semantics")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action in {"add", "update"}:
        if args.definition is not None:
            if (
                args.select
                or args.identity
                or args.where
                or args.as_percentage
                or args.scale is not None
            ):
                raise ActionError(
                    "evidence.definition.arguments_conflict",
                    "--definition cannot be combined with common evidence arguments",
                )
            from . import evidence_definition

            return evidence_definition.add_or_update(
                entry,
                action=args.action,
                record_id=args.id,
                definition=args.definition,
                dry_run=args.dry_run,
            )
        if len(args.source) != 1:
            raise ActionError(
                "evidence.common.unsupported",
                "common evidence accepts exactly one source",
            )
        from . import evidence

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
    from . import evidence

    if args.action == "rename":
        return evidence.rename(entry, args.old_id, args.new_id, dry_run=args.dry_run)
    if args.action == "remove":
        return evidence.remove(entry, args.id, dry_run=args.dry_run)
    return evidence.list_records(entry)


def _dispatch_data(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log data")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add-origin", "add-generated"):
        description = (
            "Register one producerless material input and stop Provenance"
            if name == "add-origin"
            else "Register one current confirmed same-log generated input"
        )
        action = actions.add_parser(name, help=description, description=description)
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("name", help="stable entry-scoped input name")
        action.add_argument(
            "target",
            help="existing absolute or entry-root-relative file or directory",
        )
        if name == "add-origin":
            action.add_argument(
                "--identity",
                action="append",
                help="authoritative directory file or final-component pattern",
            )
    update = actions.add_parser(
        "update", help="Change explicitly selected input properties"
    )
    _entry_arguments(update)
    _mutation_argument(update)
    update.add_argument("name", help="existing input name")
    update.add_argument("--target", help="replacement existing local target")
    classification = update.add_mutually_exclusive_group()
    classification.add_argument(
        "--origin", action="store_true", help="assert an explicit origin boundary"
    )
    classification.add_argument(
        "--generated",
        action="store_true",
        help="require current confirmed same-log production",
    )
    identity = update.add_mutually_exclusive_group()
    identity.add_argument(
        "--identity",
        action="append",
        help="replace an origin directory's authoritative selectors",
    )
    identity.add_argument(
        "--byte-complete",
        action="store_true",
        help="identify an origin directory by all descendant bytes",
    )
    rename = actions.add_parser(
        "rename", help="Rename an input after recorded-command token edits"
    )
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_name")
    rename.add_argument("new_name")
    refresh = actions.add_parser(
        "refresh", help="Record an intentional byte change"
    )
    _entry_arguments(refresh)
    _mutation_argument(refresh)
    refresh.add_argument("name")
    remove = actions.add_parser(
        "remove", help="Remove an input after command and evidence use"
    )
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("name")
    listed = actions.add_parser("list", help="List bounded input semantics")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    from . import data

    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action in {"add-origin", "add-generated"}:
        return data.add(
            entry,
            generated=args.action == "add-generated",
            arguments=DataAddArguments(
                name=args.name,
                target=args.target,
                identity=(
                    tuple(args.identity)
                    if getattr(args, "identity", None) is not None
                    else None
                ),
                dry_run=args.dry_run,
            ),
        )
    if args.action == "update":
        classification_value = (
            "origin" if args.origin else "generated" if args.generated else None
        )
        return data.update(
            entry,
            DataUpdateArguments(
                name=args.name,
                target=args.target,
                classification=classification_value,
                identity=(tuple(args.identity) if args.identity is not None else None),
                byte_complete=args.byte_complete,
                dry_run=args.dry_run,
            ),
        )
    if args.action == "rename":
        return data.rename(
            entry, args.old_name, args.new_name, dry_run=args.dry_run
        )
    if args.action == "refresh":
        return data.refresh(entry, args.name, dry_run=args.dry_run)
    if args.action == "remove":
        return data.remove(entry, args.name, dry_run=args.dry_run)
    return data.list_inputs(entry)


def _dispatch_retention(arguments: Sequence[str]) -> ActionResult:
    parser = argparse.ArgumentParser(prog="log retention")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add", "update"):
        verb = "Add" if name == "add" else "Replace"
        action = actions.add_parser(
            name,
            help=f"{verb} one disconnected-retention decision",
            description=f"{verb} one disconnected-retention decision.",
        )
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("--id", required=True, help="stable retention ID")
        action.add_argument("--reason", help="concise retention intent")
        action.add_argument(
            "targets",
            nargs="+",
            help="one directory or one or more entry-relative regular files",
        )
    rename = actions.add_parser("rename", help="Rename one retention ID")
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    remove = actions.add_parser("remove", help="Remove one retention decision")
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("--id", required=True)
    listed = actions.add_parser("list", help="List bounded retention semantics")
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
