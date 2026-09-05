"""Closed, non-executing expansion for the accepted ``pyrun`` shell grammar."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Mapping, Sequence

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
SAFE_LITERAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+,:=@%-]*\Z")
FOR_RE = re.compile(rf"for\s+(?P<name>{NAME})\s+in\s+(?P<values>.+?)\s*;\s*do\s*\Z")
CASE_RE = re.compile(r"case\s+(?P<selector>.+?)\s+in\s*\Z")
SCALAR_RE = re.compile(rf"(?P<name>{NAME})=(?P<value>[^\s;()]+)\s*\Z")
ARRAY_RE = re.compile(rf"(?P<name>{NAME})=\((?P<values>.*)\)\s*\Z")
CASE_SCALAR_RE = re.compile(rf"(?P<name>{NAME})=(?P<value>[^\s;()]+)\s*;;\s*\Z")
CASE_ARRAY_RE = re.compile(rf"(?P<name>{NAME})=\((?P<values>[^()]*)\)\s*;;\s*\Z")
BRANCH_RE = re.compile(r"(?P<literal>[^\s|()]+)\)\s*(?P<assignment>.+)\Z")
VARIABLE_RE = re.compile(
    rf"\$(?:\{{(?P<braced>{NAME})\}}|(?P<plain>{NAME})(?![A-Za-z0-9_]))"
)
ARRAY_REFERENCE_RE = re.compile(rf'"?\$\{{(?P<name>{NAME})\[@\]\}}"?\Z')
COMMAND_ARRAY_REFERENCE_RE = re.compile(rf'"?\$\{{(?P<name>{NAME})\[@\]\}}"?')
OPERATOR_CHARACTERS = frozenset(";&|<>")


class StaticShellResourceError(ValueError):
    """One finite shell expansion that exceeded an explicit caller limit."""

    def __init__(self, resource: str, observed: int, limit: int):
        super().__init__(f"static shell expansion exceeds {limit} {resource}")
        self.resource = resource
        self.observed = observed
        self.limit = limit


@dataclass(frozen=True)
class StaticToken:
    value: str
    operator: bool = False


@dataclass(frozen=True)
class StaticCommand:
    text: str
    projection: tuple[str, ...] = ()
    tokens: tuple[StaticToken, ...] = ()


@dataclass(frozen=True)
class StaticFailure:
    reason: str


@dataclass(frozen=True)
class StaticGroup:
    commands: tuple[StaticCommand | StaticFailure | StaticGroup, ...]


StaticItem = StaticCommand | StaticFailure | StaticGroup


@dataclass(frozen=True)
class _Bindings:
    scalars: Mapping[str, str]
    arrays: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class _ExpansionScope:
    depth: int
    projection: tuple[str, ...]


@dataclass
class _Budget:
    binding_limit: int
    token_limit: int
    work_limit: int
    bindings: int = 0
    tokens: int = 0
    work: int = 0

    def add_binding(self) -> None:
        self.bindings += 1
        if self.bindings > self.binding_limit:
            raise StaticShellResourceError(
                "bindings", self.bindings, self.binding_limit
            )

    def add_work(self, amount: int = 1) -> None:
        self.work += amount
        if self.work > self.work_limit:
            raise StaticShellResourceError("work_items", self.work, self.work_limit)

    def tokenize(self, value: str) -> tuple[StaticToken, ...]:
        parsed = _tokenize(value, self.token_limit - self.tokens + 1)
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
    """Expand direct commands and finite literal loop/case bindings only."""

    try:
        logical = re.sub(r"\\\r?\n", " ", body)
        lines = _logical_lines(logical)
        budget = _Budget(maximum_bindings, maximum_tokens, maximum_work)
        budget.add_work(len(lines))
        items, _ = _expand_block(lines, _Bindings({}, {}), budget, 0, ())
        return items
    except StaticShellResourceError:
        raise
    except ValueError as error:
        return (StaticFailure(str(error)),)


def _logical_lines(body: str) -> tuple[str, ...]:
    raw = [_clean_line(line) for line in body.splitlines()]
    result: list[str] = []
    index = 0
    while index < len(raw):
        line = raw[index]
        if re.fullmatch(rf"{NAME}=\(\s*", line):
            parts = [line]
            index += 1
            while index < len(raw) and raw[index] != ")":
                parts.append(raw[index])
                index += 1
            if index >= len(raw):
                raise ValueError("unterminated static array assignment")
            parts.append(raw[index])
            result.append(" ".join(parts))
        else:
            result.append(line)
        index += 1
    return tuple(result)


def _clean_line(line: str) -> str:
    line = line.strip()
    return line[2:].strip() if line.startswith("$ ") else line


def _expand_block(
    lines: Sequence[str],
    bindings: _Bindings,
    budget: _Budget,
    loop_depth: int,
    projection: tuple[str, ...],
) -> tuple[tuple[StaticItem, ...], _Bindings]:
    items: list[StaticItem] = []
    current = bindings
    index = 0
    while index < len(lines):
        budget.add_work()
        line = lines[index]
        if not line or line.startswith("#"):
            index += 1
            continue
        scalar, array = SCALAR_RE.fullmatch(line), ARRAY_RE.fullmatch(line)
        if scalar is not None:
            scalar_values = dict(current.scalars)
            scalar_values[scalar.group("name")] = _one_literal(
                scalar.group("value"), budget
            )
            current = _Bindings(scalar_values, current.arrays)
            budget.add_binding()
            index += 1
            continue
        if array is not None:
            array_values = dict(current.arrays)
            array_values[array.group("name")] = _literals(
                array.group("values"), budget
            )
            current = _Bindings(current.scalars, array_values)
            budget.add_binding()
            index += 1
            continue
        loop = FOR_RE.fullmatch(line)
        if loop is not None:
            end = _matching_end(lines, index, "done")
            expanded = _expand_loop(
                loop,
                lines[index + 1 : end],
                current,
                budget,
                _ExpansionScope(loop_depth + 1, (*projection, f"loop:{line}")),
            )
            items.append(StaticGroup(expanded))
            index = end + 1
            continue
        if CASE_RE.fullmatch(line) is not None or line in {"done", "esac", "wait"}:
            raise ValueError("unsupported shell control flow")
        if loop_depth == 0 and (current.scalars or current.arrays):
            raise ValueError("static assignments may be used only inside loops")
        concrete = _substitute(line, current)
        items.append(_command(concrete, (*projection, *_projection(current)), budget))
        index += 1
    return tuple(items), current


def _expand_loop(
    match: re.Match[str],
    body: Sequence[str],
    bindings: _Bindings,
    budget: _Budget,
    scope: _ExpansionScope,
) -> tuple[StaticItem, ...]:
    values = _loop_values(match.group("values"), bindings, budget)
    if not values:
        raise ValueError("static for loop requires literal values")
    result: list[StaticItem] = []
    for value in values:
        budget.add_binding()
        scalars = dict(bindings.scalars)
        scalars[match.group("name")] = value
        iteration = _Bindings(scalars, bindings.arrays)
        expanded, _ = _expand_loop_body(
            body,
            iteration,
            budget,
            scope.depth,
            (*scope.projection, f"iteration:{match.group('name')}={value}"),
        )
        result.extend(expanded)
    return tuple(result)


def _expand_loop_body(
    lines: Sequence[str],
    bindings: _Bindings,
    budget: _Budget,
    loop_depth: int,
    projection: tuple[str, ...],
) -> tuple[tuple[StaticItem, ...], _Bindings]:
    items: list[StaticItem] = []
    current = bindings
    index = 0
    while index < len(lines):
        case = CASE_RE.fullmatch(lines[index])
        if case is not None:
            end = _matching_end(lines, index, "esac")
            current = _apply_case(
                case.group("selector"), lines[index + 1 : end], current, budget
            )
            index = end + 1
            continue
        loop = FOR_RE.fullmatch(lines[index])
        if loop is not None:
            end = _matching_end(lines, index, "done")
            items.append(StaticGroup(_expand_loop(
                loop,
                lines[index + 1 : end],
                current,
                budget,
                _ExpansionScope(
                    loop_depth + 1, (*projection, f"loop:{lines[index]}")
                ),
            )))
            index = end + 1
            continue
        expanded, current = _expand_block(
            (lines[index],), current, budget, loop_depth, projection
        )
        items.extend(expanded)
        index += 1
    return tuple(items), current


def _matching_end(lines: Sequence[str], start: int, closing: str) -> int:
    stack = [closing]
    for index in range(start + 1, len(lines)):
        if FOR_RE.fullmatch(lines[index]) is not None:
            stack.append("done")
        elif CASE_RE.fullmatch(lines[index]) is not None:
            stack.append("esac")
        elif lines[index] == stack[-1]:
            stack.pop()
            if not stack:
                return index
    raise ValueError(f"unterminated static shell {closing}")


def _loop_values(value: str, bindings: _Bindings, budget: _Budget) -> tuple[str, ...]:
    array = ARRAY_REFERENCE_RE.fullmatch(value.strip())
    if array is not None:
        if array.group("name") not in bindings.arrays:
            raise ValueError("unbound static shell array")
        return bindings.arrays[array.group("name")]
    return _literals(_substitute(value, bindings), budget)


def _apply_case(
    selector: str, branches: Sequence[str], bindings: _Bindings, budget: _Budget
) -> _Bindings:
    selected = _selector(selector, bindings)
    assignments = []
    for line in branches:
        if not line or line.startswith("#"):
            continue
        branch = BRANCH_RE.fullmatch(line)
        if branch is None or not _safe(branch.group("literal")):
            raise ValueError("unsupported static case branch")
        if branch.group("literal") == selected:
            assignments.append(branch.group("assignment"))
    if len(assignments) != 1:
        raise ValueError("static case selection is not unique and complete")
    array = CASE_ARRAY_RE.fullmatch(assignments[0])
    if array is not None:
        array_values = dict(bindings.arrays)
        array_values[array.group("name")] = _literals(
            array.group("values"), budget
        )
        budget.add_binding()
        return _Bindings(bindings.scalars, array_values)
    scalar = CASE_SCALAR_RE.fullmatch(assignments[0])
    if scalar is None:
        raise ValueError("unsupported static case assignment")
    scalar_values = dict(bindings.scalars)
    scalar_values[scalar.group("name")] = _one_literal(
        scalar.group("value"), budget
    )
    budget.add_binding()
    return _Bindings(scalar_values, bindings.arrays)


def _selector(value: str, bindings: _Bindings) -> str:
    value = value.strip().strip('"')
    match = VARIABLE_RE.fullmatch(value)
    if match is None:
        raise ValueError("dynamic static case selector")
    name = match.group("braced") or match.group("plain")
    if name not in bindings.scalars:
        raise ValueError("unbound static case selector")
    return bindings.scalars[name]


def _one_literal(value: str, budget: _Budget) -> str:
    values = _literals(value, budget)
    if len(values) != 1:
        raise ValueError("static scalar assignment requires one literal")
    return values[0]


def _literals(value: str, budget: _Budget) -> tuple[str, ...]:
    parsed = budget.tokenize(value)
    if any(item.operator for item in parsed):
        raise ValueError("unsupported shell control flow")
    values = tuple(item.value for item in parsed)
    if any(not _safe(item) for item in values):
        raise ValueError("dynamic or unsupported static shell literal")
    return values


def _safe(value: str) -> bool:
    return SAFE_LITERAL_RE.fullmatch(value) is not None and not any(
        character in value for character in "*?[]{}$`"
    )


def _substitute(line: str, bindings: _Bindings) -> str:
    if _substitution_failure(line):
        raise ValueError(_substitution_failure(line))
    line = COMMAND_ARRAY_REFERENCE_RE.sub(
        lambda match: _array(match, bindings), line
    )
    result: list[str] = []
    for quote, value in _quote_segments(line):
        if quote == "'":
            result.append(quote + value + quote)
            continue
        value, escaped = _mask_escaped(value, quote)
        value = VARIABLE_RE.sub(lambda match: _scalar(match, bindings), value)
        if "$" in value:
            raise ValueError("unsupported shell variable expansion")
        value = _restore(value, escaped)
        result.append(quote + value + quote if quote else value)
    return "".join(result)


def _scalar(match: re.Match[str], bindings: _Bindings) -> str:
    name = match.group("braced") or match.group("plain")
    if name not in bindings.scalars:
        raise ValueError("unbound static shell variable")
    return bindings.scalars[name]


def _array(match: re.Match[str], bindings: _Bindings) -> str:
    name = match.group("name")
    if name not in bindings.arrays:
        raise ValueError("unbound static shell array")
    return shlex.join(bindings.arrays[name])


def _command(text: str, projection: tuple[str, ...], budget: _Budget) -> StaticCommand:
    tokens = budget.tokenize(text)
    if not tokens:
        raise ValueError("empty shell invocation")
    return StaticCommand(text, projection, tokens)


def _substitution_failure(line: str) -> str | None:
    visible = "".join(value for quote, value in _quote_segments(line) if quote != "'")
    if re.search(r"\$\(\(", visible) or re.search(r"\$\[[^]]*\]", visible):
        return "unsupported shell arithmetic"
    if "$(" in visible or "`" in visible or "<(" in visible or ">(" in visible:
        return "unsupported shell substitution"
    return None


def _tokenize(value: str, maximum: int) -> tuple[StaticToken, ...]:
    masked, replacements = _mask_operators(value)
    lexer = shlex.shlex(masked, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    raw: list[str] = []
    try:
        for token in lexer:
            raw.append(token)
            if len(raw) > maximum:
                raise StaticShellResourceError("tokens", len(raw), maximum)
    except ValueError as error:
        raise ValueError("unterminated shell quotation") from error
    result = []
    for token in raw:
        operator = bool(token) and all(char in OPERATOR_CHARACTERS for char in token)
        for marker, char in replacements.items():
            token = token.replace(marker, char)
        result.append(StaticToken(token, operator))
    return tuple(result)


def _mask_operators(value: str) -> tuple[str, Mapping[str, str]]:
    if any("\ue000" <= char <= "\uf8ff" for char in value):
        raise ValueError("unsupported private-use shell character")
    markers = {
        char: chr(0xE000 + i)
        for i, char in enumerate(sorted(OPERATOR_CHARACTERS))
    }
    replacements = {marker: char for char, marker in markers.items()}
    result: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            result.append(markers.get(char, char))
            escaped = False
        elif char == "\\" and quote != "'":
            result.append(char)
            escaped = True
        elif char in {"'", '"'} and (quote is None or quote == char):
            quote = char if quote is None else None
            result.append(char)
        elif quote is not None and char in OPERATOR_CHARACTERS:
            result.append(markers[char])
        else:
            result.append(char)
    if quote is not None or escaped:
        raise ValueError("unterminated shell quotation")
    return "".join(result), replacements


def _quote_segments(value: str) -> tuple[tuple[str | None, str], ...]:
    segments: list[tuple[str | None, str]] = []
    content: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            content.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            content.append(char)
            escaped = True
        elif char in {"'", '"'} and (quote is None or quote == char):
            segments.append((quote, "".join(content)))
            content = []
            quote = char if quote is None else None
        else:
            content.append(char)
    if quote is not None or escaped:
        raise ValueError("unterminated shell quotation")
    segments.append((quote, "".join(content)))
    return tuple(item for item in segments if item[1] or item[0] is not None)


def _mask_escaped(value: str, quote: str | None) -> tuple[str, Mapping[str, str]]:
    result: list[str] = []
    replacements: dict[str, str] = {}
    index = 0
    while index < len(value):
        if value[index] != "\\" or index + 1 == len(value):
            result.append(value[index])
            index += 1
            continue
        escaped = value[index + 1]
        if quote == '"' and escaped not in {'"', "$", "`", "\\", "\n"}:
            result.append(value[index])
            index += 1
            continue
        marker = f"\ue200{len(replacements)}\ue201"
        replacements[marker] = value[index : index + 2]
        result.append(marker)
        index += 2
    return "".join(result), replacements


def _restore(value: str, replacements: Mapping[str, str]) -> str:
    for marker, escaped in replacements.items():
        value = value.replace(marker, escaped)
    return value


def _projection(bindings: _Bindings) -> tuple[str, ...]:
    return (
        *(f"scalar:{name}={value}" for name, value in sorted(bindings.scalars.items())),
        *(
            f"array:{name}={shlex.join(values)}"
            for name, values in sorted(bindings.arrays.items())
        ),
    )
