#!/usr/bin/env python3
"""Migrate one Phase 8 research-log validation bundle to the final layout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validation.layout_migration import inventory, migrate_layout, project_old_layout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path, nargs="+")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    results = []
    for summary in args.summary:
        if summary.is_absolute():
            summary_path = summary.resolve()
            relative_summary = summary_path.relative_to(project_root).as_posix()
        else:
            relative_summary = summary.as_posix()
            summary_path = project_root / summary
        output_dir = summary_path.with_suffix("")
        if args.inventory_only:
            result = inventory(output_dir, relative_summary)
        elif args.dry_run:
            projection = project_old_layout(output_dir, relative_summary)
            result = {
                "summary": relative_summary,
                "row_counts": projection.new_manifest["row_counts"],
                "manifest_sha256": hashlib.sha256(
                    json.dumps(
                        projection.new_manifest,
                        sort_keys=True,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8")
                    + b"\n"
                ).hexdigest(),
                "subject_count": sum(
                    len(entries) for entries in projection.index["subjects"].values()
                ),
                "continuation": projection.new_manifest["continuation"],
            }
        else:
            project_old_layout(output_dir, relative_summary)
            result = migrate_layout(output_dir, relative_summary)
        results.append(result)
    payload: object = results[0] if len(results) == 1 else results
    rendered = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
