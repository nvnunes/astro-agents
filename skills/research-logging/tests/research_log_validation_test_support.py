from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research_log_validation.py"
sys.path.insert(0, str(SCRIPT.parent))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def mechanical_log(
    root: Path, *, output_option: str = "output-data"
) -> tuple[Path, Path]:
    """Create one complete active-format mechanical-validation fixture."""

    (root / ".git").mkdir(exist_ok=True)
    summary = root / "docs" / "study.md"
    log_root = root / "docs" / "study"
    entry_root = log_root / "entries" / "2026-08-29-e001-study"
    entry = entry_root / "e001.md"
    write(
        summary,
        "# Study\n\n"
        "Validation: [latest completed report](study/validation.md)\n\n"
        "## Summary\n\n"
        "- Success rate: `67.6%`"
        "<!-- ref entry = e001; eid = success-rate -->.\n\n"
        "## Entries\n\n"
        "- [Study trial](study/entries/2026-08-29-e001-study/e001.md)\n",
    )
    write(entry_root / "scripts" / "model.py", "# retained model\n")
    write(entry_root / "data" / "results.csv", "success_rate\n0.676\n")
    results_digest = hashlib.sha256(
        (entry_root / "data" / "results.csv").read_bytes()
    ).hexdigest()
    write(
        entry_root / "data.json",
        json.dumps(
            {
                "schema": "research-log-data/v2",
                "inputs": [
                    {
                        "name": "catalog",
                        "kind": "file",
                        "location": "https://example.test/catalog.csv",
                        "fingerprint": {
                            "algorithm": "immutable-source",
                            "value": "fixture-catalog/v1",
                        },
                        "external": {
                            "source": "test fixture",
                            "identity": "fixture-catalog/v1",
                        },
                    },
                    {
                        "name": "results",
                        "kind": "file",
                        "location": "data/results.csv",
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": results_digest,
                        },
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry_root / "evidence.json",
        json.dumps(
            {
                "schema": "research-log-evidence/v3",
                "records": [
                    {
                        "id": "success-rate",
                        "document": "entries/2026-08-29-e001-study/e001.md",
                        "kind": "statistic",
                        "sources": [
                            {
                                "source": "<results>",
                                "locator": {"select": [["success_rate"]]},
                            }
                        ],
                        "transformation": {
                            "form": "percentage",
                            "source": {"input": 0, "item": 0},
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    write(
        entry,
        "# Entry e001\n\n"
        "## Trial\n\n"
        "`Background:`\n\nWhat is the success rate?\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/model.py --catalog '<catalog>' "
        f"--{output_option} data/results.csv\n"
        "```\n\n"
        "`Results:`\n\n"
        "The success rate was `67.6%`<!-- eid:success-rate -->.\n",
    )
    return summary, entry


__all__ = [
    "Any",
    "Path",
    "SCRIPT",
    "importlib",
    "json",
    "mechanical_log",
    "mock",
    "subprocess",
    "sys",
    "tempfile",
    "unittest",
    "write",
]
