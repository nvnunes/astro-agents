#!/usr/bin/env python3
"""Prevent known research-logging complexity debt from growing."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

RULES = "C901,PLR0911,PLR0912,PLR0913,PLR0915"
SCORE_RE = re.compile(r"\((\d+) >")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ruff", type=Path, default=Path(".conda/bin/ruff"))
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("skills/research-logging/tests/complexity-baseline.json"),
    )
    return parser.parse_args()


def _qualified_function(path: Path, line: int) -> str:
    """Return the narrowest function or method containing a Ruff finding."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    candidates: list[tuple[int, str]] = []

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        nested = parents
        if isinstance(node, ast.ClassDef):
            nested = (*parents, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= line <= end:
                qualified = ".".join((*parents, node.name))
                candidates.append((end - node.lineno, qualified))
            nested = (*parents, node.name)
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(tree)
    return min(candidates)[1] if candidates else "<module>"


def _finding_record(
    finding: dict[str, object], project_root: Path
) -> tuple[str, int]:
    path = Path(str(finding["filename"])).resolve()
    relative = path.relative_to(project_root.resolve()).as_posix()
    location = finding["location"]
    if not isinstance(location, dict) or not isinstance(location.get("row"), int):
        raise ValueError("Ruff finding has no source row")
    function = _qualified_function(path, location["row"])
    code = str(finding["code"])
    score_match = SCORE_RE.search(str(finding["message"]))
    score = int(score_match.group(1)) if score_match else 1
    return f"{relative}:{function}:{code}", score


def _ratchet_issues(
    findings: list[dict[str, object]],
    expected: dict[str, int],
    project_root: Path,
) -> tuple[list[str], dict[str, int]]:
    issues: list[str] = []
    current: dict[str, int] = {}
    for finding in findings:
        try:
            key, score = _finding_record(finding, project_root)
        except (OSError, SyntaxError, ValueError) as exc:
            issues.append(f"cannot identify complexity finding: {exc}")
            continue
        current[key] = max(score, current.get(key, 0))
    for key, score in sorted(current.items()):
        if key not in expected:
            issues.append(f"new complexity finding: {key} ({score})")
        elif score > expected[key]:
            issues.append(f"complexity finding grew: {key} ({score} > {expected[key]})")
    return issues, current


def main() -> int:
    args = _arguments()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    completed = subprocess.run(
        [
            str(args.ruff),
            "check",
            "--select",
            RULES,
            "--output-format",
            "json",
            "skills/research-logging/scripts",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        sys.stderr.write(completed.stderr)
        return completed.returncode
    findings = json.loads(completed.stdout)
    issues, current = _ratchet_issues(findings, baseline["findings"], Path.cwd())
    if issues:
        sys.stderr.write("\n".join(issues) + "\n")
        return 1
    print(
        f"complexity ratchet passed: {len(findings)} advisory findings; "
        f"{sum(key.endswith(':C901') for key in current)} complex functions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
