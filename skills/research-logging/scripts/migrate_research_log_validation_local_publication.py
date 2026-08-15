#!/usr/bin/env python3
"""Dry-run or apply one v43 local-publication migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validation.local_migration import cleanup_repository_artifacts, migrate_log


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", nargs="?", type=Path)
    parser.add_argument(
        "--cleanup-project",
        type=Path,
        help="audit or remove obsolete aggregate and repository lock artifacts",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="publish the planned migration; the default is a no-write dry run",
    )
    args = parser.parse_args()
    if (args.summary is None) == (args.cleanup_project is None):
        parser.error("provide either a summary or --cleanup-project")
    result = (
        migrate_log(args.summary, apply=args.apply)
        if args.summary is not None
        else cleanup_repository_artifacts(args.cleanup_project, apply=args.apply)
    )
    print(
        json.dumps(
            result, indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
