"""Thin canonical saved-reproduction inspection routes; no execution ownership."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .context import resolve_log
from .model import ActionError
from .reproduction_domain import ArtifactRef, ExecutionRef, ReproductionDomainError
from .reproduction_inspection import (
    ARTIFACT_DETAIL_SECTIONS,
    COMMAND_DETAIL_SECTIONS,
    EmptyInspection,
    ListSelection,
    SavedInspection,
    artifact_detail,
    bounded_response,
    command_detail,
    inspection_summary,
    list_saved,
    load_inspection,
    render_saved_summary,
)
from .reproduction_root_summary import render_root_summary, root_summary


def _saved_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--format", choices=("text", "json"), default="text")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="log reproduce")
    actions = parser.add_subparsers(dest="action", required=True)
    show = actions.add_parser("show", help="Show one recorded reproduction target")
    targets = show.add_mutually_exclusive_group(required=True)
    targets.add_argument("--path", type=Path)
    targets.add_argument("--root", type=Path)
    show.add_argument("--run-id")
    show.add_argument("--format", choices=("text", "json"), default="text")
    listing = actions.add_parser("list", help="List saved commands or artifacts")
    listing.add_argument("kind", choices=("commands", "artifacts"))
    _saved_arguments(listing)
    listing.add_argument("--entry")
    listing.add_argument("--cid")
    listing.add_argument("--status")
    listing.add_argument("--reason")
    listing.add_argument("--cursor")
    detail = actions.add_parser("detail", help="Inspect retained diagnosis and context")
    items = detail.add_subparsers(dest="kind", required=True)
    command = items.add_parser("command")
    _saved_arguments(command)
    command.add_argument("--entry", required=True)
    command.add_argument("--cid", required=True)
    command.add_argument("--execution-id", required=True)
    command.add_argument("--section", choices=COMMAND_DETAIL_SECTIONS)
    command.add_argument("--cursor")
    artifact = items.add_parser("artifact")
    _saved_arguments(artifact)
    artifact.add_argument("--entry", required=True)
    artifact.add_argument("--artifact", required=True)
    artifact.add_argument("--section", choices=ARTIFACT_DETAIL_SECTIONS)
    artifact.add_argument("--cursor")
    rendering = actions.add_parser(
        "render", help="Recover the compact saved summary report"
    )
    rendering.add_argument("--path", type=Path, required=True)
    return parser


def _detail(
    args: argparse.Namespace, inspection: SavedInspection | EmptyInspection
) -> dict[str, object]:
    if isinstance(inspection, EmptyInspection):
        raise ActionError(
            "reproduction.item.unknown", "Confirmed empty target has no items"
        )
    try:
        if args.kind == "command":
            return command_detail(
                inspection,
                ExecutionRef(args.entry, args.cid, args.execution_id),
                section=args.section,
                cursor=args.cursor,
                format=args.format,
            )
        return artifact_detail(
            inspection,
            ArtifactRef(args.entry, args.artifact),
            section=args.section,
            cursor=args.cursor,
            format=args.format,
        )
    except ReproductionDomainError as error:
        raise ActionError("reproduction.selector.invalid", str(error)) from error


def dispatch(arguments: Sequence[str]) -> int:
    """Parse saved selectors and print bounded facts without triggering execution."""

    args = _parser().parse_args(arguments)
    if args.action == "render":
        from .reproduction_saved_report import render_saved_report

        print(render_saved_report(resolve_log(args.path)), end="")
        return 0
    if args.action == "show":
        return _show(args)
    log = resolve_log(args.path)
    inspection = load_inspection(log, args.run_id)
    if args.action == "list":
        value = list_saved(
            inspection,
            ListSelection(
                args.kind, args.entry, args.cid, args.status, args.reason, args.format
            ),
            args.cursor,
        )
    else:
        value = _detail(args, inspection)
    if args.format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        from .reproduction_inspection_text import render_inspection

        print(render_inspection(value), end="")
    return 0


def _show(args: argparse.Namespace) -> int:
    if args.root is not None:
        if args.run_id is not None:
            raise ActionError(
                "reproduction.selector.invalid", "--run-id requires --path"
            )
        value = root_summary(args.root)
        print(
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            if args.format == "json"
            else render_root_summary(value),
            end="\n" if args.format == "json" else "",
        )
        return 3 if value["unavailable"] else 0
    log = resolve_log(args.path)
    try:
        inspection = load_inspection(log, args.run_id)
    except ActionError as error:
        if error.code != "reproduction.results.missing":
            raise
        if args.format == "json":
            print(
                json.dumps(
                    {"log": str(log.root), "available": False, "code": error.code}
                )
            )
        else:
            print(
                f"Log: {log.root.name}\nSaved results: Unavailable\n"
                "Reproduce this log to create saved results."
            )
        return 3
    if args.format == "text":
        print(render_saved_summary(inspection), end="")
    else:
        print(
            json.dumps(
                bounded_response(inspection_summary(inspection)),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0
