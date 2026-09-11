"""Bounded parameter edits used to verify Markdown-first recipe corrections."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import ActionError


@dataclass(frozen=True)
class CommandEdit:
    """One explicit change; positions and repeated-option occurrences start at 1."""

    action: str
    parameter: str | None = None
    position: int | None = None
    occurrence: int | None = None
    value: str | None = None
    role: str | None = None

    @property
    def selector(self) -> str:
        """Return the shared runner-role selector for this parameter."""
        if self.position is not None:
            if self.position < 1:
                raise ActionError("pyrun.edit.selector", "positions start at 1")
            return f"@{self.position}"
        name = (self.parameter or "").lstrip("-")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
            raise ActionError("pyrun.edit.selector", "specify a valid parameter name")
        return name


@dataclass(frozen=True)
class ParameterSpan:
    """One valued option, flag, or positional value in a script token vector."""

    selector: str
    start: int
    stop: int
    value: str | None


def parameter_spans(tokens: tuple[str, ...]) -> list[ParameterSpan]:
    """Use the runner's bounded single-value option grammar, retaining offsets."""
    spans: list[ParameterSpan] = []
    index = 0
    position = 0
    while index < len(tokens):
        token = tokens[index]
        stop = index + 1
        value: str | None = None
        if token.startswith("-") and token not in {"-", "--"}:
            name, separator, tail = token.lstrip("-").partition("=")
            if separator:
                value = tail
            elif stop < len(tokens) and not tokens[stop].startswith("-"):
                value = tokens[stop]
                stop += 1
        else:
            position += 1
            name, value = f"@{position}", token
        spans.append(ParameterSpan(name, index, stop, value))
        index = stop
    return spans


def selected_spans(tokens: tuple[str, ...], edit: CommandEdit) -> list[ParameterSpan]:
    """Select exactly one occurrence, or all occurrences for a role declaration."""
    spans = [item for item in parameter_spans(tokens) if item.selector == edit.selector]
    if edit.occurrence is not None:
        if edit.position is not None or not 1 <= edit.occurrence <= len(spans):
            raise ActionError(
                "pyrun.edit.occurrence", "occurrence does not select a named parameter"
            )
        return [spans[edit.occurrence - 1]]
    if len(spans) > 1 and edit.action != "set-role":
        raise ActionError(
            "pyrun.edit.ambiguous", "repeated parameter requires --occurrence"
        )
    return spans


def script_parameters(
    parameters: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Separate the recipe's optional capture prefix from script parameters."""
    if parameters and parameters[0].startswith("--capture-") and "--" in parameters:
        boundary = parameters.index("--") + 1
        return parameters[:boundary], parameters[boundary:]
    return (), parameters


def changed_parameters(
    parameters: tuple[str, ...], edit: CommandEdit
) -> tuple[str, ...]:
    """Calculate only the requested token change; never write Markdown."""
    if edit.action == "set-script":
        return parameters
    prefix, tokens = script_parameters(parameters)
    spans = selected_spans(tokens, edit)
    if edit.action == "set-role":
        if not spans or any(span.value is None for span in spans):
            raise ActionError(
                "pyrun.edit.missing", "role requires an existing valued parameter"
            )
        return parameters
    if edit.action == "remove-parameter":
        if not spans:
            raise ActionError("pyrun.edit.missing", "parameter does not exist")
        span = spans[0]
        return prefix + tokens[: span.start] + tokens[span.stop :]
    return prefix + _replace_value(tokens, spans, edit)


def _replace_value(
    tokens: tuple[str, ...], spans: list[ParameterSpan], edit: CommandEdit
) -> tuple[str, ...]:
    if edit.value is None:
        raise ActionError("pyrun.edit.value", "this edit requires --value")
    if not spans:
        positions = sum(
            item.selector.startswith("@") for item in parameter_spans(tokens)
        )
        if edit.position is not None and edit.position != positions + 1:
            raise ActionError(
                "pyrun.edit.position",
                "new positional parameter must be the next position",
            )
        start = stop = len(tokens)
        spelling = f"--{edit.selector}"
    else:
        start, stop = spans[0].start, spans[0].stop
        spelling = tokens[start].split("=", 1)[0]
    replacement: tuple[str, ...]
    if edit.position is not None:
        replacement = (edit.value,)
    elif edit.value.startswith("-") or (spans and "=" in tokens[start]):
        replacement = (f"{spelling}={edit.value}",)
    else:
        replacement = (spelling, edit.value)
    return tokens[:start] + replacement + tokens[stop:]
