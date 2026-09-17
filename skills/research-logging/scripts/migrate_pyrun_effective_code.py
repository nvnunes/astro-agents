#!/usr/bin/env python3
"""Plan-owned pyrun v6 replacement and v7 effective-code refresh."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

from effective_code import (
    EffectiveCodeAnalysis,
    EffectiveCodeError,
    analyze_effective_code,
)
from log_commands.context import parse_entry_directory_name
from python_execution import PythonExecutionContext
from research_log_data import Fingerprint
from validation.file_publication import atomic_replace_text
from validation.json_codec import V2JsonError, decode_json
from validation.pyrun_state import (
    MAX_FILE_BYTES,
    PYRUN_FILENAME,
    PYRUN_SCHEMA,
    PyrunStateError,
    parse_pyrun_state_text,
    script_target_path,
)

SOURCE_SCHEMA = "research-log-pyrun/v6"
MAX_REGISTRIES = 4_096
MAX_CODE_PATHS = 256
MAX_REPORT_WARNINGS = 32
UNAVAILABLE_CODES = {
    "effective_code.source_invalid",
    "effective_code.source_outside_project",
    "effective_code.source_unavailable",
}


class MigrationError(RuntimeError):
    """A migration contract or operational failure."""


@dataclass(frozen=True)
class MigrationWarning:
    registry: str
    cid: str
    execution_id: str
    script: str
    location: str
    line: int
    construct: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cid": self.cid,
            "construct": self.construct,
            "detail": self.detail,
            "execution_id": self.execution_id,
            "line": self.line,
            "location": self.location,
            "registry": self.registry,
            "script": self.script,
        }


@dataclass(frozen=True)
class MigrationReport:
    mode: str
    registries: int
    migrated_registries: int
    current_registries: int
    executions: int
    supported: int
    unsupported: int
    changed: bool
    warning_count: int
    warnings_truncated: bool
    warnings: tuple[MigrationWarning, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "changed": self.changed,
            "current_registries": self.current_registries,
            "executions": self.executions,
            "migrated_registries": self.migrated_registries,
            "mode": self.mode,
            "registries": self.registries,
            "supported": self.supported,
            "unsupported": self.unsupported,
            "warning_count": self.warning_count,
            "warnings": [item.as_dict() for item in self.warnings],
            "warnings_truncated": self.warnings_truncated,
        }


@dataclass(frozen=True)
class _RegistryMigration:
    path: Path
    text: str
    executions: int
    supported: int
    unsupported: int
    changed: bool
    warning_count: int
    warnings: tuple[MigrationWarning, ...]


def migrate_project(
    project_root: Path, *, apply: bool, refresh_current: bool = False
) -> MigrationReport:
    """Validate and optionally migrate every canonical pyrun registry."""

    project = project_root.resolve(strict=True)
    if not project.is_dir() or project.is_symlink():
        raise MigrationError(f"project root is not a regular directory: {project}")
    paths = _discover_registries(project)
    analysis_cache: dict[
        tuple[Path, tuple[Path, ...]], EffectiveCodeAnalysis | EffectiveCodeError
    ] = {}
    candidates = tuple(
        _migrate_registry(
            path,
            project,
            refresh_current=refresh_current,
            analysis_cache=analysis_cache,
        )
        for path in paths
    )
    if apply:
        for candidate in candidates:
            if candidate.changed:
                try:
                    atomic_replace_text(candidate.path, candidate.text)
                except OSError as error:
                    raise MigrationError(
                        f"could not publish {candidate.path}: {error}"
                    ) from error
    migrated = sum(item.changed for item in candidates)
    warning_count = sum(item.warning_count for item in candidates)
    warnings: list[MigrationWarning] = []
    for item in candidates:
        remaining = MAX_REPORT_WARNINGS - len(warnings)
        if remaining <= 0:
            break
        warnings.extend(item.warnings[:remaining])
    return MigrationReport(
        "apply" if apply else "dry-run",
        len(candidates),
        migrated,
        len(candidates) - migrated,
        sum(item.executions for item in candidates),
        sum(item.supported for item in candidates),
        sum(item.unsupported for item in candidates),
        bool(migrated),
        warning_count,
        warning_count > len(warnings),
        tuple(warnings),
    )


def _discover_registries(project: Path) -> tuple[Path, ...]:
    paths = []
    for path in project.rglob(PYRUN_FILENAME):
        if path.is_symlink() or not path.is_file():
            continue
        if path.parent.parent.name != "entries":
            continue
        if parse_entry_directory_name(path.parent.name) is None:
            continue
        paths.append(path.resolve())
        if len(paths) > MAX_REGISTRIES:
            raise MigrationError(
                f"registry discovery exceeds the {MAX_REGISTRIES} registry bound"
            )
    return tuple(sorted(set(paths)))


def _migrate_registry(
    path: Path,
    project: Path,
    *,
    refresh_current: bool,
    analysis_cache: dict[
        tuple[Path, tuple[Path, ...]], EffectiveCodeAnalysis | EffectiveCodeError
    ],
) -> _RegistryMigration:
    raw = _read_registry(path)
    value = _decode_mapping(raw, path)
    schema = value.get("schema")
    if schema == PYRUN_SCHEMA:
        state = parse_pyrun_state_text(
            raw,
            subject=path,
            entry_root=path.parent,
            project_root=project,
        )
        execution_items = tuple(state.execution_items())
        if refresh_current:
            candidate = json.loads(json.dumps(value))
            commands = _mapping(candidate.get("commands"), f"{path}:commands")
        else:
            supported = sum(
                item.observed.effective_code is not None
                for _, _, item in execution_items
            )
            return _RegistryMigration(
                path,
                raw,
                len(execution_items),
                supported,
                len(execution_items) - supported,
                False,
                0,
                (),
            )
    elif schema != SOURCE_SCHEMA:
        raise MigrationError(
            f"unsupported registry schema at {path}: {schema!r}"
        )
    else:
        _require_canonical_v6(value, raw, path)
        candidate = json.loads(json.dumps(value))
        candidate["schema"] = PYRUN_SCHEMA
        commands = _mapping(candidate.get("commands"), f"{path}:commands")
        for raw_command in commands.values():
            command = _mapping(raw_command, f"{path}:command")
            execution_records = _mapping(
                command.get("executions"), f"{path}:executions"
            )
            for raw_execution in execution_records.values():
                execution_record = _mapping(raw_execution, f"{path}:execution")
                observed = _mapping(
                    execution_record.get("observed"), f"{path}:observed"
                )
                observed.pop("code")
                observed["effective_code"] = None
                execution_record["requires_reproduction"] = True
        provisional_text = _canonical(candidate)
        state = parse_pyrun_state_text(
            provisional_text,
            subject=path,
            entry_root=path.parent,
            project_root=project,
        )
    warnings: list[MigrationWarning] = []
    warning_count = 0
    registry_name = path.relative_to(project).as_posix()
    supported = 0
    unsupported = 0
    for cid, identity, execution in state.execution_items():
        script = script_target_path(
            execution.recipe.script,
            entry_root=path.parent,
            project_root=project,
        )
        fingerprint, diagnostic = _analyze_script(
            script,
            project,
            entry_root=path.parent,
            explicit_pythonpath="PYTHONPATH" in dict(execution.recipe.environment),
            analysis_cache=analysis_cache,
        )
        raw_execution = cast(
            dict[str, Any],
            cast(dict[str, Any], commands[cid])["executions"][identity],
        )
        cast(dict[str, Any], raw_execution["observed"])["effective_code"] = (
            fingerprint.as_dict() if fingerprint is not None else None
        )
        raw_execution["requires_reproduction"] = True
        if fingerprint is not None:
            supported += 1
        else:
            unsupported += 1
            execution_warnings = _warnings_for_diagnostic(
                registry_name,
                cid,
                identity,
                execution.recipe.script,
                diagnostic,
            )
            warning_count += len(execution_warnings)
            remaining = MAX_REPORT_WARNINGS - len(warnings)
            if remaining > 0:
                warnings.extend(execution_warnings[:remaining])
    text = _canonical(candidate)
    parse_pyrun_state_text(
        text,
        subject=path,
        entry_root=path.parent,
        project_root=project,
    )
    return _RegistryMigration(
        path,
        text,
        len(state.execution_items()),
        supported,
        unsupported,
        text != raw,
        warning_count,
        tuple(warnings),
    )


def _analyze_script(
    script: Path,
    project: Path,
    *,
    entry_root: Path,
    explicit_pythonpath: bool,
    analysis_cache: dict[
        tuple[Path, tuple[Path, ...]], EffectiveCodeAnalysis | EffectiveCodeError
    ],
) -> tuple[Fingerprint | None, EffectiveCodeAnalysis | EffectiveCodeError | str]:
    if explicit_pythonpath:
        return None, "explicit PYTHONPATH changes project-local import resolution"
    source = Path(os.path.abspath(script))
    python_context = PythonExecutionContext.for_research_script(
        source,
        entry_root=entry_root,
        log_root=entry_root.parent.parent,
        project_root=project,
    )
    key = (source, python_context.import_roots)
    observed = analysis_cache.get(key)
    if observed is None:
        try:
            observed = analyze_effective_code(
                source,
                project_root=project,
                import_roots=python_context.import_roots,
            )
        except EffectiveCodeError as error:
            observed = error
        except Exception as error:
            raise MigrationError(
                f"effective-code analyzer failed internally for {source}: "
                f"{type(error).__name__}: {error}"
            ) from error
        analysis_cache[key] = observed
    if isinstance(observed, EffectiveCodeError):
        if observed.code not in UNAVAILABLE_CODES:
            raise MigrationError(
                f"effective-code analysis failed for {source}: {observed}"
            )
        return None, observed
    if observed.fingerprint is None:
        return None, observed
    return (
        Fingerprint(
            observed.fingerprint.algorithm,
            digest=observed.fingerprint.digest,
        ),
        observed,
    )


def _warnings_for_diagnostic(
    registry: str,
    cid: str,
    identity: str,
    script: str,
    diagnostic: EffectiveCodeAnalysis | EffectiveCodeError | str,
) -> tuple[MigrationWarning, ...]:
    if isinstance(diagnostic, EffectiveCodeAnalysis):
        warnings = tuple(
            MigrationWarning(
                registry,
                cid,
                identity,
                script,
                item.path,
                item.line,
                item.construct,
                item.detail,
            )
            for item in diagnostic.unsupported
        )
        if diagnostic.unsupported_truncated:
            warnings += (
                MigrationWarning(
                    registry,
                    cid,
                    identity,
                    script,
                    script,
                    0,
                    "unsupported_locations_truncated",
                    "additional unsupported locations were omitted",
                ),
            )
        return warnings
    if isinstance(diagnostic, EffectiveCodeError):
        return (
            MigrationWarning(
                registry,
                cid,
                identity,
                script,
                diagnostic.path or script,
                diagnostic.line,
                diagnostic.code,
                diagnostic.detail,
            ),
        )
    return (
        MigrationWarning(
            registry,
            cid,
            identity,
            script,
            script,
            0,
            "import_path_environment",
            diagnostic,
        ),
    )


def _read_registry(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MigrationError(f"could not read {path}: {error}") from error


def _decode_mapping(raw: str, path: Path) -> dict[str, Any]:
    try:
        value = decode_json(raw, maximum_bytes=MAX_FILE_BYTES, subject=str(path))
    except V2JsonError as error:
        raise MigrationError(f"invalid registry {path}: {error}") from error
    if not isinstance(value, dict):
        raise MigrationError(f"registry is not an object: {path}")
    return cast(dict[str, Any], value)


def _require_canonical_v6(value: dict[str, Any], raw: str, path: Path) -> None:
    if set(value) != {"commands", "schema"} or value.get("schema") != SOURCE_SCHEMA:
        raise MigrationError(f"invalid v6 registry fields: {path}")
    commands = _mapping(value.get("commands"), f"{path}:commands")
    for cid, raw_command in commands.items():
        command = _mapping(raw_command, f"{path}:commands[{cid!r}]")
        if set(command) != {"executions"}:
            raise MigrationError(f"invalid v6 command fields: {path}:{cid}")
        executions = _mapping(command.get("executions"), f"{path}:{cid}:executions")
        for identity, raw_execution in executions.items():
            execution = _mapping(raw_execution, f"{path}:{cid}:{identity}")
            observed = _mapping(execution.get("observed"), f"{path}:{cid}:{identity}")
            if set(observed) != {"code", "inputs", "outputs", "script"}:
                raise MigrationError(f"invalid v6 observed fields: {path}:{identity}")
            code = _mapping(observed.get("code"), f"{path}:{identity}:code")
            if len(code) > MAX_CODE_PATHS:
                raise MigrationError(f"v6 code map exceeds bound: {path}:{identity}")
            for code_path, fingerprint in code.items():
                if not isinstance(code_path, str):
                    raise MigrationError(f"invalid v6 code path: {path}:{identity}")
                _require_sha256(fingerprint, f"{path}:{identity}:{code_path}")
    if raw != _canonical(value):
        raise MigrationError(f"noncanonical v6 registry: {path}")


def _require_sha256(value: object, subject: str) -> None:
    item = _mapping(value, subject)
    if (
        set(item) != {"algorithm", "digest"}
        or item.get("algorithm") != "sha256"
        or not isinstance(item.get("digest"), str)
        or len(cast(str, item["digest"])) != 64
        or any(character not in "0123456789abcdef" for character in item["digest"])
    ):
        raise MigrationError(f"invalid v6 fingerprint: {subject}")


def _mapping(value: object, subject: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MigrationError(f"expected object at {subject}")
    return cast(dict[str, Any], value)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replace canonical pyrun v6 registries with v7, or refresh current "
            "v7 effective-code observations without clearing reproduction."
        )
    )
    parser.add_argument("--root", required=True, type=Path, help="Git project root")
    parser.add_argument(
        "--refresh-current",
        action="store_true",
        help="recompute canonical v7 observations and retain reproduction requirements",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="validate and report")
    mode.add_argument(
        "--apply", action="store_true", help="atomically replace changed registries"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = migrate_project(
            arguments.root,
            apply=arguments.apply,
            refresh_current=arguments.refresh_current,
        )
    except (MigrationError, PyrunStateError, OSError) as error:
        print(f"migrate-pyrun-effective-code: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
