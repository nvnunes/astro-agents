"""Lazy command dispatcher for the public research-log management entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, NoReturn, Sequence

if TYPE_CHECKING:
    from .reproduction_saved_run import RunSettings, RunTarget

from research_log_result_store import ResultStoreError

from .context import EntryContext, resolve_entry, resolve_log, resolve_log_creation
from .model import (
    ActionError,
    ActionResult,
    AddArguments,
    CommandSyncArguments,
    DataUpdateArguments,
    EntryUpdateArguments,
    EvidenceSyncArguments,
    InitArguments,
    RetentionArguments,
    TransferArguments,
)

FAMILIES = (
    "add",
    "command",
    "data",
    "discover",
    "evidence",
    "init",
    "reproduce",
    "reorganize",
    "retention",
    "validate",
)
AUTHORING_FAMILIES = frozenset(
    {"add", "command", "data", "evidence", "init", "reorganize", "retention"}
)


class _AuthoringParser(argparse.ArgumentParser):
    """An authoring parser that preserves the structured failure contract."""

    def error(self, message: str) -> NoReturn:
        raise ActionError("cli.arguments.invalid", message)


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
        if family
        in {"command", "data", "evidence", "reorganize", "retention", "validate"}
        and arguments
        else family
    )
    try:
        read_only_dispatch = {
            "discover": _dispatch_discover,
            "reproduce": _dispatch_reproduce,
            "validate": _dispatch_validate,
        }
        if family in read_only_dispatch:
            return read_only_dispatch[family](arguments)
        dispatch = {
            "add": _dispatch_add,
            "command": _dispatch_command,
            "data": _dispatch_data,
            "evidence": _dispatch_evidence,
            "init": _dispatch_init,
            "reorganize": _dispatch_reorganize,
            "retention": _dispatch_retention,
        }
        result = dispatch[family](arguments)
        if isinstance(result, int):
            return result
        if isinstance(result, str):
            print(result, end="")
        elif isinstance(result, Mapping):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            from validation.json_codec import canonical_json

            print(canonical_json(result.as_dict()))
        return 0
    except (ActionError, ResultStoreError, OSError, UnicodeError) as error:
        return _report_failure(
            family, selected_task, error, dry_run="--dry-run" in arguments
        )
    except ValueError as error:
        if not hasattr(error, "code"):
            raise
        return _report_failure(
            family, selected_task, error, dry_run="--dry-run" in arguments
        )


def _report_failure(
    family: str,
    selected_task: str,
    error: Exception,
    *,
    dry_run: bool = False,
) -> int:
    """Emit one bounded expected operational or contract failure."""

    code = getattr(error, "code", f"{family}.failed")
    print(f"log: {code}: {error}", file=sys.stderr)
    if isinstance(error, ActionError) and error.records is not None:
        from .diagnostic_errors import report_diagnostic

        if dry_run:
            print("Diagnostic not cached (--dry-run).", file=sys.stderr)
            print(
                json.dumps(
                    ActionResult(
                        selected_task,
                        "failed",
                        str(code),
                        False,
                        records=error.records,
                    ).as_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            report_diagnostic(error)
        return 2
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


def _entry_arguments(
    parser: argparse.ArgumentParser, *, path_required: bool = False
) -> None:
    parser.add_argument(
        "--path",
        type=Path,
        required=path_required,
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
    parser = _AuthoringParser(prog="log init")
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
    parser = _AuthoringParser(prog="log add")
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


def _evidence_refresh_parsers(actions: argparse._SubParsersAction) -> None:
    for name in ("compare", "sync"):
        if name == "compare":
            action = actions.add_parser(
                name,
                help="Show read-only before/after values for Markdown evidence",
                description=(
                    "Show read-only before/after presented values for one "
                    "evidence ID or all related evidence from a source."
                ),
            )
        else:
            action = actions.add_parser(
                name,
                help="Apply Markdown evidence and refresh fingerprints",
                description=(
                    "Apply selected Markdown evidence, or all related evidence "
                    "from a source, and refresh fingerprints."
                ),
            )
        _entry_arguments(action)
        if name == "compare":
            scope = action.add_mutually_exclusive_group(required=True)
            scope.add_argument("--id", help="one Markdown evidence ID")
            scope.add_argument(
                "--source",
                help=(
                    "compare related evidence from a direct generated declaration "
                    "in its owning entry"
                ),
            )
        else:
            action.add_argument(
                "--id",
                action="append",
                default=[],
                help="sync selected Markdown evidence ID",
            )
            action.add_argument(
                "--rename", action="append", default=[], metavar="OLD=NEW"
            )
            action.add_argument("--delete", action="append", default=[], metavar="ID")
            action.add_argument(
                "--source",
                help=(
                    "sync related evidence from a direct generated declaration "
                    "in its owning entry"
                ),
            )
        if name == "sync":
            _mutation_argument(action)
            for flag in (
                "--add-origin",
                "--add-origin-directory",
                "--add-from-entry",
                "--change-target",
            ):
                action.add_argument(
                    flag,
                    action="append",
                    default=[],
                    metavar="NAME=ENTRY" if flag == "--add-from-entry" else "NAME=PATH",
                )


def _dispatch_evidence_refresh(
    entry: EntryContext, args: argparse.Namespace
) -> ActionResult:
    from . import evidence_sync

    return evidence_sync.compare_or_sync(
        entry,
        args.action,
        EvidenceSyncArguments(
            record_id=args.id if args.action == "compare" else None,
            source=args.source,
            record_ids=tuple(args.id) if args.action == "sync" else (),
            renames=tuple(getattr(args, "rename", ())),
            deletions=tuple(getattr(args, "delete", ())),
            add_origins=tuple(getattr(args, "add_origin", ())),
            add_origin_directories=tuple(getattr(args, "add_origin_directory", ())),
            add_from_entries=tuple(getattr(args, "add_from_entry", ())),
            target_changes=tuple(getattr(args, "change_target", ())),
            dry_run=getattr(args, "dry_run", False),
        ),
    )


def _dispatch_evidence(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log evidence")
    actions = parser.add_subparsers(dest="action", required=True)
    _evidence_refresh_parsers(actions)
    listed = actions.add_parser("list", help="List bounded evidence semantics")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action in {"compare", "sync"}:
        return _dispatch_evidence_refresh(entry, args)
    from . import evidence

    return evidence.list_records(entry)


def _dispatch_data(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log data")
    actions = parser.add_subparsers(dest="action", required=True)
    _add_data_update_parser(actions)
    rename = actions.add_parser(
        "rename", help="Rename an input after recorded-command token edits"
    )
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_name")
    rename.add_argument("new_name")
    remove = actions.add_parser(
        "delete",
        help="Delete an unused data declaration, never retained bytes",
        description=(
            "Delete an unused data declaration, never retained bytes. First "
            "remove its command and evidence uses and sync those edits; "
            "remaining consumers block deletion."
        ),
    )
    _entry_arguments(remove)
    _mutation_argument(remove)
    remove.add_argument("name")
    listed = actions.add_parser("list", help="List bounded input semantics")
    _entry_arguments(listed)
    args = parser.parse_args(arguments)
    from . import data

    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.action == "update":
        result = data.update(
            entry,
            DataUpdateArguments(
                name=args.name,
                target=args.target,
                boundary=args.boundary,
                identity=(tuple(args.identity) if args.identity is not None else None),
                kind=args.kind,
                acknowledge_shared=args.acknowledge_shared,
                reproduction_comparison=args.reproduction_comparison,
                dry_run=args.dry_run,
            ),
        )
    elif args.action == "rename":
        result = data.rename(entry, args.old_name, args.new_name, dry_run=args.dry_run)
    elif args.action == "delete":
        result = data.remove(entry, args.name, dry_run=args.dry_run)
    else:
        result = data.list_inputs(entry)
    return result


def _dispatch_command(arguments: Sequence[str]) -> ActionResult | int:
    parser = _AuthoringParser(prog="log command")
    actions = parser.add_subparsers(dest="action", required=True)
    sync = actions.add_parser(
        "sync",
        help="Synchronize selected Markdown-owned commands and lifecycle edits",
        description=(
            "Select each command by its full effective CID: the Python program "
            "stem by default, stem-N for an authored numeric --cid N, or a full "
            "authored override. The CLI selector does not require adding "
            "--cid to Markdown."
        ),
    )
    _entry_arguments(sync)
    sync.add_argument(
        "--cid", action="append", default=[], help="full effective CID"
    )
    sync.add_argument("--rename", action="append", default=[], metavar="OLD=NEW")
    sync.add_argument("--delete", action="append", default=[], metavar="CID")
    sync.add_argument("--add-origin", action="append", default=[], metavar="NAME=PATH")
    sync.add_argument(
        "--add-origin-directory",
        action="append",
        default=[],
        metavar="NAME=PATH",
    )
    sync.add_argument(
        "--add-origin-git",
        action="append",
        default=[],
        metavar="NAME=COMMIT:PATH",
    )
    sync.add_argument(
        "--add-generated", action="append", default=[], metavar="NAME=PATH"
    )
    sync.add_argument(
        "--add-generated-directory",
        action="append",
        default=[],
        metavar="NAME=PATH",
    )
    sync.add_argument(
        "--add-from-entry", action="append", default=[], metavar="NAME=ENTRY"
    )
    sync.add_argument(
        "--change-target", action="append", default=[], metavar="NAME=TARGET"
    )
    sync.add_argument(
        "--delete-stale-executions",
        action="append",
        default=[],
        metavar="CID",
    )
    _mutation_argument(sync)
    release = actions.add_parser(
        "release", help="Release abandoned ordinary execution reservations"
    )
    _entry_arguments(release)
    _mutation_argument(release)
    release.add_argument("--cid", required=True)
    listed = actions.add_parser("list", help="List command recipes and policies")
    _entry_arguments(listed)
    verify = actions.add_parser(
        "verify", help="Run one current recorded command in an isolated workspace"
    )
    _entry_arguments(verify, path_required=True)
    verify.add_argument("--cid", required=True)
    verify.add_argument("--execution-id", required=True)
    from .reproduction_invocation import (
        DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    )

    verify.add_argument(
        "--execution-timeout-seconds",
        type=int,
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        metavar="SECONDS",
    )
    verify.add_argument("--format", choices=("text", "json"), default="text")
    show = actions.add_parser("show", help="Show the latest command diagnostic")
    show.add_argument("--path", required=True, type=Path)
    show.add_argument("--id")
    show.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    if args.action == "verify":
        return _run_command_verification(args)
    if args.action == "show":
        return _show_command_diagnostic(args)
    if args.action in {"list", "release"}:
        entry = resolve_entry(resolve_log(args.path), args.entry)
        return _dispatch_command_lifecycle(entry, args)
    from .command_sync import sync_command

    return sync_command(
        resolve_entry(resolve_log(args.path), args.entry),
        CommandSyncArguments(
            cids=tuple(args.cid),
            renames=tuple(args.rename),
            deletions=tuple(args.delete),
            add_origins=tuple(args.add_origin),
            add_origin_directories=tuple(args.add_origin_directory),
            add_origin_git=tuple(args.add_origin_git),
            add_generated=tuple(args.add_generated),
            add_generated_directories=tuple(args.add_generated_directory),
            add_from_entries=tuple(args.add_from_entry),
            target_changes=tuple(args.change_target),
            stale_execution_deletions=tuple(args.delete_stale_executions),
            dry_run=args.dry_run,
        ),
    )


def _dispatch_command_lifecycle(
    entry: EntryContext, args: argparse.Namespace
) -> ActionResult:
    from . import command_lifecycle

    if args.action == "list":
        return command_lifecycle.list_commands(entry)
    from research_log_reservations import release_abandoned

    from .context import resolve_project_root

    count = release_abandoned(
        resolve_project_root(entry.root), entry.root, args.cid, dry_run=args.dry_run
    )
    return ActionResult(
        "command.release",
        "dry-run" if args.dry_run else "changed" if count else "unchanged",
        "command.released",
        bool(count),
        records=({"reservations": count},),
    )


def _run_command_verification(args: argparse.Namespace) -> int:
    """Run one isolated current command without creating reproduction state."""

    from .command_verification import CommandVerificationRequest, verify_command

    result = verify_command(
        resolve_log(args.path),
        CommandVerificationRequest(
            args.entry,
            args.cid,
            args.execution_id,
            args.execution_timeout_seconds,
        ),
    )
    if args.format == "json":
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(f"{result.status}: {result.execution_id}\nworkspace: {result.workspace}")
        policy_value = result.execution.get("policy", {})
        policy = policy_value if isinstance(policy_value, Mapping) else {}
        print(
            "policy: "
            f"auto_reproduce={policy.get('auto_reproduce')} "
            f"requires_reproduction={policy.get('requires_reproduction')}"
        )
        sources = result.execution.get("sources", [])
        for source in sources if isinstance(sources, list) else []:
            if isinstance(source, Mapping):
                print(
                    "source: "
                    f"{source.get('name')} current={source.get('current')} "
                    f"recorded={source.get('recorded')} "
                    f"differs={source.get('differs_from_recorded')}"
                )
        for item in result.inputs:
            print(
                "input: "
                f"{item.get('name')} current={item.get('current')} "
                f"recorded={item.get('recorded')} "
                f"differs={item.get('differs_from_recorded')}"
            )
        for output in result.outputs:
            print(
                f"output: {output.get('artifact')} {output.get('kind')} "
                f"{output.get('path')} "
                f"{output.get('outcome')} {output.get('reason') or ''}".rstrip()
            )
        print(f"stdout: {result.diagnostics.get('stdout_path', '')}")
        print(result.diagnostics.get("stdout", ""), end="")
        print(f"stderr: {result.diagnostics.get('stderr_path', '')}")
        print(result.diagnostics.get("stderr", ""), end="")
    return result.exit_status


def _show_command_diagnostic(args: argparse.Namespace) -> int:
    """Show one retained command-owned diagnostic without validation."""

    from validation.command_diagnostics import load_command_diagnostic

    log = resolve_log(args.path)
    value = load_command_diagnostic(log.root, diagnostic_id=args.id)
    if args.format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"Diagnostic: {value['diagnostic_id']}")
    print(f"Saved: {value['stored_at']}")
    print(f"Operation: {value['operation']}")
    print(f"Code: {value['code']}")
    if value.get("entry"):
        print(f"Entry: {value['entry']}")
    records = value["records"]
    for record in records if isinstance(records, list) else []:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def _add_data_update_parser(
    actions: argparse._SubParsersAction[_AuthoringParser],
) -> None:
    update = actions.add_parser(
        "update", help="Change explicitly selected input properties"
    )
    _entry_arguments(update)
    _mutation_argument(update)
    update.add_argument("name", help="existing input name")
    update.add_argument("--target", help="replacement PATH or COMMIT:PATH Git origin")
    update.add_argument("--boundary", choices=("origin", "generated"))
    update.add_argument("--kind", choices=("file", "directory"))
    update.add_argument(
        "--acknowledge-shared",
        action="store_true",
        help="accept the listed wider consumer scope without bypassing validation",
    )
    update.add_argument(
        "--identity",
        action="append",
        help="replace directory identity: byte-complete, file:PATH, or pattern:GLOB",
    )
    update.add_argument(
        "--reproduction-comparison",
        choices=("exact", "evidence"),
        help="select exact-default or evidence-scoped reproduction comparison",
    )


def _dispatch_retention(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log retention")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("add", "update"):
        verb = "Add a new" if name == "add" else "Update an existing"
        action = actions.add_parser(
            name,
            help=f"{verb} disconnected-retention decision",
            description=f"{verb} disconnected-retention decision.",
        )
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("--id", required=True, help="stable retention ID")
        reason = action.add_mutually_exclusive_group()
        reason.add_argument("--reason", help="concise retention intent")
        if name == "add":
            action.add_argument("--target", action="append", required=True)
        else:
            reason.add_argument("--clear-reason", action="store_true")
            action.add_argument("--add-target", action="append", default=[])
            action.add_argument("--remove-target", action="append", default=[])
    rename = actions.add_parser("rename", help="Rename one retention ID")
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    remove = actions.add_parser(
        "delete", help="Delete one retention decision without deleting material"
    )
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
                targets=tuple(args.target if args.action == "add" else args.add_target),
                remove_targets=tuple(getattr(args, "remove_target", ())),
                clear_reason=getattr(args, "clear_reason", False),
                reason=args.reason,
                dry_run=args.dry_run,
            ),
        )
    if args.action == "rename":
        return retention.rename(entry, args.old_id, args.new_id, dry_run=args.dry_run)
    if args.action == "delete":
        return retention.remove(entry, args.id, dry_run=args.dry_run)
    return retention.list_records(entry)


def _dispatch_reorganize(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log reorganize")
    actions = parser.add_subparsers(dest="action", required=True)

    update = actions.add_parser("update-entry", help="Apply one edited entry identity")
    _entry_arguments(update)
    _mutation_argument(update)
    update.add_argument("--date")
    update.add_argument("--slug")
    update.add_argument("--title")

    reorder = actions.add_parser("reorder", help="Apply one complete edited ID order")
    reorder.add_argument("--path", required=True, type=Path)
    _mutation_argument(reorder)
    reorder.add_argument("--entries", required=True)

    relocate = actions.add_parser("relocate-log", help="Relocate one complete log pair")
    relocate.add_argument("--path", required=True, type=Path)
    relocate.add_argument("--to", required=True, type=Path)
    _mutation_argument(relocate)

    transfer = actions.add_parser(
        "transfer", help="Coordinate selected authored registry changes"
    )
    transfer.add_argument("--path", required=True, type=Path)
    _mutation_argument(transfer)
    transfer.add_argument("--from-entry", required=True)
    transfer.add_argument("--to-entry", required=True)
    transfer.add_argument("--all", action="store_true")
    transfer.add_argument("--evidence")
    transfer.add_argument("--data")
    transfer.add_argument("--retention")
    for name in ("document", "path", "data", "evidence", "retention"):
        transfer.add_argument(
            f"--{name}-map",
            nargs=2,
            action="append",
            default=[],
            metavar=("SOURCE", "DESTINATION"),
        )

    remove = actions.add_parser(
        "remove-empty-entry", help="Remove one already unlisted empty scaffold"
    )
    _entry_arguments(remove)
    _mutation_argument(remove)

    args = parser.parse_args(arguments)
    from . import reorganize

    log = resolve_log(args.path)
    if args.action == "update-entry":
        return reorganize.update_entry(
            resolve_entry(log, args.entry),
            EntryUpdateArguments(args.date, args.slug, args.title, args.dry_run),
        )
    if args.action == "reorder":
        return reorganize.reorder(log, _csv(args.entries), dry_run=args.dry_run)
    if args.action == "relocate-log":
        return reorganize.relocate_log(log, args.to, dry_run=args.dry_run)
    if args.action == "remove-empty-entry":
        return reorganize.remove_empty_entry(
            resolve_entry(log, args.entry), dry_run=args.dry_run
        )
    return reorganize.transfer(
        log,
        TransferArguments(
            source_entry=args.from_entry,
            destination_entry=args.to_entry,
            evidence=_csv(args.evidence),
            data=_csv(args.data),
            retention=_csv(args.retention),
            select_all=args.all,
            document_maps=tuple(map(tuple, args.document_map)),
            path_maps=tuple(map(tuple, args.path_map)),
            data_maps=tuple(map(tuple, args.data_map)),
            evidence_maps=tuple(map(tuple, args.evidence_map)),
            retention_maps=tuple(map(tuple, args.retention_map)),
            dry_run=args.dry_run,
        ),
    )


def _csv(value: str | None) -> tuple[str, ...]:
    """Decode one nonempty comma-separated selector list."""

    if value is None:
        return ()
    items = tuple(value.split(","))
    if not items or any(not item or item != item.strip() for item in items):
        raise ActionError("reorganize.selector.invalid", str(value))
    if len(items) != len(set(items)):
        raise ActionError("reorganize.selector.duplicate", str(value))
    return items


def _dispatch_discover(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log discover")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(arguments)
    from .validation_run import run_discover

    return run_discover(args.root)


def _dispatch_validate(arguments: Sequence[str]) -> int:
    from .validation_cli import run_validate_cli

    return run_validate_cli(arguments)


def _dispatch_reproduce(arguments: Sequence[str]) -> int:
    if arguments and arguments[0] == "plan":
        return _dispatch_reproduction_plan(arguments[1:])
    if arguments and arguments[0] in {"show", "list", "detail", "render"}:
        from .reproduction_inspection_cli import dispatch

        return dispatch(arguments)
    return _dispatch_reproduction_execution(arguments)


def _reproduction_options(args: argparse.Namespace) -> tuple[RunTarget, RunSettings]:
    from .reproduction_domain import ReproductionDomainError
    from .reproduction_saved_run import RunSettings, RunTarget

    try:
        target = (
            RunTarget("entry", args.entry) if args.entry is not None else RunTarget()
        )
    except ReproductionDomainError as error:
        raise ActionError("reproduction.selector.invalid", str(error)) from error
    try:
        settings = RunSettings(
            args.include_all, args.recheck, args.jobs, args.execution_timeout_seconds
        )
    except ReproductionDomainError as error:
        code = (
            "reproduction.jobs.invalid"
            if args.jobs < 1
            else "reproduction.execution_timeout.invalid"
        )
        raise ActionError(code, str(error)) from error
    return target, settings


def _dispatch_reproduction_plan(arguments: Sequence[str]) -> int:
    from .reproduction_invocation import DEFAULT_EXECUTION_TIMEOUT_SECONDS
    from .reproduction_jobs import preview_reproduction
    from .reproduction_plan_preview import render_plan_page

    parser = argparse.ArgumentParser(prog="log reproduce plan")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--entry")
    parser.add_argument("--include-all", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--execution-timeout-seconds",
        type=int,
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    )
    parser.add_argument("--cursor")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    target, settings = _reproduction_options(args)
    value = preview_reproduction(
        resolve_log(args.path),
        target,
        settings,
        cursor=args.cursor,
        format=args.format,
    )
    print(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        if args.format == "json"
        else render_plan_page(value),
        end="\n" if args.format == "json" else "",
    )
    return 0


def _dispatch_reproduction_execution(arguments: Sequence[str]) -> int:
    from .reproduction_invocation import (
        DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    )

    if arguments and arguments[0] == "promote":
        parser = argparse.ArgumentParser(prog="log reproduce promote")
        parser.add_argument("--path", required=True, type=Path)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--cid", required=True)
        parser.add_argument("--execution-id", required=True)
        args = parser.parse_args(arguments[1:])
        from .reproduction_promotion import promote_execution

        result = promote_execution(
            resolve_log(args.path),
            run_id=args.run_id,
            cid=args.cid,
            execution_id=args.execution_id,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if arguments and arguments[0] in {"status", "stop", "resume"}:
        return _dispatch_reproduction_job(arguments[0], arguments[1:])
    from .reproduction_jobs import launch_reproduction

    parser = argparse.ArgumentParser(prog="log reproduce run")
    if not arguments or arguments[0] != "run":
        parser.error(
            "choose plan, run, show, list, detail, render, "
            "status, stop, resume or promote"
        )
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--entry")
    parser.add_argument("--include-all", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--execution-timeout-seconds",
        type=int,
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        metavar="SECONDS",
    )
    args = parser.parse_args(arguments[1:])
    target, settings = _reproduction_options(args)
    launch = launch_reproduction(
        resolve_log(args.path),
        target,
        settings,
    )
    print(launch.render(), end="")
    return 0


def _dispatch_reproduction_job(action: str, arguments: Sequence[str]) -> int:
    description = (
        "Continue unresolved work in the original logical command queue"
        if action == "resume"
        else None
    )
    parser = argparse.ArgumentParser(
        prog=f"log reproduce {action}", description=description
    )
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    if action == "status":
        parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    from .reproduction_jobs import (
        format_reproduction_status,
        reproduction_status,
        resume_reproduction,
        stop_reproduction,
    )

    log = resolve_log(args.path)
    if action == "status":
        status = reproduction_status(log, args.run_id)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        else:
            print(format_reproduction_status(status), end="")
    elif action == "stop":
        stop_reproduction(log, args.run_id)
        print(args.run_id)
    else:
        print(resume_reproduction(log, args.run_id))
    return 0
