"""Incremental canonical JSON encoding with a hard byte ceiling."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterable
from typing import Any


class ExportTooLarge(ValueError):
    """Raised before an encoder writes the first byte beyond its limit."""


class CappedJsonEncoder:
    """Write canonical JSON to a spooled sink while enforcing an exact cap."""

    def __init__(self, limit: int, *, spool_limit: int = 1024 * 1024):
        if limit < 0:
            raise ValueError("JSON byte limit must be nonnegative")
        self._limit = limit
        self._size = 0
        self._sink = tempfile.SpooledTemporaryFile(max_size=spool_limit, mode="w+b")
        self._json = json.JSONEncoder(
            ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    def append(self, value: str) -> None:
        encoded = value.encode("utf-8")
        if self._size + len(encoded) > self._limit:
            raise ExportTooLarge("export exceeds byte bound")
        self._sink.write(encoded)
        self._size += len(encoded)

    def write_value(self, value: object) -> None:
        """Incrementally write one ordinary JSON value."""

        for chunk in self._json.iterencode(value):
            self.append(chunk)

    def write_array(
        self,
        values: Iterable[Any],
        write_item: Callable[[CappedJsonEncoder, Any], None] | None = None,
    ) -> None:
        """Write an array without collecting its input iterable."""

        writer = write_item or (lambda encoder, value: encoder.write_value(value))
        self.append("[")
        for position, value in enumerate(values):
            if position:
                self.append(",")
            writer(self, value)
        self.append("]")

    def write_object(
        self,
        items: Iterable[tuple[str, Any]],
        write_value: Callable[[CappedJsonEncoder, Any], None] | None = None,
    ) -> None:
        """Write already key-ordered object members without collecting them."""

        writer = write_value or (lambda encoder, value: encoder.write_value(value))
        self.append("{")
        for position, (key, value) in enumerate(items):
            if position:
                self.append(",")
            self.write_value(key)
            self.append(":")
            writer(self, value)
        self.append("}")

    def value(self) -> str:
        self._sink.seek(0)
        value = self._sink.read().decode("utf-8")
        self._sink.close()
        return value

    def materialize(self) -> object:
        """Decode only after the complete bounded representation was accepted."""

        self._sink.seek(0)
        value = json.load(self._sink)
        self._sink.close()
        return value

    def close(self) -> None:
        self._sink.close()

    def __del__(self) -> None:
        self._sink.close()

    @property
    def size(self) -> int:
        return self._size


def encode_json_sequence(values: Iterable[object], limit: int) -> str:
    """Encode a JSON array and stop pulling values as soon as the cap fails."""

    encoder = CappedJsonEncoder(limit)
    encoder.write_array(values)
    return encoder.value()


def encode_json_value(value: object, limit: int) -> str:
    """Incrementally encode one canonical JSON value under ``limit``."""

    encoder = CappedJsonEncoder(limit)
    encoder.write_value(value)
    return encoder.value()


def measure_json_value(value: object, limit: int) -> int:
    """Count canonical bytes without ever joining or returning the JSON text."""

    encoder = CappedJsonEncoder(limit)
    try:
        encoder.write_value(value)
        return encoder.size
    finally:
        encoder.close()
