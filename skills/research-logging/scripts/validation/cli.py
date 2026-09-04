"""Public command-line contract for mechanical research-log validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence, cast

from .controller import ValidationControllerError, ValidationRequest, validate
from .discovery import discover_summaries

COMPLETED_STATUSES = frozenset(
    {"complete_clear", "complete_findings", "unsupported_metadata"}
)
CLI_RESULT_SCHEMA = "research-log-validation-cli-result/1"


def build_parser() -> argparse.ArgumentParser:
    """Build the supported validation and summary-discovery operations."""

    parser = argparse.ArgumentParser(
        description="Discover or mechanically validate maintained research logs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser(
        "discover", help="discover maintained summaries below one project root"
    )
    discover_parser.add_argument("--root", required=True, type=Path)
    validate_parser = subparsers.add_parser(
        "validate", help="mechanically validate one maintained summary"
    )
    validate_parser.add_argument("--summary", required=True, type=Path)
    validate_parser.add_argument("--date")
    validate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and report without publishing generated files",
    )
    validate_parser.add_argument(
        "--recompute",
        action="store_true",
        help="bypass validation and fingerprint reuse for this evaluation",
    )
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    return run_validate(
        args.summary,
        result_date=args.date,
        dry_run=args.dry_run,
        recompute=args.recompute,
    )


def evaluate_validation(
    summary: Path,
    *,
    result_date: str | None = None,
    dry_run: bool = False,
    recompute: bool = False,
) -> dict[str, object]:
    """Return the public result for one validation request."""

    result = validate(
        ValidationRequest(
            summary,
            result_date=result_date,
            publish=not dry_run,
            recompute=recompute,
        )
    )
    return _cli_result(result)


def run_validate(
    summary: Path,
    *,
    result_date: str | None = None,
    dry_run: bool = False,
    recompute: bool = False,
) -> int:
    """Print and classify one public validation result."""

    result = evaluate_validation(
        summary,
        result_date=result_date,
        dry_run=dry_run,
        recompute=recompute,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in COMPLETED_STATUSES else 3


def _cli_result(result: dict[str, object]) -> dict[str, object]:
    """Return a bounded CLI envelope for a completed published evaluation."""

    if not result.get("published") or not isinstance(result.get("record"), dict):
        return result
    record = cast(Mapping[str, object], result["record"])
    summary = Path(str(result["summary"]))
    log_root = summary.with_suffix("")
    return {
        "generated": {
            "human": (log_root / "validation.md").as_posix(),
            "mechanical": (log_root / "validation/mechanical.json").as_posix(),
        },
        "metrics": result.get("metrics", {}),
        "published": True,
        "result_date": record.get("result_date"),
        "rules_version": record.get("rules_version"),
        "schema": CLI_RESULT_SCHEMA,
        "scopes": record.get("scopes", []),
        "status": result["status"],
        "summary": result["summary"],
    }


def _run_discover(args: argparse.Namespace) -> int:
    return run_discover(args.root)


def run_discover(root: Path) -> int:
    """Print the public bounded maintained-summary inventory."""

    print(json.dumps(discover_summaries(root), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run mechanical validation and return its process exit status."""

    args = build_parser().parse_args(argv)
    try:
        if args.command == "discover":
            return _run_discover(args)
        return _run_validate(args)
    except (OSError, ValidationControllerError, ValueError) as exc:
        print(
            f"research_log_validation: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
