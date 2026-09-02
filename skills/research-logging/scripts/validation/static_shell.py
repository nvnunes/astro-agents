"""Closed, non-executing expansion for corpus-backed static shell forms."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Mapping, Sequence

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
SAFE_LITERAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+,:=@%-]*\Z")
FUNCTION_RE = re.compile(rf"(?P<name>{NAME})\s*\(\s*\)\s*\{{\s*\Z")
FUNCTION_PREFIX_RE = re.compile(rf"(?P<name>{NAME})\s*\(\s*\)\s*\{{")
FOR_RE = re.compile(rf"for\s+(?P<name>{NAME})\s+in\s+(?P<values>.+?)\s*;\s*do\s*\Z")
CASE_RE = re.compile(r"case\s+(?P<selector>.+?)\s+in\s*\Z")
POSITIONAL_ASSIGNMENT_RE = re.compile(
    rf"(?P<name>{NAME})=\$(?P<position>[1-9][0-9]*)\Z"
)
ARRAY_ASSIGNMENT_RE = re.compile(rf"(?P<name>{NAME})=\((?P<values>[^()]*)\)\s*;;\s*\Z")
SCALAR_ASSIGNMENT_RE = re.compile(rf"(?P<name>{NAME})=(?P<value>[^\s;()]+)\s*;;\s*\Z")
BRANCH_RE = re.compile(r"(?P<literal>[^\s|()]+)\)\s*(?P<assignment>.+)\Z")
VARIABLE_RE = re.compile(
    rf"\$(?:\{{(?P<braced>{NAME})\}}|(?P<plain>{NAME})(?![A-Za-z0-9_]))"
)
ARRAY_REFERENCE_RE = re.compile(rf'"\$\{{(?P<name>{NAME})\[@\]\}}"')
POSITIONAL_REFERENCE_RE = re.compile(r"\$(?P<position>[1-9][0-9]*)")
POSITIONAL_LIST_RE = re.compile(r'"\$@"')
CONTROL_WORDS = frozenset({"case", "for", "if", "select", "until", "while"})
OPERATOR_CHARACTERS = frozenset(";&|<>")


class StaticShellResourceError(ValueError):
    """A static expansion crossed one caller-supplied resource bound."""

    def __init__(self, resource: str, observed: int, limit: int):
        super().__init__(f"static shell expansion exceeds {limit} {resource}")
        self.resource = resource
        self.observed = observed
        self.limit = limit


@dataclass(frozen=True)
class StaticToken:
    """One quote-resolved shell token with explicit operator provenance."""

    value: str
    operator: bool = False


@dataclass(frozen=True)
class StaticCommand:
    """One concrete command and the static source projection that produced it."""

    text: str
    projection: tuple[str, ...] = ()
    tokens: tuple[StaticToken, ...] = ()


@dataclass(frozen=True)
class StaticFailure:
    """One consumed shell surface outside the closed static grammar."""

    reason: str


@dataclass(frozen=True)
class StaticGroup:
    """One composite expansion whose commands succeed or fail together."""

    commands: tuple[StaticCommand, ...]


StaticItem = StaticCommand | StaticFailure | StaticGroup


@dataclass(frozen=True)
class _Function:
    name: str
    source: tuple[str, ...]
    body: tuple[str, ...]


@dataclass
class _FunctionState:
    definitions: dict[str, _Function]
    invalid: set[str]


@dataclass(frozen=True)
class _Bindings:
    scalars: Mapping[str, str]
    arrays: Mapping[str, tuple[str, ...]]
    positional: tuple[str, ...] = ()


@dataclass
class _ExpansionBudget:
    binding_limit: int
    token_limit: int
    work_limit: int
    bindings: int = 0
    tokens: int = 0
    work_items: int = 0

    def bind(self) -> None:
        self.bindings += 1
        if self.bindings > self.binding_limit:
            raise StaticShellResourceError(
                "bindings", self.bindings, self.binding_limit
            )

    def work(self, amount: int = 1) -> None:
        self.work_items += amount
        if self.work_items > self.work_limit:
            raise StaticShellResourceError(
                "work_items", self.work_items, self.work_limit
            )

    def tokenize(
        self, value: str, *, allow_private: bool = False
    ) -> tuple[StaticToken, ...]:
        remaining = max(self.token_limit - self.tokens, 0)
        try:
            parsed = _tokenize(
                value,
                maximum_tokens=remaining + 1,
                allow_private=allow_private,
            )
        except StaticShellResourceError as exc:
            raise StaticShellResourceError(
                "tokens", self.tokens + exc.observed, self.token_limit
            ) from exc
        self.tokens += len(parsed)
        if self.tokens > self.token_limit:
            raise StaticShellResourceError("tokens", self.tokens, self.token_limit)
        return parsed


def expand_static_shell(
    body: str,
    *,
    maximum_bindings: int,
    maximum_tokens: int,
    maximum_work: int,
) -> tuple[StaticItem, ...]:
    """Expand only finite literal loops and positional helper calls."""

    logical = re.sub(r"\\\r?\n", " ", body)
    lines = tuple(_clean_line(line) for line in logical.splitlines())
    functions = _FunctionState({}, set())
    budget = _ExpansionBudget(maximum_bindings, maximum_tokens, maximum_work)
    budget.work(len(lines))
    items: list[StaticItem] = []
    index = 0
    while index < len(lines):
        expanded, index = _expand_top_level(lines, index, functions, budget)
        items.extend(expanded)
    return tuple(items)


def _expand_top_level(
    lines: Sequence[str],
    index: int,
    functions: _FunctionState,
    budget: _ExpansionBudget,
) -> tuple[tuple[StaticItem, ...], int]:
    line = lines[index]
    if not line or line.startswith("#") or line == "wait":
        return (), index + 1
    function_result = _function_surface(lines, index, functions, budget)
    if function_result is not None:
        return function_result
    loop_match = FOR_RE.fullmatch(line)
    if loop_match is not None:
        end = _closing_line(lines, index + 1, "done")
        return _expand_loop(loop_match, lines[index : end + 1], budget), end + 1
    call = _function_call(line, functions, budget)
    if call is not None:
        return call, index + 1
    if _looks_like_control(line) or line in {"done", "esac", "}", "fi"}:
        end = _unsupported_control_end(lines, index)
        return _failure("unsupported shell control flow"), end + 1
    failure = _dynamic_failure(line)
    return (
        _failure(failure) if failure else _command_items(_commands(line, (), budget)),
        index + 1,
    )


def _function_surface(
    lines: Sequence[str],
    index: int,
    functions: _FunctionState,
    budget: _ExpansionBudget,
) -> tuple[tuple[StaticItem, ...], int] | None:
    function_match = FUNCTION_RE.fullmatch(lines[index])
    if function_match is not None:
        return _record_function(lines, index, function_match, functions, budget)
    malformed = FUNCTION_PREFIX_RE.match(lines[index])
    if malformed is None:
        return None
    name = malformed.group("name")
    functions.definitions.pop(name, None)
    functions.invalid.add(name)
    end = _unsupported_function_end(lines, index)
    return _failure("unsupported static shell function"), end + 1


def _record_function(
    lines: Sequence[str],
    index: int,
    match: re.Match[str],
    functions: _FunctionState,
    budget: _ExpansionBudget,
) -> tuple[tuple[StaticItem, ...], int]:
    end = _closing_line(lines, index + 1, "}")
    function = _Function(
        match.group("name"),
        _source_projection(lines[index : end + 1], budget),
        tuple(lines[index + 1 : end]),
    )
    if function.name in functions.definitions or function.name in functions.invalid:
        functions.definitions.pop(function.name, None)
        functions.invalid.add(function.name)
        return _failure("duplicate static shell function"), end + 1
    failure = _function_definition_failure(function)
    if failure is not None:
        functions.invalid.add(function.name)
        return _failure(failure), end + 1
    functions.definitions[function.name] = function
    return (), end + 1


def _clean_line(line: str) -> str:
    line = line.strip()
    return line[2:].strip() if line.startswith("$ ") else line


def _closing_line(lines: Sequence[str], start: int, closing: str) -> int:
    for index in range(start, len(lines)):
        if lines[index] == closing:
            return index
        if FUNCTION_RE.fullmatch(lines[index]) or FOR_RE.fullmatch(lines[index]):
            raise ValueError("unsupported nested shell control flow")
    raise ValueError(f"unterminated static shell {closing}")


def _unsupported_control_end(lines: Sequence[str], start: int) -> int:
    opening = _control_word(lines[start])
    if opening is None:
        return start
    closing_for = {
        "case": "esac",
        "for": "done",
        "if": "fi",
        "select": "done",
        "until": "done",
        "while": "done",
    }
    closing = closing_for.get(opening)
    if closing is None:
        return start
    stack = [closing]
    for index in range(start + 1, len(lines)):
        nested_word = _control_word(lines[index])
        nested = closing_for.get(nested_word) if nested_word is not None else None
        if nested is not None:
            stack.append(nested)
            continue
        if lines[index] == stack[-1]:
            stack.pop()
            if not stack:
                return index
    return len(lines) - 1


def _unsupported_function_end(lines: Sequence[str], start: int) -> int:
    match = FUNCTION_PREFIX_RE.match(lines[start])
    assert match is not None
    if "}" in lines[start][match.end() :]:
        return start
    for index in range(start + 1, len(lines)):
        if lines[index] == "}":
            return index
    return len(lines) - 1


def _source_projection(
    lines: Sequence[str], budget: _ExpansionBudget
) -> tuple[str, ...]:
    return tuple(
        _canonical_template(line, budget)
        for line in lines
        if line and not line.startswith("#")
    )


def _canonical_template(line: str, budget: _ExpansionBudget) -> str:
    if any("\ue000" <= character <= "\uf8ff" for character in line):
        raise ValueError("unsupported private-use shell character")
    function = FUNCTION_RE.fullmatch(line)
    if function is not None:
        line = f"{function.group('name')} ( ) {{"
    result: list[str] = []
    for quote, value in _quote_segments(line):
        if quote != "'":
            value, escapes = _mask_escaped_characters(value, quote)
            value = re.sub(
                rf"\$\{{(?P<name>{NAME})\[@\]\}}",
                lambda match: f"\ue100array:{match.group('name')}\ue101",
                value,
            )
            value = value.replace("$@", "\ue100positionals\ue101")
            value = POSITIONAL_REFERENCE_RE.sub(
                lambda match: f"\ue100position:{match.group('position')}\ue101",
                value,
            )
            value = VARIABLE_RE.sub(
                lambda match: (
                    f"\ue100scalar:"
                    f"{match.group('braced') or match.group('plain')}\ue101"
                ),
                value,
            )
            value = _restore_escaped_characters(value, escapes)
        result.append(quote + value + quote if quote else value)
    tokens = budget.tokenize("".join(result), allow_private=True)
    return "|".join(
        f"{'operator' if token.operator else 'word'}:{len(token.value)}:{token.value}"
        for token in tokens
    )


def _function_definition_failure(function: _Function) -> str | None:
    for line in function.body:
        if not line or line.startswith("#") or line == "shift":
            continue
        if POSITIONAL_ASSIGNMENT_RE.fullmatch(line) is not None:
            continue
        if _looks_like_control(line) or line in {"done", "esac", "}"}:
            return "unsupported static shell function body"
        failure = _dynamic_failure(line, allow_bound=True)
        if failure is not None:
            return failure
    return None


def _function_call(
    line: str,
    functions: _FunctionState,
    budget: _ExpansionBudget,
) -> tuple[StaticItem, ...] | None:
    name = _leading_word(line)
    if name in functions.invalid:
        return _failure("invalid static shell function")
    if name not in functions.definitions:
        return None
    try:
        tokens = _literal_tokens(line, budget=budget, allow_background=True)
    except StaticShellResourceError:
        raise
    except ValueError as exc:
        return _failure(str(exc))
    background = tokens[-1] == "&"
    arguments = tokens[1:-1] if background else tokens[1:]
    if not arguments:
        return _failure("static shell function requires literal arguments")
    return _invoke_function(functions.definitions[tokens[0]], tokens, arguments, budget)


def _invoke_function(
    function: _Function,
    tokens: tuple[str, ...],
    arguments: tuple[str, ...],
    budget: _ExpansionBudget,
) -> tuple[StaticItem, ...]:
    bindings = _Bindings({}, {}, arguments)
    commands: list[StaticCommand] = []
    for line in function.body:
        budget.work()
        if not line or line.startswith("#"):
            continue
        assignment = POSITIONAL_ASSIGNMENT_RE.fullmatch(line)
        if assignment is not None:
            try:
                bindings = _apply_positional_assignment(assignment, bindings)
            except ValueError as exc:
                return _failure(str(exc))
            continue
        if line == "shift":
            try:
                bindings = _shift(bindings)
            except ValueError as exc:
                return _failure(str(exc))
            continue
        if line == "wait":
            continue
        try:
            concrete = _substitute(line, bindings)
        except ValueError as exc:
            return _failure(str(exc))
        projection = (
            *(f"function:{source}" for source in function.source),
            f"call:{shlex.join(tokens)}",
            *_binding_projection(bindings),
        )
        expanded = _commands(concrete, projection, budget)
        if isinstance(expanded, StaticFailure):
            return (expanded,)
        commands.extend(expanded)
    return (StaticGroup(tuple(commands)),)


def _apply_positional_assignment(
    assignment: re.Match[str], bindings: _Bindings
) -> _Bindings:
    position = int(assignment.group("position"))
    if position > len(bindings.positional):
        raise ValueError("unbound static positional parameter")
    scalars = dict(bindings.scalars)
    scalars[assignment.group("name")] = bindings.positional[position - 1]
    return _Bindings(scalars, bindings.arrays, bindings.positional)


def _shift(bindings: _Bindings) -> _Bindings:
    if not bindings.positional:
        raise ValueError("static shift has no positional argument")
    return _Bindings(bindings.scalars, bindings.arrays, bindings.positional[1:])


def _expand_loop(
    match: re.Match[str], lines: Sequence[str], budget: _ExpansionBudget
) -> tuple[StaticItem, ...]:
    try:
        values = _literal_tokens(
            match.group("values"),
            budget=budget,
        )
    except StaticShellResourceError:
        raise
    except ValueError as exc:
        return _failure(str(exc))
    if not values:
        return _failure("static for loop requires literal values")
    name = match.group("name")
    source = _source_projection(lines, budget)
    body = lines[1:-1]
    commands: list[StaticCommand] = []
    for value in values:
        budget.bind()
        bindings = _Bindings({name: value}, {})
        try:
            expanded, bindings = _expand_loop_body(body, bindings, source, name, budget)
        except StaticShellResourceError:
            raise
        except ValueError as exc:
            return _failure(str(exc))
        if isinstance(expanded, StaticFailure):
            return (expanded,)
        commands.extend(expanded)
    return (StaticGroup(tuple(commands)),)


def _expand_loop_body(
    lines: Sequence[str],
    bindings: _Bindings,
    source: tuple[str, ...],
    loop_name: str,
    budget: _ExpansionBudget,
) -> tuple[tuple[StaticCommand, ...] | StaticFailure, _Bindings]:
    commands: list[StaticCommand] = []
    index = 0
    while index < len(lines):
        budget.work()
        line = lines[index]
        if not line or line.startswith("#"):
            index += 1
            continue
        case_match = CASE_RE.fullmatch(line)
        if case_match is not None:
            end = _closing_line(lines, index + 1, "esac")
            bindings = _apply_case(
                case_match.group("selector"),
                lines[index + 1 : end],
                bindings,
                budget,
            )
            index = end + 1
            continue
        if _looks_like_control(line) or line in {"done", "esac", "}"}:
            raise ValueError("unsupported nested shell control flow")
        if line == "wait":
            index += 1
            continue
        failure = _dynamic_failure(line, allow_bound=True)
        if failure is not None:
            raise ValueError(failure)
        concrete = _substitute(line, bindings)
        projection = (
            *(f"loop:{item}" for item in source),
            f"iteration:{loop_name}={bindings.scalars[loop_name]}",
            *_binding_projection(bindings),
        )
        expanded = _commands(concrete, projection, budget)
        if isinstance(expanded, StaticFailure):
            return expanded, bindings
        commands.extend(expanded)
        index += 1
    return tuple(commands), bindings


def _apply_case(
    selector: str,
    branches: Sequence[str],
    bindings: _Bindings,
    budget: _ExpansionBudget,
) -> _Bindings:
    selector_name = _selector_name(selector)
    if selector_name not in bindings.scalars:
        raise ValueError("unbound static case selector")
    selected = bindings.scalars[selector_name]
    matches: list[str] = []
    for line in branches:
        budget.work()
        if not line or line.startswith("#"):
            continue
        branch = BRANCH_RE.fullmatch(line)
        if branch is None or not _safe_literal(branch.group("literal")):
            raise ValueError("unsupported static case branch")
        if branch.group("literal") == selected:
            matches.append(branch.group("assignment"))
    if len(matches) != 1:
        raise ValueError("static case selection is not unique and complete")
    assignment = matches[0]
    array = ARRAY_ASSIGNMENT_RE.fullmatch(assignment)
    if array is not None:
        values = _literal_tokens(array.group("values"), budget=budget)
        arrays = dict(bindings.arrays)
        arrays[array.group("name")] = values
        return _Bindings(bindings.scalars, arrays, bindings.positional)
    scalar = SCALAR_ASSIGNMENT_RE.fullmatch(assignment)
    if scalar is None or not _safe_literal(scalar.group("value")):
        raise ValueError("unsupported static case assignment")
    scalars = dict(bindings.scalars)
    scalars[scalar.group("name")] = scalar.group("value")
    return _Bindings(scalars, bindings.arrays, bindings.positional)


def _selector_name(selector: str) -> str:
    selector = selector.strip()
    if len(selector) >= 2 and selector[0] == selector[-1] == '"':
        selector = selector[1:-1]
    match = re.fullmatch(rf"\$(?:\{{(?P<braced>{NAME})\}}|(?P<plain>{NAME}))", selector)
    if match is None:
        raise ValueError("dynamic static case selector")
    return match.group("braced") or match.group("plain")


def _substitute(line: str, bindings: _Bindings) -> str:
    failure = _substitution_failure(line)
    if failure is not None:
        raise ValueError(failure)

    segments = _quote_segments(line)
    result: list[str] = []
    for quote, value in segments:
        if quote == "'":
            result.append(quote + value + quote)
            continue
        value, escapes = _mask_escaped_characters(value, quote)
        wrapped = quote + value + quote if quote else value
        array = ARRAY_REFERENCE_RE.fullmatch(wrapped)
        if array is not None:
            result.append(_array_value(array, bindings))
            continue
        if POSITIONAL_LIST_RE.fullmatch(wrapped):
            result.append(_positional_list_value(bindings))
            continue
        value = POSITIONAL_REFERENCE_RE.sub(
            lambda match: _positional_value(match, bindings), value
        )
        value = VARIABLE_RE.sub(lambda match: _scalar_value(match, bindings), value)
        if "$" in value:
            raise ValueError("unsupported shell variable expansion")
        value = _restore_escaped_characters(value, escapes)
        result.append(quote + value + quote if quote else value)
    line = "".join(result)
    if _expandable_character(line, "$"):
        raise ValueError("unsupported shell variable expansion")
    return line


def _array_value(match: re.Match[str], bindings: _Bindings) -> str:
    name = match.group("name")
    if name not in bindings.arrays:
        raise ValueError("unbound static shell array")
    return shlex.join(bindings.arrays[name])


def _positional_list_value(bindings: _Bindings) -> str:
    if not bindings.positional:
        raise ValueError("unbound static positional parameter list")
    return shlex.join(bindings.positional)


def _positional_value(match: re.Match[str], bindings: _Bindings) -> str:
    position = int(match.group("position"))
    if position > len(bindings.positional):
        raise ValueError("unbound static positional parameter")
    return bindings.positional[position - 1]


def _scalar_value(match: re.Match[str], bindings: _Bindings) -> str:
    name = match.group("braced") or match.group("plain")
    if name not in bindings.scalars:
        raise ValueError("unbound static shell variable")
    return bindings.scalars[name]


def _literal_tokens(
    value: str,
    *,
    budget: _ExpansionBudget,
    allow_background: bool = False,
) -> tuple[str, ...]:
    parsed = budget.tokenize(value)
    background = bool(
        parsed and parsed[-1] == StaticToken("&", True) and allow_background
    )
    literal_tokens = parsed[:-1] if background else parsed
    if any(token.operator for token in literal_tokens):
        raise ValueError("unsupported shell control flow")
    literals = tuple(token.value for token in literal_tokens)
    if not literals or any(not _safe_literal(token) for token in literals):
        raise ValueError("dynamic or unsupported static shell literal")
    if parsed[-1:] == (StaticToken("&", True),) and not allow_background:
        raise ValueError("unsupported shell control flow")
    return (*literals, "&") if background else literals


def _safe_literal(value: str) -> bool:
    return SAFE_LITERAL_RE.fullmatch(value) is not None and not any(
        character in value for character in "*?[]{}$`"
    )


def _dynamic_failure(line: str, *, allow_bound: bool = False) -> str | None:
    failure = _substitution_failure(line)
    if failure is not None:
        return failure
    if _expandable_character(line, "$") and not allow_bound:
        return "unbound or unsupported shell variable"
    if any(_unquoted_character(line, character) for character in "*?["):
        return "unsupported shell glob"
    return None


def _substitution_failure(line: str) -> str | None:
    expandable = "".join(
        _mask_escaped_characters(value, quote)[0]
        for quote, value in _quote_segments(line)
        if quote != "'"
    )
    if re.search(r"\$\(\(", expandable) or re.search(r"\$\[[^]]*\]", expandable):
        return "unsupported shell arithmetic"
    if (
        "$(" in expandable
        or "`" in expandable
        or "<(" in expandable
        or ">(" in expandable
    ):
        return "unsupported shell substitution"
    return None


def _looks_like_control(line: str) -> bool:
    return (
        _control_word(line) in CONTROL_WORDS or FUNCTION_RE.fullmatch(line) is not None
    )


def _control_word(line: str) -> str | None:
    match = re.match(r"(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?:\s|$)", line)
    return match.group("word") if match is not None else None


def _leading_word(line: str) -> str | None:
    match = re.match(rf"(?P<name>{NAME})(?=\s|;|&|$)", line)
    return match.group("name") if match is not None else None


def _commands(
    text: str, projection: tuple[str, ...], budget: _ExpansionBudget
) -> tuple[StaticCommand, ...] | StaticFailure:
    try:
        tokens = budget.tokenize(text)
    except StaticShellResourceError:
        raise
    except ValueError as exc:
        return StaticFailure(str(exc))
    segments: list[list[StaticToken]] = [[]]
    for token in tokens:
        if token == StaticToken(";", True):
            if not segments[-1]:
                return StaticFailure("empty shell invocation")
            segments.append([])
        else:
            segments[-1].append(token)
    if not segments[-1]:
        return StaticFailure("trailing shell separator")
    commands: list[StaticCommand] = []
    for segment in segments:
        command_text = shlex.join(token.value for token in segment)
        commands.append(StaticCommand(command_text, projection, tuple(segment)))
    return tuple(commands)


def _command_items(
    result: tuple[StaticCommand, ...] | StaticFailure,
) -> tuple[StaticItem, ...]:
    return (result,) if isinstance(result, StaticFailure) else result


def _failure(reason: str) -> tuple[StaticItem, ...]:
    return (StaticFailure(reason),)


def _tokenize(
    value: str,
    *,
    maximum_tokens: int | None = None,
    allow_private: bool = False,
) -> tuple[StaticToken, ...]:
    masked, replacements = _mask_quoted_operators(value, allow_private=allow_private)
    lexer = shlex.shlex(masked, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    raw_tokens: list[str] = []
    try:
        for raw in lexer:
            raw_tokens.append(raw)
            if maximum_tokens is not None and len(raw_tokens) > maximum_tokens:
                raise StaticShellResourceError(
                    "tokens", len(raw_tokens), maximum_tokens
                )
    except StaticShellResourceError:
        raise
    except ValueError as exc:
        raise ValueError("unterminated shell quotation") from exc
    tokens: list[StaticToken] = []
    for raw in raw_tokens:
        operator = bool(raw) and all(
            character in OPERATOR_CHARACTERS for character in raw
        )
        restored = raw
        for marker, character in replacements.items():
            restored = restored.replace(marker, character)
        tokens.append(StaticToken(restored, operator))
    return tuple(tokens)


def _mask_quoted_operators(
    value: str, *, allow_private: bool = False
) -> tuple[str, Mapping[str, str]]:
    if not allow_private and any(
        "\ue000" <= character <= "\uf8ff" for character in value
    ):
        raise ValueError("unsupported private-use shell character")
    result: list[str] = []
    markers = {
        character: chr(0xE000 + index)
        for index, character in enumerate(sorted(OPERATOR_CHARACTERS))
    }
    replacements = {marker: character for character, marker in markers.items()}
    quote: str | None = None
    escaped = False
    for character in value:
        if escaped:
            result.append(
                markers[character] if character in OPERATOR_CHARACTERS else character
            )
            escaped = False
            continue
        if character == "\\" and quote != "'":
            result.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            result.append(character)
            continue
        if quote is not None and character in OPERATOR_CHARACTERS:
            result.append(markers[character])
            continue
        result.append(character)
    if quote is not None or escaped:
        raise ValueError("unterminated shell quotation")
    return "".join(result), replacements


def _quote_segments(value: str) -> tuple[tuple[str | None, str], ...]:
    segments: list[tuple[str | None, str]] = []
    content: list[str] = []
    quote: str | None = None
    escaped = False
    for character in value:
        if escaped:
            content.append(character)
            escaped = False
            continue
        if character == "\\" and quote != "'":
            content.append(character)
            escaped = True
            continue
        if character in {"'", '"'} and (quote is None or quote == character):
            segments.append((quote, "".join(content)))
            content = []
            quote = character if quote is None else None
            continue
        content.append(character)
    if quote is not None or escaped:
        raise ValueError("unterminated shell quotation")
    segments.append((quote, "".join(content)))
    return tuple(
        segment for segment in segments if segment[1] or segment[0] is not None
    )


def _unquoted_character(value: str, target: str) -> bool:
    return any(
        target in _mask_escaped_characters(content, quote)[0]
        for quote, content in _quote_segments(value)
        if quote is None
    )


def _expandable_character(value: str, target: str) -> bool:
    return any(
        target in _mask_escaped_characters(content, quote)[0]
        for quote, content in _quote_segments(value)
        if quote != "'"
    )


def _mask_escaped_characters(
    value: str, quote: str | None
) -> tuple[str, Mapping[str, str]]:
    if any("\ue000" <= character <= "\uf8ff" for character in value):
        raise ValueError("unsupported private-use shell character")
    result: list[str] = []
    replacements: dict[str, str] = {}
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\" or index + 1 == len(value):
            result.append(character)
            index += 1
            continue
        escaped = value[index + 1]
        if quote == '"' and escaped not in {'"', "$", "`", "\\", "\n"}:
            result.append(character)
            index += 1
            continue
        marker = f"\ue200{len(replacements)}\ue201"
        replacements[marker] = value[index : index + 2]
        result.append(marker)
        index += 2
    return "".join(result), replacements


def _restore_escaped_characters(value: str, replacements: Mapping[str, str]) -> str:
    for marker, escaped in replacements.items():
        value = value.replace(marker, escaped)
    return value


def _binding_projection(bindings: _Bindings) -> tuple[str, ...]:
    scalars = tuple(
        f"scalar:{name}={value}" for name, value in sorted(bindings.scalars.items())
    )
    arrays = tuple(
        f"array:{name}={shlex.join(values)}"
        for name, values in sorted(bindings.arrays.items())
    )
    positional = (
        (f"positional:{shlex.join(bindings.positional)}",)
        if bindings.positional
        else ()
    )
    return (*scalars, *arrays, *positional)
