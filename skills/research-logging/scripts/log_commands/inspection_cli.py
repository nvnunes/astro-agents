"""Public argument parsing and presentation for cached validation inspection."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any, Sequence, cast

from validation.operation_state import operation_lock

from .context import resolve_log
from .inspection_queries import VIEWS, InspectionError, Query, inspect_result
from .inspection_views import print_view
from .model import ActionError


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
        "export",
    ):
        child = actions.add_parser(action)
        child.add_argument("--path", type=Path, required=True)
        child.add_argument("--format", choices=("text", "json"), default="text")
        _selectors(child, action)
    render = actions.add_parser("render")
    render.add_argument("--path", type=Path, required=True)
    render.add_argument("--kind", choices=("validation", "reproduction"), required=True)
    args = parser.parse_args(arguments)
    log = resolve_log(args.path)
    if args.action == "render":
        _render(log, args.kind)
        return 0
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


def _render(log, kind: str) -> None:
    """Regenerate one derived Markdown report without evaluation or execution."""

    from research_log_result_store import (
        record_report_materialization,
        result_generation,
        results_lock,
    )

    from .storage import atomic_write_texts

    if kind == "validation":
        from validation.report import compose_validation_report
        from validation.result_storage import load_validation_report_projection

        with operation_lock(log.root, "log.lock", mode="exclusive"):
            with results_lock(log.root):
                projection = load_validation_report_projection(log.root)
                identity = (
                    f"validation result {projection.stored.result_id} generation "
                    f"{projection.stored.generation}"
                )
                try:
                    report = compose_validation_report(
                        projection.record,
                        context=cast(Any, projection.context),
                        groups=cast(Any, projection.groups),
                    )
                except Exception as error:
                    raise ActionError(
                        "results.report.render_failed", f"{identity}: {error}"
                    ) from error
                try:
                    atomic_write_texts({log.root / "validation.md": report})
                    record_report_materialization(
                        log.root,
                        kind,
                        report.encode(),
                        expected_generation=projection.stored.generation,
                    )
                except Exception as error:
                    raise ActionError(
                        "results.report.write_failed", f"{identity}: {error}"
                    ) from error
        return
    with operation_lock(log.root, "log.lock", mode="exclusive"):
        expected_generation = result_generation(log.root, kind)
        try:
            report = _reproduction_render_input(log)
        except Exception as error:
            raise ActionError(
                "results.report.render_failed",
                f"reproduction generation {expected_generation}: {error}",
            ) from error
        with results_lock(log.root):
            identity = f"reproduction generation {expected_generation}"
            if result_generation(log.root, kind) != expected_generation:
                raise ActionError(
                    "results.report.write_failed",
                    f"{identity} changed before report replacement",
                )
            try:
                atomic_write_texts({log.root / "reproduction.md": report})
                record_report_materialization(
                    log.root,
                    kind,
                    report.encode(),
                    expected_generation=expected_generation,
                )
            except Exception as error:
                raise ActionError(
                    "results.report.write_failed", f"{identity}: {error}"
                ) from error


def _reproduction_render_input(log) -> str:
    """Compose reproduction Markdown before acquiring the result publication lock."""

    from validation.human_projection import load_report_context

    from .reproduction_planner import project_reproduction_state
    from .reproduction_result_storage import load_reproduction_report_projection
    from .reproduction_results import (
        compose_reproduction_report,
        project_current_results,
    )

    results = load_reproduction_report_projection(log.root / ".cache/results.sqlite")
    projected, currentness = project_current_results(
        results, project_reproduction_state(log)
    )
    return compose_reproduction_report(
        projected,
        context=load_report_context(log.summary),
        currentness=currentness,
        folder_links_from=Path.cwd(),
    )


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
            key = action
        command.extend(("--" + key.replace("_", "-"), str(value)))
    return shlex.join(command)


def _selectors(parser: argparse.ArgumentParser, action: str) -> None:
    if action in {"list", "show"}:
        parser.add_argument("--kind", choices=("full", "entry", "diagnostic"))
        parser.add_argument("--entry")
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
            "--" + action,
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
