"""Public command-line contract for mechanical research-log validation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .controller import ValidationControllerError, ValidationRequest, validate
from .discovery import discover_summaries

COMPLETED_STATUSES = frozenset(
    {"complete_clear", "complete_findings", "unsupported_metadata"}
)


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
        "--jobs", type=int, default=min(32, (os.cpu_count() or 1) + 4)
    )
    validate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and report without publishing generated files",
    )
    validate_parser.add_argument(
        "--recompute",
        action="store_true",
        help="ignore the existing mechanical cache and rebuild it after evaluation",
    )
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    result = validate(
        ValidationRequest(
            args.summary,
            result_date=args.date,
            jobs=args.jobs,
            publish=not args.dry_run,
            recompute=args.recompute,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in COMPLETED_STATUSES else 3


def _run_discover(args: argparse.Namespace) -> int:
    print(json.dumps(discover_summaries(args.root), ensure_ascii=False, sort_keys=True))
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
