"""Process-local observation for Python code loaded beneath one research log."""

from __future__ import annotations

import atexit
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence, cast

TRACE_DIRECTORY_ENV = "PYRUN_CODE_TRACE_DIRECTORY"
LOG_ROOT_ENV = "PYRUN_CODE_LOG_ROOT"
OBSERVER_MODULE_ENV = "PYRUN_CODE_OBSERVER_MODULE"
MANAGED_ENVIRONMENT = frozenset(
    {TRACE_DIRECTORY_ENV, LOG_ROOT_ENV, OBSERVER_MODULE_ENV}
)
MAX_CODE_PATHS = 256
MAX_TRACE_FILES = 4_096
MAX_TRACE_BYTES = 1024 * 1024
_TRACE_PREFIX = "process-"
_TRACE_SUFFIX = ".json"

FileIdentity = tuple[int, int, int, int, int]


class CodeObservationError(RuntimeError):
    """One unavailable or invalid runtime code-dependency observation."""


@dataclass(frozen=True)
class ObservedCodePath:
    """One loaded logical path and its import-time resolved file identity."""

    logical: Path
    resolved: Path
    identity: FileIdentity


class _RecordingLoader(importlib.abc.Loader):
    """Delegate one ordinary loader and record its successfully loaded source."""

    def __init__(self, delegate: importlib.abc.Loader, recorder: _Recorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        creator = getattr(self._delegate, "create_module", None)
        return creator(spec) if creator is not None else None

    def exec_module(self, module: ModuleType) -> None:
        executor = getattr(self._delegate, "exec_module", None)
        if executor is None:
            raise ImportError(f"loader cannot execute {module.__name__}")
        executor(module)
        self._recorder.record(getattr(module, "__file__", None))


class _RecordingFinder(importlib.abc.MetaPathFinder):
    """Wrap regular filesystem source loaders without changing their search."""

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or not isinstance(
            spec.loader, importlib.machinery.SourceFileLoader
        ):
            return spec
        spec.loader = _RecordingLoader(spec.loader, self._recorder)
        return spec


class _Recorder:
    """Collect one interpreter's bounded set of eligible loaded source files."""

    def __init__(self, trace_directory: Path, log_root: Path) -> None:
        self._trace_directory = trace_directory
        self._log_root = log_root
        self._pid = os.getpid()
        self._records: dict[str, ObservedCodePath] = {}
        self._error: str | None = None
        self._forked = False
        self._create_marker()

    def record(self, value: object) -> None:
        observed = self._eligible_path(value)
        if observed is None or observed.resolved.as_posix() in self._records:
            return
        if len(self._records) >= MAX_CODE_PATHS + 1:
            self._error = "code_path_limit"
        else:
            self._records[observed.resolved.as_posix()] = observed
        if self._forked:
            self._write_trace()

    def after_fork(self) -> None:
        self._pid = os.getpid()
        self._records = {}
        self._error = None
        self._forked = True
        self._create_marker()

    def finish(self) -> None:
        self._write_trace()

    def _eligible_path(self, value: object) -> ObservedCodePath | None:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return None
        try:
            raw = os.fsdecode(value)
            logical = Path(os.path.abspath(raw))
            relative = logical.relative_to(self._log_root)
            if not relative.parts or logical.suffix != ".py":
                return None
            resolved = logical.resolve(strict=True)
            observation = resolved.stat()
        except (OSError, TypeError, ValueError):
            return None
        if not stat.S_ISREG(observation.st_mode):
            return None
        return ObservedCodePath(logical, resolved, _file_identity(observation))

    def _create_marker(self) -> None:
        try:
            descriptor = os.open(
                self._trace_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            return
        except OSError:
            self._error = "trace_unavailable"
            return
        os.close(descriptor)

    def _write_trace(self) -> None:
        payload = {
            "error": self._error,
            "pid": self._pid,
            "records": [
                {
                    "identity": list(record.identity),
                    "logical": record.logical.as_posix(),
                    "resolved": record.resolved.as_posix(),
                }
                for record in sorted(
                    self._records.values(), key=lambda item: item.logical.as_posix()
                )
            ],
        }
        temporary = self._trace_path().with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._trace_path())
        except OSError:
            temporary.unlink(missing_ok=True)

    def _trace_path(self) -> Path:
        return self._trace_directory / f"{_TRACE_PREFIX}{self._pid}{_TRACE_SUFFIX}"


def install() -> None:
    """Install process-local observation from the inherited private context."""

    trace = os.environ.get(TRACE_DIRECTORY_ENV)
    log = os.environ.get(LOG_ROOT_ENV)
    if trace is None or log is None:
        return
    recorder = _Recorder(Path(trace), Path(log))
    finder = _RecordingFinder(recorder)
    try:
        index = sys.meta_path.index(importlib.machinery.PathFinder)
    except ValueError:
        index = len(sys.meta_path)
    sys.meta_path.insert(index, finder)
    recorder.record(sys.argv[0] if sys.argv else None)
    if hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=recorder.after_fork)
    atexit.register(recorder.finish)


def prepare_environment(
    environment: Mapping[str, str], *, temporary_root: Path, log_root: Path
) -> tuple[dict[str, str], Path]:
    """Create one inherited observer context without exposing it to signatures."""

    trace_directory = temporary_root / "python-code-traces"
    bootstrap_directory = temporary_root / "python-startup"
    trace_directory.mkdir()
    bootstrap_directory.mkdir()
    bootstrap = bootstrap_directory / "sitecustomize.py"
    bootstrap.write_text(_bootstrap_source(), encoding="utf-8")
    result = dict(environment)
    inherited = result.get("PYTHONPATH")
    result["PYTHONPATH"] = (
        str(bootstrap_directory)
        if not inherited
        else os.pathsep.join((str(bootstrap_directory), inherited))
    )
    result[TRACE_DIRECTORY_ENV] = str(trace_directory)
    result[LOG_ROOT_ENV] = str(log_root)
    result[OBSERVER_MODULE_ENV] = str(Path(__file__).resolve())
    return result, trace_directory


def load_traces(
    trace_directory: Path, *, root_pid: int
) -> tuple[ObservedCodePath, ...]:
    """Load complete bounded traces and require the root interpreter's record."""

    try:
        candidates = tuple(trace_directory.iterdir())
    except OSError as error:
        raise CodeObservationError(
            f"code trace directory unavailable: {error}"
        ) from error
    if len(candidates) > MAX_TRACE_FILES:
        raise CodeObservationError(
            f"code trace count exceeds {MAX_TRACE_FILES}: {len(candidates)}"
        )
    expected_root = f"{_TRACE_PREFIX}{root_pid}{_TRACE_SUFFIX}"
    root_complete = False
    records: list[ObservedCodePath] = []
    for path in candidates:
        payload = _completed_trace(path, expected_root=expected_root)
        if payload is None:
            continue
        if path.name == expected_root:
            root_complete = True
        records.extend(_trace_records(payload, path))
    if not root_complete:
        raise CodeObservationError("root Python process produced no complete trace")
    return tuple(records)


def _completed_trace(path: Path, *, expected_root: str) -> Mapping[str, Any] | None:
    """Return one complete trace, ignoring an unfinished descendant marker."""

    if not path.name.startswith(_TRACE_PREFIX) or path.suffix not in {
        _TRACE_SUFFIX,
        ".tmp",
    }:
        raise CodeObservationError(f"unexpected code trace path: {path.name}")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise CodeObservationError(f"code trace unavailable: {error}") from error
    if path.suffix == ".tmp" or size == 0:
        if path.name == expected_root:
            raise CodeObservationError("root Python process left no complete trace")
        return None
    if size > MAX_TRACE_BYTES:
        raise CodeObservationError(
            f"code trace exceeds {MAX_TRACE_BYTES} bytes: {path.name}"
        )
    payload = _load_trace(path)
    if payload.get("error") is not None:
        raise CodeObservationError(
            f"code observation failed in process {payload.get('pid')}: "
            f"{payload.get('error')}"
        )
    return payload


def _load_trace(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CodeObservationError(
            f"invalid code trace {path.name}: {error}"
        ) from error
    if not isinstance(value, Mapping) or set(value) != {"error", "pid", "records"}:
        raise CodeObservationError(f"invalid code trace shape: {path.name}")
    return cast(Mapping[str, Any], value)


def _trace_records(
    payload: Mapping[str, Any], path: Path
) -> tuple[ObservedCodePath, ...]:
    pid = payload.get("pid")
    raw_records = payload.get("records")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(raw_records, list):
        raise CodeObservationError(f"invalid code trace values: {path.name}")
    expected_name = f"{_TRACE_PREFIX}{pid}{_TRACE_SUFFIX}"
    if path.name != expected_name or len(raw_records) > MAX_CODE_PATHS + 1:
        raise CodeObservationError(f"invalid code trace identity: {path.name}")
    records: list[ObservedCodePath] = []
    for value in raw_records:
        if not isinstance(value, Mapping) or set(value) != {
            "identity",
            "logical",
            "resolved",
        }:
            raise CodeObservationError(f"invalid code trace record: {path.name}")
        logical = value.get("logical")
        resolved = value.get("resolved")
        identity = value.get("identity")
        if (
            not isinstance(logical, str)
            or not Path(logical).is_absolute()
            or not isinstance(resolved, str)
            or not Path(resolved).is_absolute()
            or not isinstance(identity, list)
            or len(identity) != 5
            or not all(isinstance(item, int) and item >= 0 for item in identity)
        ):
            raise CodeObservationError(f"invalid code trace record: {path.name}")
        records.append(
            ObservedCodePath(
                Path(logical), Path(resolved), cast(FileIdentity, tuple(identity))
            )
        )
    return tuple(records)


def _bootstrap_source() -> str:
    """Return the isolated startup shim and preserve downstream sitecustomize."""

    return """\
import importlib
import importlib.util
import os
import sys

bootstrap_directory = os.path.dirname(__file__)
sys.path[:] = [item for item in sys.path if item != bootstrap_directory]
module_path = os.environ[\"PYRUN_CODE_OBSERVER_MODULE\"]
spec = importlib.util.spec_from_file_location(\"_pyrun_code_observer\", module_path)
if spec is None or spec.loader is None:
    raise ImportError(\"pyrun code observer is unavailable\")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.install()

current = sys.modules.pop(\"sitecustomize\", None)
try:
    importlib.import_module(\"sitecustomize\")
except ModuleNotFoundError as error:
    if error.name != \"sitecustomize\":
        raise
finally:
    if \"sitecustomize\" not in sys.modules and current is not None:
        sys.modules[\"sitecustomize\"] = current
"""


def _file_identity(observation: os.stat_result) -> FileIdentity:
    return (
        observation.st_dev,
        observation.st_ino,
        observation.st_size,
        observation.st_mtime_ns,
        observation.st_ctime_ns,
    )
