"""Lazy command dispatcher for the public research-log management entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

from .context import resolve_entry, resolve_log, resolve_log_creation
from .model import (
    ActionError,
    ActionResult,
    AddArguments,
    DataAddArguments,
    DataUpdateArguments,
    EntryUpdateArguments,
    EvidenceCommonArguments,
    InitArguments,
    RetentionArguments,
    TransferArguments,
)

FAMILIES = (
    "add",
    "data",
    "discover",
    "evidence",
    "findings",
    "init",
    "pyrun",
    "reproduce",
    "reorganize",
    "retention",
    "results",
    "validate",
    "validate-batch",
)
AUTHORING_FAMILIES = frozenset(
    {"add", "data", "evidence", "init", "pyrun", "reorganize", "retention"}
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
        if family in {"data", "evidence", "pyrun", "reorganize", "retention"}
        and arguments
        else family
    )
    try:
        read_only_dispatch = {
            "discover": _dispatch_discover,
            "findings": _dispatch_findings,
            "reproduce": _dispatch_reproduce,
            "results": _dispatch_results,
            "validate": _dispatch_validate,
            "validate-batch": _dispatch_validate_batch,
        }
        if family in read_only_dispatch:
            return read_only_dispatch[family](arguments)
        dispatch = {
            "add": _dispatch_add,
            "data": _dispatch_data,
            "evidence": _dispatch_evidence,
            "init": _dispatch_init,
            "pyrun": _dispatch_pyrun,
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
            print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ActionError, OSError, UnicodeError) as error:
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
            print("Diagnostic not cached (--dry-run).")
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


def _dispatch_evidence(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log evidence")
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
        action.add_argument(
            "--reproduction-tolerance",
            help="absolute numeric tolerance for evidence-scoped reproduction",
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
                or args.reproduction_tolerance is not None
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
                reproduction_tolerance=args.reproduction_tolerance,
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
    parser = _AuthoringParser(prog="log data")
    actions = parser.add_subparsers(dest="action", required=True)
    _add_data_input_parsers(actions)
    use = actions.add_parser(
        "use", help="Reference one generated artifact declared by another entry"
    )
    _entry_arguments(use)
    _mutation_argument(use)
    use.add_argument("--from-entry", required=True)
    use.add_argument("name")
    _add_data_update_parser(actions)
    rename = actions.add_parser(
        "rename", help="Rename an input after recorded-command token edits"
    )
    _entry_arguments(rename)
    _mutation_argument(rename)
    rename.add_argument("old_name")
    rename.add_argument("new_name")
    refresh = actions.add_parser("refresh", help="Record an intentional byte change")
    _entry_arguments(refresh)
    _mutation_argument(refresh)
    refresh.add_argument("name")
    refresh.add_argument(
        "--requires-reproduction",
        action="store_true",
        help="fingerprint restored generated material that still requires reproduction",
    )
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
    if args.action == "use":
        result = data.use(
            entry,
            source=resolve_entry(entry.log, args.from_entry),
            name=args.name,
            dry_run=args.dry_run,
        )
    elif args.action in {"add-origin", "add-generated"}:
        result = data.add(
            entry,
            generated=args.action == "add-generated",
            arguments=DataAddArguments(
                name=args.name,
                target=args.target,
                kind=getattr(args, "kind", None),
                identity=(
                    tuple(args.identity)
                    if getattr(args, "identity", None) is not None
                    else None
                ),
                commit=getattr(args, "commit", None),
                requires_reproduction=getattr(args, "requires_reproduction", False),
                dry_run=args.dry_run,
            ),
        )
    elif args.action == "update":
        classification_value = (
            "origin" if args.origin else "generated" if args.generated else None
        )
        result = data.update(
            entry,
            DataUpdateArguments(
                name=args.name,
                target=args.target,
                classification=classification_value,
                identity=(tuple(args.identity) if args.identity is not None else None),
                byte_complete=args.byte_complete,
                commit=args.commit,
                reproduction_comparison=args.reproduction_comparison,
                dry_run=args.dry_run,
            ),
        )
    elif args.action == "rename":
        result = data.rename(entry, args.old_name, args.new_name, dry_run=args.dry_run)
    elif args.action == "refresh":
        result = data.refresh(
            entry,
            args.name,
            dry_run=args.dry_run,
            requires_reproduction=args.requires_reproduction,
        )
    elif args.action == "remove":
        result = data.remove(entry, args.name, dry_run=args.dry_run)
    else:
        result = data.list_inputs(entry)
    return result


def _add_data_input_parsers(
    actions: argparse._SubParsersAction[_AuthoringParser],
) -> None:
    for name in ("add-origin", "add-generated"):
        description = (
            "Register one producerless material input and stop Provenance"
            if name == "add-origin"
            else "Declare one named same-log generated artifact"
        )
        action = actions.add_parser(name, help=description, description=description)
        _entry_arguments(action)
        _mutation_argument(action)
        action.add_argument("name", help="stable entry-scoped input name")
        action.add_argument(
            "target",
            help="absolute or entry-root-relative file or directory",
        )
        if name == "add-origin":
            representation = action.add_mutually_exclusive_group()
            representation.add_argument(
                "--identity",
                action="append",
                help="authoritative directory file or final-component pattern",
            )
            representation.add_argument(
                "--commit",
                help="full lowercase commit hash identifying a Git repository input",
            )
        else:
            action.add_argument(
                "--kind",
                choices=("file", "directory"),
                help="declared kind, required before the output exists",
            )
            action.add_argument(
                "--identity",
                action="append",
                help="authoritative generated-directory file or pattern",
            )
            action.add_argument(
                "--requires-reproduction",
                action="store_true",
                help=(
                    "register one uniquely declared existing output before "
                    "its required reproduction"
                ),
            )


def _add_data_update_parser(
    actions: argparse._SubParsersAction[_AuthoringParser],
) -> None:
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
        help="require current same-log production that needs no reproduction",
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
    identity.add_argument(
        "--commit",
        help="full lowercase commit hash identifying a Git repository input",
    )
    update.add_argument(
        "--reproduction-comparison",
        choices=("exact", "evidence"),
        help="select exact-default or evidence-scoped reproduction comparison",
    )


def _dispatch_pyrun(
    arguments: Sequence[str],
) -> ActionResult | Mapping[str, object] | str | int:
    parser = _AuthoringParser(prog="log pyrun")
    actions = parser.add_subparsers(dest="action", required=True)
    update = actions.add_parser(
        "update", help="Apply one Markdown-first execution policy change"
    )
    _entry_arguments(update)
    update.add_argument("--execution-id", required=True)
    policy = update.add_mutually_exclusive_group(required=True)
    policy.add_argument(
        "--auto-reproduce",
        choices=("true", "false"),
        help="exact automatic-reproduction policy",
    )
    policy.add_argument(
        "--exclusive",
        choices=("true", "false"),
        help="exact managed-reproduction exclusivity policy",
    )
    args = parser.parse_args(arguments)
    from . import pyrun_policy

    entry = resolve_entry(resolve_log(args.path), args.entry)
    if args.auto_reproduce is not None:
        return pyrun_policy.update_auto_reproduce(
            entry,
            execution_id_value=args.execution_id,
            auto_reproduce=args.auto_reproduce == "true",
        )
    return pyrun_policy.update_exclusive(
        entry,
        execution_id_value=args.execution_id,
        exclusive=args.exclusive == "true",
    )


def _dispatch_retention(arguments: Sequence[str]) -> ActionResult:
    parser = _AuthoringParser(prog="log retention")
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
    parser.add_argument("--recompute-validation", action="store_true")
    parser.add_argument("--recompute-fingerprints", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    from .validation_adapter import ValidationOptions, run_validate

    return run_validate(
        path=args.path,
        root=args.root,
        output_format=args.format,
        options=ValidationOptions(
            result_date=args.date,
            dry_run=args.dry_run,
            recompute_validation=(args.recompute or args.recompute_validation),
            recompute_fingerprints=(args.recompute or args.recompute_fingerprints),
        ),
    )


def _dispatch_reproduction_report(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log reproduce report")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path", type=Path)
    selection.add_argument("--root", type=Path)
    parser.add_argument("--entry")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    if args.root is not None and not args.summary:
        parser.error("--root requires --summary")
    if args.entry is not None and (args.root is not None or args.summary):
        parser.error("--entry is available only for a full per-log report")
    if args.format == "json" and not args.summary:
        parser.error("--format json requires --summary")
    from .reproduction_queries import (
        compose_root_reproduction_summary,
        reproduction_report,
        reproduction_summary,
        reproduction_summary_text,
        root_reproduction_summary,
    )

    if args.root is not None:
        report_summary = root_reproduction_summary(args.root)
        output = (
            json.dumps(report_summary, ensure_ascii=False, sort_keys=True) + "\n"
            if args.format == "json"
            else compose_root_reproduction_summary(report_summary)
        )
        print(output, end="")
        coverage = report_summary["coverage"]
        assert isinstance(coverage, Mapping)
        return 3 if coverage["unavailable"] else 0
    log = resolve_log(args.path)
    if not args.summary:
        print(reproduction_report(log, entry=args.entry), end="")
    elif args.format == "json":
        print(json.dumps(reproduction_summary(log), ensure_ascii=False, sort_keys=True))
    else:
        print(reproduction_summary_text(log), end="")
    return 0


def _dispatch_reproduce(arguments: Sequence[str]) -> int:
    from .reproduction_contract import (
        DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        ReproductionRuntime,
    )

    if arguments and arguments[0] == "report":
        return _dispatch_reproduction_report(arguments[1:])
    if arguments and arguments[0] == "artifacts":
        return _dispatch_reproduction_artifacts(arguments[1:])
    if arguments and arguments[0] == "commands":
        return _dispatch_reproduction_commands(arguments[1:])
    if arguments and arguments[0] == "promote":
        parser = argparse.ArgumentParser(prog="log reproduce promote")
        parser.add_argument("--path", required=True, type=Path)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--execution-id", required=True)
        args = parser.parse_args(arguments[1:])
        from .reproduction_promotion import promote_execution

        result = promote_execution(
            resolve_log(args.path),
            run_id=args.run_id,
            execution_id=args.execution_id,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if arguments and arguments[0] in {"status", "stop", "resume"}:
        return _dispatch_reproduction_job(arguments[0], arguments[1:])
    parser = argparse.ArgumentParser(prog="log reproduce")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--entry")
    parser.add_argument("--execution-id")
    parser.add_argument("--include-all", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument(
        "--verify-repair",
        action="store_true",
        help="verify intentionally repaired source for one recorded execution",
    )
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--execution-timeout-seconds",
        type=int,
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="maximum runtime for each command (default: 300 seconds)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a bounded human summary of a dry-run plan",
    )
    args = parser.parse_args(arguments)
    _validate_reproduction_arguments(parser, args)
    log = resolve_log(args.path)
    from .reproduction_jobs import dry_run_reproduction, launch_reproduction
    from .reproduction_planner import ReproductionSelection

    selection = ReproductionSelection(
        "recheck" if args.recheck else "incremental",
        execution_id=args.execution_id,
        verify_repair=args.verify_repair,
    )

    if args.dry_run:
        plan = dry_run_reproduction(
            log,
            entry=args.entry,
            include_all=args.include_all,
            runtime=ReproductionRuntime(args.jobs, args.execution_timeout_seconds),
            selection=selection,
        )
        if args.summary:
            from .reproduction_contract import format_reproduction_plan_summary

            print(
                format_reproduction_plan_summary(plan, recheck=args.recheck),
                end="",
            )
        else:
            print(plan.serialized())
    else:
        launch = launch_reproduction(
            log,
            entry=args.entry,
            include_all=args.include_all,
            runtime=ReproductionRuntime(args.jobs, args.execution_timeout_seconds),
            selection=selection,
        )
        print(launch.render(), end="")
    return 0


def _validate_reproduction_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.execution_id is not None and args.entry is None:
        parser.error("--execution-id requires --entry")
    if args.verify_repair and (not args.recheck or args.execution_id is None):
        parser.error("--verify-repair requires --entry, --execution-id, and --recheck")
    if args.summary and not args.dry_run:
        parser.error("--summary requires --dry-run")


def _dispatch_validate_batch(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log validate-batch")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    from .repair_validation import validate_repair_batch

    value, complete = validate_repair_batch(
        resolve_log(args.path), validation_id=args.validation, batch_id=args.batch
    )
    from .inspection_cli import print_producer

    print_producer(value, args.path, args.format)
    return 0 if complete else 2


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


def _dispatch_reproduction_artifacts(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log reproduce artifacts")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="List current reproduction artifacts")
    listing.add_argument("--path", required=True, type=Path)
    listing.add_argument("--entry")
    listing.add_argument("--outcome")
    listing.add_argument("--artifact")
    showing = actions.add_parser("show", help="Show one reproduction artifact")
    showing.add_argument("--path", required=True, type=Path)
    showing.add_argument("--entry", required=True)
    showing.add_argument("--artifact", required=True)
    args = parser.parse_args(arguments)
    from .reproduction_queries import (
        list_reproduction_artifacts,
        show_reproduction_artifact,
    )

    log = resolve_log(args.path)
    if args.action == "list":
        result = list_reproduction_artifacts(
            log,
            entry=args.entry,
            outcome=args.outcome,
            artifact=args.artifact,
        )
    else:
        result = show_reproduction_artifact(
            log, entry=args.entry, artifact=args.artifact
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _dispatch_reproduction_commands(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log reproduce commands")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser(
        "list", help="List current completed-run command accounting"
    )
    listing.add_argument("--path", required=True, type=Path)
    listing.add_argument("--bucket")
    listing.add_argument("--entry")
    listing.add_argument("--reason")
    listing.add_argument("--run-id")
    showing = actions.add_parser(
        "show", help="Show one current completed-run command record"
    )
    showing.add_argument("--path", required=True, type=Path)
    showing.add_argument("--entry", required=True)
    showing.add_argument("--execution-id", required=True)
    showing.add_argument("--run-id")
    for subparser in (listing, showing):
        subparser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    from .reproduction_queries import (
        compose_reproduction_command,
        compose_reproduction_command_list,
        list_reproduction_commands,
        show_reproduction_command,
    )

    log = resolve_log(args.path)
    if args.action == "list":
        result = list_reproduction_commands(
            log,
            bucket=args.bucket,
            entry=args.entry,
            reason=args.reason,
            run_id=args.run_id,
        )
        output = (
            json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
            if args.format == "json"
            else compose_reproduction_command_list(
                result,
                path=args.path,
                program=Path(sys.argv[0]),
            )
        )
    else:
        result = show_reproduction_command(
            log,
            entry=args.entry,
            execution_id=args.execution_id,
            run_id=args.run_id,
        )
        output = (
            json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
            if args.format == "json"
            else compose_reproduction_command(result)
        )
    print(output, end="")
    return 0


def _dispatch_findings(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="log findings")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="List complete published finding batches")
    listing.add_argument("--path", required=True, type=Path)
    listing.add_argument("--entry", action="append", default=[])
    listing.add_argument("--validation-area", action="append", default=[])
    listing.add_argument("--code", action="append", default=[])
    listing.add_argument("--family", action="append", default=[])
    listing.add_argument("--subject", action="append", default=[])
    listing.add_argument("--command", action="append", default=[])
    batch = actions.add_parser("batch", help="Show one complete finding batch")
    batch.add_argument("--path", required=True, type=Path)
    batch.add_argument("--validation", required=True)
    batch.add_argument("--entry", required=True)
    batch.add_argument("--chain", required=True)
    showing = actions.add_parser("show", help="Show one published finding")
    showing.add_argument("--path", required=True, type=Path)
    showing.add_argument("--id", required=True)
    for subparser in (listing, batch, showing):
        subparser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(arguments)
    from .findings import FindingFilters, batch_findings, list_findings, show_finding

    log = resolve_log(args.path)
    if args.action == "list":
        result = list_findings(
            log,
            filters=FindingFilters(
                entries=args.entry,
                areas=args.validation_area,
                codes=args.code,
                families=args.family,
                subjects=args.subject,
                commands=args.command,
            ),
        )
    elif args.action == "batch":
        result = batch_findings(
            log,
            validation_id=args.validation,
            entry=args.entry,
            chain_id=args.chain,
        )
    else:
        result = show_finding(log, check_id=args.id)
    from .inspection_cli import print_findings

    print_findings(result, log.root, args.format)
    return 0


def _dispatch_results(arguments: Sequence[str]) -> int:
    from .inspection_cli import run_results

    return run_results(arguments)
