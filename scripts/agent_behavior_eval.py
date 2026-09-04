"""Run isolated, opt-in Codex agent behavior evaluations.

The tool retains raw runtime events, prompts, session traces, and workspace
state around every turn. It supplies generic observations; each evaluation
owns its fixture, pressure material, allowed changes, and pass criteria.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ARTIFACT_SCHEMA_VERSION = 2
TURN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
STATE_EXCLUDES = (
    ".DS_Store",
    ".git",
    ".agents",
    "skills",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)


@dataclass(frozen=True)
class SequenceTurn:
    """One prompt in a multi-turn evaluation sequence.

    Attributes:
        turn_id: Stable lowercase identifier used in artifact paths.
        prompt_path: Absolute path to the exact prompt text for the turn.
    """

    turn_id: str
    prompt_path: Path


@dataclass(frozen=True)
class SequenceSpec:
    """Validated sequence name and ordered prompts."""

    name: str
    turns: tuple[SequenceTurn, ...]


def _write_json(path: Path, value: Any) -> None:
    """Write deterministic UTF-8 JSON."""
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    """Hash relative paths, file content, and symlink targets below ``root``."""
    if not root.is_dir():
        raise ValueError(f"tree root is not a directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            kind = b"symlink"
            content = str(path.readlink()).encode()
        elif path.is_file():
            kind = b"file"
            content = path.read_bytes()
        else:
            continue
        for value in (kind, relative, content):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def load_sequence(path: Path) -> SequenceSpec:
    """Load and validate the sequence JSON contract.

    The file must contain a nonempty ``name`` and a nonempty ``turns`` list.
    Every turn requires a unique lowercase ``id`` and a readable prompt path
    relative to the sequence file.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    name = raw.get("name")
    turns = raw.get("turns")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("sequence name must be a nonempty string")
    if not isinstance(turns, list) or not turns:
        raise ValueError("sequence turns must be a nonempty list")

    seen: set[str] = set()
    validated: list[SequenceTurn] = []
    for index, raw_turn in enumerate(turns, start=1):
        if not isinstance(raw_turn, dict):
            raise ValueError(f"turn {index} must be an object")
        turn_id = raw_turn.get("id")
        prompt = raw_turn.get("prompt")
        if not isinstance(turn_id, str) or not TURN_ID_PATTERN.fullmatch(turn_id):
            raise ValueError(f"turn {index} has an invalid id: {turn_id!r}")
        if turn_id in seen:
            raise ValueError(f"duplicate turn id: {turn_id}")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"turn {turn_id} has no prompt path")
        prompt_path = (path.parent / prompt).resolve()
        if not prompt_path.is_file():
            raise ValueError(f"turn {turn_id} prompt does not exist: {prompt_path}")
        seen.add(turn_id)
        validated.append(SequenceTurn(turn_id=turn_id, prompt_path=prompt_path))
    return SequenceSpec(name=name.strip(), turns=tuple(validated))


def parse_event_lines(text: str) -> dict[str, Any]:
    """Summarize Codex ``--json`` output while preserving unknown events."""
    thread_id: str | None = None
    usages: list[dict[str, int]] = []
    event_types: list[str] = []
    invalid_lines = 0
    compacted = False
    for raw_line in text.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        event_type = str(event.get("type", ""))
        event_types.append(event_type)
        if event_type == "thread.started" and event.get("thread_id"):
            thread_id = str(event["thread_id"])
        usage = event.get("usage")
        if event_type == "turn.completed" and isinstance(usage, dict):
            usages.append({key: int(value) for key, value in usage.items()})
        if event_type in {"context_compacted", "compacted"}:
            compacted = True
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "context_compaction":
            compacted = True
    return {
        "thread_id": thread_id,
        "usages": usages,
        "event_types": event_types,
        "invalid_lines": invalid_lines,
        "compacted": compacted,
    }


def require_event_contract(summary: dict[str, Any], *, first_turn: bool) -> None:
    """Fail when required Codex JSON events are absent or malformed."""
    if summary["invalid_lines"]:
        raise RuntimeError("Codex emitted non-JSON lines in --json mode")
    if first_turn and not summary["thread_id"]:
        raise RuntimeError("Codex JSON omitted the initial thread.started event")
    if "turn.completed" not in summary["event_types"] or not summary["usages"]:
        raise RuntimeError("Codex JSON omitted turn.completed usage")


def parse_session_trace(text: str) -> dict[str, Any]:
    """Summarize persisted token-count and compaction records."""
    token_counts: list[dict[str, Any]] = []
    compacted_records = 0
    context_compacted_events = 0
    invalid_lines = 0
    for raw_line in text.splitlines():
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if record.get("type") == "compacted":
            compacted_records += 1
        payload = record.get("payload")
        if record.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        if payload.get("type") == "context_compacted":
            context_compacted_events += 1
        if payload.get("type") == "token_count":
            token_counts.append(payload)

    peak_input_tokens: int | None = None
    context_window: int | None = None
    for payload in token_counts:
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        usage = info.get("last_token_usage")
        window = info.get("model_context_window")
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
            value = int(usage["input_tokens"])
            peak_input_tokens = max(peak_input_tokens or 0, value)
        if isinstance(window, int):
            context_window = window
    fraction = None
    if peak_input_tokens is not None and context_window:
        fraction = peak_input_tokens / context_window
    return {
        "token_counts": token_counts,
        "compacted_records": compacted_records,
        "context_compacted_events": context_compacted_events,
        "invalid_lines": invalid_lines,
        "compacted": bool(compacted_records or context_compacted_events),
        "peak_input_tokens": peak_input_tokens,
        "model_context_window": context_window,
        "peak_context_fraction": fraction,
    }


def require_session_contract(summary: dict[str, Any]) -> None:
    """Fail when persisted runtime observability is unavailable."""
    if summary["invalid_lines"]:
        raise RuntimeError("persisted Codex session contains invalid JSONL")
    if not summary["token_counts"]:
        raise RuntimeError("persisted Codex session contains no token_count events")
    if summary["peak_input_tokens"] is None:
        raise RuntimeError("token_count events omit last input usage")
    if summary["model_context_window"] is None:
        raise RuntimeError("token_count events omit model_context_window")


def session_observation(
    summary: dict[str, Any], previous_compactions: int
) -> tuple[dict[str, Any], int]:
    """Return one cumulative session observation and its compaction transition.

    The persisted trace emits two records for one compaction. Their maximum is
    therefore the number of observed compaction boundaries. ``new_compaction``
    is true only for the first completed turn that can see a new boundary.
    """
    compactions = max(
        int(summary["compacted_records"]),
        int(summary["context_compacted_events"]),
    )
    observation = {
        key: summary[key]
        for key in (
            "compacted",
            "compacted_records",
            "context_compacted_events",
            "peak_input_tokens",
            "model_context_window",
            "peak_context_fraction",
        )
    }
    observation["new_compaction"] = compactions > previous_compactions
    return observation, compactions


def find_first_post_compaction_turn(
    turns: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the first turn completed after compaction became observable."""
    previously_compacted = False
    for turn in turns:
        observation = turn.get("session_observation")
        if not isinstance(observation, dict):
            continue
        compacted = bool(observation.get("compacted"))
        if observation.get("new_compaction") or (
            compacted and not previously_compacted
        ):
            return {"id": turn.get("id"), "index": turn.get("index")}
        previously_compacted = compacted
    return None


def find_session_trace(
    thread_id: str,
    sessions_root: Path,
    timeout_seconds: float = 5.0,
) -> Path:
    """Find the persisted JSONL trace for a non-ephemeral Codex task."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        matches = list(sessions_root.rglob(f"*{thread_id}*.jsonl"))
        if matches:
            return max(matches, key=lambda item: item.stat().st_mtime_ns)
        time.sleep(0.1)
    raise RuntimeError(f"no persisted Codex session found for {thread_id}")


def disable_skill_config(skill_paths: Sequence[Path]) -> str:
    """Build one Codex config value that disables mutable skill copies."""
    if not skill_paths:
        return "skills.config=[]"
    items = []
    for path in skill_paths:
        escaped = str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'{{path="{escaped}",enabled=false}}')
    return "skills.config=[" + ",".join(items) + "]"


def _prompt_input_text(prompt_input: str) -> str:
    """Return unescaped strings from prompt-input JSON, or the original text."""
    try:
        value = json.loads(prompt_input)
    except json.JSONDecodeError:
        return prompt_input

    strings: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)

    collect(value)
    return "\n".join(strings)


def verify_discovery(
    prompt_input: str,
    skill_name: str,
    expected_skill: Path,
    disabled_skills: Sequence[Path],
) -> dict[str, Any]:
    """Require one discovered skill name at the immutable snapshot path."""
    text = _prompt_input_text(prompt_input)
    expected = str(expected_skill.resolve())
    disabled = [str(path.resolve()) for path in disabled_skills]
    entry_pattern = re.compile(
        rf"^- {re.escape(skill_name)}: .*?\(file: ([^)]+)\)$",
        flags=re.MULTILINE,
    )
    discovered_paths = _resolve_discovered_skill_paths(
        entry_pattern.findall(text), text
    )
    disabled_occurrences = {
        path: discovered_paths.count(path) for path in disabled
    }
    result = {
        "skill_name": skill_name,
        "skill_entries": len(discovered_paths),
        "expected_skill": expected,
        "expected_path_occurrences": discovered_paths.count(expected),
        "disabled_path_occurrences": disabled_occurrences,
    }
    result["verified"] = (
        result["skill_entries"] == 1
        and result["expected_path_occurrences"] == 1
        and not any(disabled_occurrences.values())
    )
    return result


def _resolve_discovered_skill_paths(
    values: Sequence[str], prompt_input: str
) -> list[str]:
    """Resolve absolute and skill-root-aliased discovery paths."""

    root_pattern = re.compile(r"^- `([^`]+)` = `([^`]+)`$", flags=re.MULTILINE)
    roots = {name: Path(path) for name, path in root_pattern.findall(prompt_input)}
    resolved: list[str] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            root_name, separator, relative = value.partition("/")
            if separator and root_name in roots:
                path = roots[root_name] / relative
        resolved.append(str(path.resolve()))
    return resolved


def capture_state(source: Path, destination: Path) -> None:
    """Copy trial-visible files while excluding runner infrastructure."""
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(*STATE_EXCLUDES),
    )


def state_manifest(root: Path) -> dict[str, dict[str, str]]:
    """Describe files and symlinks in a captured workspace state."""
    manifest: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            manifest[relative] = {"kind": "symlink", "target": str(path.readlink())}
        elif path.is_file():
            manifest[relative] = {"kind": "file", "sha256": sha256_file(path)}
    return manifest


def compare_states(before: Path, after: Path) -> dict[str, list[str]]:
    """Return changed, added, and deleted paths between captured states."""
    left = state_manifest(before)
    right = state_manifest(after)
    return {
        "changed": sorted(
            path for path in left.keys() & right.keys() if left[path] != right[path]
        ),
        "added": sorted(right.keys() - left.keys()),
        "deleted": sorted(left.keys() - right.keys()),
    }


def create_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    """Copy one skill package to a new content-addressed trial snapshot."""
    if destination.exists():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(*STATE_EXCLUDES),
    )
    result = {
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "sha256": tree_hash(destination),
    }
    _write_json(destination.parent / f"{destination.name}.snapshot.json", result)
    return result


def prepare_workspace(
    template: Path,
    workspace: Path,
    snapshot_root: Path,
    skill_name: str,
) -> None:
    """Create an isolated Git fixture bound to one skill snapshot."""
    if workspace.exists():
        raise FileExistsError(f"trial workspace already exists: {workspace}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, workspace, symlinks=True)
    for root in (workspace / ".agents" / "skills", workspace / "skills"):
        root.mkdir(parents=True, exist_ok=True)
        (root / skill_name).symlink_to(
            snapshot_root.resolve(), target_is_directory=True
        )
    subprocess.run(
        ["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent Evaluation",
            "-c",
            "user.email=agent-eval@example.invalid",
            "commit",
            "-m",
            "evaluation fixture baseline",
        ],
        cwd=workspace,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def codex_version(codex_bin: str) -> str:
    """Return the installed Codex CLI version string."""
    completed = subprocess.run(
        [codex_bin, "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def run_discovery_capture(
    *,
    codex_bin: str,
    cwd: Path,
    skill_name: str,
    expected_skill: Path,
    disabled_skills: Sequence[Path],
    prompt: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Capture model-visible skill discovery and verify snapshot isolation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            codex_bin,
            "debug",
            "prompt-input",
            "-c",
            disable_skill_config(disabled_skills),
            prompt,
        ],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    (output_dir / "prompt-input.json").write_text(
        completed.stdout, encoding="utf-8"
    )
    (output_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    verification = verify_discovery(
        completed.stdout, skill_name, expected_skill, disabled_skills
    )
    verification["returncode"] = completed.returncode
    _write_json(output_dir / "verification.json", verification)
    return verification


def _run_turn(
    *,
    codex_bin: str,
    workspace: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    disabled_skills: Sequence[Path],
    sandbox: str,
    thread_id: str | None,
) -> subprocess.CompletedProcess[str]:
    """Run one fresh or resumed Codex turn."""
    common = [
        "--json",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-c",
        disable_skill_config(disabled_skills),
    ]
    if thread_id is None:
        command = [
            codex_bin,
            "exec",
            *common,
            "--sandbox",
            sandbox,
            "-C",
            str(workspace),
            "-",
        ]
    else:
        command = [codex_bin, "exec", "resume", *common, thread_id, "-"]
    return subprocess.run(
        command,
        cwd=workspace,
        input=prompt,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def run_sequence(args: argparse.Namespace) -> int:
    """Run one isolated multi-turn sequence and retain its complete evidence."""
    sequence_path = args.sequence_file.resolve()
    sequence = load_sequence(sequence_path)
    snapshot_root = args.snapshot_root.resolve()
    snapshot_hash = tree_hash(snapshot_root)
    workspace = args.workspace.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir == workspace or output_dir.is_relative_to(workspace):
        raise ValueError("artifact output must be outside the disposable workspace")
    prepare_workspace(
        args.template.resolve(), workspace, snapshot_root, args.skill_name
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(sequence_path, output_dir / "sequence.json")
    _write_json(
        output_dir / "trial-config.json",
        {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "codex_version": codex_version(args.codex_bin),
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "sandbox": args.sandbox,
            "skill_name": args.skill_name,
            "disabled_skills": [
                str(path.resolve()) for path in args.disable_skill
            ],
            "snapshot_root": str(snapshot_root),
            "snapshot_sha256": snapshot_hash,
            "sequence_sha256": sha256_file(sequence_path),
            "compaction_policy": args.compaction_policy,
        },
    )

    first_prompt = sequence.turns[0].prompt_path.read_text(encoding="utf-8")
    discovery = run_discovery_capture(
        codex_bin=args.codex_bin,
        cwd=workspace,
        skill_name=args.skill_name,
        expected_skill=snapshot_root / "SKILL.md",
        disabled_skills=args.disable_skill,
        prompt=first_prompt,
        output_dir=output_dir / "discovery",
    )
    if discovery["returncode"] != 0 or not discovery["verified"]:
        raise RuntimeError("skill discovery isolation preflight failed")

    thread_id: str | None = None
    trace_path: Path | None = None
    turn_reports: list[dict[str, Any]] = []
    compacted = False
    observed_compactions = 0
    first_post_compaction_turn: dict[str, Any] | None = None
    for index, turn in enumerate(sequence.turns, start=1):
        prompt = turn.prompt_path.read_text(encoding="utf-8")
        turn_dir = output_dir / f"turn-{index:02d}-{turn.turn_id}"
        turn_dir.mkdir()
        (turn_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        capture_state(workspace, turn_dir / "state-before")
        completed = _run_turn(
            codex_bin=args.codex_bin,
            workspace=workspace,
            prompt=prompt,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            disabled_skills=args.disable_skill,
            sandbox=args.sandbox,
            thread_id=thread_id,
        )
        (turn_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
        (turn_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
        capture_state(workspace, turn_dir / "state-after")

        report = parse_event_lines(completed.stdout)
        require_event_contract(report, first_turn=thread_id is None)
        reported_thread = report.get("thread_id")
        if isinstance(reported_thread, str) and reported_thread:
            if thread_id is not None and reported_thread != thread_id:
                raise RuntimeError("Codex resume returned a different thread id")
            thread_id = reported_thread
        if thread_id is None:
            raise RuntimeError("Codex did not expose a task id")
        trace_path = find_session_trace(thread_id, args.sessions_root)
        session = parse_session_trace(trace_path.read_text(encoding="utf-8"))
        require_session_contract(session)
        observation, current_compactions = session_observation(
            session, observed_compactions
        )
        if observation["new_compaction"] and first_post_compaction_turn is None:
            first_post_compaction_turn = {"id": turn.turn_id, "index": index}
        observed_compactions = current_compactions
        report.update(
            {
                "id": turn.turn_id,
                "index": index,
                "prompt": str(turn.prompt_path),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "returncode": completed.returncode,
                "state_delta": compare_states(
                    turn_dir / "state-before", turn_dir / "state-after"
                ),
                "session_observation": observation,
            }
        )
        _write_json(turn_dir / "summary.json", report)
        turn_reports.append(report)
        compacted = compacted or bool(report["compacted"] or session["compacted"])
        if completed.returncode != 0:
            break
        if compacted and args.compaction_policy == "stop":
            break

    if trace_path is None:
        raise RuntimeError("evaluation produced no persisted session trace")
    copied_trace = output_dir / "session.jsonl"
    shutil.copy2(trace_path, copied_trace)
    session_summary = parse_session_trace(copied_trace.read_text(encoding="utf-8"))
    require_session_contract(session_summary)
    if tree_hash(snapshot_root) != snapshot_hash:
        raise RuntimeError("skill snapshot changed during the evaluation")

    final_report = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "name": sequence.name,
        "thread_id": thread_id,
        "codex_version": codex_version(args.codex_bin),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "sandbox": args.sandbox,
        "skill_name": args.skill_name,
        "disabled_skills": [str(path.resolve()) for path in args.disable_skill],
        "snapshot_root": str(snapshot_root),
        "snapshot_sha256": snapshot_hash,
        "sequence_sha256": sha256_file(sequence_path),
        "turns": turn_reports,
        "session_trace": str(trace_path),
        "session": session_summary,
        "compacted": compacted or bool(session_summary["compacted"]),
        "compaction_policy": args.compaction_policy,
        "completed_turns": len(turn_reports),
        "expected_turns": len(sequence.turns),
        "first_post_compaction_turn": (
            first_post_compaction_turn
            or find_first_post_compaction_turn(turn_reports)
        ),
    }
    _write_json(output_dir / "sequence-summary.json", final_report)
    complete = len(turn_reports) == len(sequence.turns)
    successful = complete and all(report["returncode"] == 0 for report in turn_reports)
    if final_report["compacted"] and args.compaction_policy == "stop":
        return 1
    return 0 if successful else 1


def inspect_artifacts(artifacts: Path) -> dict[str, Any]:
    """Build a scorer-ready summary from retained sequence artifacts."""
    sequence_path = artifacts / "sequence-summary.json"
    sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    turns = []
    for turn_dir in sorted(artifacts.glob("turn-*")):
        turns.append(
            {
                "turn": turn_dir.name,
                "state_delta": compare_states(
                    turn_dir / "state-before", turn_dir / "state-after"
                ),
                "summary": json.loads(
                    (turn_dir / "summary.json").read_text(encoding="utf-8")
                ),
            }
        )
    trace = artifacts / "session.jsonl"
    if trace.is_file():
        session = parse_session_trace(trace.read_text(encoding="utf-8"))
    else:
        session = sequence.get("session")
        if not isinstance(session, dict):
            raise RuntimeError("artifacts contain no readable session summary")
    boundary = sequence.get("first_post_compaction_turn")
    if boundary is None:
        raw_turns = sequence.get("turns")
        if isinstance(raw_turns, list):
            boundary = find_first_post_compaction_turn(raw_turns)
    return {
        "artifact_schema_version": sequence.get("artifact_schema_version", 0),
        "name": sequence.get("name"),
        "model": sequence.get("model"),
        "reasoning_effort": sequence.get("reasoning_effort"),
        "completed_turns": sequence.get("completed_turns"),
        "expected_turns": sequence.get("expected_turns"),
        "compacted": sequence.get("compacted", session.get("compacted")),
        "peak_input_tokens": session.get("peak_input_tokens"),
        "model_context_window": session.get("model_context_window"),
        "peak_context_fraction": session.get("peak_context_fraction"),
        "first_post_compaction_turn": boundary,
        "turns": turns,
    }


def run_snapshot(args: argparse.Namespace) -> int:
    """Create one immutable-by-hash skill snapshot."""
    print(json.dumps(create_snapshot(args.source, args.destination), sort_keys=True))
    return 0


def run_inspect(args: argparse.Namespace) -> int:
    """Print and optionally retain the generic artifact summary."""
    report = inspect_artifacts(args.artifacts.resolve())
    if args.output is not None:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    """Check the non-model Codex CLI surface required by the runner."""
    checks: dict[str, Any] = {
        "codex_version": codex_version(args.codex_bin),
        "sessions_root": str(args.sessions_root.resolve()),
        "sessions_root_exists": args.sessions_root.is_dir(),
    }
    for name, command in {
        "exec_help": [args.codex_bin, "exec", "--help"],
        "resume_help": [args.codex_bin, "exec", "resume", "--help"],
        "prompt_input_help": [args.codex_bin, "debug", "prompt-input", "--help"],
    }.items():
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        checks[name] = completed.returncode == 0
    checks["compatible"] = checks["sessions_root_exists"] and all(
        checks[name] for name in ("exec_help", "resume_help", "prompt_input_help")
    )
    if args.output is not None:
        _write_json(args.output, checks)
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if checks["compatible"] else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the retained agent-evaluation CLI."""
    parser = argparse.ArgumentParser(
        description="Run opt-in isolated Codex agent behavior evaluations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--codex-bin", default="codex")
    doctor.add_argument(
        "--sessions-root", type=Path, default=Path.home() / ".codex" / "sessions"
    )
    doctor.add_argument("--output", type=Path)
    doctor.set_defaults(handler=run_doctor)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--destination", type=Path, required=True)
    snapshot.set_defaults(handler=run_snapshot)

    sequence = subparsers.add_parser("sequence")
    sequence.add_argument("--template", type=Path, required=True)
    sequence.add_argument("--workspace", type=Path, required=True)
    sequence.add_argument("--snapshot-root", type=Path, required=True)
    sequence.add_argument("--skill-name", required=True)
    sequence.add_argument(
        "--disable-skill", type=Path, action="append", default=[]
    )
    sequence.add_argument("--sequence-file", type=Path, required=True)
    sequence.add_argument("--output-dir", type=Path, required=True)
    sequence.add_argument("--model", required=True)
    sequence.add_argument("--reasoning-effort", required=True)
    sequence.add_argument("--codex-bin", default="codex")
    sequence.add_argument(
        "--sessions-root", type=Path, default=Path.home() / ".codex" / "sessions"
    )
    sequence.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write"),
        default="workspace-write",
    )
    sequence.add_argument(
        "--compaction-policy", choices=("stop", "continue"), default="stop"
    )
    sequence.set_defaults(handler=run_sequence)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--artifacts", type=Path, required=True)
    inspect.add_argument("--output", type=Path)
    inspect.set_defaults(handler=run_inspect)
    return parser


def main() -> int:
    """Run the selected agent-evaluation lifecycle command."""
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"agent behavior evaluation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
