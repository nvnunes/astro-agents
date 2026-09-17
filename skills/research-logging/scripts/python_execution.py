"""Shared Python import context for research-script execution and analysis."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PythonExecutionContext:
    """Deterministic project-local import roots for one research command."""

    import_roots: tuple[Path, ...]

    @classmethod
    def for_research_script(
        cls,
        script: Path,
        *,
        entry_root: Path,
        log_root: Path,
        project_root: Path,
    ) -> PythonExecutionContext:
        """Derive the standard script, entry, log, and project import order."""

        project = project_root.resolve(strict=True)
        entry = _project_path(entry_root, project, "entry root")
        log = _project_path(log_root, project, "log root")
        source = _project_path(script, project, "research script")
        if entry.parent.parent != log:
            raise ValueError("entry root does not belong to the supplied log root")
        return cls(
            _unique_paths(
                (
                    source.parent,
                    entry / "scripts",
                    log / "scripts",
                    project,
                )
            )
        )

    def environment(self, base: Mapping[str, str]) -> dict[str, str]:
        """Return *base* with runner-owned roots prepended to ``PYTHONPATH``."""

        environment = dict(base)
        prior = tuple(
            Path(value).resolve(strict=False)
            for value in environment.get("PYTHONPATH", "").split(os.pathsep)
            if value
        )
        roots = _unique_paths((*self.import_roots, *prior))
        environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in roots)
        return environment


def _project_path(path: Path, project: Path, role: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(project)
    except ValueError as error:
        raise ValueError(f"{role} is outside the project root: {resolved}") from error
    return resolved


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)
