"""Current producer-candidate and recorded-workflow classification rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping, NamedTuple, Optional, Sequence

from .compatibility import invocation_identities, normalized_command
from .contracts import ScanRecord, ValidationToolError


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


class WorkflowClassificationContext(NamedTuple):
    """Shared scan and identity lookup for one workflow classification."""

    scan: ScanRecord
    identities: Mapping[str, str]


class ProducerCandidateFacts(NamedTuple):
    """Target-independent producer facts prepared for one invocation."""

    output_identities: frozenset[str]
    output_containers: frozenset[str]
    input_identities: frozenset[str]
    unknown_identities: frozenset[str]
    unknown_containers: frozenset[str]
    command_text: str
    section: str


class ProducerCandidateClass(NamedTuple):
    """Current relationship between one invocation and one target."""

    direct: bool
    container: bool
    exact: bool
    section: bool


class ProducerBindingOptions(NamedTuple):
    """Optional verification inputs kept behind one stable argument."""

    producer_basis: str | None = None
    identity_cache: Mapping[str, str] | None = None


class ProducerEligibility(NamedTuple):
    """Verifiable relationship between one invocation and one target."""

    eligible: bool
    kind: str
    coverage_identity: str
    direction_evidence: str
    target_member: str | None
    review_required: bool
    reason: str


class MechanicalProducerResolution(NamedTuple):
    """One exact producer proven without semantic direction or scope choices."""

    invocation_identity: str
    dependencies: list[dict[str, str]]


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
    project_root = Path(str(scan.get("project_root", "."))).resolve()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def prepare_candidate_facts(
    scan: ScanRecord,
    command: Mapping[str, Any],
    identities: Mapping[str, str],
) -> ProducerCandidateFacts:
    """Prepare current producer-candidate facts without target semantics."""

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
    input_identities = {
        identity_for_path(scan, argument.get("path", ""), identities)
        for argument in command.get("path_arguments", [])
        if argument.get("role_hint") == "input" and argument.get("path")
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
        frozenset(input_identities),
        frozenset(unknown_identities),
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
    exact = identity in facts.output_identities or identity in facts.unknown_identities
    return ProducerCandidateClass(
        direct,
        container,
        exact,
        facts.section in sections,
    )


def _identity_tokens(value: str) -> set[str]:
    """Return lowercase alphanumeric tokens used by v43 source matching."""

    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _containing_identity(identity: str, candidates: Sequence[str]) -> str | None:
    """Return the most specific normalized candidate containing one target."""

    matches = [
        candidate
        for candidate in candidates
        if identity.startswith(candidate.rstrip("/") + "/")
    ]
    return max(matches, key=len) if matches else None


def producer_eligibility(
    scan: ScanRecord,
    command: Mapping[str, Any],
    target: str,
    identity_cache: Optional[Mapping[str, str]] = None,
) -> ProducerEligibility:
    """Return the proof form available from one recorded invocation.

    Filename similarity, source tokens, and section locality never enter this
    decision. They remain useful only when assembling diagnostic context.
    """

    identities = (
        identity_cache
        if identity_cache is not None
        else resolved_identity_cache(scan)
    )
    arguments = [
        (
            identity_for_path(scan, str(argument.get("path", "")), identities),
            str(argument.get("role_hint", "unknown")),
        )
        for argument in command.get("path_arguments", [])
        if argument.get("path")
    ]
    if any(identity == target and role == "input" for identity, role in arguments):
        result = ProducerEligibility(
            False, "", "", "", None, False, "command consumes the target"
        )
    elif "output" in (
        exact_roles := [role for identity, role in arguments if identity == target]
    ):
        result = ProducerEligibility(
            True,
            "exact-target",
            target,
            "mechanical-output-role",
            None,
            False,
            "parsed output argument resolves exactly to the target",
        )
    elif exact_roles:
        result = ProducerEligibility(
            True,
            "exact-target",
            target,
            "reviewed-output-direction",
            None,
            True,
            "exact target is recorded but output direction requires review",
        )

    else:
        input_collection = _containing_identity(
            target, [identity for identity, role in arguments if role == "input"]
        )
        collection = _containing_identity(
            target,
            [
                identity
                for identity, role in arguments
                if role in {"output", "unknown"}
            ],
        )
        if input_collection is not None:
            result = ProducerEligibility(
                False,
                "",
                input_collection,
                "",
                None,
                False,
                "command consumes a collection containing the target",
            )
        elif collection is None:
            result = ProducerEligibility(
                False,
                "",
                "",
                "",
                None,
                False,
                "command has no exact target or containing output collection",
            )
        elif (
            scan.get("resolved_paths", {}).get(collection) is None
            or scan.get("mechanical_checks", {}).get(collection, {}).get("type")
            != "directory"
        ):
            result = ProducerEligibility(
                False,
                "",
                collection,
                "",
                None,
                False,
                "containing command path is not a resolved output collection",
            )
        else:
            member = target[len(collection.rstrip("/") + "/") :]
            result = ProducerEligibility(
                True,
                "scoped-collection",
                collection,
                "reviewed-output-direction",
                member,
                True,
                "containing output collection requires reviewed member scope",
            )
    return result


def exact_mechanical_producer(
    scan: ScanRecord,
    target: str,
    candidates: Sequence[tuple[str, Mapping[str, Any]]],
    identity_cache: Optional[Mapping[str, str]] = None,
) -> MechanicalProducerResolution | None:
    """Resolve one exact producer only when its full workflow is mechanical.

    The target must have exactly one eligible invocation with an explicit
    output role, complete known path arguments, no command uncertainty, no
    duplicate recorded invocation, and a resolved dependency closure. Scoped
    collections and reviewed output direction remain semantic decisions.
    """

    if len(candidates) != 1:
        return None
    invocation_identity, command = candidates[0]
    identities = (
        identity_cache
        if identity_cache is not None
        else resolved_identity_cache(scan)
    )
    eligibility = producer_eligibility(scan, command, target, identities)
    if (
        not eligibility.eligible
        or eligibility.kind != "exact-target"
        or eligibility.coverage_identity != target
        or eligibility.direction_evidence != "mechanical-output-role"
        or eligibility.target_member is not None
        or eligibility.review_required
    ):
        return None
    arguments = command.get("path_arguments", [])
    if (
        not isinstance(arguments, list)
        or not arguments
        or any(
            not isinstance(argument, Mapping)
            or argument.get("exists") is not True
            or argument.get("role_hint") not in {"input", "output"}
            for argument in arguments
        )
    ):
        return None
    checked = check_workflow_command(command, scan, identities)
    if checked.failures or checked.uncertainties:
        return None
    dependencies = [
        dict(dependency)
        for dependency in {
            (item["path"], item["role"]): item for item in checked.dependencies
        }.values()
    ]
    binding = verify_producer_binding(
        scan,
        target,
        invocation_identity,
        dependencies,
        ProducerBindingOptions("mechanical", identities),
    )
    if binding["duplicate_count"] != 1:
        return None
    return MechanicalProducerResolution(invocation_identity, dependencies)


def _invocation_lookup(
    scan: Mapping[str, Any], invocation_identity: str
) -> tuple[str, Mapping[str, Any], Sequence[Mapping[str, Any]]]:
    for entry in scan.get("entries", []):
        entry_id = str(entry.get("id", ""))
        commands = entry.get("commands", [])
        identities = invocation_identities(entry_id, commands)
        for identity, command in zip(identities, commands):
            if identity == invocation_identity:
                return entry_id, command, commands
    raise ValidationToolError(
        f"producer binding names a missing recorded invocation: {invocation_identity}"
    )


def verify_producer_binding(
    scan: ScanRecord,
    target: str,
    invocation_identity: str,
    dependencies: Sequence[Mapping[str, Any]],
    options: ProducerBindingOptions | None = None,
) -> dict[str, Any]:
    """Build and verify one complete native producer binding."""

    entry_id, command, commands = _invocation_lookup(scan, invocation_identity)
    options = options or ProducerBindingOptions()
    identities = options.identity_cache or resolved_identity_cache(scan)
    eligibility = producer_eligibility(scan, command, target, identities)
    if not eligibility.eligible:
        raise ValidationToolError(
            f"producer binding does not cover {target}: {eligibility.reason}"
        )
    checked = check_workflow_command(command, scan, identities)
    if checked.failures:
        raise ValidationToolError(
            "producer binding has deterministic workflow failures: "
            + "; ".join(sorted(set(checked.failures)))
        )
    if eligibility.kind == "scoped-collection":
        scoped = [
            dependency
            for dependency in dependencies
            if dependency.get("path") == eligibility.coverage_identity
            and dependency.get("role") == "producer"
            and isinstance(dependency.get("members"), list)
        ]
        if len(scoped) != 1 or eligibility.target_member not in scoped[0]["members"]:
            raise ValidationToolError(
                "reviewed collection producer omits the target from its exact "
                f"member scope: {target}"
            )
        members = sorted(set(scoped[0]["members"]))
    else:
        members = None
    basis = options.producer_basis or (
        "mechanical"
        if eligibility.direction_evidence == "mechanical-output-role"
        else "reviewed"
    )
    if basis not in {"mechanical", "reviewed", "upstream-reviewed"}:
        raise ValidationToolError(f"producer binding basis is invalid: {basis}")
    if (
        eligibility.direction_evidence == "reviewed-output-direction"
        and basis == "mechanical"
    ):
        raise ValidationToolError(
            "producer binding requires reviewed output direction"
        )
    group = (
        " ".join(str(command.get("section", "")).split()).casefold(),
        normalized_command(str(command.get("command", ""))),
    )
    binding: dict[str, Any] = {
        "kind": eligibility.kind,
        "invocation_identity": invocation_identity,
        "producer_basis": basis,
        "coverage_identity": eligibility.coverage_identity,
        "direction_evidence": eligibility.direction_evidence,
        "duplicate_count": sum(
            (
                " ".join(str(item.get("section", "")).split()).casefold(),
                normalized_command(str(item.get("command", ""))),
            )
            == group
            for item in commands
        ),
        "source_locator": {"entry": entry_id, "line": command.get("line")},
    }
    if members is not None:
        binding.update(
            {
                "target_member": eligibility.target_member,
                "members": members,
            }
        )
    return binding


def verify_persisted_producer_binding(
    scan: ScanRecord,
    target: str,
    binding: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Recheck a persisted binding against the exact current invocation."""

    expected = verify_producer_binding(
        scan,
        target,
        str(binding.get("invocation_identity", "")),
        dependencies,
        ProducerBindingOptions(str(binding.get("producer_basis", ""))),
    )
    semantic_fields = {
        "kind",
        "invocation_identity",
        "producer_basis",
        "coverage_identity",
        "direction_evidence",
        "target_member",
        "members",
        "duplicate_count",
    }
    if {key: binding.get(key) for key in semantic_fields if key in binding} != {
        key: expected.get(key) for key in semantic_fields if key in expected
    }:
        raise ValidationToolError(
            f"producer binding disagrees with current invocation coverage: {target}"
        )
    return expected


def producer_bindings_for_check(
    scan: ScanRecord, check: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Project one check's raw resolutions through the shared verifier."""

    resolution = check.get("resolution")
    if not isinstance(resolution, Mapping):
        return []
    dependencies = [
        item for item in check.get("dependencies", []) if isinstance(item, Mapping)
    ]
    raw: list[tuple[str, str, str | None]] = []
    producer = resolution.get("producer_invocation")
    target = str(check.get("target", ""))
    if isinstance(producer, str):
        raw.append((target, producer, None))
    for binding in resolution.get("producer_bindings", []):
        if isinstance(binding, Mapping):
            raw.append(
                (
                    str(binding.get("material", "")),
                    str(binding.get("invocation", "")),
                    "upstream-reviewed",
                )
            )
    result = [
        verify_producer_binding(
            scan,
            material,
            invocation,
            dependencies,
            ProducerBindingOptions(basis),
        )
        for material, invocation, basis in raw
    ]
    return sorted(
        result,
        key=lambda item: (item["coverage_identity"], item["invocation_identity"]),
    )


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
    if any(
        entry_row.get("path") == target
        for entry_row in scan.get("entries", [])
        if isinstance(entry_row, Mapping)
    ):
        result = {
            "status": "pass",
            "detail": "retained research entry is a terminal source",
            "matched_commands": 0,
        }
        dependencies: list[dict[str, str]] = []
    else:
        selected, direction_confirmed = matching_workflow_commands(
            entry, target, scan, identities
        )
        result, dependencies = _classify_workflow_matches(
            entry,
            target,
            selected,
            direction_confirmed,
            WorkflowClassificationContext(scan, identities),
        )
    return result, dependencies


def _classify_workflow_matches(
    entry: Mapping[str, Any],
    target: str,
    selected: Sequence[WorkflowMatch],
    direction_confirmed: bool,
    context: WorkflowClassificationContext,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Classify already selected workflow matches for one target."""

    if not selected:
        return {
            "status": "unresolved",
            "detail": "no explicit producing command matched",
            "matched_commands": 0,
        }, []
    if Path(target).is_absolute() and all(
        match.argument.get("role_hint") == "input" for match in selected
    ):
        return {
            "status": "pass",
            "detail": "recorded workflow consumes this retained external input",
            "matched_commands": len(selected),
        }, []
    checked_matches = [
        (
            match,
            check_workflow_command(
                match.command, context.scan, context.identities
            ),
        )
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
