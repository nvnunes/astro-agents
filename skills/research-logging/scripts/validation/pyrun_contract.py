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


class PyrunContractError(ValueError):
    """One invalid ``pyrun`` command-line layout."""


@dataclass(frozen=True)
class OptionOccurrence:
    """One option name and its separate or equals-delimited value."""

    name: str
    value: str


@dataclass(frozen=True)
class PyrunLayout:
    """The script, parameters, and captures visible at the runner boundary."""

    script_index: int
    script: str
    script_arguments: tuple[str, ...]
    parameters: tuple[str, ...]
    captures: tuple[tuple[str, str], ...]


def parse_pyrun_arguments(arguments: Sequence[str]) -> PyrunLayout:
    """Parse arguments following the ``pyrun`` executable.

    Capture options are accepted only as a leading group followed by ``--``.
    The persisted parameter vector contains that exact group, separator, and
    all script arguments, but excludes the script path itself.
    """

    index = 0
    captures: list[tuple[str, str]] = []
    signature_prefix: list[str] = []
    while index < len(arguments) and arguments[index] in PYRUN_CAPTURE_STREAMS:
        option = arguments[index]
        if index + 1 >= len(arguments):
            raise PyrunContractError(f"{option} lacks target")
        target = arguments[index + 1]
        captures.append((option, target))
        signature_prefix.extend((option, target))
        index += 2
    if captures:
        if index >= len(arguments) or arguments[index] != "--":
            raise PyrunContractError("capture options require -- before the script")
        _validate_captures(captures)
        signature_prefix.append("--")
        index += 1
    if index >= len(arguments):
        raise PyrunContractError("missing script")
    script_arguments = tuple(arguments[index + 1 :])
    return PyrunLayout(
        index,
        arguments[index],
        script_arguments,
        tuple((*signature_prefix, *script_arguments)),
        tuple(captures),
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


def _role_name(name: str, role: str) -> bool:
    if name == role:
        return True
    atom = r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?"
    return re.fullmatch(rf"(?:{role}[-_]{atom}|{atom}[-_]{role})", name) is not None
