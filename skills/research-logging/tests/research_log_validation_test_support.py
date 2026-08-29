from __future__ import annotations

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
    write(
        entry_root / "data.csv",
        "name,type,location\ncatalog,csv,https://example.test/catalog.csv\n",
    )
    write(
        entry_root / "evidence.json",
        json.dumps(
            {
                "schema": "research-log-evidence/v2",
                "records": [
                    {
                        "id": "success-rate",
                        "document": "entries/2026-08-29-e001-study/e001.md",
                        "kind": "statistic",
                        "sources": [
                            {
                                "source": "data/results.csv",
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
        "`Question:`\n\nWhat is the success rate?\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "./pyrun scripts/model.py --catalog '<catalog>' "
        f"--{output_option} data/results.csv\n"
        "```\n"
        "<!-- command type = model -->\n\n"
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
