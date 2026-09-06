"""Shared output-binding projection for recorded ``pyrun`` recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .errors import MechanicalContractError
from .pyrun_contract import PYRUN_CAPTURE_STREAMS
from .pyrun_outputs import portable_output_path

OUTPUT_BINDING_RULE = "Pyrun Output Bindings"


class OutputBindingError(MechanicalContractError):
    """One recipe whose declared outputs cannot be redirected exactly."""

    def __init__(self, subject: str, observed: Mapping[str, object]):
        super().__init__(
            "pyrun.output.binding_invalid",
            subject,
            observed,
            OUTPUT_BINDING_RULE,
        )


@dataclass(frozen=True)
class OutputBinding:
    """One exact occurrence that creates a declared output."""

    output: str
    authored: str
    mechanism: str
    parameter_index: int | None = None
    option: str | None = None
    equals_prefix: str | None = None

    @property
    def canonical(self) -> bool:
        """Whether the recorded target already uses its canonical identity."""

        return self.authored == self.output

    def substituted(self, destination: Path) -> str:
        """Return the child argument redirected to ``destination``."""

        value = str(destination)
        if self.equals_prefix is not None:
            return f"{self.equals_prefix}={value}"
        return value


@dataclass(frozen=True)
class OutputBindingProjection:
    """The replayable child parameters and complete declared-output bindings."""

    child_parameters: tuple[str, ...]
    bindings: tuple[OutputBinding, ...]

    @property
    def aliases(self) -> tuple[OutputBinding, ...]:
        """Return bindings whose authored spelling is not canonical."""

        return tuple(binding for binding in self.bindings if not binding.canonical)

    @property
    def captures(self) -> tuple[OutputBinding, ...]:
        """Return runner-owned stream-capture bindings."""

        return tuple(
            binding for binding in self.bindings if binding.mechanism == "capture"
        )

    @property
    def parameters(self) -> tuple[OutputBinding, ...]:
        """Return child-parameter bindings."""

        return tuple(
            binding for binding in self.bindings if binding.mechanism == "parameter"
        )


def project_output_bindings(
    parameters: Sequence[str],
    outputs: Mapping[str, str | None] | Sequence[tuple[str, str | None]],
    *,
    entry_root: Path,
    project_root: Path | None = None,
    subject: str = "pyrun recipe",
) -> OutputBindingProjection:
    """Derive one closed output-to-occurrence projection without Markdown.

    One canonicalizable alias remains mechanically resolvable and is exposed in
    ``aliases`` for the validator's Structure conclusion. Missing, repeated, or
    otherwise ambiguous occurrences fail here for every consumer.
    """

    output_kinds = dict(outputs)
    candidates: dict[str, list[OutputBinding]] = {
        output: [] for output in output_kinds
    }
    child_start, captures = _capture_bindings(
        parameters,
        output_kinds,
        entry_root=entry_root,
        project_root=project_root,
        subject=subject,
    )
    for binding in captures:
        candidates[binding.output].append(binding)
    child_parameters = tuple(parameters[child_start:])
    for binding in _parameter_bindings(
        child_parameters,
        frozenset(output_kinds),
        entry_root=entry_root,
        project_root=project_root,
    ):
        candidates[binding.output].append(binding)
    return OutputBindingProjection(
        child_parameters, _require_unique_bindings(candidates, subject)
    )


def _capture_bindings(
    parameters: Sequence[str],
    output_kinds: Mapping[str, str | None],
    *,
    entry_root: Path,
    project_root: Path | None,
    subject: str,
) -> tuple[int, tuple[OutputBinding, ...]]:
    """Resolve the optional runner-owned capture prefix."""

    child_start, captures = _capture_prefix(parameters, subject)
    result: list[OutputBinding] = []
    for option, authored in captures:
        try:
            output = portable_output_path(
                authored,
                entry_root=entry_root,
                project_root=project_root,
                authored=True,
            )
        except MechanicalContractError as error:
            raise OutputBindingError(
                subject,
                {
                    "capture": option,
                    "reason": "capture_target_invalid",
                    "target": authored,
                },
            ) from error
        if output not in output_kinds:
            raise OutputBindingError(
                subject,
                {
                    "capture": option,
                    "output": output,
                    "reason": "capture_output_undeclared",
                },
            )
        if output_kinds[output] != "file":
            raise OutputBindingError(
                subject,
                {
                    "capture": option,
                    "kind": output_kinds[output],
                    "output": output,
                    "reason": "capture_kind_invalid",
                },
            )
        result.append(OutputBinding(output, authored, "capture", option=option))
    return child_start, tuple(result)


def _parameter_bindings(
    child_parameters: Sequence[str],
    outputs: frozenset[str],
    *,
    entry_root: Path,
    project_root: Path | None,
) -> tuple[OutputBinding, ...]:
    """Return every child-parameter occurrence of a declared output."""

    result: list[OutputBinding] = []
    for index, parameter in enumerate(child_parameters):
        authored = parameter
        equals_prefix: str | None = None
        if parameter.startswith("-") and "=" in parameter:
            equals_prefix, authored = parameter.split("=", 1)
        if authored.startswith("-"):
            continue
        try:
            output = portable_output_path(
                authored,
                entry_root=entry_root,
                project_root=project_root,
                authored=True,
            )
        except MechanicalContractError:
            continue
        if output not in outputs:
            continue
        result.append(
            OutputBinding(
                output,
                authored,
                "parameter",
                parameter_index=index,
                equals_prefix=equals_prefix,
            )
        )
    return tuple(result)


def _require_unique_bindings(
    candidates: Mapping[str, Sequence[OutputBinding]], subject: str
) -> tuple[OutputBinding, ...]:
    """Require exactly one capture or parameter occurrence per output."""

    bindings: list[OutputBinding] = []
    for output in sorted(candidates):
        occurrences = candidates[output]
        if len(occurrences) != 1:
            raise OutputBindingError(
                subject,
                {
                    "occurrences": len(occurrences),
                    "output": output,
                    "reason": "missing" if not occurrences else "ambiguous",
                },
            )
        bindings.append(occurrences[0])
    return tuple(bindings)


def _capture_prefix(
    parameters: Sequence[str], subject: str
) -> tuple[int, tuple[tuple[str, str], ...]]:
    """Return a leading persisted capture prefix only when closed by ``--``."""

    index = 0
    candidate: list[tuple[str, str]] = []
    while index < len(parameters) and parameters[index] in PYRUN_CAPTURE_STREAMS:
        if index + 1 >= len(parameters):
            return 0, ()
        candidate.append((parameters[index], parameters[index + 1]))
        index += 2
    if not candidate or index >= len(parameters) or parameters[index] != "--":
        return 0, ()
    options = [option for option, _ in candidate]
    if len(options) != len(set(options)):
        raise OutputBindingError(subject, {"reason": "duplicate_capture_option"})
    if "--capture-stdout-stderr" in options and len(options) != 1:
        raise OutputBindingError(subject, {"reason": "capture_stream_overlap"})
    return index + 1, tuple(candidate)
