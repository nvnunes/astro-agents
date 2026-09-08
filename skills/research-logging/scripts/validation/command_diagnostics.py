"""Diagnostic-only lookup of rejected commands by resolved output ownership.

These declarations explain discovery failures; they never admit a producer or
establish provenance. Lookup uses recorded canonical paths without filesystem IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping


@dataclass
class RejectedProducerIndex:
    """Index discovery diagnostics by exact outputs and directory ancestors."""

    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[str, set[str]] = field(default_factory=dict)
    directories: dict[str, set[str]] = field(default_factory=dict)

    def add(self, observed: object) -> None:
        """Retain an available rejected-command diagnostic from discovery."""
        if not isinstance(observed, Mapping):
            return
        command = observed.get("rejected_command")
        if not isinstance(command, dict):
            return
        identity = str(command["identity"])
        self.commands[identity] = command
        for output in command["declared_outputs"]:
            index = self.directories if output["kind"] == "directory" else self.outputs
            index.setdefault(output["path"], set()).add(identity)

    def related(self, material: str) -> tuple[dict[str, Any], ...]:
        """Return only commands declaring this exact output or an owning directory."""
        path = PurePosixPath(material)
        identities = set(self.outputs.get(str(path), ()))
        for parent in (path, *path.parents):
            identities.update(self.directories.get(str(parent), ()))
        return tuple(self.commands[identity] for identity in sorted(identities))


def rejected_producer_message(commands: tuple[dict[str, Any], ...]) -> str:
    """Render a bounded explanation; retained text views expose omitted detail."""
    lines = ["Recorded commands declare this output but were excluded:"]
    for command in commands[:5]:
        lines.append(
            f"  {command['document']}, fence {command['fence']}, "
            f"command {command['ordinal']}: {command['script']}"
        )
        lines.append(f"  {command['code']}")
        for argument in command["arguments"][:8]:
            lines.append(f"    {argument['selector']}: {argument['value']}")
        if len(command["arguments"]) > 8:
            lines.append("    Additional arguments omitted from this summary.")
    if len(commands) > 5:
        lines.append(f"  {len(commands) - 5} additional matching commands omitted.")
    lines.append(
        "These arguments have no declared input/output role. Declare their actual "
        "roles using --other-inputs or --other-outputs."
    )
    return "\n".join(line[:240] + ("…" if len(line) > 240 else "") for line in lines)
