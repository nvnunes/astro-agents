from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

__all__ = [
    "ADJUDICATION",
    "CLI",
    "CONTRACTS",
    "DECISIONS",
    "DISCOVERY",
    "EVIDENCE",
    "GRAPH",
    "GRAPH_ADAPTER",
    "GRAPH_QUERIES",
    "INVENTORY",
    "IDENTITIES",
    "REPORT",
    "RECORDS",
    "RENDER",
    "REVIEW_INDEX",
    "RUNTIME",
    "SCAN",
    "SCRIPT",
    "Any",
    "Path",
    "adjudication_for",
    "complete_adjudication",
    "eligible_producer_identity",
    "hashlib",
    "identity_ending",
    "importlib",
    "json",
    "make_log",
    "mock",
    "prepare_adjudication",
    "re",
    "subprocess",
    "sys",
    "tempfile",
    "unittest",
    "write",
]

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research_log_validation.py"
sys.path.insert(0, str(SCRIPT.parent))
GRAPH = importlib.import_module("validation.graph")
GRAPH_ADAPTER = importlib.import_module("validation.graph_adapter")
GRAPH_QUERIES = importlib.import_module("validation.graph_queries")
ADJUDICATION = importlib.import_module("validation.adjudication")
CLI = importlib.import_module("validation.cli")
CONTRACTS = importlib.import_module("validation.contracts")
DECISIONS = importlib.import_module("validation.decisions")
DISCOVERY = importlib.import_module("validation.discovery")
EVIDENCE = importlib.import_module("validation.evidence")
INVENTORY = importlib.import_module("validation.inventory")
IDENTITIES = importlib.import_module("validation.identities")
REPORT = importlib.import_module("validation.report")
RUNTIME = importlib.import_module("validation.runtime")
RENDER = importlib.import_module("validation.render")
REVIEW_INDEX = importlib.import_module("validation.review_index")
SCAN = importlib.import_module("validation.scan")
RECORDS = importlib.import_module("validation.records")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def prepare_adjudication(
    scan: dict[str, Any],
    date: str,
    rules_version: str,
    mode: str = "standard",
) -> dict[str, Any]:
    """Prepare fixture adjudication through the public runtime contract."""

    if rules_version != RUNTIME.RULES_VERSION:
        raise CONTRACTS.ValidationToolError("fixture rules version is not current")
    return RUNTIME.prepare_adjudication_record(scan, date, mode)


def make_log(root: Path) -> tuple[Path, Path]:
    (root / ".git").mkdir()
    summary = root / "docs" / "mini.md"
    entry = (
        root
        / "docs"
        / "mini"
        / "entries"
        / "2026-08-07-e001-validation-fixture"
        / "e001.md"
    )
    relative_entry = "mini/entries/2026-08-07-e001-validation-fixture/e001.md"
    write(
        summary,
        "# Mini Log\n\n"
        "## Summary\n\n"
        f"- The retained value is `1.0` ([e001]({relative_entry})).\n\n"
        "## Entries\n\n"
        f"- [e001]({relative_entry})\n",
    )
    write(
        entry,
        "# 2026-08-07: Validation Fixture\n\n"
        "## Results\n\n"
        "`Steps:`\n\n"
        "```bash\n"
        "MPLCONFIGDIR=/tmp/mini-mpl python scripts/no_execute.py --input <input_csv> "
        "--direct-input data/direct.csv "
        "--working-parent data/workspace "
        "--output data/command-only.csv\n"
        "python scripts/no_execute.py --retained-output data/output.csv "
        "--collection-output data/collection\n"
        "python <log>/scripts/shared.py --flag\n"
        "```\n\n"
        "`Results:`\n\n"
        "The retained value is `1.0` in [output](data/output.csv).\n\n"
        "Build `v12`, `seed=2026`, `10PH`, and `2026-08-07` are not statistics.\n\n"
        "![invalid plot](data/invalid.png)\n\n"
        "A broken [artifact](data/missing.csv) is also recorded.\n\n"
        "The retained [collection](data/collection) is available for inspection.\n\n"
        "name | value\n"
        "--- | ---:\n"
        "result | `1.0`\n\n"
        "```text\n"
        "not | a table\n"
        "--- | ---\n"
        "metric | 1.0\n"
        "```\n\n"
        "External context uses @missing-source.\n\n"
        "`Validation:`\n\n"
        "Retain disconnected fixture material for validator contract tests.\n",
    )
    write(entry.parent / "data" / "output.csv", "name,value\nresult,1.0\n")
    write(
        entry.parent / "evidence.csv",
        "entry,section,kind,evidence,sources,transformation\n"
        "e001,Results,statistic,1.0,data/output.csv :: value,\n"
        'e001,Results,table,"name,value",data/output.csv,\n'
        "e001,Results,output,not | a table,data/output.csv,\n",
    )
    write(
        root / "docs" / "mini" / "evidence.csv",
        "statistic,entry,section,transformation\n1.0,e001,Results,\n",
    )
    write(entry.parent / "data" / "invalid.png", "not a png\n")
    write(entry.parent / "data" / "command-only.csv", "name,value\nresult,1.0\n")
    write(entry.parent / "data" / "direct.csv", "name,value\ninput,2.0\n")
    write(entry.parent / "data" / "workspace" / "unrelated.txt", "temporary\n")
    write(entry.parent / "data" / "collection" / "a.txt", "a\n")
    write(entry.parent / "data" / "collection" / "b.txt", "b\n")
    write(
        entry.parent / "scripts" / "no_execute.py",
        "from pathlib import Path\n"
        "Path('EXECUTED').write_text('executed', encoding='utf-8')\n",
    )
    write(root / "docs" / "mini" / "scripts" / "shared.py", "value = 1\n")
    write(
        entry.parent / "data.csv",
        "name,type,location\n"
        "input_csv,CSV,data/output.csv\n"
        "input_csv,CSV,data/other.csv\n",
    )
    return summary, entry


def identity_ending(scan: dict, suffix: str) -> str:
    matches = [path for path in scan["resolved_paths"] if path.endswith(suffix)]
    if len(matches) != 1:
        raise AssertionError(f"expected one identity ending {suffix!r}, got {matches}")
    return matches[0]


def eligible_producer_identity(
    scan: dict[str, Any], entry_id: str, target: str, ordinal: int = 1
) -> str:
    session = REVIEW_INDEX.ReviewQuerySession(
        REVIEW_INDEX.ReviewContextIndex.build(scan)
    )
    eligible = session.eligible_candidate_invocations(entry_id, target, [])
    if len(eligible) < ordinal:
        raise AssertionError(
            f"expected eligible producer {ordinal} for {target}, got {len(eligible)}"
        )
    return eligible[ordinal - 1].key


def adjudication_for(scan: dict, entry: Path) -> dict:
    summary_item = scan["summary_items"][0]["selector"]
    output = identity_ending(scan, "data/output.csv")
    collection = identity_ending(scan, "data/collection")
    entry_identity = identity_ending(scan, "/e001.md")
    date = "2026-08-07"
    support_line, support_text = next(
        (number, line)
        for number, line in enumerate(entry.read_text(encoding="utf-8").splitlines(), 1)
        if "retained value is `1.0`" in line
    )
    scanned_entry = next(item for item in scan["entries"] if item["id"] == "e001")
    producer_session = REVIEW_INDEX.ReviewQuerySession(
        REVIEW_INDEX.ReviewContextIndex.build(scan)
    )
    eligible = producer_session.eligible_candidate_invocations(
        "e001", output, []
    )
    producer = eligible[0].key if eligible else None
    entry_orphans = scanned_entry.get("orphan_inventory", [])
    note_sha = scanned_entry["validation_notes"][0]["sha256"]
    adjudication = {
        "schema_version": RUNTIME.ADJUDICATION_SCHEMA_VERSION,
        "validation_rules_version": RUNTIME.RULES_VERSION,
        "log": scan["summary"],
        "requested_scope": "complete standard scope",
        "scope": {"summary": True, "entries": list(scan["entry_order"])},
        "date": date,
        "mode": "standard",
        "summary": [
            {
                "source_item": scan["summary_items"][0]["identity"],
                "item": summary_item,
                "entries": ["e001"],
                "sections": ["Results"],
                "provenance": date,
                "support_reviewed": True,
                "support_evidence": [
                    {
                        "entry": "e001",
                        "section": "Results",
                        "lines": str(support_line),
                        "text": support_text,
                    }
                ],
                "dependencies": [
                    {"path": scan["summary"], "role": "summary"},
                    {"path": entry_identity, "role": "supporting-entry"},
                ],
                "findings": [],
            },
        ],
        "entries": [
            {
                "id": "e001",
                "title": "2026-08-07: Validation Fixture",
                "path": entry_identity,
                "scope_reconciled": True,
                "scope_kind": "entry",
                "scope_paths": [entry_identity],
                "orphan_items": [
                    {
                        "identity": item["identity"],
                        "decision": "accepted",
                        "basis": f"validation-note:{note_sha}",
                    }
                    for item in entry_orphans
                ],
                "targets": [
                    {
                        "target": output,
                        "sections": ["Results"],
                        "integrity": date,
                        "provenance": date,
                        "reproducibility": "-",
                        "notes": "-",
                        "dependencies": [
                            {"path": entry_identity, "role": "entry"},
                            {"path": output, "role": "target"},
                        ],
                        **(
                            {"producer_invocation": producer}
                            if producer is not None
                            else {}
                        ),
                        "findings": [],
                    },
                    {
                        "target": collection,
                        "sections": ["Results"],
                        "integrity": date,
                        "provenance": date,
                        "reproducibility": "-",
                        "notes": "selected member a.txt",
                        "dependencies": [
                            {"path": entry_identity, "role": "entry"},
                            {
                                "path": collection,
                                "role": "target",
                                "members": ["a.txt"],
                            },
                        ],
                        **(
                            {"producer_invocation": producer}
                            if producer is not None
                            else {}
                        ),
                        "findings": [],
                    },
                    {
                        "target": "Unprovenanced: displayed results table",
                        "sections": ["Results"],
                        "integrity": "FAIL",
                        "provenance": "FAIL",
                        "reproducibility": "N/A",
                        "notes": "-",
                        "dependencies": [{"path": entry_identity, "role": "entry"}],
                        "findings": [
                            {
                                "check": "Integrity",
                                "finding": (
                                    "Expected a retained artifact; none was identified."
                                ),
                            },
                            {
                                "check": "Provenance",
                                "finding": (
                                    "Expected a generating workflow; none was recorded."
                                ),
                            },
                        ],
                    },
                ],
            }
        ],
        "review_queue": [],
    }
    by_id = {item["id"]: item for item in scan["entries"]}
    for scope_id in scan["entry_order"]:
        if scope_id == "e001":
            continue
        scanned = by_id[scope_id]
        adjudication["entries"].append(
            {
                "id": scope_id,
                "title": scanned["title"],
                "path": scanned["path"],
                "scope_reconciled": True,
                "scope_kind": scanned.get("scope_kind", "entry"),
                "scope_paths": scanned.get("scope_paths", [scanned["path"]]),
                "orphan_items": [
                    {
                        "identity": item["identity"],
                        "decision": "accepted",
                        "basis": f"validation-note:{note_sha}",
                    }
                    for item in scanned.get("orphan_inventory", [])
                ],
                "targets": [],
            }
        )
    return adjudication


def complete_adjudication(scan: dict) -> dict:
    prepared = prepare_adjudication(scan, "2026-08-07", RUNTIME.RULES_VERSION)
    output = identity_ending(scan, "data/output.csv")
    invalid = identity_ending(scan, "data/invalid.png")
    collection = identity_ending(scan, "data/collection")
    output_producer = eligible_producer_identity(scan, "e001", output)
    collection_producer = eligible_producer_identity(scan, "e001", collection)
    missing = next(
        item["identity"]
        for item in prepared["review_queue"]
        if item["identity"].endswith("data/missing.csv")
    )
    orphan_actions = []
    for item in prepared["review_queue"]:
        if item["kind"] != "orphan_candidates":
            continue
        note_sha = item["validation_notes"][0]["sha256"]
        orphan_actions.append(
            {
                "match": {
                    "kind": "orphan_candidates",
                    "entry": item["entry"],
                },
                "decision": "orphan",
                "unresolved": [],
                "connected": [],
                "retained": [
                    {
                        "identity": candidate["identity"],
                        "validation_note": note_sha,
                    }
                    for candidate in item["candidates"]
                ],
            }
        )
    decisions = {
        "schema_version": DECISIONS.DECISION_SCHEMA_VERSION,
        "actions": [
            {
                "match": {"kind": "semantic_provenance"},
                "decision": "support",
                "candidate": 1,
            },
            {
                "match": {"entry": "e001", "identity": output},
                "decision": "pass",
                "producer": output_producer,
            },
            {
                "match": {
                    "targets": [
                        {"entry": "e001", "identity": invalid},
                        {"entry": "e001", "identity": missing},
                    ]
                },
                "decision": "fail",
                "findings": {
                    "Provenance": (
                        "The invalid or missing artifact cannot be traced to "
                        "retained evidence."
                    )
                },
            },
            {
                "match": {"entry": "e001", "identity": collection},
                "decision": "pass",
                "producer": collection_producer,
                "members": {collection: {"glob": "a.txt"}},
            },
            *orphan_actions,
        ],
    }
    decided, counts = DECISIONS.apply_review_decisions(scan, prepared, decisions)
    if counts["remaining"]:
        raise AssertionError(f"fixture decisions left {counts['remaining']} items")
    return decided
