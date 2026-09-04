"""Strict artifact-linked execution observations owned by ``pyrun``."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, NoReturn, cast

from research_log_data import DataContractError, Fingerprint, parse_fingerprint

from .entry_materials import is_entry_material_path
from .errors import MechanicalContractError
from .json_codec import V2JsonError, decode_json

PYRUN_OUTPUTS_SCHEMA = "research-log-pyrun-outputs/v1"
PYRUN_OUTPUTS_FILENAME = "pyrun-outputs.json"
PYRUN_OUTPUTS_BACKUP_RE = re.compile(r"pyrun-outputs\.json(?:\.[2-9][0-9]*)?\.bak\Z")
PROJECT_OUTPUT_PREFIX = "<project>/"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_OUTPUTS = 10_000
MAX_INPUTS = 128
MAX_PARAMETERS = 4_096
MAX_STRING_BYTES = 8 * 1024
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

class PyrunOutputsError(MechanicalContractError):
    """One exact output-support contract failure."""


@dataclass(frozen=True)
class ScriptSupport:
    """The directly executed script and its observed byte identity."""

    path: str
    fingerprint: Fingerprint

    def as_dict(self) -> dict[str, object]:
        return {"fingerprint": self.fingerprint.as_dict(), "path": self.path}


@dataclass(frozen=True)
class OutputSupport:
    """Current execution support for one exact output artifact."""

    confirmed: bool
    fingerprint: Fingerprint
    script: ScriptSupport
    parameters: tuple[str, ...]
    inputs: tuple[tuple[str, Fingerprint], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "confirmed": self.confirmed,
            "fingerprint": self.fingerprint.as_dict(),
            "inputs": {name: value.as_dict() for name, value in self.inputs},
            "parameters": list(self.parameters),
            "script": self.script.as_dict(),
        }


@dataclass(frozen=True)
class PyrunOutputsFile:
    """One entry-owned mapping from output identity to current support."""

    path: Path
    entry_root: Path
    outputs: Mapping[str, OutputSupport]

    def as_dict(self) -> dict[str, object]:
        return {
            "outputs": {
                key: self.outputs[key].as_dict() for key in sorted(self.outputs)
            },
            "schema": PYRUN_OUTPUTS_SCHEMA,
        }

    def serialized(self) -> str:
        return json.dumps(
            self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"


def load_pyrun_outputs(
    path: Path, *, entry_root: Path, project_root: Path | None = None
) -> PyrunOutputsFile:
    """Read one strict entry-root output-support file."""

    expected = entry_root.resolve() / PYRUN_OUTPUTS_FILENAME
    if path.is_symlink() or path.resolve() != expected:
        _invalid(path, {"expected": str(expected), "reason": "location"})
    try:
        raw = path.read_text(encoding="utf-8")
        value = decode_json(raw, maximum_bytes=MAX_FILE_BYTES, subject=str(path))
    except (OSError, UnicodeError, V2JsonError) as error:
        _invalid(path, {"error": str(error)})
    if not isinstance(value, Mapping) or set(value) != {"schema", "outputs"}:
        _invalid(path, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    raw_outputs = value.get("outputs")
    if value.get("schema") != PYRUN_OUTPUTS_SCHEMA or not isinstance(
        raw_outputs, Mapping
    ):
        _invalid(path, {"schema": value.get("schema")})
    if len(raw_outputs) > MAX_OUTPUTS:
        _invalid(path, {"outputs": len(raw_outputs), "limit": MAX_OUTPUTS})
    outputs: dict[str, OutputSupport] = {}
    for key, raw_record in raw_outputs.items():
        output = portable_output_path(
            key,
            entry_root=entry_root,
            project_root=project_root,
            authored=True,
        )
        if output != key:
            _invalid(path, {"output": key, "canonical": output})
        outputs[key] = _decode_record(raw_record, f"{path}:outputs[{key!r}]")
    return PyrunOutputsFile(expected, entry_root.resolve(), outputs)


def empty_pyrun_outputs(entry_root: Path) -> PyrunOutputsFile:
    """Return an empty current-support surface for one entry."""

    root = entry_root.resolve()
    return PyrunOutputsFile(root / PYRUN_OUTPUTS_FILENAME, root, {})


def without_output_support(
    entry_root: Path,
    outputs: tuple[str, ...],
    *,
    project_root: Path | None = None,
) -> PyrunOutputsFile:
    """Build validated support with exact selected output records retired."""

    root = entry_root.resolve()
    path = root / PYRUN_OUTPUTS_FILENAME
    current = (
        load_pyrun_outputs(path, entry_root=root, project_root=project_root)
        if path.exists() or path.is_symlink()
        else empty_pyrun_outputs(root)
    )
    selected = tuple(
        portable_output_path(
            item,
            entry_root=root,
            project_root=project_root,
            authored=True,
        )
        for item in outputs
    )
    if len(selected) != len(set(selected)):
        _invalid(path, {"reason": "duplicate_output_retirement"})
    missing = sorted(set(selected) - set(current.outputs))
    if missing:
        _invalid(path, {"reason": "output_support_missing", "outputs": missing})
    result = PyrunOutputsFile(
        path,
        root,
        {key: value for key, value in current.outputs.items() if key not in selected},
    )
    _validated_serialization(result, project_root=project_root)
    return result


def portable_output_path(
    value: str | Path,
    *,
    entry_root: Path,
    project_root: Path | None = None,
    authored: bool = False,
) -> str:
    """Return one canonical entry- or project-relative output identity."""

    raw = value.as_posix() if isinstance(value, Path) else value
    if not isinstance(raw, str) or not raw or len(raw.encode()) > MAX_STRING_BYTES:
        _invalid("output", {"path": raw})
    root = entry_root.resolve()
    project = (project_root or root).resolve()
    lexical, project_declared = _output_lexical_path(
        raw, root=root, project=project, authored=authored
    )
    entry_key = _entry_output_key(raw, lexical=lexical, root=root)
    if entry_key is not None:
        return entry_key
    return _project_output_key(
        raw,
        lexical=lexical,
        project=project,
        project_declared=project_declared,
        authored=authored,
    )


def _output_lexical_path(
    raw: str, *, root: Path, project: Path, authored: bool
) -> tuple[Path, bool]:
    """Resolve syntax without accepting an implicit project-level output."""

    project_declared = raw.startswith(PROJECT_OUTPUT_PREFIX)
    if raw == "<project>" or raw.startswith("<project>") and not project_declared:
        _invalid("output", {"path": raw, "reason": "project_path_invalid"})
    if project_declared:
        suffix = raw.removeprefix(PROJECT_OUTPUT_PREFIX)
        segments = suffix.split("/")
        if (
            not suffix
            or suffix.startswith("/")
            or "\\" in suffix
            or any(character in suffix for character in "<>")
            or any(part in {"", ".", ".."} for part in segments)
        ):
            _invalid("output", {"path": raw, "reason": "project_path_invalid"})
        return project.joinpath(*segments), True
    path = Path(raw)
    if authored and path.is_absolute():
        _invalid("output", {"path": raw, "reason": "absolute_path"})
    return (path if path.is_absolute() else root / path), False


def _entry_output_key(raw: str, *, lexical: Path, root: Path) -> str | None:
    """Return the canonical entry-material key when the target belongs to it."""

    relative = _entry_output_relative(lexical, root)
    if relative is None:
        return None
    portable = PurePosixPath(*relative.parts).as_posix()
    if (
        portable in {"", "."}
        or any(part in {"", ".", ".."} for part in PurePosixPath(portable).parts)
        or not is_entry_material_path(root / portable, root)
    ):
        _invalid("output", {"path": raw, "reason": "not_entry_material"})
    return portable


def _entry_output_relative(lexical: Path, root: Path) -> Path | None:
    """Map lexical, platform-aliased, and supported material-root paths."""

    try:
        return lexical.absolute().relative_to(root)
    except ValueError:
        pass
    canonical = lexical.resolve()
    try:
        return canonical.relative_to(root)
    except ValueError:
        return _symlinked_material_relative(canonical, root)


def _symlinked_material_relative(canonical: Path, root: Path) -> Path | None:
    """Map a target beneath one supported entry material-root symlink."""

    for name in ("data", "images"):
        material_root = root / name
        if not material_root.is_symlink():
            continue
        try:
            member = canonical.relative_to(material_root.resolve())
        except ValueError:
            continue
        return Path(name) / member
    return None


def _project_output_key(
    raw: str,
    *,
    lexical: Path,
    project: Path,
    project_declared: bool,
    authored: bool,
) -> str:
    """Return a portable project key after enforcing both project boundaries."""

    if authored and not project_declared:
        _invalid("output", {"path": raw, "reason": "outside_entry"})
    canonical = lexical.resolve()
    try:
        canonical_relative = canonical.relative_to(project)
    except ValueError:
        _invalid("output", {"path": raw, "reason": "outside_project"})
    if project_declared:
        project_relative = lexical.absolute().relative_to(project)
        if project_relative != canonical_relative:
            _invalid("output", {"path": raw, "reason": "project_path_alias"})
    else:
        project_relative = canonical_relative
    if not project_relative.parts:
        _invalid("output", {"path": raw, "reason": "project_root"})
    return PROJECT_OUTPUT_PREFIX + PurePosixPath(*project_relative.parts).as_posix()


def output_target_path(
    value: str | Path,
    *,
    entry_root: Path,
    project_root: Path | None = None,
    authored: bool = False,
) -> Path:
    """Resolve one canonical portable output identity to its lexical target."""

    root = entry_root.resolve()
    project = (project_root or root).resolve()
    key = portable_output_path(
        value,
        entry_root=root,
        project_root=project,
        authored=authored,
    )
    if key.startswith(PROJECT_OUTPUT_PREFIX):
        suffix = key.removeprefix(PROJECT_OUTPUT_PREFIX)
        return project.joinpath(*PurePosixPath(suffix).parts)
    return root.joinpath(*PurePosixPath(key).parts)


def update_pyrun_outputs(
    entry_root: Path,
    updates: Mapping[str, OutputSupport],
    *,
    project_root: Path | None = None,
) -> PyrunOutputsFile:
    """Atomically replace support for only the supplied output identities."""

    return _update_pyrun_outputs(
        entry_root, updates, project_root=project_root, lock_held=False
    )


def update_pyrun_outputs_locked(
    entry_root: Path,
    updates: Mapping[str, OutputSupport],
    *,
    project_root: Path | None = None,
) -> PyrunOutputsFile:
    """Update output support while the shared stable entry lock is held."""

    return _update_pyrun_outputs(
        entry_root, updates, project_root=project_root, lock_held=True
    )


def _update_pyrun_outputs(
    entry_root: Path,
    updates: Mapping[str, OutputSupport],
    *,
    project_root: Path | None,
    lock_held: bool,
) -> PyrunOutputsFile:
    root = entry_root.resolve()
    path = root / PYRUN_OUTPUTS_FILENAME
    normalized = {
        portable_output_path(
            key,
            entry_root=root,
            project_root=project_root,
            authored=True,
        ): value
        for key, value in updates.items()
    }
    if len(normalized) != len(updates):
        _invalid(path, {"reason": "duplicate_output"})
    try:
        with nullcontext() if lock_held else _outputs_lock(path):
            current = (
                load_pyrun_outputs(
                    path, entry_root=root, project_root=project_root
                )
                if path.exists()
                else empty_pyrun_outputs(root)
            )
            outputs = dict(current.outputs)
            outputs.update(normalized)
            result = PyrunOutputsFile(path, root, outputs)
            serialized = _validated_serialization(
                result, project_root=project_root
            )
            _atomic_write(path, serialized)
            return result
    except OSError as error:
        raise PyrunOutputsError(
            "pyrun.outputs.unavailable",
            str(path),
            {"error": str(error)},
            "Pyrun Output Support Records",
        ) from error


def quarantine_invalid_pyrun_outputs(
    entry_root: Path, *, project_root: Path | None = None
) -> None:
    """Preserve malformed support, replace it empty, and require Repair."""

    root = entry_root.resolve()
    path = root / PYRUN_OUTPUTS_FILENAME
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        load_pyrun_outputs(path, entry_root=root, project_root=project_root)
        return
    try:
        load_pyrun_outputs(path, entry_root=root, project_root=project_root)
        return
    except PyrunOutputsError:
        pass
    backup = root / f"{PYRUN_OUTPUTS_FILENAME}.bak"
    number = 2
    while backup.exists() or backup.is_symlink():
        backup = root / f"{PYRUN_OUTPUTS_FILENAME}.{number}.bak"
        number += 1
    try:
        os.replace(path, backup)
        _atomic_write(path, empty_pyrun_outputs(root).serialized())
    except OSError as error:
        rollback_error: OSError | None = None
        if not path.exists() and not path.is_symlink() and backup.exists():
            try:
                os.replace(backup, path)
            except OSError as rollback:
                rollback_error = rollback
        raise PyrunOutputsError(
            "pyrun.outputs.quarantine_failed",
            str(path),
            {
                "backup": str(backup),
                "error": str(error),
                "rollback_error": (
                    str(rollback_error) if rollback_error is not None else None
                ),
            },
            "Pyrun Output Support Records",
        ) from error
    raise PyrunOutputsError(
        "pyrun.outputs.quarantined",
        str(path),
        {"backup": str(backup), "current": str(path), "repair_required": True},
        "Pyrun Output Support Records",
    )


def _validated_serialization(
    value: PyrunOutputsFile, *, project_root: Path | None = None
) -> str:
    """Return serialized state only when the writer and reader contracts agree."""

    if len(value.outputs) > MAX_OUTPUTS:
        _invalid(value.path, {"outputs": len(value.outputs), "limit": MAX_OUTPUTS})
    for key, record in value.outputs.items():
        canonical = portable_output_path(
            key,
            entry_root=value.entry_root,
            project_root=project_root,
            authored=True,
        )
        if canonical != key or not isinstance(record, OutputSupport):
            _invalid(value.path, {"output": key})
        _decode_record(record.as_dict(), f"{value.path}:outputs[{key!r}]")
    serialized = value.serialized()
    size = len(serialized.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        _invalid(value.path, {"bytes": size, "limit": MAX_FILE_BYTES})
    return serialized


def _decode_record(value: object, subject: str) -> OutputSupport:
    required = {"confirmed", "fingerprint", "inputs", "parameters", "script"}
    if not isinstance(value, Mapping) or set(value) != required:
        _invalid(subject, {"fields": _fields(value)})
    value = cast(Mapping[str, Any], value)
    confirmed = value.get("confirmed")
    parameters = value.get("parameters")
    inputs = value.get("inputs")
    script = value.get("script")
    if not isinstance(confirmed, bool):
        _invalid(subject, {"confirmed": confirmed})
    if (
        not isinstance(parameters, list)
        or len(parameters) > MAX_PARAMETERS
        or not all(_bounded_parameter(item) for item in parameters)
    ):
        _invalid(subject, {"parameters": parameters})
    if not isinstance(inputs, Mapping) or len(inputs) > MAX_INPUTS:
        _invalid(subject, {"inputs": _fields(inputs)})
    decoded_inputs: list[tuple[str, Fingerprint]] = []
    for name, fingerprint in inputs.items():
        if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
            _invalid(subject, {"input": name})
        decoded_inputs.append((name, _decode_fingerprint(fingerprint, subject)))
    if not isinstance(script, Mapping) or set(script) != {"path", "fingerprint"}:
        _invalid(subject, {"script": _fields(script)})
    script_path = script.get("path")
    if not _bounded_string(script_path):
        _invalid(subject, {"script_path": script_path})
    return OutputSupport(
        confirmed,
        _decode_fingerprint(value.get("fingerprint"), subject),
        ScriptSupport(
            cast(str, script_path),
            _decode_fingerprint(script.get("fingerprint"), subject, file_only=True),
        ),
        tuple(cast(list[str], parameters)),
        tuple(sorted(decoded_inputs)),
    )


def _decode_fingerprint(
    value: object, subject: str, *, file_only: bool = False
) -> Fingerprint:
    try:
        return parse_fingerprint(
            value, subject, kind="file" if file_only else None
        )
    except DataContractError as error:
        _invalid(subject, {"fingerprint": value, "reason": error.code})


@contextmanager
def _outputs_lock(path: Path) -> Iterator[None]:
    identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    lock = Path(tempfile.gettempdir()) / f"pyrun-outputs-{identity}.lock"
    with lock.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_string(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= MAX_STRING_BYTES
    )


def _bounded_parameter(value: object) -> bool:
    return isinstance(value, str) and len(value.encode("utf-8")) <= MAX_STRING_BYTES


def _fields(value: object) -> object:
    return sorted(value) if isinstance(value, Mapping) else type(value).__name__


def _invalid(subject: object, observed: object) -> NoReturn:
    raise PyrunOutputsError(
        "pyrun.outputs.invalid",
        str(subject),
        observed,
        "Pyrun Output Support Records",
    )
