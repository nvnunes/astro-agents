#!/usr/bin/env python3
"""Apply the reviewed one-time validation-publication ownership migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validation.migration import migrate_repository
from validation.runtime import lint_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not all(
        isinstance(item, str) for item in manifest
    ):
        parser.error("manifest must be a JSON list of summary paths")
    print(
        json.dumps(
            migrate_repository(args.project_root, manifest, lint_records),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
