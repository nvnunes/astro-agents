"""Bounded v2 recorded-command discovery without script-internal inference."""

from __future__ import annotations

import csv
import hashlib
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Mapping, MutableMapping, NoReturn, Sequence

from .json_codec import canonical_json

MAX_INVOCATIONS_PER_FENCE = 64
MAX_INVOCATIONS_PER_LOG = 1000
MAX_RELATIONSHIPS = 128
MAX_COLLECTION_MEMBERS = 100_000
MAX_COMMAND_BYTES = 1024 * 1024
MAX_FENCE_BYTES = MAX_COMMAND_BYTES * MAX_INVOCATIONS_PER_FENCE
MAX_PATH_BYTES = 512

FENCE_RE = re.compile(r"^(?P<marker>`{3,}|~{3,})(?P<info>[^`~]*)$")
HEADING_RE = re.compile(r"^##[ \t]+.+$")
BLOCK_LABEL_RE = re.compile(r"^[ \t]*`(?P<label>Steps|Results):`[ \t]*$")
ANNOTATION_RE = re.compile(
    r"<!-- command(?P<ordinal>-[1-9][0-9]*)? (?P<body>.*?) -->\Z",
    re.DOTALL,
)
TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
POSITIONAL_RE = re.compile(r"@(?P<number>[1-9][0-9]*)\Z")
ASSIGNMENT_RE = re.compile(r"(?P<target>[^=;]+) = (?P<value>[^=;]+)\Z")
ENVIRONMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*\Z")
ROLE_TOKENS = frozenset(
    {
        "input",
        "output",
        "input-directory",
        "output-directory",
        "input-manifest",
        "output-manifest",
    }
)
SHELL_LANGUAGES = frozenset({"bash", "console", "sh", "shell", "zsh"})
SIMULATION_STEMS = ("simulate", "simulation")


class CommandV2Error(ValueError):
    """One precise command-discovery or collection failure."""

    def __init__(self, code: str, subject: str, observed: object, rule: str):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule


@dataclass(frozen=True)
class MaterialRelationship:
    """One mechanically proved command-material direction."""

    path: str
    direction: str
    proof: str
    target: str | None = None
    named_input: str | None = None
    external: bool = False


@dataclass(frozen=True)
class MaterialCollection:
    """One completely enumerated finite command collection."""

    direction: str
    mechanism: str
    target: str
    members: tuple[str, ...]
    root: str | None = None


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
    script: str | None
    script_identity: str | None
    command_type: str | None
    inputs: tuple[MaterialRelationship, ...]
    outputs: tuple[MaterialRelationship, ...]
    collections: tuple[MaterialCollection, ...]
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    """All supported invocations found in one command document."""

    invocations: tuple[Invocation, ...]
    unsupported: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class _OptionOccurrence:
    name: str
    value: str


@dataclass(frozen=True)
class _ParsedCommand:
    tokens: tuple[str, ...]
    executable_index: int
    script_index: int | None
    options: tuple[_OptionOccurrence, ...]
    positionals: tuple[str, ...]
    redirections: tuple[tuple[str, str], ...]
    tee_outputs: tuple[str, ...]


@dataclass(frozen=True)
class _Annotation:
    ordinal: int
    command_type: str | None
    roles: Mapping[str, str]


@dataclass(frozen=True)
class CommandContext:
    log_id: str
    entry: str
    document: str
    entry_root: Path
    log_root: Path
    project_root: Path
    data_index: Mapping[str, str]
    require_experimental_context: bool = True
    script_identity_cache: MutableMapping[str, str] | None = None


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
        context.data_index,
        context.require_experimental_context,
        context.script_identity_cache,
    )
    invocations: list[Invocation] = []
    unsupported: list[Mapping[str, object]] = []
    duplicate_counts: dict[str, int] = {}
    for fence_number, (body, annotation_texts) in enumerate(
        _command_fences(text, context.require_experimental_context), 1
    ):
        parsed, failures = _parse_fence(body)
        unsupported.extend(
            {"fence": fence_number, "reason": failure} for failure in failures
        )
        decoded_annotations = _parse_annotations(
            annotation_texts, len(parsed), context.document
        )
        for ordinal, command in enumerate(parsed, 1):
            annotation = decoded_annotations.get(ordinal)
            if command is None:
                if annotation is not None:
                    _fail(
                        "invocation.command.unsupported",
                        f"{context.document}:fence-{fence_number}:command-{ordinal}",
                        {"annotation": True},
                    )
                continue
            canonical = canonical_json(list(command.tokens))
            duplicate = duplicate_counts.get(canonical, 0)
            duplicate_counts[canonical] = duplicate + 1
            invocations.append(
                _build_invocation(
                    command,
                    annotation,
                    context,
                    _InvocationPosition(
                        fence_number, ordinal, len(invocations), duplicate
                    ),
                )
            )
    if len(invocations) > MAX_INVOCATIONS_PER_LOG:
        _fail(
            "provenance.resource.too_large",
            context.document,
            {"invocations": len(invocations), "limit": MAX_INVOCATIONS_PER_LOG},
        )
    return DiscoveryResult(tuple(invocations), tuple(unsupported))


def automatic_option_role(name: str) -> str | None:
    """Return the closed leading-or-trailing input/output option role."""

    name = name.lstrip("-")
    matches = [role for role in ("input", "output") if _role_name(name, role)]
    return matches[0] if len(matches) == 1 else None


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


def load_data_index(path: Path) -> dict[str, str]:
    """Load the exact entry-local name-to-location surface used by commands."""

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["name", "type", "location"]:
                _fail(
                    "data_index.connection.missing",
                    str(path),
                    {"header": reader.fieldnames},
                )
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        _fail("data_index.connection.missing", str(path), {"error": str(exc)})
    result: dict[str, str] = {}
    for number, row in enumerate(rows, 2):
        name, location = row.get("name", ""), row.get("location", "")
        if (
            not name
            or TARGET_RE.fullmatch(name) is None
            or not location
            or name in result
        ):
            _fail(
                "data_index.connection.missing",
                f"{path}:{number}",
                {"name": name, "location": location},
            )
        result[name] = location
    return result


def _role_name(name: str, role: str) -> bool:
    if name == role:
        return True
    atom = r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?"
    return re.fullmatch(rf"(?:{role}[-_]{atom}|{atom}[-_]{role})", name) is not None


def _command_fences(
    text: str, require_experimental_context: bool
) -> list[tuple[str, tuple[str, ...]]]:
    lines = text.splitlines()
    eligible = _experimental_sections(lines)
    result: list[tuple[str, tuple[str, ...]]] = []
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
        annotations: list[str] = []
        while index < len(lines) and lines[index].startswith("<!-- command"):
            annotation = [lines[index]]
            while "-->" not in annotation[-1] and index + 1 < len(lines):
                index += 1
                annotation.append(lines[index])
            annotations.append("\n".join(annotation))
            index += 1
        if language in SHELL_LANGUAGES and (
            not require_experimental_context or eligible[start]
        ):
            result.append(("\n".join(body), tuple(annotations)))
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
    commands: list[_ParsedCommand | None] = []
    failures: list[str] = []
    for raw in logical.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$ "):
            line = line[2:]
        try:
            segments = _split_semicolons(line)
        except ValueError as exc:
            commands.append(None)
            failures.append(str(exc))
            continue
        for segment in segments:
            encoded_bytes = len(segment.encode("utf-8"))
            if encoded_bytes > MAX_COMMAND_BYTES:
                _fail(
                    "provenance.resource.too_large",
                    "command invocation",
                    {"bytes": encoded_bytes, "limit": MAX_COMMAND_BYTES},
                )
            try:
                commands.append(_parse_command(segment))
            except ValueError as exc:
                commands.append(None)
                failures.append(str(exc))
    if len(commands) > MAX_INVOCATIONS_PER_FENCE:
        _fail(
            "provenance.resource.too_large",
            "command fence",
            {"invocations": len(commands), "limit": MAX_INVOCATIONS_PER_FENCE},
        )
    return commands, failures


def _split_semicolons(value: str) -> list[str]:
    lexer = shlex.shlex(value, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    if any(token in {"&&", "||", "&"} for token in tokens):
        raise ValueError("unsupported shell control flow")
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == ";":
            if not segments[-1]:
                raise ValueError("empty shell invocation")
            segments.append([])
        else:
            segments[-1].append(token)
    if not segments[-1]:
        raise ValueError("trailing shell separator")
    return [shlex.join(segment) for segment in segments]


def _parse_command(value: str) -> _ParsedCommand:
    if "$(" in value or "`" in value or "<(" in value or ">(" in value:
        raise ValueError("unsupported shell substitution")
    lexer = shlex.shlex(value, posix=True, punctuation_chars="|<>")
    lexer.whitespace_split = True
    tokens = tuple(lexer)
    if not tokens or tokens.count("|") > 1:
        raise ValueError("unsupported pipeline")
    components = _pipeline_components(tokens)
    principal = components[0]
    executable_index = next(
        (
            index
            for index, token in enumerate(principal)
            if not ENVIRONMENT_RE.fullmatch(token)
        ),
        -1,
    )
    if executable_index < 0:
        raise ValueError("missing executable")
    executable = Path(principal[executable_index]).name
    script_index = (
        executable_index + 1
        if executable == "pyrun" or executable.startswith("python")
        else None
    )
    redirections, ordinary = _redirections(principal)
    options, positionals = _arguments(ordinary, executable_index, script_index)
    tee_outputs: tuple[str, ...] = ()
    if len(components) == 2:
        tee_outputs = _terminal_tee(components[1])
    return _ParsedCommand(
        tokens,
        executable_index,
        script_index,
        options,
        positionals,
        redirections,
        tee_outputs,
    )


def _pipeline_components(tokens: Sequence[str]) -> list[list[str]]:
    components: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            if not components[-1]:
                raise ValueError("empty pipeline component")
            components.append([])
        else:
            components[-1].append(token)
    if not components[-1]:
        raise ValueError("empty pipeline component")
    return components


def _redirections(
    tokens: Sequence[str],
) -> tuple[tuple[tuple[str, str], ...], list[str]]:
    redirections: list[tuple[str, str]] = []
    ordinary: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            token.isdecimal()
            and index + 2 < len(tokens)
            and tokens[index + 1] in {">", ">>"}
        ):
            redirections.append(("output", tokens[index + 2]))
            index += 3
            continue
        if token in {"<", ">", ">>"}:
            if index + 1 >= len(tokens):
                raise ValueError("redirection lacks target")
            redirections.append(
                ("input" if token == "<" else "output", tokens[index + 1])
            )
            index += 2
            continue
        ordinary.append(token)
        index += 1
    return tuple(redirections), ordinary


def _arguments(
    tokens: Sequence[str], executable_index: int, script_index: int | None
) -> tuple[tuple[_OptionOccurrence, ...], tuple[str, ...]]:
    options: list[_OptionOccurrence] = []
    positionals: list[str] = []
    index = executable_index + 1
    if script_index == index:
        if index >= len(tokens):
            raise ValueError("interpreter lacks script")
        index += 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") and token not in {"-", "--"}:
            if "=" in token:
                name, value = token.lstrip("-").split("=", 1)
                options.append(_OptionOccurrence(name, value))
                index += 1
                continue
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                options.append(_OptionOccurrence(token.lstrip("-"), tokens[index + 1]))
                index += 2
                continue
            index += 1
            continue
        positionals.append(token)
        index += 1
    return tuple(options), tuple(positionals)


def _terminal_tee(tokens: Sequence[str]) -> tuple[str, ...]:
    if not tokens or Path(tokens[0]).name != "tee" or len(tokens) < 2:
        raise ValueError("unsupported terminal pipeline")
    if any(token.startswith("-") for token in tokens[1:]):
        raise ValueError("unsupported tee option")
    return tuple(tokens[1:])


def _parse_annotations(
    values: Sequence[str], command_count: int, document: str
) -> dict[int, _Annotation]:
    result: dict[int, _Annotation] = {}
    last = 0
    for value in values:
        match = ANNOTATION_RE.fullmatch(value)
        if match is None:
            _fail("invocation.annotation.invalid", document, {"annotation": value})
        ordinal = int(match.group("ordinal")[1:]) if match.group("ordinal") else 1
        if ordinal <= last or ordinal > command_count or ordinal in result:
            _fail(
                "invocation.annotation.invalid",
                document,
                {"ordinal": ordinal, "commands": command_count},
            )
        last = ordinal
        result[ordinal] = _annotation_body(match.group("body"), ordinal, document)
    return result


def _annotation_body(body: str, ordinal: int, document: str) -> _Annotation:
    clauses = re.split(r"\s*;\s*", body.strip())
    roles: dict[str, str] = {}
    command_type: str | None = None
    for index, clause in enumerate(clauses):
        assignment = ASSIGNMENT_RE.fullmatch(clause.strip())
        if assignment is None:
            _fail("invocation.annotation.invalid", document, {"clause": clause})
        target, value = assignment.group("target"), assignment.group("value")
        if target == "type":
            if index or value not in {"model", "simulation"} or command_type:
                _fail("invocation.annotation.invalid", document, {"clause": clause})
            command_type = value
        elif (
            value not in ROLE_TOKENS
            or target in roles
            or TARGET_RE.fullmatch(target) is None
            and POSITIONAL_RE.fullmatch(target) is None
        ):
            _fail("invocation.annotation.invalid", document, {"clause": clause})
        else:
            roles[target] = value
    return _Annotation(ordinal, command_type, roles)


def _build_invocation(
    command: _ParsedCommand,
    annotation: _Annotation | None,
    context: CommandContext,
    position: _InvocationPosition,
) -> Invocation:
    executable = command.tokens[command.executable_index]
    script_token = (
        command.tokens[command.script_index]
        if command.script_index is not None
        else None
    )
    workflow_token = script_token or _explicit_local_executable(executable, context)
    script, script_identity = _resolve_script(workflow_token, context)
    command_type = annotation.command_type if annotation else None
    if command_type is None and _simulation_script(script or script_token):
        command_type = "simulation"
    relationships, collections, candidates = _relationships(
        command, annotation, context
    )
    inputs = tuple(item for item in relationships if item.direction == "input")
    outputs = tuple(item for item in relationships if item.direction == "output")
    if len(inputs) > MAX_RELATIONSHIPS or len(outputs) > MAX_RELATIONSHIPS:
        _fail(
            "provenance.resource.too_large",
            context.document,
            {"inputs": len(inputs), "outputs": len(outputs)},
        )
    identity_payload = [
        context.log_id,
        context.entry,
        context.document,
        list(command.tokens),
        script or script_token,
        position.duplicate,
    ]
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
        script,
        script_identity,
        command_type,
        inputs,
        outputs,
        collections,
        candidates,
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
    if context.script_identity_cache is not None:
        cached = context.script_identity_cache.get(canonical)
        if cached is not None:
            return canonical, cached
    payload = path.read_bytes()
    identity = hashlib.sha256(payload).hexdigest()
    if context.script_identity_cache is not None:
        context.script_identity_cache[canonical] = identity
    return canonical, identity


def _explicit_local_executable(executable: str, context: CommandContext) -> str | None:
    if executable.startswith("./") or executable.startswith("../"):
        return executable
    path = Path(executable)
    if path.is_absolute() and _within(path.resolve(), context.project_root):
        return executable
    return None


def _simulation_script(value: str | None) -> bool:
    if value is None:
        return False
    stem = Path(value).stem
    return stem in SIMULATION_STEMS or any(
        stem.startswith(prefix + "_") for prefix in SIMULATION_STEMS
    )


def _relationships(
    command: _ParsedCommand,
    annotation: _Annotation | None,
    context: CommandContext,
) -> tuple[
    tuple[MaterialRelationship, ...], tuple[MaterialCollection, ...], tuple[str, ...]
]:
    relationships: list[MaterialRelationship] = []
    collections: list[MaterialCollection] = []
    state = _RoleState(context, relationships, collections)
    candidates: list[str] = []
    annotated = annotation.roles if annotation else {}
    options = {occurrence.name for occurrence in command.options}
    positionals = {f"@{index}" for index in range(1, len(command.positionals) + 1)}
    missing = set(annotated) - options - positionals
    if missing:
        _fail(
            "invocation.annotation.invalid",
            context.document,
            {"targets": sorted(missing)},
        )
    for direction, value in (
        *command.redirections,
        *(("output", item) for item in command.tee_outputs),
    ):
        relationships.append(
            _relationship(
                _RelationshipRequest(value, direction, "shell", None), context
            )
        )
    for occurrence in command.options:
        role = annotated.get(occurrence.name, automatic_option_role(occurrence.name))
        if role is None:
            named = _named_input(occurrence.value, context)
            if named is not None:
                relationships.append(named)
            else:
                candidates.append(_candidate(occurrence.value, context))
            continue
        _apply_role(occurrence.value, role, occurrence.name, state)
    for index, value in enumerate(command.positionals, 1):
        target = f"@{index}"
        role = annotated.get(target)
        if role is None:
            named = _named_input(value, context)
            if named is not None:
                relationships.append(named)
            else:
                candidates.append(_candidate(value, context))
            continue
        _apply_role(value, role, target, state)
    collections.extend(_repeated_collections(relationships, context.document))
    relationships = _deduplicate_relationships(relationships, context.document)
    return tuple(relationships), tuple(collections), tuple(candidates)


def _candidate(value: str, context: CommandContext) -> str:
    path = _expand_path(value, context)
    return path.resolve().as_posix() if path is not None else value


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
    direction = "input" if role.startswith("input") else "output"
    if role.endswith("-directory"):
        state.collections.append(
            _directory_collection(value, direction, target, context)
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
    if role.endswith("-manifest"):
        collection, manifest = _manifest_collection(value, direction, target, context)
        state.collections.append(collection)
        state.relationships.append(manifest)
        state.relationships.extend(
            _relationship(
                _RelationshipRequest(
                    member, direction, "manifest", target, expanded=True
                ),
                context,
            )
            for member in collection.members
        )
        return
    named = _named_input(value, context) if direction == "input" else None
    state.relationships.append(
        named
        or _relationship(
            _RelationshipRequest(value, direction, "option", target), context
        )
    )


def _named_input(value: str, context: CommandContext) -> MaterialRelationship | None:
    match = re.fullmatch(r"<([A-Za-z0-9][A-Za-z0-9_-]*)>", value)
    if match is None or match.group(1) in {"log", "project"}:
        return None
    name = match.group(1)
    location = context.data_index.get(name)
    if location is None:
        _fail("data_index.connection.missing", context.document, {"name": name})
    external = _external_location(location, context.entry_root, context.log_root)
    path = (
        location
        if external and "://" in location
        else _expanded_location(location, context)
    )
    return MaterialRelationship(path, "input", "named-input", name, name, external)


def _relationship(
    request: _RelationshipRequest,
    context: CommandContext,
) -> MaterialRelationship:
    path = (
        Path(request.value)
        if request.expanded
        else _expand_path(request.value, context)
    )
    if path is None:
        _fail("material.unresolved", context.document, {"value": request.value})
    if not request.expanded and not _within(path.resolve(), context.log_root):
        _fail("data_index.raw_external", context.document, {"value": request.value})
    return MaterialRelationship(
        path.resolve().as_posix(),
        request.direction,
        request.proof,
        request.target,
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


def _expanded_location(value: str, context: CommandContext) -> str:
    path = Path(value)
    return (
        (path if path.is_absolute() else context.entry_root / path).resolve().as_posix()
    )


def _external_location(value: str, base: Path, log_root: Path) -> bool:
    if "://" in value or Path(value).is_absolute():
        return True
    try:
        relative_base = base.relative_to(log_root)
    except ValueError:
        return True
    parts: list[str] = list(relative_base.parts)
    for part in PurePosixPath(value).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return True
            parts.pop()
        else:
            parts.append(part)
    return False


def _directory_collection(
    value: str, direction: str, target: str, context: CommandContext
) -> MaterialCollection:
    root = _expand_path(value, context)
    if root is None or root.is_symlink() or not root.is_dir():
        _fail("collection.membership.invalid", context.document, {"directory": value})
    try:
        root.resolve().relative_to(context.entry_root)
    except ValueError:
        _fail("collection.membership.invalid", context.document, {"directory": value})
    members = tuple(
        path.resolve().as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    )
    _validate_members(members, context.document)
    return MaterialCollection(
        direction, "directory", target, members, root.resolve().as_posix()
    )


def _manifest_collection(
    value: str, direction: str, target: str, context: CommandContext
) -> tuple[MaterialCollection, MaterialRelationship]:
    manifest = _expand_path(value, context)
    if manifest is None or manifest.is_symlink() or not manifest.is_file():
        _fail("collection.manifest.invalid", context.document, {"manifest": value})
    try:
        with manifest.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["path"]:
                _fail(
                    "collection.manifest.invalid",
                    context.document,
                    {"header": reader.fieldnames},
                )
            raw = [row.get("path", "") for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        _fail("collection.manifest.invalid", context.document, {"error": str(exc)})
    if any(not _manifest_member(item) for item in raw) or len(raw) != len(set(raw)):
        _fail("collection.manifest.invalid", context.document, {"paths": raw})
    member_paths = tuple(manifest.parent / item for item in raw)
    if any(
        path.is_symlink()
        or not path.is_file()
        or not _within(path.resolve(), context.entry_root)
        for path in member_paths
    ):
        _fail("collection.manifest.invalid", context.document, {"paths": raw})
    members = tuple(path.resolve().as_posix() for path in member_paths)
    _validate_members(members, context.document)
    collection = MaterialCollection(
        direction,
        "manifest",
        target,
        members,
        manifest.parent.resolve().as_posix(),
    )
    relation = MaterialRelationship(
        manifest.resolve().as_posix(), direction, "manifest-file", target
    )
    return collection, relation


def _manifest_member(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and "\\" not in value
        and "://" not in value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _validate_members(members: Sequence[str], subject: str) -> None:
    if (
        not members
        or len(members) > MAX_COLLECTION_MEMBERS
        or len(members) != len(set(members))
    ):
        _fail("collection.membership.invalid", subject, {"members": len(members)})


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
