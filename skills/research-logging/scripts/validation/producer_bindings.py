"""Current producer-candidate and recorded-workflow classification rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping, NamedTuple, Optional, Sequence

from .compatibility import invocation_identities
from .contracts import ScanRecord


class WorkflowCommandCheck(NamedTuple):
    """Dependency facts and check details from one recorded command."""

    dependencies: list[dict[str, str]]
    failures: list[str]
    uncertainties: list[str]


class WorkflowMatch(NamedTuple):
    """One recorded command whose exact path argument names a target."""

    command: dict[str, Any]
    argument: dict[str, Any]
    command_index: int


class ProducerCandidateFacts(NamedTuple):
    """Target-independent producer facts prepared for one invocation."""

    output_identities: frozenset[str]
    output_containers: frozenset[str]
    unknown_containers: frozenset[str]
    command_text: str
    section: str


class ProducerCandidateClass(NamedTuple):
    """Current relationship between one invocation and one target."""

    direct: bool
    container: bool
    exact: bool
    section: bool


def resolved_identity_cache(scan: ScanRecord) -> Dict[str, str]:
    """Return one resolved-path lookup for a bounded validation operation."""

    return {
        Path(path).resolve().as_posix(): identity
        for identity, path in scan["resolved_paths"].items()
    }


def identity_for_path(
    scan: ScanRecord,
    raw: str,
    cache: Optional[Mapping[str, str]] = None,
) -> str:
    """Map one resolved path to its scan identity or stable display path."""

    resolved = Path(raw).resolve().as_posix()
    identities = cache if cache is not None else resolved_identity_cache(scan)
    if resolved in identities:
        return identities[resolved]
    path = Path(raw).resolve()
    project_root = Path(scan["project_root"]).resolve()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def prepare_candidate_facts(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identities: Mapping[str, str],
) -> ProducerCandidateFacts:
    """Prepare current v43 producer-candidate facts without target semantics."""

    output_identities = {
        identity_for_path(scan, argument.get("path", ""), identities)
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "output" and argument.get("path")
    }
    output_containers = {
        identity
        for identity in output_identities
        if scan.get("mechanical_checks", {}).get(identity, {}).get("type")
        == "directory"
    }
    unknown_identities = {
        identity_for_path(scan, argument.get("path", ""), identities)
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "unknown"
        and argument.get("path")
    }
    unknown_containers = {
        identity
        for identity in unknown_identities
        if scan.get("mechanical_checks", {}).get(identity, {}).get("type")
        == "directory"
    }
    return ProducerCandidateFacts(
        frozenset(output_identities),
        frozenset(output_containers),
        frozenset(unknown_containers),
        str(command.get("command", "")),
        str(command.get("section", "")),
    )


def classify_candidate(
    facts: ProducerCandidateFacts,
    identity: str,
    sections: Sequence[str],
    searchable_source: str,
) -> ProducerCandidateClass:
    """Classify one prepared invocation against a target under v43 semantics."""

    target_name = Path(identity).name
    direct = identity in facts.output_identities
    container = any(
        identity.startswith(container.rstrip("/") + "/")
        for container in facts.output_containers
    )
    if not container and any(
        identity.startswith(container.rstrip("/") + "/")
        for container in facts.unknown_containers
    ):
        tokens = {
            token
            for token in _identity_tokens(Path(identity).stem)
            if len(token) > 1
        }
        container = bool(tokens) and all(token in searchable_source for token in tokens)
    exact = target_name in facts.command_text or any(
        Path(path).name == target_name for path in facts.output_identities
    )
    return ProducerCandidateClass(
        direct,
        container,
        exact,
        facts.section in sections,
    )


def _identity_tokens(value: str) -> set[str]:
    """Return lowercase alphanumeric tokens used by v43 source matching."""

    return set(re.findall(r"[a-z0-9]+", value.lower()))


def matching_workflow_commands(
    entry: Mapping[str, Any],
    target: str,
    scan: ScanRecord,
    identity_cache: Mapping[str, str],
) -> tuple[list[WorkflowMatch], bool]:
    """Return target-naming commands and whether output direction is explicit."""

    matches = []
    for index, command in enumerate(entry.get("commands", []), 1):
        for argument in command.get("path_arguments", []):
            if identity_for_path(scan, argument["path"], identity_cache) != target:
                continue
            matches.append(WorkflowMatch(command, argument, index))
            break
    confirmed = [
        match for match in matches if match.argument.get("role_hint") == "output"
    ]
    return confirmed or matches, bool(confirmed)


def _producer_command_check(
    command: Mapping[str, Any],
    scan: ScanRecord,
    identity_cache: Mapping[str, str],
) -> WorkflowCommandCheck:
    """Resolve and structurally check one recorded command entrypoint."""

    script = command.get("script")
    if not script or not Path(script).is_file():
        return WorkflowCommandCheck([], [], ["producer script is unresolved"])
    identity = identity_for_path(scan, script, identity_cache)
    dependencies = [{"path": identity, "role": "producer"}]
    structure = scan["mechanical_checks"].get(identity, {})
    failures = (
        [f"producer structure is {structure.get('status', 'unknown')}"]
        if structure.get("status") != "ok"
        else []
    )
    return WorkflowCommandCheck(dependencies, failures, [])


def check_workflow_command(
    command: Mapping[str, Any],
    scan: ScanRecord,
    identity_cache: Optional[Mapping[str, str]] = None,
) -> WorkflowCommandCheck:
    """Inspect one recorded producer command without inferring semantics."""

    identities = (
        identity_cache
        if identity_cache is not None
        else resolved_identity_cache(scan)
    )
    producer = _producer_command_check(command, scan, identities)
    dependencies = list(producer.dependencies)
    failures = list(producer.failures)
    uncertainties = list(producer.uncertainties)
    if command.get("unknown_options"):
        uncertainties.append(
            "recorded command uses unknown options: "
            + ", ".join(command["unknown_options"])
        )
    for token in command.get("data_tokens", []):
        if token["name"] in {"project", "log"}:
            continue
        if token.get("status") != "resolved" or not token.get("path"):
            failures.append(f"input token <{token['name']}> is {token['status']}")
            continue
        identity = identity_for_path(scan, token["path"], identities)
        dependencies.append({"path": identity, "role": "input"})
        if not Path(token["path"]).exists():
            failures.append(f"input is missing: {identity}")
    for argument in command.get("path_arguments", []):
        if argument["role_hint"] != "input":
            continue
        identity = identity_for_path(scan, argument["path"], identities)
        dependencies.append({"path": identity, "role": "input"})
        if not Path(argument["path"]).exists():
            failures.append(f"input is missing: {identity}")
    return WorkflowCommandCheck(dependencies, failures, uncertainties)


def workflow_check(
    entry: dict[str, Any],
    target: str,
    scan: ScanRecord,
    identity_cache: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Check the recorded producer invocation that names one exact target."""

    identities = (
        identity_cache
        if identity_cache is not None
        else resolved_identity_cache(scan)
    )
    selected, direction_confirmed = matching_workflow_commands(
        entry, target, scan, identities
    )
    if not selected:
        return (
            {
                "status": "unresolved",
                "detail": "no explicit producing command matched",
                "matched_commands": 0,
            },
            [],
        )

    if Path(target).is_absolute() and all(
        match.argument.get("role_hint") == "input" for match in selected
    ):
        return (
            {
                "status": "pass",
                "detail": "recorded workflow consumes this retained external input",
                "matched_commands": len(selected),
            },
            [],
        )

    checked_matches = [
        (match, check_workflow_command(match.command, scan, identities))
        for match in selected
    ]
    viable = [pair for pair in checked_matches if not pair[1].failures]
    if not viable:
        failures = [
            failure
            for _match, checked in checked_matches
            for failure in checked.failures
        ]
        return {
            "status": "fail",
            "detail": "; ".join(sorted(set(failures))),
            "matched_commands": len(selected),
        }, []

    if len(viable) > 1:
        return (
            {
                "status": "unresolved",
                "detail": "multiple producing commands require semantic selection",
                "matched_commands": len(viable),
            },
            [],
        )

    match, checked = viable[0]
    uncertainties = list(checked.uncertainties)
    if not direction_confirmed:
        uncertainties.append(
            "command/path direction requires semantic producer confirmation"
        )
    unique_dependencies = [
        dict(item)
        for item in {
            (dependency["path"], dependency["role"]): dependency
            for dependency in checked.dependencies
        }.values()
    ]
    if uncertainties:
        return (
            {
                "status": "unresolved",
                "detail": "; ".join(sorted(set(uncertainties))),
                "matched_commands": 1,
            },
            unique_dependencies,
        )
    return {
        "status": "pass",
        "detail": "matched one recorded command",
        "matched_commands": 1,
        "producer_invocation": invocation_identities(
            entry["id"], entry.get("commands", [])
        )[match.command_index - 1],
    }, unique_dependencies
