"""Shared command-line contract for ``pyrun`` and static validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

PYRUN_CAPTURE_STREAMS = {
    "--capture-stdout": "stdout",
    "--capture-stderr": "stderr",
    "--capture-stdout-stderr": "stdout-stderr",
}
PYRUN_ROLE_OPTIONS = {
    "--other-inputs": "input",
    "--other-outputs": "output",
}
_OPTION_SELECTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_POSITIONAL_SELECTOR_RE = re.compile(r"@[1-9][0-9]*\Z")


class PyrunContractError(ValueError):
    """One invalid ``pyrun`` command-line layout."""


@dataclass(frozen=True)
class OptionOccurrence:
    """One option name and its separate or equals-delimited value."""

    name: str
    value: str


@dataclass(frozen=True)
class PyrunLayout:
    """The script, parameters, captures, and material-role declarations."""

    script_index: int
    script: str
    script_arguments: tuple[str, ...]
    parameters: tuple[str, ...]
    captures: tuple[tuple[str, str], ...]
    roles: tuple[tuple[str, str], ...]


def parse_pyrun_arguments(arguments: Sequence[str]) -> PyrunLayout:
    """Parse arguments following the ``pyrun`` executable.

    Runner options are accepted only as a leading group followed by ``--``.
    Capture options and their separator remain in the persisted parameter
    vector. Material-role declarations are normalized separately and excluded
    from that execution signature.
    """

    index = 0
    captures: list[tuple[str, str]] = []
    declarations: dict[str, tuple[str, ...]] = {}
    signature_prefix: list[str] = []
    runner_options = PYRUN_CAPTURE_STREAMS.keys() | PYRUN_ROLE_OPTIONS.keys()
    while index < len(arguments) and arguments[index] in runner_options:
        option = arguments[index]
        if index + 1 >= len(arguments):
            raise PyrunContractError(f"{option} lacks target")
        target = arguments[index + 1]
        if option in PYRUN_CAPTURE_STREAMS:
            captures.append((option, target))
            signature_prefix.extend((option, target))
        else:
            if option in declarations:
                raise PyrunContractError(f"duplicate {option} declaration")
            declarations[option] = _parse_selectors(option, target)
        index += 2
    if captures or declarations:
        if index >= len(arguments) or arguments[index] != "--":
            raise PyrunContractError("runner options require -- before the script")
        if captures:
            _validate_captures(captures)
            signature_prefix.append("--")
        index += 1
    if index >= len(arguments):
        raise PyrunContractError("missing script")
    script_arguments = tuple(arguments[index + 1 :])
    roles = _normalized_roles(declarations)
    _validate_role_targets(roles, script_arguments)
    return PyrunLayout(
        index,
        arguments[index],
        script_arguments,
        tuple((*signature_prefix, *script_arguments)),
        tuple(captures),
        roles,
    )


def split_argument_values(
    arguments: Sequence[str],
) -> tuple[tuple[OptionOccurrence, ...], tuple[str, ...]]:
    """Split the bounded option-value forms used for material discovery."""

    options: list[OptionOccurrence] = []
    positionals: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token.startswith("-") and token not in {"-", "--"}:
            if "=" in token:
                name, value = token.lstrip("-").split("=", 1)
                options.append(OptionOccurrence(name, value))
                index += 1
                continue
            if index + 1 < len(arguments) and not arguments[index + 1].startswith(
                "-"
            ):
                options.append(
                    OptionOccurrence(token.lstrip("-"), arguments[index + 1])
                )
                index += 2
                continue
            index += 1
            continue
        positionals.append(token)
        index += 1
    return tuple(options), tuple(positionals)


def automatic_option_role(name: str) -> str | None:
    """Return the closed leading-or-trailing input/output option role."""

    name = name.lstrip("-")
    matches = [role for role in ("input", "output") if _role_name(name, role)]
    return matches[0] if len(matches) == 1 else None


def _validate_captures(captures: Sequence[tuple[str, str]]) -> None:
    options = [option for option, _ in captures]
    targets = [target for _, target in captures]
    if len(options) != len(set(options)):
        raise PyrunContractError("duplicate capture option")
    if "--capture-stdout-stderr" in options and len(options) != 1:
        raise PyrunContractError(
            "--capture-stdout-stderr cannot be mixed with other captures"
        )
    if len(targets) != len(set(targets)):
        raise PyrunContractError("capture targets must be distinct")


def _parse_selectors(option: str, value: str) -> tuple[str, ...]:
    if not value or any(character.isspace() for character in value):
        raise PyrunContractError(f"{option} has invalid selector list")
    selectors = tuple(value.split(","))
    if any(
        not selector
        or (
            _OPTION_SELECTOR_RE.fullmatch(selector) is None
            and _POSITIONAL_SELECTOR_RE.fullmatch(selector) is None
        )
        for selector in selectors
    ):
        raise PyrunContractError(f"{option} has invalid selector list")
    if len(selectors) != len(set(selectors)):
        raise PyrunContractError(f"{option} has duplicate selector")
    return selectors


def _normalized_roles(
    declarations: dict[str, tuple[str, ...]],
) -> tuple[tuple[str, str], ...]:
    inputs = set(declarations.get("--other-inputs", ()))
    outputs = set(declarations.get("--other-outputs", ()))
    conflicts = sorted(inputs & outputs)
    if conflicts:
        raise PyrunContractError(
            "selectors cannot be both inputs and outputs: " + ",".join(conflicts)
        )
    return tuple(
        (selector, direction)
        for direction, selectors in (("input", inputs), ("output", outputs))
        for selector in sorted(selectors)
    )


def _validate_role_targets(
    roles: Sequence[tuple[str, str]], arguments: Sequence[str]
) -> None:
    options, positionals = split_argument_values(arguments)
    option_names = {option.name for option in options}
    missing = []
    for selector, _ in roles:
        if selector.startswith("@"):
            if int(selector[1:]) > len(positionals):
                missing.append(selector)
        elif selector not in option_names:
            missing.append(selector)
    if missing:
        raise PyrunContractError(
            "role selectors lack valued arguments: " + ",".join(sorted(missing))
        )


def _role_name(name: str, role: str) -> bool:
    if name == role:
        return True
    atom = r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?"
    return re.fullmatch(rf"(?:{role}[-_]{atom}|{atom}[-_]{role})", name) is not None
