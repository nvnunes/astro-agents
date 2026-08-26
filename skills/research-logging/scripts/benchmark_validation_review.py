#!/usr/bin/env python3
"""Generate and benchmark the canonical large orphan-review workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from validation.activity import (
    ValidationActivityLog,
    ValidationActivityRequest,
    log_checkpoint,
    log_operation,
    log_phase,
)
from validation.compatibility import invocation_identities
from validation.contracts import AdjudicationRecord, ScanRecord
from validation.decisions import apply_review_decisions
from validation.judgment_rules import SEMANTIC_REVIEW_RULES
from validation.producer_bindings import identity_for_path, resolved_identity_cache
from validation.review_exchange import (
    MAX_PACKET_BYTES,
    _ordinary_template,
    accept_review_page,
    create_exchange,
    decisions_to_actions,
    finish_review_session,
    load_decisions,
    reusable_review_actions,
)

GENERATOR_VERSION = 3
DEFAULT_ORPHANS = 12_000
DOUBLED_ORPHANS = 24_000
DEFAULT_BATCH_SIZE = 200
DEFAULT_COMMANDS = 16
DEFAULT_EXPECTED_IDENTITY = (
    "ab62e7ce2285fbcfafa90f13e2f344ec73ec1c117c08155c4e04c9accfbabb9a"
)
BENCHMARK_VALIDATION_NOTE = "d" * 64
ACTIVITY_OVERHEAD_PHASES = 10
DEFAULT_REUSE_TARGETS = 200


def _candidate_identity(index: int) -> str:
    return (
        "docs/training/entries/benchmark-e001/data/training/"
        f"case-{index:05d}/result.csv"
    )


def _content_identity(orphan_count: int, command_count: int) -> str:
    payload = {
        "generator_version": GENERATOR_VERSION,
        "orphan_count": orphan_count,
        "command_count": command_count,
        "batch_size": DEFAULT_BATCH_SIZE,
        "candidates": [_candidate_identity(index) for index in range(orphan_count)],
        "command_kinds": [
            "known-output" if index % 2 == 0 else "unknown-output"
            for index in range(command_count)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def generated_workload(
    root: Path,
    *,
    orphan_count: int,
    command_count: int = DEFAULT_COMMANDS,
) -> tuple[ScanRecord, AdjudicationRecord, str]:
    """Return a deterministic in-memory scan and adjudication benchmark pair."""

    root.mkdir(parents=True, exist_ok=True)
    script = root / "produce.py"
    script.write_text(
        "def produce(args):\n"
        "    output = args.output_dir / 'case' / 'result.csv'\n"
        "    return output\n",
        encoding="utf-8",
    )
    container = root / "data"
    container.mkdir()
    known_identity = "docs/training/entries/benchmark-e001/data"
    commands = []
    for index in range(command_count):
        role = "output" if index % 2 == 0 else "unknown"
        raw_container = container
        commands.append(
            {
                "line": 20 + index,
                "section": "Benchmark Results",
                "command": (
                    "python produce.py --output-dir <benchmark_data> "
                    f"--replicate {index % 4}"
                ),
                "script": script.as_posix(),
                "unknown_options": [],
                "data_tokens": [],
                "path_arguments": [
                    {
                        "path": raw_container.as_posix(),
                        "role_hint": role,
                        "option": "--output-dir",
                    }
                ],
            }
        )
    candidates = [
        {"identity": _candidate_identity(index), "kind": "artifact"}
        for index in range(orphan_count)
    ]
    scan = cast(
        ScanRecord,
        {
            "schema_version": 16,
            "validation_rules_version": "research-log-validation-v43",
            "input_fingerprint": _content_identity(orphan_count, command_count),
            "summary": "docs/ao-predict/training.md",
            "project_root": root.as_posix(),
            "resolved_paths": {
                "docs/training/entries/benchmark-e001/scripts/produce.py": (
                    script.as_posix()
                ),
                known_identity: container.as_posix(),
            },
            "mechanical_checks": {
                "docs/training/entries/benchmark-e001/scripts/produce.py": {
                    "status": "ok",
                    "type": "python",
                },
                known_identity: {"status": "ok", "type": "directory"},
            },
            "entries": [
                {
                    "id": "e001",
                    "path": "docs/training/entries/benchmark-e001/e001.md",
                    "commands": commands,
                }
            ],
        },
    )
    adjudication = cast(
        AdjudicationRecord,
        {
            "schema_version": 7,
            "validation_rules_version": "research-log-validation-v43",
            "date": "2026-08-16",
            "summary": [],
            "entries": [],
            "review_queue": [
                {
                    "entry": "e001",
                    "kind": "orphan_candidates",
                    "identity": "Orphaned artifacts, scripts, and references",
                    "candidates": candidates,
                    "validation_notes": [],
                    "reason": "benchmark",
                }
            ],
        },
    )
    identity = _content_identity(orphan_count, command_count)
    if (
        orphan_count == DEFAULT_ORPHANS
        and command_count == DEFAULT_COMMANDS
        and identity != DEFAULT_EXPECTED_IDENTITY
    ):
        raise RuntimeError("canonical benchmark generator identity changed")
    return scan, adjudication, identity


def generated_session_workload(
    root: Path,
    *,
    orphan_count: int,
    command_count: int = DEFAULT_COMMANDS,
) -> tuple[ScanRecord, AdjudicationRecord, str]:
    """Return a production-shaped workload for complete session acceptance."""

    scan, adjudication, identity = generated_workload(
        root,
        orphan_count=orphan_count,
        command_count=command_count,
    )
    queue_item = adjudication["review_queue"][0]
    candidates = queue_item["candidates"]
    entry = scan["entries"][0]
    entry["data_index"] = {
        f"benchmark-input-{index:05d}": {
            "path": candidate["identity"],
            "role": "input",
        }
        for index, candidate in enumerate(candidates)
    }
    entry["validation_notes"] = [{"sha256": BENCHMARK_VALIDATION_NOTE}]
    entry["orphan_inventory"] = candidates
    queue_item.update(
        {
            "identity": "Orphaned artifacts, scripts, and references",
            "subtree_splits": [
                "docs/training/entries/benchmark-e001/data/training"
            ],
            "validation_notes": [{"sha256": BENCHMARK_VALIDATION_NOTE}],
        }
    )
    adjudication["entries"] = [
        {
            "id": "e001",
            "targets": [
                {"target": "Orphaned artifacts, scripts, and references"}
            ],
            "orphan_items": [
                {
                    "identity": candidate["identity"],
                    "decision": "pending",
                    "basis": "-",
                }
                for candidate in candidates
            ],
        }
    ]
    return scan, adjudication, identity


def generated_reuse_application_workload(
    root: Path,
    *,
    target_count: int,
) -> tuple[ScanRecord, AdjudicationRecord, list[dict[str, Any]]]:
    """Return deterministic producer decisions without scanning evidence."""

    entry_path = "docs/benchmark/entries/e001/e001.md"
    commands = []
    targets = []
    resolved_paths = {}
    for number in range(target_count):
        identity = f"docs/benchmark/entries/e001/data/result-{number:04d}.csv"
        raw = root / identity
        resolved_paths[identity] = raw.as_posix()
        commands.append(
            {
                "line": number + 1,
                "section": "Results",
                "command": f"python produce.py --output {identity}",
                "path_arguments": [
                    {"path": raw.as_posix(), "role_hint": "output"}
                ],
            }
        )
        targets.append(
            {
                "target": identity,
                "sections": ["Results"],
                "integrity": None,
                "provenance": None,
                "reproducibility": "-",
                "notes": "-",
                "dependencies": [{"path": identity, "role": "target"}],
                "findings": [],
            }
        )
    invocations = invocation_identities("e001", commands)
    queue = [
        {
            "kind": "semantic_fallback",
            "entry": "e001",
            "identity": row["target"],
            "sections": ["Results"],
            "workflow": {"status": "unresolved"},
            "evidence": [],
            "hard_failures": [],
            "producer_candidates": [
                {
                    "material": row["target"],
                    "invocation": invocation,
                    "coverage_identity": row["target"],
                    "coverage_kind": "exact-target",
                }
            ],
        }
        for row, invocation in zip(targets, invocations)
    ]
    scan = cast(
        ScanRecord,
        {
            "schema_version": 16,
            "validation_rules_version": "research-log-validation-v43",
            "summary": "docs/benchmark.md",
            "project_root": root.as_posix(),
            "resolved_paths": resolved_paths,
            "files": {
                identity: {"size": 1, "sha256": "a" * 64}
                for identity in resolved_paths
            },
            "directory_memberships": {},
            "mechanical_checks": {},
            "script_inventory": [],
            "entries": [
                {
                    "id": "e001",
                    "path": entry_path,
                    "commands": commands,
                    "orphan_inventory": [],
                    "candidate_targets": [],
                }
            ],
        },
    )
    adjudication = cast(
        AdjudicationRecord,
        {
            "schema_version": 7,
            "validation_rules_version": "research-log-validation-v43",
            "date": "2026-08-26",
            "summary": [],
            "entries": [
                {
                    "id": "e001",
                    "path": entry_path,
                    "scope_paths": [entry_path],
                    "orphan_items": [],
                    "targets": targets,
                }
            ],
            "review_queue": queue,
        },
    )
    judgments = []
    for item, invocation in zip(queue, invocations):
        template = _ordinary_template(item)
        judgments.append(
            {
                "identity": template["id"],
                "kind": "review-decision",
                "subject": {
                    "kind": item["kind"],
                    "entry": item["entry"],
                    "identity": item["identity"],
                },
                "decision": invocation,
                "rule_dependencies": SEMANTIC_REVIEW_RULES,
                "input_dependencies": [],
                "rationale": "Exact deterministic benchmark decision.",
            }
        )
    return scan, adjudication, judgments


def reuse_application_sample(target_count: int) -> dict[str, Any]:
    """Benchmark exact reusable-action construction and application."""

    with tempfile.TemporaryDirectory(
        prefix="validation-reuse-application-benchmark-"
    ) as directory:
        scan, adjudication, judgments = generated_reuse_application_workload(
            Path(directory), target_count=target_count
        )
        metrics: dict[str, Any] = {}
        construction_started = time.monotonic()
        actions = reusable_review_actions(
            scan, adjudication, judgments, metrics
        )
        construction_seconds = time.monotonic() - construction_started
        application_started = time.monotonic()
        decided, counts = apply_review_decisions(scan, adjudication, actions)
        application_seconds = time.monotonic() - application_started
        return {
            "kind": "reusable_judgment_application",
            "target_count": target_count,
            "questions_considered": int(metrics.get("questions_considered", 0)),
            "answers_found": int(metrics.get("answers_found", 0)),
            "misses_by_reason": dict(metrics.get("misses_by_reason", {})),
            "action_count": len(actions["actions"]),
            "remaining_review_items": len(decided["review_queue"]),
            "applied_passes": counts.get("pass", 0),
            "action_construction_seconds": construction_seconds,
            "action_application_seconds": application_seconds,
            "machine": _machine_context(),
        }


def _legacy_candidate_commands(
    scan: ScanRecord,
    entry_id: str,
    identity: str,
    counters: dict[str, int],
) -> list[dict[str, Any]]:
    """Retain the pre-index exhaustive query as the benchmark baseline."""

    identities = resolved_identity_cache(scan)
    presenting = next(
        entry for entry in scan.get("entries", []) if entry.get("id") == entry_id
    )
    entries = [
        presenting,
        *[
            entry
            for entry in scan.get("entries", [])
            if entry is not presenting
            and isinstance(entry.get("path"), str)
            and identity.startswith(
                Path(entry["path"]).parent.as_posix().rstrip("/") + "/"
            )
        ],
    ]
    groups: list[list[dict[str, Any]]] = [[] for _ in range(6)]
    for entry_number, entry in enumerate(entries):
        counters["entry_scans"] += 1
        for command in entry.get("commands", []):
            relationship = _legacy_relationship(
                scan, command, identity, identities, counters
            )
            _add_legacy_groups(groups, command, relationship, entry_number)
    result = []
    seen = set()
    for command in (command for group in groups for command in group):
        if id(command) in seen:
            continue
        seen.add(id(command))
        result.append(command)
        if len(result) == 5:
            break
    return result


def _add_legacy_groups(
    groups: list[list[dict[str, Any]]],
    command: dict[str, Any],
    relationship: tuple[bool, bool, bool, bool],
    entry_number: int,
) -> None:
    direct, container, exact, section = relationship
    if direct:
        groups[0].append(command)
    if container:
        if section:
            groups[1].append(command)
        elif entry_number:
            groups[2].append(command)
        else:
            groups[5].append(command)
    if exact:
        groups[3].append(command)
    elif section:
        groups[4].append(command)


def _legacy_unknown_container(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identity: str,
    identities: Mapping[str, str],
    counters: dict[str, int],
) -> bool:
    unknown = [
        identity_for_path(scan, argument.get("path", ""), identities)
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "unknown" and argument.get("path")
    ]
    if not any(
        scan.get("mechanical_checks", {}).get(candidate, {}).get("type")
        == "directory"
        and identity.startswith(candidate.rstrip("/") + "/")
        for candidate in unknown
    ):
        return False
    raw_script = command.get("script")
    if not isinstance(raw_script, str) or not Path(raw_script).is_file():
        return False
    counters["producer_source_reads"] += 1
    source = Path(raw_script).read_text(encoding="utf-8").lower()
    tokens = {
        token for token in _tokenize(Path(identity).stem) if len(token) > 1
    }
    searchable = source + "\n" + command.get("command", "").lower()
    return bool(tokens) and all(token in searchable for token in tokens)


def _legacy_relationship(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identity: str,
    identities: Mapping[str, str],
    counters: dict[str, int],
) -> tuple[bool, bool, bool, bool]:
    counters["relationship_evaluations"] += 1
    output_paths = [
        argument.get("path", "")
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "output"
    ]
    output_identities = [
        identity_for_path(scan, path, identities) for path in output_paths
    ]
    direct = identity in output_identities
    container = any(
        scan.get("mechanical_checks", {})
        .get(output_identity, {})
        .get("type")
        == "directory"
        and identity.startswith(output_identity.rstrip("/") + "/")
        for output_identity in output_identities
    )
    if not container:
        container = _legacy_unknown_container(
            scan, command, identity, identities, counters
        )
    target_name = Path(identity).name
    exact = target_name in command.get("command", "") or any(
        Path(path).name == target_name for path in output_paths
    )
    section = command.get("section") == "Benchmark Results"
    return direct, container, exact, section


def _tokenize(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _command_output(command: Sequence[str]) -> str:
    try:
        return subprocess.check_output(
            command, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sysconf_bytes(page_count: str) -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf(page_count)
    except (OSError, ValueError):
        return None


def _darwin_available_memory() -> int | None:
    vm_stat = _command_output(["/usr/bin/vm_stat"])
    page_size = re.search(r"page size of (\d+) bytes", vm_stat)
    if page_size is None:
        return None
    available_pages = sum(
        int(match.group(1))
        for label in ("free", "inactive", "speculative")
        if (match := re.search(rf"Pages {label}:\s+(\d+)\.", vm_stat))
    )
    return int(page_size.group(1)) * available_pages


def _darwin_hardware() -> tuple[str, str | None, int | None]:
    cpu = _command_output(
        ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]
    )
    raw_memory = _command_output(["/usr/sbin/sysctl", "-n", "hw.memsize"])
    total_memory = int(raw_memory) if raw_memory.isdigit() else None
    hardware = _command_output(
        [
            "/usr/sbin/system_profiler",
            "SPHardwareDataType",
            "-detailLevel",
            "mini",
        ]
    )
    chip = re.search(r"^\s*Chip:\s*(.+)$", hardware, re.MULTILINE)
    model = re.search(r"^\s*Model Identifier:\s*(.+)$", hardware, re.MULTILINE)
    return (
        cpu or (chip.group(1) if chip is not None else platform.machine()),
        model.group(1) if model is not None else None,
        total_memory,
    )


def _machine_context() -> dict[str, Any]:
    available_memory = _sysconf_bytes("SC_AVPHYS_PAGES")
    total_memory = _sysconf_bytes("SC_PHYS_PAGES")
    cpu = platform.processor() or platform.machine()
    hardware_model = None
    if sys.platform == "darwin":
        cpu, hardware_model, darwin_total = _darwin_hardware()
        available_memory = _darwin_available_memory()
        total_memory = darwin_total or total_memory
    return {
        "python": platform.python_version(),
        "operating_system": platform.platform(),
        "cpu": cpu,
        "hardware_model": hardware_model,
        "logical_cpu_count": os.cpu_count(),
        "available_memory_bytes": available_memory,
        "total_memory_bytes": total_memory,
    }


def _peak_memory_bytes() -> int:
    """Return process peak resident memory normalized across supported systems."""

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _activity_overhead_sample(
    enabled: bool, cycles: int
) -> dict[str, Any]:
    """Measure one controller-shaped activity sequence with logging toggled."""

    with tempfile.TemporaryDirectory(
        prefix="validation-activity-overhead-"
    ) as directory:
        root = Path(directory)
        activity = (
            ValidationActivityLog(
                ValidationActivityRequest(
                    root,
                    root / "summary.md",
                    "standard",
                    14,
                    False,
                    heartbeat_seconds=3600.0,
                )
            )
            if enabled
            else None
        )
        started = time.monotonic()
        for _cycle in range(cycles):
            for phase in range(ACTIVITY_OVERHEAD_PHASES):
                log_phase(
                    activity,
                    f"review.finalize.phase-{phase}",
                    items=DEFAULT_ORPHANS,
                )
                with log_operation(
                    activity,
                    f"operation-{phase}",
                    subject="docs/ao-predict/training.md",
                    items=DEFAULT_ORPHANS,
                ):
                    pass
                log_checkpoint(
                    activity,
                    f"checkpoint-{phase}",
                    items=DEFAULT_ORPHANS,
                )
        elapsed = time.monotonic() - started
        log_lines = 0
        if activity is not None:
            activity.finish("complete")
            log_lines = activity.path.read_text(encoding="utf-8").count("\n")
        return {
            "enabled": enabled,
            "elapsed_seconds": elapsed,
            "event_count": cycles * ACTIVITY_OVERHEAD_PHASES * 4,
            "log_lines": log_lines,
        }


def _activity_overhead_driver(args: argparse.Namespace) -> dict[str, Any]:
    """Return paired activity-log overhead samples without research scanning."""

    for _ in range(args.warmups):
        _activity_overhead_sample(False, args.activity_cycles)
        _activity_overhead_sample(True, args.activity_cycles)
    samples: dict[str, list[dict[str, Any]]] = {
        "disabled": [],
        "enabled": [],
    }
    for number in range(args.runs):
        order = (False, True) if number % 2 == 0 else (True, False)
        for enabled in order:
            samples["enabled" if enabled else "disabled"].append(
                _activity_overhead_sample(enabled, args.activity_cycles)
            )
    disabled_median = statistics.median(
        sample["elapsed_seconds"] for sample in samples["disabled"]
    )
    enabled_median = statistics.median(
        sample["elapsed_seconds"] for sample in samples["enabled"]
    )
    event_count = args.activity_cycles * ACTIVITY_OVERHEAD_PHASES * 4
    return {
        "kind": "validation_activity_overhead",
        "warmups": args.warmups,
        "runs": args.runs,
        "cycles_per_sample": args.activity_cycles,
        "events_per_sample": event_count,
        "disabled_median_seconds": disabled_median,
        "enabled_median_seconds": enabled_median,
        "incremental_seconds_per_event": (
            enabled_median - disabled_median
        )
        / event_count,
        "samples": samples,
        "machine": _machine_context(),
    }


def _reuse_application_driver(args: argparse.Namespace) -> dict[str, Any]:
    """Return fresh-process reusable-application scaling samples."""

    executable = Path(__file__).resolve()
    results = []
    for target_count in args.reuse_targets:
        command = [
            sys.executable,
            executable.as_posix(),
            "--single-reuse-application",
            str(target_count),
        ]
        for _ in range(args.warmups):
            subprocess.run(
                command, check=True, capture_output=True, text=True
            )
        samples = [
            json.loads(
                subprocess.run(
                    command, check=True, capture_output=True, text=True
                ).stdout
            )
            for _ in range(args.runs)
        ]
        results.append(
            {
                "target_count": target_count,
                "median_action_construction_seconds": statistics.median(
                    sample["action_construction_seconds"] for sample in samples
                ),
                "median_action_application_seconds": statistics.median(
                    sample["action_application_seconds"] for sample in samples
                ),
                "samples": samples,
            }
        )
    return {
        "kind": "reusable_judgment_application_scaling",
        "warmups": args.warmups,
        "runs": args.runs,
        "results": results,
    }


def _legacy_run(
    scan: ScanRecord, adjudication: AdjudicationRecord
) -> tuple[int, dict[str, Any]]:
    counters = {
        "entry_scans": 0,
        "relationship_evaluations": 0,
        "producer_source_reads": 0,
    }
    first_batch = []
    candidates = adjudication["review_queue"][0]["candidates"]
    for number, candidate in enumerate(candidates):
        commands = _legacy_candidate_commands(
            scan, "e001", candidate["identity"], counters
        )
        if number < DEFAULT_BATCH_SIZE:
            first_batch.extend(command.get("command", "") for command in commands)
    return len("\n".join(first_batch).encode("utf-8")), counters


def _public_run(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    orphan_count: int,
) -> tuple[int, dict[str, Any]]:
    exchange = create_exchange(scan, adjudication, {})
    decision_path = Path(exchange["decision_file"])
    template = json.loads(decision_path.read_text(encoding="utf-8"))
    for item in template["items"]:
        item["decision"] = (
            {"action": "classify-subtree", "disposition": "unresolved"}
            if item["kind"] == "orphan_subtree"
            else "unresolved"
        )
        item["rationale"] = "No local evidence connection is recorded."
    decision_path.write_text(
        json.dumps(template, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decisions, internal = load_decisions(decision_path)
    following = accept_review_page(decisions, internal)
    finish_review_session(decision_path.parent.parent)
    return exchange["byte_count"], {
        "candidates_indexed": orphan_count,
        "first_packet_items": exchange["item_count"],
        "accepted_items": len(decisions["items"]),
        "next_packet_items": following.get("item_count", 0),
        "packet_byte_bound": MAX_PACKET_BYTES,
    }


def _filled_session_page(
    decision_path: Path, decision_offset: int
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    template = json.loads(decision_path.read_text(encoding="utf-8"))
    judgment_identities = []
    for number, item in enumerate(template["items"]):
        item["decision"] = {
            "action": "classify-subtree",
            "disposition": (
                "connected"
                if (decision_offset + number) % 4 == 0
                else "unresolved"
            ),
        }
        item["rationale"] = (
            "The production-shaped benchmark supplies a deterministic mixed "
            "lifecycle decision."
        )
        judgment_identities.append(
            hashlib.sha256(
                f"benchmark-judgment:{decision_offset + number}".encode()
            ).hexdigest()
        )
    decision_path.write_text(
        json.dumps(template, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decisions, internal = load_decisions(decision_path)
    return decisions, internal, judgment_identities


def _accepted_session(
    exchange: Mapping[str, Any],
) -> tuple[Mapping[str, Any], int, int, float]:
    page = exchange
    page_count = 0
    accepted_items = 0
    decision_offset = 0
    started = time.monotonic()
    while page["status"] == "review_required":
        decisions, internal, identities = _filled_session_page(
            Path(page["decision_file"]), decision_offset
        )
        page = accept_review_page(
            decisions,
            internal,
            lambda *_args, values=identities: values,
        )
        page_count += 1
        accepted_items += len(decisions["items"])
        decision_offset += len(decisions["items"])
    return page, page_count, accepted_items, time.monotonic() - started


def _session_run(
    scan: ScanRecord,
    adjudication: AdjudicationRecord,
    orphan_count: int,
) -> tuple[int, dict[str, Any]]:
    exchange_started = time.monotonic()
    exchange = create_exchange(scan, adjudication, {})
    exchange_seconds = time.monotonic() - exchange_started
    page, page_count, accepted_items, acceptance_seconds = _accepted_session(
        exchange
    )
    translation_started = time.monotonic()
    actions = decisions_to_actions(
        page["decisions"],
        {
            "scan": scan,
            "adjudication": adjudication,
            "orphan_fingerprints": page["orphan_fingerprints"],
        },
    )
    translation_seconds = time.monotonic() - translation_started
    application_started = time.monotonic()
    decided, _ = apply_review_decisions(
        scan,
        adjudication,
        actions,
        trusted_orphan_fingerprints=page["orphan_fingerprints"],
    )
    application_seconds = time.monotonic() - application_started
    finish_review_session(Path(exchange["decision_file"]).parent.parent)
    return exchange["byte_count"], {
        "candidates_indexed": orphan_count,
        "session_pages": page_count,
        "accepted_items": accepted_items,
        "merged_items": len(page["decisions"]["items"]),
        "accepted_judgment_identities": len(page.get("judgment_identities", [])),
        "action_count": len(actions["actions"]),
        "remaining_review_items": len(decided["review_queue"]),
        "exchange_seconds": exchange_seconds,
        "page_acceptance_seconds": acceptance_seconds,
        "action_translation_seconds": translation_seconds,
        "action_application_seconds": application_seconds,
        "packet_byte_bound": MAX_PACKET_BYTES,
    }


def _single_run(mode: str, orphan_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="validation-review-benchmark-"
    ) as directory:
        generator = (
            generated_session_workload
            if mode == "session"
            else generated_workload
        )
        scan, adjudication, identity = generator(
            Path(directory), orphan_count=orphan_count
        )
        started = time.monotonic()
        if mode == "legacy":
            packet_bytes, metrics = _legacy_run(scan, adjudication)
        elif mode == "public":
            packet_bytes, metrics = _public_run(
                scan, adjudication, orphan_count
            )
        else:
            packet_bytes, metrics = _session_run(
                scan, adjudication, orphan_count
            )
        elapsed = time.monotonic() - started
        return {
            "mode": mode,
            "orphan_count": orphan_count,
            "generator_identity": identity,
            "elapsed_seconds": elapsed,
            "peak_memory_bytes": _peak_memory_bytes(),
            "packet_bytes": packet_bytes,
            "metrics": metrics,
            "machine": _machine_context(),
        }


def _single_fanout(materials: int, candidates_per_material: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="validation-review-fanout-benchmark-"
    ) as directory:
        root = Path(directory)
        candidates = [
            {
                "material": f"docs/benchmark/data/material-{material:03d}.csv",
                "invocation": f"invocation-{candidate:03d}",
                "entry": "e001",
                "line": candidate + 1,
                "command": f"python produce.py --case {candidate}",
            }
            for material in range(materials)
            for candidate in range(candidates_per_material)
        ]
        scan = cast(
            ScanRecord,
            {
                "summary": "docs/benchmark.md",
                "project_root": root.as_posix(),
                "validation_rules_version": "benchmark-rules",
                "input_fingerprint": _content_identity(
                    materials, candidates_per_material
                ),
                "entries": [{"id": "e001", "commands": []}],
            },
        )
        adjudication = cast(
            AdjudicationRecord,
            {
                "date": "2026-08-16",
                "review_queue": [
                    {
                        "entry": "e001",
                        "kind": "upstream_producer",
                        "identity": "docs/benchmark/data/result.csv",
                        "producer_candidates": candidates,
                        "workflow": {"status": "unresolved"},
                        "evidence": [],
                    }
                ],
                "summary": [],
            },
        )
        started = time.monotonic()
        exchange = create_exchange(scan, adjudication, {})
        elapsed = time.monotonic() - started
        decision_path = Path(exchange["decision_file"])
        finish_review_session(decision_path.parent.parent)
        return {
            "kind": "upstream_fanout",
            "materials": materials,
            "candidates_per_material": candidates_per_material,
            "candidate_relationships": len(candidates),
            "question_count": exchange["item_count"],
            "packet_bytes": exchange["byte_count"],
            "packet_byte_bound": MAX_PACKET_BYTES,
            "elapsed_seconds": elapsed,
            "peak_memory_bytes": _peak_memory_bytes(),
            "machine": _machine_context(),
        }


def _driver(args: argparse.Namespace) -> dict[str, Any]:
    executable = Path(__file__).resolve()
    results = []
    for mode in args.mode:
        for orphan_count in args.orphans:
            for _ in range(args.warmups):
                subprocess.run(
                    [
                        sys.executable,
                        executable.as_posix(),
                        "--single",
                        "--mode",
                        mode,
                        "--orphans",
                        str(orphan_count),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            samples = []
            for _ in range(args.runs):
                completed = subprocess.run(
                    [
                        sys.executable,
                        executable.as_posix(),
                        "--single",
                        "--mode",
                        mode,
                        "--orphans",
                        str(orphan_count),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                samples.append(json.loads(completed.stdout))
            results.append(
                {
                    "mode": mode,
                    "orphan_count": orphan_count,
                    "median_seconds": statistics.median(
                        sample["elapsed_seconds"] for sample in samples
                    ),
                    "samples": samples,
                }
            )
    fanout_results = []
    for fanout in args.fanout:
        materials, candidates = (int(value) for value in fanout.split("x", 1))
        samples = []
        for _ in range(args.runs):
            completed = subprocess.run(
                [
                    sys.executable,
                    executable.as_posix(),
                    "--single-fanout",
                    fanout,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            samples.append(json.loads(completed.stdout))
        fanout_results.append(
            {
                "fanout": fanout,
                "median_seconds": statistics.median(
                    sample["elapsed_seconds"] for sample in samples
                ),
                "samples": samples,
            }
        )
    return {
        "generator_version": GENERATOR_VERSION,
        "default_generator_identity": _content_identity(
            DEFAULT_ORPHANS, DEFAULT_COMMANDS
        ),
        "warmups": args.warmups,
        "runs": args.runs,
        "results": results,
        "fanout_results": fanout_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--single-fanout")
    parser.add_argument("--single-reuse-application", type=int)
    parser.add_argument("--activity-overhead", action="store_true")
    parser.add_argument("--reuse-application", action="store_true")
    parser.add_argument("--reuse-targets", action="append", type=int, default=[])
    parser.add_argument("--activity-cycles", type=int, default=100)
    parser.add_argument(
        "--mode",
        action="append",
        choices=("legacy", "public", "session"),
        default=[],
    )
    parser.add_argument("--orphans", action="append", type=int, default=[])
    parser.add_argument("--fanout", action="append", default=[])
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser


def _requested_result(
    args: argparse.Namespace,
    modes: list[str],
    orphans: list[int],
    fanout: list[str],
) -> dict[str, Any]:
    """Dispatch one selected benchmark operation."""

    if args.activity_overhead:
        return _activity_overhead_driver(args)
    if args.reuse_application:
        return _reuse_application_driver(args)
    if args.single_reuse_application is not None:
        return reuse_application_sample(args.single_reuse_application)
    if args.single_fanout:
        materials, candidates = (
            int(value) for value in args.single_fanout.split("x", 1)
        )
        return _single_fanout(materials, candidates)
    if args.single:
        if len(modes) != 1 or len(orphans) != 1:
            raise SystemExit("--single requires one --mode and one --orphans value")
        return _single_run(modes[0], orphans[0])
    args.mode = modes
    args.orphans = orphans
    args.fanout = fanout
    return _driver(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    modes = args.mode or ["public"]
    orphans = args.orphans or [DEFAULT_ORPHANS, DOUBLED_ORPHANS]
    fanout = args.fanout or ["5x5", "10x10"]
    reuse_targets = args.reuse_targets or [
        DEFAULT_REUSE_TARGETS,
        DEFAULT_REUSE_TARGETS * 2,
    ]
    if any(value < 1 for value in orphans):
        raise SystemExit("--orphans values must be positive")
    if args.activity_cycles < 1:
        raise SystemExit("--activity-cycles must be positive")
    if any(value < 1 for value in reuse_targets):
        raise SystemExit("--reuse-targets values must be positive")
    if args.single_reuse_application is not None and args.single_reuse_application < 1:
        raise SystemExit("--single-reuse-application must be positive")
    args.reuse_targets = reuse_targets
    result = _requested_result(args, modes, orphans, fanout)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
