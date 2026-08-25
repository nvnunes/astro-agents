"""Transient activity logging for one validation CLI invocation."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .contracts import ValidationToolError

ACTIVITY_LOG_FILENAME = "validation/.cache/validation.log"
DEFAULT_HEARTBEAT_SECONDS = 30.0


@dataclass(frozen=True)
class ValidationActivityRequest:
    """Inputs that identify and configure one transient activity log."""

    output_dir: Path
    summary: Path
    mode: str
    jobs: int
    publish: bool
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS


def log_phase(
    activity: ValidationActivityLog | None, name: str, **fields: Any
) -> None:
    """Record a phase only when one CLI activity log is active."""

    if activity is not None:
        activity.phase(name, **fields)


def log_checkpoint(
    activity: ValidationActivityLog | None, name: str, **fields: Any
) -> None:
    """Record a checkpoint only when one CLI activity log is active."""

    if activity is not None:
        activity.checkpoint(name, **fields)


@contextmanager
def log_operation(
    activity: ValidationActivityLog | None,
    name: str,
    *,
    subject: str | None = None,
    **fields: Any,
) -> Iterator[None]:
    """Track an operation or act as a no-op outside the validation CLI."""

    if activity is None:
        yield
        return
    with activity.operation(name, subject=subject, **fields):
        yield


class ValidationActivityLog:
    """Write tail-friendly, noncanonical progress for one validator run.

    The log is transient validator-owned state. Each invocation replaces the
    previous log, appends one flushed line per event, and emits periodic
    heartbeats that identify the current phase and oldest active operation.
    """

    def __init__(
        self,
        request: ValidationActivityRequest,
    ) -> None:
        if request.heartbeat_seconds <= 0:
            raise ValidationToolError(
                "validation activity heartbeat must be positive"
            )
        self.path = self._prepare_path(request.output_dir.resolve())
        self.run_id = uuid.uuid4().hex[:12]
        self._started = time.monotonic()
        self._heartbeat_seconds = request.heartbeat_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._finished = False
        self._disabled = False
        self._phase = "startup"
        self._phase_started = self._started
        self._active: dict[str, tuple[str, str | None, float]] = {}
        self._replace_log()
        self._write(
            "run-start",
            summary=request.summary.resolve().as_posix(),
            mode=request.mode,
            jobs=request.jobs,
            publish=request.publish,
        )
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name=f"validation-activity-{self.run_id}",
            daemon=True,
        )
        self._heartbeat.start()

    @staticmethod
    def _prepare_path(output_dir: Path) -> Path:
        current = output_dir
        for part in Path(ACTIVITY_LOG_FILENAME).parts:
            current /= part
            if current.is_symlink():
                raise ValidationToolError(
                    "validation activity-log path must not contain a symlink"
                )
        current.parent.mkdir(parents=True, exist_ok=True)
        if current.exists() and not current.is_file():
            raise ValidationToolError(
                "validation activity-log path must be a regular file"
            )
        return current

    def _replace_log(self) -> None:
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.flush()

    @staticmethod
    def _field(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _write(self, event: str, **fields: Any) -> None:
        with self._lock:
            if self._disabled:
                return
            elapsed = time.monotonic() - self._started
            values = {
                "run": self.run_id,
                "elapsed_seconds": round(elapsed, 3),
                "event": event,
                **fields,
            }
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            rendered = " ".join(
                f"{key}={self._field(value)}" for key, value in values.items()
            )
            try:
                if self.path.is_symlink():
                    raise OSError("validation activity log became a symlink")
                with self.path.open(
                    "a", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(f"{timestamp} {rendered}\n")
                    handle.flush()
            except OSError:
                self._disabled = True
                self._stop.set()

    def phase(self, name: str, **fields: Any) -> None:
        """Record a lifecycle transition and make it the heartbeat phase."""

        with self._lock:
            previous = self._phase
            previous_elapsed = time.monotonic() - self._phase_started
            self._phase = name
            self._phase_started = time.monotonic()
        self._write(
            "phase",
            phase=name,
            previous_phase=previous,
            previous_phase_seconds=round(previous_elapsed, 3),
            **fields,
        )

    def checkpoint(self, name: str, **fields: Any) -> None:
        """Record detailed bounded progress within the current phase."""

        self._write("checkpoint", checkpoint=name, phase=self._phase, **fields)

    @contextmanager
    def operation(
        self, name: str, *, subject: str | None = None, **fields: Any
    ) -> Iterator[None]:
        """Track one potentially blocking operation for heartbeat diagnosis."""

        token = uuid.uuid4().hex
        started = time.monotonic()
        with self._lock:
            self._active[token] = (name, subject, started)
        self._write(
            "operation-start",
            phase=self._phase,
            operation=name,
            subject=subject,
            **fields,
        )
        try:
            yield
        except BaseException as exc:
            self._write(
                "operation-error",
                phase=self._phase,
                operation=name,
                subject=subject,
                duration_seconds=round(time.monotonic() - started, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        else:
            self._write(
                "operation-complete",
                phase=self._phase,
                operation=name,
                subject=subject,
                duration_seconds=round(time.monotonic() - started, 3),
            )
        finally:
            with self._lock:
                self._active.pop(token, None)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            now = time.monotonic()
            with self._lock:
                active = list(self._active.values())
                phase = self._phase
                phase_elapsed = now - self._phase_started
            fields: dict[str, Any] = {
                "phase": phase,
                "phase_seconds": round(phase_elapsed, 3),
                "active_operations": len(active),
            }
            if active:
                operation, subject, started = min(active, key=lambda item: item[2])
                fields.update(
                    {
                        "oldest_operation": operation,
                        "oldest_subject": subject,
                        "oldest_operation_seconds": round(now - started, 3),
                    }
                )
            self._write("heartbeat", **fields)

    def finish(self, status: str, **fields: Any) -> None:
        """Stop heartbeats and write one terminal line exactly once."""

        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._stop.set()
        self._heartbeat.join(timeout=self._heartbeat_seconds + 1.0)
        self._write("run-finish", status=status, phase=self._phase, **fields)
