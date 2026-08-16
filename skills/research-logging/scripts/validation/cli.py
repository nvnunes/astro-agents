"""Public command-line contract for target research-log validation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .contracts import ValidationToolError
from .controller import ValidationRequest, validate


def build_parser() -> argparse.ArgumentParser:
    """Build the single supported validation operation."""

    parser = argparse.ArgumentParser(
        description="Validate one maintained research-log summary."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser(
        "validate", help="validate one maintained summary"
    )
    validate_parser.add_argument("--summary", required=True, type=Path)
    validate_parser.add_argument("--decisions", type=Path)
    validate_parser.add_argument("--date")
    validate_parser.add_argument(
        "--mode", choices=("standard", "reproduction"), default="standard"
    )
    validate_parser.add_argument(
        "--jobs", type=int, default=min(32, (os.cpu_count() or 1) + 4)
    )
    validate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run validation and report the result without publishing artifacts",
    )
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    result = validate(
        ValidationRequest(
            args.summary,
            decision_file=args.decisions,
            result_date=args.date,
            jobs=args.jobs,
            publish=not args.dry_run,
            mode=args.mode,
        )
    )
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "error" else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run target validation and return its exit status."""

    args = build_parser().parse_args(argv)
    try:
        return _run_validate(args)
    except (OSError, ValidationToolError) as exc:
        print(f"research_log_validation: {exc}", file=sys.stderr)
        return 2
