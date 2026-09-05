"""Bounded v2 recorded-command discovery without script-internal inference."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, MutableMapping, NoReturn, Sequence

from research_log_data import (
    DataContractError,
    DataFile,
    FingerprintObservation,
    InputResource,
    input_token_candidate,
    input_token_parts,
    require_git_repository_token_pairs,
    resolve_input_token,
    verify_fingerprint,
)

from .entry_materials import (
    EntryMaterialPathError,
    is_entry_material_path,
    is_entry_material_root,
)
from .errors import MechanicalContractError
from .filesystem import BoundedTraversalError, bounded_descendants
from .json_codec import canonical_json
from .pyrun_contract import (
    OptionOccurrence,
    automatic_option_role,
    parse_pyrun_arguments,
    split_argument_values,
)
from .pyrun_outputs import portable_output_path
from .static_shell import (
    StaticCommand,
    StaticFailure,
    StaticGroup,
    StaticShellResourceError,
    StaticToken,
    expand_static_shell,
)

MAX_INVOCATIONS_PER_FENCE = 64
MAX_INVOCATIONS_PER_LOG = 1000
MAX_STATIC_BINDINGS_PER_FENCE = 256
MAX_STATIC_TOKENS_PER_FENCE = 4096
MAX_STATIC_WORK_ITEMS_PER_FENCE = 4096
MAX_RELATIONSHIPS = 128
MAX_COLLECTION_MEMBERS = 100_000
MAX_COMMAND_BYTES = 1024 * 1024
MAX_FENCE_BYTES = MAX_COMMAND_BYTES * MAX_INVOCATIONS_PER_FENCE
MAX_PATH_BYTES = 512
SCRIPT_HASH_CHUNK_BYTES = 1024 * 1024

FENCE_RE = re.compile(r"^(?P<marker>`{3,}|~{3,})(?P<info>[^`~]*)$")
HEADING_RE = re.compile(r"^##[ \t]+.+$")
BLOCK_LABEL_RE = re.compile(r"^[ \t]*`(?P<label>Steps|Results):`[ \t]*$")
SHELL_LANGUAGES = frozenset({"bash", "console", "sh", "shell", "zsh"})
MATERIAL_SUFFIXES = frozenset(
    {
        ".csv",
        ".feather",
        ".fit",
        ".fits",
        ".h5",
        ".hdf5",
        ".ini",
        ".jpeg",
        ".jpg",
        ".json",
        ".jsonl",
        ".log",
        ".mat",
        ".npy",
        ".npz",
        ".parquet",
        ".pdf",
        ".pickle",
        ".pkl",
        ".png",
        ".svg",
        ".toml",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }
)


class CommandV2Error(MechanicalContractError):
    """One precise command-discovery or collection failure."""


@dataclass(frozen=True)
class MaterialRelationship:
    """One mechanically proved command-material direction."""

    path: str
    direction: str
    proof: str
    target: str | None = None
    named_input: str | None = None
    origin: bool = False
    input_resource: InputResource | None = None


@dataclass(frozen=True)
class MaterialCollection:
    """One completely enumerated finite command collection."""

    direction: str
    mechanism: str
    target: str
    members: tuple[str, ...]
    root: str | None = None


@dataclass(frozen=True)
class ScriptObservation:
    """One stable script digest and its matching cache identity."""

    digest: str
    size: int
    mtime_ns: int
    ctime_ns: int

    def as_cache_record(self) -> Mapping[str, object]:
        return {
            "ctime_ns": self.ctime_ns,
            "mtime_ns": self.mtime_ns,
            "sha256": self.digest,
            "size": self.size,
        }


@dataclass(frozen=True)
class Invocation:
    """One supported top-level invocation and its visible relationships."""

    identity: str
    document: str
    entry: str
    fence: int
    ordinal: int
    sequence: int
    tokens: tuple[str, ...]
    executable: str
    script_argument: str | None
    parameters: tuple[str, ...]
    script: str | None
    script_identity: str | None
    inputs: tuple[MaterialRelationship, ...]
    outputs: tuple[MaterialRelationship, ...]
    collections: tuple[MaterialCollection, ...]
    candidates: tuple[str, ...]
    material_owner: str


@dataclass(frozen=True)
class DiscoveryResult:
    """All supported invocations found in one command document."""

    invocations: tuple[Invocation, ...]
    failures: tuple[CommandDiscoveryFailure, ...]


@dataclass(frozen=True)
class CommandDiscoveryFailure:
    """One failed concrete command that does not invalidate its peers."""

    fence: int
    ordinal: int
    error: CommandV2Error


@dataclass(frozen=True)
class _ParsedCommand:
    tokens: tuple[str, ...]
    executable_index: int
    script_index: int | None
    parameters: tuple[str, ...]
    options: tuple[OptionOccurrence, ...]
    positionals: tuple[str, ...]
    capture_outputs: tuple[tuple[str, str], ...]
    runner_roles: Mapping[str, str]
    static_projection: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandContext:
    log_id: str
    entry: str
    document: str
    entry_root: Path
    log_root: Path
    project_root: Path
    data_file: DataFile | None
    require_experimental_context: bool = True
    input_fingerprint_verifier: (
        Callable[[InputResource], FingerprintObservation | None] | None
    ) = None
    script_identity_cache: MutableMapping[str, ScriptObservation] | None = None
    script_identity_observer: Callable[[Path], ScriptObservation] | None = None


@dataclass(frozen=True)
class _InvocationPosition:
    fence: int
    ordinal: int
    sequence: int
    duplicate: int


@dataclass(frozen=True)
class _RoleState:
    context: CommandContext
    relationships: list[MaterialRelationship]
    collections: list[MaterialCollection]


@dataclass(frozen=True)
class _RelationshipRequest:
    value: str
    direction: str
    proof: str
    target: str | None
    expanded: bool = False
    portable_output: bool = False


def discover_commands(
    text: str,
    context: CommandContext,
) -> DiscoveryResult:
    """Discover bounded visible invocation relationships in one Markdown file."""

    context = CommandContext(
        context.log_id,
        context.entry,
        context.document,
        context.entry_root.resolve(),
        context.log_root.resolve(),
        context.project_root.resolve(),
        context.data_file,
        context.require_experimental_context,
        context.input_fingerprint_verifier,
        context.script_identity_cache,
        context.script_identity_observer,
    )
    invocations: list[Invocation] = []
    command_failures: list[CommandDiscoveryFailure] = []
    duplicate_counts: dict[str, int] = {}
    concrete_invocations = 0
    for fence_number, (body, eligible) in enumerate(_command_fences(text), 1):
        parsed, failures = _parse_fence(body)
        if failures:
            command_failures.append(
                CommandDiscoveryFailure(
                    fence_number,
                    1,
                    CommandV2Error(
                        "invocation.command.unsupported",
                        f"{context.document}:fence-{fence_number}",
                        {"reason": failures[0]},
                        "Recorded-Command Provenance And Material Graph",
                    ),
                )
            )
            continue
        if context.require_experimental_context and not eligible:
            continue
        concrete_ordinal = 0
        for command in parsed:
            if command is None:
                continue
            concrete_ordinal += 1
            concrete_invocations += 1
            if concrete_invocations > MAX_INVOCATIONS_PER_LOG:
                _fail(
                    "provenance.resource.too_large",
                    context.document,
                    {
                        "invocations": concrete_invocations,
                        "limit": MAX_INVOCATIONS_PER_LOG,
                    },
                )
            canonical_value: object = list(command.tokens)
            if command.static_projection:
                canonical_value = [canonical_value, list(command.static_projection)]
            canonical = canonical_json(canonical_value)
            duplicate = duplicate_counts.get(canonical, 0)
            try:
                invocation = _build_invocation(
                    command,
                    context,
                    _InvocationPosition(
                        fence_number,
                        concrete_ordinal,
                        len(invocations),
                        duplicate,
                    ),
                )
            except CommandV2Error as error:
                command_failures.append(
                    CommandDiscoveryFailure(fence_number, concrete_ordinal, error)
                )
                continue
            duplicate_counts[canonical] = duplicate + 1
            invocations.append(invocation)
    return DiscoveryResult(
        tuple(invocations), tuple(command_failures)
    )


def order_invocations(
    documents: Sequence[Sequence[Invocation]],
) -> tuple[Invocation, ...]:
    """Assign global sequence from caller-supplied research-record order."""

    ordered = [invocation for document in documents for invocation in document]
    if len(ordered) > MAX_INVOCATIONS_PER_LOG:
        _fail(
            "provenance.resource.too_large",
            "maintained log",
            {"invocations": len(ordered), "limit": MAX_INVOCATIONS_PER_LOG},
        )
    return tuple(
        replace(invocation, sequence=sequence)
        for sequence, invocation in enumerate(ordered)
    )


def command_input_names(
    text: str, *, require_experimental_context: bool = True
) -> frozenset[str]:
    """Return data-token names present in statically parsed command arguments."""

    names: set[str] = set()
    for body, eligible in _command_fences(text):
        if require_experimental_context and not eligible:
            continue
        parsed, _ = _parse_fence(body)
        for command in parsed:
            if command is None:
                continue
            values = [item.value for item in command.options]
            values.extend(command.positionals)
            for value in values:
                parts = input_token_parts(value)
                if parts is not None:
                    names.add(parts[0])
    return frozenset(names)


def _command_fences(text: str) -> list[tuple[str, bool]]:
    lines = text.splitlines()
    eligible = _experimental_sections(lines)
    result: list[tuple[str, bool]] = []
    index = 0
    while index < len(lines):
        opening = FENCE_RE.fullmatch(lines[index].strip())
        if opening is None:
            index += 1
            continue
        start = index
        marker = opening.group("marker")
        language = opening.group("info").strip().lower()
        index += 1
        body: list[str] = []
        while (
            index < len(lines)
            and re.fullmatch(
                rf"{re.escape(marker[0])}{{{len(marker)},}}\s*", lines[index].strip()
            )
            is None
        ):
            body.append(lines[index])
            index += 1
        index += 1
        if language in SHELL_LANGUAGES:
            result.append(("\n".join(body), eligible[start]))
    return result


def _experimental_sections(lines: Sequence[str]) -> tuple[bool, ...]:
    section = 0
    line_sections: list[int] = []
    labels: dict[int, set[str]] = {0: set()}
    fence: str | None = None
    for line in lines:
        opening = FENCE_RE.fullmatch(line.strip()) if fence is None else None
        if opening is not None:
            fence = opening.group("marker")
        elif fence is not None and re.fullmatch(
            rf"{re.escape(fence[0])}{{{len(fence)},}}\s*", line.strip()
        ):
            fence = None
        elif fence is None and HEADING_RE.fullmatch(line):
            section += 1
            labels[section] = set()
        elif fence is None:
            label = BLOCK_LABEL_RE.fullmatch(line)
            if label is not None:
                labels[section].add(label.group("label"))
        line_sections.append(section)
    experimental = {
        number for number, found in labels.items() if {"Steps", "Results"} <= found
    }
    return tuple(number in experimental for number in line_sections)


def _parse_fence(body: str) -> tuple[list[_ParsedCommand | None], list[str]]:
    if len(body.encode("utf-8")) > MAX_FENCE_BYTES:
        _fail(
            "provenance.resource.too_large",
            "command fence",
            {"bytes": len(body.encode("utf-8")), "limit": MAX_FENCE_BYTES},
        )
    logical = re.sub(r"\\\r?\n", " ", body)
    longest_line = max(
        (len(line.encode("utf-8")) for line in logical.splitlines()), default=0
    )
    if longest_line > MAX_COMMAND_BYTES:
        _fail(
            "provenance.resource.too_large",
            "static shell line",
            {"bytes": longest_line, "limit": MAX_COMMAND_BYTES},
        )
    commands: list[_ParsedCommand | None] = []
    failures: list[str] = []
    try:
        expanded = expand_static_shell(
            body,
            maximum_bindings=MAX_STATIC_BINDINGS_PER_FENCE,
            maximum_tokens=MAX_STATIC_TOKENS_PER_FENCE,
            maximum_work=MAX_STATIC_WORK_ITEMS_PER_FENCE,
        )
    except StaticShellResourceError as exc:
        _fail(
            "provenance.resource.too_large",
            f"static shell {exc.resource}",
            {exc.resource: exc.observed, "limit": exc.limit},
        )
    except ValueError as exc:
        return [None], [str(exc)]
    for item in expanded:
        parsed, unsupported = _parse_static_item(item)
        commands.extend(parsed)
        failures.extend(unsupported)
    invocation_count = sum(command is not None for command in commands)
    if invocation_count > MAX_INVOCATIONS_PER_FENCE:
        _fail(
            "provenance.resource.too_large",
            "command fence",
            {
                "invocations": invocation_count,
                "limit": MAX_INVOCATIONS_PER_FENCE,
            },
        )
    return commands, failures


def _parse_static_item(
    item: StaticCommand | StaticFailure | StaticGroup,
) -> tuple[list[_ParsedCommand | None], list[str]]:
    if isinstance(item, StaticFailure):
        return [None], [item.reason]
    if isinstance(item, StaticGroup):
        commands: list[_ParsedCommand | None] = []
        for command in item.commands:
            parsed, failures = _parse_static_item(command)
            if failures:
                return [None], [failures[0]]
            commands.extend(parsed)
        return commands, []
    _require_command_bound(item.text)
    try:
        return [_parse_command(item.tokens, item.projection)], []
    except ValueError as exc:
        return [None], [str(exc)]


def _require_command_bound(segment: str) -> None:
    encoded_bytes = len(segment.encode("utf-8"))
    if encoded_bytes > MAX_COMMAND_BYTES:
        _fail(
            "provenance.resource.too_large",
            "command invocation",
            {"bytes": encoded_bytes, "limit": MAX_COMMAND_BYTES},
        )


def _parse_command(
    parsed_tokens: Sequence[StaticToken], static_projection: tuple[str, ...] = ()
) -> _ParsedCommand:
    tokens = tuple(token.value for token in parsed_tokens)
    if not tokens:
        raise ValueError("missing executable")
    if any(token.operator for token in parsed_tokens):
        raise ValueError("unsupported shell operator")
    if any(
        "$" in token.value
        or "`" in token.value
        or any(character in token.value for character in "*?[]")
        for token in parsed_tokens
    ):
        raise ValueError("dynamic or unsupported shell argument")
    executable_index = 0
    if Path(tokens[0]).name != "pyrun":
        raise ValueError("shell commands must invoke pyrun directly")
    ordinary = list(tokens)
    script_index, parameters, capture_outputs, runner_roles = _pyrun_layout(
        ordinary, executable_index
    )
    argument_start = script_index + 1
    options, positionals = split_argument_values(ordinary[argument_start:])
    return _ParsedCommand(
        tokens,
        executable_index,
        script_index,
        parameters,
        options,
        positionals,
        capture_outputs,
        runner_roles,
        static_projection,
    )


def _pyrun_layout(
    tokens: Sequence[str], executable_index: int
) -> tuple[
    int,
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    Mapping[str, str],
]:
    """Resolve the script, signature, captures, and explicit material roles."""

    layout = parse_pyrun_arguments(tokens[executable_index + 1 :])
    captures = tuple(
        (option.removeprefix("--"), target) for option, target in layout.captures
    )
    return (
        executable_index + 1 + layout.script_index,
        layout.parameters,
        captures,
        dict(layout.roles),
    )




def _build_invocation(
    command: _ParsedCommand,
    context: CommandContext,
    position: _InvocationPosition,
) -> Invocation:
    executable = command.tokens[command.executable_index]
    script_token = (
        command.tokens[command.script_index]
        if command.script_index is not None
        else None
    )
    script, script_identity = _resolve_script(script_token, context)
    relationships, collections, candidates = _relationships(command, context)
    if candidates:
        _fail(
            "material.candidate.unresolved",
            context.document,
            {"candidates": list(candidates)},
        )
    inputs = tuple(item for item in relationships if item.direction == "input")
    outputs = tuple(item for item in relationships if item.direction == "output")
    input_slots = _relationship_slots(inputs, collections, "input")
    output_slots = _relationship_slots(outputs, collections, "output")
    if input_slots > MAX_RELATIONSHIPS or output_slots > MAX_RELATIONSHIPS:
        _fail(
            "provenance.resource.too_large",
            context.document,
            {"inputs": input_slots, "outputs": output_slots},
        )
    identity_payload: list[object] = [
        context.log_id,
        context.entry,
        context.document,
        list(command.tokens),
    ]
    if command.static_projection:
        identity_payload.append(list(command.static_projection))
    identity_payload.extend((script or script_token, position.duplicate))
    identity = hashlib.sha256(canonical_json(identity_payload).encode()).hexdigest()
    return Invocation(
        identity,
        context.document,
        context.entry,
        position.fence,
        position.ordinal,
        position.sequence,
        command.tokens,
        executable,
        script_token,
        command.parameters,
        script,
        script_identity,
        inputs,
        outputs,
        collections,
        candidates,
        _material_owner(context),
    )


def _relationship_slots(
    relationships: Sequence[MaterialRelationship],
    collections: Sequence[MaterialCollection],
    direction: str,
) -> int:
    """Count authored material slots without charging for directory expansion."""
    scalar_relationships = sum(
        relationship.proof != "directory"
        for relationship in relationships
        if relationship.direction == direction
    )
    directory_collections = sum(
        collection.mechanism == "directory" and collection.direction == direction
        for collection in collections
    )
    return scalar_relationships + directory_collections


def _material_owner(context: CommandContext) -> str:
    try:
        return context.entry_root.relative_to(context.log_root).as_posix()
    except ValueError:
        _fail(
            "material.unresolved",
            context.document,
            {
                "entry_root": context.entry_root.as_posix(),
                "log_root": context.log_root.as_posix(),
            },
        )


def _resolve_script(
    token: str | None, context: CommandContext
) -> tuple[str | None, str | None]:
    if token is None or any(character in token for character in "$`*?[]{}"):
        return token, None
    path = _expand_path(token, context)
    if (
        path is None
        or not _within(path.resolve(), context.project_root)
        or not path.is_file()
        or path.is_symlink()
    ):
        return token, None
    canonical = path.resolve().as_posix()
    observation = _reusable_script_observation(path, canonical, context)
    if observation is not None:
        return canonical, observation.digest
    observation = (
        context.script_identity_observer(path)
        if context.script_identity_observer is not None
        else _observe_script(path)
    )
    if context.script_identity_cache is not None:
        context.script_identity_cache[canonical] = observation
    return canonical, observation.digest


def _reusable_script_observation(
    path: Path, canonical: str, context: CommandContext
) -> ScriptObservation | None:
    """Return a current stable cached or seeded identity when one matches."""

    candidates: list[ScriptObservation | None] = []
    if context.script_identity_cache is not None:
        candidates.append(context.script_identity_cache.get(canonical))
    for candidate in candidates:
        if candidate is None:
            continue
        observation = _validated_script_observation(path, candidate)
        if observation is not None:
            if context.script_identity_cache is not None:
                context.script_identity_cache[canonical] = observation
            return observation
    return None


def _validated_script_observation(
    path: Path, observation: ScriptObservation
) -> ScriptObservation | None:
    """Reuse one identity only when the current script has a stable match."""

    try:
        before = path.stat()
        after = path.stat()
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"error": str(error)},
        )
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "changed_during_observation"},
        )
    expected = (observation.size, observation.mtime_ns, observation.ctime_ns)
    current = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    return observation if current == expected else None


def _observe_script(path: Path) -> ScriptObservation:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(SCRIPT_HASH_CHUNK_BYTES):
                digest.update(chunk)
        after = path.stat()
    except OSError as error:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"error": str(error)},
        )
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        _fail(
            "provenance.observation.unavailable",
            str(path),
            {"reason": "changed_during_observation"},
        )
    return ScriptObservation(
        digest.hexdigest(), after.st_size, after.st_mtime_ns, after.st_ctime_ns
    )


def _relationships(
    command: _ParsedCommand,
    context: CommandContext,
) -> tuple[
    tuple[MaterialRelationship, ...], tuple[MaterialCollection, ...], tuple[str, ...]
]:
    relationships: list[MaterialRelationship] = []
    collections: list[MaterialCollection] = []
    candidates: list[str] = []
    runner_roles = command.runner_roles
    state = _RoleState(context, relationships, collections)
    for target, value in command.capture_outputs:
        relationships.append(
            _relationship(
                _RelationshipRequest(
                    value,
                    "output",
                    "pyrun-capture",
                    target,
                    portable_output=True,
                ),
                context,
            )
        )
    for occurrence in command.options:
        role = runner_roles.get(
            occurrence.name,
            automatic_option_role(occurrence.name),
        )
        if role in {"input", "output"}:
            role = _inferred_runner_role(occurrence.value, role, context)
        _collect_argument(occurrence.value, occurrence.name, role, state, candidates)
    for index, value in enumerate(command.positionals, 1):
        target = f"@{index}"
        role = runner_roles.get(target)
        if role in {"input", "output"}:
            role = _inferred_runner_role(value, role, context)
        _collect_argument(value, target, role, state, candidates)
    collections.extend(_repeated_collections(relationships, context.document))
    try:
        require_git_repository_token_pairs(
            tuple(
                [item.value for item in command.options]
                + list(command.positionals)
            ),
            context.data_file,
        )
    except DataContractError as error:
        _fail(error.code, context.document, error.observed)
    relationships = _deduplicate_relationships(relationships, context.document)
    return tuple(relationships), tuple(collections), tuple(candidates)


def _inferred_runner_role(
    value: str, direction: str | None, context: CommandContext
) -> str | None:
    """Infer a selected argument's file or whole-directory relationship."""

    if direction == "input":
        try:
            resolved = resolve_input_token(value, context.data_file)
        except DataContractError as error:
            _fail(error.code, context.document, error.observed)
        if resolved.resource.kind == "directory" and resolved.member is None:
            return "input-directory"
        return "input"
    if direction == "output":
        path = _expand_path(value, context)
        return "output-directory" if path is not None and path.is_dir() else "output"
    return direction


def _collect_argument(
    value: str,
    target: str,
    role: str | None,
    state: _RoleState,
    candidates: list[str],
) -> None:
    if role is not None:
        _apply_role(value, role, target, state)
        return
    named = _named_input(value, state.context)
    if named is not None:
        state.relationships.append(named)
        return
    candidate = _candidate(value, state.context)
    if candidate is not None:
        candidates.append(candidate)


def _candidate(value: str, context: CommandContext) -> str | None:
    path = _expand_path(value, context)
    if path is not None and is_entry_material_root(path, context.entry_root):
        return None
    if path is not None:
        try:
            if path.exists():
                return _resolved_candidate_value(path, value)
        except OSError:
            pass
    if not _path_like(value):
        return None
    return _resolved_candidate_value(path, value) if path is not None else value


def _resolved_candidate_value(path: Path, fallback: str) -> str:
    try:
        return path.resolve().as_posix()
    except OSError:
        return fallback


def _path_like(value: str) -> bool:
    return (
        "://" in value
        or value.startswith(("/", "./", "../"))
        or input_token_candidate(value)
        or Path(value).suffix in MATERIAL_SUFFIXES
    )


def _repeated_collections(
    relationships: Sequence[MaterialRelationship], subject: str
) -> tuple[MaterialCollection, ...]:
    groups: dict[tuple[str, str], list[str]] = {}
    for relationship in relationships:
        if relationship.proof != "option" or relationship.target is None:
            continue
        groups.setdefault((relationship.direction, relationship.target), []).append(
            relationship.path
        )
    result: list[MaterialCollection] = []
    for (direction, target), members in groups.items():
        if len(members) < 2:
            continue
        _validate_members(members, subject)
        result.append(MaterialCollection(direction, "repeated", target, tuple(members)))
    return tuple(result)


def _apply_role(
    value: str,
    role: str,
    target: str,
    state: _RoleState,
) -> None:
    context = state.context
    if "=" in value:
        _fail(
            "invocation.path_value.embedded",
            context.document,
            {"target": target, "value": value},
        )
    _reject_entry_material_root(value, target, context)
    direction = "input" if role.startswith("input") else "output"
    if role.endswith("-directory"):
        if direction == "input":
            collection, relationships = _named_directory_collection(
                value, target, context
            )
            state.collections.append(collection)
            state.relationships.extend(relationships)
            return
        state.collections.append(
            _directory_collection(
                value,
                direction,
                target,
                context,
                portable_output=True,
            )
        )
        state.relationships.extend(
            _relationship(
                _RelationshipRequest(
                    member, direction, "directory", target, expanded=True
                ),
                context,
            )
            for member in state.collections[-1].members
        )
        return
    named = (
        _named_input(value, context, target=target) if direction == "input" else None
    )
    state.relationships.append(
        named
        or _relationship(
            _RelationshipRequest(
                value,
                direction,
                "option",
                target,
                portable_output=direction == "output",
            ),
            context,
        )
    )


def _named_input(
    value: str, context: CommandContext, *, target: str | None = None
) -> MaterialRelationship | None:
    if input_token_parts(value) is None:
        return None
    try:
        resolved = resolve_input_token(value, context.data_file)
    except DataContractError as error:
        _fail(error.code, context.document, error.observed)
    resource = resolved.resource
    return MaterialRelationship(
        resolved.path,
        "input",
        "named-input",
        target or resource.name,
        resource.name,
        resource.origin,
        resource,
    )


def _relationship(
    request: _RelationshipRequest,
    context: CommandContext,
) -> MaterialRelationship:
    if not request.expanded:
        _reject_entry_material_root(request.value, request.target, context)
    if request.direction == "input" and not request.expanded:
        named = _named_input(request.value, context, target=request.target)
        if named is not None:
            return named
        _reject_raw_input(request.value, context)
    if request.portable_output:
        _require_portable_output(request.value, context)
    path = (
        Path(request.value)
        if request.expanded
        else _expand_path(request.value, context)
    )
    if path is None:
        _fail("material.unresolved", context.document, {"value": request.value})
    return MaterialRelationship(
        path.resolve().as_posix(),
        request.direction,
        request.proof,
        request.target,
    )


def _reject_entry_material_root(
    value: str, target: str | None, context: CommandContext
) -> None:
    path = _expand_path(value, context)
    if path is not None and is_entry_material_root(path, context.entry_root):
        _fail(
            "material.root.invalid",
            context.document,
            {"target": target, "value": value},
        )


def _reject_raw_input(value: str, context: CommandContext) -> NoReturn:
    path = _expand_path(value, context)
    canonical = (
        value
        if "://" in value
        else (path.resolve().as_posix() if path is not None else value)
    )
    matching = []
    if context.data_file is not None:
        for resource in context.data_file.inputs:
            if resource.canonical_target == canonical:
                matching.append(resource.name)
                continue
            if (
                resource.kind == "directory"
                and path is not None
            ):
                try:
                    path.resolve().relative_to(Path(resource.canonical_target))
                except ValueError:
                    continue
                matching.append(resource.name)
    _fail(
        "data.input.token_missing" if matching else "data.input.undeclared",
        context.document,
        {"value": value, "matching": sorted(matching)},
    )


def _expand_path(value: str, context: CommandContext) -> Path | None:
    if len(value.encode("utf-8")) > MAX_PATH_BYTES or any(
        char in value for char in "$`*?[]{}"
    ):
        return None
    expanded = value.replace("<project>", context.project_root.as_posix()).replace(
        "<log>", context.log_root.as_posix()
    )
    if re.search(r"<[A-Za-z0-9_-]+>", expanded):
        return None
    path = Path(expanded)
    return path if path.is_absolute() else context.entry_root / path


def _named_directory_collection(
    value: str, target: str, context: CommandContext
) -> tuple[MaterialCollection, tuple[MaterialRelationship, ...]]:
    try:
        resolved = resolve_input_token(value, context.data_file)
        if resolved.member is not None or resolved.resource.kind != "directory":
            _fail(
                "directory.membership.invalid",
                context.document,
                {"value": value, "reason": "not_whole_directory"},
            )
        observation = (
            context.input_fingerprint_verifier(resolved.resource)
            if context.input_fingerprint_verifier is not None
            else verify_fingerprint(resolved.resource)
        )
    except DataContractError as error:
        _fail(error.code, context.document, error.observed)
    if observation is None:
        _fail(
            "directory.membership.invalid",
            context.document,
            {"value": value, "reason": "observation_unavailable"},
        )
    resource = resolved.resource
    if resource.fingerprint.algorithm in {
        "identity-files-sha256-v1",
        "identity-patterns-sha256-v1",
    }:
        collection_kind = (
            "identity-patterns"
            if resource.fingerprint.algorithm == "identity-patterns-sha256-v1"
            else "identity-files"
        )
        relationship = MaterialRelationship(
            resolved.path,
            "input",
            collection_kind,
            target,
            resource.name,
            resource.origin,
            resource,
        )
        return (
            MaterialCollection(
                "input",
                collection_kind,
                target,
                (resolved.path,),
                resolved.path,
            ),
            (relationship,),
        )
    members = tuple(
        (Path(resolved.path) / entry.path).resolve().as_posix()
        for entry in observation.entries
        if entry.type == "file"
    )
    _validate_members(members, context.document)
    collection = MaterialCollection(
        "input", "directory", target, members, resolved.path
    )
    relationships = tuple(
        MaterialRelationship(
            member,
            "input",
            "directory",
            target,
            resource.name,
            resource.origin,
            resource,
        )
        for member in members
    )
    return collection, relationships


def _directory_collection(
    value: str,
    direction: str,
    target: str,
    context: CommandContext,
    *,
    portable_output: bool = False,
) -> MaterialCollection:
    root = _expand_path(value, context)
    if root is None or not root.is_dir():
        _fail("collection.membership.invalid", context.document, {"directory": value})
    if portable_output:
        _require_portable_output(value, context)
    elif not _command_path_in_scope(root, context, entry_only=True):
        _fail("collection.membership.invalid", context.document, {"directory": value})
    try:
        descendants = bounded_descendants(root, maximum_entries=MAX_COLLECTION_MEMBERS)
    except BoundedTraversalError as error:
        _fail(
            "collection.membership.invalid",
            context.document,
            {
                "directory": value,
                "limit": error.limit,
                "observed": error.observed,
                "reason": error.reason,
            },
        )
    if any(path.is_symlink() for path in descendants):
        _fail(
            "collection.membership.invalid",
            context.document,
            {"directory": value, "reason": "nested_symlink"},
        )
    members = tuple(path.resolve().as_posix() for path in descendants if path.is_file())
    _validate_members(members, context.document)
    return MaterialCollection(
        direction, "directory", target, members, root.resolve().as_posix()
    )


def _require_portable_output(value: str, context: CommandContext) -> None:
    """Require one authored output to use the shared portable path contract."""

    try:
        portable_output_path(
            value,
            entry_root=context.entry_root,
            project_root=context.project_root,
            authored=True,
        )
    except MechanicalContractError as error:
        _fail(
            "pyrun.output.identity_invalid",
            context.document,
            error.observed,
        )


def _validate_members(members: Sequence[str], subject: str) -> None:
    if (
        not members
        or len(members) > MAX_COLLECTION_MEMBERS
        or len(members) != len(set(members))
    ):
        _fail("collection.membership.invalid", subject, {"members": len(members)})


def _command_path_in_scope(
    path: Path, context: CommandContext, *, entry_only: bool = False
) -> bool:
    try:
        if is_entry_material_path(path, context.entry_root):
            return True
    except EntryMaterialPathError as error:
        _fail(
            "material.unresolved",
            context.document,
            {"path": path.as_posix(), "reason": error.reason},
        )
    boundary = context.entry_root if entry_only else context.log_root
    return _within(path.resolve(), boundary.resolve())


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _deduplicate_relationships(
    values: Sequence[MaterialRelationship], subject: str
) -> list[MaterialRelationship]:
    by_path: dict[str, MaterialRelationship] = {}
    for value in values:
        previous = by_path.get(value.path)
        if previous is not None and previous.direction != value.direction:
            _fail("material.direction.conflict", subject, {"path": value.path})
        by_path.setdefault(value.path, value)
    return list(by_path.values())


def _fail(code: str, subject: str, observed: object) -> NoReturn:
    raise CommandV2Error(
        code, subject, observed, "Recorded-Command Provenance And Material Graph"
    )
