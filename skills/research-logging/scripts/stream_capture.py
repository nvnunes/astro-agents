"""Shared bounded byte-stream capture for research command execution."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import IO, BinaryIO, Sequence

DEFAULT_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class StreamDestination:
    """One explicit byte destination and its caller-selected failure policy."""

    name: str
    stream: BinaryIO
    required: bool
    flush_each_chunk: bool = False
    durable: bool = False


@dataclass(frozen=True)
class StreamFailure:
    """One source or destination failure observed while draining a stream."""

    name: str
    required: bool
    error: BaseException


@dataclass(frozen=True)
class _StreamPump:
    source: IO[bytes]
    thread: threading.Thread


class StreamCapture:
    """Drain process pipes to independent destinations with bounded cleanup.

    Callers own process policy and destination lifetimes. The capture owns each
    supplied source until it reaches EOF or cleanup fails, and reports required
    failures through ``required_failed`` without deciding how to stop a child.
    """

    def __init__(self, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> None:
        if chunk_bytes <= 0:
            raise ValueError("stream capture chunk size must be positive")
        self._chunk_bytes = chunk_bytes
        self._pumps: list[_StreamPump] = []
        self._failures: list[StreamFailure] = []
        self._failure_lock = threading.Lock()
        self._finished = False
        self.required_failed = threading.Event()

    @property
    def failures(self) -> tuple[StreamFailure, ...]:
        """Return failures in their recorded order."""

        with self._failure_lock:
            return tuple(self._failures)

    def start(
        self, source: IO[bytes], destinations: Sequence[StreamDestination]
    ) -> None:
        """Start draining one source to at least one explicit destination."""

        if self._finished:
            raise RuntimeError("stream capture is already finished")
        if not destinations:
            raise ValueError("stream capture requires a destination")
        thread = threading.Thread(
            target=self._pump,
            args=(source, tuple(destinations)),
            daemon=True,
        )
        self._pumps.append(_StreamPump(source, thread))
        thread.start()

    def finish(self, timeout_seconds: float) -> tuple[StreamFailure, ...]:
        """Join all pumps against one deadline and return captured failures."""

        if timeout_seconds < 0:
            raise ValueError("stream capture timeout cannot be negative")
        if self._finished:
            return self.failures
        self._finished = True
        deadline = time.monotonic() + timeout_seconds
        for pump in self._pumps:
            pump.thread.join(timeout=max(0.0, deadline - time.monotonic()))
        for index, pump in enumerate(self._pumps):
            if not pump.thread.is_alive():
                continue
            self._record_failure(
                StreamFailure(
                    f"source-{index}",
                    True,
                    RuntimeError("capture stream did not close"),
                )
            )
            try:
                pump.source.close()
            except BaseException as error:  # pragma: no cover - OS pipe failure
                self._record_failure(StreamFailure(f"source-{index}", True, error))
        return self.failures

    def _pump(
        self, source: IO[bytes], destinations: tuple[StreamDestination, ...]
    ) -> None:
        active = list(destinations)
        try:
            while chunk := source.read(self._chunk_bytes):
                retained: list[StreamDestination] = []
                for destination in active:
                    try:
                        destination.stream.write(chunk)
                        if destination.flush_each_chunk:
                            destination.stream.flush()
                    except BaseException as error:
                        self._record_failure(
                            StreamFailure(
                                destination.name, destination.required, error
                            )
                        )
                    else:
                        retained.append(destination)
                active = retained
            for destination in active:
                try:
                    destination.stream.flush()
                    if destination.durable:
                        os.fsync(destination.stream.fileno())
                except BaseException as error:
                    self._record_failure(
                        StreamFailure(destination.name, destination.required, error)
                    )
        except BaseException as error:  # pragma: no cover - OS pipe failure
            self._record_failure(StreamFailure("source", True, error))
        finally:
            try:
                source.close()
            except BaseException as error:  # pragma: no cover - OS pipe failure
                self._record_failure(StreamFailure("source", True, error))

    def _record_failure(self, failure: StreamFailure) -> None:
        with self._failure_lock:
            self._failures.append(failure)
        if failure.required:
            self.required_failed.set()
