"""Public argument parsing and presentation for cached validation inspection."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any, Sequence

from validation.inspection_store import InspectionError

from .context import resolve_log
from .inspection_queries import VIEWS, Query, inspect_result
from .inspection_views import print_view


def run_results(arguments: Sequence[str]) -> int:
    """Dispatch an explicitly selected read-only inspection view or full export."""
    parser = argparse.ArgumentParser(
        prog="log results",
        description="Inspect retained validation or diagnostics without reevaluation.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    for action in (
        "list",
        "show",
        "finding",
        "batch",
        "command",
        "artifact",
        "collection",
        "value",
        "export",
    ):
        child = actions.add_parser(action)
        child.add_argument("--path", type=Path, required=True)
        child.add_argument("--format", choices=("text", "json"), default="text")
        _selectors(child, action)
    args = parser.parse_args(arguments)
    log = resolve_log(args.path)
    values = vars(args).copy()
    values.pop("path")
    output_format = values.pop("format")
    query = Query(**values)
    result = inspect_result(log.root, query)
    if output_format == "text" and result.get("next_cursor"):
        result["next_command"] = _next_command(args, result)
        result.pop("next_cursor")
    if args.action == "export":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print_view(result, output_format)
    return 0


def _next_command(args: argparse.Namespace, result: dict[str, Any]) -> str:
    """Pin text continuations to this result and preserve its query selectors."""
    values = vars(args).copy()
    action = values.pop("action")
    values.pop("latest", None)
    if "result_id" in values:
        values.pop("result_id")
        values["id"] = result["result_id"]
    values["cursor"] = result["next_cursor"]
    command = ["log", "results", action]
    for key, value in values.items():
        if value is None:
            continue
        if key == "entity":
            key = "ref" if action == "value" else action
        command.extend(("--" + key.replace("_", "-"), str(value)))
    return shlex.join(command)


def _selectors(parser: argparse.ArgumentParser, action: str) -> None:
    if action in {"list", "show"}:
        parser.add_argument("--kind", choices=("full", "batch", "diagnostic"))
        for flag in ("entry", "validation"):
            parser.add_argument("--" + flag)
        batch = parser.add_mutually_exclusive_group()
        batch.add_argument("--chain")
        batch.add_argument("--batch")
    if action == "show":
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument("--id", dest="result_id")
        selection.add_argument("--latest", action="store_true")
        parser.add_argument("--view", choices=VIEWS, default="summary")
        parser.add_argument("--code")
    elif action != "list":
        parser.add_argument("--id", dest="result_id", required=True)
    if action not in {"list", "show", "export"}:
        parser.add_argument(
            "--" + ("ref" if action == "value" else action),
            dest="entity",
            required=True,
        )
    if action != "export":
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--cursor")


def print_producer(value: dict[str, Any], path: Path, output_format: str) -> None:
    """Preserve legacy JSON; default text exposes cached IDs and query commands."""
    identity = value.pop("_inspection_id", None)
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    log_root = path.absolute()
    if identity and "report" not in value:
        try:
            print_view(inspect_result(log_root, Query(result_id=identity)), "text")
        except InspectionError as error:
            print(f"Status: {value['status']}; cached view unavailable: {error}")
    else:
        print(value.get("report", f"Status: {value['status']}"))
    if identity:
        command = (
            f"log results show --path {shlex.quote(str(log_root))} --id {identity}"
        )
        print(
            f"Result: {identity}\nInspect: {command}\n"
            f"Findings: {command} --view findings"
        )
        if value.get("batch_id"):
            print(
                f"Batch: log results batch --path {shlex.quote(str(log_root))} "
                f"--id {identity} --batch {shlex.quote(value['batch_id'])}"
            )
    else:
        print("Result not cached; no result ID.")


def print_findings(value: dict[str, Any], log_root: Path, output_format: str) -> None:
    """Offer bounded legacy-publication text; cached result queries own details."""
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    # Publications predating the cache still support concise orientation.
    compact = {
        key: item for key, item in value.items() if key not in {"batch", "chains"}
    }
    if "chains" in value:
        compact["chains"] = [
            {
                key: item
                for key, item in chain.items()
                if key in {"entry", "chain_id", "finding_count", "codes", "counts"}
            }
            for chain in value["chains"][:20]
        ]
    if "batch" in value:
        batch = value["batch"]
        compact["batch"] = {
            "entry": batch["entry"],
            "chain_id": batch["chain_id"],
            "commands": len(batch.get("commands", [])),
            "findings": len(batch.get("findings", [])),
        }
    if len(value.get("chains", [])) > 20:
        compact["omitted_chains"] = len(value["chains"]) - 20
        compact["inspect_remaining"] = (
            f"log results show --path {shlex.quote(str(log_root))} "
            "--latest --kind full --view chains; for older uncached publications, "
            "narrow findings list with --entry or --code"
        )
    compact["log"] = str(log_root)
    print_view(compact, "text")
